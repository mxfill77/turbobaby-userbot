# -*- coding: utf-8 -*-
"""Набор сборки трёх строк в начале сообщения о закрытии (`close_msg_pc`).

ЧТО ЭТОТ НАБОР СТЕРЕЖЁТ, ОДНОЙ СТРОКОЙ: чтобы владелец, открыв телефон, читал
исход, а не машинную середину отчёта, — и чтобы шапка НИКОГДА не звучала бодрее,
чем вердикт судьи.

Два теста здесь главные, и они названы прямо:

* ``test_negative_no_goal_no_verdict_says_not_named`` — обязательный отрицательный
  тест задания. Задание без раздела цели и отчёт без вердикта дают три строки со
  словами «не назван», а не подставляют первую попавшуюся фразу отчёта;
* ``test_cheerful_report_cannot_dress_up_an_unproven_close`` — предсмертный взгляд
  задания: отчёт, у которого первый абзац сияет, при непройденном суде обязан дать
  «ВЫШЛО: неизвестно».

Набор ЧИСТЫЙ: ни моста, ни диска, ни очереди. Единственный файл, который он
открывает, — исходник демона, и только чтобы проверить, что врезка стои́т в ОДНОМ
месте (иначе «одна единица» задания жила бы обещанием, а не проверкой).
"""

import os
import re
import unittest

import close_msg_pc as cm
import done_judge_pc

REPO = os.path.dirname(os.path.abspath(__file__))

# Живая форма задания из ящика Штаба (04.09.2026, ключ close-msg.0904) — шапка ряда
# плюс тело с разделом цели. Голден снят с ДОСЛОВНОЙ постановки, а не сочинён.
TASK = (
    "[от Штаба дата=2026-09-04 ключ=close-msg.0904] ЗАДАНИЕ ШТАБА ИЗ ЯЩИКА\n"
    "Источник: документ shtab_task_close-msg.0904 папки мозга (file id 1r6Fn…);\n"
    "Владелец задачу НЕ пересылал; карточки, ворота и гард работают как обычно.\n"
    "\n"
    "ПОЛОСА: пк\n"
    "\n"
    "ЦЕЛЬ. Сообщение о закрытии задачи начинается тремя строками на человеческом "
    "языке — ЧТО СПРАШИВАЛИ, ЧТО ВЫШЛО, ЧТО ДАЛЬШЕ, — и только потом идёт техника.\n"
    "\n"
    "ЗАПРЕТЫ (стандартный блок): ничего не удалять; .env не читать.\n"
)

# Живая голова зелёного закрытия (замер 04.09 по pc_orchestrator.log, задача 158).
JUDGE_OK = ("[V0 судит done: сделано · V0: PROVEN / all_gates_passed по адресу "
            "«docs/artifacts/2026-09-04-потолок-времени-на-мост.md»]")


def report_ok(body="Работа сделана: 3 файла, 12 тестов зелены."):
    return JUDGE_OK + "\n" + body


class Goal(unittest.TestCase):
    """СПРАШИВАЛИ — первая строка раздела «ЦЕЛЬ», дословно."""

    def test_goal_on_the_same_line_as_the_header(self):
        self.assertTrue(cm.goal(TASK).startswith("Сообщение о закрытии задачи начинается"))

    def test_goal_is_verbatim_not_reworded(self):
        self.assertIn(cm.goal(TASK), TASK)

    def test_header_on_its_own_line_takes_the_next_nonempty(self):
        self.assertEqual(cm.goal("ЦЕЛЬ:\n\n   Снять потолок ящика.\n"), "Снять потолок ящика.")

    def test_markdown_header_is_read_too(self):
        self.assertEqual(cm.goal("## **ЦЕЛЬ** — Убрать дубль.\nтехника"), "Убрать дубль.")

    def test_two_word_header_is_read(self):
        self.assertEqual(cm.goal("ЦЕЛЬ ШАГА: Проверить премису."), "Проверить премису.")

    def test_no_goal_section_is_none_not_a_neighbour_phrase(self):
        text = "ПОЛОСА: пк\nЧТО СДЕЛАТЬ\n1. Открыть адрес результата.\n"
        self.assertIsNone(cm.goal(text))

    def test_lowercase_prose_is_not_a_header(self):
        self.assertIsNone(cm.goal("наша цель: была другой, и это проза, а не раздел"))

    def test_empty_task_is_none(self):
        self.assertIsNone(cm.goal(""))
        self.assertIsNone(cm.goal(None))


