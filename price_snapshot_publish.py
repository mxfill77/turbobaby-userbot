# -*- coding: utf-8 -*-
"""B1.1: fixture-only dry-run/verify для кандидата price_source.json.

Модуль намеренно НЕ импортирует Bridge, pricing, suggest и не содержит publish-команды.
Он читает существующий snapshot и явную JSON-фикстуру, строит candidate в отдельном файле,
проверяет полноту/поколения/ручки/round-trip и считает canonical SHA-256. Runtime-файл
`price_source.json` не может быть output-целью этого CLI.
"""

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile


SOURCE_SCHEMA = "turbobaby/price_source"
FIXTURE_SCHEMA = "turbobaby/price_publish_fixture"
REQUIRED_HANDLES = ("H3", "I3", "J3")
# Поля ручки, которые СНИМАЕТ проба, а не описывают её словами. Унаследованное значение такого
# поля — не «старое наблюдение», а отсутствие наблюдения: угол/число согласных юнитов/поимённый
# список обязаны прийти из fixture этого захода. Всё прочее (например `category`) — описание
# ячейки листа, оно переживает заход и наследуется от прошлого снимка.
OBSERVED_HANDLE_FIELDS = ("global_discount", "units_agreed", "taken_from")
GENERATOR_VERSION = "b1.1-fixture-only"


class CandidateError(ValueError):
    """Candidate не доказан и не может двигаться дальше dry-run."""


def _load_json(path):
    with open(path, encoding="utf-8") as src:
        return json.load(src)


def _canonical_copy(doc):
    out = copy.deepcopy(doc)
    publication = out.get("publication")
    if isinstance(publication, dict):
        publication.pop("content_sha256", None)
    return out


