# -*- coding: utf-8 -*-
"""Регресс ORCHIDLEGUARD0210: показ витка, потолок чтений, «не перечитывать вторичное», подхват.

Сеть не задевается ни разу: транспорт `bridge_http` получает инъектированный opener, паузы и
часы — виртуальные (часы витка `orch_loop_guard.G` и `time.sleep` подменены одними часами).
ВЫКЛ сверяется с ТРАНСПОРТОМ ИЗ main (`git show main:bridge_http.py`) по трассе вызовов."""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.parse
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"
os.environ["REVIZOR_PACHKA_OFF"] = "1"

import bridge_http                      # noqa: E402
import brain_writer                     # noqa: E402
import orch_loop_guard as olg           # noqa: E402
import pc_orchestrator as o             # noqa: E402
import shtab_box_run                    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXEC = "https://script.google.com/macros/s/DEPLOY/exec"
ECHO = "https://script.googleusercontent.com/macros/echo?user_content_key=K"
ENV = {"BRIDGE_URL": EXEC, "BRIDGE_TOKEN": "t"}


class Clock:
    def __init__(self, trace=None):
        self.t = 1000.0
        self.trace = trace if trace is not None else []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.trace.append(("sleep", round(float(s), 3)))
        self.t += float(s)


class Resp:
    def __init__(self, status, location=None, body=b""):
        self.status = status
        self.headers = {"Location": location} if location else {}
        self._b = body

    def read(self):
        return self._b

    def close(self):
        pass

    def getcode(self):
        return self.status


