# -*- coding: utf-8 -*-
"""
test_lesson_write.py — регресс ПУТИ ЗАПИСИ урока владельца КАНДИДАТОМ в базу уроков (05.09.2026).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ и почему именно это. До 05.09 урок из тренажёра (кнопка «🎓 Обучить» и
команда «урок:») уезжал строкой текста в ПЛОСКУЮ книгу `manager-bot/docs/playbook.md`. У такой
записи нет ни автора, ни времени, ни причины, ни номера — откатить её можно только вырезав строку
руками, а ответить «кто и почему это записал» нечем вовсе. Теперь урок ложится КАНДИДАТОМ в
`lesson_store` (вопрос клиента, ответ бота, как правильно, кто записал, когда), а действующим
становится ОТДЕЛЬНЫМ действием и только с непустой причиной.

ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА — сердце набора (класс `TestFourNegatives`), потому что каждый закрывает
свой способ соврать:
  1. пустой список прав → кандидат НЕ пишется (fail-closed: пустое «не настроено» ≠ «можно всем»);
  2. кандидат без причины НЕ переходит в действующие (инвариант «действующий урок всегда с причиной»);
  3. после «Обучить» плоская книга не изменилась НИ БАЙТОМ (сверка sha256 до и после);
  4. `withdraw()` снимает кандидата, и это видно в `active()`/`candidates()` — строка при этом на
     месте (числа строк до и после совпадают).

ПРЕДСМЕРТНЫЙ ВЗГЛЯД задания назван прямо и заперт отдельным тестом
(`test_why_is_never_derived_from_the_lesson_text`): провал этой работы выглядел бы так — причина
подставляется машиной из текста урока, и у каждого урока появляется «почему», которого владелец
не говорил. Поэтому проверяется не «поле заполнено», а «поле ПУСТО и осталось пустым».

БОЕВОГО НЕ КАСАЕМСЯ НИ ОДНОЙ ВЕТКОЙ:
  • таблица уроков — временный файл во временном каталоге (`lesson_store.STORE_PATH` подменён);
  • плоская книга — КОПИЯ во временном каталоге (`suggest.PLAYBOOK_FILE` подменён); живой
    playbook.md здесь только читается один раз, чтобы взять с него копию;
  • состояние тренажёра (moderation_ipc.meta) НЕ открывается: `get`/`set` инъектируются словарём,
    боевая база не трогается ни на чтение;
  • список прав `suggest.APPROVER_USERNAMES` подменяется на время теста и возвращается назад.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_write -v
"""

import hashlib
import os
import shutil
import tempfile
import unittest

import lesson_store as LS
import moderation_core
import suggest
import trainer

HERE = os.path.dirname(os.path.abspath(__file__))

# Живая пара «вопрос клиента / ответ бота» из тренажёра — предмет урока.
# Слова «марки» здесь СОЗНАТЕЛЬНО нет, хотя живая фраза клиента звучит именно так: детектор
# персонального маскирует его как основу имени («Марк»), и вопрос ложится в таблицу как «Какие
# Лицо_1 и модели…». Это ПЕРЕмаскировка чужого словаря (`anonymize_corpus.PROTECTED`), названная
# в шапке `lesson_store.py` и заведомо не чинимая отсюда: расширить словарь ради своего случая =
# молча обесценить замок корпуса на 710 диалогах. Отдельным тестом это поведение зафиксировано
# ниже (`test_the_word_marki_is_overmasked_by_design`), чтобы факт не потерялся.
Q = "Какие модели есть и какие цены на аренду на неделю?"
A = "[тренажёр | ТЕСТ-3 | правил: 9] Есть Yamaha NMAX и Honda PCX. Цену уточню и вернусь."
REMARK = "не пиши «уточню и вернусь», если цена уже посчитана — называй сумму сразу"


