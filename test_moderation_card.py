# -*- coding: utf-8 -*-
"""
test_moderation_card.py — замки КАРТОЧКИ МОДЕРАЦИИ (вопрос клиента → вариант ответа бота →
отправка ПО НАЖАТИЮ ЧЕЛОВЕКА). Разморозка клиентского контура 19.08.2026: отправка разрешена
ТОЛЬКО с подтверждением человека, автоматическая остаётся замороженной.

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА, названные владельцем (без них правка не годится):
  (а) карточка есть, НИКТО не нажал → клиенту не ушло НИЧЕГО     — TestNobodyPressed;
  (б) двое (здесь восемь) нажали ОДНОВРЕМЕННО → ушло РОВНО одно  — TestFirstResponderWins;
  (в) бот посчитать не может → в карточке «не считаю», а не число — TestCannotCompute.

Живой формат, а не идеализированный: `pricing_note` во всех тестах «почему» собирает НАСТОЯЩИЙ
`suggest.build_pricing_note` на фейковом Bridge, формат ответа которого скопирован с фикстуры
боевых тестов (`test_suggest.TestPricingV2._getter`) — сочинённых от руки лент здесь нет.
Отправку меряем не своим счётчиком, а ЖИВЫМ отправителем `suggest.poll_and_send` (единственная
точка отправки клиенту в bot-режиме).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_moderation_card -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import ast
import asyncio
import datetime
import io
import os
import tempfile
import threading
import unittest

import moderation_card as mc
import moderation_ipc
import suggest

REPO = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date(2026, 8, 20)


class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    """Телеграм-клиент userbot без Телеграма: всё, что «ушло клиенту», оседает в self.sent."""

    def __init__(self):
        self.sent = []

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text, reply_to=None):
        self.sent.append((chat, text))


async def _nosleep(_):
    return None


# ----------------------------- живой Bridge-фейк ------------------------------
# Формат ответа — ДОСЛОВНО как в test_suggest.TestPricingV2 (там он снят с прода): позиционные
# поля day_price/total/deposit/available/days/cap_active/cap_price/text. Правило-класс «мок
# обязан копировать живой формат» — иначе разбор «почему» проверялся бы на выдуманной ленте.
TAR = {
    "NMAX 155": (450, 2800, 9000, 5000, True, 8500),
    "ADV 350": (749, 4928, 14606, 7000, True, 10900),
    "CB 300R": (757, 4716, 12491, 15000, True, 9900),
    "XMAX 300": (700, 4200, 13000, 7000, False, 20000),
}
FLEET_NAMES = ["NMAX 155CC BLACK PHUKET 4255", "ADV 350CC BLACK PHUKET 5849",
               "CB 300CC R 9011", "XMAX 300CC GREY PHUKET 4246",
               "R7 900CC PHUKET 7777"]     # R7 в парке ЕСТЬ, а цены на него у Календаря НЕТ


def _getter(params):
    if params.get("action") == "fleet":
        return {"ok": True, "data": {"bikes": [{"name": n} for n in FLEET_NAMES]}}
    bike = params.get("bike", "")
    ds, de = params.get("date_start"), params.get("date_end")
    days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
    bk = suggest._bike_key(bike)
    key = next((k for k in TAR if suggest._bike_key(k) in bk), None)
    if key is None:
        return {"ok": False}
    d1, d7, d30, dep, ca, cp = TAR[key]
    total = {1: d1, 7: d7, 30: d30}.get(days, d1 * days)
    return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
            "deposit": dep, "available": True, "days": days, "cap_active": ca,
            "cap_price": cp, "text": f"{bike} — {total} ฿ за {days} дней; депозит {dep} ฿"}}


class LiveNoteBase(unittest.TestCase):
    """Общая обвязка: живой ценовой путь на фейковом Bridge + своя временная очередь."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (moderation_ipc.DB_PATH, suggest.pricing.PRICING_ACTION,
                      suggest.pricing.BRIDGE_URL, suggest.pricing.BRIDGE_TOKEN,
                      suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
                      suggest.APPROVER_USERNAMES)
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.APPROVER_USERNAMES = set()      # пустой whitelist = решать может любой
        suggest.reset_disabled()

    def tearDown(self):
        (moderation_ipc.DB_PATH, suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN, suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
         suggest.APPROVER_USERNAMES) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        suggest.reset_disabled()
        self._tmp.cleanup()

    def note_and_hints(self, transcript):
        """Живой разбор диалога + живая лента цены (тот же путь, что у userbot)."""
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        h = suggest.extract_booking_hints(transcript, today=TODAY)
        note = suggest.build_pricing_note(h, lang="ru", getter=_getter, today=TODAY)
        return note, h

    def row(self, transcript, draft="Здравствуйте! Вот что получается.", ref="@testclient"):
        """Строка очереди РОВНО тех полей, что кладёт боевой userbot (suggest.on_client_message)."""
        note, h = self.note_and_hints(transcript)
        incoming = transcript.split("[клиент]:")[-1].strip()
        did = moderation_ipc.enqueue_draft({
            "client_id": 424242, "client_ref": ref, "lang": "ru", "incoming": incoming,
            "draft": draft, "first_contact": True, "transcript": transcript,
            "pricing_note": note, "client_name": "ТЕСТ"})
        moderation_ipc.mark_posted(did, 5000 + did)
        return moderation_ipc.get(did), h

    def sent_by_live_executor(self):
        """Сколько сообщений УШЛО БЫ клиенту прямо сейчас — живым отправителем userbot."""
        client = FakeClient()
        got = []

        async def fake_send(_client, cid, text, sleep=None, jitter=None):
            got.append((cid, text))
            return True, None

        asyncio.run(suggest.poll_and_send(client, sender=fake_send, sleep=_nosleep,
                                          jitter=lambda: 0))
        return got


