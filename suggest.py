# -*- coding: utf-8 -*-
"""
suggest.py — ступень ① SUGGEST для userbot @turbophuket.

На входящее ЛС клиента готовит ЧЕРНОВИК ответа (LLM), отправляет его НЕ клиенту,
а на МОДЕРАЦИЮ (группа «Модерация ответов»); после одобрения reply-командой
менеджера userbot САМ шлёт клиенту. Клиент видит только одобренное.

БЕЗОПАСНОСТЬ:
  • SUGGEST_MODE по умолчанию OFF → userbot ведёт себя как чистый Stage C.
  • Отправку делает ТОЛЬКО единый userbot-Telethon-процесс (второй клиент = бан-риск).
  • Гарды: пауза 30–60с, лимит 6/час и 15/день, только существующие входящие
    диалоги, никаких первых сообщений/рассылок, typing-статус.
  • FloodWait/PeerFlood → backoff + авто-стоп SUGGEST + запись в лог.

Вся содержательная логика — здесь и без жёсткой зависимости от живого Telegram/
Anthropic (обе стороны инъектируемы), чтобы покрывать мок-тестами. Точки врезки в
userbot_listen.py — тонкие (on_client_message / on_moderation_reply).
"""

import os
import re
import glob
import json
import time
import random
import shutil
import asyncio
import logging
import datetime
import tempfile
import subprocess

import pricing  # каркас получения точной цены из Календаря (Bridge); пусто → фолбэк

log = logging.getLogger("suggest")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ------------------------------- конфиг (env) --------------------------------

def _flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "да")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (ValueError, AttributeError):
        return default


SUGGEST_MODE = _flag("SUGGEST_MODE", False)          # главный рубильник, по умолч. OFF
SUGGEST_TEST_MODE = _flag("SUGGEST_TEST_MODE", False)  # обкатка: черновики есть, отправки клиенту НЕТ

# ИЗОЛЯЦИЯ ТЕСТОВ: TESTING=1 (ставит test_isolation) → боевой IPC/токен недоступны по построению.
# moderation_ipc под TESTING уводит БД в temp и блокирует боевую очередь; здесь bot_mode_active
# читает уже изолированный (пустой) IPC → нет heartbeat → reply-режим → ничего не уходит наружу.
TESTING = _flag("TESTING", False)

_mg = os.getenv("MOD_GROUP_ID", "").strip()
MOD_GROUP_ID = int(_mg) if _mg.lstrip("-").isdigit() else None  # нет ID → резолв по имени/лог
MOD_GROUP_NAME = os.getenv("MOD_GROUP_NAME", "").strip()        # резолв группы по title, если ID пуст

RATE_PER_HOUR = _int("SUGGEST_RATE_HOUR", 6)
RATE_PER_DAY = _int("SUGGEST_RATE_DAY", 15)
PAUSE_MIN = _int("SUGGEST_PAUSE_MIN", 30)
PAUSE_MAX = _int("SUGGEST_PAUSE_MAX", 60)

# Первый контакт: нет НАШИХ (менеджера) сообщений за последние N часов → черновик с приветствием.
FIRST_CONTACT_HOURS = _int("SUGGEST_FIRST_CONTACT_HOURS", 18)


def _csv_set(name: str):
    """Разобрать список юзернеймов из env (разделитель — запятая/пробел), в lower без @."""
    raw = os.getenv(name, "") or ""
    return {p.strip().lstrip("@").lower() for p in re.split(r"[,\s]+", raw) if p.strip()}


# Право approve/edit/reject. ПУСТОЙ список = любой участник группы может approve.
APPROVER_USERNAMES = _csv_set("APPROVER_USERNAMES")

# Токен бота-модератора (задача-2). Пусто → бот не поднимается, работает деградация
# (reply-режим userbot). Токен НИКОГДА не логируем и не коммитим.
MODERBOT_TOKEN = os.getenv("MODERBOT_TOKEN", "").strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
LLM_MAX_TOKENS = _int("SUGGEST_MAX_TOKENS", 1000)
# Генерация через claude CLI (подписка Max) вместо платного API-ключа. Флаг .env SUGGEST_LLM_VIA_CLI=1.
SUGGEST_LLM_VIA_CLI = _flag("SUGGEST_LLM_VIA_CLI")
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude").strip()   # фолбэк-путь (может протухнуть при автообновлении)
CLI_TIMEOUT = _int("SUGGEST_CLI_TIMEOUT", 60)
CLI_MODEL = os.getenv("SUGGEST_CLI_MODEL", "sonnet").strip()   # алиас модели для CLI (--model)

# Порядок чтения живого FAQ из Brain: «faq» (живой ключ) → «turbobaby_faq» → локальный файл.
FAQ_DOC_ORDER = [s.strip() for s in os.getenv("FAQ_DOCS", "faq,turbobaby_faq").split(",") if s.strip()]
BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()
LOCAL_FAQ = os.path.join(BASE_DIR, "manager-bot", "docs", "turbobaby_faq_v1.md")

PAIRS_FILE = os.path.join(BASE_DIR, "suggest_pairs.jsonl")      # лог обучения
PENDING_FILE = os.path.join(BASE_DIR, "suggest_pending.jsonl")  # pending-store (durable)

MAX_MESSAGES = 50  # глубина транскрипта диалога

# Telethon-ошибки флуда (ленивый импорт — модуль тестируется и без telethon).
try:
    from telethon.errors import FloodWaitError, PeerFloodError
    FLOOD_ERRORS = (FloodWaitError, PeerFloodError)
except Exception:  # pragma: no cover
    FLOOD_ERRORS = ()


# ----- критичные факты ДОСЛОВНО (в промпт всегда, чтобы перевод не искажал) ----
# Источник: turbobaby_faq_v1.md (прайс из CRM). Правило депозита и Click — жёсткие.
CRITICAL_FACTS = """КРИТИЧНЫЕ ФАКТЫ (не искажать, числа дословно):
Депозит: либо деньги, либо паспорт (НЕ оба). Принимаем: наличные баты, перевод
(в т.ч. USDT TRC20/BEP20, тайский счёт, Сбер/Тинькофф), либо паспорт вместо денег.
Прайс-ОРИЕНТИР ДЛЯ МЕНЕДЖЕРА (клиенту цену — в т.ч. «от X»/диапазон — НЕ называть;
точную называет только менеджер/Календарь по датам) (฿/день, депозит ฿):
- Скутеры: PCX150 349/3000; ADV150 449/3000; NMAX155 449/3000; PCX160 498/5000;
  ADV160 498/5000; FORZA300 690/5000; XMAX300(2020-2022) 790/5000;
  XMAX300(NEW 2023+) 939/7000; ADV350 998/7000; XADV750 2788/25000.
- Мотоциклы: XSR155 590/7000; CB300R 890/15000; REBEL300 890/15000; MT-03 1090/15000;
  NINJA400 1185/20000; VULCAN650S 1798/20000; CBR650R 1798/20000; CB650R 1798/20000;
  XSR900 1798/20000; R7 2298/25000.
- Скидки за срок: неделя ~6-15%, 2 недели ~15-25%, месяц ~35-50%.
Доставка по районам (฿): Патонг/Банг Тао/Сурин/Камала 290; Карон/Паклок 390;
Ката/Кату 490; Раваи/Найхарн/Чалонг 590; Аэропорт 590-690. Забор бесплатно.
HONDA CLICK 125 — НЕ сдаём. На запрос Click предлагать PCX150 / ADV150 / NMAX155.
Наличие байка на даты НЕ подтверждать без проверки — уточнить даты и сказать, что проверим."""


