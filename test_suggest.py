# -*- coding: utf-8 -*-
"""
test_suggest.py — мок-тесты ступени ① SUGGEST. БЕЗ реального Telegram и БЕЗ реального
Anthropic: оба замоканы. Реальной отправки клиенту не происходит нигде.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_suggest -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import json
import time
import asyncio
import datetime
import tempfile
import unittest
from unittest import mock

import suggest


def _cli_json(result="", main="claude-fable-5", main_in=2766, main_out=31, is_error=False):
    """Собрать stdout как у claude --output-format json: поле result + modelUsage с реальной головой
    (main, большой inputTokens) и служебным haiku (крошечный вход). Для тестов _cli_llm/_parse_cli_json."""
    return json.dumps({
        "type": "result", "is_error": is_error, "result": result,
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 505, "outputTokens": 13},
            main: {"inputTokens": main_in, "outputTokens": main_out},
        },
    })


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
    def __init__(self, sender_id, message, date=None):
        self.sender_id = sender_id
        self.message = message
        self.date = date


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

    async def send_message(self, chat, text, reply_to=None):
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
    def __init__(self, client, reply_to, text, is_reply=True, sender=None, chat_id=-1002220000):
        self.client = client
        self.is_reply = is_reply
        self.reply_to_msg_id = reply_to
        self.raw_text = text
        self.sender = sender          # None → _sender_username вернёт None
        self.chat_id = chat_id        # куда идёт ack (группа модерации, не клиент)


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

    def test_detect_lang_from_client_whole_dialog(self):
        # язык по ВСЕМ репликам клиента, латинское имя модели EN не триггерит
        ru = ("[менеджер]: Здравствуйте!\n[клиент]: Какие байки есть?\n"
              "[клиент]: NMAX с 10 июля\n[клиент]: Adv 350")
        self.assertEqual(suggest.detect_lang_from_client(ru), "ru")   # последняя латиница → всё равно RU
        # латинские названия моделей, но текст кириллицей → RU
        mix = "[клиент]: хочу adv 350 или nmax 155 на месяц"
        self.assertEqual(suggest.detect_lang_from_client(mix), "ru")
        # чистый английский → EN
        en = "[клиент]: hello, what bikes do you have for rent?"
        self.assertEqual(suggest.detect_lang_from_client(en), "en")
        # только модель+число (без осмысленных слов) → дефолт RU (RU-first магазин)
        self.assertEqual(suggest.detect_lang_from_client("[клиент]: Adv 350"), "ru")
        self.assertEqual(suggest.detect_lang_from_client("[клиент]: NMAX 155"), "ru")

    def test_detect_lang_stable_between_drafts(self):
        # два черновика одного клиента (растущий транскрипт) — язык не прыгает RU↔EN
        t1 = "[клиент]: Какие байки есть в аренду?"
        t2 = t1 + "\n[менеджер]: ...\n[клиент]: Adv 350"
        self.assertEqual(suggest.detect_lang_from_client(t1),
                         suggest.detect_lang_from_client(t2))
        self.assertEqual(suggest.detect_lang_from_client(t2), "ru")

    def test_greeting_already_sent(self):
        self.assertTrue(suggest.greeting_already_sent(
            "[менеджер]: Здравствуйте! Что арендуете?\n[клиент]: NMAX"))
        self.assertTrue(suggest.greeting_already_sent("[менеджер]: Hello! How can I help?"))
        # наше сообщение без приветствия → приветствия ещё не было
        self.assertFalse(suggest.greeting_already_sent(
            "[менеджер]: NMAX стоит 449฿/день\n[клиент]: ок"))
        # приветствие у КЛИЕНТА не считается нашим
        self.assertFalse(suggest.greeting_already_sent("[клиент]: Здравствуйте!"))

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

    def test_prompt_forbids_leaking_stage_context(self):
        # Класс-фикс: промпт явно запрещает начинать ответ с описания ситуации/этапа и
        # помечает блок этапов как ВНУТРЕННИЙ (клиенту не показывать).
        sysp = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("ВНУТРЕННИЙ КОНТЕКСТ", sysp)          # блок этапов помечен как внутренний
        self.assertIn("НЕ начинай ответ с описания ситуации", sysp)
        self.assertIn("сейчас Этап N", sysp)                # запрет пометки стадии в тексте
        # инварианты целы
        self.assertIn("CLICK 125", sysp)                    # критфакты
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)             # ценовая политика
        self.assertIn("русском", sysp)                      # язык

    def test_strip_service_prefix_removes_stage_note(self):
        # Точный кейс бага: черновик начинается со служебной строки о стадии сделки.
        leaked = ("Клиент готов бронировать по датам 7 июля на неделю, менеджер ещё не назвал "
                  "цену — сейчас Этап 1.\nCBR 650R — 650฿/день, депозит 5000฿, свободен на эти даты.")
        out = suggest._strip_service_prefix(leaked)
        # служебные маркеры вычищены
        self.assertNotIn("Этап", out)
        self.assertNotIn("Клиент готов бронировать", out)
        self.assertNotIn("менеджер ещё не назвал", out)
        # реальный ответ (модель/цена/депозит) на месте
        self.assertIn("CBR 650R", out)
        self.assertIn("650฿/день", out)
        self.assertIn("депозит 5000฿", out)

    def test_strip_service_prefix_same_line(self):
        # Служебная пометка и ответ в ОДНОЙ строке — режем по концу служебного предложения.
        out = suggest._strip_service_prefix("Сейчас Этап 1. NMAX 449฿/день, свободен.")
        self.assertNotIn("Этап", out)
        self.assertTrue(out.startswith("NMAX 449"))

    def test_strip_service_prefix_leaves_clean_answer(self):
        # Нормальный ответ (приветствие/цена, без служебных маркеров) НЕ трогаем.
        clean = "Здравствуйте! NMAX на 7 дней — 449฿/день, депозит 3000฿. Какой район доставки?"
        self.assertEqual(suggest._strip_service_prefix(clean), clean)
        # Слово «цену» без служебного контекста тоже не должно триггерить срез.
        self.assertEqual(suggest._strip_service_prefix("Назову цену по датам."),
                         "Назову цену по датам.")

    def test_strip_service_prefix_never_empties(self):
        # Если весь черновик — только служебная строка: пусто НЕ возвращаем (отдаём как есть).
        only = "Клиент готов бронировать, менеджер ещё не назвал цену — сейчас Этап 1."
        self.assertEqual(suggest._strip_service_prefix(only), only)
        self.assertEqual(suggest._strip_service_prefix(""), "")

    def test_generate_draft_scrubs_service_prefix(self):
        # Сквозь generate_draft: LLM отдал служебный префикс → в черновике его нет, суть цела.
        def leaky(_s, _u):
            return ("Клиент готов бронировать — сейчас Этап 1.\n"
                    "Здравствуйте! CBR 650R — 650฿/день, депозит 5000฿.")
        d = suggest.generate_draft("[клиент]: CBR 650R на неделю с 7 июля", "ru", "FAQ",
                                   is_first_contact=True, call_llm=leaky)
        self.assertNotIn("Этап", d)
        self.assertNotIn("Клиент готов", d)
        self.assertTrue(d.startswith("Здравствуйте!"))     # приветствие сохранено
        self.assertIn("650฿/день", d)                      # цена/депозит целы
        self.assertIn("депозит 5000฿", d)

    def test_directive_in_prompt_preserves_invariants(self):
        # СТРАТЕГИЯ-директива входит в системный промпт ВЕРХНИМ приоритетом, но кап-цена и
        # критфакты (ценовая политика + CRITICAL_FACTS) НЕ ослабляются.
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          directive="дожимай на ADV, скажи что NMAX разберут")
        self.assertIn("ДИРЕКТИВА МЕНЕДЖЕРА", sysp)
        self.assertIn("дожимай на ADV", sysp)                    # директива на месте
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)      # кап-цена сохранена
        self.assertIn("CLICK 125", sysp)                         # критфакт дословно
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                  # ценовая политика (кап) на месте
        self.assertNotIn("ДИРЕКТИВА МЕНЕДЖЕРА",                  # без директивы блока нет
                         suggest.make_system_prompt("FAQ", "ru"))

    def test_regenerate_draft_injects_directive(self):
        seen = {}
        def fake(system, user):
            seen["system"] = system; seen["user"] = user
            return "перегенерённый ответ"
        out = suggest.regenerate_draft("[клиент]: NMAX на месяц?", "ru", "FAQ", False,
                                       "ЦЕНА из Календаря: 500฿/день", "жёстче про депозит", call_llm=fake)
        self.assertEqual(out, "перегенерённый ответ")
        self.assertIn("жёстче про депозит", seen["system"])      # директива в system
        self.assertIn("ЦЕНА из Календаря", seen["system"])       # кап сохранён
        self.assertIn("CLICK 125", seen["system"])               # критфакты сохранены
        self.assertEqual(seen["user"], "[клиент]: NMAX на месяц?")  # исходный транскрипт = user

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

    def test_generation_exception_posts_visible_note(self):
        # сбой генератора (claude CLI не найден) → видимая заметка в группу, НЕ молчаливый пропуск
        def boom(_s, _u):
            raise RuntimeError("claude CLI не найден (PATH/CLAUDE_BIN/AppData)")
        res = asyncio.run(suggest.on_client_message(self.client, self.sender, self.me,
                                                    call_llm=boom, faq="FAQ"))
        self.assertIsNone(res)
        notes = [t for t in self.client.sent if t[0] == suggest.MOD_GROUP_ID and "НЕ сгенерирован" in t[1]]
        self.assertEqual(len(notes), 1)
        self.assertIn("claude CLI не найден", notes[0][1])
        self.assertIn("@client1", notes[0][1])
        self.assertTrue(notes[0][1].lstrip().startswith("⚠️"))
        # карточки-черновика НЕ было, pending пуст
        self.assertFalse(any("Черновик ответа клиенту" in t[1] for t in self.client.sent))

    def test_empty_draft_posts_visible_note(self):
        # пустой вывод LLM → тоже видимая заметка (раньше молчали)
        res = asyncio.run(suggest.on_client_message(self.client, self.sender, self.me,
                                                    call_llm=lambda s, u: "", faq="FAQ"))
        self.assertIsNone(res)
        notes = [t for t in self.client.sent if "НЕ сгенерирован" in t[1]]
        self.assertEqual(len(notes), 1)
        self.assertIn("пустой вывод LLM", notes[0][1])
        self.assertIn("@client1", notes[0][1])

    def test_success_still_posts_card_no_note(self):
        # успех — карточка как раньше, никакой заметки о сбое
        mid = self._incoming()
        self.assertIsNotNone(mid)
        self.assertTrue(any("Черновик ответа клиенту" in t[1] for t in self.client.sent))
        self.assertFalse(any("НЕ сгенерирован" in t[1] for t in self.client.sent))

    def test_approve_sends_draft_to_client(self):
        mid = self._incoming()
        ev = FakeEvent(self.client, reply_to=mid, text="+")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertTrue(res["sent"])
        # отправка КЛИЕНТУ (id 999) с текстом черновика (среди отправок; после неё — ack в группу)
        client_sends = [t for t in self.client.sent if t[0] == 999]
        self.assertIn((999, "DRAFT ответа клиенту"), client_sends)
        self.assertEqual(res["ack"], "✅ Отправлено клиенту")
        pairs = self._pairs_lines()
        self.assertEqual(pairs[-1]["action"], "approve")
        self.assertEqual(pairs[-1]["client"], "@client1")

    def test_edit_sends_edited_version(self):
        mid = self._incoming()
        edited = "Здравствуйте! NMAX 155 — 449฿/день, депозит 3000฿."
        ev = FakeEvent(self.client, reply_to=mid, text=edited)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "edit")
        client_sends = [t for t in self.client.sent if t[0] == 999]
        self.assertIn((999, edited), client_sends)
        pairs = self._pairs_lines()
        self.assertEqual(pairs[-1]["edit"], edited)      # правка зафиксирована

    def test_reject_does_not_send(self):
        mid = self._incoming()
        ev = FakeEvent(self.client, reply_to=mid, text="-")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "reject")
        self.assertFalse(res["sent"])
        self.assertEqual([t for t in self.client.sent if t[0] == 999], [])  # клиенту ничего
        self.assertEqual(res["ack"], "❌ Отклонено")
        self.assertIsNone(suggest.pending.get(mid))      # pending снят

    def test_test_mode_does_not_send_to_client(self):
        suggest.SUGGEST_TEST_MODE = True
        mid = self._incoming()
        ev = FakeEvent(self.client, reply_to=mid, text="+")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertFalse(res["sent"])                    # TEST_MODE: клиенту НЕ ушло
        self.assertEqual([t for t in self.client.sent if t[0] == 999], [])
        self.assertIn("TEST_MODE", res["ack"])

    def test_reply_not_on_our_draft_ignored(self):
        self._incoming()
        ev = FakeEvent(self.client, reply_to=123456789, text="+")  # чужой reply
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertIsNone(res)

    def test_greeting_reflected_on_first_contact(self):
        # мок-LLM отражает, попросили ли в промпте приветствие; ЧИСТЫЙ первый контакт:
        # в истории ТОЛЬКО реплика клиента (нашего приветствия ещё не было).
        def refllm(system, _user):
            return "GREETED" if "ПЕРВЫЙ ответ" in system else "CONT"
        self.client.history = [FakeHistMsg(999, "Привет, сколько стоит NMAX на неделю?")]
        mid = asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=refllm, faq="FAQ")
        )
        rec = suggest.pending.get(mid)
        self.assertTrue(rec["first_contact"])
        self.assertEqual(rec["draft"], "GREETED")

    def test_no_regreet_when_greeting_in_history(self):
        # приветствие уже уходило (наше «Здравствуйте» в истории) → повторно НЕ здороваемся.
        def refllm(system, _user):
            return "GREETED" if "ПЕРВЫЙ ответ" in system else "CONT"
        self.client.history = [
            FakeHistMsg(self.me, "Здравствуйте! Что хотите арендовать?"),
            FakeHistMsg(999, "А сколько стоит NMAX на неделю?"),
        ]
        mid = asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=refllm, faq="FAQ")
        )
        rec = suggest.pending.get(mid)
        self.assertFalse(rec["first_contact"])
        self.assertEqual(rec["draft"], "CONT")

    def test_lang_stable_latin_model_name_stays_ru(self):
        # русский диалог + последняя реплика «Adv 350» (латиница) → черновик остаётся RU, НЕ EN.
        seen = {}
        def caplang(system, _user):
            seen["ru"] = "русском" in system
            return "ЧЕРНОВИК"
        self.client.history = [
            FakeHistMsg(self.me, "Здравствуйте! Что хотите арендовать?"),
            FakeHistMsg(999, "Какие байки есть в аренду?"),
            FakeHistMsg(999, "NMAX с 10 июля на месяц"),
            FakeHistMsg(999, "Adv 350"),
        ]
        mid = asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=caplang, faq="FAQ")
        )
        rec = suggest.pending.get(mid)
        self.assertEqual(rec["lang"], "ru")
        self.assertTrue(seen["ru"])


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


class TestGreeting(unittest.TestCase):
    """Приветствие на первом контакте; продолжение — без него."""

    def test_prompt_greets_on_first_contact(self):
        p = suggest.make_system_prompt("FAQ", "ru", is_first_contact=True)
        self.assertIn("ПЕРВЫЙ ответ", p)
        self.assertIn("приветств", p.lower())

    def test_prompt_no_greet_on_continuation(self):
        p = suggest.make_system_prompt("FAQ", "ru", is_first_contact=False)
        self.assertIn("ПРОДОЛЖЕНИЕ", p)
        self.assertIn("НЕ здоровайся", p)

    def test_first_contact_detection(self):
        now = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=datetime.timezone.utc)
        me = 42
        # наш ответ 2ч назад (в окне 18ч) → продолжение (False)
        cont = [FakeHistMsg(999, "вопрос", date=now - datetime.timedelta(hours=1)),
                FakeHistMsg(42, "ответ", date=now - datetime.timedelta(hours=2))]
        self.assertFalse(suggest.first_contact_from(cont, me, hours=18, now=lambda: now))
        # наш ответ 2 дня назад (вне окна) → первый контакт (True)
        old = [FakeHistMsg(999, "вопрос", date=now - datetime.timedelta(hours=1)),
               FakeHistMsg(42, "ответ", date=now - datetime.timedelta(days=2))]
        self.assertTrue(suggest.first_contact_from(old, me, hours=18, now=lambda: now))
        # только клиентские сообщения → первый контакт
        only = [FakeHistMsg(999, "привет", date=now - datetime.timedelta(hours=1))]
        self.assertTrue(suggest.first_contact_from(only, me, hours=18, now=lambda: now))


class _ReplyMsg:
    """Сообщение с полями, которые смотрит reply-логика (id/reply_to_msg_id/photo/media)."""
    def __init__(self, mid, sender_id, message="", reply_to=None, photo=False,
                 media=False, date=None):
        self.id = mid
        self.sender_id = sender_id
        self.message = message
        self.reply_to_msg_id = reply_to
        self.photo = object() if photo else None
        self.media = object() if media else None
        self.date = date


class _ReplyClient:
    """Мок клиента для _resolve_replies: get_messages(ids=) отдаёт цели из store (переписка
    вне окна). fail=True → get_messages падает (проверка fail-safe)."""
    def __init__(self, store, fail=False):
        self.store = store
        self.fail = fail

    async def get_messages(self, entity, ids=None):
        if self.fail:
            raise RuntimeError("boom")
        return [self.store.get(i) for i in (ids or [])]


class TestReplyContext(unittest.TestCase):
    """Голден 12.07: клиент шлёт «Вот» реплаями на данные из СТАРОЙ брони (Maps-ссылка, фото
    паспорта, телефон) — всё извлекается и попадает в контекст черновика."""

    MAPS = "https://maps.app.goo.gl/abc123XYZ"
    PHONE = "+66 81 234 5678"
    ME = 42

    def _window_and_store(self):
        # старая переписка (ВНЕ окна выборки) — цели reply клиента (id 999):
        store = {
            10: _ReplyMsg(10, 999, message=f"Моя вилла: {self.MAPS}"),
            11: _ReplyMsg(11, 999, photo=True),                # фото паспорта
            12: _ReplyMsg(12, 999, message=f"Мой номер {self.PHONE}"),
        }
        # окно (newest-first, как iter_messages): три «Вот» реплаями на 10/11/12 + наш вопрос
        window = [
            _ReplyMsg(23, 999, message="Вот", reply_to=12),
            _ReplyMsg(22, 999, message="Вот", reply_to=11),
            _ReplyMsg(21, 999, message="Вот", reply_to=10),
            _ReplyMsg(20, self.ME, message="Скиньте гео, паспорт и телефон"),
        ]
        return window, store

    def test_reply_pulls_geo_photo_phone_from_old_booking(self):
        window, store = self._window_and_store()
        asyncio.run(suggest._resolve_replies(_ReplyClient(store), None, window))
        tr = suggest.transcript_from(window, self.ME)
        self.assertIn(self.MAPS, tr)                 # гео-ссылка извлечена
        self.assertIn(self.PHONE, tr)                # телефон извлечён
        self.assertIn("паспорт", tr.lower())         # фото → пометка про паспорт
        # всё привязано к трём клиентским строкам-«Вот» (не потеряно в медиа-заглушке):
        embedded = [ln for ln in tr.splitlines()
                    if ln.startswith("[клиент]:") and "↩[в ответ на" in ln]
        self.assertEqual(len(embedded), 3)

    def test_reply_target_within_window(self):
        # цель reply лежит В окне — дозапрос не нужен, контент всё равно подтянут
        window = [
            _ReplyMsg(31, 999, message="Вот", reply_to=30),
            _ReplyMsg(30, 999, message=f"адрес {self.MAPS}"),
        ]
        asyncio.run(suggest._resolve_replies(_ReplyClient({}, fail=True), None, window))
        tr = suggest.transcript_from(window, self.ME)
        self.assertIn(self.MAPS, tr)

    def test_reply_resolve_failsafe(self):
        # дозапрос целей падает → без контекста reply, но черновик не рушится
        window, store = self._window_and_store()
        asyncio.run(suggest._resolve_replies(_ReplyClient(store, fail=True), None, window))
        tr = suggest.transcript_from(window, self.ME)   # не бросает
        self.assertNotIn("↩[в ответ на", tr)
        self.assertIn("Вот", tr)

    def test_non_reply_unaffected(self):
        window = [_ReplyMsg(41, 999, message="Привет, сколько стоит NMAX?")]
        asyncio.run(suggest._resolve_replies(_ReplyClient({}), None, window))
        tr = suggest.transcript_from(window, self.ME)
        self.assertEqual(tr, "[клиент]: Привет, сколько стоит NMAX?")


class _ModBase(unittest.TestCase):
    """Общий каркас: SUGGEST включён, tmp-хранилища, восстановление глобалей."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
                      suggest.APPROVER_USERNAMES, suggest.pending, suggest.PAIRS_FILE)
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = True         # безопасно по умолчанию: клиенту не уходит
        suggest.APPROVER_USERNAMES = set()        # пустой whitelist
        suggest.reset_disabled()
        suggest.limiter = suggest.RateLimiter(6, 15)
        suggest.pending = suggest.PendingStore(os.path.join(self._tmp.name, "p.jsonl"))
        suggest.PAIRS_FILE = os.path.join(self._tmp.name, "pairs.jsonl")

    def tearDown(self):
        (suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
         suggest.APPROVER_USERNAMES, suggest.pending, suggest.PAIRS_FILE) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def _add_pending(self, mid=555, draft="D"):
        suggest.pending.add(mid, {
            "client_id": 999, "client_ref": "@c", "lang": "ru",
            "incoming": "x", "draft": draft, "first_contact": True, "ts": "t",
        })
        return mid


