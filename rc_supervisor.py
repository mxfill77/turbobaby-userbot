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
только когда НЕТ обоих. Порог свежести — ПЕР-BRANCH (spec["max_age"]), потому что ветки пишут
лог СОВЕРШЕННО по-разному: сервер — вехами поллинга (разрыв в простое >20 мин, порог 3600с),
именованный канал — ОДНИМ всплеском на старте и больше никогда (порог 43200с). Общий
LIVENESS_MAX_AGE=1200с остался лишь дефолтом для ветки без своего ключа.

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
# Порог свежести ДЛЯ СЕРВЕРНОЙ ветки — шире общего 1200с. rc-server пишет веху лога раз в 100 поллов,
# и в простое интервал между вехами разрастается >20 мин (backoff поллинга), а poll-соединение кратко
# рвётся при реконнекте → общий предикат ложно приговаривал ЖИВОЙ простаивающий сервер и рестартовал
# его каждые 20–76 мин, плодя новую Environment на КАЖДЫЙ старт (churn мёртвых сред 24.07 — разбор в
# docs/artifacts/2026-07-24-rc-churn-liveness.md). Час перекрывает разрыв вех с запасом.
SERVER_LIVENESS_MAX_AGE = int(os.getenv("RC_SERVER_LIVENESS_MAX_LOG_AGE", "3600") or "3600")
# Порог свежести ДЛЯ ВЕТКИ КАНАЛА. Класс-фикс 24.07 закрыли ТОЛЬКО серверу, а зеркальную ветку
# оставили на общем 1200с — за последние сутки она погасила канал 29 раз против 1 у сервера
# (столько же и за календарный день 30.07; 27 владелец насчитал на более раннем срезе окна —
# за время работы окно сдвинулось на два цикла), и КАЖДЫЙ снос рвал живые сессии. Все числа ниже
# воспроизводятся `diag_rc_liveness_replay.py`; разбор — docs/artifacts/2026-07-30-rc-named-liveness.md.
# Порог обоснован ЧИСЛАМИ САМОЙ ВЕТКИ, а не скопирован с серверного:
#   • канал пишет в свой --debug-file РОВНО ОДИН раз за жизнь — всплеск на старте, и дальше НИ
#     БАЙТА: в отличие от сервера вех поллинга у него нет вообще (замер pid 13464: старт
#     20:41:34.623, последняя запись 20:41:35.814 — 70 строк за 904 мс; через 4 мин возраст лога
#     252.1с при возрасте процесса 253.3с, разница постоянная). То есть «свежесть лога» у этой
#     ветки — не heartbeat, а ОДНОРАЗОВАЯ отметка старта, и она протухает всегда;
#   • второе плечо (established>0) в простое тоже гаснет: это доказано самими гашениями — каждое
#     требует 0 соединений на 3 проверках подряд, и таких 358 за 6,2 суток;
#   • ⇒ при пороге 1200с смерть НАСТУПАЛА ДЕТЕРМИНИРОВАННО на 1290-й секунде (1200 + 3 страйка ×
#     30с): 333 жизни из 359 уложились в [1290; 1291) с = 21,5 мин при медиане 1290с, min 1200с;
#   • самый долгий молчок ЗДОРОВОГО канала: 9,23ч — жизнь 30.07 03:28:27→12:42:32 (33 245с)
#     пережила сон ПК и была срублена уже на пробуждении; вторая такая — 3,45ч (26.07
#     09:49:15→13:16:05, 12 410с). Обе без exit-строки, т.е. процесс всё это время был жив.
# 43200с (12ч) = запас ~30% над самым долгим наблюдённым здоровым молчком (33 245с), перекрывает
# ночной простой И типовой сон ПК (класс #171), а по-настоящему зависший канал всё равно
# срубается за 12ч + 90с. Быстрые отказы ловятся не этим порогом: выход процесса — за ≤30с,
# протухшие креды — детектором rc_auth_detect, непригодный вход — гейтом doctor.
NAMED_LIVENESS_MAX_AGE = int(os.getenv("RC_NAMED_LIVENESS_MAX_LOG_AGE", "43200") or "43200")
# Счётчик страйков: ветку гасим как зомби только после STRIKES ПОДРЯД мёртвых проверок, а не одной.
# Мгновенная просадка соединений при реконнекте (0 conns на один 30-с тик) больше не убивает живой
# процесс — предикат смерти обязан держаться ~STRIKES*CHECK_INTERVAL с непрерывно. Реальный зомби
# (стабильно стар+0conn) ловится за это время (по умолчанию ~90с после порога).
LIVENESS_STRIKES = int(os.getenv("RC_LIVENESS_STRIKES", "3") or "3")

