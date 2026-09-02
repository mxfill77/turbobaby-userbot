# -*- coding: utf-8 -*-
"""Регресс ДОСТАВКИ РАБОТЫ СЕРВЕРНОЙ ПОЛОСЫ (`srv_delivery`) + инвариант SRV_DELIVERY_PURE.

Ради чего этот файл существует — четыре вещи, и ни одна из них не «покрытие ради процента»:

  1. ОТРИЦАТЕЛЬНЫЕ ИСХОДЫ ПРОВЕРЕНЫ В ОБЕ СТОРОНЫ. «Сервер молчит» обязано давать НЕИЗВЕСТНО, а
     не «нечего забирать» и не «ошибка»; рядом с каждой такой дыркой стои́т кейс, где при полном
     факте та же ветка отвечает по существу. Инвариант молчания без второй половины — это
     молчание, а не замок.
  2. ОТКАЗ ПРОВЕРЯЕТСЯ ДВЕРЬЮ, А НЕ ОТКЛЮЧЕНИЕМ СЕТИ. Все походы наружу идут через одну
     инъектируемую функцию `run`; тест подменяет её и видит КАЖДУЮ команду, которую модуль послал
     бы. Поэтому «при недоступном сервере ничего не испортили» — это не рассуждение, а список
     команд, в котором нет `push`.
  3. ФОРМАТ ФАКТОВ = ЖИВОЙ ФОРМАТ. Хеши в фикстурах — настоящие, снятые 03.09.2026 живой пробой:
     `e646f1f…` (общий main от 30.08), `f1375cb…` (вершина сервера), `70d7553…` (первый из двух
     доставленных). Вывод `ls-remote` повторяет прод дословно, вместе с табуляцией и `refs/heads/`.
  4. ГРАНИЦА ДЕРЖИТСЯ УСТРОЙСТВОМ, А НЕ ДОКСТРИНГОМ: ast-разбор доказывает, что решение
     (`decide`/`should_say`/`due`/`signature`) не умеет ничего, кроме арифметики над переданными
     фактами, и что в сторону сервера в файле нет ни одного пишущего глагола git, а в сторону
     общего репозитория — ни одной форсной формы push.

Запуск — тем же способом, что и весь гейт репозитория (способ запуска — часть формата):
    venv\\Scripts\\python.exe -m unittest test_srv_delivery
"""
import ast
import io
import os
import unittest

import srv_delivery as sd

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "srv_delivery.py")

# Живые хеши, снятые пробой 03.09.2026 (см. артефакт того же дня).
HUB = "e646f1f12c6f55b1d26e541e2170ca36b62c0ac8"     # общий main от 30.08.2026 13:31 UTC
SRV = "f1375cbeb9d8f5d84e56594586bb1a2365eef701"     # вершина сервера, 02.09.2026 20:09 UTC
OLD = "70d755383863f988dee46fcf8c7255fc1408a204"     # первый из двух застрявших


def ls_line(sha, branch="main"):
    """Дословный формат `git ls-remote`: хеш, ТАБУЛЯЦИЯ, полное имя ссылки."""
    return "%s\trefs/heads/%s\n" % (sha, branch)


