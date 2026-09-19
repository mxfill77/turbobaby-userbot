# -*- coding: utf-8 -*-
"""
test_lesson_version.py — ВЕРСИЯ БАЗЫ ОБУЧЕНИЯ и ИСТОЧНИК УЖЕ ЛЕЖАЩИХ СТРОК.
Задание Штаба 11.09.2026 (БАЗА versiya-istochnik 1109), пункты 3, 4, 6.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ И ЧЕМ ОТЛИЧАЕТСЯ ОТ СОСЕДЕЙ. `test_lesson_source` держит ХВОСТ ФОРМАТА
(новая строка умеет нести источник) и откат набора; здесь предмет другой и он один: **версия
набора как признак, который нельзя поднять, не совершив события**, и перевод УЖЕ ЛЕЖАЩИХ строк
в формат с источником.

ПОЧЕМУ ВЕРСИЯ — ОТПЕЧАТОК, А НЕ ПОРЯДКОВЫЙ НОМЕР. Третий вопрос к любому признаку: можно ли
поднять его, не совершив события? У порядкового номера ответ «да» — число вписывается руками, и
признак негоден целиком, а не «годен с оговоркой». Поэтому версией назначен ОТПЕЧАТОК ТЕЛА
таблицы: вписать в поле чужое значение можно, но ПОДНЯТЬ им версию нельзя — дверь чтения
сверяет записанное с пересчитанным по таблице и на расхождении ОТКАЗЫВАЕТ. Ровно это и меряет
`TestNegativeVersionWithoutEvent`.

ОБРАТНАЯ СТОРОНА ОБЯЗАТЕЛЬНА (п.6 задания): прибор, который краснеет на всём, ничего не стоит.
`TestHonestChangeStillPasses` проверяет, что честное изменение базы проходит и версию двигает.

БОЕВОГО ФАЙЛА ЗДЕСЬ НЕ КАСАЕТСЯ НИ ОДНА ВЕТКА: каждая песочница — свой временный каталог.
"""

import hashlib
import os
import tempfile
import unittest

import lesson_store as LS

Q = "клиент спросил про цену аренды"
A = "бот ответил невпопад"
WHEN = "2026-09-11T10:00:00Z"


