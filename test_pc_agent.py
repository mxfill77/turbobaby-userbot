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
import shutil
import asyncio
import tempfile
import subprocess
import unittest
from unittest import mock
from pathlib import Path

import pc_agent as a
import dispatch_notify as dn
import shtab_box_signals as sig    # ПРОИЗВОДИТЕЛЬ кнопки снятия остановки ящика


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


class TestGateCallback(unittest.TestCase):
    """КНОПКИ ВОРОТ КЛИЕНТСКОГО КОНТУРА (05.09.2026). Повод — жалоба владельца «я в этой группе
    вообще ничего нажать не могу»: карточка ворот показывалась в теме-инбоксе, а слово ответа
    читалось только из текста задачи очереди. Кнопка закрывает разрыв, НЕ трогая ворота: тап
    подставляет ТО ЖЕ слово в ТОТ ЖЕ разбор. Данные берём РЕАЛЬНЫЕ — из dn._gate_markup."""

    OWNER = a.ALLOWED_USER_ID
    STRANGER = a.ALLOWED_USER_ID + 1

    def _real_cb(self, commit):
        row = dn._gate_markup(commit)["inline_keyboard"][0]
        return row[0]["callback_data"], row[1]["callback_data"]      # (да, нет)

    def test_real_callback_data_shape(self):
        yes, no = self._real_cb("4aadeb0")
        self.assertEqual((yes, no), ("gate:yes:4aadeb0", "gate:no:4aadeb0"))
        self.assertLessEqual(max(len(x.encode()) for x in self._real_cb("f" * 40)), 64,
                             "callback_data не влезает в лимит Telegram")

    def test_owner_yes_routes_to_gate_action(self):
        yes, _ = self._real_cb("4aadeb0")
        r = a._chain_cb_route(yes, self.OWNER)
        self.assertEqual((r["ok"], r["kind"], r["action"], r["pid"]),
                         (True, "gate", "yes", "4aadeb0"))
        self.assertTrue(r["answer"])

    def test_owner_no_routes_to_gate_action(self):
        _, no = self._real_cb("4aadeb0")
        r = a._chain_cb_route(no, self.OWNER)
        self.assertEqual((r["ok"], r["kind"], r["action"]), (True, "gate", "no"))
        self.assertIn("ничего не применяю", r["answer"].lower(),
                      "тост «нет» обязан честно сказать, что не применяет")

    def test_tost_da_ne_obeschaet_vykatku(self):
        """«да» ЗАПИСЫВАЕТ основание, применение идёт штатной реконсиляцией. Тост «выкатываю»
        был бы враньём о вердикте — проверяем, что его нет."""
        yes, _ = self._real_cb("4aadeb0")
        self.assertNotIn("выкатываю", a._chain_cb_route(yes, self.OWNER)["answer"].lower())

    # ── ОТРИЦАТЕЛЬНЫЙ: тап НЕ ОТТУДА / не тем — одобрением не становится и не теряется молча ──
    def test_stranger_gate_tap_ne_ispolnyaetsya(self):
        yes, _ = self._real_cb("4aadeb0")
        r = a._chain_cb_route(yes, self.STRANGER)
        self.assertFalse(r["ok"])
        self.assertIn("нет прав", r["answer"])
        self.assertIsNone(r["action"], "чужой тап получил действие")

    def test_bityi_gate_tap_nazyvaet_slovesnyi_adres(self):
        """Тап по НЕразобранной кнопке ворот — ответ владельца, не ставший одобрением. Молча
        потерять его нельзя: в пояснении обязан быть словесный адрес ответа."""
        for bad in ("gate:maybe:abc", "gate:yes:", "gate:yes:" + "z" * 60, "gate:yes"):
            r = a._chain_cb_route(bad, self.OWNER)
            self.assertFalse(r["ok"], bad)
            self.assertIn("PC-дев", r["note"] or "", bad)
            self.assertIn("задача: выкати", r["note"] or "", bad)

    def test_gate_cli_beret_slovo_iz_zakrytoi_tablicy(self):
        """Из Telegram в командную строку не уезжает НИЧЕГО: слово — один из двух литералов."""
        self.assertEqual(a.GATE_WORDS, {"yes": "выкати", "no": "не выкатывай"})
        self.assertIn("не понял кнопку", a._gate_cli("выкати; rm -rf /", "abc1234"))


