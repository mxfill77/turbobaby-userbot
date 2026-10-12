# -*- coding: utf-8 -*-
"""
test_tg_feed_names.py — З3 по TGPLAN0510 (NIGHT0710, блок Б4): лента Telegram слита с main ПК,
в ряду ленты — ник и имя КЛИЕНТА.

Живого Telegram здесь нет: события — настоящие объекты Telethon, связанные с клиентом-двойником
`StrictClient` (любой публичный метод = вызов Telegram, записывается и падает) — те же двойники, что в
test_tg_feed.py, они берутся оттуда, а не копируются. Лента пишется во временный файл, пушер ходит в
подделку двери `Door` из test_tg_feed_push.py. Люди, id и тексты выдуманы.

Что стережём (требование задания → класс):
  • слияние ветки ленты с main: одна функция пушера, место вызова в обороте демона, оба класса
    тестов из конфликта на месте, маркеров конфликта нет                       → TestMergeShape;
  • ник и имя в ряду: клиента в ОБОИХ направлениях (у исходящего — не наш аккаунт), из памяти при
    коротком апдейте, «ника нет» = null, «неизвестно» = ключа нет, сбой поля ряд не роняет,
    ни одного нового вызова Telegram, голден вызовов suggest прежний             → TestNamesInRow;
  • строка ленты: ряд без ника и имени — байт-в-байт прежний, порядок ключей,
    разрывы строки в имени экранированы, чужие ключи в строку не идут            → TestLineBytes;
  • пушер: смешанная лента (старые ряды и новые) уходит на сервер как есть      → TestPushMixedFeed;
  • регрессии второго круга ночи (выжившие мутанты F03/R06, P04 и поломки красной команды Б2–Б5):
    нестроковое поле — null только у него, коллекционный ник, символ вне UTF-8 не теряет ряд, имя
    без потолка не встаёт пушер, ключ сверки ответа с направлением, оба класса конфликта собираются
    целиком                              → классы раздела 5 и TestMergeShape.
Сверка с серверной половиной tg-core-0510 — контрактной пробой вне дерева (tmp/night_0710/tgz3_aux).
"""

import ast
import io
import json
import os
import re
import unittest

from telethon.tl import types as T

import test_tg_feed as F          # двойники и основа ленты (StrictClient, LogRec, mk_msg, _Base)
import test_tg_feed_push as PT    # подделка двери и основа пушера (Door, _Base)
import tg_feed
import tg_feed_push as P
import userbot_listen as ub

HERE = os.path.dirname(os.path.abspath(__file__))

CLIENT_FULL = T.User(id=1003, username="Ivan_P", first_name="  Иван ", last_name="Петров",
                     access_hash=13)


class _FakePeer:
    """Собеседник без Telethon: только поля, которые читает лента."""

    def __init__(self, uid, username=None, first_name=None, last_name=None):
        self.id = uid
        self.username = username
        self.first_name = first_name
        self.last_name = last_name


class _BrokenPeer:
    """Собеседник, у которого поле ника бросает (кривой объект) — ряд обязан выжить."""

    id = 1001
    first_name = "Анна"

    @property
    def username(self):
        raise RuntimeError("поле не читается")


# ─────────────────────── 1. слияние ветки ленты с main ───────────────────────

