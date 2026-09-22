# -*- coding: utf-8 -*-
"""
test_lesson_promote.py — регресс ДВЕРИ «кандидат → действующий урок» (09.09.2026).

ЧТО ЗАКРЫВАЕТСЯ И ЧЕМ ЭТО БЫЛО ДО. `lesson_store.promote` живёт с 05.09, и до 09.09 его не звал
НИ ОДИН файл дерева, кроме тестов: кнопка «🎓 Обучить» писала КАНДИДАТА, кандидата не читает
`active()`, а перевести его в действующие было нечем. Петля обучения не замыкалась — вердикт
владельца не становился правилом ни при каком его старании. Дверь — `lesson_promote.py` (CLI) и
слово владельца в теме 205 (`pc_agent.lesson_word` → тот же CLI субпроцессом).

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА — сердце набора (`TestThreeNegatives`), потому что каждый закрывает свой
способ соврать:
  1. перевод БЕЗ ПРИЧИНЫ отказывается — и «почему» остаётся пустым, а не заполняется машиной;
  2. откат по номеру возвращает прежнее состояние ПОБАЙТНО (sha256 всей таблицы до перевода
     равен sha256 после отката) — а не «примерно то же»;
  3. при СНЯТОМ праве тот же самый вход даёт ДРУГОЙ ответ. Проверяется не только слово отказа, но
     и различие двух ответов: тест, который не сравнивает их между собой, зеленел бы и на двери,
     которая права не спрашивает вовсе.

ПРЕДСМЕРТНЫЙ ВЗГЛЯД, названный прямо: провал этой работы выглядел бы так — перевод стал ПОБОЧНЫМ
СЛЕДСТВИЕМ вердикта («Обучить» сразу включает урок), и цена ошибки записи молча сравнялась с ценой
ошибки применения. Поэтому здесь есть отдельный класс `TestVerdictStillOnlyWritesACandidate`: он
проверяет не «перевод работает», а «вердикт НЕ переводит».

БОЕВОГО НЕ КАСАЕМСЯ НИ ОДНОЙ ВЕТКОЙ:
  • таблица уроков и её след — временные файлы во временном каталоге (`lesson_store.STORE_PATH`
    подменён; путь следа производный от таблицы, поэтому уезжает туда же сам);
  • список прав `suggest.APPROVER_USERNAMES` подменяется на время теста и возвращается назад;
  • состояние тренажёра (moderation_ipc) не открывается: `get`/`set` — словарь в памяти;
  • Telegram не трогается вовсе: `pc_agent.lesson_word` — чистая функция, голденится словами.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_promote -v
"""

import hashlib
import os
import tempfile
import unittest

import lesson_promote
import lesson_store as LS
import suggest
import trainer

HERE = os.path.dirname(os.path.abspath(__file__))

# Живая пара тренажёра — предмет урока (правило-класс «голдены на дословных фразах»).
Q = "Какие модели есть и какие цены на аренду на неделю?"
A = "[тренажёр | ТЕСТ-3 | правил: 9] Есть Yamaha NMAX и Honda PCX. Цену уточню и вернусь."
REMARK = "не пиши «уточню и вернусь», если цена уже посчитана — называй сумму сразу"
WHY = "клиент спросил цену прямым текстом, а бот ушёл в «вернусь» — это потеря заявки"
OWNER = "filipp"


