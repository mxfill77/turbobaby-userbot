# -*- coding: utf-8 -*-
"""test_card_ledger_push.py — СЧЁТ ПУШЕЙ БЕЗ КНОПКИ (22.09.2026, задание Штаба 0015i-71c.2209).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_card_ledger_push -v

Наружу не уходит НИЧЕГО: `deliver` подменён (сеть не трогается ни одной веткой, `_api` под
тестом взрывается), реестр пишется только во временный путь (`LEDGER_FILE` подменён), боевой
журнал доставки не читается — строки журнала собираются тут же, из лога подставного процесса.
"""
import inspect
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock

import card_ledger_pc as cl
import dispatch_notify as dn
import pretool_guard as g


def _api_must_not_run(method, payload):          # сеть под тестом запрещена
    raise AssertionError("тест дошёл до сети: %s" % method)


class _Capture(logging.Handler):
    """Строки журнала доставки ровно в боевом формате (`%(asctime)s | %(message)s`)."""

    def __init__(self):
        super().__init__()
        self.lines = []
        self.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))

    def emit(self, record):
        self.lines.append(self.format(record))


class _NotifyHarness(unittest.TestCase):
    """Прогон `dispatch_notify.main` целиком: доставка подменена «ушло, номер N»."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pushi_schet_")
        self.ledger = os.path.join(self.tmp, "ledger.jsonl")
        self.cap = _Capture()
        dn._log.addHandler(self.cap)
        self.mid = 5000
        self.patches = [
            mock.patch.object(dn, "LEDGER_FILE", self.ledger),
            mock.patch.object(dn, "_api", _api_must_not_run),
            mock.patch.object(dn, "deliver", self._deliver),
            # «боевая точка входа» — ТОЛЬКО для развилки реестра; сеть закрыта подменой выше
            mock.patch.object(dn, "live_send_verdict", lambda: (True, "тест: вход боевой")),
        ]
        for p in self.patches:
            p.start()
        self._saved_last = dict(dn._LAST_SEND)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        dn._log.removeHandler(self.cap)
        dn._LAST_SEND.clear()
        dn._LAST_SEND.update(self._saved_last)

    def _deliver(self, text, reply_markup=None, declared=None):
        self.mid += 1
        dn._LAST_SEND["mid"] = self.mid
        return ("inbox", True)

    def run_main(self, argv, stdin_payload=None):
        saved_argv, saved_stdin = sys.argv, sys.stdin
        try:
            sys.argv = ["dispatch_notify.py"] + list(argv)
            sys.stdin = io.StringIO(json.dumps(stdin_payload or {}))
            with self.assertRaises(SystemExit):
                dn.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin

    def rows(self):
        return [r for r in cl.load(self.ledger) if r.get("event") == cl.EV_PUSH]


# ── п.2: признак — из места события, слово в тексте его не поднимает ─────────────────────────
class TestPushOpFromSourceOnly(unittest.TestCase):

    def test_guard_source_is_op(self):
        self.assertIs(cl.push_row(cl.ROD_GUARD, cl.SRC_GUARD_PUSH, "env", "inbox", "1", "t")["op"],
                      True)

    def test_wait_hook_has_no_sign(self):
        r = cl.push_row(cl.ROD_WAIT, cl.SRC_WAIT_HOOK, "", "inbox", "1", "t")
        self.assertIsNone(r["op"])

    def test_unknown_sources_have_no_sign(self):
        for src in ("", None, "гард", "маркер-гарда", "SRC_GUARD_PUSH", "🔴"):
            self.assertIsNone(cl.push_row(cl.ROD_GUARD, src, "env", "inbox", "1", "t")["op"], src)

    def test_row_has_no_op_and_no_text_parameter(self):
        params = list(inspect.signature(cl.push_row).parameters)
        self.assertNotIn("op", params)
        self.assertNotIn("text", params)

    def test_row_carries_no_message_text(self):
        r = cl.push_row(cl.ROD_GUARD, cl.SRC_GUARD_PUSH, "env", "inbox", "7", "t")
        self.assertEqual(set(r), {"ts", "event", "rod", "class", "op", "src", "channel", "mid"})


class TestNotifyWritesRowAfterDelivery(_NotifyHarness):

    OP_WORDS = ("🔴 Хочу обратиться к секретам — разрешить?\nКласс операции: env\n"
                "NEEDS_APPROVAL: op=env | ВЫСШАЯ ЦЕНА")

    def test_guard_push_row_has_op_class_rod_mid(self):
        self.run_main([cl.GUARD_PUSH_FLAG, "red", "env", "🔴 текст без слов"])
        rs = self.rows()
        self.assertEqual(len(rs), 1)
        self.assertEqual((rs[0]["rod"], rs[0]["class"], rs[0]["op"], rs[0]["src"], rs[0]["mid"]),
                         (cl.ROD_GUARD, "env", True, cl.SRC_GUARD_PUSH, "5001"))

    def test_guard_push_top_tier_rod(self):
        self.run_main([cl.GUARD_PUSH_FLAG, "top", "delete", "⛔ ВЫСШАЯ ЦЕНА …"])
        self.assertEqual(self.rows()[0]["rod"], cl.ROD_GUARD_TOP)

    def test_wait_hook_row_has_no_sign_even_with_op_words(self):
        self.run_main(["--hook", "notification"], {"message": self.OP_WORDS})
        rs = self.rows()
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0]["rod"], cl.ROD_WAIT)
        self.assertIsNone(rs[0]["op"])
        self.assertEqual(rs[0]["class"], cl.CLS_UNNAMED)

    def test_plain_text_with_op_words_writes_no_row(self):
        # тот же текст карточки гарда, но БЕЗ ключа места события → ни строки, ни признака
        self.run_main([self.OP_WORDS])
        self.assertEqual(self.rows(), [])

    def test_failed_delivery_writes_no_row(self):
        with mock.patch.object(dn, "deliver", lambda *a, **k: ("inbox", False)):
            dn._LAST_SEND["mid"] = None
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "env", "🔴 x"])
        self.assertEqual(self.rows(), [])

    def test_off_switch(self):
        with mock.patch.dict(os.environ, {"CARD_LEDGER_OFF": "1"}):
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "env", "🔴 x"])
        self.assertEqual(self.rows(), [])

    def test_probe_never_writes_live_ledger(self):
        # развилка «бой ⇔ проверка» в коде: без пути и при пробе — отказ, а не запись
        with mock.patch.object(dn, "live_send_verdict", lambda: (False, "проба")):
            dn._LAST_SEND["mid"] = 42
            self.assertFalse(dn._ledger_push("guard", "red", "env", "inbox", True))
        self.assertFalse(os.path.exists(self.ledger))


class TestGuardIsThePlaceOfEvent(unittest.TestCase):

    def test_flag_is_one_string_in_three_modules(self):
        self.assertEqual(g.GUARD_PUSH_FLAG, cl.GUARD_PUSH_FLAG)
        self.assertEqual(dn.GUARD_PUSH_FLAG, cl.GUARD_PUSH_FLAG)

    def test_argv_rod_comes_from_class_not_text(self):
        a = g.guard_push_argv("⛔ ВЫСШАЯ ЦЕНА в тексте", "env")
        self.assertEqual(a[2:5], [g.GUARD_PUSH_FLAG, "red", "env"])
        a = g.guard_push_argv("🔴 обычный текст", "delete")
        self.assertEqual(a[2:5], [g.GUARD_PUSH_FLAG, "top", "delete"])
        self.assertEqual(g.guard_push_argv("x", "")[4], "-")

    def test_push_spawns_sender_with_key(self):
        seen = []
        with mock.patch.object(g, "isolated", lambda *a, **k: False), \
                mock.patch.dict(os.environ, {g.NOTIFY_COUNT_ENV: ""}), \
                mock.patch.object(g.subprocess, "Popen", lambda argv, **kw: seen.append(argv)):
            g._push("🔴 карточка", "env")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1:5], [g.DNOTIFY, g.GUARD_PUSH_FLAG, "red", "env"])

    def test_emit_ask_passes_kind_to_push(self):
        src = inspect.getsource(g._emit_ask)
        self.assertIn("_push(card, kind)", src)


# ── п.4: ушло без строки → прибор говорит ОТКАЗ, а не успех ───────────────────────────────────
class TestReconcileRefusesMissingRow(_NotifyHarness):

    SINCE = "2000-01-01 00:00:00"

    def test_all_registered_is_verified(self):
        self.run_main([cl.GUARD_PUSH_FLAG, "red", "env", "🔴 a"])
        self.run_main(["--hook", "notification"], {"message": "m"})
        r = cl.reconcile(self.cap.lines, cl.load(self.ledger), self.SINCE)
        self.assertEqual((r["verdict"], r["sent"], r["registered"]), ("СВЕРЕНО", 2, 2))

    def test_sent_without_row_is_refusal(self):
        # НАМЕРЕННО: реестр недоступен (путь — каталог) → сообщение ушло, строки нет
        with mock.patch.object(dn, "LEDGER_FILE", self.tmp):
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "env", "🔴 a"])
            self.run_main(["--hook", "notification"], {"message": "m"})
        self.run_main([cl.GUARD_PUSH_FLAG, "top", "kill", "⛔ ВЫСШАЯ ЦЕНА b"])   # эта легла
        r = cl.reconcile(self.cap.lines, cl.load(self.ledger), self.SINCE)
        self.assertEqual(r["verdict"], "ОТКАЗ")
        self.assertEqual((r["sent"], r["registered"], len(r["missing"])), (3, 1, 2))

    def test_old_path_red_without_key_is_refusal(self):
        # 🔴 ушёл старым путём (без ключа места события) — строки нет → ОТКАЗ
        self.run_main(["🔴 Хочу … — разрешить?"])
        r = cl.reconcile(self.cap.lines, cl.load(self.ledger), self.SINCE)
        self.assertEqual((r["verdict"], r["sent"], r["registered"]), ("ОТКАЗ", 1, 0))

    def test_nothing_sent_is_not_success(self):
        r = cl.reconcile([], [], self.SINCE)
        self.assertEqual(r["verdict"], "НЕИЗВЕСТНО")
        self.assertEqual(cl.reconcile(["x"], [], "")["verdict"], "НЕИЗВЕСТНО")

    def test_cli_exit_code_is_refusal(self):
        with mock.patch.object(dn, "LEDGER_FILE", self.tmp):
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "env", "🔴 a"])
        lp = os.path.join(self.tmp, "dn.log")
        with open(lp, "w", encoding="utf-8") as f:
            f.write("\n".join(self.cap.lines) + "\n")
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(cl.main(["--reconcile", self.SINCE, lp, self.ledger]), 1)


# ── п.3: пары 🔔↔🔴 ────────────────────────────────────────────────────────────────────────────
def _line(ts, branch, mid, text, ok="True", ch="inbox"):
    b = "(%s)" % branch if branch else ""
    return "%s,100 | итог%s: channel=%s ok=%s mid=%s | %s" % (ts, b, ch, ok, mid, text)


class TestPairs(unittest.TestCase):

    RED = "🔴 Хочу … — разрешить? ⏎ Объект: x ⏎ Команда: git push origin main"
    BELL = "🔔 Dispatch ждёт твоего разрешения/ввода: Claude needs … ⏎ Команда: Bash: git push origin main"
    BELL_OTHER = "🔔 Dispatch ждёт твоего разрешения/ввода: … ⏎ Команда: Bash: python x.py"

    def test_proven_time_only_none(self):
        lines = [
            _line("2026-09-10 10:00:00", "", 1, self.RED),
            _line("2026-09-10 10:00:06", "notification", 2, self.BELL),        # доказанный
            _line("2026-09-10 11:00:00", "гард-пуш", 3, self.RED),
            _line("2026-09-10 11:00:30", "notification", 4, self.BELL_OTHER),  # время есть, повода нет
            _line("2026-09-10 12:00:00", "notification", 5, self.BELL),        # пары нет
            _line("2026-09-10 12:00:01", "notification", 6, self.BELL, ok="False"),  # не ушло
        ]
        self.assertEqual(cl.pair_waits(lines),
                         {"total": 3, "proven": 1, "time_only": 1, "none": 1})

    def test_one_red_pairs_once(self):
        lines = [_line("2026-09-10 10:00:00", "", 1, self.RED),
                 _line("2026-09-10 10:00:05", "notification", 2, self.BELL),
                 _line("2026-09-10 10:00:09", "notification", 3, self.BELL)]
        r = cl.pair_waits(lines)
        self.assertEqual((r["proven"], r["none"]), (1, 1))

    def test_window_edges(self):
        lines = [_line("2026-09-10 10:00:00", "", 1, self.RED),
                 _line("2026-09-10 10:01:01", "notification", 2, self.BELL)]
        self.assertEqual(cl.pair_waits(lines)["none"], 1)

    def test_rods_by_branch(self):
        self.assertEqual(cl.rod_of_itog(cl.parse_itog(_line("2026-09-10 10:00:00", "карточка цепи 5",
                                                            1, "🔴 x"))), None)
        self.assertEqual(cl.rod_of_itog(cl.parse_itog(_line("2026-09-10 10:00:00", "", 1,
                                                            "⛔ Ворота"))), None)


if __name__ == "__main__":
    unittest.main()
