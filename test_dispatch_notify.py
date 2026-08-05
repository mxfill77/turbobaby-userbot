# -*- coding: utf-8 -*-
"""
test_dispatch_notify.py — маршрутизация уведомлений (гигиена пульта pc_agent).

Проверяем send_critical: критический инцидент контура идёт ПЕРВЫМ каналом в тему Инбокс
HQ-форума (1160), а личка Филиппа — ФОЛБЭК при недоступности форума. Реальный Bot API НЕ
дёргаем — подменяем dispatch_notify._api и фиксируем аргументы. TOKEN форсим непустым, чтобы
не читать .env (send_critical при пустом токене молча выходит).
"""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import dispatch_notify as dn

FIX_TRANSCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "fixtures", "session_end_transcript.live.jsonl")


class TestSendCritical(unittest.TestCase):
    def setUp(self):
        self._save = (dn._api, dn.TOKEN)
        self.calls = []
        dn.TOKEN = "test-token"          # непустой → маршрут не короткозамкнёт на «нет токена»

    def tearDown(self):
        (dn._api, dn.TOKEN) = self._save

    def test_inbox_first_ok(self):
        """Форум доступен → шлём ТОЛЬКО в инбокс 1160, личку не трогаем."""
        def api(method, payload):
            self.calls.append(payload)
            return True, {"ok": True}
        dn._api = api
        channel, ok = dn.send_critical("инцидент")
        self.assertEqual((channel, ok), ("inbox", True))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["chat_id"], dn.HQ_CHAT_ID)
        self.assertEqual(self.calls[0]["message_thread_id"], dn.INBOX_THREAD_ID)  # 1160

    def test_fallback_to_dm_when_forum_down(self):
        """Форум недоступен (первый вызов не ok) → ФОЛБЭК в личку Филиппа."""
        def api(method, payload):
            self.calls.append(payload)
            ok = "message_thread_id" not in payload   # инбокс падает, личка проходит
            return ok, {"ok": ok, "error_code": 403, "description": "forbidden"}
        dn._api = api
        channel, ok = dn.send_critical("инцидент")
        self.assertEqual((channel, ok), ("DM", True))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["message_thread_id"], dn.INBOX_THREAD_ID)  # 1) инбокс
        self.assertEqual(self.calls[1]["chat_id"], dn.DM_CHAT_ID)                 # 2) фолбэк личка
        self.assertNotIn("message_thread_id", self.calls[1])                      # личка — без темы

    def test_no_token_skips(self):
        dn.TOKEN = ""
        dn._api = lambda *a, **k: self.calls.append(a) or (True, {})
        channel, ok = dn.send_critical("инцидент")
        self.assertEqual((channel, ok), ("none", False))
        self.assertEqual(self.calls, [])                # без токена — ни одного вызова API


class TestSendTopic(unittest.TestCase):
    """send_topic: сигнал про ЗАДАНИЕ едет в тему постановки 328 — рядом с самим заданием, а не
    в инбокс аварий контура 1160. Фолбэки (инбокс → личка) на месте, чтобы сигнал не утонул."""

    def setUp(self):
        self._save = (dn._api, dn.TOKEN)
        self.calls = []
        dn.TOKEN = "test-token"

    def tearDown(self):
        (dn._api, dn.TOKEN) = self._save

    def test_goes_to_tasks_topic_328_first(self):
        def api(method, payload):
            self.calls.append(payload)
            return True, {"ok": True}
        dn._api = api
        channel, ok = dn.send_topic("🔇 НЕМАЯ сессия")
        self.assertEqual((channel, ok), ("topic:328", True))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["chat_id"], dn.HQ_CHAT_ID)
        self.assertEqual(self.calls[0]["message_thread_id"], dn.TASKS_THREAD_ID)

    def test_topic_is_328(self):
        self.assertEqual(dn.TASKS_THREAD_ID, 328)
        self.assertNotEqual(dn.TASKS_THREAD_ID, dn.INBOX_THREAD_ID)

    def test_falls_back_to_inbox_then_dm(self):
        """Бота выкинуло из темы постановки → сигнал НЕ теряется: инбокс, затем личка."""
        def api(method, payload):
            self.calls.append(payload)
            ok = "message_thread_id" not in payload      # обе темы падают, личка проходит
            return ok, {"ok": ok, "error_code": 400, "description": "thread not found"}
        dn._api = api
        channel, ok = dn.send_topic("🔇 НЕМАЯ сессия")
        self.assertEqual((channel, ok), ("DM", True))
        self.assertEqual([c.get("message_thread_id") for c in self.calls],
                         [dn.TASKS_THREAD_ID, dn.INBOX_THREAD_ID, None])

    def test_explicit_thread_id_wins(self):
        dn._api = lambda m, p: (self.calls.append(p), (True, {"ok": True}))[1]
        channel, ok = dn.send_topic("текст", 829)
        self.assertEqual(channel, "topic:829")
        self.assertEqual(self.calls[0]["message_thread_id"], 829)

    def test_no_token_skips(self):
        dn.TOKEN = ""
        dn._api = lambda *a, **k: self.calls.append(a) or (True, {})
        self.assertEqual(dn.send_topic("текст"), ("none", False))
        self.assertEqual(self.calls, [])


