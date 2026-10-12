# -*- coding: utf-8 -*-
"""
tg_send.py — ОТПРАВКА ОТВЕТА АГЕНТА клиенту Telegram с бизнес-аккаунта (13.10.2026, TGSEND1310).

Карточка канала Telegram в «Агентах» (сервер, `wa_agent_svc`) на «✅ Отправить» кладёт ответ в очередь
сервера `tg_outbox.db` и ждёт подтверждения ограниченное время. Писать клиенту Telegram сервер не умеет —
умеет userbot ПК, у него сессия аккаунта. Этот модуль — его руки: фоновая задача того же процесса и ТОГО ЖЕ
клиента Telethon (второго клиента нет и не будет).

ВЫКЛЮЧАТЕЛЬ ОДИН — НА СЕРВЕРЕ (WA_AGENT_TG_SEND у службы агента). Здесь своей ручки включения нет: файл
окружения ПК правит только владелец. Отправитель живёт, когда живёт лента (`TG_FEED_OFF` не задан) и есть
токен пушера; аварийный выключатель ПК — `TG_SEND_OFF=1` (по образцу `TG_FEED_OFF`), по умолчанию не задан.
Не живёт — userbot не заводит задачи вовсе: ни сети, ни файла, ни строки (`enabled()` спрашивают ДО всего).
Сервер отвечает на опрос признаком "send": "on"|"off". «off» (дверь у сервера закрыта) или ошибка — опрос
раз в 30 → 45 → 60 с (OFF_POLL_S); «on» — раз в POLL_S. Отправляется ТОЛЬКО то, что сервер выдал при «on».

ЦИКЛ (раз в POLL_S секунд при «on», только при живом соединении):
  1. подтверждения, которые не доехали (журнал) — повтор POST /tg-queue/ack;
  2. GET /tg-queue/outbox — ряды {id, token, chat_id, text, expires_at} (сервер отдаёт их queued → claimed);
  3. на каждый ряд: уже в журнале (сервер выдал повторно — ответ на прошлую выдачу потерялся) → НЕ шлём,
     подтверждаем из журнала; иначе проверка собеседника (личный чат пользователя: не бот, не группа, не свой
     аккаунт, не удалённый) и срока сервера → запись «sending» в журнал С fsync → `send_message(…,
     parse_mode=None)` (текст уходит дословно: без разметки Markdown) → запись исхода → POST ack.

ДВОЙНОЙ ОТПРАВКИ НЕТ ни одной дорогой: ряд с токеном, о котором журнал знает хоть что-то, не отправляется
второй раз; «sending» без исхода (процесс оборвался посреди отправки) подтверждается как «unknown» — исход
неизвестен, повтора нет. Журнал не пишется → не шлём (без него повтор нечем было бы отличить).

ИСХОДЫ ПК: sent (с msg_id) · failed (Telegram ОТКАЗАЛ — RPC-ошибка 4xx/420/303, собеседник не годится, срок
сервера вышел, журнал не пишется: сообщение заведомо не ушло) · unknown (сеть, таймаут, 5xx/-503 Telegram,
любое иное исключение после начала отправки: могло уйти). Отправка позже срока сервера не начинается: дверь
сервера к тому времени уже сказала человеку «исход неизвестен» или «не отправлено».

АДРЕС И СЕКРЕТ — тем же путём, что у пушера ленты (`tg_feed_push.py`; модуль userbot его не импортирует —
имена переменных те же, значения читает процесс, совпадение формул стережёт тест):
    TG_FEED_PUSH_URL    адрес двери ленты; кончается на /tg-queue/push → база = всё до него
    WA_QUEUE_BASE       иначе база (по умолчанию https://wa.turbophuket.com)
    TG_FEED_PUSH_TOKEN  токен Bearer; нет — WA_PULL_SECRET
Токен не печатается ни одной веткой (`_safe`); редиректы не идут (заголовок с токеном не уедет на чужой адрес).

ЖУРНАЛ — `tg_feed/tg_send.journal.jsonl` (каталог ленты, вне git): одна JSON-строка на событие ряда (token, id,
chat_id, state, msg_id, error, acked), дозапись с fsync. Текстов клиентам в журнале нет.
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from telethon import errors as tl_errors
from telethon.tl.types import User

import tg_feed

OFF_ENV = "TG_SEND_OFF"         # аварийный выключатель ПК (по образцу tg_feed.OFF_ENV); по умолчанию не задан
OFF_WORDS = ("1", "true", "yes", "on", "да")   # те же слова, что у tg_feed.enabled (сверяет тест)
URL_ENV, TOKEN_ENV = "TG_FEED_PUSH_URL", "TG_FEED_PUSH_TOKEN"      # те же имена, что у пушера ленты
BASE_ENV, PULL_SECRET_ENV = "WA_QUEUE_BASE", "WA_PULL_SECRET"
DEFAULT_BASE = "https://wa.turbophuket.com"
PUSH_PATH = "/tg-queue/push"
OUTBOX_PATH, ACK_PATH = "/tg-queue/outbox", "/tg-queue/ack"
JOURNAL_FILE = os.path.join(tg_feed.FEED_DIR, "tg_send.journal.jsonl")

POLL_S = 2.5                    # опрос очереди сервера; дверь сервера ждёт ~20 с — это ~8 опросов
OFF_POLL_S = (30.0, 45.0, 60.0)  # сервер сказал «off» или ошибка: опрос реже, с отступом до 60 с
HTTP_TIMEOUT_S = 8.0
MARGIN_S = 2.0                  # до срока сервера должно остаться больше — иначе не начинаем отправку
TEXT_MAX = 4096                 # Telegram: ≤ 4096 знаков (единиц UTF-16)
ERROR_MAX = 200
ALARM_EVERY_S = 900             # затяжной сбой опроса — строка журнала не чаще раза в 15 мин

S_SENDING, S_SENT, S_FAILED, S_UNKNOWN = "sending", "sent", "failed", "unknown"
FINAL = (S_SENT, S_FAILED, S_UNKNOWN)
# отказ сервера на подтверждение, после которого повторять бессмысленно (ряд не наш / отменён / не выдан)
ACK_FINAL_REFUSALS = ("not_found", "token_mismatch", "cancelled", "not_claimed")


def _now():
    return datetime.now(timezone.utc).isoformat()


def enabled(env=None) -> bool:
    """Жить ли отправителю: лента включена (TG_FEED_OFF не задан), аварийный TG_SEND_OFF не задан и токен пушера
    есть. Включать ли саму отправку — решает сервер (его единственный выключатель), а не ПК."""
    if _env(env, tg_feed.OFF_ENV).lower() in OFF_WORDS or _env(env, OFF_ENV).lower() in OFF_WORDS:
        return False
    return bool(door_token(env))


def next_poll(state, quiet):
    """Пауза до следующего опроса: «on» — POLL_S; «off»/ошибка — OFF_POLL_S по числу таких подряд (quiet ≥ 1)."""
    if state == "on":
        return POLL_S
    return OFF_POLL_S[min(max(quiet, 1), len(OFF_POLL_S)) - 1]


def _env(env, name):
    """Значение переменной без краевых пробелов; нет её или не строка — пустая строка (решение «нет» — у
    вызывающего: пустое значение и отсутствие для адреса и токена здесь одно и то же, как у пушера)."""
    v = (os.environ if env is None else env).get(name)
    return v.strip() if isinstance(v, str) else ""


def door_base(env=None) -> str:
    """База адреса сервера тем же путём, что у пушера: TG_FEED_PUSH_URL без /tg-queue/push; иначе WA_QUEUE_BASE."""
    url = _env(env, URL_ENV)
    if url and url.rstrip("/").endswith(PUSH_PATH):
        return url.rstrip("/")[:-len(PUSH_PATH)]
    base = _env(env, BASE_ENV)
    return (base if base else DEFAULT_BASE).rstrip("/")


def door_token(env=None) -> str:
    tok = _env(env, TOKEN_ENV)
    return tok if tok else _env(env, PULL_SECRET_ENV)


def tg_len(text) -> int:
    s = text if isinstance(text, str) else str(text)
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _text(body):
    """Тело ответа → текст; не байты (ответа нет) — пустая строка: разбор JSON её отвергнет."""
    return body.decode("utf-8") if isinstance(body, bytes) else ""


# ─── транспорт ───────────────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http_call(method, url, body, headers, timeout):
    """→ (код, тело-байты). HTTPError — ответ, а не исключение; сеть/таймаут — исключение."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:                                           # noqa: BLE001
            data = b""
        return e.code, data


