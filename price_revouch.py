# -*- coding: utf-8 -*-
"""ПЕРЕСВИДЕТЕЛЬСТВОВАНИЕ СЛЕПКА ЦЕН: дата двигается ТОЛЬКО за доказанным совпадением ВСЕЙ базы.

ЗАЧЕМ ОТДЕЛЬНЫЙ РЕЖИМ, А НЕ ПРАВКА ДАТЫ РУКАМИ. Сторож свежести (`price_freshness`) судит две
вещи: возраст `freshness.snapshot_on` против порога 14 суток и три ручки листа H3/I3/J3. Ручки
через дверь видны, а база по моделям — НЕТ, и ровно поэтому возрастная ветка вообще существует:
её собственная докстрока говорит «сошедшиеся ручки при мёртвом слепке — ровно тот случай, ради
которого владелец назвал ДАТУ СБОРКИ отдельным требованием». Значит любая дорога, двигающая дату
по одним ручкам, УБИВАЕТ возрастную ветку целиком: файл станет вечно свежим при протухшей базе.

Отсюда единственное законное основание сдвинуть дату без смены чисел: ВСЯ база сверена с живым
листом и совпала до бата. Тогда дата — не обещание, а новое наблюдение того же числа.

ЧЕМ ЭТОТ МОДУЛЬ НЕ ЯВЛЯЕТСЯ. Он НЕ публикует новый слепок: смена базы — решение владельца
(разбор — `docs/artifacts/2026-09-02-кандидат-слепка-цен-и-разница.md`), и её делает B1.1
(`price_snapshot_publish`). Здесь база не меняется НИ В ОДНОЙ строке ни на бат; при малейшем
расхождении режим ОТКАЗЫВАЕТ и не трогает файл вовсе.

СВОЕЙ АРИФМЕТИКИ ЦЕНЫ ЗДЕСЬ НЕТ НИ ОДНОЙ СТРОКИ. Пересчёт «цена суток листа → база низкого
сезона» делает штатный `price_snapshot_publish.build_candidate` — тот самый код, которым
собирается кандидат публикации, со своими замками (поколение не угадывается, юниты продукта не
усредняются, round-trip обязан сойтись до бата, неполный набор роняет заход). Своя формула здесь
означала бы вторую правду о цене; делегирование держится тестом
`test_price_revouch.TestDelegation`, а не обещанием.

ЗАМОК, КОТОРЫЙ ЗДЕСЬ ГЛАВНЫЙ: СВЕРКА ПУТЕЙ ДОКУМЕНТА. Обещание «тронута только дата» ничего не
стоит — его проверяет `changed_paths`: документ до и после раскладывается в плоский список
json-путей, и всё, что разошлось вне `ALLOWED_PATHS`, роняет заход ДО записи на диск. Поэтому
«режим случайно переписал базу» — не риск, а невозможное состояние: базы, множители, депозиты,
кепки, `source`, `base_method`, `owner_decision`, `n_bookings` и `p25_p75` доезжают байт в байт.

ПОЧЕМУ НЕ ИСПОЛЬЗОВАН ЗАМОК B1.1 `_assert_safe_output`. Тот запрещает выводить кандидат в файл с
именем `price_source.json` — и это правильный замок ДЛЯ ДРУГОЙ операции: кандидат несёт НОВЫЕ
базы, и молчаливая подмена боевого файла им стоила бы денег. Наша операция обратная по смыслу
(базы обязаны совпасть, иначе отказ), поэтому она пишет в боевой файл сознательно, но под
СТРОГО СИЛЬНЕЙШИМ замком: путевой инвариант выше плюс обратное чтение с дословной сверкой.
Снять путевой замок значит превратить этот модуль в тихую публикацию — не делать этого.

ТОЛЬКО ЧТЕНИЕ ЛИСТА. Живые факты приносит `price_snapshot_collect` двумя дверями GET
(`fleet`, `quote_price`); ни в лист, ни в Календарь, ни в CRM здесь не пишут. Секреты моста берёт
САМ одолженный клиент демона — этот файл окружения не читает (запрет класса 328).

ЗАПУСК (из корня репозитория):
    venv\\Scripts\\python.exe price_revouch.py --live --start 2026-09-07          # только сверка
    venv\\Scripts\\python.exe price_revouch.py --live --start 2026-09-07 --apply  # сверка + дата
    venv\\Scripts\\python.exe price_revouch.py --fixture <файл>.json              # без сети

ОТКАТ: у каждого `--apply` есть резервная копия `tmp/price_revouch/price_source.<штамп>.json`,
названная в выводе; возврат — обычное копирование её на место.
"""

