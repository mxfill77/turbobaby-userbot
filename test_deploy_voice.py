# -*- coding: utf-8 -*-
"""
test_deploy_voice.py — ГОЛДЕНЫ ГОЛОСА ПОДЪЁМА МИМО ВОРОТ (03.09.2026).

БЕЗ живого git, сети, пушей и боевых файлов: всё инъектируется, память подъёмов — во временном
каталоге. Ни один тест не поднимает и не роняет процессов, ворот не касается.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_deploy_voice -v

Что стережём:
  • ОБЕ ДВЕРИ говорят: авто-реадопшн агента (B1) и контур-вотчдог демона (B2) — врезка проверена
    по живому коду, а не по памяти;
  • СОДЕРЖАНИЕ строки: коммит на диске, отличие от прежнего подъёма, клиентские файлы среди
    отличий — и слово «выкатка клиентского кода без ворот», а не намёк;
  • ФОРМА ОТЛИЧИМА: слов новой строки нет ни в одной существующей форме контура (штатная строка
    отставания, карточка сторожа, отказ ворот) — сверка по ЖИВОМУ коду;
  • ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ: тот же коммит → строки о выкатке НЕТ; новый коммит без клиентских →
    строка есть, владельца не будим; с клиентскими → будим; git молчит → «коммит неизвестен»;
  • ДВЕРЬ НЕ ЗАКРЫВАЕТСЯ И БОТ НЕ ПАДАЕТ: голос не возвращает запрета и не выпускает исключений
    ни в одном исходе, включая полный отказ каждой своей части.
"""

import ast
import io
import json
import os
import shutil
import tempfile
import unittest

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (как в соседях)

import deploy_voice as dv              # noqa: E402
import pc_orchestrator as o            # noqa: E402

REPO = os.path.dirname(os.path.abspath(__file__))

UB, MB = "userbot", "moderation_bot"
D1, D2 = dv.DOOR_READOPT, dv.DOOR_WATCHDOG
OLD, NEW = "6cbdd15aa11", "5db456143bb"
CLIENT = ["suggest.py", "price_source.json", "model_name.py"]
INNER = ["pc_orchestrator.py", "docs/x.md", "test_pc_orchestrator.py"]


# ═══════════════════════ 1. ЧИСТОЕ ЯДРО: ЧТО ГОВОРИТ СТРОКА ══════════════════════════════════

class TestVerdictContent(unittest.TestCase):
    """Правило 2 задания: строка называет коммит на диске, отличие от прежнего подъёма и
    клиентские файлы среди отличий — СЛОВОМ, а не намёком."""

    def test_client_files_named_by_word_deploy(self):
        v = dv.verdict(UB, D1, NEW, OLD, CLIENT + INNER, CLIENT)
        self.assertEqual(v.outcome, dv.NEW_CLIENT)
        self.assertIn(dv.DEPLOY_WORDS, v.line)
        self.assertIn("suggest.py", v.line)

    def test_line_names_both_commits(self):
        v = dv.verdict(UB, D1, NEW, OLD, CLIENT, CLIENT)
        self.assertIn(NEW[:dv.SHORT], v.line)
        self.assertIn(OLD[:dv.SHORT], v.line)

    def test_line_names_the_door(self):
        self.assertIn(D1, dv.verdict(UB, D1, NEW, OLD, CLIENT, CLIENT).line)
        self.assertIn(D2, dv.verdict(MB, D2, NEW, OLD, CLIENT, CLIENT).line)

    def test_line_names_the_child(self):
        self.assertIn(UB, dv.verdict(UB, D1, NEW, OLD, INNER, []).line)
        self.assertIn(MB, dv.verdict("moderbot", D2, NEW, OLD, INNER, []).line)

    def test_line_is_one_line(self):
        for args in ((UB, D1, NEW, OLD, CLIENT, CLIENT), (UB, D1, NEW, OLD, INNER, []),
                     (UB, D1, None, OLD, None, None), (UB, D1, NEW, None, None, None),
                     (UB, D1, NEW, OLD, None, None)):
            self.assertNotIn("\n", dv.verdict(*args).line)

    def test_line_fits_journal_line_max(self):
        """Потолок строки журнала — 600 символов (cowork_log_append.LINE_MAX); длинный дифф режем
        числом остатка, а не сносим ссылку на суть."""
        many = ["file%02d.py" % i for i in range(60)]
        v = dv.verdict(UB, D1, NEW, OLD, many, many)
        self.assertLess(len(v.line), 600)
        self.assertIn("и ещё", v.line)

    def test_counts_are_named(self):
        v = dv.verdict(UB, D1, NEW, OLD, CLIENT + INNER, CLIENT)
        self.assertIn("3 из 6", v.line)

    def test_failed_closure_says_fail_closed_in_words(self):
        v = dv.verdict(UB, D1, NEW, OLD, INNER, INNER, closure_ok=False)
        self.assertEqual(v.outcome, dv.NEW_CLIENT)
        self.assertIn("fail-closed", v.line)


