# -*- coding: utf-8 -*-
"""Юниты B1.2: адаптер «живой парк + котировки → фикстура B1.1» на ИНЪЕКТИРОВАННОЙ двери.

Сети здесь нет ни одного байта: транспорт подсовывается через `_get` существующей дороги
`pricing.fleet_status` / `pricing.quote`, и КАЖДЫЙ запрос виден тесту целиком — вместе с полем
`action`. Именно этим доказывается главное утверждение задачи: наружу уходят ровно два действия,
`fleet` и `quote_price`, оба GET.

МОК КОПИРУЕТ ЖИВОЙ ФОРМАТ (правило-класс CLAUDE.md), а не удобную тесту схему:
  • имена юнитов взяты в той форме, в какой их печатает Лист1 и в какой они уже записаны в
    `price_freshness_run.PROBES` — с рабочим объёмом внутри имени и кириллическим «СС»
    («MT-03 300СС BLUE PHUKET 5068»), потому что ровно на этой форме спотыкается наивный матчер;
  • имя строки листа приходит полем `model` теми же словами, что записаны в `sheet_model`
    («YAMAHA XMAX 300 NEW 2023-»), — это ответ самого листа на вопрос «какая строка»;
  • угол ручки приходит вложенным `season.global_discount`, как его читает боевой сторож
    свежести, а не отдельным полем верхнего уровня.

Голдены не выдуманы: цены мока берутся ИЗ БОЕВОГО `price_source.json`, а углы ручек равны
скидкам низкого сезона того же файла. При таком листе нормализация обязана вернуть ровно
записанные базы — до бата, без единого изменения. Разъедется файл или формула — покраснеет это.
"""
import ast
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest

import price_snapshot_collect as collect
import price_snapshot_publish as pub
import price_freshness_run
import pricing

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_SOURCE = os.path.join(HERE, "price_source.json")

WINDOW_START = "2026-09-07"

# Живой парк мока: (имя юнита, модель файла, поколение). Имена — в форме Лист1.
FLEET_UNITS = (
    ("XSR 155СС GREEN", "XSR 155", ""),
    ("XSR 155CC BLACK PHUKET 1180", "XSR 155", ""),
    ("CB 300CC R 9011", "CB 300", ""),
    ("MT-03 300СС BLUE PHUKET 5068", "MT-03 300", ""),
    ("NINJA 400СС PHUKET 6334", "NINJA 400", ""),
    ("VULCAN 650CC S PHUKET 5065", "VULCAN 650", ""),
    ("CBR 650R PHUKET 4505", "CBR 650R", ""),
    ("CB 650R BLACK PHUKET 3503", "CB 650R", ""),
    ("NMAX 155CC GREY PHUKET 5960", "NMAX 155", ""),
    ("NMAX 155CC RED PHUKET 6001", "NMAX 155", ""),
    ("XMAX 300CC BLUE PHUKET 5773", "XMAX 300", "2020-2022"),
    ("XMAX 300CC NEW BLACK PHUKET 8969", "XMAX 300", "2023-"),
    ("ADV 350CC GREY BKK 798", "ADV 350", ""),
    ("CLICK 125CC WHITE PHUKET 1101", "CLICK 125", ""),
    ("FORZA 300CC BLACK PHUKET 2202", "FORZA 300", ""),
    ("XADV 750CC GREY PHUKET 3303", "XADV 750", ""),
)

# Имя строки листа — как его отдаёт живая дверь полем `model` (слова из `sheet_model` файла).
SHEET_MODEL = {
    ("XSR 155", ""): "YAMAHA XSR 155",
    ("CB 300", ""): "HONDA CB 300R",
    ("MT-03 300", ""): "YAMAHA MT-03 300",
    ("NINJA 400", ""): "KAWASAKI NINJA 400",
    ("VULCAN 650", ""): "KAWA VULCAN 650S",
    ("CBR 650R", ""): "HONDA CBR 650R",
    ("CB 650R", ""): "HONDA CB 650R",
    ("NMAX 155", ""): "YAMAHA NMAX 155",
    ("XMAX 300", "2020-2022"): "YAMAHA XMAX300 2020-2022",
    ("XMAX 300", "2023-"): "YAMAHA XMAX 300 NEW 2023-",
    ("ADV 350", ""): "HONDA ADV 350",
    ("CLICK 125", ""): "HONDA CLICK 125",
    ("FORZA 300", ""): "HONDA FORZA 300",
    ("XADV 750", ""): "HONDA XADV 750",
}


