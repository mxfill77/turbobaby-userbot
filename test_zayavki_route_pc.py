# -*- coding: utf-8 -*-
"""Тесты маршрута информационных заявок (`zayavki_route_pc` + горлышко демона).

КАРТА КЛАССОВ:

  TestPurity            чистота модуля обходом AST: ни диска, ни сети, ни часов, ни env,
                        и НИ ОДНОГО поиска слов по телу заявки (текст — нарратив)
  TestMarkersMatch      литералы маркеров и `from` СВЕРЕНЫ с оригиналами полосы
  TestKinds             род и оба признака; третий исход, когда признак не спрошен
  TestNegative          ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания, три случая, все на ТЕСТОВОЙ сущности
                        и БЕЗ единого сообщения владельцу
  TestVisible           снятое видно в двух местах: списком и числом витрины
  TestOff               рубильник возвращает прежний маршрут байт-в-байт
"""
from __future__ import annotations

import ast
import io
import json
import os
import tempfile
import unittest

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")   # боевой лог демона тестом не трогаем

import shtab_box_signals as sbs
import vitrina_pc as vp
import zayavki_route_pc as zr

HERE = os.path.dirname(os.path.abspath(__file__))

# Тела ТЕСТОВЫХ рядов. Живой формат маркера (дата+ключ) — дословно тот, что пишут
# ступени B и E; тело ниже маркера намеренно короткое: маршрут его не читает.
ASK_B = "[заявка-ревью дата=2026-09-09 ключ=aabbccdd1122]\nВИД НАХОДКИ: УПРОЩАЕМО"
ASK_E = "[разведка-заявка дата=2026-09-09 ключ=ffeeddcc3344]\nПОВОД: ожидание без разбора"
REVIZOR = "[ревизор-находки] сводная карточка находок ревизора"
GUARD = ("🔴 Хочу удалить файл — разрешить?\nОбъект: docs/tmp/x.md\n"
         "Если «да»: файл будет удалён безвозвратно")


class TestPurity(unittest.TestCase):
    """ZAYAVKI_ROUTE_PC_PURE + ROUTE_PC_NO_PROSE — обходом AST, а не обещанием."""

    def setUp(self):
        with io.open(os.path.join(HERE, "zayavki_route_pc.py"), encoding="utf-8") as fh:
            self.tree = ast.parse(fh.read())

    def test_no_io_no_clock_no_env(self):
        banned = {"open", "input", "print", "exec", "eval"}
        attrs = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    self.assertNotIn(f.id, banned, "чистый слой зовёт %s" % f.id)
                if isinstance(f, ast.Attribute):
                    attrs.add(f.attr)
        for bad in ("getenv", "now", "time", "run", "Popen", "urlopen", "request"):
            self.assertNotIn(bad, attrs, "чистый слой зовёт .%s" % bad)

    def test_module_imports_only_the_shared_differentiator(self):
        """Второго экземпляра признаков рода модуль не заводит — берёт чужой."""
        names = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            if isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        self.assertEqual(names - {"__future__"}, {"shtab_box_signals"})

    def test_no_prose_search_over_the_claim_body(self):
        """Текст заявки маршрута не решает: `in`-поиска слов по телу здесь нет.

        Прямой запрет задания. Единственное чтение тела — `startswith(МАРКЕР)`,
        то есть литерал, который наш же код туда и поставил."""
        with io.open(os.path.join(HERE, "zayavki_route_pc.py"), encoding="utf-8") as fh:
            src = fh.read()
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        code = code.split('"""', 2)[-1]                      # шапка модуля — проза, не код
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Compare):
                for op in node.ops:
                    self.assertNotIsInstance(op, ast.In, "маршрут ищет слова в теле заявки")


class TestMarkersMatch(unittest.TestCase):
    """Литералы сверены с оригиналами: разойдись они молча — маршрут ослеп бы."""

    def test_marks_equal_stage_b_and_e(self):
        import recon_auto
        import review_intake
        marks = [m for m, _ in zr.FROM_BY_MARK]
        self.assertIn(review_intake.CLAIM_MARK, marks)
        self.assertIn(recon_auto.ASK_MARK, marks)
        self.assertEqual(set(zr.SELF_DECLARED),
                         {review_intake.CLAIM_MARK, recon_auto.ASK_MARK})

    def test_from_values_equal_the_daemon(self):
        import pc_orchestrator as o
        by_mark = dict(zr.FROM_BY_MARK)
        self.assertEqual(by_mark["[заявка-ревью"], o.REVIEW_CLAIM_FROM)
        self.assertEqual(by_mark["[разведка-заявка"], o.RECON_ASK_FROM)
        self.assertEqual(by_mark["[ревизор-находки]"], o.REVIZOR_OWNER_FROM)

    def test_registry_name_equals_the_daemon_and_the_showcase(self):
        import pc_orchestrator as o
        import vitrina_pc_run as vr
        self.assertEqual(zr.REGISTRY, o._ROUTE_REGISTRY)
        self.assertEqual(zr.REGISTRY, vr.ROUTED_FILE)


