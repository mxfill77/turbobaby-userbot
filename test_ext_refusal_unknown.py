# -*- coding: utf-8 -*-
"""test_ext_refusal_unknown.py — ВНЕШНИЙ ОТКАЗ ЗАКРЫВАЕТСЯ «НЕИЗВЕСТНО», А НЕ ПРОВАЛОМ.

Предмет один: падение захода с признаками отказа ЧУЖОЙ СТОРОНЫ получает исход
«неизвестно» с названной причиной, выходит из знаменателя зачётной серии и не
поднимает сигнальную остановку ящика; НАШ настоящий провал при этом остаётся
провалом и сигнал поднимает как раньше.

ФИКСТУРЫ — ДОСЛОВНЫЕ СТРОКИ ЖИВЫХ ПРОВАЛОВ, а не идеализированные. Взяты из
расписок ревью-контура `docs/review_receipts/pc-2026-09-12-3*.json` (четыре ряда
ящика за 12.09.2026) и из `pc_orchestrator.log` за 03.09.2026. Правило полосы
прямое: голдены детекта — дословные фразы из живого провала, потому что
придуманная «похожая» строка зеленеет молча, а живая ловит различитель на
настоящем формате.

ОТРИЦАТЕЛЬНЫЙ ТЕСТ ТРЕМЯ КОНЦАМИ живёт в :class:`TestNegativeThreeEnds` и
каждый конец назван в имени метода — `end1` · `end2` · `end3`.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import contour_digest as cd                # noqa: E402
import queue_snapshot_pc as qs             # noqa: E402
import shtab_box_signals as sig            # noqa: E402

# ═══════════════════════ ЖИВЫЕ СТРОКИ, ДОСЛОВНО ══════════════════════════════

# Ряд #37 ящика, 12.09.2026. Расписка `docs/review_receipts/pc-2026-09-12-37.json`,
# поле `result_head`. Ровно эта же форма — у #33, #34 и #38 (разнится лишь час
# сброса и окно), то есть корпус живого класса здесь 4 ряда из 4.
LIVE_37 = ("провал [причина=exec_error · ошибка выполнения]: claude exit=1: You've hit your "
           "session limit · resets 3:30am (Asia/Bangkok). Следов работы в окне 12.09 "
           "18:01–18:14 UTC нет (коммитов 0, записей журнала 0).")
# Ряд #101, 03.09.2026 — тот же класс, но чужая сторона назвала себя сама.
LIVE_101 = ("провал [причина=exec_error · ошибка выполнения]: claude exit=1: API Error: 529 "
            "Overloaded. This is a server-side issue, usually temporary — try again in a "
            "moment. If it persists, check https://status.claude.com.. Следов работы в окне "
            "03.09 13:31–13:36 UTC нет (коммитов 0, записей журнала 0).")
# Ряд #12, 03.09.2026. До 17.09 фикстура звалась «НАШ провал под тем же кодом: чужой стороны не
# назвал никто» — и это было УТВЕРЖДЕНИЕ БЕЗ УЛИКИ, выведенное из отсутствия слов. Что лог знает
# на самом деле (`pc_orchestrator.log.1`, 03.09 04:58–05:03): заход разведки ступени E, RUN через
# 61 с после рестарта демона по self-update, `claude exit=1`, stdout и stderr пусты, 25.86 с,
# следов 0/0; думатель самопочинки через 10 с тоже `exit=1`, к 05:03 процессов демона нет вовсе.
# Что упало — CLI, среда или машина, — НЕИЗВЕСТНО; что о задании не сказано ничего — факт.
LIVE_12_SILENT = ("провал [причина=exec_error · ошибка выполнения]: claude exit=1: нет вывода. "
                  "Следов работы в окне 02.09 21:59–21:59 UTC нет (коммитов 0, записей "
                  "журнала 0).")
# Ряд #51 ящика, 16.09.2026 (#52 — та же форма). Тело журнала `2026-09-16-094622-note.md`,
# дословно. Слов словаря («API Error», «… limit») здесь нет — словарь назвал ряд нашим, и сигнал
# А держал ящик 17:41:36 → 22:51:14.
LIVE_51 = ("провал [причина=exec_error · ошибка выполнения]: claude exit=1: Failed to "
           "authenticate: OAuth session expired and could not be refreshed. Следов работы в окне "
           "16.09 09:45–09:45 UTC нет (коммитов 0, записей журнала 0).")
NO_TRACE_TAIL = "Следов работы в окне 15.09 03:10–03:14 UTC нет (коммитов 0, записей журнала 0)."
# НАШ провал с процессным падением: код тот же, но заход наработал (форма живого #100 от 03.09 —
# коммит 14cb105 в окне). Слова процесса намеренно БЕЗ словаря: ряд наш по СЛЕДАМ, а не по словам.
OURS_RC_WITH_TRACES = ("НЕ ЗАКРЫТА, но В ОКНЕ ЗАДАЧИ ЕСТЬ РАБОТА [причина=exec_error · ошибка "
                       "выполнения]: claude exit=1: нет вывода. СЛЕДЫ в окне 03.09 13:15–13:23 "
                       "UTC: коммитов 1 (14cb105 «Замок единственной копии»). Формальное закрытие "
                       "не состоялось — НЕ переделывай вслепую: сверь эти следы с заданием и "
                       "закрой руками. (Окно, а не авторство: в него попадают и параллельные "
                       "сессии ПК.)")
# НАШИ провалы, которые судья ПРОЧИТАЛ: нет артефакта по адресу и красные тесты (п. 7а 62-p).
OURS_NO_ARTIFACT = ("НЕ ДОКАЗАНО (V0): результат по названному адресу ПРОЧИТАН, «сделано» не "
                    "подтвердилось — артефакта по адресу docs/artifacts/… нет.")
OURS_RED_TESTS = ("НЕ ДОКАЗАНО (V0): результат по названному адресу ПРОЧИТАН, «сделано» не "
                  "подтвердилось — набор test_shtab_box_signals: FAILED (failures=2).")


def _row(tid, result, status="failed"):
    return {"id": tid, "status": status, "result": result}


# ═══════════════════════ 1. ПРИЗНАК ══════════════════════════════════════════


class TestSign(unittest.TestCase):
    """Признак берётся из МЕСТА СОБЫТИЯ — из части, которую пишет демон о процессе."""

    def test_the_live_session_limit_row_is_external(self):
        """Живой #37: поставщик закрыл окно подписки — это не наш дефект."""
        ok, why = sig.external_refusal(_row(37, LIVE_37))
        self.assertTrue(ok, why)
        self.assertIn("session limit", why)
        self.assertIn("exec_error", why)

    def test_the_old_api_error_row_still_counts(self):
        """Прежний класс НЕ ОСЛАБЛЕН: живой #101 как был внешним, так и остался."""
        ok, why = sig.external_refusal(_row(101, LIVE_101))
        self.assertTrue(ok, why)
        self.assertIn("API Error", why)

    def test_the_process_form_is_required(self):
        """Слова без НЕНУЛЕВОГО КОДА ВОЗВРАТА не значат ничего: код ставит процесс, не текст."""
        row = _row(51, "провал [причина=exec_error · ошибка выполнения]: insufficient_output: "
                       "нет строки «RESULT: <итог>» — выполнение не подтверждено. "
                       "stdout(хвост): You've hit your session limit. " + NO_TRACE_TAIL)
        ok, why = sig.external_refusal(row)
        self.assertFalse(ok, why)
        self.assertIn("процессного падения", why)

    def test_a_zero_exit_code_is_not_a_process_death(self):
        row = _row(52, "провал [причина=exec_error · ошибка выполнения]: claude exit=0: "
                       "You've hit your session limit. " + NO_TRACE_TAIL)
        self.assertFalse(sig.external_refusal(row)[0])

    def test_traces_in_the_window_keep_the_row_ours(self):
        """Четвёртое условие не ослаблено: заход, успевший наработать, — наш."""
        row = _row(53, "НЕ ЗАКРЫТА, но В ОКНЕ ЗАДАЧИ ЕСТЬ РАБОТА [причина=exec_error · ошибка "
                       "выполнения]: claude exit=1: You've hit your session limit. СЛЕДЫ в окне "
                       "12.09: коммитов 1 (abc1234 «правка»).")
        ok, why = sig.external_refusal(row)
        self.assertFalse(ok, why)
        self.assertIn("следы работы", why)