class TestApproverWhitelist(_ModBase):

    def test_in_whitelist_passes(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        mid = self._add_pending()
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text="+", sender=FakeSender(1, username="Danya"), chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertIsNone(suggest.pending.get(mid))       # решение принято, pending снят

    def test_not_in_whitelist_denied(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        mid = self._add_pending()
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text="+", sender=FakeSender(2, username="stranger"), chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "denied")
        self.assertEqual(res["reason"], "not_approver")
        self.assertTrue(any("⛔" in t[1] for t in c.sent if t[0] == -100))  # видимый ⛔
        self.assertEqual([t for t in c.sent if t[0] == 999], [])            # клиенту ничего
        self.assertIsNotNone(suggest.pending.get(mid))     # pending НЕ снят — approver ещё решит

    def test_empty_whitelist_allows_any(self):
        suggest.APPROVER_USERNAMES = set()
        mid = self._add_pending()
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text="+", sender=FakeSender(3, username="whoever"), chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")


class TestModerationAcks(_ModBase):
    """NO-SILENT-PATHS: каждая ветка reply даёт видимый ack в группу; safety цел."""

    def _run(self, text, test_mode):
        suggest.SUGGEST_TEST_MODE = test_mode
        mid = self._add_pending(mid=556, draft="D")
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text=text, chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        acks = [t[1] for t in c.sent if t[0] == -100]
        client_sends = [t for t in c.sent if t[0] == 999]
        return res, acks, client_sends

    def test_reject_ack(self):
        res, acks, cs = self._run("-", True)
        self.assertTrue(any("❌ Отклонено" in a for a in acks))
        self.assertEqual(cs, [])

    def test_approve_testmode_ack_no_client_send(self):
        res, acks, cs = self._run("+", True)                 # SAFETY
        self.assertTrue(any("TEST_MODE" in a for a in acks))
        self.assertEqual(cs, [])                             # клиенту НИЧЕГО в TEST_MODE

    def test_edit_testmode_ack_no_client_send(self):
        res, acks, cs = self._run("Здравствуйте, вот цена", True)
        self.assertTrue(any("Правка принята" in a for a in acks))
        self.assertEqual(cs, [])

    def test_approve_live_ack_and_sends(self):
        res, acks, cs = self._run("+", False)
        self.assertTrue(any("Отправлено клиенту" in a for a in acks))
        self.assertIn((999, "D"), cs)                        # live: клиенту ушло

    def test_every_branch_gets_ack(self):
        for text in ("+", "-", "правка текстом"):
            _, acks, _ = self._run(text, True)
            self.assertTrue(acks, f"reply «{text}» остался без ack")


class TestCliLlm(unittest.TestCase):
    """Генерация через claude CLI (подписка Max). Всё замокано — реальный claude не дёргаем."""

    def setUp(self):
        self._api = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-SECRET-must-not-leak"   # должен быть вычищен из env CLI

    def tearDown(self):
        if self._api is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._api

    def test_cli_success_returns_stdout(self):
        captured = {}

        class P:
            returncode = 0
            stdout = _cli_json(result="  Здравствуйте! NMAX свободен.\nRESULT: ок  ")
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["cwd"] = kw.get("cwd")
            captured["env"] = kw.get("env")
            captured["timeout"] = kw.get("timeout")
            return P()
        with mock.patch.object(suggest, "SUGGEST_MODEL", "fable"), \
             mock.patch.object(suggest, "SUGGEST_MODEL_FALLBACK", "sonnet"), \
             mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", side_effect=fake_run):
            out = suggest._cli_llm("SYS", "USER")
        self.assertEqual(out, "Здравствуйте! NMAX свободен.\nRESULT: ок")   # поле result из JSON, .strip()
        # ЧИСТЫЙ генератор: --allowed-tools '' + нейтральный cwd (НЕ репо) + модель/промпты на месте
        self.assertIn("--allowed-tools", captured["cmd"])
        i = captured["cmd"].index("--allowed-tools")
        self.assertEqual(captured["cmd"][i + 1], "")
        self.assertIn("--system-prompt", captured["cmd"])
        self.assertIn("SYS", captured["cmd"])
        self.assertIn("USER", captured["cmd"])
        # кондуктор: основная модель fable + фолбэк sonnet + JSON-выхлоп (одним вызовом CLI)
        self.assertEqual(captured["cmd"][captured["cmd"].index("--model") + 1], "fable")
        self.assertEqual(captured["cmd"][captured["cmd"].index("--fallback-model") + 1], "sonnet")
        self.assertEqual(captured["cmd"][captured["cmd"].index("--output-format") + 1], "json")
        self.assertNotEqual(os.path.normcase(captured["cwd"] or ""), os.path.normcase(suggest.BASE_DIR))

    def test_cli_reports_fallback_model_from_modelusage(self):
        # кондуктор: основная модель недоступна → CLI сам добил фолбэком; реально отработавшую голову
        # (sonnet, БОЛЬШОЙ inputTokens) достаём из modelUsage, а не служебный haiku (крошечный вход).
        text, real = suggest._parse_cli_json(
            _cli_json(result="OK", main="claude-sonnet-5", main_in=3077, main_out=4))
        self.assertEqual(text, "OK")
        self.assertEqual(real, "claude-sonnet-5")   # НЕ haiku, хотя у него output больше (12 > 4)

    def test_cli_parse_is_error_returns_empty(self):
        text, real = suggest._parse_cli_json(_cli_json(result="что-то", is_error=True))
        self.assertEqual(text, "")                   # is_error → пустой текст (upstream пропустит)

    def test_cli_parse_broken_json_returns_empty(self):
        self.assertEqual(suggest._parse_cli_json("не json"), ("", ""))

    def test_cli_env_has_no_api_key(self):
        def fake_run(cmd, **kw):
            self.assertIsNotNone(kw.get("env"))
            self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])   # ключ НЕ утёк → CLI по подписке
            self.assertNotIn("OPENAI_API_KEY", kw["env"])

            class P:
                returncode = 0
                stdout = _cli_json(result="ok")
                stderr = ""
            return P()
        with mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", side_effect=fake_run):
            self.assertEqual(suggest._cli_llm("S", "U"), "ok")

    def test_cli_timeout_returns_empty(self):
        def fake_run(cmd, **kw):
            raise suggest.subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        with mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", side_effect=fake_run):
            self.assertEqual(suggest._cli_llm("S", "U"), "")   # таймаут → пусто (upstream пропустит)

    def test_cli_nonzero_returns_empty(self):
        class P:
            returncode = 1
            stdout = ""
            stderr = "boom"
        with mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", return_value=P()):
            self.assertEqual(suggest._cli_llm("S", "U"), "")

    def test_cli_not_found_raises(self):
        with mock.patch.object(suggest, "_resolve_claude", return_value=None):
            with self.assertRaises(RuntimeError):
                suggest._cli_llm("S", "U")

    def test_generate_draft_uses_cli_when_flag_on(self):
        calls = {"cli": 0, "api": 0}
        with mock.patch.object(suggest, "SUGGEST_LLM_VIA_CLI", True), \
             mock.patch.object(suggest, "_cli_llm", lambda s, u: calls.__setitem__("cli", calls["cli"] + 1) or "черновик"), \
             mock.patch.object(suggest, "_default_llm", lambda s, u: calls.__setitem__("api", 1) or "нет"):
            d = suggest.generate_draft("[клиент]: привет", "ru", "FAQ")
        self.assertEqual(d, "черновик")
        self.assertEqual((calls["cli"], calls["api"]), (1, 0))   # флаг ON → CLI, платный API не тронут

    def test_injected_call_llm_wins_over_flag(self):
        # мок-call_llm инъектируется поверх флага — прежние тесты не ломаются
        with mock.patch.object(suggest, "SUGGEST_LLM_VIA_CLI", True):
            d = suggest.generate_draft("[клиент]: привет", "ru", "FAQ", call_llm=_fake_llm)
        self.assertTrue(d)

    # --- ретрай резолва: транзиентный isfile==False не убивает черновик с первой осечки ---
    def test_resolve_retries_then_succeeds(self):
        seq = [None, r"C:\claude\claude.exe"]     # первый проход пусто, второй — нашли
        with mock.patch.object(suggest, "_resolve_claude_once", side_effect=seq), \
             mock.patch.object(suggest.time, "sleep") as slp:
            self.assertEqual(suggest._resolve_claude(retries=1, retry_sleep=2.0), r"C:\claude\claude.exe")
            slp.assert_called_once()               # была пауза перед второй попыткой

    def test_resolve_exhausted_returns_none(self):
        with mock.patch.object(suggest, "_resolve_claude_once", return_value=None), \
             mock.patch.object(suggest.time, "sleep"):
            self.assertIsNone(suggest._resolve_claude(retries=1, retry_sleep=0))

    def test_cli_llm_recovers_via_retry(self):
        # первая осечка isfile → вторая удача: _cli_llm НЕ падает, зовёт CLI (кейс теста 00:33)
        class P:
            returncode = 0
            stdout = _cli_json(result="Здравствуйте! NMAX свободен.")
            stderr = ""
        with mock.patch.object(suggest, "_resolve_claude_once", side_effect=[None, r"C:\claude\claude.exe"]), \
             mock.patch.object(suggest.time, "sleep"), \
             mock.patch.object(suggest.subprocess, "run", return_value=P()):
            self.assertEqual(suggest._cli_llm("S", "U"), "Здравствуйте! NMAX свободен.")

    # --- резолв из .env-файла: спасает сервис-контекст (%APPDATA%=systemprofile) ---
    def test_resolve_reads_claude_bin_from_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8") as f:
                f.write("BRIDGE_TOKEN=secret-не-читаем\nCLAUDE_BIN=" + exe + "\n")
            with mock.patch.object(suggest, "REPO_ENV", envf), \
                 mock.patch.object(suggest, "CLAUDE_BIN", "claude"), \
                 mock.patch.object(suggest.shutil, "which", return_value=None), \
                 mock.patch.object(suggest, "_claude_base_dirs", return_value=[os.path.join(d, "nope")]):
                self.assertEqual(suggest._resolve_claude_once(), exe)          # взято ИЗ .env-файла
                self.assertEqual(suggest._resolve_claude(retry_sleep=0), exe)  # и полный резолв тоже

    def test_env_file_bin_ignores_missing_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8") as f:
                f.write("# CLAUDE_BIN=C:\\commented\\claude.exe\nCLAUDE_BIN=C:\\nope\\claude.exe\n")
            with mock.patch.object(suggest, "REPO_ENV", envf):
                self.assertEqual(suggest._env_file_claude_bin(), "")           # путь не существует → ''

    def test_env_file_bin_handles_utf8_bom_first_line(self):
        # PowerShell 5.1 `Set-Content -Encoding utf8` пишет BOM; CLAUDE_BIN первой строкой не должен теряться
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8-sig") as f:                   # ← с BOM
                f.write("CLAUDE_BIN=" + exe + "\nBRIDGE_TOKEN=x\n")
            with mock.patch.object(suggest, "REPO_ENV", envf):
                self.assertEqual(suggest._env_file_claude_bin(), exe)

    def test_env_file_bin_survives_stray_nonutf8_on_other_line(self):
        # одинокий не-utf8 байт на ЧУЖОЙ строке не должен уронить парс до валидной строки CLAUDE_BIN
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "wb") as f:
                f.write(b"SOMEKEY=\xff\xfe bad\r\nCLAUDE_BIN=" + exe.encode("utf-8") + b"\r\n")
            with mock.patch.object(suggest, "REPO_ENV", envf):
                self.assertEqual(suggest._env_file_claude_bin(), exe)

    def test_msix_package_path_found_when_roaming_virtual(self):
        # Store/MSIX: Roaming\Claude невидим процессам Планировщика; реальный бинарь в
        # AppData\Local\Packages\Claude_*\LocalCache\... — резолвер обязан его найти.
        with tempfile.TemporaryDirectory() as d:
            local = os.path.join(d, "Local")
            cc = os.path.join(local, "Packages", "Claude_abc123", "LocalCache",
                              "Roaming", "Claude", "claude-code")
            pkg = os.path.join(cc, "2.1.197"); os.makedirs(pkg)
            exe = os.path.join(pkg, "claude.exe"); open(exe, "w").close()
            # (1) _claude_base_dirs включает MSIX-базу (по LOCALAPPDATA)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": local}, clear=False):
                self.assertTrue(any("Claude_abc123" in b and "Packages" in b
                                    for b in suggest._claude_base_dirs()))
            # (2) резолв находит реальный MSIX-бинарь (базы = только MSIX, .env/which пусты)
            with mock.patch.object(suggest.shutil, "which", return_value=None), \
                 mock.patch.object(suggest, "CLAUDE_BIN", "claude"), \
                 mock.patch.object(suggest, "REPO_ENV", os.path.join(d, "no.env")), \
                 mock.patch.object(suggest, "_claude_base_dirs", return_value=[cc]):
                self.assertEqual(suggest._resolve_claude_once(), exe)

    def test_service_context_reproduction_fixed(self):
        # РЕПРО живого бага: %APPDATA%=systemprofile, env-var CLAUDE_BIN нет → раньше None. Теперь .env спасает.
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8") as f:
                f.write("CLAUDE_BIN=" + exe + "\n")
            svc = r"C:\Windows\system32\config\systemprofile\AppData\Roaming\Claude\claude-code"
            with mock.patch.object(suggest, "REPO_ENV", envf), \
                 mock.patch.object(suggest, "CLAUDE_BIN", "claude"), \
                 mock.patch.object(suggest.shutil, "which", return_value=None), \
                 mock.patch.object(suggest, "_claude_base_dirs", return_value=[svc]):
                self.assertEqual(suggest._resolve_claude(retry_sleep=0), exe)