class Verdict(unittest.TestCase):
    """ВЫШЛО — слово судьи закрытия, ДОСЛОВНО."""

    def test_judge_line_form_is_taken_from_the_judge_itself(self):
        # Форма не набрана литералом: строку строит сам судья, мы её только читаем.
        line = done_judge_pc.line({"verdict": done_judge_pc.DONE, "reason": "V0: PROVEN"})
        self.assertEqual(cm.verdict_word(line + "\nотчёт"), done_judge_pc.DONE)

    def test_unknown_verdict_is_read_word_for_word(self):
        line = done_judge_pc.line({"verdict": done_judge_pc.UNKNOWN, "reason": "адреса нет"})
        self.assertEqual(cm.verdict_word(line), done_judge_pc.UNKNOWN)

    def test_unknown_prefix_of_failed_close_is_a_verdict_too(self):
        text = done_judge_pc.fail_result({"reason": "по адресу пусто"}, "отчёт исполнителя")
        self.assertEqual(cm.verdict_word(text), done_judge_pc.UNKNOWN)

    def test_report_without_a_judge_line_has_no_verdict(self):
        self.assertIsNone(cm.verdict_word("Всё получилось прекрасно, 20 из 20."))

    def test_empty_report_has_no_verdict(self):
        self.assertIsNone(cm.verdict_word(""))
        self.assertIsNone(cm.verdict_word(None))


class Numbers(unittest.TestCase):
    """До двух чисел ИЗ ОТЧЁТА, дословно."""

    def test_at_most_two(self):
        self.assertEqual(cm.numbers("1 и 2 и 3 и 4"), ["1", "2"])

    def test_verbatim_with_percent_and_comma(self):
        self.assertEqual(cm.numbers("покрытие 3,9% при пороге 5"), ["3,9%", "5"])

    def test_repeat_is_one_number_not_two(self):
        self.assertEqual(cm.numbers("4500 при потолке 4500, срезано 101"), ["4500", "101"])

    def test_dates_times_and_hashes_are_not_numbers_for_a_human(self):
        self.assertEqual(cm.numbers("коммит 42687d8 от 2026-09-04 05:06 — файлов 7"), ["7"])

    def test_numbers_of_the_judge_line_are_not_the_executors_numbers(self):
        # В строке судьи живёт «V0», путь и дата — ни одно из этих чисел не является
        # числом ОТЧЁТА, а задание требует брать числа именно у исполнителя.
        self.assertEqual(cm.numbers(report_ok("Сделано: 3 файла.")), ["3"])

    def test_no_numbers_is_an_empty_list_not_a_zero(self):
        self.assertEqual(cm.numbers("чисел здесь нет"), [])


