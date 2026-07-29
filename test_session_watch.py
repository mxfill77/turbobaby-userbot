# -*- coding: utf-8 -*-
"""
test_session_watch.py — голдены ДЕТЕКТОРА НЕМОТЫ (session_watch.py).

Все числа и строки взяты с ЖИВОГО замера 29.07.2026 (корпус 13 сессий, инцидент PID 21216),
а не выдуманы: командная строка — дословная из Win32_Process, маркеры работы — дословные из
rc_server_debug-cse_*.log, задержки транскрипта — реальные 1.7…3.1 с.
"""

import os
import time
import json
import tempfile
import unittest

import session_watch as sw

# --- живая фактура 29.07 (см. docs/artifacts/2026-07-29-mute-session-21216-evidence.md) -----
DEAD_CMDLINE = (
    r"C:\Users\mxfill1\.local\bin\claude.exe --print "
    r"--sdk-url https://api.anthropic.com/v1/code/sessions/cse_01Xa5KTXp3RbcmFFXqeoAcJx "
    r"--session-id cse_01Xa5KTXp3RbcmFFXqeoAcJx --input-format stream-json "
    r"--output-format stream-json --replay-user-messages "
    r"--debug-file D:\turbobaby-bot\rc_server_debug-cse_01Xa5KTXp3RbcmFFXqeoAcJx.log"
)
DEAD_SID = "bc8c785c-4ecf-4290-8986-6104acbe5cf5"
T0 = 1785314198.425          # startedAt мёртвой сессии, сек (из ~/.claude/sessions/21216.json)

# Задержки «старт процесса → появился транскрипт» у ВСЕХ 12 работавших сессий суток.
LIVE_TRANSCRIPT_DELAYS = [1.7, 1.7, 1.7, 1.7, 1.7, 1.7, 1.8, 1.8, 1.8, 1.8, 2.9, 3.1]

# Дословные строки живых логов: слева — здоровая сессия, справа — немая (в немой их НОЛЬ).
LIVE_WORK_LINES = [
    "2026-07-29T05:31:56.972Z [DEBUG] [API REQUEST] /v1/messages x-client-request-id=abc",
    "2026-07-29T05:32:01.100Z [DEBUG] Stream started - received first chunk",
    '2026-07-29T05:31:56.972Z [DEBUG] "[auto-mode] new action being classified: {\\"Bash\\":\\"git diff\\"}"',
    "2026-07-29T05:32:02.000Z [DEBUG] Hooks: Found 0 total hooks in registry",
]
LIVE_MUTE_LINES = [
    "2026-07-29T08:36:39.387Z [DEBUG] SSETransport: Connected",
    "2026-07-29T11:24:24.414Z [DEBUG] CCRClient: Heartbeat sent",
    "2026-07-29T08:52:22.324Z [DEBUG] SSETransport: Event seq=2 event_type=control_request",
]


def mk_session(pid=21216, sid=DEAD_SID, started=T0, entry="sdk-cli", name="turbobaby-bot-51"):
    return {"pid": pid, "session_id": sid, "cwd": "D:\\turbobaby-bot", "started_at": started,
            "entrypoint": entry, "kind": "interactive", "name": name, "version": "2.1.218",
            "path": "C:\\Users\\mxfill1\\.claude\\sessions\\%d.json" % pid}


class Notifier(object):
    """Двойник канала: считает карточки и умеет притворяться недоставкой."""

    def __init__(self, ok=True):
        self.ok = ok
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return ("topic:328", self.ok)


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.state = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.state)
        self.notifier = Notifier()

    def tearDown(self):
        for p in (self.state, self.state + ".tmp"):
            try:
                os.unlink(p)
            except OSError:
                pass

    def run_tick(self, sessions, now, transcript=None, activity=None, live=None,
                 facts=None, notifier=None, threshold=None):
        return sw.tick(
            now=now, state_path=self.state, notifier=notifier or self.notifier,
            sessions=sessions, threshold=sw.MUTE_AFTER if threshold is None else threshold,
            live=live or (lambda: (True, {s["pid"] for s in sessions})),
            transcript=transcript or (lambda s: None),
            facts=facts or (lambda pid: {"cmdline": DEAD_CMDLINE, "created": T0}),
            activity=activity or (lambda p: False),
        )


