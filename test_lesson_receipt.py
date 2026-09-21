# -*- coding: utf-8 -*-
"""КВИТАНЦИЯ ГОВОРИТ ПРО КАЖДЫЙ УРОК ОТДЕЛЬНО — регресс (22.09.2026, задание 70i).

ЧТО СТОРОЖИТСЯ, ОДНОЙ ФРАЗОЙ: один тап штатно рождает уроки РАЗНЫХ состояний, и квитанция
обязана назвать по КАЖДОМУ номер, состояние словом и — для ждущего — ДОСЛОВНУЮ строку
включения; прочитать состояние не удалось — сказать «НЕ ЗНАЮ», а не «действует».

БОЕВОГО ЗДЕСЬ НЕТ НИ ОДНОГО ФАЙЛА: база уроков у каждого теста своя, во временном каталоге,
и путь ей передаётся параметром. Наружу не уходит ничего: ни одна дверь-отправитель не
зовётся, сетевых вызовов нет.
"""

import os
import shutil
import tempfile
import unittest

import exam_show
import lesson_store
import lesson_word_forms
import pc_agent
import trainer


MARK = "ПРОБА 70i 22.09"
WHO = "receipttest"


def _add_active(path, correct):
    return lesson_store.add(question="вопрос " + MARK, bot_answer="ответ " + MARK,
                            correct=correct, why="наблюдение критика: " + MARK, who=WHO,
                            source=lesson_store.SOURCE_EXAM, path=path)


def _add_candidate(path, correct):
    return lesson_store.add_candidate(question="вопрос " + MARK, bot_answer="ответ " + MARK,
                                      correct=correct, who=WHO,
                                      source=lesson_store.SOURCE_EXAM, path=path)


class FormLivesInOnePlace(unittest.TestCase):
    """ФОРМА СЛОВА — ОДНО ОПРЕДЕЛЕНИЕ. Образец, написанный по памяти рядом с разбором, переживает
    правку разбора и диктует владельцу строку, которую разбор уже не принимает."""

    def test_parser_is_the_same_object(self):
        self.assertIs(pc_agent.LESSON_ON_RE, lesson_word_forms.LESSON_ON_RE,
                      "разбор слова обязан быть ОДНИМ объектом, а не копией регулярки")

    def test_phrase_parses_back_by_the_live_router(self):
        """Собранный образец разбирается ЖИВЫМ роутером агента, а не только своей регуляркой."""
        phrase = lesson_word_forms.promote_phrase(7)
        self.assertEqual(phrase, "урок включи 7: " + lesson_word_forms.PROMOTE_REASON_SLOT)
        self.assertEqual(pc_agent.lesson_word(phrase),
                         ("promote", 7, lesson_word_forms.PROMOTE_REASON_SLOT, ""))

    def test_phrase_carries_given_reason(self):
        phrase = lesson_word_forms.promote_phrase(3, why="клиент дважды спросил цену")
        self.assertEqual(pc_agent.lesson_word(phrase),
                         ("promote", 3, "клиент дважды спросил цену", ""))

    def test_numberless_form_is_checked_too(self):
        """Безномерная форма («урок включи N: …») сверяется подстановкой числа — иначе её не
        проверить ничем, и она молча пережила бы правку разбора."""
        self.assertEqual(lesson_word_forms.promote_phrase(),
                         "урок включи N: " + lesson_word_forms.PROMOTE_REASON_SLOT)

    def test_broken_form_is_loud(self):
        """Разъехался разбор — образец ОТКАЗЫВАЕТ громко. Молчаливый исход здесь и есть дефект."""
        keep = lesson_word_forms.LESSON_ON_RE
        try:
            lesson_word_forms.LESSON_ON_RE = __import__("re").compile(r"^ничего не совпадёт$")
            with self.assertRaises(lesson_word_forms.PromoteFormBroken):
                lesson_word_forms.promote_phrase(7)
        finally:
            lesson_word_forms.LESSON_ON_RE = keep


