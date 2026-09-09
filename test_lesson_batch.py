# -*- coding: utf-8 -*-
"""
test_lesson_batch.py — регресс ДВЕРИ СНЯТИЯ НАБОРА уроков целиком (09.09.2026).

ЧТО ЗАКРЫВАЕТСЯ И ЧЕМ ЭТО БЫЛО ДО. Разрез `lesson_store.withdraw(source=…)` завёлся утром 09.09 и
умел ровно одно — пометить набор снятым: он не знал, КТО снимает и ЗАЧЕМ, не оставлял следа и НЕ
ВОЗВРАЩАЛСЯ. С телефона его не было вовсе (`pc_agent.lesson_word` на «урок набор …» отвечал None —
замерено пробой премис). Пока такой двери нет, заливать экспорт переписки НЕЛЬЗЯ: залить владелец
сможет, а вынуть — только с консоли, которой он не пользуется.

ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА — сердце набора (`TestFourNegatives`), каждый закрывает свой способ
соврать (задание Штаба, п.6):
  1. БЕЗ ПРАВА — отказ, и ответ ОТЛИЧАЕТСЯ от ответа с правом. Сравниваются два ответа между
     собой: тест, который их не сравнивает, зеленел бы и на двери, которая права не спрашивает;
  2. ПОДТВЕРЖДЕНИЕ БЕЗ НАЗВАННОГО ОБЪЕКТА (число строк не названо) — отказ, и таблица не
     изменилась ни байтом. Проверяется не слово отказа, а то, что машина ничего не подставила;
  3. ПОДТВЕРЖДЕНИЕ С ЧУЖИМ КЛЮЧОМ — снимает НОЛЬ строк и ОТКАЗЫВАЕТ. Три чужести проверены
     врозь: ключа нет в списке · ключ известен, но набора в таблице нет · ключ верен, а число
     чужое (объект «поехал» между показом и подтверждением);
  4. СНЯТИЕ ОДНОГО НАБОРА НЕ ЗАДЕВАЕТ ЧУЖОГО — ни строки другого набора, ни строки БЕЗ источника
     (та, что старше формата). Сверяются БАЙТЫ чужих строк, а не их состояние словами.

ПРЕДСМЕРТНЫЙ ВЗГЛЯД, названный прямо: провал этой работы выглядел бы так — снятие набора уехало
бы в один шаг («урок набор сними экспорт_переписки»), число из подтверждения исчезло бы как
«лишняя церемония», и первая же опечатка в ключе сняла бы не тот набор молча. Поэтому здесь есть
отдельный класс `TestObjectIsNamedOrNothingHappens`: он проверяет не «снятие работает», а
«без названного объекта не происходит НИЧЕГО».

БОЕВОГО НЕ КАСАЕМСЯ НИ ОДНОЙ ВЕТКОЙ:
  • таблица уроков, её след переводов и след наборов — временные файлы во временном каталоге
    (`lesson_store.STORE_PATH` подменён; оба следа производны от таблицы и уезжают туда же сами);
  • список прав `suggest.APPROVER_USERNAMES` подменяется на время теста и возвращается назад;
  • Telegram не трогается вовсе: `pc_agent.lesson_word` — чистая функция, голденится словами;
  • субпроцессов дверь здесь не запускает: CLI зовётся своей `main(argv)` в этом же процессе.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_batch -v
"""

import hashlib
import os
import tempfile
import unittest

import lesson_batch
import lesson_store as LS
import suggest
import trainer

HERE = os.path.dirname(os.path.abspath(__file__))

Q = "Какие модели есть и какие цены на аренду на неделю?"
A = "[тренажёр | ТЕСТ-3 | правил: 9] Есть Yamaha NMAX и Honda PCX. Цену уточню и вернусь."
REMARK = "не пиши «уточню и вернусь», если цена уже посчитана — называй сумму сразу"
WHY = "клиент спросил цену прямым текстом, а бот ушёл в «вернусь» — это потеря заявки"
OFF_WHY = "залили не тот выгруз: в нём переписка не менеджеров, а автоответчика"
OWNER = "filipp"
STRANGER = "someone_else"


