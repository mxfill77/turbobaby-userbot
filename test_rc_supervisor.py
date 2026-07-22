# -*- coding: utf-8 -*-
"""
test_rc_supervisor.py — супервизор канала Claude Code Remote Control (задача pc_remote_control).
Реального claude НЕ запускаем: резолвер и цикл принимают инъекции.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_rc_supervisor -v
"""

import os
import unittest

import rc_supervisor as rc


class TestVersionResolve(unittest.TestCase):
    """Живой прокол 22.07: выбор «новейшей» установки по mtime каталога отдал СТАРУЮ 2.1.215 —
    у 2.1.215 и 2.1.217 совпал LastWriteTime. Сортируем по ЧИСЛОВОМУ ключу версии."""

    BASE = r"C:\Users\u\AppData\Roaming\Claude\claude-code"

    def _globber(self, names):
        return lambda pat: [os.path.join(self.BASE, n) for n in names]

    def test_picks_highest_numeric_version(self):
        exe = rc.newest_versioned_claude(bases=[self.BASE],
                                         globber=self._globber(["2.1.215", "2.1.217"]),
                                         isfile=lambda p: True)
        self.assertTrue(exe.endswith(os.path.join("2.1.217", "claude.exe")), exe)

    def test_string_sort_trap(self):
        # лексикографически '2.1.99' > '2.1.217' — ключ обязан быть числовым
        exe = rc.newest_versioned_claude(bases=[self.BASE],
                                         globber=self._globber(["2.1.99", "2.1.217"]),
                                         isfile=lambda p: True)
        self.assertIn("2.1.217", exe)

    def test_dir_without_exe_ignored(self):
        exe = rc.newest_versioned_claude(
            bases=[self.BASE], globber=self._globber(["2.1.215", "2.1.217"]),
            isfile=lambda p: "2.1.215" in p)          # у 2.1.217 бинаря нет (битая распаковка)
        self.assertIn("2.1.215", exe)

    def test_empty_base_is_none(self):
        self.assertIsNone(rc.newest_versioned_claude(bases=[self.BASE],
                                                     globber=lambda pat: [],
                                                     isfile=lambda p: True))

    def test_ver_key(self):
        self.assertEqual(rc._ver_key("2.1.217"), (2, 1, 217))
        self.assertEqual(rc._ver_key("мусор"), (0,))

    def test_msix_base_included(self):
        # Планировщик НЕ видит виртуальный редирект Roaming\Claude у Store/MSIX-установки —
        # реальная база в LocalAppData\Packages\Claude_* обязана быть в списке (класс демона)
        bases = rc.claude_base_dirs()
        self.assertTrue(any("claude-code" in b for b in bases))
        self.assertTrue(bases[0].endswith(os.path.join("Claude", "claude-code")))

    def test_path_shim_wins(self):
        exe = rc.resolve_claude(which=lambda n: r"C:\shim\claude.cmd",
                                isfile=lambda p: True,
                                newest=lambda: r"C:\ver\claude.exe")
        self.assertEqual(exe, r"C:\shim\claude.cmd")

    def test_versioned_used_when_no_shim(self):
        exe = rc.resolve_claude(which=lambda n: None,
                                isfile=lambda p: False,
                                newest=lambda: r"C:\ver\claude.exe")
        self.assertEqual(exe, r"C:\ver\claude.exe")

    def test_none_when_nothing_found(self):
        self.assertIsNone(rc.resolve_claude(which=lambda n: None,
                                            isfile=lambda p: False,
                                            newest=lambda: None))


