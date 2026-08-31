# -*- coding: utf-8 -*-
"""Юниты C0: чистый советник по цене `pricing_advisor`.

ФИКСТУРА КОПИРУЕТ ЖИВОЙ ФОРМАТ (правило-класс CLAUDE.md), а не удобную тесту схему:
  • имя юнита взято в форме Лист1 — «XMAX 300CC NEW BLACK PHUKET 8969», с рабочим объёмом
    внутри имени и меткой поколения `NEW` там, где её ставит лист;
  • модель названа словами строки листа («XMAX 300»), поколение — «2023-», ровно как их держат
    поля `sheet_model`/`generation` боевого `price_source.json`;
  • цена суток 662 ฿ — не выдуманное круглое число, а база нового поколения XMAX из того же
    файла (у старого поколения там 557; разница между ними и есть цена ошибки разведения);
  • окно 2026-09-07..2026-09-14 и `days=7` — та самая опорная корзина «7-13», которой сняты
    живые базы (см. `price_snapshot_collect`), а `snapshot_on=2026-08-18` — дата слепка файла.

ГЛАВНОЕ, ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ, — ИНВАРИАНТ ВСЕГО C0: советник не называет НИ ОДНОГО числа,
кроме переданного ему `list_day_price`. Он проверяется не одним счастливым случаем, а прогоном
всего корпуса сценариев (`TestInvariantsAcrossCorpus`), включая красные.
"""
import ast
import copy
import hashlib
import io
import json
import os
import unittest

import pricing_advisor as advisor

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_SOURCE = os.path.join(HERE, "price_source.json")
MODULE_PATH = os.path.join(HERE, "pricing_advisor.py")

AS_OF = "2026-08-31"
LIST_PRICE = 662          # база нового поколения XMAX 300 по price_source.json
OLD_GEN_PRICE = 557       # база старого поколения — здесь только как напоминание цены ошибки

# Нечисловые «числа» держим здесь, а не пишем по месту: они приезжают в живой вход не строкой
# «nan», а готовым float — из json.loads('NaN'), из деления в чужом калькуляторе, из пустой ячейки
# листа, прошедшей через float(). Фикстура обязана копировать этот формат, а не удобную тесту
# строку (правило-класс CLAUDE.md: мок повторяет живой формат).
NAN = float("nan")
POS_INF = float("inf")
NEG_INF = float("-inf")

EVIDENCE = [
    "price_source.json@2026-08-18",
    "docs/artifacts/2026-08-17-price-table.md",
    "лист Календарь бронирования H3",
]


def quote(**over):
    """Полные факты котировки: свежий слепок, разведённое поколение, доказанное наличие."""
    facts = {
        "model": "XMAX 300",
        "unit_name": "XMAX 300CC NEW BLACK PHUKET 8969",
        "generation": "2023-",
        "generation_status": advisor.GEN_RESOLVED,
        "list_day_price": LIST_PRICE,
        "currency": "THB",
        "availability": advisor.AVAIL_OK,
        "date_start": "2026-09-07",
        "date_end": "2026-09-14",
        "days": 7,
        "snapshot_on": "2026-08-18",
        "evidence_refs": list(EVIDENCE),
    }
    facts.update(over)
    return {k: v for k, v in facts.items() if v is not _ABSENT}


def dialog(**over):
    base = {"cheaper_requested": False, "units_requested": 1}
    base.update(over)
    return {k: v for k, v in base.items() if v is not _ABSENT}


class _Absent(object):
    """Метка «ключа нет вовсе» — отличаем её от честного None в значении."""


_ABSENT = _Absent()

HISTORY_OK = {"deals_count": 5, "median_day_price_thb": 640, "window_days": 90,
              "source": "closed_deals"}


def run(q=None, d=None, h=None, p=None, as_of=AS_OF):
    return advisor.recommend_price(quote() if q is None else q,
                                   dialog() if d is None else d,
                                   h, p, as_of=as_of)


class TestHappyPath(unittest.TestCase):
    """Полный свежий случай: цена листа названа дословно, решение всё равно за человеком."""

    def test_complete_case_offers_list_price(self):
        got = run(h=HISTORY_OK)
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_OFFER_LIST)
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)
        self.assertEqual(got["recommendation"]["currency"], "THB")
        self.assertIs(got["approval_required"], True)
        self.assertEqual(got["mode"], "shadow")
        self.assertEqual(got["schema"], "turbobaby/price_recommendation/v1")

    def test_packet_keys_are_the_declared_contract(self):
        self.assertEqual(tuple(sorted(run(h=HISTORY_OK))), tuple(sorted(advisor.RESULT_KEYS)))

    def test_evidence_refs_are_carried_through(self):
        self.assertEqual(run(h=HISTORY_OK)["evidence_refs"], EVIDENCE)

    def test_history_corroboration_lifts_confidence(self):
        got = run(h=HISTORY_OK)
        self.assertEqual(got["confidence"]["level"], "high")
        self.assertIn("history_corroborates", got["reason_codes"])
        self.assertEqual(got["history"]["used_as"], "evidence_only")

    def test_without_history_confidence_is_medium_not_high(self):
        got = run()
        self.assertEqual(got["confidence"]["level"], "medium")
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)

    def test_thin_history_is_a_note_and_not_an_unknown(self):
        """Тонкая улика НЕ роняет готовую рекомендацию: у прошлых сделок такой власти нет."""
        got = run(h={"deals_count": 1, "median_day_price_thb": 640})
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertIn("history_thin", got["reason_codes"])
        self.assertEqual(got["unknowns"], [])

    def test_generation_not_applicable_is_a_full_case(self):
        got = run(q=quote(generation_status=advisor.GEN_NOT_APPLICABLE, generation=_ABSENT))
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)