# ============================ 1. «ПОЧЕМУ» В КАРТОЧКЕ ==========================

class TestWhyBlock(LiveNoteBase):
    """Без «почему» карточка бесполезна: человек не сможет понять, где бот ошибся."""

    QUOTE_TR = "[клиент]: сколько стоит nmax с 1 по 6 сентября?"

    def test_card_shows_question_answer_and_why(self):
        row, h = self.row(self.QUOTE_TR, draft="NMAX свободен, цена ниже.")
        text = mc.render(row, hints=h)
        self.assertIn("сколько стоит nmax с 1 по 6 сентября?", text)   # что написал клиент
        self.assertIn("NMAX свободен, цена ниже.", text)               # что предлагает бот
        self.assertIn("🧾 ПОЧЕМУ ИМЕННО ТАК:", text)                   # и почему именно так
        self.assertIn("@testclient", text)
        self.assertIn("первый контакт", text)

    def test_why_carries_price_verbatim_and_its_source(self):
        row, h = self.row(self.QUOTE_TR)
        card = mc.build(row, hints=h)
        self.assertEqual(card["facts"]["kind"], mc.KIND_QUOTE)
        line = card["facts"]["line"]
        self.assertIn("฿", line)
        self.assertIn("2250 ฿ за 5 дней", line)          # число из Календаря, а не из воздуха
        self.assertIn("• цена: " + line, card["text"])    # в карточке — ДОСЛОВНО
        self.assertIn("Календарь бронирования", card["text"])

    def test_why_names_season_and_term(self):
        row, h = self.row(self.QUOTE_TR)
        text = mc.render(row, hints=h)
        self.assertIn("• сезон: P1 ИЮНЬ-СЕНТЯБРЬ", text)         # какой сезон взят
        self.assertIn("границу не пересекает", text)
        self.assertIn("• срок: 2026-09-01 → 2026-09-06 (5 сут)", text)

    def test_why_names_what_was_missing(self):
        # Клиент не назвал даты — «чего боту не хватило» обязано это назвать словами человека.
        row, h = self.row("[клиент]: сколько стоит аренда nmax?")
        card = mc.build(row, hints=h)
        codes = [g[0] for g in card["facts"]["gaps"]]
        self.assertIn(mc.GAP_NO_DATES, codes)
        self.assertIn("клиент не назвал даты аренды", card["text"])

    def test_why_carries_season_mark_of_price_sheet(self):
        # Прайс по парку несёт СЛУЖЕБНУЮ пометку сезона — она тоже часть «почему».
        tr = ("[клиент]: Какие марки и модели байков вы предлагаете? "
              "Какие у вас цены на аренду? Требуется ли депозит?")
        row, h = self.row(tr)
        card = mc.build(row, hints=h)
        self.assertEqual(card["facts"]["kind"], mc.KIND_SHEET)
        self.assertIn("сезон: низкий", card["facts"]["season"])
        self.assertIn("пометка сезона из ленты:", card["text"])

    def test_empty_draft_still_becomes_a_card(self):
        # Карточки рождаются на ВСЕ сообщения клиентов: пустой черновик — не повод промолчать.
        row, h = self.row(self.QUOTE_TR, draft="")
        text = mc.render(row, hints=h)
        self.assertIn("бот ответа не собрал", text)
        self.assertIn("🧾 ПОЧЕМУ ИМЕННО ТАК:", text)
        self.assertIn("сколько стоит nmax", text)


