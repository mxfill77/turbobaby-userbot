# -*- coding: utf-8 -*-
"""
test_samoprogon.py — самопрогон тренажёра без владельца (задание Штаба 0024-72c.2309).

Прогонять с TESTING=1:
    TESTING=1 venv/Scripts/python.exe -m unittest test_samoprogon -v

Живой головы, живой сети и боевых файлов набор не касается НИ ОДНОЙ веткой: прогонщик подменён,
«боевой отправитель» — счётчик на месте `_default_sender` обоих сторожей (ровно та ручка, которую
боевой геттер отдаёт как `dispatch_notify.deliver`), состояние, лок, отчёт и таблица кандидатов —
во временном каталоге. Боевая база уроков только ЧИТАЕТСЯ (отпечаток до и после).
"""

import ast
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import test_isolation  # noqa: E402,F401 — TESTING до импорта suggest/moderation_ipc

import exam_show  # noqa: E402
import lesson_store  # noqa: E402
import noprice_gate  # noqa: E402
import samoprogon as sp  # noqa: E402
import season_gate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

SEASON_DOC = {"season": {"periods": [
    {"key": "P1", "name": "ИЮНЬ-СЕНТЯБРЬ", "from": "06-01", "to": "09-30"},
    {"key": "P2", "name": "ОКТЯБРЬ", "from": "10-01", "to": "10-31"}]}}
HINTS_CROSS = {"models": ["XMAX 300"], "iso_start": "2026-09-26", "iso_end": "2026-10-05",
               "hint_days": 9}
CASE17 = {"id": 17, "name": "период через границу сезонов", "lang": "ru",
          "needs": "season_cross", "forbid_price": {"allow": ["300"]},
          "lines": ["Сколько XMAX 300 {when_cross}?"]}
CASE_PRICE = {"id": 1, "name": "одна модель", "lang": "ru",
              "expect": {"quote": True, "price_figure": True}, "lines": ["XMAX 300 {when}"]}


def _chk(name, ok=True, **kw):
    d = {"name": name, "ok": ok, "expected": "", "fact": ""}
    d.update(kw)
    return d


def _rec(case, checks, unknown=None, draft="ответ бота"):
    return {"id": str(case["id"]), "results": [{"id": case["id"], "name": case.get("name"),
                                                "ok": all(c["ok"] for c in checks),
                                                "unknown": unknown, "checks": checks,
                                                "draft": draft, "note": "", "run": 1}]}


class FakeRunner(object):
    """Прогонщик в форме `trainer_run`: `run_one_case` зовёт сторожа В ФОРМЕ ПУТИ ОТВЕТА — без
    `sender`, как `suggest.py`, — и возвращает запись кейса с заданными чеками."""

    def __init__(self, checks_by_id=None, cases=None, action=None):
        self.checks_by_id = checks_by_id or {}
        self.cases = cases or [CASE17]
        self.action = action
        self.calls = []

    def load_cases(self):
        return list(self.cases), "sha"

    def placeholders(self):
        return {"when": "с 6 по 11 октября", "when_cross": "с 26.09 по 05.10"}

    def build_transcript(self, case, ph):
        return "клиент: " + self.substitute(case["lines"][-1], ph)

    def substitute(self, text, ph):
        return str(text).format(**ph)

    def run_one_case(self, case, runs, ph, log=print):
        self.calls.append((case["id"], runs))
        if self.action:
            self.action(case)
        elif case.get("needs") == "season_cross":
            season_gate.note_for(HINTS_CROSS, lang="ru", doc=SEASON_DOC)
        checks = self.checks_by_id.get(case["id"], [_chk("цены нет ни в каком виде")])
        return _rec(case, checks)


class _NoTrap(object):
    """КОНТРФАКТ: ловушки нет. Без него ноль ниже доказывал бы лишь то, что отправки нет вообще."""

    def __init__(self):
        self.cards = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="samoprogon_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = os.path.join(self.tmp, "state.json")
        self.lock = os.path.join(self.tmp, "run.lock")
        self.cand = os.path.join(self.tmp, "candidates.tsv")
        self.report = os.path.join(self.tmp, "report.md")
        self.gates = {"season_gate": season_gate, "noprice_gate": noprice_gate}
        self.live = {}
        for name, mod in self.gates.items():
            self.addCleanup(setattr, mod, "_default_sender", mod._default_sender)
            self.addCleanup(mod.reset)
            mod.reset()
            calls = []
            self.live[name] = calls
            mod._default_sender = self._live_getter(calls)
        for key in (season_gate.OFF_ENV, noprice_gate.OFF_ENV):
            if key in os.environ:
                self.addCleanup(os.environ.__setitem__, key, os.environ[key])
                del os.environ[key]

    @staticmethod
    def _live_getter(calls):
        def getter():
            def deliver(text, reply_markup=None, declared=None):
                calls.append(text)
                return ("боевой (счётчик набора)", True)
            return deliver
        return getter

    def sent(self):
        return sum(len(v) for v in self.live.values())


