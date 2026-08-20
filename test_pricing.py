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
        h = suggest.extract_booking_hints("[клиент]: хочу XMAX на 10.07-17.07",
                                          today=datetime.date(2026, 7, 3))
        self.assertTrue(h["has_dates"])
        self.assertEqual(h["iso_start"], "2026-07-10")   # date_start=iso_start после фикса

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

    _HINTS = {"has_dates": True, "model": "NMAX", "iso_start": "2026-07-10",
              "iso_end": "2026-07-17", "hint_days": 7, "monthly": False}
    # «Сегодня» ИНЪЕКТИРУЕМ (как в test_extract_numeric_dates): даты фикстуры обязаны быть в
    # БУДУЩЕМ относительно него, иначе гейт прошедшего старта (suggest.start_date_status) честно
    # перехватывает расчёт до Календаря — и тест ценовой ветки зависел бы от календаря на стене.
    _TODAY = datetime.date(2026, 7, 3)

    def _with_qfm(self, ret):
        saved = pricing.quote_for_model
        pricing.quote_for_model = lambda *a, **k: ret
        self.addCleanup(lambda: setattr(pricing, "quote_for_model", saved))

    def test_note_quote_ok_carries_figure(self):
        self._with_qfm({"status": "ok", "quote": {"day_price": 900, "total": 6300,
                                                  "deposit": 7000, "available": True, "days": 7}})
        note = suggest.build_pricing_note(dict(self._HINTS), today=self._TODAY)
        self.assertIn("Календаря", note)
        # ГОЛДЕН ПЕРЕСЧИТАН 21.08.2026 (источник цены — ЗАПИСАННОЕ ПРАВИЛО, 45a38cf): числа живой
        # котировки (900/6300) заменяет счёт по price_source.json. NMAX 155: база 298 x сезон 1.0
        # (P1 ИЮНЬ-СЕНТЯБРЬ, старт 2026-07-10) x ступень 1.0 (корзина 7-13, срок 7 сут) = 298 ฿/день;
        # итого 298 x 7 = 2086 ฿. Мок оставлен прежним НАРОЧНО — он доказывает, что число листа
        # действительно вытеснено правилом, а не совпало с ним.
        self.assertIn("298", note)
        self.assertIn("2086", note)

    def test_note_error_is_fallback_no_faq_number(self):
        self._with_qfm({"status": "error", "quote": None})
        note = suggest.build_pricing_note(dict(self._HINTS), today=self._TODAY)
        self.assertIn("уточн", note.lower())
        for faq_price in ("449", "939", "998", "1185", "1798"):
            self.assertNotIn(faq_price, note)

    def test_note_none_available_no_number(self):
        self._with_qfm({"status": "none_available", "quote": None})
        note = suggest.build_pricing_note(dict(self._HINTS), today=self._TODAY).lower()
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
        if "на место метки [QUOTE]" in system:
            return "HAS_PRICE"                 # маркерный quote-режим (вариант Б #274): цифр в промпте нет
        if "использовать ДОСЛОВНО" in system:
            return "HAS_PRICE"                 # фразовые ветки (несколько моделей / процент)
        if ("сейчас недоступна" in system or "все подходящие байки заняты" in system
                or "не удалось однозначно разобрать" in system or "не сходится" in system):
            return "FALLBACK"
        return "OTHER"

    # ЖИВОЙ путь on_client_message «сегодня» НЕ инъектирует (боевая дата) — значит даты в реплике
    # клиента обязаны быть в БУДУЩЕМ ОТНОСИТЕЛЬНО СЕГОДНЯ, а не прибитыми: с прибитой «10.07»
    # тест жил ровно до того дня, пока дата не утекла в прошлое, и дальше проверял бы уже гейт
    # прошедшего старта (переспрос дат), а не ту ценовую ветку, ради которой написан.
    def _ddmm(self, offset_days):
        d = suggest.today_phuket() + datetime.timedelta(days=offset_days)
        return f"{d.day:02d}.{d.month:02d}"

    def _range(self, start_offset, span_days):
        return f"{self._ddmm(start_offset)}-{self._ddmm(start_offset + span_days)}"

    def _run(self, client_line):
        client = FakeClient([FakeHistMsg(42, "Здравствуйте! Что арендуем?"),
                             FakeHistMsg(999, client_line)])
        mid = asyncio.run(suggest.on_client_message(client, FakeSender(), 42,
                                                    call_llm=self._refllm, faq="FAQ"))
        return suggest.pending.get(mid)["draft"]

    # §243/6: транскрипты несут модель+даты → в хвост черновика добавляется пометка
    # модератору «собрано: …»; проверяем маркер-ветку по началу строки.
    def test_phase_a_no_dates_asks(self):
        self.assertTrue(self._run("Сколько стоит NMAX?").startswith("ASK_DATES"))

    def test_phase_b_quote_ok_uses_figure(self):
        pricing.quote_for_model = lambda *a, **k: {"status": "ok", "quote": {
            "day_price": 900, "total": 6300, "deposit": 7000, "available": True, "days": 7}}
        self.assertTrue(self._run(f"NMAX {self._range(7, 7)} почём?").startswith("HAS_PRICE"))

    def test_phase_b_bad_days_sanity_fallback(self):
        # SANITY: quote days=360 при hint 5 дней → фолбэк, цифры НЕ уходят
        pricing.quote_for_model = lambda *a, **k: {"status": "ok", "quote": {
            "day_price": 219, "total": 78858, "deposit": 3000, "available": True, "days": 360}}
        draft = self._run(f"NMAX {self._range(7, 5)}")   # hint ~5 дней
        self.assertTrue(draft.startswith("FALLBACK"), draft)

    def test_phase_b_quote_error_fallback(self):
        # quote_for_model → error (setUp) → фолбэк, без FAQ-числа
        self.assertTrue(self._run(f"NMAX {self._range(7, 7)}").startswith("FALLBACK"))


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

    def _days(self, phrase):
        s, e = suggest.parse_date_range(phrase, self.TODAY)
        self.assertIsNotNone(s, phrase); self.assertIsNotNone(e, phrase)
        ds = datetime.date.fromisoformat(s); de = datetime.date.fromisoformat(e)
        return s, e, (de - ds).days

    def test_battery_durations(self):
        # каждый кейс → корректная длительность (days = разница дат, конвенция как у quote_price)
        self.assertEqual(self._days("с 5 по 10 июля")[2], 5)
        self.assertEqual(self._days("5–10 июля")[2], 5)
        self.assertEqual(self._days("с 5 по 10.07")[2], 5)
        self.assertEqual(self._days("10.07-15.07")[2], 5)
        self.assertEqual(self._days("на неделю с 5 июля")[2], 7)
        self.assertEqual(self._days("завтра на 3 дня")[2], 3)
        self.assertEqual(self._days("на месяц с 5 июля")[2], 30)

    def test_year_roll_dec_jan(self):
        s, e, d = self._days("с 28 декабря по 3 января")
        self.assertEqual((s, e, d), ("2026-12-28", "2027-01-03", 6))   # легитимный переход через год

    def test_swapped_order_not_360(self):
        # КОРЕНЬ БАГА: перепутанный порядок НЕ должен давать ~360 дней
        s, e, d = self._days("с 10 по 5 июля")
        self.assertEqual(d, 5)
        self.assertLess(d, 30)
        self.assertEqual((s, e), ("2026-07-05", "2026-07-10"))

    def test_hints_iso_and_hintdays(self):
        h = suggest.extract_booking_hints("[клиент]: NMAX 10.07-17.07", today=self.TODAY)
        self.assertEqual(h["iso_start"], "2026-07-10")
        self.assertEqual(h["iso_end"], "2026-07-17")
        self.assertEqual(h["hint_days"], 7)


