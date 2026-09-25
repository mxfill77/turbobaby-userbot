# -*- coding: utf-8 -*-
"""
test_profile_choice.py — замки выбора учётки строителей полосы ПК (`profile_choice.py`).

ГЛАВНЫЙ ЗАМОК, из-за которого модуль и написан: `env[CLAUDE_CONFIG_DIR] = ""` не рождается НИ НА
ОДНОЙ дороге. Пустая переменная — не «никакая», а состояние M2 замера 70u (профиль БЕЗ входа,
три маркера отказа), и от отсутствия ключа (M1, чистый основной профиль) оно отличается всем.
Проверяется не одним кейсом, а прогоном КОРПУСА входов через живую функцию.

Второй замок — ТРЕТИЙ ИСХОД: «не понял файл» обязан не трогать окружение ВОВСЕ. Сверяется
сравнением словарей целиком, а не наличием одного ключа: правка, которая «заодно» тронет соседний
ключ, здесь покраснеет.
"""

import ast
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import profile_choice as pc            # noqa: E402

REPO = os.path.dirname(os.path.abspath(__file__))
KEY = pc.KEY

# Живое окружение-образец: ключ УЖЕ стоит (так его видит демон сегодня) плюс соседи, по которым
# проверяется «не тронуто ВОВСЕ».
def parent_env():
    return {KEY: r"D:\claude_profile_2", "PATH": "C:\\bin", "PYTHONIOENCODING": "utf-8"}


def _read(name):
    with io.open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def reader_of(text, err=""):
    """Инъекция чтения: подменяем диск, а не переписываем ветки модуля."""
    return lambda repo=None, path=None: (text, err)


def isdir_of(*known):
    known = {str(k) for k in known}
    return lambda p: str(p) in known


class TestMainWord(unittest.TestCase):
    """Слово ОСНОВНОЙ → ключ УДАЛЯЕТСЯ (состояние M1 замера 70u)."""

    def test_word_drops_the_key(self):
        env = parent_env()
        d = pc.apply_to(env, reader=reader_of("ОСНОВНОЙ\n"))
        self.assertEqual(d.action, pc.ACT_DROP)
        self.assertNotIn(KEY, env)

    def test_word_is_case_and_space_tolerant(self):
        for raw in ("основной", "  ОсНоВнОй  ", "ОСНОВНОЙ", "ОСНОВНОЙ\r\n"):
            env = parent_env()
            d = pc.apply_to(env, reader=reader_of(raw))
            self.assertEqual(d.action, pc.ACT_DROP, raw)
            self.assertNotIn(KEY, env, raw)

    def test_word_drops_even_when_parent_has_no_key(self):
        env = {"PATH": "C:\\bin"}
        d = pc.apply_to(env, reader=reader_of("ОСНОВНОЙ"))
        self.assertEqual(d.action, pc.ACT_DROP)
        self.assertEqual(env, {"PATH": "C:\\bin"})       # pop отсутствующего ничего не ломает

    def test_comments_and_blank_lines_are_skipped(self):
        """Строка отката человеческим языком обязана уживаться со значением в одном файле."""
        raw = ("# Выбор учётки строителей полосы ПК.\n"
               "# ОТКАТ: замените слово ОСНОВНОЙ ниже на путь D:\\claude_profile_2.\n"
               "\n"
               "ОСНОВНОЙ\n")
        env = parent_env()
        d = pc.apply_to(env, reader=reader_of(raw))
        self.assertEqual(d.action, pc.ACT_DROP)
        self.assertNotIn(KEY, env)


class TestPathValue(unittest.TestCase):
    """Путь существующего каталога → ключ равен ему (состояние B)."""

    def test_existing_dir_is_set(self):
        env = parent_env()
        d = pc.apply_to(env, reader=reader_of("D:\\claude_profile_7\n"),
                        isdir=isdir_of("D:\\claude_profile_7"))
        self.assertEqual(d.action, pc.ACT_SET)
        self.assertEqual(env[KEY], "D:\\claude_profile_7")

    def test_quotes_around_path_are_stripped(self):
        env = parent_env()
        d = pc.apply_to(env, reader=reader_of('"D:\\claude_profile_7"'),
                        isdir=isdir_of("D:\\claude_profile_7"))
        self.assertEqual((d.action, env[KEY]), (pc.ACT_SET, "D:\\claude_profile_7"))

    def test_missing_dir_keeps_env(self):
        env = parent_env()
        before = dict(env)
        d = pc.apply_to(env, reader=reader_of("D:\\net_takogo_kataloga"), isdir=isdir_of())
        self.assertEqual(d.action, pc.ACT_KEEP)
        self.assertEqual(env, before)

    def test_isdir_raising_keeps_env(self):
        """«Не проверить» ≠ «существует»: третий исход не превращается в разрешение."""
        def boom(_p):
            raise OSError("WinError 123")
        env = parent_env()
        before = dict(env)
        d = pc.apply_to(env, reader=reader_of("D:\\x"), isdir=boom)
        self.assertEqual(d.action, pc.ACT_KEEP)
        self.assertEqual(env, before)