class TestChainCard(unittest.TestCase):
    """Карточка управления цепью: send() прокидывает reply_markup, _chain_markup даёт две кнопки
    с callback_data «chain:stop|status:<pid>» (их слушает pc_agent)."""

    def setUp(self):
        self._save = (dn._api, dn.TOKEN)
        self.calls = []
        dn.TOKEN = "test-token"

    def tearDown(self):
        (dn._api, dn.TOKEN) = self._save

    def test_markup_has_stop_and_status_buttons(self):
        mk = dn._chain_markup(42)
        row = mk["inline_keyboard"][0]
        datas = [b["callback_data"] for b in row]
        self.assertIn("chain:stop:42", datas)
        self.assertIn("chain:status:42", datas)
        self.assertIn("⏹", row[0]["text"] + row[1]["text"])
        self.assertIn("📊", row[0]["text"] + row[1]["text"])

    def test_send_forwards_reply_markup_to_dm(self):
        def api(method, payload):
            self.calls.append(payload)
            return True, {"ok": True}
        dn._api = api
        channel, ok = dn.send("🧩 план", dn._chain_markup(7))
        self.assertEqual((channel, ok), ("DM", True))
        self.assertEqual(self.calls[0]["chat_id"], dn.DM_CHAT_ID)
        self.assertEqual(self.calls[0]["reply_markup"], dn._chain_markup(7))

    def test_send_without_markup_omits_key(self):
        def api(method, payload):
            self.calls.append(payload)
            return True, {"ok": True}
        dn._api = api
        dn.send("обычный текст")
        self.assertNotIn("reply_markup", self.calls[0])

    def test_card_markup_forwarded_on_forum_fallback(self):
        # DM падает → кнопки едут и в тему-фолбэк
        def api(method, payload):
            self.calls.append(payload)
            ok = "message_thread_id" in payload
            return ok, {"ok": ok, "error_code": 403}
        dn._api = api
        dn.send("🧩 план", dn._chain_markup(9))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1]["reply_markup"], dn._chain_markup(9))


class TestSessionEndCard(unittest.TestCase):
    """ЗАВЕРШЕНИЕ Code-сессии (в т.ч. Remote Control с телефона) = ДВА КАНАЛА ритуала:
    карточка «✅ Code-сессия завершена: <сводка>» в тему Инбокс 1160 (личка — фолбэк) И строка
    в cowork_log. Сводку берём из ЖИВОГО формата transcript-а Claude Code — фикстура
    fixtures/session_end_transcript.live.jsonl снята с реального .jsonl (правило-класс
    «мок обязан копировать живой формат»): блоки text | thinking | tool_use, сайдчейны субагентов."""

    def setUp(self):
        self._save = (dn._api, dn.TOKEN, dn._cowork)
        self.calls, self.cows = [], []
        dn.TOKEN = "test-token"
        dn._cowork = lambda line, **k: self.cows.append(line) or True

    def tearDown(self):
        (dn._api, dn.TOKEN, dn._cowork) = self._save

    def test_summary_is_last_main_branch_text(self):
        s = dn._summary_from_transcript(FIX_TRANSCRIPT)
        self.assertEqual(s, "Контур жив: userbot и moderbot отвечают, демон держит heartbeat.")
        self.assertNotIn("РАЗМЫШЛЕНИЕ", s)     # thinking — не сводка
        self.assertNotIn("СУБАГЕНТА", s)       # sidechain — не сводка
        self.assertNotIn("git status", s)      # tool_use — не сводка

    def test_summary_truncated_with_ellipsis(self):
        self.assertTrue(dn._summary_from_transcript(FIX_TRANSCRIPT, limit=10).endswith("…"))

    def test_summary_missing_file_is_empty(self):
        self.assertEqual(dn._summary_from_transcript(FIX_TRANSCRIPT + ".нет"), "")

    def test_build_card_text(self):
        text = dn._build("session_end", {"transcript_path": FIX_TRANSCRIPT})
        self.assertTrue(text.startswith("✅ Code-сессия завершена: "))
        self.assertIn("Контур жив", text)

    def test_build_falls_back_to_reason(self):
        text = dn._build("session_end", {"transcript_path": "нет", "reason": "clear"})
        self.assertIn("причина: clear", text)

    def test_route_inbox_1160_first_and_cowork(self):
        def api(method, payload):
            self.calls.append(payload)
            return True, {"ok": True}
        dn._api = api
        text = dn._build("session_end", {"transcript_path": FIX_TRANSCRIPT})
        channel, ok = dn.send_critical(text)
        dn._cowork(text)
        self.assertEqual((channel, ok), ("inbox", True))
        self.assertEqual(self.calls[0]["message_thread_id"], dn.INBOX_THREAD_ID)   # 1160
        self.assertEqual(self.cows, [text])                                        # второй канал

    def test_route_falls_back_to_dm(self):
        def api(method, payload):
            self.calls.append(payload)
            ok = "message_thread_id" not in payload
            return ok, {"ok": ok, "error_code": 403}
        dn._api = api
        channel, ok = dn.send_critical(dn._build("session_end", {"transcript_path": FIX_TRANSCRIPT}))
        self.assertEqual((channel, ok), ("DM", True))
        self.assertEqual(self.calls[1]["chat_id"], dn.DM_CHAT_ID)


