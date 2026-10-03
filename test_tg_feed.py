# -*- coding: utf-8 -*-
"""
test_tg_feed.py — ЛЕНТА ЛИЧНЫХ СООБЩЕНИЙ КЛИЕНТОВ Telegram для «Агентов» (03.10.2026).

Основание — слово владельца 03.10 13:19 «всё как с ватсапом» и его «да» 13:59 на правку userbot;
задание Штаба 0128n-79l.0310, разведка TGAGENTMAP0310 (путь б).

Живого Telegram здесь НЕТ, и события не выдуманы руками полей: это НАСТОЯЩИЕ объекты Telethon
(`types.Message` в `events.NewMessage.Event`), связанные с клиентом-двойником ровно тем вызовом,
которым их связывает живой цикл (`_set_client`). Двойник считает каждое обращение к публичному
методу как вызов Telegram — так проверяется «ноль новых вызовов». Реальных данных нет: люди,
id и тексты выдуманы; лента пишется во временный файл, `userbot.log` подменён рекордером,
`moderation_ipc` и его база не открываются.

Что стережём:
  • ряд по контракту на входящее и исходящее ЛИЧНОЕ сообщение клиента (поля, ключ, время UTC, вид);
  • бот, свой аккаунт, команда и партнёр — ряда НЕТ; фильтр команды/партнёров — функции suggest;
  • фильтр не решил / собеседник неизвестен → ряда нет, пропуск посчитан и назван в журнале;
  • медиа без подписи → текст пустой; reply → reply_to;
  • сбой записи не роняет обработчик, suggest зовётся как прежде;
  • вызовы suggest и клиента в обработчике входящих — те же, что до правки (голден);
  • строка целиком: одна строка на ряд, fsync, оборванный хвост не склеивает ряды;
  • лента вне git; модуль ленты без сети, Telegram и SQLite;
  • main вешает хендлер исходящих на ТОТ ЖЕ клиент, второго клиента нет.
"""

import test_isolation  # noqa: F401 — TESTING=1, боевой IPC заблокирован
import ast             # noqa: E402
import asyncio         # noqa: E402
import io              # noqa: E402
import json            # noqa: E402
import os              # noqa: E402
import tempfile        # noqa: E402
import unittest        # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from unittest import mock  # noqa: E402

from telethon import events  # noqa: E402
from telethon.tl import types as T  # noqa: E402

import suggest         # noqa: E402
import tg_feed         # noqa: E402
import userbot_listen as ub  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FEED_TMP_DIR = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_tg_feed")

DATE = datetime(2026, 10, 3, 6, 30, 15, tzinfo=timezone.utc)
DATE_TS = 1791009015          # 2026-10-03 06:30:15 UTC, посчитано независимо от кода ленты

ME = T.User(id=9000, is_self=True, username="turbophuket", first_name="Turbo", access_hash=1)
CLIENT = T.User(id=1001, username="anna_test", first_name="Анна", access_hash=11)
CLIENT_NONICK = T.User(id=1002, first_name="Без ника", access_hash=12)
TEAM = T.User(id=2002, username="staff_test", first_name="Сотрудник", access_hash=21)
TEAM_BY_NICK = T.User(id=2003, username="office_test", first_name="Офис", access_hash=22)
PARTNER = T.User(id=3003, username="partner_test", first_name="Прокат", access_hash=31)
PARTNER_BY_TITLE = T.User(id=3004, first_name="Партнерка STM", access_hash=32)
BOT = T.User(id=4004, username="some_bot", first_name="Бот", bot=True, access_hash=41)
OWN = T.User(id=5005, username="turbophuket1", first_name="Turbo 2", access_hash=51)

REGISTRY = {"usernames": {"office_test"}, "user_ids": {2002}, "group_ids": set(),
            "nonclient_usernames": set(), "nonclient_user_ids": {3003},
            "nonclient_titles": {"партнерка"}}


# ───────────────────────────── двойники ─────────────────────────────