# ─────────────────────────── п.1: ЛОВУШКА НАРУЖУ — главный замок ─────────────────────────────

class TestNothingGoesOut(Base):
    def test_case17_under_selfrun_sends_nothing(self):
        got = sp.run(runner=FakeRunner(), critic=None, cand_path=self.cand)
        self.assertTrue(got["ok"], got.get("why"))
        self.assertEqual(self.sent(), 0, "кейс 17 под самопрогоном дошёл до отправителя")
        self.assertEqual(got["cards"], [{"case": "17", "gate": "season_gate"}])

    def test_case17_without_trap_does_send(self):
        """Контрфакт: та же дорога без ловушки доходит до отправителя — было «слал»."""
        got = sp.run(runner=FakeRunner(), trap_factory=_NoTrap, critic=None, cand_path=self.cand)
        self.assertTrue(got["ok"])
        self.assertEqual(len(self.live["season_gate"]), 1)
        self.assertTrue(self.live["season_gate"][0].startswith(season_gate.CARD_HEAD))

    def test_unknown_branch_importing_the_sender_is_caught(self):
        """ВТОРОЙ СЛОЙ: ветка вне переписи лениво импортирует отправителя сама."""
        was = sys.modules.get("dispatch_notify")
        self.addCleanup(lambda: sys.modules.__setitem__("dispatch_notify", was) if was is not None
                        else sys.modules.pop("dispatch_notify", None))
        calls = []

        class LiveStandIn(object):
            @staticmethod
            def deliver(text, reply_markup=None, declared=None):
                calls.append(text)
                return ("боевой", True)
        stand_in = LiveStandIn()
        sys.modules["dispatch_notify"] = stand_in

        def action(case):
            import dispatch_notify
            try:
                dispatch_notify.deliver("⛔ карточка из ветки вне переписи")
            except RuntimeError:
                pass
        got = sp.run(runner=FakeRunner(action=action), critic=None, cand_path=self.cand)
        self.assertEqual(calls, [])
        self.assertEqual(got["cards"], [{"case": "17", "gate": "dispatch_notify.deliver"}])
        self.assertIs(sys.modules.get("dispatch_notify"), stand_in, "модуль не вернулся на место")

    def test_trap_not_set_head_not_called(self):
        def broken():
            raise LookupError("нет _default_sender")
        runner = FakeRunner()
        got = sp.run(runner=runner, trap_factory=broken, critic=None, cand_path=self.cand)
        self.assertFalse(got["ok"])
        self.assertIn("ловушка наружу не встала", got["why"])
        self.assertEqual(runner.calls, [], "голову позвали без ловушки")

    def test_trap_removed_after_each_case(self):
        before = {n: m._default_sender for n, m in self.gates.items()}
        sp.run(runner=FakeRunner(cases=[CASE17, dict(CASE17, id=18)]), critic=None,
               cand_path=self.cand)
        for name, mod in self.gates.items():
            self.assertIs(mod._default_sender, before[name], name)
            self.assertEqual(mod._seen, {}, "пойманная карточка оставила отметку «сказано»")

    def test_default_trap_is_the_exam_trap_not_a_copy(self):
        trap = sp.default_trap()
        self.assertIsInstance(trap, exam_show.OwnerCardTrap)
        self.assertEqual(trap.gates, exam_show.OWNER_CARD_GATES)

    def test_critic_runs_under_the_trap_too(self):
        seen = []

        def critic(q, a, tr):
            seen.append(isinstance(sys.modules.get("dispatch_notify"), exam_show._SenderStub))
            return [{"observation": "", "rule": "называй минимальный срок"}]
        runner = FakeRunner(checks_by_id={17: [_chk("цены нет ни в каком виде", ok=False)]})
        sp.run(runner=runner, critic=critic, cand_path=self.cand)
        self.assertEqual(seen, [True])


