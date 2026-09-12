# -*- coding: utf-8 -*-
"""Набор подбора живых примеров (задание 51-b, 12.09.2026).

ЧТО ЗДЕСЬ ЗАКРЕПЛЕНО. Не «модуль импортируется», а три отрицательных условия задания, каждое из
которых — названный способ сделать хуже, чем было:

* выключатель СТОИТ (промпт байт в байт прежний) — иначе правка необратима на живом контуре;
* вопрос без соседей даёт НОЛЬ примеров — иначе голова учится форме ответа на чужой вопрос;
* вопрос ИЗ базы не видит СВОЙ диалог — иначе бот учится на ответе ровно на этот вопрос и
  экзамен показывает списанную работу как свою.

ТЕКСТА ПЕРЕПИСКИ НАБОР НАРУЖУ НЕ ПЕЧАТАЕТ НИ ОДНОЙ СТРОКОЙ. Сообщения провалов несут числа,
метки и длины — но не содержание базы: вывод прогона уезжает в лог захода и в артефакт.
"""
import os
import unittest

import style_examples as se
import suggest


def _switch(value):
    """Поставить выключатель и вернуть прежнее значение (для точного восстановления)."""
    old = os.environ.get("STYLE_EXAMPLES")
    if value is None:
        os.environ.pop("STYLE_EXAMPLES", None)
    else:
        os.environ["STYLE_EXAMPLES"] = value
    return old


class TestSwitch(unittest.TestCase):
    """ВЫКЛЮЧАТЕЛЬ: с ним промпт обязан быть БАЙТ В БАЙТ прежним."""

    def setUp(self):
        self._old = _switch(None)

    def tearDown(self):
        _switch(self._old)

    def test_off_gives_manual_block_verbatim(self):
        _switch("0")
        self.assertFalse(se.enabled())
        got = suggest._style_block("Здравствуйте! Хочу XMAX 300 с 6ого по 11ое октября")
        self.assertEqual(got, suggest.STYLE_FEWSHOT,
                         "выключенный подбор обязан отдавать прежний блок посимвольно")

    def test_off_prompt_is_byte_identical_to_no_question_prompt(self):
        _switch("0")
        with_q = suggest.make_system_prompt("FAQ", "ru", client_question="сколько стоит nmax на неделю")
        without_q = suggest.make_system_prompt("FAQ", "ru")
        self.assertEqual(len(with_q), len(without_q))
        self.assertEqual(with_q, without_q)

    def test_empty_question_never_touches_the_base(self):
        """Пустой вопрос обязан отдавать прежний блок, НЕ заглядывая в базу вовсе."""
        _switch("1")

        def boom(*_a, **_kw):
            raise AssertionError("подбор звался на пустом вопросе — лишняя работа на каждом вызове")

        old = se.pick
        se.pick = boom
        try:
            self.assertEqual(suggest._style_block(""), suggest.STYLE_FEWSHOT)
        finally:
            se.pick = old

    def test_on_and_matching_question_changes_the_block(self):
        _switch("1")
        self.assertTrue(se.enabled())
        q = "Здравствуйте! Хочу XMAX 300 с 6ого по 11ое октября, залог паспортом, 1 шлем"
        picked = se.pick(q)
        self.assertGreaterEqual(len(picked), se.EX_MIN,
                                "кейс 1 обязан находить примеры — на нём меряется эффект правки")
        block = suggest._style_block(q)
        self.assertNotEqual(block, suggest.STYLE_FEWSHOT)
        for m in se.marks(picked):
            self.assertIn(m, block, "метка показанного примера обязана стоять в самой записке")

    def test_style_guide_survives_in_both_paths(self):
        """Памятка стиля — не примеры: она обязана стоять на ОБОИХ путях, иначе правка её съела."""
        _switch("1")
        q = "Здравствуйте! Хочу XMAX 300 с 6ого по 11ое октября, залог паспортом, 1 шлем"
        self.assertIn(suggest.STYLE_GUIDE, suggest._style_block(q))
        self.assertIn(suggest.STYLE_GUIDE, suggest._style_block(""))


class TestNoNeighbours(unittest.TestCase):
    """ВОПРОС БЕЗ СОСЕДЕЙ: примеров НЕТ, случайные не подставляются."""

    FOREIGN = (
        "Как испечь шарлотку с яблоками и корицей в духовке",
        "Подскажите расписание паромов на Копенгаген из Осло зимой",
        "Где найти учебник по квантовой электродинамике на русском",
        "Подскажите как правильно заваривать пуэр в глиняном чайнике",
        "Можно ли оформить ипотеку без первоначального взноса в Сбербанке",
    )

    def setUp(self):
        self._old = _switch("1")

    def tearDown(self):
        _switch(self._old)

    def test_foreign_domain_gives_no_examples(self):
        for q in self.FOREIGN:
            got = se.pick(q)
            self.assertEqual(got, [], "чужой домен получил %d примеров (оценки %s) — подбор шумит"
                             % (len(got), ["%.3f" % g["score"] for g in got]))

    def test_foreign_domain_falls_back_to_manual_pairs(self):
        for q in self.FOREIGN:
            self.assertEqual(suggest._style_block(q), suggest.STYLE_FEWSHOT)

    def test_empty_and_junk_questions_are_silent(self):
        for q in ("", "   ", "?", "да", "ок", "+", "\n\n"):
            self.assertEqual(se.pick(q), [])

    def test_partial_match_below_minimum_is_dropped_whole(self):
        """Две пары выше порога — это НЕ примеры: отдаётся пусто, а не «сколько нашлось»."""
        idx = se.get_index()
        for q in ("Здравствуйте! Хочу XMAX 300 с 6ого по 11ое октября, залог паспортом, 1 шлем",) \
                + self.FOREIGN:
            n = len(se.similar(q, se.EX_MAX, se.SIM_MIN, idx))
            self.assertEqual(len(se.pick(q, index=idx)), n if n >= se.EX_MIN else 0)


