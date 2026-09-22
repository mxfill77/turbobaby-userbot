# -*- coding: utf-8 -*-
"""test_card_ledger_pc.py — СЧЁТ КАРТОЧЕК И ОТВЕТОВ ВЛАДЕЛЬЦА (22.09.2026).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_card_ledger_pc -v

Боевой файл счёта не трогается: путь каждого теста — временный каталог, а константа демона под
тестом уводится `_state` в temp (это проверяется отдельно). Мост — подставной, только чтение.
"""
import os
import re
import tempfile
import unittest
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"

import card_ledger_pc as cl            # noqa: E402
import card_terminal_log as ctl        # noqa: E402
import pc_orchestrator as o            # noqa: E402

SRC = open(o.__file__, encoding="utf-8").read()


def guard_card(kind="env", obj=".env"):
    return ("NEEDS_APPROVAL (гард): 🔴 Хочу обратиться к секретам — разрешить?\nОбъект: %s\n"
            "Число: 1 файл\nКласс операции: %s" % (obj, kind))


class TestOpComesFromSourceOnly(unittest.TestCase):
    """Признак «стояла операция» поднимает ТОЛЬКО источник-гард; слово в карточке — нет."""

    def test_guard_source_is_op(self):
        self.assertIs(cl.shown_row(1, "env", cl.SRC_GUARD, "t")["op"], True)

    def test_revizor_source_is_not_op_even_with_op_words(self):
        r = cl.shown_row(2, "env", cl.SRC_REVIZOR, "t", obj="Класс операции: env")
        self.assertIs(r["op"], False)

    def test_unknown_source_is_not_op(self):
        for src in ("", "гард", "маркер", None, "SRC_GUARD"):
            self.assertIs(cl.shown_row(3, "env", src, "t")["op"], False, src)

    def test_shown_row_has_no_op_parameter(self):
        import inspect
        self.assertNotIn("op", inspect.signature(cl.shown_row).parameters)

    def test_secret_object_hidden(self):
        r = cl.shown_row(4, "env", cl.SRC_GUARD, "t", obj="D:/turbobaby-bot/.env")
        self.assertEqual(r["object"], ctl.SECRET_MARK)


class TestTally(unittest.TestCase):
    def rows(self):
        return [
            cl.shown_row(10, "env", cl.SRC_GUARD, "a"),
            cl.answer_row(10, "env", ctl.OUT_REJECTED, cl.SRC_TERMINAL, "b"),
            cl.shown_row(11, "env", cl.SRC_GUARD, "a"),
            cl.answer_row(11, "env", ctl.OUT_EXPIRED, cl.SRC_TERMINAL, "b"),
            cl.shown_row(12, "sqlite", cl.SRC_GUARD, "a"),
            cl.shown_row(40, cl.CLS_REVIZOR, cl.SRC_REVIZOR, "a"),
            cl.shown_row(40, cl.CLS_REVIZOR, cl.SRC_REVIZOR, "a2"),   # правка той же карточки
            cl.answer_row(40, cl.CLS_REVIZOR, ctl.OUT_APPROVED, cl.SRC_REVIZOR_ANSWER, "b"),
            cl.answer_row(99, "env", ctl.OUT_APPROVED, cl.SRC_TERMINAL, "b"),  # без показа
        ]

    def test_numbers_per_class(self):
        t = cl.tally(self.rows())
        self.assertEqual(t["env"], {"cards": 2, "shown": 2, "op": 2, "answered": 1,
                                    "unanswered": 1,
                                    "answers": {ctl.OUT_REJECTED: 1, ctl.OUT_EXPIRED: 1}})
        self.assertEqual((t["sqlite"]["cards"], t["sqlite"]["answered"],
                          t["sqlite"]["unanswered"]), (1, 0, 1))
        self.assertEqual((t[cl.CLS_REVIZOR]["cards"], t[cl.CLS_REVIZOR]["shown"],
                          t[cl.CLS_REVIZOR]["op"], t[cl.CLS_REVIZOR]["answered"]), (1, 2, 0, 1))

    def test_expired_is_not_an_answer(self):
        self.assertIs(cl.answer_row(1, "env", ctl.OUT_EXPIRED, cl.SRC_TERMINAL, "t")["answered"],
                      False)

    def test_classes_without_op(self):
        self.assertEqual(cl.classes_without_op(self.rows()), [cl.CLS_REVIZOR])

    def test_empty_window_says_nothing(self):
        self.assertEqual((cl.tally([]), cl.classes_without_op([])), ({}, []))

    def test_multi_kind_card_counted_once(self):
        self.assertEqual(cl.class_of(["env", "delete", "env"]), "delete, env")
        self.assertEqual(cl.class_of([]), cl.CLS_UNNAMED)


