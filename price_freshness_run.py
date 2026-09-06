# -*- coding: utf-8 -*-
"""РУКИ СТОРОЖА СВЕЖЕСТИ: принести факты и напечатать вердикт. Решение — в `price_freshness`.

Развязка та же, что у `expectations_pc` / `expectations_pc_run`: здесь диск и сеть, там чистая
функция. Смысл развязки не в опрятности — сторож обязан судиться тестом БЕЗ живого листа,
а живой лист обязан читаться без подмены вердикта.

ЧТО ДЕЛАЕТ: читает записанное правило `price_source.json` (слепок ручек и дату сборки), берёт
ПОЛОЖЕНИЕ ТЕХ ЖЕ РУЧЕК с живого листа дверью `quote_price` (GET, только чтение) и печатает
вердикт с действием бота.

ТОЛЬКО ЧТЕНИЕ. Ни одной записи: `set_*`/`toggle_*` не зовутся, ячеек листа никто не касается,
`price_source.json` не переписывается. Секреты моста берёт САМ одолженный клиент демона —
здесь их не читают и не видят (запрет класса 328).

ЛИСТ НЕДОСТУПЕН — ЭТО ИСХОД, А НЕ АВАРИЯ. Отказ двери приходит наверх как
`{"ok": False, "error": …}`, сторож зовёт это `НЕИЗВЕСТНО`, и цена не называется. Пустого
словаря ручек вместо отказа не бывает ни на одной дороге: он означал бы «ручек ноль, значит
все сошлись».

Запуск (из корня репозитория):
    venv\\Scripts\\python.exe price_freshness_run.py              — живой лист + вердикт
    venv\\Scripts\\python.exe price_freshness_run.py --no-sheet   — БЕЗ листа (третий исход)
"""

import io
import json
import os
import sys

import price_freshness

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "price_source.json")

# Юниты-представители категорий листа. Разбор 15.08 (docs/artifacts/2026-08-15-manager-price-sheet.md
# §2.2): H3 «мото-1» — XSR155/CB300R/REBEL/MT-03/NINJA400; I3 «мото-2» — VULCAN/CBR650/CB650;
# J3 «скутеры» — NMAX/XMAX/ADV/CLICK/FORZA/XADV. По ТРИ на категорию сознательно: один юнит
# доказал бы только своё число, а предмет сверки — КАТЕГОРИЙНАЯ ручка.
PROBES = (
    ("H3", ("XSR 155СС GREEN", "MT-03 300СС BLUE PHUKET 5068", "NINJA 400СС PHUKET 6334")),
    ("I3", ("CBR 650R PHUKET 4505", "CB 650R BLACK PHUKET 3503", "VULCAN 650CC S PHUKET 5065")),
    ("J3", ("NMAX 155CC GREY PHUKET 5960", "XMAX 300CC BLUE PHUKET 5773", "ADV 350CC GREY BKK 798")),
)

# Окно ровно 1 сутки: при d<=3 скидка за срок нулевая у всех кривых, значит в ответе виден угол
# ГЛОБАЛЬНОЙ ручки, а не ступень срока.
WINDOW = ("15.09.2026", "16.09.2026")


