# -*- coding: utf-8 -*-
"""
test_pc_agent.py — тесты pc_agent (разбор #128, часть 1): stdout/stderr детей уходят в
logs/<имя>_stderr.log (APPEND), чтобы смерть ребёнка оставляла traceback.
Реальные боты НЕ поднимаются: спавним безобидный python -c, пишущий в stderr.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_pc_agent -v
"""
import os
import sys
import time
import types
import asyncio
import tempfile
import subprocess
import unittest
from pathlib import Path

import pc_agent as a
import dispatch_notify as dn


class TestChildStderrLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._save_logs = a.LOGS_DIR
        a.LOGS_DIR = Path(self.tmp)          # не пачкаем реальный logs/

    def tearDown(self):
        a.LOGS_DIR = self._save_logs

    def test_child_log_handle_creates_file_with_header(self):
        fh = a._child_log_handle("userbot")
        try:
            path = Path(self.tmp) / "userbot_stderr.log"
            self.assertTrue(path.exists())
            self.assertIn("userbot: старт", path.read_text(encoding="utf-8"))
        finally:
            fh.close()

    def test_child_log_handle_appends(self):
        a._child_log_handle("moderbot").close()
        a._child_log_handle("moderbot").close()
        path = Path(self.tmp) / "moderbot_stderr.log"
        # две шапки старта → APPEND, историю не перетёрли
        self.assertEqual(path.read_text(encoding="utf-8").count("moderbot: старт"), 2)

    def test_child_stderr_traceback_captured(self):
        """Смерть ребёнка с traceback в stderr ДОЛЖНА оседать в файле (суть фикса)."""
        logf = a._child_log_handle("userbot")
        p = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stderr.write('BOOM_TRACEBACK_XYZ'); sys.exit(1)"],
            stdout=logf, stderr=subprocess.STDOUT)
        p.wait(timeout=30)
        logf.close()
        # дать ОС дописать буфер ребёнка
        for _ in range(20):
            data = (Path(self.tmp) / "userbot_stderr.log").read_text(encoding="utf-8")
            if "BOOM_TRACEBACK_XYZ" in data:
                break
            time.sleep(0.1)
        self.assertIn("BOOM_TRACEBACK_XYZ", data)


class TestUnknownCommandReply(unittest.TestCase):
    """ФИКС #171/3: неизвестная команда темы 205 → эхо + перечень РЕАЛЬНЫХ команд, не тишина."""

    def test_echoes_unknown_and_lists_real_commands(self):
        r = a.unknown_command_reply("статус контура")
        self.assertIn("не знаю", r)
        self.assertIn("статус контура", r)          # эхо непонятого
        self.assertIn("умею", r)
        self.assertIn("обнови userbot", r)          # реальная команда из роутинга
        self.assertIn("стоп модербот", r)
        self.assertIn("обновись", r)

    def test_empty_command(self):
        r = a.unknown_command_reply("")
        self.assertIn("умею", r)
        self.assertIn("обнови userbot", r)

    def test_long_command_truncated(self):
        r = a.unknown_command_reply("ы" * 500)
        self.assertIn("…", r)
        self.assertLess(len(r), 500 + 400)          # эхо обрезан, не раздувает ответ

    def test_known_commands_nonempty(self):
        self.assertTrue(len(a.KNOWN_COMMANDS) >= 7)


