# -*- coding: utf-8 -*-
"""
test_lesson_source.py — ИСТОЧНИК НАБОРА У УРОКА: хвост формата, четвёртый разрез, откат набора.
Задание Штаба 09.09.2026 (УРОК-ИСТОЧНИК-НАБОРА-РАЗРЕЗ-ОТКАТ-0909), пункты 1–6.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ, И ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ УЖЕ ЛЕЖАЩИХ ТЕСТОВ. `test_lesson_store` держит
формат и снятие тремя прежними разрезами, `test_lesson_promote` — перевод и откат по номеру.
Здесь предмет один: НАБОР. То есть вопрос «откуда эта строка взялась и можно ли вынуть обратно
всё, что приехало вместе с ней», на который до 09.09 ответить было нечем.

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА (п.5 задания) названы прямо: `TestNegative1…`, `…2…`, `…3…`. Каждый
краснеет от СВОЕЙ поломки, и это проверено переворотом: см. комментарий в каждом.

ПОБАЙТНЫЙ ЗАМЕР (п.6) живёт в `TestBatchRollbackIsByteExact` и меряет ДВЕ вещи, потому что они
разные, и смешивать их было бы враньём:
  • файл таблицы после снятия набора НЕ равен файлу до его заливки — и не должен: строки не
    удаляются никогда, у снятого набора остаётся состояние `снят(источник;…)`. Здесь меряется
    не равенство, а РОВНО КАКИЕ байты разошлись: ни один байт чужой строки не тронут;
  • то, ЧТО ВИДИТ БОТ (действующие строки), после снятия набора возвращается к прежнему
    состоянию ПОБАЙТНО — тем же sha256, что был до заливки. Это и есть «откат набора вернул
    базу в то состояние, которое было до его включения» в единственном смысле, в котором это
    утверждение может быть правдой при запрете на удаление.

БОЕВОГО ФАЙЛА ЗДЕСЬ НЕ КАСАЕТСЯ НИ ОДНА ВЕТКА: каждая песочница — свой временный каталог.
"""

import hashlib
import io
import os
import re
import tempfile
import unittest

import lesson_store as LS

HERE = os.path.dirname(os.path.abspath(__file__))

Q = "Сколько стоит NMAX на неделю?"
A = "[тренажёр] Уточню и вернусь."
WHEN = "2026-09-09T10:00:00Z"


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    with open(path, "rb") as f:
        return sha_bytes(f.read())


def raw_lines(path):
    """Физические строки файла ДОСЛОВНО (с переводом строки), номер = индекс+1."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.readlines()


def active_image(path):
    """БАЙТОВЫЙ ОБРАЗ ТОГО, ЧТО ВИДИТ БОТ: сырые строки действующих уроков, в порядке файла.

    Читается через `LS.active` — единственное место, где написано, что значит «действующий», —
    и берутся СЫРЫЕ байты этих строк, а не пересобранный текст: круг «разобрать → собрать» не
    байт-в-байт на чужой экранированной последовательности, и замер на нём был бы не про байты."""
    if not os.path.isfile(path):
        return b""
    lines = raw_lines(path)
    store = LS.load(path)
    out = []
    for les in LS.active(store.lessons):
        out.append(lines[les.line - 1])
    return "".join(out).encode("utf-8")


class _Sandbox(unittest.TestCase):
    """Своя таблица во временном каталоге на каждый тест."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_source_test_")
        self.addCleanup(box.cleanup)
        self.box = box.name
        self.store = os.path.join(self.box, "lesson_store.tsv")

    def raw(self):
        with open(self.store, encoding="utf-8", newline="") as f:
            return f.read()

    def add_active(self, correct, source, who="filipp", when=WHEN, why="владелец назвал причину"):
        return LS.add(question=Q, bot_answer=A, correct=correct, why=why, who=who,
                      when=when, source=source, path=self.store)

    def add_cand(self, correct, source, who="filipp", when=WHEN):
        return LS.add_candidate(question=Q, bot_answer=A, correct=correct, who=who,
                                when=when, source=source, path=self.store)

    def write_legacy(self, rows, header=None):
        """Файл СТАРОГО формата — восемь колонок и восьмиколоночная шапка, ровно как лежит в
        боевой таблице с 05.09. Пишется здесь руками СОЗНАТЕЛЬНО: живого писателя такого формата
        больше нет, и попроси мы его у кода — проверяли бы новый формат под старым именем."""
        head = LS.HEADER_BASE_LINE if header is None else header
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(head + "\n")
            for row in rows:
                f.write("\t".join(row) + "\n")

    def legacy_row(self, number, correct, who="владелец", when="2026-09-05T00:00:00Z",
                   why="перенос 05.09", state=None):
        return (str(number), LS.esc(Q), LS.esc(A), LS.esc(correct), LS.esc(why),
                LS.esc(who), LS.esc(when), state or LS.STATE_ACTIVE)


