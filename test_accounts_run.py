# -*- coding: utf-8 -*-
u"""test_accounts_run.py — «учётки» и «учётка N» (25.09.2026, задание «учётки одним словом»).

Все пробы подменены: профиль ПК отвечает конвертом из словаря теста, сервер — готовым итогом
`use_slot`/`probe_all_slots`. Ни одного вызова `claude`, ни одного ssh. Каждый случай — свой
временный REPO (системный temp, префикс `uchetki_2509_`, не удаляется), боевой реестр не читается.

Держит отрицательный тест задания «учётка 9 — отказ словами»: реестр 1…3, слово с номером 9 не
поднимает НИ ОДНОЙ пробы, не зовёт сервер и не трогает реестр побайтно.
    D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_accounts_run -v
"""

import hashlib
import io
import json
import os
import re
import tempfile
import types
import unittest

import accounts_registry as accounts
import accounts_run as ar
import rc_supervisor as rc
import vps_token_install as vti

HERE = os.path.dirname(os.path.abspath(__file__))
_REAL_FIND_CLAUDE = ar.find_claude          # до подмены в setUp: паритет с RC судится по настоящей
ENV_OK = '{"is_error":false,"subtype":"success","api_error_status":null,"result":"OK","type":"result"}'
ENV_429 = ('{"is_error":true,"subtype":"success","api_error_status":429,'
           '"result":"You\'ve hit your weekly limit · resets Sep 27, 9am (UTC)","type":"result"}')
ENV_401 = ('{"is_error":true,"subtype":"success","api_error_status":401,'
           '"result":"Failed to authenticate. API Error: 401 OAuth access token is invalid.","type":"result"}')


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class _Runner(object):
    u"""Двойник `subprocess.run` пробы профиля: ответ выбирается по CLAUDE_CONFIG_DIR ребёнка."""

    def __init__(self, by_profile):
        self.by_profile, self.calls = by_profile, []

    def __call__(self, cmd, **kw):
        env = kw.get("env") or {}
        prof = env.get(accounts.KEY, accounts.WORD_MAIN)
        self.calls.append({"cmd": list(cmd), "profile": prof, "cwd": kw.get("cwd"), "env": env})
        out = self.by_profile.get(prof, ENV_OK)
        return types.SimpleNamespace(returncode=0 if out == ENV_OK else 1, stdout=out, stderr="")


class _Use(object):
    def __init__(self, ok=True, stage="done", code=200):
        self.ok, self.stage, self.code, self.calls = ok, stage, code, []

    def __call__(self, slot, force=False, work=None):
        self.calls.append((slot, force))
        return {"ok": self.ok, "stage": self.stage, "lines": [u"слово двери: этап %s" % self.stage],
                "row": {"code": self.code, "word": u"x", "reset": u""}}


class _Base(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="uchetki_2509_")
        self.p2 = os.path.join(self.repo, "claude_profile_2")
        self.p3 = os.path.join(self.repo, "claude_profile_3")
        os.mkdir(self.p2)
        os.mkdir(self.p3)
        self.reg_path = os.path.join(self.repo, accounts.REGISTRY_REL)
        self.addCleanup(setattr, ar, "find_claude", ar.find_claude)
        ar.find_claude = lambda *a, **k: "claude-fake"

    def registry(self, builders=1, slot3="A", slot1=""):
        data = {"form": 1, "builders": builders, "accounts": {
            "1": {"profile": u"ОСНОВНОЙ", "slot": slot1, "label": u"основная"},
            "2": {"profile": self.p2, "slot": "B", "label": u"вторая"},
            "3": {"profile": self.p3, "slot": slot3, "label": u"третья"}}}
        with io.open(self.reg_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))

    def builders(self):
        with io.open(self.reg_path, encoding="utf-8") as f:
            return json.load(f)["builders"]


