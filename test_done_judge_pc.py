# -*- coding: utf-8 -*-
"""Регресс судьи закрытия полосы ПК (`done_judge_pc`) — ступень C.

ДВА ОТРИЦАТЕЛЬНЫХ ОБЯЗАТЕЛЬНЫ И НАЗВАНЫ ЗАДАНИЕМ ВЛАДЕЛЬЦА (01.09.2026):
  • отчёт успешный, адрес назван, по адресу ПУСТО            → «неизвестно»;
  • отчёт успешный, по адресу лежит результат ПРЕЖНЕГО прогона → «неизвестно».
Позеленел любой из них — прибор в прод не идёт. Поэтому оба проверяют не только слово вердикта,
но и ПРИЧИНУ: зелёное по случайности отличается от зелёного по делу только ею.

Положительный контроль здесь обязателен по той же причине, что и у V0: прибор, отвечающий
«неизвестно» на всё, проходит любой отрицательный тест и бесполезен.

    venv\\Scripts\\python.exe -m unittest test_done_judge_pc
"""

import ast
import os
import shutil
import tempfile
import unittest

import content_product_verifier as v0
import done_judge_pc as dj

FOLDER = "docs/artifacts"
WORDS = "ступень C V0 судит done"
TASK = ("ЦЕЛЬ: проверить прибор. ЗАПРЕТЫ: нет.\n"
        "АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 01.09 со словами " + WORDS)
NAME = "2026-09-01-stupen-c.md"
REL = FOLDER + "/" + NAME
# Заголовок с БОЛЬШОЙ буквы — живая форма артефактов полосы (замер 01.09: 4 из 4 адресов
# отвечают заголовком, и ни один — дословной строчной фразой).
BODY = "# Ступень C V0 судит done — разбор\n\nтело\n"
OLD = "# Ступень C V0 судит done — прошлый прогон\n\nстарое тело\n"


def _write(root, rel, text):
    full = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


class JudgeCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="done_judge_pc_")
        os.makedirs(os.path.join(self.root, FOLDER.replace("/", os.sep)))
        self.addCleanup(shutil.rmtree, self.root, True)

    def base(self, text=TASK):
        return dj.baseline(text, root=self.root)

    def judge(self, base, status="done", text=TASK, tid=90):
        return dj.judge(tid, text, status, base, run_id="pc-test-90", root=self.root)


class TestAddress(JudgeCase):
    def test_live_prose_form_parses(self):
        addr = dj.read_address(TASK)
        self.assertEqual((addr["folder"], addr["day"], addr["month"], addr["words"]),
                         (FOLDER, 1, 9, WORDS))

    def test_live_task_of_the_lane_parses(self):
        """Дословная строка закрытой задачи #72 — тест на реальной фразе, а не на идеальной."""
        addr = dj.read_address(
            "…3. Одно правило, КАК чинить весь класс — текстом, без применения. "
            "АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 01.09 со словами "
            "класс абсолютного корня в тестах")
        self.assertEqual(addr["words"], "класс абсолютного корня в тестах")
        self.assertEqual(addr["folder"], "docs/artifacts")

    def test_no_marker_is_no_address(self):
        self.assertIsNone(dj.read_address("ЦЕЛЬ: что-то сделать, адреса нет"))
        self.assertIsNone(dj.read_address(None))

    def test_address_out_of_tree_is_refused(self):
        self.assertIsNone(dj.read_address(dj.MARK + ": файл C:/Windows/win.ini"))
        self.assertIsNone(dj.read_address(dj.MARK + ": файл в ../чужое за 01.09 со словами x"))

    def test_direct_path_form(self):
        addr = dj.read_address(dj.MARK + ": файл docs/artifacts/a.md со словами " + WORDS)
        self.assertEqual((addr["path"], addr["words"]), ("docs/artifacts/a.md", WORDS))


class TestPositiveControl(JudgeCase):
    def test_fresh_product_at_the_named_address_is_done(self):
        base = self.base()
        _write(self.root, REL, BODY)
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.DONE, out["reason"])
        self.assertEqual(out["v0"]["verdict"], v0.PROVEN)
        self.assertEqual(out["v0"]["reason_code"], "all_gates_passed")
        self.assertEqual(out["chosen"], REL)

    def test_case_of_the_heading_is_not_evidence(self):
        """Слова адреса — строчными, в артефакте — заголовком. Это ОДНА фраза."""
        base = self.base()
        _write(self.root, REL, BODY)
        self.assertNotIn(WORDS, BODY)                      # дословно её в теле НЕТ
        self.assertEqual(self.judge(base)["verdict"], dj.DONE)

    def test_changed_old_file_counts_as_this_run(self):
        _write(self.root, REL, OLD)
        base = self.base()
        _write(self.root, REL, BODY)                       # заход дописал сам
        self.assertEqual(self.judge(base)["verdict"], dj.DONE)


