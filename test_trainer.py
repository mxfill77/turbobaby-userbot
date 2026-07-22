# -*- coding: utf-8 -*-
"""
test_trainer.py — ГРУППА-ТРЕНАЖЁР клиентского бота (изолированный контур ПК).

Прогонять с TESTING=1 (изоляция боевого IPC; moderation_ipc уводит DB в tmp):
    TESTING=1 python -m unittest test_trainer -v

Покрытие (по ТЗ):
  • «Заново» чистит контекст ТЕСТ-клиента (collected_facts пуст) и N++;
  • урок применяется со следующего ответа (behavior → playbook → в system-prompt), источник «тренажёр»;
  • «отмени урок N» откатывает правило и снимает пометку источника;
  • CRM-карточка из тренажёра помечена [ТЕСТ] (боевые «Входящие брони» не трогаем);
  • изоляция: is_trainer_chat строго по привязанному chat_id (вне группы — боевой путь);
  • шапка [тренажёр | ТЕСТ-N | правил: K] присутствует; подсказка-строка присутствует;
  • анти-тайский: тайские буквы в выводе не появляются (знак бата ฿ сохраняется);
  • команды панели — только approver (тап чужого → отказ, без сброса);
  • ТЕКСТ-команды работают как дубль независимо от модербота (fallback).
"""

import os
import json
import tempfile
import unittest

os.environ.setdefault("TESTING", "1")

import trainer
import suggest
import moderation_ipc


def _dict_store():
    """dict-backed get/set для сессионного состояния (вместо moderation_ipc.meta)."""
    d = {}

    def get(k):
        return d.get(k)

    def set(k, v):
        d[k] = "" if v is None else str(v)

    return d, get, set


# ------------------------------- шапка / подсказка ---------------------------

class TestHeaderAndHint(unittest.TestCase):
    def test_header_exact(self):
        self.assertEqual(trainer.header(3, 7), "[тренажёр | ТЕСТ-3 | правил: 7]")

    def test_render_answer_has_header_and_hint(self):
        r = trainer.render_answer(2, 5, "Здравствуйте! Что вас интересует?")
        self.assertIn("[тренажёр | ТЕСТ-2 | правил: 5]", r)
        self.assertIn("Здравствуйте!", r)
        self.assertIn("команды:", r)          # подсказка-строка присутствует
        self.assertTrue(trainer.has_header(r))

    def test_has_header_negative(self):
        self.assertFalse(trainer.has_header("Здравствуйте, обычный текст"))
        self.assertFalse(trainer.has_header(""))


# ------------------------------- команды -------------------------------------

class TestCommandParse(unittest.TestCase):
    def test_reset(self):
        for t in ("заново", "Заново", " сброс ", "/reset", "новый клиент"):
            self.assertEqual(trainer.parse_command(t)[0], "reset", t)

    def test_crm(self):
        for t in ("до crm", "В CRM", "crm", "/crm"):
            self.assertEqual(trainer.parse_command(t)[0], "crm", t)

    def test_lesson(self):
        kind, payload = trainer.parse_command("урок: не здоровайся дважды")
        self.assertEqual(kind, "lesson")
        self.assertEqual(payload, "не здоровайся дважды")

    def test_cancel(self):
        self.assertEqual(trainer.parse_command("отмени урок 2"), ("cancel", 2))
        self.assertEqual(trainer.parse_command("отменить урок #5"), ("cancel", 5))

    def test_plain_client_message_is_not_command(self):
        # обычная клиентская реплика → не команда (идёт в пайплайн)
        for t in ("привет", "какие цены на аренду?", "хочу скутер на неделю"):
            self.assertEqual(trainer.parse_command(t), (None, None), t)


# ------------------------------- транскрипт ----------------------------------

