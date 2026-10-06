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
  • пушер: смешанная лента (старые ряды и новые) уходит на сервер как есть      → TestPushMixedFeed.
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
        tree = ast.parse(self._src("pc_orchestrator.py"))
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_tg_feed_push"]
        self.assertEqual(len(defs), 1, "функция пушера в демоне должна быть ровно одна")
        poll = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "poll_once"][0]
        calls = []
        for st in poll.body:
            if isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) \
                    and isinstance(st.value.func, ast.Name):
                calls.append(st.value.func.id)
        self.assertEqual(calls.count("_tg_feed_push"), 1)
        i = calls.index
        self.assertLess(i("_chatlog_capture"), i("_tg_feed_push"))
        self.assertLess(i("_tg_feed_push"), i("_queue_snapshot"))
        self.assertEqual(calls[-1], "_write_heartbeat", "heartbeat — последняя строка оборота")

    def test_both_conflict_classes_kept(self):
        tree = ast.parse(self._src("test_pc_orchestrator.py"))
        names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        self.assertEqual(names.count("TestBoxFirst"), 1, "класс main (BOXFIRST0410) потерян")
        self.assertEqual(names.count("TestTgFeedPush"), 1, "класс ветки (TGPUSHPC0310) потерян")

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


if __name__ == "__main__":
    unittest.main()