class TestCheaperAndBudget(unittest.TestCase):
    """Дешевле просят словами — цена от этого не двигается ни на бат."""

    def test_cheaper_without_owner_discount_goes_to_alternative(self):
        got = run(d=dialog(cheaper_requested=True))
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_OFFER_ALTERNATIVE)
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)
        self.assertIn("cheaper_requested_no_owner_discount", got["reason_codes"])

    def test_cheaper_with_declared_owner_discount_goes_to_manager(self):
        """Скидка объявлена владельцем — но считать её C0 не умеет и не должен."""
        got = run(d=dialog(cheaper_requested=True), p={"discount_approved": True})
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_MANAGER_REVIEW)
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)
        self.assertIn("owner_discount_policy_present", got["reason_codes"])

    def test_budget_above_list_does_not_raise_target(self):
        got = run(d=dialog(stated_budget_thb_per_day=1500))
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)
        self.assertNotEqual(got["recommendation"]["target"], 1500)
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_OFFER_LIST)
        self.assertIn("budget_above_list", got["reason_codes"])

    def test_budget_below_list_filters_but_does_not_cut_target(self):
        got = run(d=dialog(stated_budget_thb_per_day=400))
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_OFFER_ALTERNATIVE)
        self.assertIn("budget_below_list", got["reason_codes"])


class TestManagerReview(unittest.TestCase):
    """Случаи, где машине сказать нечего, а человеку — есть."""

    def test_long_term_180_days(self):
        got = run(q=quote(date_start="2026-09-07", date_end="2027-03-06", days=180),
                  d=dialog(term_days=180))
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_MANAGER_REVIEW)
        self.assertIn("long_term", got["reason_codes"])
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)

    def test_just_under_long_term_is_not_review(self):
        got = run(q=quote(date_start="2026-09-07", date_end="2027-03-05", days=179),
                  d=dialog(term_days=179))
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_OFFER_LIST)

    def test_multi_unit(self):
        got = run(d=dialog(units_requested=2))
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_MANAGER_REVIEW)
        self.assertIn("multi_unit", got["reason_codes"])

    def test_explicit_discount_bundle_agent(self):
        for key, code in (("discount_requested", "discount_requested"),
                          ("bundle_requested", "bundle_requested"),
                          ("agent_case", "agent_case")):
            got = run(d=dialog(**{key: True}))
            self.assertEqual(got["recommendation"]["action"], advisor.ACTION_MANAGER_REVIEW, key)
            self.assertIn(code, got["reason_codes"])
            self.assertEqual(got["recommendation"]["target"], LIST_PRICE, key)

    def test_history_conflict_keeps_list_price_and_names_the_reason(self):
        got = run(h={"deals_count": 9, "median_day_price_thb": 900, "window_days": 90})
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_MANAGER_REVIEW)
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)
        self.assertIn("history_vs_list_conflict", got["reason_codes"])
        self.assertTrue(got["history"]["conflict"])
        self.assertAlmostEqual(got["history"]["delta_pct"], 35.95, places=2)

    def test_history_inside_threshold_is_not_a_conflict(self):
        got = run(h={"deals_count": 9, "median_day_price_thb": 600, "window_days": 90})
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_OFFER_LIST)
        self.assertFalse(got["history"]["conflict"])


class TestBlockedAndUnknown(unittest.TestCase):
    """Ни одна ветка не превращает «не смог проверить» в «проверено и хорошо»."""

    def assertNoTarget(self, got, status=None):
        self.assertIsNone(got["recommendation"]["target"])
        self.assertIn(got["status"], (advisor.STATUS_BLOCKED, advisor.STATUS_UNKNOWN))
        # Уверенность отказа названа словом СХЕМЫ, а не пятым словом: `blocked` дисквалифицирующему
        # факту, `low` — неразрешимому. Раньше здесь стояло `none`, которого схема не объявляла.
        self.assertEqual(got["confidence"]["level"],
                         "blocked" if got["status"] == advisor.STATUS_BLOCKED else "low")
        if status is not None:
            self.assertEqual(got["status"], status)

    def test_stale_snapshot_is_blocked(self):
        got = run(q=quote(snapshot_on="2026-08-10"))
        self.assertNoTarget(got, advisor.STATUS_BLOCKED)
        self.assertIn("snapshot_stale", got["reason_codes"])
        self.assertEqual(got["facts"]["snapshot_age_days"], 21)

    def test_snapshot_exactly_at_threshold_is_still_fresh(self):
        got = run(q=quote(snapshot_on="2026-08-17"))
        self.assertEqual(got["facts"]["snapshot_age_days"], 14)
        self.assertEqual(got["status"], advisor.STATUS_OK)

    def test_snapshot_from_the_future_is_blocked(self):
        got = run(q=quote(snapshot_on="2026-09-01"))
        self.assertNoTarget(got, advisor.STATUS_BLOCKED)
        self.assertIn("snapshot_in_future", got["reason_codes"])

    def test_proven_busy_unit_is_blocked(self):
        got = run(q=quote(availability=advisor.AVAIL_NONE_AVAILABLE))
        self.assertNoTarget(got, advisor.STATUS_BLOCKED)
        self.assertIn("availability_none_available", got["reason_codes"])

    def test_unproven_availability_is_unknown(self):
        for value in ("error", "no_candidates"):
            got = run(q=quote(availability=value))
            self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
            self.assertIn("availability_not_proven", got["reason_codes"])

    def test_unknown_availability_word_is_unknown(self):
        got = run(q=quote(availability="занят"))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("availability_unknown_value", got["reason_codes"])

    def test_ambiguous_generation_is_unknown(self):
        got = run(q=quote(generation_status=advisor.GEN_AMBIGUOUS))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("generation_ambiguous", got["reason_codes"])
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_ASK_MISSING_FACT)

    def test_generation_state_missing_is_unknown(self):
        got = run(q=quote(generation_status=_ABSENT))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("generation_status_missing", got["reason_codes"])

    def test_missing_model_is_unknown(self):
        got = run(q=quote(model=_ABSENT))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("model_missing", got["reason_codes"])

    def test_missing_dates_and_term(self):
        got = run(q=quote(date_start=_ABSENT, date_end=_ABSENT, days=_ABSENT))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("dates_missing", got["reason_codes"])
        self.assertIn("term_missing", got["reason_codes"])

    def test_impossible_date_is_unknown(self):
        got = run(q=quote(date_end="2026-02-30"))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("dates_invalid", got["reason_codes"])

    def test_term_does_not_match_the_window(self):
        got = run(q=quote(days=30))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("dates_days_mismatch", got["reason_codes"])

    def test_client_term_far_from_quoted_term(self):
        """Допуск ±1 сутки — копия pricing.sanity_days_ok, а не новая мерка."""
        self.assertEqual(run(d=dialog(term_days=8))["status"], advisor.STATUS_OK)
        got = run(d=dialog(term_days=30))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("term_mismatch", got["reason_codes"])

    def test_expired_quote_window(self):
        got = run(q=quote(date_start="2026-07-01", date_end="2026-07-08", days=7))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("quote_window_expired", got["reason_codes"])

    def test_missing_and_non_positive_list_price(self):
        got = run(q=quote(list_day_price=_ABSENT))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("list_price_missing", got["reason_codes"])
        got = run(q=quote(list_day_price=0))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("list_price_not_positive", got["reason_codes"])
        got = run(q=quote(list_day_price=True))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("list_price_invalid", got["reason_codes"])

    def test_currency_missing_is_unknown_and_foreign_currency_is_blocked(self):
        got = run(q=quote(currency=_ABSENT))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("currency_missing", got["reason_codes"])
        got = run(q=quote(currency="USD"))
        self.assertNoTarget(got, advisor.STATUS_BLOCKED)
        self.assertIn("currency_not_thb", got["reason_codes"])

    def test_missing_evidence_refs(self):
        got = run(q=quote(evidence_refs=_ABSENT))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("evidence_missing", got["reason_codes"])
        got = run(q=quote(evidence_refs=[]))
        self.assertNoTarget(got, advisor.STATUS_UNKNOWN)
        self.assertIn("evidence_invalid", got["reason_codes"])

    def test_as_of_must_be_injected_and_real(self):
        for bad in (None, "", "вчера", "2026-13-01", 20260831):
            got = run(as_of=bad)
            self.assertNoTarget(got, advisor.STATUS_BLOCKED)
            self.assertIn("as_of_invalid", got["reason_codes"])
            self.assertIsNone(got["as_of"])

    def test_foreign_key_in_quote_facts_is_rejected(self):
        got = run(q=quote(bonus_margin=120))
        self.assertNoTarget(got, advisor.STATUS_BLOCKED)
        self.assertIn("quote_facts_unknown_key:bonus_margin", got["reason_codes"])

    def test_non_mapping_input_is_rejected(self):
        got = advisor.recommend_price(["XMAX"], None, None, None, as_of=AS_OF)
        self.assertNoTarget(got, advisor.STATUS_BLOCKED)
        self.assertIn("input_not_mapping:quote_facts", got["reason_codes"])