# ═══════════════════════ 2. ИСХОД В ОЧЕРЕДИ ══════════════════════════════════


class TestQueueOutcome(unittest.TestCase):
    """ТРИ ИСХОДА НЕ СЛИВАЮТСЯ: сдано · не доказано (упало) · НЕИЗВЕСТНО."""

    def test_external_refusal_closes_as_unknown(self):
        self.assertEqual(qs.OUT_UNKNOWN, qs.outcome_of(LIVE_37))

    def test_the_reason_is_named_and_not_empty(self):
        """«Неизвестно» без причины льготы не получает нигде на полосе."""
        words = qs.unknown_words_of(LIVE_37)
        self.assertTrue(words.strip())
        self.assertIn("session limit", words)

    def test_the_blame_is_the_foreign_side_not_the_judge(self):
        self.assertEqual(qs.UNKNOWN_BY_EXT, qs.unknown_by(LIVE_37))

    def test_our_own_exec_error_with_traces_stays_failed(self):
        """rc≠0, но заход наработал — ряд наш, и слова процесса этого не меняют."""
        self.assertEqual(qs.OUT_FAILED, qs.outcome_of(OURS_RC_WITH_TRACES))

    def test_the_silent_death_without_traces_is_unknown_like_any_other(self):
        """#12: слов нет вовсе — исход тот же, что у #37 со словами (правило, а не словарь)."""
        self.assertEqual(qs.OUT_UNKNOWN, qs.outcome_of(LIVE_12_SILENT))
        # «нет вывода» — заглушка САМОГО демона (`pc_orchestrator`, ветка rc≠0), и печатается
        # дословно: читатель видит, что процесс молчал, а не наш пересказ этого.
        self.assertIn("«нет вывода.»", qs.unknown_words_of(LIVE_12_SILENT))

    def test_the_live_16_09_auth_death_is_unknown_with_its_own_words(self):
        """#51: слов словаря нет, исход НЕИЗВЕСТНО, а причина — дословно слова процесса."""
        self.assertEqual(qs.OUT_UNKNOWN, qs.outcome_of(LIVE_51))
        self.assertIn("OAuth session expired", qs.unknown_words_of(LIVE_51))

    def test_owner_rejection_is_still_first(self):
        self.assertEqual(qs.OUT_REJECTED,
                         qs.outcome_of("%s: не надо. %s" % (qs.REJECT_MARK, LIVE_37)))

    def test_apply_failed_puts_the_blame_on_the_row(self):
        """Виновник едет НА РЯДУ: чистый счёт серии спросить различитель не может."""
        got = qs.apply_failed({}, [{"id": 37, "goal": "ЦЕЛЬ: х", "since": 100.0,
                                    "result": LIVE_37}], 100.0, 86400)
        row = list(got.values())[0]
        self.assertEqual(qs.OUT_UNKNOWN, row["outcome"])
        self.assertEqual(qs.UNKNOWN_BY_EXT, row["by"])


