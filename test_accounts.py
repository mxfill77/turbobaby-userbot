# -*- coding: utf-8 -*-
u"""test_accounts.py — реестр учёток вне git и выбор строителей (25.09.2026, «учётки одним словом»).

Держит:
  1) РЕЕСТР ЧИТАЕТСЯ ЧЕСТНО: годная запись — учётка; метка с `@` (почта), слот не буквой, номер не
     числом — выброшены С ПРИЧИНОЙ; слот, названный у двух учёток, снят у обеих.
  2) СТРОИТЕЛИ: ОСНОВНОЙ → ключ снят; каталог есть → ключ = каталог; всё прочее (реестра нет, не
     JSON, номер не назван/не из реестра, каталога нет) → ОСНОВНОЙ плюс WARNING. Пустого значения
     ключа нет ни на одной дороге.
  3) ЗАПИСЬ РЕЕСТРА: номер строителей — только из реестра; метка с почтой, чужой слот — отказ, файл
     не тронут побайтно.
  4) RC НЕ ТРОГАЕТСЯ СМЕНОЙ СТРОИТЕЛЕЙ (отрицательный тест задания): окружение ребёнка RC побайтно
     одно и то же при любом номере строителей и любом содержимом старого файла дерева.
  5) ЧИСТОТА: модуль, который импортирует демон, не знает ни `subprocess`, ни сети, ни удаления.

Временное — системный temp, каталоги с префиксом `uchetki_2509_`; тест их НЕ удаляет (запрет
«ничего не удалять, включая уборку за собой»). Боевой реестр, боевые файлы выбора и рабочее дерево
не читаются и не пишутся: каждый случай — свой временный REPO.
    D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_accounts -v
"""

import ast
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import accounts_registry as accounts
import profile_choice
import rc_supervisor as rc

KEY = accounts.KEY
HERE = os.path.dirname(os.path.abspath(__file__))


def _tmp():
    return tempfile.mkdtemp(prefix="uchetki_2509_")


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _registry(repo, builders, accounts_obj, extra=None):
    data = {"form": 1, "builders": builders, "accounts": accounts_obj}
    data.update(extra or {})
    _write(os.path.join(repo, accounts.REGISTRY_REL), json.dumps(data, ensure_ascii=False))


class _Tree(unittest.TestCase):
    """Временный REPO с тремя каталогами профилей: основной (слово), профиль 2, профиль 3."""

    def setUp(self):
        self.repo = _tmp()
        self.p2 = os.path.join(self.repo, "claude_profile_2")
        self.p3 = os.path.join(self.repo, "claude_profile_3")
        os.mkdir(self.p2)
        os.mkdir(self.p3)
        self.acc = {
            "1": {"profile": u"ОСНОВНОЙ", "slot": "A", "label": u"основная"},
            "2": {"profile": self.p2, "slot": "B", "label": u"вторая"},
            "3": {"profile": self.p3, "slot": "", "label": u"третья"},
        }


class TestRegistryRead(_Tree):
    def test_good_registry(self):
        _registry(self.repo, 3, self.acc)
        reg = accounts.load(self.repo)
        self.assertEqual(reg.state, accounts.ST_OK)
        self.assertEqual(sorted(reg.data["accounts"]), [1, 2, 3])
        self.assertEqual(reg.data["builders"], 3)
        self.assertEqual(reg.data["accounts"][1]["profile"], accounts.WORD_MAIN)
        self.assertEqual(reg.data["accounts"][3]["slot"], "")
        self.assertEqual(reg.errors, [])

    def test_bad_rows_dropped_with_reason(self):
        acc = dict(self.acc)
        acc["4"] = {"profile": self.p3, "slot": "AB", "label": u"четвёртая"}
        acc["5"] = {"profile": self.p3, "slot": "E", "label": u"кто-то@почта"}
        acc["x"] = {"profile": self.p3, "slot": "F", "label": u"икс"}
        acc["6"] = {"profile": u"какое-то слово", "slot": "G", "label": u"шестая"}
        _registry(self.repo, 1, acc)
        reg = accounts.load(self.repo)
        self.assertEqual(sorted(reg.data["accounts"]), [1, 2, 3])
        text = u"\n".join(reg.errors)
        self.assertIn(u"не одна буква", text)
        self.assertIn(u"без почты", text)
        self.assertIn(u"не число", text)
        self.assertIn(u"ни слово", text)
        self.assertEqual(len(reg.errors), 4)

    def test_same_slot_twice_is_cleared_on_both(self):
        acc = dict(self.acc)
        acc["3"] = dict(acc["3"], slot="A")
        _registry(self.repo, 1, acc)
        reg = accounts.load(self.repo)
        self.assertEqual(reg.data["accounts"][1]["slot"], "")
        self.assertEqual(reg.data["accounts"][3]["slot"], "")
        self.assertTrue(any(u"слот A" in e for e in reg.errors))

    def test_absent_and_broken(self):
        self.assertEqual(accounts.load(self.repo).state, accounts.ST_ABSENT)
        _write(os.path.join(self.repo, accounts.REGISTRY_REL), u"{не json")
        self.assertEqual(accounts.load(self.repo).state, accounts.ST_BROKEN)

    def test_bom_is_not_breakage(self):
        _write(os.path.join(self.repo, accounts.REGISTRY_REL),
               u"\ufeff" + json.dumps({"builders": 1, "accounts": self.acc}, ensure_ascii=False))
        self.assertEqual(accounts.load(self.repo).state, accounts.ST_OK)


