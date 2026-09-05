# -*- coding: utf-8 -*-
"""
test_lesson_regress.py — свой набор регрессии урока (`lesson_regress` + шов в `trainer`).

Прогонять с TESTING=1 (замок живого прогона корпуса — тот же, что у живой заявки в очередь):
    TESTING=1 python -m unittest test_lesson_regress -v

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА ЗАДАНИЯ живут в классах с говорящими именами и помечены в докстроках:
  • :class:`NegRuleBreaksCase` — правило, ломающее известный кейс, обязано дать КРАСНЫЙ исход,
    а не тишину;
  • :class:`NegNetworkDown`   — недоступная сеть даёт НЕИЗВЕСТНО, а не «всё хорошо»;
  • :class:`NegLessonSurvives`— запись урока при УПАВШЕЙ регрессии всё равно происходит.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("TESTING", "1")

import lesson_regress as lr


# ─────────────────────────────────────── стенд ───────────────────────────────────────────────

def chk(name, ok, expected="ждали X", fact="факт Y"):
    return {"name": name, "ok": ok, "expected": expected, "fact": fact}


def case_res(cid, ok, red=(), green=("цена названа", "язык ответа"), unknown=None, skipped=()):
    checks = [chk(n, True) for n in green] + [chk(n, False) for n in red]
    checks += [dict(chk(n, False), skipped=True) for n in skipped]
    return {"id": cid, "name": "кейс %s" % cid, "ok": bool(ok) and not red,
            "unknown": unknown, "checks": checks, "draft": "текст", "note": ""}


class StubRunner(object):
    """Стенд вместо `trainer_run`: тот же интерфейс (bind_head/load_cases/placeholders/run_corpus/
    verify_head), но без головы, Bridge и сети. `plan` — очередь ответов run_corpus по заходам."""

    def __init__(self, plan, commit="abc1234" * 5 + "abcde", sha="deadbeefdeadbeef", cases=None):
        self.plan = list(plan)
        self.calls = []                       # какие кейсы просили на каждом заходе
        self._commit = commit
        self._sha = sha
        self._cases = [{"id": "1"}, {"id": "2"}, {"id": "3"}] if cases is None else list(cases)
        self.verified = 0

    def bind_head(self):
        return {"commit": self._commit, "clean": True, "dirty_paths": [], "head_moved": "",
                "head_after": self._commit, "tree_known": True}

    def verify_head(self, bind):
        self.verified += 1
        return bind

    def load_cases(self, path=None):
        return list(self._cases), self._sha

    def placeholders(self, today=None):
        return {}

    def run_corpus(self, cases, runs=2, ph=None, log=print):
        self.calls.append([str(c.get("id")) for c in cases])
        if not self.plan:
            raise AssertionError("run_corpus позвали больше раз, чем заготовлено ответов")
        step = self.plan.pop(0)
        if isinstance(step, Exception):
            raise step
        results = step.get("results") or []
        unknown = step.get("unknown") or []
        ok = sum(1 for r in results for c in r["checks"] if c["ok"] and not c.get("skipped"))
        allc = sum(1 for r in results for c in r["checks"] if not c.get("skipped"))
        failed = ["%s/1 %s" % (r["id"], c["name"])
                  for r in results for c in r["checks"] if not c["ok"] and not c.get("skipped")]
        passed = sum(1 for r in results if r["ok"])
        return results, passed, ok, allc, failed, unknown


class Tmp(unittest.TestCase):
    """Временное — только в своей папке; боевые файлы состояния не трогаются ни одним тестом."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lesson_regress_test_")
        self.state = os.path.join(self.dir, "state.json")
        self.lock = os.path.join(self.dir, "state.lock")
        self.said = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def say(self, line):
        self.said.append(line)
        return True


# ─────────────────────────────────────── разбор прогона ──────────────────────────────────────