class TestMuteGoldens(Base):
    """Четыре голдена задания: немая → сигнал; повтор → тишина; транскрипт → тишина;
    tool_use без транскрипта → тишина."""

    def test_mute_older_than_threshold_signals_once(self):
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(self.notifier.texts), 1)
        self.assertEqual(sent[0]["session_id"], DEAD_SID)

    def test_same_session_next_tick_is_silent(self):
        self.run_tick([mk_session()], now=T0 + 2 * 3600)
        again = self.run_tick([mk_session()], now=T0 + 2 * 3600 + 60)
        self.assertEqual(again, [])
        self.assertEqual(len(self.notifier.texts), 1, "сигнал обязан быть РОВНО один на сессию")

    def test_session_with_transcript_is_silent(self):
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600,
                             transcript=lambda s: "C:\\...\\%s.jsonl" % s["session_id"])
        self.assertEqual((sent, self.notifier.texts), ([], []))

    def test_tool_use_without_transcript_is_silent(self):
        """Транскрипта ещё нет, но следы работы есть → это НЕ немота (признак — И, а не ИЛИ)."""
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600, activity=lambda p: True)
        self.assertEqual((sent, self.notifier.texts), ([], []))

    def test_young_session_is_silent(self):
        """Первые секунды жизни: транскрипт ещё в пути — ложняком быть не должно."""
        sent = self.run_tick([mk_session()], now=T0 + 4)
        self.assertEqual((sent, self.notifier.texts), ([], []))


class TestThreshold(unittest.TestCase):
    """Порог обоснован ЧИСЛАМИ живого замера, а не вкусом."""

    def test_threshold_far_above_every_live_start(self):
        worst = max(LIVE_TRANSCRIPT_DELAYS)
        self.assertEqual(worst, 3.1)
        self.assertGreaterEqual(sw.MUTE_AFTER / worst, 90,
                                "порог обязан быть на два порядка выше худшего живого старта")

    def test_threshold_far_below_the_incident(self):
        self.assertLessEqual(sw.MUTE_AFTER * 20, 2 * 3600,
                             "порог обязан ловить много раньше сегодняшних двух часов")

    def test_no_live_session_would_be_flagged(self):
        """Ложных срабатываний на корпусе суток — НОЛЬ: все 12 работавших уложились в порог."""
        self.assertEqual([d for d in LIVE_TRANSCRIPT_DELAYS if d >= sw.MUTE_AFTER], [])


class TestSafety(Base):
    """Пробы, которые обязаны МОЛЧАТЬ, и одна, которая обязана сигналить при сомнении."""

    def test_blind_process_list_stays_silent(self):
        """Класс #171: «не смог проверить» ≠ «мёртв» — весь тик молчит и повторит позже."""
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600, live=lambda: (False, set()))
        self.assertEqual((sent, self.notifier.texts), ([], []))

    def test_exited_process_is_not_muteness(self):
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600, live=lambda: (True, set()))
        self.assertEqual((sent, self.notifier.texts), ([], []))

    def test_pid_reuse_is_rejected(self):
        """Чужой процесс на том же PID: время старта не сходится → не наша сессия."""
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600,
                             facts=lambda pid: {"cmdline": "claude.exe", "created": T0 + 9000})
        self.assertEqual((sent, self.notifier.texts), ([], []))

    def test_unverifiable_identity_fails_open(self):
        """Проба личности не ответила → СИГНАЛИМ: лишняя карточка дешевле пропущенной немоты."""
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600, facts=lambda pid: {})
        self.assertEqual(len(sent), 1)

    def test_out_of_scope_entrypoint_ignored(self):
        sent = sw.tick(now=T0 + 2 * 3600, state_path=self.state, notifier=self.notifier,
                       sessions=[mk_session(entry="claude-desktop")], entrypoints=("sdk-cli",),
                       live=lambda: (True, {21216}), transcript=lambda s: None,
                       facts=lambda pid: {"cmdline": DEAD_CMDLINE, "created": T0},
                       activity=lambda p: False)
        self.assertEqual((sent, self.notifier.texts), ([], []))

    def test_failed_delivery_is_retried_next_tick(self):
        """Недоставка НЕ помечается «уже сигналили» — иначе сигнал терялся бы навсегда."""
        bad = Notifier(ok=False)
        self.assertEqual(self.run_tick([mk_session()], now=T0 + 2 * 3600, notifier=bad), [])
        good = Notifier(ok=True)
        sent = self.run_tick([mk_session()], now=T0 + 2 * 3600 + 60, notifier=good)
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(good.texts), 1)