# ------------------------------- рантайм-стоп --------------------------------

_disabled = False  # флип при флуде — авто-стоп до перезапуска/сброса


def is_enabled() -> bool:
    """SUGGEST активен: включён флагом и не остановлен флуд-гардом."""
    return SUGGEST_MODE and not _disabled


def disable(reason: str) -> None:
    global _disabled
    _disabled = True
    log.warning(f"SUGGEST АВТО-СТОП: {reason}")


def reset_disabled() -> None:
    """Только для тестов/ручного сброса."""
    global _disabled
    _disabled = False


# ------------------------------- утилиты -------------------------------------

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def detect_lang(text: str) -> str:
    """Язык клиента по тексту: есть кириллица → ru, иначе en."""
    for ch in text or "":
        if "а" <= ch.lower() <= "я" or ch.lower() == "ё":
            return "ru"
    return "en"


def parse_approval(reply_text: str):
    """Разобрать reply менеджера. Возвращает (action, payload):
      approve — '+'/'да'/'да N' (payload=None): отправить черновик как есть;
      reject  — '-'/'нет'      (payload=None): отклонить;
      edit    — любой другой текст (payload=текст): отправить правленую версию.
    """
    t = (reply_text or "").strip()
    low = t.lower()
    if low in ("+", "да", "ок", "ok", "yes", "+1") or low.startswith("да "):
        return "approve", None
    if low in ("-", "нет", "no", "отклонить", "reject"):
        return "reject", None
    if not t:
        return "reject", None
    return "edit", t


# ------------------------------- транскрипт ----------------------------------

async def _fetch_messages(client, entity, limit: int = MAX_MESSAGES):
    """Выбрать последние сообщения диалога (newest-first, как отдаёт iter_messages)."""
    msgs = []
    async for m in client.iter_messages(entity, limit=limit):
        msgs.append(m)
    return msgs


def transcript_from(msgs, me_id: int) -> str:
    """Текст диалога '[менеджер]/[клиент]: ...' в хронологическом порядке (old→new).
    Ручные ответы менеджера видны (они отправлены с этого же аккаунта, sender_id==me)."""
    lines = []
    for m in reversed(msgs):
        who = "менеджер" if (getattr(m, "sender_id", None) == me_id) else "клиент"
        body = (getattr(m, "message", None) or "").strip() or "[медиа/без текста]"
        body = body.replace("\n", " ⏎ ")
        lines.append(f"[{who}]: {body}")
    return "\n".join(lines)


def first_contact_from(msgs, me_id: int, hours=None, now=None) -> bool:
    """Первый контакт: НЕТ наших (менеджера) сообщений за последние `hours` часов.
    msgs — newest-first. now — инъекция для тестов."""
    hours = FIRST_CONTACT_HOURS if hours is None else hours
    now_dt = now() if now else datetime.datetime.now(datetime.timezone.utc)
    cutoff = now_dt - datetime.timedelta(hours=hours)
    for m in msgs:  # newest-first
        d = getattr(m, "date", None)
        if d is not None and d < cutoff:
            break  # дальше только более старые — выходим из окна
        if getattr(m, "sender_id", None) == me_id and d is not None:
            return False  # мы писали в окне → это продолжение диалога
    return True


async def read_transcript(client, entity, me_id: int, limit: int = MAX_MESSAGES) -> str:
    """Совместимость: выбрать сообщения и собрать транскрипт."""
    return transcript_from(await _fetch_messages(client, entity, limit), me_id)


def is_approver(username) -> bool:
    """Есть ли право approve/edit/reject. Пустой whitelist = можно любому."""
    if not APPROVER_USERNAMES:
        return True
    return (username or "").lstrip("@").lower() in APPROVER_USERNAMES


# ------------------------------- FAQ / LLM -----------------------------------

def _bridge_read_doc(name: str):
    """read_doc GET по имени дока; вернуть текст или None."""
    if not (BRIDGE_URL and BRIDGE_TOKEN):
        return None
    import urllib.request
    import urllib.parse
    full = BRIDGE_URL + "?" + urllib.parse.urlencode(
        {"action": "read_doc", "name": name, "token": BRIDGE_TOKEN}
    )
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("ok"):
        for k in ("text", "content", "fileContent", "body"):
            if isinstance(data.get(k), str) and data[k].strip():
                return data[k]
    return None