class _NoCache:
    """Кэш сущностей Telethon пуст: всё, что знает событие, — из самого апдейта."""

    def get(self, _key):
        return None


class StrictClient:
    """Клиент-двойник. Внутренние поля, которые читает связывание события, есть; ЛЮБОЙ публичный
    метод — это вызов Telegram: он записывается и падает, чтобы новый вызов не спрятался."""

    def __init__(self, self_id=ME.id):
        self._self_id = self_id
        self._mb_entity_cache = _NoCache()
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def _call(*a, **k):
            self.calls.append(name)
            raise AssertionError(f"обращение к Telegram из обработчика: {name}")
        return _call


class LogRec:
    """Двойник логгера userbot: боевой userbot.log не задет, строки проверяемы."""

    def __init__(self):
        self.lines = []

    def _add(self, level, msg, *a):
        self.lines.append((level, (msg % a) if a else str(msg)))

    def info(self, msg, *a, **kw):
        self._add("INFO", msg, *a)

    def warning(self, msg, *a, **kw):
        self._add("WARNING", msg, *a)

    def error(self, msg, *a, **kw):
        self._add("ERROR", msg, *a)

    def text(self, level=None):
        return "\n".join(t for lv, t in self.lines if level in (None, lv))


def doc(*attrs, mime="application/octet-stream"):
    return T.MessageMediaDocument(document=T.Document(
        id=77, access_hash=1, file_reference=b"", date=DATE, mime_type=mime, size=10, dc_id=1,
        attributes=list(attrs)))


PHOTO = T.MessageMediaPhoto(photo=T.Photo(id=55, access_hash=1, file_reference=b"", date=DATE,
                                          sizes=[], dc_id=1))
VOICE = doc(T.DocumentAttributeAudio(duration=3, voice=True), mime="audio/ogg")
AUDIO = doc(T.DocumentAttributeAudio(duration=200, voice=False), mime="audio/mpeg")
VIDEO = doc(T.DocumentAttributeVideo(duration=3, w=10, h=10), mime="video/mp4")
ROUND = doc(T.DocumentAttributeVideo(duration=3, w=10, h=10, round_message=True), mime="video/mp4")
GIF = doc(T.DocumentAttributeAnimated(), mime="image/gif")
STICKER = doc(T.DocumentAttributeSticker(alt="🙂", stickerset=T.InputStickerSetEmpty()),
              mime="image/webp")
VIDEO_STICKER = doc(T.DocumentAttributeSticker(alt="🙂", stickerset=T.InputStickerSetEmpty()),
                    T.DocumentAttributeVideo(duration=1, w=10, h=10), mime="video/webm")
FILE = doc(T.DocumentAttributeFilename(file_name="passport.pdf"), mime="application/pdf")
GEO = T.MessageMediaGeo(geo=T.GeoPoint(long=98.3, lat=7.8, access_hash=0))
VENUE = T.MessageMediaVenue(geo=T.GeoPoint(long=98.3, lat=7.8, access_hash=0), title="Пляж",
                            address="Раваи", provider="", venue_id="", venue_type="")
LIVE = T.MessageMediaGeoLive(geo=T.GeoPoint(long=98.3, lat=7.8, access_hash=0), period=900)
WEBPAGE = T.MessageMediaWebPage(webpage=T.WebPageEmpty(id=1))
CONTACT = T.MessageMediaContact(phone_number="", first_name="X", last_name="", vcard="", user_id=0)


def mk_msg(mid, peer_id, out=False, text="", media=None, reply_to=None, date=DATE):
    return T.Message(id=mid, peer_id=T.PeerUser(peer_id), date=date, message=text, out=out,
                     media=media,
                     reply_to=T.MessageReplyHeader(reply_to_msg_id=reply_to) if reply_to else None)


def mk_group_msg(mid, chat_id, sender_id, text=""):
    return T.Message(id=mid, peer_id=T.PeerChat(chat_id), date=DATE, message=text,
                     from_id=T.PeerUser(sender_id))


