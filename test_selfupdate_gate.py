# -*- coding: utf-8 -*-
"""Тесты гейта самообновления. Валидный код → апдейт проходит; битый (синтаксис или
import) → отклонён, а сам тест-процесс продолжает жить (гейт — чистая проверка)."""

import os
import sys
import tempfile
import unittest

import selfupdate_gate

PY = sys.executable


class TestSelfUpdateGate(unittest.TestCase):
    def _write(self, d, name, body):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return name

    def test_valid_code_passes(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "modok.py", "VALUE = 42\n")
            ok, msg = selfupdate_gate.code_gate(PY, d, ["modok.py"], "modok")
            self.assertTrue(ok, msg)
            self.assertEqual(msg, "ok")

    def test_syntax_error_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "modbad.py", "def broken(:\n    pass\n")   # синтаксическая ошибка
            ok, msg = selfupdate_gate.code_gate(PY, d, ["modbad.py"], "modbad")
            self.assertFalse(ok)
            self.assertIn("py_compile", msg)
        # процесс теста жив — гейт ничего не убивает
        self.assertTrue(True)

    def test_import_time_error_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            # компилируется, но падает при импорте → ловит import-smoke, а не py_compile
            self._write(d, "modimp.py", "import module_that_does_not_exist_xyz\n")
            ok, msg = selfupdate_gate.code_gate(PY, d, ["modimp.py"], "modimp")
            self.assertFalse(ok)
            self.assertIn("import-smoke", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