class TestOwnerBounds(unittest.TestCase):
    """C0 не изобретает владельцу лимитов и не подгоняет цену под них."""

    def test_no_policy_means_no_bounds(self):
        got = run(h=HISTORY_OK)
        self.assertIsNone(got["recommendation"]["floor"])
        self.assertIsNone(got["recommendation"]["ceiling"])

    def test_explicit_coherent_bounds_are_accepted_and_target_stays_inside(self):
        got = run(p={"floor_thb_per_day": 500, "ceiling_thb_per_day": 800})
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertEqual(got["recommendation"]["floor"], 500)
        self.assertEqual(got["recommendation"]["ceiling"], 800)
        self.assertLessEqual(got["recommendation"]["floor"], got["recommendation"]["target"])
        self.assertLessEqual(got["recommendation"]["target"], got["recommendation"]["ceiling"])
        self.assertIn("owner_bounds_declared", got["reason_codes"])

    def test_list_price_below_owner_floor_goes_to_manager_without_a_number(self):
        got = run(p={"floor_thb_per_day": 700})
        self.assertEqual(got["status"], advisor.STATUS_UNKNOWN)
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_MANAGER_REVIEW)
        self.assertIsNone(got["recommendation"]["target"])
        self.assertIn("list_price_outside_owner_bounds", got["reason_codes"])

    def test_list_price_above_owner_ceiling_goes_to_manager_without_a_number(self):
        got = run(p={"ceiling_thb_per_day": 600})
        self.assertIsNone(got["recommendation"]["target"])
        self.assertIn("list_price_outside_owner_bounds", got["unknowns"])

    def test_incoherent_bounds_are_blocked_and_not_echoed(self):
        got = run(p={"floor_thb_per_day": 800, "ceiling_thb_per_day": 500})
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIsNone(got["recommendation"]["floor"])
        self.assertIsNone(got["recommendation"]["ceiling"])
        self.assertIn("owner_policy_incoherent:floor_above_ceiling", got["reason_codes"])

    def test_garbage_bound_is_blocked_not_silently_defaulted(self):
        got = run(p={"floor_thb_per_day": "500"})
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIsNone(got["recommendation"]["floor"])
        self.assertIn("owner_policy_incoherent:floor_thb_per_day", got["reason_codes"])

    def test_owner_may_move_the_snapshot_threshold_explicitly(self):
        got = run(q=quote(snapshot_on="2026-08-10"), p={"snapshot_max_age_days": 30})
        self.assertEqual(got["status"], advisor.STATUS_OK)
        got = run(p={"snapshot_max_age_days": 5})
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIn("snapshot_stale", got["reason_codes"])

    def test_foreign_policy_key_is_rejected(self):
        got = run(p={"secret_margin": 10})
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIn("owner_policy_unknown_key:secret_margin", got["reason_codes"])