# =======================================================================================
# П.1 — ХВОСТ ФОРМАТА: старая строка читается по-прежнему и отвечает «источник неизвестен»
# =======================================================================================
class TestTailIsOptional(_Sandbox):

    def test_new_row_carries_the_source_as_the_ninth_field(self):
        n = self.add_active("не пиши «уточню и вернусь»", LS.SOURCE_TRAINER)
        body = [ln for ln in self.raw().split("\n") if ln.strip()]
        # НОВАЯ строка с 20.09 полной ширины (11), но ИСТОЧНИК стои́т там же, где стоял, —
        # девятым полем. Число названо точное: «не меньше девяти» пропустило бы сдвиг графы.
        self.assertEqual(len(body[1].split("\t")), 11, "хвост партии и режима не дописан")
        self.assertEqual(body[1].split("\t")[LS.IDX_SOURCE], LS.SOURCE_TRAINER)
        got = LS.load(self.store).lessons[0]
        self.assertEqual(got.number, n)
        self.assertEqual(got.source, LS.SOURCE_TRAINER)
        self.assertEqual(LS.say_source(got.source), LS.SOURCE_TRAINER)

    def test_old_row_reads_as_unknown_not_as_empty_and_not_as_a_value(self):
        """ТРЕТИЙ ИСХОД. Строка без хвоста не «пустой источник» и не «тренажёр по умолчанию»."""
        self.write_legacy([self.legacy_row(1, "старое правило книги")])
        store = LS.load(self.store)
        self.assertEqual(len(store.broken), 0, "старая строка попала в НЕРАЗБОР: %s" % (store.broken,))
        self.assertEqual(len(store.lessons), 1)
        les = store.lessons[0]
        self.assertIs(les.source, LS.SOURCE_UNKNOWN)
        self.assertNotEqual(les.source, "", "источник старой строки притворился пустой строкой")
        self.assertEqual(LS.say_source(les.source), LS.SAY_UNKNOWN)
        self.assertEqual(LS.say_source(les.source), "источник неизвестен")

    def test_both_headers_are_known_and_neither_becomes_a_broken_line(self):
        for head in (LS.HEADER_BASE_LINE, LS.HEADER_LINE):
            self.write_legacy([self.legacy_row(1, "правило")], header=head)
            store = LS.load(self.store)
            self.assertEqual(len(store.broken), 0, "шапка %r сочтена битой строкой" % head[:20])
            self.assertEqual(store.reading.seen, 1, head[:20])

    def test_mixed_widths_live_in_one_table(self):
        """Восьми- и девятиколоночные строки в ОДНОМ файле — то, что получится на боевой таблице
        сразу после первой новой записи. Читаются обе, номера не путаются."""
        self.write_legacy([self.legacy_row(1, "старое правило"),
                           self.legacy_row(2, "второе старое")])
        n = self.add_active("новое правило", LS.SOURCE_TRAINER)
        self.assertEqual(n, 3, "новый урок сел на чужой номер")
        store = LS.load(self.store)
        self.assertEqual(len(store.broken), 0)
        self.assertEqual([les.source for les in store.lessons],
                         [LS.SOURCE_UNKNOWN, LS.SOURCE_UNKNOWN, LS.SOURCE_TRAINER])
        self.assertEqual(LS.integrity(self.store).ok, True, LS.integrity(self.store).say)

    def test_empty_or_blank_tail_is_unknown_too(self):
        """Хвост есть, но пуст (файл правили руками) — это тоже «не знаю», а не значение."""
        self.write_legacy([self.legacy_row(1, "правило") + ("",),
                           self.legacy_row(2, "второе") + ("   ",)])
        rows = LS.load(self.store).lessons
        self.assertEqual([les.source for les in rows], [LS.SOURCE_UNKNOWN, LS.SOURCE_UNKNOWN])

    def test_a_foreign_source_value_is_carried_verbatim_not_dropped(self):
        """Списком судится ЗАПИСЬ, а не чтение: чужое значение переносится дословно. Иначе текст,
        положенный руками, исчезал бы из читателя молча."""
        self.write_legacy([self.legacy_row(1, "правило") + (LS.esc("чужой_набор"),)])
        les = LS.load(self.store).lessons[0]
        self.assertEqual(les.source, "чужой_набор")
        self.assertEqual(LS.by_source(LS.load(self.store).lessons, "чужой_набор"), (les,))


