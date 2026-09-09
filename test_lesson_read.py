# -*- coding: utf-8 -*-
"""
test_lesson_read.py — ПУТЬ ЧТЕНИЯ: в момент сборки ответа действующие уроки берутся из БАЗЫ
(`lesson_store`), а плоская книга правил стала снимком (подключено 06.09.2026).

Предмет набора — не «код вызывает функцию», а КОНТРФАКТ: три входа, на которых видно, что
источник сменился по существу.

  (1) `TestOnlyActiveReachTheAnswer` — в одной таблице лежат ДЕЙСТВУЮЩИЙ, КАНДИДАТ и СНЯТЫЙ
      урок. В собранный системный промпт попадает ровно действующий. Это и есть замок «кандидат
      к клиенту не попадает», проверенный ПОВЕДЕНИЕМ, а не отсутствием импорта: до 06.09 ту же
      мысль сторожил `test_lesson_store.TestBotNotWired` — но лишь пока читателя нет вовсе.
  (2) `TestBookIsNoLongerTheSource` — правка секции «Выученные правила» ПРЯМО В КНИГЕ (и рукой,
      и штатным `append_playbook_rule`) промпт больше НЕ меняет: он остаётся байт в байт.
      ГРАНИЦА НАЗВАНА ЧЕСТНО И В ДРУГУЮ СТОРОНУ: прочие секции книги (стиль/факты/запреты —
      они уроками не являются и в базу не переносились) в промпт идут как раньше, и их правка
      ответ меняет. Это проверяется отдельным тестом, а не умалчивается.
  (3) `TestThirdOutcome` — «база не прочитана» и «действующих уроков ноль» РАЗВЕДЕНЫ. Нет файла
      базы / все строки неразобраны / объявленный откат `LESSON_BASE_READ_OFF` → в промпт идёт
      КНИГА-снимок ровно как до 06.09. Прочитанная и пустая база → секции выученных правил в
      промпте НЕТ, и книжные буллеты её не подменяют.

Плюс два замка формы и соседства:
  • `TestBulletShape` — многострочный урок схлопывается в ОДИН буллет и читается штатным
    разборщиком книги как одно правило (несхлопнутый разорвал бы секцию молча);
  • `TestNeighbourRulesCard` — показ `/rules` и удаление правила по номеру остались на ФАЙЛЕ
    книги: они её и мутируют, и нумерация показа обязана быть нумерацией файла.

БОЕВОГО НЕ КАСАЕМСЯ: своя таблица уроков и своя книга во временном каталоге на каждый тест;
боевые `lesson_store.tsv` и `manager-bot/docs/playbook.md` не читаются и не пишутся ни разу.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_read -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import tempfile
import unittest

import lesson_store as LS
import suggest

# Книга-заготовка. Форма живая: четыре секции, из них выученных правил — одна; прочие три
# (стиль/факты/запреты) в базу уроков не переносились и остаются книжными.
BOOK = (
    "# Playbook\n\n"
    "## Стиль общения\n"
    "- КНИЖНЫЙ-СТИЛЬ отвечай коротко и по делу\n\n"
    "## Выученные правила\n"
    "- (2026-07-14) КНИЖНОЕ-ПРАВИЛО-А не тяни время\n"
    "- (2026-07-22) КНИЖНОЕ-ПРАВИЛО-Б не дублируй название модели\n\n"
    "## Так НЕ говорим\n"
    "- КНИЖНЫЙ-ЗАПРЕТ не обещай того, чего нет\n"
)

WHEN = "2026-09-03T12:00:00Z"
WHY = "цена уже известна из прайса, ожидание теряет клиента"
WHO = "владелец"


class _Base(unittest.TestCase):
    """Своя книга и своя таблица уроков во временном каталоге; глобали suggest возвращаются."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_read_test_")
        self.addCleanup(box.cleanup)
        self.book = os.path.join(box.name, "playbook.md")
        with open(self.book, "w", encoding="utf-8") as f:
            f.write(BOOK)
        self.store = os.path.join(box.name, "lesson_store.tsv")

        keep_book, keep_base, keep_off = (suggest.PLAYBOOK_FILE, suggest.LESSON_BASE_PATH,
                                         suggest.LESSON_BASE_OFF)

        def back():
            (suggest.PLAYBOOK_FILE, suggest.LESSON_BASE_PATH,
             suggest.LESSON_BASE_OFF) = keep_book, keep_base, keep_off

        self.addCleanup(back)
        suggest.PLAYBOOK_FILE = self.book
        suggest.LESSON_BASE_PATH = self.store
        suggest.LESSON_BASE_OFF = False

    def add_active(self, correct, question="Сколько стоит на неделю?", bot="Уточню и вернусь."):
        n = LS.add(question=question, bot_answer=bot, correct=correct, why=WHY, who=WHO,
                   when=WHEN, path=self.store)
        return n, self.stored(n)

    def add_candidate(self, correct, question="А депозит какой?", bot="Не знаю."):
        n = LS.add_candidate(question=question, bot_answer=bot, correct=correct, who=WHO,
                             when=WHEN, path=self.store)
        return n, self.stored(n)

    def stored(self, number):
        """Текст урока В ТОМ ВИДЕ, В КАКОМ ОН ЛЁГ В ТАБЛИЦУ (после неотключаемой чистки).
        Сверяться с ним, а не с тем, что подали на вход, — правило «мок копирует живой формат»."""
        for les in LS.load(self.store).lessons:
            if les.number == number:
                return les.correct
        raise AssertionError("урок %r в таблице не найден" % number)

    def prompt(self):
        """Системный промпт, собранный БОЕВЫМ путём: тот же вызов, что у suggest.generate_draft
        и moderation_core (`playbook=load_playbook()`)."""
        return suggest.make_system_prompt("FAQ", "ru", playbook=suggest.load_playbook())