def mk_event(msg, entities, client):
    """Событие так, как его собирает живой цикл: апдейт несёт сущности → `_set_client`."""
    ev = events.NewMessage.Event(msg)
    ev._entities = {e.id: e for e in entities}
    ev._set_client(client)
    return ev


def incoming(client, sender, mid=10, **kw):
    return mk_event(mk_msg(mid, sender.id, out=False, **kw), [sender], client)


def outgoing(client, peer, mid=20, with_entity=True, **kw):
    """Исходящее в личку `peer`. with_entity=False — короткий апдейт: сущности собеседника нет."""
    ents = [ME] + ([peer] if with_entity else [])
    return mk_event(mk_msg(mid, peer.id, out=True, **kw), ents, client)


def run(coro):
    return asyncio.run(coro)


# ───────────────────────────── основа ─────────────────────────────

class _Base(unittest.TestCase):
    def setUp(self):
        os.makedirs(FEED_TMP_DIR, exist_ok=True)
        self.feed = os.path.join(FEED_TMP_DIR, f"{self.id().rsplit('.', 1)[-1]}.jsonl")
        with io.open(self.feed, "w", encoding="utf-8"):
            pass                                  # пустая лента теста (перезапись, не удаление)
        self.rec = LogRec()
        self.client = StrictClient()
        self._save = {
            "log": ub.log, "me": ub._ME_ID, "feed": tg_feed.FEED_FILE,
            "reg": suggest.TEAM_REGISTRY, "appr": suggest.APPROVER_USERNAMES,
            "intake": suggest.INTAKE_APPROVERS, "off": os.environ.pop(tg_feed.OFF_ENV, None),
        }
        ub.log = self.rec
        ub._ME_ID = ME.id
        tg_feed.FEED_FILE = self.feed
        suggest.TEAM_REGISTRY = {k: set(v) for k, v in REGISTRY.items()}
        suggest.APPROVER_USERNAMES = set()
        suggest.INTAKE_APPROVERS = set()
        ub._FEED_PEERS.clear()
        for k in ub._FEED_COUNTS:
            ub._FEED_COUNTS[k] = 0
        self.suggest_calls = []
        self._enabled = mock.patch.object(suggest, "is_enabled", return_value=False)
        self._enabled.start()

    def tearDown(self):
        self._enabled.stop()
        s = self._save
        ub.log, ub._ME_ID, tg_feed.FEED_FILE = s["log"], s["me"], s["feed"]
        suggest.TEAM_REGISTRY = s["reg"]
        suggest.APPROVER_USERNAMES, suggest.INTAKE_APPROVERS = s["appr"], s["intake"]
        if s["off"] is None:
            os.environ.pop(tg_feed.OFF_ENV, None)
        else:
            os.environ[tg_feed.OFF_ENV] = s["off"]

    def rows(self):
        with io.open(self.feed, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f.read().split("\n") if ln]

    def no_telegram(self):
        self.assertEqual(self.client.calls, [], "лента позвала Telegram")


# ─────────────────────── 1. ряд по контракту ───────────────────────