class TestKinds(unittest.TestCase):

    def test_kind_comes_from_the_shared_differentiator(self):
        self.assertEqual(sbs.awaiting_kind({"task_text": ASK_E})[0], sbs.KIND_ASK)
        self.assertEqual(sbs.awaiting_kind({"task_text": GUARD})[0], sbs.KIND_BLOCK)
        self.assertEqual(zr.decide({"task_text": ASK_E}, owner_work=False)["kind"], sbs.KIND_ASK)

    def test_second_sign_not_asked_means_card(self):
        """ТРЕТИЙ ИСХОД: одного признака мало, и молчание второго — не «снимай»."""
        v = zr.decide({"id": 1, "task_text": ASK_E})           # owner_work не назван
        self.assertFalse(v["informational"])
        self.assertEqual(v["route"], zr.ROUTE_CARD)
        self.assertIn("НЕ СПРОШЕН", v["why"])

    def test_revizor_card_is_not_self_declared_and_keeps_its_card(self):
        """Род тот же (KIND_ASK), а маршрут прежний: не-задачей себя не объявлял."""
        self.assertEqual(sbs.awaiting_kind({"task_text": REVIZOR})[0], sbs.KIND_ASK)
        v = zr.decide({"id": 2, "task_text": REVIZOR, "from": "Filipp-revizor"},
                      owner_work=False)
        self.assertFalse(v["informational"])
        self.assertIn("не-задачей себя ряд НЕ объявляет", v["why"])

    def test_real_from_beats_the_derived_one(self):
        v = zr.decide({"id": 3, "task_text": ASK_B, "from": "Filipp-shtab"}, owner_work=True)
        self.assertEqual(v["from"], "Filipp-shtab")
        self.assertEqual(v["from_how"], "поле ряда")


class _Post:
    """Двойник Моста: запоминает поля POST и НЕ ходит в сеть НИ ОДНОЙ веткой."""

    def __init__(self):
        self.calls = []

    def __call__(self, action, **fields):
        self.calls.append((action, fields))
        return {"ok": True}