class TestMergeShape(unittest.TestCase):
    def _src(self, name):
        with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
            return f.read()

    def test_one_pusher_function_and_one_call_in_poll_once(self):
        """Функция пушера одна; в обороте — ровно одна проверка его часов (`_tg_push_clock`, 13.10)."""
        tree = ast.parse(self._src("pc_orchestrator.py"))
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_tg_feed_push"]
        self.assertEqual(len(defs), 1, "функция пушера в демоне должна быть ровно одна")
        poll = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "poll_once"][0]
        calls = []
        for st in poll.body:
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) \
                    and isinstance(st.value.func, ast.Name):
                calls.append(st.value.func.id)
        # 13.10.2026 (b5_tglag): пушер зовут свои часы; в обороте — одна проверка часов на прежнем месте
        self.assertEqual(calls.count("_tg_feed_push"), 0, "пушер снова зовётся из витка напрямую")
        self.assertEqual(calls.count("_tg_push_clock"), 1)
        i = calls.index
        self.assertLess(i("_chatlog_capture"), i("_tg_push_clock"))
        self.assertLess(i("_tg_push_clock"), i("_queue_snapshot"))
        self.assertEqual(calls[-1], "_write_heartbeat", "heartbeat — последняя строка оборота")

    def test_both_conflict_classes_kept(self):
        tree = ast.parse(self._src("test_pc_orchestrator.py"))
        names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        self.assertEqual(names.count("TestBoxFirst"), 1, "класс main (BOXFIRST0410) потерян")
        self.assertEqual(names.count("TestTgFeedPush"), 1, "класс ветки (TGPUSHPC0310) потерян")

    # Имена тестов обоих классов конфликта: TestBoxFirst — как в main 9e0acfa2, TestTgFeedPush — как в
    # ветке 31a68f92 плюс два замка выключателя (07.10, NIGHT0710: выжившие мутанты K11 и K24).
    # test_pc_orchestrator этой ночью не исполнялся (спавнит живой мост), поэтому «оба класса целиком
    # собираются загрузчиком» держит только эта проверка (красная команда NIGHT0710 Б5: потеря метода и
    # «класс не TestCase» проходили мимо счёта имён классов).
    CONFLICT_TESTS = {
        "TestBoxFirst": (
            "test_terminal_and_pc_document_box_before_e_and_start_in_the_same_turn",
            "test_the_report_names_start_and_held_steps", "test_empty_box_keeps_the_old_order",
            "test_e_past_its_bound_goes_before_the_box", "test_unreadable_tick_means_the_bound_is_out",
            "test_late_steps_keep_their_old_mutual_order", "test_signal_stop_keeps_the_old_order",
            "test_stop_file_keeps_the_old_order", "test_env_zero_keeps_the_old_order",
            "test_only_a_server_document_changes_nothing_for_the_pc_turn", "test_no_terminal_no_hop",
            "test_box_pause_means_no_start", "test_chain_goes_on_while_the_box_feeds_and_stops_on_its_pause",
            "test_self_update_takes_the_process_before_the_start",
            "test_a_failing_tail_step_does_not_cancel_the_start", "test_bounds_come_from_the_steps_code",
            "test_f_bound_follows_its_queue", "test_wiring_terminal_mark_and_loop_place",
            "test_watchdog_sees_no_false_alarm_on_the_worst_measured_hop"),
        "TestTgFeedPush": (
            "test_switch_off_means_zero_calls", "test_switch_on_spawns_once_with_timeout",
            "test_unittest_guard_without_runner", "test_loud_lines_go_to_error_and_timeout_is_caught",
            "test_poll_once_order_and_no_import", "test_switch_off_no_spawn_on_daemon_path",
            "test_switch_is_read_only_in_the_pusher"),
    }

    def test_both_conflict_classes_are_collected_whole(self):
        tree = ast.parse(self._src("test_pc_orchestrator.py"))
        bound = []
        for n in tree.body:
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.append(n.name)
            for t in getattr(n, "targets", []):
                bound += [x.id for x in ast.walk(t) if isinstance(x, ast.Name)]
        for name, want in self.CONFLICT_TESTS.items():
            with self.subTest(name):
                self.assertEqual(bound.count(name), 1, "имя класса затенено или повторено")
                (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name]
                self.assertEqual([ast.unparse(b) for b in cls.bases], ["unittest.TestCase"],
                                 "класс не TestCase — загрузчик его не соберёт")
                got = [f.name for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
                tests = [g for g in got if g.startswith("test")]
                self.assertEqual(len(got), len(set(got)), "метод затенён повтором имени")
                self.assertEqual(sorted(tests), sorted(want), "метод класса потерян или переименован")
        last = tree.body[-1]
        self.assertIsInstance(last, ast.If)
        self.assertIn("__name__", ast.unparse(last.test))

    def test_no_conflict_markers_in_merged_files(self):
        rx = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)
        for name in (".gitignore", "pc_orchestrator.py", "suggest.py", "test_pc_orchestrator.py",
                     "test_tg_feed.py", "test_tg_feed_push.py", "tg_feed.py", "tg_feed_push.py",
                     "userbot_listen.py"):
            with self.subTest(name):
                self.assertIsNone(rx.search(self._src(name)))

    def test_userbot_imports_feed_not_pusher(self):
        src = self._src("userbot_listen.py")
        self.assertIn("import tg_feed", src)
        self.assertNotIn("import tg_feed_push", src)
        # по разбору, а не по тексту (07.10, NIGHT0710: «import tg_feed, tg_feed_push» текст не видел)
        mods = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods.add(n.module or "")
                mods |= {a.name for a in n.names}
        self.assertIn("tg_feed", mods)
        self.assertNotIn("tg_feed_push", mods)
        self.assertNotIn("tg_feed_push", {n.value for n in ast.walk(ast.parse(src))
                                          if isinstance(n, ast.Constant) and isinstance(n.value, str)})


# ─────────────────────── 2. ник и имя в ряду ───────────────────────

