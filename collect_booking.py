"""
collect_booking.py — сборщик карточки брони из ВЫБРАННОГО диалога. ЭТАП 1.

ЧТО ДЕЛАЕТ (этап 1 — собрать и ПОКАЗАТЬ, без отправки):
  1. находит личный диалог по @username или id (аргумент командной строки);
  2. читает последние 50 сообщений диалога, строит текст "[менеджер]/[клиент]: ...";
  3. шлёт транскрипт в Anthropic с системным промптом из спеки → получает JSON;
  4. собирает карточку и ВЫВОДИТ её в консоль. НИКУДА НЕ ОТПРАВЛЯЕТ.

ГАРД (критично): клиенту НИЧЕГО не пишется. Файл только ЧИТАЕТ диалог
(iter_messages / get_entity). Ни одного client.send_message. Ноль исходящих.
Отправка карточки во «Входящие» — это ЭТАП 2, здесь её НЕТ.

Запуск (ТОЛЬКО на ПК):
    cd D:\\turbobaby-bot
    venv\\Scripts\\activate
    python collect_booking.py @Egor_blg_28
    python collect_booking.py 123456789

Требуется в .env (вне git):
  API_ID, API_HASH        — как у fetch_*.py (session turbobaby_session)
  ANTHROPIC_API_KEY       — ключ Anthropic (ВПИСАТЬ, если ещё нет)
  ANTHROPIC_MODEL         — необязательно; по умолчанию claude-sonnet-4-6

СИСТЕМНЫЙ ПРОМПТ — берётся ЦЕЛИКОМ из KB_collect_booking_spec и кладётся в файл
collect_booking_prompt.txt рядом с этим скриптом. Логику оценки опыта
(ok/review/unknown), формат карточки и тарифы НЕ переписываем — они в промпте/спеке,
выверены по 710 реальным диалогам. Пока промпт не вставлен — скрипт НЕ зовёт LLM.

Зависимости: telethon, python-dotenv, anthropic (уже стоят в venv).
"""

import os
import re
import sys
import json
import asyncio

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import User

# Консоль Windows часто cp1251 — карточка с эмодзи (🆕 и т.п.) иначе валит print
# UnicodeEncodeError. Принудительно переводим вывод в utf-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("TELETHON_SESSION_NAME", "turbobaby_session")  # та же сессия, что fetch_*.py
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_FILE = os.path.join(BASE_DIR, "collect_booking_prompt.txt")
# Маркер-заглушка: пока он в файле — промпт ещё не вставлен (см. KB_collect_booking_spec).
PROMPT_PLACEHOLDER = "ВСТАВЬ СЮДА"

MAX_MESSAGES = 50          # последние N сообщений диалога
LLM_MAX_TOKENS = 2000


# ----------------------------- системный промпт -----------------------------

def load_system_prompt() -> str:
    """Прочитать системный промпт из collect_booking_prompt.txt. Без него LLM не зовём."""
    if not os.path.exists(PROMPT_FILE):
        sys.exit(
            f"Нет файла {PROMPT_FILE}.\n"
            "Вставь в него системный промпт ЦЕЛИКОМ из KB_collect_booking_spec и запусти снова."
        )
    with open(PROMPT_FILE, encoding="utf-8") as f:
        text = f.read().strip()
    if not text or PROMPT_PLACEHOLDER in text:
        sys.exit(
            f"В {PROMPT_FILE} ещё заглушка. Замени её системным промптом из "
            "KB_collect_booking_spec (логику опыта/формат карточки НЕ менять) и запусти снова."
        )
    return text


# ------------------------------- чтение диалога ------------------------------

async def resolve_entity(client, target: str):
    """Найти собеседника по @username или числовому id. None — если не найден."""
    try:
        if target.lstrip("-").isdigit():
            return await client.get_entity(int(target))
        return await client.get_entity(target)  # понимает @username и username
    except Exception:
        return None


async def read_transcript(client, entity, me_id: int):
    """Собрать текст диалога '[менеджер]/[клиент]: ...' в хронологическом порядке."""
    msgs = []
    async for m in client.iter_messages(entity, limit=MAX_MESSAGES):
        msgs.append(m)
    msgs.reverse()  # от старых к новым

    lines = []
    for m in msgs:
        who = "менеджер" if (m.sender_id == me_id) else "клиент"
        body = (m.message or "").strip()
        if not body:
            body = "[медиа/без текста]"
        # одна строка на сообщение
        body = body.replace("\n", " ⏎ ")
        lines.append(f"[{who}]: {body}")
    return "\n".join(lines), len(msgs)


# --------------------------------- LLM-парсер --------------------------------

def extract_json(text: str):
    """Достать JSON из ответа LLM (с код-фенсами или без). Бросает ValueError, если не JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = re.sub(r"^json\s*", "", t, flags=re.I).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)  # первый {...} в тексте
        if m:
            return json.loads(m.group(0))
        raise ValueError("ответ LLM не похож на JSON")


def call_llm(system_prompt: str, transcript: str) -> str:
    """Вызвать Anthropic, вернуть текст ответа."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": transcript}],
    )
    # склеиваем все текстовые блоки ответа
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


# --------------------------------- карточка ----------------------------------

def render_card(data: dict) -> str:
    """Показать карточку (ЭТАП 1 — предпросмотр, НЕ отправляется).

    Если промпт из спеки уже выдаёт готовую карточку строкой (поле card/карточка/
    card_text) — печатаем её как есть (формат из спеки, не трогаем). Иначе —
    нейтральный дамп полей JSON для проверки.
    """
    for key in ("card", "карточка", "card_text"):
        if isinstance(data.get(key), str) and data[key].strip():
            return data[key].strip()
    lines = ["🆕 БРОНЬ — предпросмотр (ЭТАП 1, НЕ отправлено)"]
    for k, v in data.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


# ----------------------------------- main ------------------------------------

async def main():
    if len(sys.argv) < 2:
        sys.exit("Использование: python collect_booking.py @username | id")
    target = sys.argv[1].strip()

    if not ANTHROPIC_API_KEY:
        sys.exit("Нет ANTHROPIC_API_KEY в .env. Впиши строкой ANTHROPIC_API_KEY=... и запусти снова.")

    system_prompt = load_system_prompt()  # упадёт с понятным текстом, если промпт не вставлен

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    try:
        me = await client.get_me()

        entity = await resolve_entity(client, target)
        if entity is None or not isinstance(entity, User):
            print(f"диалог {target} не найден")
            return

        transcript, n = await read_transcript(client, entity, me.id)
        if n == 0 or not transcript.strip():
            print("диалог пуст")
            return

        peer = f"@{entity.username}" if entity.username else f"id{entity.id}"
        print(f"# диалог {peer}: прочитано сообщений — {n}\n")

        raw = call_llm(system_prompt, transcript)
        try:
            data = extract_json(raw)
        except Exception:
            print("LLM вернул не-JSON. Сырой ответ:\n")
            print(raw)
            return

        print(render_card(data))
    finally:
        await client.disconnect()  # только читали; ничего не отправляли


if __name__ == "__main__":
    asyncio.run(main())