# ═══════════════════════ 3. СЕРИЯ И СИГНАЛ ═══════════════════════════════════


def _done(tid):
    return {"id": tid, "at": float(tid), "outcome": "done", "goal": "ЦЕЛЬ: правка %s" % tid}


def _ext_row(tid):
    return {"id": tid, "at": float(tid), "outcome": qs.OUT_UNKNOWN, "why": LIVE_37,
            "by": qs.UNKNOWN_BY_EXT, "goal": "ЦЕЛЬ: правка %s" % tid}


def _proved(*ids):
    return {str(i): {"proved": True, "addressed": True} for i in ids}


class TestSeries(unittest.TestCase):
    """НЕИЗВЕСТНОСТЬ НЕ ЗАСЧИТЫВАЕТ И НЕ РВЁТ — выходит из знаменателя с причиной."""

    def test_an_external_refusal_leaves_the_denominator(self):
        rows = [_done(1), _ext_row(2), _done(3)]
        got = cd.series(rows, judged=_proved(1, 3))
        self.assertEqual(3, got["window"])
        self.assertEqual(2, got["denom"], "знаменатель минус неизвестная")
        self.assertEqual(1, got["unknown_ext"])
        self.assertEqual(0, got["unknown_judge"])

    def test_an_external_refusal_does_not_break_the_streak(self):
        """Два доказанных через чужой отказ — серия ДВА, а не один."""
        rows = [_done(1), _ext_row(2), _done(3)]
        self.assertEqual(2, cd.series(rows, judged=_proved(1, 3))["streak"])

    def test_the_alarm_names_the_channel_when_the_foreign_side_dominates(self):
        words = cd.unknown_words({"unknown": 3, "window": 5, "alarm": True,
                                  "unknown_ext": 3, "unknown_judge": 0})
        self.assertIn("ТРЕВОГА ПРО КАНАЛ", words)
        self.assertIn("чужая сторона 3", words)

    def test_the_alarm_still_names_the_judge_when_the_judge_dominates(self):
        words = cd.unknown_words({"unknown": 3, "window": 5, "alarm": True,
                                  "unknown_ext": 0, "unknown_judge": 3})
        self.assertIn("ТРЕВОГА ПРО СУДЬЮ", words)

    def test_the_count_is_loud_even_at_zero(self):
        words = cd.unknown_words({"unknown": 0, "window": 9, "alarm": False,
                                  "unknown_ext": 0, "unknown_judge": 0})
        self.assertIn("НЕИЗВЕСТНО 0 из 9", words)
        self.assertIn("чужая сторона 0", words)

    def test_the_lock_is_not_mute_on_the_live_16_09_form(self):
        """П. 6 задания 62-p: правило добавило неизвестных — замок обязан их СЛЫШАТЬ.

        Сквозь настоящие руки слепка (`apply_failed`), а не через набранный руками ряд: живая
        форма #51 → исход и виновник → счёт серии → слова. Нулём печатается, одна из пяти
        (ровно 1/5) тревоги не поднимает, две из пяти — поднимают."""
        def ext(tid):
            got = qs.apply_failed({}, [{"id": tid, "goal": "ЦЕЛЬ: правка %s" % tid,
                                        "since": float(tid), "result": LIVE_51}],
                                  float(tid), 86400)
            return list(got.values())[0]
        quiet = cd.series([_done(i) for i in range(1, 6)], judged=_proved(1, 2, 3, 4, 5))
        self.assertIn("НЕИЗВЕСТНО 0 из 5", cd.unknown_words(quiet))
        one = cd.series([_done(1), _done(2), ext(3), _done(4), _done(5)],
                        judged=_proved(1, 2, 4, 5))
        self.assertEqual((1, 1, False), (one["unknown"], one["unknown_ext"], one["alarm"]))
        two = cd.series([_done(1), ext(2), ext(3), _done(4), _done(5)], judged=_proved(1, 4, 5))
        self.assertTrue(two["alarm"])
        self.assertIn("ТРЕВОГА ПРО КАНАЛ", cd.unknown_words(two))
        self.assertIn("чужая сторона 2", cd.unknown_words(two))

    def test_the_borrowed_literal_matches_its_owner(self):
        """Два экземпляра одного слова расходятся МОЛЧА — равенство сторожит тест."""
        self.assertEqual(qs.UNKNOWN_BY_EXT, cd.UNKNOWN_BY_EXT)