class TestOwnDialogExcluded(unittest.TestCase):
    """ВОПРОС ИЗ БАЗЫ: свой диалог в примеры не попадает НИ ОДНОЙ РЕПЛИКОЙ."""

    def setUp(self):
        self._old = _switch("1")
        self.idx = se.get_index()

    def tearDown(self):
        _switch(self._old)

    def test_own_dialog_never_returned(self):
        step = max(1, len(self.idx.pairs) // 200)
        checked = 0
        for i in range(0, len(self.idx.pairs), step):
            p = self.idx.pairs[i]
            got = se.similar(p["q"], 10, 0.0, self.idx)
            self.assertNotIn(p["dialog"], [g["dialog"] for g in got],
                             "вопрос диалога %d нашёл свой же диалог — бот учится на своём ответе"
                             % p["dialog"])
            checked += 1
        self.assertGreaterEqual(checked, 100, "проверено слишком мало пар: %d" % checked)

    def test_exact_question_does_not_score_itself_one(self):
        """Без замка дословный вопрос давал бы оценку 1.0 своей же паре — её быть не должно."""
        for i in (0, len(self.idx.pairs) // 2, len(self.idx.pairs) - 1):
            p = self.idx.pairs[i]
            for g in se.similar(p["q"], 10, 0.0, self.idx):
                self.assertNotEqual((g["dialog"], g["msg"]), (p["dialog"], p["msg"]))


class TestIndex(unittest.TestCase):
    """Индекс: отпечаток, самопересборка, детерминизм, пределы."""

    def test_fingerprint_is_content_not_mtime(self):
        idx = se.get_index()
        self.assertEqual(idx.fingerprint, se.fingerprint())
        self.assertEqual(len(idx.fingerprint), 16)

    def test_index_rebuilds_when_base_changes(self):
        """Сменился отпечаток — индекс пересобрался САМ, без чьей-либо команды."""
        import tempfile
        first = se.get_index()
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            f.write('{"messages": [{"who": "client", "text": "сколько стоит аренда нмакс на неделю"},'
                    ' {"who": "company", "text": "Назовите даты, посчитаю точную стоимость."}]}\n')
            tiny = f.name
        try:
            other = se.get_index(tiny)
            self.assertNotEqual(other.fingerprint, first.fingerprint)
            self.assertEqual(len(other.pairs), 1)
            back = se.get_index()
            self.assertEqual(back.fingerprint, first.fingerprint)
            self.assertEqual(len(back.pairs), len(first.pairs))
        finally:
            os.unlink(tiny)          # СВОЙ временный файл этого теста, не артефакт репозитория

    def test_missing_base_is_silence_not_crash(self):
        idx = se.build_index(os.path.join(os.path.dirname(__file__), "нет-такого-файла.jsonl"))
        self.assertEqual(idx.fingerprint, "")
        self.assertEqual(len(idx.pairs), 0)
        self.assertEqual(se.similar("сколько стоит нмакс", index=idx), [])

    def test_pick_is_deterministic_across_calls(self):
        q = "Здравствуйте! Хочу XMAX 300 с 6ого по 11ое октября, залог паспортом, 1 шлем"
        runs = [se.marks(se.pick(q)) for _ in range(5)]
        self.assertEqual(len(set(tuple(r) for r in runs)), 1, "выдача плавает: %s" % runs)

    def test_scores_are_sorted_and_within_limits(self):
        for q in ("сколько стоит аренда nmax на неделю", "какие байки есть в наличии",
                  "сколько доставка на Найхарн", "а депозит какой"):
            got = se.pick(q)
            if not got:
                continue
            self.assertLessEqual(len(got), se.EX_MAX)
            self.assertGreaterEqual(len(got), se.EX_MIN)
            self.assertEqual([g["score"] for g in got],
                             sorted([g["score"] for g in got], reverse=True))
            for g in got:
                self.assertGreaterEqual(g["score"], se.SIM_MIN)
                self.assertLessEqual(g["score"], 1.0)

    def test_pair_texts_carry_no_contacts_or_anon_marks(self):
        """Чистота НЕ предполагается, а проверяется: ни ссылок, ни телефонов, ни меток анонимайзера."""
        idx = se.get_index()
        bad = [(p["dialog"], p["msg"]) for p in idx.pairs
               if se._LEAK_RE.search(p["a"]) or se._ANON_MARK_RE.search(p["a"])]
        self.assertEqual(bad, [], "ответов с контактами/метками: %d (адреса %s)"
                         % (len(bad), bad[:5]))

    def test_lengths_are_capped(self):
        idx = se.get_index()
        self.assertTrue(all(se.A_MIN_CHARS <= len(p["a"]) <= se.A_MAX_CHARS for p in idx.pairs))
        self.assertTrue(all(se.Q_MIN_CHARS <= len(p["q"]) <= se.Q_MAX_CHARS for p in idx.pairs))

    def test_model_canon_token_is_the_bots_own_detector(self):
        """Имя модели опознаётся ТЕМ ЖЕ детектором, что у бота, — иначе словари разъедутся молча.

        Canon здесь НЕ выдуман под тест, а взят у `model_name`: «forza 300» он сводит к `FORZA`
        (кубатуры в `CANON_TOKENS` у этого корня нет), и латиница с кириллицей дают ОДИН токен —
        ради чего псевдо-токен и заведён."""
        self.assertEqual(se._tokens("интересует forza 300 на неделю") & {"mdl:FORZA"}, {"mdl:FORZA"})
        self.assertEqual(se._tokens("интересует Форза 300 на неделю") & {"mdl:FORZA"}, {"mdl:FORZA"})
        self.assertIn("mdl:XADV", se._tokens("а x-adv 750 есть?"))
        self.assertIn("mdl:NMAX", se._tokens("nmax 155 на 5 дней"))
        for text in ("интересует forza 300", "интересует Форза 300"):
            self.assertEqual(sorted(t for t in se._tokens(text) if t.startswith("mdl:")),
                             ["mdl:FORZA"])


class TestPromptWiring(unittest.TestCase):
    """Связка с промптом: инструкция на месте, метки видны, факты не сдаются примерам."""

    def setUp(self):
        self._old = _switch("1")
        self.q = "Здравствуйте! Хочу XMAX 300 с 6ого по 11ое октября, залог паспортом, 1 шлем"

    def tearDown(self):
        _switch(self._old)

    def test_block_states_facts_come_from_price_block(self):
        block = suggest._style_block(self.q)
        self.assertIn("ИЛЛЮСТРАЦИЯ ФОРМАТА", block)
        self.assertIn("блока ЦЕНА", block)
        self.assertIn("ТОН", block)

    def test_marks_are_hidden_from_the_client(self):
        self.assertIn("клиенту их НЕ показывай", suggest._style_block(self.q))

    def test_prompt_contains_the_block(self):
        p = suggest.make_system_prompt("FAQ", "ru", client_question=self.q)
        self.assertIn("ПОДОБРАННЫЕ ПОД ЭТОТ ВОПРОС", p)
        self.assertNotIn("ПОДОБРАННЫЕ ПОД ЭТОТ ВОПРОС",
                         suggest.make_system_prompt("FAQ", "ru"))

    def test_last_client_line_is_the_current_question(self):
        tr = ("[клиент]: Здравствуйте\n[менеджер]: Здравствуйте! Чем помочь?\n"
              "[клиент]: Хочу XMAX 300 с 6ого по 11ое октября")
        self.assertEqual(suggest._last_client_line(tr), "Хочу XMAX 300 с 6ого по 11ое октября")
        self.assertEqual(suggest._last_client_line(""), "")
        self.assertEqual(suggest._last_client_line("[менеджер]: только менеджер"), "")

    def test_marks_from_transcript_match_marks_from_question(self):
        tr = "[клиент]: %s" % self.q
        self.assertEqual(suggest.live_examples_marks(tr), se.marks(se.pick(self.q)))

    def test_broken_base_falls_back_and_does_not_raise(self):
        """Отказ подбора обязан отдать ручные пары, а не уронить ответ клиенту."""
        def boom(*_a, **_kw):
            raise RuntimeError("база недоступна")

        old = se.pick
        se.pick = boom
        try:
            self.assertEqual(suggest._style_block(self.q), suggest.STYLE_FEWSHOT)
            self.assertEqual(suggest.live_examples_marks("[клиент]: %s" % self.q), [])
        finally:
            se.pick = old


class TestMarksFormat(unittest.TestCase):
    def test_marks_carry_address_only(self):
        got = se.pick("Здравствуйте! Хочу XMAX 300 с 6ого по 11ое октября, залог паспортом, 1 шлем")
        for m, e in zip(se.marks(got), got):
            self.assertEqual(m, "д%s·р%s" % (e["dialog"], e["msg"]))
            self.assertNotIn(e["client"][:20], m)
            self.assertNotIn(e["company"][:20], m)

    def test_marks_of_nothing_is_empty(self):
        self.assertEqual(se.marks([]), [])
        self.assertEqual(se.marks(None), [])


if __name__ == "__main__":
    unittest.main()