class TestSupervisionLabel(unittest.TestCase):
    """status показывает СОСТОЯНИЕ НАДЗОРА (под вотчдогом / cooldown / halt) вместо «кто запустил».
    Читаем снимок демона; чистая _supervision_label покрыта по ветвям, status_text — интеграция."""

    NOW = 1_000_000.0
    COOLDOWN = 900

    def _snap(self, ent, ts=None):
        return {"ts": self.NOW if ts is None else ts, "cooldown": self.COOLDOWN,
                "max_deaths": 3, "children": {"userbot": ent}}

    def test_stop_switch_wins(self):
        # рубильник взведён → надзор намеренно выключен, снимок игнорируем
        lbl = a._supervision_label("userbot", self._snap({"deaths": 0, "halted": False, "last_raise": 0}),
                                   self.NOW, stop_present=True)
        self.assertIn("выключен", lbl)
        self.assertIn("рубильник", lbl)

    def test_no_snapshot_demon_silent(self):
        lbl = a._supervision_label("userbot", None, self.NOW, stop_present=False)
        self.assertIn("демон молчит", lbl)

    def test_stale_snapshot_demon_silent(self):
        old = self._snap({"deaths": 0, "halted": False, "last_raise": 0}, ts=self.NOW - 10_000)
        lbl = a._supervision_label("userbot", old, self.NOW, stop_present=False, stale=900)
        self.assertIn("протух", lbl)

    def test_healthy_under_watchdog(self):
        lbl = a._supervision_label("userbot", self._snap({"deaths": 0, "halted": False, "last_raise": 0}),
                                   self.NOW, stop_present=False)
        self.assertIn("под вотчдогом", lbl)
        self.assertNotIn("cooldown", lbl)

    def test_cooldown_after_recent_raise(self):
        # недавно поднимали (600с назад, cooldown 900) → под вотчдогом · cooldown, ~300с осталось
        ent = {"deaths": 1, "halted": False, "last_raise": self.NOW - 600}
        lbl = a._supervision_label("userbot", self._snap(ent), self.NOW, stop_present=False)
        self.assertIn("cooldown", lbl)
        self.assertIn("300", lbl)

    def test_halt_needs_review(self):
        ent = {"deaths": 3, "halted": True, "last_raise": self.NOW - 10}
        lbl = a._supervision_label("userbot", self._snap(ent), self.NOW, stop_present=False)
        self.assertIn("ОСТАНОВЛЕН", lbl)
        self.assertIn("3", lbl)
        self.assertIn("разбор", lbl)

    def test_child_missing_from_snapshot(self):
        snap = {"ts": self.NOW, "cooldown": self.COOLDOWN, "children": {}}
        lbl = a._supervision_label("userbot", snap, self.NOW, stop_present=False)
        self.assertIn("нет в снимке", lbl)

    def test_status_text_shows_supervision_not_launcher(self):
        # интеграция: живой userbot + снимок «под вотчдогом» → в шапке НАДЗОР, НЕ «вручную/агентом»
        save = (a.UB.status, a._read_watch_snapshot, a._read_log_lines, a.STOP_FLAG)

        class _Stop:
            @staticmethod
            def exists():
                return False
        try:
            a.UB.status = lambda: (True, [7572], False)   # alive, orphan-PID, managed=False (был бы «вручную»)
            a._read_watch_snapshot = lambda *x, **k: {"ts": time.time(), "cooldown": 900,
                                                      "children": {"userbot": {"deaths": 0, "halted": False,
                                                                               "last_raise": 0}}}
            a._read_log_lines = lambda *x, **k: []
            a.STOP_FLAG = _Stop
            txt = a.status_text()
        finally:
            (a.UB.status, a._read_watch_snapshot, a._read_log_lines, a.STOP_FLAG) = save
        self.assertIn("РАБОТАЕТ", txt)
        self.assertIn("PID 7572", txt)
        self.assertIn("надзор:", txt)
        self.assertIn("под вотчдогом", txt)
        self.assertNotIn("вручную", txt)                 # способ запуска убран
        self.assertNotIn("агентом", txt)


