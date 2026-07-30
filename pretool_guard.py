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

КАРТОЧКА (правка 2026-07-29). Владелец жал подтверждения не читая — значит они не защищали, а
обесценивали редкую настоящую. Два изменения, оба по одному принципу «судим по ДЕЙСТВИЮ»:
  • ШУМ СНЯТ там, где действие безобидно: `clasp` разведён ПО ПОДКОМАНДЕ (чтение — зелёное;
    `push` зелёный, если прод ЗАКРЕПЛЁН на номере версии — реестр `clasp_prod_pins.json`, под
    git и без правок; `deploy`/`undeploy`/`run` — красное); работа во ВРЕМЕННЫХ каталогах из
    .gitignore (`tmp/`, `%TEMP%\\claude\\**`) удалением не краснеет; `scp`/`sftp` к СВОЕЙ машине
    больше не считается выходом наружу (разбирали первый позиционный аргумент, а это локальный
    источник); `2>&1` и `2>/dev/null` перестали считаться записью в файл.
  • КАЧЕСТВО ОСТАВШИХСЯ: карточка читается за 3 секунды — ЧТО меняется, у какого ОБЪЕКТА, какое
    ЧИСЛО и одна строка ОТКАТА, сырая команда последней и обрезанной. Нет ни объекта, ни числа →
    карточки НЕТ, вместо неё строка в журнал (свод CLAUDE.md п.5): признак сработал на подстроке,
    а не на действии. Hard-блок (`_HARD_CARD` — сбой разбора самого гарда) спрашивает всегда.

ДВЕ ПРАВКИ 30.07.2026 (оба дефекта — про ЦЕНУ подтверждения, детали у самих функций):
  • ОДОБРЕНИЕ ВЛАДЕЛЬЦА ТЕПЕРЬ ДОЕЗЖАЕТ ДО ГАРДА. Кнопка «да» меняла только СТАТУС задачи в
    очереди; новый дочерний claude встречал новый процесс гарда, тот про «да» не знал и краснел
    на том же шаге → демон закрывал задачу ✋failed. Сгорело 7 «да» из 10. Демон штампует ребёнку
    `PRETOOL_APPROVED_KINDS` (виды из карточки, на которую сказали «да»), гард уважает их КАК
    ЗАДАЧУ×КЛАСС: решение `approved` вместо карточки. Другой класс и сбой разбора самого гарда
    не покрываются — см. `decide_for_role` / `owner_approved_kinds`.
  • НАЛИЧИЕ ФАЙЛА ≠ ЕГО СОДЕРЖИМОЕ. `Test-Path .env` (в команде дословно «not reading contents»)
    давал карточку «Хочу обратиться к .env / секретам»: признак `_RE_ENV` бил по УПОМИНАНИЮ пути.
    Проверка наличия/метаданных (`Test-Path`/`ls`/`stat`/`os.path.exists`) больше не красная,
    чтение содержимого (`cat`/`Get-Content`/`open().read()`/`grep`) — красное как было, вид `env`
    остаётся в hard-блоке. См. `_env_probe_only` / `_py_env_probe_only`.

ТРЕТЬЕ УТОЧНЕНИЕ ТОГО ЖЕ КЛАССА (30.07.2026): ЧТЕНИЕ ОКРУЖЕНИЯ ЖИВОГО ПРОЦЕССА ≠ ФАЙЛ .env.
  `python peb_env.py <pid> THINKER_MODEL …` снимает переменные модели из ПАМЯТИ живого процесса
  через PEB (`OpenProcess`+`ReadProcessMemory`) и ни одного байта файла `.env` не открывает, но
  в докстринге скрипта стоит слово «.env» («читаю из PEB, НЕ из файла .env») — и `_scan_python`,
  читая тело, краснел по этому УПОМИНАНИЮ. Тот же развод по ДЕЙСТВИЮ: чтение окружения процесса —
  зелёное (`_py_reads_process_env`), чтение/запись файла `.env` (`open().read()`, `Get-Content`,
  `Select-String -Path .env`, `Set-Content .env`) — красное как было. См. `_py_env_readonly`.

ЧЕТВЁРТОЕ УТОЧНЕНИЕ (30.07.2026): ЧТЕНИЕ БАЗЫ ≠ ЗАПИСЬ В БАЗУ.
  Слово `sqlite3` красило команду ЦЕЛИКОМ, то есть ловило ИМЯ МОДУЛЯ, а не операцию. Живой счёт
  19:38:59–19:39:46: ЧЕТЫРЕ карточки подряд в одной сессии, все на
  `python -c "import sqlite3; con=sqlite3.connect('file:…moderation_ipc.db?mode=ro',uri=True);
  con.execute('select …')"` — соединение открыто READ-ONLY по URI, запрос один-единственный
  `select`. Записать такой командой физически нельзя. Карточка приходила потому, что за
  `import sqlite3` стоит перевод строки, и признак `(^|[\\s;&|(])sqlite3([\\s;&|)]|$)` считал
  упоминание модуля обращением к базе. Тела скриптов при этом УЖЕ судились по оператору
  (`_RE_SQL_WRITE` в `_scan_python`) — по слову судился только текст команды.
  Развели по ДЕЙСТВИЮ, как уже разведены clasp и сеть: чтение (`select`/схема/`mode=ro`/
  читающие dot-команды) — зелёное, запись (`insert|update|delete|drop|alter|create|…`) — красная
  с ИМЕНЕМ БАЗЫ в объекте, неразобранное — красное и так и подписано. См. `_sqlite_decide`.

ЛОГ (`pretool_guard.log`, под *.log в .gitignore): пишется КАЖДОЕ решение обеих ролей —
смягчение не должно стоить прозрачности. Строка: время | роль | инструмент | решение | вид |
команда (обрезана, значения токенов/паролей замаскированы). Решение `journal` — это подавленная
карточка без объекта: действие прошло молча, но след остался.
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

try:  # ВХОД тоже UTF-8: json.load(sys.stdin) брал системную кодовую страницу (на этом ПК cp1251),
    # и любая команда с кириллицей приезжала мохибейком — «эхо ПРОБА» в логе и, что хуже, В САМОЙ
    # КАРТОЧКЕ: владелец видел «Команда: РїРѕР»РѕСЃР°» вместо текста. Класс тот же, что 093119e
    # (cowork_log читает stdin как UTF-8) — правило CLAUDE.md п.9: класс-фикс на ОБЕ полосы.
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT = r"D:\turbobaby-bot"
PROJ_N = os.path.normcase(os.path.normpath(PROJECT))
VENV_PY = os.path.join(PROJECT, "venv", "Scripts", "python.exe")
DNOTIFY = os.path.join(PROJECT, "dispatch_notify.py")

# Клон боевого репозитория сервера на этом ПК. Каталог ЦЕЛИКОМ НЕ открыт и открыт не будет:
# там демон, splinter, devbot и деньги — молчаливая запись туда опаснее нескольких карточек.
_MANAGER_CLONE = r"D:\foreign\turbobaby-manager-bot"

# ── ИСХОДНИКИ САМОГО ГАРДА: явный ПОИМЁННЫЙ список (НЕ маска, НЕ каталог) ──────────────────
# Граница: ЗАПИСЬ В ФАЙЛ НИЧЕГО НЕ ИСПОЛНЯЕТ. Правка этих четырёх файлов ИНСТРУМЕНТОМ записи
# (Edit/Write/MultiEdit) карточки не рождает. ИСПОЛНЕНИЕ не ослаблено ничем: Bash, инлайн `-c`,
# запуск скрипта краснеют как раньше — карве-аут живёт ТОЛЬКО в _decide_write.
#
# Зачем поимённо. Файлы гарда перечисляют имена опасных операций — это их работа. Открывать под
# них каталог нельзя: `D:\foreign\turbobaby-manager-bot` — рабочее дерево боевого сервера.
# Поэтому ровно четыре пути, и добавление пятого — отдельное осознанное решение.
#
# Те же пути продублированы в `.claude/settings.json` (permissions.allow), чтобы список был виден
# В ПРАВИЛАХ, а не только в коде: хук возвращает решение ПОВЕРХ слоя настроек, зелёными должны быть
# ОБА. Расхождение двух списков сторожит тест `test_guard_sources_mirrored_in_settings`.
_GUARD_SOURCES = (
    os.path.join(PROJECT, "pretool_guard.py"),
    os.path.join(PROJECT, "test_pretool_guard.py"),
    os.path.join(_MANAGER_CLONE, "pretool_guard.py"),
    os.path.join(_MANAGER_CLONE, "tests", "test_guard_card_min.py"),
)
_GUARD_SOURCES_N = frozenset(os.path.normcase(os.path.normpath(p)) for p in _GUARD_SOURCES)
GUARD_LOG = os.path.join(PROJECT, "pretool_guard.log")   # под *.log в .gitignore — в репо не уедет
GUARD_LOG_MAX = 2 * 1024 * 1024                          # 2 МБ → ротация (лог не растёт вечно)

try:  # общая ротация + разведение тестового и боевого лога (см. log_setup)
    import log_setup
except Exception:                                        # гард обязан работать даже без модуля
    log_setup = None

# Упоминание `sqlite3` — ЕДИНСТВЕННЫЙ вход в sqlite-разбор (`_sqlite_decide` дороже подстроки,
# гонять его на каждой команде незачем). Константа названа, потому что нужна дважды: в списке
# красных признаков и в хвосте `_decide_bash_body`, где решается судьба неразобранной команды.
_RE_SQLITE_WORD = re.compile(r"(?i)(^|[\s;&|(])sqlite3([\s;&|)]|$)")

