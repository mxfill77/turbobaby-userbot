# -*- coding: utf-8 -*-
"""B1.2: ТОНКИЙ АДАПТЕР «живой парк + живые котировки → явная фикстура B1.1». ТОЛЬКО ЧТЕНИЕ.

ЗАЧЕМ. У B1.1 (`price_snapshot_publish`) вход — ЯВНАЯ фикстура, и до сих пор её набирали руками.
Этот модуль набирает её из фактов живого листа ровно двумя дверями моста, которые в контуре уже
есть: `fleet` (парк) и `quote_price` (котировка). Больше в нём нет ничего: ни своего HTTP-клиента,
ни своего адреса, ни очереди, ни демона, ни расписания, ни формулы цены, ни реестра моделей, ни
чтения секретов. Транспорт — существующая дорога `pricing.fleet_status` / `pricing.quote`;
сборка кандидата и его проверка — чистые `price_snapshot_publish.build_candidate` /
`verify_candidate`, здесь они не повторяются ни строкой.

ЧТО ЭТОТ МОДУЛЬ НЕ УМЕЕТ ПО УСТРОЙСТВУ. Публиковать (ветки publish нет вовсе), писать в лист, в
CRM, в очередь, в мозг и в Telegram; менять `price_source.json` (он ЧИТАЕТСЯ, а целью вывода быть
не может — замок `price_snapshot_publish._assert_safe_output`); звать что-либо, кроме двух
объявленных действий: КАЖДЫЙ запрос проходит через `guard_actions`, и незнакомое действие — это
исключение, а не предупреждение.

ТРИ ВЕЩИ, КОТОРЫЕ ЗДЕСЬ FAIL-CLOSED, И КАЖДАЯ СТОИТ ДЕНЕГ, ЕСЛИ СДЕЛАТЬ ИНАЧЕ:

1. ПОКОЛЕНИЕ НЕ УГАДЫВАЕТСЯ. `XMAX 300 / 2020-2022` и `XMAX 300 / 2023-` — РАЗНЫЕ продукты листа
   (557 и 662 ฿/сут). Разводит их не догадка, а уже записанные поля `sheet_model` / `unit_marker`
   плюс имя живого юнита — тем же кодом, что и боевой путь ответа (`price_source.resolve_row` →
   `_pick_generation`). Юнит, чьё поколение не опознано, в продукт НЕ попадает; продукт, у
   которого от этого не осталось ни одного юнита, роняет весь кандидат.
2. ЮНИТЫ ОДНОГО ПРОДУКТА НЕ УСРЕДНЯЮТСЯ. Два юнита назвали разные цены суток — это НЕ повод взять
   первую или среднюю: строка листа одна, значит разошёлся не лист, а наше сопоставление. Кандидат
   падает (проверку держит `_observed_price` из B1.1, здесь она не дублируется).
3. ОКНО — РОВНО СЕМЬ СУТОК и названо в самой фикстуре. Семь — не круглое число: это опорная
   корзина `7-13` файла, чей множитель ровно 1.000 по построению, и ровно ею сняты живые базы
   XMAX (26.08.2026), CLICK 125 и FORZA 300 (30.08.2026). Дверь обязана подтвердить срок полем
   `days`; не подтвердила — юнит не засчитан.

НАЛИЧИЕ, ДЕПОЗИТ И КЕПКА — УЛИКИ, А НЕ ЦЕНА. Они снимаются и кладутся в отчёт, но в базу низкого
сезона не попадают ни одним битом: занятость юнита к тарифу строки листа отношения не имеет, а
кепка — отдельный потолок ПОВЕРХ формулы (см. `price_source.json` → `low_season_caps`).

СЕКРЕТЫ. Их берёт САМ импортируемый демон (`load_dotenv` на импорте `pc_orchestrator`), как это
уже делают `queue_snapshot_pc` и `price_freshness_run`. Здесь их не читают и не видят: в этом
файле нет ни одного обращения к окружению (запрет класса 328).

ЗАПУСК (из корня репозитория):
    venv\\Scripts\\python.exe price_snapshot_collect.py --live --start 2026-09-07 \\
        --fixture _scratch_b1_2/live_fixture.json \\
        --candidate _scratch_b1_2/live_candidate.json \\
        --report _scratch_b1_2/live_report.json
"""