import argparse
import copy
import datetime as dt
import io
import json
import os
import sys

import price_snapshot_publish as pub

# ── ЧТО РЕЖИМ ВПРАВЕ ТРОНУТЬ. Список — предмет теста, а не пожелание автора. ───────────────────
# Оба пути живут в блоке `freshness` и описывают МОМЕНТ наблюдения, а не само наблюдение.
# `built_on` сюда НЕ входит сознательно: правило не пересобиралось, и врать о дате сборки нельзя.
ALLOWED_PATHS = ("freshness.snapshot_on", "freshness.revouch")

VERDICT_MATCH = "СВЕРЕНО С ЖИВЫМ ЛИСТОМ ПОСТРОЧНО"


class RevouchError(ValueError):
    """Пересвидетельствование НЕ состоялось. Не «почти», не «с оговоркой» — не состоялось."""


# ═════════════════ ЧИСТЫЙ СЛОЙ: НИ ДИСКА, НИ СЕТИ, НИ ЧАСОВ ══════════════════════════════════

def _flatten(value, prefix, out):
    """Документ → {json-путь: значение листа}. Списки раскладываются ПО ИНДЕКСУ.

    Индекс намеренно часть пути: перестановка строк `base.models` — это изменение документа,
    и путевая сверка обязана его увидеть, а не списать на «тот же набор ключей»."""
    if isinstance(value, dict):
        for key in value:
            _flatten(value[key], "%s.%s" % (prefix, key) if prefix else str(key), out)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten(item, "%s[%d]" % (prefix, index), out)
    else:
        out[prefix] = value
    return out


def changed_paths(before, after):
    """Два документа → отсортированные пути, где они разошлись (включая появившиеся/исчезнувшие).

    Это и есть доказательство «тронута только дата». Сравнение идёт по ЗНАЧЕНИЯМ листьев, поэтому
    ни порядок ключей, ни отступы файла на исход не влияют — влияет только содержимое.
    """
    old = _flatten(before, "", {})
    new = _flatten(after, "", {})
    paths = set(old) | set(new)
    return sorted(p for p in paths if old.get(p, _MISSING) != new.get(p, _MISSING))


class _Missing(object):
    def __repr__(self):
        return "<нет пути>"


_MISSING = _Missing()


def _is_allowed(path):
    """Путь принадлежит объявленной зоне даты? Префикс, а не равенство: `freshness.revouch` —
    объект, и его внутренние поля обязаны попадать под то же разрешение."""
    return any(path == root or path.startswith(root + ".") or path.startswith(root + "[")
               for root in ALLOWED_PATHS)


def stray_paths(before, after):
    """Пути, тронутые ВНЕ объявленной зоны даты. Пусто — единственный законный исход."""
    return [p for p in changed_paths(before, after) if not _is_allowed(p)]


def _recorded_handles(source):
    """Ручки ЗАПИСАННОГО правила → {ячейка: угол}. Читается тем же взглядом, что у сторожа:
    ручка без числа — это «не записана», а не ноль."""
    block = source.get("freshness") if isinstance(source, dict) else None
    rows = block.get("handles") if isinstance(block, dict) else None
    if not isinstance(rows, list):
        raise RevouchError("в записанном правиле нет блока freshness.handles — сверять не с чем")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cell, value = row.get("cell"), row.get("global_discount")
        if not isinstance(cell, str) or not cell.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[cell.strip()] = float(value)
    if not out:
        raise RevouchError("в записанном правиле не разобрана ни одна ручка — сверять не с чем")
    return out


