# -*- coding: utf-8 -*-
"""
test_pretool_guard.py — тесты классификатора PreToolUse-гарда. НИКАКИХ реальных
разрушительных действий не выполняется: гоняем только классификатор decide() и
end-to-end через stdin с PRETOOL_NOPUSH=1 (без реального Telegram).
"""

import os
import sys
import json
import subprocess
import unittest

import pretool_guard as g

PROJ = r"D:\turbobaby-bot"


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": PROJ}


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": PROJ}


def read(path):
    return {"tool_name": "Read", "tool_input": {"file_path": path}, "cwd": PROJ}


class TestGreenDefer(unittest.TestCase):
    def _defer(self, data):
        self.assertEqual(g.decide(data)[0], "defer", data)

    def test_git_safe(self):
        for c in ("git status", "git diff", "git log --oneline", "git add pricing.py",
                  "git commit -m 'x'", "git push origin main", "git check-ignore pricing.py"):
            self._defer(bash(c))

    def test_tests_and_scripts(self):
        for c in ("venv/Scripts/python.exe -m pytest",
                  "venv/Scripts/python.exe -m py_compile suggest.py",
                  "venv/Scripts/python.exe -m unittest test_suggest",
                  'venv/Scripts/python.exe cowork_log_append.py "DONE x"',
                  'venv/Scripts/python.exe dispatch_notify.py --hook stop'):
            self._defer(bash(c))

    def test_readonly_shell(self):
        for c in ("ls -la", "echo hi", "grep foo bar.py", "git status", "type suggest.py"):
            self._defer(bash(c))

    def test_python_inline_readonly(self):
        self._defer(bash('venv/Scripts/python.exe -c "print(1+1)"'))

    def test_edit_write_read_in_project(self):
        self._defer(edit(r"D:\turbobaby-bot\suggest.py"))
        self._defer({"tool_name": "Write", "tool_input": {"file_path": r"D:\turbobaby-bot\new.py"}, "cwd": PROJ})
        self._defer(read(r"D:\turbobaby-bot\suggest.py"))

    def test_read_outside_nonsecret_defers(self):
        self._defer(read(r"C:\Users\mxfill1\notes.txt"))

    def test_other_tools_defer(self):
        self._defer({"tool_name": "Grep", "tool_input": {"pattern": "x"}})
        self._defer({"tool_name": "Glob", "tool_input": {"pattern": "**/*.py"}})


class TestRedAsk(unittest.TestCase):
    def _ask(self, data):
        self.assertEqual(g.decide(data)[0], "ask", data)

    def test_delete(self):
        for c in ("del foo.txt", "erase foo.txt", "rmdir /S build", "rm -rf tmp", "Remove-Item x"):
            self._ask(bash(c))

    def test_kill_and_scheduler(self):
        for c in ("taskkill /PID 123 /F", "kill 42", "powershell -Command Stop-Process -Id 5",
                  "schtasks /Run /TN pc_agent"):
            self._ask(bash(c))

    def test_git_dangerous(self):
        for c in ("git push --force origin main", "git push -f", "git reset --hard HEAD~1", "git clean -fd"):
            self._ask(bash(c))

    def test_db(self):
        self._ask(bash("sqlite3 moderation_ipc.db"))

    def test_env_secret_access(self):
        for c in ("cat .env", "grep -oE '^[A-Z_]+=' .env", "type .env", "Get-Content .env"):
            self._ask(bash(c))

    def test_network(self):
        for c in ("curl https://example.com", "wget http://x", "scp f root@h:/", "ssh root@h ls"):
            self._ask(bash(c))

    def test_outside_write(self):
        self._ask(bash(r'echo x > C:\Windows\Temp\p.txt'))

    def test_python_red_tokens(self):
        self._ask(bash('venv/Scripts/python.exe -c "add_transaction(amount=100)"'))
        self._ask(bash('venv/Scripts/python.exe -c "import os; os.remove(\'x\')"'))

    def test_python_touches_env(self):
        self._ask(bash('venv/Scripts/python.exe -c "open(\'.env\').read()"'))

    def test_edit_secret_and_claude_and_outside(self):
        self._ask(edit(r"D:\turbobaby-bot\.env"))
        self._ask(edit(r"D:\turbobaby-bot\turbobaby_session.session"))
        self._ask(edit(r"D:\turbobaby-bot\.claude\settings.json"))
        self._ask({"tool_name": "Write", "tool_input": {"file_path": r"C:\Users\mxfill1\evil.txt"}, "cwd": PROJ})

    def test_read_secret(self):
        self._ask(read(r"D:\turbobaby-bot\.env"))
        self._ask(read(r"D:\turbobaby-bot\turbobaby_session.session"))


class TestUnknownAsk(unittest.TestCase):
    def _ask(self, data):
        self.assertEqual(g.decide(data)[0], "ask", data)

    def test_weird_command(self):
        self._ask(bash("frobnicate --weird /x"))

    def test_python_stdin(self):
        self._ask(bash("venv/Scripts/python.exe -"))

    def test_python_missing_file(self):
        self._ask(bash("venv/Scripts/python.exe no_such_file_zzz.py"))

    def test_python_unknown_module(self):
        self._ask(bash("venv/Scripts/python.exe -m somemodule"))


class TestEndToEndStdin(unittest.TestCase):
    """Гоняем гард как процесс: stdin JSON → exit 0; green без вывода, red c permissionDecision ask.
    PRETOOL_NOPUSH=1 → без реального Telegram."""
    def _run(self, data):
        env = dict(os.environ, PRETOOL_NOPUSH="1", PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, os.path.join(PROJ, "pretool_guard.py")],
                           input=json.dumps(data), capture_output=True, text=True,
                           encoding="utf-8", env=env, timeout=30)
        return p

    def test_green_no_output(self):
        p = self._run(bash("git status"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")

    def test_red_emits_ask(self):
        p = self._run(bash("taskkill /PID 1 /F"))
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("КРАСНОЕ", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_unparseable_stdin_defers(self):
        env = dict(os.environ, PRETOOL_NOPUSH="1", PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, os.path.join(PROJ, "pretool_guard.py")],
                           input="not json", capture_output=True, text=True,
                           encoding="utf-8", env=env, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
