# -*- coding: utf-8 -*-
"""
test_rc_choice_wiring.py — файл выбора учётки доходит до ЖИВОГО провода RC (задание 70z, 22.09).

Зачем отдельный файл. `test_rc_supervisor.TestChildEnvProfileChoice` проверяет `child_env` сам по
себе, а спавн и doctor — с ПОДСТАВЛЕННЫМ `envf`. Провод по умолчанию (`default_spawn` → `child_env`
→ `claude_profile_choice.txt`, `rc_ready` → `child_env`) не держал ни один тест: замер мутантами
22.09 — «спавн мимо child_env» и «doctor мимо child_env» прошли все 84 теста. Такой мутант
возвращает ровно дефект 18–22.09: ветки RC поднимаются в профиле из переменной пользователя
(91 старт, 88 гашений ЗОМБИ, 0 регистраций), а файл выбора делает вид, что решает.

Что здесь закреплено и что НЕТ. Закреплён ПРОВОД: каждый ребёнок RC (обе ветки и doctor) получает
ровно то окружение, которое решил `child_env`, а при ОСНОВНОЙ и при пути это решение одно при любой
политике третьего исхода. Политика третьего исхода (мусор → наследовать или снять ключ) здесь
НАМЕРЕННО не судится — её держит `test_rc_supervisor`, и она решается отдельно.

Боевые функции зовутся БЕЗ `envf`. Файл выбора лежит во временном REPO (боевой не читается и не
пишется), родительская переменная ставится в окружение самого теста, вместо процессов — двойники.
Сообщения отказов называют только ИМЕНА ключей: окружение теста наследует окружение демона, и
печатать его значения в вывод гейта нельзя.
    D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_rc_choice_wiring -v
"""

import os
import tempfile
import types
import unittest
from unittest import mock

import rc_supervisor as rc

KEY = "CLAUDE_CONFIG_DIR"
PARENT = r"D:\claude_profile_2"          # только строка: профиль не открывается
SHIM = r"C:\Users\u\.local\bin\claude.exe"
DOCTOR_OK = "Remote Control\n- Connected\n\n0 warnings found\n"
CHILDREN = ["doctor", "named-channel", "rc-server"]


class _Proc:
    """Процесс ветки: сразу «вышел» (poll → 0), чтобы цикл сторожа закончил жизнь за один круг."""

    pid = 4242

    def poll(self):
        return 0


class _Done:
    def __init__(self, out=""):
        self.stdout, self.stderr, self.returncode = out, "", 0


class _InertAuth:
    """Детектор кредов, который НИЧЕГО не судит: без `registered` суд о регистрации выключен,
    ENABLED=False — рестарта по кредам нет. Иначе цикл полез бы читать лог ветки."""

    ENABLED = False

    def new_state(self):
        return {}

    def scan(self, path, state):
        return state

    def should_restart(self, state):
        return False

    def describe(self, state):
        return ""


def _diff_keys(a, b):
    """Имена ключей, по которым два окружения расходятся. Значения не выводим никогда."""
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


