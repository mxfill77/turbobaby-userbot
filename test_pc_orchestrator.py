# -*- coding: utf-8 -*-
"""
test_pc_orchestrator.py — мок-тесты ПК-оркестратора. БЕЗ реального claude/Bridge/сети/schtasks —
всё замокано. Разрушительного ничего не выполняется.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_pc_orchestrator -v
"""

import os
import sys
import tempfile
import datetime
import unittest
from pathlib import Path
from unittest import mock

import pc_orchestrator as o


def iso_ago(sec):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=sec)).isoformat()


class FakeBridge:
    def __init__(self):
        self.tasks = {}
        self._id = 0

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

    def set_needs_approval(self, tid, what):
        t = self.tasks.get(tid)
        if t:
            t["status"] = "needs_approval"
            t["result"] = what
        return {"ok": True}

    def task_heartbeat(self, tid):
        return {"ok": True}

    def enqueue_task(self, frm, text, lane="pc"):
        self._id += 1
        self.tasks[self._id] = {"id": self._id, "status": "new", "lane": lane,
                                "task_text": text, "updated": None, "result": None, "from": frm}
        return {"ok": True, "id": self._id}


class Base(unittest.TestCase):
    def setUp(self):
        self._save = (o.bc, o.run_claude, o._notify, o._cowork, o._stopped, o._selfheal_on)
        self.fb = FakeBridge()
        o.bc = self.fb
        o._notify = lambda *a, **k: None
        o._cowork = lambda *a, **k: None
        o._stopped = lambda: False
        o._selfheal_on = lambda: False        # существующие тесты — прежнее поведение (флаг off)

    def tearDown(self):
        (o.bc, o.run_claude, o._notify, o._cowork, o._stopped, o._selfheal_on) = self._save

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
    def test_alive(self):
        o._heartbeat_fresh = lambda *a, **k: True
        called = {"n": 0}
        r = o.watchdog(runner=lambda: (called.__setitem__("n", called["n"] + 1), (0, "x"))[1], verify_sleep=0)
        self.assertEqual(r, "alive")
        self.assertEqual(called["n"], 0)             # живого не перезапускаем

    def test_restart_success(self):
        seq = iter([False, True])                    # протух → после /Run свежий
        o._heartbeat_fresh = lambda *a, **k: next(seq)
        calls = {"n": 0}
        def runner():
            calls["n"] += 1
            return (0, "SUCCESS: task run")
        r = o.watchdog(runner=runner, verify_sleep=0)
        self.assertEqual(r, "restarted")
        self.assertEqual(calls["n"], 1)              # schtasks /Run позван

    def test_failed_to_start_loud(self):
        o._heartbeat_fresh = lambda *a, **k: False   # так и не поднялся
        r = o.watchdog(runner=lambda: (0, "SUCCESS"), verify_sleep=0)
        self.assertEqual(r, "failed_to_start")       # НЕ тихо — громкий лог + пуш (урок 205)

    def test_stopped_switch(self):
        o._stopped = lambda: True
        calls = {"n": 0}
        r = o.watchdog(runner=lambda: (calls.__setitem__("n", calls["n"] + 1), (0, "x"))[1], verify_sleep=0)
        self.assertEqual(r, "stopped")
        self.assertEqual(calls["n"], 0)              # рубильник активен — не поднимаем


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
                mock.patch.object(o, "THINKER_MODEL", "fable-5"), \
                mock.patch.object(o, "THINKER_FALLBACK", "opus-4.8"), \
                mock.patch.object(o.subprocess, "run", fake_run):
            o._thinker_exec("prompt", 5, "t")
        cmd = captured["cmd"]
        self.assertEqual(cmd[cmd.index("--model") + 1], "fable-5")            # своя голова
        self.assertEqual(cmd[cmd.index("--fallback-model") + 1], "opus-4.8")  # свой фолбэк
        self.assertNotIn("sonnet", cmd)                                       # НЕ SUGGEST_MODEL

    def test_thinker_model_defaults_and_no_suggest_coupling(self):
        # дефолты думателя — своя пара; старое имя переменной убрано; на SUGGEST_MODEL не завязан
        self.assertEqual(o.THINKER_MODEL, "fable-5")
        self.assertEqual(o.THINKER_FALLBACK, "opus-4.8")
        self.assertFalse(hasattr(o, "THINKER_MODEL_FALLBACK"))               # переименовано → THINKER_FALLBACK


class TestClientWatchdog(unittest.TestCase):
    """Контур-вотчдог (разбор #128, часть 3): finder/raiser/now/state инъектируются —
    реальных процессов/schtasks НЕ трогаем. Проверяем: живой не поднимается; мёртвый →
    подъём + NOTE; анти-флап (кулдаун); 3 смерти подряд → стоп + громкий NOTE; skip; рубильник."""

    def setUp(self):
        self._save = (o._cowork, o._notify, o._stopped)
        self.notes, self.pushes = [], []
        o._cowork = lambda line: self.notes.append(line)
        o._notify = lambda text: self.pushes.append(text)
        o._stopped = lambda: False

    def tearDown(self):
        (o._cowork, o._notify, o._stopped) = self._save

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
        self.assertTrue(any("нужен разбор" in p for p in self.pushes))   # громкий пуш
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


class TestWatchdogClassFix(unittest.TestCase):
    """Фикс КЛАССА вотчдога (вердикт #171): «не смог проверить» ≠ «мёртв».
    Всё замокано (finder/логи/часы/очередь NOTE) — реальные процессы/CIM/schtasks НЕ трогаем."""

    NOW = 2_000_000_000   # большой wall-clock: os.utime мтаймов лога считается относительно него

    def setUp(self):
        self._save = (o._cowork, o._notify, o._stopped)
        self.notes, self.pushes = [], []
        o._cowork = lambda line: self.notes.append(line)
        o._notify = lambda text: self.pushes.append(text)
        o._stopped = lambda: False
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        (o._cowork, o._notify, o._stopped) = self._save

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
        self.assertTrue(any("слеп" in p for p in self.pushes))
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
        self.assertEqual(o._procs_for_file("booking_draft.py"), {"userbot"})
        self.assertEqual(o._procs_for_file("moderation_ipc.py"), {"userbot"})    # → userbot, НЕ moderbot
        self.assertEqual(o._procs_for_file("moderation_bot.py"), {"moderbot"})
        self.assertEqual(o._procs_for_file("moderation_core.py"), {"moderbot"})
        self.assertEqual(o._procs_for_file("suggest.py"), {"userbot", "moderbot"})
        self.assertEqual(o._procs_for_file("pricing_rules.py"), {"userbot", "moderbot"})
        self.assertEqual(o._procs_for_file("pc_agent.py"), {"pc_agent"})
        self.assertEqual(o._procs_for_file("README.md"), set())
        self.assertEqual(o._procs_for_file("CLAUDE.md"), set())
        self.assertEqual(o._procs_for_file("test_suggest.py"), set())            # тест — не рантайм

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