# ==================== 2. ОТРИЦАТЕЛЬНЫЙ ТЕСТ (в): «НЕ СЧИТАЮ» ==================

class TestCannotCompute(LiveNoteBase):
    """(в) Бот посчитать не может → в карточке «не считаю», а НЕ число.

    Три класса названы владельцем: период через границу сезонов, срок от 30 суток, модель без
    цены. Каждый — ОТДЕЛЬНОЙ строкой карточки."""

    def test_season_boundary_crossing_is_declared(self):
        # 10→20 декабря: P4 «ДО 20 ДЕКАБРЯ» (12-01..12-14) → P5 «ПИК» (12-15..02-05).
        row, h = self.row("[клиент]: хочу nmax с 10 по 20 декабря, сколько выйдет?")
        card = mc.build(row, hints=h)
        codes = [c["code"] for c in card["cannot"]]
        self.assertIn(mc.CANNOT_SEASON, codes)
        self.assertIn("⛔ НЕ СЧИТАЮ:", card["text"])
        self.assertIn("пересекает границу сезонов", card["text"])
        self.assertIn("P4 ДО 20 ДЕКАБРЯ → P5 ПИК", card["text"])
        self.assertIn("• цена: не считаю", card["text"])       # число НЕ выдано за итог

    def test_term_from_30_days_is_declared(self):
        row, h = self.row("[клиент]: нужен nmax с 1 сентября по 16 октября, сколько?")
        card = mc.build(row, hints=h)
        self.assertEqual(h["hint_days"], 45)
        codes = [c["code"] for c in card["cannot"]]
        self.assertIn(mc.CANNOT_LONG, codes)
        self.assertIn("срок 45 сут — от 30 суток тариф месячный", card["text"])
        self.assertIn("• цена: не считаю", card["text"])

    def test_model_without_price_shows_no_number_at_all(self):
        # ГЛАВНЫЙ замок (в): R7 в парке есть, цены у Календаря нет → в «почему» НИ ОДНОЙ цифры ฿.
        row, h = self.row("[клиент]: сколько стоит R7 с 1 по 6 сентября?")
        card = mc.build(row, hints=h)
        self.assertEqual(card["facts"]["kind"], mc.KIND_NONE)
        self.assertIsNone(card["facts"]["line"])
        codes = [c["code"] for c in card["cannot"]]
        self.assertEqual(codes, [mc.CANNOT_NO_PRICE])
        self.assertIn("• цена: не считаю", card["text"])
        self.assertIn("⛔ НЕ СЧИТАЮ: цены нет", card["text"])
        why = card["text"].split("🧾 ПОЧЕМУ ИМЕННО ТАК:")[1]
        self.assertNotIn("฿", why)                 # выдуманного числа нет нигде в «почему»

    def test_monthly_request_without_day_count_is_declared(self):
        facts = mc.price_facts("")
        cannot = mc.cannot_compute(facts, {"monthly": True})
        self.assertIn(mc.CANNOT_LONG, [c["code"] for c in cannot])
        self.assertIn("запрошен месяц", " ".join(c["line"] for c in cannot))

    def test_computable_case_declares_nothing(self):
        # Обратная сторона: когда посчитать МОЖНО — «не считаю» в карточке не появляется.
        row, h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?")
        card = mc.build(row, hints=h)
        self.assertEqual(card["cannot"], ())
        self.assertNotIn("⛔ НЕ СЧИТАЮ", card["text"])
        self.assertNotIn("• цена: не считаю", card["text"])

    def test_unknown_season_is_third_outcome_not_green(self):
        # Таблицы периодов нет → «не знаю», и это НЕ повод объявить, что границы нет.
        verdict, names, detail = mc.season_span("2026-12-10", "2026-12-20", doc=None)
        self.assertEqual(verdict, mc.SEASON_CROSSES)          # с живым файлом — пересечение
        self.assertIsNotNone(names)
        empty = {"schema": "turbobaby/price_source", "season": {"periods": []}}
        v2, n2, d2 = mc.season_span("2026-12-10", "2026-12-20", doc=empty)
        self.assertEqual(v2, mc.SEASON_UNKNOWN)
        self.assertIsNone(n2)
        self.assertIn("вне периодов", d2)
        self.assertEqual(mc.cannot_compute(mc.price_facts(""), {"iso_start": "2026-12-10",
                                                                "iso_end": "2026-12-20"},
                                           doc=empty)[0]["code"], mc.CANNOT_NO_PRICE)
        self.assertNotIn(mc.CANNOT_SEASON,
                         [c["code"] for c in mc.cannot_compute(
                             mc.price_facts(""), {"iso_start": "2026-12-10",
                                                  "iso_end": "2026-12-20"}, doc=empty)])

    def test_no_hints_at_all_is_not_silently_green(self):
        # Разбора не было вовсе (hints=None) → сезон «не знаю», а не «границу не пересекает».
        line = " ".join(mc._why_lines(mc.price_facts(""), (), None))
        self.assertIn("• сезон: не знаю", line)
        self.assertIn("• срок: дат в диалоге нет", line)


