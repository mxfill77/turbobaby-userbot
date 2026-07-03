# -*- coding: utf-8 -*-
"""
test_suggest.py — мок-тесты ступени ① SUGGEST. БЕЗ реального Telegram и БЕЗ реального
Anthropic: оба замоканы. Реальной отправки клиенту не происходит нигде.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_suggest -v
"""

import os
import asyncio
import tempfile
import unittest

import suggest


# ------------------------------- моки Telegram -------------------------------

class FakeMsg:
    def __init__(self, mid):
        self.id = mid


class FakeAction:  # async context manager под `async with client.action(...)`
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeHistMsg:
    def __init__(self, sender_id, message):
        self.sender_id = sender_id
        self.message = message


class FakeSender:
    def __init__(self, uid, username=None, first="Test", last=None, bot=False):
        self.id = uid
        self.username = username
        self.first_name = first
        self.last_name = last
        self.bot = bot


class FakeDialog:
    def __init__(self, title, did):
        self.title = title
        self.name = title
        self.id = did


class FakeClient:
    """Замена Telethon-клиента: пишет отправки в self.sent, отдаёт историю/диалоги."""
    def __init__(self, history=None, flood_exc=None, dialogs=None):
        self.history = history or []
        self.sent = []            # список (chat, text)
        self.flood_exc = flood_exc
        self.dialogs = dialogs or []
        self._next_id = 5000

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text):
        if self.flood_exc is not None:
            raise self.flood_exc
        self._next_id += 1
        self.sent.append((chat, text))
        return FakeMsg(self._next_id)

    def iter_messages(self, entity, limit=50):
        async def gen():
            for m in self.history:
                yield m
        return gen()

    def iter_dialogs(self):
        async def gen():
            for d in self.dialogs:
                yield d
        return gen()


class FakeEvent:
    def __init__(self, client, reply_to, text, is_reply=True):
        self.client = client
        self.is_reply = is_reply
        self.reply_to_msg_id = reply_to
        self.raw_text = text


async def _nosleep(_):
    return None


def _fake_llm(_system, _user):
    return "DRAFT ответа клиенту"


# ------------------------------- тесты ---------------------------------------