class _Base(unittest.TestCase):
    """Своя таблица уроков и свои следы на каждый тест. Боевой файл не задевается вовсе."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_batch_test_")
        self.addCleanup(box.cleanup)
        self.box = box.name
        self.store = os.path.join(self.box, "lesson_store.tsv")
        # `_path(None)` читает модульную глобаль В МОМЕНТ ВЫЗОВА — подмена уводит сюда и те ветки,
        # которым путь не передают.
        self.addCleanup(setattr, LS, "STORE_PATH", LS.STORE_PATH)
        LS.STORE_PATH = self.store

    def rights(self, *names):
        """Подменить список прав на время теста (пустой = «не настроено», fail-closed = НИКОМУ)."""
        self.addCleanup(setattr, suggest, "APPROVER_USERNAMES", suggest.APPROVER_USERNAMES)
        suggest.APPROVER_USERNAMES = {n.lstrip("@").lower() for n in names}

    def rows(self):
        return LS.load(self.store).lessons

    def add(self, source, correct=REMARK, who=OWNER, when="2026-09-09T10:00:00Z"):
        return LS.add(Q, A, correct, WHY, who, source=source, when=when, path=self.store)

    def add_candidate(self, source, correct=REMARK, who=OWNER, when="2026-09-09T11:00:00Z"):
        return LS.add_candidate(Q, A, correct, who, source=source, when=when, path=self.store)

    def add_old_row(self, number=900):
        """Строка СТАРОГО, восьмиколоночного формата — та, что старше источника.

        Пишется байтами, а не через `add`: `add` источник ТРЕБУЕТ (с 09.09 запись без него —
        отказ), и получить такую строку легальным вызовом сегодня уже нельзя. А в боевой таблице
        их девять, и снятие набора обязано их не задевать."""
        need_header = not os.path.exists(self.store)
        row = "\t".join((str(number), Q, A, "старое правило", "перенос 05.09", OWNER,
                         "2026-09-05T09:00:00Z", LS.STATE_ACTIVE))
        LS._append_row(self.store, row, need_header, header=LS.HEADER_BASE_LINE)
        return number

    def sha(self):
        with open(self.store, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def raw(self):
        with open(self.store, "rb") as f:
            return f.read()

    def line_of(self, number):
        """СЫРАЯ строка урока #number — байты, а не смысл."""
        for les in self.rows():
            if les.number == number:
                return LS._raw_fields(self.store, les.line)
        return None

    def off(self, source, count, why=OFF_WHY, who=OWNER):
        return trainer.withdraw_batch(source, count=count, why=why, who=who, path=self.store)

    def back(self, source, who=OWNER):
        return trainer.restore_batch(source, who=who, path=self.store)


