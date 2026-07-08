# -*- coding: utf-8 -*-
"""
moderation_ipc.py — IPC между userbot (Telethon) и moderation_bot (Bot API).

Почему sqlite, а не jsonl: два ПРОЦЕССА конкурентно читают/пишут очередь и МЕНЯЮТ
статус записей (new→posted→ready→sent). sqlite даёт файловые блокировки, атомарные
транзакции и UPDATE строки; jsonl потребовал бы своей блокировки и не умеет чисто
обновлять запись. Режим WAL + busy_timeout → устойчиво к параллельному доступу.

Поток статусов:
  new           — userbot положил черновик, ждёт постинга ботом
  posted        — бот запостил карточку (card_msg_id заполнен)
  pending_confirm — бот применил правку, ждёт подтверждения (final_text = кандидат)
  ready         — решение принято, userbot ДОЛЖЕН отправить клиенту (final_text)
  test_held     — TEST_MODE: решение принято, но отправка заблокирована (double-lock)
  rejected      — отклонено
  sent / failed — userbot отправил / не смог

SAFETY: сам по себе IPC клиенту ничего не шлёт. Отправка — только userbot из статуса
'ready'. В TEST_MODE бот ставит 'test_held' (не 'ready'), плюс userbot.send блокирует.
Токены/секреты тут не хранятся и не логируются.
"""

import os
import json
import sqlite3
import datetime
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROD_DB = os.path.join(BASE_DIR, "moderation_ipc.db")  # БОЕВАЯ очередь (её читает живой бот)


def _env_flag(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "да")


# ИЗОЛЯЦИЯ ТЕСТОВ: TESTING=1 → боевой IPC недоступен ПО ПОСТРОЕНИЮ. Дефолт DB_PATH уводим в
# одноразовый temp (игнорируя MODERBOT_DB, который в среде демона указывает на боевую БД), а
# _conn() дополнительно ловит любую попытку открыть боевой moderation_ipc.db и БЛОКИРУЕТ её.
# Инцидент 16:39: тесты в среде демона (bot_mode_active=True) enqueue'или фикстурные черновики
# «@client1» в боевую очередь → живой модербот запостил их в реальную группу. Больше нельзя.
TESTING = _env_flag("TESTING")
if TESTING:
    import tempfile
    DB_PATH = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_ipc.db")
else:
    DB_PATH = os.getenv("MODERBOT_DB", _PROD_DB)

# Мок-счётчик наружу: сколько раз под TESTING пытались открыть БОЕВОЙ IPC. Норма прогона = 0.
prod_ipc_open_attempts = 0

HEARTBEAT_STALE_SEC = int(os.getenv("MODERBOT_HEARTBEAT_STALE", "15") or "15")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@contextmanager
def _conn(path=None):
    """Соединение с sqlite: коммитит при успехе и ВСЕГДА закрывает (иначе файл БД
    остаётся занят — на Windows это блокирует удаление/тесты)."""
    real = path or DB_PATH
    # ТРИПВАЙР ИЗОЛЯЦИИ: под TESTING любое обращение к боевому moderation_ipc.db — ошибка
    # (тест обязан работать на своём tmp). Считаем попытку и валимся громко, а не молча в бой.
    if TESTING and os.path.abspath(real) == os.path.abspath(_PROD_DB):
        global prod_ipc_open_attempts
        prod_ipc_open_attempts += 1
        raise RuntimeError(
            "TESTING: попытка открыть БОЕВОЙ moderation_ipc.db заблокирована (изоляция тестов). "
            "Тест должен переопределить moderation_ipc.DB_PATH на временный файл."
        )
    c = sqlite3.connect(real, timeout=5.0)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        yield c
        c.commit()
    finally:
        c.close()


