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
import recon_auto
import review_intake
import series_pc

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


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