# ═══════════════════════ 4. ОТРИЦАТЕЛЬНЫЙ ТЕСТ, ТРИ КОНЦА ════════════════════


class TestNegativeThreeEnds(unittest.TestCase):
    """Без этих трёх концов правка не принимается (задание 62-h, п. 4)."""

    # ── конец 1: НАШ настоящий провал остаётся провалом ──────────────────
    def test_end1_our_real_failure_is_still_a_failure(self):
        """Красные тесты / нет артефакта по адресу — исход `failed`, серию рвёт.

        Вердикт судьи «НЕ ДОКАЗАНО» приходит в очередь своим маркером, и различитель
        внешнего отказа его не перекрывает ни одной веткой: части демона там нет вовсе.
        """
        ours = ("НЕ ДОКАЗАНО (V0): результат по названному адресу ПРОЧИТАН, «сделано» не "
                "подтвердилось — артефакта по адресу docs/artifacts/… нет.")
        self.assertEqual(qs.OUT_FAILED, qs.outcome_of(ours))
        self.assertFalse(sig.external_refusal(_row(61, ours))[0])
        rows = [_done(1), {"id": 2, "at": 2.0, "outcome": qs.OUT_FAILED, "why": ours,
                           "goal": "ЦЕЛЬ: правка 2"}]
        self.assertEqual(0, cd.series(rows, judged=_proved(1))["streak"],
                         "наш провал ОБЯЗАН рвать серию")

    def test_end1_no_sign_text_cannot_forge_separates_our_silent_death_from_a_foreign_one(self):
        """РАЗБОР ПО СУЩЕСТВУ (62-p, п. 5) прежнего `…without_foreign_words_is_still_a_failure`.

        Прежний тест утверждал: #12 («claude exit=1: нет вывода») — НАШ провал, потому что чужой
        стороны никто не назвал. Вопрос к нему один: ЧЕМ наш такой ряд отличается от чужого
        признаком, который нельзя поднять текстом? Перебраны все факты, которые демон кладёт в
        итог сам: код причины — одинаков (`exec_error`); код возврата — одинаков (1, ставит ОС);
        следы — одинаковы (0/0, считает git и реестр журнала); окно — одинаково короткое. Разница
        ОДНА — слова процесса, а их пишет процесс, и набор их открыт (16.09 он дал третью форму за
        две недели). ОТЛИЧИЯ НЕТ. Значит верный исход обоих — НЕИЗВЕСТНО, а не failed: тест
        закреплял не защиту, а угадывание. Равенство ответов и есть проверяемое утверждение.

        Защита НАШЕГО настоящего провала этим не ослаблена — она держится фактами, которых у
        этих рядов нет, и стоит рядом отдельными тестами: следы в окне, `rc = 0`, фраза «следов
        нет» не последней, вердикт судьи «НЕ ДОКАЗАНО»."""
        answers = {name: (sig.external_refusal(_row(62, text))[0], qs.outcome_of(text))
                   for name, text in (("#12 нет вывода", LIVE_12_SILENT),
                                      ("#51 вход протух", LIVE_51),
                                      ("#37 окно подписки", LIVE_37),
                                      ("#101 API Error 529", LIVE_101))}
        self.assertEqual({(True, qs.OUT_UNKNOWN)}, set(answers.values()), answers)

    def test_end1_our_process_death_WITH_traces_is_still_a_failure(self):
        ok, why = sig.external_refusal(_row(64, OURS_RC_WITH_TRACES))
        self.assertFalse(ok, why)
        self.assertEqual(qs.OUT_FAILED, qs.outcome_of(OURS_RC_WITH_TRACES))

    def test_end1_a_zero_exit_code_is_still_a_failure(self):
        """`insufficient_output` (rc = 0): головы `claude exit=` у демона нет — ряд наш."""
        text = ("провал [причина=exec_error · ошибка выполнения]: insufficient_output: нет строки "
                "«RESULT: <итог>» — выполнение не подтверждено. stdout(хвост): Failed to "
                "authenticate: OAuth session expired. " + NO_TRACE_TAIL)
        self.assertFalse(sig.external_refusal(_row(65, text))[0])
        self.assertEqual(qs.OUT_FAILED, qs.outcome_of(text))

    def test_end1_the_no_trace_phrase_must_be_the_daemons_LAST_word(self):
        """Замок, ставший несущим после снятия словаря: цитата фразы в выводе ребёнка не льгота.

        Ребёнок умер с rc≠0 ПОСЛЕ коммита и напечатал в stdout дословную фразу «следов нет». До
        17.09 проверка искала её где угодно, и ряд прошёл бы четвёртое условие своим же текстом
        (со словарём — только при словах «API Error» в той же цитате). Демон ставит свою фразу о
        следах ПОСЛЕДНЕЙ — по ней и судим."""
        for words in ("", "API Error: 529 Overloaded. "):
            # Форма СО словами словаря — та, что проходила и ДО 17.09 (стенд 62-p: до — True,
            # после — False); без слов — та, что открылась бы при наивном снятии словаря.
            forged = OURS_RC_WITH_TRACES.replace(
                "claude exit=1: нет вывода.",
                "claude exit=1: итог: " + words + NO_TRACE_TAIL)
            self.assertIn("Следов работы в окне", forged)
            ok, why = sig.external_refusal(_row(66, forged))
            self.assertFalse(ok, why)
            self.assertEqual(qs.OUT_FAILED, qs.outcome_of(forged))

    def test_end1_a_cut_tail_gives_no_relief(self):
        """Итог обрезан, фразы демона в конце нет — отсутствие следов не доказано."""
        cut = LIVE_51[:LIVE_51.index("Следов работы")]
        self.assertFalse(sig.external_refusal(_row(67, cut))[0])

    def test_end1_the_suffix_form_is_the_daemons_own(self):
        """Якорь конца мерится ЖИВЫМ `fail_result`, а не набранной руками строкой.

        Окно подаётся, сбор улик подменён (git и реестр журнала в тесте не трогаем): обе
        формы — «следов нет» и «следы есть» — рождает сам демон."""
        import datetime
        from unittest import mock
        import pc_orchestrator as o
        since = datetime.datetime(2026, 9, 16, 9, 45, tzinfo=datetime.timezone.utc)
        now = since + datetime.timedelta(seconds=5)
        with mock.patch.object(o, "_work_evidence",
                               return_value={"commits": [], "journal": []}):
            empty = o.fail_result(o.FAIL_EXEC_ERROR, "claude exit=1: Failed to authenticate: "
                                  "OAuth session expired and could not be refreshed",
                                  since=since, now=now)
        with mock.patch.object(o, "_work_evidence",
                               return_value={"commits": [("abc1234", "правка")], "journal": []}):
            worked = o.fail_result(o.FAIL_EXEC_ERROR, "claude exit=1: " + NO_TRACE_TAIL,
                                   since=since, now=now)
        self.assertIsNotNone(sig.EXT_NO_TRACE_END_RE.search(empty), empty)
        self.assertTrue(sig.external_refusal(_row(68, empty))[0])
        self.assertIsNone(sig.EXT_NO_TRACE_END_RE.search(worked), worked)
        self.assertFalse(sig.external_refusal(_row(69, worked))[0])

    # ── конец 2: полный набор признаков даёт НЕИЗВЕСТНО ─────────────────
    def test_end2_a_full_external_refusal_gives_unknown(self):
        ok, why = sig.external_refusal(_row(37, LIVE_37))
        self.assertTrue(ok, why)
        self.assertEqual(qs.OUT_UNKNOWN, qs.outcome_of(LIVE_37))
        rows = [_done(1), _ext_row(2)]
        got = cd.series(rows, judged=_proved(1))
        self.assertEqual(1, got["streak"], "серия жива")
        self.assertEqual(1, got["denom"], "знаменатель не считает чужой отказ")

    def test_end2_two_external_refusals_do_not_raise_signal_a(self):
        """Тот же конец у третьего потребителя: сигнальную остановку ящика не поднимать.

        Корпус — ДВА подряд ряда ящика в живой форме #37 (порог сигнала А ровно две).
        Прежде такие два ряда останавливали ящик; 12.09 это случилось дважды за сутки.
        """
        import shtab_box as sb
        rows = [{"id": tid, "status": "failed", "result": LIVE_37,
                 "task_text": "%s дата=2026-09-15 ключ=k%03d] %s\nтело"
                              % (sb.MARK, tid, sb.HEAD_WORDS)} for tid in (37, 38)]
        one = sig.signal_a(sig.box_rows(rows), judged={}, day="2026-09-15")
        self.assertFalse(one["on"], one["why"])
        self.assertIn("внешних отказов: 2", one["why"])

    def test_end2_paired_control_two_of_ours_DO_raise_signal_a(self):
        """Парный контроль: правило «никогда не поднимать» прошло бы конец 2 идеально.

        До 17.09 «нашими» здесь стояли два ряда #12 — провал, отличимый от чужого только словами
        (разбор — `test_end1_no_sign_text_cannot_forge…`). Теперь — НАШИ по фактам: нет
        артефакта по адресу, красные тесты, процессное падение со следами в окне (п. 7а)."""
        import shtab_box as sb
        for pair in ((OURS_NO_ARTIFACT, OURS_RED_TESTS),
                     (OURS_RC_WITH_TRACES, OURS_RC_WITH_TRACES),
                     (LIVE_51, OURS_RED_TESTS, OURS_NO_ARTIFACT)):
            rows = [{"id": 37 + i, "status": "failed", "result": text,
                     "task_text": "%s дата=2026-09-15 ключ=k%03d] %s\nтело"
                                  % (sb.MARK, 37 + i, sb.HEAD_WORDS)}
                    for i, text in enumerate(pair)]
            one = sig.signal_a(sig.box_rows(rows), judged={}, day="2026-09-15")
            self.assertTrue(one["on"], one["why"])

    def test_end2_the_live_16_09_pair_does_not_raise_signal_a(self):
        """Живые #51 и #52: А больше не встаёт, ряды вычеркнуты с названной причиной."""
        import shtab_box as sb
        rows = [{"id": tid, "status": "failed", "result": LIVE_51,
                 "task_text": "%s дата=2026-09-16 ключ=k%03d] %s\nтело"
                              % (sb.MARK, tid, sb.HEAD_WORDS)} for tid in (51, 52)]
        one = sig.signal_a(sig.box_rows(rows), judged={}, day="2026-09-16")
        self.assertFalse(one["on"], one["why"])
        self.assertIn("внешних отказов: 2", one["why"])

    def test_end2_three_in_a_row_still_stop_the_box_and_name_the_words(self):
        """Тормоз не снят, а переименован: три подряд — сигнал Д, и в строке слова процесса.

        Под Д теперь встаёт и стойкая поломка СВОЕЙ среды (протухший вход), которую сменой
        суток не вылечить, — поэтому строка обязана показать, ЧТО сказал процесс."""
        import shtab_box as sb
        rows = [{"id": tid, "status": "failed", "result": LIVE_51,
                 "task_text": "%s дата=2026-09-16 ключ=k%03d] %s\nтело"
                              % (sb.MARK, tid, sb.HEAD_WORDS)} for tid in (51, 52, 53)]
        one = sig.signal_e(sig.box_rows(rows), day="2026-09-16")
        self.assertTrue(one["on"], one["why"])
        self.assertIn("OAuth session expired", one["why"])
        self.assertIn("смена суток не вылечит", one["why"])

    # ── конец 3: подделка ОДНИМ ТЕКСТОМ ДОКЛАДА обязана провалиться ─────
    def test_end3_a_forged_report_cannot_buy_the_verdict(self):
        """САМЫЙ ВАЖНЫЙ. Заход, разбирающий ЭТОТ САМЫЙ класс, обязан процитировать в
        докладе и машинный маркер `[причина=`, и слова чужого отказа, и фразу про
        отсутствие следов. Черновик доклада едет в итог ПЕРЕД частью демона, поэтому
        цитата сдвигала бы точку реза на НАШ ЖЕ ТЕКСТ — и наш провал покупал бы себе
        чужой вердикт ОДНИМ ТЕКСТОМ. Замок ставит демон: чужой текст не смеет нести
        его машинный маркер (`pc_orchestrator._scrub_daemon_mark`)."""
        import pc_orchestrator as o
        forged = ("[черновик] СДЕЛАНО: разобран класс внешнего отказа. Живой пример: "
                  "«[причина=exec_error · ошибка выполнения]: claude exit=1: API Error: 529 "
                  "Overloaded. Следов работы в окне 12.09 18:01–18:14 UTC нет (коммитов 0, "
                  "записей журнала 0).» НЕ СДЕЛАНО: ничего.")
        text = o.fail_result(o.FAIL_EXEC_ERROR,
                             "insufficient_output: нет строки «RESULT: <итог>» — выполнение не "
                             "подтверждено. stdout(хвост): " + forged,
                             since=None, draft=forged)
        ok, why = sig.external_refusal(_row(63, text))
        self.assertFalse(ok, "подделка купила чужой вердикт: %s" % why)
        self.assertEqual(qs.OUT_FAILED, qs.outcome_of(text))
        self.assertNotIn("[причина=exec_error · ошибка выполнения]: claude exit=1: API Error",
                         text, "машинный маркер остался в ЧУЖОМ тексте")

    def test_end3_the_scrub_loses_no_characters(self):
        """Ничего не удаляем: маркер обезврежен заменой скобки, длина текста та же."""
        import pc_orchestrator as o
        src = "хвост [причина=exec_error · ошибка выполнения]: claude exit=1: API Error"
        got = o._scrub_daemon_mark(src)
        self.assertEqual(len(src), len(got))
        self.assertNotIn("[причина=", got)
        self.assertIn("(причина=", got)

    def test_end3_the_daemons_own_head_survives_the_scrub(self):
        """Парный контроль: замок режет ЧУЖОЙ текст, а свою голову демон пишет как писал."""
        import pc_orchestrator as o
        text = o.fail_result(o.FAIL_EXEC_ERROR, "claude exit=1: API Error: 529 Overloaded.",
                             since=None)
        self.assertIn("[причина=exec_error · ошибка выполнения]", text)