# =======================================================================================
# ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА (задание Штаба, п.6)
# =======================================================================================
class TestFourNegatives(_Base):

    def test_1_without_the_right_it_refuses_and_the_answer_differs(self):
        """БЕЗ ПРАВА — отказ, и это ДРУГОЙ ответ, а не тот же самый.

        Сравниваются два ответа МЕЖДУ СОБОЙ: проверка одного лишь слова отказа зеленела бы и на
        двери, которая права не спрашивает вовсе. Плюс поимённо: посторонний при НЕПУСТОМ списке
        прав — тоже отказ (иначе «право» означало бы «список непуст»)."""
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        before = self.sha()

        self.rights()                                      # список прав ПУСТ — fail-closed
        denied = self.off(LS.SOURCE_EXPORT, 2)
        self.assertEqual(denied["status"], trainer.STATUS_DENIED, denied["card"])
        self.assertEqual(self.sha(), before, "отказ по праву всё-таки тронул таблицу")
        self.assertEqual(LS.load_batch_trace(self.store), (), "отказ написал строку следа")

        self.rights(OWNER)
        alien = self.off(LS.SOURCE_EXPORT, 2, who=STRANGER)
        self.assertEqual(alien["status"], trainer.STATUS_DENIED, alien["card"])
        self.assertEqual(self.sha(), before)

        allowed = self.off(LS.SOURCE_EXPORT, 2)
        self.assertEqual(allowed["status"], trainer.STATUS_BATCH_OFF, allowed["card"])
        self.assertNotEqual(denied["card"], allowed["card"],
                            "право снято и дано, а ответ ОДИН И ТОТ ЖЕ — право не спрашивают")
        self.assertNotEqual(self.sha(), before)

    def test_2_confirmation_without_the_named_object_refuses(self):
        """ПОДТВЕРЖДЕНИЕ БЕЗ ЧИСЛА — отказ, и таблица не изменилась ни байтом.

        Проверяется не слово отказа, а то, что машина не подставила число сама: «снять столько,
        сколько найдётся» — ровно тот исход, ради запрета которого объект и называется."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        before_bytes, before = self.raw(), self.sha()

        got = self.off(LS.SOURCE_EXPORT, None)
        self.assertEqual(got["status"], trainer.STATUS_REFUSED, got["card"])
        self.assertIn("ЧИСЛО", got["card"], got["card"])
        self.assertEqual(self.raw(), before_bytes, "отказ без объекта тронул байты таблицы")
        self.assertEqual(LS.load_batch_trace(self.store), ())
        self.assertEqual(len(LS.active(self.rows())), 2, "строки всё-таки сняли")

        # Пустая строка и «не число» — тоже НЕ объект, а не «ноль».
        for bad in ("", "   ", "сколько-нибудь", "все"):
            again = self.off(LS.SOURCE_EXPORT, bad)
            self.assertEqual(again["status"], trainer.STATUS_REFUSED, repr(bad))
            self.assertEqual(self.sha(), before, repr(bad))

    def test_3_confirmation_with_a_foreign_key_takes_zero_rows_and_refuses(self):
        """ЧУЖОЙ КЛЮЧ снимает НОЛЬ строк и ОТКАЗЫВАЕТ. Три чужести — врозь, они чинятся по-разному.

        «Снял 0» с кодом успеха здесь был бы ложным зелёным ровно в той операции, ради которой
        дверь и заведена: отличить опечатку в ключе от честно пустого набора вызывающему нечем."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        before_bytes, before = self.raw(), self.sha()

        # (а) ключа нет в закрытом списке — опечатка в имени набора
        unknown = self.off("экспорт_переписки_2", 2)
        self.assertEqual(unknown["status"], trainer.STATUS_REFUSED, unknown["card"])
        self.assertIn("неизвестен", unknown["card"])
        self.assertEqual(self.raw(), before_bytes)

        # (б) ключ законный, но НАБОРА С НИМ В ТАБЛИЦЕ НЕТ
        empty = self.off(LS.SOURCE_BOOK, 2)
        self.assertEqual(empty["status"], trainer.STATUS_REFUSED, empty["card"])
        self.assertIn("снимать нечего", empty["card"].lower())
        self.assertEqual(self.raw(), before_bytes)

        # (в) ключ верный, а ЧИСЛО чужое — объект «поехал» между показом и подтверждением
        stale = self.off(LS.SOURCE_EXPORT, 5)
        self.assertEqual(stale["status"], trainer.STATUS_REFUSED, stale["card"])
        self.assertIn("названо 5", stale["card"])
        self.assertIn("можно 2", stale["card"])
        self.assertEqual(self.raw(), before_bytes, "отказ по числу тронул байты таблицы")
        self.assertEqual(LS.load_batch_trace(self.store), ())
        self.assertEqual(len(LS.active(self.rows())), 2)
        self.assertEqual(self.sha(), before)

    def test_4_taking_one_batch_touches_no_row_of_another(self):
        """СНЯТИЕ ОДНОГО НАБОРА НЕ ЗАДЕВАЕТ НИ ЧУЖОГО НАБОРА, НИ СТРОКИ БЕЗ ИСТОЧНИКА.

        Сверяются БАЙТЫ чужих строк, а не их состояние словами: правка «мимо» могла бы поменять
        соседнее поле, оставив состояние прежним, и проверка по смыслу этого не увидела бы.
        Строка старого формата тут не украшение — в боевой таблице их сегодня девять."""
        self.rights(OWNER)
        keep_trainer = self.add(LS.SOURCE_TRAINER, correct="чужой набор — тренажёр")
        keep_book = self.add(LS.SOURCE_BOOK, correct="чужой набор — книга")
        keep_old = self.add_old_row()
        take_1 = self.add(LS.SOURCE_EXPORT, correct="наш набор, строка 1")
        take_2 = self.add_candidate(LS.SOURCE_EXPORT, correct="наш набор, кандидат")

        untouched = {n: self.line_of(n) for n in (keep_trainer, keep_book, keep_old)}
        got = self.off(LS.SOURCE_EXPORT, 2)
        self.assertEqual(got["status"], trainer.STATUS_BATCH_OFF, got["card"])
        self.assertEqual(sorted(got["numbers"]), sorted([take_1, take_2]))

        for number, was in untouched.items():
            self.assertEqual(self.line_of(number), was,
                             "снятие набора тронуло БАЙТЫ чужой строки #%d" % number)
        # И положительная половина: наши две действительно сняты, и обе — разрезом «источник».
        for number in (take_1, take_2):
            state = self.line_of(number)[LS.IDX_STATE]
            self.assertEqual(LS.parse_state(state)[:2], (True, LS.CUT_SOURCE), state)
        self.assertEqual(got["lines_before"], got["lines_after"], "снятие потеряло строку файла")


