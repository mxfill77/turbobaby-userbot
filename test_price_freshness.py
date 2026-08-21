# -*- coding: utf-8 -*-
"""Юниты СТОРОЖА СВЕЖЕСТИ записанного правила цены (`price_freshness` + руки `_run`).

Три ОТРИЦАТЕЛЬНЫХ теста — сердце файла, они названы заданием и обязаны устоять все три:
  · ручки разошлись        → цена НЕ называется;
  · лист недоступен        → цена НЕ называется;
  · слепок стар при сошедшихся ручках → цена НЕ называется.

Плюс инвариант чистоты (`PRICE_FRESH_PURE`): модуль решения не смеет обзавестись диском, сетью
или импортом того, за чем следит. Наблюдатель, живущий на наблюдаемом, себя не судит.
"""

import datetime
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_isolation  # noqa: F401  офлайн-дверь сторожа свежести (§5 обвязки)
import price_freshness as pf
import price_freshness_run as pfr


TAKEN = "2026-08-18"
TODAY = datetime.date(2026, 8, 20)          # через двое суток после слепка — заведомо в пороге
WANT = {"H3": 0.15, "I3": 0.15, "J3": 0.25}


def snap(taken=TAKEN, handles=None):
    """Слепок из ФАЙЛА в том виде, в каком его отдаёт `snapshot_of` — через сам `snapshot_of`,
    а не сборкой словаря руками: иначе тест сверял бы себя с собой."""
    rows = [{"cell": c, "category": c, "global_discount": v}
            for c, v in sorted((handles if handles is not None else WANT).items())]
    doc = {"freshness": {"snapshot_on": taken, "built_on": "2026-08-17",
                         "sheet": "Календарь бронирования H3/I3/J3", "handles": rows}}
    return pf.snapshot_of(doc)


def live(handles=None, ok=True, error=None):
    facts = {"ok": ok, "handles": handles if handles is not None else dict(WANT)}
    if error is not None:
        facts["error"] = error
    return facts


class TestThreeOutcomesExist(unittest.TestCase):
    """Исходов РОВНО три, и третий — не украшение."""

    def test_states_are_exactly_three(self):
        self.assertEqual(len(set(pf.STATES)), 3)
        self.assertEqual(set(pf.STATES), {pf.FRESH, pf.STALE, pf.UNKNOWN})

    def test_fresh_is_the_only_state_that_allows_a_price(self):
        allowed = []
        for state in pf.STATES:
            if pf.bot_action({"state": state, "why": "тест"})["name_price_to_client"]:
                allowed.append(state)
        self.assertEqual(allowed, [pf.FRESH])