class TestNamesInRow(F._Base):
    def test_incoming_row_carries_client_nick_and_name(self):
        F.run(ub.on_incoming(F.incoming(self.client, F.CLIENT, mid=10, text="байк?")))
        (r,) = self.rows()
        self.assertEqual((r["username"], r["name"]), ("anna_test", "Анна"))
        self.assertEqual(r["sender_id"], F.CLIENT.id)
        self.no_telegram()

    def test_outgoing_row_names_the_client_not_our_account(self):
        F.run(ub.on_outgoing(F.outgoing(self.client, F.CLIENT, mid=21, text="Есть.")))
        (r,) = self.rows()
        self.assertEqual((r["username"], r["name"]), ("anna_test", "Анна"))
        self.assertEqual(r["sender_id"], F.ME.id, "sender_id исходящего — по-прежнему наш аккаунт")
        self.assertNotEqual(r["username"], F.ME.username)
        self.no_telegram()

    def test_outgoing_short_update_takes_names_from_memory(self):
        F.run(ub.on_incoming(F.incoming(self.client, F.CLIENT, mid=10, text="Цена?")))
        F.run(ub.on_outgoing(F.outgoing(self.client, F.CLIENT, mid=11, with_entity=False, text="1200")))
        self.assertEqual([(r["dir"], r["username"], r["name"]) for r in self.rows()],
                         [("in", "anna_test", "Анна"), ("out", "anna_test", "Анна")])
        self.no_telegram()

    def test_no_nick_is_null_and_present(self):
        F.run(ub.on_incoming(F.incoming(self.client, F.CLIENT_NONICK, mid=11, text="hi")))
        (r,) = self.rows()
        self.assertIn("username", r, "«ника нет» — это null, а не отсутствие ключа")
        self.assertIsNone(r["username"])
        self.assertEqual(r["name"], "Без ника")

    def test_first_and_last_name_joined_and_trimmed(self):
        F.run(ub.on_incoming(F.incoming(self.client, CLIENT_FULL, mid=12, text="hello")))
        (r,) = self.rows()
        self.assertEqual((r["username"], r["name"]), ("Ivan_P", "Иван Петров"))
        self.no_telegram()

    def test_row_level_name_forms(self):
        msg = F.mk_msg(5, 1001, text="x")
        cases = [
            (_FakePeer(1001, None, None, "Сидорова"), (None, "Сидорова")),
            (_FakePeer(1001, "", " ", ""), (None, None)),
            (_FakePeer(1001, "nick", "  ", None), ("nick", None)),
        ]
        for peer, want in cases:
            with self.subTest(want):
                r = tg_feed.row(msg, 1001, tg_feed.DIR_IN, 1001, peer=peer)
                self.assertEqual((r["username"], r["name"]), want)

    def test_broken_peer_row_survives_names_unknown(self):
        r = tg_feed.row(F.mk_msg(6, 1001, text="x"), 1001, tg_feed.DIR_IN, 1001, peer=_BrokenPeer())
        self.assertEqual(list(r), list(tg_feed.FIELDS), "сбой поля — ряд цел, ключей ника/имени нет")

    def test_foreign_peer_gives_no_names(self):
        """Собеседник не того чата (наш аккаунт у исходящего) — ник и имя не пишутся вовсе."""
        r = tg_feed.row(F.mk_msg(7, 1001, out=True, text="x"), 1001, tg_feed.DIR_OUT, F.ME.id, peer=F.ME)
        self.assertEqual(list(r), list(tg_feed.FIELDS))

    def test_no_peer_keeps_old_row(self):
        r = tg_feed.row(F.mk_msg(8, 1001, text="x"), 1001, tg_feed.DIR_IN, 1001)
        self.assertEqual(list(r), list(tg_feed.FIELDS))

    def test_suggest_golden_and_zero_telegram_calls(self):
        self.assertEqual(F.suggest_trace(ub, F.StrictClient), F.GOLDEN_SUGGEST_TRACE)


# ─────────────────────── 3. строка ленты ───────────────────────

