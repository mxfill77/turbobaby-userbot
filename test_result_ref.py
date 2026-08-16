# -*- coding: utf-8 -*-
"""
test_result_ref.py — регресс ПОЛЯ АДРЕСА РЕЗУЛЬТАТА (`result_ref`) + инвариант RESULT_REF_PURE.

Четыре вещи, ради которых файл существует:
  1. СЛОВАРЬ ПОЛЯ ЗАКРЕПЛЁН КОДОМ. Имя поля, РАЗДЕЛИТЕЛЬ-ПРОБЕЛ, двоеточие ключа и ПЯТЬ видов
     проверяются литералами: на полосе VPS живёт такое же поле, и разойтись они должны громко
     (красный тест), а не тихо (два несовместимых формата в одном листе очереди).
  2. ВИНДОВЫЙ ПУТЬ — ОБЯЗАТЕЛЬНЫЙ СЛУЧАЙ (`TestWindowsPathPointer`). Ради него канон Штаба
     16.08.2026 и сменил разделитель на пробел: `D:\\turbobaby-bot\\…` несёт своё двоеточие,
     и адрес обязан оставаться самоограниченным при ЛЮБОМ читателе, а не только при «делим по
     первому двоеточию».
  3. ПУСТОЕ — ЗАКОННОЕ ЗНАЧЕНИЕ, а не сбой. «Адрес не назван» обязан давать пустую строку и
     пустой префикс: на этом стои́т замок «старый путь байт-в-байт» в `_loc_release`.
  4. ГРАНИЦА ДЕРЖИТСЯ УСТРОЙСТВОМ. `RESULT_REF_PURE` (ast-разбор, зеркало EXPECT_PC_PURE и
     CARD_DUTY_PURE этой полосы) доказывает, что модуль НЕ УМЕЕТ проверить адрес: ни файла,
     ни git, ни моста, ни базы. Поле заведено инертным — и инертность держится тем, что рук
     нет вовсе, а не обещанием в докстринге.

Запуск — тем же способом, что и весь гейт репозитория (способ запуска — часть формата):
    venv\\Scripts\\python.exe -m unittest test_result_ref
"""
import ast
import os
import unittest

import result_ref as rr

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "result_ref.py")

# Живой виндовый путь этой полосы: двоеточие диска + обратные слэши. Ровно на нём деление
# по двоеточию переставало быть самоограниченным (замер 16.08, шапка result_ref.py).
WIN_PATH = r"D:\turbobaby-bot\docs\artifacts\2026-08-16-ref-canon-pc.md"


# ═════════════════════════ СЛОВАРЬ: имя, разделитель, пять видов ═════════════════════════

class TestVocabulary(unittest.TestCase):
    """Литералы договора. Меняешь их — краснеет здесь, а не на чужой полосе через сутки."""

    def test_field_name_is_the_word_we_will_reconcile_with_vps(self):
        self.assertEqual(rr.FIELD, "result_ref")
        self.assertEqual(rr.EMPTY, "")

    def test_separator_is_a_space_this_is_the_whole_point_of_the_canon(self):
        """ПРОБЕЛ, а не двоеточие: двоеточие живёт В УКАЗАТЕЛЕ (`D:\\…`, `db:table:17`)."""
        self.assertEqual(rr.SEP, " ")
        self.assertNotIn(":", rr.SEP)

    def test_exactly_five_kinds_named_by_the_owner(self):
        self.assertEqual(rr.KINDS, ("commit", "file", "row", "brain", "service_start"))
        self.assertEqual(sorted(rr.KIND_TITLES), sorted(rr.KINDS))
        self.assertEqual(rr.KIND_TITLES["commit"], "коммит в origin/main")
        self.assertEqual(rr.KIND_TITLES["file"], "файл на диске")
        self.assertEqual(rr.KIND_TITLES["row"], "строка в базе")
        self.assertEqual(rr.KIND_TITLES["brain"], "узел мозга")
        self.assertEqual(rr.KIND_TITLES["service_start"], "старт сервиса моложе коммита")

    def test_the_fifth_kind_is_service_start_not_service(self):
        """Пятый вид полосы VPS зовётся `service_start`; прежнее `service` — снятое имя."""
        self.assertEqual(rr.KINDS[4], "service_start")
        self.assertEqual(rr.KIND_SERVICE_START, "service_start")
        self.assertNotIn("service", rr.KINDS)
        self.assertFalse(hasattr(rr, "KIND_SERVICE"), "снятое имя вида осталось в модуле")

    def test_marker_keyword_is_the_field_name_with_the_key_colon(self):
        """Имя поля и ключевое слово маркера — ОДНА строка; двоеточие принадлежит КЛЮЧУ."""
        self.assertTrue(rr.marker("commit a545ad2").startswith("[" + rr.FIELD + ": "))