# =======================================================================================
# П.5 ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1 — новая запись БЕЗ источника отказывается
# =======================================================================================
class TestNegative1WriteWithoutSourceIsRefused(_Sandbox):
    """КРАСНЕЕТ, если убрать проверку источника из `_add_row`: запись пройдёт и строка ляжет.
    Проверяется не только исход, но и ФАЙЛ — отказ не имеет права оставить полстроки."""

    def test_add_without_source_is_refused_in_words(self):
        with self.assertRaises(LS.LessonRejected) as box:
            LS.add(question=Q, bot_answer=A, correct="правило", why="причина", who="filipp",
                   when=WHEN, path=self.store)
        err = box.exception
        self.assertEqual(err.field, LS.COL_SOURCE, "отказ не назвал поле: %r" % err.field)
        self.assertIn("НЕ записан", err.reason)
        self.assertIn("источник", err.reason.lower())
        for name in LS.SOURCES:                       # владельцу названы ЗНАЧЕНИЯ, а не «см. код»
            self.assertIn(name, err.reason)
        self.assertFalse(os.path.exists(self.store), "отказ всё-таки завёл таблицу")

    def test_add_candidate_without_source_is_refused_too(self):
        with self.assertRaises(LS.LessonRejected) as box:
            LS.add_candidate(question=Q, bot_answer=A, correct="правило", who="filipp",
                             when=WHEN, path=self.store)
        self.assertEqual(box.exception.field, LS.COL_SOURCE)
        self.assertFalse(os.path.exists(self.store))

    def test_empty_source_is_refused_the_same_way(self):
        for empty in ("", "   ", "\t", None):
            with self.assertRaises(LS.LessonRejected) as box:
                LS.add(question=Q, bot_answer=A, correct="правило", why="причина", who="filipp",
                       when=WHEN, source=empty, path=self.store)
            self.assertEqual(box.exception.field, LS.COL_SOURCE, repr(empty))
            self.assertEqual(box.exception.reason, LS.REASON_SOURCE_EMPTY, repr(empty))
        self.assertFalse(os.path.exists(self.store))

    def test_unknown_source_value_is_refused_and_says_what_it_knows(self):
        """Опечатка — не новый набор. «тренажер» без ё завёл бы набор, который снятием по
        источнику не достать никогда: разрез ищет по списку."""
        with self.assertRaises(LS.LessonRejected) as box:
            LS.add(question=Q, bot_answer=A, correct="правило", why="причина", who="filipp",
                   when=WHEN, source="тренажер", path=self.store)
        self.assertEqual(box.exception.field, LS.COL_SOURCE)
        self.assertIn("тренажер", box.exception.reason)
        self.assertIn("SOURCES", box.exception.reason)
        self.assertFalse(os.path.exists(self.store))

    def test_case_and_spaces_around_a_known_source_are_forgiven(self):
        """Строгость нужна к ЗНАЧЕНИЮ, а не к пробелу: «  Тренажёр » — это тот же набор, и в файл
        ложится КАНОНИЧЕСКОЕ написание, иначе разрез ловил бы половину."""
        self.add_active("правило", "  Тренажёр ")
        self.assertEqual(LS.load(self.store).lessons[0].source, LS.SOURCE_TRAINER)

    def test_refusal_does_not_burn_a_number(self):
        self.add_active("первое", LS.SOURCE_TRAINER)
        with self.assertRaises(LS.LessonRejected):
            LS.add(question=Q, bot_answer=A, correct="второе", why="причина", who="filipp",
                   when=WHEN, path=self.store)
        n = self.add_active("второе", LS.SOURCE_TRAINER)
        self.assertEqual(n, 2, "отказ съел номер")

    def test_live_writers_name_their_source(self):
        """ВСЕ ЖИВЫЕ ВЫЗОВЫ ЗАПИСИ (п.3) — не грепом по коду, а ПОВЕДЕНИЕМ: строка, положенная
        боевой дорогой, обязана нести источник. Дорог записи в базу ровно две."""
        import trainer
        # 1) тренажёр: кнопка «🎓 Обучить» / команда «урок:» → кандидат
        old = LS.STORE_PATH
        self.addCleanup(setattr, LS, "STORE_PATH", old)
        LS.STORE_PATH = self.store
        dec = trainer.lesson_candidate("не дублируй модель", who="filipp",
                                       question=Q, bot_answer=A, when=WHEN,
                                       may_write=lambda _u: True)
        self.assertEqual(dec["status"], trainer.STATUS_CANDIDATE, dec["card"])
        les = LS.load(self.store).lessons[0]
        self.assertEqual(les.source, LS.SOURCE_TRAINER)
        self.assertTrue(LS.is_candidate(les))
        # 2) перенос плоской книги: свой источник, и он ДРУГОЙ
        import lesson_migrate_book as MB
        self.assertEqual(MB.MIGRATION_SOURCE, LS.SOURCE_BOOK)
        self.assertNotEqual(MB.MIGRATION_SOURCE, LS.SOURCE_TRAINER)