# ============ 3. ОТРИЦАТЕЛЬНЫЙ ТЕСТ (а): НИКТО НЕ НАЖАЛ — НИЧЕГО НЕ УШЛО ======

class TestNobodyPressed(LiveNoteBase):
    """(а) Карточка есть, никто не нажал → клиенту НЕ ушло ничего. Меряем живым отправителем."""

    def test_card_alone_sends_nothing(self):
        row, h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?")
        text = mc.render(row, hints=h)                 # карточка построена и «висит»
        self.assertIn("🗂 КАРТОЧКА МОДЕРАЦИИ", text)
        self.assertEqual(moderation_ipc.fetch_ready(), [])       # к отправке — ничего
        self.assertEqual(self.sent_by_live_executor(), [])       # живой отправитель молчит
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "posted")

    def test_many_cards_wait_forever_without_a_press(self):
        for i in range(5):
            self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?", ref=f"@c{i}")
        self.assertEqual(self.sent_by_live_executor(), [])
        self.assertEqual(len(moderation_ipc.fetch_new()), 0)     # все запощены
        self.assertEqual(moderation_ipc.fetch_ready(), [])       # и ни одна не решена

    def test_ageing_card_never_self_sends(self):
        # Предела ожидания НЕТ по решению владельца: карточка висит, пока не ответят.
        # Древняя карточка не «дозревает» до отправки ни сама, ни от повторного опроса.
        row, _h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?")
        with moderation_ipc._conn() as c:
            c.execute("UPDATE drafts SET created_ts=?, updated_ts=? WHERE id=?",
                      ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", row["id"]))
        for _ in range(3):
            self.assertEqual(self.sent_by_live_executor(), [])
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "posted")

    def test_old_card_is_still_decidable_by_a_human(self):
        # Обратная сторона «предела нет»: спустя годы нажатие всё ещё работает.
        row, _h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?")
        with moderation_ipc._conn() as c:
            c.execute("UPDATE drafts SET created_ts=? WHERE id=?",
                      ("2020-01-01T00:00:00+00:00", row["id"]))
        dec = mc.decide(moderation_ipc.get(row["id"]), mc.ACT_SEND, "danya")
        self.assertEqual(dec["decision"], "ready")
        self.assertEqual(len(self.sent_by_live_executor()), 1)

    def test_test_mode_holds_even_after_a_press(self):
        # Второй замок цел: в TEST_MODE нажатие даёт test_held, а не ready.
        suggest.SUGGEST_TEST_MODE = True
        row, _h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?")
        dec = mc.decide(row, mc.ACT_SEND, "danya", test_mode=True)
        self.assertEqual(dec["decision"], "test_held")
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_reject_sends_nothing(self):
        row, _h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?")
        dec = mc.decide(row, mc.ACT_REJECT, "danya")
        self.assertEqual(dec["decision"], "rejected")
        self.assertIsNone(dec["final_text"])
        self.assertEqual(self.sent_by_live_executor(), [])
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "rejected")