class TestRows(_Base):
    def test_incoming_client_row(self):
        run(ub.on_incoming(incoming(self.client, CLIENT, mid=10, text="Привет, байк на неделю?")))
        self.assertEqual(self.rows(), [{
            "chat_id": 1001, "msg_id": 10, "dir": "in", "ts": DATE_TS, "kind": "text",
            "text": "Привет, байк на неделю?", "reply_to": None, "sender_id": 1001}])
        self.assertEqual(list(self.rows()[0]), list(tg_feed.FIELDS))
        self.no_telegram()

    def test_incoming_client_without_username(self):
        run(ub.on_incoming(incoming(self.client, CLIENT_NONICK, mid=11, text="hi")))
        self.assertEqual([(r["chat_id"], r["sender_id"]) for r in self.rows()], [(1002, 1002)])

    def test_outgoing_client_row(self):
        run(ub.on_outgoing(outgoing(self.client, CLIENT, mid=21, text="Здравствуйте! Есть.")))
        self.assertEqual(self.rows(), [{
            "chat_id": 1001, "msg_id": 21, "dir": "out", "ts": DATE_TS, "kind": "text",
            "text": "Здравствуйте! Есть.", "reply_to": None, "sender_id": ME.id}])
        self.no_telegram()

    def test_outgoing_short_update_uses_memory_of_incoming(self):
        """Ответ с телефона чаще приходит коротким апдейтом без сущности собеседника: решаем по
        памяти о его входящем, а не спрашиваем Telegram."""
        run(ub.on_incoming(incoming(self.client, CLIENT, mid=10, text="Цена?")))
        run(ub.on_outgoing(outgoing(self.client, CLIENT, mid=11, with_entity=False, text="1200")))
        self.assertEqual([(r["dir"], r["msg_id"], r["sender_id"]) for r in self.rows()],
                         [("in", 10, 1001), ("out", 11, ME.id)])
        self.no_telegram()

    def test_outgoing_unknown_peer_is_skipped_and_counted(self):
        run(ub.on_outgoing(outgoing(self.client, CLIENT, mid=30, with_entity=False, text="a")))
        run(ub.on_outgoing(outgoing(self.client, CLIENT_NONICK, mid=31, with_entity=False,
                                    text="b")))
        self.assertEqual(self.rows(), [])
        self.assertEqual(ub._FEED_COUNTS["undecided"], 2)
        warn = self.rec.text("WARNING")
        self.assertIn("TG-ЛЕНТА: ряд НЕ записан — не решено", warn)
        self.assertIn("таких пропусков за жизнь процесса: 2", warn)
        self.no_telegram()

    def test_reply_to(self):
        run(ub.on_incoming(incoming(self.client, CLIENT, mid=40, text="а этот?", reply_to=33)))
        run(ub.on_outgoing(outgoing(self.client, CLIENT, mid=41, text="да", reply_to=40)))
        self.assertEqual([r["reply_to"] for r in self.rows()], [33, 40])

    def test_group_message_is_not_a_row(self):
        ev = mk_event(mk_group_msg(50, 777, CLIENT.id, "в группе"), [CLIENT], self.client)
        run(ub.on_incoming(ev))
        run(ub.on_outgoing(ev))
        self.assertEqual(self.rows(), [])


# ─────────────────── 2. кому ряд НЕ положен ───────────────────

