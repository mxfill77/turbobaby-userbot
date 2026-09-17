# -*- coding: utf-8 -*-
"""
test_cut_mark_scan.py — СТОРОЖ ПОМЕТКИ РЕЗА: двусторонний отрицательный тест, дерево зелёное,
метод не сужается, список известных только убывает.

Класс и правило — шапка `cut_mark_scan.py`. Задание 63-o (17.09.2026).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_cut_mark_scan -v
"""

import io
import os
import unittest

import cut_mark_scan as cm

HERE = os.path.dirname(os.path.abspath(__file__))

# Перепись 17.09: число и дата прибиты. Список вырос → этот тест красный, и рост виден диффом.
CENSUS_DATE = "2026-09-17"
CENSUS_TOTAL = 755


def _bad(src, known=None, fname="x.py"):
    cuts = cm.scan_source(src, fname)
    return cm.violations(cuts, {} if known is None else known)


def _live(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


class TestTwoSided(unittest.TestCase):
    """Намеренный рез без пометки — красный; ТОТ ЖЕ рез с пометкой — зелёный."""

    NEW_CUT = "\n\ndef _new_decider(text):\n    return 'fatal:' in text[:160]\n"

    def test_unmarked_new_cut_in_live_file_is_red(self):
        src = _live("pc_orchestrator.py") + self.NEW_CUT
        bad = cm.violations(cm.scan_source(src, "pc_orchestrator.py"), cm.load_known()["files"])
        self.assertEqual(len(bad), 1, bad)
        self.assertEqual((bad[0][1], bad[0][0].expr), (cm.VERDICT_UNMARKED, "text[:160]"))

    def test_same_cut_marked_in_live_file_is_green(self):
        for mark in ("  # рез: показ", "  # рез: решение — якорь: метка fatal: в голове строки"):
            with self.subTest(mark):
                src = _live("pc_orchestrator.py") + self.NEW_CUT.rstrip("\n") + mark + "\n"
                known = cm.load_known()["files"]
                self.assertEqual(cm.violations(cm.scan_source(src, "pc_orchestrator.py"), known),
                                 [])

    def test_decision_without_a_right_is_red(self):
        bad = _bad("def f(t):\n    return t[:10] == 'x'  # рез: решение\n")
        self.assertEqual([b[1] for b in bad], [cm.VERDICT_NO_RIGHT])

    def test_marker_on_the_line_above_counts(self):
        self.assertEqual(_bad("def f(t):\n    # рез: показ\n    return t[:10]\n"), [])

    def test_marker_on_a_code_line_above_does_not_cover(self):
        src = "def f(t):\n    a = t[:5]  # рез: показ\n    return t[:10]\n"
        self.assertEqual([b[0].line for b in _bad(src)], [3])

    def test_multiline_cut_marked_on_its_last_line(self):
        src = "def f(t):\n    return one_line(\n        t, 50)  # рез: показ\n"
        self.assertEqual(_bad(src), [])

    def test_known_cut_sleeps_and_one_more_wakes(self):
        src = "def f(t):\n    return t[:10]\n"
        known = {"x.py": {"head|t[:10]": 1}}
        self.assertEqual(_bad(src, known), [])
        self.assertEqual(len(_bad(src + "def g(t):\n    return t[:10]\n", known)), 2)

    def test_edited_known_cut_wakes(self):
        known = {"x.py": {"head|t[:10]": 1}}
        self.assertEqual(len(_bad("def f(t):\n    return t[:11]\n", known)), 1)


class TestStagedAndStale(unittest.TestCase):
    """Режим хука читает ИНДЕКС (подменой `_git_show`, живой индекс общий — не трогаем)."""

    def setUp(self):
        self._orig = cm._git_show

    def tearDown(self):
        cm._git_show = self._orig

    def test_staged_reads_index_text_not_disk(self):
        blobs = {"a.py": "def f(t):\n    return t[:10]\n",
                 "b.py": "def g(t):\n    return t[:10]  # рез: показ\n"}
        cm._git_show = lambda name, repo: (blobs[name], "")
        cuts, unreadable = cm.scan_staged(["a.py", "b.py"], HERE)
        self.assertEqual(unreadable, [])
        self.assertEqual([(b[0].file, b[1]) for b in cm.violations(cuts, {})],
                         [("a.py", cm.VERDICT_UNMARKED)])

    def test_unreadable_index_blob_is_loud(self):
        cm._git_show = lambda name, repo: (None, "git show :%s rc=128" % name)
        cuts, unreadable = cm.scan_staged(["a.py"], HERE)
        self.assertEqual((cuts, [u[0] for u in unreadable]), ([], ["a.py"]))

    def test_stale_counts_removed_known_cut(self):
        cuts = cm.scan_source("def f(t):\n    return t[:10]\n", "x.py")
        known = {"x.py": {"head|t[:10]": 1, "head|t[:99]": 2}}
        self.assertEqual(cm.stale_count(cuts, known, ["x.py"]), 2)


class TestMethodCannotBeWeakened(unittest.TestCase):

    CAUGHT = (
        ("x[:120]", cm.SIG_HEAD), ("x[:LIMIT]", cm.SIG_HEAD), ("x[:n]", cm.SIG_HEAD),
        ("x[-500:]", cm.SIG_TAIL), ("x[-n:]", cm.SIG_TAIL),
        ("one_line(x, 50)", cm.SIG_HELPER), ("_tail(x, 160)", cm.SIG_HELPER),
        ("goal_line(x)", cm.SIG_HELPER), ("textwrap.shorten(x, 30)", cm.SIG_HELPER),
        ("f.read(65536)", cm.SIG_BYTES), ("f.seek(-4096, 2)", cm.SIG_BYTES),
    )
    NOT_CAUGHT = ("x[1:]", "x[:-3]", "x[i]", "x.split(':')[0]", "x.splitlines()[-1]",
                  "f.read()", "x[::2]", "x[2:5]")

    def test_signatures_are_exactly_the_agreed_four(self):
        self.assertEqual(cm.SIGNATURES, ("head", "tail", "helper", "bytes"))

    def test_every_form_is_caught(self):
        for expr, sig in self.CAUGHT:
            with self.subTest(expr):
                cuts = cm.scan_source("v = %s\n" % expr)
                self.assertEqual([c.sig for c in cuts], [sig])

    def test_non_cuts_are_not_caught(self):
        for expr in self.NOT_CAUGHT:
            with self.subTest(expr):
                self.assertEqual(cm.scan_source("v = %s\n" % expr), ())

    def test_helpers_are_not_narrowed(self):
        for name in ("one_line", "_tail", "_tail_shown", "_last_line", "first_line",
                     "_cap_result", "_clip", "clip_named", "shown", "goal_line", "_cut_head",
                     "cut_head", "shorten"):
            self.assertIn(name, cm.CUT_HELPERS)

    def test_scope_is_tracked_root_without_tests_and_temp(self):
        files = cm.repo_files(HERE)
        self.assertIn("pc_orchestrator.py", files)
        self.assertIn("suggest.py", files)
        for name in files:
            self.assertFalse(name.startswith(("test_", "tmp", "_scratch")), name)
            self.assertNotIn("/", name)

    def test_unreadable_file_is_loud(self):
        fs = cm.scan_file(os.path.join(HERE, "нет-такого-файла.py"))
        self.assertIsNone(fs.cuts)
        broken = cm.scan_file(os.path.join(HERE, "fixtures", "rc_server_idle_poll.live.log"))
        self.assertIsNone(broken.cuts)


class TestTree(unittest.TestCase):
    """Сторож не краснеет на том, что уже лежит, и список известных не растёт."""

    @classmethod
    def setUpClass(cls):
        cls.known = cm.load_known()
        cls.cuts, cls.unreadable = cm.scan_files(cm.repo_files(HERE), HERE)

    def test_tree_has_no_new_unmarked_cut(self):
        bad = cm.violations(self.cuts, self.known["files"])
        self.assertEqual(bad, [], "\n".join("%s:%d %s %s" % (c.file, c.line, v, c.expr)
                                            for c, v, _ in bad))

    def test_scanner_is_not_blind(self):
        self.assertEqual(self.unreadable, [])
        self.assertGreater(len(self.cuts), 0)

    def test_known_list_is_the_census_and_only_shrinks(self):
        self.assertEqual(self.known["measured"], CENSUS_DATE)
        self.assertEqual(self.known["method"], "cut_mark_scan/" + ",".join(cm.SIGNATURES))
        total = sum(sum(v.values()) for v in self.known["files"].values())
        self.assertEqual(total, self.known["total"])
        self.assertLessEqual(total, CENSUS_TOTAL)


if __name__ == "__main__":
    unittest.main()