class TestPureLogic(unittest.TestCase):

    def test_detect_lang(self):
        self.assertEqual(suggest.detect_lang("Привет, сколько стоит?"), "ru")
        self.assertEqual(suggest.detect_lang("Hello, how much?"), "en")
        self.assertEqual(suggest.detect_lang("650K per day"), "en")

    def test_parse_approval(self):
        self.assertEqual(suggest.parse_approval("+"), ("approve", None))
        self.assertEqual(suggest.parse_approval("да"), ("approve", None))
        self.assertEqual(suggest.parse_approval("да 2")[0], "approve")
        self.assertEqual(suggest.parse_approval("-"), ("reject", None))
        self.assertEqual(suggest.parse_approval("нет"), ("reject", None))
        act, payload = suggest.parse_approval("Здравствуйте! NMAX 449฿/день")
        self.assertEqual(act, "edit")
        self.assertEqual(payload, "Здравствуйте! NMAX 449฿/день")

    def test_critical_facts_in_prompt(self):
        sysp = suggest.make_system_prompt("FAQ-тело", "ru")
        self.assertIn("CLICK 125", sysp)          # Click дословно
        self.assertIn("NMAX155 449/3000", sysp)   # прайс дословно
        self.assertIn("FAQ-тело", sysp)           # живой FAQ вклеен
        self.assertIn("русском", sysp)            # язык клиента

    def test_generate_draft_uses_injected_llm(self):
        d = suggest.generate_draft("[клиент]: привет", "ru", "FAQ", call_llm=_fake_llm)
        self.assertEqual(d, "DRAFT ответа клиенту")

    def test_rate_limiter_hour_and_day(self):
        t = [1000.0]
        rl = suggest.RateLimiter(per_hour=2, per_day=3, now=lambda: t[0])
        self.assertTrue(rl.allow()[0]); rl.record()
        self.assertTrue(rl.allow()[0]); rl.record()
        self.assertFalse(rl.allow()[0])           # 2/час исчерпан
        t[0] += 3601                              # прошёл час
        self.assertTrue(rl.allow()[0]); rl.record()
        self.assertFalse(rl.allow()[0])           # 3/день исчерпан

    def test_pending_store_durable(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pending.jsonl")
            ps = suggest.PendingStore(p)
            ps.add(111, {"client_id": 7, "draft": "x"})
            self.assertEqual(ps.get(111)["client_id"], 7)
            # новый экземпляр читает журнал — pending переживает «рестарт»
            ps2 = suggest.PendingStore(p)
            self.assertEqual(ps2.get(111)["draft"], "x")
            ps2.pop(111)
            ps3 = suggest.PendingStore(p)
            self.assertIsNone(ps3.get(111))

    def test_edit_diff(self):
        self.assertEqual(suggest._edit_diff("abc", "abc"), "")
        self.assertEqual(suggest._edit_diff("abc", "abd"), "abd")


class TestSendGuards(unittest.TestCase):

    def setUp(self):
        suggest.reset_disabled()
        suggest.limiter = suggest.RateLimiter(6, 15)

    def test_send_success_no_real_wait(self):
        c = FakeClient()
        ok, reason = asyncio.run(
            suggest.send_to_client(c, 999, "привет", sleep=_nosleep, jitter=lambda: 0)
        )
        self.assertTrue(ok)
        self.assertEqual(c.sent, [(999, "привет")])

    def test_send_rate_limited(self):
        c = FakeClient()
        suggest.limiter = suggest.RateLimiter(1, 15)
        asyncio.run(suggest.send_to_client(c, 1, "a", sleep=_nosleep, jitter=lambda: 0))
        ok, reason = asyncio.run(
            suggest.send_to_client(c, 1, "b", sleep=_nosleep, jitter=lambda: 0)
        )
        self.assertFalse(ok)
        self.assertIn("лимит", reason)
        self.assertEqual(len(c.sent), 1)          # второй не ушёл

    def test_test_mode_blocks_send_to_client(self):
        # Defense-in-depth: даже прямой вызов send_to_client в TEST_MODE ничего не шлёт.
        old = suggest.SUGGEST_TEST_MODE
        suggest.SUGGEST_TEST_MODE = True
        try:
            c = FakeClient()
            ok, reason = asyncio.run(
                suggest.send_to_client(c, 1, "x", sleep=_nosleep, jitter=lambda: 0)
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "TEST_MODE")
            self.assertEqual(c.sent, [])          # клиенту ничего
        finally:
            suggest.SUGGEST_TEST_MODE = old

    def test_flood_disables_suggest(self):
        class MyFlood(Exception):
            pass
        old = suggest.FLOOD_ERRORS
        suggest.FLOOD_ERRORS = (MyFlood,)
        try:
            c = FakeClient(flood_exc=MyFlood("PEER_FLOOD"))
            ok, reason = asyncio.run(
                suggest.send_to_client(c, 1, "a", sleep=_nosleep, jitter=lambda: 0)
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "flood")
            self.assertFalse(suggest.is_enabled() if suggest.SUGGEST_MODE else False)
            self.assertTrue(suggest._disabled)     # авто-стоп сработал
        finally:
            suggest.FLOOD_ERRORS = old
            suggest.reset_disabled()


class TestFullFlow(unittest.TestCase):
    """Мок-входящее → черновик на модерацию → ✅/правка/отклонение → отправка (замокана)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self._mode = suggest.SUGGEST_MODE
        self._test = suggest.SUGGEST_TEST_MODE
        self._mg = suggest.MOD_GROUP_ID
        self._pending = suggest.pending
        self._pairs = suggest.PAIRS_FILE
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.reset_disabled()
        suggest.MOD_GROUP_ID = -1009999999999
        suggest.pending = suggest.PendingStore(os.path.join(d, "pending.jsonl"))
        suggest.PAIRS_FILE = os.path.join(d, "pairs.jsonl")
        suggest.limiter = suggest.RateLimiter(6, 15)
        self.me = 42
        self.client = FakeClient(history=[
            FakeHistMsg(self.me, "Здравствуйте! Что хотите арендовать?"),
            FakeHistMsg(999, "Привет, сколько стоит NMAX на неделю?"),
        ])
        self.sender = FakeSender(999, username="client1")

    def tearDown(self):
        suggest.SUGGEST_MODE = self._mode
        suggest.SUGGEST_TEST_MODE = self._test
        suggest.MOD_GROUP_ID = self._mg
        suggest.pending = self._pending
        suggest.PAIRS_FILE = self._pairs
        suggest.reset_disabled()
        self._tmp.cleanup()

    def _incoming(self):
        return asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=_fake_llm, faq="FAQ-тело")
        )

    def _pairs_lines(self):
        with open(suggest.PAIRS_FILE, encoding="utf-8") as f:
            import json
            return [json.loads(x) for x in f if x.strip()]

    def test_incoming_posts_draft_and_pending(self):
        mid = self._incoming()
        self.assertIsNotNone(mid)
        # черновик ушёл в группу модерации (а НЕ клиенту)
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0][0], suggest.MOD_GROUP_ID)
        self.assertIn("DRAFT ответа клиенту", self.client.sent[0][1])
        self.assertIsNotNone(suggest.pending.get(mid))

    def test_approve_sends_draft_to_client(self):
        mid = self._incoming()
        ev = FakeEvent(self.client, reply_to=mid, text="+")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertTrue(res["sent"])
        # последняя отправка — КЛИЕНТУ (id 999) с текстом черновика
        self.assertEqual(self.client.sent[-1], (999, "DRAFT ответа клиенту"))
        pairs = self._pairs_lines()
        self.assertEqual(pairs[-1]["action"], "approve")
        self.assertEqual(pairs[-1]["client"], "@client1")

    def test_edit_sends_edited_version(self):
        mid = self._incoming()
        edited = "Здравствуйте! NMAX 155 — 449฿/день, депозит 3000฿."
        ev = FakeEvent(self.client, reply_to=mid, text=edited)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "edit")
        self.assertEqual(self.client.sent[-1], (999, edited))
        pairs = self._pairs_lines()
        self.assertEqual(pairs[-1]["edit"], edited)      # правка зафиксирована

    def test_reject_does_not_send(self):
        mid = self._incoming()
        before = len(self.client.sent)
        ev = FakeEvent(self.client, reply_to=mid, text="-")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "reject")
        self.assertFalse(res["sent"])
        self.assertEqual(len(self.client.sent), before)  # клиенту ничего
        self.assertIsNone(suggest.pending.get(mid))      # pending снят

    def test_test_mode_does_not_send_to_client(self):
        suggest.SUGGEST_TEST_MODE = True
        mid = self._incoming()
        before = len(self.client.sent)
        ev = FakeEvent(self.client, reply_to=mid, text="+")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertFalse(res["sent"])                    # TEST_MODE: клиенту НЕ ушло
        self.assertEqual(len(self.client.sent), before)

    def test_reply_not_on_our_draft_ignored(self):
        self._incoming()
        ev = FakeEvent(self.client, reply_to=123456789, text="+")  # чужой reply
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertIsNone(res)


class TestFaqOrder(unittest.TestCase):
    """Порядок чтения FAQ: faq → turbobaby_faq → локальный файл."""

    def test_primary_faq_used(self):
        getter = lambda name: {"faq": "ЖИВОЙ-FAQ", "turbobaby_faq": "СТАРЫЙ"}.get(name)
        self.assertEqual(suggest.load_faq(getter=getter), "ЖИВОЙ-FAQ")

    def test_fallback_to_turbobaby(self):
        getter = lambda name: {"faq": "", "turbobaby_faq": "СТАРЫЙ-FAQ"}.get(name)
        self.assertEqual(suggest.load_faq(getter=getter), "СТАРЫЙ-FAQ")

    def test_fallback_to_local_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faq.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("ЛОКАЛЬНЫЙ-FAQ")
            old = suggest.LOCAL_FAQ
            suggest.LOCAL_FAQ = p
            try:
                self.assertEqual(suggest.load_faq(getter=lambda n: ""), "ЛОКАЛЬНЫЙ-FAQ")
            finally:
                suggest.LOCAL_FAQ = old


class TestResolveGroup(unittest.TestCase):
    """Резолв группы модерации по имени через iter_dialogs."""

    def setUp(self):
        self._id = suggest.MOD_GROUP_ID
        self._name = suggest.MOD_GROUP_NAME

    def tearDown(self):
        suggest.MOD_GROUP_ID = self._id
        suggest.MOD_GROUP_NAME = self._name

    def test_resolve_by_name(self):
        suggest.MOD_GROUP_ID = None
        suggest.MOD_GROUP_NAME = "Модерация ответов"
        c = FakeClient(dialogs=[
            FakeDialog("Входящие", -1001110000),
            FakeDialog("Модерация ответов", -1002220000),
        ])
        gid = asyncio.run(suggest.resolve_mod_group(c))
        self.assertEqual(gid, -1002220000)
        self.assertEqual(suggest.MOD_GROUP_ID, -1002220000)

    def test_not_found_returns_none(self):
        suggest.MOD_GROUP_ID = None
        suggest.MOD_GROUP_NAME = "Нет такой группы"
        c = FakeClient(dialogs=[FakeDialog("Входящие", -1001110000)])
        gid = asyncio.run(suggest.resolve_mod_group(c))
        self.assertIsNone(gid)

    def test_explicit_id_skips_resolve(self):
        suggest.MOD_GROUP_ID = -1009990000
        suggest.MOD_GROUP_NAME = "Модерация ответов"
        c = FakeClient(dialogs=[])  # iter_dialogs не нужен — ID уже есть
        gid = asyncio.run(suggest.resolve_mod_group(c))
        self.assertEqual(gid, -1009990000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