# ═══════════════════════ 2. ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ИСХОДА (правило 6) ══════════════════════════

class TestNegativeOutcomes(unittest.TestCase):

    def test_same_commit_no_deploy_line(self):
        """Подъём на ТОМ ЖЕ коммите — строки о выкатке НЕТ вовсе (её написание было бы ложью)."""
        v = dv.verdict(UB, D1, NEW, NEW, [], [])
        self.assertEqual(v.outcome, dv.SAME)
        self.assertEqual(v.line, "")
        self.assertFalse(v.loud)
        self.assertNotIn(dv.DEPLOY_WORDS, v.note)      # даже в диагностике слова «выкатка … без ворот» нет

    def test_same_commit_different_hash_length(self):
        """7/9/40 символов одного коммита — ОДИН коммит: иначе каждый подъём звался бы выкаткой."""
        self.assertEqual(dv.verdict(UB, D1, NEW, NEW[:7], [], []).outcome, dv.SAME)
        self.assertEqual(dv.verdict(UB, D1, NEW[:7], NEW, [], []).outcome, dv.SAME)

    def test_new_commit_without_client_files_does_not_wake(self):
        v = dv.verdict(UB, D1, NEW, OLD, INNER, [])
        self.assertEqual(v.outcome, dv.NEW_INTERNAL)
        self.assertTrue(v.line)                        # строка ЕСТЬ
        self.assertFalse(v.loud)                       # человека НЕ будим
        self.assertNotIn(dv.DEPLOY_WORDS, v.line)

    def test_new_commit_with_client_files_wakes(self):
        v = dv.verdict(UB, D1, NEW, OLD, CLIENT + INNER, CLIENT)
        self.assertTrue(v.loud)

    def test_git_silent_says_unknown_not_silence(self):
        v = dv.verdict(UB, D1, None, OLD, None, None)
        self.assertEqual(v.outcome, dv.NO_COMMIT)
        self.assertIn(dv.UNKNOWN_WORDS, v.line)
        self.assertFalse(v.loud)

    def test_unknown_is_not_green(self):
        """Третий исход не превращается в «всё хорошо» — это сказано словами, а не подразумевается."""
        v = dv.verdict(UB, D1, None, OLD, None, None)
        self.assertIn("не «всё хорошо»", v.line)

    def test_diff_unknown_says_not_checked(self):
        v = dv.verdict(UB, D1, NEW, OLD, None, None)
        self.assertEqual(v.outcome, dv.NO_DIFF)
        self.assertIn("НЕ ПРОВЕРЕНЫ", v.line)
        self.assertFalse(v.loud)                       # доказательства нет → не будим (правило 4)

    def test_no_previous_raise_is_named(self):
        v = dv.verdict(UB, D1, NEW, None, None, None)
        self.assertEqual(v.outcome, dv.NO_PREV)
        self.assertIn("НЕ ЗАПИСАН", v.line)
        self.assertFalse(v.loud)

    def test_loud_only_on_client_files(self):
        """Ровно один исход из шести будит человека — правило 4 задания, замок от расползания."""
        cases = {
            dv.SAME: dv.verdict(UB, D1, NEW, NEW, [], []),
            dv.NEW_CLIENT: dv.verdict(UB, D1, NEW, OLD, CLIENT, CLIENT),
            dv.NEW_INTERNAL: dv.verdict(UB, D1, NEW, OLD, INNER, []),
            dv.NO_PREV: dv.verdict(UB, D1, NEW, None, None, None),
            dv.NO_DIFF: dv.verdict(UB, D1, NEW, OLD, None, None),
            dv.NO_COMMIT: dv.verdict(UB, D1, None, OLD, None, None),
        }
        # OFF — исход не ядра, а ручки отката: `verdict` его не рождает, он живёт в `announce`
        # (см. TestRollbackKnob). Сверка полноты идёт по остальным шести.
        self.assertEqual(sorted(cases) + [dv.OFF], sorted(set(dv.OUTCOMES) - {dv.OFF}) + [dv.OFF])
        for name, v in cases.items():
            self.assertEqual(v.outcome, name)
            self.assertEqual(v.loud, name == dv.NEW_CLIENT, name)

    def test_line_empty_only_on_same(self):
        """Молчание в журнале допустимо РОВНО в одном исходе — когда выкатки не было."""
        for name, v in ((dv.NEW_CLIENT, dv.verdict(UB, D1, NEW, OLD, CLIENT, CLIENT)),
                        (dv.NEW_INTERNAL, dv.verdict(UB, D1, NEW, OLD, INNER, [])),
                        (dv.NO_PREV, dv.verdict(UB, D1, NEW, None, None, None)),
                        (dv.NO_DIFF, dv.verdict(UB, D1, NEW, OLD, None, None)),
                        (dv.NO_COMMIT, dv.verdict(UB, D1, None, OLD, None, None))):
            self.assertTrue(v.line.strip(), name)


