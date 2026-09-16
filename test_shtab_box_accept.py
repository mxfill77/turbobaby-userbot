# -*- coding: utf-8 -*-
"""Набор приёмки и дожима.

ГЛАВНЫЙ ЗДЕСЬ — ОТРИЦАТЕЛЬНЫЙ ТЕСТ, и он назван первым классом файла: задание, у
которого отвечены ВСЕ пункты, обязано получить ПРИНЯТО и НЕ уйти на дожим. Прибор,
отправляющий на дожим всё подряд, съедает суточный потолок полосы на уже сделанном
— то есть хуже отсутствующего.

Второй по важности — предсмертный взгляд задания: артефакт, который бодро говорит
о себе «всё сделано», обязан получить ДОЖАТЬ по КАЖДОМУ пункту. Приёмка, читающая
самооценку, принимала бы любой отчёт.
"""

import unittest

import shtab_box
import shtab_box_accept as acc
import shtab_box_run


# Живой образец: сокращённое задание той же формы, что кладёт Штаб.
TASK = u"""ПОЛОСА: пк

ЦЕЛЬ. Приёмка заходит после закрытия.

ЗАПРЕТЫ (стандартный блок — идёт ПЕРВЫМ, не смягчать и не сокращать):
• ничего не удалять, включая уборку за собой;
• место для временного назвать явно и держаться его;
• .env и конфиги не читать — читать код, который их читает;
• процессы не трогать и не перезапускать без отдельного разрешения;
• боевых записей нет: живых чатов, боевой базы и рабочих таблиц не касаться.

ПРЕМИСА — ПРОВЕРИТЬ ПЕРВЫМ ДЕЙСТВИЕМ.
1. Гипотеза Штаба про судью закрытия, которую сверять артефактом не надо.
2. Вторая гипотеза Штаба, тоже не предмет приёмки.

ЧТО СДЕЛАТЬ

1. Завести пороговый счётчик витков демона и показать его число в сводке контура.
2. Отрицательный тест на ложную тревогу сторожа обязателен: молчание не будильник.

АРИФМЕТИКА. Медиана заходов 1018 с.

АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 04.09 со словами пороговый счётчик витков
"""

# Артефакт, отвечающий ОБОИМ пунктам своими словами (не цитатой задания).
FULL = u"""# Пороговый счётчик витков демона

Завёл счётчик витков и вывел его число отдельной строкой в сводку контура: порог
взят замером, показан в сводке.

## Отрицательный тест на ложную тревогу
Сторож молчит там, где молчание не будильник: обязательный отрицательный тест
закрывает ложную тревогу — 0 из 106.
"""

PRAISE = u"Всё сделано. Задание выполнено полностью, все пункты закрыты, работа готова.\n" * 20


class TheNegativeTestIsTheMainOne(unittest.TestCase):
    """Отвечено всё → ПРИНЯТО. Ни одного дожима на уже сделанном."""

    def test_full_artifact_is_accepted(self):
        out = acc.accept(TASK, FULL)
        self.assertEqual(out["verdict"], acc.ACCEPTED, out["why"])
        self.assertEqual(out["remainder"], [])

    def test_accepted_task_gets_no_retry_key_from_the_hands(self):
        rows = [{"id": 7, "status": "done", "task_text": self_row(TASK)}]
        blocks, notes = shtab_box_run.retry_blocks(rows, set(),
                                                   artifact_fn=lambda _t: (FULL, True, "", "a.md"))
        self.assertEqual(blocks, [])
        self.assertEqual(notes[0]["verdict"], acc.ACCEPTED)
        self.assertEqual(notes[0]["next"], "")