class TestSanityGuard(unittest.TestCase):
    def test_matching_days_ok(self):
        self.assertTrue(pricing.sanity_days_ok(5, 5))
        self.assertTrue(pricing.sanity_days_ok(6, 5))          # расхождение 1 — ок

    def test_mismatch_blocked(self):
        self.assertFalse(pricing.sanity_days_ok(7, 5))         # расхождение 2 — режем
        self.assertFalse(pricing.sanity_days_ok(360, 5))       # дикое расхождение

    def test_over_45_without_monthly_blocked(self):
        self.assertFalse(pricing.sanity_days_ok(60, None))     # >45 без месячного → режем
        self.assertTrue(pricing.sanity_days_ok(30, 30, monthly=True))

    def test_invalid_days_blocked(self):
        self.assertFalse(pricing.sanity_days_ok(None, 5))
        self.assertFalse(pricing.sanity_days_ok(0, 5))

    def test_note_wild_days_no_number(self):
        # build_pricing_note: quote days=360 при hint 5 → фолбэк, ни одной цифры цены
        saved = pricing.quote_for_model
        pricing.quote_for_model = lambda *a, **k: {"status": "ok", "quote": {
            "day_price": 219, "total": 78858, "deposit": 3000, "available": True, "days": 360}}
        try:
            note = suggest.build_pricing_note({"has_dates": True, "model": "NMAX",
                                               "iso_start": "2026-07-05", "iso_end": "2026-07-10",
                                               "hint_days": 5, "monthly": False})
            for n in ("219", "78858", "78 858", "3000"):
                self.assertNotIn(n, note)
            self.assertIn("уточн", note.lower())
        finally:
            pricing.quote_for_model = saved


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


class TestLastBookingWindow(unittest.TestCase):
    """Корневой фикс: модель+даты из ПОСЛЕДНЕЙ релевантной брони, не из всего диалога."""
    TODAY = datetime.date(2026, 7, 3)

    def _tr(self, *client_msgs):
        return "\n".join("[клиент]: " + m for m in client_msgs)

    def _h(self, *msgs):
        return suggest.extract_booking_hints(self._tr(*msgs), today=self.TODAY)

    def test_two_bookings_last_nmax_wins(self):
        h = self._h("ADV 350 с 28 декабря по 3 января", "NMAX с 5 по 10 июля")
        self.assertEqual(h["model"], "NMAX")
        self.assertEqual((h["iso_start"], h["iso_end"]), ("2026-07-05", "2026-07-10"))
        self.assertEqual(h["hint_days"], 5)          # чужая бронь (дек-янв) НЕ подмешана

    def test_two_bookings_last_adv_wins(self):
        h = self._h("NMAX с 5 по 10 июля", "ADV 350 с 28 декабря по 3 января")
        self.assertEqual(h["model"], "ADV350")
        self.assertEqual((h["iso_start"], h["iso_end"]), ("2026-12-28", "2027-01-03"))
        self.assertEqual(h["hint_days"], 6)

    def test_changed_dates_new_wins(self):
        h = self._h("NMAX с 5 по 10 июля", "давай с 12 по 18 июля")
        self.assertEqual(h["model"], "NMAX")         # модель из той же брони
        self.assertEqual((h["iso_start"], h["iso_end"]), ("2026-07-12", "2026-07-18"))
        self.assertEqual(h["hint_days"], 6)          # новые даты, старые сброшены

    def test_window_assembly_model_last_dates_prev(self):
        h = self._h("с 5 по 10 июля", "NMAX")        # даты в предыдущей, модель в последней
        self.assertEqual(h["model"], "NMAX")
        self.assertEqual((h["iso_start"], h["iso_end"]), ("2026-07-05", "2026-07-10"))
        self.assertEqual(h["hint_days"], 5)

    def test_monthly_only_explicit(self):
        h1 = self._h("на месяц с 5 июля")
        self.assertTrue(h1["monthly"]); self.assertEqual(h1["term_days"], 30)
        h2 = self._h("с 5 июля по 20 августа")       # 46 дней, но БЕЗ слова «месяц»
        self.assertFalse(h2["monthly"]); self.assertGreater(h2["hint_days"], 45)

    def test_real_samhold_transcript_last_booking(self):
        # реальный мульти-темный диалог из розыска — должна взяться ПОСЛЕДНЯЯ бронь (NMAX июль)
        h = self._h("Привет! Есть NMAX на 5 дней?", "С 5ого по 10 июля",
                    "NMAX с 5 по 10 июля", "ADV 350 с 28 декабря по 3 января",
                    "Хорошо", "NMAX с 5 по 10 июля")
        self.assertEqual(h["model"], "NMAX")
        self.assertEqual((h["iso_start"], h["iso_end"]), ("2026-07-05", "2026-07-10"))
        self.assertEqual(h["hint_days"], 5)          # НЕ 0 и НЕ 47 (чужая дек-янв не подмешана)


