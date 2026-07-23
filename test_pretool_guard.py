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

# Разведение тестового и боевого лога — ДО импорта гарда и ДО любого subprocess-прогона.
# Прямой запуск `python -m unittest` ловится и сам (log_setup._started_as_test_runner), но
# ДЕТИ (гард запускается subprocess'ом) наследуют только окружение — без этой строки фикстуры
# снова осядут в боевом pretool_guard.log, как 21:39.
os.environ["TURBOBABY_TEST_LOGS"] = "1"

import pretool_guard as g  # noqa: E402

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

    def test_brain_writer_green_by_module_name_not_content(self):
        # доверенный писатель в мозг (класс 328): зелёный ПО ИМЕНИ модуля — содержимое НЕ
        # сканируется, хотя тело модуля законно читает конфиг с секретами (скан дал бы env).
        for c in ('venv/Scripts/python.exe brain_writer.py --name cowork_log "NOTE проба"',
                  'venv/Scripts/python.exe brain_writer.py --id 13kp-54bz "строка"',
                  "venv/Scripts/python.exe brain_writer.py --name index --probe"):
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

    def test_oneoff_script_reading_secret_config_stays_red(self):
        # класс 328 («утренний ASK про пульс»): одноразовый скрипт, который САМ читает конфиг
        # с секретами ради записи в мозг, — красный в ОБЕИХ ролях (скан тела находит доступ
        # к секретам). Легальный канал — доверенный писатель brain_writer (см. CLAUDE.md).
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "oneoff_pulse.py")
            with open(p, "w", encoding="utf-8") as f:
                f.write('vals = open(".env").read()  # сам лезет за секретами\n')
            data = bash('venv/Scripts/python.exe "%s" "строка статуса"' % p)
            self.assertEqual(g.decide(data)[:2], ("ask", "env"))
            self.assertEqual(g.decide_for_role(data, headless=True)[0], "ask")
            self.assertEqual(g.decide_for_role(data, headless=False)[0], "ask")

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
    """Строгий КЛАССИФИКАТОР decide() метит незнакомое как ("ask","unknown") — kind нужен логу.
    РЕШЕНИЕ же принимает decide_for_role: по доктрине VPS незнакомое само по себе НЕ красное
    и молча пропускается в ОБЕИХ ролях (см. TestDoctrineGreen / TestCase314HeadlessReadonly)."""

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


# ============================ ДОКТРИНА (обе роли) ============================
# Перенос доктринального списка с VPS (кейс 314): незнакомая команда сама по себе НЕ красная,
# Allow спрашивает только _stays_red — В ОБЕИХ РОЛЯХ. Исторически доктрина действовала лишь в
# интерактиве (развод по роли после 0014ea6), а headless шёл строгим decide(): чистое чтение
# (`wc -l` по репо, until-grep-ожидание) роняло задачу демона в NEEDS_APPROVAL.

# Команды, которые по доктрине обязаны проходить молча В ОБЕИХ ролях (раньше headless спрашивал).
GREEN_UNDER_DOCTRINE = (
    'powershell -NoProfile -Command "Get-Date -Format o"',   # было ("ask","unknown") — главный источник потопа
    "npm --version",
    "mkdir -p tmp/scratch",
    "rm tmp/scratch/one.txt",                                 # ОДИН явный файл — не «массовое удаление»
    "schtasks /Query /TN TurboBabyRC",                        # /Query — чтение, не контроль задач
    "venv/Scripts/python.exe no_such_script.py",              # py_write «скрипт не прочитан» = неизвестность, не боевая запись
)

# (команда, ожидаемый вид) — красное В ЛЮБОЙ роли по доктрине владельца.
RED_IN_BOTH_ROLES = (
    ('sqlite3 bookings.db "select 1"', "sqlite"),
    ("clasp push", "clasp"),
    ("taskkill /PID 1234 /F", "kill"),
    ('powershell -NoProfile -Command "Stop-Process -Id 1234"', "kill"),
    ("rm -rf tmp/scratch", "delete"),                         # рекурсивное = массовое
    ("del *.tmp", "delete"),                                  # маска = массовое
    ("rm a.txt b.txt", "delete"),                             # больше одной цели = массовое
    ("cat .env", "env"),
    ("schtasks /End /TN TurboBabyRC", "schtasks"),
    ('venv/Scripts/python.exe -c "import bridge; bridge.create_booking()"', "py_write"),
    ("python -c \"import gspread\"", "live_sheet"),
)