class TestBuildersChoice(_Tree):
    def _apply(self, parent=None):
        env = {"PATH": "x", KEY: parent} if parent is not None else {"PATH": "x"}
        c = accounts.apply_builders(env, self.repo)
        return env, c

    def test_path_sets_key(self):
        _registry(self.repo, 3, self.acc)
        env, c = self._apply(r"D:\claude_profile_2")
        self.assertEqual(env[KEY], self.p3)
        self.assertEqual((c.action, c.warn, c.number), (accounts.ACT_SET, False, 3))

    def test_main_word_drops_key(self):
        _registry(self.repo, 1, self.acc)
        env, c = self._apply(r"D:\claude_profile_2")
        self.assertNotIn(KEY, env)
        self.assertEqual((c.action, c.warn, c.number), (accounts.ACT_DROP, False, 1))

    def test_every_failure_is_main_plus_warning(self):
        cases = {
            u"реестра нет": None,
            u"не JSON": u"{",
            u"номер не назван": json.dumps({"accounts": self.acc}),
            u"номер не из реестра": json.dumps({"builders": 9, "accounts": self.acc}),
            u"каталога нет": json.dumps({"builders": 3, "accounts": dict(
                self.acc, **{"3": dict(self.acc["3"], profile=os.path.join(self.repo, "нет"))})}),
        }
        for title, text in cases.items():
            with self.subTest(title):
                repo = _tmp()
                if text is not None:
                    _write(os.path.join(repo, accounts.REGISTRY_REL), text)
                env = {"PATH": "x", KEY: r"D:\claude_profile_2"}
                c = accounts.apply_builders(env, repo)
                self.assertNotIn(KEY, env, u"унаследованный профиль 2 не снят: %s" % title)
                self.assertTrue(c.warn)
                self.assertIn(u"WARNING", accounts.line(c))
                self.assertIn(accounts.WORD_MAIN, c.reason)

    def test_never_empty_key(self):
        for b in (1, 2, 3, 9, None):
            _registry(self.repo, b, self.acc)
            for parent in ("", r"D:\x", None):
                env = {"PATH": "x"} if parent is None else {"PATH": "x", KEY: parent}
                accounts.apply_builders(env, self.repo)
                self.assertNotEqual(env.get(KEY, "absent"), "")

    def test_isdir_raising_is_main_plus_warning(self):
        _registry(self.repo, 3, self.acc)

        def boom(_p):
            raise OSError("нет доступа")
        env = {"PATH": "x", KEY: "y"}
        c = accounts.apply_builders(env, self.repo, isdir=boom)
        self.assertNotIn(KEY, env)
        self.assertTrue(c.warn)