def load_faq(getter=None) -> str:
    """Живой FAQ. Порядок: read_doc name=faq → read_doc name=turbobaby_faq → локальный файл.
    getter(name)->text|None — инъекция для тестов (заменяет чтение из Brain)."""
    read = getter if getter is not None else _bridge_read_doc
    for name in FAQ_DOC_ORDER:
        try:
            t = read(name)
        except Exception as e:
            log.warning(f"SUGGEST: read_doc FAQ '{name}' упал: {e}")
            t = None
        if t and t.strip():
            return t
    try:
        with open(LOCAL_FAQ, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# --- лёгкий парсер модель+даты из диалога (поля по образцу collect_booking) ---
_MODEL_TOKENS = [
    "nmax", "pcx", "adv350", "adv 350", "adv150", "adv 150", "adv160", "adv 160", "adv",
    "xmax", "forza", "xadv", "xsr", "cb300", "cb 300", "cb650", "cb 650",
    "cbr650", "cbr 650", "cbr", "rebel", "mt-03", "mt03", "mt 03", "ninja", "vulcan", "r7", "click",
]
_MONTHS = ("январ", "феврал", "март", "апрел", "мая", "июн", "июл", "август", "сентябр",
           "октябр", "ноябр", "декабр",
           "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _client_text(transcript: str) -> str:
    return "\n".join(l for l in (transcript or "").split("\n") if l.startswith("[клиент]:")).lower()


_MONTH_RE = r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
_MON_MAP = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "мая": 5,
            "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
            "ноябр": 11, "декабр": 12}


def _mon(stem):
    return _MON_MAP.get(stem)


def _safe_date(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def _mk(day, month, today, year=None):
    """Дата ближайшего будущего (если год не задан явно)."""
    if year:
        y = int(year)
        if y < 100:
            y += 2000
        return _safe_date(y, month, day)
    dt = _safe_date(today.year, month, day)
    if dt is None:
        return None
    if dt < today:                       # прошло в этом году → следующий год (ТОЛЬКО для старта/якоря)
        dt = _safe_date(today.year + 1, month, day)
    return dt


def _resolve_range(d_s, m_s, d_e, m_e, today, y_s=None, y_e=None):
    """Собрать (start, end) из двух точек. Год-ролл end+1г ТОЛЬКО при реальном переходе через
    год (месяц конца < месяца начала, напр. дек→янв). Перепутанный порядок в одном месяце → SWAP
    (НИКОГДА не превращаем в ~360 дней)."""
    start = _mk(d_s, m_s, today, y_s)
    if start is None:
        return None, None
    if y_e:                                          # у конца явный год
        end = _mk(d_e, m_e, today, y_e)
        if end and end < start:
            return end, start                        # явные годы, но реверс → swap
        return start, end
    end = _safe_date(start.year, m_e, d_e)
    if end is None:
        return start, None
    if end >= start:
        return start, end
    if int(m_e) < int(m_s):                          # дек→янв: легитимный переход через год
        return start, _safe_date(start.year + 1, m_e, d_e)
    return end, start                                # тот же/поздний месяц, но end<start → перепутан → swap


def _parse_term(t):
    """Срок из слов → (days:int, monthly:bool) или None."""
    if re.search(r"\bна\s+месяц\b", t) or re.search(r"\bмесяц\b", t):
        return (30, True)
    m = re.search(r"на\s+(\d{1,3})\s*(нед|недел)", t)
    if m:
        return (int(m.group(1)) * 7, False)
    if re.search(r"\bна\s+недел|\bнеделю\b", t):
        return (7, False)
    m = re.search(r"на\s+(\d{1,3})\s*(дн|дня|дней|день|сут)", t)
    if m:
        return (int(m.group(1)), False)
    m = re.search(r"на\s+(\d{1,3})\s*(day|days|week|weeks|month)", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        if u.startswith("week"):
            return (n * 7, False)
        if u == "month":
            return (n * 30, True)
        return (n, False)
    return None


def _anchor_date(t, today):
    """Дата начала для срочных фраз («завтра на 3 дня», «на неделю с 5 июля»)."""
    if "послезавтра" in t:
        return today + datetime.timedelta(days=2)
    if "завтра" in t:
        return today + datetime.timedelta(days=1)
    if "сегодня" in t:
        return today
    m = re.search(r"с\s+(\d{1,2})\s+" + _MONTH_RE, t)
    if m:
        return _mk(int(m.group(1)), _mon(m.group(2)), today)
    m = re.search(r"с\s+(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), today, m.group(3) or None)
    m = re.search(r"(\d{1,2})\s+" + _MONTH_RE, t)
    if m:
        return _mk(int(m.group(1)), _mon(m.group(2)), today)
    m = re.search(r"(\d{1,2})[./](\d{1,2})", t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), today)
    return None


def parse_date_range(text, today=None):
    """Диапазон дат из слов клиента → (iso_start, iso_end) или (None, None).
    Поддержка: «10.07-15.07», «с 5 по 10.07», «5–10 июля», «с 5 по 10 июля», «с 28 декабря по
    3 января» (год-ролл), «на неделю/месяц с 5 июля», «завтра на 3 дня», перепутанный порядок
    (swap). Год-ролл end+1г — ТОЛЬКО при реальном переходе через год, НЕ как лечение."""
    today = today or datetime.date.today()
    t = (text or "").lower().replace("–", "-").replace("—", "-").replace("ё", "е")
    dmw = [(int(m.group(1)), _mon(m.group(2))) for m in re.finditer(r"(\d{1,2})\s+" + _MONTH_RE, t)]
    dmy = re.findall(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", t)
    mwm = re.search(_MONTH_RE, t)
    mw = _mon(mwm.group(1)) if mwm else None
    rng = re.search(r"(?:с\s*)?(\d{1,2})\s*(?:-|по|до)\s*(\d{1,2})", t)
    term = _parse_term(t)

    s = e = None
    if len(dmw) >= 2:                                        # «28 декабря по 3 января»
        s, e = _resolve_range(dmw[0][0], dmw[0][1], dmw[1][0], dmw[1][1], today)
    elif len(dmy) >= 2:                                      # «10.07-15.07»
        s, e = _resolve_range(int(dmy[0][0]), int(dmy[0][1]), int(dmy[1][0]), int(dmy[1][1]),
                              today, dmy[0][2] or None, dmy[1][2] or None)
    elif rng and (mw or len(dmy) == 1):                     # «с 5 по 10 июля» / «с 5 по 10.07» / «5-10 июля»
        month = mw if mw else int(dmy[0][1])
        yhint = (dmy[0][2] or None) if len(dmy) == 1 else None
        s, e = _resolve_range(int(rng.group(1)), month, int(rng.group(2)), month, today, yhint, yhint)
    elif term:                                              # «на неделю с 5 июля» / «завтра на 3 дня»
        anchor = _anchor_date(t, today)
        if anchor:
            s = anchor
            e = anchor + datetime.timedelta(days=term[0])

    if not (s and e):
        return None, None
    if e < s:                                               # финальная страховка: swap, НЕ год-ролл
        s, e = e, s
    return s.isoformat(), e.isoformat()


def _client_messages(transcript: str):
    """Реплики КЛИЕНТА (не менеджера) в хронологическом порядке, lowercased."""
    return [ln[len("[клиент]:"):].strip().lower()
            for ln in (transcript or "").split("\n") if ln.startswith("[клиент]:")]


def _detect_model(text: str):
    for tok in _MODEL_TOKENS:
        if tok in text:
            return tok.upper().replace(" ", "")
    return None


def _detect_models(text: str):
    """ВСЕ модели, упомянутые в тексте (для запроса нескольких байков сразу). Порядок
    появления, без дублей; «ADV» отбрасываем, если есть более точная «ADV350» и т.п."""
    found = []
    for tok in _MODEL_TOKENS:
        if tok in text:
            canon = tok.upper().replace(" ", "")
            if canon not in found:
                found.append(canon)
    # снять префиксы, поглощённые более длинной моделью (ADV ⊂ ADV350, CBR ⊂ CBR650)
    return [c for c in found if not any(o != c and o.startswith(c) for o in found)]


# --- класс байка → минимальный срок аренды (правила цен v2, п.2) ---------------
# Скутеры сдаём от 5 дней (XSR155 тоже 5), мотоциклы — от 3.
SCOOTER_MIN_DAYS = 5
MOTO_MIN_DAYS = 3
_MOTO_PREFIXES = ("CB300", "CB650", "CBR", "REBEL", "MT", "NINJA", "VULCAN", "R7")
_SCOOTER_PREFIXES = ("PCX", "NMAX", "FORZA", "XMAX", "XADV", "ADV")


def bike_class(model):
    """(kind, min_days, label) для модели или None, если класс неизвестен.
    kind: 'scooter'|'moto'. label — как называть тип в сообщении клиенту."""
    m = re.sub(r"[^A-Z0-9]", "", (model or "").upper())
    if not m:
        return None
    if m.startswith("XSR"):
        if "900" in m:                       # XSR900 — большой мотоцикл (3)
            return ("moto", MOTO_MIN_DAYS, "мотоциклы")
        return ("scooter", SCOOTER_MIN_DAYS, "этот байк")   # XSR155 → минимум как у скутеров (5)
    for t in _MOTO_PREFIXES:
        if m.startswith(t):
            return ("moto", MOTO_MIN_DAYS, "мотоциклы")
    for t in _SCOOTER_PREFIXES:
        if m.startswith(t):
            return ("scooter", SCOOTER_MIN_DAYS, "скутеры")
    return None


def _asks_deposit_reduction_multi(newest: str, recent: str, models) -> bool:
    """Явный вопрос клиента про уменьшение депозита при нескольких байках (правила цен v2, п.4).
    newest — последняя реплика клиента, recent — склейка последних реплик, models — найденные модели."""
    dep = re.search(r"депозит|залог|deposit", newest or "")
    less = re.search(r"меньше|уменьш|сниз|скид|дешевл|пониз|lower|reduce|discount|less", newest or "")
    multi = re.search(r"нескольк|два\b|две\b|\bоба\b|\bобе\b|байка|байков|мотик|two|both|several",
                      recent or "")
    return bool(dep and less and (multi or len(models or []) >= 2))


def _explicit_monthly(text: str) -> bool:
    """monthly ТОЛЬКО по явному слову клиента, НЕ по длительности диапазона."""
    return bool(re.search(r"месяц|\bmonth\b|monthly", text or ""))


def _has_date_signal(text: str) -> bool:
    if re.search(r"\d{1,2}[.\-/]\d{1,2}", text or ""):
        return True
    if _parse_term(text):
        return True
    if re.search(r"с\s+\d{1,2}\s+(?:по|до)\s+\d{1,2}", text or ""):
        return True
    return any(mo in (text or "") for mo in _MONTHS)


BOOKING_WINDOW = 3  # сколько последних реплик клиента смотрим, чтобы дособрать ОДНУ бронь


def extract_booking_hints(transcript: str, today=None) -> dict:
    """Модель + даты ТОЛЬКО из ПОСЛЕДНЕЙ релевантной брони клиента (не из всего диалога).
    База — последняя реплика клиента; до 2 предыдущих его реплик смотрим лишь чтобы дособрать
    НЕДОСТАЮЩЕЕ той же брони. Разные модели / разные даты = разные брони: приоритет у последней,
    старую отбрасываем (не смешиваем). Возвращает {model, date_start, date_end, iso_start,
    iso_end, term_days, hint_days, monthly, has_dates}."""
    window = _client_messages(transcript)[-BOOKING_WINDOW:][::-1]  # новейшая первой
    newest = window[0] if window else ""
    recent = " ".join(window)

    model = iso_start = iso_end = term_days = None
    monthly = False

    for idx, msg in enumerate(window):
        m_model = _detect_model(msg)
        s, e = parse_date_range(msg, today)
        t = _parse_term(msg)
        if idx == 0:
            model = m_model
            if s and e:
                iso_start, iso_end = s, e
            if t:
                term_days = t[0]
            monthly = _explicit_monthly(msg)
            if model and (iso_start or term_days):
                break  # последняя реплика самодостаточна — назад не идём
            continue
        # предыдущая реплика окна: СТОП при признаках ДРУГОЙ брони (другая модель)
        if m_model and model and m_model != model:
            break
        # дособираем ТОЛЬКО недостающее (даты/срок), если у нас их ещё нет
        if iso_start is None and term_days is None:
            if s and e:
                iso_start, iso_end = s, e
                monthly = monthly or _explicit_monthly(msg)
            elif t:
                term_days = t[0]
                monthly = monthly or _explicit_monthly(msg)
        if model is None and m_model:
            model = m_model
        if model and (iso_start or term_days):
            break

    hint_days = None
    if iso_start and iso_end:
        try:
            hint_days = (datetime.date.fromisoformat(iso_end)
                         - datetime.date.fromisoformat(iso_start)).days
        except Exception:
            hint_days = None
    if hint_days is None:
        hint_days = term_days
    has_dates = bool(iso_start or term_days) or _has_date_signal(newest)

    # Несколько моделей в ОДНОМ запросе (правила цен v2, п.3) — берём из последней реплики;
    # одна/ноль → падаем на единственную разрешённую модель окна.
    newest_models = _detect_models(newest)
    if len(newest_models) >= 2:
        models = newest_models
    elif model:
        models = [model]
    else:
        models = newest_models
    deposit_multi_q = _asks_deposit_reduction_multi(newest, recent, models)

    return {"model": model, "models": models, "date_start": iso_start, "date_end": iso_end,
            "iso_start": iso_start, "iso_end": iso_end, "term_days": term_days,
            "hint_days": hint_days, "monthly": monthly, "has_dates": has_dates,
            "deposit_multi_q": deposit_multi_q}


# Инструкция про депозит при нескольких байках (правила цен v2, п.4).
DEPOSIT_MULTI_NOTE = ("ДЕПОЗИТ: клиент спрашивает про уменьшение депозита при нескольких байках "
                      "— сам скидку/снижение депозита НЕ предлагай и НЕ обещай; ответь ровно: "
                      "«уточню у менеджера».")


def _iso_plus(iso_date, n_days):
    """iso-дата + n_days дней → iso-строка или None."""
    try:
        return (datetime.date.fromisoformat(iso_date) + datetime.timedelta(days=int(n_days))).isoformat()
    except Exception:
        return None


def _safe_quote_for_model(model, ds, de):
    """pricing.quote_for_model без падений → всегда dict {status, quote}."""
    try:
        res = pricing.quote_for_model(model, ds, de)
    except Exception:
        res = None
    if not isinstance(res, dict):
        return {"status": "error", "quote": None}
    return res


def _client_price(q: dict) -> str:
    """Фраза ЦЕНЫ клиенту из quote. Правила цен v2, п.1 и п.5:
    кап (низкий сезон, total>cap_price) → «аренда от <cap> ฿/мес …» вместо J-цены (+депозит/наличие);
    иначе — поле text из quote ДОСЛОВНО (цена J); иначе — сборка из day_price/total/deposit."""
    cap_active = q.get("cap_active")
    cap_price = q.get("cap_price")
    total = q.get("total")
    if cap_active and cap_price is not None and total is not None and total > cap_price:
        parts = [f"аренда от {cap_price} ฿/мес — предложение низкого сезона"]
        if q.get("deposit") is not None:
            parts.append(f"депозит {q['deposit']} ฿")
        if q.get("available"):
            parts.append("свободен на эти даты")
        return "; ".join(parts)
    if isinstance(q.get("text"), str) and q["text"].strip():
        return q["text"].strip()               # J-цена дословно (п.5)
    parts = []
    if q.get("day_price") is not None:
        parts.append(f"{q['day_price']} ฿/день")
    if q.get("total") is not None:
        parts.append(f"итого {q['total']} ฿")
    if q.get("deposit") is not None:
        parts.append(f"депозит {q['deposit']} ฿")
    if q.get("available"):
        parts.append("свободен на эти даты")
    return "; ".join(parts)


def _resolve_model_price(model, ds, de, hint_days, monthly):
    """Цена для ОДНОЙ модели по датам ds..de. Возвращает (kind, phrase):
      ok    — цену использовать дословно (кап/J-текст/сборка внутри _client_price);
      min   — срок короче минимального: «<тип> сдаём от N дней» + цена на минимум;
      sanity/none/error — фолбэк без числа."""
    cls = bike_class(model)
    # (п.2) минимальный срок аренды: короче → предлагаем минимум и цену на него
    if cls and hint_days is not None and hint_days < cls[1]:
        min_days, label = cls[1], cls[2]
        de_min = _iso_plus(ds, min_days)
        q = None
        if de_min:
            res = _safe_quote_for_model(model, ds, de_min)
            if res.get("status") == "ok" and res.get("quote") and \
                    pricing.sanity_days_ok(res["quote"].get("days"), min_days, monthly):
                q = res["quote"]
        base = f"{label} сдаём от {min_days} дней (короче срок не оформляем)"
        if q:
            return ("min", f"{base}; цена за {min_days} дн: {_client_price(q)}")
        return ("min", f"{base}; точную цену за {min_days} дн уточню и вернусь")
    res = _safe_quote_for_model(model, ds, de)
    status, q = res.get("status"), res.get("quote")
    if status == "ok" and q:
        # SANITY-ГАРД: сверяем days из quote с длительностью из слов клиента.
        if not pricing.sanity_days_ok(q.get("days"), hint_days, monthly):
            return ("sanity", "расчёт по датам не сходится (длительность подозрительная) — НЕ "
                              "называй никакого числа; ответь, что уточню цену по датам и вернусь")
        return ("ok", _client_price(q))
    if status == "none_available":
        return ("none", "на эти даты все подходящие байки заняты — НЕ называй числа; ответь, что "
                        "уточню наличие и цену на эти даты и вернусь")
    return ("error", "точная цена из Календаря сейчас недоступна — НЕ называй никакого числа "
                     "(в т.ч. из FAQ); ответь, что уточнишь цену и вернёшься")


def _wrap_single(kind: str, phrase: str) -> str:
    if kind == "ok":
        return ("ЦЕНА из Календаря бронирования (использовать ДОСЛОВНО, не пересчитывать и не "
                "округлять): " + phrase + ".")
    return "ЦЕНА: " + phrase + "."


def build_pricing_note(hints: dict) -> str:
    """Инструкция по цене для промпта. ИНВАРИАНТ: без котировки из Календаря — без числа.
    Правила цен v2: кап низкого сезона (п.1), минимальный срок (п.2), несколько моделей одной
    строкой каждая (п.3), депозит при нескольких байках (п.4), J-текст дословно (п.5)."""
    # (п.4) депозит при нескольких байках — инструкция дописывается к ЛЮБОМУ исходу цены.
    dep = (" " + DEPOSIT_MULTI_NOTE) if hints.get("deposit_multi_q") else ""
    if not hints.get("has_dates"):
        return ("ЦЕНА: дат аренды в диалоге НЕТ — попроси у клиента даты (начало/конец) и срок. "
                "НЕ называй НИКАКУЮ цену: ни точную, ни ориентир, ни «от X ฿», ни диапазон "
                "(«X–Y ฿»), ни «from X» — вообще никаких чисел цены, в т.ч. из FAQ. "
                "Цену назовём только после дат, из Календаря.") + dep
    ds, de = hints.get("iso_start"), hints.get("iso_end")
    models = hints.get("models") or ([hints["model"]] if hints.get("model") else [])
    if not (models and ds and de):
        # даты есть словами, но модель/полные даты не разобрались → фолбэк БЕЗ числа
        return ("ЦЕНА: не удалось однозначно разобрать модель/даты для Календаря — НЕ называй "
                "никакого числа (в т.ч. из FAQ); уточни модель и точные даты и скажи, что "
                "назовёшь цену по датам.") + dep
    hint_days, monthly = hints.get("hint_days"), hints.get("monthly")

    if len(models) == 1:
        kind, phrase = _resolve_model_price(models[0], ds, de, hint_days, monthly)
        return _wrap_single(kind, phrase) + dep

    # (п.3) несколько моделей — раздельная цена по каждой, отдельной строкой в одном сообщении
    bullets = []
    for m in models:
        _, phrase = _resolve_model_price(m, ds, de, hint_days, monthly)
        bullets.append(f"- {m}: {phrase}")
    header = ("ЦЕНЫ ПО МОДЕЛЯМ (клиент запросил несколько) — назови КАЖДУЮ отдельной строкой в "
              "ОДНОМ сообщении, цену использовать ДОСЛОВНО, модели НЕ смешивай и НЕ суммируй:\n")
    return header + "\n".join(bullets) + dep


def make_system_prompt(faq: str, lang: str, is_first_contact: bool = False, pricing_note: str = "") -> str:
    lang_name = "русском" if lang == "ru" else "английском"
    if is_first_contact:
        greet = (
            "\n\nЭто ПЕРВЫЙ ответ в этом диалоге — НАЧНИ ответ с фирменного приветствия из "
            "FAQ («Здравствуйте! …»), затем переходи к сути."
        )
    else:
        greet = (
            "\n\nЭто ПРОДОЛЖЕНИЕ диалога — НЕ здоровайся повторно, сразу отвечай по сути."
        )
    policy = (
        "\n\nЦЕНОВАЯ ПОЛИТИКА (СТРОГО): цену клиенту называй ТОЛЬКО если она передана ниже в "
        "блоке «ЦЕНА из Календаря». Пока такой цены нет (или дат в диалоге нет) — НЕ называй "
        "клиенту НИКАКУЮ цену: ни точную, ни ориентир, ни «от X», ни диапазон, ни «from», ни "
        "ставку из FAQ. Дат нет — сперва спроси даты. Цена недоступна — скажи, что уточнишь "
        "цену по датам и вернёшься, БЕЗ числа. Дневные ставки FAQ — ориентир ДЛЯ МЕНЕДЖЕРА, "
        "не для клиента. Про уменьшение депозита при нескольких байках сам НЕ предлагай и НЕ "
        "обещай; на прямой вопрос клиента ответь ровно «уточню у менеджера»."
    )
    scenario = (
        "\n\nПОРЯДОК ДИАЛОГА (СТРОГО по этапам, не забегай вперёд):\n"
        "Этап 1 — ЦЕНА: назови цену по датам из Календаря (если она в блоке ЦЕНА выше). "
        "На этом этапе НЕ проси паспорт, апартаменты и шлемы — только цена и наличие.\n"
        "Этап 2 — ДОСТАВКА: когда клиент заинтересовался ценой — уточни район доставки и назови "
        "её стоимость по прайсу районов; добавь, что забор байка в конце аренды БЕСПЛАТНЫЙ.\n"
        "Этап 3 — БРОНЬ: только когда клиент готов бронировать — запроси качественное фото "
        "паспорта, название апартаментов (или ссылку Google Maps), количество шлемов и "
        "номер(а) телефона. Раньше этапа 3 документы/апартаменты/шлемы не запрашивай."
    )
    price_block = ("\n\n" + pricing_note) if pricing_note else ""
    return (
        "Ты — менеджер проката мотобайков TurboBaby (Пхукет). По переписке с клиентом "
        f"составь ОДИН короткий, вежливый ответ на {lang_name} языке (язык клиента). "
        "Отвечай только на то, что ещё НЕ отвечено менеджером в диалоге; не повторяй уже "
        "сказанное; держи контекст сделки. Не выдумывай данные и наличие. Верни ТОЛЬКО "
        "текст ответа клиенту — без пояснений, без кавычек, без префиксов."
        + greet + policy + scenario + price_block + "\n\n"
        + CRITICAL_FACTS
        + "\n\nFAQ и эталонные формулировки:\n" + (faq or "(FAQ недоступен — опирайся на критичные факты выше)")
    )


def _default_llm(system: str, user: str) -> str:
    import anthropic
    c = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = c.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


_CLAUDE_BASE = os.path.join(os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
                            "Claude", "claude-code")


def _resolve_claude_once():
    """Один проход резолва (без ретраев). PATH-шим → CLAUDE_BIN если жив → новейшая версия в AppData
    → кандидаты иных схем установки. → путь|None."""
    w = shutil.which("claude")
    if w and os.path.isfile(w):
        return w
    if CLAUDE_BIN and os.path.isabs(CLAUDE_BIN) and os.path.isfile(CLAUDE_BIN):
        return CLAUDE_BIN
    try:
        cands = []
        for d in glob.glob(os.path.join(_CLAUDE_BASE, "*")):
            exe = os.path.join(d, "claude.exe")
            if os.path.isfile(exe):
                nums = re.findall(r"\d+", os.path.basename(d))
                cands.append((tuple(int(n) for n in nums) if nums else (0,), exe))
        if cands:
            cands.sort()
            return cands[-1][1]
    except Exception:
        pass
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude.cmd"),
              os.path.join(local, "Programs", "claude", "claude.exe")):
        if os.path.isfile(c):
            return c
    return None


def _resolve_claude(retries=1, retry_sleep=2.0):
    """Версионно-независимый резолв claude CLI (порт pc_orchestrator.resolve_claude, коммит 45bdd75 —
    не импортируем pc_orchestrator, чтобы не тянуть его logging.basicConfig/Bridge в userbot).
    НЕ сдаёмся с первой осечки: транзиентный os.path.isfile()==False на 236-МБ claude.exe
    (AV-скан/локация файла — кейс теста 00:33 и задачи #35) не должен убивать черновик — короткий
    ретрай (2 попытки, пауза ~2с). → путь (str) | None (после ретраев — реально нигде нет)."""
    for i in range(retries + 1):
        p = _resolve_claude_once()
        if p:
            return p
        if i < retries:
            log.warning("SUGGEST: claude CLI не найден (проход %s/%s) — транзиент? повтор через %sс",
                        i + 1, retries + 1, retry_sleep)
            time.sleep(retry_sleep)
    return None


def _cli_llm(system: str, user: str) -> str:
    """Генерация через claude CLI по подписке Max (не платный API-ключ → нет 'credit balance too low').
    ЧИСТЫЙ генератор текста, НЕ агент: --allowed-tools '' + нейтральный cwd (НЕ репо) → CLI не читает
    .claude/settings.json+pretool_guard и не дёргает инструменты. ANTHROPIC_API_KEY вычищен из env,
    иначе CLI пошёл бы по платному ключу. Таймаут/ошибка → '' (upstream: 'пустой черновик' → пропуск)."""
    cbin = _resolve_claude()
    if not cbin:
        raise RuntimeError("claude CLI не найден (PATH/CLAUDE_BIN/AppData) — генерация через CLI невозможна")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # ← ключ НЕ утекает: CLI идёт по подписке Max
    env.pop("OPENAI_API_KEY", None)
    try:
        p = subprocess.run(
            [cbin, "-p", user, "--system-prompt", system, "--model", CLI_MODEL, "--allowed-tools", ""],
            cwd=tempfile.gettempdir(),          # нейтральный cwd: без settings.json/pretool_guard репо
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.warning("SUGGEST: claude CLI таймаут %sс — пустой черновик", CLI_TIMEOUT)
        return ""
    if p.returncode != 0:
        log.warning("SUGGEST: claude CLI rc=%s — %s", p.returncode, (p.stderr or "").strip()[-300:])
        return ""
    return (p.stdout or "").strip()


def generate_draft(transcript: str, lang: str, faq: str,
                   is_first_contact: bool = False, pricing_note: str = "", call_llm=None) -> str:
    """Сгенерировать черновик. call_llm(system, user)->str инъектируется в тестах; иначе по флагу
    SUGGEST_LLM_VIA_CLI — claude CLI (подписка Max) либо _default_llm (платный API-ключ)."""
    call_llm = call_llm or (_cli_llm if SUGGEST_LLM_VIA_CLI else _default_llm)
    system = make_system_prompt(faq, lang, is_first_contact, pricing_note)
    return call_llm(system, transcript).strip()


# ------------------------------- rate-limit ----------------------------------

class RateLimiter:
    """Лимит отправок: не больше per_hour в час и per_day в сутки. now — инъекция."""

    def __init__(self, per_hour: int, per_day: int, now=time.time):
        self.per_hour = per_hour
        self.per_day = per_day
        self.now = now
        self._sends = []

    def allow(self):
        t = self.now()
        self._sends = [s for s in self._sends if t - s < 86400]
        in_hour = [s for s in self._sends if t - s < 3600]
        if len(self._sends) >= self.per_day:
            return False, f"дневной лимит {self.per_day} исчерпан"
        if len(in_hour) >= self.per_hour:
            return False, f"часовой лимит {self.per_hour} исчерпан"
        return True, None

    def record(self):
        self._sends.append(self.now())


limiter = RateLimiter(RATE_PER_HOUR, RATE_PER_DAY)


# ------------------------------- pending-store -------------------------------

class PendingStore:
    """map moderation_msg_id → {client_id, draft, lang, incoming}. In-memory + durable
    jsonl-журнал событий (add/del), переживает рестарт процесса."""

    def __init__(self, path: str = PENDING_FILE):
        self.path = path
        self._d = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("ev") == "add":
                        self._d[int(ev["mid"])] = ev["rec"]
                    elif ev.get("ev") == "del":
                        self._d.pop(int(ev["mid"]), None)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"SUGGEST: pending _load: {e}")

    def _append(self, obj):
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning(f"SUGGEST: pending _append: {e}")

    def add(self, mid: int, rec: dict):
        self._d[int(mid)] = rec
        self._append({"ev": "add", "mid": int(mid), "rec": rec})

    def get(self, mid: int):
        return self._d.get(int(mid))

    def pop(self, mid: int):
        rec = self._d.pop(int(mid), None)
        if rec is not None:
            self._append({"ev": "del", "mid": int(mid)})
        return rec


pending = PendingStore()


# ------------------------------- лог обучения --------------------------------

def _edit_diff(draft: str, final: str) -> str:
    """Компактный след правки: '' если не правили, иначе финал (перекрывает черновик)."""
    return "" if (final or "").strip() == (draft or "").strip() else final


def record_pair(rec: dict, path: str = None):
    """Запись пары обучения в suggest_pairs.jsonl:
    {ts, client, lang, incoming, draft, action, final_sent, edit}."""
    path = path or PAIRS_FILE
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"SUGGEST: record_pair: {e}")


# ------------------------------- отправка ------------------------------------

async def send_to_client(client, client_id, text, sleep=None, jitter=None):
    """Отправка клиенту с гардами. Возвращает (ok, reason). При флуде — авто-стоп.
    Defense-in-depth: в TEST_MODE отправка клиенту заблокирована и ЗДЕСЬ (второй слой,
    помимо гейта в on_moderation_reply) — чтобы никакой путь не смог написать клиенту."""
    if SUGGEST_TEST_MODE:
        log.info("SUGGEST[TEST_MODE] send_to_client заблокирован — клиенту ничего не уходит.")
        return False, "TEST_MODE"
    sleep = sleep or asyncio.sleep
    jitter = jitter or (lambda: random.uniform(PAUSE_MIN, PAUSE_MAX))

    ok, reason = limiter.allow()
    if not ok:
        log.warning(f"SUGGEST: отправка отклонена — {reason}")
        return False, reason
    try:
        async with client.action(client_id, "typing"):
            await sleep(jitter())
            await client.send_message(client_id, text)
    except FLOOD_ERRORS as e:  # FloodWait/PeerFlood → стоп
        disable(f"flood при отправке: {e}")
        return False, "flood"
    except Exception as e:
        log.warning(f"SUGGEST: ошибка отправки: {e}")
        return False, str(e)
    limiter.record()
    return True, None


# ---------------------- оркестрация (зовётся из хендлеров) -------------------

async def post_draft(client, draft: str, client_ref: str):
    """Отправить черновик на модерацию. Есть MOD_GROUP_ID → в группу, иначе в лог.
    Возвращает moderation_msg_id (int) — ключ pending. При логе — синтетический id."""
    header = f"✏️ Черновик ответа клиенту {client_ref}\n(reply: «+»/«да» — отправить, свой текст — правка, «-» — отклонить)\n\n"
    if MOD_GROUP_ID is not None:
        msg = await client.send_message(MOD_GROUP_ID, header + draft)
        return int(getattr(msg, "id", 0))
    # нет группы (или обкатка без группы) — черновик в лог, синтетический id
    mid = int(time.time() * 1000) % 2147483647
    log.info(f"SUGGEST[draft→log mid={mid}] {client_ref}: {draft}")
    return mid


async def post_mod_note(client, text: str):
    """Служебная заметка в группу «Модерация» (НЕ клиенту!). Конец слепоты: любой сбой генерации
    виден сразу в группе, без раскопок лога. Есть MOD_GROUP_ID → в группу, иначе в лог. Не падает."""
    try:
        if MOD_GROUP_ID is not None:
            await client.send_message(MOD_GROUP_ID, text)
        else:
            log.warning(f"SUGGEST[note→log] {text}")
    except Exception as e:
        log.warning(f"SUGGEST: post_mod_note упал: {e} | {text}")


async def resolve_mod_group(client):
    """Если MOD_GROUP_ID пуст и задано MOD_GROUP_NAME — найти группу по title через
    iter_dialogs и подставить её id. Возвращает id или None. Не падает: не нашли →
    лог + None (черновики пойдут в лог, как при отсутствии ID)."""
    global MOD_GROUP_ID
    if MOD_GROUP_ID is not None:
        return MOD_GROUP_ID
    if not MOD_GROUP_NAME:
        return None
    try:
        async for d in client.iter_dialogs():
            title = getattr(d, "title", None) or getattr(d, "name", None)
            if title == MOD_GROUP_NAME:
                MOD_GROUP_ID = int(d.id)
                log.info(f"SUGGEST: MOD_GROUP resolved: {MOD_GROUP_ID} (по имени «{MOD_GROUP_NAME}»).")
                return MOD_GROUP_ID
        log.warning(
            f"SUGGEST: группа «{MOD_GROUP_NAME}» не найдена среди диалогов — "
            f"черновики пойдут в ЛОГ (approve недоступен, отправки клиенту нет)."
        )
    except Exception as e:
        log.warning(f"SUGGEST: resolve_mod_group упал: {e} — черновики в лог.")
    return None


def bot_mode_active():
    """Активен ли бот-модератор (задача-2): есть токен И свежий heartbeat в IPC.
    Нет → деградация в reply-режим userbot (текущее поведение). Без токена IPC не трогаем."""
    if not MODERBOT_TOKEN:
        return False
    try:
        import moderation_ipc
        return moderation_ipc.is_bot_alive()
    except Exception as e:
        log.warning(f"SUGGEST: проверка bot-mode упала ({e}) — деградация в reply-режим.")
        return False


async def poll_and_send(client, sender=None, jitter=None, sleep=None):
    """Исполнитель userbot: разобрать решения бота (status='ready') из IPC и отправить
    клиенту. ЕДИНСТВЕННАЯ точка отправки клиенту в bot-режиме. Double-lock: в TEST_MODE
    send_to_client откажет (сюда 'ready' в TEST_MODE и не должен попасть — бот ставит
    'test_held'). Возвращает число обработанных."""
    if not is_enabled():
        return 0
    try:
        import moderation_ipc
        rows = moderation_ipc.fetch_ready()
    except Exception as e:
        log.warning(f"SUGGEST: poll_and_send: IPC недоступен ({e}).")
        return 0
    n = 0
    _send = sender or send_to_client
    for r in rows:
        final = r.get("final_text") or ""
        if not final:
            moderation_ipc.mark(r["id"], "failed", reason="пустой final_text")
            continue
        ok, reason = await _send(client, r["client_id"], final, sleep=sleep, jitter=jitter)
        moderation_ipc.mark(r["id"], "sent" if ok else "failed", reason=reason)
        record_pair({
            "ts": _now_iso(), "client": r.get("client_ref"), "lang": r.get("lang"),
            "incoming": r.get("incoming"), "draft": r.get("draft"),
            "action": "bot_decision", "final_sent": final if ok else "",
            "edit": _edit_diff(r.get("draft") or "", final), "first_contact": bool(r.get("first_contact")),
            "sent": ok, "reason": reason, "via": "moderation_bot",
        })
        n += 1
    return n


async def on_client_message(client, sender, me_id, call_llm=None, faq=None):
    """Врезка в on_incoming: собрать диалог → черновик → на модерацию.
    bot-режим → в IPC (бот запостит карточку с кнопками); иначе reply-режим (в группу)."""
    if not is_enabled():
        return None
    client_id = sender.id
    client_ref = f"@{sender.username}" if getattr(sender, "username", None) else f"id{sender.id}"
    msgs = await _fetch_messages(client, sender)
    transcript = transcript_from(msgs, me_id)
    first = first_contact_from(msgs, me_id)   # первый контакт → приветствие в черновике
    last_client_line = ""
    for ln in reversed(transcript.split("\n")):
        if ln.startswith("[клиент]:"):
            last_client_line = ln[len("[клиент]:"):].strip()
            break
    lang = detect_lang(last_client_line or transcript)
    faq = faq if faq is not None else load_faq()
    # Двухфазная цена: даты есть → пробуем Календарь (pricing.quote), иначе/None → фолбэк.
    price_note = build_pricing_note(extract_booking_hints(transcript))
    try:
        draft = generate_draft(transcript, lang, faq, is_first_contact=first,
                               pricing_note=price_note, call_llm=call_llm)
    except Exception as e:   # сбой генератора (напр. claude CLI не найден / API-ошибка) — НЕ молчим
        reason = " ".join(str(e).split())[:200] or type(e).__name__
        log.warning(f"SUGGEST: сбой генерации для {client_ref}: {reason}")
        await post_mod_note(client, f"⚠️ Черновик НЕ сгенерирован для {client_ref}: {reason}")
        return None
    if not draft:            # пустой вывод LLM — раньше молчали, теперь видно в группе (конец слепоты)
        log.warning(f"SUGGEST: пустой черновик для {client_ref} — пропускаю.")
        await post_mod_note(client, f"⚠️ Черновик НЕ сгенерирован для {client_ref}: пустой вывод LLM")
        return None

    rec = {
        "client_id": client_id, "client_ref": client_ref, "lang": lang,
        "incoming": last_client_line, "draft": draft, "first_contact": first,
    }
    # bot-режим: кладём в IPC, карточку с кнопками запостит moderation_bot.
    if bot_mode_active():
        try:
            import moderation_ipc
            did = moderation_ipc.enqueue_draft(rec)
            log.info(f"SUGGEST: черновик #{did} → IPC (bot-режим) для {client_ref}.")
            return f"ipc:{did}"
        except Exception as e:
            log.warning(f"SUGGEST: IPC-enqueue упал ({e}) — деградация в reply-режим.")
    # reply-режим (деградация / бот не поднят): постим в группу сами + pending.
    mid = await post_draft(client, draft, client_ref)
    rec["ts"] = _now_iso()
    pending.add(mid, rec)
    return mid


async def _sender_username(event):
    """Юзернейм автора reply (для проверки прав). Без падения."""
    s = getattr(event, "sender", None)
    if s is None and hasattr(event, "get_sender"):
        try:
            s = await event.get_sender()
        except Exception:
            s = None
    return getattr(s, "username", None)


async def _ack(event, draft_msg_id, text):
    """Видимый ответ userbot'а на сообщение-черновик в ГРУППЕ МОДЕРАЦИИ (не клиенту)."""
    try:
        chat = getattr(event, "chat_id", None)
        if chat is None:
            chat = getattr(getattr(event, "message", None), "chat_id", None)
        await event.client.send_message(chat, text, reply_to=draft_msg_id)
    except Exception as e:
        log.warning(f"SUGGEST: не смог отправить ack модерации: {e}")


async def on_moderation_reply(event, sender=None, jitter=None, sleep=None):
    """Врезка во второй хендлер: reply менеджера в группе модерации → approve/edit/reject.
    NO-SILENT-PATHS: каждый reply получает видимый ответ userbot'а (reply на черновик).
    Права approve/edit/reject — по APPROVER-whitelist. sender/jitter/sleep — инъекция для тестов."""
    if not is_enabled():
        return None
    reply_to = None
    if getattr(event, "is_reply", False):
        reply_to = getattr(event, "reply_to_msg_id", None)
    if reply_to is None:
        return None
    rec = pending.get(reply_to)
    if rec is None:
        return None  # reply не на наш черновик

    # Права: не-approver → ⛔ и НЕ трогаем pending (пусть approver ещё сможет решить).
    username = await _sender_username(event)
    if not is_approver(username):
        await _ack(event, reply_to, "⛔ Нет прав на approve")
        return {"action": "denied", "sent": False, "reason": "not_approver", "final": None}

    action, payload = parse_approval(getattr(event, "raw_text", "") or getattr(event, "text", ""))
    final = None
    sent = False
    reason = None
    if action == "reject":
        ack = "❌ Отклонено"
    else:
        final = rec["draft"] if action == "approve" else payload
        if SUGGEST_TEST_MODE:
            # SAFETY: в TEST_MODE send_to_client НЕ вызываем — клиенту ничего не уходит.
            reason = "TEST_MODE"
            log.info(f"SUGGEST[TEST_MODE] одобрено, НЕ шлю клиенту {rec['client_ref']}: {final}")
            ack = ("🧪 Принято (TEST_MODE — клиенту НЕ отправлено)" if action == "approve"
                   else "✅ Правка принята 🧪 (TEST_MODE — клиенту НЕ отправлено)")
        else:
            _send = sender or send_to_client
            sent, reason = await _send(event.client, rec["client_id"], final, sleep=sleep, jitter=jitter)
            if action == "approve":
                ack = "✅ Отправлено клиенту" if sent else f"⚠️ Не отправлено: {reason}"
            else:
                ack = ("✅ Правка принята — отправлено клиенту" if sent
                       else f"✅ Правка принята, ⚠️ не отправлено: {reason}")

    await _ack(event, reply_to, ack)
    record_pair({
        "ts": _now_iso(), "client": rec["client_ref"], "lang": rec["lang"],
        "incoming": rec["incoming"], "draft": rec["draft"], "action": action,
        "final_sent": final if sent else ("" if action == "reject" else final),
        "edit": _edit_diff(rec["draft"], final or ""),
        "first_contact": rec.get("first_contact"), "sent": sent, "reason": reason, "ack": ack,
    })
    pending.pop(reply_to)
    return {"action": action, "sent": sent, "reason": reason, "final": final, "ack": ack}
