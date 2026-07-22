# -*- coding: utf-8 -*-
"""
test_dispatch_notify.py — маршрутизация уведомлений (гигиена пульта pc_agent).

Проверяем send_critical: критический инцидент контура идёт ПЕРВЫМ каналом в тему Инбокс
HQ-форума (1160), а личка Филиппа — ФОЛБЭК при недоступности форума. Реальный Bot API НЕ
дёргаем — подменяем dispatch_notify._api и фиксируем аргументы. TOKEN форсим непустым, чтобы
не читать .env (send_critical при пустом токене молча выходит).
"""

import json
import os
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
