"""Регресс сужения ambiguous-ask в pretool_guard (заведено 06.07.2026).

Правка: инфо-флаги интерпретатора (--version/-V/-VV/--help/-h) и инертные stdlib-модули
(platform/sysconfig/site) → ЗЕЛЁНОЕ (defer), а не ложный ambiguous-ask. Раньше
`venv/bin/python3 --version` не имел .py-цели → падал в ask, хотя venv python в allow.

КРАСНОЕ — ОСТАЁТСЯ ask. в3 (23.07.2026): настоящий ambiguous (stdin `-`, неизвестный `-m`,
нечитаемый .py, флаг вне белого списка) → defer БЕЗ конверта — ambiguous сам по себе не красный,
решают слои settings; красное гарда — только доктринальный список.
Пуши глушим PRETOOL_NOPUSH=1 (ноль карточек в личку).
"""
import os
import sys
import json
import tempfile
import subprocess

ROOT = "/root/turbobaby-manager-bot"
PY = os.path.join(ROOT, "venv", "bin", "python3")
PRETOOL = os.path.join(ROOT, "pretool_guard.py")


def run(cmd):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": ROOT})
    env = dict(os.environ, PRETOOL_NOPUSH="1")
    return subprocess.run([PY, PRETOOL], input=payload, capture_output=True, text=True,
                          timeout=60, env=env)


def ok(c, label):
    print(("  PASS " if c else "  FAIL ") + label)
    return c


def is_green(cmd):
    r = run(cmd)
    return r.returncode == 0 and '"ask"' not in r.stdout


def is_ask(cmd):
    r = run(cmd)
    return r.returncode == 0 and '"ask"' in r.stdout


res = []

print("Инфо-флаги интерпретатора → ЗЕЛЁНОЕ (defer, без ask):")
for cmd in [
    PY + " --version",
    PY + " -V",
    "python3 --version",
    "venv/bin/python3 --help",
    "python3 -h",
]:
    res.append(ok(is_green(cmd), cmd + " → green"))

print("Инертные stdlib-модули → ЗЕЛЁНОЕ:")
for cmd in [
    PY + " -m platform",
    PY + " -m sysconfig",
    PY + " -m site",
    PY + " -m py_compile splinter.py",
]:
    res.append(ok(is_green(cmd), cmd + " → green"))

print("КРАСНОЕ по-прежнему ask (регресс: сужение не пробило red-детект):")
tmp = tempfile.mkdtemp(prefix="pt_if_")
red = os.path.join(tmp, "recon_fleet.py")   # боевое имя, но PRETOOL_NOPUSH=1 глушит пуш
with open(red, "w", encoding="utf-8") as f:
    f.write("# фикстура\nprint('set_fleet_oil')\n")
res.append(ok(is_ask(PY + " " + red), "скрипт с set_fleet_oil → ask"))
res.append(ok(is_ask(PY + " -c \"add_transaction(amount=500)\""), "inline add_transaction → ask"))

print("Настоящий ambiguous → в3: defer БЕЗ конверта (сам по себе не красный):")
res.append(ok(is_green(PY + " -"), "stdin '-' → defer"))
res.append(ok(is_green(PY + " -m some_unknown_module"), "неизвестный -m → defer"))
res.append(ok(is_green(PY + " --version --frobnicate"), "флаг вне белого списка → defer"))
missing = os.path.join(tmp, "nope_missing.py")
res.append(ok(is_green(PY + " " + missing), "нечитаемый .py-путь → defer"))

print("\nИТОГ:", "ВСЕ PASS" if all(res) else f"ЕСТЬ FAIL ({sum(res)}/{len(res)})")
sys.exit(0 if all(res) else 1)