class PraiseAloneIsNotEvidence(unittest.TestCase):
    """Предсмертный взгляд задания: бодрый отчёт о себе — не ответ на пункты."""

    def test_praise_alone_is_not_evidence(self):
        out = acc.accept(TASK, PRAISE)
        self.assertEqual(out["verdict"], acc.RETRY)
        self.assertEqual(len(out["remainder"]), 2)

    def test_self_assessment_words_are_never_terms(self):
        for word in (u"сделано", u"готово", u"полностью", u"выполнено", u"успешно"):
            self.assertEqual(acc.terms(word), [], word)


class PointsComeFromTheDoSection(unittest.TestCase):
    """Сверяемся с «ЧТО СДЕЛАТЬ», а не со всем телом задания."""

    def test_premise_numbers_are_not_points(self):
        got = acc.points(TASK)
        self.assertEqual([p["n"] for p in got], [1, 2])
        self.assertNotIn(u"Гипотеза", " ".join(p["text"] for p in got))

    def test_next_section_ends_the_list(self):
        self.assertNotIn(u"Медиана", " ".join(p["text"] for p in acc.points(TASK)))
        self.assertNotIn(u"АДРЕС", " ".join(p["text"] for p in acc.points(TASK)))

    def test_no_section_means_unknown_not_accepted(self):
        out = acc.accept(u"ЦЕЛЬ. Что-то сделать.", FULL)
        self.assertEqual(out["verdict"], acc.UNKNOWN)

    def test_unread_artifact_is_unknown_and_never_done(self):
        out = acc.accept(TASK, "", artifact_ok=False, why_unread=u"папка недоступна")
        self.assertEqual(out["verdict"], acc.UNKNOWN)
        self.assertIn(u"папка недоступна", out["why"])
        self.assertEqual(out["remainder"], [])


class TheDedupLockIsNotWeakened(unittest.TestCase):
    """Дожим — ДРУГОЙ ключ того же документа, а не ослабленный дедуп."""

    def test_retry_key_is_a_new_key_of_the_same_document(self):
        self.assertEqual(acc.retry_key("dozhim.0904")[0], "dozhim.0904.p2")
        self.assertEqual(acc.base_key("dozhim.0904.p4"), "dozhim.0904")
        self.assertEqual(acc.attempt_of("dozhim.0904"), 1)
        self.assertEqual(acc.attempt_of("dozhim.0904.p4"), 4)

    def test_retry_key_passes_the_key_gate_of_the_box(self):
        nxt, _why = acc.retry_key("dozhim.0904")
        self.assertTrue(shtab_box.KEY_RE.match(nxt))
        self.assertTrue(shtab_box.MARK_RE.match(
            "%s дата=2026-09-04 ключ=%s]" % (shtab_box.MARK, nxt)))

    def test_key_that_would_overflow_the_marker_is_refused_not_trimmed(self):
        long_key = "k" * (shtab_box.KEY_MAX - 1)
        nxt, why = acc.retry_key(long_key)
        self.assertIsNone(nxt)
        self.assertIn(u"длиннее", why)

    def test_already_placed_retry_is_not_placed_twice(self):
        rows = [{"id": 8, "status": "done", "task_text": self_row(TASK)}]
        blocks, notes = shtab_box_run.retry_blocks(
            rows, {"probe.0904.p2"}, artifact_fn=lambda _t: (PRAISE, True, "", "a.md"))
        self.assertEqual(blocks, [])
        self.assertIn(u"уже стои́т", notes[0]["held"])

    def test_the_mirror_of_key_max_matches_the_box(self):
        self.assertEqual(acc.KEY_MAX, shtab_box.KEY_MAX)