import argparse
import datetime as dt
import json
import os
import sys

import price_snapshot_publish as pub
import price_source

# ── ДВЕ ДВЕРИ, И БОЛЬШЕ НИ ОДНОЙ. Список — предмет теста, а не пожелание. ──────────────────────
ALLOWED_ACTIONS = ("fleet", "quote_price")

# Окно опорной корзины 7-13: ровно семь суток (обоснование — в докстроке модуля).
WINDOW_DAYS = 7

GENERATOR_VERSION = "b1.2-live-read"

# ЯЧЕЙКА РУЧКИ ПО МОДЕЛИ. Не выдумка и не «класс из файла»: у листа ТРИ ручки скидки, а классов в
# правиле два — «мото» разложено листом на «мото-1» (H3) и «мото-2» (I3), и по классу их не
# развести. Перечни категорий записаны в этом репозитории ЗАРАНЕЕ — `price_freshness_run.PROBES`
# и комментарий над ними (разбор docs/artifacts/2026-08-15-manager-price-sheet.md §2.2):
# H3 — XSR155/CB300R/REBEL/MT-03/NINJA400; I3 — VULCAN/CBR650/CB650;
# J3 — NMAX/XMAX/ADV/CLICK/FORZA/XADV.
# Ошибиться этой картой молча нельзя: живая котировка КАЖДОГО юнита несёт СВОЮ `global_discount`,
# и продукты, попавшие в одну ячейку с разными углами, роняют кандидат (`_handles_from`).
HANDLE_CELL_BY_MODEL = {
    "XSR 155": "H3", "CB 300": "H3", "MT-03 300": "H3", "NINJA 400": "H3",
    "VULCAN 650": "I3", "CBR 650R": "I3", "CB 650R": "I3",
    "NMAX 155": "J3", "XMAX 300": "J3", "ADV 350": "J3",
    "CLICK 125": "J3", "FORZA 300": "J3", "XADV 750": "J3",
}


class CollectError(ValueError):
    """Факты не доказаны — кандидат не собирается. Не «почти собрался», а не собрался."""


# ═══════════════════ ЧИСТЫЙ СЛОЙ: ФАКТЫ → ФИКСТУРА. НИ ДИСКА, НИ СЕТИ ════════════════════════

def probe_window(start, days=WINDOW_DAYS):
    """ISO-дата начала → окно пробы. Срок объявлен ЧИСЛОМ и уезжает в фикстуру как есть."""
    try:
        first = dt.date.fromisoformat(str(start))
    except (TypeError, ValueError) as exc:
        raise CollectError("дата начала окна не разобрана: %r" % (start,)) from exc
    if int(days) != WINDOW_DAYS:
        raise CollectError("окно обязано быть ровно %d суток, названо %r" % (WINDOW_DAYS, days))
    last = first + dt.timedelta(days=WINDOW_DAYS)
    return {"date_start": first.isoformat(), "date_end": last.isoformat(), "days": WINDOW_DAYS,
            "bucket": "7-13",
            "why": "опорная корзина 7-13 файла: её множитель ровно 1.000 по построению, значит "
                   "цена суток строки листа читается без досчёта ступени срока"}


def guard_actions(inner, seen):
    """Обёртка транспорта: пропускает РОВНО `ALLOWED_ACTIONS` и ведёт список того, что ушло.

    Незнакомое действие — исключение, а не запись в лог: доказательством «ходили только двумя
    дверями» служит эта ветка, и молчаливый пропуск отнял бы у неё весь смысл.
    """
    def get(params):
        action = (params or {}).get("action") if isinstance(params, dict) else None
        if action not in ALLOWED_ACTIONS:
            raise CollectError("адаптер не вправе звать действие %r (разрешены только %s)"
                               % (action, ", ".join(ALLOWED_ACTIONS)))
        seen.append(str(action))
        return inner(params)
    return get


def actions_report(seen):
    """Список ушедших действий → счёт по именам. Форма доклада, а не решение."""
    counts = {}
    for name in seen:
        counts[name] = counts.get(name, 0) + 1
    return [{"method": "GET", "action": name, "calls": counts[name]} for name in sorted(counts)]