class TestThirdOutcomeTouchesNothing(unittest.TestCase):
    """Файла нет · не прочитан · пусто · мусор → окружение НЕ ТРОГАЕТСЯ ВОВСЕ."""

    BAD = (
        ("файла нет", None, ""),
        ("файл не прочитан", None, "UnicodeDecodeError: invalid start byte"),
        ("пусто", "", ""),
        ("одни пробелы", "   \n\t\n", ""),
        ("одни комментарии", "# только комментарий\n# и ещё один\n", ""),
        ("две значимые строки", "ОСНОВНОЙ\nD:\\claude_profile_2\n", ""),
        ("управляющий символ", "D:\\profile\x00dir", ""),
        ("неправдоподобно длинное", "D:\\" + "x" * (pc.VALUE_MAX + 1), ""),
        ("пустые кавычки", '""', ""),
    )

    def test_env_is_byte_equal_to_parent(self):
        for name, raw, err in self.BAD:
            with self.subTest(name):
                env = parent_env()
                before = dict(env)
                d = pc.apply_to(env, reader=reader_of(raw, err), isdir=isdir_of())
                self.assertEqual(d.action, pc.ACT_KEEP, name)
                self.assertEqual(env, before, name)      # НИ ОДНОГО ключа, не только KEY

    def test_every_refusal_names_its_reason(self):
        """Причина обязана быть непустой: третий исход, молчащий в журнал, неотличим от успеха."""
        for name, raw, err in self.BAD:
            with self.subTest(name):
                d = pc.decide(raw, err, isdir=isdir_of())
                self.assertTrue(d.reason.strip(), name)
                self.assertIn("НЕ ПРИМЕНЁН", pc.line(d))


class TestNeverEmpty(unittest.TestCase):
    """PROFILE_CHOICE_NEVER_EMPTY — инвариант-корпус, а не один кейс."""

    CORPUS = (None, "", " ", "\n", "\t\n \n", '""', "''", "#коммент", "# a\n# b",
              "\x00", "\x00\x00", "ОСНОВНОЙ", "основной", "  ", "D:\\net_takogo",
              "ОСНОВНОЙ\nОСНОВНОЙ", "x" * (pc.VALUE_MAX + 5), '" "', "'  '")

    def test_no_input_ever_sets_an_empty_key(self):
        empties = 0
        for raw in self.CORPUS:
            for parent in (parent_env(), {"PATH": "C:\\bin"}):
                env = dict(parent)
                pc.apply_to(env, reader=reader_of(raw), isdir=isdir_of())
                if env.get(KEY, None) == "":
                    empties += 1
        self.assertEqual(empties, 0, "пустая переменная попала в окружение %d раз" % empties)

    def test_decide_never_returns_set_with_empty_value(self):
        for raw in self.CORPUS:
            d = pc.decide(raw, "", isdir=isdir_of())
            if d.action == pc.ACT_SET:
                self.assertTrue(d.value, raw)

    def test_source_has_no_empty_assignment_at_all(self):
        """Ветки, ставящей пустое, нет ФИЗИЧЕСКИ: ни одного `Decision(ACT_SET, …)` с пустым
        литералом и ни одного присваивания `env[KEY] = ''` в исходнике."""
        src = _read("profile_choice.py")
        tree = ast.parse(src)
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Decision":
                if node.args and getattr(node.args[0], "value", None) == pc.ACT_SET:
                    v = node.args[1] if len(node.args) > 1 else None
                    if isinstance(v, ast.Constant) and v.value == "":
                        bad.append(ast.dump(node))
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if (isinstance(t, ast.Subscript)
                            and isinstance(node.value, ast.Constant)
                            and node.value.value == ""):
                        bad.append(ast.dump(node))
        self.assertEqual(bad, [], "в модуле есть дорога к пустому значению: %s" % bad)


class TestPurity(unittest.TestCase):
    """PROFILE_CHOICE_PURE: io/os/collections — и всё. Ни сети, ни подпроцессов, ни БД."""

    FORBIDDEN = {"subprocess", "socket", "sqlite3", "urllib", "requests", "winreg",
                 "ctypes", "http", "telethon", "shutil"}

    def test_imports_are_narrow(self):
        src = _read("profile_choice.py")
        names = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names, {"io", "os", "collections"}, "импорты модуля: %s" % sorted(names))
        self.assertEqual(names & self.FORBIDDEN, set())

    def test_module_never_writes_or_deletes(self):
        src = _read("profile_choice.py")
        for bad in ("os.remove", "os.unlink", "shutil.rmtree", 'io.open(p, "w"', "winreg"):
            self.assertNotIn(bad, src, bad)


