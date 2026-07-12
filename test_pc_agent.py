# -*- coding: utf-8 -*-
"""
test_pc_agent.py — тесты pc_agent (разбор #128, часть 1): stdout/stderr детей уходят в
logs/<имя>_stderr.log (APPEND), чтобы смерть ребёнка оставляла traceback.
Реальные боты НЕ поднимаются: спавним безобидный python -c, пишущий в stderr.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_pc_agent -v
"""
import os
import sys
import time
import tempfile
import subprocess
import unittest
from pathlib import Path

import pc_agent as a


class TestChildStderrLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._save_logs = a.LOGS_DIR
        a.LOGS_DIR = Path(self.tmp)          # не пачкаем реальный logs/

    def tearDown(self):
        a.LOGS_DIR = self._save_logs

    def test_child_log_handle_creates_file_with_header(self):
        fh = a._child_log_handle("userbot")
        try:
            path = Path(self.tmp) / "userbot_stderr.log"
            self.assertTrue(path.exists())
            self.assertIn("userbot: старт", path.read_text(encoding="utf-8"))
        finally:
            fh.close()

    def test_child_log_handle_appends(self):
        a._child_log_handle("moderbot").close()
        a._child_log_handle("moderbot").close()
        path = Path(self.tmp) / "moderbot_stderr.log"
        # две шапки старта → APPEND, историю не перетёрли
        self.assertEqual(path.read_text(encoding="utf-8").count("moderbot: старт"), 2)

    def test_child_stderr_traceback_captured(self):
        """Смерть ребёнка с traceback в stderr ДОЛЖНА оседать в файле (суть фикса)."""
        logf = a._child_log_handle("userbot")
        p = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.write('BOOM_TRACEBACK_XYZ'); sys.exit(1)"],
            stdout=logf, stderr=subprocess.STDOUT)
        p.wait(timeout=30)
        logf.close()
        # дать ОС дописать буфер ребёнка
        for _ in range(20):
            data = (Path(self.tmp) / "userbot_stderr.log").read_text(encoding="utf-8")
            if "BOOM_TRACEBACK_XYZ" in data:
                break
            time.sleep(0.1)
        self.assertIn("BOOM_TRACEBACK_XYZ", data)


class TestUnknownCommandReply(unittest.TestCase):
    """ФИКС #171/3: неизвестная команда темы 205 → эхо + перечень РЕАЛЬНЫХ команд, не тишина."""

    def test_echoes_unknown_and_lists_real_commands(self):
        r = a.unknown_command_reply("статус контура")
        self.assertIn("не знаю", r)
        self.assertIn("статус контура", r)          # эхо непонятого
        self.assertIn("умею", r)
        self.assertIn("обнови userbot", r)          # реальная команда из роутинга
        self.assertIn("стоп модербот", r)
        self.assertIn("обновись", r)

    def test_empty_command(self):
        r = a.unknown_command_reply("")
        self.assertIn("умею", r)
        self.assertIn("обнови userbot", r)

    def test_long_command_truncated(self):
        r = a.unknown_command_reply("ы" * 500)
        self.assertIn("…", r)
        self.assertLess(len(r), 500 + 400)          # эхо обрезан, не раздувает ответ

    def test_known_commands_nonempty(self):
        self.assertTrue(len(a.KNOWN_COMMANDS) >= 7)


class TestSupervisionLabel(unittest.TestCase):
    """status показывает СОСТОЯНИЕ НАДЗОРА (под вотчдогом / cooldown / halt) вместо «кто запустил».
    Читаем снимок демона; чистая _supervision_label покрыта по ветвям, status_text — интеграция."""

    NOW = 1_000_000.0
    COOLDOWN = 900

    def _snap(self, ent, ts=None):
        return {"ts": self.NOW if ts is None else ts, "cooldown": self.COOLDOWN,
                "max_deaths": 3, "children": {"userbot": ent}}

    def test_stop_switch_wins(self):
        # рубильник взведён → надзор намеренно выключен, снимок игнорируем
        lbl = a._supervision_label("userbot", self._snap({"deaths": 0, "halted": False, "last_raise": 0}),
                                   self.NOW, stop_present=True)
        self.assertIn("выключен", lbl)
        self.assertIn("рубильник", lbl)

    def test_no_snapshot_demon_silent(self):
        lbl = a._supervision_label("userbot", None, self.NOW, stop_present=False)
        self.assertIn("демон молчит", lbl)

    def test_stale_snapshot_demon_silent(self):
        old = self._snap({"deaths": 0, "halted": False, "last_raise": 0}, ts=self.NOW - 10_000)
        lbl = a._supervision_label("userbot", old, self.NOW, stop_present=False, stale=900)
        self.assertIn("протух", lbl)

    def test_healthy_under_watchdog(self):
        lbl = a._supervision_label("userbot", self._snap({"deaths": 0, "halted": False, "last_raise": 0}),
                                   self.NOW, stop_present=False)
        self.assertIn("под вотчдогом", lbl)
        self.assertNotIn("cooldown", lbl)

    def test_cooldown_after_recent_raise(self):
        # недавно поднимали (600с назад, cooldown 900) → под вотчдогом · cooldown, ~300с осталось
        ent = {"deaths": 1, "halted": False, "last_raise": self.NOW - 600}
        lbl = a._supervision_label("userbot", self._snap(ent), self.NOW, stop_present=False)
        self.assertIn("cooldown", lbl)
        self.assertIn("300", lbl)

    def test_halt_needs_review(self):
        ent = {"deaths": 3, "halted": True, "last_raise": self.NOW - 10}
        lbl = a._supervision_label("userbot", self._snap(ent), self.NOW, stop_present=False)
        self.assertIn("ОСТАНОВЛЕН", lbl)
        self.assertIn("3", lbl)
        self.assertIn("разбор", lbl)

    def test_child_missing_from_snapshot(self):
        snap = {"ts": self.NOW, "cooldown": self.COOLDOWN, "children": {}}
        lbl = a._supervision_label("userbot", snap, self.NOW, stop_present=False)
        self.assertIn("нет в снимке", lbl)

    def test_status_text_shows_supervision_not_launcher(self):
        # интеграция: живой userbot + снимок «под вотчдогом» → в шапке НАДЗОР, НЕ «вручную/агентом»
        save = (a.UB.status, a._read_watch_snapshot, a._read_log_lines, a.STOP_FLAG)

        class _Stop:
            @staticmethod
            def exists():
                return False
        try:
            a.UB.status = lambda: (True, [7572], False)   # alive, orphan-PID, managed=False (был бы «вручную»)
            a._read_watch_snapshot = lambda *x, **k: {"ts": time.time(), "cooldown": 900,
                                                      "children": {"userbot": {"deaths": 0, "halted": False,
                                                                               "last_raise": 0}}}
            a._read_log_lines = lambda *x, **k: []
            a.STOP_FLAG = _Stop
            txt = a.status_text()
        finally:
            (a.UB.status, a._read_watch_snapshot, a._read_log_lines, a.STOP_FLAG) = save
        self.assertIn("РАБОТАЕТ", txt)
        self.assertIn("PID 7572", txt)
        self.assertIn("надзор:", txt)
        self.assertIn("под вотчдогом", txt)
        self.assertNotIn("вручную", txt)                 # способ запуска убран
        self.assertNotIn("агентом", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