# ═════════════════════════ ЗНАЧЕНИЕ: сборка, разбор, канон ═══════════════════════════════

class TestMakeAndParse(unittest.TestCase):

    def test_roundtrip_for_every_kind(self):
        cases = {
            "commit": "a545ad2",
            "file": "docs/artifacts/2026-08-16-ref-canon-pc.md",
            "row": "moderation_ipc.db:drafts:1278",     # двоеточия ВНУТРИ указателя законны
            "brain": "orchestrator_plan §контракт третьего исхода",   # и пробелы тоже
            "service_start": "pc_orchestrator PID 12345 старше HEAD a545ad2",
        }
        for kind, pointer in cases.items():
            val = rr.make(kind, pointer)
            self.assertEqual(val, kind + " " + pointer)
            self.assertEqual(rr.parse(val), (kind, pointer))
            self.assertTrue(rr.is_named(val))
            self.assertEqual(rr.norm(val), val)

    def test_only_the_first_space_divides(self):
        """Пробелы в указателе законны — делит ПЕРВЫЙ, остальные едут внутрь дословно."""
        self.assertEqual(rr.parse("brain queue_state_pc §полоса ПК"),
                         ("brain", "queue_state_pc §полоса ПК"))
        self.assertEqual(rr.parse("service_start moderation_bot PID 4242 старше HEAD"),
                         ("service_start", "moderation_bot PID 4242 старше HEAD"))

    def test_colons_never_divide_anything(self):
        """Двоеточие разделителем БЫТЬ ПЕРЕСТАЛО: указатель несёт их сколько угодно."""
        self.assertEqual(rr.parse("row db:table:17"), ("row", "db:table:17"))
        self.assertEqual(rr.parse("file C:\\a\\b:z.md"), ("file", "C:\\a\\b:z.md"))

    def test_case_and_spaces_are_normalised_not_rejected(self):
        self.assertEqual(rr.norm("  COMMIT   a545ad2  "), "commit a545ad2")

    def test_empty_is_a_legal_value_meaning_not_named(self):
        for empty in ("", "   ", None, 0, [], {}):
            self.assertIsNone(rr.parse(empty), repr(empty))
            self.assertFalse(rr.is_named(empty), repr(empty))
            self.assertEqual(rr.norm(empty), rr.EMPTY, repr(empty))
            self.assertEqual(rr.marker(empty), rr.EMPTY, repr(empty))
            self.assertEqual(rr.prefix(empty), rr.EMPTY, repr(empty))

    def test_unknown_kind_is_not_an_address(self):
        for bad in ("commmit a545ad2", "коммит a545ad2", "sheet строка 7", "a545ad2",
                    "commit", "commit   ", " a545ad2"):
            self.assertIsNone(rr.parse(bad), bad)
            self.assertEqual(rr.norm(bad), rr.EMPTY, bad)

    def test_make_refuses_loudly_it_never_returns_a_silent_empty(self):
        """Тихая потеря адреса — тот самый класс, ради которого поле заводится."""
        with self.assertRaises(ValueError):
            rr.make("узел", "мозг")
        with self.assertRaises(ValueError):
            rr.make("service", "pc_orchestrator PID 1")     # снятое имя пятого вида
        with self.assertRaises(ValueError):
            rr.make("commit", "   ")
        with self.assertRaises(ValueError):
            rr.make("commit", "a" * (rr.POINTER_MAX + 1))
        for ch in rr.BAD_CHARS:
            with self.assertRaises(ValueError):
                rr.make("file", "docs/x%sy.md" % ch)

    def test_pointer_ceiling_is_a_boundary_not_a_slice(self):
        ok = rr.make("file", "d" * rr.POINTER_MAX)
        self.assertEqual(rr.parse(ok)[1], "d" * rr.POINTER_MAX)
        self.assertIsNone(rr.parse("file " + "d" * (rr.POINTER_MAX + 1)))