class TestRegistryWrite(_Tree):
    def test_set_builders_only_from_registry(self):
        _registry(self.repo, 1, self.acc, {"комментарий": u"владельца"})
        prev = accounts.set_builders(3, self.repo)
        self.assertEqual(prev, 1)
        with io.open(os.path.join(self.repo, accounts.REGISTRY_REL), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["builders"], 3)
        self.assertEqual(data[u"комментарий"], u"владельца")      # чужие ключи владельца целы
        path = os.path.join(self.repo, accounts.REGISTRY_REL)
        before = _sha(path)
        with self.assertRaises(ValueError):
            accounts.set_builders(9, self.repo)
        self.assertEqual(_sha(path), before)

    def test_set_entry_refuses_email_and_foreign_slot(self):
        _registry(self.repo, 1, self.acc)
        path = os.path.join(self.repo, accounts.REGISTRY_REL)
        before = _sha(path)
        with self.assertRaises(ValueError):
            accounts.set_entry(4, self.p3, "C", u"я@почта", repo=self.repo)
        with self.assertRaises(ValueError):
            accounts.set_entry(4, self.p3, "B", u"четвёртая", repo=self.repo)  # B уже у №2
        with self.assertRaises(ValueError):
            accounts.set_entry(4, self.p3, "CC", u"четвёртая", repo=self.repo)
        self.assertEqual(_sha(path), before)

    def test_fourth_account_without_code(self):
        u"""Четвёртая учётка — строкой реестра: код не правится, выбор её видит сразу."""
        _registry(self.repo, 1, self.acc)
        p4 = os.path.join(self.repo, "claude_profile_4")
        os.mkdir(p4)
        accounts.set_entry(4, p4, "c", u"четвёртая", repo=self.repo)
        accounts.set_builders(4, self.repo)
        env = {"PATH": "x"}
        c = accounts.apply_builders(env, self.repo)
        self.assertEqual((env[KEY], c.number), (p4, 4))
        self.assertEqual(accounts.load(self.repo).data["accounts"][4]["slot"], "C")

    def test_registry_is_outside_git(self):
        with io.open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            lines = {ln.strip() for ln in f}
        for name in (accounts.REGISTRY_REL, accounts.LAST_REL, rc.RC_CHOICE_REL):
            self.assertIn(name, lines, u"%s не под .gitignore — правка словом пачкала бы дерево" % name)


class TestRcUntouchedByBuilders(_Tree):
    u"""ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗАДАНИЯ: смена строителей не меняет окружение ребёнка RC.

    Родитель несёт профиль 2 (живой прокол 18–22.09). Строителей гоняем по всем учёткам и пишем в
    старый файл дерева путь существующего каталога — у RC окружение обязано остаться побайтно
    одним и тем же и при ОСНОВНОМ (ключа нет)."""

    PARENT = {KEY: r"D:\claude_profile_2", "PATH": r"C:\x"}

    def _rc_env(self):
        env, why = rc.child_env(base=dict(self.PARENT), repo=self.repo)
        return (env if env is not None else dict(self.PARENT)), why

    def test_builders_switch_leaves_rc_alone(self):
        seen = []
        for b in (1, 2, 3):
            _registry(self.repo, b, self.acc)
            _write(os.path.join(self.repo, profile_choice.CHOICE_REL), self.p3 + u"\n")
            env_b = {"PATH": "x"}
            accounts.apply_builders(env_b, self.repo)
            env, _why = self._rc_env()
            self.assertNotIn(KEY, env, u"RC ушёл с ОСНОВНОГО при строителях №%d" % b)
            seen.append(sorted(env.items()))
        self.assertEqual(seen[0], seen[1])
        self.assertEqual(seen[1], seen[2])

    def test_rc_reads_only_its_own_file(self):
        _registry(self.repo, 3, self.acc)
        _write(os.path.join(self.repo, rc.RC_CHOICE_REL), self.p2 + u"\n")
        env, why = self._rc_env()
        self.assertEqual(env[KEY], self.p2)                      # явный путь RC работает
        self.assertIn(u"RC", why)

    def test_rc_default_is_main_when_own_file_absent(self):
        _registry(self.repo, 2, self.acc)
        env, why = self._rc_env()
        self.assertNotIn(KEY, env)
        self.assertIn(rc.RC_CHOICE_REL, why)                     # причина называет СВОЙ файл RC

    def test_rc_module_never_names_builders_registry(self):
        with io.open(os.path.join(HERE, "rc_supervisor.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = {getattr(n, "id", None) for n in ast.walk(tree)} | {
            getattr(n, "attr", None) for n in ast.walk(tree)}
        self.assertNotIn("accounts", names)
        self.assertNotIn("apply_builders", names)
        consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str)}
        self.assertNotIn(accounts.REGISTRY_REL, consts)
        self.assertNotIn(profile_choice.CHOICE_REL, consts)


