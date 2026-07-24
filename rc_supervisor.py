# -*- coding: utf-8 -*-
"""
rc_supervisor.py — вечный ДВУХРЕЖИМНЫЙ сеанс Claude Code Remote Control на ПК (ОСНОВНОЙ канал
владельца, Termux — в резерве). Поднимается задачей Планировщика `TurboBabyRC` при входе в
систему (шаблон задачи — pc_remote_control.task.xml; имя задаётся ключом /TN при установке).

ДВЕ ВЕТКИ, ДВА ЖИВЫХ ПРОЦЕССА (каждая — свой поток-демон, свой вечный цикл, свой рестарт):
  • «rc-server»     — `claude rc` (алиас `remote-control`, «persistent server», same-dir,
                      capacity 32): регистрирует Environment машины и держит его в poll-loop —
                      ЭТО и есть постоянная запись в списке Devices/Environments приложения;
  • «named-channel» — `claude --remote-control <имя>` («interactive session»): именованная
                      сессия, через которую с телефона поднимаются удалённые сессии.
Обе ветки эмпирически СОСУЩЕСТВУЮТ в одном каталоге без конфликта синглтона (живой факт 24.07:
сервер + именованная сессия работали одновременно). Падение/рестарт одной НЕ трогает другую.

ПОЧЕМУ ОБЁРТКА, А НЕ ПРЯМОЙ ЗАПУСК claude ИЗ ПЛАНИРОВЩИКА (как у pc_agent):
  1) remote-control — это ФЛАГ ИНТЕРАКТИВНОЙ сессии (`claude --remote-control [имя]`), а не
     headless-подкоманда: ей нужен ЖИВОЙ консольный TTY. Поэтому задача зовёт wscript →
     rc_remote_control.vbs (WshShell.Run …, 0): консоль создаётся, ОКНА НЕ ВИДНО (класс
     «мигающие чёрные окна» 22.07 не воскрешаем). pythonw/`CREATE_NO_WINDOW` тут НЕ годятся —
     они убивают саму консоль, а с ней и TTY. Оба процесса-ветки наследуют ЭТУ скрытую консоль
     (Popen без redirect и без creationflags), поэтому TTY есть у обоих.
  2) RestartOnFailure Планировщика даёт всего 3 попытки, а канал обязан жить ВСЕГДА → рестарт
     живёт здесь: у каждой ветки бесконечный цикл с паузой RESTART_DELAY после каждого выхода.
  3) Путь к claude.exe версионный (AppData\\Roaming\\Claude\\claude-code\\<версия>\\claude.exe) и
     протухает на КАЖДОМ автообновлении CLI — резолвим ПЕРЕД КАЖДЫМ стартом через resolve_claude()
     (предпочтение ВЕЧНОГО нативного шима ~/.local/bin/claude.exe; класс-фикс WinError 2).

ПРОБА ЖИВОСТИ (класс «0 TCP ≠ зомби»): рестарт не только по ВЫХОДУ процесса, но и по «живой
процесс при мёртвом канале». Судить по одному числу TCP НЕЛЬЗЯ — доказано, что и живой канал
приходит к 0 соединений в простое. Единый предикат channel_alive: канал жив, если процесс
НЕДАВНО писал в свой --debug-file (свежесть лога) ЛИБО держит установленные соединения; мёртв —
только когда НЕТ обоих. Порог свежести берём с ЗАПАСОМ над реальными интервалами (лог сервера
пишется вехами ~раз в 10 мин, соединения именованной сессии тают ~за 15 мин) → дефолт 20 мин.

Синглтон: именованный мьютекс Windows — вторая копия СУПЕРВИЗОРА (ручной запуск поверх задачи)
молча выходит, чтобы не плодить дубли обеих веток.

Секреты не пишем и не логируем: в rc_remote_control.log идут только время/PID/код выхода;
per-branch --debug-file пишет сам CLI (секреты он редактирует как [REDACTED]), файлы под *.log.
"""

import os
import re
import sys
import glob
import time
import shutil
import logging
import threading
import subprocess

