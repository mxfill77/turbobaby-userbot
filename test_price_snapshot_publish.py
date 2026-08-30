# -*- coding: utf-8 -*-
import copy
import json
import os
import tempfile
import unittest

import price_snapshot_publish as pub


class TestPriceSnapshotPublisherB11(unittest.TestCase):
    def source(self):
        return {
            "schema": pub.SOURCE_SCHEMA,
            "version": 1,
            "built_on": "2026-08-18",
            "status": {"verdict": "RUNTIME SOURCE"},
            "low_season_discount": {"скутер": 0.25, "мото": 0.15},
            "base": {"models": [
                {"model": "NMAX 155", "class": "скутер", "judged": True,
                 "base_thb_per_day": 298},
                {"model": "XMAX 300", "generation": "2020-2022", "class": "скутер",
                 "judged": True, "base_thb_per_day": 557},
                {"model": "XMAX 300", "generation": "2023-", "class": "скутер",
                 "judged": True, "base_thb_per_day": 662},
            ]},
            "season": {"periods": [{"key": "P1", "multiplier": {"скутер": 1.0}}]},
            "term": {"buckets": [{"bucket": "7-13", "multiplier": 1.0}]},
            "freshness": {
                "snapshot_on": "2026-08-18", "built_on": "2026-08-17",
                "max_age_days_default": 14, "max_age_env": "PRICE_FRESH_MAX_AGE_DAYS",
                # УНАСЛЕДОВАННОЕ окно и УНАСЛЕДОВАННОЕ наблюдение ручек — ровно то, что живой
                # кандидат B1.2 протащил в себя молча. Числа нарочно отличаются от fixture.
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

    def fixture(self):
        return {
            "schema": pub.FIXTURE_SCHEMA,
            "snapshot_id": "price-20260831T120000Z",
            "generated_at": "2026-08-31T12:00:00Z",
            "effective_at": "2026-09-01T00:00:00Z",
            "source_window": {"date_start": "2026-09-07", "date_end": "2026-09-14", "days": 7},
            "evidence_ref": "docs/artifacts/b1-dry-run.json",
            "handles": [
                {"cell": "H3", "global_discount": 0.15, "units_agreed": 4,
                 "taken_from": ["CB 300/", "MT-03 300/", "NINJA 400/", "XSR 155/"]},
                {"cell": "I3", "global_discount": 0.15, "units_agreed": 3,
                 "taken_from": ["CB 650R/", "CBR 650R/", "VULCAN 650/"]},
                {"cell": "J3", "global_discount": 0.20, "units_agreed": 7,
                 "taken_from": ["ADV 350/", "CLICK 125/", "FORZA 300/", "NMAX 155/",
                                "XADV 750/", "XMAX 300/2020-2022", "XMAX 300/2023-"]},
            ],
            "products": [
                {"model": "NMAX 155", "handle_cell": "J3",
                 "units": [{"name": "NMAX A", "day_price": 307}, {"name": "NMAX B", "day_price": 307}]},
                {"model": "XMAX 300", "generation": "2020-2022", "handle_cell": "J3",
                 "units": [{"name": "XMAX OLD", "day_price": 594}]},
                {"model": "XMAX 300", "generation": "2023-", "handle_cell": "J3",
                 "units": [{"name": "XMAX NEW", "day_price": 706}]},
            ],
        }

    def test_candidate_is_deterministic_and_hash_verifies(self):
        a = pub.build_candidate(self.source(), self.fixture())
        b = pub.build_candidate(self.source(), self.fixture())
        self.assertEqual(a, b)
        self.assertEqual(pub.verify_candidate(a)["content_sha256"], a["publication"]["content_sha256"])

    def test_candidate_updates_base_and_freshness_together(self):
        candidate = pub.build_candidate(self.source(), self.fixture())
        rows = {(r["model"], r.get("generation") or ""): r for r in candidate["base"]["models"]}
        self.assertEqual(rows[("NMAX 155", "")]["base_thb_per_day"], 288)
        self.assertEqual(rows[("XMAX 300", "2020-2022")]["base_thb_per_day"], 557)
        self.assertEqual(rows[("XMAX 300", "2023-")]["base_thb_per_day"], 662)
        self.assertEqual(candidate["built_on"], "2026-08-31")
        self.assertEqual(candidate["freshness"]["snapshot_on"], "2026-08-31")
        handles = {r["cell"]: r["global_discount"] for r in candidate["freshness"]["handles"]}
        self.assertEqual(handles, {"H3": 0.15, "I3": 0.15, "J3": 0.20})

    def resealed(self, candidate):
        """Кандидата помяли — пересчитали ЕГО ЖЕ hash. Так негативный замок доказывает именно
        сверку метаданных, а не то, что заодно съехала контрольная сумма."""
        candidate["publication"]["content_sha256"] = pub.content_sha256(candidate)
        return candidate

    # --- B1.2a: метаданные кандидата обязаны совпадать с его собственной уликой ---

    def test_freshness_window_is_the_fixture_window_not_the_inherited_one(self):
        fixture = self.fixture()
        candidate = pub.build_candidate(self.source(), fixture)
        self.assertEqual(candidate["freshness"]["window"], fixture["source_window"])
        self.assertEqual(candidate["publication"]["source_window"], fixture["source_window"])
        self.assertNotEqual(candidate["freshness"]["window"],
                            self.source()["freshness"]["window"])

    def test_observed_handle_fields_come_from_fixture_and_category_survives(self):
        candidate = pub.build_candidate(self.source(), self.fixture())
        rows = {r["cell"]: r for r in candidate["freshness"]["handles"]}
        self.assertEqual({cell: row["units_agreed"] for cell, row in rows.items()},
                         {"H3": 4, "I3": 3, "J3": 7})
        self.assertEqual(rows["I3"]["taken_from"], ["CB 650R/", "CBR 650R/", "VULCAN 650/"])
        # Описательное поле ячейки листа переживает заход, наблюдённое — нет.
        self.assertEqual({cell: row["category"] for cell, row in rows.items()},
                         {"H3": "мото-1", "I3": "мото-2", "J3": "скутеры"})
        for row in rows.values():
            self.assertNotIn("старое", " ".join(row["taken_from"]))

    def test_publication_handles_are_a_deep_copy_of_freshness_handles(self):
        candidate = pub.build_candidate(self.source(), self.fixture())
        self.assertEqual(candidate["publication"]["handles"], candidate["freshness"]["handles"])
        candidate["freshness"]["handles"][0]["units_agreed"] = 99
        self.assertEqual(candidate["publication"]["handles"][0]["units_agreed"], 4)

    def test_window_disagreement_is_fail_closed(self):
        candidate = pub.build_candidate(self.source(), self.fixture())
        candidate["freshness"]["window"] = {"date_start": "15.09.2026", "date_end": "16.09.2026"}
        with self.assertRaisesRegex(pub.CandidateError, "source_window"):
            pub.verify_candidate(self.resealed(candidate))

    def test_empty_window_is_not_agreement(self):
        candidate = pub.build_candidate(self.source(), self.fixture())
        candidate["freshness"]["window"] = {}
        candidate["publication"]["source_window"] = {}
        with self.assertRaisesRegex(pub.CandidateError, "freshness.window отсутствует"):
            pub.verify_candidate(self.resealed(candidate))

    def test_handle_block_disagreement_is_fail_closed(self):
        candidate = pub.build_candidate(self.source(), self.fixture())
        candidate["publication"]["handles"][2]["units_agreed"] = 3
        with self.assertRaisesRegex(pub.CandidateError, "publication.handles"):
            pub.verify_candidate(self.resealed(candidate))

    def test_missing_observed_handle_field_is_fail_closed_on_verify(self):
        for field in pub.OBSERVED_HANDLE_FIELDS:
            candidate = pub.build_candidate(self.source(), self.fixture())
            for row in (candidate["freshness"]["handles"][0],
                        candidate["publication"]["handles"][0]):
                row.pop(field)
            with self.assertRaisesRegex(pub.CandidateError, field):
                pub.verify_candidate(self.resealed(candidate))

    def test_missing_observed_handle_field_is_fail_closed_on_build(self):
        for field in pub.OBSERVED_HANDLE_FIELDS:
            fixture = self.fixture()
            fixture["handles"][1].pop(field)
            with self.assertRaisesRegex(pub.CandidateError, field):
                pub.build_candidate(self.source(), fixture)

    def test_fixture_without_source_window_is_fail_closed(self):
        fixture = self.fixture()
        fixture.pop("source_window")
        with self.assertRaisesRegex(pub.CandidateError, "source_window"):
            pub.build_candidate(self.source(), fixture)

    def test_missing_handle_is_fail_closed(self):
        fixture = self.fixture()
        fixture["handles"] = fixture["handles"][:-1]
        with self.assertRaisesRegex(pub.CandidateError, "J3"):
            pub.build_candidate(self.source(), fixture)

    def test_unknown_model_is_fail_closed(self):
        fixture = self.fixture()
        fixture["products"][0]["model"] = "SUZUKI НЕТ ТАКОЙ"
        with self.assertRaisesRegex(pub.CandidateError, "неизвестный продукт"):
            pub.build_candidate(self.source(), fixture)

    def test_missing_product_blocks_incomplete_candidate(self):
        fixture = self.fixture()
        fixture["products"] = fixture["products"][:-1]
        with self.assertRaisesRegex(pub.CandidateError, "неполный candidate"):
            pub.build_candidate(self.source(), fixture)

    def test_xmax_without_generation_is_ambiguous(self):
        fixture = self.fixture()
        fixture["products"][1].pop("generation")
        with self.assertRaisesRegex(pub.CandidateError, "неоднозначный продукт XMAX"):
            pub.build_candidate(self.source(), fixture)

    def test_unit_price_disagreement_blocks_candidate(self):
        fixture = self.fixture()
        fixture["products"][0]["units"][1]["day_price"] = 308
        with self.assertRaisesRegex(pub.CandidateError, "дали разные цены"):
            pub.build_candidate(self.source(), fixture)

    def test_hash_mismatch_is_rejected(self):
        candidate = pub.build_candidate(self.source(), self.fixture())
        candidate["base"]["models"][0]["base_thb_per_day"] += 1
        with self.assertRaisesRegex(pub.CandidateError, "content_sha256"):
            pub.verify_candidate(candidate)

    def test_dry_run_writes_separate_files_and_keeps_source_byte_exact(self):
        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "price_source.json")
            fixture = os.path.join(td, "fixture.json")
            candidate = os.path.join(td, "candidate.json")
            report = os.path.join(td, "report.json")
            for path, value in ((source, self.source()), (fixture, self.fixture())):
                with open(path, "w", encoding="utf-8", newline="\n") as out:
                    json.dump(value, out, ensure_ascii=False, indent=2)
            with open(source, "rb") as current:
                before = current.read()
            code = pub.main(["--dry-run", "--source", source, "--fixture", fixture,
                             "--output", candidate, "--report", report])
            self.assertEqual(code, 0)
            with open(source, "rb") as current:
                self.assertEqual(current.read(), before)
            self.assertTrue(os.path.exists(candidate))
            self.assertTrue(os.path.exists(report))
            self.assertEqual(pub.main(["--verify", candidate]), 0)

    def test_dry_run_cannot_target_price_source(self):
        with tempfile.TemporaryDirectory() as td:
            source = os.path.join(td, "source.json")
            fixture = os.path.join(td, "fixture.json")
            forbidden = os.path.join(td, "price_source.json")
            report = os.path.join(td, "report.json")
            for path, value in ((source, self.source()), (fixture, self.fixture())):
                with open(path, "w", encoding="utf-8") as out:
                    json.dump(value, out, ensure_ascii=False)
            code = pub.main(["--dry-run", "--source", source, "--fixture", fixture,
                             "--output", forbidden, "--report", report])
            self.assertEqual(code, 2)
            self.assertFalse(os.path.exists(forbidden))


if __name__ == "__main__":
    unittest.main()