class TestBuilderChild(unittest.TestCase):
    u"""Слово владельца не зовётся из захода строителей: метки ребёнка демона узнаются по имени."""

    def test_markers_found_and_absent(self):
        self.assertEqual(accounts.builder_child({}), "")
        self.assertEqual(accounts.builder_child({"PATH": "x", "PRETOOL_MARKER_TOKEN": "t"}),
                         "PRETOOL_MARKER_TOKEN")
        self.assertEqual(accounts.builder_child({"PRETOOL_ASK_MARKER": ""}), "")   # пустая — не метка

    def test_marker_names_match_the_daemon(self):
        import re
        import git_serial_pc
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as f:
            src = f.read()
        ask = re.search(r'^ASK_MARKER_ENV\s*=\s*"([^"]+)"', src, re.M).group(1)
        tok = re.search(r'^MARKER_TOKEN_ENV\s*=\s*"([^"]+)"', src, re.M).group(1)
        self.assertEqual(set(accounts.BUILDER_MARKERS), {ask, tok, git_serial_pc.OWNER_ENV})
        # демон обязан по-прежнему класть их ребёнку — иначе гейт ослеп бы молча
        for const in ("env[ASK_MARKER_ENV]", "env[MARKER_TOKEN_ENV]", "env[git_serial_pc.OWNER_ENV]"):
            self.assertIn(const, src)


class TestReviewFixes(_Tree):
    u"""Находки ревью 25.09: каждая — случай, где демон или слово владельца падали трассой или молчали."""

    def test_non_ascii_digit_never_throws(self):
        u"""«²», «③», арабская «٣» — `isdigit()` их пропускал, `int()` падал, и падение роняло спавн."""
        for raw in (u"²", u"①", u"٣", u"３"):
            with self.subTest(raw=raw):
                self.assertIsNone(accounts.parse_number(raw))
                _registry(self.repo, raw, self.acc)
                reg = accounts.load(self.repo)
                self.assertEqual(reg.state, accounts.ST_OK)
                self.assertTrue(any(u"номер строителей" in e for e in reg.errors))
                env = {"PATH": "x", KEY: "/родитель"}
                c = accounts.apply_builders(env, self.repo)
                self.assertTrue(c.warn)
                self.assertNotIn(KEY, env)

    def test_apply_builders_never_throws(self):
        u"""Любое исключение разбора → ОСНОВНОЙ плюс WARNING с причиной, а не трасса в спавне."""
        env = {"PATH": "x", KEY: "/родитель"}
        with mock.patch.object(accounts, "builders_choice", side_effect=RuntimeError("сломано")):
            c = accounts.apply_builders(env, self.repo)
        self.assertEqual((c.action, c.warn), (accounts.ACT_DROP, True))
        self.assertIn(u"RuntimeError", c.reason)
        self.assertNotIn(KEY, env)
        self.assertTrue(accounts.line(c).startswith("WARNING: "))

    def test_builders_null_is_not_an_error(self):
        _registry(self.repo, None, self.acc)
        reg = accounts.load(self.repo)
        self.assertEqual((reg.state, reg.errors), (accounts.ST_OK, []))
        c = accounts.builders_choice(reg)
        self.assertEqual((c.action, c.warn), (accounts.ACT_DROP, True))
        self.assertIn(u"не назван номер строителей", c.reason)
        _registry(self.repo, "три", self.acc)                   # близнец: слово вместо числа — ошибка
        self.assertTrue(accounts.load(self.repo).errors)

    def test_shared_slot_leaves_a_named_trace(self):
        acc = dict(self.acc)
        acc["3"] = dict(acc["3"], slot="A")
        _registry(self.repo, 1, acc)
        reg = accounts.load(self.repo)
        for n in (1, 3):
            self.assertIn(u"спорный", reg.data["accounts"][n].get("slot_error", u""))
        self.assertNotIn("slot_error", reg.data["accounts"][2])     # близнец: чужой слот не задет

    def test_malformed_history_is_reset_not_thrown(self):
        u"""`accounts_last.json` с `null`/списком в ветвях: итог слова не смеет упасть ПОСЛЕ сервера."""
        _write(os.path.join(self.repo, accounts.LAST_REL), json.dumps({"pc": None, "srv": [1, 2]}))
        last = accounts.load_last(self.repo)
        self.assertEqual((last["pc"], last["srv"]), ({}, {}))
        last["pc"]["1"] = {"code": 200}
        accounts.save_last(last, self.repo)
        self.assertEqual(accounts.load_last(self.repo)["pc"]["1"]["code"], 200)

    def test_create_refuses_existing_and_bad_rows(self):
        rows = {1: {"profile": u"ОСНОВНОЙ", "slot": "", "label": u"основная"},
                3: {"profile": self.p3, "slot": "A", "label": u"третья"}}
        data = accounts.create(rows, 3, self.repo)
        self.assertEqual(data["builders"], 3)
        self.assertEqual(accounts.load(self.repo).state, accounts.ST_OK)
        before = _sha(os.path.join(self.repo, accounts.REGISTRY_REL))
        with self.assertRaises(FileExistsError):
            accounts.create(rows, 1, self.repo)
        self.assertEqual(_sha(os.path.join(self.repo, accounts.REGISTRY_REL)), before)
        other = _tmp()
        with self.assertRaises(ValueError):
            accounts.create({1: {"profile": self.p3, "slot": "A", "label": u"x@y"}}, 1, other)
        self.assertFalse(os.path.exists(os.path.join(other, accounts.REGISTRY_REL)))


