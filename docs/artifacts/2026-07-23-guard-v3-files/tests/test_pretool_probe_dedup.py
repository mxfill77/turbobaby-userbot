"""Судьба ambiguous-конвертов стража красной зоны headless.

История: UX-фикс 08.07.2026 (спам-инцидент задачи 163) дал ambiguous-карточке команду в теле
и дедуп «Повтор: ×N». в3 (23.07.2026, доктрина «шквал подтверждений»): ambiguous САМ ПО СЕБЕ
НЕ КРАСНЫЙ — незнакомая команда, python без внятной цели (stdin/heredoc, неизвестный -m,
нечитаемый .py) → defer к слоям settings БЕЗ конверта и БЕЗ пуша. Конверт «не распознал
операцию» упразднён; дедуп из main() удалён вместе с ним (хелперы живы — test_guard_inbox).

Красное гарда — ТОЛЬКО доктринальный список (таблицы/CRM/деньги, секреты, sqlite вне memory.db,
kill/stop боевых, PID 1): red-конверт как раньше, КАЖДЫЙ пушится (red не дедупится).
Пуши в тесте — только мок-счётчик NOTIFY_COUNT_FILE (сети нет).
"""
import os
import sys
import json
import shutil
import tempfile
import subprocess

ROOT = "/root/turbobaby-manager-bot"
PY = os.path.join(ROOT, "venv", "bin", "python3")
PRETOOL = os.path.join(ROOT, "pretool_guard.py")

TMP = tempfile.mkdtemp(prefix="pt_pd_")


def run(cmd, session="s-default", count_file=None, nopush=True):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                          "cwd": ROOT, "session_id": session})
    env = dict(os.environ)
    env.pop("PRETOOL_NOPUSH", None)
    env.pop("NOTIFY_COUNT_FILE", None)
    if nopush:
        env["PRETOOL_NOPUSH"] = "1"
    if count_file:
        env["NOTIFY_COUNT_FILE"] = count_file   # мок-счётчик: попытка пуша = строка, сети НЕТ
    return subprocess.run([PY, PRETOOL], input=payload, capture_output=True, text=True,
                          timeout=60, env=env)


def ok(c, label):
    print(("  PASS " if c else "  FAIL ") + label)
    return c


def lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]
    except Exception:
        return []


res = []

print("(1) probe-паттерны репо → БЕЗ конверта (green/defer, ask нет):")
for cmd in [
    "PRETOOL_NOPUSH=1 " + PY + " --version",              # корень инцидента 163
    "PRETOOL_NOPUSH=1 venv/bin/python3 -V",
    "PRETOOL_NOPUSH=1 " + PY + " " + os.path.join(ROOT, "tests", "test_fmt.py"),
    "PRETOOL_NOPUSH=1 venv/bin/python3 tests/test_fmt.py",
    PY + " --version",                                     # регресс без префикса
    "node --check tests/booking_gs_harness.js",
    "node tests/booking_gs_harness.js",
    "cat " + os.path.join(ROOT, "splinter.log"),
    "grep -n def " + os.path.join(ROOT, "bot.py"),
    "diff " + os.path.join(ROOT, "gate.py") + " " + os.path.join(ROOT, "gate.py"),
]:
    r = run(cmd, session="s-probe")
    res.append(ok(r.returncode == 0 and '"ask"' not in r.stdout, cmd[:80] + " → без конверта"))

print("(2) в3: настоящий ambiguous → defer БЕЗ конверта и БЕЗ пуша:")
missing = os.path.join(TMP, "fx_writer_probe.py")   # нет на диске → нечитаем → ambiguous
cnt_amb = os.path.join(TMP, "cnt_amb.txt")
for cmd in [PY + " " + missing,
            PY + " -",
            PY + " -m unknown_mod_" + "x" * 300,
            PY + " --version --frobnicate"]:
    r = run(cmd, session="s-amb", count_file=cnt_amb, nopush=False)
    res.append(ok(r.returncode == 0 and r.stdout.strip() == "", cmd[:70] + " → defer (пустой stdout)"))
res.append(ok(lines(cnt_amb) == [], "ноль пушей: ambiguous-карточек больше не существует"))

print("(3) red — по-прежнему конверт, КАЖДЫЙ пушится (red не дедупится):")
cnt_red = os.path.join(TMP, "cnt_red.txt")
red = os.path.join(TMP, "fx_red_live.py")
with open(red, "w", encoding="utf-8") as f:
    f.write("# фикстура регресса\nprint('set_fleet_oil')\n")
for _ in range(2):
    r = run(PY + " " + red, session="s-red", count_file=cnt_red, nopush=False)
res.append(ok('"ask"' in r.stdout and "Лист1" in r.stdout, "red-скрипт → конверт с операцией (ask)"))
res.append(ok("Повтор:" not in r.stdout and "не распознал операцию" not in r.stdout,
              "в конверте нет счётчика ×N и нет формулировки ambiguous"))
res.append(ok(len(lines(cnt_red)) == 2 and not any(t.startswith("EDIT ") for t in lines(cnt_red)),
              "2 запуска = 2 отдельных пуша (дедуп red как не было, так и нет)"))

print("(4) defer не ослабил red: ambiguous-звено не заслоняет красное читаемое:")
r = run(PY + " " + red + " && " + PY + " " + missing, session="s-mix")
res.append(ok('"ask"' in r.stdout and "Лист1" in r.stdout,
              "компаунд red.py && нечитаемый.py → конверт по red"))

shutil.rmtree(TMP, ignore_errors=True)
print("\nИТОГ:", "ВСЕ PASS" if all(res) else "ЕСТЬ FAIL (%d/%d)" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