class ReceiptTellsEachLesson(unittest.TestCase):
    """Квитанция по КАЖДОМУ уроку: номер, состояние словом, дословное слово включения."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="receipt_70i_")
        self.base = os.path.join(self.dir, "lessons.tsv")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.keep_set = exam_show.SET_NAME

    def tearDown(self):
        exam_show.SET_NAME = self.keep_set

    def test_one_tap_two_states_are_told_apart(self):
        n1 = _add_active(self.base, MARK + ": подсказка с наблюдением")
        n2 = _add_candidate(self.base, MARK + ": подсказка без наблюдения")
        said = exam_show.lessons_roster([n1, n2], path=self.base)
        self.assertIn("#%d" % n1, said)
        self.assertIn("#%d" % n2, said)
        self.assertEqual(said.count("«актив»"), 1)
        self.assertEqual(said.count("«кандидат»"), 1)
        # слово включения — ДОСЛОВНО и только ждущему
        self.assertIn(lesson_word_forms.promote_phrase(n2), said)
        self.assertNotIn(lesson_word_forms.promote_phrase(n1), said)

    def test_state_is_read_from_base_not_guessed(self):
        """МУТАНТ: состояние подменено В БАЗЕ, запись не трогали — квитанция обязана сказать
        другое. Скажи она то же, значит состояние выводится из условия записи, а не читается."""
        n1 = _add_active(self.base, MARK + ": первый")
        n2 = _add_candidate(self.base, MARK + ": второй")
        before = exam_show.lessons_roster([n1, n2], path=self.base)
        self.assertTrue(lesson_store.promote(n2, why=MARK + ": причина мутанта", who=WHO,
                                             path=self.base).ok)
        after = exam_show.lessons_roster([n1, n2], path=self.base)
        self.assertNotEqual(before, after, "квитанция не заметила подмены состояния")
        self.assertEqual((before.count("«кандидат»"), after.count("«кандидат»")), (1, 0))
        self.assertEqual((before.count("«актив»"), after.count("«актив»")), (1, 2))
        self.assertNotIn(lesson_word_forms.promote_phrase(n2), after)

    def test_live_set_never_dictates_the_bare_chat_word(self):
        """У ЖИВОГО набора слова в чат нет: `урок включи 7` разбирается без `--path` и перевёл бы
        урок #7 ТРЕНАЖЁРА — ровно тот класс, ради которого базы разведены."""
        n = _add_candidate(self.base, MARK + ": кандидат живого набора")
        exam_show.SET_NAME = exam_show.SET_LIVE
        said = exam_show.lessons_roster([n], path=self.base)
        self.assertNotIn(lesson_word_forms.promote_phrase(n), said)
        self.assertIn("--path", said)
        self.assertIn("lesson_promote.py", said)

    def test_three_lessons_fit_one_telegram_message(self):
        nums = [_add_active(self.base, MARK + ": раз"),
                _add_candidate(self.base, MARK + ": два"),
                _add_candidate(self.base, MARK + ": три")]
        said = exam_show.lessons_roster(nums, path=self.base)
        self.assertLess(len(said), trainer.TG_MSG_LIMIT,
                        "перечень трёх уроков не влезает в одно сообщение Telegram")


class ThirdOutcomeIsNamed(unittest.TestCase):
    """ТРЕТИЙ ИСХОД: состояние прочитать не удалось → «НЕ ЗНАЮ» с причиной. Ни одна ветка не
    превращает «не смог прочитать» в «действует»."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="receipt_70i_blind_")
        self.base = os.path.join(self.dir, "lessons.tsv")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _assert_blind(self, said):
        self.assertIn("НЕ ЗНАЮ", said)
        self.assertNotIn("ДЕЙСТВУЕТ", said)
        self.assertNotIn("«кандидат»", said)

    def test_base_missing(self):
        self._assert_blind(exam_show.lessons_roster(
            [1], path=os.path.join(self.dir, "net-takogo-fayla.tsv")))

    def test_number_absent(self):
        _add_active(self.base, MARK + ": единственный")
        said = exam_show.lessons_roster([777], path=self.base)
        self._assert_blind(said)
        self.assertIn("#777", said)

    def test_state_not_recognised(self):
        n = _add_candidate(self.base, MARK + ": станет непонятным")
        with open(self.base, encoding="utf-8") as f:
            rows = f.read().splitlines()
        out = []
        for row in rows:
            parts = row.split("\t")
            if len(parts) > lesson_store.IDX_STATE and parts[0].strip() == str(n):
                parts[lesson_store.IDX_STATE] = "непонятно_что"
                row = "\t".join(parts)
            out.append(row)
        with open(self.base, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out) + "\n")
        said = exam_show.lessons_roster([n], path=self.base)
        self._assert_blind(said)
        self.assertIn("непонятно_что", said)

    def test_read_raises(self):
        """Файл есть, но байты не UTF-8: `lesson_store.load` бросает — квитанция обязана пережить
        это словами, а не падением двери. Тап к этой минуте УЖЕ записан."""
        with open(self.base, "wb") as f:
            f.write(b"1\t\xff\xfe\xfd\tx\ty\tz\t" + WHO.encode()
                    + "\tкогда\tактив\n".encode("utf-8"))
        self._assert_blind(exam_show.lessons_roster([1], path=self.base))


if __name__ == "__main__":
    unittest.main(verbosity=2)
