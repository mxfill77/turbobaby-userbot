# -*- coding: utf-8 -*-
"""Голден трёх правок pretool_guard (23.07.2026; в2 — headless-тупик 339/340):
  (а) чёрный список процессов  — kill/pkill/systemctl kill|stop по боевым процессам и PID 1 → HARD-BLOCK
      (deny, карточки НЕТ, approve НЕВОЗМОЖЕН, событие proc_hard_block в журнале); в2: systemctl
      restart|start СВОИХ сервисов — ЗЕЛЁНОЕ (штатный поток, defer к allow settings), ask здесь
      ломал headless (красное не исполнялось даже после «да» владельца);
  (б) данные ≠ команда         — красное слово в ПОИСКОВОМ ШАБЛОНЕ grep/rg/sed/awk не краснит команду,
      но ОПЕРАНДЫ и соседние звенья цепи остаются под сканом (дыру не открыли);
  (в) hard-block файла секретов — обращение к нему в ЛЮБОЙ позиции цепи → deny, событие env_hard_block;
  (г) в3 (23.07.2026)          — ambiguous САМ ПО СЕБЕ не красный: незнакомая команда, heredoc/stdin,
      неизвестный -m, нечитаемый .py, кривое квотирование → defer (решение None, конверта НЕТ);
      красное гарда — ТОЛЬКО доктринальный список (таблицы/CRM/деньги, секреты, sqlite вне memory.db,
      kill/stop боевых, PID 1) — и оно ловится И СКВОЗЬ heredoc (секция 13).

ТЕСТ НИЧЕГО НЕ ИСПОЛНЯЕТ: только импорт модуля и ЧИСТЫЕ функции classify()/can_approve()/decision()/
_card()/_guard_log(). Ни одна проверяемая строка не уходит в shell — ни один процесс не трогается.

Красные литералы собраны КОНКАТЕНАЦИЕЙ из кусков (образец — test_guard_тест_entity): иначе гард
краснеет на САМОМ файле теста при его чтении/правке, и правка файла становится невозможной.
"""
import json
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PRETOOL_NOPUSH"] = "1"      # страховка: ни одна карточка не уйдёт в Telegram

# pretool_guard тянет fcntl (POSIX-локи дедупа). Классификация от него не зависит: на ПК
# (Windows-клон репо) подставляем пустышку, на VPS импортируется настоящий модуль.
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        _fake = types.ModuleType("fcntl")
        _fake.flock = lambda *a, **k: None
        _fake.LOCK_EX = 2
        sys.modules["fcntl"] = _fake

import pretool_guard as PG

# ── красные литералы по кускам ────────────────────────────────────────────────
K = "k" + "ill"
PK = "p" + K
SC = "system" + "ctl"
SPL = "spl" + "inter"
ORCH = "orchestrator" + "-daemon"
UB = "user" + "bot"
MB = "moderation" + "_bot"
PCA = "pc" + "_agent"
ENVF = "." + "env"
TRX = "add_" + "transaction"
AWK_EXEC = "awk 'BEGIN{" + "system" + '("' + PK + " -9 " + SPL + '")}' + "'"


def ok(c, label):
    print(("  PASS " if c else "  FAIL ") + label)
    return bool(c)


def cls(cmd):
    return PG.classify(cmd, ROOT)


def is_block(cmd, event):
    """Жёсткий блок: kind=block, ожидаемое событие журнала, approve невозможен, решение хука = deny.
    can_approve()=False — это И ЕСТЬ «без карточки»: main() строит и пушит карточку только на
    ветке approve-возможен, до неё жёсткий блок не доходит."""
    kind, hit, blob = cls(cmd)
    if kind != "block" or hit != event:
        return False
    d = PG.decision(kind, hit, PG._block_reason(hit, blob))
    return (PG.can_approve(kind, hit) is False
            and d["hookSpecificOutput"]["permissionDecision"] == "deny"
            and hit in PG.HARD_BLOCK_HITS)


def red_card(cmd):
    """Обычное КРАСНОЕ: kind=red, approve ВОЗМОЖЕН, решение = ask, карточка владельцу собралась.
    → (проверка, текст карточки)."""
    kind, hit, blob = cls(cmd)
    if kind != "red":
        return False, "kind=" + kind
    card = PG._card(hit, blob, False, cmd=cmd)
    d = PG.decision(kind, hit, card)
    good = (PG.can_approve(kind, hit) is True
            and d["hookSpecificOutput"]["permissionDecision"] == "ask"
            and card.startswith("🔴 КРАСНОЕ")
            and "жду твоё «да»" in card)
    return good, card