# =======================================================================================
# КОНТРФАКТ 1 и 2: действующий урок в ответе есть, кандидат и снятый — нет
# =======================================================================================
class TestOnlyActiveReachTheAnswer(_Base):

    def test_active_candidate_withdrawn_in_one_table(self):
        n_act, t_act = self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        _, t_cand = self.add_candidate("КАНДИДАТ предлагай шлем в подарок на неделю")
        n_wd, t_wd = self.add_active("СНЯТЫЙ спрашивай опыт вождения до цены",
                                     question="Можно завтра?", bot="Наверное.")
        LS.withdraw(number=n_wd, path=self.store)

        # Таблица собрана так, как задумано: три строки, действующая одна.
        lessons = LS.load(self.store).lessons
        self.assertEqual(len(lessons), 3)
        self.assertEqual([les.number for les in LS.active(lessons)], [n_act])

        got = self.prompt()
        self.assertIn(t_act, got, "действующий урок в ответ не попал")
        self.assertNotIn(t_cand, got, "КАНДИДАТ дошёл до клиентского промпта")
        self.assertNotIn(t_wd, got, "СНЯТЫЙ урок дошёл до клиентского промпта")

    def test_withdrawing_the_lesson_takes_it_out_of_the_answer(self):
        """Отзыв урока — ЖИВОЙ контрфакт: один и тот же прибор до и после снятия."""
        n, text = self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        self.assertIn(text, self.prompt())
        LS.withdraw(number=n, path=self.store)
        self.assertNotIn(text, self.prompt(), "снятый урок продолжает идти в ответ")

    def test_promoted_candidate_starts_reaching_the_answer(self):
        """И обратно: кандидат входит в ответ РОВНО тогда, когда стал действующим (`promote`),
        а не когда был записан. Ворота «действующий урок всегда с причиной» на месте."""
        n, text = self.add_candidate("КАНДИДАТ предлагай шлем в подарок на неделю")
        self.assertNotIn(text, self.prompt())
        LS.promote(n, why="владелец назвал причину при подтверждении", who="filipp",
                   path=self.store)
        self.assertIn(text, self.prompt(), "утверждённый урок в ответ не попал")

    def test_candidate_alone_leaves_the_learned_section_empty(self):
        """Таблица из ОДНИХ кандидатов — это не «уроков нет по недочитанности»: база прочитана,
        действующих ноль, значит секции выученных правил в промпте нет, и КНИЖНЫЕ буллеты её
        не подменяют (иначе кандидат вернул бы книгу в источники через чёрный ход)."""
        _, text = self.add_candidate("КАНДИДАТ предлагай шлем в подарок на неделю")
        got = self.prompt()
        self.assertNotIn(text, got)
        self.assertNotIn("КНИЖНОЕ-ПРАВИЛО-А", got)
        self.assertNotIn(suggest._LEARNED_HEADER, got)


