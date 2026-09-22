# -*- coding: utf-8 -*-
"""test_owner_mute.py — ПОКАЗ ТОЛЬКО ПО СПИСКУ ВЛАДЕЛЬЦА (22.09.2026, задание Штаба 0015k-71f.2209).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_owner_mute -v

Наружу не уходит НИЧЕГО: `deliver` подменён счётчиком, `_api` под тестом взрывается, реестр
пишется только во временный путь (`LEDGER_FILE` подменён), боевой журнал доставки, логи гарда и
демона не читаются — строки собираются тут же.
"""
import contextlib
import inspect
import io
import os
import tempfile
import unittest
from unittest import mock

import card_ledger_pc as cl
import dispatch_notify as dn
import pretool_guard as g
import shum_opis_pc as so
from test_card_ledger_push import _NotifyHarness

_SW_MUTE, _SW_LEDGER = "OWNER_MUTE_OFF", "CARD_LEDGER_OFF"


class _MuteHarness(_NotifyHarness):

    def setUp(self):
        super().setUp()
        self.sent = []
        self._sw = mock.patch.dict(os.environ, {_SW_MUTE: "", _SW_LEDGER: ""})
        self._sw.start()

    def tearDown(self):
        self._sw.stop()
        super().tearDown()

    def _deliver(self, text, reply_markup=None, declared=None):
        self.sent.append(text)
        return super()._deliver(text, reply_markup, declared)

    def muted(self):
        return [r for r in cl.load(self.ledger) if r.get("event") == cl.EV_MUTED]


# ── список владельца один, перечни покрывают словарь гарда без пересечений ─────────────────
class TestListsAreGuardLists(unittest.TestCase):

    def test_owner_list_is_guard_top_tier(self):
        self.assertEqual(tuple(cl.OWNER_LIST_KINDS), tuple(g._TOP_TIER))

    def test_four_lists_partition_guard_vocab(self):
        parts = [set(cl.OWNER_LIST_KINDS), set(cl.VAULT_KINDS), set(cl.FAILSAFE_SHOW),
                 set(cl.MUTED_KINDS)]
        self.assertEqual(set().union(*parts), set(g._KIND_VOCAB))
        self.assertEqual(sum(len(p) for p in parts), len(set(g._KIND_VOCAB)))

    def test_flag_shared(self):
        self.assertEqual(dn.GUARD_PUSH_FLAG, cl.GUARD_PUSH_FLAG)


class TestPushShown(unittest.TestCase):

    def test_owner_list_shown_on_both_rods(self):
        for k in cl.OWNER_LIST_KINDS:
            self.assertIs(cl.push_shown(cl.ROD_GUARD, k, env={}), True, k)
            self.assertIs(cl.push_shown(cl.ROD_GUARD_TOP, k, env={}), True, k)

    def test_top_rod_shown_whatever_class(self):
        for k in cl.MUTED_KINDS + ("", "x"):
            self.assertIs(cl.push_shown(cl.ROD_GUARD_TOP, k, env={}), True, k)

    def test_muted_kinds_hidden(self):
        for k in cl.MUTED_KINDS:
            self.assertIs(cl.push_shown(cl.ROD_GUARD, k, env={}), False, k)

    def test_vault_kinds_stay_shown(self):
        for k in cl.VAULT_KINDS:
            self.assertIs(cl.push_shown(cl.ROD_GUARD, k, env={}), True, k)

    def test_unnamed_or_new_class_shown(self):
        for k in ("", "-", None, "unknown", "unknown_tool", cl.CLS_UNNAMED, "network2", "NETWORK"):
            self.assertIs(cl.push_shown(cl.ROD_GUARD, k, env={}), True, repr(k))

    def test_other_rods_not_decided(self):
        for rod in (cl.ROD_WAIT, cl.ROD_DEMON_RESTART, "прочее", ""):
            self.assertIsNone(cl.push_shown(rod, "network", env={}), rod)

    def test_rollback_switch(self):
        self.assertIs(cl.push_shown(cl.ROD_GUARD, "network", env={_SW_MUTE: "1"}), True)

    def test_muted_row_has_reason_no_text(self):
        r = cl.muted_row(cl.ROD_GUARD, cl.SRC_GUARD_PUSH, "network", "t")
        self.assertEqual(set(r), {"ts", "event", "rod", "class", "op", "src", "reason"})
        self.assertEqual((r["event"], r["class"], r["op"]), (cl.EV_MUTED, "network", True))
        self.assertTrue(r["reason"])


