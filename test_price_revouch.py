# -*- coding: utf-8 -*-
"""Замки пересвидетельствования слепка цен.

Предмет набора — не «функция вернула словарь», а ДВА обещания, каждое из которых стоит денег,
если соврать:

1. ДАТА ДВИГАЕТСЯ ТОЛЬКО ЗА СОВПАДЕНИЕМ. Цена одного юнита на поддельной копии сдвинута на один
   бат — режим обязан ОТКАЗАТЬ и не тронуть дату (`TestRefusal`). Без этого замка режим
   превращается в кнопку «объявить файл свежим», то есть ровно в ту тихую ложь, ради которой
   возрастная ветка сторожа и заведена.
2. КРОМЕ ДАТЫ НЕ ДВИГАЕТСЯ НИЧЕГО. Проверяется не глазами, а путевой сверкой всего документа
   (`TestPathInvariant`): базы, множители, депозиты, кепки, `owner_decision`, `n_bookings`,
   `p25_p75` обязаны доехать байт в байт.
"""

import copy
import io
import json
import os
import tempfile
import unittest

import price_freshness as pf
import price_revouch as rv
import price_snapshot_publish as pub


def source_doc():
    """Записанное правило-образец. Базы подобраны так, что при ручках листа они СОВПАДАЮТ с
    ценой суток фикстуры: у класса «скутер» низкий сезон 0.25 и живая J3 0.25, значит
    `база = цена суток` по построению `build_candidate` — своей арифметики тест не заводит."""
    return {
        "schema": pub.SOURCE_SCHEMA,
        "version": 1,
        "built_on": "2026-08-17",
        "low_season_discount": {"скутер": 0.25, "мото": 0.15},
        "base": {"models": [
            {"model": "NMAX 155", "class": "скутер", "judged": True, "base_thb_per_day": 298,
             "n_bookings": 48, "p25_p75": [274, 314], "measured_on": "2026-08-16",
             "base_method": "медиана уплаченного", "source": "срез боевого моста"},
            {"model": "XMAX 300", "generation": "2020-2022", "class": "скутер", "judged": True,
             "base_thb_per_day": 557, "owner_decision": "решение владельца 26.08.2026"},
            {"model": "XMAX 300", "generation": "2023-", "class": "скутер", "judged": True,
             "base_thb_per_day": 662, "owner_decision": "решение владельца 26.08.2026"},
        ]},
        "season": {"periods": [{"key": "P1", "multiplier": {"скутер": 1.0}}]},
        "term": {"buckets": [{"bucket": "7-13", "multiplier": 1.0}]},
        "low_season_caps": {"NMAX 155": {"cap": 6900, "active": True}},
        "provenance": {"bookings_slice": {"rows": 1278}},
        "freshness": {
            "snapshot_on": "2026-08-18", "built_on": "2026-08-17",
            "max_age_days_default": 14, "max_age_env": "PRICE_FRESH_MAX_AGE_DAYS",
            "window": {"date_start": "15.09.2026", "date_end": "16.09.2026"},
            "handles": [
                {"cell": "H3", "category": "мото-1", "global_discount": 0.15,
                 "units_agreed": 3, "taken_from": ["старое H3"]},
                {"cell": "I3", "category": "мото-2", "global_discount": 0.15,
                 "units_agreed": 3, "taken_from": ["старое I3"]},
                {"cell": "J3", "category": "скутеры", "global_discount": 0.25,
                 "units_agreed": 3, "taken_from": ["старое J3"]},
            ],
        },
    }


def fixture_doc():
    """Фикстура живого листа, СОГЛАСНАЯ с правилом до бата."""
    return {
        "schema": pub.FIXTURE_SCHEMA,
        "snapshot_id": "price-20260902T090000Z",
        "generated_at": "2026-09-02T09:00:00Z",
        "effective_at": "2026-09-03T00:00:00Z",
        "source_window": {"date_start": "2026-09-07", "date_end": "2026-09-14", "days": 7,
                          "bucket": "7-13"},
        "evidence_ref": "_scratch/live_fixture.json",
        "handles": [
            {"cell": "H3", "global_discount": 0.15, "units_agreed": 3, "taken_from": ["H3 юниты"]},
            {"cell": "I3", "global_discount": 0.15, "units_agreed": 3, "taken_from": ["I3 юниты"]},
            {"cell": "J3", "global_discount": 0.25, "units_agreed": 4, "taken_from": ["J3 юниты"]},
        ],
        "products": [
            {"model": "NMAX 155", "handle_cell": "J3",
             "units": [{"name": "NMAX A", "day_price": 298}, {"name": "NMAX B", "day_price": 298}]},
            {"model": "XMAX 300", "generation": "2020-2022", "handle_cell": "J3",
             "units": [{"name": "XMAX OLD", "day_price": 557}]},
            {"model": "XMAX 300", "generation": "2023-", "handle_cell": "J3",
             "units": [{"name": "XMAX NEW", "day_price": 662}]},
        ],
    }