class TestNotClients(_Base):
    def test_bot(self):
        run(ub.on_incoming(incoming(self.client, BOT, text="/start")))
        run(ub.on_outgoing(outgoing(self.client, BOT, text="/start")))
        self.assertEqual(self.rows(), [])
        self.no_telegram()

    def test_own_accounts(self):
        run(ub.on_incoming(incoming(self.client, OWN, text="служебное")))
        run(ub.on_outgoing(outgoing(self.client, OWN, text="служебное")))
        saved = mk_event(mk_msg(60, ME.id, out=True, text="заметка"), [ME], self.client)
        run(ub.on_outgoing(saved))
        self.assertEqual(self.rows(), [])
        self.assertEqual(ub._FEED_COUNTS["undecided"], 0)

    def test_team_by_id_and_by_username(self):
        run(ub.on_incoming(incoming(self.client, TEAM, mid=1, text="задача на завтра")))
        run(ub.on_incoming(incoming(self.client, TEAM_BY_NICK, mid=2, text="ключи у меня")))
        run(ub.on_outgoing(outgoing(self.client, TEAM, mid=3, text="ок")))
        run(ub.on_outgoing(outgoing(self.client, TEAM_BY_NICK, mid=4, with_entity=False, text="ок")))
        self.assertEqual(self.rows(), [])
        self.assertEqual(ub._FEED_COUNTS["team"], 4)

    def test_team_by_approver_list(self):
        """Команда — это и approve-аккаунты: тот же набор, что у suggest (`_team_usernames`)."""
        suggest.APPROVER_USERNAMES = {"anna_test"}
        run(ub.on_incoming(incoming(self.client, CLIENT, text="одобряю")))
        self.assertEqual(self.rows(), [])

    def test_partner_by_id_and_by_window_name(self):
        run(ub.on_incoming(incoming(self.client, PARTNER, mid=1, text="есть 2 PCX?")))
        run(ub.on_incoming(incoming(self.client, PARTNER_BY_TITLE, mid=2, text="заберёте?")))
        run(ub.on_outgoing(outgoing(self.client, PARTNER, mid=3, text="да")))
        self.assertEqual(self.rows(), [])
        self.assertEqual(ub._FEED_COUNTS["partner"], 3)

    def test_filter_cannot_decide(self):
        with mock.patch.object(suggest, "is_internal_sender", side_effect=RuntimeError("реестр")):
            run(ub.on_incoming(incoming(self.client, CLIENT, text="привет")))
        self.assertEqual(self.rows(), [])
        self.assertEqual(ub._FEED_COUNTS["undecided"], 1)
        self.assertIn("фильтр suggest: RuntimeError: реестр", self.rec.text("WARNING"))

    def test_filter_is_suggest_code_not_a_copy(self):
        """Реестр не скопирован: что знает suggest, то знает и лента — в обе стороны."""
        suggest.TEAM_REGISTRY = {"usernames": set(), "user_ids": set(), "group_ids": set()}
        run(ub.on_incoming(incoming(self.client, TEAM, mid=1, text="я теперь клиент")))
        self.assertEqual([r["chat_id"] for r in self.rows()], [TEAM.id])


# ─────────────────────── 3. вид и медиа ───────────────────────

class TestKinds(_Base):
    CASES = [
        (PHOTO, "photo"), (VOICE, "voice"), (AUDIO, "file"), (VIDEO, "video"), (ROUND, "video"),
        (GIF, "video"), (STICKER, "sticker"), (VIDEO_STICKER, "sticker"), (FILE, "file"),
        (GEO, "location"), (VENUE, "location"), (LIVE, "location"), (CONTACT, "other"),
    ]

    def test_media_without_caption(self):
        for i, (media, kind) in enumerate(self.CASES, start=100):
            run(ub.on_incoming(incoming(self.client, CLIENT, mid=i, media=media)))
        got = [(r["kind"], r["text"]) for r in self.rows()]
        self.assertEqual(got, [(k, "") for _, k in self.CASES])
        self.no_telegram()

    def test_caption_is_text(self):
        run(ub.on_incoming(incoming(self.client, CLIENT, mid=1, media=PHOTO, text="мой паспорт")))
        self.assertEqual([(r["kind"], r["text"]) for r in self.rows()], [("photo", "мой паспорт")])

    def test_link_preview_is_text(self):
        run(ub.on_incoming(incoming(self.client, CLIENT, mid=1, media=WEBPAGE,
                                    text="https://maps.example/x")))
        self.assertEqual([r["kind"] for r in self.rows()], ["text"])

    def test_every_kind_is_in_contract(self):
        for media, _ in self.CASES:
            self.assertIn(tg_feed.kind_of(mk_msg(1, CLIENT.id, media=media)), tg_feed.KINDS)
        self.assertEqual(tg_feed.kind_of(mk_msg(1, CLIENT.id, text="")), "other")


# ──────────────── 4. сбой ленты не роняет обработчик ────────────────