class TestSensitiveDialog(unittest.TestCase):
    """Сырой текст и личные данные в пакет не попадают ни ключом, ни значением."""

    CASES = (
        ("phone", "+66812345678", "contact"),
        ("client_name", "Ivan Petrov", "identity"),
        ("passport_number", "AB1234567", "document"),
        ("transcript", "здравствуйте, а сколько стоит аренда", "raw_text"),
        ("citizenship", "russian", "nationality"),
        ("locale", "ru-RU", "language"),
        ("home_address", "Rawai, Soi 5", "address"),
        ("card_number", "4111111111111111", "payment"),
        ("device_id", "iPhone-15-Pro", "device"),
        ("income_level", "top_bracket", "wealth"),
    )

    def test_sensitive_key_is_rejected_and_never_echoed(self):
        for key, value, category in self.CASES:
            got = run(d=dialog(**{key: value}))
            dumped = advisor.to_json(got)
            self.assertEqual(got["status"], advisor.STATUS_BLOCKED, key)
            self.assertIsNone(got["recommendation"]["target"], key)
            self.assertIn("dialog_sensitive_key:%s" % category, got["reason_codes"], key)
            self.assertNotIn(key, dumped, "имя ключа утекло: %s" % key)
            self.assertNotIn(value, dumped, "значение утекло: %s" % key)

    def test_raw_string_on_an_allowed_key_is_rejected_too(self):
        """Строк в диалоге не бывает ВОВСЕ: сырому тексту некуда лечь даже по знакомому ключу."""
        raw = "да, дешевле, меня зовут Иван, телефон +66812345678"
        got = run(d={"cheaper_requested": raw})
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIn("dialog_value_invalid:cheaper_requested", got["reason_codes"])
        self.assertNotIn(raw, advisor.to_json(got))
        self.assertNotIn("Иван", advisor.to_json(got))

    def test_unknown_but_harmless_key_is_still_rejected(self):
        got = run(d=dialog(mood_score=7))
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIn("dialog_unknown_key:mood_score", got["reason_codes"])

    def test_dangerous_key_name_itself_is_redacted(self):
        got = run(d={"+66812345678": True})
        self.assertNotIn("66812345678", advisor.to_json(got))
        self.assertIn("dialog_unknown_key:<redacted>", got["reason_codes"])

    def test_dialog_echo_holds_only_whitelisted_flags(self):
        got = run(d=dialog(cheaper_requested=True, units_requested=2, term_days=7,
                           stated_budget_thb_per_day=500))
        self.assertEqual(tuple(sorted(got["dialog"])), tuple(sorted(advisor.DIALOG_KEYS)))

    def test_history_source_of_bad_shape_is_redacted_not_echoed(self):
        got = run(h={"deals_count": 5, "median_day_price_thb": 640,
                     "source": "клиент Иван\nтелефон +66812345678"})
        self.assertEqual(got["history"]["source"], advisor.REDACTED)
        self.assertNotIn("66812345678", advisor.to_json(got))

    def test_units_token_is_not_mistaken_for_user(self):
        """Разбор по токенам, а не по подстроке: `units_requested` — законный ключ."""
        self.assertIsNone(advisor._category_of_key("units_requested"))
        self.assertIsNone(advisor._category_of_key("stated_budget_thb_per_day"))
        self.assertEqual(advisor._category_of_key("client_phone"), "contact")


class TestDeterminismAndSerialization(unittest.TestCase):

    def test_same_input_and_as_of_give_byte_equal_json(self):
        q, d, h, p = quote(), dialog(cheaper_requested=True), dict(HISTORY_OK), {"floor_thb_per_day": 100}
        first = advisor.to_json(advisor.recommend_price(q, d, h, p, as_of=AS_OF))
        second = advisor.to_json(advisor.recommend_price(copy.deepcopy(q), copy.deepcopy(d),
                                                         copy.deepcopy(h), copy.deepcopy(p),
                                                         as_of=AS_OF))
        self.assertEqual(first, second)

    def test_key_order_of_the_input_does_not_change_the_answer(self):
        straight = quote()
        shuffled = {k: straight[k] for k in sorted(straight, reverse=True)}
        self.assertEqual(advisor.to_json(run(q=straight, h=HISTORY_OK)),
                         advisor.to_json(run(q=shuffled, h=HISTORY_OK)))

    def test_packet_serializes_without_a_custom_encoder(self):
        packet = run(h=HISTORY_OK, p={"ceiling_thb_per_day": 900})
        text = json.dumps(packet, ensure_ascii=False)
        self.assertEqual(json.loads(text), packet)

    def test_inputs_are_not_mutated(self):
        q, d, h, p = quote(), dialog(cheaper_requested=True), dict(HISTORY_OK), {"floor_thb_per_day": 100}
        before = (copy.deepcopy(q), copy.deepcopy(d), copy.deepcopy(h), copy.deepcopy(p))
        advisor.recommend_price(q, d, h, p, as_of=AS_OF)
        self.assertEqual((q, d, h, p), before)

    def test_as_of_is_keyword_only(self):
        with self.assertRaises(TypeError):
            advisor.recommend_price(quote(), dialog(), None, None, AS_OF)