class TestCoworkDetached(unittest.TestCase):
    """Запись в cowork_log ОТДЕЛЯЕТСЯ (не ждём): живой замер показал 8 с на два round-trip к
    Bridge, из-за чего Claude Code гасил SessionEnd-хук («Hook cancelled») и терялись ОБА канала."""

    def test_spawn_is_detached_and_not_awaited(self):
        seen = {}

        class _Fake:
            pid = 4242

        def spawner(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            return _Fake()

        self.assertTrue(dn._cowork("DONE тест", spawner=spawner))
        self.assertIn("cowork_log_append.py", " ".join(seen["cmd"]))
        self.assertEqual(seen["cmd"][-1], "DONE тест")
        self.assertNotIn("timeout", seen["kw"])          # НЕ ждём завершения
        if hasattr(dn.subprocess, "DETACHED_PROCESS"):   # Windows: переживает смерть сессии
            self.assertTrue(seen["kw"]["creationflags"] & dn.subprocess.DETACHED_PROCESS)

    def test_spawn_failure_is_swallowed(self):
        def boom(*a, **k):
            raise OSError("нет python")
        self.assertFalse(dn._cowork("DONE тест", spawner=boom))   # НЕ роняем сессию


class TestNotificationPing(unittest.TestCase):
    """Пинг «сессия ждёт разрешения» (задача «шквал подтверждений», 2026-07-23): маршрут —
    тема Инбокс 1160 первым каналом (раньше DM-first; исторический Termux-мост умер), в тексте —
    ОЖИДАЮЩАЯ КОМАНДА. Payload Notification-хука команду не содержит («Claude needs your
    permission to use Bash») — команду достаём последним tool_use из ЖИВОГО transcript-а
    (та же фикстура session_end_transcript.live.jsonl: её хвост главной ветки — Bash git status,
    после него attachment-строка, которую парсер обязан перешагнуть)."""

    def setUp(self):
        self._save = (dn._api, dn.TOKEN)
        self.calls = []
        dn.TOKEN = "test-token"

    def tearDown(self):
        (dn._api, dn.TOKEN) = self._save

    def test_command_extracted_from_live_fixture(self):
        self.assertEqual(dn._last_tool_command(FIX_TRANSCRIPT), "Bash: git status")

    def test_missing_transcript_gives_empty(self):
        self.assertEqual(dn._last_tool_command(FIX_TRANSCRIPT + ".нет"), "")

    def test_build_has_message_and_command(self):
        text = dn._build("notification", {
            "message": "Claude needs your permission to use Bash",
            "transcript_path": FIX_TRANSCRIPT,
        })
        self.assertIn("ждёт твоего разрешения", text)
        self.assertIn("Claude needs your permission to use Bash", text)
        self.assertIn("Команда: Bash: git status", text)

    def test_build_without_transcript_has_no_command_line(self):
        text = dn._build("notification", {"message": "ввод"})
        self.assertIn("ждёт твоего разрешения", text)
        self.assertNotIn("Команда:", text)

    def test_secret_values_masked_in_command(self):
        line = {"isSidechain": False, "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "name": "Bash",
                     "input": {"command": "BRIDGE_TOKEN=abc123 venv/Scripts/python.exe x.py"}}]}}
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
            cmd = dn._last_tool_command(p)
        finally:
            os.remove(p)
        self.assertIn("BRIDGE_TOKEN=***", cmd)
        self.assertNotIn("abc123", cmd)

    def test_long_command_truncated(self):
        line = {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "x" * 999}}]}}
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
            cmd = dn._last_tool_command(p)
        finally:
            os.remove(p)
        self.assertTrue(cmd.endswith("…"))
        self.assertLessEqual(len(cmd), dn.CMD_MAX + 1)

    def test_main_routes_to_inbox_1160_first(self):
        # проводка main(): --hook notification → send_critical (payload с темой 1160), не send()
        def api(method, payload):
            self.calls.append(payload)
            return True, {"ok": True}
        dn._api = api
        saved_argv, saved_stdin = sys.argv, sys.stdin
        try:
            sys.argv = ["dispatch_notify.py", "--hook", "notification"]
            sys.stdin = io.StringIO(json.dumps({
                "message": "Claude needs your permission to use Bash",
                "transcript_path": FIX_TRANSCRIPT}))
            with self.assertRaises(SystemExit):
                dn.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["chat_id"], dn.HQ_CHAT_ID)
        self.assertEqual(self.calls[0]["message_thread_id"], dn.INBOX_THREAD_ID)   # 1160
        self.assertIn("Команда: Bash: git status", self.calls[0]["text"])