# =======================================================================================
# КОНТРФАКТ 3: правка книги руками ответ больше не меняет
# =======================================================================================
class TestBookIsNoLongerTheSource(_Base):

    def setUp(self):
        super().setUp()
        self.n, self.text = self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")

    def _rewrite_book(self, new_text):
        with open(self.book, "w", encoding="utf-8") as f:
            f.write(new_text)

    def test_hand_edited_learned_rule_does_not_change_the_answer(self):
        before = self.prompt()
        self.assertIn(self.text, before)
        self.assertNotIn("КНИЖНОЕ-ПРАВИЛО-А", before)

        self._rewrite_book(BOOK.replace("КНИЖНОЕ-ПРАВИЛО-А не тяни время",
                                        "РУКОПРАВКА отвечай одним словом")
                               .replace("- (2026-07-22) КНИЖНОЕ-ПРАВИЛО-Б не дублируй название модели",
                                        "- (2026-07-22) РУКОПРАВКА-2 здоровайся дважды"))
        after = self.prompt()
        self.assertNotIn("РУКОПРАВКА", after, "правка книги руками всё ещё меняет ответ")
        self.assertEqual(before, after, "промпт изменился от правки книги — она осталась источником")

    def test_append_playbook_rule_no_longer_reaches_the_answer(self):
        """Штатный писатель книги (`lesson_router._style_sink` → `append_playbook_rule`) пишет
        по-прежнему В КНИГУ — и с 06.09 до ответа эта запись НЕ доходит. Цена подключения
        названа тестом, а не спрятана в комментарии."""
        before = self.prompt()
        self.assertEqual(suggest.append_playbook_rule("НОВОЕ-В-КНИГУ не переспрашивай даты дважды"),
                         "added")
        self.assertIn("НОВОЕ-В-КНИГУ", suggest.load_playbook_file(), "правило в книгу не легло")
        after = self.prompt()
        self.assertNotIn("НОВОЕ-В-КНИГУ", after)
        self.assertEqual(before, after)

    def test_other_book_sections_still_reach_the_answer(self):
        """ГРАНИЦА В ДРУГУЮ СТОРОНУ, названная вслух: снимком стала СЕКЦИЯ ВЫУЧЕННЫХ ПРАВИЛ.
        Стиль, факты и запреты уроками не являются, в базу не переносились и идут из книги —
        значит их правка ответ меняет, и молчать об этом нельзя."""
        got = self.prompt()
        self.assertIn("КНИЖНЫЙ-СТИЛЬ", got)
        self.assertIn("КНИЖНЫЙ-ЗАПРЕТ", got)
        self._rewrite_book(BOOK.replace("КНИЖНЫЙ-СТИЛЬ отвечай коротко и по делу",
                                        "КНИЖНЫЙ-СТИЛЬ-2 отвечай развёрнуто"))
        self.assertIn("КНИЖНЫЙ-СТИЛЬ-2", self.prompt())


# =======================================================================================
# ТРЕТИЙ ИСХОД: «не прочитано» и «пусто» — разные ответы
# =======================================================================================
class TestThirdOutcome(_Base):

    def test_missing_base_falls_back_to_the_book_snapshot(self):
        """Файла базы нет вовсе → книга отвечает как до 06.09. Это НЕ «уроков ноль»: снимок для
        того и держат, чтобы пропажа источника не стёрла выученное молча."""
        self.assertFalse(os.path.exists(self.store))
        self.assertEqual(suggest.active_lesson_bullets(), ((), False))
        self.assertEqual(suggest.load_playbook(), suggest.load_playbook_file())
        self.assertIn("КНИЖНОЕ-ПРАВИЛО-А", self.prompt())

    def test_unparsable_base_falls_back_to_the_book_snapshot(self):
        """Таблица непуста, а разобрать нечего (НЕРАЗБОР по контракту `parse_outcome`) → книга.
        Нуль наверху означал бы отказ читателя, а не «уроков нет»."""
        with open(self.store, "w", encoding="utf-8") as f:
            f.write(LS.HEADER_LINE + "\nмусор без табуляций\nещё мусор\n")
        store = LS.load(self.store)
        self.assertTrue(store.reading.blind)
        self.assertEqual(suggest.active_lesson_bullets(), ((), False))
        self.assertEqual(suggest.load_playbook(), suggest.load_playbook_file())

    def test_declared_rollback_returns_the_old_behaviour(self):
        """Объявленный откат `LESSON_BASE_READ_OFF=1`: база не читается вовсе, книга снова
        источник. Откат обязан быть, и он обязан быть проверенным."""
        self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        suggest.LESSON_BASE_OFF = True
        self.assertEqual(suggest.active_lesson_bullets(), ((), False))
        self.assertEqual(suggest.load_playbook(), suggest.load_playbook_file())
        self.assertIn("КНИЖНОЕ-ПРАВИЛО-А", self.prompt())

    def test_empty_but_readable_base_is_not_a_fallback(self):
        """Таблица есть, читается, действующих ноль → секции нет. Книжные буллеты НЕ возвращаются:
        иначе снятие последнего урока молча вернуло бы старый источник."""
        with open(self.store, "w", encoding="utf-8") as f:
            f.write(LS.HEADER_LINE + "\n")
        self.assertEqual(suggest.active_lesson_bullets(), ((), True))
        got = suggest.load_playbook()
        self.assertNotIn("КНИЖНОЕ-ПРАВИЛО-А", got)
        self.assertNotIn(suggest._LEARNED_HEADER, got)
        self.assertIn("КНИЖНЫЙ-СТИЛЬ", got)          # прочие секции целы, книга не выпотрошена
        self.assertIn("КНИЖНЫЙ-ЗАПРЕТ", got)

    def test_broken_store_module_does_not_break_the_answer(self):
        """FAIL-SAFE пути ответа: хранилище бросило — черновик всё равно собирается, на книге."""
        self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        keep = LS.load

        def boom(*a, **kw):
            raise RuntimeError("диск отвалился")

        LS.load = boom
        self.addCleanup(lambda: setattr(LS, "load", keep))
        self.assertEqual(suggest.active_lesson_bullets(), ((), False))
        self.assertEqual(suggest.load_playbook(), suggest.load_playbook_file())