class TestGateCallbackHandlerGolden(unittest.TestCase):
    """Сквозной голден: тап по кнопке ворот → ACK + видимое сообщение, действие роутится в
    ПРАВИЛЬНЫЙ исполнитель (_gate_cli), а не в _chain_cli («стоп цепи» на воротах)."""

    def setUp(self):
        self._save = (a._gate_cli, a._chain_cli, a._zayavka_cli)
        self.gate, self.chain = [], []
        a._gate_cli = lambda action, c: self.gate.append((action, c)) or f"GATE:{action}:{c}"
        a._chain_cli = lambda action, pid: self.chain.append((action, pid)) or "CHAIN"
        a._zayavka_cli = lambda action, tid: "ZAYAVKA"

    def tearDown(self):
        a._gate_cli, a._chain_cli, a._zayavka_cli = self._save

    def _run(self, data, uid):
        q, bot = _FakeQuery(data, uid), _FakeBot()
        asyncio.run(a.on_chain_callback(types.SimpleNamespace(callback_query=q),
                                        types.SimpleNamespace(bot=bot)))
        return q, bot

    def test_tap_da_uhodit_v_gate_cli(self):
        yes = dn._gate_markup("4aadeb0")["inline_keyboard"][0][0]["callback_data"]
        q, bot = self._run(yes, a.ALLOWED_USER_ID)
        self.assertEqual(len(q.answers), 1)
        self.assertEqual(self.gate, [("yes", "4aadeb0")])
        self.assertEqual(self.chain, [], "ворота уехали в исполнитель ЦЕПЕЙ")
        self.assertIn("GATE:yes:4aadeb0", bot.sent[0][1])

    def test_tap_net_uhodit_v_gate_cli(self):
        no = dn._gate_markup("4aadeb0")["inline_keyboard"][0][1]["callback_data"]
        _q, bot = self._run(no, a.ALLOWED_USER_ID)
        self.assertEqual(self.gate, [("no", "4aadeb0")])
        self.assertIn("GATE:no:4aadeb0", bot.sent[0][1])

    def test_chuzhoi_tap_nichego_ne_ispolnyaet(self):
        yes = dn._gate_markup("4aadeb0")["inline_keyboard"][0][0]["callback_data"]
        q, bot = self._run(yes, a.ALLOWED_USER_ID + 1)
        self.assertEqual(self.gate, [])
        self.assertEqual(bot.sent, [])
        self.assertIn("нет прав", q.answers[0][0])

    def test_knopki_cepei_ne_slomany(self):
        """Замок: новая ветка не перехватила чужие кнопки."""
        stop = dn._chain_markup(42)["inline_keyboard"][0][0]["callback_data"]
        self._run(stop, a.ALLOWED_USER_ID)
        self.assertEqual((self.chain, self.gate), ([("stop", "42")], []))


