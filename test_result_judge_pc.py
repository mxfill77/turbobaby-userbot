# -*- coding: utf-8 -*-
"""
test_result_judge_pc.py — регресс СУДЬИ АДРЕСА РЕЗУЛЬТАТА + четыре замка захода.

Порядок классов в файле — порядок замков, и он не косметический:

  A. `TestNegativeFirst` — ОТРИЦАТЕЛЬНЫЙ ТЕСТ, ПЕРВЫМ. «Адрес назван, по адресу пусто» обязан
     дать НЕ ДОКАЗАН, «источник недоступен» — НЕИЗВЕСТНО, и НИ ОДИН из этих случаев не смеет
     позеленеть. Прибор, который зеленит пустоту, дальше можно не проверять.
  B. `TestDiscriminatingPower` — ПОРЧА ОДНОГО ЗНАКА указателя переворачивает вердикт. Иначе
     прибор ничего не различает, а раскладка по истории — просто перепись адресов.
  C. `TestWindowInsensitivity` — сдвиг окна на два часа не меняет НИ ОДНОГО вердикта, ХОТЯ
     `in_window` при том же сдвиге переворачивается и наивный приём меняет ответ. Ручка живая,
     а вердикт её не видит.
  D. `TestWindowsPathPointer` — виндовый путь с ДВОЕТОЧИЕМ ДИСКА, обязательный случай набора
     (ради него канон 05e54aa сменил разделитель на пробел). Проверяется и у вида `file`, и у
     вида `row`, где двоеточие диска соседствует с разметкой `<база>:<таблица>:<условие>`.

Плюс три инварианта, которые держат ГРАНИЦУ ЗАХОДА устройством, а не обещанием:
  • `RESULT_JUDGE_UNWIRED` — вызовов судьи из боевого кода НОЛЬ (ast-обход всех боевых `*.py`);
  • `JULIANDAY_ONLY` — в модуле НИ ОДНОГО вычитания на Python, ни `datetime`, ни `time`;
  • `READ_ONLY` — все SQL-литералы модуля начинаются со `SELECT`, git — три читающие подкоманды.

Запуск — тем же способом, что и весь гейт репозитория:
    venv\\Scripts\\python.exe -m unittest test_result_judge_pc
"""
import ast
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

import result_judge_pc as rj
import result_ref as rr

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "result_judge_pc.py")

# Живой виндовый путь этой полосы — тот же, что в наборе поля (`test_result_ref.WIN_PATH`).
WIN_SELF = os.path.join(REPO, "result_judge_pc.py")

# Хеш, которого в дереве нет и быть не может: 40 знаков, все нули с хвостом.
ABSENT_HASH = "0000000000000000000000000000000000000123"


def _origin_main():
    """Хеш `origin/main` живого дерева | None. Нужен, чтобы ЖИВОЙ ДОКАЗАН брался не литералом
    из вчерашнего дня, а тем самым коммитом, относительно которого вид `commit` и определён."""
    try:
        done = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "origin/main^{commit}"],
                              cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


ORIGIN_MAIN = _origin_main()
HAS_GIT = ORIGIN_MAIN is not None


def _iso(unix_ts, hours=0):
    """unix → метка вида `YYYY-MM-DD HH:MM:SS` (UTC), при желании со сдвигом на часы.

    Считается ТЕМ ЖЕ инструментом, что и разности (`julianday`-семейство SQLite), — набор
    сознательно не заводит второй системы времени, чтобы замок метода нельзя было обойти
    в проверке того же замка."""
    con = sqlite3.connect(":memory:")
    try:
        row = con.execute("SELECT datetime(?, 'unixepoch', ?)",
                          (unix_ts, "%+d hours" % hours)).fetchone()
    finally:
        con.close()
    return row[0]


def naive_freshness_verdict(reading):
    """ПРИЁМ, КОТОРЫМ СУДЬЯ НЕ СУДИТ: «свежее окна — значит сделано». Живёт в наборе, а не в
    модуле, и нужен ровно затем, чтобы замок C был проверяем: при том же сдвиге окна он МЕНЯЕТ
    ответ. Без него «нечувствительность» доказывала бы лишь, что ручка никуда не подключена."""
    fresh = reading.get(rj.IN_WINDOW)
    if fresh is None:
        return rj.UNKNOWN
    if fresh:
        return rj.PROVEN
    return rj.DISPROVEN