class TestParkModelsAllowlist(unittest.TestCase):
    """Модели клиенту — ТОЛЬКО реальный парк (Лист1). allowlist из fleet()/park_list.md + fail-safe."""

    def setUp(self):
        self._pf = suggest.PARK_LIST_FILE
        suggest.pricing._FLEET_CACHE["data"] = None      # сброс кэша fleet между тестами
        suggest.pricing._FLEET_CACHE["ts"] = 0

    def tearDown(self):
        suggest.PARK_LIST_FILE = self._pf
        suggest.pricing._FLEET_CACHE["data"] = None

    def _fleet(self, names):
        return lambda params: {"ok": True, "data": {"bikes": [{"name": n} for n in names]}}

    def test_allowlist_from_fleet_park_only(self):
        # реальный парк (имена как в Байки.xlsx Лист1) → только эти модели; «нет в парке» отсеяны
        names = ["NMAX 155CC BLACK PHUKET 4255", "XMAX 300CC GREY PHUKET 4246",
                 "ADV 350CC BLACK PHUKET 5849", "CB 300CC R 9011", "CBR 650R PHUKET 4505",
                 "MT-03 300СС BLUE PHUKET 5068", "CLICK 125CC PHUKET 5580"]
        allow = suggest.park_allowlist(getter=self._fleet(names))
        for m in ("NMAX 155", "XMAX 300", "ADV 350", "CB 300R", "CBR 650R", "MT-03"):
            self.assertIn(m, allow)
        for m in ("PCX 150", "PCX 160", "ADV 150", "ADV 160", "REBEL 300", "XSR 900", "R7", "CB 650R"):
            self.assertNotIn(m, allow)          # ← «нет в парке» НЕ попадают

    def test_prompt_hard_restricts_to_park(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", park_models=["NMAX 155", "XMAX 300", "ADV 350"])
        self.assertIn("ПАРК (СТРОГО)", sysp)
        self.assertIn("NMAX 155", sysp)
        self.assertIn("ADV 350", sysp)
        self.assertIn("НЕ предлагай", sysp)
        self.assertIn("PCX150", sysp)           # названы как «нет в парке — не предлагать»
        # без allowlist — блока НЕТ (fail-safe)
        self.assertNotIn("ПАРК (СТРОГО)", suggest.make_system_prompt("FAQ", "ru"))

    def test_failsafe_no_source_no_restriction(self):
        # fleet пуст + park_list.md отсутствует → None → бот отвечает как раньше (не онемел)
        suggest.PARK_LIST_FILE = os.path.join(tempfile.gettempdir(), "no_such_park_zzz.md")
        empty = lambda params: {"ok": False}
        self.assertIsNone(suggest.park_allowlist(getter=empty))
        self.assertNotIn("ПАРК (СТРОГО)", suggest.make_system_prompt("FAQ", "ru", park_models=None))
        d = suggest.generate_draft("[клиент]: NMAX?", "ru", "FAQ", call_llm=_fake_llm, park_models=None)
        self.assertTrue(d)                       # ← отвечает, ограничения нет

    def test_fallback_to_park_list_md_when_fleet_empty(self):
        with tempfile.TemporaryDirectory() as dd:
            pf = os.path.join(dd, "park_list.md")
            with open(pf, "w", encoding="utf-8") as f:
                f.write("| № | Название | Номер | Статус |\n|---|---|---|---|\n"
                        "| 1 | NMAX 155CC BLACK 4255 | 4255 | ДОМА |\n"
                        "| 2 | ADV 350CC GREY 798 | 798 | ДОМА |\n")
            suggest.PARK_LIST_FILE = pf
            allow = suggest.park_allowlist(getter=lambda p: {"ok": False})   # fleet пуст → файл
            self.assertIn("NMAX 155", allow)
            self.assertIn("ADV 350", allow)
            self.assertNotIn("PCX 150", allow)

    def test_price_cap_and_critfacts_intact_with_park(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          park_models=["NMAX 155"])
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)   # кап-цена цела
        self.assertIn("CLICK 125", sysp)                       # критфакт цел
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                # ценовая политика цела
        self.assertIn("ПАРК (СТРОГО)", sysp)

    def test_generate_draft_threads_park_into_prompt(self):
        cap = {}
        def capture(system, user):
            cap["s"] = system
            return "ok"
        suggest.generate_draft("[клиент]: что есть?", "ru", "FAQ", call_llm=capture,
                               park_models=["NMAX 155", "XMAX 300"])
        self.assertIn("ПАРК (СТРОГО)", cap["s"])
        self.assertIn("XMAX 300", cap["s"])


