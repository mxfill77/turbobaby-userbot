# -*- coding: utf-8 -*-
"""
test_utf8_output_guard.py — СТРАЖ класса кодировки вывода ПК-контура.

Класс укусил ТРИЖДЫ за 29–30.07.2026: текст с не-ASCII (кириллица, эмодзи 📊 ⏹ 🔔) уходит
наружу без явного UTF-8, Python на Windows берёт кодировку консоли (cp1251) →
  • эмодзи вне cp1251 РОНЯЮТ процесс: `'charmap' codec can't encode character '\\U0001f4ca'`
    (📊) — так кнопка «Статус цепи» вместо статуса присылала traceback;
  • кириллица в UTF-8-приёмник ложится МОХИБЕЙКОМ (093119e: «С‚РёРї РўРЎ» вместо «тип ТС»).
Чинили ПО ОДНОМУ месту из трёх (журнал → dispatch_notify → кнопки) — класс возвращался.

Страж держит по ФАКТУ ИСХОДНИКА (а не по договорённости «не забудь») ТРИ инварианта — по одному
на КАЖДЫЙ канал, которым текст уходит наружу:
  A) вывод в ПАЙП/КОНСОЛЬ: КАЖДЫЙ вход-процесс контура первой командой зовёт io_utf8.force_utf8()
     → его stdout/stderr пишут UTF-8, откуда бы Планировщик/терминал/родитель его ни поднял;
  B) ЧТЕНИЕ чужого вывода: КАЖДЫЙ subprocess.run/Popen/check_output в текстовом режиме
     (text=True/universal_newlines) задаёт encoding= явно → декод UTF-8, а не локалью Windows;
  C) запись в ФАЙЛ: КАЖДЫЙ builtin open() в текст-режиме и КАЖДЫЙ файловый sink logging
     (FileHandler/basicConfig(filename=)) задаёт encoding= явно. force_utf8() эту полосу НЕ
     закрывает — он трогает только stdout/stderr; файл берёт локаль (cp1251) → эмодзи 📊 в
     open(log,'w')/log.info падает 'charmap' ровно как в пайпе (потенциальный ЧЕТВЁРТЫЙ укус).
Новое место (print-скрипт без force_utf8 / text=True без encoding / open()|FileHandler|
basicConfig(filename=) без encoding) валит гейт — класс не вернётся молча.

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
#
# ПЕРЕЧЕНЬ — САМОЕ СЛАБОЕ МЕСТО ЭТОГО СТОРОЖА, И ЭТО ЗАМЕРЕНО, А НЕ ОПАСЕНИЕ (11.09.2026).
# Четыре имени выше набраны 30.07.2026, и всё, что родилось ПОЗЖЕ, для стража не существует:
# 10.09 в 17:37:47Z снятие сигнальной остановки ящика Штаба (`shtab_box_run.py`, рождён 02.09)
# ПРОШЛО и легло на диск, а печать исхода упала 'charmap' на ✅ (U+2705) — владелец получил
# трейсбек вместо слова «снято», ровно тот класс, который страж заведён не пускать. Гейт при
# этом был зелёный: имени в перечне не было. Замер того же дня по корню репозитория — боевых
# командных строк со знаками вне cp1251 в печатаемых литералах **45**, из них под стражем **5**
# (четыре исходных плюс ящик). ОСТАЛЬНЫЕ 40 — ОТКРЫТЫЙ ОСТАТОК, а не «проверено и хорошо».
ENTRYPOINTS = ("pc_agent.py", "pc_orchestrator.py", "dispatch_notify.py", "cowork_log_append.py",
               "shtab_box_run.py")

# Модули контура, ЧИТАЮЩИЕ чужой вывод subprocess'ом → каждый text-режим обязан задать encoding=.
# rc_supervisor.py уже был чист (encoding на месте) — держим его под стражем, чтобы не отъехал;
# lesson_router.py правится этой же задачей (git rev-parse, вывод отбрасывался, но класс тот же).
SUBPROCESS_READERS = ENTRYPOINTS + ("selfupdate_gate.py", "rc_supervisor.py", "lesson_router.py")

# Границы: правка этих файлов запрещена (боты/клиент). Держим их СПИСКОМ явно, чтобы страж не
# притворялся, будто закрыл и их: тот же класс здесь — отдельный остаток с точками касания.
OUT_OF_SCOPE = ("userbot_listen.py", "moderation_bot.py", "suggest.py")

_RUNNERS = {"run", "Popen", "check_output"}

# Полоса C — ПИШУЩИЕ В ФАЙЛ модули контура (open()/logging→файл с не-ASCII: кириллица, эмодзи).
# log_setup.py — центральная фабрика RotatingFileHandler для ОБОИХ логов (pc_agent+pc_orchestrator):
# оброни там encoding — и оба лога разом уедут в cp1251, потому держим её под стражем в первую очередь.
FILE_WRITERS = SUBPROCESS_READERS + ("log_setup.py",)

# Файловые sink'и logging: пишут запись в файл кодировкой ЛОКАЛИ, если не задать encoding= (StreamHandler
# в набор НЕ входит — его stdout/stderr закрывает force_utf8(), полоса A).
_LOG_FILE_SINKS = {"FileHandler", "RotatingFileHandler", "TimedRotatingFileHandler", "WatchedFileHandler"}


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


def _has_kw(node, name):
    return any(k.arg == name for k in node.keywords)


def _open_is_binary(node):
    """mode из 2-го позиционного либо mode=; 'b' в нём → бинарный (encoding не нужен и запрещён).
    mode отсутствует или динамический (не строковая константа) → считаем ТЕКСТОМ (дефолт 'r' текст)."""
    mode = None
    kw = {k.arg: k.value for k in node.keywords if k.arg}
    if "mode" in kw:
        mode = kw["mode"]
    elif len(node.args) >= 2:
        mode = node.args[1]
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value


def open_text_without_encoding(tree):
    """→ [lineno] для builtin open(...) в ТЕКСТОВОМ режиме БЕЗ encoding=.
    Только bare Name('open'): os.open (байтовый fd, encoding не принимает) и .open(...) объектов
    (opener.open, io.open) — не он; encoding как 4-й позиционный (open(f,mode,buf,enc)) засчитываем."""
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        if _open_is_binary(node):
            continue
        if not (_has_kw(node, "encoding") or len(node.args) >= 4):
            bad.append(node.lineno)
    return sorted(bad)


def logging_file_sink_without_encoding(tree):
    """→ [lineno] для FileHandler-семейства и basicConfig(filename=...) БЕЗ encoding=.
    basicConfig без filename= (handlers=/stream=) — не прямой файловый sink, не трогаем."""
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node.func)
        if name in _LOG_FILE_SINKS:
            if not _has_kw(node, "encoding"):
                bad.append(node.lineno)
        elif name == "basicConfig" and _has_kw(node, "filename") and not _has_kw(node, "encoding"):
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


class TestFileWritesUtf8(unittest.TestCase):
    """Полоса ЗАПИСИ В ФАЙЛ: builtin open() и файловый sink logging задают encoding='utf-8'.
    force_utf8() (полоса A) файл НЕ трогает — только stdout/stderr; open(log,'w') без encoding
    берёт cp1251 и падает 'charmap' на 📊 ровно как print в пайп. Это ЧЕТВЁРТЫЙ канал того же
    класса — держим его под стражем ДО первого укуса, а не после."""

    def test_open_text_mode_has_encoding(self):
        offenders = {}
        for n in FILE_WRITERS:
            bad = open_text_without_encoding(_parse(n))
            if bad:
                offenders[n] = bad
        self.assertEqual(
            offenders, {},
            "builtin open() в текст-режиме без encoding= пишет/читает файл локалью Windows (cp1251) "
            "→ мохибейк, а на эмодзи — 'charmap'. Добавь encoding='utf-8'. Строки: %s" % offenders)

    def test_logging_file_sink_has_encoding(self):
        offenders = {}
        for n in FILE_WRITERS:
            bad = logging_file_sink_without_encoding(_parse(n))
            if bad:
                offenders[n] = bad
        self.assertEqual(
            offenders, {},
            "FileHandler/basicConfig(filename=) без encoding= пишет лог кодировкой локали → эмодзи "
            "в log.info падает 'charmap'. Добавь encoding='utf-8'. Строки: %s" % offenders)


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
