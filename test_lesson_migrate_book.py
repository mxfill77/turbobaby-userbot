# -*- coding: utf-8 -*-
"""
test_lesson_migrate_book.py — регресс переноса плоской книги правил в базу уроков
(`lesson_migrate_book.py`).

ДВА ОТРИЦАТЕЛЬНЫХ ТЕСТА, названные заданием, стоят первыми классами — они и есть предмет:
  (а) `TestRerunNeverDoubles` — повторный запуск переноса НЕ задваивает записи: ни числом строк,
      ни номерами. Ключ повтора — `trainer._norm_rule` + служебная причина, и тест это ловит
      на живом круге «перенесли → перенесли снова», а не на вере в код;
  (б) `TestBookNeverWritten` — плоская книга после переноса не изменилась НИ БАЙТОМ. Проверено
      двумя слоями: sha256 файла до/после живого переноса И разбором ИСХОДНИКА переносчика
      (в нём нет ни `open(..., "w"/"a")` по книге, ни `append_playbook_rule`). Второй слой нужен
      потому, что первый не заметит запись, добавленную завтра в редкую ветку.

Дальше — замки на то, что легко потерять молча:
  • `TestTextGoesVerbatim` — предсмертный взгляд задания: текст правила НЕ причёсан. Голдены —
    ДОСЛОВНЫЕ строки владельца из живой книги, включая опечатки («здароваемся в приветаенном
    автосообщении») и хвост из двух пронумерованных пунктов в одном правиле;
  • `TestSidecarDupsCountedOnce` — совпавшая запись бокового списка не даёт второй строки;
  • `TestEmptyRecordNotMigrated` — запись без содержания не переносится и НАЗЫВАЕТСЯ поимённо;
  • `TestSnapshotGuard` — разошедшийся снимок ОТКАЗЫВАЕТ в переносе, а не подставляет «сегодня»;
  • `TestReadBackNumbers` — чтение назад даёт те же числа, которыми отчитался перенос;
  • `TestReasonIsNeverInvented` — причина одна и служебная, из текста правила не берётся.

Тесты НЕ КАСАЮТСЯ боевой таблицы уроков: у всех свой временный файл (`--path`/`store_path`),
живая книга и живой сайдкар только ЧИТАЮТСЯ, а там, где нужен изменённый вход, лежит своя
фикстура-книга во временном каталоге.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_migrate_book -v
"""

import ast
import hashlib
import os
import shutil
import tempfile
import unittest

import lesson_migrate_book as M
import lesson_store as LS
import suggest
import trainer

HERE = os.path.dirname(os.path.abspath(__file__))

# Дословные строки владельца из ЖИВОЙ книги — голдены «не причесать». Опечатки сохранены
# намеренно: ровно на них ломается «перенос причешет тексты под единый вид».
GOLD_TYPOS = ("здароваемся в приветаенном автосообщении и так второй раз необязательно, это один "
              "момент, а второй момент это то что у нас даты не понятные а мы уже всё учли и т.д., "
              "пока нничено не дублируем особо что мы там учли, а уточняем какие именно даты ведь "
              "уже 23 июля")
GOLD_TWO_ITEMS = ("1. Клиент уже назвал даты (25–30) во втором сообщении, а бот снова спрашивает "
                  "«с какого числа и на какой срок» — не переспрашивай данные, которые клиент "
                  "только что сообщил. 2. Бот не подтвердил итоговую цену клиенту, хотя она "
                  "известна (3520) — всегда озвучивай клиенту рассчитанную стоимость, а не только "
                  "помечай её служебным тегом.")