class TestSwitch(_Base):
    def test_unknown_number_refused_in_words_and_touches_nothing(self):
        u"""«учётка 9» — ОТКАЗ СЛОВАМИ: проб 0, сервер не зван, реестр побайтно тот же."""
        self.registry()
        before = _sha(self.reg_path)
        run, use = _Runner({}), _Use()
        code, text = ar.switch("9", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual(code, 2)
        self.assertIn(u"«9» в реестре нет", text)
        self.assertIn(u"№1, №2, №3", text)
        self.assertIn(u"Ничего не изменено", text)
        self.assertEqual((run.calls, use.calls), ([], []))
        self.assertEqual(_sha(self.reg_path), before)

    def test_no_registry_refused(self):
        run, use = _Runner({}), _Use()
        code, text = ar.switch("3", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual(code, 2)
        self.assertIn(u"WARNING", text)
        self.assertEqual((run.calls, use.calls), ([], []))
        self.assertFalse(os.path.exists(self.reg_path))

    def test_green_both_lanes(self):
        self.registry(builders=1, slot3="A")
        run, use = _Runner({self.p3: ENV_OK}), _Use(ok=True)
        code, text = ar.switch("3", runner=run, use=use, repo=self.repo, save=True)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.builders(), 3)
        self.assertEqual(use.calls, [("A", False)])                 # --force из слова не идёт никогда
        self.assertEqual([c["profile"] for c in run.calls], [self.p3])
        self.assertIn(u"обе полосы на №3", text)
        self.assertIn(u"RC не тронут", text)
        last = accounts.load_last(self.repo)
        self.assertEqual(last["pc"]["3"]["code"], 200)
        self.assertEqual(last["srv_active"], "A")

    def test_pc_limit_keeps_builders_and_says_so(self):
        self.registry(builders=1)
        run, use = _Runner({self.p2: ENV_429}), _Use(ok=False, stage="probe", code=429)
        code, text = ar.switch("2", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.builders(), 1)
        self.assertIn(u"НЕ переключён", text)
        self.assertIn(u"сброс «Sep 27, 9am (UTC)»", text)
        self.assertIn(u"переключено НЕ всё", text)

    def test_no_slot_server_untouched(self):
        self.registry(builders=1, slot3="")
        run, use = _Runner({self.p3: ENV_OK}), _Use()
        code, text = ar.switch("3", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual(code, 0)
        self.assertEqual(use.calls, [])
        self.assertIn(u"слота нет", text)

    def test_switch_never_writes_rc_or_tree_choice(self):
        self.registry(builders=1)
        ar.switch("3", runner=_Runner({}), use=_Use(), repo=self.repo, save=False)
        self.assertFalse(os.path.exists(os.path.join(self.repo, rc.RC_CHOICE_REL)))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "claude_profile_choice.txt")))
        env, _why = rc.child_env(base={accounts.KEY: self.p2, "PATH": "x"}, repo=self.repo)
        self.assertNotIn(accounts.KEY, env)                       # RC остался на ОСНОВНОМ


