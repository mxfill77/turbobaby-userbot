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
  ящик снять <метка>        → снять сигнальную остановку ящика Штаба (та же дверь, что у кнопки
                              под извещением: shtab_box_run.py --free <метка>)
  урок кандидаты            → уроки, записанные кнопкой «Обучить», но ещё НЕ включённые
  урок включи N: причина    → перевести кандидата #N в ДЕЙСТВУЮЩИЕ (lesson_promote.py; без
                              причины — отказ, право судит moderation_core.may_write_rule)
  урок откати N             → вернуть урок #N ровно в состояние до включения (не «снять»)
  урок след                 → кто, когда и какой урок включал
  урок наборы               → какие наборы лежат в базе и по сколько строк
  урок набор <ключ>         → ШАГ 1: что за набор — сколько уйдёт, когда залит, кем (чтение)
  урок набор сними <ключ> <N>: причина
                            → ШАГ 2: снять НАБОР ЦЕЛИКОМ (lesson_batch.py). Подтверждение обязано
                              называть объект — ключ И число строк; без числа или с чужим числом
                              отказ, и не тронуто ничего
  урок набор верни <ключ>   → вернуть набор ровно в то состояние, что было до снятия (побайтно)
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


# ───────── ЗАМОК БЕЗОПАСНОГО РЕЖИМА ПЕРЕД ПОДЪЁМОМ РЕБЁНКА (09.09.2026) ─────────
# Ребёнок поднимается ВСЕГДА — замок дверь не закрывает ни одной веткой. Он лишь отнимает у
# неодобренного кода право писать ЖИВОМУ клиенту и говорит об этом вслух в ту же секунду.
# Вся логика вердикта и все слова живут в `deploy_voice` (родитель, вне клиентского замыкания);
# здесь — только вызов в ДВУХ местах подъёма, ровно там, где уже передаются `cwd` и `creationflags`.

def _safe_mode_gate(kind):
    """Вердикт замка ДО `Popen`. → (env|None, decision|None).

    `env=None` означает «ничего не подмешиваем»: `Popen(env=None)` — это наследование окружения
    родителя, то есть прежнее поведение байт-в-байт (исход «коммит одобрен» и снятый замок).
    Исключений не выпускает: подъём важнее замка. Но и незнание здесь не толкуется в пользу
    движения — отказ самого замка кончается безопасным режимом, а не тишиной."""
    try:
        d = deploy_voice.safe_mode_decision()
        return deploy_voice.safe_mode_env(d), d
    except BaseException:                    # noqa: BLE001 — замок не имеет права уронить подъём
        alog.warning("замок безопасного режима отказал целиком — поднимаю БЕЗ права писать клиенту")
        try:
            # Имя флага названо здесь ВТОРОЙ раз в дереве СОЗНАТЕЛЬНО: это последний рубеж, и он
            # не имеет права зависеть от того самого модуля, который только что отказал.
            return dict(os.environ, SUGGEST_TEST_MODE="1"), None
        except BaseException:
            return None, None


def _say_safe_mode(kind, decision):
    """Причина безопасного режима ВСЛУХ сразу после `Popen`. Молчит законно, когда режима нет."""
    try:
        deploy_voice.announce_safe_mode(kind, deploy_voice.DOOR_START, decision, log_fn=alog.info)
    except BaseException:                    # noqa: BLE001 — крик не отменяет состоявшийся подъём
        pass


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
        child_env, lock = _safe_mode_gate("userbot")   # вердикт ДО запуска; None = не трогаем env
        self.proc = subprocess.Popen([str(VENV_PY), str(LISTEN_SCRIPT)], cwd=str(REPO_DIR),
                                     stdout=logf, stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW, env=child_env)
        alog.info(f"userbot запущен агентом, PID {self.proc.pid}")
        _say_safe_mode("userbot", lock)

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
        child_env, lock = _safe_mode_gate("moderbot")  # вердикт ДО запуска; None = не трогаем env
        self.proc = subprocess.Popen([str(VENV_PY), str(MODERBOT_SCRIPT)], cwd=str(REPO_DIR),
                                     stdout=logf, stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW, env=child_env)
        alog.info(f"moderation_bot запущен агентом, PID {self.proc.pid}")
        _say_safe_mode("moderbot", lock)
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
    "ящик снять <метка> — снять сигнальную остановку ящика Штаба (метка — из извещения)",
    "урок кандидаты — какие уроки записаны, но ещё НЕ включены",
    "урок включи N: причина — включить кандидата #N (без причины — отказ)",
    "урок откати N — вернуть урок #N ровно в то состояние, что было до включения",
    "урок след — кто, когда и какой урок включал",
    "урок наборы · урок набор <ключ> — какие наборы есть и что в наборе (чтение)",
    "урок набор сними <ключ> <N>: причина — снять НАБОР целиком (объект: ключ И число)",
    "урок набор верни <ключ> — вернуть набор ровно в то, что было до снятия",
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


