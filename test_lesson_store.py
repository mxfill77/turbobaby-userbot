# -*- coding: utf-8 -*-
"""
test_lesson_store.py — регресс хранилища уроков тренажёра (`lesson_store.py`).

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА, названные владельцем, стоят первыми классами — они и есть предмет:
  (а) `TestWhyIsMandatory`  — урок без «почему» НЕ записан, причина названа словами;
  (б) `TestWithdrawKeepsRow` — снятие оставляет строку на месте и лишь метит её;
  (в) `TestPersonalDataNeverLands` — телефон и имя из входного текста в таблицу НЕ попали,
      а остальной смысл урока цел.

Класс (в) держит и ЦЕНУ чистки, а не только её пользу: ПЕРЕмаскировку местоимения в звательной
позиции и то, что поле из одного телефона становится МЕТКОЙ, а не пустеет. Оба ожидания сняты
с ЖИВОГО детектора пробой (`_scratch_lessonstore_0819/probe_scrub.py`), а не выписаны «как
удобно тесту»: правило-класс «мок обязан копировать живой формат». Разбор — в
docs/artifacts/2026-08-19-lesson-store.md §3.

Дальше — замки на требования, которые легко потерять молча:
  • `TestNothingEverDeletes` — «уроки не исчезают НИКОГДА»: и по ФАКТУ (двести уроков переживают
    три снятия), и по ИСХОДНИКУ (в модуле нет ни `os.remove`, ни `os.truncate`, а единственный
    усекающий `open(..., "w")` открывает ВРЕМЕННЫЙ файл, а не таблицу). Второй слой нужен потому,
    что первый не заметит ротацию, добавленную завтра «на всякий случай»;
  • `TestOneLineOneLesson` — правило «строка файла = урок» держится байтами даже когда в тексте
    урока живут табуляция и перевод строки;
  • `TestReaderSaysNonparse` — непустая, но неразобранная таблица даёт НЕРАЗБОР, а не «уроков нет»
    (контракт `parse_outcome`, класс «нуль по неразбору»);
  • `TestCapacityNeverLoses` — за порогом комфорта хранилище говорит «тесно» и продолжает писать;
  • `TestBotNotWired` — граница шага: круг файлов, ходящих в хранилище, назван ПОИМЁННО
    (с 06.09 их два — `trainer.py` пишет кандидата, `suggest.py` читает действующих), а утечка
    кандидата к клиенту проверяется НАПРЯМУЮ: отбор в `suggest.py` идёт только `active()`.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_store -v
"""

import ast
import os
import re
import tempfile
import unittest

import lesson_store as LS
import parse_outcome

HERE = os.path.dirname(os.path.abspath(__file__))

# Живой урок, на котором стоят почти все проверки: реальная фраза клиента (правило-класс
# «голдены детекта — дословные фразы»), реальный промах бота и реальная поправка.
Q_LIVE = ("Здравствуйте! Меня зовут Сергей, мой номер +66 81 234 5678. "
          "Сколько стоит Yamaha NMAX на неделю в Раваи?")
BOT_LIVE = "Уточню и вернусь с ответом."
RIGHT_LIVE = "NMAX на неделю — 3520 бат, доставка в Раваи 590 бат."
WHY_LIVE = "цена уже известна из прайса, «уточню и вернусь» тянет время и теряет клиента"
WHO_LIVE = "manager_ivan"
WHEN_LIVE = "2026-08-19T09:00:00Z"

# ГОЛДЕН, СНЯТЫЙ С ЖИВОГО ДЕТЕКТОРА (проба `_scratch_lessonstore_0819/probe_scrub.py`,
# 19.08.2026), а не выписанный «как удобно тесту»: правило-класс «мок обязан копировать живой
# формат». Здесь дословно то, что ложится в таблицу из Q_LIVE — вместе с ПЕРЕмаскировкой
# местоимения «Меня» (см. `test_greeting_position_overmasks_by_design`).
Q_LIVE_SCRUBBED = ("Здравствуйте! Лицо_2 зовут Лицо_1, мой номер <телефон_1>. "
                   "Сколько стоит Yamaha NMAX на неделю в Раваи?")