class Rows(unittest.TestCase):

    def test_green_and_red_split(self):
        rows = lr._rows([case_res("1", True), case_res("2", False, red=["доставка = цена зоны"])])
        self.assertTrue(rows["1"]["ok"])
        self.assertFalse(rows["2"]["ok"])
        self.assertEqual([c["name"] for c in rows["2"]["red"]], ["доставка = цена зоны"])
        self.assertIn("цена названа", rows["1"]["checks"])

    def test_skipped_check_not_counted(self):
        """Снятый с причиной чек виден в отчёте, но в счёт и в красное не идёт."""
        rows = lr._rows([case_res("2", True, skipped=["депозит без противоречий"])])
        self.assertTrue(rows["2"]["ok"])
        self.assertEqual(rows["2"]["red"], [])
        self.assertNotIn("депозит без противоречий", rows["2"]["checks"])

    def test_red_in_any_run_is_red(self):
        rows = lr._rows([case_res("1", True), case_res("1", False, red=["язык ответа"])])
        self.assertFalse(rows["1"]["ok"])
        self.assertEqual([c["name"] for c in rows["1"]["red"]], ["язык ответа"])


# ─────────────────────────────────────── замер ───────────────────────────────────────────────

class Measure(Tmp):

    def test_all_green_one_screening_pass(self):
        run = StubRunner([{"results": [case_res("1", True), case_res("2", True)]}],
                         cases=[{"id": "1"}, {"id": "2"}])
        rec = lr.measure(runner=run)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["cases_ok"], 2)
        self.assertEqual(rec["cases_seen"], 2)
        self.assertEqual(len(run.calls), 1, "зелёный корпус подтверждать нечего — второй прогон лишний")
        self.assertEqual(run.verified, 1)

    def test_confirm_walks_only_red_cases(self):
        """Цена подтверждения пропорциональна беде: второй прогон идёт ТОЛЬКО по красным."""
        run = StubRunner([
            {"results": [case_res("1", True), case_res("2", False, red=["доставка = цена зоны"]),
                         case_res("3", True)]},
            {"results": [case_res("2", False, red=["доставка = цена зоны"])]},
        ])
        rec = lr.measure(runner=run)
        self.assertEqual(run.calls[0], ["1", "2", "3"])
        self.assertEqual(run.calls[1], ["2"], "подтверждали не только красный кейс")
        self.assertTrue(rec["ok"])
        self.assertFalse(rec["cases"]["2"]["ok"])

    def test_flake_confirmed_green_is_green(self):
        run = StubRunner([
            {"results": [case_res("1", True), case_res("2", False, red=["язык ответа"])]},
            {"results": [case_res("2", True)]},
        ], cases=[{"id": "1"}, {"id": "2"}])
        rec = lr.measure(runner=run)
        self.assertTrue(rec["cases"]["2"]["ok"])
        self.assertEqual(rec["flaked"], ["2"])
        self.assertEqual(rec["cases_ok"], 2)

    def test_unknown_from_corpus_is_not_a_measurement(self):
        run = StubRunner([{"results": [case_res("1", True)],
                           "unknown": ["1/1 голова промолчала: пустых ответов 1 из 1"]}])
        rec = lr.measure(runner=run)
        self.assertFalse(rec["ok"])
        self.assertTrue(rec["unknown"])
        self.assertIn("голова промолчала", rec["why"])

    def test_unknown_on_confirm_pass_is_unknown_too(self):
        run = StubRunner([
            {"results": [case_res("1", False, red=["цена названа"])]},
            {"results": [], "unknown": ["1/1 голову не спросили ни разу"]},
        ], cases=[{"id": "1"}])
        rec = lr.measure(runner=run)
        self.assertFalse(rec["ok"])
        self.assertIn("подтверждение красных", rec["why"])

    def test_crash_is_named_not_raised(self):
        run = StubRunner([RuntimeError("Bridge молчит")])
        rec = lr.measure(runner=run)
        self.assertFalse(rec["ok"])
        self.assertIn("Bridge молчит", rec["why"])
        self.assertEqual(rec["cases"], {})

    def test_only_filter_narrows_corpus(self):
        run = StubRunner([{"results": [case_res("1", True)]}])
        lr.measure(runner=run, only=["1"])
        self.assertEqual(run.calls[0], ["1"])

    def test_empty_corpus_is_not_a_measurement(self):
        run = StubRunner([], cases=[])
        rec = lr.measure(runner=run)
        self.assertFalse(rec["ok"])
        self.assertIn("корпус пуст", rec["why"])


# ─────────────────────────────────────── эталон ──────────────────────────────────────────────

