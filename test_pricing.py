# -*- coding: utf-8 -*-
"""
test_pricing.py — мок-тесты двухфазного ценового черновика. БЕЗ реального Telegram/
Anthropic/Bridge. Ключевой инвариант: FAQ-цена клиенту как финальная НЕ уходит; при
любой неясности (нет дат / нет котировки) — фолбэк без числа.
"""

import os
import asyncio
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

    def test_note_quote_ok_carries_figure(self):
        saved = pricing.quote
        pricing.quote = lambda *a, **k: {"day_price": 900, "total": 6300, "deposit": 7000, "available": True}
        try:
            note = suggest.build_pricing_note({"has_dates": True, "model": "NMAX",
                                               "date_start": "10.07", "date_end": "17.07"})
            self.assertIn("Календаря", note)
            self.assertIn("900", note)
            self.assertIn("6300", note)
        finally:
            pricing.quote = saved

    def test_note_quote_none_is_fallback_no_faq_number(self):
        saved = pricing.quote
        pricing.quote = lambda *a, **k: None
        try:
            note = suggest.build_pricing_note({"has_dates": True, "model": "NMAX",
                                               "date_start": "10.07", "date_end": "17.07"})
            self.assertIn("уточн", note.lower())
            # ни одной FAQ-ставки не просочилось
            for faq_price in ("449", "939", "998", "1185", "1798"):
                self.assertNotIn(faq_price, note)
        finally:
            pricing.quote = saved

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
                      suggest.bot_mode_active, pricing.quote)
        suggest.SUGGEST_MODE = True
        suggest.reset_disabled()
        suggest.MOD_GROUP_ID = -100777
        suggest.pending = suggest.PendingStore(os.path.join(self._tmp.name, "p.jsonl"))
        suggest.bot_mode_active = lambda: False   # reply-режим, черновик в группу
        pricing.quote = lambda *a, **k: None      # по умолчанию котировки нет

    def tearDown(self):
        (suggest.SUGGEST_MODE, suggest.MOD_GROUP_ID, suggest.pending,
         suggest.bot_mode_active, pricing.quote) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def _refllm(self, system, user):
        # маркеры, УНИКАЛЬНЫЕ для ноты (не пересекаются с текстом ПОЛИТИКИ, что всегда в промпте)
        if "дат аренды в диалоге НЕТ" in system:
            return "ASK_DATES"
        if "использовать ДОСЛОВНО" in system:
            return "HAS_PRICE"
        if "сейчас недоступна" in system:
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
        pricing.quote = lambda *a, **k: {"day_price": 900, "total": 6300, "deposit": 7000, "available": True}
        self.assertEqual(self._run("NMAX с 10 по 17, почём?"), "HAS_PRICE")

    def test_phase_b_quote_none_fallback(self):
        # pricing.quote == None (setUp) → фолбэк, без FAQ-числа
        self.assertEqual(self._run("NMAX с 10 по 17, почём?"), "FALLBACK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