# ── КНОПКА ВЕРДИКТА ЭКЗАМЕНА В ГРУППЕ-ТРЕНАЖЁРЕ (11.09.2026) ───────────────────────────────
# Карточку кейса шлёт `exam_show.py` НАШИМ ЖЕ токеном (dispatch_notify.send_chat_strict →
# AGENT_BOT_TOKEN), поэтому тап по ней приходит СЮДА, а не модерботу. Нового бота заводить не
# понадобилось ни для показа, ни для приёма: пара «dispatch_notify шлёт — pc_agent ловит» уже
# работает на кнопках ворот, здесь она переиспользована.
#
# ЧТО ДЕЛАЕТ ТАП: зовёт `exam_show.py --tap ok|no --case N` — ту же дверь, что и рука с консоли.
# Дверь судит право (`moderation_core.may_write_rule`, fail-closed) и кладёт вердикт КАНДИДАТОМ
# в свой журнал. В реестр ворот, в счёт «пройдено N из 17» и в базу уроков эта ветка не пишет
# ни байта — кандидат действующим не становится ни одним путём.
#
# Хвост callback_data — НОМЕР КЕЙСА, и регулярка пропускает только цифры: из Telegram в командную
# строку не уезжает ничего, кроме числа, которое владелец мог бы набрать и сам.
#
# ★ 12.09.2026, рабочее место экзамена. Кнопок стало пять видов, и ВСЕ ОНИ ПО-ПРЕЖНЕМУ несут
# только цифры: `ok`/`no` — вердикт, `h1`…`h4` — номер подсказки-тумблера, `own` — «напишу своё»,
# `go` — «✔ Применить». Номер подсказки уехал В ГОЛОВУ ключа, а не вторым хвостом, ровно затем,
# чтобы хвост остался ОДНИМ числом и правило «из Telegram едет только число» не пришлось
# ослаблять ради второй группы.
EXAM_CB_RE = re.compile(r"^exam:(ok|no|own|go|h[1-4]):([0-9]{1,3})$")
# ★ 18.09.2026, ЖИВОЙ НАБОР (задание 63-t). Номера кейсов у двух наборов общие (кейс 13 есть в
# обоих), поэтому набор едет В ГОЛОВЕ ключа, а не в хвосте: хвост остаётся одним числом. Тап по
# `examlive:` зовёт ту же дверь с `--live`, и вердикт ложится в журнал ЖИВОГО набора
# (`exam_live/verdicts.tsv`), а не тренажёра. Регулярка отдельная и с тем же хвостом: разбор
# тренажёра (`EXAM_CB_RE`) остаётся байт в байт прежним, а `exam_show.agent_parse` сверяет карточку
# живого набора именно с этой строкой.
EXAM_LIVE_CB_RE = re.compile(r"^examlive:(ok|no|own|go|h[1-4]):([0-9]{1,3})$")
EXAM_ANSWER_AT = ("консоль репозитория, командой «exam_show.py --case N --tap ok|no --who <имя>» "
                  "(для живого набора — с --live; словом в тему это пока не делается)")
# Группа-тренажёр: карточка экзамена висит ТАМ, и ответ владельца на «✍️ своё» приходит оттуда же.
# Число названо здесь литералом по той же причине, по какой оно названо литералом в `exam_show`:
# адрес, взятый из изменяемого места, может однажды указать в клиентский чат.
EXAM_CHAT_ID = -5193185299


# ── КНОПКА СНЯТИЯ СИГНАЛЬНОЙ ОСТАНОВКИ ЯЩИКА ШТАБА (06.09.2026) ────────────────────────────
# Ящик Штаба (`shtab_box_run`) сам останавливает полосу, когда его задания перестают
# доказываться. До 06.09 остановка не выходила наружу вовсе, а снять её можно было ровно одним
# способом — положить строку `[[ЯЩИК СНЯТЬ метка=…]]` В УЗЕЛ МОЗГА. С телефона это не делается,
# и владелец узнавал о стоящей полосе из журнала, который читает не он.
#
# ЧТО ДЕЛАЕТ ТАП: зовёт `shtab_box_run.py --free <метка>` — ту же дверь, что и слово. Ящик не
# трогается ничем другим: ни задач, ни очереди, ни узла мозга эта ветка не касается, снимается
# РОВНО один записанный случай. Хвост callback_data — метка случая (12 hex, `mark_of`), и в
# командную строку из Telegram уезжает только она, уже просеянная этой регуляркой.
BOX_CB_RE = re.compile(r"^box:free:([0-9a-f]{6,32})$")
# Слово владельца — запасной путь на случай, когда кнопка не сработала (живой класс 05.09:
# процесс агента оказался СТАРШЕ кнопки, и тап получил «карточка устарела»). Форма та же, что
# печатает извещение (`shtab_box_signals.RELEASE_WORD`), и сверяет их тест.
BOX_WORD_RE = re.compile(r"^(?:ящик|box)[\s:]+(?:снять|free)\s+([0-9a-f]{6,32})$")
BOX_WORD_HEAD_RE = re.compile(r"^(?:ящик|box)[\s:]+(?:снять|free)\b")
BOX_ANSWER_AT = ("тема «PC-дев», сообщением «ящик снять <метка>» — метка стои́т в самом извещении "
                 "об остановке")


