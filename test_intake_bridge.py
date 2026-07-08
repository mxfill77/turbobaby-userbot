# -*- coding: utf-8 -*-
"""
test_intake_bridge.py — мок-тесты O3-2c «Заявка → INTAKE». БЕЗ реального Telegram/Telethon/IPC-боя:
очередь intake в temp-БД, постинг через инъектируемый poster, хендлер «✅ В CRM» на фейках.
Сценарии а–г из задания.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_intake_bridge -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import asyncio
import tempfile
import unittest
from unittest import mock

import booking_draft
import moderation_ipc
import suggest


ALLOW = ["NMAX 155", "XMAX 300", "ADV 350", "PCX 150", "ADV 160", "FORZA 300"]


def parse_intake(text):
    """Грубый парсер карточки «🆕 БРОНЬ» → {ключ: значение} (как сделал бы INTAKE по префиксам строк)."""
    d = {}
    for ln in text.splitlines():
        if ln.startswith("🆕"):
            continue
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k.strip()] = v.strip()
    return d


# --------- (а)/(б)/Click: сборка текста поста из валидных полей (чистые функции) ---------

class TestBuildIntake(unittest.TestCase):

    # (а) полная заявка → пост со всеми валидными полями, структура парсится INTAKE-полями
    def test_a_full_intake_parses(self):
        ex = {"model": "NMAX 155", "name": "Иван", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15", "price_day": "449", "deposit": "3000",
              "helmets": "2", "contact": "@ivan", "note": "Патонг"}
        txt = booking_draft.build_intake(ex, allowlist=ALLOW, meta={"client_ref": "@ivan"})
        self.assertTrue(txt.startswith("🆕 БРОНЬ"))
        d = parse_intake(txt)
        self.assertEqual(d["Модель"], "NMAX 155")
        self.assertIn("Иван", d["Клиент"])
        self.assertIn("@ivan", d["Клиент"])
        self.assertIn("10.07.2026", d["Даты"])
        self.assertIn("15.07.2026", d["Даты"])
        self.assertIn("(5 дн.)", d["Даты"])
        self.assertEqual(d["Даты ISO"], "2026-07-10 – 2026-07-15")   # ISO + человеческие
        self.assertEqual(d["Шлемы"], "2")
        self.assertEqual(d["Депозит"], "деньги")
        self.assertEqual(d["Доставка"], "Патонг")

    # (б) заявка с пропусками (⚠️/—) → пропущенные поля НЕ в посте
    def test_b_gaps_omitted(self):
        ex = {"model": "Suzuki Burgman", "name": "Оля", "date_from": None,
              "date_to_datetime": None, "price_day": None, "deposit": "7000 и паспорт",
              "helmets": None, "contact": "@olya", "note": "доставка Краби"}
        txt = booking_draft.build_intake(ex, allowlist=ALLOW, meta={"client_ref": "@olya"})
        d = parse_intake(txt)
        self.assertTrue(txt.startswith("🆕 БРОНЬ"))
        self.assertIn("Оля", d.get("Клиент", ""))       # валидное — есть
        self.assertNotIn("Модель", d)                   # не из парка, черновика нет → нет строки
        self.assertNotIn("Даты", d)                     # дат нет
        self.assertNotIn("Депозит", d)                  # конфликт ฿+паспорт → не включаем
        self.assertNotIn("Доставка", d)                 # доставка вне Пхукета (⚠️) → не включаем
        self.assertNotIn("Шлемы", d)

    def test_click_not_sendable(self):
        ex = {"model": "Honda Click 125", "name": "Петя", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15", "price_day": "250", "deposit": "3000",
              "helmets": "1", "contact": "@petya", "note": "Патонг"}
        self.assertIsNone(booking_draft.build_intake(ex, allowlist=ALLOW, meta={}))

    def test_make_booking_and_intake_returns_both(self):
        ex = {"model": "NMAX 155", "name": "Иван", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15", "price_day": "449", "deposit": "паспорт",
              "helmets": "1", "contact": "@ivan", "note": "Патонг"}
        def _llm(_t):
            import json
            return json.dumps(ex, ensure_ascii=False)
        qf = mock.Mock(return_value={"status": "ok", "quote": {"day_price": 449, "available": True, "days": 5}})
        card, intake = booking_draft.make_booking_and_intake(
            "dlg", call_llm=_llm, allowlist=ALLOW, quote_fn=qf, meta={"client_ref": "@ivan"})
        self.assertIn("📋 ЗАЯВКА", card)
        self.assertTrue(intake.startswith("🆕 БРОНЬ"))


# --------------- очередь intake + постинг userbot-аккаунтом (temp-БД) ---------------

class TestIntakeQueueAndPoster(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save_db = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()

    def tearDown(self):
        moderation_ipc.DB_PATH = self._save_db
        self._tmp.cleanup()

    def test_confirm_flips_draft_to_pending(self):
        iid = moderation_ipc.save_intake_candidate("🆕 БРОНЬ\nКлиент: X")
        self.assertEqual(moderation_ipc.get_intake(iid)["status"], "draft")
        self.assertTrue(moderation_ipc.confirm_intake(iid))
        self.assertEqual(moderation_ipc.get_intake(iid)["status"], "pending")
        self.assertFalse(moderation_ipc.confirm_intake(iid))   # повторно — уже не draft

    def test_userbot_posts_to_constant_group(self):
        iid = moderation_ipc.save_intake_candidate("🆕 БРОНЬ\nКлиент: X")
        moderation_ipc.confirm_intake(iid)
        calls = []
        async def poster(client, gid, text):
            calls.append((gid, text))
            return 555
        n = asyncio.run(suggest.poll_and_post_intake(object(), poster=poster))
        self.assertEqual(n, 1)
        self.assertEqual(calls[0][0], suggest.INBOX_GROUP_ID)   # адресат — СТРОГО константа группы
        row = moderation_ipc.get_intake(iid)
        self.assertEqual(row["status"], "posted")
        self.assertEqual(row["posted_msg_id"], 555)

    def test_userbot_rejects_non_prefixed_text(self):
        iid = moderation_ipc.save_intake_candidate("привет, это не карточка")
        moderation_ipc.confirm_intake(iid)
        calls = []
        async def poster(client, gid, text):
            calls.append(text)
            return 1
        asyncio.run(suggest.poll_and_post_intake(object(), poster=poster))
        self.assertEqual(calls, [])                              # наружу НЕ постим
        self.assertEqual(moderation_ipc.get_intake(iid)["status"], "failed")

    def test_draft_not_posted_until_confirmed(self):
        moderation_ipc.save_intake_candidate("🆕 БРОНЬ\nКлиент: X")  # остаётся draft
        calls = []
        async def poster(client, gid, text):
            calls.append(text)
            return 1
        n = asyncio.run(suggest.poll_and_post_intake(object(), poster=poster))
        self.assertEqual(n, 0)
        self.assertEqual(calls, [])                              # draft не уходит без «✅ В CRM»


# ------------------- хендлер «✅ В CRM» на фейках Telegram -------------------

class FakeUser:
    def __init__(self, username="approver"):
        self.username = username


class FakeMsg:
    def __init__(self):
        self.chat_id = 123
        self.text = "📋 ЗАЯВКА (черновик)\nA=Бронь · ..."


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.from_user = FakeUser()
        self.message = FakeMsg()
        self.edited_text = None
        self.markup_kept = False

    async def answer(self):
        pass

    async def edit_message_text(self, text, **kw):
        self.edited_text = text

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_kept = True


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class FakeContext:
    def __init__(self):
        self.bot = FakeBot()


class TestConfirmIntakeHandler(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save_db = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        self._save_appr = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = set()   # пустой whitelist → approve любому (детерминизм теста)

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._save_appr
        moderation_ipc.DB_PATH = self._save_db
        self._tmp.cleanup()

    # (в) тап при мёртвом IPC → честная ошибка на карточке, модербот НЕ падает
    def test_c_dead_ipc_error_no_crash(self):
        import moderation_bot
        q = FakeQuery("crm:5")
        ctx = FakeContext()
        with mock.patch.object(moderation_ipc, "confirm_intake", side_effect=RuntimeError("db dead")):
            asyncio.run(moderation_bot._confirm_intake(ctx, q, "crm:5"))   # не должно бросить
        self.assertTrue(any("Не удалось поставить" in t for _, t in ctx.bot.sent))
        self.assertTrue(q.markup_kept)          # кнопку оставили для ретрая
        self.assertIsNone(q.edited_text)        # карточку НЕ пометили как отправленную

    def test_confirm_success_edits_card_and_enqueues(self):
        import moderation_bot
        iid = moderation_ipc.save_intake_candidate("🆕 БРОНЬ\nКлиент: X")
        q = FakeQuery(f"crm:{iid}")
        ctx = FakeContext()
        asyncio.run(moderation_bot._confirm_intake(ctx, q, f"crm:{iid}"))
        self.assertIsNotNone(q.edited_text)
        self.assertIn("Входящие брони", q.edited_text)
        self.assertEqual(moderation_ipc.get_intake(iid)["status"], "pending")

    def test_confirm_intake_kb_callback_shape(self):
        import moderation_bot
        kb = moderation_bot._kb_intake(42)
        flat = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
        self.assertTrue(any("В CRM" in t and c == "crm:42" for t, c in flat))


if __name__ == "__main__":
    unittest.main()
