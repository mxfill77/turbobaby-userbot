# -*- coding: utf-8 -*-
u"""test_accounts_door_raise.py — самоподъём агента не убивает идущее слово учёток (25.09.2026).

Находка проверки перед слиянием main ПК: демон снимает агента `taskkill /PID … /F /T` — вместе с
дочерним `accounts_run.py`, который мог уже переписать файл окружения сервера и ждать пробы. Замок
двери жил в памяти агента, демону не виден. Теперь агент кладёт отметку на диск, демон её читает:
занято → подъём откладывается пометкой на диске, главный цикл поднимает агента, когда дверь свободна.

Держит:
  1) отметку судят честно: занято ТОЛЬКО свежая «running» от живого экземпляра; всякое сомнение
     (нет файла, мусор, протухла, будущее время, чужой pid) — не занято, подъём не держим вечно;
  2) занято → агента НЕ снимаем: ни разбора, ни гейта, ни убийства; пометка отсрочки на диске;
  3) отложенный подъём идёт ровно один раз и только при свободной двери;
  4) агент и демон пишут и читают ОДИН файл (и в бою, и под тестом);
  5) главный цикл зовёт отложенный подъём, и самообновление отдаёт откату прежний коммит.
Временное — системный temp, префикс `uchetki_2509_`, не удаляется.
"""

import ast
import asyncio
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import pc_orchestrator as o

HERE = os.path.dirname(os.path.abspath(__file__))


def _tmp():
    return tempfile.mkdtemp(prefix="uchetki_2509_")


def _mark(path, **kw):
    data = {"running": True, "pid": 4242, "at": 1000.0, "act": u"учётка 2"}
    data.update(kw)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False))
    return path


def _lock(pid):
    return lambda: {"pid": pid}


class TestBusyVerdict(unittest.TestCase):
    def setUp(self):
        self.p = os.path.join(_tmp(), "accounts_door.json")

    def busy(self, now=1060.0, lock=4242):
        return o.accounts_door_busy(now, path=self.p, lock_fn=_lock(lock))

    def test_fresh_running_mark_of_the_live_agent_is_busy(self):
        _mark(self.p)
        b, why = self.busy()
        self.assertTrue(b, why)
        self.assertIn(u"учётка 2", why)

    def test_every_doubt_is_not_busy(self):
        self.assertFalse(self.busy()[0])                                   # файла нет
        cases = (
            ("free", dict(running=False)),
            ("stale", dict(at=1000.0 - o.ACCOUNTS_DOOR_MAX - 1)),
            ("future", dict(at=5000.0)),
            ("garbage pid", dict(pid="x")),
        )
        for name, kw in cases:
            with self.subTest(name):
                _mark(self.p, **kw)
                self.assertFalse(self.busy()[0])
        with io.open(self.p, "w", encoding="utf-8") as f:
            f.write(u"{не json")
        self.assertFalse(self.busy()[0])

    def test_other_agent_instance_mark_is_not_busy(self):
        _mark(self.p, pid=4242)
        self.assertFalse(self.busy(lock=5555)[0])                          # лок держит другой агент
        self.assertTrue(o.accounts_door_busy(1060.0, path=self.p, lock_fn=lambda: None)[0])  # лок слеп


class TestSelfraiseDefers(unittest.TestCase):
    def setUp(self):
        self.pending = os.path.join(_tmp(), "pending.json")
        self.boom = mock.Mock(side_effect=AssertionError("агента тронули при идущем слове учёток"))

    def raise_(self, busy):
        return o.selfraise_agent(
            "abc1234", "self-update", now=2000.0, old_commit="0d34f16",
            busy_fn=lambda now, lock_fn=None: (busy, u"идёт слово учёток «учётка 2» (1 мин)"),
            pending_path=self.pending, journal=lambda t: None,
            parse_fn=self.boom if busy else (lambda: (False, "стоп теста")),
            gate_fn=self.boom, rehearse_fn=self.boom, killer=self.boom, finder=self.boom,
            critical=lambda t: None, notifier=lambda t: None)

    def test_busy_door_defers_without_touching_the_agent(self):
        with mock.patch.object(o, "AGENT_SELFRAISE_OFF", False):
            msg = self.raise_(busy=True)
        self.assertIn(u"ОТЛОЖЕН", msg)
        self.assertIn(u"подниму сам", msg)
        self.boom.assert_not_called()
        with io.open(self.pending, encoding="utf-8") as f:
            pend = json.load(f)
        self.assertEqual((pend["pending"], pend["commit"], pend["why"], pend["old_commit"]),
                         (True, "abc1234", "self-update", "0d34f16"))

    def test_twin_free_door_goes_on_as_before(self):
        with mock.patch.object(o, "AGENT_SELFRAISE_OFF", False):
            msg = self.raise_(busy=False)
        self.assertIn(u"НЕ РАЗБИРАЕТСЯ", msg)                          # дошли до прежнего первого шага
        self.assertFalse(os.path.exists(self.pending))