# =======================================================================================
# П.5 ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2 — снятие набора не задевает ни одной строки чужого источника
# =======================================================================================
class TestNegative2WithdrawBySourceTouchesNobodyElse(_Sandbox):
    """КРАСНЕЕТ, если `_matches` для `CUT_SOURCE` начнёт отвечать True на чужой строке (например
    вернётся прежний хвост `return norm_who(...)` вместо явного `False`)."""

    def build(self):
        """Три набора плюс две строки старого формата — то, что есть на боевой полосе."""
        self.write_legacy([self.legacy_row(1, "старое правило книги"),
                           self.legacy_row(2, "второе старое правило")])
        self.t1 = self.add_active("урок тренажёра один", LS.SOURCE_TRAINER)
        self.b1 = self.add_active("правило из книги", LS.SOURCE_BOOK)
        self.e1 = self.add_active("из экспорта переписки один", LS.SOURCE_EXPORT)
        self.e2 = self.add_cand("из экспорта переписки два", LS.SOURCE_EXPORT)
        self.t2 = self.add_active("урок тренажёра два", LS.SOURCE_TRAINER)

    def test_only_the_named_batch_is_marked(self):
        self.build()
        before = raw_lines(self.store)
        res = LS.withdraw(source=LS.SOURCE_EXPORT, path=self.store, now=0)
        self.assertEqual(res.cut, LS.CUT_SOURCE)
        self.assertEqual(sorted(res.marked), sorted([self.e1, self.e2]),
                         "снятие набора задело не те номера")
        self.assertEqual(res.lines_before, res.lines_after, "снятие потеряло строку")
        after = raw_lines(self.store)
        self.assertEqual(len(before), len(after))
        mine = {self.e1, self.e2}
        store = LS.load(self.store)
        by_line = {les.line: les.number for les in store.lessons}
        for idx, (was, now) in enumerate(zip(before, after), start=1):
            if by_line.get(idx) in mine:
                self.assertNotEqual(was, now, "строка своего набора не снята: %d" % idx)
            else:
                self.assertEqual(was, now, "снятие набора тронуло ЧУЖУЮ строку %d" % idx)

    def test_candidates_of_the_batch_go_down_with_it(self):
        """Кандидат — такая же строка набора: ошибочно залитый экспорт обязан уходить целиком,
        а не оставлять после себя недозревшие строки."""
        self.build()
        LS.withdraw(source=LS.SOURCE_EXPORT, path=self.store, now=0)
        rows = LS.load(self.store).lessons
        for les in rows:
            if les.source == LS.SOURCE_EXPORT:
                gone, cut, _stamp = LS.parse_state(les.state)
                self.assertTrue(gone, "строка набора осталась живой: #%d" % les.number)
                self.assertEqual(cut, LS.CUT_SOURCE)
            else:
                self.assertFalse(LS.parse_state(les.state)[0],
                                 "чужая строка снята: #%d" % les.number)

    def test_rows_without_a_source_are_never_caught_by_any_batch_cut(self):
        """Строка старше формата не принадлежит НИ ОДНОМУ набору. Иначе снятие экспорта унесло бы
        девять живых правил книги, у которых источника нет."""
        self.build()
        for src in LS.SOURCES:
            res = LS.withdraw(source=src, path=self.store, now=0)
            self.assertNotIn(1, res.marked, "старая строка попала в набор %s" % src)
            self.assertNotIn(2, res.marked, "старая строка попала в набор %s" % src)
        rows = LS.load(self.store).lessons
        for les in rows:
            if les.source is LS.SOURCE_UNKNOWN:
                self.assertTrue(LS.is_active(les),
                                "строка без источника снята набором: #%d" % les.number)

    def test_a_typo_in_the_source_refuses_loudly_and_changes_nothing(self):
        """«Снял 0» с кодом успеха — ложный зелёный ровно в той операции, ради которой всё."""
        self.build()
        before = sha_file(self.store)
        with self.assertRaises(ValueError) as box:
            LS.withdraw(source="экспорт", path=self.store, now=0)
        self.assertIn("экспорт", str(box.exception))
        self.assertIn("Ничего не тронуто", str(box.exception))
        self.assertEqual(sha_file(self.store), before, "отказавшее снятие тронуло файл")

    def test_exactly_one_cut_of_four(self):
        self.build()
        with self.assertRaises(ValueError) as box:
            LS.withdraw(number=1, source=LS.SOURCE_TRAINER, path=self.store)
        self.assertIn("четырёх", str(box.exception))
        with self.assertRaises(ValueError):
            LS.withdraw(path=self.store)

    def test_by_source_reader_agrees_with_the_cut(self):
        """Читающий разрез и снимающий обязаны видеть ОДНО И ТО ЖЕ множество: разойдись они, и
        владелец, посмотрев список набора, снял бы не его."""
        self.build()
        for src in LS.SOURCES:
            seen = {les.number for les in LS.by_source(LS.load(self.store).lessons, src)}
            res = LS.withdraw(source=src, path=self.store, now=0)
            self.assertEqual(seen, set(res.marked) | set(res.already), src)