# ── п.2/п.4: гашение меняет адресат, след остаётся ────────────────────────────────────────
class TestMuteLeavesTrace(_MuteHarness):

    def test_muted_class_not_sent_but_traced(self):
        self.run_main([cl.GUARD_PUSH_FLAG, "red", "network", "🔴 сеть — разрешить?"])
        self.assertEqual(self.sent, [])
        m = self.muted()
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0]["rod"], m[0]["class"], m[0]["src"]),
                         (cl.ROD_GUARD, "network", cl.SRC_GUARD_PUSH))
        self.assertEqual(m[0]["reason"], cl.MUTE_REASON)
        self.assertEqual(self.rows(), [])                     # пуша не было — строки «пуш» нет
        self.assertTrue(any("погашено(гард-пуш)" in ln for ln in self.cap.lines))
        self.assertFalse(any("итог" in ln for ln in self.cap.lines))

    def test_every_muted_kind_traced(self):
        for k in cl.MUTED_KINDS:
            self.run_main([cl.GUARD_PUSH_FLAG, "red", k, "🔴 x"])
        self.assertEqual(self.sent, [])
        self.assertEqual(sorted(r["class"] for r in self.muted()), sorted(cl.MUTED_KINDS))

    def test_no_silent_mute_when_ledger_unwritable(self):
        with mock.patch.object(dn, "LEDGER_FILE", self.tmp):    # каталог вместо файла
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "network", "🔴 x"])
        self.assertEqual(len(self.sent), 1)

    def test_no_silent_mute_when_ledger_off(self):
        with mock.patch.dict(os.environ, {_SW_LEDGER: "1"}):
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "network", "🔴 x"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.muted(), [])

    def test_probe_never_writes_live_ledger_and_does_not_mute(self):
        with mock.patch.object(dn, "live_send_verdict", lambda: (False, "проба")):
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "network", "🔴 x"])
        self.assertEqual(self.muted(), [])
        self.assertEqual(len(self.sent), 1)

    def test_rollback_sends(self):
        with mock.patch.dict(os.environ, {_SW_MUTE: "1"}):
            self.run_main([cl.GUARD_PUSH_FLAG, "red", "write_outside", "🔴 x"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.muted(), [])

    def test_vault_class_still_sent(self):
        for k in cl.VAULT_KINDS:
            self.run_main([cl.GUARD_PUSH_FLAG, "red", k, "🔴 ключи"])
        self.assertEqual(len(self.sent), len(cl.VAULT_KINDS))
        self.assertEqual(self.muted(), [])


# ── п.5: ОТРИЦАТЕЛЬНЫЙ — класс ИЗ списка при включённом гашении уходит владельцу ────────────
class TestOwnerListStillDelivered(_MuteHarness):

    def test_owner_list_delivered_with_mute_on(self):
        self.run_main([cl.GUARD_PUSH_FLAG, "red", "network", "🔴 гашение включено"])
        self.assertEqual((len(self.sent), len(self.muted())), (0, 1))   # состояние создано
        n = 0
        for k in cl.OWNER_LIST_KINDS:
            for rod in ("top", "red"):
                self.run_main([cl.GUARD_PUSH_FLAG, rod, k, "⛔ ВЫСШАЯ ЦЕНА %s" % k])
                n += 1
        self.assertEqual(n, 16)
        self.assertEqual(len(self.sent), n)                      # 16 из 16 ушли
        self.assertEqual(len(self.muted()), 1)                   # погашен только network
        self.assertEqual(len(self.rows()), n)                    # и у каждого — строка «пуш»

    def test_owner_words_in_muted_class_do_not_unmute(self):
        # слова «удаление»/«ВЫСШАЯ ЦЕНА» в тексте класса вне списка показ не возвращают
        self.run_main([cl.GUARD_PUSH_FLAG, "red", "network", "⛔ ВЫСШАЯ ЦЕНА delete kill деньги"])
        self.assertEqual(self.sent, [])


# ── п.2: решение гарда не зависит от показа ────────────────────────────────────────────────
class TestGuardDecisionUntouched(unittest.TestCase):

    def test_guard_does_not_read_show_verdict(self):
        src = inspect.getsource(g)
        for name in ("push_shown", "MUTED_KINDS", _SW_MUTE, "_ledger_muted"):
            self.assertNotIn(name, src, name)

    def test_emit_ask_still_pushes_every_kind(self):
        calls = []
        with contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(g, "_push", lambda card, kind="": calls.append(kind)), \
                mock.patch.object(g, "isolated", lambda env=None: False), \
                mock.patch.dict(os.environ, {g.ASK_MARKER_ENV: ""}):
            for k in cl.MUTED_KINDS:
                with self.assertRaises(SystemExit):
                    g._emit_ask("🔴 карточка", k)
        self.assertEqual(calls, list(cl.MUTED_KINDS))


# ── опись: класс из места события ─────────────────────────────────────────────────────────
def _itog(ts, text, mid, branch="", ch="inbox"):
    b = "(%s)" % branch if branch else ""
    return "%s,100 | итог%s: channel=%s ok=True mid=%s | %s" % (ts, b, ch, mid, text)


class TestOpisPlace(unittest.TestCase):

    GUARD = ["2026-09-10 10:00:00 | interactive | Bash | ask | network | curl https://x",
             "2026-09-10 10:05:00 | interactive | Write | ask | write_outside | "
             "C:\\Users\\u\\AppData\\Local\\Temp\\a.txt",
             "2026-09-10 10:06:00 | headless | Bash | ask | delete | rm x"]
    DEMON = ["2026-09-10 11:00:00,001 ERROR self-update демона: дерево ГРЯЗНОЕ — авто-рестарт «d» ЗАПРЕЩЁН"]

    def classify(self, itog):
        return so.classify(itog, self.GUARD, self.DEMON, [], "2026-09-01 00:00:00",
                           "2026-09-30 00:00:00")

    def test_class_from_guard_not_words(self):
        rs = self.classify([_itog("2026-09-10 10:00:02", "🔴 ключи env удаление", "1")])
        self.assertEqual((rs[0]["rod"], rs[0]["cls"], rs[0]["place"]),
                         (so.ROD_GUARD, "network", "лог гарда"))

    def test_temp_bucket_from_guard_path(self):
        rs = self.classify([_itog("2026-09-10 10:05:01", "🔴 x", "2")])
        self.assertEqual(so.premise_bucket(rs[0]), "запись во временную папку Windows")

    def test_before_guard_log_is_unknown_not_suppressible(self):
        rs = self.classify([_itog("2026-09-05 10:00:00", "🔴 сеть network", "3")])
        self.assertEqual(rs[0]["cls"], so.CLS_UNKNOWN_PLACE)
        self.assertEqual(so.summary(rs)["suppressible"], 0)

    def test_demon_restart_needs_demon_line(self):
        rs = self.classify([_itog("2026-09-10 11:00:01", "⛔ Оркестратор: авто-рестарт x", "4"),
                            _itog("2026-09-10 12:00:01", "⛔ Оркестратор: авто-рестарт y", "5")])
        self.assertEqual([r["rod"] for r in rs], [so.ROD_DEMON_RESTART, so.ROD_OTHER])

    def test_one_ask_pairs_once(self):
        rs = self.classify([_itog("2026-09-10 10:00:02", "🔴 a", "6"),
                            _itog("2026-09-10 10:00:03", "🔴 b", "7")])
        self.assertEqual([r["cls"] for r in rs], ["network", "без-пары"])

    def test_opis_lists_only_muted_mids(self):
        rs = self.classify([_itog("2026-09-10 10:00:02", "🔴 a", "11"),
                            _itog("2026-09-10 10:06:01", "⛔ ВЫСШАЯ ЦЕНА", "12")])
        p = os.path.join(tempfile.mkdtemp(prefix="shum_opis_"), "o.md")
        so.write_opis(rs, p, "a", "b")
        with open(p, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("`11`", txt)
        self.assertNotIn("`12`", txt)


if __name__ == "__main__":
    unittest.main()