class Baseline(Tmp):

    def test_own_state_wins_over_gate_registry(self):
        lr.write_state({"last": {"when": "2026-09-05 10:00:00", "cases": {"1": {"ok": True}}}},
                       self.state)
        base, frm = lr.baseline(path=self.state, gate={"last": {"cases_total": 16, "failed": []}})
        self.assertIn("свой эталон", frm)
        self.assertTrue(base["cases"]["1"]["ok"])

    def test_seeded_from_gate_registry_read_only(self):
        gate = {"last": {"cases_total": 16, "cases": 15, "checks_passed": 140, "checks_total": 141,
                         "corpus_sha": "s1", "commit": "c" * 40, "when": "2026-09-05 01:00:00",
                         "result": "red", "failed": ["3/1 доставка = цена зоны"]}}
        base, frm = lr.baseline(path=self.state, corpus_sha="s1", gate=gate)
        self.assertIn("реестра ворот", frm)
        self.assertFalse(base["cases"]["3"]["ok"])
        self.assertTrue(base["green_implicit"], "не названный красным кейс записи ворот был зелёным")
        self.assertTrue(lr._was_green(base, "7"))
        self.assertFalse(lr._was_green(base, "3"))

    def test_gate_record_on_other_corpus_is_not_a_baseline(self):
        gate = {"last": {"cases_total": 12, "corpus_sha": "старый", "failed": []}}
        base, frm = lr.baseline(path=self.state, corpus_sha="новый", gate=gate)
        self.assertIsNone(base)
        self.assertIn("другом корпусе", frm)

    def test_no_baseline_at_all(self):
        base, frm = lr.baseline(path=self.state, gate={})
        self.assertIsNone(base)
        self.assertEqual(frm, "эталона нет")


# ─────────────────────────────────────── сравнение ───────────────────────────────────────────

class Compare(unittest.TestCase):

    def base(self, cases):
        return {"cases": cases, "cases_total": len(cases), "when": "вчера"}

    def test_green_to_red_is_regression(self):
        now = {"ok": True, "cases_seen": 2, "cases": {
            "1": {"ok": True, "checks": ["цена названа"], "red": []},
            "3": {"ok": False, "checks": [], "red": [chk("доставка = цена зоны", False,
                                                         "590 ฿ Раваи", "цена зоны не названа")]}}}
        got = lr.compare(now, self.base({"1": {"ok": True, "checks": ["цена названа"]},
                                         "3": {"ok": True, "checks": ["доставка = цена зоны"]}}))
        self.assertEqual(got["verdict"], "broken")
        self.assertEqual([b["id"] for b in got["broken"]], ["3"])
        self.assertEqual(got["broken"][0]["checks"][0]["name"], "доставка = цена зоны")
        self.assertEqual(got["broken"][0]["checks"][0]["expected"], "590 ฿ Раваи")

    def test_red_before_and_red_now_is_not_regression(self):
        now = {"ok": True, "cases_seen": 1,
               "cases": {"3": {"ok": False, "checks": [], "red": [chk("доставка", False)]}}}
        got = lr.compare(now, self.base({"3": {"ok": False, "checks": []}}))
        self.assertEqual(got["verdict"], "ok")
        self.assertEqual(got["broken"], [])

    def test_red_to_green_is_reported_as_fixed(self):
        now = {"ok": True, "cases_seen": 1,
               "cases": {"3": {"ok": True, "checks": ["доставка"], "red": []}}}
        got = lr.compare(now, self.base({"3": {"ok": False, "checks": []}}))
        self.assertEqual(got["verdict"], "ok")
        self.assertEqual(got["fixed"], ["3"])

    def test_stopped_checks_first_new_checks_kept(self):
        """Первым идёт чек, который БЫЛ зелёным (он перестал), но новый красный не теряется."""
        now = {"ok": True, "cases_seen": 1, "cases": {"5": {"ok": False, "checks": [], "red": [
            chk("новый чек", False), chk("цена названа", False)]}}}
        got = lr.compare(now, self.base({"5": {"ok": True, "checks": ["цена названа"]}}))
        names = [c["name"] for c in got["broken"][0]["checks"]]
        self.assertEqual(names, ["цена названа", "новый чек"])

    def test_no_baseline_is_base_verdict(self):
        now = {"ok": True, "cases_seen": 1, "cases": {"1": {"ok": True, "checks": [], "red": []}}}
        self.assertEqual(lr.compare(now, None)["verdict"], "base")

    def test_no_measurement_is_unknown(self):
        got = lr.compare({"ok": False, "why": "голова промолчала"}, self.base({"1": {"ok": True}}))
        self.assertEqual(got["verdict"], "unknown")
        self.assertIn("голова промолчала", got["why"])