# ─────────────────────────── п.3: ЧЕСТНЫЙ СЧЁТ — цена никогда не зелёная ────────────────────

class TestHonestJudge(unittest.TestCase):
    def test_code_printed_price_is_unknown_not_green(self):
        j = sp.judge_case(CASE_PRICE, _rec(CASE_PRICE, [
            _chk("строка J дословно"), _chk("цена цифрой"), _chk("язык ответа")]))
        self.assertEqual(j["outcome"], sp.UNKNOWN)
        self.assertTrue(j["why"].startswith("замкнутый судья"), j["why"])
        self.assertEqual(j["honest_ok"], ["язык ответа"])

    def test_honest_red_wins_over_closed(self):
        j = sp.judge_case(CASE_PRICE, _rec(CASE_PRICE, [
            _chk("цена цифрой"), _chk("без «опасн»", ok=False)]))
        self.assertEqual(j["outcome"], sp.RED)
        self.assertEqual(j["honest_red"], ["без «опасн»"])

    def test_closed_red_is_shown_but_not_counted_red(self):
        j = sp.judge_case(CASE_PRICE, _rec(CASE_PRICE, [
            _chk("строка J дословно", ok=False), _chk("язык ответа")]))
        self.assertEqual(j["outcome"], sp.UNKNOWN)
        self.assertEqual(j["closed_red"], ["строка J дословно"])

    def test_all_honest_green_is_green(self):
        j = sp.judge_case(CASE17, _rec(CASE17, [_chk("цены нет ни в каком виде"),
                                                _chk("язык ответа")]))
        self.assertEqual(j["outcome"], sp.GREEN)

    def test_instrument_unknown_is_unknown_even_with_green_checks(self):
        j = sp.judge_case(CASE17, _rec(CASE17, [_chk("язык ответа")], unknown="голова промолчала"))
        self.assertEqual(j["outcome"], sp.UNKNOWN)
        self.assertEqual(j["honest_ok"], [])

    def test_delivery_is_closed_only_without_corpus_price(self):
        chk = _chk(sp.DELIVERY_CHECK)
        self.assertEqual(sp.judge_check({"zone_price": 590}, chk)[0], "honest")
        self.assertEqual(sp.judge_check({}, chk)[0], "closed")

    def test_skipped_check_is_not_counted(self):
        j = sp.judge_case(CASE17, _rec(CASE17, [_chk("язык ответа"),
                                                _chk("депозит", ok=False, skipped="снят")]))
        self.assertEqual(j["outcome"], sp.GREEN)

    def test_tally_line_is_loud_about_unknown(self):
        js = [{"outcome": sp.GREEN, "why": ""}, {"outcome": sp.RED, "why": ""},
              {"outcome": sp.UNKNOWN, "why": "замкнутый судья: цена цифрой"},
              {"outcome": sp.UNKNOWN, "why": "прибор: голова промолчала"}]
        t = sp.tally(js)
        self.assertTrue(t["line"].startswith("НЕИЗВЕСТНО 2 из 4 (50%)"), t["line"])
        self.assertEqual((t["unknown_closed"], t["unknown_instrument"], t["honest_measurable"]),
                         (1, 1, 2))

    def test_closed_names_exist_in_the_live_checks(self):
        """Перечень замкнутого судьи не протух: каждое имя стоит в живом коде чеков."""
        src = ""
        for name in ("trainer_run.py", "suggest.py"):
            with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
                src += f.read()
        for name in list(sp.CLOSED_CHECKS) + [sp.DELIVERY_CHECK]:
            self.assertIn('"%s"' % name, src, name)

    def test_plan_of_the_live_corpus(self):
        """Числа доклада: корпус 17, замкнутый судья цены у 9, кейс 17 — честный."""
        import json
        with io.open(os.path.join(HERE, "trainer_cases.json"), encoding="utf-8") as f:
            data = json.load(f)
        p = sp.plan(data["cases"] if isinstance(data, dict) else data)
        self.assertEqual(len(p), 17)
        closed = [x["id"] for x in p if x["closed"]]
        self.assertEqual(closed, ["1", "2", "3", "7", "8", "12", "13", "14", "16"])
        self.assertEqual([x["judge"] for x in p if x["id"] == "17"], ["честный"])


# ─────────────────────────── п.4: КАНДИДАТЫ — не действующие, не в боевой базе ────────────────