class TestReport(_Base):
    def srv(self, busy=0):
        rows = [vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0)),
                vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1", ENV_OK, rc=0)),
                vti.slot_row("TB_CLAUDE_TOKEN_B", _out("0", ENV_429)),
                vti.slot_row("TB_CLAUDE_TOKEN_D", _out("0", ENV_401))]
        return lambda work=None: {"ok": True, "target": "/x", "words": "", "rows": rows, "busy": busy}

    def test_everything_named(self):
        self.registry(builders=3, slot3="A")
        run = _Runner({accounts.WORD_MAIN: ENV_429, self.p2: ENV_429, self.p3: ENV_OK})
        text = ar.report(runner=run, srv_probe=self.srv(busy=1), repo=self.repo)
        self.assertEqual(len(run.calls), 3)                         # по одной пробе на профиль
        self.assertIn(u"№1 «основная» · ПК ОСНОВНОЙ: 429 лимит, сброс «Sep 27, 9am (UTC)» · сервер слота нет", text)
        self.assertIn(u"№2 «вторая»", text)
        self.assertIn(u"сервер слот B: 429 лимит, сброс «Sep 27, 9am (UTC)»", text)
        self.assertIn(u"№3 «третья» · ПК %s: 200 жива · сервер слот A: 200 жива ✓действует" % self.p3, text)
        self.assertIn(u"слот D вне реестра: 401 вход не принят", text)
        self.assertIn(u"ДЕЙСТВУЕТ: строители ПК — №3 · RC — ОСНОВНОЙ", text)
        self.assertIn(u"сервер — слот A (№3)", text)
        self.assertIn(u"идёт заход", text)
        self.assertIn(u"при 429 сервер повторит ЗАДАЧУ один раз под слотом B (№2)", text)
        self.assertIn(u"учётку он НЕ переключает", text)
        self.assertIn(u"модели проб: ПК %s" % ar.PROBE_MODEL, text)
        last = accounts.load_last(self.repo)
        self.assertEqual((last["pc"]["1"]["code"], last["srv"]["B"]["reset"]), (429, u"Sep 27, 9am (UTC)"))

    def test_retry_words_without_second_slot(self):
        rows = [vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0)),
                vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1", ENV_OK, rc=0))]
        words = ar.retry_words({"ok": True, "rows": rows}, {"A": 3})
        self.assertEqual(len(words), 1)
        self.assertIn(u"слот B пуст или его нет", words[0])
        self.assertEqual(ar.retry_words({"ok": False}, {}), [])

    def test_without_registry_main_plus_warning(self):
        run = _Runner({accounts.WORD_MAIN: ENV_OK})
        text = ar.report(runner=run, srv_probe=self.srv(), repo=self.repo, save=False)
        self.assertIn(u"WARNING", text)
        self.assertIn(u"без реестра — строители идут так · ПК ОСНОВНОЙ: 200 жива", text)
        self.assertIn(u"«учётки заведи»", text)
        self.assertIn(u"строители ПК — ОСНОВНОЙ (WARNING", text)

    def test_server_silent_is_said(self):
        self.registry()
        text = ar.report(runner=_Runner({}), repo=self.repo, save=False,
                         srv_probe=lambda work=None: {"ok": False, "words": u"ssh до сервера не поднялся",
                                                      "rows": []})
        self.assertIn(u"сервер — не проверено — ssh до сервера не поднялся", text)