# ====== 4. ОТРИЦАТЕЛЬНЫЙ ТЕСТ (б): ДВОЕ НАЖАЛИ — УШЛО РОВНО ОДНО ==============

class TestFirstResponderWins(LiveNoteBase):
    """(б) Карточка закрывается за ПЕРВЫМ ответившим. Двух сообщений одному клиенту не бывает
    ни при какой гонке. Замок — предусловие по статусу внутри одного UPDATE (СУБД, а не питон)."""

    def _card(self):
        row, _h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?")
        return row

    def test_second_press_loses_and_sees_the_winner(self):
        row = self._card()
        first = mc.decide(row, mc.ACT_SEND, "danya")
        second = mc.decide(row, mc.ACT_SEND, "dasha")
        self.assertEqual(first["decision"], "ready")
        self.assertEqual(second["decision"], "closed")
        self.assertIn("уже закрыта", second["card"])
        self.assertIn("@danya", second["card"])           # видно, КЕМ закрыта
        self.assertIn("Второе сообщение клиенту не уйдёт", second["card"])
        self.assertEqual(len(self.sent_by_live_executor()), 1)

    def test_press_after_delivery_is_refused(self):
        # Дорога, которой второе сообщение уходило ЖИВЬЁМ: карточка остаётся с кнопками и после
        # отправки, а у set_decision предусловия по статусу нет вовсе — повторный тап снова
        # ставил 'ready', и отправитель (fetch_ready → send → mark, без дедупа) слал ВТОРОЕ.
        row = self._card()
        mc.decide(row, mc.ACT_SEND, "danya")
        self.assertEqual(len(self.sent_by_live_executor()), 1)
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "sent")
        again = mc.decide(moderation_ipc.get(row["id"]), mc.ACT_SEND, "danya")
        self.assertEqual(again["decision"], "closed")
        self.assertIn("уже отправлено клиенту", again["card"])
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(self.sent_by_live_executor(), [])     # второго сообщения нет

    def test_reject_after_send_is_refused(self):
        row = self._card()
        mc.decide(row, mc.ACT_SEND, "danya")
        late = mc.decide(moderation_ipc.get(row["id"]), mc.ACT_REJECT, "dasha")
        self.assertEqual(late["decision"], "closed")
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "ready")

    def test_send_after_reject_is_refused(self):
        row = self._card()
        mc.decide(row, mc.ACT_REJECT, "dasha")
        late = mc.decide(moderation_ipc.get(row["id"]), mc.ACT_SEND, "danya")
        self.assertEqual(late["decision"], "closed")
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_eight_simultaneous_presses_send_exactly_one(self):
        """НАСТОЯЩАЯ гонка: восемь потоков, синхронный старт по барьеру, одна строка очереди.
        Победитель обязан быть ровно один — и клиенту уходит ровно одно сообщение."""
        row = self._card()
        n = 8
        barrier = threading.Barrier(n)
        results, errors = [], []
        lock = threading.Lock()

        def press(who):
            barrier.wait()
            try:
                dec = mc.decide(row, mc.ACT_SEND, who)
            except Exception as e:               # noqa: BLE001 — гонка обязана быть БЕЗ сбоев
                with lock:
                    errors.append(f"{type(e).__name__}: {e}")
                return
            with lock:
                results.append(dec["decision"])

        threads = [threading.Thread(target=press, args=(f"mod{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [], "гонка дала сбой вместо честного отказа")
        self.assertEqual(len(results), n)
        self.assertEqual(results.count("ready"), 1, results)          # победитель РОВНО один
        self.assertEqual(results.count("closed"), n - 1, results)     # остальные — «уже закрыто»
        ready = moderation_ipc.fetch_ready()
        self.assertEqual(len(ready), 1)
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1, sent)                          # клиенту РОВНО одно
        self.assertEqual(sent[0][0], 424242)

    def test_claim_is_atomic_at_the_queue_layer(self):
        # Тот же замок на уровне очереди, без карточки: захват берёт РОВНО один.
        row = self._card()
        won1, _r1 = moderation_ipc.claim_decision(row["id"], "ready", final_text="A",
                                                  decided_by="@a")
        won2, r2 = moderation_ipc.claim_decision(row["id"], "ready", final_text="B",
                                                 decided_by="@b")
        self.assertTrue(won1)
        self.assertFalse(won2)
        self.assertEqual(r2["final_text"], "A")        # текст победителя не перезаписан
        self.assertEqual(r2["decided_by"], "@a")

    def test_claim_on_missing_row_is_honest(self):
        won, row = moderation_ipc.claim_decision(999999, "ready", final_text="X")
        self.assertFalse(won)
        self.assertIsNone(row)
        self.assertIn("строки очереди больше нет", mc.render_closed(None))


# ============================ 5. ТРИ ДЕЙСТВИЯ ЧЕЛОВЕКА ========================

class TestThreeActions(LiveNoteBase):
    """Ровно три действия: отправить как есть; поправить текст и отправить; отклонить."""

    def _card(self):
        row, _h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?",
                           draft="Черновик бота")
        return row

    def test_exactly_three_actions_offered(self):
        codes = [c for c, _label in mc.actions()]
        self.assertEqual(codes, [mc.ACT_SEND, mc.ACT_EDIT, mc.ACT_REJECT])
        text = mc.render(self._card())
        for _code, label in mc.actions():
            self.assertIn(label, text)

    def test_send_as_is_takes_the_draft(self):
        row = self._card()
        dec = mc.decide(row, mc.ACT_SEND, "danya")
        self.assertEqual(dec["decision"], "ready")
        self.assertEqual(dec["final_text"], "Черновик бота")
        self.assertEqual(self.sent_by_live_executor(), [(424242, "Черновик бота")])

    def test_edit_and_send_takes_the_human_text(self):
        row = self._card()
        dec = mc.decide(row, mc.ACT_EDIT, "danya", text="Поправленный человеком ответ")
        self.assertEqual(dec["decision"], "ready")
        self.assertEqual(self.sent_by_live_executor(),
                         [(424242, "Поправленный человеком ответ")])

    def test_edit_without_text_changes_nothing(self):
        row = self._card()
        dec = mc.decide(row, mc.ACT_EDIT, "danya", text="   ")
        self.assertEqual(dec["decision"], "empty")
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "posted")   # карточка открыта
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_send_of_empty_draft_changes_nothing(self):
        row, _h = self.row("[клиент]: сколько стоит nmax с 1 по 6 сентября?", draft="")
        dec = mc.decide(row, mc.ACT_SEND, "danya")
        self.assertEqual(dec["decision"], "empty")
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_unknown_action_changes_nothing(self):
        row = self._card()
        dec = mc.decide(row, "нажал_лапой", "danya")
        self.assertEqual(dec["decision"], "noop")
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "posted")
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_non_approver_cannot_send_and_card_stays_open(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        row = self._card()
        dec = mc.decide(row, mc.ACT_SEND, "stranger")
        self.assertEqual(dec["decision"], "denied")
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "posted")
        self.assertEqual(self.sent_by_live_executor(), [])
        ok = mc.decide(moderation_ipc.get(row["id"]), mc.ACT_SEND, "danya")
        self.assertEqual(ok["decision"], "ready")          # карточка осталась рабочей

    def test_edit_prefers_candidate_over_stale_draft(self):
        row = self._card()
        moderation_ipc.set_candidate(row["id"], "Кандидат после правки", directive="короче")
        dec = mc.decide(moderation_ipc.get(row["id"]), mc.ACT_SEND, "danya")
        self.assertEqual(dec["final_text"], "Кандидат после правки")