class _Sandbox(unittest.TestCase):
    """Своя таблица во временном каталоге на каждый тест."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_version_test_")
        self.addCleanup(box.cleanup)
        self.box = box.name
        self.store = os.path.join(self.box, "lesson_store.tsv")

    def raw_bytes(self):
        with open(self.store, "rb") as f:
            return f.read()

    def add_active(self, correct, source=None, who="filipp", when=WHEN):
        return LS.add(question=Q, bot_answer=A, correct=correct, why="владелец назвал причину",
                      who=who, when=when, source=source or LS.SOURCE_TRAINER, path=self.store)

    def write_legacy(self, count, why="перенос 05.09", who="владелец",
                     when="2026-09-03T00:00:00Z"):
        """Файл СТАРОГО формата — восемь колонок и восьмиколоночная шапка, ровно как лежит в
        боевой таблице с 06.09. Пишется руками СОЗНАТЕЛЬНО: живого писателя такого формата
        больше нет, и попроси мы его у кода — проверяли бы новый формат под старым именем."""
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(LS.HEADER_BASE_LINE + "\n")
            for n in range(1, count + 1):
                f.write("\t".join((str(n), LS.esc(Q), LS.esc(A), LS.esc("правило %d" % n),
                                   LS.esc(why), LS.esc(who), LS.esc(when),
                                   LS.STATE_ACTIVE)) + "\n")


# =======================================================================================
# П.3 — ВЕРСИЯ ЕСТЬ, И ОНА ОДНА
# =======================================================================================
class TestVersionExists(_Sandbox):

    def test_door_is_one_and_named(self):
        """Дверь чтения версии существует ровно одна и зовётся `version`."""
        self.assertTrue(hasattr(LS, "version"), "двери чтения версии нет вовсе")
        self.assertTrue(callable(LS.version))

    def test_no_table_says_unknown_not_zero(self):
        """Таблицы нет → версия НЕ НАЗВАНА (третий исход), а не «нулевая версия»."""
        got = LS.version(path=self.store)
        self.assertFalse(got.ok)
        self.assertIsNone(got.said)
        self.assertFalse(got.exists)
        self.assertIn("не названа", got.say)

    def test_write_raises_version(self):
        """Запись урока (событие) поднимает версию: до и после — разные значения, и обе сошлись."""
        self.add_active("правило один")
        first = LS.version(path=self.store)
        self.assertTrue(first.ok, first.say)
        self.assertEqual(len(first.said), LS.VERSION_LEN)

        self.add_active("правило два")
        second = LS.version(path=self.store)
        self.assertTrue(second.ok, second.say)
        self.assertNotEqual(first.said, second.said,
                            "версия не сдвинулась на записанном уроке — событие ею не видно")

    def test_version_is_fingerprint_of_the_table(self):
        """Версия — функция СОДЕРЖИМОГО, а не независимое число: пересчёт совпадает дословно."""
        self.add_active("правило один")
        got = LS.version(path=self.store)
        want = hashlib.sha256(self.raw_bytes()).hexdigest()[:LS.VERSION_LEN]
        self.assertEqual(got.said, want)
        self.assertEqual(got.live, want)


# =======================================================================================
# П.6 — ОТРИЦАТЕЛЬНЫЙ ТЕСТ: ПОЛЕ ЗАПОЛНЕНО, А СОБЫТИЯ НЕ БЫЛО
# =======================================================================================
class TestNegativeVersionWithoutEvent(_Sandbox):
    """КРАСНЕЕТ ОТ СВОЕЙ ПОЛОМКИ. Проверено переворотом: если дверь чтения отдаёт записанное
    поле, не сверяя его с таблицей, оба теста ниже зеленеют на вранье."""

    def test_hand_written_version_is_refused(self):
        """Поле версии ВЫГЛЯДИТ заполненным (16 hex, как настоящее), а таблицу никто не трогал.
        Прибор обязан показать ОТКАЗОМ, а не принять значение на слово."""
        self.add_active("правило один")
        honest = LS.version(path=self.store)
        self.assertTrue(honest.ok)

        forged = "0" * LS.VERSION_LEN
        self.assertNotEqual(forged, honest.said)
        with open(LS.version_path(self.store), "w", encoding="utf-8", newline="") as f:
            f.write(forged + "\n")

        got = LS.version(path=self.store)
        self.assertFalse(got.ok, "вписанная руками версия принята за поднятую — признак негоден")
        self.assertEqual(got.said, forged, "дверь обязана НАЗВАТЬ вписанное, а не скрыть его")
        self.assertEqual(got.live, honest.said, "пересчёт обязан остаться прежним: события не было")
        self.assertIn("не отвечает", got.say)

    def test_table_moved_past_the_door_is_refused(self):
        """Обратная сторона того же расхождения: таблицу правили МИМО двери, версия отстала.
        Отказ обязан быть тот же — версия не описывает содержимое."""
        self.add_active("правило один")
        said_before = LS.version(path=self.store).said

        with open(self.store, "a", encoding="utf-8", newline="") as f:
            f.write("\t".join(("2", LS.esc(Q), LS.esc(A), LS.esc("правило мимо двери"),
                               LS.esc("причина"), LS.esc("filipp"), LS.esc(WHEN),
                               LS.STATE_ACTIVE, LS.esc(LS.SOURCE_TRAINER))) + "\n")

        got = LS.version(path=self.store)
        self.assertFalse(got.ok, "правка мимо двери осталась незамеченной")
        self.assertEqual(got.said, said_before)
        self.assertNotEqual(got.live, said_before)


# =======================================================================================
# П.6 — КОНТРОЛЬ ОБРАТНОЙ СТОРОНЫ: ЧЕСТНОЕ ИЗМЕНЕНИЕ ПРОХОДИТ
# =======================================================================================
class TestHonestChangeStillPasses(_Sandbox):

    def test_withdraw_passes_and_raises_version(self):
        """Снятие урока — честное событие: проходит, строк не теряет, версию двигает."""
        n1 = self.add_active("правило один")
        self.add_active("правило два")
        before = LS.version(path=self.store)

        res = LS.withdraw(number=n1, path=self.store, now=0)
        self.assertEqual(tuple(res.marked), (n1,))
        self.assertEqual(res.lines_before, res.lines_after, "снятие потеряло строку")

        after = LS.version(path=self.store)
        self.assertTrue(after.ok, after.say)
        self.assertNotEqual(before.said, after.said, "честное событие версию не подняло")

    def test_promote_passes_and_raises_version(self):
        """Перевод кандидата в действующие — тоже событие, и версия обязана его увидеть."""
        num = LS.add_candidate(question=Q, bot_answer=A, correct="правило кандидат", who="filipp",
                               when=WHEN, source=LS.SOURCE_TRAINER, path=self.store)
        before = LS.version(path=self.store)
        LS.promote(num, why="владелец подтвердил", who="filipp", path=self.store, now=0)
        after = LS.version(path=self.store)
        self.assertTrue(after.ok, after.say)
        self.assertNotEqual(before.said, after.said)


# =======================================================================================
# П.4 — ИСТОЧНИК УЖЕ ЛЕЖАЩИМ СТРОКАМ: ПОИМЁННО, БЕЗ ДОГАДОК
# =======================================================================================
class TestSetSourceOnLegacyRows(_Sandbox):

    def test_named_rows_get_source_and_others_do_not(self):
        """Источник ставится РОВНО названным номерам. Неназванная строка остаётся без источника —
        догадку за неё никто не делает."""
        self.write_legacy(3)
        res = LS.set_source((1, 2), LS.SOURCE_BOOK, path=self.store)

        self.assertEqual(res.stamped, 2)
        self.assertEqual(res.lines_before, res.lines_after, "перевод потерял строку")

        store = LS.load(self.store)
        got = {les.number: les.source for les in store.lessons}
        self.assertEqual(got[1], LS.SOURCE_BOOK)
        self.assertEqual(got[2], LS.SOURCE_BOOK)
        self.assertIs(got[3], LS.SOURCE_UNKNOWN, "неназванной строке источник придуман")
        self.assertEqual(len(store.broken), 0, "перевод сделал строку неразбираемой")

    def test_header_grows_with_the_rows(self):
        """Шапка переезжает вместе со строками: файл остаётся законным для читателя.

        `set_source` растит восьмиколоночную шапку РОВНО до девяти — не до одиннадцати: партию
        и режим он не проставляет ни одной веткой, и шапка не смеет обещать граф, которых в
        строках нет."""
        self.write_legacy(2)
        LS.set_source((1, 2), LS.SOURCE_BOOK, path=self.store)
        with open(self.store, encoding="utf-8", newline="") as f:
            head = f.readline().rstrip("\n").rstrip("\r")
        self.assertEqual(head, LS.HEADER_SRC_LINE)
        self.assertIn(head, LS.HEADERS, "шапка после перевода не из законных")

    def test_unknown_source_is_refused_loudly(self):
        """Чужое значение источника — ГРОМКИЙ отказ через ту же дверь проверки, а не «0 строк»."""
        self.write_legacy(2)
        before = self.raw_bytes()
        with self.assertRaises(LS.LessonRejected):
            LS.set_source((1,), "перенос-книги-с-опечаткой", path=self.store)
        self.assertEqual(self.raw_bytes(), before, "отказ всё-таки тронул файл")

    def test_backup_is_left_before_rewrite(self):
        """Копия `.bak` кладётся ДО перезаписи — тем же замком, что у прочих правок таблицы."""
        self.write_legacy(2)
        before = self.raw_bytes()
        LS.set_source((1, 2), LS.SOURCE_BOOK, path=self.store)
        bak = self.store + LS.BACKUP_SUFFIX
        self.assertTrue(os.path.isfile(bak), "копия .bak перед перезаписью не положена")
        with open(bak, "rb") as f:
            self.assertEqual(f.read(), before, "копия .bak не побайтна")

    def test_set_source_raises_version(self):
        """Перевод девяти строк — событие, и версия обязана его увидеть."""
        self.write_legacy(9)
        LS.set_source(tuple(range(1, 10)), LS.SOURCE_BOOK, path=self.store)
        got = LS.version(path=self.store)
        self.assertTrue(got.ok, got.say)
        self.assertEqual(len(got.said), LS.VERSION_LEN)

    def test_already_stamped_row_is_named_not_restamped(self):
        """Строка, у которой источник уже есть, переставлению не подлежит: её считают отдельно."""
        self.write_legacy(2)
        LS.set_source((1,), LS.SOURCE_BOOK, path=self.store)
        res = LS.set_source((1, 2), LS.SOURCE_BOOK, path=self.store)
        self.assertEqual(res.already, 1)
        self.assertEqual(res.stamped, 1)

    def test_missing_number_is_counted_not_invented(self):
        """Названный номер, которого в таблице нет, попадает в `missing`, а не молча теряется."""
        self.write_legacy(2)
        res = LS.set_source((1, 77), LS.SOURCE_BOOK, path=self.store)
        self.assertEqual(res.stamped, 1)
        self.assertEqual(tuple(res.missing), (77,))


# =======================================================================================
# П.2/П.4 — ПРОИСХОЖДЕНИЕ ДОКАЗЫВАЕТСЯ СОБЫТИЕМ, А НЕ СХОДСТВОМ
# =======================================================================================
class TestProvenanceNeedsBothLiterals(_Sandbox):
    """Живёт ЗДЕСЬ, а не в `test_lesson_migrate_book`, сознательно: предмет тот же, что у всего
    файла, — признак, который нельзя удовлетворить, не совершив события. Там предмет другой:
    перенос книги и его побайтный замок на снимке."""

    def one_literal_row(self, number, why, who):
        with open(self.store, "a", encoding="utf-8", newline="") as f:
            f.write("\t".join((str(number), LS.esc(Q), LS.esc(A), LS.esc("правило %d" % number),
                               LS.esc(why), LS.esc(who), LS.esc("2026-09-03T00:00:00Z"),
                               LS.STATE_ACTIVE)) + "\n")

    def test_one_literal_is_not_enough(self):
        """Совпала причина ИЛИ автор, но не оба — происхождение НЕИЗВЕСТНО, источник не ставится."""
        import lesson_migrate_book as MB
        self.write_legacy(1)                                   # оба литерала — доказана
        self.one_literal_row(2, MB.MIGRATION_WHY, "кто-то другой")
        self.one_literal_row(3, "своя причина", MB.MIGRATION_WHO)

        plan = MB.book_rows(self.store)
        self.assertEqual(plan.total, 3)
        self.assertEqual(tuple(plan.numbers), (1,), "источник уехал к строке чужого происхождения")
        self.assertEqual(tuple(plan.unknown), (2, 3))

    def test_dry_run_writes_nothing(self):
        """Сухой прогон не трогает ни байта — ни таблицы, ни версии."""
        import lesson_migrate_book as MB
        self.write_legacy(2)
        before = self.raw_bytes()
        plan, res = MB.stamp_source(self.store, apply=False)
        self.assertEqual(len(plan.numbers), 2)
        self.assertIsNone(res)
        self.assertEqual(self.raw_bytes(), before)
        self.assertFalse(os.path.isfile(LS.version_path(self.store)))

    def test_apply_stamps_only_the_proven(self):
        """С `--apply` источник получают РОВНО доказанные, и версия поднимается."""
        import lesson_migrate_book as MB
        self.write_legacy(2)
        self.one_literal_row(3, "своя причина", "кто-то другой")

        plan, res = MB.stamp_source(self.store, apply=True)
        self.assertEqual(res.stamped, 2)
        self.assertEqual(res.lines_before, res.lines_after)

        store = LS.load(self.store)
        got = {les.number: les.source for les in store.lessons}
        self.assertEqual(got[1], LS.SOURCE_BOOK)
        self.assertEqual(got[2], LS.SOURCE_BOOK)
        self.assertIs(got[3], LS.SOURCE_UNKNOWN)
        self.assertTrue(LS.version(self.store).ok)


if __name__ == "__main__":
    unittest.main()
