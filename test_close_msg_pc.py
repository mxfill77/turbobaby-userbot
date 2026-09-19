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

import json
import os
import re
import unittest

import close_msg_pc as cm
import done_judge_pc
import shtab_box

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

    def test_both_markers_of_a_failed_close_are_verdicts_too(self):
        """С 11.09.2026 маркеров у судьи ДВА, и оба обязаны читаться словом исхода.

        Читай мы один — «не доказано» приходило бы владельцу как «судья молчал», то есть самый
        сильный исход выглядел бы отсутствием суда."""
        for word, reason in ((done_judge_pc.UNKNOWN, "sensitive_content по адресу"),
                             (done_judge_pc.UNPROVEN, "по адресу пусто")):
            text = done_judge_pc.fail_result({"verdict": word, "reason": reason},
                                             "отчёт исполнителя")
            self.assertEqual(cm.verdict_word(text), word, reason)
            self.assertEqual(done_judge_pc.reason_of(text), reason, "причина читается обратно")

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
        # ПОПРАВЛЕНО 05.09: у пояснения потолок СВОЙ и больший (`GOAL_MAX`) — иначе
        # мысль не договаривает (замер: живые цели 265 и 316 знаков). Остальные
        # строки шапки по-прежнему под строку телефона, и это здесь и проверяется.
        self.assertLessEqual(len(self.lines[0].split(" …(", 1)[0]),
                             cm.GOAL_MAX + len(cm.L_ASK), self.lines[0])
        for ln in self.lines[1:3]:
            self.assertLessEqual(len(ln), cm.LINE_MAX, ln)

    def test_no_line_is_empty(self):
        for ln in self.lines:
            self.assertTrue(ln.split(": ", 1)[1].strip(), ln)

    def test_outcome_word_is_the_judges_own(self):
        self.assertIn(done_judge_pc.DONE, self.lines[1])

    def test_numbers_are_marked_as_a_quote_of_the_report(self):
        self.assertIn("из отчёта:", self.lines[1])
        self.assertIn("3", self.lines[1])

    def test_long_goal_is_shortened_meaningfully_and_says_where_the_rest_is(self):
        # ПОПРАВЛЕНО 05.09 (прежнее имя — ..._cut_by_word_with_an_ellipsis). Голого
        # многоточия мало: оно говорит «текст кончился не здесь» и не говорит, где
        # он кончается. Живой обрыв, ради которого правило сменилось, — закрытие
        # #227: «…обязана быть отвечаемой ОТТУДА, ГДЕ ОНА…» и больше ничего.
        long_goal = "ЦЕЛЬ. " + "слово " * 40
        ln = cm.lead(long_goal, report_ok(), "done").split("\n")[0]
        # Потолок бюджетирует ТЕКСТ; служебная пометка с адресом идёт сверх него
        # (иначе длинный адрес отчёта отнимал бы место у самой мысли).
        self.assertLessEqual(len(ln.split(" …(", 1)[0]), cm.GOAL_MAX + len(cm.L_ASK))
        self.assertIn("сокращено", ln)
        self.assertIn("docs/artifacts/", ln)          # адрес отчёта назван
        self.assertFalse(ln.endswith("сло…"), ln)     # обрубка посреди слова нет


