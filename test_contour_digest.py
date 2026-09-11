# -*- coding: utf-8 -*-
"""test_contour_digest.py — регресс сводки состояния контура полосы ПК.

* ``TestPurity`` — ``CONTOUR_DIGEST_PURE``: чистая логика не смеет завести часы,
  диск, сеть или ``getenv``; номера темы сводок не знает ВООБЩЕ.
* ``TestReadsOnly`` — ``CONTOUR_DIGEST_READS_ONLY``: САМОЕ ДОРОГОЕ здесь. Сводка
  ничего не исполняет и задач не ставит — ни одной ветки записи в очередь во всех
  боевых файлах ветки, обходом AST. Нарушение этого молчаливо: ряд, поставленный
  «на всякий случай», выглядел бы для владельца обычной задачей.
* ``TestLaws`` — четыре закона показа: нет адреса → гипотеза; нет метки замера →
  неизвестно; слепок старше СВОЕГО предела → неизвестно с названным пределом;
  свежий слепок исход зовущего сохраняет.
* ``TestNegative`` — ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания: источник недоступен → сводка
  ГОВОРИТ «неизвестно», а не пропускает строку молча. Проверяется и на уровне
  строки, и на уровне готового сообщения, и на живом дереве без файлов.
* ``TestSeries`` — счёт серии: чистая = сдана, «не сверен» — обрыв; служебные
  корни мимо счёта признаком :mod:`series_pc`; метки читающих строк ВЫВОДЯТСЯ из
  модулей-владельцев, а не набраны литералом.
* ``TestCalm`` — спокойно = ОДНА строка, разделов нет; неспокойно = разделы есть,
  и каждая тема имеет строку.
* ``TestAddress`` — тема не настроена → наружу НЕ УХОДИТ ничего; настроена →
  сообщение уходит РОВНО в неё; сорванная отправка НЕ двигает окно сводки.
* ``TestFindingFate`` — СУДЬБА находки и ПОЛЬЗА от неё (04.09.2026): пять исходов
  задания числами, третий исход у каждого нового числа, потолок прироста в четыре
  строки и главный замок — заявка ступени B пользой НЕ считается.
"""
from __future__ import annotations

import ast
import io
import json
import os
import tempfile
import unittest

import contour_digest as cd
import contour_digest_run as run
import done_judge_pc
import queue_snapshot_pc
import recon_auto
import review_intake
import series_pc
import shtab_box
import vitrina_pc as vp
import vitrina_pc_run as vpr

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1788290000.0


def _snapshot(open_rows=None, closed_rows=None):
    return {"open": dict(open_rows or {}), "closed": dict(closed_rows or {})}


def _closed(tid, at, outcome, goal=""):
    return str(tid), {"id": tid, "at": at, "outcome": outcome, "goal": goal, "why": ""}