class TestQuizFourCases(unittest.TestCase):
    """Доводка ценовой викторины: 4 боевые фразы (с моделью) через parse_date_range +
    extract_booking_hints на today=2026-07-05. Замок против дрейфа год-ролла/swap/monthly."""
    TODAY = datetime.date(2026, 7, 5)

    def _pd(self, phrase):
        s, e = suggest.parse_date_range(phrase, self.TODAY)
        self.assertIsNotNone(s, phrase); self.assertIsNotNone(e, phrase)
        ds = datetime.date.fromisoformat(s); de = datetime.date.fromisoformat(e)
        return s, e, (de - ds).days

    def _h(self, phrase):
        return suggest.extract_booking_hints("[клиент]: " + phrase, today=self.TODAY)

    def test_case1_adv350_year_roll_6_days(self):
        s, e, d = self._pd("ADV 350 с 28 декабря по 3 января")
        self.assertEqual((s, e, d), ("2026-12-28", "2027-01-03", 6))   # легитимный переход через год
        h = self._h("ADV 350 с 28 декабря по 3 января")
        self.assertEqual(h["model"], "ADV350")
        self.assertEqual(h["hint_days"], 6)
        self.assertFalse(h["monthly"])

    def test_case2_nmax_tomorrow_3_days(self):
        s, e, d = self._pd("NMAX завтра на 3 дня")
        self.assertEqual((s, e, d), ("2026-07-06", "2026-07-09", 3))   # 3 дня от завтра
        h = self._h("NMAX завтра на 3 дня")
        self.assertEqual(h["model"], "NMAX")
        self.assertEqual(h["hint_days"], 3)
        self.assertFalse(h["monthly"])

    def test_case3_nmax_swapped_5_days_not_360(self):
        s, e, d = self._pd("NMAX с 10 по 5 июля")
        self.assertEqual((s, e), ("2026-07-05", "2026-07-10"))         # swap, НЕ 360
        self.assertEqual(d, 5)
        self.assertLess(d, 30)
        h = self._h("NMAX с 10 по 5 июля")
        self.assertEqual(h["hint_days"], 5)
        self.assertFalse(h["monthly"])

    def test_case4_nmax_monthly_only_explicit_word(self):
        s, e, d = self._pd("NMAX на месяц с 5 июля")
        self.assertEqual((s, e, d), ("2026-07-05", "2026-08-04", 30))  # monthly по явному слову
        h = self._h("NMAX на месяц с 5 июля")
        self.assertEqual(h["model"], "NMAX")
        self.assertEqual(h["term_days"], 30)
        self.assertTrue(h["monthly"])                                   # «месяц» сказано явно


class TestSanityStrengthened(unittest.TestCase):
    def test_long_range_no_month_word_blocked(self):
        # ~47 дней БЕЗ явного «месяц» → sanity режет (auto-monthly убран)
        self.assertFalse(pricing.sanity_days_ok(47, 47, monthly=False))
        self.assertFalse(pricing.sanity_days_ok(47, None, monthly=False))

    def test_note_garbage_range_no_number(self):
        saved = pricing.quote_for_model
        pricing.quote_for_model = lambda *a, **k: {"status": "ok", "quote": {
            "day_price": 219, "total": 10293, "deposit": 3000, "available": True, "days": 47}}
        try:
            note = suggest.build_pricing_note({"has_dates": True, "model": "NMAX",
                                               "iso_start": "2026-07-05", "iso_end": "2026-08-21",
                                               "hint_days": 47, "monthly": False})
            for n in ("219", "10293", "10 293", "3000"):
                self.assertNotIn(n, note)
            self.assertIn("уточн", note.lower())     # инвариант: мусорный диапазон → фолбэк
        finally:
            pricing.quote_for_model = saved