# --- КРАСНЫЕ признаки Bash-команды → (regex, kind) ---
_RED_CMD = [
    (re.compile(r"(?i)(^|[\s;&|(])(del|erase|rmdir|rd|rm)([\s;&|)]|$)"), "delete"),
    (re.compile(r"(?i)remove-item\b"), "delete"),
    (re.compile(r"(?i)(^|[\s;&|(])(taskkill|killall|kill|pkill)([\s;&|)]|$)"), "kill"),
    (re.compile(r"(?i)stop-process\b"), "kill"),
    # Остановка сервиса по ИМЕНИ — тот же класс «снять процесс», числа у неё нет по природе.
    # restart|start красными НЕ делаем: это штатный поток (зеркало _GREEN_VERBS полосы сервера).
    (re.compile(r"(?i)(^|[\s;&|(])systemctl\s+(?:--?\S+\s+)*(stop|kill|disable|mask)\b"), "kill"),
    (re.compile(r"(?i)\bschtasks\b"), "schtasks"),
    (re.compile(r"(?i)git\s+push\b.*(--force|(?<![\w-])-f(?![\w]))"), "git_force"),
    (re.compile(r"(?i)git\s+reset\s+--hard"), "git_force"),
    (re.compile(r"(?i)git\s+clean(\s|$)"), "git_force"),
    # `sqlite3` — ПОВОД РАЗОБРАТЬ, а не приговор: вид определяет `_sqlite_decide` по оператору
    # запроса (чтение → зелёное и разбор продолжается, запись → красное, неясное → красное).
    (_RE_SQLITE_WORD, "sqlite"),
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
    """ОБЪЕКТ остановки: PID либо ИМЯ. Имя — полноправный объект: у остановки по имени числа
    нет ПО ПРИРОДЕ (`pkill ngrok`, `systemctl stop nginx`), и раньше такая команда оставалась
    и без объекта, и без числа — то есть уходила в журнал молча."""
    m = (re.search(r"(?i)/pid\s+(\d+)", cmd) or re.search(r"(?i)-id\s+(\d+)", cmd)
         or re.search(r"(?i)\b(?:kill|pkill)\s+(\d+)", cmd))
    if m:
        return "PID " + m.group(1)
    m = re.search(r"(?i)/im\s+([^\s\"']+)", cmd) or re.search(r"(?i)-name\s+([^\s\"']+)", cmd)
    if m:
        return m.group(1)
    m = re.search(r"(?i)\bsystemctl\s+(?:--?\S+\s+)*(?:stop|kill|disable|mask)\s+"
                  r"[\"']?([^\s\"';|&]+)", cmd)
    if m:
        return "сервис " + m.group(1)
    # pkill/killall ПО ИМЕНИ: флаги (-9, -f) пропускаем, объект — первый не-флаговый токен.
    m = re.search(r"(?i)\b(?:pkill|killall)\s+(?:-\S+\s+)*[\"']?([^\s\"';|&]+)", cmd)
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


# Файл базы: .db/.db3/.sqlite/.sqlite3 — в любом виде, в каком его пишут живые команды. Кроме
# голого пути это ещё и URI (`file:D:/turbobaby-bot/moderation_ipc.db?mode=ro`), поэтому знаки
# `:` и `?` границей имени не считаем — их отрезает уже разбор ниже.
_RE_DB_FILE = re.compile(r"(?i)([^\s'\"();,=|&]+\.(?:db|db3|sqlite|sqlite3))(?![\w])")


def _extract_db(cmd):
    """ИМЯ базы для объекта карточки — КОРОТКОЕ и читаемое: последний компонент пути.
    Карточку владелец читает за три секунды, и `D:/turbobaby-bot/moderation_ipc.db` в ней
    занимает строку ради одного значащего слова. Полный путь остаётся в строке «Команда:»."""
    m = _RE_DB_FILE.search(cmd or "")
    if not m:
        return None
    name = re.split(r"[\\/]", m.group(1))[-1]
    return name.split(":")[-1] or None      # `file:moderation_ipc.db` → `moderation_ipc.db`


def _human(kind, obj=""):
    """Человекочитаемая фраза для карточки: ЧТО (+ объект/причина). Без «— разрешить?» (добавит _card)."""
    o = (obj or "").strip()
    tpl = {
        "delete": ("Хочу удалить файл " + o) if o else "Хочу удалить файл(ы)",
        # Остановка сервиса по имени — та же красная зона, но фраза своя: «снять процесс сервис
        # nginx» не читается, а карточку владелец читает за три секунды.
        "kill": ("Хочу остановить " + o) if o.startswith("сервис ") else (
            ("Хочу снять процесс " + o + " (taskkill/kill)") if o
            else "Хочу снять процесс (taskkill/kill)"),
        "schtasks": "Хочу выполнить операцию Планировщика задач (schtasks)",
        "git_force": "Хочу перезаписать git-историю (push --force / reset --hard / clean)",
        "sqlite": ("Хочу записать в базу данных " + o) if o else "Хочу записать в базу данных (sqlite)",
        "clasp": "Хочу выполнить clasp (код Apps Script живых таблиц)",
        "clasp_push": "Хочу залить код в Apps Script (clasp push), пин прода НЕ подтверждён",
        "clasp_deploy": "Хочу ДВИНУТЬ ПРОД Apps Script (clasp deploy/undeploy — живые таблицы)",
        "clasp_run": "Хочу ИСПОЛНИТЬ функцию Apps Script в живом контуре (clasp run)",
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
# SQL-ЗАПИСЬ в ТЕЛЕ скрипта (`_scan_python`). Список операторов сведён с `_SQL_WRITE_OPS` —
# разбором команды и разбором тела обязан править ОДИН перечень, иначе `ALTER TABLE` в теле
# проезжает молча, а в команде краснеет. Формы намеренно SQL-специфичные (`CREATE TABLE`, а не
# голое `CREATE`): голый глагол встречается в обычном питоне (`create_booking`, `def update`).
_RE_SQL_WRITE = re.compile(
    r"(?i)\b(UPDATE|DELETE\s+FROM|INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|"
    r"DROP\s+(?:TABLE|INDEX|VIEW|TRIGGER)|ALTER\s+TABLE|"
    r"CREATE\s+(?:TEMP\s+|TEMPORARY\s+|UNIQUE\s+|VIRTUAL\s+)*(?:TABLE|INDEX|VIEW|TRIGGER))\b")
# Путь конфига Claude Code, упомянутый В КОМАНДЕ шелла (обход Write/Edit-гейта через cp/mv/>).
_RE_CLAUDE_CFG_CMD = re.compile(
    r"(?i)\.claude[\\/](settings[\w.-]*\.json|hooks|agents|commands|plugins|skills)|(?<![\w.])\.claude\.json\b")
# Чистое ЧТЕНИЕ конфига остаётся зелёным. Список смотрелок задан явно: проверять «команда
# выглядит read-only» общим списком нельзя — `echo '{}' > .claude/settings.json` начинается с
# echo и прошёл бы как безобидный, а это перезапись конфига.
_RE_CFG_VIEW = re.compile(r"(?i)^\s*(cat|type|head|tail|more|less|grep|rg|findstr|select-string|"
                          r"get-content|get-item|get-childitem|test-path|ls|dir|git)\b")
_RE_REDIRECT = re.compile(r">>?")
# Перенаправление, которое ДЕЙСТВИТЕЛЬНО пишет файл. `2>&1` (стдерр в стдаут) и `2>/dev/null`
# (в пустоту) файлов не создают — а прежний признак `>>?` считал их записью. Живой счёт за сутки:
# 4 карточки «хочу изменить конфиг Claude» из 4 — все на `ls -la …/.claude/ 2>&1` и
# `grep … .claude/settings.json 2>/dev/null`, то есть на ЧИСТОМ ЧТЕНИИ. Тот же класс, что и всё
# в 8401d33: судим по ДЕЙСТВИЮ, а не по подстроке.
_REDIR_TO_FILE = r">>?\s*(?!&|/dev/null\b|\$null\b|nul(?:[\s;|&]|$))"


# НАМЕРЕНИЕ ЗАПИСИ в конфиг: перенаправление, пишущие командлеты/утилиты, python-запись.
# Именно оно и есть красное; всё остальное — просто смотрение.
_RE_CFG_WRITE = re.compile(
    r"(?i)" + _REDIR_TO_FILE + r"|\bset-content\b|\badd-content\b|\bout-file\b|\bnew-item\b|\bcopy-item\b|"
    r"\bmove-item\b|\bremove-item\b|\btee\b|(^|[\s;&|(])(cp|mv|sed\s+-i|truncate)([\s;&|)]|$)|"
    r"open\s*\([^)]*['\"][wax]|\.write\s*\(|json\.dump\s*\(|\.writelines\s*\(")
# ЧТЕНИЕ конфига: смотрелка ГДЕ УГОДНО в команде (не обязательно первым словом) либо
# python-чтение. Прежняя проверка требовала, чтобы команда НАЧИНАЛАСЬ со смотрелки, и
# `python -c "json.load(open('.claude/settings.json'))"` давал карточку «хочу изменить конфиг» —
# на чистом чтении. Класс тот же, что и всюду сегодня: судим по ДЕЙСТВИЮ, а не по позиции слова.
_RE_CFG_READ = re.compile(
    r"(?i)(^|[\s;&|(])(cat|type|head|tail|more|less|grep|rg|findstr|ls|dir|git)([\s;&|)]|$)|"
    r"\bget-content\b|\bget-item\b|\bget-childitem\b|\bselect-string\b|\btest-path\b|"
    r"json\.load\b|\.read\s*\(|\breadlines\s*\(|\bio\.open\b|\bopen\s*\(")


def _is_pure_config_read(cmd):
    """True ⇔ команда только СМОТРИТ конфиг: есть признак чтения и НЕТ ни одного признака записи.
    Оба условия обязательны — `echo '{}' > .claude/settings.json` начинается с безобидного echo,
    но несёт перенаправление, и остаётся красным."""
    c = cmd or ""
    if _RE_CFG_WRITE.search(c):
        return False
    return bool(_RE_CFG_READ.search(c))
_RE_OUTSIDE_WRITE = re.compile(r"(?i)(>>?|out-file|set-content|new-item|move-item|copy-item)\s+[\"']?([a-z]:[\\/][^\"'\s]+)")

# ------------------------- СЕТЬ: выход наружу против своего канала -----------------------------
# Прежний признак был подстрочный: слово ssh/nc где угодно в строке красило команду. За сутки это
# дало 148 карточек из 245 — почти все на РАБОЧЕМ канале `ssh … root@<свой сервер>`, который и так
# разрешён явным правилом в settings.json, а заодно на `Get-Command ssh`, на `$HOME/.ssh/ключ` и на
# слове «SSH» ВНУТРИ текста записи в журнал. Владелец жал «разрешить» не глядя — это не защита, а
# привычка её игнорировать.
# Сузили по тому же принципу, что и всё сегодня: смотрим ДЕЙСТВИЕ, а не подстроку.
#   • инструмент должен стоять в КОМАНДНОЙ позиции сегмента (структурно, через _cmd_index);
#   • ssh/scp/sftp К СВОЕЙ машине — рабочий канал, вопрос не задаём;
#   • ssh к НЕизвестному хосту, curl/wget/iwr/irm/nc/telnet — как было, красное.
_SSH_TOOLS = {"ssh", "scp", "sftp"}
_OPEN_NET_TOOLS = {"curl", "wget", "iwr", "irm", "nc", "ncat", "telnet",
                   "invoke-webrequest", "invoke-restmethod"}
_SSH_OWN_EXTRA = {"5.223.94.179", "splinter"}     # свой VPS: явно, а не «что найдётся в конфиге»
_OWN_HOSTS_CACHE = []


def _own_ssh_hosts():
    """Свои хосты: явный список + все Host из ~/.ssh/config (их владелец завёл сам)."""
    if _OWN_HOSTS_CACHE:
        return _OWN_HOSTS_CACHE[0]
    hosts = set(_SSH_OWN_EXTRA)
    try:
        cfg = os.path.join(os.path.expanduser("~"), ".ssh", "config")
        if os.path.isfile(cfg):
            with open(cfg, "r", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    p = ln.strip().split()
                    if len(p) >= 2 and p[0].lower() == "host":
                        hosts.update(x.lower() for x in p[1:] if "*" not in x)
    except Exception:
        pass
    _OWN_HOSTS_CACHE.append(hosts)
    return hosts


# Удалённая цель scp/sftp: `[логин@]хост:путь`. Имя хоста — минимум два символа, иначе под
# признак попала бы буква диска Windows (`C:\tmp\x`). Локальный путь (`"$SP/runner.sh"`,
# `/d/turbobaby-bot/x`) двоеточия в этой позиции не имеет и целью не считается.
_RE_SCP_REMOTE = re.compile(r"^(?:[^@/\\:]+@)?([A-Za-z0-9_.\-]{2,}):")
_SSH_FLAG_WITH_VALUE = {"-i", "-o", "-p", "-P", "-l", "-F", "-b", "-c", "-e", "-m", "-w",
                        "-J", "-L", "-R", "-D", "-S", "-Q"}


def _ssh_targets(toks, idx):
    """Хосты из аргументов ssh/scp/sftp. → список (может быть пустым).

    Для `ssh` цель — первый позиционный аргумент. Для `scp`/`sftp` это НЕВЕРНО: первым
    позиционным идёт ЛОКАЛЬНЫЙ ИСТОЧНИК, а хост живёт в аргументе вида `root@хост:/путь`.
    Прежний разбор брал первый позиционный всегда — и на рабочей команде
    `scp -i ~/.ssh/ключ "$SP/файл.py" root@<свой VPS>:/tmp/файл.py` считал «хостом» строку
    `$sp/файл.py`, не находил её в своих → «выход наружу» → карточка. Живой счёт за сутки:
    15 карточек «хочу выйти в сеть» из 15 — все на СВОЁМ канале, который доктрина гарда
    (и явное правило settings.json) считает рабочим и молчаливым."""
    name = _base(toks[idx]) if idx < len(toks) else ""
    positional, i = [], idx + 1
    while i < len(toks):
        t = toks[i]
        if t in _SSH_FLAG_WITH_VALUE:
            i += 2
            continue
        if t.startswith("-") and t != "-":
            i += 1
            continue
        positional.append(t.strip("'\""))
        i += 1
    if name == "ssh":
        return [positional[0].split("@")[-1].split(":")[0].lower()] if positional else []
    hosts = []
    for t in positional:
        m = _RE_SCP_REMOTE.match(t)
        if m:
            hosts.append(m.group(1).lower())
        elif "@" in t and not re.match(r"^[A-Za-z]:[\\/]", t):
            # форма с логином, но разобрать не вышло → считаем ЧУЖИМ (fail-safe: молчать нельзя)
            hosts.append((t.split("@")[-1].split(":")[0] or t).lower())
    return hosts       # пусто = удалённой цели нет (локальное копирование, `ssh -V`) → не сеть


def _net_scan(cmd):
    """→ (вид, цель). Вид: None (сетевой команды нет) | 'own' (ssh/scp/sftp к своей машине) |
    'open' (выход наружу). Цель — хост или, если хоста не разобрать, имя самого инструмента:
    у карточки обязан быть объект, а «инструмент в командной позиции» — объект всегда.
    Разбор структурный: инструмент обязан стоять в КОМАНДНОЙ позиции сегмента."""
    kind, target = None, ""
    for i, seg in enumerate(_split_segments(cmd or "")):
        if i % 2:
            continue
        try:
            toks = shlex.split(seg)
        except Exception:
            toks = seg.split()
        j = _cmd_index(toks)
        if j is None or j >= len(toks):
            continue
        name = _base(toks[j])
        if name in _OPEN_NET_TOOLS:
            return "open", (_extract_host(seg) or name)
        if name in _SSH_TOOLS:
            hosts = _ssh_targets(toks, j)
            foreign = [h for h in hosts if h not in _own_ssh_hosts()]
            if foreign:
                return "open", foreign[0]
            # хостов нет вовсе (`ssh -V`, локальное `scp a b`) — наружу команда не идёт
            kind, target = kind or "own", target or (hosts[0] if hosts else name)
    return kind, target


def _net_cmd_kind(cmd):
    """Совместимая обёртка над _net_scan: только вид, без цели."""
    return _net_scan(cmd)[0]


# ---------------------- clasp: развод по ПОДКОМАНДЕ, а не по имени утилиты ---------------------
# `clasp` целиком считался красным — за сутки это 10 карточек, из которых 6 были ЧТЕНИЕМ
# (`list`, `pull`, `status`, `deployments`, `--version`). Владелец жал их не глядя, и на этом фоне
# единственная настоящая — продвижение прода на новую версию — ничем не выделялась.
#   • читающие подкоманды → зелёное;
#   • `push` заливает код в HEAD скрипт-проекта. Прод-деплой, ЗАКРЕПЛЁННЫЙ на номере версии, от
#     этого не меняется — значит при пиновом проде push зелёный; пин не подтверждён → красное;
#   • `deploy`/`undeploy`/`run` и всё прочее → красное: это выкатка и исполнение в живом контуре.
_CLASP_READ_SUB = {"status", "pull", "versions", "deployments", "logs", "list",
                   "--version", "-v", "help", "--help", "-h"}
_CLASP_META_SUB = {"--version", "-v", "--help", "-h"}
CLASP_PINS = os.path.join(PROJECT, "clasp_prod_pins.json")   # реестр пинов, ПОД git (review+git)


def _to_win_path(p):
    """MSYS/POSIX-путь → путь Windows (`/d/turbobaby-bot/tmp` → `D:\\turbobaby-bot\\tmp`)."""
    s = (p or "").strip().strip("'\"")
    if re.match(r"^/[A-Za-z]/", s):
        s = s[1] + ":" + s[2:]
    return s.replace("/", os.sep)


def _clasp_call(cmd):
    """clasp В КОМАНДНОЙ ПОЗИЦИИ сегмента → (подкоманда, каталог из предшествующего `cd`).
    None — слова `clasp` как команды в строке нет (`which clasp`, `echo "clasp deploy"`,
    путь `~/.clasprc.json`): судим по ДЕЙСТВИЮ, а не по подстроке."""
    cd_hint = ""
    for i, seg in enumerate(_split_segments(cmd or "")):
        if i % 2:
            continue
        try:
            toks = shlex.split(seg)
        except Exception:
            toks = seg.split()
        j = _cmd_index(toks)
        if j is None or j >= len(toks):
            continue
        name = _base(toks[j])
        if name == "cd" and j + 1 < len(toks):
            cd_hint = toks[j + 1].strip("'\"")
            continue
        if name != "clasp":
            continue
        sub = ""
        for t in toks[j + 1:]:
            t = t.strip("'\"")
            if t.startswith("-") and t.lower() not in _CLASP_META_SUB:
                continue
            sub = t.lower()
            break
        return sub, cd_hint
    return None


def _clasp_script_id(cd_hint, cwd):
    """scriptId проекта Apps Script из `.clasp.json` рабочего каталога (учитывая `cd` в самой
    команде). Не нашли — пустая строка: без опознанного проекта пин не подтвердить."""
    for base in (_to_win_path(cd_hint) if cd_hint else "", cwd or PROJECT, PROJECT):
        if not base:
            continue
        try:
            d = base if os.path.isabs(base) else os.path.join(cwd or PROJECT, base)
            with open(os.path.join(d, ".clasp.json"), encoding="utf-8") as f:
                sid = (json.load(f) or {}).get("scriptId") or ""
            if sid:
                return sid
        except Exception:
            continue
    return ""


_PIN_CACHE = {}


def _clasp_prod_pinned(script_id):
    """True ⇔ в реестре `clasp_prod_pins.json` этот scriptId помечен продом, ЗАКРЕПЛЁННЫМ на
    номере версии. Реестр обязан быть ПОД git и БЕЗ незакоммиченных правок — иначе сессия
    расширила бы себе права, дописав пин сама (та же дыра, что закрыта для конфига `.claude`).
    Любой сбой/сомнение → False: непроверенный пин пином не считается."""
    if not script_id:
        return False
    if script_id in _PIN_CACHE:
        return _PIN_CACHE[script_id]
    ok = False
    try:
        if _is_repo_tracked(CLASP_PINS, PROJECT):
            p = subprocess.run(["git", "-C", PROJECT, "status", "--porcelain", "--",
                                os.path.basename(CLASP_PINS)],
                               capture_output=True, text=True, timeout=5, creationflags=_NO_WINDOW)
            if p.returncode == 0 and not (p.stdout or "").strip():
                with open(CLASP_PINS, encoding="utf-8") as f:
                    reg = json.load(f) or {}
                for item in reg.get("projects") or []:
                    if (item.get("script_id") or "") != script_id:
                        continue
                    ver = item.get("pinned_version")
                    ok = isinstance(ver, int) and ver >= 1 and bool(item.get("prod_deployment_id"))
                    break
    except Exception:
        ok = False
    _PIN_CACHE[script_id] = ok
    return ok


def _clasp_decide(cmd, cwd):
    """→ None (clasp не команда) | (kind, obj). ЗЕЛЁНЫЕ виды: clasp_read (читающие подкоманды) и
    clasp_push_pinned (заливка кода при проде, закреплённом на номере версии). КРАСНЫЕ:
    clasp_push (пин не подтверждён), clasp_deploy (выкатка/снятие деплоя), clasp_run
    (исполнение функции в живом контуре), clasp (прочее — login/clone/create/…)."""
    call = _clasp_call(cmd)
    if call is None:
        return None
    sub, cd_hint = call
    sid = _clasp_script_id(cd_hint, cwd)
    tail = ("…" + sid[-8:]) if sid else "проект не опознан"
    obj = "clasp %s · %s" % (sub or "(без подкоманды)", tail)
    if sub in _CLASP_READ_SUB:
        return ("clasp_read", obj)
    if sub == "push":
        return ("clasp_push_pinned", obj) if _clasp_prod_pinned(sid) else ("clasp_push", obj)
    if sub in ("deploy", "undeploy", "redeploy"):
        return ("clasp_deploy", obj)
    if sub == "run":
        return ("clasp_run", obj)
    return ("clasp", obj)

# ---------------- sqlite: развод по ОПЕРАТОРУ ЗАПРОСА, а не по слову `sqlite3` -----------------
# `sqlite3` целиком стоял в «спрашивать всегда» — и это ловило не действие, а ИМЯ МОДУЛЯ. За одну
# сессию 30.07.2026 (19:38:59, 19:39:17, 19:39:32, 19:39:46) это дало ЧЕТЫРЕ карточки подряд на
# `select` к базе, открытой `mode=ro`: записать таким соединением нельзя в принципе. Признак
# срабатывал на `import sqlite3`, потому что за словом стоит перевод строки.
#
# Тела скриптов ГАРД УЖЕ судил по оператору (`_RE_SQL_WRITE` в `_scan_python`) — по слову судился
# только ТЕКСТ КОМАНДЫ. Здесь текст команды доводится до того же стандарта:
#   ЧТЕНИЕ  (select / explain / with-select / читающая pragma / .tables/.schema/.dump / mode=ro)
#           → зелёное, вид `sqlite_read`, и разбор команды ПРОДОЛЖАЕТСЯ: красное всего
#           остального (env, .claude, запись вне репо, боевые токены питона) не ослаблено;
#   ЗАПИСЬ  (insert/update/delete/drop/alter/create/replace/vacuum/attach/пишущая pragma,
#           .import/.restore/.clone/.read/.load) → красное, ИМЯ БАЗЫ в объекте карточки;
#   НЕЯСНО  → красное, и в объекте так и написано «оператор не разобран».
#
# ЧЕСТНАЯ ГРАНИЦА РАЗБОРА (требование ТЗ: «не выходит надёжно — брать по первому оператору и
# сказать об этом»). Полного SQL-парсера в хуке нет и не будет. Утверждения режутся по `;` и
# переводу строки, вид берётся ПО ПЕРВОМУ СЛОВУ-ОПЕРАТОРУ. Доразбирается ровно два случая, где
# первое слово врёт: `WITH …` (в sqlite CTE умеет нести INSERT/UPDATE/DELETE) и `PRAGMA …=…`
# (присваивание МЕНЯЕТ настройку базы). Смесь «select + insert» — это ЗАПИСЬ: любой пишущий
# оператор в любом утверждении красит команду целиком.
#
# ЧТО ОСТАЛОСЬ КРАСНЫМ ПО УМОЛЧАНИЮ (доктрина «неизвестное у ЖИВОЙ базы спрашивает»):
# интерактивный вход `sqlite3 <база>` без запроса (в шелле можно набрать что угодно), SQL,
# собранный из переменной (`c.execute(q)`), незнакомая dot-команда. Ошибка разбора может
# ДОБАВИТЬ подтверждение, но не пропустить запись.
_RE_SQLITE_EXEC = re.compile(r"(?i)\.\s*execute(?:script|many)?\s*\(")
_RE_SQLITE_CONNECT = re.compile(r"(?i)\bsqlite3?\s*\.\s*connect\s*\(")
# Соединение, которым ЗАПИСАТЬ НЕЛЬЗЯ: URI-режим `mode=ro` и CLI-флаг `-readonly`.
_RE_SQLITE_RO = re.compile(r"(?i)mode\s*=\s*ro(?![\w])|(^|[\s\"'])--?readonly(?![\w])")

_SQL_READ_OPS = frozenset(("select", "explain", "values", "pragma", "with"))
_SQL_WRITE_OPS = frozenset((
    "insert", "replace", "update", "delete", "drop", "alter", "create", "truncate",
    "vacuum", "reindex", "attach", "detach", "analyze",
    # транзакционные слова: сами по себе данных не меняют, но открывают запись — красим их,
    # потому что рядом всегда стоит то, ради чего транзакцию и начали
    "begin", "commit", "rollback", "savepoint", "release"))
# Читающие dot-команды CLI — БЕЛЫЙ и поимённый список (тот же приём, что `_EXISTS_CMDS` и
# `_CLASP_READ_SUB`). Всё, чего здесь нет, красное: `.import`/`.restore`/`.clone` пишут в базу,
# `.read`/`.load`/`.shell`/`.system` исполняют, `.output`/`.once`/`.save`/`.backup` пишут файл.
_SQLITE_READ_DOT = frozenset((
    ".tables", ".schema", ".fullschema", ".databases", ".dbinfo", ".indexes", ".indices",
    ".dump", ".show", ".headers", ".mode", ".nullvalue", ".separator", ".width", ".stats",
    ".changes", ".echo", ".print", ".help", ".quit", ".exit", ".version", ".timeout",
    ".prompt", ".bail", ".explain"))
# Первое слово утверждения. Хвост `(?=[\s;]|$)` обязателен: без него `print(` читалось бы как
# оператор `print`, а `delete(x)`/`update = 5` — как SQL. У настоящего SQL за оператором стоит
# пробел (`select 1`, `delete from …`) либо конец утверждения (`vacuum`, `commit;`).
_RE_SQL_FIRST_WORD = re.compile(r"^[\s(\\'\"`\[]*([A-Za-z_]+)(?=[\s;]|$)")
_RE_CTE_WRITE = re.compile(r"(?i)\b(?:insert|update|delete|replace)\b")
_RE_SQL_TABLE = re.compile(r"(?i)\b(?:into|from|table|update)\s+[\"'`\[]?([\w.]+)")


def _quoted_runs(text):
    """Команда, разрезанная по кавычкам ОБОИХ типов → список кусков.

    ПОЧЕМУ РЕЖЕМ, А НЕ ИЩЕМ «строки в кавычках». Живая форма — `python -c " … con.execute(\\"select
    key … from meta\\") … "`, где внешняя кавычка `-c` и внутренние кавычки литерала ЧЕРЕДУЮТСЯ.
    Пара «открыл-закрыл» на такой строке ловит ПРОМЕЖУТКИ (` con.execute(`), а сам литерал
    оказывается между закрывающей и следующей открывающей — то есть теряется целиком (проверено:
    на живой команде 19:38:59 парный разбор `select` не находил). Разрез отдаёт и то, и другое.
    Экранирование `\\"`/`\\'` снимается заранее — именно в таком виде литерал и приезжает.

    Лишние куски (обычный текст, куски вне кавычек) безвредны: вид утверждения определяет
    `_sql_ops` по ПЕРВОМУ СЛОВУ-ОПЕРАТОРУ, и проза оператором не становится. Плата за полноту —
    редкое лишнее подтверждение (`echo "update available"` рядом с обращением к базе), то есть
    ошибка в безопасную сторону, как и вся доктрина гарда."""
    s = (text or "").replace('\\"', '"').replace("\\'", "'")
    out = []
    for q in ("'", '"'):
        out.extend(s.split(q))
    return out


def _sql_ops(text):
    """Виды операторов в тексте → подмножество {'read','write'}. Пусто = SQL не опознан.
    Разбор ПО ПЕРВОМУ СЛОВУ утверждения (см. «честная граница» выше)."""
    ops = set()
    for stmt in re.split(r"[;\n]", text or ""):
        m = _RE_SQL_FIRST_WORD.match(stmt)
        if not m:
            continue
        w = m.group(1).lower()
        if w in _SQL_WRITE_OPS:
            ops.add("write")
        elif w == "with":
            ops.add("write" if _RE_CTE_WRITE.search(stmt) else "read")
        elif w == "pragma":
            ops.add("write" if "=" in stmt else "read")   # `pragma x=y` меняет настройку базы
        elif w in _SQL_READ_OPS:
            ops.add("read")
    return ops


def _sqlite_cli(cmd):
    """`sqlite3` В КОМАНДНОЙ ПОЗИЦИИ сегмента → список позиционных аргументов (без флагов).
    None — слова `sqlite3` как команды в строке нет (`import sqlite3`, `Get-Command sqlite3`,
    имя модуля в тексте): судим по ДЕЙСТВИЮ, а не по подстроке — тот же разбор, что у clasp."""
    for i, seg in enumerate(_split_segments(cmd or "")):
        if i % 2:
            continue
        try:
            toks = shlex.split(seg)
        except Exception:
            toks = seg.split()
        j = _cmd_index(toks)
        if j is None or j >= len(toks):
            continue
        if _base(toks[j]) != "sqlite3":
            continue
        return [t.strip("'\"") for t in toks[j + 1:] if not t.startswith("-")]
    return None


def _sqlite_decide(cmd):
    """→ None (обращения к базе в команде нет) | ('sqlite_read', база) | ('sqlite', объект).

    None — это «слово есть, действия нет»: `import sqlite3` без запроса, `Get-Command sqlite3`,
    имя модуля в строке журнала. Такая команда возвращается в общий разбор нетронутой.
    Объект красного вида — ИМЯ БАЗЫ; если базу не назвали, берём таблицу из запроса, а если и
    оператор не разобрался — так и подписываем, чтобы карточка не притворялась точной."""
    c = cmd or ""
    args = _sqlite_cli(c)
    if args is None and not _RE_SQLITE_EXEC.search(c) and not _RE_SQLITE_CONNECT.search(c):
        return None
    db = _extract_db(c) or ""
    ops, dots = set(), []
    for run in _quoted_runs(c):
        ops |= _sql_ops(run)
    for a in args or []:
        if a.startswith("."):
            dots.append(a.split()[0].lower())
        else:
            ops |= _sql_ops(a)          # незакавыченный запрос/аргумент CLI
    if any(d not in _SQLITE_READ_DOT for d in dots):
        return ("sqlite", db or "dot-команда " + dots[0])
    if "write" in ops:
        if not db:
            m = _RE_SQL_TABLE.search(c)
            db = ("таблица " + m.group(1)) if m else ""
        return ("sqlite", db)
    if "read" in ops or dots or _RE_SQLITE_RO.search(c):
        return ("sqlite_read", db)
    return ("sqlite", (db + " · " if db else "") + "оператор не разобран")


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


def _is_guard_source(path):
    """ТОЧНОЕ совпадение с одним из `_GUARD_SOURCES`. Ни маски, ни префикса каталога: сосед по
    каталогу (`D:\\foreign\\turbobaby-manager-bot\\bot.py`) сюда НЕ попадает."""
    if not path:
        return False
    try:
        ap = path if os.path.isabs(path) else os.path.join(PROJECT, path)
        return os.path.normcase(os.path.normpath(ap)) in _GUARD_SOURCES_N
    except Exception:
        return False


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


# ВРЕМЕННЫЕ каталоги, которых нет в git: `tmp/` внутри репозитория (.gitignore:60) и весь
# `%TEMP%\claude\**` (скретчпад сессии и соседние рабочие каталоги harness'а). Работа в них —
# это работа сессии со СВОИМИ черновиками: они не едут в историю, не видны прод-контуру и
# живут один сеанс. Подтверждение на них ничего не защищает.
_RE_TEMP_CLAUDE = re.compile(r"(?i)(^|[\\/])(temp|tmp)[\\/]claude[\\/]")


def _is_temp_zone(path):
    """True ⇔ путь лежит во временной зоне из .gitignore. Неразрешимый путь → False (fail-safe:
    непонятное не считаем временным). `..` в пути снимает доверие целиком — иначе
    `tmp/../suggest.py` проехал бы как «временный»."""
    s = (path or "").strip().strip("'\"").replace("\\", "/")
    if not s or ".." in s.split("/"):
        return False
    if _RE_TEMP_CLAUDE.search(s):
        return True                            # в т.ч. с неразвёрнутым $LOCALAPPDATA/%TEMP%
    if s.startswith("$") or s.startswith("%") or s.startswith("~"):
        return False                           # переменную не развернуть → не угадываем
    try:
        ap = s if os.path.isabs(s) or re.match(r"^[A-Za-z]:", s) else os.path.join(PROJECT, s)
        if re.match(r"^/[A-Za-z]/", ap):       # MSYS-форма /d/turbobaby-bot/… → D:\turbobaby-bot\…
            ap = ap[1] + ":" + ap[2:]
        ap = os.path.normcase(os.path.normpath(ap))
    except Exception:
        return False
    if not _inside_project(ap):
        return False
    rel = os.path.relpath(ap, PROJ_N).split(os.sep)
    return bool(rel) and rel[0] == "tmp"


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
    """Зоны ВНЕ репо, где запись не требует подтверждения: временные каталоги harness'а
    (`%TEMP%\\claude\\**`, включая скретчпад) и хранилище памяти агента. Оба — рабочая зона самой
    сессии, не вектор эскалации. Живой факт аудита 22:27: 6 из 20 последних Allow были про них."""
    return _is_scratchpad(path) or _is_temp_zone(path) or _is_memory_store(path)


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
        if _looks_like_secret_arg(t) or _RE_ARG_EXEC.search(t):
            kept.append(t)   # путь к секрету и подстановка команды обязаны остаться видимыми скану
    return " ".join(kept) if cut else cmd


# Аргумент, который САМ исполняет команду ($(…), `…`, ${…}), вырезать нельзя: это не данные,
# а операция. Иначе `cclog.py "$(rm -rf …)"` спрятал бы удаление за видом текста записи.
_RE_ARG_EXEC = re.compile(r"\$\(|`|\$\{")


def _looks_like_secret_arg(tok):
    """Аргумент — ПУТЬ к секрету, а не проза, где слово просто встретилось.
    `.env`, `../.env`, `C:/proj/.env`, `x.session` → да (вырезать нельзя);
    `DONE: правил .env и настройки` → нет (пробелы ⇒ это текст записи, а не путь)."""
    t = (tok or "").strip().strip("'\"")
    if not t or any(c.isspace() for c in t):
        return False
    return _is_secret_path(t) or bool(_RE_ENV.search(t))


# --------------------- разбор команды для СКАН-ТЕКСТА (порт VPS 25.07.2026) -------------------
# Класс один: БОЕВЫЕ СЛОВА В ДАННЫХ — это не операция. Скан-текст строится посегментно; сегменты
# сохраняются ЦЕЛИКОМ (иначе `python x.py && rm -rf D:\turbobaby-bot` спрятал бы удаление в
# вырезанном хвосте), а ВНУТРИ каждого вырезаются именно данные: позиционные аргументы
# .py-скрипта (уже было), текст `git -m` и поисковый шаблон grep/rg/sed/awk/Select-String.

_RE_ENV_ASSIGN = re.compile(r"^\w+=")      # env-префикс VAR=val перед именем команды
_WRAPPERS = {"sudo", "doas", "env", "nohup", "nice", "ionice", "time", "timeout",
             "stdbuf", "xargs", "systemd-run",
             # Ключевые слова шелла тоже стоят ПЕРЕД командой. Без них структурный разбор не
             # видел `until curl …; do sleep 5; done` — команда цикла пряталась за словом until,
             # и выход в сеть внутри ожидания переставал краснеть. Поймано существующим тестом
             # test_until_wait_does_not_whitelist_red при сужении сетевого признака 25.07.2026.
             "until", "while", "if", "then", "do", "done", "else", "elif", "!",
             "exec", "command"}
# Поисковые команды. К серверному набору добавлены ПК-специфичные Select-String / sls / findstr:
# на этой машине основной шелл PowerShell, и ищут обычно ими.
_SEARCH_CMDS = {"grep", "egrep", "fgrep", "zgrep", "rg", "ag", "ack", "sed", "awk", "gawk",
                "mawk", "select-string", "sls", "findstr"}
_PATTERN_FLAGS = {"-e", "-E", "-n", "--regexp", "--expression", "-pattern"}
# Флаги, чьё значение — ФАЙЛ, а не шаблон. Без них PowerShell-форма
# `Select-String -Path .env -Pattern TOKEN` теряла `.env` из скан-текста: первый позиционный
# аргумент считался шаблоном и вырезался вместе с путём к секрету — и ЧТЕНИЕ .env проезжало
# зелёным (проверено на HEAD до этой правки). Докрутка того же правила, что уже записано выше:
# операнды-файлы не трогаем.
_PATH_FLAGS = {"-path", "-literalpath", "-lp"}
_EXEC_IN_PATTERN = re.compile(
    r"\$\(|`|\bsystem\s*\(|\bpopen\s*\(|\|\s*['\"]?\s*(?:sh|bash|zsh|xargs)\b")


def _base(tok):
    """Имя команды без пути, кавычек и .exe, нижним регистром (C:\\bin\\grep.exe → grep)."""
    name = os.path.basename((tok or "").strip("'\"")).lower()
    return name[:-4] if name.endswith(".exe") else name


def _split_segments(cmd):
    """Команда → [сегмент, разделитель, сегмент, …] С УВАЖЕНИЕМ К КАВЫЧКАМ. Контракт как у
    прежнего _RE_SHELL_SEP.split (чётные — сегменты, нечётные — разделители дословно), но
    разделитель ВНУТРИ кавычек больше не режет.

    Живой провал 25.07.2026: запись в журнал
    `python cowork_log_append.py "DONE …, убраны os.remove и rm -rf …; демон 79694 active"`
    рвалась по точке с запятой ВНУТРИ текста записи. Кавычка оставалась непарной, shlex падал,
    аргумент переставал вырезаться — и гард видел «rm -rf» рядом с путём репозитория. Владелец
    не мог записать в журнал строку, где эти слова просто УПОМЯНУТЫ.

    `&` разделителем НЕ считаем — на ПК это оператор вызова PowerShell (`& "C:\\…\\claude.exe"`),
    а не связка команд. Дыры это не создаёт: не разрезанный кусок сканируется целиком."""
    out, buf, q, i, n = [], [], None, 0, len(cmd or "")
    while i < n:
        ch = cmd[i]
        if q:
            buf.append(ch)
            if ch == "\\" and q == '"' and i + 1 < n:
                buf.append(cmd[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
            i += 1
            continue
        if ch in "'\"":
            q = ch
            buf.append(ch)
            i += 1
            continue
        if cmd[i:i + 2] in ("&&", "||"):
            out.append("".join(buf))
            out.append(cmd[i:i + 2])
            buf = []
            i += 2
            continue
        if ch in ";|\n":
            out.append("".join(buf))
            out.append(ch)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


def _cmd_index(toks):
    """Индекс слова-КОМАНДЫ сегмента: пропускает env-префикс (VAR=val) и обёртки
    (sudo/env/timeout N/…). None — команды в сегменте нет. Разбор СТРУКТУРНЫЙ, а не по подстроке:
    `cat pretool_guard.log` поисковой командой не станет от того, что рядом есть слово grep."""
    i, hops = 0, 0
    while i < len(toks) and _RE_ENV_ASSIGN.match(toks[i]):
        i += 1
    while i < len(toks) and hops < 4:
        name = _base(toks[i])
        if name not in _WRAPPERS:
            return i
        i += 1
        while i < len(toks) and toks[i].startswith("-"):
            i += 1
        if name in ("timeout", "nice", "ionice") and i < len(toks) \
                and re.match(r"^[\d.]+[smhd]?$", toks[i]):
            i += 1
        while i < len(toks) and _RE_ENV_ASSIGN.match(toks[i]):
            i += 1
        hops += 1
    return i if i < len(toks) else None


def _strip_git_msg(seg):
    """Текст `git commit -m "…"` — ДАННЫЕ, а не операция (нюанс bd5d516 на VPS). Сообщение
    коммита несёт боевые слова В ТЕКСТЕ: «убрал rm -rf из скрипта» — это описание правки, а не
    удаление. Вырезаются payload'ы -m/-am/--message во всех формах (-m txt, --message=txt,
    приклеенное -mtxt). Не git или сбой разбора → сегмент КАК ЕСТЬ (fail-safe: скан полный,
    `git commit -m "x" && rm -rf y` ловится вторым сегментом)."""
    try:
        toks = shlex.split(seg)
    except Exception:
        return seg
    i0 = 0
    while i0 < len(toks) and _RE_ENV_ASSIGN.match(toks[i0]):
        i0 += 1
    if i0 >= len(toks) or _base(toks[i0]) != "git":
        return seg
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in ("-m", "-am", "--message"):
            out.append(t)
            i += 2
            continue
        if t.startswith("--message="):
            out.append("--message")
            i += 1
            continue
        if re.match(r"^-a?m.", t):        # приклеенный payload: -mтекст / -amтекст
            out.append("-m")
            i += 1
            continue
        out.append(t)
        i += 1
    return " ".join(out)


def _strip_search_pattern(toks, idx):
    """→ (токены БЕЗ поискового шаблона, вырезанные). Шаблон = аргумент -e/-E/-n/-Pattern либо
    ПЕРВЫЙ позиционный у поисковой команды. Операнды-ФАЙЛЫ не трогаем — иначе
    `grep -n TOKEN .env` перестал бы блокироваться. Шаблон с признаком ИСПОЛНЕНИЯ
    ($(…) / `…` / system( / | sh) не вырезаем: это уже не данные."""
    if idx is None or idx >= len(toks) or _base(toks[idx]) not in _SEARCH_CMDS:
        return toks, []
    drop, pat_seen, i = set(), False, idx + 1
    while i < len(toks):
        t = toks[i]
        if t.lower() in _PATH_FLAGS and i + 1 < len(toks):
            i += 2            # значение -Path/-LiteralPath — ОПЕРАНД-ФАЙЛ: сохраняем скану
            continue
        if t.lower() in _PATTERN_FLAGS and i + 1 < len(toks):
            drop.add(i + 1)
            pat_seen = True
            i += 2
            continue
        if t.startswith("-") and t != "-":
            i += 1
            continue
        if not pat_seen:
            drop.add(i)
            pat_seen = True
        i += 1
    keep, dropped = [], []
    for k, t in enumerate(toks):
        if k in drop and not _EXEC_IN_PATTERN.search(t):
            dropped.append(t)
        else:
            keep.append(t)
    return keep, dropped


def _mask(seg, dropped):
    """Убрать шаблоны из ТЕКСТА сегмента (в кавычках или без), сохранив всё прочее ДОСЛОВНО —
    red-скан не должен слабеть от переклейки токенов (на Windows shlex ест «\\» в путях).
    Форма не нашлась → текст как есть, то есть краснее."""
    out = seg
    for d in dropped:
        for form in ('"' + d + '"', "'" + d + "'", d):
            if d and form in out:
                out = out.replace(form, " ", 1)
                break
    return out


def _scan_text(cmd):
    """Текст для поиска КРАСНЫХ признаков. Исполняется всегда ИСХОДНАЯ команда — правится только
    то, по чему ищем. Внутри сегмента порядок: текст git -m → аргументы .py-скрипта → поисковый
    шаблон. Сегменты и разделители сохраняются, чтобы соседний кусок цепи остался под сканом."""
    if not cmd:
        return cmd
    out = []
    for i, p in enumerate(_split_segments(cmd)):
        if i % 2:
            out.append(p)                       # разделитель — дословно
            continue
        clean = _strip_script_cli_args(_strip_git_msg(p))
        try:
            toks = shlex.split(clean)
        except Exception:
            out.append(clean)                   # кривое квотирование → сегмент как есть (краснее)
            continue
        _keep, dropped = _strip_search_pattern(toks, _cmd_index(toks))
        out.append(_mask(clean, dropped))
    return "".join(out)


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


def _scan_python(cmd, cwd, env_probe=False):
    """→ ('defer','','') | ('ask','py_write',detail). Инлайн -c и тела .py-целей, ищем боевую запись.
    env_probe — вердикт ШЕЛЛОВОГО разбора («секрет в самой команде упомянут только пробой
    наличия», см. `_env_probe_only`): он снимает красное ТОЛЬКО с упоминания В КОМАНДЕ, тело
    скрипта проверяется отдельно и своей проверкой."""
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
    # скан-текст: позиционные аргументы скрипта — данные, не операция (_scan_text, посегментно)
    cmd_scan = _scan_text(cmd)
    blob = cmd_scan + "\n" + content
    env_hit = _RE_ENV.search(blob)
    # `.env` в коде — не всегда обращение к секрету (см. `_py_env_readonly`): проверка НАЛИЧИЯ
    # (os.path.exists/stat) и чтение ОКРУЖЕНИЯ ЖИВОГО ПРОЦЕССА из PEB (ReadProcessMemory) ни байта
    # файла не открывают, а имя `.env` в их докстринге — прояснение «НЕ из файла .env». Два
    # источника упоминания судим ПОРОЗНЬ, иначе проба в команде прикрыла бы чтение в теле скрипта:
    # `Test-Path .env; python evil.py`, где .env читает evil.py, обязан остаться красным.
    # Признак ПРОБЫ не завершает разбор: остальные красные токены (боевая запись Bridge, живые
    # таблицы, SQL) проверяются ниже как раньше — послабление касается ТОЛЬКО вида `env`.
    if env_hit:
        cmd_ok = (not _RE_ENV.search(cmd_scan)) or env_probe or _py_env_readonly(cmd_scan)
        body_ok = (not _RE_ENV.search(content)) or _py_env_readonly(content)
        if not (cmd_ok and body_ok):
            # объект называем ДОСЛОВНО найденным именем: у карточки .env объект обязан быть, иначе
            # правило «нет объекта → журнал» проглотило бы её (тело скрипта в команде не видно)
            return ("ask", "env", env_hit.group(0).strip("'\" "))
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
    return ("defer", ("env_probe" if env_hit else ""), "")


# ---------------- НАЛИЧИЕ ФАЙЛА ≠ ЕГО СОДЕРЖИМОЕ (правка 30.07.2026) -------------------------
# `_RE_ENV` красит команду по УПОМИНАНИЮ пути секрета и не различает «читаю содержимое» и
# «проверяю, есть ли файл». Цена — живой факт: задача 55, УЖЕ ОДОБРЕННАЯ владельцем, умерла в
# 15:09 на `Test-Path "D:\turbobaby-bot\.env"`, где в самой команде стояло «not reading
# contents». Проверка наличия не выдаёт ни одного байта секрета: секрет — это СОДЕРЖИМОЕ, а факт
# существования файла виден и из `git status`, и из любого листинга каталога.
# Разводим ПО ДЕЙСТВИЮ (свод CLAUDE.md п.5): наличие/метаданные → зелёное, содержимое → красное.
#
# Список смотрелок наличия — БЕЛЫЙ и поимённый (как `_RE_CFG_VIEW` выше): «команда выглядит
# безобидной» общим признаком проверять нельзя. Всё, чего в списке нет, остаётся красным.
_EXISTS_CMDS = {"test-path", "test", "[", "[[", "ls", "dir", "stat",
                "get-item", "gi", "get-childitem", "gci", "resolve-path"}
# Команда, которая только ПЕЧАТАЕТ свой аргумент: путь в её тексте — ДАННЫЕ, а не операция (тот
# же класс, что вырезание текста `git -m` и аргументов .py-скрипта). Живой факт задачи 55: имя
# секрета попало в карточку из строки `Write-Output "=== .env exists (not reading contents) ==="`,
# то есть из ПОДПИСИ К ВЫВОДУ. Перенаправление в файл проверяется отдельно и ниже: `echo … > .env`
# остаётся красным.
_PRINT_CMDS = {"echo", "printf", "write-output", "write-host", "write-debug", "write-verbose",
               "write-information", "write-warning"}
# python-форма того же: проверка наличия/метаданных…
_RE_PY_EXISTS = re.compile(
    r"(?i)os\.path\.(?:exists|isfile|isdir|islink|lexists|getsize|getmtime)\s*\(|"
    r"os\.(?:stat|lstat)\s*\(|\.exists\s*\(\s*\)|\.is_file\s*\(\s*\)|\.is_dir\s*\(\s*\)|"
    r"\.stat\s*\(\s*\)")
# …и ЛЮБОЙ признак, что кодом трогают не факт файла, а его содержимое/окружение/саму сущность
# (чтение, запись, переименование, копирование, dotenv, окружение, вызов шелла). Одно совпадение
# отменяет послабление целиком — это НЕ список «что красное», а список «доказательства, что это
# уже не проба наличия».
# `\.read\b` (а не голое `\.read`): метод-чтение файла `f.read()`/`.read ` ловим, но `.Read` с
# продолжением слова НЕ ловим — иначе `ReadProcessMemory` (чтение памяти ЖИВОГО процесса, а не
# файла .env) ложно считался бы файловым чтением. `.readline/.readlines/.read_text/.read_bytes`
# перечислены отдельно и остаются красными. `\.write` НЕ сужаем: `WriteProcessMemory` — мутация,
# ей краснеть правильно.
_RE_PY_NOT_PROBE = re.compile(
    r"(?i)\bopen\s*\(|\.read\b|\.write|readline|readlines|read_text|read_bytes|"
    r"os\.(?:rename|replace|remove|unlink|truncate|chmod|chown|system|popen)|"
    r"\bshutil\.|\bdotenv\b|\bload_env\b|os\.environ|\bgetenv\b|\bfileinput\b|\blinecache\b|"
    r"\bmmap\b|\bsubprocess\b|\bPopen\b|\bexec\s*\(|\beval\s*\(|\b__import__\b")
# ЧТЕНИЕ ОКРУЖЕНИЯ ЖИВОГО ПРОЦЕССА из PEB — это НЕ файл секретов. OpenProcess(QUERY|VM_READ) +
# NtQueryInformationProcess + ReadProcessMemory снимают переменные окружения из ПАМЯТИ процесса
# (модель/эффорт живого userbot/демона), ни одного байта файла `.env` при этом не открывая.
# Позитивный, поимённый маркер (как `_RE_PY_EXISTS` для проверки наличия): доказывает, что
# упоминание `.env` в докстринге/комментарии такого скрипта — прояснение «читаю из PEB, НЕ из
# файла .env», а не обращение к секрету.
_RE_PY_PROC_ENV = re.compile(
    r"(?i)\bReadProcessMemory\b|\bNtQueryInformationProcess\b|\bPROCESS_VM_READ\b")


def _py_env_probe_only(code):
    """True ⇔ в python-коде путь секрета встречается ТОЛЬКО в проверке наличия/метаданных
    (`os.path.exists`, `os.stat`, `Path(...).exists()`) и НЕТ ни одного признака обращения к
    содержимому/окружению (`_RE_PY_NOT_PROBE`). Нет ни одной проверки наличия → False: послабление
    даётся за ДОКАЗАННУЮ пробу, а не за отсутствие улик."""
    c = code or ""
    if not _RE_ENV.search(c) or _RE_PY_NOT_PROBE.search(c):
        return False
    return bool(_RE_PY_EXISTS.search(c))


def _py_reads_process_env(code):
    """True ⇔ python-код читает ОКРУЖЕНИЕ ЖИВОГО ПРОЦЕССА из PEB (`ReadProcessMemory` +
    `NtQueryInformationProcess`/`PROCESS_VM_READ`), а не файл `.env`. Память процесса — не
    секретный файл: ни одного его байта такое чтение не открывает, поэтому упоминание `.env`
    в докстринге/комментарии («читаю из PEB, НЕ из файла .env») секретом не является.
    Любой признак файлового доступа/мутации (`_RE_PY_NOT_PROBE`: open/read-файла/dotenv/
    os.environ/subprocess/…) → False: одна улика обращения к файлу отменяет послабление целиком."""
    c = code or ""
    if not _RE_ENV.search(c) or _RE_PY_NOT_PROBE.search(c):
        return False
    return bool(_RE_PY_PROC_ENV.search(c))


def _py_env_readonly(code):
    """True ⇔ `.env` в python-коде — ЧИСТОЕ ЧТЕНИЕ, не выдающее ни байта файла секретов:
    либо проверка НАЛИЧИЯ/метаданных (`_py_env_probe_only`), либо чтение ОКРУЖЕНИЯ ЖИВОГО
    ПРОЦЕССА из PEB (`_py_reads_process_env`). Оба — доказанная read-only-операция; обе несут
    один и тот же fail-safe: любой признак файлового доступа (`_RE_PY_NOT_PROBE`) → False."""
    return _py_env_probe_only(code) or _py_reads_process_env(code)


def _env_probe_only(cmd):
    """True ⇔ путь секрета в команде встречается ТОЛЬКО в проверке НАЛИЧИЯ/метаданных.
    Разбор ПОСЕГМЕНТНЫЙ и структурный (`_split_segments` + `_cmd_index`) — по КОМАНДНОЙ позиции,
    а не по подстроке. Fail-safe: любое сомнение → False, то есть красное как было. Сомнением
    считаем:
      • труба ИЗ сегмента с секретом (`Get-Item .env | Get-Content` вернул бы содержимое);
      • любое перенаправление в этом сегменте (`ls > .env` секрет бы ПЕРЕЗАПИСАЛ);
      • команда сегмента не из белого списка `_EXISTS_CMDS` (в т.ч. присваивание `$p = ".env"`,
        после которого содержимое читает уже другой сегмент);
      • python-сегмент, не прошедший `_py_env_probe_only`;
      • кривое квотирование (shlex не разобрал)."""
    if not cmd:
        return False
    segs = _split_segments(cmd)
    seen = False
    for i in range(0, len(segs), 2):
        seg = segs[i]
        if not _RE_ENV.search(seg):
            continue
        seen = True
        if i + 1 < len(segs) and segs[i + 1].strip() == "|":
            return False
        if _RE_REDIRECT.search(seg):
            return False
        try:
            toks = shlex.split(seg)
        except Exception:
            return False
        j = _cmd_index(toks)
        if j is None or j >= len(toks):
            return False
        if _base(toks[j]) in _EXISTS_CMDS or _base(toks[j]) in _PRINT_CMDS:
            continue
        if _RE_PY.search(seg) and _py_env_readonly(seg):
            continue
        return False
    return seen


# ------------------------------- классификаторы ------------------------------

def _decide_write(ti, cwd):
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if _is_secret_path(path):
        return ("ask", "edit_secret", os.path.basename(path))
    if _is_claude_path(path):
        return ("ask", "edit_claude", os.path.basename(path))
    # Исходники самого гарда — запись инструментом, она НИЧЕГО НЕ ИСПОЛНЯЕТ. Стоит СТРОГО ПОСЛЕ
    # секретов и конфига `.claude` (те спрашивают всегда, карве-аут их не обходит) и ДО проверки
    # «вне проекта» — иначе два файла гарда в клоне сервера так и остались бы `write_outside`.
    if _is_guard_source(path):
        return ("defer", "", "")
    if not _inside_project(path) and not _sanctioned_outside(path):
        return ("ask", "write_outside", path)
    return ("defer", "", "")


def _decide_read(ti, cwd):
    path = ti.get("file_path") or ""
    if _is_secret_path(path):
        return ("ask", "read_secret", os.path.basename(path))
    return ("defer", "", "")


def _decide_bash(cmd, cwd):
    """Разбор Bash/PowerShell. Тонкая обёртка над `_decide_bash_body`: вычисляет ОДИН РАЗ
    скан-текст и признак «секрет упомянут только пробой наличия», а по итогу проставляет вид
    `env_probe` там, где иначе в логе стояло бы безликое `unknown`. Само решение — в body."""
    if not cmd:
        return ("defer", "", "")
    scan = _scan_text(cmd)
    probe = bool(_RE_ENV.search(scan)) and _env_probe_only(cmd)
    action, kind, obj = _decide_bash_body(cmd, cwd, scan, probe)
    if action == "defer" and not kind and _RE_SQLITE_WORD.search(scan):
        # Питон-форма чтения базы доходит сюда через `_scan_python` с пустым видом. Смягчение не
        # должно стоить прозрачности (доктрина лога): в журнале обязано быть видно, что молча
        # прошло именно ЧТЕНИЕ базы, а не безымянное «ну ничего красного не нашли».
        sq = _sqlite_decide(scan)
        if sq and sq[0] == "sqlite_read":
            return ("defer", "sqlite_read", sq[1])
    if probe and action == "defer" and not kind:
        return ("defer", "env_probe", "")     # прозрачность лога: пробу наличия видно как пробу
    return (action, kind, obj)


def _decide_bash_body(cmd, cwd, scan, env_probe=False):
    # Красное ищем в СКАН-ТЕКСТЕ: позиционные аргументы .py-скриптов — ДАННЫЕ, а не операция
    # (класс-фикс, порт с VPS). Сегменты шелла сохранены целиком, подстановки команд и пути к
    # секретам из аргументов НЕ вырезаются. `_RE_OUTSIDE_WRITE` ниже намеренно смотрит СЫРУЮ
    # команду: перенаправление `> C:\…` стоит после имени скрипта и вырезанием пряталось бы.
    netk, nettarget = _net_scan(scan)
    sq = _sqlite_decide(scan) if _RE_SQLITE_WORD.search(scan) else None
    for rx, kind in _RED_CMD:
        if rx.search(scan):
            # Сеть: красное — только НАСТОЯЩИЙ выход наружу. Свой ssh-канал и упоминание слова
            # в тексте карточки не порождают (см. _net_scan).
            if kind == "network" and netk != "open":
                continue
            if kind == "clasp":
                # Разводим по подкоманде: чтение — зелёное, выкатка/исполнение — красное.
                cl = _clasp_decide(scan, cwd)
                if cl is None:
                    continue                  # `clasp` словом в тексте, а не командой
                return ("ask", cl[0], cl[1])
            if kind == "sqlite":
                # Разводим по оператору запроса. ЧТЕНИЕ (и просто имя модуля) не обрывает разбор:
                # уходим в `continue`, чтобы остальные красные ветви (env, конфиг `.claude`,
                # запись вне репо, боевые токены питона) отработали как раньше. Иначе
                # `python -c "…select…; os.remove(x)"` проехал бы зелёным целиком.
                if sq is None or sq[0] == "sqlite_read":
                    continue
                return ("ask", "sqlite", sq[1])
            obj = ""
            if kind == "delete":
                obj = _extract_delete_target(scan) or ""
            elif kind == "kill":
                obj = _extract_kill_target(scan) or ""
            elif kind == "network":
                obj = nettarget or _extract_host(scan) or ""
            elif kind == "sqlite":
                obj = _extract_db(scan) or ""
            return ("ask", kind, obj)
    # Секрет упомянут — но ПРОБА НАЛИЧИЯ секретом не делится (`_env_probe_only`). Признак пробы
    # НЕ обрывает разбор: команда идёт дальше по всем прочим красным ветвям (конфиг `.claude`,
    # запись вне репо, python-скан) — иначе `Test-Path .env; python evil.py` перестал бы
    # сканировать второй сегмент. Послабление касается ТОЛЬКО вида `env`.
    if not env_probe and _RE_ENV.search(scan):
        return ("ask", "env", "")
    # Конфиг `.claude` защищён для инструментов Write/Edit — значит его надо защитить и от
    # ОБХОДА через шелл. Иначе дыра тривиальна: сессия пишет settings.json.new (внутри репо,
    # зелёное) и копирует его поверх боевого одной командой `cp`, молча расширив свои права.
    # Чтение конфига остаётся зелёным (ветка _RE_READONLY_SHELL ниже — cat/type/Get-Content).
    m = _RE_CLAUDE_CFG_CMD.search(scan)
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
        return _scan_python(cmd, cwd, env_probe)
    m = _RE_UNTIL_WAIT.match(cmd)
    if m:
        cond = (m.group(1) or "").lstrip("! \t")
        tail = m.group(2) or ""
        if _RE_READONLY_SHELL.match(cond) and (not tail or _RE_READONLY_SHELL.match(tail)):
            return ("defer", "", "")
    if _RE_READONLY_SHELL.search(cmd):
        return ("defer", "", "")
    # Команда — ЧТЕНИЕ базы и ничего больше (`sqlite3 bookings.db "select 1"`): смотрелки шелла
    # её не знают, и без этой ветки она упала бы в `unknown`, а `unknown` — hard-блок, карточка
    # ВСЕГДА. Вид ставим честный: в журнале видно, что решение принято разбором запроса.
    if sq and sq[0] == "sqlite_read":
        return ("defer", "sqlite_read", sq[1])
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

# ── «ДА» ВЛАДЕЛЬЦА ДОЛЖНО ДОЕЗЖАТЬ ДО ГАРДА (правка 30.07.2026) ──────────────────────────────
# Живой класс: владелец жмёт «да» в 1160, демон честно перезапускает задачу (status=approved →
# process_approved → run_task) — и НОВЫЙ дочерний claude встречает НОВЫЙ процесс гарда, который
# про одобрение не знает ничего. Тот же шаг снова красный → карточка в маркер → демон ставит
# ✋failed «одобрено, но шаг снова упирается в красное». За историю демона так сгорело 7 «да» из
# 10 (задачи 281, 338, 362, 363, 364, 48, 55); задача 55 — дословно: `ask | env` в 15:09:05
# ВНУТРИ одобренного ре-рана 15:05:34→15:20:25.
#
# Одобрение теперь едет в ребёнка env-маркером и уважается ПО КЛАССУ ОПЕРАЦИИ:
#   PRETOOL_APPROVED_KINDS = 'env,delete'  — виды, НАЗВАННЫЕ в карточке, на которую сказали «да»;
#   PRETOOL_APPROVED_TASK  = '<id>'        — задача (для лога; решение принимают ВИДЫ).
# Границы послабления, каждая намеренная:
#   • ПО ЗАДАЧЕ: маркер ставит демон на КОНКРЕТНЫЙ запуск (`_run_task_impl`), он не живёт нигде
#     ещё — интерактивные сессии владельца и любой другой запуск его не имеют;
#   • ПО КЛАССУ: пропускается ровно тот вид, что стоял в карточке. Другое красное — как было;
#   • СБОЙ РАЗБОРА САМОГО ГАРДА не пропускается НИКОГДА: hard-блок из `main()` идёт мимо
#     `decide_for_role` (except-ветка), значит одобрение его не касается;
#   • доверие к env — ТО ЖЕ, что уже принято для `PRETOOL_ASK_MARKER`/`PRETOOL_MARKER_TOKEN`:
#     эти переменные ставит демон своему ребёнку, и канал env для гарда уже авторитетен.
APPROVED_KINDS_ENV = "PRETOOL_APPROVED_KINDS"
APPROVED_TASK_ENV = "PRETOOL_APPROVED_TASK"
# Строка КЛАССА в карточке: гард дописывает её в файл-маркер (в интерактивной карточке ничего не
# меняется), демон сохраняет карточку в очередь вместе с ней, а после «да» читает класс ОТТУДА ЖЕ.
# Так одобрение переживает и рестарт демона: никакого локального состояния не нужно.
KIND_LINE_PREFIX = "Класс операции: "
_RE_KIND_LINE = re.compile(r"(?im)^\s*Класс операции:\s*([a-z_]+(?:\s*,\s*[a-z_]+)*)\s*$")
# Маркер класса от САМОЙ МОДЕЛИ: «NEEDS_APPROVAL: op=<класс> | <карточка>» (второй канал красного —
# ребёнок сам отказался от операции, гард при этом ничего не писал; так умерла задача 48).
_RE_OP_MARK = re.compile(r"(?i)\bop\s*=\s*([a-z_]+)")
# Виды, которые вообще могут быть одобрены. Список ЗАКРЫТЫЙ: незнакомое слово в карточке
# одобрением не становится (`op=other` — честное «класс не назван» → одобрения нет).
_KIND_VOCAB = ("delete", "kill", "schtasks", "git_force", "sqlite", "clasp", "clasp_push",
               "clasp_deploy", "clasp_run", "live_sheet", "network", "env", "outside",
               "write_outside", "py_write", "edit_secret", "read_secret", "edit_claude",
               "unknown")


def owner_approved_kinds(env=None):
    """Виды, одобренные владельцем для ТЕКУЩЕГО запуска (из env-маркера демона). env — инъекция
    для тестов.

    Разбор ВСЁ-ИЛИ-НИЧЕГО: демон складывает значение из слов закрытого `_KIND_VOCAB`, поэтому
    ЛЮБОЕ посторонное слово означает, что маркер писал не он, — тогда одобрения нет вовсе
    (`env; rm -rf /` не должно читаться как «одобрен env»). Пустое/битое значение → пусто, то
    есть карточка как была: ошибка здесь может только ДОБАВИТЬ подтверждение."""
    e = os.environ if env is None else env
    raw = (e.get(APPROVED_KINDS_ENV) or "").strip()
    if not raw:
        return frozenset()
    words = [t.strip().lower() for t in re.split(r"[,;]+", raw) if t.strip()]
    if not words or any(w not in _KIND_VOCAB for w in words):
        return frozenset()
    return frozenset(words)


def _kind_phrase(kind):
    """Устойчивый ПРЕФИКС человеческой фразы карточки для вида: общая часть `_human(kind, '')` и
    `_human(kind, '<объект>')`. Считается ИЗ `_human`, а не дублируется литералом, — иначе два
    списка формулировок разъехались бы при первой же правке карточки."""
    a, b = _human(kind, ""), _human(kind, "@@")
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return a[:n]


def kinds_from_card(text):
    """Виды красных операций, НАЗВАННЫЕ в карточке needs_approval → frozenset (может быть пустым).
    Читает демон ПОСЛЕ «да» владельца, чтобы одобрение вернулось ровно на свой класс. Три слоя,
    в порядке надёжности:
      1) строка «Класс операции: <вид>[, <вид>]» — её пишет сам гард в файл-маркер;
      2) `op=<вид>` — маркер, который печатает модель, когда красное отклонила ОНА;
      3) фраза карточки (`_human`) — страховка для карточек, выписанных до этой правки.
    Пусто → у демона прежнее поведение байт-в-байт (одобрение в env не поедет)."""
    t = str(text or "")
    if not t.strip():
        return frozenset()
    out = set()
    for m in _RE_KIND_LINE.finditer(t):
        out.update(k.strip() for k in m.group(1).split(","))
    for m in _RE_OP_MARK.finditer(t):
        out.add(m.group(1).strip().lower())
    if not (out & set(_KIND_VOCAB)):
        for kind in _KIND_VOCAB:
            ph = _kind_phrase(kind)
            if len(ph) >= 12 and ph in t:
                out.add(kind)
    return frozenset(k for k in out if k in _KIND_VOCAB)

# Массовое удаление (красное) vs удаление ОДНОГО явного файла (в интерактиве зелёное).
_RE_DEL_TAIL = re.compile(r"(?i)(?:^|[\s;&|(])(?:del|erase|rmdir|rd|rm|remove-item)\b(.*)$")
_RE_DEL_RECURSE = re.compile(r"(?i)(^|\s)(-[a-z]*r[a-z]*|/s|-recurse\w*)(\s|$)")
_RE_SCHTASKS_QUERY = re.compile(r"(?i)\bschtasks\b[^;&|]*\s/query\b")

_DEL_CMDS = {"del", "erase", "rmdir", "rd", "rm", "remove-item", "ri"}
_RE_CMDEXE_FLAG = re.compile(r"^/[A-Za-z]{1,2}$")   # /f /s /q cmd.exe — не путь MSYS вида /d/…


def _delete_scan(cmd):
    """Разбор удаления ПОСЕГМЕНТНО → (цели, рекурсивно, есть маска).

    Хвост берётся ТОЛЬКО из своего сегмента. Прежний разбор тянул `.*$` до конца строки, и
    команда `rm -f память/один.md; ls память/один.md 2>&1` выглядела удалением ЧЕТЫРЁХ целей
    (файл, `ls`, файл ещё раз, `2>&1`) — ложное «массовое удаление» на удалении ОДНОГО файла.
    Цели отдаются с приклеенным каталогом из предшествующего `cd` — иначе относительное имя
    (`rm -f token.txt` после `cd "$SP"`) не сопоставить ни с одной зоной."""
    hint, targets, recurse, mask = "", [], False, False
    for i, seg in enumerate(_split_segments(cmd or "")):
        if i % 2:
            continue
        try:
            toks = shlex.split(seg)
        except Exception:
            toks = seg.split()
        j = _cmd_index(toks)
        if j is None or j >= len(toks):
            continue
        name = _base(toks[j])
        if name == "cd" and j + 1 < len(toks):
            hint = toks[j + 1].strip("'\"")
            continue
        if name not in _DEL_CMDS:
            continue
        for t in toks[j + 1:]:
            if t.startswith("-") or _RE_CMDEXE_FLAG.match(t):
                if _RE_DEL_RECURSE.search(" " + t + " "):
                    recurse = True
                continue
            t = t.strip("'\"")
            if not t:
                continue
            if "*" in t or "?" in t:
                mask = True
            targets.append(t if (re.match(r"^([A-Za-z]:|[/\\~$%])", t) or not hint)
                           else hint.rstrip("/\\") + "/" + t)
    return targets, recurse, mask


def _delete_targets_all_temp(cmd):
    """True ⇔ удаление ЦЕЛИКОМ живёт во временных зонах из .gitignore (`tmp/`, `%TEMP%\\claude\\**`).
    Пустой список целей или хоть одна цель вне зоны → False: послабление не распространяется."""
    targets, _rec, _mask = _delete_scan(cmd)
    return bool(targets) and all(_is_temp_zone(t) for t in targets)


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
    именно «массовые удаления»). Разбор посегментный (_delete_scan); если сегмент удаления не
    нашёлся вовсе — падаем на прежний подстрочный разбор, чтобы не ослабить признак."""
    targets, recurse, mask = _delete_scan(cmd)
    if targets or recurse or mask:
        return bool(mask or recurse or len(targets) > 1)
    m = _RE_DEL_TAIL.search(cmd or "")
    tail = m.group(1) if m else (cmd or "")
    if "*" in tail or "?" in tail or _RE_DEL_RECURSE.search(tail):
        return True
    rest = [t for t in re.findall(r"[^\s\"';|&]+", tail)
            if not t.startswith("-") and not t.startswith("/")]
    return len(rest) > 1


def _stays_red(kind, obj, cmd):
    """Доктринальный список (перенос с VPS, действует В ОБЕИХ ролях): что продолжает
    спрашивать Allow. Всё, что сюда не попало, пропускается молча (и пишется в лог)."""
    if kind in ("env", "edit_secret", "read_secret",     # .env и секреты
                "sqlite",                                 # живая БД
                "clasp", "live_sheet",                    # живые таблицы (Лист1 / CRM / Календарь)
                "clasp_deploy", "clasp_run", "clasp_push",  # выкатка / исполнение / заливка без пина
                "kill",                                   # остановка процессов
                "git_force",                              # git clean/reset --hard = массовый снос работы
                "network",                                # выход в сеть = канал утечки секретов
                "outside", "write_outside",               # требование п.1: правки ТОЛЬКО внутри репо
                "edit_claude"):                           # иначе сессия молча расширит собственные права
        return True
    if kind in ("clasp_read", "clasp_push_pinned", "sqlite_read"):
        # чтение проекта Apps Script; заливка кода при проде НА ВЕРСИИ (прод не двигают);
        # ЧТЕНИЕ базы (`select`/схема/`mode=ro`) — данные не меняются, спрашивать не о чем
        return False
    if kind == "delete":
        # Временные каталоги из .gitignore (`tmp/`, `%TEMP%\claude\**`) — рабочие черновики самой
        # сессии: в git не едут, прод-контур их не видит, живут один сеанс. Уборка за собой
        # подтверждения не стоит. Хоть одна цель вне зоны — красное как было.
        return _is_mass_delete(cmd) and not _delete_targets_all_temp(cmd)
    if kind == "schtasks":
        return not _RE_SCHTASKS_QUERY.search(cmd or "")   # /query — чтение, остальное = контроль задач
    if kind == "py_write":
        return obj in _RED_PY_TOKENS      # боевой токен = красное; «скрипт не прочитан»/«-m X» = неизвестность
    return False                          # unknown и прочее: незнакомое САМО ПО СЕБЕ не красное (доктрина VPS)


def decide_for_role(data, headless, env=None):
    """Решение ПО ДОКТРИНЕ (перенос списка с VPS, кейс 314): Allow спрашивает только
    доктринальное (_stays_red) — В ОБЕИХ РОЛЯХ, незнакомая команда сама по себе не красная.
    Раньше headless шёл строгим decide() без послаблений, и чистое чтение (`wc -l` по репо,
    until-grep-ожидание output) роняло задачу демона в NEEDS_APPROVAL. Параметр headless
    решение больше не меняет (оставлен вызывающим: роль нужна логу и каналу ask). Возвращает
    (action, kind, obj); kind/obj сохраняются и при смягчении — они нужны логу.

    Третье решение — `approved`: вид ЭТОЙ операции владелец уже подтвердил кнопкой, и демон
    прокинул его в env запуска (`owner_approved_kinds`). Тогда карточки нет, но и молчания нет:
    строка в логе стоит с решением `approved` (видно, ЧТО прошло по чужому «да»). Красное
    ДРУГОГО класса и сбой разбора самого гарда одобрением не покрываются."""
    action, kind, obj = decide(data)
    if action != "ask":
        return action, kind, obj
    cmd = ""
    if (data.get("tool_name") or "") in ("Bash", "PowerShell"):
        cmd = (data.get("tool_input") or {}).get("command") or ""
    if _stays_red(kind, obj, cmd):
        if kind in owner_approved_kinds(env):
            return ("approved", kind, obj)
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

# ОДНА строка отката на вид операции: карточка без ответа «а если не то?» решения не даёт.
# Формулировки честные — там, где отката нет, так и написано.
_ROLLBACK = {
    "delete": "Откат: удалённое не вернуть — только из git или бэкапа",
    "kill": "Откат: поднять процесс заново (сторож поднимет сам, если он под ним)",
    "schtasks": "Откат: обратная команда schtasks (/change /enable ↔ /disable)",
    "git_force": "Откат: git reflog → git reset --hard <прежний хеш>",
    "sqlite": "Откат: восстановить БД из бэкапа; бэкапа нет — сделать ДО записи",
    "clasp": "Откат: зависит от подкоманды — эффект в проекте Apps Script вручную",
    "clasp_push": "Откат: залить прежний код (tmp/bridge_v<прежняя>); прод НА ВЕРСИИ не двигается",
    "clasp_deploy": "Откат: clasp deploy -i <deploymentId> -V <прежняя версия из clasp deployments>",
    "clasp_run": "Откат: автоматического нет — функция уже отработала в живых таблицах",
    "live_sheet": "Откат: история версий Google Sheets (Файл → История версий)",
    "network": "Откат: чтение отката не требует; ОТПРАВЛЕННОЕ не вернуть",
    "env": "Откат: .env вне git — вернуть из .env.bak*",
    "edit_secret": "Откат: .env вне git — вернуть из .env.bak*",
    "read_secret": "Откат: не нужен (чтение), но значение окажется в контексте сессии",
    "edit_claude": "Откат: git checkout -- .claude/settings.json (файл под git)",
    "outside": "Откат: удалить созданное вручную — это вне репозитория",
    "write_outside": "Откат: удалить созданное вручную — это вне репозитория",
    "py_write": "Откат: боевую запись Bridge снимает только обратная операция (void_last/…)",
    "unknown": "Откат: неизвестен — гард не разобрал команду",
}

_RE_NUM_VERSION = re.compile(r"(?i)(?:^|\s)-V\s+(\d+)")
_RE_NUM_PID = re.compile(r"(\d+)")

# Виды, карточка которых обязательна ДАЖЕ без объекта, — hard-блок:
#   • `unknown` — сбой разбора самого гарда: на нём молчать нельзя ни при каких условиях;
#   • `.env` и секреты — прямое требование владельца «.env не трогать»: правило «нет объекта →
#     журнал» не имеет права его обойти, даже если имя файла осталось внутри тела скрипта;
#   • `py_write` — БОЕВАЯ ЗАПИСЬ Bridge, то есть ДЕНЬГИ (отмена последней проводки зовётся без
#     аргументов по своей природе). Зеркало `_ALWAYS_CARD` полосы сервера: деньги спрашивают всегда.
_HARD_CARD = ("unknown", "env", "edit_secret", "read_secret", "py_write")


def _card_fields(kind, obj="", raw_cmd=""):
    """→ (объект, число). Объект — ЧТО именно трогаем (файл, хост, PID, проект); число — версия,
    PID, сколько целей. Пустая пара означает: признак сработал на ПОДСТРОКЕ, а не на действии."""
    o, n = (obj or "").strip(), ""
    cmd = raw_cmd or ""
    if kind == "delete":
        targets, _rec, _mask = _delete_scan(cmd)
        o = o or (targets[0] if targets else "")
        if targets:
            n = "%d цел%s" % (len(targets), "ь" if len(targets) == 1 else "и")
    elif kind == "kill":
        n = ""                      # число (PID) уже внутри объекта — второй строкой это шум
    elif kind == "schtasks":
        m = re.search(r"(?i)(?:/tn|-taskname)\s+[\"']?([^\"'\s;|&]+)", cmd)
        o = o or (m.group(1) if m else "")
    elif kind.startswith("clasp"):
        m = _RE_NUM_VERSION.search(cmd)
        if m:
            n = "версия " + m.group(1)
        else:
            d = re.search(r"(?i)-i\s+(\S{12,})", cmd)
            n = ("деплой …" + d.group(1)[-8:]) if d else ""
    elif kind == "git_force":
        m = re.search(r"(?i)git\s+(push|reset|clean)\b([^;|&]*)", cmd)
        o = o or ((m.group(1) + (" " + m.group(2).strip() if m.group(2).strip() else "")).strip()
                  if m else "")
    elif kind == "env":
        m = _RE_ENV.search(_scan_text(cmd)) if cmd else None
        o = o or (m.group(0).strip("'\" ") if m else "")
    elif kind == "live_sheet":
        o = o or (_extract_host(cmd) or "")
    elif kind == "sqlite":
        # ИМЯ БАЗЫ — обязательный объект карточки записи (требование ТЗ 30.07.2026). Базу не
        # назвали в команде (`c.execute(…)` по соединению из переменной, имя в теле скрипта) —
        # берём ТАБЛИЦУ из запроса: это тоже объект, и он честный.
        o = o or _extract_db(cmd) or ""
        if not o:
            m = _RE_SQL_TABLE.search(cmd or "")
            o = ("таблица " + m.group(1)) if m else ""
    return o.strip(), n.strip()


def _rollback(kind, raw_cmd=""):
    """Одна строка отката. Для выкатки Apps Script собирается ПО КОМАНДЕ: тот же деплой, прежняя
    версия — ровно та строка, которой владелец сам откатывался (артефакт 2026-07-29). id деплоя
    маскируем хвостом: карточка обязана читаться, полный id есть в `clasp deployments`."""
    if kind == "clasp_deploy":
        m = re.search(r"(?i)-i\s+(\S{12,})", raw_cmd or "")
        dep = ("…" + m.group(1)[-6:]) if m else "<deploymentId>"
        if re.search(r"(?i)(^|\s)clasp\s+undeploy\b", raw_cmd or ""):
            return "Откат: clasp deploy -i %s -V <снятая версия> (вернуть удалённый деплой)" % dep
        return "Откат: clasp deploy -i %s -V <прежняя версия из clasp deployments>" % dep
    return _ROLLBACK.get(kind, "Откат: обратной операцией вручную")


def _card(kind, obj="", raw_cmd=""):
    """Человеческая карточка. Обязательный минимум за 3 секунды: ЧТО меняется (первая строка),
    у какого ОБЪЕКТА, какое ЧИСЛО и одна строка ОТКАТА. Сырая команда — последней и обрезанной:
    она для прозрачности, а не для чтения."""
    o, n = _card_fields(kind, obj, raw_cmd)
    # ОБЕ подписанные строки стоят ВСЕГДА, пустое значение — честный прочерк (формат общий с
    # полосой сервера). Раньше строка «Число» при пустом значении просто исчезала, и владелец не
    # мог отличить «числа у операции нет по природе» от «карточка его потеряла».
    lines = ["🔴 " + _human(kind, obj) + " — разрешить?",
             "Объект: " + (o or "—"),
             "Число: " + (n or "—")]
    lines.append(_rollback(kind, raw_cmd))
    if raw_cmd:
        c = " ".join(raw_cmd.split())
        lines.append("Команда: " + (c if len(c) <= 200 else c[:200] + "…"))
    return "\n".join(lines)


def card_gate(kind, obj, num=""):
    """ЕДИНОЕ ПРАВИЛО КАРТОЧКИ, общее с полосой сервера (29.07.2026) → True = карточка, False = журнал.

        • ОБЪЕКТ обязателен ВСЕГДА. Нет объекта — признак сработал на ПОДСТРОКЕ, а не на действии
          (живой пример суток: `echo "---SCHTASKS XML---"` внутри `ls`, где слова Планировщика нет
          ни в одной командной позиции): показывать владельцу нечего, идёт строка в журнал.
        • ЧИСЛО карточку НЕ гейтит НИКОГДА. Оно есть там, где операция несёт его ПО СВОЕЙ ПРИРОДЕ
          (сумма, пробег, количество целей, версия, PID), и честно пусто там, где не несёт.
        • Hard-блок и секреты (_HARD_CARD) спрашивают всегда — как и было. Деньги тоже.

    ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ПРЕЖНЕГО ПК-ПРАВИЛА. Было «пусто И объект, И число → журнала», то есть
    безобъектная команда, в которой нашлась ЛЮБАЯ цифра, карточку РОЖДАЛА. Полоса сервера в тот же
    день гасила карточку при отсутствии объекта ИЛИ числа и потому молча съедала операции без
    числа по природе. Одно правило вместо двух: объект — гейт, число — поле."""
    return bool((obj or "").strip()) or kind in _HARD_CARD


def card_or_journal(kind, obj="", raw_cmd=""):
    """→ текст карточки либо None (карточки нет — вместо неё строка в журнал).
    Само правило — в card_gate(). Любой сбой здесь → карточка (FAIL-SAFE)."""
    try:
        o, n = _card_fields(kind, obj, raw_cmd)
        if not card_gate(kind, o, n):
            return None
    except Exception:
        pass
    return _card(kind, obj, raw_cmd)


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


def _write_marker(mk, card, kind=""):
    """Дописать красную карточку в файл-маркер headless-сигнала С ДЕДУПОМ и ШТАМПОМ ТОКЕНА.

    Последней строкой блока идёт КЛАСС операции (`KIND_LINE_PREFIX`) — он нужен демону, чтобы
    после «да» владельца вернуть одобрение ИМЕННО на этот класс (см. `kinds_from_card`). Строка
    живёт ТОЛЬКО в headless-канале: текст интерактивной карточки не меняется ни на байт.
    Формат строки: '<run_token>\\x1f<строка карточки>'. Токен из env PRETOOL_MARKER_TOKEN —
    задаёт демон на КАЖДЫЙ запуск; так демон отсеивает чужие/старые карточки (напр. фикстуры
    из subprocess-тестов гарда, унаследовавших боевой маркер). Дедуп: одно красное действие
    могло ретраиться → карточка набегала ×N (было ×5)."""
    c = (card or "").strip()
    if not c:
        return
    if kind:
        c += "\n" + KIND_LINE_PREFIX + kind
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


def _emit_ask(card, kind=""):
    # Сигнал headless→демон (pc_orchestrator): в headless карточку не показать интерактивно,
    # поэтому при заданном env пишем красную карточку в файл-маркер — демон детектит и ставит
    # NEEDS_APPROVAL. В интерактивной сессии env не задан → поведение не меняется.
    mk = os.environ.get(ASK_MARKER_ENV)
    if mk:
        _write_marker(mk, card, kind)
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
    card = None
    if action == "ask":
        card = card_or_journal(kind, obj, detail if tool in ("Bash", "PowerShell") else "")
        if card is None:
            action = "journal"   # объекта и числа нет → вместо карточки строка в журнал
    _log(role, tool, action, kind, detail)
    if card is not None:
        _emit_ask(card, kind)
    sys.exit(0)  # defer / journal


if __name__ == "__main__":
    main()