class TestSupervisorLoop(unittest.TestCase):
    """Канал обязан жить ВСЕГДА: RestartOnFailure Планировщика даёт лишь 3 попытки, поэтому
    подъём сессии после каждого выхода — здесь, в вечном цикле."""

    def test_restarts_after_each_exit(self):
        runs, slept = [], []

        class _P:
            returncode = 0

        def runner(cmd, **kw):
            runs.append(cmd)
            return _P()

        rc.main(resolver=lambda: r"C:\ver\claude.exe", runner=runner,
                sleeper=slept.append, singleton=lambda: (True, None), rounds=3)
        self.assertEqual(len(runs), 3)                       # три подъёма подряд
        self.assertEqual(runs[0][1:], ["--remote-control", rc.SESSION_NAME])
        self.assertEqual(slept, [rc.RESTART_DELAY] * 3)      # пауза между подъёмами

    def test_crash_does_not_break_loop(self):
        slept = []

        def runner(cmd, **kw):
            raise OSError("сессия упала")

        rc.main(resolver=lambda: r"C:\ver\claude.exe", runner=runner,
                sleeper=slept.append, singleton=lambda: (True, None), rounds=2)
        self.assertEqual(slept, [rc.RESTART_DELAY] * 2)      # падение = не выход из цикла

    def test_missing_claude_waits_and_retries(self):
        runs, slept = [], []
        rc.main(resolver=lambda: None, runner=lambda *a, **k: runs.append(a),
                sleeper=slept.append, singleton=lambda: (True, None), rounds=2)
        self.assertEqual(runs, [])                           # без бинаря ничего не спавним
        self.assertEqual(slept, [rc.RESTART_DELAY] * 2)

    def test_second_instance_exits_without_spawning(self):
        # синглтон: вторая копия (ручной запуск поверх задачи) НЕ поднимает второе устройство
        runs = []
        rc.main(resolver=lambda: r"C:\ver\claude.exe", runner=lambda *a, **k: runs.append(a),
                sleeper=lambda s: None, singleton=lambda: (False, None), rounds=5)
        self.assertEqual(runs, [])


class TestDebugArgs(unittest.TestCase):
    """Диагностика канала: stdout/stderr сессии перехватить нельзя (перенаправление убивает
    TTY), поэтому просим сам CLI писать отладку в файл. Включатель — файл-флаг: env для задачи
    Планировщика без прав администратора не задать, а флаг кладётся обычным пользователем."""

    def test_off_without_flag(self):
        self.assertEqual(rc.debug_args(exists=lambda p: False), [])

    def test_on_with_flag(self):
        args = rc.debug_args(exists=lambda p: True)
        self.assertEqual(args[0], "--debug-file")
        self.assertTrue(args[1].endswith(".log"))     # под *.log в .gitignore — секреты не уедут

    def test_flag_path_is_repo_local(self):
        self.assertTrue(rc.DEBUG_FLAG.startswith(rc.REPO))
        self.assertTrue(rc.DEBUG_LOG.startswith(rc.REPO))

    def test_run_once_appends_debug_args(self):
        seen = {}

        class _P:
            returncode = 0

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            return _P()

        rc.run_once(r"C:\ver\claude.exe", runner=runner, dbg=["--debug-file", "x.log"])
        self.assertEqual(seen["cmd"][-2:], ["--debug-file", "x.log"])
        self.assertEqual(seen["cmd"][1:3], ["--remote-control", rc.SESSION_NAME])

    def test_run_once_clean_without_debug(self):
        seen = {}

        class _P:
            returncode = 0

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            return _P()

        rc.run_once(r"C:\ver\claude.exe", runner=runner, dbg=[])
        self.assertEqual(len(seen["cmd"]), 3)         # ничего лишнего в боевом запуске


class TestNoWindowKilling(unittest.TestCase):
    """Интерактивной сессии нужен ЖИВОЙ TTY: run_once НЕ имеет права перенаправлять stdio или
    гасить консоль (CREATE_NO_WINDOW/pythonw). Скрытость даёт wscript (WshShell.Run …, 0)."""

    def test_run_once_keeps_console_and_stdio(self):
        seen = {}

        class _P:
            returncode = 7

        def runner(cmd, **kw):
            seen.update(kw)
            return _P()

        self.assertEqual(rc.run_once(r"C:\ver\claude.exe", runner=runner), 7)
        self.assertNotIn("creationflags", seen)              # консоль не гасим
        for k in ("stdout", "stderr", "stdin"):
            self.assertNotIn(k, seen)                        # stdio не перенаправляем
        self.assertEqual(seen.get("cwd"), rc.REPO)