class TestRoleDetection(unittest.TestCase):
    """Роль ПО ФАКТУ через штамп демона PRETOOL_ASK_MARKER (переиспользуем существующий
    механизм pc_orchestrator.run_task, а не заводим второй)."""

    def test_marker_means_headless(self):
        self.assertTrue(g.is_headless({"PRETOOL_ASK_MARKER": r"D:\turbobaby-bot\pc_ask_42.marker"}))

    def test_no_marker_means_interactive(self):
        self.assertFalse(g.is_headless({}))
        self.assertFalse(g.is_headless({"PRETOOL_ASK_MARKER": ""}))
        self.assertFalse(g.is_headless({"PRETOOL_ASK_MARKER": "   "}))


class TestDoctrineGreen(unittest.TestCase):
    """ГОЛДЕН: не-доктринальная команда → НОЛЬ Allow в ОБЕИХ ролях."""

    def test_green_passes_silently_in_both_roles(self):
        for c in GREEN_UNDER_DOCTRINE:
            for headless in (False, True):
                self.assertEqual(g.decide_for_role(bash(c), headless=headless)[0], "defer",
                                 (c, headless))

    def test_unknown_is_softened_in_both_roles(self):
        d = bash("some-unheard-of-tool --flag")
        self.assertEqual(g.decide(d), ("ask", "unknown", ""))          # строгий классификатор не изменён
        self.assertEqual(g.decide_for_role(d, headless=False)[0], "defer")
        self.assertEqual(g.decide_for_role(d, headless=True)[0], "defer")   # кейс 314: headless тоже

    def test_edits_inside_repo_pass_but_outside_and_dotclaude_stay_red(self):
        self.assertEqual(g.decide_for_role(edit(os.path.join(PROJ, "suggest.py")), False)[0], "defer")
        # требование п.1: Edit разрешён ТОЛЬКО внутри репо — вне репо остаётся Allow
        self.assertEqual(g.decide_for_role(edit(r"C:\Windows\Temp\x.py"), False)[0], "ask")
        # .claude/* — иначе сессия молча расширила бы собственные права
        self.assertEqual(g.decide_for_role(edit(os.path.join(PROJ, ".claude", "settings.json")), False)[0], "ask")


class TestDoctrineRed(unittest.TestCase):
    """ГОЛДЕНЫ: sqlite3 / clasp / .env / kill / массовое удаление — Allow обязателен В ЛЮБОЙ роли."""

    def test_doctrine_red_still_asks(self):
        for cmd, kind in RED_IN_BOTH_ROLES:
            for headless in (False, True):
                action, got_kind, _ = g.decide_for_role(bash(cmd), headless=headless)
                self.assertEqual(action, "ask", (cmd, headless))
                self.assertEqual(got_kind, kind, (cmd, headless))

    def test_read_env_asks(self):
        for headless in (False, True):
            action, kind, _ = g.decide_for_role(read(os.path.join(PROJ, ".env")), headless=headless)
            self.assertEqual((action, kind), ("ask", "read_secret"))

    def test_mass_delete_vs_single_file(self):
        self.assertFalse(g._is_mass_delete("rm tmp/one.txt"))
        self.assertFalse(g._is_mass_delete("del old.log"))
        for c in ("rm -rf tmp/", "rm -r tmp", "del *.tmp", "rmdir /s tmp",
                  "Remove-Item -Recurse -Force tmp", "rm a.txt b.txt"):
            self.assertTrue(g._is_mass_delete(c), c)