class TestMandatoryNegatives(JudgeCase):
    def test_report_says_done_but_the_address_is_empty(self):
        """ОТРИЦАТЕЛЬНЫЙ 1 (задание, п. 3). Отчёт успешен, адрес назван, по адресу ПУСТО."""
        base = self.base()
        _write(self.root, FOLDER + "/2026-08-30-другое.md", BODY)   # соседний день — не адрес
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertIn("ПУСТО", out["reason"])
        self.assertIsNone(out["v0"])

    def test_report_says_done_but_the_address_holds_the_previous_run(self):
        """ОТРИЦАТЕЛЬНЫЙ 2 (задание, п. 4). По адресу — продукт ПРЕЖНЕГО прогона, не тронут.

        Ловит это не адаптер, а V0: адрес назван в `allowed_changed_paths`, изменений по нему
        ноль → `changed_scope_mismatch`. Слова адреса при этом НАЙДЕНЫ — то есть внешний признак
        зелёный, как у сданной работы, и отличается ровно одно."""
        _write(self.root, REL, OLD)
        base = self.base()                                  # опорный снимок ВИДИТ труп
        out = self.judge(base)                              # заход не тронул ничего
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertEqual(out["v0"]["verdict"], v0.DISPROVEN)
        self.assertEqual(out["v0"]["reason_code"], "changed_scope_mismatch")
        self.assertIn(WORDS.casefold(), OLD.casefold())     # признак был зелёным

    def test_previous_run_survives_a_lying_report(self):
        """Тот же труп, но отчёт исполнителя кричит об успехе. Отчёт в бандл не входит вовсе."""
        _write(self.root, REL, OLD)
        base = self.base()
        out = dj.judge(90, TASK, "done", base, run_id="pc-test-90", root=self.root)
        self.assertEqual(out["verdict"], dj.UNKNOWN)


class TestOtherRefusals(JudgeCase):
    def test_product_without_the_named_words_is_not_proven(self):
        base = self.base()
        _write(self.root, REL, "# Совсем про другое\n")
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertEqual(out["v0"]["reason_code"], "text_condition_failed")

    def test_no_address_is_unknown_not_done(self):
        text = "ЦЕЛЬ: что-то сделать. Адреса результата нет."
        out = dj.judge(90, text, "done", dj.baseline(text, root=self.root), root=self.root)
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertIn("не назван", out["reason"])

    def test_baseline_not_taken_is_unknown(self):
        out = self.judge({"address": dj.read_address(TASK), "files": None, "ok": False})
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertIn("НЕ СНЯТ", out["reason"])

    def test_disappeared_product_is_unknown(self):
        _write(self.root, REL, BODY)
        base = self.base()
        os.remove(os.path.join(self.root, REL.replace("/", os.sep)))
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertIn("ИСЧЕЗЛО", out["reason"])
        self.assertNotIn("нет ни одного файла", out["reason"])   # «не появилось» ≠ «снесли»

    def test_only_done_is_judged(self):
        base = self.base()
        for status in ("failed", "needs_approval", "new"):
            self.assertIsNone(self.judge(base, status=status), status)

    def test_judge_never_raises_and_a_crash_is_not_done(self):
        for base in (None, {}, {"address": {"folder": 1}, "files": {}, "ok": True}):
            out = dj.judge(90, TASK, "done", base, root=self.root)
            self.assertIsNotNone(out)
            self.assertNotEqual(out["verdict"], dj.DONE)


class TestEnforcement(unittest.TestCase):
    def test_mode_from_environment(self):
        self.assertEqual(dj.enforce_mode({}), dj.MODE_ADDR)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "off"}), dj.MODE_OFF)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "0"}), dj.MODE_OFF)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "all"}), dj.MODE_ALL)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "1"}), dj.MODE_ALL)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "мусор"}), dj.MODE_ADDR)

    def test_who_changes_the_status(self):
        addressed = {"verdict": dj.UNKNOWN, "address": {"words": "x"}}
        blind = {"verdict": dj.UNKNOWN, "address": None}
        proven = {"verdict": dj.DONE, "address": {"words": "x"}}
        self.assertTrue(dj.enforces(addressed, dj.MODE_ADDR))
        self.assertFalse(dj.enforces(blind, dj.MODE_ADDR))      # безадресную задачу не трогаем
        self.assertTrue(dj.enforces(blind, dj.MODE_ALL))        # полное правило владельца
        self.assertFalse(dj.enforces(proven, dj.MODE_ALL))
        self.assertFalse(dj.enforces(addressed, dj.MODE_OFF))
        self.assertFalse(dj.enforces(None, dj.MODE_ALL))

    def test_line_puts_the_verdict_in_words(self):
        self.assertEqual(dj.line(None), "")
        self.assertIn(dj.UNKNOWN, dj.line({"verdict": dj.UNKNOWN, "reason": "почему"}))

    def test_run_token_comes_from_the_daemon_not_the_executor(self):
        self.assertEqual(dj.run_token(90, "tmp/pc_report/task90-11480-1788265605336-90.md"),
                         "11480-1788265605336-90")
        self.assertEqual(dj.run_token(90, None), "pc-task-90")


class TestPurity(unittest.TestCase):
    """Судья исхода не смеет иметь рук сильнее чтения — и не смеет смотреть на часы."""

    def source(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "done_judge_pc.py"),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_no_network_no_processes_no_database(self):
        tree = ast.parse(self.source())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []) or []:
                    names.add((getattr(node, "module", None) or alias.name).split(".")[0])
        self.assertEqual(names.intersection(
            {"subprocess", "socket", "requests", "urllib", "sqlite3", "bridge_http",
             "brain_writer", "pc_orchestrator"}), set())

    def test_freshness_is_measured_not_clocked(self):
        """Класс О3 этой полосы: свежий ЧУЖОЙ файл не доказывает авторство. Часов здесь нет."""
        source = self.source()
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []) or []:
                    names.add((getattr(node, "module", None) or alias.name).split(".")[0])
        self.assertEqual(names.intersection({"time", "datetime", "calendar"}), set())
        for forbidden in ("st_mtime", "getmtime", "getctime", "utcnow", "monotonic"):
            self.assertNotIn(forbidden, source)

    def test_writes_only_into_the_one_named_folder(self):
        self.assertEqual(dj.PACKET_DIR, "tmp/done_judge_pc")
        for node in ast.walk(ast.parse(self.source())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                mode = node.args[1].value if len(node.args) > 1 else "r"
                self.assertIn(mode, ("rb", "wb"), "неожиданный режим открытия: %s" % mode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
