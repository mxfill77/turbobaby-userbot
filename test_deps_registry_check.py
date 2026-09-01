#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_deps_registry_check.py — проверки СТОРОЖА РЕЕСТРОВ (deps_registry_check.py).

Стережём здесь ровно одно: третий исход. Сторож обязан различать «тест упал» (расхождение
реестра — коммит держим) и «тест не нашёлся/не загрузился» (сверка НЕ состоялась — коммит
пропускаем, но громко). Если эти два исхода слипнутся, сторож начнёт стеречь пустоту молча —
то есть станет ровно той дырой, ради закрытия которой заведён.

Гоняем ЧИСТУЮ функцию `classify` (ни git, ни подпроцессов): она и есть всё собственное решение
модуля, остальное — оболочка над `-m unittest`.
"""
import unittest

import deps_registry_check as d


class TestClassify(unittest.TestCase):

    def test_all_green_is_ok(self):
        v, why = d.classify(0, "...\n----\nRan 3 tests in 1.9s\n\nOK\n")
        self.assertEqual(v, d.OK)
        self.assertIn("совпали", why)

    def test_failed_assertion_is_divergence(self):
        out = ("FAIL: test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap\n"
               "AssertionError: Items in the first set but not the second:\n'model_name.py'\n"
               "Ran 3 tests in 1.8s\n\nFAILED (failures=1)\n")
        self.assertEqual(d.classify(1, out)[0], d.DIVERGED)

    def test_renamed_guard_test_is_unknown_not_green(self):
        """Тест-сторож переименовали — unittest отдаёт _FailedTest и НЕНУЛЕВОЙ код. Назвать это
        расхождением реестра было бы враньём (реестр никто не сверял), зелёным — вдвойне."""
        out = ("ERROR: test_gone (unittest.loader._FailedTest.test_gone)\n"
               "AttributeError: module 'test_pc_orchestrator' has no attribute 'test_gone'\n"
               "Ran 1 test in 0.0s\n\nFAILED (errors=1)\n")
        v, why = d.classify(1, out)
        self.assertEqual(v, d.UNKNOWN)
        self.assertIn("НЕ ЗАГРУЗИЛИСЬ", why)

    def test_missing_module_is_unknown(self):
        v, _ = d.classify(1, "ModuleNotFoundError: No module named 'test_pc_orchestrator'\n")
        self.assertEqual(v, d.UNKNOWN)

    def test_partial_run_is_unknown_even_with_zero_code(self):
        """Прогнали меньше, чем просили, — сверка НЕПОЛНАЯ. Нулевой код возврата этого не лечит:
        «часть сторожей промолчала» и «все сказали да» — разные вещи."""
        v, why = d.classify(0, "Ran 2 tests in 1.0s\n\nOK\n")
        self.assertEqual(v, d.UNKNOWN)
        self.assertIn("2", why)

    def test_no_ran_line_is_unknown(self):
        """Вывода нет вовсе (таймаут, убитый процесс) → «неизвестно», а не «хорошо»."""
        self.assertEqual(d.classify(0, "")[0], d.UNKNOWN)

    def test_guard_list_is_not_empty_and_names_are_full_paths(self):
        """Список сторожей обязан быть непустым и адресовать МОДУЛЬ.КЛАСС.МЕТОД — иначе unittest
        молча прогонит не то, что нужно."""
        self.assertTrue(d.GUARD_TESTS)
        for name in d.GUARD_TESTS:
            self.assertEqual(name.count("."), 2, name)
            self.assertTrue(name.startswith("test_"), name)


if __name__ == "__main__":
    unittest.main()