class TestFileAndSwitch(unittest.TestCase):
    def test_append_load_roundtrip_and_never_raises(self):
        p = os.path.join(tempfile.mkdtemp(prefix="turbobaby_TESTING_ledger_"), "l.jsonl")
        self.assertTrue(cl.append(p, cl.shown_row(1, "env", cl.SRC_GUARD, "t")))
        with open(p, "a", encoding="utf-8") as f:
            f.write("{битая строка\n")
        self.assertEqual(len(cl.load(p)), 1)
        self.assertFalse(cl.append(os.path.join(p, "нет", "такого"), {"x": 1}))

    def test_off_switch(self):
        self.assertTrue(cl.off({"CARD_LEDGER_OFF": "1"}))
        self.assertFalse(cl.off({}))

    def test_daemon_ledger_path_is_not_live_under_test(self):
        self.assertNotEqual(os.path.normcase(o.CARD_LEDGER_FILE),
                            os.path.normcase(os.path.join(o.REPO, "pc_orchestrator.cards_ledger.jsonl")))


class ReadOnlyBridge:
    def __init__(self, rows):
        self.rows = rows

    def get_pending(self, status, lane="pc"):
        return {"ok": True, "items": [dict(r) for r in self.rows if r["status"] == status]}


class TestDaemonWiring(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="turbobaby_TESTING_ledgerwire_")
        self.watch = os.path.join(d, "watch.json")
        self.ledger = os.path.join(d, "ledger.jsonl")
        for p in (mock.patch.object(o, "_cowork", lambda s: None),
                  mock.patch.object(o, "CARD_LEDGER_FILE", self.ledger),
                  mock.patch.dict(os.environ, {"CARD_TERMINAL_OFF": "0", "CARD_LEDGER_OFF": "0"})):
            p.start()
            self.addCleanup(p.stop)

    def test_terminal_observer_writes_answer_rows(self):
        ctl.save(self.watch, [{"task": "7", "opened": "2026-09-22T10:00:00+00:00", "object": "",
                               "kinds": ["env"]},
                              {"task": "8", "opened": "2026-09-22T10:00:00+00:00", "object": "",
                               "kinds": ["sqlite"]}])
        rows = [{"id": 7, "status": "failed", "lane": "pc", "result": o._REJECT_PREFIX + " x"}]
        with mock.patch.object(o, "bc", ReadOnlyBridge(rows)):
            o.process_card_terminals(path=self.watch)
        got = cl.load(self.ledger)
        self.assertEqual([(r["event"], r["task"], r["class"], r["answer"]) for r in got],
                         [(cl.EV_ANSWER, "7", "env", ctl.OUT_REJECTED)])

    def test_guard_shown_row_from_marker_card(self):
        o._card_ledger_shown(5, cl.SRC_GUARD, guard_card("env", ".env"))
        r = cl.load(self.ledger)[0]
        self.assertEqual((r["task"], r["class"], r["op"], r["object"]),
                         ("5", "env", True, ctl.SECRET_MARK))

    def test_off_switch_writes_nothing(self):
        with mock.patch.dict(os.environ, {"CARD_LEDGER_OFF": "1"}):
            o._card_ledger_shown(5, cl.SRC_GUARD, guard_card())
            o._card_ledger_answer(5, "env", ctl.OUT_APPROVED, cl.SRC_TERMINAL)
        self.assertEqual(cl.load(self.ledger), [])


class TestCallSites(unittest.TestCase):
    """Где пишется строка — замок по исходнику: признак операции даёт ОДНА ветка демона."""

    def test_guard_source_used_once_after_delivery_receipt(self):
        sites = [m.start() for m in re.finditer(r"_card_ledger_shown\(tid, card_ledger_pc\.SRC_GUARD", SRC)]
        self.assertEqual(len(sites), 1)
        receipt = SRC.rfind("_card_delivery_receipt(bc.set_needs_approval(tid, result))", 0, sites[0])
        self.assertGreater(receipt, 0)
        self.assertIn("if ok:", SRC[receipt:sites[0]])

    def test_revizor_shown_only_after_receipt(self):
        sites = [m.start() for m in re.finditer(r"_card_ledger_shown\(tid, card_ledger_pc\.SRC_REVIZOR", SRC)]
        self.assertEqual(len(sites), 2)
        for s in sites:
            self.assertIn("if not ok:\n", SRC[s - 120:s])

    def test_module_in_daemon_runtime_list(self):
        self.assertIn('"card_ledger_pc.py"', SRC)


if __name__ == "__main__":
    unittest.main()