def read_runtime_source():
    with io.open(RUNTIME_SOURCE, encoding="utf-8") as src:
        return json.load(src)


class FakeBridge(object):
    """Инъектируемая дверь моста. Помнит КАЖДЫЙ запрос целиком — включая `action`."""

    def __init__(self, source, units=FLEET_UNITS, fleet_ok=True, name_only=False,
                 overrides=None, handles=None):
        self.source = source
        self.units = list(units)
        self.fleet_ok = fleet_ok
        self.name_only = name_only            # дверь не назвала строку листа (поле `model` пусто)
        self.overrides = dict(overrides or {})
        self.handles = dict(handles or {})
        self.calls = []
        self.base = {}
        self.cls = {}
        for row in source["base"]["models"]:
            key = (row["model"], (row.get("generation") or "").strip())
            self.base[key] = int(row["base_thb_per_day"])
            self.cls[key] = row["class"]

    def key_of(self, name):
        for unit, model, generation in self.units:
            if unit == name:
                return (model, generation)
        return None

    def discount(self, key):
        if key in self.handles:
            return self.handles[key]
        return float(self.source["low_season_discount"][self.cls[key]])

    def __call__(self, params):
        self.calls.append(dict(params))
        action = params.get("action")
        if action == "fleet":
            if not self.fleet_ok:
                return {"ok": False, "error": "мост занят"}
            return {"ok": True, "bikes": [{"name": n} for n, _m, _g in self.units]}
        if action == "quote_price":
            name = params.get("bike")
            key = self.key_of(name)
            if key is None:
                return {"ok": False, "error": "нет такого байка"}
            answer = {
                "ok": True,
                "day_price": self.overrides.get(name, self.base[key]),
                "total": self.overrides.get(name, self.base[key]) * collect.WINDOW_DAYS,
                "days": collect.WINDOW_DAYS,
                "deposit": 5000,
                "available": True,
                "season": {"global_discount": self.discount(key), "name": "низкий"},
                "model": None if self.name_only else SHEET_MODEL[key],
                "bike": name,
                "cap_active": True,
                "cap_price": 8900,
                "text": "строка столбца J",
            }
            return answer
        raise AssertionError("мок позвали действием %r" % (action,))


class CollectBase(unittest.TestCase):
    def setUp(self):
        self.source = read_runtime_source()
        self.saved = (pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN,
                      dict(pricing._FLEET_CACHE))
        pricing.PRICING_ACTION = "quote_price"
        pricing.BRIDGE_URL = "https://fake.invalid/exec"
        pricing.BRIDGE_TOKEN = "fake-token-for-tests"
        pricing._FLEET_CACHE["data"], pricing._FLEET_CACHE["ts"] = None, 0.0

    def tearDown(self):
        pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN, cache = self.saved
        pricing._FLEET_CACHE.clear()
        pricing._FLEET_CACHE.update(cache)

    def collect_fixture(self, bridge, start=WINDOW_START):
        window = collect.probe_window(start)
        stamps = collect.stamps_now(evidence_ref="tests")
        return collect.collect(self.source, pricing, bridge, window, stamps)

    def run_into(self, bridge, tmp, names=("fixture.json", "candidate.json", "report.json")):
        return collect.run(RUNTIME_SOURCE, pricing, bridge, WINDOW_START,
                           *[os.path.join(tmp, n) for n in names])