def is_green(cmd):
    kind, hit, _ = cls(cmd)
    return kind == "green" and PG.decision(kind, hit, "") is None


res = []

print("(1) ТЗ-кейсы: чёрный список процессов (approve НЕВОЗМОЖЕН, карточки нет):")
res.append(ok(is_block(PK + " -9 " + SPL, "proc_hard_block"), PK + " -9 " + SPL + " → блок"))
res.append(ok(is_block(SC + " " + K + " " + ORCH, "proc_hard_block"),
              SC + " " + K + " " + ORCH + " → блок"))

print("(2) ТЗ-кейс: сервис ВНЕ списка — обычное красное, не жёсткое:")
good, card = red_card(SC + " " + K + " nginx")
res.append(ok(good, SC + " " + K + " nginx → красное с карточкой (approve возможен)"))
res.append(ok("nginx" in card, "цель nginx попала в карточку: " + card.splitlines()[1][:60]))

print("(3) ТЗ-кейс: данные ≠ команда — красное слово в шаблоне поиска не краснит:")
grep_cmd = 'grep -n "' + K + "|" + PK + "|" + ENVF + '" file.py'
res.append(ok(is_green(grep_cmd), grep_cmd + " → ЗЕЛЁНОЕ"))

print("(4) ТЗ-кейс: файл секретов в цепи — жёсткий блок:")
res.append(ok(is_block("cat " + ENVF + " && python gate.py", "env_hard_block"),
              "cat <секреты> && python gate.py → блок (ранний defer гейта НЕ спасает команду)"))

print("(5) ТЗ-кейсы в2: restart|start своих — ЗЕЛЁНОЕ; отложенный/сигнальный stop — deny:")
res.append(ok(is_green(SC + " restart " + SPL), SC + " restart " + SPL + " → ЗЕЛЁНОЕ (штатный поток)"))
res.append(ok(is_green(SC + " start " + ORCH), SC + " start " + ORCH + " → ЗЕЛЁНОЕ"))
res.append(ok(is_green("systemd-run --on-active=10s " + SC + " restart " + SPL),
              "отложенный restart своего → ЗЕЛЁНОЕ (allow-правило settings)"))
res.append(ok(is_block(SC + " stop " + SPL, "proc_hard_block"), SC + " stop " + SPL + " → deny"))
res.append(ok(is_block(PK + " -9 " + SPL, "proc_hard_block"), PK + " -9 " + SPL + " → deny"))
res.append(ok(is_block(K + " -SIGKILL 1", "proc_hard_block"), K + " -SIGKILL 1 → deny (PID 1)"))
res.append(ok(is_block("systemd-run --on-active=60 " + SC + " stop " + SPL, "proc_hard_block"),
              "systemd-run --on-active=60 " + SC + " stop " + SPL + " → deny (отложенный stop)"))

print("(6) чёрный список целиком + PID 1:")
for tgt in (SPL, ORCH, UB, MB, PCA):
    res.append(ok(is_block(PK + " -f " + tgt, "proc_hard_block"), PK + " -f " + tgt + " → блок"))
    res.append(ok(is_block(SC + " stop " + tgt, "proc_hard_block"), SC + " stop " + tgt + " → блок"))
res.append(ok(is_block(K + " -9 1", "proc_hard_block"), K + " -9 1 → блок (PID 1)"))
res.append(ok(is_block(K + " 1", "proc_hard_block"), K + " 1 → блок (PID 1)"))
res.append(ok(is_block("sudo " + SC + " stop " + SPL, "proc_hard_block"), "sudo-обёртка → блок"))
res.append(ok(is_block("systemd-run --on-active=10s " + SC + " stop " + SPL, "proc_hard_block"),
              "отложенный запуск (systemd-run) → блок"))
res.append(ok(is_block(SC + " " + K + " --signal=SIGKILL " + SPL, "proc_hard_block"),
              "цель после флага со значением → блок"))
res.append(ok(is_block(PK + ' -f "python3 /root/turbobaby-manager-bot/' + SPL + '.py"',
                       "proc_hard_block"), "цель внутри кавычек -f → блок"))