# =======================================================================================
# ФОРМА БУЛЛЕТА: урок обязан читаться разборщиком книги как ОДНО правило
# =======================================================================================
class TestBulletShape(_Base):

    def test_multiline_lesson_becomes_one_bullet(self):
        n, text = self.add_active("ПЕРВАЯ строка урока\nВТОРАЯ строка того же урока")
        self.assertIn("\n", text, "многострочность урока в таблице не сохранилась — тест пуст")
        merged = suggest.load_playbook()
        rules = suggest.list_playbook_rules(merged)
        self.assertEqual(len(rules), 1, "многострочный урок разорвал секцию на %d правил"
                         % len(rules))
        self.assertIn("ПЕРВАЯ строка урока ВТОРАЯ строка того же урока", rules[0]["rule"])

    def test_bullet_carries_the_day_from_the_base(self):
        self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        rules = suggest.list_playbook_rules(suggest.load_playbook())
        self.assertEqual(rules[0]["date"], WHEN[:10])

    def test_section_keeps_the_order_of_the_base(self):
        _, a = self.add_active("ПЕРВЫЙ урок по порядку номеров")
        _, b = self.add_active("ВТОРОЙ урок по порядку номеров", question="А ещё?", bot="Хм.")
        merged = suggest.load_playbook()
        self.assertLess(merged.index(a), merged.index(b))

    def test_book_without_the_section_gets_it_appended(self):
        """Книга без секции «Выученные правила» (её могли снести руками) — уроки всё равно
        доезжают: секция дописывается в конец, прочее не трогается."""
        with open(self.book, "w", encoding="utf-8") as f:
            f.write("# Playbook\n\n## Стиль общения\n- КНИЖНЫЙ-СТИЛЬ коротко\n")
        _, text = self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        got = suggest.load_playbook()
        self.assertIn("КНИЖНЫЙ-СТИЛЬ", got)
        self.assertIn(suggest._LEARNED_HEADER, got)
        self.assertIn(text, got)


# =======================================================================================
# ПОСЫЛКА СОСЕДНЕГО МЕСТА: /rules и удаление правила по номеру
# =======================================================================================
class TestNeighbourRulesCard(_Base):

    def test_rules_listing_stays_on_the_file_it_mutates(self):
        """`list_playbook_rules()` без аргумента показывает ФАЙЛ книги, а не промпт: по этим
        номерам `remove_playbook_rule` удаляет буллеты ИЗ ФАЙЛА. Разъедься они — «удали 3» било
        бы не по тому правилу. Цена решения названа в докстринге `list_playbook_rules`."""
        self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        listed = [r["rule"] for r in suggest.list_playbook_rules()]
        self.assertEqual(len(listed), 2)
        self.assertTrue(any("КНИЖНОЕ-ПРАВИЛО-А" in r for r in listed))
        self.assertTrue(any("КНИЖНОЕ-ПРАВИЛО-Б" in r for r in listed))

    def test_removal_by_number_still_hits_the_named_bullet(self):
        self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        res = suggest.remove_playbook_rule(1)
        self.assertEqual(res["status"], "removed")
        self.assertIn("КНИЖНОЕ-ПРАВИЛО-А", res["rule"])
        self.assertNotIn("КНИЖНОЕ-ПРАВИЛО-А", suggest.load_playbook_file())

    def test_conflict_search_stays_on_the_file_it_replaces(self):
        """`find_playbook_conflict` ищет в книге, потому что разрешение конфликта
        (`replace_playbook_rule`) книгу и правит."""
        self.add_active("ДЕЙСТВУЮЩИЙ называй цену сразу, если она в прайсе")
        got = suggest.find_playbook_conflict("КНИЖНОЕ-ПРАВИЛО-Б дублируй название модели")
        self.assertIn("КНИЖНОЕ-ПРАВИЛО-Б", got)


if __name__ == "__main__":
    unittest.main()
