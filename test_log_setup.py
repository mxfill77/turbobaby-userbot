# -*- coding: utf-8 -*-
"""
test_log_setup.py — голдены ротации логов и разведения тестовых/боевых логов.

Оба класса дефектов — живой факт аудита 22:27:
  * ротации не было НИГДЕ (голый FileHandler): pc_agent.log 28.7 МБ, pricing.log 22.2 МБ,
    pc_orchestrator.log 11.2 МБ — при 0.4 ГБ свободных на C:;
  * тесты писали в БОЕВОЙ pretool_guard.log: фикстуры прогона 21:39 (`clasp push`,
    `sqlite3 bookings.db "select 1"`, пустая команда) неотличимы от реальных действий владельца
    и попали в разбор «последних 20 Allow» как настоящие события;
  * (31.07.2026) один файл держали ТРИ процесса — перекат на Windows не проходил никогда,
    и `RotatingFileHandler` девять суток молча выбрасывал КАЖДУЮ строку `pricing.log`.
"""

import ast
import os
import logging
import tempfile
import unittest

import log_setup

REPO = os.path.dirname(os.path.abspath(__file__))


class TestTestContextDetection(unittest.TestCase):
    def test_env_switches(self):
        for env in ({"TURBOBABY_TEST_LOGS": "1"}, {"TESTING": "1"},
                    {"PYTEST_CURRENT_TEST": "test_x (call)"}):
            self.assertTrue(log_setup.is_test_context(env), env)

    def test_empty_and_zero_are_not_test(self):
        for env in ({}, {"TESTING": "0"}, {"TURBOBABY_TEST_LOGS": ""}, {"TESTING": "   "}):
            self.assertFalse(log_setup.is_test_context(env), env)

    def test_entrypoint_test_file_is_test_runner(self):
        """ДЫРА 25.07.2026, симметричная серверной: `python test_pc_orchestrator.py` НАПРЯМУЮ
        (без -m unittest) не взводит ни TESTING, ни PYTEST_CURRENT_TEST — и строки теста уходили
        в БОЕВОЙ лог. Запуск файла test_*.py — тоже способ запустить тест; на боевом демоне
        ложного срабатывания нет, его входной файл называется иначе."""
        import sys as _s
        import types as _t
        old_argv, old_main = _s.argv[:], _s.modules.get("__main__")
        try:
            _s.modules["__main__"] = _t.ModuleType("__fake_main__")   # как при прямом запуске
            _s.argv = ["D:\\turbobaby-bot\\test_pc_orchestrator.py"]
            self.assertTrue(log_setup._started_as_test_runner())
            _s.argv = ["D:\\turbobaby-bot\\pc_orchestrator.py"]
            self.assertFalse(log_setup._started_as_test_runner())
        finally:
            _s.argv = old_argv
            if old_main is not None:
                _s.modules["__main__"] = old_main

    def test_daemon_importing_unittest_is_not_test_context(self):
        """РЕГРЕСС: демон импортирует gate_selective, который гоняет тесты. Признак «unittest
        есть в sys.modules» увёл бы БОЕВОЙ pc_orchestrator.log в temp — живая диагностика
        исчезла бы ровно тогда, когда она нужнее всего. Признак — способ запуска процесса."""
        import unittest as _ut                      # noqa: F401  (именно он и есть в sys.modules)
        self.assertFalse(log_setup.is_test_context({}))