GOLD_SHORT = "2 раза вопрос про даты в одном сообщении не пишем"


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class _Sandbox(unittest.TestCase):
    """Общая песочница: своя копия книги, свой снимок под неё, свой сайдкар, своя таблица.

    Копия книги снимается с ЖИВОЙ (`suggest.PLAYBOOK_FILE`) побайтно — вход обязан повторять
    живой формат, включая CRLF и дата-префиксы буллетов."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self.book = os.path.join(d, "playbook.md")
        shutil.copyfile(suggest.PLAYBOOK_FILE, self.book)
        self.snap_dir = os.path.join(d, "lesson_base")
        os.makedirs(self.snap_dir)
        self.snap = os.path.join(self.snap_dir, "playbook-2026-09-03.md")
        shutil.copyfile(self.book, self.snap)
        self.side = os.path.join(d, "trainer_rules.json")
        shutil.copyfile(trainer.TRAINER_RULES_FILE, self.side)
        self.store = os.path.join(d, "lesson_store.tsv")
        self.addCleanup(self._tmp.cleanup)

    def run_migrate(self, apply=True):
        return M.migrate(book_path=self.book, side_path=self.side, snap_dir=self.snap_dir,
                         store_path=self.store, apply=apply)

    def rows(self):
        return LS.load(self.store).lessons


# ---------------------------------------------------------------------------------------
# (а) ОТРИЦАТЕЛЬНЫЙ ТЕСТ: повтор не задваивает
# ---------------------------------------------------------------------------------------
class TestRerunNeverDoubles(_Sandbox):

    def test_second_run_adds_nothing(self):
        first = self.run_migrate()
        self.assertTrue(first.ok, first.say)
        self.assertEqual(first.added, 9)
        self.assertEqual(first.skipped, 0)
        after_first = [(l.number, l.correct) for l in self.rows()]

        second = self.run_migrate()
        self.assertTrue(second.ok, second.say)
        self.assertEqual(second.added, 0, "повтор дописал строки — перенос не идемпотентен")
        self.assertEqual(second.skipped, 9)
        self.assertEqual([(l.number, l.correct) for l in self.rows()], after_first,
                         "после повтора таблица изменилась — номера или тексты разъехались")

    def test_third_run_too(self):
        """Не «второй раз», а «сколько угодно раз»: идемпотентность не бывает одноразовой."""
        self.run_migrate()
        self.run_migrate()
        self.run_migrate()
        self.assertEqual(len(self.rows()), 9)
        self.assertEqual(len({l.number for l in self.rows()}), 9, "номера задвоились")

    def test_rerun_key_survives_hand_edited_spacing(self):
        """Ключ повтора — нормализованный текст, а не байты: лишний пробел в таблице (её правят
        руками) не должен породить второй экземпляр правила."""
        self.run_migrate()
        raw = _read(self.store).replace(GOLD_SHORT, GOLD_SHORT + "   ")
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(raw)
        again = self.run_migrate()
        self.assertEqual(again.added, 0)
        self.assertEqual(len(self.rows()), 9)


# ---------------------------------------------------------------------------------------
# (б) ОТРИЦАТЕЛЬНЫЙ ТЕСТ: книга не изменилась ни байтом
# ---------------------------------------------------------------------------------------
class TestBookNeverWritten(_Sandbox):

    def test_book_bytes_identical_after_migration(self):
        before = _sha(self.book)
        size_before = os.path.getsize(self.book)
        self.run_migrate()
        self.run_migrate()
        self.assertEqual(_sha(self.book), before, "книга изменилась после переноса")
        self.assertEqual(os.path.getsize(self.book), size_before)

    def test_sidecar_bytes_identical_after_migration(self):
        """Боковой список — тоже снимок на своём месте: перенос его не чистит и не сжимает."""
        before = _sha(self.side)
        self.run_migrate()
        self.assertEqual(_sha(self.side), before, "сайдкар изменился после переноса")

    def test_source_has_no_writing_branch_at_all(self):
        """ВТОРОЙ СЛОЙ, по исходнику: в переносчике нет ни одной пишущей ветки по книге.

        Проверяется не грепом по строке, а разбором AST: любой `open(...)` со вторым аргументом,
        отличным от чтения, — красный. Первый слой (sha) не заметит запись, которую завтра
        добавят в редкую ветку и не пройдут тестом."""
        tree = ast.parse(_read(os.path.join(HERE, "lesson_migrate_book.py")))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                mode = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if mode is not None and not str(mode).startswith("r"):
                    bad.append((node.lineno, mode))
        self.assertEqual(bad, [], "переносчик открывает файл на запись: %s" % bad)
        # Запретные имена ищутся В КОДЕ, а не в тексте файла: шапка модуля НАЗЫВАЕТ
        # `append_playbook_rule` — ровно чтобы сказать, что его здесь нет. Греп по байтам счёл бы
        # объяснение нарушением, и замок пришлось бы снять; AST различает вызов и прозу.
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                called.add(node.attr)
            elif isinstance(node, ast.Name):
                called.add(node.id)
        for forbidden in ("append_playbook_rule", "remove_playbook_rule", "replace_playbook_rule",
                          "remove", "unlink", "truncate", "mark_source", "unmark_source"):
            self.assertNotIn(forbidden, called,
                             "в переносчике появился %s — он трогает книгу или сайдкар" % forbidden)


# ---------------------------------------------------------------------------------------
# Предсмертный взгляд задания: текст владельца НЕ причёсан
# ---------------------------------------------------------------------------------------
class TestTextGoesVerbatim(_Sandbox):

    def test_typos_survive(self):
        self.run_migrate()
        got = [l.correct for l in self.rows()]
        self.assertIn(GOLD_TYPOS, got, "правило с опечатками владельца причёсано или потеряно")

    def test_two_numbered_items_stay_one_rule(self):
        """Правило из двух пронумерованных пунктов НЕ разрезается на два урока."""
        self.run_migrate()
        got = [l.correct for l in self.rows()]
        self.assertIn(GOLD_TWO_ITEMS, got)
        self.assertEqual(len(self.rows()), 9)

    def test_every_rule_matches_the_book_char_for_char(self):
        """Все девять — посимвольно те же тела буллетов, что отдаёт живой парсер книги."""
        self.run_migrate()
        book_rules = [r["rule"] for r in suggest.list_playbook_rules(_read(self.book))]
        self.assertEqual([l.correct for l in self.rows()], book_rules)

    def test_scrubbing_did_not_touch_these_texts(self):
        """Чистка персонального в хранилище неотключаема — тест НЕ отключает её, а МЕРЯЕТ: на
        этих девяти текстах она не сработала ни разу. Расхождение здесь — это новость, а не
        поломка теста: значит детектор начал считать что-то в правилах персональным."""
        for r in suggest.list_playbook_rules(_read(self.book)):
            clean = LS.scrub_lesson("q", "b", r["rule"], M.MIGRATION_WHY)
            self.assertEqual(clean.correct, r["rule"],
                             "чистка изменила текст правила #%d: %s" % (r["n"], clean.hits))


# ---------------------------------------------------------------------------------------
# Боковой список: дубли и запись без содержания
# ---------------------------------------------------------------------------------------
class TestSidecarDupsCountedOnce(_Sandbox):

    def test_five_dups_named_and_not_duplicated(self):
        res = self.run_migrate()
        self.assertEqual(len(res.dups), 5, "число дублей бокового списка разошлось")
        self.assertEqual(len(self.rows()), 9, "дубль сайдкара дал вторую строку правилу")
        for key, n in res.dups:
            self.assertIsNotNone(n)
            self.assertEqual(trainer._norm_rule(key),
                             trainer._norm_rule(self.rows()[n - 1].correct))

    def test_dup_key_is_the_sidecar_own_key(self):
        """Совпадение ищется ТЕМ ЖЕ ключом, которым сайдкар ищет свою пометку — иначе «дубль»
        значил бы одно у переносчика и другое у тренажёра."""
        plan = M.build_plan(M.read_book(self.book), M.read_side(self.side))
        for key, _n in plan.dups:
            self.assertEqual(trainer.rule_source(key, path=self.side), trainer.TRAINER_SOURCE)


class TestEmptyRecordNotMigrated(_Sandbox):

    def test_orphan_is_named_and_never_written(self):
        res = self.run_migrate()
        self.assertEqual(list(res.orphans), ["уже было"])
        self.assertNotIn("уже было", [l.correct for l in self.rows()])

    def test_orphan_criterion_is_the_book_not_a_hardcoded_string(self):
        """Критерий «без содержания» — «за записью нет правила книги», а не список слов в коде.

        Тест это и доказывает: как только правило появляется в книге, та же запись перестаёт
        быть сиротой. Обратная сторона названа честно в шапке модуля — удалённое из книги
        правило с оставшейся пометкой попадёт в сироты, потому отчёт и печатает их целиком."""
        self.assertNotIn("уже было", _read(os.path.join(HERE, "lesson_migrate_book.py")))
        text = _read(self.book).rstrip("\r\n")
        text += "\r\n- уже было\r\n"
        with open(self.book, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        shutil.copyfile(self.book, self.snap)
        plan = M.build_plan(M.read_book(self.book), M.read_side(self.side))
        self.assertEqual(plan.orphans, [], "запись осталась сиротой, хотя правило есть в книге")


# ---------------------------------------------------------------------------------------
# Замок даты: снимок обязан совпадать с книгой
# ---------------------------------------------------------------------------------------
class TestSnapshotGuard(_Sandbox):

    def test_diverged_snapshot_refuses_migration(self):
        with open(self.snap, "a", encoding="utf-8") as f:
            f.write("- лишняя строка снимка\n")
        res = self.run_migrate()
        self.assertFalse(res.ok)
        self.assertEqual(res.added, 0)
        self.assertFalse(os.path.exists(self.store), "при отказе в таблицу что-то записалось")
        self.assertIn("разошёлся", res.say)

    def test_stamp_comes_from_the_snapshot_name(self):
        res = self.run_migrate()
        self.assertEqual(res.stamp, "2026-09-03T00:00:00Z")
        self.assertEqual({l.when for l in self.rows()}, {"2026-09-03T00:00:00Z"})
        self.assertEqual({LS.day_of(l.when) for l in self.rows()}, {"2026-09-03"})

    def test_missing_snapshot_refuses_too(self):
        os.remove(self.snap)
        res = self.run_migrate()
        self.assertFalse(res.ok)
        self.assertIn("снимка книги нет", res.say)


# ---------------------------------------------------------------------------------------
# Чтение назад и служебные поля
# ---------------------------------------------------------------------------------------
class TestReadBackNumbers(_Sandbox):

    def test_numbers_agree_with_what_was_written(self):
        res = self.run_migrate()
        rb = M.read_back(self.store)
        self.assertEqual(rb.total, res.added)
        self.assertEqual(rb.active_, res.added, "перенесённое не действует — база врёт о боте")
        self.assertEqual(rb.with_who, res.added)
        self.assertEqual(rb.with_why, res.added)
        self.assertEqual(rb.migrated, res.added)

    def test_numbers_are_unique_and_dense(self):
        self.run_migrate()
        self.assertEqual(sorted(l.number for l in self.rows()), list(range(1, 10)))

    def test_withdraw_by_day_takes_the_whole_migration_back(self):
        """Ради чего затея: перенесённое правило теперь ОТКАТЫВАЕТСЯ разрезом, а строка остаётся."""
        self.run_migrate()
        res = LS.withdraw(day="2026-09-03", path=self.store)
        self.assertEqual(len(res.marked), 9)          # `marked` — НОМЕРА снятых, не их число
        self.assertEqual(res.lines_before, res.lines_after, "снятие удалило строки")
        self.assertEqual(len(LS.active(self.rows())), 0)
        self.assertEqual(len(self.rows()), 9)

    def test_withdraw_by_author_works_too(self):
        self.run_migrate()
        res = LS.withdraw(who=M.MIGRATION_WHO, path=self.store)
        self.assertEqual(len(res.marked), 9)
        self.assertEqual(len(self.rows()), 9)


class TestReasonIsNeverInvented(_Sandbox):

    def test_reason_is_one_service_literal_for_all(self):
        self.run_migrate()
        self.assertEqual({l.why for l in self.rows()}, {M.MIGRATION_WHY})

    def test_reason_is_not_taken_from_the_rule_text(self):
        self.run_migrate()
        for les in self.rows():
            self.assertNotIn(les.why, les.correct,
                             "причина взята из текста правила — её нельзя отличить от названной")

    def test_author_is_the_owner_for_all(self):
        self.run_migrate()
        self.assertEqual({l.who for l in self.rows()}, {M.MIGRATION_WHO})

    def test_two_missing_fields_are_marked_not_faked(self):
        """Вопрос клиента и ответ бота источника не имеют — в таблице стои́т ЯВНАЯ отметка."""
        self.run_migrate()
        for les in self.rows():
            self.assertEqual(les.question, M.NO_SOURCE_Q)
            self.assertEqual(les.bot_answer, M.NO_SOURCE_A)
            self.assertIn("не сохранён", les.question)
            self.assertIn("не сохранён", les.bot_answer)


class TestDryRunWritesNothing(_Sandbox):

    def test_dry_run_leaves_no_table(self):
        res = self.run_migrate(apply=False)
        self.assertTrue(res.ok)
        self.assertEqual(res.added, 9, "сухой прогон обязан показать ПЛАН, а не ноль")
        self.assertFalse(os.path.exists(self.store), "сухой прогон завёл таблицу")

    def test_dry_run_then_apply_gives_the_planned_number(self):
        planned = self.run_migrate(apply=False).added
        self.assertEqual(self.run_migrate(apply=True).added, planned)


if __name__ == "__main__":
    unittest.main()