class TestTranscript(unittest.TestCase):
    def test_append_client_and_manager(self):
        t = trainer.append_turn("", "client", "привет")
        self.assertEqual(t, "[клиент]: привет")
        t = trainer.append_turn(t, "manager", "Здравствуйте!")
        self.assertEqual(t, "[клиент]: привет\n[менеджер]: Здравствуйте!")

    def test_manager_turn_strips_header_and_hint(self):
        # в транскрипт кладём ТОЛЬКО клиентское тело ответа (без шапки/подсказки тренажёра)
        full = trainer.render_answer(1, 0, "Привет! Чем помочь?")
        t = trainer.append_turn("", "manager", full)
        self.assertEqual(t, "[менеджер]: Привет! Чем помочь?")
        self.assertNotIn("тренажёр", t)
        self.assertNotIn("команды:", t)

    def test_has_manager_turn_first_contact(self):
        self.assertFalse(trainer.has_manager_turn("[клиент]: привет"))
        self.assertTrue(trainer.has_manager_turn("[клиент]: привет\n[менеджер]: hi"))


# ------------------------------- анти-тайский --------------------------------

class TestAntiThai(unittest.TestCase):
    def test_strip_thai_letters_keeps_baht(self):
        s = "Доставка в Раваи — 590 ฿ สวัสดี ครับ"
        out = trainer.strip_thai(s)
        self.assertFalse(any(("ก" <= c <= "ฺ") or ("เ" <= c <= "๛") for c in out),
                         "тайских букв в выводе быть не должно")
        self.assertIn("฿", out)               # знак бата легитимен — сохраняется
        self.assertIn("Доставка в Раваи", out)

    def test_strip_thai_noop_on_clean(self):
        s = "Здравствуйте! Скутер 300 ฿/сутки."
        self.assertEqual(trainer.strip_thai(s), s)

    def test_render_answer_run_through_strip_is_thai_free(self):
        r = trainer.strip_thai(trainer.render_answer(1, 0, "Цена 300 ฿ สวัสดี"))
        self.assertFalse(any(("ก" <= c <= "ฺ") or ("เ" <= c <= "๛") for c in r))


# ------------------------------- гипотезы ------------------------------------

class TestHypotheses(unittest.TestCase):
    def test_parse_strips_numbering_and_dedupes(self):
        raw = ("1. Не здоровайся дважды в одном диалоге\n"
               "2) Сразу называй цену, не тяни\n"
               "- Предлагай доставку явно\n"
               "Не здоровайся дважды в одном диалоге\n")   # дубль
        hyps = trainer.parse_hypotheses(raw)
        self.assertEqual(len(hyps), 3)
        self.assertEqual(hyps[0], "Не здоровайся дважды в одном диалоге")
        self.assertEqual(hyps[1], "Сразу называй цену, не тяни")

    def test_parse_caps_at_limit(self):
        raw = "\n".join(f"правило номер {i}" for i in range(10))
        self.assertEqual(len(trainer.parse_hypotheses(raw, limit=4)), 4)

    def test_prompt_mentions_both_turns(self):
        system, user = trainer.hypotheses_prompt("сколько стоит?", "300 бат в сутки")
        self.assertIn("сколько стоит?", user)
        self.assertIn("300 бат в сутки", user)


# ------------------------------- сессионное состояние + сброс ----------------

class TestStateReset(unittest.TestCase):
    def test_reset_clears_context_and_increments_n(self):
        d, get, set = _dict_store()
        set(trainer.K_N, 1)
        set(trainer.K_TRANSCRIPT, "[клиент]: хочу скутер на 5 дней, паспорт есть\n[менеджер]: ок")
        set(trainer.K_INCOMING, "хочу скутер")
        set(trainer.K_ANSWER, "ок")
        # до сброса — факты из транскрипта есть (модель/срок/паспорт)
        facts_before = suggest.collected_facts(trainer.get_transcript(get))
        self.assertTrue(any(facts_before.values()))
        n = trainer.reset(get, set)
        self.assertEqual(n, 2)                                   # N++
        self.assertEqual(trainer.get_transcript(get), "")        # транскрипт очищен
        # collected_facts — ДЕРИВАТ транскрипта: пустой транскрипт ⇒ все факты False
        self.assertFalse(set_of_true(suggest.collected_facts(trainer.get_transcript(get))))
        self.assertEqual(trainer.get_last_pair(get), ("", ""))
        self.assertEqual(trainer.get_hyps(get), [])

    def test_record_turn_and_last_pair(self):
        d, get, set = _dict_store()
        trainer.record_turn("привет", "[клиент]: привет\n[менеджер]: hi", "hi", set)
        self.assertEqual(trainer.get_last_pair(get), ("привет", "hi"))
        self.assertEqual(trainer.get_transcript(get), "[клиент]: привет\n[менеджер]: hi")

    def test_hyps_roundtrip(self):
        d, get, set = _dict_store()
        trainer.set_hyps(["a", "b"], set)
        self.assertEqual(trainer.get_hyps(get), ["a", "b"])