class TestBoxReleaseCallback(unittest.TestCase):
    """СНЯТИЕ СИГНАЛЬНОЙ ОСТАНОВКИ ЯЩИКА ШТАБА ОТВЕТОМ В TELEGRAM (06.09.2026).

    Повод замерен по коду: остановка ящика не выходила наружу ни одной веткой, а снять
    её можно было ТОЛЬКО строкой `[[ЯЩИК СНЯТЬ метка=…]]` в узле мозга — то есть не с
    телефона. Кнопка закрывает разрыв, НЕ трогая сам ящик: тап зовёт ту же дверь, что и
    слово (`shtab_box_run --free`), и снимает РОВНО один записанный случай.

    ДАННЫЕ БЕРЁМ РЕАЛЬНЫЕ — из `shtab_box_signals.release_buttons`, то есть у того, кто
    кнопку и печатает. Набери мы `callback_data` здесь литералом, две формы разъехались
    бы молча на первой же правке производителя — ровно так проверяются кнопки цепей,
    заявок и ворот.
    """

    OWNER = a.ALLOWED_USER_ID
    STRANGER = a.ALLOWED_USER_ID + 1
    MARK = "e0140bfc78dc"          # живая метка полосы (сигнал Б, случай 05.09)

    def _real_cb(self, mark=None):
        row = sig.release_buttons(mark or self.MARK)["inline_keyboard"][0]
        return row[0]["callback_data"]

    def test_real_callback_data_shape(self):
        self.assertEqual(self._real_cb(), "box:free:%s" % self.MARK)
        self.assertLessEqual(len(self._real_cb("f" * 32).encode()), 64,
                             "callback_data не влезает в лимит Telegram")

    def test_owner_tap_routes_to_the_box_release(self):
        r = a._chain_cb_route(self._real_cb(), self.OWNER)
        self.assertEqual((r["ok"], r["kind"], r["action"], r["pid"]),
                         (True, "box", "free", self.MARK))
        self.assertTrue(r["answer"])

    def test_tost_ne_obeschaet_nemedlennogo_vzyatiya(self):
        """Взятие произойдёт следующим витком ящика (до 30 минут). Тост «ящик поехал»
        был бы враньём о сроке — проверяем, что его нет."""
        answer = a._chain_cb_route(self._real_cb(), self.OWNER)["answer"].lower()
        self.assertIn("снимаю", answer)
        self.assertNotIn("взял", answer)

    # ── КОНТРФАКТ ЗАДАНИЯ: ЧУЖОЙ ОТВЕТ НЕ ПРОХОДИТ ───────────────────────────────
    def test_stranger_tap_ne_snimaet_nichego(self):
        """«Снять может ТОЛЬКО владелец» — и отказ ВНЯТНЫЙ, а не тишина."""
        r = a._chain_cb_route(self._real_cb(), self.STRANGER)
        self.assertFalse(r["ok"])
        self.assertIn("нет прав", r["answer"])
        self.assertTrue(r["alert"], "чужому отказали тостом, который он может не заметить")
        self.assertIsNone(r["action"], "чужой тап получил действие")

    def test_bityi_tap_nazyvaet_slovesnyi_adres(self):
        """Тап по неразобранной кнопке — ответ владельца, не ставший снятием. Молча
        потерять его нельзя: в пояснении обязан быть словесный адрес ответа."""
        for bad in ("box:free:", "box:free:ЖЖЖ", "box:free:" + "f" * 40, "box:hold:aa11bb22"):
            r = a._chain_cb_route(bad, self.OWNER)
            self.assertFalse(r["ok"], bad)
            self.assertIn("ящик снять", r["note"] or "", bad)

    def test_cli_ne_puskaet_chuzhoe_v_komandnuyu_stroku(self):
        """Из Telegram в argv уезжает ТОЛЬКО метка, уже просеянная регуляркой."""
        self.assertIn("не понял кнопку", a._box_cli("hold", self.MARK))
        self.assertIn("не разобрана", a._box_cli("free", "aa11; rm -rf /"))


class TestBoxReleaseWord(unittest.TestCase):
    """СЛОВЕСНЫЙ ПУТЬ — запасной, и он не декорация: живой класс 05.09 — процесс агента
    оказался СТАРШЕ кнопки, и тап владельца получил «карточка устарела». Форма слова та
    же, что печатает извещение, и сверяем мы её С ИЗВЕЩЕНИЕМ, а не с литералом."""

    MARK = "e0140bfc78dc"

    def test_the_word_form_is_the_one_the_notice_prints(self):
        told = sig.RELEASE_WORD % self.MARK
        self.assertEqual(self.MARK, a.box_word_mark(told),
                         "агент не понимает слово, которое сам же обещал в извещении")

    def test_the_word_is_forgiving_to_people_who_copy_it(self):
        for said in ("ящик снять %s" % self.MARK, "ЯЩИК СНЯТЬ %s" % self.MARK,
                     "ящик: снять   %s" % self.MARK, "box free %s" % self.MARK,
                     "  ящик снять %s  " % self.MARK):
            self.assertEqual(self.MARK, a.box_word_mark(said), said)

    def test_a_foreign_phrase_is_NOT_a_release(self):
        for said in ("", "статус", "ящик", "ящик снять", "ящик снять всё",
                     "ящик снять ЖЖЖЖЖЖ", "снять %s" % self.MARK):
            self.assertEqual("", a.box_word_mark(said), said)

    def test_the_command_is_listed_in_the_help(self):
        """Перечень команд КАНОНИЧЕСКИЙ: команда, которой нет в подсказке, не существует
        для владельца — он о ней не узнает ниоткуда."""
        self.assertTrue(any("ящик снять" in c for c in a.KNOWN_COMMANDS))