class TestRolesAgree(unittest.TestCase):
    """РЕГРЕСС переноса доктрины: роль решение БОЛЬШЕ НЕ МЕНЯЕТ — headless и интерактив
    совпадают до последнего поля на зелёных, красных и Edit/Read-кейсах."""

    def test_headless_equals_interactive(self):
        cases = ([bash(c) for c in GREEN_UNDER_DOCTRINE]
                 + [bash(c) for c, _ in RED_IN_BOTH_ROLES]
                 + [bash("git status"), bash("venv/Scripts/python.exe -m unittest test_suggest"),
                    edit(os.path.join(PROJ, "suggest.py")), edit(r"C:\Windows\Temp\x.py"),
                    read(os.path.join(PROJ, ".env"))])
        for d in cases:
            self.assertEqual(g.decide_for_role(d, headless=True),
                             g.decide_for_role(d, headless=False), d)

    def test_green_for_both_roles_stays_green(self):
        """Что было зелёным ДО переноса — зелено в обеих ролях (доктрина не ужесточает)."""
        for c in ("git status", "git commit -m 'x'", "venv/Scripts/python.exe -m unittest test_suggest",
                  'venv/Scripts/python.exe cowork_log_append.py "DONE x"',
                  'venv/Scripts/python.exe brain_writer.py --name cowork_log "NOTE x"'):
            self.assertEqual(g.decide_for_role(bash(c), headless=True)[0], "defer", c)
            self.assertEqual(g.decide_for_role(bash(c), headless=False)[0], "defer", c)


class TestCase314HeadlessReadonly(unittest.TestCase):
    """ГОЛДЕН кейса 314 (2026-07-23 14:33/14:56 → id=314 NEEDS_APPROVAL): headless-задача
    считала строки файлов репо (`wc -l`) и ждала вердикт фонового прогона циклом until-grep —
    строгий гард давал ("ask","unknown") на ЧИСТОМ ЧТЕНИИ, демон ронял задачу. Команды —
    ДОСЛОВНО из pretool_guard.log (правило репо: голден = живая строка, не идеализация);
    хвост until-строки в логе обрезан на 300 знаках — восстановлен `cat` того же output."""

    WC = ("wc -l D:/turbobaby-bot/suggest.py D:/turbobaby-bot/test_suggest.py "
          "D:/turbobaby-bot/test_golden_llm.py D:/turbobaby-bot/pc_orchestrator.py")
    OUT = (r"C:\Users\mxfill1\AppData\Local\Temp\claude\D--turbobaby-bot"
           r"\40f1c77d-93b3-4d19-a89c-7002dd27f235\tasks\beo05ww9a.output")
    UNTIL = ('until grep -qE "^(OK|FAILED)" "%s" 2>/dev/null; do sleep 5; done; cat "%s"'
             % (OUT, OUT))

    def test_case314_green_in_both_roles(self):
        for cmd in (self.WC, self.UNTIL):
            for headless in (False, True):
                self.assertEqual(g.decide_for_role(bash(cmd), headless=headless)[0], "defer",
                                 (cmd, headless))

    def test_wc_stat_until_are_classifier_green(self):
        # не смягчение unknown, а ЗЕЛЁНОЕ классификатором: смотрелки wc/stat и цикл ожидания
        for cmd in (self.WC, self.UNTIL, "stat suggest.py",
                    "until grep -q RESULT out.log; do sleep 5; done"):
            self.assertEqual(g.decide(bash(cmd))[0], "defer", cmd)

    def test_until_wait_does_not_whitelist_red(self):
        # красное внутри цикла/хвоста цикл не обеляет: red-признаки всей команды первичны
        for cmd, kind in (("until grep -q X f; do sleep 5; done; rm -rf tmp", "delete"),
                          ("until curl -s https://x/ok; do sleep 5; done", "network"),
                          ("until grep -q X .env; do sleep 5; done", "env")):
            action, got_kind, _ = g.decide_for_role(bash(cmd), headless=True)
            self.assertEqual((action, got_kind), ("ask", kind), cmd)

    def test_e2e_headless_until_no_ask_no_marker(self):
        """Живой прогон процессом в headless-роли (штамп демона в env): ноль Allow, красная
        карточка в файл-маркер демону НЕ пишется — NEEDS_APPROVAL на чтении больше нет."""
        fd, mk = tempfile.mkstemp(suffix=".marker")
        os.close(fd)
        os.remove(mk)   # начинаем с чистого (несуществующего) пути
        env = dict(os.environ, PRETOOL_NOPUSH="1", PYTHONIOENCODING="utf-8", PRETOOL_ASK_MARKER=mk)
        try:
            for cmd in (self.WC, self.UNTIL):
                p = subprocess.run([sys.executable, os.path.join(PROJ, "pretool_guard.py")],
                                   input=json.dumps(bash(cmd)), capture_output=True,
                                   text=True, encoding="utf-8", env=env, timeout=30)
                self.assertEqual(p.returncode, 0, cmd)
                self.assertEqual(p.stdout.strip(), "", cmd)          # ← НОЛЬ Allow
            self.assertFalse(os.path.isfile(mk),
                             "красная карточка ушла демону на чистом чтении (кейс 314)")
        finally:
            try:
                os.remove(mk)
            except Exception:
                pass


