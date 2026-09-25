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

import accounts
import accounts_run as ar
import rc_supervisor as rc
import vps_token_install as vti

HERE = os.path.dirname(os.path.abspath(__file__))
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
        last = accounts.load_last(self.repo)
        self.assertEqual((last["pc"]["1"]["code"], last["srv"]["B"]["reset"]), (429, u"Sep 27, 9am (UTC)"))

    def test_without_registry_main_plus_warning(self):
        run = _Runner({accounts.WORD_MAIN: ENV_OK})
        text = ar.report(runner=run, srv_probe=self.srv(), repo=self.repo, save=False)
        self.assertIn(u"WARNING", text)
        self.assertIn(u"без реестра · ПК ОСНОВНОЙ: 200 жива", text)
        self.assertIn(u"строители ПК — ОСНОВНОЙ (WARNING", text)

    def test_server_silent_is_said(self):
        self.registry()
        text = ar.report(runner=_Runner({}), repo=self.repo, save=False,
                         srv_probe=lambda work=None: {"ok": False, "words": u"ssh до сервера не поднялся",
                                                      "rows": []})
        self.assertIn(u"сервер — не проверено — ssh до сервера не поднялся", text)


def _out(active, envelope, rc=1):
    return ("loaded=1\nslot_filled=1\nis_active=%s\nfile_mtime=100\ndaemon_start=200\n%s\nprobe_rc=%d\n"
            % (active, envelope, rc))


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