class TestPlaybook(unittest.TestCase):
    """Растущая книга правил (playbook): загрузка + подмешивание НИЖЕ кап-цены/критфактов + fail-safe."""

    def setUp(self):
        self._pf = suggest.PLAYBOOK_FILE

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._pf

    def test_seeded_playbook_has_park_rule(self):
        # боевой файл засеян первым правилом (модели только парка; Click не предлагать)
        txt = suggest.load_playbook()
        self.assertTrue(txt)
        self.assertIn("реального парка", txt)
        self.assertIn("Click 125", txt)

    def test_load_playbook_missing_returns_empty(self):
        suggest.PLAYBOOK_FILE = os.path.join(tempfile.gettempdir(), "no_such_playbook_zzz.md")
        self.assertEqual(suggest.load_playbook(), "")     # нет файла → '' (fail-safe)

    def test_playbook_block_below_critfacts_and_above_faq(self):
        sysp = suggest.make_system_prompt("ТЕЛО-FAQ", "ru", playbook="ПРАВИЛО-X: говорить мягко")
        self.assertIn("КНИГА ПРАВИЛ", sysp)
        self.assertIn("ПРАВИЛО-X", sysp)
        # СТРОГО НИЖЕ критфактов, ВЫШЕ общего FAQ:
        self.assertLess(sysp.index("КРИТИЧНЫЕ ФАКТЫ"), sysp.index("КНИГА ПРАВИЛ"))
        self.assertLess(sysp.index("КНИГА ПРАВИЛ"), sysp.index("FAQ и эталонные"))

    def test_playbook_empty_skipped_generation_ok(self):
        # пусто → блока нет, генерация цела (fail-safe)
        self.assertNotIn("КНИГА ПРАВИЛ", suggest.make_system_prompt("FAQ", "ru", playbook=""))
        d = suggest.generate_draft("[клиент]: NMAX?", "ru", "FAQ", call_llm=_fake_llm, playbook="")
        self.assertTrue(d)

    def test_playbook_does_not_override_price_and_critfacts(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          playbook="ПРАВИЛО-Y")
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)   # кап-цена цела
        self.assertIn("CLICK 125", sysp)                       # критфакт цел
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                # ценовая политика цела
        self.assertIn("НЕ отменяет", sysp)                     # playbook прямо помечен как неприоритетный

    def test_generate_draft_threads_playbook_into_prompt(self):
        cap = {}
        def capture(system, user):
            cap["s"] = system
            return "ok"
        suggest.generate_draft("[клиент]: привет", "ru", "FAQ", call_llm=capture,
                               playbook="ПРАВИЛО-Z: без давления")
        self.assertIn("КНИГА ПРАВИЛ", cap["s"])
        self.assertIn("ПРАВИЛО-Z", cap["s"])


class TestPlaybookAppend(unittest.TestCase):
    """Фаза 2: дозапись выученного правила в playbook.md (append + дата + дедуп + fail-safe)."""

    SEED = ("# Playbook\n\n## Стиль общения\n- коротко\n\n## Выученные правила\n"
            "- Предлагать клиенту только модели реального парка; Honda Click 125 не предлагать.\n")

    def setUp(self):
        self._pf = suggest.PLAYBOOK_FILE
        self._tmp = tempfile.TemporaryDirectory()
        self._file = os.path.join(self._tmp.name, "playbook.md")
        with open(self._file, "w", encoding="utf-8") as f:
            f.write(self.SEED)
        suggest.PLAYBOOK_FILE = self._file

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._pf
        self._tmp.cleanup()

    def _read(self):
        with open(self._file, encoding="utf-8") as f:
            return f.read()

    def test_append_adds_rule_with_date_in_learned_section(self):
        import datetime as _dt
        st = suggest.append_playbook_rule("Всегда уточняй район доставки заранее", now=_dt.date(2026, 7, 6))
        self.assertEqual(st, "added")
        txt = self._read()
        self.assertIn("- (2026-07-06) Всегда уточняй район доставки заранее", txt)   # с датой
        self.assertIn("только модели реального парка", txt)                          # append, не перезапись
        self.assertLess(txt.index("## Выученные правила"), txt.index("Всегда уточняй район"))

    def test_append_dedup_near_identical(self):
        st = suggest.append_playbook_rule(
            "предлагать клиенту только модели реального парка honda click 125 не предлагать")
        self.assertEqual(st, "duplicate")
        self.assertEqual(self._read().count("реального парка"), 1)   # дубль НЕ дописан

    def test_append_failsafe_on_missing_target(self):
        suggest.PLAYBOOK_FILE = os.path.join(self._tmp.name, "nope", "playbook.md")  # каталога нет
        self.assertEqual(suggest.append_playbook_rule("правило"), "error")

    def test_learned_rule_then_shows_in_prompt(self):
        suggest.append_playbook_rule("Всегда предлагай шлем в подарок на неделю аренды")
        sysp = suggest.make_system_prompt("FAQ", "ru", playbook=suggest.load_playbook())
        self.assertIn("шлем в подарок", sysp)
        self.assertIn("КНИГА ПРАВИЛ", sysp)


class TestSalesPressureAndSafety(unittest.TestCase):
    """Хвост 2 — рычаг SALES_PRESSURE НАД базой; Хвост 1 — жёсткое правило опыт/безопасность."""

    def setUp(self):
        self._sp = suggest.SALES_PRESSURE

    def tearDown(self):
        suggest.SALES_PRESSURE = self._sp

    def test_pressure_firm_block_and_cta(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pressure="firm")
        self.assertIn("НАСТОЙЧИВОСТИ", sysp)
        self.assertIn("FIRM", sysp)
        self.assertIn("предоплат", sysp.lower())          # явный CTA к предоплате

    def test_pressure_normal_no_block_regression(self):
        suggest.SALES_PRESSURE = "normal"
        normal = suggest.make_system_prompt("FAQ", "ru", pressure="normal")
        default = suggest.make_system_prompt("FAQ", "ru")  # дефолт из глобали (normal)
        self.assertNotIn("НАСТОЙЧИВОСТИ", normal)          # normal НЕ добавляет блок
        self.assertEqual(normal, default)                  # регресс: дефолт == явный normal

    def test_pressure_soft_block(self):
        self.assertIn("SOFT", suggest.make_system_prompt("FAQ", "ru", pressure="soft"))

    def test_pressure_invalid_falls_to_normal(self):
        self.assertEqual(suggest._pressure_block("garbage"), "")
        self.assertNotIn("НАСТОЙЧИВОСТИ", suggest.make_system_prompt("FAQ", "ru", pressure="garbage"))

    def test_pressure_default_reads_global(self):
        suggest.SALES_PRESSURE = "firm"
        self.assertIn("FIRM", suggest.make_system_prompt("FAQ", "ru"))   # без аргумента → глобаль

    def test_firm_keeps_invariants(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          park_models=["NMAX 155"], pressure="firm")
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)   # кап-цена цела
        self.assertIn("CLICK 125", sysp)                       # критфакт цел
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                # политика цела
        self.assertIn("★ ПАРК (СТРОГО)", sysp)                 # парк цел
        self.assertIn("НЕ нарушай", sysp)                      # firm явно защищает инварианты

    def test_experience_safety_rule_always_and_positioned(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", playbook="ПРАВИЛО-X")
        self.assertIn("ОПЫТ И БЕЗОПАСНОСТЬ", sysp)             # жёсткое правило A.3 есть всегда
        self.assertIn("ТОЛЬКО менеджер", sysp)
        # рядом с критфактами: ПОСЛЕ CRITICAL_FACTS, ВЫШЕ playbook
        self.assertLess(sysp.index("КРИТИЧНЫЕ ФАКТЫ"), sysp.index("ОПЫТ И БЕЗОПАСНОСТЬ"))
        self.assertLess(sysp.index("ОПЫТ И БЕЗОПАСНОСТЬ"), sysp.index("КНИГА ПРАВИЛ"))

    def test_approval_whitelist_rule_always_and_positioned(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", playbook="ПРАВИЛО-X")
        self.assertIn("БЕЛЫЙ СПИСОК", sysp)                    # белый список утверждаемого есть всегда
        self.assertIn("уточню у команды и вернусь", sysp)      # деградация вместо утверждения
        self.assertIn("[уточнить: цвет ADV350]", sysp)         # формат пометки менеджеру
        self.assertIn("цвет, комплектацию", sysp)              # цвет/комплектация — запрещено утверждать
        # рядом с критфактами: ПОСЛЕ CRITICAL_FACTS/ОПЫТ, ВЫШЕ playbook
        self.assertLess(sysp.index("ОПЫТ И БЕЗОПАСНОСТЬ"), sysp.index("БЕЛЫЙ СПИСОК"))
        self.assertLess(sysp.index("БЕЛЫЙ СПИСОК"), sysp.index("КНИГА ПРАВИЛ"))

    def test_approval_whitelist_note_not_stripped_as_service(self):
        # пометка «[уточнить: …]» адресована модератору — _strip_service_prefix её НЕ режет
        # (её маркер — «[внутренн», не «[уточнить»); ведущий срез трогает только стадию сделки.
        draft = "Уточню у команды и вернусь.\n[уточнить: цвет ADV350]"
        self.assertEqual(suggest._strip_service_prefix(draft), draft)


class TestDraftPostcheck(unittest.TestCase):
    """Пост-чек черновика (шаг 4/7 #243): утверждения о цвете/наличии/цене ВНЕ белого списка
    переписываются в «уточню»-форму + пометка модератору. Голдены — ДОСЛОВНЫЕ живые провалы 12.07
    (правило-класс CLAUDE.md: тест детекта = реальная фраза клиента/бота, не идеализация)."""

    # три дословных провала 12.07, каждый ДОЛЖЕН блокироваться пост-чеком
    G_COLOR = "выбора по цвету, к сожалению, нет"
    G_AVAIL_COLOR = "светлого ADV 350 сейчас нет — есть чёрный"
    G_PRICE = "9000 бат за 10 дней"

    def _client_part(self, out):
        """Часть, видимая клиенту (до пометки модератору «[уточнить: …]»)."""
        return out.split("[уточнить", 1)[0]

    def test_golden_color_blocked(self):
        out = suggest.postcheck_draft(self.G_COLOR, "ru", pricing_note="")
        self.assertNotEqual(out, self.G_COLOR)                       # переписан
        self.assertIn("уточню у команды", out.lower())               # деградация вместо утверждения
        self.assertNotIn("цвет", self._client_part(out).lower())     # цвет клиенту не утверждаем
        self.assertIn("[уточнить: цвет", out)                        # пометка модератору

    def test_golden_avail_color_blocked(self):
        out = suggest.postcheck_draft(self.G_AVAIL_COLOR, "ru", pricing_note="")
        self.assertNotEqual(out, self.G_AVAIL_COLOR)
        self.assertIn("уточню у команды", out.lower())
        cp = self._client_part(out).lower()
        self.assertNotIn("чёрн", cp)                                 # цвет не утверждаем
        self.assertNotIn("сейчас нет", cp)                           # наличие не утверждаем
        self.assertIn("ADV350", out)                                 # модель в пометке модератору

    def test_golden_price_total_blocked(self):
        # 9000 нет в белом источнике цен (pricing_note без него) → итог-за-период клеймится
        note = "ЦЕНА из Календаря: ADV 350 — 998฿/день."
        out = suggest.postcheck_draft(self.G_PRICE, "ru", pricing_note=note)
        self.assertNotEqual(out, self.G_PRICE)
        self.assertIn("уточню у команды", out.lower())
        self.assertNotIn("9000", self._client_part(out))             # выдуманный итог клиенту не уходит
        self.assertIn("[уточнить: цена 9000]", out)

    def test_price_from_whitelist_kept(self):
        # тот же итог, но ЧИСЛО есть в белом источнике (пришло из Календаря) → НЕ трогаем
        note = "ЦЕНА из Календаря: ADV 350 — за 10 дней 9000฿."
        text = "ADV 350 — 9000 бат за 10 дней."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note=note), text)

    def test_per_day_price_not_flagged(self):
        # суточный тариф из котировки (не итог-за-период) пост-чек не переписывает
        text = "NMAX на 7 дней — 449฿/день, депозит 3000฿."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note=""), text)

    def test_clean_draft_byte_for_byte(self):
        # нет нарушений → вход возвращается БАЙТ-В-БАЙТ (fail-safe, регресс не трогаем)
        text = "Здравствуйте! Подскажите даты аренды — подберём вариант."
        self.assertIs(suggest.postcheck_draft(text, "ru", pricing_note=""), text)

    def test_park_enumeration_allowed(self):
        # перечень моделей парка «есть NMAX и ADV 350» — в белом списке, не клеймим
        text = "У нас есть NMAX 155 и ADV 350."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note=""), text)

    def test_surrounding_text_preserved(self):
        # нарушение в середине — соседние законные предложения целы, переписан только виновный сегмент
        text = "Здравствуйте! Светлого ADV 350 сейчас нет. Чем ещё помочь?"
        out = suggest.postcheck_draft(text, "ru", pricing_note="")
        self.assertTrue(out.startswith("Здравствуйте!"))
        self.assertIn("Чем ещё помочь?", out)
        self.assertIn("Уточню у команды и вернусь.", out)
        self.assertNotIn("Светлого", self._client_part(out))

    def test_llm_tiebreak_flags_disputed(self):
        # спорный «ADV 350 будет к пятнице» (готовность-на-дату, склад-RE молчит) → LLM-тайбрейк
        def judge(_s, _u):
            return '{"violation": true, "kind": "avail"}'
        text = "ADV 350 точно готов будет к пятнице."
        out = suggest.postcheck_draft(text, "ru", pricing_note="", call_llm=judge)
        self.assertIn("уточню у команды", out.lower())
        self.assertNotIn("пятниц", self._client_part(out).lower())

    def test_llm_tiebreak_clears_disputed(self):
        # тот же спорный, но LLM говорит «не нарушение» → НЕ трогаем (fail-safe на сомнении)
        def judge(_s, _u):
            return '{"violation": false, "kind": "none"}'
        text = "ADV 350 готов вам помочь с выбором."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note="", call_llm=judge), text)


