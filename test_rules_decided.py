# -*- coding: utf-8 -*-
"""ПРИБОР КЛАССА «решение записано в узел — до бота не доехало».

ЧТО ОН МЕРЯЕТ. Для каждой записи реестра `rules_decided.RULES` промпт собирается ТОЙ ЖЕ
функцией, которой его собирает бот (`suggest.make_system_prompt`), и дословная формулировка
ищется В СОБРАННОМ ПРОМПТЕ. Исходов три: ДОЕХАЛО, НЕ ДОЕХАЛО, НЕИЗВЕСТНО.

ПОЧЕМУ НЕ ГРЕП ПО ИСХОДНИКУ. Признак, поднимаемый написанием слов в файле, негоден ПО
УСТРОЙСТВУ: строка может лежать в константе, которую никто не склеивает; в ветке под `if`,
которая на живом пути не берётся; в комментарии. Ровно так класс и живёт — узел `business_rules`
в `suggest.py` упомянут трижды и все три раза комментарием, а в промпт не подаётся ни одной
веткой. Греп на этом позеленел бы.

«НЕ ДОЕХАЛО» ЕСТЬ ПАДЕНИЕ ТЕСТА, А НЕ СТРОКА ОТЧЁТА — иначе прибор был бы докладчиком, а не
прибором, и молчаливое расхождение продолжилось бы при зелёном гейте.

«НЕИЗВЕСТНО» НЕ РАВНО «ХОРОШО». Промпт собрать не удалось — тест краснеет отдельным словами
сказанным сообщением о том, ЧТО именно помешало и ГДЕ. Ни одна ветка не превращает «не смог
проверить» в «проверено и хорошо».
"""
import unittest

import rules_decided


class TestRegistryShape(unittest.TestCase):
    """Форма реестра: четыре записи, все поля на месте, места — из объявленного списка."""

    def test_four_records_with_all_fields(self):
        self.assertEqual(len(rules_decided.RULES), 4)
        keys = [r["key"] for r in rules_decided.RULES]
        self.assertEqual(len(set(keys)), 4, "ключи записей обязаны быть уникальны: %s" % keys)
        for r in rules_decided.RULES:
            for field in ("key", "text", "decided", "block", "place"):
                self.assertTrue(str(r.get(field, "")).strip(),
                                "запись %r: поле %r пустое" % (r.get("key"), field))
            self.assertIn(r["place"], rules_decided.PLACES,
                          "запись %r указывает место вне объявленного списка" % r["key"])
            # Дата решения владельца, а не дата правки кода — форма ГГГГ-ММ-ДД.
            self.assertRegex(r["decided"], r"^\d{4}-\d{2}-\d{2}$")


class TestRulesReachAssembledPrompt(unittest.TestCase):
    """ГЛАВНОЕ: каждая запись реестра обязана найтись в СОБРАННОМ промпте."""

    def test_every_registry_rule_is_in_the_assembled_prompt(self):
        prompt, err = rules_decided.build_prompt()
        # НЕИЗВЕСТНО — тоже красное, и причина называется словами.
        self.assertEqual(err, "", "промпт собрать не удалось → НЕИЗВЕСТНО: %s" % err)
        self.assertTrue(prompt, "промпт собрался пустым → НЕИЗВЕСТНО, судить нечем")
        bad = []
        for key, outcome, why in rules_decided.check_all(prompt=prompt):
            if outcome != rules_decided.DELIVERED:
                bad.append("%s: %s — %s" % (key, outcome, why))
        self.assertEqual(bad, [], "правила не доехали до промпта:\n" + "\n".join(bad))

    def test_prompt_is_assembled_by_the_same_function_the_bot_uses(self):
        """Замок на подмену предмета: прибор обязан звать именно `make_system_prompt`.

        Без него однажды можно «починить» прибор, подсунув ему собственную склейку строк — и он
        позеленеет на промпте, которого голова не видит.
        """
        import suggest
        calls = []
        real = suggest.make_system_prompt

        def spy(*a, **kw):
            calls.append((a, kw))
            return real(*a, **kw)

        suggest.make_system_prompt = spy
        try:
            prompt, err = rules_decided.build_prompt()
        finally:
            suggest.make_system_prompt = real
        self.assertEqual(err, "")
        self.assertEqual(len(calls), 1, "промпт собран не через suggest.make_system_prompt")
        self.assertTrue(prompt)

    def test_rules_survive_without_the_playbook_block(self):
        """Правила обязаны доезжать на ТОМ минимуме, что голова видит ВСЕГДА.

        Блок книги правил появляется, только если вызывающий передал `playbook`. Если бы правило
        жило там, промпт без книги его бы не нёс — и клиент на этом пути остался бы без правила.
        """
        prompt, err = rules_decided.build_prompt(playbook="")
        self.assertEqual(err, "")
        self.assertNotIn("КНИГА ПРАВИЛ", prompt)
        for key, outcome, why in rules_decided.check_all(prompt=prompt):
            self.assertEqual(outcome, rules_decided.DELIVERED, "%s: %s — %s" % (key, outcome, why))


