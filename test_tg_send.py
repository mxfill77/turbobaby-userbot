# -*- coding: utf-8 -*-
"""
test_tg_send.py — «✅ Отправить» из «Агентов»: userbot шлёт ответ агента клиенту Telegram (13.10.2026, TGSEND1310).

Живого Telegram и сервера здесь НЕТ: клиент Telethon — двойник (`FakeTG`: get_entity из словаря настоящих объектов
`telethon.tl.types`, send_message записывает вызов), сервер — двойник очереди в памяти (`FakeServer`: те же правила,
что у `tg_outbox` сервера — queued → claimed, повторная выдача claimed, подтверждение идемпотентно) за поддельным
HTTP. Журнал — во временном каталоге (`<TEMP>/turbobaby_TESTING_tg_send/`), файлы ПЕРЕПИСЫВАЮТСЯ в setUp, а не
удаляются. Токен — выдуманная строка. Боевой userbot.log подменён рекордером, боевые файлы ленты не тронуты.

Что стережём:
  • флаг выключен — ни задачи, ни сети, ни строки (`_startup_once` не заводит задачу, лог без «TG-ОТПРАВКА»);
    включён — РОВНО одна задача на процесс (повторная инициализация второй не заводит);
  • адрес и токен — те же формулы, что у пушера ленты (матрица окружений против `tg_feed_push`);
  • отправка: только личный чат живого пользователя (не бот, не группа/канал, не свой аккаунт, не удалённый),
    текст дословно (parse_mode=None), подтверждение «sent» с msg_id;
  • двойной отправки нет: подтверждение потеряно — сервер выдаёт ряд снова — второй отправки нет, ack из журнала;
    процесс оборвался после записи «sending» — повтора нет, ack «unknown»; ответ GET потерян — ряд приходит снова
    и уходит ОДИН раз; журнал не пишется — не шлём;
  • исходы ошибок Telegram: 4xx/420 — failed, 5xx/-503 и сеть — unknown; срок сервера на исходе — не шлём;
  • токен не печатается; редирект не идёт.
"""

import test_isolation  # noqa: F401 — TESTING=1, боевой IPC заблокирован
import asyncio         # noqa: E402
import http.server     # noqa: E402
import io              # noqa: E402
import json            # noqa: E402
import os              # noqa: E402
import tempfile        # noqa: E402
import threading       # noqa: E402
import types           # noqa: E402
import unittest        # noqa: E402
from unittest import mock  # noqa: E402

from telethon import errors as tl_errors  # noqa: E402
from telethon.tl import types as T  # noqa: E402

import tg_feed_push    # noqa: E402
import tg_send as S    # noqa: E402
import userbot_listen as ub  # noqa: E402
import test_userbot_reconnect as R  # noqa: E402

TMP = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_tg_send")
TOKEN = "tgsend-vydumannyj-token-1310-PC"
ENV = {"TG_FEED_PUSH_TOKEN": TOKEN, "WA_QUEUE_BASE": "https://server.test"}
NOW = 1_790_000_000.0

ME = T.User(id=9000, is_self=True, username="turbophuket", first_name="Turbo", access_hash=1)
CLIENT = T.User(id=1001, username="anna_test", first_name="Анна", access_hash=11)
BOT = T.User(id=4004, username="some_bot", first_name="Бот", bot=True, access_hash=41)
OWN = T.User(id=5005, username="turbophuket1", first_name="Turbo 2", access_hash=51)
GONE = T.User(id=6006, deleted=True, access_hash=61)
GROUP = T.Chat(id=7007, title="Группа", photo=T.ChatPhotoEmpty(), participants_count=3, date=None, version=1)


def run(coro):
    return asyncio.run(coro)