class NextStep(unittest.TestCase):
    """ДАЛЬШЕ — по состоянию ряда очереди, а не по прозе отчёта."""

    def test_needs_approval_waits_for_the_owner(self):
        self.assertEqual(cm.next_step("needs_approval"), cm.NEXT_WAIT)

    def test_done_goes_on(self):
        self.assertEqual(cm.next_step("done"), cm.NEXT_GO)

    def test_failed_names_the_reason_from_the_daemons_own_head(self):
        got = cm.next_step("failed", "⏱ провал [причина=run_timeout · таймаут прогона]: …")
        self.assertEqual(got, cm.NEXT_STOP % "таймаут прогона")

    def test_failed_by_the_judge_quotes_the_judges_own_reason(self):
        # Самый частый провал полосы приходит БЕЗ головы `[причина=…]` — её кладёт
        # только демон. Не читай мы причину судьи, владельцу докладывали бы
        # «причина не названа» при названной причине.
        text = done_judge_pc.fail_result({"reason": "по адресу ПУСТО"}, "отчёт")
        self.assertEqual(cm.next_step("failed", text), cm.NEXT_STOP % "по адресу ПУСТО")

    def test_failed_without_a_head_says_so(self):
        self.assertEqual(cm.next_step("failed", "просто упало"), cm.NEXT_STOP_MUTE)

    def test_unknown_status_is_unknown_not_the_nearest_of_three(self):
        for st in ("", None, "wat", "in_progress"):
            self.assertEqual(cm.next_step(st, "текст"), cm.NEXT_UNKNOWN, st)

    def test_prose_saying_i_wait_does_not_move_the_row(self):
        # Ждёт РЯД, а не текст: отчёт может писать «жду „да“» сколько угодно.
        self.assertEqual(cm.next_step("done", "жду решения владельца по этому вопросу"),
                         cm.NEXT_GO)


class Lead(unittest.TestCase):
    """Три строки целиком."""

    def setUp(self):
        self.lines = cm.lead(TASK, report_ok(), "done").split("\n")

    def test_exactly_three_lines_in_the_named_order(self):
        self.assertEqual(len(self.lines), 3)
        self.assertTrue(self.lines[0].startswith(cm.L_ASK))
        self.assertTrue(self.lines[1].startswith(cm.L_GOT))
        self.assertTrue(self.lines[2].startswith(cm.L_NEXT))

    def test_every_line_fits_a_phone_line(self):
        for ln in self.lines:
            self.assertLessEqual(len(ln), cm.LINE_MAX, ln)

    def test_no_line_is_empty(self):
        for ln in self.lines:
            self.assertTrue(ln.split(": ", 1)[1].strip(), ln)

    def test_outcome_word_is_the_judges_own(self):
        self.assertIn(done_judge_pc.DONE, self.lines[1])

    def test_numbers_are_marked_as_a_quote_of_the_report(self):
        self.assertIn("из отчёта:", self.lines[1])
        self.assertIn("3", self.lines[1])

    def test_long_goal_is_cut_by_word_with_an_ellipsis(self):
        long_goal = "ЦЕЛЬ. " + "слово " * 40
        ln = cm.lead(long_goal, report_ok(), "done").split("\n")[0]
        self.assertLessEqual(len(ln), cm.LINE_MAX)
        self.assertTrue(ln.endswith("…"))
        self.assertFalse(ln.endswith("сло…"), ln)


class NegativeAndDeathLook(unittest.TestCase):
    """Два обязательных теста задания."""

    def test_negative_no_goal_no_verdict_says_not_named(self):
        task = "ПОЛОСА: пк\nЧТО СДЕЛАТЬ\n1. Померить.\nАДРЕС РЕЗУЛЬТАТА: файл в docs\n"
        report = ("Всё отлично: 12 проверок зелены, регресс чист, полёт нормальный.\n"
                  "RESULT: посчитано")
        lines = cm.lead(task, report, "done").split("\n")
        self.assertIn("не назван", lines[0])
        self.assertIn("не назван", lines[1])
        # …и ни одного слова отчёта в этих двух строках: подстановка «первой
        # попавшейся фразы» — ровно то, что задание запретило.
        for word in ("отлично", "зелены", "регресс", "нормальный", "посчитано"):
            self.assertNotIn(word, lines[0] + lines[1])

    def test_cheerful_report_cannot_dress_up_an_unproven_close(self):
        cheer = ("ВСЁ ПРЕКРАСНО: задача закрыта, 20 из 20 проверок зелены, замечаний нет.\n"
                 "Дальше можно ничего не делать.")
        report = done_judge_pc.fail_result(
            {"reason": "по адресу ПУСТО: за 04.09 в «docs/artifacts» нет ни одного файла"},
            cheer)
        lines = cm.lead(TASK, report, "failed").split("\n")
        self.assertIn(done_judge_pc.UNKNOWN, lines[1])
        for word in ("ПРЕКРАСНО", "зелены", "замечаний"):
            self.assertNotIn(word, lines[1])
        self.assertTrue(lines[2].startswith(cm.L_NEXT + "остановились"), lines[2])