res.append(ok(is_block(SC + " restart nginx && " + PK + " -9 " + SPL, "proc_hard_block"),
              "жёсткое во ВТОРОМ звене цепи не заслонено красным первого"))
res.append(ok(is_block(AWK_EXEC, "proc_hard_block"),
              "исполнение внутри программы awk (system) шаблоном НЕ прикрылось → блок"))

print("(7) обычное красное (не жёсткое) — процессы вне списка:")
for cmd in (K + " -s TERM 4242", PK + " -9 chrome", SC + " stop nginx"):
    good, _c = red_card(cmd)
    res.append(ok(good, cmd + " → красное с карточкой"))

print("(8) файл секретов в любой позиции и любым инструментом:")
for cmd in ("cat " + ENVF,
            "head -5 " + ENVF + ".local",
            "source /root/turbobaby-manager-bot/" + ENVF,
            'bash -c "cat ' + ENVF + '"',
            "python3 -c \"print(open('" + ENVF + "').read())\"",
            "sed -n 1p " + ENVF,
            "grep -n TOKEN " + ENVF,
            "python3 gate.py && cat " + ENVF,
            "cat " + ENVF + " | head -2"):
    res.append(ok(is_block(cmd, "env_hard_block"), cmd[:72] + " → блок"))

print("(9) дыру НЕ открыли: операнды и соседние звенья остаются под сканом:")
res.append(ok(is_block("grep -n foo " + ENVF, "env_hard_block"),
              "шаблон вырезан, а ФАЙЛ-операнд остался → блок"))
res.append(ok(is_block("grep -n " + ENVF + " " + ENVF, "env_hard_block"),
              "тот же текст шаблоном и операндом → блок по операнду"))
res.append(ok(is_block('grep -e "x" ; ' + PK + " -9 " + SPL, "proc_hard_block"),
              "второе звено цепи после ; → блок"))
res.append(ok(is_block("grep -e x&&" + PK + " -9 " + SPL, "proc_hard_block"),
              "склейка без пробелов (&&) → блок"))
res.append(ok(is_block('grep -e "$(cat ' + ENVF + ')" f.py', "env_hard_block"),
              "подстановка $(…) внутри шаблона раскрыта → блок"))
res.append(ok(is_block('git commit -m "$(cat ' + ENVF + ')"', "env_hard_block"),
              "подстановка внутри текста git -m раскрыта → блок"))

print("(10) регресс: рутина осталась зелёной (шума не добавили):")
for cmd in ("grep -n def /root/turbobaby-manager-bot/bot.py",
            "cat /root/turbobaby-manager-bot/" + SPL + ".log",
            "grep -rn " + ENVF + " .",
            'rg -e "' + PK + '" -n .',
            "sed -n '/" + K + "/p' notes.txt",
            SC + " daemon-reload",
            SC + " restart nginx",
            "node --check tests/booking_gs_harness.js",
            'git commit -m "правка ' + ENVF + ' шаблона"',
            'git commit -m "фикс python скрипта"',
            "python3 --version",
            "venv/bin/python3 -V"):
    res.append(ok(is_green(cmd), cmd[:72] + " → зелёное"))

print("(11) регресс: python-скан НЕ ослаблен:")
kind, hit, _b = cls('python3 -c "' + TRX + '(amount=500)"')
res.append(ok(kind == "red" and hit == TRX, "инлайн денежная операция → красное (" + hit + ")"))
kind, hit, _b = cls("python3 -")
res.append(ok(kind == "ambiguous" and hit == "ambiguous", "stdin '-' → класс ambiguous (в3: решение defer, см. 13)"))
kind, hit, _b = cls("python3 -m some_unknown_module")
res.append(ok(kind == "ambiguous", "неизвестный -m → класс ambiguous"))
res.append(ok(PG.can_approve("red", TRX) and PG.can_approve("ambiguous", "ambiguous"),
              "красное по-прежнему approve-able (жёсткое — только два события)"))

print("(12) журнал жёстких блоков (событие пишется, значений секретов в строке нет):")
tmp = tempfile.mkdtemp(prefix="pt_hb_")
logp = os.path.join(tmp, "guard.log")
os.environ["PRETOOL_GUARD_LOG"] = logp
for cmd in (PK + " -9 " + SPL, "cat " + ENVF):
    kind, hit, blob = cls(cmd)
    PG._guard_log(hit, cmd, PG._block_reason(hit, blob))