def set_of_true(facts):
    return {k for k, v in facts.items() if v}


# ------------------------------- изоляция ------------------------------------

class TestIsolation(unittest.TestCase):
    def test_is_trainer_chat_by_bound_meta(self):
        d, get, set = _dict_store()
        self.assertFalse(trainer.is_trainer_chat(-100500, get))     # ничего не привязано
        trainer.bind_chat(-100500, get, set)
        self.assertTrue(trainer.is_trainer_chat(-100500, get))      # в группе — тренажёрный путь
        self.assertFalse(trainer.is_trainer_chat(-999, get))        # другая группа — боевой путь
        self.assertFalse(trainer.is_trainer_chat(None, get))

    def test_bind_inits_n_to_one(self):
        d, get, set = _dict_store()
        trainer.bind_chat(-777, get, set)
        self.assertEqual(trainer.get_n(get), 1)

    def test_env_override_wins(self):
        old = trainer.TRAINER_GROUP_ID_ENV
        trainer.TRAINER_GROUP_ID_ENV = -100777
        try:
            d, get, set = _dict_store()          # meta пуст — но env-override задан
            self.assertTrue(trainer.is_trainer_chat(-100777, get))
            self.assertFalse(trainer.is_trainer_chat(-100500, get))
        finally:
            trainer.TRAINER_GROUP_ID_ENV = old

    def test_title_matches(self):
        self.assertTrue(trainer.title_matches("Тренеровка"))
        self.assertTrue(trainer.title_matches(" тренеровка "))
        self.assertFalse(trainer.title_matches("Модерация ответов"))


# ------------------------------- урок: behavior|code (инъекции) --------------

class TestLessonRouting(unittest.TestCase):
    def test_behavior_appends_and_marks_source(self):
        calls, marked = {}, {}

        def _append(r):
            calls["rule"] = r
            return "added"

        dec = trainer.apply_lesson(
            "не здоровайся дважды",
            append_rule=_append,
            classify=lambda r: "behavior",
            mark=lambda r: marked.setdefault("rule", r))
        self.assertEqual(dec["axis"], "behavior")
        self.assertIn("Принято", dec["card"])
        self.assertIn("тренажёр", dec["card"])          # источник помечен в карточке
        self.assertEqual(calls["rule"], "не здоровайся дважды")
        self.assertEqual(marked["rule"], "не здоровайся дважды")

    def test_code_makes_owner_card_no_append(self):
        def _fail(_r):
            raise AssertionError("append_rule НЕ должен вызываться для code-урока")

        dec = trainer.apply_lesson(
            "если клиент прислал паспорт, ставь галочку passport",
            append_rule=_fail, classify=lambda r: "code", mark=_fail)
        self.assertEqual(dec["axis"], "code")
        self.assertIn("код-фикс", dec["card"])

    def test_empty_lesson(self):
        dec = trainer.apply_lesson("   ", append_rule=lambda r: "added",
                                   classify=lambda r: "behavior", mark=lambda r: None)
        self.assertEqual(dec["status"], "error")

    def test_classify_lesson_real(self):
        # реальный классификатор 2-й оси (lesson_router)
        self.assertEqual(trainer.classify_lesson("пиши короче, без воды"), "behavior")
        self.assertEqual(
            trainer.classify_lesson("если клиент прислал паспорт, ставь галочку passport"), "code")


# ------------- урок применяется со следующего ответа + откат (интеграция) ----

