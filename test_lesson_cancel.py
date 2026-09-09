# -*- coding: utf-8 -*-
"""
test_lesson_cancel.py — регресс ОТМЕНЫ УРОКА ПО НОМЕРУ: команда владельца → отзыв в базе (06.09.2026).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ И ПОЧЕМУ ИМЕННО ЭТО. С коммита 8bfb55b секция «Выученные правила» в промпте
собирается из БАЗЫ уроков (`suggest.load_playbook` → `lesson_store.active`), а плоская книга стала
снимком. Команда «отмени урок N» при этом продолжала вырезать буллет ИЗ КНИГИ: владелец получал
карточку «↩️ Отменил урок #N», а бот продолжал отвечать по этому уроку — в базе он оставался
действующим и ехал в каждый промпт. Успешная карточка при неснятом уроке опаснее отказа, поэтому
команда переведена на `lesson_store.withdraw`, и здесь это меряется КОНТРФАКТОМ НА ОТВЕТЕ, а не
чтением кода.

ЧЕТЫРЕ ВХОДА КОНТРФАКТА (сердце набора, класс `TestCounterfactFourInputs`):
  1. ДО отмены ответ несёт урок — текст урока лежит в промпте;
  2. ПОСЛЕ отмены не несёт И ВОЗВРАЩАЕТСЯ К ПРЕЖНЕМУ — промпт посимвольно равен тому, что был до
     появления урока (не «похож», а равен: иначе отмена могла бы оставить свой след в ответе);
  3. ПОВТОРНАЯ отмена того же номера не ломается и говорит «уже отменён» — ни исключения, ни
     ложного «снял ещё раз»;
  4. отмена НЕСУЩЕСТВУЮЩЕГО номера — внятный отказ; таблица при этом не изменилась ни байтом
     (sha256 до и после), то есть отказ не «тихий успех».

ПРАВО (класс `TestRight`) — то же самое, что у перевода кандидата в действующие:
`moderation_core.may_write_rule`, fail-closed (пустой список прав = НИКОМУ). Отказ обязан быть
СЛОВАМИ и без единого касания таблицы.

СЛЕД СНЯТОГО УРОКА (класс `TestTrace`) — пункт задания «что происходит с отменённым уроком»:
строка ОСТАЁТСЯ (число строк до и после равно), состояние становится `снят(урок;штамп)`, урок
по-прежнему читается по своему номеру. Исчезнувший урок нельзя ни объяснить, ни отменить саму
отмену — поэтому это проверяется числом, а не словом.

СОСЕДНЯЯ ПОСЫЛКА (класс `TestNeighbourFallback`): у чтения базы есть ТРЕТИЙ ИСХОД — «база не
прочитана», и тогда в промпт идёт книга-снимок. Снятый урок обязан не вернуться и там.

БОЕВОГО НЕ КАСАЕМСЯ НИ ОДНОЙ ВЕТКОЙ:
  • таблица уроков — временный файл (`lesson_store.STORE_PATH` подменён, и путь передаётся явно);
  • плоская книга — КОПИЯ во временном каталоге (`suggest.PLAYBOOK_FILE`), живая только читается;
  • состояние тренажёра (moderation_ipc) не открывается: `get` инъектируется словарём;
  • прибор регрессии не запускается: `regress` инъектируется шпионом, живой `spawn` не зовётся;
  • список прав `suggest.APPROVER_USERNAMES` подменяется на время теста и возвращается назад.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_cancel -v
"""

import hashlib
import os
import shutil
import tempfile
import unittest

import lesson_regress
import lesson_store as LS
import suggest
import trainer

HERE = os.path.dirname(os.path.abspath(__file__))

Q = "Сколько стоит аренда на неделю?"
A = "[тренажёр | ТЕСТ-3] Уточню и вернусь."
RULE = "называй сумму сразу, если она уже посчитана"
WHY = "клиент дважды спросил цену и не получил её"


