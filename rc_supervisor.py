# -*- coding: utf-8 -*-
"""
rc_supervisor.py — вечный сеанс Claude Code Remote Control на ПК (ОСНОВНОЙ канал владельца,
Termux — в резерве). Поднимается задачей Планировщика `TurboBabyRC` при входе в систему
(шаблон задачи — pc_remote_control.task.xml; имя задаётся ключом /TN при установке).

ПОЧЕМУ ОБЁРТКА, А НЕ ПРЯМОЙ ЗАПУСК claude ИЗ ПЛАНИРОВЩИКА (как у pc_agent):
  1) remote-control — это ФЛАГ ИНТЕРАКТИВНОЙ сессии (`claude --remote-control [имя]`), а не
     headless-подкоманда: ей нужен ЖИВОЙ консольный TTY. Поэтому задача зовёт wscript →
     rc_remote_control.vbs (WshShell.Run …, 0): консоль создаётся, ОКНА НЕ ВИДНО (класс
     «мигающие чёрные окна» 22.07 не воскрешаем). pythonw/`CREATE_NO_WINDOW` тут НЕ годятся —
     они убивают саму консоль, а с ней и TTY интерактивной сессии.
  2) RestartOnFailure Планировщика даёт всего 3 попытки, а канал обязан жить ВСЕГДА → рестарт
     живёт здесь: бесконечный цикл с паузой RESTART_DELAY после каждого выхода сессии.
  3) Путь к claude.exe версионный (AppData\\Roaming\\Claude\\claude-code\\<версия>\\claude.exe) и
     протухает на КАЖДОМ автообновлении CLI — резолвим новейшую установку ПЕРЕД КАЖДЫМ стартом
     (зеркало resolve_claude() демона, класс-фикс WinError 2).

Синглтон: именованный мьютекс Windows — вторая копия (ручной запуск поверх задачи) молча
выходит, чтобы не регистрировать второе устройство в кабинете.

Секреты не пишем и не логируем: в rc_remote_control.log идут только время/PID/код выхода.
"""

import os
import re
import sys
import glob
import time
import shutil
import logging
import subprocess

REPO = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(REPO, "rc_remote_control.log")
SESSION_NAME = os.getenv("RC_SESSION_NAME", "turbobaby-pc")
RESTART_DELAY = int(os.getenv("RC_RESTART_DELAY", "15") or "15")   # сек между выходом и подъёмом
MUTEX_NAME = "Global\\turbobaby_rc_supervisor"
# Диагностика канала. Сессию поднимает ПЛАНИРОВЩИК, её stdout/stderr перехватить нельзя:
# любое перенаправление убивает TTY, без которого интерактивная сессия не живёт. Поэтому
# просим сам CLI писать отладку в ФАЙЛ (--debug-file) — TTY цел, диагностика есть.
# Включатель — файл-флаг rc_debug.flag рядом с супервизором (env для задачи Планировщика
# без прав администратора не задать, а флаг кладётся обычным пользователем).
DEBUG_FLAG = os.path.join(REPO, "rc_debug.flag")
DEBUG_LOG = os.path.join(REPO, "rc_session_debug.log")   # под *.log в .gitignore — в репо не уедет

log = logging.getLogger("rc_supervisor")
log.setLevel(logging.INFO)
log.propagate = False
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
    """Путь к CLI claude: 1) PATH-шим (учитывает PATHEXT), 2) новейшая версионная установка,
    3) кандидаты нативного/npm-инсталлера. → str | None (зеркало resolve_claude демона)."""
    _which = which or shutil.which
    _isf = isfile or os.path.isfile
    shim = _which("claude")
    if shim:
        return shim
    exe = (newest or newest_versioned_claude)()
    if exe:
        return exe
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude.cmd"),
              os.path.join(local, "Programs", "claude", "claude.exe"),
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


def debug_args(exists=None):
    """Аргументы отладки сессии: есть файл-флаг rc_debug.flag → ['--debug-file', <лог>], иначе [].
    Путь можно переопределить env RC_DEBUG_FILE. exists — инъекция для тестов."""
    _exists = exists or os.path.exists
    if not _exists(DEBUG_FLAG):
        return []
    return ["--debug-file", os.getenv("RC_DEBUG_FILE") or DEBUG_LOG]


def run_once(claude, runner=None, name=SESSION_NAME, dbg=None):
    """Один прогон интерактивной сессии Remote Control. Консоль/stdin/stdout НЕ перенаправляем
    (иначе TTY исчезнет и интерактивная сессия не поднимется). → код выхода."""
    cmd = [claude, "--remote-control", name] + (debug_args() if dbg is None else dbg)
    p = (runner or subprocess.run)(cmd, cwd=REPO)
    return getattr(p, "returncode", 0)


def main(resolver=None, runner=None, sleeper=None, rounds=None, singleton=None):
    """Вечный цикл: резолв claude → сессия → пауза → снова. rounds — ограничитель для тестов
    (None = бесконечно), singleton — инъекция гарда-синглтона (иначе тесты зависели бы от того,
    жив ли РЕАЛЬНЫЙ супервизор на этой машине: мьютекс-то один на всю ОС)."""
    ok, _handle = (singleton or acquire_singleton)()
    if not ok:
        log.info("супервизор уже запущен (мьютекс занят) — вторая копия выходит")
        return 0
    _resolve = resolver or resolve_claude
    _sleep = sleeper or time.sleep
    log.info("супервизор стартовал (pid=%s, сессия='%s', cwd=%s)", os.getpid(), SESSION_NAME, REPO)
    n = 0
    while rounds is None or n < rounds:
        n += 1
        claude = _resolve()
        if not claude:
            log.error("claude CLI НЕ НАЙДЕН (ни PATH, ни claude-code\\<версия>, ни кандидаты) — "
                      "жду %s с", RESTART_DELAY)
            _sleep(RESTART_DELAY)
            continue
        log.info("старт сессии: %s --remote-control %s", claude, SESSION_NAME)
        try:
            rc = run_once(claude, runner=runner)
            log.info("сессия завершилась (exit=%s) — рестарт через %s с", rc, RESTART_DELAY)
        except Exception as e:
            log.error("сессия упала (%s) — рестарт через %s с", type(e).__name__, RESTART_DELAY)
        _sleep(RESTART_DELAY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