class HungEcho:
    """Первое плечо отвечает 302 на echo сразу; ВТОРОЕ ПЛЕЧО ГЛУХО: каждый open съедает свой
    таймаут целиком и падает таймаутом — сценарий «второе плечо глухо» замера 04.09."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        path = urllib.parse.urlsplit(req.full_url).path
        self.clock.trace.append(("open", req.get_method(), path, timeout))
        if path.endswith("/exec"):
            return Resp(302, ECHO)
        self.clock.t += float(timeout)
        raise socket.timeout("The read operation timed out")


class DeafFirst(HungEcho):
    """Первое плечо само глухо (как list_brain_folder 02.10 17:05: TimeoutError)."""

    def open(self, req, timeout=None):
        self.calls += 1
        self.clock.trace.append(("open", req.get_method(),
                                 urllib.parse.urlsplit(req.full_url).path, timeout))
        self.clock.t += float(timeout)
        raise socket.timeout("The read operation timed out")


class Bounce(HungEcho):
    """Echo отскакивает обратно на /exec — BridgeTransportError (02.10: 13 строк)."""

    def open(self, req, timeout=None):
        self.calls += 1
        path = urllib.parse.urlsplit(req.full_url).path
        self.clock.trace.append(("open", req.get_method(), path, timeout))
        self.clock.t += 0.5
        if path.endswith("/exec"):
            return Resp(302, ECHO)
        return Resp(302, EXEC)


class Ok(HungEcho):
    def open(self, req, timeout=None):
        self.calls += 1
        self.clock.t += 0.2
        return Resp(200, body=b'{"ok": true, "items": [], "files": [], "text": "x"}')


def _base_bridge_http():
    """Транспорт из main — эталон «байт-в-байт прежнего». Нет git → тест пропускается."""
    try:
        src = subprocess.run(["git", "show", "main:bridge_http.py"], cwd=HERE, capture_output=True,
                             timeout=30).stdout.decode("utf-8")
    except Exception:
        return None
    if "def exchange" not in src or "orch_loop_guard" in src:
        return None
    mod = types.ModuleType("bridge_http_main_ref")
    exec(compile(src, "bridge_http_main_ref", "exec"), mod.__dict__)
    return mod


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self._sleep = mock.patch.object(time, "sleep", self.clock.sleep)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()
        olg.G.begin(limit=0, clock=time.monotonic)
        olg.G.open = False


class TransportTrace(GuardCase):
    """ВЫКЛ → трасса транспорта байт-в-байт как у main; ВКЛ → чтение закрыто за потолок."""

    def _trace(self, mod, opener_cls, method="GET"):
        self.clock.trace.clear()
        self.clock.t = 1000.0
        op = opener_cls(self.clock)
        try:
            mod.request_json(EXEC, method, params={"action": "get_pending", "token": "t"},
                             payload={"action": "claim_task", "token": "t"}, timeout=90,
                             opener=op, sleeper=self.clock.sleep)
            end = "ok"
        except Exception as e:
            end = type(e).__name__
        return list(self.clock.trace) + [("end", end, round(self.clock.t - 1000.0, 3))]

    def test_off_trace_byte_for_byte_vs_main(self):
        base = _base_bridge_http()
        if base is None:
            self.skipTest("git show main:bridge_http.py недоступен")
        for cls in (HungEcho, DeafFirst, Bounce, Ok):
            for method in ("GET", "POST"):
                olg.G.begin(limit=0, clock=self.clock)
                with olg.G.reading("get_pending"):
                    new = self._trace(bridge_http, cls, method)
                ref = self._trace(base, cls, method)
                self.assertEqual(new, ref, "%s %s" % (cls.__name__, method))
        # цена одного GET на глухом втором плече при timeout=90 — 3 × (3 × 90 + 1.8) + 3.6 = 819 с:
        # ровно её потолок и обязан срезать (у main и у ветки при ВЫКЛ она одна и та же)
        olg.G.begin(limit=0, clock=self.clock)
        self.assertEqual(self._trace(bridge_http, HungEcho)[-1][2], 819.0)

    def test_on_hung_read_closed_within_budget(self):
        olg.G.begin(limit=120, clock=self.clock)
        with olg.G.reading("get_pending"):
            tr = self._trace(bridge_http, HungEcho)
        self.assertEqual(tr[-1][:2], ("end", "ReadSkipped"))
        self.assertLessEqual(tr[-1][2], 120.0 + 1e-6)
        snap = olg.G.snapshot()
        self.assertTrue(snap["tripped"])
        # следующее чтение витка — без сети вовсе
        with self.assertRaises(olg.ReadSkipped):
            with olg.G.reading("get_pending"):
                pass

    def test_on_post_never_capped(self):
        olg.G.begin(limit=5, clock=self.clock)
        with olg.G.reading("claim_task"):
            tr = self._trace(bridge_http, HungEcho, method="POST")
        self.assertTrue(all(t[3] == 90 for t in tr if t[0] == "open"), tr)


class LoopNegative(GuardCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания: зависшее плечо моста, виток демона (очередь + ящик + витрина)."""

    def _loop(self, limit, opener_cls=HungEcho):
        olg.G.begin(limit=limit, clock=self.clock)
        op = opener_cls(self.clock)
        br = o.Bridge(url=EXEC, token="t", timeout=90, opener=op,
                      witness=lambda *a, **k: None)
        t0 = self.clock.t
        out = [br.get_pending(st) for st in ("new", "approved", "needs_approval", "in_progress")]
        lister = (lambda p: brain_writer.list_folder(
            prefix=p, env=ENV, get=lambda u, prm: brain_writer._get(u, prm, opener=op)))
        reader = (lambda fid: brain_writer.read_text(
            doc_id=fid, env=ENV, get=lambda u, prm: brain_writer._get(u, prm, opener=op)))
        files, fok, fwhy = shtab_box_run.read_folder("shtab_task_", lister=lister)
        body = shtab_box_run.read_doc_text("DOCID", reader=reader)
        snap = olg.G.snapshot()
        return {"sec": self.clock.t - t0, "out": out, "folder": (fok, fwhy), "body": body,
                "snap": snap, "reason": olg.idle_reason(olg.facts_of(snap)), "calls": op.calls}

    def test_on_loop_closes_within_budget_bridge_stall(self):
        r = self._loop(300)
        self.assertLessEqual(r["sec"], 300.0 + 1e-6, r["sec"])
        self.assertEqual(r["reason"], olg.BRIDGE_STALL)
        # исход НЕИЗВЕСТНО, а не провал: форма третьего исхода потолка 04.09
        self.assertTrue(all(x.get("budget_skipped") for x in r["out"][1:]), r["out"])
        self.assertEqual(r["out"][1]["error_kind"], o.KIND_BUDGET)
        self.assertFalse(r["folder"][0])
        self.assertIn("НЕ СПРАШИВАЛИ", r["folder"][1])
        self.assertFalse(r["body"][1])

    def test_off_loop_unbounded_same_as_before(self):
        r = self._loop(0)
        # 4 GET очереди × 819 с + перечисление (3 попытки писателя) + тело — виток не ограничен
        self.assertGreater(r["sec"], 4 * 819.0)
        self.assertFalse(any(x.get("budget_skipped") for x in r["out"]))
        # показ работает и при ВЫКЛ: перечисление упало по таймауту — частное раньше общего
        self.assertEqual(r["reason"], olg.BRAIN_LIST_TIMEOUT)

    def test_in_status_not_cut_even_when_exhausted(self):
        olg.G.begin(limit=1, clock=self.clock)
        olg.G.account("get_pending", "GET", 5.0)                # потолок уже перейдён
        op = HungEcho(self.clock)
        br = o.Bridge(url=EXEC, token="t", timeout=90, opener=op, witness=lambda *a, **k: None)
        self.assertIsNone(br._in_status(7, "in_progress"))      # спросили по-настоящему
        self.assertGreater(op.calls, 0)
        self.assertTrue(all(t[3] == 90 for t in self.clock.trace if t[0] == "open"))