class TheAttemptCeiling(unittest.TestCase):
    """Пять на задание: пятая не принята — слово владельцу, а не шестой заход."""

    def test_fifth_attempt_gets_no_sixth(self):
        nxt, why = acc.retry_key("probe.0904.p5")
        self.assertIsNone(nxt)
        self.assertIn(u"потолок попыток", why)

    def test_ceiling_produces_a_card_and_not_silence(self):
        rows = [{"id": 9, "status": "done",
                 "task_text": self_row(TASK, key="probe.0904.p5")}]
        blocks, notes = shtab_box_run.retry_blocks(
            rows, set(), artifact_fn=lambda _t: (PRAISE, True, "", "a.md"))
        self.assertEqual(blocks, [])
        self.assertIn(u"потолок попыток", notes[0]["card"])
        self.assertIn(u"п.1", notes[0]["card"])

    def test_five_attempts_and_not_six(self):
        key, seen = "probe.0904", []
        while True:
            nxt, _why = acc.retry_key(key)
            if not nxt:
                break
            seen.append(nxt)
            key = nxt
        self.assertEqual(len(seen), acc.ATTEMPT_MAX - 1)
        self.assertEqual(acc.attempt_of(seen[-1]), acc.ATTEMPT_MAX)


class TheRetryBodyCarriesTheRemainder(unittest.TestCase):
    """Остаток словами — первой строкой, и ворота приёма не ослаблены ни на символ."""

    def test_remainder_is_the_first_line_and_names_the_points(self):
        out = acc.accept(TASK, PRAISE)
        body, why = acc.retry_body(TASK, out["remainder"], 2, artifact="docs/artifacts/x.md")
        self.assertEqual(why, "")
        self.assertTrue(body.startswith(acc.RETRY_MARK))
        self.assertIn(u"п.1", body.splitlines()[0])
        self.assertIn(u"СВЕРКОЙ", body.splitlines()[0])

    def test_retry_passes_the_same_gates_of_the_box(self):
        out = acc.accept(TASK, PRAISE)
        body, _why = acc.retry_body(TASK, out["remainder"], 2)
        ok, reason, why = shtab_box.check({"key": "probe.0904.p2", "body": body})
        self.assertTrue(ok, "%s: %s" % (reason, why))

    def test_the_original_task_travels_verbatim(self):
        out = acc.accept(TASK, PRAISE)
        body, _why = acc.retry_body(TASK, out["remainder"], 2)
        self.assertIn(TASK.strip(), body)

    def test_the_remainder_of_the_previous_attempt_is_not_accumulated(self):
        out = acc.accept(TASK, PRAISE)
        second, _w = acc.retry_body(TASK, out["remainder"], 2)
        third, _w = acc.retry_body(second, out["remainder"], 3)
        self.assertEqual(third.count(acc.RETRY_MARK), 1)
        self.assertIn(u"3 из 5", third.splitlines()[0])

    def test_overgrown_body_is_refused_and_not_trimmed(self):
        out = acc.accept(TASK, PRAISE)
        body, why = acc.retry_body(TASK, out["remainder"], 2, body_max=200)
        self.assertIsNone(body)
        self.assertIn(u"при потолке", why)

    def test_the_retry_default_moved_with_the_body_ceiling(self):
        """Умолчание потолка дожима — экземпляр shtab_box.BODY_MAX (замер ёмкости 16.09)."""
        import inspect

        default = inspect.signature(acc.retry_body).parameters["body_max"].default
        self.assertEqual(default, shtab_box.BODY_MAX)


class TheRowIsParsedBackWithoutTheBridge(unittest.TestCase):
    """Дожим встаёт ТЕМ ЖЕ документом — по строке происхождения самого ряда."""

    def test_head_mirrors_shtab_box(self):
        self.assertEqual(acc.HEAD_MARK, shtab_box.MARK)

    def test_body_of_a_row_is_the_original_body_verbatim(self):
        row = shtab_box.task_text({"key": "probe.0904", "body": TASK.strip(),
                                   "name": "shtab_task_probe.0904", "id": "FILEID"},
                                  "2026-09-04")
        self.assertEqual(acc.body_of(row), TASK.strip())
        self.assertEqual(acc.key_of(row), "probe.0904")
        self.assertEqual(acc.source_of(row),
                         {"name": "shtab_task_probe.0904", "id": "FILEID"})