class Explain(unittest.TestCase):
    """ПОЯСНЕНИЕ ДОГОВАРИВАЕТ (задание 05.09, п.3)."""

    def test_short_goal_goes_byte_for_byte(self):
        self.assertEqual(cm.explain("ЦЕЛЬ. Убрать дубль."), "Убрать дубль.")

    def test_long_goal_ends_at_a_phrase_not_mid_thought(self):
        # ЖИВОЙ ГОЛДЕН: цель задания 228 дословно, 316 знаков (замер 05.09).
        goal = ("ЦЕЛЬ. Сообщение о закрытии задачи ДОГОВАРИВАЕТ и показывает, где мы в "
                "плане. Сегодня оно обрывается на полуслове и заканчивается ничем: "
                "владелец видит, что задача закрыта, но не видит ни законченного "
                "пояснения, ни что осталось, ни куда идём. Прямой заказ владельца 05.09: "
                "«информативность правильная, но без лишнего шума».")
        out = cm.explain(goal, report_ok())
        head = out.split(" …(", 1)[0]
        self.assertTrue(head.endswith("."), head)                 # мысль закончена
        self.assertIn(head, goal)                                 # и она ДОСЛОВНА
        self.assertLessEqual(len(head), cm.GOAL_MAX)              # потолок бюджетирует ТЕКСТ
        self.assertIn("сокращено", out)                           # …а остаток назван

    def test_shortening_names_the_report_address(self):
        out = cm.explain("ЦЕЛЬ. " + "слово " * 60, report_ok())
        self.assertIn("сокращено", out)
        self.assertIn("docs/artifacts/2026-09-04-потолок-времени-на-мост.md", out)

    def test_no_address_in_the_report_is_said_not_invented(self):
        out = cm.explain("ЦЕЛЬ. " + "слово " * 60, "отчёт без единого адреса")
        self.assertIn(cm.CUT_NOREF.strip(), out)
        self.assertNotIn("docs/artifacts", out)

    def test_no_phrase_fits_still_cuts_by_word_and_marks(self):
        # Ни одной точки в бюджете — режем по слову, но пометка остаётся: грубое
        # сокращение обязано называть себя так же, как осмысленное.
        out = cm.explain("ЦЕЛЬ. " + "оченьдлинноеслово " * 30, report_ok())
        self.assertIn("сокращено", out)
        self.assertNotIn("оченьдлинноесл…", out)

    def test_ref_is_read_not_built(self):
        self.assertEqual(cm.report_ref(JUDGE_OK),
                         "docs/artifacts/2026-09-04-потолок-времени-на-мост.md")
        self.assertEqual(cm.report_ref("ни одного адреса"), "")


class PlanLines(unittest.TestCase):
    """РАЗДЕЛ «ЧТО ДАЛЬШЕ»: четыре пункта, форма не меняется никогда."""

    FULL = {"box": {"ok": True, "waiting": 3, "next": "00d-next.0905"},
            "run": {"ok": True, "waiting": 1, "next_id": 229},
            "retry": {"ok": True, "count": 0},
            "pace": {"ok": True, "median_min": 25.0, "samples": 12}}

    def test_exactly_four_bullets_always(self):
        for facts in (self.FULL, {}, {"box": {"ok": True, "waiting": 0}}, None):
            got = cm.plan_lines(facts if facts is not None else {})
            self.assertEqual(len(got), cm.PLAN_LINES, got)

    def test_box_says_the_count_and_the_next_by_name(self):
        self.assertIn("ещё 3", cm.plan_lines(self.FULL)[0])
        self.assertIn("00d-next.0905", cm.plan_lines(self.FULL)[0])

    def test_running_line_says_what_waits_and_its_number(self):
        self.assertIn("#229", cm.plan_lines(self.FULL)[1])

    def test_estimate_calls_itself_an_estimate_and_names_its_basis(self):
        line = cm.plan_lines(self.FULL)[3]
        self.assertIn("ОЦЕНКА", line)
        self.assertIn("медиана", line)      # из чего посчитана
        self.assertIn("взятий", line)
        self.assertIn("1 ч 15 мин", line)   # 3 × 25 мин

    def test_the_section_is_bounded_by_number_not_by_taste(self):
        # Предсмертный взгляд задания: раздел, выросший в простыню, хуже, чем его
        # отсутствие. Потолок держит ЧИСЛО, а не обещание автора следующей правки.
        block = cm.plan_block({"box": {"ok": False, "why": "ы" * 4000},
                               "run": {}, "retry": {}, "pace": {}})
        self.assertLessEqual(len(block), cm.PLAN_MAX)
        self.assertLessEqual(len(block.split("\n")), cm.PLAN_LINES + 1)

    def test_no_facts_at_all_means_the_section_was_not_asked_for(self):
        self.assertEqual(cm.plan_block(None), "")
        self.assertTrue(cm.plan_block({}).startswith(cm.PLAN_HEAD))