class BrainKnownFail(GuardCase):
    """П.3: перечисление упало — зависимые чтения мозга этого витка не делаются (ВКЛ)."""

    def _run(self, limit, cls=Bounce):
        olg.G.begin(limit=limit, clock=self.clock)
        op = cls(self.clock)
        lister = (lambda p: brain_writer.list_folder(
            prefix=p, env=ENV, get=lambda u, prm: brain_writer._get(u, prm, opener=op)))
        reader = (lambda fid: brain_writer.read_text(
            doc_id=fid, env=ENV, get=lambda u, prm: brain_writer._get(u, prm, opener=op)))
        f1 = shtab_box_run.read_folder("shtab_task_", lister=lister)
        n1 = op.calls
        f2 = shtab_box_run.read_folder("shtab_vitrina", lister=lister)
        body = shtab_box_run.read_doc_text("DOCID", reader=reader)
        node = shtab_box_run.read_node("shtab_box", reader=reader)
        return f1, f2, body, node, n1, op.calls

    def test_on_dependent_reads_skipped(self):
        f1, f2, body, node, n1, n2 = self._run(10000)
        self.assertFalse(f1[1])
        self.assertEqual(n1, n2, "после упавшего перечисления сеть не трогали")
        for why in (f2[2], body[2], node[2]):
            self.assertIn("уже упало", why)
        self.assertEqual(olg.idle_reason(olg.facts_of(olg.G.snapshot())), olg.BRIDGE_STALL)

    def test_off_dependent_reads_as_before(self):
        f1, f2, body, node, n1, n2 = self._run(0)
        self.assertFalse(f1[1])
        self.assertGreater(n2, n1, "ВЫКЛ: зависимые чтения идут как прежде")

    def test_list_timeout_reason(self):
        self._run(10000, cls=DeafFirst)
        self.assertEqual(olg.idle_reason(olg.facts_of(olg.G.snapshot())), olg.BRAIN_LIST_TIMEOUT)

    def test_cut_mid_list_no_retry_sleeps(self):
        """Срез ВНУТРИ перечисления: первая попытка — честный таймаут, вторая срезана сроком,
        третьей и её паузы нет (`no_retry`); срез отказом перечисления не считается."""
        olg.G.begin(limit=50, clock=self.clock)
        op = DeafFirst(self.clock)
        t0 = self.clock.t
        files, ok, why = shtab_box_run.read_folder("shtab_task_", lister=lambda p: (
            brain_writer.list_folder(prefix=p, env=ENV,
                                     get=lambda u, prm: brain_writer._get(u, prm, opener=op))))
        self.assertFalse(ok)
        self.assertLessEqual(self.clock.t - t0, 50.0 + 1e-6)
        self.assertEqual(op.calls, 2)
        self.assertEqual(olg.G.snapshot()["brain_fail"], "")

    def test_cut_list_is_not_brain_fail(self):
        olg.G.begin(limit=10, clock=self.clock)
        olg.G.account("get_pending", "GET", 11.0)
        files, ok, why = shtab_box_run.read_folder("shtab_task_", lister=lambda p: {"ok": True})
        self.assertFalse(ok)
        self.assertEqual(olg.G.snapshot()["brain_fail"], "")