def handle_cell(model):
    """Модель → ячейка ручки листа. Неизвестная модель — ОСТАНОВКА, а не «пусть будет скутер»."""
    cell = HANDLE_CELL_BY_MODEL.get(str(model).strip())
    if cell is None:
        raise CollectError("для модели %r не записана категория ручки листа — не угадываем"
                           % (model,))
    return cell


def unit_facts(name, quote, window):
    """Нормализованная котировка ОДНОГО юнита → факты | (None, почему юнит не засчитан).

    Возвращает пару, а не бросает: отказ ОДНОГО юнита сам по себе кандидат не роняет — роняет его
    продукт, у которого не осталось ни одного засчитанного юнита. Разница не косметическая: без
    неё занятый или молчащий юнит убивал бы весь слепок.
    """
    if not isinstance(quote, dict):
        return None, "дверь не ответила котировкой"
    day = quote.get("day_price")
    if isinstance(day, bool) or not isinstance(day, (int, float)) or day <= 0:
        return None, "в ответе нет положительной day_price (%r)" % (day,)
    days = quote.get("days")
    try:
        got = int(days)
    except (TypeError, ValueError):
        return None, "дверь не назвала срок (days=%r) — окно не подтверждено" % (days,)
    if got != window["days"]:
        return None, "дверь посчитала %d сут вместо %d — окно не подтверждено" % (got, window["days"])
    season = quote.get("season")
    angle = season.get("global_discount") if isinstance(season, dict) else None
    if isinstance(angle, bool) or not isinstance(angle, (int, float)):
        return None, "в ответе нет season.global_discount — угол ручки не снят"
    return {
        "name": name,
        "day_price": int(round(float(day))),
        "global_discount": float(angle),
        "days": got,
        # УЛИКИ, А НЕ ЦЕНА: ниже по дороге они не участвуют в счёте базы ни одним битом.
        "sheet_model": quote.get("model"),
        "available": quote.get("available"),
        "deposit": quote.get("deposit"),
        "cap_price": quote.get("cap_price"),
        "cap_active": quote.get("cap_active"),
        "total": quote.get("total"),
    }, None


def _one_discount(key, units):
    """Единственный угол ручки у юнитов продукта. Разошлись — остановка, а не выбор большинства."""
    distinct = sorted({u["global_discount"] for u in units})
    if len(distinct) != 1:
        raise CollectError("юниты продукта %s/%s дали разные углы ручки: %s"
                           % (key[0], key[1], distinct))
    return distinct[0]


def _handles_from(products):
    """Продукты → три ручки листа. Одна ячейка с разными углами — остановка (карта категорий
    разошлась с листом), неполный набор ячеек — тоже: у B1.1 обязательны все три."""
    by_cell = {}
    for item in products:
        by_cell.setdefault(item["handle_cell"], {}).setdefault(item["global_discount"], []).append(
            "%s/%s" % (item["model"], item.get("generation") or ""))
    rows = []
    for cell in pub.REQUIRED_HANDLES:
        seen = by_cell.get(cell)
        if not seen:
            raise CollectError("ручка %s не снята ни одним продуктом — набор ячеек неполон" % cell)
        if len(seen) != 1:
            raise CollectError("ручка %s снята разными углами: %s"
                               % (cell, {k: v for k, v in sorted(seen.items())}))
        value = list(seen)[0]
        rows.append({"cell": cell, "global_discount": value,
                     "units_agreed": sum(len(v) for v in seen.values()),
                     "taken_from": sorted(seen[value])})
    return rows


def assemble(source, window, units_by_key, stamps, evidence=None):
    """Факты → ФИКСТУРА схемы B1.1. Чистая функция: ни диска, ни сети, ни часов.

    Полный набор продуктов — это ключи `base.models` судимых строк (их же считает B1.1), поэтому
    «продукт пропал» здесь ловится ДО сборки кандидата и называется поимённо.
    """
    index = pub._base_index(source)
    products, missing = [], []
    for key in sorted(index):
        units = units_by_key.get(key) or []
        if not units:
            missing.append("%s/%s" % (key[0], key[1] or "—"))
            continue
        products.append({
            "model": key[0],
            "generation": key[1] or None,
            "handle_cell": handle_cell(key[0]),
            "global_discount": _one_discount(key, units),
            "sheet_model": index[key].get("sheet_model") or units[0].get("sheet_model"),
            "class": index[key].get("class"),
            "units": [{"name": u["name"], "day_price": u["day_price"]} for u in units],
            "evidence": [{k: u[k] for k in
                          ("name", "available", "deposit", "cap_price", "cap_active", "total",
                           "sheet_model", "days", "global_discount")} for u in units],
        })
    if missing:
        raise CollectError("живой парк не дал ни одного юнита продуктам: %s" % ", ".join(missing))
    fixture = {
        "schema": pub.FIXTURE_SCHEMA,
        "snapshot_id": stamps["snapshot_id"],
        "generated_at": stamps["generated_at"],
        "effective_at": stamps["effective_at"],
        "generator_version": GENERATOR_VERSION,
        "source_window": dict(window),
        "source": "живой лист через мост: GET fleet + GET quote_price, только чтение",
        "approval_ref": None,
        "evidence_ref": stamps.get("evidence_ref"),
        "handles": _handles_from(products),
        "products": products,
    }
    if evidence is not None:
        fixture["evidence"] = evidence
    return fixture


