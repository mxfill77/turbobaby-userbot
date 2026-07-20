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


# ------------- O3-2.1 (хвост §7): мост дособирает Maps-гео и фото паспорта -------------

class TestBridgeAutoCollect(unittest.TestCase):
    """(1) ссылка Google Maps из КЛИЕНТСКИХ строк → поле «Доставка:» поста; ссылки менеджера
    (наши точки в приветствии) НЕ подхватываются; (2) фото из диалога пересылается СРАЗУ ЗА
    карточкой (client_id через intake-очередь); (3) нет в диалоге → прежнее поведение (страж
    честно попросит)."""

    EX = {"model": "NMAX 155", "name": "Иван", "date_from": "2026-07-10",
          "date_to_datetime": "2026-07-15", "deposit": "3000", "helmets": "2",
          "contact": "@ivan"}
    # реальный паттерн приветствия: менеджер шлёт ссылки НАШИХ точек (БангТао/Камала)
    MGR_LINKS = ("[менеджер]: Наши точки: ⏎ https://maps.app.goo.gl/P4KsR1WT4dg6UTXb9 ⏎ "
                 "https://maps.app.goo.gl/bHHtLL1N7Uz5mX7N9")
    CLIENT_LINK = "https://maps.app.goo.gl/CLIENTxyz123"

    # ---- client_maps_link ----
    def test_maps_link_from_client_lines_only(self):
        tr = (self.MGR_LINKS + "\n[клиент]: мы живём тут " + self.CLIENT_LINK + " приезжайте\n"
              "[менеджер]: принято")
        self.assertEqual(booking_draft.client_maps_link(tr), self.CLIENT_LINK)

    def test_manager_links_alone_yield_none(self):
        self.assertIsNone(booking_draft.client_maps_link(self.MGR_LINKS))
        self.assertIsNone(booking_draft.client_maps_link("[клиент]: без ссылок"))
        self.assertIsNone(booking_draft.client_maps_link(""))

    def test_latest_client_link_wins_and_punct_stripped(self):
        tr = ("[клиент]: старый адрес https://maps.app.goo.gl/OLD1\n"
              "[клиент]: новый https://maps.app.goo.gl/NEW2, приезжайте")
        self.assertEqual(booking_draft.client_maps_link(tr), "https://maps.app.goo.gl/NEW2")

    # ---- build_intake: поле «Доставка:» ----
    def test_intake_delivery_gets_client_geo_with_note(self):
        ex = dict(self.EX, note="Патонг")
        tr = self.MGR_LINKS + "\n[клиент]: наш отель " + self.CLIENT_LINK
        txt = booking_draft.build_intake(ex, allowlist=ALLOW,
                                         meta={"client_ref": "@ivan", "transcript": tr})
        d = parse_intake(txt)
        self.assertEqual(d["Доставка"], "Патонг · " + self.CLIENT_LINK)
        self.assertNotIn("P4KsR1WT4dg6UTXb9", txt)     # гео проката НЕ утекает в заявку

    def test_intake_delivery_geo_without_note(self):
        ex = dict(self.EX)                              # note нет вовсе
        tr = "[клиент]: адрес вот " + self.CLIENT_LINK
        txt = booking_draft.build_intake(ex, allowlist=ALLOW,
                                         meta={"client_ref": "@ivan", "transcript": tr})
        self.assertEqual(parse_intake(txt)["Доставка"], self.CLIENT_LINK)

    def test_intake_no_link_regression(self):
        # (3) нет ссылки в диалоге → прежнее поведение байт-в-байт: note как было / поля нет.
        ex = dict(self.EX, note="Патонг")
        txt = booking_draft.build_intake(ex, allowlist=ALLOW,
                                         meta={"client_ref": "@ivan",
                                               "transcript": "[клиент]: просто текст"})
        self.assertEqual(parse_intake(txt)["Доставка"], "Патонг")
        ex2 = dict(self.EX)
        txt2 = booking_draft.build_intake(ex2, allowlist=ALLOW,
                                          meta={"client_ref": "@ivan",
                                                "transcript": "[клиент]: просто текст"})
        self.assertNotIn("Доставка:", txt2)             # страж честно попросит гео сам