class TestFailure(_Base):
    def test_write_failure_keeps_handler_and_suggest(self):
        seen = []

        async def fake_ocm(client, sender, me_id):
            seen.append((sender.id, me_id))

        with mock.patch.object(suggest, "is_enabled", return_value=True), \
                mock.patch.object(suggest, "on_client_message", side_effect=fake_ocm), \
                mock.patch.object(tg_feed, "append", side_effect=OSError(28, "No space left")):
            run(ub.on_incoming(incoming(self.client, CLIENT, text="привет")))
            run(ub.on_outgoing(outgoing(self.client, CLIENT, text="ответ")))
        self.assertEqual(seen, [(CLIENT.id, ME.id)], "suggest не дождался своего вызова")
        self.assertEqual(ub._FEED_COUNTS["failed"], 2)
        warn = self.rec.text("WARNING")
        self.assertIn("TG-ЛЕНТА: ряд не записан (OSError", warn)
        self.assertIn("Обработчик продолжает", warn)

    def test_unwritable_path_is_a_journal_line(self):
        tg_feed.FEED_FILE = os.path.join(self.feed, "нельзя", "tg_feed.jsonl")  # файл как каталог
        run(ub.on_incoming(incoming(self.client, CLIENT, text="привет")))
        self.assertEqual(ub._FEED_COUNTS["failed"], 1)
        self.assertIn("TG-ЛЕНТА: ряд не записан", self.rec.text("WARNING"))

    def test_off_switch(self):
        os.environ[tg_feed.OFF_ENV] = "1"
        run(ub.on_incoming(incoming(self.client, CLIENT, text="привет")))
        run(ub.on_outgoing(outgoing(self.client, CLIENT, with_entity=False, text="ответ")))
        self.assertEqual(self.rows(), [])
        self.assertEqual(sum(ub._FEED_COUNTS.values()), 0)
        self.assertNotIn("TG-ЛЕНТА", self.rec.text())


# ──────────── 5. голден: поток suggest и вызовы клиента не изменились ────────────

def suggest_trace(module, client_factory, me_id=ME.id):
    """Прогон обработчика входящих модуля `module` по одному набору событий с включённым suggest.
    → список вызовов suggest-потока и клиента. Функция не знает про ленту: её зовёт и прогон
    по дереву main (до правки), и тест ветки — числа обязаны совпасть."""
    trace = []

    async def fake_ocm(client, sender, me):
        trace.append(("suggest.on_client_message", sender.id, me))

    def fake_enabled():
        trace.append(("suggest.is_enabled",))
        return True

    scenario = [(CLIENT, dict(text="привет")), (CLIENT_NONICK, dict(media=PHOTO)),
                (TEAM, dict(text="задача")), (PARTNER, dict(text="есть байк?")),
                (BOT, dict(text="/start")), (OWN, dict(text="служебное")),
                (CLIENT, dict(text="а этот?", reply_to=10))]
    save_me = module._ME_ID
    module._ME_ID = me_id
    try:
        with mock.patch.object(suggest, "is_enabled", side_effect=fake_enabled), \
                mock.patch.object(suggest, "on_client_message", side_effect=fake_ocm):
            for i, (who, kw) in enumerate(scenario, start=10):
                client = client_factory()
                asyncio.run(module.on_incoming(incoming(client, who, mid=i, **kw)))
                trace.extend(("client." + c,) for c in client.calls)
    finally:
        module._ME_ID = save_me
    return trace


# Снят прогоном `suggest_trace` по дереву main fa5b7348 (до правки), см. артефакт TGFEEDPC0310.
GOLDEN_SUGGEST_TRACE = [
    ("suggest.is_enabled",), ("suggest.on_client_message", 1001, 9000),
    ("suggest.is_enabled",), ("suggest.on_client_message", 1002, 9000),
    ("suggest.is_enabled",), ("suggest.on_client_message", 2002, 9000),
    ("suggest.is_enabled",), ("suggest.on_client_message", 3003, 9000),
    ("suggest.is_enabled",), ("suggest.on_client_message", 1001, 9000),
]


class TestGolden(_Base):
    def test_suggest_calls_unchanged(self):
        self.assertEqual(suggest_trace(ub, StrictClient), GOLDEN_SUGGEST_TRACE)

    def test_suggest_calls_same_with_feed_off(self):
        os.environ[tg_feed.OFF_ENV] = "1"
        self.assertEqual(suggest_trace(ub, StrictClient), GOLDEN_SUGGEST_TRACE)