class TestInit(_Base):
    u"""«учётки заведи»: первый реестр по п.1 задания 25.09 и п.0 (строители по пробе профиля №3)."""

    def setUp(self):
        _Base.setUp(self)
        self.addCleanup(setattr, ar, "INIT_PROFILE_2", ar.INIT_PROFILE_2)
        self.addCleanup(setattr, ar, "INIT_PROFILE_3", ar.INIT_PROFILE_3)
        ar.INIT_PROFILE_2, ar.INIT_PROFILE_3 = self.p2, self.p3

    def srv(self, a_env, a_rc=1, ok=True):
        rows = [vti.slot_row(vti.NAME_ACTIVE, _out("1", a_env, rc=a_rc)),
                vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1", a_env, rc=a_rc)),
                vti.slot_row("TB_CLAUDE_TOKEN_B", _out("0", ENV_429))]
        return lambda work=None: {"ok": ok, "target": "/x", "words": u"ssh не поднялся", "rows": rows if ok else [],
                                  "busy": 0}

    def test_a_green_means_third_in_a_and_builders_on_third(self):
        run = _Runner({accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK})
        code, text = ar.init_registry(runner=run, srv_probe=self.srv(ENV_OK, a_rc=0), repo=self.repo,
                                      today=(2026, 9, 25))
        self.assertEqual(code, 0, text)
        reg = accounts.load(self.repo)
        acc = reg.data["accounts"]
        self.assertEqual((acc[1]["slot"], acc[2]["slot"], acc[3]["slot"]), ("", "B", "A"))
        self.assertEqual((acc[1]["profile"], acc[2]["profile"], acc[3]["profile"]),
                         (accounts.WORD_MAIN, self.p2, self.p3))
        self.assertEqual(reg.data["builders"], 3)
        self.assertEqual([c["profile"] for c in run.calls], [accounts.WORD_MAIN, self.p3])
        self.assertIn(u"строители на №3", text)
        self.assertIn(u"сервер НЕ трогает", text)
        self.assertIn(u"основная в лимите, 200 ей не принадлежит", text)
        for row in acc.values():
            self.assertNotIn("@", row["label"])

    def test_a_limit_with_third_alive_means_main_in_a(self):
        run = _Runner({accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK})
        code, text = ar.init_registry(runner=run, srv_probe=self.srv(ENV_429), repo=self.repo,
                                      today=(2026, 9, 25))
        self.assertEqual(code, 0, text)
        acc = accounts.load(self.repo).data["accounts"]
        self.assertEqual((acc[1]["slot"], acc[3]["slot"]), ("A", ""))
        self.assertIn(u"третья жива, 429 ей не принадлежит", text)

    def test_contradicting_probes_assign_no_slot(self):
        u"""Находка ревью 25.09: одна проба A не различает учёток. Посылка П2 не подтвердилась — слот НЕ
        назначается никому (прежде: A = 429 при третьей тоже в лимите → №1 ← A навсегда)."""
        cases = (
            (u"A=429, третья тоже 429", ENV_429, 1, {accounts.WORD_MAIN: ENV_429, self.p3: ENV_429}),
            (u"A=429, третья 401 на ПК", ENV_429, 1, {accounts.WORD_MAIN: ENV_429, self.p3: ENV_401}),
            (u"A=200, основная жива (сброс прошёл)", ENV_OK, 0, {accounts.WORD_MAIN: ENV_OK, self.p3: ENV_OK}),
            (u"A=200, обе в лимите", ENV_OK, 0, {accounts.WORD_MAIN: ENV_429, self.p3: ENV_429}),
            (u"A=401", ENV_401, 1, {accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK}),
        )
        for name, a_env, a_rc, pcs in cases:
            with self.subTest(name=name):
                repo = tempfile.mkdtemp(prefix="uchetki_2509_")
                code, text = ar.init_registry(runner=_Runner(pcs), srv_probe=self.srv(a_env, a_rc=a_rc),
                                              repo=repo, today=(2026, 9, 25))
                self.assertEqual(code, 0, text)
                acc = accounts.load(repo).data["accounts"]
                self.assertEqual((acc[1]["slot"], acc[2]["slot"], acc[3]["slot"]), ("", "B", ""))
                self.assertIn(u"НЕ назначены", text)

    def test_rule_expires_at_measured_main_reset(self):
        pcs = {accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK}
        for now in ((2026, 9, 28, 21, 0), (2026, 9, 29, 3, 0)):
            with self.subTest(now=now):
                repo = tempfile.mkdtemp(prefix="uchetki_2509_")
                code, text = ar.init_registry(runner=_Runner(pcs), srv_probe=self.srv(ENV_OK, a_rc=0),
                                              repo=repo, today=now)
                self.assertEqual(code, 0, text)
                acc = accounts.load(repo).data["accounts"]
                self.assertEqual((acc[1]["slot"], acc[3]["slot"]), ("", ""))
                self.assertIn(u"правило П2 истекло", text)
        repo = tempfile.mkdtemp(prefix="uchetki_2509_")               # близнец: минутой раньше — живо
        ar.init_registry(runner=_Runner(pcs), srv_probe=self.srv(ENV_OK, a_rc=0), repo=repo,
                         today=(2026, 9, 28, 20, 59))
        self.assertEqual(accounts.load(repo).data["accounts"][3]["slot"], "A")

    def test_inconclusive_probe_writes_nothing_and_retry_works(self):
        u"""Находка ревью 25.09: 529/таймаут прежде писали реестр БЕЗ слотов навсегда (повтор слова
        отказывал «уже есть»). Теперь — ничего не пишем, повтор после выздоровления проходит."""
        env_529 = '{"is_error":true,"api_error_status":529,"result":"Overloaded","type":"result"}'
        pcs = {accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK}
        code, text = ar.init_registry(runner=_Runner(pcs), srv_probe=self.srv(env_529), repo=self.repo,
                                      today=(2026, 9, 25))
        self.assertEqual(code, 2, text)
        self.assertIn(u"повтори «учётки заведи» позже", text)
        self.assertFalse(os.path.exists(self.reg_path))
        code, text = ar.init_registry(runner=_Runner(dict(pcs, **{accounts.WORD_MAIN: env_529})),
                                      srv_probe=self.srv(ENV_OK, a_rc=0), repo=self.repo, today=(2026, 9, 25))
        self.assertEqual(code, 2, text)                                 # основная на ПК не ответила по делу
        self.assertFalse(os.path.exists(self.reg_path))
        code, text = ar.init_registry(runner=_Runner(pcs), srv_probe=self.srv(ENV_OK, a_rc=0),
                                      repo=self.repo, today=(2026, 9, 25))
        self.assertEqual(code, 0, text)
        self.assertEqual(accounts.load(self.repo).data["accounts"][3]["slot"], "A")

    def test_existing_registry_is_never_overwritten(self):
        good = json.dumps({"builders": 1, "accounts": {
            "1": {"profile": u"ОСНОВНОЙ", "slot": "", "label": u"основная"}}}, ensure_ascii=False)
        for body in (good, u"{битый"):
            with self.subTest(body=body[:10]):
                repo = tempfile.mkdtemp(prefix="uchetki_2509_")
                path = os.path.join(repo, accounts.REGISTRY_REL)
                with io.open(path, "w", encoding="utf-8") as f:
                    f.write(body)
                before = _sha(path)
                run = _Runner({})
                code, text = ar.init_registry(runner=run, srv_probe=self.srv(ENV_OK, a_rc=0), repo=repo)
                self.assertEqual(code, 2)
                self.assertIn(u"не перезаписывает", text)
                self.assertEqual(_sha(path), before)
                self.assertEqual(run.calls, [])                          # ни одной пробы

    def test_server_silent_creates_nothing(self):
        code, text = ar.init_registry(runner=_Runner({}), srv_probe=self.srv(ENV_OK, ok=False),
                                      repo=self.repo)
        self.assertEqual(code, 2)
        self.assertIn(u"сервер не ответил", text)
        self.assertFalse(os.path.exists(self.reg_path))

    def test_init_never_writes_or_restarts_the_server(self):
        u"""Заведение сервер ТОЛЬКО пробует: ни записи файла окружения, ни рестарта, ни --use."""
        from unittest import mock
        boom = mock.Mock(side_effect=AssertionError("заведение тронуло сервер"))
        with mock.patch.object(vti, "use_slot", boom), mock.patch.object(vti, "restart_unit", boom), \
                mock.patch.object(vti, "apply_value", boom), mock.patch.object(vti, "run_remote_py", boom), \
                mock.patch.object(vti, "restore", boom), mock.patch.object(vti, "ssh_run", boom):
            code, text = ar.init_registry(runner=_Runner({self.p3: ENV_OK}),
                                          srv_probe=self.srv(ENV_OK, a_rc=0), repo=self.repo,
                                          today=(2026, 9, 25))
        self.assertEqual(code, 0, text)
        boom.assert_not_called()

    def test_init_then_switch_works_end_to_end(self):
        u"""Одним словом с телефона от пустого места: «учётки заведи», затем «учётка 2»."""
        ar.init_registry(runner=_Runner({accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK}),
                         srv_probe=self.srv(ENV_OK, a_rc=0), repo=self.repo, today=(2026, 9, 25))
        use = _Use(ok=True)
        code, text = ar.switch("2", runner=_Runner({self.p2: ENV_OK}), use=use, repo=self.repo, save=False)
        self.assertEqual(code, 0, text)
        self.assertEqual(use.calls, [("B", False)])
        self.assertEqual(accounts.load(self.repo).data["builders"], 2)


