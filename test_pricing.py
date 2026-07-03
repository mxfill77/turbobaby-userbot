# -*- coding: utf-8 -*-
"""
test_pricing.py — мок-тесты двухфазного ценового черновика. БЕЗ реального Telegram/
Anthropic/Bridge. Ключевой инвариант: FAQ-цена клиенту как финальная НЕ уходит; при
любой неясности (нет дат / нет котировки) — фолбэк без числа.
"""

import os
import asyncio
import datetime
import tempfile
import unittest

import suggest
import pricing


# ------------------------------ моки Telegram --------------------------------

class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeHistMsg:
    def __init__(self, sender_id, message, date=None):
        self.sender_id = sender_id
        self.message = message
        self.date = date


class FakeSender:
    def __init__(self, uid=999, username="client1"):
        self.id = uid
        self.username = username


class FakeClient:
    def __init__(self, history):
        self.history = history
        self.sent = []

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text, reply_to=None):
        self.sent.append((chat, text))
        class M:  # noqa
            id = 4242
        return M()

    def iter_messages(self, entity, limit=50):
        async def gen():
            for m in self.history:
                yield m
        return gen()


# --------------------------------- pricing -----------------------------------

class TestPricingSkeleton(unittest.TestCase):
    def setUp(self):
        self._save = (pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN)

    def tearDown(self):
        pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN = self._save

    def test_none_when_action_empty(self):
        pricing.PRICING_ACTION = ""            # экшен не объявлен → штатный фолбэк
        self.assertIsNone(pricing.quote("NMAX", "10.07", "17.07"))

    def test_ok_quote_normalized(self):
        pricing.PRICING_ACTION = "quote_price"
        pricing.BRIDGE_URL = "https://x"; pricing.BRIDGE_TOKEN = "t"
        fake = lambda params: {"ok": True, "data": {"day_price": 900, "total": 6300,
                                                    "deposit": 7000, "available": True}}
        q = pricing.quote("NMAX", "10.07", "17.07", _get=fake)
        self.assertEqual(q["day_price"], 900)
        self.assertEqual(q["total"], 6300)
        self.assertTrue(q["available"])

    def test_non_ok_returns_none(self):
        pricing.PRICING_ACTION = "quote_price"; pricing.BRIDGE_URL = "https://x"; pricing.BRIDGE_TOKEN = "t"
        self.assertIsNone(pricing.quote("NMAX", "1", "2", _get=lambda p: {"ok": False}))

    def test_no_digits_returns_none(self):
        pricing.PRICING_ACTION = "quote_price"; pricing.BRIDGE_URL = "https://x"; pricing.BRIDGE_TOKEN = "t"
        self.assertIsNone(pricing.quote("NMAX", "1", "2", _get=lambda p: {"ok": True, "data": {}}))

    def test_exception_swallowed_returns_none(self):
        pricing.PRICING_ACTION = "quote_price"; pricing.BRIDGE_URL = "https://x"; pricing.BRIDGE_TOKEN = "t"
        def boom(p):
            raise RuntimeError("network down")
        self.assertIsNone(pricing.quote("NMAX", "1", "2", _get=boom))


# ---------------------------- парсер + нота ----------------------------------

