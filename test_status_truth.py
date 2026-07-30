# -*- coding: utf-8 -*-
"""
test_status_truth.py — голдены ИНСТРУМЕНТА ЗАМЕРА (diag_status_truth).

Зачем тесты у диагностики: первый прогон замера 30.07.2026 насчитал провалов на ОДИН больше,
потому что свободный подстрочный regex поймал строку ИЗ ЦИТАТЫ внутри чужого result
(«… stuck-single: id=5 in_progress 5429с > 5400с → failed» — это ТЕКСТ отчёта задачи 51, а не
её собственный провал). Инструмент, который тихо считает не то, врёт ровно так же, как статус,
который мы чиним. Правило репо №7: числа — факт, поэтому у чисел есть голдены.

Все фразы ниже — ДОСЛОВНЫЕ строки из живого журнала и живого pc_orchestrator.log за 29–30.07.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_status_truth -v
"""

import datetime
import unittest

import diag_status_truth as d

UTC = datetime.timezone.utc


def ts(day, hh, mm):
    return datetime.datetime(2026, 7, day, hh, mm, tzinfo=UTC)


class TestJournalParsing(unittest.TestCase):
    """Разбор строк журнала: заголовок, claim, провал — и НЕ-провал в цитате."""

    def test_header_type_and_stamp(self):
        m = d._J_HEAD.match("NOTE 2026-07-30 07:04 UTC: Orchestrator: задача #54 → failed · ⏱ таймаут")
        self.assertEqual(m.group(1), "NOTE")
        self.assertEqual(d.journal_ts(m.group(2)), ts(30, 7, 4))
        self.assertTrue(m.group(3).startswith("Orchestrator:"))

    def test_claim_line(self):
        self.assertEqual(d._J_CLAIM.match("Orchestrator: взял задачу #61 (in_progress)").group(1), "61")

    def test_fail_line_plain(self):
        m = d._J_FAIL.match("Orchestrator: задача #54 → failed · ⏱ подтверждение не получено за 30 мин")
        self.assertEqual(m.group(1), "54")

    def test_fail_line_with_note(self):
        for line, tid in (("Orchestrator: задача #55 (approved) → failed · ✋ одобрено, но…", "55"),
                          ("Orchestrator: задача #5 (одиночка) → failed по ПК-таймауту (5429с)", "5"),
                          ("Orchestrator: смоук-шаг #12 → failed · смоук красный", "12")):
            self.assertEqual(d._J_FAIL.match(line).group(1), tid, line)

    def test_quoted_failure_inside_result_is_not_a_failure(self):
        # ЖИВОЙ ложняк: это отчёт ЗАДАЧИ 51 (done), внутри которого процитирован чужой лог
        quoted = ("Orchestrator: задача #51 → done · Разведка ревизора … 1390 28.07 21:12:14 "
                  "stuck-single: id=5 in_progress 5429с > 5400с → failed …")
        self.assertIsNone(d._J_FAIL.match(quoted))
        self.assertEqual(d._J_TERM.match(quoted).group(1), "51")   # терминал — у 51, и он «done»

    def test_done_line_counts_as_terminal(self):
        self.assertEqual(d._J_TERM.match("Orchestrator: задача #65 → done · всё ок").group(1), "65")


