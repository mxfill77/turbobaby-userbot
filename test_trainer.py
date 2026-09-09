# -*- coding: utf-8 -*-
"""
test_trainer.py — ГРУППА-ТРЕНАЖЁР клиентского бота (изолированный контур ПК).

Прогонять с TESTING=1 (изоляция боевого IPC; moderation_ipc уводит DB в tmp):
    TESTING=1 python -m unittest test_trainer -v

Покрытие (по ТЗ):
  • «Заново» чистит контекст ТЕСТ-клиента (collected_facts пуст) и N++;
  • урок применяется со следующего ответа (behavior → playbook → в system-prompt), источник «тренажёр»;
  • «отмени урок N» ОТЗЫВАЕТ урок #N в базе (`lesson_store.withdraw`, право — как у перевода
    кандидата), после чего он не доезжает до промпта; буллет книги-снимка и пометка источника
    снимаются заодно, а строка таблицы ОСТАЁТСЯ с отметкой «снят»;
  • CRM-карточка из тренажёра помечена [ТЕСТ] (боевые «Входящие брони» не трогаем);
  • изоляция: is_trainer_chat строго по привязанному chat_id (вне группы — боевой путь);
  • шапка [тренажёр | ТЕСТ-N | правил: K] присутствует; подсказка-строка присутствует;
  • анти-тайский: тайские буквы в выводе не появляются (знак бата ฿ сохраняется);
  • команды панели — только approver (тап чужого → отказ, без сброса);
  • ТЕКСТ-команды работают как дубль независимо от модербота (fallback).
"""

import os
import json
import inspect
import datetime
import tempfile
import unittest

os.environ.setdefault("TESTING", "1")

import trainer
import suggest
import lesson_store
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
        # rules=[] — здесь меряем РАЗБОР, а не сверку с книгой (задача 220): книга живая и растёт,
        # а тест обязан остаться про нумерацию и дедуп.
        hyps = trainer.parse_hypotheses(raw, rules=[])
        self.assertEqual(len(hyps), 3)
        self.assertEqual(hyps[0], "Не здоровайся дважды в одном диалоге")
        self.assertEqual(hyps[1], "Сразу называй цену, не тяни")

    def test_parse_caps_at_limit(self):
        raw = "\n".join(f"правило номер {i}" for i in range(10))
        self.assertEqual(len(trainer.parse_hypotheses(raw, limit=4, rules=[])), 4)

    def test_prompt_mentions_both_turns(self):
        system, user = trainer.hypotheses_prompt("сколько стоит?", "300 бат в сутки")
        self.assertIn("сколько стоит?", user)
        self.assertIn("300 бат в сутки", user)


# --------------- КОНТЕКСТ ГИПОТЕЗ: книга правил + канон (задача 220) ---------------
# Живой случай 05.09.2026 02:29 (KB_trainer_log, TEST-11): сборщик видел РОВНО ДВЕ строки и выдал
# четыре гипотезы, из которых ДВЕ противоречат бизнесу. Обе — ДОСЛОВНО из мозга, не пересказ.

LIVE_0905_BAD_ISLAND = (
    "Клиент не указал место/остров подачи — бот должен спросить город, а не только район, если "
    "сервис работает в нескольких локациях; не переходи сразу к району без уточнения.")
LIVE_0905_BAD_TIME = (
    "Уточняй точное время подачи/возврата мотобайка при заданных датах, а не только дни — не "
    "оставляй время аренды неопределённым.")
LIVE_0905_GOOD_PRICE = (
    "Проверяй актуальную стоимость и наличие модели в системе перед ответом, а не полагайся на "
    "общий прайс — не называй цену без подтверждения из базы наличия.")
LIVE_0905_GOOD_MARK = (
    "Подтверждай итоговые условия (депозит, шлем, даты) явным списком-чеклистом для клиента, а не "
    "одной строкой в скобках для внутреннего трекинга.")


