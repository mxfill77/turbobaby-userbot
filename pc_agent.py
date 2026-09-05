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
import re
import json
import time
import asyncio
import logging
import datetime
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (ApplicationBuilder, MessageHandler, CallbackQueryHandler,
                          filters, ContextTypes)

import io_utf8          # переключатель stdout/stderr в UTF-8 (класс «charmap can't encode 📊»)
import selfupdate_gate  # гейт самообновления (проверка нового кода перед рестартом)
import proc_identity    # ЛИЧНОСТЬ ПРОЦЕССА: номер + имя запуска + момент старта (лок и taskkill)
import deploy_voice     # ГОЛОС подъёма мимо ворот: какой коммит выкачен ребёнку (03.09.2026)
import decision_waits   # человеческая строка на протухшее нажатие + парковка открытых решений

# Скрытый запуск служебных консольных подпроцессов (powershell/tasklist/taskkill/git/боты):
# без флага каждый console-ребёнок создавал новое окно → «мигающие чёрные окна» (инцидент-каскад
# 22.07, тот же класс, что NO_WINDOW в pc_orchestrator). POSIX → 0.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# --- пути (всё относительно этого файла = D:\turbobaby-bot) ---
REPO_DIR = Path(__file__).resolve().parent
VENV_PY = REPO_DIR / "venv" / "Scripts" / "python.exe"
LISTEN_SCRIPT = REPO_DIR / "userbot_listen.py"
MODERBOT_SCRIPT = REPO_DIR / "moderation_bot.py"  # бот-модератор (задача-2)
USERBOT_LOG = REPO_DIR / "userbot.log"
AGENT_LOG = REPO_DIR / "pc_agent.log"
LOGS_DIR = REPO_DIR / "logs"        # ФИКС 1 (#128): сюда пишем stdout/stderr детей (append)
AGENT_LOCK = REPO_DIR / "pc_agent.lock"  # singleton-гард: не запускать ДВА агента сразу
# Надзор контур-вотчдога (пишет демон pc_orchestrator в своём процессе; читаем ФАКТ супервизии,
# а не «кто запустил»). STOP_FLAG — рубильник демона: взведён → надзор намеренно выключен.
CLIENT_WATCH_FILE = REPO_DIR / "pc_orchestrator.client_watch.json"
STOP_FLAG = REPO_DIR / "pc_orchestrator.stop"
WATCH_SNAPSHOT_STALE = int(os.getenv("PC_WATCH_SNAPSHOT_STALE", "900") or "900")  # снимок старше → «демон молчит» (≈3× цикла вотчдога)
TASK_NAME = "pc_agent"  # ФИКС 3: имя задачи в Планировщике Windows — ДОЛЖНО совпадать с реальным

# --- доступ (жёстко зашит) ---
HQ_CHAT_ID = -1003853365891
HQ_THREAD_ID = 205
ALLOWED_USER_ID = 504608015

MAX_TG = 3500  # длиннее — отправляем файлом

load_dotenv()
AGENT_BOT_TOKEN = os.getenv("AGENT_BOT_TOKEN", "").strip()

# --- логирование агента: файл (utf-8) + stdout ---
_AGENT_FMT = "%(asctime)s | %(levelname)s | %(message)s"
try:
    import log_setup                       # ротация + тестовый лог в temp (см. log_setup)
    _fh = log_setup.rotating_handler(AGENT_LOG, fmt=_AGENT_FMT)
except Exception:
    _fh = None
if _fh is None:
    _fh = logging.FileHandler(AGENT_LOG, encoding="utf-8")
    _fh.setFormatter(logging.Formatter(_AGENT_FMT))
logging.basicConfig(
    level=logging.INFO,
    format=_AGENT_FMT,
    handlers=[_fh, logging.StreamHandler()],
)
# Глушим болтливый сетевой лог PTB, оставляем свои сообщения.
logging.getLogger("httpx").setLevel(logging.WARNING)
alog = logging.getLogger("pc_agent")


# ==================== ФИКС 1 (#128): stderr/stdout детей → файл ================
# Раньше детей (userbot_listen.py, moderation_bot.py) спавнили без перенаправления
# stdout/stderr. Под Планировщиком (без консоли) их stderr уходил в никуда → смерть
# ребёнка ДО настройки его собственного logging (ошибка импорта, битый токен, падение
# на старте PTB) не оставляла НИ traceback, НИ строки в *.log — «мгновенная смерть»
# без улик (разбор #128). Теперь stdout+stderr каждого ребёнка идут в
# logs/<имя>_stderr.log (APPEND, не перетираем историю падений). Собственный лог ребёнка
# (userbot.log/moderation_bot.log) остаётся — это дополнительный, «сырой» канал для того,
# что до логгера не дошло.

def _child_log_handle(name):
    """Открыть logs/<name>_stderr.log на APPEND (utf-8) для stdout+stderr ребёнка и вписать
    строку-разделитель со временем старта. Возвращает файловый объект (передаём в Popen).
    Файл переживает выход родителя: ребёнок наследует dup дескриптора."""
    LOGS_DIR.mkdir(exist_ok=True)
    fh = open(LOGS_DIR / f"{name}_stderr.log", "a", encoding="utf-8")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fh.write(f"\n===== {name}: старт {ts} (pc_agent PID {os.getpid()}) =====\n")
    fh.flush()
    return fh


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
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=NO_WINDOW,
        )
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception as e:
        alog.warning(f"не смог проверить процессы userbot: {e}")
        return []


