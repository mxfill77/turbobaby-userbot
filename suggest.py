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
import json
import time
import random
import asyncio
import re
import logging
import datetime

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
Прайс-ОРИЕНТИР (НЕ финальная цена; точная — по датам из Календаря бронирования;
клиенту как финальную НЕ называть) (฿/день, депозит ฿):
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


def extract_booking_hints(transcript: str) -> dict:
    """Грубая эвристика: модель + признак наличия дат/срока в словах КЛИЕНТА.
    Возвращает {model, date_start, date_end, term_days, has_dates}. Достаточно для выбора
    фазы; точную цену всё равно даёт только Календарь."""
    text = _client_text(transcript)
    model = None
    for tok in _MODEL_TOKENS:
        if tok in text:
            model = tok.upper().replace(" ", "")
            break
    dates = re.findall(r"\b(\d{1,2}[.\-/]\d{1,2}(?:[./]\d{2,4})?)\b", text)
    date_start = dates[0] if dates else None
    date_end = dates[1] if len(dates) > 1 else None
    m_term = (re.search(r"на\s+(\d{1,3})\s*(дн|дней|день|сут|недел|мес)", text)
              or re.search(r"(\d{1,3})\s*(day|days|week|month)", text))
    term_days = None
    if m_term:
        try:
            term_days = int(m_term.group(1))
        except Exception:
            term_days = None
    m_range = re.search(r"с\s+(\d{1,2})\s+(?:по|до)\s+(\d{1,2})", text)
    has_month = any(mo in text for mo in _MONTHS)
    has_dates = bool(dates or term_days or m_range or has_month)
    return {"model": model, "date_start": date_start, "date_end": date_end,
            "term_days": term_days, "has_dates": has_dates}


def build_pricing_note(hints: dict) -> str:
    """Инструкция по цене для промпта. ИНВАРИАНТ: без котировки из Календаря — без числа."""
    if not hints.get("has_dates"):
        return ("ЦЕНА: дат аренды в диалоге НЕТ — попроси у клиента даты (начало/конец) и срок. "
                "Точную цену НЕ называй, никаких чисел цены (даже ориентировочных из FAQ).")
    try:
        q = pricing.quote(hints.get("model"), hints.get("date_start"), hints.get("date_end"))
    except Exception:
        q = None
    if q:
        parts = []
        if q.get("day_price") is not None:
            parts.append(f"{q['day_price']} ฿/день")
        if q.get("total") is not None:
            parts.append(f"итого {q['total']} ฿")
        if q.get("deposit") is not None:
            parts.append(f"депозит {q['deposit']} ฿")
        if q.get("available") is not None:
            parts.append("свободен" if q["available"] else "занят на эти даты")
        return ("ЦЕНА из Календаря бронирования (использовать ДОСЛОВНО, не пересчитывать и не "
                "округлять): " + "; ".join(parts) + ".")
    return ("ЦЕНА: точная цена из Календаря сейчас недоступна — НЕ называй никакого числа "
            "(в т.ч. из FAQ); ответь, что уточнишь цену и вернёшься.")


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
        "\n\nЦЕНОВАЯ ПОЛИТИКА (СТРОГО): финальную/точную цену клиенту называй ТОЛЬКО если она "
        "передана ниже в блоке «ЦЕНА из Календаря». Дневные ставки из FAQ — ОРИЕНТИР, НЕ "
        "финальная цена: клиенту как финальную НЕ называй. Дат нет — сперва спроси даты. Если "
        "точная цена недоступна — скажи, что уточнишь цену и вернёшься, БЕЗ числа."
    )
    price_block = ("\n\n" + pricing_note) if pricing_note else ""
    return (
        "Ты — менеджер проката мотобайков TurboBaby (Пхукет). По переписке с клиентом "
        f"составь ОДИН короткий, вежливый ответ на {lang_name} языке (язык клиента). "
        "Отвечай только на то, что ещё НЕ отвечено менеджером в диалоге; не повторяй уже "
        "сказанное; держи контекст сделки. Не выдумывай данные и наличие. Верни ТОЛЬКО "
        "текст ответа клиенту — без пояснений, без кавычек, без префиксов."
        + greet + policy + price_block + "\n\n"
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


def generate_draft(transcript: str, lang: str, faq: str,
                   is_first_contact: bool = False, pricing_note: str = "", call_llm=None) -> str:
    """Сгенерировать черновик. call_llm(system, user)->str инъектируется в тестах."""
    call_llm = call_llm or _default_llm
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
    draft = generate_draft(transcript, lang, faq, is_first_contact=first,
                           pricing_note=price_note, call_llm=call_llm)
    if not draft:
        log.warning(f"SUGGEST: пустой черновик для {client_ref} — пропускаю.")
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