class TestHypsContext(unittest.TestCase):
    """Тренер видит книгу правил и факты о компании; вредное владельцу не показывается."""

    def tearDown(self):
        # Флаги захода — модульные (в модерботе три вызова идут подряд в одном обработчике).
        # Между тестами их обязан гасить тест, иначе исход одного протекает в рендер другого.
        trainer._LAST_MISSING = []
        trainer._LAST_DROPPED = []

    # --- сам контекст -------------------------------------------------------

    def test_canon_carries_the_facts_that_decide_the_live_case(self):
        """Не «канон непустой», а ИМЕННО те факты, которыми судятся обе вредные гипотезы 05.09."""
        canon = trainer.business_canon()
        self.assertTrue(canon, "канон обязан собираться из живых источников кода")
        self.assertIn("ПХУКЕТЕ", canon)                       # где работаем
        self.assertIn("Другого города и другого острова", canon)
        self.assertIn("сроки подготовки или выдачи", canon)    # что НЕ утверждаем (белый список)
        self.assertIn("точного времени подачи/возврата в брони нет", canon)
        self.assertIn("весь состав брони", canon)              # поля брони: часов среди них нет
        self.assertIn("Депозит: либо деньги, либо паспорт", canon)   # критфакты дословно

    def test_prompt_carries_book_canon_and_transcript(self):
        book = trainer.rules_book()
        self.assertTrue(book, "книга правил обязана читаться (suggest.load_playbook)")
        tr = "[клиент]: привет\n[менеджер]: здравствуйте\n[клиент]: adv 350 на неделю"
        system, user = trainer.hypotheses_prompt("adv 350 на неделю", "ADV 350 — 6 286 ฿",
                                                 transcript=tr)
        self.assertIn("ФАКТЫ О КОМПАНИИ", system)
        self.assertIn("КНИГА ПРАВИЛ БОТА", system)
        self.assertIn(book.splitlines()[-1].strip(), system)   # книга ЦЕЛИКОМ, не выжимка
        self.assertIn("привет", user)                          # весь транскрипт, а не одна реплика
        self.assertIn("adv 350 на неделю", user)
        self.assertEqual(trainer.last_missing(), [])
        # Предсмертный взгляд задания: материалы даны для ПРОВЕРКИ, а не для пересказа.
        self.assertIn("для ПРОВЕРКИ", system)
        self.assertIn("НЕ для пересказа", system)

    def test_transcript_is_cut_from_the_head_keeping_fresh_turns(self):
        long_tr = "\n".join("[клиент]: реплика %d" % i for i in range(400))
        self.assertGreater(len(long_tr), trainer.HYPS_TRANSCRIPT_MAX)
        _, user = trainer.hypotheses_prompt("свежая", "ответ", canon="К", rules="П",
                                            transcript=long_tr)
        self.assertIn("реплика 399", user)                     # хвост уцелел
        self.assertNotIn("реплика 0\n", user)                  # голова срезана
        self.assertLess(len(user), len(long_tr))

    # --- ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1: живой случай 05.09 --------------------------

    def test_live_0905_two_harmful_dropped_two_useful_stay(self):
        """Ответ модели в НОВОМ формате на живом входе: вредные — в «ОТСЕЯНО», годные — владельцу.

        Что здесь доказано машиной: секция «ОТСЕЯНО» до владельца НЕ доезжает ни одной строкой, а
        канон (тест выше) несёт ровно те факты, которыми обе вредные и судятся. Само суждение
        «противоречит бизнесу» выносит модель, которая теперь эти факты ВИДИТ, — и это измерено
        живым прогоном в артефакте, а не здесь."""
        raw = "\n".join([
            trainer.HYPS_MARK_KEEP,
            LIVE_0905_GOOD_PRICE,
            LIVE_0905_GOOD_MARK,
            trainer.HYPS_MARK_DROP,
            LIVE_0905_BAD_ISLAND + " — противоречит факту: работаем только на Пхукете",
            LIVE_0905_BAD_TIME + " — противоречит факту: времени подачи в брони нет",
        ])
        hyps = trainer.parse_hypotheses(raw, rules=[])
        self.assertEqual(hyps, [LIVE_0905_GOOD_PRICE, LIVE_0905_GOOD_MARK])
        shown = "\n".join(trainer.hyps_messages(hyps))
        for bad in ("остров подачи", "точное время подачи"):
            self.assertNotIn(bad, shown)
        self.assertEqual(len(trainer.last_dropped()), 2)
        self.assertIn("Скрыто гипотез: 2", shown)              # владелец видит, что отсев был

    def test_flat_answer_without_markers_parses_as_before(self):
        """FAIL-SAFE: модель ответила плоским списком (старый формат) — разбор прежний, ничего
        не теряем. Иначе первый же ответ без секций отдал бы владельцу пустой список."""
        raw = LIVE_0905_GOOD_PRICE + "\n" + LIVE_0905_GOOD_MARK
        self.assertEqual(trainer.parse_hypotheses(raw, rules=[]),
                         [LIVE_0905_GOOD_PRICE, LIVE_0905_GOOD_MARK])
        self.assertEqual(trainer.last_dropped(), [])

    # --- ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2: дубль правила, которое в книге УЖЕ есть -----

    def test_hypothesis_repeating_existing_rule_is_dropped(self):
        """Повтор = то, что книга и так отказалась бы принять (suggest._rules_similar — предикат
        append_playbook_rule). Берём ЖИВОЕ правило книги и подаём его гипотезой."""
        book = trainer.book_rules_list()
        self.assertTrue(book, "книга обязана разбираться в список правил")
        existing = max(book, key=len)
        fresh = "Не пиши клиенту служебную скобку [собрано: …] — она для менеджера"
        raw = "\n".join([trainer.HYPS_MARK_KEEP, existing, fresh])
        hyps = trainer.parse_hypotheses(raw)                   # rules=None → живая книга
        self.assertNotIn(existing, hyps, "правило из книги владельцу второй раз не показываем")
        self.assertIn(fresh, hyps, "новая гипотеза обязана уцелеть")
        why = dict(trainer.last_dropped()).get(existing, "")
        self.assertTrue(why.startswith("повтор правила книги"), why)
        self.assertIn("повтор правила книги: 1", "\n".join(trainer.hyps_messages(hyps)))

    def test_near_duplicate_by_meaning_is_dropped_too(self):
        """«По смыслу», а не только дословно: перефраз правила книги тоже не показываем."""
        rule = "не дублируй название модели — одно упоминание модели на строку"
        raw = trainer.HYPS_MARK_KEEP + "\nне дублируй название модели, одно упоминание на строку"
        self.assertEqual(trainer.parse_hypotheses(raw, rules=[rule]), [])
        self.assertEqual(len(trainer.last_dropped()), 1)

    def test_fragment_of_a_rule_is_not_a_repeat(self):
        """Замок соразмерности. Предикат книги считает похожим и ВХОЖДЕНИЕ: слово «коротко» лежит
        внутри правила стиля целиком. Без замка гипотеза умирала бы об это вхождение — живой
        промах поймал тест панели тренажёра (кнопок стало 2 вместо 3)."""
        rule = "Коротко, вежливо, на языке клиента. Один вопрос за раз, без давления."
        self.assertEqual(trainer.parse_hypotheses("коротко", rules=[rule]), ["коротко"])
        self.assertEqual(trainer.last_dropped(), [])
        # а соразмерный повтор того же правила по-прежнему ловится
        self.assertEqual(trainer.parse_hypotheses(rule, rules=[rule]), [])

    # --- ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3: канон недоступен ---------------------------

    def test_blind_mode_is_said_out_loud_not_silently_old_behaviour(self):
        """Канон/книга не прочитаны → владелец УЗНАЁТ, что тренер советует вслепую."""
        system, _ = trainer.hypotheses_prompt("вопрос", "ответ", canon="", rules="",
                                              transcript="")
        self.assertIn("ФАКТОВ О КОМПАНИИ СЕЙЧАС НЕТ", system)
        self.assertIn("КНИГИ ПРАВИЛ СЕЙЧАС НЕТ", system)
        self.assertNotIn("ФАКТЫ О КОМПАНИИ (бизнес-канон)", system)
        self.assertEqual(trainer.last_missing(), ["фактов о компании", "книги правил"])
        parts = trainer.hyps_messages(["гипотеза раз", "гипотеза два"])
        self.assertIn("ВСЛЕПУЮ", parts[0])
        self.assertIn("фактов о компании", parts[0])
        self.assertTrue(parts[-1].startswith(trainer.HYPS_TITLE))   # клавиатура — на последней
        for p in parts:
            self.assertLessEqual(len(p), trainer.TG_MSG_LIMIT)

    def test_broken_source_yields_no_book_and_no_facts(self):
        """Не «канон пустой строкой», а СБОЙ источника: книга — '', критфактов в каноне нет.
        Тренажёр при этом не падает — исход честный, а не исключение в обработчике."""
        broken = _BrokenSuggest()
        self.assertEqual(trainer.rules_book(mod=broken), "")
        self.assertEqual(trainer.book_rules_list(text=""), [])
        self.assertNotIn("КРИТИЧНЫЕ ФАКТЫ", trainer.business_canon(mod=broken))

    def test_quiet_path_adds_no_notes(self):
        """Контекст полный и отсева не было — служебных строк над списком НЕТ (регресс вида)."""
        trainer.hypotheses_prompt("вопрос", "ответ")
        trainer.parse_hypotheses(trainer.HYPS_MARK_KEEP + "\nсовсем новая гипотеза про шлемы")
        parts = trainer.hyps_messages(["одна", "две"])
        self.assertEqual(len(parts), 1)
        self.assertTrue(parts[0].startswith(trainer.HYPS_TITLE))


