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


class Base(unittest.TestCase):
    def setUp(self):
        self._save = (o.bc, o.run_claude, o._notify, o._cowork, o._stopped)
        self.fb = FakeBridge()
        o.bc = self.fb
        o._notify = lambda *a, **k: None
        o._cowork = lambda *a, **k: None
        o._stopped = lambda: False

    def tearDown(self):
        (o.bc, o.run_claude, o._notify, o._cowork, o._stopped) = self._save

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

    def tearDown(self):
        (o._claude_cache, o.CLAUDE_BIN, o._CLAUDE_BASE) = self._save

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
            with mock.patch.object(o.shutil, "which", return_value=None):
                self.assertIsNone(o.resolve_claude())

    def test_cache_revalidates_after_removal(self):
        with tempfile.TemporaryDirectory() as base:
            sub = os.path.join(base, "2.1.5"); os.makedirs(sub)
            exe = os.path.join(sub, "claude.exe"); open(exe, "w").close()
            o._CLAUDE_BASE = base; o.CLAUDE_BIN = "claude"
            with mock.patch.object(o.shutil, "which", return_value=None):
                self.assertEqual(o.resolve_claude(), exe)
                os.remove(exe)                                     # автообновление удалило версию
                self.assertIsNone(o.resolve_claude())              # кэш перепроверен → не мёртвый путь


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
