# -*- coding: utf-8 -*-
u"""test_accounts_run.py — «учётки» и «учётка N» (25.09.2026, задание «учётки одним словом»).

Все пробы подменены: профиль ПК отвечает конвертом из словаря теста, сервер — готовым итогом
`use_slot`/`probe_all_slots`. Ни одного вызова `claude`, ни одного ssh. Каждый случай — свой
временный REPO (системный temp, префикс `uchetki_2509_`, не удаляется), боевой реестр не читается.

Держит отрицательный тест задания «учётка 9 — отказ словами»: реестр 1…3, слово с номером 9 не
поднимает НИ ОДНОЙ пробы, не зовёт сервер и не трогает реестр побайтно.
    D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_accounts_run -v
"""

import datetime
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
# «сейчас» живого замера 25.09: 16:28 UTC = 23:28 на ПК (UTC+7, пояс владельца). 25.09.2026 — пятница.
NOW = datetime.datetime(2026, 9, 25, 16, 28, tzinfo=datetime.timezone.utc)


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
        self.assertIn(u"Учётки №9 нет", text)
        self.assertIn(u"Есть: №1 основная, №2, №3.", text)      # «вторая»/«третья» к номеру ничего не добавляют
        self.assertIn(u"Ничего не изменено", text)
        self.assertEqual((run.calls, use.calls), ([], []))
        self.assertEqual(_sha(self.reg_path), before)

    def test_no_registry_refused(self):
        run, use = _Runner({}), _Use()
        code, text = ar.switch("3", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual(code, 2)
        self.assertIn(u"Реестра учёток ещё нет — сначала «учётки заведи»", text)
        self.assertIn(u"Ничего не изменено", text)
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
        self.assertIn(u"✅ ПК: строители перешли на неё (были на №1 основная).", text)
        self.assertIn(u"✅ Сервер: переключён и перезапущен, проверка — работает.", text)
        self.assertIn(u"Телефон не трогал", text)
        self.assertIn(u"Готово: ПК и сервер на №3.", text)
        self.assertTrue(text.startswith(u"🔁 Учётка №3 — готово ✅\n"), text)     # итог виден в шапке
        last = accounts.load_last(self.repo)
        self.assertEqual(last["pc"]["3"]["code"], 200)
        self.assertEqual(last["srv_active"], "A")

    def test_pc_limit_keeps_builders_and_says_so(self):
        self.registry(builders=1)
        run, use = _Runner({self.p2: ENV_429}), _Use(ok=False, stage="probe", code=429)
        code, text = ar.switch("2", runner=run, use=use, repo=self.repo, save=False, now=NOW, tz=7)
        self.assertEqual(code, 1)
        self.assertEqual(self.builders(), 1)
        self.assertIn(u"⏳ ПК: лимит до вс 27.09 16:00 — строители остались на №1 основная.", text)
        self.assertIn(u"Не всё: ПК остался на №1 основная", text)
        self.assertTrue(text.startswith(u"🔁 Учётка №2 — не всё ⚠️\n"), text)

    def test_no_slot_server_untouched(self):
        self.registry(builders=1, slot3="")
        run, use = _Runner({self.p3: ENV_OK}), _Use()
        code, text = ar.switch("3", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual(code, 0)
        self.assertEqual(use.calls, [])
        self.assertIn(u"▫️ Сервер: у неё входа нет — не трогал.", text)
        self.assertIn(u"Готово: ПК на №3 (входа на сервере у неё нет).", text)

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
        text = ar.report(runner=run, srv_probe=self.srv(busy=1), repo=self.repo, now=NOW, tz=7)
        self.assertEqual(len(run.calls), 3)                         # по одной пробе на профиль
        self.assertIn(u"👥 Учётки · 23:28", text)
        self.assertIn(u"№1 основная — ⏳ лимит до вс 27.09 16:00 · только ПК", text)
        self.assertIn(u"№2 — ⏳ лимит до вс 27.09 16:00 · ПК и сервер (вход B)", text)   # ПК и вход B сказали одно
        self.assertIn(u"№3 — ✅ работает · ПК и сервер (вход A)", text)                  # буква входа названа
        self.assertIn(u"Строители ПК — №3\nТелефон — №1 основная\nСервер — №3", text)   # RC-страховка: основной = №1
        self.assertIn(u"На сервере есть ещё вход D, ничей: ❌ не принят.", text)
        self.assertIn(u"На сервере сейчас идёт задача", text)
        self.assertIn(u"Если у сервера кончится лимит, подстраховки нет: вход B (№2) тоже в лимите.", text)
        for raw in (ar.PROBE_MODEL, u"UTC", u"429", u"200", u"401", self.p3, u"слот", u"ДЕЙСТВУЕТ"):
            self.assertNotIn(raw, text)
        last = accounts.load_last(self.repo)
        self.assertEqual((last["pc"]["1"]["code"], last["srv"]["B"]["reset"]), (429, u"Sep 27, 9am (UTC)"))

    def test_retry_line_only_when_it_applies(self):
        u"""Строка про серверный повтор — только когда он правда будет (действующий ровно A или B, второй
        заполнен), и честно про второй вход: он в лимите или не пускает — подстраховки нет."""
        a_row = vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1", ENV_OK, rc=0))
        empty_b = vti.slot_row("TB_CLAUDE_TOKEN_B", "loaded=1\nslot_filled=0\nis_active=0\n")
        ok_b = vti.slot_row("TB_CLAUDE_TOKEN_B", _out("0", ENV_OK, rc=0))
        accs = {2: {"label": u"farishka"}}
        self.assertEqual(ar.retry_line({"A": a_row}, ["A"], {}, {}), u"")                 # второго входа нет
        self.assertEqual(ar.retry_line({"A": a_row, "B": empty_b}, ["A"], {}, {}), u"")   # второй пуст
        self.assertEqual(ar.retry_line({"A": a_row, "B": ok_b}, [], {}, {}), u"")         # действующий ни A, ни B
        self.assertEqual(ar.retry_line({"A": a_row, "B": ok_b}, ["A"], {"B": 2}, accs),
                         u"Если у сервера кончится лимит, упавшую задачу он один раз повторит через вход B (№2 farishka).")
        self.assertEqual(ar.retry_line({"A": a_row, "B": vti.slot_row("TB_CLAUDE_TOKEN_B", _out("0", ENV_429))},
                                       ["A"], {}, {}),
                         u"Если у сервера кончится лимит, подстраховки нет: вход B тоже в лимите.")
        self.assertEqual(ar.retry_line({"A": a_row, "B": vti.slot_row("TB_CLAUDE_TOKEN_B", _out("0", ENV_401))},
                                       ["A"], {}, {}),
                         u"Если у сервера кончится лимит, подстраховки нет: вход B не работает.")
        active = vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0))
        for rows in ([active, a_row], [active, a_row, empty_b]):                         # и в самом ответе «учётки»
            text = render(srv={"ok": True, "rows": rows, "busy": 0})
            self.assertNotIn(u"Если у сервера", text)
            self.assertIn(u"Сейчас всё на №3 samfold", text)

    def test_without_registry_main_plus_warning(self):
        run = _Runner({accounts.WORD_MAIN: ENV_OK})
        text = ar.report(runner=run, srv_probe=self.srv(), repo=self.repo, save=False, now=NOW, tz=7)
        self.assertIn(u"⚠️ Реестра учёток нет — скажи «учётки заведи».", text)
        self.assertIn(u"Строители ПК и телефон — основной профиль: ✅ работает", text)   # то, чем идут на деле
        self.assertIn(u"Сервер — вход A: ✅ работает", text)
        self.assertNotIn(u"WARNING", text)

    def test_server_silent_is_said(self):
        self.registry()
        text = ar.report(runner=_Runner({}), repo=self.repo, save=False,
                         srv_probe=lambda work=None: {"ok": False, "words": u"ssh до сервера не поднялся",
                                                      "rows": []})
        self.assertIn(u"❔ Сервер не ответил — его входы не проверены.", text)
        self.assertIn(u"№3 — ✅ ПК: работает · ❔ сервер (вход A): не проверен", text)


class TestInit(_Base):
    u"""«учётки заведи»: первый реестр по п.1 задания 25.09 и п.0 (строители по пробе профиля №3)."""

    def setUp(self):
        _Base.setUp(self)
        self.addCleanup(setattr, ar, "INIT_PROFILE_2", ar.INIT_PROFILE_2)
        self.addCleanup(setattr, ar, "INIT_PROFILE_3", ar.INIT_PROFILE_3)
        ar.INIT_PROFILE_2, ar.INIT_PROFILE_3 = self.p2, self.p3

    def srv(self, a_env, a_rc=1, ok=True, b_env=ENV_OK, b_rc=0):
        # слот B по умолчанию жив — как профиль 2 у двойника (_Runner отдаёт 200 всем, кого не назвали):
        # пробы №2 не спорят. Спор профиля 2 и слота B — отдельные тесты (TestSecondAccountSplit)
        rows = [vti.slot_row(vti.NAME_ACTIVE, _out("1", a_env, rc=a_rc)),
                vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1", a_env, rc=a_rc)),
                vti.slot_row("TB_CLAUDE_TOKEN_B", _out("0", b_env, rc=b_rc))]
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
        self.assertEqual([c["profile"] for c in run.calls], [accounts.WORD_MAIN, self.p3, self.p2])
        self.assertIn(u"№2 — ПК и сервер (вход B)", text)
        self.assertIn(u"№3 — ПК и сервер (вход A)", text)
        self.assertIn(u"№1 основная — только ПК", text)
        self.assertEqual(accounts.load_last(self.repo)["pc"]["2"]["code"], 200)   # память знает и №2
        self.assertIn(u"Строители ПК — на №3.", text)
        self.assertIn(u"Сервер не трогал — он на №3. Перевести — «учётка N».", text)
        self.assertIn(u"Вход A на сервере — №3: он работает, а основная в лимите.", text)
        self.assertNotIn(u"accounts_registry.json", text)
        for row in acc.values():
            self.assertNotIn("@", row["label"])

    def test_a_limit_with_third_alive_means_main_in_a(self):
        run = _Runner({accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK})
        code, text = ar.init_registry(runner=run, srv_probe=self.srv(ENV_429), repo=self.repo,
                                      today=(2026, 9, 25))
        self.assertEqual(code, 0, text)
        acc = accounts.load(self.repo).data["accounts"]
        self.assertEqual((acc[1]["slot"], acc[3]["slot"]), ("A", ""))
        self.assertIn(u"Вход A на сервере — №1: он в лимите, как основная, а №3 работает.", text)

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
                self.assertIn(u"Чей вход A — не понять: ", text)
                self.assertIn(u". Не записал.", text)
                self.assertIn(ar.SCREEN_HINT, text)                      # как назначить — экран владельца
                self.assertNotIn(u"--set", text)                         # голое «--set» консоль отвергла бы

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
                self.assertIn(u"Чей вход A — теперь не понять", text)
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
        self.assertIn(u"Повтори «учётки заведи» позже", text)
        self.assertIn(u"вход A на сервере не ответил (Claude перегружен)", text)
        self.assertIn(u"Ничего не изменено", text)
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
                self.assertIn(u"⛔ Реестр уже есть", text)
                self.assertIn(u"Ничего не изменено", text)
                if body != good:
                    self.assertIn(u"заново заводить не буду", text)
                self.assertEqual(_sha(path), before)
                self.assertEqual(run.calls, [])                          # ни одной пробы

    def test_server_silent_creates_nothing(self):
        code, text = ar.init_registry(runner=_Runner({}), srv_probe=self.srv(ENV_OK, ok=False),
                                      repo=self.repo)
        self.assertEqual(code, 2)
        self.assertIn(u"Реестр не завёл: сервер не ответил", text)
        self.assertIn(u"Ничего не изменено", text)
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