class TestPriceSheet(unittest.TestCase):
    """Прайс по всему парку: детект намерения, детерминированный рендер день/7/месяц из ЖИВОГО
    формата ячеек Bridge, капы в месячной колонке, allowlist-фильтр (CLICK/не-в-парке), анти-луп.
    Bridge замокан getter'ом — боевой Календарь НЕ трогаем."""

    # тариф по модели: (day_total, week_total, month_total, deposit, cap_active, cap_price)
    TAR = {
        "NMAX 155": (450, 2800, 9000, 5000, True, 8500),      # месяц 9000>cap8500 → «от 8500»
        "ADV 350": (749, 4928, 14606, 7000, True, 10900),     # месяц капнут
        "CB 300R": (757, 4716, 12491, 15000, True, 9900),     # мото, месяц капнут
        "XMAX 300": (700, 4200, 13000, 7000, False, 20000),   # кап НЕ активен → сумма месяца
    }
    FLEET_NAMES = ["NMAX 155CC BLACK PHUKET 4255", "ADV 350CC BLACK PHUKET 5849",
                   "CB 300CC R 9011", "XMAX 300CC GREY PHUKET 4246",
                   "CLICK 125CC PHUKET 5580"]   # CLICK физически в парке, но НЕ сдаём

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _getter(self):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            bk = suggest._bike_key(bike)
            key = next((k for k in self.TAR if suggest._bike_key(k) in bk), None)
            if key is None:
                return {"ok": False}
            d1, d7, d30, dep, ca, cp = self.TAR[key]
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    # Реальное сообщение клиента (дословно, 21:11) — провалившийся живой тест 68cfc31.
    # Правило-класс: фразы для тестов детекта = РЕАЛЬНЫЕ клиентские, не идеализированные.
    LIVE_PHRASE = ("Какие марки и модели байков вы предлагаете? Какие у вас цены на аренду? "
                   "(Стоимость за день, неделю и месяц для разных моделей.) Требуется ли депозит?")

    # ---- ЭТАП 2: детект намерения (позитив/негатив) ----
    def test_detect_positive(self):
        for s in [# старые явные формы
                  "цены на все модели на 15.07-15.08", "пришлите прайс", "прайс-лист по всему парку",
                  "сколько стоит аренда всех байков", "all models price for a month", "price list please",
                  "дайте цены по всем моделям",
                  # ЖИВАЯ фраза клиента + парафразы RU/EN (перечень моделей / цены-в-целом)
                  self.LIVE_PHRASE,
                  "какие модели байков у вас есть?",
                  "сколько стоит аренда байка в день и в неделю?",
                  "что по ценам на прокат скутеров?",
                  "подскажите стоимость аренды на месяц",
                  "какие у вас расценки на прокат?",
                  "what bikes do you have?",
                  "what are your rental prices?",
                  "how much is the rental per day, week and month?",
                  "price per day, week or month for different models?"]:
            self.assertTrue(suggest._asks_price_sheet(s.lower(), s.lower()), s)

    def test_detect_negative(self):
        for s in [# явная одна модель / нет цены-парка
                  "сколько стоит NMAX", "цена на ADV350 на месяц", "всё включено в цену?",
                  "у вас все байки новые?", "какой депозит", "можно два байка?",
                  # одна названная модель → точечный quote, приветствие, ТОЛЬКО депозит
                  "сколько стоит nmax на неделю", "какие цены на nmax?",
                  "здравствуйте!", "добрый день, вы работаете?",
                  "нужен ли депозит?", "какой залог?"]:
            self.assertFalse(suggest._asks_price_sheet(s.lower(), s.lower()), s)

    def test_live_phrase_end_to_end_yields_grid_no_date_question(self):
        # ЖИВАЯ фраза клиента дословно → hints.price_sheet_q True → сетка СРАЗУ, без вопроса про даты/модель.
        hints = suggest.extract_booking_hints(f"[клиент]: {self.LIVE_PHRASE}",
                                              today=datetime.date(2026, 7, 11))
        self.assertTrue(hints["price_sheet_q"])          # детект на реальной фразе сработал
        self.assertIsNone(hints["model"])                # конкретной модели нет → не точечный quote
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)            # сетка по парку
        self.assertIn("• Сутки: 450 ฿", note)              # реальные цифры сразу
        self.assertNotIn("Попроси", note)                # НЕ просим даты
        self.assertNotIn("НЕ называй НИКАКУЮ цену", note)  # НЕ ушли в гейт дат

    def test_live_2227_prior_model_mention_still_yields_grid(self):
        # КОРЕНЬ живого провала 22:27 (код aae1ed2): та же прайс-фраза, но клиент РАНЬШЕ в окне
        # упомянул модель («интересует nmax»). Старый модель-гард сканировал ВСЁ окно и глушил
        # сетку → бот спрашивал даты. Теперь каталог-вопрос сетку сохраняет. Реальный путь боя
        # (extract_booking_hints по многосообщенческому транскрипту), НЕ идеализированная одиночка.
        tr = ("[клиент]: привет, интересует nmax\n"
              "[менеджер]: Здравствуйте!\n"
              f"[клиент]: {self.LIVE_PHRASE}")
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])              # каталог-вопрос перебивает старую модель окна
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)            # сетка, а не гейт дат
        self.assertIn("• Сутки: 450 ฿", note)              # реальные цифры сразу
        self.assertNotIn("Попроси", note)                # НЕ просим даты
        self.assertNotIn("НЕ называй НИКАКУЮ цену", note)

    def test_live_2227_two_message_first_contact_yields_grid(self):
        # Первое обращение ДВУМЯ сообщениями: сперва «хочу adv», следом каталог+цены — тоже сетка.
        tr = ("[клиент]: здравствуйте, хочу adv\n"
              f"[клиент]: {self.LIVE_PHRASE}")
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])

    def test_catalog_question_with_model_example_yields_grid(self):
        # Каталог-вопрос в ОДНОМ сообщении с моделью-примером → всё равно сетка (смещение к показу).
        for s in ["какие модели предлагаете, например nmax, и какие цены?",
                  "what models do you have, like adv, and prices?"]:
            self.assertTrue(suggest._asks_price_sheet(s.lower(), s.lower()), s)

    def test_pointed_single_model_in_window_stays_negative(self):
        # Точечный диалог по ОДНОЙ модели без каталог-вопроса — сетку НЕ включаем (негатив цел),
        # даже если ценовые слова размазаны по окну.
        self.assertFalse(suggest._asks_price_sheet("а на неделю?", "сколько стоит nmax а на неделю?"))
        self.assertFalse(suggest._asks_price_sheet("какие цены на nmax?", "какие цены на nmax?"))

    def test_hints_flag_set_on_episode(self):
        h = suggest.extract_booking_hints("[клиент]: нужны цены на все модели на 15.07-15.08")
        self.assertTrue(h["price_sheet_q"])
        self.assertTrue(h["has_dates"])

    # ---- ЭТАП 1+3: детерминированный рендер + allowlist-фильтр ----
    def test_rows_exclude_click_and_out_of_park(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        models = [r["model"] for r in rows]
        self.assertIn("NMAX 155", models)
        self.assertIn("CB 300R", models)          # мото матчится (CC-стрип)
        self.assertNotIn("CLICK 125", models)     # несдаваемая — исключена
        for m in ("PCX 150", "REBEL 300", "XSR 900", "R7"):
            self.assertNotIn(m, models)           # не-в-парке отсутствуют

    def test_render_deterministic_numbers(self):
        # ЭТАП-формат: карточка на байк (заголовок + Сутки/Неделя/Месяц + Депозит), цифры из quote.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("NMAX 155\n• Сутки: 450 ฿\n• Неделя (7 дней): 2800 ฿\n"
                      "• Месяц: от 8500 ฿\n• Депозит: 5000 ฿ / паспорт", block)
        self.assertIn("ADV 350\n• Сутки: 749 ฿\n• Неделя (7 дней): 4928 ฿\n"
                      "• Месяц: от 10900 ฿\n• Депозит: 7000 ฿ / паспорт", block)

    def test_render_blank_line_between_bike_cards(self):
        # между карточками моделей — ПУСТАЯ строка; внутри карточки пустых строк нет (карточка цельная).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("\n\n", block)                                     # разделитель-пустая строка есть
        cards = block.split("\n\n")
        self.assertGreaterEqual(len(cards), 2)                           # ≥2 модели → ≥2 карточки
        for c in cards:
            self.assertNotIn("\n\n", c)                                  # карточка цельная (без пустых строк)
            self.assertTrue(c.splitlines()[0].strip())                   # первая строка карточки — модель

    def test_month_cap_applied_only_when_over_cap(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        # XMAX кап НЕ активен → показываем СУММУ месяца (13000), не «от»
        self.assertIn("XMAX 300\n• Сутки: 700 ฿\n• Неделя (7 дней): 4200 ฿\n• Месяц: 13000 ฿", block)
        self.assertNotIn("• Месяц: от 13000 ฿", block)

    def test_month_cap_en_prefix(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "en")
        self.assertIn("• Month: from 8500 ฿", block)    # EN: «from», не «от»
        self.assertIn("NMAX 155\n• Daily: 450 ฿\n• Week (7 days): 2800 ฿\n"
                      "• Month: from 8500 ฿\n• Deposit: 5000 ฿ / passport", block)
        self.assertNotIn("месяц", block)                # RU-строк нет
        self.assertNotIn("Сутки", block)

    # ---- ЭТАП 2: анти-луп в note (новый класс: блок в скобках, инструкция — метка) ----
    def test_note_has_numbers_no_promise_no_reask(self):
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("450 ฿", note)                    # реальные цифры в блоке (внутри скобок)
        self.assertIn("[PRICE_SHEET]", note)            # инструкция про метку для LLM
        self.assertIn("ДОСЛОВНО", note)                 # инвариант: вставит КОД дословно
        self.assertIsNotNone(suggest._sheet_block_from_note(note))   # блок извлекаем для сборки

    def test_note_no_dates_yields_sheet_no_date_question(self):
        # прайс-интент БЕЗ дат → сетка СРАЗУ (день/7/месяц), НИ ОДНОГО вопроса про даты.
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "has_dates": False}, lang="ru",
            getter=self._getter(), today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("• Сутки: 450 ฿", note)              # реальные цифры сразу, без гейта дат
        self.assertNotIn("Попроси", note)                # НЕ просим даты у клиента
        self.assertIn("старт завтра", note)              # дефолтный якорь = ближайшая дата
        self.assertIn("12.07.2026", note)                # завтра от 11.07 (инъекция today)
        self.assertIn("НЕ переспрашивай даты", note)     # анти-луп: даты не блокируют выдачу

    def test_dates_in_history_used_for_anchor(self):
        # даты в истории (15.07–15.08) → расчёт по ним (якорь = дата из диалога, не «завтра»).
        hints = suggest.extract_booking_hints(
            "[клиент]: нужны цены на все модели на 15.07-15.08", today=datetime.date(2026, 7, 11))
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        self.assertIn("Дата отсчёта: 15.07.2026", note)  # якорь из истории
        self.assertIn("• Сутки: 450 ฿", note)
        self.assertNotIn("старт завтра", note)           # не дефолтный якорь

    def test_min_term_line_ru_and_en(self):
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        ru = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        self.assertIn("Минимальный срок: скутеры от 5 дней, мотоциклы от 3", ru)
        en = suggest.build_pricing_note(hints, lang="en", getter=self._getter())
        self.assertIn("scooters from 5 days, motorcycles from 3", en)

    def test_min_term_line_from_real_constants(self):
        # строка мин-срока строится из SCOOTER_MIN_DAYS/MOTO_MIN_DAYS, не из литералов.
        self.assertEqual((suggest.SCOOTER_MIN_DAYS, suggest.MOTO_MIN_DAYS), (5, 3))
        line = suggest._sheet_min_term_line("ru")
        self.assertIn(f"скутеры от {suggest.SCOOTER_MIN_DAYS} дней", line)
        self.assertIn(f"мотоциклы от {suggest.MOTO_MIN_DAYS}", line)

    def test_season_note_low_from_quote(self):
        # кап активен в моке → пометка низкого сезона выведена из живого quote (наличие сезона — не
        # хардкод); конец сезона — документированная граница парка «до 31 октября».
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=self._getter())
        self.assertIn("Цены низкого сезона, действуют до 31 октября", note)

    def test_season_note_en(self):
        # EN-аналог сезонной пометки низкого сезона.
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="en", getter=self._getter())
        self.assertIn("Low-season prices, valid until 31 October", note)

    def test_season_note_in_header_above_cards(self):
        # сезонная строка — В ШАПКЕ прайса (выше карточек моделей), а не в хвосте.
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=self._getter())
        self.assertLess(note.index("низкого сезона"), note.index("NMAX 155"))

    def test_season_note_absent_when_no_cap(self):
        # ни у одной модели кап не активен → сезонную пометку НЕ утверждаем.
        def getter(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            bk = suggest._bike_key(bike)
            key = next((k for k in self.TAR if suggest._bike_key(k) in bk), None)
            if key is None:
                return {"ok": False}
            d1, d7, d30, dep, _ca, _cp = self.TAR[key]
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": False,
                    "cap_price": None, "text": f"{bike} {days}d {total}"}}
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=getter)
        self.assertIn("ПРАЙС ПО ПАРКУ", note)            # сетка всё равно есть
        self.assertNotIn("низкого сезона", note)         # но сезон не утверждаем
        self.assertNotIn("31 октября", note)             # и конец сезона не называем

    def test_month_cap_reflected_in_note(self):
        # кап низкого сезона отражён в месячной колонке промпт-блока («от <cap>»).
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=self._getter())
        self.assertIn("• Месяц: от 8500 ฿", note)        # NMAX капнут (кап в месячной ячейке карточки)

    def test_numbers_come_only_from_quote(self):
        # инвариант: числа в блоке = суммы из quote (код подставляет, LLM не трогает).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        for total in (450, 2800, 749, 4928, 700, 4200):   # day/week totals из TAR
            self.assertIn(f"{total} ฿", block)

    def test_anti_loop_forbids_date_question_on_price(self):
        # ANTI_LOOP явно запрещает вопрос про даты при переданном ПРАЙС ПО ПАРКУ.
        self.assertIn("вопрос про даты клиенту НЕ задавай", suggest.ANTI_LOOP_NOTE)
        self.assertIn("ПРАЙС ПО ПАРКУ", suggest.ANTI_LOOP_NOTE)

    def test_note_unavailable_no_numbers_no_reask(self):
        empty = lambda p: {"ok": False}                 # Bridge не отдаёт цифр
        note = suggest.build_pricing_note({"price_sheet_q": True, "iso_start": "2026-07-15",
                                           "has_dates": True}, lang="ru", getter=empty)
        self.assertIn("недоступны", note)
        self.assertNotIn("450", note)
        self.assertNotIn("฿", note)

    # ---- база: ANTI_LOOP всегда в промпте, инварианты целы ----
    def test_anti_loop_in_base_prompt(self):
        sysp = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("БЕЗ ПЕРЕСПРОСОВ", sysp)
        self.assertIn("НЕ обещай", sysp)
        # ценовая политика и критфакты целы (f0b9316 / инварианты)
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)
        self.assertIn("CLICK 125", sysp)

    def test_sheet_block_hidden_from_prompt_marker_instead(self):
        # НОВЫЙ класс (вёрстка 21:36 #276): сетку вставляет КОД — из промпта LLM блок ВЫРЕЗАН
        # (переписать нечего по построению), вместо него метка-инструкция [PRICE_SHEET].
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note=note)
        self.assertNotIn("• Сутки: 450 ฿", sysp)        # цифры сетки LLM НЕ видит
        self.assertNotIn("<<<SHEET>>>", sysp)           # служебные скобки в промпт не текут
        self.assertIn("[PRICE_SHEET]", sysp)            # метка-инструкция на месте
        self.assertIn("БЕЗ ПЕРЕСПРОСОВ", sysp)
        # а извлечённый блок для сборки — дословный, с цифрами
        block = suggest._sheet_block_from_note(note)
        self.assertIn("• Сутки: 450 ฿", block)