class Reasons(unittest.TestCase):
    def test_closed_list_and_order(self):
        R = olg.idle_reason
        self.assertEqual(R({"worked": True, "bridge_fails": 3}), olg.OK)
        self.assertEqual(R({"placed": True}), olg.OK)
        self.assertEqual(R({"brain_fail": "timeout", "bridge_fails": 1}), olg.BRAIN_LIST_TIMEOUT)
        self.assertEqual(R({"tripped": True}), olg.BRIDGE_STALL)
        self.assertEqual(R({"bridge_fails": 1, "box_stop": True}), olg.BRIDGE_STALL)
        self.assertEqual(R({"read_sec": 400}), olg.BRIDGE_STALL)
        self.assertEqual(R({"box_stop": True, "owner_busy": True}), olg.SIGNAL_STOP)
        self.assertEqual(R({"stopped": True}), olg.SIGNAL_STOP)
        self.assertEqual(R({"owner_busy": True}), olg.OWNER_CARD)
        self.assertEqual(R({}), olg.NO_TASKS)
        self.assertEqual(R({"read_sec": 239.9}), olg.NO_TASKS)

    def test_line_names_reason_and_legs(self):
        g = olg.Guard(clock=Clock())
        g.begin(limit=0)
        g.account("get_pending", "GET", 3.0, socket.timeout("timed out"))
        with g.phase("box"):
            pass
        s = olg.line(g.snapshot())
        self.assertIn("idle_reason=bridge_stall", s)
        self.assertIn("get_pending 1×3.0с отказ 1(timeout:1)", s)
        self.assertIn("потолок чтений выкл", s)
        self.assertTrue(any("idle_reason=%s " % r in s for r in olg.IDLE_REASONS))

    def test_switch_parsing(self):
        self.assertEqual(olg.read_budget_sec({}), 0.0)
        self.assertEqual(olg.read_budget_sec({olg.BUDGET_ENV: "мусор"}), 0.0)
        self.assertEqual(olg.read_budget_sec({olg.BUDGET_ENV: "-5"}), 0.0)
        self.assertEqual(olg.read_budget_sec({olg.BUDGET_ENV: "300"}), 300.0)
        self.assertFalse(olg.wake_on_done({}))
        self.assertFalse(olg.wake_on_done({olg.WAKE_ENV: "true"}))
        self.assertTrue(olg.wake_on_done({olg.WAKE_ENV: "1"}))

    def test_one_guard_per_process(self):
        """Класс отката 04.09: одолженный клиент видит ТОТ ЖЕ виток, что и `__main__`."""
        self.assertIs(o._olg, sys.modules["orch_loop_guard"])
        self.assertIs(bridge_http._olg, o._olg)
        self.assertIs(shtab_box_run._olg, o._olg)

    def test_defaults_off(self):
        self.assertEqual(o.ORCH_READ_BUDGET_SEC, olg.read_budget_sec())
        self.assertEqual(o.ORCH_WAKE_ON_DONE, olg.wake_on_done())


class Wake(unittest.TestCase):
    def test_off_is_poll_sec(self):
        for worked, placed in ((True, True), (True, False), (False, True), (False, False)):
            self.assertEqual(o._wake_decision(False, worked, placed, 0, poll=60), (60, 0, ""))

    def test_on_event_shortens_and_caps(self):
        self.assertEqual(o._wake_decision(True, True, False, 0, poll=60, nap=1, cap=5)[:2], (1, 1))
        self.assertEqual(o._wake_decision(True, False, True, 2, poll=60, nap=1, cap=5)[:2], (1, 3))
        self.assertEqual(o._wake_decision(True, True, True, 5, poll=60, nap=1, cap=5)[:2], (60, 0))
        self.assertEqual(o._wake_decision(True, False, False, 3, poll=60, nap=1, cap=5),
                         (60, 0, ""))

    def test_box_force_skips_floor_only(self):
        d = tempfile.mkdtemp(prefix="orchidle_")
        tick = os.path.join(d, "tick.json")
        calls = []

        def runner(**kw):
            calls.append(kw)
            return {"acted": False, "why": "пусто"}

        o._shtab_box_write_tick(1000.0, tick)
        with mock.patch.object(o, "_shtab_box_on", return_value=True), \
                mock.patch.object(o, "_shtab_box_announce", return_value=[]):
            self.assertIsNone(o.maybe_shtab_box(now=1010.0, tick_path=tick, runner=runner))
            self.assertEqual(calls, [])
            rep = o.maybe_shtab_box(now=1020.0, tick_path=tick, runner=runner, force=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["limit"], o.SHTAB_BOX_LIMIT)
        self.assertEqual(rep["why"], "пусто")

    def test_box_facts(self):
        f = o._box_facts({"placed": [{"id": 5, "lane": "pc"}], "stop": "", "owner_busy": True})
        self.assertEqual(f, {"placed": True, "placed_pc": True, "box_stop": False,
                             "owner_busy": True})
        self.assertFalse(o._box_facts({"placed": [{"id": 6, "lane": "vps"}]})["placed_pc"])
        self.assertEqual(o._box_facts(None), {})


if __name__ == "__main__":
    unittest.main()
