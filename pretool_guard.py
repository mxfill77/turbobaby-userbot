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

# --- КРАСНЫЕ признаки Bash-команды ---
_RED_CMD = [
    (re.compile(r"(?i)(^|[\s;&|(])(del|erase|rmdir|rd|rm)([\s;&|)]|$)"), "удаление файлов (del/rm/rmdir)"),
    (re.compile(r"(?i)remove-item\b"), "удаление (Remove-Item)"),
    (re.compile(r"(?i)(^|[\s;&|(])(taskkill|kill|pkill)([\s;&|)]|$)"), "снятие процесса (taskkill/kill)"),
    (re.compile(r"(?i)stop-process\b"), "снятие процесса (Stop-Process)"),
    (re.compile(r"(?i)\bschtasks\b"), "Планировщик задач (schtasks)"),
    (re.compile(r"(?i)git\s+push\b.*(--force|(?<![\w-])-f(?![\w]))"), "git push --force"),
    (re.compile(r"(?i)git\s+reset\s+--hard"), "git reset --hard"),
    (re.compile(r"(?i)git\s+clean(\s|$)"), "git clean"),
    (re.compile(r"(?i)(^|[\s;&|(])sqlite3([\s;&|)]|$)"), "sqlite3 CLI (запись в БД)"),
    (re.compile(r"(?i)(^|[\s;&|(])(curl|wget|iwr|irm)([\s;&|)]|$)|invoke-webrequest|invoke-restmethod"),
     "сетевое действие (curl/wget/Invoke-WebRequest)"),
    (re.compile(r"(?i)(^|[\s;&|(])(ssh|scp|sftp|nc|ncat|telnet)([\s;&|)]|$)"), "сетевой доступ (ssh/scp/nc)"),
]
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
    """→ ('defer','') | ('ask', reason). Читаем инлайн -c и тела .py-целей, ищем боевую запись."""
    try:
        toks = shlex.split(cmd)
    except Exception:
        return ("ask", "python: кривое квотирование — не разобрал команду")
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
            return ("ask", "python: код из stdin — читать нечего")
        if t == "-m":
            mod = toks[i + 1] if i + 1 < len(toks) else ""
            if mod in _GREEN_MODULES:
                return ("defer", "")
            return ("ask", f"python -m {mod}: модуль не в списке безопасных")
        if t.endswith(".py"):
            body = _read_file(t, cwd)
            if body is None:
                return ("ask", "python: цель-скрипт не прочитан")
            content += body
            saw_target = True
        i += 1
    blob = cmd + "\n" + content
    if _RE_ENV.search(blob):
        return ("ask", "python трогает .env/секрет")
    for tok in _RED_PY_TOKENS:
        if tok in blob:
            return ("ask", f"python: боевая запись/удаление ({tok})")
    if _RE_SQL_WRITE.search(blob) and ".db" in blob.lower() and "memory.db" not in blob.lower():
        return ("ask", "python: SQL-запись в БД (не memory.db)")
    if not saw_target:
        return ("ask", "python без внятной цели (REPL/неясно)")
    return ("defer", "")


# ------------------------------- классификаторы ------------------------------

def _decide_write(ti, cwd):
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if _is_secret_path(path):
        return ("ask", "правка секрета/.env — красное")
    if _is_claude_path(path):
        return ("ask", "правка .claude/* — красное")
    if not _inside_project(path):
        return ("ask", "запись вне D:\\turbobaby-bot — красное")
    return ("defer", "")


def _decide_read(ti, cwd):
    path = ti.get("file_path") or ""
    if _is_secret_path(path):
        return ("ask", "чтение .env/секрета — красное")
    return ("defer", "")


def _decide_bash(cmd, cwd):
    if not cmd:
        return ("defer", "")
    for rx, reason in _RED_CMD:
        if rx.search(cmd):
            return ("ask", reason + " — красное")
    if _RE_ENV.search(cmd):
        return ("ask", "доступ к .env/секрету — красное")
    m = _RE_OUTSIDE_WRITE.search(cmd)
    if m and not _inside_project(m.group(2)):
        return ("ask", "запись по пути вне D:\\turbobaby-bot — красное")
    # зелёное
    if _RE_SAFE_SCRIPTS.search(cmd):
        return ("defer", "")
    if _RE_GIT_SAFE.search(cmd):
        return ("defer", "")
    if _RE_TESTS.search(cmd):
        return ("defer", "")
    if _RE_PY.search(cmd):
        return _scan_python(cmd, cwd)
    if _RE_READONLY_SHELL.search(cmd):
        return ("defer", "")
    return ("ask", "команда не распознана как безопасная — подтверди (fail-safe)")


def decide(data):
    """Чистая классификация: → ('defer','') или ('ask', reason). Без I/O."""
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or PROJECT
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return _decide_write(ti, cwd)
    if tool == "Read":
        return _decide_read(ti, cwd)
    if tool == "Bash":
        return _decide_bash(ti.get("command") or "", cwd)
    return ("defer", "")  # Grep/Glob/прочие read-only инструменты


# ------------------------------- вывод/пуш -----------------------------------

def _card(reason):
    return ("🔴 КРАСНОЕ — нужно твоё «да»\n"
            "Что: " + reason + "\n"
            "Гард Dispatch форсит подтверждение (fail-safe). Проверь и подтверди в сессии.")


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


def _emit_ask(reason):
    card = _card(reason)
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
        action, reason = decide(data)
    except Exception:
        action, reason = ("ask", "ошибка анализа — подтверди (fail-safe)")
    if action == "ask":
        _emit_ask(reason)
    sys.exit(0)  # defer


if __name__ == "__main__":
    main()