class _Base(unittest.TestCase):
    """Своя таблица уроков и свой след на каждый тест. Боевой файл не задевается вовсе."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_promote_test_")
        self.addCleanup(box.cleanup)
        self.box = box.name
        self.store = os.path.join(self.box, "lesson_store.tsv")
        # `_path(None)` читает модульную глобаль В МОМЕНТ ВЫЗОВА — подмена уводит сюда и те ветки,
        # которым путь не передают.
        self.addCleanup(setattr, LS, "STORE_PATH", LS.STORE_PATH)
        LS.STORE_PATH = self.store
        # ЖИВОЙ ЧИТАТЕЛЬ ПРАВИЛ (22.09.2026): дверь перевода сверяет собранный промпт, а читатель берёт
        # и книгу. Книга уводится во временный каталог, чтение базы — включено и по умолчанию смотрит
        # в подменённый `STORE_PATH`: боевые книга и база не читаются ни одной веткой теста.
        for name in ("PLAYBOOK_FILE", "LESSON_BASE_PATH", "LESSON_BASE_OFF"):
            self.addCleanup(setattr, suggest, name, getattr(suggest, name))
        suggest.PLAYBOOK_FILE = os.path.join(self.box, "playbook.md")
        suggest.LESSON_BASE_PATH = None
        suggest.LESSON_BASE_OFF = False
        self.meta = {trainer.K_INCOMING: Q, trainer.K_ANSWER: A}

    def get(self, key):
        return self.meta.get(key)

    def set(self, key, value):
        self.meta[key] = value

    def rights(self, *names):
        """Подменить список прав на время теста (пустой = «не настроено», fail-closed = НИКОМУ)."""
        self.addCleanup(setattr, suggest, "APPROVER_USERNAMES", suggest.APPROVER_USERNAMES)
        suggest.APPROVER_USERNAMES = {n.lstrip("@").lower() for n in names}

    def rows(self):
        return LS.load(self.store).lessons

    def add_candidate(self, **kw):
        kw.setdefault("question", Q)
        kw.setdefault("bot_answer", A)
        kw.setdefault("correct", REMARK)
        kw.setdefault("who", OWNER)
        kw.setdefault("when", "2026-09-09T10:00:00Z")
        kw.setdefault("path", self.store)
        kw.setdefault("source", LS.SOURCE_TRAINER)          # источник обязателен с 09.09.2026
        return LS.add_candidate(**kw)

    def sha(self):
        with open(self.store, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def raw(self):
        with open(self.store, "rb") as f:
            return f.read()


# =======================================================================================
# ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА (задание Штаба, п.6)
# =======================================================================================
class TestThreeNegatives(_Base):

    def test_1_promote_without_why_refuses_and_invents_nothing(self):
        """Перевод без причины ОТКАЗЫВАЕТСЯ, и «почему» остаётся пустым.

        Проверяется не только слово отказа: если бы дверь молча подставила причину из текста
        урока, отказа не было бы вовсе, а поле перестало бы отличать названное от придуманного."""
        self.rights(OWNER)
        n = self.add_candidate()
        before = self.sha()

        dec = trainer.promote_lesson(n, why="", who=OWNER, get=self.get, path=self.store)
        self.assertEqual(dec["status"], trainer.STATUS_NO_WHY, dec["card"])
        self.assertIn("причина не названа", dec["card"])
        self.assertEqual(LS.active(self.rows()), (), "урок без причины стал действующим")
        self.assertEqual(self.rows()[0].why, "", "в «почему» что-то подставилось само")
        self.assertEqual(self.sha(), before, "отказ всё-таки тронул таблицу")

        # Пробелы причиной не являются — и это тот же отказ, а не другой.
        again = trainer.promote_lesson(n, why="   \n  ", who=OWNER, get=self.get, path=self.store)
        self.assertEqual(again["status"], trainer.STATUS_NO_WHY, again["card"])
        self.assertEqual(self.sha(), before)
        # Следа тоже не появилось: отказавший перевод не движение.
        self.assertEqual(LS.load_trace(self.store), ())

    def test_2_rollback_returns_the_previous_state_byte_for_byte(self):
        """Откат по номеру возвращает прежнее состояние ПОБАЙТНО — sha256 всей таблицы.

        Байты, а не смысл: круг «разобрать → собрать» не тождественен на чужой экранированной
        последовательности, и «ровно то состояние» перестало бы быть правдой на первом же уроке,
        который правили руками. Поэтому здесь в тексте урока СТОИТ такая последовательность."""
        self.rights(OWNER)
        n = self.add_candidate(correct=REMARK + " (см. пометку \\q в книге)")
        before_bytes = self.raw()
        before = self.sha()

        got = trainer.promote_lesson(n, why=WHY, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(got["status"], trainer.STATUS_PROMOTED, got["card"])
        self.assertEqual([l.number for l in LS.active(self.rows())], [n])
        self.assertNotEqual(self.sha(), before, "перевод ничего не изменил в таблице")

        back = trainer.rollback_promotion(n, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(back["status"], trainer.STATUS_ROLLED_BACK, back["card"])
        self.assertEqual(self.raw(), before_bytes,
                         "откат вернул НЕ те байты, что были до перевода")
        self.assertEqual(self.sha(), before)
        # И смыслом тоже: снова кандидат, снова без причины, строка на месте.
        rows = self.rows()
        self.assertEqual([l.number for l in LS.candidates(rows)], [n])
        self.assertEqual(LS.active(rows), ())
        self.assertEqual(rows[0].why, "")
        self.assertEqual(back["lines_before"], back["lines_after"])

    def test_3_with_the_right_removed_the_same_input_answers_differently(self):
        """ТОТ ЖЕ вход при снятом праве даёт ДРУГОЙ ответ — и урок не включается.

        Сравниваются два ответа между собой, а не только слово одного: дверь, которая права не
        спрашивает вовсе, дала бы на оба входа один и тот же зелёный ответ."""
        n = self.add_candidate()

        self.rights(OWNER)                                   # право ЕСТЬ
        allowed = trainer.promote_lesson(n, why=WHY, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(allowed["status"], trainer.STATUS_PROMOTED, allowed["card"])
        trainer.rollback_promotion(n, who=OWNER, get=self.get, path=self.store)

        suggest.APPROVER_USERNAMES = set()                   # право СНЯТО (список пуст)
        denied = trainer.promote_lesson(n, why=WHY, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(denied["status"], trainer.STATUS_DENIED, denied["card"])
        self.assertNotEqual(denied["card"], allowed["card"],
                            "при снятом праве дверь ответила ТО ЖЕ САМОЕ")
        self.assertNotEqual(denied["status"], allowed["status"])
        self.assertEqual(LS.active(self.rows()), (), "при снятом праве урок всё-таки включился")

        # И поимённо: посторонний при НЕПУСТОМ списке — тоже отказ.
        self.rights(OWNER)
        alien = trainer.promote_lesson(n, why=WHY, who="postoronniy_0909", get=self.get,
                                       path=self.store)
        self.assertEqual(alien["status"], trainer.STATUS_DENIED, alien["card"])
        self.assertEqual(LS.active(self.rows()), ())


# =======================================================================================
# Предсмертный взгляд: вердикт по-прежнему только ЗАПИСЫВАЕТ
# =======================================================================================
class TestVerdictStillOnlyWritesACandidate(_Base):

    def test_teach_button_writes_a_candidate_and_turns_nothing_on(self):
        """Нажатие «🎓 Обучить» кладёт КАНДИДАТА и не включает ничего: `active()` пуст, следа нет."""
        self.rights(OWNER)
        dec = trainer.apply_lessons([REMARK], who=OWNER, get=self.get, set=self.set,
                                    classify=lambda r: "behavior")
        self.assertEqual(len(dec["accepted"]), 1, dec["card"])
        rows = self.rows()
        self.assertEqual(len(LS.candidates(rows)), 1)
        self.assertEqual(LS.active(rows), (), "вердикт включил урок сам")
        self.assertEqual(LS.load_trace(self.store), (), "у вердикта появился след перевода")

    def test_no_source_line_calls_promote_from_the_verdict_path(self):
        """СТОРОЖ НА ИСХОДНИКЕ: путь вердикта не зовёт перевод ни одной строкой.

        Поведенческий тест выше поймал бы только сегодняшнюю ветку; этот ловит и завтрашнюю,
        добавленную «чтобы было удобнее»."""
        with open(os.path.join(HERE, "trainer.py"), encoding="utf-8") as f:
            src = f.read()
        # Режем по ЗАГОЛОВКУ раздела двери: всё до него — путь записи урока и путь отмены, и
        # именно там перевода быть не должно. Резать по `def _promote_lesson(` нельзя — выше него
        # законно живёт `_default_promote`, инъекционная точка самой двери.
        head, sep, _tail = src.partition("# ====== ПЕРЕВОД КАНДИДАТА В ДЕЙСТВУЮЩИЕ")
        self.assertTrue(sep, "в trainer.py не стало двери перевода — тест смотрит не туда")
        for bad in ("lesson_store.promote(", "_default_promote("):
            self.assertNotIn(bad, head,
                             "путь записи урока начал звать перевод сам: %r" % bad)


# =======================================================================================
# След перевода: четыре части (автор, время, номер, откат)
# =======================================================================================
class TestTraceHasFourParts(_Base):

    def test_trace_names_author_time_number_and_is_rollbackable(self):
        self.rights(OWNER)
        n = self.add_candidate()
        got = trainer.promote_lesson(n, why=WHY, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(got["status"], trainer.STATUS_PROMOTED, got["card"])

        seen = LS.load_trace(self.store)
        self.assertEqual(len(seen), 1, "перевод не оставил следа")
        rec = seen[0]
        self.assertEqual(rec.act, LS.ACT_PROMOTE)          # что за движение
        self.assertEqual(rec.number, n)                    # номер
        self.assertEqual(rec.who, OWNER)                   # автор
        self.assertTrue(rec.stamp.endswith("Z") and len(rec.stamp) == 20, rec.stamp)  # время
        self.assertEqual(rec.prev_state, LS.STATE_CANDIDATE)
        # Четвёртая часть — ОТКАТ: он существует и приписывается своему автору.
        back = trainer.rollback_promotion(n, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(back["status"], trainer.STATUS_ROLLED_BACK, back["card"])
        seen2 = LS.load_trace(self.store)
        self.assertEqual([t.act for t in seen2], [LS.ACT_PROMOTE, LS.ACT_ROLLBACK])
        self.assertEqual(seen2[-1].who, OWNER)
        # Карточка называет владельцу все четыре части — иначе след есть, но его не видно.
        for part in ("включил", OWNER, str(n), "откати"):
            self.assertIn(part, got["card"], got["card"])

    def test_promote_without_author_is_refused_by_the_store_itself(self):
        """Имя требует ХРАНИЛИЩЕ, а не только дверь: обойти его новым вызывающим нельзя."""
        n = self.add_candidate()
        res = LS.promote(n, why=WHY, who="", path=self.store)
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, LS.REASON_PROMOTE_NO_WHO)
        self.assertEqual(LS.active(self.rows()), ())
        self.assertEqual(LS.load_trace(self.store), ())

    def test_rollback_refuses_twice_and_refuses_after_the_lesson_moved(self):
        """Откат один раз на один перевод; после снятия урока откат НЕ ТРОГАЕТ ничего."""
        self.rights(OWNER)
        n = self.add_candidate()
        trainer.promote_lesson(n, why=WHY, who=OWNER, get=self.get, path=self.store)
        first = trainer.rollback_promotion(n, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(first["status"], trainer.STATUS_ROLLED_BACK, first["card"])

        second = trainer.rollback_promotion(n, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(second["status"], trainer.STATUS_REFUSED, second["card"])
        self.assertIn("уже откачен", second["card"])

        # Перевели снова, потом СНЯЛИ — откат обязан отказаться, а не воскресить снятый урок.
        trainer.promote_lesson(n, why=WHY, who=OWNER, get=self.get, path=self.store)
        LS.withdraw(number=n, path=self.store)
        after = self.sha()
        moved = trainer.rollback_promotion(n, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(moved["status"], trainer.STATUS_REFUSED, moved["card"])
        self.assertEqual(self.sha(), after, "отказавший откат всё-таки тронул таблицу")
        self.assertEqual(LS.active(self.rows()), ())

    def test_rollback_without_trace_is_named_not_silent(self):
        n = self.add_candidate()
        res = LS.rollback(n, who=OWNER, path=self.store)
        self.assertFalse(res.ok)
        self.assertIn("в следе нет", res.reason)


# =======================================================================================
# ДВЕРЬ: CLI и слово владельца
# =======================================================================================
class TestTheDoorItself(_Base):

    def test_cli_promotes_refuses_and_rolls_back_with_exit_codes(self):
        """CLI — живой вызывающий, а не тест: коды возврата отличают сделанное от отказа."""
        self.rights(OWNER)
        n = self.add_candidate()
        self.assertEqual(lesson_promote.main(["--candidates", "--path", self.store]), 0)
        self.assertEqual(
            lesson_promote.main(["--who", OWNER, "--promote", str(n), "--path", self.store]),
            lesson_promote.EXIT_REFUSED, "перевод без причины вернул код успеха")
        self.assertEqual(
            lesson_promote.main(["--who", OWNER, "--promote", str(n), "--why", WHY,
                                 "--path", self.store]), lesson_promote.EXIT_OK)
        self.assertEqual([l.number for l in LS.active(self.rows())], [n])
        self.assertEqual(
            lesson_promote.main(["--who", OWNER, "--rollback", str(n), "--path", self.store]),
            lesson_promote.EXIT_OK)
        self.assertEqual(LS.active(self.rows()), ())
        # Два движения в одном вызове — отказ, а не «сделаю оба».
        self.assertEqual(
            lesson_promote.main(["--who", OWNER, "--promote", "1", "--rollback", "1",
                                 "--path", self.store]), lesson_promote.EXIT_REFUSED)

    def test_owner_word_is_parsed_and_keeps_the_reason_case(self):
        """Слово владельца в теме 205 разбирается ЧИСТОЙ функцией, регистр причины сохраняется.

        Регистр здесь не косметика: причина ложится в таблицу уроков дословно и читается человеком,
        а роутер темы 205 работает на `msg.text.lower()` — разбор на нём испортил бы её навсегда."""
        import pc_agent
        # ЧЕТВЁРТОЕ ПОЛЕ (ключ набора) добавлено 09.09.2026 и у команд ОДНОГО урока всегда пусто.
        # Голдены обновлены здесь, а не обойдены срезом `[:3]`: срез спрятал бы ровно ту ошибку,
        # ради которой они стоят, — уехавшее не в то поле значение.
        self.assertEqual(pc_agent.lesson_word("урок включи 7: Клиент дважды спросил цену"),
                         ("promote", 7, "Клиент дважды спросил цену", ""))
        self.assertEqual(pc_agent.lesson_word("УРОК ОТКАТИ 7"), ("rollback", 7, "", ""))
        self.assertEqual(pc_agent.lesson_word("урок кандидаты"), ("candidates", None, "", ""))
        self.assertEqual(pc_agent.lesson_word("урок след"), ("trace", None, "", ""))
        # Чужие команды темы 205 не задеты ни одной формой.
        for alien in ("статус", "ящик снять abcdef123456", "обнови userbot", ""):
            self.assertIsNone(pc_agent.lesson_word(alien), alien)
        # Почти команда опознаётся как почти команда, а не как перевод.
        self.assertIsNone(pc_agent.lesson_word("урок включи"))
        self.assertTrue(pc_agent.LESSON_HEAD_RE.match("урок включи"))

    def test_the_door_names_the_same_gate_the_write_path_names(self):
        """ОДИН И ТОТ ЖЕ ГЕЙТ, а не два похожих: перевод ходит через `may_write_rule`."""
        import moderation_core
        self.assertIs(trainer._default_may_write.__wrapped__
                      if hasattr(trainer._default_may_write, "__wrapped__")
                      else trainer._default_may_write, trainer._default_may_write)
        self.rights()                                        # список прав ПУСТ
        self.assertFalse(moderation_core.may_write_rule(OWNER))
        n = self.add_candidate()
        dec = trainer.promote_lesson(n, why=WHY, who=OWNER, get=self.get, path=self.store)
        self.assertEqual(dec["status"], trainer.STATUS_DENIED, dec["card"])


# =======================================================================================
# П.5 задания 08.09: поля ИСТОЧНИКА НАБОРА у урока НЕ БЫЛО — замок на факт и на его закрытие
# =======================================================================================
class TestNoBatchSourceColumnYet(unittest.TestCase):
    """ЧИТАЮЩИЙ замок, а не требование. Задание Штаба спросило отдельной строкой: есть ли у урока
    поле ИСТОЧНИКА набора и можно ли откатить набор одного источника целиком. 08.09 ответ был
    НЕТ, и он записан здесь ЧИСЛОМ, чтобы следующий шаг начинался с факта, а не с пересказа.

    09.09.2026 ответ СТАЛ ДА, и класс переписан, а не удалён: он держит ровно то, что закрывало
    прежний пробел, и краснеет, если хвост источника или четвёртый разрез из кода уйдут. Прежние
    утверждения («колонок восемь», «разрезов три») сохранены как ИСТОРИЯ формата — восьмиколоночная
    часть по-прежнему восемь колонок, потому что лежащие строки не переписывались."""

    def test_columns_grew_by_a_tail_and_the_old_eight_are_untouched(self):
        # прежний факт: восемь колонок, среди них источника нет — он верен ДЛЯ СТАРОЙ ЧАСТИ
        self.assertEqual(len(LS.COLUMNS_BASE), 8, LS.COLUMNS_BASE)
        for name in LS.COLUMNS_BASE:
            self.assertNotIn("источник", name)
        # факт 09.09: девятая колонка — источник, и она ПОСЛЕДНЯЯ в своей редакции
        self.assertEqual(len(LS.COLUMNS_SRC), 9, LS.COLUMNS_SRC)
        self.assertEqual(LS.COLUMNS_SRC[-1], LS.COL_SOURCE)
        self.assertEqual(LS.COLUMNS_SRC[:8], LS.COLUMNS_BASE)
        # факт 20.09: десятая и одиннадцатая — партия и режим, и они тоже ХВОСТ, а не вставка
        self.assertEqual(len(LS.COLUMNS), 11, LS.COLUMNS)
        self.assertEqual(LS.COLUMNS[-2:], (LS.COL_BATCH, LS.COL_MODE))
        self.assertEqual(LS.COLUMNS[:9], LS.COLUMNS_SRC)
        # …и ни один индекс старых колонок не сдвинулся — это и есть «читается по-прежнему»
        self.assertEqual(LS.IDX_STATE, LS.COLUMNS_BASE.index(LS.COL_STATE))
        self.assertEqual(LS.IDX_WHY, LS.COLUMNS_BASE.index(LS.COL_WHY))
        self.assertEqual(LS.IDX_SOURCE, LS.COLUMNS_SRC.index(LS.COL_SOURCE))

    def test_cuts_are_five_and_the_fifth_is_the_batch(self):
        """Разрезов снятия ПЯТЬ — урок, день, автор, источник, партия. Четыре прежних стоят на
        своих местах и в прежнем порядке: пятый добавлен хвостом, как и колонка."""
        self.assertEqual(LS.CUTS,
                         (LS.CUT_ONE, LS.CUT_DAY, LS.CUT_WHO, LS.CUT_SOURCE, LS.CUT_BATCH))
        # прежде это бросало ValueError — теперь это законное состояние
        state = LS.withdrawn_state(LS.CUT_SOURCE, "2026-09-09T10:00:00Z")
        self.assertEqual(state, "снят(источник;2026-09-09T10:00:00Z)")
        gone, cut, stamp = LS.parse_state(state)
        self.assertTrue(gone)
        self.assertEqual(cut, LS.CUT_SOURCE, "регулярка состояния не знает четвёртого разреза")
        self.assertEqual(stamp, "2026-09-09T10:00:00Z")
        # ПЯТЫЙ разрез виден В САМОЙ СТРОКЕ и своим словом: снятие одной заливки не притворяется
        # снятием всего набора
        lot_state = LS.withdrawn_state(LS.CUT_BATCH, "2026-09-20T10:00:00Z")
        self.assertEqual(lot_state, "снят(партия;2026-09-20T10:00:00Z)")
        self.assertNotEqual(lot_state, LS.withdrawn_state(LS.CUT_SOURCE, "2026-09-20T10:00:00Z"))
        gone2, cut2, _stamp2 = LS.parse_state(lot_state)
        self.assertTrue(gone2)
        self.assertEqual(cut2, LS.CUT_BATCH, "регулярка состояния не знает пятого разреза")
        # а вот ШЕСТОГО разреза по-прежнему нет, и незнание остаётся ГРОМКИМ
        with self.assertRaises(ValueError):
            LS.withdrawn_state("набор", "2026-09-09T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