class TestSecondAccountSplit(_Base):
    u"""Находка проверки 25.09: профиль 2 на ПК сегодня вошёл в ТУ ЖЕ учётку, что профиль 3, а слот B
    сервера — вторая учётка. «учётки заведи» писало №2 = профиль 2 + слот B вслепую, и «учётка 2»
    отвечала «обе полосы на №2», разведя полосы по РАЗНЫМ учёткам молча. Судить можно только по
    кодам: 200 на одной стороне и 429 на другой у одной учётки не бывает (посылка правила П2)."""

    def setUp(self):
        _Base.setUp(self)
        self.addCleanup(setattr, ar, "INIT_PROFILE_2", ar.INIT_PROFILE_2)
        self.addCleanup(setattr, ar, "INIT_PROFILE_3", ar.INIT_PROFILE_3)
        ar.INIT_PROFILE_2, ar.INIT_PROFILE_3 = self.p2, self.p3

    def init(self, pc2_env, b_env, b_rc, repo=None):
        pcs = {accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK, self.p2: pc2_env}
        return ar.init_registry(runner=_Runner(pcs), repo=repo or self.repo, today=(2026, 9, 25),
                                srv_probe=TestInit.srv(self, ENV_OK, a_rc=0, b_env=b_env, b_rc=b_rc))

    def test_disagreeing_second_gets_no_slot_and_switch_leaves_server_alone(self):
        for name, pc2_env, b_env, b_rc in ((u"ПК 200, B 429", ENV_OK, ENV_429, 1),
                                           (u"ПК 429, B 200", ENV_429, ENV_OK, 0)):
            with self.subTest(name=name):
                repo = tempfile.mkdtemp(prefix="uchetki_2509_")
                code, text = self.init(pc2_env, b_env, b_rc, repo=repo)
                self.assertEqual(code, 0, text)
                acc = accounts.load(repo).data["accounts"]
                self.assertEqual((acc[2]["profile"], acc[2]["slot"]), (self.p2, ""))
                self.assertEqual(acc[3]["slot"], "A")                    # соседние строки не задеты
                self.assertIn(u"Вход B за №2 не записал: на ПК №2 ", text)
                self.assertIn(u" — похоже, это разные учётки.\n" + ar.SCREEN_HINT, text)
                self.assertIn(u"№2 — только ПК", text)
                use = _Use(ok=True)
                code, text = ar.switch("2", runner=_Runner({self.p2: ENV_OK}), use=use, repo=repo, save=False)
                self.assertEqual(use.calls, [])                          # сервер под №2 НЕ переводится
                self.assertIn(u"Готово: ПК на №2 (входа на сервере у неё нет).", text)
                self.assertNotIn(u"ПК и сервер на №2", text)

    def test_twins_that_do_not_disagree_keep_slot_b(self):
        u"""Близнецы: коды не спорят (оба 200, оба 429, у ПК 401/нет каталога/529) — №2 ← B, как п.1
        задания. «Не спорят» не значит «одна учётка» — это сказано словами, а не подразумевается."""
        env_529 = '{"is_error":true,"api_error_status":529,"result":"Overloaded","type":"result"}'
        for name, pc2_env, b_env, b_rc in ((u"оба 200", ENV_OK, ENV_OK, 0),
                                           (u"оба 429", ENV_429, ENV_429, 1),
                                           (u"ПК 401", ENV_401, ENV_429, 1),
                                           (u"ПК 529", env_529, ENV_OK, 0)):
            with self.subTest(name=name):
                repo = tempfile.mkdtemp(prefix="uchetki_2509_")
                code, text = self.init(pc2_env, b_env, b_rc, repo=repo)
                self.assertEqual(code, 0, text)
                self.assertEqual(accounts.load(repo).data["accounts"][2]["slot"], "B")
                self.assertNotIn(u"разные учётки", text)
                self.assertIn(u"№2 — ПК и сервер (вход B)", text)
                # «не спорят» ≠ «одна учётка» — это сказано словами, а не подразумевается
                self.assertIn(u"Вход B записал за №2, как задумано; что на ПК и на сервере это одна учётка, пробы не доказывают.", text)

    def test_bad_second_login_is_named_even_without_dispute(self):
        u"""Находка ревью: «вход B записал за №2, как задумано» печаталось и при пустом/не пускающем B и при
        отсутствии профиля 2 — владелец не узнавал, что «учётка 2» и подстраховка сервера не сработают."""
        for name, pc2_env, b_env, b_rc, want in (
                (u"B не пускает", ENV_OK, ENV_401, 1,
                 u"⚠️ Но вход B сейчас не пускает — «учётка 2» сервер на него не переведёт, и подстраховки у сервера нет."),
                (u"ПК 401", ENV_401, ENV_OK, 0, u"⚠️ А на ПК у №2 вход не принят — «учётка 2» строителей не переведёт."),
                (u"ПК 529", ENV_529, ENV_OK, 0,
                 u"⚠️ А на ПК у №2 проверка без ответа — «учётка 2» строителей не переведёт.")):
            with self.subTest(name=name):
                repo = tempfile.mkdtemp(prefix="uchetki_2509_")
                code, text = self.init(pc2_env, b_env, b_rc, repo=repo)
                self.assertEqual(code, 0, text)
                self.assertIn(want, text)
        repo = tempfile.mkdtemp(prefix="uchetki_2509_")
        code, text = self.init(ENV_OK, ENV_OK, 0, repo=repo)                     # оба работают — лишних ⚠️ нет
        self.assertNotIn(u"⚠️ Но вход B", text)
        self.assertNotIn(u"⚠️ А на ПК у №2", text)

    def test_second_profile_probe_never_blocks_the_registry(self):
        u"""Проба профиля 2 в заведении — СВЕДЕНИЕ, а не условие: её 529 реестр не отменяет (иначе
        лишняя проба отняла бы у владельца «заведи» из-за учётки, которая заведению не нужна)."""
        env_529 = '{"is_error":true,"api_error_status":529,"result":"Overloaded","type":"result"}'
        code, text = self.init(env_529, ENV_429, 1)
        self.assertEqual(code, 0, text)
        self.assertTrue(os.path.exists(self.reg_path))

    def test_plan_is_pure_and_default_keeps_old_shape(self):
        u"""Без проб №2 (прежний вызов init_plan) — строка №2 = B, как было: правка ничего не меняет
        тем, кто её не зовёт."""
        a = {"code": 200}
        rows, _b, words, retry = ar.init_plan(a, {"code": 429}, {"code": 200}, (2026, 9, 25))
        self.assertFalse(retry)
        self.assertEqual(rows[2]["slot"], "B")
        self.assertFalse(any(u"№2" in w for w in words))
        rows, _b, words, _r = ar.init_plan(a, {"code": 429}, {"code": 200}, (2026, 9, 25),
                                           slot_b={"code": 429}, pc2={"code": 200})
        self.assertEqual(rows[2]["slot"], "")

    def test_disagree_verdict_table(self):
        yes = ((200, 429), (429, 200))
        no = ((200, 200), (429, 429), (200, 401), (401, 429), (None, 200), (429, None), (529, 200),
              (200, 529), (None, None))
        for pc, sv in yes:
            self.assertTrue(ar.probes_disagree({"code": pc}, {"code": sv}), (pc, sv))
        for pc, sv in no:
            self.assertFalse(ar.probes_disagree({"code": pc}, {"code": sv}), (pc, sv))
        self.assertFalse(ar.probes_disagree(None, {"code": 429}))
        self.assertFalse(ar.probes_disagree({"code": 200}, None))

    def test_report_flags_a_disagreeing_row_and_only_it(self):
        u"""«учётки» называет спор строки прямо: реестр уже есть (заведён до правки или руками) — владелец
        видит, что «учётка N» развела бы полосы. Близнец: строка без спора предупреждения не получает."""
        data = {"form": 1, "builders": 3, "accounts": {
            "1": {"profile": u"ОСНОВНОЙ", "slot": "", "label": u"основная"},
            "2": {"profile": self.p2, "slot": "B", "label": u"вторая"},
            "3": {"profile": self.p3, "slot": "A", "label": u"третья"}}}
        with io.open(self.reg_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
        srv = TestInit.srv(self, ENV_OK, a_rc=0, b_env=ENV_429, b_rc=1)
        text = ar.report(runner=_Runner({accounts.WORD_MAIN: ENV_429, self.p2: ENV_OK, self.p3: ENV_OK}),
                         srv_probe=srv, repo=self.repo, save=False)
        self.assertIn(u"⚠️ №2: на ПК работает, на сервере в лимите — похоже, это разные учётки. Поправь №2 на ПК "
                      u"(accounts_run.py --screen, шаг 3).", text)
        self.assertNotIn(u"⚠️ №3", text)                                 # A и профиль 3 оба 200
        text = ar.report(runner=_Runner({accounts.WORD_MAIN: ENV_429, self.p2: ENV_429, self.p3: ENV_OK}),
                         srv_probe=srv, repo=self.repo, save=False)
        self.assertNotIn(u"разные учётки", text)
        silent = lambda work=None: {"ok": False, "words": u"ssh не поднялся", "rows": []}
        text = ar.report(runner=_Runner({self.p2: ENV_OK}), srv_probe=silent, repo=self.repo, save=False)
        self.assertNotIn(u"разные учётки", text)                          # сервер молчит — судить не по чему


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
        self.assertIn(u"⚠️ Сервер: вход B записан и за другой учёткой — не трогал", text)
        self.assertIn(u"Не всё: ПК перешёл, сервер не тронут (вход спорный).", text)
        self.assertNotIn(u"у неё входа нет", text)               # «спорный» ≠ «входа нет»
        rep = ar.report(runner=_Runner({}), srv_probe=self._srv(), repo=self.repo, save=False)
        self.assertEqual(rep.count(u"· вход спорный"), 2)
        self.assertEqual(rep.count(u"⚠️ Вход сервера B записан сразу за №2 и №3 — «учётка 2» и «учётка 3» сервер "
                                   u"не тронут."), 1)                  # одна беда — одна строка на обе учётки
        self.assertIn(u"Сервер — вход B (спорный): ✅ работает", rep)   # вход честно «спорный», а не чей-то
        self.assertNotIn(u"В реестре: слот B", rep)                 # та же беда не повторена сырой строкой
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
        self.assertIn(u"Строители ПК и телефон — основной профиль (не в реестре): ⏳ лимит", text)
        self.assertIn(u"⚠️ Профиля №3 на ПК нет — строители пока на основном профиле; там лимит — строители стоят.",
                      text)
        self.assertIn(u"№3 — ❌ профиля нет на ПК · только ПК", text)

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
        self.assertIn(u"⚠️ Реестра учёток нет — скажи «учётки заведи».", text)
        self.assertIn(u"Строители ПК и телефон — профиль 3: ✅ работает", text)   # RC по прежнему файлу — тот же
        self.assertNotIn(self.p3, text)                                     # путь целиком наружу не идёт
        self.assertNotIn(u"claude_profile_choice.txt", text)

    def test_switch_without_registry_points_to_the_word(self):
        code, text = ar.switch("3", runner=_Runner({}), use=_Use(), repo=self.repo, save=False)
        self.assertEqual(code, 2)
        self.assertIn(u"«учётки заведи»", text)
        with io.open(self.reg_path, "w", encoding="utf-8") as f:
            f.write(u"{битый")
        code, text = ar.switch("3", runner=_Runner({}), use=_Use(), repo=self.repo, save=False)
        self.assertEqual(code, 2)
        self.assertIn(u"⛔ Реестр учёток не читается (файл испорчен) — переключить не могу.", text)
        self.assertIn(u"Ничего не изменено", text)


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


class TestCrashInWords(unittest.TestCase):
    u"""Находка ревью: упала дверь посреди слова — агент слал бы в тему трассу Python (stdout пуст, stderr —
    трасса). Теперь владельцу одна строка словами на stdout, трасса — только в stderr (агент кладёт её в журнал)."""

    def test_crash_is_one_line_in_words(self):
        import contextlib
        from unittest import mock
        boom = mock.Mock(side_effect=KeyError("x"))
        for argv, fn, want in ((["--report"], "report", u"❔ Слово «учётки» сорвалось на ПК — ничего не менял."),
                               (["--switch", "3"], "switch",
                                u"❔ Слово «учётка 3» сорвалось на ПК — что успело измениться, покажет «учётки»."),
                               (["--init"], "init_registry",
                                u"❔ Слово «учётки заведи» сорвалось на ПК — что успело измениться, покажет «учётки».")):
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                with mock.patch.object(ar, fn, boom), mock.patch.dict(os.environ, {}, clear=False), \
                        mock.patch.object(accounts, "builder_child", return_value=u""), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = ar.main(argv)
                self.assertEqual(code, 1)
                self.assertTrue(out.getvalue().startswith(want), out.getvalue())
                self.assertNotIn(u"Traceback", out.getvalue())                    # трасса — не в тему
                self.assertIn(u"Traceback", err.getvalue())                       # а в журнал агента
                self.assertIn(u"KeyError", err.getvalue())


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


# ═══════════════════ человеческие слова (25.09.2026, «красиво, кратко, правильно — везде») ═══════════════════
# Ниже — всё, что владелец читает с телефона: время сброса по часам ПК, статус словами, «учётки» живого
# замера 25.09 целиком, «учётка N» на КАЖДОМ этапе настоящего `vti.use_slot` (руки подменены: ни ssh,
# ни сервера), «учётки заведи». И замок: технических слов в этих ответах нет ни одного.

ENV_429_BKK = ENV_429.replace(u"resets Sep 27, 9am (UTC)", u"resets Sep 29, 4am (Asia/Bangkok)")
ENV_529 = '{"is_error":true,"api_error_status":529,"result":"Overloaded","type":"result"}'
FORBIDDEN = (u"CLAUDE_CONFIG_DIR", u"limit_slot", u"accounts_registry.json", u"claude-opus", u"UTC",
             u"✓действует", u"ДЕЙСТВУЕТ", u"WARNING", u"claude_profile_choice.txt", u"rc_profile_choice.txt",
             u"жива", u"TB_CLAUDE_TOKEN", u"CLAUDE_CODE_OAUTH_TOKEN")
_RAW_CODE_RE = re.compile(r"(?<![\d.:])(200|401|429|529)(?![\d.:])")


def pc_row(env, profile=accounts.WORD_MAIN):
    u"""Строка пробы профиля ПК ровно той формы, что даёт `pc_probe` (конверт — из словаря теста)."""
    run = lambda cmd, **kw: types.SimpleNamespace(returncode=0 if env == ENV_OK else 1, stdout=env, stderr="")
    return ar.pc_probe(profile, runner=run, claude="claude-fake", isdir=lambda p: True)


def live_reg(builders=3, slot1="", slot2="", slot3="A", extra=None):
    data = {"form": 1, "builders": builders, "accounts": {
        "1": {"profile": u"ОСНОВНОЙ", "slot": slot1, "label": u"mxfill"},
        "2": {"profile": u"D:\\claude_profile_2", "slot": slot2, "label": u"farishka"},
        "3": {"profile": u"D:\\claude_profile_3", "slot": slot3, "label": u"samfold"}}}
    data["accounts"].update(extra or {})
    return accounts.decode(json.dumps(data, ensure_ascii=False), "", u"реестр")


def live_pc(p1=ENV_429_BKK, p2=ENV_OK, p3=ENV_OK):
    return {1: pc_row(p1), 2: pc_row(p2), 3: pc_row(p3)}


def live_srv(ok=True, busy=0, a=ENV_OK, b=ENV_429, active=u"A", extra=()):
    rows = [vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0)),
            vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1" if active == u"A" else "0", a, rc=0 if a == ENV_OK else 1)),
            vti.slot_row("TB_CLAUDE_TOKEN_B", _out("1" if active == u"B" else "0", b, rc=0 if b == ENV_OK else 1))]
    rows += [vti.slot_row("TB_CLAUDE_TOKEN_%s" % x, _out("1" if active == x else "0", env,
                                                         rc=0 if env == ENV_OK else 1)) for x, env in extra]
    return {"ok": ok, "target": "/x", "words": u"ssh до сервера не поднялся", "rows": rows if ok else [],
            "busy": busy}