# ═══════════════════ РУКИ: ДВЕ ДВЕРИ МОСТА, ДИСК, ЧАСЫ ══════════════════════════════════════

def live_pricing():
    """Модуль `pricing`, поднятый ПОСЛЕ демона, — и потому с настроенным транспортом.

    Порядок импорта здесь — не аккуратность, а условие работы: константы `pricing` читаются на
    ИМПОРТЕ, а в окружение их кладёт сам демон (`load_dotenv` на импорте `pc_orchestrator`).
    Поднять `pricing` раньше значит получить пустой адрес и молчаливый фолбэк вместо котировки.
    Флаг тестовых логов на время импорта — закрытый класс «процесс сорит в чужой боевой лог».
    """
    import queue_snapshot_pc as qs

    def boot():
        import pc_orchestrator                              # noqa: F401 — секреты берёт САМ демон
        import pricing
        return pricing

    module = qs._guard_test_logs(boot)
    if module.PRICING_ACTION != "quote_price":
        raise CollectError("дверь котировки названа не «quote_price» (%r) — доказать, что ходим "
                           "только двумя разрешёнными действиями, нечем" % (module.PRICING_ACTION,))
    if not (module.BRIDGE_URL and module.BRIDGE_TOKEN):
        raise CollectError("мост не настроен в этом процессе — живого чтения не будет")
    return module, module._default_get


def collect(source, pricing, inner_get, window, stamps):
    """Живой парк и живые котировки → (фикстура, улики). Единственное место, трогающее мост.

    Возвращает `{"status": "unknown"}` РОВНО тогда, когда парк не получен: молчание моста не есть
    «парк пуст», и кандидата из него не бывает ни на одной дороге.
    """
    seen = []
    get = guard_actions(inner_get, seen)
    # Парк берём СВЕЖИМ: кэш `pricing` живёт 5 минут и годится боевому ответу клиенту, но слепок
    # цены обязан стоять на том парке, который мы видели сами.
    pricing._FLEET_CACHE["data"], pricing._FLEET_CACHE["ts"] = None, 0.0
    bikes, ok = pricing.fleet_status(_get=get)
    if not ok:
        return {"status": "unknown", "fixture": None,
                "why": "парк НЕ получен (мост молчит или ответил не-ok) — кандидата не строим",
                "actions": actions_report(seen), "evidence": {"fleet_units": 0}}

    index = pub._base_index(source)
    wanted, order = set(), []
    for key in sorted(index):
        for bike in pricing._candidates(key[0], bikes):
            name = (bike or {}).get("name")
            if isinstance(name, str) and name.strip() and name not in wanted:
                wanted.add(name)
                order.append(name)

    units_by_key, skipped = {}, []
    for name in order:
        quote = pricing.quote(name, window["date_start"], window["date_end"], _get=get)
        facts, why = unit_facts(name, quote, window)
        if facts is None:
            skipped.append({"unit": name, "why": why})
            continue
        row, how = price_source.resolve_row(source, None, pricing._norm_nocc, quote=quote)
        if row is None:
            skipped.append({"unit": name, "why": "продукт не опознан: %s" % how})
            continue
        key = pub._row_key(row)
        if key not in index:
            skipped.append({"unit": name, "why": "строка %s/%s не судима файлом" % key})
            continue
        facts["resolved_by"] = how
        units_by_key.setdefault(key, []).append(facts)

    evidence = {
        "fleet_units": len(bikes),
        "candidates_quoted": len(order),
        "units_counted": sum(len(v) for v in units_by_key.values()),
        "units_skipped": skipped,
        "bridge_actions": actions_report(seen),
        "window": dict(window),
    }
    fixture = assemble(source, window, units_by_key, stamps, evidence=evidence)
    return {"status": "ok", "fixture": fixture, "why": "", "actions": actions_report(seen),
            "evidence": evidence}


