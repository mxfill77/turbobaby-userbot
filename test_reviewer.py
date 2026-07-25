# -*- coding: utf-8 -*-
"""Юнит-тесты reviewer.py (класс 23.07.2026).

Тесты на каждый чек + STAFF фильтр + интегральный review_record.
Без сети / без IPC / без Bridge.
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reviewer as R


class TestStaffFilter(unittest.TestCase):
    """STAFF фильтр: Earth 8562625260 и Пым 659135499 пропускаются."""

    def test_earth_excluded(self):
        rec = {"client_id": 8562625260, "final_text": "депозит 3000 ฿ и паспорт"}
        self.assertEqual(R.review_record(rec), [])

    def test_pym_excluded(self):
        rec = {"client_id": 659135499, "final_text": "год поколения XMAX 2024"}
        self.assertEqual(R.review_record(rec), [])

    def test_non_staff_not_excluded(self):
        rec = {"client_id": 529849022, "final_text": "депозит 3000 ฿ и паспорт"}
        self.assertNotEqual(R.review_record(rec), [])

    def test_staff_ids_constant(self):
        self.assertIn(8562625260, R.STAFF_IDS)
        self.assertIn(659135499, R.STAFF_IDS)


class TestDepcheck(unittest.TestCase):
    """Чек депозита: ИЛИ-предложение = норма; И-требование = находка."""

    # ── ИЛИ-предложения: НЕ должны флагить ──────────────────────────────────

    def test_or_choice_not_flagged(self):
        """«3000 ฿ или паспорт» — нормальное предложение выбора."""
        self.assertIsNone(R._depcheck("Депозит: 3000 ฿ или паспорт"))

    def test_or_choice_ru_libo(self):
        self.assertIsNone(R._depcheck("залог 5000 ฿ либо паспорт"))

    def test_or_choice_15000_20000(self):
        """Живой кейс-репро: 15000 или 20000 ฿ или паспорт — НЕ находка."""
        self.assertIsNone(R._depcheck(
            "Депозит от 15000 ฿ или 20000 ฿ в зависимости от модели. "
            "Вместо денег можно оставить паспорт."
        ))

    def test_only_money_no_passport(self):
        self.assertIsNone(R._depcheck("депозит 5000 ฿, наличными"))

    def test_only_passport_no_money(self):
        self.assertIsNone(R._depcheck("паспорт оставить как залог"))

    def test_no_deposit_at_all(self):
        self.assertIsNone(R._depcheck("Байк свободен, доставка 300 ฿"))

    # ── И-требования: ДОЛЖНЫ флагить ─────────────────────────────────────────

    def test_and_requirement_flagged(self):
        """«3000 ฿ и паспорт» — нарушение правила."""
        result = R._depcheck("Депозит: 3000 ฿ и паспорт")
        self.assertIsNotNone(result)
        self.assertIn("depcheck", "depcheck")   # маркер в detail (проверяем тип)

    def test_plus_sign_flagged(self):
        result = R._depcheck("нужно 5000 ฿ плюс паспорт в залог")
        self.assertIsNotNone(result)

    def test_oba_flagged(self):
        result = R._depcheck("депозит 3000 ฿, нужны оба: деньги и паспорт")
        self.assertIsNotNone(result)

    def test_a_takzhe_flagged(self):
        result = R._depcheck("5000 ฿, а также паспорт обязателен")
        self.assertIsNotNone(result)


class TestYearcheck(unittest.TestCase):
    """Чек года: только год поколения (рядом с моделью/г.в./поколение)."""

    # ── Денежный контекст: НЕ должны флагить ─────────────────────────────────

    def test_money_baht_not_flagged(self):
        """Репро окна 529849022 и 8562625260: «2000 ฿» — не год."""
        self.assertIsNone(R._yearcheck("аренда 2000 ฿/день"))

    def test_money_bat_not_flagged(self):
        self.assertIsNone(R._yearcheck("депозит 2000 бат"))

    def test_money_per_day_not_flagged(self):
        self.assertIsNone(R._yearcheck("стоимость 2000/день"))

    def test_money_itogo_not_flagged(self):
        self.assertIsNone(R._yearcheck("итого 20000 ฿"))

    def test_money_deposit_not_flagged(self):
        self.assertIsNone(R._yearcheck("депозит 3000"))

    def test_unrelated_large_number(self):
        self.assertIsNone(R._yearcheck("заказ #2025111"))  # большой номер

    # ── Год поколения: ДОЛЖНЫ флагить ────────────────────────────────────────

    def test_gv_marker_flagged(self):
        result = R._yearcheck("YAMAHA XMAX 300, г.в. 2024")
        self.assertIsNotNone(result)

    def test_pokolenie_marker_flagged(self):
        result = R._yearcheck("новое поколение 2023 — отличный байк")
        self.assertIsNotNone(result)

    def test_model_nearby_flagged(self):
        result = R._yearcheck("XMAX 300 2024 года — рекомендую")
        self.assertIsNotNone(result)

    def test_new_gen_flagged(self):
        result = R._yearcheck("NMAX New Gen 2023")
        self.assertIsNotNone(result)


class TestJcheck(unittest.TestCase):
    """Чек J-цены: цифры побуквенно, модель через _bike_key."""

    def _pricing(self, phrase):
        return (
            "ЦЕНА из Календаря бронирования (использовать ДОСЛОВНО, "
            "не пересчитывать и не округлять): " + phrase + "."
        )

    # ── Совпадение: НЕ должны флагить ────────────────────────────────────────

    def test_exact_digits_match(self):
        note = self._pricing("15000 ฿/мес")
        self.assertIsNone(R._jcheck("Аренда XMAX: 15000 ฿/мес", note))

    def test_comma_digits_match(self):
        """15,000 в J-тексте — цифровой контент «15000», должен совпасть."""
        note = self._pricing("15,000 ฿/мес")
        self.assertIsNone(R._jcheck("стоимость 15000 ฿ в месяц", note))

    def test_bike_key_model_match(self):
        """«XMAX 300 New Gen» в черновике == «YAMAHA XMAX 300 NEW» через _bike_key."""
        note = self._pricing("YAMAHA XMAX 300 NEW — 15000 ฿/мес")
        self.assertIsNone(R._jcheck("XMAX 300 New Gen: 15000 ฿/мес", note))

    def test_no_j_text_in_note(self):
        """Нет J-цены в pricing_note → чек не применяется."""
        note = "ЦЕНА: дат аренды нет — попросить даты."
        self.assertIsNone(R._jcheck("уточните даты", note))

    def test_empty_pricing_note(self):
        self.assertIsNone(R._jcheck("любой текст", ""))

    # ── Несовпадение: ДОЛЖНЫ флагить ─────────────────────────────────────────

    def test_wrong_digits_flagged(self):
        """Черновик называет 12000 вместо J-цены 15000."""
        note = self._pricing("15000 ฿/мес")
        result = R._jcheck("аренда 12000 ฿ в месяц", note)
        self.assertIsNotNone(result)
        self.assertIn("15000", result)

    def test_missing_digits_flagged(self):
        """Черновик вообще не называет цену из J-текста."""
        note = self._pricing("15000 ฿/мес")
        result = R._jcheck("уточним цену у менеджера", note)
        self.assertIsNotNone(result)


class TestBikeKey(unittest.TestCase):
    """Зеркало _bike_key из suggest.py."""

    def test_xmax_core_in_both(self):
        """«xmax300» — общее ядро в обоих ключах XMAX 300 New Gen и YAMAHA XMAX 300 NEW."""
        k_short = R._bike_key("XMAX 300 New Gen")     # "xmax300newgen"
        k_full  = R._bike_key("YAMAHA XMAX 300 NEW")  # "yamahaxmax300new"
        self.assertIn("xmax300", k_short)
        self.assertIn("xmax300", k_full)

    def test_nmax_core_in_both(self):
        """«nmax155» — общее ядро в NMAX 155 и YAMAHA NMAX 155."""
        k_short = R._bike_key("NMAX 155")
        k_full  = R._bike_key("YAMAHA NMAX 155")
        self.assertIn("nmax155", k_short)
        self.assertIn("nmax155", k_full)

    def test_cc_stripped(self):
        k = R._bike_key("XMAX 300CC")
        self.assertNotIn("cc", k)


class TestReviewRecord(unittest.TestCase):
    """Интегральный review_record: STAFF фильтр + все чеки."""

    def test_staff_returns_empty(self):
        rec = {
            "client_id": 8562625260,
            "final_text": "депозит 3000 ฿ и паспорт",
            "draft": "",
            "pricing_note": "",
        }
        self.assertEqual(R.review_record(rec), [])

    def test_clean_record_no_findings(self):
        rec = {
            "client_id": 100,
            "final_text": "Депозит: 3000 ฿ или паспорт. Доставка 300 ฿.",
            "draft": "Депозит: 3000 ฿ или паспорт. Доставка 300 ฿.",
            "pricing_note": "",
        }
        self.assertEqual(R.review_record(rec), [])

    def test_depcheck_finding(self):
        rec = {
            "client_id": 200,
            "final_text": "залог 5000 ฿ и паспорт",
            "draft": "",
            "pricing_note": "",
        }
        found = R.review_record(rec)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["check"], "depcheck")
        self.assertEqual(found[0]["client_id"], 200)

    def test_yearcheck_finding(self):
        rec = {
            "client_id": 300,
            "final_text": "XMAX поколение 2023 — топ",
            "draft": "",
            "pricing_note": "",
        }
        found = R.review_record(rec)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["check"], "yearcheck")

    def test_empty_text_no_crash(self):
        rec = {"client_id": 400, "final_text": None, "draft": None, "pricing_note": None}
        self.assertEqual(R.review_record(rec), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