class TestBothSpawnBranchesAsk(unittest.TestCase):
    """ОБЕ ветки спавна claude спрашивают файл — иначе зеркальная течь: исполнитель уедет на
    основной профиль, думатель останется на застрявшем в окружении демона, и МОЛЧА."""

    BRANCHES = ("_run_task_impl", "_thinker_exec")

    def setUp(self):
        src = _read("pc_orchestrator.py")
        self.tree = ast.parse(src)

    def _fn(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        self.fail("в pc_orchestrator.py нет функции %s" % name)

    # ★ 25.09.2026: решение переехало в реестр учёток вне git (`accounts.apply_builders`), а
    # `profile_choice` стал его словарём. Инвариант тот же — ОБЕ ветки спрашивают ОДНУ дверь.
    def test_each_branch_calls_apply_builders(self):
        for name in self.BRANCHES:
            with self.subTest(name):
                calls = [n for n in ast.walk(self._fn(name))
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute)
                         and n.func.attr == "apply_builders"
                         and getattr(n.func.value, "id", "") == "accounts"]
                self.assertEqual(len(calls), 1,
                                 "%s зовёт accounts.apply_builders %d раз" % (name, len(calls)))

    def test_no_branch_reads_the_tree_file_anymore(self):
        """Файл дерева в git больше не решает: его правка пачкала бы дерево (авто-фетч встаёт)."""
        for name in self.BRANCHES:
            with self.subTest(name):
                calls = [n for n in ast.walk(self._fn(name))
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                         and getattr(n.func.value, "id", "") == "profile_choice"]
                self.assertEqual(calls, [])

    def test_daemon_imports_module_on_top(self):
        tops = {a.name for n in self.tree.body if isinstance(n, ast.Import) for a in n.names}
        self.assertIn("accounts_registry", tops)


class TestBomTolerated(unittest.TestCase):
    """Файл, сохранённый PowerShell 5.1 (`Set-Content -Encoding UTF8`), начинается с BOM. До 22.09
    BOM прилипал к первой `#`-строке, она переставала быть комментарием, и решение молча уходило
    в ACT_KEEP («значимых строк 2»). Читаем с диска живой функцией, а не инъекцией."""

    TEXT = "# строка отката\n# ещё комментарий\nОСНОВНОЙ\n"

    def _decide_file(self, data):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, pc.CHOICE_REL)
            with open(p, "wb") as f:
                f.write(data)
            raw, err = pc.read_raw(path=p)
        return pc.decide(raw, err)

    def test_bom_file_still_decides_main(self):
        d = self._decide_file(b"\xef\xbb\xbf" + self.TEXT.encode("utf-8"))
        self.assertEqual(d.action, pc.ACT_DROP, d.reason)

    def test_plain_utf8_unchanged(self):
        d = self._decide_file(self.TEXT.encode("utf-8"))
        self.assertEqual(d.action, pc.ACT_DROP, d.reason)

    def test_bom_crlf_file_still_decides_main(self):
        d = self._decide_file(b"\xef\xbb\xbf" + self.TEXT.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(d.action, pc.ACT_DROP, d.reason)


class TestLiveChoiceFile(unittest.TestCase):
    """Живой файл полосы читается ТЕМ ЖЕ кодом (правило «мок обязан копировать живой формат»).

    Здесь СОЗНАТЕЛЬНО не проверяется, какое именно решение в файле лежит: этот тест ходит в гейт
    self-update, и красный тест из-за правки текстового файла заморозил бы доставку кода ВСЕМУ
    демону. Проверяется то, что от файла требуется всегда: он разбирается без исключения, а
    третий исход называет причину."""

    def test_live_file_parses_and_explains_itself(self):
        raw, err = pc.read_raw(REPO)
        d = pc.decide(raw, err)
        self.assertIn(d.action, (pc.ACT_DROP, pc.ACT_SET, pc.ACT_KEEP))
        self.assertTrue(d.reason.strip())
        if d.action == pc.ACT_KEEP:
            self.assertIn("не тронуто", pc.line(d))

    def test_choice_path_points_into_the_working_tree(self):
        self.assertEqual(pc.choice_path(REPO), os.path.join(REPO, pc.CHOICE_REL))


if __name__ == "__main__":
    unittest.main(verbosity=2)
