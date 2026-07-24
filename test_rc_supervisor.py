# -*- coding: utf-8 -*-
"""
test_rc_supervisor.py — ДВУХРЕЖИМНЫЙ супервизор канала Claude Code Remote Control
(задача pc_remote_control). Реального claude НЕ запускаем: резолвер, спавн, проба живости и цикл
принимают инъекции. Запуск:
    D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_rc_supervisor -v
"""

import os
import tempfile
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

    def test_native_shim_preferred_over_versioned(self):
        # Живой прокол 24.07: нативный ВЕЧНЫЙ шим ~/.local/bin/claude.exe (2.1.218) есть, но
        # .local\bin НЕ в PATH → which пуст. Резолвер обязан взять вечный шим, а НЕ протухающий
        # версионный Desktop-каталог (иначе RC навсегда завязан на версию Claude Desktop и
        # ловит WinError 2 на каждом апдейте — класс «CLAUDE_BIN стухшая версия»).
        native = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")
        exe = rc.resolve_claude(which=lambda n: None,
                                isfile=lambda p: p == native,
                                newest=lambda: r"C:\ver\claude.exe")
        self.assertEqual(exe, native)

    def test_none_when_nothing_found(self):
        self.assertIsNone(rc.resolve_claude(which=lambda n: None,
                                            isfile=lambda p: False,
                                            newest=lambda: None))


class TestBuildCmd(unittest.TestCase):
    """ГОЛДЕН: обе ветки резолвятся на ШИМ и несут правильный режим + СВОЙ --debug-file.
    Сервер — постоянный Environment (устройство в списке Devices), именованный — сессии с телефона."""

    SHIM = r"C:\Users\u\.local\bin\claude.exe"

    def test_server_branch_cmd(self):
        # `claude rc` = постоянный сервер-Environment (регистрирует устройство)
        cmd = rc.build_cmd(self.SHIM, rc.SERVER_SPEC)
        self.assertEqual(cmd, [self.SHIM, "rc", "--debug-file", rc.SERVER_LOG])

    def test_named_branch_cmd(self):
        # `claude --remote-control <имя>` = именованная интерактивная сессия
        cmd = rc.build_cmd(self.SHIM, rc.NAMED_SPEC)
        self.assertEqual(cmd, [self.SHIM, "--remote-control", rc.SESSION_NAME,
                               "--debug-file", rc.NAMED_LOG])

    def test_both_branches_use_resolved_binary_and_own_log(self):
        s = rc.build_cmd(self.SHIM, rc.SERVER_SPEC)
        n = rc.build_cmd(self.SHIM, rc.NAMED_SPEC)
        self.assertEqual(s[0], self.SHIM)             # обе ветки — на резолвнутый бинарь (шим)
        self.assertEqual(n[0], self.SHIM)
        self.assertNotEqual(rc.SERVER_LOG, rc.NAMED_LOG)   # у каждой ветки СВОЙ лог живости
        self.assertIn("--debug-file", s)              # --debug-file ВСЕГДА (сигнал живости)
        self.assertIn("--debug-file", n)

    def test_verbose_only_when_asked(self):
        self.assertNotIn("--verbose", rc.build_cmd(self.SHIM, rc.SERVER_SPEC, verbose=False))
        self.assertIn("--verbose", rc.build_cmd(self.SHIM, rc.SERVER_SPEC, verbose=True))


