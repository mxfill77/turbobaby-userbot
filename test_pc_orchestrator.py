# -*- coding: utf-8 -*-
"""
test_pc_orchestrator.py — мок-тесты ПК-оркестратора. БЕЗ реального claude/Bridge/сети/schtasks —
всё замокано. Разрушительного ничего не выполняется.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_pc_orchestrator -v
"""

import os
import re
import sys
import json
import types
import tempfile
import datetime
import unittest
from pathlib import Path
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (деплой 334);
                                       # ставим ДО импорта o: load_dotenv(override=False) не перепишет

import pc_orchestrator as o           # noqa: E402
import pretool_guard                  # noqa: E402  (словарь видов/разбор карточки — смычка с гардом)
import selfupdate_gate                # noqa: E402  (голден «гейт не спавнит claude»)


def iso_ago(sec):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=sec)).isoformat()


def iso_dt(sec):
    """То же, но объектом datetime: окно следов работы считается по datetime, а не по строке."""
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=sec)


class FakeBridge:
    def __init__(self):
        self.tasks = {}
        self._id = 0
        self.hb = {}

    def add(self, status="new", lane="pc", task_text="сделай X", updated=None):
        self._id += 1
        self.tasks[self._id] = {"id": self._id, "status": status, "lane": lane,
                                "task_text": task_text, "updated": updated, "result": None}
        return self._id

    def get_pending(self, status, lane="pc"):
        return {"ok": True, "items": [dict(t) for t in self.tasks.values() if t["status"] == status]}

    def claim_task(self, tid):
        t = self.tasks.get(tid)
        if t and t["status"] == "new":
            t["status"] = "in_progress"
            return {"ok": True}
        return {"ok": False, "error": "not_new"}

    def complete_task(self, tid, status, result):
        t = self.tasks.get(tid)
        if t:
            t["status"] = status
            t["result"] = result
        return {"ok": True}

    def set_needs_approval(self, tid, what, topic=None):
        t = self.tasks.get(tid)
        if t:
            t["status"] = "needs_approval"
            t["result"] = what
            t["what"] = what
            t["topic"] = topic
        return {"ok": True}

    def task_heartbeat(self, tid):
        self.hb[tid] = self.hb.get(tid, 0) + 1     # счётчик тиков heartbeat по задаче (для тестов ожидания)
        return {"ok": True}

    def enqueue_task(self, frm, text, lane="pc"):
        self._id += 1
        self.tasks[self._id] = {"id": self._id, "status": "new", "lane": lane,
                                "task_text": text, "updated": None, "result": None, "from": frm}
        return {"ok": True, "id": self._id}


class Base(unittest.TestCase):
    def setUp(self):
        self._save = (o.bc, o.run_claude, o._notify, o._cowork, o._stopped, o._selfheal_on,
                      o._notify_chain_card, o._loc_mark_chain_final)
        # бюджет claude (инцидент-каскад 22.07): в тестах НЕ считаем реальные процессы ПК
        # (powershell/CIM) — гейт всегда открыт; сам гейт тестирует TestClaudeBudget отдельно
        self._save_budget = o._claude_budget_gate
        o._claude_budget_gate = lambda *a, **k: (True, "тест: бюджет пропущен")
        self.addCleanup(lambda: setattr(o, "_claude_budget_gate", self._save_budget))
        self._save_lw = o.LESSON_WAIT_STATE       # ждущие low-уроки: боевой state-файл в тестах не читаем
        o.LESSON_WAIT_STATE = os.path.join(tempfile.mkdtemp(), "lesson_waits.json")
        self.addCleanup(lambda: setattr(o, "LESSON_WAIT_STATE", self._save_lw))
        self._save_af = o.AUTOFETCH_STATE_FILE    # признак состояния авто-фетча: свежий на КАЖДЫЙ тест
        o.AUTOFETCH_STATE_FILE = os.path.join(tempfile.mkdtemp(), "autofetch.json")  # (prev=None → чистый лист)
        self.addCleanup(lambda: setattr(o, "AUTOFETCH_STATE_FILE", self._save_af))
        # СЛЕДЫ РАБОТЫ (класс 30.07 «статус врёт»): по умолчанию следов НЕТ и отметки claim пишем
        # во временный файл. Иначе сбор улик пошёл бы в ЖИВОЙ git репозитория — окна тестов строятся
        # от «сейчас», и вердикт зависел бы от того, коммитил ли кто-то в последний час (тот же
        # класс, что боевой state-файл в тестах). Сам сбор проверяет TestFailReasonEvidence.
        self._save_ev = (o._work_evidence, o.TASK_START_FILE, o.COWORK_LEDGER)
        o._work_evidence = lambda since, until=None: {"commits": [], "journal": []}
        _tmp_marks = tempfile.mkdtemp()
        o.TASK_START_FILE = os.path.join(_tmp_marks, "task_started.json")
        o.COWORK_LEDGER = os.path.join(_tmp_marks, "cowork_log.ledger")
        self.addCleanup(lambda: (setattr(o, "_work_evidence", self._save_ev[0]),
                                 setattr(o, "TASK_START_FILE", self._save_ev[1]),
                                 setattr(o, "COWORK_LEDGER", self._save_ev[2])))
        # ЗАПРЕТ грязного дерева (класс 28.07): в тестах дерево по умолчанию ЧИСТОЕ — живой git не
        # дёргаем и не зависим от состояния рабочей копии. Сам запрет проверяет
        # TestDirtyTreeBlocksRestart, подменяя это же место своим списком.
        self._save_dirty = o._dirty_tracked
        o._dirty_tracked = lambda runner=None: []
        o._DIRTY_WARNED.clear()
        self.addCleanup(o._DIRTY_WARNED.clear)
        self.addCleanup(lambda: setattr(o, "_dirty_tracked", self._save_dirty))
        # ВОРОТА КЛИЕНТСКОГО КОНТУРА (класс 30.07): в тестах МЕХАНИКИ применения и маршрутизации
        # ворота по умолчанию ОТКРЫТЫ — иначе каждый тест рестарта проверял бы не рестарт, а право
        # на выкатку. Тот же приём, что с грязным деревом выше. Сами ворота стережёт
        # TestClientContourGate (ниже — восстанавливает боевые функции) и test_client_contour.py.
        self._save_cb = (o._client_block, o._revizor_finding_touches_client)
        o._client_block = lambda *a, **k: []
        o._revizor_finding_touches_client = lambda t: (False, [], True)
        o._CLIENT_HELD_WARNED.clear()
        self.addCleanup(o._CLIENT_HELD_WARNED.clear)
        self.addCleanup(lambda: (setattr(o, "_client_block", self._save_cb[0]),
                                 setattr(o, "_revizor_finding_touches_client", self._save_cb[1])))
        self.fb = FakeBridge()
        o.bc = self.fb
        o._notify = lambda *a, **k: None
        o._cowork = lambda *a, **k: None
        o._stopped = lambda: False
        o._selfheal_on = lambda: False        # существующие тесты — прежнее поведение (флаг off)
        o._notify_chain_card = lambda *a, **k: None   # не спавним dispatch_notify в тестах
        o._loc_mark_chain_final = lambda pid, path=None: None  # боевой state-файл в тестах не пишем

    def tearDown(self):
        (o.bc, o.run_claude, o._notify, o._cowork, o._stopped, o._selfheal_on,
         o._notify_chain_card, o._loc_mark_chain_final) = self._save

    def _claude(self, rc=0, out="готово\nRESULT: готово", err="", raise_timeout=False, write_marker=False):
        def fake(prompt, timeout, cwd, env):
            if raise_timeout:
                raise TimeoutError("timeout")
            if write_marker:                      # имитируем гард: штампуем карточку токеном запуска
                tok = env.get(o.MARKER_TOKEN_ENV, "")
                with open(env[o.ASK_MARKER_ENV], "a", encoding="utf-8") as f:
                    f.write(tok + o.MARKER_SEP + "🔴 Хочу удалить файл X — разрешить?\n")
            return (rc, out, err)
        o.run_claude = fake


class TestProcessNew(Base):
    def test_new_done(self):
        tid = self.fb.add(status="new")
        self._claude(0, "выполнено\nRESULT: выполнено")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertIn("выполнено", self.fb.tasks[tid]["result"])

    def test_new_timeout_failed(self):
        tid = self.fb.add(status="new")
        self._claude(raise_timeout=True)
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("таймаут", self.fb.tasks[tid]["result"].lower())

    def test_new_exit_nonzero_failed(self):
        tid = self.fb.add(status="new")
        self._claude(rc=1, out="", err="boom")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")

    def test_new_needs_approval_stdout_marker(self):
        tid = self.fb.add(status="new")
        self._claude(0, "NEEDS_APPROVAL: op=other | удалить старый лог")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "needs_approval")

    def test_new_needs_approval_file_marker(self):
        tid = self.fb.add(status="new")
        self._claude(0, "сделал", write_marker=True)
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "needs_approval")
        self.assertIn("гард", self.fb.tasks[tid]["result"].lower())

    def test_marker_lines_deduped_in_result(self):
        # claude ретраил красное — гард дописал карточку ×5. Демон дедупит строки → карточка ×1.
        tid = self.fb.add(status="new")
        line = "🔴 Хочу удалить файл X — разрешить?"

        def fake(prompt, timeout, cwd, env):
            tok = env.get(o.MARKER_TOKEN_ENV, "")
            with open(env[o.ASK_MARKER_ENV], "a", encoding="utf-8") as f:
                for _ in range(5):
                    f.write(tok + o.MARKER_SEP + line + "\n")
            return (0, "сделал", "")
        o.run_claude = fake
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "needs_approval")
        self.assertEqual(self.fb.tasks[tid]["result"].count("Хочу удалить файл X"), 1)

    def test_foreign_token_card_ignored(self):
        # карточка с ЧУЖИМ токеном (напр. утечка из другого запуска) → демон её игнорирует.
        tid = self.fb.add(status="new")

        def fake(prompt, timeout, cwd, env):
            with open(env[o.ASK_MARKER_ENV], "a", encoding="utf-8") as f:
                f.write("ЧУЖОЙ-РАН-999" + o.MARKER_SEP + "🔴 чужая карточка — разрешить?\n")
            return (0, "готово\nRESULT: готово", "")
        o.run_claude = fake
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")          # не наш токен → не наш ask
        self.assertNotIn("чужая карточка", str(self.fb.tasks[tid]["result"]))

    def test_stale_marker_cleared_before_run(self):
        # маркер прошлого прогона не должен протечь в новый результат (чистим перед запуском).
        tid = self.fb.add(status="new")
        stale = os.path.join(o.REPO, f"pc_ask_{tid}.marker")
        with open(stale, "w", encoding="utf-8") as f:
            f.write("🔴 старая красная карточка — разрешить?\n")
        try:
            self._claude(0, "всё зелёное, готово\nRESULT: готово")   # новый прогон без красного
            o.process_new()
            self.assertEqual(self.fb.tasks[tid]["status"], "done")   # не needs_approval
            self.assertNotIn("старая красная", self.fb.tasks[tid]["result"])
        finally:
            try:
                os.remove(stale)
            except Exception:
                pass

    def test_lane_isolation_other_lane_untouched(self):
        vps = self.fb.add(status="new", lane="vps", task_text="чужая VPS-задача")
        pc = self.fb.add(status="new", lane="pc")
        self._claude(0, "ок\nRESULT: ок")
        o.process_new()
        self.assertEqual(self.fb.tasks[vps]["status"], "new")   # чужая полоса не тронута
        self.assertEqual(self.fb.tasks[pc]["status"], "done")

    def test_no_lane_field_skipped(self):
        t = self.fb.add(status="new")
        self.fb.tasks[t]["lane"] = None            # нет полосы → пропуск (fail-safe)
        self._claude(0, "ок")
        o.process_new()
        self.assertEqual(self.fb.tasks[t]["status"], "new")

    def test_stopped_noop(self):
        tid = self.fb.add(status="new")
        o._stopped = lambda: True
        self._claude(0, "ок")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "new")   # рубильник → не берём


class TestOutputContract(Base):
    """Контракт результата (фикс ложного done #24): done только с «RESULT:»; пустой stdout →
    авто-повтор, снова пустой → failed с хвостом stderr; без RESULT → failed insufficient_output."""

    def test_empty_stdout_retried_then_failed_with_stderr(self):
        tid = self.fb.add(status="new")
        calls = {"n": 0}

        def fake(prompt, timeout, cwd, env):
            calls["n"] += 1
            return (0, "   ", "rate limit reached — upgrade plan")
        o.run_claude = fake
        o.process_new()
        self.assertEqual(calls["n"], 2)                       # ровно один авто-повтор
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        r = self.fb.tasks[tid]["result"]
        self.assertIn("пустой вывод", r.lower())
        self.assertIn("rate limit", r)                        # хвост stderr виден в карточке

    def test_empty_then_ok_transient_done(self):
        tid = self.fb.add(status="new")
        seq = iter([(0, "", ""), (0, "сделал\nRESULT: сделал X", "")])
        calls = {"n": 0}

        def fake(prompt, timeout, cwd, env):
            calls["n"] += 1
            return next(seq)
        o.run_claude = fake
        o.process_new()
        self.assertEqual(calls["n"], 2)
        self.assertEqual(self.fb.tasks[tid]["status"], "done")   # транзиент пережит повтором

    def test_no_result_line_insufficient_output(self):
        tid = self.fb.add(status="new")
        self._claude(0, "долго болтал, но итог не подтвердил")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        r = self.fb.tasks[tid]["result"]
        self.assertIn("insufficient_output", r)
        self.assertIn("болтал", r)                            # хвост stdout виден в карточке

    def test_with_result_line_done(self):
        tid = self.fb.add(status="new")
        self._claude(0, "сводка работ\nRESULT: закоммитил фикс abc123")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")

    def test_nonzero_exit_not_retried(self):
        tid = self.fb.add(status="new")
        calls = {"n": 0}

        def fake(prompt, timeout, cwd, env):
            calls["n"] += 1
            return (1, "", "boom")
        o.run_claude = fake
        o.process_new()
        self.assertEqual(calls["n"], 1)                       # повтор только для пустого rc=0
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")

    def test_preamble_requires_result_line(self):
        self.assertIn("RESULT:", o.PREAMBLE)                  # преамбула обязывает контракт


class TestEncoding(Base):
    """Кодировка headless (фикс кракозябр в карточках 829): utf-8/replace на чтении вывода +
    PYTHONIOENCODING=utf-8 ребёнку. Кириллица в мок-выводе должна доходить читаемой."""

    def test_run_claude_reads_utf8_replace(self):
        captured = {}

        class _P:
            returncode = 0
            stdout = "готово\nRESULT: кириллица жива"
            stderr = ""

        def fake_run(cmd, **kw):
            captured.update(kw)
            return _P()

        with mock.patch.object(o.subprocess, "run", fake_run), \
                mock.patch.object(o, "resolve_claude", lambda: sys.executable):
            rc, out, err = o.run_claude("p", 10, o.REPO, {"X": "1"})
        self.assertEqual(captured.get("encoding"), "utf-8")     # не локаль Windows (cp1251)
        self.assertEqual(captured.get("errors"), "replace")     # не падаем на неведомом байте
        self.assertIn("кириллица жива", out)

    def test_run_claude_passes_executor_model_and_effort(self):
        # ГОЛДЕН исполнителя (тема 328, 24.07.2026; 30.07.2026 переведён на Opus 5): модель и
        # усилие в argv ЯВНО — claude-opus-5 / xhigh, ПОЛНЫЙ id (короткий алиас → HTTP 404,
        # класс #194). Без --model claude -p молча брал model из .claude/settings.json (дефолт
        # интерактивных сессий), а env-ручки ORCH_MODEL/EXECUTOR_MODEL (имена VPS-полосы) на ПК
        # не читаются вовсе.
        captured = {}

        class _P:
            returncode = 0
            stdout = "RESULT: ок"
            stderr = ""

        def fake_run(cmd, **kw):
            captured["argv"] = cmd
            return _P()

        with mock.patch.object(o.subprocess, "run", fake_run), \
                mock.patch.object(o, "resolve_claude", lambda: r"C:\x\claude.exe"):
            o.run_claude("p", 10, o.REPO, {"X": "1"})
        argv = captured["argv"]
        self.assertEqual(o.EXECUTOR_MODEL, "claude-opus-5")                 # решение владельца 30.07
        self.assertEqual(argv[argv.index("--model") + 1], "claude-opus-5")
        self.assertEqual(argv[argv.index("--effort") + 1], "xhigh")
        self.assertEqual(argv[-1], "p")                                     # prompt строго последним

    def test_child_env_has_pythonioencoding_utf8(self):
        captured = {}

        def fake(prompt, timeout, cwd, env):
            captured["env"] = dict(env)
            return (0, "готово\nRESULT: ок", "")
        o.run_claude = fake
        self.fb.add(status="new")
        o.process_new()
        self.assertEqual(captured["env"].get("PYTHONIOENCODING"), "utf-8")

    def test_cyrillic_result_readable_end_to_end(self):
        tid = self.fb.add(status="new")
        self._claude(0, "выполнил\nRESULT: настроил кодировку — кириллица читаема, кракозябр нет")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertIn("кириллица читаема", self.fb.tasks[tid]["result"])

    def test_schtasks_run_reads_utf8_replace(self):
        captured = {}

        class _P:
            returncode = 0
            stdout = "УСПЕХ: задача запущена"
            stderr = ""

        def fake_run(cmd, **kw):
            captured.update(kw)
            return _P()

        with mock.patch.object(o.subprocess, "run", fake_run):
            rc, out = o._schtasks_run("pc_orchestrator")
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")
        self.assertIn("УСПЕХ", out)


class TestCoworkFullResult(Base):
    """Полный отчёт в мозг: при done/failed в cowork_log идёт не только статус, а полный текст
    RESULT/причины failed (усечение до ~1500 символов с пометкой «…обрезано»)."""

    def setUp(self):
        super().setUp()
        self.lines = []
        o._cowork = lambda line: self.lines.append(line)

    def test_done_writes_full_result_to_cowork(self):
        self.fb.add(status="new")
        self._claude(0, "сводка работ\nRESULT: закоммитил фикс кодировки и наблюдаемости pc_orchestrator")
        o.process_new()
        joined = "\n".join(self.lines)
        self.assertIn("→ done", joined)
        self.assertIn("закоммитил фикс кодировки", joined)     # полный RESULT, не только статус

    def test_failed_writes_reason_to_cowork(self):
        self.fb.add(status="new")
        self._claude(0, "долго болтал, но итог не подтвердил")   # нет RESULT: → insufficient_output
        o.process_new()
        joined = "\n".join(self.lines)
        self.assertIn("→ failed", joined)
        self.assertIn("insufficient_output", joined)           # причина failed видна штабу

    def test_long_result_truncated_with_marker(self):
        self.fb.add(status="new")
        big = "RESULT: " + ("хвост " * 600)                     # >1500 символов
        self._claude(0, "работа\n" + big)
        o.process_new()
        done_line = next(l for l in self.lines if "→ done" in l)
        self.assertIn("…обрезано", done_line)                  # усечение помечено
        self.assertLessEqual(len(done_line), 1600)             # ~1500 + префикс/пометка

    def test_clip_collapses_and_marks(self):
        self.assertEqual(o._clip("а  б\nв"), "а б в")           # схлопывает пробелы/переносы
        self.assertTrue(o._clip("x" * 5000).endswith("…обрезано"))
        self.assertEqual(o._clip("коротко"), "коротко")        # короткое не трогаем


class TestApproved(Base):
    def test_approved_rerun_done(self):
        tid = self.fb.add(status="approved", updated=iso_ago(10))
        self._claude(0, "доделал после одобрения\nRESULT: доделал")
        o.process_approved()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")

    def test_approved_again_red_failed(self):
        tid = self.fb.add(status="approved", updated=iso_ago(10))
        self._claude(0, "NEEDS_APPROVAL: op=other | снова красное")
        o.process_approved()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")   # не зацикливаемся
        self.assertIn("вручную", self.fb.tasks[tid]["result"].lower())

    def test_approved_expired_failed_no_claude(self):
        tid = self.fb.add(status="approved", updated=iso_ago(4000))   # >30 мин
        called = {"n": 0}
        def fake(*a, **k):
            called["n"] += 1
            return (0, "не должно вызваться")
        o.run_claude = fake
        o.process_approved()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertEqual(called["n"], 0)             # истёкшую не запускаем
        self.assertIn("истёк", self.fb.tasks[tid]["result"].lower())


class TestApprovalReachesExecutor(Base):
    """ГОЛДЕНЫ 30.07.2026 (дефект «одобрение не доходит»). Живой разбор: 10 нажатий «да» → 3 done
    и 7 ✋failed «одобрено, но шаг снова упирается в красное» (281, 338, 362, 363, 364, 48, 55).
    Одобрение доезжало до ре-рана, но терялось ВНУТРИ него: гард в НОВОМ дочернем процессе про
    «да» не знал (задача 55), а модель по преамбуле снова печатала NEEDS_APPROVAL (задача 48).
    Теперь класс операции из карточки едет ребёнку ДВУМЯ каналами — env-маркер и абзац промпта."""

    def _spy(self, out="сделал одобренное\nRESULT: готово"):
        seen = {}

        def fake(prompt, timeout, cwd, env):
            seen["prompt"] = prompt
            seen["env"] = dict(env)
            return (0, out, "")
        o.run_claude = fake
        return seen

    def _approved_with_card(self, card, text="почисти указатели мозга"):
        tid = self.fb.add(status="approved", updated=iso_ago(10), task_text=text)
        self.fb.tasks[tid]["result"] = card
        self.fb.tasks[tid]["what"] = card
        return tid

    # --- канал А: гард в дочернем процессе ---

    def test_env_marker_reaches_child_with_class_from_card(self):
        card = ("NEEDS_APPROVAL (гард): 🔴 Хочу обратиться к .env / секретам — разрешить?\n"
                "Объект: .env\nЧисло: —\n" + pretool_guard.KIND_LINE_PREFIX + "env")
        tid = self._approved_with_card(card)
        seen = self._spy()
        o.process_approved()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertEqual(seen["env"][o.APPROVED_KINDS_ENV], "env")     # маркер ДОЛЕТЕЛ до ребёнка
        self.assertEqual(seen["env"][o.APPROVED_TASK_ENV], str(tid))
        # маркер живёт ТОЛЬКО в этом запуске: у самого демона его нет
        self.assertNotIn(o.APPROVED_KINDS_ENV, os.environ)

    def test_guard_accepts_that_marker_for_that_class_only(self):
        """Смычка двух модулей: то, что демон положил в env, гард уважает — и только по классу."""
        card = pretool_guard.KIND_LINE_PREFIX + "env"
        tid = self._approved_with_card(card)
        seen = self._spy()
        o.process_approved()
        env = seen["env"]
        red = {"tool_name": "Bash", "tool_input": {"command": "cat ." + "env"}, "cwd": o.REPO}
        other = {"tool_name": "Bash", "tool_input": {"command": "del /f /q a.log b.log"}, "cwd": o.REPO}
        self.assertEqual(pretool_guard.decide_for_role(red, True, env=env)[0], "approved")
        self.assertEqual(pretool_guard.decide_for_role(other, True, env=env)[0], "ask")
        self.assertEqual(pretool_guard.decide_for_role(red, True, env={})[0], "ask")
        self.assertEqual(self.fb.tasks[tid]["status"], "done")

    # --- канал Б: сама модель ---

    def test_prompt_gets_approval_clause_after_preamble(self):
        tid = self._approved_with_card("op=schtasks | автозапуск через Планировщик")
        seen = self._spy()
        o.process_approved()
        p = seen["prompt"]
        self.assertIn("ОДОБРЕНО ЧЕЛОВЕКОМ", p)                       # прежняя нота на месте
        self.assertIn("ОДОБРЕНИЕ ВЛАДЕЛЬЦА", p)                      # и новый абзац
        self.assertIn("schtasks", p)
        self.assertGreater(p.index("ОДОБРЕНИЕ ВЛАДЕЛЬЦА"), p.index("NEEDS_APPROVAL"))  # ПОСЛЕ преамбулы
        self.assertLess(p.index("ОДОБРЕНИЕ ВЛАДЕЛЬЦА"), p.index("автозапуск")
                        if "автозапуск" in p else len(p))            # …и ДО текста задачи
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertEqual(seen["env"][o.APPROVED_KINDS_ENV], "schtasks")

    def test_preamble_requires_class_in_marker(self):
        self.assertIn("op=<класс>", o.PREAMBLE)
        for kind in ("delete", "env", "kill", "schtasks", "other"):
            self.assertIn(kind, o.PREAMBLE)
        self.assertIn("RESULT:", o.PREAMBLE)                          # контракт итога не потерян

    # --- границы: без класса — прежнее поведение байт-в-байт ---

    def test_no_class_in_card_keeps_old_behaviour(self):
        for card in (None, "", "op=other | снова красное", "просто текст"):
            self.fb.tasks.clear()
            tid = self.fb.add(status="approved", updated=iso_ago(10))
            self.fb.tasks[tid]["result"] = card
            seen = self._spy()
            o.process_approved()
            self.assertNotIn(o.APPROVED_KINDS_ENV, seen["env"], repr(card))
            self.assertNotIn("ОДОБРЕНИЕ ВЛАДЕЛЬЦА", seen["prompt"], repr(card))

    def test_approved_kinds_reader(self):
        self.assertEqual(o._approved_kinds({"result": pretool_guard.KIND_LINE_PREFIX + "delete"}),
                         frozenset({"delete"}))
        self.assertEqual(o._approved_kinds({"what": "op=kill | снять процесс 4242"}),
                         frozenset({"kill"}))
        for junk in ({}, {"result": None}, {"result": "op=other | x"}, None):
            self.assertEqual(o._approved_kinds(junk), frozenset(), repr(junk))

    def test_still_red_after_approve_says_which_class_was_approved(self):
        """Ре-ран всё равно упёрся в красное → ✋ как было, но диагноз теперь различает
        «одобрен класс X, уперлись в другое» и «класс не назван»."""
        tid = self._approved_with_card(pretool_guard.KIND_LINE_PREFIX + "env")
        self._claude(0, "NEEDS_APPROVAL: op=delete | а теперь удалить")
        o.process_approved()
        st = self.fb.tasks[tid]
        self.assertEqual(st["status"], "failed")
        self.assertTrue(st["result"].startswith(o.MANUAL_MARK))
        self.assertIn("вручную", st["result"])
        self.assertIn("одобрен класс env", st["result"])

        self.fb.tasks.clear()
        tid2 = self.fb.add(status="approved", updated=iso_ago(10))
        self.fb.tasks[tid2]["result"] = "op=other | не назвал класс"
        self._claude(0, "NEEDS_APPROVAL: op=other | снова красное")
        o.process_approved()
        self.assertIn("не назван", self.fb.tasks[tid2]["result"])

    def test_expired_approve_never_spawns_child(self):
        tid = self._approved_with_card(pretool_guard.KIND_LINE_PREFIX + "env")
        self.fb.tasks[tid]["updated"] = iso_ago(4000)
        called = {"n": 0}
        o.run_claude = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or (0, "нет", "")
        o.process_approved()
        self.assertEqual(called["n"], 0)                              # истёкшее не запускаем
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")

    def test_orch_runtime_covers_every_top_import_of_daemon(self):
        """Ворота грязного дерева (класс 28.07) обязаны знать ВСЁ, что демон несёт своими верхними
        импортами: иначе незакоммиченная правка такого модуля уедет в бой с авто-рестартом. Список
        сверяем с реальными `import X` файла демона, а не с памятью."""
        import ast
        with open(os.path.join(o.REPO, "pc_orchestrator.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        # МОДУЛЬНЫЙ уровень (в т.ч. внутри try/except — так втянуты log_setup и pretool_guard).
        # Ленивые импорты внутри функций (`suggest` — тяжёлый клиентский модуль) сюда НЕ относятся:
        # их грязь стережёт карта клиентского контура, а не список демона.
        local, stack = set(), list(tree.body)
        while stack:
            node = stack.pop()
            if isinstance(node, ast.Try):
                stack.extend(node.body + node.orelse + node.finalbody)
                for h in node.handlers:
                    stack.extend(h.body)
                continue
            if isinstance(node, ast.Import):
                for a in node.names:
                    if os.path.isfile(os.path.join(o.REPO, a.name + ".py")):
                        local.add(a.name + ".py")
        self.assertIn("pretool_guard.py", local)          # смычка с гардом реально в импортах
        missing = sorted(local - set(o._ORCH_RUNTIME))
        self.assertEqual(missing, [], f"верхние импорты демона вне _ORCH_RUNTIME: {missing}")

    def test_plain_new_task_has_no_approval_marker(self):
        """Обычная (не одобренная) задача маркера не получает — послабление строго per-task."""
        self.fb.add(status="new")
        seen = self._spy()
        o.process_new()
        self.assertNotIn(o.APPROVED_KINDS_ENV, seen["env"])
        self.assertNotIn(o.APPROVED_TASK_ENV, seen["env"])
        self.assertNotIn("ОДОБРЕНИЕ ВЛАДЕЛЬЦА", seen["prompt"])


class TestApprovalTimeout(Base):
    def test_na_timeout_failed(self):
        tid = self.fb.add(status="needs_approval", updated=iso_ago(4000))
        o.process_approval_timeouts()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("30 мин", self.fb.tasks[tid]["result"])

    def test_na_recent_kept(self):
        tid = self.fb.add(status="needs_approval", updated=iso_ago(60))
        o.process_approval_timeouts()
        self.assertEqual(self.fb.tasks[tid]["status"], "needs_approval")   # ждём ещё


class TestWatchdog(Base):
    """Внешний вотчдог: живость ПО ФАКТУ (heartbeat ИЛИ процесс) + антиспам алерта.
    Класс-голдены ложняка 13.07: живой демон на длинной задаче (heartbeat замер) и self-update
    смена PID НЕ считаются смертью; алерт — ровно один на инцидент."""

    def setUp(self):
        super().setUp()
        self._save_hb = o._heartbeat_fresh
        self._tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self._tmp.name, "wd_state.json")
        self.notes, self.pushes = [], []
        self.cow = lambda s: self.notes.append(s)
        self.push = lambda s: self.pushes.append(s)
        self.runs = {"n": 0}

    def tearDown(self):
        o._heartbeat_fresh = self._save_hb
        self._tmp.cleanup()
        super().tearDown()

    def _runner(self):
        self.runs["n"] += 1
        return (0, "SUCCESS: task run")

    def _wd(self, finder, wall_now=None):
        return o.watchdog(runner=self._runner, verify_sleep=0, finder=finder,
                          state_path=self.state, notify=self.push, cowork=self.cow,
                          wall_now=wall_now)

    # (a) демон жив + heartbeat свеж → ТИШИНА: ни подъёма, ни алертов.
    def test_a_alive_hb_fresh_silence(self):
        o._heartbeat_fresh = lambda *a, **k: True
        finder_calls = {"n": 0}
        def finder():
            finder_calls["n"] += 1
            return [111]
        r = self._wd(finder)
        self.assertEqual(r, "alive")
        self.assertEqual(self.runs["n"], 0)
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.notes, [])
        self.assertEqual(finder_calls["n"], 0)       # свежий heartbeat — CIM даже не дёргаем

    # КОРЕНЬ ложняка 13.07: heartbeat замер на длинной задаче, но процесс демона ЖИВ → тишина.
    def test_long_task_hb_stale_process_alive_no_alert(self):
        o._heartbeat_fresh = lambda *a, **k: False
        r = self._wd(lambda: [2096])                 # живой PID демона (как 13.07 на задачах 256-260)
        self.assertEqual(r, "alive")
        self.assertEqual(self.runs["n"], 0)          # НЕ поднимаем второй экземпляр
        self.assertEqual(self.pushes, [])            # НЕ спамим
        self.assertEqual(self.notes, [])

    # (c) self-update: PID сменился, heartbeat свеж → БЕЗ алерта (прежний PID никого не волнует).
    def test_c_selfupdate_new_pid_hb_fresh_no_alert(self):
        o._heartbeat_fresh = lambda *a, **k: True
        r = self._wd(lambda: [55555])                # новый PID после эстафеты
        self.assertEqual(r, "alive")
        self.assertEqual(self.pushes, [])
        # и вариант: heartbeat на миг рестарта протух, но НОВЫЙ процесс уже жив → тоже тишина
        o._heartbeat_fresh = lambda *a, **k: False
        r = self._wd(lambda: [55556])
        self.assertEqual(r, "alive")
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.runs["n"], 0)

    # (b) ДОКАЗАННО мёртв → подъём + РОВНО один алерт + NOTE; инцидент длится → без повторов.
    def test_b_dead_exactly_one_alert_and_note(self):
        o._heartbeat_fresh = lambda *a, **k: False
        dead = lambda: []                            # CIM честно: процессов нет (и после /Run)
        r = self._wd(dead, wall_now=1_000_000.0)
        self.assertEqual(r, "failed_to_start")
        self.assertEqual(self.runs["n"], 1)          # попытка подъёма была
        self.assertEqual(len(self.pushes), 1)        # РОВНО один алерт
        self.assertEqual(len(self.notes), 1)         # + NOTE в cowork
        self.assertIn("не смог поднять", self.pushes[0])
        # следующие тики того же инцидента (каждые 5 мин) — ТИШИНА
        for dt in (300.0, 600.0, 3600.0):
            self._wd(dead, wall_now=1_000_000.0 + dt)
        self.assertEqual(len(self.pushes), 1)
        self.assertEqual(len(self.notes), 1)

    # мёртв → schtasks поднял (процесс появился) → restarted, БЕЗ алерта.
    def test_restart_success_by_process(self):
        o._heartbeat_fresh = lambda *a, **k: False
        seq = iter([[], [7777]])                     # до /Run пусто → после /Run процесс жив
        r = self._wd(lambda: next(seq))
        self.assertEqual(r, "restarted")
        self.assertEqual(self.runs["n"], 1)
        self.assertEqual(self.pushes, [])

    # регресс прежнего пути: подъём подтверждён свежим heartbeat.
    def test_restart_success_by_heartbeat(self):
        hb = iter([False, True])
        o._heartbeat_fresh = lambda *a, **k: next(hb)
        r = self._wd(lambda: [])
        self.assertEqual(r, "restarted")
        self.assertEqual(self.runs["n"], 1)
        self.assertEqual(self.pushes, [])

    # CIM не смог (#171) → страховочный /Run, БЕЗ алерта (смерть не доказана).
    def test_cim_blind_unknown_no_alert(self):
        o._heartbeat_fresh = lambda *a, **k: False
        r = self._wd(lambda: None)
        self.assertEqual(r, "unknown")
        self.assertEqual(self.runs["n"], 1)          # подстраховались (singleton защитит живого)
        self.assertEqual(self.pushes, [])

    # восстановление после инцидента: NOTE «инцидент закрыт» один раз; новый инцидент после
    # кулдауна → НОВЫЙ алерт (переход жив→мёртв), внутри кулдауна → тишина (флап-защита).
    def test_recovery_note_and_next_incident(self):
        o._heartbeat_fresh = lambda *a, **k: False
        dead = lambda: []
        self._wd(dead, wall_now=1_000_000.0)                       # инцидент №1: алерт
        self.assertEqual(len(self.pushes), 1)
        o._heartbeat_fresh = lambda *a, **k: True
        self.assertEqual(self._wd(dead, wall_now=1_000_100.0), "alive")   # ожил
        self.assertTrue(any("снова жив" in s for s in self.notes))        # NOTE о закрытии
        n_notes = len(self.notes)
        self._wd(dead, wall_now=1_000_160.0)                       # всё ещё жив — без дублей NOTE
        self.assertEqual(len(self.notes), n_notes)
        o._heartbeat_fresh = lambda *a, **k: False
        self._wd(dead, wall_now=1_000_200.0)                       # инцидент №2 ВНУТРИ кулдауна
        self.assertEqual(len(self.pushes), 1)                      # флап-защита: алерта нет
        o._heartbeat_fresh = lambda *a, **k: True
        self._wd(dead, wall_now=1_000_300.0)                       # снова ожил
        o._heartbeat_fresh = lambda *a, **k: False
        self._wd(dead, wall_now=1_001_000.0)                       # инцидент №3 ПОСЛЕ кулдауна
        self.assertEqual(len(self.pushes), 2)                      # новый переход → новый алерт

    def test_stopped_switch(self):
        o._stopped = lambda: True
        r = self._wd(lambda: [])
        self.assertEqual(r, "stopped")
        self.assertEqual(self.runs["n"], 0)          # рубильник активен — не поднимаем


class TestUnit(unittest.TestCase):
    def test_detect_needs_approval(self):
        self.assertIn("гард", o._detect_needs_approval("сделал", "удалить X").lower())
        self.assertIsNotNone(o._detect_needs_approval("NEEDS_APPROVAL: op=other | X", ""))
        self.assertIsNotNone(o._detect_needs_approval("это требует подтверждения", ""))
        self.assertIsNone(o._detect_needs_approval("всё зелёное, готово", ""))

    def test_detect_needs_approval_dedups_marker_lines(self):
        dup = "🔴 карточка — разрешить?\n" * 5
        card = o._detect_needs_approval("сделал", dup)
        self.assertEqual(card.count("🔴 карточка — разрешить?"), 1)   # была ×5 → ×1

    def test_lane_ok(self):
        self.assertTrue(o._lane_ok({"lane": "pc"}))
        self.assertFalse(o._lane_ok({"lane": "vps"}))
        self.assertFalse(o._lane_ok({}))

    def test_self_update_gate_ok(self):
        ok, msg = o.self_update_ok()
        self.assertTrue(ok, msg)                     # актуальный pc_orchestrator.py проходит гейт


class TestNeedsApprovalTopic(unittest.TestCase):
    def test_topic_829_default(self):
        self.assertEqual(o.NEEDS_APPROVAL_TOPIC, 829)

    def test_set_needs_approval_passes_topic_829(self):
        b = o.Bridge(url="https://x", token="t")
        captured = {}

        def fake_post(action, **fields):
            captured["action"] = action
            captured.update(fields)
            return {"ok": True}

        b._post = fake_post
        b.set_needs_approval(5, "красная карточка")
        self.assertEqual(captured["action"], "set_needs_approval")
        self.assertEqual(captured["id"], 5)
        self.assertEqual(captured["topic"], 829)     # карточка → тема 829 (уточнение Филиппа)


class TestResolveClaude(unittest.TestCase):
    """Версионно-независимый резолв claude (класс-фикс WinError 2 при автообновлении)."""

    def setUp(self):
        self._save = (o._claude_cache, o.CLAUDE_BIN, o._CLAUDE_BASE)
        o._claude_cache = None
        # ИЗОЛЯЦИЯ от реального MSIX-claude на машине (LOCALAPPDATA\Packages\Claude_*) —
        # иначе _claude_base_dirs нашёл бы боевой бинарь и сломал тесты «None/конкретный путь».
        self._latmp = tempfile.TemporaryDirectory()
        self._save_la = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._latmp.name

    def tearDown(self):
        (o._claude_cache, o.CLAUDE_BIN, o._CLAUDE_BASE) = self._save
        if self._save_la is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._save_la
        self._latmp.cleanup()

    def test_msix_package_path_found(self):
        # Store/MSIX: реальный бинарь в LOCALAPPDATA\Packages\Claude_*\LocalCache\...\claude-code
        pkg = os.path.join(self._latmp.name, "Packages", "Claude_xyz", "LocalCache",
                           "Roaming", "Claude", "claude-code", "2.1.197")
        os.makedirs(pkg)
        exe = os.path.join(pkg, "claude.exe"); open(exe, "w").close()
        o.CLAUDE_BIN = "claude"
        with tempfile.TemporaryDirectory() as empty:
            o._CLAUDE_BASE = empty                            # обычная база пуста → берём MSIX
            self.assertTrue(any("Claude_xyz" in b for b in o._claude_base_dirs()))
            with mock.patch.object(o.shutil, "which", return_value=None), \
                 mock.patch.object(o, "_claude_candidates", return_value=[]):
                self.assertEqual(o.resolve_claude(retry_sleep=0), exe)

    def test_prefers_path_shim(self):
        o.CLAUDE_BIN = "claude"
        with mock.patch.object(o.shutil, "which", return_value=sys.executable):
            self.assertEqual(o.resolve_claude(), sys.executable)   # PATH-шим (.cmd/.exe) выигрывает

    def test_env_bin_fallback_when_not_on_path(self):
        o.CLAUDE_BIN = sys.executable                              # существующий абсолютный файл
        with mock.patch.object(o.shutil, "which", return_value=None):
            self.assertEqual(o.resolve_claude(), sys.executable)

    def test_env_bin_ignored_when_missing(self):
        o.CLAUDE_BIN = r"C:\nope\claude-2.1.197\claude.exe"        # протухший .env-путь
        with tempfile.TemporaryDirectory() as base:
            sub = os.path.join(base, "2.1.201"); os.makedirs(sub)
            exe = os.path.join(sub, "claude.exe"); open(exe, "w").close()
            o._CLAUDE_BASE = base
            with mock.patch.object(o.shutil, "which", return_value=None):
                self.assertEqual(o.resolve_claude(), exe)          # протухший .env → берём установленную версию

    def test_live_but_stale_pin_loses_to_newer_install(self):
        """ГОЛДЕН авто-подхвата обновлений: пин в .env — ПОЛ, а не потолок.

        Мёртвый пин мы пропускали и раньше (os.path.isfile), но ЖИВОЙ, ОТСТАВШИЙ пин молча
        побеждал новейшую установку. Живой факт 22.07: CLAUDE_BIN=…\\2.1.215\\claude.exe при
        стоящей рядом 2.1.217 → резолвер отдавал 2.1.215. Версия берётся ЧИСЛОВЫМ ключом:
        mtime каталога врёт (у 2.1.215 и 2.1.217 он совпал)."""
        with tempfile.TemporaryDirectory() as base:
            for v in ("2.1.215", "2.1.217"):
                os.makedirs(os.path.join(base, v))
                open(os.path.join(base, v, "claude.exe"), "w").close()
            o._CLAUDE_BASE = base
            o.CLAUDE_BIN = os.path.join(base, "2.1.215", "claude.exe")   # живой, но отставший
            with mock.patch.object(o.shutil, "which", return_value=None):
                got = o.resolve_claude()
            self.assertEqual(os.path.basename(os.path.dirname(got)), "2.1.217")

    def test_pin_on_newest_is_kept(self):
        with tempfile.TemporaryDirectory() as base:
            for v in ("2.1.215", "2.1.217"):
                os.makedirs(os.path.join(base, v))
                open(os.path.join(base, v, "claude.exe"), "w").close()
            o._CLAUDE_BASE = base
            pin = os.path.join(base, "2.1.217", "claude.exe")
            o.CLAUDE_BIN = pin
            with mock.patch.object(o.shutil, "which", return_value=None):
                self.assertEqual(o.resolve_claude(), pin)

    def test_non_versioned_pin_is_honoured(self):
        """Пин ИНОЙ схемы (не …/<версия>/claude.exe) — осознанный выбор пути, а не отставшая
        версия: его не подменяем, даже если рядом стоит версионная установка."""
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "9.9.9"))
            open(os.path.join(base, "9.9.9", "claude.exe"), "w").close()
            o._CLAUDE_BASE = base
            o.CLAUDE_BIN = sys.executable                    # …\Scripts\python.exe — не версионная схема
            with mock.patch.object(o.shutil, "which", return_value=None):
                self.assertEqual(o.resolve_claude(), sys.executable)

    def test_strict_switch_freezes_on_pinned_version(self):
        """Осознанно замереть на старой версии всё ещё можно — рубильником CLAUDE_BIN_STRICT=1."""
        with tempfile.TemporaryDirectory() as base:
            for v in ("2.1.215", "2.1.217"):
                os.makedirs(os.path.join(base, v))
                open(os.path.join(base, v, "claude.exe"), "w").close()
            o._CLAUDE_BASE = base
            pin = os.path.join(base, "2.1.215", "claude.exe")
            o.CLAUDE_BIN = pin
            os.environ["CLAUDE_BIN_STRICT"] = "1"
            try:
                with mock.patch.object(o.shutil, "which", return_value=None):
                    self.assertEqual(o.resolve_claude(), pin)
            finally:
                os.environ.pop("CLAUDE_BIN_STRICT", None)

    def test_version_key_is_numeric_not_lexicographic(self):
        self.assertGreater(o._ver_key("2.1.217"), o._ver_key("2.1.99"))
        self.assertEqual(o._pinned_version(os.path.join("x", "2.1.217", "claude.exe")), (2, 1, 217))
        self.assertIsNone(o._pinned_version(os.path.join("x", "Scripts", "claude.exe")))

    def test_newest_version_glob(self):
        o.CLAUDE_BIN = r"C:\nope\claude.exe"
        with tempfile.TemporaryDirectory() as base:
            for v in ("2.1.99", "2.1.100", "2.1.7"):
                sub = os.path.join(base, v); os.makedirs(sub)
                open(os.path.join(sub, "claude.exe"), "w").close()
            o._CLAUDE_BASE = base
            with mock.patch.object(o.shutil, "which", return_value=None):
                got = o.resolve_claude()
            self.assertEqual(os.path.basename(os.path.dirname(got)), "2.1.100")  # 100>99 (версией, не строкой)

    def test_none_when_absent(self):
        o.CLAUDE_BIN = r"C:\nope\claude.exe"
        with tempfile.TemporaryDirectory() as d:
            o._CLAUDE_BASE = d
            with mock.patch.object(o.shutil, "which", return_value=None), \
                 mock.patch.object(o, "_claude_candidates", return_value=[]):
                self.assertIsNone(o.resolve_claude(retry_sleep=0))

    def test_cache_revalidates_after_removal(self):
        with tempfile.TemporaryDirectory() as base:
            sub = os.path.join(base, "2.1.5"); os.makedirs(sub)
            exe = os.path.join(sub, "claude.exe"); open(exe, "w").close()
            o._CLAUDE_BASE = base; o.CLAUDE_BIN = "claude"
            with mock.patch.object(o.shutil, "which", return_value=None), \
                 mock.patch.object(o, "_claude_candidates", return_value=[]):
                self.assertEqual(o.resolve_claude(), exe)
                os.remove(exe)                                     # автообновление удалило версию
                self.assertIsNone(o.resolve_claude(retry_sleep=0))   # кэш перепроверен → не мёртвый путь

    def test_new_scheme_candidate_found(self):
        # автообновление сменило место установки → бинарь находится по явным кандидатам (п.4)
        o.CLAUDE_BIN = r"C:\nope\claude.exe"
        with tempfile.TemporaryDirectory() as d:
            o._CLAUDE_BASE = os.path.join(d, "empty-base")         # старой схемы больше нет
            exe = os.path.join(d, ".local", "bin", "claude.exe")   # native-инсталлер (новая схема)
            os.makedirs(os.path.dirname(exe)); open(exe, "w").close()
            with mock.patch.object(o.shutil, "which", return_value=None), \
                 mock.patch.object(o, "_claude_candidates", return_value=[exe]):
                self.assertEqual(o.resolve_claude(), exe)

    def test_candidates_cover_known_schemes(self):
        cands = "\n".join(o._claude_candidates()).lower()
        self.assertIn(os.path.join(".local", "bin", "claude.exe"), cands)   # native-инсталлер
        self.assertIn(os.path.join("programs", "claude"), cands)            # LOCALAPPDATA\Programs
        self.assertIn(os.path.join("npm", "claude.cmd"), cands)             # npm-шим

    def test_transient_miss_recovered_by_retry(self):
        # кейс #35: файл «мигнул» (стейджинг автообновления/AV) — первый проход пуст, ретрай находит
        o.CLAUDE_BIN = r"C:\nope\claude.exe"
        with tempfile.TemporaryDirectory() as base:
            o._CLAUDE_BASE = base
            exe = os.path.join(base, "2.1.200", "claude.exe")

            def appear(_):
                os.makedirs(os.path.dirname(exe), exist_ok=True)
                open(exe, "w").close()                             # файл «вернулся» перед ретраем
            with mock.patch.object(o.shutil, "which", return_value=None), \
                 mock.patch.object(o, "_claude_candidates", return_value=[]), \
                 mock.patch.object(o.time, "sleep", side_effect=appear):
                self.assertEqual(o.resolve_claude(retries=1), exe)   # второй проход нашёл


class TestClaudeUnresolvable(Base):
    def test_run_task_failed_loud_when_not_found(self):
        tid = self.fb.add(status="new")
        save = o.resolve_claude
        o.resolve_claude = lambda: None                            # claude нигде не найден
        try:
            o.process_new()
        finally:
            o.resolve_claude = save
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("не найден", self.fb.tasks[tid]["result"].lower())


class TestSelfUpdate(Base):
    """Механика самообновления (порт VPS): всё замокано — git/гейты/spawn НЕ дёргаем."""

    def setUp(self):
        super().setUp()
        self._su = (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB, o._selfupdate_restart_children)
        o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB = "blob-old", "aaa1111", None
        # реконсиляция детей замокана в no-op: git-дифф/рестарт боевые НЕ трогаем (проверяем отдельно)
        o._selfupdate_restart_children = lambda *a, **k: ""

    def tearDown(self):
        (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB, o._selfupdate_restart_children) = self._su
        super().tearDown()

    def test_no_diff_no_restart(self):
        spawned = {"n": 0}
        r = o.maybe_self_update(blob_fn=lambda: "blob-old",
                                spawner=lambda: spawned.__setitem__("n", 1) or True)
        self.assertFalse(r)
        self.assertEqual(spawned["n"], 0)                     # без диффа не трогаем процесс

    def test_git_unavailable_no_restart(self):
        r = o.maybe_self_update(blob_fn=lambda: None, spawner=lambda: True)
        self.assertFalse(r)                                   # git молчит → не рискуем

    def test_diff_gates_pass_handover(self):
        calls = []
        r = o.maybe_self_update(
            blob_fn=lambda: "blob-new", head_fn=lambda: "bbb2222",
            code_gate=lambda: calls.append("code") or (True, "ok"),
            tests_gate=lambda: calls.append("tests") or (True, "ok"),
            spawner=lambda: calls.append("spawn") or True)
        self.assertTrue(r)                                    # → старый процесс должен выйти
        self.assertEqual(calls, ["code", "tests", "spawn"])   # гейты СТРОГО до spawn

    def test_selfupdate_line_in_cowork(self):
        lines = []
        o._cowork = lambda s: lines.append(s)
        o.maybe_self_update(blob_fn=lambda: "blob-new", head_fn=lambda: "bbb2222",
                            code_gate=lambda: (True, "ok"), tests_gate=lambda: (True, "ok"),
                            spawner=lambda: True)
        self.assertTrue(any("self-update: aaa1111→bbb2222" in s for s in lines))   # строка старый→новый

    def test_code_gate_fail_no_restart_memoized(self):
        gates, spawned = {"n": 0}, {"n": 0}

        def cg():
            gates["n"] += 1
            return (False, "битый импорт")
        for _ in (1, 2):                                      # два цикла подряд
            r = o.maybe_self_update(blob_fn=lambda: "blob-new", code_gate=cg,
                                    spawner=lambda: spawned.__setitem__("n", 1) or True)
            self.assertFalse(r)
        self.assertEqual(spawned["n"], 0)
        self.assertEqual(gates["n"], 1)                       # провал запомнен по блобу — гейт не перегоняем

    def test_new_commit_after_reject_regates(self):
        gates = {"n": 0}

        def cg():
            gates["n"] += 1
            return (False, "всё ещё битый")
        o.maybe_self_update(blob_fn=lambda: "blob-new", code_gate=cg, spawner=lambda: True)
        o.maybe_self_update(blob_fn=lambda: "blob-new2", code_gate=cg, spawner=lambda: True)
        self.assertEqual(gates["n"], 2)                       # НОВЫЙ блоб → гейт гоняем заново

    def test_unittest_gate_fail_no_restart(self):
        spawned = {"n": 0}
        r = o.maybe_self_update(blob_fn=lambda: "blob-new", code_gate=lambda: (True, "ok"),
                                tests_gate=lambda: (False, "FAILED (failures=1)"),
                                spawner=lambda: spawned.__setitem__("n", 1) or True)
        self.assertFalse(r)
        self.assertEqual(spawned["n"], 0)                     # красные тесты → остаёмся на старом

    def test_spawn_fail_old_keeps_running(self):
        r = o.maybe_self_update(blob_fn=lambda: "blob-new", code_gate=lambda: (True, "ok"),
                                tests_gate=lambda: (True, "ok"), spawner=lambda: False)
        self.assertFalse(r)                                   # эстафета НЕ передана → старый живёт

    def test_stopped_no_selfupdate(self):
        o._stopped = lambda: True
        r = o.maybe_self_update(blob_fn=lambda: "blob-new", code_gate=lambda: (True, "ok"),
                                tests_gate=lambda: (True, "ok"), spawner=lambda: True)
        self.assertFalse(r)                                   # рубильник → никакой эстафеты


class TestAutoUpdateBots(Base):
    """Авто-обновление userbot/moderbot после дев-задач (убираем ручное «обнови userbot»).
    Всё внешнее (git-дифф/гейт/рестарт) инъектируется — реальные процессы НЕ трогаем."""

    def _upd(self, text="тз: правка", head_before="old", changed=None, gate=(True, "ok"),
             restart=(True, [4321], "PID поднят, лог свежий"), commit="abc1234"):
        return o.maybe_update_bots(
            5, text, head_before,
            changed_fn=lambda hb: (changed if changed is not None else ["userbot_listen.py"]),
            gate_fn=lambda mods: gate,
            restart_fn=lambda kind: restart,
            head_fn=lambda: commit)

    def test_is_dev_task(self):
        self.assertTrue(o._is_dev_task("тз: сделай X"))
        self.assertTrue(o._is_dev_task("  ТЗ добавь фичу"))
        self.assertFalse(o._is_dev_task("обнови userbot"))
        self.assertFalse(o._is_dev_task(""))

    def test_non_dev_task_no_update(self):
        self.assertEqual(self._upd(text="просто вопрос"), "")

    def test_no_new_commits_no_update(self):
        self.assertEqual(self._upd(changed=[]), "")   # head не двигался / нет диффа

    def test_non_runtime_change_no_update(self):
        self.assertEqual(self._upd(changed=["README.md", "bot.py"]), "")

    def test_userbot_change_restart_ok(self):
        note = self._upd(changed=["userbot_listen.py"])
        self.assertIn("userbot обновлён до abc1234", note)
        self.assertIn("PID 4321", note)

    def test_gate_red_no_restart(self):
        called = {"n": 0}
        note = o.maybe_update_bots(
            5, "тз: правка", "old",
            changed_fn=lambda hb: ["suggest.py"],
            gate_fn=lambda mods: (False, "FAILED (failures=1)"),
            restart_fn=lambda kind: called.__setitem__("n", called["n"] + 1) or (True, [1], "x"),
            head_fn=lambda: "abc1234")
        self.assertIn("рестарт отложен: тесты красные", note)
        self.assertEqual(called["n"], 0)              # красный гейт → рестарт не звали

    def test_shared_suggest_restarts_both(self):
        kinds = []
        note = o.maybe_update_bots(
            5, "тз: правка suggest", "old",
            changed_fn=lambda hb: ["suggest.py"],     # общий рантайм → и userbot, и модербот
            gate_fn=lambda mods: (True, "ok"),
            restart_fn=lambda kind: kinds.append(kind) or (True, [7], "ok"),
            head_fn=lambda: "c0ffee1")
        self.assertEqual(sorted(kinds), ["moderbot", "userbot"])
        self.assertIn("userbot обновлён", note)
        self.assertIn("модербот обновлён", note)

    def test_moderbot_only_change(self):
        kinds = []
        o.maybe_update_bots(
            5, "тз: модерация", "old",
            changed_fn=lambda hb: ["moderation_core.py"],
            gate_fn=lambda mods: (True, "ok"),
            restart_fn=lambda kind: kinds.append(kind) or (True, [9], "ok"),
            head_fn=lambda: "d00d")
        self.assertEqual(kinds, ["moderbot"])         # userbot не трогаем — его рантайм не менялся

    def test_moderbot_reply_mode_note(self):
        note = o.maybe_update_bots(
            5, "тз: модерация", "old",
            changed_fn=lambda hb: ["moderation_bot.py"],
            gate_fn=lambda mods: (True, "ok"),
            restart_fn=lambda kind: (True, [], "модербот в reply-режиме (нет MODERBOT_TOKEN) — рестарт не требуется"),
            head_fn=lambda: "e1e1")
        self.assertIn("reply-режиме", note)
        self.assertNotIn("PID", note)                 # PID нет — не выдумываем

    def test_restart_failed_note(self):
        note = self._upd(changed=["pricing.py"], restart=(False, [], "процесс не поднялся"))
        self.assertIn("рестарт НЕ удался", note)

    def test_stop_flag_respected(self):
        o._stopped = lambda: True
        self.assertEqual(self._upd(), "")             # рубильник → не обновляем

    def test_classify_changed(self):
        ub, mb = o._classify_changed(["userbot_listen.py", "suggest.py", "moderation_core.py",
                                      "pricing.py", "README.md"])
        self.assertIn("userbot_listen.py", ub)
        self.assertIn("suggest.py", ub)               # suggest — общий, в обеих группах
        self.assertIn("suggest.py", mb)
        self.assertIn("moderation_core.py", mb)
        self.assertNotIn("moderation_core.py", ub)
        self.assertNotIn("README.md", ub + mb)

    def test_affected_test_modules(self):
        mods = o._affected_test_modules(["suggest.py", "pricing.py", "userbot_listen.py",
                                         "test_moderation.py"])
        self.assertIn("test_suggest", mods)           # есть на диске
        self.assertIn("test_pricing", mods)
        self.assertIn("test_moderation", mods)        # сам тест-файл
        self.assertNotIn("test_userbot_listen", mods) # такого файла нет → не включаем

    def test_gate_no_tests_passes(self):
        ok, msg = o._gate_test_modules([])            # нет затронутых тестов → зелено (config-правка)
        self.assertTrue(ok)


class TestDirtyTreeBlocksRestart(Base):
    """Класс 28.07: авто-рестарт не смеет увозить в бой НЕЗАКОММИЧЕННЫЙ код.

    Инцидент: self-update 1f5d10d→580d0d4 диффил от СВОЕГО запущенного коммита, поймал в диапазон
    старый 7a3c9b6 (suggest.py, pricing.py) и поднял userbot 16900 / moderbot 9592 вместе с
    незакоммиченным detectVehicleType из рабочего дерева — python грузит модули С ДИСКА, а не из
    коммита. Обошлось лишь потому, что функцию никто не вызывает.
    """

    def setUp(self):
        super().setUp()
        self.sent, self.cw = [], []
        o._notify = lambda t, *a, **k: self.sent.append(t)
        o._cowork = lambda t, *a, **k: self.cw.append(t)

    def _upd(self, changed, dirty, restarts):
        return o.maybe_update_bots(
            5, "тз: правка", "old",
            changed_fn=lambda hb: changed,
            gate_fn=lambda mods: (True, "ok"),
            restart_fn=lambda kind: restarts.append(kind) or (True, [4321], "ok"),
            head_fn=lambda: "abc1234",
            dirty_fn=lambda: dirty)

    # ── (1) чистое дерево → рестарт идёт, строка журнала обычная ──────────────────
    def test_clean_tree_restarts_and_note_is_normal(self):
        kinds = []
        note = self._upd(["suggest.py"], [], kinds)
        self.assertEqual(sorted(kinds), ["moderbot", "userbot"])
        self.assertIn("userbot обновлён до abc1234", note)
        self.assertNotIn("ОТМЕНЁН", note)
        self.assertEqual(self.sent, [])                 # чисто → владельца не дёргаем

    # ── (2) грязный файл, который несёт ЭТОТ процесс → рестарта НЕТ + сигнал ──────
    def test_dirty_file_of_this_process_blocks_restart(self):
        kinds = []
        note = self._upd(["suggest.py"], ["suggest.py"], kinds)
        self.assertEqual(kinds, [])                     # рестарта НЕ было
        self.assertIn("рестарт ОТМЕНЁН — грязное дерево: suggest.py", note)
        self.assertTrue(self.sent, "сигнал владельцу обязан уйти, а не тишина")
        joined = " ".join(self.sent)
        self.assertIn("suggest.py", joined)             # поимённо
        self.assertIn("abc1234", joined)                # с коммитом
        self.assertIn("userbot", joined)                # с именем процесса

    # ── (3) грязный файл, к этому процессу не относящийся → рестарт идёт ──────────
    def test_dirty_file_of_another_process_does_not_block(self):
        """booking_draft.py несёт ТОЛЬКО модербот — userbot рестартится как обычно."""
        kinds = []
        note = self._upd(["userbot_listen.py"], ["booking_draft.py"], kinds)
        self.assertEqual(kinds, ["userbot"])
        self.assertIn("userbot обновлён", note)
        self.assertEqual(self.sent, [])

    def test_dirty_non_runtime_file_does_not_block(self):
        """README/доки рантайма ботов не несут — карта _FILE_PROCESS_RULES их не знает."""
        kinds = []
        self._upd(["userbot_listen.py"], ["README.md", "docs/artifacts/x.md"], kinds)
        self.assertEqual(kinds, ["userbot"])

    # ── (4) сигнал ровно один раз, а не каждый цикл ───────────────────────────────
    def test_signal_sent_once_not_every_cycle(self):
        for _ in range(4):
            self._upd(["suggest.py"], ["suggest.py"], [])
        self.assertEqual(len(self.sent), 2, self.sent)   # по одному на userbot и модербот
        self.assertEqual(len(self.cw), 2, self.cw)

    def test_signal_repeats_when_dirty_set_changes(self):
        self._upd(["userbot_listen.py"], ["suggest.py"], [])
        n1 = len(self.sent)
        self._upd(["userbot_listen.py"], ["suggest.py", "pricing.py"], [])
        self.assertGreater(len(self.sent), n1, "состав грязного изменился — сказать обязаны заново")

    def test_clean_tree_resets_memory(self):
        self._upd(["userbot_listen.py"], ["suggest.py"], [])
        self._upd(["userbot_listen.py"], [], [])                # вычистили
        self._upd(["userbot_listen.py"], ["suggest.py"], [])    # снова грязно
        self.assertEqual(len(self.sent), 2, "после чистого дерева отказ снова заслуживает карточки")

    def test_git_silent_is_not_clean(self):
        """git не ответил → «не знаю» ≠ «чисто»: рестарт не идём."""
        kinds = []
        note = self._upd(["userbot_listen.py"], None, kinds)
        self.assertEqual(kinds, [])
        self.assertIn("ОТМЕНЁН", note)

    # ── тот самый путь инцидента: дети self-update ────────────────────────────────
    def test_selfupdate_children_blocked_and_journal_is_honest(self):
        kinds = []
        note = o._selfupdate_restart_children(
            "1f5d10d", "580d0d4",
            diff_fn=lambda a, b: ["suggest.py", "pricing.py"],
            restart_fn=lambda kind: kinds.append(kind) or (True, [1], "ok"),
            state={}, dirty_fn=lambda: ["suggest.py"])
        self.assertEqual(kinds, [])
        self.assertIn("ОТМЕНЁН", note)
        self.assertFalse([c for c in self.cw if c.startswith("авто-применил")],
                         "«авто-применил» не имеет права появиться: коммит в бой НЕ уехал")
        self.assertTrue([c for c in self.cw if "ОТМЕНЁН" in c and "suggest.py" in c],
                        "журнал обязан прямо сказать про грязное дерево и назвать файлы")

    def test_selfupdate_children_clean_writes_applied_line(self):
        kinds = []
        o._selfupdate_restart_children(
            "1f5d10d", "580d0d4",
            diff_fn=lambda a, b: ["suggest.py"],
            restart_fn=lambda kind: kinds.append(kind) or (True, [11], "ok"),
            state={}, dirty_fn=lambda: [])
        self.assertEqual(sorted(kinds), ["moderbot", "userbot"])
        self.assertTrue([c for c in self.cw if c.startswith("авто-применил 580d0d4")])

    # ── пункт 4: то же ограничение на само обновление демона ──────────────────────
    def _su(self, dirty, spawned):
        return o.maybe_self_update(
            blob_fn=lambda: "blob-new", head_fn=lambda: "bbb2222",
            code_gate=lambda: (True, "ok"), tests_gate=lambda: (True, "ok"),
            spawner=lambda: spawned.append(1) or True,
            children_fn=lambda *a, **k: "", dirty_fn=lambda: dirty)

    def test_daemon_self_update_blocked_by_its_own_dirty_file(self):
        saved = (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB)
        o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB = "blob-old", "aaa1111", None
        try:
            spawned = []
            self.assertFalse(self._su(["pc_orchestrator.py"], spawned))
            self.assertEqual(spawned, [], "новый демон не поднимался")
            self.assertIsNone(o._SU_REJECTED_BLOB,
                              "блоб не отвергаем: вычищенное дерево обязано разблокировать")
            self.assertIn("pc_orchestrator.py", " ".join(self.sent))
        finally:
            (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB) = saved

    def test_daemon_self_update_goes_when_its_files_are_clean(self):
        saved = (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB)
        o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB = "blob-old", "aaa1111", None
        try:
            spawned = []
            self.assertTrue(self._su(["suggest.py"], spawned))   # чужой файл демону не помеха
            self.assertEqual(spawned, [1])
        finally:
            (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB) = saved

    def test_dirty_names_arrive_whole(self):
        """РЕГРЕСС: первая версия читала `status --porcelain` и резала имя по фиксированной колонке,
        а _git_call делает .strip() ВСЕГО вывода — первая строка теряла ведущий пробел и имя
        приезжало «c_orchestrator.py», мимо карты процессов: гард молча открывался. Поймано живой
        проверкой на реальном дереве."""
        seen = {}

        def runner(args, timeout=None):
            seen["args"] = args
            return (0, "pc_orchestrator.py\ntest_pc_orchestrator.py", "")

        real = self._save_dirty          # Base подменил модульный _dirty_tracked заглушкой «чисто»
        self.assertEqual(real(runner=runner), ["pc_orchestrator.py", "test_pc_orchestrator.py"])
        self.assertEqual(seen["args"], ["diff", "--name-only", "HEAD"])   # без колонок статуса
        self.assertEqual(o._dirty_for_proc("orchestrator", lambda: real(runner=runner)),
                         ["pc_orchestrator.py"])

    def test_git_failure_is_unknown_not_clean(self):
        real = self._save_dirty
        self.assertIsNone(real(runner=lambda a, timeout=None: (1, "", "fatal")))
        self.assertIsNone(real(runner=lambda a, timeout=None: None))

    def test_daemon_runtime_covers_its_top_imports(self):
        """Демон грузит не только свой модуль: грязный gate_selective уедет в бой так же."""
        for f in ("pc_orchestrator.py", "gate_selective.py", "task_metrics.py",
                  "lesson_router.py", "log_setup.py"):
            self.assertEqual(o._dirty_for_proc("orchestrator", lambda: [f]), [f], f)


class TestSelectiveGateWiring(Base):
    """Селективный гейт в maybe_update_bots (порт VPS GATE_STEP/SINGLE_SELECTIVE). Флаги читаются
    из os.environ — ставим/снимаем на время теста; gate_fn перехватывает список гоняемых модулей."""

    def _set_flag(self, name, val):
        prev = os.environ.get(name)
        if val is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = val
        self.addCleanup(lambda: (os.environ.__setitem__(name, prev) if prev is not None
                                 else os.environ.pop(name, None)))

    def _run(self, text, changed):
        """→ (captured_mods_первого_вызова_gate, note). Рестарт всегда зелёный, гейт зелёный."""
        captured = []
        note = o.maybe_update_bots(
            5, text, "old",
            changed_fn=lambda hb: changed,
            gate_fn=lambda mods: (captured.append(list(mods or [])), (True, "ok"))[1],
            restart_fn=lambda kind: (True, [1], "ok"),
            head_fn=lambda: "c0ffee1")
        return (captured[0] if captured else None), note

    def test_single_flag_off_is_legacy_affected(self):
        # дефолт (флаг off): одиночка → прежний путь _affected_test_modules; userbot_listen без теста → []
        self._set_flag("GATE_SINGLE_SELECTIVE", None)
        mods, _ = self._run("тз: правка", ["userbot_listen.py"])
        self.assertEqual(mods, [])                     # прежнее поведение: пусто → гейт без тестов

    def test_single_selective_failsafe_full(self):
        # флаг on + правка без сопоставимого теста → fail-safe в ПОЛНЫЙ гейт (не «пусто без гейта»)
        self._set_flag("GATE_SINGLE_SELECTIVE", "1")
        mods, _ = self._run("тз: правка", ["userbot_listen.py"])
        self.assertIn("test_pc_orchestrator", mods)    # полный список репо
        self.assertGreater(len(mods), 5)

    def test_single_selective_affected(self):
        # флаг on + правка с тестом → только затронутый модуль
        self._set_flag("GATE_SINGLE_SELECTIVE", "1")
        mods, _ = self._run("тз: правка suggest", ["suggest.py"])
        self.assertEqual(mods, ["test_suggest"])

    def test_step_flag_off_no_apply(self):
        # дефолт: ШАГ цепи бот НЕ авто-применяет (прежнее поведение) → пустая нота, гейт не звался
        self._set_flag("GATE_STEP_SELECTIVE", None)
        mods, note = self._run("[шаг 2/5 родитель 92] правь suggest", ["suggest.py"])
        self.assertIsNone(mods)                        # gate_fn не вызывался
        self.assertEqual(note, "")

    def test_step_selective_intermediate(self):
        # флаг on + промежуточный шаг → селектив (затронутый модуль), рестарт состоялся
        self._set_flag("GATE_STEP_SELECTIVE", "1")
        mods, note = self._run("[шаг 2/5 родитель 92] правь suggest", ["suggest.py"])
        self.assertEqual(mods, ["test_suggest"])
        self.assertIn("обновлён", note)

    def test_step_selective_final_full(self):
        # флаг on + ФИНАЛЬНЫЙ шаг → полный гейт (неубираем), даже если затронут лишь один тест
        self._set_flag("GATE_STEP_SELECTIVE", "1")
        mods, _ = self._run("[шаг 5/5 родитель 92] финал suggest", ["suggest.py"])
        self.assertIn("test_pc_orchestrator", mods)
        self.assertIn("test_suggest", mods)
        self.assertGreater(len(mods), 5)

    def test_step_flag_does_not_enable_single_apply_semantics(self):
        # GATE_STEP_SELECTIVE не влияет на одиночку: одиночка при step=1/single=0 → прежний путь
        self._set_flag("GATE_STEP_SELECTIVE", "1")
        self._set_flag("GATE_SINGLE_SELECTIVE", None)
        mods, _ = self._run("тз: правка suggest", ["suggest.py"])
        self.assertEqual(mods, ["test_suggest"])       # legacy affected (совпадает с селективом здесь)


class TestAutoUpdateInProcessNew(Base):
    """Интеграция: done дев-задачи → суффикс авто-обновления попадает в результат/карточку."""

    def setUp(self):
        super().setUp()
        self._patch = (o._git_out, o._changed_files_since, o._gate_test_modules,
                       o._restart_via_pc_agent, o._head_commit)
        o._git_out = lambda args: "headsha_before"
        o._changed_files_since = lambda hb: ["userbot_listen.py"]
        o._gate_test_modules = lambda mods: (True, "ok")
        o._restart_via_pc_agent = lambda kind: (True, [5150], "PID поднят, лог свежий")
        o._head_commit = lambda: "beef123"

    def tearDown(self):
        (o._git_out, o._changed_files_since, o._gate_test_modules,
         o._restart_via_pc_agent, o._head_commit) = self._patch
        super().tearDown()

    def test_dev_task_done_appends_update_note(self):
        tid = self.fb.add(status="new", task_text="тз: правка userbot")
        self._claude(0, "сделал\nRESULT: закоммитил и запушил")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertIn("userbot обновлён до beef123, PID 5150", self.fb.tasks[tid]["result"])

    def test_non_dev_task_no_note(self):
        tid = self.fb.add(status="new", task_text="почини баг в логах")
        self._claude(0, "сделал\nRESULT: готово")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertNotIn("обновлён до", self.fb.tasks[tid]["result"])   # не дев-задача → без рестарта

    def test_failed_task_no_update(self):
        called = {"n": 0}
        o._restart_via_pc_agent = lambda kind: called.__setitem__("n", called["n"] + 1) or (True, [1], "x")
        tid = self.fb.add(status="new", task_text="тз: правка")
        self._claude(0, "болтал без итога")        # нет RESULT: → failed
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertEqual(called["n"], 0)           # провал задачи → бот не трогаем


class TestTaskSelfheal(Base):
    """САМОПОЧИНКА ОДИНОЧНЫХ (STEP_SELFHEAL): думатель/кондуктор и claude замоканы — сеть/боевое
    не трогаем. Проверяем: (а) починимый → retry → 1 перерождение с маркером → перерождение done;
    (б) непочинимый → 1 попытка (маркер) → повтор = терминальный failed, БЕЗ третьего перерождения;
    (в) плановый рестарт (4 признака) → done, думатель НЕ зван; (г) флаг off → голый failed;
    плюс fail-safe: думатель вернул мусор/таймаут/очередь отказала → голый failed."""

    def setUp(self):
        super().setUp()
        o._selfheal_on = lambda: True                 # флаг ВКЛЮЧЁН для этого класса
        self.thinker_calls = {"n": 0}
        self._save_thinker = o._thinker_exec

    def tearDown(self):
        o._thinker_exec = self._save_thinker
        super().tearDown()

    def _thinker(self, reply):
        """Замокать думателя (кондуктор): вернуть заданный сырой текст, считая вызовы."""
        def fake(prompt, timeout, tag):
            self.thinker_calls["n"] += 1
            return reply
        o._thinker_exec = fake

    # ---- (а) починимый провал → retry → одно перерождение с маркером → перерождение done -------
    def test_a_fixable_retry_reborn_then_done(self):
        tid = self.fb.add(status="new", task_text="тз: почини путь к файлу")
        self._thinker('{"verdict":"retry","fixed_task":"почини путь: файл лежит в src/, не в корне","reason":"неверный путь"}')

        def fake(prompt, timeout, cwd, env):
            if "[самопочинка задачи" in prompt:       # перерождённая задача — успех
                return (0, "починил\nRESULT: путь исправлен, закоммитил", "")
            return (0, "болтал без итога", "")         # исходная — нет RESULT → insufficient_output failed
        o.run_claude = fake

        o.process_new()                                # 1-й цикл: исходная падает → retry → перерождение
        self.assertEqual(self.fb.tasks[tid]["status"], "done")          # исходная закрыта как done (перерождена)
        self.assertIn("перерожден", self.fb.tasks[tid]["result"].lower())
        reborn = [t for t in self.fb.tasks.values() if t["id"] != tid]
        self.assertEqual(len(reborn), 1)                                # РОВНО одно перерождение
        self.assertTrue(reborn[0]["task_text"].startswith(f"[самопочинка задачи {tid}, попытка 1]"))
        self.assertEqual(reborn[0]["status"], "new")

        o.process_new()                                # 2-й цикл: перерождение исполняется → done
        self.assertEqual(reborn[0]["status"], "done")
        self.assertIn("путь исправлен", reborn[0]["result"])
        self.assertEqual(self.thinker_calls["n"], 1)                    # думатель звался ровно раз (для исходной)

    # ---- (б) непочинимый → 1 попытка (маркер) → повтор = терминальный failed, БЕЗ 3-го ---------
    def test_b_unfixable_marker_stops_loop(self):
        tid = self.fb.add(status="new", task_text="тз: невыполнимое")
        self._thinker('{"verdict":"retry","fixed_task":"попробуй иначе","reason":"кривая команда"}')
        o.run_claude = lambda p, t, c, e: (0, "снова болтал без итога", "")   # падает ВСЕГДА

        o.process_new()                                # исходная → retry → перерождение
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        reborn = next(t for t in self.fb.tasks.values() if t["id"] != tid)
        self.assertEqual(len(self.fb.tasks), 2)

        o.process_new()                                # перерождение падает ПОВТОРНО → терминальный failed
        self.assertEqual(reborn["status"], "failed")
        self.assertIn("самопочинка не помогла", reborn["result"])
        self.assertEqual(len(self.fb.tasks), 2)                         # ТРЕТЬЕГО перерождения НЕТ
        self.assertEqual(self.thinker_calls["n"], 1)                    # на маркированной думателя НЕ звали

    def test_b_halt_terminal_failed_no_reborn(self):
        tid = self.fb.add(status="new", task_text="тз: нужен человек")
        self._thinker('{"verdict":"halt","fixed_task":"","reason":"нужен доступ к секрету"}')
        o.run_claude = lambda p, t, c, e: (0, "болтал без итога", "")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")        # halt → сразу терминальный
        self.assertIn("halt", self.fb.tasks[tid]["result"].lower())
        self.assertEqual(len(self.fb.tasks), 1)                         # перерождения НЕТ

    # ---- (в) плановый self-update-рестарт (4 признака) → done, думатель НЕ зван -----------------
    def test_c_planned_restart_done_no_thinker(self):
        self._thinker("НЕ ДОЛЖЕН ВЫЗЫВАТЬСЯ")
        self._save_blob = (o.RUNNING_BLOB, o._blob_hash, o._stopped)
        try:
            o.RUNNING_BLOB = "blob-old"
            o._blob_hash = lambda: "blob-new"          # HEAD демона изменился (self-update назрел)
            o._stopped = lambda: True                  # рубильник взведён (окно рестарта)
            with mock.patch.object(o, "resolve_claude", lambda: sys.executable):
                o.run_claude = lambda p, t, c, e: (143, "", "SIGTERM")   # rc!=0, оборван
                status, result = o.run_task(7, "тз: самомодификация pc_orchestrator.py")
            self.assertEqual(status, "done")           # плановый рестарт ≠ падение
            self.assertIn("плановым рестартом", result)
            self.assertEqual(self.thinker_calls["n"], 0)                # думатель НЕ зван
        finally:
            (o.RUNNING_BLOB, o._blob_hash, o._stopped) = self._save_blob

    def test_c_planned_restart_needs_all_4_signs(self):
        # не хватает признака (текст НЕ про демон) → честный failed, не done
        save = (o.RUNNING_BLOB, o._blob_hash)
        try:
            o.RUNNING_BLOB = "blob-old"
            o._blob_hash = lambda: "blob-new"
            o._stopped = lambda: True
            with mock.patch.object(o, "resolve_claude", lambda: sys.executable):
                o.run_claude = lambda p, t, c, e: (143, "", "boom")
                status, _ = o.run_task(8, "обычная задача, демон не при чём")
            self.assertEqual(status, "failed")         # 3 из 4 → честный failed (fail-safe)
        finally:
            (o.RUNNING_BLOB, o._blob_hash) = save

    # ---- (г) STEP_SELFHEAL=0 → голый failed, думатель не зван (байт-в-байт прежнее) -------------
    def test_g_flag_off_bare_failed(self):
        o._selfheal_on = lambda: False
        tid = self.fb.add(status="new", task_text="тз: упадёт")
        self._thinker("НЕ ДОЛЖЕН ВЫЗЫВАТЬСЯ")
        o.run_claude = lambda p, t, c, e: (0, "болтал без итога", "")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("insufficient_output", self.fb.tasks[tid]["result"])   # прежняя причина
        self.assertEqual(len(self.fb.tasks), 1)                              # без перерождения
        self.assertEqual(self.thinker_calls["n"], 0)

    # ---- fail-safe: любой сбой думателя = прежний ГОЛЫЙ failed (не хуже) ------------------------
    def test_failsafe_garbage_reply_bare_failed(self):
        tid = self.fb.add(status="new", task_text="тз: упадёт")
        self._thinker("не json, просто болтовня без объекта")            # мусор → parse None
        o.run_claude = lambda p, t, c, e: (0, "болтал без итога", "")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("insufficient_output", self.fb.tasks[tid]["result"])
        self.assertEqual(len(self.fb.tasks), 1)                              # голый failed, без перерождения

    def test_failsafe_thinker_timeout_bare_failed(self):
        tid = self.fb.add(status="new", task_text="тз: упадёт")
        self._thinker(None)                                             # таймаут/сбой → None
        o.run_claude = lambda p, t, c, e: (0, "болтал без итога", "")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertEqual(len(self.fb.tasks), 1)

    def test_failsafe_enqueue_rejected_bare_failed(self):
        tid = self.fb.add(status="new", task_text="тз: упадёт")
        self._thinker('{"verdict":"retry","fixed_task":"почини","reason":"путь"}')
        self.fb.enqueue_task = lambda frm, text, lane="pc": {"ok": False, "error": "queue_down"}
        o.run_claude = lambda p, t, c, e: (0, "болтал без итога", "")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")             # очередь отказала → голый failed
        self.assertIn("insufficient_output", self.fb.tasks[tid]["result"])
        self.assertEqual(len(self.fb.tasks), 1)

    # ---- парсер думателя (строгий JSON) --------------------------------------------------------
    def test_parse_thinker_json_strict(self):
        self.assertIsNone(o._parse_thinker_json("нет объекта"))
        self.assertIsNone(o._parse_thinker_json('{"verdict":"maybe"}'))       # verdict не retry/halt
        v = o._parse_thinker_json('шум {"verdict":"retry","fixed_task":"X","reason":"Y"} хвост')
        self.assertEqual(v["verdict"], "retry")
        self.assertEqual(v["fixed_task"], "X")

    def test_thinker_exec_failsafe_on_exception(self):
        # реальный _thinker_exec: любой сбой subprocess → None (fail-safe), claude не найден → None
        with mock.patch.object(o, "resolve_claude", lambda: None):
            self.assertIsNone(o._thinker_exec("p", 5, "t"))
        with mock.patch.object(o, "resolve_claude", lambda: sys.executable), \
                mock.patch.object(o.subprocess, "run", side_effect=RuntimeError("boom")):
            self.assertIsNone(o._thinker_exec("p", 5, "t"))

    def test_thinker_uses_own_thinker_models_not_suggest(self):
        # думатель формирует CLI-вызов из СВОЕЙ пары THINKER_MODEL/THINKER_FALLBACK,
        # НЕ из SUGGEST_MODEL клиентского suggest (=sonnet).
        captured = {}

        class _P:
            returncode = 0
            stdout = '{"result": "{\\"verdict\\":\\"halt\\",\\"fixed_task\\":\\"\\",\\"reason\\":\\"x\\"}"}'
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cmd"] = list(cmd)
            return _P()

        with mock.patch.object(o, "resolve_claude", lambda: sys.executable), \
                mock.patch.object(o, "THINKER_MODEL", "model-main-X"), \
                mock.patch.object(o, "THINKER_FALLBACK", "model-fb-Y"), \
                mock.patch.object(o.subprocess, "run", fake_run):
            o._thinker_exec("prompt", 5, "t")
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "model-main-X")       # своя голова
        self.assertEqual(cmd[cmd.index("--fallback-model") + 1], "model-fb-Y")  # свой фолбэк
        self.assertNotIn("sonnet", cmd)                                       # НЕ SUGGEST_MODEL

    def test_thinker_model_defaults_and_no_suggest_coupling(self):
        # думатель — своя пара (НЕ SUGGEST_MODEL), старое имя убрано, модель — ПОЛНЫЙ id.
        # Эффективное значение читаем во ВЛОЖЕННОМ процессе с ЧИСТЫМ env (без ambient
        # THINKER_MODEL/FALLBACK): гейт self-update наследует env демона, где до рестарта лежит
        # ПРЕЖНИЙ короткий алиас — само это наследование не должно ронять тест (иначе дедлок:
        # гейт не проходит → демон не перечитает .env → в env вечно старый алиас). ПОЛНЫЙ id
        # обязателен: любой короткий алиас → claude -p отвечает 404 (родитель #194).
        self.assertFalse(hasattr(o, "THINKER_MODEL_FALLBACK"))               # переименовано → THINKER_FALLBACK
        import subprocess
        env = {k: v for k, v in os.environ.items()
               if k not in ("THINKER_MODEL", "THINKER_FALLBACK")}
        code = ("import pc_orchestrator as o;"
                "print('T=' + o.THINKER_MODEL);"
                "print('F=' + o.THINKER_FALLBACK);"
                "print('S=' + o.SUGGEST_MODEL)")
        p = subprocess.run([sys.executable, "-c", code], cwd=o.REPO, env=env,
                           capture_output=True, text=True, timeout=60)
        vals = {ln[0]: ln[2:] for ln in p.stdout.splitlines()
                if len(ln) > 2 and ln[1] == "=" and ln[0] in "TFS"}
        diag = (p.stdout or "") + (p.stderr or "")
        self.assertTrue(vals.get("T", "").startswith("claude-"), diag)       # ПОЛНЫЙ id, не короткий алиас
        self.assertTrue(vals.get("F", "").startswith("claude-"), diag)
        self.assertNotIn(vals.get("T"), o._MODEL_ALIAS_FULL, diag)           # уже нормализован, не ключ-алиас #194
        self.assertNotEqual(vals.get("T"), vals.get("S"))                    # НЕ завязан на SUGGEST_MODEL

    def test_thinker_model_normalizes_stale_short_alias_in_env(self):
        # ГОЛДЕН реального провала 205–208 (не идеализированный clean-env, а ГРЯЗНЫЙ env как у живого
        # демона): демон УНАСЛЕДОВАЛ короткий THINKER_MODEL / THINKER_FALLBACK=opus-4.8 в
        # os.environ (ancestor стартовал со старым .env; load_dotenv override=False не перезаписал) →
        # claude -p с коротким алиасом → HTTP 404 «model may not exist» → exit=1 → планировщик молча
        # падал. Модуль ОБЯЗАН нормализовать короткий алиас → ПОЛНЫЙ id НА СТАРТЕ, иммунно к
        # застрявшему env (иначе self-update не лечит: новый демон наследует тот же короткий env).
        # 30.07.2026: имена снятой головы ведут на claude-opus-5 — застрявшее окружение НЕ вернёт
        # её чёрным ходом мимо .env (решение владельца «убрать снятую голову из работы»).
        import subprocess
        env = dict(os.environ)
        env["THINKER_MODEL"] = "fable-5"      # ровно то, что PEB показал в живом PID 1120
        env["THINKER_FALLBACK"] = "opus-4.8"
        code = ("import pc_orchestrator as m;"
                "print('T=' + m.THINKER_MODEL);"
                "print('F=' + m.THINKER_FALLBACK)")
        p = subprocess.run([sys.executable, "-c", code], cwd=o.REPO, env=env,
                           capture_output=True, text=True, timeout=60)
        vals = {ln[0]: ln[2:] for ln in p.stdout.splitlines()
                if len(ln) > 2 and ln[1] == "=" and ln[0] in "TF"}
        diag = (p.stdout or "") + (p.stderr or "")
        self.assertEqual(vals.get("T"), "claude-opus-5", diag)               # короткий env → ПОЛНЫЙ id снятия
        self.assertEqual(vals.get("F"), "claude-opus-4-8", diag)             # короткий env → ПОЛНЫЙ id
        # направления нормализации (unit)
        self.assertEqual(o._norm_model_id("fable-5"), "claude-opus-5")           # снятая голова → Opus 5
        self.assertEqual(o._norm_model_id("claude-fable-5"), "claude-opus-5")    # и ПОЛНОЕ имя снятой — тоже
        self.assertEqual(o._norm_model_id("opus-4.8"), "claude-opus-4-8")
        self.assertEqual(o._norm_model_id("claude-opus-5"), "claude-opus-5")     # полный id — как есть
        self.assertEqual(o._norm_model_id("sonnet"), "sonnet")                   # неизвестный алиас — как есть


class TestThinkerEffort(unittest.TestCase):
    """ГОЛДЕН глубины мышления на пути, который .claude/settings.json НЕ читает.

    Думатель намеренно живёт в нейтральном cwd (tempdir), чтобы не тянуть hooks/pretool_guard
    репо. Вместе с ними он терял и `effortLevel` — при доктрине «каждая задача ultrathink» это
    тихая деградация ровно там, где рассуждение и нужно. Значения обязаны браться из ТОГО ЖЕ
    settings.json, а не дублироваться константой (иначе два источника правды разъедутся)."""

    def test_reads_effort_and_budget_from_repo_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "settings.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"effortLevel": "xhigh", "env": {"MAX_THINKING_TOKENS": "31999"}}, f)
            self.assertEqual(o.repo_thinking_settings(p), ("xhigh", "31999"))

    def test_live_repo_settings_are_ultrathink(self):
        """Боевой файл репо — не абстракция: доктрина должна лежать именно в нём."""
        eff, mtt = o.repo_thinking_settings(o.REPO_SETTINGS)
        self.assertEqual(eff, "xhigh")
        self.assertTrue(int(mtt) >= 31999, mtt)

    def test_broken_or_missing_settings_fall_back_to_doctrine(self):
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "нет.json")
            self.assertEqual(o.repo_thinking_settings(missing), (o.DEFAULT_EFFORT, o.DEFAULT_MTT))
            broken = os.path.join(d, "battle.json")
            with open(broken, "w", encoding="utf-8") as f:
                f.write("{ это не json")
            self.assertEqual(o.repo_thinking_settings(broken), (o.DEFAULT_EFFORT, o.DEFAULT_MTT))

    def test_thinker_command_carries_effort_and_budget(self):
        captured = {}

        class _P:
            returncode = 0
            stdout = '{"result":"ok"}'
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env") or {}
            return _P()

        with mock.patch.object(o, "resolve_claude", return_value=r"C:\x\claude.exe"), \
             mock.patch.object(o, "_claude_budget_gate", return_value=(True, "")), \
             mock.patch.object(o.subprocess, "run", side_effect=fake_run):
            self.assertEqual(o._thinker_exec("думай", 30, "тест"), "ok")
        cmd = captured["cmd"]
        self.assertIn("--effort", cmd)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "xhigh")
        self.assertEqual(captured["env"].get("MAX_THINKING_TOKENS"), "31999")
        self.assertNotIn("ANTHROPIC_API_KEY", captured["env"])   # регресс: подписка, не платный ключ


class TestClientWatchdog(unittest.TestCase):
    """Контур-вотчдог (разбор #128, часть 3): finder/raiser/now/state инъектируются —
    реальных процессов/schtasks НЕ трогаем. Проверяем: живой не поднимается; мёртвый →
    подъём + NOTE; анти-флап (кулдаун); 3 смерти подряд → стоп + громкий NOTE; skip; рубильник."""

    def setUp(self):
        self._save = (o._cowork, o._notify, o._notify_critical, o._stopped)
        self.notes, self.pushes, self.crit = [], [], []
        o._cowork = lambda line: self.notes.append(line)
        o._notify = lambda text: self.pushes.append(text)
        o._notify_critical = lambda text: self.crit.append(text)   # критические инциденты → инбокс 1160
        o._stopped = lambda: False

    def tearDown(self):
        (o._cowork, o._notify, o._notify_critical, o._stopped) = self._save

    def _spec(self, name, alive_seq, raiser_ok=True, skip=False):
        """Спека процесса: alive_seq — очередь ответов finder (True/False по тикам).
        После исчерпания повторяем последнее значение (лишние тики не роняют finder)."""
        it = iter(alive_seq)
        last = {"v": alive_seq[-1] if alive_seq else False}
        raises = []

        def finder():
            try:
                last["v"] = next(it)
            except StopIteration:
                pass
            return [123] if last["v"] else []
        sp = {"name": name,
              "finder": finder,
              "raiser": lambda: (raises.append(1) or (raiser_ok, "detail")),
              "logfile": os.path.join(o.REPO, "nope.log"),
              "skip": lambda: skip}
        sp["_raises"] = raises
        return sp

    def test_alive_not_raised(self):
        st = {}
        sp = self._spec("userbot", [True])
        out = o.client_watchdog_tick(now=1000, specs=[sp], state=st, cooldown=900, max_deaths=3)
        self.assertEqual(out["userbot"], "alive")
        self.assertEqual(sp["_raises"], [])            # живого не поднимаем
        self.assertEqual(st["userbot"]["deaths"], 0)

    def test_dead_raised_with_note(self):
        st = {}
        sp = self._spec("userbot", [False])
        out = o.client_watchdog_tick(now=1000, specs=[sp], state=st, cooldown=900, max_deaths=3)
        self.assertEqual(out["userbot"], "raise")
        self.assertEqual(len(sp["_raises"]), 1)        # мёртвого подняли
        self.assertTrue(any("вотчдог поднял userbot" in n for n in self.notes))
        self.assertEqual(st["userbot"]["deaths"], 1)

    def test_anti_flap_cooldown(self):
        st = {}
        sp = self._spec("userbot", [False, False])
        o.client_watchdog_tick(now=1000, specs=[sp], state=st, cooldown=900, max_deaths=3)   # подъём в t=1000
        o.client_watchdog_tick(now=1100, specs=[sp], state=st, cooldown=900, max_deaths=3)   # t=1100 (<15мин)
        self.assertEqual(len(sp["_raises"]), 1)        # второй раз НЕ поднимали (кулдаун)

    def test_three_deaths_halt_loud(self):
        st = {}
        sp = self._spec("moderation_bot", [False, False, False])
        # три тика вне кулдауна (cooldown=0) → death1 raise, death2 raise, death3 halt
        o.client_watchdog_tick(now=1, specs=[sp], state=st, cooldown=0, max_deaths=3)
        o.client_watchdog_tick(now=2, specs=[sp], state=st, cooldown=0, max_deaths=3)
        out = o.client_watchdog_tick(now=3, specs=[sp], state=st, cooldown=0, max_deaths=3)
        self.assertEqual(out["moderation_bot"], "halt_now")
        self.assertEqual(len(sp["_raises"]), 2)        # подняли дважды, на 3-й — стоп
        self.assertTrue(st["moderation_bot"]["halted"])
        self.assertTrue(any("нужен разбор" in p for p in self.crit))     # критич. пуш → инбокс 1160 (НЕ личка)
        self.assertEqual(self.pushes, [])                                 # 3-смерти-halt в личку НЕ дублируем
        # после halt дальше молчим
        out2 = o.client_watchdog_tick(now=4, specs=[sp], state=st, cooldown=0, max_deaths=3)
        self.assertEqual(out2["moderation_bot"], "halted")
        self.assertEqual(len(sp["_raises"]), 2)

    def test_recovery_resets_deaths(self):
        st = {}
        sp = self._spec("userbot", [False, True])
        o.client_watchdog_tick(now=1, specs=[sp], state=st, cooldown=0, max_deaths=3)   # death1
        o.client_watchdog_tick(now=2, specs=[sp], state=st, cooldown=0, max_deaths=3)   # ожил
        self.assertEqual(st["userbot"]["deaths"], 0)   # ожил → счётчик обнулён

    def test_skip_no_token_moderbot(self):
        st = {}
        sp = self._spec("moderation_bot", [False], skip=True)   # skip=True (нет MODERBOT_TOKEN)
        out = o.client_watchdog_tick(now=1, specs=[sp], state=st, cooldown=0, max_deaths=3)
        self.assertEqual(out["moderation_bot"], "skip")
        self.assertEqual(len(sp["_raises"]), 0)        # штатный простой ≠ смерть
        self.assertEqual(st["moderation_bot"]["deaths"], 0)

    def test_stop_flag_skips_contour(self):
        o._stopped = lambda: True
        sp = self._spec("userbot", [False])
        out = o.client_watchdog_tick(now=1, specs=[sp], state={}, cooldown=0, max_deaths=3)
        self.assertEqual(out, {"_": "stopped"})
        self.assertEqual(len(sp["_raises"]), 0)        # рубильник → контур не трогаем

    def test_raiser_failure_pushes(self):
        sp = self._spec("pc_agent", [False], raiser_ok=False)
        o.client_watchdog_tick(now=1, specs=[sp], state={}, cooldown=0, max_deaths=3)
        self.assertTrue(any("не смог поднять pc_agent" in p for p in self.pushes))

    def test_throttle_maybe_client_watchdog(self):
        o._client_watch_last_run = 0.0
        calls = {"n": 0}
        save = o.client_watchdog_tick
        try:
            o.client_watchdog_tick = lambda now=None: (calls.__setitem__("n", calls["n"] + 1), {"ok": True})[1]
            self.assertIsNotNone(o.maybe_client_watchdog(now=1000))   # первый прогон
            self.assertIsNone(o.maybe_client_watchdog(now=1100))      # <5мин → пропуск
            self.assertIsNotNone(o.maybe_client_watchdog(now=1000 + o.CLIENT_WATCH_SEC + 1))
            self.assertEqual(calls["n"], 2)
        finally:
            o.client_watchdog_tick = save
            o._client_watch_last_run = 0.0


class TestClientWatchSnapshot(unittest.TestCase):
    """Снимок надзора на диск (_persist_client_watch) → его читает status pc_agent (отдельный
    процесс). Пишем реальных детей, спец-счётчики (__blind__) пропускаем; атомарно; сбой не роняет."""

    def test_persist_writes_children_skips_blind(self):
        import json
        st = {"userbot": {"deaths": 2, "halted": False, "last_raise": 1234.0},
              "moderation_bot": {"deaths": 0, "halted": True, "last_raise": 5.0},
              "__blind__": 3}                              # спец-счётчик (int) — НЕ ребёнок
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "snap.json")
            o._persist_client_watch(st, now=9999.0, path=p)
            blob = json.loads(Path(p).read_text(encoding="utf-8"))
        self.assertEqual(blob["ts"], 9999.0)
        self.assertEqual(blob["cooldown"], o.CLIENT_COOLDOWN_SEC)
        self.assertEqual(blob["max_deaths"], o.CLIENT_MAX_DEATHS)
        self.assertIn("userbot", blob["children"])
        self.assertIn("moderation_bot", blob["children"])
        self.assertNotIn("__blind__", blob["children"])   # int-счётчик отфильтрован
        self.assertEqual(blob["children"]["userbot"]["deaths"], 2)
        self.assertTrue(blob["children"]["moderation_bot"]["halted"])

    def test_persist_write_failure_is_silent(self):
        # путь в несуществующей директории → os.replace бросит; функция НЕ должна падать
        bad = os.path.join(tempfile.gettempdir(), "no_such_dir_zzz", "snap.json")
        try:
            o._persist_client_watch({"userbot": {"deaths": 0}}, now=1.0, path=bad)
        except Exception as e:
            self.fail(f"_persist_client_watch не должен пробрасывать сбой записи: {e}")

    def test_maybe_watchdog_persists_after_tick(self):
        o._client_watch_last_run = 0.0
        calls = []
        save_tick, save_persist = o.client_watchdog_tick, o._persist_client_watch
        try:
            o.client_watchdog_tick = lambda now=None: {"userbot": "alive"}
            o._persist_client_watch = lambda state, now, **k: calls.append(now)
            self.assertIsNotNone(o.maybe_client_watchdog(now=10_000.0))   # прогон → снимок записан
            self.assertIsNone(o.maybe_client_watchdog(now=10_050.0))      # троттл → ни тика, ни снимка
            self.assertEqual(calls, [10_000.0])
        finally:
            o.client_watchdog_tick, o._persist_client_watch = save_tick, save_persist
            o._client_watch_last_run = 0.0


class TestWatchdogClassFix(unittest.TestCase):
    """Фикс КЛАССА вотчдога (вердикт #171): «не смог проверить» ≠ «мёртв».
    Всё замокано (finder/логи/часы/очередь NOTE) — реальные процессы/CIM/schtasks НЕ трогаем."""

    NOW = 2_000_000_000   # большой wall-clock: os.utime мтаймов лога считается относительно него

    def setUp(self):
        self._save = (o._cowork, o._notify, o._notify_critical, o._stopped)
        self.notes, self.pushes, self.crit = [], [], []
        o._cowork = lambda line: self.notes.append(line)
        o._notify = lambda text: self.pushes.append(text)
        o._notify_critical = lambda text: self.crit.append(text)   # критические инциденты → инбокс 1160
        o._stopped = lambda: False
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        (o._cowork, o._notify, o._notify_critical, o._stopped) = self._save

    def _spec(self, name, finder, logfile=None, skip=False):
        raises = []
        sp = {"name": name, "finder": finder,
              "raiser": lambda: (raises.append(1) or (True, "detail")),
              "logfile": logfile or os.path.join(self.tmp, "nope.log"),
              "skip": lambda: skip}
        sp["_raises"] = raises
        return sp

    def _logfile(self, name, age_sec):
        """Лог с mtime = NOW - age_sec (относительно фейкового NOW)."""
        p = os.path.join(self.tmp, name)
        open(p, "w").close()
        t = self.NOW - age_sec
        os.utime(p, (t, t))
        return p

    # (а) CIM timeout/ERROR у finder → SKIP + WARN, БЕЗ рестарта
    def test_a_finder_error_is_skip_not_death(self):
        st = {}
        sp = self._spec("userbot", finder=lambda: None)          # None = «не смог проверить»
        out = o.client_watchdog_tick(now=self.NOW, specs=[sp], state=st,
                                     cooldown=0, max_deaths=3, grace_until=0, log_stale=120, blind_alarm=3)
        self.assertEqual(out["userbot"], "check_failed")
        self.assertEqual(sp["_raises"], [])                       # НЕ рестартили
        self.assertNotIn("userbot", st)                          # состояние смерти не трогали
        self.assertEqual(st.get("__blind__"), 1)                 # цикл слеп

    def test_a_finder_exception_is_skip_not_death(self):
        st = {}
        def boom():
            raise RuntimeError("CIM boom")
        sp = self._spec("userbot", finder=boom)
        out = o.client_watchdog_tick(now=self.NOW, specs=[sp], state=st,
                                     cooldown=0, max_deaths=3, grace_until=0, log_stale=120, blind_alarm=3)
        self.assertEqual(out["userbot"], "check_failed")
        self.assertEqual(sp["_raises"], [])

    # (б) доказанная смерть (finder OK + пусто + лог протух) → рестарт как раньше
    def test_b_proven_death_restarts(self):
        st = {}
        logf = self._logfile("userbot.log", age_sec=5000)         # протух (>120)
        sp = self._spec("userbot", finder=lambda: [], logfile=logf)
        out = o.client_watchdog_tick(now=self.NOW, specs=[sp], state=st,
                                     cooldown=0, max_deaths=3, grace_until=0, log_stale=120, blind_alarm=3)
        self.assertEqual(out["userbot"], "raise")
        self.assertEqual(len(sp["_raises"]), 1)
        self.assertEqual(st["userbot"]["deaths"], 1)
        self.assertTrue(any("вотчдог поднял userbot" in n for n in self.notes))

    def test_b_fresh_log_vetoes_restart(self):
        """Свежий лог + нет PID → смерть НЕ доказана → рестарт отложен (третье условие)."""
        st = {}
        logf = self._logfile("userbot.log", age_sec=10)           # свеж (<120)
        sp = self._spec("userbot", finder=lambda: [], logfile=logf)
        out = o.client_watchdog_tick(now=self.NOW, specs=[sp], state=st,
                                     cooldown=0, max_deaths=3, grace_until=0, log_stale=120, blind_alarm=3)
        self.assertEqual(out["userbot"], "fresh_log")
        self.assertEqual(sp["_raises"], [])
        self.assertNotIn("userbot", st)                           # смерть не засчитана

    # (в) 3 подряд слепых цикла → NOTE «вотчдог слеп», рестартов не было
    def test_c_three_blind_cycles_alarm_no_restart(self):
        st = {}
        sp = self._spec("userbot", finder=lambda: None)
        for t in (1, 2, 3):
            o.client_watchdog_tick(now=self.NOW + t, specs=[sp], state=st,
                                   cooldown=0, max_deaths=3, grace_until=0, log_stale=120, blind_alarm=3)
        self.assertEqual(sp["_raises"], [])                       # ни одного рестарта
        self.assertTrue(any("слеп" in n for n in self.notes))     # NOTE-алярм
        self.assertTrue(any("слеп" in p for p in self.crit))      # критич. пуш слепоты → инбокс 1160 (НЕ личка)
        self.assertEqual(self.pushes, [])                         # halt-слепота в личку НЕ дублируется
        self.assertFalse(any("поднял" in n for n in self.notes))  # никого не поднимали

    def test_c_success_resets_blind_counter(self):
        st = {"__blind__": 2}
        logf = self._logfile("userbot.log", age_sec=1)            # свежий → alive-путь всё равно
        sp = self._spec("userbot", finder=lambda: [123], logfile=logf)   # живой → успех finder'а
        o.client_watchdog_tick(now=self.NOW, specs=[sp], state=st,
                               cooldown=0, max_deaths=3, grace_until=0, log_stale=120, blind_alarm=3)
        self.assertEqual(st["__blind__"], 0)                      # успешный finder обнулил

    # (г) скачок часов (пробуждение) → grace 120с, вердиктов нет
    def test_g_wake_detect(self):
        self.assertTrue(o._woke_from_sleep(now=1000, prev=1000 - 200, poll=60, margin=60))   # gap 200 > 120
        self.assertFalse(o._woke_from_sleep(now=1000, prev=1000 - 61, poll=60, margin=60))   # gap 61 < 120
        self.assertFalse(o._woke_from_sleep(now=1000, prev=None, poll=60, margin=60))         # первый виток

    def test_g_grace_suppresses_verdicts(self):
        st = {}
        logf = self._logfile("userbot.log", age_sec=5000)         # протух — в обычном цикле был бы рестарт
        sp = self._spec("userbot", finder=lambda: [], logfile=logf)
        out = o.client_watchdog_tick(now=self.NOW, specs=[sp], state=st,
                                     cooldown=0, max_deaths=3, grace_until=self.NOW + 120,
                                     log_stale=120, blind_alarm=3)
        self.assertEqual(out, {"_": "grace"})                     # вердиктов нет
        self.assertEqual(sp["_raises"], [])                       # рестарта нет
        self.assertNotIn("userbot", st)

    def test_g_grace_expired_normal(self):
        """После окна grace нормальный цикл работает (не сломали норму)."""
        st = {}
        logf = self._logfile("userbot.log", age_sec=5000)
        sp = self._spec("userbot", finder=lambda: [], logfile=logf)
        out = o.client_watchdog_tick(now=self.NOW, specs=[sp], state=st,
                                     cooldown=0, max_deaths=3, grace_until=self.NOW - 1,
                                     log_stale=120, blind_alarm=3)
        self.assertEqual(out["userbot"], "raise")

    # три исхода finder напрямую (парсинг PID / пусто / None на сбое)
    def test_finder_three_outcomes(self):
        real = subprocess = None
        with mock.patch.object(o.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="123\n456\n", stderr="", returncode=0)
            self.assertEqual(o._find_pids_by_script("userbot_listen.py"), [123, 456])
            run.return_value = mock.Mock(stdout="", stderr="", returncode=0)
            self.assertEqual(o._find_pids_by_script("userbot_listen.py"), [])          # честная пустота
            run.return_value = mock.Mock(stdout="", stderr="err", returncode=1)
            self.assertIsNone(o._find_pids_by_script("userbot_listen.py"))             # скрытый сбой → None
            run.side_effect = o.subprocess.TimeoutExpired(cmd="powershell", timeout=20)
            self.assertIsNone(o._find_pids_by_script("userbot_listen.py"))             # таймаут → None


class TestSingletonLock(unittest.TestCase):
    """OS-синглтон демона (разбор #128, часть 4): lock_path/pid_alive инъектируются, tasklist
    НЕ дёргаем. Проверяем: свободный лок берётся; живой чужой → отказ; мёртвый холдер → забор;
    supersede-PID → ждём и забираем; release снимает только свой."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "orch.lock")
        self._env = os.environ.pop(o.SUPERSEDE_ENV, None)

    def tearDown(self):
        if self._env is not None:
            os.environ[o.SUPERSEDE_ENV] = self._env
        else:
            os.environ.pop(o.SUPERSEDE_ENV, None)

    def test_acquire_free(self):
        self.assertTrue(o.acquire_singleton(lock_path=self.lock, pid_alive=lambda p: False))
        self.assertEqual(Path(self.lock).read_text().strip(), str(os.getpid()))

    def test_live_other_refused(self):
        with open(self.lock, "w") as f:
            f.write("99999")                            # чужой PID
        self.assertFalse(o.acquire_singleton(lock_path=self.lock, pid_alive=lambda p: True))

    def test_stale_holder_stolen(self):
        with open(self.lock, "w") as f:
            f.write("99999")
        self.assertTrue(o.acquire_singleton(lock_path=self.lock, pid_alive=lambda p: False))
        self.assertEqual(Path(self.lock).read_text().strip(), str(os.getpid()))

    def test_supersede_waits_then_takes(self):
        os.environ[o.SUPERSEDE_ENV] = "77777"
        with open(self.lock, "w") as f:
            f.write("77777")                            # лок держит сменяемый старый
        # старый «умирает» после первой проверки: pid_alive → False со 2-го вызова
        seq = {"n": 0}
        def alive(pid):
            seq["n"] += 1
            return seq["n"] < 2
        ok = o.acquire_singleton(lock_path=self.lock, pid_alive=alive, supersede_wait=5, sleep=0)
        self.assertTrue(ok)
        self.assertEqual(Path(self.lock).read_text().strip(), str(os.getpid()))

    def test_release_only_own(self):
        with open(self.lock, "w") as f:
            f.write("42")                               # не наш
        o.release_singleton(lock_path=self.lock)
        self.assertTrue(os.path.exists(self.lock))      # чужой лок не трогаем
        with open(self.lock, "w") as f:
            f.write(str(os.getpid()))
        o.release_singleton(lock_path=self.lock)
        self.assertFalse(os.path.exists(self.lock))     # свой — сняли

    def test_spawn_daemon_sets_supersede_env(self):
        captured = {}
        def fake_popen(cmd, **kw):
            captured.update(kw)
            class P: pass
            return P()
        with mock.patch.object(o.subprocess, "Popen", fake_popen):
            self.assertTrue(o._spawn_daemon())
        self.assertEqual(captured["env"][o.SUPERSEDE_ENV], str(os.getpid()))


class TestFileProcessMap(unittest.TestCase):
    """ЯВНАЯ карта файл→процесс (не эвристика) + распознавание команд-рычагов по якорям."""

    def test_explicit_map(self):
        self.assertEqual(o._procs_for_file("userbot_listen.py"), {"userbot"})
        self.assertEqual(o._procs_for_file("booking_draft.py"), {"moderbot"})   # build_intake исполняет moderbot (импорт только в moderation_bot), НЕ userbot
        self.assertEqual(o._procs_for_file("moderation_ipc.py"), {"userbot"})    # → userbot, НЕ moderbot
        self.assertEqual(o._procs_for_file("moderation_bot.py"), {"moderbot"})
        self.assertEqual(o._procs_for_file("moderation_core.py"), {"moderbot"})
        self.assertEqual(o._procs_for_file("suggest.py"), {"userbot", "moderbot"})
        self.assertEqual(o._procs_for_file("pricing_rules.py"), {"userbot", "moderbot"})
        self.assertEqual(o._procs_for_file("delivery.py"), {"userbot", "moderbot"})   # резолвер доставки → общий рантайм (dae330a)
        # trainer.py: карта ПО ФАКТУ импортов — userbot_listen.py:44 И moderation_bot.py:36 (оба
        # модульного уровня) → общий рантайм. Без правила правка только trainer.py не рестартила никого.
        self.assertEqual(o._procs_for_file("trainer.py"), {"userbot", "moderbot"})
        # trainer_log.py — лог тренажёра в мозг: импортят ОБА (userbot пишет реплики/ответы/команды,
        # moderbot — нажатия кнопок и уроки). Правило ТОЧНОЕ по имени, а не префикс «trainer»:
        # рядом лежат ДАННЫЕ (trainer_rules.json / trainer_log_doc.json), читаемые на вызове.
        self.assertEqual(o._procs_for_file("trainer_log.py"), {"userbot", "moderbot"})
        self.assertEqual(o._procs_for_file("trainer_log_doc.json"), set())            # file id дока — данные, не код
        self.assertEqual(o._procs_for_file("test_trainer_log.py"), set())             # тест — не рантайм
        self.assertEqual(o._procs_for_file("trainer_rules.json"), set())              # данные правил, не код → рестарт не нужен
        self.assertEqual(o._procs_for_file("test_trainer.py"), set())                 # тест — не рантайм
        self.assertEqual(o._procs_for_file("fetch_delivery.py"), set())               # утилита-фетчер, НЕ рантайм ботов
        self.assertEqual(o._procs_for_file("pc_agent.py"), {"pc_agent"})
        # log_setup.py — общая ротация логов: файловый хендлер вешается НА ИМПОРТЕ, значит новый
        # порог/путь подхватывается только рестартом. Без правила правка одного log_setup.py не
        # рестартила бы никого — ровно класс delivery.py dae330a (живой бот на старом коде часами).
        self.assertEqual(o._procs_for_file("log_setup.py"), {"userbot", "moderbot", "pc_agent"})
        self.assertEqual(o._procs_for_file("test_log_setup.py"), set())          # тест — не рантайм
        self.assertEqual(o._procs_for_file("README.md"), set())
        self.assertEqual(o._procs_for_file("CLAUDE.md"), set())
        self.assertEqual(o._procs_for_file("test_suggest.py"), set())            # тест — не рантайм

    def test_classify_trainer_to_both(self):
        ub, mb = o._classify_changed(["trainer.py"])
        self.assertIn("trainer.py", ub)
        self.assertIn("trainer.py", mb)

    def test_classify_moderation_ipc_to_userbot(self):
        ub, mb = o._classify_changed(["moderation_ipc.py"])
        self.assertIn("moderation_ipc.py", ub)
        self.assertNotIn("moderation_ipc.py", mb)

    def test_match_command_anchored(self):
        self.assertEqual(o._match_command("рестартни userbot"), "restart_userbot")
        self.assertEqual(o._match_command("Перезапусти userbot!"), "restart_userbot")
        self.assertEqual(o._match_command("рестартни модербот"), "restart_moderbot")
        self.assertEqual(o._match_command("рестарт moderation_bot"), "restart_moderbot")
        self.assertEqual(o._match_command("статус контура"), "status")
        self.assertIsNone(o._match_command("тз: рестартни userbot при сбое"))    # дев-задача НЕ перехвачена
        self.assertIsNone(o._match_command("обнови userbot"))                    # не наша команда
        self.assertIsNone(o._match_command("расскажи про статус контура войск"))
        self.assertIsNone(o._match_command(""))


class TestSelfUpdateChildren(Base):
    """(1) авто-рестарт детей при self-update по диффу old..new (ЯВНАЯ карта): git-дифф/рестарт/
    время/реестр инъектируются — боевое НЕ трогаем. Пункты (а),(б),(д),(е) + интеграция + анти-флап
    против дев-рестарта."""

    def test_a_userbot_change_restarts_userbot_with_note(self):
        kinds, cows = [], []
        o._cowork = lambda s: cows.append(s)
        note = o._selfupdate_restart_children(
            "old", "c0mmit1",
            diff_fn=lambda a, b: ["userbot_listen.py"],
            restart_fn=lambda kind: kinds.append(kind) or (True, [9999], "PID поднят, лог свежий"),
            state={}, now=1000, cooldown=120)
        self.assertEqual(kinds, ["userbot"])                                     # стоп+старт userbot
        self.assertIn("userbot рестартнут", note)
        self.assertTrue(any("авто-применил c0mmit1: рестарт userbot" in s for s in cows))

    def test_b_only_md_nobody_restarted(self):
        kinds = []
        note = o._selfupdate_restart_children(
            "old", "c2",
            diff_fn=lambda a, b: ["README.md", "CLAUDE.md", "docs/guide.md"],
            restart_fn=lambda kind: kinds.append(kind) or (True, [1], "x"),
            state={}, now=1, cooldown=120)
        self.assertEqual(kinds, [])                                              # никого не тронули
        self.assertEqual(note, "")

    def test_d_pc_agent_manual_note_not_restarted(self):
        kinds, cows = [], []
        o._cowork = lambda s: cows.append(s)
        note = o._selfupdate_restart_children(
            "old", "c3",
            diff_fn=lambda a, b: ["pc_agent.py"],
            restart_fn=lambda kind: kinds.append(kind) or (True, [1], "x"),
            state={}, now=1, cooldown=120)
        self.assertEqual(kinds, [])                                              # агент чужими руками НЕ рестартим
        self.assertIn("РУЧНОГО рестарта", note)

    def test_e_antiflap_recent_restart_suppressed(self):
        kinds = []
        note = o._selfupdate_restart_children(
            "old", "c4",
            diff_fn=lambda a, b: ["userbot_listen.py"],
            restart_fn=lambda kind: kinds.append(kind) or (True, [1], "x"),
            state={"userbot": 1000}, now=1050, cooldown=120)                     # рестартили 50с назад (<120)
        self.assertEqual(kinds, [])                                              # анти-флап подавил
        self.assertIn("анти-флап", note)

    def test_devtask_restart_then_selfupdate_suppressed(self):
        save = dict(o._apply_restart_at)
        o._apply_restart_at.clear()
        try:
            o.maybe_update_bots(                                                 # дев-задача рестартит userbot → штамп
                5, "тз: правка", "old",
                changed_fn=lambda hb: ["userbot_listen.py"],
                gate_fn=lambda mods: (True, "ok"),
                restart_fn=lambda kind: (True, [111], "ok"),
                head_fn=lambda: "cc")
            self.assertIn("userbot", o._apply_restart_at)
            kinds = []
            note = o._selfupdate_restart_children(                               # self-update видит тот же файл
                "old", "cc",
                diff_fn=lambda a, b: ["userbot_listen.py", "pc_orchestrator.py"],
                restart_fn=lambda kind: kinds.append(kind) or (True, [1], "x"),
                cooldown=120, now=o._apply_restart_at["userbot"] + 1)
            self.assertEqual(kinds, [])                                          # повторно НЕ дёрнули
            self.assertIn("анти-флап", note)
        finally:
            o._apply_restart_at.clear()
            o._apply_restart_at.update(save)

    def test_selfupdate_invokes_children_reconcile(self):
        save = (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB)
        o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB = "blob-old", "old7", None
        try:
            seen = {}
            r = o.maybe_self_update(
                blob_fn=lambda: "blob-new", head_fn=lambda: "new7",
                code_gate=lambda: (True, "ok"), tests_gate=lambda: (True, "ok"),
                spawner=lambda: True,
                children_fn=lambda old, new: seen.update(old=old, new=new) or "")
            self.assertTrue(r)
            self.assertEqual((seen.get("old"), seen.get("new")), ("old7", "new7"))
        finally:
            (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB) = save

    def test_moderbot_reply_mode_ok_no_pids(self):
        note = o._selfupdate_restart_children(
            "old", "c5",
            diff_fn=lambda a, b: ["moderation_bot.py"],
            restart_fn=lambda kind: (True, [], "модербот в reply-режиме (нет MODERBOT_TOKEN) — рестарт не требуется"),
            state={}, now=1, cooldown=120)
        self.assertIn("reply-режиме", note)

    def test_child_restart_failure_noted(self):
        note = o._selfupdate_restart_children(
            "old", "c6",
            diff_fn=lambda a, b: ["userbot_listen.py"],
            restart_fn=lambda kind: (False, [], "процесс не поднялся"),
            state={}, now=1, cooldown=120)
        self.assertIn("рестарт НЕ удался", note)

    def test_stop_flag_no_children_restart(self):
        o._stopped = lambda: True
        kinds = []
        note = o._selfupdate_restart_children(
            "old", "c7",
            diff_fn=lambda a, b: ["userbot_listen.py"],
            restart_fn=lambda kind: kinds.append(kind) or (True, [1], "x"),
            state={}, now=1, cooldown=120)
        self.assertEqual(note, "")
        self.assertEqual(kinds, [])


class TestReconcileChildren(Base):
    """Класс-фикс c6d8a30: реконсиляция детей на ЛЮБОЙ новый коммит (не только self-update/дев-таск).
    Реагирует на изменённые файлы детей в диффе, когда pc_orchestrator.py НЕ менялся. git-дифф/
    рестарт/гейт/время/реестр инъектируются — боевое НЕ трогаем."""

    def setUp(self):
        super().setUp()
        self._save_rc = (o._last_child_commit, o._child_reconcile_rejected, o._child_reconcile_last_run)
        o._last_child_commit = None
        o._child_reconcile_rejected = None
        o._child_reconcile_last_run = 0.0

    def tearDown(self):
        (o._last_child_commit, o._child_reconcile_rejected, o._child_reconcile_last_run) = self._save_rc
        super().tearDown()

    def _run(self, head, changed, **kw):
        return o.reconcile_children_tick(
            head_fn=lambda: head,
            diff_fn=lambda a, b: changed,
            gate_fn=kw.get("gate_fn", (lambda mods: (True, "ok"))),
            restart_fn=kw.get("restart_fn"),
            now=kw.get("now", 1000), cooldown=kw.get("cooldown", 120), state=kw.get("state"))

    def test_first_tick_adopts_head_no_restart(self):
        # первый прогон (метка None): текущий HEAD принят как применённый (дети стартовали с ним).
        kinds = []
        note = self._run("h1", ["suggest.py"],
                         restart_fn=lambda k: kinds.append(k) or (True, [1], "x"), state={})
        self.assertEqual((note, kinds), ("", []))
        self.assertEqual(o._last_child_commit, "h1")

    def test_child_only_commit_restarts_without_orchestrator_change(self):
        # ГЛАВНОЕ: коммит тронул ТОЛЬКО suggest.py (pc_orchestrator.py НЕ менялся) → рестарт детей.
        o._last_child_commit = "old"
        kinds, cows = [], []
        o._cowork = lambda s: cows.append(s)
        note = self._run("c6d8a30ab", ["suggest.py", "test_suggest.py"],
                         restart_fn=lambda k: kinds.append(k) or (True, [777], "PID поднят, лог свежий"),
                         state={})
        self.assertEqual(kinds, ["userbot", "moderbot"])          # suggest → оба рантайма
        self.assertIn("userbot рестартнут", note)
        self.assertTrue(any("авто-применил c6d8a30ab: рестарт userbot" in s for s in cows))
        self.assertEqual(o._last_child_commit, "c6d8a30ab")       # метка сдвинута — второй раз не дёрнет

    def test_golden_direct_delivery_commit_restarts_userbot(self):
        # ГОЛДЕН живого кейса dae330a: прямой коммит (НЕ дев-таск, пришёл фетчем/pull) тронул
        # delivery.py → авто-рестарт userbot. Раньше delivery.py не было в _FILE_PROCESS_RULES →
        # реконсиляция считала его не-код-файлом → userbot жил на старом коде 2ч+.
        o._last_child_commit = "old"
        kinds, cows = [], []
        o._cowork = lambda s: cows.append(s)
        note = self._run("dae330a00", ["delivery.py", "test_delivery.py"],
                         restart_fn=lambda k: kinds.append(k) or (True, [828], "PID поднят, лог свежий"),
                         state={})
        self.assertIn("userbot", kinds)                           # userbot рестартнут по delivery.py
        self.assertIn("userbot рестартнут", note)
        self.assertTrue(any("авто-применил dae330a00: рестарт userbot" in s for s in cows))
        self.assertEqual(o._last_child_commit, "dae330a00")       # метка сдвинута — второй раз не дёрнет

    def test_golden_trainer_commit_restarts_both_bots(self):
        # ГОЛДЕН класса dae330a на trainer.py: коммит тронул ТОЛЬКО trainer.py (+ его тест) →
        # рестарт ОБОИХ ботов (импорт модульного уровня в userbot_listen.py:44 и moderation_bot.py:36).
        # До правила trainer.py не был в карте → реконсиляция считала его не-код-файлом и живые боты
        # доживали на старом коде (кейс e3b8994: UX-фикс гипотез поднял только модербота — по
        # moderation_bot.py; правка одного trainer.py не подняла бы никого).
        o._last_child_commit = "old"
        kinds, cows = [], []
        o._cowork = lambda s: cows.append(s)
        note = self._run("7ra1ne200", ["trainer.py", "test_trainer.py"],
                         restart_fn=lambda k: kinds.append(k) or (True, [909], "PID поднят, лог свежий"),
                         state={})
        self.assertEqual(kinds, ["userbot", "moderbot"])          # trainer → оба рантайма
        self.assertIn("userbot рестартнут", note)
        self.assertIn("модербот рестартнут", note)
        self.assertEqual(o._last_child_commit, "7ra1ne200")

    def test_golden_docs_only_commit_no_restart(self):
        # ГОЛДЕН: коммит тронул ТОЛЬКО docs/ (и notes/) → никого не рестартим, метку двигаем.
        o._last_child_commit = "old"
        kinds = []
        note = self._run("d0c50000", ["docs/task_notes/n.md", "notes/plan.md", "docs/readme.txt"],
                         restart_fn=lambda k: kinds.append(k) or (True, [1], "x"), state={})
        self.assertEqual((note, kinds), ("", []))
        self.assertEqual(o._last_child_commit, "d0c50000")

    def test_pc_agent_commit_manual_note_not_restarted(self):
        # pc_agent.py в диффе ВНЕ self-update → реконсиляция даёт пометку «ждёт ручного рестарта»,
        # но чужими руками НЕ рестартит (та же ручная карта, что в _selfupdate_restart_children).
        o._last_child_commit = "old"
        kinds, cows = [], []
        o._cowork = lambda s: cows.append(s)
        note = self._run("a9e17c000", ["pc_agent.py"],
                         restart_fn=lambda k: kinds.append(k) or (True, [1], "x"), state={})
        self.assertEqual(kinds, [])                               # агент чужими руками НЕ рестартим
        self.assertIn("РУЧНОГО рестарта", note)
        self.assertEqual(o._last_child_commit, "a9e17c000")       # пометка разовая — метку двигаем (не спамим каждый тик)

    def test_no_child_files_advances_marker_only(self):
        # коммит тронул только сам pc_orchestrator.py/доки → метку двигаем, никого не рестартим.
        o._last_child_commit = "old"
        kinds = []
        note = self._run("new9", ["pc_orchestrator.py", "README.md"],
                         restart_fn=lambda k: kinds.append(k) or (True, [1], "x"), state={})
        self.assertEqual((note, kinds), ("", []))
        self.assertEqual(o._last_child_commit, "new9")

    def test_red_gate_holds_marker_and_remembers_head(self):
        # гейт КРАСНЫЙ → метку НЕ двигаем (стале-код доживёт до фикса), HEAD запомнен (не гоняем гейт).
        o._last_child_commit = "old"
        kinds = []
        note = self._run("bad1", ["suggest.py"],
                         gate_fn=lambda mods: (False, "FAILED тест"),
                         restart_fn=lambda k: kinds.append(k) or (True, [1], "x"), state={})
        self.assertEqual(kinds, [])                               # красный гейт — не рестартим
        self.assertIn("тесты красные", note)
        self.assertEqual(o._last_child_commit, "old")             # метка на месте
        self.assertEqual(o._child_reconcile_rejected, "bad1")
        # повторный прогон того же HEAD — гейт НЕ гоняем (ранний выход)
        gate_calls = []
        note2 = self._run("bad1", ["suggest.py"],
                          gate_fn=lambda mods: gate_calls.append(1) or (False, "x"),
                          restart_fn=lambda k: (True, [1], "x"), state={})
        self.assertEqual((note2, gate_calls), ("", []))

    def test_antiflap_suppresses_when_recently_restarted(self):
        # дев-таск/self-update только что рестартили userbot (штамп) → реконсиляция не дёргает повторно.
        o._last_child_commit = "old"
        kinds = []
        note = self._run("c9", ["suggest.py"],
                         restart_fn=lambda k: kinds.append(k) or (True, [1], "x"),
                         state={"userbot": 950, "moderbot": 950}, now=1000, cooldown=120)
        self.assertEqual(kinds, [])                               # анти-флап подавил оба
        self.assertIn("анти-флап", note)
        self.assertEqual(o._last_child_commit, "c9")              # применено (код уже стоит) — метка сдвинута

    def test_no_new_commit_is_noop(self):
        o._last_child_commit = "same"
        note = self._run("same", ["suggest.py"], restart_fn=lambda k: (True, [1], "x"), state={})
        self.assertEqual(note, "")

    def test_stop_flag_no_reconcile(self):
        o._stopped = lambda: True
        o._last_child_commit = "old"
        kinds = []
        note = self._run("new", ["suggest.py"],
                         restart_fn=lambda k: kinds.append(k) or (True, [1], "x"), state={})
        self.assertEqual((note, kinds), ("", []))

    def test_throttle_skips_until_interval(self):
        # maybe_reconcile_children троттлит тело не чаще CHILD_RECONCILE_SEC.
        o._child_reconcile_last_run = 1000.0
        called = {"n": 0}
        save = o.reconcile_children_tick
        o.reconcile_children_tick = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ""
        try:
            self.assertIsNone(o.maybe_reconcile_children(now=1000.0 + o.CHILD_RECONCILE_SEC - 1))
            self.assertEqual(called["n"], 0)
            self.assertEqual(o.maybe_reconcile_children(now=1000.0 + o.CHILD_RECONCILE_SEC + 1), "")
            self.assertEqual(called["n"], 1)
        finally:
            o.reconcile_children_tick = save


class TestGitFfPull(Base):
    """Родитель #221: периодический git fetch + FAST-FORWARD-ONLY pull origin/<branch>.
    Тянем ТОЛЬКО чистое дерево на целевой ветке, строго позади origin и с возможным ff.
    Грязно / не-ff / другая ветка / сбой → пропуск + NOTE, pull НЕ вызывается. Никогда merge/rebase.
    git-вызовы инъектируются — боевой git не трогаем."""

    def setUp(self):
        super().setUp()
        self._save_gp = o._git_pull_last_run
        o._git_pull_last_run = 0.0
        self.cows = []
        o._cowork = lambda s: self.cows.append(s)

    def tearDown(self):
        o._git_pull_last_run = self._save_gp
        super().tearDown()

    def _call(self, *, branch="main", status="", head="loc", origin="rem", is_ancestor=0,
              rev_ancestor=1, fetch=(0, "", ""), pull=(0, "Updating loc..new1", ""),
              new_head="new1234ab", trace=None):
        """Фейковый _git_call: диспетчеризация по git-подкоманде. trace копит выполненные команды.
        is_ancestor  = код `merge-base --is-ancestor HEAD origin`  (0 = HEAD предок origin → позади/ff).
        rev_ancestor = код обратного `--is-ancestor origin HEAD`   (0 = origin предок HEAD → мы впереди;
                       ≠0 = ни один не предок → истинное расхождение). Спрашивается лишь при is_ancestor≠0."""
        def call(args, timeout=90):
            if trace is not None:
                trace.append(tuple(args))
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return (0, branch, "")
            if args == ["status", "--porcelain"]:
                return (0, status, "")
            if args == ["fetch", "origin"]:
                return fetch
            if args == ["rev-parse", "HEAD"]:
                return (0, head, "")
            if args == ["rev-parse", f"origin/{branch}"]:
                return (0, origin, "")
            if args == ["merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"]:
                return (is_ancestor, "", "")
            if args == ["merge-base", "--is-ancestor", f"origin/{branch}", "HEAD"]:
                return (rev_ancestor, "", "")
            if args == ["pull", "--ff-only", "origin", branch]:
                return pull
            if args == ["rev-parse", "--short", "HEAD"]:
                return (0, new_head, "")
            raise AssertionError(f"неожиданный git-вызов: {args}")
        return call

    def test_ff_when_clean_and_behind(self):
        # ГЛАВНОЕ: чистая копия, HEAD позади и предок origin → git pull --ff-only, метка-итог.
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(head="loc", origin="rem", is_ancestor=0, trace=tr))
        self.assertEqual(note, "ff → new1234ab")
        self.assertIn(("pull", "--ff-only", "origin", "main"), tr)
        self.assertTrue(any("fast-forward main → new1234ab" in s for s in self.cows))

    def test_dirty_tree_skips_pull(self):
        # грязное дерево → pull НЕ вызываем (не спорим с незакоммиченными правками).
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(status=" M suggest.py", trace=tr))
        self.assertEqual(note, "грязно — пропуск")
        self.assertNotIn(("pull", "--ff-only", "origin", "main"), tr)
        self.assertTrue(any("грязная" in s for s in self.cows))

    def test_untracked_only_does_not_block_pull(self):
        # КЛАСС-ГОЛДЕН (живое дерево ПК): tracked-изменений НЕТ, но постоянные untracked
        # (pc_orchestrator.heartbeat, fetch_delivery.py) дают непустой porcelain. По старому
        # условию авто-фетч блокировался ВЕЧНО. Теперь '??'-строки грязью НЕ считаются →
        # ff-pull проходит; NOTE «грязная» не спамится.
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(
            status="?? fetch_delivery.py\n?? pc_orchestrator.heartbeat",
            head="loc", origin="rem", is_ancestor=0, trace=tr))
        self.assertEqual(note, "ff → new1234ab")
        self.assertIn(("pull", "--ff-only", "origin", "main"), tr)
        self.assertFalse(any("грязная" in s for s in self.cows))

    def test_untracked_plus_tracked_change_still_skips(self):
        # РЕГРЕСС: untracked РЯДОМ с реальной tracked-правкой — грязь не ослаблена, пропуск.
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(
            status="?? pc_orchestrator.heartbeat\n M suggest.py", trace=tr))
        self.assertEqual(note, "грязно — пропуск")
        self.assertNotIn(("pull", "--ff-only", "origin", "main"), tr)

    def test_ahead_of_origin_skips_pull(self):
        # КЛАСС «main впереди»: чисто, HEAD НЕ предок origin, но origin — предок HEAD → мы строго
        # ВПЕРЕДИ (неотправленные локальные коммиты). ff нечего применять → ТИХИЙ пропуск БЕЗ NOTE
        # (штатное состояние до push, спамить cowork_log незачем). pull НЕ вызываем, merge/rebase — нет.
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(head="ahead2", origin="behind1",
                                                     is_ancestor=1, rev_ancestor=0, trace=tr))
        self.assertEqual(note, "впереди origin — пропуск")
        self.assertNotIn(("pull", "--ff-only", "origin", "main"), tr)
        self.assertEqual(self.cows, [])                      # впереди — не NOTE-worthy

    def test_non_ff_divergence_skips_pull(self):
        # КЛАСС «расхождение»: HEAD не предок origin И origin не предок HEAD (истинный не-ff, есть
        # локальные коммиты по обе стороны) → пропуск + NOTE, нужен разбор. НИКОГДА merge/rebase.
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(head="fork_l", origin="fork_r",
                                                     is_ancestor=1, rev_ancestor=1, trace=tr))
        self.assertEqual(note, "не-ff (расхождение) — пропуск")
        self.assertNotIn(("pull", "--ff-only", "origin", "main"), tr)
        self.assertTrue(any("не-ff" in s for s in self.cows))

    def test_up_to_date_noop(self):
        # HEAD == origin → уже актуально, тихий no-op (без pull, без NOTE).
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(head="same", origin="same", trace=tr))
        self.assertEqual((note, self.cows), ("", []))
        self.assertNotIn(("pull", "--ff-only", "origin", "main"), tr)

    def test_wrong_branch_noop(self):
        # не на целевой ветке (feature/detached) → тихий пропуск, даже fetch не гоняем.
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(branch="feature", trace=tr))
        self.assertEqual(note, "")
        self.assertNotIn(("fetch", "origin"), tr)

    def test_fetch_failure_skips(self):
        # сбой сети на fetch → пропуск + NOTE, pull НЕ вызываем.
        tr = []
        note = o.git_ff_pull_tick(call_fn=self._call(fetch=(1, "", "could not resolve host"), trace=tr))
        self.assertEqual(note, "fetch не удался")
        self.assertNotIn(("pull", "--ff-only", "origin", "main"), tr)

    def test_pull_failure_reported(self):
        # ff внезапно отвергнут самим git (гонка) → NOTE об ошибке, метка HEAD не двигается сама.
        note = o.git_ff_pull_tick(call_fn=self._call(pull=(1, "", "Not possible to fast-forward")))
        self.assertEqual(note, "pull не удался")
        self.assertTrue(any("не удался" in s for s in self.cows))

    def test_stop_flag_no_pull(self):
        o._stopped = lambda: True
        note = o.git_ff_pull_tick(call_fn=self._call(trace=[]))
        self.assertEqual(note, "")

    def test_git_unavailable_silent(self):
        # git недоступен (call → None) → тихий пропуск без исключений.
        note = o.git_ff_pull_tick(call_fn=lambda args, timeout=90: None)
        self.assertEqual((note, self.cows), ("", []))

    def test_throttle_skips_until_interval(self):
        o._git_pull_last_run = 1000.0
        called = {"n": 0}
        save = o.git_ff_pull_tick
        o.git_ff_pull_tick = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or ""
        try:
            self.assertIsNone(o.maybe_git_ff_pull(now=1000.0 + o.GIT_PULL_SEC - 1))
            self.assertEqual(called["n"], 0)
            self.assertEqual(o.maybe_git_ff_pull(now=1000.0 + o.GIT_PULL_SEC + 1), "")
            self.assertEqual(called["n"], 1)
        finally:
            o.git_ff_pull_tick = save

    # ── строки состояния пишутся при СМЕНЕ состояния, а не каждый цикл ──
    def test_dirty_journal_once_until_state_changes(self):
        # ГЛАВНОЕ (родитель: спам «грязная» каждый цикл, 47 строк/сутки): при НЕИЗМЕННОМ составе
        # грязного строка уходит в журнал РОВНО раз; сменился состав — новая строка; стало чисто —
        # ОДНА строка «снова чистое», между ними тишина.
        o._dirty_tracked = lambda runner=None: ["suggest.py"]
        for _ in range(3):                          # три цикла подряд — грязно, состав тот же
            self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(status=" M suggest.py")), "грязно — пропуск")
        dirty = [s for s in self.cows if "грязная" in s]
        self.assertEqual(len(dirty), 1, "одинаковое грязное состояние — ровно одна строка")
        self.assertIn("suggest.py", dirty[0], "строка обязана назвать файл поимённо")
        # состав грязного сменился → новая строка
        o._dirty_tracked = lambda runner=None: ["pricing.py", "suggest.py"]
        self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(status=" M suggest.py")), "грязно — пропуск")
        self.assertEqual(len([s for s in self.cows if "грязная" in s]), 2, "сменился состав — вторая строка")
        # дерево вычистили + актуально → ОДНА строка «снова чистое», дальше тишина
        o._dirty_tracked = lambda runner=None: []
        self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(head="same", origin="same")), "")
        self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(head="same", origin="same")), "")
        self.assertEqual(len([s for s in self.cows if "снова чист" in s]), 1,
                         "возврат к чистому — ровно одна строка, дальше тишина")

    def test_dirty_state_survives_daemon_restart(self):
        # ПУНКТ 3: признак на ДИСКЕ → перезапуск демона в том же грязном состоянии строку НЕ родит.
        # Эмулируем «уже сказали до рестарта», записав признак в state-файл руками (новый процесс его
        # прочитает вместо памяти).
        o._dirty_tracked = lambda runner=None: ["suggest.py"]
        o._autofetch_state_write({"cond": "dirty:suggest.py"})   # состояние ДО рестарта
        note = o.git_ff_pull_tick(call_fn=self._call(status=" M suggest.py"))
        self.assertEqual(note, "грязно — пропуск")
        self.assertEqual([s for s in self.cows if "грязная" in s], [],
                         "то же грязное состояние после рестарта — журнал молчит")

    def test_fetch_failure_journaled_once(self):
        # периодика fetch-сбоя по тому же правилу: сеть лежит — строка одна, а не каждый цикл.
        for _ in range(3):
            self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(fetch=(1, "", "could not resolve host"))),
                             "fetch не удался")
        self.assertEqual(len([s for s in self.cows if "fetch origin не удался" in s]), 1)

    def test_non_ff_journaled_once(self):
        # периодика не-ff по тому же правилу: расхождение висит — строка одна.
        for _ in range(3):
            self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(head="fork_l", origin="fork_r",
                                                                   is_ancestor=1, rev_ancestor=1)),
                             "не-ff (расхождение) — пропуск")
        self.assertEqual(len([s for s in self.cows if "не-ff" in s]), 1)

    def test_first_clean_tick_is_silent(self):
        # свежий демон на чистом актуальном дереве НЕ объявляет «снова чистое» на ровном месте.
        self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(head="same", origin="same")), "")
        self.assertEqual(self.cows, [])

    def test_recovery_line_after_dirty_then_ff(self):
        # ff после грязи: одна строка «грязная», затем ОДНА строка про fast-forward; лишней «снова
        # чистое» ff не плодит (свою строку он уже сказал).
        o._dirty_tracked = lambda runner=None: ["suggest.py"]
        self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(status=" M suggest.py")), "грязно — пропуск")
        o._dirty_tracked = lambda runner=None: []
        self.assertEqual(o.git_ff_pull_tick(call_fn=self._call(head="loc", origin="rem", is_ancestor=0)),
                         "ff → new1234ab")
        self.assertEqual(len([s for s in self.cows if "fast-forward" in s]), 1)
        self.assertEqual([s for s in self.cows if "снова чист" in s], [], "ff не плодит лишней recovery-строки")


class TestCommandLevers(Base):
    """(2) команды-рычаги: одиночная lane=pc задача-команда исполняется НАПРЯМУЮ, без headless claude."""

    def _no_headless(self):
        def boom(*a, **k):
            raise AssertionError("headless claude НЕ должен запускаться для команды-рычага")
        o.run_claude = boom

    def _patch_restart(self, fn):
        save = o._restart_via_pc_agent
        self.addCleanup(lambda: setattr(o, "_restart_via_pc_agent", save))
        o._restart_via_pc_agent = fn

    def test_v_restart_moderbot_direct_done_no_headless(self):
        self._no_headless()
        self._patch_restart(lambda kind: (True, [222], "PID поднят, лог свежий") if kind == "moderbot" else (False, [], "?"))
        tid = self.fb.add(status="new", task_text="рестартни модербот")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")                   # помечена done
        self.assertIn("moderbot перезапущен", self.fb.tasks[tid]["result"])      # стоп/старт напрямую
        self.assertIn("222", self.fb.tasks[tid]["result"])                       # новый PID

    def test_restart_userbot_direct(self):
        self._no_headless()
        self._patch_restart(lambda kind: (True, [333], "PID поднят, лог свежий"))
        tid = self.fb.add(status="new", task_text="рестартни userbot")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertIn("userbot перезапущен", self.fb.tasks[tid]["result"])

    def test_status_command_direct(self):
        self._no_headless()
        save = o._find_pids_by_script
        self.addCleanup(lambda: setattr(o, "_find_pids_by_script", save))
        o._find_pids_by_script = lambda name: [42] if "userbot_listen" in name else []
        tid = self.fb.add(status="new", task_text="статус контура")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertIn("Статус контура", self.fb.tasks[tid]["result"])
        self.assertIn("userbot: жив", self.fb.tasks[tid]["result"])

    def test_restart_command_failure_failed(self):
        self._patch_restart(lambda kind: (False, [], "процесс не поднялся"))
        tid = self.fb.add(status="new", task_text="рестартни userbot")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("не удался", self.fb.tasks[tid]["result"])

    def test_exec_command_respects_stopflag(self):
        o._stopped = lambda: True
        status, res = o._exec_command("restart_userbot", restart_fn=lambda k: (True, [1], "x"))
        self.assertEqual(status, "failed")
        self.assertIn("рубильник", res)

    def test_non_command_dev_task_uses_headless(self):
        called = {"n": 0}

        def fake(prompt, timeout, cwd, env):
            called["n"] += 1
            return (0, "сделал\nRESULT: готово", "")
        o.run_claude = fake
        tid = self.fb.add(status="new", task_text="тз: рестартни userbot при сбое")
        o.process_new()
        self.assertEqual(called["n"], 1)                                         # ушло в headless (не перехвачено)
        self.assertEqual(self.fb.tasks[tid]["status"], "done")


class TestContourStatusChains(unittest.TestCase):
    """Косметика «статус контура»: секция «в работе» = ТОЛЬКО живые локальные цепи (без призраков
    финализированных родителей pcloc-dec) + честная строка тика ревизора. Чистые функции —
    инъекция items/revizor_state, ни Bridge, ни диска."""

    F = "Filipp-pcloc-dec"

    def _parent(self, pid, status, text="тз: почини детект X"):
        return {"id": pid, "from": self.F, "status": status, "task_text": text}

    def _step(self, pid, i, n, status):
        return {"id": 900 + pid + i, "from": self.F, "status": status,
                "task_text": f"[шаг {i}/{n} родитель {pid}] сделай шаг"}

    # ---- classifier: _loc_active_chains ----

    def test_finalized_parent_without_summary_not_active(self):
        # ЖИВОЙ провал 15:36: старый финализированный родитель pcloc-dec (failed) + его step-карточки
        # done/failed висят в очереди → это ПРИЗРАК, работы за ним нет → НЕ в списке «в работе».
        items = [self._parent(194, "failed", text="тз: тест-эпизод июля"),
                 self._step(194, 1, 3, "done"), self._step(194, 2, 3, "failed")]
        self.assertEqual(o._loc_active_chains(items), [])

    def test_done_parent_all_steps_done_not_active(self):
        items = [self._parent(200, "done"),
                 self._step(200, 1, 2, "done"), self._step(200, 2, 2, "done")]
        self.assertEqual(o._loc_active_chains(items), [])

    def test_live_chain_step_2_of_5_active(self):
        # живая цепь: шаг 2/5 ещё in_progress → в списке с ярлыком «шаг 2/5».
        items = [self._parent(300, "done"),                     # родитель закрыт, но шаг ещё идёт
                 self._step(300, 1, 5, "done"), self._step(300, 2, 5, "in_progress")]
        active = o._loc_active_chains(items)
        self.assertEqual(active, [{"pid": 300, "label": "шаг 2/5"}])

    def test_live_parent_no_steps_is_plan_building(self):
        # родитель new/in_progress, шагов ещё нет → «план строится» (живой признак родителя).
        self.assertEqual(o._loc_active_chains([self._parent(310, "in_progress")]),
                         [{"pid": 310, "label": "план строится"}])

    def test_ghosts_excluded_live_included_sorted(self):
        # смесь пяти призраков (194/200/206/207/208) и одной живой цепи → только живая, по pid.
        items = []
        for pid in (194, 200, 206, 207, 208):
            items += [self._parent(pid, "failed"), self._step(pid, 1, 2, "failed")]
        items += [self._parent(300, "done"), self._step(300, 2, 5, "needs_approval")]
        self.assertEqual(o._loc_active_chains(items), [{"pid": 300, "label": "шаг 2/5"}])

    def test_synthetic_summary_card_not_a_parent(self):
        # synthetic-сводка/карточка pcloc-dec — НЕ родитель (не даёт ложную «план строится»).
        items = [{"id": 500, "from": self.F, "status": "new",
                  "task_text": "[сводка родитель 194] итог цепи"},
                 {"id": 501, "from": self.F, "status": "new",
                  "task_text": "[карточка родитель 194] событие"}]
        self.assertEqual(o._loc_active_chains(items), [])

    # ---- renderer: _contour_status ----

    def _find(self, name):
        return [42] if "userbot_listen" in name else []

    def test_status_empty_system_tiho(self):
        txt = o._contour_status(finder=self._find, items=[], revizor_state={})
        self.assertIn("🔧 В работе:", txt)
        self.assertIn("🟢 ТИХО", txt)

    def test_status_regression_other_sections_intact(self):
        # регресс: шапка и строки живости процессов на месте при любой секции «в работе».
        txt = o._contour_status(finder=self._find, items=[], revizor_state={})
        self.assertIn("📊 Статус контура", txt)
        self.assertIn("userbot: жив", txt)
        self.assertIn("pc_orchestrator", txt)

    def test_status_lists_live_chain_hides_ghost(self):
        items = [self._parent(194, "failed"), self._step(194, 1, 2, "failed"),   # призрак
                 self._parent(300, "done"), self._step(300, 2, 5, "in_progress")]  # живая
        txt = o._contour_status(finder=self._find, items=items, revizor_state={})
        self.assertIn("• цепь 300: шаг 2/5", txt)
        self.assertNotIn("194", txt)                    # призрак не показан
        self.assertNotIn("🟢 ТИХО", txt)                # есть живая работа

    def test_status_bridge_silent_not_tiho(self):
        # снимок None (Bridge молчит: _loc_fetch_items вернул None) → НЕ «ТИХО» (это было бы
        # ложью «работы нет»), а честное «очередь недоступна».
        save = o._loc_fetch_items
        self.addCleanup(lambda: setattr(o, "_loc_fetch_items", save))
        o._loc_fetch_items = lambda: None
        txt = o._contour_status(finder=self._find, revizor_state={})
        self.assertIn("очередь недоступна", txt)
        self.assertNotIn("🟢 ТИХО", txt)

    # ---- revizor tick: _revizor_tick_label ----

    def test_revizor_tick_from_mark(self):
        lbl = o._revizor_tick_label({"last_run": "2026-07-13T13:21:17.969802+00:00"})
        self.assertIn("последний тик 2026-07-13 13:21", lbl)
        self.assertIn("UTC", lbl)
        self.assertNotIn("неизвестно", lbl)

    def test_revizor_tick_no_mark(self):
        self.assertEqual(o._revizor_tick_label({}), "надзор: ревизор — тиков ещё не было")


class TestDirectChannel(Base):
    """Этап 1 (развязка 328-pc): прямой канал ПК↔Bridge для одиночек pc — enqueue+claim минуя
    девбот-в-splinter. Дополнительный путь, тема 328 остаётся рабочей (регресс)."""

    def test_direct_enqueue_then_claim_without_devbot(self):
        # никакого девбота/splinter в контуре — только Bridge. Прямой enqueue → демон claim'ит и исполняет.
        ok, nid, err = o.enqueue_pc_task("пк: сделай Y")
        self.assertTrue(ok)
        self.assertIsNotNone(nid)
        self.assertIsNone(err)
        self.assertEqual(self.fb.tasks[nid]["status"], "new")
        self.assertEqual(self.fb.tasks[nid]["lane"], "pc")
        self._claude(0, "сделал\nRESULT: готово")
        o.process_new()
        self.assertEqual(self.fb.tasks[nid]["status"], "done")     # принята и взята напрямую

    def test_direct_enqueue_forces_lane_pc(self):
        ok, nid, _ = o.enqueue_pc_task("пк: X")
        self.assertTrue(ok)
        self.assertEqual(self.fb.tasks[nid]["lane"], "pc")          # изоляция: строго своя полоса

    def test_direct_enqueue_empty_rejected(self):
        ok, nid, err = o.enqueue_pc_task("   ")
        self.assertFalse(ok)
        self.assertIsNone(nid)
        self.assertIn("пуст", err.lower())

    def test_direct_enqueue_bridge_error_reported(self):
        class _Down:
            def enqueue_task(self, frm, text, lane="pc"):
                return {"ok": False, "error": "URLError"}
        ok, nid, err = o.enqueue_pc_task("пк: X", bridge=_Down())
        self.assertFalse(ok)
        self.assertIsNone(nid)
        self.assertTrue(err)

    def test_splinter_down_direct_path_still_serves_single(self):
        # «Splinter down» на ПК = девбота/splinter в контуре нет вовсе; путь опирается ТОЛЬКО на
        # Bridge. Прямой enqueue принимает одиночку, демон её исполняет — инвариант развязки 328-pc.
        ok, nid, _ = o.enqueue_pc_task("пк: важная одиночка")
        self.assertTrue(ok)
        self._claude(0, "ок\nRESULT: сделано")
        o.process_new()
        self.assertEqual(self.fb.tasks[nid]["status"], "done")

    def test_regress_devbot_path_intact(self):
        # регресс: путь темы 328/девбота (задача положена «извне», как её кладёт девбот) цел
        tid = self.fb.add(status="new", task_text="сделай Z")
        self._claude(0, "готово\nRESULT: готово")
        o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")


class TestStuckSingles(Base):
    """Этап 2 (развязка 328-pc): ПК-side таймаут застрявших одиночек lane=pc в in_progress."""

    def test_stale_in_progress_reaped_failed(self):
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 120))
        o.process_stuck_singles()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("ПК-таймаут", self.fb.tasks[tid]["result"])

    def test_fresh_in_progress_kept(self):
        tid = self.fb.add(status="in_progress", updated=iso_ago(60))
        o.process_stuck_singles()
        self.assertEqual(self.fb.tasks[tid]["status"], "in_progress")   # живой прогон не трогаем

    def test_threshold_strictly_above_task_timeout(self):
        # порог реапа СТРОГО > жёсткого TASK_TIMEOUT (45 мин) → живой синхронный прогон не срубим
        self.assertGreater(o.PC_SINGLE_STALE, o.TASK_TIMEOUT)

    def test_other_lane_in_progress_untouched(self):
        # изоляция контуров: чужая полоса in_progress не реапится ПК-стороной
        tid = self.fb.add(status="in_progress", lane="vps", updated=iso_ago(o.PC_SINGLE_STALE + 600))
        o.process_stuck_singles()
        self.assertEqual(self.fb.tasks[tid]["status"], "in_progress")

    def test_no_updated_not_reaped(self):
        # updated=None → не реапим (fail-safe: не рубим задачу без метки времени наугад)
        tid = self.fb.add(status="in_progress", updated=None)
        o.process_stuck_singles()
        self.assertEqual(self.fb.tasks[tid]["status"], "in_progress")

    def test_stopflag_skips_reaper(self):
        o._stopped = lambda: True
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 600))
        o.process_stuck_singles()
        self.assertEqual(self.fb.tasks[tid]["status"], "in_progress")   # рубильник → реапер молчит

    def test_poll_once_includes_reaper(self):
        # реапер встроен в обычный цикл: орфан-одиночка добивается прямо в poll_once
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 300))
        with mock.patch.object(o, "_write_heartbeat", lambda: None):
            o.poll_once()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")


class TestFailReasonEvidence(Base):
    """Класс 30.07.2026 «статус врёт»: причина провала называется КОДОМ, а выполненная работа —
    словами. Живой повод (замер tmp/measure_status_truth.py за двое суток): из 6 разобранных
    провалов ПК-полосы ТРИ несли за собой коммит или запись в журнал — 54 (c00bb08+88eec9e и
    4 записи, потом таймаут подтверждения), 55 (a8f8822), 48 (артефакт+ASK); плюс 61 (коммит
    a3f75dd) с провалом от ЧУЖОЙ полосы. Голдены ниже — дословные причины этих провалов."""

    EV_COMMIT = {"commits": [("a3f75dd", "гард: чтение окружения живого процесса (PEB)")], "journal": []}
    EV_JOURNAL = {"commits": [], "journal": ["DONE RC 2026-07-30 20:15: гард PEB закрыт"]}

    def _ev(self, ev):
        return mock.patch.object(o, "_work_evidence", lambda since, until=None: ev)

    # --- словарь причин -------------------------------------------------------
    def test_four_named_reasons_are_four_distinct_codes(self):
        # ровно то, что просил владелец: «сейчас всё сваливается в одно слово»
        codes = {o.FAIL_APPROVAL_TIMEOUT, o.FAIL_HEARTBEAT_TIMEOUT,
                 o.FAIL_MODEL_REFUSAL, o.FAIL_EXEC_ERROR}
        self.assertEqual(len(codes), 4)
        for c in codes | {o.FAIL_RUN_TIMEOUT}:
            self.assertIn(c, o.FAIL_REASONS)
            self.assertTrue(o.FAIL_REASONS[c][0].strip(), c)     # у каждого кода есть имя словами

    def test_marks_stay_first_char(self):
        # ⏱/✋ первым символом — на них смотрят гейты самопочинки и надзора цепей
        for code in (o.FAIL_APPROVAL_TIMEOUT, o.FAIL_HEARTBEAT_TIMEOUT, o.FAIL_RUN_TIMEOUT):
            self.assertTrue(o.fail_result(code, "боль").startswith(o.TIMEOUT_MARK), code)
        self.assertTrue(o.fail_result(o.FAIL_MODEL_REFUSAL, "боль").startswith(o.MANUAL_MARK))
        self.assertFalse(o.fail_result(o.FAIL_EXEC_ERROR, "боль").startswith(o.TIMEOUT_MARK))

    def test_reason_code_is_greppable(self):
        r = o.fail_result(o.FAIL_APPROVAL_TIMEOUT, "боль")
        self.assertEqual(o.FAIL_CODE_RE.search(r).group(1), o.FAIL_APPROVAL_TIMEOUT)
        self.assertIn("таймаут подтверждения", r)

    # --- следы работы ---------------------------------------------------------
    def test_commit_in_window_names_work_done(self):
        with self._ev(self.EV_COMMIT):
            r = o.fail_result(o.FAIL_HEARTBEAT_TIMEOUT, "сердце молчит", since=iso_dt(600))
        self.assertIn(o.WORK_DONE_MARK, r)
        self.assertIn("a3f75dd", r)                    # улику НАЗЫВАЕМ, а не «работа была»
        self.assertIn("переделывать с нуля НЕ надо", r)
        self.assertIn("heartbeat_timeout", r)          # причина не растворилась в улике

    def test_journal_only_evidence_also_counts(self):
        # задача 61 записала журнал — этого достаточно, коммит не обязателен
        with self._ev(self.EV_JOURNAL):
            r = o.fail_result(o.FAIL_APPROVAL_TIMEOUT, "нет «да»", since=iso_dt(600))
        self.assertIn(o.WORK_DONE_MARK, r)
        self.assertIn("записей журнала 1", r)

    def test_no_evidence_says_so_explicitly(self):
        with self._ev({"commits": [], "journal": []}):
            r = o.fail_result(o.FAIL_EXEC_ERROR, "claude exit=1", since=iso_dt(600))
        self.assertNotIn(o.WORK_DONE_MARK, r)
        self.assertIn("Следов работы в окне", r)
        self.assertIn("коммитов 0, записей журнала 0", r)
        self.assertIn("claude exit=1", r)              # прежний диагноз на месте

    def test_unknown_window_is_named_not_guessed(self):
        # нет отметки claim → честно «окно неизвестно», следы НЕ собираем (иначе приписали бы чужое)
        def boom(*a, **k):
            raise AssertionError("следы не должны собираться без окна")
        with mock.patch.object(o, "_work_evidence", boom):
            r = o.fail_result(o.FAIL_APPROVAL_TIMEOUT, "нет «да»", since=None)
        self.assertIn("Окно работы неизвестно", r)
        self.assertNotIn(o.WORK_DONE_MARK, r)

    def test_evidence_failure_never_breaks_closing(self):
        # сбор улик упал → итог беднее, но строка есть и причина названа (путь провала fail-safe)
        with mock.patch.object(o, "_git_out", mock.Mock(side_effect=RuntimeError("git умер"))):
            with mock.patch.object(o, "COWORK_LEDGER", os.path.join(tempfile.mkdtemp(), "нет.ledger")):
                r = o.fail_result(o.FAIL_RUN_TIMEOUT, "таймаут 2700s", since=iso_dt(600))
        self.assertIn("run_timeout", r)
        self.assertIn("таймаут 2700s", r)

    # --- отметка старта (restart-proof окно) ---------------------------------
    def test_start_mark_survives_process_change(self):
        # закрывает задачу часто ДРУГОЙ процесс демона — отметка живёт на диске, а не в памяти
        t0 = iso_dt(1200)
        o._task_started_mark(7, now=t0)
        got = o._task_started_get(7)
        self.assertIsNotNone(got)
        self.assertLess(abs((got - t0).total_seconds()), 2)

    def test_start_mark_not_overwritten(self):
        # у одобренной задачи работа шла в ПЕРВОМ прогоне — вторая отметка окно бы обрезала
        first = iso_dt(3000)
        o._task_started_mark(8, now=first)
        o._task_started_mark(8, now=iso_dt(10))
        self.assertLess(abs((o._task_started_get(8) - first).total_seconds()), 2)

    def test_start_mark_capped(self):
        for i in range(o.TASK_START_KEEP + 25):
            o._task_started_mark(1000 + i, now=iso_dt(o.TASK_START_KEEP + 25 - i))
        self.assertLessEqual(len(o._task_started_read()), o.TASK_START_KEEP)

    def test_missing_mark_is_none_not_crash(self):
        self.assertIsNone(o._task_started_get(999999))

    def test_claim_writes_start_mark(self):
        tid = self.fb.add(status="new")
        self._claude(0, "ок\nRESULT: ок")
        o.process_new()
        self.assertIsNotNone(o._task_started_get(tid))   # окно есть с первой секунды задачи

    # --- сбор улик из живых источников ---------------------------------------
    def test_git_commits_parsed(self):
        out = "a3f75dd\x1fгард PEB\nfa54ce7\x1fгард sqlite"
        with mock.patch.object(o, "_git_out", lambda args: out):
            rows = o._git_commits_between(iso_dt(600), iso_dt(0))
        self.assertEqual(rows, [("a3f75dd", "гард PEB"), ("fa54ce7", "гард sqlite")])

    def test_journal_ledger_window_respected(self):
        path = os.path.join(tempfile.mkdtemp(), "cowork_log.ledger")
        with open(path, "w", encoding="utf-8") as f:
            for age, line in ((5000, "старая"), (600, "в окне"), (0, "свежая")):
                f.write(json.dumps({"ts": iso_dt(age).isoformat(), "line": line},
                                   ensure_ascii=False) + "\n")
        rows = o._journal_writes_between(iso_dt(1200), iso_dt(300), path=path)
        self.assertEqual(rows, ["в окне"])

    def test_journal_ledger_missing_is_empty(self):
        self.assertEqual(o._journal_writes_between(iso_dt(600), iso_dt(0),
                                                   path=os.path.join(tempfile.mkdtemp(), "нет")), [])

    # --- сквозные: четыре причины на четырёх боевых путях ---------------------
    def test_approval_timeout_end_to_end(self):
        # ЖИВОЙ случай задачи 54: работа сделана, коммит есть, а закрытие сорвал таймаут «да»
        tid = self.fb.add(status="needs_approval", updated=iso_ago(4000))
        o._task_started_mark(tid, now=iso_dt(4200))
        with self._ev(self.EV_COMMIT):
            o.process_approval_timeouts()
        r = self.fb.tasks[tid]["result"]
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("причина=approval_timeout", r)
        self.assertIn(o.WORK_DONE_MARK, r)
        self.assertIn("30 мин", r)                       # прежний диагноз владельцу цел

    def test_stuck_single_reason_is_heartbeat(self):
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 120))
        with self._ev(self.EV_JOURNAL):
            o.process_stuck_singles()
        r = self.fb.tasks[tid]["result"]
        self.assertIn("причина=heartbeat_timeout", r)
        self.assertIn("ПК-таймаут", r)                   # прежний признак реапера цел
        self.assertIn(o.WORK_DONE_MARK, r)

    def test_run_timeout_reason(self):
        tid = self.fb.add(status="new")
        self._claude(raise_timeout=True)
        o.process_new()
        self.assertIn("причина=run_timeout", self.fb.tasks[tid]["result"])

    def test_exec_error_reason(self):
        tid = self.fb.add(status="new")
        self._claude(1, "", "API Error: 529 Overloaded")
        o.process_new()
        r = self.fb.tasks[tid]["result"]
        self.assertIn("причина=exec_error", r)
        self.assertIn("claude exit=1", r)

    def test_selfheal_gate_untouched_by_new_text(self):
        # ⏱-гейт: думатель по-прежнему НЕ чинит таймауты — даже когда итог рассказал о работе
        o._selfheal_on = lambda: True
        with self._ev(self.EV_COMMIT):
            txt = o.fail_result(o.FAIL_RUN_TIMEOUT, "таймаут 2700s", since=iso_dt(600))
        self.assertFalse(o._maybe_selfheal(9, "обычная задача", txt, frm="Filipp-pc"))

    def test_human_headline_stops_lying(self):
        with self._ev(self.EV_COMMIT):
            txt = o.fail_result(o.FAIL_HEARTBEAT_TIMEOUT, "сердце молчит", since=iso_dt(600))
        self.assertIn("работа выполнена", o._human("failed", 61, txt))
        self.assertIn("провалена", o._human("failed", 61, "пустой вывод claude"))


class TestLocalDec(Base):
    """Локальный дирижёр-декомпозер (флаг PC_LOCAL_DEC, порт мозга VPS — шаг 2/7 родителя 185).
    Всё замокано: думатель/кондуктор (_thinker_exec) не дёргает реальный claude."""

    def setUp(self):
        super().setUp()
        os.environ["PC_LOCAL_DEC"] = "1"

    def tearDown(self):
        os.environ.pop("PC_LOCAL_DEC", None)
        super().tearDown()

    def _add_parent(self, text="сделай большую фичу X: A, B и C"):
        tid = self.fb.add(task_text=text)
        self.fb.tasks[tid]["from"] = o.PC_LOCAL_DEC_FROM
        return tid

    # --- детект родителя ---

    def test_flag_off_never_matches(self):
        os.environ["PC_LOCAL_DEC"] = "0"
        self.assertFalse(o._is_local_dec_parent(o.PC_LOCAL_DEC_FROM, "крупное ТЗ"))
        os.environ.pop("PC_LOCAL_DEC", None)   # нет флага = тоже off
        self.assertFalse(o._is_local_dec_parent(o.PC_LOCAL_DEC_FROM, "крупное ТЗ"))

    def test_parent_match_requires_from_and_no_step_marker(self):
        self.assertTrue(o._is_local_dec_parent(o.PC_LOCAL_DEC_FROM, "крупное ТЗ"))
        # шаг цепи (маркер [шаг i/N родитель id]) — НЕ родитель
        self.assertFalse(o._is_local_dec_parent(o.PC_LOCAL_DEC_FROM, "[шаг 2/5 родитель 7] сделай"))
        # чужая метка from — не наш родитель
        self.assertFalse(o._is_local_dec_parent("Filipp-pc-dev", "крупное ТЗ"))
        self.assertFalse(o._is_local_dec_parent("", "крупное ТЗ"))

    # --- парс плана (_PLAN_LINE_RE) ---

    def test_plan_parse_dots_parens_and_garbage(self):
        out = "1. первый шаг\n2) второй шаг\nмусорная строка без номера\n 3.  третий шаг\n"
        self.assertEqual(o._plan_steps(out), ["первый шаг", "второй шаг", "третий шаг"])

    def test_plan_parse_empty_and_none(self):
        self.assertEqual(o._plan_steps(""), [])
        self.assertEqual(o._plan_steps(None), [])
        self.assertEqual(o._plan_steps("Вот план:\nникаких номеров"), [])

    # --- 🔴-пометка красных шагов ---

    def test_red_note_marks_red_steps(self):
        note = o._dec_red_note(["поправь код в suggest.py", "clasp redeploy прод-деплой",
                                "запиши строку в Лист 1"])
        self.assertIn("🔴 красные шаги: 2, 3", note)
        self.assertTrue(note.endswith("\n"))

    def test_red_note_empty_for_clean_plan(self):
        self.assertEqual(o._dec_red_note(["поправь код", "прогони тесты"]), "")

    def test_red_note_is_display_only_not_plan_line(self):
        # строка с 🔴 не матчит _PLAN_LINE_RE → restart-proof парс плана из result не ломается
        note = o._dec_red_note(["clasp redeploy"])
        self.assertIsNone(o._PLAN_LINE_RE.match(note.splitlines()[0]))

    # --- планировщик: happy path ---

    def test_plan_happy_path_done_with_plan_in_result(self):
        tid = self._add_parent()
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: "1. шаг A\n2. шаг B"):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "done")
        self.assertIn("🧩 Декомпозиция (локальный дирижёр PC): 2 шагов", t["result"])
        self.assertIn("1. шаг A", t["result"])
        self.assertIn("2. шаг B", t["result"])

    def test_planner_gets_ported_preamble_and_parent_text(self):
        tid = self._add_parent(text="построй фичу Y")
        seen = {}

        def fake_exec(prompt, timeout, tag):
            seen["prompt"], seen["timeout"], seen["tag"] = prompt, timeout, tag
            return "1. a\n2. b"

        with mock.patch.object(o, "_thinker_exec", fake_exec):
            o.process_new()
        self.assertTrue(seen["prompt"].startswith(o.PLANNER_PREAMBLE))
        self.assertIn("D:\\turbobaby-bot", seen["prompt"])       # порт под ПК-репо
        self.assertNotIn("/root/turbobaby-manager-bot", seen["prompt"])  # НЕ VPS-путь
        self.assertTrue(seen["prompt"].endswith("построй фичу Y"))
        self.assertEqual(seen["timeout"], o.PC_DEC_PLAN_TIMEOUT)
        self.assertEqual(self.fb.tasks[tid]["status"], "done")

    # --- урок 166: красное в ТЗ не валит план ---

    def test_lesson166_red_in_output_ignored_when_plan_present(self):
        out = ("1. поправь код\n2. clasp redeploy прод\n"
               "NEEDS_APPROVAL: op=other | какой-то хвост от модели")
        tid = self._add_parent()
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: out):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "done")                     # план есть → NA-хвост игнор
        self.assertIn("🔴 красные шаги: 2", t["result"])          # красный шаг помечен

    def test_pure_red_parent_failed_not_button(self):
        # чисто-красное ТЗ (план пуст + NEEDS_APPROVAL) → честный failed с картой, НЕ needs_approval
        tid = self._add_parent(text="задеплой прод")
        with mock.patch.object(o, "_thinker_exec",
                               lambda p, t, tag: "NEEDS_APPROVAL: op=other | деплой прода · red"):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "failed")
        self.assertIn("планировщик needs_approval", t["result"])

    # --- фейл-ветки ---

    def test_empty_plan_failed_with_tail(self):
        tid = self._add_parent()
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: "не могу, расплывчато"):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "failed")
        self.assertIn("план пуст", t["result"])

    def test_too_many_steps_failed(self):
        out = "\n".join(f"{i}. шаг {i}" for i in range(1, o.MAX_STEPS + 2))   # 9 > 8
        tid = self._add_parent()
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: out):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "failed")
        self.assertIn("упрости ТЗ", t["result"])

    def test_thinker_failure_failed(self):
        tid = self._add_parent()
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: None):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "failed")
        self.assertIn("планировщик локальной декомпозиции не отработал", t["result"])

    # --- ОБЯЗАТЕЛЬНЫЙ финальный смоук цепи домена suggest/delivery/moderation (родитель 92, шаг 4/6) ---

    def test_domain_detector_matches_suggest_delivery_moderation(self):
        self.assertTrue(o._chain_touches_suggest_domain(["поправь цену в suggest.py"]))
        self.assertTrue(o._chain_touches_suggest_domain(["правка delivery: зона Раваи"]))
        self.assertTrue(o._chain_touches_suggest_domain(["moderation_core: фикс правила"]))
        self.assertTrue(o._chain_touches_suggest_domain(["почини модерацию алертов"]))
        self.assertTrue(o._chain_touches_suggest_domain(["пересчёт доставки по зоне"]))
        # не домен → False (смоук не навязываем цепям, которых он не касается)
        self.assertFalse(o._chain_touches_suggest_domain(["рефактор pc_agent", "прогони тесты"]))
        self.assertFalse(o._chain_touches_suggest_domain([]))
        self.assertFalse(o._chain_touches_suggest_domain(None))

    def test_append_smoke_step_added_last_for_domain_chain(self):
        steps = o._append_smoke_step(["поправь цену в suggest.py", "прогони тесты"])
        self.assertEqual(len(steps), 3)
        self.assertTrue(o._is_smoke_step(steps[-1]))                     # смоук — ПОСЛЕДНИЙ шаг
        self.assertIn("runLiveSmoke", steps[-1])
        self.assertEqual(steps[:2], ["поправь цену в suggest.py", "прогони тесты"])  # ТЗ не тронуто

    def test_append_smoke_step_skipped_for_unrelated_chain(self):
        steps = ["рефактор pc_agent.py", "обнови README"]
        self.assertEqual(o._append_smoke_step(steps), steps)            # не домен → без смоука
        self.assertIsNot(o._append_smoke_step(steps), steps)           # исходный список не мутируем

    def test_append_smoke_step_idempotent_no_double(self):
        once = o._append_smoke_step(["фикс delivery зон"])
        twice = o._append_smoke_step(once)
        self.assertEqual(once, twice)                                   # повтор не дублирует смоук
        self.assertEqual(sum(o._is_smoke_step(s) for s in twice), 1)

    def test_planner_auto_adds_smoke_step_for_domain_plan(self):
        # ЯДРО (родитель 92): план, трогающий suggest → авто-финальный смоук в result родителя
        tid = self._add_parent(text="почини прайс в suggest")
        with mock.patch.object(o, "_thinker_exec",
                               lambda p, t, tag: "1. поправь suggest.py\n2. прогони тесты"):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "done")
        self.assertIn("3 шагов", t["result"])                          # 2 шага ТЗ + обязательный смоук
        self.assertIn("3. " + o.SMOKE_STEP_MARK, t["result"])          # смоук — последним номером

    def test_planner_no_smoke_for_unrelated_plan(self):
        tid = self._add_parent(text="рефактор pc_agent")
        with mock.patch.object(o, "_thinker_exec",
                               lambda p, t, tag: "1. правка pc_agent.py\n2. прогони тесты"):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "done")
        self.assertNotIn(o.SMOKE_STEP_MARK, t["result"])
        self.assertIn("2 шагов", t["result"])

    def test_smoke_exempt_from_max_steps_cap(self):
        # план ровно MAX_STEPS шагов ТЗ (домен) + смоук = MAX_STEPS+1, но по потолку НЕ валится
        out = "\n".join(f"{i}. правка suggest часть {i}" for i in range(1, o.MAX_STEPS + 1))
        tid = self._add_parent()
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: out):
            o.process_new()
        t = self.fb.tasks[tid]
        self.assertEqual(t["status"], "done")                          # смоук сверх потолка — не failed
        self.assertIn(f"{o.MAX_STEPS + 1} шагов", t["result"])
        self.assertIn(o.SMOKE_STEP_MARK, t["result"])

    # --- прямое исполнение смоук-шага: провал смоука → шаг failed (родитель 92) ---

    def test_exec_smoke_step_failed_marks_failed(self):
        r = {"status": "failed", "card": "ОЖИДАНИЕ: доставка 590\nФАКТ: доставка 0"}
        status, res = o._exec_smoke_step(smoke_fn=lambda: r)
        self.assertEqual(status, "failed")
        self.assertIn("ПРОВАЛЕН", res)
        self.assertIn("ОЖИДАНИЕ: доставка 590", res)                    # карточка ОЖИДАНИЕ/ФАКТ в result

    def test_exec_smoke_step_passed_and_skipped_are_done(self):
        for st in ("passed", "skipped"):
            status, res = o._exec_smoke_step(smoke_fn=lambda st=st: {"status": st})
            self.assertEqual(status, "done", st)
            self.assertIn(st, res)

    def test_exec_smoke_step_crash_is_failed(self):
        def boom():
            raise RuntimeError("контур молчит")
        status, res = o._exec_smoke_step(smoke_fn=boom)
        self.assertEqual(status, "failed")                             # недостоверный прогон → failed
        self.assertIn("RuntimeError", res)

    def test_exec_smoke_step_unknown_status_is_failed(self):
        status, _ = o._exec_smoke_step(smoke_fn=lambda: {"status": "weird"})
        self.assertEqual(status, "failed")                             # неизвестный статус → провал

    def test_process_new_routes_smoke_step_direct_not_headless(self):
        # смоук-шаг в очереди → демон исполняет САМ (без headless run_task); провал → шаг failed
        tid = self.fb.add(task_text=f"[шаг 3/3 родитель 92] {o.SMOKE_STEP_TEXT}")
        self.fb.tasks[tid]["from"] = o.PC_LOCAL_DEC_FROM
        boom = mock.Mock(side_effect=AssertionError("headless run_task для смоука не зовём"))
        with mock.patch.object(o, "run_task", boom), \
             mock.patch.object(o, "_run_live_smoke", lambda: {"status": "failed", "card": "дифф"}):
            o.process_new()
        boom.assert_not_called()
        self.assertEqual(self.fb.tasks[tid]["status"], "failed")
        self.assertIn("ПРОВАЛЕН", self.fb.tasks[tid]["result"])

    # --- маршрутизация process_new ---

    def test_process_new_routes_parent_to_planner_not_run_task(self):
        tid = self._add_parent()
        boom = mock.Mock(side_effect=AssertionError("run_task не должен зваться для родителя"))
        with mock.patch.object(o, "run_task", boom), \
             mock.patch.object(o, "_thinker_exec", lambda p, t, tag: "1. a\n2. b"):
            o.process_new()
        boom.assert_not_called()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")

    def test_flag_off_parent_goes_old_path_run_task(self):
        # PC_LOCAL_DEC=0 → родитель идёт ПРЕЖНИМ headless-путём (поведение байт-в-байт)
        os.environ["PC_LOCAL_DEC"] = "0"
        tid = self._add_parent()
        with mock.patch.object(o, "run_task", lambda *a, **k: ("done", "RESULT: ок")), \
             mock.patch.object(o, "_thinker_exec",
                               mock.Mock(side_effect=AssertionError("планировщик не должен зваться"))):
            o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertEqual(self.fb.tasks[tid]["result"], "RESULT: ок")

    def test_step_marked_task_not_planned(self):
        # задача С маркером шага от того же from → НЕ родитель, идёт обычным путём
        tid = self.fb.add(task_text="[шаг 1/3 родитель 9] сделай кусок")
        self.fb.tasks[tid]["from"] = o.PC_LOCAL_DEC_FROM
        with mock.patch.object(o, "run_task", lambda *a, **k: ("done", "RESULT: кусок готов")), \
             mock.patch.object(o, "_thinker_exec",
                               mock.Mock(side_effect=AssertionError("планировщик не должен зваться"))):
            o.process_new()
        self.assertEqual(self.fb.tasks[tid]["status"], "done")


class TestLocalDecChain(Base):
    """Sequential-релиз и надзор локальной цепи (шаг 3/7 родителя 185): максимум один шаг цепи
    в очереди, состояние ТОЛЬКО из очереди (restart-proof), synthetic-сводки/карточки прямым
    каналом (enqueue→claim→done). Всё замокано."""

    def setUp(self):
        super().setUp()
        os.environ["PC_LOCAL_DEC"] = "1"
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()

    def tearDown(self):
        os.environ.pop("PC_LOCAL_DEC", None)
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()
        super().tearDown()

    # --- обвязка сцены ---

    def _mk_parent_done(self, plan_lines):
        pid = self.fb.add(status="done", task_text="крупное ТЗ")
        self.fb.tasks[pid]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[pid]["result"] = ("🧩 Декомпозиция (локальный дирижёр PC): "
                                        f"{len(plan_lines)} шагов — исполняю ПО ОДНОМУ.\n"
                                        + "\n".join(plan_lines) + "\nШаг 1 уже в очереди.")
        return pid

    def _mk_step(self, pid, i, n, status="done", text="кусок", result="RESULT: ок", k=0):
        mark = f"[коррекция плана {k}] " if k else ""
        tid = self.fb.add(status=status, task_text=f"[шаг {i}/{n} родитель {pid}] {mark}{text}")
        self.fb.tasks[tid]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[tid]["result"] = result
        return tid

    def _news(self):
        return [t for t in self.fb.tasks.values() if t["status"] == "new"]

    def _summaries(self, pid):
        return [t for t in self.fb.tasks.values()
                if str(t.get("task_text") or "").startswith(f"[сводка родитель {pid}]")]

    def _cards(self, pid):
        return [t for t in self.fb.tasks.values()
                if str(t.get("task_text") or "").startswith(f"[карточка родитель {pid}]")]

    # --- маркеры/regex байт-в-байт из порт-спеки (docs/dec_port_spec.md, раздел 2) ---

    def test_markers_byte_identical_to_spec(self):
        self.assertEqual(o._STEP_RE.pattern, r"^\[шаг (\d+)/(\d+) родитель (\d+)\]")
        self.assertEqual(o._SUM_RE.pattern, r"^\[сводка родитель (\d+)\]")
        self.assertEqual(o._HEAL_RE.pattern, r"\[самопочинка шага (\d+), попытка (\d+)\]")
        self.assertEqual(o._HEAL_TASK_RE.pattern, r"^\s*\[самопочинка задачи (\d+), попытка (\d+)\]")
        self.assertEqual(o._ADAPT_MARK_RE.pattern, r"\[коррекция плана (\d+)\]")
        self.assertEqual(o._ADAPT_CARD_RE.pattern, r"^\[коррекция плана родитель (\d+)\]")
        self.assertEqual(o._CARD_RE.pattern, r"^\[карточка родитель (\d+)\]")
        self.assertEqual(o._ADAPT_BASE_RE.pattern, r"после шага (\d+)")
        self.assertEqual(o.ADAPT_REPLACED_MARK, "♻️ заменён коррекцией плана")
        self.assertEqual(o.ADAPT_FINISH_MARK, "⏭ закрыт досрочно")

    def test_heal_marker_searched_after_step_marker(self):
        # маркер самопочинки идёт ПОСЛЕ [шаг i/N] → у шага regex search, у одиночки — якорь ^
        t = "[шаг 2/5 родитель 7] [самопочинка шага 2, попытка 1] исправленный текст"
        self.assertTrue(o._HEAL_RE.search(t))
        self.assertFalse(o._HEAL_TASK_RE.match(t))

    # --- релиз шага 1 из планировщика (crash-окно: шаг ДО закрытия родителя) ---

    def test_plan_releases_step1_before_parent_done(self):
        pid = self.fb.add(task_text="крупное ТЗ")
        self.fb.tasks[pid]["from"] = o.PC_LOCAL_DEC_FROM
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: "1. шаг A\n2. шаг B"):
            o.process_new()
        self.assertEqual(self.fb.tasks[pid]["status"], "done")
        news = self._news()
        self.assertEqual(len(news), 1)
        st1 = news[0]
        self.assertEqual(st1["task_text"], f"[шаг 1/2 родитель {pid}] шаг A")
        self.assertEqual(st1["from"], o.PC_LOCAL_DEC_FROM)   # VPS-надзор (Filipp-pc-dec) цепь НЕ видит
        self.assertEqual(st1["lane"], "pc")
        self.assertIn("исполняю ПО ОДНОМУ", self.fb.tasks[pid]["result"])
        self.assertIn("1. шаг A", self.fb.tasks[pid]["result"])   # restart-proof источник плана

    def test_step1_enqueue_fail_parent_stays_in_progress(self):
        pid = self.fb.add(task_text="крупное ТЗ")
        self.fb.tasks[pid]["from"] = o.PC_LOCAL_DEC_FROM
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: "1. a\n2. b"), \
             mock.patch.object(self.fb, "enqueue_task",
                               lambda *a, **k: {"ok": False, "error": "bridge down"}):
            o.process_new()
        # шаг 1 не встал → родитель НЕ закрыт (in_progress; зависание добьёт ПК-ливнесс)
        self.assertEqual(self.fb.tasks[pid]["status"], "in_progress")

    # --- sequential-релиз: максимум один шаг цепи в очереди ---

    def test_done_step_releases_exactly_next_step(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B", "3. шаг C"])
        self._mk_step(pid, 1, 3, status="done", text="шаг A")
        o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"], f"[шаг 2/3 родитель {pid}] шаг B")
        self.assertEqual(news[0]["from"], o.PC_LOCAL_DEC_FROM)

    def test_waiting_step_releases_nothing(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        for st in ("new", "in_progress", "needs_approval", "approved"):
            self.fb.tasks.clear()
            pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
            self._mk_step(pid, 1, 2, status=st, text="шаг A")
            before = len(self.fb.tasks)
            o.process_local_chains()
            self.assertEqual(len(self.fb.tasks), before, f"статус {st} породил задачу")
            self.assertEqual(self._summaries(pid), [], f"статус {st} породил сводку")

    def test_release_keeps_correction_origin_marker(self):
        self.assertTrue(o._loc_release(5, 2, 3, "текст", k=2))
        news = self._news()
        self.assertEqual(news[-1]["task_text"], "[шаг 2/3 родитель 5] [коррекция плана 2] текст")
        self.assertTrue(o._loc_release(5, 3, 3, "хвост"))
        self.assertEqual(self._news()[-1]["task_text"], "[шаг 3/3 родитель 5] хвост")

    # --- финал цепи и сводка (synthetic прямым каналом enqueue→claim→done) ---

    def test_final_step_done_posts_summary_synthetic(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done", result="RESULT: A готов")
        self._mk_step(pid, 2, 2, status="done", result="RESULT: B готов")
        o.process_local_chains()
        sums = self._summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertEqual(sums[0]["status"], "done")   # прямой канал: enqueue→claim→done одним тиком
        self.assertIn(f"🧩 Сводка декомпозиции (родитель {pid}, локальный дирижёр): 2/2 шагов done",
                      sums[0]["result"])
        self.assertIn("✅ шаг 1/2: RESULT: A готов", sums[0]["result"])
        self.assertIn("✅ шаг 2/2: RESULT: B готов", sums[0]["result"])

    def test_summary_posts_cowork_note(self):
        # шаг 5/5 родителя 221: постановка сводки пишет NOTE в cowork_log
        # «сводка родитель <pid>: d/n done» — журнал замокан, проверяем формат и значения (d≠n)
        cows = []
        o._cowork = lambda line: cows.append(line)
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done", result="RESULT: A готов")
        self._mk_step(pid, 2, 2, status="failed", result="B упал")   # halt: 1 done из 2
        o.process_local_chains()
        self.assertEqual(len(self._summaries(pid)), 1)                # сводка встала в очередь
        self.assertIn(f"сводка родитель {pid}: 1/2 done", cows)       # NOTE: формат + d/n значения
        self.assertEqual(sum(1 for c in cows if c.startswith(f"сводка родитель {pid}:")), 1)

    def test_summary_idempotent_across_restart(self):
        pid = self._mk_parent_done(["1. шаг A"])
        self._mk_step(pid, 1, 1, status="done")
        o.process_local_chains()
        self.assertEqual(len(self._summaries(pid)), 1)
        o._loc_summarized.clear()                     # эмуляция рестарта демона (кэш пуст)
        o.process_local_chains()                      # restart-proof: скан очереди видит сводку
        self.assertEqual(len(self._summaries(pid)), 1)
        self.assertIn(pid, o._loc_summarized)

    def test_failed_step_halts_chain_with_summary(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="сломалось")
        o.process_local_chains()
        self.assertEqual(len(self._news()), 0)        # halt: шаг 2 НЕ релизнут
        sums = self._summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("❌ шаг 1/2: сломалось", sums[0]["result"])
        self.assertIn("есть упавшие/пропущенные", sums[0]["result"])

    def test_summary_dedups_rebirth_by_id(self):
        # дубль номера (провал + перерождение самопочинки) → в сводке последняя запись по id
        pid = self._mk_parent_done(["1. шаг A"])
        self._mk_step(pid, 1, 1, status="failed", result="упал")
        self._mk_step(pid, 1, 1, status="done", text="[самопочинка шага 1, попытка 1] фикс",
                      result="RESULT: со второй попытки")
        o.process_local_chains()
        sums = self._summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("1/1 шагов done", sums[0]["result"])
        self.assertIn("✅ шаг 1/1: RESULT: со второй попытки", sums[0]["result"])
        self.assertNotIn("❌", sums[0]["result"])

    # --- restart-proof: план ТОЛЬКО из очереди ---

    def test_plan_restored_from_queue_with_adapt_cards(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B", "3. шаг C"])
        card = self.fb.add(status="done",
                           task_text=f"[коррекция плана родитель {pid}] после шага 1 (K=1)")
        self.fb.tasks[card]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[card]["result"] = "🧭 новый остаток:\n2. шаг B2\n3. шаг C2"
        plan, k_cnt, last_base = o._loc_current_plan(pid)
        self.assertEqual(plan, {1: ("шаг A", 0), 2: ("шаг B2", 1), 3: ("шаг C2", 1)})
        self.assertEqual((k_cnt, last_base), (1, 1))
        # done скорректированного шага 2 → релиз шага 3 с K-происхождением
        self._mk_step(pid, 2, 3, status="done", text="шаг B2", k=1)
        o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"], f"[шаг 3/3 родитель {pid}] [коррекция плана 1] шаг C2")

    def test_plan_not_restored_warns_and_halts(self):
        # родителя в done нет (очередь потеряла план) → ⚠️-карточка + сводка, релиза нет
        self._mk_step(77, 1, 2, status="done")
        o.process_local_chains()
        self.assertEqual(len(self._news()), 0)
        cards = self._cards(77)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["status"], "done")
        self.assertIn("⚠️ план родителя 77 не восстановился", cards[0]["result"])
        self.assertEqual(len(self._summaries(77)), 1)

    def test_orphaned_empty_adapt_card_is_keep(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        card = self.fb.add(status="done",
                           task_text=f"[коррекция плана родитель {pid}] после шага 1 (K=1)")
        self.fb.tasks[card]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[card]["result"] = "🧭 без нумерованного остатка"
        plan, _, last_base = o._loc_current_plan(pid)
        self.assertEqual(plan, {1: ("шаг A", 0), 2: ("шаг B", 0)})   # fail-safe keep
        self.assertIsNone(last_base)

    def test_fetch_error_skips_cycle_entirely(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done")
        real = self.fb.get_pending

        def flaky(status, lane="pc"):
            if status == "failed":
                return {"ok": False, "error": "quota"}
            return real(status, lane)

        with mock.patch.object(self.fb, "get_pending", flaky):
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)        # частичная картина → цикл пропущен целиком

    # --- изоляция: чужое не трогаем, флаг off = noop ---

    def test_vps_theatre_chains_untouched(self):
        tid = self.fb.add(status="done", task_text="[шаг 1/2 родитель 50] кусок VPS-театра")
        self.fb.tasks[tid]["from"] = "Filipp-pc-dec"   # цепь VPS-надзора — НЕ наша
        single = self.fb.add(status="done", task_text="одиночка")
        self.fb.tasks[single]["from"] = "Filipp-pc-dev"
        before = {k: dict(v) for k, v in self.fb.tasks.items()}
        o.process_local_chains()
        self.assertEqual(self.fb.tasks, before)        # ни релизов, ни сводок, ни complete

    def test_flag_off_supervision_noop(self):
        os.environ["PC_LOCAL_DEC"] = "0"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done")
        before = {k: dict(v) for k, v in self.fb.tasks.items()}
        o.process_local_chains()
        self.assertEqual(self.fb.tasks, before)

    def test_stop_flag_supervision_noop(self):
        o._stopped = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done")
        before = {k: dict(v) for k, v in self.fb.tasks.items()}
        o.process_local_chains()
        self.assertEqual(self.fb.tasks, before)

    # --- шаг цепи не угоняется одиночной самопочинкой ---

    def test_chain_step_failure_not_hijacked_by_single_selfheal(self):
        o._selfheal_on = lambda: True
        boom = mock.Mock(side_effect=AssertionError("одиночная самопочинка не должна зваться"))
        with mock.patch.object(o, "_maybe_task_selfheal", boom):
            self.assertFalse(o._maybe_selfheal(9, "[шаг 1/2 родитель 5] x", "err",
                                               frm=o.PC_LOCAL_DEC_FROM))
        boom.assert_not_called()

    def test_single_task_selfheal_path_intact(self):
        # регресс: обычная одиночка (не pcloc-dec) идёт в самопочинку как раньше
        o._selfheal_on = lambda: True
        with mock.patch.object(o, "_maybe_task_selfheal", lambda *a, **k: True) as _:
            self.assertTrue(o._maybe_selfheal(9, "обычная задача", "err", frm="Filipp-pc-dev"))

    # --- осиротевшие synthetic не исполняются headless'ом ---

    def test_orphan_synthetic_finalized_not_executed(self):
        for text, mark in ((f"[сводка родитель 7] сводный отчёт по шагам", "🧩 Сводка"),
                           (f"[коррекция плана родитель 7] после шага 1 (K=1)", "🧭 карточка"),
                           (f"[карточка родитель 7] событие локальной цепи", "🃏 карточка")):
            self.fb.tasks.clear()
            tid = self.fb.add(task_text=text)
            self.fb.tasks[tid]["from"] = o.PC_LOCAL_DEC_FROM
            boom = mock.Mock(side_effect=AssertionError("run_task не должен зваться"))
            planner = mock.Mock(side_effect=AssertionError("планировщик не должен зваться"))
            with mock.patch.object(o, "run_task", boom), \
                 mock.patch.object(o, "_thinker_exec", planner):
                o.process_new()
            self.assertEqual(self.fb.tasks[tid]["status"], "done", text)
            self.assertIn(mark, self.fb.tasks[tid]["result"])

    # --- сквозной happy-path: план → шаги по одному → сводка ---

    def test_end_to_end_two_step_chain(self):
        pid = self.fb.add(task_text="сделай фичу из двух кусков")
        self.fb.tasks[pid]["from"] = o.PC_LOCAL_DEC_FROM
        with mock.patch.object(o, "_thinker_exec", lambda p, t, tag: "1. кусок A\n2. кусок B"):
            o.process_new()                            # план + релиз шага 1
        self._claude(0, "сделано\nRESULT: сделано")
        o.process_new()                                # исполняем шаг 1 (обычный headless-путь)
        o.process_local_chains()                       # done шага 1 → релиз шага 2
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"], f"[шаг 2/2 родитель {pid}] кусок B")
        o.process_new()                                # исполняем шаг 2
        o.process_local_chains()                       # финал → сводка
        sums = self._summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("2/2 шагов done", sums[0]["result"])
        # инвариант доказан: в очереди никогда не было двух new-шагов цепи разом
        self.assertEqual(len(self._news()), 0)


class TestLocalDecSelfhealAdapt(Base):
    """Шаг 4/7 родителя 185: самопочинка шага локальной цепи (думатель, РОВНО 1 перерождение,
    ⏱-гейт, «отклонено Филиппом»/конверты не трогаются) + адаптация плана после done-шага
    (keep/adjust/finish, лимит 2 коррекции restart-proof, fail-safe = keep, на последнем шаге
    думатель не зовётся). Всё замокано, реальный claude не дёргается."""

    def setUp(self):
        super().setUp()
        os.environ["PC_LOCAL_DEC"] = "1"
        os.environ.pop("PLAN_ADAPT", None)
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()
        o._loc_adapted.clear()

    def tearDown(self):
        for k in ("PC_LOCAL_DEC", "PLAN_ADAPT"):
            os.environ.pop(k, None)
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()
        o._loc_adapted.clear()
        super().tearDown()

    # --- обвязка сцены (как в TestLocalDecChain) ---

    def _mk_parent_done(self, plan_lines, goal="крупное ТЗ"):
        pid = self.fb.add(status="done", task_text=goal)
        self.fb.tasks[pid]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[pid]["result"] = ("🧩 Декомпозиция (локальный дирижёр PC): "
                                        f"{len(plan_lines)} шагов.\n" + "\n".join(plan_lines))
        return pid

    def _mk_step(self, pid, i, n, status="done", text="кусок", result="RESULT: ок", k=0):
        mark = f"[коррекция плана {k}] " if k else ""
        tid = self.fb.add(status=status, task_text=f"[шаг {i}/{n} родитель {pid}] {mark}{text}")
        self.fb.tasks[tid]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[tid]["result"] = result
        return tid

    def _news(self):
        return [t for t in self.fb.tasks.values() if t["status"] == "new"]

    def _summaries(self, pid):
        return [t for t in self.fb.tasks.values()
                if str(t.get("task_text") or "").startswith(f"[сводка родитель {pid}]")]

    def _cards(self, pid):
        return [t for t in self.fb.tasks.values()
                if str(t.get("task_text") or "").startswith(f"[карточка родитель {pid}]")]

    def _adapt_cards(self, pid):
        return [t for t in self.fb.tasks.values()
                if str(t.get("task_text") or "").startswith(f"[коррекция плана родитель {pid}]")]

    def _thinker(self, reply):
        """Мок думателя: reply=строка (ответ) / None (сбой) / callable(prompt)->строка.
        Копит промпты в self.prompts."""
        self.prompts = []

        def fake(prompt, timeout, tag):
            self.prompts.append(prompt)
            return reply(prompt) if callable(reply) else reply
        return mock.patch.object(o, "_thinker_exec", fake)

    def _boom_thinker(self):
        return mock.patch.object(o, "_thinker_exec",
                                 mock.Mock(side_effect=AssertionError("думатель не должен зваться")))

    # ================= САМОПОЧИНКА ШАГА ЛОКАЛЬНОЙ ЦЕПИ =================

    def test_selfheal_off_prior_haltonfail_byte_identical(self):
        # STEP_SELFHEAL off (Base) → провал шага = прежний halt-on-fail: сводка, думатель не зовётся
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="сломалось")
        with self._boom_thinker():
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)
        self.assertEqual(len(self._summaries(pid)), 1)
        self.assertEqual(self._cards(pid), [])

    def test_retry_rebirths_exactly_once_with_marker(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"], goal="цель-дословно")
        self._mk_step(pid, 1, 2, status="failed", text="шаг A", result="claude exit=1: боль")
        with self._thinker('{"verdict":"retry","fixed_step":"шаг A с верным путём","reason":"кривой путь"}'):
            o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"],
                         f"[шаг 1/2 родитель {pid}] [самопочинка шага 1, попытка 1] шаг A с верным путём")
        self.assertEqual(news[0]["from"], o.PC_LOCAL_DEC_FROM)
        self.assertEqual(news[0]["lane"], "pc")
        cards = self._cards(pid)
        self.assertEqual(len(cards), 1)                      # 🩹-карта решения думателя
        self.assertEqual(cards[0]["status"], "done")
        self.assertIn("🩹", cards[0]["result"])
        self.assertIn("попытка 1 из 1", cards[0]["result"])
        self.assertEqual(self._summaries(pid), [])           # цепь ЖИВА — сводки нет
        # промпт думателя несёт контекст родителя: цель + план + упавший шаг + провал
        p = self.prompts[0]
        self.assertIn("цель-дословно", p)
        self.assertIn("1. шаг A", p)
        self.assertIn("УПАВШИЙ ШАГ 1/2", p)
        self.assertIn("claude exit=1: боль", p)

    def test_reborn_failed_again_terminal_halt(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="упал")
        self._mk_step(pid, 1, 2, status="failed",
                      text="[самопочинка шага 1, попытка 1] фикс", result="упал снова")
        with self._boom_thinker():                            # повторный провал: думатель НЕ зовётся
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)                # перерождения №2 нет — петля невозможна
        cards = self._cards(pid)
        self.assertEqual(len(cards), 1)
        self.assertIn("🛑 самопочинка не помогла (попытка 1 исчерпана)", cards[0]["result"])
        self.assertEqual(len(self._summaries(pid)), 1)

    def test_timeout_gate_no_thinker(self):
        # ⏱-гейт: таймаут/сироту/просрочку approve думатель не чинит → сводка без консульта
        o._selfheal_on = lambda: True
        for diag in (f"{o.TIMEOUT_MARK} таймаут 2700s — headless прерван, задача не завершилась",
                     f"{o.TIMEOUT_MARK} ПК-таймаут одиночки: задача провисела in_progress 9999с",
                     f"{o.TIMEOUT_MARK} подтверждение не получено за 30 мин — задача провалена"):
            self.fb.tasks.clear()
            o._loc_summarized.clear()
            pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
            self._mk_step(pid, 1, 2, status="failed", result=diag)
            with self._boom_thinker():
                o.process_local_chains()
            self.assertEqual(len(self._news()), 0, diag)
            self.assertEqual(len(self._summaries(pid)), 1, diag)

    def test_reject_gate_no_thinker(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="отклонено Филиппом (кнопка ❌)")
        with self._boom_thinker():
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)
        self.assertEqual(len(self._summaries(pid)), 1)

    def test_thinker_halt_posts_card_and_summary(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="непонятная боль")
        with self._thinker('{"verdict":"halt","fixed_step":"","reason":"нужен человек"}'):
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)
        cards = self._cards(pid)
        self.assertEqual(len(cards), 1)
        self.assertIn("думатель: halt, причина: нужен человек", cards[0]["result"])
        self.assertEqual(len(self._summaries(pid)), 1)

    def test_thinker_garbage_or_none_failsafe_halt(self):
        o._selfheal_on = lambda: True
        for reply in (None, "мусор без json", '{"verdict":"чинить"}',
                      '{"verdict":"retry","fixed_step":""}'):   # retry без fixed = halt
            self.fb.tasks.clear()
            o._loc_summarized.clear()
            pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
            self._mk_step(pid, 1, 2, status="failed", result="боль")
            with self._thinker(reply):
                o.process_local_chains()
            self.assertEqual(len(self._news()), 0, str(reply))
            self.assertEqual(len(self._summaries(pid)), 1, str(reply))

    def test_rebirth_enqueue_fail_failsafe_halt(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="боль")
        real = self.fb.enqueue_task

        def flaky(frm, text, lane="pc"):
            if "[самопочинка шага" in text:
                return {"ok": False, "error": "bridge down"}
            return real(frm, text, lane)

        with self._thinker('{"verdict":"retry","fixed_step":"фикс","reason":"r"}'), \
             mock.patch.object(self.fb, "enqueue_task", flaky):
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)
        self.assertEqual(len(self._summaries(pid)), 1)        # очередь не приняла → halt, не хуже

    def test_timeout_diagnoses_carry_mark(self):
        # фундамент ⏱-гейта: локальные таймаут-диагнозы несут TIMEOUT_MARK первым символом
        tid = self.fb.add(status="new")
        self._claude(raise_timeout=True)
        o.process_new()
        self.assertTrue(self.fb.tasks[tid]["result"].startswith(o.TIMEOUT_MARK))
        stuck = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 999))
        o.process_stuck_singles()
        self.assertTrue(self.fb.tasks[stuck]["result"].startswith(o.TIMEOUT_MARK))
        na = self.fb.add(status="needs_approval", updated=iso_ago(o.APPROVAL_TTL + 999))
        o.process_approval_timeouts()
        self.assertTrue(self.fb.tasks[na]["result"].startswith(o.TIMEOUT_MARK))

    def test_single_selfheal_timeout_and_convert_gates(self):
        # паритет одиночек с VPS: ⏱-провал и конверт одобренной заявки думатель не трогает
        o._selfheal_on = lambda: True
        with self._boom_thinker():
            self.assertFalse(o._maybe_selfheal(9, "обычная задача",
                                               f"{o.TIMEOUT_MARK} таймаут 600s", frm="Filipp-pc"))
            self.assertFalse(o._maybe_selfheal(9, "[конверт одобренной заявки 5] сделай красное",
                                               "claude exit=1", frm="Filipp-pc"))

    # ================= АДАПТАЦИЯ ПЛАНА ПОСЛЕ DONE-ШАГА =================

    def test_adapt_off_keep_byte_identical(self):
        # PLAN_ADAPT off → done-шаг = прежний релиз следующего, думатель не зовётся
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done")
        with self._boom_thinker():
            o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"], f"[шаг 2/2 родитель {pid}] шаг B")

    def test_adapt_last_step_no_thinker(self):
        # на последнем шаге думатель НЕ зовётся (экономия лимитов) → сразу сводка
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done")
        self._mk_step(pid, 2, 2, status="done")
        with self._boom_thinker():
            o.process_local_chains()
        self.assertEqual(len(self._summaries(pid)), 1)

    def test_adapt_keep_releases_next_and_dedups(self):
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B", "3. шаг C"])
        self._mk_step(pid, 1, 3, status="done", result="RESULT: A готов")
        with self._thinker('{"verdict":"keep","adjusted_steps":[],"reason":"план верен"}'):
            o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"], f"[шаг 2/3 родитель {pid}] шаг B")
        self.assertIn((pid, 1), o._loc_adapted)               # дедуп: второй раз не спросим
        # промпт думателя несёт результаты сделанных и оставшиеся шаги
        p = self.prompts[0]
        self.assertIn("шаг 1: RESULT: A готов", p)
        self.assertIn("шаг 2: шаг B", p)
        self.assertIn("шаг 3: шаг C", p)

    def test_adapt_adjust_posts_card_and_releases_corrected(self):
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B", "3. шаг C"])
        self._mk_step(pid, 1, 3, status="done")
        with self._thinker('{"verdict":"adjust","adjusted_steps":["шаг B2","шаг C2"],'
                           '"reason":"B устарел"}'):
            o.process_local_chains()
        cards = self._adapt_cards(pid)
        self.assertEqual(len(cards), 1)                       # 🧭-карточка = restart-proof остаток
        self.assertEqual(cards[0]["status"], "done")
        self.assertEqual(cards[0]["task_text"], f"[коррекция плана родитель {pid}] после шага 1 (K=1)")
        self.assertIn("🧭", cards[0]["result"])
        self.assertIn("2. шаг B2", cards[0]["result"])
        self.assertIn("3. шаг C2", cards[0]["result"])
        news = self._news()
        self.assertEqual(len(news), 1)                        # релиз ПЕРВОГО скорректированного
        self.assertEqual(news[0]["task_text"],
                         f"[шаг 2/3 родитель {pid}] [коррекция плана 1] шаг B2")
        # restart-proof: план из очереди уже НОВЫЙ
        plan, k_cnt, last_base = o._loc_current_plan(pid)
        self.assertEqual(plan, {1: ("шаг A", 0), 2: ("шаг B2", 1), 3: ("шаг C2", 1)})
        self.assertEqual((k_cnt, last_base), (1, 1))

    def test_adapt_third_adjust_terminal_halt(self):
        # счётчик K restart-proof из карточек очереди: 2 уже есть → третий adjust = 🛑 дрейф
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B", "3. шаг C"])
        for k, base in ((1, 1), (2, 1)):
            card = self.fb.add(status="done",
                               task_text=f"[коррекция плана родитель {pid}] после шага 1 (K={k})")
            self.fb.tasks[card]["from"] = o.PC_LOCAL_DEC_FROM
            self.fb.tasks[card]["result"] = f"остаток {k}:\n2. шаг B{k}\n3. шаг C{k}"
        self._mk_step(pid, 2, 3, status="done", text="шаг B2", k=2)
        with self._thinker('{"verdict":"adjust","adjusted_steps":["шаг C3"],"reason":"ещё раз"}'):
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)                # релиза нет — терминальный halt
        cards = self._cards(pid)
        self.assertEqual(len(cards), 1)
        self.assertIn("🛑 план дрейфует", cards[0]["result"])
        self.assertIn("нужен", cards[0]["result"])
        self.assertEqual(len(self._summaries(pid)), 1)
        self.assertEqual(len(self._adapt_cards(pid)), 2)      # третья карточка НЕ создана

    def test_adapt_over_max_steps_failsafe_keep(self):
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done")
        eight = "[" + ",".join(f'"s{j}"' for j in range(8)) + "]"   # 1+8 > MAX_STEPS=8
        with self._thinker(f'{{"verdict":"adjust","adjusted_steps":{eight},"reason":"взрыв"}}'):
            o.process_local_chains()
        self.assertEqual(self._adapt_cards(pid), [])          # коррекция НЕ применена
        news = self._news()
        self.assertEqual(len(news), 1)                        # fail-safe keep: прежний шаг 2
        self.assertEqual(news[0]["task_text"], f"[шаг 2/2 родитель {pid}] шаг B")

    def test_adapt_finish_early_summary(self):
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B", "3. шаг C"])
        self._mk_step(pid, 1, 3, status="done", result="RESULT: всё уже сделано")
        with self._thinker('{"verdict":"finish","adjusted_steps":[],"reason":"цель достигнута"}'):
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)                # остаток НЕ релизится
        cards = self._cards(pid)
        self.assertEqual(len(cards), 1)
        self.assertIn("🏁", cards[0]["result"])
        sums = self._summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("🏁 завершено досрочно: цель достигнута", sums[0]["result"])

    def test_adapt_fresh_correction_step_not_reconsulted(self):
        # шаг сам вышел из свежей коррекции (last_base == i) → думатель НЕ зовётся, релиз штатно
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B", "3. шаг C"])
        card = self.fb.add(status="done",
                           task_text=f"[коррекция плана родитель {pid}] после шага 1 (K=1)")
        self.fb.tasks[card]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[card]["result"] = "остаток:\n2. шаг B2\n3. шаг C2"
        self._mk_step(pid, 1, 3, status="done")               # done ШАГА-БАЗЫ коррекции (i=1=last_base)
        with self._boom_thinker():
            o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"],
                         f"[шаг 2/3 родитель {pid}] [коррекция плана 1] шаг B2")

    def test_adapt_thinker_failure_failsafe_keep(self):
        os.environ["PLAN_ADAPT"] = "1"
        for reply in (None, "мусор", '{"verdict":"adjust","adjusted_steps":[]}',
                      '{"verdict":"переделать"}'):
            self.fb.tasks.clear()
            o._loc_summarized.clear()
            o._loc_adapted.clear()
            pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
            self._mk_step(pid, 1, 2, status="done")
            with self._thinker(reply):
                o.process_local_chains()
            news = self._news()
            self.assertEqual(len(news), 1, str(reply))        # keep: прежний план исполняется
            self.assertEqual(news[0]["task_text"], f"[шаг 2/2 родитель {pid}] шаг B", str(reply))

    def test_adapt_card_enqueue_fail_failsafe_keep(self):
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="done")
        real = self.fb.enqueue_task

        def flaky(frm, text, lane="pc"):
            if text.startswith("[коррекция плана родитель"):
                return {"ok": False, "error": "bridge down"}
            return real(frm, text, lane)

        with self._thinker('{"verdict":"adjust","adjusted_steps":["шаг B2"],"reason":"r"}'), \
             mock.patch.object(self.fb, "enqueue_task", flaky):
            o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)                        # карточка не встала → keep
        self.assertEqual(news[0]["task_text"], f"[шаг 2/2 родитель {pid}] шаг B")

    # --- слои живут вместе: 🩹-перерождение done → релиз следующего (адаптация после реборна) ---

    def test_reborn_done_continues_chain(self):
        os.environ["PLAN_ADAPT"] = "1"
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="упал")
        self._mk_step(pid, 1, 2, status="done",
                      text="[самопочинка шага 1, попытка 1] фикс", result="RESULT: со 2-й попытки")
        with self._thinker('{"verdict":"keep","adjusted_steps":[],"reason":"ок"}'):
            o.process_local_chains()
        news = self._news()
        self.assertEqual(len(news), 1)                        # цепь продолжилась штатно
        self.assertEqual(news[0]["task_text"], f"[шаг 2/2 родитель {pid}] шаг B")


class TestLocalDecRed(Base):
    """Шаг 5/7 родителя 185: красная механика локальной цепи (спека §4 «КРАСНОЕ В ЦЕПИ») —
    NEEDS_APPROVAL шага → set_needs_approval штатным путём, цепь ждёт «да» (тик не релизит);
    approve → ре-ран [ОДОБРЕНО ЧЕЛОВЕКОМ] → done → продолжение; reject / ⏱-просрочки /
    ✋-повторное красное после approve → halt цепи БЕЗ думателя + сводка. Красное не ослаблено:
    перерождение самопочинки с красным выводом снова даёт кнопку. Всё замокано."""

    def setUp(self):
        super().setUp()
        os.environ["PC_LOCAL_DEC"] = "1"
        os.environ.pop("PLAN_ADAPT", None)
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()
        o._loc_adapted.clear()

    def tearDown(self):
        for k in ("PC_LOCAL_DEC", "PLAN_ADAPT"):
            os.environ.pop(k, None)
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()
        o._loc_adapted.clear()
        super().tearDown()

    # --- обвязка сцены (как в TestLocalDecChain) ---

    def _mk_parent_done(self, plan_lines):
        pid = self.fb.add(status="done", task_text="крупное ТЗ")
        self.fb.tasks[pid]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[pid]["result"] = ("🧩 Декомпозиция (локальный дирижёр PC): "
                                        f"{len(plan_lines)} шагов.\n" + "\n".join(plan_lines))
        return pid

    def _mk_step(self, pid, i, n, status="new", text="кусок", result=None, updated=None):
        tid = self.fb.add(status=status, task_text=f"[шаг {i}/{n} родитель {pid}] {text}",
                          updated=updated)
        self.fb.tasks[tid]["from"] = o.PC_LOCAL_DEC_FROM
        self.fb.tasks[tid]["result"] = result
        return tid

    def _news(self):
        return [t for t in self.fb.tasks.values() if t["status"] == "new"]

    def _summaries(self, pid):
        return [t for t in self.fb.tasks.values()
                if str(t.get("task_text") or "").startswith(f"[сводка родитель {pid}]")]

    def _boom_thinker(self):
        return mock.patch.object(o, "_thinker_exec",
                                 mock.Mock(side_effect=AssertionError("думатель не должен зваться")))

    # --- маркеры красной механики байт-в-байт ---

    def test_red_marks_byte_values(self):
        self.assertEqual(o.MANUAL_MARK, "✋")
        self.assertEqual(o.TIMEOUT_MARK, "⏱")
        self.assertEqual(o._REJECT_PREFIX, "отклонено Филиппом")   # префикс devbot-отказа из спеки

    # --- NEEDS_APPROVAL шага → set_needs_approval, цепь ждёт «да» ---

    def test_red_step_sets_needs_approval_and_chain_waits(self):
        o._selfheal_on = lambda: True                     # даже со включённой самопочинкой
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        sid = self._mk_step(pid, 1, 2, status="new", text="задеплой clasp")
        self._claude(0, "NEEDS_APPROVAL: op=other | clasp redeploy Bridge")
        o.process_new()                                   # шаг цепи = ШТАТНЫЙ путь одиночки
        st = self.fb.tasks[sid]
        self.assertEqual(st["status"], "needs_approval")  # НЕ failed: кнопку понесёт devbot
        self.assertIn("op=other", st["result"])           # карточка (what) сохранена для devbot
        before = len(self.fb.tasks)
        with self._boom_thinker():
            o.process_local_chains()                      # тик: ждём Филиппа
        self.assertEqual(len(self.fb.tasks), before)      # ничего не релизнуто/не посталось
        self.assertEqual(self._summaries(pid), [])        # цепь ЖИВА, не закрыта

    # --- approve → ре-ран с нотой → done → продолжение цепи ---

    def test_approve_reruns_and_continues_chain(self):
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        sid = self._mk_step(pid, 1, 2, status="approved", text="задеплой clasp",
                            updated=iso_ago(10))
        prompts = []
        def fake(prompt, timeout, cwd, env):
            prompts.append(prompt)
            return (0, "сделал по одобрению\nRESULT: ок", "")
        o.run_claude = fake
        o.process_approved()
        self.assertEqual(self.fb.tasks[sid]["status"], "done")
        self.assertTrue(any("ОДОБРЕНО ЧЕЛОВЕКОМ" in p for p in prompts))
        o.process_local_chains()                          # done → релиз следующего шага
        news = self._news()
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["task_text"], f"[шаг 2/2 родитель {pid}] шаг B")

    # --- reject Филиппа → halt цепи без думателя ---

    def test_reject_halts_chain_without_thinker(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        self._mk_step(pid, 1, 2, status="failed", result="отклонено Филиппом (кнопка)")
        with self._boom_thinker():
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)            # halt: шаг 2 не релизнут
        sums = self._summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("❌ шаг 1/2", sums[0]["result"])

    # --- просрочка needs_approval (30 мин) → ⏱-failed → halt без думателя ---

    def test_na_timeout_halts_chain_without_thinker(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        sid = self._mk_step(pid, 1, 2, status="needs_approval",
                            updated=iso_ago(o.APPROVAL_TTL + 999))
        o.process_approval_timeouts()
        self.assertEqual(self.fb.tasks[sid]["status"], "failed")
        self.assertTrue(self.fb.tasks[sid]["result"].startswith(o.TIMEOUT_MARK))
        with self._boom_thinker():
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)
        self.assertEqual(len(self._summaries(pid)), 1)

    # --- просрочка approved (одобрено, но не исполнено 30 мин) → ⏱-failed → halt без думателя ---

    def test_approved_expiry_halts_chain_without_thinker(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        sid = self._mk_step(pid, 1, 2, status="approved", updated=iso_ago(4000))
        called = {"n": 0}
        def fake(*a, **k):
            called["n"] += 1
            return (0, "не должно вызваться")
        o.run_claude = fake
        o.process_approved()
        self.assertEqual(called["n"], 0)                  # истёкшее не исполняем
        self.assertEqual(self.fb.tasks[sid]["status"], "failed")
        self.assertTrue(self.fb.tasks[sid]["result"].startswith(o.TIMEOUT_MARK))
        with self._boom_thinker():
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)
        self.assertEqual(len(self._summaries(pid)), 1)

    # --- одобрено, но снова красное → ✋-ручная карта → halt без думателя (разрыв петли) ---

    def test_reapproved_red_manual_card_halts_without_thinker(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        sid = self._mk_step(pid, 1, 2, status="approved", text="задеплой clasp",
                            updated=iso_ago(10))
        self._claude(0, "NEEDS_APPROVAL: op=other | снова красное")
        o.process_approved()
        st = self.fb.tasks[sid]
        self.assertEqual(st["status"], "failed")          # НЕ needs_approval: ре-аппрув = петля
        self.assertTrue(st["result"].startswith(o.MANUAL_MARK))
        self.assertIn("вручную", st["result"])
        with self._boom_thinker():
            o.process_local_chains()
        self.assertEqual(len(self._news()), 0)            # halt цепи
        self.assertEqual(len(self._summaries(pid)), 1)

    # --- красное НЕ ослаблено: перерождение самопочинки с красным выводом снова даёт кнопку ---

    def test_reborn_red_step_gets_button_again(self):
        o._selfheal_on = lambda: True
        pid = self._mk_parent_done(["1. шаг A", "2. шаг B"])
        sid = self._mk_step(pid, 1, 2, status="new",
                            text="[самопочинка шага 1, попытка 1] фикс с clasp")
        self._claude(0, "NEEDS_APPROVAL: op=other | clasp redeploy")
        o.process_new()
        self.assertEqual(self.fb.tasks[sid]["status"], "needs_approval")   # кнопка как раньше


# --------------------------- РЕВИЗОР ДИАЛОГОВ (шаг 2/7, 262) ---------------------------

def _rev_row(rid, client_id, updated_ts, status="new", incoming=None, draft=None,
             final_text=None, transcript=None, client_name=None):
    """Строка drafts для тестов ревизора (только колонки, которые читает ревизор)."""
    return {"id": rid, "client_id": client_id, "client_name": client_name,
            "incoming": incoming, "transcript": transcript, "draft": draft,
            "final_text": final_text, "status": status, "updated_ts": updated_ts}


class TestRevizorSelect(unittest.TestCase):
    """Отбор окон с активностью и сборка пакета по окну (чистые функции, без БД)."""

    def test_newer_parse_and_compare(self):
        base = "2026-07-13T10:00:00+00:00"
        self.assertTrue(o._revizor_newer("2026-07-13T10:00:01+00:00", base))   # строго новее
        self.assertFalse(o._revizor_newer(base, base))                          # равно → не новее
        self.assertFalse(o._revizor_newer("2026-07-13T09:59:59+00:00", base))   # старее
        self.assertTrue(o._revizor_newer("что угодно", None))                   # нет метки → всё ново
        self.assertFalse(o._revizor_newer("не-дата", base))                     # не парсится → не тянем

    def test_select_groups_by_client_and_filters_by_since(self):
        since = "2026-07-13T10:00:00+00:00"
        rows = [
            _rev_row(1, 111, "2026-07-13T09:00:00+00:00"),   # окно 111: только старое → пропуск
            _rev_row(2, 222, "2026-07-13T09:00:00+00:00"),   # окно 222: старое +…
            _rev_row(3, 222, "2026-07-13T11:00:00+00:00"),   # …свежее → активно
            _rev_row(4, 333, "2026-07-13T12:00:00+00:00"),   # окно 333: свежее → активно
            _rev_row(5, None, "2026-07-13T12:00:00+00:00"),  # без client_id → игнор
        ]
        self.assertEqual(o._revizor_select_windows(rows, since), [222, 333])

    def test_select_since_none_takes_all_windows(self):
        rows = [_rev_row(1, 111, "2026-07-13T09:00:00+00:00"),
                _rev_row(2, 222, "2026-07-13T09:00:00+00:00")]
        self.assertEqual(o._revizor_select_windows(rows, None), [111, 222])

    def test_build_package_splits_client_sent_drafts(self):
        rows = [
            _rev_row(1, 222, "2026-07-13T09:00:00+00:00", status="sent",
                     incoming="привет, какие цены?", draft="черновик-1",
                     final_text="Привет! Тарифы такие…", transcript="[client]: привет",
                     client_name="Иван"),
            _rev_row(2, 222, "2026-07-13T11:00:00+00:00", status="new",
                     incoming="а депозит?", draft="черновик-2",
                     final_text="ОТКЛОНЁННЫЙ", transcript="[client]: привет\n[client]: а депозит?"),
            _rev_row(3, 999, "2026-07-13T11:00:00+00:00", incoming="чужое окно"),
        ]
        pkg = o._revizor_build_package(222, rows)
        self.assertEqual(pkg["client_id"], 222)
        self.assertEqual(pkg["client_name"], "Иван")
        self.assertEqual(pkg["incoming"], ["привет, какие цены?", "а депозит?"])
        # sent — ТОЛЬКО final_text одобренных статусов (ready/sent/test_held); new не в счёт
        self.assertEqual(pkg["sent"], ["Привет! Тарифы такие…"])
        self.assertNotIn("ОТКЛОНЁННЫЙ", pkg["sent"])
        self.assertEqual(pkg["drafts"], ["черновик-1", "черновик-2"])
        self.assertEqual(pkg["transcript"], "[client]: привет\n[client]: а депозит?")  # самый свежий
        self.assertEqual(pkg["last_ts"], "2026-07-13T11:00:00+00:00")

    def test_build_package_sent_from_ready_and_test_held(self):
        rows = [_rev_row(1, 5, "t1", status="ready", final_text="R"),
                _rev_row(2, 5, "t2", status="test_held", final_text="T"),
                _rev_row(3, 5, "t3", status="rejected", final_text="X")]
        self.assertEqual(o._revizor_build_package(5, rows)["sent"], ["R", "T"])

    def test_tick_builds_packages_for_active_windows_only(self):
        o._cowork = lambda line: None
        since = "2026-07-13T10:00:00+00:00"
        rows = [_rev_row(1, 111, "2026-07-13T09:00:00+00:00"),   # старое → не активно
                _rev_row(2, 222, "2026-07-13T11:00:00+00:00", incoming="свежее")]
        pkgs = o.revizor_tick(since=since, rows=rows)
        self.assertEqual([p["client_id"] for p in pkgs], [222])

    def test_tick_reads_real_sqlite(self):
        import sqlite3
        o._cowork = lambda line: None
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "moderation_ipc.db")
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, client_id INTEGER, "
                      "client_name TEXT, incoming TEXT, transcript TEXT, draft TEXT, "
                      "final_text TEXT, status TEXT, updated_ts TEXT)")
            c.execute("INSERT INTO drafts (client_id, incoming, status, updated_ts) "
                      "VALUES (777, 'свежий вопрос', 'sent', '2026-07-13T12:00:00+00:00')")
            c.execute("INSERT INTO drafts (client_id, incoming, status, updated_ts) "
                      "VALUES (888, 'старое', 'sent', '2026-07-13T08:00:00+00:00')")
            c.commit(); c.close()
            pkgs = o.revizor_tick(since="2026-07-13T10:00:00+00:00", db_path=db)
        self.assertEqual([p["client_id"] for p in pkgs], [777])

    def test_db_rows_missing_file_is_empty(self):
        self.assertEqual(o._revizor_db_rows(db_path=os.path.join(tempfile.gettempdir(), "no_db_zzz.db")), [])


# ------- РЕВИЗОР: автоприветствие Telegram Business (дополнение к цепи #262) -------
# Первое «от нас» = статичный автогритинг Business (мимо userbot/модерации). Детект детерминированный,
# по сигнатурной фразе ОБОИХ поколений; голдены — ДОСЛОВНЫЕ реальные тексты владельца + парафразы,
# негативы — обычное приветствие бота / реплика клиента / только депозит (правило-класс CLAUDE.md).
# Старый текст (13.07): «Здравствуйте, спасибо, что выбрали нас 🤝 Наши точки: <maps БангТао> <maps Камала>…»
_AG_OLD = ("Здравствуйте, спасибо, что выбрали нас 🤝 Наши точки: "
           "https://maps.app.goo.gl/bangtao1 https://maps.app.goo.gl/kamala2 "
           "Напишите, пожалуйста, что хотели бы арендовать, с какого числа и на какой срок.")
_AG_NEW = "Уже смотрю ваше сообщение, отвечу через пару минут 🙏"   # новое поколение владельца


class TestRevizorAutogreeting(unittest.TestCase):
    """Детект автоприветствия Telegram Business + пометка пакета + рендер для думателя."""

    def test_detect_positives_both_generations(self):
        # ОБА поколения + парафразы (регистр/пунктуация/ё-е агностично)
        for txt in (_AG_OLD, _AG_NEW,
                    "спасибо что выбрали нас!",                 # без запятой
                    "СПАСИБО, ЧТО ВЫБРАЛИ НАС",                 # верхний регистр
                    "Уже смотрю ваше сообщение…",               # многоточие-Unicode
                    "уже  смотрю   ваше  сообщение"):           # лишние пробелы
            self.assertTrue(o._revizor_is_autogreeting(txt), f"не распознал автогритинг: {txt!r}")

    def test_detect_negatives(self):
        # обычное приветствие бота / реплика клиента / только депозит — НЕ автогритинг
        for txt in ("Здравствуйте! Из скутеров есть NMAX 155, ADV 350. Что интересно?",
                    "Honda ADV350 с 15 по 22 июля, сколько выйдет?",
                    "Депозит — 3000 бат или паспорт, на выбор.",
                    "", None):
            self.assertFalse(o._revizor_is_autogreeting(txt), f"ложный автогритинг: {txt!r}")

    def test_greeting_line_from_transcript_strips_role(self):
        tr = f"[менеджер]: {_AG_OLD}\n[клиент]: Honda ADV350 с 15 по 22 июля?"
        self.assertEqual(o._revizor_greeting_line([], tr), _AG_OLD)   # ярлык роли снят

    def test_greeting_line_from_sent_when_not_in_transcript(self):
        self.assertEqual(o._revizor_greeting_line([_AG_NEW], "[клиент]: привет"), _AG_NEW)

    def test_greeting_line_none_when_absent(self):
        tr = "[менеджер]: Здравствуйте! Что арендуем?\n[клиент]: NMAX"
        self.assertIsNone(o._revizor_greeting_line(["Здравствуйте! Что арендуем?"], tr))

    def test_build_package_marks_greeting(self):
        rows = [_rev_row(1, 700, "2026-07-13T09:00:00+00:00", status="new",
                         incoming="Honda ADV350 с 15 по 22 июля?",
                         transcript=f"[менеджер]: {_AG_OLD}\n[клиент]: Honda ADV350 с 15 по 22 июля?")]
        self.assertEqual(o._revizor_build_package(700, rows)["greeting"], _AG_OLD)

    def test_build_package_greeting_none_without_autogreeting(self):
        rows = [_rev_row(1, 701, "2026-07-13T09:00:00+00:00", status="sent",
                         incoming="привет", final_text="Здравствуйте! Что арендуем?",
                         transcript="[клиент]: привет")]
        self.assertIsNone(o._revizor_build_package(701, rows)["greeting"])

    def test_pkg_text_renders_autogreeting_block(self):
        pkg = {"client_id": 700, "incoming": [], "sent": [], "drafts": [],
               "transcript": "[клиент]: привет", "greeting": _AG_NEW}
        txt = o._revizor_pkg_text(pkg)
        self.assertIn("АВТОПРИВЕТСТВИЕ TELEGRAM BUSINESS", txt)
        self.assertIn(_AG_NEW, txt)

    def test_pkg_text_omits_block_without_greeting(self):
        pkg = {"client_id": 701, "incoming": [], "sent": [], "drafts": [],
               "transcript": "[клиент]: привет", "greeting": None}
        self.assertNotIn("АВТОПРИВЕТСТВИЕ TELEGRAM BUSINESS", o._revizor_pkg_text(pkg))

    def test_preamble_wires_autogreeting_rules(self):
        # инструкции думателю: не судить содержание автогритинга; чек е про повтор ПОСЛЕ автогритинга
        pre = o._revizor_preamble()
        self.assertIn("АВТОПРИВЕТСТВИ", pre)
        self.assertIn("Telegram Business", pre)
        self.assertIn("не заводи", pre.replace("\n", " "))


class TestRevizorChecklist(unittest.TestCase):
    """Чек-лист классов ревизора вынесен в живой файл docs/revizor_checklist.md (механика «урок
    навсегда»): _revizor_checklist читает файл КАЖДЫЙ тик, _revizor_preamble вклеивает его между
    ролью и контрактом вывода; файла нет/пуст → встроенный дефолт (fail-safe)."""

    def _norm(self, s):
        return re.sub(r"\s+", " ", s).strip()

    def _tmp(self, text):
        fd, p = tempfile.mkstemp(suffix=".md")
        os.close(fd)
        Path(p).write_text(text, encoding="utf-8")
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        return p

    def test_shipped_file_matches_builtin_default(self):
        # тело живого файла (без md-комментария) == встроенный дефолт → пропажа файла не меняет суд
        self.assertEqual(self._norm(o._revizor_checklist(o.REVIZOR_CHECKLIST_FILE)),
                         self._norm(o.REVIZOR_CHECKLIST_DEFAULT))

    def test_default_has_all_classes_and_autogreeting(self):
        d = o.REVIZOR_CHECKLIST_DEFAULT
        for cls in "абвгдеж":
            self.assertIn(f"[класс {cls}]", d)
        self.assertIn("ВАЖНО об АВТОПРИВЕТСТВИИ", d)      # правило автогритинга — часть чек-листа

    def test_file_is_read_and_stripped_of_comments(self):
        p = self._tmp("<!-- секрет для человека -->\n- [класс а] реальный пункт\n")
        cl = o._revizor_checklist(p)
        self.assertIn("- [класс а] реальный пункт", cl)
        self.assertNotIn("секрет для человека", cl)       # md-комментарий не течёт в думателя

    def test_new_item_lands_in_next_tick_preamble(self):
        # дописанный класс попадает в преамбулу СЛЕДУЮЩЕГО прогона без правки кода
        p = self._tmp(o.REVIZOR_CHECKLIST_DEFAULT + "\n- [класс з] тестовый новый дефект\n")
        pre = o._revizor_preamble(p)
        self.assertIn("[класс з] тестовый новый дефект", pre)
        self.assertIn("думатель-ревизор", pre)            # роль-префикс на месте
        self.assertIn("JSON-массив", pre)                 # контракт-суффикс на месте

    def test_suffix_forbids_deploy_steps_in_task_text(self):
        # МАНДАТ 14.07: контракт вывода велит думателю НЕ вписывать деплой/рестарт в task_text,
        # а осознанный рестарт прода помечать action=owner (не зелёная задача).
        s = o.REVIZOR_PREAMBLE_SUFFIX
        self.assertIn("код", s)
        self.assertIn("тест", s.lower())
        self.assertTrue(any(w in s.lower() for w in ("деплой", "рестарт", "taskkill", "schtasks")))
        self.assertIn("owner", s)                          # осознанный рестарт → owner-путь назван

    def test_preamble_reads_file_each_call(self):
        p = self._tmp("- [класс а] первый\n")
        self.assertIn("[класс а] первый", o._revizor_preamble(p))
        Path(p).write_text("- [класс а] второй\n", encoding="utf-8")   # тот же файл поменяли
        pre = o._revizor_preamble(p)
        self.assertIn("[класс а] второй", pre)            # прочитано заново, без рестарта
        self.assertNotIn("первый", pre)

    def test_missing_file_failsafe_default(self):
        cl = o._revizor_checklist(os.path.join(tempfile.gettempdir(), "нет-такого-revizor-checklist.md"))
        self.assertEqual(cl, o.REVIZOR_CHECKLIST_DEFAULT)  # файла нет → дефолт

    def test_empty_file_failsafe_default(self):
        p = self._tmp("   \n<!-- только комментарий -->\n\n")   # тело пустое после вырезки
        self.assertEqual(o._revizor_checklist(p), o.REVIZOR_CHECKLIST_DEFAULT)


class TestRevizorConsult(unittest.TestCase):
    """Думатель-ревизор окна (шаг 3/7 262): рендер пакета, парс JSON-массива находок, consult с
    инъекцией _thinker_exec (реальный claude не дёргаем)."""

    def _pkg(self, **kw):
        base = {"client_id": 555, "client_name": "Пётр",
                "incoming": ["Какие марки и модели, какие цены на аренду на неделю?"],
                "transcript": "[client]: Какие марки и модели, какие цены на аренду на неделю?",
                "sent": ["Здравствуйте! Уточните, пожалуйста, модель и даты аренды."],
                "drafts": ["черновик"], "last_ts": "2026-07-13T12:00:00+00:00"}
        base.update(kw)
        return base

    def test_pkg_text_has_all_sections(self):
        txt = o._revizor_pkg_text(self._pkg())
        self.assertIn("client_id=555", txt)
        self.assertIn("ТРАНСКРИПТ", txt)
        self.assertIn("Какие марки и модели", txt)          # реплика клиента внутри
        self.assertIn("ОТПРАВЛЕНО КЛИЕНТУ", txt)
        self.assertIn("Уточните, пожалуйста, модель", txt)

    def test_pkg_text_empty_sections_dash(self):
        txt = o._revizor_pkg_text({"client_id": 1, "incoming": [], "sent": [], "drafts": [], "transcript": None})
        self.assertIn("—", txt)                              # пустые секции не роняют рендер

    def test_parse_valid_array(self):
        raw = ('[{"class":"ж","evidence":"первым сообщением дал модель+даты, а мы шлём анкету",'
               '"action":"task","task_text":"фикс: при первом сообщении с моделью+датами котировать, не анкетировать"}]')
        out = o._parse_revizor_json(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["class"], "ж")
        self.assertEqual(out[0]["action"], "task")
        self.assertTrue(out[0]["task_text"].startswith("фикс:"))

    def test_parse_empty_array_is_no_findings(self):
        self.assertEqual(o._parse_revizor_json("[]"), [])     # нарушений нет → []

    def test_parse_tolerates_wrapper_garbage(self):
        raw = 'вот находки: [{"class":"г","evidence":"утечка «собрано» в отправленном","action":"owner"}] всё'
        out = o._parse_revizor_json(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["action"], "owner")
        self.assertEqual(out[0]["task_text"], "")             # owner → пустой task_text

    def test_parse_drops_bad_items(self):
        raw = ('[{"class":"а","evidence":"e","action":"task","task_text":"t"},'
               '"строка-не-объект",'
               '{"class":"б","action":"unknown"},'          # чужой action → отброшен
               '{"class":"в","evidence":"переспрос дат","action":"noise"}]')
        out = o._parse_revizor_json(raw)
        self.assertEqual([f["action"] for f in out], ["task", "noise"])

    def test_parse_clips_evidence_and_task(self):
        raw = o.json.dumps([{"class": "д", "evidence": "x" * 500, "action": "task", "task_text": "y" * 900}])
        out = o._parse_revizor_json(raw)
        self.assertEqual(len(out[0]["evidence"]), 200)
        self.assertEqual(len(out[0]["task_text"]), 400)

    def test_parse_non_array_is_none(self):
        self.assertIsNone(o._parse_revizor_json('{"class":"а"}'))   # объект, не массив → None
        self.assertIsNone(o._parse_revizor_json("не json вовсе"))
        self.assertIsNone(o._parse_revizor_json(""))

    def test_consult_injects_thinker_and_parses(self):
        seen = {}
        save = o._thinker_exec
        try:
            o._thinker_exec = lambda prompt, timeout, tag: seen.update(prompt=prompt, timeout=timeout, tag=tag) or \
                '[{"class":"ж","evidence":"анкета на первом сообщении","action":"task","task_text":"котировать"}]'
            out = o._revizor_consult(self._pkg())
        finally:
            o._thinker_exec = save
        self.assertEqual(seen["timeout"], o.REVIZOR_TIMEOUT)
        self.assertEqual(seen["tag"], "dialog-revizor")
        self.assertIn("чек-листу", seen["prompt"])            # преамбула вклеена
        self.assertIn("Какие марки и модели", seen["prompt"])  # текст пакета вклеен
        self.assertEqual(out[0]["class"], "ж")

    def test_consult_thinker_none_is_failsafe(self):
        save = o._thinker_exec
        try:
            o._thinker_exec = lambda *a, **k: None            # думатель упал/таймаут
            self.assertIsNone(o._revizor_consult(self._pkg()))
        finally:
            o._thinker_exec = save

    def test_consult_unparseable_is_none(self):
        save = o._thinker_exec
        try:
            o._thinker_exec = lambda *a, **k: "болтовня без массива"
            self.assertIsNone(o._revizor_consult(self._pkg()))
        finally:
            o._thinker_exec = save


class TestRevizorState(unittest.TestCase):
    """Метка прошлого прогона (restart-proof) + троттлинг/бутстрап maybe_revizor."""

    def setUp(self):
        self._save = (o._cowork, o._notify, o._revizor_on, o._revizor_route)
        o._cowork = lambda line: None
        o._notify = lambda text: None
        o._revizor_on = lambda: True
        o._revizor_route = lambda *a, **k: None   # маршрутизацию (реальный claude) тут не дёргаем — она отдельно
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "revizor_state.json")

    def tearDown(self):
        (o._cowork, o._notify, o._revizor_on, o._revizor_route) = self._save

    def test_read_missing_state_is_empty(self):
        self.assertEqual(o._revizor_read_state(path=self.state), {})

    def test_write_then_read_roundtrip(self):
        o._revizor_write_state(1_000_000.0, path=self.state)
        st = o._revizor_read_state(path=self.state)
        self.assertEqual(st["ts"], 1_000_000.0)
        self.assertTrue(o._parse_iso(st["last_run"]) is not None)   # last_run — валидная iso

    def test_write_failure_is_silent(self):
        bad = os.path.join(self.tmp, "no_such_dir_zzz", "s.json")
        try:
            o._revizor_write_state(1.0, path=bad)
        except Exception as e:
            self.fail(f"_revizor_write_state не должен пробрасывать сбой: {e}")

    def test_flag_off_returns_none_and_writes_nothing(self):
        o._revizor_on = lambda: False
        self.assertIsNone(o.maybe_revizor(now=1_000_000.0, state_path=self.state))
        self.assertFalse(os.path.exists(self.state))               # выключено → метку не ставим

    def test_bootstrap_sets_mark_without_tick(self):
        calls = {"n": 0}
        save = o.revizor_tick
        try:
            o.revizor_tick = lambda **k: calls.__setitem__("n", calls["n"] + 1) or []
            out = o.maybe_revizor(now=1_000_000.0, state_path=self.state)
        finally:
            o.revizor_tick = save
        self.assertIsNone(out)                                     # бутстрап: тик НЕ зовём
        self.assertEqual(calls["n"], 0)
        self.assertEqual(o._revizor_read_state(path=self.state)["ts"], 1_000_000.0)  # но метку поставили

    def test_throttle_skips_before_period(self):
        o._revizor_write_state(1_000_000.0, path=self.state)
        # прошло меньше REVIZOR_SEC → None, метку не двигаем
        out = o.maybe_revizor(now=1_000_000.0 + o.REVIZOR_SEC - 10, state_path=self.state)
        self.assertIsNone(out)
        self.assertEqual(o._revizor_read_state(path=self.state)["ts"], 1_000_000.0)

    def test_after_period_runs_tick_with_since_prev_mark(self):
        o._revizor_write_state(1_000_000.0, path=self.state)
        prev_iso = o._revizor_read_state(path=self.state)["last_run"]
        seen = {}
        save = o.revizor_tick
        try:
            o.revizor_tick = lambda since=None, **k: seen.__setitem__("since", since) or [{"client_id": 1}]
            now2 = 1_000_000.0 + o.REVIZOR_SEC + 1
            out = o.maybe_revizor(now=now2, state_path=self.state)
        finally:
            o.revizor_tick = save
        self.assertEqual(seen["since"], prev_iso)                  # since = метка прошлого прогона
        self.assertEqual(out, [{"client_id": 1}])
        self.assertEqual(o._revizor_read_state(path=self.state)["ts"], now2)  # метка сдвинута вперёд

    def test_mark_is_restart_proof(self):
        # «рестарт» = ничего в памяти не помним, читаем метку с диска: свежая метка на диске
        # гейтит повторный прогон даже сразу после старта (в отличие от in-memory троттла).
        o._revizor_write_state(2_000_000.0, path=self.state)
        self.assertIsNone(o.maybe_revizor(now=2_000_000.0 + 5, state_path=self.state))


# --------------------------- РЕВИЗОР: МАРШРУТИЗАЦИЯ (шаг 4/7, 262) ---------------------------

_REV_NOW = 1_752_400_000.0   # фикс. wall-clock для стабильной даты бюджета (Date.now не дёргаем)


class TestRevizorRouteHelpers(unittest.TestCase):
    """Чистые части маршрутизации: маркеры бюджета/дедупа, текст owner-карточки."""

    def test_today_utc_date(self):
        self.assertEqual(len(o._revizor_today(_REV_NOW)), 10)          # YYYY-MM-DD
        self.assertEqual(o._revizor_today(_REV_NOW), o._revizor_today(_REV_NOW + 3600))  # тот же день

    def test_task_markers_parse(self):
        today = o._revizor_today(_REV_NOW)
        items = [{"task_text": f"[ревизор дата={today} класс=а] фикс детекта"},
                 {"task_text": f"[ревизор дата=2020-01-01 класс=ж] старое"},
                 {"task_text": "[шаг 1/3 родитель 5] обычный шаг"},   # не наш маркер
                 {"task_text": None}]
        got = o._revizor_task_markers(items)
        self.assertEqual(got, [(today, "а"), ("2020-01-01", "ж")])

    def test_owner_card_text_dedup_and_merge(self):
        f = [{"class": "г", "evidence": "утечка «собрано»", "client_id": 555},
             {"class": "г", "evidence": "утечка «собрано»", "client_id": 555},   # дубль → 1 строка
             {"class": "", "evidence": "", "client_id": 7}]                       # пустая улика → пропуск
        prior = ["• [класс а] окно 111: чужой тариф", "мусор не-строка"]
        txt = o._revizor_owner_card_text(f, prior)
        self.assertIn("🔍 Ревизор: находки", txt)
        self.assertIn("окно 111", txt)                     # строка прошлой карточки сохранена
        self.assertEqual(txt.count("утечка «собрано»"), 1)  # дедуп сработал
        self.assertNotIn("окно 7", txt)                    # пустая улика не попала

    def test_owner_card_text_empty(self):
        self.assertEqual(o._revizor_owner_card_text([], ()), "")

    def test_prior_lines_extracts_bullets(self):
        item = {"what": "заголовок\n• [класс а] окно 1: x\nне буллет\n• [класс б] окно 2: y"}
        self.assertEqual(o._revizor_prior_lines(item), ["• [класс а] окно 1: x", "• [класс б] окно 2: y"])

    def test_is_owner_card_marker(self):
        self.assertTrue(o._is_revizor_owner_card(o.REVIZOR_OWNER_MARK + " ..."))
        self.assertFalse(o._is_revizor_owner_card("[шаг 1/2 родитель 3] x"))
        self.assertFalse(o._is_revizor_owner_card(None))


class TestRevizorPostrelease(unittest.TestCase):
    """ШАГ 5/6 (92): ПОСТ-РЕЛИЗНАЯ сверка живых черновиков окна ТЕМ ЖЕ чек-листом #92, что e2e-смоук
    (suggest._smoke_checks). Эталон инъектируем — Bridge/сеть не трогаем. Чистый черновик → находок
    нет; грязный → провалы всех 6 чеков → owner-находки (класс #92). Пост-релизная особинка: без
    эталона J/доставки эти два чека находок НЕ заводят (нет ground-truth ≠ «черновик врёт»); text-only
    чеки (годы/наличие/депозит/вернусь) — всегда."""

    J_LINE = "2400 ฿ за 5 дней (Скидка за срок 15%, 480 ฿ в день); депозит 3000 ฿"
    DELIVERY_LINE = "Доставка — 590 ฿ (забор байка в конце аренды — бесплатный)."

    def _exp(self, **over):
        e = {"j_line": self.J_LINE, "delivery_line": self.DELIVERY_LINE,
             "zone": "Раваи", "zone_price": 590, "full_data": True}
        e.update(over)
        return e

    _CLEAN = ("Здравствуйте! Рассчитал аренду NMAX 👍\n"
              "NMAX — 2400 ฿ за 5 дней (Скидка за срок 15%, 480 ฿ в день); депозит 3000 ฿.\n"
              "Доставка — 590 ฿ (забор байка в конце аренды — бесплатный).")
    # Грязный: строка J ПЕРЕСОБРАНА (нет «Скидки за срок»), доставка 700 (не зона 590), год «2023
    # года», отписка «вернусь», клеймы наличия «свободен/в наличии», конфликт депозита 3000 vs 5000.
    _DIRTY = ("Здравствуйте! Байк 2023 года свободен и в наличии 👍\n"
              "NMAX — 2400 ฿ за 5 дней; депозит 3000 ฿.\n"
              "Ещё депозит 5000 ฿ или паспорт.\n"
              "Доставка — 700 ฿.\n"
              "Уточню детали и вернусь.")

    def test_clean_draft_no_findings(self):
        pkg = {"client_id": 42, "sent": [self._CLEAN], "drafts": []}
        self.assertEqual(o._revizor_postrelease_findings(pkg, exp=self._exp()), [])

    def test_dirty_draft_all_six_checks_flagged(self):
        pkg = {"client_id": 77, "sent": [self._DIRTY], "drafts": []}
        found = o._revizor_postrelease_findings(pkg, exp=self._exp())
        names = {f["check"] for f in found}
        self.assertEqual(names, {"строка J дословно", "доставка = цена зоны", "нет годов",
                                 "нет «вернусь/уточним» при полных данных",
                                 "нет утверждений о наличии", "депозит без противоречий"})
        self.assertTrue(all(f["class"] == "#92" and f["action"] == "owner"
                            and f["client_id"] == 77 and not f["task_text"] for f in found))

    def test_missing_expectation_skips_j_and_delivery(self):
        # Эталон J/доставки НЕ выведен (пусто) → эти два чека находок не заводят даже на грязном
        # черновике; text-only чеки (годы/наличие/депозит) — заводят. Реаск гасится full_data=False.
        pkg = {"client_id": 9, "drafts": [self._DIRTY]}
        exp = self._exp(j_line="", delivery_line="", zone=None, zone_price=None, full_data=False)
        found = o._revizor_postrelease_findings(pkg, exp=exp)
        names = {f["check"] for f in found}
        self.assertEqual(names, {"нет годов", "нет утверждений о наличии", "депозит без противоречий"})

    def test_no_live_texts_no_bridge_no_findings(self):
        # Окно без sent/drafts → сверять нечего, эталон (Bridge) НЕ строим.
        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("эталон строить не должны"))
        self.assertEqual(o._revizor_postrelease_findings({"client_id": 1}, exp_fn=boom), [])

    def test_dedup_same_defect_across_texts(self):
        # Один и тот же дефект в sent И drafts → одна находка на класс (дедуп по имени чека).
        pkg = {"client_id": 5, "sent": [self._DIRTY], "drafts": [self._DIRTY]}
        found = o._revizor_postrelease_findings(pkg, exp=self._exp())
        self.assertEqual(len(found), 6)                       # 6 дефектов, не 12 (дедуп sent+drafts)
        self.assertEqual(len(found), len({f["check"] for f in found}))

    def test_findings_render_into_owner_card(self):
        pkg = {"client_id": 77, "sent": [self._DIRTY], "drafts": []}
        found = o._revizor_postrelease_findings(pkg, exp=self._exp())
        card = o._revizor_owner_card_text(found)
        self.assertIn("🔍 Ревизор: находки", card)
        self.assertIn("[класс #92]", card)
        self.assertIn("окно 77", card)
        self.assertIn("нет годов", card)

    def test_expectations_no_context_skips_bridge(self):
        # _revizor_expectations: окно без модели/пина → эталон пустой, getter/resolve НЕ зовём.
        boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Bridge звать не должны"))
        exp = o._revizor_expectations({"client_id": 1, "transcript": "[клиент]: привет, как дела?"},
                                      getter=boom, resolve_delivery=boom)
        self.assertIsNone(exp["j_line"])
        self.assertIsNone(exp["zone_price"])
        self.assertFalse(exp["full_data"])


class TestRevizorEnqueueBudget(Base):
    """Бюджет ≤2/сутки и дедуп по классу задач-находок — restart-proof из маркеров очереди."""

    def _f(self, cls, cid=555, task="фикс детекта класса " + "x"):
        return {"class": cls, "action": "task", "task_text": task, "client_id": cid}

    def test_enqueue_puts_dec_parent(self):
        enq, skip = o._revizor_enqueue_tasks([self._f("а")], [], _REV_NOW)
        self.assertEqual((enq, skip), (1, 0))
        news = [t for t in self.fb.tasks.values() if t["status"] == "new"]
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["from"], o.PC_LOCAL_DEC_FROM)       # зелёный родитель дирижёру
        self.assertTrue(news[0]["task_text"].startswith(f"[ревизор дата={o._revizor_today(_REV_NOW)} класс=а]"))

    def test_budget_caps_at_two_per_day(self):
        today = o._revizor_today(_REV_NOW)
        items = [{"task_text": f"[ревизор дата={today} класс=а] уже", "status": "done"},
                 {"task_text": f"[ревизор дата={today} класс=б] уже", "status": "done"}]
        enq, skip = o._revizor_enqueue_tasks([self._f("в"), self._f("г")], items, _REV_NOW)
        self.assertEqual(enq, 0)                                     # бюджет уже исчерпан сегодня
        self.assertEqual(skip, 2)
        self.assertEqual(len([t for t in self.fb.tasks.values()]), 0)

    def test_budget_ignores_other_days(self):
        items = [{"task_text": "[ревизор дата=2020-01-01 класс=а] вчера", "status": "done"},
                 {"task_text": "[ревизор дата=2020-01-02 класс=б] позавчера", "status": "done"}]
        enq, _skip = o._revizor_enqueue_tasks([self._f("в")], items, _REV_NOW)
        self.assertEqual(enq, 1)                                     # прошлые дни бюджет сегодня не жгут

    def test_dedup_by_class(self):
        today = o._revizor_today(_REV_NOW)
        items = [{"task_text": f"[ревизор дата={today} класс=а] уже есть", "status": "new"}]
        enq, skip = o._revizor_enqueue_tasks([self._f("а"), self._f("б")], items, _REV_NOW)
        self.assertEqual((enq, skip), (1, 1))                        # класс «а» дедуп, «б» поставлен
        self.assertTrue(any("класс=б]" in t["task_text"] for t in self.fb.tasks.values()))

    def test_dedup_within_batch(self):
        enq, skip = o._revizor_enqueue_tasks([self._f("а"), self._f("а")], [], _REV_NOW)
        self.assertEqual((enq, skip), (1, 1))                        # второй той же партии — дедуп


class TestRevizorOwnerCard(Base):
    """Сводная owner-карточка в инбокс 1160: создание, редактирование существующей, дедуп."""

    def _f(self, cls="г", ev="спорный тариф", cid=555):
        return {"class": cls, "action": "owner", "evidence": ev, "client_id": cid}

    def test_create_new_card_to_1160(self):
        o._revizor_post_owner_card([self._f()], [])
        cards = [t for t in self.fb.tasks.values() if t["status"] == "needs_approval"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["topic"], o.NEEDS_APPROVAL_TOPIC)     # тема = инбокс одобрений (1160 в проде)
        self.assertEqual(cards[0]["from"], o.REVIZOR_OWNER_FROM)
        self.assertTrue(cards[0]["task_text"].startswith(o.REVIZOR_OWNER_MARK))
        self.assertIn("спорный тариф", cards[0]["what"])

    def test_edit_existing_card_not_second(self):
        tid = self.fb.add(status="needs_approval", task_text=o.REVIZOR_OWNER_MARK + " карточка")
        self.fb.tasks[tid]["what"] = "🔍 Ревизор: находки\n• [класс а] окно 111: старое"
        items = [dict(t) for t in self.fb.tasks.values()]
        o._revizor_post_owner_card([self._f(cls="г", ev="новое", cid=222)], items)
        cards = [t for t in self.fb.tasks.values() if t["status"] == "needs_approval"]
        self.assertEqual(len(cards), 1)                                # НЕ вторая карточка — редактируем ту же
        self.assertEqual(cards[0]["id"], tid)
        self.assertIn("окно 111", cards[0]["what"])                    # старая находка сохранена (мердж)
        self.assertIn("новое", cards[0]["what"])                       # новая добавлена

    def test_empty_findings_no_card(self):
        o._revizor_post_owner_card([{"class": "г", "evidence": "", "client_id": 1}], [])
        self.assertEqual([t for t in self.fb.tasks.values() if t["status"] == "needs_approval"], [])


class TestRevizorRoute(Base):
    """Полная маршрутизация _revizor_route: разводка task/owner/noise, пусто/сбой → тишина+NOTE."""

    def setUp(self):
        super().setUp()
        self._save_c = (o._revizor_consult,)
        self.notes = []
        o._cowork = lambda line: self.notes.append(line)

    def tearDown(self):
        (o._revizor_consult,) = self._save_c
        super().tearDown()

    def _consult(self, mapping):
        """mapping: client_id → находки|None. Инъектируем вместо реального думателя."""
        o._revizor_consult = lambda pkg: mapping.get(pkg.get("client_id"))

    def test_empty_windows_note_clean(self):
        self._consult({1: [], 2: []})
        out = o._revizor_route([{"client_id": 1}, {"client_id": 2}], now=_REV_NOW)
        self.assertEqual((out["tasks"], out["owner"]), (0, 0))
        self.assertTrue(any("2 окон, чисто" in n for n in self.notes))
        self.assertEqual([t for t in self.fb.tasks.values()], [])      # тишина: ничего не поставили

    def test_all_thinker_fail_note_silence(self):
        self._consult({1: None, 2: None})                              # думатель по всем окнам упал
        out = o._revizor_route([{"client_id": 1}, {"client_id": 2}], now=_REV_NOW)
        self.assertEqual(out["failed"], 2)
        self.assertTrue(any("не ответил" in n for n in self.notes))
        self.assertEqual([t for t in self.fb.tasks.values()], [])      # наружу тишина

    def test_task_routed_to_dirijor(self):
        self._consult({1: [{"class": "ж", "action": "task", "task_text": "котировать, не анкетировать"}]})
        out = o._revizor_route([{"client_id": 1}], now=_REV_NOW)
        self.assertEqual(out["tasks"], 1)
        news = [t for t in self.fb.tasks.values() if t["status"] == "new"]
        self.assertEqual(news[0]["from"], o.PC_LOCAL_DEC_FROM)
        self.assertIn("котировать", news[0]["task_text"])

    def test_owner_routed_to_card(self):
        self._consult({1: [{"class": "г", "action": "owner", "evidence": "спорный тариф"}]})
        out = o._revizor_route([{"client_id": 1}], now=_REV_NOW)
        self.assertEqual(out["owner"], 1)
        cards = [t for t in self.fb.tasks.values() if t["status"] == "needs_approval"]
        self.assertEqual(len(cards), 1)
        self.assertIn("спорный тариф", cards[0]["what"])

    def test_noise_only_logged_no_side_effects(self):
        self._consult({1: [{"class": "в", "action": "noise", "evidence": "ложное"}]})
        out = o._revizor_route([{"client_id": 1}], now=_REV_NOW)
        self.assertEqual((out["tasks"], out["owner"], out["noise"]), (0, 0, 1))
        self.assertEqual([t for t in self.fb.tasks.values()], [])      # noise наружу не выносим

    def test_queue_unavailable_defers(self):
        self._consult({1: [{"class": "ж", "action": "task", "task_text": "фикс"}]})
        save = o._loc_fetch_items
        try:
            o._loc_fetch_items = lambda: None                          # очередь недоступна
            out = o._revizor_route([{"client_id": 1}], now=_REV_NOW)
        finally:
            o._loc_fetch_items = save
        self.assertTrue(out.get("deferred"))
        self.assertEqual(out["tasks"], 0)
        self.assertTrue(any("очередь недоступна" in n for n in self.notes))

    def test_empty_packages_no_note(self):
        self._consult({})
        out = o._revizor_route([], now=_REV_NOW)
        self.assertEqual(out["windows"], 0)
        self.assertEqual(self.notes, [])                               # окон нет вовсе → даже NOTE не пишем


# ---- МАНДАТ 14.07: авто-задачи ревизора НЕ заказывают деплой/рестарт прода ----
# Живой урок: цепи 310/311 из находок ревизора доходили до КРАСНЫХ шагов «деплой+рестарт прод-ботов»
# (✋ владельцу), хотя применение делает авто-reconcile. Голдены: находка «рестартни бота» → owner-
# карточка (не задача); обычная находка → задача БЕЗ деплой-шагов. Реальные дев-ТЗ-фразы из провала.

class TestRevizorDeployWords(unittest.TestCase):
    """Детектор деплой/рестарт-слов в task_text (пост-фильтр). Реальная фраза живого провала 14.07
    (цепь 310/311 «деплой+рестарт прод-ботов») + парафразы RU/EN; негативы — чистое код+тест-ТЗ."""

    POS = [
        "деплой и рестарт прод-ботов после правки",                    # дословный живой провал 310/311
        "перезапусти userbot и moderbot",
        "рестартни бота, чтобы правка подхватилась",
        "redeploy the suggest module and restart the bots",
        "выполни deploy и reboot userbot",
        "taskkill userbot.py и подними заново",
        "обнови schtasks-задачу оркестратора",
        "убей процесс модербота и перезагрузи",
    ]
    NEG = [
        "фикс детекта прайс-интента в suggest.py + голден с реальной фразой клиента",
        "добавь гард переспроса дат в suggest, покрой юнит-тестом",
        "поправь шаблон ответа: не переспрашивать уже данную модель; тест на класс в",
        "исправь ложную ✅ трекера в collect_booking — юнит на класс д",
        "",
        None,
    ]

    def test_positives_flagged(self):
        for t in self.POS:
            self.assertTrue(o._revizor_task_wants_deploy(t), f"НЕ поймал деплой/рестарт: {t!r}")

    def test_negatives_pass(self):
        for t in self.NEG:
            self.assertFalse(o._revizor_task_wants_deploy(t), f"ложно принял за деплой: {t!r}")

    def test_demote_moves_task_to_owner(self):
        f = {"class": "ж", "action": "task", "client_id": 42, "evidence": "",
             "task_text": "фикс детекта + деплой и рестарт прод-ботов"}
        g = o._revizor_demote_deploy_task(f)
        self.assertEqual(g["action"], "owner")
        self.assertEqual(g["task_text"], "")                           # owner-карточка task_text не несёт
        self.assertIn("деплой", g["evidence"])                         # суть находки видна владельцу (из task_text)
        self.assertEqual(g["client_id"], 42)                           # окно сохранено

    def test_demote_keeps_existing_evidence(self):
        f = {"class": "ж", "action": "task", "client_id": 7, "evidence": "живая улика окна",
             "task_text": "рестартни бота"}
        g = o._revizor_demote_deploy_task(f)
        self.assertEqual(g["evidence"], "живая улика окна")            # непустую улику не затираем


class TestRevizorDeployRouting(Base):
    """Голдены маршрутизации: находка с деплой/рестарт-шагом → owner-карточка (не зелёная задача);
    обычная находка → задача дирижёру. + страховка enqueue (деплой-ТЗ в очередь не встаёт)."""

    def setUp(self):
        super().setUp()
        self._save_c = (o._revizor_consult,)
        self.notes = []
        o._cowork = lambda line: self.notes.append(line)

    def tearDown(self):
        (o._revizor_consult,) = self._save_c
        super().tearDown()

    def _consult(self, mapping):
        o._revizor_consult = lambda pkg: mapping.get(pkg.get("client_id"))

    def test_restart_finding_becomes_owner_card_not_task(self):
        # ГОЛДЕН: «рестартни бота» в task_text → owner-карточка 1160, НИ ОДНОЙ зелёной задачи дирижёру
        self._consult({1: [{"class": "ж", "action": "task",
                            "task_text": "фикс детекта + деплой и рестарт прод-ботов"}]})
        out = o._revizor_route([{"client_id": 1}], now=_REV_NOW)
        self.assertEqual(out["tasks"], 0)                              # задачей НЕ поставлено
        self.assertEqual(out["demoted"], 1)
        self.assertEqual(out["owner"], 1)                              # ушло в owner-карточку
        news = [t for t in self.fb.tasks.values() if t["status"] == "new"]
        self.assertEqual(news, [])                                     # дирижёру зелёной задачи нет
        cards = [t for t in self.fb.tasks.values() if t["status"] == "needs_approval"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["from"], o.REVIZOR_OWNER_FROM)
        self.assertTrue(any("деплой-шаг" in n for n in self.notes))    # журнал-ритуал: перевод отмечен в NOTE

    def test_ordinary_finding_becomes_task_without_deploy(self):
        # ГОЛДЕН: обычная находка (только код+тест) → зелёная задача дирижёру, деплой-слов в ней нет
        self._consult({1: [{"class": "ж", "action": "task",
                            "task_text": "фикс детекта прайс-интента в suggest.py + голден"}]})
        out = o._revizor_route([{"client_id": 1}], now=_REV_NOW)
        self.assertEqual(out["tasks"], 1)
        self.assertEqual(out["demoted"], 0)
        news = [t for t in self.fb.tasks.values() if t["status"] == "new"]
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["from"], o.PC_LOCAL_DEC_FROM)
        self.assertFalse(o._revizor_task_wants_deploy(news[0]["task_text"]))  # в задаче нет деплой-шагов

    def test_enqueue_guard_drops_deploy_task(self):
        # СТРАХОВКА: если деплой-ТЗ дошло до enqueue напрямую — в очередь НЕ ставим (skip), не задача
        f = {"class": "ж", "action": "task", "client_id": 5,
             "task_text": "деплой и рестарт прод-ботов"}
        enq, skip = o._revizor_enqueue_tasks([f], [], _REV_NOW)
        self.assertEqual((enq, skip), (0, 1))
        self.assertEqual([t for t in self.fb.tasks.values() if t["status"] == "new"], [])


class TestRevizorGuards(Base):
    """Маркер-гарды owner-карточки в process_new/approved/approval_timeouts."""

    def test_orphan_card_in_new_closed_not_run(self):
        tid = self.fb.add(status="new", task_text=o.REVIZOR_OWNER_MARK + " осиротела")
        ran = {"n": 0}
        _save = o.run_task
        self.addCleanup(lambda: setattr(o, "run_task", _save))
        o.run_task = lambda *a, **k: ran.__setitem__("n", ran["n"] + 1) or ("done", "x")
        o.process_new()
        self.assertEqual(ran["n"], 0)                                  # headless НЕ запускали
        self.assertEqual(self.fb.tasks[tid]["status"], "done")

    def test_approval_timeout_skips_card(self):
        tid = self.fb.add(status="needs_approval", task_text=o.REVIZOR_OWNER_MARK + " карточка",
                          updated="2000-01-01T00:00:00+00:00")        # заведомо просрочена
        o.process_approval_timeouts()
        self.assertEqual(self.fb.tasks[tid]["status"], "needs_approval")  # НЕ погашена по таймауту

    def test_approved_card_acknowledged_not_rerun(self):
        tid = self.fb.add(status="approved", task_text=o.REVIZOR_OWNER_MARK + " карточка")
        ran = {"n": 0}
        _save = o.run_task
        self.addCleanup(lambda: setattr(o, "run_task", _save))
        o.run_task = lambda *a, **k: ran.__setitem__("n", ran["n"] + 1) or ("done", "x")
        o.process_approved()
        self.assertEqual(ran["n"], 0)                                  # approve не гонит headless
        self.assertEqual(self.fb.tasks[tid]["status"], "done")
        self.assertIn("приняты", self.fb.tasks[tid]["result"])


# ------------------- РЕВИЗОР: КОНТУРНЫЕ ТЕСТЫ (шаг 6/7 родителя 262) ---------------------
# Сквозной прогон ВСЕГО контура ревизора: реальная sqlite-БД drafts → maybe_revizor → revizor_tick
# (отбор окон/сборка пакетов) → _revizor_route → _revizor_consult (парс/валидация) → enqueue задач /
# owner-карточка / бюджет / дедуп / метка на диск. Замокан ТОЛЬКО _thinker_exec (граница реального
# claude) — всё остальное (парс JSON, маршрутизация, очередь FakeBridge, restart-proof метка) боевое.
# Это отличает контурные тесты от юнитов выше (TestRevizor*): там части проверены по отдельности,
# здесь — их СЦЕПКА в один демонский тик. Дефолт DIALOG_REVIZOR=0 проверен отдельным классом ниже.

_REV_ACTIVE_TS = "2030-01-01T00:00:00+00:00"   # заведомо новее любой метки прогона → окно активно
_REV_STALE_TS = "2020-01-01T00:00:00+00:00"    # заведомо старее метки → окно НЕ отбираем


def _make_drafts_db(path, rows):
    """Реальная moderation_ipc.db с таблицей drafts (recon §A) для контурного отбора окон."""
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, client_id INTEGER, client_name TEXT, "
              "incoming TEXT, transcript TEXT, draft TEXT, final_text TEXT, status TEXT, updated_ts TEXT)")
    for r in rows:
        c.execute("INSERT INTO drafts (client_id, client_name, incoming, transcript, draft, final_text, "
                  "status, updated_ts) VALUES (?,?,?,?,?,?,?,?)",
                  (r.get("client_id"), r.get("client_name"), r.get("incoming"), r.get("transcript"),
                   r.get("draft"), r.get("final_text"), r.get("status", "sent"),
                   r.get("updated_ts", _REV_ACTIVE_TS)))
    c.commit(); c.close()


class TestRevizorContour(Base):
    """Сквозной контур ревизора: реальная БД → maybe_revizor → route → очередь/карточка/метка.
    Мок только на границе LLM (_thinker_exec); вердикты думателя инъектируем по client_id окна."""

    def setUp(self):
        super().setUp()
        self._save_r = (o._revizor_on, o._thinker_exec)
        o._revizor_on = lambda: True                 # флаг включён (дефолт-0 проверяется отдельным классом)
        self.notes = []
        o._cowork = lambda line: self.notes.append(line)
        self.audited = []                            # client_id окон, дошедших до думателя
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "revizor_state.json")
        self.db = os.path.join(self.tmp, "moderation_ipc.db")

    def tearDown(self):
        (o._revizor_on, o._thinker_exec) = self._save_r
        super().tearDown()

    def _thinker(self, verdicts):
        """verdicts: client_id(int) → JSON-строка ответа думателя | None (сбой/таймаут = None, как
        боевой _thinker_exec). Инъектируем на границе LLM; фиксируем, какие окна аудировались."""
        def fake(prompt, timeout, tag):
            m = re.search(r"client_id=(\d+)", prompt)
            cid = int(m.group(1)) if m else None
            self.audited.append(cid)
            return verdicts.get(cid)
        o._thinker_exec = fake

    def _seed_state(self, ts):
        o._revizor_write_state(ts, path=self.state)

    def _tasks_with(self, needle):
        return [t for t in self.fb.tasks.values() if needle in str(t.get("task_text"))]

    def test_contour_full_flow_task_owner_noise(self):
        # три активных окна (task/owner/noise) + одно протухшее (НЕ аудируем)
        _make_drafts_db(self.db, [
            {"client_id": 100, "incoming": "первым же дал модель и даты, а мне анкету", "updated_ts": _REV_ACTIVE_TS},
            {"client_id": 200, "incoming": "спорный тариф на месяц?", "updated_ts": _REV_ACTIVE_TS},
            {"client_id": 300, "incoming": "обычный вопрос", "updated_ts": _REV_ACTIVE_TS},
            {"client_id": 400, "incoming": "давно молчит", "updated_ts": _REV_STALE_TS},
        ])
        self._thinker({
            100: '[{"class":"ж","evidence":"анкета на первом сообщении","action":"task",'
                 '"task_text":"фикс детекта: модель+даты первым сообщением → котировать, не анкетировать"}]',
            200: '[{"class":"г","evidence":"спорный тариф на месяц","action":"owner","task_text":""}]',
            300: '[{"class":"в","evidence":"ложное срабатывание","action":"noise","task_text":""}]',
        })
        t0 = _REV_NOW
        self._seed_state(t0)                                   # не бутстрап → следующий прогон тикает
        now1 = t0 + o.REVIZOR_SEC + 1
        out = o.maybe_revizor(now=now1, db_path=self.db, state_path=self.state)

        self.assertEqual(sorted(self.audited), [100, 200, 300])   # протухшее окно 400 НЕ аудировали
        self.assertEqual([p["client_id"] for p in out], [100, 200, 300])
        # task → зелёный родитель дирижёру с маркером даты/класса
        dec = [t for t in self.fb.tasks.values() if t.get("from") == o.PC_LOCAL_DEC_FROM]
        self.assertEqual(len(dec), 1)
        self.assertTrue(dec[0]["task_text"].startswith(f"[ревизор дата={o._revizor_today(now1)} класс=ж]"))
        self.assertIn("котировать", dec[0]["task_text"])
        # owner → одна сводная карточка в инбокс одобрений
        cards = [t for t in self.fb.tasks.values() if t["status"] == "needs_approval"
                 and o._is_revizor_owner_card(t.get("task_text"))]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["topic"], o.NEEDS_APPROVAL_TOPIC)
        self.assertIn("спорный тариф", cards[0]["what"])
        # noise наружу не вынесен (ни задачи, ни карточки под класс «в»)
        self.assertEqual(self._tasks_with("класс=в]"), [])
        # метка сдвинута вперёд (restart-proof троттл)
        self.assertEqual(o._revizor_read_state(path=self.state)["ts"], now1)

    def test_contour_budget_two_tasks_per_day(self):
        _make_drafts_db(self.db, [{"client_id": c, "incoming": f"окно {c}"} for c in (100, 200, 300)])
        mk = lambda cls: ('[{"class":"%s","evidence":"улика %s","action":"task",'
                          '"task_text":"фикс класса %s"}]') % (cls, cls, cls)
        self._thinker({100: mk("а"), 200: mk("б"), 300: mk("в")})   # 3 РАЗНЫХ класса → дедуп не при чём
        self._seed_state(_REV_NOW)
        o.maybe_revizor(now=_REV_NOW + o.REVIZOR_SEC + 1, db_path=self.db, state_path=self.state)
        dec = [t for t in self.fb.tasks.values() if t.get("from") == o.PC_LOCAL_DEC_FROM]
        self.assertEqual(len(dec), 2)                          # бюджет ≤2/сутки: третья отложена
        self.assertEqual(self._tasks_with("класс=в]"), [])     # именно третья (в порядке окон) не встала

    def test_contour_dedup_task_by_class_restart_proof(self):
        # прогон 1: класс «а» встал в очередь; «рестарт» = свежий вызов, дедуп читает МАРКЕР из очереди
        _make_drafts_db(self.db, [{"client_id": 100, "incoming": "окно"}])
        self._thinker({100: '[{"class":"а","evidence":"e","action":"task","task_text":"фикс класса а"}]'})
        self._seed_state(_REV_NOW)
        o.maybe_revizor(now=_REV_NOW + o.REVIZOR_SEC + 1, db_path=self.db, state_path=self.state)
        self.assertEqual(len(self._tasks_with("класс=а]")), 1)
        # прогон 2 (период снова прошёл, окно снова активно): тот же класс → дедуп по очереди, не дублируем
        o.maybe_revizor(now=_REV_NOW + 2 * (o.REVIZOR_SEC + 1), db_path=self.db, state_path=self.state)
        self.assertEqual(len(self._tasks_with("класс=а]")), 1)  # restart-proof дедуп: вторая не встала

    def test_contour_owner_card_single_across_runs(self):
        _make_drafts_db(self.db, [{"client_id": 200, "incoming": "спорно"}])
        # улика — уникальный токен, которого НЕТ в шапке карточки → счётчик ловит именно дубль строки
        self._thinker({200: '[{"class":"г","evidence":"улика-спор-777","action":"owner","task_text":""}]'})
        self._seed_state(_REV_NOW)
        o.maybe_revizor(now=_REV_NOW + o.REVIZOR_SEC + 1, db_path=self.db, state_path=self.state)
        o.maybe_revizor(now=_REV_NOW + 2 * (o.REVIZOR_SEC + 1), db_path=self.db, state_path=self.state)
        cards = [t for t in self.fb.tasks.values() if t["status"] == "needs_approval"
                 and o._is_revizor_owner_card(t.get("task_text"))]
        self.assertEqual(len(cards), 1)                        # редактируем ту же карточку, не плодим вторую
        self.assertEqual(cards[0]["what"].count("улика-спор-777"), 1)   # дедуп идентичной строки-цитаты

    def test_contour_failsafe_thinker_timeout_silence(self):
        # думатель молчит по ВСЕМ окнам (таймаут/сбой = None) → наружу тишина + NOTE, но метку ставим
        _make_drafts_db(self.db, [{"client_id": 100, "incoming": "a"}, {"client_id": 200, "incoming": "b"}])
        self._thinker({100: None, 200: None})
        self._seed_state(_REV_NOW)
        now1 = _REV_NOW + o.REVIZOR_SEC + 1
        o.maybe_revizor(now=now1, db_path=self.db, state_path=self.state)
        self.assertEqual(sorted(self.audited), [100, 200])     # дошли до думателя — он молчал
        self.assertEqual([t for t in self.fb.tasks.values()], [])   # ни задач, ни карточек
        self.assertTrue(any("не ответил" in n for n in self.notes))
        self.assertEqual(o._revizor_read_state(path=self.state)["ts"], now1)  # троттл всё равно сдвинут

    def test_contour_failsafe_route_crash_still_marks(self):
        # маршрутизация упала внутри тика → maybe_revizor гасит исключение и ВСЁ РАВНО ставит метку
        _make_drafts_db(self.db, [{"client_id": 100, "incoming": "a"}])
        self._thinker({100: '[{"class":"ж","action":"task","task_text":"фикс"}]'})
        save = o._revizor_route
        self.addCleanup(lambda: setattr(o, "_revizor_route", save))
        o._revizor_route = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bang"))
        self._seed_state(_REV_NOW)
        now1 = _REV_NOW + o.REVIZOR_SEC + 1
        try:
            o.maybe_revizor(now=now1, db_path=self.db, state_path=self.state)
        except Exception as e:
            self.fail(f"maybe_revizor не должен пробрасывать сбой маршрутизации: {e}")
        self.assertEqual(o._revizor_read_state(path=self.state)["ts"], now1)   # метка поставлена (не зациклимся)

    def test_contour_throttle_restart_proof_no_audit(self):
        # свежая метка на диске гейтит повторный прогон ДО периода — БД не читаем, окна не аудируем
        _make_drafts_db(self.db, [{"client_id": 100, "incoming": "a"}])
        self._thinker({100: '[{"class":"ж","action":"task","task_text":"фикс"}]'})
        self._seed_state(_REV_NOW)
        out = o.maybe_revizor(now=_REV_NOW + 5, db_path=self.db, state_path=self.state)  # период НЕ прошёл
        self.assertIsNone(out)
        self.assertEqual(self.audited, [])                     # думатель не дёрнут — контур не тикал
        self.assertEqual(o._revizor_read_state(path=self.state)["ts"], _REV_NOW)  # метку не двигаем


class TestRevizorDefaultOff(unittest.TestCase):
    """Дефолт DIALOG_REVIZOR=0: без флага ревизор молчит целиком — ни тика, ни метки, ни чтения БД.
    Проверяем НАСТОЯЩИЙ _revizor_on (не мок) при отсутствующем/нулевом флаге в окружении."""

    def test_default_flag_is_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIALOG_REVIZOR", None)             # флаг не задан → дефолт
            self.assertFalse(o._revizor_on())

    def test_flag_zero_is_off(self):
        with mock.patch.dict(os.environ, {"DIALOG_REVIZOR": "0"}, clear=False):
            self.assertFalse(o._revizor_on())

    def test_flag_one_is_on(self):
        with mock.patch.dict(os.environ, {"DIALOG_REVIZOR": "1"}, clear=False):
            self.assertTrue(o._revizor_on())

    def test_maybe_revizor_off_no_state_no_db(self):
        tmp = tempfile.mkdtemp()
        state = os.path.join(tmp, "revizor_state.json")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DIALOG_REVIZOR", None)
            # db_path указывает на несуществующий файл — если бы ревизор тикал, увидели бы попытку чтения;
            # но выключенный ревизор возвращает None ДО обращения к БД/метке.
            out = o.maybe_revizor(now=_REV_NOW, db_path=os.path.join(tmp, "no.db"), state_path=state)
        self.assertIsNone(out)
        self.assertFalse(os.path.exists(state))                # метку НЕ ставим → демон байт-в-байт прежний


class TestNotifyHygiene(unittest.TestCase):
    """Гигиена уведомлений пульта (задача #: гигиена pc_agent):
      • done/failed задач в личку НЕ дублируются (видны в темах постановки 328/829);
      • needs_approval — call-to-action, пуш в личку остаётся;
      • критические инциденты идут через _notify_critical (маршрут инбокс 1160 → личка-фолбэк)."""

    def setUp(self):
        self._save = (o._notify, o._notify_critical)
        self.dm, self.crit = [], []
        o._notify = lambda text: self.dm.append(text)
        o._notify_critical = lambda text: self.crit.append(text)

    def tearDown(self):
        (o._notify, o._notify_critical) = self._save

    def test_done_not_pushed_to_dm(self):
        o._notify_task("done", 42, "готово")
        self.assertEqual(self.dm, [])                 # done-дубль в личку НЕ шлётся

    def test_failed_not_pushed_to_dm(self):
        o._notify_task("failed", 42, "провал")
        self.assertEqual(self.dm, [])                 # failed-дубль в личку НЕ шлётся

    def test_needs_approval_still_pushed(self):
        o._notify_task("needs_approval", 42, "нужно да")
        self.assertEqual(len(self.dm), 1)             # call-to-action — оставляем пуш
        self.assertIn("#42", self.dm[0])

    def test_notify_critical_spawns_dispatch_with_flag(self):
        """_notify_critical зовёт dispatch_notify с флагом --critical (маршрут форум→личка)."""
        # снимаем моки setUp: тестируем НАСТОЯЩИЙ _notify_critical
        (o._notify, o._notify_critical) = self._save
        captured = {}
        real_popen = o.subprocess.Popen

        def fake_popen(argv, *a, **k):
            captured["argv"] = argv
            class _P:  # заглушка процесса
                pass
            return _P()
        o.subprocess.Popen = fake_popen
        try:
            o._notify_critical("тест-инцидент")
        finally:
            o.subprocess.Popen = real_popen
        self.assertIn("--critical", captured["argv"])
        self.assertIn("тест-инцидент", captured["argv"])
        self.assertIn(o.DNOTIFY, captured["argv"])


class TestChainControl(TestLocalDecChain):
    """Кнопки управления цепью (стоп/статус) из карточки дирижёра. Механика стопа = вмешательство
    21:05 13.07: текущий шаг доигрывает, дальше не релизим; сводка с нотой «остановлено владельцем».
    Осиротевших шагов нет, статус честный, регресс релиза цел."""

    def _sum_result(self, pid):
        s = self._summaries(pid)
        return s[0]["result"] if s else ""

    # --- СТОП ---

    def test_stop_posts_summary_with_owner_note(self):
        pid = self._mk_parent_done(["1. A", "2. B", "3. C"])
        self._mk_step(pid, 1, 3, status="done", text="A", result="RESULT: сделал A")
        ok, msg = o._loc_stop_chain(pid)
        self.assertTrue(ok)
        self.assertIn(f"#{pid}", msg)
        self.assertEqual(len(self._summaries(pid)), 1)
        self.assertIn("остановлено владельцем", self._sum_result(pid))  # нота restart-proof в тексте

    def test_stop_no_next_release_even_after_restart(self):
        # сводка есть в очереди → тик цепи короткозамкнут ДО релиза, даже когда память процесса чиста
        pid = self._mk_parent_done(["1. A", "2. B", "3. C"])
        self._mk_step(pid, 1, 3, status="done", text="A")
        self.assertTrue(o._loc_stop_chain(pid)[0])
        o._loc_summarized.clear()                       # имитируем рестарт демона (память пуста)
        o.process_local_chains()
        news = [t for t in self._news() if str(t.get("task_text") or "").startswith("[шаг 2/3")]
        self.assertEqual(news, [], "после стопа релизнулся следующий шаг (осиротил бы цепь)")

    def test_stop_current_step_finishes_no_orphan(self):
        # текущий шаг in_progress на момент стопа → доигрывает; его done НЕ релизит следующий
        pid = self._mk_parent_done(["1. A", "2. B", "3. C"])
        step2 = self._mk_step(pid, 2, 3, status="in_progress", text="B")
        self._mk_step(pid, 1, 3, status="done", text="A")
        self.assertTrue(o._loc_stop_chain(pid)[0])
        o._loc_summarized.clear()
        self.fb.tasks[step2]["status"] = "done"         # текущий шаг доработал сам
        self.fb.tasks[step2]["result"] = "RESULT: доделал B"
        o.process_local_chains()
        news = [t for t in self._news() if str(t.get("task_text") or "").startswith("[шаг 3/3")]
        self.assertEqual(news, [], "done текущего шага после стопа релизнул следующий")
        self.assertEqual(len(self._summaries(pid)), 1, "сводка задублировалась")

    def test_stop_idempotent_when_already_summarized(self):
        pid = self._mk_parent_done(["1. A", "2. B"])
        self._mk_step(pid, 1, 2, status="done", text="A")
        self.assertTrue(o._loc_stop_chain(pid)[0])
        n_before = len(self._summaries(pid))
        o._loc_summarized.clear()                       # память сброшена, но сводка в очереди
        ok, msg = o._loc_stop_chain(pid)
        self.assertTrue(ok)
        self.assertIn("уже закрыта", msg)
        self.assertEqual(len(self._summaries(pid)), n_before, "второй стоп задублировал сводку")

    def test_stop_unknown_chain_not_found(self):
        ok, msg = o._loc_stop_chain(99999)
        self.assertFalse(ok)
        self.assertIn("не найдена", msg)

    def test_stop_bridge_unreadable(self):
        with mock.patch.object(o, "_loc_fetch_items", lambda: None):
            ok, msg = o._loc_stop_chain(7)
        self.assertFalse(ok)
        self.assertIn("недоступна", msg)

    # --- СТАТУС ---

    def test_status_in_progress_honest(self):
        pid = self._mk_parent_done(["1. A", "2. B", "3. C", "4. D", "5. E"])
        self._mk_step(pid, 1, 5, status="done", text="A", result="RESULT: готово A")
        self._mk_step(pid, 2, 5, status="in_progress", text="B")
        st = o._loc_chain_status(pid)
        self.assertIn("шаг 2/5", st)                    # текущий шаг честно из очереди
        self.assertIn("Последний done: шаг 1/5", st)
        self.assertIn("готово A", st)

    def test_status_no_done_yet(self):
        pid = self._mk_parent_done(["1. A", "2. B"])
        self._mk_step(pid, 1, 2, status="in_progress", text="A")
        st = o._loc_chain_status(pid)
        self.assertIn("шаг 1/2", st)
        self.assertIn("Последний done: пока нет", st)

    def test_status_after_stop_shows_closed(self):
        pid = self._mk_parent_done(["1. A", "2. B"])
        self._mk_step(pid, 1, 2, status="done", text="A", result="RESULT: A")
        o._loc_stop_chain(pid)
        st = o._loc_chain_status(pid)
        self.assertIn("закрыта", st)
        self.assertIn("Последний done: шаг 1/2", st)

    def test_status_unknown_chain(self):
        self.assertIn("не найдена", o._loc_chain_status(99999))

    # --- РЕГРЕСС РЕЛИЗА: стоп одной цепи не глушит другую ---

    def test_release_regression_other_chain_unaffected(self):
        stopped = self._mk_parent_done(["1. A", "2. B"])
        self._mk_step(stopped, 1, 2, status="done", text="A")
        live = self._mk_parent_done(["1. X", "2. Y"])
        self._mk_step(live, 1, 2, status="done", text="X")
        self.assertTrue(o._loc_stop_chain(stopped)[0])
        o._loc_summarized.clear()
        o.process_local_chains()
        # остановленная НЕ релизит шаг 2; живая — релизит штатно
        stopped_next = [t for t in self._news()
                        if str(t.get("task_text") or "").startswith(f"[шаг 2/2 родитель {stopped}]")]
        live_next = [t for t in self._news()
                     if str(t.get("task_text") or "").startswith(f"[шаг 2/2 родитель {live}]")]
        self.assertEqual(stopped_next, [])
        self.assertEqual(len(live_next), 1)


class TestChainCardDedup(unittest.TestCase):
    """Класс-голдены спам-лупа 14:25 14.07 (залп «План цепи #101» при рестарте демона):
    «отправлено» — в state-файле (restart-proof, ключ = родитель+sha1 текста, т.е. версия
    плана+шаг), финализированные цепи не анонсируются никогда, событие = ровно одна карточка."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self._tmp.name, "chain_cards.json")
        self.sent = []
        self.spawn = lambda pid, text: self.sent.append((pid, text))

    def tearDown(self):
        self._tmp.cleanup()

    def test_event_sent_exactly_once(self):
        # событие → ровно одна карточка; повтор того же события — тишина.
        r1 = o._notify_chain_card(101, "🧩 План цепи #101: 2 шагов", state_path=self.state,
                                  spawn=self.spawn)
        r2 = o._notify_chain_card(101, "🧩 План цепи #101: 2 шагов", state_path=self.state,
                                  spawn=self.spawn)
        self.assertTrue(r1)
        self.assertFalse(r2)
        self.assertEqual(len(self.sent), 1)

    def test_restart_zero_repeats(self):
        # ГЛАВНЫЙ голден: «рестарт демона» = новая память процесса, ТОТ ЖЕ state-файл →
        # ноль повторов по всем историческим событиям.
        events = [f"▶️ Цепь #101: шаг {j}/5 в очереди." for j in range(1, 6)]
        for e in events:
            o._notify_chain_card(101, e, state_path=self.state, spawn=self.spawn)
        self.assertEqual(len(self.sent), 5)
        # «рестарт»: свежий список отправок (память умерла), state-файл пережил
        self.sent.clear()
        for e in events:
            o._notify_chain_card(101, e, state_path=self.state, spawn=self.spawn)
        self.assertEqual(self.sent, [])                      # НОЛЬ повторов после рестарта

    def test_different_events_both_sent(self):
        o._notify_chain_card(200, "▶️ Цепь #200: шаг 1/3 в очереди.", state_path=self.state,
                             spawn=self.spawn)
        o._notify_chain_card(200, "▶️ Цепь #200: шаг 2/3 в очереди.", state_path=self.state,
                             spawn=self.spawn)
        self.assertEqual(len(self.sent), 2)                  # разные события — обе уходят

    def test_finalized_chain_always_silent(self):
        # финализированная цепь → тишина НАВСЕГДА, даже для новых текстов (артефакты 101).
        o._loc_mark_chain_final(101, path=self.state)
        r = o._notify_chain_card(101, "🧩 План цепи #101: новая версия", state_path=self.state,
                                 spawn=self.spawn)
        self.assertFalse(r)
        self.assertEqual(self.sent, [])
        # соседняя живая цепь не задета
        self.assertTrue(o._notify_chain_card(102, "🧩 План цепи #102", state_path=self.state,
                                             spawn=self.spawn))

    def test_sent_history_capped(self):
        for i in range(o.CHAIN_CARD_SENT_MAX + 50):
            o._notify_chain_card(300, f"событие {i}", state_path=self.state, spawn=self.spawn)
        st = o._chain_cards_read(self.state)
        self.assertLessEqual(len(st["sent"]), o.CHAIN_CARD_SENT_MAX)   # история не пухнет


class TestOwnerCardDelivery(unittest.TestCase):
    """Доставка owner-карточки НЕЯСНОГО урока в 1160: СИНХРОННО, с подтверждением канала (send
    инъектируется — реального Bot API/сети не касаемся). Разрыв, который чиним: fire-and-forget
    _notify_critical возвращал None → рапорт всегда «1160 недоступно», хотя карточка реально
    доходила. Теперь _deliver_owner_card отдаёт (канал, ok) → рапорт честный «доставлено через X»."""

    def test_inbox_channel_mapped_human(self):
        # send_critical дошёл инбоксом 1160 → человекочитаемый канал + True
        ch, ok = o._deliver_owner_card("карточка", send=lambda t: ("inbox", True))
        self.assertTrue(ok)
        self.assertEqual(f"инбокс {o.INBOX_TOPIC_ID}", ch)

    def test_fallback_dm_channel_mapped_human(self):
        # инбокс лёг → send_critical ушёл в личку-фолбэк; канал честно назван фолбэком
        ch, ok = o._deliver_owner_card("карточка", send=lambda t: ("DM", True))
        self.assertTrue(ok)
        self.assertEqual("личку (фолбэк)", ch)

    def test_not_delivered_returns_empty_channel(self):
        # оба канала не прошли (ok=False) → пустой канал + False (рапорт скажет «не доставлена»)
        self.assertEqual(("", False), o._deliver_owner_card("карточка", send=lambda t: ("DM", False)))

    def test_send_exception_is_failsafe(self):
        # сбой доставки НЕ роняет тик демона → ('', False)
        self.assertEqual(("", False),
                         o._deliver_owner_card("карточка", send=lambda t: (_ for _ in ()).throw(OSError("net"))))


class TestUnclearLessonHonestReport(Base):
    """ГОЛДЕН живого провала (урок-цикл): неясный урок → карточка ДОСТАВЛЕНА, рапорт БЕЗ ложного
    «1160 недоступно». Сквозь реальный _handle_lesson + lesson_router на FakeBridge; доставку
    подтверждаем инъекцией _deliver_owner_card (сеть/Bot API не трогаем)."""

    def _task(self, remark):
        return ("[урок:правка от @danya] родитель 292 — замечание менеджера\n"
                "окно диалога: Света (999) (client_id=999) · черновик #33\n"
                "карточка модер-группы: msg=90510\n"
                f"Замечание: {remark}\n"
                "Исходный черновик: Здравствуйте! Чем помочь?")

    def test_unclear_delivered_report_is_honest(self):
        self._save_owner = o._deliver_owner_card
        self.addCleanup(lambda: setattr(o, "_deliver_owner_card", self._save_owner))
        o._deliver_owner_card = lambda text: (f"инбокс {o.INBOX_TOPIC_ID}", True)   # карточка ДОШЛА
        tid = self.fb.add(task_text=self._task("плохо, переделай"))
        o._handle_lesson(tid, self.fb.tasks[tid]["task_text"])
        t = self.fb.tasks[tid]
        self.assertEqual("done", t["status"])
        self.assertIn("доставлено через инбокс", t["result"])
        self.assertNotIn("недоступ", t["result"])           # НЕТ ложной жалобы «1160 недоступно»
        self.assertNotIn("не доставлена", t["result"])

    def test_unclear_undelivered_report_is_honest_too(self):
        # обратная честность: доставка реально провалилась → рапорт говорит «не доставлена» (не врёт в плюс)
        self._save_owner = o._deliver_owner_card
        self.addCleanup(lambda: setattr(o, "_deliver_owner_card", self._save_owner))
        o._deliver_owner_card = lambda text: ("", False)
        tid = self.fb.add(task_text=self._task("что-то не то"))
        o._handle_lesson(tid, self.fb.tasks[tid]["task_text"])
        self.assertIn("не доставлена", self.fb.tasks[tid]["result"])


class TestLessonWaitReaper(Base):
    """КЛАСС-ФИКС инцидента 354: low-урок в ожидании ответа учителя (переспрос «верно?») ЖИВ — не
    гибнет ложно от process_stuck_singles на 90-й минуте. heartbeat тикает; ЕДИНСТВЕННЫЙ предел
    ожидания — 24ч → карточка владельцу в 1160; ответ учителя «да» после ДОЛГОЙ паузы → resume
    берёт урок в работу; регресс реапера на НАСТОЯЩИХ зависаниях (не в ожидании) цел."""

    _T0 = 1_000_000.0        # база epoch для created_at/now (детерминизм — время инъектируем)

    def setUp(self):
        super().setUp()
        self._save_owner = o._deliver_owner_card         # инъекции owner-канала не должны течь в другие тесты
        self.addCleanup(lambda: setattr(o, "_deliver_owner_card", self._save_owner))

    def _pending(self, route="style", created_ago=0.0):
        return {"text": "[урок:правка от @danya] замечание\nЗамечание: пиши короче, без воды",
                "route": route, "class": "СТИЛЬ", "reading": "писать короче",
                "plan": "добавить правило в книгу правил", "remark": "пиши короче, без воды",
                "draft": "", "window": "Света (999)", "who": "@danya", "card_msg_id": "90510",
                "created_at": self._T0 - created_ago}

    def _waiting_dec(self, route="style", created_ago=0.0):
        return {"route": route, "confidence": "low", "delegate": False, "status": "waiting",
                "pending_low": self._pending(route, created_ago), "llm_class": "СТИЛЬ",
                "result": "🤔 неуверенно → спросил учителя «верно?», жду «да»"}

    def _enter(self, tid, route="style", created_ago=0.0):
        # штатный путь: живой канал урок: вернул low-waiting dec → _handle_lesson уводит урок в ожидание.
        # #112 шаг 8/8: _handle_lesson теперь зовёт route_lesson_urok (2-я ось; low-wait рождается в её
        # unsure-фолбэке на handle_lesson_task) — патчим ИМЕННО этот шов, поведение ожидания то же.
        with mock.patch.object(o.lesson_router, "route_lesson_urok",
                               return_value=self._waiting_dec(route, created_ago)):
            o._handle_lesson(tid, self.fb.tasks[tid]["task_text"])

    def test_enter_wait_keeps_in_progress_and_saves(self):
        tid = self.fb.add(status="in_progress", updated=iso_ago(10))
        self._enter(tid)
        self.assertEqual("in_progress", self.fb.tasks[tid]["status"])   # НЕ закрыта — ждём подтверждения
        self.assertIn(tid, o._lesson_wait_ids())                         # ожидание сохранено на диск
        self.assertGreaterEqual(self.fb.hb.get(tid, 0), 1)               # heartbeat тикнут сразу

    def test_reaper_skips_waiting_lesson(self):
        # даже со СТАРЫМ updated (>90 мин) ждущий урок исключён из реапа — не орфан
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 3600))
        self._enter(tid)
        o.process_stuck_singles()
        self.assertEqual("in_progress", self.fb.tasks[tid]["status"])   # ЖИВ, не failed

    def test_waiting_2h_alive(self):
        # ГОЛДЕН: урок ждёт 2ч+ (<24ч) → жив; тик держит heartbeat, реапер молчит
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 1000))
        self._enter(tid)
        hb0 = self.fb.hb.get(tid, 0)
        o._deliver_owner_card = lambda *a, **k: ("инбокс 1160", True)
        o.process_lesson_waits(now=self._T0 + 2 * 3600)
        o.process_stuck_singles()
        self.assertEqual("in_progress", self.fb.tasks[tid]["status"])
        self.assertGreater(self.fb.hb.get(tid, 0), hb0)                  # heartbeat тикнул ещё раз
        self.assertIn(tid, o._lesson_wait_ids())                         # ожидание не снято (<24ч)

    def test_24h_timeout_to_owner_is_sole_limit(self):
        # ЕДИНСТВЕННЫЙ предел: молчит учитель >24ч → карточка владельцу 1160 + задача done + снятие
        tid = self.fb.add(status="in_progress", updated=iso_ago(60))
        self._enter(tid)
        cards = []
        o._deliver_owner_card = lambda text, *a, **k: (cards.append(text) or ("инбокс 1160", True))
        o.process_lesson_waits(now=self._T0 + 24 * 3600 + 60)
        self.assertEqual("done", self.fb.tasks[tid]["status"])
        self.assertNotIn(tid, o._lesson_wait_ids())                     # ожидание снято
        self.assertEqual(1, len(cards))                                  # ровно одна карточка владельцу
        o.process_lesson_waits(now=self._T0 + 25 * 3600)                # повторный тик — не задваивает
        self.assertEqual(1, len(cards))

    def test_resume_yes_after_long_pause(self):
        # ГОЛДЕН: ответ учителя «да» после ДОЛГОЙ паузы (10ч, <24ч) → resume берёт урок В РАБОТУ
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 2000))
        self._enter(tid)
        o._deliver_owner_card = lambda *a, **k: ("инбокс 1160", True)
        o.process_lesson_waits(now=self._T0 + 10 * 3600)                # долго ждал — но жив
        self.assertEqual("in_progress", self.fb.tasks[tid]["status"])
        added = []
        dec = o.resume_lesson_wait(tid, "да", is_approver=True,
                                   append_style=lambda r: (added.append(r) or "added"),
                                   reply_moderation=lambda c, t: True)
        self.assertEqual("approved", dec.get("resumed"))                # учитель подтвердил трактовку
        self.assertEqual("done", self.fb.tasks[tid]["status"])          # урок доведён
        self.assertNotIn(tid, o._lesson_wait_ids())                     # ожидание снято
        self.assertEqual(1, len(added))                                  # правило записано в книгу правил

    def test_resume_yes_non_approver_stays_waiting(self):
        # «да» НЕ от аппрувера → игнор, урок жив и ждёт уполномоченного
        tid = self.fb.add(status="in_progress", updated=iso_ago(60))
        self._enter(tid)
        dec = o.resume_lesson_wait(tid, "да", is_approver=False,
                                   append_style=lambda r: "added", reply_moderation=lambda c, t: True)
        self.assertEqual("waiting", dec.get("status"))
        self.assertEqual("in_progress", self.fb.tasks[tid]["status"])   # не закрыт
        self.assertIn(tid, o._lesson_wait_ids())                         # ожидание НЕ снято

    def test_reaper_still_reaps_real_stuck(self):
        # РЕГРЕСС: настоящий зависший одиночка (НЕ в ожидании) реапится как прежде
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 300))
        o.process_stuck_singles()
        self.assertEqual("failed", self.fb.tasks[tid]["status"])
        self.assertIn("ПК-таймаут", self.fb.tasks[tid]["result"])

    def test_selfheal_drops_wait_when_not_in_progress(self):
        # задача закрыта вне resume-шва → ожидание снимается (self-heal), тик не трогает чужой статус
        tid = self.fb.add(status="in_progress", updated=iso_ago(60))
        self._enter(tid)
        self.fb.tasks[tid]["status"] = "done"
        o.process_lesson_waits(now=self._T0 + 60)
        self.assertNotIn(tid, o._lesson_wait_ids())

    def test_poll_once_protects_waiting(self):
        # весь цикл: process_lesson_waits ДО реапера → ждущий урок переживает poll_once
        tid = self.fb.add(status="in_progress", updated=iso_ago(o.PC_SINGLE_STALE + 500))
        self._enter(tid)
        import time as _time                                            # poll_once не инъектирует now →
        st = o._lesson_waits_read()                                     # ставим свежую метку (age<24ч под реальным now)
        st[str(tid)]["created_at"] = _time.time()
        o._lesson_waits_write(st)
        o._deliver_owner_card = lambda *a, **k: ("инбокс 1160", True)
        with mock.patch.object(o, "_write_heartbeat", lambda: None), \
             mock.patch.object(o, "process_new", lambda: None), \
             mock.patch.object(o, "process_local_chains", lambda: None):
            o.poll_once()
        self.assertEqual("in_progress", self.fb.tasks[tid]["status"])   # жив после полного цикла


class _FakeHTTPResp:
    """Мини-контекст-менеджер под urlopen: .read() → заранее заданное тело (bytes)."""
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._body


class TestLessonReaskDelivery(Base):
    """КЛАСС-ФИКС инцидента #102: ПЕРЕСПРОС low-урока «верно?» РЕАЛЬНО доходит до учителя — реплаем в
    модер-группу на карточку черновика, с проверкой факта доставки (message_id) и ретраем. Недоставлен
    после ретраев → урок НЕ висит молча: падает failed с диагнозом + карточка-уточнение владельцу в 1160.
    Переспрос и ack приёма урока («Принял…») — РАЗНЫЕ сообщения (ack шлёт moderation_bot, здесь не дублируем)."""

    def setUp(self):
        super().setUp()
        self._save_owner = o._deliver_owner_card          # инъекции owner-канала не должны течь в другие тесты
        self.addCleanup(lambda: setattr(o, "_deliver_owner_card", self._save_owner))

    def _task(self, remark, card="368"):
        return ("[урок:правка от @turbophuket] родитель 292 — замечание менеджера в копилку обучения\n"
                "окно диалога: @cryptopeppa (client_id=529849022) · черновик #303\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                "Исходный черновик: Локацию и данные получил, спасибо 🤝")

    def _force_low(self, cls="ФАКТ", route=None):
        # думатель 1-й оси вернул confidence=low по маршруту route (реальный claude не дёргаем).
        # #112 шаг 8/8: _handle_lesson входит через route_lesson_urok (2-я ось); low-wait живёт в её
        # unsure-фолбэке на handle_lesson_task — форсим classify_lesson_type=unsure, чтобы канал ушёл
        # в фолбэк, где и срабатывает низкоуверенный LLM-путь (иначе keyword-тип увёл бы в behavior/code).
        route = route if route is not None else o.lesson_router.FACT
        return mock.patch.multiple(
            o.lesson_router,
            _lesson_llm_enabled=lambda: True,
            classify_lesson_type=lambda remark: {"type": o.lesson_router.UNSURE, "confidence": "low",
                                                 "reason": "форс-unsure (тест low-wait)",
                                                 "behavior_hits": 0, "code_hits": 0},
            classify_lesson_llm=lambda remark, draft="", window="": {
                "reading": "бот подтвердил приём данных, хотя клиент прислал только гео",
                "class": cls, "confidence": "low", "plan": "поправить квитанцию + тест", "route": route})

    # --- низкоуровневый транспорт _moderation_reply_send -----------------------------------
    def test_send_extracts_message_id_from_bot_api(self):
        fake_suggest = types.SimpleNamespace(MODERBOT_TOKEN="T", MOD_GROUP_ID=-100500)
        with mock.patch.dict(sys.modules, {"suggest": fake_suggest}), \
             mock.patch.object(o.urllib.request, "urlopen",
                               lambda req, timeout=8: _FakeHTTPResp({"ok": True, "result": {"message_id": 777}})):
            ok, mid, diag = o._moderation_reply_send(368, "🤔 верно?")
        self.assertTrue(ok)
        self.assertEqual(777, mid)                                  # факт доставки = message_id из ответа

    def test_send_api_error_is_honest_false(self):
        fake_suggest = types.SimpleNamespace(MODERBOT_TOKEN="T", MOD_GROUP_ID=-100500)
        with mock.patch.dict(sys.modules, {"suggest": fake_suggest}), \
             mock.patch.object(o.urllib.request, "urlopen",
                               lambda req, timeout=8: _FakeHTTPResp({"ok": False, "description": "chat not found"})):
            ok, mid, diag = o._moderation_reply_send(368, "🤔 верно?")
        self.assertFalse(ok)
        self.assertIsNone(mid)
        self.assertIn("chat not found", diag)                      # честный диагноз, не тихий no-op

    def test_send_no_token_is_honest_false(self):
        fake_suggest = types.SimpleNamespace(MODERBOT_TOKEN="", MOD_GROUP_ID=-100500)
        with mock.patch.dict(sys.modules, {"suggest": fake_suggest}):
            ok, mid, diag = o._moderation_reply_send(368, "verno?")
        self.assertFalse(ok)
        self.assertIn("MODERBOT_TOKEN", diag)

    # --- ретрай + вердикт _reply_moderation_lesson ----------------------------------------
    def test_reask_retries_then_delivers(self):
        calls = {"n": 0}
        def flaky(card, text):
            calls["n"] += 1
            return (False, None, "timeout") if calls["n"] == 1 else (True, 42, "")
        mid = o._reply_moderation_lesson(368, "🤔 верно?", send=flaky, retries=2)
        self.assertEqual(42, mid)                                  # доставлен со 2-й попытки → message_id
        self.assertEqual(2, calls["n"])

    def test_reask_all_attempts_fail_returns_false(self):
        calls = {"n": 0}
        def dead(card, text):
            calls["n"] += 1
            return (False, None, "chat not found")
        self.assertIs(False, o._reply_moderation_lesson(368, "t", send=dead, retries=3))
        self.assertEqual(3, calls["n"])                            # ретраил ровно 3 раза, потом сдался

    # --- сквозь _handle_lesson: доставлено → ожидание; провал → failed --------------------
    def test_low_lesson_reask_delivered_as_reply_enters_wait(self):
        # ГОЛДЕН: low-урок → переспрос ДОСТАВЛЕН реплаем на карточку → урок в ожидании, не закрыт
        sent = {}
        o._deliver_owner_card = lambda text: self.fail("владельца не зовём — переспрос ДОШЁЛ")
        tid = self.fb.add(status="in_progress", task_text=self._task("не пиши «данные получил» на одно гео"))
        with self._force_low(), \
             mock.patch.object(o, "_moderation_reply_send",
                               lambda card, text: (sent.update(card=card, text=text), (True, 900, ""))[1]):
            o._handle_lesson(tid, self.fb.tasks[tid]["task_text"])
        self.assertEqual("in_progress", self.fb.tasks[tid]["status"])   # НЕ закрыт — ждём ответа учителя
        self.assertIn(tid, o._lesson_wait_ids())                         # ожидание сохранено
        self.assertEqual("368", sent["card"])                            # РЕПЛАЙ на карточку черновика (там где учитель писал)
        self.assertIn("верно?", sent["text"])                            # это ПЕРЕСПРОС, отдельное сообщение
        self.assertNotIn("Принял замечание", sent["text"])               # НЕ подменяем ack приёма урока

    def test_low_lesson_reask_api_error_fails_not_silent_wait(self):
        # ГОЛДЕН (#102): переспрос НЕ доставлен (API-ошибка после ретраев) → урок failed с диагнозом +
        # карточка владельцу; НЕ остаётся тихо «в ожидании»
        cards = []
        o._deliver_owner_card = lambda text: (cards.append(text) or (f"инбокс {o.INBOX_TOPIC_ID}", True))
        tid = self.fb.add(status="in_progress", task_text=self._task("что-то не так с депозитом"))
        with self._force_low(), \
             mock.patch.object(o, "_moderation_reply_send", lambda card, text: (False, None, "chat not found")):
            o._handle_lesson(tid, self.fb.tasks[tid]["task_text"])
        t = self.fb.tasks[tid]
        self.assertEqual("failed", t["status"])                         # НЕ висит молча — упал
        self.assertNotIn(tid, o._lesson_wait_ids())                      # в ожидание не ушёл
        self.assertIn("НЕ доставлен", t["result"])                       # диагноз в результате
        self.assertEqual(1, len(cards))                                  # карточка-уточнение ушла владельцу
        self.assertIn("не дошёл до учителя", cards[0])


class TestLessonUrokChannel(Base):
    """#112 шаг 8/8: живой канал «урок:» сквозь _handle_lesson → route_lesson_urok.
      • behavior → правило в playbook + реплай «✅ Принято» + ЗАПИСЬ В ЖУРНАЛ уроков (cowork) + задача done;
      • code     → карточка владельцу «нужен код-фикс» БЕЗ авто-правки/делегирования (планировщик НЕ зван)
                   + журнал + done.
    Права/анти-тайский/живой-реплей покрыты router-level в test_lesson_urok. Playbook/владелец/реплай —
    инъекции (боевую книгу и 1160 не трогаем)."""

    def _task(self, remark, card="90777"):
        return ("[урок:урок от @filipp] родитель 112 — замечание менеджера в копилку обучения\n"
                "окно диалога: @nikita (client_id=606) · черновик #42\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                "Исходный черновик: Локацию и данные получил, спасибо! Оформляю бронь.")

    def setUp(self):
        super().setUp()
        self.cows = []
        o._cowork = lambda line: self.cows.append(line)

    def test_behavior_playbook_prinyato_and_journal(self):
        acks, rules = [], []
        with mock.patch.object(o.lesson_router, "_default_append_style",
                               lambda rule: (rules.append(rule), "added")[1]), \
             mock.patch.object(o.lesson_router, "_default_find_conflict", lambda rule: ""), \
             mock.patch.object(o, "_reply_moderation_lesson",
                               lambda card, text: (acks.append((str(card), text)), 900)[1]):
            tid = self.fb.add(status="in_progress", task_text=self._task('не пиши "данные получил"'))
            o._handle_lesson(tid, self.fb.tasks[tid]["task_text"])
        self.assertEqual("done", self.fb.tasks[tid]["status"])
        self.assertTrue(rules and "данные получил" in rules[0])          # правило ушло в playbook
        self.assertTrue(acks and "Принято" in acks[0][1])                # ответ «✅ Принято» реплаем
        self.assertEqual("90777", acks[0][0])                            # на карточку урока
        self.assertTrue(any("урок #" in c for c in self.cows))           # ЗАПИСЬ В ЖУРНАЛ уроков (cowork)

    def test_code_owner_card_no_delegate_no_code_change_and_journal(self):
        cards = []
        with mock.patch.object(o, "_deliver_owner_card",
                               lambda text: (cards.append(text), (f"инбокс {o.INBOX_TOPIC_ID}", True))[1]), \
             mock.patch.object(o, "_local_dec_plan",
                               lambda *a, **k: self.fail("code-урок канала урок: НЕ делегируем планировщику")):
            tid = self.fb.add(status="in_progress", task_text=self._task("цена берётся из столбца J, а не из K"))
            o._handle_lesson(tid, self.fb.tasks[tid]["task_text"])
        self.assertEqual("done", self.fb.tasks[tid]["status"])
        self.assertTrue(cards and "нужен код-фикс" in cards[0].lower())   # карточка владельцу
        self.assertIn("Готовый текст задачи", cards[0])                   # + готовый текст задачи (копипаст)
        self.assertTrue(any("урок #" in c for c in self.cows))           # журнал уроков


class TestClaudeBudget(unittest.TestCase):
    """Бюджет процессов claude (зеркало VPS oom2 1520c68; инцидент-каскад 22.07: 18×claude.exe).
    Всё замокано: counter/sleeper/notifier инъектируются, реальные процессы/CIM не трогаем."""

    def setUp(self):
        o._claude_budget_denials = 0
        self.addCleanup(setattr, o, "_claude_budget_denials", 0)

    def test_budget_free_allows(self):
        ok, detail = o._claude_budget_gate(counter=lambda: 1, sleeper=lambda s: None)
        self.assertTrue(ok)
        self.assertIn(f"1/{o.MAX_CLAUDE_PROCS}", detail)
        self.assertEqual(o._claude_budget_denials, 0)

    def test_budget_full_waits_then_allows(self):
        # лимит занят, на втором опросе слот освободился → ok (спали, но дождались)
        seq = iter([o.MAX_CLAUDE_PROCS, o.MAX_CLAUDE_PROCS, o.MAX_CLAUDE_PROCS - 1])
        slept = []
        ok, detail = o._claude_budget_gate(counter=lambda: next(seq),
                                           sleeper=slept.append, wait_sec=60)
        self.assertTrue(ok)
        self.assertTrue(slept)                              # ждали, не мгновенно
        self.assertEqual(o._claude_budget_denials, 0)       # успех сбрасывает отказы

    def test_budget_full_denies_after_wait(self):
        ok, detail = o._claude_budget_gate(counter=lambda: o.MAX_CLAUDE_PROCS + 3,
                                           sleeper=lambda s: None, wait_sec=10)
        self.assertFalse(ok)
        self.assertEqual(o._claude_budget_denials, 1)
        self.assertIn("лимит", detail)

    def test_three_denials_send_card_and_reset(self):
        cards = []
        for i in range(3):
            ok, _ = o._claude_budget_gate(counter=lambda: 99, sleeper=lambda s: None,
                                          notifier=cards.append, wait_sec=0)
            self.assertFalse(ok)
        self.assertEqual(len(cards), 1)                     # карточка ровно одна — на 3-м отказе
        self.assertIn("каскад", cards[0])
        self.assertEqual(o._claude_budget_denials, 0)       # после карточки счётчик заново

    def test_fail_open_when_count_unavailable(self):
        # CIM/powershell не смог (None) → fail-open: бюджет не смеет остановить демон
        ok, detail = o._claude_budget_gate(counter=lambda: None, sleeper=lambda s: None)
        self.assertTrue(ok)
        self.assertIn("?", detail)

    def test_count_fails_open_on_junk(self):
        class _P:
            def __init__(self, rc, out): self.returncode, self.stdout = rc, out
        self.assertIsNone(o._count_claude_procs(runner=lambda *a, **k: _P(0, "мусор")))
        self.assertIsNone(o._count_claude_procs(runner=lambda *a, **k: _P(0, "3\n")))   # старый формат «просто число» больше не наш
        self.assertIsNone(o._count_claude_procs(runner=lambda *a, **k: _P(1, '{"claude":[],"tree":[]}')))
        self.assertIsNone(o._count_claude_procs(runner=lambda *a, **k: _P(0, '{"claude":[]}')))  # нет ключа tree
        def boom(*a, **k):
            raise RuntimeError("CIM boom")
        self.assertIsNone(o._count_claude_procs(runner=boom))

    def test_run_task_budget_denied_no_spawn(self):
        # бюджет исчерпан → run_task возвращает failed И headless claude НЕ спавнится
        spawned = []
        def spy_claude(prompt, timeout, cwd, env):
            spawned.append(prompt)
            return (0, "RESULT: не должно случиться", "")
        saved = (o.run_claude, o._claude_budget_gate)
        o.run_claude = spy_claude
        o._claude_budget_gate = lambda *a, **k: (False, "лимит 2 занят (тест)")
        try:
            with mock.patch.object(o, "resolve_claude", lambda: sys.executable):
                status, result = o.run_task(1, "тз: что-нибудь")
        finally:
            o.run_claude, o._claude_budget_gate = saved
        self.assertEqual(status, "failed")
        self.assertIn("бюджет claude", result)
        self.assertEqual(spawned, [])                       # спавна НЕ было

    def test_thinker_budget_denied_failsafe_none(self):
        # думатель при занятом бюджете → None (fail-safe), без ожидания и без спавна
        ran = []
        def spy_run(*a, **k):
            ran.append(a)
            raise AssertionError("думатель не должен спавнить при занятом бюджете")
        saved = o._claude_budget_gate
        o._claude_budget_gate = lambda *a, **k: (False, "лимит (тест)")
        try:
            with mock.patch.object(o, "resolve_claude", lambda: sys.executable), \
                    mock.patch.object(o.subprocess, "run", spy_run):
                self.assertIsNone(o._thinker_exec("p", 5, "t"))
        finally:
            o._claude_budget_gate = saved
        self.assertEqual(ran, [])


class TestClaudeBudgetByParent(unittest.TestCase):
    """ГОЛДЕН варианта A (счёт ПО РОДИТЕЛЮ) на ЖИВОМ снимке процессов ПК.

    fixtures/claude_procs.live.json снят пробой 22.07 в момент, когда на машине одновременно жили:
      • 7 CLI-claude агент-сессий Claude Desktop (PPID 18628 = Electron-приложение);
      • ИНТЕРАКТИВНАЯ RC-сессия владельца pid 8680 (PPID 21396 = rc_supervisor.py);
      • HEADLESS-ребёнок питон-родителя pid 15724 (PPID 17744) — ровно то, что плодит демон.
    Формат — дословный вывод живого powershell-снимка (правило-класс «мок = живой формат»):
    ключи ProcessId/ParentProcessId/Created, ветки claude/tree."""

    FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "claude_procs.live.json")
    DAEMON_LIKE_PID = 17744     # питон-родитель headless-ребёнка 15724 (роль «демон»)
    RC_SUPERVISOR_PID = 21396   # родитель ИНТЕРАКТИВНОЙ RC-сессии 8680
    ELECTRON_PID = 18628        # Claude Desktop: 7 чужих агент-сессий

    class _P:
        def __init__(self, rc, out): self.returncode, self.stdout = rc, out

    @classmethod
    def setUpClass(cls):
        with open(cls.FIX, encoding="utf-8") as f:
            cls.live = f.read()

    def _count(self, own_pid, blob=None):
        return o._count_claude_procs(runner=lambda *a, **k: self._P(0, blob or self.live),
                                     own_pid=own_pid)

    def test_live_fixture_shape(self):
        data = json.loads(self.live)
        self.assertEqual(len(data["claude"]), 9)          # 7 Desktop + 1 RC + 1 headless
        self.assertGreater(len(data["tree"]), 100)
        self.assertEqual(set(data["claude"][0]), {"ProcessId", "ParentProcessId", "Created"})

    def test_counts_only_own_headless_children(self):
        # демон видит РОВНО одного своего headless-ребёнка из девяти живых CLI-claude
        self.assertEqual(self._count(self.DAEMON_LIKE_PID), 1)

    def test_interactive_rc_session_not_counted(self):
        # ГЛАВНОЕ владельца: интерактивная RC-сессия в бюджет демона НЕ входит…
        self.assertEqual(self._count(self.DAEMON_LIKE_PID), 1)
        # …и она же прекрасно считается, если корнем взять её собственного родителя-супервизора
        self.assertEqual(self._count(self.RC_SUPERVISOR_PID), 1)

    def test_desktop_agent_sessions_not_counted(self):
        # 7 агент-сессий Claude Desktop (каскад 22.07) — тоже мимо бюджета демона…
        self.assertEqual(self._count(self.DAEMON_LIKE_PID), 1)
        # …а по корню-Electron их видно ВОСЕМЬ, не семь: пробник, породивший headless-ребёнка
        # 15724, сам был запущен ИЗ сессии Claude Desktop (15724→17744→14912→8660→18628). Число
        # честное по живому снимку — подгонять фикстуру под «красивую» семёрку нельзя
        # (правило-класс: честный итог живого пина важнее ожидаемого).
        self.assertEqual(self._count(self.ELECTRON_PID), 8)

    def test_rc_session_lives_outside_desktop_tree(self):
        # RC-сессия поднята wscript'ом/Планировщиком, а не из приложения: её цепь предков
        # (8680→21396→18764→5772) до Electron НЕ доходит — потому она и не «чужой хвост» демона.
        data = json.loads(self.live)
        parents = {int(e["ProcessId"]): int(e["ParentProcessId"]) for e in data["tree"]}
        created = {int(e["ProcessId"]): str(e.get("Created") or "") for e in data["tree"]}
        self.assertFalse(o._is_descendant(8680, self.ELECTRON_PID, parents, created))
        self.assertTrue(o._is_descendant(8680, self.RC_SUPERVISOR_PID, parents, created))

    def test_unrelated_root_counts_zero(self):
        self.assertEqual(self._count(999999), 0)          # чужой PID — ни одного нашего

    def test_grandchildren_counted(self):
        # субагент headless-ребёнка (внук демона) — тоже НАШ расход, считаем
        data = json.loads(self.live)
        data["claude"].append({"ProcessId": 40001, "ParentProcessId": 15724, "Created": "20260722190000"})
        data["tree"].append({"ProcessId": 40001, "ParentProcessId": 15724, "Created": "20260722190000"})
        self.assertEqual(self._count(self.DAEMON_LIKE_PID, json.dumps(data)), 2)

    def test_pid_reuse_breaks_chain(self):
        # Живой факт ЭТОГО ПК: у ботов PPID=5980, а процесса 5980 нет — PID переиспользуем.
        # Если «родитель» СОЗДАН ПОЗЖЕ ребёнка, это не родитель, а тёзка → цепь рвём.
        data = json.loads(self.live)
        tree = {int(e["ProcessId"]): e for e in data["tree"]}
        tree[self.DAEMON_LIKE_PID]["Created"] = "20260722235959"   # «демон» моложе своего ребёнка
        data["tree"] = list(tree.values())
        self.assertEqual(self._count(self.DAEMON_LIKE_PID, json.dumps(data)), 0)

    def test_single_element_json_not_collapsed(self):
        # ConvertTo-Json схлопывает список из ОДНОГО элемента в объект — живой формат, не теория
        one = {"claude": {"ProcessId": 15724, "ParentProcessId": 17744, "Created": "20260722185500"},
               "tree": {"ProcessId": 15724, "ParentProcessId": 17744, "Created": "20260722185500"}}
        self.assertEqual(self._count(self.DAEMON_LIKE_PID, json.dumps(one)), 1)

    def test_gate_respects_limit_for_headless(self):
        # лимит по-прежнему СОБЛЮДАЕТСЯ для headless: счёт ≥ лимита → отказ
        o._claude_budget_denials = 0
        self.addCleanup(setattr, o, "_claude_budget_denials", 0)
        ok, detail = o._claude_budget_gate(counter=lambda: o.MAX_CLAUDE_PROCS,
                                           sleeper=lambda s: None, wait_sec=0)
        self.assertFalse(ok)
        self.assertIn("headless", detail)

    def test_gate_open_when_only_interactive_alive(self):
        # 9 живых CLI-claude на машине, но ни один не наш → счёт 0 → гейт ОТКРЫТ (раньше замок)
        cnt = self._count(self.DAEMON_LIKE_PID + 12345)
        self.assertEqual(cnt, 0)
        ok, detail = o._claude_budget_gate(counter=lambda: cnt, sleeper=lambda s: None)
        self.assertTrue(ok)
        self.assertIn(f"0/{o.MAX_CLAUDE_PROCS}", detail)


class TestGateSpawnsNoClaude(unittest.TestCase):
    """ГОЛДЕН класса-каскада (22.07, зеркало VPS fixture-guard 6e7e726): прогон ГЕЙТОВ демона
    не порождает НИ ОДНОГО процесса claude. Перехватываем subprocess.run целиком и проверяем
    argv каждого спавна: гейт затронутых тестов зовёт ТОЛЬКО venv-python -m unittest, гейт
    self-update — только python -m py_compile / -c import; строки 'claude' нет нигде.
    Заодно фиксируем NO_WINDOW: служебный спавн идёт скрыто (мигающие чёрные окна 22.07)."""

    class _P:
        returncode = 0
        stdout = ""
        stderr = ""

    def test_test_gate_spawns_only_unittest_no_claude(self):
        seen = []
        def spy(cmd, **kw):
            seen.append((list(cmd), kw))
            return self._P()
        with mock.patch.object(o.subprocess, "run", spy):
            ok, _ = o._gate_test_modules(["test_delivery"])
        self.assertTrue(ok)
        self.assertEqual(len(seen), 1)                       # ровно один спавн
        cmd, kw = seen[0]
        self.assertEqual(cmd[:3], [o.VENV_PY, "-m", "unittest"])   # и это unittest, не claude
        for c in cmd:
            self.assertNotIn("claude", str(c).lower())
        self.assertEqual(kw.get("creationflags"), o.NO_WINDOW)     # скрытый запуск (окна не мигают)

    def test_selfupdate_unittest_gate_no_claude(self):
        seen = []
        def spy(cmd, **kw):
            seen.append((list(cmd), kw))
            return self._P()
        with mock.patch.object(o.subprocess, "run", spy):
            ok, _ = o._gate_unittests()
        self.assertTrue(ok)
        for cmd, kw in seen:
            for c in cmd:
                self.assertNotIn("claude", str(c).lower())
            self.assertEqual(kw.get("creationflags"), o.NO_WINDOW)

    def test_selfupdate_code_gate_spawns_only_python(self):
        # selfupdate_gate.code_gate (py_compile + import-smoke) НА РЕАЛЬНЫХ подпроцессах:
        # это python-спавны, claude в argv нет. Живой прогон — гейт остаётся рабочим, не только мок.
        ok, msg = selfupdate_gate.code_gate(
            sys.executable, o.REPO, ["gate_selective.py"], "gate_selective", timeout=60)
        self.assertTrue(ok, msg)

    def test_empty_mods_no_spawn_at_all(self):
        def boom(*a, **k):
            raise AssertionError("без затронутых тестов гейт не должен спавнить ничего")
        with mock.patch.object(o.subprocess, "run", boom):
            ok, msg = o._gate_test_modules([])
        self.assertTrue(ok)


class TestLessonCommitRetry(Base):
    """Пакет «полнота лога» п.6: провал _commit_lesson НЕ оставляет docs/revizor_checklist.md
    грязным. Раньше сорванный git commit бросал файл модифицированным навсегда → git_ff_pull_tick
    видел tracked-грязь и пропускал pull ВЕЧНО (+NOTE-спам). Теперь: добавленные строки — в спул,
    пути — откат к HEAD, владельцу — карточка, докоммит — следующим циклом демона."""

    REL = os.path.join("docs", "revizor_checklist.md")
    BASE = "# чек-лист ревизора\n- старый класс: цена без брони"
    NEW_LINE = "- новый класс: мок обязан копировать живой формат"

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "docs"), exist_ok=True)
        self._old_repo = o.REPO
        o.REPO = self.tmp                                  # файлы урока живут в песочнице
        self.addCleanup(lambda: setattr(o, "REPO", self._old_repo))
        self.retry = os.path.join(self.tmp, "lesson_retry.json")
        self.cards = []                                    # карточки владельцу (вместо _notify_critical)
        self._write(self.BASE + "\n" + self.NEW_LINE + "\n")   # урок дописал строку → файл грязный

    def _write(self, text):
        with open(os.path.join(self.tmp, self.REL), "w", encoding="utf-8") as f:
            f.write(text)

    def _read(self):
        with open(os.path.join(self.tmp, self.REL), encoding="utf-8") as f:
            return f.read()

    def _git(self, commit_rc=1, commit_err="fatal: Unable to create index.lock: File exists",
             trace=None):
        """Мини-git по живому контракту _git_call: (rc, stdout, stderr); checkout РЕАЛЬНО
        возвращает файл к HEAD (иначе тест «дерево чистое» ничего бы не доказывал)."""
        tr = trace if trace is not None else []

        def call(args, timeout=90):
            tr.append(tuple(args[:2]))
            if args[0] == "add":
                return (0, "", "")
            if args[0] == "diff":
                return (0, self.REL, "")
            if args[0] == "commit":
                return (commit_rc, "", commit_err if commit_rc else "")
            if args[0] == "show":
                return (0, self.BASE, "")
            if args[0] == "checkout":
                self._write(self.BASE + "\n")
                return (0, "", "")
            if args[0] == "rev-parse":
                return (0, "abc1234", "")
            return (0, "", "")
        return call, tr

    def test_commit_failure_rolls_back_spools_and_cards(self):
        """ЮНИТ НА ПРОВАЛ (требование п.6): сорванный коммит → откат к HEAD (дерево чистое,
        авто-фетч жив) + добавленные строки в спуле + ровно одна карточка владельцу."""
        call, tr = self._git()
        res = o._commit_lesson(7, "НАДЗОР", "мок = живой формат", [self.REL], call_fn=call,
                               ack_where="чек-лист ревизора", card_msg_id="555",
                               retry_path=self.retry, notify=self.cards.append)
        self.assertIsNone(res)                             # коммита не было — ack не уйдёт (гейт шага 4)
        self.assertIn(("checkout", "HEAD"), tr)            # откат к HEAD выполнен
        self.assertEqual(self._read(), self.BASE + "\n")   # дерево ЧИСТОЕ — вечного блокера нет
        with open(self.retry, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["added"][self.REL], [self.NEW_LINE])   # строка урока не потеряна
        self.assertEqual(data["ack_where"], "чек-лист ревизора")     # материал для запоздалого ack
        self.assertEqual(data["card_msg_id"], "555")
        self.assertEqual(len(self.cards), 1)               # карточка владельцу — одна, не спам
        self.assertIn("не прошёл", self.cards[0])
        self.assertIn("Авто-фетч не заблокирован", self.cards[0])
        # грязи после отката нет → git_ff_pull_tick больше не скажет «грязно — пропуск»

    def test_commit_success_path_untouched(self):
        call, tr = self._git(commit_rc=0)
        res = o._commit_lesson(7, "НАДЗОР", "тема", [self.REL], call_fn=call,
                               retry_path=self.retry, notify=self.cards.append)
        self.assertEqual(res, "abc1234")                   # штатный путь как был
        self.assertFalse(os.path.exists(self.retry))       # спул не заводился
        self.assertEqual(self.cards, [])
        self.assertNotIn(("checkout", "HEAD"), tr)         # и откатов не было

    def test_no_real_diff_means_no_spool_no_card(self):
        def call(args, timeout=90):
            if args[0] == "add":
                return (0, "", "")
            if args[0] == "diff":
                return (0, "", "")                         # индекс пуст (дедуп) — коммитить нечего
            raise AssertionError(f"лишний git-вызов: {args}")
        res = o._commit_lesson(7, "СТИЛЬ", "т", [self.REL], call_fn=call,
                               retry_path=self.retry, notify=self.cards.append)
        self.assertIsNone(res)
        self.assertFalse(os.path.exists(self.retry))
        self.assertEqual(self.cards, [])

    def test_retry_commits_next_cycle_and_acks_late(self):
        """Следующий цикл: строки из спула накладываются на актуальный файл, коммит проходит,
        спул снят, запоздалое подтверждение учителю уходит ТОЛЬКО теперь (после коммита)."""
        call, _ = self._git()                              # цикл 1: провал → спул + откат
        o._commit_lesson(7, "НАДЗОР", "мок = живой формат", [self.REL], call_fn=call,
                         ack_where="чек-лист ревизора", card_msg_id="42",
                         retry_path=self.retry, notify=self.cards.append)
        acks = []
        save_ack = o._deliver_lesson_ack
        save_verify = o.lesson_router.verify_commit
        o._deliver_lesson_ack = lambda mid, text: acks.append((mid, text))
        o.lesson_router.verify_commit = lambda ref: True   # хеш фейкового git «существует»
        self.addCleanup(lambda: setattr(o, "_deliver_lesson_ack", save_ack))
        self.addCleanup(lambda: setattr(o.lesson_router, "verify_commit", save_verify))
        call2, tr2 = self._git(commit_rc=0)                # цикл 2: git ожил
        note = o.lesson_commit_retry_tick(call_fn=call2, path=self.retry)
        self.assertIn("повторный коммит abc1234", note)
        self.assertIn(self.NEW_LINE, self._read())         # строка урока вернулась в файл
        self.assertIn(("commit", "-m"), tr2)
        self.assertFalse(os.path.exists(self.retry))       # спул снят
        self.assertEqual(acks[0][0], "42")                 # подтверждение — на ту же карточку
        self.assertIn("Урок принят", acks[0][1])

    def test_retry_when_lines_already_in_head_clears_spool(self):
        """pull/руки уже принесли строки в HEAD → повторять нечего: спул снят БЕЗ коммита."""
        o._lesson_retry_save({"tid": 7, "route": "НАДЗОР", "msg": "m", "paths": [self.REL],
                              "added": {self.REL: [self.NEW_LINE]}, "attempts": 0}, self.retry)
        self._write(self.BASE + "\n" + self.NEW_LINE + "\n")   # файл уже содержит строку

        def call(args, timeout=90):
            if args[0] == "add":
                return (0, "", "")
            if args[0] == "diff":
                return (0, "", "")                         # против HEAD пусто
            raise AssertionError(f"коммит не должен вызываться: {args}")
        note = o.lesson_commit_retry_tick(call_fn=call, path=self.retry)
        self.assertIn("уже в HEAD", note)
        self.assertFalse(os.path.exists(self.retry))

    def test_retry_failure_rolls_back_again_and_counts(self):
        """git всё ещё лежит: снова откат (дерево чистое), спул цел, счётчик попыток растёт."""
        call, _ = self._git()
        o._commit_lesson(7, "Н", "т", [self.REL], call_fn=call, retry_path=self.retry,
                         notify=self.cards.append)
        call2, tr2 = self._git(commit_rc=1)
        self.assertIn("попытка 1", o.lesson_commit_retry_tick(call_fn=call2, path=self.retry))
        self.assertIn(("checkout", "HEAD"), tr2)           # снова откат — авто-фетч жив
        self.assertEqual(self._read(), self.BASE + "\n")
        with open(self.retry, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["attempts"], 1)
        self.assertIn("попытка 2", o.lesson_commit_retry_tick(call_fn=call2, path=self.retry))
        self.assertEqual(len(self.cards), 1)               # карточка ушла ОДИН раз, ретраи не спамят

    def test_maybe_retry_throttles_and_needs_spool(self):
        save = (o.LESSON_RETRY_FILE, o._lesson_retry_last)
        o.LESSON_RETRY_FILE, o._lesson_retry_last = self.retry, 0.0
        save_tick = o.lesson_commit_retry_tick
        called = {"n": 0}
        o.lesson_commit_retry_tick = lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "тик"
        try:
            self.assertIsNone(o.maybe_lesson_commit_retry(now=1000.0))   # спула нет → даже не тикаем
            with open(self.retry, "w", encoding="utf-8") as f:
                json.dump({"added": {self.REL: ["x"]}}, f)
            self.assertEqual(o.maybe_lesson_commit_retry(now=2000.0), "тик")
            self.assertIsNone(o.maybe_lesson_commit_retry(now=2000.0 + o.LESSON_RETRY_SEC - 1))
            self.assertEqual(o.maybe_lesson_commit_retry(now=2000.0 + o.LESSON_RETRY_SEC + 1), "тик")
            self.assertEqual(called["n"], 2)
        finally:
            o.lesson_commit_retry_tick = save_tick
            o.LESSON_RETRY_FILE, o._lesson_retry_last = save

    def test_main_loop_wired(self):
        """Проводка: повтор коммита урока реально стоит в главном цикле демона (до авто-фетча —
        успешный докоммит делает дерево чистым к моменту pull-проверки)."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "pc_orchestrator.py"), encoding="utf-8") as f:
            src = f.read()
        body = src.split("def _main_loop", 1)[1]
        self.assertIn("maybe_lesson_commit_retry()", body)
        self.assertLess(body.index("maybe_lesson_commit_retry()"), body.index("maybe_git_ff_pull()"))


class TestMetricsLine(unittest.TestCase):
    """Строка METRICS: дословный формат (голден), norm_effort, extract_tokens, selfheal_count,
    и что обёртка run_task РЕАЛЬНО пишет строку METRICS в лог на завершении задачи."""

    def test_format_exact(self):
        line = o.task_metrics.metrics_line(
            task=42, lane="pc", model="claude-opus-5", effort="xhigh",
            start_iso="2026-07-24T14:00:00+00:00", end_iso="2026-07-24T14:00:37+00:00",
            dur_s=37.4, outcome="done", attempts=2, selfheals=1,
            tokens_in=None, tokens_out=None,
            task_text="ultrathink Приземлить проверенный гард из origin/main",
            mode="prod", src="pc_orchestrator.py")
        self.assertEqual(
            line,
            "METRICS task=42 lane=pc type=code mode=prod src=pc_orchestrator.py "
            "model=claude-opus-5 effort=xhigh "
            "start=2026-07-24T14:00:00+00:00 end=2026-07-24T14:00:37+00:00 dur_s=37.40 "
            "outcome=done attempts=2 selfheals=1 tokens_in=na tokens_out=na")

    def test_old_call_without_new_fields_still_works(self):
        """Обратная совместимость: вызов без task_text/mode/src не падает и честно даёт na."""
        line = o.task_metrics.metrics_line(
            task=7, lane="pc", model="m", effort="xhigh", start_iso="s", end_iso="e",
            dur_s=1, outcome="done", attempts=1, selfheals=0)
        self.assertIn(" type=other mode=na src=na ", line)

    def test_under_test_detects_direct_script_run(self):
        """ДЫРА, закрытая 25.07.2026 (сначала на VPS, теперь здесь): КОПИЯ теста под чужим именем
        и прямой запуск файла test_*.py писали строки в БОЕВОЙ журнал как живые задачи."""
        M = o.task_metrics
        self.assertTrue(M.under_test("D:/x/test_pc_orchestrator.py", {}, ()))
        self.assertTrue(M.under_test("/tmp/old_om.py", {"ORCH_TEST_MODE": "1"}, ()))
        self.assertTrue(M.under_test("x.py", {"PYTEST_CURRENT_TEST": "t"}, ()))
        self.assertTrue(M.under_test("x.py", {}, ("pytest",)))
        self.assertTrue(M.under_test("x.py", {"ORCH_DAEMON_TEST": "1"}, ()))
        self.assertFalse(M.under_test("D:/turbobaby-bot/pc_orchestrator.py", {}, ()))
        self.assertFalse(M.under_test("pc_orchestrator.py", {"ORCH_TEST_MODE": "0"}, ()))

    def test_task_type_on_live_owner_phrases(self):
        """ГОЛДЕН типа задачи на ДОСЛОВНЫХ формулировках владельца из журнала (правило репо:
        детект проверяется реальными фразами, а не идеализированными)."""
        M = o.task_metrics
        cases = [
            ("ultrathink ТОЛЬКО read-only. Ответ ТЕКСТОМ в 328 И краткий итог 3-5 строк в result.", "read"),
            ("ultrathink Read-only живая проверка гарда + обновление пульса. Код не менять, не коммитить.", "read"),
            ("[замер Opus 5 — read-only] Ответь ОДНОЙ строкой: сколько файлов *.py лежит в каталоге tests", "read"),
            ("ultrathink Замер влияния ключевого слова на уровень усилий.", "read"),
            ("ultrathink Приземлить проверенный фикс из GitHub через merge.", "code"),
            ("[шаг 2/6 родитель 185] поправить карточку приёма", "code"),
            ("ultrathink Класс-фикс гарда: позиционные аргументы скрипта не делают команду красной.", "build"),
            ("ultrathink Выкатить одометр в прод.", "build"),
            ("ultrathink Очистить и достроить метрики.", "build"),
            ("ultrathink Перезапустить демон оркестратора, чтобы вступили новые пороги гейтов (коммит 0bbca3d).", "other"),
        ]
        for text, want in cases:
            got, marker = M.task_type_explain(text)
            self.assertEqual(got, want, "%s ← %s (признак: %s)" % (got, text[:60], marker))
        self.assertEqual(M.task_type(""), "other")
        self.assertEqual(M.task_type(None), "other")

    def test_duration_keeps_fraction(self):
        """Короткие задачи больше не схлопываются в dur_s=0: формат резал int(round(...))."""
        M = o.task_metrics
        mk = lambda d: M.metrics_line(task=1, lane="pc", model="m", effort="xhigh", start_iso="s",
                                      end_iso="e", dur_s=d, outcome="done", attempts=1, selfheals=0)
        self.assertIn(" dur_s=0.83 ", mk(0.834))
        self.assertIn(" dur_s=0.01 ", mk(0.009))
        self.assertIn(" dur_s=176.00 ", mk(176))

    def test_run_task_marks_mode_and_source(self):
        """СКВОЗНО: обёртка run_task кладёт в строку METRICS режим прогона, имя входного файла и
        текст задания (из него считается type). Исполнитель замокан — ни claude, ни сети."""
        cap = {}
        real_ml = o.task_metrics.metrics_line

        def spy(**kw):
            cap.update(kw)
            return real_ml(**kw)

        with mock.patch.object(o, "_run_task_impl", return_value=("done", "ок")), \
             mock.patch.object(o.task_metrics, "metrics_line", side_effect=spy):
            o.run_task(4242, "ultrathink ТОЛЬКО read-only. Ничего не менять, не коммитить.")
        self.assertEqual(cap.get("mode"), "test")
        self.assertTrue(str(cap.get("src") or "").startswith(("test_", "unittest", "python")),
                        "src=%r" % cap.get("src"))
        line = real_ml(**cap)
        self.assertIn(" lane=pc ", line)
        self.assertIn(" type=read ", line)
        self.assertIn(" mode=test ", line)

    def test_effort_norm_default_xhigh(self):
        self.assertEqual(o.task_metrics.norm_effort("XHIGH"), "xhigh")
        self.assertEqual(o.task_metrics.norm_effort("bogus"), "xhigh")
        self.assertEqual(o.task_metrics.norm_effort(""), "xhigh")
        self.assertEqual(o.task_metrics.norm_effort(None), "xhigh")
        self.assertEqual(o.task_metrics.norm_effort("max"), "max")

    def test_extract_tokens(self):
        self.assertEqual(
            o.task_metrics.extract_tokens({"usage": {"input_tokens": 12, "output_tokens": 3}}), (12, 3))
        self.assertEqual(
            o.task_metrics.extract_tokens({"modelUsage": {"claude-opus-5": {"inputTokens": 5, "outputTokens": 7}}}),
            (5, 7))
        self.assertEqual(o.task_metrics.extract_tokens("not-json"), (None, None))
        self.assertEqual(o.task_metrics.extract_tokens({}), (None, None))

    def test_extract_tokens_full_input_with_cache(self):
        # ПОЛНЫЙ вход = input + cacheRead + cacheCreation (иначе с кэшем промпта соврём владельцу).
        # Живой формат (замер 23.07): sonnet input=2, cacheRead=29339, cacheCreation=16329 → 45670.
        self.assertEqual(
            o.task_metrics.extract_tokens({"modelUsage": {"claude-sonnet-4-6": {
                "inputTokens": 2, "cacheReadInputTokens": 29339,
                "cacheCreationInputTokens": 16329, "outputTokens": 8}}}),
            (45670, 8))
        self.assertEqual(
            o.task_metrics.extract_tokens({"usage": {
                "input_tokens": 2, "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 50, "output_tokens": 9}}),
            (152, 9))

    def test_selfheal_count(self):
        self.assertEqual(o.task_metrics.selfheal_count("обычная задача"), 0)
        self.assertEqual(o.task_metrics.selfheal_count("[самопочинка задачи 3, попытка 1] чинись"), 1)
        self.assertEqual(o.task_metrics.selfheal_count("[самопочинка шага 2, попытка 2]"), 2)

    def test_run_task_emits_metrics(self):
        seen = []

        def fake_impl(tid, text, note="", _mctx=None, approved=()):
            if _mctx is not None:
                _mctx["attempts"] = 2
            return "done", "RESULT: ок"

        with mock.patch.object(o, "_run_task_impl", fake_impl), \
                mock.patch.object(o.log, "info", lambda *a, **k: seen.append(a[0] if a else "")):
            st, r = o.run_task(77, "простая задача")
        self.assertEqual(st, "done")
        metrics = [s for s in seen if isinstance(s, str) and s.startswith("METRICS ")]
        self.assertTrue(metrics, "run_task обязан писать строку METRICS в лог")
        self.assertIn("task=77 lane=pc", metrics[-1])
        self.assertIn("model=claude-opus-5", metrics[-1])   # METRICS показывает модель ИЗ КОМАНДЫ (EXECUTOR_MODEL), не settings.json
        self.assertIn("effort=xhigh", metrics[-1])
        self.assertIn("outcome=done attempts=2", metrics[-1])
        self.assertIn("tokens_in=na tokens_out=na", metrics[-1])

    def test_metrics_attempts_zero_when_no_spawn(self):
        # Ранний выход _run_task_impl ДО цикла (claude не найден) → ни одной headless-попытки:
        # METRICS обязан честно писать attempts=0, а не 1 (иначе over-count спавнов в агрегате).
        seen = []
        with mock.patch.object(o, "resolve_claude", lambda *a, **k: None), \
                mock.patch.object(o.log, "info", lambda *a, **k: seen.append(a[0] if a else "")):
            st, r = o.run_task(78, "любая задача")
        self.assertEqual(st, "failed")
        metrics = [s for s in seen if isinstance(s, str) and s.startswith("METRICS ")]
        self.assertTrue(metrics, "METRICS должна писаться и на раннем провале")
        self.assertIn("outcome=failed attempts=0", metrics[-1])


class TestRevizorIpcChecks(unittest.TestCase):
    """Подключение двух детерминированных чеков черновиков (модуль reviewer) к действующему
    ревизору. Третий чек модуля — _jcheck — НЕ подключён намеренно: он дублирует работающий
    #92 «строка J дословно», и тест ниже сторожит, что его даже не вызывают."""

    DEP_TEXT = "Депозит 3000 ฿ и паспорт — нужны оба сразу"
    YEAR_TEXT = "YAMAHA XMAX 300, 2021 г.в., пробег небольшой"
    CID = 111222333

    def _pkg(self, *texts, cid=None):
        return {"client_id": self.CID if cid is None else cid, "sent": list(texts), "drafts": []}

    def test_depcheck_fires(self):
        out = o._revizor_ipc_findings(self._pkg(self.DEP_TEXT))
        self.assertEqual([f["check"] for f in out], ["depcheck"], out)
        self.assertEqual(out[0]["class"], "#93")
        self.assertEqual(out[0]["action"], "owner")
        self.assertEqual(out[0]["client_id"], self.CID)
        self.assertIn("depcheck", out[0]["evidence"])

    def test_yearcheck_fires(self):
        out = o._revizor_ipc_findings(self._pkg(self.YEAR_TEXT))
        self.assertEqual([f["check"] for f in out], ["yearcheck"], out)
        self.assertEqual(out[0]["class"], "#93")

    def test_both_checks_in_one_window(self):
        out = o._revizor_ipc_findings(self._pkg(self.DEP_TEXT, self.YEAR_TEXT))
        self.assertEqual(sorted(f["check"] for f in out), ["depcheck", "yearcheck"], out)

    def test_jcheck_duplicate_is_not_called(self):
        """ДУБЛЬ НЕ ВЫЗЫВАЕТСЯ: если бы подключили review_record целиком, _jcheck отработал бы —
        и одна и та же находка пошла бы владельцу дважды (своя + #92)."""
        import reviewer as rv
        with mock.patch.object(rv, "_jcheck", side_effect=AssertionError("дубль вызван")) as m:
            out = o._revizor_ipc_findings(self._pkg(self.DEP_TEXT, self.YEAR_TEXT))
        self.assertFalse(m.called, "_jcheck не должен вызываться")
        self.assertEqual(len(out), 2)
        self.assertNotIn("jcheck", [f["check"] for f in out])
        self.assertEqual(o._REVIZOR_IPC_CHECKS, ("depcheck", "yearcheck"))

    def test_dedup_one_finding_per_check_in_window(self):
        out = o._revizor_ipc_findings(self._pkg(self.DEP_TEXT, self.DEP_TEXT + " ещё раз"))
        self.assertEqual(len(out), 1, out)

    def test_staff_window_skipped(self):
        import reviewer as rv
        staff = sorted(rv.STAFF_IDS)[0]
        self.assertEqual(o._revizor_ipc_findings(self._pkg(self.DEP_TEXT, cid=staff)), [])

    def test_clean_window_and_empty_package(self):
        self.assertEqual(o._revizor_ipc_findings(self._pkg("Здравствуйте! Чем помочь?")), [])
        self.assertEqual(o._revizor_ipc_findings({}), [])
        self.assertEqual(o._revizor_ipc_findings(None), [])

    def test_failing_check_does_not_kill_window(self):
        """Упавший чек пропускаем поштучно — второй обязан отработать."""
        def boom(_t):
            raise RuntimeError("чек сломан")
        out = o._revizor_ipc_findings(
            self._pkg(self.YEAR_TEXT),
            checks_fn=(("depcheck", boom), ("yearcheck", __import__("reviewer")._yearcheck)))
        self.assertEqual([f["check"] for f in out], ["yearcheck"], out)

    def test_routing_same_path_as_other_findings(self):
        """Маршрутизация ТА ЖЕ: находка попадает в owner-карточку через _revizor_route, вместе с
        остальными, с тем же снимком очереди (бюджет/дедуп) — своего канала у неё нет."""
        posted = {}

        def fake_post(owner_findings, items):
            posted["findings"] = list(owner_findings)
            return True

        with mock.patch.object(o, "_revizor_postrelease_findings", return_value=[]), \
             mock.patch.object(o, "_revizor_consult", return_value=[]), \
             mock.patch.object(o, "_loc_fetch_items", return_value=[]), \
             mock.patch.object(o, "_revizor_post_owner_card", side_effect=fake_post), \
             mock.patch.object(o, "_cowork"):
            res = o._revizor_route([self._pkg(self.DEP_TEXT)], now=1000.0)
        self.assertEqual(res["owner"], 1, res)
        self.assertEqual(res["tasks"], 0)
        self.assertEqual([f["check"] for f in posted.get("findings", [])], ["depcheck"])
        self.assertEqual(posted["findings"][0]["class"], "#93")

    def test_owner_card_line_renders_class(self):
        out = o._revizor_ipc_findings(self._pkg(self.DEP_TEXT))
        text = o._revizor_owner_card_text(out)
        self.assertIn("[класс #93]", text)
        self.assertIn(str(self.CID), text)


class TestSessionWatchWiring(unittest.TestCase):
    """Детектор НЕМОТЫ сессий висит на тике демона (инцидент 29.07, PID 21216): демон —
    «единственный надёжно выживающий процесс» и подхватывает новый код self-update'ом."""

    def setUp(self):
        o._session_watch_last_run = 0.0

    def tearDown(self):
        o._session_watch_last_run = 0.0

    def test_throttled_to_its_own_interval(self):
        calls = []
        tick = lambda now=None: calls.append(now) or []
        self.assertIsNotNone(o.maybe_session_watch(now=1000, ticker=tick))
        self.assertIsNone(o.maybe_session_watch(now=1000 + o.SESSION_WATCH_SEC - 1, ticker=tick))
        self.assertIsNotNone(o.maybe_session_watch(now=1000 + o.SESSION_WATCH_SEC + 1, ticker=tick))
        self.assertEqual(len(calls), 2)

    def test_findings_are_logged_loudly(self):
        found = [{"pid": 21216, "session_id": "bc8c785c-4ecf-4290-8986-6104acbe5cf5", "age": 10380}]
        got = o.maybe_session_watch(now=1000, ticker=lambda now=None: found)
        self.assertEqual(got, found)

    def test_detector_crash_does_not_break_the_daemon_tick(self):
        """НЕ НАВРЕДИ: сорвавшийся надзор глотается — демон продолжает поллинг."""
        def boom(now=None):
            raise RuntimeError("сорвался")
        self.assertIsNone(o.maybe_session_watch(now=1000, ticker=boom))

    def test_interval_is_a_minute_scale(self):
        self.assertLessEqual(o.SESSION_WATCH_SEC, 300)   # обнаружение немоты — минуты, не часы


class TestClientContourGate(Base):
    """ВОРОТА КЛИЕНТСКОГО КОНТУРА (класс 30.07) — здесь ворота ЗАКРЫТЫ обратно (Base их открывает
    для тестов механики). Класс живёт ИМЕННО в этом модуле намеренно: правка одного
    pc_orchestrator.py гоняет по карте затронутых только test_pc_orchestrator — без этих голденов
    ворота можно было бы сломать, не покрасив гейт. Подробные голдены — в test_client_contour.py.

    Живой инцидент: 30.07 01:48–01:53 авто-реконсайл сам выкатил коммит цепи ревизора 4528917 на
    боевые userbot/moderation_bot за 17 минут до того, как владелец успел цепь остановить."""

    def setUp(self):
        super().setUp()
        (o._client_block, o._revizor_finding_touches_client) = self._save_cb   # боевые ворота обратно
        self.cards, self.cows, self.restarts = [], [], []
        o._notify = lambda t, *a, **k: self.cards.append(t)
        o._cowork = lambda t, *a, **k: self.cows.append(t)
        self._save_subj = o._commit_subject
        o._commit_subject = lambda c: "тема коммита"
        self.addCleanup(lambda: setattr(o, "_commit_subject", self._save_subj))
        self._save_rr = o.client_contour.release_reason
        o.client_contour.release_reason = lambda *a, **k: None       # оснований пропуска нет
        self.addCleanup(lambda: setattr(o.client_contour, "release_reason", self._save_rr))
        o._apply_restart_at.clear()

    def _restart(self, kind):
        self.restarts.append(kind)
        return True, [4242], "PID поднят, лог свежий"

    def _upd(self, changed):
        return o.maybe_update_bots(5, "тз: правка", "old", changed_fn=lambda hb: changed,
                                   gate_fn=lambda mods: (True, "ok"), restart_fn=self._restart,
                                   head_fn=lambda: "4528917")

    def test_klientskii_fail_derzhitsya_i_daet_kartochku(self):
        note = self._upd(["suggest.py"])
        self.assertEqual(self.restarts, [])                       # НИ ОДНОГО рестарта живого бота
        self.assertIn("ОСТАНОВЛЕНО воротами клиентского контура", note)
        self.assertEqual(len(self.cards), 1)
        self.assertIn("suggest.py", self.cards[0])
        self.assertIn("git revert --no-edit 4528917", self.cards[0])

    def test_vnutrennii_fail_ne_trogaet_vorota(self):
        """Внутренний контур не задет: карта на ботов не ведёт → прежний путь, ворота молчат."""
        self.assertEqual(self._upd(["pc_orchestrator.py", "gate_selective.py"]), "")
        self.assertEqual(self.cards, [])
        self.assertEqual(o._client_paths(["pc_orchestrator.py", "gate_selective.py",
                                          "task_metrics.py", "client_contour.py"]), [])

    def test_fail_closed_priznak_upal(self):
        with mock.patch.object(o.client_contour, "is_client", side_effect=RuntimeError("нет графа")):
            self.assertEqual(o._client_paths(["novyi.py"]), ["novyi.py"])

    def test_osnovanie_da_otkryvaet(self):
        o.client_contour.release_reason = lambda *a, **k: "owner"
        note = self._upd(["suggest.py"])
        self.assertEqual(sorted(self.restarts), ["moderbot", "userbot"])
        self.assertIn("обновлён до 4528917", note)
        self.assertEqual(self.cards, [])

    def test_rekonsilyaciya_derzhit_i_ne_dvigaet_metku(self):
        save = (o._last_child_commit, o._child_reconcile_rejected)
        self.addCleanup(lambda: setattr(o, "_child_reconcile_rejected", save[1]))
        self.addCleanup(lambda: setattr(o, "_last_child_commit", save[0]))
        o._last_child_commit, o._child_reconcile_rejected = "a" * 12, None
        out = o.reconcile_children_tick(head_fn=lambda: "4528917456", diff_fn=lambda a, b: ["suggest.py"],
                                        gate_fn=lambda m: (True, "ok"), restart_fn=self._restart)
        self.assertEqual(self.restarts, [])
        self.assertIn("ОСТАНОВЛЕНО", out)
        self.assertEqual(o._last_child_commit, "a" * 12)          # метка на месте — «да» применит этот же коммит
        self.assertIsNone(o._child_reconcile_rejected)

    def test_vorota_vhoda_klientskaya_nahodka_vladelcu(self):
        cl, hits, det = o._revizor_finding_touches_client("поправь гард приветствий в suggest.py")
        self.assertTrue(cl)
        self.assertEqual(o._revizor_demote_client_task({"action": "task", "task_text": "x"}, hits, det)["action"],
                         "owner")

    def test_vorota_vhoda_vnutrennyaya_nahodka_ostaetsya_zadachei(self):
        cl, _hits, det = o._revizor_finding_touches_client("в pc_orchestrator.py почини троттлинг тика")
        self.assertFalse(cl)
        self.assertTrue(det)

    def test_vorota_vhoda_fail_closed_bez_imen(self):
        cl, _hits, det = o._revizor_finding_touches_client("почини детект, он врёт")
        self.assertTrue(cl)
        self.assertFalse(det)


if __name__ == "__main__":
    unittest.main(verbosity=2)
