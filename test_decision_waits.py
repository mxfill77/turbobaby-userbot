# -*- coding: utf-8 -*-
"""
test_decision_waits.py — ГОЛДЕНЫ ОТКРЫТЫХ РЕШЕНИЙ ВЛАДЕЛЬЦА (06.09.2026).
БЕЗ сети, git, очереди и боевых файлов состояния: хранилище — временный файл на каждый тест.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_decision_waits -v

Четыре ОТРИЦАТЕЛЬНЫХ теста задания стоят первыми и названы в именах классов — они и есть
предмет: 1) молчание владельца не применяет ничего; 2) ответ на повод с изменившимся составом не
применяет старое решение, а спрашивает заново; 3) постоянное решение не заводится без явного
слова; 4) нажатие на протухший повод даёт человеческое объяснение, а не «не ок».
"""

import os
import shutil
import tempfile
import unittest

import decision_waits as dw            # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dwtest_")
        self.p = os.path.join(self.dir, "waits.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def park(self, key="c0ffee", shape="userbot|a.py", cls="gate", t=1000.0, title="повод"):
        return dw.park(cls, key, shape, title, path=self.p, now=t)


# ═══ ОТРИЦАТЕЛЬНЫЙ 1: МОЛЧАНИЕ НЕ ПРИМЕНЯЕТ НИЧЕГО ══════════════════════════════════════════
class TestSilenceAppliesNothing(Base):
    """Fail-closed не тронут: сколько бы поводов ни накопилось и сколько бы ни прошло времени,
    без ОТВЕТА не применяется ничего. Накопление — это память, а не разрешение."""

    def test_ten_waits_and_a_week_of_silence_apply_nothing(self):
        for i in range(10):
            self.park(key="c%02d" % i, t=1000.0 + i)
        # неделя молчания
        self.assertEqual(len(dw.open_waits("gate", self.p)), 10, "поводы обязаны остаться открытыми")
        # ни один не закрыт и ни один не помечен разрешённым — закрыть их может только answer_class
        for r in dw.open_waits("gate", self.p):
            self.assertNotIn("closed_at", r, "молчание закрыло повод — это применение по времени")

    def test_module_has_no_apply_branch_at_all(self):
        """ИНВАРИАНТ DW_NO_APPLY. Предсмертный взгляд задания — «поводы начнут применяться скопом».
        Пока в модуле физически нет ни рестарта, ни записи основания, ни очереди, применять
        скопом нечему. Тест смотрит ИСХОДНИК: комментарий обещает — код обязан подтверждать."""
        with open(os.path.join(os.path.dirname(os.path.abspath(dw.__file__)),
                               "decision_waits.py"), encoding="utf-8") as f:
            src = f.read()
        for forbidden in ("subprocess", "restart", "approve(", "enqueue", "requests", "urllib"):
            self.assertNotIn(forbidden, src,
                             "модуль обзавёлся применением (%r) — замок против «скопом» снят"
                             % forbidden)

    def test_digest_is_not_an_answer(self):
        """Сводка ушла — поводы остались открытыми. Показать не значит решить."""
        self.park(key="a"); self.park(key="b")
        due, why = dw.digest_due("gate", self.p, now=2000.0)
        self.assertTrue(due, why)
        dw.digest_mark("gate", self.p, now=2000.0)
        self.assertEqual(len(dw.open_waits("gate", self.p)), 2)


# ═══ ОТРИЦАТЕЛЬНЫЙ 2: ИЗМЕНИВШИЙСЯ СОСТАВ СПРАШИВАЕТСЯ ЗАНОВО ═══════════════════════════════
class TestChangedShapeAsksAgain(Base):
    """Одобрение не воскрешается вслепую: «да» относится к тому составу, который владелец ВИДЕЛ."""

    def test_yes_does_not_apply_to_changed_composition(self):
        self.park(key="c1", shape="userbot|a.py")
        res = dw.answer_class("gate", "yes", {"c1": "userbot|a.py,b.py"}, self.p, now=2000.0)
        self.assertEqual(res["applied"], [], "старое «да» применилось к новому составу")
        self.assertEqual(res["changed"], ["c1"])
        self.assertIn("спрошу ЗАНОВО", res["text"])
        self.assertEqual(len(dw.open_waits("gate", self.p)), 1,
                         "повод с уехавшим составом обязан остаться ОТКРЫТЫМ (=спросить заново)")

    def test_no_is_symmetric_and_also_asks_again(self):
        """«Нет» — тоже решение о конкретном составе. Закрыть им повод, которого владелец не
        видел, значит потерять вопрос молча."""
        self.park(key="c1", shape="userbot|a.py")
        res = dw.answer_class("gate", "no", {"c1": "moderbot|a.py"}, self.p, now=2000.0)
        self.assertEqual(res["applied"], [])
        self.assertEqual(res["changed"], ["c1"])

    def test_matching_composition_is_allowed_and_closed(self):
        """Половина, которую легко убить фиксом: совпал состав — ответ засчитан и повод закрыт."""
        self.park(key="c1", shape="userbot|a.py")
        res = dw.answer_class("gate", "yes", {"c1": "userbot|a.py"}, self.p, now=2000.0)
        self.assertEqual(res["applied"], ["c1"])
        self.assertEqual(dw.open_waits("gate", self.p), [])
        self.assertIn("РАЗРЕШЕНИЕ, а не выкатка", res["text"])

    def test_batch_answer_splits_by_composition(self):
        """Ответ ОДНИМ движением на класс из трёх: применимо только совпавшее."""
        self.park(key="c1", shape="s1"); self.park(key="c2", shape="s2"); self.park(key="c3", shape="s3")
        res = dw.answer_class("gate", "yes", {"c1": "s1", "c2": "УЕХАЛ"}, self.p, now=2000.0)
        self.assertEqual(res["applied"], ["c1"])
        self.assertEqual(res["changed"], ["c2"])
        self.assertEqual(res["gone"], ["c3"])

    def test_reshaped_park_resets_the_occasion(self):
        """Тот же ключ с другим составом — повод НОВЫЙ, счёт с нуля."""
        self.park(key="c1", shape="s1", t=1000.0)
        self.park(key="c1", shape="s1", t=1100.0)
        out, rec, err = dw.park("gate", "c1", "s2", "новый состав", path=self.p, now=1200.0)
        self.assertEqual(out, dw.PARK_RESHAPED)
        self.assertEqual(rec["n"], 1, "счёт повторов обязан начаться заново")
        self.assertEqual(rec["was"], "s1")

    def test_same_shape_is_a_repeat_not_a_new_card(self):
        self.park(key="c1", shape="s1", t=1000.0)
        out, rec, err = dw.park("gate", "c1", "s1", "он же", path=self.p, now=1100.0)
        self.assertEqual(out, dw.PARK_AGAIN)
        self.assertEqual(rec["n"], 2)
        self.assertEqual(len(dw.open_waits("gate", self.p)), 1, "повтор размножил поводы")


# ═══ ОТРИЦАТЕЛЬНЫЙ 3: ПРАВИЛО НЕ ЗАВОДИТСЯ БЕЗ ЯВНОГО СЛОВА ═════════════════════════════════
class TestStandingRuleNeedsExplicitWord(Base):

    def test_guessy_phrases_do_not_create_a_rule(self):
        for phrase in ("ну наверное не надо", "пока не спрашивай", "отстань", "не сейчас",
                       "да хватит уже", "", "нет"):
            ok, text = dw.rule_set("gate", phrase, path=self.p, now=1000.0)
            self.assertFalse(ok, "фраза %r завела постоянное решение по догадке" % phrase)
            self.assertFalse(dw.rule_active("gate", self.p)[0], repr(phrase))
            self.assertIn("догадка тут", text)

    def test_explicit_word_creates_rule_and_names_the_cancel(self):
        ok, text = dw.rule_set("gate", dw.RULE_ON_WORDS[0], path=self.p, now=1000.0)
        self.assertTrue(ok, text)
        on, why = dw.rule_active("gate", self.p)
        self.assertTrue(on)
        self.assertIn("Отменить", text)
        self.assertIn(dw.RULE_OFF_WORDS[0], text, "правило без названного способа отмены")

    def test_rule_record_carries_a_cancel_or_it_is_not_a_rule(self):
        """ИНВАРИАНТ DW_RULE_CANCEL: правило без отмены не заводится (задание, п.7)."""
        dw.rule_set("gate", dw.RULE_ON_WORDS[0], path=self.p, now=1000.0)
        rec = dw._load(self.p)["rules"]["gate"]
        self.assertTrue(str(rec.get("cancel") or "").strip(), "запись правила без способа отмены")

    def test_rule_mutes_the_question_and_applies_nothing(self):
        self.park(key="c1"); self.park(key="c2")
        dw.rule_set("gate", dw.RULE_ON_WORDS[0], path=self.p, now=1000.0)
        due, why = dw.digest_due("gate", self.p, now=99999.0)
        self.assertFalse(due, "правило обязано глушить ВОПРОС")
        self.assertIn("постоянное решение", why)
        self.assertEqual(len(dw.open_waits("gate", self.p)), 2,
                         "правило применило/съело поводы — оно глушит вопрос, а не решает его")

    def test_cancel_also_needs_the_explicit_word(self):
        dw.rule_set("gate", dw.RULE_ON_WORDS[0], path=self.p, now=1000.0)
        ok, text = dw.rule_clear("gate", "ладно спрашивай", path=self.p, now=1100.0)
        self.assertFalse(ok)
        self.assertTrue(dw.rule_active("gate", self.p)[0], "правило снято по догадке")
        ok, text = dw.rule_clear("gate", dw.RULE_OFF_WORDS[0], path=self.p, now=1200.0)
        self.assertTrue(ok, text)
        self.assertFalse(dw.rule_active("gate", self.p)[0])


# ═══ ОТРИЦАТЕЛЬНЫЙ 4: ПРОТУХШЕЕ НАЖАТИЕ ОБЪЯСНЯЕТ СЕБЯ ═════════════════════════════════════
class TestStalePressExplainsItself(Base):

    def test_explanation_answers_what_why_and_what_now(self):
        t = dw.stale_explain("карточка ворот", "4528917",
                             "кнопка новее процесса, который её ловит",
                             answer_at="тема «PC-дев», «задача: выкати»", parked=True)
        self.assertIn("4528917", t)
        self.assertIn("кнопка новее процесса", t)
        self.assertIn("Ответить прямо сейчас", t)
        self.assertIn("НЕ потерян", t)
        self.assertNotIn("не ок", t)

    def test_unknown_cause_is_named_unknown_not_invented(self):
        """Не знаем причину — так и говорим. Выдать одну из двух за обе — это тот же дефект,
        что и молчание: 05.09 владельцу сказали «карточка устарела», а она не устаревала."""
        t = dw.stale_explain("карточка", "x1", "", answer_at="тема «PC-дев»")
        self.assertIn("не выдумываю", t)

    def test_no_parked_occasion_says_so_plainly(self):
        t = dw.stale_explain("карточка", "x1", "повод закрыт по сроку", parked=False)
        self.assertIn("не нашёл", t)
        self.assertIn("ничего не применено", t)


# ═══ СВЁРТКА: ЧИСЛА ПОРОГА И ЧАСТОТЫ ═══════════════════════════════════════════════════════
class TestDigestFolding(Base):

    def test_single_wait_is_not_folded(self):
        self.park(key="c1")
        due, why = dw.digest_due("gate", self.p, now=2000.0)
        self.assertFalse(due, "один повод свернули в сводку — владелец потерял кнопку")
        self.assertIn("порог свёртки", why)

    def test_window_holds_the_second_digest(self):
        self.park(key="c1"); self.park(key="c2")
        self.assertTrue(dw.digest_due("gate", self.p, now=2000.0)[0])
        dw.digest_mark("gate", self.p, now=2000.0)
        due, why = dw.digest_due("gate", self.p, now=2000.0 + 3600)
        self.assertFalse(due, "сводка ушла второй раз внутри окна")
        due, why = dw.digest_due("gate", self.p, now=2000.0 + dw.DIGEST_EVERY_SEC + 1)
        self.assertTrue(due, why)

    def test_numbers_are_the_measured_ones(self):
        """Числа названы и обоснованы в артефакте — тест стережёт, что их не сдвинули молча."""
        self.assertEqual(dw.FOLD_MIN, 2)
        self.assertEqual(dw.DIGEST_EVERY_SEC, 6 * 3600)

    def test_digest_says_nothing_was_applied_and_that_yes_rechecks(self):
        """Две фразы, без которых сводка ОПАСНЕЕ десяти карточек."""
        self.park(key="c1", title="коммит aaa"); self.park(key="c2", title="коммит bbb")
        t = dw.digest_text("gate", self.p, now=2000.0, answer_at="тема «PC-дев»")
        self.assertIn("НЕ применилось ничего", t)
        self.assertIn("состав сверяется заново", t)
        self.assertIn("ГДЕ ОТВЕЧАТЬ", t)
        self.assertIn("2", t.splitlines()[0])

    def test_digest_of_empty_class_is_empty(self):
        self.assertEqual(dw.digest_text("gate", self.p, now=2000.0), "")


# ═══ ХРАНИЛИЩЕ: СБОЙ ЧЕСТЕН ════════════════════════════════════════════════════════════════
class TestStoreFailClosed(Base):

    def test_broken_store_reads_as_empty_not_as_crash(self):
        with open(self.p, "w", encoding="utf-8") as f:
            f.write("{битый жсон")
        self.assertEqual(dw.open_waits("gate", self.p), [])
        out, rec, err = dw.park("gate", "c1", "s1", "повод", path=self.p, now=1000.0)
        self.assertEqual(out, dw.PARK_NEW, "битый файл потерял повод вместо того, чтобы его завести")

    def test_unwritable_store_reports_the_reason(self):
        bad = os.path.join(self.dir, "нет-такого-каталога", "waits.json")
        out, rec, err = dw.park("gate", "c1", "s1", "повод", path=bad, now=1000.0)
        self.assertTrue(err, "сбой записи проглочен молча")

    def test_park_without_class_or_key_is_refused(self):
        out, rec, err = dw.park("", "c1", "s1", "повод", path=self.p)
        self.assertIsNone(out)
        self.assertIn("нечем ответить", err)

    def test_closed_waits_leave_a_trace(self):
        self.park(key="c1", shape="s1")
        dw.answer_class("gate", "yes", {"c1": "s1"}, self.p, now=2000.0)
        closed = dw._load(self.p)["closed"]
        self.assertEqual(len(closed), 1)
        self.assertIn("ответ владельца", closed[0]["closed_why"])

    def test_junk_verdict_changes_nothing(self):
        self.park(key="c1", shape="s1")
        res = dw.answer_class("gate", "может быть", {"c1": "s1"}, self.p, now=2000.0)
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(dw.open_waits("gate", self.p)), 1)

    def test_shape_of_is_order_independent(self):
        self.assertEqual(dw.shape_of(["userbot", ["b.py", "a.py"]]),
                         dw.shape_of(["userbot", ["a.py", "b.py"]]))
        self.assertNotEqual(dw.shape_of(["userbot", ["a.py"]]),
                            dw.shape_of(["userbot", ["a.py", "b.py"]]))