class TestLiveness(unittest.TestCase):
    """ГОЛДЕН пробы живости (класс «0 TCP ≠ зомби»): единый предикат — канал жив, если лог СВЕЖ
    ИЛИ есть соединения; мёртв только когда НЕТ обоих. Простаивающий-но-живой (свежий лог, 0 TCP)
    не приговаривается, реально застрявший (старый лог, 0 TCP) — приговаривается."""

    MAX = 1200

    # --- единый предикат channel_alive ---
    def test_fresh_log_zero_tcp_is_alive(self):
        # ГОЛДЕН: свежий лог, 0 TCP → ЖИВ (иначе простаивающий канал ложно убивался бы)
        self.assertTrue(rc.channel_alive(age_sec=10, established=0, max_age=self.MAX))

    def test_old_log_zero_tcp_is_dead(self):
        # ГОЛДЕН: старый лог, 0 TCP → МЁРТВ (настоящий зомби ловится)
        self.assertFalse(rc.channel_alive(age_sec=9999, established=0, max_age=self.MAX))

    def test_old_log_with_connection_is_alive(self):
        # старый лог, но есть соединение → ЖИВ (соединение спасает при редком логе)
        self.assertTrue(rc.channel_alive(age_sec=9999, established=1, max_age=self.MAX))

    def test_fresh_log_with_connection_is_alive(self):
        self.assertTrue(rc.channel_alive(age_sec=5, established=3, max_age=self.MAX))

    def test_missing_log_zero_tcp_is_dead(self):
        # лога ещё/уже нет (None) и 0 соединений → МЁРТВ
        self.assertFalse(rc.channel_alive(age_sec=None, established=0, max_age=self.MAX))

    def test_boundary_age_equals_max_is_fresh(self):
        self.assertTrue(rc.channel_alive(age_sec=self.MAX, established=0, max_age=self.MAX))
        self.assertFalse(rc.channel_alive(age_sec=self.MAX + 1, established=0, max_age=self.MAX))

    # --- probe_alive: связка свежести лога + соединений ---
    def test_probe_fresh_log_zero_tcp_alive(self):
        # свежий лог (mtime = now-10), 0 соединений → ЖИВ
        ok = rc.probe_alive(rc.SERVER_SPEC, pid=4242,
                            now=1000.0, getmtime=lambda p: 990.0,
                            conns=lambda pid: 0, max_age=self.MAX)
        self.assertTrue(ok)

    def test_probe_old_log_zero_tcp_dead(self):
        # старый лог (mtime = now-9999), 0 соединений → МЁРТВ
        ok = rc.probe_alive(rc.SERVER_SPEC, pid=4242,
                            now=10000.0, getmtime=lambda p: 1.0,
                            conns=lambda pid: 0, max_age=self.MAX)
        self.assertFalse(ok)

    def test_probe_old_log_but_connected_alive(self):
        ok = rc.probe_alive(rc.NAMED_SPEC, pid=4242,
                            now=10000.0, getmtime=lambda p: 1.0,
                            conns=lambda pid: 2, max_age=self.MAX)
        self.assertTrue(ok)

    # --- established_conns: разбор ЖИВОГО формата netstat -ano (правило-класс «мок = живой формат») ---
    LIVE_NETSTAT = (
        "\r\n"
        "Active Connections\r\n"
        "\r\n"
        "  Proto  Local Address          Foreign Address        State           PID\r\n"
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1080\r\n"
        "  TCP    192.168.1.5:54120      160.79.104.10:443     ESTABLISHED     1304\r\n"
        "  TCP    192.168.1.5:54121      160.79.104.10:443     ESTABLISHED     1304\r\n"
        "  TCP    192.168.1.5:54999      140.82.113.25:443     ESTABLISHED     9999\r\n"
        "  TCP    127.0.0.1:49670        127.0.0.1:49671       ESTABLISHED     1304\r\n"
    )

    class _NP:
        def __init__(self, out):
            self.stdout = out

    def test_established_conns_counts_only_matching_pid(self):
        runner = lambda *a, **k: self._NP(self.LIVE_NETSTAT)
        self.assertEqual(rc.established_conns(1304, runner=runner), 3)   # три ESTABLISHED у 1304
        self.assertEqual(rc.established_conns(9999, runner=runner), 1)
        self.assertEqual(rc.established_conns(5, runner=runner), 0)      # LISTENING/чужие — мимо

    def test_established_conns_zero_when_netstat_breaks(self):
        def boom(*a, **k):
            raise OSError("netstat не запустился")
        self.assertEqual(rc.established_conns(1304, runner=boom), 0)