# =======================================================================================
# П.5 ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3 — старая строка без источника не ломает ни чтения, ни трёх разрезов
# =======================================================================================
class TestNegative3OldRowKeepsWorking(_Sandbox):
    """КРАСНЕЕТ, если ширину строки снова начнут сверять с `len(COLUMNS)` вместо `WIDTHS`:
    девять живых строк боевой таблицы уедут в НЕРАЗБОР, а бот останется без правил."""

    def build(self):
        self.write_legacy([
            self.legacy_row(1, "старое правило один", who="владелец", when="2026-09-05T00:00:00Z"),
            self.legacy_row(2, "старое правило два", who="владелец", when="2026-09-05T00:00:00Z"),
            self.legacy_row(3, "старое правило три", who="ivan", when="2026-09-06T00:00:00Z"),
        ])

    def test_reading_is_not_broken(self):
        self.build()
        store = LS.load(self.store)
        self.assertEqual(len(store.lessons), 3)
        self.assertEqual(store.broken, ())
        self.assertEqual((store.reading.seen, store.reading.parsed), (3, 3))
        self.assertFalse(store.reading.blind)
        self.assertTrue(LS.integrity(self.store).ok, LS.integrity(self.store).say)
        self.assertEqual(len(LS.active(store.lessons)), 3)

    def test_cut_by_number_still_works(self):
        self.build()
        res = LS.withdraw(number=2, path=self.store, now=0)
        self.assertEqual(res.marked, (2,))
        self.assertEqual(res.lines_before, res.lines_after)
        rows = LS.load(self.store).lessons
        self.assertEqual([les.number for les in LS.active(rows)], [1, 3])
        self.assertEqual(LS.parse_state(rows[1].state)[1], LS.CUT_ONE)

    def test_cut_by_day_still_works(self):
        self.build()
        res = LS.withdraw(day="2026-09-05", path=self.store, now=0)
        self.assertEqual(sorted(res.marked), [1, 2])
        self.assertEqual(res.lines_before, res.lines_after)

    def test_cut_by_author_still_works(self):
        self.build()
        res = LS.withdraw(who="@Владелец", path=self.store, now=0)
        self.assertEqual(sorted(res.marked), [1, 2])
        self.assertEqual(res.lines_before, res.lines_after)

    def test_widths_of_untouched_rows_do_not_grow(self):
        """Снятие НЕ дописывает хвост чужой строке: формат растёт только у новых записей."""
        self.build()
        LS.withdraw(number=1, path=self.store, now=0)
        for line in raw_lines(self.store)[1:]:
            self.assertEqual(len(line.rstrip("\n").split("\t")), 8,
                             "правка дописала хвост лежащей строке")