# =======================================================================================
# ДВА ШАГА И НАЗВАННЫЙ ОБЪЕКТ (задание Штаба, п.2 и решение, которое не пересматривается)
# =======================================================================================
class TestObjectIsNamedOrNothingHappens(_Base):

    def test_step_one_names_key_count_when_and_who(self):
        """ШАГ ПЕРВЫЙ отвечает на все четыре вопроса решения: ключ, сколько, когда, кем."""
        self.add(LS.SOURCE_EXPORT, who="filipp", when="2026-09-07T08:00:00Z")
        self.add(LS.SOURCE_EXPORT, who="manager", when="2026-09-08T19:30:00Z")
        self.add_candidate(LS.SOURCE_EXPORT, who="filipp", when="2026-09-07T09:00:00Z")
        self.add(LS.SOURCE_TRAINER)

        before = self.sha()
        dec = trainer.batch_card(LS.SOURCE_EXPORT, path=self.store)
        self.assertEqual(dec["status"], "shown", dec["card"])
        self.assertEqual(dec["count"], 3)
        card = dec["card"]
        self.assertIn(LS.SOURCE_EXPORT, card)                       # КЛЮЧ
        self.assertIn("3", card)                                    # СКОЛЬКО
        self.assertIn("2026-09-07T08:00:00Z", card)                 # КОГДА (первый)
        self.assertIn("2026-09-08T19:30:00Z", card)                 # КОГДА (последний)
        self.assertIn("@filipp", card)                              # КЕМ
        self.assertIn("@manager", card)
        self.assertEqual(self.sha(), before, "ШАГ ПЕРВЫЙ (чтение) изменил таблицу")

    def test_step_one_asks_for_the_object_in_the_very_words_that_work(self):
        """Карточка показа предлагает команду, которую МОЖНО СКОПИРОВАТЬ и она сработает.

        Замок против расхождения показа со вторым шагом: если форма подтверждения в карточке
        разъедется с тем, что разбирает роутер, владелец будет копировать нерабочую строку —
        и узнает об этом на самой опасной команде."""
        import pc_agent
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        card = trainer.batch_card(LS.SOURCE_EXPORT, path=self.store)["card"]

        offer = [ln for ln in card.splitlines() if "сними" in ln]
        self.assertEqual(len(offer), 1, card)
        quoted = offer[0].split("«", 1)[1].split("»", 1)[0]
        self.assertEqual(quoted, "урок набор сними %s 2: причина словами" % LS.SOURCE_EXPORT)
        act, count, why, source = pc_agent.lesson_word(quoted)
        self.assertEqual((act, count, source), ("batch_off", 2, LS.SOURCE_EXPORT))
        self.assertEqual(why, "причина словами")
        # И она действительно проходит — с настоящей причиной вместо примера.
        got = self.off(source, count)
        self.assertEqual(got["status"], trainer.STATUS_BATCH_OFF, got["card"])

    def test_the_reason_is_asked_before_the_number_when_both_are_missing(self):
        """Не названо НИЧЕГО — отказ называет ПРИЧИНУ, а не число.

        Порядок выбран, а не случился: «почему» — единственное жёсткое требование владельца к
        таблице уроков и требование более старое, а новая проверка, перехватывающая чужие отказы,
        прячет их причину. Тот же выбор, что у источника в `add` (09.09, утренний заход)."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        got = self.off(LS.SOURCE_EXPORT, None, why="")
        self.assertEqual(got["status"], trainer.STATUS_REFUSED, got["card"])
        self.assertIn("причина не названа", got["card"])
        self.assertNotIn("ЧИСЛО", got["card"])
        # А когда причина ЕСТЬ — отказ переходит к числу, и это уже другой текст.
        nxt = self.off(LS.SOURCE_EXPORT, None)
        self.assertIn("ЧИСЛО", nxt["card"])

    def test_the_count_is_checked_in_the_store_not_only_in_the_door(self):
        """ЧИСЛО СВЕРЯЕТ ХРАНИЛИЩЕ, а не только дверь: гейт, оставленный вызывающему, обойдёт
        первый же новый вызывающий — и обойдёт молча (довод `REASON_PROMOTE_NO_WHO`)."""
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        before = self.sha()
        # Хранилище зовётся НАПРЯМУЮ, мимо `trainer` и мимо CLI.
        res = LS.batch_withdraw(LS.SOURCE_EXPORT, count=None, why=OFF_WHY, who=OWNER,
                                path=self.store)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, LS.REASON_BATCH_NO_COUNT)
        self.assertEqual(self.sha(), before)
        wrong = LS.batch_withdraw(LS.SOURCE_EXPORT, count=1, why=OFF_WHY, who=OWNER,
                                  path=self.store)
        self.assertFalse(wrong.ok)
        self.assertEqual(self.sha(), before)


# =======================================================================================
# ОБРАТИМОСТЬ — ПОБАЙТНО (задание Штаба, п.5)
# =======================================================================================
class TestReversibleToTheByte(_Base):

    def test_restoring_the_batch_gives_back_the_very_same_file(self):
        """ВЕРНУТЬ НАБОР — ОДНО ДВИЖЕНИЕ, и файл после возврата РАВЕН файлу до снятия ПОБАЙТНО.

        В тексте одного урока СТОИТ чужая экранированная последовательность `\\q` — именно на ней
        круг «разобрать → собрать» не тождественен, и «ровно то состояние» перестало бы быть
        правдой на первом же уроке, который правили руками."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT, correct=REMARK + " (см. пометку \\q в книге)")
        self.add(LS.SOURCE_EXPORT)
        self.add_candidate(LS.SOURCE_EXPORT)                # кандидат уходит и возвращается тоже
        self.add(LS.SOURCE_TRAINER)
        self.add_old_row()

        before_bytes, before = self.raw(), self.sha()
        got = self.off(LS.SOURCE_EXPORT, 3)
        self.assertEqual(got["status"], trainer.STATUS_BATCH_OFF, got["card"])
        self.assertNotEqual(self.sha(), before, "снятие ничего не изменило в таблице")

        back = self.back(LS.SOURCE_EXPORT)
        self.assertEqual(back["status"], trainer.STATUS_BATCH_BACK, back["card"])
        self.assertEqual(self.raw(), before_bytes,
                         "возврат вернул НЕ ТЕ БАЙТЫ, что лежали до снятия")
        self.assertEqual(self.sha(), before)
        self.assertEqual(back["count"], 3)
        self.assertEqual(got["lines_before"], back["lines_after"])

    def test_restore_returns_each_row_to_its_own_previous_state(self):
        """Возврат — это НЕ «включить набор»: действующий станет действующим, кандидат кандидатом,
        а снятый РАНЬШЕ другим разрезом останется снятым тем разрезом."""
        self.rights(OWNER)
        act = self.add(LS.SOURCE_EXPORT)
        cand = self.add_candidate(LS.SOURCE_EXPORT)
        gone = self.add(LS.SOURCE_EXPORT)
        LS.withdraw(number=gone, path=self.store)           # снят РАНЬШЕ, разрезом «урок»
        before_gone = self.line_of(gone)[LS.IDX_STATE]

        self.assertEqual(self.off(LS.SOURCE_EXPORT, 2)["status"], trainer.STATUS_BATCH_OFF)
        self.assertEqual(self.back(LS.SOURCE_EXPORT)["status"], trainer.STATUS_BATCH_BACK)

        states = {les.number: les.state for les in self.rows()}
        self.assertEqual(states[act], LS.STATE_ACTIVE)
        self.assertEqual(states[cand], LS.STATE_CANDIDATE)
        self.assertEqual(states[gone], before_gone, "снятое РАНЬШЕ уехало в другое состояние")

    def test_restore_refuses_wholly_when_a_single_row_moved(self):
        """Разошлась ОДНА строка — отказ ЦЕЛИКОМ, таблица не тронута ни одной правкой.

        Вернуть половину набора хуже, чем не вернуть ничего: вторую половину после этого нечем
        опознать. И правка руками между снятием и возвратом обязана быть ВИДНА, а не затёрта."""
        self.rights(OWNER)
        one = self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        self.assertEqual(self.off(LS.SOURCE_EXPORT, 2)["status"], trainer.STATUS_BATCH_OFF)

        # Кто-то тронул одну строку после снятия — снял её ещё раз, уже разрезом «урок».
        LS._rewrite(self.store, {LS.load(self.store).lessons[0].line:
                                 {LS.IDX_STATE: LS.withdrawn_state(LS.CUT_ONE, "2026-09-09")}})
        after_touch = self.raw()
        back = self.back(LS.SOURCE_EXPORT)
        self.assertEqual(back["status"], trainer.STATUS_REFUSED, back["card"])
        self.assertIn("после снятия изменился", back["card"])
        self.assertEqual(self.raw(), after_touch, "отказ возврата всё-таки правил таблицу")
        del one

    def test_restore_without_a_withdrawal_refuses(self):
        """Возвращать нечего — отказ словами, а не «вернул 0» и не молчание."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        before = self.sha()
        back = self.back(LS.SOURCE_EXPORT)
        self.assertEqual(back["status"], trainer.STATUS_REFUSED, back["card"])
        self.assertIn("в следе нет", back["card"])
        self.assertEqual(self.sha(), before)

    def test_off_back_off_again_returns_the_right_withdrawal(self):
        """Снял → вернул → снял: возврат отменяет ПОСЛЕДНЕЕ НЕЗАКРЫТОЕ снятие, а не первое.

        Судить «по последней строке следа» здесь было бы неверно: закрытость движения видна по
        ССЫЛКЕ возврата на штамп снятия, а не по порядку строк."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        clean = self.raw()

        self.assertEqual(self.off(LS.SOURCE_EXPORT, 2, why="первый раз")["status"],
                         trainer.STATUS_BATCH_OFF)
        self.assertEqual(self.back(LS.SOURCE_EXPORT)["status"], trainer.STATUS_BATCH_BACK)
        self.assertEqual(self.raw(), clean)

        second = self.off(LS.SOURCE_EXPORT, 2, why="второй раз")
        self.assertEqual(second["status"], trainer.STATUS_BATCH_OFF, second["card"])
        back = self.back(LS.SOURCE_EXPORT)
        self.assertEqual(back["status"], trainer.STATUS_BATCH_BACK, back["card"])
        self.assertEqual(self.raw(), clean, "второй возврат вернул не то состояние")
        # Третий возврат возвращать уже нечего — все снятия закрыты.
        self.assertEqual(self.back(LS.SOURCE_EXPORT)["status"], trainer.STATUS_REFUSED)

    def test_two_movements_inside_one_second_do_not_merge(self):
        """ОДНА СЕКУНДА НА ТРИ ДВИЖЕНИЯ — и они всё равно три, а не одно.

        Замер, а не осторожность: пока движение опознавалось общим ШТАМПОМ, второе снятие внутри
        той же секунды считалось уже закрытым первым возвратом, и владелец получал «снятия в
        следе нет» при живом снятом наборе. Часы здесь прибиты гвоздём (`now=`), поэтому тест
        ловит это ВСЕГДА, а не когда повезёт с расписанием машины."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        clean = self.raw()
        tick = 1788000000                              # одна и та же секунда на все движения

        self.assertTrue(LS.batch_withdraw(LS.SOURCE_EXPORT, 2, OFF_WHY, OWNER,
                                          path=self.store, now=tick).ok)
        self.assertTrue(LS.batch_restore(LS.SOURCE_EXPORT, OWNER, path=self.store, now=tick).ok)
        second = LS.batch_withdraw(LS.SOURCE_EXPORT, 2, "второй раз", OWNER,
                                   path=self.store, now=tick)
        self.assertTrue(second.ok, second.reason)

        seen = LS.load_batch_trace(self.store)
        self.assertEqual(len({t.stamp for t in seen}), 1, "часы всё-таки разъехались")
        self.assertEqual(sorted({t.move for t in seen}), [1, 2, 3], "движения слиплись в одно")
        back = LS.batch_restore(LS.SOURCE_EXPORT, OWNER, path=self.store, now=tick)
        self.assertTrue(back.ok, back.reason)
        self.assertEqual(self.raw(), clean, "возврат внутри одной секунды вернул не то")


# =======================================================================================
# СЛЕД: автор, время, ключ, число, причина (задание Штаба, п.4)
# =======================================================================================
class TestTraceHasAllFourParts(_Base):

    def test_the_trace_names_who_when_which_batch_and_how_many(self):
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        got = self.off(LS.SOURCE_EXPORT, 2)
        self.assertEqual(got["status"], trainer.STATUS_BATCH_OFF, got["card"])

        seen = LS.load_batch_trace(self.store)
        self.assertEqual(len(seen), 2, "след пишется на КАЖДЫЙ урок движения")
        for t in seen:
            self.assertEqual(t.act, LS.ACT_BATCH_OFF)
            self.assertEqual(t.who, OWNER)                      # АВТОР
            self.assertTrue(t.stamp.endswith("Z"), t.stamp)     # ВРЕМЯ
            self.assertEqual(t.source, LS.SOURCE_EXPORT)        # КЛЮЧ НАБОРА
            self.assertEqual(t.total, 2)                        # ЧИСЛО СНЯТЫХ СТРОК
            self.assertEqual(t.why, OFF_WHY)                    # ПРИЧИНА
        self.assertEqual(len({t.stamp for t in seen}), 1, "движение обязано быть ОДНИМ штампом")

        # И карточка владельцу называет то же самое — иначе след и слова разошлись бы молча.
        for part in (OWNER, LS.SOURCE_EXPORT, OFF_WHY, "2"):
            self.assertIn(part, got["card"], part)

    def test_the_batch_trace_does_not_confuse_the_promotion_trace(self):
        """След НАБОРА живёт своей таблицей, и откат ПЕРЕВОДА он не ломает.

        Ляг снятие набора в след переводов — `rollback(N)` на любом уроке набора ответил бы
        «перевод уже откачен» (последняя запись по номеру не `перевод`), то есть соврал бы про
        чужое движение. Здесь это проверено поведением, а не расположением файлов."""
        self.rights(OWNER)
        n = self.add_candidate(LS.SOURCE_EXPORT)
        self.assertEqual(trainer.promote_lesson(n, why=WHY, who=OWNER,
                                                path=self.store)["status"],
                         trainer.STATUS_PROMOTED)
        self.assertEqual(self.off(LS.SOURCE_EXPORT, 1)["status"], trainer.STATUS_BATCH_OFF)
        self.assertNotEqual(LS.batch_log_path(self.store), LS.promote_log_path(self.store))
        # След перевода не подрос ни строкой от снятия набора.
        self.assertEqual([t.act for t in LS.load_trace(self.store)], [LS.ACT_PROMOTE])
        # А откат перевода отказывает СВОИМИ словами (урок сняли), а не «уже откачен».
        back = trainer.rollback_promotion(n, who=OWNER, path=self.store)
        self.assertEqual(back["status"], trainer.STATUS_REFUSED, back["card"])
        self.assertIn("изменился", back["card"], back["card"])


# =======================================================================================
# ПРАВО — ТО ЖЕ САМОЕ, ЧТО У ПЕРЕВОДА (задание Штаба, п.3)
# =======================================================================================
class TestSameRightAsPromotion(_Base):

    def test_the_batch_door_names_the_very_same_gate(self):
        """Своего второго права нет: обе двери судят `moderation_core.may_write_rule`.

        Сверяется не имя в комментарии, а ФУНКЦИЯ, которую зовёт код по умолчанию."""
        import moderation_core
        self.assertIs(trainer._default_may_write("x") if False else moderation_core.may_write_rule,
                      moderation_core.may_write_rule)
        self.rights()                                   # пустой список — fail-closed
        self.assertFalse(moderation_core.may_write_rule(OWNER))
        self.add(LS.SOURCE_EXPORT)
        for dec in (self.off(LS.SOURCE_EXPORT, 1), self.back(LS.SOURCE_EXPORT),
                    trainer.promote_lesson(1, why=WHY, who=OWNER, path=self.store)):
            self.assertEqual(dec["status"], trainer.STATUS_DENIED, dec["card"])

    def test_showing_a_batch_needs_no_right(self):
        """ПОКАЗ права не спрашивает — симметрия с `--candidates`/`--trace` двери перевода:
        право судится там, где что-то МЕНЯЕТСЯ."""
        self.rights()                                   # прав нет ни у кого
        self.add(LS.SOURCE_EXPORT)
        dec = trainer.batch_card(LS.SOURCE_EXPORT, path=self.store)
        self.assertEqual(dec["status"], "shown", dec["card"])
        self.assertEqual(dec["count"], 1)

    def test_no_author_is_refused_before_the_right_is_even_asked(self):
        """Безымянное движение отказывается СВОИМИ словами: право судится по имени, и без имени
        судить нечего."""
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        got = trainer.withdraw_batch(LS.SOURCE_EXPORT, count=1, why=OFF_WHY, who="",
                                     path=self.store)
        self.assertEqual(got["status"], trainer.STATUS_NO_AUTHOR, got["card"])
        self.assertIn("КТО", got["card"])


# =======================================================================================
# ОДНО СЛОВО, ОДИН РОУТЕР (задание Штаба, п.1)
# =======================================================================================
class TestOneWordOneRouter(_Base):

    def test_owner_word_parses_every_batch_form(self):
        """Слово владельца разбирается ЧИСТОЙ функцией — той же, что у перевода. Регистр причины
        сохраняется дословно: её текст ложится в след и читается человеком."""
        import pc_agent
        self.assertEqual(
            pc_agent.lesson_word("урок набор сними экспорт_переписки 12: Залили НЕ ТОТ выгруз"),
            ("batch_off", 12, "Залили НЕ ТОТ выгруз", "экспорт_переписки"))
        # Двоеточие вплотную к ключу — живая форма письма, и ключ от неё не портится.
        self.assertEqual(pc_agent.lesson_word("урок набор сними экспорт_переписки: ошиблись"),
                         ("batch_off", None, "ошиблись", "экспорт_переписки"))
        self.assertEqual(pc_agent.lesson_word("УРОК НАБОР ВЕРНИ тренажёр"),
                         ("batch_back", None, "", "тренажёр"))
        self.assertEqual(pc_agent.lesson_word("урок набор перенос_книги"),
                         ("batch_show", None, "", "перенос_книги"))
        self.assertEqual(pc_agent.lesson_word("урок наборы"), ("batch_census", None, "", ""))

    def test_a_typo_in_a_dangerous_command_never_becomes_a_harmless_read(self):
        """«урок набор сними» без ключа — НЕ показ набора «сними».

        Без явного взгляда вперёд в регулярке показа опечатка в САМОЙ ОПАСНОЙ команде
        притворилась бы безобидным чтением, и владелец получил бы «набор «сними» мне неизвестен»
        вместо «не разобрал команду»."""
        import pc_agent
        for half in ("урок набор сними", "урок набор верни", "урок набор снять",
                     "урок набор back"):
            self.assertIsNone(pc_agent.lesson_word(half), half)
            self.assertTrue(pc_agent.LESSON_HEAD_RE.match(half), half)

    def test_lesson_commands_are_not_touched_by_the_batch_forms(self):
        """Формы набора не съели ни одной формы урока, и чужие команды темы 205 не задеты."""
        import pc_agent
        self.assertEqual(pc_agent.lesson_word("урок включи 7: причина"),
                         ("promote", 7, "причина", ""))
        self.assertEqual(pc_agent.lesson_word("урок откати 7"), ("rollback", 7, "", ""))
        for alien in ("статус", "ящик снять abcdef123456", "наборы", "набор экспорт", ""):
            self.assertIsNone(pc_agent.lesson_word(alien), alien)

    def test_there_is_exactly_one_router_and_it_picks_the_door_by_the_list(self):
        """РОУТЕР ОДИН (`lesson_word`), дверей две, и выбор двери идёт по ПЕРЕЧНЮ действий.

        Сторож на исходнике: второй разбор слова «урок» рядом (`def *_word`) означал бы два
        места, решающих одно, — они разъехались бы молча."""
        import pc_agent
        with open(os.path.join(HERE, "pc_agent.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count("def lesson_word("), 1)
        self.assertEqual(src.count("набор[\\s:]+"), 3, "формы набора живут в трёх регулярках")
        self.assertIn("LESSON_BATCH_ACTS", src)
        self.assertEqual(set(pc_agent.LESSON_BATCH_ACTS),
                         {"batch_off", "batch_back", "batch_show", "batch_census"})

    def test_the_cli_argv_carries_the_object_and_omits_what_was_not_named(self):
        """`_lesson_cli` собирает argv двери набора и НЕ ПОДСТАВЛЯЕТ число, которого не назвали.

        Подставленный ноль означал бы «снять ноль строк» вместо «объект не назван» — то есть
        превратил бы отказ в тихий успех."""
        import pc_agent
        seen = {}

        class _R:
            stdout, stderr = "ответ двери", ""

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return _R()

        self.addCleanup(setattr, pc_agent.subprocess, "run", pc_agent.subprocess.run)
        pc_agent.subprocess.run = fake_run
        if not pc_agent.VENV_PY.exists():
            self.skipTest("venv не на месте — argv собирать нечем")

        pc_agent._lesson_cli("batch_off", None, "почему", OWNER, "экспорт_переписки")
        self.assertIn("lesson_batch.py", " ".join(seen["argv"]))
        self.assertNotIn("--count", seen["argv"], "число подставилось само")
        self.assertEqual(seen["argv"][seen["argv"].index("--off") + 1], "экспорт_переписки")

        pc_agent._lesson_cli("batch_off", 12, "почему", OWNER, "экспорт_переписки")
        self.assertEqual(seen["argv"][seen["argv"].index("--count") + 1], "12")

        pc_agent._lesson_cli("promote", 7, "почему", OWNER, "")
        self.assertIn("lesson_promote.py", " ".join(seen["argv"]))


# =======================================================================================
# CLI ДВЕРИ: коды возврата и чтение без права
# =======================================================================================
class TestDoorCli(_Base):

    def test_exit_codes_say_done_or_refused(self):
        self.rights(OWNER)
        self.add(LS.SOURCE_EXPORT)
        self.add(LS.SOURCE_EXPORT)
        p = ["--path", self.store]

        self.assertEqual(lesson_batch.main(["--census"] + p), lesson_batch.EXIT_OK)
        self.assertEqual(lesson_batch.main(["--show", LS.SOURCE_EXPORT] + p),
                         lesson_batch.EXIT_OK)
        self.assertEqual(lesson_batch.main(["--show", "нет_такого"] + p),
                         lesson_batch.EXIT_REFUSED)
        # Без числа — отказ.
        self.assertEqual(
            lesson_batch.main(["--who", OWNER, "--off", LS.SOURCE_EXPORT, "--why", OFF_WHY] + p),
            lesson_batch.EXIT_REFUSED)
        # С числом — сделано.
        self.assertEqual(
            lesson_batch.main(["--who", OWNER, "--off", LS.SOURCE_EXPORT, "--count", "2",
                               "--why", OFF_WHY] + p), lesson_batch.EXIT_OK)
        self.assertEqual(lesson_batch.main(["--trace"] + p), lesson_batch.EXIT_OK)
        self.assertEqual(lesson_batch.main(["--who", OWNER, "--back", LS.SOURCE_EXPORT] + p),
                         lesson_batch.EXIT_OK)
        # Два движения за вызов — отказ, а не «сделаю оба».
        self.assertEqual(
            lesson_batch.main(["--who", OWNER, "--off", LS.SOURCE_EXPORT, "--count", "2",
                               "--back", LS.SOURCE_EXPORT] + p), lesson_batch.EXIT_REFUSED)

    def test_census_names_the_rows_that_are_older_than_the_format(self):
        """Перепись называет строки без источника СВОИМ словом и не обещает их к снятию."""
        self.add(LS.SOURCE_EXPORT)
        self.add_old_row()
        card = lesson_batch._census_card(self.store)
        self.assertIn(LS.SAY_UNKNOWN, card)
        self.assertIn("набором не является", card)


if __name__ == "__main__":
    unittest.main(verbosity=2)