class TestAllowFloodFalsePositives(unittest.TestCase):
    """ГОЛДЕНЫ по разбору Allow-потопа 22:27. Из 20 последних подтверждений 6 были ложными:
    3 × запись СВОИХ ЖЕ файлов памяти и 3 × запись во временный скретчпад сессии. Оба —
    рабочая зона самой сессии, подтверждением они ничего не защищали, а поток притуплял
    внимание к настоящему красному. Дословные пути из живого лога."""

    MEM = r"C:\Users\mxfill1\.claude\projects\D--turbobaby-bot\memory\rc-effort-override.md"
    MEM_IDX = r"C:\Users\mxfill1\.claude\projects\D--turbobaby-bot\memory\MEMORY.md"
    SCRATCH = (r"C:\Users\mxfill1\AppData\Local\Temp\claude\D--turbobaby-bot"
               r"\b0e2e361-1db0-478d-93f5-3bb6e8415847\scratchpad\condump.ps1")

    def test_memory_store_is_not_config(self):
        for p in (self.MEM, self.MEM_IDX):
            self.assertFalse(g._is_claude_path(p), p)
            self.assertEqual(g.decide_for_role(edit(p), headless=False)[0], "defer", p)

    def test_session_scratchpad_passes(self):
        self.assertEqual(g.decide_for_role(edit(self.SCRATCH), headless=False)[0], "defer")

    def test_claude_config_stays_red(self):
        """Сужение `.claude` НЕ открывает конфиг: settings/hooks/agents/commands — по-прежнему Allow."""
        for p in (os.path.join(PROJ, ".claude", "settings.json"),
                  os.path.join(PROJ, ".claude", "settings.local.json"),
                  r"C:\Users\mxfill1\.claude\settings.json",
                  r"C:\Users\mxfill1\.claude\hooks\evil.py",
                  r"C:\Users\mxfill1\.claude\agents\x.md",
                  r"C:\Users\mxfill1\.claude.json"):
            self.assertTrue(g._is_claude_path(p), p)
            self.assertEqual(g.decide_for_role(edit(p), headless=False)[:2], ("ask", "edit_claude"), p)

    def test_shell_cannot_bypass_the_config_gate(self):
        """Конфиг закрыт для Write/Edit — значит закрыт и для ОБХОДА через шелл. Иначе дыра
        тривиальна: сессия пишет settings.json.new (внутри репо, зелёное) и копирует его поверх
        боевого одной командой, молча расширив собственные права."""
        for cmd in ("cp .claude/settings.json.new .claude/settings.json",
                    "copy .claude\\settings.json.new .claude\\settings.json",
                    "mv /tmp/x.json .claude/settings.local.json",
                    "echo '{}' > .claude/settings.json",
                    "Copy-Item x.json C:\\Users\\mxfill1\\.claude\\hooks\\evil.py"):
            self.assertEqual(g.decide_for_role(bash(cmd), headless=False)[:2],
                             ("ask", "edit_claude"), cmd)

    def test_reading_config_stays_green(self):
        for cmd in ("cat .claude/settings.json", "type .claude\\settings.json",
                    "Get-Content .claude/settings.json"):
            self.assertEqual(g.decide_for_role(bash(cmd), headless=False)[0], "defer", cmd)

    def test_outside_write_that_is_not_sanctioned_stays_red(self):
        for p in (r"C:\Windows\System32\drivers\etc\hosts", r"C:\Users\mxfill1\Documents\x.txt",
                  r"C:\Users\mxfill1\.claude\projects\D--turbobaby-bot\session.jsonl"):
            self.assertEqual(g.decide_for_role(edit(p), headless=False)[0], "ask", p)


