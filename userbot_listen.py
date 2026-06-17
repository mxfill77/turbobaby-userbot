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
import time
import asyncio
import logging
import subprocess
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

# Singleton-гард: lock-файл с PID живого экземпляра. Главная страховка от ДВУХ
# клиентов на одной session turbobaby_session (двойной клиент = риск бана).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(BASE_DIR, "userbot.lock")

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


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с данным PID (Windows, без psutil).

    НЕ используем os.kill(pid, 0): на Windows это ВЫЗЫВАЕТ TerminateProcess —
    то есть убило бы процесс. Спрашиваем tasklist (фиксированный запрос).
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
        )
        return f'"{pid}"' in out.stdout or f",{pid}," in out.stdout
    except Exception:
        # Не смогли проверить — считаем мёртвым, чтобы не заклинить legit-старт.
        return False


def _read_lock_pid() -> int:
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def acquire_lock() -> bool:
    """Гарантия одного экземпляра. True — лок наш, работаем; False — уже кто-то живой.

    Создаём lock атомарно (O_CREAT|O_EXCL). Если файл уже есть — проверяем, жив ли
    владелец по PID: жив → выходим (второй экземпляр не поднимаем), мёртв → снимаем
    устаревший лок и пробуем снова.
    """
    for _ in range(3):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            old = _read_lock_pid()
            if old == 0:
                # Владелец, возможно, ещё дописывает PID — подождём и перечитаем.
                time.sleep(0.3)
                old = _read_lock_pid()
            if old and _pid_alive(old):
                log.info(
                    f"{_now()} | userbot уже запущен (PID {old}), выхожу — "
                    f"второй экземпляр на session не поднимаю."
                )
                return False
            log.info(f"{_now()} | устаревший userbot.lock (PID {old or '?'} мёртв) — забираю лок.")
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass
    log.warning(f"{_now()} | не смог получить lock — на всякий случай НЕ стартую (защита от дубля).")
    return False


def release_lock() -> None:
    """Снять lock — только если он наш (наш PID внутри)."""
    try:
        if _read_lock_pid() == os.getpid():
            os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"{_now()} | не смог снять lock-файл: {e}")


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
    # Singleton-гард ДО подключения: если живой экземпляр уже есть — выходим,
    # чтобы не было двух клиентов на одной session.
    if not acquire_lock():
        return

    # Клиент создаём внутри main (под asyncio.run) — как в fetch_client_chats.py,
    # чтобы Telethon корректно привязался к event loop.
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.add_event_handler(on_incoming, events.NewMessage(incoming=True))

    log.info(f"{_now()} | --- userbot_listen ЗАПУСК (ЭТАП C: слушаю, НЕ отвечаю) ---")
    try:
        # start() поднимет существующую сессию turbobaby_session — код подтверждения не спросит.
        await client.start()
        me = await client.get_me()
        log.info(
            f"{_now()} | вошёл как @{me.username} (id={me.id}). "
            f"Слушаю входящие ЛИЧНЫЕ сообщения. Исходящих — ноль."
        )
        # Процесс ЖИВЁТ постоянно — это и есть отличие от разведчиков.
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
        release_lock()  # снимаем lock при любом выходе
        log.info(f"{_now()} | --- userbot_listen ОСТАНОВЛЕН ---")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info(f"{_now()} | Ctrl+C — останавливаюсь.")