class TestChoiceFileReachesLiveWiring(unittest.TestCase):
    """Три исхода файла × все дети RC (обе ветки и doctor пре-флайта), провод по умолчанию."""

    GARBAGE = (
        None,                                   # файла нет
        "",                                     # пусто
        "  \n# только комментарий\n",           # значимых строк 0
        '""',                                   # пустые кавычки
        "ОСНОВНОЙ\nD:\\x\n",                    # две значимые строки
        "D:\\a\x07b",                           # управляющий символ
        r"D:\нет_такого_каталога_rc_70z",       # путь не существует
    )

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.profile = os.path.join(self.root, "fake_profile")
        os.mkdir(self.profile)
        self.case = 0
        for p in (mock.patch.object(rc, "REPO", self.root),
                  mock.patch.dict(os.environ, {KEY: PARENT})):
            p.start()
            self.addCleanup(p.stop)

    def _choice(self, text):
        """Каждый случай — СВОЙ подкаталог временного REPO: «файла нет» получается без удаления.
        Подмену rc.REPO снимает патч из setUp — он возвращает исходное значение."""
        self.case += 1
        repo = os.path.join(self.root, "case%02d" % self.case)
        os.mkdir(repo)
        rc.REPO = repo
        if text is not None:
            with open(os.path.join(repo, "claude_profile_choice.txt"), "w", encoding="utf-8") as f:
                f.write(text)

    def _spec(self, spec):
        return dict(spec, log=os.path.join(rc.REPO, spec["label"] + ".log"))

    def _children(self):
        """→ {кто: kwargs процесса}. Боевые default_spawn и rc_ready, envf НЕ подставлен."""
        out = {}
        for spec in rc.BRANCHES:
            seen = {}

            def popen(cmd, **kw):
                seen["kw"] = kw
                return _Proc()
            rc.default_spawn(SHIM, self._spec(spec), verbose=False, popen=popen)
            out[spec["label"]] = seen["kw"]
        doc = []
        rc.rc_ready(SHIM, runner=lambda *a, **k: doc.append(k) or _Done(DOCTOR_OK))
        out["doctor"] = doc[0]
        return out

    @staticmethod
    def _effective(kw):
        """Что увидит ребёнок: env не передан в Popen → блок окружения родителя целиком."""
        return kw["env"] if "env" in kw else dict(os.environ)

    def test_main_word_no_key_in_every_child(self):
        self._choice("# строка отката\nОСНОВНОЙ\n")
        kids = self._children()
        self.assertEqual(sorted(kids), CHILDREN)
        for who, kw in kids.items():
            with self.subTest(who):
                env = self._effective(kw)
                self.assertFalse(KEY in env, "ключ профиля дошёл до ребёнка при ОСНОВНОЙ")
                self.assertEqual(_diff_keys(env, dict(os.environ)), [KEY])   # снят ровно он

    def test_path_sets_key_in_every_child(self):
        self._choice(self.profile + "\n")
        for who, kw in self._children().items():
            with self.subTest(who):
                env = self._effective(kw)
                self.assertTrue(env.get(KEY) == self.profile, "ключ не равен пути из файла")
                self.assertEqual(_diff_keys(env, dict(os.environ)), [KEY])

    def test_third_outcome_children_get_exactly_child_env_decision(self):
        # политика третьего исхода не судится; судится, что ребёнок получает ИМЕННО её
        for text in self.GARBAGE:
            self._choice(text)
            want_env, _why = rc.child_env()
            want = want_env if want_env is not None else dict(os.environ)
            for who, kw in self._children().items():
                with self.subTest(text=text, who=who):
                    self.assertEqual("env" in kw, want_env is not None)
                    self.assertEqual(_diff_keys(self._effective(kw), want), [])

    def test_never_empty_key_in_any_child(self):
        for text in self.GARBAGE + ("ОСНОВНОЙ", self.profile):
            self._choice(text)
            for who, kw in self._children().items():
                with self.subTest(text=text, who=who):
                    self.assertFalse(self._effective(kw).get(KEY, "нет ключа") == "",
                                     "ребёнку поставлена ПУСТАЯ переменная профиля")

    def test_branch_loop_default_wiring_reaches_file(self):
        """Живой формат подъёма: цикл сторожа с боевыми spawner/gate по умолчанию. subprocess
        подменён ТОЛЬКО внутри модуля супервизора — ни одного настоящего процесса."""
        self._choice("ОСНОВНОЙ\n")
        calls = []

        def run(argv, **kw):
            calls.append(("run", list(argv), kw))
            return _Done(DOCTOR_OK)

        def popen(argv, **kw):
            calls.append(("popen", list(argv), kw))
            return _Proc()
        fake = types.SimpleNamespace(run=run, Popen=popen)
        with mock.patch.object(rc, "subprocess", fake):
            for spec in rc.BRANCHES:
                rc.supervise_branch(self._spec(spec), resolver=lambda: SHIM,
                                    alive=lambda sp, pid: True, sleeper=lambda s: None,
                                    killer=lambda p: None, rounds=1, checks=1, grace_checks=1,
                                    auth=_InertAuth(), notifier=lambda t: True)
        doctors = [c for c in calls if c[0] == "run" and c[1][-1:] == ["doctor"]]
        spawns = [c for c in calls if c[0] == "popen" and c[1][:1] == [SHIM]]
        self.assertEqual((len(doctors), len(spawns)), (2, 2),
                         [(k, a[1:3]) for k, a, _ in calls])     # только argv, без окружения
        for kind, argv, kw in doctors + spawns:
            with self.subTest(kind=kind, argv=argv[1:3]):
                self.assertTrue("env" in kw, "окружение по файлу выбора не передано")
                self.assertFalse(KEY in kw["env"], "ключ профиля дошёл до ребёнка при ОСНОВНОЙ")
                self.assertFalse("creationflags" in kw, "консоль ребёнка погашена — TTY мёртв")


if __name__ == "__main__":
    unittest.main()