class TestLessonAppliesAndCancels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pb = os.path.join(self.tmp, "playbook.md")
        with open(self.pb, "w", encoding="utf-8") as f:
            f.write("# playbook\n")
        self._old_pb = suggest.PLAYBOOK_FILE
        self._old_side = trainer.TRAINER_RULES_FILE
        suggest.PLAYBOOK_FILE = self.pb
        trainer.TRAINER_RULES_FILE = os.path.join(self.tmp, "trainer_rules.json")

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._old_pb
        trainer.TRAINER_RULES_FILE = self._old_side

    def test_behavior_rule_reaches_next_system_prompt_with_source(self):
        remark = "сразу называй цену, не тяни с ответом"
        dec = trainer.apply_lesson(remark, classify=lambda r: "behavior")   # реальный suggest.append + mark
        self.assertEqual(dec["axis"], "behavior")
        self.assertIn(remark, suggest.load_playbook())                      # записано в книгу
        # применится со СЛЕДУЮЩЕГО ответа — правило попадает в system-prompt генератора
        sysp = suggest.make_system_prompt("", "ru", False, "", playbook=suggest.load_playbook())
        self.assertIn(remark, sysp)
        self.assertTrue(trainer.is_trainer_rule(remark))                    # источник «тренажёр»

    def test_cancel_lesson_rolls_back_and_unmarks(self):
        remark = "предлагай доставку явно в первом ответе"
        trainer.apply_lesson(remark, classify=lambda r: "behavior")
        self.assertIn(remark, suggest.load_playbook())
        self.assertTrue(trainer.is_trainer_rule(remark))
        dec = trainer.cancel_lesson(1)                                      # «отмени урок 1»
        self.assertEqual(dec["status"], "removed")
        self.assertNotIn(remark, suggest.load_playbook())                  # правило откатано
        self.assertFalse(trainer.is_trainer_rule(remark))                  # пометка снята

    def test_cancel_out_of_range(self):
        dec = trainer.cancel_lesson(9)
        self.assertEqual(dec["status"], "empty")                            # правил нет


# ------------------------------- CRM-карточка [ТЕСТ] -------------------------

class TestCrmCard(unittest.TestCase):
    def test_crm_card_marked_test(self):
        card = trainer.crm_card("Клиент: ТЕСТ\nМодель: Yamaha")
        self.assertTrue(card.startswith("🆕 БРОНЬ [ТЕСТ]"))
        self.assertIn("Yamaha", card)


# --------- ВЛОЖЕНИЯ в тренажёре: гео-ПИН и фото доходят маркерами (регресс ТЕСТ-2) -----
# Дефекты живого прогона (ТЕСТ-2, 01:07-01:09): турн собирался из event.raw_text → гео-ПИН и
# фото Telegram ТЕРЯЛИСЬ. Следствие: пин не резолвился в зону (бот спрашивал район), а фото
# паспорта не засчитывалось трекером. Фикс: trainer.client_body ставит те же маркеры, что
# suggest.transcript_from (ЛС-путь) — «[локация lat,lon]» и «[фото]».

class TestTrainerMediaBody(unittest.TestCase):
    def test_client_body_priority(self):
        # текст > фото > гео > медиа-без-текста (как transcript_from)
        self.assertEqual(trainer.client_body("хочу скутер"), "хочу скутер")
        self.assertEqual(trainer.client_body("", has_photo=True), "[фото]")
        self.assertEqual(trainer.client_body("", geo_marker="[локация 7.77,98.33]"),
                         "[локация 7.77,98.33]")
        self.assertEqual(trainer.client_body("  ", has_photo=True,
                                             geo_marker="[локация 7.77,98.33]"), "[фото]")
        self.assertEqual(trainer.client_body(""), "[медиа/без текста]")

    def test_geo_marker_public_wrapper(self):
        class Geo:
            def __init__(s, lat, lon):
                s.lat, s.long = lat, lon
        self.assertEqual(suggest.geo_marker(Geo(7.771, 98.327)), "[локация 7.771000,98.327000]")