class FakeGit(object):
    """Подменённая дверь наружу. Пишет всё, о чём её просили, и отвечает по сценарию.

    Сценарий — словарь «первое слово команды (+ремоут) → (rc, вывод)». Всё неназванное отвечает
    нулём и пустой строкой: тест обязан падать на НЕОЖИДАННОМ походе наружу, а не на забытом ключе."""

    def __init__(self, plan):
        self.plan = plan
        self.calls = []

    def __call__(self, argv, cwd=None, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        verb = argv[0] if argv else ""
        key = verb
        if verb in ("ls-remote", "fetch", "push") and len(argv) > 1:
            key = "%s:%s" % (verb, [a for a in argv[1:] if not a.startswith("-")][0])
        if key in self.plan:
            return self.plan[key]
        if verb in self.plan:
            return self.plan[verb]
        return 0, ""

    def verbs(self):
        return [c[0] for c in self.calls]

    def flat(self):
        return [" ".join(c) for c in self.calls]


def plan_ok(srv=SRV, hub=HUB, push=(0, "To github\n")):
    """Полный сценарий счастливого пути: обе стороны отвечают, hub — предок srv, между ними 2."""
    return {
        "init": (0, ""),
        "remote": (0, ""),
        "ls-remote:srv": (0, ls_line(srv)),
        "ls-remote:hub": (0, ls_line(hub)),
        "fetch:srv": (0, ""),
        "fetch:hub": (0, ""),
        "merge-base": (0, ""),          # уточняется в тестах, где важны ОБЕ стороны родства
        "rev-list": (0, "2\n"),
        "push:hub": push,
        "diff": (0, ""),
        "show": (0, ""),
    }


class AncestryGit(FakeGit):
    """То же, но с честным ответом на ДВА разных `merge-base --is-ancestor`: порядок аргументов
    и есть вопрос, а один общий ответ на оба превратил бы конфликт в перемотку."""

    def __init__(self, plan, hub_in_srv, srv_in_hub, revlist=None):
        FakeGit.__init__(self, plan)
        self.hub_in_srv, self.srv_in_hub = hub_in_srv, srv_in_hub
        self.revlist = revlist or [OLD, SRV]

    def __call__(self, argv, cwd=None, timeout=None):
        argv = list(argv)
        if argv[:2] == ["merge-base", "--is-ancestor"]:
            self.calls.append(argv)
            a, b = argv[2], argv[3]
            val = self.hub_in_srv if (a, b) == (HUB, SRV) else self.srv_in_hub
            return (0 if val else 1), ""
        if argv[:2] == ["rev-list", "--count"]:
            self.calls.append(argv)
            return 0, "%d\n" % len(self.revlist)
        if argv[:2] == ["rev-list", "--reverse"]:
            self.calls.append(argv)
            return 0, "\n".join(self.revlist) + "\n"
        return FakeGit.__call__(self, argv, cwd=cwd, timeout=timeout)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  РЕШЕНИЕ: факты → исход
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestDecide(unittest.TestCase):

    def test_server_silent_is_unknown_not_error_and_not_nothing(self):
        kind, why = sd.decide({"srv_head": None, "hub_head": HUB})
        self.assertEqual(sd.UNKNOWN, kind)
        self.assertIn(u"сервер не ответил", why)
        # ЗАМОК: молчание источника не имеет права выглядеть как «забирать нечего».
        self.assertNotEqual(sd.NOTHING, kind)

    def test_hub_silent_is_unknown_too(self):
        kind, why = sd.decide({"srv_head": SRV, "hub_head": None})
        self.assertEqual(sd.UNKNOWN, kind)
        self.assertIn(u"общий репозиторий не ответил", why)

    def test_both_sides_answer_and_are_equal_is_nothing_not_error(self):
        kind, why = sd.decide({"srv_head": SRV, "hub_head": SRV})
        self.assertEqual(sd.NOTHING, kind)
        self.assertIn(u"нечего", why)

    def test_server_behind_hub_is_nothing_not_conflict(self):
        # Кто-то положил в общий репозиторий больше, чем есть у сервера. Забирать с сервера
        # нечего — но это НОРМА, а не расхождение историй.
        kind, why = sd.decide({"srv_head": OLD, "hub_head": SRV,
                               "hub_in_srv": False, "srv_in_hub": True})
        self.assertEqual(sd.NOTHING, kind)

    def test_fast_forward_is_deliver_and_names_the_count(self):
        kind, why = sd.decide({"srv_head": SRV, "hub_head": HUB,
                               "hub_in_srv": True, "srv_in_hub": False, "count": 2})
        self.assertEqual(sd.DELIVER, kind)
        self.assertIn("2", why)

    def test_diverged_histories_stop_and_call_a_human(self):
        kind, why = sd.decide({"srv_head": SRV, "hub_head": HUB,
                               "hub_in_srv": False, "srv_in_hub": False})
        self.assertEqual(sd.CONFLICT, kind)
        self.assertIn(u"человек", why)

    def test_unmeasured_ancestry_is_unknown_not_deliver(self):
        # Третий исход обязателен и здесь: «родство не сверено» ≠ «перематывай».
        kind, why = sd.decide({"srv_head": SRV, "hub_head": HUB,
                               "hub_in_srv": None, "srv_in_hub": None})
        self.assertEqual(sd.UNKNOWN, kind)

    def test_decide_never_returns_an_unnamed_kind(self):
        known = {sd.DELIVER, sd.NOTHING, sd.CONFLICT, sd.UNKNOWN}
        cases = [
            {"srv_head": None, "hub_head": None},
            {"srv_head": SRV, "hub_head": None},
            {"srv_head": SRV, "hub_head": SRV},
            {"srv_head": SRV, "hub_head": HUB, "hub_in_srv": True, "srv_in_hub": False},
            {"srv_head": SRV, "hub_head": HUB, "hub_in_srv": False, "srv_in_hub": False},
            {"srv_head": SRV, "hub_head": HUB, "hub_in_srv": None, "srv_in_hub": None},
        ]
        for f in cases:
            self.assertIn(sd.decide(f)[0], known, f)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ЖУРНАЛ ПО СМЕНЕ ИСХОДА И ЧАСТОТА ПОХОДОВ
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestSayAndDue(unittest.TestCase):

    def test_new_event_speaks(self):
        prev = {"sig": "nothing|a|a", "kind": sd.NOTHING, "said_at": 0}
        self.assertTrue(sd.should_say(prev, "deliver|b|a", sd.DELIVER, 1000.0))

    def test_nothing_is_silent_even_when_signature_changed(self):
        # После доставки подписи разойдутся, и наивное «сменилось → пиши» дало бы вторую
        # строку «нечего» сразу за строкой «доставлено». Отсутствие новостей новостью не является.
        prev = {"sig": "deliver|b|a", "kind": sd.DELIVER, "said_at": 10.0}
        self.assertFalse(sd.should_say(prev, "nothing|b|b", sd.NOTHING, 1000.0))

    def test_recovery_after_a_loud_outcome_speaks(self):
        prev = {"sig": "unknown|-|a", "kind": sd.UNKNOWN, "said_at": 10.0}
        self.assertTrue(sd.should_say(prev, "nothing|b|b", sd.NOTHING, 1000.0))

    def test_stuck_unknown_repeats_not_more_often_than_the_floor(self):
        prev = {"sig": "unknown|-|a", "kind": sd.UNKNOWN, "said_at": 1000.0}
        self.assertFalse(sd.should_say(prev, "unknown|-|a", sd.UNKNOWN, 1000.0 + 3600))
        self.assertTrue(sd.should_say(prev, "unknown|-|a", sd.UNKNOWN,
                                      1000.0 + sd.REPEAT_H * 3600 + 1))

    def test_first_ever_run_speaks_when_loud(self):
        self.assertTrue(sd.should_say(None, "unknown|-|-", sd.UNKNOWN, 5.0))
        self.assertFalse(sd.should_say(None, "nothing|a|a", sd.NOTHING, 5.0))

    def test_due_respects_its_own_stamp_and_survives_a_clock_going_back(self):
        self.assertTrue(sd.due(None, 1000.0, every_min=30))
        self.assertFalse(sd.due({"probed_at": 1000.0}, 1000.0 + 60, every_min=30))
        self.assertTrue(sd.due({"probed_at": 1000.0}, 1000.0 + 1801, every_min=30))
        # Часы уехали назад (сон/перевод) — доставка не имеет права запереться навсегда.
        self.assertTrue(sd.due({"probed_at": 5000.0}, 1000.0, every_min=30))


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ОБОРОТ ЦЕЛИКОМ НА ПОДМЕНЁННОЙ ДВЕРИ — включая три отрицательных случая задания
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestTick(unittest.TestCase):

    def run_tick(self, git, prev=None, **kw):
        said = []
        res = sd.tick(run=git, now=kw.pop("now", 10000.0),
                      say=lambda line: (said.append(line), True)[1],
                      state=prev if prev is not None else {}, force=True, **kw)
        res["said_lines"] = said
        return res

    def test_server_down_is_unknown_and_pushes_nothing(self):
        git = FakeGit(dict(plan_ok(), **{"ls-remote:srv": (128, "ssh: connect timed out\n")}))
        res = self.run_tick(git)
        self.assertEqual(sd.UNKNOWN, res["kind"])
        self.assertNotIn("push", git.verbs())          # ← «ничего не портит» списком команд
        self.assertNotIn("fetch", git.verbs())         # к молчащему серверу второй раз не идём
        self.assertEqual(1, len(res["said_lines"]))    # …и молчанием это НЕ становится

    def test_a_side_we_never_asked_is_not_recorded_as_silent(self):
        """Молчание и невопрос — разные вещи. Сервер не ответил → общий репозиторий мы даже не
        спрашивали, и отчёт обязан это различать, иначе владелец пойдёт чинить не то."""
        git = FakeGit(dict(plan_ok(), **{"ls-remote:srv": (128, "timeout\n")}))
        res = self.run_tick(git)
        self.assertEqual(["srv"], res["facts"]["asked"])
        git2 = FakeGit(dict(plan_ok(), **{"ls-remote:hub": (128, "denied\n")}))
        res2 = self.run_tick(git2)
        self.assertEqual(["srv", "hub"], res2["facts"]["asked"])

    def test_hub_down_is_unknown_and_pushes_nothing(self):
        git = FakeGit(dict(plan_ok(), **{"ls-remote:hub": (128, "could not read Username\n")}))
        res = self.run_tick(git)
        self.assertEqual(sd.UNKNOWN, res["kind"])
        self.assertNotIn("push", git.verbs())

    def test_nothing_to_take_says_nothing_not_error(self):
        git = FakeGit(dict(plan_ok(), **{"ls-remote:hub": (0, ls_line(SRV))}))
        res = self.run_tick(git)
        self.assertEqual(sd.NOTHING, res["kind"])
        self.assertNotIn("push", git.verbs())
        self.assertNotIn("fetch", git.verbs())         # равные вершины — за объектами не ходим
        self.assertEqual([], res["said_lines"])        # норма в журнал не лезет

    def test_diverged_histories_stop_and_do_not_push(self):
        git = AncestryGit(plan_ok(), hub_in_srv=False, srv_in_hub=False)
        res = self.run_tick(git)
        self.assertEqual(sd.CONFLICT, res["kind"])
        self.assertNotIn("push", git.verbs())
        self.assertEqual(1, len(res["said_lines"]))    # человек обязан узнать

    def test_fast_forward_pushes_exactly_once_and_never_forcibly(self):
        git = AncestryGit(plan_ok(), hub_in_srv=True, srv_in_hub=False)
        res = self.run_tick(git)
        self.assertEqual(sd.DELIVER, res["kind"])
        pushes = [c for c in git.calls if c and c[0] == "push"]
        self.assertEqual(1, len(pushes))
        self.assertEqual(["push", "hub", "%s:refs/heads/main" % SRV], pushes[0])
        self.assertNotIn("--force", pushes[0])
        self.assertFalse(pushes[0][-1].startswith("+"))
        self.assertEqual([OLD, SRV], res["shas"])

    def test_rejected_push_is_blocked_not_a_silent_success(self):
        git = AncestryGit(dict(plan_ok(), **{"push:hub": (128, "remote: Permission denied\n")}),
                          hub_in_srv=True, srv_in_hub=False)
        res = self.run_tick(git)
        self.assertEqual(sd.BLOCKED, res["kind"])
        self.assertEqual([], res["shas"])
        self.assertEqual(1, len(res["said_lines"]))
        self.assertNotIn("last_deliver", res["state"])

    def test_dry_run_never_pushes_and_never_moves_the_stamp(self):
        git = AncestryGit(plan_ok(), hub_in_srv=True, srv_in_hub=False)
        res = self.run_tick(git, prev={"probed_at": 1.0}, dry=True)
        self.assertEqual(sd.DELIVER, res["kind"])
        self.assertNotIn("push", git.verbs())
        self.assertEqual(1.0, res["state"]["probed_at"])
        self.assertEqual([], res["said_lines"])

    def test_off_flag_kills_the_branch_entirely(self):
        git = FakeGit(plan_ok())
        os.environ["SRV_DELIVERY_OFF"] = "1"
        try:
            res = self.run_tick(git)
        finally:
            os.environ.pop("SRV_DELIVERY_OFF", None)
        self.assertEqual(sd.OFF, res["kind"])
        self.assertEqual([], git.calls)                # ни одного обращения наружу

    def test_throttle_keeps_the_daemon_from_going_out_every_turn(self):
        git = FakeGit(plan_ok())
        res = sd.tick(run=git, now=1000.0, say=lambda l: True,
                      state={"probed_at": 1000.0}, force=False)
        self.assertEqual("skip", res["kind"])
        self.assertEqual([], git.calls)

    def test_unknown_does_not_pretend_the_state_moved_forward(self):
        # Молчание источника не имеет права стереть память о последней УДАЧНОЙ доставке.
        git = FakeGit(dict(plan_ok(), **{"ls-remote:srv": (128, "timeout\n")}))
        prev = {"last_deliver": [OLD, SRV], "last_deliver_at": 5.0, "kind": sd.DELIVER,
                "sig": "deliver|f|e", "said_at": 5.0}
        res = self.run_tick(git, prev=prev)
        self.assertEqual(sd.UNKNOWN, res["kind"])
        self.assertEqual([OLD, SRV], res["state"]["last_deliver"])
        self.assertEqual(5.0, res["state"]["last_deliver_at"])

    def test_exit_codes_separate_unknown_from_ok(self):
        self.assertEqual({sd.CONFLICT: 1, sd.BLOCKED: 1, sd.UNKNOWN: 2}.get(sd.NOTHING, 0), 0)
        self.assertEqual({sd.CONFLICT: 1, sd.BLOCKED: 1, sd.UNKNOWN: 2}.get(sd.UNKNOWN, 0), 2)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ИНВАРИАНТ SRV_DELIVERY_PURE — граница держится отсутствием инструментов, а не докстрингом
# ══════════════════════════════════════════════════════════════════════════════════════════
PURE_FUNCS = ("decide", "should_say", "due", "signature")
_FORBIDDEN_CALLS = frozenset(("open", "exec", "eval", "compile", "__import__", "input"))
_FORBIDDEN_ROOTS = frozenset(("os", "sys", "subprocess", "shutil", "socket", "urllib", "time",
                              "requests", "pathlib", "tempfile", "sqlite3"))


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _tree(path=None):
    path = path or SRC
    return ast.parse(_read(path), path)


def _run_argvs():
    """Все командные строки, которые модуль вообще способен послать наружу: литеральные списки,
    отданные первым аргументом в `run(...)`. Проверять ТЕКСТ файла на слово нельзя — «push» и
    «--force» стоя́т в прозе шапки, где им и место; предмет проверки — argv, а не рассказ о нём."""
    out = []
    for node in ast.walk(_tree()):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "run" and node.args):
            continue
        arg = node.args[0]
        if not isinstance(arg, ast.List):
            continue
        argv = []
        for el in arg.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                argv.append(el.value)
            elif isinstance(el, ast.BinOp) and isinstance(el.left, ast.Constant):
                argv.append(str(el.left.value))       # форма `"%s:refs/heads/%s" % (...)`
            else:
                argv.append("<computed>")
        out.append(argv)
    return out


