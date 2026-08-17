# -*- coding: utf-8 -*-
"""
test_brain_probe_pc.py — регресс ЖИВЫХ РУК ЧТЕНИЯ УЗЛА полосы ПК.

Порядок классов — порядок замков:

  A. `TestBlameIsNamedByFields` — ЧЕТЫРЕ ВИНЫ РАЗЛИЧАЮТСЯ, и различаются они ПОЛЯМИ отказа
     (`verdict`, `code`), а не подстрокой сообщения. «Поломка рук» и «недоступный источник» —
     разные новости; слепить их значит чинить мост, когда сломан свой модуль.
  B. `TestLiveReadIsClosedUnderTest` — под тест-прогоном наружу НЕ ХОДИМ, и признак снимается
     FAIL-CLOSED: не снялся — тоже отказ. Мина названа заранее: флаг `TURBOBABY_TEST_LOGS=1`
     держит `brain_writer` от живой ЗАПИСИ, а чтения не касается вовсе.
  C. `TestHandsOnlyRead` — руки только читают: ни одного пишущего имени моста в модуле.

СЕТИ ЗДЕСЬ НЕТ НИ В ОДНОМ ТЕСТЕ: живой читатель подменяется на уровне `brain_writer.read_text`
(руки берут его `getattr`-ом в момент вызова), а отказы поднимаются НАСТОЯЩИМ классом
`BrainWriterError` с настоящими полями — мок повторяет живой формат, а не удобную схему.
Живые пробы рук лежат в наборе судьи (`test_result_judge_pc.TestBrainLiveHands`): там они на
месте, потому что проверяют прибор целиком.

Запуск: venv\\Scripts\\python.exe -m unittest test_brain_probe_pc
"""
import ast
import os
import unittest

# ДО первого чтения признака: набор обязан оставаться оффлайновым при любом способе запуска.
os.environ["TURBOBABY_TEST_LOGS"] = "1"

import brain_probe_pc as bp     # noqa: E402
import brain_writer as bw       # noqa: E402

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "brain_probe_pc.py")


class _Hands(unittest.TestCase):
    """Утварь: подмена живого читателя НА УРОВНЕ ЖИВОГО МОДУЛЯ и её честный возврат."""

    def _reader(self, fn):
        was = bw.read_text
        bw.read_text = fn
        self.addCleanup(setattr, bw, "read_text", was)

    def _raises(self, exc):
        def fn(**_kw):
            raise exc
        self._reader(fn)

    def _returns(self, value):
        seen = []

        def fn(**kw):
            seen.append(kw)
            return value
        self._reader(fn)
        return seen