class TestNoPostSurface(CollectBase):
    """Первое утверждение задачи: у адаптера НЕТ поверхности записи, и доказывается это кодом."""

    def test_only_two_actions_are_declared(self):
        self.assertEqual(collect.ALLOWED_ACTIONS, ("fleet", "quote_price"))

    def test_guard_blocks_any_other_action(self):
        seen = []
        get = collect.guard_actions(lambda p: {"ok": True}, seen)
        for forbidden in ("create_booking", "set_caps", "toggle_cap", "clients", None, ""):
            with self.assertRaises(collect.CollectError):
                get({"action": forbidden})
        self.assertEqual(seen, [])
        self.assertEqual(get({"action": "fleet"}), {"ok": True})
        self.assertEqual(get({"action": "quote_price"}), {"ok": True})
        self.assertEqual(seen, ["fleet", "quote_price"])

    def test_module_source_has_no_write_surface(self):
        with io.open(os.path.join(HERE, "price_snapshot_collect.py"), encoding="utf-8") as src:
            text = src.read()
        for token in ("urlopen", "urllib", "requests", "http.client", "socket", "POST",
                      "create_booking", "set_caps", "toggle_cap", "brain_writer", "subprocess",
                      "shutil", "os.remove", "os.unlink", "cowork_log"):
            self.assertNotIn(token, text, "в адаптере найдено %r — это поверхность записи" % token)

    def test_module_level_imports_are_a_closed_list(self):
        """Транспорт поднимается ЛЕНИВО и только внутри `live_pricing`; на уровне модуля сети нет."""
        with io.open(os.path.join(HERE, "price_snapshot_collect.py"), encoding="utf-8") as src:
            tree = ast.parse(src.read())
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                top.add(node.module or "")
        self.assertEqual(top, {"argparse", "datetime", "json", "os", "sys",
                               "price_snapshot_publish", "price_source"})

    def test_live_run_asks_only_fleet_and_quote_price(self):
        bridge = FakeBridge(self.source)
        got = self.collect_fixture(bridge)
        self.assertEqual(got["status"], "ok")
        self.assertEqual({c["action"] for c in bridge.calls}, {"fleet", "quote_price"})
        self.assertEqual([r["action"] for r in got["actions"]], ["fleet", "quote_price"])
        self.assertEqual([r["method"] for r in got["actions"]], ["GET", "GET"])


class TestFleetFailureIsUnknown(CollectBase):
    def test_fleet_refusal_gives_unknown_and_no_candidate(self):
        bridge = FakeBridge(self.source, fleet_ok=False)
        got = self.collect_fixture(bridge)
        self.assertEqual(got["status"], "unknown")
        self.assertIsNone(got["fixture"])
        self.assertIn("парк НЕ получен", got["why"])
        self.assertEqual({c["action"] for c in bridge.calls}, {"fleet"})

    def test_run_writes_nothing_when_fleet_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = self.run_into(FakeBridge(self.source, fleet_ok=False), tmp)
            self.assertEqual(got["status"], "unknown")
            self.assertEqual(os.listdir(tmp), [])