def stamps_now(now=None, effective_at=None, evidence_ref=None):
    """Штампы фикстуры. Часы трогаются ровно здесь, чтобы сборка осталась чистой."""
    at = now if isinstance(now, dt.datetime) else dt.datetime.now(dt.timezone.utc)
    at = at.replace(microsecond=0)
    return {"generated_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "effective_at": effective_at or (at.date() + dt.timedelta(days=1)).strftime(
                "%Y-%m-%dT00:00:00Z"),
            "snapshot_id": "price-%s" % at.strftime("%Y%m%dT%H%M%SZ"),
            "evidence_ref": evidence_ref}


def run(source_path, pricing, inner_get, start, out_fixture, out_candidate, out_report,
        now=None, effective_at=None):
    """Полный сухой заход: живое чтение → фикстура → кандидат B1.1 → отчёт. НИЧЕГО НЕ ПУБЛИКУЕТ.

    Замок цели вывода взят у B1.1 и стои́т ДО первого запроса к мосту: ходить в сеть ради записи,
    которую всё равно запретят, незачем.
    """
    for target in (out_fixture, out_candidate, out_report):
        pub._assert_safe_output(source_path, target)
    window = probe_window(start)
    source = pub._load_json(source_path)
    stamps = stamps_now(now=now, effective_at=effective_at, evidence_ref=out_fixture)
    got = collect(source, pricing, inner_get, window, stamps)
    if got["status"] != "ok":
        return got
    candidate = pub.build_candidate(source, got["fixture"])
    verified = pub.verify_candidate(candidate)
    report = pub.build_report(source, candidate)
    report.update({
        "status": "dry-run (live read-only)",
        "published": False,
        "source_path": os.path.abspath(source_path),
        "window": dict(window),
        "handles": got["fixture"]["handles"],
        "bridge_actions": got["actions"],
        "evidence": got["evidence"],
        "note": "кандидат НЕ опубликован: runtime price_source.json только читался",
    })
    pub._write_json_atomic(out_fixture, got["fixture"])
    pub._write_json_atomic(out_candidate, candidate)
    pub._write_json_atomic(out_report, report)
    got.update({"candidate": candidate, "report": report, "verified": verified,
                "paths": {"fixture": out_fixture, "candidate": out_candidate,
                          "report": out_report}})
    return got


def _parser():
    parser = argparse.ArgumentParser(
        description="B1.2 live read-only price candidate (GET fleet + GET quote_price)")
    parser.add_argument("--live", action="store_true", required=True,
                        help="живое чтение моста; другого источника у этого CLI нет")
    parser.add_argument("--start", required=True, help="ISO-дата начала окна (ровно 7 суток)")
    parser.add_argument("--source", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                         "price_source.json"))
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--effective-at")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        pricing, inner = live_pricing()
        got = run(args.source, pricing, inner, args.start, args.fixture, args.candidate,
                  args.report, effective_at=args.effective_at)
    except (OSError, ValueError, CollectError, pub.CandidateError) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc),
                          "type": type(exc).__name__}, ensure_ascii=False, sort_keys=True),
              file=sys.stderr)
        return 2
    if got["status"] != "ok":
        print(json.dumps({"ok": False, "status": got["status"], "why": got["why"],
                          "bridge_actions": got["actions"]}, ensure_ascii=False, sort_keys=True),
              file=sys.stderr)
        return 3
    print(json.dumps({
        "ok": True, "status": "dry-run", "published": False,
        "candidate_sha256": got["candidate"]["publication"]["content_sha256"],
        "snapshot_id": got["candidate"]["publication"]["snapshot_id"],
        "bridge_actions": got["actions"],
        "products": len(got["fixture"]["products"]),
        "handles": {r["cell"]: r["global_discount"] for r in got["fixture"]["handles"]},
        "paths": got["paths"],
        "changes": got["report"]["changes"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