TODAY = "2026-09-02"


class TestMatch(unittest.TestCase):
    """Совпало — дата двигается, и ровно она."""

    def test_compare_says_matched_on_agreeing_pair(self):
        report = rv.compare(source_doc(), fixture_doc())
        self.assertTrue(report["matched"])
        self.assertEqual(report["products_checked"], 3)
        self.assertEqual(report["products_agreed"], 3)
        self.assertEqual(report["diverged"], [])
        self.assertEqual(report["units_counted"], 4)
        self.assertEqual(report["handles_live"], {"H3": 0.15, "I3": 0.15, "J3": 0.25})

    def test_snapshot_on_moves_to_named_day(self):
        out, _ = rv.revouch(source_doc(), fixture_doc(), TODAY)
        self.assertEqual(out["freshness"]["snapshot_on"], TODAY)
        self.assertEqual(out["freshness"]["revouch"]["previous_snapshot_on"], "2026-08-18")
        self.assertEqual(out["freshness"]["revouch"]["verdict"], rv.VERDICT_MATCH)

    def test_built_on_does_not_move(self):
        """Правило не пересобиралось: врать о дате СБОРКИ пересвидетельствование не вправе."""
        out, _ = rv.revouch(source_doc(), fixture_doc(), TODAY)
        self.assertEqual(out["built_on"], "2026-08-17")
        self.assertEqual(out["freshness"]["built_on"], "2026-08-17")

    def test_record_carries_numbers_a_human_can_check(self):
        out, _ = rv.revouch(source_doc(), fixture_doc(), TODAY)
        record = out["freshness"]["revouch"]
        self.assertEqual(record["products_checked"], 3)
        self.assertEqual(record["products_diverged"], 0)
        self.assertEqual(record["units_counted"], 4)
        self.assertEqual(record["handles_agreed"], ["H3", "I3", "J3"])
        self.assertEqual(record["fixture_snapshot_id"], "price-20260902T090000Z")
        self.assertEqual(record["window"]["date_start"], "2026-09-07")

    def test_count_increments_across_revouches(self):
        first, _ = rv.revouch(source_doc(), fixture_doc(), TODAY)
        second, _ = rv.revouch(first, fixture_doc(), "2026-09-03")
        self.assertEqual(first["freshness"]["revouch"]["count"], 1)
        self.assertEqual(second["freshness"]["revouch"]["count"], 2)
        self.assertEqual(second["freshness"]["revouch"]["previous_snapshot_on"], TODAY)

    def test_input_document_is_not_mutated(self):
        doc = source_doc()
        untouched = copy.deepcopy(doc)
        rv.revouch(doc, fixture_doc(), TODAY)
        self.assertEqual(doc, untouched)

    def test_date_must_be_named_and_parseable(self):
        self.assertRaises(rv.RevouchError, rv.revouch, source_doc(), fixture_doc(), "")
        self.assertRaises(rv.RevouchError, rv.revouch, source_doc(), fixture_doc(), "вчера")