class TestHintsAndNote(unittest.TestCase):
    def test_extract_dates_and_model(self):
        h = suggest.extract_booking_hints("[клиент]: NMAX с 10 по 17, сколько?")
        self.assertTrue(h["has_dates"])
        self.assertEqual(h["model"], "NMAX")

    def test_extract_numeric_dates(self):
        h = suggest.extract_booking_hints("[клиент]: хочу XMAX на 10.07-17.07")
        self.assertTrue(h["has_dates"])
        self.assertEqual(h["date_start"], "10.07")

    def test_extract_term_days(self):
        h = suggest.extract_booking_hints("[клиент]: ADV на 7 дней")
        self.assertTrue(h["has_dates"])
        self.assertEqual(h["term_days"], 7)

    def test_no_dates(self):
        h = suggest.extract_booking_hints("[клиент]: сколько стоит NMAX?")
        self.assertFalse(h["has_dates"])
        self.assertEqual(h["model"], "NMAX")

    def test_note_no_dates_asks_no_number(self):
        note = suggest.build_pricing_note({"has_dates": False})
        self.assertIn("дат", note.lower())
        self.assertIn("не называй", note.lower())

    def test_note_no_dates_forbids_any_price_form(self):
        # уточнение владельца: запрещена ЛЮБАЯ цена, включая «от X»/диапазон/«from»
        note = suggest.build_pricing_note({"has_dates": False}).lower()
        self.assertIn("никак", note)
        self.assertIn("от x", note)       # прямой запрет «от X»
        self.assertIn("диапазон", note)   # запрет диапазона
        self.assertIn("from", note)       # запрет англ. «from»

    def test_policy_forbids_any_price_without_calendar(self):
        p = suggest.make_system_prompt("FAQ", "ru").lower()
        self.assertIn("от x", p)          # политика прямо запрещает «от X»
        self.assertIn("никак", p)
        self.assertIn("from", p)

    _HINTS = {"has_dates": True, "model": "NMAX", "iso_start": "2026-07-10", "iso_end": "2026-07-17"}

    def _with_qfm(self, ret):
        saved = pricing.quote_for_model
        pricing.quote_for_model = lambda *a, **k: ret
        self.addCleanup(lambda: setattr(pricing, "quote_for_model", saved))

    def test_note_quote_ok_carries_figure(self):
        self._with_qfm({"status": "ok", "quote": {"day_price": 900, "total": 6300,
                                                  "deposit": 7000, "available": True}})
        note = suggest.build_pricing_note(dict(self._HINTS))
        self.assertIn("Календаря", note)
        self.assertIn("900", note)
        self.assertIn("6300", note)

    def test_note_error_is_fallback_no_faq_number(self):
        self._with_qfm({"status": "error", "quote": None})
        note = suggest.build_pricing_note(dict(self._HINTS))
        self.assertIn("уточн", note.lower())
        for faq_price in ("449", "939", "998", "1185", "1798"):
            self.assertNotIn(faq_price, note)

    def test_note_none_available_no_number(self):
        self._with_qfm({"status": "none_available", "quote": None})
        note = suggest.build_pricing_note(dict(self._HINTS)).lower()
        self.assertIn("заняты", note)
        for faq_price in ("449", "939", "998", "1185", "1798"):
            self.assertNotIn(faq_price, note)

    def test_note_unparsed_dates_fallback(self):
        note = suggest.build_pricing_note({"has_dates": True, "model": "NMAX",
                                           "iso_start": None, "iso_end": None})
        self.assertIn("не удалось", note.lower())
        for faq_price in ("449", "939", "998"):
            self.assertNotIn(faq_price, note)

    def test_prompt_has_policy_and_faq_relabeled(self):
        p = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА: тест")
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", p)
        self.assertIn("ОРИЕНТИР", p)           # FAQ-прайс переразмечен
        self.assertIn("ЦЕНА: тест", p)          # нота вклеена


# ------------------------- интеграция (три фазы) -----------------------------