def _out(active, envelope, rc=1):
    return ("loaded=1\nslot_filled=1\nis_active=%s\nfile_mtime=100\ndaemon_start=200\n%s\nprobe_rc=%d\n"
            % (active, envelope, rc))


class TestReviewFixesRun(_Base):
    u"""Находки ревью 25.09 на ходах слова: спорный слот, откат строителей без строки ОСНОВНОГО, кривая
    история. Каждый случай прежде либо молчал, либо звал сервер, либо падал ПОСЛЕ переключения."""

    def _srv(self):
        rows = [vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0)),
                vti.slot_row("TB_CLAUDE_TOKEN_B", _out("1", ENV_OK, rc=0))]
        return lambda work=None: {"ok": True, "target": "/x", "words": "", "rows": rows, "busy": 0}

    def test_shared_slot_never_reaches_the_server(self):
        u"""№2 и №3 оба на B: слот снят у обоих — слово НЕ зовёт сервер и говорит «спорный», не «нет»."""
        self.registry(builders=1, slot3="B")
        run, use = _Runner({self.p3: ENV_OK}), _Use()
        code, text = ar.switch("3", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual(code, 1)
        self.assertEqual(use.calls, [])
        self.assertIn(u"спорный", text)
        self.assertIn(u"Демон не тронут", text)
        self.assertIn(u"переключено НЕ всё", text)
        self.assertNotIn(u"слота нет", text)
        rep = ar.report(runner=_Runner({}), srv_probe=self._srv(), repo=self.repo, save=False)
        self.assertEqual(rep.count(u"слот спорный"), 2)
        self.assertIn(u"слот B вне реестра", rep)                  # слот честно «ничей», а не чей-то
        self.assertIn(u"слот спорный", ar.show(self.repo))

    def test_twin_distinct_slot_goes_to_the_server(self):
        self.registry(builders=1, slot3="A")
        use = _Use()
        code, _text = ar.switch("3", runner=_Runner({self.p3: ENV_OK}), use=use, repo=self.repo, save=False)
        self.assertEqual((code, use.calls), (0, [("A", False)]))

    def test_fallback_without_main_row_probes_main(self):
        u"""Строители №3, каталога №3 нет, строки ОСНОВНОГО в реестре нет: меряется ОСНОВНОЙ — тем
        строители и идут. Без этой пробы отчёт молчал бы о единственной учётке, что работает."""
        data = {"form": 1, "builders": 3, "accounts": {
            "2": {"profile": self.p2, "slot": "B", "label": u"вторая"},
            "3": {"profile": os.path.join(self.repo, "нет_каталога"), "slot": "", "label": u"третья"}}}
        with io.open(self.reg_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
        run = _Runner({accounts.WORD_MAIN: ENV_429})
        text = ar.report(runner=run, srv_probe=self._srv(), repo=self.repo, save=False)
        self.assertIn(accounts.WORD_MAIN, [c["profile"] for c in run.calls])
        self.assertIn(u"откат строителей (строки в реестре нет) · ПК ОСНОВНОЙ: 429 лимит", text)
        self.assertIn(u"строители ПК — ОСНОВНОЙ (WARNING", text)

    def test_twin_main_row_present_is_not_probed_twice(self):
        self.registry(builders=1)
        run = _Runner({})
        ar.report(runner=run, srv_probe=self._srv(), repo=self.repo, save=False)
        self.assertEqual([c["profile"] for c in run.calls].count(accounts.WORD_MAIN), 1)

    def test_malformed_history_does_not_break_the_word(self):
        self.registry(builders=1, slot3="A")
        with io.open(os.path.join(self.repo, accounts.LAST_REL), "w", encoding="utf-8") as f:
            f.write(u'{"pc": null, "srv": ["мусор"]}')
        code, text = ar.switch("3", runner=_Runner({self.p3: ENV_OK}), use=_Use(), repo=self.repo, save=True)
        self.assertEqual(code, 0, text)
        last = accounts.load_last(self.repo)
        self.assertEqual((last["pc"]["3"]["code"], last["srv_active"]), (200, "A"))


class TestBridgeInWords(_Base):
    u"""Мост 25.09 в словах: «учётки» меряет и называет то, чем строители и RC идут НА САМОМ ДЕЛЕ."""

    def _srv(self):
        rows = [vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0)),
                vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1", ENV_OK, rc=0))]
        return lambda work=None: {"ok": True, "target": "/x", "words": "", "rows": rows, "busy": 0}

    def test_no_registry_legacy_profile_is_probed_and_named(self):
        with io.open(os.path.join(self.repo, "claude_profile_choice.txt"), "w", encoding="utf-8") as f:
            f.write(self.p3 + u"\n")
        run = _Runner({self.p3: ENV_OK})
        text = ar.report(runner=run, srv_probe=self._srv(), repo=self.repo, save=False)
        self.assertEqual([c["profile"] for c in run.calls], [self.p3])      # меряем профиль 3, не ОСНОВНОЙ
        self.assertIn(u"без реестра — строители идут так · ПК %s: 200 жива" % self.p3, text)
        self.assertIn(u"строители ПК — %s (WARNING: реестра нет — решает прежний файл" % self.p3, text)
        self.assertIn(u"RC — %s (своего файла нет — прежний файл claude_profile_choice.txt)" % self.p3, text)

    def test_switch_without_registry_points_to_the_word(self):
        code, text = ar.switch("3", runner=_Runner({}), use=_Use(), repo=self.repo, save=False)
        self.assertEqual(code, 2)
        self.assertIn(u"«учётки заведи»", text)
        with io.open(self.reg_path, "w", encoding="utf-8") as f:
            f.write(u"{битый")
        code, text = ar.switch("3", runner=_Runner({}), use=_Use(), repo=self.repo, save=False)
        self.assertEqual(code, 2)
        self.assertIn(u"не перезапишет", text)                           # битый реестр — слово не поможет


class TestBuilderGate(unittest.TestCase):
    u"""Из захода строителей (метки демона в окружении) слова «учётки», «учётка N», «заведи» не идут."""

    def test_builder_child_is_refused_before_any_probe(self):
        from unittest import mock
        boom = mock.Mock(side_effect=AssertionError("дверь поднялась из захода строителей"))
        with mock.patch.dict(os.environ, {"PRETOOL_MARKER_TOKEN": "run-token"}), \
                mock.patch.object(ar, "switch", boom), mock.patch.object(ar, "report", boom), \
                mock.patch.object(ar, "init_registry", boom), mock.patch.object(ar, "pc_probe", boom), \
                mock.patch.object(accounts, "set_builders", boom), mock.patch.object(accounts, "set_entry", boom):
            for argv in (["--switch", "3"], ["--report"], ["--init"], ["--builders", "2"],
                         ["--set", "4", "--profile", "ОСНОВНОЙ"]):
                with self.subTest(argv=argv):
                    self.assertEqual(ar.main(argv), 3)
        boom.assert_not_called()

    def test_show_and_screen_stay_allowed(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"GIT_SERIAL_PC_OWNER": "task#7"}), \
                mock.patch.object(ar, "show", lambda: u"реестр"):
            self.assertEqual(ar.main(["--show"]), 0)
            self.assertEqual(ar.main(["--screen"]), 0)