def canonical_bytes(doc):
    """Стабильные байты без self-hash; одинаковый input обязан давать одинаковый hash."""
    return json.dumps(
        _canonical_copy(doc), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def content_sha256(doc):
    return hashlib.sha256(canonical_bytes(doc)).hexdigest()


def _iso_utc(value, field):
    if not isinstance(value, str) or not value.strip():
        raise CandidateError("%s отсутствует" % field)
    text = value.strip()
    try:
        dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateError("%s не является ISO datetime" % field) from exc
    return text


def _row_key(row):
    if not isinstance(row, dict) or not isinstance(row.get("model"), str) or not row["model"].strip():
        raise CandidateError("строка base.models без model")
    generation = row.get("generation")
    if generation is None:
        generation = ""
    if not isinstance(generation, str):
        raise CandidateError("generation модели %s не строка" % row["model"])
    return row["model"].strip(), generation.strip()


def _base_index(doc):
    if not isinstance(doc, dict) or doc.get("schema") != SOURCE_SCHEMA:
        raise CandidateError("source snapshot не схемы %s" % SOURCE_SCHEMA)
    base = doc.get("base")
    rows = base.get("models") if isinstance(base, dict) else None
    if not isinstance(rows, list) or not rows:
        raise CandidateError("source snapshot не содержит base.models")
    index = {}
    for row in rows:
        key = _row_key(row)
        if key in index:
            raise CandidateError("дублирующая строка base.models: %s/%s" % key)
        if row.get("judged") is True:
            value = row.get("base_thb_per_day")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise CandidateError("судимая модель %s/%s без положительной базы" % key)
            index[key] = row
    if not index:
        raise CandidateError("source snapshot не содержит судимых моделей")
    return index


def _handle_rows(block):
    """Блок ручек (fixture ИЛИ freshness кандидата) → {ячейка: наблюдённые поля}.

    Одна функция на оба входа сознательно: candidate проверяется ровно той меркой, которой
    принимался fixture, и «ручка без units_agreed/taken_from» не проезжает ни на одном входе.
    """
    rows = block.get("handles") if isinstance(block, dict) else None
    if not isinstance(rows, list):
        raise CandidateError("handles должен быть списком")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise CandidateError("handles содержит не объект")
        cell = row.get("cell")
        if not isinstance(cell, str) or cell not in REQUIRED_HANDLES:
            raise CandidateError("неизвестная ручка %r" % cell)
        if cell in result:
            raise CandidateError("ручка %s продублирована" % cell)
        absent = [name for name in OBSERVED_HANDLE_FIELDS if name not in row]
        if absent:
            raise CandidateError("ручка %s без наблюдённых полей: %s" % (cell, ", ".join(absent)))
        value = row["global_discount"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CandidateError("ручка %s не является числом" % cell)
        if value >= 1:
            raise CandidateError("ручка %s делает знаменатель неположительным" % cell)
        agreed = row["units_agreed"]
        if isinstance(agreed, bool) or not isinstance(agreed, int) or agreed < 1:
            raise CandidateError("ручка %s без положительного units_agreed" % cell)
        taken = row["taken_from"]
        if not isinstance(taken, list) or not taken or not all(
                isinstance(item, str) and item.strip() for item in taken):
            raise CandidateError("ручка %s без непустого taken_from" % cell)
        observed = {name: copy.deepcopy(row[name]) for name in OBSERVED_HANDLE_FIELDS}
        observed["global_discount"] = float(value)
        result[cell] = observed
    if set(result) != set(REQUIRED_HANDLES):
        missing = sorted(set(REQUIRED_HANDLES) - set(result))
        raise CandidateError("не сняты обязательные ручки: %s" % ", ".join(missing))
    return result


def _handle_map(block):
    """Только углы — то, чем считается нормализация базы."""
    return {cell: row["global_discount"] for cell, row in _handle_rows(block).items()}


def _source_window(fixture):
    """Окно наблюдения fixture. Пустое окно запрещено: иначе `freshness.window` и
    `publication.source_window` «сходятся» на None, и сверка становится пустой формальностью."""
    window = fixture.get("source_window") if isinstance(fixture, dict) else None
    if not isinstance(window, dict) or not window:
        raise CandidateError("fixture.source_window отсутствует")
    return copy.deepcopy(window)


def _product_key(product, source_index):
    if not isinstance(product, dict) or not isinstance(product.get("model"), str):
        raise CandidateError("fixture.products содержит продукт без model")
    model = product["model"].strip()
    generation = product.get("generation")
    if generation is not None:
        key = (model, str(generation).strip())
        if key not in source_index:
            raise CandidateError("неизвестный продукт %s/%s" % key)
        return key
    matches = [key for key in source_index if key[0] == model]
    if len(matches) > 1:
        raise CandidateError("неоднозначный продукт %s: укажите generation" % model)
    if not matches:
        raise CandidateError("неизвестный продукт %s" % model)
    return matches[0]


def _observed_price(product):
    units = product.get("units") if isinstance(product, dict) else None
    if not isinstance(units, list) or not units:
        raise CandidateError("продукт %s не содержит unit-проб" % product.get("model"))
    prices = []
    for unit in units:
        if not isinstance(unit, dict) or not isinstance(unit.get("name"), str) or not unit["name"].strip():
            raise CandidateError("unit-проба без имени у %s" % product.get("model"))
        value = unit.get("day_price")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise CandidateError("unit-проба без положительной day_price у %s" % product.get("model"))
        prices.append(float(value))
    distinct = sorted(set(prices))
    if len(distinct) != 1:
        raise CandidateError("юниты продукта %s дали разные цены: %s" % (product.get("model"), distinct))
    return int(round(distinct[0]))


def _publication_revision(source):
    old = source.get("publication") if isinstance(source, dict) else None
    value = old.get("revision") if isinstance(old, dict) else 0
    try:
        return int(value) + 1
    except (TypeError, ValueError):
        return 1


def build_candidate(source, fixture):
    """Чистая сборка candidate. Не читает диск/сеть и не пишет файлы."""
    if not isinstance(fixture, dict) or fixture.get("schema") != FIXTURE_SCHEMA:
        raise CandidateError("fixture не схемы %s" % FIXTURE_SCHEMA)
    generated_at = _iso_utc(fixture.get("generated_at"), "generated_at")
    effective_at = _iso_utc(fixture.get("effective_at"), "effective_at")
    generated_date = generated_at[:10]
    observed_handles = _handle_rows(fixture)
    handles = {cell: row["global_discount"] for cell, row in observed_handles.items()}
    source_window = _source_window(fixture)
    source_index = _base_index(source)
    products = fixture.get("products")
    if not isinstance(products, list) or not products:
        raise CandidateError("fixture.products пуст")

    updates = {}
    for product in products:
        key = _product_key(product, source_index)
        if key in updates:
            raise CandidateError("продукт %s/%s продублирован" % key)
        cell = product.get("handle_cell")
        if cell not in handles:
            raise CandidateError("продукт %s/%s ссылается на неснятую ручку %r" % (key[0], key[1], cell))
        cls = source_index[key].get("class")
        discounts = source.get("low_season_discount")
        low = discounts.get(cls) if isinstance(discounts, dict) else None
        if isinstance(low, bool) or not isinstance(low, (int, float)) or low >= 1:
            raise CandidateError("нет low_season_discount для класса %r" % cls)
        observed = _observed_price(product)
        live = handles[cell]
        base = int(round(observed * (1.0 - float(low)) / (1.0 - live)))
        if base <= 0:
            raise CandidateError("нормализованная база неположительна для %s/%s" % key)
        round_trip = int(round(base * (1.0 - live) / (1.0 - float(low))))
        if round_trip != observed:
            raise CandidateError(
                "round-trip не сошёлся до бата для %s/%s: %s != %s" % (key[0], key[1], round_trip, observed)
            )
        updates[key] = {
            "base": base,
            "observed": observed,
            "handle_cell": cell,
            "live_global_discount": live,
            "low_season_discount": float(low),
            "units": len(product["units"]),
        }

    if set(updates) != set(source_index):
        missing = sorted(set(source_index) - set(updates))
        extra = sorted(set(updates) - set(source_index))
        raise CandidateError("неполный candidate: missing=%s extra=%s" % (missing, extra))

    candidate = copy.deepcopy(source)
    for row in candidate["base"]["models"]:
        key = _row_key(row)
        if key not in updates:
            continue
        item = updates[key]
        row["base_thb_per_day"] = item["base"]
        row["judged"] = True
        row["base_method"] = "GOOGLE SHEET — owner-approved published snapshot"
        row["source"] = "B1 price snapshot candidate from explicit fixture"
        row["measured_on"] = generated_date
        row["publication_snapshot_id"] = fixture.get("snapshot_id") or generated_at

    previous_freshness = candidate.get("freshness")
    previous_by_cell = {}
    if isinstance(previous_freshness, dict):
        for row in previous_freshness.get("handles") or []:
            if isinstance(row, dict) and row.get("cell") in REQUIRED_HANDLES:
                previous_by_cell[row["cell"]] = row
    handle_rows = []
    for cell in REQUIRED_HANDLES:
        row = copy.deepcopy(previous_by_cell.get(cell, {}))
        # Сначала СНИМАЕМ унаследованное наблюдение и только потом кладём своё: иначе поле,
        # которого нет в этом заходе, молча доедет из прошлого снимка как будто оно снято.
        for name in OBSERVED_HANDLE_FIELDS:
            row.pop(name, None)
        row["cell"] = cell
        row.update(copy.deepcopy(observed_handles[cell]))
        handle_rows.append(row)
    freshness = copy.deepcopy(previous_freshness) if isinstance(previous_freshness, dict) else {}
    freshness.update(snapshot_on=generated_date, built_on=generated_date,
                     window=copy.deepcopy(source_window),
                     handles=copy.deepcopy(handle_rows))
    candidate["freshness"] = freshness
    candidate["built_on"] = generated_date
    candidate["status"] = {
        "verdict": "CANDIDATE — NOT PUBLISHED",
        "note": "B1.1 fixture-only dry-run; runtime source is unchanged until a separate owner-approved publish.",
    }
    snapshot_id = fixture.get("snapshot_id") or generated_at
    candidate["publication"] = {
        "status": "candidate",
        "revision": _publication_revision(source),
        "snapshot_id": snapshot_id,
        "generated_at": generated_at,
        "effective_at": effective_at,
        "publisher": "pending-owner-approval",
        "approval_ref": fixture.get("approval_ref"),
        "source": "explicit-fixture-only",
        "source_window": copy.deepcopy(source_window),
        "handles": copy.deepcopy(handle_rows),
        "generator_version": fixture.get("generator_version") or GENERATOR_VERSION,
        "previous_content_sha256": content_sha256(source),
        "evidence_ref": fixture.get("evidence_ref"),
    }
    candidate["publication"]["content_sha256"] = content_sha256(candidate)
    verify_candidate(candidate)
    return candidate


def verify_candidate(candidate):
    """Структурная и hash-проверка candidate без сети и без исходной fixture."""
    _base_index(candidate)
    freshness = candidate.get("freshness")
    if not isinstance(freshness, dict):
        raise CandidateError("candidate не содержит freshness")
    _handle_rows(freshness)
    if not isinstance(freshness.get("snapshot_on"), str) or freshness.get("snapshot_on") != candidate.get("built_on"):
        raise CandidateError("freshness.snapshot_on и built_on должны обновляться вместе")
    window = freshness.get("window")
    if not isinstance(window, dict) or not window:
        raise CandidateError("freshness.window отсутствует")
    publication = candidate.get("publication")
    required = (
        "status", "revision", "snapshot_id", "generated_at", "effective_at", "publisher",
        "source", "source_window", "handles", "generator_version", "previous_content_sha256",
        "content_sha256", "evidence_ref",
    )
    if not isinstance(publication, dict):
        raise CandidateError("candidate не содержит publication")
    missing = [name for name in required if name not in publication]
    if missing:
        raise CandidateError("publication не содержит: %s" % ", ".join(missing))
    # Кандидат, у которого метаданные спорят с собственной уликой, не «почти готов»: он врёт
    # про то, ЧТО именно наблюдали. Обе сверки стоя́т ДО hash — hash подтверждает целостность
    # уже согласованного документа, а не заменяет согласованность.
    if window != publication.get("source_window"):
        raise CandidateError("freshness.window разошлась с publication.source_window")
    if publication.get("handles") != freshness.get("handles"):
        raise CandidateError("publication.handles разошлись с freshness.handles")
    expected = content_sha256(candidate)
    if publication.get("content_sha256") != expected:
        raise CandidateError("content_sha256 не совпадает с canonical candidate")
    return {
        "ok": True,
        "snapshot_id": publication["snapshot_id"],
        "content_sha256": expected,
        "models": len(candidate["base"]["models"]),
    }


def build_report(source, candidate):
    old = _base_index(source)
    new = _base_index(candidate)
    changes = []
    for key in sorted(new):
        before = int(round(old[key]["base_thb_per_day"]))
        after = int(round(new[key]["base_thb_per_day"]))
        delta = round((after - before) * 100.0 / before, 2)
        changes.append({
            "model": key[0], "generation": key[1] or None,
            "old_base_thb_per_day": before, "new_base_thb_per_day": after,
            "delta_pct": delta,
        })
    return {
        "status": "dry-run",
        "source_unchanged": True,
        "candidate_sha256": candidate["publication"]["content_sha256"],
        "snapshot_id": candidate["publication"]["snapshot_id"],
        "changes": changes,
    }


def _write_json_atomic(path, value):
    target = os.path.abspath(path)
    parent = os.path.dirname(target) or os.getcwd()
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".b1-candidate-", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, sort_keys=False)
            out.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _assert_safe_output(source_path, output_path):
    source = os.path.abspath(source_path)
    output = os.path.abspath(output_path)
    if os.path.normcase(source) == os.path.normcase(output):
        raise CandidateError("dry-run output не может заменить source snapshot")
    if os.path.basename(output).lower() == "price_source.json":
        raise CandidateError("B1.1 запрещает output с именем price_source.json")


def _parser():
    parser = argparse.ArgumentParser(description="B1.1 fixture-only price snapshot dry-run/verify")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--verify", metavar="CANDIDATE")
    parser.add_argument("--source", default=os.path.join(os.path.dirname(__file__), "price_source.json"))
    parser.add_argument("--fixture")
    parser.add_argument("--output")
    parser.add_argument("--report")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.verify:
            result = verify_candidate(_load_json(args.verify))
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if not args.fixture or not args.output or not args.report:
            raise CandidateError("--dry-run требует --fixture, --output и --report")
        _assert_safe_output(args.source, args.output)
        source = _load_json(args.source)
        fixture = _load_json(args.fixture)
        candidate = build_candidate(source, fixture)
        report = build_report(source, candidate)
        _write_json_atomic(args.output, candidate)
        _write_json_atomic(args.report, report)
        print(json.dumps({"ok": True, "mode": "dry-run", "candidate": args.output,
                          "report": args.report, "content_sha256": report["candidate_sha256"]},
                         ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, CandidateError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