class TestFixtureShape(CollectBase):
    def test_window_is_exactly_seven_days_and_explicit(self):
        window = collect.probe_window(WINDOW_START)
        self.assertEqual(window["date_start"], "2026-09-07")
        self.assertEqual(window["date_end"], "2026-09-14")
        self.assertEqual(window["days"], 7)
        got = self.collect_fixture(FakeBridge(self.source))
        self.assertEqual(got["fixture"]["source_window"]["days"], 7)
        self.assertEqual(got["fixture"]["source_window"]["date_end"], "2026-09-14")
        with self.assertRaises(collect.CollectError):
            collect.probe_window(WINDOW_START, days=30)

    def test_every_judged_product_is_covered_once(self):
        got = self.collect_fixture(FakeBridge(self.source))
        want = set(pub._base_index(self.source))
        seen = {(p["model"], p.get("generation") or "") for p in got["fixture"]["products"]}
        self.assertEqual(seen, want)
        self.assertEqual(len(got["fixture"]["products"]), len(want))

    def test_all_three_handles_are_taken_from_live_quotes(self):
        got = self.collect_fixture(FakeBridge(self.source))
        handles = {r["cell"]: r["global_discount"] for r in got["fixture"]["handles"]}
        self.assertEqual(sorted(handles), ["H3", "I3", "J3"])
        self.assertEqual(handles["J3"], self.source["low_season_discount"]["скутер"])
        self.assertEqual(handles["H3"], self.source["low_season_discount"]["мото"])

    def test_handle_map_agrees_with_recorded_probes(self):
        """Карта категорий сверяется с УЖЕ ЗАПИСАННЫМ набором проб сторожа свежести, а не с собой."""
        for cell, bikes in price_freshness_run.PROBES:
            for bike in bikes:
                row, how = price_source_resolve(self.source, bike)
                self.assertIsNotNone(row, "проба %s не опознана: %s" % (bike, how))
                self.assertEqual(collect.handle_cell(row["model"]), cell,
                                 "проба %s записана за ячейкой %s" % (bike, cell))

    def test_handle_map_covers_every_model_of_the_runtime_file(self):
        for key in pub._base_index(self.source):
            self.assertIn(key[0], collect.HANDLE_CELL_BY_MODEL)

    def test_availability_deposit_and_cap_stay_evidence_only(self):
        got = self.collect_fixture(FakeBridge(self.source))
        for product in got["fixture"]["products"]:
            for unit in product["units"]:
                self.assertEqual(sorted(unit), ["day_price", "name"])
            self.assertTrue(any("deposit" in e for e in product["evidence"]))
        candidate = pub.build_candidate(self.source, got["fixture"])
        for row in candidate["base"]["models"]:
            for leak in ("deposit", "available", "cap_price", "cap_active"):
                self.assertNotIn(leak, row)


class TestGenerationsStaySeparate(CollectBase):
    def test_xmax_generations_are_two_products_with_two_prices(self):
        got = self.collect_fixture(FakeBridge(self.source))
        xmax = {p["generation"]: p for p in got["fixture"]["products"] if p["model"] == "XMAX 300"}
        self.assertEqual(sorted(xmax), ["2020-2022", "2023-"])
        old = xmax["2020-2022"]["units"][0]["day_price"]
        new = xmax["2023-"]["units"][0]["day_price"]
        self.assertNotEqual(old, new)
        self.assertEqual(xmax["2020-2022"]["units"][0]["name"], "XMAX 300CC BLUE PHUKET 5773")
        self.assertEqual(xmax["2023-"]["units"][0]["name"], "XMAX 300CC NEW BLACK PHUKET 8969")

    def test_generation_survives_a_door_that_did_not_name_the_sheet_row(self):
        """Строку листа дверь может не назвать — тогда поколение держит метка `NEW` в имени юнита."""
        got = self.collect_fixture(FakeBridge(self.source, name_only=True))
        xmax = {p["generation"]: p for p in got["fixture"]["products"] if p["model"] == "XMAX 300"}
        self.assertEqual(sorted(xmax), ["2020-2022", "2023-"])
        self.assertEqual(len(xmax["2023-"]["units"]), 1)

    def test_unnamed_generation_blocks_instead_of_guessing(self):
        """Юнит без метки и без строки листа поколением не наделяется — продукт остаётся пустым."""
        units = [u for u in FLEET_UNITS if u[0] != "XMAX 300CC NEW BLACK PHUKET 8969"]
        units.append(("XMAX 300CC BLACK PHUKET 8969", "XMAX 300", "2023-"))
        bridge = FakeBridge(self.source, units=tuple(units), name_only=True)
        with self.assertRaisesRegex(collect.CollectError, "XMAX 300/2023-"):
            self.collect_fixture(bridge)