RC_P3 = ar.RcChoice(u"D:\\claude_profile_3", u"своего файла нет — прежний файл", False)
RC_MAIN = ar.RcChoice(accounts.WORD_MAIN, u"свой файл", False)


def render(reg=None, pc=None, srv=None, rc=RC_P3, choice=None):
    reg = reg or live_reg()
    choice = choice or accounts.builders_choice(reg, isdir=lambda p: True)
    return ar.report_text(reg, live_pc() if pc is None else pc, live_srv() if srv is None else srv, choice, rc,
                          now=NOW, tz=7)


LIVE_TEXT = u"""👥 Учётки · 23:28

№1 mxfill — ⏳ лимит до вт 29.09 04:00 · только ПК
№2 farishka — ✅ работает · только ПК
№3 samfold — ✅ работает · ПК и сервер (вход A)

Сейчас всё на №3 samfold: строители ПК, телефон, сервер.
На сервере есть ещё вход B, ничей: ⏳ лимит до вс 27.09 16:00.
Если у сервера кончится лимит, подстраховки нет: вход B тоже в лимите."""


# ── настоящий `vti.use_slot` на подменённых руках ──
_BAK = u"/etc/orch.env.bak-20260925-162800Z"
_NAMES = u"1:CLAUDE_CODE_OAUTH_TOKEN:108,2:TB_CLAUDE_TOKEN_A:108,3:TB_CLAUDE_TOKEN_B:108"
FACTS_FREE = {"host_ok": "1", "file_exists": "1", "names": _NAMES, "busy_claude": "0"}
FACTS_BUSY = dict(FACTS_FREE, busy_claude="1")
FACTS_BLIND = {"host_ok": "1", "file_exists": "1", "names": _NAMES}          # до счёта заходов не дошла
WRITE_OK = {"slot_filled": "1", "already_active": "0", "backup": _BAK, "backup_ok": "1", "done": "1"}
WRITE_SAME = {"slot_filled": "1", "already_active": "1", "done": "1"}


def probe_row(code, reset=u"", after=False):
    word = {200: vti.GREEN, 429: vti.LIMIT, 401: vti.DENIED}.get(code, vti.OTHER)
    return {"name": vti.NAME_ACTIVE, "active": "1", "filled": "1", "is_error": code != 200, "code": code,
            "word": word, "text": u"Overloaded" if code == 529 else u"", "called": True,
            "file_after_start": after, "reset": reset}


def drive_use_slot(slot="A", address=True, pre=(), marks=(), write=None, write_rc=0, probes=(), restores=(),
                   restarts=()):
    u"""НАСТОЯЩИЙ `vti.use_slot` с подменёнными руками → его итог (этап, строки, флаги).

    Подменены только двери наружу: адрес, разведка, отметка демона, программа на сервере (запись и возврат
    из копии), проба, ssh рестарта. Ход, этапы и СЛОВА строк — живой код `use_slot`, `restore`,
    `restart_unit`: так «учётка N» проверяется на тех строках, что придут на самом деле."""
    from unittest import mock
    pre, marks, probes, restores, restarts = map(list, (pre, marks, probes, restores, restarts))

    def preflight(target):
        ok, facts = pre.pop(0) if pre else (True, FACTS_FREE)
        return ok, dict(facts), (u"канал жив, файл окружения на месте" if ok else u"ssh до сервера не поднялся")

    def run_remote_py(program, timeout=None):
        if u"restored_ok" in program:                              # возврат из копии
            how = restores.pop(0) if restores else True
            if how is True:
                return 0, {"restored_ok": "1"}, u"restored_ok=1\n"
            if how == u"missing":
                return 6, {"err": "backup_missing"}, u"err=backup_missing\n"
            return 255, {}, u"ssh: connection reset"
        fields = dict(write or {})
        return write_rc, fields, u"".join(u"%s=%s\n" % kv for kv in sorted(fields.items())) or u"ssh: connection reset"

    def ssh_run(cmd, stdin_text=None, timeout=None):                # зовёт только restart_unit
        up = restarts.pop(0) if restarts else True
        return 0, (u"MainPID=4242\nActiveState=active\nSubState=running\nNRestarts=0\n" if up
                   else u"MainPID=0\nActiveState=failed\nSubState=failed\nNRestarts=3\n")

    where = (u"/etc/orch.env", u"адрес назван юнитом") if address else (u"", u"ssh до сервера не поднялся")
    with mock.patch.object(vti, "find_env_path", lambda: where), \
            mock.patch.object(vti, "preflight", preflight), \
            mock.patch.object(vti, "daemon_mark", lambda: marks.pop(0) if marks else u"100@1"), \
            mock.patch.object(vti, "run_remote_py", run_remote_py), \
            mock.patch.object(vti, "probe_one",
                              lambda target, name, work=None: probes.pop(0) if probes else probe_row(200)), \
            mock.patch.object(vti, "ssh_run", ssh_run):
        return vti.use_slot(slot, force=False, work=None, stamp="20260925-162800Z")