class TestPowerShellTool(unittest.TestCase):
    """PowerShell — ОТДЕЛЬНЫЙ от Bash инструмент и основной шелл этого ПК. До фикса он не попадал
    ни в matcher хука, ни в классификацию: `PowerShell(Remove-Item -Recurse …)` шёл МИМО красного
    гейта целиком. Красное на PowerShell-форме обязано спрашивать так же, как на bash-форме."""

    def _ps(self, cmd):
        return {"tool_name": "PowerShell", "tool_input": {"command": cmd}, "cwd": PROJ}

    def test_powershell_red_forms_ask(self):
        for cmd, kind in (("Remove-Item -Recurse -Force C:\\data", "delete"),
                          ("Stop-Process -Id 1 -Force", "kill"),
                          ("Invoke-WebRequest https://example.com", "network"),
                          ("Get-Content .env", "env")):
            self.assertEqual(g.decide(self._ps(cmd))[:2], ("ask", kind), cmd)
            self.assertEqual(g.decide_for_role(self._ps(cmd), headless=False)[0], "ask", cmd)

    def test_powershell_readonly_passes(self):
        for cmd in ("Get-CimInstance Win32_Process | Select-Object Id",
                    "Get-ChildItem D:\\turbobaby-bot",
                    "Get-Content pc_orchestrator.log -Tail 20"):
            self.assertEqual(g.decide_for_role(self._ps(cmd), headless=False)[0], "defer", cmd)


class TestGuardLog(unittest.TestCase):
    """Смягчение не должно стоить прозрачности: пишем КАЖДОЕ решение обеих ролей."""

    def test_test_run_never_writes_to_live_log(self):
        """ГОЛДЕН разведения тестового и боевого лога. Живой факт 21:39: фикстуры прогона
        (`clasp push`, `sqlite3 bookings.db "select 1"`, пустая команда) осели в БОЕВОМ
        pretool_guard.log и попали в разбор «последних 20 Allow» как реальные события."""
        import log_setup
        live = os.path.join(PROJ, "pretool_guard.log")
        before = os.path.getsize(live) if os.path.isfile(live) else 0
        env = dict(os.environ, TURBOBABY_TEST_LOGS="1")
        self.assertNotEqual(log_setup.log_path(live, env), live)
        g._log("interactive", "Bash", "ask", "clasp", "clasp push")   # фикстура как в живом логе
        after = os.path.getsize(live) if os.path.isfile(live) else 0
        self.assertEqual(before, after, "фикстура теста дописалась в БОЕВОЙ pretool_guard.log")

    def _tmp(self):
        fd, p = tempfile.mkstemp(suffix=".guardlog")
        os.close(fd)
        self.addCleanup(lambda: os.path.isfile(p) and os.remove(p))
        return p

    def test_log_writes_decision(self):
        p = self._tmp()
        g._log("interactive", "Bash", "defer", "unknown", "npm --version", path=p)
        with open(p, encoding="utf-8") as f:
            line = f.read().strip()
        for part in ("interactive", "Bash", "defer", "unknown", "npm --version"):
            self.assertIn(part, line)

    def test_secret_values_are_masked(self):
        line = g._log_line("interactive", "Bash", "defer", "", 'set TELEGRAM_TOKEN=8123:AAF-real-secret')
        self.assertIn("TELEGRAM_TOKEN=***", line)
        self.assertNotIn("AAF-real-secret", line)

    def test_long_command_truncated(self):
        line = g._log_line("headless", "Bash", "ask", "unknown", "x" * 900)
        self.assertLess(len(line), 400)
        self.assertTrue(line.endswith("…"))