class _BrokenSuggest:
    """Источник, который есть, но не отдаёт ничего (сбой чтения книги/фактов)."""

    KNOWN_MODELS = []
    _COLL_LABELS = []

    def load_playbook(self):
        raise OSError("книга не прочитана")

    def _read_park_snapshot(self):
        raise OSError("снимок парка не прочитан")

    def _bike_key(self, name):
        return ""


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

    def test_text_path_lesson_gets_number_like_buttons(self):
        """Пакет «полнота лога» п.3: урок ТЕКСТОВОГО пути («урок: …» / «✍ другое») получает #N
        в карточке — та же нумерация, что кнопочный apply_lessons и «отмени урок N»."""
        rules = [{"n": 1, "rule": "старое"}, {"n": 2, "rule": "не здоровайся дважды"}]
        dec = trainer.apply_lesson("не здоровайся дважды", append_rule=lambda r: "added",
                                   classify=lambda r: "behavior", mark=lambda r: True,
                                   list_rules=lambda: rules)
        self.assertEqual(dec["n"], 2)
        self.assertIn("урок #2", dec["card"])
        # дубликат тоже показывает номер СУЩЕСТВУЮЩЕГО правила
        dup = trainer.apply_lesson("старое", append_rule=lambda r: "duplicate",
                                   classify=lambda r: "behavior", mark=lambda r: True,
                                   list_rules=lambda: rules)
        self.assertEqual(dup["n"], 1)
        # книга недоступна → урок принят, карточка честная, но без номера (fail-safe)
        boom = trainer.apply_lesson("x", append_rule=lambda r: "added",
                                    classify=lambda r: "behavior", mark=lambda r: True,
                                    list_rules=lambda: (_ for _ in ()).throw(RuntimeError("нет")))
        self.assertIsNone(boom["n"])
        self.assertIn("Принято", boom["card"])

    def test_apply_lesson_never_raises(self):
        """Пакет «полнота лога» п.4: исключение ВНУТРИ урока не роняет обработчик группы —
        status='error' + карточка-ошибка (след в лог; карточку в TRN пишет вызывающий)."""
        dec = trainer.apply_lesson(
            "урок с падением",
            append_rule=lambda r: (_ for _ in ()).throw(RuntimeError("книга сломана")),
            classify=lambda r: "behavior", mark=lambda r: True)
        self.assertEqual(dec["status"], "error")
        self.assertIn("внутренняя ошибка", dec["card"])
        self.assertIn("RuntimeError", dec["card"])                  # тип виден владельцу

    def test_cancel_lesson_never_raises(self):
        """Исключение ВНУТРИ отмены не роняет обработчик группы.

        МЕРИТСЯ ТЕПЕРЬ НА ОТЗЫВЕ В БАЗЕ, а не на правке книги (06.09.2026): работу делает он, и
        падать по-настоящему может он. Книга-снимок правится после и best-effort. `find`
        инъектирован сознательно — без него ветка пошла бы читать БОЕВУЮ таблицу уроков."""
        dec = trainer.cancel_lesson(
            3, who="filipp", may_write=lambda _u: True,
            find=lambda _n, _p=None: (None, True),
            withdraw=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("таблица сломана")))
        self.assertEqual(dec["status"], "error")
        self.assertIn("не отменён", dec["card"])
        self.assertIn("RuntimeError", dec["card"])

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
        # БАЗА УРОКОВ — ТОЖЕ В ПЕСОЧНИЦУ (06.09.2026). С подключением отмены к отзыву в базе
        # ветка отмены читает и правит таблицу; без подмены обоих путей (модульного и того,
        # откуда читает промпт) тест трогал бы БОЕВОЙ `lesson_store.tsv` этой машины.
        self.store = os.path.join(self.tmp, "lesson_store.tsv")
        self._old_store = lesson_store.STORE_PATH
        self._old_base = suggest.LESSON_BASE_PATH
        lesson_store.STORE_PATH = self.store
        suggest.LESSON_BASE_PATH = self.store

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._old_pb
        trainer.TRAINER_RULES_FILE = self._old_side
        lesson_store.STORE_PATH = self._old_store
        suggest.LESSON_BASE_PATH = self._old_base

    # ПОЧЕМУ ЗДЕСЬ ТЕПЕРЬ ЯВНЫЙ `append_rule` (05.09.2026). Боевой путь урока больше НЕ пишет в
    # плоскую книгу: он кладёт кандидата в базу уроков (`test_lesson_write.py`). Книжный синк
    # оставлен живым для ОТДЕЛЬНОГО задания «перенос накопленных правил», и меряется он теперь
    # только по явному имени. Убрать `append_rule=` отсюда — значит померить не то, что написано
    # в названии теста.
    def test_behavior_rule_reaches_next_system_prompt_with_source(self):
        remark = "сразу называй цену, не тяни с ответом"
        dec = trainer.apply_lesson(remark, classify=lambda r: "behavior",
                                   append_rule=suggest.append_playbook_rule)   # старый синк книги
        self.assertEqual(dec["axis"], "behavior")
        self.assertIn(remark, suggest.load_playbook())                      # записано в книгу
        # применится со СЛЕДУЮЩЕГО ответа — правило попадает в system-prompt генератора
        sysp = suggest.make_system_prompt("", "ru", False, "", playbook=suggest.load_playbook())
        self.assertIn(remark, sysp)
        self.assertTrue(trainer.is_trainer_rule(remark))                    # источник «тренажёр»

    # ПОЧЕМУ ЭТИ ДВА ТЕСТА ПЕРЕПИСАНЫ, А НЕ ОСЛАБЛЕНЫ (06.09.2026). Они мерили ВЫРЕЗАНИЕ БУЛЛЕТА
    # ИЗ КНИГИ и были зелёными ровно тогда, когда команда НЕ отменяла урок: с коммита 8bfb55b
    # ответ собирается из базы, и правка книги на него не влияет — то есть старый зелёный
    # доказывал ложь («↩️ Отменил» при живом уроке в промпте). Предмет обоих сохранён целиком —
    # «после отмены правило не доезжает до промпта, пометка источника снята» и «промах номером
    # получает внятный ответ»; поменялось МЕСТО, где это правда, и оно названо здесь.
    def test_cancel_lesson_withdraws_in_base_and_unmarks(self):
        remark = "предлагай доставку явно в первом ответе"
        n = lesson_store.add(question="сколько стоит на неделю?", bot_answer="уточню и вернусь",
                             correct=remark, why="владелец назвал причину", who="filipp",
                             when="2026-09-06T10:00:00Z", path=self.store,
                             source=lesson_store.SOURCE_TRAINER)
        suggest.append_playbook_rule(remark)         # тот же текст лежит и в книге-снимке
        trainer.mark_source(remark)
        self.assertIn(remark, suggest.load_playbook())
        self.assertTrue(trainer.is_trainer_rule(remark))

        dec = trainer.cancel_lesson(n, who="filipp", may_write=lambda _u: True,   # «отмени урок N»
                                    path=self.store, regress=lambda *_a, **_k: {"spawned": False})
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertNotIn(remark, suggest.load_playbook())                  # из ОТВЕТА ушло
        self.assertFalse(trainer.is_trainer_rule(remark))                  # пометка снята
        rows = lesson_store.load(self.store).lessons
        self.assertEqual([l.number for l in rows], [n], "строка урока пропала из таблицы")
        self.assertEqual(lesson_store.active(rows), ())

    def test_cancel_names_missing_number_and_missing_base_apart(self):
        no_base = trainer.cancel_lesson(9, who="filipp", may_write=lambda _u: True,
                                        path=self.store)
        self.assertEqual(no_base["status"], trainer.STATUS_NO_STORE, no_base["card"])
        n = lesson_store.add(question="вопрос", bot_answer="ответ", correct="какое-то правило",
                             why="причина", who="filipp", when="2026-09-06T10:00:00Z",
                             path=self.store, source=lesson_store.SOURCE_TRAINER)
        gone = trainer.cancel_lesson(n + 5, who="filipp", may_write=lambda _u: True,
                                     path=self.store)
        self.assertEqual(gone["status"], trainer.STATUS_NOT_FOUND, gone["card"])
        self.assertEqual([l.number for l in lesson_store.active(lesson_store.load(self.store)
                                                                .lessons)], [n])


# ------------- источник урока переживает формат книги (класс 03.09.2026) -----
# Книга кладёт правило С ДАТОЙ («- (2026-07-22) текст»), пометку ставит СЫРОЙ текст урока.
# ЧЕСТНЫЕ ЧИСЛА (замер 04.09.2026, боевой сайдкар 6 записей × снимок книги 9 буллетов):
# путь КОДА — ДО 5 из 9, ПОСЛЕ 5 из 9 (не изменилось: suggest снимает дату САМ, :901 и :935);
# сырая строка книги — ДО 0 из 9, ПОСЛЕ 5 из 9. Значит боевого разрыва не было, а правка
# снимает ЗАВИСИМОСТЬ от чужого парсера. Тесты ниже пиньят обе стороны И этот контракт suggest:
# сломается он — покраснеет здесь, а не в тишине сайдкара.

class TestLessonSourceSurvivesBookFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.side = os.path.join(self.tmp, "trainer_rules.json")
        self.pb = os.path.join(self.tmp, "playbook.md")
        self._old_pb = suggest.PLAYBOOK_FILE
        self._old_side = trainer.TRAINER_RULES_FILE
        suggest.PLAYBOOK_FILE = self.pb
        trainer.TRAINER_RULES_FILE = self.side

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._old_pb
        trainer.TRAINER_RULES_FILE = self._old_side

    def test_source_found_by_rule_text_as_book_stores_it(self):
        """Определение источника находит его по тексту, КАКИМ ЕГО ХРАНИТ КНИГА (с датой)."""
        remark = "не переспрашивай даты, которые клиент уже назвал"
        trainer.mark_source(remark)                                  # ставится СЫРЫМ текстом
        self.assertEqual(trainer.rule_source(remark), trainer.TRAINER_SOURCE)
        # ровно те формы, в которых текст приходит из книги
        self.assertEqual(trainer.rule_source("(2026-07-22) " + remark), trainer.TRAINER_SOURCE)
        self.assertEqual(trainer.rule_source("- (2026-07-22) " + remark), trainer.TRAINER_SOURCE)
        self.assertTrue(trainer.is_trainer_rule("(2026-07-22) " + remark))

    def test_book_bullets_gain_source_after_fix(self):
        """ЧИСЛО на СЫРЫХ строках книги — той стороне, где правка и меняет исход.
        Книга-фикстура ОБЯЗАТЕЛЬНО смешанная: буллеты С датой и БЕЗ даты. Именно на вторых
        ломается «подгонка под один префикс» — они обязаны сходиться так же (замечание
        предсмертного взгляда 04.09). Пометки лежат сырым текстом — как их кладёт mark_source."""
        dated = ["правило альфа один", "правило бета два"]
        plain = ["правило дельта без даты", "правило эпсилон без даты"]
        for r in (dated[0], plain[0]):                    # источник есть ровно у половины каждой пары
            trainer.mark_source(r)
        text = ("# playbook\n\n## Выученные правила\n"
                + "".join("- (2026-07-22) %s\n" % r for r in dated)
                + "".join("- %s\n" % r for r in plain))   # ← буллеты БЕЗ префикса даты
        with open(self.pb, "w", encoding="utf-8") as f:
            f.write(text)
        raw = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")]
        self.assertEqual(len(raw), 4)
        found = sum(1 for b in raw if trainer.rule_source(b))
        self.assertEqual(found, 2)                        # ПОСЛЕ: 2 из 4 (ДО было 0 из 4)
        # и поимённо — чтобы «2 из 4» нельзя было набрать не теми двумя
        self.assertEqual(trainer.rule_source("- (2026-07-22) " + dated[0]), trainer.TRAINER_SOURCE)
        self.assertEqual(trainer.rule_source("- " + plain[0]), trainer.TRAINER_SOURCE)
        self.assertIsNone(trainer.rule_source("- (2026-07-22) " + dated[1]))
        self.assertIsNone(trainer.rule_source("- " + plain[1]))

    def test_undated_book_bullet_key_is_unchanged_by_fix(self):
        """Буллет БЕЗ даты правка не трогает вовсе: обе регулярки холостые, кроме дефиса.
        Это защита от «подогнали под префикс и сломали остальное»."""
        for s in ("правило без даты", "1. пункт с номером", "«кавычки» в начале"):
            self.assertEqual(trainer._norm_rule(s), " ".join(s.split()).lower())
            self.assertEqual(trainer._norm_rule("- " + s), " ".join(s.split()).lower())

    def test_cancel_unmarks_when_unmark_gets_book_text(self):
        """Снятие пометки срабатывает, даже если на снятие пришёл буллет книги С ДАТОЙ.
        ЧЕСТНО: боевой suggest.remove_playbook_rule дату снимает САМ, поэтому сегодня сюда
        такой текст не приходит и сироты на живом пути не было (замер 04.09: cancel через
        живой suggest-путь оставлял 0 записей и ДО правки). Тест держит КОНТРАКТ на будущее —
        remove_rule здесь нарочно отдаёт текст с датой."""
        remark = "предлагай доставку явно в первом ответе"
        trainer.mark_source(remark)
        self.assertTrue(trainer.is_trainer_rule(remark))
        les = lesson_store.Lesson(1, "вопрос", "ответ", remark, "причина", "filipp",
                                  "2026-09-06T10:00:00Z", lesson_store.STATE_ACTIVE, 2)
        dec = trainer.cancel_lesson(
            1, who="filipp", may_write=lambda _u: True,
            find=lambda _n, _p=None: (les, True),
            withdraw=lambda *_a, **_k: lesson_store.WithdrawResult(
                lesson_store.CUT_ONE, 1, (1,), (), 2, 2),
            list_rules=lambda: [{"n": 1, "date": "2026-07-22", "rule": remark}],
            remove_rule=lambda n: {"status": "removed", "n": 1,
                                   "rule": "(2026-07-22) " + remark, "remaining": 0},
            regress=lambda *_a, **_k: {"spawned": False})
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertFalse(trainer.is_trainer_rule(remark))             # пометка СНЯТА
        self.assertEqual(json.load(open(self.side, encoding="utf-8")), {})   # сироты не осталось

    def test_old_sidecar_format_still_read_after_fix(self):
        """Уже лежащие записи НЕ ПОТЕРЯНЫ: старый сайдкар писался сырым текстом урока, и на
        тексте без даты новая нормализация тождественна прежней — ключ тот же."""
        old = {"не дублируй название модели — одно упоминание модели на строку": "тренажёр",
               "2 раза вопрос про даты в одном сообщении не пишем": "тренажёр"}
        with open(self.side, "w", encoding="utf-8") as f:
            json.dump(old, f, ensure_ascii=False, indent=0)
        for k in old:                                                 # находится и как раньше…
            self.assertEqual(trainer.rule_source(k), trainer.TRAINER_SOURCE)
            self.assertEqual(trainer.rule_source(k.upper()), trainer.TRAINER_SOURCE)
            # …и в форме книги, ради которой правка и делалась
            self.assertEqual(trainer.rule_source("- (2026-07-22) " + k), trainer.TRAINER_SOURCE)

    def test_norm_rule_strips_only_leading_date_prefix(self):
        """Дата ВНУТРИ правила — часть текста, её не трогаем: снимается только ВЕДУЩИЙ префикс."""
        self.assertEqual(trainer._norm_rule("(2026-07-22) текст"), "текст")
        self.assertEqual(trainer._norm_rule("- (2026-07-22)   текст"), "текст")
        self.assertEqual(trainer._norm_rule("текст (2026-07-22)"), "текст (2026-07-22)")
        self.assertEqual(trainer._norm_rule("(в скобках) текст"), "(в скобках) текст")
        self.assertEqual(trainer._norm_rule(None), "")

    def test_live_apply_then_cancel_leaves_no_orphan(self):
        """БОЕВОЙ путь целиком, живыми suggest.append/remove: урок → книга → отмена → сайдкар
        ПУСТ. Здесь снятие пометки и проверяется по-настоящему (остальные тесты класса подают
        текст руками). classify инъектируем — боевой роутер ходит в LLM, а мерим не его.

        С 06.09.2026 отмена идёт ОТЗЫВОМ В БАЗЕ, поэтому у теста появилась своя таблица уроков:
        буллет книги команда правит ЗАОДНО и по ТОЧНОМУ тексту снятого урока — на этом стыке
        сирота и появлялась бы, если бы текст в книгу и в базу лёг разный."""
        with open(self.pb, "w", encoding="utf-8") as f:
            f.write("# playbook\n\n## Выученные правила\n")
        store = os.path.join(self.tmp, "lesson_store.tsv")
        self.addCleanup(setattr, lesson_store, "STORE_PATH", lesson_store.STORE_PATH)
        lesson_store.STORE_PATH = store
        remark = "предлагай доставку явно в первом ответе"
        res = trainer.apply_lesson(remark, classify=lambda r: "behavior",
                                   append_rule=suggest.append_playbook_rule)   # старый синк книги
        self.assertEqual(res["status"], "added")
        self.assertTrue(trainer.is_trainer_rule(remark))                 # пометка легла
        rows = suggest.list_playbook_rules(open(self.pb, encoding="utf-8").read())
        self.assertEqual(len(rows), 1)
        self.assertEqual(trainer.rule_source(rows[0]["rule"]), trainer.TRAINER_SOURCE)
        n = lesson_store.add(question="вопрос", bot_answer="ответ", correct=remark,
                             why="владелец назвал причину", who="filipp",
                             when="2026-09-06T10:00:00Z", path=store,
                             source=lesson_store.SOURCE_TRAINER)
        dec = trainer.cancel_lesson(n, who="filipp", may_write=lambda _u: True, path=store,
                                    regress=lambda *_a, **_k: {"spawned": False})
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(dec["book"]["status"], "removed", dec["book"])   # буллет книги убран
        self.assertEqual(json.load(open(self.side, encoding="utf-8")), {})   # сироты нет

    def test_remove_playbook_rule_still_strips_date_itself(self):
        """КОНТРАКТ suggest, на котором держалась пометка ДО правки: remove_playbook_rule
        отдаёт тело БЕЗ даты. Правка сделала пометку независимой от него, но если контракт
        поедет — пусть краснеет тест, а не молчит сайдкар."""
        with open(self.pb, "w", encoding="utf-8") as f:
            f.write("# playbook\n\n## Выученные правила\n- (2026-07-22) правило про доставку\n")
        res = suggest.remove_playbook_rule(1)
        self.assertEqual(res["status"], "removed")
        self.assertEqual(res["rule"], "правило про доставку")            # дата снята САМИМ suggest

    def test_norm_rule_date_shape_mirrors_suggest_parser(self):
        """Форма префикса ОБЯЗАНА совпадать с той, что снимает парсер книги (suggest), иначе
        ключи снова разъедутся. Обе — по ФОРМЕ \\d{4}-\\d{2}-\\d{2}, а не по календарю: 13-й
        месяц книга тоже сняла бы, и пометка обязана вести себя так же."""
        self.assertEqual(trainer._norm_rule("(2026-13-99) текст"), "текст")   # как и suggest
        self.assertEqual(suggest.list_playbook_rules(
            "## Выученные правила\n- (2026-13-99) текст\n")[0]["rule"], "текст")


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
    """Кнопки-ТУМБЛЕРЫ: номер отмечается ✅ прямо на кнопке, запись — по «✔ Применить»."""

    def _flat(self, kb):
        return [b for row in kb.inline_keyboard for b in row]

    def test_buttons_are_numbers_and_carry_index(self):
        import moderation_bot
        hyps = [LONG_HYP, "б", "в", "г", "д", "е"]
        flat = self._flat(moderation_bot._kb_trainer_hyps(hyps))
        self.assertEqual([b.text for b in flat[:6]], ["1", "2", "3", "4", "5", "6"])
        self.assertEqual([b.callback_data for b in flat[:6]],
                         [f"tr:hyp:{i}" for i in range(6)])     # индекс, а не текст — маппинг надёжен
        self.assertEqual([b.callback_data for b in flat[6:]],
                         ["tr:hyp:apply", "tr:hyp:cancel", "tr:hyp:other"])
        self.assertIn("Применить", flat[6].text)
        self.assertIn("Отмена", flat[7].text)
        self.assertIn("другое", flat[8].text)
        self.assertTrue(all(len(row) <= 5 for row in kb_rows(moderation_bot, hyps)))

    def test_selected_numbers_are_marked_on_the_button(self):
        import moderation_bot
        hyps = ["а", "б", "в"]
        flat = self._flat(moderation_bot._kb_trainer_hyps(hyps, [0, 2]))
        self.assertEqual([b.text for b in flat[:3]], ["✅1", "2", "✅3"])
        # callback_data не меняется от отметки — маршрутизация тумблера стабильна
        self.assertEqual([b.callback_data for b in flat[:3]],
                         ["tr:hyp:0", "tr:hyp:1", "tr:hyp:2"])
        self.assertEqual([b.text for b in self._flat(
            moderation_bot._kb_trainer_hyps(hyps, []))[:3]], ["1", "2", "3"])

    def test_n_not_four(self):
        import moderation_bot
        for n in (2, 3, 5):
            flat = self._flat(moderation_bot._kb_trainer_hyps([f"г{i}" for i in range(n)]))
            self.assertEqual(len(flat), n + 3)                  # N номеров + Применить/Отмена/другое
            self.assertEqual([b.text for b in flat[:n]], [str(i + 1) for i in range(n)])


