"""Регресс `node_age_pc` — возраст перед содержимым (задание 63-r, 17.09.2026).

Стенд ПОДСТАВЛЯЕТ чтение: мост, папка мозга и боевые узлы не трогаются ни одним тестом. Тройной
отрицательный тест (п.3 задания) — :class:`TestTriple`: свежий узел показан и несёт время; тот же
узел с состаренным штампом содержимого не показывает; испорченный штамп даёт третий исход. Он же
прогнан через оба места полосы — витрину (A5) и `brain_writer --probe` (A6).
"""
from __future__ import annotations

import ast
import calendar
import io
import os
import sys
import time
import unittest
from unittest import mock

import node_age_pc as na

HERE = os.path.dirname(os.path.abspath(__file__))


def _ts(s):
    return float(calendar.timegm(time.strptime(s, "%Y-%m-%d %H:%M")))


NOW = _ts("2026-09-17 14:40")
BODY = "ЖДЁТ ВЛАДЕЛЬЦА\n• #49 секретное-содержимое-витрины"


def _pulse_pc(stamp):
    return "ВИТРИНА ПОЛОСЫ ПК · одно сообщение\nчисла менялись последний раз: %s UTC\n\n%s" % (stamp, BODY)


FRESH_TEXT = _pulse_pc("2026-09-17 14:11")          # 29 мин при пороге 58
AGED_TEXT = _pulse_pc("2026-09-17 12:11")           # тот же узел, штамп состарен на 2 ч → 149 мин
BROKEN_TEXT = _pulse_pc("2026-13-45 25:61")         # штамп на месте, но разобрать нечего


class TestTriple(unittest.TestCase):
    """П.3: свежо → показано со временем; состарено → содержимого нет; испорчено → третий исход."""

    def test_fresh_is_shown_and_carries_time(self):
        got = na.decide("pulse_pc", FRESH_TEXT, NOW)
        self.assertEqual(got["outcome"], na.FRESH)
        self.assertTrue(got["show"])
        self.assertIn("снято 2026-09-17 14:11 UTC", got["head"])
        self.assertIn("29 мин назад", got["head"])
        shown = na.render("pulse_pc", FRESH_TEXT, NOW)
        self.assertTrue(shown.startswith("pulse_pc · снято 2026-09-17 14:11 UTC"), shown)
        self.assertIn("секретное-содержимое-витрины", shown)

    def test_aged_stamp_hides_content(self):
        got = na.decide("pulse_pc", AGED_TEXT, NOW)
        self.assertEqual(got["outcome"], na.STALE)
        self.assertFalse(got["show"])
        self.assertIn("УСТАРЕЛ", got["head"])
        self.assertIn("порог 58 мин", got["head"])
        shown = na.render("pulse_pc", AGED_TEXT, NOW)
        self.assertNotIn("секретное-содержимое-витрины", shown)
        self.assertIn("содержимое не показано", shown)

    def test_broken_stamp_is_third_outcome_not_fresh(self):
        got = na.decide("pulse_pc", BROKEN_TEXT, NOW)
        self.assertEqual(got["outcome"], na.UNKNOWN)
        self.assertNotEqual(got["outcome"], na.FRESH)
        self.assertFalse(got["show"])
        self.assertIn("возраст pulse_pc неизвестен", got["head"])
        self.assertNotIn("секретное-содержимое-витрины", na.render("pulse_pc", BROKEN_TEXT, NOW))

    def test_missing_stamp_is_third_outcome_too(self):
        got = na.decide("pulse_pc", BODY, NOW)
        self.assertEqual(got["outcome"], na.UNKNOWN)
        self.assertFalse(got["show"])

    def test_triple_through_brain_writer_probe(self):
        import brain_writer as bw
        for text, show, mark in ((FRESH_TEXT, True, "снято 2026-09-17 14:11 UTC"),
                                 (AGED_TEXT, False, "УСТАРЕЛ"),
                                 (BROKEN_TEXT, False, "неизвестен")):
            out = []
            with mock.patch.object(bw, "read_text", lambda **_kw: text), \
                    mock.patch.object(bw, "_out", out.append), \
                    mock.patch.object(na.time, "time", lambda: NOW):
                rc = bw.main(["--probe", "--name", "KB_PULSE_PC"])
            self.assertEqual(rc, 0)
            self.assertIn(mark, out[0], "первая строка показа — возраст")
            self.assertEqual("секретное-содержимое-витрины" in "\n".join(out), show, out)

    def test_triple_through_vitrina_read_shtab(self):
        import test_vitrina_pc as tv
        import vitrina_pc_run as run
        doc = "дата=%s\n[[СЕЙЧАС]]\nсекретное-содержимое-витрины\n[[ЗАСТРЯЛО]]\nб\n[[КУДА ИДЁМ]]\nв"
        spec = dict(na.NODES["shtab_vitrina"], limit=3 * 86400)   # стенд: порог подставлен
        with mock.patch.dict(na.NODES, {"shtab_vitrina": spec}):
            fresh = run.read_shtab(reader=lambda _i: doc % "2026-09-16",
                                   lister=tv._folder([tv._FILE]), now=NOW)
            aged = run.read_shtab(reader=lambda _i: doc % "2026-09-09",
                                  lister=tv._folder([tv._FILE]), now=NOW)
            broken = run.read_shtab(reader=lambda _i: doc % "2026-19-99",
                                    lister=tv._folder([tv._FILE]), now=NOW)
        self.assertTrue(fresh["ok"])
        self.assertIn("секретное-содержимое-витрины", run.vp.shtab_words(fresh, "now", "2026-09-17"))
        self.assertTrue(run.vp.shtab_words(fresh, "now", "2026-09-17").startswith(
            "[Штаб обновлял 2026-09-16, 1 сут назад]"))
        for bad, mark in ((aged, "УСТАРЕЛ"), (broken, "неизвестен")):
            self.assertFalse(bad["ok"])
            self.assertIn(mark, bad["why"])
            self.assertNotIn("секретное-содержимое-витрины",
                             run.vp.shtab_words(bad, "now", "2026-09-17"))