class TestChainCallback(unittest.TestCase):
    """Кнопки управления цепью дирижёра: разбор callback_data и owner-gate (только владелец)."""

    def test_parse_stop_and_status(self):
        self.assertEqual(a._chain_cb_parse("chain:stop:42"), ("stop", "42"))
        self.assertEqual(a._chain_cb_parse("chain:status:7"), ("status", "7"))

    def test_parse_rejects_foreign_callback(self):
        self.assertIsNone(a._chain_cb_parse("m:12:yes"))     # callback модербота — не наш
        self.assertIsNone(a._chain_cb_parse("chain:kill:1"))  # неизвестное действие
        self.assertIsNone(a._chain_cb_parse("chain:stop:abc"))
        self.assertIsNone(a._chain_cb_parse(""))
        self.assertIsNone(a._chain_cb_parse(None))

    def test_owner_gate(self):
        self.assertTrue(a._chain_cb_authorized(a.ALLOWED_USER_ID))
        self.assertFalse(a._chain_cb_authorized(a.ALLOWED_USER_ID + 1))
        self.assertFalse(a._chain_cb_authorized(None))


class TestChainCallbackRoute(unittest.TestCase):
    """ГОЛДЕН роутинга кнопок цепи. Инцидент 14.07 23:37 / 15.07 10:59: тап молчал.
    Классовый инвариант: answer НИКОГДА не пуст → тап всегда снимает «часики»; неизвестный/
    протухший callback → честное «карточка устарела», а не тишина. Данные берём РЕАЛЬНЫЕ —
    ровно те, что эмитит dispatch_notify._chain_markup (тест ≠ идеализация)."""

    OWNER = a.ALLOWED_USER_ID
    STRANGER = a.ALLOWED_USER_ID + 1

    def _real_cb(self, pid):
        """Достаём callback_data так, как их реально кладёт продюсер карточек."""
        row = dn._chain_markup(pid)["inline_keyboard"][0]
        return row[0]["callback_data"], row[1]["callback_data"]  # (stop, status)

    def test_real_callback_data_shape(self):
        stop_cb, status_cb = self._real_cb(42)
        self.assertEqual(stop_cb, "chain:stop:42")
        self.assertEqual(status_cb, "chain:status:42")

    def test_owner_stop_routes_to_action(self):
        stop_cb, _ = self._real_cb(42)
        r = a._chain_cb_route(stop_cb, self.OWNER)
        self.assertTrue(r["ok"])
        self.assertEqual((r["action"], r["pid"]), ("stop", "42"))
        self.assertTrue(r["answer"])                 # непустой ACK
        self.assertFalse(r["alert"])
        self.assertIsNone(r["note"])

    def test_owner_status_routes_to_action(self):
        _, status_cb = self._real_cb(7)
        r = a._chain_cb_route(status_cb, self.OWNER)
        self.assertTrue(r["ok"])
        self.assertEqual((r["action"], r["pid"]), ("status", "7"))
        self.assertTrue(r["answer"])

    def test_owner_stale_card_is_honest_not_silent(self):
        # неразобранный/протухший callback владельца → «карточка устарела» + пояснение, НЕ тишина
        for bad in ("chain:kill:1", "chain:stop:abc", "m:12:yes", "", None):
            r = a._chain_cb_route(bad, self.OWNER)
            self.assertFalse(r["ok"], bad)
            self.assertIsNone(r["action"], bad)
            self.assertIn("устарела", r["answer"], bad)
            self.assertTrue(r["alert"], bad)
            self.assertIn("статус", (r["note"] or "").lower(), bad)

    def test_stranger_denied_before_parse(self):
        stop_cb, _ = self._real_cb(42)
        r = a._chain_cb_route(stop_cb, self.STRANGER)
        self.assertFalse(r["ok"])
        self.assertIn("нет прав", r["answer"])
        self.assertTrue(r["alert"])
        self.assertIsNone(r["note"])

    def test_answer_never_empty_invariant(self):
        # ядро фикса: на ЛЮБОЙ вход — непустой мгновенный ACK
        for data in ("chain:stop:1", "chain:status:2", "garbage", "", None):
            for uid in (self.OWNER, self.STRANGER, None):
                r = a._chain_cb_route(data, uid)
                self.assertTrue(r["answer"], f"пустой answer для data={data!r} uid={uid}")


class _FakeQuery:
    def __init__(self, data, uid, chat_id=a.HQ_CHAT_ID, thread=None):
        self.data = data
        self.from_user = types.SimpleNamespace(id=uid) if uid is not None else None
        self.message = types.SimpleNamespace(chat_id=chat_id, message_thread_id=thread)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))