class TestNonFiniteNumbers(unittest.TestCase):
    """Дефекты 1–3 независимой пробы 31.08.2026: `NaN`/`Inf` — ГЛУШИТЕЛЬ СРАВНЕНИЙ, а не «плохое
    число». Все три случая — один корень: у `NaN` ложны разом `x <= 0`, `x > порог` и
    `abs(x) > порог`, поэтому одно значение проходило три разные заставы подряд."""

    def test_nan_and_inf_list_price_never_become_the_target(self):
        """Дефект 1. `NaN` проходил как `ok` и становился ЦЕЛЬЮ: `NaN <= 0` ложно."""
        for bad in (NAN, POS_INF, NEG_INF):
            got = run(q=quote(list_day_price=bad))
            self.assertEqual(got["status"], advisor.STATUS_UNKNOWN, repr(bad))
            self.assertIsNone(got["recommendation"]["target"], repr(bad))
            self.assertIsNone(got["facts"]["list_day_price"], repr(bad))
            self.assertIn("list_price_invalid", got["reason_codes"], repr(bad))
            json.dumps(got, allow_nan=False)

    def test_nan_median_is_not_corroboration_and_does_not_lift_confidence(self):
        """Дефект 2. `abs(NaN) > порог` ложно → ветка «расхождения нет» → уверенность `high`."""
        got = run(h={"deals_count": 9, "median_day_price_thb": NAN, "window_days": 90})
        self.assertFalse(got["history"]["corroborates"])
        self.assertIsNone(got["history"]["median_day_price_thb"])
        self.assertIsNone(got["history"]["delta_pct"])
        self.assertNotEqual(got["confidence"]["level"], "high")
        self.assertIn("history_invalid:median_day_price_thb", got["reason_codes"])
        self.assertNotIn("history_corroborates", got["reason_codes"])
        json.dumps(got, allow_nan=False)

    def test_inf_median_is_rejected_too(self):
        for bad in (POS_INF, NEG_INF):
            got = run(h={"deals_count": 9, "median_day_price_thb": bad})
            self.assertIsNone(got["history"]["median_day_price_thb"], repr(bad))
            self.assertFalse(got["history"]["corroborates"], repr(bad))
            json.dumps(got, allow_nan=False)

    def test_nan_snapshot_threshold_does_not_switch_off_the_staleness_check(self):
        """Дефект 3. `NaN < 0` ложно → порог принимался; `NaN > 0` ложно → проверка молчала."""
        got = run(q=quote(snapshot_on="2020-01-01"), p={"snapshot_max_age_days": NAN})
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIsNone(got["recommendation"]["target"])
        self.assertIn("owner_policy_incoherent:snapshot_max_age_days", got["reason_codes"])
        # Кривая ручка НЕ отменила проверку: слепок пятилетней давности всё равно назван протухшим.
        self.assertIn("snapshot_stale", got["reason_codes"])

    def test_every_numeric_policy_knob_refuses_non_finite(self):
        for key in ("floor_thb_per_day", "ceiling_thb_per_day", "snapshot_max_age_days",
                    "history_max_age_days", "history_conflict_pct", "discount_max_pct"):
            for bad in (NAN, POS_INF, NEG_INF):
                got = run(p={key: bad})
                self.assertEqual(got["status"], advisor.STATUS_BLOCKED, "%s=%r" % (key, bad))
                self.assertIn("owner_policy_incoherent:%s" % key, got["reason_codes"],
                              "%s=%r" % (key, bad))
                self.assertIsNone(got["recommendation"]["floor"], "%s=%r" % (key, bad))
                self.assertIsNone(got["recommendation"]["ceiling"], "%s=%r" % (key, bad))

    def test_non_finite_client_budget_is_rejected(self):
        """Бюджет — тот же класс: он уходит в эхо диалога и сравнивается с целью."""
        for bad in (NAN, POS_INF, NEG_INF):
            got = run(d=dialog(stated_budget_thb_per_day=bad))
            self.assertEqual(got["status"], advisor.STATUS_BLOCKED, repr(bad))
            self.assertIsNone(got["dialog"]["stated_budget_thb_per_day"], repr(bad))
            self.assertIn("dialog_value_invalid:stated_budget_thb_per_day", got["reason_codes"])
            json.dumps(got, allow_nan=False)

    def test_finite_inputs_whose_ratio_overflows_do_not_leak_inf(self):
        """Оба входа конечны, а их отношение — нет. Наружу такое число уйти не должно."""
        got = run(q=quote(list_day_price=5e-324),
                  h={"deals_count": 9, "median_day_price_thb": 1e308})
        json.dumps(got, allow_nan=False)
        self.assertIsNone(got["history"]["delta_pct"])
        self.assertFalse(got["history"]["corroborates"])

    def test_is_finite_helper_tells_numbers_from_their_impostors(self):
        for good in (0, 1, -1, 662, 0.5, -0.5, 1e308):
            self.assertTrue(advisor._is_finite(good), repr(good))
        for bad in (NAN, POS_INF, NEG_INF, True, False, None, "662", [662]):
            self.assertFalse(advisor._is_finite(bad), repr(bad))


class TestQuoteWindowAgainstAsOf(unittest.TestCase):
    """Дефект 4: окно, НАЧАВШЕЕСЯ до `as_of`, проходило как `ok` и получало цель."""

    def test_window_started_before_as_of_gets_no_target(self):
        got = run(q=quote(date_start="2026-08-20", date_end="2026-09-10", days=21))
        self.assertEqual(got["status"], advisor.STATUS_UNKNOWN)
        self.assertIsNone(got["recommendation"]["target"])
        self.assertIn("quote_window_already_started", got["reason_codes"])
        self.assertIn("quote_window_already_started", got["unknowns"])

    def test_window_starting_exactly_on_as_of_is_still_a_full_case(self):
        """Граница названа строго: начало РОВНО в момент сверки прошлым ещё не является."""
        got = run(q=quote(date_start=AS_OF, date_end="2026-09-07", days=7))
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)

    def test_fully_past_window_keeps_its_agreed_code(self):
        """Согласованную семантику полностью прошедшего окна правка НЕ трогает: код прежний
        (`quote_window_expired`), и второй код к нему не приклеивается."""
        got = run(q=quote(date_start="2026-07-01", date_end="2026-07-08", days=7))
        self.assertEqual(got["status"], advisor.STATUS_UNKNOWN)
        self.assertIn("quote_window_expired", got["reason_codes"])
        self.assertNotIn("quote_window_already_started", got["reason_codes"])

    def test_future_window_is_untouched(self):
        got = run(q=quote(date_start="2026-12-01", date_end="2026-12-08", days=7))
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)


class TestConfidenceSchema(unittest.TestCase):
    """Дефект 5: `confidence.level = none` не входит в утверждённую схему."""

    def test_whitelist_is_exactly_the_approved_four_words(self):
        self.assertEqual(tuple(advisor.CONFIDENCE_LEVELS), ("high", "medium", "low", "blocked"))

    def test_blocked_status_says_blocked(self):
        self.assertEqual(run(q=quote(currency="USD"))["confidence"]["level"], "blocked")

    def test_unknown_status_says_low(self):
        self.assertEqual(run(q=quote(model=_ABSENT))["confidence"]["level"], "low")

    def test_the_word_none_is_gone_from_every_branch(self):
        """Пятого слова нет НИ НА ОДНОЙ ветке — проверяем корпусом, а не счастливым случаем."""
        cases = [run(), run(h=HISTORY_OK), run(d=dialog(cheaper_requested=True)),
                 run(d=dialog(units_requested=2)), run(p={"floor_thb_per_day": 900}),
                 run(q=quote(currency="USD")), run(q=quote(availability="none_available")),
                 run(q=quote(list_day_price=NAN)), run(as_of="не дата"),
                 run(d={"phone": "+66812345678"})]
        for got in cases:
            self.assertIn(got["confidence"]["level"], advisor.CONFIDENCE_LEVELS,
                          got["reason_codes"])
            self.assertNotEqual(got["confidence"]["level"], "none")