class TestTable(unittest.TestCase):
    """Пороги — только замеренные, и число совпадает с замером."""

    def test_measured_limits(self):
        self.assertEqual(na.NODES["pulse"]["limit"], 3074 * 60)
        self.assertEqual(na.NODES["pulse_pc"]["limit"], 58 * 60)
        self.assertEqual(na.NODES["cowork_log"]["limit"], 162 * 60)
        self.assertEqual(na.NODES["cc_log"]["limit"], 2233 * 60)

    def test_unmeasured_go_without_refusal(self):
        for node in ("shtab_vitrina", "queue_state", "queue_state_pc"):
            self.assertIsNone(na.NODES[node]["limit"], node)

    def test_every_node_names_where_its_number_came_from(self):
        for node, spec in na.NODES.items():
            self.assertTrue(spec["why"].strip(), node)
            self.assertEqual(spec["stamp"].groups, 1, node)

    def test_module_is_pure(self):
        tree = ast.parse(open(os.path.join(HERE, "node_age_pc.py"), encoding="utf-8").read())
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                names.add(n.module)
        self.assertEqual(names, {"calendar", "math", "re", "time"})


class TestNodes(unittest.TestCase):

    def test_pulse_live_case_is_stale(self):
        text = "2026-09-09 11:27 | 🟢 | разведка отбора devbot"
        got = na.decide("pulse", text, _ts("2026-09-17 12:43"))
        self.assertEqual(got["outcome"], na.STALE)
        self.assertIn("8 сут 1 ч", got["head"])

    def test_journal_takes_freshest_stamp_not_first_line(self):
        text = ("NOTE Orchestrator: без штампа\nDONE 2026-09-17 12:00 UTC: старое\n"
                "DONE 2026-09-17 14:30 UTC: свежее\nASK 2026-09-16 10:00 UTC: давно")
        got = na.decide("cowork_log", text, NOW)
        self.assertEqual(got["outcome"], na.FRESH)
        self.assertIn("снято 2026-09-17 14:30 UTC", got["head"])

    def test_queue_snapshot_age_only(self):
        text = "СЛЕПОК\nснято: 2026-09-17 09:42:05 UTC   ← ВОЗРАСТ СЧИТАТЬ ОТ ЭТОГО ВРЕМЕНИ\n" + BODY
        got = na.decide("queue_state_pc", text, NOW)
        self.assertEqual(got["outcome"], na.NOT_JUDGED)
        self.assertTrue(got["show"])
        self.assertIn("снято 2026-09-17 09:42:05 UTC · 4 ч 57 мин назад", got["head"])
        self.assertIn("порог не замерен", got["head"])

    def test_unverified_snapshot_header_is_read_too(self):
        text = "СЛЕПОК\nНЕ СВЕРЕНО: мост\nпоследний верный снимок: 2026-09-17 14:00:00 UTC (40 мин назад)"
        self.assertEqual(na.stamp_of("queue_state_pc", text, NOW)[0], _ts("2026-09-17 14:00"))

    def test_day_stamp_counts_age_from_end_of_day(self):
        got = na.decide("shtab_vitrina", "дата=2026-09-16\n[[СЕЙЧАС]]\nа", NOW)
        self.assertEqual(got["age"], NOW - _ts("2026-09-17 00:00"))
        self.assertIn("известны только сутки", got["head"])
        self.assertIn("(не меньше)", got["head"])

    def test_future_stamp_is_unknown(self):
        got = na.decide("pulse_pc", _pulse_pc("2026-09-18 14:11"), NOW)
        self.assertEqual(got["outcome"], na.UNKNOWN)
        self.assertFalse(got["show"])

    def test_node_outside_table_is_not_judged_and_shown(self):
        got = na.decide("index", "справочник", NOW)
        self.assertEqual(got["outcome"], na.NOT_JUDGED)
        self.assertTrue(got["show"])
        self.assertIn("вне таблицы", na.age_line("index", "справочник", NOW))


class TestMeasure(unittest.TestCase):

    def test_gap_stats_nearest_rank(self):
        stamps = [i * 60.0 for i in range(101)] + [101 * 60.0 + 3600]
        st = na.gap_stats(stamps)
        self.assertEqual(st["gaps"], 101)
        self.assertEqual(st["p99"], 60.0)
        self.assertEqual(st["max"], 3660.0)
        self.assertEqual(na.limit_from(st), 60)

    def test_small_sample_gives_no_limit(self):
        st = na.gap_stats([0, 4165, 110168, 114333])
        self.assertIsNone(na.limit_from(st))
        self.assertIsNone(na.gap_stats([5]))


if __name__ == "__main__":
    unittest.main()
