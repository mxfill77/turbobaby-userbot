# -*- coding: utf-8 -*-
"""
test_log_setup.py — голдены ротации логов и разведения тестовых/боевых логов.

Оба класса дефектов — живой факт аудита 22:27:
  * ротации не было НИГДЕ (голый FileHandler): pc_agent.log 28.7 МБ, pricing.log 22.2 МБ,
    pc_orchestrator.log 11.2 МБ — при 0.4 ГБ свободных на C:;
  * тесты писали в БОЕВОЙ pretool_guard.log: фикстуры прогона 21:39 (`clasp push`,
    `sqlite3 bookings.db "select 1"`, пустая команда) неотличимы от реальных действий владельца
    и попали в разбор «последних 20 Allow» как настоящие события.
"""

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
            files = [f for f in os.listdir(d) if f.startswith("h.log")]
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


if __name__ == "__main__":
    unittest.main()