def compare(source, fixture):
    """(записанное правило, фикстура живого листа) → отчёт ПОСТРОЧНОЙ сверки. Чистая функция.

    Совпадением считается равенство ДО БАТА у КАЖДОЙ строки и у КАЖДОЙ ручки. Порога допуска нет
    и не будет: «почти совпало» на цене означает, что клиенту назовут не то число.
    """
    try:
        candidate = pub.build_candidate(source, fixture)
    except pub.CandidateError as exc:
        # Неполный/противоречивый живой набор — это тоже расхождение, просто названное раньше:
        # продукт, которому лист не дал ни одного юнита, сверить нечем, и «совпало» о нём сказать
        # нельзя ни на одной дороге.
        raise RevouchError("живой лист не дал сверяемого набора: %s" % exc)

    old = pub._base_index(source)
    new = pub._base_index(candidate)
    rows, diverged = [], []
    for key in sorted(old):
        was = int(round(old[key]["base_thb_per_day"]))
        now = int(round(new[key]["base_thb_per_day"]))
        row = {"model": key[0], "generation": key[1] or None,
               "recorded_thb_per_day": was, "live_thb_per_day": now,
               "agree": was == now,
               "delta_thb": now - was,
               "delta_pct": round((now - was) * 100.0 / was, 2) if was else None,
               "base_method": old[key].get("base_method"),
               "owner_decision": bool(old[key].get("owner_decision"))}
        rows.append(row)
        if not row["agree"]:
            diverged.append(row)

    want = _recorded_handles(source)
    got = pub._handle_map(fixture)
    handles_diverged = [{"cell": cell, "recorded": want[cell], "live": got.get(cell)}
                        for cell in sorted(want) if got.get(cell) != want[cell]]
    handles_extra = sorted(set(got) - set(want))

    units = 0
    for product in fixture.get("products") or []:
        units += len(product.get("units") or [])

    return {
        "matched": not diverged and not handles_diverged and not handles_extra,
        "products_checked": len(rows),
        "products_agreed": len(rows) - len(diverged),
        "rows": rows,
        "diverged": diverged,
        "handles_recorded": want,
        "handles_live": got,
        "handles_diverged": handles_diverged,
        "handles_extra": handles_extra,
        "units_counted": units,
        "window": copy.deepcopy(fixture.get("source_window")),
        "fixture_snapshot_id": fixture.get("snapshot_id"),
        "fixture_generated_at": fixture.get("generated_at"),
        "recorded_snapshot_on": (source.get("freshness") or {}).get("snapshot_on"),
    }


def refusal_text(report):
    """Отчёт несовпадения → ПЕРВАЯ разошедшаяся строка словами. Владельцу нужна строка, а не
    «что-то не сошлось»: по имени модели он идёт в лист, по числу «что-то» — никуда."""
    if report.get("handles_diverged"):
        first = report["handles_diverged"][0]
        return ("ручка %s разошлась: записано %s, лист даёт %s (всего разошлось ручек %d)"
                % (first["cell"], first["recorded"], first["live"],
                   len(report["handles_diverged"])))
    if report.get("handles_extra"):
        return "живой лист несёт ручки, которых в слепке нет: %s" % ", ".join(report["handles_extra"])
    if report.get("diverged"):
        first = report["diverged"][0]
        return ("база строки %s%s разошлась с листом: записано %d ฿/сут, лист даёт %d ฿/сут "
                "(%+.2f%%); всего разошлось строк %d из %d"
                % (first["model"], "/%s" % first["generation"] if first["generation"] else "",
                   first["recorded_thb_per_day"], first["live_thb_per_day"], first["delta_pct"],
                   len(report["diverged"]), report["products_checked"]))
    return "расхождений нет"


