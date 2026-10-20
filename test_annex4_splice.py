# -*- coding: utf-8 -*-
"""Регресс чистых частей `annex4/build_annex.py splice` без pypdf и без PDF: какая страница у листа
байка, разбор --swap и строка оглавления. Текст оглавления — дословно то, что pypdf извлёк из
подписанного Приложения 4 (20.10.2026, стыки строк 20–23 через границу стр. 1 → 2)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "annex4"))
import build_annex as ba  # noqa: E402

# flat_text(стр. 1) + " " + flat_text(стр. 2), отрезок от VIN №20 до меток №23.
LIVE_INDEX = ("MH3SH222111010411 ✓ ✓ ✓ 21 XMAX 300CC BLUE PHUKET 5773 1 กม 5773 ภูเก็ต (Phuket) "
              "MH3SH087111025257 ✓ ✓ ✓ ANNEX 4  REGISTRATION BOOKS AND PHOTOGRAPHS | Page 1 / 78 "
              "No. รถจักรยานยนต์ ( ภาคผนวก 1) Motorcycle (Annex 1) เลขทะเบียนตามคู่มือ Reg. no. (book) "
              "เลขตัวรถ VIN หน้า ปก Cover หน้ารายการจด ทะเบียน Reg. page ภาพถ่าย Photo 22 XMAX 300CC GREEN "
              "PHUKET 4248 1 กม 4248 ภูเก็ต (Phuket) MH3SH087111041628 ○ ✓ ✓ 23 XMAX 300CC GREY PHUKET "
              "4246 1 กม 4246 ภูเก็ต (Phuket) MH3SH087111020010 ✓ ✓ ✓")


class TestBikePage(unittest.TestCase):
    def test_two_index_pages_then_books_and_photo_per_bike(self):
        self.assertEqual(ba.bike_page(1, "books"), 2)   # стр. 3
        self.assertEqual(ba.bike_page(1, "photo"), 3)   # стр. 4
        self.assertEqual(ba.bike_page(3, "photo"), 7)   # стр. 8 — №3 в r6
        self.assertEqual(ba.bike_page(25, "photo"), 51)  # стр. 52 — №25 в r6
        self.assertEqual(ba.bike_page(38, "photo"), 77)  # последняя из 78


class TestParseSwaps(unittest.TestCase):
    def test_list_with_spaces(self):
        self.assertEqual(ba.parse_swaps("3:photo, 25:photo,7:books"),
                         [(3, "photo"), (25, "photo"), (7, "books")])

    def test_bad_parts_stop_with_message_not_traceback(self):
        for bad in ("3:photo,", "", "x:photo", "1_0:photo", "3:cover", "3", "٣:photo"):
            with self.assertRaises(SystemExit, msg=bad) as cm:
                ba.parse_swaps(bad)
            self.assertIn("--swap: bad part", str(cm.exception.code))


class TestIndexRow(unittest.TestCase):
    def test_row_is_own_text_only(self):
        self.assertEqual(ba.index_row(LIVE_INDEX, "MH3SH087111020010"),
                         "23 XMAX 300CC GREY PHUKET 4246 1 กม 4246 ภูเก็ต (Phuket) MH3SH087111020010 ✓ ✓ ✓")

    def test_first_row_of_page_two_carries_page_break_text(self):
        row = ba.index_row(LIVE_INDEX, "MH3SH087111041628")
        self.assertTrue(row.startswith("ANNEX 4 "), row)
        self.assertTrue(row.endswith("22 XMAX 300CC GREEN PHUKET 4248 1 กม 4248 ภูเก็ต (Phuket) "
                                     "MH3SH087111041628 ○ ✓ ✓"), row)
        self.assertNotIn("MH3SH087111025257", row)

    def test_mark_change_changes_row(self):
        changed = LIVE_INDEX.replace("MH3SH087111041628 ○ ✓ ✓", "MH3SH087111041628 ✓ ✓ ✓")
        self.assertNotEqual(ba.index_row(LIVE_INDEX, "MH3SH087111041628"),
                            ba.index_row(changed, "MH3SH087111041628"))
        self.assertEqual(ba.index_row(LIVE_INDEX, "MH3SH087111020010"),
                         ba.index_row(changed, "MH3SH087111020010"))

    def test_reg_no_change_changes_row(self):
        changed = LIVE_INDEX.replace("4246 1 กม 4246", "4246 1 กม 9999")
        self.assertIn("9999", ba.index_row(changed, "MH3SH087111020010"))

    def test_unknown_vin_or_row_without_marks(self):
        self.assertIsNone(ba.index_row(LIVE_INDEX, "MLHNF1478R5202471"))
        self.assertIsNone(ba.index_row("MH3SH087111020010 ✓ ✓", "MH3SH087111020010"))


class TestWhereDiffer(unittest.TestCase):
    def test_points_at_first_difference(self):
        out = ba.where_differ("Make/model: HONDA CB300R · VIN", "Make/model: HONDA CB650R · VIN", ctx=8)
        self.assertIn("CB300R", out)
        self.assertIn("CB650R", out)

    def test_prefix_difference(self):
        out = ba.where_differ("abc", "abcdef", ctx=2)
        self.assertIn("fresh:  …bcde…", out)


if __name__ == "__main__":
    unittest.main()
