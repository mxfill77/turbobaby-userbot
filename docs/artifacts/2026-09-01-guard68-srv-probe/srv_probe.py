# -*- coding: utf-8 -*-
"""Прогон КЛАССИФИКАТОРА гарда ВТОРОЙ полосы (VPS) по нашему классу — с ПК.

Мерим живое дерево сервера (`git show origin/main:pretool_guard.py`, извлечено в
srv_live/), а не снимок tmp/srv от 01.08: за месяц файл вырос с 1441 до 2899 строк.

Ничего не исполняет: зовётся только `classify(cmd)` — та самая единая точка,
которую по её докстрингу зовут main() и тесты («исполнять ничего не требуется»).
Модуль импортирует POSIX-only fcntl — подставляем шим, как в срв-раннере (класс
«серверные тесты с ПК»).

Запуск:  venv/Scripts/python.exe tmp/guard68/srv_probe.py
"""
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
# каталог с гардом второй полосы: srv_live — как на origin/main, srv_after — с правкой
SRV = os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "srv_live")

# ── шим fcntl: на Windows его нет, а гард импортирует его первой строкой ──────────────
if "fcntl" not in sys.modules:
    _f = types.ModuleType("fcntl")
    _f.LOCK_EX = 2
    _f.LOCK_UN = 8
    _f.LOCK_NB = 4
    _f.flock = lambda *a, **k: None
    _f.lockf = lambda *a, **k: None
    sys.modules["fcntl"] = _f

sys.path.insert(0, SRV)
import pretool_guard as g                       # noqa: E402

# «красное» у второй полосы — это kind ∈ {block, red}; green/ambiguous операцию не краснят
RED = ("block", "red")

CASES = [
    # id, команда, ожидание ПРАВИЛЬНОГО поведения, зачем
    ("S1", "git rm --cached docs/artifacts/x.md",
     "molchit", u"снятие с ИНДЕКСА: файл на диске цел, обратно одним git add"),
    ("S2", "git rm --cached docs/artifacts/x.md && git add docs/artifacts/x.md",
     "molchit", u"живой образец карточки 65: следом идёт git add"),
    ("S3", "git rm -r --cached docs/artifacts",
     "molchit", u"та же форма с рекурсией — тоже только индекс"),
    ("S4", "git rm docs/artifacts/x.md",
     "krasnoe", u"ЗАЩИТА: настоящее удаление из рабочего дерева"),
    ("S5", "rm -rf docs/artifacts",
     "krasnoe", u"ЗАЩИТА: доктринальное красное, класс не наш"),
    ("S6", "git rm --cached",
     "krasnoe", u"ЗАЩИТА: форма без операндов — цель не названа, fail-closed"),
]


def main():
    print(u"гард второй полосы: %s" % os.path.abspath(g.__file__))
    print(u"строк: %d" % len(io.open(g.__file__, encoding="utf-8", errors="replace").readlines()))
    print(u"-" * 100)
    bad = 0
    for cid, cmd, expect, why in CASES:
        kind, hit, _blob = g.classify(cmd, "/root/turbobaby-manager-bot")
        fact = "krasnoe" if kind in RED else "molchit"
        ok = (fact == expect)
        bad += 0 if ok else 1
        print(u"%-3s %-52s ожидание=%-8s факт=%-8s kind=%-10s hit=%-14s %s" % (
            cid, cmd[:52], expect, fact, kind, hit or "-",
            u"OK" if ok else u"РАСХОЖДЕНИЕ"))
        print(u"      %s" % why)
    print(u"-" * 100)
    print(u"ИТОГО: случаев %d, расхождений с ожиданием %d" % (len(CASES), bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