# ═══ ЖИВАЯ ПОЛОВИНА: СРОК ЗАКРЫВАЕТ РЯД, А НЕ ВОПРОС ═══════════════════════════════════════
class TestParkOnApprovalTimeout(Base):
    """Чистого модуля мало: пока его никто не зовёт ТАМ, ГДЕ ПОВОД УМИРАЕТ, ничего не изменилось.
    Голден стережёт саму врезку в `process_approval_timeouts` — что она есть и что она не смеет
    отменить закрытие ряда своим падением."""

    def _orch(self):
        os.environ["LESSON_LLM_ROUTE"] = "0"      # боевой рубильник не течёт в тест
        import pc_orchestrator as o
        return o

    def test_expired_card_becomes_an_open_wait(self):
        o = self._orch()
        seen = {}

        def fake_park(cls, key, shape, title, where="", answer_at="", **kw):
            seen.update(cls=cls, key=key, shape=shape, title=title, answer_at=answer_at)
            return dw.PARK_NEW, {}, ""
        ok = o._park_expired_approval(77, {"what": "⛔ красная операция: rm -rf X"}, park_fn=fake_park)
        self.assertTrue(ok)
        self.assertEqual(seen["cls"], o.DECISION_CLS_APPROVAL)
        self.assertEqual(seen["key"], "77")
        self.assertIn("rm -rf X", seen["shape"], "отпечаток снят НЕ с текста карточки")
        self.assertTrue(seen["answer_at"], "повод припаркован без адреса ответа")

    def test_shape_is_the_card_text_not_the_row_id(self):
        """Номер ряда переживает подмену содержимого (класс TASK_START_RECLAIM): один и тот же id
        приходит НОВОЙ задачей через дни. Значит состав обязан сниматься с текста."""
        o = self._orch()
        got = []
        o._park_expired_approval(5, {"what": "карточка A"},
                                 park_fn=lambda *a, **k: (got.append(a[2]), (dw.PARK_NEW, {}, ""))[1])
        o._park_expired_approval(5, {"what": "карточка Б — другое"},
                                 park_fn=lambda *a, **k: (got.append(a[2]), (dw.PARK_NEW, {}, ""))[1])
        self.assertNotEqual(got[0], got[1], "два разных вопроса получили один отпечаток")

    def test_park_failure_never_blocks_closing_the_row(self):
        """Парковка — СТРАХОВКА поверх закрытия ряда. Её падение не смеет оставить ряд открытым:
        иначе новый замок ломает старый, который работал."""
        o = self._orch()

        def boom(*a, **k):
            raise RuntimeError("диск отвалился")
        self.assertFalse(o._park_expired_approval(9, {"what": "x"}, park_fn=boom))

    def test_store_error_is_reported_not_swallowed(self):
        o = self._orch()
        self.assertFalse(o._park_expired_approval(
            9, {"what": "x"}, park_fn=lambda *a, **k: (dw.PARK_NEW, {}, "OSError: нет места")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