class TestBlocks(CollectBase):
    def test_missing_product_blocks_the_whole_candidate(self):
        units = tuple(u for u in FLEET_UNITS if u[1] != "CLICK 125")
        with self.assertRaisesRegex(collect.CollectError, "CLICK 125"):
            self.collect_fixture(FakeBridge(self.source, units=units))

    def test_unit_price_disagreement_blocks(self):
        bridge = FakeBridge(self.source, overrides={"NMAX 155CC RED PHUKET 6001": 999})
        got = self.collect_fixture(bridge)
        with self.assertRaisesRegex(pub.CandidateError, "дали разные цены"):
            pub.build_candidate(self.source, got["fixture"])

    def test_handle_disagreement_inside_one_cell_blocks(self):
        bridge = FakeBridge(self.source, handles={("NMAX 155", ""): 0.10})
        with self.assertRaisesRegex(collect.CollectError, "J3"):
            self.collect_fixture(bridge)

    def test_unknown_product_has_no_handle_cell(self):
        with self.assertRaisesRegex(collect.CollectError, "категория ручки"):
            collect.handle_cell("SUZUKI НЕТ ТАКОЙ")

    def test_unknown_product_in_fixture_is_rejected_by_b11(self):
        got = self.collect_fixture(FakeBridge(self.source))
        fixture = copy.deepcopy(got["fixture"])
        fixture["products"].append({"model": "SUZUKI НЕТ ТАКОЙ", "handle_cell": "J3",
                                    "units": [{"name": "X", "day_price": 100}]})
        with self.assertRaisesRegex(pub.CandidateError, "неизвестный продукт"):
            pub.build_candidate(self.source, fixture)

    def test_door_that_counted_other_days_is_not_counted(self):
        window = collect.probe_window(WINDOW_START)
        facts, why = collect.unit_facts("X", {"day_price": 300, "days": 8,
                                              "season": {"global_discount": 0.25}}, window)
        self.assertIsNone(facts)
        self.assertIn("8 сут", why)

    def test_quote_without_handle_angle_is_not_counted(self):
        window = collect.probe_window(WINDOW_START)
        facts, why = collect.unit_facts("X", {"day_price": 300, "days": 7}, window)
        self.assertIsNone(facts)
        self.assertIn("global_discount", why)