class TestOldRowStillPromotableAndRollbackable(_Sandbox):
    """Старая строка остаётся ПОЛНОПРАВНОЙ: её можно перевести в действующие и откатить, и откат
    по-прежнему побайтный. Хвост источника к переводу отношения не имеет — и не должен иметь."""

    def test_promote_then_rollback_returns_the_bytes(self):
        self.write_legacy([self.legacy_row(1, "старый кандидат", state=LS.STATE_CANDIDATE,
                                           why="")])
        before = sha_file(self.store)
        res = LS.promote(1, why="владелец назвал причину", who="filipp", path=self.store)
        self.assertTrue(res.ok, res.reason)
        self.assertNotEqual(sha_file(self.store), before)
        les = LS.load(self.store).lessons[0]
        self.assertTrue(LS.is_active(les))
        self.assertIs(les.source, LS.SOURCE_UNKNOWN, "перевод выдумал источник строке без него")
        back = LS.rollback(1, who="filipp", path=self.store)
        self.assertTrue(back.ok, back.reason)
        self.assertEqual(sha_file(self.store), before,
                         "откат перевода не вернул старую строку побайтно")


# =======================================================================================
# П.6 ПОБАЙТНЫЙ ЗАМЕР: откат набора возвращает базу в то состояние, что было до его включения
# =======================================================================================
class TestBatchRollbackIsByteExact(_Sandbox):
    """ГЛАВНЫЙ ЗАМЕР ЗАХОДА, и он честно разделён надвое (см. шапку файла).

    Сценарий — ровно тот, ради которого источник заведён: в базе живёт своё (правила книги без
    хвоста + уроки тренажёра), приезжает ВНЕШНИЙ ЭКСПОРТ ПЕРЕПИСКИ, оказывается негодным, и его
    вынимают одним движением."""

    def setUp(self):
        _Sandbox.setUp(self)
        # --- состояние ДО заливки набора -------------------------------------------------
        self.write_legacy([self.legacy_row(1, "правило книги один"),
                           self.legacy_row(2, "правило книги два")])
        self.own = [self.add_active("урок тренажёра один", LS.SOURCE_TRAINER),
                    self.add_cand("кандидат тренажёра", LS.SOURCE_TRAINER)]
        self.S0_file = sha_file(self.store)
        self.S0_active = sha_bytes(active_image(self.store))
        self.S0_lines = raw_lines(self.store)
        self.copy0 = os.path.join(self.box, "before_batch.tsv")
        with open(self.copy0, "wb") as dst:
            with open(self.store, "rb") as src:
                dst.write(src.read())

    def fill_batch(self, k=5):
        return [self.add_active("правило из экспорта %d" % i, LS.SOURCE_EXPORT,
                                who="менеджер", when="2026-09-09T12:00:0%dZ" % i)
                for i in range(k)]

    def test_batch_goes_in_and_comes_out_in_one_movement(self):
        got = self.fill_batch()
        self.assertEqual(len(got), 5)
        S1_file = sha_file(self.store)
        S1_lines = raw_lines(self.store)
        self.assertNotEqual(S1_file, self.S0_file, "заливка набора ничего не изменила")
        self.assertNotEqual(sha_bytes(active_image(self.store)), self.S0_active)
        batch_lines = {les.line for les in LS.load(self.store).lessons if les.number in set(got)}
        self.assertEqual(len(batch_lines), len(got))

        res = LS.withdraw(source=LS.SOURCE_EXPORT, path=self.store, now=0)
        self.assertEqual(sorted(res.marked), sorted(got), "снялся не весь набор")
        self.assertEqual(res.lines_before, res.lines_after)

        # (а) ТО, ЧТО ВИДИТ БОТ, вернулось ПОБАЙТНО в состояние до заливки
        self.assertEqual(sha_bytes(active_image(self.store)), self.S0_active,
                         "действующие строки после отката набора не совпали побайтно с теми, "
                         "что были до его включения")

        # (б) ФАЙЛ таблицы к S0 НЕ вернулся, и это названо, а не спрятано: строки не удаляются.
        # Меряется не равенство, а РОВНО КАКИЕ байты разошлись — колонка за колонкой.
        self.assertNotEqual(sha_file(self.store), self.S0_file)
        after = raw_lines(self.store)
        self.assertEqual(len(after), len(S1_lines), "снятие набора потеряло или добавило строку")
        for lineno, (was, now) in enumerate(zip(S1_lines, after), start=1):
            was_f, now_f = was.rstrip("\n").split("\t"), now.rstrip("\n").split("\t")
            self.assertEqual(len(was_f), len(now_f), "строка %d поменяла ширину" % lineno)
            for col, (a, b) in enumerate(zip(was_f, now_f)):
                if col == LS.IDX_STATE and lineno in batch_lines:
                    self.assertNotEqual(a, b, "строка набора %d не снята" % lineno)
                    self.assertEqual(b, LS.withdrawn_state(LS.CUT_SOURCE, LS.now_stamp(0)))
                    continue
                name = LS.COLUMNS[col] if col < len(LS.COLUMNS) else str(col)
                self.assertEqual(a, b, "снятие набора тронуло строку %d, колонку «%s»"
                                 % (lineno, name))

        # (в) САМОЕ СИЛЬНОЕ УТВЕРЖДЕНИЕ, какое здесь может быть правдой: файл БЕЗ строк набора
        # побайтно равен файлу до его заливки. То есть набор въехал и выехал, не оставив следа
        # НИ В ОДНОМ чужом байте — а свои строки остались, потому что их не удаляют никогда.
        without_batch = [ln for no, ln in enumerate(after, start=1) if no not in batch_lines]
        self.assertEqual(without_batch, self.S0_lines,
                         "база без строк набора не совпала побайтно с той, что была до заливки")
        self.assertEqual(sha_bytes("".join(without_batch).encode("utf-8")), self.S0_file,
                         "sha256 базы без набора разошёлся с sha256 базы до заливки")

    def test_the_batch_can_be_taken_out_even_if_someone_wrote_in_between(self):
        """Между заливкой и откатом жизнь не останавливается: свой урок, записанный ПОСЛЕ набора,
        обязан пережить снятие набора целым и остаться действующим."""
        self.fill_batch(3)
        mine = self.add_active("свой урок уже после экспорта", LS.SOURCE_TRAINER,
                               when="2026-09-09T13:00:00Z")
        target = sha_bytes(active_image(self.store))    # каким бот видит мир СЕЙЧАС
        LS.withdraw(source=LS.SOURCE_EXPORT, path=self.store, now=0)
        rows = LS.load(self.store).lessons
        alive = {les.number for les in LS.active(rows)}
        self.assertIn(mine, alive, "снятие набора унесло чужой урок")
        self.assertNotEqual(sha_bytes(active_image(self.store)), target)
        # действующее после снятия = действующее до набора ПЛЮС свой поздний урок, побайтно
        expect = active_image(self.copy0) + raw_lines(self.store)[
            [les.line for les in rows if les.number == mine][0] - 1].encode("utf-8")
        self.assertEqual(sha_bytes(active_image(self.store)), sha_bytes(expect))

    def test_nothing_is_ever_deleted_by_the_batch_cut(self):
        got = self.fill_batch(4)
        n_before = len(LS.load(self.store).lessons)
        LS.withdraw(source=LS.SOURCE_EXPORT, path=self.store, now=0)
        rows = LS.load(self.store).lessons
        self.assertEqual(len(rows), n_before, "снятие набора потеряло уроки")
        self.assertEqual(sorted(les.number for les in rows if les.source == LS.SOURCE_EXPORT),
                         sorted(got))
        self.assertTrue(os.path.exists(self.store + LS.BACKUP_SUFFIX),
                        "копия .bak перед перезаписью не положена")

    def test_the_second_withdrawal_of_the_same_batch_says_already(self):
        got = self.fill_batch(3)
        LS.withdraw(source=LS.SOURCE_EXPORT, path=self.store, now=0)
        again = LS.withdraw(source=LS.SOURCE_EXPORT, path=self.store, now=0)
        self.assertEqual(again.marked, ())
        self.assertEqual(sorted(again.already), sorted(got))
        self.assertEqual(again.lines_before, again.lines_after)