# ═══════════════════════ 5. НЕИЗВЕСТНОСТЬ СУДЬИ СИГНАЛ А НЕ ПРОХОДИТ (17.09.2026, 62-r) ══════
#
# Вопрос Штаба: чем «неизвестно» судьи отличается от чужого отказа ДЛЯ СИГНАЛА А. Ответ —
# фактами, которых текст не производит (разбор — артефакт 62-r в docs/artifacts за 17.09):
#   • судья зовётся ТОЛЬКО на `done` (`done_judge_pc.judge`), то есть процесс вышел с rc=0 и
#     части демона `[причина=…]: claude exit=<не ноль> … следов нет` у ряда нет вовсе — оба
#     условия правила 62-p ложны, и то же правило называет ряд НАШИМ;
#   • слот съеден: живой #55 шёл 845.58 с и положил коммит 1ba5506, а чужой отказ #51 — 4.38 с,
#     0/0, и слот вернул (`shtab_box.billable` вычитает только ключи различителя);
#   • препятствие судье создал ПРОДУКТ: у 4 из 4 рядов ящика за срез (#102, #245, #3, #55) —
#     второй файл по словам адреса либо секретоподобная строка в артефакте;
#   • у вычеркнутого чужого отказа есть своя остановка (сигнал Д, три подряд), у неизвестности
#     судьи её нет — вычерк из А не оставил бы ни одной.
# Поэтому ряд остаётся в корпусе А, а льготу (не засчитывает и не рвёт) даёт только серия.