class TestPriceRulesV2(unittest.TestCase):
    """Правила цен v2: (1) кап низкого сезона, (2) минимальный срок, (3) несколько моделей,
    (4) депозит при нескольких байках, (5) J-текст quote дословно."""
    TODAY = datetime.date(2026, 7, 5)

    def _with_qfm(self, fn):
        saved = pricing.quote_for_model
        pricing.quote_for_model = fn
        self.addCleanup(lambda: setattr(pricing, "quote_for_model", saved))

    def _h(self, phrase):
        return suggest.extract_booking_hints("[клиент]: " + phrase, today=self.TODAY)

    # --- (1) кап низкого сезона ---------------------------------------------
    def test_cap_active_replaces_j_price_with_low_season(self):
        # total (за месяц) > cap_price → «аренда от <cap> ฿/мес — предложение низкого сезона»
        self._with_qfm(lambda *a, **k: {"status": "ok", "quote": {
            "text": "30000 ฿ за месяц", "total": 30000, "cap_active": True, "cap_price": 15000,
            "deposit": 7000, "available": True, "days": 30}})
        note = suggest.build_pricing_note(self._h("NMAX на месяц с 5 июля"))
        self.assertIn("аренда от 15000 ฿/мес", note)
        self.assertIn("низкого сезона", note)
        self.assertIn("депозит 7000", note)     # депозит/наличие как обычно
        self.assertIn("свободен", note)
        self.assertNotIn("30000", note)          # J-цена НЕ уходит клиенту

    def test_cap_inactive_uses_j_text(self):
        # cap_active=False → обычная J-цена (поле text) дословно, не кап-фраза
        self._with_qfm(lambda *a, **k: {"status": "ok", "quote": {
            "text": "6300 ฿ за 7 дней, депозит 7000 ฿", "total": 6300, "cap_active": False,
            "cap_price": 15000, "available": True, "days": 7}})
        note = suggest.build_pricing_note(self._h("NMAX 10.07-17.07"))
        self.assertIn("6300 ฿ за 7 дней", note)
        self.assertNotIn("низкого сезона", note)

    def test_cap_active_but_total_below_cap_no_low_season(self):
        # кап активен, но total < cap_price → берём J-цену, без кап-фразы
        self._with_qfm(lambda *a, **k: {"status": "ok", "quote": {
            "text": "6300 ฿", "total": 6300, "cap_active": True, "cap_price": 15000,
            "available": True, "days": 7}})
        note = suggest.build_pricing_note(self._h("NMAX 10.07-17.07"))
        self.assertNotIn("низкого сезона", note)
        # ГОЛДЕН ПЕРЕСЧИТАН 21.08.2026: NMAX 155 = 298 x 1.0 (P1, старт 2026-07-10) x 1.0 (7-13,
        # 7 сут) = 298 ฿/день; итого 2086 ฿. Посылка теста ЦЕЛА: кепка файла для NMAX 155 = 5000 ฿,
        # итог 2086 < 5000 — «кап активен, но сумма ниже потолка» проверяется по-прежнему.
        self.assertIn("2086 ฿", note)

    # --- (2) минимальный срок ------------------------------------------------
    def test_bike_class_min_days(self):
        self.assertEqual(suggest.bike_class("NMAX")[1], 5)        # скутер → 5
        self.assertEqual(suggest.bike_class("PCX")[1], 5)
        self.assertEqual(suggest.bike_class("ADV350")[1], 5)
        self.assertEqual(suggest.bike_class("XSR")[1], 5)         # XSR155 → 5 (как скутер)
        self.assertEqual(suggest.bike_class("CB300")[1], 3)      # мотоцикл → 3
        self.assertEqual(suggest.bike_class("NINJA")[1], 3)
        self.assertEqual(suggest.bike_class("XSR900")[1], 3)     # большой мотоцикл → 3
        self.assertIsNone(suggest.bike_class("CLICK"))

    def test_scooter_below_min_offers_5_days(self):
        # скутер на 3 дня (min 5) → «скутеры сдаём от 5 дней» + цена на минимум
        self._with_qfm(lambda m, ds, de, *a, **k: {"status": "ok", "quote": {
            "text": "4500 ฿ за 5 дней", "total": 4500, "available": True, "days": 5}})
        note = suggest.build_pricing_note(self._h("NMAX завтра на 3 дня"))
        self.assertIn("скутеры сдаём от 5 дней", note)
        # ГОЛДЕН ПЕРЕСЧИТАН 21.08.2026: цена на МИНИМАЛЬНЫЙ срок считается правилом —
        # NMAX 155 = 298 x 1.0 (P1, старт 2026-07-06) x 1.03 (корзина 4-6, срок 5 сут) = 307 ฿/день;
        # итого 307 x 5 = 1535 ฿. Предмет теста прежний: минимум назван И оценён.
        self.assertIn("за 5 дн: 307 ฿/день; итого 1535 ฿", note)

    def test_moto_below_min_offers_3_days(self):
        self._with_qfm(lambda m, ds, de, *a, **k: {"status": "ok", "quote": {
            "text": "3000 ฿ за 3 дня", "total": 3000, "available": True, "days": 3}})
        note = suggest.build_pricing_note(self._h("CB300 завтра на 2 дня"))
        self.assertIn("мотоциклы сдаём от 3 дней", note)
        # ГОЛДЕН ПЕРЕСЧИТАН 21.08.2026: CB 300 = 637 x 1.0 (P1, старт 2026-07-06) x 0.97
        # (корзина 1-3, срок 3 сут) = 618 ฿/день; итого 618 x 3 = 1854 ฿.
        self.assertIn("за 3 дн: 618 ฿/день; итого 1854 ฿", note)

    def test_xsr155_below_min_offers_5_days(self):
        self._with_qfm(lambda m, ds, de, *a, **k: {"status": "ok", "quote": {
            "text": "5000 ฿ за 5 дней", "total": 5000, "available": True, "days": 5}})
        note = suggest.build_pricing_note(self._h("XSR завтра на 4 дня"))
        self.assertIn("от 5 дней", note)

    def test_at_min_days_no_min_message(self):
        # ровно минимум (скутер 5 дней) → обычная цена, без «сдаём от»
        self._with_qfm(lambda *a, **k: {"status": "ok", "quote": {
            "text": "4500 ฿", "total": 4500, "available": True, "days": 5}})
        note = suggest.build_pricing_note(self._h("NMAX с 5 по 10 июля"))
        self.assertNotIn("сдаём от", note)
        # ГОЛДЕН ПЕРЕСЧИТАН 21.08.2026: NMAX 155 = 298 x 1.0 (P1, старт 2026-07-05) x 1.03
        # (корзина 4-6, срок 5 сут) = 307 ฿/день; итого 307 x 5 = 1535 ฿.
        self.assertIn("1535 ฿", note)

    # --- (3) несколько моделей ----------------------------------------------
    def test_detect_multiple_models(self):
        ms = suggest._detect_models("nmax и pcx на 10.07-17.07")
        self.assertIn("NMAX", ms)
        self.assertIn("PCX", ms)
        self.assertEqual(len(ms), 2)

    def test_detect_models_no_false_split_adv350(self):
        # «ADV 350» не должно расщепляться на ADV + ADV350
        self.assertEqual(suggest._detect_models("adv 350 на 10.07-17.07"), ["ADV350"])

    def test_multi_model_separate_prices(self):
        prices = {"NMAX": "6300 ฿ NMAX", "PCX": "5600 ฿ PCX"}
        self._with_qfm(lambda m, ds, de, *a, **k: {"status": "ok", "quote": {
            "text": prices.get(suggest.bike_class(m) and m, "?"), "total": 6300,
            "available": True, "days": 7}})
        h = self._h("NMAX и PCX на 10.07-17.07")
        self.assertEqual(len(h["models"]), 2)
        note = suggest.build_pricing_note(h)
        self.assertIn("6300 ฿ NMAX", note)
        self.assertIn("5600 ฿ PCX", note)
        self.assertIn("- NMAX:", note)
        self.assertIn("- PCX:", note)
        self.assertIn("отдельной строкой", note)

    # --- (4) депозит при нескольких байках -----------------------------------
    def test_deposit_multi_question_detected(self):
        h = self._h("Беру NMAX и PCX, можно депозит поменьше на два байка?")
        self.assertTrue(h["deposit_multi_q"])
        note = suggest.build_pricing_note(h)
        self.assertIn("уточню у менеджера", note)

    def test_deposit_single_bike_not_triggered(self):
        h = self._h("NMAX 10.07-17.07, какой депозит?")
        self.assertFalse(h["deposit_multi_q"])

    def test_policy_forbids_self_deposit_reduction(self):
        p = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("уточню у менеджера", p)
        self.assertIn("депозит", p.lower())

    # --- (5) J-текст дословно ------------------------------------------------
    def test_ok_uses_quote_text_verbatim(self):
        # поле text из quote уходит клиенту дословно; day_price игнорируется
        self._with_qfm(lambda *a, **k: {"status": "ok", "quote": {
            "text": "Ровно так: 900 ฿/день, 6300 ฿ за неделю", "day_price": 111, "total": 6300,
            "available": True, "days": 7}})
        note = suggest.build_pricing_note(self._h("NMAX 10.07-17.07"))
        self.assertIn("Ровно так: 900 ฿/день, 6300 ฿ за неделю", note)
        self.assertIn("ДОСЛОВНО", note)
        self.assertNotIn("111", note)            # day_price не подмешан

    def test_ok_without_text_falls_back_to_assembly(self):
        # обратная совместимость: нет поля text → сборка из чисел
        self._with_qfm(lambda *a, **k: {"status": "ok", "quote": {
            "day_price": 900, "total": 6300, "deposit": 7000, "available": True, "days": 7}})
        note = suggest.build_pricing_note(self._h("NMAX 10.07-17.07"))
        # ГОЛДЕН ПЕРЕСЧИТАН 21.08.2026: сборка из чисел цела, но числа теперь ПРАВИЛА —
        # NMAX 155 = 298 x 1.0 (P1, старт 2026-07-10) x 1.0 (7-13, 7 сут) = 298 ฿/день; итого 2086 ฿.
        self.assertIn("298", note)
        self.assertIn("2086", note)