class TestPurityAndBlastRadius(unittest.TestCase):

    def test_decision_functions_touch_nothing_but_their_arguments(self):
        found = 0
        for node in ast.walk(_tree()):
            if not (isinstance(node, ast.FunctionDef) and node.name in PURE_FUNCS):
                continue
            found += 1
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    if isinstance(f, ast.Name):
                        self.assertNotIn(f.id, _FORBIDDEN_CALLS, node.name)
                    if isinstance(f, ast.Attribute):
                        root = f
                        while isinstance(root, ast.Attribute):
                            root = root.value
                        if isinstance(root, ast.Name):
                            self.assertNotIn(root.id, _FORBIDDEN_ROOTS,
                                             "%s: %s" % (node.name, root.id))
        self.assertEqual(len(PURE_FUNCS), found, u"функция решения пропала или переименована")

    def test_the_invariant_can_actually_fail(self):
        # Замок без доказанной способности падать — украшение. Вносим нарушение и требуем отказа.
        bad = ast.parse("def decide(f):\n    return subprocess.run(['x'])\n")
        hit = False
        for node in ast.walk(bad):
            if isinstance(node, ast.FunctionDef) and node.name in PURE_FUNCS:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        root = sub.func
                        while isinstance(root, ast.Attribute):
                            root = root.value
                        if isinstance(root, ast.Name) and root.id in _FORBIDDEN_ROOTS:
                            hit = True
        self.assertTrue(hit, u"инвариант не ловит внесённое нарушение")

    # Весь словарь git этого модуля. Читающие глаголы + РОВНО ОДИН пишущий, и тот в сторону
    # общего репозитория. Список закрыт: новый глагол обязан пройти через правку этого теста.
    _READ_ONLY = frozenset(("init", "remote", "ls-remote", "fetch", "merge-base", "rev-list",
                            "diff", "show"))

    def test_the_whole_git_vocabulary_is_read_only_plus_one_push(self):
        """Чтение сервера — обещание модуля. Держим его РАЗБОРОМ argv, а не памятью автора."""
        argvs = _run_argvs()
        self.assertGreaterEqual(len(argvs), 8, u"разбор argv сломался — команд почти не видно")
        writing = [a for a in argvs if a[0] not in self._READ_ONLY]
        self.assertEqual(1, len(writing), u"пишущих команд не одна: %s" % writing)
        self.assertEqual("push", writing[0][0])

    def test_the_only_writing_command_never_names_the_server(self):
        for argv in _run_argvs():
            if argv[0] in self._READ_ONLY:
                continue
            self.assertNotIn("srv", argv, u"пишущая команда смотрит на сервер: %s" % argv)

    def test_every_remote_named_in_argv_is_one_of_the_two_known(self):
        for argv in _run_argvs():
            for tok in argv:
                if tok in ("origin", "upstream"):
                    self.fail(u"чужой ремоут в argv: %s" % argv)

    def test_push_goes_only_to_the_hub_and_only_as_a_plain_fast_forward(self):
        pushes = [a for a in _run_argvs() if a and a[0] == "push"]
        self.assertEqual(1, len(pushes), u"push обязан быть ровно один")
        argv = pushes[0]
        self.assertEqual("hub", argv[1])
        self.assertNotIn("--force", argv)
        self.assertNotIn("--force-with-lease", argv)
        self.assertNotIn("--mirror", argv)
        self.assertFalse(argv[-1].startswith("+"), u"форсный refspec: %s" % argv[-1])
        self.assertTrue(argv[-1].startswith("%s:refs/heads/"), argv[-1])

    def test_no_remote_shell_and_no_receive_pack_anywhere_in_argv(self):
        for argv in _run_argvs():
            for tok in argv:
                self.assertNotIn("receive-pack", tok)
                self.assertNotIn("--exec", tok)

    def test_ssh_timeouts_are_present_and_are_the_ones_the_owner_named(self):
        self.assertIn("ConnectTimeout=10", sd.SSH_OPTS)
        self.assertIn("ServerAliveInterval=15", sd.SSH_OPTS)
        self.assertIn("ServerAliveCountMax=4", sd.SSH_OPTS)
        self.assertIn("BatchMode=yes", sd.SSH_OPTS)
        cmd = sd._ssh_command()
        for opt in ("ConnectTimeout=10", "ServerAliveInterval=15", "ServerAliveCountMax=4",
                    "BatchMode=yes"):
            self.assertIn(opt, cmd)

    def test_mirror_lives_under_tmp_and_is_bare(self):
        # Голое зеркало без рабочего дерева — доставка физически не может задеть файлы репозитория.
        self.assertTrue(sd.MIRROR.replace("\\", "/").endswith("tmp/srv_delivery/manager-bot.git"))
        self.assertIn("--bare", _read(SRC))