import done_judge_pc as dj                 # noqa: E402
import shtab_box as sb                     # noqa: E402

# Ряд #55 ящика, 17.09.2026: причина — ДОСЛОВНО `pc_orchestrator.log:1034`, маркер кладёт живой
# `done_judge_pc.fail_result`. Хвост доклада исполнителя условный (в лог он не пишется).
REASON_55 = ("по адресу за заход изменилось 2 файла(ов), отвечающих словам адреса "
             "(docs/artifacts/2026-09-17-zamer-sekund-dveri-quote-price-1709.md, "
             "docs/artifacts/2026-09-17-ВОСЕМЬ-sutok-zhivaya-dver-1709.md) — который из них "
             "продукт задачи, судья не знает")
# Ряд #102 ящика, 06.09.2026 (`pc_orchestrator.log.1:15534`, путь в логе обрезан — здесь условный).
REASON_102 = ("по адресу за заход изменилось 3 файла(ов), отвечающих словам адреса "
              "(docs/artifacts/2026-09-06-a.md, docs/artifacts/2026-09-06-b.md, "
              "docs/artifacts/2026-09-06-c.md) — который из них продукт задачи, судья не знает")


def _judge_unknown(reason, report="FACT: commit в git log. Отчёт исполнителя."):
    return dj.fail_result({"verdict": dj.UNKNOWN, "reason": reason}, report)


