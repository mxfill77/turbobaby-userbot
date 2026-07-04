#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pretool_guard.py — PreToolUse-хук Claude Code для ПК (D:\\turbobaby-bot).
Порт VPS-паттерна (/root/turbobaby-manager-bot/pretool_guard.py) под Windows.

ПОЗИЦИЯ БЕЗОПАСНОСТИ (как на VPS): хук ТОЛЬКО ДОБАВЛЯЕТ подтверждения, НИКОГДА не выдаёт
новых разрешений. Он не возвращает permissionDecision="allow" — авто-allow зелёного делает
список permissions.allow в settings.json. Хук лишь форсит "ask" (+🔔 пуш) на КРАСНОМ и
НЕИЗВЕСТНОМ. Значит ошибка классификации может только ДОБАВИТЬ лишний ask (безопасная
сторона), но не пропустить опасное.

Трёхцветно:
  ЗЕЛЁНОЕ  → defer (exit 0, без вывода) → штатные allow-правила решают.
  КРАСНОЕ  → ask (карточка в сессии) + 🔔 через dispatch_notify.
  НЕИЗВЕСТНОЕ / ошибка анализа → ask (FAIL-SAFE, в сторону подтверждения).

КРАСНЫЙ список (НЕ смягчать): правка/чтение .env и секретов; удаление файлов
(del/rm/rmdir/Remove-Item); правка .claude/*; kill/taskkill/schtasks/Stop-Process;
git push --force / reset --hard / clean; sqlite3 / SQL-запись в БД; запись/исполнение вне
D:\\turbobaby-bot; сеть кроме git/Bridge/Telegram (curl/wget/ssh/scp/…); python-скрипт с
боевой записью (Bridge create_booking/add_transaction/… , os.remove/rmtree, SQL-write).
"""
import sys
import os
import re
import json
import shlex
import subprocess

try:  # хук-протокол Claude Code читает stdout как UTF-8; форсим, чтобы 🔴/кириллица не бились
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT = r"D:\turbobaby-bot"
PROJ_N = os.path.normcase(os.path.normpath(PROJECT))
VENV_PY = os.path.join(PROJECT, "venv", "Scripts", "python.exe")
DNOTIFY = os.path.join(PROJECT, "dispatch_notify.py")

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
_RE_OUTSIDE_WRITE = re.compile(r"(?i)(>>?|out-file|set-content|new-item|move-item|copy-item)\s+[\"']?([a-z]:[\\/][^\"'\s]+)")

# --- ЗЕЛЁНЫЕ признаки Bash-команды (проверяются ПОСЛЕ красных) ---
_RE_SAFE_SCRIPTS = re.compile(r"(?i)(cowork_log_append|dispatch_notify)\.py")
_RE_GIT_SAFE = re.compile(r"(?i)(^|[\s;&|(])git\s+(status|diff|log|add|commit|push|fetch|pull|branch|show|check-ignore|rev-parse|remote|ls-files|config\s+--get)")
_RE_TESTS = re.compile(r"(?i)-m\s+(pytest|py_compile|unittest)(\s|$)|(^|[\s/\\])pytest(\s|$)")
_RE_READONLY_SHELL = re.compile(r"(?i)^\s*(ls|dir|echo|type|cat|head|tail|findstr|grep|rg|get-content|get-childitem|select-string|get-item|get-ciminstance|test-path|measure-object|where|get-command|git|py|python\s+--version)\b")
_RE_PY = re.compile(r"(?i)(^|[\s/\\])(python3?|python\.exe|venv[\\/]scripts[\\/]python(\.exe)?)(\s|$)")
_GREEN_MODULES = {"py_compile", "pytest", "unittest", "json.tool"}

# python-скрипт с боевой записью → красное
_RED_PY_TOKENS = [
    "create_booking", "activate_booking", "add_transaction", "void_last",
    "set_fleet_oil", "set_fleet_service", "delete_event", "closing_upsert",
    "os.remove", "os.unlink", "shutil.rmtree", "rmtree(", "os.rmdir",
]


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


def _is_claude_path(path):
    n = os.path.normcase(os.path.normpath(path or ""))
    return (os.sep + ".claude" + os.sep) in n or n.endswith(os.sep + ".claude")


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
            body = _read_file(t, cwd)
            if body is None:
                return ("ask", "py_write", "скрипт не прочитан")
            content += body
            saw_target = True
        i += 1
    blob = cmd + "\n" + content
    if _RE_ENV.search(blob):
        return ("ask", "env", "")
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
    if not _inside_project(path):
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
        return _scan_python(cmd, cwd)
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
    if tool == "Bash":
        return _decide_bash(ti.get("command") or "", cwd)
    return ("defer", "", "")  # Grep/Glob/прочие read-only инструменты


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


def _emit_ask(card):
    # Сигнал headless→демон (pc_orchestrator): в headless карточку не показать интерактивно,
    # поэтому при заданном env пишем красную карточку в файл-маркер — демон детектит и ставит
    # NEEDS_APPROVAL. В интерактивной сессии env не задан → поведение не меняется.
    mk = os.environ.get("PRETOOL_ASK_MARKER")
    if mk:
        try:
            with open(mk, "a", encoding="utf-8") as f:
                f.write(card + "\n")
        except Exception:
            pass
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
    try:
        action, kind, obj = decide(data)
    except Exception:
        action, kind, obj = ("ask", "unknown", "")
    if action == "ask":
        raw = ""
        if (data.get("tool_name") or "") == "Bash":
            raw = (data.get("tool_input") or {}).get("command", "") or ""
        _emit_ask(_card(kind, obj, raw))
    sys.exit(0)  # defer


if __name__ == "__main__":
    main()