REPO = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(REPO, "rc_remote_control.log")
SESSION_NAME = os.getenv("RC_SESSION_NAME", "turbobaby-pc")
RESTART_DELAY = int(os.getenv("RC_RESTART_DELAY", "15") or "15")   # сек между выходом и подъёмом
# Пауза, когда канал ЗАБЛОКИРОВАН и чинится только руками владельца (нет пригодного входа).
# Отдельная и длинная: ждать 15 с бессмысленно — состояние не изменится само, а лог за ночь
# распухнет от одинаковых строк (живой замер: ~4 строки в минуту).
NOT_READY_DELAY = int(os.getenv("RC_NOT_READY_DELAY", "300") or "300")
MUTEX_NAME = "Global\\turbobaby_rc_supervisor"
# Диагностика канала. Сессию поднимает ПЛАНИРОВЩИК, её stdout/stderr перехватить нельзя:
# любое перенаправление убивает TTY, без которого интерактивная сессия не живёт. Поэтому
# просим сам CLI писать отладку в ФАЙЛ (--debug-file) — TTY цел, диагностика есть.
# Включатель — файл-флаг rc_debug.flag рядом с супервизором (env для задачи Планировщика
# без прав администратора не задать, а флаг кладётся обычным пользователем).
DEBUG_FLAG = os.path.join(REPO, "rc_debug.flag")

# Пер-branch --debug-file. Пишутся ВСЕГДА (не только по флагу): их свежесть — сигнал живости
# канала для probe_alive, а не просто отладка. Оба под *.log в .gitignore — в репо не уедут.
SERVER_LOG = os.path.join(REPO, "rc_server_debug.log")    # ветка `claude rc`
NAMED_LOG = os.path.join(REPO, "rc_session_debug.log")    # ветка `claude --remote-control <имя>`

# --- Проба живости (класс «0 TCP ≠ зомби») ------------------------------------------------
# Интервал опроса монитора: и выход процесса, и зомби-проверка. Держим коротким для быстрого
# рестарта после падения, netstat-опрос раз в интервал дешёв.
CHECK_INTERVAL = int(os.getenv("RC_CHECK_INTERVAL", "30") or "30")
# Порог свежести лога. С ЗАПАСОМ над живыми интервалами (лог `rc` пишется вехами ~раз в 10 мин:
# poll #1 в 09:03:23 → poll #100 в 09:12:44; соединения именованной сессии тают ~за 15 мин).
# 20 мин перекрывает оба → простаивающий-но-живой процесс не будет ошибочно убит. Ценой того,
# что настоящий зомби ловится за ≤20 мин (редкий отказ — медленный рестарт допустим).
LIVENESS_MAX_AGE = int(os.getenv("RC_LIVENESS_MAX_LOG_AGE", "1200") or "1200")
# Грейс после старта: первые GRACE_SECONDS зомби-проверку НЕ гоняем (даём процессу зайти в
# poll-loop, записать init-строки и открыть соединение). Выход процесса при этом ловится сразу.
GRACE_SECONDS = int(os.getenv("RC_LIVENESS_GRACE", "180") or "180")
# Запасной клапан: RC_LIVENESS=0 полностью выключает зомби-киллер (откат к «рестарт лишь по
# выходу процесса» — прежнее поведение) на случай, если проба в бою окажется ложно-срабатывающей.
LIVENESS_ENABLED = os.getenv("RC_LIVENESS", "1") != "0"

# Две ветки супервизора. `mode` — аргументы claude ПОСЛЕ бинаря и ДО --debug-file:
#   rc-server:     ['rc']                          → постоянный сервер-Environment (устройство)
#   named-channel: ['--remote-control', <имя>]     → именованная интерактивная сессия
SERVER_SPEC = {"label": "rc-server", "mode": ("rc",), "log": SERVER_LOG}
NAMED_SPEC = {"label": "named-channel", "mode": ("--remote-control", SESSION_NAME), "log": NAMED_LOG}
BRANCHES = (SERVER_SPEC, NAMED_SPEC)

