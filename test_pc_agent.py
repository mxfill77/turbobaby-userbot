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


if __name__ == "__main__":
    unittest.main(verbosity=2)
