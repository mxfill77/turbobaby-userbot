# -*- coding: utf-8 -*-
"""ПАРТИЯ ЗАЛИВКИ И РЕЖИМ ЗАПИСИ — регресс двух дыр, названных заходом 68e (20.09.2026).

ЧТО ЗДЕСЬ СТОРОЖИТСЯ, ДВУМЯ ФРАЗАМИ.

1. ДВЕ ЗАЛИВКИ ОДНОГО ИСТОЧНИКА СНИМАЮТСЯ ПОРОЗНЬ. До 20.09 ключом снятия был один источник,
   и второй экспорт переписки уехал бы вместе с первым — с честным кодом успеха и без единого
   слова о том, что снято вдвое больше названного. Здесь это проверяется ЧИСЛОМ и вместе с
   КОНТРФАКТОМ: тот же вход БЕЗ номера обязан снять оба, иначе тест зеленеет на пустом месте.

2. РЕЖИМ НЕЛЬЗЯ ПОДНЯТЬ, НЕ СОВЕРШИВ СОБЫТИЯ. Режима нет среди аргументов ни одной двери записи:
   он выводится из источника. Перебор ВСЕХ дорог записи считает, сколько строк легло с режимом
   «обучение» при боевом источнике; ответ обязан быть ноль, и он назван числом, а не словом.

Боевых файлов тест не касается ни одной веткой: у каждого класса своя временная таблица."""

import inspect
import os
import re
import shutil
import tempfile
import time as _real_time
import unittest

import lesson_store as LS