class TestChainCallbackHandlerGolden(unittest.TestCase):
    """Сквозной голден обработчика: тап → (мгновенный answerCallbackQuery) + видимое сообщение,
    действие роутится в демон. Реальные callback_data из dispatch_notify. _chain_cli замокан."""

    def setUp(self):
        self._save_cli = a._chain_cli
        self._calls = []
        a._chain_cli = lambda action, pid: self._calls.append((action, pid)) or f"OK:{action}:{pid}"

    def tearDown(self):
        a._chain_cli = self._save_cli

    def _run(self, data, uid, thread=None):
        q = _FakeQuery(data, uid, thread=thread)
        bot = _FakeBot()
        update = types.SimpleNamespace(callback_query=q)
        context = types.SimpleNamespace(bot=bot)
        asyncio.run(a.on_chain_callback(update, context))
        return q, bot

    def test_owner_stop_acks_and_confirms(self):
        stop_cb = dn._chain_markup(42)["inline_keyboard"][0][0]["callback_data"]
        q, bot = self._run(stop_cb, a.ALLOWED_USER_ID)
        self.assertEqual(len(q.answers), 1)             # мгновенный ACK ровно один
        self.assertTrue(q.answers[0][0])                # непустой текст тоста
        self.assertEqual(self._calls, [("stop", "42")]) # действие ушло в демон
        self.assertEqual(len(bot.sent), 1)              # подтверждение сообщением
        self.assertIn("OK:stop:42", bot.sent[0][1])

    def test_owner_status_forum_thread_preserved(self):
        status_cb = dn._chain_markup(9)["inline_keyboard"][0][1]["callback_data"]
        q, bot = self._run(status_cb, a.ALLOWED_USER_ID, thread=205)
        self.assertEqual(self._calls, [("status", "9")])
        self.assertEqual(bot.sent[0][2].get("message_thread_id"), 205)  # ответ в ту же тему

    def test_owner_stale_card_answers_and_explains_no_action(self):
        q, bot = self._run("chain:kill:1", a.ALLOWED_USER_ID)
        self.assertEqual(len(q.answers), 1)
        self.assertIn("устарела", q.answers[0][0])
        self.assertTrue(q.answers[0][1])                # show_alert
        self.assertEqual(self._calls, [])               # НИКАКОГО действия
        self.assertEqual(len(bot.sent), 1)              # но честное пояснение прислали
        self.assertIn("устаре", bot.sent[0][1].lower())

    def test_stranger_denied_no_action_no_message(self):
        stop_cb = dn._chain_markup(42)["inline_keyboard"][0][0]["callback_data"]
        q, bot = self._run(stop_cb, a.ALLOWED_USER_ID + 1)
        self.assertEqual(len(q.answers), 1)
        self.assertIn("нет прав", q.answers[0][0])
        self.assertEqual(self._calls, [])               # действие не исполнено
        self.assertEqual(bot.sent, [])                  # чужому в чат ничего не шлём

    def test_answer_failure_still_sends_visible_reply(self):
        # протухший query: answerCallbackQuery бросает («too old») — но видимый ответ всё равно есть
        stop_cb = dn._chain_markup(5)["inline_keyboard"][0][0]["callback_data"]
        q = _FakeQuery(stop_cb, a.ALLOWED_USER_ID)

        async def _boom(text=None, show_alert=False):
            raise RuntimeError("query is too old")
        q.answer = _boom
        bot = _FakeBot()
        update = types.SimpleNamespace(callback_query=q)
        context = types.SimpleNamespace(bot=bot)
        asyncio.run(a.on_chain_callback(update, context))
        self.assertEqual(self._calls, [("stop", "5")])  # действие исполнено несмотря на провал тоста
        self.assertEqual(len(bot.sent), 1)              # и видимое сообщение отправлено
        self.assertIn("OK:stop:5", bot.sent[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