# ═══════════════════════ 3. ФОРМА ОТЛИЧИМА ОТ СУЩЕСТВУЮЩИХ (правило 3) ═══════════════════════

class TestWordsAreNew(unittest.TestCase):
    """Правило 3: новая форма отличается от штатной РАЗНЫМИ СЛОВАМИ, а не пунктуацией.
    Сверка идёт по ЖИВОМУ коду контура, а не по памяти о нём."""

    def test_mark_absent_from_stale_debt_line(self):
        """Штатная строка отставания («метка отведена назад … долг детей снова виден») — та самая,
        которой обход был неотличим от отставания. Ни одного общего опознавательного слова."""
        save = (o._last_child_commit, o._child_reconcile_rejected, o._is_ancestor)
        try:
            o._last_child_commit = None
            o._is_ancestor = lambda a, b, **k: True       # git в тесте не зовём
            line = o._adopt_live_child_base("f" * 40, lambda: (OLD, "ok", "userbot жив с t"))
        finally:
            (o._last_child_commit, o._child_reconcile_rejected, o._is_ancestor) = save
        self.assertIn("долг детей снова виден", line)     # штатная форма на месте, её не трогали
        self.assertNotIn(dv.MARK, line)
        self.assertNotIn(dv.DEPLOY_WORDS, line)
        self.assertNotIn("мимо ворот", line)

    def test_mark_absent_from_watchdog_raise_card(self):
        """Карточка сторожа говорит про ЖИЗНЬ процесса; про КОД в ней нет ни слова — и не должно
        появиться: это разные новости с разными адресами."""
        text = o.raise_card_text(UB, 1, 3, 120.0, [42], True, "detail", "процесс")
        self.assertNotIn(dv.MARK, text)
        self.assertNotIn(dv.DEPLOY_WORDS, text)

    def test_mark_absent_from_gate_refusal(self):
        """Отказ ворот («применение … ОСТАНОВЛЕНО») — про НЕсостоявшуюся выкатку. Наша строка про
        состоявшуюся, и спутать их нельзя."""
        import client_contour as cc
        text = cc.card_text([UB], NEW, CLIENT, subject="s", where="w")
        self.assertNotIn(dv.MARK, text)
        self.assertNotIn(dv.DEPLOY_WORDS, text)

    def test_mark_lives_only_in_named_files(self):
        """Слова новой формы не встречаются НИ В ОДНОМ другом модуле корня: их не с чем спутать
        грепом, и у формы ровно ОДИН владелец. Двери зовут её именем константы, а не копией
        текста — копия разошлась бы с оригиналом молча (класс «две правды одной строки»)."""
        owners = {"deploy_voice.py"}
        found = set()
        for name in sorted(os.listdir(REPO)):
            p = os.path.join(REPO, name)
            if not name.endswith(".py") or not os.path.isfile(p):
                continue
            try:
                with io.open(p, encoding="utf-8") as f:
                    src = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            if dv.MARK in src or dv.DEPLOY_WORDS in src:
                found.add(name)
        self.assertEqual(found, owners, "новая форма разъехалась по файлам: %s" % (found ^ owners))

    def test_forms_share_no_distinctive_word(self):
        """Не пунктуацией: множества значимых слов двух форм не пересекаются по опознавательным."""
        ours = set(dv.MARK.lower().split())
        theirs = set("живая база детей метка отведена назад долг детей снова виден".split())
        self.assertFalse(ours & theirs)


# ═══════════════════════ 4. ВСЯ ДОРОГА: announce ═════════════════════════════════════════════