class TestTrainerMediaFacts(unittest.TestCase):
    """Голдены ТЕСТ-2: пин с координатами → зона + geo✅; фото → passport✅; регресс
    ссылки-без-координат (ТЕСТ-1) → честный вопрос остаётся (geo НЕ выдумывается)."""

    def _transcript_with_geo_and_photo(self):
        class Geo:
            def __init__(s, lat, lon):
                s.lat, s.long = lat, lon
        t = trainer.append_turn("", "client",
                                trainer.client_body("хочу на 7 дней с 1 по 8 августа"))
        t = trainer.append_turn(t, "manager", "Здравствуйте! Уточню.")
        # клиент кинул гео-ПИН (Telegram location) — отдельным сообщением, без текста
        t = trainer.append_turn(t, "client",
                                trainer.client_body("", geo_marker=suggest.geo_marker(Geo(7.771, 98.327))))
        # затем фото паспорта — тоже отдельным сообщением, без подписи
        t = trainer.append_turn(t, "client", trainer.client_body("", has_photo=True))
        return t

    def test_geo_pin_resolves_zone_and_geo_fact(self):
        t = self._transcript_with_geo_and_photo()
        # 1) трекер видит гео
        self.assertTrue(suggest.collected_facts(t)["geo"])
        # 2) координаты пина дошли до hints и до резолвера доставки → ЗОНА (не вопрос про район)
        hints = suggest.extract_booking_hints(t)
        self.assertEqual(hints.get("geo_pin"), (7.771, 98.327))
        res = suggest._resolve_delivery_for_draft(
            hints, _resolve_coords=lambda lat, lon: {"status": "zone", "zone": "Раваи", "price": 590})
        self.assertEqual(res["status"], "zone")
        self.assertEqual(res["price"], 590)

    def test_photo_marks_passport_fact(self):
        t = self._transcript_with_geo_and_photo()
        self.assertTrue(suggest.collected_facts(t)["passport"])

    def test_link_without_coords_stays_honest_question(self):
        # РЕГРЕСС ТЕСТ-1: ссылка-без-координат — НЕ пин: координат нет (geo_pin=None), маркер
        # «[локация lat,lon]» НЕ подставляется, текст ссылки сохраняется как есть. Резолвер
        # доставки уходит по maps_link и БЕЗ координат честно даёт [уточнить] (вопрос про район),
        # а НЕ выдуманную зону — то же поведение, что вчера на ТЕСТ-1.
        link = "вот https://maps.app.goo.gl/c4G4B3sNrfJZBSue6"
        body = trainer.client_body(link)                 # текст есть → гео-маркер НЕ подставляем
        self.assertEqual(body, link)
        t = trainer.append_turn("", "client", body)
        hints = suggest.extract_booking_hints(t)
        self.assertIsNone(hints.get("geo_pin"))          # координат нет — пина не выдумали
        res = suggest._resolve_delivery_for_draft(
            hints, _resolve_text=lambda u: {"status": "uncertain", "marker": "[уточнить]",
                                            "zone": None, "price": None})
        self.assertEqual(res["status"], "uncertain")     # честный вопрос про район остаётся


# ------------------------------- модербот: панель owner-only (async) ---------

class TestModerbotCallbackGate(unittest.IsolatedAsyncioTestCase):
    """Кнопки панели тренажёра только для approver; тап чужого → отказ, без сброса.
    approver-тап «Заново» реально инкрементит N. Модербот использует боевой moderation_ipc.meta
    (под TESTING — tmp DB)."""

    def setUp(self):
        import moderation_bot
        self.mb = moderation_bot
        moderation_ipc.init_db()
        self._old_appr = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = {"mike"}
        moderation_ipc.set_meta(trainer.K_N, "5")
        moderation_ipc.set_meta(trainer.K_TRANSCRIPT, "[клиент]: привет\n[менеджер]: hi")

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._old_appr

    def _fakes(self, username, chat_id=-100500):
        sent = []

        class Bot:
            async def send_message(self, cid, text, **kw):
                sent.append((cid, text))

        class Ctx:
            bot = Bot()

        class User:
            def __init__(self, u):
                self.username = u

        class Msg:
            def __init__(self, cid):
                self.chat_id = cid

        class Q:
            def __init__(self, u, cid):
                self.from_user = User(u)
                self.message = Msg(cid)

        return Ctx(), Q(username, chat_id), sent

    async def test_foreign_tap_refused_no_reset(self):
        ctx, q, sent = self._fakes("intruder")
        await self.mb._trainer_callback(ctx, q, "tr:reset")
        self.assertTrue(any("⛔" in t for _, t in sent), "чужому — отказ")
        self.assertEqual(trainer.get_n(), 5, "сброс НЕ должен произойти для чужого")

    async def test_approver_reset_works(self):
        ctx, q, sent = self._fakes("mike")
        await self.mb._trainer_callback(ctx, q, "tr:reset")
        self.assertEqual(trainer.get_n(), 6, "approver-тап «Заново» → N++")
        self.assertTrue(any("ТЕСТ-6" in t for _, t in sent))

    async def test_fallback_textcommand_reset_without_moderbot(self):
        # ТЕКСТ-команда «заново» = дубль кнопки: сброс через trainer.reset без участия модербота
        moderation_ipc.set_meta(trainer.K_N, "7")
        n = trainer.reset()
        self.assertEqual(n, 8)
        self.assertEqual(trainer.get_transcript(), "")