class TestNegativeThree(unittest.TestCase):
    """ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА ЗАДАНИЯ. Каждый доказывает одно: цена НЕ называется."""

    def _price_is_withheld(self, verdict, expect_state):
        self.assertEqual(verdict["state"], expect_state, verdict["why"])
        self.assertFalse(verdict["may_quote"], verdict["why"])
        self.assertTrue(verdict["call_owner"], verdict["why"])
        action = pf.bot_action(verdict)
        self.assertFalse(action["name_price_to_client"])
        self.assertIsNone(action["client_price"])
        self.assertTrue(action["call_owner"])
        self.assertTrue(action["owner_card"])
        self.assertEqual(action["quote"], {"status": "error", "quote": None})
        return action

    # 1 ─────────────────────────────────────────────────────────────────────────────────────
    def test_handle_moved_price_is_not_named(self):
        """Владелец повернул скутерную ручку из низкого сезона в пик (0.25 → -0.10)."""
        v = pf.judge(snap(), live({"H3": 0.15, "I3": 0.15, "J3": -0.10}), now=TODAY, max_age=14)
        action = self._price_is_withheld(v, pf.STALE)
        self.assertEqual([d["cell"] for d in v["diverged"]], ["J3"])
        self.assertEqual(v["diverged"][0]["was"], 0.25)
        self.assertEqual(v["diverged"][0]["now"], -0.10)
        self.assertIn("J3", action["owner_card"])

    def test_any_one_of_three_handles_moving_is_enough(self):
        for cell in ("H3", "I3", "J3"):
            moved = dict(WANT)
            moved[cell] = moved[cell] + 0.05
            v = pf.judge(snap(), live(moved), now=TODAY, max_age=14)
            self._price_is_withheld(v, pf.STALE)

    def test_a_hundredth_of_a_point_is_a_divergence(self):
        """Допуск не превращается в «примерно то же»: 0.15 → 0.16 это уже другая ручка."""
        v = pf.judge(snap(), live({"H3": 0.16, "I3": 0.15, "J3": 0.25}), now=TODAY, max_age=14)
        self._price_is_withheld(v, pf.STALE)

    # 2 ─────────────────────────────────────────────────────────────────────────────────────
    def test_sheet_unavailable_price_is_not_named(self):
        """Лист недоступен — и это НЕ «проверено и хорошо»."""
        v = pf.judge(snap(), live(None, ok=False, error="мост не ответил"), now=TODAY, max_age=14)
        action = self._price_is_withheld(v, pf.UNKNOWN)
        self.assertIn("мост не ответил", v["why"])
        self.assertIn("ПРОВЕРИТЬ НЕ УДАЛОСЬ", action["owner_card"])

    def test_sheet_answers_nothing_at_all_price_is_not_named(self):
        for facts in ({}, {"ok": True}, {"ok": True, "handles": None}, {"ok": False},
                      None, "мусор"):
            v = pf.judge(snap(), facts, now=TODAY, max_age=14)
            self._price_is_withheld(v, pf.UNKNOWN)

    def test_one_handle_did_not_come_back_price_is_not_named(self):
        """Часть ручек снялась, часть нет — сверка НЕПОЛНА, значит свежести не объявляем."""
        v = pf.judge(snap(), live({"H3": 0.15, "I3": None, "J3": 0.25}), now=TODAY, max_age=14)
        self._price_is_withheld(v, pf.UNKNOWN)
        self.assertEqual(v["unverified"], ["I3"])

    # 3 ─────────────────────────────────────────────────────────────────────────────────────
    def test_old_snapshot_with_agreed_handles_price_is_not_named(self):
        """СОШЕДШИЕСЯ ручки при мёртвом слепке. Через дверь виден только их угол, а не лист."""
        old = snap(taken="2026-06-01")
        v = pf.judge(old, live(), now=TODAY, max_age=14)
        action = self._price_is_withheld(v, pf.STALE)
        self.assertEqual(v["agreed"], ["H3", "I3", "J3"])       # ручки сошлись все три
        self.assertEqual(v["diverged"], [])                     # расхождений нет ни одного
        self.assertEqual(v["age_days"], 80.0)
        self.assertIn("старше порога", action["owner_card"])

    def test_age_is_judged_strictly_by_the_threshold(self):
        base = datetime.date(2026, 8, 18)
        for days, expect in ((13, pf.FRESH), (14, pf.FRESH), (15, pf.STALE), (400, pf.STALE)):
            v = pf.judge(snap(taken=base.isoformat()), live(),
                         now=base + datetime.timedelta(days=days), max_age=14)
            self.assertEqual(v["state"], expect, "возраст %d сут" % days)

    def test_undated_snapshot_is_not_fresh(self):
        """Дата слепка ЕСТЬ, но не разобрана — сосед по файлу её не подменяет.

        Первая редакция сторожа падала здесь на `built_on` и объявляла такое правило СВЕЖИМ:
        неразбор выдавался за факт. Тест поставлен на тот самый разрыв."""
        v = pf.judge(snap(taken="не-дата"), live(), now=TODAY, max_age=14)
        self._price_is_withheld(v, pf.UNKNOWN)
        self.assertIsNone(v["age_days"])

    def test_absent_snapshot_date_falls_back_to_the_build_date(self):
        """А вот ОТСУТСТВИЕ даты слепка — не отказ читателя: правило собрано и снято разом."""
        doc = {"freshness": {"built_on": "2026-06-01",
                             "handles": [{"cell": c, "global_discount": v}
                                         for c, v in sorted(WANT.items())]}}
        s = pf.snapshot_of(doc)
        self.assertEqual(s["taken"], datetime.date(2026, 6, 1))
        self._price_is_withheld(pf.judge(s, live(), now=TODAY, max_age=14), pf.STALE)