class PlanFacts(unittest.TestCase):
    """Слепок ящика → факты. Чистая арифметика, ни моста, ни диска, ни часов."""

    def box_report(self, docs, marks=(), gates=None, retry=(), placed=(), folder_ok=True,
                   marks_ok=True):
        return {"placed": list(placed),
                "build": {"folder_ok": folder_ok, "folder_why": "мост молчал",
                          "marks_ok": marks_ok, "marks_why": "закрытые ряды не спрашивали",
                          "docs": [{"key": k, "name": "shtab_task_" + k} for k in docs],
                          "task_marks": [("2026-09-05", m) for m in marks],
                          "gates": gates or {}, "retry": [{"key": r} for r in retry]}}

    def test_incomplete_markers_give_no_number_at_all(self):
        # ЖИВОЙ ЗАМЕР 05.09: 37 документов, 7 снятых, открытым маркером помечен 1 →
        # «ждут 29», тогда как два десятка ключей уже взяты И ЗАКРЫТЫ (закрытых рядов
        # ящик в тот виток не спрашивал). Число здесь врало бы втрое.
        rep = self.box_report(["a", "b", "c"], marks=["a"], marks_ok=False)
        got = cm.facts_from_box(rep, now=10.0)
        self.assertFalse(got["ok"])
        self.assertEqual(got["waiting"], 0)          # не число, а «нет числа»
        self.assertIn("закрытые ряды", got["why"])   # причина — ЯЩИКА, а не наша выдумка
        # …и когда ящик причины не назвал, свою мы всё равно говорим словами.
        rep["build"]["marks_why"] = ""
        self.assertIn("маркеров", cm.facts_from_box(rep, now=10.0)["why"])

    def test_an_unmeasurable_tick_does_not_erase_what_we_knew(self):
        prev = {"ts": 5.0, "ok": True, "waiting": 4, "next": "a", "retry": 1, "taken": [1.0]}
        got = cm.facts_from_box(self.box_report(["a"], marks_ok=False), prev=prev, now=99.0)
        self.assertTrue(got["ok"])
        self.assertEqual(got["ts"], 5.0)             # …но и не молодит: возраст судит plan_facts
        self.assertEqual(got["waiting"], 4)

    def test_waiting_is_docs_minus_taken(self):
        got = cm.facts_from_box(self.box_report(["a", "b", "c"], marks=["b"]), now=100.0)
        self.assertTrue(got["ok"])
        self.assertEqual(got["waiting"], 2)
        self.assertEqual(got["next"], "a")           # первый ПО ИМЕНИ

    def test_a_doc_the_gates_already_refused_is_not_waiting(self):
        rep = self.box_report(["a", "b"], gates={"a": {"ok": False}})
        self.assertEqual(cm.facts_from_box(rep, now=1.0)["waiting"], 1)

    def test_unread_folder_is_unknown_not_zero(self):
        got = cm.facts_from_box(self.box_report([], folder_ok=False), now=1.0)
        self.assertFalse(got["ok"])
        self.assertIn("мост молчал", got["why"])

    def test_takings_accumulate_and_are_capped(self):
        prev = {"taken": [float(i) for i in range(cm.TAKEN_KEEP)]}
        got = cm.facts_from_box(self.box_report(["a"], placed=[{"id": 1}]),
                                prev=prev, now=999.0)
        self.assertEqual(len(got["taken"]), cm.TAKEN_KEEP)
        self.assertEqual(got["taken"][-1], 999.0)

    def test_a_stale_snapshot_says_unknown_instead_of_yesterdays_number(self):
        census = {"ts": 0.0, "ok": True, "waiting": 3, "next": "a", "retry": 0}
        got = cm.plan_facts(census, now=cm.PLAN_STALE_SEC + 60.0)
        self.assertFalse(got["box"]["ok"])
        self.assertIn("слепку ящика", got["box"]["why"])

    def test_a_fresh_snapshot_answers_with_numbers(self):
        census = {"ts": 100.0, "ok": True, "waiting": 3, "next": "a", "retry": 2,
                  "taken": [0.0, 600.0, 1500.0, 2400.0]}
        got = cm.plan_facts(census, now=200.0, queue={"ok": True, "waiting": 0})
        self.assertTrue(got["box"]["ok"])
        self.assertEqual(got["retry"], {"ok": True, "count": 2})
        self.assertTrue(got["pace"]["ok"])

    def test_one_gap_is_not_a_rhythm(self):
        self.assertFalse(cm._pace([0.0, 600.0])["ok"])