class TestPreflight(unittest.TestCase):
    """ГОЛДЕН живого прокола 22.07: задача Running, процесс claude жив — а канал МЁРТВ.
    `claude auth status` при этом отвечал loggedIn=true/max и ничего не подозревал; правду
    сказал только `claude doctor`: вход выдан БЕЗ скоупа user:profile, мост не поднимется.
    Значит пре-флайт обязан смотреть doctor и НЕ плодить пустых процессов."""

    # дословный фрагмент живого вывода doctor (правило-класс «мок = живой формат»)
    DOCTOR_BAD = (
        "Remote Control\n"
        "Remote Control requires a claude.ai subscription. Run claude auth login to sign in "
        "with your claude.ai account.\n"
        "- Not signed in to claude.ai\n"
        "- claude.ai subscription auth not active\n"
        "- Sign-in is missing the user:profile scope\n"
        "\n4 warnings found\n"
    )
    DOCTOR_OK = "Remote Control\n- Connected\n\n0 warnings found\n"

    class _P:
        def __init__(self, out="", err="", rc=0):
            self.stdout, self.stderr, self.returncode = out, err, rc

    def test_blocked_login_detected(self):
        ok, detail = rc.rc_ready(r"C:\c.exe", runner=lambda *a, **k: self._P(self.DOCTOR_BAD))
        self.assertFalse(ok)
        self.assertIn("user:profile", detail)

    def test_healthy_login_passes(self):
        ok, detail = rc.rc_ready(r"C:\c.exe", runner=lambda *a, **k: self._P(self.DOCTOR_OK))
        self.assertTrue(ok)

    def test_fail_open_when_doctor_breaks(self):
        def boom(*a, **k):
            raise OSError("doctor не запустился")
        ok, detail = rc.rc_ready(r"C:\c.exe", runner=boom)
        self.assertTrue(ok)                       # гард не смеет сам стать точкой отказа
        self.assertIn("не блокируем", detail)

    def test_blockers_found_in_stderr_too(self):
        ok, _ = rc.rc_ready(r"C:\c.exe", runner=lambda *a, **k: self._P("", self.DOCTOR_BAD))
        self.assertFalse(ok)

    def test_no_session_spawned_when_not_ready(self):
        runs, cards, slept = [], [], []
        rc.main(resolver=lambda: r"C:\ver\claude.exe",
                runner=lambda *a, **k: runs.append(a),
                sleeper=slept.append, singleton=lambda: (True, None),
                ready=lambda c: (False, "Sign-in is missing the user:profile scope"),
                notifier=cards.append, rounds=3)
        self.assertEqual(runs, [])                # НИ ОДНОГО пустого процесса-зомби
        self.assertEqual(len(cards), 1)           # карточка владельцу ровно одна, не спам
        self.assertIn("auth login", cards[0])
        self.assertEqual(slept, [rc.RESTART_DELAY] * 3)

    def test_session_spawned_when_ready(self):
        runs = []

        class _R:
            returncode = 0

        def runner(cmd, **kw):
            runs.append(cmd)
            return _R()

        rc.main(resolver=lambda: r"C:\ver\claude.exe", runner=runner,
                sleeper=lambda s: None, singleton=lambda: (True, None),
                ready=lambda c: (True, "ок"), notifier=lambda t: None, rounds=2)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0][1:3], ["--remote-control", rc.SESSION_NAME])

    def test_card_repeats_after_recovery(self):
        # починили → сломалось снова: владелец должен узнать повторно (флаг сбрасывается)
        seq = iter([(False, "нет входа"), (True, "ок"), (False, "нет входа")])
        cards = []

        class _R:
            returncode = 0

        rc.main(resolver=lambda: r"C:\ver\claude.exe", runner=lambda *a, **k: _R(),
                sleeper=lambda s: None, singleton=lambda: (True, None),
                ready=lambda c: next(seq), notifier=cards.append, rounds=3)
        self.assertEqual(len(cards), 2)


class TestLauncherFiles(unittest.TestCase):
    """Цепочка запуска обязана остаться скрытой И консольной: wscript → VBS → venv-python."""

    HERE = os.path.dirname(os.path.abspath(__file__))

    def _read(self, name):
        with open(os.path.join(self.HERE, name), encoding="utf-8") as f:
            return f.read()

    def test_vbs_runs_hidden_via_console_python(self):
        vbs = self._read("rc_remote_control.vbs")
        self.assertIn("sh.Run cmd, 0, True", vbs)            # 0 = окно скрыто
        # проверяем ИМЕННО командную строку, а не пояснения в комментариях
        cmdline = [l for l in vbs.splitlines() if l.strip().startswith("cmd =")][0]
        self.assertIn("rc_supervisor.py", cmdline)
        self.assertIn(r"venv\Scripts\python.exe", cmdline)   # НЕ pythonw: ему нечем дать TTY
        self.assertNotIn("pythonw", cmdline)

    def test_task_xml_matches_launcher(self):
        xml = self._read("pc_remote_control.task.xml")
        self.assertIn("wscript.exe", xml)
        self.assertIn("rc_remote_control.vbs", xml)
        self.assertIn("<LogonTrigger />", xml)               # автозапуск при входе в систему
        self.assertIn("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", xml)   # без лимита времени
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", xml)
        self.assertIn("<RestartOnFailure>", xml)
        self.assertIn(r"<WorkingDirectory>D:\turbobaby-bot</WorkingDirectory>", xml)


if __name__ == "__main__":
    unittest.main(verbosity=2)