class TestPriceSheetMinAcrossVariants(unittest.TestCase):
    """Правило-класс: минимум-по-вариантам для НЕ-XMAX-моделей (несколько живых юнитов одной модели →
    каждая колонка сетки = МИНИМУМ по вариантам через ЖИВОЙ quote). XMAX — ИСКЛЮЧЕНИЕ (родитель 243,
    шаг 1/7): два поколения = ДВА продукта отдельными строками («XMAX 300» старое / «XMAX 300 New Gen»
    новое 2023+), минимум-по-вариантам для XMAX СНЯТ (иначе наценка нового поколения пропала бы —
    старый провал показывал единый «от 8900»). Bridge замокан getter'ом (Календарь НЕ трогаем),
    цифры приходят ТОЛЬКО из мок-quote своих юнитов, в коде НЕ хардкодятся."""

    # 3 живых СТАРЫХ XMAX (2020-2022, дешевле) + 1 НОВЫЙ (2023+, дороже) + одиночная NMAX для регресса.
    OLD_XMAX = ["XMAX 300CC GREY PHUKET 4246", "XMAX 300CC BLUE PHUKET 4247",
                "XMAX 300CC BLACK PHUKET 4248"]
    NEW_XMAX = ["XMAX 300CC NEW 2023 PHUKET 7701"]
    FLEET_NAMES = OLD_XMAX + NEW_XMAX + ["NMAX 155CC BLACK PHUKET 4255"]

    # тариф: (day_total, week_total, month_total, deposit, cap_active, cap_price)
    OLD = (790, 4700, 23700, 5000, True, 8900)     # старый XMAX: кап 8900, депозит 5000
    NEW = (939, 5600, 28170, 7000, True, 9900)     # новый XMAX: кап 9900, депозит 7000
    NMAX = (450, 2800, 9000, 5000, True, 8500)     # одиночная модель — регресс (не должна измениться)

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _tar_for(self, bike):
        """Тариф ПО ИМЕНИ юнита (не только по модели): новый 2023+ дороже старого; NMAX — своя."""
        if "NMAX" in bike.upper():
            return self.NMAX
        if "2023" in bike or "NEW" in bike.upper():
            return self.NEW
        return self.OLD

    def _getter(self):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            d1, d7, d30, dep, ca, cp = self._tar_for(bike)
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    def test_xmax_grid_splits_old_and_new_gen(self):
        # ГОЛДЕН (родитель 243, шаг 1/7): живы старые XMAX (790/8900/деп5000) рядом с новым
        # (939/9900/деп7000) → в сетке ДВЕ отдельные строки со СВОИМИ цифрами (минимум НЕ схлопывает).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("XMAX 300\n• Сутки: 790 ฿\n• Неделя (7 дней): 4700 ฿\n"
                      "• Месяц: от 8900 ฿\n• Депозит: 5000 ฿ / паспорт", block)       # старое поколение
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿\n• Неделя (7 дней): 5600 ฿\n"
                      "• Месяц: от 9900 ฿\n• Депозит: 7000 ฿ / паспорт", block)       # новое поколение
        # обе строки живы и цены РАЗНЫЕ (не единый минимум)
        self.assertIn("790 ฿", block)
        self.assertIn("939 ฿", block)

    def test_live_phrase_grid_shows_both_xmax_gens(self):
        # Живой прогон «какие модели и цены» (дословная фраза клиента) → в сетке ОБА поколения XMAX.
        phrase = ("Какие марки и модели байков вы предлагаете? Какие у вас цены на аренду? "
                  "(Стоимость за день, неделю и месяц для разных моделей.) Требуется ли депозит?")
        hints = suggest.extract_booking_hints(f"[клиент]: {phrase}", today=datetime.date(2026, 7, 11))
        self.assertTrue(hints["price_sheet_q"])
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("XMAX 300\n• Сутки: 790 ฿", note)             # старое поколение
        self.assertIn("• Месяц: от 8900 ฿", note)
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿", note)     # новое поколение
        self.assertIn("• Месяц: от 9900 ฿", note)

    def test_single_variant_model_unchanged(self):
        # РЕГРЕСС: одиночная модель (один вариант в парке) — цифры БЕЗ изменений (мин по одному == он сам).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("NMAX 155\n• Сутки: 450 ฿\n• Неделя (7 дней): 2800 ฿\n"
                      "• Месяц: от 8500 ฿\n• Депозит: 5000 ฿ / паспорт", block)

    def test_pointwise_xmax_shows_both_gens(self):
        # ГОЛДЕН (родитель 243, шаг 1/7): точечный quote по XMAX на неделю → ОБЕ строки-поколения
        # со СВОИМИ цифрами (старое 4700 vs New Gen 5600), а не одна цена-минимум. Реальные фразы
        # клиента (правило-класс: golden-тест детекта = дословное сообщение, не идеализированное).
        for phrase in ("сколько стоит xmax на неделю с 15 июля?",
                       "аренда xmax 15.07-22.07 сколько?",
                       "цена xmax на неделю с 15 июля",
                       "почём xmax на неделю с 15 июля?",
                       "xmax на неделю с 15 июля какая цена?"):
            hints = suggest.extract_booking_hints(f"[клиент]: {phrase}",
                                                  today=datetime.date(2026, 7, 11))
            note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                              today=datetime.date(2026, 7, 11))
            self.assertIn("- XMAX 300: ", note, phrase)             # строка старого поколения
            self.assertIn("- XMAX 300 New Gen: ", note, phrase)     # строка нового поколения
            self.assertIn("7d 4700", note, phrase)                  # неделя старого — своя цифра
            self.assertIn("7d 5600", note, phrase)                  # неделя нового — РАЗНАЯ цифра
            # старую строку квотировал ТОЛЬКО старый юнит, новую — ТОЛЬКО новый (поколения не смешаны)
            old_line = next(l for l in note.splitlines() if l.startswith("- XMAX 300:"))
            new_line = next(l for l in note.splitlines() if l.startswith("- XMAX 300 New Gen:"))
            self.assertNotIn("NEW", old_line.upper())
            self.assertIn("NEW", new_line.upper())

    def test_pointwise_non_xmax_single_line_unchanged(self):
        # РЕГРЕСС: точечный quote по НЕ-XMAX (NMAX) — одна строка _wrap_single, без разворота вариантов.
        hints = suggest.extract_booking_hints("[клиент]: сколько стоит nmax на неделю с 15 июля?",
                                              today=datetime.date(2026, 7, 11))
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ЦЕНА из Календаря", note)          # одиночная модель → _wrap_single
        self.assertNotIn("- NMAX", note)                  # НЕ развёрнута в bullets
        self.assertNotIn("New Gen", note)                 # у NMAX поколений нет

    def test_min_helpers_pick_cheapest_column_independently(self):
        # Юнит на редьюсер: сутки/неделя — по сумме, месяц — по кап-«от», депозит — общий минимум.
        old = {"total": 23700, "deposit": 5000, "cap_active": True, "cap_price": 8900}
        new = {"total": 28170, "deposit": 7000, "cap_active": True, "cap_price": 9900}
        self.assertEqual(suggest._sheet_q_month(old), 8900)
        self.assertEqual(suggest._sheet_q_month(new), 9900)
        self.assertIs(suggest._sheet_min_variant([new, old], suggest._sheet_q_month), old)
        self.assertEqual(suggest._sheet_min_deposit({"month": [new, old]}), 5000)
        # без цап — величина месяца = сумма (min из сумм)
        self.assertEqual(suggest._sheet_q_month({"total": 13000, "cap_active": False}), 13000)