# --------- РЕНДЕР гипотез: полный текст в сообщении, кнопки = номера --------------

LONG_HYP = ("Не отвечай одной строкой на вопрос о доставке: сначала уточни адрес или район, "
            "затем назови стоимость доставки именно для этого района и срок подачи байка, "
            "и только после этого предлагай перейти к оформлению брони — иначе клиент уходит.")


class TestHypsRender(unittest.TestCase):
    """Живой провал 22.07: подпись inline-кнопки Telegram режет по ширине — владелец не мог
    дочитать гипотезу. Полные формулировки печатаются нумерованным списком В СООБЩЕНИИ."""

    def test_long_hypothesis_full_in_message(self):
        self.assertGreater(len(LONG_HYP), 200)
        parts = trainer.hyps_messages(["коротко", LONG_HYP])
        self.assertEqual(len(parts), 1)
        self.assertIn(LONG_HYP, parts[0])                      # ЦЕЛИКОМ, без «…»
        self.assertIn("2. " + LONG_HYP, parts[0])              # под своим номером
        self.assertNotIn("…[обрезано]", parts[0])
        self.assertTrue(parts[0].startswith(trainer.HYPS_TITLE))

    def test_any_n_numbered_from_one(self):
        for n in (2, 3, 5, 7):
            parts = trainer.hyps_messages([f"правило {i}" for i in range(n)])
            body = "\n".join(parts)
            for i in range(n):
                self.assertIn(f"{i + 1}. правило {i}", body)
            self.assertNotIn(f"{n + 1}. ", body)

    def test_empty_list_returns_title_only(self):
        self.assertEqual(trainer.hyps_messages([]), [trainer.HYPS_TITLE])

    def test_limit_4096_splits_by_hypothesis_boundary(self):
        hyps = [f"г{i} " + "я" * 900 for i in range(9)]        # ~8.2k символов
        parts = trainer.hyps_messages(hyps)
        self.assertGreater(len(parts), 1, "длинный список обязан разбиться, а не обрезаться")
        for p in parts:
            self.assertLessEqual(len(p), trainer.TG_MSG_LIMIT)
        body = "\n".join(parts)
        for i, h in enumerate(hyps):                            # ничего не потеряно
            self.assertIn(f"{i + 1}. {h}", body)
        self.assertNotIn("…[обрезано]", body)
        for p in parts[1:]:
            self.assertTrue(p.startswith(trainer.HYPS_TITLE_CONT))

    def test_single_giant_hypothesis_truncated_with_marker(self):
        parts = trainer.hyps_messages(["ю" * 6000])
        for p in parts:
            self.assertLessEqual(len(p), trainer.TG_MSG_LIMIT)
        self.assertIn("…[обрезано]", "\n".join(parts))          # усечение — только явное

    def test_no_thai_in_render(self):
        parts = trainer.hyps_messages(["скажи цену 500 ฿ สวัสดี сразу"])
        self.assertNotIn("สวัสดี", parts[0])
        self.assertIn("฿", parts[0])


