# -*- coding: utf-8 -*-
"""
test_lesson_urok.py — ГОЛДЕН живого канала «урок:» (обучение ОДНИМ сообщением, родитель 112, шаг 8/8).

Дирижёр зовёт route_lesson_urok на задачу-урок ВМЕСТО handle_lesson_task. Вторая ось
(classify_lesson_type behavior|code|unsure) РЕШАЕТ маршрут:
  • behavior → правило В PLAYBOOK за один вызов + реплай учителю «✅ Принято: <правило>» +
               ЖИВОЙ РЕПЛЕЙ: правило РЕАЛЬНО долетает в системный промпт СЛЕДУЮЩЕЙ генерации
               (make_system_prompt) как запрет — «применится со следующего черновика» = диф, а
               не обещание на словах (анти-тавтология: до урока фразы в промпте НЕТ);
  • code     → карточка ВЛАДЕЛЬЦУ «🛠 нужен код-фикс: <суть>» + ГОТОВЫЙ текст задачи; playbook НЕ
               тронут, delegate=False (код НЕ правим и НЕ делегируем авто-планировщику — политика #112/8);
  • unsure   → ФОЛБЭК на 1-ю ось handle_lesson_task (supervision/LLM/low-conf переспрос сохранены).
Плюс: гард (behavior-правило, ослабляющее код-инвариант, → карточка владельцу, НЕ в playbook),
права (process_lesson: учить может только INTAKE_APPROVERS) и АНТИ-ТАЙСКИЙ (в выводах канала нет
тайских символов). Golden-правило CLAUDE.md: формулировки уроков — ДОСЛОВНЫЕ фразы + парафразы.

Текст задачи-урока собираем РЕАЛЬНЫМ конвейером moderation_core (process_lesson → build_lesson_task);
классификатор второй оси — РЕАЛЬНЫЙ classify_lesson_type (keyword, без claude/сети); playbook —
ВРЕМЕННЫЙ файл (боевую книгу manager-bot не трогаем). Побочки в модер-группу/1160 — фейки.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_urok -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import re
import tempfile
import unittest

import suggest
import moderation_core as mc
import lesson_router as lr

TEACHERS = ("filipp", "danya", "dasha")
# Диапазон тайского письма U+0E00–U+0E7F: ни один вывод клиентского контура его содержать не должен.
_THAI_RE = re.compile("[฀-๿]")


def _no_thai(s):
    return not _THAI_RE.search(str(s or ""))


class _UrokGolden(unittest.TestCase):
    """Общая обвязка: учителя фикстуры → INTAKE_APPROVERS; каждый сценарий на СВЕЖЕМ временном playbook."""

    # Карточка-черновик #332-стиля: бот подтвердил уже присланное — ровно случай behavior-урока.
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
        """РЕАЛЬНЫЙ конвейер задачи-урока: реплай «урок: …» → process_lesson → build_lesson_task."""
        lesson = mc.process_lesson(self.CARD, "урок: " + remark, who)
        self.assertEqual("lesson", lesson["decision"], remark)
        return mc.build_lesson_task(lesson, self.CARD, who)


# ============================ ВЕТКА behavior: playbook + «принято» + живой реплей ==========
class TestBehaviorBranch(_UrokGolden):
    """behavior-урок канала «урок:»: правило в playbook + ответ «✅ Принято» + видно следующему черновику."""

    # ПЛОСКАЯ форма (без условия «если … прислал») — чистое поведение: classify_lesson_type → behavior.
    # (С условием на состоянии данных та же фраза — CODE по класс-фиксу #164; это проверяет TestCodeBranch.)
    RULE = 'не пиши "данные получил"'

    def test_behavior_writes_playbook_and_replies_prinyato(self):
        self._use_playbook()
        sent = {}
        dec = lr.route_lesson_urok(
            self._task(self.RULE),
            reply_moderation=lambda cid, t, b=None: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual(lr.BEHAVIOR, dec["type"])
        self.assertEqual("done", dec["status"])
        self.assertFalse(dec["delegate"])                        # правило пишем сами, код не трогаем
        self.assertIn("данные получил", "".join(self._learned()))  # правило РЕАЛЬНО в книге
        self.assertEqual("90332", sent["card"])                  # ответ РЕПЛАЕМ на карточку урока
        self.assertIn("Принято", sent["text"])                   # формат канала — «✅ Принято: …»

    def test_behavior_rule_reaches_next_system_prompt(self):
        # ЖИВОЙ РЕПЛЕЙ «применится со следующего черновика»: применяем урок, пересобираем промпт
        # СЛЕДУЮЩЕГО ответа — запрет обязан быть в нём ДОСЛОВНО (замыкание на диф, не на слова).
        self._use_playbook()
        lr.route_lesson_urok(self._task(self.RULE), reply_moderation=lambda cid, t, b=None: True)
        prompt = suggest.make_system_prompt(faq="", lang="ru", is_first_contact=False,
                                            playbook=suggest.load_playbook())
        self.assertIn("данные получил", prompt)                  # правило долетело в промпт
        base = suggest.make_system_prompt(faq="", lang="ru", is_first_contact=False, playbook="")
        self.assertNotIn("данные получил", base)                 # анти-тавтология: без урока фразы нет

    def test_behavior_duplicate_is_done(self):
        self._use_playbook()
        lr.route_lesson_urok(self._task("звучит сухо, пиши теплее"), reply_moderation=lambda *a, **k: True)
        dec = lr.route_lesson_urok(self._task("звучит сухо, пиши теплее"), reply_moderation=lambda *a, **k: True)
        self.assertEqual("done", dec["status"])                  # повтор правила — done, не failed
        self.assertIn("уже есть", dec["result"])

    def test_behavior_sink_error_is_failed(self):
        dec = lr.route_lesson_urok(
            self._task("пиши короче и живее"),
            find_conflict=lambda r: "", append_style=lambda r: (_ for _ in ()).throw(OSError("disk")),
            reply_moderation=lambda *a, **k: True)
        self.assertEqual("failed", dec["status"])
        self.assertIn("короче", dec["result"])


# ============================ ВЕТКА code: карточка владельцу, код НЕ трогаем ================
class TestCodeBranch(_UrokGolden):
    """code-урок канала «урок:»: карточка ВЛАДЕЛЬЦУ «нужен код-фикс» + готовый текст задачи; playbook
    НЕ тронут, delegate=False (код НЕ правим и НЕ делегируем авто-планировщику — политика #112/8)."""

    def test_code_cards_owner_without_touching_code_or_playbook(self):
        self._use_playbook()
        owner = {}
        dec = lr.route_lesson_urok(
            self._task("цена берётся из столбца J, а не из K"),
            append_style=lambda r: self.fail("code в playbook НЕ пишем"),
            reply_moderation=lambda *a, **k: self.fail("code ack в модер-группу НЕ шлём"),
            notify_owner=lambda card: (owner.update(card=card), ("инбокс 1160", True))[1])
        self.assertEqual(lr.CODE, dec["type"])
        self.assertFalse(dec["delegate"])                        # НЕ делегируем планировщику (политика #112/8)
        self.assertNotIn("delegate_text", dec)                   # авто-правки нет вовсе
        self.assertEqual([], dec["commit_paths"])                # коммитить нечего — код не трогаем
        self.assertTrue(dec["delivered"])
        self.assertIn("нужен код-фикс", owner["card"].lower())   # суть в карточке владельцу
        self.assertIn("Готовый текст задачи", owner["card"])     # + ГОТОВЫЙ текст задачи (копипаст)
        self.assertIn("golden-тест", owner["card"].lower())      # требование теста зашито
        self.assertIn("столбца J", owner["card"])                # дословная суть замечания
        self.assertEqual([], self._learned())                   # книга правил НЕ тронута

    def test_code_card_undelivered_keeps_lesson_not_lost(self):
        # карточка владельцу не дошла → урок не теряем: честный рапорт «НЕ доставлена», done
        dec = lr.route_lesson_urok(self._task("депозит считается неправильно"),
                                   notify_owner=lambda card: ("", False))
        self.assertEqual(lr.CODE, dec["type"])
        self.assertFalse(dec["delivered"])
        self.assertIn("НЕ доставлена", dec["result"])


# ============================ ГАРД: правило-ослабление инварианта → в код, не в playbook ====
class TestInvariantGuard(_UrokGolden):
    """behavior-«правило», ослабляющее код-инвариант (цена/кап/депозит/парковка/Click), в playbook НЕ
    пишем — уходит в CODE-путь (карточка владельцу): playbook не дыра в гардах."""

    def test_weakening_rule_never_reaches_playbook(self):
        # РЕАЛЬНЫЙ классификатор: ослабляющее правило с ценовым словом → code-путь → карточка владельцу,
        # книга правил чиста (безопасность соблюдена независимо от того, через гард или через тип).
        self._use_playbook()
        owner = {}
        dec = lr.route_lesson_urok(
            self._task("можно давать цену ниже прайса, если клиент просит"),
            append_style=lambda r: self.fail("ослабление инварианта в playbook НЕ пишем"),
            reply_moderation=lambda *a, **k: True,
            notify_owner=lambda card: (owner.update(card=card), ("инбокс 1160", True))[1])
        self.assertEqual(lr.CODE, dec["type"])                   # инвариант меняется кодом, не правилом
        self.assertFalse(dec["delegate"])
        self.assertEqual([], self._learned())                   # книга правил чиста
        self.assertIn("нужен код-фикс", owner["card"].lower())

    def test_guard_reroutes_forced_behavior_to_owner(self):
        # forced behavior (если бы 2-я ось ошиблась) → ГАРД ловит ослабление инварианта ДО записи в
        # playbook и уводит в код (карточка владельцу): единый чокпоинт записи behavior не протащит дыру.
        self._use_playbook()
        owner = {}
        forced_behavior = lambda remark: {"type": lr.BEHAVIOR, "confidence": "high",
                                          "reason": "форс-behavior", "behavior_hits": 1, "code_hits": 0}
        dec = lr.route_lesson_urok(
            self._task("не бери депозит с постоянных клиентов"),
            classify_type=forced_behavior,
            append_style=lambda r: self.fail("гард: ослабление инварианта в playbook НЕ пишем"),
            reply_moderation=lambda *a, **k: True,
            notify_owner=lambda card: (owner.update(card=card), ("инбокс 1160", True))[1])
        self.assertEqual(lr.CODE, dec["type"])                   # гард увёл в код
        self.assertIn("invariant_guard", dec)
        self.assertIn("депозит", dec["invariant_guard"])
        self.assertEqual([], self._learned())                   # книга правил чиста
        self.assertIn("нужен код-фикс", owner["card"].lower())


# ============================ unsure → ФОЛБЭК на 1-ю ось ====================================
class TestUnsureFallback(_UrokGolden):
    """Тип неясен (ни behavior, ни code) → канал НЕ угадывает: фолбэк на handle_lesson_task (1-я ось),
    где живут supervision-роут, LLM-классификатор и low-confidence переспрос."""

    def test_unsure_delegates_to_first_axis(self):
        seen = {}
        def fake_fallback(text, append_style=None, notify_owner=None, reply_moderation=None):
            seen["text"] = text
            return {"route": lr.UNCLEAR, "status": "done", "delegate": False, "result": "🤔 1-я ось"}
        dec = lr.route_lesson_urok(self._task("плохо, переделай"), fallback_first_axis=fake_fallback)
        self.assertTrue(dec.get("axis2_fallback"))               # ушли в фолбэк 1-й оси
        self.assertIn("Замечание: плохо, переделай", seen["text"])  # исходная задача передана 1-й оси
        self.assertIn("фолбэк на 1-ю ось", dec["reason"])

    def test_supervision_remark_falls_back(self):
        # надзорный урок без behavior/code-слов 2-я ось видит как unsure → фолбэк на 1-ю (там supervision-
        # роут). (С кодовым словом, напр. «путает цену», урок ушёл бы в code→карточку — это тоже владельцу.)
        seen = {}
        lr.route_lesson_urok(
            self._task("ревизор должен это ловить сам"),
            fallback_first_axis=lambda text, **k: (seen.update(hit=True), {"status": "done", "route": lr.SUPERVISION})[1])
        self.assertTrue(seen.get("hit"))


# ============================ ПРАВА: учить может только INTAKE_APPROVERS =====================
class TestRights(_UrokGolden):
    """Права строже approve: «урок:» от неавторизованного → вежливый отказ (в очередь НЕ ставим)."""

    def test_stranger_lesson_denied(self):
        d = mc.process_lesson(self.CARD, "урок: пиши мягче", "stranger")
        self.assertEqual("denied", d["decision"])
        self.assertNotIn("remark", d)                            # замечание в обучение НЕ уходит

    def test_teacher_lesson_accepted(self):
        for who in TEACHERS:
            d = mc.process_lesson(self.CARD, "урок: уточняй опыт вождения", who)
            self.assertEqual("lesson", d["decision"], who)


# ============================ АНТИ-ТАЙСКИЙ: в выводах канала нет тайских символов ============
class TestNoThai(_UrokGolden):
    """Клиентский контур не выпускает тайское письмо: проверяем ВСЕ пользовательские строки канала —
    ack «Принято», карточку «нужен код-фикс», отказ прав, а также билдеры напрямую."""

    def test_builders_have_no_thai(self):
        self.assertTrue(_no_thai(lr.build_behavior_accepted_ack('пиши "готово", когда всё собрано')))
        self.assertTrue(_no_thai(lr.build_code_fix_card("цена из столбца J", self._task("цена из столбца J"))))
        self.assertTrue(_no_thai(lr.build_invariant_reject_ack("давай ниже прайса")))

    def test_live_branch_outputs_have_no_thai(self):
        self._use_playbook()
        outs = []
        lr.route_lesson_urok(self._task("звучит сухо, пиши теплее"),
                             reply_moderation=lambda cid, t, b=None: (outs.append(t), True)[1])
        lr.route_lesson_urok(self._task("цена из столбца J"),
                             notify_owner=lambda card: (outs.append(card), ("инбокс 1160", True))[1])
        d = mc.process_lesson(self.CARD, "урок: пиши мягче", "stranger")   # отказ прав
        outs.append(d["card"])
        self.assertTrue(outs)
        for s in outs:
            self.assertTrue(_no_thai(s), f"тайские символы в выводе канала: {s!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