def kb_rows(moderation_bot, hyps):
    """Ряды клавиатуры БЕЗ служебных (проверяем ширину только у рядов с номерами)."""
    return moderation_bot._kb_trainer_hyps(hyps).inline_keyboard[:-2]


class TestMultiSelectLessons(unittest.TestCase):
    """ТЗ п.7: тумблеры + «Применить»; КАЖДАЯ отмеченная гипотеза — ОТДЕЛЬНОЕ правило со своим
    номером (совместимо с «отмени урок N»), подтверждение — ОДНИМ сообщением."""

    def test_toggle_is_pure_and_idempotent(self):
        self.assertEqual(trainer._toggle([], 2), [2])
        self.assertEqual(trainer._toggle([2], 2), [])
        self.assertEqual(trainer._toggle([2, 0], 1), [0, 1, 2])   # всегда отсортирован
        self.assertEqual(trainer._toggle([0, 1, 2], 1), [0, 2])

    def test_selection_roundtrip_and_reset_on_new_hyps(self):
        d, get, set = _dict_store()
        trainer.set_hyps(["а", "б", "в"], set=set)
        self.assertEqual(trainer.toggle_selection(0, get, set), [0])
        self.assertEqual(trainer.toggle_selection(2, get, set), [0, 2])
        self.assertEqual(trainer.selected_hypotheses(get), ["а", "в"])
        self.assertEqual(trainer.toggle_selection(0, get, set), [2])
        trainer.set_hyps(["новые"], set=set)                      # новая выдача → отметки сброшены
        self.assertEqual(trainer.get_selection(get), [])

    def test_selection_ignores_stale_indices(self):
        d, get, set = _dict_store()
        trainer.set_hyps(["а", "б"], set=set)
        trainer.set_selection([0, 5], set)                        # 5 — устаревший индекс
        self.assertEqual(trainer.selected_hypotheses(get), ["а"])
        set(trainer.K_HYP_SEL, "не json")                         # битьё → пусто, не падаем
        self.assertEqual(trainer.get_selection(get), [])

    def test_each_selected_hypothesis_becomes_its_own_rule(self):
        added = []
        rules = [{"n": 1, "rule": "первое"}, {"n": 2, "rule": "второе"}]
        dec = trainer.apply_lessons(
            ["первое", "второе"],
            append_rule=lambda r: added.append(r) or "added",
            classify=lambda r: "behavior", mark=lambda r: True,
            list_rules=lambda: rules)
        self.assertEqual(added, ["первое", "второе"])             # ДВА отдельных правила
        self.assertEqual(dec["accepted"], [(1, "первое"), (2, "второе")])
        self.assertEqual(dec["card"].count("✅"), 1)               # ОДНО подтверждение, не два
        self.assertIn("#1 — первое", dec["card"])
        self.assertIn("#2 — второе", dec["card"])

    def test_apply_lessons_reports_code_duplicate_and_empty(self):
        dec = trainer.apply_lessons(["почини парсер дат"],
                                    append_rule=lambda r: "added",
                                    classify=lambda r: "code", list_rules=lambda: [])
        self.assertEqual(dec["code"], ["почини парсер дат"])
        self.assertEqual(dec["accepted"], [])
        self.assertIn("код-фикс", dec["card"])
        dup = trainer.apply_lessons(["уже было"], append_rule=lambda r: "duplicate",
                                    classify=lambda r: "behavior", list_rules=lambda: [])
        self.assertEqual(dup["duplicates"], ["уже было"])
        empty = trainer.apply_lessons([], list_rules=lambda: [])
        self.assertIn("Ничего не отмечено", empty["card"])
        # сбой книги правил не роняет карточку (fail-safe)
        boom = trainer.apply_lessons(["x"], append_rule=lambda r: "added",
                                     classify=lambda r: "behavior", mark=lambda r: True,
                                     list_rules=lambda: (_ for _ in ()).throw(RuntimeError("нет")))
        self.assertIn("x", boom["card"])


class TestPendingFreeTextLesson(unittest.TestCase):
    """ТЗ п.8: «✍ другое» БЕЗ ПРЕФИКСА — ждём следующее сообщение владельца, TTL 10 минут."""

    def test_pending_lifecycle_and_ttl(self):
        d, get, set = _dict_store()
        trainer.start_pending_lesson("mike", now=1000, set=set)
        self.assertEqual(trainer.pending_lesson(now=1000, get=get), "mike")
        self.assertEqual(trainer.pending_lesson(now=1000 + 599, get=get), "mike")
        self.assertIsNone(trainer.pending_lesson(now=1000 + 601, get=get))   # TTL 10 мин истёк
        self.assertIsNone(trainer.pending_lesson(now=1000, get=lambda k: ""))

    def test_take_is_once_and_owner_scoped(self):
        d, get, set = _dict_store()
        trainer.start_pending_lesson("mike", now=1000, set=set)
        self.assertFalse(trainer.take_pending_lesson("someone", now=1000, get=get, set=set))
        self.assertTrue(trainer.take_pending_lesson("@mike", now=1000, get=get, set=set))
        self.assertFalse(trainer.take_pending_lesson("mike", now=1000, get=get, set=set))  # уже забрали

    def test_reset_clears_pending_and_selection(self):
        d, get, set = _dict_store()
        trainer.set_hyps(["а", "б"], set=set)
        trainer.toggle_selection(1, get, set)
        trainer.start_pending_lesson("mike", now=1000, set=set)
        trainer.reset(get, set)
        self.assertEqual(trainer.get_selection(get), [])
        self.assertIsNone(trainer.pending_lesson(now=1000, get=get))

    def test_wiring_userbot_catches_pending_text(self):
        """Проводка (userbot_listen.py импортить в тестах нельзя — Telethon и живая сессия):
        проверяем ПО ИСХОДНИКУ, что ловец свободного текста реально подключён и что ловит его
        именно userbot (в группе он видит ВСЕ сообщения, модербот — не обязательно)."""
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "userbot_listen.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("trainer.pending_lesson()", src)
        self.assertIn("trainer.take_pending_lesson(username)", src)
        self.assertIn("trainer.apply_lesson, text.strip()", src)   # ДОСЛОВНО, без префикса
        with open(os.path.join(here, "moderation_bot.py"), encoding="utf-8") as f:
            mb = f.read()
        self.assertIn("trainer.start_pending_lesson(username)", mb)  # кнопка ставит ожидание
        self.assertIn("trainer.apply_lessons", mb)                   # «Применить» пишет пачкой
        self.assertIn("trainer.toggle_selection", mb)                # номер — тумблер

    def test_free_text_is_saved_verbatim(self):
        # ДОСЛОВНО: опечатки и разговорную форму (голосовой ввод) НЕ правим.
        said = "не надо повторять про доствку в кажном ответе ага"
        added = []
        dec = trainer.apply_lesson(said, append_rule=lambda r: added.append(r) or "added",
                                   classify=lambda r: "behavior", mark=lambda r: True)
        self.assertEqual(added, [said])
        self.assertIn(said, dec["card"])