class _Base(unittest.TestCase):
    """Своя песочница на каждый тест: таблица уроков и КОПИЯ книги во временном каталоге."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_cancel_test_")
        self.addCleanup(box.cleanup)
        self.box = box.name
        self.store = os.path.join(self.box, "lesson_store.tsv")
        # Таблица: подменяем и МОДУЛЬНЫЙ путь (на случай ветки без явного path), и передаём path.
        self.addCleanup(setattr, LS, "STORE_PATH", LS.STORE_PATH)
        LS.STORE_PATH = self.store
        # Книга: КОПИЯ живой (если есть) — иначе каркас с секцией выученных правил.
        self.book = os.path.join(self.box, "playbook.md")
        live = os.path.join(HERE, "manager-bot", "docs", "playbook.md")
        if os.path.isfile(live):
            shutil.copyfile(live, self.book)
        else:
            with open(self.book, "w", encoding="utf-8") as f:
                f.write("# книга\n\n## Стиль общения\n- коротко\n\n## Выученные правила\n"
                        "- старое правило книги\n")
        self.addCleanup(setattr, suggest, "PLAYBOOK_FILE", suggest.PLAYBOOK_FILE)
        suggest.PLAYBOOK_FILE = self.book
        # Промпт читает базу ОТСЮДА же — иначе контрфакт мерил бы живую таблицу машины.
        self.addCleanup(setattr, suggest, "LESSON_BASE_PATH", suggest.LESSON_BASE_PATH)
        suggest.LESSON_BASE_PATH = self.store
        self.addCleanup(setattr, suggest, "LESSON_BASE_OFF", suggest.LESSON_BASE_OFF)
        suggest.LESSON_BASE_OFF = False
        # БОКОВОЙ СПИСОК ИСТОЧНИКОВ — ТОЖЕ В ПЕСОЧНИЦУ, и это не перестраховка, а замер: снятие
        # урока зовёт `unmark_source`, а тот пишет в БОЕВОЙ `trainer_rules.json` в корне репо.
        # На мутационной пробе 06.09 незакрытая дыра стоила живой записи бокового списка (её
        # заметил чужой набор `test_lesson_migrate_book`, меряющий этот файл живым).
        self.addCleanup(setattr, trainer, "TRAINER_RULES_FILE", trainer.TRAINER_RULES_FILE)
        trainer.TRAINER_RULES_FILE = os.path.join(self.box, "trainer_rules.json")
        self.meta = {}
        self.spawned = []                      # шпион прибора регрессии: (rule, n, act)

    # --- инъекции вместо боевых источников ------------------------------------
    def get(self, key):
        return self.meta.get(key)

    def set(self, key, value):
        self.meta[key] = value

    def regress(self, rule, n=None, act=None):
        self.spawned.append((rule, n, act))
        return {"spawned": True, "why": ""}

    def rights(self, *names):
        """Подменить список прав на время теста (пустой вызов = «не настроено»)."""
        self.addCleanup(setattr, suggest, "APPROVER_USERNAMES", suggest.APPROVER_USERNAMES)
        suggest.APPROVER_USERNAMES = {n.lstrip("@").lower() for n in names}

    # --- сущности песочницы ----------------------------------------------------
    def add_active(self, correct=RULE, why=WHY, who="filipp", when="2026-09-06T10:00:00Z"):
        """Действующий урок в базе (через штатный путь: кандидат → promote с причиной и автором).

        `who=` у перевода обязателен с 09.09.2026: перевод оставляет след из четырёх частей
        (автор, время, номер, откат), и безымянный перевод хранилище отклоняет само."""
        n = LS.add_candidate(question=Q, bot_answer=A, correct=correct, who=who, when=when,
                             path=self.store, source=LS.SOURCE_TRAINER)
        res = LS.promote(n, why=why, who=who, path=self.store)
        self.assertTrue(res.ok, res.reason)
        return n

    def add_candidate(self, correct="кандидат без причины", who="filipp"):
        return LS.add_candidate(question=Q, bot_answer=A, correct=correct, who=who,
                                when="2026-09-06T10:00:00Z", path=self.store,
                                source=LS.SOURCE_TRAINER)   # источник обязателен с 09.09.2026

    def cancel(self, n, **kw):
        """Боевой вход команды со всеми путями в песочницу (прибор — шпион)."""
        kw.setdefault("who", "@filipp")
        kw.setdefault("path", self.store)
        kw.setdefault("regress", self.regress)
        kw.setdefault("get", self.get)
        return trainer.cancel_lesson(n, **kw)

    def prompt(self):
        """Книга В ТОМ ВИДЕ, В КАКОМ ОНА ИДЁТ В ПРОМПТ — предмет контрфакта."""
        return suggest.load_playbook()

    def store_sha(self):
        with open(self.store, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def rows(self):
        return LS.load(self.store).lessons

    def row(self, n):
        for les in self.rows():
            if les.number == n:
                return les
        return None

    def lines(self):
        with open(self.store, encoding="utf-8") as f:
            return sum(1 for _ in f)


# =======================================================================================
# КОНТРФАКТ — ЧЕТЫРЕ ВХОДА (задание Штаба, п.3)
# =======================================================================================
class TestCounterfactFourInputs(_Base):

    def test_1_and_2_answer_carries_the_lesson_then_returns_to_the_previous_prompt(self):
        """Вход 1 и вход 2 одним замером: до отмены урок в ответе есть, после — нет, и промпт
        ПОСИМВОЛЬНО равен прежнему.

        Равенство берётся не для красоты: «похожий» промпт означал бы, что отмена оставила в
        ответе свой след (пустой заголовок секции, лишний перевод строки), и бот продолжал бы
        видеть место, где урок был."""
        self.rights("filipp")
        self.add_candidate()                       # таблица существует, действующих уроков нет
        before = self.prompt()
        self.assertNotIn(RULE, before)

        n = self.add_active()
        during = self.prompt()
        self.assertIn(RULE, during, "действующий урок не доехал до промпта — мерить нечего")

        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        after = self.prompt()
        self.assertNotIn(RULE, after, "снятый урок остался в промпте")
        self.assertEqual(after, before, "промпт после отмены не вернулся к прежнему")

    def test_3_second_cancel_of_the_same_number_says_already_and_breaks_nothing(self):
        """Вход 3: повторная отмена того же номера — не исключение и не ложный успех."""
        self.rights("filipp")
        n = self.add_active()
        first = self.cancel(n)
        self.assertEqual(first["status"], trainer.STATUS_WITHDRAWN, first["card"])
        sha_after_first, lines_after_first = self.store_sha(), self.lines()

        second = self.cancel(n)
        self.assertEqual(second["status"], trainer.STATUS_ALREADY, second["card"])
        self.assertIn("уже отменён", second["card"])
        self.assertIn(str(n), second["card"])
        self.assertEqual(self.store_sha(), sha_after_first,
                         "повторная отмена всё-таки переписала таблицу")
        self.assertEqual(self.lines(), lines_after_first)
        # И прибор регрессии на повторе НЕ дёргается: мерить нечего, ответ не менялся.
        self.assertEqual(len(self.spawned), 1, "повтор запустил лишний прогон корпуса")

    def test_4_cancel_of_a_missing_number_refuses_in_words_and_writes_nothing(self):
        """Вход 4: несуществующий номер — внятный отказ, таблица не изменилась НИ БАЙТОМ."""
        self.rights("filipp")
        n = self.add_active()
        before_sha, before_prompt = self.store_sha(), self.prompt()

        dec = self.cancel(n + 990)
        self.assertEqual(dec["status"], trainer.STATUS_NOT_FOUND, dec["card"])
        self.assertIn("НЕТ", dec["card"])
        self.assertTrue(dec["card"].strip(), "отказ обязан быть словами, а не тишиной")
        self.assertEqual(self.store_sha(), before_sha, "отказ всё-таки тронул таблицу")
        self.assertEqual(self.prompt(), before_prompt, "отказ изменил ответ")
        self.assertEqual([l.number for l in LS.active(self.rows())], [n],
                         "действующий урок пострадал от промаха номером")
        self.assertEqual(self.spawned, [], "промах номером запустил прогон корпуса")

    def test_missing_store_is_named_apart_from_missing_number(self):
        """«Таблицы нет» и «номера нет» — РАЗНЫЕ ответы: чинятся они по-разному."""
        self.rights("filipp")
        dec = self.cancel(1)
        self.assertEqual(dec["status"], trainer.STATUS_NO_STORE, dec["card"])
        self.assertFalse(os.path.exists(self.store), "отмена создала таблицу на пустом месте")


# =======================================================================================
# ПРАВО — ТО ЖЕ, ЧТО У ПЕРЕВОДА КАНДИДАТА В ДЕЙСТВУЮЩИЕ (задание Штаба, п.2)
# =======================================================================================
class TestRight(_Base):

    def test_empty_rights_deny_the_cancel_fail_closed(self):
        """Пустой список прав = НИКОМУ (fail-closed): урок остаётся действующим и в ответе."""
        self.rights()                                   # «не настроено»
        n = self.add_active()
        before = self.store_sha()

        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_DENIED, dec["card"])
        self.assertIn("Нет прав", dec["card"])
        self.assertEqual(self.store_sha(), before, "отказ по праву всё-таки тронул таблицу")
        self.assertEqual([l.number for l in LS.active(self.rows())], [n])
        self.assertIn(RULE, self.prompt(), "отказанная отмена изменила ответ")
        self.assertEqual(self.spawned, [])

    def test_stranger_is_denied_and_owner_is_not(self):
        """Право судится ПО ИМЕНИ и тем же гейтом, что у записи: чужой получает отказ словами."""
        self.rights("filipp")
        n = self.add_active()
        stranger = self.cancel(n, who="@postoronniy")
        self.assertEqual(stranger["status"], trainer.STATUS_DENIED, stranger["card"])
        self.assertEqual([l.number for l in LS.active(self.rows())], [n])

        owner = self.cancel(n, who="@filipp")
        self.assertEqual(owner["status"], trainer.STATUS_WITHDRAWN, owner["card"])

    def test_the_gate_is_the_same_one_promote_names(self):
        """ОДИН И ТОТ ЖЕ ГЕЙТ, а не два похожих: отмена ходит через `moderation_core.may_write_rule`.

        Меряется поведением на общей развилке, а не грепом: при пустом списке прав общая проверка
        модерации (`suggest.is_approver`) отвечает «да» ЛЮБОМУ, а строгая — «нет». Если бы отмена
        сидела на общей проверке, чужой снял бы урок при пустом env."""
        import moderation_core
        self.rights()
        self.assertTrue(suggest.is_approver("kto_ugodno"),
                        "предпосылка развилки изменилась: общая проверка перестала пускать всех")
        self.assertFalse(moderation_core.may_write_rule("kto_ugodno"))
        n = self.add_active()
        self.assertEqual(self.cancel(n, who="kto_ugodno")["status"], trainer.STATUS_DENIED)

    def test_nameless_cancel_is_refused_in_words(self):
        """Имени нет — права нет: отмена без автора отказывается СЛОВАМИ и таблицу не трогает.

        Автор берётся из записи кнопки (`peek_actor`); её нет — судить право не по чему, а
        «не по чему» у fail-closed означает «нет», и владельцу говорят, чем это чинится."""
        self.rights("filipp")
        n = self.add_active()
        before = self.store_sha()
        dec = self.cancel(n, who=None)
        self.assertEqual(dec["status"], trainer.STATUS_NO_AUTHOR, dec["card"])
        self.assertIn("КТО", dec["card"])
        self.assertEqual(self.store_sha(), before)
        self.assertEqual([l.number for l in LS.active(self.rows())], [n])

    def test_actor_of_the_button_is_taken_but_not_burned(self):
        """Автор кнопки годится для отмены — и ОСТАЁТСЯ на месте: гашение отняло бы его у
        следующего «урок: …», а отмена чужую запись портить не вправе."""
        self.rights("filipp")
        n = self.add_active()
        trainer.set_actor("filipp", now=1000, set=self.set)
        dec = self.cancel(n, who=None, now=1000)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(dec["who"], "filipp")
        self.assertEqual(trainer.peek_actor(now=1000, get=self.get), "filipp",
                         "отмена сожгла запись автора, приготовленную для урока")


# =======================================================================================
# СЛЕД СНЯТОГО УРОКА (задание Штаба, п.5)
# =======================================================================================
class TestTrace(_Base):

    def test_withdrawn_lesson_stays_in_the_table_with_a_mark(self):
        """Отменённый урок ОСТАЁТСЯ с отметкой, а не исчезает: числа строк равны, состояние
        названо разрезом и штампом, урок читается по своему номеру."""
        self.rights("filipp")
        n = self.add_active()
        lines_before = self.lines()
        row_before = self.row(n)

        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(dec["lines_before"], dec["lines_after"],
                         "снятие потеряло строку — это удаление, а не отзыв")
        self.assertEqual(self.lines(), lines_before)

        row_after = self.row(n)
        self.assertIsNotNone(row_after, "снятый урок исчез из таблицы")
        gone, cut, stamp = LS.parse_state(row_after.state)
        self.assertTrue(gone)
        self.assertEqual(cut, LS.CUT_ONE, "разрез снятия не назван «урок»")
        self.assertTrue(stamp, "у снятия нет штампа времени — объяснить его нечем")
        # всё, кроме состояния, осталось дословно: урок читаем и после отмены
        self.assertEqual(row_after.correct, row_before.correct)
        self.assertEqual(row_after.why, row_before.why)
        self.assertEqual(row_after.who, row_before.who)
        self.assertEqual(row_after.when, row_before.when)
        # карточка владельцу говорит то же самое числом и словом
        self.assertIn("НЕ удалена", dec["card"])
        self.assertIn(RULE[:20], dec["card"], "карточка не называет ТЕКСТ снятого урока")

    def test_number_is_the_base_number_not_the_book_bullet(self):
        """Номер — номер БАЗЫ. Проверяется там, где нумерации разъезжаются: часть уроков базы
        в книге не была никогда, поэтому «третий буллет» и «урок #3» — разные вещи."""
        self.rights("filipp")
        n1 = self.add_active(correct="первый урок базы")
        n2 = self.add_active(correct="второй урок базы")
        self.assertEqual((n1, n2), (1, 2))
        dec = self.cancel(n2)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual([l.number for l in LS.active(self.rows())], [n1],
                         "снят не тот урок, что назвали номером")

    def test_candidate_can_be_cancelled_too(self):
        """Ошибочный КАНДИДАТ снимается той же командой — иначе он лежал бы в таблице вечно
        (в `active()` его нет, и «уже снят» о нём было бы неправдой)."""
        self.rights("filipp")
        n = self.add_candidate()
        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(LS.candidates(self.rows()), ())
        self.assertIsNotNone(self.row(n), "кандидат исчез из таблицы вместо пометки")