# ═════════════════════════ ВИНДОВЫЙ ПУТЬ — ГЛАВНЫЙ СЛУЧАЙ КАНОНА ═════════════════════════

class TestWindowsPathPointer(unittest.TestCase):
    """Указатель с ДВОЕТОЧИЕМ ДИСКА проходит все четыре двери поля дословно: значение,
    маркер, текст шага, ряд очереди. Красный тест здесь = разделитель уехал обратно."""

    PATHS = (
        WIN_PATH,
        r"D:\turbobaby-bot\result_ref.py",
        r"D:\turbobaby-bot\_scratch_refcanon_pc_0816\probe_before.py",
        r"C:\Program Files\что-то\с пробелом.txt",     # пробелы В УКАЗАТЕЛЕ тоже законны
    )

    def test_value_roundtrip_is_verbatim(self):
        for path in self.PATHS:
            val = rr.make("file", path)
            self.assertEqual(val, "file " + path)
            self.assertEqual(rr.parse(val), ("file", path), path)
            self.assertEqual(rr.norm(val), val, path)

    def test_the_kind_never_swallows_the_drive_letter(self):
        """Именно это ломала снятая форма: у читателя «по последнему двоеточию» вид
        получался `file:D`, а указатель — обрубок. Пробел такого исхода не допускает."""
        for path in self.PATHS:
            kind, pointer = rr.parse(rr.make("file", path))
            self.assertEqual(kind, "file", path)
            self.assertNotIn(":", kind, path)
            self.assertEqual(pointer, path, path)
            self.assertEqual(pointer.count(":"), path.count(":"), "двоеточие пути потерялось")

    def test_marker_and_read_back_from_a_live_step_text(self):
        val = rr.make("file", WIN_PATH)
        self.assertEqual(rr.marker(val), "[result_ref: file " + WIN_PATH + "]")
        text = "[шаг 2/5 родитель 431] " + rr.prefix(val) + "написать артефакт по пути"
        self.assertEqual(rr.read(text), val)
        self.assertEqual(rr.parse(rr.read(text)), ("file", WIN_PATH))
        self.assertTrue(text.endswith("написать артефакт по пути"), "адрес откусил хвост ТЗ")

    def test_read_back_from_a_queue_row(self):
        row = {"id": 431, "status": "new", "lane": "pc",
               "task_text": "[шаг 1/2 родитель 430] " + rr.prefix(rr.make("file", WIN_PATH))
                            + "шаг"}
        self.assertEqual(rr.read_item(row), "file " + WIN_PATH)

    def test_the_snapped_colon_form_of_the_same_address_is_not_an_address(self):
        """Совместимости со снятой формой нет по замеру (0 писателей, 0 строк очереди)."""
        self.assertIsNone(rr.parse("file:" + WIN_PATH))
        self.assertEqual(rr.norm("file:" + WIN_PATH), rr.EMPTY)


# ═════════════════════════ СНЯТАЯ ФОРМА НЕ ПРИНИМАЕТСЯ ═══════════════════════════════════

class TestOldFormIsGoneOnPurpose(unittest.TestCase):
    """Замер 16.08 перед правкой: писавших адрес вызовов 0, строк очереди с адресом 0 —
    значит принимать старую форму НЕ НУЖНО. Отказ ТИХИЙ и fail-safe: «адрес не назван»,
    шаг от этого не меняется ни на байт (`_loc_release` ещё и пишет warn в лог демона)."""

    def test_old_values_are_not_addresses(self):
        for old in ("commit:a545ad2", "file:docs/a.md", "row:db:table:17",
                    "brain:queue_state_pc", "service:pc_orchestrator PID 1"):
            self.assertIsNone(rr.parse(old), old)
            self.assertEqual(rr.norm(old), rr.EMPTY, old)
            self.assertEqual(rr.marker(old), rr.EMPTY, old)
            self.assertEqual(rr.prefix(old), rr.EMPTY, old)

    def test_old_marker_in_text_is_not_read(self):
        for text in ("[шаг 1/2 родитель 7] [result_ref commit:a545ad2] шаг",
                     "[шаг 1/2 родитель 7] [result_ref service:demon PID 1] шаг",
                     "[result_ref: commit:a545ad2] мешанина форм"):
            self.assertEqual(rr.read(text), rr.EMPTY, text)

    def test_a_row_carrying_the_old_form_reads_as_not_named(self):
        row = {"id": 1, "task_text": "[шаг 1/2 родитель 7] [result_ref brain:узел] шаг"}
        self.assertEqual(rr.read_item(row), rr.EMPTY)
        row2 = {"id": 1, "task_text": "[шаг 1/2 родитель 7] шаг", "result_ref": "commit:aaa1111"}
        self.assertEqual(rr.read_item(row2), rr.EMPTY)


