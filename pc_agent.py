"""
pc_agent.py — удалённое управление userbot'ом с телефона через Telegram.

Отдельный бот (свой токен AGENT_BOT_TOKEN, НЕ session userbot). Управляет
ПРОЦЕССОМ userbot_listen.py на этой же машине (ПК). Сам ничего в Telegram от
имени userbot не пишет и session 'turbobaby_session' НЕ трогает.

Точка управления: userbot_listen.py (слушатель ЭТАПА C), НЕ bot.py.

Доступ (проверяется ДО любой команды):
  • только chat_id HQ_CHAT_ID, только тема message_thread_id = HQ_THREAD_ID (205);
  • команды только от ALLOWED_USER_ID (@SamHold). Чужой → игнор + лог «отклонено».

Белый список команд (фиксированный, НЕ произвольный shell):
  обнови userbot | update   → стоп → git pull → старт; ответ: вывод git pull + статус рестарта
  статус         | status   → жив ли userbot (PID) + последние ~15 строк userbot.log
  стоп userbot   | stop     → остановить процесс userbot_listen
  старт userbot  | start    → запустить userbot_listen.py
  сводка         | summary  → выжимка userbot.log (сколько сообщений / от скольких людей /
                              последние ~5 строк); если лог большой — выжимка + ПОЛНЫЙ лог файлом
  иное → подсказка со списком команд

Большие выводы (>3500 символов) — файлом (send_document), в тексте короткая выжимка.

Запуск (ТОЛЬКО на ПК):
    cd D:\\turbobaby-bot
    venv\\Scripts\\activate
    python pc_agent.py
Зависимости: python-telegram-bot (стоит в venv). psutil НЕ нужен — поиск процесса
userbot_listen.py делается фиксированным CIM-запросом через PowerShell.

Безопасность: allowlist жёсткий; .env/session не трогаем и не логируем; действия → pc_agent.log.
"""

import os
import io
import asyncio
import logging
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# --- пути (всё относительно этого файла = D:\turbobaby-bot) ---
REPO_DIR = Path(__file__).resolve().parent
VENV_PY = REPO_DIR / "venv" / "Scripts" / "python.exe"
LISTEN_SCRIPT = REPO_DIR / "userbot_listen.py"
USERBOT_LOG = REPO_DIR / "userbot.log"
AGENT_LOG = REPO_DIR / "pc_agent.log"

# --- доступ (жёстко зашит) ---
HQ_CHAT_ID = -1003853365891
HQ_THREAD_ID = 205
ALLOWED_USER_ID = 504608015

MAX_TG = 3500  # длиннее — отправляем файлом

load_dotenv()
AGENT_BOT_TOKEN = os.getenv("AGENT_BOT_TOKEN", "").strip()

# --- логирование агента: файл (utf-8) + stdout ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(AGENT_LOG, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
# Глушим болтливый сетевой лог PTB, оставляем свои сообщения.
logging.getLogger("httpx").setLevel(logging.WARNING)
alog = logging.getLogger("pc_agent")


# ======================== управление процессом userbot ========================

def _find_userbot_pids():
    """PID'ы python-процессов, исполняющих userbot_listen.py (любой запуск, в т.ч. ручной).

    Фиксированный CIM-запрос (НЕ пользовательский ввод) — никакого произвольного shell.
    """
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| Where-Object { $_.CommandLine -like '*userbot_listen.py*' } "
        "| Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        )
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception as e:
        alog.warning(f"не смог проверить процессы userbot: {e}")
        return []


def _taskkill(pid):
    """Жёстко снять внешний (ручной) процесс по PID. PID — наш int из _find_userbot_pids."""
    try:
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True, text=True, timeout=15,
        )
        return r.returncode == 0
    except Exception as e:
        alog.warning(f"taskkill {pid} не сработал: {e}")
        return False


class UserbotProcess:
    """Управляет жизненным циклом userbot_listen.py. Блокирующие методы зовём через to_thread."""

    def __init__(self):
        self.proc = None  # Popen, запущенный самим агентом

    def _agent_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def status(self):
        pids = _find_userbot_pids()
        return (len(pids) > 0), pids, self._agent_alive()

    def start(self):
        if self._agent_alive():
            return f"userbot уже работает (PID {self.proc.pid}, запущен агентом)."
        pids = _find_userbot_pids()
        if pids:
            return (
                "userbot уже работает (запущен вручную, PID "
                + ", ".join(map(str, pids))
                + "). Останови его прежде чем управлять через агента "
                "(команда «стоп userbot» или закрой окно на ПК)."
            )
        if not VENV_PY.exists():
            return f"не нашёл python venv: {VENV_PY}"
        self.proc = subprocess.Popen([str(VENV_PY), str(LISTEN_SCRIPT)], cwd=str(REPO_DIR))
        alog.info(f"userbot запущен агентом, PID {self.proc.pid}")
        return f"userbot запущен (PID {self.proc.pid})."

    def stop(self):
        stopped = []
        # 1) аккуратно завершаем то, что агент запускал сам
        if self._agent_alive():
            pid = self.proc.pid
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            stopped.append(pid)
            self.proc = None
        # 2) добиваем внешние (ручные) экземпляры, если остались
        for pid in _find_userbot_pids():
            if _taskkill(pid):
                stopped.append(pid)
        if stopped:
            alog.info(f"userbot остановлен, PID {stopped}")
            return "userbot остановлен (PID " + ", ".join(map(str, stopped)) + ")."
        return "userbot не запущен — останавливать нечего."

    def update(self):
        stop_msg = self.stop()
        try:
            pull = subprocess.run(
                ["git", "-C", str(REPO_DIR), "pull"],
                capture_output=True, text=True, timeout=120,
            )
            pull_out = (pull.stdout + pull.stderr).strip() or "(git pull: пустой вывод)"
        except Exception as e:
            pull_out = f"git pull упал: {e}"
        start_msg = self.start()
        return f"git pull:\n{pull_out}\n\nстоп: {stop_msg}\nстарт: {start_msg}"