# =======================================================================================
# СОСЕДНЯЯ ПОСЫЛКА: КНИГА-СНИМОК И ТРЕТИЙ ИСХОД ЧТЕНИЯ (задание Штаба, п.6)
# =======================================================================================
class TestNeighbourFallback(_Base):

    def _book_bullets(self):
        return [r["rule"] for r in suggest.list_playbook_rules()]

    def test_withdrawn_lesson_does_not_come_back_through_the_snapshot_book(self):
        """База не прочитана → в промпт идёт книга-снимок. Снятый урок обязан не вернуться и там:
        иначе отмена держалась бы только на исправности файла базы."""
        self.rights("filipp")
        # книга-снимок держит тот же буллет, что и база (так было в день переезда)
        with open(self.book, "a", encoding="utf-8") as f:
            f.write("- (2026-09-06) %s\n" % RULE)
        self.assertIn(RULE, self._book_bullets())
        n = self.add_active()

        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(dec["book"]["status"], "removed", dec["book"])
        self.assertNotIn(RULE, self._book_bullets())
        # ТРЕТИЙ ИСХОД: базу «не прочитать» — промпт возвращается к книге, и урока там нет
        suggest.LESSON_BASE_PATH = os.path.join(self.box, "нет-такой-таблицы.tsv")
        self.assertNotIn(RULE, suggest.load_playbook(),
                         "снятый урок вернулся в ответ через книгу-снимок")

    def test_book_is_left_alone_when_the_match_is_not_exactly_one(self):
        """Совпало не ровно одно — книгу НЕ ТРОГАЕМ. Нечёткий матчинг вырезал бы соседнее
        правило молча, а не тронуть дешевле, чем испортить."""
        self.rights("filipp")
        with open(self.book, "a", encoding="utf-8") as f:
            f.write("- (2026-09-06) %s\n- (2026-09-06) %s\n" % (RULE, RULE))
        n = self.add_active()
        before = self._book_bullets()
        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(dec["book"]["status"], "ambiguous", dec["book"])
        self.assertEqual(self._book_bullets(), before, "книгу тронули при неоднозначном совпадении")
        self.assertIn("несколько", dec["card"], "владельцу не сказали, что книга не поправлена")

    def test_lesson_that_never_was_in_the_book_cancels_quietly(self):
        """Урока в книге не было (все уроки после переезда такие) — отмена всё равно проходит,
        а книга остаётся байт в байт."""
        self.rights("filipp")
        n = self.add_active(correct="правило, которого в книге не было никогда")
        with open(self.book, "rb") as f:
            before = hashlib.sha256(f.read()).hexdigest()
        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(dec["book"]["status"], "skipped", dec["book"])
        with open(self.book, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), before)


