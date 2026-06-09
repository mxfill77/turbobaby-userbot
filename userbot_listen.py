"""
userbot_listen.py — слушающее ядро userbot @turbophuket (ЭТАП C).

ТОЛЬКО слушает входящие ЛИЧНЫЕ сообщения и логирует их в userbot.log.
НИЧЕГО не отправляет, не пересылает, не реагирует — НОЛЬ исходящих сообщений.
Это первый постоянно живущий процесс (в отличие от разведчиков fetch_*/recon_*,
которые делают disconnect и выходят сразу).

Запускать ТОЛЬКО с ПК (D:\\turbobaby-bot), где лежат .env и
turbobaby_session.session — те же ключи и та же сессия, что у fetch_*.py.
.env и *.session НЕ в git и кодом не трогаются.

Запуск:
    cd D:\\turbobaby-bot
    venv\\Scripts\\activate
    python userbot_listen.py

Остановка: Ctrl+C (аккуратно отключится от Telegram).

Зависимости: telethon, python-dotenv (уже стоят, как у fetch_*.py).

ЭТАП C — гарантия: в этом файле НЕТ client.send_message / reply / forward /
respond. Userbot ничего не пишет. Доступ к мозгу/Drive не используется.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import User

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("TELETHON_SESSION_NAME", "turbobaby_session")  # та же сессия, что fetch_*.py

# Свои аккаунты — их сообщения НЕ логируем (как в fetch_client_chats.py).
OWN_USERNAMES = {"turbophuket", "turbophuket1"}

LOG_FILE = "userbot.log"

# Логирование: файл (utf-8) + stdout. Каждая строка самодостаточна (своя ISO-дата).
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("userbot")


def _now() -> str:
    """ISO-метка текущего момента (UTC) для служебных строк журнала."""
    return datetime.now(timezone.utc).isoformat()


async def on_incoming(event):
    """Обработчик входящих сообщений. ЭТАП C: только логируем, ничего не шлём."""
    # 1) Только личные диалоги (не группы, не каналы).
    if not event.is_private:
        return

    sender = await event.get_sender()
    # 2) Только живые пользователи; ботов исключаем.
    if not isinstance(sender, User) or sender.bot:
        return

    # 3) Исключаем свои же аккаунты.
    if (sender.username or "").lower() in OWN_USERNAMES:
        return

    who = f"@{sender.username}" if sender.username else f"id{sender.id}"
    name = " ".join(filter(None, [sender.first_name, sender.last_name])) or "?"
    # Текст в одну строку, чтобы журнал оставался по строке на сообщение.
    text = (event.raw_text or "").replace("\n", " ⏎ ").strip() or "[без текста / медиа]"
    date_iso = event.message.date.isoformat()

    log.info(f"{date_iso} | {who} | {name} | {text}")


async def main():
    # Клиент создаём внутри main (под asyncio.run) — как в fetch_client_chats.py,
    # чтобы Telethon корректно привязался к event loop.
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.add_event_handler(on_incoming, events.NewMessage(incoming=True))

    log.info(f"{_now()} | --- userbot_listen ЗАПУСК (ЭТАП C: слушаю, НЕ отвечаю) ---")
    # start() поднимет существующую сессию turbobaby_session — код подтверждения не спросит.
    await client.start()
    me = await client.get_me()
    log.info(
        f"{_now()} | вошёл как @{me.username} (id={me.id}). "
        f"Слушаю входящие ЛИЧНЫЕ сообщения. Исходящих — ноль."
    )

    try:
        # Процесс ЖИВЁТ постоянно — это и есть отличие от разведчиков.
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
        log.info(f"{_now()} | --- userbot_listen ОСТАНОВЛЕН ---")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info(f"{_now()} | Ctrl+C — останавливаюсь.")
