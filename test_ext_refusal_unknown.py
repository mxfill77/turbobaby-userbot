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
# Ряд #12, 03.09.2026 — НАШ провал под тем же кодом: чужой стороны не назвал никто.
LIVE_12_OURS = ("провал [причина=exec_error · ошибка выполнения]: claude exit=1: нет вывода. "
                "Следов работы в окне 02.09 21:59–21:59 UTC нет (коммитов 0, записей "
                "журнала 0).")
NO_TRACE_TAIL = "Следов работы в окне 15.09 03:10–03:14 UTC нет (коммитов 0, записей журнала 0)."


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

    def test_our_own_exec_error_stays_failed(self):
        self.assertEqual(qs.OUT_FAILED, qs.outcome_of(LIVE_12_OURS))

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

    def test_end1_our_exec_error_without_foreign_words_is_still_a_failure(self):
        """Тот же конец с другой стороны: код тот же, чужой стороны никто не назвал."""
        self.assertEqual(qs.OUT_FAILED, qs.outcome_of(LIVE_12_OURS))
        self.assertFalse(sig.external_refusal(_row(62, LIVE_12_OURS))[0])

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
        """Парный контроль: правило «никогда не поднимать» прошло бы конец 2 идеально."""
        import shtab_box as sb
        rows = [{"id": tid, "status": "failed", "result": LIVE_12_OURS,
                 "task_text": "%s дата=2026-09-15 ключ=k%03d] %s\nтело"
                              % (sb.MARK, tid, sb.HEAD_WORDS)} for tid in (37, 38)]
        one = sig.signal_a(sig.box_rows(rows), judged={}, day="2026-09-15")
        self.assertTrue(one["on"], one["why"])

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
