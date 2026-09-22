# -*- coding: utf-8 -*-
"""
test_rc_supervisor.py — ДВУХРЕЖИМНЫЙ супервизор канала Claude Code Remote Control
(задача pc_remote_control). Реального claude НЕ запускаем: резолвер, спавн, проба живости и цикл
принимают инъекции. Запуск:
    D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_rc_supervisor -v
"""

import os
import time
import logging
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


class _Auth:
    """Двойник детектора протухшей авторизации (rc_auth_detect). По умолчанию ИНЕРТЕН — иначе
    прежние тесты ветки rc-server полезли бы читать БОЕВОЙ rc_server_debug.log и их вердикт
    зависел бы от содержимого живого лога (класс «тест обязан быть детерминирован»).
    fire_at=N → на N-й проверке объявляет «пора рестартовать»."""

    ENABLED = True

    def __init__(self, fire_at=None):
        self.fire_at = fire_at
        self.scans = 0

    def new_state(self):
        return {"failures": 0}

    def scan(self, path, state):
        self.scans += 1
        return state

    def should_restart(self, state):
        return self.fire_at is not None and self.scans >= self.fire_at

    def describe(self, state):
        return "протухшая авторизация: 1 новых сессий подряд убито"


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
            auth=kw.pop("auth", _Auth()),
            notifier=kw.pop("notifier", lambda t: True),
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