# ================== 6. ИНВАРИАНТЫ МОДУЛЯ (по исходнику, не по вере) ===========

def _identifiers(path):
    """Все ИМЕНА исходника (переменные, поля, функции, аргументы, импорты). Проза докстрингов
    сюда не попадает СОЗНАТЕЛЬНО: замок обязан ловить ручку в коде, а не слово в комментарии."""
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            out.add(node.arg)
        elif isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            out.add(node.module.split(".")[0])
    return out


class TestModuleInvariants(unittest.TestCase):
    """Замки, которые обязаны держаться ПО ПОСТРОЕНИЮ модуля, а не по обещанию в докстринге."""

    PATH = os.path.join(REPO, "moderation_card.py")

    def test_module_cannot_send_anything(self):
        # Автоотправки нет ни при каких условиях: отправлять модулю попросту НЕЧЕМ.
        names = _identifiers(self.PATH)
        for forbidden in ("send_message", "send_to_client", "poll_and_send", "telegram",
                          "telethon", "requests", "urllib"):
            self.assertNotIn(forbidden, names, f"в карточке появился канал отправки: {forbidden}")

    def test_no_deadline_knob_exists(self):
        # Предела ожидания НЕТ по решению владельца: карточка висит, пока не ответят.
        names = _identifiers(self.PATH)
        for forbidden in ("timeout", "ttl", "TTL", "deadline", "expire", "expires", "expiry"):
            self.assertNotIn(forbidden, names, f"появился предел ожидания: {forbidden}")

    def test_no_confidence_knob_exists(self):
        # Карточки рождаются на ВСЕ сообщения: самостоятельности «по уверенности» нет.
        names = _identifiers(self.PATH)
        for forbidden in ("confidence", "threshold", "auto_send", "autosend", "auto_approve"):
            self.assertNotIn(forbidden, names, f"появилась ручка уверенности: {forbidden}")

    def test_decide_is_the_only_way_to_ready(self):
        # Единственный вызов захвата — внутри decide (то есть из явного действия человека).
        with io.open(self.PATH, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=self.PATH)
        holders = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Attribute) and sub.attr == "claim_decision":
                        holders.append(node.name)
        self.assertEqual(holders, ["decide"], holders)


