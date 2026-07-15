# -*- coding: utf-8 -*-
"""
test_playbook_rules.py — /rules: показ книги правил с номерами + удаление правила репликой
(по номеру или тексту) с подтверждением ответным сообщением (родитель 112, шаг 5/8).

Storage-слой (suggest.list_playbook_rules / remove_playbook_rule) гоняется на ВРЕМЕННОМ
playbook (боевой не трогаем); командный слой (moderation_core.render_rules_card /
parse_rules_delete / process_rules_delete) — на инъектируемых показе/удалении, без файла и бота.

Нумерация показа и селектор-номер удаления берут правила в ОДНОМ порядке (файл: старые→новые),
поэтому «удали N» бьёт ровно то правило, что под номером N в /rules. Дедуп/лимит — забота append
(проверены в test_suggest); тут только показ и точечное удаление.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_playbook_rules -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import tempfile
import unittest

import suggest
import moderation_core as mc

TEACHERS = ("filipp", "danya", "dasha")


class _TmpPlaybook(unittest.TestCase):
    """Временный playbook с секцией «Выученные правила» (3 правила, порядок = нумерация /rules)."""

    SEED = (
        "# Playbook\n\n## Стиль общения\n- коротко\n\n## Выученные правила\n"
        "- (2026-07-01) Здоровайся дважды в одном диалоге для теплоты\n"
        "- (2026-07-02) Всегда предлагай шлем в подарок на неделю аренды\n"
        "- (2026-07-03) Уточняй район доставки до финального расчёта цены\n"
    )

    def setUp(self):
        self._pf = suggest.PLAYBOOK_FILE
        self._tmp = tempfile.TemporaryDirectory()
        self._file = os.path.join(self._tmp.name, "playbook.md")
        with open(self._file, "w", encoding="utf-8") as f:
            f.write(self.SEED)
        suggest.PLAYBOOK_FILE = self._file
        self._ia = suggest.INTAKE_APPROVERS
        suggest.INTAKE_APPROVERS = set(TEACHERS)

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._pf
        suggest.INTAKE_APPROVERS = self._ia
        self._tmp.cleanup()

    def _read(self):
        with open(self._file, encoding="utf-8") as f:
            return f.read()


class TestListAndShow(_TmpPlaybook):
    """ПОКАЗ: выученные правила нумеруются в порядке файла; статичный «Стиль общения» — не правило."""

    def test_list_numbers_learned_rules_in_file_order(self):
        rules = suggest.list_playbook_rules()
        self.assertEqual([1, 2, 3], [r["n"] for r in rules])
        self.assertEqual("Здоровайся дважды в одном диалоге для теплоты", rules[0]["rule"])
        self.assertEqual("Уточняй район доставки до финального расчёта цены", rules[2]["rule"])
        self.assertEqual("2026-07-02", rules[1]["date"])         # дата-префикс распознан
        # «коротко» из «Стиль общения» — НЕ выученное правило, в список не попадает
        self.assertNotIn("коротко", " || ".join(r["rule"] for r in rules).lower())

    def test_render_card_shows_numbers_to_teacher(self):
        card = mc.render_rules_card("danya")
        self.assertIn("1.", card)
        self.assertIn("2.", card)
        self.assertIn("3.", card)
        self.assertIn("Здоровайся дважды", card)
        self.assertIn("район доставки", card)
        self.assertIn("удали", card.lower())                     # подсказка про удаление

    def test_render_card_denied_for_stranger(self):
        card = mc.render_rules_card("stranger")
        self.assertIn("только Филипп", card)
        self.assertNotIn("Здоровайся дважды", card)              # чужому книгу не показываем

    def test_render_card_empty_book(self):
        with open(self._file, "w", encoding="utf-8") as f:
            f.write("# Playbook\n\n## Стиль общения\n- коротко\n")
        self.assertIn("пуста", mc.render_rules_card("filipp"))

    def test_is_rules_command_recognition(self):
        for t in ("/rules", " /rules ", "/RULES", "/rules@turbobot", "правила"):
            self.assertTrue(mc.is_rules_command(t), t)
        for t in ("удали 3", "правило дня", "покажи правила пожалуйста"):
            self.assertFalse(mc.is_rules_command(t), t)


class TestDeleteByNumber(_TmpPlaybook):
    """УДАЛЕНИЕ ПО НОМЕРУ: «удали 2» бьёт ровно 2-е правило /rules; итог — подтверждение реплаем."""

    def test_remove_by_number_hits_that_rule(self):
        res = suggest.remove_playbook_rule("2")
        self.assertEqual("removed", res["status"])
        self.assertEqual(2, res["n"])
        self.assertIn("шлем в подарок", res["rule"])
        self.assertEqual(2, res["remaining"])
        txt = self._read()
        self.assertNotIn("шлем в подарок", txt)                  # #2 убрано
        self.assertIn("Здоровайся дважды", txt)                  # соседние целы
        self.assertIn("район доставки", txt)
        self.assertIn("коротко", txt)                            # секция «Стиль» не тронута

    def test_process_delete_confirms_by_reply(self):
        dec = mc.process_rules_delete("удали 1", "filipp")
        self.assertEqual("removed", dec["decision"])
        self.assertIn("Удалил правило #1", dec["card"])
        self.assertIn("Здоровайся дважды", dec["card"])
        self.assertIn("Осталось правил: 2", dec["card"])         # подтверждение с остатком
        self.assertNotIn("Здоровайся дважды", self._read())

    def test_bare_number_reply_is_delete(self):
        # голый номер реплаем на список — однозначно удаление (карточка-список задаёт контекст)
        dec = mc.process_rules_delete("3", "dasha")
        self.assertEqual("removed", dec["decision"])
        self.assertIn("район доставки", dec["card"])

    def test_number_selector_matches_show_numbering(self):
        # инвариант «показ ↔ селектор»: номер N в /rules и в «удали N» — одно и то же правило
        shown = suggest.list_playbook_rules()
        target = next(r for r in shown if r["n"] == 2)["rule"]
        res = suggest.remove_playbook_rule("2")
        self.assertEqual(target, res["rule"])

    def test_delete_denied_for_stranger(self):
        dec = mc.process_rules_delete("удали 1", "stranger")
        self.assertEqual("denied", dec["decision"])
        self.assertIn("только Филипп", dec["card"])
        self.assertIn("Здоровайся дважды", self._read())        # чужой ничего не удалил


class TestDeleteByText(_TmpPlaybook):
    """УДАЛЕНИЕ ПО ТЕКСТУ: нечёткое совпадение (тот же матчинг, что дедуп); несколько похожих → уточнение."""

    def test_remove_by_text_fuzzy(self):
        dec = mc.process_rules_delete("удали шлем в подарок на неделю", "danya")
        self.assertEqual("removed", dec["decision"])
        self.assertIn("шлем в подарок", dec["card"])
        self.assertNotIn("шлем в подарок", self._read())

    def test_remove_by_text_with_word_pravilo(self):
        # «удали правило про район доставки» — слово «правило» отбрасывается, матч по остатку
        dec = mc.process_rules_delete("удали правило Уточняй район доставки до финального расчёта цены",
                                      "filipp")
        self.assertEqual("removed", dec["decision"])
        self.assertIn("район доставки", dec["card"])

    def test_ambiguous_text_asks_for_number(self):
        # добавим почти-двойника по теме «доставки», чтобы текст совпал с двумя правилами
        suggest.append_playbook_rule("Согласовывай район доставки с клиентом заранее")
        dec = mc.process_rules_delete("удали район доставки", "dasha")
        self.assertEqual("ambiguous", dec["decision"])
        self.assertIn("несколько правил", dec["card"])
        self.assertIn("#", dec["card"])                          # предлагает уточнить номером
        self.assertIn("район доставки", self._read())           # при неоднозначности НЕ удаляем


class TestDeleteNonexistent(_TmpPlaybook):
    """УДАЛЕНИЕ НЕСУЩЕСТВУЮЩЕГО: номер вне диапазона / чужой текст → not_found, книга цела."""

    def test_number_out_of_range(self):
        res = suggest.remove_playbook_rule("9")
        self.assertEqual("not_found", res["status"])
        self.assertEqual(3, len(suggest.list_playbook_rules()))  # ничего не убрано

    def test_process_number_out_of_range_card(self):
        dec = mc.process_rules_delete("удали 9", "filipp")
        self.assertEqual("not_found", dec["decision"])
        self.assertIn("Не нашёл", dec["card"])
        self.assertIn("9", dec["card"])
        self.assertEqual(self.SEED, self._read())                # книга дословно цела

    def test_text_no_match(self):
        dec = mc.process_rules_delete("удали депозит наличными в долларах", "danya")
        self.assertEqual("not_found", dec["decision"])
        self.assertEqual(3, len(suggest.list_playbook_rules()))

    def test_zero_number_not_found(self):
        self.assertEqual("not_found", suggest.remove_playbook_rule("0")["status"])

    def test_empty_book_delete(self):
        with open(self._file, "w", encoding="utf-8") as f:
            f.write("# Playbook\n\n## Стиль общения\n- коротко\n")
        res = suggest.remove_playbook_rule("1")
        self.assertEqual("empty", res["status"])
        dec = mc.process_rules_delete("удали 1", "filipp")
        self.assertEqual("empty", dec["decision"])
        self.assertIn("пуста", dec["card"])


class TestDeleteParsingAndFailsafe(_TmpPlaybook):
    """Разбор селектора и FAIL-SAFE: не-удаление → not_rules; сбой хранилища → error, бот жив."""

    def test_non_delete_reply_is_not_rules(self):
        for t in ("спасибо", "покажи ещё", "", None, "это правило хорошее"):
            self.assertIsNone(mc.parse_rules_delete(t), repr(t))
            self.assertEqual("not_rules", mc.process_rules_delete(t, "filipp")["decision"], repr(t))

    def test_delete_verbs_extract_selector(self):
        self.assertEqual("3", mc.parse_rules_delete("удали 3"))
        self.assertEqual("3", mc.parse_rules_delete("удалить правило 3"))
        self.assertEqual("шлем", mc.parse_rules_delete("убери шлем"))
        self.assertEqual("2", mc.parse_rules_delete("delete 2"))
        self.assertEqual("2", mc.parse_rules_delete("№2"))       # голый номер с решёткой-«№»

    def test_storage_failure_is_error_not_crash(self):
        boom = lambda sel: (_ for _ in ()).throw(RuntimeError("disk gone"))
        dec = mc.process_rules_delete("удали 1", "filipp", remove=boom)
        self.assertEqual("error", dec["decision"])
        self.assertIn("Не удалось удалить", dec["card"])

    def test_remove_failsafe_on_missing_file(self):
        suggest.PLAYBOOK_FILE = os.path.join(self._tmp.name, "nope", "playbook.md")
        self.assertEqual("error", suggest.remove_playbook_rule("1")["status"])


class TestDedupLimitAwareness(_TmpPlaybook):
    """Дедуп/лимит из ХРАНИЛИЩА учтены: после удаления освобождённое место снова заполнимо, а
    повторное добавление того же правила по-прежнему дедупится штатно (append не сломан удалением)."""

    def test_delete_then_readd_same_rule(self):
        suggest.remove_playbook_rule("2")                        # убрали «шлем в подарок»
        self.assertEqual("added", suggest.append_playbook_rule("Всегда предлагай шлем в подарок на неделю аренды"))
        self.assertIn("шлем в подарок", self._read())            # вернулось (место освободилось)

    def test_dedup_still_blocks_existing_after_delete(self):
        suggest.remove_playbook_rule("2")                        # трогаем другое правило
        # оставшееся «район доставки» всё ещё дедупится (append не разъехался с файлом после удаления)
        self.assertEqual("duplicate",
                         suggest.append_playbook_rule("уточняй район доставки до финального расчёта цены"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