# =======================================================================================
# ПРИБОР РЕГРЕССИИ — ОБЕ СТОРОНЫ (задание Штаба, п.4)
# =======================================================================================
class TestRegressBothWays(_Base):

    def test_cancel_starts_the_corpus_run_and_names_the_movement(self):
        """Отмена меняет ответ — значит меряется тем же корпусом, что и запись, и движение
        называется своим словом («снят»), иначе строка исхода соврала бы про свой повод."""
        self.rights("filipp")
        n = self.add_active()
        dec = self.cancel(n)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertEqual(len(self.spawned), 1, "отмена не запустила регрессию")
        rule, num, act = self.spawned[0]
        self.assertEqual(num, n)
        self.assertEqual(act, trainer.ACT_WITHDRAWN)
        self.assertEqual(rule, RULE, "в прибор уехал не текст снятого урока")

    def test_broken_instrument_does_not_undo_the_cancel(self):
        """Прибор упал — урок ВСЁ РАВНО снят: он снят ДО прибора, и падение измерителя не имеет
        права превратить состоявшуюся отмену в «не отменено»."""
        self.rights("filipp")
        n = self.add_active()

        def boom(*_a, **_k):
            raise RuntimeError("прибор сломан")

        dec = self.cancel(n, regress=boom)
        self.assertEqual(dec["status"], trainer.STATUS_WITHDRAWN, dec["card"])
        self.assertFalse(dec["regress"]["spawned"])
        self.assertEqual(LS.active(self.rows()), ())

    def test_outcome_line_says_written_or_withdrawn(self):
        """Строка исхода прибора называет ОБА движения своими словами, включая смешанную склейку."""
        rec = {"cases_ok": 16, "cases_seen": 16, "checks_ok": 300, "checks_all": 300, "sec": 420}
        added = lesson_regress.outcome_line({"verdict": "ok"}, rec, [{"n": 12}])
        self.assertIn("Урок #12 записан", added)          # умолчание = как до 06.09
        gone = lesson_regress.outcome_line({"verdict": "ok"}, rec,
                                           [{"n": 12, "act": lesson_regress.ACT_WITHDRAWN}])
        self.assertIn("Урок #12 снят", gone)
        mixed = lesson_regress.outcome_line({"verdict": "ok"}, rec,
                                            [{"n": 12}, {"n": 13,
                                                         "act": lesson_regress.ACT_WITHDRAWN}])
        self.assertIn("#12 (записан)", mixed)
        self.assertIn("#13 (снят)", mixed)

    def test_red_run_after_a_cancel_does_not_advise_cancelling_again(self):
        """Красный исход после ОТМЕНЫ не советует «снять — отмени урок N»: снимать уже нечего,
        а обратной команды у полосы нет — прибор об этом говорит, а не выдумывает."""
        rec = {"cases_ok": 14, "cases_seen": 16, "checks_ok": 280, "checks_all": 300, "sec": 420}
        broken = {"verdict": "broken",
                  "broken": [{"id": "3", "checks": [{"name": "цена", "expected": "590",
                                                     "fact": "уточню"}]}]}
        gone = lesson_regress.outcome_line(broken, rec,
                                           [{"n": 12, "act": lesson_regress.ACT_WITHDRAWN}])
        self.assertNotIn("отмени урок", gone)
        self.assertIn("Отмена НЕ откатана", gone)
        added = lesson_regress.outcome_line(broken, rec, [{"n": 12}])
        self.assertIn("отмени урок 12", added)            # сторона записи не изменилась


# =======================================================================================
# ОБРАБОТЧИК НЕ ПАДАЕТ
# =======================================================================================
class TestNeverRaises(_Base):

    def test_cancel_never_raises(self):
        """Исключение внутри отмены не роняет обработчик группы: status='error' + карточка."""
        self.rights("filipp")

        def boom(*_a, **_k):
            raise RuntimeError("таблица сломана")

        dec = self.cancel(7, withdraw=boom)
        self.assertEqual(dec["status"], "error")
        self.assertIn("не отменён", dec["card"])
        self.assertIn("RuntimeError", dec["card"])

    def test_unparsable_number_is_named_not_crashed(self):
        """Не число — внятный ответ, а не исключение и не отмена наугад."""
        self.rights("filipp")
        dec = self.cancel("двенадцать")
        self.assertEqual(dec["status"], "error")
        self.assertIn("номер", dec["card"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