log = logging.getLogger("rc_supervisor")
log.setLevel(logging.INFO)
log.propagate = False
try:
    # Ротация обязательна: супервизор пишет строку на КАЖДЫЙ круг вечного цикла (живой замер —
    # ~4 строки в минуту при заблокированном канале), без ротации файл растёт до бесконечности.
    import log_setup
    _h = log_setup.rotating_handler(LOG_PATH)
    if _h is not None:
        log.addHandler(_h)
except Exception:
    try:
        _h = logging.FileHandler(LOG_PATH, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        log.addHandler(_h)
    except Exception:
        pass


def _ver_key(name):
    """Ключ сортировки версии '2.1.217' → (2,1,217); нечисловое → (0,). Зеркало _ver_key демона:
    сортировать версии СТРОКОЙ нельзя ('2.1.99' > '2.1.217' лексикографически), и mtime каталога
    тоже врёт — у соседних версий он совпадает (живой факт 22.07: 2.1.215 и 2.1.217 с одним
    LastWriteTime → выбор «новейшей» по времени отдал СТАРУЮ 2.1.215)."""
    nums = re.findall(r"\d+", name or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def claude_base_dirs():
    """Базы версий claude-code. Кроме обычной Roaming\\Claude\\claude-code обязательно РЕАЛЬНАЯ
    MSIX-база AppData\\Local\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude\\claude-code: у
    Store/MSIX-установки Roaming\\Claude — виртуальный редирект, видимый ТОЛЬКО в интерактивной
    сессии, а НАС поднимает Планировщик (тот же класс, что чинил демон: «не найден в бою»)."""
    home = os.path.expanduser("~")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    local = os.getenv("LOCALAPPDATA") or os.path.join(os.getenv("USERPROFILE") or home,
                                                      "AppData", "Local")
    out = [os.path.join(appdata, "Claude", "claude-code")]
    try:
        for cc in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache",
                                         "Roaming", "Claude", "claude-code")):
            if cc not in out:
                out.append(cc)
    except Exception:
        pass
    return out


def newest_versioned_claude(bases=None, globber=None, isfile=None):
    """Новейшая версионная установка claude-code → путь к claude.exe (или None).
    «Новейшая» — по ЧИСЛОВОМУ ключу версии (_ver_key). globber/isfile — инъекция для тестов."""
    bases = bases if bases is not None else claude_base_dirs()
    _glob = globber or glob.glob
    _isf = isfile or os.path.isfile
    cands = []
    try:
        for base in bases:
            for d in _glob(os.path.join(base, "*")):
                exe = os.path.join(d, "claude.exe")
                if _isf(exe):
                    cands.append((_ver_key(os.path.basename(d)), exe))
    except Exception:
        return None
    if not cands:
        return None
    cands.sort()
    return cands[-1][1]


def resolve_claude(which=None, isfile=None, newest=None):
    """Путь к CLI claude, по убыванию предпочтения:
      1) PATH-шим (учитывает PATHEXT);
      2) ВЕЧНЫЙ нативный шим ~/.local/bin/claude.exe(.cmd) — путь НЕ версионный, его держит
         свежим сам нативный автообновлятор; предпочитаем его протухающему версионному
         Desktop/MSIX-каталогу (класс «CLAUDE_BIN стухшая версия»: версионный путь
         протухает на КАЖДОМ апдейте CLI и исчезает — WinError 2, — а шим остаётся);
      3) новейшая версионная установка (Desktop/MSIX) — фолбэк, если нативного шима нет;
      4) прочие кандидаты нативного/npm-инсталлера.
    → str | None (зеркало resolve_claude демона). which/isfile/newest — инъекция для тестов."""
    _which = which or shutil.which
    _isf = isfile or os.path.isfile
    shim = _which("claude")
    if shim:
        return shim
    home = os.path.expanduser("~")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude.cmd")):
        if _isf(c):
            return c
    exe = (newest or newest_versioned_claude)()
    if exe:
        return exe
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    for c in (os.path.join(local, "Programs", "claude", "claude.exe"),
              os.path.join(local, "Programs", "claude-code", "claude.exe"),
              os.path.join(appdata, "npm", "claude.cmd")):
        if _isf(c):
            return c
    return None