class _Sandbox(unittest.TestCase):
    """Своя таблица на класс. Боевой `lesson_store.tsv` не открывается ни на чтение."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lesson_partiya_")
        self.store = os.path.join(self.dir, "lessons.tsv")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def add(self, text, source, batch=None, state="актив"):
        """Одна строка нужного набора и нужной заливки. Текст синтетический — живой сюда не ходит."""
        if state == "актив":
            return LS.add(text, "ответил не так", "надо так", "потому что так", "@тест",
                          source=source, batch=batch, path=self.store)
        return LS.add_candidate(text, "ответил не так", "надо так", "@тест",
                                source=source, batch=batch, path=self.store)

    def raw_bytes(self):
        with open(self.store, "rb") as f:
            return f.read()

    def body_lines(self):
        """Непустые физические строки файла. Через `with`, а не `open(...).read()`: незакрытый
        дескриптор в тесте — это ResourceWarning в выводе гейта, то есть шум поверх результата."""
        with open(self.store, encoding="utf-8", newline="") as f:
            return [ln for ln in f.read().split("\n") if ln.strip()]

    def actives(self, source=None, batch=LS.BATCH_ANY):
        rows = LS.load(self.store).lessons
        if source is not None:
            rows = LS.by_batch(rows, source, batch)
        return LS.active(rows)


# =======================================================================================
# П.4 — ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПРО ЗАЛИВКУ: снимаем одну, вторая обязана остаться
# =======================================================================================
class TestTwoLotsWithdrawApart(_Sandbox):

    def setUp(self):
        super(TestTwoLotsWithdrawApart, self).setUp()
        # ОДИН источник, ДВА номера: ровно та картина, в которой до 20.09 второй экспорт уезжал
        # вместе с первым. Числа разные (3 и 2) СОЗНАТЕЛЬНО — на равных числах сверка count
        # прошла бы и при снятии не той заливки.
        for i in range(3):
            self.add("первая поставка, строка %d" % i, LS.SOURCE_EXPORT, batch=1)
        for i in range(2):
            self.add("вторая поставка, строка %d" % i, LS.SOURCE_EXPORT, batch=2)

    def test_card_says_how_many_lots_will_go_before_the_move(self):
        """Карточка называет ЗАЛИВКИ числом ДО движения: владелец видит, что ключом «весь набор»
        уедут обе, и видит это ДО снятия, а не после."""
        ok, _reason, whole = LS.batch_card(LS.SOURCE_EXPORT, path=self.store)
        self.assertTrue(ok)
        self.assertEqual(whole.movable, 5, "весь набор — это пять строк")
        self.assertEqual(len(whole.lots), 2, "карточка не показала, что заливок ДВЕ: %s"
                         % (whole.lots,))
        self.assertEqual(dict(whole.lots),
                         {(LS.SOURCE_EXPORT, 1): 3, (LS.SOURCE_EXPORT, 2): 2})

        ok1, _r1, one = LS.batch_card(LS.SOURCE_EXPORT, path=self.store, batch=1)
        self.assertTrue(ok1)
        self.assertEqual(one.movable, 3, "карточка одной заливки посчитала чужие строки")
        self.assertEqual(len(one.lots), 1)

    def test_withdrawing_one_lot_leaves_the_other_alive(self):
        """ГЛАВНОЕ ЧИСЛО ЗАДАНИЯ. Снята заливка №1 (3 строки) — заливка №2 обязана остаться
        действующей ЦЕЛИКОМ (2 строки), и физических строк в файле не убавиться."""
        before_lines = len(self.body_lines())
        res = LS.batch_withdraw(LS.SOURCE_EXPORT, count=3, why="первая поставка оказалась сырой",
                                who="@тест", path=self.store, batch=1)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.count, 3, "снято не три строки")
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT, 1)), 0, "заливка №1 не снята")
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT, 2)), 2,
                         "ЗАЛИВКА №2 УЕХАЛА ВМЕСТЕ С ПЕРВОЙ — ровно тот класс, ради которого "
                         "партия и заводилась")
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT)), 2, "в наборе осталось не два")
        after_lines = len(self.body_lines())
        self.assertEqual(before_lines, after_lines, "снятие удалило строки")

    def test_counterfactual_without_the_lot_the_same_input_takes_both(self):
        """КОНТРФАКТ. Тот же набор, тот же автор, та же причина — но БЕЗ номера заливки: уходят
        ОБЕ поставки, все пять строк. Без этого числа предыдущий тест зеленел бы и на коде, в
        котором партия не работает вовсе."""
        res = LS.batch_withdraw(LS.SOURCE_EXPORT, count=5, why="снимаем набор целиком",
                                who="@тест", path=self.store)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.count, 5, "без номера ушло не пять строк")
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT)), 0)
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT, 2)), 0,
                         "контрфакт не состоялся: заливка №2 пережила снятие всего набора")

    def test_the_cut_is_written_into_the_row_and_differs_from_the_whole_set(self):
        """Разрез виден В СТРОКЕ: снятая заливка помечена `снят(партия;…)`, а снятый набор —
        `снят(источник;…)`. Глазами в таблице эти два движения обязаны различаться."""
        LS.batch_withdraw(LS.SOURCE_EXPORT, count=3, why="сырая поставка", who="@тест",
                          path=self.store, batch=1)
        gone = [les for les in LS.load(self.store).lessons if not LS.is_active(les)]
        self.assertEqual(len(gone), 3)
        for les in gone:
            _g, cut, _s = LS.parse_state(les.state)
            self.assertEqual(cut, LS.CUT_BATCH, "снятие заливки помечено не своим разрезом")

    def test_count_is_checked_against_the_lot_not_the_whole_set(self):
        """Число подтверждения сверяется с ЗАЛИВКОЙ. Назвать пять (размер всего набора), целясь
        в заливку из трёх, — отказ, и ничего не тронуто."""
        before = self.raw_bytes()
        res = LS.batch_withdraw(LS.SOURCE_EXPORT, count=5, why="ошибся числом", who="@тест",
                                path=self.store, batch=1)
        self.assertFalse(res.ok)
        self.assertIn("3", res.reason, "отказ не назвал живое число заливки")
        self.assertEqual(self.raw_bytes(), before, "отказ всё-таки тронул файл")

    def test_each_lot_returns_apart_and_byte_for_byte(self):
        """Возврат тоже адресный: сняли обе заливки порознь — вернулась ИМЕННО названная, и
        файл после снятия+возврата побайтно равен файлу до снятия."""
        start = self.raw_bytes()
        LS.batch_withdraw(LS.SOURCE_EXPORT, count=3, why="сырая первая", who="@тест",
                          path=self.store, batch=1)
        LS.batch_withdraw(LS.SOURCE_EXPORT, count=2, why="сырая вторая", who="@тест",
                          path=self.store, batch=2)
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT)), 0)

        back2 = LS.batch_restore(LS.SOURCE_EXPORT, who="@тест", path=self.store, batch=2)
        self.assertTrue(back2.ok, back2.reason)
        self.assertEqual(back2.count, 2)
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT, 1)), 0,
                         "возврат заливки №2 поднял чужую заливку")
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT, 2)), 2)

        back1 = LS.batch_restore(LS.SOURCE_EXPORT, who="@тест", path=self.store, batch=1)
        self.assertTrue(back1.ok, back1.reason)
        self.assertEqual(self.raw_bytes(), start, "после возврата обеих заливок байты разошлись")

    def test_empty_lot_refuses_loudly_and_names_the_number(self):
        """Заливка, которой нет, — ГРОМКИЙ отказ, а не «снял 0» с кодом успеха (тот же довод,
        что у чужого значения источника)."""
        before = self.raw_bytes()
        res = LS.batch_withdraw(LS.SOURCE_EXPORT, count=1, why="есть ли третья", who="@тест",
                                path=self.store, batch=7)
        self.assertFalse(res.ok)
        self.assertIn("7", res.reason)
        self.assertEqual(self.raw_bytes(), before)

    def test_lot_without_a_source_is_refused(self):
        """Номер заливки без набора ни на что не указывает: `withdraw(day=…, batch=…)` — отказ,
        а не снятие по номеру строки чужих наборов."""
        with self.assertRaises(ValueError):
            LS.withdraw(who="@тест", batch=1, path=self.store)

    def test_garbage_lot_is_refused_and_never_becomes_the_first(self):
        """Мусор в номере — отказ, а не «заливка №1». Подставленная единица увела бы строки
        чужой поставки."""
        for bad in ("0", "-3", "первая", " ", "1.5"):
            res = LS.batch_withdraw(LS.SOURCE_EXPORT, count=3, why="мусор", who="@тест",
                                    path=self.store, batch=bad)
            self.assertFalse(res.ok, "номер %r проехал как настоящий" % bad)
        self.assertEqual(len(self.actives(LS.SOURCE_EXPORT)), 5, "мусорный номер что-то снял")


# =======================================================================================
# П.1 (вторая половина) — СТАРЫМ СТРОКАМ НОМЕР ПРОСТАВЛЯЕТСЯ, ЧУЖИЕ БАЙТЫ НЕ ПЕРЕПИСЫВАЮТСЯ
# =======================================================================================
class TestSetBatchOnLyingRows(_Sandbox):

    def write_src_rows(self, count, source=LS.SOURCE_BOOK):
        """Девятиколоночные строки — формат 09.09–20.09, тот самый, в котором лежит боевая
        таблица. Пишутся РУКАМИ, а не дверью: дверь сегодня кладёт одиннадцать граф."""
        lines = [LS.HEADER_SRC_LINE]
        for i in range(1, count + 1):
            lines.append("\t".join([str(i), "вопрос %d" % i, "ответ бота", "как правильно",
                                    "почему", "@владелец", "2026-09-03T00:00:00Z",
                                    LS.STATE_ACTIVE, source]))
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines) + "\n")

    def test_tail_grows_and_the_old_bytes_are_carried_verbatim(self):
        """Хвост ДОРАСТАЕТ: девять граф становятся одиннадцатью, а первые девять переносятся
        байт в байт. Приём тот же, каким 11.09 проставили источник."""
        self.write_src_rows(3)
        before = self.body_lines()
        res = LS.set_batch((1, 2, 3), 4, path=self.store)
        self.assertEqual(res.stamped, 3)
        self.assertEqual(res.already, 0)
        self.assertEqual(res.no_source, ())
        self.assertEqual(res.missing, ())
        self.assertEqual(res.lines_before, res.lines_after, "перевод формата потерял строку")

        after = self.body_lines()
        self.assertEqual(len(before), len(after))
        for old, new in zip(before, after):
            self.assertEqual(new.split("\t")[:9], old.split("\t"),
                             "перевод переписал уже лежащие байты")
            self.assertEqual(len(new.split("\t")), 11)
        self.assertEqual(after[0], LS.HEADER_LINE, "шапка не переехала вместе со строками")

        rows = LS.load(self.store).lessons
        self.assertEqual([les.batch for les in rows], [4, 4, 4])
        self.assertEqual([les.mode for les in rows], [LS.MODE_TRAIN] * 3,
                         "режим переноса книги выведен не из источника")

    def test_mode_is_computed_from_the_source_even_here(self):
        """И В ПЕРЕВОДЕ ФОРМАТА РЕЖИМ НЕ ПАРАМЕТР. Строки боевого набора получают «бой», строки
        двусмысленного — пустоту; вписать «обучение» этой дверью нечем."""
        self.write_src_rows(2, source=LS.SOURCE_EXPORT)
        res = LS.set_batch((1, 2), 1, path=self.store)
        self.assertEqual(dict(res.modes), {LS.MODE_FIGHT: 2})
        self.assertEqual([les.mode for les in LS.load(self.store).lessons], [LS.MODE_FIGHT] * 2)

        self.write_src_rows(2, source=LS.SOURCE_EXAM)
        res2 = LS.set_batch((1, 2), 1, path=self.store)
        self.assertEqual(dict(res2.modes), {LS.SAY_MODE_UNKNOWN: 2})
        for les in LS.load(self.store).lessons:
            self.assertIs(les.mode, LS.MODE_UNKNOWN, "двусмысленному источнику подставлен режим")

    def test_second_pass_does_not_grow_a_thirteenth_column(self):
        """Повторный перевод — `already`, а не второй хвост. Судится ШИРИНОЙ строки, а не
        значением поля: одиннадцатиколоночная строка с пустой партией хвоста уже несёт."""
        self.write_src_rows(2)
        LS.set_batch((1, 2), 1, path=self.store)
        again = LS.set_batch((1, 2), 2, path=self.store)
        self.assertEqual(again.stamped, 0)
        self.assertEqual(again.already, 2)
        for ln in self.body_lines():
            self.assertEqual(len(ln.split("\t")), 11, "выросла лишняя графа")
        self.assertEqual([les.batch for les in LS.load(self.store).lessons], [1, 1],
                         "повтор переписал уже проставленный номер")

    def test_row_without_a_source_is_named_not_stamped(self):
        """Восьмиколоночная строка (источника нет) партии НЕ получает: режим ей выводить не из
        чего. Это отдельное число `no_source`, а не «переведено 0» и не тихий пропуск."""
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(LS.HEADER_BASE_LINE + "\n")
            f.write("\t".join(["1", "в", "о", "к", "п", "@в", "2026-09-03T00:00:00Z",
                               LS.STATE_ACTIVE]) + "\n")
        before = self.raw_bytes()
        res = LS.set_batch((1,), 1, path=self.store)
        self.assertEqual(res.stamped, 0)
        self.assertEqual(res.no_source, (1,))
        self.assertEqual(self.raw_bytes(), before, "строку без источника всё-таки тронули")

    def test_garbage_lot_refuses_before_touching_anything(self):
        self.write_src_rows(2)
        before = self.raw_bytes()
        for bad in ("0", "-1", "", "первая"):
            with self.assertRaises(LS.LessonRejected):
                LS.set_batch((1, 2), bad, path=self.store)
        self.assertEqual(self.raw_bytes(), before)


# =======================================================================================
# П.3 — СОВМЕСТИМОСТЬ: строка старого вида читается, и её режим НЕИЗВЕСТНО
# =======================================================================================
class TestOldRowsReadAsUnknown(_Sandbox):

    def rows_of(self, header, row):
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(header + "\n" + row + "\n")
        return LS.load(self.store)

    def test_eight_column_row_reads_and_knows_neither_lot_nor_mode(self):
        store = self.rows_of(LS.HEADER_BASE_LINE,
                             "\t".join(["1", "в", "о", "к", "п", "@в",
                                        "2026-08-01T00:00:00Z", LS.STATE_ACTIVE]))
        self.assertEqual(len(store.broken), 0, "старая строка уехала в НЕРАЗБОР: %s"
                         % (store.broken,))
        les = store.lessons[0]
        self.assertIs(les.source, LS.SOURCE_UNKNOWN)
        self.assertIs(les.batch, LS.BATCH_UNKNOWN)
        self.assertIs(les.mode, LS.MODE_UNKNOWN, "строке старого формата подставлен режим")
        self.assertFalse(LS.is_training(les), "неизвестный режим дал право правила")
        self.assertFalse(LS.is_fight(les))

    def test_nine_column_row_keeps_its_source_and_stays_unknown_in_mode(self):
        """ВАЖНЕЙШИЙ СЛУЧАЙ СОВМЕСТИМОСТИ: так лежат ВСЕ 24 живые строки на 20.09. Источник у
        них есть, и соблазн вывести режим из него силён — но в строке его НЕТ, а вывод при
        чтении был бы нашей догадкой, выданной за её показание."""
        store = self.rows_of(LS.HEADER_SRC_LINE,
                             "\t".join(["1", "в", "о", "к", "п", "@в",
                                        "2026-09-03T00:00:00Z", LS.STATE_ACTIVE,
                                        LS.SOURCE_BOOK]))
        self.assertEqual(len(store.broken), 0)
        les = store.lessons[0]
        self.assertEqual(les.source, LS.SOURCE_BOOK)
        self.assertIs(les.batch, LS.BATCH_UNKNOWN)
        self.assertIs(les.mode, LS.MODE_UNKNOWN,
                      "режим выведен из источника ПРИ ЧТЕНИИ — умолчание, притворяющееся знанием")
        self.assertFalse(LS.is_training(les))
        self.assertEqual(LS.say_mode(les.mode), LS.SAY_MODE_UNKNOWN)

    def test_ten_column_row_is_broken_not_half_read(self):
        """Ширины 10 в формате нет: хвост растёт парой. Полустрока идёт в НЕРАЗБОР ГРОМКО, а
        не читается как «партия есть, режима нет»."""
        store = self.rows_of(LS.HEADER_SRC_LINE,
                             "\t".join(["1", "в", "о", "к", "п", "@в",
                                        "2026-09-03T00:00:00Z", LS.STATE_ACTIVE,
                                        LS.SOURCE_BOOK, "1"]))
        self.assertEqual(len(store.lessons), 0)
        self.assertEqual(len(store.broken), 1, "строка ширины 10 прочиталась как законная")

    def test_old_and_new_rows_live_in_one_file(self):
        """Смешанный файл читается целиком: старая строка отвечает «не знаю», новая — значением.
        Это и есть «читается без падения»."""
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(LS.HEADER_SRC_LINE + "\n")
            f.write("\t".join(["1", "в", "о", "к", "п", "@в", "2026-09-03T00:00:00Z",
                               LS.STATE_ACTIVE, LS.SOURCE_BOOK]) + "\n")
        LS.add("новый вопрос", "ответ", "как надо", "почему", "@тест",
               source=LS.SOURCE_EXPORT, batch=1, path=self.store)
        store = LS.load(self.store)
        self.assertEqual(len(store.broken), 0)
        self.assertEqual(len(store.lessons), 2)
        old, new = store.lessons
        self.assertIs(old.mode, LS.MODE_UNKNOWN)
        self.assertEqual(new.mode, LS.MODE_FIGHT)
        self.assertEqual(dict(LS.modes_census(store.lessons)),
                         {LS.MODE_FIGHT: 1, LS.SAY_MODE_UNKNOWN: 1})


# =======================================================================================
# П.5 — ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПРО РЕЖИМ: «обучение» не поднимается ни одной веткой
# =======================================================================================
class TestModeCannotBeRaised(_Sandbox):

    # ВСЕ дороги записи урока в хранилище, поимённо. Список — часть теста: появится шестая
    # дорога, и её придётся вписать сюда руками, то есть заметить.
    WRITE_DOORS = ("add", "add_candidate", "_add_row", "set_source", "set_batch",
                   "withdraw", "promote", "rollback", "batch_withdraw", "batch_restore")

    def test_mode_is_not_a_parameter_of_any_write_door(self):
        """ГЛАВНЫЙ ЗАМОК: вписать режим НЕЧЕМ — такого входа у хранилища нет. Это не дисциплина
        вызывающего, а отсутствие двери."""
        bad = []
        for name in self.WRITE_DOORS:
            fn = getattr(LS, name)
            for param in inspect.signature(fn).parameters:
                if param.lower() in ("mode", "режим", "modes", "is_training", "training"):
                    bad.append((name, param))
        self.assertEqual(bad, [], "у двери записи появился вход режима: %s" % (bad,))

    def test_mode_is_only_ever_assigned_from_mode_of_source(self):
        """…и в самом модуле режим присваивается РОВНО одним выражением. Второе место, решающее,
        что такое режим строки, разошлось бы с первым молча."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lesson_store.py"),
                  encoding="utf-8") as f:
            text = f.read()
        found = re.findall(r"^\s*mode\s*=\s*(.+?)\s*$", text, re.M)
        self.assertTrue(found, "переменная режима в модуле не нашлась вовсе — тест ослеп")
        for value in found:
            self.assertTrue(value.startswith("mode_of_source("),
                            "режим присвоен мимо единственной дороги: %r" % value)

    def test_the_map_covers_every_source_by_name(self):
        """Карта источник → режим ПОЛНАЯ. Новый источник не проедет молча «неизвестным»: его
        придётся назвать здесь руками, в тот же час, когда его заводят."""
        self.assertEqual(sorted(LS.SOURCE_MODE), sorted(LS.SOURCES),
                         "карта режимов разошлась со списком источников")
        self.assertEqual(LS.SOURCE_MODE[LS.SOURCE_EXPORT], LS.MODE_FIGHT,
                         "экспорт переписки менеджеров перестал быть боем")
        self.assertIs(LS.SOURCE_MODE[LS.SOURCE_EXAM], LS.MODE_UNKNOWN,
                      "двусмысленному экзамену назначено значение — умолчание вместо знания")

    def test_no_branch_puts_a_fight_lesson_into_training_mode(self):
        """ЧИСЛОМ. Перебор ВСЕХ дорог записи × всех форм аргумента партии для БОЕВОГО источника:
        сколько строк легло с режимом «обучение». Ответ обязан быть 0 из N."""
        landed, training, fight = 0, 0, 0
        for door in ("add", "add_candidate"):
            for lot in (None, 1, 2, "3"):
                path = os.path.join(self.dir, "b_%s_%s.tsv" % (door, lot))
                if door == "add":
                    LS.add("боевой вопрос", "ответ", "как надо", "почему", "@тест",
                           source=LS.SOURCE_EXPORT, batch=lot, path=path)
                else:
                    LS.add_candidate("боевой вопрос", "ответ", "как надо", "@тест",
                                     source=LS.SOURCE_EXPORT, batch=lot, path=path)
                les = LS.load(path).lessons[0]
                landed += 1
                training += 1 if LS.is_training(les) else 0
                fight += 1 if LS.is_fight(les) else 0
        self.assertEqual(landed, 8, "перебор прошёл не по всем дорогам")
        self.assertEqual(training, 0,
                         "урок из боя лёг с режимом обучения — %d раз(а) из %d"
                         % (training, landed))
        self.assertEqual(fight, 8, "боевой источник дал не бой")

    def test_every_source_lands_with_the_mode_its_map_promises(self):
        """Зеркало предыдущего числа с другой стороны: каждая дорога кладёт РОВНО то, что обещает
        карта, — включая пустоту у двусмысленного источника."""
        seen = {}
        for source in LS.SOURCES:
            path = os.path.join(self.dir, "s_%s.tsv" % LS.norm_source(source))
            LS.add("вопрос", "ответ", "как надо", "почему", "@тест",
                   source=source, batch=1, path=path)
            seen[source] = LS.load(path).lessons[0].mode
        self.assertEqual(seen, dict(LS.SOURCE_MODE))
        self.assertIs(seen[LS.SOURCE_EXAM], LS.MODE_UNKNOWN)

    def test_a_hand_written_training_mode_gives_no_right(self):
        """Дверью «обучение» боевой строке не вписать — а РУКОЙ в файле можно. Такая строка
        читается (байты чужие не выкидываем), но права правила не получает: расхождение с
        источником — отдельная новость, и `is_training` на ней fail-closed."""
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(LS.HEADER_LINE + "\n")
            f.write("\t".join(["1", "в", "о", "к", "п", "@рука", "2026-09-20T00:00:00Z",
                               LS.STATE_ACTIVE, LS.SOURCE_EXPORT, "1", LS.MODE_TRAIN]) + "\n")
        les = LS.load(self.store).lessons[0]
        self.assertEqual(les.mode, LS.MODE_TRAIN, "чужой байт выкинут при чтении")
        self.assertTrue(LS.mode_conflict(les), "расхождение с источником не замечено")
        self.assertFalse(LS.is_training(les),
                         "вписанное руками «обучение» дало право отвечать клиентам")

    def test_unknown_is_not_a_conflict(self):
        """«Не знаю» ничему не противоречит: строка без режима расхождением не считается, иначе
        все 24 живые строки объявились бы поправленными мимо двери."""
        les = LS.Lesson(1, "в", "о", "к", "п", "@в", "2026-09-03T00:00:00Z", LS.STATE_ACTIVE,
                        2, LS.SOURCE_BOOK)
        self.assertIs(les.mode, LS.MODE_UNKNOWN)
        self.assertFalse(LS.mode_conflict(les))
        self.assertFalse(LS.is_training(les))