# ─── журнал ──────────────────────────────────────────────────────────────────────────────

class Journal:
    """Журнал отправленного: token → последняя запись. Дозапись строки целиком с fsync (как лента)."""

    def __init__(self, path):
        self.path = path
        self.recs = {}
        self.bad_lines = 0
        self.broken = ""            # журнал есть, но не читается — отправлять нельзя (повтор нечем отличить)
        self.fresh = False
        try:
            with open(path, "rb") as f:
                for raw in f.read().split(b"\n"):
                    if not raw.strip():
                        continue
                    try:
                        rec = json.loads(raw.decode("utf-8"))
                        tok = rec["token"]
                        if not isinstance(tok, str) or not tok:
                            raise ValueError("token")
                    except Exception:                               # noqa: BLE001 — оборванная строка
                        self.bad_lines += 1
                        continue
                    self.recs[tok] = dict(self.recs.get(tok, {}), **rec)
        except FileNotFoundError:
            self.fresh = True       # журнала ещё нет: первый запуск, отправленного не было
        except OSError as e:
            self.broken = "журнал отправки не читается (%s)" % type(e).__name__

    def get(self, token):
        return self.recs.get(token)

    def write(self, token, **fields):
        rec = dict(self.recs.get(token, {}), **fields)
        rec["token"], rec["at"] = token, round(time.time(), 3)
        data = (json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        try:
            size = os.fstat(fd).st_size
            if size:
                os.lseek(fd, size - 1, os.SEEK_SET)
                if os.read(fd, 1) != b"\n":
                    data = b"\n" + data
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                if not n:
                    raise OSError("журнал: запись не продвинулась")
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        self.recs[token] = rec
        return rec

    def pending(self):
        """Записи с исходом (или оборванной отправкой), подтверждение которых сервер ещё не принял."""
        return [r for r in self.recs.values() if r.get("state") in FINAL + (S_SENDING,) and not r.get("acked")]


def outcome_of(rec):
    """Запись журнала → (outcome, msg_id, error) для подтверждения. «sending» без исхода — unknown."""
    st = rec.get("state")
    if st == S_SENT and isinstance(rec.get("msg_id"), int):
        return S_SENT, rec["msg_id"], None
    if st == S_FAILED:
        return S_FAILED, None, rec.get("error") or "отказ"
    if st == S_SENDING:
        return S_UNKNOWN, None, "отправка начата, исход не записан (обрыв процесса) — повтора нет"
    return S_UNKNOWN, None, rec.get("error") or "исход неизвестен"


def classify_send_error(e):
    """Исключение отправки → (state, слова). Отказ Telegram (RPC 4xx/420/303) и ошибка входа до сети — failed:
    сообщение заведомо не ушло. 5xx/-503 Telegram, сеть, таймаут, иное — unknown: могло уйти."""
    name = type(e).__name__
    if isinstance(e, tl_errors.RPCError):
        code = getattr(e, "code", None)
        if isinstance(e, (tl_errors.ServerError, tl_errors.TimedOutError)) or \
                (isinstance(code, int) and abs(code) in (500, 503)):
            return S_UNKNOWN, "Telegram %s (%s) — мог принять" % (name, code)
        if isinstance(code, int):
            return S_FAILED, "Telegram отказал: %s (%s)" % (name, code)
        return S_UNKNOWN, "Telegram %s без кода" % name
    if isinstance(e, (ValueError, TypeError)):
        return S_FAILED, "до отправки: %s" % name
    return S_UNKNOWN, "%s — могло уйти" % name


# ─── отправитель ─────────────────────────────────────────────────────────────────────────

class Sender:
    def __init__(self, client, me_id, journal, env=None, log=None, http=None, clock=None,
                 own_usernames=()):
        self.client, self.me_id, self.journal = client, me_id, journal
        self.base, self.token = door_base(env), door_token(env)
        self.log = log
        self.http = http or http_call
        self.clock = clock or time.monotonic
        self.own = {str(u).lower() for u in own_usernames}
        self.fail_since = None
        self.fail_said = None
        self.door_open = None           # последнее сказанное сервером: True «on» · False «off» · None — не знаем
        self.counts = {"sent": 0, "failed": 0, "unknown": 0, "rehanded": 0, "acked": 0}
        # оборванная прошлым процессом отправка — исход неизвестен, повтора нет никогда
        for rec in list(journal.recs.values()):
            if rec.get("state") == S_SENDING:
                self._say("warning", "TG-ОТПРАВКА: ряд %s оборван прошлым процессом посреди отправки — исход "
                                     "неизвестен, повтора нет" % rec.get("id"))

    # ── журнал процесса userbot ──
    def _safe(self, s):
        s = str(s)
        return s.replace(self.token, "<токен>") if self.token else s

    def _say(self, level, text):
        if self.log is not None:
            getattr(self.log, level)(f"{_now()} | {self._safe(text)}")

    def _headers(self):
        return {"Content-Type": "application/json", "Authorization": "Bearer " + self.token}

    async def _http(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return await asyncio.to_thread(self.http, method, self.base + path, body, self._headers(), HTTP_TIMEOUT_S)

    def _net_fail(self, what):
        now = self.clock()
        if self.fail_since is None:
            self.fail_since, self.fail_said = now, now
            self._say("warning", "TG-ОТПРАВКА: %s — повторю через %.1f с" % (what, POLL_S))
        elif now - self.fail_said >= ALARM_EVERY_S:
            self.fail_said = now
            self._say("error", "TG-ОТПРАВКА: сервер не отвечает %d с: %s" % (int(now - self.fail_since), what))

    def _net_ok(self):
        if self.fail_since is not None:
            self._say("info", "TG-ОТПРАВКА: связь с сервером снова есть (простой %d с)"
                      % int(self.clock() - self.fail_since))
            self.fail_since = self.fail_said = None

    # ── шаг ──
    async def step(self):
        """Один опрос. → "on" (дверь сервера открыта, выданное обработано) · "off" (сервер: отправка выключена) ·
        "error" (сеть, не 200, ответ не по контракту). Отправляется только выданное при «on»."""
        await self.flush_acks()
        got_at = self.clock()
        try:
            status, body = await self._http("GET", OUTBOX_PATH)
        except Exception as e:                                      # noqa: BLE001
            self._net_fail("очередь не прочитана (%s)" % type(e).__name__)
            return "error"
        if status != 200:
            self._net_fail("очередь: ответ %s" % status)
            return "error"
        try:
            data = json.loads(_text(body))
            items, server_now, send = data["items"], float(data["now"]), data["send"]
            if not isinstance(items, list) or send not in ("on", "off"):
                raise ValueError("items/send")
        except Exception as e:                                      # noqa: BLE001
            self._net_fail("очередь: ответ не по контракту (%s)" % type(e).__name__)
            return "error"
        self._net_ok()
        if send != "on":
            if items:                                               # сервер так не отвечает; не шлём ничего
                self._say("warning", "TG-ОТПРАВКА: сервер сказал «off» и выдал %d ряд(ов) — не шлю ни одного"
                          % len(items))
            self._door(False, data.get("why"))
            return "off"
        self._door(True, None)
        for it in items:
            await self.one(it, got_at, server_now)
        return "on"

    def _door(self, open_, why):
        """Строка журнала на СМЕНУ состояния двери сервера (первое известное — тоже), а не на каждый опрос."""
        if self.door_open is open_:
            return
        self.door_open = open_
        if open_:
            self._say("info", "TG-ОТПРАВКА: сервер — отправка ВКЛЮЧЕНА, опрос раз в %s с" % POLL_S)
        else:
            self._say("info", "TG-ОТПРАВКА: сервер — отправка выключена (%s), опрос раз в %d–%d с"
                      % (why or "причина не названа", OFF_POLL_S[0], OFF_POLL_S[-1]))

    async def one(self, it, got_at, server_now):
        try:
            rid, token, chat, text = int(it["id"]), str(it["token"]), int(it["chat_id"]), it["text"]
            expires = float(it["expires_at"])
            if not token or not isinstance(text, str) or isinstance(it["id"], bool) or isinstance(it["chat_id"], bool):
                raise ValueError("ряд")
        except Exception:                                           # noqa: BLE001 — подтвердить нечем
            self._say("warning", "TG-ОТПРАВКА: ряд очереди не по контракту — пропущен")
            return False
        rec = self.journal.get(token)
        if rec is not None:
            # сервер выдал ряд повторно (ответ на прошлую выдачу или подтверждение потерялись) — НЕ шлём
            self.counts["rehanded"] += 1
            self._say("info", "TG-ОТПРАВКА: ряд %d уже в журнале (%s) — второй раз не шлю, подтверждаю из журнала"
                      % (rid, rec.get("state")))
            await self.ack(token)
            return True
        why = (self.journal.broken + " — без него повтор нечем отличить, не шлю") if self.journal.broken \
            else self._refuse(chat, text)
        ent = None
        if why is None:
            ent, why = await self._peer(chat)
        left = (expires - server_now) - (self.clock() - got_at)
        if why is None and left < MARGIN_S:
            why = "срок сервера на исходе (осталось %.1f с) — отправку не начинаю" % left
        if why is not None:
            try:
                self.journal.write(token, id=rid, chat_id=chat, state=S_FAILED, error=why[:ERROR_MAX])
            except Exception as e:                                  # noqa: BLE001 — исход в памяти, ack всё равно
                self._say("error", "TG-ОТПРАВКА: журнал не записан (%s)" % type(e).__name__)
                self.journal.recs[token] = {"token": token, "id": rid, "chat_id": chat, "state": S_FAILED,
                                            "error": why[:ERROR_MAX]}
            self.counts["failed"] += 1
            self._say("warning", "TG-ОТПРАВКА: ряд %d НЕ отправлен — %s" % (rid, why))
            await self.ack(token)
            return True
        try:
            self.journal.write(token, id=rid, chat_id=chat, state=S_SENDING)        # ДО отправки, с fsync
        except Exception as e:                                      # noqa: BLE001
            why = "журнал отправки не пишется (%s) — без него повтор нечем отличить, не шлю" % type(e).__name__
            self.journal.recs[token] = {"token": token, "id": rid, "chat_id": chat, "state": S_FAILED, "error": why}
            self.counts["failed"] += 1
            self._say("error", "TG-ОТПРАВКА: ряд %d — %s" % (rid, why))
            await self.ack(token)
            return True
        try:
            msg = await self.client.send_message(ent, text, parse_mode=None)
            msg_id = getattr(msg, "id", None)
            if isinstance(msg_id, int) and not isinstance(msg_id, bool) and msg_id > 0:
                state, err = S_SENT, None
            else:
                state, err, msg_id = S_UNKNOWN, "Telegram не вернул id сообщения", None
        except Exception as e:                                      # noqa: BLE001
            state, err = classify_send_error(e)
            msg_id = None
        self.counts[state] += 1
        try:
            self.journal.write(token, state=state, msg_id=msg_id, error=err)
        except Exception as e:                                      # noqa: BLE001
            self._say("error", "TG-ОТПРАВКА: исход ряда %d не записан в журнал (%s) — подтверждаю из памяти"
                      % (rid, type(e).__name__))
            self.journal.recs[token] = dict(self.journal.recs.get(token, {}), state=state, msg_id=msg_id, error=err)
        self._say("info" if state == S_SENT else "warning",
                  "TG-ОТПРАВКА: ряд %d → %s%s" % (rid, state, " (%s)" % err if err else ""))
        await self.ack(token)
        return True

    def _refuse(self, chat, text):
        if chat <= 0:
            return "чат не личный (id ≤ 0) — в группы и каналы не пишу"
        if self.me_id is not None and chat == self.me_id:
            return "чат с самим собой — не пишу"
        if not text.strip():
            return "пустой текст"
        if tg_len(text) > TEXT_MAX:
            return "текст длиннее %d знаков" % TEXT_MAX
        return None

    async def _peer(self, chat):
        """Собеседник → (сущность, None) или (None, почему не шлём). Только личный чат живого пользователя."""
        try:
            ent = await self.client.get_entity(chat)
        except Exception as e:                                      # noqa: BLE001
            return None, "собеседник не определён (%s)" % type(e).__name__
        if not isinstance(ent, User):
            return None, "не личный чат пользователя (%s)" % type(ent).__name__
        if getattr(ent, "bot", False):
            return None, "собеседник — бот"
        if getattr(ent, "is_self", False) or (self.me_id is not None and ent.id == self.me_id):
            return None, "свой аккаунт"
        if getattr(ent, "deleted", False):
            return None, "аккаунт собеседника удалён"
        nick = getattr(ent, "username", None)
        if isinstance(nick, str) and nick.lower() in self.own:
            return None, "свой аккаунт"
        return ent, None

    async def ack(self, token):
        rec = self.journal.get(token)
        if rec is None or rec.get("acked"):
            return True
        outcome, msg_id, err = outcome_of(rec)
        payload = {"id": rec.get("id"), "token": token, "outcome": outcome}
        if msg_id is not None:
            payload["msg_id"] = msg_id
        if err:
            payload["error"] = str(err)[:ERROR_MAX]
        try:
            status, body = await self._http("POST", ACK_PATH, payload)
        except Exception as e:                                      # noqa: BLE001
            self._net_fail("подтверждение ряда %s не доехало (%s)" % (rec.get("id"), type(e).__name__))
            return False
        try:
            data = json.loads(_text(body))
            if not isinstance(data, dict):
                raise ValueError("ответ не объект")
        except Exception:                                           # noqa: BLE001 — тело не JSON: решает код ответа
            data = {"_unparsed": True}
        if status == 200 and data.get("ok"):
            mark = True
        elif (status == 200 and data.get("error") in ACK_FINAL_REFUSALS) or status in (400, 413):
            mark = "отказ:%s" % (data.get("error") or status)
            self._say("error", "TG-ОТПРАВКА: сервер не принял подтверждение ряда %s (%s) — больше не повторяю"
                      % (rec.get("id"), data.get("error") or status))
        else:
            self._net_fail("подтверждение ряда %s: ответ %s" % (rec.get("id"), status))
            return False
        self._net_ok()
        self.counts["acked"] += 1
        try:
            self.journal.write(token, acked=mark)
        except Exception as e:                                      # noqa: BLE001
            self.journal.recs[token] = dict(rec, acked=mark)
            self._say("warning", "TG-ОТПРАВКА: отметка подтверждения не записана (%s)" % type(e).__name__)
        return True

    async def flush_acks(self):
        for rec in self.journal.pending():
            if not await self.ack(rec["token"]):
                break                                               # сервер молчит — остальное следующим шагом


async def run(client, me_id, log, own_usernames=(), env=None, sleep=None, journal_path=None, sender=None):
    """Фоновая задача userbot: опрос сервера при живом соединении; пауза — `next_poll` по ответу («on» — POLL_S,
    «off»/ошибка — 30 → 45 → 60 с). Никогда не падает наружу (кроме отмены задачи): сбой шага — строка журнала."""
    sleep = sleep or asyncio.sleep
    if sender is None:
        if not door_token(env):
            log.error(f"{_now()} | TG-ОТПРАВКА: токена нет ({TOKEN_ENV} / {PULL_SECRET_ENV}) — задача отправки "
                      f"НЕ запущена, ответы агента не уходят")
            return
        sender = Sender(client, me_id, Journal(journal_path or JOURNAL_FILE), env=env, log=log,
                        own_usernames=own_usernames)
    log.info(f"{_now()} | TG-ОТПРАВКА: живу — опрашиваю {sender.base}{OUTBOX_PATH}; включает отправку сервер "
             f"(WA_AGENT_TG_SEND у wa-agent), аварийно выключить здесь — {OFF_ENV}=1; журнал {sender.journal.path}")
    if getattr(sender.journal, "broken", ""):
        log.error(f"{_now()} | TG-ОТПРАВКА: {sender.journal.broken} — ни один ответ не уйдёт (каждый подтверждаю "
                  f"отказом), пока журнал не починят и userbot не перезапустят")
    quiet = 0
    while True:
        state = "error"
        try:
            if client.is_connected():
                state = await sender.step()
        except asyncio.CancelledError:
            raise
        except Exception as e:                                      # noqa: BLE001 — задача не умирает
            log.warning(f"{_now()} | TG-ОТПРАВКА: шаг упал ({type(e).__name__}: {sender._safe(e)})")
        quiet = 0 if state == "on" else quiet + 1
        await sleep(next_poll(state, quiet))