class TestCautionIsIdentical(unittest.TestCase):
    """Второй и третий исходы ведут себя ОДИНАКОВО в сторону осторожности."""

    def test_stale_and_unknown_act_the_same(self):
        stale = pf.judge(snap(), live({"H3": 0.0, "I3": 0.15, "J3": 0.25}), now=TODAY, max_age=14)
        unknown = pf.judge(snap(), live(None, ok=False, error="лист молчит"), now=TODAY, max_age=14)
        self.assertEqual(stale["state"], pf.STALE)
        self.assertEqual(unknown["state"], pf.UNKNOWN)
        for key in ("may_quote", "call_owner"):
            self.assertEqual(stale[key], unknown[key], key)
        a, b = pf.bot_action(stale), pf.bot_action(unknown)
        for key in ("name_price_to_client", "client_price", "call_owner", "quote"):
            self.assertEqual(a[key], b[key], key)

    def test_names_still_differ_so_the_owner_knows_what_to_fix(self):
        stale = pf.bot_action(pf.judge(snap(), live({"H3": 0.0, "I3": 0.15, "J3": 0.25}),
                                       now=TODAY, max_age=14))
        unknown = pf.bot_action(pf.judge(snap(), live(None, ok=False, error="лист молчит"),
                                         now=TODAY, max_age=14))
        self.assertNotEqual(stale["owner_card"], unknown["owner_card"])

    def test_proven_beats_unknown(self):
        """Одна ручка разошлась ТОЧНО, другая не снялась → УСТАРЕЛО, а не НЕИЗВЕСТНО."""
        v = pf.judge(snap(), live({"H3": 0.99, "I3": None, "J3": 0.25}), now=TODAY, max_age=14)
        self.assertEqual(v["state"], pf.STALE)
        self.assertEqual(v["unverified"], ["I3"])

    def test_garbage_verdict_is_not_a_permission(self):
        for bad in (None, {}, "СВЕЖЕЕ ЖЕ", {"state": "ЧТО-ТО"}, {"state": None}):
            self.assertFalse(pf.bot_action(bad)["name_price_to_client"], repr(bad))


class TestFresh(unittest.TestCase):
    def test_agreed_and_young_is_fresh(self):
        v = pf.judge(snap(), live(), now=TODAY, max_age=14)
        self.assertEqual(v["state"], pf.FRESH)
        self.assertTrue(v["may_quote"])
        self.assertFalse(v["call_owner"])
        action = pf.bot_action(v)
        self.assertTrue(action["name_price_to_client"])
        self.assertIsNone(action["owner_card"])

    def test_sheet_grew_a_handle_the_rule_never_saw(self):
        """В листе появилась четвёртая ручка — записанное правило описывает вчерашний лист."""
        more = dict(WANT)
        more["K3"] = 0.30
        v = pf.judge(snap(), live(more), now=TODAY, max_age=14)
        self.assertEqual(v["state"], pf.STALE)
        self.assertIn("K3", v["why"])


class TestThresholdKnob(unittest.TestCase):
    """Порог — ОДНА ручка; ноль на ней объявлен откатом возрастной ветки."""

    def test_default_is_fourteen_days(self):
        self.assertEqual(pf.MAX_AGE_DEFAULT, 14.0)
        self.assertEqual(pf.max_age_days({}), 14.0)
        self.assertEqual(pf.max_age_days(None), 14.0)

    def test_env_sets_it(self):
        self.assertEqual(pf.max_age_days({pf.MAX_AGE_ENV: "30"}), 30.0)
        self.assertEqual(pf.max_age_days({pf.MAX_AGE_ENV: "1,5"}), 1.5)

    def test_garbage_and_negative_fall_back_to_default(self):
        for raw in ("", "   ", "две недели", "-5", None, [], {}):
            self.assertEqual(pf.max_age_days({pf.MAX_AGE_ENV: raw}), 14.0, repr(raw))

    def test_zero_kills_the_age_branch_only(self):
        old = snap(taken="2020-01-01")
        self.assertEqual(pf.max_age_days({pf.MAX_AGE_ENV: "0"}), 0.0)
        dead = pf.judge(old, live(), now=TODAY, max_age=0)
        self.assertEqual(dead["state"], pf.FRESH)          # возраст больше не судится
        alive = pf.judge(old, live(), now=TODAY, max_age=14)
        self.assertEqual(alive["state"], pf.STALE)
        # ...но РУЧКИ судятся по-прежнему: откат гасит одну ветку, а не сторожа целиком.
        moved = pf.judge(old, live({"H3": 0.15, "I3": 0.15, "J3": -0.1}), now=TODAY, max_age=0)
        self.assertEqual(moved["state"], pf.STALE)