# =======================================================================================
# Инвариант формата — хвост ничего не сдвинул, и ширины 10 не существует
# =======================================================================================
class TestFormatInvariants(unittest.TestCase):

    def test_three_widths_three_headers_and_no_tenth(self):
        self.assertEqual(LS.WIDTHS, (8, 9, 11))
        self.assertEqual(len(LS.HEADERS), len(LS.WIDTHS))
        self.assertEqual([len(h.split("\t")) for h in LS.HEADERS], list(LS.WIDTHS))

    def test_the_tail_did_not_move_a_single_old_index(self):
        self.assertEqual(LS.IDX_WHY, 4)
        self.assertEqual(LS.IDX_STATE, 7)
        self.assertEqual(LS.IDX_SOURCE, 8)
        self.assertEqual(LS.IDX_BATCH, 9)
        self.assertEqual(LS.IDX_MODE, 10)

    def test_grow_refuses_every_illegal_shape(self):
        """`_grow_row` судит ОБЕ стороны — исходную ширину и получившуюся."""
        row9 = "\t".join(["1"] + ["x"] * 8)
        row8 = "\t".join(["1"] + ["x"] * 7)
        row11 = "\t".join(["1"] + ["x"] * 10)
        self.assertEqual(len(LS._grow_row(row9, ("1", "бой")).split("\t")), 11)
        self.assertEqual(LS._grow_row(row8, ("1", "бой")), row8, "8→10 не отказано")
        self.assertEqual(LS._grow_row(row11, ("1", "бой")), row11, "11→13 не отказано")
        self.assertEqual(len(LS._grow_row(row8, "источник").split("\t")), 9)
        self.assertEqual(LS._grow_row(row9, "хвост"), row9, "9→10 не отказано")

    def test_batch_key_has_three_outcomes(self):
        self.assertIs(LS.batch_key(None), LS.BATCH_ANY)
        self.assertIs(LS.batch_key("  "), LS.BATCH_ANY)
        self.assertIs(LS.batch_key(LS.BATCH_KEY_NONE), LS.BATCH_UNKNOWN)
        self.assertEqual(LS.batch_key("4"), "4")
        self.assertIsNot(LS.BATCH_ANY, LS.BATCH_UNKNOWN,
                         "«любая» и «без номера» схлопнулись в одно значение")