class _FakeProc:
    """Заглушка процесса ветки: poll() → None (жив) или код выхода; pid для лога."""

    def __init__(self, poll_value=None, pid=4242):
        self._poll = poll_value
        self.pid = pid

    def poll(self):
        return self._poll


class TestSuperviseBranch(unittest.TestCase):
    """Канал КАЖДОЙ ветки живёт ВСЕГДА и НЕЗАВИСИМО: рестарт по ВЫХОДУ процесса ЛИБО по зомби-пробе
    (процесс жив, но канал мёртв). Живой процесс НЕ рестартуется."""

    SHIM = r"C:\Users\u\.local\bin\claude.exe"

    def _run(self, spec, **kw):
        spawns, kills, slept = [], [], []
        pf = kw.pop("proc_factory", lambda: _FakeProc())

        def spawner(claude, sp):
            spawns.append((claude, sp["label"]))
            return pf()

        rc.supervise_branch(
            spec,
            resolver=kw.pop("resolver", lambda: self.SHIM),
            spawner=kw.pop("spawner", spawner),
            alive=kw.pop("alive", lambda sp, pid: True),
            gate=kw.pop("gate", lambda c: (True, "ok")),
            sleeper=slept.append,
            killer=kills.append,
            rounds=kw.pop("rounds", 1),
            checks=kw.pop("checks", 3),
            grace_checks=kw.pop("grace_checks", 0),
        )
        return spawns, kills, slept

    def test_live_process_not_restarted(self):
        # процесс жив (poll None) и канал жив (alive True) → за 3 проверки НИ киллов, ни новых спавнов
        spawns, kills, slept = self._run(rc.NAMED_SPEC, rounds=1, checks=3,
                                         proc_factory=lambda: _FakeProc(poll_value=None),
                                         alive=lambda sp, pid: True)
        self.assertEqual(len(spawns), 1)                 # подняли ОДИН раз и не трогали
        self.assertEqual(len(kills), 0)
        self.assertEqual(spawns[0][0], self.SHIM)        # ветка резолвится на ШИМ
        self.assertEqual(slept.count(rc.CHECK_INTERVAL), 3)   # три круга мониторинга

    def test_dead_exited_process_restarted(self):
        # процесс ВЫШЕЛ сам (poll=7) → рестарт каждый раунд; киллер не нужен (уже мёртв)
        spawns, kills, slept = self._run(rc.SERVER_SPEC, rounds=3, checks=5,
                                         proc_factory=lambda: _FakeProc(poll_value=7))
        self.assertEqual(len(spawns), 3)                 # три подъёма подряд
        self.assertEqual(len(kills), 0)

    def test_zombie_process_killed_and_restarted(self):
        # процесс жив (poll None), но канал мёртв (alive False) → гасим и рестартуем
        spawns, kills, slept = self._run(rc.NAMED_SPEC, rounds=2, checks=5, grace_checks=0,
                                         proc_factory=lambda: _FakeProc(poll_value=None),
                                         alive=lambda sp, pid: False)
        self.assertEqual(len(spawns), 2)
        self.assertEqual(len(kills), 2)                  # каждый зомби ПОГАШЕН перед рестартом

    def test_grace_period_skips_liveness(self):
        # в грейс-окне зомби-проба НЕ гоняется — свежий процесс не убивается на старте
        spawns, kills, slept = self._run(rc.NAMED_SPEC, rounds=1, checks=2, grace_checks=2,
                                         proc_factory=lambda: _FakeProc(poll_value=None),
                                         alive=lambda sp, pid: False)   # «мёртв», но грейс защищает
        self.assertEqual(len(spawns), 1)
        self.assertEqual(len(kills), 0)

    def test_liveness_killswitch_off_no_zombie_kill(self):
        # RC_LIVENESS=0: зомби-киллер выключен, откат к «рестарт лишь по выходу процесса»
        saved = rc.LIVENESS_ENABLED
        rc.LIVENESS_ENABLED = False
        try:
            spawns, kills, slept = self._run(rc.SERVER_SPEC, rounds=1, checks=3, grace_checks=0,
                                             proc_factory=lambda: _FakeProc(poll_value=None),
                                             alive=lambda sp, pid: False)
            self.assertEqual(len(spawns), 1)             # процесс жив по poll → не трогаем
            self.assertEqual(len(kills), 0)
        finally:
            rc.LIVENESS_ENABLED = saved

    def test_both_branches_resolve_to_shim(self):
        # обе ветки через ОДИН резолвер → ШИМ; спавнер видит именно его
        for spec in rc.BRANCHES:
            spawns, kills, _ = self._run(spec, rounds=1, checks=1, grace_checks=1,
                                         proc_factory=lambda: _FakeProc(poll_value=None))
            self.assertEqual(spawns[0][0], self.SHIM, spec["label"])

    def test_missing_claude_waits_and_retries(self):
        runs, slept = [], []
        rc.supervise_branch(rc.SERVER_SPEC, resolver=lambda: None,
                            spawner=lambda c, s: runs.append(1),
                            gate=lambda c: (True, "ok"), sleeper=slept.append,
                            killer=lambda p: None, rounds=2)
        self.assertEqual(runs, [])                       # без бинаря ничего не спавним
        self.assertEqual(slept, [rc.RESTART_DELAY] * 2)

    def test_not_ready_gate_waits_without_spawn(self):
        runs, slept = [], []
        rc.supervise_branch(rc.NAMED_SPEC, resolver=lambda: self.SHIM,
                            spawner=lambda c, s: runs.append(1),
                            gate=lambda c: (False, "нет входа"), sleeper=slept.append,
                            killer=lambda p: None, rounds=2)
        self.assertEqual(runs, [])                       # непригодный вход → НИ одного зомби-процесса
        self.assertEqual(slept, [rc.NOT_READY_DELAY] * 2)  # пауза ДЛИННАЯ, а не 15 с
        self.assertGreater(rc.NOT_READY_DELAY, rc.RESTART_DELAY)