os.environ.pop("PRETOOL_GUARD_LOG", None)
rows = [json.loads(ln) for ln in open(logp, encoding="utf-8").read().splitlines() if ln.strip()]
shutil.rmtree(tmp, ignore_errors=True)
res.append(ok([r.get("event") for r in rows] == ["proc_hard_block", "env_hard_block"],
              "две строки JSONL: " + ", ".join(r.get("event", "?") for r in rows)))
res.append(ok(all(r.get("cmd") for r in rows), "команда в строке журнала есть"))
res.append(ok(PG.HARD_BLOCK_HITS == ("proc_hard_block", "env_hard_block"),
              "имена событий журнала = ключи жёсткого блока"))


def is_defer_ambiguous(cmd):
    """в3: класс ambiguous, решение хука None (defer) — ни ask, ни deny, конверта НЕТ."""
    kind, hit, _b = cls(cmd)
    return kind == "ambiguous" and hit == "ambiguous" and PG.decision(kind, hit, "") is None


print("(13) в3: ambiguous сам по себе НЕ красный — defer; красное только по доктрине:")
HD_Q = ("python3 - <<PYEOF\n"
        "import json\n"
        "q = json.load(open('tmp/tasks_queue.json'))\n"
        "print(len(q))\n"
        "PYEOF")
res.append(ok(is_defer_ambiguous(HD_Q), "python3 - <<PYEOF (чтение очереди) → defer (зелёное)"))
res.append(ok(is_defer_ambiguous("python3 -"), "stdin '-' без heredoc → defer"))
res.append(ok(is_defer_ambiguous("python3 -m some_unknown_module"), "неизвестный -m → defer"))
res.append(ok(is_defer_ambiguous("python3 'незакрытая кавычка"), "кривое квотирование → defer"))
res.append(ok(is_green("frobnicate --such-flag-much-unknown"), "незнакомая не-python команда → defer"))

print("(13б) …но доктринальное красное СКВОЗЬ heredoc/stdin ловится по-прежнему:")
kind, hit, _b = cls("python3 - <<PYEOF\nfrom bridge_client import api\n" + TRX + "(amount=500)\nPYEOF")
res.append(ok(kind == "red" and hit == TRX, "heredoc с денежной операцией → красное (" + hit + ")"))
kind, hit, _b = cls("python3 - <<PYEOF\nimport sqlite3\ncon = sqlite3.connect('/root/x/tasks.db')\n"
                    "con.execute('UPDATE tasks SET s=1')\nPYEOF")
res.append(ok(kind == "red" and hit == "sqlite", "heredoc с UPDATE чужой .db → красное (sqlite)"))
res.append(ok(is_defer_ambiguous("python3 - <<PYEOF\nimport sqlite3\ncon = sqlite3.connect('memory.db')\n"
                                 "con.execute('UPDATE tasks SET s=1')\nPYEOF"),
              "тот же UPDATE в СВОЮ memory.db → defer (доктрина 02.07 цела)"))
res.append(ok(is_block("python3 - <<PYEOF\nimport os\nos.system('cat " + ENVF + "')\nPYEOF",
                       "env_hard_block"), "heredoc с чтением секретов → блок"))
res.append(ok(is_block("python3 - <<PYEOF\nimport os\nos.system('" + PK + " -9 " + SPL + "')\nPYEOF",
                       "proc_hard_block"), "heredoc с гашением боевого процесса → блок"))
FLEET_OIL = "set_fleet_" + "oil"
tmp2 = tempfile.mkdtemp(prefix="pt_v3_").replace(os.sep, "/")   # forward slashes: shlex ест бэкслэши ПК-клона
with open(tmp2 + "/fx_red_live.py", "w", encoding="utf-8") as _f:
    _f.write("# фикстура\nprint('" + FLEET_OIL + "')\n")
kind, hit, _b = cls("python3 " + tmp2 + "/fx_red_live.py && python3 " + tmp2 + "/fx_absent.py")
shutil.rmtree(tmp2, ignore_errors=True)
res.append(ok(kind == "red" and hit == FLEET_OIL,
              "red.py && нечитаемый.py → красное (ambiguous-звено red-скан НЕ заслоняет)"))

print("\nИТОГ:", "ВСЕ PASS" if all(res) else "ЕСТЬ FAIL (%d/%d)" % (sum(res), len(res)))
sys.exit(0 if all(res) else 1)