class TestLogPathSeparation(unittest.TestCase):
    """ГОЛДЕН: под тестом боевой файл не адресуется вообще."""

    LIVE = ("pretool_guard.log", "pc_orchestrator.log", "pc_agent.log",
            "userbot.log", "pricing.log", "rc_remote_control.log")

    def test_test_context_redirects_out_of_repo(self):
        env = {"TURBOBABY_TEST_LOGS": "1"}
        for name in self.LIVE:
            p = log_setup.log_path(os.path.join(REPO, name), env)
            self.assertNotEqual(os.path.normcase(p),
                                os.path.normcase(os.path.join(REPO, name)), name)
            self.assertTrue(os.path.basename(p).startswith(log_setup.TEST_PREFIX), p)
            self.assertNotEqual(os.path.normcase(os.path.dirname(p)),
                                os.path.normcase(REPO), name)

    def test_prod_context_keeps_repo_path(self):
        for name in self.LIVE:
            p = log_setup.log_path(os.path.join(REPO, name), {})
            self.assertEqual(os.path.normcase(p),
                             os.path.normcase(os.path.join(REPO, name)), name)

    def test_bare_name_resolves_next_to_module_in_prod(self):
        self.assertEqual(os.path.normcase(log_setup.log_path("moderation_bot.log", {})),
                         os.path.normcase(os.path.join(REPO, "moderation_bot.log")))