# --- Жизнь БЕЗ РЕГИСТРАЦИИ в мосте (класс 18–22.09, docs/artifacts/2026-09-22-rc-device-profile2-stall.md) ---
# `claude rc` на профиле 2 висел до `[bridge:init]` (вопрос первого запуска в скрытой консоли): 91
# старт, 88 гашений ЗОМБИ, 0 регистраций, 0 карточек за 3,8 суток. Предикат живости тут бессилен:
# стартовый всплеск (11 строк за 0,9 с) держит лог «свежим» весь час порога 3600с, а между жизнями
# сторож не помнил ничего, и 88-й одинаковый вердикт выглядел как первый. Поэтому у ветки с
# spec["auth_watch"] (rc-server) есть ВТОРОЙ вопрос — была ли в жизни регистрация
# (rc_auth_detect.registered: True / False / None):
#   • после грейса жизнь без регистрации и БЕЗ ESTABLISHED-соединений LIVENESS_STRIKES проверок подряд
#     гасится сразу (180 + 3×30 = 270 с), а не через час;
#   • UNREG_LIVES погашенных так жизней подряд → ОДНА карточка владельцу (notify_owner, --critical →
#     инбокс 1160) и рестарты реже: UNREG_BACKOFF, дальше ×2, не выше UNREG_BACKOFF_MAX (1→2→4→6 ч);
#   • жизнь с регистрацией рвёт серию и снова взводит карточку; «не знаю» не считается и не рвёт.
# ВЫШЕДШАЯ САМА жизнь без регистрации в серию тоже не идёт и её не рвёт: выход — собственный
# вердикт процесса с кодом в журнале, а не молчаливое зависание. Замер 05.08: час сетевого отказа
# («Unable to connect to API») дал 79, а затем ещё 6 выходов exit=1 подряд раз в 30 с, и оба раза
# сеть вернулась сама. Засчитай их — было бы 2 ложные карточки «почини» и ~57 мин лишнего простоя
# устройства после второго (реплей — в артефакте 2026-09-22-rc-unregistered-life.md).
# Пороги 24.07/30.07 (свежесть, channel_alive, страйки) не тронуты; ветку канала этот вопрос не судит.
UNREG_ENABLED = os.getenv("RC_UNREG", "1") != "0"        # запасной клапан: RC_UNREG=0 — как до 22.09
UNREG_LIVES = max(1, int(os.getenv("RC_UNREG_LIVES", "3") or "3"))
UNREG_BACKOFF = int(os.getenv("RC_UNREG_BACKOFF", "3600") or "3600")               # 1 ч
UNREG_BACKOFF_MAX = int(os.getenv("RC_UNREG_BACKOFF_MAX", "21600") or "21600")     # 6 ч

# Две ветки супервизора. `mode` — аргументы claude ПОСЛЕ бинаря и ДО --debug-file:
#   rc-server:     ['rc']                          → постоянный сервер-Environment (устройство)
#   named-channel: ['--remote-control', <имя>]     → именованная интерактивная сессия
SERVER_SPEC = {"label": "rc-server", "mode": ("rc",), "log": SERVER_LOG,
               "max_age": SERVER_LIVENESS_MAX_AGE, "auth_watch": True}
NAMED_SPEC = {"label": "named-channel", "mode": ("--remote-control", SESSION_NAME), "log": NAMED_LOG,
              "max_age": NAMED_LIVENESS_MAX_AGE}