class TestTwoPhaseDraft(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (suggest.SUGGEST_MODE, suggest.MOD_GROUP_ID, suggest.pending,
                      suggest.bot_mode_active, pricing.quote_for_model)
        suggest.SUGGEST_MODE = True
        suggest.reset_disabled()
        suggest.MOD_GROUP_ID = -100777
        suggest.pending = suggest.PendingStore(os.path.join(self._tmp.name, "p.jsonl"))
        suggest.bot_mode_active = lambda: False   # reply-режим, черновик в группу
        pricing.quote_for_model = lambda *a, **k: {"status": "error", "quote": None}  # по умолчанию котировки нет

    def tearDown(self):
        (suggest.SUGGEST_MODE, suggest.MOD_GROUP_ID, suggest.pending,
         suggest.bot_mode_active, pricing.quote_for_model) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def _refllm(self, system, user):
        # маркеры, УНИКАЛЬНЫЕ для ноты (не пересекаются с текстом ПОЛИТИКИ/СЦЕНАРИЯ в промпте)
        if "дат аренды в диалоге НЕТ" in system:
            return "ASK_DATES"
        if "использовать ДОСЛОВНО" in system:
            return "HAS_PRICE"
        if ("сейчас недоступна" in system or "все подходящие байки заняты" in system
                or "не удалось однозначно разобрать" in system):
            return "FALLBACK"
        return "OTHER"

    def _run(self, client_line):
        client = FakeClient([FakeHistMsg(42, "Здравствуйте! Что арендуем?"),
                             FakeHistMsg(999, client_line)])
        mid = asyncio.run(suggest.on_client_message(client, FakeSender(), 42,
                                                    call_llm=self._refllm, faq="FAQ"))
        return suggest.pending.get(mid)["draft"]

    def test_phase_a_no_dates_asks(self):
        self.assertEqual(self._run("Сколько стоит NMAX?"), "ASK_DATES")

    def test_phase_b_quote_ok_uses_figure(self):
        pricing.quote_for_model = lambda *a, **k: {"status": "ok", "quote": {
            "day_price": 900, "total": 6300, "deposit": 7000, "available": True}}
        self.assertEqual(self._run("NMAX 10.07-17.07 почём?"), "HAS_PRICE")

    def test_phase_b_quote_error_fallback(self):
        # quote_for_model → error (setUp) → фолбэк, без FAQ-числа
        self.assertEqual(self._run("NMAX 10.07-17.07"), "FALLBACK")


class TestFleetResolve(unittest.TestCase):
    def setUp(self):
        self._save = (pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN,
                      pricing._FLEET_CACHE["ts"], pricing._FLEET_CACHE["data"])
        pricing.PRICING_ACTION = "quote_price"
        pricing.BRIDGE_URL = "https://x"
        pricing.BRIDGE_TOKEN = "t"
        pricing._FLEET_CACHE["ts"] = 0.0
        pricing._FLEET_CACHE["data"] = None

    def tearDown(self):
        (pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN,
         pricing._FLEET_CACHE["ts"], pricing._FLEET_CACHE["data"]) = self._save

    def _factory(self, avail_by_bike):
        def getter(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": "NMAX 155 A 111"},
                                                       {"name": "NMAX 155 B 222"}]}}
            bike = params.get("bike", "")
            return {"ok": True, "day_price": 900, "total": 6300, "deposit": 7000,
                    "available": avail_by_bike.get(bike, False), "bike": bike}
        return getter

    def test_fleet_cache_ttl(self):
        calls = {"n": 0}
        def getter(params):
            calls["n"] += 1
            return {"ok": True, "data": {"bikes": [{"name": "NMAX 155 A 111"}]}}
        t0 = 1000.0
        pricing.fleet(_get=getter, _now=lambda: t0)
        pricing.fleet(_get=getter, _now=lambda: t0 + 10)       # в TTL → из кэша
        self.assertEqual(calls["n"], 1)
        pricing.fleet(_get=getter, _now=lambda: t0 + 100000)   # TTL истёк → новый вызов
        self.assertEqual(calls["n"], 2)

    def test_first_available_wins(self):
        g = self._factory({"NMAX 155 A 111": True, "NMAX 155 B 222": True})
        r = pricing.quote_for_model("NMAX", "2026-07-10", "2026-07-17", _get=g)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["quote"]["bike"], "NMAX 155 A 111")

    def test_second_available_when_first_busy(self):
        g = self._factory({"NMAX 155 A 111": False, "NMAX 155 B 222": True})
        r = pricing.quote_for_model("NMAX", "2026-07-10", "2026-07-17", _get=g)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["quote"]["bike"], "NMAX 155 B 222")

    def test_none_available_fallback(self):
        g = self._factory({"NMAX 155 A 111": False, "NMAX 155 B 222": False})
        r = pricing.quote_for_model("NMAX", "2026-07-10", "2026-07-17", _get=g)
        self.assertEqual(r["status"], "none_available")
        self.assertIsNone(r["quote"])

    def test_no_candidates(self):
        g = self._factory({})
        r = pricing.quote_for_model("XMAX", "2026-07-10", "2026-07-17", _get=g)  # XMAX нет в парке
        self.assertEqual(r["status"], "no_candidates")

    def test_fleet_unavailable_error(self):
        def g(params):
            if params.get("action") == "fleet":
                return {"ok": False}
            return {"ok": True, "available": True, "day_price": 1}
        r = pricing.quote_for_model("NMAX", "2026-07-10", "2026-07-17", _get=g)
        self.assertEqual(r["status"], "error")


class TestDateParse(unittest.TestCase):
    TODAY = datetime.date(2026, 7, 3)

    def test_numeric_range(self):
        self.assertEqual(suggest.parse_date_range("10.07-15.07", self.TODAY),
                         ("2026-07-10", "2026-07-15"))

    def test_s_po_shared_month(self):
        self.assertEqual(suggest.parse_date_range("с 5 по 10.07", self.TODAY),
                         ("2026-07-05", "2026-07-10"))

    def test_month_word_range(self):
        self.assertEqual(suggest.parse_date_range("5–10 июля", self.TODAY),
                         ("2026-07-05", "2026-07-10"))

    def test_past_month_rolls_next_year(self):
        self.assertEqual(suggest.parse_date_range("10.01-15.01", self.TODAY),
                         ("2027-01-10", "2027-01-15"))

    def test_hints_include_iso(self):
        h = suggest.extract_booking_hints("[клиент]: NMAX 10.07-17.07", today=self.TODAY)
        self.assertEqual(h["iso_start"], "2026-07-10")
        self.assertEqual(h["iso_end"], "2026-07-17")


class TestScenarioOrder(unittest.TestCase):
    def test_three_stages_in_order(self):
        p = suggest.make_system_prompt("FAQ", "ru")
        i1, i2, i3 = p.find("Этап 1"), p.find("Этап 2"), p.find("Этап 3")
        self.assertTrue(0 <= i1 < i2 < i3, (i1, i2, i3))

    def test_price_stage_asks_no_docs(self):
        p = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("На этом этапе НЕ проси паспорт", p)

    def test_stage2_free_pickup(self):
        p = suggest.make_system_prompt("FAQ", "ru").lower()
        self.assertIn("забор", p)
        self.assertIn("бесплат", p)

    def test_stage3_full_booking_request(self):
        p = suggest.make_system_prompt("FAQ", "ru").lower()
        for w in ("паспорт", "апартамент", "шлем", "телефон"):
            self.assertIn(w, p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
