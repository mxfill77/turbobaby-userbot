# -*- coding: utf-8 -*-
"""
test_dispatch_notify.py — маршрутизация уведомлений (гигиена пульта pc_agent).

Проверяем send_critical: критический инцидент контура идёт ПЕРВЫМ каналом в тему Инбокс
HQ-форума (1160), а личка Филиппа — ФОЛБЭК при недоступности форума. Реальный Bot API НЕ
дёргаем — подменяем dispatch_notify._api и фиксируем аргументы. TOKEN форсим непустым, чтобы
не читать .env (send_critical при пустом токене молча выходит).
"""

import unittest

import dispatch_notify as dn


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