# ═════════════════════════ МАРКЕР В ТЕКСТЕ ЗАДАЧИ ════════════════════════════════════════

class TestMarkerInTaskText(unittest.TestCase):

    def test_marker_and_prefix_shapes(self):
        self.assertEqual(rr.marker("commit a545ad2"), "[result_ref: commit a545ad2]")
        self.assertEqual(rr.prefix("commit a545ad2"), "[result_ref: commit a545ad2] ")

    def test_read_from_a_live_shaped_step_text(self):
        """Формат — живой: маркер шага цепи впереди, адрес сразу за ним (см. _loc_release)."""
        text = ("[шаг 2/5 родитель 431] [result_ref: commit a545ad2] "
                "закоммитить правку и показать хеш в отчёте")
        self.assertEqual(rr.read(text), "commit a545ad2")

    def test_read_survives_the_correction_marker_between_them(self):
        text = ("[шаг 3/4 родитель 431] [коррекция плана 1] [result_ref: file docs/a.md] "
                "написать артефакт")
        self.assertEqual(rr.read(text), "file docs/a.md")

    def test_whitespace_after_the_key_colon_is_a_liberty_not_a_meaning(self):
        self.assertEqual(rr.read("[result_ref:commit a545ad2] шаг"), "commit a545ad2")
        self.assertEqual(rr.read("[result_ref:   commit   a545ad2] шаг"), "commit a545ad2")

    def test_no_marker_means_not_named(self):
        for text in ("[шаг 1/2 родитель 7] обычный шаг без адреса", "", None, 17,
                     "текст со словом result_ref без скобок"):
            self.assertEqual(rr.read(text), rr.EMPTY, repr(text))

    def test_broken_or_foreign_marker_does_not_mute_the_real_one(self):
        text = ("[result_ref: мусор] [result_ref: sheet 7] "
                "[result_ref: brain queue_state_pc] хвост")
        self.assertEqual(rr.read(text), "brain queue_state_pc")

    def test_first_valid_marker_wins(self):
        text = "[result_ref: commit aaa1111] середина [result_ref: commit bbb2222]"
        self.assertEqual(rr.read(text), "commit aaa1111")

    def test_marker_cannot_be_smuggled_across_brackets_or_lines(self):
        """Жадный маркер съел бы половину ТЗ — границы скобок и строк держатся регуляркой."""
        self.assertEqual(rr.read("[result_ref: file a.md\nb.md] хвост"), rr.EMPTY)
        self.assertEqual(rr.read("[result_ref: file a[1].md] хвост"), rr.EMPTY)


# ═════════════════════════ ЧТЕНИЕ РЯДА ОЧЕРЕДИ ═══════════════════════════════════════════

class TestReadQueueRow(unittest.TestCase):
    """Ряд — живого формата моста: те же поля, что отдаёт get_pending полосы ПК."""

    def _row(self, **over):
        row = {"id": 431, "status": "new", "lane": "pc", "from": "Filipp-pcloc-dec",
               "created": "2026-08-16T09:00:00.000000+00:00",
               "updated": "2026-08-16T09:00:00.000000+00:00",
               "task_text": "[шаг 2/5 родитель 430] обычный шаг", "result": ""}
        row.update(over)
        return row

    def test_row_without_address_reads_empty(self):
        self.assertEqual(rr.read_item(self._row()), rr.EMPTY)

    def test_row_with_marker_reads_it(self):
        row = self._row(task_text="[шаг 2/5 родитель 430] [result_ref: row bookings:1278] шаг")
        self.assertEqual(rr.read_item(row), "row bookings:1278")

    def test_real_column_outranks_the_marker(self):
        """День, когда мост заведёт десятую колонку: читатель уже готов, править нечего."""
        row = self._row(task_text="[шаг 2/5 родитель 430] [result_ref: commit aaa1111] шаг",
                        result_ref="commit bbb2222")
        self.assertEqual(rr.read_item(row), "commit bbb2222")

    def test_empty_or_broken_column_falls_back_to_the_marker(self):
        row = self._row(task_text="[шаг 2/5 родитель 430] [result_ref: commit aaa1111] шаг",
                        result_ref="")
        self.assertEqual(rr.read_item(row), "commit aaa1111")
        row2 = self._row(task_text="[шаг 2/5 родитель 430] [result_ref: commit aaa1111] шаг",
                         result_ref="мусор")
        self.assertEqual(rr.read_item(row2), "commit aaa1111")

    def test_not_a_row_at_all(self):
        for junk in (None, "", 17, ["шаг"]):
            self.assertEqual(rr.read_item(junk), rr.EMPTY, repr(junk))