class TestHypsTapMapping(unittest.IsolatedAsyncioTestCase):
    """Тап по номеру ОТМЕЧАЕТ именно ту гипотезу; запись — только по «✔ Применить»."""

    def setUp(self):
        import moderation_bot
        self.mb = moderation_bot
        moderation_ipc.init_db()
        self._old_appr = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = {"mike"}
        self._old_apply = trainer.apply_lesson
        self._old_applies = trainer.apply_lessons
        self.applied = []
        trainer.apply_lesson = lambda remark: (self.applied.append(remark)
                                               or {"card": f"✅ урок: {remark}"})
        trainer.apply_lessons = lambda remarks, **kw: (self.applied.extend(remarks)
                                                       or {"card": "✅ Принято уроков: "
                                                                   f"{len(remarks)}"})
        trainer.set_selection([])

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._old_appr
        trainer.apply_lesson = self._old_apply
        trainer.apply_lessons = self._old_applies
        trainer.set_selection([])
        trainer.clear_pending_lesson()

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
                self.edits = []

            async def edit_message_reply_markup(self, reply_markup=None):
                self.edits.append(reply_markup)

        return Ctx(), Q(username, chat_id), sent

    async def test_tap_number_only_marks_and_writes_nothing(self):
        trainer.set_hyps(["первая", LONG_HYP, "третья"])
        ctx, q, sent = self._fakes()
        await self.mb._trainer_callback(ctx, q, "tr:hyp:1")     # кнопка «2» → индекс 1
        self.assertEqual(self.applied, [])                      # тап НЕ пишет правило
        self.assertEqual(trainer.get_selection(), [1])          # он ставит отметку
        self.assertEqual(trainer.selected_hypotheses(), [LONG_HYP])
        marks = [b.text for row in q.edits[-1].inline_keyboard for b in row][:3]
        self.assertEqual(marks, ["1", "✅2", "3"])               # ✅ видно прямо на кнопке
        await self.mb._trainer_callback(ctx, q, "tr:hyp:1")     # повторный тап снимает
        self.assertEqual(trainer.get_selection(), [])

    async def test_apply_writes_all_marked_in_one_message(self):
        trainer.set_hyps(["первая", LONG_HYP, "третья"])
        ctx, q, sent = self._fakes()
        await self.mb._trainer_callback(ctx, q, "tr:hyp:0")
        await self.mb._trainer_callback(ctx, q, "tr:hyp:2")
        self.assertEqual(self.applied, [])
        await self.mb._trainer_callback(ctx, q, "tr:hyp:apply")
        self.assertEqual(self.applied, ["первая", "третья"])    # обе, каждая отдельным правилом
        self.assertEqual(len([t for _, t, _ in sent if "Принято уроков" in t]), 1)  # ОДНО сообщение
        self.assertEqual(trainer.get_selection(), [])           # отметки сняты после применения

    async def test_cancel_drops_marks_and_writes_nothing(self):
        trainer.set_hyps(["первая", "вторая"])
        ctx, q, sent = self._fakes()
        await self.mb._trainer_callback(ctx, q, "tr:hyp:0")
        await self.mb._trainer_callback(ctx, q, "tr:hyp:cancel")
        self.assertEqual(self.applied, [])
        self.assertEqual(trainer.get_selection(), [])
        self.assertTrue(any("Отменено" in t for _, t, _ in sent))

    async def test_other_button_starts_pending_without_prefix(self):
        ctx, q, sent = self._fakes()
        await self.mb._trainer_callback(ctx, q, "tr:hyp:other")
        self.assertEqual(self.applied, [])
        self.assertEqual(trainer.pending_lesson(), "mike")      # ждём следующий текст владельца
        body = "\n".join(t for _, t, _ in sent)
        self.assertIn("СЛЕДУЮЩИМ сообщением", body)
        self.assertIn("не нужен", body)                         # префикс «урок:» больше не требуем
        self.assertNotIn("Напиши правило текстом", body)        # прежней инструкции-префикса нет

    async def test_non_approver_cannot_toggle_or_apply(self):
        trainer.set_hyps(["первая"])
        ctx, q, sent = self._fakes(username="stranger")
        await self.mb._trainer_callback(ctx, q, "tr:hyp:0")
        await self.mb._trainer_callback(ctx, q, "tr:hyp:apply")
        self.assertEqual(self.applied, [])
        self.assertEqual(trainer.get_selection(), [])
        self.assertTrue(all("только для approver" in t for _, t, _ in sent))

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
        self.assertEqual([b.text for b in flat],
                         ["1", "2", "3", "✔ Применить", "✖ Отмена", "✍️ другое"])
        # тап по номеру «2» из этой же выдачи → отмечена та самая длинная гипотеза
        await self.mb._trainer_callback(ctx, q, flat[1].callback_data)
        self.assertEqual(trainer.selected_hypotheses(), [LONG_HYP])


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

    # «СЕГОДНЯ» У ЭТОГО ГОЛДЕНА ЗАКРЕПЛЕНО (02.08.2026). Реплика клиента называет АБСОЛЮТНУЮ дату,
    # и 02.08 старт «1 августа» стал прошлым: прошедший старт `collected_facts` собранной датой
    # СОЗНАТЕЛЬНО не считает (гейт как раз просит его уточнить) — то есть тест краснел от смены
    # СУТОК, а не от смены кода, и валил весь гейт. Лечим не фразой и не кодом контура: фраза
    # клиента остаётся ДОСЛОВНОЙ (правило голденов детекта), а закрепляется ВТОРОЙ вход обеих
    # функций — `today`, он для того и заведён. Проверяемое здесь — КУМУЛЯТИВНОСТЬ окна, к
    # календарю отношения не имеющая.
    TODAY = datetime.date(2026, 7, 25)      # любой день ДО старта из реплики; сам он не проверяется

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
        f = suggest.collected_facts(t, today=self.TODAY)
        self.assertTrue(f["dates"])          # даты из первого события
        self.assertTrue(f["term"])
        self.assertTrue(f["geo"])            # гео из второго — В ТОМ ЖЕ окне
        self.assertEqual(suggest.extract_booking_hints(t, today=self.TODAY).get("geo_pin"),
                         (7.771, 98.327))

    def test_why_today_is_pinned_and_that_it_stays_pinned(self):
        """Страж протухания. Показывает МЕХАНИЗМ: тот же вход при разном `today` даёт РАЗНЫЙ ответ
        по датам (прошедший старт собранной датой не считается — это правильное поведение контура,
        трогать его нечего), значит незакреплённый голден мерил календарь машины. И сторожит сам
        пин: уберут `today=` обратно — тест упадёт СРАЗУ, а не через сутки на чужом гейте."""
        t = trainer.append_turn("", "client",
                                trainer.client_body("аренда с 1 по 8 августа на 7 дней"))
        before = suggest.collected_facts(t, today=datetime.date(2026, 7, 25))
        after = suggest.collected_facts(t, today=datetime.date(2026, 8, 9))
        self.assertTrue(before["dates"])
        self.assertFalse(after["dates"], "прошедший старт собранной датой не считается")
        self.assertTrue(after["term"], "длительность прошедший старт не отменяет")
        src = inspect.getsource(TestTrainerAccumulation.test_window_is_cumulative_across_events)
        self.assertIn("today=self.TODAY", src, "закрепление «сегодня» отвинтили назад")


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