class TestRefusal(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ЗАМОК ЗАДАНИЯ: подменённая цена одного юнита обязана дать ОТКАЗ."""

    def bumped_fixture(self, delta=1):
        """Поддельная копия: цена ОДНОГО юнита сдвинута на бат. Второй юнит того же продукта
        сдвинут вместе с ним сознательно — иначе заход упал бы раньше, на замке B1.1 «юниты
        продукта дали разные цены», и отказ доказывал бы НЕ ТО правило."""
        fixture = fixture_doc()
        for unit in fixture["products"][0]["units"]:
            unit["day_price"] += delta
        return fixture

    def test_one_unit_price_changed_refuses(self):
        report = rv.compare(source_doc(), self.bumped_fixture())
        self.assertFalse(report["matched"])
        self.assertEqual(len(report["diverged"]), 1)
        self.assertEqual(report["diverged"][0]["model"], "NMAX 155")
        self.assertEqual(report["diverged"][0]["recorded_thb_per_day"], 298)
        self.assertEqual(report["diverged"][0]["live_thb_per_day"], 299)

    def test_refusal_names_the_row_and_the_numbers(self):
        report = rv.compare(source_doc(), self.bumped_fixture())
        text = rv.refusal_text(report)
        self.assertIn("NMAX 155", text)
        self.assertIn("298", text)
        self.assertIn("299", text)

    def test_revouch_raises_and_date_stays(self):
        doc = source_doc()
        with self.assertRaises(rv.RevouchError) as caught:
            rv.revouch(doc, self.bumped_fixture(), TODAY)
        self.assertIn("ОТКАЗ", str(caught.exception))
        self.assertIn("NMAX 155", str(caught.exception))
        # Дата на месте, и записи о пересвидетельствовании не появилось.
        self.assertEqual(doc["freshness"]["snapshot_on"], "2026-08-18")
        self.assertNotIn("revouch", doc["freshness"])

    def test_turned_handle_refuses(self):
        fixture = fixture_doc()
        for row in fixture["handles"]:
            if row["cell"] == "J3":
                row["global_discount"] = 0.20
        report = rv.compare(source_doc(), fixture)
        self.assertFalse(report["matched"])
        self.assertEqual(report["handles_diverged"][0]["cell"], "J3")
        self.assertIn("ручка J3", rv.refusal_text(report))

    def test_missing_product_refuses(self):
        """Продукт, которому лист не дал юнитов, — расхождение, а не «нечего сверять»."""
        fixture = fixture_doc()
        fixture["products"] = fixture["products"][:2]
        with self.assertRaises(rv.RevouchError) as caught:
            rv.compare(source_doc(), fixture)
        self.assertIn("живой лист не дал сверяемого набора", str(caught.exception))

    def test_frozen_row_divergence_refuses_whole_run(self):
        """Одна строка из трёх — уже отказ ЦЕЛИКОМ: частичного пересвидетельствования нет."""
        fixture = fixture_doc()
        fixture["products"][2]["units"][0]["day_price"] = 700
        with self.assertRaises(rv.RevouchError):
            rv.revouch(source_doc(), fixture, TODAY)


class TestPathInvariant(unittest.TestCase):
    """Обещание «кроме даты не двигается ничего» — измеримое, а не словесное."""

    def test_only_date_paths_change(self):
        before = source_doc()
        after, _ = rv.revouch(before, fixture_doc(), TODAY)
        for path in rv.changed_paths(before, after):
            self.assertTrue(rv._is_allowed(path), "тронут путь вне зоны даты: %s" % path)
        self.assertEqual(rv.stray_paths(before, after), [])

    def test_bases_and_evidence_survive_word_for_word(self):
        before = source_doc()
        after, _ = rv.revouch(before, fixture_doc(), TODAY)
        self.assertEqual(before["base"], after["base"])
        self.assertEqual(before["season"], after["season"])
        self.assertEqual(before["term"], after["term"])
        self.assertEqual(before["low_season_caps"], after["low_season_caps"])
        self.assertEqual(before["low_season_discount"], after["low_season_discount"])
        self.assertEqual(before["provenance"], after["provenance"])
        self.assertEqual(before["freshness"]["handles"], after["freshness"]["handles"])

    def test_changed_paths_sees_nested_value_and_list_index(self):
        a = source_doc()
        b = copy.deepcopy(a)
        b["base"]["models"][0]["base_thb_per_day"] = 299
        self.assertEqual(rv.changed_paths(a, b), ["base.models[0].base_thb_per_day"])

    def test_changed_paths_sees_reordered_list(self):
        """Перестановка строк — изменение документа, а не «тот же набор»."""
        a = source_doc()
        b = copy.deepcopy(a)
        b["base"]["models"][1], b["base"]["models"][2] = b["base"]["models"][2], b["base"]["models"][1]
        self.assertTrue(rv.changed_paths(a, b))

    def test_changed_paths_sees_appearing_and_vanishing_paths(self):
        a = source_doc()
        b = copy.deepcopy(a)
        b["base"]["models"][0].pop("n_bookings")
        b["base"]["models"][0]["новое"] = 1
        self.assertEqual(rv.changed_paths(a, b),
                         ["base.models[0].n_bookings", "base.models[0].новое"])

    def test_stray_paths_catches_a_base_edit(self):
        a = source_doc()
        b = copy.deepcopy(a)
        b["freshness"]["snapshot_on"] = TODAY
        b["base"]["models"][0]["base_thb_per_day"] = 317
        self.assertEqual(rv.stray_paths(a, b), ["base.models[0].base_thb_per_day"])

    def test_allowed_zone_is_exactly_two_roots(self):
        """Список зоны — предмет договора: расширение молча превратило бы режим в публикацию."""
        self.assertEqual(rv.ALLOWED_PATHS, ("freshness.snapshot_on", "freshness.revouch"))


class _Sentinel(RuntimeError):
    pass


class TestDelegation(unittest.TestCase):
    """Своей арифметики цены в модуле нет — счёт делает штатный B1.1."""

    def test_compare_counts_by_build_candidate(self):
        seen = []
        original = pub.build_candidate

        def spy(source, fixture):
            seen.append((source, fixture))
            raise _Sentinel("сюда и должен уходить счёт базы")

        pub.build_candidate = spy
        try:
            self.assertRaises(_Sentinel, rv.compare, source_doc(), fixture_doc())
        finally:
            pub.build_candidate = original
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0]["schema"], pub.SOURCE_SCHEMA)
        self.assertEqual(seen[0][1]["schema"], pub.FIXTURE_SCHEMA)


class TestDisk(unittest.TestCase):
    """Руки: боевой файл, резервная копия, обратное чтение."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="revouch-")
        self.path = os.path.join(self.folder, "price_source.json")
        pub._write_json_atomic(self.path, source_doc())

    def read(self):
        with io.open(self.path, encoding="utf-8") as src:
            return json.load(src)

    def raw(self):
        with io.open(self.path, encoding="utf-8") as src:
            return src.read()

    def test_refusal_leaves_the_file_byte_identical(self):
        before = self.raw()
        fixture = fixture_doc()
        fixture["products"][0]["units"][0]["day_price"] = 299
        fixture["products"][0]["units"][1]["day_price"] = 299
        self.assertRaises(rv.RevouchError, rv.apply_to_disk, self.path, fixture, TODAY)
        self.assertEqual(self.raw(), before)
        # И резервной копии тоже нет: отказ не касается диска вовсе.
        self.assertFalse(os.path.isdir(os.path.join(self.folder, "tmp", "price_revouch")))

    def test_apply_moves_only_the_date_and_leaves_a_backup(self):
        before = self.read()
        done = rv.apply_to_disk(self.path, fixture_doc(), TODAY, fixture_ref="фикстура.json")
        after = self.read()
        self.assertTrue(done["ok"])
        self.assertEqual(after["freshness"]["snapshot_on"], TODAY)
        self.assertEqual(rv.stray_paths(before, after), [])
        self.assertEqual(before["base"], after["base"])
        self.assertTrue(os.path.isfile(done["backup"]))
        with io.open(done["backup"], encoding="utf-8") as src:
            self.assertEqual(json.load(src), before)

    def test_apply_is_idempotent_in_effect(self):
        """Второй заход того же дня не двигает дату дальше и не портит базу."""
        rv.apply_to_disk(self.path, fixture_doc(), TODAY)
        first = self.read()
        rv.apply_to_disk(self.path, fixture_doc(), TODAY)
        second = self.read()
        self.assertEqual(first["freshness"]["snapshot_on"], second["freshness"]["snapshot_on"])
        self.assertEqual(first["base"], second["base"])
        self.assertEqual(second["freshness"]["revouch"]["count"], 2)


class TestGuardReadsBack(unittest.TestCase):
    """Чтение назад: что скажет СТОРОЖ до и после пересвидетельствования."""

    def live(self):
        return {"ok": True, "handles": {"H3": 0.15, "I3": 0.15, "J3": 0.25}}

    def test_guard_is_stale_before_revouch(self):
        verdict = pf.judge(pf.snapshot_of(source_doc()), self.live(),
                           now=TODAY, max_age=pf.MAX_AGE_DEFAULT)
        self.assertEqual(verdict["state"], pf.STALE)
        self.assertFalse(verdict["may_quote"])
        self.assertEqual(verdict["age_days"], 15.0)

    def test_guard_is_fresh_after_revouch(self):
        out, _ = rv.revouch(source_doc(), fixture_doc(), TODAY)
        verdict = pf.judge(pf.snapshot_of(out), self.live(),
                           now=TODAY, max_age=pf.MAX_AGE_DEFAULT)
        self.assertEqual(verdict["state"], pf.FRESH)
        self.assertTrue(verdict["may_quote"])
        self.assertFalse(verdict["call_owner"])
        self.assertEqual(verdict["age_days"], 0.0)
        self.assertEqual(pf.bot_action(verdict)["name_price_to_client"], True)

    def test_guard_stays_stale_when_revouch_refused(self):
        """Отказ не «оставляет как было по недосмотру» — он оставляет как было ПО УСТРОЙСТВУ."""
        doc = source_doc()
        fixture = fixture_doc()
        for unit in fixture["products"][0]["units"]:
            unit["day_price"] = 299
        self.assertRaises(rv.RevouchError, rv.revouch, doc, fixture, TODAY)
        verdict = pf.judge(pf.snapshot_of(doc), self.live(), now=TODAY,
                           max_age=pf.MAX_AGE_DEFAULT)
        self.assertEqual(verdict["state"], pf.STALE)


if __name__ == "__main__":
    unittest.main()