class TestProbes(unittest.TestCase):
    """Разбор живых форматов: slug, cmdline, маркеры работы, поиск транскрипта."""

    def test_project_slug_matches_live_dir(self):
        self.assertEqual(sw.project_slug("D:\\turbobaby-bot"), "d--turbobaby-bot")

    def test_parse_debug_file_from_live_cmdline(self):
        self.assertEqual(sw.parse_debug_file(DEAD_CMDLINE),
                         r"D:\turbobaby-bot\rc_server_debug-cse_01Xa5KTXp3RbcmFFXqeoAcJx.log")

    def test_parse_debug_file_absent(self):
        self.assertIsNone(sw.parse_debug_file("claude.exe --print"))
        self.assertIsNone(sw.parse_debug_file(None))

    def test_live_work_lines_are_activity(self):
        import io
        for line in LIVE_WORK_LINES:
            with self.subTest(line=line[:60]):
                self.assertTrue(sw.log_has_activity("x", opener=lambda p, l=line: io.StringIO(l)))

    def test_live_mute_lines_are_not_activity(self):
        """Дословный хвост немой сессии: heartbeat + SSE и ни одного следа работы."""
        import io
        self.assertFalse(sw.log_has_activity(
            "x", opener=lambda p: io.StringIO("\n".join(LIVE_MUTE_LINES))))

    def test_missing_log_is_not_activity(self):
        self.assertFalse(sw.log_has_activity(None))
        self.assertFalse(sw.log_has_activity("Z:\\нет\\такого.log"))

    def test_transcript_found_by_glob_when_cwd_differs(self):
        """Сессия заведена в другом каталоге: точного пути нет, но транскрипт ЕСТЬ — молчим."""
        s = mk_session()
        got = sw.transcript_path(s, projects_dir="P", isfile=lambda p: False,
                                 globber=lambda pat: ["P\\иной-проект\\%s.jsonl" % DEAD_SID])
        self.assertTrue(got.endswith("%s.jsonl" % DEAD_SID))

    def test_transcript_absent(self):
        s = mk_session()
        self.assertIsNone(sw.transcript_path(s, projects_dir="P", isfile=lambda p: False,
                                             globber=lambda pat: []))


class TestReadSessions(unittest.TestCase):
    """Реестр ~/.claude/sessions/<pid>.json — формат снят с живого файла 21216.json."""

    LIVE_JSON = {"pid": 21216, "sessionId": DEAD_SID, "cwd": "D:\\turbobaby-bot",
                 "startedAt": 1785314198425, "procStart": "639209361974962110",
                 "version": "2.1.218", "peerProtocol": 1, "kind": "interactive",
                 "entrypoint": "sdk-cli", "name": "turbobaby-bot-51", "nameSource": "derived"}

    def test_parses_live_registry_entry(self):
        import io
        got = sw.read_sessions(
            sessions_dir="S", globber=lambda p: ["S\\21216.json"],
            opener=lambda p: io.StringIO(json.dumps(self.LIVE_JSON)))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["pid"], 21216)
        self.assertEqual(got[0]["entrypoint"], "sdk-cli")
        self.assertAlmostEqual(got[0]["started_at"], T0, places=3)

    def test_broken_json_is_skipped_not_fatal(self):
        import io
        got = sw.read_sessions(sessions_dir="S", globber=lambda p: ["S\\bad.json"],
                               opener=lambda p: io.StringIO("{недописан"))
        self.assertEqual(got, [])