def acquire_singleton(name=MUTEX_NAME):
    """Именованный мьютекс Windows → (ok, handle). ok=False — супервизор уже жив (вторая копия
    молча выходит). Не-Windows / ctypes недоступен → (True, None): гард не обязателен."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return True, None
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = wintypes.HANDLE
        handle = k32.CreateMutexW(None, wintypes.BOOL(True), name)
        if not handle:
            return True, None                      # создать не смогли — гард не мешает работе
        if k32.GetLastError() == 183:              # ERROR_ALREADY_EXISTS
            return False, handle
        return True, handle
    except Exception:
        return True, None


# Маркеры НЕПРИГОДНОГО для Remote Control входа — дословно из штатного `claude doctor`.
# Живой прокол 22.07: `claude auth status` бодро отвечал loggedIn=true / max, а мост при этом
# не поднимался НИКОГДА, потому что вход был выдан без скоупа user:profile. Сессия при этом не
# падала и не ругалась — просто жила пустым процессом. Проверять надо ИМЕННО doctor.
RC_BLOCKERS = (
    "Remote Control requires",
    "Not signed in to claude.ai",
    "missing the user:profile scope",
    "subscription auth not active",
)


def rc_ready(claude, runner=None, timeout=120):
    """Готов ли контур поднять мост Remote Control → (ok, detail).

    FAIL-OPEN: если сам doctor не запустился/завис — НЕ блокируем канал (гард обязан ловить
    известную поломку, а не становиться новой точкой отказа)."""
    try:
        p = (runner or subprocess.run)([claude, "doctor"], capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=timeout, cwd=REPO)
    except Exception as e:
        return True, "doctor не отработал (%s) — не блокируем" % type(e).__name__
    out = (getattr(p, "stdout", "") or "") + (getattr(p, "stderr", "") or "")
    hits = [m for m in RC_BLOCKERS if m in out]
    if hits:
        return False, "; ".join(hits)
    return True, "doctor: препятствий для Remote Control нет"


def notify_owner(text, runner=None):
    """Карточка владельцу через существующий dispatch_notify (тема 1160, личка — фолбэк).
    Fire-and-forget: канал уже сломан, уведомление не смеет сломать ещё и супервизор."""
    py = os.path.join(REPO, "venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    try:
        (runner or subprocess.run)([py, os.path.join(REPO, "dispatch_notify.py"),
                                    "--critical", text],
                                   capture_output=True, timeout=30, cwd=REPO)
        return True
    except Exception as e:
        log.warning("карточка владельцу не ушла (%s)", type(e).__name__)
        return False


def build_cmd(claude, spec, verbose=False):
    """Командная строка ветки: claude + режим ветки + --debug-file <лог ветки> (+ --verbose).
    --debug-file пишем ВСЕГДА: его свежесть — сигнал живости канала для probe_alive, а не только
    отладка. --verbose (детальнее лог) — по файлу-флагу rc_debug.flag. stdio НЕ трогаем."""
    cmd = [claude] + list(spec["mode"]) + ["--debug-file", spec["log"]]
    if verbose:
        cmd += ["--verbose"]
    return cmd


def log_age(path, now=None, getmtime=None):
    """Возраст (сек) последней записи лога ветки; файла нет/ошибка → None (трактуем как «не
    свежий»). now/getmtime — инъекция для тестов."""
    _getm = getmtime or os.path.getmtime
    try:
        m = _getm(path)
    except Exception:
        return None
    _now = now if now is not None else time.time()
    return max(0.0, _now - m)


def established_conns(pid, runner=None):
    """Число УСТАНОВЛЕННЫХ TCP-соединений процесса pid через netstat -ano (psutil в venv нет).
    Ошибка/таймаут → 0: ноль сам по себе не приговор — решает channel_alive в паре со свежестью
    лога. runner — инъекция для тестов."""
    try:
        p = (runner or subprocess.run)(["netstat", "-ano", "-p", "TCP"],
                                       capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=15)
    except Exception:
        return 0
    spid = str(pid)
    cnt = 0
    for line in (getattr(p, "stdout", "") or "").splitlines():
        parts = line.split()
        # живой формат: 'TCP  <local>  <foreign>  ESTABLISHED  <pid>' (LISTENING/иные — мимо)
        if (len(parts) >= 5 and parts[0].upper() == "TCP"
                and parts[3].upper() == "ESTABLISHED" and parts[-1] == spid):
            cnt += 1
    return cnt


def channel_alive(age_sec, established, max_age=None):
    """ЕДИНЫЙ предикат живости канала (НЕ по одному числу TCP — доказано: и живой канал приходит
    к 0 соединений в простое). Канал ЖИВ, если процесс НЕДАВНО писал в свой лог
    (age_sec <= max_age) ИЛИ держит хотя бы одно установленное соединение. МЁРТВ — только когда
    НЕТ ОБОИХ: лог протух И соединений ноль. Так простаивающий-но-живой (свежий лог при 0 TCP,
    либо старый лог, но есть соединение) НЕ считается мёртвым, а реально застрявший (старый лог
    И 0 TCP) — считается. age_sec=None (лога ещё нет) = «не свежий»."""
    _max = LIVENESS_MAX_AGE if max_age is None else max_age
    log_fresh = age_sec is not None and age_sec <= _max
    return bool(log_fresh or (established or 0) > 0)


def probe_alive(spec, pid, now=None, getmtime=None, conns=None, max_age=None):
    """Живость канала ветки: свежесть её --debug-file + установленные соединения pid → предикат
    channel_alive. Инъекции now/getmtime/conns — для тестов без файловой системы и netstat."""
    age = log_age(spec["log"], now=now, getmtime=getmtime)
    est = (conns or established_conns)(pid)
    return channel_alive(age, est, max_age=max_age)


def default_spawn(claude, spec, verbose=None, popen=None):
    """Поднять процесс ветки. Сначала УСЕКАЕМ лог живости (свежий mtime = чистая точка отсчёта на
    новую жизнь процесса), затем Popen БЕЗ перенаправления stdio и БЕЗ creationflags: интерактивной
    сессии нужен живой TTY скрытой консоли wscript (redirect/CREATE_NO_WINDOW его убивают — классы
    «мёртвый TTY» / «мигающие чёрные окна»). verbose=None → берём по файлу-флагу. popen —
    инъекция для тестов. → объект процесса (.poll/.pid/.terminate) | None."""
    try:
        open(spec["log"], "w", encoding="utf-8").close()
    except Exception:
        pass
    vb = os.path.exists(DEBUG_FLAG) if verbose is None else verbose
    try:
        return (popen or subprocess.Popen)(build_cmd(claude, spec, verbose=vb), cwd=REPO)
    except Exception as e:
        log.error("[%s] процесс не поднялся (%s)", spec["label"], type(e).__name__)
        return None


def default_kill(proc):
    """Мягко погасить зомби-процесс ветки: terminate → wait → в крайнем случае kill."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    except Exception:
        pass