class FakeTG:
    """Двойник клиента Telethon: сущности из словаря, отправка записывается (или бросает `fail`)."""

    def __init__(self, entities=None, fail=None, first_id=500):
        self.entities = {1001: CLIENT, 4004: BOT, 5005: OWN, 6006: GONE, 7007: GROUP, 9000: ME}
        self.entities.update(entities or {})
        self.fail, self.next_id, self.sent, self.connected = fail, first_id, [], True

    async def get_entity(self, chat):
        v = self.entities.get(chat)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise ValueError("Could not find the input entity for PeerUser")
        return v

    async def send_message(self, entity, text, parse_mode="md"):
        self.sent.append((entity.id, text, parse_mode))
        if self.fail is not None:
            raise self.fail
        self.next_id += 1
        return types.SimpleNamespace(id=self.next_id)

    def is_connected(self):
        return self.connected


class FakeServer:
    """Двойник очереди сервера за HTTP. drop_get/drop_ack — сколько ответов «потерять» (запрос ДОШЁЛ и исполнен,
    ответ — исключение сети), как в жизни при обрыве посреди обмена."""

    def __init__(self, now=NOW):
        self.now, self.rows, self.calls = now, {}, []
        self.drop_get = self.drop_ack = 0
        self.headers = []
        self.send = "on"            # дверь сервера (пульс wa-agent): "off" — пустая выдача
        self.bogus = False          # «off» и всё равно ряды (сервер так не делает) — ПК обязан не слать

    def add(self, rid, chat=1001, text="Здравствуйте! PCX свободен, 350 ฿/сутки.", wait=20, token=None):
        self.rows[rid] = {"id": rid, "token": token or "tok%d" % rid, "chat_id": chat, "text": text,
                          "state": "queued", "created_at": self.now, "expires_at": self.now + wait, "handed": 0,
                          "msg_id": None, "error": None}
        return self.rows[rid]

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append((method, url))
        self.headers.append(dict(headers))
        if headers.get("Authorization") != "Bearer " + TOKEN:
            return 404, b"Not found"
        if method == "GET" and url.endswith(S.OUTBOX_PATH):
            items = []
            if self.send == "off" and not self.bogus:
                return 200, json.dumps({"ok": True, "send": "off", "why": "дверь закрыта", "now": self.now,
                                        "items": []}).encode()
            for r in self.rows.values():
                if r["expires_at"] > self.now and (r["state"] == "queued" or r["state"] == "claimed"):
                    r["state"] = "claimed"
                    r["handed"] += 1
                    items.append({k: r[k] for k in ("id", "token", "chat_id", "text", "created_at", "expires_at",
                                                    "handed")})
            if self.drop_get:
                self.drop_get -= 1
                raise ConnectionResetError("ответ потерян")
            return 200, json.dumps({"ok": True, "send": self.send, "now": self.now, "items": items}).encode()
        if method == "POST" and url.endswith(S.ACK_PATH):
            p = json.loads(body.decode())
            r = self.rows.get(p.get("id"))
            if r is None:
                res = {"ok": False, "error": "not_found"}
            elif r["token"] != p.get("token"):
                res = {"ok": False, "error": "token_mismatch"}
            elif r["state"] in ("sent", "failed", "unknown"):
                res = {"ok": True, "state": r["state"], "dup": True}
            else:
                r["state"], r["msg_id"], r["error"] = p["outcome"], p.get("msg_id"), p.get("error")
                res = {"ok": True, "state": r["state"], "dup": False}
            if self.drop_ack:
                self.drop_ack -= 1
                raise TimeoutError("ответ потерян")
            return 200, json.dumps(res).encode()
        return 404, b"Not found"


class _Base(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP, exist_ok=True)
        self.jpath = os.path.join(TMP, self.id().rsplit(".", 1)[-1] + ".jsonl")
        with io.open(self.jpath, "w", encoding="utf-8"):
            pass                                  # пустой журнал теста (перезапись, не удаление)
        self.rec = R.LogRec()
        self.srv = FakeServer()
        self.tg = FakeTG()

    def sender(self, journal=None, **kw):
        s = S.Sender(self.tg, ME.id, journal or S.Journal(self.jpath), env=ENV, log=self.rec, http=self.srv,
                     clock=lambda: 0.0, own_usernames=ub.OWN_USERNAMES, **kw)
        return s

    def journal_lines(self):
        with io.open(self.jpath, encoding="utf-8") as f:
            return [json.loads(x) for x in f.read().splitlines() if x.strip()]


# ─────────────────────── 1. флаг ───────────────────────

