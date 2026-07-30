# -*- coding: utf-8 -*-
"""
test_utf8_output_guard.py — СТРАЖ класса кодировки вывода ПК-контура.

Класс укусил ТРИЖДЫ за 29–30.07.2026: текст с не-ASCII (кириллица, эмодзи 📊 ⏹ 🔔) уходит
наружу без явного UTF-8, Python на Windows берёт кодировку консоли (cp1251) →
  • эмодзи вне cp1251 РОНЯЮТ процесс: `'charmap' codec can't encode character '\\U0001f4ca'`
    (📊) — так кнопка «Статус цепи» вместо статуса присылала traceback;
  • кириллица в UTF-8-приёмник ложится МОХИБЕЙКОМ (093119e: «С‚РёРї РўРЎ» вместо «тип ТС»).
Чинили ПО ОДНОМУ месту из трёх (журнал → dispatch_notify → кнопки) — класс возвращался.

Страж держит по ФАКТУ ИСХОДНИКА (а не по договорённости «не забудь») два инварианта:
  A) КАЖДЫЙ вход-процесс контура первой командой зовёт io_utf8.force_utf8() → его stdout/stderr
     пишут UTF-8, откуда бы Планировщик/терминал/родитель его ни поднял (полоса ЗАПИСИ);
  B) КАЖДЫЙ subprocess.run/Popen/check_output в текстовом режиме (text=True/universal_newlines)
     задаёт encoding= явно → чужой вывод декодируется UTF-8, а не локалью (полоса ЧТЕНИЯ).
Новое место (свежий print-скрипт без force_utf8 либо свежий text=True без encoding) валит гейт —
класс не вернётся четвёртый раз молча.

ГРАНИЦЫ задачи: userbot_listen.py, moderation_bot.py и клиентский suggest.py тут НЕ проверяются
(их правка запрещена контуром) — тот же класс на них остаётся отдельным остатком.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_utf8_output_guard -v
"""
import ast
import os
import sys
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
if not os.path.isfile(PY):
    PY = sys.executable

# Вход-процессы контура, ПЕЧАТАЮЩИЕ наружу (print / logging→stdout) → обязаны звать force_utf8().
ENTRYPOINTS = ("pc_agent.py", "pc_orchestrator.py", "dispatch_notify.py", "cowork_log_append.py")

# Модули контура, ЧИТАЮЩИЕ чужой вывод subprocess'ом → каждый text-режим обязан задать encoding=.
# rc_supervisor.py уже был чист (encoding на месте) — держим его под стражем, чтобы не отъехал;
# lesson_router.py правится этой же задачей (git rev-parse, вывод отбрасывался, но класс тот же).
SUBPROCESS_READERS = ENTRYPOINTS + ("selfupdate_gate.py", "rc_supervisor.py", "lesson_router.py")

# Границы: правка этих файлов запрещена (боты/клиент). Держим их СПИСКОМ явно, чтобы страж не
# притворялся, будто закрыл и их: тот же класс здесь — отдельный остаток с точками касания.
OUT_OF_SCOPE = ("userbot_listen.py", "moderation_bot.py", "suggest.py")

_RUNNERS = {"run", "Popen", "check_output"}