class TestRoleEndToEnd(unittest.TestCase):
    """Живой прогон процессом: интерактив = БЕЗ штампа демона в env."""

    def _run(self, data, env_extra=None, raw=None):
        env = dict(os.environ, PRETOOL_NOPUSH="1", PYTHONIOENCODING="utf-8")
        env.pop("PRETOOL_ASK_MARKER", None)     # нет штампа демона ⇒ роль = интерактивная сессия
        env.pop("PRETOOL_MARKER_TOKEN", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, os.path.join(PROJ, "pretool_guard.py")],
                              input=(raw if raw is not None else json.dumps(data)),
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)

    def test_interactive_green_zero_allow_but_logged(self):
        fd, logp = tempfile.mkstemp(suffix=".guardlog")
        os.close(fd)
        try:
            p = self._run(bash('powershell -NoProfile -Command "Get-Date -Format o"'),
                          env_extra={"PRETOOL_GUARD_LOG": logp})
            self.assertEqual(p.returncode, 0)
            self.assertEqual(p.stdout.strip(), "")          # ← НОЛЬ Allow: карточки нет
            with open(logp, encoding="utf-8") as f:
                logged = f.read()
            self.assertIn("interactive", logged)            # ← но след в логе есть
            self.assertIn("defer", logged)
            self.assertIn("Get-Date", logged)
        finally:
            try:
                os.remove(logp)
            except Exception:
                pass

    def test_interactive_sqlite_still_asks(self):
        p = self._run(bash('sqlite3 bookings.db "select 1"'))
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("базу данных", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_interactive_clasp_still_asks(self):
        p = self._run(bash("clasp push"))
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("Apps Script", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_internal_error_is_not_softened(self):
        """FAIL-SAFE: сбой анализа → ask В ЛЮБОЙ роли (гард — единственный красный гейт при
        широком permissions.allow, тихий пропуск на ошибке недопустим)."""
        p = self._run({"tool_name": "Bash", "tool_input": "кривой вход", "cwd": PROJ})
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")


class TestShkvalClassification(unittest.TestCase):
    """Задача «шквал подтверждений» (2026-07-23), проверка КЛАССИФИКАЦИЕЙ (не исполнением):
    зелёная рутина интерактивной сессии обязана проходить БЕЗ промпта (defer), доктринально
    красное — спрашивать (ask) — в ОБЕИХ ролях. Фразы — дословно из карточки задачи
    («kill splinter», запись в CRM), а не идеализированные (правило-класс голденов)."""

    ROUTINE = [
        "wc -l userbot.log",
        "grep -n ERROR userbot.log",
        "cat pc_agent.py",
        "head -50 userbot.log",
        "tail -20 dispatch_notify.log",
        "stat suggest.py",
        "which python",          # не в списке смотрелок → unknown, доктрина смягчает в defer
        "ls -la",
        "git status",
        "git log --oneline -5",
        "git diff -- suggest.py",
        "git add suggest.py",
        "git commit -m 'фикс'",
        "venv/Scripts/python.exe -m py_compile suggest.py",
        "venv/Scripts/python.exe -m unittest test_pricing",
        'venv/Scripts/python.exe cowork_log_append.py "DONE x"',
        'venv/Scripts/python.exe -c "print(1+1)"',
    ]

    def test_routine_defers_in_both_roles(self):
        for c in self.ROUTINE:
            for headless in (False, True):
                self.assertEqual(g.decide_for_role(bash(c), headless=headless)[0], "defer",
                                 (c, headless))

    def test_routine_edit_in_repo_defers(self):
        self.assertEqual(g.decide_for_role(edit(os.path.join(PROJ, "suggest.py")), False)[0],
                         "defer")

    def test_env_read_asks(self):
        for headless in (False, True):
            self.assertEqual(g.decide_for_role(read(os.path.join(PROJ, ".env")),
                                               headless=headless)[0], "ask")
        self.assertEqual(g.decide_for_role(bash("cat .env"), False)[0], "ask")

    def test_kill_splinter_asks(self):
        for c in ("kill splinter", "taskkill /IM splinter.exe /F", "pkill splinter"):
            for headless in (False, True):
                action, kind, _ = g.decide_for_role(bash(c), headless=headless)
                self.assertEqual((action, kind), ("ask", "kill"), (c, headless))

    def test_crm_write_asks(self):
        # запись в живые таблицы/CRM: clasp, gspread из python, sqlite-INSERT — всё ask
        cases = [
            ("clasp push", "clasp"),
            ('venv/Scripts/python.exe -c "import gspread; gspread.service_account()"',
             "live_sheet"),
            ('sqlite3 bookings.db "INSERT INTO b VALUES (1)"', "sqlite"),
        ]
        for c, kind in cases:
            for headless in (False, True):
                action, got_kind, _ = g.decide_for_role(bash(c), headless=headless)
                self.assertEqual((action, got_kind), ("ask", kind), (c, headless))

    def test_git_force_asks(self):
        for c in ("git push --force origin main", "git reset --hard HEAD~1", "git clean -fd"):
            for headless in (False, True):
                self.assertEqual(g.decide_for_role(bash(c), headless=headless)[0], "ask",
                                 (c, headless))


class TestSettingsThreeLayers(unittest.TestCase):
    """Трёхслойная схема прав (та же задача): allow широкий на рутину, ask — только
    доктринально красное, deny — абсолютный запрет, bypassPermissions выключен, гард
    зарегистрирован PreToolUse-хуком (слой сужения поверх широкого allow), Notification-хук
    на месте. Строго проверяем ПОДГОТОВЛЕННЫЙ файл docs/artifacts/2026-07-23-settings-tri-layer.json
    (headless не имеет права писать sensitive .claude/settings.json — файл применяет владелец
    после «да», см. одноимённый .md); боевой файл — мягко: хуки обязаны быть уже сейчас,
    слои — как только файл применён (появился ключ deny)."""

    STAGED = os.path.join(PROJ, "docs", "artifacts", "2026-07-23-settings-tri-layer.json")
    LIVE = os.path.join(PROJ, ".claude", "settings.json")

    @staticmethod
    def _load(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_staged_allow_is_broad_routine(self):
        allow = self._load(self.STAGED)["permissions"]["allow"]
        for rule in ("Bash", "PowerShell", "Read",
                     "Edit(//d/turbobaby-bot/**)", "Write(//d/turbobaby-bot/**)"):
            self.assertIn(rule, allow)

    def test_staged_ask_is_doctrinal_only(self):
        ask = self._load(self.STAGED)["permissions"]["ask"]
        for rule in ("Read(//d/turbobaby-bot/.env)", "Edit(//d/turbobaby-bot/.env)",
                     "Bash(sqlite3 *)", "Bash(clasp *)", "Bash(kill *)", "Bash(pkill *)",
                     "Bash(taskkill *)", "Bash(rm -rf *)", "Bash(git push --force*)",
                     "Edit(//d/turbobaby-bot/.claude/**)", "PowerShell(Stop-Process *)"):
            self.assertIn(rule, ask)
        # рутина в ask НЕ живёт — иначе шквал вернётся
        for rule in ask:
            for green in ("git status", "git commit", "git add", "grep", "wc ",
                          "py_compile", "unittest", "cat "):
                self.assertNotIn(green, rule, rule)

    def test_staged_deny_absolute(self):
        deny = self._load(self.STAGED)["permissions"]["deny"]
        for rule in ("Bash(rm -rf /)", "Bash(chmod -R 777 /)", "Bash(dd of=/dev/*)",
                     "Bash(*--dangerously-skip-permissions*)", "Bash(*--no-verify*)"):
            self.assertIn(rule, deny)

    def test_staged_no_bypass_permissions(self):
        perms = self._load(self.STAGED)["permissions"]
        self.assertEqual(perms.get("defaultMode"), "acceptEdits")
        self.assertNotIn("bypassPermissions", json.dumps(perms))

    def test_staged_hooks_guard_and_notification(self):
        hooks = self._load(self.STAGED)["hooks"]
        pre = hooks["PreToolUse"][0]
        for tool in ("Bash", "PowerShell", "Edit", "Write", "Read"):
            self.assertIn(tool, pre["matcher"])       # PowerShell — основной инструмент ПК
        self.assertIn("pretool_guard.py", pre["hooks"][0]["command"])
        self.assertIn("--hook notification", hooks["Notification"][0]["hooks"][0]["command"])

    def test_live_hooks_registered_for_interactive(self):
        # интерактивная роль получает гард ИЗ ЭТОГО файла (роль в matcher не участвует) —
        # регистрация обязана быть уже в текущем боевом settings.json
        hooks = self._load(self.LIVE)["hooks"]
        self.assertIn("pretool_guard.py", hooks["PreToolUse"][0]["hooks"][0]["command"])
        self.assertIn("--hook notification", hooks["Notification"][0]["hooks"][0]["command"])

    def test_live_three_layers_once_applied(self):
        perms = self._load(self.LIVE).get("permissions") or {}
        if "deny" not in perms:
            self.skipTest("итоговый settings ещё не применён владельцем (ждёт «да»)")
        staged = self._load(self.STAGED)
        live = self._load(self.LIVE)
        self.assertEqual(live["permissions"]["deny"], staged["permissions"]["deny"])
        self.assertIn("PowerShell", live["hooks"]["PreToolUse"][0]["matcher"])
        self.assertNotEqual(perms.get("defaultMode"), "bypassPermissions")


if __name__ == "__main__":
    unittest.main(verbosity=2)