# ─────────────────────────────────────── строка исхода ───────────────────────────────────────

class Line(unittest.TestCase):

    REC_OK = {"ok": True, "cases_ok": 16, "cases_seen": 16, "checks_ok": 141, "checks_all": 141,
              "sec": 430.0, "flaked": [], "head_moved": ""}

    def test_voice_green(self):
        line = lr.outcome_line({"verdict": "ok", "broken": [], "fixed": []}, self.REC_OK,
                               [{"n": 12}])
        self.assertIn("ничего не сломалось", line)
        self.assertIn("16/16", line)
        self.assertIn("урок #12".replace("урок", "Урок"), line)

    def test_voice_red_names_case_and_the_check_itself(self):
        cmp_res = {"verdict": "broken", "fixed": [], "broken": [
            {"id": "3", "checks": [chk("доставка = цена зоны", False, "590 ฿ Раваи",
                                       "цена зоны не названа")]}]}
        line = lr.outcome_line(cmp_res, self.REC_OK, [{"n": 12}])
        self.assertIn("СЛОМАЛОСЬ", line)
        self.assertIn("#3", line)
        self.assertIn("доставка = цена зоны", line)
        self.assertIn("590 ฿ Раваи", line)
        self.assertIn("цена зоны не названа", line)
        self.assertIn("отмени урок 12", line)

    def test_voice_unknown_is_not_all_fine(self):
        line = lr.outcome_line({"verdict": "unknown", "why": "Bridge недоступен"}, {}, [{"n": 12}])
        self.assertIn("НЕИЗВЕСТНО", line)
        self.assertIn("Bridge недоступен", line)
        self.assertNotIn("ничего не сломалось", line)

    def test_voice_base_does_not_claim_nothing_broke(self):
        line = lr.outcome_line({"verdict": "base", "broken": [], "fixed": []}, self.REC_OK, [])
        self.assertIn("сравнивать НЕ С ЧЕМ", line)
        self.assertNotIn("ничего не сломалось", line)

    def test_one_short_line(self):
        cmp_res = {"verdict": "broken", "fixed": [], "broken": [
            {"id": str(i), "checks": [chk("чек %d" % i, False, "ж" * 200, "ф" * 200)]}
            for i in range(1, 9)]}
        line = lr.outcome_line(cmp_res, self.REC_OK, [{"n": 1}])
        self.assertNotIn("\n", line)
        self.assertLessEqual(len(line), lr.LINE_MAX)

    def test_glue_names_all_lessons_and_admits_ignorance(self):
        cmp_res = {"verdict": "broken", "fixed": [], "broken": [
            {"id": "3", "checks": [chk("цена названа", False, "3520", "нет числа")]}]}
        line = lr.outcome_line(cmp_res, self.REC_OK, [{"n": 12}, {"n": 13}, {"n": 14}])
        for n in ("12", "13", "14"):
            self.assertIn(n, line)
        self.assertIn("прибор не знает", line)

    def test_head_moved_is_named_on_red(self):
        rec = dict(self.REC_OK, head_moved="abc1234→def5678")
        cmp_res = {"verdict": "broken", "fixed": [], "broken": [
            {"id": "3", "checks": [chk("цена названа", False, "3520", "нет числа")]}]}
        self.assertIn("HEAD уехал", lr.outcome_line(cmp_res, rec, [{"n": 12}]))

    def test_flake_named_on_green(self):
        rec = dict(self.REC_OK, flaked=["5"])
        line = lr.outcome_line({"verdict": "ok", "broken": [], "fixed": []}, rec, [{"n": 12}])
        self.assertIn("Мигнули", line)


# ─────────────────────────────────────── одиночка и склейка ──────────────────────────────────

