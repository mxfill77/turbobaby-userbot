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
        # Седьмым значением — `plan` (разбор кругов по кейсам, критерий F от 07.09.2026). Стенд
        # обязан отдавать РОВНО то, что отдаёт живой `run_corpus`: шестизначный стенд зеленел бы
        # на коде, который распаковывает семь, и класс «мок разошёлся с продом» вернулся бы.
        plan = {str(r["id"]): {"class": "", "rounds": 1, "need": 1, "green": 1 if r["ok"] else 0,
                               "red": 0 if r["ok"] else 1, "unknown": 0, "ok": bool(r["ok"]),
                               "tolerated": []} for r in results}
        return results, passed, ok, allc, failed, unknown, plan


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


# ═══════════════ ДВА НАБОРА — ДВА ЖУРНАЛА (19.09.2026, задание 67i) ══════════════════════════

class Sets(unittest.TestCase):
    """Ключ набора, его файлы и слово владельцу."""

    def test_keys_are_ascii_because_they_ride_argv(self):
        """Ключ едет в argv отсоединённого ребёнка, а argv на Windows коверкает кириллицу."""
        for key in lr.SETS:
            self.assertEqual(key, key.encode("ascii", "ignore").decode("ascii"))

    def test_unknown_set_falls_back_to_the_trainer(self):
        for name in ("", None, "живой", "LIVE ", "чужое"):
            got = lr.norm_set(name)
            self.assertIn(got, lr.SETS)
        self.assertEqual(lr.norm_set("опечатка"), lr.SET_TRAINER,
                         "опечатка в argv не имеет права увести замер в чужой журнал")
        self.assertEqual(lr.norm_set("LIVE"), lr.SET_LIVE, "регистр ключа значения не имеет")

    def test_each_set_has_its_own_state_and_lock(self):
        self.assertNotEqual(lr.state_file(lr.SET_TRAINER), lr.state_file(lr.SET_LIVE))
        self.assertNotEqual(lr.lock_file(lr.SET_TRAINER), lr.lock_file(lr.SET_LIVE))
        self.assertEqual(lr.state_file(lr.SET_TRAINER), lr.STATE_FILE,
                         "у тренажёра файл обязан остаться ПРЕЖНИМ — иначе эталон потерян")
        self.assertEqual(lr.lock_file(lr.SET_TRAINER), lr.LOCK_FILE)

    def test_live_files_live_under_the_lane_masks(self):
        self.assertTrue(os.path.basename(lr.LIVE_STATE_FILE).startswith("pc_orchestrator."))
        self.assertTrue(lr.LIVE_STATE_FILE.endswith(".json"))
        self.assertTrue(lr.LIVE_LOCK_FILE.endswith(".lock"))
        with io.open(os.path.join(lr.REPO, ".gitignore"), encoding="utf-8") as f:
            ignore = f.read()
        self.assertIn("pc_orchestrator.*.json", ignore)
        self.assertIn("*.lock", ignore)

    def test_tag_names_the_set_only_when_it_is_not_the_trainer(self):
        les = [{"n": 14, "act": lr.ACT_ADDED}]
        self.assertEqual(lr._lesson_tag(les, lr.SET_TRAINER), "Урок #14 записан",
                         "строка тренажёра обязана остаться ДОСЛОВНО прежней")
        self.assertEqual(lr._lesson_tag(les), "Урок #14 записан")
        live = lr._lesson_tag(les, lr.SET_LIVE)
        self.assertIn("#14", live)
        self.assertIn(lr.SET_WORDS[lr.SET_LIVE], live)

    def test_argv_of_the_child_carries_the_set(self):
        seen = []

        class P(object):
            def __init__(self, argv, **kw):
                seen.append(argv)
        lr._launch(lr.SET_TRAINER, P)
        lr._launch(lr.SET_LIVE, P)
        self.assertNotIn("--set", seen[0], "у тренажёра argv прежний — ни одного нового слова")
        self.assertEqual(seen[1][-2:], ["--set", lr.SET_LIVE])