_notify_lock = threading.Lock()
_notify_ts = [0.0]   # метка последней карточки владельцу: дедуп между ДВУМЯ потоками-ветками


def default_gate(claude, ready=None, notifier=None, now=None):
    """Пре-флайт ветки перед подъёмом процесса: doctor (rc_ready). Если вход непригоден — сессия
    НЕ падает, а живёт пустым процессом (живой прокол 22.07), поэтому НЕ поднимаем и шлём карточку
    владельцу — но НЕ ЧАЩЕ раза в NOT_READY_DELAY на ОБЕ ветки сразу (иначе два потока шлют дубли).
    → (ok, detail). now — инъекция для тестов."""
    ok, detail = (ready or rc_ready)(claude)
    if ok:
        return True, detail
    _now = now if now is not None else time.time()
    with _notify_lock:
        if _now - _notify_ts[0] >= NOT_READY_DELAY:
            _notify_ts[0] = _now
            (notifier or notify_owner)(
                "⚠️ Канал Remote Control на ПК не поднимается: " + detail +
                ". Нужен вход руками: открой обычное окно терминала и выполни "
                "`claude auth login` (аккаунт claude.ai), затем разреши Remote Control. "
                "Супервизор ждёт и поднимет канал сам, как только вход станет пригодным.")
    return False, detail