class TestSnapshotReader(unittest.TestCase):
    def test_missing_block_is_unknown_not_fresh(self):
        for doc in ({}, {"freshness": None}, {"freshness": {}}, {"freshness": {"handles": []}},
                    {"freshness": {"handles": "H3=0.15"}}, None, "мусор"):
            self.assertIsNone(pf.snapshot_of(doc), repr(doc))
            v = pf.judge(pf.snapshot_of(doc), live(), now=TODAY, max_age=14)
            self.assertEqual(v["state"], pf.UNKNOWN, repr(doc))
            self.assertFalse(pf.bot_action(v)["name_price_to_client"], repr(doc))

    def test_booleans_are_not_handle_values(self):
        s = pf.snapshot_of({"freshness": {"snapshot_on": TAKEN,
                                          "handles": [{"cell": "H3", "global_discount": True},
                                                      {"cell": "I3", "global_discount": 0.15}]}})
        self.assertEqual(sorted(s["handles"]), ["I3"])


class TestShippedRuleCarriesTheSnapshot(unittest.TestCase):
    """Требование владельца «файл обязан нести дату сборки и слепок» — проверяется ФАЙЛОМ."""

    def setUp(self):
        with io.open(os.path.join(HERE, "price_source.json"), encoding="utf-8") as f:
            self.doc = json.load(f)

    def test_rule_file_carries_date_and_three_handles(self):
        s = pf.snapshot_of(self.doc)
        self.assertIsNotNone(s, "в price_source.json нет годного блока freshness")
        self.assertIsInstance(s["taken"], datetime.date)
        self.assertEqual(sorted(s["handles"]), ["H3", "I3", "J3"])

    def test_shipped_snapshot_is_judgeable_against_itself(self):
        s = pf.snapshot_of(self.doc)
        v = pf.judge(s, live(dict(s["handles"])), now=s["taken"], max_age=14)
        self.assertEqual(v["state"], pf.FRESH)
        self.assertEqual(v["age_days"], 0.0)

    def test_threshold_default_matches_the_one_written_into_the_file(self):
        self.assertEqual(float(self.doc["freshness"]["max_age_days_default"]), pf.MAX_AGE_DEFAULT)
        self.assertEqual(self.doc["freshness"]["max_age_env"], pf.MAX_AGE_ENV)


class TestQuenchShapeMatchesTheLivePricePath(unittest.TestCase):
    """ЗАМОК КОПИИ: форма гашения у сторожа обязана совпасть с той, что путь ответа уже знает.

    Сторож не импортирует `price_source` в бою (иначе наблюдатель жил бы на наблюдаемом), и
    поэтому держит литерал КОПИЕЙ. Копия без замка расходится молча — замок здесь."""

    def test_quench_equals_price_source_dead(self):
        import price_source
        had, prev = price_source.ENV_FLAG in os.environ, os.environ.get(price_source.ENV_FLAG)
        path = price_source.PATH
        os.environ[price_source.ENV_FLAG] = "1"
        price_source.PATH = os.path.join(HERE, "нет-такого-файла-freshness.json")
        price_source._cache.update(key=None, doc=None)
        try:
            dead = price_source.reprice({"status": "ok", "quote": {"day_price": 1, "total": 1}},
                                        "NMAX 155", "2026-01-29", "2026-02-05", lambda s: s)
        finally:
            price_source.PATH = path
            price_source._cache.update(key=None, doc=None)
            if had:
                os.environ[price_source.ENV_FLAG] = prev
            else:
                del os.environ[price_source.ENV_FLAG]
        self.assertEqual(dead, pf.QUENCH)
        self.assertEqual(pf.bot_action({"state": pf.STALE, "why": "тест"})["quote"], dead)