class Single(Tmp):

    def test_second_taker_is_refused(self):
        took, _why = lr.acquire(self.lock, now=1000.0)
        self.assertTrue(took)
        took2, why2 = lr.acquire(self.lock, now=1001.0)
        self.assertFalse(took2)
        self.assertIn("склеится", why2)

    def test_stale_lock_is_taken_over(self):
        lr.acquire(self.lock, now=1000.0)
        took, why = lr.acquire(self.lock, now=1000.0 + 99999, stale=60)
        self.assertTrue(took)
        self.assertIn("стухший лок отобран", why)

    def test_release_frees_without_deleting_file(self):
        lr.acquire(self.lock, now=1000.0)
        lr.release(self.lock)
        self.assertTrue(os.path.exists(self.lock), "уборка за собой запрещена — файл остаётся")
        self.assertTrue(lr.acquire(self.lock, now=1002.0)[0])

    def test_pending_accumulates_and_is_taken_once(self):
        lr.note_pending(12, "правило A", path=self.state, now=1.0)
        lr.note_pending(13, "правило B", path=self.state, now=2.0)
        got = lr.take_pending(self.state)
        self.assertEqual([g["n"] for g in got], [12, 13])
        self.assertEqual(lr.take_pending(self.state), [])

    def test_glue_reruns_when_lesson_arrives_mid_run(self):
        """Урок, приехавший ВО ВРЕМЯ замера, вызывает пересчёт по итоговой книге, а не второй пуш."""
        state = self.state
        seq = {"n": 0}

        def fake_measure(**kw):
            seq["n"] += 1
            if seq["n"] == 1:
                lr.note_pending(13, "правило B", path=state, now=5.0)   # приехал во время замера
            return {"ok": True, "cases": {"1": {"ok": True, "checks": ["c"], "red": []}},
                    "cases_seen": 1, "cases_ok": 1, "checks_ok": 1, "checks_all": 1,
                    "sec": 1.0, "corpus_sha": "s1", "flaked": [], "head_moved": ""}

        lr.note_pending(12, "правило A", path=state, now=1.0)
        real, lr.measure = lr.measure, fake_measure
        try:
            got = lr.after_lesson(path=state, lock=self.lock, say_fn=self.say)
        finally:
            lr.measure = real
        self.assertEqual(seq["n"], 2, "склейка обязана пересчитать книгу после приехавшего урока")
        self.assertEqual(got["glued"], 1)
        self.assertEqual(len(self.said), 1, "склейка — ОДНА строка, а не по строке на урок")
        self.assertIn("12", self.said[0])
        self.assertIn("13", self.said[0])

    def test_glue_is_bounded(self):
        state = self.state
        seq = {"n": 0}

        def fake_measure(**kw):
            seq["n"] += 1
            lr.note_pending(seq["n"], "правило", path=state, now=float(seq["n"]))
            return {"ok": True, "cases": {}, "cases_seen": 0, "cases_ok": 0, "checks_ok": 0,
                    "checks_all": 0, "sec": 1.0, "corpus_sha": "s1", "flaked": [], "head_moved": ""}

        real, lr.measure = lr.measure, fake_measure
        try:
            got = lr.after_lesson(path=state, lock=self.lock, say_fn=self.say, max_glued=2)
        finally:
            lr.measure = real
        self.assertEqual(got["glued"], 2)
        self.assertEqual(seq["n"], 3, "склейка бесконечной быть не может")
        self.assertIn("быстрее корпуса", self.said[0],
                      "исчерпанная склейка обязана СКАЗАТЬ, что мерилась не последняя книга")

    def test_busy_lock_means_no_second_run_and_no_second_line(self):
        lr.acquire(self.lock)                    # СВЕЖИЙ лок: подставное время сделало бы его
        run = StubRunner([])                     # стухшим, замер бы его отобрал и полез в живой корпус
        got = lr.after_lesson(runner=run, path=self.state, lock=self.lock, say_fn=self.say)
        self.assertFalse(got["ran"])
        self.assertEqual(self.said, [], "второй строки владельцу склейка не шлёт")


# ─────────────────────────────────────── хранение ────────────────────────────────────────────