def supervise_branch(spec, resolver=None, spawner=None, alive=None, gate=None,
                     sleeper=None, killer=None, rounds=None, checks=None, grace_checks=None):
    """Вечный НЕЗАВИСИМЫЙ цикл ОДНОЙ ветки (свой поток): резолв шима → пре-флайт → подъём процесса
    → монитор → пауза → снова. Монитор рестартует по ДВУМ причинам: процесс ВЫШЕЛ сам (poll!=None)
    ЛИБО зомби — процесс жив, но канал мёртв по probe_alive (после грейс-периода, если проба не
    выключена RC_LIVENESS=0). rounds/checks/grace_checks — ограничители/инъекции для тестов
    (None = боевой бесконечный режим). → 0."""
    _resolve = resolver or resolve_claude
    _spawn = spawner or default_spawn
    _alive = alive or probe_alive
    _gate = gate or default_gate
    _sleep = sleeper or time.sleep
    _kill = killer or default_kill
    _grace = max(1, GRACE_SECONDS // max(1, CHECK_INTERVAL)) if grace_checks is None else grace_checks
    n = 0
    while rounds is None or n < rounds:
        n += 1
        claude = _resolve()
        if not claude:
            log.error("[%s] claude CLI НЕ НАЙДЕН (ни PATH, ни шим, ни версия) — жду %s с",
                      spec["label"], RESTART_DELAY)
            _sleep(RESTART_DELAY)
            continue
        ok_rc, detail = _gate(claude)
        if not ok_rc:
            log.error("[%s] Remote Control НЕДОСТУПЕН: %s — процесс НЕ поднимаю (иначе живой "
                      "процесс при мёртвом канале); жду %s с", spec["label"], detail, NOT_READY_DELAY)
            _sleep(NOT_READY_DELAY)
            continue
        proc = _spawn(claude, spec)
        if proc is None:
            _sleep(RESTART_DELAY)
            continue
        log.info("[%s] старт pid=%s: %s %s", spec["label"], getattr(proc, "pid", "?"),
                 claude, " ".join(spec["mode"]))
        c = 0
        while checks is None or c < checks:
            c += 1
            _sleep(CHECK_INTERVAL)
            code = proc.poll()
            if code is not None:
                log.info("[%s] процесс вышел (exit=%s) — рестарт через %s с",
                         spec["label"], code, RESTART_DELAY)
                break
            # Зомби: процесс жив, но канал мёртв. Только ПОСЛЕ грейса и только если проба включена
            # (RC_LIVENESS=0 = запасной клапан, откат к «рестарт лишь по выходу процесса»).
            if LIVENESS_ENABLED and c > _grace and not _alive(spec, proc.pid):
                log.info("[%s] ЗОМБИ: лог протух (>%s с) И 0 соединений — гашу pid=%s, рестарт",
                         spec["label"], LIVENESS_MAX_AGE, getattr(proc, "pid", "?"))
                if proc.poll() is None:
                    _kill(proc)
                break
        _sleep(RESTART_DELAY)
    return 0


def main(singleton=None, branch_runner=None, branches=None):
    """Точка входа задачи Планировщика: один синглтон-супервизор поднимает ОБЕ ветки (сервер
    устройства + именованный канал) в отдельных потоках-демонах — у каждой свой вечный цикл и
    свой рестарт. Вторая копия СУПЕРВИЗОРА (ручной запуск поверх задачи) молча выходит по мьютексу.
    branch_runner/branches — инъекции для тестов (нити с реальным supervise_branch не гоняем)."""
    ok, _handle = (singleton or acquire_singleton)()
    if not ok:
        log.info("супервизор уже запущен (мьютекс занят) — вторая копия выходит")
        return 0
    _run = branch_runner or supervise_branch
    _br = branches if branches is not None else BRANCHES
    log.info("супервизор стартовал (pid=%s, ветки=%s, cwd=%s)",
             os.getpid(), [b["label"] for b in _br], REPO)
    threads = []
    for spec in _br:
        t = threading.Thread(target=_run, args=(spec,), name="rc-%s" % spec["label"], daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