def revouch(source, fixture, on, fixture_ref=None):
    """Совпало ВСЁ → новый документ с новой датой слепка и записью о пересвидетельствовании.

    Дату приносит ВЫЗЫВАЮЩИЙ (`on`), а не часы этого модуля: иначе функция перестала бы быть
    чистой и тест не смог бы судить её без подкрутки времени процесса. Не совпало → RevouchError.
    """
    if not isinstance(on, str) or not on.strip():
        raise RevouchError("дата пересвидетельствования не названа")
    try:
        day = dt.date.fromisoformat(on.strip()).isoformat()
    except ValueError as exc:
        raise RevouchError("дата пересвидетельствования не разобрана: %r" % (on,)) from exc

    report = compare(source, fixture)
    if not report["matched"]:
        raise RevouchError("ОТКАЗ: %s. Дата слепка НЕ обновлена" % refusal_text(report))

    out = copy.deepcopy(source)
    block = out.get("freshness")
    if not isinstance(block, dict):
        raise RevouchError("в записанном правиле нет блока freshness")
    previous = block.get("snapshot_on")
    prior = block.get("revouch") if isinstance(block.get("revouch"), dict) else None
    try:
        count = int(prior.get("count")) + 1 if prior else 1
    except (TypeError, ValueError):
        count = 1

    block["snapshot_on"] = day
    block["revouch"] = {
        "on": day,
        "previous_snapshot_on": previous,
        "count": count,
        "verdict": VERDICT_MATCH,
        "products_checked": report["products_checked"],
        "products_agreed": report["products_agreed"],
        "products_diverged": len(report["diverged"]),
        "units_counted": report["units_counted"],
        "handles_agreed": sorted(report["handles_live"]),
        "window": copy.deepcopy(report["window"]),
        "fixture_snapshot_id": report["fixture_snapshot_id"],
        "fixture_generated_at": report["fixture_generated_at"],
        "fixture_ref": fixture_ref,
        "how": ("price_revouch.py: живой лист прочитан двумя дверями GET (fleet + quote_price), "
                "база пересчитана штатным price_snapshot_publish.build_candidate и сверена "
                "ПОСТРОЧНО с записанной — все %d строк(и) совпали до бата, разошлось 0."
                % report["products_checked"]),
        "what_did_not_move": ("базы, множители сезона и срока, депозиты, кепки, source, "
                              "base_method, owner_decision, n_bookings и p25_p75 не тронуты ни в "
                              "одной строке. Это не обещание: путевая сверка документа "
                              "(price_revouch.changed_paths) роняет заход ДО записи, если "
                              "разошлось хоть одно поле вне даты."),
        "why_date_may_move": ("дата слепка описывает МОМЕНТ, на который записанное правило сверено "
                              "с листом, а не день, когда числа придумали. Совпадение всей базы до "
                              "бата и есть новое наблюдение тех же чисел — двигается дата, число "
                              "не двигается. Пересборки правила НЕ было: built_on остался прежним."),
    }

    stray = stray_paths(source, out)
    if stray:
        raise RevouchError("пересвидетельствование тронуло поля вне даты: %s"
                           % ", ".join(stray[:8]))
    return out, report


# ═════════════════ РУКИ: ДИСК, ЧАСЫ, ЖИВОЙ ЛИСТ ══════════════════════════════════════════════

def _backup(source_path, stamp):
    """Резервная копия ДО записи. Образец — `brain_writer`: копия кладётся всегда и называется
    в выводе, потому что откат обязан быть выполним человеком без нас."""
    target = os.path.abspath(source_path)
    folder = os.path.join(os.path.dirname(target), "tmp", "price_revouch")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "price_source.%s.json" % stamp)
    with io.open(target, encoding="utf-8") as src:
        payload = src.read()
    with io.open(path, "w", encoding="utf-8", newline="") as out:
        out.write(payload)
    return path


def apply_to_disk(source_path, fixture, on, fixture_ref=None, stamp=None):
    """Сверка → (при совпадении) запись боевого файла с резервной копией и ОБРАТНЫМ ЧТЕНИЕМ.

    Отказ не пишет НИЧЕГО: ни резервной копии, ни файла — сверка стои́т ДО первого касания диска.
    """
    source = pub._load_json(source_path)
    out, report = revouch(source, fixture, on, fixture_ref=fixture_ref)
    backup = _backup(source_path, stamp or on.replace("-", ""))
    pub._write_json_atomic(source_path, out)

    # ОБРАТНОЕ ЧТЕНИЕ С ДОСЛОВНОЙ СВЕРКОЙ. Расписка о записи судьбы записи не описывает — судьбу
    # описывает то, что теперь лежит на диске. Разошлось — возвращаем копию и падаем.
    back = pub._load_json(source_path)
    stray = stray_paths(source, back)
    if back != out or stray:
        with io.open(backup, encoding="utf-8") as src:
            payload = src.read()
        with io.open(os.path.abspath(source_path), "w", encoding="utf-8", newline="") as dst:
            dst.write(payload)
        raise RevouchError("обратное чтение не сошлось (%s) — файл возвращён из %s"
                           % (", ".join(stray[:5]) if stray else "документ отличается", backup))
    return {"ok": True, "snapshot_on": out["freshness"]["snapshot_on"],
            "previous_snapshot_on": report["recorded_snapshot_on"],
            "backup": backup, "report": report,
            "changed_paths": changed_paths(source, back)}