class TestBridgePassportForward(unittest.TestCase):
    """(2) фото паспорта: client_id едет через intake-очередь; после поста карточки userbot
    пересылает последнее клиентское фото СРАЗУ ЗА ней; нет фото/нет client_id/сбой → прежнее
    поведение, статус posted не трогается."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save_db = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()

    def tearDown(self):
        moderation_ipc.DB_PATH = self._save_db
        self._tmp.cleanup()

    def _pending(self, client_id=None):
        iid = moderation_ipc.save_intake_candidate("🆕 БРОНЬ\nКлиент: X", client_id=client_id)
        moderation_ipc.confirm_intake(iid)
        return iid

    def test_client_id_persisted_through_queue(self):
        iid = self._pending(client_id=529849022)
        row = moderation_ipc.get_intake(iid)
        self.assertEqual(row["client_id"], 529849022)
        rows = moderation_ipc.fetch_pending_intake()
        self.assertEqual(rows[0]["client_id"], 529849022)

    def test_photo_forwarded_right_after_card(self):
        # ГЛАВНЫЙ сценарий: диалог с фото → пост карточки, затем СРАЗУ пересылка фото в ту же группу.
        iid = self._pending(client_id=111)
        order = []
        async def poster(client, gid, text):
            order.append(("post", gid)); return 777
        async def finder(client, cid):
            order.append(("find", cid)); return "PHOTO_MSG"
        async def forwarder(client, gid, msg):
            order.append(("fwd", gid, msg))
        n = asyncio.run(suggest.poll_and_post_intake(object(), poster=poster,
                                                     photo_finder=finder, forwarder=forwarder))
        self.assertEqual(n, 1)
        self.assertEqual(order, [("post", suggest.INBOX_GROUP_ID), ("find", 111),
                                 ("fwd", suggest.INBOX_GROUP_ID, "PHOTO_MSG")])  # фото СРАЗУ ЗА карточкой
        self.assertEqual(moderation_ipc.get_intake(iid)["status"], "posted")

    def test_no_photo_in_dialog_regression(self):
        # (3) фото в диалоге нет → пересылки нет, карточка как раньше (страж попросит фото).
        self._pending(client_id=222)
        fwd = []
        async def poster(client, gid, text): return 1
        async def finder(client, cid): return None
        async def forwarder(client, gid, msg): fwd.append(msg)
        n = asyncio.run(suggest.poll_and_post_intake(object(), poster=poster,
                                                     photo_finder=finder, forwarder=forwarder))
        self.assertEqual(n, 1)
        self.assertEqual(fwd, [])

    def test_no_client_id_regression(self):
        # старые записи без client_id (миграция) → finder вообще не зовётся, поведение прежнее.
        self._pending(client_id=None)
        calls = []
        async def poster(client, gid, text): return 1
        async def finder(client, cid): calls.append(cid); return "X"
        n = asyncio.run(suggest.poll_and_post_intake(object(), poster=poster, photo_finder=finder))
        self.assertEqual(n, 1)
        self.assertEqual(calls, [])

    def test_forward_failure_does_not_touch_posted(self):
        # сбой пересылки — best-effort: статус остаётся posted, исключение не всплывает.
        iid = self._pending(client_id=333)
        async def poster(client, gid, text): return 9
        async def finder(client, cid): return "PHOTO"
        async def forwarder(client, gid, msg): raise RuntimeError("flood wait")
        n = asyncio.run(suggest.poll_and_post_intake(object(), poster=poster,
                                                     photo_finder=finder, forwarder=forwarder))
        self.assertEqual(n, 1)
        row = moderation_ipc.get_intake(iid)
        self.assertEqual(row["status"], "posted")
        self.assertEqual(row["posted_msg_id"], 9)

    def test_old_db_migrates_client_id_column(self):
        # БД со СТАРОЙ схемой intake (без client_id) → init_db дособирает колонку (ALTER), не падает.
        import sqlite3
        old = os.path.join(self._tmp.name, "old.db")
        c = sqlite3.connect(old)
        c.execute("""CREATE TABLE intake (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT, status TEXT, created_ts TEXT, updated_ts TEXT,
            posted_msg_id INTEGER, reason TEXT)""")
        c.commit(); c.close()
        save = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = old
        try:
            moderation_ipc.init_db()
            iid = moderation_ipc.save_intake_candidate("🆕 БРОНЬ\nКлиент: Y", client_id=42)
            self.assertEqual(moderation_ipc.get_intake(iid)["client_id"], 42)
        finally:
            moderation_ipc.DB_PATH = save


class TestBridgeMissingGaps(unittest.TestCase):
    """O3-2.1 ЭТАП 2: (а) карточка ЧЕСТНО перечисляет «не хватает: точка / фото» из collected_facts —
    менеджер доносит ТОЛЬКО недостающее; (б) Telegram гео-ПИН (маркер [локация]), не только URL,
    даёт точку доставки. 4 кейса: точка есть/нет × фото есть/нет + гео-пин + регрессия без транскрипта.
    Фото помечаем ЧЕСТНО как неопределённость (живой скан диалога — отдельный источник)."""

    EX = {"model": "NMAX 155", "name": "Иван", "date_from": "2026-07-22",
          "date_to_datetime": "2026-07-27", "deposit": "3000", "helmets": "2", "contact": "@ivan"}
    CLINK = "https://maps.app.goo.gl/CLIENThotel7"
    MGR_PIN = "[менеджер]: наша точка [локация]"     # пин менеджера НЕ считается точкой клиента

    def _card(self, tr):
        return booking_draft.build_intake(self.EX, allowlist=ALLOW,
                                          meta={"client_ref": "@ivan", "transcript": tr})

    # --- кейс 1: точка (URL) ЕСТЬ + фото ЕСТЬ → «не хватает» строки нет вовсе ---
    def test_both_present_no_gaps(self):
        tr = "[клиент]: мы тут " + self.CLINK + "\n[клиент]: [фото]"
        txt = self._card(tr)
        self.assertNotIn("Не хватает", txt)
        self.assertEqual(parse_intake(txt)["Доставка"], self.CLINK)

    # --- кейс 2 (+б): точка = ГЕО-ПИН + фото НЕТ → Доставка из пина, в недостающем только фото ---
    def test_geo_pin_present_photo_missing(self):
        tr = "[клиент]: вот наш адрес [локация]"
        txt = self._card(tr)
        self.assertEqual(parse_intake(txt)["Доставка"], "точка на карте (пин в диалоге)")
        self.assertIn("Не хватает", txt)
        self.assertNotIn("точка (гео/Maps)", txt)   # точка ЕСТЬ (пин) → её нет в недостающем
        self.assertIn("фото паспорта", txt)          # фото — недостающее (мягко, с оговоркой)

    # --- кейс 3: точки НЕТ + фото ЕСТЬ → в недостающем только точка ---
    def test_geo_missing_photo_present(self):
        tr = "[клиент]: привет, хочу байк\n[клиент]: [фото]"
        txt = self._card(tr)
        self.assertIn("Не хватает: точка (гео/Maps)", txt)
        self.assertNotIn("фото паспорта", txt)       # фото собрано → не в недостающем
        self.assertNotIn("Доставка:", txt)           # ни note, ни ссылки, ни пина

    # --- кейс 4: обеих НЕТ → карточка честно перечисляет и точку, и фото ---
    def test_both_missing_lists_both(self):
        tr = "[клиент]: просто хочу арендовать"
        txt = self._card(tr)
        self.assertTrue(txt.startswith("🆕 БРОНЬ"))
        self.assertIn("точка (гео/Maps)", txt)
        self.assertIn("фото паспорта", txt)

    # --- пин МЕНЕДЖЕРА не считается точкой клиента (как и ссылки менеджера) ---
    def test_manager_pin_not_client_point(self):
        tr = self.MGR_PIN + "\n[клиент]: хочу байк"
        txt = self._card(tr)
        self.assertNotIn("Доставка:", txt)
        self.assertIn("Не хватает: точка (гео/Maps)", txt)

    # --- регрессия: транскрипта нет (старый вызов) → хвоста «не хватает» нет, байт-в-байт ---
    def test_no_transcript_no_gap_tail(self):
        txt = booking_draft.build_intake(self.EX, allowlist=ALLOW, meta={"client_ref": "@ivan"})
        self.assertNotIn("Не хватает", txt)

    # --- тайский НЕ появляется в хвосте (класс регрессий: автосбор/шаблоны не тянут тайский) ---
    def test_no_thai_in_gaps(self):
        tr = "[клиент]: просто хочу арендовать"
        txt = self._card(tr)
        self.assertFalse(any("฀" <= ch <= "๿" for ch in txt))


if __name__ == "__main__":
    unittest.main()