class TestAnnounce(unittest.TestCase):
    """announce() с инъекциями: ни git, ни мозга, ни пушей. Память подъёмов — во временном файле."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dv_")
        self.state = os.path.join(self.tmp, "raise.json")
        self.journal, self.wakes, self.logs = [], [], []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, head, prev, changed, hits, closure_ok=True, kind=UB, door=D1):
        if prev:
            dv.remember(kind, prev, "прежняя дверь", self.state)
        return dv.announce(
            kind, door, state_path=self.state,
            journal=self.journal.append, wake=self.wakes.append, log_fn=self.logs.append,
            head_fn=lambda repo=None: head,
            diff_fn=lambda a, b, repo=None: changed,
            client_fn=lambda paths, repo=None: (hits, closure_ok))

    def test_client_deploy_writes_journal_and_wakes(self):
        v = self._run(NEW, OLD, CLIENT + INNER, CLIENT)
        self.assertEqual(v.outcome, dv.NEW_CLIENT)
        self.assertEqual(len(self.journal), 1)
        self.assertIn(dv.DEPLOY_WORDS, self.journal[0])
        self.assertEqual(len(self.wakes), 1)
        self.assertIn("suggest.py", self.wakes[0])

    def test_internal_deploy_writes_journal_and_does_not_wake(self):
        v = self._run(NEW, OLD, INNER, [])
        self.assertEqual(v.outcome, dv.NEW_INTERNAL)
        self.assertEqual(len(self.journal), 1)
        self.assertEqual(self.wakes, [])

    def test_same_commit_writes_nothing_to_journal(self):
        v = self._run(NEW, NEW, [], [])
        self.assertEqual(v.outcome, dv.SAME)
        self.assertEqual(self.journal, [])
        self.assertEqual(self.wakes, [])
        self.assertTrue(self.logs)                     # но в лог процесса диагностика ушла

    def test_git_silent_still_writes_journal(self):
        v = self._run(None, OLD, None, None)
        self.assertEqual(v.outcome, dv.NO_COMMIT)
        self.assertEqual(len(self.journal), 1)
        self.assertIn(dv.UNKNOWN_WORDS, self.journal[0])
        self.assertEqual(self.wakes, [])

    def test_memory_moves_to_new_commit(self):
        self._run(NEW, OLD, CLIENT, CLIENT)
        self.assertTrue(dv._same_commit(dv.read_prev(UB, self.state), NEW))

    def test_memory_not_erased_by_unknown_commit(self):
        """git промолчал — прежний ИЗВЕСТНЫЙ коммит остаётся: затирать его словом «неизвестно»
        значит терять единственную точку сравнения для следующего подъёма."""
        self._run(None, OLD, None, None)
        self.assertTrue(dv._same_commit(dv.read_prev(UB, self.state), OLD))

    def test_second_raise_on_same_commit_is_quiet(self):
        """Живая последовательность: выкатили → сказали; подняли снова на том же коде → молчим."""
        self._run(NEW, OLD, CLIENT, CLIENT)
        self.journal[:], self.wakes[:] = [], []
        v = dv.announce(UB, D1, state_path=self.state, journal=self.journal.append,
                        wake=self.wakes.append, log_fn=self.logs.append,
                        head_fn=lambda repo=None: NEW,
                        diff_fn=lambda a, b, repo=None: [],
                        client_fn=lambda p, repo=None: ([], True))
        self.assertEqual(v.outcome, dv.SAME)
        self.assertEqual(self.journal, [])

    def test_two_children_have_separate_memory(self):
        self._run(NEW, OLD, CLIENT, CLIENT, kind=UB)
        self.assertIsNone(dv.read_prev(MB, self.state))

    def test_moderbot_alias_shares_memory_with_moderation_bot(self):
        """Сторож зовёт ребёнка 'moderation_bot', подниматель — 'moderbot': память ОДНА, иначе
        каждый подъём читался бы как первый и выкатки не находились бы никогда."""
        dv.remember("moderbot", OLD, D2, self.state)
        self.assertTrue(dv._same_commit(dv.read_prev("moderation_bot", self.state), OLD))

    def test_diff_not_asked_when_commit_unchanged(self):
        """Дешевизна не косметика: подъём на том же коммите не тратит git на дифф."""
        asked = []
        dv.remember(UB, NEW, D1, self.state)
        dv.announce(UB, D1, state_path=self.state, journal=self.journal.append,
                    wake=self.wakes.append,
                    head_fn=lambda repo=None: NEW,
                    diff_fn=lambda a, b, repo=None: asked.append(1) or [],
                    client_fn=lambda p, repo=None: ([], True))
        self.assertEqual(asked, [])


# ═══════════════════════ 5. ГОЛОС НЕ РОНЯЕТ БОТА (правило 5) ═════════════════════════════════

class TestNeverBreaksTheRaise(unittest.TestCase):
    """Ни одна поломка голоса не имеет права стать исключением наружу: подъём уже состоялся."""

    def _boom(self, *a, **k):
        raise RuntimeError("бум")

    def test_journal_failure_does_not_raise(self):
        v = dv.announce(UB, D1, journal=self._boom, wake=lambda t: None,
                        head_fn=lambda repo=None: NEW, prev_fn=lambda k, p=None: OLD,
                        diff_fn=lambda a, b, repo=None: INNER,
                        client_fn=lambda p, repo=None: ([], True),
                        remember_fn=lambda *a, **k: True)
        self.assertEqual(v.outcome, dv.NEW_INTERNAL)

    def test_wake_failure_does_not_raise(self):
        v = dv.announce(UB, D1, journal=lambda s: None, wake=self._boom,
                        head_fn=lambda repo=None: NEW, prev_fn=lambda k, p=None: OLD,
                        diff_fn=lambda a, b, repo=None: CLIENT,
                        client_fn=lambda p, repo=None: (CLIENT, True),
                        remember_fn=lambda *a, **k: True)
        self.assertEqual(v.outcome, dv.NEW_CLIENT)

    def test_git_failure_does_not_raise(self):
        v = dv.announce(UB, D1, journal=lambda s: None, wake=lambda t: None,
                        head_fn=self._boom, prev_fn=lambda k, p=None: OLD,
                        remember_fn=lambda *a, **k: True)
        self.assertIn(v.outcome, dv.OUTCOMES)

    def test_client_probe_failure_does_not_raise(self):
        v = dv.announce(UB, D1, journal=lambda s: None, wake=lambda t: None,
                        head_fn=lambda repo=None: NEW, prev_fn=lambda k, p=None: OLD,
                        diff_fn=lambda a, b, repo=None: CLIENT, client_fn=self._boom,
                        remember_fn=lambda *a, **k: True)
        self.assertIn(v.outcome, dv.OUTCOMES)

    def test_memory_failure_does_not_raise(self):
        v = dv.announce(UB, D1, journal=lambda s: None, wake=lambda t: None,
                        head_fn=lambda repo=None: NEW, prev_fn=lambda k, p=None: OLD,
                        diff_fn=lambda a, b, repo=None: INNER,
                        client_fn=lambda p, repo=None: ([], True), remember_fn=self._boom)
        self.assertIn(v.outcome, dv.OUTCOMES)

    def test_log_failure_does_not_raise(self):
        v = dv.announce(UB, D1, journal=lambda s: None, wake=lambda t: None, log_fn=self._boom,
                        head_fn=lambda repo=None: NEW, prev_fn=lambda k, p=None: OLD,
                        diff_fn=lambda a, b, repo=None: INNER,
                        client_fn=lambda p, repo=None: ([], True), remember_fn=lambda *a, **k: True)
        self.assertEqual(v.outcome, dv.NEW_INTERNAL)

    def test_unreadable_memory_is_no_prev_not_crash(self):
        tmp = tempfile.mkdtemp(prefix="dv_bad_")
        try:
            p = os.path.join(tmp, "raise.json")
            with io.open(p, "w", encoding="utf-8") as f:
                f.write("{это не json")
            self.assertIsNone(dv.read_prev(UB, p))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_announce_returns_no_permission_value(self):
        """Голос не отдаёт «поднимать/не поднимать»: у Voice нет ни одного поля-разрешения —
        замок против превращения голоса в замок."""
        self.assertEqual(dv.Voice._fields, ("outcome", "line", "loud", "note"))


# ═══════════════════════ 5б. РУЧКА ОТКАТА ════════════════════════════════════════════════════

class TestRollbackKnob(unittest.TestCase):
    """Откат выключает РОВНО голос. Замок: ручка физически не способна тронуть подъём — всё, что
    умеет `announce`, это говорить, поэтому её единственное действие — молчание."""

    ON = {dv.OFF_ENV: "1"}

    def test_knob_silences_journal_and_wake(self):
        journal, wakes = [], []
        v = dv.announce(UB, D1, env=self.ON, journal=journal.append, wake=wakes.append,
                        head_fn=lambda repo=None: NEW, prev_fn=lambda k, p=None: OLD,
                        diff_fn=lambda a, b, repo=None: CLIENT,
                        client_fn=lambda p, repo=None: (CLIENT, True),
                        remember_fn=lambda *a, **k: True)
        self.assertEqual(v.outcome, dv.OFF)
        self.assertEqual(journal, [])
        self.assertEqual(wakes, [])

    def test_knob_still_speaks_to_the_process_log(self):
        """Выключенный голос обязан быть ВИДЕН читающему лог: иначе тишина неотличима от поломки."""
        logs = []
        dv.announce(UB, D1, env=self.ON, log_fn=logs.append,
                    head_fn=lambda repo=None: NEW, prev_fn=lambda k, p=None: OLD)
        self.assertEqual(len(logs), 1)
        self.assertIn(dv.OFF_ENV, logs[0])

    def test_knob_does_not_touch_git_or_memory(self):
        asked = []
        dv.announce(UB, D1, env=self.ON,
                    head_fn=lambda repo=None: asked.append("head") or NEW,
                    prev_fn=lambda k, p=None: asked.append("prev") or OLD,
                    remember_fn=lambda *a, **k: asked.append("remember"))
        self.assertEqual(asked, [])

    def test_knob_is_off_by_default(self):
        self.assertFalse(dv.is_off({}))
        self.assertFalse(dv.is_off({dv.OFF_ENV: ""}))
        self.assertFalse(dv.is_off({dv.OFF_ENV: "0"}))
        for on in ("1", "true", "YES", " On "):
            self.assertTrue(dv.is_off({dv.OFF_ENV: on}), on)

    def test_knob_cannot_reach_the_door(self):
        """Ни одна дверь не спрашивает голос о разрешении — значит выключить подъём ручкой нельзя
        по устройству, а не по обещанию. Сторожим текстом врезки в обеих дверях."""
        with io.open(os.path.join(REPO, "pc_orchestrator.py"), encoding="utf-8") as f:
            orch = f.read()
        with io.open(os.path.join(REPO, "pc_agent.py"), encoding="utf-8") as f:
            agent = f.read()
        self.assertNotIn(dv.OFF_ENV, orch)
        self.assertNotIn(dv.OFF_ENV, agent)


# ═══════════════════════ 6. ОБЕ ДВЕРИ ВРЕЗАНЫ (правило 2) ════════════════════════════════════

class TestBothDoorsWired(unittest.TestCase):

    def test_watchdog_calls_voice_after_raise(self):
        """Дверь B2: сторож поднял ребёнка → голос позван, и позван ПОСЛЕ подъёма."""
        order = []
        sp = {"name": UB, "finder": lambda: [], "logfile": os.path.join(REPO, "нет-такого.log"),
              "raiser": lambda: (order.append("raise") or (True, "d")), "skip": lambda: False}
        save = (o._cowork, o._notify, o._notify_topic, o._notify_critical, o._stopped,
                o.RAISE_VERIFY_SEC)
        o._cowork = o._notify = o._notify_critical = lambda *a, **k: None
        o._notify_topic = lambda *a, **k: None
        o._stopped = lambda: False
        o.RAISE_VERIFY_SEC = 0
        try:
            o.client_watchdog_tick(now=1, specs=[sp], state={}, cooldown=0, max_deaths=3,
                                   voice=lambda name: order.append("voice:" + name))
        finally:
            (o._cowork, o._notify, o._notify_topic, o._notify_critical, o._stopped,
             o.RAISE_VERIFY_SEC) = save
        self.assertEqual(order, ["raise", "voice:" + UB])

    def test_watchdog_silent_when_child_alive(self):
        """Ребёнок жив — подъёма не было, и говорить не о чем."""
        said = []
        sp = {"name": UB, "finder": lambda: [7], "logfile": "x", "raiser": lambda: (True, "d"),
              "skip": lambda: False}
        save = o._stopped
        o._stopped = lambda: False
        try:
            o.client_watchdog_tick(now=1, specs=[sp], state={}, cooldown=0, max_deaths=3,
                                   voice=said.append)
        finally:
            o._stopped = save
        self.assertEqual(said, [])

    def test_voice_skips_pc_agent(self):
        """pc_agent клиентским процессом не является; его подъём каскадом запускает дверь B1,
        которая скажет за userbot сама. Двух строк на одно событие быть не должно."""
        said = []
        save = dv.announce
        dv.announce = lambda *a, **k: said.append(a)
        try:
            o._child_deploy_voice("pc_agent")
            self.assertEqual(said, [])
            o._child_deploy_voice(UB)
            self.assertEqual(len(said), 1)
        finally:
            dv.announce = save

    def test_voice_failure_does_not_break_watchdog(self):
        """Даже если голос падает насмерть, подъём ребёнка состоялся и тик не рушится."""
        sp = {"name": UB, "finder": lambda: [], "logfile": os.path.join(REPO, "нет-такого.log"),
              "raiser": lambda: (True, "d"), "skip": lambda: False}
        save = (o._cowork, o._notify, o._notify_topic, o._notify_critical, o._stopped,
                o.RAISE_VERIFY_SEC, dv.announce)
        o._cowork = o._notify = o._notify_critical = lambda *a, **k: None
        o._notify_topic = lambda *a, **k: None
        o._stopped = lambda: False
        o.RAISE_VERIFY_SEC = 0

        def boom(*a, **k):
            raise RuntimeError("бум")
        dv.announce = boom
        try:
            out = o.client_watchdog_tick(now=1, specs=[sp], state={}, cooldown=0, max_deaths=3)
            self.assertEqual(out.get(UB), "raise")
        finally:
            (o._cowork, o._notify, o._notify_topic, o._notify_critical, o._stopped,
             o.RAISE_VERIFY_SEC, dv.announce) = save

    def test_agent_readoption_calls_voice_after_start(self):
        """Дверь B1: в живом коде pc_agent голос стои́т в ветке авто-реадопшна и ПОСЛЕ UB.start().
        Читаем исходник, а не память: врезка обязана быть доказана файлом."""
        with io.open(os.path.join(REPO, "pc_agent.py"), encoding="utf-8") as f:
            src = f.read()
        i_start = src.find("alog.info(UB.start())")
        i_voice = src.find("_announce_raise(\"userbot\", deploy_voice.DOOR_READOPT)")
        self.assertGreater(i_start, 0, "ветка авто-реадопшна не найдена")
        self.assertGreater(i_voice, i_start, "голос обязан стоять ПОСЛЕ подъёма")
        self.assertLess(i_voice - i_start, 900, "голос уехал из ветки авто-реадопшна")

    def test_agent_voice_helper_swallows_everything(self):
        """Помощник агента ловит BaseException: правило «ботов не ронять ни в одном исходе» не
        держится на чужой дисциплине."""
        with io.open(os.path.join(REPO, "pc_agent.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_announce_raise"]
        self.assertEqual(len(fn), 1)
        handlers = [h for n in ast.walk(fn[0]) if isinstance(n, ast.Try) for h in n.handlers]
        self.assertTrue(any(isinstance(h.type, ast.Name) and h.type.id == "BaseException"
                            for h in handlers))

    def test_neither_door_is_closed(self):
        """Задача добавляет ГОЛОС, а не замок: ни в одном месте врезки нет ветки, отменяющей
        подъём. Сторожим текстом живого кода — `voice` зовётся, но его ответ никто не читает."""
        with io.open(os.path.join(REPO, "pc_orchestrator.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("(voice or _child_deploy_voice)(name)", src)
        for bad in ("if (voice or _child_deploy_voice)", "= (voice or _child_deploy_voice)(name)",
                    "if _child_deploy_voice(", "return _child_deploy_voice("):
            self.assertNotIn(bad, src)


# ═══════════════════════ 7. КАРТОЧКА ВЛАДЕЛЬЦУ ═══════════════════════════════════════════════

class TestCardText(unittest.TestCase):

    def test_card_names_facts_owner_needs(self):
        t = dv.card_text(UB, D1, NEW, OLD, CLIENT, CLIENT + INNER)
        for must in (dv.DEPLOY_WORDS, UB, D1, NEW[:dv.SHORT], OLD[:dv.SHORT], "suggest.py",
                     "git show --stat"):
            self.assertIn(must, t)

    def test_card_says_it_is_not_a_question(self):
        """Карточка — НОВОСТЬ о свершившемся, а не вопрос: она прямо говорит, что ответа не ждёт
        и что дверь не закрыта (иначе владелец прочитает её как запрос решения)."""
        t = dv.card_text(UB, D1, NEW, OLD, CLIENT)
        self.assertIn("НЕ вопрос", t)
        self.assertIn("дверь не закрыта", t)

    def test_card_says_gate_did_not_see_the_commit(self):
        self.assertIn("Ворота этот коммит не смотрели", dv.card_text(UB, D1, NEW, OLD, CLIENT))

    def test_card_names_fail_closed_when_probe_blind(self):
        t = dv.card_text(UB, D1, NEW, OLD, CLIENT, CLIENT, closure_ok=False)
        self.assertIn("fail-closed", t)


# ═══════════════════════ 8. СЛОЙ ВВОДА-ВЫВОДА (без живого git) ═══════════════════════════════

class TestIOLayer(unittest.TestCase):

    def test_changed_between_distinguishes_empty_from_silence(self):
        """[] («отличий нет») и None («git не ответил») — РАЗНЫЕ ответы; путать их значит терять
        третий исход и звать неизвестность внутренним коммитом."""
        self.assertEqual(dv.changed_between(OLD, NEW, git=lambda *a, **k: ""), [])
        self.assertIsNone(dv.changed_between(OLD, NEW, git=lambda *a, **k: None))
        self.assertEqual(dv.changed_between(OLD, NEW, git=lambda *a, **k: "a.py\nb.py\n"),
                         ["a.py", "b.py"])

    def test_changed_between_without_base_is_unknown(self):
        self.assertIsNone(dv.changed_between(None, NEW, git=lambda *a, **k: "a.py"))

    def test_head_commit_silence_is_none(self):
        self.assertIsNone(dv.head_commit(git=lambda *a, **k: ""))
        self.assertIsNone(dv.head_commit(git=lambda *a, **k: None))

    def test_client_of_uses_the_same_probe_as_the_gate(self):
        """Признак клиентского — ТОТ ЖЕ, что у ворот: живое замыкание userbot/moderation_bot."""
        hits, ok = dv.client_of(["suggest.py", "test_deploy_voice.py"])
        self.assertTrue(ok)
        self.assertIn("suggest.py", hits)
        self.assertNotIn("test_deploy_voice.py", hits)

    def test_client_of_fail_closed_when_probe_blind(self):
        class Blind:
            ok = False
        hits, ok = dv.client_of(INNER, closure_fn=lambda repo=None: Blind())
        self.assertEqual(hits, INNER)
        self.assertFalse(ok)

    def test_state_file_is_under_gitignore_mask(self):
        """Память подъёмов — рабочее состояние машины, а не реестр решений: имя выбрано так, что
        уже закрыто маской `pc_orchestrator.*.json` в .gitignore и в коммит не поедет."""
        self.assertTrue(os.path.basename(dv.STATE_FILE).startswith("pc_orchestrator."))
        self.assertTrue(dv.STATE_FILE.endswith(".json"))
        with io.open(os.path.join(REPO, ".gitignore"), encoding="utf-8") as f:
            mask = f.read()
        self.assertIn("pc_orchestrator.*.json", mask)

    def test_state_file_is_isolated_under_test(self):
        """Тот же класс, что уже уничтожал спул ревизора и снимок надзора: тест зовёт живую
        функцию, та берёт путь по умолчанию — и БОЕВАЯ память подъёмов переписана фикстурой.
        Живой замер 03.09 при заведении: три тест-класса зовут `client_watchdog_tick` напрямую,
        и боевой файл успел записаться дважды за один прогон. Правило стои́т в константе."""
        self.assertNotEqual(dv.STATE_FILE,
                            os.path.join(REPO, "pc_orchestrator.child_raise_commit.json"),
                            "под тестом память подъёмов обязана уезжать в temp")

    def test_state_file_is_not_a_gate_registry(self):
        """Замок против расползания: наш блокнот не смеет оказаться реестром одобрений ворот."""
        import client_contour as cc
        self.assertNotEqual(os.path.normcase(dv.STATE_FILE), os.path.normcase(cc.RELEASE_FILE))
        self.assertNotEqual(os.path.normcase(dv.STATE_FILE),
                            os.path.normcase(cc.TRAINER_GREEN_FILE))

    def test_remember_refuses_unknown_commit(self):
        tmp = tempfile.mkdtemp(prefix="dv_rm_")
        try:
            p = os.path.join(tmp, "s.json")
            self.assertFalse(dv.remember(UB, None, D1, p))
            self.assertFalse(os.path.exists(p))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_remember_keeps_other_children(self):
        tmp = tempfile.mkdtemp(prefix="dv_rm2_")
        try:
            p = os.path.join(tmp, "s.json")
            dv.remember(UB, OLD, D1, p)
            dv.remember(MB, NEW, D2, p)
            with io.open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.assertEqual(sorted(d), [MB, UB])
            self.assertEqual(d[UB]["commit"], OLD)
            self.assertEqual(d[MB]["door"], D2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