BRANCHES = (SERVER_SPEC, NAMED_SPEC)

# Детектор ПРОТУХШЕЙ авторизации (вариант «а» артефакта 2026-07-29-rc-token-staleness-prevention):
# после ротации кредов сервер отдаёт НОВЫМ воркерам протухший токен — каждая новая сессия
# владельца умирает за 2–3 с, а все пробы живости зелены. Лечение — рестарт ветки rc-server.
# Импорт мягкий: детектор не смеет стать новой точкой отказа канала.
try:
    import rc_auth_detect
except Exception:
    rc_auth_detect = None

# УЧЁТКА ДЕТЕЙ — рычаг 70w (`claude_profile_choice.txt`, читатель profile_choice.py), тот же, что у
# детей демона. Живой прокол 18–22.09 (docs/artifacts/2026-09-22-rc-device-profile2-stall.md):
# постоянная переменная пользователя CLAUDE_CONFIG_DIR=D:\claude_profile_2 увела ОБЕ ветки в
# профиль второго аккаунта. Там `claude rc` ждёт ответа первого запуска в скрытой консоли —
# 91 старт, 88 гашений ЗОМБИ, 0 регистраций, — а телефон вдобавок сидит на основном аккаунте.
# Импорт мягкий: не импортировался — окружение детей наследуется, как было до правки.
try:
    import profile_choice
except Exception:
    profile_choice = None

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


def child_env(chooser=None, base=None, repo=None):
    """Окружение детей супервизора (обе ветки И doctor пре-флайта) → (env | None, строка журнала).

    Решение даёт файл выбора учётки (рычаг 70w): ОСНОВНОЙ — ключ CLAUDE_CONFIG_DIR снят, путь —
    поставлен. env=None значит «не передавать env в Popen вовсе», то есть прежнее наследование
    байт-в-байт. Так будет при третьем исходе файла, при отсутствии модуля и при его срыве.
    doctor обязан судить ТОТ ЖЕ профиль, в котором поднимется ветка, иначе гейт даёт вердикт
    о чужом входе. chooser/base/repo — инъекция для тестов."""
    _pc = chooser if chooser is not None else profile_choice
    if _pc is None:
        return None, ("выбор учётки НЕ ПРИМЕНЁН: модуль profile_choice не импортирован — "
                      "окружение детей наследуется")
    env = dict(os.environ if base is None else base)
    try:
        d = _pc.apply_to(env, repo=repo or REPO)
        why = _pc.line(d)
    except Exception as e:          # рычаг не смеет уронить канал
        return None, ("выбор учётки НЕ ПРИМЕНЁН: сорвался (%s) — окружение детей наследуется"
                      % type(e).__name__)
    if d.action == _pc.ACT_KEEP:
        return None, why
    return env, why


def rc_ready(claude, runner=None, timeout=120, envf=None):
    """Готов ли контур поднять мост Remote Control → (ok, detail).

    FAIL-OPEN: если сам doctor не запустился/завис — НЕ блокируем канал (гард обязан ловить
    известную поломку, а не становиться новой точкой отказа). doctor идёт с окружением детей
    (child_env), envf — инъекция для тестов."""
    env, _why = (envf or child_env)()
    kw = {"env": env} if env is not None else {}
    try:
        p = (runner or subprocess.run)([claude, "doctor"], capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=timeout, cwd=REPO, **kw)
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


def probe_detail(spec, pid, now=None, getmtime=None, conns=None, max_age=None):
    """Живость канала ветки С ЧИСЛАМИ, по которым вынесен вердикт → (alive, age_sec, est, max_age).
    Числа возвращаем, а не только bool, чтобы строка сноса называла ПРИЧИНУ (возраст лога, число
    соединений, порог) — иначе будущие ложные и настоящие гашения снова неотличимы в логе.
    Порог берётся ПЕР-BRANCH из spec["max_age"] (сервер 3600с, канал 43200с), когда явный max_age
    не задан; ключа нет → общий LIVENESS_MAX_AGE. Инъекции now/getmtime/conns/max_age — для
    тестов без файловой системы и netstat."""
    age = log_age(spec["log"], now=now, getmtime=getmtime)
    est = (conns or established_conns)(pid)
    _max = max_age if max_age is not None else spec.get("max_age")
    if _max is None:
        _max = LIVENESS_MAX_AGE
    return channel_alive(age, est, max_age=_max), age, est, _max


