# -*- coding: utf-8 -*-
"""
test_isolation_guard.py — регресс изоляции тестов от боевого модербота (инцидент 16:39).

Воспроизводит сценарий утечки: живой токен + «живой» bot-режим. Доказывает, что теперь
фикстурный черновик НЕ может достичь боевого IPC/группы — по построению:
  • боевой moderation_ipc.db заблокирован тривайром (RuntimeError + счётчик);
  • даже с боевым токеном on_client_message читает ИЗОЛИРОВАННЫЙ (пустой) IPC → reply-режим →
    карточка уходит в FakeClient (мок группы), клиенту — ничего, боевой IPC не тронут;
  • мок-счётчик обращений к боевому IPC за прогон = 0.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_isolation_guard -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import asyncio
import tempfile
import unittest

import suggest
import moderation_ipc


class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    """Мок Telethon: копит отправки в self.sent, отдаёт историю диалога."""
    def __init__(self, history=None):
        self.sent = []
        self._hist = history or []

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text, reply_to=None):
        self.sent.append((chat, text))

        class _M:
            id = 4242
        return _M()

    def iter_messages(self, entity, limit=50):
        async def gen():
            for m in self._hist:
                yield m
        return gen()


class _HistMsg:
    def __init__(self, sid, msg):
        self.sender_id = sid
        self.message = msg
        self.date = None


class _Sender:
    id = 999
    username = "client1"


class TestIsolationArmed(unittest.TestCase):
    """Обвязка взведена: TESTING, изолированный путь, стёртый токен."""

    def test_flags_and_paths(self):
        self.assertTrue(suggest.TESTING)
        self.assertTrue(moderation_ipc.TESTING)
        self.assertEqual(suggest.MODERBOT_TOKEN, "")
        self.assertNotEqual(os.path.abspath(moderation_ipc.DB_PATH),
                            os.path.abspath(moderation_ipc._PROD_DB))


class TestProdIpcBlocked(unittest.TestCase):
    """Боевой moderation_ipc.db недоступен по построению — тривайр + счётчик."""

    def test_open_prod_db_raises_and_counts(self):
        before = moderation_ipc.prod_ipc_open_attempts
        old = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = moderation_ipc._PROD_DB   # имитируем «забытый» редирект
        try:
            with self.assertRaises(RuntimeError):
                moderation_ipc.init_db()
        finally:
            moderation_ipc.DB_PATH = old
        self.assertEqual(moderation_ipc.prod_ipc_open_attempts, before + 1)
        # это НАМЕРЕННАЯ проба тривайра, а не утечка — вернём счётчик, чтобы suite-инвариант «0» жил
        moderation_ipc.prod_ipc_open_attempts = before


class TestNoLeakOnIncoming(unittest.TestCase):
    """Инцидент 16:39: фикстурный @client1 с живым токеном → остаётся ЛОКАЛЬНЫМ."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self._save = (suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE, suggest.MODERBOT_TOKEN,
                      suggest.MOD_GROUP_ID, suggest.pending, suggest.PAIRS_FILE)
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.MODERBOT_TOKEN = "boevoy-token"      # ← «живой» токен, как в среде демона
        suggest.MOD_GROUP_ID = -1002220000           # мок-группа модерации (FakeClient)
        suggest.reset_disabled()
        suggest.limiter = suggest.RateLimiter(6, 15)
        suggest.pending = suggest.PendingStore(os.path.join(d, "p.jsonl"))
        suggest.PAIRS_FILE = os.path.join(d, "pairs.jsonl")

    def tearDown(self):
        (suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE, suggest.MODERBOT_TOKEN,
         suggest.MOD_GROUP_ID, suggest.pending, suggest.PAIRS_FILE) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def test_incoming_stays_local_zero_prod_ipc(self):
        before = moderation_ipc.prod_ipc_open_attempts
        c = FakeClient(history=[_HistMsg(999, "Привет, сколько стоит NMAX на неделю?")])
        r = asyncio.run(suggest.on_client_message(
            c, _Sender(), 42, call_llm=lambda s, u: "DRAFT ответа клиенту", faq="FAQ-тело"))
        # bot_mode_active видит изолированный (пустой) IPC → reply-режим (mid, а не «ipc:…»)
        self.assertFalse(str(r).startswith("ipc:"))
        # карточка — в мок-группу модерации; клиенту (id 999) НИЧЕГО
        self.assertEqual(len(c.sent), 1)
        self.assertEqual(c.sent[0][0], suggest.MOD_GROUP_ID)
        self.assertEqual([t for t in c.sent if t[0] == 999], [])
        # и ноль обращений к БОЕВОМУ IPC за весь флоу
        self.assertEqual(moderation_ipc.prod_ipc_open_attempts, before)

    def test_full_suite_outbound_counter_is_zero(self):
        # Мок-счётчик наружу за прогон обязан быть нулём (доступ к боевой очереди = 0).
        self.assertEqual(test_isolation.outbound_prod_attempts(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