class TestLineBytes(unittest.TestCase):
    OLD = {"chat_id": 1, "msg_id": 2, "dir": "in", "ts": 3, "kind": "text", "text": "x",
           "reply_to": None, "sender_id": 1}

    def test_old_row_bytes_unchanged(self):
        want = ('{"chat_id":1,"msg_id":2,"dir":"in","ts":3,"kind":"text","text":"x",'
                '"reply_to":null,"sender_id":1}\n')
        self.assertEqual(tg_feed.line(self.OLD), want)

    def test_new_row_order_and_nulls(self):
        rec = dict(self.OLD, username=None, name="Анна")
        s = tg_feed.line(rec)
        self.assertTrue(s.endswith(',"sender_id":1,"username":null,"name":"Анна"}\n'), s)
        self.assertEqual(list(json.loads(s)), list(tg_feed.FIELDS) + list(tg_feed.OPTIONAL))

    def test_foreign_keys_do_not_reach_the_line(self):
        s = tg_feed.line(dict(self.OLD, foo="bar", username="nick"))
        self.assertNotIn("foo", json.loads(s))
        self.assertEqual(json.loads(s)["username"], "nick")

    def test_line_breakers_in_name_are_escaped(self):
        rec = dict(self.OLD, username="a", name="Анна Мария\nЛи\x85")
        s = tg_feed.line(rec)
        self.assertEqual(len(s[:-1].splitlines()), 1, "имя порвало ряд на строки")
        self.assertEqual(json.loads(s)["name"], "Анна Мария\nЛи\x85")


# ─────────────────────── 4. пушер: смешанная лента ───────────────────────

class TestPushMixedFeed(PT._Base):
    def test_old_and_new_rows_go_as_is(self):
        old = PT.mk_row(1001, 1)
        new = dict(PT.mk_row(1001, 2), username="anna_test", name="Анна")
        nick_null = dict(PT.mk_row(1002, 3), username=None, name="Без ника")
        data = self.write_feed(old, new, nick_null)
        door = PT.Door()
        self.assertEqual(self.tick(door), P.RC_OK)
        self.assertEqual(door.calls[0][1]["rows"], [old, new, nick_null])
        self.assertEqual(self.state()["offset"], len(data))
        self.assert_cursor_consistent()


# ─────────────── 5. регрессии ночи NIGHT0710: выжившие мутанты и поломки красной команды ───────────────

class TestNonStringFieldIsOnlyThatField(unittest.TestCase):
    """Не-строковое поле собеседника — null ТОЛЬКО у этого поля, остальные поля пишутся, ряд цел
    (выжившие мутанты F03 агента мутантов — «is None» вместо проверки типа — и R06 красной команды —
    нестрока проходит в ряд как есть)."""

    def _row(self, peer):
        return tg_feed.row(F.mk_msg(5, 1001, text="x"), 1001, tg_feed.DIR_IN, 1001, peer=peer)

    def _names(self, peer):
        r = self._row(peer)
        return r.get("username", "<нет ключа>"), r.get("name", "<нет ключа>")

    def test_numeric_nick_is_null_name_kept(self):
        self.assertEqual(self._names(_FakePeer(1001, 12345, "Анна", None)), (None, "Анна"))

    def test_bytes_first_name_keeps_nick_and_last_name(self):
        self.assertEqual(self._names(_FakePeer(1001, "anna_test", b"\xd0", "Петрова")),
                         ("anna_test", "Петрова"))

    def test_non_string_values_never_reach_the_line(self):
        for bad in (12345, 1.5, True, b"anna", ["a"], {"a": 1}):
            with self.subTest(bad=repr(bad)):
                r = self._row(_FakePeer(1001, bad, bad, bad))
                self.assertEqual((r["username"], r["name"]), (None, None))
                self.assertEqual(json.loads(tg_feed.line(r))["username"], None)


class TestCollectibleNick(F._Base):
    """Коллекционный ник (красная команда NIGHT0710 Б2): у клиента `username` пуст, а ник есть в
    `usernames` — ряд не смеет утверждать «ника нет» (null). Telegram ради этого не зовётся."""

    def _one(self, user):
        F.run(ub.on_incoming(F.incoming(self.client, user, mid=10, text="x")))
        (r,) = self.rows()
        self.no_telegram()
        return r

    def test_collectible_only_nick_is_the_nick(self):
        u = T.User(id=1701, username=None, first_name="Колл", access_hash=17,
                   usernames=[T.Username(username="collect_nick", active=True, editable=False)])
        r = self._one(u)
        self.assertEqual((r["username"], r["name"]), ("collect_nick", "Колл"))

    def test_first_active_wins_inactive_skipped(self):
        u = T.User(id=1702, username=None, first_name="Два", access_hash=18,
                   usernames=[T.Username(username="old_nick", active=False, editable=True),
                              T.Username(username="new_nick", active=True, editable=False),
                              T.Username(username="third_nick", active=True, editable=False)])
        self.assertEqual(self._one(u)["username"], "new_nick")

    def test_only_inactive_nicks_is_null(self):
        u = T.User(id=1703, username=None, first_name="Без", access_hash=19,
                   usernames=[T.Username(username="gone_nick", active=False, editable=True)])
        r = self._one(u)
        self.assertIn("username", r)
        self.assertIsNone(r["username"])

    def test_plain_username_wins_over_the_list(self):
        u = T.User(id=1704, username="main_nick", first_name="Оба", access_hash=20,
                   usernames=[T.Username(username="collect_nick", active=True, editable=False)])
        self.assertEqual(self._one(u)["username"], "main_nick")