class TestRotateIfNeeded(unittest.TestCase):
    """Ротация для писателей БЕЗ logging (гард пишет строку через open(..., 'a'))."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="turbobaby_rot_")
        self.p = os.path.join(self.dir, "x.log")

    def tearDown(self):
        for f in os.listdir(self.dir):
            try:
                os.remove(os.path.join(self.dir, f))
            except OSError:
                pass
        os.rmdir(self.dir)

    def _write(self, n):
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("x" * n)

    def test_under_limit_does_not_rotate(self):
        self._write(100)
        self.assertFalse(log_setup.rotate_if_needed(self.p, limit=1000))
        self.assertTrue(os.path.isfile(self.p))
        self.assertFalse(os.path.isfile(self.p + ".1"))

    def test_over_limit_rotates_to_dot_one(self):
        self._write(2000)
        self.assertTrue(log_setup.rotate_if_needed(self.p, limit=1000))
        self.assertFalse(os.path.isfile(self.p))          # боевой файл начнётся заново
        self.assertEqual(os.path.getsize(self.p + ".1"), 2000)

    def test_backups_are_capped_and_oldest_dropped(self):
        """ГОЛДЕН потолка: при keep=2 файлов не больше трёх (x.log.1, x.log.2 + текущий),
        самый старый выбывает — иначе «ротация» просто размазала бы рост по диску."""
        for gen in range(5):
            self._write(2000)
            log_setup.rotate_if_needed(self.p, limit=1000, keep=2)
        self.assertTrue(os.path.isfile(self.p + ".1"))
        self.assertTrue(os.path.isfile(self.p + ".2"))
        self.assertFalse(os.path.isfile(self.p + ".3"))

    def test_missing_file_is_noop(self):
        self.assertFalse(log_setup.rotate_if_needed(self.p, limit=10))


class TestRotatingHandler(unittest.TestCase):
    def test_handler_rotates_and_caps_total(self):
        d = tempfile.mkdtemp(prefix="turbobaby_rh_")
        p = os.path.join(d, "h.log")
        os.environ["LOG_MAX_BYTES"] = "2000"
        os.environ["LOG_BACKUPS"] = "2"
        try:
            h = log_setup.rotating_handler(p, env={})     # env={} → боевая ветка, путь как задан
            self.assertIsNotNone(h)
            lg = logging.getLogger("turbobaby_test_rot")
            lg.propagate = False
            lg.setLevel(logging.INFO)
            lg.addHandler(h)
            for i in range(400):
                lg.info("строка %d %s", i, "y" * 60)
            h.close()
            lg.removeHandler(h)
            # ВАЖНО: имена берём от РЕАЛЬНОГО файла хендлера, а не от «h.log». С 31.07 хендлер
            # разводит файл по владельцу процесса, и жёсткий префикс «h.log» дал бы пустой
            # список — тест зеленел бы, ничего не проверяя (мок перестал задевать ветку).
            base = os.path.basename(h.baseFilename)
            self.assertTrue(os.path.isfile(os.path.join(d, base)), base)
            files = [f for f in os.listdir(d) if f.startswith(base)]
            self.assertLessEqual(len(files), 3, files)     # текущий + 2 бэкапа, не больше
            total = sum(os.path.getsize(os.path.join(d, f)) for f in files)
            self.assertLess(total, 2000 * 4)               # потолок = MAX × (BACKUPS+1)
        finally:
            os.environ.pop("LOG_MAX_BYTES", None)
            os.environ.pop("LOG_BACKUPS", None)
            for f in os.listdir(d):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
            os.rmdir(d)

    def test_defaults_are_sane(self):
        self.assertEqual(log_setup.max_bytes(), log_setup.DEFAULT_MAX_BYTES)
        self.assertEqual(log_setup.backups(), log_setup.DEFAULT_BACKUPS)

    def test_broken_env_falls_back_to_defaults(self):
        os.environ["LOG_MAX_BYTES"] = "не-число"
        try:
            self.assertEqual(log_setup.max_bytes(), log_setup.DEFAULT_MAX_BYTES)
        finally:
            os.environ.pop("LOG_MAX_BYTES", None)


class TestLiveModulesWired(unittest.TestCase):
    """ГОЛДЕН подключения: модули, чьи логи распухли, обязаны иметь РОТИРУЕМЫЙ хендлер.
    Проверяем по исходнику — импортировать боевые модули ради этого нельзя (потянут сеть/токены)."""

    WIRED = ("pc_orchestrator.py", "pc_agent.py", "userbot_listen.py", "moderation_bot.py",
             "pricing.py", "delivery.py", "dispatch_notify.py", "rc_supervisor.py")

    def test_no_module_uses_bare_filehandler_as_only_path(self):
        for name in self.WIRED:
            with open(os.path.join(REPO, name), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("log_setup", src, "%s не подключён к ротации" % name)
            self.assertIn("rotating_handler", src, "%s не использует ротируемый хендлер" % name)


class TestOwnerSplit(unittest.TestCase):
    """БОЛЬ 3 (31.07.2026): `pricing.log` держали ТРИ процесса (userbot_listen, moderation_bot,
    pc_orchestrator — все тянут `suggest`), rename открытого файла на Windows не проходит, и
    файл замер на 23 МБ со штампом 22.07 21:39 — девять суток строк котировок в никуда."""

    def test_owner_tag_from_entrypoint_name(self):
        self.assertEqual(log_setup.owner_tag("D:\\turbobaby-bot\\userbot_listen.py", env={}),
                         "userbot_listen")
        self.assertEqual(log_setup.owner_tag("/opt/x/pc_orchestrator.py", env={}),
                         "pc_orchestrator")

    def test_owner_tag_degrades_to_misc(self):
        """`python -c …`, `python -m unittest`, пустой argv — владельца назвать нельзя."""
        for argv0 in ("", "-c", "-m", "C:\\Python\\lib\\unittest\\__main__.py"):
            self.assertEqual(log_setup.owner_tag(argv0, env={}), "misc", repr(argv0))

    def test_owner_tag_env_override_wins(self):
        self.assertEqual(log_setup.owner_tag("whatever.py", env={"TURBOBABY_LOG_OWNER": "rc"}),
                         "rc")

    def test_owner_tag_is_sanitized(self):
        self.assertEqual(log_setup.owner_tag("D:\\x\\Мой Скрипт v2.py", env={}), "v2")

    def test_shared_log_gets_its_own_file_per_process(self):
        """ГОЛДЕН лечения: у трёх процессов — три РАЗНЫХ файла, спорить за rename некому."""
        got = [log_setup.handler_name("pricing.log", owner=o)
               for o in ("userbot_listen", "moderation_bot", "pc_orchestrator")]
        self.assertEqual(got, ["pricing.userbot_listen.log", "pricing.moderation_bot.log",
                               "pricing.pc_orchestrator.log"])
        self.assertEqual(len(set(got)), 3)

    def test_owning_process_keeps_canonical_name(self):
        """Хозяин пишет ПРЕЖНИЙ файл — иначе поехали бы все читатели логов и вся документация."""
        for name, owner in (("pc_agent.log", "pc_agent"),
                            ("pc_orchestrator.log", "pc_orchestrator"),
                            ("moderation_bot.log", "moderation_bot"),
                            ("session_watch.log", "session_watch"),
                            ("userbot.log", "userbot_listen"),           # объявлен в LOG_OWNERS
                            ("rc_remote_control.log", "rc_supervisor")):  # тоже
            self.assertEqual(log_setup.handler_name(name, owner=owner), name)

    def test_foreign_process_never_takes_canonical_name(self):
        """Обратная сторона: чужой процесс, импортировавший модуль, канон НЕ занимает — иначе
        демон снова не смог бы перекатить свой лог. Живой случай: moderation_core лениво тянет
        pc_orchestrator, lesson_router — тоже."""
        self.assertEqual(log_setup.handler_name("pc_orchestrator.log", owner="moderation_bot"),
                         "pc_orchestrator.moderation_bot.log")
        self.assertEqual(log_setup.handler_name("dispatch_notify.log", owner="pc_orchestrator"),
                         "dispatch_notify.pc_orchestrator.log")

    def test_handler_path_keeps_directory_and_test_redirect(self):
        p = log_setup.handler_path(os.path.join(REPO, "pricing.log"), env={}, owner="userbot_listen")
        self.assertEqual(os.path.normcase(p),
                         os.path.normcase(os.path.join(REPO, "pricing.userbot_listen.log")))
        t = log_setup.handler_path("pricing.log", env={"TURBOBABY_TEST_LOGS": "1"},
                                   owner="userbot_listen")
        self.assertEqual(os.path.basename(t),
                         log_setup.TEST_PREFIX + "pricing.userbot_listen.log")
        self.assertNotEqual(os.path.normcase(os.path.dirname(t)), os.path.normcase(REPO))

    def test_append_writers_keep_one_shared_file(self):
        """`log_path` НЕ разводит: гард (`open(p,'a')`) и дозапись метрик в pc_orchestrator.log
        файл не держат и ротации не мешают. Разведи их — и единая лента аудита гарда рассыпалась
        бы на файл-на-процесс без всякой пользы."""
        for name in ("pretool_guard.log", "pc_orchestrator.log"):
            self.assertEqual(os.path.normcase(log_setup.log_path(os.path.join(REPO, name), {})),
                             os.path.normcase(os.path.join(REPO, name)), name)


class TestRolloverFailureIsLoud(unittest.TestCase):
    """БОЛЬ 3, вторая половина: отказ переката НЕ проглатывается и НЕ стоит записей.
    Прежде `RotatingFileHandler.emit` ловил WinError 32 и терял строку — молча."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="turbobaby_rfail_")
        self.p = os.path.join(self.dir, "boom.log")
        del log_setup._ROTATE_FAILURES[:]
        os.environ["LOG_MAX_BYTES"] = "300"
        os.environ["TURBOBABY_TEST_LOGS"] = "1"      # файл-сигнал уедет в temp, не в репо

    def tearDown(self):
        for k in ("LOG_MAX_BYTES", "LOG_ROLLOVER_RETRY_SEC", "TURBOBABY_TEST_LOGS"):
            os.environ.pop(k, None)
        del log_setup._ROTATE_FAILURES[:]
        for f in os.listdir(self.dir):
            try:
                os.remove(os.path.join(self.dir, f))
            except OSError:
                pass
        os.rmdir(self.dir)

    def _handler(self):
        """Хендлер на ЗАДАННЫЙ путь (env={} → без temp-редиректа) с заведомо падающим rename —
        так на Windows ведёт себя файл, открытый другим процессом."""
        h = log_setup.rotating_handler(self.p, env={}, owner="fixture")
        self.assertIsNotNone(h)
        h.rotate = self._explode
        return h

    @staticmethod
    def _explode(src, dst):
        raise PermissionError(32, "The process cannot access the file because it is being "
                                  "used by another process")

    def test_records_survive_failed_rollover(self):
        """ГЛАВНОЕ: строка ложится в файл ДАЖЕ когда перекат сорвался. Растущий лог лучше немого."""
        h = self._handler()
        lg = logging.getLogger("turbobaby_test_rfail_1")
        lg.propagate, lg.handlers = False, [h]
        lg.setLevel(logging.INFO)
        for i in range(40):
            lg.info("строка %d %s", i, "z" * 40)
        h.close()
        with open(h.baseFilename, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("строка 39", body, "запись потеряна на сорвавшемся перекате")
        self.assertIn("ROTATE-FAIL", body, "в самом логе нет отметки об отказе переката")

    def test_failure_reaches_signal_file_and_memory(self):
        h = self._handler()
        lg = logging.getLogger("turbobaby_test_rfail_2")
        lg.propagate, lg.handlers = False, [h]
        lg.setLevel(logging.INFO)
        for i in range(40):
            lg.info("строка %d %s", i, "z" * 40)
        h.close()
        self.assertTrue(log_setup.rotation_failures(), "отказ переката не виден в процессе")
        sig = log_setup.log_path(log_setup.ROTATE_ERROR_LOG)
        self.assertTrue(os.path.isfile(sig), sig)
        with open(sig, encoding="utf-8") as f:
            self.assertIn("ROTATE-FAIL", f.read())
        os.remove(sig)

    def test_retry_is_backed_off_not_hammered(self):
        """Без паузы rename дёргался бы на КАЖДОЙ строке: сорвавшийся перекат «пора» не отменяет."""
        os.environ["LOG_ROLLOVER_RETRY_SEC"] = "3600"
        calls = []

        def counting(src, dst):
            calls.append(1)
            self._explode(src, dst)

        h = log_setup.rotating_handler(self.p, env={}, owner="fixture")
        h.rotate = counting
        lg = logging.getLogger("turbobaby_test_rfail_3")
        lg.propagate, lg.handlers = False, [h]
        lg.setLevel(logging.INFO)
        for i in range(60):
            lg.info("строка %d %s", i, "z" * 40)
        h.close()
        self.assertEqual(len(calls), 1, "перекат повторяется без паузы: %d попыток" % len(calls))

    def test_rotate_if_needed_reports_failure(self):
        """Писатели БЕЗ logging (гард) — тот же контракт: False по-прежнему, но не молча."""
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("x" * 2000)
        real = os.replace

        def boom(src, dst):
            raise PermissionError(32, "used by another process")

        os.replace = boom
        try:
            self.assertFalse(log_setup.rotate_if_needed(self.p, limit=1000))
        finally:
            os.replace = real
        self.assertTrue(log_setup.rotation_failures(), "отказ append-ротации проглочен молча")
        sig = log_setup.log_path(log_setup.ROTATE_ERROR_LOG)
        if os.path.isfile(sig):
            os.remove(sig)


# ── статический разбор дерева: КТО ещё болен тем же ──────────────────────────────────────────

_SKIP_DIRS = {"manager-bot", "tmp", "venv", "docs", "fixtures", "data", ".git", ".claude",
              "__pycache__", "whatsapp-bot", "node_modules"}


def _repo_modules():
    """Питон-модули БОЕВОГО дерева (корень репо). Тесты исключены сознательно: под тестом любой
    лог и так уезжает в temp, а как «владелец» test_*.py только зашумил бы граф."""
    out = {}
    for fn in os.listdir(REPO):
        if not fn.endswith(".py") or fn.startswith("test_"):
            continue
        if os.path.isdir(os.path.join(REPO, fn)):
            continue
        try:
            with open(os.path.join(REPO, fn), encoding="utf-8") as f:
                out[fn[:-3]] = ast.parse(f.read(), filename=fn)
        except (OSError, SyntaxError):
            continue
    return out


def _imports(tree, known):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names & known


def _is_entrypoint(tree):
    """Файл, который запускают как процесс: есть `if __name__ == "__main__"`."""
    for node in tree.body:
        if isinstance(node, ast.If):
            src = ast.dump(node.test)
            if "__name__" in src and "__main__" in src:
                return True
    return False


def _handler_logs(tree):
    """Имена логов, на которые модуль вешает ДЕРЖАЩИЙ хендлер (`rotating_handler(...)`).
    Аргумент бывает константой-переменной (NOTIFY_LOG, LOG_PATH, AGENT_LOG) — резолвим по
    модульным присваиваниям, собирая из них любую строку, оканчивающуюся на `.log`."""
    const = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            lit = [s.value for s in ast.walk(node.value)
                   if isinstance(s, ast.Constant) and isinstance(s.value, str)
                   and s.value.lower().endswith(".log")]
            if lit:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        const[tgt.id] = os.path.basename(lit[-1])
    logs = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != "rotating_handler" or not node.args:
            continue
        arg = node.args[0]
        found = [s.value for s in ast.walk(arg)
                 if isinstance(s, ast.Constant) and isinstance(s.value, str)
                 and s.value.lower().endswith(".log")]
        if found:
            logs.add(os.path.basename(found[-1]))
        elif isinstance(arg, ast.Name) and arg.id in const:
            logs.add(const[arg.id])
    return logs


class TestCanonicalLogNameHasOneOwner(unittest.TestCase):
    """АВТОПРОВЕРКА БОЛЕЗНИ, а не разовый список файлов.

    Разбираем дерево: кто вешает ротируемый хендлер, кто кого импортирует, кто входная точка.
    Инвариант: КАНОНИЧЕСКОЕ (без суффикса) имя лога вправе занимать не больше ОДНОГО процесса.
    Два и больше — это ровно диагноз `pricing.log`: перекат не пройдёт ни у кого, и узнается об
    этом через девять суток по замершему mtime. Тест ловит и новый модуль-библиотеку с
    хендлером, и ошибку в LOG_OWNERS."""

    def _graph(self):
        mods = _repo_modules()
        known = set(mods)
        edges = {m: _imports(t, known) for m, t in mods.items()}
        entries = [m for m, t in mods.items() if _is_entrypoint(t)]
        return mods, edges, entries

    @staticmethod
    def _reaches(entry, target, edges):
        seen, stack = set(), [entry]
        while stack:
            cur = stack.pop()
            if cur == target and cur != entry:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(edges.get(cur, ()))
        return target == entry

    def test_no_log_is_claimed_by_two_processes(self):
        mods, edges, entries = self._graph()
        self.assertIn("pricing", mods, "дерево разобрано неверно: pricing не найден")
        self.assertIn("userbot_listen", entries, "дерево разобрано неверно: нет входных точек")
        sick = []
        for mod, tree in mods.items():
            for logname in _handler_logs(tree):
                owners = [e for e in entries if self._reaches(e, mod, edges)]
                canon = [e for e in owners if log_setup.handler_name(logname, owner=e) == logname]
                if len(canon) > 1:
                    sick.append("%s (%s): канон занимают %s" % (logname, mod, sorted(canon)))
        self.assertEqual(sick, [], "один файл — несколько держателей, перекат не пройдёт:\n"
                                   + "\n".join(sick))

    def test_pricing_log_is_reached_by_three_processes(self):
        """Фиксация ДИАГНОЗА, чтобы он не считался легендой: pricing действительно живёт в трёх
        процессах, и канон `pricing.log` теперь не занимает ни один из них."""
        mods, edges, entries = self._graph()
        live = {"userbot_listen", "moderation_bot", "pc_orchestrator"}
        reach = {e for e in entries if self._reaches(e, "pricing", edges)}
        self.assertTrue(live <= reach, "ожидали три живых процесса, дошли: %s" % sorted(reach))
        for e in live:
            self.assertNotEqual(log_setup.handler_name("pricing.log", owner=e), "pricing.log", e)

    def test_declared_owners_are_real_entrypoints(self):
        """LOG_OWNERS не должен ссылаться на несуществующий скрипт — иначе канон не занят никем,
        и «прежнее имя лога» тихо перестанет существовать."""
        _, _, entries = self._graph()
        for logname, owner in log_setup.LOG_OWNERS.items():
            self.assertIn(owner, entries, "%s → %s: такой входной точки нет" % (logname, owner))


if __name__ == "__main__":
    unittest.main()