class TheDailyBudgetIsSpentByRetriesToo(unittest.TestCase):
    """Дожим тратит суточный потолок наравне с первым заходом, и идёт ПЕРВЫМ."""

    def test_retry_marker_is_counted_by_the_same_daily_counter(self):
        out = acc.accept(TASK, PRAISE)
        body, _w = acc.retry_body(TASK, out["remainder"], 2)
        row = shtab_box.task_text({"key": "probe.0904.p2", "body": body,
                                   "name": "shtab_task_probe.0904", "id": "X"}, "2026-09-04")
        self.assertEqual(shtab_box.taken_today([{"task_text": row}], "2026-09-04"), 1)

    def test_the_ceiling_stops_a_retry_exactly_like_a_first_run(self):
        out = acc.accept(TASK, PRAISE)
        body, _w = acc.retry_body(TASK, out["remainder"], 2)
        blk = {"key": "probe.0904.p2", "body": body, "name": "n", "id": "X", "retry": True}
        marks = [("2026-09-04", "spent-%d" % i) for i in range(shtab_box.DAILY_BUDGET)]
        take, held = shtab_box.select([blk], task_marks=marks,
                                      lane_marks={"pc": marks, "vps": []},
                                      today="2026-09-04")
        self.assertEqual(take, [])
        self.assertIn(u"потолок", held[0][1])

    def test_budget_words_are_named_in_the_code(self):
        self.assertIn(u"наравне", acc.BUDGET_WORDS)


class TheHandsReadOnlyTheProduct(unittest.TestCase):
    """Руки приёмки: три исхода чтения артефакта, и третий не притворяется вторым."""

    def test_ambiguous_address_is_unknown_not_retry(self):
        rows = [{"id": 10, "status": "done", "task_text": self_row(TASK)}]
        blocks, notes = shtab_box_run.retry_blocks(
            rows, set(),
            artifact_fn=lambda _t: ("", False, u"по адресу 2 файла(ов) отвечают словам", ""))
        self.assertEqual(blocks, [])
        self.assertEqual(notes[0]["verdict"], acc.UNKNOWN)

    def test_failed_rows_are_not_pushed_to_retry(self):
        rows = [{"id": 11, "status": "failed", "task_text": self_row(TASK)}]
        blocks, notes = shtab_box_run.retry_blocks(
            rows, set(), artifact_fn=lambda _t: (PRAISE, True, "", "a.md"))
        self.assertEqual((blocks, notes), ([], []))

    def test_foreign_rows_are_untouched(self):
        rows = [{"id": 12, "status": "done", "task_text": u"обычная задача владельца"}]
        blocks, notes = shtab_box_run.retry_blocks(
            rows, set(), artifact_fn=lambda _t: (PRAISE, True, "", "a.md"))
        self.assertEqual((blocks, notes), ([], []))

    def test_a_retry_block_is_built_of_the_same_document(self):
        rows = [{"id": 13, "status": "done", "task_text": self_row(TASK)}]
        blocks, notes = shtab_box_run.retry_blocks(
            rows, set(), artifact_fn=lambda _t: (PRAISE, True, "", "docs/artifacts/x.md"))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["key"], "probe.0904.p2")
        self.assertEqual(blocks[0]["name"], "shtab_task_probe.0904")
        self.assertTrue(blocks[0]["retry"])
        self.assertEqual(notes[0]["next"], "probe.0904.p2")
        ok, reason, why = shtab_box.check(blocks[0])
        self.assertTrue(ok, "%s: %s" % (reason, why))


def self_row(body, key="probe.0904", day="2026-09-04"):
    """Текст ряда очереди из тела — собранный ЧУЖОЙ функцией, а не своей копией."""
    return shtab_box.task_text({"key": key, "body": body.strip(),
                                "name": "shtab_task_probe.0904", "id": "FILEID"}, day)


if __name__ == "__main__":
    unittest.main(verbosity=2)