class TestChildEnvProfileChoice(unittest.TestCase):
    """Рычаг 70w доехал до RC (22.09). Постоянная CLAUDE_CONFIG_DIR=D:\\claude_profile_2 увела обе
    ветки в профиль второго аккаунта: 91 старт rc-server, 88 гашений ЗОМБИ, 0 регистраций, и телефон
    на другом аккаунте. Теперь окружение детей и doctor решает claude_profile_choice.txt, а третий
    исход файла оставляет прежнее наследование байт-в-байт (env в Popen не передаётся вовсе)."""

    KEY = "CLAUDE_CONFIG_DIR"
    SHIM = r"C:\Users\u\.local\bin\claude.exe"
    BASE = {"CLAUDE_CONFIG_DIR": r"D:\claude_profile_2", "PATH": r"C:\x", "USERPROFILE": r"C:\u"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.log = os.path.join(self.repo, "rc_test_env_liveness.log")
        self.spec = {"label": "t", "mode": ("rc",), "log": self.log}

    def tearDown(self):
        self.tmp.cleanup()

    def _choice(self, text):
        with open(os.path.join(self.repo, "claude_profile_choice.txt"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_main_word_drops_key_keeps_rest(self):
        self._choice("# строка отката\nОСНОВНОЙ\n")
        env, why = rc.child_env(base=dict(self.BASE), repo=self.repo)
        self.assertIsNotNone(env)
        self.assertNotIn(self.KEY, env)                       # ключа НЕТ, а не пустой
        self.assertEqual(env["PATH"], r"C:\x")               # прочее окружение цело
        self.assertIn("основной профиль", why)

    def test_existing_path_sets_key(self):
        self._choice(self.repo + "\n")
        env, _ = rc.child_env(base=dict(self.BASE), repo=self.repo)
        self.assertEqual(env[self.KEY], self.repo)

    def test_third_outcome_keeps_inheritance(self):
        # файла нет / две значимые строки / несуществующий путь — env не передаём вовсе
        cases = (None, "ОСНОВНОЙ\nD:\\x\n", r"D:\нет_такого_каталога_rc_test")
        for text in cases:
            if text is not None:
                self._choice(text)
            env, why = rc.child_env(base=dict(self.BASE), repo=self.repo)
            self.assertIsNone(env, text)
            self.assertIn("НЕ ПРИМЕНЁН", why)

    def test_never_empty_value(self):
        for text in ("ОСНОВНОЙ", "", "\"\"", "  \n# c\n", self.repo):
            self._choice(text)
            env, _ = rc.child_env(base=dict(self.BASE), repo=self.repo)
            self.assertNotEqual((env or {}).get(self.KEY, "absent"), "")

    def test_base_env_not_mutated(self):
        self._choice("ОСНОВНОЙ")
        base = dict(self.BASE)
        rc.child_env(base=base, repo=self.repo)
        self.assertEqual(base, self.BASE)                     # правим копию, не окружение сторожа

    def test_chooser_crash_falls_back_to_inheritance(self):
        class _Boom:
            ACT_KEEP = "keep"

            @staticmethod
            def apply_to(env, repo=None):
                raise ValueError("сломан")
        env, why = rc.child_env(chooser=_Boom, base=dict(self.BASE), repo=self.repo)
        self.assertIsNone(env)
        self.assertIn("сорвался", why)

    def test_module_missing_falls_back_to_inheritance(self):
        saved = rc.profile_choice
        rc.profile_choice = None
        try:
            env, why = rc.child_env(base=dict(self.BASE), repo=self.repo)
        finally:
            rc.profile_choice = saved
        self.assertIsNone(env)
        self.assertIn("не импортирован", why)

    def test_spawn_passes_env_and_keeps_tty(self):
        seen = {}

        def fake_popen(cmd, **kw):
            seen["kw"] = kw
            return _FakeProc()
        env = {"PATH": r"C:\x"}
        rc.default_spawn(self.SHIM, self.spec, verbose=False, popen=fake_popen,
                         envf=lambda: (env, "выбор учётки строителей: тест"))
        self.assertIs(seen["kw"].get("env"), env)
        self.assertNotIn("creationflags", seen["kw"])         # консоль не гасим
        for k in ("stdout", "stderr", "stdin"):
            self.assertNotIn(k, seen["kw"])                   # TTY цел
        self.assertEqual(seen["kw"].get("cwd"), rc.REPO)

    def test_spawn_keep_passes_no_env_kwarg(self):
        seen = {}

        def fake_popen(cmd, **kw):
            seen["kw"] = kw
            return _FakeProc()
        rc.default_spawn(self.SHIM, self.spec, verbose=False, popen=fake_popen,
                         envf=lambda: (None, "выбор учётки НЕ ПРИМЕНЁН: тест"))
        self.assertEqual(seen["kw"], {"cwd": rc.REPO})        # прежний вызов байт-в-байт

    def test_doctor_judges_same_profile_as_children(self):
        seen = []
        env = {"PATH": r"C:\x"}

        def runner(*a, **k):
            seen.append(k)
            return TestPreflightGate._P(TestPreflightGate.DOCTOR_OK)
        rc.rc_ready(r"C:\c.exe", runner=runner, envf=lambda: (env, "drop"))
        rc.rc_ready(r"C:\c.exe", runner=runner, envf=lambda: (None, "keep"))
        self.assertIs(seen[0].get("env"), env)
        self.assertNotIn("env", seen[1])


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

    def test_startup_line_names_thresholds_of_both_branches(self):
        """Стартовая строка НАЗЫВАЕТ порог каждой ветки — это единственное доказательство того,
        что в ПАМЯТИ процесса новое число, доступное СРАЗУ и не требующее провала. Класс 30.07:
        порог читается на импорте, супервизор 20 часов работал старым кодом при новом файле на
        диске, а «фикс в силе» проверялось ожиданием 22 минут тишины."""
        handler = _Collect()
        rc.log.addHandler(handler)
        try:
            rc.main(singleton=lambda: (True, None), branch_runner=lambda spec: None)
        finally:
            rc.log.removeHandler(handler)
        start = [ln for ln in handler.lines if "супервизор стартовал" in ln]
        self.assertEqual(len(start), 1)
        # порог назван ЧИСЛОМ и привязан к имени ветки — обе ветки, оба своих числа
        self.assertIn("named-channel:порог %sс" % rc.NAMED_LIVENESS_MAX_AGE, start[0])
        self.assertIn("rc-server:порог %sс" % rc.SERVER_LIVENESS_MAX_AGE, start[0])
        self.assertIn("pid=%s" % os.getpid(), start[0])

    def test_startup_line_survives_spec_without_max_age(self):
        """Инъекция ветки без ключа max_age (тестовые спеки такие есть) не смеет уронить старт —
        падает общий дефолт, а не супервизор."""
        handler = _Collect()
        rc.log.addHandler(handler)
        try:
            rc.main(singleton=lambda: (True, None), branch_runner=lambda spec: None,
                    branches=[{"label": "t", "mode": ("rc",), "log": "x.log"}])
        finally:
            rc.log.removeHandler(handler)
        start = [ln for ln in handler.lines if "супервизор стартовал" in ln]
        self.assertIn("t:порог %sс" % rc.LIVENESS_MAX_AGE, start[0])


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


class TestChurnFixServerLiveness(unittest.TestCase):
    """ГОЛДЕН фикса churn 24.07 (docs/artifacts/2026-07-24-rc-churn-liveness.md): ложная зомби-проба
    гасила ЖИВОЙ простаивающий сервер каждые 20–76 мин, плодя новую Environment на каждый старт.
    Фикс: (а) серверу — свой, более широкий порог свежести лога (3600с vs 1200с у именованного);
    (б) счётчик страйков — гасим только после N ПОДРЯД мёртвых проверок, а не одной (мгновенная
    просадка соединений при реконнекте больше не убивает процесс)."""

    SHIM = r"C:\Users\u\.local\bin\claude.exe"

    def _run(self, spec, alive, **kw):
        spawns, kills, slept = [], [], []

        def spawner(claude, sp):
            spawns.append((claude, sp["label"]))
            return _FakeProc(poll_value=None)                 # процесс всегда жив по poll

        rc.supervise_branch(
            spec, resolver=lambda: self.SHIM, spawner=spawner,
            alive=alive, gate=lambda c: (True, "ok"),
            sleeper=slept.append, killer=kills.append,
            rounds=kw.pop("rounds", 1), checks=kw.pop("checks", 6),
            grace_checks=kw.pop("grace_checks", 0),
            strikes_needed=kw.pop("strikes_needed", 3),
            auth=kw.pop("auth", _Auth()), notifier=lambda t: True,
        )
        return spawns, kills, slept

    # --- (а) пер-branch порог свежести: у КАЖДОЙ ветки он реально СВОЙ ---
    def test_thresholds_are_per_branch_not_shared(self):
        # ГОЛДЕН пер-branch: ОДИН и тот же возраст лога 5000с (>3600, <43200), 0 соединений →
        # СЕРВЕР (порог 3600) уже МЁРТВ, КАНАЛ (порог 43200) ещё ЖИВ. Пороги не общие.
        server_dead = rc.probe_alive(rc.SERVER_SPEC, pid=1,
                                     now=50000.0, getmtime=lambda p: 50000.0 - 5000,
                                     conns=lambda pid: 0)
        named_alive = rc.probe_alive(rc.NAMED_SPEC, pid=1,
                                     now=50000.0, getmtime=lambda p: 50000.0 - 5000,
                                     conns=lambda pid: 0)
        self.assertFalse(server_dead)                        # сервер на 3600 — 5000с протух
        self.assertTrue(named_alive)                         # канал на 43200 — 5000с ещё свеж

    def test_config_server_wide_named_default_strikes_three(self):
        self.assertEqual(rc.SERVER_SPEC.get("max_age"), rc.SERVER_LIVENESS_MAX_AGE)
        self.assertGreaterEqual(rc.SERVER_LIVENESS_MAX_AGE, 3600)     # серверу — не меньше часа
        self.assertEqual(rc.NAMED_SPEC.get("max_age"), rc.NAMED_LIVENESS_MAX_AGE)
        self.assertEqual(rc.LIVENESS_STRIKES, 3)

    # --- (б) страйки: живой сервер в простое НЕ рестартуется ---
    def test_idle_server_survives_connection_blip(self):
        # ЖИВОЙ сервер: соединение просело на 2 тика (реконнект), потом живо → страйки сброшены,
        # процесс НЕ гасится (ни одного kill), спавн ровно один.
        seq = iter([False, False, True, True, True, True])
        spawns, kills, _ = self._run(rc.SERVER_SPEC, alive=lambda sp, pid: next(seq),
                                     rounds=1, checks=6, strikes_needed=3)
        self.assertEqual(len(kills), 0)                      # 2 мёртвых тика подряд < 3 → жив
        self.assertEqual(len(spawns), 1)

    # --- (б) мёртвый сервер: гасим РОВНО после 3-го страйка, не раньше, и рестартуем ---
    def test_dead_server_killed_after_three_strikes(self):
        spawns, kills, slept = self._run(rc.SERVER_SPEC, alive=lambda sp, pid: False,
                                         rounds=1, checks=6, strikes_needed=3)
        self.assertEqual(len(kills), 1)                      # погашен один раз
        self.assertEqual(slept.count(rc.CHECK_INTERVAL), 3)  # ровно на 3-й проверке (не 1-й, не 2-й)

    def test_two_consecutive_dead_checks_not_enough(self):
        # ровно 2 мёртвых тика — НЕ гасим (нужно 3 ПОДРЯД)
        spawns, kills, _ = self._run(rc.SERVER_SPEC, alive=lambda sp, pid: False,
                                     rounds=1, checks=2, strikes_needed=3)
        self.assertEqual(len(kills), 0)

    def test_dead_server_restarted_next_round(self):
        # мёртвый сервер погашен и ПОДНЯТ заново (рестарт): за 2 раунда — 2 спавна и 2 гашения
        spawns, kills, _ = self._run(rc.SERVER_SPEC, alive=lambda sp, pid: False,
                                     rounds=2, checks=6, strikes_needed=3)
        self.assertEqual(len(spawns), 2)
        self.assertEqual(len(kills), 2)

    # --- именованный канал ведёт себя как раньше: тоже гасится при стабильно мёртвом канале ---
    def test_named_channel_zombie_still_killed(self):
        spawns, kills, _ = self._run(rc.NAMED_SPEC, alive=lambda sp, pid: False,
                                     rounds=1, checks=6, strikes_needed=3)
        self.assertEqual(len(kills), 1)                      # реальный зомби именованного — погашен


class _Collect(logging.Handler):
    """Собирает записи rc_supervisor.log — чтобы проверить, что строка сноса НАЗЫВАЕТ ЧИСЛА."""

    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class TestNamedChannelLivenessThreshold(unittest.TestCase):
    """ГОЛДЕН зеркального фикса 30.07 (docs/artifacts/2026-07-30-rc-named-liveness.md): класс-фикс
    24.07 применили ТОЛЬКО серверной ветке, а ветку КАНАЛА оставили на общем 1200с — за сутки она
    погасила канал 29 раз против 1 у сервера, каждый снос рвал живые сессии владельца.

    Числа ветки КАНАЛА (замер, а не копия серверных): свой --debug-file она пишет РОВНО ОДИН раз
    за жизнь — всплеск 70 строк за 904 мс на старте, дальше НИ БАЙТА (pid 13464: старт
    20:41:34.623, последняя запись 20:41:35.814; через 4 мин возраст лога 252.1с при возрасте
    процесса 253.3с). Второе плечо (established>0) в простое тоже гаснет — это доказано самими
    гашениями: каждое требует 0 соединений на 3 проверках подряд. Значит при пороге 1200с смерть
    наступала ДЕТЕРМИНИРОВАННО на 1290-й секунде (1200 + 3×30): реплей боевого журнала
    (diag_rc_liveness_replay.py) — 333 жизни из 359 длились ровно 1290с. Самый долгий молчок
    ЖИВОГО канала — 9,23ч (33 245с). Порог 43200с (12ч) кладём над ним с запасом ~30%."""

    SHIM = r"C:\Users\u\.local\bin\claude.exe"
    IDLE_21_MIN = 1290.0        # ровно тот возраст лога, на котором канал гасили 29 раз за сутки
    OBSERVED_SILENCE = 33245.0  # 9,23ч — самый долгий молчок ЖИВОГО канала (30.07 03:28:27→12:42:32)

    def _run(self, spec, probe, **kw):
        spawns, kills = [], []

        def spawner(claude, sp):
            spawns.append(sp["label"])
            return _FakeProc(poll_value=None)                # процесс всегда жив по poll

        handler = _Collect()
        rc.log.addHandler(handler)
        try:
            rc.supervise_branch(
                spec, resolver=lambda: self.SHIM, spawner=spawner,
                probe=probe, gate=lambda c: (True, "ok"),
                sleeper=lambda s: None, killer=kills.append,
                rounds=kw.pop("rounds", 1), checks=kw.pop("checks", 6),
                grace_checks=kw.pop("grace_checks", 0),
                strikes_needed=kw.pop("strikes_needed", 3),
                auth=kw.pop("auth", _Auth()), notifier=lambda t: True,
            )
        finally:
            rc.log.removeHandler(handler)
        return spawns, kills, handler.lines

    # --- порог обоснован СВОИМИ числами, а не скопирован с серверного ---
    def test_named_threshold_covers_observed_healthy_silence(self):
        self.assertGreater(rc.NAMED_LIVENESS_MAX_AGE, self.OBSERVED_SILENCE)
        self.assertEqual(rc.NAMED_SPEC.get("max_age"), rc.NAMED_LIVENESS_MAX_AGE)

    def test_named_threshold_is_not_a_copy_of_server(self):
        # у веток РАЗНЫЕ интервалы записи лога → пороги обязаны отличаться, а не быть скопированы
        self.assertNotEqual(rc.NAMED_LIVENESS_MAX_AGE, rc.SERVER_LIVENESS_MAX_AGE)
        self.assertGreater(rc.NAMED_LIVENESS_MAX_AGE, rc.SERVER_LIVENESS_MAX_AGE)

    # --- ГОЛДЕН 1: 0-TCP + лог свежее НОВОГО порога → НЕ гасится ---
    def test_idle_channel_zero_tcp_fresh_log_not_killed(self):
        # ДОСЛОВНО живой случай: 0 соединений, лог молчит 1290с. Старый порог гасил, новый — нет.
        self.assertFalse(rc.channel_alive(self.IDLE_21_MIN, 0, max_age=1200))          # было: труп
        self.assertTrue(rc.channel_alive(self.IDLE_21_MIN, 0,
                                         max_age=rc.NAMED_LIVENESS_MAX_AGE))           # стало: жив
        ok = rc.probe_alive(rc.NAMED_SPEC, pid=1, now=100000.0,
                            getmtime=lambda p: 100000.0 - self.IDLE_21_MIN,
                            conns=lambda pid: 0)
        self.assertTrue(ok)

    def test_idle_channel_survives_full_monitor_loop(self):
        # тот же случай через ВЕСЬ цикл монитора: ни одного гашения за 6 проверок подряд
        probe = lambda sp, pid: rc.probe_detail(
            sp, pid, now=100000.0, getmtime=lambda p: 100000.0 - self.IDLE_21_MIN,
            conns=lambda _p: 0)
        spawns, kills, lines = self._run(rc.NAMED_SPEC, probe, rounds=1, checks=6)
        self.assertEqual(len(kills), 0)
        self.assertEqual(len(spawns), 1)
        self.assertEqual([l for l in lines if "ЗОМБИ" in l], [])

    # --- ГОЛДЕН 2: 0-TCP + молчок ДОЛЬШЕ нового порога → гасится, и строка называет ЧИСЛА ---
    def test_really_dead_channel_still_killed_with_numbers(self):
        dead_age = rc.NAMED_LIVENESS_MAX_AGE + 90.0          # порог + три страйка по 30с
        probe = lambda sp, pid: rc.probe_detail(
            sp, pid, now=100000.0, getmtime=lambda p: 100000.0 - dead_age,
            conns=lambda _p: 0)
        spawns, kills, lines = self._run(rc.NAMED_SPEC, probe, rounds=1, checks=6)
        self.assertEqual(len(kills), 1)                      # настоящий труп ПО-ПРЕЖНЕМУ гасится
        zombie = [l for l in lines if "ЗОМБИ" in l]
        self.assertEqual(len(zombie), 1)
        line = zombie[0]
        self.assertIn("%.0fс" % dead_age, line)              # возраст лога — числом
        self.assertIn(str(rc.NAMED_LIVENESS_MAX_AGE), line)  # порог ветки — числом
        self.assertIn("соединений 0", line)                  # соединения — числом
        self.assertIn("named-channel", line)

    def test_kill_line_names_age_threshold_and_conns(self):
        # строка сноса обязана содержать ВСЕ три числа + pid: без них ложное гашение неотличимо
        # от настоящего (ровно так класс 24.07 прожил на второй ветке ещё шесть суток)
        probe = lambda sp, pid: (False, 43290.0, 0, 43200)
        _spawns, kills, lines = self._run(rc.NAMED_SPEC, probe, rounds=1, checks=6)
        self.assertEqual(len(kills), 1)
        line = [l for l in lines if "ЗОМБИ" in l][0]
        for token in ("43290с", "43200", "соединений 0", "pid=", "3 проверок подряд"):
            self.assertIn(token, line)

    def test_missing_log_file_reported_as_such_not_zero(self):
        # лога нет вовсе → в строке честное «нет файла», а не «0с» (иначе читается как «свежий»)
        self.assertEqual(rc.fmt_age(None), "нет файла")
        self.assertEqual(rc.fmt_age(1290.0), "1290с")

    # --- РЕГРЕСС: серверная ветка правкой не задета ---
    def test_server_branch_untouched(self):
        self.assertEqual(rc.SERVER_LIVENESS_MAX_AGE, 3600)
        self.assertEqual(rc.SERVER_SPEC.get("max_age"), 3600)
        # сервер судится ровно как 24.07: 1500с свеж, 3700с протух
        self.assertTrue(rc.probe_alive(rc.SERVER_SPEC, pid=1, now=50000.0,
                                       getmtime=lambda p: 50000.0 - 1500, conns=lambda pid: 0))
        self.assertFalse(rc.probe_alive(rc.SERVER_SPEC, pid=1, now=50000.0,
                                        getmtime=lambda p: 50000.0 - 3700, conns=lambda pid: 0))

    def test_server_kill_still_fires_after_three_strikes(self):
        probe = lambda sp, pid: rc.probe_detail(
            sp, pid, now=100000.0, getmtime=lambda p: 100000.0 - 4000.0, conns=lambda _p: 0)
        _spawns, kills, lines = self._run(rc.SERVER_SPEC, probe, rounds=1, checks=6)
        self.assertEqual(len(kills), 1)
        self.assertIn("порога 3600с", [l for l in lines if "ЗОМБИ" in l][0])

    # --- ТРЕТЬЕЙ ветки с этим предикатом нет: замок на будущее ---
    def test_no_branch_rides_the_shared_default(self):
        # веток ровно две, и у КАЖДОЙ порог задан явно — новая ветка без своего max_age
        # молча унаследовала бы общий 1200с и повторила бы этот же класс третий раз
        self.assertEqual(len(rc.BRANCHES), 2)
        for spec in rc.BRANCHES:
            self.assertIsNotNone(spec.get("max_age"), spec["label"])

    def test_predicate_itself_unchanged(self):
        # чинили ТОЛЬКО порог: сам предикат (лог свеж ИЛИ есть соединения) не тронут —
        # соединение по-прежнему спасает канал при сколь угодно старом логе
        self.assertTrue(rc.channel_alive(999999, 1, max_age=rc.NAMED_LIVENESS_MAX_AGE))
        self.assertFalse(rc.channel_alive(999999, 0, max_age=rc.NAMED_LIVENESS_MAX_AGE))
        self.assertFalse(rc.channel_alive(None, 0, max_age=rc.NAMED_LIVENESS_MAX_AGE))


class TestAuthStalenessWiring(unittest.TestCase):
    """ВАРИАНТ «а» артефакта 2026-07-29-rc-token-staleness-prevention: сторож рестартует ветку
    rc-server, когда авторизация протухла (новые сессии убиты). Здесь — ВШИВКА в supervise_branch;
    сам разбор лога проверяет test_rc_auth_detect."""

    SHIM = r"C:\Users\u\.local\bin\claude.exe"

    def _run(self, spec, auth, **kw):
        spawns, kills, cards = [], [], []

        def spawner(claude, sp):
            spawns.append(sp["label"])
            return _FakeProc(poll_value=None)          # процесс ЖИВ по poll и по зомби-пробе

        rc.supervise_branch(
            spec, resolver=lambda: self.SHIM, spawner=spawner,
            alive=lambda sp, pid: True, gate=lambda c: (True, "ok"),
            sleeper=lambda s: None, killer=kills.append,
            rounds=kw.pop("rounds", 1), checks=kw.pop("checks", 4),
            grace_checks=kw.pop("grace_checks", 0),
            auth=auth, notifier=cards.append,
        )
        return spawns, kills, cards

    def test_server_restarted_when_auth_stale(self):
        """Все пробы живости ЗЕЛЕНЫ (процесс жив, канал жив) — и всё равно рестарт: именно это
        отличает отказ авторизации от всего, что сторож умел ловить раньше."""
        spawns, kills, cards = self._run(rc.SERVER_SPEC, _Auth(fire_at=2), rounds=1, checks=4)
        self.assertEqual(len(kills), 1)                # погашен, несмотря на живой канал
        self.assertEqual(len(spawns), 1)
        self.assertEqual(len(cards), 1)                # владелец предупреждён о смене Environment
        self.assertIn("Remote Control", cards[0])

    def test_healthy_auth_never_restarts(self):
        """Детектор молчит → поведение сторожа байт-в-байт прежнее: ни гашения, ни карточки."""
        spawns, kills, cards = self._run(rc.SERVER_SPEC, _Auth(fire_at=None), rounds=1, checks=4)
        self.assertEqual((kills, cards), ([], []))
        self.assertEqual(len(spawns), 1)

    def test_named_channel_has_no_auth_watch(self):
        """Детектор — только у серверной ветки: именованный канал новые сессии не порождает,
        и трогать его по чужой улике нельзя."""
        auth = _Auth(fire_at=1)
        spawns, kills, cards = self._run(rc.NAMED_SPEC, auth, rounds=1, checks=4)
        self.assertEqual(auth.scans, 0, "у named-channel детектор не должен даже опрашиваться")
        self.assertEqual((kills, cards), ([], []))

    def test_spec_flags(self):
        self.assertTrue(rc.SERVER_SPEC.get("auth_watch"))
        self.assertFalse(rc.NAMED_SPEC.get("auth_watch"))

    def test_detector_crash_does_not_kill_supervisor(self):
        """НЕ НАВРЕДИ: сорвавшийся детектор гасит СЕБЯ, а не канал владельца."""
        class Boom(_Auth):
            def scan(self, path, state):
                raise RuntimeError("сорвался")
        spawns, kills, cards = self._run(rc.SERVER_SPEC, Boom(), rounds=1, checks=3)
        self.assertEqual((kills, cards), ([], []))
        self.assertEqual(len(spawns), 1)


_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture_body(name):
    """Тело живого лога из фикстуры: без строк-комментариев, концы LF — как пишет CLI."""
    with open(os.path.join(_FIX, name), encoding="utf-8") as f:
        return "\n".join(l for l in f.read().splitlines() if not l.startswith("#")) + "\n"


STUCK = _fixture_body("rc_server_stuck_prompt.live.log")        # 11 строк, моста нет (22.09)
HEALTHY = _fixture_body("rc_server_registered_start.live.log")  # старт 09.09 до poll loop
EMPTY = ""                                                      # лог жизни пуст → «не знаю»
ALIEN = "[22/09/26 16:35] settings ok\n" * 11                   # формат уехал → «не знаю»


class TestUnregisteredLife(unittest.TestCase):
    """ЖИЗНЬ БЕЗ РЕГИСТРАЦИИ (класс 18–22.09, docs/artifacts/2026-09-22-rc-device-profile2-stall.md):
    `claude rc` висел до `[bridge:init]`, стартовый всплеск (11 строк за 0,9 с) держал лог «свежим» весь
    час порога 3600с — 91 старт, 88 гашений ЗОМБИ, 0 регистраций, 0 карточек.

    Здесь — НАСТОЯЩИЙ детектор rc_auth_detect на временном логе (боевой rc_server_debug.log не
    читается): спавнер пишет в лог то, что пишет CLI, а проба отвечает как в живом случае — лог
    «свеж» (старый предикат говорит «жив»), соединений столько, сколько задал тест."""

    SAVED = ("UNREG_ENABLED", "UNREG_LIVES", "UNREG_BACKOFF", "UNREG_BACKOFF_MAX", "LIVENESS_ENABLED")

    def setUp(self):
        self._saved = {k: getattr(rc, k) for k in self.SAVED}
        rc.UNREG_ENABLED, rc.UNREG_LIVES = True, 3
        rc.UNREG_BACKOFF, rc.UNREG_BACKOFF_MAX = 3600, 21600
        rc.LIVENESS_ENABLED = True
        self._auth_enabled = rc.rc_auth_detect.ENABLED
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "rc_server_debug.log")
        self.spec = dict(rc.SERVER_SPEC, log=self.log)
        self.binary = os.path.join(self.tmp.name, "claude.exe")
        with open(self.binary, "w", encoding="utf-8") as f:
            f.write("шим")
        self.handler = _Collect()
        rc.log.addHandler(self.handler)

    def tearDown(self):
        rc.log.removeHandler(self.handler)
        for k, v in self._saved.items():
            setattr(rc, k, v)
        rc.rc_auth_detect.ENABLED = self._auth_enabled
        self.tmp.cleanup()

    def _run(self, lives, conns=None, checks=12, spec=None, auth=None, notifier=None):
        """lives — список (тело лога, код выхода | None) на каждую жизнь. → (kills, cards, pauses,
        checks_slept). pauses — паузы МЕЖДУ жизнями (всё, что спало не интервалом проверки)."""
        kills, cards, slept = [], [], []
        queue = list(lives)
        _conns = conns or (lambda: 0)

        def spawner(claude, sp):
            body, code = queue.pop(0)
            with open(sp["log"], "w", encoding="utf-8", newline="\n") as f:
                f.write(body)            # то, что CLI пишет в лог, который default_spawn усёк
            return _FakeProc(poll_value=code, pid=4000 + len(queue))

        rc.supervise_branch(
            spec or self.spec, resolver=lambda: self.binary, spawner=spawner,
            probe=lambda sp, pid: (True, 20.0, _conns(), sp.get("max_age")),
            gate=lambda c: (True, "ok"), sleeper=slept.append, killer=kills.append,
            rounds=len(lives), checks=checks, grace_checks=6, strikes_needed=3,
            auth=auth if auth is not None else rc.rc_auth_detect,
            notifier=notifier if notifier is not None else cards.append,
            envf=lambda: (None, "выбор учётки строителей: слово ОСНОВНОЙ — тест"))
        pauses = [s for s in slept if s != rc.CHECK_INTERVAL]
        return kills, cards, pauses, slept.count(rc.CHECK_INTERVAL)

    def _lines(self, word):
        return [l for l in self.handler.lines if word in l]

    # --- раннее гашение ---
    def test_stuck_life_killed_after_grace_and_three_strikes(self):
        """ДОСЛОВНО живой случай: 11 строк, моста нет, 0 соединений → гашение на 9-й проверке
        (грейс 6 + 3 страйка) = 270 с, а не через 3661 с, как 88 раз с 18.09."""
        kills, cards, pauses, checks = self._run([(STUCK, None)])
        self.assertEqual((len(kills), checks), (1, 9))
        self.assertEqual(9 * rc.CHECK_INTERVAL, 270)
        line = self._lines("БЕЗ РЕГИСТРАЦИИ")[0]
        for token in ("≈270с", "грейс 180с", "до [bridge:init]", "строк лога 11", "соединений 0",
                      "3 проверок подряд", "pid="):
            self.assertIn(token, line)
        self.assertEqual(self._lines("ЗОМБИ"), [])          # это не старый путь сноса
        self.assertEqual(cards, [])                         # одна жизнь — ещё не серия

    def test_old_predicate_alone_keeps_the_zombie_alive(self):
        """Без нового вопроса (RC_UNREG=0) та же жизнь живёт: свежий лог = «жив». Так и было 88 раз."""
        rc.UNREG_ENABLED = False
        kills, cards, pauses, _ = self._run([(STUCK, None)], checks=40)
        self.assertEqual((kills, cards, pauses), ([], [], [rc.RESTART_DELAY]))
        self.assertEqual(self._lines("итог жизни"), [])      # клапан выключает суд целиком

    def test_registered_life_with_zero_conns_is_not_killed(self):
        kills, _, _, _ = self._run([(HEALTHY, None)], checks=40)
        self.assertEqual(kills, [])

    def test_unregistered_but_connected_is_not_killed(self):
        kills, _, _, _ = self._run([(STUCK, None)], conns=lambda: 1, checks=40)
        self.assertEqual(kills, [])

    def test_unknown_verdict_never_kills_early(self):
        for body in (EMPTY, ALIEN):
            kills, _, _, _ = self._run([(body, None)], checks=40)
            self.assertEqual(kills, [], repr(body[:20]))

    def test_connection_blip_resets_strikes(self):
        seq = iter([0, 0, 1, 0, 0, 0, 0, 0])               # с 7-й проверки (после грейса)
        kills, _, _, checks = self._run([(STUCK, None)], conns=lambda: next(seq))
        self.assertEqual((len(kills), checks), (1, 12))     # 7,8 → сброс на 9 → 10,11,12

    def test_grace_protects_a_fresh_life(self):
        kills, _, _, _ = self._run([(STUCK, None)], checks=6)
        self.assertEqual(kills, [])

    def test_liveness_killswitch_also_stops_early_kill(self):
        rc.LIVENESS_ENABLED = False                         # RC_LIVENESS=0: «рестарт лишь по выходу»
        kills, _, _, _ = self._run([(STUCK, None)], checks=40)
        self.assertEqual(kills, [])

    def test_auth_watch_off_does_not_blind_the_registration_question(self):
        """RC_AUTH_WATCH=0 выключает рестарт по кредам, а не вопрос о регистрации."""
        rc.rc_auth_detect.ENABLED = False
        kills, _, _, _ = self._run([(STUCK, None)])
        self.assertEqual(len(kills), 1)

    # --- серия, карточка, backoff ---
    def test_three_lives_one_card_then_backoff_capped_at_6h(self):
        kills, cards, pauses, _ = self._run([(STUCK, None)] * 10)
        self.assertEqual(len(kills), 10)
        self.assertEqual(len(cards), 1, "карточка одна на серию, а не на жизнь")
        self.assertEqual(pauses, [rc.RESTART_DELAY, rc.RESTART_DELAY,
                                  3600, 7200, 14400, 21600, 21600, 21600, 21600, 21600])
        self.assertEqual(len(self._lines("итог жизни")), 10)

    def test_card_names_stage_lines_binary_mtime_and_the_fix(self):
        _, cards, _, _ = self._run([(STUCK, None)] * 3)
        card = cards[0]
        mtime = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(os.path.getmtime(self.binary)))
        for token in ("3 жизней подряд НЕ зарегистрировался", "до [bridge:init]", "строк лога 11",
                      self.binary, "mtime " + mtime, "слово ОСНОВНОЙ", "следующий через 1 ч",
                      "не чаще раза в 6 ч", "перезапусти TurboBabyRC",
                      "2026-09-22-rc-device-profile2-stall.md"):
            self.assertIn(token, card)

    def test_card_goes_the_critical_path(self):
        """notify_owner (карточка по умолчанию) зовёт dispatch_notify с --critical: deliver с
        declared=True → send_critical → инбокс 1160. Проверяем argv, в сеть не ходим."""
        calls = []
        rc.notify_owner("🛑 RC: тест", runner=lambda argv, **kw: calls.append(argv))
        argv = calls[0]
        self.assertTrue(argv[1].endswith("dispatch_notify.py"))
        self.assertEqual(argv[2:], ["--critical", "🛑 RC: тест"])

    def test_registered_life_resets_streak_and_rearms_card(self):
        s, r = (STUCK, None), (HEALTHY, None)
        _, cards, pauses, _ = self._run([s, s, s, r, s, s, s])
        self.assertEqual(len(cards), 2)
        d = rc.RESTART_DELAY
        self.assertEqual(pauses, [d, d, 3600, d, d, d, 3600])   # после регистрации — снова с нуля
        self.assertEqual(len(self._lines("прервана жизнью с регистрацией")), 1)

    def test_unknown_life_neither_counts_nor_resets(self):
        s = (STUCK, None)
        _, cards, pauses, _ = self._run([s, s, (EMPTY, None), (ALIEN, None), s])
        self.assertEqual(len(cards), 1)                     # серия 3 — на пятой жизни, не на третьей
        d = rc.RESTART_DELAY
        self.assertEqual(pauses, [d, d, d, d, 3600])
        self.assertEqual(len(self._lines("про регистрацию не знаю")), 2)

    def test_self_exited_unregistered_life_is_not_a_hang(self):
        """Выход — собственный вердикт процесса (05.08: 79 + 6 выходов exit=1 за сетевой отказ,
        прошедший сам). В серию зависаний он не идёт и её не рвёт; вердикт в журнале — есть."""
        s = (STUCK, None)
        kills, cards, pauses, _ = self._run([s, (STUCK, 1), s, s])
        self.assertEqual(len(kills), 3)
        self.assertEqual(len(cards), 1)
        d = rc.RESTART_DELAY
        self.assertEqual(pauses, [d, d, d, 3600])
        self.assertEqual(len(self._lines("вышел сам — в серию не идёт")), 1)

    def test_exit_loop_alone_never_backs_off(self):
        """Живой 05.08 01:17–02:18 в миниатюре: выход exit=1 на первой проверке раз за разом —
        ни карточки, ни паузы длиннее обычной: сеть вернётся, и старт через 15 с её поймает."""
        _, cards, pauses, _ = self._run([(STUCK, 1)] * 8)
        self.assertEqual(cards, [])
        self.assertEqual(pauses, [rc.RESTART_DELAY] * 8)

    def test_failed_card_is_retried_on_the_next_life(self):
        attempts = []

        def flaky(text):
            attempts.append(text)
            if len(attempts) == 1:
                raise OSError("dispatch_notify не поднялся")
            return True
        _, _, pauses, _ = self._run([(STUCK, None)] * 5, notifier=flaky)
        self.assertEqual(len(attempts), 2)                  # сорвалась → повтор → ушла → тишина
        self.assertEqual(pauses[2:], [3600, 7200, 14400])   # backoff от срыва карточки не зависит
        self.assertEqual(len(self._lines("сорвалась")), 1)

    def test_notifier_false_counts_as_not_sent(self):
        attempts = []
        _, _, _, _ = self._run([(STUCK, None)] * 5,
                               notifier=lambda t: attempts.append(t) or False)
        self.assertEqual(len(attempts), 3)                  # жизни 3, 4, 5 — пока не уйдёт

    # --- границы: чего правка не трогает ---
    def test_named_channel_is_never_judged(self):
        spec = dict(rc.NAMED_SPEC, log=self.log)
        kills, cards, pauses, _ = self._run([(STUCK, None)] * 4, spec=spec, checks=40)
        self.assertEqual((kills, cards), ([], []))
        self.assertEqual(pauses, [rc.RESTART_DELAY] * 4)
        self.assertEqual(self._lines("итог жизни") + self._lines("БЕЗ РЕГИСТРАЦИИ"), [])

    def test_detector_without_registered_is_the_old_behaviour(self):
        """Двойник без registered (все прежние тесты ветки) — байт-в-байт прежнее поведение."""
        kills, cards, pauses, _ = self._run([(STUCK, None)] * 4, auth=_Auth(), checks=40)
        self.assertEqual((kills, cards), ([], []))
        self.assertEqual(pauses, [rc.RESTART_DELAY] * 4)
        self.assertEqual(self._lines("итог жизни"), [])

    def test_unreg_pause_sequence(self):
        seq = [rc.unreg_pause(k, lives=3, base=3600, cap=21600) for k in range(1, 9)]
        d = rc.RESTART_DELAY
        self.assertEqual(seq, [d, d, 3600, 7200, 14400, 21600, 21600, 21600])
        self.assertEqual(rc.unreg_pause(10 ** 6, lives=3, base=3600, cap=21600), 21600)
        self.assertEqual(rc.unreg_pause(3, lives=3, base=0, cap=0), d)   # ноль в ручке — не тугой цикл

    def test_defaults_are_the_named_ones(self):
        for k, v in self._saved.items():
            setattr(rc, k, v)
        self.assertEqual((rc.UNREG_LIVES, rc.UNREG_BACKOFF, rc.UNREG_BACKOFF_MAX), (3, 3600, 21600))
        self.assertTrue(rc.UNREG_ENABLED)

    def test_startup_line_names_the_unreg_knobs(self):
        rc.main(singleton=lambda: (True, None), branch_runner=lambda spec: None)
        start = [l for l in self.handler.lines if "супервизор стартовал" in l][0]
        for token in ("жизни без регистрации (rc-server)", "RC_UNREG=1", "RC_UNREG_LIVES=3",
                      "RC_UNREG_BACKOFF=3600с", "RC_UNREG_BACKOFF_MAX=21600с",
                      "rc-server:порог %sс" % rc.SERVER_LIVENESS_MAX_AGE):
            self.assertIn(token, start)
        rc.UNREG_ENABLED = False
        self.assertIn("RC_UNREG=0 (выключено)", rc.unreg_knobs_note(rc.BRANCHES))
        self.assertIn("НЕ судятся", rc.unreg_knobs_note(rc.BRANCHES, auth=_Auth()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
