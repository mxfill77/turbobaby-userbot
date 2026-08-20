# -*- coding: utf-8 -*-
"""
test_dialog_card.py — замки КАРТОЧКИ НА РАЗГОВОР (единица — диалог, а не сообщение).

ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА, названные владельцем 20.08.2026 (без них правка не годится):
  (а) карточка есть, НИКТО не нажал      → клиенту не ушло НИЧЕГО      — TestNobodyPressed;
  (б) клиент ДОПИСАЛ, пока карточка ждала → устаревшее отправить НЕЛЬЗЯ — TestStaleProposal;
  (в) двое (здесь восемь) нажали разом    → ушло РОВНО одно            — TestFirstResponderWins;
  (г) бот посчитать не может              → «не считаю», а не число    — TestCannotCompute.

Случай (б) — ГЛАВНЫЙ НОВЫЙ, ради него сменилась единица, и проверяется он отдельным классом на
шести дорогах: одна дописка, три подряд, устаревший СНИМОК строки в руках у нажимающего, гонка
«нажали ровно тогда, когда клиент дописал», контрфакт неверного порядка и попытка воскресить
устаревшую строку прямо на слое очереди.

ЖИВОЙ ФОРМАТ, А НЕ ИДЕАЛИЗИРОВАННЫЙ. Ценовая лента во всех тестах — НАСТОЯЩИЙ
`suggest.build_pricing_note` на том же фейковом Bridge, что и у карточки сообщения (фикстура
одна на два файла, СОЗНАТЕЛЬНО импортирована, а не скопирована: две копии живого формата
разъезжаются). Транскрипт собран ровно так, как его собирает `suggest.transcript_from` —
одна строка на сообщение, перенос внутри реплики заменён на ' ⏎ '. Отправку меряем не своим
счётчиком, а ЖИВЫМ отправителем `suggest.poll_and_send`.

ГОЛДЕНЫ ДЕТЕКТА ТЕМ — ДОСЛОВНЫЕ ПЕРВЫЕ ХОДЫ КЛИЕНТОВ из обезличенного корпуса (710 диалогов,
`client_chats.anonstable.jsonl`), а не сочинённые формулировки: правило-класс репозитория.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_dialog_card -v
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

import card_load
import dialog_card as dc
import moderation_card as mc
import moderation_ipc
import suggest

# Фикстура живого Bridge — ОДНА на два файла тестов карточки (см. докстринг).
from test_moderation_card import FakeClient, _getter, _nosleep

REPO = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date(2026, 8, 20)
CLIENT_ID = 710710          # 710 — число диалогов замера, чтобы id опознавался в выводе

# ---- ДОСЛОВНЫЕ многотемные первые ходы клиентов (обезличенный корпус, 39 таких из 398) ----
# Ники/имена/адреса в корпусе уже заменены плейсхолдерами при обезличивании — здесь они как есть.
OPEN_5_TOPICS = ("Доброго вечера! \nХотели у вас забронировать байк на двоих (для девушек) и два "
                 "шлема на 21 октября в 17.00\nОтель <адрес_1> мне нужно вам для этого предоставить?)")
OPEN_4_DELIVERY = ("Здравствуйте \nЛицо_1 Ваш контакт \nИнтересует байк в ноябре с 14 по 21, байк "
                   "на 2х человек(общий вес +-160) с 2мя шлемами, сориентируйте по цене и какие "
                   "модели подойдут нам!\nБайк привозите к отелю или нужно забирать?")
OPEN_4_PRICE = ("Добрый вечер, подскажите, пожалуйста, по ценам и наличию байка в аренду?\n\n"
                "С 6 января по 13 января, район найхарн")
OPEN_4_DEPOSIT = "Добрый.  Найдете Adv 350 с 6.01 по 21.01? Стоимость аренды и депозит какой?"
OPEN_PRICE_ONLY = "Здравствуйте, подскажите стоимость аренды Honda pcx160 на 19 дней?"
OPEN_GREETING = "Здравствуйте"


class DialogBase(unittest.TestCase):
    """Обвязка РАЗГОВОРА: живой ценовой путь на фейковом Bridge, своя временная очередь,
    накопительный транскрипт формата `suggest.transcript_from`."""

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
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.APPROVER_USERNAMES = set()      # пустой whitelist = решать может любой
        suggest.reset_disabled()
        self.history = []
        # Замер нагрузки — в свой временный файл: боевую копилку тесты не трогают.
        self.meter_path = os.path.join(self._tmp.name, "card_load.jsonl")
        self._queue_n = 0
        card_load.reset_errors()

    def fresh_queue(self):
        """Чистая очередь и чистый транскрипт БЕЗ пересборки временного каталога: повторный
        setUp внутри теста оставлял бы за собой неубранные TemporaryDirectory."""
        self._queue_n += 1
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc%d.db" % self._queue_n)
        moderation_ipc.init_db()
        self.history = []

    def tearDown(self):
        (moderation_ipc.DB_PATH, suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN, suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
         suggest.APPROVER_USERNAMES) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        suggest.reset_disabled()
        card_load.reset_errors()
        self._tmp.cleanup()

    # ------------------------------ живой ценовой путь ------------------------
    def _note(self, transcript):
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        h = suggest.extract_booking_hints(transcript, today=TODAY)
        note = suggest.build_pricing_note(h, lang="ru", getter=_getter, today=TODAY)
        return note, h

    # ------------------------------ ход разговора -----------------------------
    def client_says(self, text, draft="Здравствуйте! Вот что получается по вашему запросу.",
                    client_id=CLIENT_ID):
        """Клиент написал → userbot ПРИНЯЛ сообщение в разговор (гашение + постановка) и
        модербот запостил/обновил карточку. Возвращает (строка очереди, hints, итог приёма)."""
        self.history.append("[клиент]: " + text.replace("\n", " ⏎ "))
        transcript = "\n".join(self.history)
        note, h = self._note(transcript)
        res = dc.accept_client_message({
            "client_id": client_id, "client_ref": "@dlgclient", "lang": "ru",
            "incoming": text, "draft": draft, "first_contact": len(self.history) == 1,
            "transcript": transcript, "pricing_note": note, "client_name": "ТЕСТ"},
            ipc=moderation_ipc)
        moderation_ipc.mark_posted(res["draft_id"], 7000 + res["draft_id"])
        return moderation_ipc.get(res["draft_id"]), h, res

    def we_say(self, text):
        """Наш ход в диалоге (ответ бота ушёл ИЛИ менеджер написал руками — аккаунт один)."""
        self.history.append("[менеджер]: " + text.replace("\n", " ⏎ "))

    def rows(self, client_id=CLIENT_ID):
        return moderation_ipc.dialog_rows(client_id)

    def card(self, client_id=CLIENT_ID, hints=None):
        return dc.render(self.rows(client_id), hints=hints, today=TODAY, ipc=moderation_ipc)

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


# ====================== 1. ОДНА КАРТОЧКА НА ВЕСЬ РАЗГОВОР =====================

class TestOneCardPerDialog(DialogBase):
    """Единица — РАЗГОВОР. Двенадцать ходов (медиана замера) дают ОДНУ карточку, а не двенадцать."""

    def test_twelve_turns_give_one_card_and_one_open_proposal(self):
        # Медиана замера 20.08 до запроса данных на бронь — 12 ходов. Строк очереди столько,
        # сколько сообщений клиента; ОТКРЫТОЕ предложение при этом ровно одно.
        self.client_says(OPEN_4_PRICE)
        for i in range(5):
            self.we_say("Ответ менеджера номер " + str(i))
            self.client_says("Уточнение клиента номер " + str(i))
        rows = self.rows()
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(moderation_ipc.open_rows(CLIENT_ID)), 1)
        keys = {dc.card_key(r["client_id"]) for r in rows}
        self.assertEqual(len(keys), 1, "разговор обязан иметь ОДИН ключ карточки")

    def test_card_shows_the_four_things_owner_asked_for(self):
        row, h, _res = self.client_says(OPEN_4_DELIVERY)
        self.we_say("Привозим к отелю бесплатно от 5 суток.")
        row, h, _res = self.client_says("а на неделю сколько выйдет?",
                                        draft="На неделю получится 4928 ฿ за ADV 350.")
        text = self.card(hints=h)
        self.assertIn("🗂 КАРТОЧКА РАЗГОВОРА", text)
        self.assertIn("🎬 С ЧЕГО КЛИЕНТ НАЧАЛ:", text)          # 1) с чего начал
        self.assertIn("Интересует байк в ноябре с 14 по 21", text)
        self.assertIn("📤 УЖЕ ОТПРАВЛЕНО КЛИЕНТУ НАМИ:", text)  # 2) что уже отправлено нами
        self.assertIn("Привозим к отелю", text)
        self.assertIn("🤖 БОТ ПРЕДЛАГАЕТ ОТПРАВИТЬ СЕЙЧАС:", text)   # 3) что предлагает сейчас
        self.assertIn("На неделю получится 4928 ฿ за ADV 350.", text)
        self.assertIn("🧾 ПОЧЕМУ ИМЕННО ТАК:", text)            # 4) почему именно так
        self.assertIn("ходов 3", text)

    def test_card_says_when_the_dialog_was_opened_by_us(self):
        # 285 диалогов из 710 начала КОМПАНИЯ. «Первый вопрос клиента» здесь — уже ОТВЕТ на
        # наше сообщение, и читать его как самостоятельное обращение значит понять наоборот.
        self.we_say("Здравствуйте! Мы TurboBaby, видели ваш запрос в чате.")
        self.client_says("а какие модели есть?")
        text = self.card()
        self.assertIn("разговор начали мы", text)
        self.assertIn("а какие модели есть?", text)

    def test_card_without_a_single_client_reply_is_honest(self):
        self.we_say("Здравствуйте! Мы TurboBaby.")
        rows = ({"id": 1, "client_id": CLIENT_ID, "client_ref": "@x", "status": "posted",
                 "draft": "черновик", "final_text": None, "transcript": "\n".join(self.history),
                 "pricing_note": "", "first_contact": 0},)
        text = dc.render(rows, hints={}, today=TODAY, ipc=moderation_ipc)
        self.assertIn("реплик клиента в разговоре ещё нет", text)

    def test_proposal_shown_is_exactly_what_would_be_sent(self):
        """Карточка, показывающая НЕ ТОТ текст, который уйдёт, опаснее отсутствующей."""
        row, _h, _res = self.client_says(OPEN_PRICE_ONLY, draft="Черновик бота.")
        moderation_ipc.set_candidate(row["id"], "Правка модератора — вот ЭТО уйдёт.")
        row = moderation_ipc.get(row["id"])
        shown = dc.proposal_text(row)
        self.assertIn(shown, self.card())
        dec = dc.decide(row, mc.ACT_SEND, "danya", ipc=moderation_ipc,
                        path=self.meter_path)
        self.assertEqual(dec["decision"], "ready")
        self.assertEqual(dec["final_text"], shown)
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], shown)

    def test_no_open_row_is_said_in_words_not_shown_as_emptiness(self):
        row, _h, _res = self.client_says(OPEN_PRICE_ONLY)
        dc.decide(row, mc.ACT_REJECT, "danya", ipc=moderation_ipc, path=self.meter_path)
        text = self.card()
        self.assertIn(dc.NO_LIVE, text)
        self.assertEqual(self.sent_by_live_executor(), [])


# ================== 2. ИСТОРИЯ СЖАТА, А НЕ ВЫРЕЗАНА ===========================

class TestCompressedHistory(DialogBase):
    """Последние ходы целиком, ранние одной строкой. Человек видит, КУДА идёт разговор."""

    def _long_dialog(self, turns=58):
        # 58 ходов — МАКСИМУМ замера 20.08 до запроса данных на бронь.
        self.client_says(OPEN_4_PRICE)
        for i in range((turns - 1) // 2):
            self.we_say("Наш длинный ответ номер " + str(i) + ". " + "детали " * 40)
            self.client_says("Длинное уточнение клиента номер " + str(i) + ". " + "текст " * 40)

    def test_early_turns_collapse_and_last_turns_stay_verbatim(self):
        self._long_dialog()
        text = self.card()
        self.assertIn("… ещё ", text)
        self.assertIn(" ходов до этого (клиент ", text)
        self.assertIn("── последние 3 ход(ов) целиком ──", text)

    def test_the_longest_measured_dialog_still_fits_a_telegram_message(self):
        """58 ходов по ~250 симв. — это простыня на десятки тысяч знаков. Карточка обязана
        оставаться читаемой; предел здесь — ДЛИНА ТЕКСТА, а не предел ожидания нажатия."""
        self._long_dialog()
        text = self.card()
        raw = sum(len(t["text"]) for t in dc.turns_from_transcript(self.rows()[-1]["transcript"]))
        self.assertGreater(raw, 12000, "фикстура обязана быть простынёй, иначе замок ничего не ловит")
        self.assertLess(len(text), 4096, "карточка не влезает в одно сообщение Telegram")

    def test_head_turn_is_not_repeated_in_history(self):
        self.client_says("НАЧАЛО-ЯКОРЬ: сколько стоит nmax с 1 по 6 сентября?")
        self.we_say("Ответ.")
        self.client_says("а adv?")
        text = self.card()
        self.assertEqual(text.count("НАЧАЛО-ЯКОРЬ"), 1, "первый ход задвоился в карточке")

    def test_turn_is_glued_from_consecutive_replies_of_one_side(self):
        """ХОД = склейка подряд идущих реплик. Человек бьёт вопрос на 2–3 сообщения, и счёт
        по сообщениям завысил бы длину разговора вдвое (та же линейка, что у замера)."""
        turns = dc.turns_from_transcript(
            "[клиент]: привет\n[клиент]: есть nmax?\n[клиент]: на неделю\n"
            "[менеджер]: есть\n[менеджер]: 4928 ฿\n[клиент]: беру")
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[0]["who"], dc.TURN_CLIENT)
        self.assertEqual(turns[0]["text"], "привет\nесть nmax?\nна неделю")
        self.assertEqual(turns[1]["who"], dc.TURN_US)

    def test_line_without_marker_is_not_lost_silently(self):
        turns = dc.turns_from_transcript("сирота без маркера\n[клиент]: привет")
        self.assertEqual(len(turns), 2)
        self.assertIs(turns[0]["who"], dc.TURN_UNKNOWN)
        self.assertEqual(turns[0]["text"], "сирота без маркера")

    def test_clipping_says_so_out_loud(self):
        long_turn = "я" * (dc.TURN_CLIP + 500)
        self.client_says(long_turn)
        text = self.card()
        self.assertIn("обрезано, полностью", text)

    def test_short_dialog_says_it_only_just_started(self):
        self.client_says(OPEN_GREETING)
        self.assertIn("разговор только начался", self.card())


# ================ 3. МНОГОТЕМНЫЙ ВОПРОС: ЧТО ЗАКРЫТО, А ЧТО НЕТ ===============

class TestMultiTopic(DialogBase):
    """71 % первых вопросов многотемные. Бот, отвечающий на одну тему, отвечает на ЧАСТЬ —
    и человек обязан видеть, на какую именно."""

    def test_real_five_topic_opening_is_seen_as_five_topics(self):
        got = dc.topics_of(OPEN_5_TOPICS)
        self.assertIn("наличие/модель", got)
        self.assertIn("даты/срок", got)
        self.assertIn("доставка/адрес", got)
        self.assertIn("шлемы/комплект", got)
        self.assertIn("документы/бронь", got)

    def test_real_openings_are_multi_topic(self):
        for phrase in (OPEN_5_TOPICS, OPEN_4_DELIVERY, OPEN_4_PRICE, OPEN_4_DEPOSIT):
            self.assertGreaterEqual(len(dc.topics_of(phrase)), 3, phrase[:60])

    def test_greeting_alone_is_not_a_topic(self):
        self.assertEqual(dc.topics_of(OPEN_GREETING), ())

    def test_unanswered_topic_is_named_in_the_card(self):
        """Клиент спросил про цену, модели, даты И доставку; бот предлагает только цену."""
        row, h, _res = self.client_says(
            OPEN_4_DELIVERY,
            draft="ADV 350 — 4928 ฿ за 7 дней, депозит 7000 ฿.")
        text = self.card(hints=h)
        self.assertIn("🎯 ТЕМЫ КЛИЕНТА", text)
        self.assertIn("шлемы/комплект", text)
        self.assertIn("❗ БЕЗ ОТВЕТА", text)
        self.assertIn("останется БЕЗ ОТВЕТА даже после отправки", text)
        ledger = dc.build(self.rows(), hints=h, today=TODAY, ipc=moderation_ipc)["ledger"]
        self.assertIn("шлемы/комплект", dc.unanswered(ledger))
        self.assertIn("доставка/адрес", dc.unanswered(ledger))

    def test_topic_answered_earlier_is_marked_done_not_open(self):
        self.client_says(OPEN_4_DELIVERY)
        self.we_say("Привозим к отелю, доставка по району 390 ฿, шлема даём.")
        row, h, _res = self.client_says("а на две недели?", draft="На две недели пересчитаю.")
        ledger = dc.build(self.rows(), hints=h, today=TODAY, ipc=moderation_ipc)["ledger"]
        states = {r["topic"]: r["state"] for r in ledger}
        self.assertEqual(states.get("доставка/адрес"), dc.TOPIC_DONE)
        self.assertEqual(states.get("шлемы/комплект"), dc.TOPIC_DONE)
        self.assertIn("✅ уже отвечено", self.card(hints=h))

    def test_topic_covered_by_the_proposal_is_will_not_is(self):
        """Тема, которую закроет НЕОТПРАВЛЕННОЕ предложение, — это «закроет», а не «закрыто»."""
        row, h, _res = self.client_says(
            OPEN_4_DELIVERY,
            draft="Привезём к отелю: доставка по вашему району 390 ฿.")
        ledger = dc.build(self.rows(), hints=h, today=TODAY, ipc=moderation_ipc)["ledger"]
        states = {r["topic"]: r["state"] for r in ledger}
        self.assertEqual(states.get("доставка/адрес"), dc.TOPIC_NOW)
        self.assertIn("➡️ закроет это предложение", self.card(hints=h))

    def test_our_reply_BEFORE_the_question_is_not_an_answer(self):
        # Наш ход ДО вопроса ответом не является — иначе карточка молча зачла бы старую реплику.
        turns = ({"who": dc.TURN_US, "text": "Доставка по району 390 ฿."},
                 {"who": dc.TURN_CLIENT, "text": "а доставка сколько стоит?"})
        ledger = dc.topic_ledger(turns, proposal="")
        states = {r["topic"]: r["state"] for r in ledger}
        self.assertEqual(states.get("доставка/адрес"), dc.TOPIC_OPEN)

    def test_unrecognized_topics_are_a_third_outcome_not_all_clear(self):
        """Словарь тем — словарный, и его незнание обязано звучать как «не знаю», а не как
        «клиент ничего не спрашивал»."""
        self.client_says("Мне вас порекомендовали, я по поводу погоды на неделе")
        text = self.card()
        self.assertIn("тем не опознал", text)
        self.assertNotIn("❗ БЕЗ ОТВЕТА", text)

    def test_card_says_the_topic_check_is_word_based(self):
        """Галочка не выдаётся за доказательство: перекос назван в самой карточке."""
        _row, h, _r = self.client_says(OPEN_4_DELIVERY)
        self.assertIn(dc.TOPIC_CAVEAT, self.card(hints=h))

    def test_known_skew_of_the_word_based_check_is_documented(self):
        """Обе стороны вранья словаря наблюдаемы — фиксируем их тестом, а не обещанием.
        Правка словаря запрещена (он один на замер и на карточку), поэтому перекос обязан быть
        ЗАМЕРЕН и назван: молчаливое расхождение хуже названного."""
        # (1) лишнее «❗»: наша ЖИВАЯ строка инструмента срок не задевает — она «цена/расчёт».
        tool_line = "ADV 350 | дней: 7 — 4928 ฿ (704 ฿ в день), депозит 7000 ฿."
        self.assertIn("цена/расчёт", dc.topics_of(tool_line))
        self.assertNotIn("даты/срок", dc.topics_of(tool_line))
        turns = ({"who": dc.TURN_CLIENT, "text": "на 7 дней сколько?"},
                 {"who": dc.TURN_US, "text": tool_line})
        states = {r["topic"]: r["state"] for r in dc.topic_ledger(turns, proposal="")}
        self.assertEqual(states.get("даты/срок"), dc.TOPIC_OPEN)   # лишнее ❗ — перекос в «спроси»
        # (2) ложное «✅»: упоминание темы без ответа словарь засчитывает закрытием.
        turns2 = ({"who": dc.TURN_CLIENT, "text": "а доставка почём?"},
                  {"who": dc.TURN_US, "text": "про доставку напишу позже"})
        states2 = {r["topic"]: r["state"] for r in dc.topic_ledger(turns2, proposal="")}
        self.assertEqual(states2.get("доставка/адрес"), dc.TOPIC_DONE)

    def test_topic_vocabulary_is_the_measured_one(self):
        # Свой словарь здесь завести нельзя: «71 % многотемных» и «темы карточки» обязаны
        # считаться ОДНОЙ линейкой (иначе расхождение невидимо).
        self.assertEqual(dc.TOPIC_NAMES, (
            "цена/расчёт", "наличие/модель", "даты/срок", "доставка/адрес",
            "шлемы/комплект", "документы/бронь", "деньги/оплата", "фото/видео"))


# ========= 4. ОТРИЦАТЕЛЬНЫЙ (а): КАРТОЧКА ЕСТЬ, НИКТО НЕ НАЖАЛ ================

class TestNobodyPressed(DialogBase):
    """(а) Карточка висит — клиенту НЕ ушло ничего. Меряем ЖИВЫМ отправителем."""

    def test_card_alone_sends_nothing(self):
        row, h, _res = self.client_says(OPEN_4_PRICE)
        self.assertIn("🗂 КАРТОЧКА РАЗГОВОРА", self.card(hints=h))
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(self.sent_by_live_executor(), [])
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "posted")

    def test_whole_conversation_without_a_press_sends_nothing(self):
        """Двенадцать ходов подряд без единого нажатия — ни одного сообщения клиенту."""
        self.client_says(OPEN_4_PRICE)
        for i in range(6):
            self.client_says("ещё вопрос номер " + str(i))
            self.assertEqual(self.sent_by_live_executor(), [])
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(len(self.rows()), 7)

    def test_ageing_card_never_self_sends(self):
        # Предела ожидания НЕТ: карточка висит, пока не ответят, и не «дозревает» до отправки.
        row, _h, _res = self.client_says(OPEN_4_PRICE)
        with moderation_ipc._conn() as c:
            c.execute("UPDATE drafts SET created_ts=?, updated_ts=? WHERE id=?",
                      ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", row["id"]))
        for _ in range(3):
            self.assertEqual(self.sent_by_live_executor(), [])
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "posted")

    def test_test_mode_holds_even_after_a_press(self):
        suggest.SUGGEST_TEST_MODE = True
        row, _h, _res = self.client_says(OPEN_4_PRICE)
        dec = dc.decide(row, mc.ACT_SEND, "danya", test_mode=True, ipc=moderation_ipc,
                        path=self.meter_path)
        self.assertEqual(dec["decision"], "test_held")
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_module_has_no_automatic_path_to_ready(self):
        """Своего захвата в модуле разговора НЕТ: дорога к 'ready' по-прежнему одна.
        Смотрим ИМЕНА кода, а не текст файла: в докстринге замок назван по имени намеренно."""
        self.assertNotIn("claim_decision", _identifiers(os.path.join(REPO, "dialog_card.py")))
        self.assertIn("claim_decision", _identifiers(os.path.join(REPO, "moderation_card.py")))


# ==== 5. ОТРИЦАТЕЛЬНЫЙ (б): КЛИЕНТ ДОПИСАЛ — УСТАРЕВШЕЕ ОТПРАВИТЬ НЕЛЬЗЯ =====

class TestStaleProposal(DialogBase):
    """(б) ГЛАВНЫЙ НОВЫЙ СЛУЧАЙ. Предложение обновляется под новое сообщение клиента,
    старое НЕПРИНЯТОЕ помечается устаревшим и отправлено быть не может."""

    def test_second_message_makes_the_first_proposal_unsendable(self):
        old, _h, _r = self.client_says(OPEN_4_PRICE, draft="ОТВЕТ НА ПЕРВЫЙ ВОПРОС")
        new, h, res = self.client_says("ой, лучше на месяц посчитайте", draft="ОТВЕТ НА ВТОРОЙ ВОПРОС")
        self.assertEqual(res["superseded"], 1)
        self.assertEqual(moderation_ipc.get(old["id"])["status"], moderation_ipc.STATUS_SUPERSEDED)
        # Нажатие по устаревшему: отказ, и клиенту НЕ уходит ничего.
        dec = dc.decide(moderation_ipc.get(old["id"]), mc.ACT_SEND, "danya", ipc=moderation_ipc,
                        path=self.meter_path)
        self.assertEqual(dec["decision"], "closed")
        self.assertIn("УСТАРЕЛО", dec["card"])
        self.assertIn("не ушло ничего", dec["card"])
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(self.sent_by_live_executor(), [])
        # А СВЕЖЕЕ предложение отправляемо — карточка не «окирпичена».
        dec2 = dc.decide(moderation_ipc.get(new["id"]), mc.ACT_SEND, "danya", ipc=moderation_ipc,
                         path=self.meter_path)
        self.assertEqual(dec2["decision"], "ready")
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "ОТВЕТ НА ВТОРОЙ ВОПРОС")

    def test_stale_snapshot_in_hand_does_not_help_the_presser(self):
        """У нажимающего в руках СТАРЫЙ снимок строки (в Telegram кнопка живёт вечно):
        в памяти статус ещё 'posted'. Решает СУБД, а не снимок."""
        stale_snapshot, _h, _r = self.client_says(OPEN_4_PRICE, draft="СТАРЫЙ ТЕКСТ")
        self.assertEqual(stale_snapshot["status"], "posted")
        self.client_says("и ещё вопрос", draft="НОВЫЙ ТЕКСТ")
        dec = dc.decide(stale_snapshot, mc.ACT_SEND, "danya", ipc=moderation_ipc,
                        path=self.meter_path)
        self.assertEqual(dec["decision"], "closed")
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_three_dopiskas_leave_exactly_one_sendable_proposal(self):
        old1, _h, _r = self.client_says(OPEN_4_PRICE, draft="ТЕКСТ-1")
        old2, _h, _r = self.client_says("а если на две недели?", draft="ТЕКСТ-2")
        old3, _h, _r = self.client_says("и доставка до Найхарна?", draft="ТЕКСТ-3")
        live, _h, _r = self.client_says("и шлемы дадите?", draft="ТЕКСТ-4")
        self.assertEqual(len(moderation_ipc.open_rows(CLIENT_ID)), 1)
        results = []
        for r in (old1, old2, old3):
            results.append(dc.decide(moderation_ipc.get(r["id"]), mc.ACT_SEND, "danya",
                                     ipc=moderation_ipc, path=self.meter_path)["decision"])
        self.assertEqual(results, ["closed", "closed", "closed"])
        self.assertEqual(self.sent_by_live_executor(), [])
        dec = dc.decide(moderation_ipc.get(live["id"]), mc.ACT_SEND, "danya", ipc=moderation_ipc,
                        path=self.meter_path)
        self.assertEqual(dec["decision"], "ready")
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(sent[0][1], "ТЕКСТ-4")

    def test_card_says_out_loud_that_the_old_proposal_is_stale(self):
        old, _h, _r = self.client_says(OPEN_4_PRICE, draft="ТЕКСТ-1")
        _new, h, _r = self.client_says("а на месяц?", draft="ТЕКСТ-2")
        text = self.card(hints=h)
        self.assertIn("♻️ ПРЕДЛОЖЕНИЕ ПЕРЕСОБРАНО", text)
        self.assertIn("ОТПРАВКЕ НЕ ПОДЛЕЖАТ", text)
        self.assertIn("#" + str(old["id"]), text)
        self.assertIn("ТЕКСТ-2", text)
        self.assertNotIn("ТЕКСТ-1", text)   # устаревший текст в предложении не показываем

    def test_stale_list_is_capped_not_a_growing_census(self):
        """В разговоре на 12 ходов устаревших будет 11 — карточка обязана назвать факт, а не
        переписать все id."""
        self.client_says(OPEN_4_PRICE, draft="ТЕКСТ-0")
        for i in range(8):
            self.client_says("дописка " + str(i), draft="ТЕКСТ-" + str(i + 1))
        text = self.card()
        self.assertIn("Прежние предложения разговора (8)", text)
        self.assertIn(" и ещё 5", text)

    def test_race_press_versus_dopiska_never_sends_two(self):
        """Гонка «нажали ровно тогда, когда клиент дописал». Исход может быть любым из двух —
        решение принято ДО дописки, либо предложение устарело ДО нажатия. Чего быть НЕ МОЖЕТ
        никогда — двух сообщений клиенту."""
        for _attempt in range(6):
            self.fresh_queue()
            old, _h, _r = self.client_says(OPEN_4_PRICE, draft="СТАРЫЙ")
            barrier = threading.Barrier(2)
            out = {}

            def press():
                barrier.wait()
                out["dec"] = dc.decide(moderation_ipc.get(old["id"]), mc.ACT_SEND, "danya",
                                       ipc=moderation_ipc, path=self.meter_path)["decision"]

            def dopiska():
                barrier.wait()
                out["killed"] = moderation_ipc.supersede_open(CLIENT_ID,
                                                              reason=dc.SUPERSEDE_REASON)

            threads = [threading.Thread(target=press), threading.Thread(target=dopiska)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            self.assertIn(out.get("dec"), ("ready", "closed"), out)
            sent = self.sent_by_live_executor()
            self.assertLessEqual(len(sent), 1, sent)
            if out.get("dec") == "ready":
                self.assertEqual(len(sent), 1)      # успел раньше дописки — решение в силе
            else:
                self.assertEqual(len(sent), 0)      # дописка успела раньше — не ушло ничего

    def test_already_accepted_decision_survives_a_dopiska(self):
        """Гасим только НЕПРИНЯТОЕ. Решение, принятое человеком раньше дописки, остаётся в силе:
        иначе дописка клиента отменяла бы уже сказанное «отправить»."""
        row, _h, _r = self.client_says(OPEN_4_PRICE, draft="ПРИНЯТЫЙ ТЕКСТ")
        dec = dc.decide(row, mc.ACT_SEND, "danya", ipc=moderation_ipc, path=self.meter_path)
        self.assertEqual(dec["decision"], "ready")
        killed = moderation_ipc.supersede_open(CLIENT_ID, reason=dc.SUPERSEDE_REASON)
        self.assertEqual(killed, 0)
        self.assertEqual(moderation_ipc.get(row["id"])["status"], "ready")
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "ПРИНЯТЫЙ ТЕКСТ")

    def test_superseded_row_cannot_be_revived_at_the_queue_layer(self):
        old, _h, _r = self.client_says(OPEN_4_PRICE, draft="СТАРЫЙ")
        self.client_says("дописка", draft="НОВЫЙ")
        won, row = moderation_ipc.claim_decision(old["id"], "ready", final_text="СТАРЫЙ",
                                                 decided_by="@hacker")
        self.assertFalse(won)
        self.assertEqual(row["status"], moderation_ipc.STATUS_SUPERSEDED)
        self.assertEqual(moderation_ipc.fetch_ready(), [])

    def test_wrong_order_leaves_a_window_and_our_order_does_not(self):
        """КОНТРФАКТ: порядок «сначала гасим, потом ставим» — это и есть замок, а не украшение.
        Обратный порядок оставляет миг, в котором отправляемы ОБА предложения."""
        self.client_says(OPEN_4_PRICE, draft="ТЕКСТ-1")
        # НЕВЕРНЫЙ порядок вручную: сначала постановка, гашение потом.
        self.history.append("[клиент]: дописка")
        transcript = "\n".join(self.history)
        note, _h = self._note(transcript)
        moderation_ipc.enqueue_draft({
            "client_id": CLIENT_ID, "client_ref": "@dlgclient", "lang": "ru",
            "incoming": "дописка", "draft": "ТЕКСТ-2", "first_contact": False,
            "transcript": transcript, "pricing_note": note, "client_name": "ТЕСТ"})
        self.assertEqual(len(moderation_ipc.open_rows(CLIENT_ID)), 2,
                         "контрфакт не воспроизвёл окно — тест ничего не доказывает")
        # ВЕРНЫЙ порядок (accept_client_message): открытая строка всегда РОВНО одна.
        self.fresh_queue()
        self.client_says(OPEN_4_PRICE, draft="ТЕКСТ-1")
        for i in range(4):
            self.client_says("дописка " + str(i), draft="ТЕКСТ-" + str(i + 2))
            self.assertEqual(len(moderation_ipc.open_rows(CLIENT_ID)), 1)

    def test_supersede_without_client_id_is_loud_not_a_silent_zero(self):
        with self.assertRaises(ValueError):
            moderation_ipc.supersede_open(None)


# ====== 6. ОТРИЦАТЕЛЬНЫЙ (в): ДВОЕ НАЖАЛИ РАЗОМ — УШЛО РОВНО ОДНО ============

class TestFirstResponderWins(DialogBase):
    """(в) Захват первым ответившим остаётся как есть: двух сообщений одному клиенту не бывает."""

    def test_eight_simultaneous_presses_send_exactly_one(self):
        row, _h, _r = self.client_says(OPEN_4_PRICE, draft="ЕДИНСТВЕННЫЙ ОТВЕТ")
        n = 8
        barrier = threading.Barrier(n)
        results, errors = [], []
        lock = threading.Lock()

        def press(who):
            barrier.wait()
            try:
                dec = dc.decide(row, mc.ACT_SEND, who, ipc=moderation_ipc, path=self.meter_path)
            except Exception as e:            # noqa: BLE001 — гонка обязана быть БЕЗ сбоев
                with lock:
                    errors.append(type(e).__name__ + ": " + str(e))
                return
            with lock:
                results.append(dec["decision"])

        threads = [threading.Thread(target=press, args=("mod" + str(i),)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [], "гонка дала сбой вместо честного отказа")
        self.assertEqual(results.count("ready"), 1, results)
        self.assertEqual(results.count("closed"), n - 1, results)
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(sent[0][0], CLIENT_ID)

    def test_second_press_sees_the_winner(self):
        row, _h, _r = self.client_says(OPEN_4_PRICE)
        dc.decide(row, mc.ACT_SEND, "danya", ipc=moderation_ipc, path=self.meter_path)
        late = dc.decide(row, mc.ACT_SEND, "dasha", ipc=moderation_ipc, path=self.meter_path)
        self.assertEqual(late["decision"], "closed")
        self.assertIn("@danya", late["card"])
        self.assertEqual(len(self.sent_by_live_executor()), 1)


# ====== 7. ОТРИЦАТЕЛЬНЫЙ (г): НЕ МОГУ ПОСЧИТАТЬ — «НЕ СЧИТАЮ», А НЕ ЧИСЛО ====

class TestCannotCompute(DialogBase):
    """(г) Три класса владельца: граница сезонов, срок от 30 суток, модель без цены."""

    def test_season_boundary_crossing_is_declared(self):
        _row, h, _r = self.client_says("нужен nmax с 25 октября по 10 ноября")
        text = self.card(hints=h)
        self.assertIn("⛔ НЕ СЧИТАЮ", text)
        self.assertIn(mc.NOT_COMPUTED, text)

    def test_long_term_is_declared_not_guessed(self):
        _row, h, _r = self.client_says("nmax на месяц, с 1 сентября по 5 октября")
        card = dc.build(self.rows(), hints=h, today=TODAY, ipc=moderation_ipc)
        codes = [c["code"] for c in card["cannot"]]
        self.assertIn(mc.CANNOT_LONG, codes)
        self.assertIn("⛔ НЕ СЧИТАЮ", card["text"])

    def test_model_without_price_shows_no_number_at_all(self):
        # R7 в парке ЕСТЬ, а цены на него Календарь не отдаёт (фикстура живого Bridge).
        _row, h, _r = self.client_says("сколько стоит R7 с 1 по 6 сентября?")
        card = dc.build(self.rows(), hints=h, today=TODAY, ipc=moderation_ipc)
        codes = [c["code"] for c in card["cannot"]]
        self.assertIn(mc.CANNOT_NO_PRICE, codes)
        self.assertIn("• цена: " + mc.NOT_COMPUTED, card["text"])

    def test_computable_case_declares_nothing(self):
        _row, h, _r = self.client_says("nmax с 1 по 6 сентября")
        card = dc.build(self.rows(), hints=h, today=TODAY, ipc=moderation_ipc)
        self.assertEqual(card["cannot"], ())
        self.assertNotIn("⛔ НЕ СЧИТАЮ", card["text"])

    def test_no_dates_at_all_is_not_silently_green(self):
        _row, h, _r = self.client_says("а какие вообще модели есть?")
        card = dc.build(self.rows(), hints=h, today=TODAY, ipc=moderation_ipc)
        self.assertIn("• сезон: не знаю", card["text"])


# ===================== 8. ЗАМЕР НАГРУЗКИ НАДЗОРА ==============================

class TestLoadMeter(unittest.TestCase):
    """Числа копятся к ноябрю: падение времени на карточку до секунд = надзор стал рефлексом."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "events.jsonl")
        card_load.reset_errors()

    def tearDown(self):
        card_load.reset_errors()
        self._tmp.cleanup()

    def _ev(self, kind, card, ts):
        return {"kind": kind, "card": card, "ts": ts}

    def test_cards_per_day_counts_cards_not_shows(self):
        evs = [self._ev(card_load.EV_SHOWN, "dlg:1", "2026-11-03T10:00:00+00:00"),
               self._ev(card_load.EV_SHOWN, "dlg:1", "2026-11-03T10:05:00+00:00"),   # пересборка
               self._ev(card_load.EV_SHOWN, "dlg:2", "2026-11-03T11:00:00+00:00"),
               self._ev(card_load.EV_SHOWN, "dlg:3", "2026-11-04T09:00:00+00:00")]
        s = card_load.summarize(evs)
        self.assertEqual(s["per_day"], {"2026-11-03": 2, "2026-11-04": 1})
        self.assertEqual(s["cards"], 3)
        self.assertEqual(s["shows"], 4)

    def test_wait_is_measured_from_the_LAST_show(self):
        """Клиент дописал → предложение пересобрано → человек обязан читать заново, и отсчёт
        начинается заново. Иначе рефлекс маскировался бы долгим ожиданием клиента."""
        evs = [self._ev(card_load.EV_SHOWN, "dlg:1", "2026-11-03T10:00:00+00:00"),
               self._ev(card_load.EV_SHOWN, "dlg:1", "2026-11-03T10:09:00+00:00"),
               self._ev(card_load.EV_PRESSED, "dlg:1", "2026-11-03T10:09:03+00:00")]
        s = card_load.summarize(evs)
        self.assertEqual(s["waits"], [3.0])          # 3 с на ТЕКУЩЕМ тексте — вот он, рефлекс
        self.assertEqual(s["opens"], [543.0])        # а карточка прожила 9 минут
        self.assertEqual(s["wait_median"], 3.0)

    def test_press_without_a_show_is_a_third_outcome_not_a_zero(self):
        evs = [self._ev(card_load.EV_PRESSED, "dlg:9", "2026-11-03T10:00:00+00:00")]
        s = card_load.summarize(evs)
        self.assertEqual(s["unpaired"], 1)
        self.assertEqual(s["waits"], [])
        self.assertIsNone(s["wait_median"], "нажатие без показа зачлось нулевым ожиданием")

    def test_empty_meter_says_i_do_not_know_not_zero(self):
        s = card_load.summarize([])
        self.assertIsNone(s["wait_median"])
        self.assertIsNone(s["wait_p90"])
        self.assertIsNone(s["wait_min"])
        self.assertEqual(s["cards"], 0)

    def test_unparsable_timestamp_is_counted_not_swallowed(self):
        evs = [self._ev(card_load.EV_SHOWN, "dlg:1", "не-дата")]
        s = card_load.summarize(evs)
        self.assertEqual(s["undated"], 1)
        self.assertEqual(s["cards"], 0)

    def test_record_and_load_roundtrip(self):
        card_load.record(card_load.EV_SHOWN, "dlg:5", ts="2026-11-03T10:00:00+00:00",
                         path=self.path)
        card_load.record(card_load.EV_PRESSED, "dlg:5", ts="2026-11-03T10:02:00+00:00",
                         path=self.path, action="send", outcome="ready")
        s = card_load.stats(path=self.path)
        self.assertEqual(s["waits"], [120.0])
        self.assertEqual(s["presses_per_day"], {"2026-11-03": 1})
        self.assertEqual(s["meter_errors"], ())

    def test_meter_failure_is_said_out_loud_not_swallowed(self):
        # Живой класс сбоя: на месте каталога копилки оказался ФАЙЛ (так выглядит чужая
        # запись по тому же пути). Запись обязана вернуть None и назвать причину.
        blocker = os.path.join(self._tmp.name, "занято")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("не каталог")
        got = card_load.record(card_load.EV_SHOWN, "dlg:1",
                               path=os.path.join(blocker, "events.jsonl"))
        self.assertIsNone(got)
        self.assertTrue(len(card_load.errors()) > 0, "сбой замера промолчал")

    def test_broken_path_does_not_escape_as_exception(self):
        got = card_load.record(card_load.EV_SHOWN, "dlg:1", path="\0/x.jsonl")
        self.assertIsNone(got)
        self.assertTrue(len(card_load.errors()) > 0)

    def test_unknown_event_kind_is_refused(self):
        with self.assertRaises(ValueError):
            card_load.event("выдумка", "dlg:1")