class TestClass0ModelResolveAndTerm(unittest.TestCase):
    """Класс-0 (родитель #253): «XSR 155» не резолвился → цена-нот проваливалась в «уточни модель/
    даты», и LLM подставлял ЧУЖУЮ карточку парка (в живом провале — MT-03/≈5166฿).
    Корни: (1) серийный корень «XSR» матчил все XSR-юниты; (2) «с 20 на 2 недели» не давал старт
    (нет голого-дня в _anchor_date) → нечего квотировать. Голдены на ДОСЛОВНЫХ фразах клиента."""
    TODAY = datetime.date(2026, 7, 13)

    # Реальные имена Лист1 (Bridge отдаёт с кириллич. «СС»): XSR155 и «чужой» MT-03 в одном парке.
    FLEET = ["XSR 155СС BLACK PHUKET 8949", "XSR 155СС GREEN",
             "MT-03 300СС BLUE PHUKET 5068", "NMAX 155СС BLACK 8952", "ADV 350СС RED 9890"]

    def setUp(self):
        pricing._FLEET_CACHE.update(ts=0.0, data=None)
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        self._pa, self._bu, self._bt = pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN
        pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN = "quote_price", "https://x", "t"

        def _getter(params, fleet=None):
            names = self.FLEET if fleet is None else fleet
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in names]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            per = 927 if bike.upper().startswith("MT") else 472   # MT-03 дороже — чтобы подмена была видна
            return {"ok": True, "data": {"day_price": per, "total": per * days, "deposit": 7000,
                                         "available": True, "days": days,
                                         "text": f"{per} ฿/день, {per * days} ฿ за {days} дн"}}
        self.getter = _getter

    def tearDown(self):
        pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN = self._pa, self._bu, self._bt
        pricing._FLEET_CACHE.update(ts=0.0, data=None)

    def _note(self, phrase, fleet=None):
        h = suggest.extract_booking_hints("[клиент]: " + phrase, today=self.TODAY)
        pricing._FLEET_CACHE.update(ts=0.0, data=None)
        g = (lambda p: self.getter(p, fleet=fleet)) if fleet is not None else self.getter
        return suggest.build_pricing_note(h, lang="ru", getter=g, today=self.TODAY)

    # --- голый день без месяца: «с 20 на 2 недели» = 20 → +14 (старт из трекера) --------------
    def test_bare_day_start_plus_duration(self):
        self.assertEqual(suggest.parse_date_range("с 20 на 2 недели", self.TODAY),
                         ("2026-07-20", "2026-08-03"))
        # парафразы дословного вида «с ЧИСЛО + длительность» без месяца
        for phrase, exp in (("хочу с 20 на 2 недели", ("2026-07-20", "2026-08-03")),
                            ("с 20 на 14 дней", ("2026-07-20", "2026-08-03")),
                            ("возьму с 20 на неделю", ("2026-07-20", "2026-07-27")),
                            ("с 20 на 10 дней", ("2026-07-20", "2026-07-30"))):
            self.assertEqual(suggest.parse_date_range(phrase, self.TODAY), exp, phrase)
        # прошедший день → следующий месяц (не следующий год, не текущий прошедший)
        self.assertEqual(suggest.parse_date_range("с 5 на неделю", self.TODAY),
                         ("2026-08-05", "2026-08-12"))

    def test_bare_day_negatives(self):
        # диапазон «с 20 по 25» — НЕ старт+срок (нет длительности) → без даты
        self.assertEqual(suggest.parse_date_range("с 20 по 25", self.TODAY), (None, None))
        # «с 20 июля …» и «с 20.07 …» идут прежними ветками (месяц явный) — регресс не задет
        self.assertEqual(suggest.parse_date_range("с 20 июля на 2 недели", self.TODAY),
                         ("2026-07-20", "2026-08-03"))
        self.assertEqual(suggest.parse_date_range("с 20.07 на 2 недели", self.TODAY),
                         ("2026-07-20", "2026-08-03"))

    # --- детерминированный резолв модели по Лист1/_bike_key + алиасы ---------------------------
    def test_resolve_xsr_aliases_to_park_model(self):
        for canon in ("XSR", "XSR 155", "xsr155", "xsr 155"):
            st, disp, key = suggest.resolve_park_model(canon, getter=self.getter)
            pricing._FLEET_CACHE.update(ts=0.0, data=None)
            self.assertEqual((st, disp, key), ("ok", "XSR 155", "xsr155"), canon)

    def test_resolve_ambiguous_series_asks(self):
        fleet = self.FLEET + ["XSR 900СС GREY 7943"]
        st, disp, key = suggest.resolve_park_model("XSR", getter=lambda p: self.getter(p, fleet=fleet))
        self.assertEqual(st, "ambiguous")
        self.assertEqual(sorted(disp), ["XSR 155", "XSR 900"])
        self.assertIsNone(key)

    def test_resolve_absent_or_unknown_keeps_old_path(self):
        # серия не представлена в парке (PCX нет среди FLEET) → unknown (прежний путь, не подстановка)
        self.assertEqual(suggest.resolve_park_model("PCX", getter=self.getter)[0], "unknown")
        pricing._FLEET_CACHE.update(ts=0.0, data=None)
        # пустой/чужой canon → unknown (fail-safe, исходную строку вызывающий оставит как есть)
        self.assertEqual(suggest.resolve_park_model("", getter=self.getter)[0], "unknown")

    # --- ДОСЛОВНЫЙ живой провал: XSR 155 + «с 20 на 2 недели» → XSR155-цена, БЕЗ ПОДМЕНЫ --------
    # ПОПРАВКА 20.08.2026 (правило владельца «подбор по классу»): предмет голдена сузился с «MT-03
    # в ответе быть НЕ ДОЛЖНО» до «MT-03 не смеет стоять в КАРТОЧКЕ XSR». Причина названа: MT-03 —
    # тот же класс (мотоциклы), и владелец прямо велел называть свободные модели того же класса
    # дополнительно. Живой провал, ради которого голден заводился, был про ПОДМЕНУ (цена чужого
    # юнита выдавалась за цену спрошенного), и именно она здесь и проверяется — построчно.
    # Даром это не досталось: NMAX 155 и ADV 350 в том же парке свободны и в ответ НЕ попали —
    # они классом НИЖЕ, и это второй замок правила, проверяемый тем же голденом.
    def _quote_lines(self, note):
        """Строки клиентского блока (то, что КОД вставит на место [QUOTE]); [] — блока нет."""
        block = suggest._quote_block_from_note(note) or ""
        return [ln for ln in block.split("\n") if ln.strip()]

    def test_live_xsr155_bare_day_gives_xsr_price_not_mt03(self):
        phrases = [
            "Здравствуйте! Интересует XSR 155, можно с 20 на 2 недели?",   # дословная фраза провала
            "хочу xsr155 с 20 на 2 недели",
            "Можно взять XSR 155 с 20 на две недели?",
            "интересует иксэсэр 155, беру с 20 на 14 дней",                # без модели-латиницы не резолвим — оставим латиницу
            "XSR 155 rental from 20 for 2 weeks",
        ]
        for ph in phrases:
            note = self._note(ph)
            lines = self._quote_lines(note)
            if not lines:
                # расчёта нет вовсе → чужой карточке взяться неоткуда (прежняя проверка целиком)
                self.assertNotIn("mt-03", note.lower(), f"чужая карточка MT-03 в ответе на: {ph}\n{note}")
                self.assertNotIn("593", note, f"чужая цена MT-03 в ответе на: {ph}\n{note}")
                continue
            self.assertTrue(lines[0].upper().startswith("XSR"),
                            f"спрошенная модель не первая на: {ph}\n{note}")
            # ГОЛДЕНЫ ПЕРЕСЧИТАНЫ 21.08.2026 по ЗАПИСАННОМУ ПРАВИЛУ (источник цены с 45a38cf):
            #   XSR 155   = 444 x 1.0 (P1, старт 2026-07-20) x 0.82 (корзина 14-29, 14 сут) = 364 ฿/день;
            #   MT-03 300 = 723 x 1.0 x 0.82 = 593 ฿/день.
            # Мок Bridge (472/927) НЕ трогали: он и есть «цена листа», которую правило вытесняет.
            # Число соседа обновлено ВМЕСТЕ со своим — иначе проверка подмены стала бы холостой
            # (927 не может появиться нигде, и тест перестал бы ловить свой класс).
            self.assertIn("364", lines[0], ph)                  # цена XSR155 — своя
            self.assertNotIn("593", lines[0], f"чужой тариф MT-03 в карточке XSR на: {ph}")
            for ln in lines[1:]:                                # дополнительные — только тот же класс
                self.assertTrue(ln.upper().startswith("MT-03"), f"чужой класс в подборе: {ln}")
                self.assertNotIn("364", ln, "цена XSR подставлена в карточку MT-03")
            self.assertNotIn("NMAX", note.upper(), "модель классом НИЖЕ предложена сама собой")
            self.assertNotIn("ADV", note.upper(), "модель классом НИЖЕ предложена сама собой")

    def test_live_xsr155_resolved_quote_present(self):
        # ядро: дословная фраза даёт ДЕТЕРМИНИРОВАННУЮ цену XSR155 из Календаря (не вакуум-фолбэк)
        note = self._note("Здравствуйте! Интересует XSR 155, можно с 20 на 2 недели?")
        # ГОЛДЕН ПЕРЕСЧИТАН 21.08.2026: 444 x 1.0 (P1) x 0.82 (14-29) = 364 ฿/день (см. соседний тест).
        self.assertIn("364", note)
        self.assertNotIn("не удалось", note.lower())    # не свалились в «уточни модель/даты»
        lines = self._quote_lines(note)
        self.assertIn("364", lines[0])                  # своя цена в СВОЕЙ карточке (первой)
        self.assertNotIn("593", lines[0])               # подмены чужой карточкой нет

    def test_ambiguous_series_note_asks_not_substitutes(self):
        # в парке XSR155 и XSR900 → на голый «XSR» просим уточнить, число и чужую модель НЕ даём
        note = self._note("Интересует XSR с 20 на 2 недели",
                          fleet=self.FLEET + ["XSR 900СС GREY 7943"])
        low = note.lower()
        self.assertIn("уточни", low)
        self.assertIn("xsr 155", low)
        self.assertIn("xsr 900", low)
        # числа правила (а не мока листа) — иначе проверка «числа нет» стала бы холостой
        self.assertNotIn("364", note)                   # никакого числа при неоднозначности
        self.assertNotIn("593", note)