def read_rule(path=None):
    """Записанное правило → (слепок | None, почему нет). None — честное «не прочитал»,
    пустым словарём оно не подменяется: пустой слепок сторож принял бы за «сверять нечего»
    с тем же исходом, но с ЛОЖНОЙ причиной в карточке владельцу."""
    target = path if path else PATH
    try:
        with io.open(target, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        return None, "%s не прочитан (%s)" % (os.path.basename(target), type(exc).__name__)
    snap = price_freshness.snapshot_of(doc)
    if snap is None:
        return None, "в %s нет годного блока freshness" % os.path.basename(target)
    return snap, None


def live_handles(get=None):
    """Живой лист → факты для сторожа. Никогда не бросает: отказ двери — это ИСХОД.

    `get(action, **kw)` инъектируется тестом; боевой путь — клиент моста, одолженный у демона.
    """
    caller = get
    if caller is None:
        try:
            import queue_snapshot_pc as qs
            daemon = qs._guard_test_logs(qs._daemon)
            # ПАСПОРТ ПОРОГА: терпит=ОДИН GET к мосту, когда руки подняли клиента САМИ
            #   | замер=НЕТ, число круглое. И у него есть НАЗВАННАЯ АСИММЕТРИЯ: та же самая
            #   проба идёт с ДРУГИМ таймаутом, когда её зовёт врезка — `price_gate._bridge_caller`
            #   не ставит `bc.timeout` вовсе и работает на боевых 90с. Одна проба, два разных
            #   потолка, и выбирает потолок не проба, а тот, кто её позвал. Замера, который сказал
            #   бы, какой из двух верен, на полосе нет — поэтому НЕ ТРОГАЕМ ни один
            #   | снят=— | делится=НЕ ДЕЛИТСЯ — потолок одного вызова
            #   | предел-единицы=НЕ НУЖЕН: единица и есть этот вызов. Но потолок НЕ РАВЕН стенному
            #   времени: над ним лестница повторов `bridge_http` (READ_TRIES=3 целиком новых
            #   запроса), и замер 06.09 даёт живой хвост 111.7с при таймауте 90с
            #   | род=латентность
            #   | артефакт=docs/artifacts/2026-09-06-правило-порога-запись-замера-06.09.md
            daemon.bc.timeout = 240
            caller = daemon.bc._get
        except Exception as exc:                     # noqa: BLE001 — нужен сам факт отказа
            return {"ok": False, "handles": None,
                    "error": "клиент моста не поднялся (%s)" % type(exc).__name__}

    handles, detail, refusals = {}, {}, []
    for cell, bikes in PROBES:
        values = []
        for bike in bikes:
            try:
                answer = caller("quote_price", bike=bike,
                                date_start=WINDOW[0], date_end=WINDOW[1])
            except Exception as exc:                 # noqa: BLE001 — отказ двери, не авария
                # СЛОВА ОТКАЗА, а не только имя класса: у врезки два разных RuntimeError —
                # «бюджет пробы исчерпан» и «проба не уложилась в свой предел», — и по одному
                # имени класса владелец не отличил бы «дверь не спрашивали» от «дверь молчала».
                said = str(exc).strip()
                refusals.append("%s/%s: %s%s" % (cell, bike, type(exc).__name__,
                                                 (" — %s" % said[:70]) if said else ""))
                continue
            if not isinstance(answer, dict) or answer.get("ok") is not True:
                refusals.append("%s/%s: дверь отказала" % (cell, bike))
                continue
            season = answer.get("season")
            value = season.get("global_discount") if isinstance(season, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                refusals.append("%s/%s: ручки в ответе нет" % (cell, bike))
                continue
            values.append((bike, float(value)))
        distinct = sorted({v for _, v in values})
        if len(distinct) == 1:
            handles[cell] = distinct[0]
        else:
            handles[cell] = None                     # None = «не снялась», а не «ноль»
        detail[cell] = {"units": values, "distinct": distinct}
    ok = any(v is not None for v in handles.values())
    facts = {"ok": ok, "handles": handles, "detail": detail}
    if refusals:
        # СЛЕД СРАБАТЫВАНИЯ (правило порога 06.09.2026, п.5): срез `[:6]` МОЛЧА терял хвост
        # отказов — при девяти пробах и девяти отказах владелец видел шесть и не знал, что их
        # девять. Теперь число названо ВСЕГДА, а срезано только перечисление. Число отказов —
        # это и есть след бюджета проб (`PRICE_GATE_PROBE_SEC`/`PRICE_GATE_ONE_SEC`): их броски
        # приходят сюда поштучно и иначе нигде не считаются.
        facts["refused"] = len(refusals)
        facts["error"] = "; ".join(refusals[:6])
        if len(refusals) > 6:
            facts["error"] += " … и ещё %d (всего отказов %d из %d проб)" % (
                len(refusals) - 6, len(refusals), sum(len(b) for _c, b in PROBES))
    if not ok:
        facts["error"] = facts.get("error") if facts.get("error") else "живой лист не ответил"
    return facts


def report(snap, why_no_snap, facts, env=None, now=None):
    """Факты → (вердикт, действие бота). Порог берётся ручкой окружения ровно здесь."""
    limit = price_freshness.max_age_days(env if env is not None else dict(os.environ))
    verdict = price_freshness.judge(snap, facts, now=now, max_age=limit)
    if snap is None and why_no_snap:
        verdict["why"] = "%s (%s)" % (verdict["why"], why_no_snap)
    return verdict, price_freshness.bot_action(verdict)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")

    snap, why = read_rule()
    if snap is None:
        print("ЗАПИСАННОЕ ПРАВИЛО: НЕ ПРОЧИТАНО — %s" % why)
    else:
        print("ЗАПИСАННОЕ ПРАВИЛО: слепок от %s (сборка %s), ручек в слепке %d: %s"
              % (snap.get("snapshot_on"), snap.get("built_on"), len(snap["handles"]),
                 ", ".join("%s=%s" % (c, snap["handles"][c]) for c in sorted(snap["handles"]))))
        print("АДРЕС РУЧЕК (из слепка): %s" % snap.get("sheet"))

    if "--no-sheet" in args:
        facts = {"ok": False, "handles": None, "error": "лист не опрашивался (--no-sheet)"}
        print("\nЖИВОЙ ЛИСТ: не опрашивался (--no-sheet)")
    else:
        facts = live_handles()
        print("\nЖИВОЙ ЛИСТ (GET quote_price, окно %s..%s):" % WINDOW)
        for cell, _ in PROBES:
            info = facts["detail"][cell] if isinstance(facts.get("detail"), dict) else {}
            units = info.get("units") if isinstance(info, dict) else None
            got = facts["handles"].get(cell) if isinstance(facts.get("handles"), dict) else None
            print("  %-2s юнитов ответило %d, разных значений %d → %s"
                  % (cell, len(units) if units else 0,
                     len(info.get("distinct")) if isinstance(info, dict) and info.get("distinct") else 0,
                     got if got is not None else "НЕ СНЯЛАСЬ"))
        if facts.get("error"):
            print("  отказы двери: %s" % facts["error"])

    verdict, action = report(snap, why, facts)
    print("\nПОРОГ ВОЗРАСТА: %s=%.0f сут%s"
          % (price_freshness.MAX_AGE_ENV, verdict["max_age_days"],
             " (ветка выключена)" if verdict["max_age_days"] <= 0 else ""))
    print("ВОЗРАСТ СЛЕПКА: %s"
          % ("%.0f сут" % verdict["age_days"] if verdict["age_days"] is not None else "неизвестен"))
    print("ВЕРДИКТ: %s — %s" % (verdict["state"], verdict["why"]))
    print("БОТ: цену клиенту %s; владельца %s"
          % ("НАЗЫВАЕТ" if action["name_price_to_client"] else "НЕ НАЗЫВАЕТ",
             "зовёт" if action["call_owner"] else "не зовёт"))
    if action["owner_card"]:
        print("КАРТОЧКА ВЛАДЕЛЬЦУ: %s" % action["owner_card"])
    if "--json" in args:
        print(json.dumps({"verdict": verdict, "action": action}, ensure_ascii=False, indent=1))
    return 0 if verdict["state"] == price_freshness.FRESH else 2


if __name__ == "__main__":
    sys.exit(main())