class Prepend(unittest.TestCase):
    """Сборка: шапка сверху, техника — байт-в-байт ниже."""

    def test_body_below_is_untouched_byte_for_byte(self):
        body = report_ok("Тело отчёта.\n\nСсылка: docs/artifacts/reports/x.md")
        out = cm.prepend(body, TASK, "done")
        self.assertTrue(out.endswith(body))
        self.assertEqual(out[len(out) - len(body):], body)

    def test_head_is_the_three_lines_and_stands_first(self):
        out = cm.prepend(report_ok(), TASK, "done")
        self.assertTrue(out.startswith(cm.L_ASK))
        self.assertEqual(out.split("\n\n", 1)[0].count("\n"), 2)

    def test_idempotent_second_pass_does_not_add_a_second_head(self):
        once = cm.prepend(report_ok(), TASK, "done")
        self.assertEqual(cm.prepend(once, TASK, "done"), once)

    def test_empty_report_still_gets_the_three_lines(self):
        out = cm.prepend("", TASK, "done")
        self.assertEqual(len(out.split("\n")), 3)

    def test_never_raises_whatever_comes_in(self):
        for body in (None, "", "текст", 0, object()):
            for task in (None, "", object()):
                for st in (None, "", "done", object()):
                    self.assertIsInstance(cm.prepend(body, task, st), str)


class KeepFirst(unittest.TestCase):
    """МАРКЕР ПРИЧИНЫ ОСТАЁТСЯ ПЕРВЫМ СИМВОЛОМ — машинный контракт, не косметика.

    Класс заведён 04.09.2026 по живому провалу: шапка встала поверх ⏱, и гейт шага
    цепи (`pc_orchestrator._loc_after_fail`, `startswith(NO_HEAL_PREFIXES)`) перестал
    узнавать таймаут. Проверяем не «красиво ли», а различает ли прибор две вещи,
    которые обязан различать."""

    MARK = "⏱"                      # ⏱ — тот же символ, что у демона в FAIL_REASONS
    KEEP = (MARK, "✋", "отклонено Филиппом")

    def _timeout_body(self):
        return (self.MARK + " провал [причина=run_timeout · таймаут прогона]: claude не ответил "
                "за 2700s. Следов работы в окне 04.09 10:00–10:45 UTC нет.")

    def test_marker_stays_the_very_first_character(self):
        out = cm.prepend(self._timeout_body(), TASK, "failed", self.KEEP)
        self.assertTrue(out.startswith(self.MARK), out[:60])

    def test_head_still_stands_above_the_technical_body(self):
        # Маркер поднялся НАД шапкой, но техника по-прежнему НИЖЕ трёх строк:
        # обрезка ест хвост, и человеческое обязано остаться в голове.
        out = cm.prepend(self._timeout_body(), TASK, "failed", self.KEEP)
        self.assertLess(out.index(cm.L_ASK), out.index("[причина=run_timeout"))
        self.assertLess(out.index(cm.L_NEXT), out.index("[причина=run_timeout"))

    def test_body_below_the_head_travels_byte_for_byte(self):
        body = self._timeout_body()
        out = cm.prepend(body, TASK, "failed", self.KEEP)
        self.assertTrue(out.endswith(body[len(self.MARK) + 1:]),
                        "ниже шапки правится только разделитель после маркера")

    def test_idempotent_with_a_marker_no_second_head(self):
        once = cm.prepend(self._timeout_body(), TASK, "failed", self.KEEP)
        self.assertEqual(cm.prepend(once, TASK, "failed", self.KEEP), once)
        self.assertEqual(once.count(cm.L_ASK), 1)

    def test_unknown_marker_is_not_invented_by_the_module(self):
        # Набор приходит СНАРУЖИ. Пустой набор — прежнее поведение: шапка первой
        # строкой даже перед ⏱. Иначе модуль завёл бы свою копию перечня и она
        # протухла бы на первом новом маркере.
        out = cm.prepend(self._timeout_body(), TASK, "failed")
        self.assertTrue(out.startswith(cm.L_ASK))
        self.assertFalse(out.startswith(self.MARK))

    def test_only_a_leading_marker_is_lifted_not_one_from_the_middle(self):
        body = "провал: исполнитель написал " + self.MARK + " в середине отчёта"
        out = cm.prepend(body, TASK, "failed", self.KEEP)
        self.assertTrue(out.startswith(cm.L_ASK))
        self.assertTrue(out.endswith(body))

    def test_has_lead_sees_the_head_behind_the_marker(self):
        once = cm.prepend(self._timeout_body(), TASK, "failed", self.KEEP)
        self.assertTrue(cm.has_lead(once, self.KEEP))
        self.assertFalse(cm.has_lead(once), "без набора маркер шапку заслоняет — это и есть баг")
        self.assertFalse(cm.has_lead(self._timeout_body(), self.KEEP))