class TestHypsKeyboard(unittest.TestCase):
    def test_buttons_are_numbers_and_carry_index(self):
        import moderation_bot
        hyps = [LONG_HYP, "б", "в", "г", "д", "е"]
        kb = moderation_bot._kb_trainer_hyps(hyps)
        flat = [b for row in kb.inline_keyboard for b in row]
        self.assertEqual([b.text for b in flat[:-1]], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual([b.callback_data for b in flat[:-1]],
                         [f"tr:hyp:{i}" for i in range(6)])     # индекс, а не текст — маппинг надёжен
        self.assertEqual(flat[-1].callback_data, "tr:hyp:other")
        self.assertIn("другое", flat[-1].text)
        self.assertTrue(all(len(row) <= 5 for row in kb.inline_keyboard))

    def test_n_not_four(self):
        import moderation_bot
        for n in (2, 3, 5):
            kb = moderation_bot._kb_trainer_hyps([f"г{i}" for i in range(n)])
            flat = [b for row in kb.inline_keyboard for b in row]
            self.assertEqual(len(flat), n + 1)                  # N номеров + «другое»
            self.assertEqual([b.text for b in flat[:-1]], [str(i + 1) for i in range(n)])


class TestHypsTapMapping(unittest.IsolatedAsyncioTestCase):
    """Тап по номеру выбирает ИМЕННО ту гипотезу (и «✍️ другое» работает как раньше)."""

    def setUp(self):
        import moderation_bot
        self.mb = moderation_bot
        moderation_ipc.init_db()
        self._old_appr = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = {"mike"}
        self._old_apply = trainer.apply_lesson
        self.applied = []
        trainer.apply_lesson = lambda remark: (self.applied.append(remark)
                                               or {"card": f"✅ урок: {remark}"})

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._old_appr
        trainer.apply_lesson = self._old_apply

    def _fakes(self, username="mike", chat_id=-100500):
        sent = []

        class Bot:
            async def send_message(self, cid, text, **kw):
                sent.append((cid, text, kw.get("reply_markup")))

        class Ctx:
            bot = Bot()

        class User:
            def __init__(self, u):
                self.username = u

        class Msg:
            def __init__(self, cid):
                self.chat_id = cid

        class Q:
            def __init__(self, u, cid):
                self.from_user = User(u)
                self.message = Msg(cid)

        return Ctx(), Q(username, chat_id), sent

    async def test_tap_number_picks_that_hypothesis(self):
        trainer.set_hyps(["первая", LONG_HYP, "третья"])
        ctx, q, sent = self._fakes()
        await self.mb._trainer_callback(ctx, q, "tr:hyp:1")     # кнопка «2» → индекс 1
        self.assertEqual(self.applied, [LONG_HYP])

    async def test_other_button_unchanged(self):
        ctx, q, sent = self._fakes()
        await self.mb._trainer_callback(ctx, q, "tr:hyp:other")
        self.assertEqual(self.applied, [])
        self.assertTrue(any("урок:" in t for _, t, _ in sent))

    async def test_teach_posts_full_text_and_number_buttons(self):
        moderation_ipc.set_meta(trainer.K_INCOMING, "а сколько доставка?")
        moderation_ipc.set_meta(trainer.K_ANSWER, "500 бат")
        old_llm = suggest.default_llm_caller
        suggest.default_llm_caller = lambda: (lambda s, u: f"коротко\n{LONG_HYP}\nтретья")
        try:
            ctx, q, sent = self._fakes()
            await self.mb._trainer_callback(ctx, q, "tr:teach")
        finally:
            suggest.default_llm_caller = old_llm
        body = "\n".join(t for _, t, _ in sent)
        self.assertIn(LONG_HYP, body)                           # формулировка читаема ЦЕЛИКОМ
        kb = sent[-1][2]                                        # клавиатура — на последней части
        self.assertIsNotNone(kb)
        flat = [b for row in kb.inline_keyboard for b in row]
        self.assertEqual([b.text for b in flat], ["1", "2", "3", "✍️ другое"])
        # тап по номеру «2» из этой же выдачи → та самая длинная гипотеза
        await self.mb._trainer_callback(ctx, q, flat[1].callback_data)
        self.assertEqual(self.applied, [LONG_HYP])


# --------- накопление окна диалога + токен состояния (регресс ТЕСТ-4 раздвоения) ------

class TestTrainerAccumulation(unittest.TestCase):
    def test_seq_bump_and_reset_bumps_seq(self):
        d, get, put = _dict_store()
        put(trainer.K_SEQ, 0)
        self.assertEqual(trainer.bump_seq(get, put), 1)
        self.assertEqual(trainer.bump_seq(get, put), 2)
        put(trainer.K_N, 4)
        n = trainer.reset(get, put)
        self.assertEqual(n, 5)
        self.assertEqual(trainer.get_seq(get), 3)          # сброс тоже инкрементит токен

    def test_set_transcript_persists_and_incoming(self):
        d, get, put = _dict_store()
        trainer.set_transcript("[клиент]: привет", incoming="привет", set=put)
        self.assertEqual(trainer.get_transcript(get), "[клиент]: привет")
        self.assertEqual(trainer.get_last_pair(get)[0], "привет")

    def test_window_is_cumulative_across_events(self):
        # текст (даты) + гео-ПИН разными событиями → collected_facts КУМУЛЯТИВНЫ по окну
        # (а не по одному событию): и даты, и гео видны одновременно → нет переспроса собранного.
        class Geo:
            def __init__(s, lat, lon):
                s.lat, s.long = lat, lon
        t = trainer.append_turn("", "client",
                                trainer.client_body("аренда с 1 по 8 августа на 7 дней"))
        t = trainer.append_turn(t, "client",
                                trainer.client_body("", geo_marker=suggest.geo_marker(Geo(7.771, 98.327))))
        f = suggest.collected_facts(t)
        self.assertTrue(f["dates"])          # даты из первого события
        self.assertTrue(f["term"])
        self.assertTrue(f["geo"])            # гео из второго — В ТОМ ЖЕ окне
        self.assertEqual(suggest.extract_booking_hints(t).get("geo_pin"), (7.771, 98.327))


class TestTrainerDebounce(unittest.IsolatedAsyncioTestCase):
    """Регресс ТЕСТ-4 раздвоения: два близких Telegram-события (текст+пин) → ОДИН ответ по
    ПОЛНОМУ окну; гонка/клоббер контекста исключены; сброс инвалидирует отложенный ответ."""

    def setUp(self):
        import userbot_listen
        self.ub = userbot_listen
        moderation_ipc.init_db()
        for k in (trainer.K_TRANSCRIPT, trainer.K_INCOMING, trainer.K_ANSWER, trainer.K_HYPS):
            moderation_ipc.set_meta(k, "")
        moderation_ipc.set_meta(trainer.K_N, "4")
        moderation_ipc.set_meta(trainer.K_SEQ, "0")
        moderation_ipc.set_meta(trainer.K_CHAT, "-100999")
        self._old_deb = self.ub.TRAINER_DEBOUNCE_SEC
        self.ub.TRAINER_DEBOUNCE_SEC = 0.05
        self._gen_calls = []

        async def fake_gen(transcript, first):
            self._gen_calls.append(transcript)
            return "Ответ бота ТЕСТ-клиенту"
        self._old_gen = self.ub._trainer_generate
        self.ub._trainer_generate = fake_gen

    def tearDown(self):
        self.ub.TRAINER_DEBOUNCE_SEC = self._old_deb
        self.ub._trainer_generate = self._old_gen

    def _event(self, chat_id=-100999):
        sent = []

        class Client:
            async def send_message(self, cid, text, **kw):
                sent.append((cid, text))

        class Ev:
            def __init__(s):
                s.client = Client()
                s.chat_id = chat_id
        return Ev(), sent

    async def test_two_rapid_events_one_reply_full_context(self):
        import asyncio
        ev, sent = self._event()
        await self.ub._trainer_client_turn(ev, "хочу yamaha с 1 по 8 августа")
        await self.ub._trainer_client_turn(ev, "[локация 7.771000,98.327000]")
        await asyncio.sleep(0.25)
        self.assertEqual(len(sent), 1, f"ожидался ОДИН ответ, а не {len(sent)}")
        self.assertEqual(len(self._gen_calls), 1, "генерация должна пройти ОДИН раз")
        full = self._gen_calls[0]
        self.assertIn("yamaha", full)                          # контекст текста…
        self.assertIn("[локация 7.771000,98.327000]", full)    # …И пина — в ОДНОМ окне
        self.assertTrue(any("[тренажёр | ТЕСТ-4" in m for _, m in sent))

    async def test_reset_invalidates_pending_reply(self):
        import asyncio
        ev, sent = self._event()
        await self.ub._trainer_client_turn(ev, "хочу скутер")
        trainer.reset()                                        # сброс до срабатывания дебаунса
        await asyncio.sleep(0.25)
        self.assertEqual(len(sent), 0, "после сброса устаревший ответ постить нельзя")
        self.assertEqual(len(self._gen_calls), 0)


if __name__ == "__main__":
    unittest.main()