class TestHands(unittest.TestCase):
    """Руки: отказ двери обязан приходить наверх ОТКАЗОМ, а не пустотой."""

    def _getter(self, table):
        def get(action, **kw):
            if action != "quote_price":
                return {"ok": False, "error": "unknown_action"}
            return table(kw.get("bike"))
        return get

    def test_live_read_maps_three_handles(self):
        by_cell = {b: c for c, bikes in pfr.PROBES for b in bikes}
        values = {"H3": 0.15, "I3": 0.15, "J3": 0.25}
        facts = pfr.live_handles(self._getter(
            lambda bike: {"ok": True, "season": {"global_discount": values[by_cell[bike]]}}))
        self.assertTrue(facts["ok"])
        self.assertEqual(facts["handles"], values)

    def test_door_refusal_is_an_outcome_not_an_empty_dict(self):
        facts = pfr.live_handles(self._getter(lambda bike: {"ok": False, "error": "нет доступа"}))
        self.assertFalse(facts["ok"])
        self.assertTrue(facts["error"])
        v = pf.judge(snap(), facts, now=TODAY, max_age=14)
        self.assertEqual(v["state"], pf.UNKNOWN)
        self.assertFalse(pf.bot_action(v)["name_price_to_client"])

    def test_door_exception_is_an_outcome_too(self):
        def boom(action, **kw):
            raise RuntimeError("сокет закрыт")
        facts = pfr.live_handles(boom)
        self.assertFalse(facts["ok"])
        self.assertEqual(pf.judge(snap(), facts, now=TODAY, max_age=14)["state"], pf.UNKNOWN)

    def test_units_of_one_category_disagreeing_means_not_a_handle(self):
        """Три юнита категории дали РАЗНОЕ — значит это не ручка. Ручка НЕ снята, а не «первая»."""
        seen = {"n": 0}

        def wobbly(bike):
            seen["n"] += 1
            return {"ok": True, "season": {"global_discount": 0.15 + seen["n"] / 100.0}}
        facts = pfr.live_handles(self._getter(wobbly))
        self.assertEqual(set(facts["handles"].values()), {None})
        self.assertFalse(facts["ok"])

    def test_reader_of_the_rule_says_why_it_failed(self):
        s, why = pfr.read_rule(os.path.join(HERE, "нет-такого-файла-freshness.json"))
        self.assertIsNone(s)
        self.assertTrue(why)
        s, why = pfr.read_rule()
        self.assertIsNotNone(s)
        self.assertIsNone(why)

    def test_report_uses_the_env_knob(self):
        s, _ = pfr.read_rule()
        old = dict(s)
        old["taken"] = datetime.date(2020, 1, 1)
        v, action = pfr.report(old, None, live(dict(s["handles"])), env={}, now=TODAY)
        self.assertEqual(v["state"], pf.STALE)
        self.assertFalse(action["name_price_to_client"])
        v, action = pfr.report(old, None, live(dict(s["handles"])),
                               env={pf.MAX_AGE_ENV: "0"}, now=TODAY)
        self.assertEqual(v["state"], pf.FRESH)


class TestPriceFreshPure(unittest.TestCase):
    """PRICE_FRESH_PURE — модуль решения не обзаводится диском, сетью и наблюдаемым."""

    def test_decision_module_has_no_io_and_no_observed_imports(self):
        with io.open(os.path.join(HERE, "price_freshness.py"), encoding="utf-8") as f:
            body = f.read()
        code = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
        for forbidden in ("import os", "import io", "import json", "import urllib",
                          "import requests", "import socket", "import subprocess",
                          "import price_source", "import suggest", "import pricing",
                          "import queue_snapshot_pc", "open(", "os.environ", "os.getenv"):
            self.assertNotIn(forbidden, code, "в сторож просочилось: %s" % forbidden)

    def test_judge_does_not_touch_the_process_environment(self):
        before = dict(os.environ)
        pf.judge(snap(), live(), now=TODAY, max_age=14)
        pf.judge(snap(taken="2020-01-01"), live(None, ok=False), now=TODAY, max_age=14)
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