def _box_cb_parse(data):
    """callback_data кнопки ящика → (action, метка) | None (не наш callback)."""
    m = BOX_CB_RE.match(str(data or ""))
    return ("free", m.group(1)) if m else None


def box_word_mark(text):
    """Слово владельца «ящик снять <метка>» → метка | "" (не наша команда).

    ЧИСТАЯ функция, поэтому голденится без Telegram. Регистр и лишние пробелы не
    считаются: строку с меткой КОПИРУЮТ из извещения, а копируют её люди.
    """
    m = BOX_WORD_RE.match(" ".join(str(text or "").split()).strip().lower())
    return m.group(1) if m else ""


def _box_cli(action, mark):
    """Снятие остановки через `shtab_box_run --free` → текст ответа.

    Субпроцессом и той же механикой, что `_gate_cli`/`_zayavka_cli`, по той же
    причине: агент не держит ни клиента Моста, ни его секретов — он роутит тап,
    гейтит владельца и показывает результат. Слова исхода печатает ЯЩИК
    (`free_mark`), а не мы: вторая формулировка того же исхода разошлась бы с
    первой молча.

    ДЕЙСТВИЕ ЗДЕСЬ РОВНО ОДНО, и чужое имя дальше не едет: у остановки нет ответа
    «не снимать» — бездействие и есть «не снимать».
    """
    if action != "free":
        return f"ящик Штаба: не понял кнопку ({action})."
    if not BOX_CB_RE.match("box:free:%s" % mark):
        return (f"ящик Штаба: метка «{mark}» не разобрана — она шестнадцатеричная, 6–32 знака, и "
                f"стои́т в самом извещении. Ничего не снято.")
    if not VENV_PY.exists():
        return f"ящик Штаба: не нашёл python venv ({VENV_PY})."
    try:
        r = subprocess.run(
            [str(VENV_PY), str(REPO_DIR / "shtab_box_run.py"), "--free", str(mark),
             "--by", "Telegram"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, cwd=str(REPO_DIR),
            creationflags=NO_WINDOW,
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return out or (f"ящик Штаба: пустой ответ на снятие метки {mark}. "
                       f"Запасной путь — {BOX_ANSWER_AT}.")
    except Exception as e:
        return (f"ящик Штаба: снятие метки {mark} НЕ прошло ({type(e).__name__}: {e}). "
                f"Запасной путь — {BOX_ANSWER_AT}.")


# ── СЛОВО ВЛАДЕЛЬЦА: ВКЛЮЧИТЬ УРОК / ОТКАТИТЬ ВКЛЮЧЕНИЕ (09.09.2026) ────────────────────────
# ЧТО ЭТО ЗА ДВЕРЬ И ПОЧЕМУ ОНА ЗДЕСЬ. Кнопка «🎓 Обучить» в тренажёре кладёт урок КАНДИДАТОМ, а
# кандидата бот не читает: действующим он становится ОТДЕЛЬНЫМ действием (`lesson_store.promote`).
# До 09.09 это действие не звал никто, кроме тестов, — то есть вердикт владельца правилом не
# становился ни при каком его старании. Дверь нужна такая, до которой владелец дотягивается С
# ТЕЛЕФОНА, и тема 205 — единственная его дверь на ПК, которую можно завести, не трогая ни
# клиентского бота, ни модербота: ровно тем же устройством, что «ящик снять <метка>».
#
# ЧТО ДЕЛАЕТ СЛОВО: зовёт `lesson_promote.py` субпроцессом — ту же дверь, что и с консоли. Право
# судит НЕ агент: allowlist темы 205 решает «пускать ли к команде», а «вправе ли этот человек
# менять правила бота» решает `moderation_core.may_write_rule` внутри двери, по ИМЕНИ в Telegram.
# Два гейта здесь не дублируют друг друга — они про разное, и слабее ни один не делает.
#
# ПРИЧИНА ЕДЕТ ДОСЛОВНО, И ПОЭТОМУ РАЗБИРАЕТСЯ ИЗ ОРИГИНАЛА СООБЩЕНИЯ. Роутер темы 205 работает
# на `text` = `msg.text.lower()`, и причина, проехавшая через него, легла бы в таблицу уроков
# строчными буквами навсегда. Здесь берётся `msg.text` как есть.
LESSON_ON_RE = re.compile(
    r"^(?:урок|lesson)[\s:]+(?:включи(?:ть)?|on)\s+#?(\d+)\s*[:\-—]?\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
LESSON_BACK_RE = re.compile(r"^(?:урок|lesson)[\s:]+(?:откати(?:ть)?|rollback)\s+#?(\d+)$",
                            re.IGNORECASE)
LESSON_LIST_RE = re.compile(r"^(?:урок|lesson)[\s:]+(?:кандидаты|candidates)$", re.IGNORECASE)
LESSON_TRACE_RE = re.compile(r"^(?:урок|lesson)[\s:]+(?:след|trace)$", re.IGNORECASE)

# НАБОР — ВТОРОЙ ОБЪЕКТ ТОГО ЖЕ СЛОВА (09.09.2026). Снятие набора зовётся тем же «урок» и тем же
# роутером, что перевод: второго роутера рядом не заводим. Отличает объект слово «набор» сразу
# после команды — у урока объект номер, у набора ключ источника.
#
# ЧИСЛО В ПОДТВЕРЖДЕНИИ — НЕОБЯЗАТЕЛЬНАЯ ГРУППА, И ЭТО СОЗНАТЕЛЬНО. Соблазн потребовать его
# регуляркой велик, но тогда «урок набор сними экспорт_переписки: ошиблись файлом» (число забыли)
# уехало бы в «хвост не разобран» — то есть владелец услышал бы «не понял команду» вместо
# «подтверждение обязано называть число». Отказ выписывает ДВЕРЬ, которая знает, чего не хватает.
#
# КЛЮЧ — `[^\s:]+`, а не `\S+`: владелец пишет «сними экспорт_переписки: причина» без пробела
# перед двоеточием, и жадное `\S+` унесло бы двоеточие внутрь ключа — набор стал бы неизвестным
# на ровном месте. Жадность при этом обязательна (ленивое `\S+?` отдало бы ключом первую букву).
LESSON_BATCH_OFF_RE = re.compile(
    r"^(?:урок|lesson)[\s:]+набор[\s:]+(?:сними|снять|off)\s+([^\s:]+)(?:\s+#?(\d+))?\s*[:\-—]?"
    r"\s*(.*)$", re.IGNORECASE | re.DOTALL)
LESSON_BATCH_BACK_RE = re.compile(
    r"^(?:урок|lesson)[\s:]+набор[\s:]+(?:верни(?:ть)?|back)\s+(\S+)$", re.IGNORECASE)
# ПОКАЗ — ключ и БОЛЬШЕ НИЧЕГО. Глаголы движения исключены явным взглядом вперёд: без него
# «урок набор сними» (ключ забыли) разобралось бы как ПОКАЗ набора с ключом «сними» — то есть
# опечатка в опасной команде притворилась бы безобидным чтением.
LESSON_BATCH_SHOW_RE = re.compile(
    r"^(?:урок|lesson)[\s:]+набор[\s:]+(?!сними\b|снять\b|off\b|верни\b|вернуть\b|back\b)(\S+)$",
    re.IGNORECASE)
LESSON_BATCH_LIST_RE = re.compile(r"^(?:урок|lesson)[\s:]+(?:наборы|batches)$", re.IGNORECASE)

# «Почти команда» — чтобы промах формой не уехал в общее «не знаю»: команду мы знаем, не
# разобрался ХВОСТ. Тот же довод, что у `BOX_WORD_HEAD_RE`.
LESSON_HEAD_RE = re.compile(
    r"^(?:урок|lesson)[\s:]+(?:включи(?:ть)?|откати(?:ть)?|кандидаты|след|наборы|набор|on"
    r"|rollback|candidates|trace|batches)\b", re.IGNORECASE)
LESSON_ANSWER_AT = ("тема «PC-дев» / консоль ПК, командой "
                    "«venv/Scripts/python.exe lesson_promote.py --who <имя> --promote N "
                    "--why \"причина\"»")
BATCH_ANSWER_AT = ("тема «PC-дев» / консоль ПК, командой "
                   "«venv/Scripts/python.exe lesson_batch.py --who <имя> --off <ключ> "
                   "--count N --why \"причина\"»")
# Действия НАБОРА — одним перечнем, а не проверкой префикса `batch_` по месту: перечень читается
# и роутером, и выбором двери, и разъехаться им нечем.
LESSON_BATCH_ACTS = ("batch_off", "batch_back", "batch_show", "batch_census")


def lesson_word(text):
    """Слово владельца про урок или НАБОР → (действие, номер, причина, ключ) | None.

    ЧИСТАЯ функция, поэтому голденится без Telegram. Действия урока: `promote` (номер и причина),
    `rollback` (номер), `candidates`/`trace`. Действия набора: `batch_show` (ключ),
    `batch_off` (ключ, число в поле номера, причина), `batch_back` (ключ), `batch_census`.
    Регистр команды не значим, а регистр ПРИЧИНЫ сохраняется дословно — её текст ложится в
    таблицу уроков и читается человеком.

    ЧЕТВЁРТОЕ ПОЛЕ (ключ набора) добавлено 09.09.2026 и всегда присутствует: у команд урока оно
    пустая строка. Отдавать наборам свой кортеж другой длины значило бы завести у вызывающего
    развилку по длине — то есть второй роутер под видом распаковки."""
    body = " ".join(str(text or "").split()).strip().lstrip("/").strip()
    if not body:
        return None
    # НАБОР РАЗБИРАЕТСЯ ПЕРВЫМ: его формы длиннее и специфичнее, и любая из них, попав сначала на
    # разбор урока, была бы отвергнута — но уже после того, как «урок» съеден.
    m = LESSON_BATCH_OFF_RE.match(body)
    if m:
        count = int(m.group(2)) if m.group(2) else None
        return ("batch_off", count, m.group(3).strip(), m.group(1))
    m = LESSON_BATCH_BACK_RE.match(body)
    if m:
        return ("batch_back", None, "", m.group(1))
    m = LESSON_BATCH_SHOW_RE.match(body)
    if m:
        return ("batch_show", None, "", m.group(1))
    if LESSON_BATCH_LIST_RE.match(body):
        return ("batch_census", None, "", "")
    m = LESSON_ON_RE.match(body)
    if m:
        return ("promote", int(m.group(1)), m.group(2).strip(), "")
    m = LESSON_BACK_RE.match(body)
    if m:
        return ("rollback", int(m.group(1)), "", "")
    if LESSON_LIST_RE.match(body):
        return ("candidates", None, "", "")
    if LESSON_TRACE_RE.match(body):
        return ("trace", None, "", "")
    return None


def _lesson_cli(action, number, why, who, source=""):
    """Движение уроком или НАБОРОМ через дверь-CLI → текст ответа.

    Субпроцессом и той же механикой, что `_box_cli`/`_gate_cli`, по той же причине: агент роутит
    слово и показывает результат, а решение о праве, причине и следе принимает дверь. Слова исхода
    печатает ДВЕРЬ, а не мы: вторая формулировка того же исхода разошлась бы с первой молча.

    ДВЕРЕЙ ДВЕ, А РОУТЕР ОДИН: у урока объект — номер (`lesson_promote.py`), у набора — ключ
    источника (`lesson_batch.py`). Выбор двери идёт по перечню `LESSON_BATCH_ACTS`, а не по
    префиксу имени действия, разобранному здесь на месте."""
    if not VENV_PY.exists():
        return f"урок: не нашёл python venv ({VENV_PY})."
    door = "lesson_batch.py" if action in LESSON_BATCH_ACTS else "lesson_promote.py"
    argv = [str(VENV_PY), str(REPO_DIR / door)]
    # `who` и `why` приходят УЖЕ строками: имя нормализует единственное место, где «имени нет»
    # что-то значит (роутер темы 205), а причину отдаёт `lesson_word`, у которой пустая причина
    # это `""`, а не `None`. Второй раз подставлять здесь пустую строку значило бы завести ВТОРОЕ
    # место, решающее, что такое «нет имени», — и разъехаться с первым молча.
    if action == "promote":
        argv += ["--who", who, "--promote", str(int(number)), "--why", why]
    elif action == "rollback":
        argv += ["--who", who, "--rollback", str(int(number))]
    elif action == "candidates":
        argv += ["--candidates"]
    elif action == "trace":
        argv += ["--trace"]
    elif action == "batch_show":
        argv += ["--show", source]
    elif action == "batch_census":
        argv += ["--census"]
    elif action == "batch_back":
        argv += ["--who", who, "--back", source]
    elif action == "batch_off":
        # `--count` НЕ ПОДСТАВЛЯЕТСЯ, если владелец числа не назвал: «объект не назван» обязано
        # доехать до двери как отсутствие, а не как выдуманный ноль. Дверь на этом откажет
        # словами про подтверждение, а не посчитает, что снять надо ноль строк.
        argv += ["--who", who, "--off", source, "--why", why]
        if number is not None:
            argv += ["--count", str(int(number))]
    else:
        return f"урок: не понял действие ({action})."
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, cwd=str(REPO_DIR), creationflags=NO_WINDOW,
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        answer_at = BATCH_ANSWER_AT if action in LESSON_BATCH_ACTS else LESSON_ANSWER_AT
        return out or (f"урок: пустой ответ на «{action}». Запасной путь — {answer_at}.")
    except Exception as e:
        answer_at = BATCH_ANSWER_AT if action in LESSON_BATCH_ACTS else LESSON_ANSWER_AT
        return (f"урок: движение «{action}» НЕ прошло ({type(e).__name__}: {e}). "
                f"Запасной путь — {answer_at}.")


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


def _exam_cb_parse(data):
    """callback_data кнопки экзамена → (вердикт, номер кейса) | None (не наш callback)."""
    m = EXAM_CB_RE.match(str(data or ""))
    return (m.group(1), m.group(2)) if m else None


def _exam_live_cb_parse(data):
    """callback_data кнопки ЖИВОГО набора → (вердикт, номер кейса) | None (не наш callback)."""
    m = EXAM_LIVE_CB_RE.match(str(data or ""))
    return (m.group(1), m.group(2)) if m else None


# Стол экзамена — маленький json, который пишет `exam_show`. ЧИТАЕМ ЕГО САМИ, А НЕ ЧЕРЕЗ ИМПОРТ
# `exam_show`, и это не удобство, а ЗАМЕР: `import exam_show` в этом файле затаскивает в замыкание
# ЖИВЫХ ВОРОТ клиентского контура прогонщик корпуса (`exam_show.freeze` → `import trainer_run`), и
# правка прогонщика после этого упирается в ворота. Цена названа числом самим набором
# (`test_trainer_run.test_raner_ne_v_klientskom_konture`: замыкание ворот 77 → 78 файлов, лишним
# входит ровно `trainer_run.py`). Две строчки разбора здесь дешевле сужения охраны там.
#
# СОГЛАСИЕ ДВУХ ЧИТАТЕЛЕЙ ОДНОГО ФАЙЛА СТОРОЖИТ НАБОР: `exam_show.own_start` пишет — этот читатель
# читает, и контрактный тест сверяет их на живом файле. Разъедутся ключи — покраснеет он, а не
# владелец, чей урок молча не поймали.
EXAM_DESK = REPO_DIR / "exam_session.json"
EXAM_DESK_CASE, EXAM_DESK_UNTIL = "case", "own_until"
# Стол ЖИВОГО набора (18.09.2026) — свой файл в его каталоге; `exam_show.LIVE_DESK` пишет его, этот
# литерал читает, и контрактный тест сверяет их так же, как пару столов тренажёра.
EXAM_LIVE_DESK = REPO_DIR / "exam_live" / "desk.json"
EXAM_SET_TRAINER, EXAM_SET_LIVE = "trainer", "live"


def _exam_pending_set(desks=None, now=None):
    """Чьё ожидание «✍️ своё» живо → (набор, кейс) | None. Набор — `EXAM_SET_TRAINER`/`EXAM_SET_LIVE`.

    Ждать могут ОБА стола сразу (владелец нажал «своё» под карточкой тренажёра, а потом под
    карточкой живого набора). Текст тогда уходит тому, чьё ожидание ОТКРЫТО ПОЗЖЕ (больший
    `own_until`: срок у обоих один, 10 минут), — это последняя кнопка, которую владелец нажал.
    Ответ двери называет набор, так что ошибка адреса не бывает молчаливой."""
    best = None
    for name, path in (desks or ((EXAM_SET_TRAINER, EXAM_DESK), (EXAM_SET_LIVE, EXAM_LIVE_DESK))):
        wait = _exam_desk_wait(path, now)
        if wait is not None and (best is None or wait[1] > best[2]):
            best = (name, wait[0], wait[1])
    return (best[0], best[1]) if best else None


def _exam_desk_wait(path=None, now=None):
    """Живое ожидание на столе → (кейс, own_until) | None. Разбор ОДИН на оба стола."""
    try:
        with io.open(str(path or EXAM_DESK), encoding="utf-8") as f:
            desk = json.load(f)
        until = int(desk[EXAM_DESK_UNTIL])
    except Exception:                                                       # noqa: BLE001
        return None
    if (now if now is not None else time.time()) > until:
        return None
    return desk.get(EXAM_DESK_CASE), until


def _exam_pending_case(path=None, now=None):
    """Кейс, ждущий урока СВОИМИ СЛОВАМИ → номер | None (ожидания нет / истекли 10 минут).

    ОШИБКА ЧТЕНИЯ = «ожидания нет», fail-closed. Исход выбран, а не случился: ложное «жду»
    превратило бы первое же сообщение владельца в группе в урок, которого он не писал."""
    wait = _exam_desk_wait(path, now)
    return wait[0] if wait is not None else None


def exam_args(action, case):
    """Кнопка экзамена → аргументы двери `exam_show.py` | None (кнопка не наша).

    ЧИСТАЯ функция, и вынесена ею намеренно: это единственное место, где решается, ЧТО именно
    сделает тап, — и голденить его надо без субпроцесса, без Telegram и без диска. Всё, что она
    отдаёт, состоит из литералов и ЦИФР, приехавших через `EXAM_CB_RE`: строки из Telegram в
    командную строку не попадает ни одной."""
    tail = ["--case", str(case)]
    if action in ("ok", "no"):
        return tail + ["--tap", action]
    if action == "go":
        return tail + ["--apply"]
    if action == "own":
        return tail + ["--own"]
    if action.startswith("h") and action[1:].isdigit():
        return tail + ["--toggle", action[1:]]
    return None


def _exam_cli(action, case, who, stdin_text=None, live=False):
    """Кнопка экзамена через дверь `exam_show.py`: тот же путь, что и рука с консоли.

    `live` — кнопка ЖИВОГО набора: к аргументам двери спереди встаёт `--live`, и дверь пишет в его
    журнал, стол и базу уроков. Больше ничем путь живого набора от тренажёра не отличается.

    Субпроцессом и той же механикой, что `_gate_cli`, по той же причине: агент роутит тап и
    показывает результат, а судит право, пишет журнал и базу уроков ДВЕРЬ. Имя автора едет из
    `from_user` Telegram — выдумать его здесь нельзя, а без имени право fail-closed откажет, и это
    верно: вердикт без автора непроверяем.

    `stdin_text` — урок владельца СВОИМИ СЛОВАМИ. Едет через stdin, а не через argv: это живая
    кириллица произвольной длины, а argv на Windows ходит через кодировку консоли и коверкает её
    молча. Заодно принцип «из Telegram в командную строку едет только число» остаётся целым — текст
    в командной строке не появляется ни разу."""
    args = ["--own-text"] if stdin_text is not None else exam_args(action, case)
    if args is None:
        return f"экзамен, кейс {case}: не понял кнопку ({action})."
    if live:
        args = ["--live"] + args
    if not VENV_PY.exists():
        return f"экзамен, кейс {case}: не нашёл python venv ({VENV_PY})."
    try:
        r = subprocess.run(
            [str(VENV_PY), str(REPO_DIR / "exam_show.py")] + args + ["--who", str(who or "")],
            input=stdin_text, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, cwd=str(REPO_DIR), creationflags=NO_WINDOW,
        )
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return out or (f"экзамен, кейс {case}: пустой ответ двери на «{action}». "
                       f"Запасной путь — {EXAM_ANSWER_AT}.")
    except Exception as e:
        return (f"экзамен, кейс {case}: «{action}» НЕ записано ({type(e).__name__}: {e}). "
                f"Запасной путь — {EXAM_ANSWER_AT}.")


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
    box = _box_cb_parse(data)
    exam = _exam_cb_parse(data)
    exam_live = _exam_live_cb_parse(data)
    if not _chain_cb_authorized(uid):
        # owner-gate раньше разбора: чужому не подсказываем формат кнопок.
        return {"ok": False, "kind": None, "action": None, "pid": None,
                "answer": "⛔ нет прав", "alert": True, "note": None}
    if box is not None:
        # Кнопка СНЯТИЯ ОСТАНОВКИ ЯЩИКА. Тост говорит «снимаю», а не «ящик поехал»:
        # взятие произойдёт следующим витком ящика, и обещать его немедленно значило
        # бы соврать о сроке. Что именно снято — скажет ответ самого ящика.
        action, mark = box
        return {"ok": True, "kind": "box", "action": action, "pid": mark,
                "answer": "🔓 снимаю остановку…", "alert": False, "note": None}
    if exam is not None or exam_live is not None:
        # Кнопки РАБОЧЕГО МЕСТА ЭКЗАМЕНА. Тост обещает РОВНО ТО, что произойдёт, и у пяти кнопок
        # он разный намеренно: у номера последствий нет вовсе (тумблер), у «✔ Применить» они
        # необратимы в одну сторону (урок начинает действовать на всех клиентов), у вердикта
        # последствие — строка журнала. Один общий тост на пять кнопок обещал бы владельцу не то,
        # что он нажал, раньше, чем он отпустит палец.
        # Живой набор (18.09.2026) — свой вид `exam_live`: его исполнитель зовёт дверь с `--live`,
        # а тост называет набор, потому что уроки там правилом бота не становятся — их базу голова
        # не читает. О судьбе самого ТЕКСТА тост не судит ни словом: комнату он не наблюдает, а
        # написанное в ней голова читает как реплику клиента (замер 18.09 23:13, разбор 19.09).
        action, case = exam if exam is not None else exam_live
        said = {"ok": "✅ записываю «верно»…", "no": "❌ записываю «неверно»…",
                "go": "🎓 записываю уроки и вердикт…",
                "own": "✍️ слушаю: следующим сообщением — правило…"}.get(action)
        said = said or ("☑️ отмечаю подсказку %s…" % action[1:])
        if exam is None:
            said = "живой набор: " + said
        return {"ok": True, "kind": "exam" if exam is not None else "exam_live",
                "action": action, "pid": case, "answer": said, "alert": False, "note": None}
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
        # Наш бот (AGENT_BOT_TOKEN) шлёт ТОЛЬКО карточки цепи, заявок, ворот, ящика и экзамена
        # (пять видов, шестого нет) → неразобранный
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
                    answer_at=(f"если это была карточка ворот — {GATE_ANSWER_AT}; если карточка "
                               f"ОСТАНОВКИ ЯЩИКА — {BOX_ANSWER_AT}"),
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
    # Имя автора нужно ровно одной двери — экзамену (вердикт без автора непроверяем), поэтому
    # оно подмешивается замыканием, а не третьим аргументом всем четырём: лишний параметр у
    # `_chain_cli`/`_gate_cli` означал бы, что имя тапнувшего им зачем-то нужно.
    # `getattr`, а не `.username`: у пользователя Telegram ника может не быть вовсе (поле
    # необязательное), и падение здесь уронило бы обработчик ВСЕХ кнопок, а не одной нашей.
    who = getattr(q.from_user, "username", "") or ""
    runner = {"zayavka": _zayavka_cli, "gate": _gate_cli, "box": _box_cli,
              "exam": (lambda a, p: _exam_cli(a, p, who)),
              "exam_live": (lambda a, p: _exam_cli(a, p, who, live=True))}.get(
                  route.get("kind"), _chain_cli)
    reply = await asyncio.to_thread(runner, route["action"], route["pid"])
    await _chain_reply(context, q, reply)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if msg is None or chat is None:
        return

    # --- гейт доступа ДО любой команды ---
    # 0) ГРУППА-ТРЕНАЖЁР: ровно одна ветка и только при ОБЪЯВЛЕННОМ ожидании «✍️ своё».
    # Ставится она ДО гейта темы 205 по единственной причине: карточка экзамена висит в другом
    # чате, и ответ владельца на неё приходит оттуда. Узость ветки — по построению: чужой чат,
    # чужой человек и отсутствие живого ожидания дают ровно то же молчание, что и раньше, а сам
    # текст берётся ДОСЛОВНО (не нижним регистром и без срезанного «/»): это правило владельца, и
    # править его нам нечем.
    if chat.id == EXAM_CHAT_ID:
        if (user.id if user else None) != ALLOWED_USER_ID:
            return
        pending = await asyncio.to_thread(_exam_pending_set)
        if pending is None:
            return
        raw = msg.text or ""
        alog.info("экзамен (%s): ловлю урок своими словами (%d симв.)", pending[0], len(raw))
        who = getattr(user, "username", "") or ""
        reply = await asyncio.to_thread(_exam_cli, "own", "", who, raw,
                                        pending[0] == EXAM_SET_LIVE)
        await _send(context, chat.id, reply)
        return
    # 1) только наш чат и только тема 205 — прочее молча игнорим
    if chat.id != HQ_CHAT_ID or msg.message_thread_id != HQ_THREAD_ID:
        return
    text = (msg.text or "").strip().lstrip("/").lower()
    chat_id = chat.id
    # 2) только разрешённый пользователь
    uid = user.id if user else None
    if uid != ALLOWED_USER_ID:
        alog.warning(f"отклонено: id {uid}")
        # МОЛЧАНИЕ ЧУЖОМУ ОСТАЁТСЯ ПРАВИЛОМ — кроме ОДНОЙ команды (06.09.2026).
        # Снятие сигнальной остановки ящика владелец делает с телефона по карточке, и
        # «ответил, а полоса стои́т» без единого слова в ответ он прочитает как поломку
        # доставки, а не как отказ в праве. Условие задания дословно: «кто не вправе —
        # внятный отказ, не тишина». Ветка узкая по построению: она отвечает только на
        # текст, ТОЧНО совпавший с формой снятия, и только в теме 205 нашего чата, —
        # то есть болтливее агент не стал ни на одно постороннее сообщение.
        if box_word_mark(text):
            await _send(context, chat_id,
                        "⛔ Нет прав: сигнальную остановку ящика снимает ТОЛЬКО владелец. "
                        "Ничего не снято, ящик держится.")
        return

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

        elif box_word_mark(text):
            # СЛОВЕСНЫЙ ПУТЬ СНЯТИЯ — та же дверь, что у кнопки (`_box_cli`), и это
            # обязательно: два разбора одного ответа разошлись бы молча, и владелец
            # получал бы на слово и на тап разные исходы одного решения.
            mark = box_word_mark(text)
            alog.info("команда: снять остановку ящика, метка %s", mark)
            await _send(context, chat_id, await asyncio.to_thread(_box_cli, "free", mark))

        elif lesson_word(msg.text):
            # ПРИЧИНА БЕРЁТСЯ ИЗ ОРИГИНАЛА (`msg.text`), а не из `text`: тот приведён к нижнему
            # регистру для сверки команд, и причина уехала бы в таблицу уроков строчной навсегда.
            act, num, why, source = lesson_word(msg.text)
            author = (user.username or "").strip() if user else ""
            alog.info("команда урока: %s #%s набор %r от @%s", act, num, source, author or "?")
            await _send(context, chat_id,
                        await asyncio.to_thread(_lesson_cli, act, num, why, author, source))

        elif LESSON_HEAD_RE.match(text):
            # ПОЧТИ КОМАНДА — ЭТО РЕШЕНИЕ ВЛАДЕЛЬЦА, А НЕ МУСОР (тот же довод, что у ящика):
            # общая подсказка «не знаю «урок …»» здесь врёт — команду мы знаем, не разобрался хвост.
            alog.info("движение уроком: хвост не разобран в %r", (msg.text or "")[:120])
            await _send(context, chat_id,
                        "⚠️ Не разобрал команду урока. Формы по ОДНОМУ уроку: «урок включи 7: "
                        "причина словами» · «урок откати 7» · «урок кандидаты» · «урок след». "
                        "Формы по НАБОРУ: «урок наборы» · «урок набор <ключ>» · «урок набор сними "
                        "<ключ> <сколько строк>: причина словами» · «урок набор верни <ключ>». "
                        "Номер — из карточки записи кандидата, ключ и число — из «урок набор "
                        "<ключ>». Ничего не изменено.")

        elif BOX_WORD_HEAD_RE.match(text):
            # ПОЧТИ КОМАНДА — ЭТО ОТВЕТ ВЛАДЕЛЬЦА, А НЕ МУСОР. Общая подсказка
            # («не знаю «ящик снять …»») здесь врёт: команду мы знаем, не разобралась
            # МЕТКА. Молча свести это к «не знаю» значило бы потерять ответ человека.
            alog.info("снятие остановки: метка не разобрана в %r", (msg.text or "")[:120])
            await _send(context, chat_id,
                        "⚠️ Метку не разобрал: она шестнадцатеричная, 6–32 знака, и стои́т в самом "
                        "извещении об остановке — скопируй её целиком. Ничего не снято.")

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