class TestInstrumentGoesRedWhenRuleIsLost(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ: без него прибор к работе не допускается.

    Одна запись реестра НАМЕРЕННО разводится с промптом ВО ВРЕМЕННОМ МЕСТЕ — в копии, живущей в
    памяти этого теста. Боевые файлы (`suggest.py`, `rules_decided.py`) при этом не меняются ни
    байтом: `RULES` — кортеж, копия записи делается словарём рядом, промпт мутируется как строка.
    """

    def test_rule_removed_from_the_prompt_is_reported_not_delivered(self):
        """Развод СО СТОРОНЫ ПРОМПТА — это ровно «правку откатили/потеряли при слиянии».

        Берём НАСТОЯЩУЮ запись реестра и вырезаем её формулировку из СОБРАННОГО промпта
        (временная строка в памяти). Прибор обязан сказать НЕ ДОЕХАЛО именно про неё и
        ДОЕХАЛО про остальные три — иначе он ловит не то, что должен.
        """
        prompt, err = rules_decided.build_prompt()
        self.assertEqual(err, "")
        victim = rules_decided.RULES[0]
        self.assertIn(victim["text"], prompt, "предусловие: до развода правило в промпте есть")
        crippled = prompt.replace(victim["text"], "")          # временная копия, файлы целы
        self.assertNotIn(victim["text"], crippled)

        outcomes = dict((k, o) for k, o, _ in rules_decided.check_all(prompt=crippled))
        self.assertEqual(outcomes[victim["key"]], rules_decided.NOT_DELIVERED,
                         "прибор не заметил пропажи правила %r — он негоден" % victim["key"])
        for r in rules_decided.RULES[1:]:
            self.assertEqual(outcomes[r["key"]], rules_decided.DELIVERED,
                             "пропажа одного правила не должна красить соседей (%r)" % r["key"])

        # И боевой реестр от развода не пострадал — та же запись, тот же текст.
        self.assertIs(rules_decided.RULES[0], victim)
        self.assertIn(victim["text"], prompt)

    def test_rule_reworded_in_the_registry_is_reported_not_delivered(self):
        """Развод СО СТОРОНЫ РЕЕСТРА — это «владелец переформулировал, а промпт не поправили»."""
        victim = dict(rules_decided.RULES[1])
        victim["text"] = victim["text"] + " И ЭТОГО ХВОСТА В ПРОМПТЕ НЕТ (развод 57-a)."
        key, outcome, why = rules_decided.check_all(rules=[victim])[0]
        self.assertEqual(outcome, rules_decided.NOT_DELIVERED, why)

    def test_unbuildable_prompt_is_unknown_and_never_delivered(self):
        """Третий исход: сборка отказала → НЕИЗВЕСТНО с причиной, и это НЕ «доехало»."""
        for key, outcome, why in rules_decided.check_all(
                prompt="", build_error="мост молчит (проба)"):
            self.assertEqual(outcome, rules_decided.UNKNOWN)
            self.assertIn("мост молчит", why)
        # Пустой промпт БЕЗ названной причины — тоже неизвестно, а не «нет строки».
        outcome, why = rules_decided.check_rule(rules_decided.RULES[0], "")
        self.assertEqual(outcome, rules_decided.UNKNOWN, why)


if __name__ == "__main__":
    unittest.main()