class Save(Tmp):

    def test_failed_measurement_does_not_erase_baseline(self):
        lr.write_state({"last": {"when": "вчера", "cases": {"1": {"ok": True}}, "cases_ok": 16}},
                       self.state)
        lr._save({"ok": False, "why": "голова молчит"}, {"verdict": "unknown", "why": "молчит"},
                 [{"n": 12}], path=self.state)
        st = lr.read_state(self.state)
        self.assertEqual(st["last"]["when"], "вчера", "несостоявшийся замер стёр эталон")
        self.assertEqual(st["history"][-1]["verdict"], "unknown")

    def test_good_measurement_becomes_the_new_baseline(self):
        rec = {"ok": True, "cases": {"1": {"ok": True, "checks": ["c"], "red": []}},
               "cases_seen": 1, "cases_ok": 1, "checks_ok": 1, "checks_all": 1, "sec": 3.0,
               "corpus_sha": "s1", "commit": "c" * 40}
        lr._save(rec, {"verdict": "ok", "broken": []}, [{"n": 12, "rule": "A"}], path=self.state)
        st = lr.read_state(self.state)
        self.assertEqual(st["last"]["cases_ok"], 1)
        self.assertEqual(st["last"]["lessons"][0]["n"], 12)

    def test_history_is_bounded(self):
        for i in range(lr.HISTORY_KEEP + 5):
            lr._save({"ok": False}, {"verdict": "unknown", "why": str(i)}, [], path=self.state)
        self.assertEqual(len(lr.read_state(self.state)["history"]), lr.HISTORY_KEEP)

    def test_broken_state_file_is_not_a_crash(self):
        with io.open(self.state, "w", encoding="utf-8") as f:
            f.write("{это не json")
        self.assertEqual(lr.read_state(self.state), {})


# ─────────────────────────────────────── запуск из урока ─────────────────────────────────────

class FakePopen(object):
    calls = []

    def __init__(self, argv, **kw):
        FakePopen.calls.append((list(argv), kw))
        self.waited = 0

    def wait(self, timeout=None):
        self.waited += 1
        raise AssertionError("нажатие владельца не имеет права ждать прогон корпуса")

    def communicate(self, *a, **kw):
        raise AssertionError("нажатие владельца не имеет права читать потоки прогона")


class Spawn(Tmp):

    def setUp(self):
        Tmp.setUp(self)
        FakePopen.calls = []

    def test_testing_lock_blocks_live_run(self):
        got = lr.spawn("правило", n=12, popen=FakePopen, env={"TESTING": "1"}, path=self.state)
        self.assertFalse(got["spawned"])
        self.assertIn("TESTING", got["why"])
        self.assertEqual(FakePopen.calls, [])

    def test_off_switch_kills_the_branch(self):
        got = lr.spawn("правило", n=12, popen=FakePopen, env={lr.OFF_ENV: "1"}, path=self.state)
        self.assertFalse(got["spawned"])
        self.assertIn(lr.OFF_ENV, got["why"])

    def test_detached_and_never_waited(self):
        got = lr.spawn("правило", n=12, popen=FakePopen, env={}, path=self.state, now=7.0)
        self.assertTrue(got["spawned"])
        argv, kw = FakePopen.calls[0]
        self.assertTrue(argv[1].endswith("lesson_regress.py"))
        self.assertIn("--after-lesson", argv)
        self.assertEqual(kw["creationflags"], lr.DETACHED)
        self.assertEqual(kw["stdout"], subprocess.DEVNULL)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual([p["n"] for p in lr.read_state(self.state)["pending"]], [12])

    def test_spawn_never_raises(self):
        def boom(*a, **kw):
            raise OSError("нет venv")
        got = lr.spawn("правило", n=12, popen=boom, env={}, path=self.state)
        self.assertFalse(got["spawned"])
        self.assertIn("нет venv", got["why"])


# ─────────────────────────────────── ГРАНИЦЫ: ворота не трогаем ──────────────────────────────