class TestCandidates(Base):
    def _book_sha(self):
        try:
            with open(lesson_store.STORE_PATH, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return "нет файла"

    def test_proposals_land_as_candidates_with_empty_reason(self):
        book = self._book_sha()
        runner = FakeRunner(checks_by_id={17: [_chk("цены нет ни в каком виде", ok=False)]})
        got = sp.run(runner=runner, critic=lambda q, a, tr: [{"observation": "о", "rule": "правило"}],
                     cand_path=self.cand)
        self.assertEqual(got["cand_ok"], 1, got.get("cand_why"))
        rows = lesson_store.load(self.cand).lessons
        self.assertEqual([r.state for r in rows], [lesson_store.STATE_CANDIDATE])
        self.assertEqual(rows[0].why, "")
        self.assertEqual(rows[0].who, sp.WHO)
        self.assertEqual(list(lesson_store.active(rows)), [], "кандидат попал в книгу головы")
        self.assertEqual(self._book_sha(), book, "боевая база уроков тронута")

    def test_green_case_asks_no_critic(self):
        asked = []
        sp.run(runner=FakeRunner(), critic=lambda q, a, tr: asked.append(q) or [],
               cand_path=self.cand)
        self.assertEqual(asked, [])
        self.assertFalse(os.path.exists(self.cand))

    def test_combat_path_is_refused(self):
        class Store(object):
            STORE_PATH = self.cand
            SOURCE_TRAINER = "тренажёр"

            @staticmethod
            def add_candidate(*a, **k):
                raise AssertionError("писать в боевую базу нельзя")
        ok, bad, why = sp.write_candidates([{"question": "q", "answer": "a", "rule": "r"}],
                                           path=self.cand, store=Store)
        self.assertEqual((ok, bad), (0, 1))
        self.assertIn("боевой базой", why[0])


# ─────────────────────────── п.2: ЖИВОЙ ВЫЗЫВАЮЩИЙ и пустая очередь ──────────────────────────

S = {"commit": "a" * 40, "corpus_sha": "c" * 16, "book_sha": "b" * 16}
# 2026-09-24 05:00 по Пхукету = 2026-09-23 22:00 UTC
T_DUE = 1790200800.0
T_EARLY = T_DUE - 2 * 3600          # 03:00 местного


class TestLiveCaller(Base):
    def popen(self):
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw))
        return calls, fake

    def test_due_once_per_local_day_after_hour(self):
        self.assertFalse(sp.due({}, T_EARLY, 4)[0])
        ok, day = sp.due({}, T_DUE, 4)
        self.assertTrue(ok)
        self.assertEqual(day, "2026-09-24")
        self.assertFalse(sp.due({"launched_day": day}, T_DUE, 4)[0])
        self.assertTrue(sp.due({"launched_day": day}, T_DUE + 86400, 4)[0])

    def test_first_launch_is_detached_and_not_the_lesson_path(self):
        calls, fake = self.popen()
        got = sp.maybe_launch(now=T_DUE, path=self.state, popen=fake, env={}, subj=S,
                              lock=self.lock)
        self.assertTrue(got["launched"], got["why"])
        argv, kw = calls[0]
        self.assertEqual(argv, sp.launch_argv())
        self.assertEqual(argv[-1], "--run")
        self.assertNotIn("--after-lesson", argv)
        self.assertEqual(kw["creationflags"], sp.DETACHED)
        again = sp.maybe_launch(now=T_DUE + 60, path=self.state, popen=fake, env={}, subj=S,
                                lock=self.lock)
        self.assertFalse(again["launched"])
        self.assertEqual(len(calls), 1)

    def test_empty_queue_launches_nothing(self):
        sp.write_state({"measured_subject": dict(S)}, self.state)
        calls, fake = self.popen()
        got = sp.maybe_launch(now=T_DUE, path=self.state, popen=fake, env={}, subj=dict(S),
                              lock=self.lock)
        self.assertFalse(got["launched"])
        self.assertIn("очередь пуста", got["why"])
        self.assertEqual(calls, [])
        self.assertEqual(sp.read_state(self.state).get("skipped_day"), "2026-09-24")

    def test_off_and_testing_launch_nothing(self):
        calls, fake = self.popen()
        self.assertFalse(sp.maybe_launch(now=T_DUE, path=self.state, popen=fake,
                                         env={sp.OFF_ENV: "1"}, subj=S)["launched"])
        self.assertFalse(sp.maybe_launch(now=T_DUE, path=self.state, popen=fake, subj=S)["launched"])
        self.assertEqual(calls, [])

    def test_never_raises(self):
        def boom(*a, **k):
            raise OSError("нет python")
        got = sp.maybe_launch(now=T_DUE, path=self.state, popen=boom, env={}, subj=S,
                              lock=self.lock)
        self.assertFalse(got["launched"])

    def test_queue_words(self):
        self.assertEqual(sp.queue({}, S), ["первый замер"])
        self.assertEqual(sp.queue({"measured_subject": S}, dict(S)), [])
        moved = sp.queue({"measured_subject": S}, dict(S, commit="b" * 40))
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0].startswith("коммит кода"))
        self.assertEqual(len(sp.queue({"measured_subject": S}, dict(S, book_sha="?"))), 1)

    def test_child_with_empty_queue_does_not_measure(self):
        sp.write_state({"measured_subject": dict(S)}, self.state)
        runner = FakeRunner()
        got = sp.child(path=self.state, lock=self.lock, subj=dict(S), runner=runner, critic=None,
                       report=self.report, cand_path=self.cand)
        self.assertFalse(got["ran"])
        self.assertEqual(runner.calls, [], "при пустой очереди замер состоялся")
        self.assertEqual(self.sent(), 0)
        hist = sp.read_state(self.state)["history"]
        self.assertFalse(hist[-1]["measured"])
        self.assertFalse(os.path.exists(self.report))

    def test_child_measures_and_records_zero_sent(self):
        runner = FakeRunner(cases=[CASE17, CASE_PRICE], checks_by_id={
            1: [_chk("строка J дословно"), _chk("цена цифрой"), _chk("язык ответа")]})
        got = sp.child(path=self.state, lock=self.lock, subj=dict(S), runner=runner, critic=None,
                       report=self.report, cand_path=self.cand)
        self.assertTrue(got["ran"])
        self.assertEqual(runner.calls, [(17, sp.RUNS), (1, sp.RUNS)])
        self.assertEqual(self.sent(), 0)
        st = sp.read_state(self.state)
        self.assertEqual(st["measured_subject"], S)
        last = st["last"]
        self.assertEqual((last["tally"]["green"], last["tally"]["unknown_closed"]), (1, 1))
        self.assertEqual((last["cards_caught"], last["sent"]), (1, 0))
        with io.open(self.report, encoding="utf-8") as f:
            self.assertIn("НЕИЗВЕСТНО 1 из 2", f.read())
        self.assertTrue(sp.acquire(self.lock)[0], "лок не освобождён")

    def test_partial_run_does_not_empty_the_queue(self):
        runner = FakeRunner(cases=[CASE17, CASE_PRICE])
        got = sp.child(path=self.state, lock=self.lock, subj=dict(S), runner=runner, critic=None,
                       report=self.report, cand_path=self.cand, only=["17"])
        self.assertTrue(got["ran"])
        self.assertEqual(runner.calls, [(17, sp.RUNS)])
        st = sp.read_state(self.state)
        self.assertNotIn("measured_subject", st)
        self.assertEqual(sp.queue(st, dict(S)), ["первый замер"])