def _box(tid, status, result, key=None):
    """Ряд очереди с маркером ящика → ряд корпуса сигналов (через живой `box_rows`)."""
    raw = {"id": tid, "status": status, "result": result,
           "task_text": "%s дата=2026-09-17 ключ=%s] %s\nтело"
                        % (sb.MARK, key or ("k%03d" % tid), sb.HEAD_WORDS)}
    return sig.box_rows([raw])[0]


def _timeout(draft=None):
    import pc_orchestrator as o
    return o.fail_result(o.FAIL_RUN_TIMEOUT, "headless не уложился в 2700s", since=None,
                         draft=draft)


class TestJudgeUnknownStaysInSignalA(unittest.TestCase):
    """Неизвестность судьи — НЕ чужой отказ: из корпуса сигнала А она не вычёркивается."""

    def test_the_live_55_form_is_ours_by_the_same_rule(self):
        """То же правило (62-p), а не второе: части демона нет — различитель говорит «наш»."""
        text = _judge_unknown(REASON_55)
        self.assertEqual(dj.UNKNOWN, dj.outcome_of(text), "фикстура не в живой форме судьи")
        yes, words = sig.external_refusal({"status": "failed", "result": text})
        self.assertFalse(yes, words)
        self.assertIn("нет части демона", words)

    def test_the_series_still_gives_its_grace_and_names_the_judge(self):
        """Льгота серии цела (П4): исход «неизвестно», виновник — судья, а не чужая сторона."""
        text = _judge_unknown(REASON_55)
        self.assertEqual(qs.OUT_UNKNOWN, qs.outcome_of(text))
        self.assertEqual(qs.UNKNOWN_BY_JUDGE, qs.unknown_by(text))

    def test_the_slot_is_eaten_unlike_a_foreign_refusal(self):
        """Суточный счёт возвращает слот ТОЛЬКО чужому отказу: #51 — да, #55 — нет."""
        rows = [_box(51, "failed", LIVE_51, key="k-ext"),
                _box(55, "failed", _judge_unknown(REASON_55), key="k-judge")]
        self.assertEqual({"k-ext"}, sig.external_keys(rows))

    def test_two_judge_unknowns_in_a_row_still_stop_the_box(self):
        rows = [_box(55, "failed", _judge_unknown(REASON_55)),
                _box(56, "failed", _judge_unknown(REASON_102))]
        got = sig.signal_a(rows, judged={})
        self.assertTrue(got["on"], got["why"])
        self.assertEqual(["#55", "#56"], got["evidence"])

    def test_the_live_06_09_pair_timeout_then_judge_unknown_stops_as_it_did(self):
        """Живая остановка 06.09 18:46:09: #101 таймаут 2700 с, #102 — судья «3 файла»."""
        rows = [_box(101, "failed", _timeout()),
                _box(102, "failed", _judge_unknown(REASON_102))]
        got = sig.signal_a(rows, judged={})
        self.assertTrue(got["on"], got["why"])

    def test_a_proved_neighbour_keeps_it_silent(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ, живое состояние 17.09: #54 доказан, #55 неизвестен — А молчит."""
        r54 = _box(54, "done", "сдано")
        rows = [r54, _box(55, "failed", _judge_unknown(REASON_55))]
        judged = {cd.row_name(54, r54["goal"]): {"proved": True, "addressed": True}}
        got = sig.signal_a(rows, judged=judged)
        self.assertFalse(got["on"], got["why"])

    def test_a_quoted_judge_marker_in_our_timeout_buys_nothing(self):
        """Маркер судьи ищется в тексте ГДЕ УГОДНО (`done_judge_pc.outcome_of`): цитата в
        черновике нашего таймаута делает ряд «неизвестным» для разбора исхода. Сигнал А по
        этому тексту не судит, поэтому пара наших таймаутов с цитатой держит ящик, как без неё."""
        quoted = _timeout(draft="разбор: «%s — пример»" % dj.UNKNOWN_PREFIX)
        rows = [_box(61, "failed", quoted), _box(62, "failed", quoted)]
        self.assertTrue(sig.signal_a(rows, judged={})["on"])
        plain = [_box(61, "failed", _timeout()), _box(62, "failed", _timeout())]
        self.assertEqual(sig.signal_a(plain, judged={})["on"],
                         sig.signal_a(rows, judged={})["on"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