class TestNegative(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗАДАНИЯ. Три случая, ТЕСТОВЫЕ сущности, владельцу — НИЧЕГО.

    Меряем ровно то поле, которым карточка и рождается: `topic` в POST
    `set_needs_approval`. Названа тема — карточка будет; не названа — не будет.
    Ни одной отправки в Telegram здесь не происходит: двойник `_Post` перехватывает
    вызов ДО сети, а `dispatch_notify` в этой дороге не участвует вовсе.
    """

    def setUp(self):
        import pc_orchestrator as o
        self.o = o
        self.post = _Post()
        self._orig = o.bc._post
        o.bc._post = self.post
        self.reg = o._route_registry_path()
        self._had = os.path.exists(self.reg)

    def tearDown(self):
        self.o.bc._post = self._orig

    def _topic_of(self):
        self.assertEqual(len(self.post.calls), 1)
        action, fields = self.post.calls[0]
        self.assertEqual(action, "set_needs_approval")
        return fields.get("topic")

    def test_1_operational_guard_card_still_reaches_the_owner(self):
        """КОНТРФАКТ: операционная карточка гарда доходит — тема названа, как вчера."""
        self.o.bc.set_needs_approval(9001, GUARD, topic=self.o.NEEDS_APPROVAL_TOPIC)
        self.assertEqual(self._topic_of(), self.o.NEEDS_APPROVAL_TOPIC)

    def test_2_informational_claim_does_not_reach_the_owner(self):
        """Информационная заявка карточки НЕ создаёт: темы в POST нет."""
        self.o.bc.set_needs_approval(9002, ASK_E, topic=self.o.NEEDS_APPROVAL_TOPIC,
                                     frm=self.o.RECON_ASK_FROM)
        self.assertIsNone(self._topic_of())

    def test_2b_but_it_is_visible_in_the_list(self):
        """…и при этом ВИДНА: реестр маршрута несёт её строкой с номером и причиной."""
        self.o.bc.set_needs_approval(9003, ASK_B, topic=self.o.NEEDS_APPROVAL_TOPIC,
                                     frm=self.o.REVIEW_CLAIM_FROM)
        with io.open(self.reg, encoding="utf-8") as fh:
            state = json.load(fh)
        rec = (state.get("routed") or {}).get("9003")
        self.assertIsNotNone(rec, "снятая заявка исчезла без следа — это запрещено")
        self.assertIn("#9003", rec["line"])
        self.assertIn("карточки владельцу НЕ БЫЛО", rec["line"])

    def test_3_claim_that_holds_work_stays_a_card(self):
        """ТРЕТИЙ СЛУЧАЙ: род тот же, но ряд ДЕРЖИТ работу → карточка остаётся.

        Признак взят ИЗ КОДА (`_is_owner_work` по полю `from`), а не из текста:
        тело ряда здесь дословно то же, что во втором случае."""
        self.assertTrue(self.o._is_owner_work({"from": "Filipp-shtab", "task_text": ASK_E}, ()))
        self.o.bc.set_needs_approval(9004, ASK_E, topic=self.o.NEEDS_APPROVAL_TOPIC,
                                     frm="Filipp-shtab")
        self.assertEqual(self._topic_of(), self.o.NEEDS_APPROVAL_TOPIC)

    def test_registry_failure_keeps_the_card(self):
        """СЦЕПКА: реестр не лёг → тема остаётся. Молчаливого снятия не бывает."""
        orig = self.o._route_registry_write
        self.o._route_registry_write = lambda row, verdict: False
        try:
            self.o.bc.set_needs_approval(9005, ASK_E, topic=self.o.NEEDS_APPROVAL_TOPIC,
                                         frm=self.o.RECON_ASK_FROM)
            self.assertEqual(self._topic_of(), self.o.NEEDS_APPROVAL_TOPIC)
        finally:
            self.o._route_registry_write = orig


class TestVisible(unittest.TestCase):
    """Второе место видимости — ЧИСЛО ВИТРИНЫ отдельной строкой."""

    def test_showcase_has_its_own_line_with_the_number(self):
        rows = [{"at": 1.0, "id": 7, "line": "#7 заявка — карточки владельцу НЕ БЫЛО: …"}]
        lines = vp.part_nums(None, None, "2026-09-09", None, None,
                             routed={"ok": True, "rows": rows, "why": ""})
        head = [l for l in lines if l.startswith("ЗАЯВКИ МИМО ИНБОКСА")]
        self.assertEqual(len(head), 1, "число обязано стоять ОТДЕЛЬНОЙ строкой")
        self.assertIn("за сутки: 1", head[0])
        self.assertTrue(any("#7" in l for l in lines), "списка в витрине нет")

    def test_no_registry_means_unknown_and_never_zero(self):
        lines = vp.part_nums(None, None, "2026-09-09", None, None, routed=None)
        head = [l for l in lines if l.startswith("ЗАЯВКИ МИМО ИНБОКСА")][0]
        self.assertIn(vp.UNKNOWN, head)
        self.assertNotIn("за сутки: 0", head)

    def test_showcase_did_not_lose_the_two_old_numbers(self):
        """Операционные числа витрины заходом не тронуты ни одной строкой."""
        lines = vp.part_nums(None, None, "2026-09-09", None, {"holding": 2, "asking": 5},
                             routed={"ok": True, "rows": [], "why": ""})
        self.assertTrue(any(l.startswith("ДЕРЖАТ операцию (карточки гарда): 2") for l in lines))
        self.assertTrue(any(l.startswith("ЖДУТ МНЕНИЯ (заявки, ящик не держат): 5") for l in lines))

    def test_day_window_is_sliding_and_drops_the_old(self):
        state = {"routed": {"1": {"at": 100.0}, "2": {"at": 100.0 - zr.DAY_SEC - 1}}}
        self.assertEqual([r["at"] for r in zr.day_rows(state, 100.0)], [100.0])


class TestOff(unittest.TestCase):

    def test_switch_off_restores_the_previous_route(self):
        v = zr.decide({"id": 5, "task_text": ASK_E}, owner_work=False, enabled=False)
        self.assertFalse(v["informational"])
        self.assertEqual(v["route"], zr.ROUTE_CARD)
        self.assertIn(zr.OFF_FLAG, v["why"])

    def test_daemon_switch_reads_the_same_name(self):
        import pc_orchestrator as o
        os.environ[zr.OFF_FLAG] = "1"
        try:
            self.assertFalse(o._route_enabled())
            self.assertEqual(o._route_needs_approval(9006, ASK_E, 829,
                                                     frm=o.RECON_ASK_FROM), 829)
        finally:
            os.environ.pop(zr.OFF_FLAG, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