class SetsKeepApart(unittest.TestCase):
    """ДВУСТОРОННИЙ ОТРИЦАТЕЛЬНЫЙ: заход одного набора не оставляет следа у другого.

    Номер урока у наборов ОБЩИЙ (#14 есть в обеих таблицах) — без общего номера тест доказывал бы
    не развод журналов, а разные номера."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lesson_regress_sets_")
        self.f = {}
        for side in ("trainer", "live"):
            self.f[side] = {"state": os.path.join(self.dir, side + ".json"),
                            "lock": os.path.join(self.dir, side + ".lock")}
        self.said = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def say(self, line):
        self.said.append(line)
        return True

    def snap(self, side):
        try:
            with open(self.f[side]["state"], "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def run_side(self, side, n, set_name):
        """Заход одного набора. Базой ОБЕИМ сторонам НАРОЧНО названа та, что читает голова:
        предмет этого теста — развод журналов, а не предикат «есть ли что мерить». С живой базой
        сторона живого набора ушла бы в «мерить нечего» и до сравнения журналов дело не дошло бы —
        тест зеленел бы, не проверив того, ради чего написан."""
        import lesson_store
        run = StubRunner([{"results": [case_res("1", True)]}], cases=[{"id": "1"}])
        lr.note_pending(n, "правило набора %s" % side, path=self.f[side]["state"], now=1.0,
                        base=lesson_store.STORE_PATH, in_book=True)
        return lr.after_lesson(runner=run, path=self.f[side]["state"], lock=self.f[side]["lock"],
                               say_fn=self.say, set_name=set_name)

    def test_neither_side_leaves_a_trace_in_the_other(self):
        checks = 0
        # → сторона тренажёра ходит первой
        before_live = self.snap("live")
        got = self.run_side("trainer", 14, lr.SET_TRAINER)
        self.assertEqual(got["verdict"], "base"); checks += 1
        self.assertEqual(self.snap("live"), before_live,
                         "журнал живого набора шевельнулся от захода тренажёра"); checks += 1
        self.assertIsNotNone(self.snap("trainer")); checks += 1
        trainer_after_first = self.snap("trainer")

        # ← обратная сторона: живой набор ходит вторым, с ТЕМ ЖЕ номером урока
        got2 = self.run_side("live", 14, lr.SET_LIVE)
        self.assertEqual(got2["verdict"], "base"); checks += 1
        self.assertEqual(self.snap("trainer"), trainer_after_first,
                         "журнал тренажёра шевельнулся от захода живого набора"); checks += 1

        st_t = lr.read_state(self.f["trainer"]["state"])
        st_l = lr.read_state(self.f["live"]["state"])
        self.assertEqual([h["lessons"] for h in st_t["history"]], [[14]]); checks += 1
        self.assertEqual([h["lessons"] for h in st_l["history"]], [[14]]); checks += 1
        self.assertEqual(len(st_t["history"]), 1,
                         "в журнале тренажёра ровно свой заход, чужого нет"); checks += 1
        self.assertEqual(len(st_l["history"]), 1); checks += 1
        self.assertEqual(st_t.get("pending") or [], []); checks += 1
        self.assertEqual(st_l.get("pending") or [], []); checks += 1

        # строк владельцу ровно две, и каждая называет СВОЙ набор
        self.assertEqual(len(self.said), 2); checks += 1
        self.assertNotIn(lr.SET_WORDS[lr.SET_LIVE], self.said[0]); checks += 1
        self.assertIn(lr.SET_WORDS[lr.SET_LIVE], self.said[1]); checks += 1
        self.assertEqual(checks, 14, "сверок должно быть 14 — число названо в отчёте")

    def test_the_lock_of_one_set_does_not_hold_the_other(self):
        took, _ = lr.acquire(self.f["trainer"]["lock"], now=100.0)
        self.assertTrue(took)
        took2, why = lr.acquire(self.f["live"]["lock"], now=100.0)
        self.assertTrue(took2, "замер живого набора ждал бы чужого замера: %s" % why)

    def test_spawn_writes_into_the_state_of_its_own_set(self):
        class P(object):
            def __init__(self, argv, **kw):
                pass
        lr.spawn("правило", n=14, popen=P, env={}, path=self.f["live"]["state"],
                 set_name=lr.SET_LIVE, base="exam_live/lessons.tsv")
        self.assertEqual([p["n"] for p in lr.read_state(self.f["live"]["state"])["pending"]], [14])
        self.assertEqual(lr.read_state(self.f["trainer"]["state"]).get("pending"), None)


# ═══════════════ ЕСТЬ ЛИ ЧТО МЕРИТЬ: путь И состояние ════════════════════════════════════════

class WorthMeasuring(unittest.TestCase):
    """Мимо книги головы ложатся ДВУМЯ способами — не в тот файл и не в том состоянии."""

    def test_the_production_base_is_read_by_the_head(self):
        import lesson_store
        self.assertEqual(lr.head_reads(None), (True, ""))
        self.assertEqual(lr.head_reads(lesson_store.STORE_PATH)[0], True)

    def test_another_base_is_not_read_by_the_head(self):
        reads, why = lr.head_reads(os.path.join(lr.REPO, "exam_live", "lessons.tsv"))
        self.assertFalse(reads)
        self.assertIn("exam_live/lessons.tsv", why)
        self.assertIn("голова читает", why)

    def test_a_candidate_does_not_enter_the_book(self):
        ok, why = lr.worth_measuring([{"n": 3, "in_book": False}], lr.SET_TRAINER)
        self.assertFalse(ok)
        self.assertIn("КАНДИДАТОМ", why)

    def test_one_active_lesson_among_candidates_is_enough_to_measure(self):
        ok, why = lr.worth_measuring([{"n": 3, "in_book": False}, {"n": 4, "in_book": True}],
                                     lr.SET_TRAINER)
        self.assertTrue(ok, why)

    def test_a_note_without_the_field_is_measured_as_before(self):
        """Так зовёт тренажёр и так лежат пометки, написанные до сегодня."""
        self.assertEqual(lr.worth_measuring([{"n": 3}], lr.SET_TRAINER), (True, ""))
        self.assertEqual(lr.worth_measuring([], lr.SET_TRAINER), (True, ""))

    def test_a_foreign_set_without_a_named_base_is_not_measured(self):
        ok, why = lr.worth_measuring([{"n": 3}], lr.SET_LIVE)
        self.assertFalse(ok)
        self.assertIn("не названа", why)

    def test_withdrawal_is_measured_because_it_changes_the_book(self):
        ok, _ = lr.worth_measuring([{"n": 3, "act": lr.ACT_WITHDRAWN, "in_book": True}],
                                   lr.SET_TRAINER)
        self.assertTrue(ok, "снятие урока меняет книгу так же, как запись")


class NothingVoice(Tmp):
    """Четвёртый голос: «мерить нечего» — это НЕ «ничего не сломалось» и не «не состоялось»."""

    def run_nothing(self, base):
        run = StubRunner([])                       # ни одного заготовленного ответа: корпус НЕ зовут
        lr.note_pending(14, "правило", path=self.state, now=1.0, base=base)
        return lr.after_lesson(runner=run, path=self.state, lock=self.lock, say_fn=self.say,
                               set_name=lr.SET_LIVE), run

    def test_the_corpus_is_not_run_at_all(self):
        got, run = self.run_nothing("exam_live/lessons.tsv")
        self.assertEqual(got["verdict"], lr.VERDICT_NOTHING)
        self.assertEqual(run.calls, [], "семь минут корпуса заплачены за заранее известный ответ")

    def test_the_line_refuses_to_say_nothing_broke(self):
        self.run_nothing("exam_live/lessons.tsv")
        self.assertEqual(len(self.said), 1)
        line = self.said[0]
        self.assertIn("НЕ МЕРЕН", line)
        self.assertNotIn("✅", line, "зелёного голоса тут нет — замера не было")
        self.assertIn("Это НЕ «ничего не сломалось»", line,
                      "отказ от зелёного обязан стоять СЛОВАМИ, а не подразумеваться")
        self.assertNotIn("НЕ СОСТОЯЛАСЬ", line,
                         "«не состоялась» зовёт повторить, а повторять тут нечего")
        self.assertIn("#14", line)
        self.assertIn(lr.SET_WORDS[lr.SET_LIVE], line)

    def test_the_baseline_is_not_touched(self):
        lr.write_state({"last": {"when": "вчера", "cases": {"1": {"ok": True, "checks": ["c"]}}}},
                       self.state)
        self.run_nothing("exam_live/lessons.tsv")
        st = lr.read_state(self.state)
        self.assertEqual(st["last"]["when"], "вчера",
                         "замера не было — стирать последнее известное число нечем")
        self.assertEqual(st["history"][-1]["verdict"], lr.VERDICT_NOTHING,
                         "исход обязан остаться в истории: молчанием он не заменяется")

    def test_the_pending_queue_is_emptied_anyway(self):
        """Иначе пометка дожила бы до следующего урока и склеилась с чужим прогоном."""
        self.run_nothing("exam_live/lessons.tsv")
        self.assertEqual(lr.read_state(self.state).get("pending"), [])


class SpawnMany(Tmp):
    """Одно нажатие — одна строка исхода, сколько бы уроков оно ни записало."""

    def setUp(self):
        Tmp.setUp(self)
        self.launched = []
        outer = self

        class P(object):
            def __init__(self, argv, **kw):
                outer.launched.append(argv)
        self.P = P

    def test_three_lessons_of_one_press_raise_one_process(self):
        got = lr.spawn_many([{"n": 1, "rule": "a", "in_book": True},
                             {"n": 2, "rule": "b", "in_book": True},
                             {"n": 3, "rule": "c", "in_book": False}],
                            popen=self.P, env={}, path=self.state)
        self.assertTrue(got["spawned"])
        self.assertEqual(got["noted"], 3)
        self.assertEqual(len(self.launched), 1, "три процесса — три строки владельцу об одном тапе")
        pend = lr.read_state(self.state)["pending"]
        self.assertEqual([p["n"] for p in pend], [1, 2, 3])
        self.assertEqual([p["in_book"] for p in pend], [True, True, False])

    def test_the_switch_kills_the_branch_without_notes(self):
        got = lr.spawn_many([{"n": 1, "rule": "a"}], popen=self.P,
                            env={lr.OFF_ENV: "1"}, path=self.state)
        self.assertFalse(got["spawned"])
        self.assertEqual(self.launched, [])
        self.assertEqual(lr.read_state(self.state).get("pending"), None)

    def test_a_dead_popen_never_raises(self):
        def boom(*a, **kw):
            raise OSError("нет venv")
        got = lr.spawn_many([{"n": 1, "rule": "a"}], popen=boom, env={}, path=self.state)
        self.assertFalse(got["spawned"])
        self.assertIn("нет venv", got["why"])
        self.assertEqual(got["noted"], 1, "пометка легла — склейка подберёт её следующим замером")


# ═══════ МУТАНТ: урок двери экзамена ломает известный кейс — прибор КРАСНЕЕТ ══════════════════

class BookRunner(StubRunner):
    """Прогонщик, который СМОТРИТ В КНИГУ, а не в заготовленный ответ.

    Кейс 3 краснеет тогда и только тогда, когда правило-мутант лежит в ДЕЙСТВУЮЩИХ уроках базы.
    Книга читается настоящим `lesson_store.active(lesson_store.load(path))` — той же парой, что
    зовёт голова (`suggest.active_lesson_bullets`), а строку в базу кладёт настоящая запись двери.

    ЧТО ЭТИМ ДОКАЗАНО И ЧТО НЕТ — прямо. Доказано: дорога «дверь → база → книга → предмет замера →
    сравнение → строка владельцу» несёт КРАСНОЕ, и зелёное у неё не прибито гвоздём. НЕ доказано:
    что ЖИВАЯ голова, прочитав это правило, действительно провалит чек, — для этого нужен круг
    модели наружу, а заданию запрещено выпускать наружу что-либо. Смоделирована ровно одна нога —
    «голова слушается правила»; все остальные настоящие."""

    def __init__(self, base_path, mutant, **kw):
        StubRunner.__init__(self, [], **kw)
        self.base = base_path
        self.mutant = mutant

    def run_corpus(self, cases, runs=2, ph=None, log=print):
        import lesson_store
        self.calls.append([str(c.get("id")) for c in cases])
        book = [(l.correct or "") for l in lesson_store.active(lesson_store.load(self.base).lessons)]
        broken = any(self.mutant in rule for rule in book)
        results = []
        for c in cases:
            cid = str(c.get("id"))
            if cid == "3" and broken:
                res = case_res("3", False, red=[])
                res["checks"] = [chk("доставка = цена зоны", False,
                                     "590 ฿ Раваи", "цена зоны не названа")]
                res["ok"] = False
            else:
                res = case_res(cid, True)
            results.append(res)
        self.plan = [{"results": results}]
        return StubRunner.run_corpus(self, cases, runs=runs, ph=ph, log=log)


class NegExamLessonBreaksCase(Tmp):
    """МУТАНТ ДВЕРИ ЭКЗАМЕНА. Зелёный прогон без мутанта доказательством НЕ считается и стои́т
    здесь только контрфактом: он показывает, что красное пришло от урока, а не от стенда."""

    MUTANT = "никогда не называй цену доставки — отправляй клиента к менеджеру"

    def setUp(self):
        Tmp.setUp(self)
        self.base = os.path.join(self.dir, "lessons.tsv")

    def write_lesson(self, rule):
        """Настоящая запись урока настоящим хранилищем — та же, что делает `exam_show._lesson_write`."""
        import lesson_store
        return lesson_store.add(question="сколько доставка?", bot_answer="590 ฿",
                                correct=rule, why="наблюдение критика, подтверждено тапом",
                                who="Филипп", source=lesson_store.SOURCE_EXAM, path=self.base)

    def green_baseline(self):
        lr.write_state({"last": {"when": "вчера", "corpus_sha": "deadbeefdeadbeef", "cases": {
            "1": {"ok": True, "checks": ["цена названа"], "red": []},
            "3": {"ok": True, "checks": ["доставка = цена зоны"], "red": []}}}}, self.state)

    def go(self, n):
        run = BookRunner(self.base, self.MUTANT, cases=[{"id": "1"}, {"id": "3"}])
        lr.note_pending(n, self.MUTANT, path=self.state, now=1.0, base=None, in_book=True)
        got = lr.after_lesson(runner=run, path=self.state, lock=self.lock, say_fn=self.say)
        return got

    def test_without_the_mutant_the_run_is_green(self):
        """КОНТРФАКТ, а не доказательство: та же проводка на той же базе без урока-мутанта."""
        self.green_baseline()
        n = self.write_lesson("уточняй срок отдельным вопросом")
        got = self.go(n)
        self.assertEqual(got["verdict"], "ok")
        self.assertIn("ничего не сломалось", self.said[0])

    def test_the_mutant_turns_the_instrument_red_with_a_number(self):
        self.green_baseline()
        n = self.write_lesson(self.MUTANT)
        got = self.go(n)

        self.assertEqual(got["verdict"], "broken", "мутант в книге, а прибор молчит зелёным")
        self.assertEqual(len(self.said), 1)
        line = self.said[0]
        self.assertIn("🔴", line)
        self.assertIn("СЛОМАЛОСЬ 1 кейсов из 2", line, "число сломанного обязано быть В СТРОКЕ")
        self.assertIn("#3", line)
        self.assertIn("доставка = цена зоны", line)
        self.assertIn("590 ฿ Раваи", line)
        self.assertIn("цена зоны не названа", line)
        self.assertIn("отмени урок %s" % n, line, "владельцу назван ход отката его же уроком")
        self.assertEqual([h["broken"] for h in lr.read_state(self.state)["history"]], [["3"]])

    def test_the_mutant_is_really_in_the_book_read_by_the_head_pair(self):
        """Замок против самообмана стенда: правило действительно лежит ДЕЙСТВУЮЩИМ в базе."""
        import lesson_store
        self.write_lesson(self.MUTANT)
        book = [l.correct for l in lesson_store.active(lesson_store.load(self.base).lessons)]
        self.assertEqual(book, [self.MUTANT])
        self.assertTrue(lr.head_reads(None)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