class _Base(unittest.TestCase):
    """Своя песочница на каждый тест: таблица уроков и КОПИЯ плоской книги во временном каталоге."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_write_test_")
        self.addCleanup(box.cleanup)
        self.box = box.name
        self.store = os.path.join(self.box, "lesson_store.tsv")
        # Таблица: подменяем МОДУЛЬНЫЙ путь — `_path(None)` читает глобаль в момент вызова, поэтому
        # даже те ветки, которым путь не передают (боевой `trainer._default_add_candidate`), пишут
        # сюда, а не в живой файл.
        self.addCleanup(setattr, LS, "STORE_PATH", LS.STORE_PATH)
        LS.STORE_PATH = self.store
        # Плоская книга: КОПИЯ живого файла (если он есть) — иначе минимальный каркас с секцией.
        self.book = os.path.join(self.box, "playbook.md")
        live = os.path.join(HERE, "manager-bot", "docs", "playbook.md")
        if os.path.isfile(live):
            shutil.copyfile(live, self.book)
        else:
            with open(self.book, "w", encoding="utf-8") as f:
                f.write("# книга\n\n## Выученные правила\n- старое правило\n")
        self.addCleanup(setattr, suggest, "PLAYBOOK_FILE", suggest.PLAYBOOK_FILE)
        suggest.PLAYBOOK_FILE = self.book
        # Состояние тренажёра — словарь в памяти. Боевой moderation_ipc не открывается вовсе.
        self.meta = {trainer.K_INCOMING: Q, trainer.K_ANSWER: A}

    # --- инъекции вместо боевых источников ------------------------------------
    def get(self, key):
        return self.meta.get(key)

    def set(self, key, value):
        self.meta[key] = value

    def rights(self, *names):
        """Подменить список прав на время теста (пустой кортеж = «не настроено»)."""
        self.addCleanup(setattr, suggest, "APPROVER_USERNAMES", suggest.APPROVER_USERNAMES)
        suggest.APPROVER_USERNAMES = {n.lstrip("@").lower() for n in names}

    def book_sha(self):
        with open(self.book, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def rows(self):
        return LS.load(self.store).lessons

    def add_candidate(self, **kw):
        kw.setdefault("question", Q)
        kw.setdefault("bot_answer", A)
        kw.setdefault("correct", REMARK)
        kw.setdefault("who", "filipp")
        kw.setdefault("when", "2026-09-05T10:00:00Z")
        kw.setdefault("path", self.store)
        kw.setdefault("source", LS.SOURCE_TRAINER)          # источник обязателен с 09.09.2026
        return LS.add_candidate(**kw)


# =======================================================================================
# ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА (задание Штаба, п.5)
# =======================================================================================
class TestFourNegatives(_Base):

    def test_1_empty_rights_write_no_candidate(self):
        """ПУСТОЙ список прав — это «не настроено», а не «можно всем»: кандидат НЕ пишется.

        Проверяется не только исход-слово, но и ФАЙЛ: таблицы не должно появиться вовсе."""
        self.rights()                                  # список прав пуст
        dec = trainer.lesson_candidate(REMARK, who="@filipp", get=self.get)
        self.assertEqual(dec["status"], trainer.STATUS_DENIED, dec["card"])
        self.assertIsNone(dec["n"])
        self.assertFalse(os.path.exists(self.store),
                         "при пустом списке прав таблица уроков всё-таки была создана")
        # И через боевой вход тоже — тот, которым зовёт кнопка.
        got = trainer.apply_lessons([REMARK], who="@filipp", get=self.get, set=self.set,
                                    classify=lambda r: "behavior")
        self.assertEqual(got["accepted"], [])
        self.assertEqual(got["errors"], [REMARK])
        self.assertFalse(os.path.exists(self.store))

    def test_2_candidate_without_why_does_not_become_active(self):
        """Кандидат без причины НЕ переходит в действующие. Причина не берётся ниоткуда."""
        n = self.add_candidate()
        res = LS.promote(n, path=self.store)                  # причину не назвали
        self.assertFalse(res.ok)
        self.assertEqual(res.reason, LS.REASON_PROMOTE_NO_WHY)
        rows = self.rows()
        self.assertEqual(len(LS.active(rows)), 0, "урок без причины стал действующим")
        self.assertEqual([l.number for l in LS.candidates(rows)], [n])
        self.assertEqual(rows[0].why, "", "в «почему» что-то подставилось само")
        # Пробел причиной не является: «   » — это по-прежнему пусто.
        self.assertFalse(LS.promote(n, why="   ", path=self.store).ok)
        self.assertEqual(len(LS.active(self.rows())), 0)

    def test_3_teach_leaves_the_flat_book_byte_identical(self):
        """После «Обучить» плоская книга не изменилась НИ БАЙТОМ — сверка sha256 до и после."""
        self.rights("filipp")
        before = self.book_sha()
        size_before = os.path.getsize(self.book)
        dec = trainer.apply_lessons([REMARK], who="@filipp", get=self.get, set=self.set,
                                    classify=lambda r: "behavior")
        self.assertEqual(len(dec["accepted"]), 1, dec["card"])
        self.assertEqual(self.book_sha(), before, "плоская книга изменилась после «Обучить»")
        self.assertEqual(os.path.getsize(self.book), size_before)
        # И кандидат при этом ЛЁГ — иначе «книга не изменилась» доказывало бы лишь бездействие.
        rows = self.rows()
        self.assertEqual(len(LS.candidates(rows)), 1)
        self.assertEqual(rows[0].correct, REMARK)

    def test_4_withdraw_takes_the_candidate_off_and_active_shows_it(self):
        """`withdraw()` снимает кандидата; строка при этом ОСТАЁТСЯ (числа строк совпадают)."""
        n_live = self.add_candidate()
        LS.promote(n_live, why="клиент дважды спросил цену и не получил её", who="filipp",
                   path=self.store)
        n_cand = self.add_candidate(correct="второй урок")
        self.assertEqual([l.number for l in LS.active(self.rows())], [n_live])
        self.assertEqual([l.number for l in LS.candidates(self.rows())], [n_cand])

        # снимаем ДЕЙСТВУЮЩИЙ — уходит из active()
        res = LS.withdraw(number=n_live, path=self.store)
        self.assertEqual(res.marked, (n_live,))
        self.assertEqual(res.lines_before, res.lines_after)
        self.assertEqual([l.number for l in LS.active(self.rows())], [])

        # снимаем КАНДИДАТА — уходит из candidates() и в active() не появляется
        res2 = LS.withdraw(number=n_cand, path=self.store)
        self.assertEqual(res2.marked, (n_cand,), "снятие не увидело кандидата: %r" % (res2,))
        self.assertEqual(res2.lines_before, res2.lines_after, "снятие потеряло строку файла")
        rows = self.rows()
        self.assertEqual(LS.candidates(rows), ())
        self.assertEqual(LS.active(rows), ())
        self.assertEqual(len(rows), 2, "снятие удалило строку вместо пометки")
        self.assertTrue(LS.parse_state(rows[1].state)[0])
        # снятый кандидат в действующие уже не переводится
        self.assertFalse(LS.promote(n_cand, why="передумал", who="filipp", path=self.store).ok)


# =======================================================================================
# Предсмертный взгляд: причина НЕ подставляется
# =======================================================================================
class TestWhyIsNeverInvented(_Base):

    def test_why_is_never_derived_from_the_lesson_text(self):
        """Кандидат ложится с ПУСТЫМ «почему», хотя текст урока длинный и «объясняющий».

        Это и есть тот способ провалить задачу, который назван предсмертным взглядом: машина
        сочиняет причину из текста урока, и владелец больше не отличает свою причину от чужой."""
        self.rights("filipp")
        wordy = ("всегда называй сумму сразу, потому что клиент уже назвал даты и ждёт цену, "
                 "а не обещание вернуться")
        dec = trainer.lesson_candidate(wordy, who="filipp", get=self.get)
        self.assertEqual(dec["status"], trainer.STATUS_CANDIDATE, dec["card"])
        row = self.rows()[0]
        self.assertEqual(row.why, "", "причина подставилась из текста урока: %r" % row.why)
        self.assertEqual(row.state, LS.STATE_CANDIDATE)
        self.assertIn("причина", dec["card"].lower())

    def test_promote_takes_the_named_why_and_only_it(self):
        """Названная причина ложится дословно (после вычистки персонального), строк не теряя."""
        n = self.add_candidate()
        why = "клиент спросил цену прямым текстом, а бот ушёл в «вернусь» — это потеря заявки"
        res = LS.promote(n, why=why, who="filipp", path=self.store)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(res.lines_before, res.lines_after)
        row = self.rows()[0]
        self.assertEqual(row.state, LS.STATE_ACTIVE)
        self.assertEqual(row.why, why)
        self.assertEqual([l.number for l in LS.active(self.rows())], [n])
        # Повторный перевод уже действующего урока — названный отказ, а не тихое «ок».
        again = LS.promote(n, why=why, who="filipp", path=self.store)
        self.assertFalse(again.ok)
        self.assertIn("не кандидат", again.reason)

    def test_candidate_may_carry_a_why_from_the_start(self):
        """Кандидат ВПРАВЕ иметь причину сразу — тогда `promote` берёт её из строки."""
        n = self.add_candidate(why="цена была посчитана и не названа")
        res = LS.promote(n, who="filipp", path=self.store)
        self.assertTrue(res.ok, res.reason)
        self.assertEqual(self.rows()[0].why, "цена была посчитана и не названа")


# =======================================================================================
# Путь записи: что именно ложится в кандидата
# =======================================================================================
class TestCandidateFields(_Base):

    def test_five_fields_land_from_the_trainer_session(self):
        """Вопрос клиента и ответ бота берутся из ПАРЫ сессии, автор и время — свои."""
        self.rights("filipp")
        dec = trainer.lesson_candidate(REMARK, who="@filipp", when="2026-09-05T12:00:00Z",
                                       get=self.get)
        self.assertEqual(dec["status"], trainer.STATUS_CANDIDATE, dec["card"])
        row = self.rows()[0]
        self.assertEqual(row.question, Q)
        self.assertEqual(row.bot_answer, A)
        self.assertEqual(row.correct, REMARK)
        self.assertEqual(row.who, "filipp")
        self.assertEqual(row.when, "2026-09-05T12:00:00Z")
        self.assertEqual(row.state, LS.STATE_CANDIDATE)
        self.assertEqual(LS.by_author(self.rows(), "@FILIPP")[0].number, row.number)
        self.assertEqual(LS.by_day(self.rows(), "2026-09-05")[0].number, row.number)

    def test_no_author_writes_nothing(self):
        """Автора не назвал никто — кандидат НЕ пишется (его нечем ни проверить, ни отозвать)."""
        self.rights("filipp")
        dec = trainer.lesson_candidate(REMARK, who=None, get=self.get)
        self.assertEqual(dec["status"], trainer.STATUS_NO_AUTHOR)
        self.assertFalse(os.path.exists(self.store))

    def test_no_pair_writes_nothing(self):
        """Нет пары «вопрос клиента / ответ бота» — кандидат НЕ пишется: предмета урока нет."""
        self.rights("filipp")
        self.meta[trainer.K_ANSWER] = ""
        dec = trainer.lesson_candidate(REMARK, who="filipp", get=self.get)
        self.assertEqual(dec["status"], trainer.STATUS_NO_PAIR)
        self.assertFalse(os.path.exists(self.store))

    def test_rights_come_from_the_write_point_not_from_the_general_check(self):
        """Право берётся из ТОЧКИ ЗАПИСИ (`may_write_rule`), а не из общей проверки модерации.

        Разница видна ровно на пустом списке: общая проверка там говорит «да» ВСЕМ (и это не
        дефект — иначе на пустом env обездвижилась бы вся модерация), точка записи — «нет»."""
        self.rights()
        self.assertTrue(suggest.is_approver("кто угодно"), "общая проверка изменила поведение")
        self.assertFalse(moderation_core.may_write_rule("кто угодно"))
        seen = []
        trainer.lesson_candidate(REMARK, who="кто угодно", get=self.get,
                                 may_write=lambda u: seen.append(u) or False)
        self.assertEqual(seen, ["кто угодно"], "право спрошено не про того, кто пишет")

    def test_the_word_marki_is_overmasked_by_design(self):
        """ЧЕСТНАЯ ГРАНИЦА, а не дефект этого шага: живое «какие марки и модели» ложится в таблицу
        как «какие Лицо_1 и модели» — детектор видит в «марки» основу имени «Марк».

        Записано тестом, чтобы факт не открывали заново: цена выбора «сомнительный случай — в
        пользу приватности» названа числом в шапке `lesson_store.py`, а чинить её здесь нельзя —
        `PROTECTED` живёт в `anonymize_corpus`, и его полноту держит замок корпуса на 710
        диалогах. Тест упадёт, если кто-нибудь молча расширит словарь: это будет поводом
        пересчитать чужой замер, а не тихо порадоваться."""
        self.rights("filipp")
        self.meta[trainer.K_INCOMING] = "Какие марки и модели есть?"
        trainer.lesson_candidate(REMARK, who="filipp", get=self.get)
        self.assertIn("Лицо_1", self.rows()[0].question,
                      "перемаскировка «марки» исчезла — словарь детектора изменили, "
                      "и замер корпуса на 710 диалогах надо пересчитать")

    def test_personal_data_is_scrubbed_on_the_way_in(self):
        """Чистка персонального работает и на кандидате — выключателя у неё нет."""
        self.rights("filipp")
        self.meta[trainer.K_INCOMING] = "Здравствуйте, это +66 812345678, хочу NMAX"
        trainer.lesson_candidate(REMARK, who="filipp", get=self.get)
        row = self.rows()[0]
        self.assertNotIn("812345678", row.question)
        self.assertEqual(row.who, "filipp", "автор вычищен — разрез отката «автор» сломан")


# =======================================================================================
# Автор одного следующего урока (кросс-процессный разрыв «✍ другое»)
# =======================================================================================
class TestActorIsSingleUse(_Base):

    def test_take_pending_lesson_records_the_author_once(self):
        trainer.start_pending_lesson("filipp", now=1000, set=self.set)
        self.assertTrue(trainer.take_pending_lesson("@filipp", now=1000,
                                                    get=self.get, set=self.set))
        self.assertEqual(trainer.peek_actor(now=1000, get=self.get), "filipp")
        self.assertEqual(trainer.take_actor(now=1000, get=self.get, set=self.set), "filipp")
        self.assertIsNone(trainer.take_actor(now=1000, get=self.get, set=self.set),
                          "имя автора осталось липким и подпишет чужой следующий урок")

    def test_actor_expires(self):
        trainer.set_actor("filipp", now=1000, set=self.set)
        self.assertEqual(trainer.peek_actor(now=1000 + trainer.ACTOR_TTL_SEC, get=self.get),
                         "filipp")
        self.assertIsNone(trainer.peek_actor(now=1000 + trainer.ACTOR_TTL_SEC + 1, get=self.get))
        # протухшее имя гасится тоже: оно не должно дождаться следующего урока
        self.assertIsNone(trainer.take_actor(now=1000 + trainer.ACTOR_TTL_SEC + 1,
                                             get=self.get, set=self.set))
        self.assertEqual(self.meta[trainer.K_ACTOR], "")

    def test_one_press_one_author_for_all_marked(self):
        """Две отмеченные гипотезы = два кандидата ОДНОГО автора: запись автора одноразовая, и
        забирать её должен пакет, а не каждый урок по очереди."""
        self.rights("filipp")
        trainer.set_actor("filipp", set=self.set)      # часы настоящие: apply_lessons зовёт take_actor без now
        dec = trainer.apply_lessons(["первый урок", "второй урок"], get=self.get, set=self.set,
                                    classify=lambda r: "behavior")
        self.assertEqual(len(dec["accepted"]), 2, dec["card"])
        self.assertEqual([r.who for r in self.rows()], ["filipp", "filipp"])
        self.assertEqual([r.state for r in self.rows()],
                         [LS.STATE_CANDIDATE, LS.STATE_CANDIDATE])
        self.assertIn("кандидат", dec["card"].lower())
        self.assertNotIn("применятся со следующего ответа", dec["card"])


# =======================================================================================
# Инварианты устройства (запреты, а не поведение)
# =======================================================================================
class TestInvariants(unittest.TestCase):

    def test_live_behaviour_path_never_calls_the_flat_book(self):
        """Боевой путь урока не зовёт `suggest.append_playbook_rule` ни одной веткой.

        Подменяем сам аппендер взрывом: если хоть одна ветка боевого пути его позовёт, тест
        упадёт с этим именем, а не молча запишет строку в книгу."""
        box = tempfile.TemporaryDirectory(prefix="lesson_write_inv_")
        self.addCleanup(box.cleanup)
        self.addCleanup(setattr, LS, "STORE_PATH", LS.STORE_PATH)
        LS.STORE_PATH = os.path.join(box.name, "lesson_store.tsv")
        self.addCleanup(setattr, suggest, "APPROVER_USERNAMES", suggest.APPROVER_USERNAMES)
        suggest.APPROVER_USERNAMES = {"filipp"}

        def boom(*a, **kw):
            raise AssertionError("боевой путь урока позвал suggest.append_playbook_rule")

        self.addCleanup(setattr, suggest, "append_playbook_rule", suggest.append_playbook_rule)
        suggest.append_playbook_rule = boom
        meta = {trainer.K_INCOMING: Q, trainer.K_ANSWER: A}
        dec = trainer.apply_lessons([REMARK], who="filipp", get=meta.get,
                                    set=meta.__setitem__, classify=lambda r: "behavior")
        self.assertEqual(len(dec["accepted"]), 1, dec["card"])

    def test_active_state_is_written_in_exactly_two_places(self):
        """`актив` присваивается ровно в двух местах — `_add_row` (через `add`) и `promote`, и обе
        дороги требуют непустой причины. Третьей дороги в действующие нет, и появиться она не
        должна незаметно: этот тест — сторож на исходнике."""
        with open(os.path.join(HERE, "lesson_store.py"), encoding="utf-8") as f:
            src = f.read()
        writes = [ln.strip() for ln in src.splitlines()
                  if "STATE_ACTIVE" in ln and ("IDX_STATE" in ln or "state," in ln
                                               or "STATE_ACTIVE, True" in ln)]
        self.assertEqual(len(writes), 2,
                         "мест, кладущих состояние «актив», стало не два: %s" % writes)

    def test_add_still_refuses_empty_why(self):
        """Прямая запись ДЕЙСТВУЮЩЕГО урока по-прежнему требует причину: инвариант не ослаблен."""
        box = tempfile.TemporaryDirectory(prefix="lesson_write_add_")
        self.addCleanup(box.cleanup)
        store = os.path.join(box.name, "lesson_store.tsv")
        with self.assertRaises(LS.LessonRejected) as cm:
            LS.add(Q, A, REMARK, "", "filipp", source=LS.SOURCE_TRAINER,
                   when="2026-09-05T10:00:00Z", path=store)
        self.assertEqual(cm.exception.field, LS.COL_WHY)
        self.assertFalse(os.path.exists(store))

    def test_add_names_why_before_source_when_both_are_missing(self):
        """ПОРЯДОК ОТКАЗОВ, а не просто «оба обязательны». Источник заведён 09.09.2026 и
        проверяется ПОСЛЕ шести полей: у вызова, где не названо ничего, отказ обязан назвать
        более старое и более жёсткое требование владельца («почему»), иначе новая проверка
        перехватила бы чужие отказы и спрятала их причину."""
        box = tempfile.TemporaryDirectory(prefix="lesson_write_order_")
        self.addCleanup(box.cleanup)
        store = os.path.join(box.name, "lesson_store.tsv")
        with self.assertRaises(LS.LessonRejected) as cm:
            LS.add(Q, A, REMARK, "", "filipp", when="2026-09-05T10:00:00Z", path=store)
        self.assertEqual(cm.exception.field, LS.COL_WHY, cm.exception.reason)
        # …а при названной причине тот же вызов упирается уже в источник
        with self.assertRaises(LS.LessonRejected) as cm2:
            LS.add(Q, A, REMARK, "причина есть", "filipp", when="2026-09-05T10:00:00Z", path=store)
        self.assertEqual(cm2.exception.field, LS.COL_SOURCE, cm2.exception.reason)
        self.assertFalse(os.path.exists(store), "отказ оставил файл таблицы")


if __name__ == "__main__":
    unittest.main(verbosity=2)