class TestLoadMeterIsNotShownToOwner(DialogBase):
    """«Числа копить, владельцу не показывать» — замок, а не обещание в докстринге."""

    def test_card_text_carries_no_meter_numbers(self):
        row, h, _r = self.client_says(OPEN_4_PRICE)
        dc.card_shown(CLIENT_ID, ts="2026-11-03T10:00:00+00:00", path=self.meter_path)
        dc.decide(row, mc.ACT_REJECT, "danya", ipc=moderation_ipc,
                  ts="2026-11-03T10:00:07+00:00", path=self.meter_path)
        s = card_load.stats(path=self.meter_path)
        self.assertEqual(s["waits"], [7.0])
        self.client_says("ещё вопрос")
        text = self.card()
        for word in ("waits", "wait_median", "нагрузк", "секунд на карточк", "рефлекс"):
            self.assertNotIn(word, text)

    def test_only_the_two_recording_points_touch_the_meter(self):
        """`build`/`render` счётчика не читают ВОВСЕ — иначе числа однажды поедут в карточку."""
        path = os.path.join(REPO, "dialog_card.py")
        with io.open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        touching = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id == "card_load":
                        touching.add(node.name)
        self.assertEqual(touching, {"card_shown", "decide"}, touching)

    def test_broken_meter_cannot_change_what_is_sent(self):
        """Даже полностью сломанный замер не отправляет и не отменяет отправку."""
        class DeadMeter:
            EV_SHOWN = card_load.EV_SHOWN
            EV_PRESSED = card_load.EV_PRESSED

            def record(self, *a, **kw):
                raise OSError("замер мёртв")

        row, _h, _r = self.client_says(OPEN_4_PRICE, draft="ТЕКСТ")
        with self.assertRaises(OSError):
            dc.decide(row, mc.ACT_SEND, "danya", ipc=moderation_ipc, meter=DeadMeter())
        # Решение человека уже принято ДО замера и в силе: клиенту уходит ровно одно.
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "ТЕКСТ")