class Invariants(unittest.TestCase):
    """Замки, которые переживают правку автора."""

    def _src(self, name):
        with open(os.path.join(REPO, name), encoding="utf-8") as f:
            return f.read()

    def test_module_is_pure_no_disk_no_net_no_processes(self):
        src = self._src("close_msg_pc.py")
        body = src.split('"""', 2)[-1]           # мимо докстринга модуля
        for bad in ("import os", "import subprocess", "import urllib", "open(",
                    "requests", "sqlite3", "brain_writer", "bridge"):
            self.assertNotIn(bad, body, bad)

    def test_the_daemon_assembles_the_three_lines_in_exactly_one_place(self):
        # «Единица одна: сборка трёх строк в одном месте» — держим числом, а не
        # обещанием: расползись вызов по веткам, шапка разъехалась бы молча.
        src = self._src("pc_orchestrator.py")
        self.assertEqual(src.count("close_msg_pc.prepend("), 1)

    def test_the_module_travels_with_the_daemon(self):
        # Верхний импорт демона обязан стоять в _ORCH_RUNTIME, иначе self-update
        # выкатит демона, зовущего модуль, которого на диске нет.
        src = self._src("pc_orchestrator.py")
        self.assertTrue(re.search(r"^import close_msg_pc\b", src, re.M))
        self.assertIn('"close_msg_pc.py"', src)

    def test_the_module_keeps_no_copy_of_the_daemons_marker_set(self):
        # Перечень маркеров живёт в ОДНОМ месте (`pc_orchestrator.NO_HEAL_PREFIXES`) и приходит
        # сюда параметром. Копия здесь протухла бы на первом новом маркере — и молча: шапка
        # снова встала бы поверх него, а красным это стало бы только на гейте самообновления.
        body = self._src("close_msg_pc.py").split('"""', 2)[-1]
        for mark in ("⏱", "✋", "📡", "отклонено Филиппом"):
            self.assertNotIn(mark, body, mark)

    def test_the_daemon_hands_its_own_marker_set_to_the_assembly(self):
        # Сборка получает набор демона, а не пустой: иначе `keep_first` был бы мёртвой веткой.
        src = self._src("pc_orchestrator.py")
        self.assertIn("close_msg_pc.prepend(result, text, status, NO_HEAL_PREFIXES)", src)

    def test_the_head_stands_above_the_judges_verdict(self):
        # Весь смысл порядка: режется ХВОСТ, значит человеческое — сверху.
        out = cm.prepend(report_ok(), TASK, "done")
        self.assertLess(out.index(cm.L_ASK), out.index("[V0 судит done:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
