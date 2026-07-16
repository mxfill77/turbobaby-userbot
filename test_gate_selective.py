# -*- coding: utf-8 -*-
"""Тесты селективного тест-гейта (порт VPS GATE_STEP_SELECTIVE / GATE_SINGLE_SELECTIVE).

Проверяем: разбор шага цепи, сопоставление затронутых тестов, полный список репо, и решающую
матрицу decide() — флаг off → прежний путь; промежуточный шаг/одиночка → селектив; финал → полный;
сбой селектора (исключение / пустое сопоставление) → полный (fail-safe)."""

import os
import tempfile
import unittest

import gate_selective as gs


class TestParseStep(unittest.TestCase):
    def test_step_marker(self):
        self.assertEqual(gs.parse_step("[шаг 2/5 родитель 92] правь suggest"), (True, 2, 5))

    def test_final_marker(self):
        self.assertEqual(gs.parse_step("[шаг 5/5 родитель 92] финал"), (True, 5, 5))

    def test_not_a_step(self):
        self.assertEqual(gs.parse_step("тз: одиночная правка"), (False, 0, 0))
        self.assertEqual(gs.parse_step(""), (False, 0, 0))
        self.assertEqual(gs.parse_step(None), (False, 0, 0))

    def test_is_final_step(self):
        self.assertTrue(gs.is_final_step(True, 5, 5))
        self.assertTrue(gs.is_final_step(True, 6, 5))     # i>n (перестраховка) тоже финал
        self.assertFalse(gs.is_final_step(True, 4, 5))    # промежуточный
        self.assertFalse(gs.is_final_step(False, 1, 1))   # одиночка — не финал шага цепи
        self.assertFalse(gs.is_final_step(True, 0, 0))    # без нумерации


class TestModuleSelection(unittest.TestCase):
    def _mk(self, d, *names):
        for n in names:
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                f.write("# stub\n")

    def test_affected_maps_source_to_test(self):
        with tempfile.TemporaryDirectory() as d:
            self._mk(d, "test_suggest.py", "test_pricing.py")
            mods = gs.affected_test_modules(["suggest.py", "pricing.py"], d)
            self.assertEqual(mods, ["test_pricing", "test_suggest"])

    def test_affected_includes_test_file_itself(self):
        with tempfile.TemporaryDirectory() as d:
            self._mk(d, "test_moderation.py")
            self.assertEqual(gs.affected_test_modules(["test_moderation.py"], d), ["test_moderation"])

    def test_affected_skips_source_without_test(self):
        with tempfile.TemporaryDirectory() as d:
            # нет test_userbot_listen.py на диске → не включаем
            self.assertEqual(gs.affected_test_modules(["userbot_listen.py"], d), [])

    def test_affected_ignores_non_py(self):
        with tempfile.TemporaryDirectory() as d:
            self._mk(d, "test_suggest.py")
            self.assertEqual(gs.affected_test_modules(["README.md", "data.json"], d), [])

    def test_all_test_modules_globs_repo(self):
        with tempfile.TemporaryDirectory() as d:
            self._mk(d, "test_a.py", "test_b.py", "suggest.py", "notes.txt")
            self.assertEqual(gs.all_test_modules(d), ["test_a", "test_b"])


class TestEnvOn(unittest.TestCase):
    def test_on_off(self):
        self.assertTrue(gs.env_on("F", {"F": "1"}))
        self.assertTrue(gs.env_on("F", {"F": " 1 "}))
        self.assertFalse(gs.env_on("F", {"F": "0"}))
        self.assertFalse(gs.env_on("F", {}))
        self.assertFalse(gs.env_on("F", {"F": "true"}))


class TestDecide(unittest.TestCase):
    """Инъектируем affected_fn/all_fn — файловую систему не трогаем, проверяем чистую логику."""

    AFF = staticmethod(lambda changed, repo: ["test_suggest"])
    ALL = staticmethod(lambda repo: ["test_a", "test_b", "test_c"])

    def _decide(self, **kw):
        base = dict(is_step=False, step_i=0, step_n=0, step_selective=False,
                    single_selective=False, affected_fn=self.AFF, all_fn=self.ALL)
        base.update(kw)
        return gs.decide(["suggest.py"], "/repo", **base)

    def test_flag_off_single(self):
        mods, mode = self._decide(is_step=False, single_selective=False)
        self.assertEqual(mode, gs.MODE_OFF)
        self.assertIsNone(mods)                       # вызывающий гонит прежний гейт

    def test_flag_off_step(self):
        mods, mode = self._decide(is_step=True, step_i=2, step_n=5, step_selective=False)
        self.assertEqual(mode, gs.MODE_OFF)

    def test_single_selective(self):
        mods, mode = self._decide(is_step=False, single_selective=True)
        self.assertEqual(mode, gs.MODE_SELECTIVE)
        self.assertEqual(mods, ["test_suggest"])

    def test_intermediate_step_selective(self):
        mods, mode = self._decide(is_step=True, step_i=2, step_n=5, step_selective=True)
        self.assertEqual(mode, gs.MODE_SELECTIVE)
        self.assertEqual(mods, ["test_suggest"])

    def test_final_step_full(self):
        mods, mode = self._decide(is_step=True, step_i=5, step_n=5, step_selective=True)
        self.assertEqual(mode, gs.MODE_FULL_FINAL)
        self.assertEqual(mods, ["test_a", "test_b", "test_c"])   # полный гейт неубираем

    def test_step_flag_does_not_leak_to_single(self):
        # одиночка при step_selective=1, single_selective=0 → прежний путь (флаги независимы)
        mods, mode = self._decide(is_step=False, step_selective=True, single_selective=False)
        self.assertEqual(mode, gs.MODE_OFF)

    def test_failsafe_on_empty_match(self):
        # селектор не сопоставил ни один тест → полный гейт (не «пропускаем без гейта»)
        mods, mode = self._decide(is_step=False, single_selective=True,
                                  affected_fn=lambda c, r: [])
        self.assertEqual(mode, gs.MODE_FULL_FAILSAFE)
        self.assertEqual(mods, ["test_a", "test_b", "test_c"])

    def test_failsafe_on_selector_exception(self):
        def boom(changed, repo):
            raise RuntimeError("селектор упал")
        mods, mode = self._decide(is_step=True, step_i=2, step_n=5, step_selective=True,
                                  affected_fn=boom)
        self.assertEqual(mode, gs.MODE_FULL_FAILSAFE)
        self.assertEqual(mods, ["test_a", "test_b", "test_c"])

    def test_final_beats_selector_exception(self):
        # финал считается ДО селектора → полный, даже если affected_fn упал бы
        def boom(changed, repo):
            raise RuntimeError("не должен вызваться")
        mods, mode = self._decide(is_step=True, step_i=5, step_n=5, step_selective=True,
                                  affected_fn=boom)
        self.assertEqual(mode, gs.MODE_FULL_FINAL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