def init_db(path=None):
    with _conn(path) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER, client_ref TEXT, lang TEXT, incoming TEXT,
                draft TEXT, first_contact INTEGER,
                status TEXT, card_msg_id INTEGER, final_text TEXT,
                decided_by TEXT, reason TEXT, created_ts TEXT, updated_ts TEXT,
                transcript TEXT, pricing_note TEXT, directive TEXT, client_name TEXT
            )"""
        )
        # МИГРАЦИЯ для старых БД: transcript/pricing_note — СТРАТЕГИЯ-перегенерация; directive —
        # текст правки модератора (для кнопки «Запомнить как правило» → playbook); client_name —
        # first_name профиля клиента (O3 кусок 1.1: D-колонка карточки «Бронь», когда имя в тексте не названо).
        for col in ("transcript TEXT", "pricing_note TEXT", "directive TEXT", "client_name TEXT"):
            try:
                c.execute("ALTER TABLE drafts ADD COLUMN " + col)
            except Exception:
                pass  # колонка уже есть
        c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON drafts(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_card ON drafts(card_msg_id)")
        # O3-2c МОСТ «Заявка → INTAKE»: обратный канал модербот→userbot для ПОСТА карточки
        # «🆕 БРОНЬ» во «Входящие брони». Статусы: draft (кандидат под кнопкой «✅ В CRM») →
        # pending (менеджер подтвердил, ждёт постинга userbot-аккаунтом) → posted | failed.
        # SAFETY: тут только ТЕКСТ поста во внутреннюю группу; НИКАКОЙ записи в CRM, ни адресата-клиента.
        c.execute(
            """CREATE TABLE IF NOT EXISTS intake (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT, status TEXT, created_ts TEXT, updated_ts TEXT,
                posted_msg_id INTEGER, reason TEXT
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_intake_status ON intake(status)")


def _row(r):
    return dict(r) if r is not None else None


def enqueue_draft(rec, path=None):
    """userbot кладёт черновик (status=new). rec: client_id, client_ref, lang, incoming,
    draft, first_contact, transcript, pricing_note, client_name. Возвращает id.
    transcript+pricing_note хранятся для СТРАТЕГИЯ-перегенерации черновика с нуля;
    client_name (first_name профиля) — для D-колонки карточки «Бронь»."""
    ts = _now_iso()
    with _conn(path) as c:
        cur = c.execute(
            """INSERT INTO drafts
               (client_id, client_ref, lang, incoming, draft, first_contact,
                status, created_ts, updated_ts, transcript, pricing_note, client_name)
               VALUES (?,?,?,?,?,?, 'new', ?, ?, ?, ?, ?)""",
            (rec.get("client_id"), rec.get("client_ref"), rec.get("lang"),
             rec.get("incoming"), rec.get("draft"), int(bool(rec.get("first_contact"))),
             ts, ts, rec.get("transcript"), rec.get("pricing_note"), rec.get("client_name")),
        )
        return cur.lastrowid


# ------------------- O3-2c: очередь поста «🆕 БРОНЬ» → Входящие -----------------

def save_intake_candidate(text, path=None):
    """Модербот сохраняет КАНДИДАТ поста (status='draft') под кнопкой «✅ В CRM». Возвращает id.
    Ещё НЕ отправляется — ждёт подтверждения менеджера-авторизатора."""
    ts = _now_iso()
    with _conn(path) as c:
        cur = c.execute(
            "INSERT INTO intake (text, status, created_ts, updated_ts) VALUES (?, 'draft', ?, ?)",
            (text, ts, ts),
        )
        return cur.lastrowid


def confirm_intake(intake_id, path=None):
    """Тап «✅ В CRM»: перевод draft→pending (запись в очередь на постинг userbot'ом). Это и есть
    «подтверждение записи в очередь». → True (перевели) | False (нет такой draft-записи / уже ушла)."""
    with _conn(path) as c:
        cur = c.execute(
            "UPDATE intake SET status='pending', updated_ts=? WHERE id=? AND status='draft'",
            (_now_iso(), intake_id),
        )
        return cur.rowcount > 0


def fetch_pending_intake(path=None):
    """userbot забирает подтверждённые посты (status='pending') для отправки во «Входящие брони»."""
    with _conn(path) as c:
        return [_row(r) for r in c.execute("SELECT * FROM intake WHERE status='pending' ORDER BY id")]


def mark_intake(intake_id, status, posted_msg_id=None, reason=None, path=None):
    """Итог постинга userbot'ом: posted (ушло, posted_msg_id) | failed (reason)."""
    assert status in ("posted", "failed")
    with _conn(path) as c:
        c.execute(
            "UPDATE intake SET status=?, posted_msg_id=?, reason=?, updated_ts=? WHERE id=?",
            (status, posted_msg_id, reason, _now_iso(), intake_id),
        )


def get_intake(intake_id, path=None):
    with _conn(path) as c:
        return _row(c.execute("SELECT * FROM intake WHERE id=?", (intake_id,)).fetchone())


def fetch_new(path=None):
    with _conn(path) as c:
        return [_row(r) for r in c.execute("SELECT * FROM drafts WHERE status='new' ORDER BY id")]


def fetch_ready(path=None):
    with _conn(path) as c:
        return [_row(r) for r in c.execute("SELECT * FROM drafts WHERE status='ready' ORDER BY id")]


def get(draft_id, path=None):
    with _conn(path) as c:
        return _row(c.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())


def draft_by_card(card_msg_id, path=None):
    with _conn(path) as c:
        return _row(c.execute("SELECT * FROM drafts WHERE card_msg_id=?", (card_msg_id,)).fetchone())


def mark_posted(draft_id, card_msg_id, path=None):
    with _conn(path) as c:
        c.execute("UPDATE drafts SET status='posted', card_msg_id=?, updated_ts=? WHERE id=?",
                  (card_msg_id, _now_iso(), draft_id))


def set_candidate(draft_id, final_text, directive=None, path=None):
    """Правка применена, ждём подтверждения (status=pending_confirm). directive — текст правки
    модератора (сохраняем для кнопки «Запомнить как правило»)."""
    with _conn(path) as c:
        c.execute("UPDATE drafts SET status='pending_confirm', final_text=?, directive=?, "
                  "updated_ts=? WHERE id=?",
                  (final_text, directive, _now_iso(), draft_id))


def set_decision(draft_id, status, final_text=None, decided_by=None, reason=None, path=None):
    """status ∈ ready | test_held | rejected. Итоговое решение по черновику."""
    assert status in ("ready", "test_held", "rejected")
    with _conn(path) as c:
        c.execute(
            "UPDATE drafts SET status=?, final_text=?, decided_by=?, reason=?, updated_ts=? WHERE id=?",
            (status, final_text, decided_by, reason, _now_iso(), draft_id),
        )


def mark(draft_id, status, reason=None, path=None):
    """Пометить итог отправки: sent | failed | test_held."""
    with _conn(path) as c:
        c.execute("UPDATE drafts SET status=?, reason=?, updated_ts=? WHERE id=?",
                  (status, reason, _now_iso(), draft_id))


# ------------------------------- heartbeat -----------------------------------

def heartbeat(ts_iso=None, path=None):
    """Бот периодически отмечает, что жив."""
    with _conn(path) as c:
        c.execute("INSERT INTO meta(k,v) VALUES('heartbeat',?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (ts_iso or _now_iso(),))


def set_meta(k, v, path=None):
    with _conn(path) as c:
        c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def get_meta(k, path=None):
    with _conn(path) as c:
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None


def last_heartbeat(path=None):
    with _conn(path) as c:
        r = c.execute("SELECT v FROM meta WHERE k='heartbeat'").fetchone()
        return r["v"] if r else None


def is_bot_alive(now=None, threshold=None, path=None):
    """Свежий ли heartbeat бота (бот жив). now — инъекция для тестов."""
    threshold = HEARTBEAT_STALE_SEC if threshold is None else threshold
    hb = last_heartbeat(path)
    if not hb:
        return False
    try:
        t = datetime.datetime.fromisoformat(hb)
    except Exception:
        return False
    now_dt = now() if now else datetime.datetime.now(datetime.timezone.utc)
    return (now_dt - t).total_seconds() <= threshold