UB = UserbotProcess()


# ============================ чтение userbot.log =============================

def _read_log_lines():
    if not USERBOT_LOG.exists():
        return []
    return USERBOT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()


def _is_message_line(parts):
    # строка сообщения: "<ISO> | <@user|idNNN> | <имя> | <текст>" (>=4 поля),
    # служебные строки (--- ЗАПУСК ---, вошёл как ...) имеют меньше полей.
    return len(parts) >= 4 and (parts[1].startswith("@") or parts[1].startswith("id"))


def summarize_log():
    lines = _read_log_lines()
    msgs, senders, last_msgs = 0, set(), []
    for line in lines:
        parts = line.split(" | ")
        if _is_message_line(parts):
            msgs += 1
            senders.add(parts[1])
            last_msgs.append(line)
    last5 = last_msgs[-5:]
    digest = (
        "Сводка userbot.log:\n"
        f"• сообщений: {msgs}\n"
        f"• от разных людей: {len(senders)}\n"
        f"• последние {len(last5)}:\n" + ("\n".join(last5) if last5 else "(пусто)")
    )
    full = "\n".join(lines)
    return digest, full


def status_text():
    alive, pids, managed = UB.status()
    head = (
        f"userbot: {'РАБОТАЕТ' if alive else 'не запущен'}"
        + (f" (PID {', '.join(map(str, pids))}" if pids else "")
        + (", агентом" if managed else (", вручную" if pids else ""))
        + (")" if pids else "")
    )
    last15 = _read_log_lines()[-15:]
    tail = "\n".join(last15) if last15 else "(userbot.log пуст или отсутствует)"
    return f"{head}\n\nПоследние строки userbot.log:\n{tail}"


# ============================ отправка в Telegram ============================

async def _send(context, chat_id, text):
    """Текст: если длиннее MAX_TG — короткая выжимка + полный текст файлом."""
    if len(text) <= MAX_TG:
        await context.bot.send_message(chat_id, text, message_thread_id=HQ_THREAD_ID)
        return
    head = text[:1500]
    await context.bot.send_message(
        chat_id,
        head + "\n\n…вывод большой — полностью в файле ниже.",
        message_thread_id=HQ_THREAD_ID,
    )
    bio = io.BytesIO(text.encode("utf-8"))
    bio.name = "output.txt"
    await context.bot.send_document(chat_id, document=bio, message_thread_id=HQ_THREAD_ID)


async def _send_log_file(context, chat_id):
    """Отправить ПОЛНЫЙ userbot.log как файл."""
    if not USERBOT_LOG.exists():
        return
    data = USERBOT_LOG.read_bytes()
    bio = io.BytesIO(data)
    bio.name = "userbot.log"
    await context.bot.send_document(chat_id, document=bio, message_thread_id=HQ_THREAD_ID)


# ================================ обработчик ================================

HELP = (
    "не понял. Доступно: обнови userbot / статус / стоп userbot / старт userbot / сводка"
)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if msg is None or chat is None:
        return

    # --- гейт доступа ДО любой команды ---
    # 1) только наш чат и только тема 205 — прочее молча игнорим
    if chat.id != HQ_CHAT_ID or msg.message_thread_id != HQ_THREAD_ID:
        return
    # 2) только разрешённый пользователь
    uid = user.id if user else None
    if uid != ALLOWED_USER_ID:
        alog.warning(f"отклонено: id {uid}")
        return

    text = (msg.text or "").strip().lstrip("/").lower()
    chat_id = chat.id

    try:
        if text in ("обнови userbot", "update"):
            alog.info("команда: update")
            result = await asyncio.to_thread(UB.update)
            await _send(context, chat_id, result)

        elif text in ("статус", "status"):
            alog.info("команда: status")
            await _send(context, chat_id, await asyncio.to_thread(status_text))

        elif text in ("стоп userbot", "stop"):
            alog.info("команда: stop")
            await _send(context, chat_id, await asyncio.to_thread(UB.stop))

        elif text in ("старт userbot", "start"):
            alog.info("команда: start")
            await _send(context, chat_id, await asyncio.to_thread(UB.start))

        elif text in ("сводка", "summary"):
            alog.info("команда: summary")
            digest, full = await asyncio.to_thread(summarize_log)
            await _send(context, chat_id, digest)
            if len(full) > MAX_TG:  # лог большой — шлём полный файлом
                await _send_log_file(context, chat_id)

        else:
            await _send(context, chat_id, HELP)
    except Exception as e:
        alog.exception("ошибка обработки команды")
        await _send(context, chat_id, f"ошибка: {e}")


def main():
    if not AGENT_BOT_TOKEN:
        raise SystemExit(
            "AGENT_BOT_TOKEN пуст. Впиши токен бота (из BotFather) в .env строкой "
            "AGENT_BOT_TOKEN=... и запусти снова."
        )
    alog.info("pc_agent ЗАПУСК — слушаю HQ/тему 205, команды только от разрешённого id.")
    app = ApplicationBuilder().token(AGENT_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.UpdateType.EDITED, on_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
