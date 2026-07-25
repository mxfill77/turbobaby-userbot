#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pretool_guard.py — PreToolUse-хук Claude Code для ПК (D:\\turbobaby-bot).
Порт VPS-паттерна (/root/turbobaby-manager-bot/pretool_guard.py) под Windows.

ПОЗИЦИЯ БЕЗОПАСНОСТИ (как на VPS): хук ТОЛЬКО ДОБАВЛЯЕТ подтверждения, НИКОГДА не выдаёт
новых разрешений. Он не возвращает permissionDecision="allow" — авто-allow зелёного делает
список permissions.allow в settings.json. Хук лишь форсит "ask" (+🔔 пуш) на КРАСНОМ по
доктринальному списку. Значит ошибка классификации может только ДОБАВИТЬ лишний ask
(безопасная сторона), но не пропустить опасное.

Трёхцветно:
  ЗЕЛЁНОЕ  → defer (exit 0, без вывода) → штатные allow-правила решают.
  КРАСНОЕ  (доктринальный список `_stays_red`) → ask (карточка в сессии) + 🔔 dispatch_notify.
  НЕИЗВЕСТНОЕ → defer с логом (доктрина VPS: незнакомая команда сама по себе НЕ красная);
  ошибка анализа самого гарда → ask (FAIL-SAFE, в сторону подтверждения, В ЛЮБОЙ роли).

КРАСНЫЙ список (НЕ смягчать): правка/чтение .env и секретов; удаление файлов
(del/rm/rmdir/Remove-Item); правка КОНФИГА .claude (settings*.json / hooks / agents / commands /
plugins / skills — вектор само-эскалации; память и стенограммы агента там же конфигом НЕ
считаются); kill/taskkill/schtasks/Stop-Process;
git push --force / reset --hard / clean; sqlite3 / SQL-запись в БД; запись/исполнение вне
D:\\turbobaby-bot; сеть кроме git/Bridge/Telegram (curl/wget/ssh/scp/…); python-скрипт с
боевой записью (Bridge create_booking/add_transaction/… , os.remove/rmtree, SQL-write);
живые таблицы (clasp / Apps Script / Sheets API).

ДОКТРИНА РЕШЕНИЯ (перенос списка с VPS, кейс 314). Исторически красное делилось по роли
(регрессия удобства после оживления гарда в 0014ea6): интерактиву — только доктринальное
(`_stays_red`), headless-детям демона — строгий `decide()`, где НЕИЗВЕСТНОЕ = ask. Кейс 314
(2026-07-23 14:33/14:56) показал цену строгости: headless-задача упала в NEEDS_APPROVAL на
ЧИСТОМ ЧТЕНИИ — `wc -l` по файлам репо и цикл ожидания `until grep …; do sleep 5; done`.
Доктрина VPS теперь действует В ОБЕИХ РОЛЯХ: незнакомая команда сама по себе НЕ красная;
Allow спрашивает ТОЛЬКО доктринальный список `_stays_red` (живые таблицы, .env/секреты,
sqlite, clasp, kill/schtasks-контроль, массовые удаления, git-force, сеть, запись вне репо,
конфиг .claude, боевые py-токены). `decide()` остаётся строгим классификатором (kind нужен
логу и карточке), решение принимает `decide_for_role`. Ошибка анализа — по-прежнему ask.

Роль определяется ПО ФАКТУ и ПЕРЕИСПОЛЬЗУЕТ уже существующий механизм: демон штампует
КАЖДОМУ своему headless-ребёнку env `PRETOOL_ASK_MARKER` (pc_orchestrator.run_task, это
ЕДИНСТВЕННЫЙ путь спавна headless — run_claude), и гард этот штамп уже читает в `_emit_ask`.
env наследуется вниз по дереву процессов, значит субагенты headless-ребёнка тоже помечены.
Решение роль больше НЕ меняет, но остаётся нужна: колонке роли в логе и КАНАЛУ ask —
headless пишет красную карточку в файл-маркер демону (NEEDS_APPROVAL), интерактив
показывает её в сессии.