class TestNameCannotLoseTheRow(F._Base):
    """Символ имени вне UTF-8 не теряет ряд с текстом клиента (красная команда NIGHT0710 Б3; с провода
    Telethon такого не отдаёт — он сам заменяет кривое на U+FFFD, — но докстринг peer_names обещает:
    «Сбой здесь ряд НЕ роняет»)."""

    def test_bad_char_in_name_keeps_the_row_and_text(self):
        u = T.User(id=1801, username="sur", first_name="Анна\ud800", access_hash=21)
        F.run(ub.on_incoming(F.incoming(self.client, u, mid=51, text="ТЕКСТ КЛИЕНТА")))
        self.assertEqual(ub._FEED_COUNTS["failed"], 0, "ряд с текстом клиента потерян из-за имени")
        (r,) = self.rows()
        self.assertEqual((r["text"], r["username"], r["name"]), ("ТЕКСТ КЛИЕНТА", "sur", "Анна�"))
        self.no_telegram()

    def test_lone_surrogates_anywhere_become_one_replacement_each(self):
        cases = {"А\ud800Б": "А�Б", "\udc00Ли": "�Ли", "Ан\ud83dна": "Ан�на",
                 "Анна 🏍️": "Анна 🏍️"}
        for raw, want in cases.items():
            with self.subTest(raw=ascii(raw)):
                r = tg_feed.row(F.mk_msg(5, 1001, text="x"), 1001, tg_feed.DIR_IN, 1001,
                                peer=_FakePeer(1001, None, raw))
                self.assertEqual(r["name"], want)
                tg_feed.line(r).encode("utf-8")


class TestLongNameCannotStopThePusher(PT._Base):
    """Имя без потолка вставало пушер СТОПом навсегда (красная команда NIGHT0710 Б4: ряд больше пачки
    256 КиБ). Потолок поля FIELD_MAX выше лимитов Telegram — живое не режется."""

    def test_a_name_cannot_stop_the_feed(self):
        peer = _FakePeer(1001, "n" * 300000, "Я" * 140000, "Ё" * 140000)
        rec = tg_feed.row(F.mk_msg(1, 1001, text="x"), 1001, "in", 1001, peer=peer)
        self.assertEqual(rec["username"], "n" * tg_feed.FIELD_MAX)
        self.assertEqual(rec["name"], "Я" * tg_feed.FIELD_MAX + " " + "Ё" * tg_feed.FIELD_MAX)
        data = self.write_feed(rec, PT.mk_row(1001, 2))
        door = PT.Door()
        self.assertEqual(self.tick(door), P.RC_OK, self.lines)
        self.assertEqual([r["msg_id"] for c in door.calls for r in c[1]["rows"]], [1, 2])
        self.assertEqual(self.state()["offset"], len(data))

    def test_telegram_sized_values_are_never_cut(self):
        nick, first, last = "n" * 32, "Я" * 64, "Ё" * 64
        rec = tg_feed.row(F.mk_msg(1, 1001, text="x"), 1001, "in", 1001, peer=_FakePeer(1001, nick, first, last))
        self.assertEqual((rec["username"], rec["name"]), (nick, first + " " + last))
        self.assertGreaterEqual(tg_feed.FIELD_MAX, 64)


class TestPushKeyKeepsDirection(PT._Base):
    """Ключ ряда — (chat_id, msg_id, dir) и в сверке ответа двери (выживший мутант P04: accepted без
    направления — неподтверждённый ряд «out» с тем же msg_id курсор проходил молча)."""

    def test_in_confirmed_out_not_cursor_stops_before_out(self):
        a, b = PT.mk_row(1001, 7, "in"), PT.mk_row(1001, 7, "out")
        data = self.write_feed(a, b)
        door = PT.Door(("only", [0]))                     # сервер подтвердил только (1001, 7, "in")
        self.assertEqual(self.tick(door), P.RC_OK)
        self.assertEqual(self.state()["count"], 1, "неподтверждённый out пройден курсором")
        self.assert_cursor_consistent()
        self.tick(door)
        self.assertEqual(door.calls[1][1]["rows"], [b])     # out уходит повтором
        self.assertEqual(self.state()["offset"], len(data))


if __name__ == "__main__":
    unittest.main()