class _Fixture(unittest.TestCase):
    """Общая утварь: свой корень (чтобы замок «только внутри полосы» не мешал фикстурам) и
    подставные руки. НИ ОДИН тест набора не трогает боевую `moderation_ipc.db`."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="judge_pc_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _file(self, name, text="тело"):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def _db(self, name="fixture.db", rows=(1278,)):
        path = os.path.join(self.root, name)
        con = sqlite3.connect(path)
        try:
            con.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, chat TEXT)")
            for one in rows:
                con.execute("INSERT INTO drafts (id, chat) VALUES (?, ?)", (one, "клиент"))
            con.commit()
        finally:
            con.close()
        return path

    def _ctx(self, **over):
        over.setdefault("repo", self.root)
        return rj.Ctx(**over)

    def _git_says(self, table):
        """Подставной git: словарь «первый аргумент+хвост → (код, stdout)»; None = не запустился."""
        def run(args):
            return table.get(tuple(args))
        return run


# ═════════════════════════ A. ОТРИЦАТЕЛЬНЫЙ ТЕСТ — ПЕРВЫМ ════════════════════════════════════

class TestNegativeFirst(_Fixture):
    """ГЛАВНЫЙ замок захода. Каждый случай назван вслух и собран в две таблицы: «по адресу
    пусто» (обязан быть НЕ ДОКАЗАН) и «источник недоступен» (обязан быть НЕИЗВЕСТНО).
    Позеленевший случай роняет набор с числом, а не с абстрактным «не равно»."""

    def _empty_cases(self):
        """Адрес НАЗВАН, по адресу ПУСТО → ждём НЕ ДОКАЗАН. Пять видов, восемь случаев."""
        missing_db = self._db()
        empty_file = self._file("пусто.md", "")
        self._file("есть.md", "тело")
        return [
            ("commit · хеша нет в дереве",
             "commit " + ABSENT_HASH,
             self._ctx(run_git=self._git_says({
                 ("rev-parse", "--verify", "--quiet", ABSENT_HASH + "^{commit}"): (1, ""),
             }))),
            ("commit · есть локально, в origin/main нет",
             "commit abcdef1",
             self._ctx(run_git=self._git_says({
                 ("rev-parse", "--verify", "--quiet", "abcdef1^{commit}"): (0, "abcdef1" * 5),
                 ("rev-parse", "--verify", "--quiet", "origin/main^{commit}"): (0, "b" * 40),
                 ("merge-base", "--is-ancestor", "abcdef1" * 5, "origin/main"): (1, ""),
                 ("show", "-s", "--format=%ct", "abcdef1" * 5): (0, "1786789580"),
             }))),
            ("file · файла нет",
             "file нет-такого-файла.md", self._ctx()),
            ("file · файл есть, но ПУСТ",
             "file " + os.path.basename(empty_file), self._ctx()),
            ("row · строки по условию нет",
             "row " + os.path.basename(missing_db) + ":drafts:9999", self._ctx()),
            ("brain · названного в узле нет",
             "brain queue_state_pc §полоса ПК",
             self._ctx(read_doc=lambda _doc: "узел живой, но про полосу тут ни слова",
                       brain_baseline=1)),
            ("service_start · процесса нет",
             "service_start pc_orchestrator PID 424242 старше HEAD abcdef1",
             self._ctx(proc_start=lambda _pid: (False, None),
                       run_git=self._git_says({
                           ("rev-parse", "--verify", "--quiet", "abcdef1^{commit}"):
                               (0, "abcdef1" * 5),
                           ("show", "-s", "--format=%ct", "abcdef1" * 5): (0, "1786789580"),
                       }))),
            ("service_start · сервис стартовал ДО коммита",
             "service_start pc_orchestrator PID 4242 старше HEAD abcdef1",
             self._ctx(proc_start=lambda _pid: (True, (rj.T_UNIX, 1786000000)),
                       run_git=self._git_says({
                           ("rev-parse", "--verify", "--quiet", "abcdef1^{commit}"):
                               (0, "abcdef1" * 5),
                           ("show", "-s", "--format=%ct", "abcdef1" * 5): (0, "1786789580"),
                       }))),
        ]

    def _unreadable_cases(self):
        """ИСТОЧНИК НЕДОСТУПЕН → ждём НЕИЗВЕСТНО. Это НЕ «в порядке» и НЕ «не доказан»."""
        return [
            ("commit · git не запустился",
             "commit abcdef1", self._ctx(run_git=lambda _args: None)),
            ("commit · ссылки origin/main нет",
             "commit abcdef1",
             self._ctx(run_git=self._git_says({
                 ("rev-parse", "--verify", "--quiet", "abcdef1^{commit}"): (0, "abcdef1" * 5),
                 ("rev-parse", "--verify", "--quiet", "origin/main^{commit}"): (128, ""),
             }))),
            ("commit · указатель не несёт хеша",
             "commit смотри отчёт", self._ctx()),
            ("file · путь ведёт за пределы полосы",
             "file C:\\Windows\\win.ini", self._ctx()),
            ("row · базы нет вовсе",
             "row нет-базы.db:drafts:1", self._ctx()),
            ("row · условие не разобрано",
             "row fixture.db:drafts:где-то там", self._ctx()),
            ("row · таблица не опознана",
             "row fixture.db:drafts;DROP:1", self._ctx()),
            ("brain · читателя не дали",
             "brain queue_state_pc §полоса ПК", self._ctx()),
            ("brain · читатель сорвался",
             "brain queue_state_pc §полоса ПК",
             self._ctx(read_doc=_raiser, brain_baseline=1)),
            ("brain · нашлось, но роста не с чем сравнить",
             "brain queue_state_pc §полоса ПК",
             self._ctx(read_doc=lambda _doc: "полоса ПК: сдано #553")),
            ("service_start · про PID ответа нет",
             "service_start демон PID 4242 старше HEAD abcdef1",
             self._ctx(proc_start=lambda _pid: (None, None),
                       run_git=self._git_says({
                           ("rev-parse", "--verify", "--quiet", "abcdef1^{commit}"):
                               (0, "abcdef1" * 5),
                           ("show", "-s", "--format=%ct", "abcdef1" * 5): (0, "1786789580"),
                       }))),
            ("service_start · PID есть, возраст не добыть",
             "service_start демон PID 4242 старше HEAD abcdef1",
             self._ctx(proc_start=lambda _pid: (True, None),
                       run_git=self._git_says({
                           ("rev-parse", "--verify", "--quiet", "abcdef1^{commit}"):
                               (0, "abcdef1" * 5),
                           ("show", "-s", "--format=%ct", "abcdef1" * 5): (0, "1786789580"),
                       }))),
            ("адрес не назван вовсе", "", self._ctx()),
            ("адрес снятой формы (двоеточие) — не адрес",
             "commit:abcdef1", self._ctx()),
        ]

    def test_empty_address_is_disproven_never_green(self):
        wrong = []
        green = 0
        for name, ref, ctx in self._empty_cases():
            got = rj.judge(ref, ctx)
            if got["verdict"] != rj.DISPROVEN:
                wrong.append("%s → %s (%s)" % (name, got["verdict"], got["detail"]))
            if got["verdict"] == rj.PROVEN:
                green += 1
        self.assertEqual(green, 0, "ПОЗЕЛЕНЕЛО случаев пустоты: %d — судья не годен" % green)
        self.assertEqual(wrong, [], "не НЕ ДОКАЗАН: %s" % "; ".join(wrong))

    def test_unreadable_source_is_unknown_never_green_and_never_red(self):
        wrong = []
        green = 0
        for name, ref, ctx in self._unreadable_cases():
            got = rj.judge(ref, ctx)
            if got["verdict"] != rj.UNKNOWN:
                wrong.append("%s → %s (%s)" % (name, got["verdict"], got["detail"]))
            if got["verdict"] == rj.PROVEN:
                green += 1
        self.assertEqual(green, 0, "ПОЗЕЛЕНЕЛО недоступных источников: %d" % green)
        self.assertEqual(wrong, [], "не НЕИЗВЕСТНО: %s" % "; ".join(wrong))

    def test_unknown_is_not_a_pass_it_is_the_third_outcome(self):
        """«Прочитать не удалось» не смеет молча совпасть с «в порядке»: слова разные, и
        сильнее НЕИЗВЕСТНО только НЕ ДОКАЗАН."""
        self.assertNotEqual(rj.UNKNOWN, rj.PROVEN)
        self.assertEqual(rj.combine((rj.UNKNOWN, rj.PROVEN)), rj.UNKNOWN)
        self.assertEqual(rj.combine((rj.UNKNOWN, rj.DISPROVEN)), rj.DISPROVEN)

    def test_every_case_of_the_negative_set_is_actually_named(self):
        """Набор обязан быть ПЕРЕЧИСЛИМЫМ: числа замка A уходят в артефакт и журнал."""
        self.assertEqual(len(self._empty_cases()), 8)
        self.assertEqual(len(self._unreadable_cases()), 14)


def _raiser(_doc):
    raise RuntimeError("мост не ответил")


# ═════════════════════════ B. РАЗЛИЧАЮЩАЯ СИЛА ═══════════════════════════════════════════════

class TestDiscriminatingPower(_Fixture):
    """Порча ОДНОГО ЗНАКА указателя обязана перевернуть вердикт. Прибор, у которого доказанное
    остаётся доказанным при испорченном адресе, не читает назад, а поддакивает."""

    def test_one_char_in_a_file_pointer_flips_proven_to_disproven(self):
        self._file("есть.md", "тело")
        good = rj.judge("file есть.md", self._ctx())
        bad = rj.judge("file ест.md", self._ctx())
        self.assertEqual(good["verdict"], rj.PROVEN, good["detail"])
        self.assertEqual(bad["verdict"], rj.DISPROVEN, bad["detail"])

    def test_one_char_in_a_row_condition_flips_proven_to_disproven(self):
        self._db()
        good = rj.judge("row fixture.db:drafts:1278", self._ctx())
        bad = rj.judge("row fixture.db:drafts:1279", self._ctx())
        self.assertEqual(good["verdict"], rj.PROVEN, good["detail"])
        self.assertEqual(bad["verdict"], rj.DISPROVEN, bad["detail"])

    def test_one_char_in_a_brain_needle_flips_proven_to_disproven(self):
        ctx = self._ctx(read_doc=lambda _doc: "полоса ПК: сдано #553", brain_baseline=1)
        good = rj.judge("brain queue_state_pc §сдано #553", ctx)
        bad = rj.judge("brain queue_state_pc §сдано #554", ctx)
        self.assertEqual(good["verdict"], rj.PROVEN, good["detail"])
        self.assertEqual(bad["verdict"], rj.DISPROVEN, bad["detail"])

    @unittest.skipUnless(HAS_GIT, "origin/main недоступен — живой git не проверить")
    def test_one_char_in_a_live_commit_hash_flips_proven_to_disproven(self):
        """ЖИВОЙ случай: сам `origin/main` доказан, он же с испорченным знаком — нет."""
        ctx = rj.Ctx(repo=REPO)
        good = rj.judge("commit " + ORIGIN_MAIN, ctx)
        self.assertEqual(good["verdict"], rj.PROVEN, good["detail"])
        spoiled = ORIGIN_MAIN[:-1] + ("0" if ORIGIN_MAIN[-1] != "0" else "1")
        bad = rj.judge("commit " + spoiled, ctx)
        self.assertEqual(bad["verdict"], rj.DISPROVEN, bad["detail"])

    @unittest.skipUnless(HAS_GIT, "origin/main недоступен — живой git не проверить")
    def test_live_file_of_this_lane_is_proven_and_its_neighbour_is_not(self):
        ctx = rj.Ctx(repo=REPO)
        self.assertEqual(rj.judge("file result_judge_pc.py", ctx)["verdict"], rj.PROVEN)
        self.assertEqual(rj.judge("file result_judge_pd.py", ctx)["verdict"], rj.DISPROVEN)

    def test_three_outcomes_all_reachable_on_one_kind(self):
        """Различающая сила — это три РАЗНЫХ ответа на одном виде, а не два."""
        self._file("есть.md", "тело")
        seen = {
            rj.judge("file есть.md", self._ctx())["verdict"],
            rj.judge("file нет.md", self._ctx())["verdict"],
            rj.judge("file C:\\Windows\\win.ini", self._ctx())["verdict"],
        }
        self.assertEqual(seen, {rj.PROVEN, rj.DISPROVEN, rj.UNKNOWN})


# ═════════════════════════ C. НЕЧУВСТВИТЕЛЬНОСТЬ К ОКНУ ══════════════════════════════════════

class TestWindowInsensitivity(_Fixture):
    """Сдвиг окна на ДВА ЧАСА не меняет ни одного вердикта — и этого мало: тем же сдвигом
    обязан ломаться наивный приём, иначе доказано лишь, что ручка никуда не ведёт."""

    def _live_set(self):
        path = self._file("есть.md", "тело")
        self._db()
        stamp = os.path.getmtime(path)
        window = (_iso(stamp, -1), _iso(stamp, +1))
        refs = ["file есть.md", "file нет.md", "row fixture.db:drafts:1278",
                "row fixture.db:drafts:9999", "file C:\\Windows\\win.ini", ""]
        return refs, window

    def test_two_hour_shift_changes_no_verdict(self):
        refs, window = self._live_set()
        base = [rj.judge(r, self._ctx(window=window))["verdict"] for r in refs]
        moved = []
        for hours in (2, -2):
            shifted = rj.shift_window(window, hours)
            moved.append([rj.judge(r, self._ctx(window=shifted))["verdict"] for r in refs])
        self.assertEqual(base, moved[0], "сдвиг +2ч поменял вердикты — судья судит по времени")
        self.assertEqual(base, moved[1], "сдвиг -2ч поменял вердикты — судья судит по времени")

    def test_the_same_shift_does_move_in_window_and_the_naive_trick(self):
        """Ручка ЖИВАЯ: то же окно, тот же сдвиг — `in_window` переворачивается, а наивный
        приём «свежее окна = сделано» меняет ответ. Значит нечувствительность не пуста."""
        refs, window = self._live_set()
        here = [rj.read_back(r, self._ctx(window=window)) for r in refs]
        there = [rj.read_back(r, self._ctx(window=rj.shift_window(window, 2))) for r in refs]
        flipped = [i for i in range(len(refs))
                   if here[i][rj.IN_WINDOW] != there[i][rj.IN_WINDOW]]
        self.assertTrue(flipped, "окно никуда не доехало — замок C ничего не доказывает")
        naive_here = [naive_freshness_verdict(one) for one in here]
        naive_there = [naive_freshness_verdict(one) for one in there]
        self.assertNotEqual(naive_here, naive_there,
                            "наивный приём не сдвинулся — сравнивать судью не с чем")

    def test_verdict_of_never_reads_in_window(self):
        """Инвариант, а не соглашение: имя поля не встречается в теле функции вердикта."""
        with open(SRC, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        body = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "verdict_of"]
        self.assertEqual(len(body), 1)
        names = set()
        for node in ast.walk(body[0]):
            if isinstance(node, ast.Name):
                names.add(node.id)
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
        for banned in ("in_window", "IN_WINDOW", "window", "AT", "at"):
            self.assertNotIn(banned, names, "вердикт заглянул в окно: %s" % banned)

    def test_window_is_carried_but_optional(self):
        self._file("есть.md", "тело")
        without = rj.judge("file есть.md", self._ctx())
        self.assertEqual(without["verdict"], rj.PROVEN)
        self.assertIsNone(without["reading"][rj.IN_WINDOW], "без окна свежести быть не может")

    def test_shift_window_is_sql_and_survives_nonsense(self):
        self.assertIsNone(rj.shift_window(None, 2))
        moved = rj.shift_window(("2026-08-16 10:00:00", "2026-08-16 12:00:00"), 2)
        self.assertEqual(moved, ("2026-08-16 12:00:00", "2026-08-16 14:00:00"))
        self.assertEqual(rj.shift_window(("мусор", "мусор"), 2), (None, None))


# ═════════════════════════ D. ВИНДОВЫЙ ПУТЬ ══════════════════════════════════════════════════

class TestWindowsPathPointer(_Fixture):
    """Указатель с ДВОЕТОЧИЕМ ДИСКА — обязательный случай. У вида `row` он опаснее вдвое: там
    двоеточие диска стои́т рядом с разметкой `<база>:<таблица>:<условие>`, и делить приходится
    СПРАВА. Красный тест здесь = вернулось деление слева, то есть класс 05e54aa."""

    def test_absolute_windows_path_of_this_lane_is_read(self):
        got = rj.judge("file " + WIN_SELF, rj.Ctx(repo=REPO))
        self.assertEqual(got["verdict"], rj.PROVEN, got["detail"])
        self.assertEqual(got["pointer"], WIN_SELF)
        self.assertIn(":", got["pointer"], "двоеточие диска потерялось")

    def test_windows_path_with_spaces_and_absent_file_is_disproven(self):
        deep = os.path.join(self.root, "с пробелом")
        os.makedirs(deep)
        got = rj.judge("file " + os.path.join(deep, "нет.md"), self._ctx())
        self.assertEqual(got["verdict"], rj.DISPROVEN, got["detail"])

    def test_row_pointer_with_a_drive_colon_splits_from_the_right(self):
        path = self._db()
        self.assertIn(":", path, "у временного корня нет буквы диска — случай не тот")
        got = rj.judge("row " + path + ":drafts:1278", self._ctx())
        self.assertEqual(got["verdict"], rj.PROVEN, got["detail"])
        gone = rj.judge("row " + path + ":drafts:9999", self._ctx())
        self.assertEqual(gone["verdict"], rj.DISPROVEN, gone["detail"])

    def test_row_pointer_with_a_named_column_condition(self):
        path = self._db()
        got = rj.judge("row " + path + ":drafts:chat='клиент'", self._ctx())
        self.assertEqual(got["verdict"], rj.PROVEN, got["detail"])
        gone = rj.judge("row " + path + ":drafts:chat='никто'", self._ctx())
        self.assertEqual(gone["verdict"], rj.DISPROVEN, gone["detail"])

    def test_the_canon_marker_of_a_live_step_reaches_the_judge(self):
        """Сквозной случай: маркер в тексте шага → значение → вердикт, без ручной склейки."""
        row = {"id": 431, "status": "done", "lane": "pc",
               "task_text": "[шаг 2/5 родитель 430] " + rr.prefix(rr.make("file", WIN_SELF))
                            + "построить судью"}
        got = rj.judge_item(row, rj.Ctx(repo=REPO))
        self.assertEqual(got["verdict"], rj.PROVEN, got["detail"])
        self.assertEqual(got["kind"], "file")

    def test_a_row_without_an_address_is_unknown_not_a_failure(self):
        row = {"id": 431, "task_text": "[шаг 2/5 родитель 430] обычный шаг"}
        got = rj.judge_item(row, rj.Ctx(repo=REPO))
        self.assertEqual(got["verdict"], rj.UNKNOWN)
        self.assertEqual(got["detail"], "адрес не назван")


# ═════════════════════════ ПОРЯДОК СИЛЫ ══════════════════════════════════════════════════════

class TestStrengthOrder(unittest.TestCase):
    """НЕ ДОКАЗАН > НЕИЗВЕСТНО > ДОКАЗАН — дословно с полосы VPS, принято как ЗАЯВЛЕННОЕ."""

    def test_the_order_is_written_as_numbers(self):
        self.assertGreater(rj.STRENGTH[rj.DISPROVEN], rj.STRENGTH[rj.UNKNOWN])
        self.assertGreater(rj.STRENGTH[rj.UNKNOWN], rj.STRENGTH[rj.PROVEN])
        self.assertEqual(rj.VERDICTS, (rj.DISPROVEN, rj.UNKNOWN, rj.PROVEN))

    def test_combine_picks_the_strongest(self):
        self.assertEqual(rj.combine([rj.PROVEN, rj.PROVEN]), rj.PROVEN)
        self.assertEqual(rj.combine([rj.PROVEN, rj.UNKNOWN]), rj.UNKNOWN)
        self.assertEqual(rj.combine([rj.PROVEN, rj.UNKNOWN, rj.DISPROVEN]), rj.DISPROVEN)
        self.assertEqual(rj.stronger(rj.UNKNOWN, rj.DISPROVEN), rj.DISPROVEN)

    def test_nothing_judged_is_unknown_not_proven(self):
        self.assertEqual(rj.combine([]), rj.UNKNOWN)
        self.assertEqual(rj.combine(["мусор"]), rj.UNKNOWN)

    def test_answers_combine_by_the_very_same_order(self):
        """Две реализации одного порядка обязаны совпадать — иначе они разойдутся молча."""
        pairs = {True: rj.PROVEN, False: rj.DISPROVEN, None: rj.UNKNOWN}
        for first in (True, False, None):
            for second in (True, False, None):
                merged = rj._combine_answers([first, second])
                self.assertEqual(pairs[merged], rj.combine([pairs[first], pairs[second]]),
                                 "%r + %r" % (first, second))
        self.assertIsNone(rj._combine_answers([]))


# ═════════════════════════ ВРЕМЯ: ТОЛЬКО julianday ═══════════════════════════════════════════

class TestJulianDayOnly(unittest.TestCase):
    """ЗАМОК МЕТОДА. Разности времени считает SQLite, а не Python, — и это проверяется
    устройством модуля, а не обещанием: ни одного узла вычитания в дереве разбора."""

    def _tree(self):
        with open(SRC, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_not_a_single_python_subtraction_in_the_module(self):
        subs = [n.lineno for n in ast.walk(self._tree())
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub)]
        self.assertEqual(subs, [], "вычитание на Python в строках %s" % subs)

    def test_no_time_library_is_imported_at_all(self):
        banned = {"datetime", "time", "calendar"}
        seen = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                seen.extend(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                seen.append((node.module or "").split(".")[0])
        self.assertEqual(banned.intersection(seen), set(), "импорт времени: %s" % seen)
        for name in ("timedelta", "total_seconds", "monotonic", "perf_counter", "mktime"):
            self.assertNotIn(name, [n.attr for n in ast.walk(self._tree())
                                    if isinstance(n, ast.Attribute)], name)

    def test_julianday_is_what_actually_counts_the_difference(self):
        one_day = rj._days_between((rj.T_ISO, "2026-08-16 00:00:00"),
                                   (rj.T_ISO, "2026-08-15 00:00:00"))
        self.assertAlmostEqual(one_day, 1.0, places=6)
        back = rj._days_between((rj.T_ISO, "2026-08-15 00:00:00"),
                                (rj.T_ISO, "2026-08-16 00:00:00"))
        self.assertAlmostEqual(back, -1.0, places=6)

    def test_all_three_time_shapes_meet_on_one_scale(self):
        """ISO СО СМЕЩЕНИЕМ, unix-секунды и виндовый FILETIME — одно и то же мгновение.

        Пара взята не из головы: это коммит `origin/main` живого дерева, `git show -s
        --format='%cI %ct'` → `2026-08-15T17:26:20+07:00 1786789580`. Смещение `+07:00`
        в наборе ОБЯЗАТЕЛЬНО: git отдаёт время именно так, и молчаливая потеря часового
        пояса дала бы у вида `service_start` ровно семичасовую ошибку знака."""
        iso = (rj.T_ISO, "2026-08-15T17:26:20+07:00")
        unix = (rj.T_UNIX, 1786789580)
        filetime = (rj.T_FILETIME, 134312631800000000)     # (unix + 11644473600) * 10^7
        self.assertAlmostEqual(rj._days_between(iso, unix), 0.0, places=6)
        self.assertAlmostEqual(rj._days_between(filetime, unix), 0.0, places=6)
        self.assertAlmostEqual(rj._days_between(filetime, iso), 0.0, places=6)

    def test_unparsable_time_is_none_not_zero(self):
        """Ноль означал бы «одновременно» — а мы не знаем. Третье состояние держится и здесь."""
        self.assertIsNone(rj._days_between((rj.T_ISO, "мусор"), (rj.T_UNIX, 1786883921)))
        self.assertIsNone(rj._days_between(("неведомый вид", 1), (rj.T_UNIX, 1)))
        self.assertIsNone(rj._days_between(None, (rj.T_UNIX, 1)))

    def test_in_window_is_computed_but_never_fatal(self):
        window = ("2026-08-16 10:00:00", "2026-08-16 12:00:00")
        self.assertTrue(rj._in_window((rj.T_ISO, "2026-08-16 11:00:00"), window))
        self.assertFalse(rj._in_window((rj.T_ISO, "2026-08-16 13:00:00"), window))
        self.assertIsNone(rj._in_window(None, window))
        self.assertIsNone(rj._in_window((rj.T_ISO, "2026-08-16 11:00:00"), None))


# ═════════════════════════ ИНВАРИАНТ RESULT_JUDGE_UNWIRED ════════════════════════════════════
# ГРАНИЦА ЗАХОДА ЧИСЛОМ: судья построен и НЕ ПОДКЛЮЧЁН. Ноль вызовов из боевого кода.

MODULE = "result_judge_pc"


def prod_sources(repo):
    """Боевые `*.py` В КОРНЕ (не рекурсивно, `test_*.py` не в счёте), ТОЛЬКО ОТСЛЕЖИВАЕМЫЕ git.

    Отслеживаемые — потому что в дереве полосы лежат мины: `manager-bot/` (июньский снимок VPS),
    архивные копии гарда, `_tmp_*.py` разведки. Нерекурсивно и по git — тот же метод, каким
    считает `nonparse_scan.repo_files`, чтобы два стража мерили ОДНО множество."""
    try:
        done = subprocess.run(["git", "ls-files", "*.py"], cwd=repo, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    out = []
    for line in done.stdout.splitlines():
        name = line.strip()
        if len(name) == 0 or "/" in name or name.startswith("test_"):
            continue
        if name == MODULE + ".py":
            continue
        out.append(os.path.join(repo, name))
    return out


def wiring_findings(src, where="<строка>"):
    """→ список мест, где боевой код зовёт судью. Пустой = судья не подключён."""
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == MODULE:
                    out.append("%s:%d импорт «%s»" % (where, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == MODULE:
                out.append("%s:%d импорт из «%s»" % (where, node.lineno, node.module))
        elif isinstance(node, ast.Name) and node.id == MODULE:
            out.append("%s:%d имя «%s» в коде" % (where, node.lineno, MODULE))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and MODULE in node.value:
            out.append("%s:%d строка с именем «%s»" % (where, node.lineno, MODULE))
    return out


class TestJudgeIsUnwired(unittest.TestCase):
    """FAIL-CLOSED: git не ответил или файлов ноль → провал, а не «нарушений нет»."""

    def test_no_prod_module_calls_the_judge(self):
        files = prod_sources(REPO)
        self.assertIsNotNone(files, "git ls-files не ответил — множество боевых файлов неизвестно")
        self.assertGreater(len(files), 30, "боевых файлов подозрительно мало: %d" % len(files))
        found = []
        for path in files:
            with open(path, encoding="utf-8") as handle:
                found.extend(wiring_findings(handle.read(), os.path.basename(path)))
        self.assertEqual(found, [], "СУДЬЯ ПОДКЛЮЧЁН, а заход это запретил: %s" % found)

    def test_the_invariant_catches_an_injected_wiring(self):
        """Инвариант, который не ловит подлог, — молчание, а не замок. Четыре подлога."""
        cases = [
            ("прямой импорт", "import result_judge_pc\n"),
            ("импорт с переименованием", "import result_judge_pc as rj\n"),
            ("частичный импорт", "from result_judge_pc import judge\n"),
            ("динамика строкой", "m = __import__('result_judge_pc')\n"),
        ]
        for name, src in cases:
            self.assertTrue(wiring_findings(src), "подлог «%s» инвариант не поймал" % name)
        self.assertEqual(wiring_findings("import result_ref as rr\nX = rr.FIELD\n"), [],
                         "законный код инвариант флагать не должен")

    def test_the_daemon_does_not_carry_the_judge_in_its_runtime_list(self):
        """Демон не знает о судье и на уровне списка модулей самообновления."""
        with open(os.path.join(REPO, "pc_orchestrator.py"), encoding="utf-8") as handle:
            self.assertNotIn(MODULE, handle.read(), "имя судьи просочилось в демона")


# ═════════════════════════ ИНВАРИАНТ READ_ONLY ═══════════════════════════════════════════════

class TestJudgeOnlyReads(unittest.TestCase):
    """ТОЛЬКО ЧТЕНИЕ — устройством. Все SQL-литералы начинаются со `SELECT`, git знает три
    читающие подкоманды, сети нет вовсе, очередь не трогается ни одним именем."""

    def _tree(self):
        with open(SRC, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_every_sql_literal_is_a_select(self):
        bad = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                head = node.value.strip().upper()
                for word in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "PRAGMA"):
                    # СО ПРОБЕЛОМ: иначе имя константы `CREATE_NO_WINDOW` читается как SQL —
                    # страж, ловящий подстроку вместо слова, ловит себя (класс полосы).
                    if head.startswith(word + " "):
                        bad.append((node.lineno, node.value))
        self.assertEqual(bad, [], "не читающий SQL в судье: %s" % bad)

    def test_the_row_probe_opens_the_base_read_only(self):
        self.assertIn("?mode=ro", rj._ro_uri("D:\\turbobaby-bot\\fixture.db"))
        self.assertTrue(rj._ro_uri("D:\\turbobaby-bot\\fixture.db").startswith("file:///D:/"))

    def test_git_subcommands_are_reading_only(self):
        self.assertEqual(rj.GIT_READ_ONLY, ("rev-parse", "merge-base", "show"))
        starts = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "run_git" and node.args:
                first = node.args[0]
                self.assertIsInstance(first, ast.List, "аргумент git собран не списком-литералом")
                starts.append(first.elts[0].value)
        self.assertTrue(starts, "вызовов git в судье не нашлось — инвариант пуст")
        for one in starts:
            self.assertIn(one, rj.GIT_READ_ONLY, "подкоманда git вне читающих: %s" % one)

    def test_no_network_and_no_queue_names_in_the_module(self):
        banned_roots = {"bridge_http", "urllib", "socket", "requests", "http", "brain_writer",
                        "cowork_log_append", "pc_orchestrator"}
        seen = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                seen.extend(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                seen.append((node.module or "").split(".")[0])
        self.assertEqual(banned_roots.intersection(seen), set(), "судья пошёл наружу: %s" % seen)
        names = [n.attr for n in ast.walk(self._tree()) if isinstance(n, ast.Attribute)]
        for one in ("claim_task", "complete_task", "enqueue_task", "approve_task", "write_doc",
                    "remove", "unlink", "rmtree", "kill", "system"):
            self.assertNotIn(one, names, "судья умеет менять мир: %s" % one)

    def test_the_guard_of_the_git_runner_refuses_a_writing_subcommand(self):
        """Список читающих подкоманд — не украшение: живой бегун отказывает вслух."""
        self.assertIsNone(rj.Ctx(repo=REPO)._git(["commit", "-m", "нет"]))
        self.assertIsNone(rj.Ctx(repo=REPO)._git([]))

    def test_file_probe_never_reads_content(self):
        names = [n.id for n in ast.walk(self._tree()) if isinstance(n, ast.Name)]
        self.assertNotIn("open", names, "судья открыл файл — виду `file` хватает stat")


# ═════════════════════════ СКВОЗНОЕ ПОВЕДЕНИЕ ════════════════════════════════════════════════

class TestJudgeShape(_Fixture):

    def test_verdict_shape_is_stable(self):
        self._file("есть.md", "тело")
        got = rj.judge("file есть.md", self._ctx())
        self.assertEqual(sorted(got), ["detail", "kind", "pointer", "reading", "verdict"])
        self.assertIn(got["verdict"], rj.VERDICTS)
        self.assertEqual(got["kind"], "file")
        self.assertTrue(got["detail"], "вердикт обязан ГОВОРИТЬ, чем он получен")

    def test_not_named_is_unknown_for_every_shape_of_emptiness(self):
        for empty in ("", "   ", None, 17, "мусор", "commit:abcdef1"):
            got = rj.judge(empty, self._ctx())
            self.assertEqual(got["verdict"], rj.UNKNOWN, repr(empty))
            self.assertEqual(got["detail"], "адрес не назван", repr(empty))

    def test_verdict_of_is_pure_and_needs_no_context(self):
        ref = "file есть.md"
        self.assertEqual(rj.verdict_of(ref, {rj.ANSWER: True, rj.DETAIL: "есть"})[0], rj.PROVEN)
        self.assertEqual(rj.verdict_of(ref, {rj.ANSWER: False, rj.DETAIL: "нет"})[0], rj.DISPROVEN)
        self.assertEqual(rj.verdict_of(ref, {rj.ANSWER: None, rj.DETAIL: "?"})[0], rj.UNKNOWN)
        self.assertEqual(rj.verdict_of(ref, None)[0], rj.UNKNOWN)
        self.assertEqual(rj.verdict_of("", {rj.ANSWER: True})[0], rj.UNKNOWN)

    def test_every_kind_of_the_canon_has_a_reader(self):
        """Пятый вид без читателя — тихая дыра: адрес примут, а прочесть его будет нечем."""
        self.assertEqual(sorted(rj.PROBES), sorted(rr.KINDS))

    def test_read_back_says_words_even_when_it_fails(self):
        for ref in ("commit смотри отчёт", "file C:\\Windows\\win.ini", "brain узел §что-то"):
            got = rj.read_back(ref, self._ctx())
            self.assertIsNone(got[rj.ANSWER], ref)
            self.assertTrue(got[rj.DETAIL], "молчаливое «не знаю»: %s" % ref)


# ═════════════════════════ ТЕНЬ ЦЕПОЧКИ: ЧЕТЫРЕ ЗАМКА ЗАХОДА ═════════════════════════════════
# Порядок тот же, что и у судьи шага: отрицательный тест ПЕРВЫМ, различающая сила второй,
# «тень не влияет на ход» третьим, форма записи последней.

class _Chain(_Fixture):
    """Утварь для цепочек: ряды очереди собираются В ЖИВОМ ФОРМАТЕ МОСТА (снят пробой 14.08,
    `test_queue_snapshot_pc`), адрес едет КАНОНОМ в `task_text` — тем же маркером, каким его
    пишет `_loc_release`. Идеализированной схемы «как удобно тесту» здесь нет."""

    def _row(self, tid, text="дело", ref="", status="done"):
        body = (rr.prefix(ref) + text) if ref else text
        return {"id": str(tid), "created": "2026-08-14T15:30:22.664Z", "from": "Filipp-pcloc-dec",
                "task_text": body, "status": status, "result": "итог", "approved_by": "",
                "updated": "2026-08-14T15:41:02.010Z", "lane": "pc"}

    def _step(self, tid, i, n, pid, ref="", status="done"):
        return self._row(tid, "[шаг %d/%d родитель %d] дело" % (i, n, pid), ref, status)


class TestChainShadowNegativeFirst(_Chain):
    """ГЛАВНЫЙ замок захода, прогнан первым. Цепочка, чей КОРНЕВОЙ АДРЕС УКАЗЫВАЕТ В ПУСТОТУ,
    обязана дать теневое НЕ ДОКАЗАН — ПРИ ЖИВОМ ЗЕЛЁНОМ НАСТОЯЩЕМ. Позеленела хоть одна —
    правило выродилось, и это видно числом, а не рассуждением."""

    def _void_cases(self):
        """Корневой адрес НАЗВАН и ведёт в пустоту. Настоящий исход у всех — ЗЕЛЁНЫЙ."""
        self._db()
        self._file("пусто.md", "")
        return [
            ("файла по адресу нет", "file нет-такого-файла.md"),
            ("файл есть, но ПУСТ", "file пусто.md"),
            ("строки по условию нет", "row fixture.db:drafts:9999"),
            ("хеша нет в origin/main", "commit " + ABSENT_HASH),
        ]

    def test_root_pointing_into_the_void_is_disproven_while_the_real_is_green(self):
        green = 0
        wrong = []
        for name, ref in self._void_cases():
            ctx = self._ctx(run_git=self._git_says({
                ("rev-parse", "--verify", "--quiet", ABSENT_HASH + "^{commit}"): (1, ""),
            }))
            got = rj.chain_shadow(self._row(4, ref=ref),
                                  [self._step(7, 1, 2, 4), self._step(8, 2, 2, 4)], ctx)
            if got["verdict"] == rj.PROVEN:
                green += 1
            if got["verdict"] != rj.DISPROVEN:
                wrong.append("%s → %s" % (name, got["verdict"]))
            # настоящий ЖИВОЙ ЗЕЛЁНЫЙ: тень обязана назвать разрыв, а не подпеть
            self.assertEqual(rj.shadow_diff(True, got["verdict"]), rj.STRICTER, name)
        self.assertEqual(green, 0, "ПОЗЕЛЕНЕЛО пустых корней: %d — правило выродилось" % green)
        self.assertEqual(wrong, [], "не НЕ ДОКАЗАН: %s" % "; ".join(wrong))

    def test_the_void_set_is_enumerable(self):
        """Числа замка уходят в артефакт и журнал — набор обязан быть перечислимым."""
        self.assertEqual(len(self._void_cases()), 4)

    def test_root_without_an_address_is_unknown_for_the_whole_chain(self):
        """Корень молчит → НЕИЗВЕСТНО про всю цепочку. Это НЕ «в порядке» и НЕ «не доказан»."""
        got = rj.chain_shadow(self._row(4), [self._step(7, 1, 1, 4)], self._ctx())
        self.assertEqual(got["verdict"], rj.UNKNOWN)
        self.assertIn("адрес не назван", got["detail"])


class TestChainShadowDiscriminatingPower(_Chain):
    """РАЗЛИЧАЮЩАЯ СИЛА: порча ОДНОГО ЗНАКА в КОРНЕВОМ адресе переворачивает вердикт всей
    цепочки. Тень, у которой доказанное остаётся доказанным при испорченном корне, не читает
    назад, а поддакивает."""

    def _green_chain(self, ref):
        return rj.chain_shadow(self._row(4, ref=ref),
                               [self._step(7, 1, 2, 4), self._step(8, 2, 2, 4)], self._ctx())

    def test_one_char_in_the_root_pointer_flips_the_whole_chain(self):
        self._file("есть.md", "тело")
        self.assertEqual(self._green_chain("file есть.md")["verdict"], rj.PROVEN)
        self.assertEqual(self._green_chain("file ecть.md")["verdict"], rj.DISPROVEN)

    def test_one_char_in_a_row_condition_flips_the_whole_chain(self):
        self._db()
        self.assertEqual(self._green_chain("row fixture.db:drafts:1278")["verdict"], rj.PROVEN)
        self.assertEqual(self._green_chain("row fixture.db:drafts:1279")["verdict"], rj.DISPROVEN)

    def test_the_live_windows_path_of_this_lane_is_read_as_the_root(self):
        """Виндовый путь СВОЕЙ полосы — обязательный случай канона (05e54aa), и у корня тоже."""
        got = rj.chain_shadow(self._row(4, ref=rr.make("file", WIN_SELF)), (), rj.Ctx(repo=REPO))
        self.assertEqual(got["verdict"], rj.PROVEN)

    @unittest.skipUnless(HAS_GIT, "origin/main не прочитан — живой коммит недоступен")
    def test_one_char_in_a_live_commit_flips_the_whole_chain(self):
        good = rj.chain_shadow(self._row(4, ref="commit " + ORIGIN_MAIN[:12]), (),
                               rj.Ctx(repo=REPO))
        self.assertEqual(good["verdict"], rj.PROVEN)
        spoiled = ORIGIN_MAIN[:11] + ("0" if ORIGIN_MAIN[11] != "0" else "1")
        bad = rj.chain_shadow(self._row(4, ref="commit " + spoiled), (), rj.Ctx(repo=REPO))
        self.assertEqual(bad["verdict"], rj.DISPROVEN)


class TestStepWithoutAddressDoesNotBreakTheChain(_Chain):
    """ПРАВИЛО 2 — УСТРОЙСТВОМ, А НЕ ОГОВОРКОЙ. Безадресный шаг в бюллетень не кладётся вовсе;
    если бы клался, он давал бы НЕИЗВЕСТНО, а НЕИЗВЕСТНО сильнее ДОКАЗАНного — и цепочка с
    молчащим шагом садилась бы в НЕИЗВЕСТНО навсегда. Контрфакт считается прямо здесь."""

    def test_five_silent_steps_do_not_move_the_chain_off_proven(self):
        self._file("есть.md", "тело")
        steps = [self._step(10 + k, k + 1, 5, 4) for k in range(5)]
        got = rj.chain_shadow(self._row(4, ref="file есть.md"), steps, self._ctx())
        self.assertEqual(got["verdict"], rj.PROVEN)
        self.assertEqual(got[rj.MUTED], 5, "молчание обязано быть ВИДНО числом")
        self.assertEqual(got[rj.VOTED], 1, "в бюллетене только корень")
        # КОНТРФАКТ: судись молчащие шаги — было бы НЕИЗВЕСТНО, то есть правило выродилось бы
        self.assertEqual(rj.combine([rj.PROVEN] + [rj.UNKNOWN] * 5), rj.UNKNOWN)

    def test_a_step_with_its_own_address_is_judged_additionally(self):
        self._file("есть.md", "тело")
        root = self._row(4, ref="file есть.md")
        loud = self._step(7, 1, 2, 4, ref="file нет-такого.md")
        got = rj.chain_shadow(root, [loud, self._step(8, 2, 2, 4)], self._ctx())
        self.assertEqual(got["verdict"], rj.DISPROVEN, "НЕ ДОКАЗАН шага обязан оборвать цепочку")
        self.assertEqual(got[rj.VOTED], 2)
        self.assertEqual(got[rj.MUTED], 1)

    def test_an_unreadable_step_sits_the_chain_in_unknown_not_in_green(self):
        self._file("есть.md", "тело")
        got = rj.chain_shadow(self._row(4, ref="file есть.md"),
                              [self._step(7, 1, 1, 4, ref="brain queue_state_pc §полоса ПК")],
                              self._ctx())
        self.assertEqual(got["verdict"], rj.UNKNOWN)

    def test_a_chain_without_steps_at_all_is_judged_by_its_root(self):
        """Корень БЕЗ шагов — тоже цепочка (единица полосы), и судится своим адресом."""
        self._file("есть.md", "тело")
        self.assertEqual(rj.chain_shadow(self._row(4, ref="file есть.md"), (),
                                         self._ctx())["verdict"], rj.PROVEN)
        self.assertEqual(rj.chain_shadow(self._row(4, ref="file нет.md"), (),
                                         self._ctx())["verdict"], rj.DISPROVEN)


class TestShadowDoesNotTouchTheRun(_Chain):
    """ТЕНЬ НЕ ВЛИЯЕТ НА ХОД. Три замка: рядов не мутирует, писателя не имеет, настоящий исход
    не выводит сам."""

    def test_the_rows_come_out_byte_for_byte_as_they_went_in(self):
        self._file("есть.md", "тело")
        root = self._row(4, ref="file есть.md")
        steps = [self._step(7, 1, 2, 4), self._step(8, 2, 2, 4, ref="file есть.md")]
        before = repr(root) + "|" + repr(steps)
        rj.chain_shadow(root, steps, self._ctx())
        self.assertEqual(repr(root) + "|" + repr(steps), before, "тень поправила ряд очереди")

    def test_a_chain_with_an_unknown_shadow_is_told_nothing_about_moving(self):
        """Теневое НЕИЗВЕСТНО — это СЛОВО, а не команда: в ответе нет ни статуса очереди, ни
        имени действия, которым цепь двигают."""
        got = rj.chain_shadow(self._row(4), [self._step(7, 1, 1, 4)], self._ctx())
        self.assertEqual(got["verdict"], rj.UNKNOWN)
        flat = repr(got)
        for word in ("claim", "complete", "enqueue", "release", "in_progress", "failed"):
            self.assertNotIn(word, flat, "тень заговорила ходом цепи: %s" % word)

    def test_the_shadow_has_a_named_place_and_no_writer(self):
        """Место названо одно, а писателя нет: имени `open` в судье не может быть вовсе."""
        self.assertTrue(rj.SHADOW_LOG.endswith("result_shadow_chain_pc.jsonl"))
        self.assertEqual(os.path.dirname(rj.SHADOW_LOG), REPO)
        with open(SRC, encoding="utf-8") as handle:
            names = [n.id for n in ast.walk(ast.parse(handle.read())) if isinstance(n, ast.Name)]
        self.assertNotIn("open", names, "у тени завёлся писатель — «только чтение» сломано")
        self.assertNotIn(rj.SHADOW_LOG, [getattr(rj, n, None) for n in dir(rj)
                                         if n != "SHADOW_LOG"], "место тени названо дважды")

    def test_the_real_outcome_is_given_from_outside_not_guessed(self):
        """Судья не знает статусов очереди ни одним литералом — иначе тень толкует чужой слой."""
        with open(SRC, encoding="utf-8") as handle:
            src = handle.read()
        for word in ('"done"', '"failed"', '"in_progress"', '"needs_approval"'):
            self.assertNotIn(word, src, "статус очереди просочился в судью: %s" % word)


class TestShadowRecordShape(_Chain):
    """ФОРМА ЗАПИСИ: теневой вердикт РЯДОМ с настоящим и разница между ними — в одной строке."""

    def test_all_four_directions_of_the_difference_are_reachable(self):
        self.assertEqual(rj.shadow_diff(True, rj.PROVEN), rj.SAME)
        self.assertEqual(rj.shadow_diff(True, rj.UNKNOWN), rj.STRICTER)
        self.assertEqual(rj.shadow_diff(True, rj.DISPROVEN), rj.STRICTER)
        self.assertEqual(rj.shadow_diff(False, rj.PROVEN), rj.SOFTER)
        self.assertEqual(rj.shadow_diff(False, rj.DISPROVEN), rj.SAME)
        self.assertEqual(rj.shadow_diff(None, rj.PROVEN), rj.NOT_CLOSED)

    def test_the_record_carries_both_verdicts_and_the_difference(self):
        self._file("есть.md", "тело")
        shadow = rj.chain_shadow(self._row(4, ref="file есть.md"),
                                 [self._step(7, 1, 1, 4)], self._ctx())
        rec = rj.shadow_record(4, True, shadow)
        self.assertEqual(sorted(rec), ["chain", "detail", "diff", "kind", "muted", "pointer",
                                       "real", "shadow", "voted"])
        self.assertEqual(rec[rj.SHADOW], rj.PROVEN)
        self.assertEqual(rec[rj.REAL], rj.REAL_GREEN)
        self.assertEqual(rec[rj.DIFF], rj.SAME)
        self.assertEqual(rec["kind"], "file")
        self.assertEqual(rec[rj.MUTED], 1)
        self.assertTrue(rec[rj.DETAIL], "запись обязана ГОВОРИТЬ, чем вердикт получен")

    def test_the_record_says_the_real_word_for_every_tristate(self):
        shadow = rj.chain_shadow(self._row(4), (), self._ctx())
        self.assertEqual(rj.shadow_record(4, True, shadow)[rj.REAL], rj.REAL_GREEN)
        self.assertEqual(rj.shadow_record(4, False, shadow)[rj.REAL], rj.REAL_RED)
        self.assertEqual(rj.shadow_record(4, None, shadow)[rj.REAL], rj.REAL_OPEN)
        self.assertEqual(rj.shadow_record(4, None, shadow)[rj.DIFF], rj.NOT_CLOSED)


if __name__ == "__main__":
    unittest.main()