def probe_alive(spec, pid, now=None, getmtime=None, conns=None, max_age=None):
    """Булев вердикт probe_detail (совместимость: вызывающим, кому числа не нужны)."""
    return probe_detail(spec, pid, now=now, getmtime=getmtime, conns=conns, max_age=max_age)[0]


def fmt_age(age_sec):
    """Возраст лога для строки журнала: None (файла нет) — так и говорим, а не «0с»."""
    return "нет файла" if age_sec is None else "%.0fс" % age_sec


def default_spawn(claude, spec, verbose=None, popen=None, envf=None):
    """Поднять процесс ветки. Сначала УСЕКАЕМ лог живости (свежий mtime = чистая точка отсчёта на
    новую жизнь процесса), затем Popen БЕЗ перенаправления stdio и БЕЗ creationflags: интерактивной
    сессии нужен живой TTY скрытой консоли wscript (redirect/CREATE_NO_WINDOW его убивают — классы
    «мёртвый TTY» / «мигающие чёрные окна»). Окружение — по рычагу учётки (child_env): env
    передаётся только при решении drop/set, консоль и stdio он не трогает. verbose=None → берём по
    файлу-флагу. popen/envf — инъекция для тестов. → объект процесса (.poll/.pid/.terminate) | None."""
    try:
        open(spec["log"], "w", encoding="utf-8").close()
    except Exception:
        pass
    vb = os.path.exists(DEBUG_FLAG) if verbose is None else verbose
    env, why = (envf or child_env)()
    log.info("[%s] %s", spec["label"], why)
    kw = {"env": env} if env is not None else {}
    try:
        return (popen or subprocess.Popen)(build_cmd(claude, spec, verbose=vb), cwd=REPO, **kw)
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


def unreg_pause(streak, lives=None, base=None, cap=None):
    """Пауза перед следующим стартом после жизни, погашенной без регистрации → сек.

    Серия короче lives — обычная RESTART_DELAY. С lives-й жизни — base, дальше ×2 на каждую
    следующую, не выше cap (по умолчанию 1 → 2 → 4 → 6 ч). Не меньше RESTART_DELAY: ноль в ручке не
    превращает backoff в тугой цикл."""
    k = UNREG_LIVES if lives is None else max(1, lives)
    b = UNREG_BACKOFF if base is None else base
    top = UNREG_BACKOFF_MAX if cap is None else cap
    if streak < k:
        return RESTART_DELAY
    step = min(streak - k, 32)            # дальше потолок достигнут при любой разумной ручке
    return max(RESTART_DELAY, min(b * (2 ** step), top))