class TestHistoryFreshness(unittest.TestCase):
    """Дефект 6: `fresh_as_of` отвергался как незнакомый ключ, а свежесть истории не проверялась
    вовсе. Правило асимметрии: сомнительная улика не подтверждает, но руку поднять вправе."""

    def test_fresh_as_of_is_accepted_and_echoed(self):
        got = run(h=dict(HISTORY_OK, fresh_as_of="2026-08-20"))
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertNotIn("history_unknown_key:fresh_as_of", got["reason_codes"])
        self.assertEqual(got["history"]["fresh_as_of"], "2026-08-20")
        self.assertEqual(got["history"]["fresh_age_days"], 11)
        self.assertTrue(got["history"]["corroborates"])
        self.assertEqual(got["confidence"]["level"], "high")

    def test_stale_history_does_not_corroborate_and_does_not_lift_confidence(self):
        got = run(h=dict(HISTORY_OK, fresh_as_of="2026-01-01"))
        self.assertEqual(got["status"], advisor.STATUS_OK)
        self.assertFalse(got["history"]["corroborates"])
        self.assertNotIn("history_corroborates", got["reason_codes"])
        self.assertIn("history_stale", got["reason_codes"])
        self.assertEqual(got["confidence"]["level"], "medium")
        self.assertEqual(got["recommendation"]["target"], LIST_PRICE)

    def test_history_from_the_future_does_not_corroborate(self):
        got = run(h=dict(HISTORY_OK, fresh_as_of="2027-01-01"))
        self.assertFalse(got["history"]["corroborates"])
        self.assertIn("history_fresh_as_of_in_future", got["reason_codes"])
        self.assertNotEqual(got["confidence"]["level"], "high")

    def test_malformed_fresh_as_of_does_not_corroborate_and_is_not_echoed(self):
        for bad in ("вчера", "2026-02-30", "2026-13-01", 20260820, "", None):
            got = run(h=dict(HISTORY_OK, fresh_as_of=bad))
            if bad is None:
                continue          # ключ со значением None = «не передано», старое поведение
            self.assertFalse(got["history"]["corroborates"], repr(bad))
            self.assertIsNone(got["history"]["fresh_as_of"], repr(bad))
            self.assertIn("history_invalid:fresh_as_of", got["reason_codes"], repr(bad))
            self.assertNotEqual(got["confidence"]["level"], "high", repr(bad))

    def test_freshness_without_an_injected_moment_is_unverified_not_fresh(self):
        """Момент сверки кривой → свежесть НЕ подтверждена. «Не смог проверить» ≠ «хорошо»."""
        got = run(h=dict(HISTORY_OK, fresh_as_of="2026-08-20"), as_of="не дата")
        self.assertFalse(got["history"]["corroborates"])
        self.assertIn("history_freshness_unverified", got["reason_codes"])
        self.assertIsNone(got["history"]["fresh_age_days"])

    def test_stale_history_still_raises_its_hand_on_a_conflict(self):
        """Асимметрия односторонняя: протухшая улика не подтверждает, но конфликт объявляет —
        обе ветки ведут к человеку, а не к машинной уверенности."""
        got = run(h={"deals_count": 9, "median_day_price_thb": 900, "window_days": 90,
                     "fresh_as_of": "2026-01-01"})
        self.assertTrue(got["history"]["conflict"])
        self.assertEqual(got["recommendation"]["action"], advisor.ACTION_MANAGER_REVIEW)
        self.assertIn("history_vs_list_conflict", got["reason_codes"])
        self.assertFalse(got["history"]["corroborates"])

    def test_history_never_moves_the_target_however_fresh_it_is(self):
        for fresh in ("2026-08-31", "2026-08-20", "2026-01-01", "2027-01-01", "мусор"):
            got = run(h={"deals_count": 9, "median_day_price_thb": 900, "fresh_as_of": fresh})
            target = got["recommendation"]["target"]
            if target is not None:
                self.assertEqual(target, LIST_PRICE, fresh)
            self.assertEqual(got["history"]["used_as"], "evidence_only", fresh)

    def test_owner_may_move_the_history_freshness_bound_explicitly(self):
        stale = dict(HISTORY_OK, fresh_as_of="2026-08-01")
        self.assertFalse(run(h=stale)["history"]["corroborates"])
        self.assertTrue(run(h=stale, p={"history_max_age_days": 60})["history"]["corroborates"])

    def test_history_freshness_bound_is_independent_of_the_snapshot_bound(self):
        """Владелец, отключивший проверку возраста слепка, не отключает этим возраст истории."""
        got = run(h=dict(HISTORY_OK, fresh_as_of="2026-01-01"), p={"snapshot_max_age_days": 0})
        self.assertFalse(got["history"]["corroborates"])
        self.assertIn("history_stale", got["reason_codes"])

    def test_a_foreign_history_key_is_still_rejected(self):
        """Расширение белого списка ровно на один ключ: соседний по смыслу — по-прежнему отказ."""
        got = run(h=dict(HISTORY_OK, collected_at="2026-08-20"))
        self.assertEqual(got["status"], advisor.STATUS_BLOCKED)
        self.assertIn("history_unknown_key:collected_at", got["reason_codes"])