def _taskkill(pid, seen_at):
    """Жёстко снять внешний (ручной) процесс по PID — ТОЛЬКО ПОСЛЕ ОПОЗНАНИЯ.

    `seen_at` обязателен (позиционный, без значения по умолчанию) СОЗНАТЕЛЬНО: это момент, когда
    мы этот номер ВИДЕЛИ в CIM-поиске по имени скрипта. Между поиском и ударом лежит окно —
    секунды в `stop()`, до 20 с в цикле добивания `_wait_until_clear`. Умри владелец в этом окне
    — Windows отдаёт номер следующему, и удар достаётся ЧУЖОМУ живому процессу. Тот же класс,
    что и «лок с чужим номером», только цена выше: там мы себя не пускаем, здесь мы бьём соседа.

    Опознаём общим правилом полосы (`proc_identity.kill_ok`): образ обязан быть python, а
    рождение — не позже момента наблюдения. Не опознали — НЕ БЬЁМ и говорим об этом вслух."""
    ok, why = proc_identity.kill_ok(pid, seen_at)
    if not ok:
        alog.warning(f"taskkill {pid} ОТМЕНЁН — процесс не опознан как наш: {why}")
        return False
    try:
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=NO_WINDOW,
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
        # ФИКС 1 (#128): stdout+stderr ребёнка → logs/userbot_stderr.log (смерть оставит traceback).
        logf = _child_log_handle("userbot")
        self.proc = subprocess.Popen([str(VENV_PY), str(LISTEN_SCRIPT)], cwd=str(REPO_DIR),
                                     stdout=logf, stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW)
        alog.info(f"userbot запущен агентом, PID {self.proc.pid}")

        # 2) Подстраховка от гонки: подождём и перепроверим. Если экземпляров >1 —
        #    оставляем один, лишние убиваем. (singleton-гард в userbot_listen.py
        #    обычно сам отсеет дубль, это второй рубеж.)
        time.sleep(2.5)
        seen_at = time.time()               # момент НАБЛЮДЕНИЯ номеров — им и опознаём перед ударом
        pids = _find_userbot_pids()
        if len(pids) > 1:
            keep = self.proc.pid if self.proc.pid in pids else pids[0]
            killed = [pid for pid in pids if pid != keep and _taskkill(pid, seen_at)]
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
            seen_at = time.time()           # свежий момент наблюдения НА КАЖДЫЙ виток добивания:
            pids = _find_userbot_pids()     # цикл живёт до 20 с, и старая метка тут врала бы
            if not pids:
                return True
            for pid in pids:
                _taskkill(pid, seen_at)
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
        seen_at = time.time()
        for pid in _find_userbot_pids():
            if _taskkill(pid, seen_at):
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
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=120, creationflags=NO_WINDOW,
            )
            pull_out = (pull.stdout + pull.stderr).strip() or "(git pull: пустой вывод)"
        except Exception as e:
            pull_out = f"git pull упал: {e}"
        start_msg = self.start()
        return f"git pull:\n{pull_out}\n\nстоп: {stop_msg}\nстарт: {start_msg}"


UB = UserbotProcess()


# ===================== управление процессом moderation_bot ====================