class TestBlameIsNamedByFields(_Hands):

    def test_the_codes_are_the_live_ones_not_invented(self):
        """Литералы кодов живут в двух модулях — сверяем с живым отказом, а не с памятью."""
        self.assertEqual(bw.BrainWriterError("x", 1).code, bp.CODE_CONFIG)
        self.assertEqual(bw.BrainWriterError("x", 2).code, bp.CODE_READ)

    def test_missing_config_is_broken_hands_not_a_dead_source(self):
        """ЖИВАЯ ветка читателя: без адреса/токена он падает кодом 1 — это НАША поломка."""
        with self.assertRaises(bw.BrainWriterError) as got:
            bw.read_text(name="index", env={"BRIDGE_URL": "", "BRIDGE_TOKEN": ""})
        self.assertEqual(got.exception.code, bp.CODE_CONFIG)
        self._raises(bw.BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN", 1))
        with self.assertRaises(bp.HandsBroken) as hands:
            bp.read_doc("index", allow_test_context=True)
        self.assertEqual(hands.exception.blame, bp.BLAME_HANDS)

    def test_a_failed_read_is_the_source(self):
        self._raises(bw.BrainWriterError("read_doc(name:index) упал: TimeoutError", 2))
        with self.assertRaises(bp.SourceDown) as got:
            bp.read_doc("index", allow_test_context=True)
        self.assertEqual(got.exception.blame, bp.BLAME_SOURCE)

    def test_a_verdict_about_the_name_is_the_node_even_though_its_code_is_one(self):
        """ЛОВУШКА ПОРЯДКА: вердикт ПРО ИМЯ несёт код 1, как и отказ конфига. Спрашиваем
        `verdict` ПЕРВЫМ — иначе неизвестный ключ узла выглядел бы поломкой рук."""
        exc = bw.BrainWriterError("ИМЯ НЕ РАЗРЕШЕНО: 'нет-такого'", 1)
        exc.verdict = True
        self._raises(exc)
        with self.assertRaises(bp.UnknownNode) as got:
            bp.read_doc("нет-такого", allow_test_context=True)
        self.assertEqual(got.exception.blame, bp.BLAME_NODE)

    def test_an_unclassifiable_failure_is_not_pinned_on_the_source(self):
        self._raises(RuntimeError("что-то пошло не так"))
        with self.assertRaises(bp.ProbeError) as got:
            bp.read_doc("index", allow_test_context=True)
        self.assertEqual(got.exception.blame, "", "вина выдумана: %s" % got.exception.blame)
        self.assertNotIsInstance(got.exception, bp.SourceDown)

    def test_a_non_text_answer_is_broken_hands(self):
        self._returns(17)
        with self.assertRaises(bp.HandsBroken):
            bp.read_doc("index", allow_test_context=True)

    def test_an_empty_key_never_reaches_the_bridge(self):
        seen = self._returns("тело")
        for empty in ("", "   "):
            with self.assertRaises(bp.HandsBroken):
                bp.read_doc(empty, allow_test_context=True)
        self.assertEqual(seen, [], "руки пошли на мост с пустым ключом")

    def test_every_blame_word_belongs_to_the_agreed_set(self):
        for cls in (bp.HandsBroken, bp.SourceDown, bp.UnknownNode, bp.LiveForbidden):
            self.assertIn(cls.blame, bp.BLAMES)
        self.assertEqual(len(set(bp.BLAMES)), 4, "вины слиплись: %s" % (bp.BLAMES,))
        self.assertEqual(bp.ProbeError.blame, "", "у безымянной вины появилось имя")

    def test_a_long_message_of_a_stranger_is_cut(self):
        self._raises(bw.BrainWriterError("ы" * 5000, 2))
        with self.assertRaises(bp.SourceDown) as got:
            bp.read_doc("index", allow_test_context=True)
        self.assertLess(len(str(got.exception)), bp.MSG_MAX + 200)


class TestLiveReadIsClosedUnderTest(_Hands):

    def test_a_test_run_is_refused_and_the_bridge_is_not_touched(self):
        seen = self._returns("тело")
        with self.assertRaises(bp.LiveForbidden) as got:
            bp.read_doc("index")
        self.assertEqual(got.exception.blame, bp.BLAME_TESTRUN)
        self.assertEqual(seen, [], "гейт ушёл на живой мост — ровно та мина, что названа в шапке")

    def test_the_waiver_is_the_only_way_out_and_it_is_named(self):
        seen = self._returns("тело")
        self.assertEqual(bp.read_doc("index", allow_test_context=True), "тело")
        self.assertEqual(seen, [{"name": "index"}], "ключ уехал не именем: %s" % seen)

    def test_an_unreadable_test_flag_closes_the_door_rather_than_opens_it(self):
        """FAIL-CLOSED: «не знаю, тест ли это» дверь НЕ открывает. Сосед (`brain_writer`) на том
        же сбое отвечает `False` — ему это ничем не грозит, а здесь стоило бы живого выхода."""
        was = bp._test_context
        bp._test_context = lambda: None
        self.addCleanup(setattr, bp, "_test_context", was)
        seen = self._returns("тело")
        with self.assertRaises(bp.LiveForbidden):
            bp.read_doc("index")
        self.assertEqual(seen, [])

    def test_the_flag_itself_is_read_from_the_repo_point_of_truth(self):
        self.assertTrue(bp._test_context(), "признак тест-прогона не снялся под тест-прогоном")

    def test_the_factory_carries_the_waiver_and_takes_one_argument(self):
        seen = self._returns("тело")
        self.assertEqual(bp.reader(allow_test_context=True)("index"), "тело")
        self.assertEqual(len(seen), 1)
        with self.assertRaises(bp.LiveForbidden):
            bp.reader()("index")


class TestHandsOnlyRead(unittest.TestCase):

    def _tree(self):
        with open(SRC, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_no_writing_action_of_the_bridge_is_named_in_the_module(self):
        names = [n.attr for n in ast.walk(self._tree()) if isinstance(n, ast.Attribute)]
        for one in ("write_doc", "append", "apply", "create_plain", "register", "post", "_post"):
            self.assertNotIn(one, names, "руки умеют менять мозг: %s" % one)

    def test_the_module_reads_no_secrets_of_its_own(self):
        """Запрет класса 328: секреты берёт САМ процесс писателя, здесь их не видно ни строкой."""
        with open(SRC, encoding="utf-8") as handle:
            src = handle.read()
        for one in ("BRIDGE_TOKEN", "BRIDGE_URL", "load_env", "_env_file", ".env"):
            self.assertNotIn(one, src, "руки читают конфиг сами: %s" % one)

    def test_the_only_reader_it_borrows_is_the_trusted_one(self):
        borrowed = [n.value for n in ast.walk(self._tree())
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and n.value in ("read_text", "write_doc")]
        self.assertEqual(borrowed, ["read_text"], "руки берут у писателя не то: %s" % borrowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