class TestPurity(unittest.TestCase):
    """CONTOUR_DIGEST_PURE — решение остаётся решением, а не руками."""

    BANNED_CALLS = {"now", "utcnow", "time", "monotonic", "getenv", "open", "run", "Popen",
                    "urlopen", "getmtime"}
    BANNED_IMPORTS = {"os", "subprocess", "socket", "urllib", "time", "shutil", "requests",
                      "datetime", "io"}

    def test_no_clock_no_disk_no_network_no_env(self):
        with io.open(os.path.join(HERE, "contour_digest.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="contour_digest.py")
        bad_imports, bad_calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad_imports |= {a.name.split(".")[0] for a in node.names} & self.BANNED_IMPORTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                bad_imports |= {node.module.split(".")[0]} & self.BANNED_IMPORTS
            elif isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in self.BANNED_CALLS:
                    bad_calls.add(name)
        self.assertEqual(bad_imports, set(), "чистый модуль завёл запрещённый импорт")
        self.assertEqual(bad_calls, set(), "чистый модуль завёл часы/диск/сеть/env")

    def test_module_knows_no_topic_number(self):
        """Номера темы сводок в чистом модуле нет — ни дефолтом, ни литералом.

        Тот же замок, что у ступени D: адрес живёт в настройке демона, и чужая
        тема здесь дороже молчания.
        """
        with io.open(os.path.join(HERE, "contour_digest.py"), encoding="utf-8") as fh:
            body = fh.read()
        for known in ("328", "1160", "829", "161"):
            self.assertNotIn(known, body, "номер темы просочился в чистый модуль: %s" % known)


class TestReadsOnly(unittest.TestCase):
    """CONTOUR_DIGEST_READS_ONLY — сводка ничего не исполняет и задач не ставит."""

    WRITERS = {"place", "place_task", "place_ask", "enqueue_pc_task", "set_needs_approval",
               "claim_task", "complete_task", "update_task", "delete", "remove", "unlink",
               "rmtree"}
    FILES = ("contour_digest.py", "contour_digest_run.py")

    def test_no_branch_writes_to_the_queue(self):
        found = {}
        for name in self.FILES:
            with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    call = getattr(fn, "attr", None) or getattr(fn, "id", None)
                    if call in self.WRITERS:
                        found.setdefault(name, set()).add(call)
        self.assertEqual(found, {}, "ветка сводки завела запись: %r" % found)

    def test_the_only_thing_written_is_its_own_tick(self):
        """Единственная запись на диск — метка оборота, и она названа одним именем."""
        with io.open(os.path.join(HERE, "contour_digest_run.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="contour_digest_run.py")
        writers = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if (getattr(fn, "attr", None) or getattr(fn, "id", None)) in ("open", "dump"):
                    parent = getattr(node, "args", [])
                    writers.add(len(parent))
        with io.open(os.path.join(HERE, "contour_digest_run.py"), encoding="utf-8") as fh:
            self.assertIn("def write_state", fh.read())
        self.assertTrue(writers, "запись есть, но её не видно — тест перестал задевать ветку")


class TestLaws(unittest.TestCase):
    """Четыре закона показа."""

    def test_no_source_means_hypothesis(self):
        rd = cd.reading("red", "всё хорошо", src=None, read_at=NOW, now=NOW, kind=cd.OK)
        self.assertEqual(rd["kind"], cd.HYPO)
        self.assertIn("ГИПОТЕЗА", cd.line_text(rd))

    def test_hypothesis_beats_what_the_caller_asked_for(self):
        """Без адреса строка не становится фактом оттого, что зовущий в него верит."""
        rd = cd.reading("red", "красное", src="", read_at=NOW, now=NOW, kind=cd.RED)
        self.assertEqual(rd["kind"], cd.HYPO)

    def test_no_read_stamp_means_unknown(self):
        rd = cd.reading("red", "тихо", src="queue", read_at=None, now=NOW, kind=cd.OK)
        self.assertEqual(rd["kind"], cd.UNKNOWN)
        self.assertIn("НЕИЗВЕСТНО", cd.line_text(rd))

    def test_snapshot_older_than_its_own_limit_is_unknown(self):
        old = NOW - cd.limit_of("queue") - 1
        rd = cd.reading("closed", "закрылось 3", src="queue", read_at=old, now=NOW, kind=cd.OK)
        self.assertEqual(rd["kind"], cd.UNKNOWN)
        self.assertIn("60 мин", cd.line_text(rd), "предел обязан быть НАЗВАН числом")

    def test_limits_are_per_source_not_one_for_all(self):
        """Ритмы источников разные — общий предел врал бы в обе стороны сразу."""
        self.assertNotEqual(cd.limit_of("queue"), cd.limit_of("expect"))
        self.assertNotEqual(cd.limit_of("expect"), cd.limit_of("outbox"))
        self.assertIsNone(cd.limit_of("git"), "у git возраста нет — он читается в момент сборки")

    def test_fresh_snapshot_keeps_the_verdict(self):
        rd = cd.reading("red", "упало", src="queue", read_at=NOW - 60, now=NOW, kind=cd.RED)
        self.assertEqual(rd["kind"], cd.RED)

    def test_interval_is_named_by_number(self):
        self.assertEqual(cd.INTERVAL_SEC, 14400)
        self.assertIn("14400", cd.interval_words())
        self.assertIn("4 ч", cd.interval_words())


class TestNegative(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ: источник недоступен → «неизвестно», а не тишина."""

    def test_dead_source_still_gives_a_line(self):
        rd = cd.dead_source("closed", "queue", "файла нет")
        self.assertEqual(rd["kind"], cd.UNKNOWN)
        self.assertIn("файла нет", cd.line_text(rd))
        self.assertIn("queue_snapshot_pc.state.json", cd.line_text(rd))

    def test_every_topic_survives_a_dead_tree(self):
        """Пустой корень: файлов нет ни одного — и КАЖДАЯ тема всё равно говорит."""
        with tempfile.TemporaryDirectory() as tmp:
            rep = run.build(root=tmp, now=NOW, since=NOW - cd.INTERVAL_SEC,
                            runner=lambda *a, **k: (_ for _ in ()).throw(OSError("git нет")))
            for key in cd.TOPIC_KEYS:
                rows = rep["readings"].get(key) or []
                self.assertTrue(rows, "тема %r пропала молча — закон 3 нарушен" % key)
                self.assertTrue(any(r["kind"] == cd.UNKNOWN for r in rows),
                                "тема %r смолчала вместо «неизвестно»" % key)
            text = cd.render(rep)
            self.assertIn("НЕИЗВЕСТНО", text)
            self.assertFalse(cd.is_calm(rep), "«не смог проверить» не есть «всё хорошо»")

    def test_the_dead_source_message_survives_the_outbound_guard(self):
        """САМАЯ ДОРОГАЯ проверка раздела, и она родилась из ЖИВОГО провала 02.09.

        Голый текст исключения нёс абсолютный путь Windows, страж исходящего ловил
        его и задерживал СООБЩЕНИЕ ЦЕЛИКОМ — то есть сводка молчала бы ровно тогда,
        когда обязана сказать «неизвестно». Причина отказа теперь едет без пути.
        """
        import review_audit

        with tempfile.TemporaryDirectory() as tmp:
            rep = run.build(root=tmp, now=NOW, since=NOW - cd.INTERVAL_SEC,
                            runner=lambda *a, **k: (_ for _ in ()).throw(OSError("git нет")))
            text = cd.render(rep)
            self.assertEqual(review_audit.outbound_safe(text), [],
                             "страж задержал сводку о мёртвых источниках — канал молчит")
            self.assertNotIn(":\\", text, "абсолютный путь просочился в сообщение")

    def test_foreign_text_goes_through_the_guard_line_by_line(self):
        """Чужая цитата задерживается ПОСТРОЧНО и сообщения не глушит."""
        got = run.quote("токен sk-ant-api03-AAAABBBBCCCCDDDD и ещё текст", 90)
        self.assertNotIn("sk-ant-api03-AAAABBBBCCCCDDDD", got)
        self.assertIn("страж исходящего", got)

    def test_unknown_never_becomes_calm(self):
        rep = {"readings": {"closed": [cd.dead_source("closed", "queue", "нет")],
                            "red": [], "await": [], "axis": [], "series": []}}
        self.assertFalse(cd.is_calm(rep))

    def test_broken_json_is_unknown_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with io.open(os.path.join(tmp, cd.source("queue")["addr"]), "w",
                         encoding="utf-8") as fh:
                fh.write("{это не json")
            data, at, why = run.read_json(tmp, cd.source("queue")["addr"])
            self.assertIsNone(data)
            self.assertIsNotNone(at, "метка замера у битого файла есть — он существует")
            self.assertIn("не разобран", why)

    def test_stale_snapshot_takes_the_series_numbers_away(self):
        """Числа, сосчитанные по протухшему слепку, наружу не едут: они выглядят фактом."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, cd.source("queue")["addr"])
            with io.open(path, "w", encoding="utf-8") as fh:
                json.dump(_snapshot(closed_rows=dict([_closed(1, NOW - 99999, "done")])), fh)
            future = os.path.getmtime(path) + cd.limit_of("queue") + 600
            rep = run.build(root=tmp, now=future, since=future - cd.INTERVAL_SEC,
                            runner=lambda *a, **k: (_ for _ in ()).throw(OSError("git нет")))
            self.assertIsNone(rep["series"], "серия посчиталась по слепку старше предела")
            self.assertIn("НЕИЗВЕСТНО", cd.render(rep))


def _proved(*ids):
    """Реестр вердиктов: названные строки ДОКАЗАНЫ судьёй закрытия."""
    return {str(i): {cd.F_PROVED: True, cd.F_ADDRESSED: True} for i in ids}


def _blind(*ids):
    """Реестр вердиктов: названные строки закрыты БЕЗ АДРЕСА (судить было нечем)."""
    return {str(i): {cd.F_PROVED: False, cd.F_ADDRESSED: False} for i in ids}


def _unknown_row(tid, goal="ЦЕЛЬ: правка"):
    """Ряд, закрытый ТРЕТЬИМ исходом: судья не смог прочитать продукт (живой случай 245)."""
    return {"id": tid, "outcome": cd.OUT_UNKNOWN, "goal": goal,
            "why": "V0: UNKNOWN / sensitive_content по адресу «docs/artifacts/…-1009.md»"}


class TestThirdOutcomeInTheSeries(unittest.TestCase):
    """Решение Штаба 11.09.2026: НЕИЗВЕСТНО не засчитывает, не рвёт и звучит громко.

    Все три отрицательных пункта задания проверяются здесь на ТОМ ЖЕ приборе, которым сводка
    считает серию боем, а не на его копии."""

    def test_the_word_mirrors_the_snapshot(self):
        """Литерал ЗАИМСТВОВАН у слепка очереди. Разойдись они — исход стал бы невидимым, и
        именно молча: неизвестная строка снова считалась бы обрывом."""
        self.assertEqual(cd.OUT_UNKNOWN, queue_snapshot_pc.OUT_UNKNOWN)

    def test_a_real_failure_is_still_a_failure(self):
        """ОТРИЦАТЕЛЬНЫЙ 1: `failed` рвёт серию и стои́т в знаменателе, как стоял."""
        rows = [{"id": 1, "outcome": "done", "goal": "ЦЕЛЬ: правка"},
                {"id": 2, "outcome": "failed", "goal": "ЦЕЛЬ: правка"}]
        got = cd.series(rows, judged=_proved(1))
        self.assertEqual(got["streak"], 0, "провал серию не оборвал")
        self.assertEqual((got["unknown"], got["denom"]), (0, 2))
        self.assertIs(got["alarm"], False)

    def test_unknown_neither_counts_nor_breaks(self):
        """ОТРИЦАТЕЛЬНЫЙ 2: неизвестная строка серию не рвёт и из знаменателя выходит."""
        rows = [{"id": 1, "outcome": "done", "goal": "ЦЕЛЬ: правка"},
                _unknown_row(2),
                {"id": 3, "outcome": "done", "goal": "ЦЕЛЬ: правка"}]
        got = cd.series(rows, judged=_proved(1, 3))
        self.assertEqual(got["streak"], 2, "неизвестная строка разорвала серию надвое")
        self.assertEqual(got["unknown"], 1)
        self.assertEqual(got["window"], 3)
        self.assertEqual(got["denom"], 2, "неизвестная осталась в знаменателе")
        # …и в разборе закрытий её нет ни в одном из четырёх слов: она не «сдана» ничем.
        self.assertEqual(got["proved"] + got["blind"] + got["unproved"] + got["unjudged"],
                         got["denom"])

    def test_an_unknown_row_never_becomes_clean(self):
        """Замок с другой стороны: льгота не должна превращаться в зачёт.

        `is_clean` обязана отвечать «нет» даже при доказанной записи в реестре — ряд закрыт не
        `done`, и зачесть его значило бы засчитать работу, которую никто не прочитал."""
        row = _unknown_row(7)
        self.assertIs(cd.is_unknown(row), True)
        self.assertIs(cd.is_clean(row, judged=_proved(7)), False)

    def test_the_number_is_printed_even_when_it_is_zero(self):
        """ОТРИЦАТЕЛЬНЫЙ 3 (первая половина решения п. 3): ноль печатается тоже."""
        got = cd.series([{"id": 1, "outcome": "done", "goal": "ЦЕЛЬ: правка"}],
                        judged=_proved(1))
        self.assertIn("НЕИЗВЕСТНО 0 из 1", cd.series_line(got))

    def test_the_share_above_one_fifth_is_an_alarm_about_the_judge(self):
        """Замок от дыры: льгота, о которой не кричат, становится способом не считаться."""
        rows = [{"id": i, "outcome": "done", "goal": "ЦЕЛЬ: правка"} for i in range(1, 4)]
        rows.append(_unknown_row(4))
        got = cd.series(rows, judged=_proved(1, 2, 3))
        self.assertIs(got["alarm"], True, "1 из 4 — это выше пятой части")
        words = cd.series_line(got)
        self.assertIn("НЕИЗВЕСТНО 1 из 4", words)
        self.assertIn(cd.MARK[cd.RED], words, "тревога звучит тише красного")
        self.assertIn("ПРО СУДЬЮ", words, "тревога не называет, кого чинить")
        self.assertIn("НЕИЗВЕСТНО 1 из 4", cd.journal_line({"series": got, "readings": {}}))

    def test_one_unknown_in_ten_is_not_an_alarm(self):
        """Контроль рядом с тревогой: прибор, кричащий всегда, не сообщает ничего."""
        rows = [{"id": i, "outcome": "done", "goal": "ЦЕЛЬ: правка"} for i in range(1, 10)]
        rows.append(_unknown_row(10))
        got = cd.series(rows, judged=_proved(*range(1, 10)))
        self.assertIs(got["alarm"], False)
        self.assertNotIn(cd.MARK[cd.RED], cd.series_line(got))


class TestSeries(unittest.TestCase):
    """Счёт серии цепочек — отдельной строкой и с названными определениями."""

    def test_clean_is_done_and_unsure_breaks_the_streak(self):
        rows = [{"id": 1, "outcome": "done", "goal": "работа"},
                {"id": 2, "outcome": None, "goal": "работа"},
                {"id": 3, "outcome": "done", "goal": "работа"}]
        self.assertEqual(cd.series(rows, judged=_proved(1, 3))["streak"], 1,
                         "«не сверен» обязан обрывать серию")

    def test_failed_breaks_the_streak(self):
        rows = [{"id": 1, "outcome": "done", "goal": "работа"},
                {"id": 2, "outcome": "failed", "goal": "работа"}]
        self.assertEqual(cd.series(rows, judged=_proved(1, 2))["streak"], 0)

    def test_service_roots_are_out_of_the_count(self):
        """Признак служебного корня ВЗЯТ у счётчика полосы, а не выдуман здесь."""
        rows = [{"id": 1, "outcome": "done", "goal": "[ревизор-находки] сводка"},
                {"id": 2, "outcome": "done", "goal": "работа"}]
        got = cd.series(rows, judged=_proved(1, 2))
        self.assertEqual(got["service"], 1)
        self.assertEqual(got["seen"], 1)
        self.assertTrue(series_pc.is_service("[ревизор-находки] сводка"))

    def test_read_only_marks_are_derived_not_typed(self):
        """Метки читающих строк приходят из модулей-владельцев: копия разошлась бы молча."""
        self.assertEqual(cd.read_only_marks(),
                         (review_intake.CLAIM_MARK, recon_auto.TASK_MARK, recon_auto.ASK_MARK))
        self.assertFalse(cd.changes_state(review_intake.CLAIM_MARK + " дата=2026-09-01]"))
        self.assertFalse(cd.changes_state(recon_auto.TASK_MARK + " дата=2026-09-01]"))
        self.assertTrue(cd.changes_state("ЦЕЛЬ: починить очередь"))

    def test_moved_counts_only_inside_the_streak(self):
        rows = [{"id": 1, "outcome": "done", "goal": "ЦЕЛЬ: правка"},
                {"id": 2, "outcome": "failed", "goal": "ЦЕЛЬ: правка"},
                {"id": 3, "outcome": "done", "goal": "ЦЕЛЬ: правка"},
                {"id": 4, "outcome": "done", "goal": review_intake.CLAIM_MARK + " x]"}]
        got = cd.series(rows, judged=_proved(1, 3, 4))
        self.assertEqual(got["streak"], 2)
        self.assertEqual(got["moved"], 1, "заявка ревью операционного состояния не меняет")

    def test_window_is_thirty(self):
        self.assertEqual(cd.SERIES_TARGET, 30)
        rows = [{"id": i, "outcome": "done", "goal": "ЦЕЛЬ: правка"} for i in range(50)]
        got = cd.series(rows, judged=_proved(*range(50)))
        self.assertEqual(got["window"], 30)
        self.assertEqual(got["streak"], 30)

    def test_series_line_says_unknown_when_the_source_is_dead(self):
        line = cd.series_line(None, cd.dead_source("series", "queue", "файла нет"))
        self.assertIn("НЕИЗВЕСТНО", line)
        self.assertNotIn("подряд из 30", line, "нуль вместо счёта — это утверждение, а не молчание")


class TestSeriesCountsOnlyTheProven(unittest.TestCase):
    """ПОПРАВКА 02.09.2026 — серия зачитывает ТОЛЬКО ДОКАЗАННОЕ судьёй закрытие.

    Повод измерен на живом слепке полосы: серия 18 подряд, из них судья доказал 5,
    а 13 закрылись БЕЗ АДРЕСА (журнал демона, `V0-DONE` по задачам 104, 105,
    109–116). Отрицательный и положительный ходят ПАРОЙ: прибор, который не
    двигает серию никогда, проходит любой отрицательный тест и бесполезен.
    """

    ROWS = [{"id": 7, "outcome": "done", "goal": "ЦЕЛЬ: правка"},
            {"id": 8, "outcome": "done", "goal": "ЦЕЛЬ: правка"}]

    def test_a_done_row_without_an_address_does_not_move_the_series(self):
        """ОТРИЦАТЕЛЬНЫЙ. Поддельный ряд `done`, у судьи «адрес не назван» — серия стои́т."""
        got = cd.series(self.ROWS, judged=_blind(7, 8))
        self.assertEqual(got["streak"], 0, "закрытие без проверки зачтено в серию")
        self.assertEqual(got["blind"], 2)
        self.assertEqual(got["proved"], 0)

    def test_a_proven_row_does_move_the_series(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ. Тот же ряд с доказанным адресом серию двигает."""
        got = cd.series(self.ROWS, judged=_proved(7, 8))
        self.assertEqual(got["streak"], 2)
        self.assertEqual(got["proved"], 2)
        self.assertEqual(got["blind"], 0)

    def test_a_blind_row_at_the_tail_breaks_a_proven_streak(self):
        """Одно безадресное закрытие в хвосте обрывает серию доказанных — как «упало»."""
        rows = self.ROWS + [{"id": 9, "outcome": "done", "goal": "ЦЕЛЬ: правка"}]
        judged = dict(_proved(7, 8))
        judged.update(_blind(9))
        got = cd.series(rows, judged=judged)
        self.assertEqual(got["streak"], 0)
        self.assertEqual((got["proved"], got["blind"]), (2, 1))

    def test_silence_of_the_judge_is_not_a_clean_chain(self):
        """Реестра нет вовсе → «не доказан сильнее неизвестно»: серия ноль, и это видно числом."""
        for judged in (None, {}, {"нет": "того"}):
            got = cd.series(self.ROWS, judged=judged)
            self.assertEqual(got["streak"], 0, repr(judged))
            self.assertEqual(got["unjudged"], 2, repr(judged))
            self.assertEqual(got["proved"], 0, repr(judged))

    def test_judged_but_unproven_is_its_own_answer(self):
        """Четвёртый исход назван отдельно: судья смотрел и НЕ доказал ≠ он не смотрел."""
        judged = {"7": {cd.F_PROVED: False, cd.F_ADDRESSED: True},
                  "8": {cd.F_PROVED: False, cd.F_ADDRESSED: True}}
        got = cd.series(self.ROWS, judged=judged)
        self.assertEqual((got["unproved"], got["blind"], got["unjudged"]), (2, 0, 0))
        self.assertEqual(got["streak"], 0)

    def test_the_four_answers_sum_to_the_closed_rows_of_the_window(self):
        """Замок полноты: сумма разбора равна числу сданных строк окна — дыры быть не может."""
        rows = [{"id": i, "outcome": "done", "goal": "ЦЕЛЬ: правка"} for i in range(6)]
        rows.append({"id": 99, "outcome": "failed", "goal": "ЦЕЛЬ: правка"})
        judged = dict(_proved(0, 1))
        judged.update(_blind(2, 3))
        judged["4"] = {cd.F_PROVED: False, cd.F_ADDRESSED: True}
        got = cd.series(rows, judged=judged)
        self.assertEqual(got["proved"] + got["blind"] + got["unproved"] + got["unjudged"], 6)

    def test_the_line_carries_both_numbers_the_owner_needs(self):
        """Владелец видит, НА ЧЁМ стои́т серия, не открывая код (пункт 3 задания)."""
        line = cd.series_line(cd.series(self.ROWS, judged=_blind(7, 8)))
        self.assertIn("судья доказал 0", line)
        self.assertIn("закрыто без адреса 2", line)
        self.assertIn("0 чистых подряд из 30", line)

    def test_zero_is_printed_and_not_folded_away(self):
        """Исход, спрятанный при нуле, читается как «такого не бывает». Печатаются все четыре."""
        line = cd.series_line(cd.series(self.ROWS, judged=_proved(7, 8)))
        for words in ("судья доказал 2", "закрыто без адреса 0", "не доказано 0",
                      "судья не судил 0"):
            self.assertIn(words, line)

    def test_the_journal_index_carries_the_support_of_the_number(self):
        """Строка журнала несёт серию ВМЕСТЕ с опорой: одна цифра врала бы молча."""
        rep = {"interval": cd.INTERVAL_SEC, "closed_count": 2, "shtab_taken": 0,
               "series": cd.series(self.ROWS, judged=_blind(7, 8)),
               "readings": {k: [] for k in cd.TOPIC_KEYS}}
        line = cd.journal_line(rep)
        self.assertIn("доказал судья 0", line)
        self.assertIn("без адреса 2", line)

    def test_field_names_mirror_the_judge_and_do_not_drift(self):
        """Имена полей реестра ЗАИМСТВОВАНЫ у судьи: два экземпляра разошлись бы молча."""
        import done_judge_pc

        self.assertEqual(cd.F_PROVED, done_judge_pc.F_PROVED)
        self.assertEqual(cd.F_ADDRESSED, done_judge_pc.F_ADDRESSED)

    def test_the_pure_layer_does_not_import_the_judge(self):
        """Чистый слой судью НЕ тянет: реестр подают руки параметром."""
        with io.open(os.path.join(HERE, "contour_digest.py"), encoding="utf-8") as fh:
            body = fh.read()
        tree = ast.parse(body, filename="contour_digest.py")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []) or []:
                    name = (getattr(node, "module", None) or alias.name).split(".")[0]
                    self.assertNotIn(name, ("done_judge_pc", "content_product_verifier"))

    def test_series_line_is_always_present_in_the_message(self):
        rep = run.build(root=tempfile.gettempdir(), now=NOW, since=NOW - 10,
                        runner=lambda *a, **k: (_ for _ in ()).throw(OSError("нет")))
        self.assertIn("СЕРИЯ ЦЕПОЧЕК:", cd.render(rep))


# ───────────── ИМЯ РЯДА ПРОТИВ ПЕРЕИСПОЛЬЗУЕМОГО НОМЕРА (задание 32-a) ─────────────

_GOAL_OLD = "[от Штаба дата=2026-09-09 ключ=09-x-staryi-ryad.0909] ЗАДАНИЕ ШТАБА ИЗ ЯЩИКА"
_GOAL_NEW = "[от Штаба дата=2026-09-11 ключ=32-a-imya-ryada.1109] ЗАДАНИЕ ШТАБА ИЗ ЯЩИКА"
_GOAL_SAME_DAY = "[от Штаба дата=2026-09-11 ключ=31-a-pokaz-neizvestno.1109] ЗАДАНИЕ ШТАБА"
_VERDICT_PROVED = {"verdict": done_judge_pc.DONE, "address": {"words": "слова адреса"},
                   "reason": "V0: PROVEN / all_gates_passed"}


def _named_row(tid, goal, at=100.0):
    """Закрытая строка слепка — РОВНО те поля, что кладёт `contour_digest_run.all_closed`.

    Имя своё, а не `_closed`: под этим именем в файле УЖЕ живёт помощник (:55) с другой
    формой аргументов, и второй экземпляр молча заслонил бы первый — 28 чужих тестов
    падали с `TypeError`, пока имя было общим (замер 11.09).
    """
    return {"id": tid, "at": at, "outcome": "done", "goal": goal, "why": ""}


def _note_as_the_lane_does(root, tid, verdict, goal=None):
    """Позвать писателя ТАК, КАК ЕГО ЗОВЁТ ПОЛОСА НА ЭТОМ КОДЕ.

    Шим назван вслух и нужен ровно контролю обратной стороны: до правки имени не
    существует вовсе, и контроль, написанный только под новый вызов, падал бы на
    HEAD по отсутствию имени, а не по существу. Тогда «честная цепочка считается»
    осталось бы недоказанным ДО правки — то есть нечем было бы отличить починку от
    показа, который молчит всегда.
    """
    try:
        return done_judge_pc.note(tid, verdict, root=root, name=cd.row_name(tid, goal))
    except (AttributeError, TypeError):                  # код до 11.09: имени нет ни в одной двери
        return done_judge_pc.note(tid, verdict, root=root)


class TestRowNameNotNumber(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ №2 переписи 30-a: вердикт под номером не смеет зачесть ДРУГОЙ ряд.

    Корпус — живая улика полосы: за 10.09.2026 нумерация очереди начиналась заново
    ДВАЖДЫ, и в реестре судьи (плоский `{номер: вердикт}`, 158 ключей от 1 до 245,
    поля дня нет ни одного) лежат номера И до сброса, И после. Обычный тест здесь
    зелен всегда: запись есть, число печатается, исключений нет.

    ПИСАТЕЛЬ И ЧИТАТЕЛЬ ЗДЕСЬ НАСТОЯЩИЕ, а реестр лежит файлом на своём корне:
    между `done_judge_pc.note` и `contour_digest.judged_of` лежит ключ словаря, и
    молча разойтись может именно он. Боевого реестра и боевой очереди тест не
    касается ни одной веткой.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="row_name_32a_")
        self.addCleanup(__import__("shutil").rmtree, self.root, True)

    def _series(self, rows):
        return cd.series(rows, judged=done_judge_pc.read_ledger(self.root))

    # ─── падает на сегодняшнем коде ───

    def test_a_verdict_left_under_the_bare_number_does_not_count_a_new_row(self):
        """ГЛАВНЫЙ: `proved` под номером 4 от 09.09 против НОВОГО ряда 4 от 11.09.

        Пишется голым номером НАМЕРЕННО — так писал код до 11.09, и ровно так лежат
        151 запись живого реестра, чьё имя восстановить нечем.
        """
        done_judge_pc.note(4, _VERDICT_PROVED, root=self.root)
        got = self._series([_named_row(4, _GOAL_NEW)])
        self.assertEqual(got["streak"], 0, "чужой вердикт зачёл ряд, которого судья не судил")
        self.assertEqual(got["proved"], 0)
        self.assertEqual(got["unjudged"], 1, "исход обязан быть «судья не судил», а не «доказано»")

    def test_a_verdict_of_the_previous_row_with_the_same_number_does_not_count(self):
        """То же, но вердикт лёг ИМЕНЕМ прежнего ряда: имя обязано не совпасть."""
        _note_as_the_lane_does(self.root, 4, _VERDICT_PROVED, goal=_GOAL_OLD)
        got = self._series([_named_row(4, _GOAL_NEW)])
        self.assertEqual(got["streak"], 0)
        self.assertEqual(got["unjudged"], 1)

    def test_the_judge_writes_the_name_and_not_the_number(self):
        """Продукт задания: ключ реестра — имя. Иначе сшивка осталась бы номерной."""
        _note_as_the_lane_does(self.root, 4, _VERDICT_PROVED, goal=_GOAL_NEW)
        keys = list(done_judge_pc.read_ledger(self.root))
        self.assertEqual(keys, ["32-a-imya-ryada.1109#4"])

    # ─── КОНТРОЛЬ ОБРАТНОЙ СТОРОНЫ: зелен и ДО правки ───

    def test_an_honest_proven_chain_is_still_counted(self):
        """Показ, который не засчитывает НИКОГДА, зелен по той же причине, что сломанный."""
        _note_as_the_lane_does(self.root, 4, _VERDICT_PROVED, goal=_GOAL_NEW)
        got = self._series([_named_row(4, _GOAL_NEW)])
        self.assertEqual(got["streak"], 1, "честная доказанная цепочка перестала считаться")
        self.assertEqual(got["proved"], 1)

    def test_a_chain_of_three_honest_rows_still_grows(self):
        """Серия обязана РАСТИ: единица могла бы быть случайностью одной ветки."""
        rows = []
        for num, goal in ((1, _GOAL_OLD), (2, _GOAL_SAME_DAY), (3, _GOAL_NEW)):
            _note_as_the_lane_does(self.root, num, _VERDICT_PROVED, goal=goal)
            rows.append(_named_row(num, goal, at=100.0 + num))
        got = self._series(rows)
        self.assertEqual((got["streak"], got["proved"]), (3, 3))

    # ─── свойства самого имени ───

    def test_two_resets_in_one_day_are_still_two_names(self):
        """Почему не «номер плюс день»: 10.09 нумерация сбрасывалась ДВАЖДЫ ЗА СУТКИ.

        Оба маркера несут ОДИН день — «номер+день» дал бы им одно имя, и вердикт
        одного ряда зачёл бы другой.
        """
        self.assertNotEqual(cd.row_name(4, _GOAL_NEW), cd.row_name(4, _GOAL_SAME_DAY))

    def test_a_resent_task_keeps_its_key_and_changes_its_number(self):
        """Почему не «ключ маркера» один: у переотправленной задачи ключ ТОТ ЖЕ."""
        self.assertNotEqual(cd.row_name(4, _GOAL_NEW), cd.row_name(9, _GOAL_NEW))

    def test_a_row_without_a_marker_keeps_the_number_and_that_is_named(self):
        """Названный остаток: маркера нет — имени нет, остаётся АДРЕС (номер)."""
        self.assertEqual(cd.row_name(4, "ЦЕЛЬ: правка"), "4")
        self.assertEqual(cd.row_name(4, None), "4")

    def test_the_name_is_raised_from_the_row_and_not_from_a_report_about_it(self):
        """Имя поднимается из ПЕРВОЙ строки ряда, а не из текста, где маркер упомянут."""
        self.assertEqual(cd.row_name(4, "отчёт: задача [от Штаба дата=2026-09-11 "
                                        "ключ=32-a-imya-ryada.1109] сдана"), "4")

    def test_the_one_form_agrees_with_every_marker_owner(self):
        """Пятый экземпляр разбора разошёлся бы молча — согласие сверяется с хозяевами."""
        for owner, sample in ((shtab_box.MARK_RE, _GOAL_NEW),
                              (recon_auto._TASK_RE, "[разведка дата=2026-09-05 ключ=cc17298127d2]"),
                              (recon_auto._ASK_RE,
                               "[разведка-заявка дата=2026-09-05 ключ=cc17298127d2]"),
                              (review_intake._RE_CLAIM,
                               "[заявка-ревью дата=2026-09-09 ключ=eab084d7799b]")):
            his = owner.match(sample)
            mine = cd.ROW_MARK.match(sample)
            self.assertIsNotNone(his, "образец разошёлся с живой регуляркой хозяина: %r" % sample)
            self.assertIsNotNone(mine, "своя форма не узнала живой маркер: %r" % sample)
            self.assertEqual(mine.group(1), his.group(2))
            self.assertEqual(cd.row_name(7, sample), "%s#7" % his.group(2))

    def test_the_longest_legal_marker_survives_the_goal_line(self):
        """Граница измерена: писатель видит ТЕКСТ, читатель — обрезанную цель.

        Обрезка `goal_line` по 90 символов обязана оставить маркер целым — иначе
        имена двух дверей разошлись бы молча на длинном ключе.
        """
        longest = "[от Штаба дата=2026-09-11 ключ=%s] ЗАДАНИЕ ШТАБА ИЗ ЯЩИКА" % ("k" * 40)
        self.assertLessEqual(longest.index("]") + 1, queue_snapshot_pc.GOAL_MAX)
        self.assertEqual(cd.row_name(4, queue_snapshot_pc.goal_line(longest)),
                         cd.row_name(4, longest))
        self.assertEqual(cd.row_name(4, longest), "%s#4" % ("k" * 40))

    def test_the_reader_asks_the_ledger_by_name_end_to_end(self):
        """Обе двери одной строкой: что писатель положил, читатель обязан найти."""
        _note_as_the_lane_does(self.root, 5, _VERDICT_PROVED, goal=_GOAL_NEW)
        said = cd.judged_of(5, done_judge_pc.read_ledger(self.root), goal=_GOAL_NEW)
        self.assertEqual(said, cd.JUDGE_PROVED)


class TestSeriesEndToEnd(unittest.TestCase):
    """ТОТ ЖЕ ОТРИЦАТЕЛЬНЫЙ, НО ЧЕРЕЗ ВЕСЬ ХОД — слепок на диске → руки → строка сообщения.

    Чистая функция может быть права, а сводка всё равно врать: между ними лежит
    чтение реестра (:func:`contour_digest_run.read_judged`), и молча отвалиться
    может именно оно. Поэтому ряды здесь ПОДДЕЛЬНЫЕ и лежат файлами на своём
    корне — боевой очереди и боевого реестра тест не касается ни одной веткой.
    """

    def _root(self, judged):
        root = tempfile.mkdtemp(prefix="digest_series_")
        closed = dict([_closed(7, NOW - 300, "done", "ЦЕЛЬ: поддельный ряд один"),
                       _closed(8, NOW - 200, "done", "ЦЕЛЬ: поддельный ряд два")])
        with io.open(os.path.join(root, cd.source("queue")["addr"]), "w", encoding="utf-8") as fh:
            json.dump(_snapshot(closed_rows=closed), fh)
        if judged is not None:
            path = os.path.join(root, "tmp", "done_judge_pc")
            os.makedirs(path)
            with io.open(os.path.join(path, "judged.json"), "w", encoding="utf-8") as fh:
                json.dump({"schema_version": "v1", "rows": judged}, fh)
        return root

    def _line(self, judged):
        root = self._root(judged)
        rep = run.build(root=root, now=NOW, since=NOW - 600,
                        runner=lambda *a, **k: (_ for _ in ()).throw(OSError("нет git")))
        return rep, cd.render(rep)

    def test_addressless_rows_do_not_move_the_series_and_are_visible(self):
        """ОТРИЦАТЕЛЬНЫЙ (пункт 5 задания): ряд `done` без адреса серию не двигает и ВИДЕН."""
        rep, text = self._line(_blind(7, 8))
        self.assertEqual(rep["series"]["streak"], 0)
        self.assertEqual(rep["series"]["blind"], 2)
        self.assertIn("закрыто без адреса 2", text)

    def test_proven_rows_do_move_the_series(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ на том же ходу: доказанный адрес серию двигает."""
        rep, text = self._line(_proved(7, 8))
        self.assertEqual(rep["series"]["streak"], 2)
        self.assertIn("судья доказал 2", text)

    def test_a_missing_ledger_is_not_a_clean_series(self):
        """Реестра на диске нет — серия ноль, и молчание названо словом «не судил»."""
        rep, text = self._line(None)
        self.assertEqual(rep["series"]["streak"], 0)
        self.assertEqual(rep["series"]["unjudged"], 2)
        self.assertIn("судья не судил 2", text)

    def test_the_hands_read_the_ledger_with_the_judges_own_code(self):
        """Путь и форма записи принадлежат судье: свой разбор разошёлся бы молча."""
        import done_judge_pc

        root = self._root(_proved(7))
        self.assertEqual(run.read_judged(root), done_judge_pc.read_ledger(root))
        self.assertIn("7", run.read_judged(root))


class TestCalm(unittest.TestCase):
    """Спокойно — одной строкой, и разделов при этом нет."""

    def _calm_report(self):
        return {"interval": cd.INTERVAL_SEC, "since_words": "a", "now_words": "b",
                "closed_count": 0, "series": cd.series([]),
                "readings": {k: [cd.reading(k, "тихо", src="queue", read_at=NOW, now=NOW)]
                             for k in cd.TOPIC_KEYS}}

    def test_calm_is_one_line_without_sections(self):
        rep = self._calm_report()
        self.assertTrue(cd.is_calm(rep))
        text = cd.render(rep)
        self.assertIn("СПОКОЙНО:", text)
        self.assertNotIn("ЖДЁТ РЕШЕНИЯ ВЛАДЕЛЬЦА:", text)
        self.assertLess(len(text.splitlines()), 12, "спокойная сводка раздулась")

    def test_series_survives_the_calm_fold(self):
        """Серию просили ЧИСЛОМ, а не признаком новости: она печатается и в тишине."""
        self.assertIn("СЕРИЯ ЦЕПОЧЕК:", cd.render(self._calm_report()))

    def test_waiting_for_the_owner_is_not_calm(self):
        rep = self._calm_report()
        rep["readings"]["await"] = [cd.reading("await", "ждут 3", src="queue", read_at=NOW,
                                               now=NOW, kind=cd.WAIT)]
        self.assertFalse(cd.is_calm(rep))
        self.assertIn("ЖДЁТ РЕШЕНИЯ ВЛАДЕЛЬЦА:", cd.render(rep))

    def test_a_topic_without_a_line_is_a_failure_not_a_skip(self):
        rep = self._calm_report()
        rep["readings"]["red"] = [cd.reading("red", "упало", src="queue", read_at=NOW,
                                             now=NOW, kind=cd.RED)]
        rep["readings"]["await"] = []
        with self.assertRaises(AssertionError):
            cd.render(rep)


class TestAddress(unittest.TestCase):
    """Адрес и дверь: тема сводок или молчание, третьего нет."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with io.open(os.path.join(self.tmp, cd.source("queue")["addr"]), "w",
                     encoding="utf-8") as fh:
            json.dump(_snapshot(closed_rows=dict([_closed(7, NOW - 100, "done", "ЦЕЛЬ: x")])), fh)
        self.sent = []

    def _sender(self, text, topic):
        self.sent.append((text, topic))
        return "topic", True, "9099"

    def test_topic_not_set_sends_nothing(self):
        class Daemon:
            AUDIT_TOPIC = 0

        rep = run.tick(root=self.tmp, now=NOW, send=True, daemon=Daemon(),
                       sender=self._sender, runner=lambda *a, **k: (_ for _ in ()).throw(OSError))
        self.assertEqual(self.sent, [], "ненастроенная тема обязана означать молчание")
        self.assertFalse(rep["sent"])
        self.assertIn("PC_AUDIT_TOPIC", rep["send_why"])

    def test_configured_topic_gets_the_message(self):
        class Daemon:
            AUDIT_TOPIC = 4242

        rep = run.tick(root=self.tmp, now=NOW, send=True, daemon=Daemon(),
                       sender=self._sender, runner=lambda *a, **k: (_ for _ in ()).throw(OSError))
        self.assertTrue(rep["sent"])
        self.assertEqual([t for _txt, t in self.sent], [4242])
        self.assertIn("СВОДКА КОНТУРА", self.sent[0][0])

    def test_failed_send_does_not_move_the_window(self):
        """Сорванная отправка оставляет окно прежним — иначе период проглатывается молча."""
        class Daemon:
            AUDIT_TOPIC = 4242

        def dead(_text, _topic):
            return "topic", False, "канал молчит"

        run.tick(root=self.tmp, now=NOW, send=True, daemon=Daemon(), sender=dead,
                 runner=lambda *a, **k: (_ for _ in ()).throw(OSError))
        st = run.read_state(run.state_path(self.tmp))
        self.assertNotIn("last", st, "окно сдвинулось при несостоявшейся отправке")

    def test_interval_holds_between_ticks(self):
        class Daemon:
            AUDIT_TOPIC = 4242

        run.tick(root=self.tmp, now=NOW, send=True, daemon=Daemon(), sender=self._sender,
                 runner=lambda *a, **k: (_ for _ in ()).throw(OSError))
        again = run.tick(root=self.tmp, now=NOW + 60, send=True, daemon=Daemon(),
                         sender=self._sender,
                         runner=lambda *a, **k: (_ for _ in ()).throw(OSError))
        self.assertIn("интервал не вышел", again.get("skipped", ""))
        self.assertEqual(len(self.sent), 1)

    def test_the_message_carries_no_button(self):
        """Кнопка означает «жду ответа», а сводку читают. Дверь у нас без разметки."""
        class Daemon:
            AUDIT_TOPIC = 4242

        rep = run.tick(root=self.tmp, now=NOW, send=True, daemon=Daemon(), sender=self._sender,
                       runner=lambda *a, **k: (_ for _ in ()).throw(OSError))
        self.assertNotIn("reply_markup", json.dumps(rep, default=str))


class TestGitWindow(unittest.TestCase):
    """Окно движения осей — с НАЗВАННЫМ поясом: полоса живёт на UTC+7."""

    def test_since_carries_a_timezone(self):
        seen = {}

        class Done:
            returncode = 0
            stdout = b""
            stderr = b""

        def runner(argv, **_kw):
            seen["argv"] = argv
            return Done()

        run.git_moves(HERE, NOW, ("x.py",), runner=runner)
        since = [a for a in seen["argv"] if a.startswith("--since=")][0]
        self.assertIn("+00:00", since, "наивная метка уехала бы на семь часов")

    def test_git_failure_is_unknown_not_zero(self):
        class Done:
            returncode = 128
            stdout = b""
            stderr = b"fatal: not a git repository"

        rows, why = run.git_moves(HERE, NOW, ("x.py",), runner=lambda *a, **k: Done())
        self.assertIsNone(rows, "«git не ответил» это не «коммитов ноль»")
        self.assertIn("128", why)

    def test_axis_paths_name_both_axes(self):
        self.assertIn("pc_orchestrator.py", run.AXIS3_PATHS)
        self.assertIn("suggest.py", run.BUSINESS_PATHS)
        self.assertFalse(set(run.AXIS3_PATHS) & set(run.BUSINESS_PATHS),
                         "один путь в двух осях — ответ станет двусмысленным")


class TestWiring(unittest.TestCase):
    """Врезка в демона: один вызов, один рубильник, и адрес не подменён числом."""

    def setUp(self):
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as fh:
            self.body = fh.read()

    def test_called_exactly_once_in_the_loop(self):
        self.assertEqual(self.body.count("maybe_contour_digest()"), 1,
                         "сводка зовётся не один раз — окно поедет")
        self.assertEqual(self.body.count("def maybe_contour_digest"), 1)

    def test_interval_is_four_hours_by_number(self):
        self.assertIn('CONTOUR_DIGEST_SEC = float(os.getenv("CONTOUR_DIGEST_SEC", "14400")',
                      self.body)
        self.assertEqual(cd.INTERVAL_SEC, 14400,
                         "число интервала в демоне и в чистой логике обязано совпадать")

    def test_rollback_switches_exist(self):
        self.assertIn('os.environ.get("CONTOUR_DIGEST")', self.body)
        self.assertIn('_flag_forced_off("CONTOUR_DIGEST")', self.body)

    def test_the_daemon_does_not_name_a_topic_for_the_digest(self):
        """Адрес сводки берётся ручкой темы, а не числом рядом с вызовом."""
        head = self.body.split("def maybe_contour_digest")[1].split("\n# ---")[0]
        for known in ("328", "1160", "829"):
            self.assertNotIn(known, head, "номер темы просочился во врезку: %s" % known)


class TestLiveTree(unittest.TestCase):
    """Живое дерево: сборка проходит и все темы на месте."""

    def test_build_on_the_real_repo_keeps_every_topic(self):
        rep = run.build(root=HERE)
        for key in cd.TOPIC_KEYS:
            self.assertTrue(rep["readings"].get(key), "тема %r пропала на живом дереве" % key)
        text = cd.render(rep)
        self.assertIn("СВОДКА КОНТУРА", text)
        self.assertIn("СЕРИЯ ЦЕПОЧЕК:", text)
        self.assertLess(len(text), 4000, "сообщение не влезет в один пост канала")

    def test_journal_line_is_an_index_not_a_body(self):
        rep = run.build(root=HERE)
        rep["series"] = rep.get("series") or cd.series([])
        line = cd.journal_line(rep)
        self.assertTrue(line.startswith("NOTE "))
        self.assertLess(len(line), 600, "строка журнала обязана быть индексом, а не телом")


class TestExternalAnswersLine(unittest.TestCase):
    """ВНЕШНИЕ ОТВЕТЫ ЗА СУТКИ — дайджест, а не поток.

    Проверяется по существу: числа задания стои́т в строке ВСЕ ТРИ, чужого текста
    в ней нет ни знака, а молчание канала названо СЛОВОМ, а не нулём.
    """

    DAY = "2026-09-01"

    def heads(self):
        """Живой разрез 01.09 в форме, которую отдаёт ``answer_headers``."""
        rows = []
        for n in range(6):
            rows.append({"channel": "codex", "send_date": self.DAY, "outcome": "answered",
                         "reason": "", "answered": True, "rel": "docs/review_inbox/a%d.md" % n})
        for n in range(10):
            rows.append({"channel": "manus", "send_date": self.DAY, "outcome": "refused",
                         "reason": "http_400", "answered": False, "rel": "docs/review_inbox/m%d.md" % n})
        for n in range(3):
            rows.append({"channel": "manus", "send_date": self.DAY, "outcome": "refused",
                         "reason": "accepted_no_answer", "answered": False,
                         "rel": "docs/review_inbox/s%d.md" % n})
        # Чужой день в корпусе есть намеренно: он обязан выпасть из счёта.
        rows.append({"channel": "codex", "send_date": "2026-08-31", "outcome": "answered",
                     "reason": "", "answered": True, "rel": "docs/review_inbox/old.md"})
        return rows

    def records(self):
        out = [{"send_date": self.DAY, "kind": "дефект", "quote": "СЕКРЕТНЫЙ ЧУЖОЙ ТЕКСТ ревьюера"}
               for _ in range(4)]
        out += [{"send_date": self.DAY, "kind": "переусложнено", "quote": "ещё чужой текст"}]
        out += [{"send_date": "2026-08-31", "kind": "иное", "quote": "вчерашнее"}]
        return out

    def test_all_three_numbers_of_the_task_are_in_the_line(self):
        got = cd.external_stats(self.heads(), self.records(), self.DAY)
        self.assertEqual(got["answers"], 6)
        self.assertEqual(got["channels_answered"], 1)
        self.assertEqual(got["refused"], 13)
        words = cd.external_words(got)
        self.assertIn("пришло 6", words)
        self.assertIn("каналов ответило 1 из 2", words)
        self.assertIn("отказов канала 13", words)

    def test_the_day_is_a_calendar_day_and_the_neighbour_day_is_out(self):
        """Окно — КАЛЕНДАРНЫЙ день: скользящих суток в источнике нет вовсе."""
        got = cd.external_stats(self.heads(), self.records(), self.DAY)
        self.assertEqual(got["attempts"], 19, "заход соседнего дня попал в счёт")
        self.assertEqual(got["findings"], 5, "находка соседнего дня попала в счёт")
        self.assertIn(self.DAY, cd.external_words(got), "день обязан быть НАЗВАН в строке")

    def test_a_silent_channel_is_called_down_not_zero(self):
        """«Канал лежал» — отдельное слово: ноль читался бы как «нечего сказать»."""
        got = cd.external_stats(self.heads(), self.records(), self.DAY)
        self.assertEqual(got["down"], ["manus"])
        words = cd.external_words(got)
        self.assertIn("КАНАЛ ЛЕЖАЛ: manus", words)
        self.assertEqual(cd.external_verdict(got), cd.RED,
                         "лежащий канал — это красное, а не спокойствие")

    def test_a_living_channel_is_not_red(self):
        alive = [h for h in self.heads() if h["answered"]]
        got = cd.external_stats(alive, self.records(), self.DAY)
        self.assertEqual(got["down"], [])
        self.assertEqual(cd.external_verdict(got), cd.OK)

    def test_no_foreign_text_ever_reaches_the_line(self):
        """САМОЕ ДОРОГОЕ здесь: дайджест, а не поток — счёт и темы, не тела."""
        words = cd.external_words(cd.external_stats(self.heads(), self.records(), self.DAY))
        self.assertNotIn("СЕКРЕТНЫЙ", words)
        self.assertNotIn("чужой текст", words)
        self.assertIn("темы находок (5)", words)
        self.assertIn("дефект ×4", words)
        self.assertIn("тексты не пересылаем", words)
        # Указатель на лоток обязан быть в ГОТОВОЙ строке — его ставит адрес
        # источника, а не второй литерал внутри слов.
        rd = cd.reading("external", words, src="inbox", read_at=NOW, now=NOW, kind=cd.OK)
        self.assertIn(cd.INBOX_POINTER, cd.line_text(rd))
        self.assertEqual(cd.line_text(rd).count(cd.INBOX_POINTER), 1,
                         "адрес лотка напечатан дважды — два экземпляра разойдутся молча")

    def test_the_pointer_is_the_source_address_not_a_second_literal(self):
        """Два экземпляра одного адреса расходятся молча — класс полосы."""
        self.assertEqual(cd.INBOX_POINTER, cd.source("inbox")["addr"])
        self.assertIsNone(cd.limit_of("inbox"), "лоток читается живьём — возраста не имеет")

    def test_an_empty_day_says_so_instead_of_pretending_calm(self):
        got = cd.external_stats([], [], self.DAY)
        words = cd.external_words(got)
        self.assertIn("заходов в каналы не было", words)
        self.assertEqual(cd.external_verdict(got), cd.OK)

    def test_a_missing_tray_is_unknown_not_an_empty_day(self):
        """Отсутствующий лоток и пустой — РАЗНЫЕ новости, и различает их код."""
        with tempfile.TemporaryDirectory() as tmp:
            heads, records, why = run.read_inbox(tmp)
            self.assertIsNone(heads)
            self.assertIsNone(records)
            self.assertIn("лоток не найден", why)
            rows = run.section_external(heads, records, why, self.DAY, NOW)
            self.assertEqual(rows[0]["kind"], cd.UNKNOWN)
            self.assertNotIn(tmp, cd.line_text(rows[0]),
                             "абсолютный путь в строке задержал бы сообщение стражем")

    def test_the_day_of_the_window_is_utc(self):
        """Местный день сдвинул бы разрез на семь часов — известная мина полосы."""
        # 2026-09-01 23:30 UTC: местное время полосы уже 02.09, день обязан быть 01.
        self.assertEqual(run.day_utc(1788305400.0), "2026-09-01")

    def test_the_topic_is_in_the_message_on_the_live_repo(self):
        rep = run.build(root=HERE)
        self.assertTrue(rep["readings"].get("external"))
        text = cd.render(rep) if not cd.is_calm(rep) else ""
        if text:
            self.assertIn("ВНЕШНИЕ ОТВЕТЫ ЗА СУТКИ", text)


class TestFindingFate(unittest.TestCase):
    """СУДЬБА НАХОДКИ И ПОЛЬЗА ОТ НЕЁ (заведено 04.09.2026).

    Самое дорогое здесь — ``test_a_claim_alone_is_never_a_benefit``: заявку
    ступени B получает почти каждая находка, и посчитай мы её пользой, строка
    хвалила бы внешние каналы за разговорчивость. Остальное — третий исход у
    КАЖДОГО нового числа и потолок в четыре строки прироста.
    """

    DAY = "2026-09-03"

    # Тексты находок РАЗНЫЕ ПО СМЫСЛУ, а не по номеру: ключ считается по МЕРЕ
    # СМЫСЛА цитаты (:func:`review_intake.claim_key`), и «находка номер 1/2/3»
    # дала бы пять записей с ОДНИМ ключом — корпус, на котором зелёными были бы
    # любые числа. Поймано живьём при сборке 04.09.
    QUOTES = (
        "СЕКРЕТНАЯ премиса заявки читается очередью и логом без правки кода",
        "СЕКРЕТНЫЙ порог тишины наблюдателя измерен корпусом двадцати суток",
        "СЕКРЕТНЫЙ адрес результата задания назван последней строкой файла",
        "СЕКРЕТНЫЙ слепок очереди стареет быстрее реестра исходящих ступени",
        "СЕКРЕТНЫЙ маркер отказа владельца лежит в причине упавшей строки",
    )

    def records(self):
        """Пять находок суток + одна вчерашняя: чужой день обязан выпасть."""
        out = [{"send_date": self.DAY, "kind": "УПРОЩАЕМО", "channel": "codex",
                "pack": "p.md", "index": i, "quote": quote}
               for i, quote in enumerate(self.QUOTES)]
        out.append({"send_date": "2026-09-02", "kind": "УПРОЩАЕМО", "channel": "codex",
                    "pack": "p.md", "index": 99, "quote": "вчерашняя чужая находка о другом"})
        return out

    def keys(self):
        keys = [review_intake.claim_key(r) for r in self.records() if r["send_date"] == self.DAY]
        assert len(set(keys)) == len(keys), "корпус теста склеился в один ключ"
        return keys

    def intake(self):
        """Реестр ступени B: четыре находки из пяти дошли до заявки."""
        keys = self.keys()
        return {"claims": {k: {"keys": [k], "queue_id": 100 + n, "kind": "УПРОЩАЕМО"}
                           for n, k in enumerate(keys[:4])}}

    def recon(self):
        """Реестр ступени E: разведка, заявка владельцу и придержанное отбором."""
        keys = self.keys()
        place = recon_auto.key_of(recon_auto.SRC_CLAIM, keys[0])
        owner = recon_auto.key_of(recon_auto.SRC_CLAIM, keys[1])
        held = recon_auto.key_of(recon_auto.SRC_CLAIM, keys[2])
        return {"placed": {place: {"way": recon_auto.ROUTE_RECON, "src": recon_auto.SRC_CLAIM,
                                   "queue_id": 200},
                           owner: {"way": recon_auto.ROUTE_OWNER, "src": recon_auto.SRC_CLAIM,
                                   "queue_id": 201}},
                "held": {held: {"why": "бюджет суток исчерпан (2 автозадачи)"}}}

    def queue(self):
        """Слепок очереди: в работе, сдана, отклонена владельцем, ждёт решения."""
        return _snapshot(
            {"100": {"goal": "заявка", "status": "in_progress"},
             "103": {"goal": "заявка", "status": "needs_approval"}},
            dict([_closed(101, NOW - 60, "done", "заявка"),
                  _closed(102, NOW - 60, "rejected", "заявка")]))

    def fate(self, **swap):
        got = {"records": self.records(), "day": self.DAY, "intake": self.intake(),
               "recon": self.recon(), "queue": self.queue()}
        got.update(swap)
        return cd.external_fate(got["records"], got["day"], intake=got["intake"],
                                recon=got["recon"], queue=got["queue"])

    def test_all_five_outcomes_of_the_task_are_counted(self):
        """Пять исходов задания — числами, и каждый своим."""
        got = self.fate()
        self.assertEqual(got["findings"], 5, "находка соседнего дня попала в счёт")
        self.assertEqual(got["claimed"], 4, "заявка владельцу")
        self.assertEqual(got["recon"], 1, "автозадача разведки")
        self.assertEqual(got["owner_ask"], 1, "заявка владельцу от ступени E")
        self.assertEqual(got["held"], 1, "придержано отбором ступени E")
        self.assertEqual(got["rejected"], 1, "владелец отклонил")
        self.assertEqual(got["waiting"], 1, "ждёт решения владельца")
        self.assertEqual(got["worked"], 1, "сдано работой")
        self.assertEqual(got["unmapped"], 1, "находка вне реестра ступени B")

    def test_a_claim_alone_is_never_a_benefit(self):
        """ПРЕДСМЕРТНЫЙ ВЗГЛЯД ЗАДАНИЯ: заявка — машинный шаг, а не польза.

        Заявок четыре, дошедших до дела — две. Посчитай мы пользой «есть заявка»,
        число росло бы вместе с болтливостью канала, а не с его толком.
        """
        got = self.fate()
        self.assertEqual(got["benefit"], 2, "польза обязана считать ДЕЛО, а не заявку")
        self.assertLess(got["benefit"], got["claimed"],
                        "польза, равная числу заявок, хвалит канал за разговорчивость")
        words = cd.benefit_words(got)
        self.assertIn("наличие заявки пользой НЕ считается", words,
                      "определение обязано ехать В СТРОКЕ, а не остаться в коде")
        self.assertIn("правило прибора не имеет", words,
                      "правило прибора не имеет — это обязано быть сказано, а не подразумеваться")

    def test_a_finding_with_neither_task_nor_done_row_is_not_useful(self):
        """Заявка есть, дела нет → в пользу НЕ идёт ни по одной ветке."""
        got = self.fate(recon={"placed": {}, "held": {}},
                        queue=_snapshot({"100": {"goal": "з", "status": "needs_approval"}}, {}))
        self.assertEqual(got["claimed"], 4)
        self.assertEqual(got["benefit"], 0, "четыре заявки без дела дали пользу")

    def test_a_dead_registry_says_unknown_and_never_zero(self):
        """ТРЕТИЙ ИСХОД у каждого нового числа: молчание прибора ≠ ноль."""
        got = self.fate(intake=None, recon=None, queue=None)
        for field in ("claimed", "unmapped", "recon", "owner_ask", "held",
                      "waiting", "rejected", "worked", "benefit"):
            self.assertIsNone(got[field], "поле %s подменило молчание нулём" % field)
        for words in (cd.fate_words_intake(got), cd.fate_words_recon(got),
                      cd.fate_words_queue(got), cd.benefit_words(got)):
            self.assertIn(cd.FATE_UNKNOWN, words)
            self.assertNotIn(" 0 ", " %s " % words, "ноль вместо «неизвестно»")
        # У СТУПЕНИ B «неизвестно» ТЕПЕРЬ ОДНО ПОСТОЯННОЕ («закрыто отбором» прибора
        # не имеет никогда), и одного `assertIn` выше стало мало: он зеленел бы и на
        # строке, где живые числа втихую съехали в нули. Считаем ШТУКИ — при мёртвом
        # реестре их обязано быть три (заявки, вне реестра, отбор).
        self.assertGreaterEqual(cd.fate_words_intake(got).count(cd.FATE_UNKNOWN), 3,
                                "постоянное «неизвестно» прикрыло собой нули живых чисел")

    def test_one_dead_registry_does_not_erase_the_others(self):
        """Смерть ступени E не отменяет посчитанного ступенью B."""
        got = self.fate(recon=None)
        self.assertEqual(got["claimed"], 4)
        self.assertIsNone(got["recon"])
        self.assertIsNone(got["benefit"], "польза стои́т на ДВУХ реестрах — половины мало")

    def test_benefit_needs_both_registries_alive(self):
        """Нижняя оценка, напечатанная как число, — это враньё прибора."""
        self.assertIsNone(self.fate(queue=None)["benefit"])
        self.assertIsNone(self.fate(recon=None)["benefit"])
        self.assertEqual(self.fate()["benefit"], 2)

    def test_a_stale_snapshot_keeps_its_numbers_at_home(self):
        """Слепок старше предела → числа наружу НЕ ЕДУТ, как у серии и ящика."""
        fresh = run.fresh_or_none({"claims": {}}, NOW - 10, NOW, "intake")
        self.assertIsNotNone(fresh)
        old = run.fresh_or_none({"claims": {}}, NOW - cd.limit_of("intake") - 1, NOW, "intake")
        self.assertIsNone(old, "числа поехали по протухшему реестру")
        self.assertIsNone(run.fresh_or_none({"claims": {}}, None, NOW, "intake"),
                          "возраст не сверить → числа не едут")

    def _grown(self):
        """Четыре строки прироста — те же, что уедут в Telegram, и в том же виде."""
        heads = [{"channel": "codex", "send_date": self.DAY, "outcome": "answered",
                  "reason": "", "answered": True, "rel": "docs/review_inbox/a.md"}]
        rows = run.section_external(heads, self.records(), "", self.DAY, NOW,
                                    intake=self.intake(), i_at=NOW - 10,
                                    recon=self.recon(), r_at=NOW - 10,
                                    queue=self.queue(), q_at=NOW - 10)
        return rows, rows[1:]

    def test_the_growth_of_the_section_is_four_lines_and_no_more(self):
        """Сводку читают с телефона: стена хуже отсутствия."""
        rows, grown = self._grown()
        self.assertEqual(len(rows), 5, "прирост раздела больше четырёх строк")
        self.assertEqual(len(grown), 4)
        self.assertLessEqual(len(rows), cd.LIST_MAX,
                             "строки раздела не влезают в LIST_MAX и обрежутся молча")
        self.assertEqual({rd["topic"] for rd in rows}, {"external"})

    def test_four_lines_are_counted_in_characters_and_not_in_rows(self):
        """ПОПРАВКА 04.09: «четыре строки» кода — не четыре строки телефона.

        Первая сборка держала потолок в единицах кода и была зелёной, а на экране
        шириной 36 знаков разворачивалась в 26 физических строк (786 знаков) — в ту
        самую стену, которую пункт 6 задания запрещал. Поэтому потолок мерится ЗНАКАМИ
        готовой строки, а число взято у самой сводки: медиана её собственного пункта
        130 знаков, четыре таких = 520, известный и объяснённый перебор → 640.
        """
        _rows, grown = self._grown()
        body = sum(len(cd.line_text(rd)) for rd in grown)
        self.assertLessEqual(body, cd.FATE_CHARS,
                             "прирост раздела снова растёт стеной: %d знаков" % body)
        self.assertLess(cd.FATE_CHARS, 786,
                        "потолок не ниже той стены, ради которой заведён")
        for rd in grown:
            self.assertLess(len(cd.line_text(rd)), 260,
                            "одна строка прироста тянет на экран целиком")

    def test_the_shortening_did_not_cut_a_single_lock(self):
        """Резалась вода, а не замки: определение, третий исход и паспорта на месте."""
        _rows, grown = self._grown()
        body = " ".join(rd["words"] for rd in grown)
        self.assertIn("наличие заявки пользой НЕ считается", body)
        self.assertIn("правило прибора не имеет", body)
        self.assertIn("закрыто отбором %s" % cd.FATE_UNKNOWN, body,
                      "третий исход ступени B ужат в ноль вместо «неизвестно»")
        self.assertIn(self.DAY, body)
        for rd in grown:
            self.assertTrue(rd["addr"] and rd["age"], "паспорт числа срезан ради краткости")

    def test_every_new_number_carries_its_own_source_and_age(self):
        """Закон 1: у каждого числа свой адрес и свой возраст — общего нет."""
        rows = run.section_external(
            [{"channel": "codex", "send_date": self.DAY, "outcome": "answered", "reason": "",
              "answered": True, "rel": "docs/review_inbox/a.md"}],
            self.records(), "", self.DAY, NOW, intake=self.intake(), i_at=NOW - 10,
            recon=self.recon(), r_at=NOW - 20, queue=self.queue(), q_at=NOW - 30)
        addrs = [rd["addr"] for rd in rows]
        self.assertEqual(len(set(addrs)), len(addrs), "два числа делят один адрес источника")
        for rd in rows:
            self.assertTrue(rd["addr"], "число без адреса источника — мнение, а не факт")
            self.assertTrue(rd["age"])

    def test_the_benefit_line_ages_by_the_older_of_its_two_sources(self):
        """Число живо настолько, насколько жив слабейший из его источников."""
        self.assertEqual(run._oldest(NOW - 10, NOW - 900), NOW - 900)
        self.assertIsNone(run._oldest(NOW, None), "нет метки хоть у одного → возраста нет")
        self.assertIn("+", cd.source("benefit")["addr"],
                      "источник пользы обязан назвать ОБА файла, а не главный")

    def test_the_owner_refusal_is_the_row_outcome_not_a_second_marker(self):
        """«Отклонено» считает слепок очереди; второй экземпляр маркера разошёлся бы."""
        with io.open(os.path.join(HERE, "contour_digest.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("отклонено Филиппом", src,
                         "литерал маркера отказа заведён вторым экземпляром")
        got = self.fate(queue=_snapshot({}, dict([_closed(101, NOW, "rejected", "з"),
                                                  _closed(102, NOW, "failed", "з")])))
        self.assertEqual(got["rejected"], 1, "«упало» посчитано отказом владельца")

    def test_route_words_are_borrowed_from_the_owner_module(self):
        """Разведка и заявка владельцу — РАЗНЫЕ исходы, и слова у них не свои."""
        keys = self.keys()
        recon = {"placed": {recon_auto.key_of(recon_auto.SRC_CLAIM, keys[0]):
                            {"way": recon_auto.ROUTE_OWNER}}, "held": {}}
        got = self.fate(recon=recon)
        self.assertEqual(got["recon"], 0)
        self.assertEqual(got["owner_ask"], 1, "маршрут владельца посчитан разведкой")

    def test_no_foreign_text_reaches_the_fate_lines(self):
        """Тот же закон, что у счёта: дайджест, а не поток."""
        got = self.fate()
        for words in (cd.fate_words_intake(got), cd.fate_words_recon(got),
                      cd.fate_words_queue(got), cd.benefit_words(got)):
            self.assertNotIn("СЕКРЕТ", words, "чужой текст доехал до строки судьбы")
            for quote in self.QUOTES:
                self.assertNotIn(quote, words)
        self.assertIn(self.DAY, cd.fate_words_intake(got), "день обязан быть НАЗВАН")
        self.assertIn(self.DAY, cd.benefit_words(got), "день обязан быть НАЗВАН")

    def test_the_finding_key_is_counted_by_stage_b_not_by_our_own_formula(self):
        """Второй способ считать ключ промахивался бы мимо реестра молча."""
        rec = self.records()[0]
        self.assertEqual(cd.finding_keys([rec], self.DAY), [review_intake.claim_key(rec)])
        self.assertEqual(cd.finding_keys([rec], "2026-09-02"), [],
                         "чужой день попал в ключи суток")

    def test_the_anchor_may_move_and_the_finding_is_still_found(self):
        """Ключ заявки переезжает с приходом второго канала — узнаём по НАБОРУ."""
        keys = self.keys()
        intake = {"claims": {"чужой-якорь": {"keys": [keys[0]], "queue_id": 101}}}
        got = self.fate(intake=intake, recon={"placed": {}, "held": {}})
        self.assertEqual(got["claimed"], 1, "заявка потеряна из-за переехавшего якоря")
        self.assertEqual(got["worked"], 1)

    def test_new_sources_have_their_own_limits_with_named_reasons(self):
        """Общего предела у реестров нет: ритмы разные, и это названо."""
        for name in ("intake", "recon", "benefit"):
            paper = cd.source(name)
            self.assertTrue(paper["addr"])
            self.assertIsNotNone(paper["limit"], "предел не назван — стареть будет молча")
            self.assertTrue(paper["why"], "предел без причины — это цифра из головы")
        self.assertNotEqual(cd.limit_of("intake"), cd.limit_of("recon"))
        self.assertEqual(cd.limit_of("benefit"), min(cd.limit_of("recon"), cd.limit_of("queue")),
                         "предел пары обязан браться у более быстрого источника")

    def test_a_dead_tray_keeps_the_section_to_one_honest_line(self):
        """Записей нет → судьбу считать не от чего, и четыре нуля были бы враньём."""
        rows = run.section_external(None, None, "лоток не найден", self.DAY, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], cd.UNKNOWN)

    def test_the_live_repo_shows_the_fate_under_the_external_topic(self):
        """Живое дерево: строки судьбы стоя́т ИМЕННО в разделе внешних ответов."""
        rep = run.build(root=HERE)
        rows = rep["readings"]["external"]
        self.assertGreaterEqual(len(rows), 1)
        if len(rows) > 1:
            body = " ".join(rd["words"] for rd in rows)
            self.assertIn("судьба находок", body)
            self.assertIn("ПОЛЬЗА", body)


def _stale_corpus(tmp):
    """Слепок очереди с ТРЕМЯ чистыми цепочками + реестр судьи, их доказавший.

    → метка замера слепка (его `mtime`), от которой тесты отсчитывают возраст.
    Корпус выбран так, чтобы при живом счёте серия была ЯВНО НЕ НУЛЕВОЙ: иначе
    «ноль вместо неизвестности» и «честный ноль» неотличимы, и тест зеленел бы на
    сломанном показе.
    """
    path = os.path.join(tmp, cd.source("queue")["addr"])
    closed = dict(_closed(n, NOW - 5000 + n, "done", "ЦЕЛЬ: правка %d" % n) for n in (1, 2, 3))
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(_snapshot(closed_rows=closed), fh)
    ledger = os.path.join(tmp, done_judge_pc.LEDGER.replace("/", os.sep))
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    rows = {str(n): {cd.F_PROVED: True, cd.F_ADDRESSED: True, "seq": n} for n in (1, 2, 3)}
    with io.open(ledger, "w", encoding="utf-8") as fh:
        json.dump({"schema": done_judge_pc.LEDGER_SCHEMA, "rows": rows}, fh)
    return os.path.getmtime(path)


def _no_git(*_a, **_k):
    raise OSError("git в проверке не зовём")


def _window_series(text):
    """Строка витрины, несущая серию. Нет такой — падение, а не пустая строка."""
    for line in text.splitlines():
        if "серия" in line:
            return line.strip()
    raise AssertionError("в витрине не осталось строки серии вовсе")


class TestStaleSourceIsSpokenNotZeroed(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания 31-a: величина по протухшему источнику — СЛОВО, а не ноль.

    ОБА ПОКАЗА ОДНОЙ ВЕЛИЧИНЫ ПРОВЕРЯЮТСЯ ОДНИМ КОРПУСОМ И В ОДНОМ КЛАССЕ, и это
    не удобство сборки: формула серии у журнальной сводки и у витрины ОДНА
    (:func:`contour_digest.series`, зовут её обе одной строкой), а разошлись они
    ПРАВИЛОМ ПОКАЗА НЕИЗВЕСТНОСТИ. Проверяй их порознь — разойдутся снова и снова
    молча.

    Вход один на все проверки: слепок очереди СТАРШЕ своего предела
    (`cd.limit_of("queue")`), три чистых цепочки, судья их доказал. На коде до
    правки 11.09.2026 этот вход давал ДВА неверных ответа сразу — «серия 3 из 30»
    в витрине (число по слепку ЛЮБОГО возраста) и «серия 0/30» в журнале (ноль на
    месте уже погашенного числа). Ровно эти две строки Штаб 11.09 прочитал,
    сложил в вывод и доложил владельцу неправдой; цена класса заплачена один раз,
    и второй раз её платить нечем.
    """

    def test_the_journal_index_says_question_mark_where_the_number_is_damped(self):
        """Журнал: погашенная серия печатается знаком вопроса, а не нулём.

        ДО правки здесь стояло «серия 0/30 · доказал судья 0 … НЕИЗВЕСТНО 0 из 0»
        — четыре нуля, каждый читается как ИЗМЕРЕННЫЙ факт.
        """
        with tempfile.TemporaryDirectory() as tmp:
            at = _stale_corpus(tmp)
            future = at + cd.limit_of("queue") + 600
            rep = run.build(root=tmp, now=future, since=future - cd.INTERVAL_SEC, runner=_no_git)
            self.assertIsNone(rep["series"], "сборка перестала гасить числа протухшего слепка")
            line = cd.journal_line(rep)
        self.assertNotIn("серия 0/%d" % cd.SERIES_TARGET, line,
                         "погашенное число напечатано нулём — ноль в индексе читается фактом")
        self.assertIn("серия ?/%d" % cd.SERIES_TARGET, line)
        self.assertIn("доказал судья ?", line)
        self.assertIn("НЕИЗВЕСТНО ? из ?", line,
                      "«0 из 0» утверждает, что неизвестных не было, а спросить было нечем")

    def test_the_shop_window_refuses_to_count_on_a_stale_snapshot(self):
        """Витрина: слепок старше предела считать НЕЛЬЗЯ — ни в фактах, ни на печати.

        ДО правки ветка в `collect` была одна (`if snapshot is not None`), и число
        ехало наружу по слепку любого возраста, хотя текст самой витрины обещал
        «не прочитан ИЛИ СТАРШЕ ПРЕДЕЛА».
        """
        with tempfile.TemporaryDirectory() as tmp:
            at = _stale_corpus(tmp)
            future = at + cd.limit_of("queue") + 600
            facts = vpr.collect(root=tmp, now=future, runner=_no_git,
                                shtab=vp.parse_shtab("", ok=False, why="узла нет"))
            self.assertIsNone(facts["series"], "серия посчитана по слепку старше предела")
            line = _window_series(vp.render(facts, "?"))
        self.assertIn(vp.UNKNOWN, line)
        self.assertIn("старше предела", line, "причина названа у́же правды: слепок ПРОЧИТАН, но стар")
        self.assertNotIn("3 из %d подряд" % cd.SERIES_TARGET, line,
                         "число по протухшему слепку подано фактом")

    def test_a_fresh_snapshot_still_carries_the_real_number_in_both_shows(self):
        """ЗАМОК ОБРАТНОЙ СТОРОНЫ: живое число правкой не убито.

        Показ, который молчит ВСЕГДА, зелен по той же причине, по какой был зелен
        сломанный, — поэтому свежий слепок обязан дать РОВНО те же три цепочки в
        обоих показах.
        """
        with tempfile.TemporaryDirectory() as tmp:
            at = _stale_corpus(tmp)
            fresh = at + 10.0
            rep = run.build(root=tmp, now=fresh, since=fresh - cd.INTERVAL_SEC, runner=_no_git)
            facts = vpr.collect(root=tmp, now=fresh, runner=_no_git,
                                shtab=vp.parse_shtab("", ok=False, why="узла нет"))
            line = cd.journal_line(rep)
            window = _window_series(vp.render(facts, "?"))
        self.assertEqual(rep["series"]["streak"], 3)
        self.assertEqual(facts["series"]["streak"], 3)
        self.assertIn("серия 3/%d" % cd.SERIES_TARGET, line)
        self.assertIn("3 из %d подряд" % cd.SERIES_TARGET, window)
        self.assertNotIn("?", line.split("· задач от Штаба")[0].split("серия")[1],
                         "свежий слепок напечатан незнанием — показ замолчал вместо счёта")

    def test_both_shows_take_the_limit_from_the_one_place_that_names_it(self):
        """Второй копии предела нет: граница показа стои́т РОВНО на `cd.limit_of("queue")`.

        Проверяется поведением, а не грепом по числу: собственный предел витрины
        выдал бы себя тем, что граница уехала бы с чужой. Десять секунд по обе
        стороны от предела — и ответ обязан смениться.
        """
        limit = cd.limit_of("queue")
        self.assertEqual(limit, 3600, "предел слепка переехал — проверка обязана поехать за ним")
        with tempfile.TemporaryDirectory() as tmp:
            at = _stale_corpus(tmp)
            before = vpr.collect(root=tmp, now=at + limit - 10, runner=_no_git,
                                 shtab=vp.parse_shtab("", ok=False, why="узла нет"))
            after = vpr.collect(root=tmp, now=at + limit + 10, runner=_no_git,
                                shtab=vp.parse_shtab("", ok=False, why="узла нет"))
        self.assertIsNotNone(before["series"], "витрина замолчала РАНЬШЕ предела — предел свой")
        self.assertIsNone(after["series"], "витрина считает ПОСЛЕ предела — предел свой")


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