class Boundaries(unittest.TestCase):

    def test_module_never_writes_the_gate_verdict(self):
        """Регрессия не вердикт: имени `write_verdict` и записи TRAINER_GREEN_FILE в модуле нет."""
        with io.open(lr.__file__.replace(".pyc", ".py"), encoding="utf-8") as f:
            src = f.read()
        body = src.split('"""', 2)[-1]            # шапка про «НЕ пишет вердикт» — не код
        self.assertNotIn("write_verdict", body)
        self.assertNotIn("TRAINER_GREEN_FILE", body.replace(
            "client_contour.TRAINER_GREEN_FILE, encoding", "<чтение эталона>"))

    def test_state_file_lives_under_the_lane_mask(self):
        self.assertTrue(os.path.basename(lr.STATE_FILE).startswith("pc_orchestrator."))
        self.assertTrue(lr.STATE_FILE.endswith(".json"))
        with io.open(os.path.join(lr.REPO, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("pc_orchestrator.*.json", f.read())


# ═══════════════════════ ОТРИЦАТЕЛЬНЫЙ 1: правило сломало кейс ═══════════════════════════════

class NegRuleBreaksCase(Tmp):
    """Правило, ломающее известный кейс, обязано дать КРАСНЫЙ исход, а не тишину."""

    def test_broken_case_produces_a_red_line_not_silence(self):
        lr.write_state({"last": {"when": "вчера", "corpus_sha": "s1", "cases": {
            "1": {"ok": True, "checks": ["цена названа"], "red": []},
            "3": {"ok": True, "checks": ["доставка = цена зоны"], "red": []}}}}, self.state)
        broken = case_res("3", False, red=[])
        broken["checks"] = [chk("доставка = цена зоны", False, "590 ฿ Раваи", "цена зоны не названа")]
        broken["ok"] = False
        run = StubRunner([{"results": [case_res("1", True), broken]},
                          {"results": [broken]}],
                         sha="s1", cases=[{"id": "1"}, {"id": "3"}])
        lr.note_pending(12, "не повторяй модель", path=self.state, now=1.0)
        got = lr.after_lesson(runner=run, path=self.state, lock=self.lock, say_fn=self.say)

        self.assertEqual(got["verdict"], "broken")
        self.assertEqual(len(self.said), 1, "красное сказано РОВНО одной строкой, а не молчанием")
        line = self.said[0]
        self.assertIn("СЛОМАЛОСЬ", line)
        self.assertIn("#3", line)
        self.assertIn("доставка = цена зоны", line)
        self.assertIn("590 ฿ Раваи", line)
        self.assertIn("цена зоны не названа", line)

    def test_regression_does_not_touch_the_rule_book(self):
        """ИЗМЕРЯЕТ И ГОВОРИТ: снятие урока — движение владельца, автоотката нет ни одной веткой."""
        with io.open(lr.__file__.replace(".pyc", ".py"), encoding="utf-8") as f:
            body = f.read().split('"""', 2)[-1]
        for forbidden in ("remove_playbook_rule", "append_playbook_rule", "cancel_lesson",
                          "unmark_source"):
            self.assertNotIn(forbidden, body)


# ═══════════════════════ ОТРИЦАТЕЛЬНЫЙ 2: сети нет ═══════════════════════════════════════════

class NegNetworkDown(Tmp):
    """Недоступная сеть даёт НЕИЗВЕСТНО, а не «всё хорошо»."""

    def test_network_error_is_unknown_not_green(self):
        import urllib.error
        run = StubRunner([urllib.error.URLError("сеть недоступна")])
        lr.write_state({"last": {"when": "вчера", "cases": {"1": {"ok": True, "checks": ["c"]}}}},
                       self.state)
        lr.note_pending(12, "правило", path=self.state, now=1.0)
        got = lr.after_lesson(runner=run, path=self.state, lock=self.lock, say_fn=self.say)

        self.assertEqual(got["verdict"], "unknown")
        self.assertEqual(len(self.said), 1, "третий голос молчанием не заменяется")
        self.assertIn("НЕИЗВЕСТНО", self.said[0])
        self.assertIn("сеть недоступна", self.said[0])
        self.assertNotIn("ничего не сломалось", self.said[0])
        self.assertEqual(lr.read_state(self.state)["last"]["when"], "вчера",
                         "провал сети не имеет права стереть последнее известное число")

    def test_silent_head_is_unknown_not_green(self):
        run = StubRunner([{"results": [case_res("1", True)],
                           "unknown": ["1/1 голова промолчала: пустых ответов 1 из 1"]}],
                         cases=[{"id": "1"}])
        lr.write_state({"last": {"when": "вчера", "cases": {"1": {"ok": True, "checks": ["c"]}}}},
                       self.state)
        got = lr.after_lesson(runner=run, path=self.state, lock=self.lock, say_fn=self.say)
        self.assertEqual(got["verdict"], "unknown")
        self.assertIn("НЕИЗВЕСТНО", self.said[0])

    def test_dispatch_failure_does_not_crash_the_run(self):
        def boom(*a, **kw):
            raise OSError("dispatch_notify не запустился")
        self.assertFalse(lr.say("строка", popen=boom))


# ═══════════════════════ ОТРИЦАТЕЛЬНЫЙ 3: урок переживает падение прибора ════════════════════

class NegLessonSurvives(unittest.TestCase):
    """Запись урока при УПАВШЕЙ регрессии всё равно происходит — владелец не теряет свой урок."""

    def setUp(self):
        import trainer
        self.trainer = trainer
        self.written = []

    def _append(self, rule):
        self.written.append(rule)
        return "added"

    def _rules(self):
        return [{"n": 7, "rule": r} for r in self.written]

    def test_lesson_is_written_even_when_regression_explodes(self):
        def boom(rule, n=None):
            raise RuntimeError("прибор развалился")
        dec = self.trainer.apply_lesson("не дублируй модель", append_rule=self._append,
                                        classify=lambda _r: "behavior", mark=lambda _r: None,
                                        list_rules=self._rules, regress=boom)
        self.assertEqual(dec["status"], "added", "урок потерян из-за нашего прибора")
        self.assertIn("записано в книгу правил", dec["card"])
        self.assertEqual(dec["n"], 7)
        self.assertEqual(self.written, ["не дублируй модель"])
        self.assertNotIn("не применён", dec["card"])

    def test_lesson_is_written_when_spawn_reports_failure(self):
        dec = self.trainer.apply_lesson("правило", append_rule=self._append,
                                        classify=lambda _r: "behavior", mark=lambda _r: None,
                                        list_rules=self._rules,
                                        regress=lambda r, n=None: {"spawned": False, "why": "нет venv"})
        self.assertEqual(dec["status"], "added")
        self.assertIn("записано в книгу правил", dec["card"])

    def test_regress_is_called_after_the_rule_is_in_the_book(self):
        seen = {}

        def spy(rule, n=None):
            seen["rule"], seen["n"], seen["book"] = rule, n, list(self.written)
            return {"spawned": True, "why": ""}
        self.trainer.apply_lesson("правило X", append_rule=self._append,
                                  classify=lambda _r: "behavior", mark=lambda _r: None,
                                  list_rules=self._rules, regress=spy)
        self.assertEqual(seen["rule"], "правило X")
        self.assertEqual(seen["n"], 7, "прибор обязан знать номер урока — им же «отмени урок N»")
        self.assertEqual(seen["book"], ["правило X"], "регрессия позвана ДО записи правила")

    def test_duplicate_does_not_pay_for_a_run(self):
        called = []
        dec = self.trainer.apply_lesson("правило", append_rule=lambda _r: "duplicate",
                                        classify=lambda _r: "behavior", mark=lambda _r: None,
                                        list_rules=self._rules,
                                        regress=lambda r, n=None: called.append(r))
        self.assertEqual(dec["status"], "duplicate")
        self.assertEqual(called, [], "книга не изменилась — 7 минут корпуса тратить не на что")

    def test_code_axis_does_not_run_the_corpus(self):
        """Ось «код» книгу не трогает — мерить нечего. `code_fix_claim` замокан: живой сбор заявки
        ходит в сессию и в мост, а предмет теста — ровно то, что регрессия НЕ зовётся."""
        called = []
        real = self.trainer.code_fix_claim
        self.trainer.code_fix_claim = lambda *a, **kw: {"card": "карточка", "placed": False,
                                                        "tid": None, "why": ""}
        try:
            self.trainer.apply_lesson("почини расчёт", append_rule=self._append,
                                      classify=lambda _r: "code", mark=lambda _r: None,
                                      list_rules=self._rules,
                                      regress=lambda r, n=None: called.append(r))
        finally:
            self.trainer.code_fix_claim = real
        self.assertEqual(called, [])
        self.assertEqual(self.written, [], "в книгу правил ось «код» не пишет")

    def test_button_path_passes_the_hook_through(self):
        seen = []
        self.trainer.apply_lessons(["правило A", "правило B"], append_rule=self._append,
                                   classify=lambda _r: "behavior", mark=lambda _r: None,
                                   list_rules=self._rules,
                                   regress=lambda r, n=None: seen.append(r))
        self.assertEqual(seen, ["правило A", "правило B"],
                         "кнопочный путь обязан мерить так же, как текстовый")


if __name__ == "__main__":
    unittest.main(verbosity=2)