# ═══ УРОК ОСИ «КОД» → ЗАЯВКА В ОЧЕРЕДЬ КНОПКОЙ ВЛАДЕЛЬЦА (04.09.2026) ════════
#
# Что здесь доказывается и почему именно это. Исход «код» был ТУПИКОМ: карточка со
# строкой «Готовый текст задачи: …» и конец ветки. Правка делает его заявкой в
# очередь ПК, и у такой правки ровно три способа сгнить молча:
#
#   1. запреты и форму адреса напишут в тренажёре СВОИМИ словами — и через месяц
#      ворота ящика начнут отвергать всё, что тренажёр собирает (предсмертный
#      взгляд задания, названный дословно);
#   2. появится ветка, ставящая задачу БЕЗ «да» владельца;
#   3. замечание, из которого канонного ТЗ не собирается, даст карточку с
#      полупустым телом вместо отказа словами.
#
# Против каждого — свой замок ниже, и все три судят ДЕЙСТВИЕ (живой вызов чужих
# ворот, обход AST, реальная сборка), а не подстроку в комментарии.

class TestCodeFixClaimBuild(unittest.TestCase):
    """Сборка ТЗ: три обязательные вещи в теле + ЖИВОЙ прогон через чужие ворота."""

    INC = "Какие марки и модели есть и какие цены на аренду на месяц?"
    ANS = "Здравствуйте! У нас есть скутеры. Цены уточните у менеджера."
    REM = "бот не подставил помесячный тариф из прайса, хотя клиент прямо спросил цену на месяц"

    def _built(self, **kw):
        kw.setdefault("day", "04.09")
        kw.setdefault("n", 7)
        return trainer.build_claim(kw.pop("remark", self.REM), kw.pop("incoming", self.INC),
                                   kw.pop("answer", self.ANS), **kw)

    def test_body_carries_the_three_required_things(self):
        import shtab_box
        built = self._built()
        self.assertTrue(built["ok"], built["why"])
        text = built["text"]
        # 1) запреты — ДОСЛОВНО из ящика, а не пересказом
        self.assertIn(shtab_box.PROHIBITIONS, text)
        # 2) премиса: ТОТ САМЫЙ ответ бота и ТОТ САМЫЙ вопрос клиента
        self.assertIn(self.INC, text)
        self.assertIn(self.ANS, text)
        self.assertIn(self.REM, text)
        # 3) адрес — той формой, которую читает СУДЬЯ ЗАКРЫТИЯ (спрашиваем его самого)
        import done_judge_pc
        addr = done_judge_pc.read_address(text)
        self.assertIsNotNone(addr, "судья закрытия адрес в собранном ТЗ не прочитал")
        self.assertTrue(addr["words"], "адрес без слов — доказывать им нечего")
        self.assertEqual((addr["day"], addr["month"]), (4, 9))

    def test_passes_the_foreign_gates_of_the_box(self):
        """ЧУЖИЕ ворота, а не наша копия той же формы."""
        import shtab_box
        built = self._built()
        ok, reason, why = shtab_box.check({"key": built["key"], "body": built["text"]})
        self.assertTrue(ok, "%s: %s" % (reason, why))
        self.assertTrue(shtab_box.KEY_RE.match(built["key"]), built["key"])
        lane = shtab_box.read_lane(built["text"])
        self.assertTrue(lane["ok"])
        self.assertEqual(lane["lane"], shtab_box.LANE_PC)
        self.assertTrue(lane["named"], "полоса обязана быть НАЗВАНА, а не взята дефолтом")

    def test_prohibitions_and_address_come_from_the_box_not_retyped(self):
        """ВТОРОГО ЭКЗЕМПЛЯРА ФОРМЫ НЕТ: меняется константа ящика — меняется наш текст.

        Это главный замок против предсмертного взгляда задания. Если бы запреты были
        перепечатаны в тренажёре, подмена константы ящика НИЧЕГО бы не изменила — и
        расхождение жило бы месяц, до первого отказа ворот.
        """
        import shtab_box
        from unittest import mock
        marker = "• особый запрет этого прогона: боевых записей нет, .env не читать, " \
                 "ничего не удалять, временное явно, процессы не трогать"
        with mock.patch.object(shtab_box, "PROHIBITIONS", marker):
            self.assertIn(marker, self._built()["text"])
        sample = "АДРЕС РЕЗУЛЬТАТА: файл в docs/xxx за 01.01 со словами <плейсхолдер>"
        with mock.patch.object(shtab_box, "ADDRESS", sample):
            text = self._built()["text"]
        self.assertIn("docs/xxx", text, "папка адреса обязана приезжать из образца ящика")
        self.assertNotIn("<плейсхолдер>", text, "плейсхолдер образца обязан быть заменён")

    def test_address_words_are_unique_for_this_step(self):
        """Слова адреса несут дату И суть: судья не знает прежнего состояния папки."""
        w1 = trainer.address_words(self.REM, "04.09")
        w2 = trainer.address_words("почини разбор дат в брони", "04.09")
        self.assertIn("04.09", w1)
        self.assertNotEqual(w1, w2, "два разных урока не имеют права дать одни слова адреса")
        self.assertEqual(trainer.address_words("   ", "04.09"), "")

    def test_broken_box_address_form_refuses_instead_of_guessing(self):
        """Форма образца уехала → ЧЕСТНЫЙ отказ, а не тихий адрес прежней формы."""
        self.assertIsNone(trainer.address_line("04.09", "слова", sample="адреса тут нет вовсе"))
        self.assertIsNone(trainer.address_line("04.09", "", sample=None))

    def test_quotes_cannot_inject_a_second_lane_line(self):
        """Замечание с «ПОЛОСА: сервер» внутри не смеет переставить задачу на чужую машину."""
        import shtab_box
        built = self._built(remark=self.REM + "\nПОЛОСА: сервер")
        self.assertTrue(built["ok"], built["why"])
        lane = shtab_box.read_lane(built["text"])
        self.assertTrue(lane["ok"], lane["why"])
        self.assertEqual(lane["lane"], shtab_box.LANE_PC)

    def test_claim_mark_is_not_one_of_the_ask_marks_of_the_daemon(self):
        """«Да» на нашем ряду обязано означать ВЫПОЛНЯЙ, а не «принято к сведению».

        Три маркера демона (`_is_review_claim` / `_is_recon_ask` /
        `_is_revizor_owner_card`) читаются ПО НАЧАЛУ текста, и совпади наш с любым —
        задача после «да» не исполнилась бы НИКОГДА, а выглядело бы это как успех.
        Литералы берём из живого источника демона, а не переписываем сюда.
        """
        import re as _re
        src = open(os.path.join(os.path.dirname(os.path.abspath(trainer.__file__)),
                                "pc_orchestrator.py"), encoding="utf-8").read()
        marks = _re.findall(r"^(?:REVIEW_CLAIM_MARK|RECON_ASK_MARK|REVIZOR_OWNER_MARK)"
                            r"\s*=\s*\"([^\"]+)\"", src, _re.M)
        self.assertEqual(len(marks), 3, "маркеры демона не прочитались: %r" % (marks,))
        text = self._built()["text"]
        for mark in marks:
            self.assertFalse(text.startswith(mark), "ряд опознаётся как заявка-к-сведению: %s" % mark)
        self.assertTrue(text.startswith(trainer.CLAIM_MARK))