class TestStopHookDoesNotReachTheDm(unittest.TestCase):
    """ЛИЧКА — ТОЛЬКО ТО, ЧЕГО НЕТ В ИНБОКСЕ И ЧТО ТРЕБУЕТ ОТВЕТА (правило владельца 05.08.2026).

    «✅ Dispatch: задача завершена.» ответа не требует и содержания не несёт, а тот же факт
    через секунду уходит в инбокс 1160 хуком session_end — уже со сводкой. Замер за 7 суток
    (dispatch_notify.log, 29.07–05.08): 392 сообщения в личку, 178 из них — эта строка."""

    def setUp(self):
        # `_cowork` и `_write_session_metrics` МОКАЕМ ОБЯЗАТЕЛЬНО: живой `_cowork` спавнит
        # `cowork_log_append.py` и пишет НАСТОЯЩУЮ строку в мозг. Проверено ценой одной такой
        # строки 05.08.2026 13:07 — тест без этого мока боевой канал не трогать не может.
        self._save = (dn._api, dn.TOKEN, dn._cowork, dn._write_session_metrics)
        self.calls, self.cows = [], []
        dn.TOKEN = "test-token"
        dn._cowork = lambda t: self.cows.append(t)
        dn._write_session_metrics = lambda m: False

        def api(method, payload):
            self.calls.append(payload)
            return True, {"ok": True}
        dn._api = api

    def tearDown(self):
        (dn._api, dn.TOKEN, dn._cowork, dn._write_session_metrics) = self._save

    def _run(self, argv, payload):
        saved_argv, saved_stdin = sys.argv, sys.stdin
        try:
            sys.argv = argv
            sys.stdin = io.StringIO(json.dumps(payload))
            with self.assertRaises(SystemExit):
                dn.main()
        finally:
            sys.argv, sys.stdin = saved_argv, saved_stdin

    def test_stop_hook_sends_nothing(self):
        self._run(["dispatch_notify.py", "--hook", "stop"], {})
        self.assertEqual(self.calls, [], "хук stop снова пишет в личку")

    def test_stop_text_is_kept_for_the_log(self):
        """Текст не выброшен: он остаётся в строке лога, чтобы пропажа читалась как решение."""
        self.assertIn("задача завершена", dn._build("stop", {}))

    def test_session_end_still_goes_to_inbox_1160(self):
        """Событие не потеряно: канал со СВОДКОЙ не тронут."""
        self._run(["dispatch_notify.py", "--hook", "session_end"],
                  {"transcript_path": FIX_TRANSCRIPT})
        self.assertTrue(self.calls, "session_end перестал доставляться")
        self.assertEqual(self.calls[0]["chat_id"], dn.HQ_CHAT_ID)
        self.assertEqual(self.calls[0]["message_thread_id"], dn.INBOX_THREAD_ID)   # 1160
        self.assertEqual(len(self.cows), 1, "второй канал (cowork_log) замокан и позван ровно раз")


