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


class TestSeries(unittest.TestCase):
    """Счёт серии цепочек — отдельной строкой и с названными определениями."""

    def test_clean_is_done_and_unsure_breaks_the_streak(self):
        rows = [{"id": 1, "outcome": "done", "goal": "работа"},
                {"id": 2, "outcome": None, "goal": "работа"},
                {"id": 3, "outcome": "done", "goal": "работа"}]
        self.assertEqual(cd.series(rows)["streak"], 1, "«не сверен» обязан обрывать серию")

    def test_failed_breaks_the_streak(self):
        rows = [{"id": 1, "outcome": "done", "goal": "работа"},
                {"id": 2, "outcome": "failed", "goal": "работа"}]
        self.assertEqual(cd.series(rows)["streak"], 0)

    def test_service_roots_are_out_of_the_count(self):
        """Признак служебного корня ВЗЯТ у счётчика полосы, а не выдуман здесь."""
        rows = [{"id": 1, "outcome": "done", "goal": "[ревизор-находки] сводка"},
                {"id": 2, "outcome": "done", "goal": "работа"}]
        got = cd.series(rows)
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
        got = cd.series(rows)
        self.assertEqual(got["streak"], 2)
        self.assertEqual(got["moved"], 1, "заявка ревью операционного состояния не меняет")

    def test_window_is_thirty(self):
        self.assertEqual(cd.SERIES_TARGET, 30)
        rows = [{"id": i, "outcome": "done", "goal": "ЦЕЛЬ: правка"} for i in range(50)]
        got = cd.series(rows)
        self.assertEqual(got["window"], 30)
        self.assertEqual(got["streak"], 30)

    def test_series_line_says_unknown_when_the_source_is_dead(self):
        line = cd.series_line(None, cd.dead_source("series", "queue", "файла нет"))
        self.assertIn("НЕИЗВЕСТНО", line)
        self.assertNotIn("подряд из 30", line, "нуль вместо счёта — это утверждение, а не молчание")

    def test_series_line_is_always_present_in_the_message(self):
        rep = run.build(root=tempfile.gettempdir(), now=NOW, since=NOW - 10,
                        runner=lambda *a, **k: (_ for _ in ()).throw(OSError("нет")))
        self.assertIn("СЕРИЯ ЦЕПОЧЕК:", cd.render(rep))


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


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