# ═════════════════════════ ИНВАРИАНТ RESULT_REF_PURE ═════════════════════════════════════
# У ПОЛЯ НЕТ РУК. Модуль не смеет проверить адрес (git/файл/мост/база) — иначе поле перестанет
# быть местом и станет судьёй, а это отдельное решение владельца, не побочный эффект правки.

_ALLOWED_IMPORTS = frozenset(("re",))
_FORBIDDEN_CALLS = frozenset(("open", "exec", "eval", "compile", "__import__", "input"))
_FORBIDDEN_ATTR_ROOTS = frozenset((
    "os", "sys", "subprocess", "shutil", "socket", "requests", "urllib", "pathlib", "tempfile",
    "sqlite3", "bridge_http", "pc_orchestrator", "bc", "logging", "time", "git",
))
_FORBIDDEN_NAMES = frozenset((
    "claim_task", "complete_task", "enqueue_task", "task_heartbeat", "approve_task",
    "Popen", "system", "remove", "unlink", "rmtree", "kill", "exists", "isfile", "getmtime",
))


def pure_findings(src):
    """→ список (адрес, чем плохо). Пустой = у поля рук нет. Разбор ast, не подстрока:
    имя в комментарии и докстринге кодом не является."""
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in _ALLOWED_IMPORTS:
                    out.append(("строка %d" % node.lineno,
                                "импорт «%s» вне списка %s — у поля появились руки"
                                % (a.name, sorted(_ALLOWED_IMPORTS))))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in _ALLOWED_IMPORTS:
                out.append(("строка %d" % node.lineno,
                            "импорт из «%s» вне списка — у поля появились руки" % node.module))
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _FORBIDDEN_CALLS:
                out.append(("строка %d" % node.lineno,
                            "вызов «%s» в модуле, который обязан быть чистым" % fn.id))
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                    and fn.value.id in _FORBIDDEN_ATTR_ROOTS:
                out.append(("строка %d" % node.lineno,
                            "обращение к «%s.%s» — поле не смеет проверять адрес"
                            % (fn.value.id, fn.attr)))
            if isinstance(fn, ast.Attribute) and fn.attr in _FORBIDDEN_NAMES:
                out.append(("строка %d" % node.lineno,
                            "вызов «%s»: поле ХРАНИТ адрес, а не судит по нему" % fn.attr))
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            out.append(("строка %d" % node.lineno,
                        "имя «%s» в коде поля: судить по адресу оно не вправе" % node.id))
    return out


class TestResultRefPure(unittest.TestCase):
    """FAIL-CLOSED: файла нет / не парсится → провал, а не тишина."""

    def test_live_module_has_no_hands(self):
        with open(SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(pure_findings(src), [], "живой result_ref.py обзавёлся руками")

    def test_exactly_one_import(self):
        with open(SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(len(imports), 1, "импорт у поля ровно один: re")

    def test_the_invariant_catches_an_injected_violation(self):
        """Инвариант, который не ловит подлог, — это молчание, а не замок. Пять подлогов."""
        cases = [
            ("проверка файла", "import os\ndef ok(p):\n    return os.path.isfile(p)\n"),
            ("проверка коммита", "import subprocess\n"),
            ("поход на мост", "import bridge_http\n"),
            ("мутация очереди", "import re\ndef f(bc):\n    return bc.complete_task(1, 'done')\n"),
            ("чтение диска", "import re\ndef f(p):\n    return open(p).read()\n"),
        ]
        for name, src in cases:
            self.assertTrue(pure_findings(src), "подлог «%s» инвариант не поймал" % name)
        self.assertEqual(pure_findings("import re\nX = re.compile('a')\n"), [],
                         "законный код инвариант флагать не должен")


if __name__ == "__main__":
    unittest.main()