# ─────────────────────────── замки по коду модуля (AST, не комментарии) ─────────────────────

class TestLocks(unittest.TestCase):
    FORBIDDEN_NAMES = {"dispatch_notify", "deliver", "send_critical", "say", "after_lesson",
                       "promote", "promote_lesson", "write_verdict", "append_playbook_rule",
                       "brain_writer", "cowork_log_append", "spawn", "spawn_many"}
    FORBIDDEN_TEXT = ("--after-lesson", "Урок записан", "урок записан", "dispatch_notify.py")

    def _tree(self):
        with io.open(sp.__file__, encoding="utf-8") as f:
            return ast.parse(f.read())

    def test_no_sender_promote_or_lesson_path_in_code(self):
        tree = self._tree()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update(a.name.split(".")[0] for a in node.names)
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
        self.assertEqual(names & self.FORBIDDEN_NAMES, set())

    def test_no_lesson_line_or_lesson_key_in_code_strings(self):
        tree = self._tree()
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                d = ast.get_docstring(node, clean=False)
                if d:
                    docs.add(d)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value not in docs:
                for bad in self.FORBIDDEN_TEXT:
                    self.assertNotIn(bad, node.value)

    def test_module_is_outside_the_client_contour(self):
        import client_contour
        self.assertFalse(client_contour.is_client("samoprogon.py"))


if __name__ == "__main__":
    unittest.main()
