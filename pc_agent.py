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
import time
import asyncio
import logging
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# --- пути (всё относительно этого файла = D:\turbobaby-bot) ---
REPO_DIR = Path(__file__).resolve().parent
VENV_PY = REPO_DIR / "venv" / "Scripts" / "python.exe"
LISTEN_SCRIPT = REPO_DIR / "userbot_listen.py"
USERBOT_LOG = REPO_DIR / "userbot.log"
AGENT_LOG = REPO_DIR / "pc_agent.log"
AGENT_LOCK = REPO_DIR / "pc_agent.lock"  # singleton-гард: не запускать ДВА агента сразу

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
        # 1) ВСЕГДА живой CIM-поиск ПЕРЕД запуском (не полагаемся на self.proc).
        #    Любой найденный userbot_listen.py (агента/ручной/автозапуск) → второй не поднимаем.
        pids = _find_userbot_pids()
        if pids:
            return (
                "userbot уже работает (PID " + ", ".join(map(str, pids))
                + "), второй не поднимаю."
            )
        if not VENV_PY.exists():
            return f"не нашёл python venv: {VENV_PY}"
        # ВАЖНО: userbot_listen.py запускаем ТОЛЬКО через venv-python (VENV_PY),
        # НИКОГДА не системным и НИКОГДА не сам себя (pc_agent.py).
        self.proc = subprocess.Popen([str(VENV_PY), str(LISTEN_SCRIPT)], cwd=str(REPO_DIR))
        alog.info(f"userbot запущен агентом, PID {self.proc.pid}")

        # 2) Подстраховка от гонки: подождём и перепроверим. Если экземпляров >1 —
        #    оставляем один, лишние убиваем. (singleton-гард в userbot_listen.py
        #    обычно сам отсеет дубль, это второй рубеж.)
        time.sleep(2.5)
        pids = _find_userbot_pids()
        if len(pids) > 1:
            keep = self.proc.pid if self.proc.pid in pids else pids[0]
            killed = [pid for pid in pids if pid != keep and _taskkill(pid)]
            alog.warning(f"обнаружен дубль userbot {pids}, оставил PID {keep}, убил {killed}")
            return (
                f"обнаружил дубль, оставил PID {keep}"
                + (f" (убил лишние: {', '.join(map(str, killed))})" if killed else "")
                + "."
            )
        if not pids:
            return "запустил, но процесс не виден — проверь userbot.log (возможно, сразу вышел)."
        return f"userbot запущен (PID {pids[0]})."

    def _wait_until_clear(self, timeout=20):
        """Дождаться, пока НИ ОДНОГО userbot_listen не останется (добивая по пути). True — чисто."""
        start_t = time.monotonic()
        while time.monotonic() - start_t < timeout:
            pids = _find_userbot_pids()
            if not pids:
                return True
            for pid in pids:
                _taskkill(pid)
            time.sleep(0.7)
        return not _find_userbot_pids()

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
        # ДО git pull/start — дождаться, пока старые экземпляры реально умрут.
        if not self._wait_until_clear(timeout=20):
            remaining = _find_userbot_pids()
            return (
                f"стоп: {stop_msg}\n\n"
                f"НЕ смог остановить старый userbot (PID {', '.join(map(str, remaining))}). "
                "git pull/старт отменён — останови вручную на ПК и повтори «обнови userbot»."
            )
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


# ============================ обработчик ошибок (ФИКС 1) ============================
# Сетевые сбои (NetworkError/TimedOut, в т.ч. httpx.ReadError/ReadTimeout, которые PTB
# заворачивает в NetworkError) НЕ должны ронять процесс агента. Логируем и продолжаем —
# PTB сам переподключит long-polling. Прочие необработанные ошибки — логируем с трейсом.
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        alog.warning(f"сетевая ошибка (не падаю, PTB переподключится): {type(err).__name__}: {err}")
    else:
        alog.error("необработанная ошибка в обработчике", exc_info=err)