_LIM = dict(reset=u"Sep 27, 9am (UTC)")
_RESTART = u"❗ Нужна рука на сервере: systemctl restart %s." % vti.UNIT
_COPY = u"копии от 23:28 (.bak-20260925-162800Z)"               # копия этого хода — по часам ПК и хвосту имени
_BUSY_WORDS = u"Не всё: ПК перешёл, сервер не переключён (занят задачей)."
_BLIND_WORDS = u"Не всё: ПК перешёл, сервер не переключён (не проверил, свободен ли)."
_LATE_BLIND = (u"⏸ Сервер: после проверки не вышло убедиться, что он свободен, — не перезапускал, вернул как было; "
               u"повтори «учётка 3» позже.")
_CUT_UNKNOWN = u"❔ Сервер: связь оборвалась при записи — поменялся ли файл входов, неизвестно, а вернуть не вышло."
# (имя, как вести use_slot, что обязано стоять в ответе, код слова). Все случаи — «учётка 3» при ПК 200.
USE_CASES = (
    (u"done", dict(write=WRITE_OK), [u"✅ Сервер: переключён и перезапущен, проверка — работает.",
                                     u"Готово: ПК и сервер на №3 samfold."], 0),
    (u"already", dict(write=WRITE_SAME), [u"✅ Сервер: и так на ней, проверка — работает.",
                                          u"Готово: ПК и сервер на №3 samfold."], 0),
    (u"already_restarted", dict(write=WRITE_SAME, probes=[probe_row(200, after=True)]),
     [u"✅ Сервер: уже был записан на неё, перезапущен — работает."], 0),
    (u"already_restart_down", dict(write=WRITE_SAME, probes=[probe_row(200, after=True)], restarts=[False]),
     [u"❌ Сервер: уже был записан на неё, но после перезапуска не поднялся.", _RESTART,
      u"Не всё: ПК перешёл, сервер ждёт руки (см. ❗)."], 1),
    (u"already_limit", dict(write=WRITE_SAME, probes=[probe_row(429, **_LIM)]),
     [u"⏳ Сервер: и так на ней, но у неё лимит до вс 27.09 16:00.",
      u"Не всё: ПК перешёл, сервер на ней, но в лимите до вс 27.09 16:00."], 1),
    (u"already_limit_daemon_older", dict(write=WRITE_SAME, probes=[probe_row(429, after=True, **_LIM)]),
     [u"⚠️ Сервер пока на прежней учётке, но в файле входов уже она, а у неё лимит до вс 27.09 16:00 — "
      u"перезапуск сервера поднимет его на ней.",
      u"❗ До перезапуска сервера переведи его словом «учётка N» на рабочую учётку.",
      u"Не всё: ПК перешёл, сервер ждёт руки (см. ❗)."], 1),
    (u"already_denied_daemon_older", dict(write=WRITE_SAME, probes=[probe_row(401, after=True)]),
     [u"⚠️ Сервер пока на прежней учётке, но в файле входов уже она, а её вход не принят — перезапуск сервера "
      u"поднимет его на ней.", u"Не всё: ПК перешёл, сервер ждёт руки (см. ❗)."], 1),
    (u"already_denied", dict(write=WRITE_SAME, probes=[probe_row(401)]),
     [u"❌ Сервер: и так на ней, но её вход не принят.", u"Не всё: ПК перешёл, сервер на ней, но вход не принят."], 1),
    (u"already_busy", dict(write=WRITE_SAME, probes=[probe_row(200, after=True)], pre=[(True, FACTS_FREE),
                                                                                       (True, FACTS_BUSY)]),
     [u"⏸ Сервер занят задачей и пока на прежней учётке (новая уже записана). Повтори «учётка 3» позже.",
      _BUSY_WORDS], 1),
    (u"already_blind", dict(write=WRITE_SAME, probes=[probe_row(200, after=True)], pre=[(True, FACTS_FREE),
                                                                                        (True, FACTS_BLIND)]),
     [u"⏸ Сервер пока на прежней учётке (новая уже записана), а свободен ли он — не проверить. "
      u"Повтори «учётка 3» позже.", _BLIND_WORDS], 1),
    (u"busy", dict(pre=[(True, FACTS_BUSY)]),
     [u"⏸ Сервер: сейчас выполняет задачу — не переключал; повтори «учётка 3» позже.", _BUSY_WORDS], 1),
    (u"busy_blind", dict(pre=[(True, FACTS_BLIND)]),
     [u"⏸ Сервер: не вышло проверить, свободен ли он, — не переключал; повтори «учётка 3» позже.", _BLIND_WORDS], 1),
    (u"busy_late", dict(write=WRITE_OK, pre=[(True, FACTS_FREE), (True, FACTS_BUSY)]),
     [u"⏸ Сервер: пока проверял, он взял задачу — не перезапускал, вернул как было; повтори «учётка 3» позже.",
      _BUSY_WORDS], 1),
    (u"busy_late_blind", dict(write=WRITE_OK, pre=[(True, FACTS_FREE), (True, FACTS_BLIND)]),
     [_LATE_BLIND, _BLIND_WORDS], 1),
    (u"busy_late_recheck_failed", dict(write=WRITE_OK, pre=[(True, FACTS_FREE), (False, {})]),
     [_LATE_BLIND, _BLIND_WORDS], 1),
    (u"busy_late_kept", dict(write=WRITE_OK, pre=[(True, FACTS_FREE), (True, FACTS_BUSY)], restores=[False]),
     [u"⏸ Сервер: пока проверял, он взял задачу — не перезапускал; повтори «учётка 3» позже.",
      u"ℹ️ Новая учётка на сервере уже записана (проверка — работает) и включится при следующем перезапуске."], 1),
    (u"slot_empty", dict(pre=[(True, dict(FACTS_FREE, names=u"1:CLAUDE_CODE_OAUTH_TOKEN:108,2:TB_CLAUDE_TOKEN_A:0"))]),
     [u"❌ Сервер: вход A пустой — не переключал.", u"Не всё: ПК перешёл, сервер не переключён (вход пустой)."], 1),
    (u"slot_missing", dict(pre=[(True, dict(FACTS_FREE, names=u"1:CLAUDE_CODE_OAUTH_TOKEN:108"))]),
     [u"❌ Сервер: входа A на сервере нет — не переключал.", u"Не всё: ПК перешёл, сервер не переключён (входа нет)."],
     1),
    (u"slot_empty_on_write", dict(write={"slot_filled": "0", "err": "slot_empty"}),
     [u"❌ Сервер: вход A пустой — не переключал."], 1),
    (u"probe_limit", dict(write=WRITE_OK, probes=[probe_row(429, **_LIM)]),
     [u"⏳ Сервер: проверка — лимит до вс 27.09 16:00, вернул как было.",
      u"Не всё: ПК перешёл, сервер остался как был."], 1),
    (u"probe_denied", dict(write=WRITE_OK, probes=[probe_row(401)]),
     [u"❌ Сервер: проверка — вход не принят, вернул как было."], 1),
    (u"probe_silent", dict(write=WRITE_OK, probes=[probe_row(529)]),
     [u"❔ Сервер: проверка — нет ответа (Claude перегружен), вернул как было."], 1),
    (u"probe_restarted", dict(write=WRITE_OK, probes=[probe_row(429, **_LIM)], marks=[u"100@1", u"200@2"]),
     [u"⏳ Сервер: проверка — лимит до вс 27.09 16:00, вернул как было (сервер перезапущен на прежней учётке)."], 1),
    (u"probe_restart_down", dict(write=WRITE_OK, probes=[probe_row(429, **_LIM)], marks=[u"100@1", u"200@2"],
                                 restarts=[False]),
     [u"❌ Сервер лежит: у неё лимит до вс 27.09 16:00, прежнюю учётку вернул, но после перезапуска сервер не "
      u"поднялся.", _RESTART], 1),
    (u"probe_needs_restart", dict(write=WRITE_OK, probes=[probe_row(429, **_LIM)], marks=[u"100@1", u"200@2"],
                                  pre=[(True, FACTS_FREE), (True, FACTS_BUSY)]),
     [u"⚠️ Сервер: у неё лимит до вс 27.09 16:00 — вернул как было, но сервер мог успеть её подхватить.",
      u"❗ Когда закончится текущая задача, перезапусти сервер: systemctl restart %s." % vti.UNIT,
      u"Не всё: ПК перешёл, сервер ждёт руки (см. ❗)."], 1),
    (u"probe_needs_restart_blind", dict(write=WRITE_OK, probes=[probe_row(429, **_LIM)], marks=[u"100@1", u"200@2"],
                                        pre=[(True, FACTS_FREE), (True, FACTS_BLIND)]),
     [u"❗ Перезапусти сервер, когда он освободится (сейчас это не проверить): systemctl restart %s." % vti.UNIT],
     1),
    (u"probe_stuck", dict(write=WRITE_OK, probes=[probe_row(429, **_LIM)], restores=[False]),
     [u"❌ Сервер: у неё лимит до вс 27.09 16:00, а вернуть прежнюю учётку не вышло — в файле сервера остался её "
      u"вход.", u"❗ Вручную на сервере: вернуть файл входов из %s." % _COPY], 1),
    (u"write_mismatch", dict(write={"slot_filled": "1", "already_active": "0", "backup": _BAK,
                                    "err": "backup_mismatch"}),
     [u"❌ Сервер: записать не вышло — ничего не поменялось."], 1),
    (u"write_rolled", dict(write={"slot_filled": "1", "backup": _BAK, "done": "0"}),
     [u"❌ Сервер: записать не вышло — вернул как было."], 1),
    (u"write_stuck", dict(write={"slot_filled": "1", "backup": _BAK, "done": "0"}, restores=[False]),
     [u"❌ Сервер: записать не вышло, и вернуть как было — тоже.",
      u"❗ Вручную на сервере: вернуть файл входов из %s." % _COPY], 1),
    (u"write_cut_rolled", dict(write={}, write_rc=255),
     [u"❔ Сервер: связь оборвалась при записи — вернул как было."], 1),
    (u"write_cut_untouched", dict(write={}, write_rc=255, restores=[u"missing"]),
     [u"❔ Сервер: связь оборвалась до записи — ничего не поменялось."], 1),
    (u"write_cut_unknown", dict(write={}, write_rc=255, restores=[False]),
     [_CUT_UNKNOWN, u"❗ Проверь «учётки»: если сервер не на прежней учётке — вручную на сервере вернуть файл "
                    u"входов из самой свежей копии (около 23:28)."], 1),
    (u"write_cut_after_copy_rolled", dict(write={"slot_filled": "1", "backup": _BAK}, write_rc=255),
     [u"❔ Сервер: связь оборвалась при записи — вернул как было."], 1),
    (u"write_cut_after_copy_stuck", dict(write={"slot_filled": "1", "backup": _BAK}, write_rc=255, restores=[False]),
     [_CUT_UNKNOWN, u"вернуть файл входов из %s." % _COPY], 1),
    (u"write_no_backup", dict(write={"slot_filled": "1", "done": "0"}),
     [u"❌ Сервер: записать не вышло — ничего не поменялось."], 1),
    (u"restart_back", dict(write=WRITE_OK, restarts=[False, True]),
     [u"❌ Сервер: после перезапуска не поднялся — вернул прежнюю учётку и поднял обратно."], 1),
    (u"restart_down", dict(write=WRITE_OK, restarts=[False, False]),
     [u"❌ Сервер: после перезапуска не поднялся — вернул прежнюю учётку, но поднять не вышло.", _RESTART], 1),
    (u"restart_kept_up", dict(write=WRITE_OK, restarts=[False, True], restores=[False]),
     [u"⚠️ Сервер не поднялся с первого раза, со второго поднялся; вернулась ли прежняя учётка — не подтвердилось.",
      u"❗ Проверь «учётки»: на какой учётке сервер.",
      u"Не всё: ПК перешёл, сервер поднялся, а на какой учётке — покажет «учётки»."], 1),
    (u"restart_kept_down", dict(write=WRITE_OK, restarts=[False, False], restores=[False]),
     [u"❌ Сервер: после перезапуска не поднялся, и вернуть прежнюю учётку не вышло.",
      u"❗ Вручную на сервере: вернуть файл входов из %s и перезапустить: systemctl restart %s." % (_COPY, vti.UNIT)],
     1),
    (u"address", dict(address=False), [u"❔ Сервер: не достучался — ничего не менял.",
                                       u"Не всё: ПК перешёл, сервер не тронут (не достучался)."], 1),
    (u"preflight", dict(pre=[(False, {})]), [u"❔ Сервер: не достучался — ничего не менял."], 1),
    (u"slot_name", dict(slot=u"1"), [u"❔ Сервер: не достучался — ничего не менял."], 1),
)