class TestDefaultSpawnNoWindow(unittest.TestCase):
    """default_spawn: интерактивной сессии нужен ЖИВОЙ TTY скрытой консоли — НЕ перенаправлять stdio
    и НЕ гасить консоль (CREATE_NO_WINDOW/pythonw). Лог живости усекается (свежий mtime)."""

    SHIM = r"C:\Users\u\.local\bin\claude.exe"

    def setUp(self):
        self.log = os.path.join(tempfile.gettempdir(), "rc_test_spawn_liveness.log")
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("stale content that must be truncated")
        self.spec = {"label": "t", "mode": ("rc",), "log": self.log}

    def tearDown(self):
        try:
            os.remove(self.log)
        except OSError:
            pass

    def test_no_creationflags_no_stdio_redirect(self):
        seen = {}

        def fake_popen(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            return _FakeProc()

        rc.default_spawn(self.SHIM, self.spec, verbose=False, popen=fake_popen)
        self.assertNotIn("creationflags", seen["kw"])          # консоль не гасим
        for k in ("stdout", "stderr", "stdin"):
            self.assertNotIn(k, seen["kw"])                    # stdio не перенаправляем
        self.assertEqual(seen["kw"].get("cwd"), rc.REPO)
        self.assertEqual(seen["cmd"][0], self.SHIM)            # запускаем ШИМ
        self.assertIn("--debug-file", seen["cmd"])

    def test_spawn_truncates_liveness_log(self):
        rc.default_spawn(self.SHIM, self.spec, verbose=False, popen=lambda cmd, **kw: _FakeProc())
        self.assertEqual(os.path.getsize(self.log), 0)         # старый хвост усечён, mtime=now

    def test_spawn_returns_none_on_popen_error(self):
        def boom(cmd, **kw):
            raise OSError("не поднялся")
        self.assertIsNone(rc.default_spawn(self.SHIM, self.spec, verbose=False, popen=boom))


class TestPreflightGate(unittest.TestCase):
    """ГОЛДЕН живого прокола 22.07: задача Running, процесс claude жив — а канал МЁРТВ.
    `claude auth status` при этом отвечал loggedIn=true/max; правду сказал только `claude doctor`:
    вход выдан БЕЗ скоупа user:profile. Пре-флайт смотрит doctor и НЕ плодит пустых процессов;
    карточка владельцу — одна на ОБЕ ветки (дедуп между потоками)."""

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

    def test_gate_ok_passes_without_card(self):
        cards = []
        ok, _ = rc.default_gate(r"C:\c.exe", ready=lambda c: (True, "ок"),
                                notifier=cards.append)
        self.assertTrue(ok)
        self.assertEqual(cards, [])               # всё хорошо — владельца не дёргаем

    def test_gate_blocked_notifies_once_then_dedup(self):
        rc._notify_ts[0] = 0.0                    # сброс дедуп-метки
        cards = []
        bad = lambda c: (False, "Sign-in is missing the user:profile scope")
        # первый провал — карточка уходит
        rc.default_gate(r"C:\c.exe", ready=bad, notifier=cards.append, now=1000.0)
        # второй провал СРАЗУ (другая ветка) — дубль подавлен
        rc.default_gate(r"C:\c.exe", ready=bad, notifier=cards.append, now=1000.0 + 5)
        self.assertEqual(len(cards), 1)
        self.assertIn("auth login", cards[0])
        # спустя NOT_READY_DELAY — можно снова
        rc.default_gate(r"C:\c.exe", ready=bad, notifier=cards.append,
                        now=1000.0 + rc.NOT_READY_DELAY + 1)
        self.assertEqual(len(cards), 2)


class TestMain(unittest.TestCase):
    """Один синглтон-супервизор поднимает ОБЕ ветки в потоках; вторая копия молча выходит."""

    def test_second_instance_exits_without_launching(self):
        launched = []
        code = rc.main(singleton=lambda: (False, None), branch_runner=lambda spec: launched.append(spec))
        self.assertEqual(code, 0)
        self.assertEqual(launched, [])            # вторая копия НЕ поднимает веток

    def test_launches_both_branches(self):
        launched = []
        rc.main(singleton=lambda: (True, None), branch_runner=lambda spec: launched.append(spec["label"]))
        self.assertEqual(sorted(launched), ["named-channel", "rc-server"])   # обе ветки подняты


class TestConfigPaths(unittest.TestCase):
    """Пути и задержки: логи веток и флаг — в репо (под *.log / .gitignore); пауза «не готов» —
    ДЛИННАЯ, чтобы лог за ночь не распух."""

    def test_flag_and_branch_logs_are_repo_local(self):
        self.assertTrue(rc.DEBUG_FLAG.startswith(rc.REPO))
        self.assertTrue(rc.SERVER_LOG.startswith(rc.REPO))
        self.assertTrue(rc.NAMED_LOG.startswith(rc.REPO))
        self.assertTrue(rc.SERVER_LOG.endswith(".log"))
        self.assertTrue(rc.NAMED_LOG.endswith(".log"))

    def test_not_ready_delay_is_longer(self):
        self.assertGreater(rc.NOT_READY_DELAY, rc.RESTART_DELAY)

    def test_liveness_threshold_covers_idle_intervals(self):
        # порог свежести обязан перекрывать и лог-вехи (~10 мин), и таяние соединений (~15 мин)
        self.assertGreaterEqual(rc.LIVENESS_MAX_AGE, 15 * 60)


class TestLauncherFiles(unittest.TestCase):
    """Цепочка запуска обязана остаться скрытой И консольной: wscript → VBS → venv-python."""

    HERE = os.path.dirname(os.path.abspath(__file__))

    def _read(self, name):
        with open(os.path.join(self.HERE, name), encoding="utf-8") as f:
            return f.read()

    def test_vbs_runs_hidden_via_console_python(self):
        vbs = self._read("rc_remote_control.vbs")
        self.assertIn("sh.Run cmd, 0, True", vbs)            # 0 = окно скрыто
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