class _FakeMsg:
    def __init__(self, text, thread=a.HQ_THREAD_ID):
        self.text = text
        self.message_thread_id = thread


class TestBoxReleaseWordHandlerGolden(unittest.IsolatedAsyncioTestCase):
    """Сквозной голден словесного пути: кто вправе — снимает, кто не вправе — получает
    ВНЯТНЫЙ отказ (условие задания дословно: «не тишина»)."""

    MARK = "e0140bfc78dc"

    def setUp(self):
        self._save = a._box_cli
        self.calls, self.sent = [], []
        a._box_cli = lambda action, mark: self.calls.append((action, mark)) or "BOX:OK"

        async def _send(context, chat_id, text, **kw):
            self.sent.append(text)
        self._save_send = a._send
        a._send = _send

    def tearDown(self):
        a._box_cli, a._send = self._save, self._save_send

    async def _say(self, text, uid):
        update = types.SimpleNamespace(
            effective_message=_FakeMsg(text),
            effective_chat=types.SimpleNamespace(id=a.HQ_CHAT_ID),
            effective_user=types.SimpleNamespace(id=uid))
        await a.on_message(update, types.SimpleNamespace(bot=_FakeBot()))

    async def test_owner_word_frees_the_case(self):
        await self._say(sig.RELEASE_WORD % self.MARK, a.ALLOWED_USER_ID)
        self.assertEqual([("free", self.MARK)], self.calls)
        self.assertEqual(["BOX:OK"], self.sent)

    async def test_stranger_word_frees_nothing_and_is_answered(self):
        await self._say(sig.RELEASE_WORD % self.MARK, a.ALLOWED_USER_ID + 1)
        self.assertEqual([], self.calls, "чужое слово сняло остановку")
        self.assertEqual(1, len(self.sent), "чужому ответили тишиной")
        self.assertIn("Нет прав", self.sent[0])

    async def test_a_broken_mark_is_answered_by_the_MARK_and_not_by_the_help(self):
        """Общая подсказка «не знаю такой команды» здесь врёт: команду мы знаем, не
        разобралась МЕТКА. Свести это к «не знаю» значило бы потерять ответ человека."""
        await self._say("ящик снять всё", a.ALLOWED_USER_ID)
        self.assertEqual([], self.calls)
        self.assertIn("Метку не разобрал", self.sent[0])

    async def test_a_stranger_saying_anything_else_still_gets_silence(self):
        """Молчание чужому осталось правилом: болтливее агент не стал."""
        await self._say("статус", a.ALLOWED_USER_ID + 1)
        self.assertEqual([], self.sent)