class TestCreateIsExclusive(_Tree):
    u"""Находка ревью 25.09: проверка «файла нет» и запись разнесены во времени; реестр, положенный
    рукой владельца в этот промежуток, заведение НЕ затирает."""

    def test_file_appearing_after_the_check_is_not_overwritten(self):
        rows = {1: {"profile": u"ОСНОВНОЙ", "slot": "", "label": u"основная"}}
        p = os.path.join(self.repo, accounts.REGISTRY_REL)
        _registry(self.repo, 2, self.acc)                           # «рука владельца» уже положила файл
        before = _sha(p)
        real_exists = os.path.exists
        with mock.patch.object(accounts.os.path, "exists",
                               lambda x: False if x == p else real_exists(x)):   # проверка его «не видела»
            with self.assertRaises(FileExistsError):
                accounts.create(rows, 1, self.repo)
        self.assertEqual(_sha(p), before)
        self.assertFalse(real_exists(p + ".tmp"))


class TestClientGateNotWeakened(unittest.TestCase):
    u"""Находка ревью 25.09: ворота входа клиентского контура (`client_contour.mentions`) ищут в тексте
    задания голый stem КАЖДОГО модуля корня. Модуль с именем-словом прозы (`accounts`) делал бы
    находку «клиенту не приходят ответы про accounts» ВНУТРЕННЕЙ вместо fail-closed «клиентской».
    Модули этой правки обязаны не менять вердикт ворот ни на одном таком тексте."""

    TEXTS = (u"клиенту не приходят ответы про accounts", u"account клиента заблокирован",
             u"Accounts page shows wrong price to the client")

    def test_prose_word_does_not_name_our_modules(self):
        import client_contour as cc
        cl = cc.closure(HERE)
        self.assertTrue(cl.ok)
        for text in self.TEXTS:
            with self.subTest(text=text):
                self.assertEqual(cc.mentions(text, repo=HERE, cl=cl), (True, [], False))

    def test_our_modules_stay_out_of_the_client_closure(self):
        import client_contour as cc
        cl = cc.closure(HERE)
        for mod in ("accounts_registry.py", "accounts_run.py", "vps_token_install.py"):
            with self.subTest(mod=mod):
                self.assertFalse(cc.is_client(mod, cl=cl))
        self.assertFalse(os.path.exists(os.path.join(HERE, "accounts.py")))   # имя-слово не вернулось


class TestPurity(unittest.TestCase):
    ALLOWED = {"io", "json", "os", "re", "collections", "profile_choice"}

    def setUp(self):
        with io.open(os.path.join(HERE, "accounts_registry.py"), encoding="utf-8") as f:
            self.tree = ast.parse(f.read())

    def test_imports_are_narrow(self):
        got = set()
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                got |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                got.add((n.module or "").split(".")[0])
        self.assertEqual(got - self.ALLOWED, set())

    def test_never_deletes(self):
        attrs = {getattr(n, "attr", None) for n in ast.walk(self.tree)}
        for bad in ("remove", "unlink", "rmtree", "rmdir"):
            self.assertNotIn(bad, attrs)

    def test_no_empty_assignment_to_key(self):
        u"""`env[KEY] = ""` не встречается НИ РАЗУ — ветки с пустым значением нет физически."""
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value == "":
                for t in n.targets:
                    if isinstance(t, ast.Subscript):
                        self.assertNotEqual(getattr(t.slice, "id", None), "KEY",
                                            u"env[KEY] = \"\" — профиль БЕЗ входа (M2 замера 70u)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