def fmt_pause(sec):
    """Пауза для строки журнала и карточки: 3600 → «1 ч», 900 → «15 мин», 15 → «15 с»."""
    sec = int(sec)
    if sec >= 3600 and sec % 3600 == 0:
        return "%d ч" % (sec // 3600)
    if sec >= 60 and sec % 60 == 0:
        return "%d мин" % (sec // 60)
    return "%d с" % sec


def binary_note(path, getmtime=None):
    """Бинарь ветки С ВРЕМЕНЕМ его записи: апдейт CLI и смена профиля — первые два подозреваемых,
    и mtime шима отличает одно от другого без чтения чего-либо ещё."""
    try:
        m = (getmtime or os.path.getmtime)(path)
        return "%s (mtime %s)" % (path, time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(m)))
    except Exception as e:
        return "%s (mtime не прочитан: %s)" % (path, type(e).__name__)


def unreg_card(spec, streak, stage, life_sec, claude, pause, envf=None):
    """Карточка владельцу о серии жизней без регистрации: стадия (с числом строк лога), бинарь и
    его mtime, учётка детей, следующая пауза и что сделать после починки."""
    try:
        why = (envf or child_env)()[1]
    except Exception as e:
        why = "выбор учётки не прочитан (%s)" % type(e).__name__
    return ("🛑 RC: сервер устройства (`claude rc`, ветка %s) %d жизней подряд НЕ зарегистрировался "
            "в мосте — в Devices на телефоне ПК нет. Последняя жизнь ≈%d с, стадия: %s. "
            "Бинарь: %s. Учётка детей сейчас: %s. Такие жизни сторож гасит после грейса, а "
            "рестарты теперь реже: следующий через %s, дальше ×2, не чаще раза в %s. Карточка одна "
            "на серию, новая будет только после жизни с регистрацией. После починки перезапусти "
            "TurboBabyRC: серия и пауза живут в памяти супервизора. Разбор похожего случая: "
            "docs/artifacts/2026-09-22-rc-device-profile2-stall.md"
            % (spec["label"], streak, life_sec, stage, binary_note(claude), why,
               fmt_pause(pause), fmt_pause(max(pause, UNREG_BACKOFF_MAX))))


def unreg_knobs_note(branches, auth=None):
    """Что стартовая строка говорит о суде над жизнями без регистрации: ручки ЧИСЛАМИ, как их держит
    ПАМЯТЬ процесса (константы связаны на импорте — урок 30.07), либо прямо названная причина, по
    которой суда нет."""
    watched = [b["label"] for b in branches if b.get("auth_watch")]
    if not watched:
        return "жизни без регистрации: веток под судом нет"
    _auth = auth if auth is not None else rc_auth_detect
    if _auth is None or getattr(_auth, "registered", None) is None:
        return "жизни без регистрации: НЕ судятся — детектора регистрации нет"
    return ("жизни без регистрации (%s): RC_UNREG=%s RC_UNREG_LIVES=%s RC_UNREG_BACKOFF=%sс "
            "RC_UNREG_BACKOFF_MAX=%sс" % (",".join(watched), "1" if UNREG_ENABLED else "0 (выключено)",
                                          UNREG_LIVES, UNREG_BACKOFF, UNREG_BACKOFF_MAX))


def supervise_branch(spec, resolver=None, spawner=None, alive=None, gate=None,
                     sleeper=None, killer=None, rounds=None, checks=None, grace_checks=None,
                     strikes_needed=None, auth=None, notifier=None, probe=None, envf=None):
    """Вечный НЕЗАВИСИМЫЙ цикл ОДНОЙ ветки (свой поток): резолв шима → пре-флайт → подъём процесса
    → монитор → пауза → снова. Монитор рестартует по ДВУМ причинам: процесс ВЫШЕЛ сам (poll!=None)
    ЛИБО зомби — процесс жив, но канал мёртв по probe_alive _strikes_needed проверок ПОДРЯД (после
    грейс-периода, если проба не выключена RC_LIVENESS=0). Порог свежести — пер-branch (spec["max_age"]).
    ТРЕТЬЯ причина, только у ветки с spec["auth_watch"] (rc-server): ПРОТУХШАЯ АВТОРИЗАЦИЯ —
    новые сессии убиты дословным «token has been revoked» и падением за секунды (rc_auth_detect).
    Грейс её НЕ касается: этот отказ виден с первой же убитой сессии, а ждать 3 минуты значит
    подарить владельцу ещё один труп.
    ЧЕТВЁРТАЯ причина, у той же ветки: ЖИЗНЬ БЕЗ РЕГИСТРАЦИИ в мосте (класс 18–22.09, см. ручки
    UNREG_*) — после грейса регистрации нет и ESTABLISHED-соединений 0 _strikes_needed проверок
    подряд. Каждая жизнь в конце получает вердикт (есть регистрация / нет / не знаю), и серия таких
    жизней переживает рестарты: карточка владельцу и backoff пауз.
    rounds/checks/grace_checks/strikes_needed — ограничители/инъекции для тестов
    (None = боевой бесконечный режим); auth/notifier — инъекция детектора кредов;
    probe — инъекция пробы С ЧИСЛАМИ (probe_detail), alive — старая булева (числа в строке
    сноса тогда неизвестны; суд о регистрации без числа соединений не гасит ничего);
    envf — инъекция выбора учётки для строки карточки. → 0."""
    _resolve = resolver or resolve_claude
    _spawn = spawner or default_spawn
    if probe is not None:
        _probe = probe
    elif alive is not None:
        _probe = lambda sp, pid: (alive(sp, pid), None, None,
                                  sp.get("max_age") or LIVENESS_MAX_AGE)
    else:
        _probe = probe_detail
    _gate = gate or default_gate
    _sleep = sleeper or time.sleep
    _kill = killer or default_kill
    _grace = max(1, GRACE_SECONDS // max(1, CHECK_INTERVAL)) if grace_checks is None else grace_checks
    _strikes_needed = LIVENESS_STRIKES if strikes_needed is None else strikes_needed
    _auth = auth if auth is not None else rc_auth_detect
    _notify = notifier or notify_owner
    _watch = bool(spec.get("auth_watch") and _auth is not None)
    _auth_on = bool(_watch and getattr(_auth, "ENABLED", True))
    # Суд о регистрации — только у ветки auth_watch и только у детектора, который умеет на него
    # отвечать (двойник без registered = прежнее поведение байт-в-байт). Читатель лога у него общий с
    # детектором кредов, но RC_AUTH_WATCH=0 выключает рестарт по кредам, а не этот вопрос.
    _registered = getattr(_auth, "registered", None) if _watch else None
    _unreg_on = bool(UNREG_ENABLED and _registered is not None)
    unreg_streak = 0               # погашенных жизней без регистрации ПОДРЯД — переживает рестарты
    unreg_armed = True             # карточка одна на серию; взводится жизнью с регистрацией
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
        strikes = 0
        unreg_strikes = 0
        ended = None                   # чем кончилась жизнь: "exit" — сам, "killed" — сторож
        # Состояние детектора кредов заводим НА ЖИЗНЬ ПРОЦЕССА: default_spawn только что усёк
        # лог ветки, значит смещение 0 — честная точка отсчёта, старые улики не в счёт.
        auth_state = _auth.new_state() if (_auth_on or _unreg_on) else None
        auth_blind_said = False        # третий исход читателя говорим ОДИН раз на жизнь процесса
        while checks is None or c < checks:
            c += 1
            _sleep(CHECK_INTERVAL)
            code = proc.poll()
            if code is not None:
                log.info("[%s] процесс вышел (exit=%s) — рестарт через %s с",
                         spec["label"], code, RESTART_DELAY)
                ended = "exit"
                break
            # Протухшая авторизация: сервер жив и здоров по всем пробам, но НОВЫЕ сессии убиты
            # (дословная фраза + падение за секунды). Рестарт = новый сервер со свежими кредами.
            if auth_state is not None:
                try:
                    auth_state = _auth.scan(spec["log"], auth_state)
                    if _auth_on and _auth.should_restart(auth_state):
                        log.info("[%s] %s — гашу pid=%s, рестарт",
                                 spec["label"], _auth.describe(auth_state), getattr(proc, "pid", "?"))
                        _notify("♻️ Канал Remote Control: " + _auth.describe(auth_state) +
                                ". Сторож перезапустил rc-server — новые сессии поднимутся на "
                                "свежих кредах. На телефоне может смениться Environment.")
                        if proc.poll() is None:
                            _kill(proc)
                        ended = "killed"
                        break
                    # ТРЕТИЙ ИСХОД читателя (parse_outcome.NONPARSE): хвост лога непуст, а формат
                    # не опознан НИ В ОДНОЙ строке. Молчать здесь нельзя: ноль отказов от слепого
                    # детектора неотличим от «авторизация цела» — ровно тот класс, из-за которого
                    # 08.08 наверх уходил ноль при 292 живых строках и 0 из 4 маркеров. Говорим
                    # ОДИН раз на жизнь процесса (лог сторожа пишется каждый круг — иначе шум).
                    blind = getattr(_auth, "is_blind", None)
                    if blind is not None and not auth_blind_said and blind(auth_state):
                        auth_blind_said = True
                        log.warning("[%s] %s", spec["label"], _auth.blind_note(auth_state))
                except Exception as e:      # детектор не смеет уронить сторож
                    log.warning("[%s] детектор кредов сорвался (%s)", spec["label"],
                                type(e).__name__)
                    auth_state = None
            # Зомби: процесс жив, но канал мёртв. Только ПОСЛЕ грейса и только если проба включена
            # (RC_LIVENESS=0 = запасной клапан, откат к «рестарт лишь по выходу процесса»). Гасим НЕ по
            # одному тику, а после _strikes_needed ПОДРЯД мёртвых проверок: мгновенная просадка соединений
            # при реконнекте (0 conns на один тик) сбрасывает счётчик и НЕ убивает здоровый простаивающий
            # сервер (класс churn 24.07). Порог свежести лога — пер-branch, из spec (у сервера шире).
            dead, age, est, thr = False, None, None, None
            if LIVENESS_ENABLED and c > _grace:
                ok_live, age, est, thr = _probe(spec, proc.pid)
                dead = not ok_live
                # Жизнь БЕЗ РЕГИСТРАЦИИ: свежесть лога тут не довод — стартовый всплеск держит его
                # «свежим» весь час порога. Гасим, только когда оба ответа ТВЁРДЫЕ: детектор сказал
                # False (а не «не знаю») и соединений ровно 0 (число неизвестно — не гасим)
                # _strikes_needed проверок подряд. Здоровый старт регистрируется за 3,3 с.
                if _unreg_on:
                    try:
                        bare = _registered(auth_state) is False and est == 0
                    except Exception:           # суд не смеет уронить сторож
                        bare = False
                    unreg_strikes = unreg_strikes + 1 if bare else 0
                    if unreg_strikes >= _strikes_needed:
                        log.info("[%s] БЕЗ РЕГИСТРАЦИИ: жизнь ≈%sс, грейс %sс позади, %s; соединений 0"
                                 " %s проверок подряд — гашу pid=%s, рестарт",
                                 spec["label"], c * CHECK_INTERVAL, _grace * CHECK_INTERVAL,
                                 _life_stage(_auth, auth_state), unreg_strikes,
                                 getattr(proc, "pid", "?"))
                        if proc.poll() is None:
                            _kill(proc)
                        ended = "killed"
                        break
            if dead:
                strikes += 1
                if strikes >= _strikes_needed:
                    # Строка сноса НАЗЫВАЕТ ЧИСЛА, а не только вердикт: возраст лога, порог ветки,
                    # соединения. Без них ложное гашение неотличимо от настоящего — ровно так класс
                    # 24.07 и прожил незамеченным на второй ветке ещё шесть суток.
                    log.info("[%s] ЗОМБИ: канал мёртв %s проверок подряд — возраст лога %s > порога"
                             " %sс, соединений %s — гашу pid=%s, рестарт",
                             spec["label"], strikes, fmt_age(age), thr, est,
                             getattr(proc, "pid", "?"))
                    if proc.poll() is None:
                        _kill(proc)
                    ended = "killed"
                    break
            else:
                strikes = 0
        pause = RESTART_DELAY
        if _unreg_on:
            pause, unreg_streak, unreg_armed = _close_life(
                spec, proc, claude, c, ended, _auth, auth_state, unreg_streak, unreg_armed,
                _notify, envf)
        _sleep(pause)
    return 0


def _life_stage(auth, state):
    """Стадия жизни от детектора; детектор без life_stage или сорвавшийся — не причина молчать."""
    try:
        return auth.life_stage(state)
    except Exception as e:
        return "стадия неизвестна (%s)" % type(e).__name__


def _close_life(spec, proc, claude, c, ended, auth, state, streak, armed, notify, envf):
    """Вердикт кончившейся жизни ветки и его след: серия, карточка, пауза → (pause, streak, armed).

    Последний проход по логу — до вердикта: процесс мог дописать строки после прошлой проверки.
      • регистрация была → серия рвётся, карточка снова взведена;
      • регистрации нет, жизнь ПОГАСИЛ сторож → серия +1; на UNREG_LIVES-й — одна карточка и backoff;
      • регистрации нет, но процесс ВЫШЕЛ САМ, либо «не знаю» → серия не растёт и не рвётся (выход —
        свой вердикт процесса с кодом в журнале; 05.08 так выглядел сетевой отказ, прошедший сам)."""
    if state is not None:
        try:
            state = auth.scan(spec["log"], state)
        except Exception:
            state = None
    try:
        verdict = auth.registered(state)
    except Exception:
        verdict = None
    stage = _life_stage(auth, state)
    life_sec = c * CHECK_INTERVAL
    counts = verdict is False and ended == "killed"
    if verdict is True:
        if streak:
            log.info("[%s] серия без регистрации (%s подряд) прервана жизнью с регистрацией — "
                     "карточка снова взведена", spec["label"], streak)
        streak, armed = 0, True
    elif counts:
        streak += 1
    if verdict is True:
        say = "регистрация есть"
    elif verdict is False:
        say = ("регистрации НЕТ" if counts else
               "регистрации НЕТ, но процесс вышел сам — в серию не идёт")
    else:
        say = "про регистрацию не знаю — в серию не идёт и её не рвёт"
    log.info("[%s] итог жизни pid=%s (≈%sс, %s): %s; %s — серия без регистрации %s из %s",
             spec["label"], getattr(proc, "pid", "?"), life_sec,
             {"exit": "вышла сама", "killed": "погашена сторожем"}.get(ended, "не окончена"),
             say, stage, streak, UNREG_LIVES)
    if not (counts and streak >= UNREG_LIVES):
        return RESTART_DELAY, streak, armed
    pause = unreg_pause(streak)
    if armed:
        try:
            sent = notify(unreg_card(spec, streak, stage, life_sec, claude, pause, envf=envf))
        except Exception as e:            # карточка не смеет уронить сторож
            log.warning("[%s] карточка о серии без регистрации сорвалась (%s)", spec["label"],
                        type(e).__name__)
            sent = False
        armed = sent is False           # не ушла — повторим на следующей жизни серии
        log.info("[%s] %s жизней подряд без регистрации — карточка владельцу %s",
                 spec["label"], streak, "НЕ ушла, повторю" if armed else "ушла")
    log.info("[%s] backoff: следующий старт через %s (серия %s, потолок %s)", spec["label"],
             fmt_pause(pause), streak, fmt_pause(UNREG_BACKOFF_MAX))
    return pause, streak, armed


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
    # Ветки НАЗЫВАЮТ свой порог свежести ПРЯМО НА СТАРТЕ. Иначе «фикс в силе» доказуем лишь двумя
    # плохими способами: ждать 22 минуты отсутствия сноса (доказательство молчанием) либо прочесть
    # строку сноса — то есть узнать порог только в момент ПРОВАЛА. Файл на диске не доказывает
    # ничего: константы связываются ОДИН раз на импорте, hot-reload в CPython нет — 30.07 супервизор
    # 20 часов гасил канал каждые 21,5 мин по старому числу, когда на диске уже лежало новое.
    # Теперь процесс сам сообщает, что держит в ПАМЯТИ, первой же строкой после рестарта. Так же —
    # ручки суда о регистрации (22.09): «правка в силе» видна на старте, а не на первой карточке.
    log.info("супервизор стартовал (pid=%s, ветки=%s, %s, cwd=%s)",
             os.getpid(),
             ["%s:порог %sс" % (b["label"], b.get("max_age") or LIVENESS_MAX_AGE) for b in _br],
             unreg_knobs_note(_br), REPO)
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