class TestStrictJsonSerialization(unittest.TestCase):
    """`json.dumps(packet, allow_nan=False)` обязан проходить на ЛЮБОМ входе — и на зелёном, и на
    отказном. `NaN`/`Infinity` печатаются словами, которых в JSON нет: такой текст падает уже у
    потребителя, вдали от причины."""

    HOSTILE = (
        ("цена NaN", quote(list_day_price=NAN), dialog(), None, None),
        ("цена +Inf", quote(list_day_price=POS_INF), dialog(), None, None),
        ("цена -Inf", quote(list_day_price=NEG_INF), dialog(), None, None),
        ("медиана NaN", quote(), dialog(), {"deals_count": 9, "median_day_price_thb": NAN}, None),
        ("медиана Inf", quote(), dialog(), {"deals_count": 9,
                                           "median_day_price_thb": POS_INF}, None),
        ("бюджет NaN", quote(), dialog(stated_budget_thb_per_day=NAN), None, None),
        ("пол NaN", quote(), dialog(), None, {"floor_thb_per_day": NAN}),
        ("потолок Inf", quote(), dialog(), None, {"ceiling_thb_per_day": POS_INF}),
        ("возраст слепка NaN", quote(), dialog(), None, {"snapshot_max_age_days": NAN}),
        ("порог конфликта NaN", quote(), dialog(), None, {"history_conflict_pct": NAN}),
        ("скидка Inf", quote(), dialog(), None, {"discount_max_pct": POS_INF}),
        ("всё сразу", quote(list_day_price=NAN), dialog(stated_budget_thb_per_day=POS_INF),
         {"deals_count": 9, "median_day_price_thb": NEG_INF, "fresh_as_of": NAN},
         {"floor_thb_per_day": NAN, "ceiling_thb_per_day": POS_INF}),
    )

    def test_hostile_numeric_inputs_still_produce_strict_json(self):
        for name, q, d, h, p in self.HOSTILE:
            got = advisor.recommend_price(q, d, h, p, as_of=AS_OF)
            text = json.dumps(got, allow_nan=False)
            self.assertEqual(json.loads(text), got, name)
            self.assertNotIn("NaN", text, name)
            self.assertNotIn("Infinity", text, name)
            # Цель по-прежнему либо не названа, либо это ПОБИТОВО цена листа. Требовать здесь
            # непременно `None` было бы неверно: `NaN` в МЕДИАНЕ прошлых сделок — это негодная
            # улика, а не пропавший факт котировки, и ронять из-за неё готовую рекомендацию
            # значило бы дать истории власть, которой у неё в C0 нет.
            target = got["recommendation"]["target"]
            if target is not None:
                self.assertEqual(target, LIST_PRICE, name)

    def test_hostile_numbers_never_lift_confidence_to_high(self):
        for name, q, d, h, p in self.HOSTILE:
            got = advisor.recommend_price(q, d, h, p, as_of=AS_OF)
            self.assertNotEqual(got["confidence"]["level"], "high", name)

    def test_canonical_writer_is_strict_too(self):
        for name, q, d, h, p in self.HOSTILE:
            text = advisor.to_json(advisor.recommend_price(q, d, h, p, as_of=AS_OF))
            self.assertNotIn("NaN", text, name)
            self.assertNotIn("Infinity", text, name)

    def test_the_canonical_writer_refuses_rather_than_prints_a_broken_number(self):
        """Замок сверху: если промах ситa всё же случится, текста НЕ БУДЕТ — вместо тихой выдачи
        невалидного JSON. Проверяем сам замок, подсовывая ему уже испорченный пакет."""
        packet = run(h=HISTORY_OK)
        packet["recommendation"]["target"] = NAN
        with self.assertRaises(ValueError):
            advisor.to_json(packet)


CORPUS = (
    ("полный случай", quote(), dialog(), HISTORY_OK, None),
    ("дешевле", quote(), dialog(cheaper_requested=True), None, None),
    ("бюджет выше", quote(), dialog(stated_budget_thb_per_day=2000), None, None),
    ("бюджет ниже", quote(), dialog(stated_budget_thb_per_day=200), None, None),
    ("длинный срок", quote(date_end="2027-03-06", days=180), dialog(), None, None),
    ("несколько юнитов", quote(), dialog(units_requested=3), None, None),
    ("скидка", quote(), dialog(discount_requested=True), None, None),
    ("конфликт истории", quote(), dialog(),
     {"deals_count": 9, "median_day_price_thb": 900}, None),
    ("протухший слепок", quote(snapshot_on="2026-01-01"), dialog(), None, None),
    ("занят", quote(availability="none_available"), dialog(), None, None),
    ("поколение не разведено", quote(generation_status="ambiguous"), dialog(), None, None),
    ("нет цены", quote(list_day_price=None), dialog(), None, None),
    ("чужая валюта", quote(currency="EUR"), dialog(), None, None),
    ("нет улик", quote(evidence_refs=None), dialog(), None, None),
    ("границы владельца", quote(), dialog(), None,
     {"floor_thb_per_day": 600, "ceiling_thb_per_day": 700}),
    ("цена вне границ", quote(), dialog(), None, {"floor_thb_per_day": 900}),
    ("личное в диалоге", quote(), {"phone": "+66812345678"}, None, None),
    ("as_of кривой", quote(), dialog(), None, None),
    # Шесть форм независимой пробы 31.08.2026 — в общий корпус, чтобы инварианты мели и их тоже.
    ("цена NaN", quote(list_day_price=NAN), dialog(), None, None),
    ("цена Inf", quote(list_day_price=POS_INF), dialog(), None, None),
    ("медиана NaN", quote(), dialog(), {"deals_count": 9, "median_day_price_thb": NAN}, None),
    ("бюджет NaN", quote(), dialog(stated_budget_thb_per_day=NAN), None, None),
    ("порог возраста NaN", quote(snapshot_on="2020-01-01"), dialog(), None,
     {"snapshot_max_age_days": NAN}),
    ("окно уже началось", quote(date_start="2026-08-20", date_end="2026-09-10", days=21),
     dialog(), None, None),
    ("история протухла", quote(), dialog(), dict(HISTORY_OK, fresh_as_of="2026-01-01"), None),
    ("история из будущего", quote(), dialog(), dict(HISTORY_OK, fresh_as_of="2027-01-01"), None),
    ("свежесть истории кривая", quote(), dialog(), dict(HISTORY_OK, fresh_as_of="вчера"), None),
)