class TestDaemonLogParsing(unittest.TestCase):
    """Лог демона: локальное время → UTC, claim/терминал/провал по ЯКОРЮ строки."""

    def test_local_time_converted_to_utc(self):
        # сверка по живой паре из одной строки METRICS: 13:33:47 локально = 06:33:47 UTC
        line = "2026-07-30 13:33:47,551 INFO CLAIM id=54 in_progress"
        m = d._L_TS.match(line)
        got = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=d.PC_TZ).astimezone(UTC)
        self.assertEqual(got, ts(30, 6, 33).replace(second=47))

    def test_claim_and_fail_anchors(self):
        self.assertEqual(d._L_CLAIM.match("CLAIM id=61 in_progress").group(1), "61")
        self.assertEqual(d._L_FAIL.match("NEEDS_APPROVAL id=54 таймаут (>1800s) → failed").group(1), "54")
        self.assertEqual(d._L_FAIL.match("stuck-single: id=5 in_progress 5429с > 5400с → failed"
                                         " (ПК-ливнесс одиночки)").group(1), "5")

    def test_fail_anchor_ignores_mid_line_mentions(self):
        # «failed» посреди чужой строки (например, в тексте METRICS/диагноза) провалом не считаем
        self.assertIsNone(d._L_FAIL.match("METRICS task=54 lane=pc outcome=failed attempts=1"))
        self.assertIsNone(d._L_FAIL.match("ревизор: находка про exited status=failed — 0"))


class TestClassify(unittest.TestCase):
    """Арифметика замера: улика внутри окна = «работа была», вне окна — чужая."""

    def setUp(self):
        self.now = ts(30, 14, 0)
        self.commits = [(ts(30, 6, 32), "c00bb08", "полоса C под стражем"),
                        (ts(30, 13, 14), "a3f75dd", "гард PEB")]

    def test_commit_inside_window_marks_lie(self):
        lying, honest, unknown, _ = d.classify(
            [], {54: ts(30, 6, 15)}, {54: (ts(30, 7, 4), "⏱ таймаут")}, set(), self.commits, now=self.now)
        self.assertEqual([r[0] for r in lying], [54])
        self.assertEqual(lying[0][3][0][1], "c00bb08")
        self.assertEqual((honest, unknown), ([], []))

    def test_commit_outside_window_is_not_evidence(self):
        lying, honest, _u, _o = d.classify(
            [], {52: ts(30, 8, 0)}, {52: (ts(30, 8, 30), "claude exit=1")}, set(), self.commits, now=self.now)
        self.assertEqual(lying, [])
        self.assertEqual([r[0] for r in honest], [52])      # честный провал не записываем в ложь

    def test_journal_line_is_evidence_but_orchestrator_note_is_not(self):
        rows = [("DONE", ts(30, 12, 45), "ARTIFACT тренажёр-вердикт → docs/artifacts/…"),
                ("NOTE", ts(30, 12, 42), "Orchestrator: взял задачу #61 (in_progress)")]
        lying, _h, _u, _o = d.classify(
            rows, {61: ts(30, 12, 41)}, {61: (ts(30, 12, 50), "⏱ сердцебиение")}, set(), [], now=self.now)
        self.assertEqual([x[2] for x in lying[0][4]], ["ARTIFACT тренажёр-вердикт → docs/artifacts/…"])

    def test_no_claim_means_unknown_window_not_a_verdict(self):
        _l, _h, unknown, _o = d.classify([], {}, {77: (ts(30, 9, 0), "боль")}, set(), self.commits,
                                         now=self.now)
        self.assertEqual([r[0] for r in unknown], [77])

    def test_claimed_without_terminal_is_orphan(self):
        # ровно случай задачи 61: клеймили, статус поставила чужая полоса, на ПК терминала нет
        _l, _h, _u, orphan = d.classify([], {61: ts(30, 12, 41)}, {}, set(), self.commits, now=self.now)
        self.assertEqual([r[0] for r in orphan], [61])
        self.assertEqual([c[1] for c in orphan[0][2]], ["a3f75dd"])

    def test_terminal_seen_is_not_orphan(self):
        _l, _h, _u, orphan = d.classify([], {65: ts(30, 12, 41)}, {}, {65}, self.commits, now=self.now)
        self.assertEqual(orphan, [])

    def test_orphan_window_capped(self):
        # окно сироты ограничено одним прогоном — иначе ей припишут все коммиты суток
        _l, _h, _u, orphan = d.classify([], {44: ts(29, 18, 24)}, {}, set(), self.commits, now=self.now)
        self.assertEqual(orphan[0][2], [])


if __name__ == "__main__":
    unittest.main()
