# -*- coding: utf-8 -*-
"""
test_lesson_cycle.py — ГОЛДЕН ПОЛНОГО ЦИКЛА урока (родитель 292, шаг 5/7).

Сшивает шаги 1–4 в ОДИН сквозной прогон на фикстуре fixtures/lesson_cycle_292.json —
не юнит отдельной функции, а весь конвейер РЕАЛЬНЫМИ функциями обоих модулей:

  реплай «правка:/урок:/не так:» на карточку черновика  (moderation_core.submit_lesson)
    → ШТАТНАЯ задача в очереди дирижёра, from=Filipp-pcloc-dec  (build_lesson_task)
    → дирижёр забрал: КЛАССИФИКАЦИЯ + маршрут                   (lesson_router.handle_lesson_task)
    → применённый диф + golden-требование теста                (checklist-append / delegate-note)
    → подтверждение «урок принят…» ТОЛЬКО после коммита         (ack_after_commit → build_lesson_ack)

Плюс гейт триггера/прав/неясного (guards): триггерные префиксы, вежливый отказ чужому,
не-урок проходит мимо, размытое «переделай» → карточка-уточнение владельцу (не угадываем).

БЕЗ Telegram/Bridge/Anthropic и БЕЗ боевых доков/очереди/1160: enqueue/checklist/notify/commit
инъектируются фейками, чек-лист правится во ВРЕМЕННЫЙ файл. Golden — ожидания из фикстуры
(expect): чему обязан быть равен исход цикла на ДОСЛОВНОЙ формулировке из живого окна.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_cycle -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import json
import os
import tempfile
import unittest

import suggest
import moderation_core as mc
import lesson_router as lr

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "lesson_cycle_292.json")


def _load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class TestLessonFullCycle(unittest.TestCase):
    """Каждый сценарий cycles[] — реплай-обучение, проведённый через ВЕСЬ конвейер урока."""

    @classmethod
    def setUpClass(cls):
        cls.fx = _load_fixture()

    def setUp(self):
        # учителя из фикстуры → INTAKE_APPROVERS; approve открыт всем (проверяем, что учить всё равно строже)
        self._save = (suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES)
        suggest.INTAKE_APPROVERS = set(self.fx["teachers"])
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES = self._save

    def _submit(self, sc):
        """Шаг 1–2: реплай учителя → задача в очередь. Фейк-enqueue захватывает (text, frm)."""
        calls = []
        fake = lambda text, frm: (calls.append((text, frm)) or (True, 4242, None))
        dec = mc.submit_lesson(sc["card"], sc["reply_text"], sc["teacher"], enqueue=fake)
        return dec, calls

    def test_all_cycles(self):
        for sc in self.fx["cycles"]:
            with self.subTest(cycle=sc["name"]):
                self._run_cycle(sc)

    def _run_cycle(self, sc):
        exp = sc["expect"]
        # --- Шаг 1–2: реплай → задача в очереди дирижёра (from=Filipp-pcloc-dec) ---
        dec, calls = self._submit(sc)
        self.assertEqual(exp["decision"], dec["decision"])
        self.assertEqual(exp["queued"], dec.get("queued", False))
        self.assertEqual(1, len(calls))                          # ровно один enqueue — прямого исполнения нет
        task_text, frm = calls[0]
        self.assertEqual(mc.LESSON_TASK_FROM, frm)               # штатный родитель локального дирижёра
        for frag in exp.get("task_text_has", []):
            self.assertIn(frag, task_text, frag)

        # --- Шаг 3: дирижёр забрал задачу — распознаёт как урок и классифицирует ---
        self.assertEqual(exp["is_lesson_task"], lr.is_lesson_task(task_text))

        # инъекции побочек: чек-лист → временный файл (реальный append_checklist_class = реальный диф),
        # стиль/владелец — захватывающие фейки; ничего боевого не пишем.
        seen = {}
        chk_path = None
        if "checklist_seed" in sc:
            fd, chk_path = tempfile.mkstemp(suffix=".md", text=True)
            os.close(fd)
            with open(chk_path, "w", encoding="utf-8") as f:
                f.write(sc["checklist_seed"])
            self.addCleanup(lambda p=chk_path: os.path.exists(p) and os.remove(p))
        append_checklist = (lambda r, p=chk_path: lr.append_checklist_class(r, p)) if chk_path else \
            (lambda r: seen.__setitem__("checklist", r) or "added")
        append_style = lambda r: seen.__setitem__("style", r) or "added"
        notify_owner = lambda c: seen.__setitem__("owner", c) or True

        dec2 = lr.handle_lesson_task(task_text, append_style=append_style,
                                     append_checklist=append_checklist, notify_owner=notify_owner)
        self.assertEqual(exp["route"], dec2["route"])
        self.assertEqual(exp["delegate"], dec2.get("delegate", False))
        if "status" in exp:
            self.assertEqual(exp["status"], dec2.get("status"))

        # --- Шаг 3 (продолжение): маршрут-специфика ---
        if exp["route"] == lr.FACT:
            for frag in exp.get("delegate_text_has", []):
                self.assertIn(frag, dec2["delegate_text"], frag)  # требование golden-теста ушло планировщику
        if exp["route"] == lr.SUPERVISION and chk_path:
            with open(chk_path, encoding="utf-8") as f:
                diff = f.read()
            for frag in exp.get("checklist_diff_has", []):
                self.assertIn(frag, diff, frag)                   # реальная строка-класс дописана (диф)
            self.assertIn(sc["checklist_seed"].split("\n")[1], diff)  # старое не затёрто
        if exp["route"] == lr.STYLE:
            self.assertIn(exp["style_rule_has"], seen.get("style", ""))
        if exp["route"] == lr.UNCLEAR:
            for frag in exp.get("owner_card_has", []):
                self.assertIn(frag, seen.get("owner", ""), frag)  # карточка-уточнение владельцу в 1160
            self.assertEqual(exp["owner_delivered"], dec2.get("delivered"))

        # --- Шаг 4: подтверждение «урок принят…» ТОЛЬКО после реального коммита ---
        self.assertEqual(exp["card_msg_id"] if "card_msg_id" in exp else "",
                         dec2.get("card_msg_id", ""))
        # ветка с ожидаемым коммитом (НАДЗОР tracked-путь / ФАКТ после планировщика) → ack формируется
        if exp.get("ack_after_commit_has"):
            ack = lr.ack_after_commit("abc1234", dec2.get("ack_subject"), dec2.get("ack_where"),
                                      verify=lambda ref: True)   # симулируем ДОКАЗАННЫЙ реальный коммит
            self.assertIsNotNone(ack)
            for frag in exp["ack_after_commit_has"]:
                self.assertIn(frag, ack, frag)
            # инвариант: до коммита (ref пуст / verify=False) подтверждения НЕТ
            self.assertIsNone(lr.ack_after_commit("", dec2.get("ack_subject"), dec2.get("ack_where")))
            self.assertIsNone(lr.ack_after_commit("abc1234", dec2.get("ack_subject"),
                                                  dec2.get("ack_where"), verify=lambda ref: False))
        # ветка без репо-коммита (СТИЛЬ playbook gitignored / НЕЯСНОЕ) → в модер-группу дирижёр молчит
        if exp.get("ack_withheld"):
            self.assertEqual([], dec2.get("commit_paths", []))
            self.assertIsNone(lr.ack_after_commit(None, dec2.get("ack_subject"), dec2.get("ack_where")))

        # tracked-путь чек-листа к коммиту (НАДЗОР) — именно он попадёт в _commit_lesson
        if "commit_paths_rel" in exp:
            self.assertIn(exp["commit_paths_rel"].replace("/", os.sep), dec2.get("commit_paths", []))
        if "commit_paths" in exp:
            self.assertEqual(exp["commit_paths"], dec2.get("commit_paths"))


class TestLessonCycleGuards(unittest.TestCase):
    """Гейт входа в цикл (guards[]): триггер обучения, права INTAKE_APPROVERS, не-урок мимо."""

    @classmethod
    def setUpClass(cls):
        cls.fx = _load_fixture()

    def setUp(self):
        self._save = (suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES)
        suggest.INTAKE_APPROVERS = set(self.fx["teachers"])
        suggest.APPROVER_USERNAMES = set()          # approve открыт всем → учить всё равно только учителям

    def tearDown(self):
        suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES = self._save

    CARD = {"id": 7, "client_id": 555, "client_ref": "@petya",
            "draft": "Аренда от 1200฿/сутки, беру?", "card_msg_id": 90210}

    def test_all_guards(self):
        for g in self.fx["guards"]:
            with self.subTest(guard=g["name"]):
                exp = g["expect"]
                calls = []
                fake = lambda text, frm: (calls.append((text, frm)) or (True, 4242, None))
                dec = mc.submit_lesson(self.CARD, g["reply_text"], g["teacher"], enqueue=fake)
                self.assertEqual(exp["decision"], dec["decision"])
                self.assertEqual(exp["queued"], dec.get("queued", False))
                self.assertEqual(1 if exp["queued"] else 0, len(calls))  # в очередь идёт ТОЛЬКО принятый урок
                if "kind" in exp:
                    self.assertEqual(exp["kind"], dec["kind"])
                    self.assertEqual(exp["remark"], dec["remark"])
                if "card_has" in exp:
                    self.assertIn(exp["card_has"], dec["card"])         # вежливый отказ чужому

    def test_stranger_denied_even_when_approve_open(self):
        # approve открыт всем (APPROVER пуст), но учить вне INTAKE_APPROVERS нельзя — цикл не стартует
        self.assertTrue(suggest.is_approver("stranger"))
        self.assertFalse(suggest.is_intake_approver("stranger"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
