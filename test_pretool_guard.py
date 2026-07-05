# -*- coding: utf-8 -*-
"""
test_pretool_guard.py — тесты классификатора PreToolUse-гарда. НИКАКИХ реальных
разрушительных действий не выполняется: гоняем только классификатор decide() и
end-to-end через stdin с PRETOOL_NOPUSH=1 (без реального Telegram).
"""

import os
import sys
import json
import tempfile
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

    def test_test_runner_modules_defer_without_scanning_args(self):
        # -m unittest / -m pytest c тест-файлами-аргументами: green-модуль, арг-файлы НЕ сканируем
        # (порт VPS-урока). Тест-файлы содержат красные токены-фикстуры — не должны триггерить ask.
        for c in ("venv/Scripts/python.exe -m unittest test_pretool_guard",
                  "venv/Scripts/python.exe -m unittest test_pretool_guard test_pc_orchestrator",
                  "venv/Scripts/python.exe -m pytest test_pretool_guard.py",
                  "python -m unittest -v test_pc_orchestrator"):
            self._defer(bash(c))

    def test_direct_test_file_run_defers(self):
        # прямой запуск test_*.py (в т.ч. tests/test_*.py) — defer без контент-скана: тела этих
        # файлов — фикстуры с .env/os.remove/add_transaction, а не боевая запись (гейтуются в репо).
        for c in ("venv/Scripts/python.exe test_pretool_guard.py",
                  "venv/Scripts/python.exe test_pc_orchestrator.py",
                  "python test_pretool_guard.py",
                  "venv/Scripts/python.exe tests/test_something.py",
                  r"venv\Scripts\python.exe tests\test_something.py"):
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

    def test_nontest_script_still_scanned(self):
        # красное НЕ ослаблено: не-тест .py по-прежнему сканируется на боевую запись.
        # pretool_guard.py содержит токен os.remove (в списке _RED_PY_TOKENS) → ask.
        self._ask(bash("venv/Scripts/python.exe pretool_guard.py"))
        # тест-файл-аргумент рядом с не-тест целью НЕ обеляет её (не-тест всё равно сканируется):
        self._ask(bash("venv/Scripts/python.exe pretool_guard.py test_pretool_guard.py"))

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
    def _run(self, data, raw=None):
        # ИЗОЛЯЦИЯ МАРКЕРА: свой tempfile в PRETOOL_ASK_MARKER, чтобы тест НИКОГДА не писал в
        # боевой маркер демона (иначе фикстурные красные карточки → «призрак» ложного ask).
        fd, mk = tempfile.mkstemp(suffix=".marker")
        os.close(fd)
        os.remove(mk)   # начинаем с чистого (несуществующего) пути
        env = dict(os.environ, PRETOOL_NOPUSH="1", PYTHONIOENCODING="utf-8", PRETOOL_ASK_MARKER=mk)
        try:
            p = subprocess.run([sys.executable, os.path.join(PROJ, "pretool_guard.py")],
                               input=(raw if raw is not None else json.dumps(data)),
                               capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        finally:
            try:
                os.remove(mk)
            except Exception:
                pass
        return p

    def test_green_no_output(self):
        p = self._run(bash("git status"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")

    def test_red_emits_ask(self):
        p = self._run(bash("taskkill /PID 1 /F"))
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        reason = out["hookSpecificOutput"]["permissionDecision"], out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(reason[0], "ask")
        # человеческая карточка: ЧТО + «— разрешить?», не сырая команда первой строкой
        self.assertIn("Хочу снять процесс", reason[1])
        self.assertIn("разрешить?", reason[1])
        self.assertTrue(reason[1].lstrip().startswith("🔴"))

    def test_unparseable_stdin_defers(self):
        p = self._run(None, raw="not json")   # тот же изолированный маркер
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")


class TestHumanCards(unittest.TestCase):
    def test_card_is_human_not_raw(self):
        card = g._card("delete", "old.log", "del /f old.log")
        self.assertTrue(card.lstrip().startswith("🔴 Хочу удалить файл old.log"))
        self.assertIn("разрешить?", card)
        self.assertIn("Команда: del /f old.log", card)   # сырая команда — отдельной строкой, не первой
        self.assertLess(card.index("Хочу"), card.index("Команда:"))

    def test_human_phrases(self):
        self.assertIn("снять процесс PID 42", g._human("kill", "PID 42"))
        self.assertIn("секретный файл .env", g._human("edit_secret", ".env"))
        self.assertIn(".env / секрет", g._human("env"))
        self.assertIn("git-историю", g._human("git_force"))
        self.assertIn("за пределами проекта", g._human("write_outside", r"C:\x.txt"))
        self.assertIn("не распознана как безопасная", g._human("unknown"))

    def test_extractors(self):
        self.assertEqual(g._extract_delete_target("del /f old.log"), "old.log")
        self.assertEqual(g._extract_kill_target("taskkill /PID 42 /F"), "PID 42")
        self.assertEqual(g._extract_kill_target("Stop-Process -Id 7"), "PID 7")
        self.assertEqual(g._extract_host("curl https://api.telegram.org/bot/x"), "api.telegram.org")
        self.assertEqual(g._extract_host("ssh root@5.223.94.179 ls"), "root@5.223.94.179")

    def test_env_edit_card_via_decide(self):
        action, kind, obj = g.decide(edit(r"D:\turbobaby-bot\.env"))
        self.assertEqual(action, "ask")
        card = g._card(kind, obj)
        self.assertIn("секретный файл", card)
        self.assertIn(".env", card)
        self.assertNotIn("D:\\turbobaby-bot", card)   # показываем имя файла, не команду


class TestMarkerDedup(unittest.TestCase):
    """PRETOOL_ASK_MARKER: дедуп карточек. claude мог ретраить красное → карточка набегала ×5."""

    def _tmp_marker(self):
        import tempfile
        fd, mk = tempfile.mkstemp(suffix=".marker")
        os.close(fd)
        os.remove(mk)   # начинаем с чистого (несуществующего) пути
        return mk

    def test_write_marker_dedups_repeats(self):
        mk = self._tmp_marker()
        try:
            card = "🔴 Хочу удалить файл X — разрешить?\nКоманда: del X"
            for _ in range(5):
                g._write_marker(mk, card)
            with open(mk, encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content.count("Хочу удалить файл X"), 1)   # была ×5 → стала ×1
        finally:
            try:
                os.remove(mk)
            except Exception:
                pass

    def test_write_marker_keeps_distinct_cards(self):
        mk = self._tmp_marker()
        try:
            g._write_marker(mk, "🔴 карточка A — разрешить?")
            g._write_marker(mk, "🔴 карточка B — разрешить?")
            g._write_marker(mk, "🔴 карточка A — разрешить?")   # повтор A не добавляется
            with open(mk, encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content.count("карточка A"), 1)
            self.assertEqual(content.count("карточка B"), 1)   # разные карточки сохраняются
        finally:
            try:
                os.remove(mk)
            except Exception:
                pass


class TestMarkerIsolation(unittest.TestCase):
    """Регресс «призрака PID 1»: полный прогон тестов под выставленным БОЕВЫМ PRETOOL_ASK_MARKER
    не должен записать в него ни байта (иначе демон принял бы тест-фикстуру за реальный ask)."""

    @unittest.skipIf(os.environ.get("PRETOOL_ISOLATION_CHILD") == "1", "дочерний прогон — избегаем рекурсии")
    def test_live_marker_stays_empty_after_full_suite(self):
        fd, mk = tempfile.mkstemp(suffix=".livemarker")
        os.close(fd)
        with open(mk, "w", encoding="utf-8") as f:
            f.write("")                                   # боевой маркер стартует пустым
        env = dict(os.environ, PRETOOL_ASK_MARKER=mk, PRETOOL_MARKER_TOKEN="daemon-run-probe",
                   PRETOOL_NOPUSH="1", PRETOOL_ISOLATION_CHILD="1", PYTHONIOENCODING="utf-8")
        try:
            r = subprocess.run([sys.executable, "-m", "unittest",
                                "test_pretool_guard", "test_pc_orchestrator"],
                               cwd=PROJ, capture_output=True, text=True, env=env, timeout=300)
            with open(mk, encoding="utf-8", errors="ignore") as f:
                leaked = f.read()
            self.assertEqual(r.returncode, 0, (r.stdout or "") + "\n" + (r.stderr or ""))
            self.assertEqual(leaked, "",                  # ← файл ПУСТ = ни один тест не тронул боевой маркер
                             "боевой PRETOOL_ASK_MARKER наполнился тестами (призрак):\n" + leaked[:800])
        finally:
            try:
                os.remove(mk)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