# =======================================================================================
# П.4 — ЗНАЧЕНИЯ ИСТОЧНИКА ЖИВУТ ОДНИМ СПИСКОМ, РАЗРЕЗЫ — ОДНИМ КОРТЕЖЕМ
# =======================================================================================
class TestOnePlaceOnly(unittest.TestCase):
    """Сторож на исходнике: копия списка рядом — это ровно тот дефект, из-за которого набор,
    заведённый одним файлом, перестаёт находиться другим."""

    def src(self, name):
        with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
            return f.read()

    def test_callers_read_the_constant_instead_of_writing_the_literal(self):
        for name in ("trainer.py", "lesson_migrate_book.py"):
            text = self.src(name)
            for value in LS.SOURCES:
                quoted = ('"%s"' % value, "'%s'" % value)
                for form in quoted:
                    if form in text:
                        # единственное законное совпадение — `TRAINER_SOURCE` сайдкара в
                        # trainer.py: другая сущность, другое назначение (см. `_lesson_source`)
                        self.assertEqual((name, value), ("trainer.py", LS.SOURCE_TRAINER),
                                         "литерал источника %s продублирован в %s" % (form, name))

    def assign_line(self, name, text):
        """Строка ПРИСВОЕНИЯ имени в исходнике. Сверять надо код, а не прозу: комментарий,
        объясняющий старый литерал, не должен ронять сторожа (уже уронил однажды)."""
        for line in text.splitlines():
            if line.startswith(name + " ="):
                return line
        self.fail("присвоения %s в исходнике нет" % name)

    def test_the_withdrawn_regex_is_built_from_cuts(self):
        """Регулярка состояния обязана СОБИРАТЬСЯ из `CUTS`, а не повторять их литералом: иначе
        пятый разрез разойдётся с ней молча, как чуть не разошёлся четвёртый."""
        line = self.assign_line("_RE_WITHDRAWN", self.src("lesson_store.py"))
        self.assertIn("CUTS", line, "регулярка состояния перестала читать CUTS: %s" % line)
        for cut in LS.CUTS:
            self.assertNotIn('"%s"' % cut, line, "литерал разреза вернулся в регулярку")
            self.assertNotIn("'%s'" % cut, line, "литерал разреза вернулся в регулярку")
            state = LS.withdrawn_state(cut, "2026-09-09T10:00:00Z")
            self.assertEqual(LS.parse_state(state)[1], cut, cut)

    def test_no_branch_ever_writes_the_source_column_by_rewrite(self):
        """Хвост дописывается только ДОЗАПИСЬЮ новой строки. Правка по месту графу источника не
        трогает ни одной веткой — иначе `_replace_fields` пришлось бы уметь растить строку, а это
        и есть запрещённое переписывание лежащих строк.

        Ищется РОВНО форма правки — `{… IDX_SOURCE: …}`, словарь колонок для `_rewrite`, — а не
        имя `IDX_SOURCE` где угодно: читателю оно нужно и в чтении хвоста."""
        found = re.findall(r"\{[^{}]*IDX_SOURCE\s*:", self.src("lesson_store.py"))
        self.assertEqual(found, [], "появилась ветка, правящая графу источника по месту: %s" % found)
        # …и поведением: попытка дописать хвост восьмиколоночной строке её НЕ растит
        raw = "\t".join(["1"] + ["x"] * 7)
        self.assertEqual(LS._replace_fields(raw, {LS.IDX_SOURCE: "экспорт_переписки"}), raw)

    def test_widths_are_named_not_computed_at_call_sites(self):
        text = self.src("lesson_store.py")
        self.assertNotIn("len(parts) != len(COLUMNS)", text,
                         "осталась сверка ширины с одной шириной — старая строка уедет в НЕРАЗБОР")
        self.assertNotIn("len(parts) != len(COLUMNS_SRC)", text,
                         "сверка ширины с одной шириной вернулась вторым именем")
        self.assertEqual(LS.WIDTHS, (8, 9, 11))
        # ШИРИНЫ 10 В ФОРМАТЕ НЕТ И НЕ ДОЛЖНО БЫТЬ: хвост партии и режима растёт ПАРОЙ, иначе
        # на полпути заводится строка, про которую нечего решать.
        self.assertNotIn(10, LS.WIDTHS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