class TestBoxReleaseHandlerGolden(unittest.TestCase):
    """Сквозной голден кнопки: тап → ACK + видимое сообщение, действие уходит в ПРАВИЛЬНЫЙ
    исполнитель (_box_cli), а не в чужой («стоп цепи» на остановке ящика)."""

    MARK = "e0140bfc78dc"

    def setUp(self):
        self._save = (a._box_cli, a._chain_cli, a._gate_cli, a._zayavka_cli)
        self.box, self.chain, self.gate = [], [], []
        a._box_cli = lambda action, m: self.box.append((action, m)) or f"BOX:{action}:{m}"
        a._chain_cli = lambda action, pid: self.chain.append((action, pid)) or "CHAIN"
        a._gate_cli = lambda action, c: self.gate.append((action, c)) or "GATE"
        a._zayavka_cli = lambda action, tid: "ZAYAVKA"

    def tearDown(self):
        a._box_cli, a._chain_cli, a._gate_cli, a._zayavka_cli = self._save

    def _run(self, data, uid):
        q, bot = _FakeQuery(data, uid), _FakeBot()
        asyncio.run(a.on_chain_callback(types.SimpleNamespace(callback_query=q),
                                        types.SimpleNamespace(bot=bot)))
        return q, bot

    def test_tap_uhodit_v_box_cli(self):
        cb = sig.release_buttons(self.MARK)["inline_keyboard"][0][0]["callback_data"]
        q, bot = self._run(cb, a.ALLOWED_USER_ID)
        self.assertEqual(len(q.answers), 1)                       # мгновенный ACK ровно один
        self.assertEqual(self.box, [("free", self.MARK)])
        self.assertEqual((self.chain, self.gate), ([], []), "остановка уехала в чужой исполнитель")
        self.assertIn("BOX:free:%s" % self.MARK, bot.sent[0][1])

    def test_chuzhoi_tap_nichego_ne_snimaet(self):
        cb = sig.release_buttons(self.MARK)["inline_keyboard"][0][0]["callback_data"]
        q, bot = self._run(cb, a.ALLOWED_USER_ID + 1)
        self.assertEqual(self.box, [])
        self.assertEqual(bot.sent, [], "чужому в чат ничего не шлём")
        self.assertIn("нет прав", q.answers[0][0])

    def test_chuzhie_knopki_ne_slomany(self):
        """Замок: новая ветка не перехватила кнопки цепей, ворот и заявок."""
        self._run(dn._chain_markup(42)["inline_keyboard"][0][0]["callback_data"],
                  a.ALLOWED_USER_ID)
        self._run(dn._gate_markup("4aadeb0")["inline_keyboard"][0][0]["callback_data"],
                  a.ALLOWED_USER_ID)
        self.assertEqual((self.chain, self.gate, self.box),
                         ([("stop", "42")], [("yes", "4aadeb0")], []))


class _FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid

    def poll(self):
        return None