class TestPriceSheetManyVariantsRobust(unittest.TestCase):
    """Класс-голден живого регресса 20:59 (после 551987e): реальный парк = МНОГО юнитов на модель
    (~сотня живых quote на сетку). Требования класса: (1) битый юнит (quote кидает/молчит) НЕ валит
    сетку — пропуск с логом, колонки из живых остальных; (2) построение параллельное и ограничено
    дедлайном — на большом парке укладывается в разумную стену (последовательно 20:59 было 6м16с);
    (3) минимум-по-вариантам НЕ ослаблен."""

    # Парк как живой: XMAX 3 старых (кап 8900) + 6 новых (кап 9900), ADV 6 юнитов, NMAX одиночка.
    OLD_XMAX = [f"XMAX 300CC OLD-{i} PHUKET 400{i}" for i in range(3)]
    NEW_XMAX = [f"XMAX 300CC NEW-{i} PHUKET 870{i}" for i in range(6)]
    ADV = [f"ADV 350CC UNIT-{i} PHUKET 580{i}" for i in range(6)]
    FLEET_NAMES = OLD_XMAX + NEW_XMAX + ADV + ["NMAX 155CC BLACK PHUKET 4255"]
    OLD = (790, 4700, 23700, 5000, True, 8900)
    NEW = (939, 5600, 28170, 7000, True, 9900)
    ADVT = (749, 4928, 14606, 7000, True, 10900)
    NMAX = (450, 2800, 9000, 5000, True, 8500)

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _getter(self, broken=(), slow=(), delay=0.0):
        """Мок Bridge: broken — юниты, чей quote КИДАЕТ; slow — юниты со сном delay (для дедлайна)."""
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            if any(b in bike for b in broken):
                raise RuntimeError(f"битый юнит {bike} (живой формат строки Лист1 сломал quote)")
            if any(s in bike for s in slow):
                time.sleep(delay)
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            up = bike.upper()
            tar = (self.NMAX if "NMAX" in up else self.ADVT if "ADV" in up
                   else self.NEW if "NEW" in up else self.OLD)
            d1, d7, d30, dep, ca, cp = tar
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    def test_broken_unit_skipped_grid_survives(self):
        # ГОЛДЕН класса: один старый XMAX бит (quote кидает) → строка старого поколения ЦЕЛА
        # (живы 2 других старых, 790/8900), New Gen тоже жив (939/9900), соседи не задеты.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter(broken=("OLD-1",)))
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("XMAX 300\n• Сутки: 790 ฿\n• Неделя (7 дней): 4700 ฿\n"
                      "• Месяц: от 8900 ฿\n• Депозит: 5000 ฿ / паспорт", block)
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿", block)
        self.assertIn("ADV 350\n• Сутки: 749 ฿", block)
        self.assertIn("NMAX 155\n• Сутки: 450 ฿", block)

    def test_all_old_units_broken_only_new_gen_row_survives(self):
        # ВСЕ старые XMAX биты → строка старого поколения выпадает (нет живых юнитов), остаётся
        # только «XMAX 300 New Gen» (939/9900) — честно из живых, не пустота и не обвал.
        rows = suggest.price_sheet("2026-07-15",
                                   getter=self._getter(broken=("OLD-0", "OLD-1", "OLD-2")))
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿", block)
        self.assertIn("• Месяц: от 9900 ฿", block)
        self.assertNotIn("XMAX 300\n• Сутки: 790", block)  # старое поколение выпало (все юниты биты)
        self.assertNotIn("XMAX 300\n• Сутки: 939", block)  # New Gen НЕ подменяет строку старого
        self.assertIn("NMAX 155\n• Сутки: 450 ฿", block)   # соседи не задеты

    def test_many_variants_build_is_parallel_fast(self):
        # Класс «разумное время»: 16 юнитов × 3 срока (48 quote) по 0.2с сна каждый. Последовательно
        # это 9.6с; пул (8 воркеров) обязан уложиться заметно быстрее. Порог щедрый — не флапает.
        t0 = time.time()
        rows = suggest.price_sheet("2026-07-15",
                                   getter=self._getter(slow=("XMAX", "ADV", "NMAX"), delay=0.2))
        dt = time.time() - t0
        self.assertTrue(rows)
        self.assertLess(dt, 6.0, f"параллельный билд занял {dt:.1f}с — пул не работает")

    def test_deadline_expired_units_skipped_not_hang(self):
        # Юниты, висящие ДОЛЬШЕ дедлайна, пропускаются: сетка выходит из успевших, без зависания.
        import unittest.mock as mock
        with mock.patch.object(suggest, "_SHEET_DEADLINE", 1):
            t0 = time.time()
            rows = suggest.price_sheet("2026-07-15",
                                       getter=self._getter(slow=("ADV",), delay=8.0))
            dt = time.time() - t0
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertLess(dt, 7.0, f"дедлайн не сработал: билд {dt:.1f}с")
        self.assertIn("XMAX 300", block)                   # успевшие модели в сетке
        self.assertNotIn("ADV 350\n• Сутки: 749 ฿", block)  # висящий юнит не дождались — без цифр


class TestSheetAwarePricePolicy(unittest.TestCase):
    """Класс-голден второй ноги живого регресса 20:59 (черновик #275): сетка ДОШЛА до промпта, но
    ценовая политика («Дат нет — сперва спроси даты», цена только из блока «ЦЕНА из Календаря»)
    конфликтовала с блоком «ПРАЙС ПО ПАРКУ» — LLM 9/10 переспрашивал даты/опыт, выбрасывая сетку.
    Фикс у источника: при sheet-режиме политика сама велит выдать прайс дословно без переспроса."""

    SHEET_NOTE = ("ПРАЙС ПО ПАРКУ из Календаря — приведи цифры И строки мин-срока/сезона ДОСЛОВНО…\n"
                  "XMAX 300\n• Сутки: 593 ฿\n• Месяц: от 8900 ฿")

    def test_sheet_mode_policy_orders_grid_not_date_question(self):
        p = suggest.make_system_prompt("faq", "ru", pricing_note=self.SHEET_NOTE)
        self.assertIn("ГОТОВЫЙ блок «ПРАЙС ПО ПАРКУ из Календаря»", p)
        self.assertIn("Даты НЕ переспрашивай", p)
        self.assertIn("Вопрос об опыте НЕ заменяет выдачу прайса", p)
        self.assertNotIn("Дат нет — сперва спроси даты", p)      # конфликт-источник УБРАН
        self.assertIn("приведи прайс-сетку из блока «ПРАЙС ПО ПАРКУ» ДОСЛОВНО", p)  # этап 1

    def test_sheet_mode_en_note_triggers_too(self):
        p = suggest.make_system_prompt("faq", "en",
                                       pricing_note="PARK PRICE LIST from the Calendar …\nXMAX …")
        self.assertIn("ГОТОВЫЙ блок «ПРАЙС ПО ПАРКУ из Календаря»", p)
        self.assertNotIn("Дат нет — сперва спроси даты", p)

    def test_regular_price_note_policy_unchanged(self):
        # РЕГРЕСС: обычный ценовой путь (ЦЕНА по датам / вообще без цены) — прежняя политика.
        for note in ("", "ЦЕНА из Календаря: XMAX 300 на 15.07–20.07 — 3500 ฿ …"):
            p = suggest.make_system_prompt("faq", "ru", pricing_note=note)
            self.assertIn("цену клиенту называй ТОЛЬКО если она передана ниже", p)
            self.assertIn("Дат нет — сперва спроси даты", p)
            self.assertNotIn("ГОТОВЫЙ блок «ПРАЙС ПО ПАРКУ", p)
            self.assertIn("Этап 1 — ЦЕНА: назови цену по датам из Календаря", p)