class TestStdinBom(unittest.TestCase):
    """BOM перед JSON-нагрузкой хука не должен обнулять её (живой прокол: сводка выродилась
    в «без текстового итога», потому что strip() не срезает ﻿)."""

    def test_bom_prefixed_payload_parses(self):
        import io
        saved = dn.sys.stdin
        try:
            dn.sys.stdin = io.StringIO("﻿" + json.dumps({"reason": "clear"}))
            self.assertEqual(dn._read_stdin_json(), {"reason": "clear"})
        finally:
            dn.sys.stdin = saved


class TestSessionMetrics(unittest.TestCase):
    """Метрики СЕССИИ (26.07.2026): третья полоса пишет ту же строку METRICS, что и два демона.
    Формат транскрипта снят с живого файла ~/.claude/projects/<repo>/<session>.jsonl."""

    LINES = [
        {"type": "user", "timestamp": "2026-07-24T21:34:27.310Z", "effort": "xhigh",
         "entrypoint": "claude-desktop"},
        {"type": "last-prompt", "lastPrompt": "ultrathink ТОЛЬКО read-only, ничего не менять"},
        {"type": "assistant", "timestamp": "2026-07-24T21:35:00.000Z",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "usage": {"input_tokens": 2, "cache_read_input_tokens": 1000,
                               "cache_creation_input_tokens": 500, "output_tokens": 40}}},
        {"type": "assistant", "timestamp": "2026-07-24T21:35:03.500Z",
         "message": {"role": "assistant", "model": "claude-opus-5",
                     "usage": {"input_tokens": 3, "cache_read_input_tokens": 2000,
                               "cache_creation_input_tokens": 0, "output_tokens": 60}}},
    ]

    def _transcript(self):
        fd, p = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for d in self.LINES:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        return p

    def test_format_matches_both_lanes(self):
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "xhigh",
                                          "CLAUDE_CODE_SESSION_ID": "78d27126-abcd",
                                          "CLAUDE_CODE_ENTRYPOINT": "claude-desktop"}):
            line = dn._session_metrics_line(self._transcript())
        self.assertTrue(line.startswith("METRICS "), line)
        self.assertIn(" task=78d27126 ", line)
        self.assertIn(" model=claude-opus-5 ", line)
        self.assertIn(" effort=xhigh ", line)
        self.assertIn(" src=claude-desktop ", line)
        self.assertIn(" outcome=done ", line)
        # все поля общего формата на месте
        keys = [p.split("=")[0] for p in line.split()[1:]]
        for k in ("task", "lane", "type", "mode", "src", "model", "effort", "start", "end",
                  "dur_s", "outcome", "attempts", "selfheals", "tokens_in", "tokens_out"):
            self.assertIn(k, keys, k)

    def test_lane_distinguishes_session(self):
        """Отдельное значение полосы: не pc и не vps."""
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "xhigh"}):
            line = dn._session_metrics_line(self._transcript())
        self.assertIn(" lane=session ", line)
        self.assertNotIn(" lane=pc ", line)
        self.assertNotIn(" lane=vps ", line)
        self.assertEqual(dn.SESSION_LANE, "session")

    def test_duration_is_fractional(self):
        """Длительность с дробной частью: 21:34:27.310 → 21:35:03.500 = 36.19 с."""
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "xhigh"}):
            line = dn._session_metrics_line(self._transcript())
        self.assertIn(" dur_s=36.19 ", line)

    def test_type_from_prompt(self):
        """Тип задачи — из последнего запроса владельца, теми же признаками, что у полос."""
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "xhigh"}):
            line = dn._session_metrics_line(self._transcript())
        self.assertIn(" type=read ", line)

    def test_tokens_include_cache(self):
        """ПОЛНЫЙ вход: input + cacheRead + cacheCreation (2+1000+500 + 3+2000+0 = 3505)."""
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "xhigh"}):
            line = dn._session_metrics_line(self._transcript())
        self.assertIn(" tokens_in=3505 ", line)
        self.assertTrue(line.endswith(" tokens_out=100"), line)   # последнее поле, пробела за ним нет

    def test_effort_from_environment_wins(self):
        """Уровень усилий берём из ЖИВОГО окружения сессии, а не из файла настроек."""
        with mock.patch.dict(os.environ, {"CLAUDE_EFFORT": "high"}):
            line = dn._session_metrics_line(self._transcript())
        self.assertIn(" effort=high ", line)

    def test_failsafe_no_transcript(self):
        """Замерить нечего → пустая строка, и session_end от этого не падает."""
        self.assertEqual(dn._session_metrics_line("/нет/такого/файла.jsonl"), "")
        self.assertEqual(dn._session_metrics_line(""), "")
        self.assertFalse(dn._write_session_metrics(""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