class TestStep2ModelTermQuoteDepositPercent(unittest.TestCase):
    """Шаг 2/7 (родитель #253): названы модель+срок → черновик ОБЯЗАН нести живой quote ИМЕННО
    этой модели на этот срок С ЕЁ ДЕПОЗИТОМ; карточка чужой модели блокируется пост-чеком; вопрос
    «сколько будет N%» → процент от суммы ЭТОГО расчёта (код, не LLM). Голдены — ДОСЛОВНЫЕ фразы
    клиента + парафразы (правило-класс CLAUDE.md). Bridge замокан: XSR155 (472฿/день, депозит 7000,
    text БЕЗ депозита — проверяем, что депозит дописывает КОД) и чужой MT-03 (927฿/день, депозит
    15000) в одном парке; XSR900 в парк НЕ кладём (иначе серия неоднозначна)."""
    TODAY = datetime.date(2026, 7, 13)
    FLEET = ["XSR 155СС BLACK PHUKET 8949", "XSR 155СС GREEN",
             "MT-03 300СС BLUE PHUKET 5068", "NMAX 155СС BLACK 8952"]

    def setUp(self):
        pricing._FLEET_CACHE.update(ts=0.0, data=None)
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        self._pa, self._bu, self._bt = pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN
        pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN = "quote_price", "https://x", "t"

        def _getter(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            mt = bike.upper().startswith("MT")
            per, dep = (927, 15000) if mt else (472, 7000)
            return {"ok": True, "data": {"day_price": per, "total": per * days, "deposit": dep,
                                         "available": True, "days": days,
                                         "text": f"{per} ฿/день, {per * days} ฿ за {days} дн"}}
        self.getter = _getter

    def tearDown(self):
        pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN = self._pa, self._bu, self._bt
        pricing._FLEET_CACHE.update(ts=0.0, data=None)

    def _note(self, transcript):
        h = suggest.extract_booking_hints(transcript, today=self.TODAY)
        pricing._FLEET_CACHE.update(ts=0.0, data=None)
        return suggest.build_pricing_note(h, lang="ru", getter=self.getter, today=self.TODAY)

    # --- ГОЛДЕН: модель+срок → quote XSR155 на 14 дней + ЕЁ депозит ---------------------------
    def test_golden_xsr155_two_weeks_quote_with_deposit(self):
        # ПОПРАВКА 20.08.2026 (правило владельца «подбор по классу»): «чужих чисел в НОТЕ нет»
        # сузилось до «чужих чисел нет В КАРТОЧКЕ XSR». MT-03 — тот же класс (мотоциклы) и идёт
        # ДОПОЛНИТЕЛЬНО, со своими цифрами и своим депозитом; подмена по-прежнему запрещена.
        note = self._note("[клиент]: XSR 155 с 20 июля на 2 недели")
        # ГОЛДЕНЫ ПЕРЕСЧИТАНЫ 21.08.2026 по правилу: XSR 155 = 444 x 1.0 (P1, старт 2026-07-20)
        # x 0.82 (корзина 14-29, 14 сут) = 364 ฿/день, итого 364 x 14 = 5096 ฿. Депозит правилом
        # НЕ подменяется — он приходит живой котировкой, поэтому 7000 остаётся как было.
        self.assertIn("364", note)                       # суточный тариф XSR155
        self.assertIn("5096", note)                      # итог за 14 дней = 364*14
        self.assertIn("депозит 7000 ฿", note)            # ЕЁ депозит дописан КОДОМ (в text его нет)
        line = (suggest._quote_block_from_note(note) or "").split("\n")[0]
        self.assertTrue(line.upper().startswith("XSR"))  # спрошенная модель — ОСНОВНОЙ вариант, первая
        self.assertNotIn("593", line)                    # чужой тариф MT-03 не подставлен в её карточку
        self.assertNotIn("15000", line)                  # чужой депозит MT-03 не подставлен
        self.assertNotIn("не удалось", note.lower())     # не свалились в «уточни модель/даты»
        self.assertNotIn("NMAX", note.upper())           # классом НИЖЕ сами не предлагаем

    def test_golden_paraphrases_model_term_carry_deposit(self):
        # дословная фраза + парафразы «модель + срок» → в каждом ответе живой quote XSR155 + депозит
        for ph in ("Здравствуйте! XSR 155 с 20 июля на 2 недели, посчитайте.",
                   "хочу xsr155 с 20 июля на 14 дней",
                   "Можно XSR 155 с 20.07 на 2 недели?",
                   "XSR 155, аренда с 20 июля на 2 недели",
                   "беру XSR 155 с 20 на 2 недели"):
            note = self._note("[клиент]: " + ph)
            # ГОЛДЕНЫ ПЕРЕСЧИТАНЫ 21.08.2026: 444 x 1.0 (P1) x 0.82 (14-29) = 364 ฿/день, итого 5096 ฿.
            self.assertIn("364", note, ph)
            self.assertIn("5096", note, ph)
            self.assertIn("депозит 7000 ฿", note, ph)

    # --- ГОЛДЕН: «10% это какая сумма» → 10% от суммы ЭТОГО расчёта -----------------------------
    def test_golden_percent_of_calculation(self):
        # окно диалога: модель+срок в первой реплике, вопрос про процент — в последней
        tr = ("[клиент]: XSR 155 с 20 июля на 2 недели\n"
              "[менеджер]: секунду\n"
              "[клиент]: 10% это какая сумма")
        note = self._note(tr)
        # ГОЛДЕНЫ ПЕРЕСЧИТАНЫ 21.08.2026: база расчёта = итог правила 5096 ฿ (364 x 14),
        # значит и процент считается от него: 5096 x 0.10 = 509.6 → 510 ฿.
        self.assertIn("5096", note)                      # база расчёта — итог XSR155 на 14 дней
        self.assertIn("510 ฿", note)                     # 10% от 5096 = 509.6 → 510 (КОД посчитал)
        self.assertIn("10%", note)

    def test_golden_percent_paraphrases(self):
        for ph in ("10% это какая сумма",
                   "а сколько будет 10%?",
                   "10 процентов это сколько",
                   "how much is 10%?",
                   "what's 10% of that?"):
            tr = f"[клиент]: XSR 155 с 20 июля на 2 недели\n[клиент]: {ph}"
            note = self._note(tr)
            self.assertIn("510 ฿", note, ph)      # 10% от итога правила 5096 ฿ (пересчёт 21.08.2026)

    def test_percent_detector_positive_negative(self):
        for pos in ("10% это какая сумма", "сколько будет 10%", "10 процентов это сколько",
                    "how much is 10%"):
            self.assertEqual(suggest._asks_percent_amount(pos, pos), 10, pos)
        for neg in ("даю скидку 10% сам", "депозит меньше на 10 процентов", "привет",
                    "нужен nmax на неделю"):
            self.assertIsNone(suggest._asks_percent_amount(neg, neg), neg)

    def test_percent_no_quote_no_number(self):
        # процент спросили, но цены нет (парк недоступен) → число НЕ называем, просим уточнить
        empty = lambda p: {"ok": False}
        h = suggest.extract_booking_hints(
            "[клиент]: XSR 155 с 20 июля на 2 недели\n[клиент]: 10% это какая сумма", today=self.TODAY)
        note = suggest.build_pricing_note(h, lang="ru", getter=empty, today=self.TODAY)
        self.assertNotIn("510", note)                    # число правила (пересчёт 21.08.2026)
        self.assertIn("10%", note)                       # вопрос отражён, но без числа

    # --- ГОЛДЕН: карточка чужой модели блокируется пост-чеком чисел ----------------------------
    # Голден оставлен ДОСЛОВНЫМ и приколот к прежнему пути (ручка отката PRICE_CLASS_OFFER_OFF):
    # его посылка — «числа MT-03 никем не посчитаны, значит выдуманы» — с 20.08 верна ровно там,
    # где подбор по классу выключен. Живой класс он держит: без расчёта чужие цифры режутся.
    def test_foreign_model_card_blocked_by_postcheck(self):
        save = suggest._CLASS_OFFER_OFF
        suggest._CLASS_OFFER_OFF = True
        try:
            note = self._note("[клиент]: XSR 155 с 20 июля на 2 недели")   # белый список = числа XSR155
        finally:
            suggest._CLASS_OFFER_OFF = save
        # ЧИСЛА ПЕРЕСЧИТАНЫ 21.08.2026 по правилу: своя сумма XSR155 = 364 x 14 = 5096 ฿;
        # «чужая» сумма MT-03 на тех же датах = 593 x 14 = 8302 ฿ (её белый список НЕ содержит,
        # потому что при выключенном подборе по классу MT-03 никто не считал).
        draft = ("XSR 155 — 5096 ฿ за 14 дней, депозит 7000 ฿. "
                 "А MT-03 — 8302 ฿ за 14 дней, депозит 15000 ฿.")
        out = suggest.postcheck_draft(draft, "ru", pricing_note=note)
        client = out.split("[уточнить", 1)[0]
        self.assertIn("5096", client)                    # своя сумма (из quote) цела
        self.assertIn("7000", client)                    # свой депозит цел
        self.assertNotIn("8302", client)                 # чужая сумма MT-03 вырезана
        self.assertNotIn("15000", client)                # чужой депозит MT-03 вырезан
        self.assertIn("уточню у команды", out.lower())

    def test_class_offer_whitelists_only_computed_numbers(self):
        # ТОТ ЖЕ пост-чек при ВКЛЮЧЁННОМ подборе по классу (поведение по умолчанию с 20.08):
        # MT-03 того же класса реально ПОСЧИТАНА, её цифры законны и остаются; а число, которого
        # источник не считал, режется по-прежнему — граница «посчитано/выдумано» не сдвинулась.
        note = self._note("[клиент]: XSR 155 с 20 июля на 2 недели")
        # ЧИСЛА ПЕРЕСЧИТАНЫ 21.08.2026 по правилу: XSR 155 = 364 x 14 = 5096 ฿,
        # MT-03 300 = 723 x 1.0 (P1) x 0.82 (14-29) = 593 ฿/день → 593 x 14 = 8302 ฿.
        self.assertIn("8302", note)                      # MT-03 посчитана КОДОМ (та же дверь quote)
        # NMAX 155 классом НИЖЕ — в подбор не идёт, значит НЕ посчитана, значит её цифры выдуманы.
        draft = ("XSR 155 — 5096 ฿ за 14 дней, депозит 7000 ฿. "
                 "А MT-03 — 8302 ฿ за 14 дней, депозит 15000 ฿. "
                 "А NMAX 155 — 44444 ฿ за 14 дней, депозит 3000 ฿.")
        out = suggest.postcheck_draft(draft, "ru", pricing_note=note)
        client = out.split("[уточнить", 1)[0]
        self.assertIn("5096", client)                    # своя сумма цела
        self.assertIn("8302", client)                    # посчитанная сумма соседа по классу цела
        self.assertNotIn("44444", client)                # НЕ посчитанное источником по-прежнему режется
        self.assertNotIn("3000", client)                 # и выдуманный депозит вместе с ним

    def test_own_deposit_and_total_kept_by_postcheck(self):
        # весь черновик из чисел quote XSR155 → пост-чек не трогает (fail-safe, регресс)
        note = self._note("[клиент]: XSR 155 с 20 июля на 2 недели")
        # 5096 ฿ = итог правила (364 x 14), пересчёт 21.08.2026
        draft = "XSR 155: 5096 ฿ за 14 дней, депозит 7000 ฿."
        self.assertEqual(suggest.postcheck_draft(draft, "ru", pricing_note=note), draft)


import urllib.error


def _http_err(code):
    return urllib.error.HTTPError("https://x/exec", code, "boom", None, None)


class TestFleetStatusSplitsEmptyFromFailure(unittest.TestCase):
    """«Парк реально пуст» и «парк не получен» — РАЗНЫЕ исходы. Раньше оба давали [] и были
    неразличимы: 28.07 мост отдал 404, а вызывающий код увидел то же, что при пустом парке."""

    def setUp(self):
        pricing._FLEET_CACHE["data"] = None
        pricing._FLEET_CACHE["ts"] = 0

    def tearDown(self):
        pricing._FLEET_CACHE["data"] = None
        pricing._FLEET_CACHE["ts"] = 0

    def test_live_ok_with_bikes(self):
        g = lambda p: {"ok": True, "data": {"bikes": [{"name": "NMAX 155CC BLACK 4255"}]}}
        bikes, ok = pricing.fleet_status(_get=g)
        self.assertTrue(ok)
        self.assertEqual([b["name"] for b in bikes], ["NMAX 155CC BLACK 4255"])

    def test_really_empty_park_is_not_a_failure(self):
        bikes, ok = pricing.fleet_status(_get=lambda p: {"ok": True, "data": {"bikes": []}})
        self.assertTrue(ok)                      # ← мост ответил: парк ДЕЙСТВИТЕЛЬНО пуст
        self.assertEqual(bikes, [])

    def test_bridge_failure_is_not_empty_park(self):
        def boom(p):
            raise _http_err(404)
        bikes, ok = pricing.fleet_status(_get=boom, _sleep=lambda s: None)
        self.assertFalse(ok)                     # ← сбой отличим от пустого парка
        self.assertEqual(bikes, [])

    def test_non_ok_answer_is_failure(self):
        _, ok = pricing.fleet_status(_get=lambda p: {"ok": False})
        self.assertFalse(ok)

    def test_retry_recovers_single_flap(self):
        calls = []

        def flaky(p):
            calls.append(1)
            if len(calls) == 1:
                raise _http_err(404)             # ровно отказ 28.07
            return {"ok": True, "data": {"bikes": [{"name": "ADV 350CC BLACK 5849"}]}}

        bikes, ok = pricing.fleet_status(_get=flaky, _sleep=lambda s: None)
        self.assertTrue(ok)                      # ← одного повтора хватило
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(bikes), 1)

    def test_permanent_error_not_retried(self):
        calls = []

        def denied(p):
            calls.append(1)
            raise _http_err(403)

        _, ok = pricing.fleet_status(_get=denied, _sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)          # 403 постоянный — второй заход бессмыслен

    def test_stale_cache_returned_but_marked_not_ok(self):
        pricing._FLEET_CACHE["data"] = [{"name": "CB 300CC R 9011"}]
        pricing._FLEET_CACHE["ts"] = 0           # кэш стух (TTL истёк)

        def boom(p):
            raise _http_err(500)

        bikes, ok = pricing.fleet_status(_get=boom, _now=lambda: 10 ** 9, _sleep=lambda s: None)
        self.assertEqual(len(bikes), 1)          # данные отдаём…
        self.assertFalse(ok)                     # …но честно помечаем: не свежие

    def test_fleet_wrapper_still_returns_list(self):
        self.assertEqual(pricing.fleet(_get=lambda p: {"ok": False}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
