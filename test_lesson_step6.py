# -*- coding: utf-8 -*-
"""
test_lesson_step6.py — СВОДНЫЙ приёмочный голден ВТОРОЙ оси урока (родитель 112, шаг 6/8).

Шаги 1–5 дали классификатор второй оси (behavior|code|unsure), playbook с FIFO-лимитом,
маршрут route_lesson_by_type, гард «правило не смеет ослаблять код-инвариант» и /rules
(показ+удаление). Шаг 6/8 сшивает ТРИ канонических сценария спеки в ОДИН приёмочный прогон
на РЕАЛЬНЫХ функциях обоих модулей (не только инъектированных фейках) + временный playbook —
и закрывает главный ещё не замкнутый разрыв «тест ≠ реальность» второй оси: обещание
behavior-ack «действует со следующего черновика».

  (а) «не пиши "данные получил"» → behavior → правило В PLAYBOOK за один вызов + ack Филиппу
      «действует со следующего черновика». ЖИВОЙ РЕПЛЕЙ: выученное правило РЕАЛЬНО долетает в
      системный промпт СЛЕДУЮЩЕЙ генерации (make_system_prompt) как запрет — значит «следующий
      черновик БЕЗ фразы» не обещание на словах, а диф, доехавший до промпта, который его родит.
      Анти-тавтология: до применения урока фразы в промпте НЕТ (замыкание на диф, не тавтология).
  (б) «цена из столбца J» → code → в дев-очередь ПК (delegate=True) с ОБЯЗАТЕЛЬНЫМ golden-тестом
      дословной фразой клиента и запретом Bridge/денег; сами код не трогаем.
  (в) конфликтующее правило (та же тема, обратная полярность) → НЕ затираем вслепую: показываем
      ОБА и спрашиваем кнопками «оставить новое/старое»; выбор «оставить новое» РЕАЛЬНО заменяет
      правило во временном playbook (старое ушло, новое на месте).

Текст задачи-урока собираем РЕАЛЬНЫМ конвейером moderation_core (process_lesson → build_lesson_
task), чтобы прогон шёл по живой цепочке сборки, а не по идеал-строке. Классификатор второй оси —
РЕАЛЬНЫЙ classify_lesson_type (keyword-детерминированный, без claude/сети). Побочки в модер-группу/
1160 — фейки; playbook — ВРЕМЕННЫЙ файл (боевой manager-bot/docs/playbook.md не трогаем).
Golden-правило CLAUDE.md: формулировки уроков — дословные примеры спеки шага 1/8.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_step6 -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import tempfile
import unittest

import suggest
import moderation_core as mc
import lesson_router as lr

TEACHERS = ("filipp", "danya", "dasha")


class _SecondAxisGolden(unittest.TestCase):
    """Общая обвязка: учителя фикстуры → INTAKE_APPROVERS (триггер урока их пускает); каждый
    сценарий гоняется на СВЕЖЕМ временном playbook (боевую книгу правил не трогаем)."""

    # Карточка черновика #332-стиля: бот подтвердил уже присланное — ровно тот случай, что правит (а).
    CARD = {"id": 42, "client_id": 606, "client_ref": "@nikita",
            "draft": "Локацию и данные получил, спасибо! Оформляю бронь.", "card_msg_id": 90332}

    def setUp(self):
        self._save = (suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES, suggest.PLAYBOOK_FILE)
        suggest.INTAKE_APPROVERS = set(TEACHERS)
        suggest.APPROVER_USERNAMES = set()
        self._tmp = tempfile.TemporaryDirectory()
        self._pf = os.path.join(self._tmp.name, "playbook.md")

    def tearDown(self):
        suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES, suggest.PLAYBOOK_FILE = self._save
        self._tmp.cleanup()

    def _use_playbook(self, seed="# Playbook\n\n## Выученные правила\n"):
        with open(self._pf, "w", encoding="utf-8") as f:
            f.write(seed)
        suggest.PLAYBOOK_FILE = self._pf

    def _learned(self):
        with open(self._pf, encoding="utf-8") as f:
            return suggest._playbook_learned_rules(f.read())

    def _task(self, remark, who="filipp"):
        """РЕАЛЬНЫЙ конвейер сборки задачи-урока: реплай «не так: …» → process_lesson → build_lesson_
        task (живая цепочка, а не идеал-строка). Возвращает текст задачи для route_lesson_by_type."""
        lesson = mc.process_lesson(self.CARD, "не так: " + remark, who)
        self.assertEqual("lesson", lesson["decision"], remark)
        return mc.build_lesson_task(lesson, self.CARD, who)


class TestScenarioA_BehaviorToPlaybookAndNextDraft(_SecondAxisGolden):
    """(а) «не пиши "данные получил"» → правило в playbook за секунды + ЖИВОЙ реплей: правило
    долетает в промпт СЛЕДУЮЩЕЙ генерации как запрет (значит следующий черновик — без фразы)."""

    RULE = 'не пиши "данные получил"'

    def test_behavior_rule_lands_in_playbook_and_acks(self):
        # РЕАЛЬНЫЙ classify_lesson_type (без инъекции) + РЕАЛЬНЫЙ append в ВРЕМЕННЫЙ playbook.
        self._use_playbook()
        sent = {}
        dec = lr.route_lesson_by_type(
            self._task(self.RULE),
            reply_moderation=lambda cid, t, b=None: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual(lr.BEHAVIOR, dec["type"])              # вторая ось = поведение
        self.assertEqual("done", dec["status"])
        self.assertFalse(dec["delegate"])                      # правило пишем сами, код не трогаем
        self.assertIn('не пиши "данные получил"', self._learned())   # правило РЕАЛЬНО в книге за один вызов
        self.assertEqual("90332", sent["card"])                # ack реплаем на карточку урока
        self.assertIn("действует со следующего черновика", sent["text"])

    def test_learned_rule_reaches_next_system_prompt(self):
        # ЖИВОЙ РЕПЛЕЙ обещания «со следующего черновика»: применяем урок РЕАЛЬНЫМ sink'ом, затем
        # пересобираем системный промпт СЛЕДУЮЩЕГО ответа — запрет обязан быть в нём ДОСЛОВНО.
        self._use_playbook()
        lr.route_lesson_by_type(self._task(self.RULE),
                                reply_moderation=lambda cid, t, b=None: True)
        prompt = suggest.make_system_prompt(faq="", lang="ru", is_first_contact=False,
                                            playbook=suggest.load_playbook())
        self.assertIn("КНИГА ПРАВИЛ", prompt)                  # блок playbook подмешан
        self.assertIn("данные получил", prompt)                # запрет долетел в промпт → черновик его учтёт
        # анти-тавтология: БЕЗ применения урока (playbook пуст) фразы в промпте нет — замыкание на диф
        base = suggest.make_system_prompt(faq="", lang="ru", is_first_contact=False, playbook="")
        self.assertNotIn("данные получил", base)

    def test_spec_example_classifies_as_behavior(self):
        # дословный пример спеки шага 1/8 → behavior/high (замок классификатора второй оси)
        d = lr.classify_lesson_type(self.RULE)
        self.assertEqual(lr.BEHAVIOR, d["type"])
        self.assertEqual("high", d["confidence"])


class TestScenarioB_CodeToDevQueue(_SecondAxisGolden):
    """(б) «цена из столбца J» → code-путь: задача в дев-очередь ПК с обязательным golden-тестом."""

    def test_code_rule_delegates_to_dev_queue_with_test(self):
        # playbook в этом сценарии не трогается вовсе — code лечится КОДОМ, а не правилом в книге.
        self._use_playbook()
        dec = lr.route_lesson_by_type(
            self._task("цена берётся из столбца J, а не из K"),
            append_style=lambda r: self.fail("code в playbook НЕ пишем"),
            reply_moderation=lambda cid, t, b=None: self.fail("code ack в модер-группу НЕ шлём"))
        self.assertEqual(lr.CODE, dec["type"])
        self.assertTrue(dec["delegate"])                       # → дев-очередь ПК (как ФАКТ первой оси)
        self.assertIn("столбца J", dec["delegate_text"])       # исходный урок в тексте задачи планировщику
        self.assertIn("юнит-тест", dec["delegate_text"])       # golden-тест ОБЯЗАТЕЛЕН
        self.assertIn("ДОСЛОВНОЙ фразой клиента", dec["delegate_text"])
        self.assertIn("Bridge", dec["delegate_text"])          # Bridge/деньги запрещены явно
        self.assertEqual([], self._learned())                  # книга правил не тронута

    def test_spec_example_classifies_as_code(self):
        # дословный пример спеки шага 1/8 → code/high
        d = lr.classify_lesson_type("цена из столбца J")
        self.assertEqual(lr.CODE, d["type"])
        self.assertEqual("high", d["confidence"])


class TestScenarioC_ConflictAsksBothThenReplaces(_SecondAxisGolden):
    """(в) конфликтующее правило → показать ОБА и спросить кнопками; «оставить новое» РЕАЛЬНО
    заменяет правило во временном playbook (старое убрано, новое на месте)."""

    OLD = "здоровайся дважды в одном диалоге"
    NEW = "не здоровайся дважды в одном диалоге"

    def _seed_with_old(self):
        self._use_playbook(f"# Playbook\n\n## Выученные правила\n- (2026-07-01) {self.OLD}\n")

    def test_conflict_shows_both_variants_and_asks(self):
        # новое правило противоречит записанному (та же тема, обратная полярность) → НЕ затираем:
        # реплаем показываем ОБА варианта + кнопки, ждём выбора учителя (РЕАЛЬНЫЙ find_playbook_conflict).
        self._seed_with_old()
        asked = {}
        dec = lr.route_lesson_by_type(
            self._task(self.NEW),
            append_style=lambda r: self.fail("при конфликте вслепую НЕ пишем"),
            reply_moderation=lambda cid, t, b=None: (asked.update(text=t, buttons=b), True)[1])
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("behavior_conflict", dec["resolve"])
        self.assertIn("pending_conflict", dec)
        self.assertEqual([lr.BEHAVIOR_KEEP_NEW, lr.BEHAVIOR_KEEP_OLD], asked["buttons"])  # кнопки в реплае
        self.assertIn(f"НОВОЕ: {self.NEW}", asked["text"])     # показали ОБА варианта
        self.assertIn(f"СТАРОЕ: {self.OLD}", asked["text"])
        self.assertIn(self.OLD, self._learned())               # при неоднозначности книгу НЕ тронули

    def test_keep_new_replaces_rule_in_real_playbook(self):
        # доводим до конца: «оставить новое» → РЕАЛЬНЫЙ replace_playbook_rule на временном файле:
        # старое правило исчезает, новое (обратной полярности) остаётся единственным.
        self._seed_with_old()
        dec = lr.route_lesson_by_type(self._task(self.NEW),
                                      reply_moderation=lambda cid, t, b=None: True)
        sent = {}
        dec2 = lr.resume_behavior_conflict(
            dec["pending_conflict"], "оставить новое",
            reply_moderation=lambda cid, t, b=None: (sent.update(text=t), True)[1])
        self.assertEqual("done", dec2["status"])
        self.assertEqual("kept_new", dec2["resolved"])
        self.assertIn("действует со следующего черновика", sent["text"])
        learned = self._learned()
        self.assertIn(self.NEW, learned)                       # новое записано
        self.assertNotIn(self.OLD, learned)                    # старое убрано (не задвоили полярности)
        self.assertEqual(1, len(learned))                      # ровно одно правило по теме приветствия

    def test_keep_old_writes_nothing(self):
        # контроль: «оставить старое» → книга не меняется, новое не пишем
        self._seed_with_old()
        dec = lr.route_lesson_by_type(self._task(self.NEW),
                                      reply_moderation=lambda cid, t, b=None: True)
        dec2 = lr.resume_behavior_conflict(dec["pending_conflict"], "оставить старое",
                                           reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual("kept_old", dec2["resolved"])
        self.assertIn(self.OLD, self._learned())
        self.assertNotIn(self.NEW, self._learned())


if __name__ == "__main__":
    unittest.main(verbosity=2)