class TestInvariantsAcrossCorpus(unittest.TestCase):
    """Инварианты проверяются на ВСЁМ корпусе, включая красные случаи, а не на счастливом."""

    def cases(self):
        for name, q, d, h, p in CORPUS:
            as_of = "не дата" if name == "as_of кривой" else AS_OF
            yield name, advisor.recommend_price(q, d, h, p, as_of=as_of)

    def test_target_is_never_a_number_of_our_own(self):
        for name, got in self.cases():
            target = got["recommendation"]["target"]
            if target is not None:
                self.assertEqual(target, LIST_PRICE, name)

    def test_target_exists_only_together_with_status_ok(self):
        for name, got in self.cases():
            has_target = got["recommendation"]["target"] is not None
            self.assertEqual(has_target, got["status"] == advisor.STATUS_OK, name)

    def test_target_never_leaves_declared_owner_bounds(self):
        for name, got in self.cases():
            rec = got["recommendation"]
            if rec["target"] is None:
                continue
            if rec["floor"] is not None:
                self.assertLessEqual(rec["floor"], rec["target"], name)
            if rec["ceiling"] is not None:
                self.assertGreaterEqual(rec["ceiling"], rec["target"], name)

    def test_shape_of_every_packet(self):
        for name, got in self.cases():
            self.assertEqual(tuple(sorted(got)), tuple(sorted(advisor.RESULT_KEYS)), name)
            self.assertIn(got["status"], advisor.STATUSES, name)
            self.assertIn(got["recommendation"]["action"], advisor.ACTIONS, name)
            self.assertEqual(got["recommendation"]["currency"], "THB", name)
            self.assertEqual(got["mode"], "shadow", name)
            self.assertIs(got["approval_required"], True, name)
            self.assertIn(got["confidence"]["level"], advisor.CONFIDENCE_LEVELS, name)
            self.assertEqual(json.loads(advisor.to_json(got)), got, name)

    def test_every_packet_is_strict_json_on_the_whole_corpus(self):
        """`allow_nan=False` — на ВСЁМ корпусе, включая враждебные числовые входы."""
        for name, got in self.cases():
            self.assertEqual(json.loads(json.dumps(got, allow_nan=False)), got, name)

    def test_no_packet_carries_a_number_that_is_not_a_number(self):
        """Обход пакета целиком: конечность проверяется у КАЖДОГО числа, а не у названных полей."""
        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, "%s.%s" % (path, key))
            elif isinstance(node, (list, tuple)):
                for index, value in enumerate(node):
                    walk(value, "%s[%d]" % (path, index))
            elif isinstance(node, float):
                self.assertTrue(advisor._is_finite(node), "%s = %r" % (path, node))

        for name, got in self.cases():
            walk(got, name)

    def test_bounds_are_never_invented(self):
        for name, got in self.cases():
            if name in ("границы владельца", "цена вне границ"):
                continue
            self.assertIsNone(got["recommendation"]["floor"], name)
            self.assertIsNone(got["recommendation"]["ceiling"], name)

    def test_unknown_status_always_names_at_least_one_unknown(self):
        for name, got in self.cases():
            if got["status"] == advisor.STATUS_UNKNOWN:
                self.assertTrue(got["unknowns"], name)


class TestNoSideEffects(unittest.TestCase):
    """Модуль обязан быть слеп и нем: ни поверхности записи, ни импорта наружу."""

    ALLOWED_IMPORTS = {"json", "re"}
    FORBIDDEN_CALLS = {"open", "eval", "exec", "compile", "__import__", "input", "print",
                       "getattr", "setattr"}
    FORBIDDEN_ATTRS = {"system", "popen", "urlopen", "connect", "commit", "execute", "send",
                       "sendall", "environ", "getenv", "now", "today", "utcnow", "monotonic",
                       "randint", "choice", "uuid4", "write", "writelines", "remove", "unlink"}
    FORBIDDEN_TOKENS = ("urllib", "urlopen", "requests", "socket", "subprocess", "sqlite",
                        "os.environ", "getenv", "load_dotenv", "BRIDGE", "brain_writer",
                        "cowork_log", "pc_orchestrator", "suggest", "moderation", "trainer",
                        "anthropic", "openai", "time.time", "datetime", "utcnow", "random.",
                        "uuid", "logging", "smtplib", "ftplib", "pickle")

    def source(self):
        with io.open(MODULE_PATH, encoding="utf-8") as handle:
            return handle.read()

    def test_import_list_is_closed_everywhere_in_the_file(self):
        tree = ast.parse(self.source())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual(names, self.ALLOWED_IMPORTS)

    def test_no_forbidden_calls_or_attributes(self):
        tree = ast.parse(self.source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, self.FORBIDDEN_CALLS)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, self.FORBIDDEN_ATTRS)

    def test_no_forbidden_surface_tokens(self):
        text = self.source()
        for token in self.FORBIDDEN_TOKENS:
            self.assertNotIn(token, text, "в советнике найдено %r" % token)

    def test_module_defines_no_state_that_calls_could_change(self):
        """Кэшей и накопителей нет: два одинаковых захода не влияют друг на друга ничем."""
        mutable = [name for name, value in vars(advisor).items()
                   if not name.startswith("__") and isinstance(value, (list, set))]
        self.assertEqual(mutable, [])

    @staticmethod
    def _digest(path):
        with io.open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_runtime_price_source_is_untouched(self):
        before = self._digest(RUNTIME_SOURCE)
        for _name, q, d, h, p in CORPUS:
            advisor.recommend_price(q, d, h, p, as_of=AS_OF)
        after = self._digest(RUNTIME_SOURCE)
        self.assertEqual(before, after)

    def test_advisor_knows_nothing_about_the_current_moment(self):
        """Без инъекции момента ответа нет вовсе — собственных часов у C0 не существует."""
        with self.assertRaises(TypeError):
            advisor.recommend_price(quote(), dialog(), None, None)


if __name__ == "__main__":
    unittest.main()