# ======================= 9. ЗАМКИ ПО ПОСТРОЕНИЮ МОДУЛЯ ========================

def _identifiers(path):
    """Все ИМЕНА исходника. Проза докстрингов сюда не попадает СОЗНАТЕЛЬНО: замок обязан ловить
    ручку в коде, а не слово в комментарии."""
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
    return out


class TestModuleInvariants(unittest.TestCase):

    PATHS = (os.path.join(REPO, "dialog_card.py"), os.path.join(REPO, "card_load.py"))

    def test_modules_cannot_send_anything(self):
        for path in self.PATHS:
            names = _identifiers(path)
            for forbidden in ("send_message", "send_to_client", "poll_and_send", "telegram",
                              "telethon", "requests", "urllib"):
                self.assertNotIn(forbidden, names, path + ": появился канал отправки " + forbidden)

    def test_no_deadline_knob_exists(self):
        # Предела ожидания НЕТ: карточка висит, пока не ответят (решение владельца).
        for path in self.PATHS:
            names = _identifiers(path)
            for forbidden in ("timeout", "ttl", "TTL", "deadline", "expire", "expires", "expiry"):
                self.assertNotIn(forbidden, names, path + ": появился предел ожидания " + forbidden)

    def test_no_confidence_knob_exists(self):
        # Ничего мимо человека: самостоятельности «по уверенности» у бота нет.
        for path in self.PATHS:
            names = _identifiers(path)
            for forbidden in ("confidence", "threshold", "auto_send", "autosend", "auto_approve"):
                self.assertNotIn(forbidden, names, path + ": появилась ручка уверенности " + forbidden)

    def test_superseded_literal_matches_the_queue(self):
        self.assertEqual(mc.SUPERSEDED, moderation_ipc.STATUS_SUPERSEDED)

    def test_superseded_is_outside_both_claim_sets(self):
        self.assertNotIn(moderation_ipc.STATUS_SUPERSEDED, moderation_ipc.CLAIMABLE_FROM)
        self.assertNotIn(moderation_ipc.STATUS_SUPERSEDED, moderation_ipc.DECISION_STATUSES)

    def test_open_statuses_mirror_the_queue(self):
        self.assertEqual(dc.OPEN_STATUSES, moderation_ipc.CLAIMABLE_FROM)

    def test_supersede_writes_only_the_stale_status(self):
        """Гашение не умеет поставить 'ready' ни одной веткой — литерал в SQL ровно один."""
        with io.open(os.path.join(REPO, "moderation_ipc.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="moderation_ipc.py")
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "supersede_open")
        names = {sub.id for sub in ast.walk(fn) if isinstance(sub, ast.Name)}
        self.assertIn("STATUS_SUPERSEDED", names)
        for forbidden in ("DECISION_STATUSES",):
            self.assertNotIn(forbidden, names)
        texts = {sub.value for sub in ast.walk(fn)
                 if isinstance(sub, ast.Constant) and isinstance(sub.value, str)}
        for forbidden in ("ready", "test_held", "sent"):
            self.assertNotIn(forbidden, texts)

    def test_measured_thresholds_are_named_not_magic(self):
        self.assertEqual(dc.TAIL_TURNS, 3)
        self.assertEqual(dc.EARLY_LINES, 6)
        self.assertEqual(len(dc.TOPICS), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