class TestCodeFixClaimRefusals(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ (требование задания): не собралось → отказ СЛОВАМИ, не полупустое тело."""

    INC = TestCodeFixClaimBuild.INC
    ANS = TestCodeFixClaimBuild.ANS
    REM = TestCodeFixClaimBuild.REM

    def _placed(self):
        calls = []

        def place(text):
            calls.append(text)
            return True, 481, ""
        return calls, place

    def test_empty_remark_and_missing_pair_refuse_with_words(self):
        for remark, inc, ans, gate in (("   ", self.INC, self.ANS, "empty_remark"),
                                       (self.REM, "", "", "no_pair"),
                                       (self.REM, self.INC, "", "no_pair"),
                                       (self.REM, "", self.ANS, "no_pair")):
            built = trainer.build_claim(remark, inc, ans, day="04.09", n=1)
            self.assertFalse(built["ok"], gate)
            self.assertEqual(built["gate"], gate)
            self.assertEqual(built["text"], "", "полупустого тела быть не должно ВОВСЕ")
            self.assertGreater(len(built["why"]), 30, "отказ обязан быть СЛОВАМИ: %r" % built["why"])

    def test_refusal_never_places_anything(self):
        calls, place = self._placed()
        got = trainer.code_fix_claim("   ", incoming=self.INC, answer=self.ANS, place=place)
        self.assertFalse(got["placed"])
        self.assertEqual(calls, [], "при отказе в очередь не уходит НИЧЕГО")
        self.assertIn(trainer.CODE_CARD_MARK, got["card"])
        self.assertIn("собрать НЕ УДАЛОСЬ", got["card"])
        self.assertIn(got["why"][:20], got["card"], "причина обязана доехать до владельца")

    def test_foreign_gate_refusal_reaches_the_owner_in_its_own_words(self):
        """Ворота ящика не пропустили → карточки с кнопкой НЕТ, а есть ЧУЖАЯ причина словами."""
        import shtab_box
        from unittest import mock
        calls, place = self._placed()
        # запреты, в которых не названо ничего, — ворота обязаны сказать, ЧЕГО не хватает
        with mock.patch.object(shtab_box, "PROHIBITIONS", "будь молодцом"):
            got = trainer.code_fix_claim(self.REM, incoming=self.INC, answer=self.ANS, place=place)
        self.assertFalse(got["placed"])
        self.assertEqual(got["gate"], "no_prohibitions")
        self.assertEqual(calls, [])
        self.assertIn("ворота приёма ящика", got["card"])
        self.assertIn("не названы запреты", got["card"])

    def test_pair_missing_is_a_refusal_and_not_a_task_about_nothing(self):
        """Живой путь: пары нет в сессии → отказ, и в очередь не ушло ничего."""
        calls, place = self._placed()
        got = trainer.code_fix_claim(self.REM, place=place, pair=lambda: ("", ""))
        self.assertFalse(got["placed"])
        self.assertEqual(calls, [])
        self.assertIn("Напиши как клиент", got["card"])


class TestCodeFixClaimPlacement(unittest.TestCase):
    """Карточка с кнопкой: без «да» не встаёт ничего, и владелец видит, ЧТО именно встанет."""

    INC = TestCodeFixClaimBuild.INC
    ANS = TestCodeFixClaimBuild.ANS
    REM = TestCodeFixClaimBuild.REM

    def test_places_ask_and_card_shows_what_will_run(self):
        seen = []

        def place(text):
            seen.append(text)
            return True, 512, ""
        got = trainer.code_fix_claim(self.REM, incoming=self.INC, answer=self.ANS, place=place)
        self.assertTrue(got["placed"])
        self.assertEqual(got["tid"], 512)
        self.assertEqual(len(seen), 1)
        self.assertIn(self.ANS, seen[0], "в очередь обязан уехать ТОТ САМЫЙ плохой ответ")
        self.assertIn("заявка #512", got["card"])
        self.assertIn("«да 512»", got["card"])
        self.assertIn("«нет 512»", got["card"])
        self.assertIn(trainer.CODE_CARD_MARK, got["card"])

    def test_place_failure_keeps_the_text_visible_to_the_owner(self):
        got = trainer.code_fix_claim(self.REM, incoming=self.INC, answer=self.ANS,
                                     place=lambda t: (False, None, "мост не ответил"))
        self.assertFalse(got["placed"])
        self.assertIn("мост не ответил", got["card"])
        self.assertIn("ЗАПРЕТЫ", got["card"], "текст задачи не теряется — владелец несёт руками")

    def test_apply_lesson_code_axis_goes_through_the_claim(self):
        """Живая дорога урока: axis=code → заявка, а не тупик со строкой «готовый текст»."""
        from unittest import mock
        with mock.patch.object(trainer, "code_fix_claim",
                               return_value={"placed": True, "tid": 77, "why": "",
                                             "card": "🛠 Нужен код-фикс — заявка #77"}) as spy:
            dec = trainer.apply_lesson("почини парсер дат", append_rule=lambda r: "added",
                                       classify=lambda r: "code", mark=lambda r: None)
        self.assertEqual(dec["axis"], "code")
        self.assertTrue(dec["placed"])
        self.assertEqual(dec["tid"], 77)
        self.assertEqual(spy.call_count, 1)

    def test_apply_lessons_card_names_the_outcome_of_every_code_lesson(self):
        from unittest import mock
        with mock.patch.object(trainer, "code_fix_claim",
                               return_value={"placed": False, "tid": None,
                                             "why": "мост молчит", "card": "🛠 …"}):
            dec = trainer.apply_lessons(["почини парсер дат"], append_rule=lambda r: "added",
                                        classify=lambda r: "code", list_rules=lambda: [])
        self.assertEqual(dec["code"], ["почини парсер дат"])
        self.assertIn("код-фикс", dec["card"])
        self.assertIn("в очередь НЕ встала: мост молчит", dec["card"])

    def test_crash_inside_never_raises_and_still_says_code_fix(self):
        from unittest import mock
        with mock.patch.object(trainer, "build_claim", side_effect=RuntimeError("бум")):
            got = trainer.code_fix_claim(self.REM, incoming=self.INC, answer=self.ANS,
                                         place=lambda t: (True, 1, ""))
        self.assertFalse(got["placed"])
        self.assertEqual(got["gate"], "crash")
        self.assertIn(trainer.CODE_CARD_MARK, got["card"])
        self.assertIn(self.REM, got["card"], "замечание не теряется даже при сбое")


class TestNothingGoesToQueueSilently(unittest.TestCase):
    """ВЕТКИ, СТАВЯЩЕЙ ЗАДАЧУ БЕЗ «ДА», НЕТ. Судим ДЕЙСТВИЕМ (обход AST), а не подстрокой."""

    BANNED = ("place_task", "enqueue_task", "approve_task")

    def _tree(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.abspath(trainer.__file__)), "trainer.py")
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def _calls(self, node):
        import ast
        out = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                out.add(getattr(func, "attr", None) or getattr(func, "id", None))
        return out

    def test_green_row_actions_are_never_called(self):
        names = self._calls(self._tree())
        for banned in self.BANNED:
            self.assertNotIn(banned, names,
                             "тренажёр зовёт %s — это ряд, исполняемый без «да»" % banned)

    def test_every_enqueue_is_followed_by_needs_approval_in_the_same_function(self):
        """Постановка ряда живёт ТОЛЬКО в паре с переводом в ожидание решения человека."""
        import ast
        found = 0
        for node in ast.walk(self._tree()):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = self._calls(node)
            if "enqueue_pc_task" not in calls:
                continue
            found += 1
            self.assertIn("set_needs_approval", calls,
                          "%s ставит ряд и НЕ переводит его в needs_approval" % node.name)
            self.assertIn("claim_task", calls,
                          "%s не снимает ряд из new — его подберёт process_new без «да»" % node.name)
        self.assertEqual(found, 1, "постановщик ряда обязан быть ровно один, найдено %d" % found)

    def test_testing_flag_forbids_a_live_row(self):
        """Набор гоняют с TESTING=1 — живой ряд из тестового прогона невозможен."""
        self.assertTrue(os.getenv("TESTING"), "набор обязан идти с TESTING=1")
        ok, tid, why = trainer._default_place("что угодно")
        self.assertFalse(ok)
        self.assertIsNone(tid)
        self.assertIn("TESTING", why)

    def test_trainer_never_writes_to_the_brain_folder(self):
        """В ПАПКУ МОЗГА тренажёр не пишет ни одной веткой — дорога задачи прямая, в очередь."""
        names = self._calls(self._tree())
        for banned in ("write_doc", "create_plain", "register", "unregister", "move_into_brain"):
            self.assertNotIn(banned, names, "тренажёр пишет в папку мозга: %s" % banned)


if __name__ == "__main__":
    unittest.main()