class TestProbe(_Base):
    def test_probe_env_and_argv(self):
        run = _Runner({})
        base = {"ANTHROPIC_API_KEY": "x", accounts.KEY: "old", "PATH": "p", "PRETOOL_NOPUSH": "1"}
        ar.pc_probe(accounts.WORD_MAIN, runner=run, base_env=base)
        ar.pc_probe(self.p2, runner=run, base_env=base)
        main, p2 = run.calls
        self.assertNotIn(accounts.KEY, main["env"])
        self.assertEqual(p2["env"][accounts.KEY], self.p2)
        for c in run.calls:
            self.assertNotIn("ANTHROPIC_API_KEY", c["env"])
            self.assertNotIn("PRETOOL_NOPUSH", c["env"])
            self.assertEqual(c["cwd"], tempfile.gettempdir())       # вне дерева: ни хуков, ни карточек
            self.assertIn("--no-session-persistence", c["cmd"])
            self.assertEqual(c["cmd"][c["cmd"].index("--setting-sources") + 1], "project")
            self.assertEqual(c["cmd"][c["cmd"].index("--model") + 1], ar.PROBE_MODEL)

    def test_missing_dir_is_not_probed(self):
        run = _Runner({})
        row = ar.pc_probe(os.path.join(self.repo, "нет"), runner=run)
        self.assertEqual((row["word"], run.calls), (u"нет каталога", []))

    def test_no_claude_is_unknown_not_green(self):
        ar.find_claude = lambda *a, **k: None
        row = ar.pc_probe(accounts.WORD_MAIN, runner=_Runner({}))
        self.assertEqual((row["word"], row["code"]), (ar.PC_UNKNOWN, None))

    def test_claude_found_like_rc_does(self):
        u"""Проба профиля ищет claude ТЕМ ЖЕ порядком, что RC: иначе на ПК без claude в PATH (задача
        Планировщика) «учётка N» отказывала бы в переключении ПК, пока строители работают."""
        shim = r"C:\u\.local\bin\claude.exe"
        ver = r"C:\u\AppData\Roaming\Claude\claude-code\2.1.217\claude.exe"
        prog = os.path.join(os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
                            "Programs", "claude", "claude.exe")
        cases = [
            (lambda n: "/usr/bin/claude", set(), lambda: ver),        # PATH побеждает
            (lambda n: None, {os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")}, lambda: ver),
            (lambda n: None, set(), lambda: ver),                      # новейшая версионная
            (lambda n: None, {prog}, lambda: None),                    # прочие схемы
            (lambda n: None, set(), lambda: None),                     # нигде
        ]
        for which, files, newest in cases:
            isf = files.__contains__
            self.assertEqual(_REAL_FIND_CLAUDE(which=which, isfile=isf, newest=newest),
                             rc.resolve_claude(which=which, isfile=isf, newest=newest))
        self.assertEqual(ar.claude_base_dirs(), rc.claude_base_dirs())
        self.assertIsNotNone(shim)

    def test_probe_model_is_executor_model(self):
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as f:
            m = re.search(r'^EXECUTOR_MODEL\s*=\s*"([^"]+)"', f.read(), re.M)
        self.assertEqual(ar.PROBE_MODEL, m.group(1))

    def test_rc_choice_name_is_one(self):
        self.assertEqual(ar.RC_CHOICE_REL, rc.RC_CHOICE_REL)

    def test_never_reads_credentials(self):
        u"""Файлы входа не открываются: ни `open` в коде, ни строки с `credentials` вне докстрингов."""
        import ast
        with io.open(os.path.join(HERE, "accounts_run.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        docs = set()
        for node in [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]:
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                docs.add(id(node.body[0].value))
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                self.assertNotIn("credentials", n.value.lower())
            if isinstance(n, ast.Call):
                self.assertNotEqual(getattr(n.func, "id", None), "open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