def _find_moderbot_pids():
    """PID'ы python-процессов, исполняющих moderation_bot.py. Фиксированный CIM-запрос."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| Where-Object { $_.CommandLine -like '*moderation_bot.py*' } "
        "| Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=NO_WINDOW,
        )
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception as e:
        alog.warning(f"не смог проверить процессы moderation_bot: {e}")
        return []


class ModerbotProcess:
    """Жизненный цикл moderation_bot.py (задача-2). По образцу UserbotProcess.
    НЕ поднимается без MODERBOT_TOKEN — тогда работает деградация (reply-режим userbot)."""

    def __init__(self):
        self.proc = None

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def status(self):
        pids = _find_moderbot_pids()
        token = bool(os.getenv("MODERBOT_TOKEN", "").strip())
        state = "работает" if pids else ("не запущен" if token else "не запущен (нет MODERBOT_TOKEN → reply-режим)")
        return f"moderation_bot: {state}" + (f" (PID {', '.join(map(str, pids))})" if pids else "")

    def start(self):
        if not os.getenv("MODERBOT_TOKEN", "").strip():
            return "не запускаю: нет MODERBOT_TOKEN в .env (работает деградация — reply-режим userbot)."
        pids = _find_moderbot_pids()
        if pids:
            return f"moderation_bot уже работает (PID {', '.join(map(str, pids))}), второй не поднимаю."
        if not VENV_PY.exists():
            return f"не нашёл python venv: {VENV_PY}"
        # ФИКС 1 (#128): stdout+stderr ребёнка → logs/moderbot_stderr.log (смерть оставит traceback).
        logf = _child_log_handle("moderbot")
        self.proc = subprocess.Popen([str(VENV_PY), str(MODERBOT_SCRIPT)], cwd=str(REPO_DIR),
                                     stdout=logf, stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW)
        alog.info(f"moderation_bot запущен агентом, PID {self.proc.pid}")
        time.sleep(2.0)
        pids = _find_moderbot_pids()
        if not pids:
            return "запустил, но процесс не виден — проверь moderation_bot.log (возможно, сразу вышел)."
        return f"moderation_bot запущен (PID {pids[0]})."

    def stop(self):
        stopped = []
        if self._alive():
            pid = self.proc.pid
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            stopped.append(pid)
            self.proc = None
        seen_at = time.time()
        for pid in _find_moderbot_pids():
            if _taskkill(pid, seen_at):
                stopped.append(pid)
        if stopped:
            alog.info(f"moderation_bot остановлен, PID {stopped}")
            return "moderation_bot остановлен (PID " + ", ".join(map(str, stopped)) + ")."
        return "moderation_bot не запущен — останавливать нечего."


MB = ModerbotProcess()


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


def _read_watch_snapshot(path=None):
    """Снимок надзора, записанный демоном (см. pc_orchestrator._persist_client_watch). → dict|None.
    Любая ошибка (нет файла / битый JSON) → None (демон молчит), НЕ падаем."""
    path = CLIENT_WATCH_FILE if path is None else path
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _supervision_label(name, snapshot, now, stop_present, stale=WATCH_SNAPSHOT_STALE):
    """Человекочитаемое СОСТОЯНИЕ НАДЗОРА за <name> (вместо «кто запустил»). Чистая/тестируемая.
      • рубильник взведён            → надзор намеренно выключен;
      • снимка нет / протух          → «демон молчит» (надзор недоказуем, не врём «под вотчдогом»);
      • halted                       → надзор сдался после N смертей подряд — нужен разбор;
      • недавно поднимали (cooldown) → под вотчдогом, ждём окно до следующего подъёма;
      • иначе                        → под вотчдогом (жив, надзор активен)."""
    if stop_present:
        return "выключен (рубильник pc_orchestrator.stop)"
    if not snapshot:
        return "? демон молчит"
    ts = snapshot.get("ts")
    if not isinstance(ts, (int, float)) or (now - ts) > stale:
        return "? демон молчит (снимок протух)"
    ent = (snapshot.get("children") or {}).get(name)
    if not isinstance(ent, dict):
        return "? нет в снимке демона"
    if ent.get("halted"):
        return f"ОСТАНОВЛЕН — умер {ent.get('deaths', '?')} раз подряд, нужен разбор"
    deaths = int(ent.get("deaths") or 0)
    last_raise = float(ent.get("last_raise") or 0.0)
    cooldown = float(snapshot.get("cooldown") or 0.0)
    if deaths > 0 and last_raise > 0 and (now - last_raise) < cooldown:
        left = int(cooldown - (now - last_raise))
        return f"под вотчдогом · cooldown (~{left}с до подъёма)"
    return "под вотчдогом"


def status_text():
    alive, pids, _managed = UB.status()
    sup = _supervision_label("userbot", _read_watch_snapshot(), time.time(), STOP_FLAG.exists())
    head = (
        f"userbot: {'РАБОТАЕТ' if alive else 'не запущен'}"
        + (f" (PID {', '.join(map(str, pids))})" if pids else "")
        + f" · надзор: {sup}"
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


# ===================== само-рестарт агента (ФИКС 3) =====================
# Цель: применять свежий код агента с телефона (команда «обновись» из темы 205), без
# PowerShell на ПК. Самое тонкое — перезапустить СЕБЯ на Windows так, чтобы (1) НИКОГДА
# не было двух агентов одновременно (конфликт getUpdates на одном токене), (2) новый
# экземпляр ГАРАНТИРОВАННО поднялся (не «оба умерли»).
# Механизм: после git pull текущий агент спавнит DETACHED-помощника (переживает наш
# выход). Помощник ЖДЁТ смерти нашего PID и ТОЛЬКО ПОСЛЕ этого просит Планировщик поднять
# задачу (schtasks /Run). Перекрытия нет: новый стартует строго после смерти старого.
# Лок снимаем сами перед выходом; даже забудь мы — новый агент заберёт устаревший лок
# (singleton-гард НЕ ослабляем). userbot не трогаем: если он отвалится со старым агентом,
# его поднимет авто-реадопшн (ФИКС 2) при старте нового.

def _git_pull():
    """git pull --ff-only в REPO_DIR → (ok: bool, output: str).
    --ff-only: без merge-коммитов; при расхождении/конфликте дерево НЕ ломаем, вернём ошибку."""
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO_DIR), "pull", "--ff-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, creationflags=NO_WINDOW,
        )
        out = (r.stdout + r.stderr).strip() or "(пустой вывод)"
        return (r.returncode == 0), out
    except Exception as e:
        return False, f"git pull упал: {e}"


def _spawn_restart_helper():
    """DETACHED-помощник: ждёт смерти ТЕКУЩЕГО агента (наш PID), затем просит Планировщик
    поднять задачу заново. creationflags → помощник переживает выход родителя."""
    my_pid = os.getpid()
    ps = (
        f"$old={my_pid}; "
        "while (Get-Process -Id $old -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 300 }; "
        "Start-Sleep -Milliseconds 800; "
        f"schtasks /Run /TN {TASK_NAME}"
    )
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
        cwd=str(REPO_DIR),
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
    )
    alog.info(f"помощник рестарта запущен (ждёт смерти PID {my_pid} → schtasks /Run /TN {TASK_NAME}).")


async def self_restart(context, chat_id):
    """Команда «обновись»: git pull → (если ок) спавн помощника → снять лок → жёсткий выход.
    git pull упал → НЕ перезапускаемся, остаёмся на текущем коде."""
    await _send(context, chat_id, "🔄 принято: git pull, затем перезапуск себя.")
    ok, out = await asyncio.to_thread(_git_pull)
    if not ok:
        await _send(context, chat_id, f"⛔ git pull не удался — НЕ перезапускаюсь, остаюсь на текущем коде:\n{out}")
        alog.warning("self-restart отменён: git pull failed")
        return
    # ГЕЙТ КЛАССА: НИКОГДА не перезапускаться в непроверенный код — иначе рискуем убить
    # канал управления (некому будет принять команду отката). py_compile + import-smoke.
    ok_code, gate_msg = await asyncio.to_thread(
        selfupdate_gate.code_gate, VENV_PY, REPO_DIR,
        ["pc_agent.py", "moderation_bot.py", "userbot_listen.py", "suggest.py"], "pc_agent",
    )
    if not ok_code:
        await _send(
            context, chat_id,
            "⛔ обновление отклонено: новый код НЕ прошёл проверку — остаюсь на текущем "
            f"(рабочем) коде, канал управления жив.\n{gate_msg}",
        )
        alog.warning(f"self-restart отменён гейтом: {gate_msg[:300]}")
        return
    await _send(
        context, chat_id,
        f"✅ git pull:\n{out}\n\n✅ гейт кода пройден (py_compile + import-smoke). Перезапускаюсь. "
        "Новый экземпляр поднимется через Планировщик после моей смерти. Вернусь через ~10–20с — проверь «статус»."
    )
    alog.info("self-restart: pull OK + гейт пройден → спавню помощника, снимаю лок, выхожу.")
    _spawn_restart_helper()
    release_agent_lock()
    os._exit(0)  # жёсткий выход: гарантирует смерть PID; помощник стартует новый ТОЛЬКО после


# ================================ обработчик ================================

# Реальные команды темы 205 — ЕДИНЫЙ источник для справки-подсказки. Перечень КАНОНИЧЕСКИЙ:
# ровно то, что понимает on_message ниже (см. цепочку elif). Добавил команду в роутинг — добавь строку.
KNOWN_COMMANDS = (
    "обнови userbot (update) — стоп → git pull → старт",
    "статус (status) — жив ли userbot + хвост лога",
    "стоп userbot (stop)",
    "старт userbot (start)",
    "статус модербот (moderbot status)",
    "старт модербот (moderbot start)",
    "стоп модербот (moderbot stop)",
    "сводка (summary) — дайджест лога",
    "обновись (restart) — само-рестарт агента",
)


def unknown_command_reply(raw_text):
    """(ФИКС #171/3) Ответ на НЕИЗВЕСТНУЮ команду в теме 205: эхо непонятого + перечень РЕАЛЬНЫХ
    команд — вместо тишины/глухого «не понял» (инцидент со «статусом»). raw_text — оригинал (обрезаем)."""
    echo = " ".join((raw_text or "").split())
    if len(echo) > 80:
        echo = echo[:80] + "…"
    head = f"не знаю «{echo}»." if echo else "пустая команда."
    cmds = "\n".join(f"  • {c}" for c in KNOWN_COMMANDS)
    return f"{head} умею:\n{cmds}"


# Совместимость: прежнее имя HELP всё ещё зовётся из else-ветки (теперь — динамический ответ).
HELP = unknown_command_reply("")


# ===================== кнопки управления цепями дирижёра =====================
# Карточки цепи (dispatch_notify --card) несут кнопки [⏹ Стоп цепи][📊 Статус цепи] с
# callback_data «chain:stop:<pid>» / «chain:status:<pid>». Тот же бот-токен (AGENT_BOT_TOKEN),
# что шлёт карточки, ловит и их callback'и — обрабатываем ЗДЕСЬ. Действие исполняет демон
# pc_orchestrator (субпроцессом, свой канал к Bridge), агент лишь роутит + гейтит владельца.

CHAIN_CB_RE = re.compile(r"^chain:(stop|status):(\d+)$")

# ── КНОПКИ ЗАЯВОК ВНЕШНИХ КАНАЛОВ (ступень G, 02.09.2026) ────────────────────────────────
# Заявка ступени B — ряд очереди needs_approval, который ЖДЁТ РЕШЕНИЯ ЧЕЛОВЕКА. До 02.09 у
# решения не было двери: Мост на set_needs_approval пишет строку таблицы и в Telegram не шлёт
# НИЧЕГО, а полоса ПК не отправляла по трём накопленным заявкам ни одного сообщения (замер:
# dispatch_notify.log, 9732 строки, совпадений ноль). Теперь заявка приезжает сообщением с двумя
# кнопками, и ловятся они ЗДЕСЬ — тем же токеном, тем же обработчиком, что кнопки цепей.
#
# ЧТО ДЕЛАЕТ ТАП, И ЧЕГО ОН НЕ ДЕЛАЕТ НИ ПРИ КАКОМ ОТВЕТЕ: «да» помечает заявку принятой к
# сведению, «нет» закрывает её — и ОБА исхода задач не рождают. Агент здесь только роутит и
# гейтит владельца; действие исполняет zayavki_pc_run своим каналом к Мосту.
ZAYAVKA_CB_RE = re.compile(r"^zayavka:(yes|no):(\d+)$")

# ── КНОПКИ ВОРОТ КЛИЕНТСКОГО КОНТУРА (05.09.2026) ───────────────────────────────────────────
# Карточка ворот показывается в теме-инбоксе, а СЛОВО ответа («выкати» / «не выкатывай») читается
# демоном только из ТЕКСТА ЗАДАЧИ ОЧЕРЕДИ — то есть НЕ там, где карточка показана. Замер 05.09:
# devbot темы-инбокса на голое слово отвечает «это тема-инбокс подтверждений: „да N“ / „нет N“ или
# кнопки под карточкой», а слова «выкати» не знает вовсе. Владелец сказал прямо: «я в этой группе
# вообще ничего нажать не могу» — и был прав, нажимать было не на что.
#
# ЧТО ДЕЛАЕТ ТАП: подставляет ТО ЖЕ слово в ТОТ ЖЕ разбор рычага (pc_orchestrator --gate-word).
# Новых слов не заводит, ворот мимо оснований не открывает, fail-closed не трогает. Хвост
# callback_data — КОММИТ (hex или иная короткая метка карточки), он идёт в лог и в тост: решение
# по-прежнему принимается на текущий HEAD, ровно как при ответе словом.
GATE_CB_RE = re.compile(r"^gate:(yes|no):([0-9A-Za-z._-]{1,48})$")
GATE_WORDS = {"yes": "выкати", "no": "не выкатывай"}
# Куда писать словом, если кнопка не дошла. Держим дословно рядом с кнопкой: подсказка обязана
# уехать владельцу ровно в том месте, где его тап не сработал.
GATE_ANSWER_AT = ("тема «PC-дев», сообщением «задача: выкати» или «задача: не выкатывай» "
                  "(в теме-инбоксе голое слово не сработает — только «да N»/«нет N» и кнопки)")


def _gate_cb_parse(data):
    """callback_data кнопки ворот → (action, метка коммита) | None (не наш callback)."""
    m = GATE_CB_RE.match(str(data or ""))
    return (m.group(1), m.group(2)) if m else None


def _gate_cli(action, commit):
    """Ответ воротам через демон-CLI (--gate-word «слово»): тот же разбор, что у задачи очереди.

    Субпроцессом и той же механикой, что `_chain_cli`/`_zayavka_cli`, по той же причине: агент не
    держит ни клиента Моста, ни его секретов — он роутит тап и показывает результат. Слово берём
    из закрытой таблицы GATE_WORDS: из кнопки в командную строку не уезжает НИЧЕГО, пришедшего
    из Telegram, — только два литерала, которые владелец мог бы написать и сам.
    """
    word = GATE_WORDS.get(action)
    if not word:
        return f"ворота {commit}: не понял кнопку ({action})."
    if not VENV_PY.exists():
        return f"ворота {commit}: не нашёл python venv ({VENV_PY})."
    try:
        r = subprocess.run(
            [str(VENV_PY), str(REPO_DIR / "pc_orchestrator.py"), "--gate-word", word],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, cwd=str(REPO_DIR),
            creationflags=NO_WINDOW,
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return out or (f"ворота {commit}: пустой ответ демона на «{word}». "
                       f"Запасной путь — {GATE_ANSWER_AT}.")
    except Exception as e:
        return (f"ворота {commit}: ответ «{word}» НЕ принят ({type(e).__name__}: {e}). "
                f"Запасной путь — {GATE_ANSWER_AT}.")


def _chain_cb_parse(data):
    """callback_data → (action, pid) | None (не наш callback)."""
    m = CHAIN_CB_RE.match(str(data or ""))
    return (m.group(1), m.group(2)) if m else None


def _zayavka_cb_parse(data):
    """callback_data кнопки заявки → (action, id) | None (не наш callback)."""
    m = ZAYAVKA_CB_RE.match(str(data or ""))
    return (m.group(1), m.group(2)) if m else None


def _zayavka_cli(action, tid):
    """Ответ на заявку через zayavki_pc_run (свой канал к Мосту) → текст ответа.

    Субпроцессом и той же механикой, что `_chain_cli`, по той же причине: агент не
    держит ни клиента Моста, ни его секретов — он роутит тап и показывает результат.
    """
    if not VENV_PY.exists():
        return f"заявка #{tid}: не нашёл python venv ({VENV_PY})."
    try:
        r = subprocess.run(
            [str(VENV_PY), str(REPO_DIR / "zayavki_pc_run.py"), "--answer", action, "--id", str(tid)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, cwd=str(REPO_DIR),
            creationflags=NO_WINDOW,
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return out or f"заявка #{tid}: пустой ответ ступени G ({action})."
    except Exception as e:
        return f"заявка #{tid}: ошибка ответа ({type(e).__name__}: {e})."


def _chain_cb_authorized(uid):
    """Кнопки цепи слушаются ТОЛЬКО от владельца (ALLOWED_USER_ID) — как и команды темы 205."""
    return uid == ALLOWED_USER_ID


def _chain_cli(action, pid):
    """Действие над цепью через демон-CLI (свой канал к Bridge): pc_orchestrator --chain-<action>
    <pid> → текст ответа. Демон читает/пишет очередь сам; агент только показывает результат."""
    if not VENV_PY.exists():
        return f"цепь #{pid}: не нашёл python venv ({VENV_PY})."
    try:
        r = subprocess.run(
            [str(VENV_PY), str(REPO_DIR / "pc_orchestrator.py"), f"--chain-{action}", str(pid)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, cwd=str(REPO_DIR),
            creationflags=NO_WINDOW,
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return out or f"цепь #{pid}: пустой ответ демона ({action})."
    except Exception as e:
        return f"цепь #{pid}: ошибка {action} ({type(e).__name__}: {e})."


def _chain_cb_route(data, uid):
    """ЧИСТОЕ решение по нажатию кнопки цепи — без Telegram/сети, потому легко голденить.

    Инцидент (14.07 23:37 и 15.07 10:59): тап по «Статус/Стоп цепи» молчал — ни тоста, ни
    действия. Корень класса: (1) живой pc_agent крутился на коде ДО появления обработчика
    → callback уходил в никуда; (2) даже в новом коде ветка «не разобрал data» делала голый
    return БЕЗ answerCallbackQuery → «часики» на кнопке висли вечно = та же тишина.

    Классовый инвариант ЭТОЙ функции: поле answer ВСЕГДА непусто → на любой тап есть чем
    мгновенно снять «часики». Возвращает dict:
      ok:     bool          — исполнять ли действие демоном (_chain_cli)
      action: 'stop'|'status'|None
      pid:    str|None
      answer: str           — текст мгновенного answerCallbackQuery (тост), НИКОГДА не пустой
      alert:  bool          — show_alert (модалка) — для отказов/устаревших карточек
      note:   str|None      — пояснение в чат (для отказа/устаревшей); None → шлём вывод демона
    """
    parsed = _chain_cb_parse(data)
    zayavka = _zayavka_cb_parse(data)
    gate = _gate_cb_parse(data)
    if not _chain_cb_authorized(uid):
        # owner-gate раньше разбора: чужому не подсказываем формат кнопок.
        return {"ok": False, "kind": None, "action": None, "pid": None,
                "answer": "⛔ нет прав", "alert": True, "note": None}
    if gate is not None:
        # Кнопка ВОРОТ клиентского контура. Тост говорит РАЗНОЕ про «да» и «нет» намеренно: «да»
        # только ЗАПИСЫВАЕТ основание (применение пойдёт штатной реконсиляцией), «нет» не применяет
        # и не откатывает ничего вовсе. Обещать кнопкой «выкатываю» значило бы соврать о вердикте.
        action, commit = gate
        return {"ok": True, "kind": "gate", "action": action, "pid": commit,
                "answer": ("✅ записываю твоё «да»…" if action == "yes"
                           else "⛔ записываю отказ, ничего не применяю…"),
                "alert": False, "note": None}
    if zayavka is not None:
        # Кнопка ЗАЯВКИ внешнего канала. Тост говорит про «принято к сведению», а не про
        # «выполняю»: ни один ответ здесь задачи не ставит, и владелец обязан видеть это
        # раньше, чем отпустит палец.
        action, tid = zayavka
        return {"ok": True, "kind": "zayavka", "action": action, "pid": tid,
                "answer": ("✅ принимаю к сведению…" if action == "yes" else "❌ закрываю заявку…"),
                "alert": False, "note": None}
    if parsed is None:
        # Наш бот (AGENT_BOT_TOKEN) шлёт ТОЛЬКО карточки цепи, заявок и ворот → неразобранный
        # callback = старый формат / протухшая карточка. Честно говорим это, а не молчим.
        #
        # ПРИЧИНУ НЕ ВЫДУМЫВАЕМ (класс 06.09.2026, живой замер). 05.09 10:29:12 владелец нажал
        # [⛔ Не выкатывай] и получил «карточка устарела» — а она НЕ устаревала: обработчик кнопки
        # ворот внесён коммитом 4bbe548 в 05:20, живой процесс агента стартовал 03.09 в 05:02 и
        # перезапустился только в 12:06. Кнопка была НОВЕЕ процесса, который её ловил. Диагноз,
        # выданный за единственный, стоил владельцу второго потерянного нажатия — поэтому строка
        # ниже называет ОБЕ причины и ни одну из них не выдаёт за установленную.
        return {"ok": False, "kind": None, "action": None, "pid": None,
                "answer": "карточка устарела", "alert": True,
                "note": decision_waits.stale_explain(
                    "эта кнопка", "",
                    "формат кнопки мне не знаком. Две причины, и какая из них — я отсюда не вижу: "
                    "(1) карточка УСТАРЕЛА (старый формат); (2) кнопка НОВЕЕ меня — код с ней уже в репо, а "
                    "живой процесс агента ещё на прежнем (так было 05.09 в 10:29). "
                    "Пришли «статус» — дам актуальную картинку и версию",
                    # Тап по кнопке ВОРОТ, которую мы не разобрали, — это ответ владельца, НЕ
                    # ставший одобрением. Молча потерять его нельзя: называем словесный путь тут
                    # же, иначе владелец во второй раз останется без двери.
                    answer_at=f"если это была карточка ворот — {GATE_ANSWER_AT}",
                    parked=None)}
    action, pid = parsed
    return {"ok": True, "kind": "chain", "action": action, "pid": pid,
            "answer": ("⏹ останавливаю цепь…" if action == "stop" else "📊 читаю статус…"),
            "alert": False, "note": None}


async def _chain_reply(context, q, text):
    """Ответ в чат/тему карточки (форум-фолбэк → та же тема). Никогда не роняет обработчик."""
    msg = q.message
    if msg is None:
        return
    kwargs = {}
    if getattr(msg, "message_thread_id", None):
        kwargs["message_thread_id"] = msg.message_thread_id
    try:
        await context.bot.send_message(msg.chat_id, text, **kwargs)
    except Exception:
        alog.exception("chain-callback: не смог отправить ответ")


async def on_chain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажатие кнопки цепи. Гейт: ТОЛЬКО владелец. Стоп/статус исполняет демон, ответ — в чат карточки.

    Порядок жёсткий: сперва МГНОВЕННЫЙ answerCallbackQuery (снять «часики» на кнопке при ЛЮБОМ
    исходе), затем видимое сообщение. Даже если тост не дошёл (протухший query «too old» / сеть) —
    всё равно отдаём ответ отдельным сообщением, чтобы тап никогда не выглядел «проглоченным»."""
    q = update.callback_query
    if q is None:
        return
    uid = q.from_user.id if q.from_user else None
    route = _chain_cb_route(q.data, uid)
    alog.info(f"chain-callback: data={q.data!r} uid={uid} → action={route['action']} ok={route['ok']}")
    # 1) МГНОВЕННЫЙ ACK — снимаем «часики» при любом исходе. Провал (query too old/сеть) не глотаем молча.
    try:
        await q.answer(route["answer"], show_alert=route["alert"])
    except Exception as e:
        alog.warning(f"chain-callback: answerCallbackQuery не прошёл ({type(e).__name__}: {e}) — ответлю сообщением")
    # 2) отказ / устаревшая карточка: без действия, но с честным пояснением (если есть)
    if not route["ok"]:
        if route["note"]:
            await _chain_reply(context, q, route["note"])
        return
    # 3) действие исполняет демон / ступень G (свой канал к Bridge), результат — сообщением
    #    в чат карточки. Роутим ПО ВИДУ кнопки: у цепи и у заявки разные исполнители и разные
    #    последствия, и складывать их в один вызов значило бы звать «стоп цепи» на заявке.
    runner = {"zayavka": _zayavka_cli, "gate": _gate_cli}.get(route.get("kind"), _chain_cli)
    reply = await asyncio.to_thread(runner, route["action"], route["pid"])
    await _chain_reply(context, q, reply)


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

        elif text in ("статус модербот", "moderbot status", "статус moderbot"):
            alog.info("команда: moderbot status")
            await _send(context, chat_id, await asyncio.to_thread(MB.status))

        elif text in ("старт модербот", "moderbot start", "старт moderbot"):
            alog.info("команда: moderbot start")
            await _send(context, chat_id, await asyncio.to_thread(MB.start))

        elif text in ("стоп модербот", "moderbot stop", "стоп moderbot"):
            alog.info("команда: moderbot stop")
            await _send(context, chat_id, await asyncio.to_thread(MB.stop))

        elif text in ("сводка", "summary"):
            alog.info("команда: summary")
            digest, full = await asyncio.to_thread(summarize_log)
            await _send(context, chat_id, digest)
            if len(full) > MAX_TG:  # лог большой — шлём полный файлом
                await _send_log_file(context, chat_id)

        elif text in ("обновись", "перезапустись", "обнови себя", "restart"):
            alog.info("команда: self-restart")
            await self_restart(context, chat_id)

        else:
            alog.info("неизвестная команда: %r", (msg.text or "")[:120])
            await _send(context, chat_id, unknown_command_reply(msg.text))
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

def _read_agent_lock_pid() -> int:
    """Номер из лока — ПЕРВОЙ строкой (во второй, у лока нового формата, лежит личность)."""
    rec = proc_identity.read_lock(str(AGENT_LOCK))
    return int(rec["pid"]) if rec else 0


def acquire_agent_lock() -> bool:
    """True — лок наш, работаем; False — другой агент уже жив, выходим.

    ЖИВОЙ СЛУЧАЙ, РАДИ КОТОРОГО ЭТО ПЕРЕПИСАНО (23.08.2026). BSOD 0x7F в 05:14 —
    `release_agent_lock()` не позвался, в локе остался номер 8960. После загрузки 8960 достался
    `wlanext.exe`, а прежняя проба спрашивала у `tasklist` ровно одно: «есть ли процесс с таким
    номером». Есть. Агент выходил с «pc_agent уже запущен (PID 8960)» НАВСЕГДА, полоса стояла
    без агента 05:14 → 06:16, а контур-вотчдог честно рапортовал «подъём ok, процесса нет» и
    через три попытки вставал в стоп. Класс был назван в тот же день; правки не случилось, и
    через три дня он повторился на модерботе ценой 3 суток 15 часов простоя.

    Теперь личность владельца — номер + имя запуска + момент старта (`proc_identity`), стухший
    лок программа опознаёт и снимает САМА (файл уезжает уликой в `tmp/stale_locks/`), а молчание
    пробы больше не читается как «мёртв»: прежний `_agent_pid_alive` глотал свой отказ в False
    и уводил в кражу лока — то есть в двойного агента, от которого гард и стои́т."""
    ok, verdict, why = proc_identity.acquire(
        str(AGENT_LOCK), script=os.path.basename(__file__),
        log=lambda m: alog.info(f"pc_agent.lock: {m}"))
    if ok:
        return True
    if verdict == proc_identity.OURS_ALIVE:
        alog.warning(f"pc_agent уже запущен — выхожу, второй экземпляр не поднимаю. {why}")
    else:
        alog.warning(f"НЕ стартую [{verdict}] (защита от двойного агента): {why}")
    return False


def release_agent_lock() -> None:
    """Снять лок, только если он наш — по номеру И моменту старта."""
    try:
        proc_identity.release(str(AGENT_LOCK))
    except Exception as e:
        alog.warning(f"не смог снять pc_agent.lock: {e}")


# ===================== авто-синк cowork → общий мозг (Bridge) =====================
# Слепые зоны 0.1/0.2: pc_agent фоном относит отчёты Cowork в cowork_log на Drive.
# Cowork дописывает строки в DROP_FILE; синк раз в SYNC_PERIOD_SEC шлёт НОВЫЕ строки в
# Bridge (read_doc → склейка «новое сверху» → write_doc) и двигает байтовый offset.
# Идемпотентность: offset сдвигается ТОЛЬКО после успешного write_doc → дважды не отправим,
# при ошибке Bridge строки не теряются (повтор на след. тике).
# БЕЗОПАСНОСТЬ: это ТОЛЬКО HTTP к Bridge. НЕ трогает userbot, session, Telethon-клиент.
# Отдельный лёгкий канал; забор вокруг личного номера эта задача не затрагивает.
try:
    import requests
except Exception:
    requests = None

DROP_FILE = REPO_DIR / "cowork_drop.log"        # сюда Cowork дописывает строки-отчёты
DROP_OFFSET = REPO_DIR / "cowork_drop.offset"   # байтовый указатель уже отнесённого
COWORK_DOC = "cowork_log"                        # имя дока в Brain (Bridge)
SYNC_PERIOD_SEC = 30                             # 30 секунд между тиками
BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()


def _drop_read_offset() -> int:
    try:
        return int(DROP_OFFSET.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0


def _drop_write_offset(n: int) -> None:
    try:
        DROP_OFFSET.write_text(str(n), encoding="utf-8")
    except Exception as e:
        alog.warning(f"cowork-синк: не смог записать offset: {e}")


def _bridge_read_doc() -> str:
    r = requests.get(
        BRIDGE_URL,
        params={"action": "read_doc", "name": COWORK_DOC, "token": BRIDGE_TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok", False):
        raise RuntimeError(f"read_doc не ok: {data}")
    return data.get("text", "")


def _bridge_write_doc(text: str) -> None:
    r = requests.post(
        BRIDGE_URL,
        json={"token": BRIDGE_TOKEN, "action": "write_doc", "name": COWORK_DOC, "text": text},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok", False):
        raise RuntimeError(f"write_doc не ok: {data}")


def _flush_cowork_drop() -> int:
    """Отнести новые байты drop-файла в мозг (Bridge). Возвращает число отнесённых байт.
    Идемпотентность: байтовый offset; сдвигаем ТОЛЬКО после успешного write_doc.
    При ошибке Bridge offset НЕ двигаем → строки не теряются, повтор на след. тике."""
    if not DROP_FILE.exists():
        return 0
    size = DROP_FILE.stat().st_size
    off = _drop_read_offset()
    if off > size:          # drop усечён/переписан — сбрасываем указатель
        off = 0
    if size <= off:
        return 0            # новых байт нет
    raw = DROP_FILE.read_bytes()[off:size]
    chunk = raw.decode("utf-8", errors="replace").strip()
    if not chunk:
        _drop_write_offset(size)   # только пустые строки — просто двигаем offset
        return 0
    current = _bridge_read_doc()                  # R
    _bridge_write_doc(chunk + "\n\n" + current)  # M+W: новое сверху; текст непуст
    _drop_write_offset(size)                      # успех → фиксируем offset
    return size - off


async def _cowork_sync_job(context):
    # Один тик синка — JobQueue вызывает его каждые SYNC_PERIOD_SEC. Канонично для PTB,
    # без Application.create_task (та «мина» создавала задачу, пока приложение не запущено).
    try:
        n = await asyncio.to_thread(_flush_cowork_drop)
        if n:
            alog.info(f"cowork-синк: отнёс {n} новых байт в cowork_log.")
    except Exception as e:
        alog.warning(f"cowork-синк: ошибка тика (offset не сдвинут, повтор позже): {e}")


def _announce_raise(kind, door):
    """ГОЛОС подъёма ребёнка мимо ворот (03.09.2026). Ничего не решает и ничего не запрещает:
    зовётся ПОСЛЕ состоявшегося подъёма и только называет факт — какой коммит лежал на диске,
    отличается ли он от прежнего подъёма, есть ли среди отличий клиентские файлы.

    ПОЧЕМУ ЗДЕСЬ ЕЩЁ ОДИН try. `deploy_voice.announce` исключений наружу не выпускает сам, но
    правило «ботов не ронять ни в одном исходе» не должно держаться на чужой дисциплине: сломать
    подъём userbot ради строки в журнале — цена, которой у голоса нет. Второй забор дешевле разбора.
    Журнал и пуш голос берёт своими каналами (отдельные процессы `cowork_log_append`/`dispatch_notify`),
    секретов агента он не видит."""
    try:
        deploy_voice.announce(kind, door, log_fn=alog.info)
    except BaseException as e:                      # noqa: BLE001 — подъём уже состоялся
        alog.warning("голос подъёма мимо ворот отказал (%s) — подъём состоялся, факт кода не назван", e)


def main():
    # stdout/stderr → UTF-8 ДО всего: логи агента и вывод (в т.ч. traceback) содержат кириллицу
    # и эмодзи; без этого StreamHandler ронял бы запись на 📊 под cp1251-консолью Планировщика.
    io_utf8.force_utf8()
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
            # ГОЛОС ПОДЪЁМА МИМО ВОРОТ (03.09.2026, дверь B1 переписи). Ворота клиентского контура
            # живут в демоне и об этой двери не знают: агент поднимает userbot с диска, а какой
            # код лежал на диске — не называл никто (замер: 0 таких строк за 42.5 суток при 19
            # состоявшихся выкатках клиентских файлов). Дверь НЕ закрывается и подъём НЕ
            # отменяется: голос зовётся ПОСЛЕ start() и исключений наружу не выпускает вовсе.
            _announce_raise("userbot", deploy_voice.DOOR_READOPT)

        app = ApplicationBuilder().token(AGENT_BOT_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.UpdateType.EDITED, on_message))
        app.add_handler(CallbackQueryHandler(on_chain_callback))  # кнопки цепей дирижёра (owner-gate)
        app.add_error_handler(on_error)  # ФИКС 1: сетевые ошибки не роняют агента
        # cowork-синк через JobQueue (канонично, без create_task-мины). Планируем ТОЛЬКО при кредах
        # и наличии JobQueue (иначе просто отключаем — НЕ роняем агента).
        if requests is not None and BRIDGE_URL and BRIDGE_TOKEN and app.job_queue is not None:
            app.job_queue.run_repeating(_cowork_sync_job, interval=SYNC_PERIOD_SEC, first=20)
            alog.info(f"cowork-синк включён (JobQueue): {DROP_FILE.name} каждые {SYNC_PERIOD_SEC}с, первый тик +20с.")
        else:
            alog.info("cowork-синк ОТКЛЮЧЁН: нет requests / BRIDGE-кред / JobQueue.")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        release_agent_lock()


if __name__ == "__main__":
    main()