class WaitingAgainstTheLiveGate(unittest.TestCase):
    """«Ждут N» ПРОТИВ ЖИВЫХ ВОРОТ — замок класса 19.09.2026 («ящик не видит лежащих»).

    ПОВОД — ЗАМЕР, А НЕ ВООБРАЖЕНИЕ. 19.09.2026 04:03 UTC живой `--status`: в папке 260
    документов, три из них лежат непрочитанными вторые сутки (`67b-urok-iskazhen.1809`,
    `67f-kodirovka-i-schetchik.1909`, `67g-petlya-zhivogo-nabora.1909`), и все три ворота
    отвергли одной причиной — `too_long`: тела 4873, 4853 и 4514 единиц UTF-16 при потолке
    :data:`shtab_box.BODY_MAX` = 4500. А слепок оборота в тот час говорил `waiting: 0`, и
    сообщение о закрытии сказало бы владельцу :data:`close_msg_pc.P_BOX_EMPTY` — «брать
    нечего», то есть УТВЕРЖДЕНИЕ о пустом ящике поверх трёх лежащих документов.

    ЧЕМ ЭТОТ НАБОР ОТЛИЧАЕТСЯ ОТ СОСЕДНЕГО ``test_a_doc_the_gates_already_refused_is_not_waiting``.
    Тот подаёт вердикт ворот РУКАМИ (``{"ok": False}``) и стережёт арифметику вычитания. Здесь
    вердикт поднимают САМИ ВОРОТА (:func:`shtab_box.check`) на теле, собранном по ту и по эту
    сторону потолка, — то есть стережётся СТЫК двух модулей: разойдись потолок ворот со счётом
    ждущих, синтетический вердикт этого не заметил бы ни одной веткой.

    ДЕНЬ БЕРЁТСЯ У ЗАПИСИ ЗАМЕРА ЁМКОСТИ, А НЕ У КАЛЕНДАРЯ: ворота закрываются, когда запись
    старше :data:`shtab_box.CAPACITY_TTL_DAYS` (30 суток), и набор, пришпиленный к 19.09.2026,
    начал бы падать `capacity_unmeasured` с середины октября — по причине, к предмету теста
    отношения не имеющей.
    """

    DAY = shtab_box.CAPACITY_RECORD["date"]

    def body(self, size):
        """Тело, проходящее ВСЕ ворота кроме длины, ровно ``size`` единиц UTF-16. → str.

        Набивка — кириллическая буква (одна кодовая единица UTF-16 на символ) и стои́т ПОСЛЕ
        адреса результата: подрежь мы хвост — резался бы именно адрес, и отказ приехал бы
        `no_address`, то есть тест мерил бы не то, что назвал.
        """
        head = ("ЦЕЛЬ. Замок счёта ждущих: тело по ту и по эту сторону потолка.\n"
                "%s\n%s\n" % (shtab_box.PROHIBITIONS, shtab_box.ADDRESS))
        pad = int(size) - shtab_box.units(head)
        self.assertGreater(pad, 0, "голова тела уже переросла заказанный размер")
        return head + "я" * pad

    def census(self, bodies):
        """{ключ: тело} → слепок оборота, собранный ЖИВЫМИ воротами. → dict."""
        docs, gates = [], {}
        for key in sorted(bodies):
            doc = {"key": key, "name": shtab_box.doc_name(key), "body": bodies[key]}
            ok, reason, why = shtab_box.check(doc, self.DAY)
            gates[key] = {"ok": ok, "reason": reason, "why": why}
            docs.append(doc)
        return cm.facts_from_box(
            {"placed": [],
             "build": {"folder_ok": True, "folder_why": "", "marks_ok": True, "marks_why": "",
                       "docs": docs, "task_marks": [], "gates": gates, "retry": []}},
            now=100.0)

    def test_three_bodies_over_the_cap_leave_waiting_at_zero(self):
        # ЖИВОЙ СЛУЧАЙ 19.09, воспроизведённый по эту сторону моста: три тела через потолок —
        # и «ждут» отвечает НОЛЬ при трёх лежащих документах.
        over = shtab_box.BODY_MAX + 1
        got = self.census({"67b-urok-iskazhen.1809": self.body(over),
                           "67f-kodirovka-i-schetchik.1909": self.body(over),
                           "67g-petlya-zhivogo-nabora.1909": self.body(over)})
        self.assertTrue(got["ok"], "корпус полон — число обязано быть числом")
        self.assertEqual(got["waiting"], 0)
        self.assertEqual(got["next"], "")

    def test_the_refusal_does_not_travel_with_the_number(self):
        # ОТДЕЛЬНАЯ НАХОДКА ЗАДАНИЯ 19.09, записанная замком: причина отказа в слепок НЕ ЕДЕТ
        # ни одним полем. Пока это так, «ждут 0» и «в ящике пусто» для читателя слепка — одно
        # и то же, и различить их ему нечем.
        got = self.census({"67g-petlya-zhivogo-nabora.1909":
                           self.body(shtab_box.BODY_MAX + 1)})
        self.assertNotIn("too_long", json.dumps(got, ensure_ascii=False))
        self.assertEqual(cm.plan_lines({"box": {"ok": True, "waiting": got["waiting"]},
                                        "run": {}, "retry": {"ok": True, "count": 0},
                                        "pace": {}})[0], cm.P_BOX_EMPTY)

    def test_a_body_under_the_cap_makes_waiting_more_than_zero(self):
        # ОБЯЗАТЕЛЬНЫЙ ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗАДАНИЯ 19.09 (п.5): подаём заведомо ГОДНОЕ тело в тот
        # же корпус — и ждущих становится больше нуля. Годное взято по САМОЙ границе (ровно
        # потолок), потому что живой 67g перерос её на 14 единиц: сдвинься граница на единицу —
        # и это должно быть видно ТЕСТОМ, а не вторыми сутками молчания.
        bodies = {"67b-urok-iskazhen.1809": self.body(shtab_box.BODY_MAX + 1),
                  "67f-kodirovka-i-schetchik.1909": self.body(shtab_box.BODY_MAX + 1),
                  "67g-petlya-zhivogo-nabora.1909": self.body(shtab_box.BODY_MAX + 1),
                  "00-godnoe-telo.1909": self.body(shtab_box.BODY_MAX)}
        got = self.census(bodies)
        self.assertEqual(got["waiting"], 1)
        self.assertEqual(got["next"], "00-godnoe-telo.1909")
        self.assertIn("00-godnoe-telo.1909",
                      cm.plan_lines({"box": {"ok": True, "waiting": got["waiting"],
                                             "next": got["next"]},
                                     "run": {}, "retry": {}, "pace": {}})[0])