class TestSafeModeAtBothRaisePlaces(unittest.TestCase):
    """ЗАМОК БЕЗОПАСНОГО РЕЖИМА (09.09.2026) — ОБА места подъёма, а не одно.

    Реальные боты НЕ поднимаются: `subprocess.Popen` подменён целиком, PID-искатели тоже.
    Стережём ровно две вещи, и обе — отрицательные:
      • на НЕОДОБРЕННОМ коммите ребёнок ПОДНЯТ (Popen состоялся) И флаг выставлен И причина названа;
      • на ОДОБРЕННОМ коммите замок в окружение не лезет вовсе (`env=None` = наследование, как было).
    """

    def setUp(self):
        import deploy_voice as dv
        self.dv = dv
        self.tmp = tempfile.mkdtemp(prefix="pa_safe_")
        self._logs, self._popen = a.LOGS_DIR, a.subprocess.Popen
        self._sleep, self._ub, self._mb = a.time.sleep, a._find_userbot_pids, a._find_moderbot_pids
        self._decide, self._say = dv.safe_mode_decision, dv.announce_safe_mode
        a.LOGS_DIR = Path(self.tmp)
        a.time.sleep = lambda *_a, **_k: None
        a.subprocess.Popen = self._fake_popen
        a._find_userbot_pids = self._pids
        a._find_moderbot_pids = self._pids
        dv.announce_safe_mode = self._fake_say
        self._reset()

    def _fake_say(self, kind, door, d, **k):
        """Повторяет КОНТРАКТ настоящего крика: на неопасном вердикте он молчит законно (голден
        этого молчания живёт в test_deploy_voice). Настоящий звать нельзя — он спавнит процессы."""
        if d is None or not getattr(d, "safe", False):
            return ""
        self.said.append((kind, d))
        return "сказано"

    def _reset(self):
        """Состояние ОДНОГО прогона. Патчи ставит setUp один раз: повторный его вызов из цикла
        запомнил бы уже подменённые функции как «оригинал» и утёк бы патчами наружу."""
        self.calls, self.said, self._seen = [], [], 0

    def tearDown(self):
        a.LOGS_DIR, a.subprocess.Popen = self._logs, self._popen
        a.time.sleep, a._find_userbot_pids, a._find_moderbot_pids = self._sleep, self._ub, self._mb
        self.dv.safe_mode_decision, self.dv.announce_safe_mode = self._decide, self._say
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_popen(self, argv, **kw):
        self.calls.append(kw)
        try:
            kw["stdout"].close()          # ручку лога ребёнка настоящий Popen забрал бы себе
        except Exception:
            pass
        return _FakeProc()

    def _pids(self):
        """До запуска — пусто (иначе start() откажется), после — один живой PID."""
        self._seen += 1
        return [] if self._seen == 1 else [4242]

    def _verdict(self, safe, outcome, commit="f3e50ee21"):
        d = self.dv.SafeMode(safe, outcome, commit, "причина для теста")
        self.dv.safe_mode_decision = lambda **k: d
        return d

    def _start(self, kind):
        self._reset()
        if kind == "userbot":
            return a.UserbotProcess().start()
        with mock.patch.dict(os.environ, {"MODERBOT_TOKEN": "x"}):
            return a.ModerbotProcess().start()

    def test_unapproved_commit_raises_the_child_and_mutes_it(self):
        for kind in ("userbot", "moderbot"):
            with self.subTest(kind=kind):
                d = self._verdict(True, self.dv.NOT_APPROVED)
                msg = self._start(kind)
                self.assertEqual(len(self.calls), 1, "ребёнок НЕ поднят — замок закрыл дверь")
                env = self.calls[0].get("env")
                self.assertIsNotNone(env, "флаг безопасного режима не доехал до ребёнка")
                self.assertEqual(env[self.dv.SAFE_MODE_ENV], self.dv.SAFE_MODE_ON)
                self.assertIn("PID", msg)
                self.assertEqual([s[1] for s in self.said], [d], "причина не названа вслух")

    def test_approved_commit_leaves_the_environment_alone(self):
        for kind in ("userbot", "moderbot"):
            with self.subTest(kind=kind):
                self._verdict(False, self.dv.APPROVED)
                self._start(kind)
                self.assertEqual(len(self.calls), 1)
                self.assertIsNone(self.calls[0].get("env"),
                                  "замок тронул окружение на ОДОБРЕННОМ коммите")
                self.assertEqual(self.said, [], "на одобренном коммите крика быть не должно")

    def test_broken_lock_still_raises_the_child_and_still_mutes_it(self):
        """Отказ самого замка не имеет права ни отменить подъём, ни выдать право писать клиенту."""
        def _boom(**k):
            raise RuntimeError("BOOM")
        for kind in ("userbot", "moderbot"):
            with self.subTest(kind=kind):
                self.dv.safe_mode_decision = _boom
                self._start(kind)
                self.assertEqual(len(self.calls), 1, "сломанный замок отменил подъём")
                env = self.calls[0].get("env")
                self.assertEqual(env[self.dv.SAFE_MODE_ENV], self.dv.SAFE_MODE_ON)

    def test_both_raise_places_ask_the_lock(self):
        """Мест подъёма РОВНО два, и оба спрашивают замок ДО Popen — сверка по живому исходнику."""
        import ast as _ast
        with open(a.__file__, encoding="utf-8") as f:
            src = f.read()
        child = []
        for cls in _ast.walk(_ast.parse(src)):
            if not isinstance(cls, _ast.ClassDef):
                continue
            for fn in cls.body:
                if not (isinstance(fn, _ast.FunctionDef) and fn.name == "start"):
                    continue
                child += [n for n in _ast.walk(fn) if isinstance(n, _ast.Call)
                          and isinstance(n.func, _ast.Attribute) and n.func.attr == "Popen"]
        self.assertEqual(len(child), 2, "мест подъёма ребёнка стало не два: %d" % len(child))
        for n in child:
            self.assertTrue(any(k.arg == "env" for k in n.keywords),
                            "место подъёма в строке %d не передаёт окружение" % n.lineno)
        self.assertEqual(src.count("_safe_mode_gate("), 3)   # объявление + два места подъёма


if __name__ == "__main__":
    unittest.main(verbosity=2)