class TestContractWithSuggest(unittest.TestCase):
    """«Почему» стоит на ДОСЛОВНЫХ фрагментах живых инструкций suggest. Перепишут инструкцию —
    этот тест покраснеет ГРОМКО, а карточка не начнёт молча терять основания."""

    def setUp(self):
        with io.open(os.path.join(REPO, "suggest.py"), encoding="utf-8") as f:
            self.src = f.read()

    def test_every_gap_fragment_is_alive_in_suggest(self):
        for code, fragment, human, _kills in mc.GAPS:
            self.assertIn(fragment, self.src, f"фрагмент класса {code} исчез из suggest: {fragment}")
            self.assertTrue(len(human) > 0)

    def test_gap_codes_are_unique(self):
        codes = [g[0] for g in mc.GAPS]
        self.assertEqual(len(codes), len(set(codes)))

    def test_block_extractors_are_borrowed_not_copied(self):
        # Своих копий регулярок в карточке нет — берём ЖИВЫЕ у suggest.
        for name in ("_quote_block_from_note", "_sheet_block_from_note",
                     "_delivery_block_from_note", "_SEASON_BLOCK_RE", "_DELIVERY_ASK_BLOCK_RE"):
            self.assertTrue(hasattr(suggest, name), f"suggest потерял {name}")
        names = _identifiers(os.path.join(REPO, "moderation_card.py"))
        self.assertNotIn("compile", names, "в карточке завелась своя регулярка вместо живой")

    def test_long_term_threshold_is_the_live_one(self):
        self.assertEqual(mc.LONG_TERM_DAYS, suggest._CAP_MIN_DAYS)
        self.assertEqual(mc.LONG_TERM_DAYS, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