class NegativeAndDeathLook(unittest.TestCase):
    """Два обязательных теста задания 04.09 плюс три обязательных теста 05.09."""

    # ── ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА ЗАДАНИЯ 05.09 ────────────────────────────────

    def test_negative_empty_box_says_it_in_words_not_by_an_empty_section(self):
        # Раздел, молчащий при пустом ящике, неотличим от сломанного.
        only_retry = cm.plan_lines({"box": {"ok": True, "waiting": 0},
                                    "run": {"ok": True, "waiting": 0},
                                    "retry": {"ok": True, "count": 2},
                                    "pace": {"ok": True, "median_min": 20.0, "samples": 5}})
        self.assertIn("новых заданий в ящике нет", only_retry[0])
        self.assertIn("доведением прежних", only_retry[0])      # отдельный законный исход
        nothing = cm.plan_lines({"box": {"ok": True, "waiting": 0},
                                 "run": {"ok": True, "waiting": 0},
                                 "retry": {"ok": True, "count": 0},
                                 "pace": {"ok": True, "median_min": 20.0, "samples": 5}})
        self.assertIn("брать нечего", nothing[0])
        for line in only_retry + nothing:
            self.assertTrue(line.strip(), "пустых пунктов быть не может")

    def test_negative_unreachable_queue_says_unknown_not_zero(self):
        # Ни один источник не отвечает → четыре «НЕИЗВЕСТНО» и ни одного нуля.
        lines = cm.plan_lines({})
        self.assertEqual(len(lines), cm.PLAN_LINES)
        for line in lines:
            self.assertIn("НЕИЗВЕСТНО", line, line)
        joined = " ".join(lines)
        for lie in ("ещё 0", "ждёт 0", "— 0", "нет"):
            self.assertNotIn(lie, joined, joined)

    def test_negative_text_over_the_cap_arrives_finished_with_an_address(self):
        # Полный путь: собрали шапку → отдали ту же обрезку, что в проде.
        import result_spill

        body = report_ok("Очень длинное тело отчёта. " * 400)
        out = cm.prepend(body, TASK, "done", (), self.FACTS)
        capped, rel = result_spill.cap_result(out, tid="t", cap=4500, save=False)
        self.assertLessEqual(len(capped), 4500)
        # 1) человеческая часть уцелела ЦЕЛИКОМ — она сверху по построению;
        self.assertTrue(capped.startswith(cm.L_ASK))
        self.assertIn(cm.PLAN_HEAD, capped)
        for line in cm.plan_lines(self.FACTS):
            self.assertIn(line, capped)
        # 2) сообщение кончается ЗАКОНЧЕННОЙ пометкой с адресом полного тела,
        #    а не обрубком посреди слова.
        self.assertIn(result_spill.TRUNC_HEAD, capped)
        self.assertRegex(capped, r"(Полный текст: \S+|сохранить НЕ УДАЛОСЬ)")

    FACTS = {"box": {"ok": True, "waiting": 2, "next": "00d-x.0905"},
             "run": {"ok": True, "waiting": 1, "next_id": 230},
             "retry": {"ok": True, "count": 1},
             "pace": {"ok": True, "median_min": 30.0, "samples": 8}}

    # ── ДВА ОБЯЗАТЕЛЬНЫХ ТЕСТА ЗАДАНИЯ 04.09 (не ослаблены) ──────────────────

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
            {"verdict": done_judge_pc.UNPROVEN,
             "reason": "по адресу ПУСТО: за 04.09 в «docs/artifacts» нет ни одного файла"},
            cheer)
        lines = cm.lead(TASK, report, "failed").split("\n")
        self.assertIn(done_judge_pc.UNPROVEN, lines[1])
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
        self.assertIn("close_msg_pc.prepend(result, text, status, NO_HEAL_PREFIXES", src)

    def test_the_daemon_hands_the_plan_facts_too(self):
        # ПЯТЫЙ ДОВОД — ФАКТЫ РАЗДЕЛА, и он обязан ехать из демона, а не подразумеваться:
        # без него `prepend` получил бы `None`, то есть «раздела не просили», и «ЧТО
        # ДАЛЬШЕ» пропал бы МОЛЧА — ровно тем способом, каким его сегодня и нет.
        src = self._src("pc_orchestrator.py")
        self.assertIn("_close_plan_facts(_plan_queue)", src)
        # …и факты собираются БЕЗ похода в мост: слепок с диска + уже прочитанные ряды.
        self.assertIn("close_msg_pc.plan_facts(st.get(\"census\")", src)

    def test_the_box_snapshot_is_taken_in_exactly_one_place(self):
        # Слепок ящика снимается там, где состояние уже оплачено, и только там.
        src = self._src("pc_orchestrator.py")
        self.assertEqual(src.count("close_msg_pc.facts_from_box("), 1)

    def test_the_head_stands_above_the_judges_verdict(self):
        # Весь смысл порядка: режется ХВОСТ, значит человеческое — сверху.
        out = cm.prepend(report_ok(), TASK, "done")
        self.assertLess(out.index(cm.L_ASK), out.index("[V0 судит done:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