def _parse(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return ast.parse(f.read(), filename=name)


def _callee_name(func):
    """Имя вызываемого. Разворачиваем (runner or subprocess.run)(...) — иначе инъекция-раннер прячет
    настоящий subprocess.run от статической проверки."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.BoolOp):
        for v in func.values:
            n = _callee_name(v)
            if n:
                return n
    return None


def _is_true(node):
    return isinstance(node, ast.Constant) and node.value is True


def subprocess_text_calls_without_encoding(tree):
    """→ [lineno] для subprocess.run/Popen/check_output в ТЕКСТОВОМ режиме БЕЗ encoding=."""
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _callee_name(node.func) not in _RUNNERS:
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        text_mode = _is_true(kw.get("text")) or _is_true(kw.get("universal_newlines"))
        if text_mode and "encoding" not in kw:
            bad.append(node.lineno)
    return sorted(bad)


def calls_force_utf8(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee_name(node.func) == "force_utf8":
            return True
    return False


class TestEntrypointsForceUtf8(unittest.TestCase):
    """Полоса ЗАПИСИ: каждый печатающий вход-процесс переключает stdout/stderr в UTF-8."""

    def test_all_entrypoints_call_force_utf8(self):
        missing = [n for n in ENTRYPOINTS if not calls_force_utf8(_parse(n))]
        self.assertEqual(
            missing, [],
            "нет вызова io_utf8.force_utf8(): под cp1251-консолью Планировщика print с эмодзи "
            "(📊/⏹/🔔) упадёт 'charmap codec can't encode'. Файлы: %s" % missing)


class TestSubprocessReadsUtf8(unittest.TestCase):
    """Полоса ЧТЕНИЯ: каждый text-режим subprocess задаёт encoding='utf-8'."""

    def test_no_text_mode_without_encoding(self):
        offenders = {}
        for n in SUBPROCESS_READERS:
            bad = subprocess_text_calls_without_encoding(_parse(n))
            if bad:
                offenders[n] = bad
        self.assertEqual(
            offenders, {},
            "subprocess.run(text=True) без encoding= читает чужой вывод локалью Windows (cp1251) "
            "→ мохибейк/падение. Добавь encoding='utf-8', errors='replace'. Строки: %s" % offenders)


class TestOutOfScopeListedHonestly(unittest.TestCase):
    """Границы не молчаливые: файлы, которые страж НЕ закрывает, перечислены и существуют."""

    def test_out_of_scope_files_exist(self):
        for n in OUT_OF_SCOPE:
            self.assertTrue(os.path.isfile(os.path.join(HERE, n)),
                            "границу заявили, а файла нет: %s" % n)


class TestButtonEmojiRoundTrip(unittest.TestCase):
    """ЖИВОЙ путь кнопки БЕЗ сети: ребёнок печатает эмодзи в пайп (как pc_orchestrator
    --chain-status), родитель читает его теми же kwargs, что pc_agent._chain_cli. Эмодзи в коде
    ребёнка задан ASCII-escape'ом (\\U0001F4CA) — argv чист, проверяем именно encode/decode, а не
    доставку argv. PYTHONUTF8/PYTHONIOENCODING из окружения ребёнка убираем: под тестом работает
    ТОЛЬКО force_utf8() (иначе на cp1251-Windows ребёнок упал бы 'charmap')."""

    def _round_trip(self, py_string_literal):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        code = "import io_utf8; io_utf8.force_utf8(); print(%s)" % py_string_literal
        return subprocess.run([PY, "-c", code], cwd=HERE, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)

    def test_status_button_bar_chart_survives(self):
        # 📊 = U+1F4CA — ровно тот символ, на котором падала «Статус цепи».
        r = self._round_trip(r"'\U0001F4CA цепь #1: демо (в работе), done 2'")
        self.assertEqual(r.returncode, 0, "ребёнок упал: %s" % r.stderr)
        self.assertNotIn("charmap", r.stderr or "", "класс вернулся: %s" % r.stderr)
        self.assertIn("\U0001F4CA", r.stdout, "📊 не дошёл целым: %r" % r.stdout)
        self.assertIn("цепь #1", r.stdout)                       # кириллица тоже цела

    def test_stop_button_stop_symbol_survives(self):
        # ⏹ = U+23F9 — эмодзи кнопки «Стоп цепи».
        r = self._round_trip(r"'\u23F9 цепь #1 остановлена'")
        self.assertEqual(r.returncode, 0, "ребёнок упал: %s" % r.stderr)
        self.assertIn("\u23F9", r.stdout, "⏹ не дошёл целым: %r" % r.stdout)


if __name__ == "__main__":
    unittest.main()