class TestDeferredRaise(unittest.TestCase):
    def setUp(self):
        self.pending = os.path.join(_tmp(), "pending.json")
        self.calls = []

    def fake_raise(self, commit, why, old_commit=None):
        self.calls.append((commit, why, old_commit))
        return u"поднят"

    def run_(self, busy):
        return o.maybe_raise_deferred_agent(now=3000.0, path=self.pending,
                                            busy_fn=lambda now: (busy, u"x"), raise_fn=self.fake_raise)

    def test_no_mark_nothing_happens(self):
        self.assertIsNone(self.run_(busy=False))
        self.assertEqual(self.calls, [])

    def test_waits_while_busy_then_raises_exactly_once(self):
        o._raise_pending_write({"pending": True, "commit": "abc1234", "why": "self-update",
                                "old_commit": "0d34f16"}, self.pending)
        self.assertIsNone(self.run_(busy=True))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_(busy=False), u"поднят")
        self.assertEqual(self.calls, [("abc1234", u"self-update, отложенный", "0d34f16")])
        self.assertIsNone(self.run_(busy=False))                           # пометка снята — второго нет
        self.assertEqual(len(self.calls), 1)

    def test_broken_mark_is_not_a_raise(self):
        with io.open(self.pending, "w", encoding="utf-8") as f:
            f.write(u"{мусор")
        self.assertIsNone(self.run_(busy=False))
        self.assertEqual(self.calls, [])


class TestBothSidesMeet(unittest.TestCase):
    u"""Агент пишет, демон читает — ОДИН файл. Иначе замок вышел бы декоративным."""

    def test_production_paths_have_the_same_tail(self):
        import pc_agent as a
        tail = os.path.join("tmp", "accounts_door.json")
        self.assertTrue(a.ACCOUNTS_DOOR_FILE.endswith(tail), a.ACCOUNTS_DOOR_FILE)
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as f:
            self.assertIn(u'os.path.join(REPO, "tmp", "accounts_door.json")', f.read())

    def test_agent_door_is_seen_busy_by_the_daemon_and_free_after(self):
        import pc_agent as a
        self.assertEqual(os.path.realpath(a._door_path()), os.path.realpath(o.ACCOUNTS_DOOR_FILE))
        seen = []

        def cli(act, number=None):
            seen.append(o.accounts_door_busy(lock_fn=_lock(os.getpid()))[0])
            if number == "9":
                raise RuntimeError("дверь упала")
            return u"ответ двери"

        sent = []

        async def fake_send(context, chat_id, text):
            sent.append(text)

        with mock.patch.object(a, "_accounts_cli", cli), mock.patch.object(a, "_send", fake_send):
            asyncio.run(a._accounts_door(None, 1, "switch", "2"))
            asyncio.run(a._accounts_door(None, 1, "switch", "9"))
        self.assertEqual(seen, [True, True])                               # во время слова — занято
        self.assertFalse(o.accounts_door_busy(lock_fn=_lock(os.getpid()))[0])   # после — свободно
        self.assertIn(u"НЕ прошло", sent[1])                               # и после падения тоже


class TestWiring(unittest.TestCase):
    def test_main_loop_calls_deferred_raise_before_self_update(self):
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as f:
            src = f.read()
        i_rec = src.index(u"            maybe_reconcile_children()")
        i_def = src.index(u"            maybe_raise_deferred_agent()")
        i_su = src.index(u"            if maybe_self_update():")
        self.assertTrue(i_rec < i_def < i_su)

    def test_self_update_hands_the_old_commit_to_the_rollback(self):
        got = []
        fake = lambda commit, why, old_commit=None: got.append((commit, old_commit)) or u"ok"
        with mock.patch.object(o, "_agent_hit", return_value=True), \
                mock.patch.object(o, "_agent_closure", return_value=({"pc_agent.py"}, "")), \
                mock.patch.object(o, "_stopped", return_value=False):
            for old, want in (("472abd4", "472abd4"), ("?", None)):
                o._selfupdate_restart_children(old, "66b1617", diff_fn=lambda a, b: ["pc_agent.py"],
                                               bots_hit_fn=lambda ch: ([], []), selfraise_fn=fake,
                                               client_block_fn=lambda *a, **k: [])
                self.assertEqual(got[-1], ("66b1617", want))


if __name__ == "__main__":
    unittest.main()