class TestSignalText(unittest.TestCase):
    """Карточка обязана нести всё, что просил владелец: имя, PID, старт, срок, ЧТО не появилось."""

    def setUp(self):
        self.text = sw.format_signal({
            "session_id": DEAD_SID, "pid": 21216, "name": "turbobaby-bot-51",
            "entrypoint": "sdk-cli", "started_at": T0, "age": 2 * 3600 + 53 * 60,
            "cwd": "D:\\turbobaby-bot",
            "debug_log": r"D:\turbobaby-bot\rc_server_debug-cse_01Xa5KTXp3RbcmFFXqeoAcJx.log"})

    def test_carries_every_required_field(self):
        for part in ("turbobaby-bot-51", "21216", "2 ч 53 мин", DEAD_SID,
                     "транскрипт", "tool_use", time.strftime("%d.%m", time.localtime(T0))):
            with self.subTest(part=part):
                self.assertIn(part, self.text)

    def test_names_the_log_of_the_session(self):
        self.assertIn("rc_server_debug-cse_01Xa5KTXp3RbcmFFXqeoAcJx.log", self.text)

    def test_human_age(self):
        self.assertEqual(sw.human_age(45), "45 с")
        self.assertEqual(sw.human_age(420), "7 мин")
        self.assertEqual(sw.human_age(2 * 3600 + 53 * 60), "2 ч 53 мин")


class TestState(unittest.TestCase):
    def test_prune_drops_only_stale_marks(self):
        st = {"signalled": {"свежая": {"at": 1000.0}, "древняя": {"at": 1.0}}}
        sw.prune_state(st, now=1000.0 + 10, ttl=100)
        self.assertEqual(list(st["signalled"]), ["свежая"])

    def test_load_of_broken_state_is_empty_not_fatal(self):
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("не json")
            self.assertEqual(sw.load_state(p), {"signalled": {}})
        finally:
            os.unlink(p)

    def test_notify_reads_delivery_from_child_stdout(self):
        """ok берём из stdout ребёнка: dispatch_notify по контракту всегда выходит нулём."""
        class P(object):
            stdout = "channel=topic:328 ok=1"
        self.assertEqual(sw.notify("текст", runner=lambda *a, **k: P()), ("topic:328", True))

        class F(object):
            stdout = "channel=DM ok=0"
        self.assertEqual(sw.notify("текст", runner=lambda *a, **k: F()), ("DM", False))

        class Junk(object):
            stdout = ""
        self.assertEqual(sw.notify("текст", runner=lambda *a, **k: Junk()), ("unknown", False))


class TestLivePids(unittest.TestCase):
    """tasklist: живой CSV-формат, честная пустота и слепота — три разных исхода."""

    LIVE_CSV = ('"claude.exe","21216","Console","1","40 916 КБ"\n'
                '"claude.exe","20552","Console","1","60 000 КБ"\n')

    def test_parses_live_csv(self):
        class P(object):
            stdout = TestLivePids.LIVE_CSV
            returncode = 0
        ok, pids = sw.live_pids(runner=lambda *a, **k: P())
        self.assertEqual((ok, pids), (True, {21216, 20552}))

    def test_empty_is_honest_not_blind(self):
        class P(object):
            stdout = "INFO: No tasks are running which match the specified criteria."
            returncode = 0
        ok, pids = sw.live_pids(runner=lambda *a, **k: P())
        self.assertEqual((ok, pids), (True, set()))

    def test_timeout_is_blindness(self):
        def boom(*a, **k):
            raise OSError("timeout")
        self.assertEqual(sw.live_pids(runner=boom), (False, set()))


if __name__ == "__main__":
    unittest.main()