def live_fixture(start, folder, effective_at=None):
    """Живой лист → фикстура ЭТОГО захода. Только чтение, две двери GET; своего клиента нет."""
    import price_snapshot_collect as collect

    pricing, inner = collect.live_pricing()
    window = collect.probe_window(start)
    source = pub._load_json(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "price_source.json"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "revouch_fixture.json")
    stamps = collect.stamps_now(effective_at=effective_at, evidence_ref=path)
    got = collect.collect(source, pricing, inner, window, stamps)
    if got["status"] != "ok":
        raise RevouchError("живой лист не прочитан: %s" % got.get("why"))
    pub._write_json_atomic(path, got["fixture"])
    return got["fixture"], path


def _print_report(report, matched_head, out=None):
    stream = out or sys.stdout
    print("СВЕРКА СЛЕПКА С ЖИВЫМ ЛИСТОМ", file=stream)
    print("  окно: %s..%s (%s сут, корзина %s)"
          % ((report["window"] or {}).get("date_start"), (report["window"] or {}).get("date_end"),
             (report["window"] or {}).get("days"), (report["window"] or {}).get("bucket")),
          file=stream)
    print("  дата слепка в файле: %s" % report["recorded_snapshot_on"], file=stream)
    print("  юнитов прочитано: %d, продуктов сверено: %d, совпало: %d, разошлось: %d"
          % (report["units_counted"], report["products_checked"], report["products_agreed"],
             len(report["diverged"])), file=stream)
    print("  ручки записанные %s / живые %s → %s"
          % (report["handles_recorded"], report["handles_live"],
             "СОШЛИСЬ" if not report["handles_diverged"] else "РАЗОШЛИСЬ"), file=stream)
    print(file=stream)
    for row in report["rows"]:
        print("  %-4s %-12s %-10s записано %5d  лист %5d  %s"
              % ("OK" if row["agree"] else "РАЗН", row["model"], row["generation"] or "—",
                 row["recorded_thb_per_day"], row["live_thb_per_day"],
                 "" if row["agree"] else "(%+.2f%%)" % row["delta_pct"]), file=stream)
    print(file=stream)
    print(matched_head, file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Пересвидетельствование слепка цен: дата двигается только за совпадением базы")
    parser.add_argument("--live", action="store_true", help="снять фикстуру живого листа сейчас")
    parser.add_argument("--start", help="ISO-дата начала окна (ровно 7 суток), нужна для --live")
    parser.add_argument("--fixture", help="готовая фикстура вместо живого чтения")
    parser.add_argument("--source", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "price_source.json"))
    parser.add_argument("--apply", action="store_true",
                        help="при совпадении обновить дату слепка в боевом файле")
    parser.add_argument("--on", help="дата пересвидетельствования (по умолчанию сегодня)")
    parser.add_argument("--work", default="_scratch_price_revouch", help="куда класть фикстуру --live")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")
    day = args.on or dt.date.today().isoformat()
    try:
        if bool(args.live) == bool(args.fixture):
            raise RevouchError("нужен ровно один источник фактов: --live или --fixture")
        if args.live:
            if not args.start:
                raise RevouchError("--live требует --start")
            fixture, ref = live_fixture(args.start, args.work)
        else:
            fixture, ref = pub._load_json(args.fixture), args.fixture

        if not args.apply:
            report = compare(pub._load_json(args.source), fixture)
            head = ("ИСХОД: СОВПАЛО ПОЛНОСТЬЮ — дату можно пересвидетельствовать (--apply)"
                    if report["matched"] else "ИСХОД: ОТКАЗ — %s" % refusal_text(report))
            _print_report(report, head)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
            return 0 if report["matched"] else 2

        done = apply_to_disk(args.source, fixture, day, fixture_ref=ref)
        _print_report(done["report"],
                      "ИСХОД: ДАТА ПЕРЕСВИДЕТЕЛЬСТВОВАНА %s → %s"
                      % (done["previous_snapshot_on"], done["snapshot_on"]))
        print("резервная копия: %s" % done["backup"])
        print("тронуто путей документа: %d (все в объявленной зоне даты)"
              % len(done["changed_paths"]))
        if args.json:
            print(json.dumps({k: v for k, v in done.items() if k != "report"},
                             ensure_ascii=False, indent=1, sort_keys=True))
        return 0
    except (OSError, ValueError, RevouchError, pub.CandidateError) as exc:
        print("ОТКАЗ: %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