# =======================================================================================
# ЗАМЕРЕННАЯ МИНА ЗАХОДА: снятие набора на ГРАНИЦЕ СЕКУНДЫ было НЕВОЗВРАТНЫМ
# =======================================================================================
class _TwoTickClock(object):
    """Часы, на которых ВИДНО разницу между «спросили часы» и «назвали мгновение».

    `gmtime()` без аргумента отдаёт КАЖДЫЙ РАЗ следующую секунду — так выглядит вызов,
    переступивший границу секунды. `gmtime(epoch)` отвечает честно на названное число. Код,
    берущий мгновение ОДИН раз и передающий его вниз, на этих часах ведёт себя как на обычных;
    код, спрашивающий часы дважды, — расходится, и расхождение видно числом."""

    def __init__(self, base):
        self.base = base
        self.asked = 0

    def time(self):
        return self.base

    def gmtime(self, when=None):
        if when is None:
            self.asked += 1
            return _real_time.gmtime(self.base + self.asked)
        return _real_time.gmtime(when)

    def strftime(self, fmt, moment):
        return _real_time.strftime(fmt, moment)


class TestWithdrawalIsReturnableAcrossASecondBoundary(_Sandbox):
    """Снятие набора пишет след ДО правки, и оба обязаны нести ОДИН штамп. До правки 20.09
    мгновение бралось дважды (`now=None` уезжал вниз как «спроси часы ещё раз»), и на границе
    секунды след говорил одно, строка — другое: возврат честно отказывал, а набор становился
    НЕВОЗВРАТНЫМ. Замер до правки: 1 срыв на 25 прогонов регресса."""

    def setUp(self):
        super(TestWithdrawalIsReturnableAcrossASecondBoundary, self).setUp()
        self.add("строка поставки", LS.SOURCE_EXPORT, batch=1)
        self.add("вторая строка поставки", LS.SOURCE_EXPORT, batch=1)
        self.real = LS.time
        LS.time = _TwoTickClock(1_800_000_000)

    def tearDown(self):
        LS.time = self.real
        super(TestWithdrawalIsReturnableAcrossASecondBoundary, self).tearDown()

    def test_trace_and_row_carry_the_same_stamp(self):
        start = self.raw_bytes()
        res = LS.batch_withdraw(LS.SOURCE_EXPORT, count=2, why="поставка оказалась сырой",
                                who="@тест", path=self.store, batch=1)
        self.assertTrue(res.ok, res.reason)
        trace = LS.load_batch_trace(self.store, source=LS.SOURCE_EXPORT)
        rows = LS.load(self.store).lessons
        for t, les in zip(trace, rows):
            self.assertEqual(t.new_state, les.state,
                             "след и строка разошлись штампом — набор стал невозвратным")

        back = LS.batch_restore(LS.SOURCE_EXPORT, who="@тест", path=self.store, batch=1)
        self.assertTrue(back.ok, back.reason)
        self.assertEqual(self.raw_bytes(), start, "возврат вернул не те байты")

    def test_the_clock_really_moves_otherwise_the_test_proves_nothing(self):
        """КОНТРФАКТ НА САМ ПРИБОР: часы обязаны идти, иначе предыдущее число зелёное даром."""
        clock = LS.time
        first = LS.now_stamp()
        second = LS.now_stamp()
        self.assertNotEqual(first, second, "часы стенда не двигаются — мина не воспроизводится")
        self.assertEqual(LS.now_stamp(clock.base), LS.now_stamp(clock.base),
                         "названное мгновение даёт разные штампы — прибор врёт")


if __name__ == "__main__":
    unittest.main(verbosity=2)