class TestOutputIsFenced(CollectBase):
    def test_output_cannot_target_price_source_json(self):
        bridge = FakeBridge(self.source)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(pub.CandidateError, "price_source.json"):
                self.run_into(bridge, tmp, names=("fixture.json", "price_source.json",
                                                  "report.json"))
            self.assertEqual(os.listdir(tmp), [])
        # Замок стои́т ДО моста: запрещённая цель не стоит ни одного запроса наружу.
        self.assertEqual(bridge.calls, [])

    def test_runtime_source_is_byte_identical_around_a_full_run(self):
        with io.open(RUNTIME_SOURCE, "rb") as src:
            before = hashlib.sha256(src.read()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            got = self.run_into(FakeBridge(self.source), tmp)
            self.assertEqual(got["status"], "ok")
            self.assertEqual(sorted(os.listdir(tmp)),
                             ["candidate.json", "fixture.json", "report.json"])
            self.assertEqual(pub.main(["--verify", os.path.join(tmp, "candidate.json")]), 0)
        with io.open(RUNTIME_SOURCE, "rb") as src:
            after = hashlib.sha256(src.read()).hexdigest()
        self.assertEqual(before, after)


class TestNumbersAreHonest(CollectBase):
    def test_sheet_equal_to_low_season_reproduces_recorded_bases_to_the_baht(self):
        """Углы ручек равны скидкам низкого сезона → нормализация обязана вернуть ТЕ ЖЕ базы."""
        got = self.collect_fixture(FakeBridge(self.source))
        candidate = pub.build_candidate(self.source, got["fixture"])
        was = {(r["model"], (r.get("generation") or "").strip()): r["base_thb_per_day"]
               for r in self.source["base"]["models"]}
        now = {(r["model"], (r.get("generation") or "").strip()): r["base_thb_per_day"]
               for r in candidate["base"]["models"]}
        self.assertEqual(was, now)
        report = pub.build_report(self.source, candidate)
        self.assertEqual({c["delta_pct"] for c in report["changes"]}, {0.0})

    def test_candidate_says_it_is_not_published(self):
        got = self.collect_fixture(FakeBridge(self.source))
        candidate = pub.build_candidate(self.source, got["fixture"])
        self.assertIn("NOT PUBLISHED", candidate["status"]["verdict"])
        self.assertEqual(candidate["publication"]["status"], "candidate")
        self.assertEqual(candidate["publication"]["publisher"], "pending-owner-approval")


class TestCandidateMetadataIsCoherent(CollectBase):
    """B1.2a: кандидат, собранный из фикстуры ЖИВОЙ формы, не имеет права нести унаследованные
    от боевого `price_source.json` окно наблюдения и наблюдение ручек. Ровно этим дефектом
    прошлый живой кандидат прошёл структурную проверку: окно 15.09..16.09 при наблюдении
    07.09..14.09 и ручки 3/3/3 при снятых 4/3/7."""

    def built(self):
        got = self.collect_fixture(FakeBridge(self.source))
        return got["fixture"], pub.build_candidate(self.source, got["fixture"])

    def test_window_travels_from_fixture_into_freshness(self):
        fixture, candidate = self.built()
        self.assertEqual(candidate["freshness"]["window"], fixture["source_window"])
        self.assertEqual(candidate["publication"]["source_window"], fixture["source_window"])
        self.assertEqual(candidate["freshness"]["window"]["date_start"], WINDOW_START)
        self.assertNotEqual(candidate["freshness"]["window"], self.source["freshness"]["window"])

    def test_units_agreed_is_observed_not_inherited(self):
        _fixture, candidate = self.built()
        for block in (candidate["freshness"]["handles"], candidate["publication"]["handles"]):
            self.assertEqual({r["cell"]: r["units_agreed"] for r in block},
                             {"H3": 4, "I3": 3, "J3": 7})
        self.assertEqual({r["cell"]: r["units_agreed"] for r in self.source["freshness"]["handles"]},
                         {"H3": 3, "I3": 3, "J3": 3})

    def test_taken_from_is_observed_and_category_survives(self):
        fixture, candidate = self.built()
        rows = {r["cell"]: r for r in candidate["freshness"]["handles"]}
        self.assertEqual({c: r["taken_from"] for c, r in rows.items()},
                         {r["cell"]: r["taken_from"] for r in fixture["handles"]})
        self.assertIn("XMAX 300/2023-", rows["J3"]["taken_from"])
        self.assertEqual({c: r["category"] for c, r in rows.items()},
                         {r["cell"]: r["category"] for r in self.source["freshness"]["handles"]})

    def test_inherited_metadata_no_longer_verifies(self):
        _fixture, candidate = self.built()
        stale_window = copy.deepcopy(candidate)
        stale_window["freshness"]["window"] = copy.deepcopy(self.source["freshness"]["window"])
        stale_window["publication"]["content_sha256"] = pub.content_sha256(stale_window)
        with self.assertRaisesRegex(pub.CandidateError, "source_window"):
            pub.verify_candidate(stale_window)
        stale_handles = copy.deepcopy(candidate)
        inherited = copy.deepcopy(self.source["freshness"]["handles"])
        stale_handles["freshness"]["handles"] = inherited
        stale_handles["publication"]["handles"] = copy.deepcopy(inherited)
        stale_handles["publication"]["content_sha256"] = pub.content_sha256(stale_handles)
        with self.assertRaisesRegex(pub.CandidateError, "taken_from"):
            pub.verify_candidate(stale_handles)


def price_source_resolve(source, bike):
    """Имя юнита → строка файла тем же кодом, что и боевой путь ответа."""
    import price_source
    return price_source.resolve_row(source, None, pricing._norm_nocc, quote={"bike": bike})


if __name__ == "__main__":
    unittest.main()