class _Base(unittest.TestCase):
    """Своя таблица во временном каталоге на каждый тест: боевой файл не задевается вовсе."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_store_test_")
        self.addCleanup(box.cleanup)
        self.store = os.path.join(box.name, "lesson_store.tsv")

    def add_live(self, **over):
        # `source` обязателен с 09.09.2026 (см. `TestSourceIsMandatory`): запись без источника
        # отказывается, поэтому песочница называет его по умолчанию.
        kw = dict(question=Q_LIVE, bot_answer=BOT_LIVE, correct=RIGHT_LIVE, why=WHY_LIVE,
                  who=WHO_LIVE, when=WHEN_LIVE, path=self.store, source=LS.SOURCE_TRAINER)
        kw.update(over)
        return LS.add(**kw)

    def raw(self):
        with open(self.store, encoding="utf-8", newline="") as f:
            return f.read()


# =======================================================================================
# (а) ОТРИЦАТЕЛЬНЫЙ ТЕСТ: урок без «почему» не записывается
# =======================================================================================
class TestWhyIsMandatory(_Base):
    """Единственное жёсткое требование владельца: без «почему» урок НЕ принимается."""

    def test_empty_why_is_rejected_with_named_reason(self):
        for empty in ("", "   ", "\t", "\n  \n"):
            with self.assertRaises(LS.LessonRejected) as box:
                self.add_live(why=empty)
            err = box.exception
            self.assertEqual(err.field, LS.COL_WHY,
                             "отказ обязан назвать ИМЕННО поле «почему», названо: %r" % err.field)
            self.assertIn("почему", err.reason.lower(),
                          "причина отказа обязана называть поле словами: %r" % err.reason)
            self.assertIn("НЕ записан", err.reason,
                          "причина обязана сказать, что урок не записан: %r" % err.reason)

    def test_rejected_lesson_leaves_no_trace(self):
        """Отказ — это НЕ полузапись: ни файла, ни строки, ни съеденного номера."""
        with self.assertRaises(LS.LessonRejected):
            self.add_live(why="")
        self.assertFalse(os.path.isfile(self.store),
                         "отказанный урок создал файл таблицы — значит что-то записалось")

        first = self.add_live()
        with self.assertRaises(LS.LessonRejected):
            self.add_live(why="   ")
        second = self.add_live()
        self.assertEqual((first, second), (1, 2),
                         "отказ съел номер: после отказа выдан %d вместо 2" % second)
        self.assertEqual(len(LS.load(self.store).lessons), 2)

    def test_every_required_field_is_checked(self):
        """Все ШЕСТЬ обязательных полей, а не одно «почему»."""
        cases = (("question", LS.COL_QUESTION), ("bot_answer", LS.COL_BOT),
                 ("correct", LS.COL_RIGHT), ("why", LS.COL_WHY), ("who", LS.COL_WHO),
                 ("when", LS.COL_WHEN))
        self.assertEqual(len(cases), len(LS.REQUIRED_FIELDS), "полей обязано быть шесть")
        for arg, column in cases:
            with self.assertRaises(LS.LessonRejected, msg="пустое %s проехало" % arg) as box:
                self.add_live(**{arg: "  "})
            self.assertEqual(box.exception.field, column)

    def test_validate_is_pure(self):
        """`validate` — чистая функция: файла не касается, зовётся до записи."""
        ok, reason, field = LS.validate(Q_LIVE, BOT_LIVE, RIGHT_LIVE, "", WHO_LIVE, WHEN_LIVE)
        self.assertFalse(ok)
        self.assertEqual(field, LS.COL_WHY)
        self.assertIn("почему", reason.lower())
        self.assertFalse(os.path.isfile(self.store))
        ok2, _reason2, _field2 = LS.validate(Q_LIVE, BOT_LIVE, RIGHT_LIVE, WHY_LIVE,
                                             WHO_LIVE, WHEN_LIVE)
        self.assertTrue(ok2)

    def test_non_text_field_is_rejected_not_stringified(self):
        """`почему=None` — это отказ, а не строка «None» в таблице."""
        with self.assertRaises(LS.LessonRejected) as box:
            self.add_live(why=None)
        self.assertEqual(box.exception.field, LS.COL_WHY)
        self.assertFalse(os.path.isfile(self.store))


# =======================================================================================
# (б) ОТРИЦАТЕЛЬНЫЙ ТЕСТ: снятие метит строку, а не удаляет её
# =======================================================================================
class TestWithdrawKeepsRow(_Base):
    """Откат по трём разрезам владельца. След обязан остаться."""

    def setUp(self):
        _Base.setUp(self)
        self.n1 = self.add_live(who="filipp", when="2026-08-19T09:00:00Z")
        self.n2 = self.add_live(who="manager_ivan", when="2026-08-19T10:00:00Z")
        self.n3 = self.add_live(who="manager_ivan", when="2026-08-18T10:00:00Z")

    def test_one_lesson_row_stays_and_is_marked(self):
        before = self.raw()
        res = LS.withdraw(number=self.n2, path=self.store, now=0)

        self.assertEqual(res.marked, (self.n2,))
        self.assertEqual(res.lines_before, res.lines_after,
                         "снятие изменило число ФИЗИЧЕСКИХ строк: было %d, стало %d"
                         % (res.lines_before, res.lines_after))

        store = LS.load(self.store)
        self.assertEqual(len(store.lessons), 3, "строка исчезла при снятии")
        self.assertEqual([les.number for les in store.lessons], [self.n1, self.n2, self.n3])

        gone = [les for les in store.lessons if les.number == self.n2][0]
        self.assertFalse(LS.is_active(gone), "урок снят, а состояние осталось активным")
        marked, cut, stamp = LS.parse_state(gone.state)
        self.assertTrue(marked)
        self.assertEqual(cut, LS.CUT_ONE, "разрез снятия обязан быть записан в строку")
        self.assertTrue(len(stamp) > 0, "время снятия обязано быть записано в строку")

        # СОДЕРЖИМОЕ урока не пострадало — снялось только состояние.
        self.assertEqual((gone.question, gone.bot_answer, gone.correct, gone.why, gone.who),
                         tuple(before.split("\n")[2].split("\t")[1:6]))

    def test_all_other_bytes_untouched(self):
        """Снятие меняет ТОЛЬКО поле состояния своей строки: остальные строки байт в байт те же.

        Сверка идёт ПО ИМЕНОВАННОМУ ИНДЕКСУ, а не «всё кроме последнего поля»: с 09.09.2026
        последнее поле строки — это ИСТОЧНИК, а не состояние, и прежний `rsplit` молча сравнивал
        бы состояние со состоянием, объявляя чистым любое движение графы источника."""
        before = self.raw().split("\n")
        LS.withdraw(number=self.n2, path=self.store, now=0)
        after = self.raw().split("\n")
        self.assertEqual(len(before), len(after))
        for idx, (was, now) in enumerate(zip(before, after)):
            if idx == 2:                                   # строка урока n2 (0 — шапка)
                self.assertNotEqual(was, now)
                was_f, now_f = was.split("\t"), now.split("\t")
                self.assertEqual(len(was_f), len(now_f), "снятие поменяло ширину строки")
                for col, (a, b) in enumerate(zip(was_f, now_f)):
                    if col == LS.IDX_STATE:
                        continue
                    self.assertEqual(a, b, "снятие тронуло колонку «%s»" % LS.COLUMNS[col])
                self.assertNotEqual(was_f[LS.IDX_STATE], now_f[LS.IDX_STATE])
            else:
                self.assertEqual(was, now, "снятие тронуло ЧУЖУЮ строку %d" % idx)

    def test_withdraw_by_day(self):
        res = LS.withdraw(day="2026-08-19", path=self.store, now=0)
        self.assertEqual(sorted(res.marked), [self.n1, self.n2])
        self.assertEqual(res.lines_before, res.lines_after)
        store = LS.load(self.store)
        self.assertEqual(len(store.lessons), 3)
        self.assertEqual([les.number for les in LS.active(store.lessons)], [self.n3])
        for les in store.lessons:
            if les.number != self.n3:
                self.assertEqual(LS.parse_state(les.state)[1], LS.CUT_DAY)

    def test_withdraw_by_author(self):
        res = LS.withdraw(who="manager_ivan", path=self.store, now=0)
        self.assertEqual(sorted(res.marked), [self.n2, self.n3])
        store = LS.load(self.store)
        self.assertEqual(len(store.lessons), 3)
        self.assertEqual([les.number for les in LS.active(store.lessons)], [self.n1])

    def test_author_key_ignores_at_and_case(self):
        """«@Manager_Ivan» и «manager_ivan» — один человек, иначе откат по автору дырявый."""
        res = LS.withdraw(who="  @Manager_IVAN ", path=self.store, now=0)
        self.assertEqual(sorted(res.marked), [self.n2, self.n3])

    def test_second_withdraw_is_idempotent(self):
        LS.withdraw(number=self.n2, path=self.store, now=0)
        again = LS.withdraw(number=self.n2, path=self.store, now=0)
        self.assertEqual(again.marked, (), "повторное снятие пометило строку заново")
        self.assertEqual(again.already, (self.n2,), "повторное снятие обязано назвать «уже снят»")

    def test_withdrawn_number_is_never_reused(self):
        """Снятый урок не освобождает номер: иначе два разных урока получат один номер."""
        LS.withdraw(number=self.n3, path=self.store, now=0)
        self.assertEqual(self.add_live(), 4)

    def test_exactly_one_cut_required(self):
        for kwargs in ({}, {"number": 1, "day": "2026-08-19"},
                       {"day": "2026-08-19", "who": "filipp"},
                       {"number": 1, "day": "2026-08-19", "who": "filipp"}):
            with self.assertRaises(ValueError, msg="проехало снятие с разрезами %s" % kwargs):
                LS.withdraw(path=self.store, now=0, **kwargs)

    def test_backup_is_written_before_rewrite(self):
        """Единственная операция, трогающая старые байты, кладёт рядом копию ДО правки."""
        before = self.raw()
        LS.withdraw(number=self.n2, path=self.store, now=0)
        backup = self.store + LS.BACKUP_SUFFIX
        self.assertTrue(os.path.isfile(backup), "копия перед перезаписью не создана")
        with open(backup, encoding="utf-8", newline="") as f:
            self.assertEqual(f.read(), before, "копия не совпала с тем, что было до правки")

    def test_withdraw_on_missing_table_is_quiet_and_named(self):
        empty = os.path.join(os.path.dirname(self.store), "no_such.tsv")
        res = LS.withdraw(number=1, path=empty, now=0)
        self.assertEqual((res.marked, res.lines_before), ((), 0))


# =======================================================================================
# (в) ОТРИЦАТЕЛЬНЫЙ ТЕСТ: персональное в таблицу не попадает, смысл цел
# =======================================================================================
class TestPersonalDataNeverLands(_Base):
    """Имена и телефоны не ложатся в таблицу, даже придя во входном тексте."""

    def _digits(self, text):
        return re.sub(r"\D+", "", text)

    def test_phone_and_name_absent_meaning_intact(self):
        self.add_live()
        raw = self.raw()

        # 1. Телефона нет НИ В ОДНОЙ форме — проверяем по цифрам всего файла, а не по строке.
        self.assertNotIn("66812345678", self._digits(raw), "телефон лёг в таблицу цифрами")
        self.assertNotIn("+66 81 234 5678", raw)
        self.assertNotIn("812345678", self._digits(raw))

        # 2. Имени нет ни в одном падеже.
        for form in ("Сергей", "Сергея", "Сергею", "Сергеем", "сергей"):
            self.assertNotIn(form, raw, "имя «%s» лёгло в таблицу" % form)

        # 3. ОСТАЛЬНОЙ СМЫСЛ ЦЕЛ — модель, место, срок, цены и вся поправка на месте.
        for kept in ("Yamaha NMAX", "Раваи", "на неделю", "3520 бат", "590 бат"):
            self.assertIn(kept, raw, "чистка съела осмысленный кусок урока: %r" % kept)
        store = LS.load(self.store)
        self.assertEqual(store.lessons[0].correct, RIGHT_LIVE, "поправка изменилась при чистке")
        self.assertEqual(store.lessons[0].why, WHY_LIVE, "«почему» изменилось при чистке")

        # 4. На месте персонального стоит МЕТКА, а не дыра: ход разговора читается.
        self.assertIn("Лицо_", store.lessons[0].question)
        self.assertIn("<телефон_", store.lessons[0].question)

    def test_scrub_covers_all_four_text_fields(self):
        """Чистятся все четыре текстовых поля, а не только вопрос клиента."""
        self.add_live(bot_answer="Сергей, перезвоню на +66 81 234 5678.",
                      correct="Назовите цену сразу, не спрашивая телефон.",
                      why="Сергей уже писал номер выше, повторный запрос злит клиента")
        raw = self.raw()
        self.assertNotIn("Сергей", raw)
        self.assertNotIn("66812345678", self._digits(raw))
        # Человек назван в ТРЁХ полях (вопрос, ответ бота, почему) — и во всех трёх он ОДНА и
        # та же `Лицо_1`. Четвёртая метка в файле — не четвёртое поле, а ПЕРЕмаскировка
        # местоимения в звательной позиции (`Лицо_2`, тест ниже). Считать их одним числом
        # значило бы спрятать перемаскировку за общим счётом — поэтому метки разведены.
        self.assertEqual(raw.count("Лицо_1"), 3, "метка человека не во всех полях, где он назван")
        self.assertEqual(raw.count("Лицо_2"), 1, "перемаскировка местоимения изменилась молча")

    def test_labels_stable_inside_lesson_and_not_across(self):
        """Один человек — одна метка ВНУТРИ урока; между уроками общей метки нет (не трекер)."""
        clean = LS.scrub_lesson("Меня зовут Сергей", "Здравствуйте, Сергей!",
                                "Сергей ждёт цену", "Сергей спросил дважды")
        self.assertEqual(clean.question.count("Лицо_1"), 1)
        for field in (clean.bot_answer, clean.correct, clean.why):
            self.assertIn("Лицо_1", field)
        self.assertNotIn("Лицо_2", " ".join(clean[:4]))

        other = LS.scrub_lesson("Меня зовут Елена", "ок", "ок", "ок")
        self.assertIn("Лицо_1", other.question)   # счёт начался заново → сцепить уроки нечем

    def test_author_field_is_not_scrubbed(self):
        """`кто_записал` чистке НЕ подлежит — иначе разрез отката «по автору» умирает."""
        self.add_live(who="Сергей")
        raw = self.raw()
        self.assertIn("\tСергей\t", raw, "автор урока обезличен — откат по человеку сломан")
        store = LS.load(self.store)
        self.assertEqual(len(LS.by_author(store.lessons, "Сергей")), 1)

    def test_protected_words_survive(self):
        """Защищённый словарь отменяет маску: модели и места именами не считаются."""
        clean = LS.scrub_lesson("Хочу Honda PCX в Патонг", "Yamaha в Камале нет",
                                "Есть Click в Раваи", "клиент назвал модель и район")
        for kept in ("Honda PCX", "Патонг", "Yamaha", "Камале", "Click", "Раваи"):
            self.assertIn(kept, " ".join(clean[:4]), "защищённое слово съедено: %r" % kept)

    def test_scrub_is_not_switchable_off(self):
        """У чистки нет выключателя: `add` не принимает ни `scrub=`, ни `raw=`."""
        import inspect
        names = set(inspect.signature(LS.add).parameters)
        self.assertEqual(names & {"scrub", "raw", "clean", "anonymize"}, set(),
                         "у add появился выключатель чистки — персональное поедет в таблицу")

    def test_only_personal_field_becomes_a_label(self):
        """Поле, в котором не было НИЧЕГО, кроме персонального, становится МЕТКОЙ, а не пустеет.

        Здесь ожидание теста правится по ЗАМЕРУ, а не наоборот. Сегодняшний детектор
        ПОДСТАВЛЯЕТ метку («<телефон_1>»), а не вырезает текст, поэтому непустое поле после
        чистки не пустеет ни на одном входе — и ветка отказа `REASON_SCRUBBED_OUT` в `add()`
        остаётся СТРАХОВКОЙ на случай, если детектор начнёт вырезать. Модуль называет её
        непройденной честно и ссылается сюда именем этого теста (`lesson_store.py:211`).

        Требование владельца при этом держится ЦЕЛИКОМ: телефон в таблицу не лёг."""
        clean = LS.scrub_lesson("q", "a", "c", "+66 81 234 5678")
        self.assertEqual(clean.why, "<телефон_1>",
                         "поле из одного телефона перестало быть меткой: %r" % clean.why)

        number = self.add_live(why="+66 81 234 5678")
        raw = self.raw()
        self.assertNotIn("66812345678", self._digits(raw), "телефон лёг в таблицу цифрами")
        self.assertNotIn("+66 81 234 5678", raw)
        got = LS.load(self.store).lessons[0]
        self.assertEqual((got.number, got.why), (number, "<телефон_1>"))

        # Ветка отказа существует и называет причину словами — но сегодня недостижима, и это
        # сказано вслух, а не выдано за рабочую проверку.
        self.assertIn("стало пустым", LS.REASON_SCRUBBED_OUT)

    def test_greeting_position_overmasks_by_design(self):
        """ЦЕНА выбора «сомнительное — в пользу приватности», названная, а не спрятанная.

        Звательная позиция ловит первое слово после приветствия, и на живой фразе под маску
        уходит МЕСТОИМЕНИЕ «Меня»: в таблицу ложится «Здравствуйте! Лицо_2 зовут Лицо_1».
        Имя вычищено верно, местоимение — лишнее. Тест запирает это КАК ЕСТЬ (докстринг
        `lesson_store.py` §«ЦЕНА ЭТОГО ВЫБОРА»): чинить перемаскировку здесь НЕЛЬЗЯ — словарь
        `PROTECTED` живёт в `anonymize_corpus`, и его полноту держит замок корпуса на 710
        диалогах; расширить словарь под свой случай = молча обесценить чужой замер.

        Что именно ловит тест: НАПРАВЛЕНИЕ ошибки. Лишняя маска — не течь, недостающая — течь."""
        clean = LS.scrub_lesson(Q_LIVE, BOT_LIVE, RIGHT_LIVE, WHY_LIVE)
        self.assertEqual(clean.question, Q_LIVE_SCRUBBED,
                         "перемаскировка изменилась — это смена поведения детектора, а не мелочь")

        # 1. Перемаскировано ИМЕННО местоимение, и СВОЕЙ меткой: склеив его с человеком, детектор
        #    выдал бы «Лицо_1 зовут Лицо_1» — фразу, читаемую как один участник вместо двух слов.
        self.assertNotIn("Меня", clean.question)
        self.assertIn("Лицо_2 зовут Лицо_1", clean.question)

        # 2. Ловится ПОЗИЦИЯ, а не местоимения вообще: «мой» тремя словами позже уцелел.
        self.assertIn("мой номер", clean.question)

        # 3. Направление ошибки — в сторону приватности: имя и телефон ушли всеми формами.
        for form in ("Сергей", "Сергея", "Сергею", "сергей"):
            self.assertNotIn(form, clean.question, "имя «%s» уцелело" % form)
        self.assertNotIn("5678", clean.question)

        # 4. Перемаскировка не съела смысл: приветствие, модель, срок и место на месте.
        for kept in ("Здравствуйте!", "Yamaha NMAX", "на неделю", "Раваи"):
            self.assertIn(kept, clean.question, "перемаскировка съела осмысленный кусок: %r" % kept)


# =======================================================================================
# ЗАМОК: уроки не исчезают НИКОГДА
# =======================================================================================
class TestNothingEverDeletes(_Base):
    """Требование владельца: переполнения, молча стирающего записи, быть не должно НИ В КАКОМ
    ВИДЕ. Держим двумя слоями — фактом и исходником."""

    def test_two_hundred_lessons_survive_three_withdrawals(self):
        for i in range(200):
            day = "2026-08-%02d" % (1 + i % 28)
            self.add_live(who="teacher_%d" % (i % 3), when=day + "T09:00:00Z")
        self.assertEqual(len(LS.load(self.store).lessons), 200)

        LS.withdraw(number=7, path=self.store, now=0)
        LS.withdraw(day="2026-08-05", path=self.store, now=0)
        LS.withdraw(who="teacher_1", path=self.store, now=0)

        store = LS.load(self.store)
        self.assertEqual(len(store.lessons), 200, "после снятий строк стало меньше двухсот")
        self.assertEqual([les.number for les in store.lessons], list(range(1, 201)),
                         "номера перестали быть сплошными — строка потерялась")
        self.assertEqual(store.reading.outcome, parse_outcome.PARSED)
        self.assertTrue(LS.integrity(self.store).ok)

    def test_source_has_no_deleting_call(self):
        """В модуле нет ни одного вызова, способного стереть данные."""
        tree = _module_tree()
        forbidden = {"remove", "unlink", "truncate", "rmtree", "removedirs"}
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden:
                    found.append((node.func.attr, node.lineno))
        self.assertEqual(found, [], "в хранилище появился стирающий вызов: %s" % found)

    def test_only_truncating_open_is_the_temp_file(self):
        """Единственный усекающий `open` открывает ВРЕМЕННЫЙ файл, а не таблицу."""
        bad = []
        for node in ast.walk(_module_tree()):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "open"):
                continue
            mode = _open_mode(node)
            if mode is None or not ("w" in mode or "x" in mode):
                continue
            target = node.args[0]
            if not (isinstance(target, ast.Name) and target.id == "tmp"):
                bad.append(node.lineno)
        self.assertEqual(bad, [], "усекающий open() смотрит не на временный файл, строки: %s" % bad)

    def test_new_lesson_is_appended_not_rewritten(self):
        """Запись урока идёт дозаписью: прежние байты остаются на своих местах."""
        self.add_live()
        before = self.raw()
        self.add_live()
        after = self.raw()
        self.assertTrue(after.startswith(before),
                        "новый урок переписал начало файла, а не дописался в конец")


# =======================================================================================
# Формат, чтение, ёмкость, граница шага
# =======================================================================================
def _module_tree():
    with open(os.path.join(HERE, "lesson_store.py"), encoding="utf-8") as f:
        return ast.parse(f.read(), filename="lesson_store.py")


def _open_mode(node):
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value
    return None


class TestOneLineOneLesson(_Base):
    """«Строка файла = урок» держится БАЙТАМИ, а не соглашением."""

    def test_tabs_and_newlines_in_text_do_not_break_the_row(self):
        wild = "первая строка\nвторая\tс табуляцией\r\nтретья " + chr(92) + "n не перевод"
        self.add_live(question=wild + " Сколько стоит NMAX?")
        body = [ln for ln in self.raw().split("\n") if len(ln.strip()) > 0]
        self.assertEqual(len(body), 2, "урок с переводами строк занял не одну строку файла")
        self.assertEqual(len(body[1].split("\t")), len(LS.COLUMNS),
                         "табуляция из текста разъехалась по колонкам")
        got = LS.load(self.store).lessons[0]
        self.assertTrue(got.question.startswith(wild),
                        "текст не вернулся дословно:\n%r\n%r" % (got.question, wild))

    def test_escape_round_trip(self):
        for sample in ("", "простой", "таб\tтут", "строка\nвторая", "возврат\rтут",
                       chr(92) + "n дословно", chr(92) + chr(92), "смесь\t" + chr(92) + "t\n"):
            self.assertEqual(LS.unesc(LS.esc(sample)), sample, "круг не сошёлся на %r" % sample)
            self.assertNotIn("\n", LS.esc(sample))
            self.assertNotIn("\t", LS.esc(sample))

    def test_header_and_columns(self):
        """НОВЫЙ файл получает ОДИННАДЦАТИколоночную шапку; восемь прежних колонок стоят на
        прежних местах, источник — девятой (09.09.2026), партия и режим дописаны ХВОСТОМ
        (20.09.2026). Число названо здесь ОДНО и точное: «не меньше» пропустило бы лишнюю графу,
        появившуюся мимо решения."""
        self.add_live()
        first = self.raw().split("\n")[0]
        self.assertEqual(first, LS.HEADER_LINE)
        self.assertEqual(len(LS.COLUMNS_BASE), 8,
                         "обязательных колонок восемь: шесть полей + номер + состояние")
        self.assertEqual(len(LS.COLUMNS_SRC), 9, "девятая — необязательный хвост «источник»")
        self.assertEqual(len(LS.COLUMNS), 11, "десятая и одиннадцатая — «партия» и «режим»")
        self.assertEqual(LS.COLUMNS[0], LS.COL_NUM)
        self.assertEqual(LS.COLUMNS_BASE[-1], LS.COL_STATE)
        self.assertEqual(LS.COLUMNS_SRC[-1], LS.COL_SOURCE)
        self.assertEqual(LS.COLUMNS[-2:], (LS.COL_BATCH, LS.COL_MODE))
        # ХВОСТ НИЧЕГО НЕ СДВИНУЛ — замок на индексах, а не на глазах.
        self.assertEqual(LS.COLUMNS[:9], LS.COLUMNS_SRC)
        self.assertEqual(LS.IDX_STATE, 7)
        self.assertEqual(LS.IDX_SOURCE, 8)


class TestReaderSaysNonparse(_Base):
    """Контракт `parse_outcome`: слепота читателя не притворяется молчанием источника."""

    def test_missing_file_is_named_not_faked_empty(self):
        store = LS.load(self.store)
        self.assertFalse(store.exists)
        self.assertEqual(store.reading.outcome, parse_outcome.EMPTY)
        self.assertEqual(store.lessons, ())

    def test_unparsable_body_is_nonparse(self):
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(LS.HEADER_LINE + "\n")
            f.write("это не таблица, а заметка руками\n")
            f.write("и вторая такая же\n")
        store = LS.load(self.store)
        self.assertEqual(store.reading.outcome, parse_outcome.NONPARSE)
        self.assertTrue(store.reading.blind)
        self.assertEqual(len(store.broken), 2)
        self.assertIn("НЕРАЗБОР", store.reading.say("строк"))
        self.assertFalse(LS.integrity(self.store).ok)

    def test_broken_line_does_not_steal_a_number(self):
        """Битая строка с читаемым номером всё равно занимает его — новый урок сядет следом."""
        with open(self.store, "w", encoding="utf-8", newline="") as f:
            f.write(LS.HEADER_LINE + "\n")
            f.write("41\tобрубок строки\n")
        self.assertEqual(self.add_live(), 42)

    def test_reading_counts_are_honest(self):
        self.add_live()
        self.add_live()
        reading = LS.load(self.store).reading
        self.assertEqual((reading.seen, reading.parsed), (2, 2))

    def test_filters_read_all_by_author_by_day(self):
        self.add_live(who="filipp", when="2026-08-19T09:00:00Z")
        self.add_live(who="manager_ivan", when="2026-08-19T11:00:00Z")
        self.add_live(who="manager_ivan", when="2026-08-18T11:00:00Z")
        rows = LS.load(self.store).lessons
        self.assertEqual(len(rows), 3)
        self.assertEqual([les.number for les in LS.by_author(rows, "manager_ivan")], [2, 3])
        self.assertEqual([les.number for les in LS.by_day(rows, "2026-08-19")], [1, 2])
        self.assertEqual([les.number for les in LS.by_day(rows, "2026-08-18")], [3])


class TestCapacityNeverLoses(_Base):
    """Ёмкость названа числом, а превышение — замедление, а не потеря."""

    def test_comfort_threshold_is_a_number(self):
        self.assertIsInstance(LS.COMFORT_LESSONS, int)
        self.assertGreaterEqual(LS.COMFORT_LESSONS, 1000)

    def test_capacity_reports_free_and_tight(self):
        self.add_live()
        cap = LS.capacity(self.store)
        self.assertEqual(cap.state, LS.CAPACITY_FREE)
        self.assertIn(str(LS.COMFORT_LESSONS), cap.say)
        self.assertGreater(cap.bytes, 0)

    def test_above_comfort_still_writes_and_says_tight(self):
        """Порог понижаем на время теста — писать двести тысяч строк ради ветки не нужно."""
        real = LS.COMFORT_LESSONS
        LS.COMFORT_LESSONS = 2
        self.addCleanup(setattr, LS, "COMFORT_LESSONS", real)
        for _ in range(3):
            self.add_live()
        cap = LS.capacity(self.store)
        self.assertEqual(cap.state, LS.CAPACITY_TIGHT)
        self.assertIn("Ничего не потеряно", cap.say)
        # И ЗА порогом запись продолжается: отказа по переполнению нет ни одной веткой.
        self.assertEqual(self.add_live(), 4)
        self.assertEqual(len(LS.load(self.store).lessons), 4)


class TestBotNotWired(unittest.TestCase):
    """ГРАНИЦА ШАГА, ПОДВИНУТАЯ 06.09.2026 и названная числом файлов, а не словом.

    До 05.09 к хранилищу не был подключён НИКТО. 05.09 подключился `trainer.py` — путь ЗАПИСИ
    урока кандидатом. 06.09 подключился `suggest.py` — путь ЧТЕНИЯ действующих уроков в промпт
    (`load_playbook`). Остальные пять по-прежнему не ходят сюда ни одной веткой. Список заперт
    ПОИМЁННО, а не ослаблен до «кто-нибудь может»: расширение круга обязано требовать правки
    ЭТОГО списка и объяснения, а не проходить молча вместе с чужим импортом.

    ПОЧЕМУ `suggest.py` ВПУЩЕН, ХОТЯ ПРЕЖНЯЯ РЕДАКЦИЯ ЗАПРЕЩАЛА ЕГО ОСОБО — довод разобран, а не
    отброшен. Запрет стоял на ПРИЧИНЕ: «импорт хранилища из сборщика клиентского ответа означал
    бы, что КАНДИДАТ начал влиять на то, что читает клиент». Причина держалась на подмене:
    наличие импорта считалось признаком утечки кандидата. Теперь утечка проверяется НАПРЯМУЮ и в
    двух слоях, а не через посредника:

      • ПО ПОВЕДЕНИЮ — `test_lesson_read.TestOnlyActiveReachTheAnswer`: в таблицу кладутся
        действующий, кандидат и снятый урок, и в собранный промпт попадает ровно действующий;
      • ПО ИСХОДНИКУ — `test_suggest_selects_only_through_active` ниже: отбор в `suggest.py` идёт
        через `lesson_store.active` и никак иначе (ни `candidates`, ни своё сравнение состояния).

    Ослаблением это не является: прежний тест ловил бы утечку кандидата ТОЛЬКО пока читателя нет
    вовсе, и в тот день, когда чтение появилось бы законно, он бы всё равно упал — не заметив
    ничего про кандидатов. Новые два ловят утечку и ПОСЛЕ подключения."""

    WIRED = ("suggest.py", "trainer.py")

    def test_only_the_named_module_imports_the_store(self):
        watched = ("trainer.py", "trainer_run.py", "moderation_bot.py", "moderation_core.py",
                   "userbot_listen.py", "lesson_router.py", "suggest.py")
        wired = []
        for name in watched:
            path = os.path.join(HERE, name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                src = f.read()
            if re.search(r"^\s*(import|from)\s+lesson_store\b", src, re.M):
                wired.append(name)
        self.assertEqual(sorted(wired), sorted(self.WIRED),
                         "круг файлов, ходящих в хранилище, разошёлся с объявленным: было %s, "
                         "стало %s" % (sorted(self.WIRED), sorted(wired)))

    def test_suggest_selects_only_through_active(self):
        """ЧИТАТЕЛЬ КЛИЕНТСКОГО ОТВЕТА ОТБИРАЕТ УРОКИ ТОЛЬКО `lesson_store.active`.

        Замок на класс «своё сравнение состояния»: `active()` — единственное место, где написано
        «действующий значит `актив`». Свой фильтр в `suggest.py` (по `candidates`, по
        `STATE_CANDIDATE`, по литералу состояния) означал бы ВТОРОЕ определение действующего
        урока, и разъехались бы они молча — ровно так кандидат и попал бы к клиенту."""
        with open(os.path.join(HERE, "suggest.py"), encoding="utf-8") as f:
            src = f.read()
        # По ДЕРЕВУ, а не грепом: греп посчитал бы упоминания в комментариях и докстрингах
        # (`lesson_store.tsv`, `lesson_store.withdraw`) настоящими вызовами и позеленел бы/
        # покраснел бы на прозе.
        used = sorted({node.attr for node in ast.walk(ast.parse(src))
                       if isinstance(node, ast.Attribute)
                       and isinstance(node.value, ast.Name) and node.value.id == "lesson_store"})
        self.assertEqual(used, ["active", "load"],
                         "suggest.py трогает хранилище не только через load/active: %s" % used)
        for forbidden in ("STATE_CANDIDATE", "STATE_ACTIVE", '"актив"', "'актив'",
                          '"кандидат"', "'кандидат'"):
            self.assertNotIn(forbidden, src,
                             "в suggest.py завелось СВОЁ определение действующего урока (%s) — "
                             "второе определение разъедется с lesson_store.active молча" % forbidden)

    def test_store_file_is_outside_git(self):
        """Файл таблицы не под git: `git checkout HEAD --` молча откатил бы накопленные уроки."""
        with open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            self.assertIn(LS.STORE_NAME, f.read(),
                          "таблица уроков не занесена в .gitignore — откат дерева её сотрёт")


if __name__ == "__main__":
    unittest.main()