ЛОГ (`pretool_guard.log`, под *.log в .gitignore): пишется КАЖДОЕ решение обеих ролей —
смягчение не должно стоить прозрачности. Строка: время | роль | инструмент | решение | вид |
команда (обрезана, значения токенов/паролей замаскированы).
"""
import sys
import os
import re
import json
import shlex
import subprocess
import datetime

try:  # хук-протокол Claude Code читает stdout как UTF-8; форсим, чтобы 🔴/кириллица не бились
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = r"D:\turbobaby-bot"
PROJ_N = os.path.normcase(os.path.normpath(PROJECT))
VENV_PY = os.path.join(PROJECT, "venv", "Scripts", "python.exe")
DNOTIFY = os.path.join(PROJECT, "dispatch_notify.py")
GUARD_LOG = os.path.join(PROJECT, "pretool_guard.log")   # под *.log в .gitignore — в репо не уедет
GUARD_LOG_MAX = 2 * 1024 * 1024                          # 2 МБ → ротация (лог не растёт вечно)

try:  # общая ротация + разведение тестового и боевого лога (см. log_setup)
    import log_setup
except Exception:                                        # гард обязан работать даже без модуля
    log_setup = None

# --- КРАСНЫЕ признаки Bash-команды → (regex, kind) ---
_RED_CMD = [
    (re.compile(r"(?i)(^|[\s;&|(])(del|erase|rmdir|rd|rm)([\s;&|)]|$)"), "delete"),
    (re.compile(r"(?i)remove-item\b"), "delete"),
    (re.compile(r"(?i)(^|[\s;&|(])(taskkill|kill|pkill)([\s;&|)]|$)"), "kill"),
    (re.compile(r"(?i)stop-process\b"), "kill"),
    (re.compile(r"(?i)\bschtasks\b"), "schtasks"),
    (re.compile(r"(?i)git\s+push\b.*(--force|(?<![\w-])-f(?![\w]))"), "git_force"),
    (re.compile(r"(?i)git\s+reset\s+--hard"), "git_force"),
    (re.compile(r"(?i)git\s+clean(\s|$)"), "git_force"),
    (re.compile(r"(?i)(^|[\s;&|(])sqlite3([\s;&|)]|$)"), "sqlite"),
    (re.compile(r"(?i)(^|[\s;&|(])clasp([\s;&|)]|$)"), "clasp"),
    # живые таблицы напрямую (не через Bridge): Apps Script / Sheets API / gspread
    (re.compile(r"(?i)script\.google\.com|sheets\.googleapis\.com|(^|[\s;&|(])gspread([\s;&|)]|$)"), "live_sheet"),
    (re.compile(r"(?i)(^|[\s;&|(])(curl|wget|iwr|irm)([\s;&|)]|$)|invoke-webrequest|invoke-restmethod"), "network"),
    (re.compile(r"(?i)(^|[\s;&|(])(ssh|scp|sftp|nc|ncat|telnet)([\s;&|)]|$)"), "network"),
]


def _extract_delete_target(cmd):
    m = re.search(r"(?i)(?:del|erase|rmdir|rd|rm|remove-item)\s+(?:[-/][a-z]+\s+)*[\"']?([^\s\"';|&]+)", cmd)
    return m.group(1) if m else None


def _extract_kill_target(cmd):
    m = (re.search(r"(?i)/pid\s+(\d+)", cmd) or re.search(r"(?i)-id\s+(\d+)", cmd)
         or re.search(r"(?i)\b(?:kill|pkill)\s+(\d+)", cmd))
    if m:
        return "PID " + m.group(1)
    m = re.search(r"(?i)/im\s+([^\s\"']+)", cmd) or re.search(r"(?i)-name\s+([^\s\"']+)", cmd)
    return m.group(1) if m else None


def _extract_host(cmd):
    m = re.search(r"(?i)https?://([^/\s\"']+)", cmd)
    if m:
        return m.group(1)
    m = re.search(r"([\w.\-]+@[\w.\-]+)", cmd)
    if m:
        return m.group(1)
    m = re.search(r"(?i)(?:ssh|scp|sftp)\s+([^\s\"'-][^\s\"']*)", cmd)
    return m.group(1) if m else None


def _extract_db(cmd):
    m = re.search(r"([\w.\-\\/]+\.db)\b", cmd)
    return m.group(1) if m else None


def _human(kind, obj=""):
    """Человекочитаемая фраза для карточки: ЧТО (+ объект/причина). Без «— разрешить?» (добавит _card)."""
    o = (obj or "").strip()
    tpl = {
        "delete": ("Хочу удалить файл " + o) if o else "Хочу удалить файл(ы)",
        "kill": ("Хочу снять процесс " + o + " (taskkill/kill)") if o else "Хочу снять процесс (taskkill/kill)",
        "schtasks": "Хочу выполнить операцию Планировщика задач (schtasks)",
        "git_force": "Хочу перезаписать git-историю (push --force / reset --hard / clean)",
        "sqlite": ("Хочу записать в базу данных " + o) if o else "Хочу записать в базу данных (sqlite)",
        "clasp": "Хочу выполнить clasp (код Apps Script живых таблиц)",
        "live_sheet": "Хочу обратиться к ЖИВЫМ таблицам напрямую (Лист1 / CRM / Календарь)",
        "network": ("Хочу выйти в сеть к " + o) if o else "Хочу выполнить сетевую команду (curl/wget/ssh)",
        "env": "Хочу обратиться к .env / секретам",
        "outside": ("Хочу записать за пределами проекта: " + o) if o else "Хочу выполнить операцию за пределами проекта",
        "py_write": ("Хочу выполнить python с боевой записью (" + o + ")") if o else "Хочу выполнить python с боевой записью",
        "edit_secret": "Хочу изменить секретный файл " + (o or ".env/сессия") + " (секреты/токен)",
        "read_secret": "Хочу прочитать секретный файл " + (o or ".env/сессия") + " (секреты/токен)",
        "edit_claude": "Хочу изменить конфиг Claude Code (.claude): " + (o or "*"),
        "write_outside": "Хочу записать за пределами проекта: " + (o or "вне D:\\turbobaby-bot"),
        "unknown": "Требуется подтверждение: команда не распознана как безопасная",
    }
    return tpl.get(kind, "Требуется подтверждение операции")
_RE_ENV = re.compile(r"(?i)(\.env(\b|['\"\s]|$)|\.session\b)")            # .env / *.session в команде
_RE_SQL_WRITE = re.compile(r"(?i)\b(UPDATE|DELETE\s+FROM|INSERT\s+INTO|DROP\s+TABLE)\b")
# Путь конфига Claude Code, упомянутый В КОМАНДЕ шелла (обход Write/Edit-гейта через cp/mv/>).
_RE_CLAUDE_CFG_CMD = re.compile(
    r"(?i)\.claude[\\/](settings[\w.-]*\.json|hooks|agents|commands|plugins|skills)|(?<![\w.])\.claude\.json\b")
# Чистое ЧТЕНИЕ конфига остаётся зелёным. Список смотрелок задан явно: проверять «команда
# выглядит read-only» общим списком нельзя — `echo '{}' > .claude/settings.json` начинается с
# echo и прошёл бы как безобидный, а это перезапись конфига.
_RE_CFG_VIEW = re.compile(r"(?i)^\s*(cat|type|head|tail|more|less|grep|rg|findstr|select-string|"
                          r"get-content|get-item|get-childitem|test-path|ls|dir|git)\b")
_RE_REDIRECT = re.compile(r">>?")


def _is_pure_config_read(cmd):
    """True ⇔ команда только СМОТРИТ конфиг: известная смотрелка и НИ ОДНОГО перенаправления."""
    return bool(_RE_CFG_VIEW.match(cmd or "")) and not _RE_REDIRECT.search(cmd or "")
_RE_OUTSIDE_WRITE = re.compile(r"(?i)(>>?|out-file|set-content|new-item|move-item|copy-item)\s+[\"']?([a-z]:[\\/][^\"'\s]+)")

# --- ЗЕЛЁНЫЕ признаки Bash-команды (проверяются ПОСЛЕ красных) ---
# Доверенные скрипты — зелёные ПО ИМЕНИ модуля, содержимое не сканируется (их тела законно
# читают конфиг с секретами; гейтуются в репо review+git). brain_writer — ЕДИНСТВЕННЫЙ
# легальный канал записи в Brain-доки с ПК (класс 328, образец VPS-cclog): секреты Bridge
# берёт сам процесс писателя, вызывающие скрипты их не читают и в коде не видят.
_RE_SAFE_SCRIPTS = re.compile(r"(?i)(cowork_log_append|dispatch_notify|brain_writer)\.py")
_RE_GIT_SAFE = re.compile(r"(?i)(^|[\s;&|(])git\s+(status|diff|log|add|commit|push|fetch|pull|branch|show|check-ignore|rev-parse|remote|ls-files|config\s+--get)")
_RE_TESTS = re.compile(r"(?i)-m\s+(pytest|py_compile|unittest)(\s|$)|(^|[\s/\\])pytest(\s|$)")
_RE_READONLY_SHELL = re.compile(r"(?i)^\s*(ls|dir|echo|type|cat|head|tail|wc|stat|findstr|grep|rg|get-content|get-childitem|select-string|get-item|get-ciminstance|test-path|measure-object|where|get-command|git|py|python\s+--version)\b")
# Цикл ОЖИДАНИЯ вида `until <смотрелка>; do sleep N; done[; <смотрелка>]` — зелёный: чистое
# чтение + sleep (кейс 314: headless ждал вердикт фонового прогона в task-output). Условие и
# необязательный хвост после done обязаны начинаться с известной смотрелки; красные признаки
# ВСЕЙ команды (curl в хвосте, .env в пути и т.п.) уже проверены выше по _decide_bash.
_RE_UNTIL_WAIT = re.compile(
    r"(?is)^\s*until\s+(!?\s*[^;]+?)\s*;\s*do\s+sleep\s+[\d.]+\s*;?\s*done\s*(?:;\s*(\S.*))?$")
_RE_PY = re.compile(r"(?i)(^|[\s/\\])(python3?|python\.exe|venv[\\/]scripts[\\/]python(\.exe)?)(\s|$)")
_GREEN_MODULES = {"py_compile", "pytest", "unittest", "json.tool"}
# .py-цели в сырой команде (устойчиво к shlex, который на Windows-путях с «\» ломает токены)
_RE_PY_FILE = re.compile(r"(?i)(?:^|[\s/\\\"'])([^\s/\\\"';|&]+\.py)(?:[\s\"';|&]|$)")
_RE_PY_FLAG = re.compile(r"(?i)(^|\s)-(c|m)(\s|$)|(^|\s)-(\s|$)")   # -c/-m/stdin → полный скан, не шорткат

# python-скрипт с боевой записью → красное
_RED_PY_TOKENS = [
    "create_booking", "activate_booking", "add_transaction", "void_last",
    "set_fleet_oil", "set_fleet_service", "delete_event", "closing_upsert",
    "os.remove", "os.unlink", "shutil.rmtree", "rmtree(", "os.rmdir",
]

# Живые таблицы из python-кода (Лист1/CRM/Календарь напрямую, минуя Bridge). Отдельно от
# _RED_PY_TOKENS: у них своя карточка, и в _stays_red они красные безусловно.
_LIVE_SHEET_TOKENS = ["gspread", "sheets.googleapis.com", "script.google.com"]


def _inside_project(path):
    if not path:
        return False
    try:
        ap = path if os.path.isabs(path) else os.path.join(PROJECT, path)
        ap = os.path.normcase(os.path.normpath(ap))
    except Exception:
        return False
    return ap == PROJ_N or ap.startswith(PROJ_N + os.sep)


def _is_secret_path(path):
    b = os.path.basename(path or "").lower()
    return (b == ".env" or b.startswith(".env") or b.endswith(".session")
            or ".session" in b or b.endswith(".key") or b.endswith(".pem") or "secret" in b)


# Внутри `.claude` красное — ТОЛЬКО конфиг (вектор само-эскалации: правами и хуками сессия
# расширяет собственные полномочия). Рабочие данные агента там же — НЕ конфиг: память
# (projects/<репо>/memory/*.md), стенограммы (*.jsonl), сессии/тудушки/снапшоты шелла. Живой
# факт аудита 22:27: 3 из 20 последних Allow — это запись СВОИХ ЖЕ файлов памяти
# (rc-effort-override.md, claude-bin-stale-version.md, MEMORY.md). Подтверждения на них не
# защищают ничего, а поток притупляет внимание к настоящему красному.
_CLAUDE_CFG_FILE = re.compile(r"(?i)^(settings(\.[\w.-]+)?\.json|\.?claude\.json|[\w.-]*mcp[\w.-]*\.json)$")
_CLAUDE_CFG_DIR = ("hooks", "agents", "commands", "plugins", "skills")


def _is_claude_path(path):
    """True ⇔ путь — КОНФИГ Claude Code (settings*.json / mcp-конфиг / hooks|agents|commands|
    plugins|skills внутри `.claude`). Память, стенограммы и прочие рабочие данные агента → False."""
    n = os.path.normcase(os.path.normpath(path or ""))
    parts = n.split(os.sep)
    try:
        i = max(k for k, p in enumerate(parts) if p == ".claude")
    except ValueError:
        if os.path.basename(n) in (".claude.json", "claude.json"):
            return True                       # ~/.claude.json лежит РЯДОМ с каталогом, не внутри
        return False
    tail = parts[i + 1:]
    if not tail:
        return False                          # сам каталог .claude — не файл конфига
    if _CLAUDE_CFG_FILE.match(tail[-1]):
        return True
    return any(seg in _CLAUDE_CFG_DIR for seg in tail[:-1])


# Скретчпад сессии (%TEMP%\claude\<проект>\<сессия>\scratchpad\…) — САНКЦИОНИРОВАННАЯ временная
# зона harness'а: изолирована от репо и от данных пользователя, живёт один сеанс. Требование
# «правки только внутри репо» защищает от правок ЧУЖОГО кода, а не от временных файлов, ради
# которых скретчпад и существует. Живой факт аудита: 3 из 20 Allow — запись собственных
# временных скриптов разбора.
def _is_scratchpad(path):
    n = os.path.normcase(os.path.normpath(path or ""))
    parts = n.split(os.sep)
    return "scratchpad" in parts and "claude" in parts and (
        os.sep + "temp" + os.sep in n or os.sep + "tmp" + os.sep in n)


def _is_memory_store(path):
    """Хранилище памяти агента: `.claude/projects/<репо>/memory/**`. Это СОБСТВЕННЫЕ заметки
    сессии, а не чужой код и не конфиг — правило «только внутри репо» защищает от правок чужого,
    и на память оно не про то. Конфигом эти файлы не являются (см. _is_claude_path), значит
    расширить свои права через них нельзя."""
    parts = os.path.normcase(os.path.normpath(path or "")).split(os.sep)
    try:
        i = parts.index("memory")
    except ValueError:
        return False
    return ".claude" in parts[:i] and "projects" in parts[:i]


def _sanctioned_outside(path):
    """Зоны ВНЕ репо, где запись не требует подтверждения: временный скретчпад сессии и
    хранилище памяти агента. Оба — рабочая зона самой сессии, не вектор эскалации.
    Живой факт аудита 22:27: 6 из 20 последних Allow были именно про них."""
    return _is_scratchpad(path) or _is_memory_store(path)


def _is_test_target(path):
    """test_*.py как ЦЕЛЬ прямого запуска (в т.ч. tests/test_*.py). Содержимое тест-файлов —
    фикстуры с красными токенами (.env/os.remove/add_transaction/…), а НЕ боевые вызовы; сами
    тесты гейтуются в репо (review+git). Порт VPS-урока: арг-файлы тест-раннера не сканируем."""
    b = os.path.basename(path or "").lower()
    return b.startswith("test_") and b.endswith(".py")


def _all_py_targets_are_tests(cmd):
    """True ⇔ это прямой запуск python, где ВСЕ .py-цели — test_*.py (и нет -c/-m/stdin).
    Тогда контент не сканируем (тесты гейтуются в репо). Смешанный запуск (есть не-тест .py)
    или -c/-m/stdin → False, идём в полный _scan_python — красное для остального НЕ ослабляем."""
    if _RE_PY_FLAG.search(cmd):
        return False                          # -c/-m/stdin: .py-имя могло быть внутри кода → скан
    files = _RE_PY_FILE.findall(cmd)
    return bool(files) and all(_is_test_target(f) for f in files)


# Позиционные аргументы .py-скрипта — ДАННЫЕ, а не операция (порт класса VPS 23.07.2026,
# коммит 7af280c). `python cclog.py DONE "<боевое имя> записан"` интерпретатор НЕ исполняет —
# строку читает сам скрипт; её попадание в скан-текст давало ЛОЖНОЕ красное на каждой такой записи.
def _strip_script_cli_args(cmd):
    """Вырезать из СКАН-ТЕКСТА позиционные аргументы после токена, оканчивающегося на .py.
    Интерпретатор и имя скрипта остаются. Исполняется всегда ИСХОДНАЯ команда — правится только
    текст, по которому идёт поиск красных токенов.

    Доктрина не ослаблена — режем не всегда:
      • -c / -m / stdin в команде → там аргумент И ЕСТЬ код → возвращаем как есть (полный скан);
      • команда без интерпретатора python (grep/cat с .py в аргументе) → как есть;
      • аргумент, похожий на файл секретов (.env/*.session/…), СОХРАНЯЕМ — иначе
        `python reader.py .env` перестал бы блокироваться;
      • кривое квотирование → возвращаем исходную строку, не угадываем.
    """
    if not cmd or not _RE_PY.search(cmd) or _RE_PY_FLAG.search(cmd):
        return cmd
    try:
        toks = shlex.split(cmd)
    except Exception:
        return cmd
    kept, cut = [], False
    for t in toks:
        if not cut:
            kept.append(t)
            if t.lower().endswith(".py"):
                cut = True
            continue
        if _is_secret_path(t) or _RE_ENV.search(t):
            kept.append(t)                       # секрет в аргументе обязан остаться видимым скану
    return " ".join(kept) if cut else cmd


# «Доверие по происхождению» (порт VPS a5c148e): файл ПОД git приехал в репо через review+git —
# его тело не сканируем, ровно тот же довод, что уже принят для test_*.py выше. Красное для всего
# остального НЕ ослаблено: инлайн -c/-m, сама команда, .env, живые таблицы и SQL-запись
# проверяются полностью, а НЕотслеживаемый .py по-прежнему читается и сканируется.
_TRACKED_CACHE = {}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # без него git-проба мигала бы чёрным окном


def _is_repo_tracked(path, cwd):
    """True ⇔ .py-цель лежит внутри проекта и отслеживается git. git недоступен/ошибка → False
    (fail-safe: без доверия идём прежним путём — читаем и сканируем тело)."""
    if not path:
        return False
    for cand in (path, os.path.join(cwd or PROJECT, path), os.path.join(PROJECT, path)):
        try:
            ap = os.path.abspath(cand)
        except Exception:
            continue
        if not os.path.isfile(ap) or not _inside_project(ap):
            continue
        key = os.path.normcase(ap)
        if key not in _TRACKED_CACHE:
            try:
                rel = os.path.relpath(ap, PROJECT)
                p = subprocess.run(["git", "-C", PROJECT, "ls-files", "--error-unmatch", "--", rel],
                                   capture_output=True, text=True, timeout=5,
                                   creationflags=_NO_WINDOW)
                _TRACKED_CACHE[key] = (p.returncode == 0)
            except Exception:
                _TRACKED_CACHE[key] = False
        return _TRACKED_CACHE[key]
    return False


# ------------------------------- python scan ---------------------------------

def _read_file(path, cwd):
    for cand in (path, os.path.join(cwd or PROJECT, path), os.path.join(PROJECT, path)):
        try:
            if os.path.isfile(cand):
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read(400000)
        except Exception:
            continue
    return None


def _scan_python(cmd, cwd):
    """→ ('defer','','') | ('ask','py_write',detail). Инлайн -c и тела .py-целей, ищем боевую запись."""
    try:
        toks = shlex.split(cmd)
    except Exception:
        return ("ask", "py_write", "кривое квотирование")
    content = ""
    saw_target = False
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "-c":
            content += (toks[i + 1] if i + 1 < len(toks) else "")
            saw_target = True
            i += 2
            continue
        if t == "-":
            return ("ask", "py_write", "код из stdin")
        if t == "-m":
            mod = toks[i + 1] if i + 1 < len(toks) else ""
            if mod in _GREEN_MODULES:
                return ("defer", "", "")
            return ("ask", "py_write", f"-m {mod}")
        if t.endswith(".py"):
            if _is_test_target(t) or _is_repo_tracked(t, cwd):
                # тело НЕ читаем/НЕ сканируем в двух случаях: (1) прямой запуск test_*.py —
                # красные токены внутри тестов это фикстуры, а не боевая запись; (2) файл ПОД
                # git — приехал через review+git («доверие по происхождению», порт VPS a5c148e).
                # Красный список для остального НЕ ослабляем: НЕотслеживаемый не-тест .py
                # по-прежнему читается и сканируется ниже.
                saw_target = True
                i += 1
                continue
            body = _read_file(t, cwd)
            if body is None:
                return ("ask", "py_write", "скрипт не прочитан")
            content += body
            saw_target = True
        i += 1
    # скан-текст: позиционные аргументы скрипта — данные, не операция (_strip_script_cli_args)
    blob = _strip_script_cli_args(cmd) + "\n" + content
    if _RE_ENV.search(blob):
        return ("ask", "env", "")
    for tok in _LIVE_SHEET_TOKENS:
        if tok in blob:
            return ("ask", "live_sheet", tok)
    for tok in _RED_PY_TOKENS:
        if tok in blob:
            return ("ask", "py_write", tok)
    if _RE_SQL_WRITE.search(blob) and ".db" in blob.lower() and "memory.db" not in blob.lower():
        return ("ask", "sqlite", _extract_db(blob) or "")
    if not saw_target:
        return ("ask", "py_write", "без внятной цели")
    return ("defer", "", "")


# ------------------------------- классификаторы ------------------------------

def _decide_write(ti, cwd):
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if _is_secret_path(path):
        return ("ask", "edit_secret", os.path.basename(path))
    if _is_claude_path(path):
        return ("ask", "edit_claude", os.path.basename(path))
    if not _inside_project(path) and not _sanctioned_outside(path):
        return ("ask", "write_outside", path)
    return ("defer", "", "")


def _decide_read(ti, cwd):
    path = ti.get("file_path") or ""
    if _is_secret_path(path):
        return ("ask", "read_secret", os.path.basename(path))
    return ("defer", "", "")


def _decide_bash(cmd, cwd):
    if not cmd:
        return ("defer", "", "")
    for rx, kind in _RED_CMD:
        if rx.search(cmd):
            obj = ""
            if kind == "delete":
                obj = _extract_delete_target(cmd) or ""
            elif kind == "kill":
                obj = _extract_kill_target(cmd) or ""
            elif kind == "network":
                obj = _extract_host(cmd) or ""
            elif kind == "sqlite":
                obj = _extract_db(cmd) or ""
            return ("ask", kind, obj)
    if _RE_ENV.search(cmd):
        return ("ask", "env", "")
    # Конфиг `.claude` защищён для инструментов Write/Edit — значит его надо защитить и от
    # ОБХОДА через шелл. Иначе дыра тривиальна: сессия пишет settings.json.new (внутри репо,
    # зелёное) и копирует его поверх боевого одной командой `cp`, молча расширив свои права.
    # Чтение конфига остаётся зелёным (ветка _RE_READONLY_SHELL ниже — cat/type/Get-Content).
    m = _RE_CLAUDE_CFG_CMD.search(cmd)
    if m and not _is_pure_config_read(cmd):
        return ("ask", "edit_claude", m.group(0))
    m = _RE_OUTSIDE_WRITE.search(cmd)
    if m and not _inside_project(m.group(2)):
        return ("ask", "outside", m.group(2))
    # зелёное
    if _RE_SAFE_SCRIPTS.search(cmd):
        return ("defer", "", "")
    if _RE_GIT_SAFE.search(cmd):
        return ("defer", "", "")
    if _RE_TESTS.search(cmd):
        return ("defer", "", "")
    if _RE_PY.search(cmd):
        if _all_py_targets_are_tests(cmd):        # прямой запуск только test_*.py → без контент-скана
            return ("defer", "", "")
        return _scan_python(cmd, cwd)
    m = _RE_UNTIL_WAIT.match(cmd)
    if m:
        cond = (m.group(1) or "").lstrip("! \t")
        tail = m.group(2) or ""
        if _RE_READONLY_SHELL.match(cond) and (not tail or _RE_READONLY_SHELL.match(tail)):
            return ("defer", "", "")
    if _RE_READONLY_SHELL.search(cmd):
        return ("defer", "", "")
    return ("ask", "unknown", "")


def decide(data):
    """Чистая классификация: → ('defer','','') или ('ask', kind, obj). Без I/O."""
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or PROJECT
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return _decide_write(ti, cwd)
    if tool == "Read":
        return _decide_read(ti, cwd)
    # PowerShell — ОТДЕЛЬНЫЙ инструмент от Bash, и на этом ПК он основной. До правки он не
    # попадал ни в matcher хука, ни под классификацию: `PowerShell(Remove-Item …)` проходил
    # мимо КРАСНОГО гейта целиком. Красные признаки (Remove-Item / Stop-Process /
    # Invoke-WebRequest / .env) уже описаны в _RED_CMD в PowerShell-форме — переиспользуем их.
    if tool in ("Bash", "PowerShell"):
        return _decide_bash(ti.get("command") or "", cwd)
    return ("defer", "", "")  # Grep/Glob/прочие read-only инструменты


# ------------------------------- роль и доктрина ------------------------------

ASK_MARKER_ENV = "PRETOOL_ASK_MARKER"   # штамп демона на КАЖДОМ headless-ребёнке (pc_orchestrator.run_task)

# Массовое удаление (красное) vs удаление ОДНОГО явного файла (в интерактиве зелёное).
_RE_DEL_TAIL = re.compile(r"(?i)(?:^|[\s;&|(])(?:del|erase|rmdir|rd|rm|remove-item)\b(.*)$")
_RE_DEL_RECURSE = re.compile(r"(?i)(^|\s)(-[a-z]*r[a-z]*|/s|-recurse\w*)(\s|$)")
_RE_SCHTASKS_QUERY = re.compile(r"(?i)\bschtasks\b[^;&|]*\s/query\b")


def is_headless(env=None):
    """Роль ПО ФАКТУ. True ⇔ headless-ребёнок демона (или его субагент): демон штампует
    env PRETOOL_ASK_MARKER каждому спавну (единственный путь — run_task→run_claude), env
    наследуется вниз по дереву процессов. Интерактивные сессии владельца (RC-сессия под
    rc_supervisor, сессии Claude Desktop) этого штампа не имеют. env — инъекция для тестов."""
    e = os.environ if env is None else env
    return bool((e.get(ASK_MARKER_ENV) or "").strip())


def _is_mass_delete(cmd):
    """Массовое удаление ⇔ рекурсивный флаг (-r/-rf/-Recurse//s), маска (*/?) или БОЛЬШЕ ОДНОЙ
    цели. Удаление одного явного файла массовым НЕ считается (доктрина владельца: красное —
    именно «массовые удаления»)."""
    m = _RE_DEL_TAIL.search(cmd or "")
    tail = m.group(1) if m else (cmd or "")
    if "*" in tail or "?" in tail:
        return True
    if _RE_DEL_RECURSE.search(tail):
        return True
    targets = [t for t in re.findall(r"[^\s\"';|&]+", tail)
               if not t.startswith("-") and not t.startswith("/")]
    return len(targets) > 1


def _stays_red(kind, obj, cmd):
    """Доктринальный список (перенос с VPS, действует В ОБЕИХ ролях): что продолжает
    спрашивать Allow. Всё, что сюда не попало, пропускается молча (и пишется в лог)."""
    if kind in ("env", "edit_secret", "read_secret",     # .env и секреты
                "sqlite",                                 # живая БД
                "clasp", "live_sheet",                    # живые таблицы (Лист1 / CRM / Календарь)
                "kill",                                   # остановка процессов
                "git_force",                              # git clean/reset --hard = массовый снос работы
                "network",                                # выход в сеть = канал утечки секретов
                "outside", "write_outside",               # требование п.1: правки ТОЛЬКО внутри репо
                "edit_claude"):                           # иначе сессия молча расширит собственные права
        return True
    if kind == "delete":
        return _is_mass_delete(cmd)
    if kind == "schtasks":
        return not _RE_SCHTASKS_QUERY.search(cmd or "")   # /query — чтение, остальное = контроль задач
    if kind == "py_write":
        return obj in _RED_PY_TOKENS      # боевой токен = красное; «скрипт не прочитан»/«-m X» = неизвестность
    return False                          # unknown и прочее: незнакомое САМО ПО СЕБЕ не красное (доктрина VPS)


def decide_for_role(data, headless):
    """Решение ПО ДОКТРИНЕ (перенос списка с VPS, кейс 314): Allow спрашивает только
    доктринальное (_stays_red) — В ОБЕИХ РОЛЯХ, незнакомая команда сама по себе не красная.
    Раньше headless шёл строгим decide() без послаблений, и чистое чтение (`wc -l` по репо,
    until-grep-ожидание output) роняло задачу демона в NEEDS_APPROVAL. Параметр headless
    решение больше не меняет (оставлен вызывающим: роль нужна логу и каналу ask). Возвращает
    (action, kind, obj); kind/obj сохраняются и при смягчении — они нужны логу."""
    action, kind, obj = decide(data)
    if action != "ask":
        return action, kind, obj
    cmd = ""
    if (data.get("tool_name") or "") in ("Bash", "PowerShell"):
        cmd = (data.get("tool_input") or {}).get("command") or ""
    if _stays_red(kind, obj, cmd):
        return action, kind, obj
    return ("defer", kind, obj)


# ------------------------------- лог прозрачности -----------------------------

_RE_SECRET_VALUE = re.compile(
    r"(?i)([A-Za-z_]*(?:token|api_?key|secret|password|passwd|pwd)[A-Za-z_]*)(\s*[=:]\s*)(\S+)")


def _log_line(role, tool, action, kind, detail):
    """Одна строка лога. Значения токенов/паролей маскируем: лог обязан быть безопасным
    артефактом (сам файл под *.log в .gitignore, но печатать секреты нельзя и локально)."""
    d = _RE_SECRET_VALUE.sub(lambda m: m.group(1) + m.group(2) + "***", (detail or "").replace("\n", " "))
    if len(d) > 300:
        d = d[:300] + "…"
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"{ts} | {role} | {tool or '-'} | {action} | {kind or '-'} | {d}"


def _log(role, tool, action, kind, detail, path=None):
    """Дописать решение в лог. Смягчение не должно стоить прозрачности: пишем ОБЕ роли и ОБА
    решения (defer и ask). Лог вторичен — никогда не роняет решение гарда."""
    p = path or os.getenv("PRETOOL_GUARD_LOG")
    if not p:
        # Под тестом лог уезжает в temp: фикстуры прогона (clasp push / sqlite3 / пустая
        # команда) не имеют права оседать в боевом файле — они неотличимы от настоящих
        # действий владельца и искажают разбор «кто просил Allow» (живой факт 21:39).
        p = log_setup.log_path(GUARD_LOG) if log_setup else GUARD_LOG
    try:
        if log_setup:
            log_setup.rotate_if_needed(p, GUARD_LOG_MAX)
        elif os.path.isfile(p) and os.path.getsize(p) > GUARD_LOG_MAX:
            bak = p + ".1"
            try:
                if os.path.isfile(bak):
                    os.remove(bak)
                os.replace(p, bak)
            except Exception:
                pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(_log_line(role, tool, action, kind, detail) + "\n")
    except Exception:
        pass


# ------------------------------- вывод/пуш -----------------------------------

def _card(kind, obj="", raw_cmd=""):
    """Человеческая карточка: первая строка — ЧТО хочу + зачем, затем (для прозрачности) сырая команда."""
    card = "🔴 " + _human(kind, obj) + " — разрешить?"
    if raw_cmd:
        card += "\nКоманда: " + raw_cmd.strip()
    return card


def _push(card):
    if os.environ.get("PRETOOL_NOPUSH") == "1":
        return  # тест-режим: не слать реальный Telegram
    try:
        subprocess.Popen(
            [VENV_PY, DNOTIFY, card],
            cwd=PROJECT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
    except Exception:
        pass  # пуш вторичен — не роняем решение


MARKER_TOKEN_ENV = "PRETOOL_MARKER_TOKEN"   # токен запуска демона: штампуем им каждую строку карточки,
MARKER_SEP = "\x1f"                          # чтобы демон принимал только карточки СВОЕГО запуска


def _write_marker(mk, card):
    """Дописать красную карточку в файл-маркер headless-сигнала С ДЕДУПОМ и ШТАМПОМ ТОКЕНА.
    Формат строки: '<run_token>\\x1f<строка карточки>'. Токен из env PRETOOL_MARKER_TOKEN —
    задаёт демон на КАЖДЫЙ запуск; так демон отсеивает чужие/старые карточки (напр. фикстуры
    из subprocess-тестов гарда, унаследовавших боевой маркер). Дедуп: одно красное действие
    могло ретраиться → карточка набегала ×N (было ×5)."""
    c = (card or "").strip()
    if not c:
        return
    token = os.environ.get(MARKER_TOKEN_ENV, "")
    block = "\n".join(token + MARKER_SEP + ln for ln in c.splitlines())
    try:
        existing = ""
        if os.path.isfile(mk):
            with open(mk, "r", encoding="utf-8", errors="ignore") as f:
                existing = f.read()
        if block in existing:
            return
        with open(mk, "a", encoding="utf-8") as f:
            f.write(block + "\n")
    except Exception:
        pass


def _emit_ask(card):
    # Сигнал headless→демон (pc_orchestrator): в headless карточку не показать интерактивно,
    # поэтому при заданном env пишем красную карточку в файл-маркер — демон детектит и ставит
    # NEEDS_APPROVAL. В интерактивной сессии env не задан → поведение не меняется.
    mk = os.environ.get(ASK_MARKER_ENV)
    if mk:
        _write_marker(mk, card)
    _push(card)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": card,
    }}, ensure_ascii=False))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # вход не распарсили → штатный flow (defer)
    headless = is_headless()
    role = "headless" if headless else "interactive"
    tool, detail = "", ""
    try:
        tool = data.get("tool_name") or ""
        ti = data.get("tool_input") or {}
        detail = ti.get("command") or ti.get("file_path") or ti.get("notebook_path") or ""
    except Exception:
        pass
    try:
        action, kind, obj = decide_for_role(data, headless)
    except Exception:
        # FAIL-SAFE в ЛЮБОЙ роли: ошибку анализа НЕ смягчаем (иначе сбой гарда = тихий пропуск,
        # а permissions.allow здесь широкий — гард единственный красный гейт).
        action, kind, obj = ("ask", "unknown", "")
    _log(role, tool, action, kind, detail)
    if action == "ask":
        _emit_ask(_card(kind, obj, detail if tool in ("Bash", "PowerShell") else ""))
    sys.exit(0)  # defer


if __name__ == "__main__":
    main()