_ENV_KEYS = (S.OFF_ENV, "TG_FEED_OFF", "TG_FEED_PUSH_TOKEN", "WA_PULL_SECRET")


class TestFlag(R.Base):
    """Выключатель ОДИН — на сервере. ПК: отправитель живёт при живой ленте и токене пушера; аварийный
    TG_SEND_OFF=1 (по образцу TG_FEED_OFF). Не живёт — ни задачи, ни строки."""

    def setUp(self):
        super().setUp()
        self._saved_sender = getattr(ub, "_tg_sender", None)
        ub._tg_sender = None
        self._env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}

    def tearDown(self):
        ub._tg_sender = self._saved_sender
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        super().tearDown()

    def test_enabled_rule(self):
        tok = {"TG_FEED_PUSH_TOKEN": "t"}
        self.assertTrue(S.enabled(tok))
        self.assertTrue(S.enabled({"WA_PULL_SECRET": "p"}))
        self.assertFalse(S.enabled({}), "без токена пушера отправителю жить нечем")
        for name in (S.OFF_ENV, "TG_FEED_OFF"):
            for v in ("1", "true", "YES", " on ", "да"):
                self.assertFalse(S.enabled(dict(tok, **{name: v})), (name, v))
            for v in ("", "0", "no", "off", "2"):
                self.assertTrue(S.enabled(dict(tok, **{name: v})), (name, v))

    def test_off_words_are_the_feed_rule(self):
        import tg_feed
        for v in ("1", "true", "yes", "on", "да", "YES", " On ", "0", "", "no", "off", "нет", "2"):
            with self.subTest(v=v):
                os.environ["TG_FEED_OFF"] = v
                try:
                    self.assertEqual(tg_feed.enabled(), v.strip().lower() not in S.OFF_WORDS)
                finally:
                    os.environ.pop("TG_FEED_OFF", None)

    async def test_off_no_task_no_line(self):
        for env in ({}, {"TG_FEED_PUSH_TOKEN": "t", S.OFF_ENV: "1"}, {"TG_FEED_PUSH_TOKEN": "t", "TG_FEED_OFF": "1"}):
            with self.subTest(env=env):
                ub._startup_done, ub._tg_sender = False, None
                cl = R.FakeClient()
                made = []
                with mock.patch.dict(os.environ, env), \
                        mock.patch.object(S, "run", side_effect=lambda *a, **k: made.append(a)):
                    before = len(asyncio.all_tasks())
                    await ub._startup_once(cl)
                    after = len(asyncio.all_tasks())
                self.assertEqual((made, ub._tg_sender, after - before), ([], None, 0))
                self.assertNotIn("TG-ОТПРАВКА", self.rec.text())

    async def test_on_exactly_one_task(self):
        os.environ["TG_FEED_PUSH_TOKEN"] = "t"
        runs = []

        async def fake_run(client, me_id, log, own_usernames=(), on_sent=None):
            runs.append((client, me_id, tuple(sorted(own_usernames))))
            self.assertIs(on_sent, ub._feed_sent, "ряд ленты своей отправки не подключён")
        cl = R.FakeClient()
        with mock.patch.object(S, "run", fake_run):
            await ub._startup_once(cl)
            ub._startup_done = False                 # даже повторная инициализация — второй задачи нет
            await ub._startup_once(cl)
            await asyncio.sleep(0)
        self.assertEqual(runs, [(cl, R.FakeMe.id, tuple(sorted(ub.OWN_USERNAMES)))])
        self.assertIsNotNone(ub._tg_sender)

    def test_userbot_imports_sender_not_pusher(self):
        with io.open(ub.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("import tg_send", src)
        self.assertNotIn("tg_feed_push", src)
        with io.open(S.__file__, encoding="utf-8") as f:
            self.assertNotIn("import tg_feed_push", f.read(), "модуль отправки тянул бы пушер в память userbot")


# ─────────────────────── 2. адрес и токен — как у пушера ───────────────────────

class TestSameDoorAsPusher(unittest.TestCase):
    def test_matrix(self):
        envs = [{}, {"WA_QUEUE_BASE": "https://a.test/"}, {"TG_FEED_PUSH_URL": "https://b.test/tg-queue/push"},
                {"TG_FEED_PUSH_URL": "https://b.test/tg-queue/push/"},
                {"TG_FEED_PUSH_URL": "https://c.test/tg-queue/push", "WA_QUEUE_BASE": "https://a.test"},
                {"TG_FEED_PUSH_TOKEN": " t1 "}, {"WA_PULL_SECRET": "p1"},
                {"TG_FEED_PUSH_TOKEN": "t1", "WA_PULL_SECRET": "p1"}, {"TG_FEED_PUSH_TOKEN": "", "WA_PULL_SECRET": "p1"}]
        for e in envs:
            with self.subTest(e=e):
                self.assertEqual(S.door_token(e), tg_feed_push.door_token(e))
                self.assertEqual(S.door_base(e) + S.PUSH_PATH, tg_feed_push.door_url(e).rstrip("/"))


# ─────────────────────── 3. отправка ───────────────────────

class TestSend(_Base):
    def test_happy_path_verbatim_text_parse_mode_none(self):
        text = "Привет! *PCX* свободен_до_15-го — [цена](x) `350` ฿"
        self.srv.add(1, text=text)
        s = self.sender()
        self.assertEqual(run(s.step()), "on")
        self.assertEqual(self.tg.sent, [(1001, text, None)])
        r = self.srv.rows[1]
        self.assertEqual((r["state"], r["msg_id"]), ("sent", 501))
        states = [ln.get("state") for ln in self.journal_lines()]
        self.assertEqual(states[:2], ["sending", "sent"], "«sending» пишется ДО отправки")
        self.assertIs(self.journal_lines()[-1]["acked"], True)
        self.assertNotIn(text, open(self.jpath, encoding="utf-8").read(), "текст клиенту в журнале")
        self.assertEqual(run(s.step()), "on")                   # решённое сервер не выдаёт
        self.assertEqual(len(self.tg.sent), 1)

    def test_lost_ack_is_retried_from_journal_first(self):
        self.srv.add(1)
        s = self.sender()
        lost = [1]

        def ack_lost(method, url, body, headers, timeout):
            if method == "POST" and lost:
                lost.pop()
                raise ConnectionResetError("ack не доехал")
            return self.srv(method, url, body, headers, timeout)
        s.http = ack_lost
        run(s.step())
        self.assertEqual(self.srv.rows[1]["state"], "claimed")
        run(s.step())                                           # повтор подтверждения — ДО опроса очереди
        self.assertEqual((len(self.tg.sent), self.srv.rows[1]["state"], self.srv.rows[1]["msg_id"]), (1, "sent", 501))
        self.assertEqual(self.srv.rows[1]["handed"], 1, "ряд не выдавался снова — подтверждение ушло первым")

    def test_lost_ack_rehanded_row_is_not_sent_again(self):
        self.srv.add(1)
        s = self.sender()
        lost = [1, 1]                                           # подтверждение НЕ доезжает дважды: шаг и повтор

        def ack_lost(method, url, body, headers, timeout):
            if method == "POST" and lost:
                lost.pop()
                raise ConnectionResetError("ack не доехал")
            return self.srv(method, url, body, headers, timeout)
        s.http = ack_lost
        run(s.step())
        self.assertEqual(self.srv.rows[1]["state"], "claimed", "ack не доехал — ряд у сервера claimed")
        run(s.step())                                           # сервер выдал ряд снова
        self.assertEqual(len(self.tg.sent), 1, "ВТОРАЯ отправка после потери ack")
        self.assertEqual((self.srv.rows[1]["state"], self.srv.rows[1]["msg_id"]), ("sent", 501))
        self.assertIn("второй раз не шлю", self.rec.text())

    def test_crash_after_sending_record_no_resend_ack_unknown(self):
        j = S.Journal(self.jpath)
        self.srv.add(1)
        j.write("tok1", id=1, chat_id=1001, state=S.S_SENDING)   # прошлый процесс оборвался посреди отправки
        self.srv.rows[1]["state"] = "claimed"
        s = self.sender(journal=S.Journal(self.jpath))          # новый процесс читает журнал с диска
        run(s.step())
        self.assertEqual(self.tg.sent, [], "повтор оборванной отправки")
        self.assertEqual(self.srv.rows[1]["state"], "unknown")
        self.assertIn("оборван", self.rec.text())

    def test_lost_get_answer_row_comes_again_and_goes_once(self):
        self.srv.add(1)
        self.srv.drop_get = 1                                   # сервер выдал (claimed), ответ GET потерян
        s = self.sender()
        run(s.step())
        self.assertEqual((self.tg.sent, self.srv.rows[1]["state"]), ([], "claimed"))
        run(s.step())                                           # повторная выдача — первая для ПК
        self.assertEqual(len(self.tg.sent), 1)
        self.assertEqual(self.srv.rows[1]["state"], "sent")

    def test_refusals_never_call_send(self):
        cases = {2: (4004, "бот"), 3: (7007, "не личный"), 4: (9000, "самим собой"), 5: (5005, "свой аккаунт"),
                 6: (6006, "удалён"), 7: (-1001234, "не личный"), 8: (8888, "не определён")}
        for rid, (chat, _) in cases.items():
            self.srv.add(rid, chat=chat)
        self.srv.add(9, text="   ")
        self.srv.add(10, text="я" * 4097)
        s = self.sender()
        run(s.step())
        self.assertEqual(self.tg.sent, [])
        for rid, (chat, word) in cases.items():
            r = self.srv.rows[rid]
            self.assertEqual(r["state"], "failed", rid)
            self.assertIn(word, r["error"], (rid, r["error"]))
        self.assertIn("пустой", self.srv.rows[9]["error"])
        self.assertIn("4096", self.srv.rows[10]["error"])

    def test_telegram_errors_outcomes(self):
        cases = [(tl_errors.UserIsBlockedError(request=None), "failed"),
                 (tl_errors.FloodWaitError(request=None, capture=30), "failed"),
                 (tl_errors.PeerIdInvalidError(request=None), "failed"),
                 (tl_errors.RpcCallFailError(request=None), "unknown"),
                 (tl_errors.RPCError(None, "Timeout", -503), "unknown"),
                 (tl_errors.ServerError(None, "No workers running"), "unknown"),
                 (tl_errors.RandomIdDuplicateError(request=None), "unknown"),     # прошлая попытка, вероятно, дошла
                 (tl_errors.UserPrivacyRestrictedError(request=None), "failed"),
                 (tl_errors.InputUserDeactivatedError(request=None), "failed"),
                 (ConnectionError("Connection to Telegram failed"), "unknown"),
                 (ConnectionError("Cannot send requests while disconnected"), "failed"),   # до сети
                 (asyncio.TimeoutError(), "unknown"),
                 (ValueError("Could not find the input entity for PeerUser(user_id=1001)"), "failed"),
                 (ValueError("The message cannot be empty unless a file is provided"), "failed"),
                 # Telethon 1.43 при raise_last_call_error=False: исчерпанные повторы 5xx/таймаута — ValueError
                 (ValueError("Request was unsuccessful 6 time(s)"), "unknown")]
        for i, (err, want) in enumerate(cases, start=1):
            with self.subTest(err=type(err).__name__):
                self.srv = FakeServer()
                self.srv.add(i)
                self.tg = FakeTG(fail=err)
                with io.open(self.jpath, "w", encoding="utf-8"):
                    pass
                run(self.sender().step())
                self.assertEqual(len(self.tg.sent), 1)
                self.assertEqual(self.srv.rows[i]["state"], want, self.srv.rows[i]["error"])

    def test_server_deadline_near_not_sent(self):
        self.srv.add(1, wait=1.5)                               # до срока меньше MARGIN_S
        run(self.sender().step())
        self.assertEqual(self.tg.sent, [])
        self.assertIn("срок сервера", self.srv.rows[1]["error"])

    def test_journal_unwritable_not_sent(self):
        os.makedirs(os.path.join(TMP, "dir_as_journal"), exist_ok=True)
        j = S.Journal(os.path.join(TMP, "dir_as_journal"))      # путь — каталог: запись падает
        self.srv.add(1)
        s = S.Sender(self.tg, ME.id, j, env=ENV, log=self.rec, http=self.srv, clock=lambda: 0.0)
        run(s.step())
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(self.srv.rows[1]["state"], "failed")
        self.assertIn("журнал", self.srv.rows[1]["error"])

    def test_token_never_printed_and_sent_only_as_bearer(self):
        self.srv.add(1)

        def boom(method, url, body, headers, timeout):
            raise RuntimeError("упало с " + TOKEN)
        s = self.sender()
        s.http = boom
        run(s.step())
        self.assertIn("очередь не прочитана (RuntimeError)", self.rec.text())
        self.assertNotIn(TOKEN, self.rec.text())
        self.assertEqual(s._safe("x " + TOKEN), "x <токен>")
        s2 = self.sender()
        run(s2.step())
        self.assertTrue(all(h.get("Authorization") == "Bearer " + TOKEN for h in self.srv.headers))
        self.assertTrue(all(TOKEN not in url for _, url in self.srv.calls))

    def test_wrong_token_server_404_nothing_sent(self):
        self.srv.add(1)
        s = S.Sender(self.tg, ME.id, S.Journal(self.jpath), env={"TG_FEED_PUSH_TOKEN": "chuzhoj"}, log=self.rec,
                     http=self.srv, clock=lambda: 0.0)
        run(s.step())
        self.assertEqual((self.tg.sent, self.srv.rows[1]["state"]), ([], "queued"))
        self.assertIn("ответ 404", self.rec.text())


class TestHaltCancelOnSent(_Base):
    """Факты Telethon (b6v_telethon/TELETHON_FACTS.md): стоп-кран после FloodWait/PeerFlood (§6.5), отмена изнутри
    при отключении клиента (§4), свой ряд ленты (§2 — на СВОЮ отправку NewMessage(outgoing) обычно нет)."""

    def setUp(self):
        super().setUp()
        self.t = [0.0]

    def sender(self, journal=None, **kw):
        return S.Sender(self.tg, ME.id, journal or S.Journal(self.jpath), env=ENV, log=self.rec, http=self.srv,
                        clock=lambda: self.t[0], own_usernames=ub.OWN_USERNAMES, **kw)

    def test_flood_wait_halts_until_it_passes(self):
        self.srv.add(1)
        self.srv.add(2)
        self.tg.fail = tl_errors.FloodWaitError(request=None, capture=30)
        s = self.sender()
        self.assertEqual(run(s.step()), "on")
        self.assertEqual(len(self.tg.sent), 1, "после FloodWait второй ряд пачки отправлять нельзя")
        self.assertEqual([self.srv.rows[i]["state"] for i in (1, 2)], ["failed", "failed"])
        self.assertIn("стоп-кран", self.srv.rows[2]["error"])
        self.srv.add(3)
        calls = len(self.srv.calls)
        self.assertEqual(run(s.step()), "halt")
        self.assertEqual([m for m, _ in self.srv.calls[calls:]], [], "при стопе ряды не забираем")
        self.assertEqual(self.srv.rows[3]["state"], "queued")
        self.t[0] = 31.0
        self.tg.fail = None
        self.assertEqual(run(s.step()), "on")
        self.assertEqual(self.srv.rows[3]["state"], "sent")
        self.assertIn("СТОП", self.rec.text("ERROR"))
        self.assertIn("стоп снят", self.rec.text("INFO"))

    def test_peer_flood_halts_until_restart(self):
        self.srv.add(1)
        self.tg.fail = tl_errors.PeerFloodError(request=None)
        s = self.sender()
        run(s.step())
        self.t[0] = 10 ** 9
        self.assertEqual(run(s.step()), "halt")

    def test_cancel_from_inside_telethon_is_unknown_and_task_lives(self):
        self.srv.add(1)
        self.tg.fail = asyncio.CancelledError()            # client.disconnect() отменил висящий запрос
        s = self.sender()
        self.assertEqual(run(s.step()), "on")
        self.assertEqual(self.srv.rows[1]["state"], "unknown")
        self.assertIn("отключением клиента", self.srv.rows[1]["error"])

    def test_own_cancel_is_not_swallowed(self):
        self.srv.add(1)
        s = self.sender()

        async def slow_send(entity, text, parse_mode="md"):
            await asyncio.sleep(10)
        self.tg.send_message = slow_send

        async def main():
            task = asyncio.create_task(s.step())
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        run(main())
        self.assertEqual(S.Journal(self.jpath).get("tok1")["state"], S.S_SENDING, "при старте — unknown без повтора")

    def test_on_sent_after_ack_only_for_sent(self):
        got = []
        self.srv.add(1)
        s = self.sender(on_sent=lambda msg, chat, ent: got.append((msg.id, chat, ent.id, self.srv.rows[1]["state"])))
        run(s.step())
        self.assertEqual(got, [(501, 1001, 1001, "sent")], "ряд ленты — после подтверждения и только у ушедшего")
        self.srv = FakeServer()
        self.srv.add(2)
        self.tg.fail = tl_errors.UserIsBlockedError(request=None)
        got.clear()
        run(self.sender(on_sent=lambda *a: got.append(a)).step())
        self.assertEqual(got, [])

    def test_on_sent_failure_does_not_break_sending(self):
        self.srv.add(1)

        def boom(*a):
            raise OSError(28, "No space left")
        run(self.sender(on_sent=boom).step())
        self.assertEqual(self.srv.rows[1]["state"], "sent")
        self.assertIn("ряд ленты своей отправки не записан", self.rec.text("WARNING"))


import test_tg_feed as F  # noqa: E402


class TestFeedRowOfOwnSend(F._Base):
    """userbot `_feed_sent`: ответ, ушедший отправителем, — ряд ленты dir=out теми же фильтрами, что on_outgoing."""

    def test_row_written_with_client_names(self):
        msg = F.mk_msg(777, F.CLIENT.id, out=True, text="Байк свободен, *350* ฿")
        ub._feed_sent(msg, F.CLIENT.id, F.CLIENT)
        (r,) = self.rows()
        self.assertEqual((r["chat_id"], r["msg_id"], r["dir"], r["text"], r["sender_id"]),
                         (F.CLIENT.id, 777, "out", "Байк свободен, *350* ฿", F.ME.id))
        self.assertEqual((r["username"], r["name"]), ("anna_test", "Анна"))
        self.no_telegram()

    def test_team_peer_no_row(self):
        ub._feed_sent(F.mk_msg(778, F.TEAM.id, out=True, text="x"), F.TEAM.id, F.TEAM)
        self.assertEqual(self.rows(), [])


class TestRunLoop(_Base):
    def test_no_token_no_task_body(self):
        res = run(S.run(self.tg, ME.id, self.rec, env={}))
        self.assertIsNone(res)
        self.assertIn("токена нет", self.rec.text())

    def test_loop_survives_step_errors_and_skips_when_disconnected(self):
        calls = []

        class Boom:
            base, journal = "https://x", types.SimpleNamespace(path="j")

            async def step(self):
                calls.append(1)
                raise RuntimeError("шаг упал")

            def _safe(self, s):
                return str(s)
        sleeps = []

        async def fake_sleep(sec):
            sleeps.append(sec)
            if len(sleeps) == 2:
                self.tg.connected = False
            if len(sleeps) >= 4:
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            run(S.run(self.tg, ME.id, self.rec, sender=Boom(), sleep=fake_sleep))
        self.assertEqual(len(calls), 2, "без связи шаг не идёт, со связью — идёт")
        self.assertEqual(sleeps, [5.0, 10.0, 10.0, 10.0], "сбой и нет связи — реже, потолок 10 с")
        self.assertIn("шаг упал", self.rec.text("WARNING"))

    def test_poll_schedule_follows_the_server_switch(self):
        """«on» — раз в POLL_S; «off»/ошибка — 5 → 10 с (потолок 10); «on» снова — сразу частый опрос."""
        states = ["on", "on", "off", "off", "off", "off", "error", "on", "off"]

        class Seq:
            base, journal = "https://x", types.SimpleNamespace(path="j")

            async def step(self):
                return states.pop(0)

            def _safe(self, s):
                return str(s)
        sleeps = []

        async def fake_sleep(sec):
            sleeps.append(sec)
            if not states:
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            run(S.run(self.tg, ME.id, self.rec, sender=Seq(), sleep=fake_sleep))
        self.assertEqual(sleeps, [2.5, 2.5, 5.0, 10.0, 10.0, 10.0, 10.0, 2.5, 5.0])

    def test_off_poll_ceiling_fits_the_server_door_window(self):
        """Потолок опроса вне «on» — 10 с, меньше окна двери сервера (по умолчанию 20 с, минимум 5–60): после
        рестарта службы агента сервер отвечает «off», пока не ляжет свежий пульс, и ПК обязан узнать «on» в
        пределах одного окна двери. Прежний отступ 30 → 60 с — нажатия следующей минуты не находили ПК."""
        server_door_wait_default = 20.0
        for state in ("off", "error", "halt", None, "что-то"):
            for quiet in range(0, 200):
                p = S.next_poll(state, quiet)
                self.assertLessEqual(p, 10.0, (state, quiet, p))
                self.assertLess(p, server_door_wait_default, (state, quiet, p))
                self.assertGreater(p, S.POLL_S - 0.001, "реже частого — сервер не дёргаем впустую")
        self.assertEqual(S.next_poll("on", 7), S.POLL_S)
        self.assertEqual(max(S.OFF_POLL_S), 10.0)
        self.assertEqual(S.OFF_POLL_CEIL_S, 10.0)


class TestServerSwitch(_Base):
    """Выключатель сервера: «off» — ничего не шлём (даже если сервер ошибочно выдал ряды), подтверждения
    из журнала доезжают; смена состояния — одна строка журнала, а не строка на опрос."""

    def test_off_sends_nothing_even_bogus_items(self):
        self.srv.add(1)
        self.srv.send, self.srv.bogus = "off", True
        s = self.sender()
        self.assertEqual(run(s.step()), "off")
        self.assertEqual(self.tg.sent, [])
        self.assertIn("не шлю ни одного", self.rec.text("WARNING"))

    def test_transitions_said_once(self):
        s = self.sender()
        self.srv.send = "off"
        for _ in range(3):
            self.assertEqual(run(s.step()), "off")
        self.srv.send = "on"
        self.srv.add(1)
        for _ in range(3):
            self.assertEqual(run(s.step()), "on")
        self.assertEqual(len(self.tg.sent), 1)
        lines = [t for _, t in self.rec.lines]
        self.assertEqual(sum("отправка выключена" in t for t in lines), 1, lines)
        self.assertEqual(sum("отправка ВКЛЮЧЕНА" in t for t in lines), 1, lines)

    def test_acks_flush_while_off(self):
        self.srv.add(1)
        s = self.sender()
        lost = [1]

        def ack_lost(method, url, body, headers, timeout):
            if method == "POST" and lost:
                lost.pop()
                raise ConnectionResetError("ack не доехал")
            return self.srv(method, url, body, headers, timeout)
        s.http = ack_lost
        run(s.step())                                           # ушло, подтверждение потеряно
        self.srv.send = "off"                                   # дверь сервера закрылась
        self.assertEqual(run(s.step()), "off")
        self.assertEqual((len(self.tg.sent), self.srv.rows[1]["state"]), (1, "sent"))

    def test_contract_without_send_word_is_an_error(self):
        s = self.sender()
        s.http = lambda *a: (200, json.dumps({"ok": True, "now": NOW, "items": []}).encode())
        self.assertEqual(run(s.step()), "error")


class TestNoRedirect(unittest.TestCase):
    def test_redirect_is_an_answer_not_a_follow(self):
        hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            st, _ = S.http_call("GET", "http://127.0.0.1:%d%s" % (srv.server_address[1], S.OUTBOX_PATH), None,
                                {"Authorization": "Bearer " + TOKEN}, 5)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(st, 302)
        self.assertEqual(hits, [S.OUTBOX_PATH], "редирект пройден — заголовок с токеном уехал бы дальше")


if __name__ == "__main__":
    unittest.main()