# ============================ singleton-гард агента ============================
# Чтобы НИКОГДА не было ДВУХ pc_agent одновременно (как бы второй ни запустился:
# вручную системным python, дважды от Планировщика и т.п.). Два poller'а на одном
# токене → Telegram отдаёт "Conflict: terminated by other getUpdates" + путаница
# в управлении. Lock-файл pc_agent.lock с PID, атомарный O_CREAT|O_EXCL.

def _agent_pid_alive(pid: int) -> bool:
    """Жив ли процесс по PID (Windows, без psutil). НЕ os.kill (он бы убил процесс)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
        )
        return f'"{pid}"' in out.stdout or f",{pid}," in out.stdout
    except Exception:
        return False


def _read_agent_lock_pid() -> int:
    try:
        return int(AGENT_LOCK.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0


def acquire_agent_lock() -> bool:
    """True — лок наш, работаем; False — другой агент уже жив, выходим."""
    for _ in range(3):
        try:
            fd = os.open(str(AGENT_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            old = _read_agent_lock_pid()
            if old == 0:
                time.sleep(0.3)
                old = _read_agent_lock_pid()
            if old and _agent_pid_alive(old):
                alog.warning(f"pc_agent уже запущен (PID {old}) — выхожу, второй экземпляр не поднимаю.")
                return False
            alog.info(f"устаревший pc_agent.lock (PID {old or '?'} мёртв) — забираю лок.")
            try:
                AGENT_LOCK.unlink()
            except FileNotFoundError:
                pass
    alog.warning("не смог получить pc_agent.lock — НЕ стартую (защита от двойного агента).")
    return False


def release_agent_lock() -> None:
    try:
        if _read_agent_lock_pid() == os.getpid():
            AGENT_LOCK.unlink()
    except FileNotFoundError:
        pass
    except Exception as e:
        alog.warning(f"не смог снять pc_agent.lock: {e}")


def main():
    # Singleton-гард — ПЕРВЫМ действием в main, атомарно, ДО всего остального.
    # (Примечание: «второй процесс» с системным python был НЕ от кода, а от venv-
    #  лаунчера — см. fix_venv_launcher.ps1. Гард остаётся как страховка от реального
    #  второго запуска агента.)
    if not acquire_agent_lock():
        return
    try:
        if not AGENT_BOT_TOKEN:
            raise SystemExit(
                "AGENT_BOT_TOKEN пуст. Впиши токен бота (из BotFather) в .env строкой "
                "AGENT_BOT_TOKEN=... и запусти снова."
            )
        # При старте агент ТОЛЬКО слушает Telegram. Ничего сам не запускает.
        # userbot_listen.py поднимается лишь по команде «старт userbot» (через VENV_PY).
        alog.info("pc_agent ЗАПУСК — слушаю HQ/тему 205, команды только от разрешённого id.")

        # ФИКС 2: авто-реадопшн userbot. Дефолт — userbot жив всегда.
        # При старте агента (в т.ч. после краша и перезапуска Планировщиком) проверяем,
        # живёт ли userbot (CIM-поиск процесса userbot_listen.py = источник правды).
        # Не жив → поднимаем через VENV_PY (UB.start идемпотентен, дубль не создаст).
        # Жив → не трогаем. Команды стоп/старт из темы 205 работают как прежде —
        # это разовая проверка ТОЛЬКО на старте агента.
        alive, pids, _ = UB.status()
        if alive:
            alog.info(f"userbot уже жив (PID {', '.join(map(str, pids))}) — не трогаю.")
        else:
            alog.info("userbot не запущен при старте агента — поднимаю (авто-реадопшн).")
            alog.info(UB.start())

        app = ApplicationBuilder().token(AGENT_BOT_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.UpdateType.EDITED, on_message))
        app.add_error_handler(on_error)  # ФИКС 1: сетевые ошибки не роняют агента
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        release_agent_lock()


if __name__ == "__main__":
    main()