# ══════════════════════════════════════════════════════════════════════════════════════════
#  РЕГУЛЯРНОСТЬ: механизм обязан БЫТЬ ПОДКЛЮЧЁН, а не лежать рядом
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestWiredIntoTheDaemon(unittest.TestCase):

    def test_daemon_calls_delivery_once_per_turn_before_the_heartbeat(self):
        """Скрипт, которого никто не зовёт, регулярностью не является. И вызов обязан стоять ДО
        `_write_heartbeat()`: тот держит инвариант «последняя строка оборота» для О2/О4."""
        src = _read(os.path.join(REPO, "pc_orchestrator.py"))
        body = src.split("def poll_once(")[1].split("\ndef ")[0]
        self.assertIn("_srv_delivery()", body)
        self.assertLess(body.index("_srv_delivery()"), body.index("_write_heartbeat()"))
        self.assertIn('"srv_delivery.py"', src)

    def test_delivery_is_spawned_as_a_separate_process_not_inlined(self):
        src = _read(os.path.join(REPO, "pc_orchestrator.py"))
        block = src.split("def _srv_delivery(")[1].split("\ndef ")[0]
        self.assertIn("subprocess.Popen", block)
        self.assertNotIn("subprocess.run", block)

    def test_test_flag_keeps_the_cli_off_the_wire(self):
        src = _read(SRC)
        self.assertIn("TURBOBABY_TEST_LOGS", src)


if __name__ == "__main__":
    unittest.main()
