# -*- coding: utf-8 -*-
"""РУКИ ПРАВИЛА ПОРОГА: прочитать исходники и напечатать реестр паспортов.

Развязка та же, что у `price_freshness` / `price_freshness_run` и `expectations_pc` /
`expectations_pc_run`: здесь диск, там чистая функция. Смысл развязки не в опрятности —
судья обязан судиться тестом БЕЗ диска, а диск обязан читаться без подмены вердикта.

ТОЛЬКО ЧТЕНИЕ. Ни одной записи: файлы открываются на чтение, ничего не правится, в сеть
модуль не ходит, `.env` и конфигов не касается.

ФАЙЛ НЕ ПРОЧИТАН — ЭТО ИСХОД, А НЕ АВАРИЯ: он приезжает в судью как `None` и получает
КРАСНЫЙ вердикт `ПРОЧЕСТЬ НЕ СМОГЛИ`. Пропустить непрочитанный файл молча значило бы
превратить незнание в зелёный — ровно тот ложный зелёный, против которого правило и заведено.

Запуск (из корня репозитория):
    venv\\Scripts\\python.exe threshold_ledger_run.py            — реестр и вердикт
    venv\\Scripts\\python.exe threshold_ledger_run.py --red      — только красное
    venv\\Scripts\\python.exe threshold_ledger_run.py --json     — машинам

Код возврата: 0 — красного нет; 2 — есть. Это ответ прибора, а не «всё хорошо»: пороги с
честной записью «замера нет» остаются НЕВЫЯСНЕННЫМИ и считаются отдельным числом.
"""

import io
import json
import os
import sys

import threshold_ledger as tl

HERE = os.path.dirname(os.path.abspath(__file__))


def read_sources(root=None, files=None):
    """{имя файла: текст | None}. None — не прочитан; беда не глотается, а едет в судью."""
    base = root if root else HERE
    names = files if files is not None else tl.COVERED_FILES + tuple(tl.COVERED_NAMES)
    out = {}
    for name in names:
        try:
            with io.open(os.path.join(base, name), encoding="utf-8") as f:
                out[name] = f.read()
        except OSError:
            out[name] = None
    return out


def run(root=None, today=None):
    """Реестр живого дерева → сводка судьи."""
    return tl.audit(read_sources(root), today=today, names=tl.COVERED_NAMES)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")
    out = run()

    if "--json" in args:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 2 if out["red"] else 0

    only_red = "--red" in args
    print("РЕЕСТР ОБЩИХ ПОРОГОВ — паспорт рядом со значением (правило 06.09.2026)\n")
    current = None
    for r in out["rows"]:
        if only_red and r["state"] not in tl.RED:
            continue
        if r["file"] != current:
            current = r["file"]
            print("── %s" % current)
        print("   %-24s = %-10s %-16s %s"
              % (r["name"], r["value"], r["state"],
                 (r["why"][:96] + "…") if len(r["why"]) > 97 else r["why"]))
        if r.get("unit") and r["unit"] != "НЕ ДЕЛИТСЯ":
            print("   %-24s   делится: %s" % ("", r["unit"][:80]))
            print("   %-24s   предел единицы: %s" % ("", (r["unit_limit"] or "—")[:80]))

    c = out["counts"]
    print("\nВСЕГО ПОРОГОВ В ОХВАТЕ: %d" % out["total"])
    print("  с живым замером ............ %d" % c[tl.OK])
    print("  помечены «замера нет» ...... %d" % c[tl.NO_MEASURE])
    print("  объявлены «не порог» ....... %d" % c[tl.NOT_A_THRESHOLD])
    print("  СВОЙ ПРЕДЕЛ ЕДИНИЦЫ ЕСТЬ ... %d  (%s)"
          % (len(out["with_unit_limit"]), ", ".join(out["with_unit_limit"]) or "—"))
    print("  КРАСНОЕ .................... %d  (записи нет %d · паспорт битый %d · "
          "замер протух %d · не прочитан %d)"
          % (len(out["red"]), c[tl.NO_RECORD], c[tl.BROKEN], c[tl.STALE], c[tl.UNREAD]))
    print("\n%s" % tl.UNCOVERED_NOTE)
    print("ГОРИЗОНТ ПРОТУХАНИЯ: %g сут для рода «%s» (у самого горизонта паспорт есть); "
          "род «%s» по календарю не протухает."
          % (tl.HORIZON_LATENCY_DAYS, tl.KIND_LATENCY, tl.KIND_DECISION))
    if out["red"]:
        print("\nВЕРДИКТ: КРАСНО — %d порог(ов) молчат о своём основании." % len(out["red"]))
        for r in out["red"]:
            print("  · %s:%s %s — %s" % (r["file"], r["line"], r["name"], r["why"]))
    else:
        print("\nВЕРДИКТ: красного нет. Это НЕ значит «все пороги обоснованы»: %d из %d "
              "честно помечены «замера нет» и остаются невыясненными."
              % (c[tl.NO_MEASURE], out["total"]))
    return 2 if out["red"] else 0


if __name__ == "__main__":
    sys.exit(main())
