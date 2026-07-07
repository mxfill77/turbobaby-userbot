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
        self._su = (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB)
        o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB = "blob-old", "aaa1111", None

    def tearDown(self):
        (o.RUNNING_BLOB, o.RUNNING_COMMIT, o._SU_REJECTED_BLOB) = self._su
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