class TestHumanWords(_Base):
    u"""Просьба владельца 25.09: «учётки», «учётка N», «учётки заведи» — чётко, красиво, по-человечески,
    кратко и правильно; не компьютерный список."""

    # ── время сброса: слова поставщика → часы ПК ──
    def test_reset_words_to_pc_local_time(self):
        cases = ((u"Sep 27, 9am (UTC)", u"вс 27.09 16:00"),
                 (u"Sep 29, 4am (Asia/Bangkok)", u"вт 29.09 04:00"),
                 (u"Sep 27, 9:30am (UTC)", u"вс 27.09 16:30"),
                 (u"4am (Asia/Bangkok)", u"04:00 завтра"),                     # без даты — ближайшее после «сейчас»
                 (u"9pm (UTC)", u"04:00 завтра"),
                 (u"limit reached|1790499600", u"вс 27.09 16:00"),             # эпоха
                 (u"27.09 09:00 UTC", u"вс 27.09 16:00"),                      # эпоха после vti.reset_of
                 (u"Sep 27, 12pm (UTC)", u"вс 27.09 19:00"),
                 (u"Sep 27 at 9am UTC", u"вс 27.09 16:00"),
                 (u"2026-09-27T09:00:00Z", u"вс 27.09 16:00"))
        for said, want in cases:
            with self.subTest(said=said):
                self.assertEqual(ar.reset_words(said, now=NOW, tz=7), want)

    def test_today_tomorrow_and_year(self):
        morning = datetime.datetime(2026, 9, 25, 3, 0, tzinfo=datetime.timezone.utc)      # 10:00 на ПК
        self.assertEqual(ar.reset_words(u"Sep 25, 9am (UTC)", now=morning, tz=7), u"16:00 сегодня")
        self.assertEqual(ar.reset_words(u"Sep 25, 9pm (UTC)", now=morning, tz=7), u"04:00 завтра")
        self.assertEqual(ar.reset_words(u"4pm (Asia/Bangkok)", now=morning, tz=7), u"16:00 сегодня")
        new_year = datetime.datetime(2026, 12, 30, 10, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(ar.reset_words(u"Jan 2, 9am (UTC)", now=new_year, tz=7), u"сб 02.01.2027 16:00")
        self.assertEqual(ar.reset_words(u"Sep 20, 9am (UTC)", now=NOW, tz=7), u"вс 20.09 16:00")   # 5 дней назад — тот же год
        self.assertEqual(ar.reset_words(u"limit reached|1758963600", now=NOW, tz=7), u"сб 27.09.2025 16:00")
        for tz in (7, datetime.timedelta(hours=7), datetime.timezone(datetime.timedelta(hours=7))):
            self.assertEqual(ar.reset_words(u"Sep 27, 9am (UTC)", now=NOW, tz=tz), u"вс 27.09 16:00")
        # находка ревью: сразу после Нового года «Dec 31» — это вчера, а не через год
        after_ny = datetime.datetime(2027, 1, 1, 1, 0, tzinfo=datetime.timezone.utc)      # 08:00 1 января на ПК
        self.assertEqual(ar.reset_words(u"Dec 31, 11pm (UTC)", now=after_ny, tz=7), u"06:00 сегодня")
        self.assertEqual(ar.reset_words(u"Dec 30, 9am (UTC)", now=after_ny, tz=7), u"ср 30.12.2026 16:00")
        self.assertEqual(ar.reset_words(u"Mar 28, 9am (UTC)", now=NOW, tz=7), u"вс 28.03.2027 16:00")  # 181 день назад

    def test_unknown_zone_or_form_kept_in_provider_words(self):
        for said in (u"Sep 27, 9am (Mars/Olympus)", u"next Tuesday", u"Sep 27, 9 (UTC)", u"Feb 30, 9am (UTC)",
                     u"Sep 27, 13pm (UTC)", u"2026-09-27T09:00:00", u"Feb 29, 9am (UTC)"):
            with self.subTest(said=said):
                self.assertIsNone(ar.reset_moment(said, NOW))
                self.assertEqual(ar.reset_words(said, now=NOW, tz=7), u"«%s»" % said)
        self.assertEqual(ar.reset_words(u"", now=NOW, tz=7), u"")

    def test_time_helper_never_throws(self):
        u"""Находка ревью: момент, который часы ПК не переводят (год 9999, до 1970), не роняет ответ — остаются
        слова поставщика. Проверено и поясом по умолчанию (так зовёт прод)."""
        for said in (u"9999-12-31T23:59:00-12:00", u"0001-01-01T00:00:00+14:00", u"1969-12-31T23:00:00Z",
                     u"limit reached|99999999999"):
            with self.subTest(said=said):
                self.assertIsNone(ar.reset_moment(said, NOW))
                self.assertEqual(ar.reset_words(said, now=NOW), u"«%s»" % said)
                self.assertEqual(ar.reset_words(said, now=NOW, tz=7), u"«%s»" % said)
        from unittest import mock                  # второй рубеж: момент разобран, а перевод в часы ПК падает
        edge = datetime.datetime(9999, 12, 31, 23, 59, tzinfo=datetime.timezone.utc)
        with mock.patch.object(ar, "reset_moment", return_value=edge):
            self.assertEqual(ar.reset_words(u"Dec 31, 11:59pm (UTC)", now=NOW, tz=7), u"«Dec 31, 11:59pm (UTC)»")

    def test_fixed_zone_map_when_zoneinfo_is_missing(self):
        self.addCleanup(setattr, ar, "_zoneinfo", ar._zoneinfo)
        ar._zoneinfo = None
        self.assertEqual(ar.reset_words(u"Sep 29, 4am (Asia/Bangkok)", now=NOW, tz=7), u"вт 29.09 04:00")
        self.assertEqual(ar.reset_words(u"Sep 27, 9am (UTC)", now=NOW, tz=7), u"вс 27.09 16:00")
        self.assertEqual(ar.reset_words(u"Sep 27, 9am (Europe/Berlin)", now=NOW, tz=7),
                         u"«Sep 27, 9am (Europe/Berlin)»")                        # летнее время — не угадываем

    def test_zone_with_summer_time_goes_through_zoneinfo(self):
        u"""Находка ревью: путь через базу поясов не был проверен ни разу (UTC и Бангкок есть и в запасной карте)."""
        try:
            import zoneinfo
            zoneinfo.ZoneInfo("Europe/Berlin")
        except Exception:                                                 # noqa: BLE001
            self.skipTest("база поясов не загрузилась")
        self.assertEqual(ar.reset_words(u"Sep 27, 9am (Europe/Berlin)", now=NOW, tz=7), u"вс 27.09 14:00")  # CEST +2
        self.assertEqual(ar.reset_words(u"Dec 27, 9am (Europe/Berlin)", now=NOW, tz=7), u"вс 27.12 15:00")  # CET +1

    def test_default_zone_is_the_pc_local_zone(self):
        u"""Находка ревью: прод зовёт без tz — время обязано идти по МЕСТНЫМ часам ПК, а не по UTC (иначе
        владелец в UTC+7 видит сброс на 7 часов раньше). Ожидание считает сам Python, мимо accounts_run."""
        self.assertEqual(ar.clock_words(NOW), NOW.astimezone().strftime("%H:%M"))
        moment = datetime.datetime(2026, 9, 27, 9, 0, tzinfo=datetime.timezone.utc)
        loc = moment.astimezone()
        self.assertEqual(ar.reset_words(u"Sep 27, 9am (UTC)", now=NOW),
                         ar.when_words(moment, now=NOW, tz=loc.utcoffset()))
        self.assertIn(u"%02d:%02d" % (loc.hour, loc.minute), ar.reset_words(u"Sep 27, 9am (UTC)", now=NOW))
        reg = live_reg()
        choice = accounts.builders_choice(reg, isdir=lambda p: True)
        self.assertEqual(ar.report_text(reg, live_pc(), live_srv(), choice, RC_P3, now=NOW),
                         ar.report_text(reg, live_pc(), live_srv(), choice, RC_P3, now=NOW,
                                        tz=NOW.astimezone().utcoffset()))

    # ── статус и имена ──
    def test_status_words(self):
        rows = ((pc_row(ENV_OK), u"✅ работает"),
                (pc_row(ENV_429), u"⏳ лимит до вс 27.09 16:00"),
                ({"code": 429, "word": vti.LIMIT, "reset": u""}, u"⏳ лимит"),
                (pc_row(ENV_401), u"❌ вход не принят"),
                (ar.pc_probe(os.path.join(self.repo, u"нет"), runner=_Runner({})), u"❌ профиля нет на ПК"),
                ({"code": None, "word": u"пуст"}, u"❌ вход пустой"),
                (pc_row(ENV_529), u"❔ нет ответа"),
                ({"code": None, "word": ar.PC_UNKNOWN, "text": u"claude не найден на ПК — пробы не было"},
                 u"❔ нет ответа"),
                (None, u"❔ не проверено"))
        for row, want in rows:
            with self.subTest(want=want):
                self.assertEqual(ar.status_words(row, now=NOW, tz=7), want)
        self.assertEqual(ar.account_name(3, {"label": u"samfold"}), u"№3 samfold")
        self.assertEqual(ar.account_name(4, {"label": u"учётка 4"}), u"№4")      # метка по умолчанию — просто номер
        self.assertEqual(ar.account_name(3, {"label": u"третья"}), u"№3")        # «№3 третья» — заикание
        self.assertEqual(ar.account_name(4, {"label": u"Четвёртая"}), u"№4")
        self.assertEqual(ar.account_name(1, {"label": u"основная"}), u"№1 основная")   # смысл — показываем
        self.assertEqual(ar.account_name(2, {"label": u"третья"}), u"№2 третья")       # чужой порядковый — это метка
        self.assertEqual(ar.profile_words(u"D:\\claude_profile_5"), u"профиль 5")
        self.assertEqual(ar.profile_words(u"D:\\work\\prof\\"), u"профиль prof")
        self.assertEqual(ar.profile_words(accounts.WORD_MAIN, prep=True), u"основном профиле")
        self.assertEqual(ar.silent_why({"code": None, "text": u"claude не ответил за 240 с"}),
                         u"Claude не ответил за 4 мин")
        self.assertEqual(ar.silent_why({"code": None, "text": u"claude не запустился (FileNotFoundError)"}),
                         u"Claude не запустился")                                  # имя исключения наружу не идёт
        self.assertEqual(ar.silent_why({"code": 529}), u"Claude перегружен")

    # ── «учётки» ──
    def test_live_report_reads_as_designed(self):
        self.assertEqual(render(), LIVE_TEXT)

    def test_no_registry(self):
        reg = accounts.decode(None, "", u"реестр")
        choice = accounts.Choice(accounts.ACT_SET, u"D:\\claude_profile_3", u"реестра нет — прежний файл", True, None)
        pc = {"main": dict(pc_row(ENV_OK), profile=u"D:\\claude_profile_3")}
        text = render(reg=reg, pc=pc, choice=choice)
        self.assertTrue(text.startswith(u"👥 Учётки · 23:28\n\n⚠️ Реестра учёток нет — скажи «учётки заведи»."), text)
        self.assertIn(u"Строители ПК и телефон — профиль 3: ✅ работает\nСервер — вход A: ✅ работает", text)
        self.assertIn(u"На сервере есть ещё вход B: ⏳ лимит до вс 27.09 16:00.", text)   # без «ничей»: реестра нет
        self.assertNotIn(u"не в реестре", text)
        self.assertNotIn(u"ничей", text)

    def test_broken_registry(self):
        reg = accounts.decode(u"{битый", "", u"реестр")
        choice = accounts.builders_choice(reg)
        text = render(reg=reg, pc={"main": dict(pc_row(ENV_429_BKK), profile=accounts.WORD_MAIN)}, choice=choice)
        self.assertIn(u"⚠️ Реестр учёток не читается (файл испорчен) — поправить на ПК (посмотреть: "
                      u"accounts_run.py --show).", text)
        self.assertIn(u"Строители ПК — основной профиль: ⏳ лимит до вт 29.09 04:00\nТелефон — профиль 3", text)
        self.assertNotIn(u"Expecting", text)                                     # сырой разбор JSON не наружу
        self.assertNotIn(u"--set", text)                                         # голое «--set» консоль отвергла бы

    def test_server_unreachable(self):
        text = render(srv=live_srv(ok=False))
        self.assertIn(u"№3 samfold — ✅ ПК: работает · ❔ сервер (вход A): не проверен", text)
        self.assertIn(u"Строители ПК и телефон — №3 samfold\n❔ Сервер не ответил — его входы не проверены.", text)
        self.assertNotIn(u"Если у сервера", text)
        self.assertNotIn(u"вход B", text)

    def test_disputed_slot(self):
        text = render(reg=live_reg(slot2=u"A"), srv=live_srv(active=u"A"))
        self.assertIn(u"№2 farishka — ✅ работает · вход спорный", text)
        self.assertIn(u"№3 samfold — ✅ работает · вход спорный", text)
        self.assertIn(u"Сервер — вход A (спорный): ✅ работает", text)
        self.assertIn(u"⚠️ Вход сервера A записан сразу за №2 farishka и №3 samfold — «учётка 2» и «учётка 3» сервер "
                      u"не тронут.\nОставь вход A за одной из них — на ПК: accounts_run.py --screen, шаг 3.", text)
        self.assertEqual(text.count(u"записан сразу за"), 1)                     # один факт — одна строка
        self.assertNotIn(u"В реестре:", text)

    def test_probes_disagree(self):
        text = render(reg=live_reg(slot2=u"B"), srv=live_srv(b=ENV_429))
        self.assertIn(u"№2 farishka — ✅ ПК: работает · ⏳ сервер (вход B): лимит до вс 27.09 16:00", text)
        self.assertIn(u"⚠️ №2 farishka: на ПК работает, на сервере в лимите — похоже, это разные учётки. Поправь №2 "
                      u"на ПК (accounts_run.py --screen, шаг 3).", text)
        self.assertIn(u"Если у сервера кончится лимит, подстраховки нет: вход B (№2 farishka) тоже в лимите.", text)

    def test_lanes_on_different_accounts(self):
        text = render(reg=live_reg(slot2=u"B"), srv=live_srv(b=ENV_OK, active=u"B"), rc=RC_MAIN)
        self.assertIn(u"Строители ПК — №3 samfold\nТелефон — №1 mxfill\nСервер — №2 farishka", text)
        self.assertNotIn(u"Сейчас всё", text)
        self.assertIn(u"Если у сервера кончится лимит, упавшую задачу он один раз повторит через вход A (№3 samfold).",
                      text)
        text = render(srv=live_srv(active=u"B"))                 # строители и телефон вместе, сервер — на ничьём B
        self.assertIn(u"Строители ПК и телефон — №3 samfold\nСервер — вход B (ничей): ⏳ лимит до вс 27.09 16:00", text)

    def test_same_profile_written_differently_is_one_account(self):
        u"""Находка ревью: хвостовой «\\», «/» или регистр в пути — тот же каталог, а не «не в реестре»."""
        for rc_path in (u"D:\\claude_profile_3\\", u"D:/claude_profile_3", u"d:\\CLAUDE_PROFILE_3"):
            with self.subTest(rc=rc_path):
                self.assertEqual(render(rc=ar.RcChoice(rc_path, u"свой файл", False)), LIVE_TEXT)

    def test_retry_line_follows_the_other_login(self):
        u"""Находка ревью: «повторит через вход B», когда B сам в лимите, — ложное обещание подстраховки."""
        self.assertIn(u"Если у сервера кончится лимит, подстраховки нет: вход B тоже в лимите.", render())
        self.assertIn(u"Если у сервера кончится лимит, упавшую задачу он один раз повторит через вход B.",
                      render(srv=live_srv(b=ENV_OK)))
        self.assertIn(u"Если у сервера кончится лимит, подстраховки нет: вход B не работает.",
                      render(srv=live_srv(b=ENV_401)))
        self.assertIn(u"Если у сервера кончится лимит, подстраховка под вопросом: вход B не ответил.",
                      render(srv=live_srv(b=ENV_529)))

    def test_stray_and_busy_and_odd_servers(self):
        text = render(srv=live_srv(busy=1, extra=((u"D", ENV_401),)))
        self.assertIn(u"На сервере есть ещё вход D, ничей: ❌ не принят.", text)
        self.assertIn(u"На сервере сейчас идёт задача — пока она не кончится, «учётка N» сервер не переключит.", text)
        text = render(reg=live_reg(slot3=u""), srv=live_srv())                     # действующий вход ничей
        self.assertIn(u"Сервер — вход A (ничей): ✅ работает", text)
        self.assertIn(u"№3 samfold — ✅ работает · только ПК", text)
        text = render(srv=live_srv(active=u"-"))                                 # значение ни с кем не совпало
        self.assertIn(u"Сервер — незнакомый вход (не совпал ни с A, ни с B): ✅ работает", text)
        self.assertNotIn(u"Если у сервера", text)
        text = render(reg=live_reg(slot1=u"C"))                                  # у №1 вход C, а на сервере его нет
        self.assertIn(u"№1 mxfill — ⏳ ПК: лимит до вт 29.09 04:00 · ❌ сервер (вход C): не найден", text)

    def test_warnings_are_actionable_lines(self):
        reg = live_reg(builders=None, extra={"4": {"profile": u"", "slot": u"", "label": u""}})
        text = render(reg=reg, rc=ar.RcChoice(accounts.WORD_MAIN, u"свой файл не разобран", True))
        self.assertIn(u"⚠️ В реестре: №4 — профиль пуст.", text)
        self.assertIn(u"⚠️ За строителями учётка не закреплена — пока они на №1 mxfill; там лимит — строители стоят. "
                      u"Закрепить: «учётка N».", text)
        self.assertIn(u"⚠️ Не читается, какую учётку выбрал телефон, — он пока на №1 mxfill.", text)
        self.assertIn(u"Строители ПК и телефон — №1 mxfill", text)               # основной профиль = №1 — одним именем
        self.assertNotIn(u"основном профиле", text)
        text = render(rc=ar.RcChoice(u"D:\\claude_profile_5", u"свой файл", False))
        self.assertIn(u"Телефон — профиль 5 (не в реестре)", text)
        legacy = u"D:\\claude_profile_3 (своего файла нет — прежний файл claude_profile_choice.txt)"
        self.assertEqual(render(rc=legacy), LIVE_TEXT)          # прежняя строка rc_choice_words тоже понята
        self.assertEqual(ar.rc_choice_words(self.repo), u"ОСНОВНОЙ (ни своего файла, ни прежнего — RC-страховка)")

    def test_builders_fell_back_named_like_their_lane(self):
        u"""Находка ревью: строка «Строители ПК — …» и ⚠️ называют одно и то же одним именем, и ⚠️ говорит итог:
        там лимит — строители стоят."""
        data = {"form": 1, "builders": 3, "accounts": {
            "2": {"profile": u"D:\\claude_profile_2", "slot": u"", "label": u"farishka"},
            "3": {"profile": u"D:\\нет_каталога", "slot": u"A", "label": u"samfold"}}}
        regf = accounts.decode(json.dumps(data, ensure_ascii=False), "", u"реестр")
        pcf = {2: pc_row(ENV_OK), 3: ar.pc_probe(u"D:\\нет_каталога", runner=_Runner({}), isdir=lambda p: False),
               "main": dict(pc_row(ENV_429_BKK), profile=accounts.WORD_MAIN)}
        text = render(reg=regf, pc=pcf, choice=accounts.builders_choice(regf, isdir=lambda p: False))
        self.assertIn(u"№3 samfold — ❌ ПК: профиля нет · ✅ сервер (вход A): работает", text)
        self.assertIn(u"Строители ПК — основной профиль (не в реестре): ⏳ лимит до вт 29.09 04:00", text)
        self.assertIn(u"⚠️ Профиля №3 samfold на ПК нет — строители пока на основном профиле; там лимит — строители "
                      u"стоят.", text)
        # находка ревью: телефон на D:\claude_profile_3, а №3 реестра — другой каталог: не «профиль 3» (спутался бы
        # с №3), а имя каталога целиком
        text = render(reg=regf, pc=pcf, choice=accounts.builders_choice(regf, isdir=lambda p: False),
                      rc=ar.RcChoice(u"D:\\claude_profile_3", u"свой файл", False))
        self.assertIn(u"профиль claude_profile_3 (не в реестре)", text)
        self.assertNotIn(u"профиль 3 ", text)

    # ── «учётка N» ──
    def live_registry(self, builders=1, slot3=u"A", slot2=u"", rc=u"p3"):
        u"""Реестр живого замера в self.repo. rc="p3" — телефон по прежнему файлу на №3 (как 25.09); иначе —
        это значение в своём файле RC."""
        data = {"form": 1, "builders": builders, "accounts": {
            "1": {"profile": u"ОСНОВНОЙ", "slot": u"", "label": u"mxfill"},
            "2": {"profile": self.p2, "slot": slot2, "label": u"farishka"},
            "3": {"profile": self.p3, "slot": slot3, "label": u"samfold"}}}
        with io.open(self.reg_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
        if rc == u"p3":
            with io.open(os.path.join(self.repo, "claude_profile_choice.txt"), "w", encoding="utf-8") as f:
                f.write(self.p3 + u"\n")
        else:
            with io.open(os.path.join(self.repo, ar.RC_CHOICE_REL), "w", encoding="utf-8") as f:
                f.write(rc + u"\n")

    def switch3(self, use, pc_env=ENV_OK, builders=1, slot3=u"A", rc=u"p3"):
        self.live_registry(builders=builders, slot3=slot3, rc=rc)
        return ar.switch("3", runner=_Runner({self.p3: pc_env}), use=use, repo=self.repo, save=False,
                         now=NOW, tz=7)

    def test_switch_every_use_slot_stage(self):
        for name, how, want, code_want in USE_CASES:
            with self.subTest(stage=name):
                res = drive_use_slot(**how)
                code, text = self.switch3(lambda slot, force=False, work=None: res)
                self.assertEqual(code, code_want, text)
                head = u"🔁 Учётка №3 samfold — %s" % (u"готово ✅" if code_want == 0 else u"не всё ⚠️")
                self.assertTrue(text.startswith(head + u"\n\n✅ ПК: строители перешли на неё (были на №1 mxfill).\n"),
                                text)
                for line in want:
                    self.assertIn(line, text)
                self.assertIn(u"Телефон не трогал — он на №3 samfold.", text)
                for raw in FORBIDDEN + (u"ОТКАТ", u"ОТКАЗ", u"СТОП", u"MainPID", u"демон", u"слот ", u"этап", u"НЕ "):
                    self.assertNotIn(raw, text)
                self.assertIsNone(_RAW_CODE_RE.search(text), text)

    def test_switch_crash_and_unknown_stage(self):
        def boom(slot, force=False, work=None):
            raise RuntimeError("канал")
        code, text = self.switch3(boom)
        self.assertEqual(code, 1)
        self.assertIn(u"❔ Сервер: переключение оборвалось ошибкой — что с сервером, неизвестно; проверь «учётки».", text)
        self.assertNotIn(u"RuntimeError", text)
        long_line = u"новый этап двери: " + u"слово " * 60
        code, text = self.switch3(lambda slot, force=False, work=None: {"ok": False, "stage": u"weird",
                                                                        "lines": [long_line], "row": None})
        self.assertEqual(code, 1)
        self.assertIn(u"❔ Сервер: исход непонятен — проверь «учётки».", text)
        detail = [ln for ln in text.splitlines() if ln.startswith(u"Подробности: ")]
        self.assertEqual(len(detail), 1)
        self.assertLessEqual(len(detail[0]) - len(u"Подробности: "), 120)
        self.assertTrue(detail[0].endswith(u"…"))

    def test_switch_pc_side_in_words(self):
        done = drive_use_slot(write=WRITE_OK)
        use = lambda slot, force=False, work=None: done
        cases = ((ENV_OK, 3, u"✅ ПК: строители и так на ней.", 0),
                 (ENV_429, 1, u"⏳ ПК: лимит до вс 27.09 16:00 — строители остались на №1 mxfill.", 1),
                 (ENV_401, 1, u"❌ ПК: вход не принят — строители остались на №1 mxfill.", 1),
                 (ENV_529, 1, u"❔ ПК: нет ответа (Claude перегружен) — строители остались на №1 mxfill.", 1))
        for env, builders, want, code_want in cases:
            with self.subTest(want=want):
                code, text = self.switch3(use, pc_env=env, builders=builders)
                self.assertEqual(code, code_want, text)
                self.assertIn(want, text)
        code, text = self.switch3(use, pc_env=ENV_429)
        self.assertIn(u"Не всё: ПК остался на №1 mxfill, сервер перешёл.", text)
        ar.find_claude = lambda *a, **k: None
        code, text = self.switch3(use)
        self.assertIn(u"❔ ПК: нет ответа (Claude не найден на ПК) — строители остались на №1 mxfill.", text)
        ar.find_claude = lambda *a, **k: "claude-fake"
        from unittest import mock
        with mock.patch.object(accounts, "set_builders", side_effect=OSError("диск")):
            code, text = self.switch3(use)
        self.assertEqual(code, 1)
        self.assertIn(u"❌ ПК: проверка прошла, но записать не вышло — строители остались на №1 mxfill.", text)
        self.live_registry()                                                     # «каталога нет» — подменой isdir
        code, text = ar.switch("3", runner=_Runner({}), use=use, repo=self.repo, save=False, now=NOW, tz=7,
                               isdir=lambda p: p != self.p3)
        self.assertIn(u"❌ ПК: профиля нет — строители остались на №1 mxfill.", text)

    def test_switch_phone_line_names_where_the_phone_really_is(self):
        u"""Находка ревью: «Телефон не трогал — он на …» называет выбор ТЕЛЕФОНА — не цель слова и не строителей."""
        done = drive_use_slot(write=WRITE_OK)
        use = lambda slot, force=False, work=None: done
        for rc_value, want in ((accounts.WORD_MAIN, u"№1 mxfill"), (self.p2, u"№2 farishka"),
                               (self.p2 + u"\\", u"№2 farishka")):
            with self.subTest(rc=rc_value):
                code, text = self.switch3(use, rc=rc_value)
                self.assertEqual(code, 0, text)
                self.assertIn(u"Телефон не трогал — он на %s." % want, text)

    def test_switch_without_server_login_and_refusals(self):
        code, text = self.switch3(_Use(), slot3=u"")
        self.assertEqual(code, 0)
        self.assertEqual(text, u"🔁 Учётка №3 samfold — готово ✅\n\n"
                               u"✅ ПК: строители перешли на неё (были на №1 mxfill).\n"
                               u"▫️ Сервер: у неё входа нет — не трогал.\n"
                               u"Телефон не трогал — он на №3 samfold.\n\n"
                               u"Готово: ПК на №3 samfold (входа на сервере у неё нет).")
        run, use = _Runner({}), _Use()
        self.live_registry()
        before = _sha(self.reg_path)
        code, text = ar.switch("9", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual((code, text), (2, u"⛔ Учётки №9 нет. Есть: №1 mxfill, №2 farishka, №3 samfold. "
                                           u"Ничего не изменено."))
        code, text = ar.switch("abc", runner=run, use=use, repo=self.repo, save=False)
        self.assertEqual((code, text), (2, u"⛔ Учётки «abc» нет. Есть: №1 mxfill, №2 farishka, №3 samfold. "
                                           u"Ничего не изменено."))
        self.assertEqual((run.calls, use.calls, _sha(self.reg_path)), ([], [], before))

    def test_copy_words(self):
        u"""Находка ревью: ❗ «вернуть из копии» называет ЭТУ копию — на сервере их много, не удаляется ни одна."""
        self.assertEqual(ar.copy_words(u"/etc/orch.env.bak-20260925-162800Z", now=NOW, tz=7),
                         u"копии от 23:28 (.bak-20260925-162800Z)")
        self.assertEqual(ar.copy_words(u"/etc/orch.env.bak-20260920-010000Z", now=NOW, tz=7),
                         u"копии от 20.09 08:00 (.bak-20260920-010000Z)")
        self.assertEqual(ar.copy_words(u"", now=NOW, tz=7), u"самой свежей копии (около 23:28)")
        self.assertEqual(ar.copy_words(u"/etc/x.old", now=NOW, tz=7), u"копии «x.old»")

    def test_use_words_phrases_still_exist_in_use_slot(self):
        u"""`use_words` различает исходы по словам строк `use_slot` — сверка по исходнику: переименуют строку
        в двери — этот тест покраснеет раньше, чем владелец получит «исход непонятен»."""
        with io.open(os.path.join(HERE, "vps_token_install.py"), encoding="utf-8") as f:
            src = f.read()
        for phrase in (u"в файле не назван", u"не проверено", u"идёт заход", u"Копия не сошлась",
                       u"(канал оборвался)", u"запись до файла не дошла", u"НО демон мог", u"демон не поднялся",
                       u"поднят обратно: %s", u"демон активен", u"начался заход", u"ОТКАЗ на записи: %s",
                       u"канал: %s", u"заходов демона: %d", u'"%s.bak-%s"', u'"%Y%m%d-%H%M%SZ"'):
            self.assertIn(phrase, src)

    # ── «учётки заведи» ──
    def init_live(self, pcs, a_env=ENV_OK, b_env=ENV_429, ok=True, today=(2026, 9, 25)):
        self.addCleanup(setattr, ar, "INIT_PROFILE_2", ar.INIT_PROFILE_2)
        self.addCleanup(setattr, ar, "INIT_PROFILE_3", ar.INIT_PROFILE_3)
        ar.INIT_PROFILE_2, ar.INIT_PROFILE_3 = self.p2, self.p3
        with io.open(os.path.join(self.repo, "claude_profile_choice.txt"), "w", encoding="utf-8") as f:
            f.write(self.p3 + u"\n")
        srv = lambda work=None: live_srv(ok=ok, a=a_env, b=b_env)
        return ar.init_registry(runner=_Runner(pcs), srv_probe=srv, repo=self.repo, today=today, save=False,
                                now=NOW, tz=7)

    def test_init_texts(self):
        code, text = self.init_live({accounts.WORD_MAIN: ENV_429, self.p2: ENV_OK, self.p3: ENV_OK})
        self.assertEqual(code, 0, text)
        self.assertEqual(text, u"🆕 Реестр учёток заведён\n\n"
                               u"№1 основная — только ПК\n№2 — только ПК\n№3 — ПК и сервер (вход A)\n\n"
                               u"Вход A на сервере — №3: он работает, а основная в лимите.\n"
                               u"Вход B за №2 не записал: на ПК №2 работает, а вход B в лимите — похоже, это разные "
                               u"учётки.\n"
                               u"Назначить вход на ПК: accounts_run.py --screen, шаг 3.\n"
                               u"Строители ПК — на №3.\n"
                               u"Сервер не трогал — он на №3. Перевести — «учётка N».\n"
                               u"Телефон не трогал — он на №3.")

    def test_init_says_only_what_probes_showed(self):
        u"""Находка ревью: A = 200, основная в лимите, а №3 на ПК = 401 — вход A за №3 (правило П2), но сказать
        «№3 работает» нельзя: её вход не принят. Ответ не спорит сам с собой."""
        code, text = self.init_live({accounts.WORD_MAIN: ENV_429, self.p3: ENV_401})
        self.assertEqual(code, 0, text)
        self.assertIn(u"Вход A на сервере — №3: он работает, а основная в лимите.", text)
        self.assertIn(u"Строители ПК — на №1: вход №3 не принят.", text)
        self.assertNotIn(u"№3 работает", text)

    def test_init_builders_stay_on_first_in_words(self):
        code, text = self.init_live({accounts.WORD_MAIN: ENV_429, self.p3: ENV_429}, a_env=ENV_429, b_env=ENV_OK)
        self.assertEqual(code, 0, text)
        self.assertIn(u"Чей вход A — не понять: вход A, основная и №3 — все в лимите. Не записал.", text)
        self.assertIn(ar.SCREEN_HINT, text)
        self.assertIn(u"Строители ПК — на №1: №3 в лимите до вс 27.09 16:00.", text)
        self.assertIn(u"№2 — ПК и сервер (вход B)", text)
        self.assertIn(u"Вход B записал за №2, как задумано; что на ПК и на сервере это одна учётка, пробы не доказывают.", text)
        self.assertIn(u"Телефон не трогал — он на №3.", text)                  # телефон — свой выбор, не строителей
        self.assertIn(u"Сервер не трогал — он на ничьём входе A.", text)

    def test_init_refusals_in_words(self):
        code, text = self.init_live({}, ok=False)
        self.assertEqual((code, text), (2, u"⏳ Реестр не завёл: сервер не ответил. Повтори «учётки заведи» позже. "
                                           u"Ничего не изменено."))
        code, text = self.init_live({accounts.WORD_MAIN: ENV_529, self.p3: ENV_OK})
        self.assertEqual((code, text), (2, u"⏳ Реестр не завёл: основная на ПК не ответила (Claude перегружен). "
                                           u"Повтори «учётки заведи» позже. Ничего не изменено."))
        self.assertFalse(os.path.exists(self.reg_path))
        code, text = self.init_live({accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK})
        self.assertEqual(code, 0, text)
        before = _sha(self.reg_path)
        code, text = self.init_live({})
        self.assertEqual((code, text), (2, u"⛔ Реестр уже есть — «учётки» покажет, что в нём. Ничего не изменено."))
        self.assertEqual(_sha(self.reg_path), before)
        with io.open(self.reg_path, "w", encoding="utf-8") as f:
            f.write(u"{битый")
        before = _sha(self.reg_path)
        run = _Runner({})
        code, text = ar.init_registry(runner=run, srv_probe=lambda work=None: live_srv(), repo=self.repo, save=False)
        self.assertEqual((code, text), (2, u"⛔ Реестр уже есть, но не читается (файл испорчен) — заново заводить не "
                                           u"буду.\nПоправь его на ПК (посмотреть: accounts_run.py --show). Ничего не "
                                           u"изменено."))
        self.assertEqual((_sha(self.reg_path), run.calls), (before, []))
        from unittest import mock
        other = tempfile.mkdtemp(prefix="uchetki_2509_")
        with mock.patch.object(accounts, "create", side_effect=FileExistsError("гонка")):
            code, text = ar.init_registry(runner=_Runner({accounts.WORD_MAIN: ENV_429}), repo=other, save=False,
                                          srv_probe=lambda work=None: live_srv(), today=(2026, 9, 25))
        self.assertEqual((code, text), (2, u"⛔ Пока шли пробы, реестр уже завели — «учётки» покажет, что в нём. "
                                           u"Ничего не изменено."))

    def test_init_without_server_login_a(self):
        srv = lambda work=None: {"ok": True, "rows": [vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0)),
                                                      vti.slot_row("TB_CLAUDE_TOKEN_B", _out("1", ENV_OK, rc=0))],
                                 "busy": 0}
        self.addCleanup(setattr, ar, "INIT_PROFILE_2", ar.INIT_PROFILE_2)
        self.addCleanup(setattr, ar, "INIT_PROFILE_3", ar.INIT_PROFILE_3)
        ar.INIT_PROFILE_2, ar.INIT_PROFILE_3 = self.p2, self.p3
        code, text = ar.init_registry(runner=_Runner({accounts.WORD_MAIN: ENV_429}), srv_probe=srv, repo=self.repo,
                                      today=(2026, 9, 25), save=False, now=NOW, tz=7)
        self.assertEqual(code, 0, text)
        self.assertIn(u"Входа A на сервере нет — №1 и №3 пока без входа на сервере.", text)
        self.assertIn(u"Сервер не трогал — он на №2. Перевести — «учётка N».", text)
        self.assertIn(u"Вход B записал за №2, как задумано; что на ПК и на сервере это одна учётка, пробы не доказывают.", text)
        srv_no_b = lambda work=None: {"ok": True, "busy": 0, "rows": [
            vti.slot_row(vti.NAME_ACTIVE, _out("1", ENV_OK, rc=0)), vti.slot_row("TB_CLAUDE_TOKEN_A", _out("1", ENV_OK, rc=0))]}
        code, text = ar.init_registry(runner=_Runner({accounts.WORD_MAIN: ENV_429}), srv_probe=srv_no_b,
                                      repo=tempfile.mkdtemp(prefix="uchetki_2509_"), today=(2026, 9, 25), save=False,
                                      now=NOW, tz=7)
        self.assertEqual(code, 0, text)
        self.assertIn(u"Вход B записал за №2, но на сервере его пока нет.", text)

    # ── замок: технических слов в ответах владельцу нет ──
    def test_no_technical_words_anywhere(self):
        from unittest import mock
        texts = [self.init_live({accounts.WORD_MAIN: ENV_429, self.p3: ENV_OK})[1]]    # до реестра — заведение
        self.assertTrue(texts[0].startswith(u"🆕 Реестр учёток заведён"), texts[0])
        texts.append(self.init_live({})[1])                                              # реестр уже есть
        broken = accounts.decode(u"{битый", "", u"реестр")
        texts += [render(), render(srv=live_srv(ok=False)), render(reg=live_reg(slot2=u"A")),
                  render(reg=live_reg(slot2=u"B")), render(srv=live_srv(busy=1, extra=((u"D", ENV_401),))),
                  render(srv=live_srv(active=u"-")), render(reg=live_reg(slot3=u"")), render(reg=live_reg(slot1=u"C")),
                  render(rc=RC_MAIN), render(reg=live_reg(slot2=u"B"), srv=live_srv(b=ENV_OK, active=u"B"), rc=RC_MAIN),
                  render(reg=live_reg(builders=None), rc=ar.RcChoice(accounts.WORD_MAIN, u"не разобран", True)),
                  render(reg=accounts.decode(None, "", u"реестр"),
                         pc={"main": dict(pc_row(ENV_OK), profile=u"D:\\claude_profile_3")},
                         choice=accounts.Choice(accounts.ACT_SET, u"D:\\claude_profile_3", u"реестра нет", True, None)),
                  render(reg=broken, pc={"main": dict(pc_row(ENV_OK), profile=accounts.WORD_MAIN)},
                         choice=accounts.builders_choice(broken))]
        for name, how, _want, _code in USE_CASES:
            texts.append(self.switch3(lambda slot, force=False, work=None, _r=drive_use_slot(**how): _r)[1])
        self.live_registry()                                                             # отказы «учётка N»
        texts += [ar.switch(x, runner=_Runner({}), use=_Use(), repo=self.repo, save=False)[1] for x in ("9", "abc")]
        other = tempfile.mkdtemp(prefix="uchetki_2509_")
        texts.append(ar.switch("3", runner=_Runner({}), use=_Use(), repo=other, save=False)[1])      # реестра нет
        with io.open(os.path.join(other, accounts.REGISTRY_REL), "w", encoding="utf-8") as f:
            f.write(u"{битый")
        texts.append(ar.switch("3", runner=_Runner({}), use=_Use(), repo=other, save=False)[1])      # реестр битый
        texts.append(ar.init_registry(runner=_Runner({}), srv_probe=lambda work=None: live_srv(), repo=other,
                                      save=False)[1])                                               # заведи: битый
        fresh = tempfile.mkdtemp(prefix="uchetki_2509_")
        texts.append(ar.init_registry(runner=_Runner({}), srv_probe=lambda work=None: live_srv(ok=False), repo=fresh,
                                      save=False)[1])                                               # сервер молчит
        texts.append(ar.init_registry(runner=_Runner({accounts.WORD_MAIN: ENV_529}), repo=fresh, save=False,
                                      srv_probe=lambda work=None: live_srv(), today=(2026, 9, 25))[1])  # 529
        with mock.patch.object(accounts, "create", side_effect=FileExistsError("гонка")):
            texts.append(ar.init_registry(runner=_Runner({accounts.WORD_MAIN: ENV_429}), repo=fresh, save=False,
                                          srv_probe=lambda work=None: live_srv(), today=(2026, 9, 25))[1])
        for text in texts:
            with self.subTest(text=text[:50]):
                for raw in FORBIDDEN + (u"слот", u"проба действующего", u"модели проб", u"Traceback", u"Error"):
                    self.assertNotIn(raw, text)
                self.assertIsNone(_RAW_CODE_RE.search(text), text)
                for ln in text.splitlines():
                    self.assertLessEqual(len(ln), 160, ln)                            # телефон: строка — не абзац
                    if ln.startswith(u"⚠️"):
                        self.assertTrue(ln.endswith(u"."), ln)                        # ⚠️ — законченная фраза


if __name__ == "__main__":
    unittest.main(verbosity=2)
