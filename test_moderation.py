# -*- coding: utf-8 -*-
"""
test_moderation.py — мок-тесты задачи-2 (бот-модератор). БЕЗ реального Telegram и БЕЗ
реального Anthropic. Отправки клиенту нигде не происходит (проверяем инварианты).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_moderation -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import json
import asyncio
import datetime
import tempfile
import unittest

import suggest
import moderation_ipc
import moderation_core


async def _nosleep(_):
    return None


class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self):
        self.sent = []

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text, reply_to=None):
        self.sent.append((chat, text))


# JSON-мок LLM по интенту
def _llm(intent, final="ИТОГ", answer="ОТВЕТ"):
    def call(_system, _user):
        return json.dumps({"intent": intent, "final_text": final, "answer": answer}, ensure_ascii=False)
    return call


class TestIPC(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()

    def tearDown(self):
        moderation_ipc.DB_PATH = self._old
        self._tmp.cleanup()

    def test_full_lifecycle(self):
        did = moderation_ipc.enqueue_draft({
            "client_id": 999, "client_ref": "@c", "lang": "ru",
            "incoming": "цена?", "draft": "черновик", "first_contact": True})
        self.assertEqual([r["id"] for r in moderation_ipc.fetch_new()], [did])
        moderation_ipc.mark_posted(did, card_msg_id=5001)
        self.assertEqual(moderation_ipc.draft_by_card(5001)["id"], did)
        self.assertEqual(moderation_ipc.fetch_new(), [])          # уже posted
        moderation_ipc.set_decision(did, "ready", final_text="ОК", decided_by="@danya")
        ready = moderation_ipc.fetch_ready()
        self.assertEqual(ready[0]["final_text"], "ОК")
        moderation_ipc.mark(did, "sent")
        self.assertEqual(moderation_ipc.fetch_ready(), [])

    def test_heartbeat_liveness(self):
        now = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=datetime.timezone.utc)
        moderation_ipc.heartbeat(now.isoformat())
        self.assertTrue(moderation_ipc.is_bot_alive(now=lambda: now, threshold=15))
        later = now + datetime.timedelta(seconds=60)
        self.assertFalse(moderation_ipc.is_bot_alive(now=lambda: later, threshold=15))

    def test_no_heartbeat_is_dead(self):
        self.assertFalse(moderation_ipc.is_bot_alive())


class TestInterpret(unittest.TestCase):
    def setUp(self):
        self._wl = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._wl

    def test_fast_approve_reject(self):
        self.assertEqual(moderation_core.interpret("D", "+", "FAQ")["intent"], "approve")
        self.assertEqual(moderation_core.interpret("D", "нет", "FAQ")["intent"], "reject")

    def test_cosmetic_edits_and_confirms(self):
        # КОСМЕТИКА: правит форму существующего черновика → need_confirm
        r = moderation_core.interpret("D", "сделай короче", "FAQ", call_llm=_llm("cosmetic", final="Кратко"))
        self.assertEqual(r["intent"], "cosmetic")
        self.assertTrue(r["need_confirm"])
        self.assertEqual(r["final_text"], "Кратко")

    def test_strategy_marks_regenerate(self):
        # СТРАТЕГИЯ: final_text пуст (перегенерация с нуля отдельно), need_confirm
        r = moderation_core.interpret("D", "дожимай на ADV", "FAQ", call_llm=_llm("strategy", final="неважно"))
        self.assertEqual(r["intent"], "strategy")
        self.assertTrue(r["need_confirm"])
        self.assertEqual(r["final_text"], "")     # НЕ патч старого текста

    def test_dictation_confirms(self):
        # ДИКТОВКА: текст менеджера почти как есть, НО повторное подтверждение обязательно
        r = moderation_core.interpret("D", "ответь дословно: приедем в 5", "FAQ",
                                      call_llm=_llm("dictation", final="Приедем в 5"))
        self.assertEqual(r["intent"], "dictation")
        self.assertTrue(r["need_confirm"])
        self.assertEqual(r["final_text"], "Приедем в 5")

    def test_question_answers(self):
        r = moderation_core.interpret("D", "что по ценам XMAX?", "FAQ", call_llm=_llm("question", answer="939฿/день"))
        self.assertEqual(r["intent"], "question")
        self.assertEqual(r["answer"], "939฿/день")

    def test_ambiguous_is_strategy(self):
        # двусмысленно/битый JSON → СТРАТЕГИЯ (глубже безопаснее), с подтверждением
        r = moderation_core.interpret("D", "мутный текст", "FAQ", call_llm=lambda s, u: "не json")
        self.assertEqual(r["intent"], "strategy")
        self.assertTrue(r["need_confirm"])

    def test_classifier_intent_not_length(self):
        # длинная косметика остаётся косметикой; короткая стратегия — стратегией (решает интент, не длина)
        long_cos = moderation_core.interpret("D", "пожалуйста " * 20 + "убери восклицательные знаки",
                                             "FAQ", call_llm=_llm("cosmetic", final="без !"))
        self.assertEqual(long_cos["intent"], "cosmetic")
        short_str = moderation_core.interpret("D", "жёстче", "FAQ", call_llm=_llm("strategy"))
        self.assertEqual(short_str["intent"], "strategy")


class TestDecisions(unittest.TestCase):
    def setUp(self):
        self._wl = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._wl

    DRAFT = {"id": 1, "draft": "черновик", "final_text": "кандидат"}

    def test_callback_yes_live_ready(self):
        d = moderation_core.process_callback(self.DRAFT, "yes", "danya", test_mode=False)
        self.assertEqual(d["decision"], "ready")
        self.assertEqual(d["final_text"], "черновик")

    def test_callback_yes_testmode_held(self):
        d = moderation_core.process_callback(self.DRAFT, "yes", "danya", test_mode=True)
        self.assertEqual(d["decision"], "test_held")     # DOUBLE-LOCK слой 1
        self.assertIn("не отправлено", d["card"].lower())

    def test_callback_send_uses_candidate(self):
        d = moderation_core.process_callback(self.DRAFT, "send", "danya", test_mode=False, candidate="кандидат")
        self.assertEqual(d["decision"], "ready")
        self.assertEqual(d["final_text"], "кандидат")

    def test_callback_no_reject(self):
        self.assertEqual(moderation_core.process_callback(self.DRAFT, "no", "d", False)["decision"], "rejected")

    def test_callback_more(self):
        self.assertEqual(moderation_core.process_callback(self.DRAFT, "more", "d", False)["decision"], "await_more")

    def test_whitelist_denies_callback(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        d = moderation_core.process_callback(self.DRAFT, "yes", "stranger", False)
        self.assertEqual(d["decision"], "denied")
        self.assertIn("⛔", d["card"])

    STRAT_DRAFT = {"id": 1, "draft": "черновик", "final_text": "кандидат",
                   "transcript": "[клиент]: NMAX?", "lang": "ru",
                   "first_contact": False, "pricing_note": "ЦЕНА из Календаря: 500฿/день"}

    def test_reply_cosmetic_confirm(self):
        d = moderation_core.process_reply(self.DRAFT, "короче", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("cosmetic", final="Кратко"))
        self.assertEqual(d["decision"], "confirm")       # обязательное подтверждение
        self.assertEqual(d["final_text"], "Кратко")
        self.assertEqual(d["level"], "cosmetic")

    def test_reply_dictation_confirm(self):
        # ДИКТОВКА больше НЕ авто-шлёт (раньше replacement уходил без подтверждения) → confirm
        d = moderation_core.process_reply(self.DRAFT, "ответь дословно: ок", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("dictation", final="Ок"))
        self.assertEqual(d["decision"], "confirm")
        self.assertEqual(d["final_text"], "Ок")

    def test_reply_strategy_regenerates_with_directive(self):
        # СТРАТЕГИЯ: перегенерация С НУЛЯ — regen получает ДИРЕКТИВУ и исходный контекст, НЕ патчит старое
        seen = {}
        def fake_regen(draft, faq, directive):
            seen["directive"] = directive
            seen["transcript"] = draft.get("transcript")
            return "НОВЫЙ ЧЕРНОВИК по стратегии"
        d = moderation_core.process_reply(self.STRAT_DRAFT, "дожимай на ADV", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("strategy"), regen=fake_regen)
        self.assertEqual(d["decision"], "confirm")
        self.assertEqual(d["final_text"], "НОВЫЙ ЧЕРНОВИК по стратегии")   # не старый черновик
        self.assertEqual(seen["directive"], "дожимай на ADV")             # реплика = директива
        self.assertEqual(seen["transcript"], "[клиент]: NMAX?")           # исходный контекст

    def test_reconfirm_mandatory_all_levels(self):
        for intent in ("cosmetic", "dictation"):
            d = moderation_core.process_reply(self.DRAFT, "x", "d", "FAQ", test_mode=False,
                                              call_llm=_llm(intent, final="T"))
            self.assertEqual(d["decision"], "confirm", intent)   # ни один уровень не авто-шлёт
        d = moderation_core.process_reply(self.STRAT_DRAFT, "жёстче", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("strategy"), regen=lambda *a: "R")
        self.assertEqual(d["decision"], "confirm")

    def test_reply_edit_never_autosends_in_testmode(self):
        # даже в TEST_MODE правка не «test_held» на этапе reply — сперва повторное подтверждение
        d = moderation_core.process_reply(self.DRAFT, "ответь дословно: ок", "d", "FAQ", test_mode=True,
                                          call_llm=_llm("dictation", final="Ок"))
        self.assertEqual(d["decision"], "confirm")
        self.assertNotIn(d["decision"], ("ready", "test_held", "sent"))

    def test_reply_approve_still_sends(self):
        # быстрый путь «+» реплики → отправка как есть (без доп-подтверждения)
        d = moderation_core.process_reply(self.DRAFT, "+", "d", "FAQ", test_mode=False)
        self.assertEqual(d["decision"], "ready")
        self.assertEqual(d["final_text"], "черновик")

    def test_reply_question_answer(self):
        d = moderation_core.process_reply(self.DRAFT, "что по ценам?", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("question", answer="A"))
        self.assertEqual(d["decision"], "answer")
        self.assertEqual(d["answer"], "A")

    def test_reply_whitelist_denies(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        d = moderation_core.process_reply(self.DRAFT, "+", "stranger", "FAQ", test_mode=False)
        self.assertEqual(d["decision"], "denied")


class TestDegradationAndExecutor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (moderation_ipc.DB_PATH, suggest.MODERBOT_TOKEN, suggest.SUGGEST_MODE,
                      suggest.SUGGEST_TEST_MODE, suggest.pending, suggest.PAIRS_FILE)
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.reset_disabled()
        suggest.limiter = suggest.RateLimiter(6, 15)
        suggest.PAIRS_FILE = os.path.join(self._tmp.name, "pairs.jsonl")

    def tearDown(self):
        (moderation_ipc.DB_PATH, suggest.MODERBOT_TOKEN, suggest.SUGGEST_MODE,
         suggest.SUGGEST_TEST_MODE, suggest.pending, suggest.PAIRS_FILE) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def test_bot_mode_inactive_without_token(self):
        suggest.MODERBOT_TOKEN = ""          # нет токена → деградация
        self.assertFalse(suggest.bot_mode_active())

    def test_bot_mode_active_with_token_and_heartbeat(self):
        suggest.MODERBOT_TOKEN = "x"
        moderation_ipc.heartbeat()           # свежий → жив
        self.assertTrue(suggest.bot_mode_active())

    def test_bot_mode_inactive_when_heartbeat_stale(self):
        suggest.MODERBOT_TOKEN = "x"
        old = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        moderation_ipc.heartbeat(old.isoformat())   # протух → мёртв
        self.assertFalse(suggest.bot_mode_active())

    def test_executor_sends_ready_row(self):
        did = moderation_ipc.enqueue_draft({"client_id": 999, "client_ref": "@c", "lang": "ru",
                                            "incoming": "?", "draft": "D", "first_contact": False})
        moderation_ipc.set_decision(did, "ready", final_text="ОТВЕТ", decided_by="@d")
        sent = []
        async def fake_send(client, cid, text, sleep=None, jitter=None):
            sent.append((cid, text)); return True, None
        n = asyncio.run(suggest.poll_and_send(FakeClient(), sender=fake_send))
        self.assertEqual(n, 1)
        self.assertEqual(sent, [(999, "ОТВЕТ")])
        self.assertEqual(moderation_ipc.get(did)["status"], "sent")

    def test_executor_double_lock_in_testmode(self):
        # SAFETY: даже если 'ready' оказался в TEST_MODE — send_to_client не отправит.
        suggest.SUGGEST_TEST_MODE = True
        did = moderation_ipc.enqueue_draft({"client_id": 999, "client_ref": "@c", "lang": "ru",
                                            "incoming": "?", "draft": "D", "first_contact": False})
        moderation_ipc.set_decision(did, "ready", final_text="ОТВЕТ")
        c = FakeClient()
        n = asyncio.run(suggest.poll_and_send(c, sleep=_nosleep, jitter=lambda: 0))
        self.assertEqual(c.sent, [])                     # клиенту НИЧЕГО
        self.assertEqual(moderation_ipc.get(did)["status"], "failed")


class TestRoutingIntoIPC(unittest.TestCase):
    """on_client_message: bot-режим → в IPC (не в группу); деградация → в группу."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (moderation_ipc.DB_PATH, suggest.SUGGEST_MODE, suggest.MOD_GROUP_ID,
                      suggest.pending, suggest.bot_mode_active)
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        suggest.SUGGEST_MODE = True
        suggest.reset_disabled()
        suggest.MOD_GROUP_ID = -100777
        suggest.pending = suggest.PendingStore(os.path.join(self._tmp.name, "p.jsonl"))

    def tearDown(self):
        (moderation_ipc.DB_PATH, suggest.SUGGEST_MODE, suggest.MOD_GROUP_ID,
         suggest.pending, suggest.bot_mode_active) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    class _Sender:
        id = 999
        username = "client1"

    def _client(self):
        return FakeClient()

    def _hist(self):
        class M:
            def __init__(s, sid, msg): s.sender_id = sid; s.message = msg; s.date = None
        c = FakeClient()
        c._hist = [M(999, "привет")]

        def iter_messages(entity, limit=50):
            async def gen():
                for m in c._hist:
                    yield m
            return gen()
        c.iter_messages = iter_messages
        return c

    def test_botmode_routes_to_ipc(self):
        suggest.bot_mode_active = lambda: True
        c = self._hist()
        r = asyncio.run(suggest.on_client_message(c, self._Sender(), 42,
                                                  call_llm=lambda s, u: "ЧЕРНОВИК", faq="FAQ"))
        self.assertTrue(str(r).startswith("ipc:"))
        self.assertEqual(c.sent, [])                     # в группу НЕ постим (это делает бот)
        self.assertEqual(len(moderation_ipc.fetch_new()), 1)

    def test_degraded_routes_to_group(self):
        suggest.bot_mode_active = lambda: False
        c = self._hist()
        r = asyncio.run(suggest.on_client_message(c, self._Sender(), 42,
                                                  call_llm=lambda s, u: "ЧЕРНОВИК", faq="FAQ"))
        self.assertFalse(str(r).startswith("ipc:"))
        self.assertEqual(len(c.sent), 1)                 # постим в группу модерации сами
        self.assertEqual(c.sent[0][0], suggest.MOD_GROUP_ID)
        self.assertEqual(moderation_ipc.fetch_new(), [])  # в IPC ничего


if __name__ == "__main__":
    unittest.main(verbosity=2)