# ─────────────────────── 6. строка целиком ───────────────────────

class TestLine(_Base):
    REC = {"chat_id": 1, "msg_id": 2, "dir": "in", "ts": 3, "kind": "text",
           "text": "раз\nдва три\x85четыре", "reply_to": None, "sender_id": 1}

    def test_one_line_roundtrip(self):
        s = tg_feed.line(self.REC)
        self.assertTrue(s.endswith("\n"))
        self.assertEqual(len(s[:-1].splitlines()), 1, "ряд рвётся на строки")
        self.assertEqual(json.loads(s), self.REC)

    def test_append_fsyncs_whole_line(self):
        synced = []
        real = os.fsync
        with mock.patch.object(tg_feed.os, "fsync", side_effect=lambda fd: (synced.append(fd),
                                                                           real(fd))):
            n = tg_feed.append(self.REC, self.feed)
        self.assertEqual(len(synced), 1)
        with io.open(self.feed, "rb") as f:
            data = f.read()
        self.assertEqual(n, len(data))
        self.assertEqual(self.rows(), [self.REC])

    def test_torn_tail_does_not_glue_rows(self):
        with io.open(self.feed, "wb") as f:
            f.write(b'{"chat_id":1,"msg_id":1,"di')        # оборванная прошлая запись
        tg_feed.append(self.REC, self.feed)
        with io.open(self.feed, encoding="utf-8") as f:
            lines = f.read().split("\n")
        self.assertEqual(json.loads(lines[1]), self.REC)

    def test_creates_folder(self):
        path = os.path.join(FEED_TMP_DIR, "new_dir_" + str(os.getpid()), "tg_feed.jsonl")
        tg_feed.append(self.REC, path)
        with io.open(path, encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline()), self.REC)


# ─────────────── 7. вне git, без сети, один клиент ───────────────

class TestBoundaries(unittest.TestCase):
    def test_feed_is_outside_git(self):
        with io.open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        self.assertIn("tg_feed/", lines, "лента выпала из .gitignore — тексты клиентов уедут в git")
        rel = os.path.relpath(tg_feed.FEED_DIR, tg_feed.BASE_DIR)
        self.assertEqual(rel, "tg_feed")
        self.assertEqual(os.path.dirname(tg_feed.FEED_FILE), tg_feed.FEED_DIR)

    def test_feed_module_has_no_network_telegram_or_sqlite(self):
        with io.open(os.path.join(HERE, "tg_feed.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names, {"json", "os", "datetime"})

    def test_main_hooks_outgoing_on_the_same_single_client(self):
        made = []

        class FakeTG:
            def __init__(self, *a, **k):
                self.handlers = []
                made.append(self)

            def add_event_handler(self, cb, ev):
                self.handlers.append((cb, ev))

            async def disconnect(self):
                pass

        async def no_serve(client):
            return None

        import moderation_ipc
        with mock.patch.object(ub, "TelegramClient", FakeTG), \
                mock.patch.object(ub, "acquire_lock", return_value=True), \
                mock.patch.object(ub, "release_lock"), \
                mock.patch.object(ub, "serve_forever", side_effect=no_serve), \
                mock.patch.object(moderation_ipc, "init_db"), \
                mock.patch.object(ub, "log", LogRec()):
            asyncio.run(ub.main())
        self.assertEqual(len(made), 1, "второй клиент Telethon")
        by_cb = {cb: ev for cb, ev in made[0].handlers}
        self.assertIn(ub.on_outgoing, by_cb)
        self.assertIs(by_cb[ub.on_outgoing].outgoing, True)
        self.assertIs(by_cb[ub.on_incoming].incoming, True)
        self.assertEqual(len(made[0].handlers), 3)


if __name__ == "__main__":
    unittest.main()