class TestSheetUntouchableBlock(unittest.TestCase):
    """Класс-голден вёрстки 21:36 (#276): сетка = НЕПРИКОСНОВЕННЫЙ блок. LLM цифры не видит и не
    переписывает; финал собирает КОД: intro + render_price_sheet ДОСЛОВНО + outro. Вёрстка —
    плоский текст: группы «Скутеры:»/«Мотоциклы:», карточка-на-байк, пустые строки, БЕЗ markdown."""

    ROWS = [
        {"model": "NMAX 155", "class": ("scooter", 5, "этот байк"), "bike": "NMAX 155CC X",
         "cells": {"day": {"total": 450}, "week": {"total": 2800},
                   "month": {"total": 9000, "cap_active": True, "cap_price": 8500}},
         "deposit": 3000},
        {"model": "XMAX 300", "class": ("scooter", 5, "этот байк"), "bike": "XMAX 300CC X",
         "cells": {"day": {"total": 790}, "week": {"total": 4700},
                   "month": {"total": 23700, "cap_active": True, "cap_price": 8900}},
         "deposit": 5000},
        {"model": "CB 300R", "class": ("moto", 3, "мотоциклы"), "bike": "CB 300CC R",
         "cells": {"day": {"total": 757}, "week": {"total": 4716},
                   "month": {"total": 12491, "cap_active": True, "cap_price": 9900}},
         "deposit": 15000},
    ]

    def test_render_groups_flat_no_markdown(self):
        # ГОЛДЕН вёрстки: группы, карточки, пустые строки, ноль markdown-символов.
        block = suggest.render_price_sheet(self.ROWS, "2026-07-15", "ru")
        self.assertNotIn("**", block)                          # сырой markdown = живой провал #276
        self.assertNotIn("__", block)
        self.assertIn("Скутеры:\n\nNMAX 155\n• Сутки: 450 ฿", block)
        self.assertIn("Мотоциклы:\n\nCB 300R\n• Сутки: 757 ฿", block)
        self.assertLess(block.index("Скутеры:"), block.index("Мотоциклы:"))   # порядок групп
        # карточка-на-байк + пустая строка между карточками внутри группы
        self.assertIn("• Депозит: 3000 ฿ / паспорт\n\nXMAX 300\n• Сутки: 790 ฿", block)
        self.assertIn("• Месяц: от 8900 ฿", block)             # цифры целы (регресс минимума)

    def test_render_groups_en(self):
        block = suggest.render_price_sheet(self.ROWS, "2026-07-15", "en")
        self.assertIn("Scooters:\n\nNMAX 155\n• Daily: 450 ฿", block)
        self.assertIn("Motorcycles:\n\nCB 300R", block)
        self.assertNotIn("**", block)

    def test_compose_marker_variants(self):
        # Сборка кодом: метка отдельной строкой / в скобках с точкой / инлайн — intro+блок+outro.
        block = "Скутеры:\n\nXMAX 300\n• Сутки: 790 ฿"
        for marker in ("[PRICE_SHEET]", "PRICE_SHEET", "[price sheet].", "«[PRICE_SHEET]»"):
            out = suggest.compose_sheet_draft(f"Здравствуйте!\n{marker}\nПодскажите модель.",
                                              block, "ru")
            self.assertEqual(out, "Здравствуйте!\n\n" + block + "\n\nПодскажите модель.", marker)

    def test_compose_inline_marker(self):
        block = "XMAX 300\n• Сутки: 790 ฿"
        out = suggest.compose_sheet_draft("Вот прайс: [PRICE_SHEET] Жду вопросов.", block, "ru")
        self.assertEqual(out, "Вот прайс:\n\n" + block + "\n\nЖду вопросов.")

    def test_compose_no_marker_fallback_deterministic(self):
        # LLM не выдал метку (или переписал прайс своим текстом) → его текст ОТБРАСЫВАЕМ,
        # каркас детерминированный, блок дословно. Сетка доходит ВСЕГДА.
        block = "XMAX 300\n• Сутки: 790 ฿\n• Месяц: от 8900 ฿"
        bad_llm = "XMAX 300 — 790 ฿/сутки, **дешево**"        # однострочник + markdown, без метки
        out = suggest.compose_sheet_draft(bad_llm, block, "ru")
        self.assertIn(block, out)                              # блок дословно
        self.assertNotIn("**", out)                            # LLM-переписывание не протекло
        self.assertIn("Актуальный прайс", out)
        out_en = suggest.compose_sheet_draft(bad_llm, block, "en")
        self.assertIn("Here is our current price list:", out_en)

    def test_generate_draft_assembles_by_code(self):
        # Уровень generate_draft: fake-LLM ставит метку → финал собран КОДОМ, блок дословный.
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        rows = self.ROWS
        with mock.patch.object(suggest, "price_sheet", return_value=rows):
            note = suggest.build_pricing_note(hints, lang="ru", getter=lambda p: {"ok": False})
        block = suggest._sheet_block_from_note(note)
        self.assertIsNotNone(block)
        fake = lambda system, user: "Здравствуйте! Вот наш прайс:\n[PRICE_SHEET]\nКакая модель интересна?"
        draft = suggest.generate_draft("[клиент]: цены?", "ru", "FAQ",
                                       pricing_note=note, call_llm=fake)
        self.assertIn(block, draft)                            # рендер ДОСЛОВНО в черновике
        self.assertTrue(draft.startswith("Здравствуйте! Вот наш прайс:"))
        self.assertTrue(draft.endswith("Какая модель интересна?"))
        self.assertNotIn("[PRICE_SHEET]", draft)               # метка заменена
        self.assertNotIn("**", draft)

    def test_generate_draft_llm_rewrite_cannot_leak(self):
        # LLM вопреки всему переписал прайс однострочниками с ** и без метки → его текст отброшен,
        # финал = детерминированный каркас + блок дословно. Модель прайс не переписывает НИКОГДА.
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        with mock.patch.object(suggest, "price_sheet", return_value=self.ROWS):
            note = suggest.build_pricing_note(hints, lang="ru", getter=lambda p: {"ok": False})
        block = suggest._sheet_block_from_note(note)
        fake = lambda system, user: "**Скутеры**\n- XMAX 300 — 790 ฿/сутки, от 8900 ฿/месяц"
        draft = suggest.generate_draft("[клиент]: цены?", "ru", "FAQ",
                                       pricing_note=note, call_llm=fake)
        self.assertIn(block, draft)
        self.assertNotIn("**", draft)
        self.assertNotIn("790 ฿/сутки", draft)                 # однострочник LLM не протёк

    def test_regenerate_draft_same_class(self):
        # Перегенерация (СТРАТЕГИЯ-директива) — тот же класс сборки кодом.
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        with mock.patch.object(suggest, "price_sheet", return_value=self.ROWS):
            note = suggest.build_pricing_note(hints, lang="ru", getter=lambda p: {"ok": False})
        block = suggest._sheet_block_from_note(note)
        fake = lambda system, user: "Добрый день!\n[PRICE_SHEET]\nНа связи."
        draft = suggest.regenerate_draft("[клиент]: цены?", "ru", "FAQ", False,
                                         note, "мягче тон", call_llm=fake)
        self.assertIn(block, draft)
        self.assertNotIn("[PRICE_SHEET]", draft)


class TestSheetSubselection(unittest.TestCase):
    """Подвыборки сетки («скутеры 200+», «мотоциклы до 400») — ДЕТЕРМИНИРОВАННЫЙ отбор КОДОМ из тех
    же rows, что и полная сетка (та же точка правды), БЕЗ LLM-отбора моделей. Класс-голден живого
    провала 12.07 15:46: XADV 750 (скутер 750cc) в «скутерах 200+» у LLM-отбора ТЕРЯЛСЯ. Тесты
    детекта на РЕАЛЬНОЙ фразе клиента («скутеры 200+») + парафразах RU/EN + негативах."""

    # тариф по модели: (day_total, week_total, month_total, deposit, cap_active, cap_price)
    TAR = {
        "NMAX 155": (450, 2800, 9000, 5000, True, 8500),       # скутер 155cc
        "ADV 350": (749, 4928, 14606, 7000, True, 10900),      # скутер 350cc
        "XADV 750": (2788, 18000, 55000, 25000, False, 0),     # скутер 750cc — тот, что «терялся»
        "CB 300R": (757, 4716, 12491, 15000, True, 9900),      # мотоцикл 300cc
        "CBR 650R": (1200, 7000, 22000, 20000, False, 0),      # мотоцикл 650cc
    }
    FLEET_NAMES = ["NMAX 155CC BLACK PHUKET 4255", "ADV 350CC BLACK PHUKET 5849",
                   "XADV 750CC GREY PHUKET 4290", "CB 300CC R 9011", "CBR 650R PHUKET 4505"]

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _getter(self):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            bk = suggest._bike_key(bike)
            key = next((k for k in self.TAR if suggest._bike_key(k) in bk), None)
            if key is None:
                return {"ok": False}
            d1, d7, d30, dep, ca, cp = self.TAR[key]
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    # ---- рабочий объём из метки модели ----
    def test_model_cc_from_label(self):
        self.assertEqual(suggest._model_cc("XADV 750"), 750)
        self.assertEqual(suggest._model_cc("NMAX 155"), 155)
        self.assertEqual(suggest._model_cc("CB 300R"), 300)
        self.assertEqual(suggest._model_cc("XMAX 300 New Gen"), 300)   # New Gen без цифр не мешает
        self.assertIsNone(suggest._model_cc("MT-03"))                  # 2-значный хвост серии — не cc
        self.assertIsNone(suggest._model_cc("R7"))

    # ---- детерминированная подвыборка rows (та же точка правды, что и сетка) ----
    def test_filter_scooters_200plus_keeps_xadv(self):
        # ГЛАВНОЕ требование задачи: XADV 750 (скутер 750cc) в «скутерах 200+» ПРИСУТСТВУЕТ.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        sub = suggest.filter_sheet_rows(rows, kind="scooter", cc_min=200)
        models = [r["model"] for r in sub]
        self.assertIn("XADV 750", models)          # 750cc скутер НЕ потерян (живой провал 12.07)
        self.assertIn("ADV 350", models)
        self.assertNotIn("NMAX 155", models)       # 155 < 200 — отсечён
        self.assertNotIn("CB 300R", models)        # мотоцикл — не тот класс
        self.assertNotIn("CBR 650R", models)

    def test_filter_moto_up_to_400(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        sub = suggest.filter_sheet_rows(rows, kind="moto", cc_max=400)
        models = [r["model"] for r in sub]
        self.assertIn("CB 300R", models)           # 300 ≤ 400
        self.assertNotIn("CBR 650R", models)       # 650 > 400 — отсечён
        self.assertNotIn("XADV 750", models)       # скутер — не тот класс
        self.assertNotIn("NMAX 155", models)

    def test_filter_unknown_cc_kept_failopen(self):
        # cc не прочли (MT-03 / R7) → строку класса НЕ выкидываем (fail-open, корень провала XADV).
        rows = [{"model": "MT-03", "class": ("moto", 3, "мотоциклы"), "cells": {"day": {"total": 700}}}]
        self.assertEqual([r["model"] for r in suggest.filter_sheet_rows(rows, kind="moto", cc_max=400)],
                         ["MT-03"])
        self.assertEqual([r["model"] for r in suggest.filter_sheet_rows(rows, kind="moto", cc_min=200)],
                         ["MT-03"])

    def test_full_sheet_unchanged_regression(self):
        # РЕГРЕСС сетки: без подвыборки rows = ВЕСЬ парк, формат/цифры целы.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        for m in ("NMAX 155", "ADV 350", "XADV 750", "CB 300R", "CBR 650R"):
            self.assertIn(m, block)
        self.assertIn("XADV 750\n• Сутки: 2788 ฿", block)   # цифры/формат карточки не тронуты
        self.assertLess(block.index("Скутеры:"), block.index("Мотоциклы:"))

    # ---- детект подвыборки из СЛОВ клиента (реальная фраза + парафразы RU/EN + негативы) ----
    def test_parse_scooters_min_positive(self):
        # «скутеры 200+» — дословная формулировка задачи (живой черновик 12.07 15:46) + парафразы.
        for s in ["скутеры 200+",
                  "какие есть скутеры от 200 кубов?",
                  "интересуют скутеры больше 200",
                  "покажите скутеры свыше 200 кубов",
                  "scooters from 200cc please",
                  "what scooters over 200 do you have?"]:
            f = suggest._parse_sheet_filter(s.lower(), s.lower())
            self.assertIsNotNone(f, s)
            self.assertEqual(f["kind"], "scooter", s)
            self.assertEqual(f["cc_min"], 200, s)
            self.assertIsNone(f["cc_max"], s)

    def test_parse_moto_max_positive(self):
        for s in ["мотоциклы до 400",
                  "какие мотоциклы до 400 кубов?",
                  "мотоцикл не больше 400",
                  "мотоциклы меньше 400 кубов",
                  "motorcycles under 400cc",
                  "what motorcycles up to 400 do you have?"]:
            f = suggest._parse_sheet_filter(s.lower(), s.lower())
            self.assertIsNotNone(f, s)
            self.assertEqual(f["kind"], "moto", s)
            self.assertEqual(f["cc_max"], 400, s)
            self.assertIsNone(f["cc_min"], s)

    def test_parse_negatives(self):
        # Ни класса-подвыборки, ни границы cc → None (полная сетка). Даты/сроки за cc НЕ считаем.
        for s in ["какие цены на все модели",
                  "сколько стоит nmax",
                  "пришлите прайс-лист",
                  "нужны все модели на 15.07-15.08",     # даты, не cc; нет класс-слова
                  "какие байки у вас есть?",              # «байк» — не scooter/moto
                  "какие есть скутеры и мотоциклы?"]:     # оба класса → весь парк
            self.assertIsNone(suggest._parse_sheet_filter(s.lower(), s.lower()), s)

    # ---- сквозной путь: hints → build_pricing_note (КОД фильтрует, LLM не отбирает) ----
    def test_end_to_end_scooters_200plus_keeps_xadv(self):
        tr = "[клиент]: какие есть скутеры от 200 кубов и цены на аренду?"
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])
        self.assertEqual(h["sheet_filter"], {"kind": "scooter", "cc_min": 200, "cc_max": None})
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("XADV 750", note)            # 750cc скутер в подвыборке — не потерян
        self.assertIn("ADV 350", note)
        self.assertNotIn("NMAX 155", note)         # 155 < 200 — отфильтрован КОДОМ
        self.assertNotIn("CB 300R", note)          # мотоцикл — не тот класс
        self.assertNotIn("CBR 650R", note)

    def test_end_to_end_moto_up_to_400(self):
        tr = "[клиент]: какие мотоциклы до 400 кубов и почём аренда?"
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])
        self.assertEqual(h["sheet_filter"], {"kind": "moto", "cc_min": None, "cc_max": 400})
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("CB 300R", note)             # 300 ≤ 400
        self.assertNotIn("CBR 650R", note)         # 650 > 400
        self.assertNotIn("XADV 750", note)         # скутер — не тот класс
        self.assertNotIn("NMAX 155", note)

    def test_end_to_end_no_filter_full_grid_regression(self):
        tr = "[клиент]: пришлите прайс по всему парку"
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])
        self.assertIsNone(h["sheet_filter"])
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        for m in ("NMAX 155", "ADV 350", "XADV 750", "CB 300R", "CBR 650R"):
            self.assertIn(m, note)                 # без подвыборки — весь парк


if __name__ == "__main__":
    unittest.main(verbosity=2)
