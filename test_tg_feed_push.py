# -*- coding: utf-8 -*-
"""
test_tg_feed_push.py — пушер ленты Telegram (03.10.2026, задание 0128p-79n.0310, TGPUSHPC0310).

Без сокетов, сети, Telegram, моста и SQLite: дверь сервера — подделка `Door` (хранит ряды по ключу,
отвечает по сценарию). Ленты синтетические, во временном каталоге теста
(`<TEMP>/turbobaby_TESTING_tg_feed_push/<тест>/`); файлы ПЕРЕПИСЫВАЮТСЯ в setUp, а не удаляются.
Живая лента не открывается: путь к ней тест только сравнивает строкой.

Запуск: python -m unittest test_tg_feed_push -v
"""

import ast
import io
import json
import os
import socket
import tempfile
import unittest
from unittest import mock

import tg_feed
import tg_feed_push as P

ROOT = os.path.dirname(os.path.abspath(__file__))
TMP_ROOT = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_tg_feed_push")
NOW = 1_790_000_000.0
TOKEN = "tok-FAKE-1234567890"          # синтетика: живых секретов тест не видит


def mk_row(chat, mid, d="in", text="привет", ts=1_790_000_000):
    return {"chat_id": chat, "msg_id": mid, "dir": d, "ts": ts, "kind": "text", "text": text,
            "reply_to": None, "sender_id": chat if d == "in" else 777}


def line(rec):
    return tg_feed.line(rec).encode("utf-8")


class Crash(BaseException):
    """Процесс убит посреди пачки (не Exception — его не ловит ни одна ветка пушера)."""


class Door:
    """Подделка двери сервера. Сценарий — список действий на вызовы по порядку; кончился — "ok".
    ok · lose (записал, ответ потерян) · crash (записал, процесс убит) · timeout (не дошло) ·
    ("partial", n) · ("only", [индексы]) · ("status", код) · ("raw", байты) · ("json", объект)."""

    def __init__(self, *script):
        self.script = list(script)
        self.store = {}
        self.order = []            # ключи в порядке первой записи
        self.calls = []            # (url, тело-объект, заголовки, таймаут)

    def _keep(self, rows):
        for r in rows:
            k = (r["chat_id"], r["msg_id"], r["dir"])
            if k not in self.store:
                self.store[k] = r
                self.order.append(k)

    def __call__(self, url, body, headers, timeout):
        obj = json.loads(body.decode("utf-8"))
        self.calls.append((url, obj, headers, timeout))
        rows = obj["rows"]
        act = self.script.pop(0) if self.script else "ok"
        keys = lambda rs: [[r["chat_id"], r["msg_id"], r["dir"]] for r in rs]  # noqa: E731
        if act == "ok":
            self._keep(rows)
            return 200, json.dumps({"accepted": keys(rows)}).encode()
        if act == "lose":
            self._keep(rows)
            raise socket.timeout("timed out")
        if act == "crash":
            self._keep(rows)
            raise Crash()
        if act == "timeout":
            raise socket.timeout("timed out with " + TOKEN)
        kind, arg = act
        if kind == "partial":
            self._keep(rows[:arg])
            return 200, json.dumps({"accepted": keys(rows[:arg])}).encode()
        if kind == "only":
            got = [rows[i] for i in arg]
            self._keep(got)
            return 200, json.dumps({"accepted": keys(got)}).encode()
        if kind == "status":
            self._keep(rows)
            return arg, json.dumps({"accepted": keys(rows)}).encode()
        if kind == "raw":
            self._keep(rows)
            return 200, arg
        if kind == "json":
            self._keep(rows)
            return 200, json.dumps(arg).encode()
        raise AssertionError(act)


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(TMP_ROOT, self.id().rsplit(".", 1)[-1])
        os.makedirs(self.dir, exist_ok=True)
        self.feed = os.path.join(self.dir, "feed.jsonl")
        self.cursor = os.path.join(self.dir, "cursor.json")
        self.alarm = os.path.join(self.dir, "alarm.json")
        self.lock = os.path.join(self.dir, "push.lock")
        with io.open(self.feed, "wb"):
            pass                                            # пустая лента (перезапись)
        P._atomic_write_json(self.cursor, P._fresh_state())  # курсор «с нуля» (перезапись)
        with io.open(self.alarm, "wb") as f:
            f.write(b"{}")                                  # признака нет (перезапись)
        self.lines = []

    def write_feed(self, *recs, tail=b""):
        data = b"".join(line(r) if isinstance(r, dict) else r for r in recs) + tail
        with io.open(self.feed, "wb") as f:
            f.write(data)
        return data

    def append_feed(self, data):
        with io.open(self.feed, "ab") as f:
            f.write(data)

    def tick(self, door, now=NOW, **kw):
        kw.setdefault("token", TOKEN)
        kw.setdefault("url", "https://door.test/tg-queue/push")
        kw.setdefault("budget", 1000.0)
        return P.tick(feed=self.feed, cursor=self.cursor, alarm=self.alarm, lock=self.lock,
                      transport=door, now=now, out=self.lines.append, **kw)

    def state(self):
        return P.load_state(self.cursor)

    def alarm_rec(self):
        return P._read_json(self.alarm)

    def feed_bytes(self):
        with io.open(self.feed, "rb") as f:
            return f.read()

    def assert_cursor_consistent(self):
        st, data = self.state(), self.feed_bytes()
        self.assertEqual(st["count"], data[:st["offset"]].count(b"\n"))
        self.assertTrue(st["offset"] == 0 or data[st["offset"] - 1:st["offset"]] == b"\n")


class TestHappyPath(_Base):
    def test_all_rows_sent_once_cursor_at_end(self):
        rows = [mk_row(1, i) for i in range(1, 6)]
        data = self.write_feed(*rows)
        door = Door()
        self.assertEqual(self.tick(door), P.RC_OK)
        self.assertEqual(len(door.calls), 1)
        self.assertEqual(door.calls[0][1]["rows"], rows)          # ряды как есть
        st = self.state()
        self.assertEqual((st["offset"], st["count"], st["fails"]), (len(data), 5, 0))
        self.assert_cursor_consistent()
        self.assertTrue(any("отправлено 5 рядов" in s for s in self.lines), self.lines)
        self.assertEqual(self.tick(door), P.RC_OK)                # нового нет — ни вызова, ни строки
        self.assertEqual(len(door.calls), 1)

    def test_contract_headers_body_url(self):
        self.write_feed(mk_row(5, 1))
        door = Door()
        self.tick(door)
        url, obj, headers, timeout = door.calls[0]
        self.assertEqual(url, "https://door.test/tg-queue/push")
        self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(set(obj), {"batch_id", "rows"})
        self.assertTrue(obj["batch_id"].startswith("tgp-0-"))
        self.assertLessEqual(timeout, P.HTTP_TIMEOUT_S)

    def test_env_names_and_fallback_to_pull_secret(self):
        self.assertEqual(P.door_url({}), "https://wa.turbophuket.com/tg-queue/push")
        self.assertEqual(P.door_url({"WA_QUEUE_BASE": "https://x.test/"}), "https://x.test/tg-queue/push")
        self.assertEqual(P.door_url({"TG_FEED_PUSH_URL": "https://y.test/p", "WA_QUEUE_BASE": "z"}),
                         "https://y.test/p")
        self.assertEqual(P.door_token({"WA_PULL_SECRET": " s1 "}), "s1")
        self.assertEqual(P.door_token({"WA_PULL_SECRET": "s1", "TG_FEED_PUSH_TOKEN": "s2"}), "s2")
        self.assertEqual(P.door_token({}), "")

    def test_no_token_is_loud_stop_without_call(self):
        self.write_feed(mk_row(1, 1))
        door = Door()
        self.assertEqual(self.tick(door, token=""), P.RC_STOP)
        self.assertEqual(door.calls, [])
        self.assertIs(self.alarm_rec().get("alarm"), True)
        self.assertTrue(any(s.startswith(P.LOUD) for s in self.lines))

    def test_feed_absent_and_cursor_zero_is_quiet(self):
        door = Door()
        rc = P.tick(feed=os.path.join(self.dir, "never.jsonl"), cursor=self.cursor, alarm=self.alarm,
                    lock=self.lock, transport=door, now=NOW, out=self.lines.append, token=TOKEN,
                    url="u")
        self.assertEqual((rc, door.calls, self.lines), (P.RC_OK, [], []))

    def test_missing_cursor_file_means_from_zero(self):
        self.assertEqual(P.load_state(os.path.join(self.dir, "never-cursor.json")), P._fresh_state())

    def test_live_paths_are_in_feed_dir(self):
        self.assertEqual(P.FEED_FILE, tg_feed.FEED_FILE)
        for p in (P.CURSOR_FILE, P.ALARM_FILE, P.LOCK_FILE):
            self.assertEqual(os.path.dirname(p), tg_feed.FEED_DIR)


class TestLostAndRetry(_Base):
    def test_lost_response_cursor_stays_then_resend_dupes_ok(self):
        rows = [mk_row(1, i) for i in range(1, 4)]
        data = self.write_feed(*rows)
        door = Door("lose")
        self.assertEqual(self.tick(door), P.RC_FAIL)
        st = self.state()
        self.assertEqual((st["offset"], st["count"], st["fails"]), (0, 0, 1))
        self.assertEqual(st["next_at"], NOW + 5)
        self.assertEqual(len(door.store), 3)                     # сервер записал, ответ потерян
        self.assertEqual(self.tick(door, now=NOW + 4), P.RC_OK)  # пауза идёт — вызова нет
        self.assertEqual(len(door.calls), 1)
        self.assertEqual(self.tick(door, now=NOW + 5), P.RC_OK)  # повтор: сервер ответил дублями
        self.assertEqual(len(door.calls), 2)
        self.assertEqual(door.calls[1][1]["rows"], rows)
        st = self.state()
        self.assertEqual((st["offset"], st["count"], st["fails"], st["next_at"]), (len(data), 3, 0, 0.0))
        self.assertEqual(len(door.store), 3)

    def test_timeout_nothing_confirmed_token_not_printed(self):
        self.write_feed(mk_row(1, 1))
        door = Door("timeout")
        self.assertEqual(self.tick(door), P.RC_FAIL)
        st = self.state()
        self.assertEqual((st["offset"], st["fails"]), (0, 1))
        self.assertIn("timed out", st["last_error"])
        self.assertNotIn(TOKEN, st["last_error"])
        self.assertFalse(any(TOKEN in s for s in self.lines), self.lines)
        with io.open(self.cursor, "rb") as f:
            self.assertNotIn(TOKEN.encode(), f.read())

    def test_backoff_sequence_and_cap(self):
        self.assertEqual([P.backoff(n) for n in range(0, 10)],
                         [0, 5, 10, 20, 40, 80, 160, 300, 300, 300])

    def test_ceiling_12_fails_loud_and_flag_then_cleared(self):
        self.write_feed(mk_row(1, 1))
        door = Door(*(["timeout"] * 13))
        now = NOW
        for n in range(1, 12):
            self.assertEqual(self.tick(door, now=now), P.RC_FAIL)
            self.assertNotEqual(self.alarm_rec().get("alarm"), True, n)
            now = self.state()["next_at"]
        self.assertFalse(any(s.startswith(P.LOUD) for s in self.lines))
        self.assertEqual(self.tick(door, now=now), P.RC_FAIL)     # 12-я неудача
        rec = self.alarm_rec()
        self.assertIs(rec.get("alarm"), True)
        self.assertEqual((rec["kind"], rec["fails"]), ("ceiling", 12))
        loud = [s for s in self.lines if s.startswith(P.LOUD)]
        self.assertEqual(len(loud), 1)
        self.assertIn("ПОТОЛОК", loud[0])
        now = self.state()["next_at"]
        self.assertEqual(now - NOW, sum(P.backoff(n) for n in range(1, 13)))
        self.tick(door, now=now)                                   # 13-я: громко не повторяет
        self.assertEqual(len([s for s in self.lines if s.startswith(P.LOUD)]), 1)
        now = self.state()["next_at"]
        self.assertEqual(self.tick(door, now=now), P.RC_OK)        # сервер ожил
        self.assertIs(self.alarm_rec().get("alarm"), False)
        self.assertEqual(self.state()["fails"], 0)


class TestPartialAndOrder(_Base):
    def test_partial_confirmation_moves_to_first_unconfirmed(self):
        rows = [mk_row(1, i) for i in range(1, 6)]
        self.write_feed(*rows)
        door = Door(("partial", 2))
        self.assertEqual(self.tick(door), P.RC_OK)
        st = self.state()
        self.assertEqual(st["count"], 2)
        self.assertEqual(st["offset"], len(line(rows[0])) + len(line(rows[1])))
        self.assertTrue(any("частично" in s for s in self.lines))
        self.assertEqual(len(door.calls), 1)                       # остаток — следующим заходом
        self.tick(door)
        self.assertEqual(door.calls[1][1]["rows"], rows[2:])
        self.assertEqual(self.state()["count"], 5)

    def test_hole_in_accepted_stops_cursor_before_it(self):
        rows = [mk_row(1, i) for i in range(1, 6)]
        self.write_feed(*rows)
        door = Door(("only", [0, 1, 3, 4]))                        # третий не подтверждён
        self.tick(door)
        self.assertEqual(self.state()["count"], 2)
        self.assert_cursor_consistent()
        self.tick(door)
        self.assertEqual(door.calls[1][1]["rows"], rows[2:])        # 4 и 5 — повтором, как дубли
        self.assertEqual(self.state()["count"], 5)

    def test_first_row_unconfirmed_is_failure_not_progress(self):
        self.write_feed(mk_row(1, 1), mk_row(1, 2))
        door = Door(("only", [1]))
        self.assertEqual(self.tick(door), P.RC_FAIL)
        st = self.state()
        self.assertEqual((st["offset"], st["fails"]), (0, 1))

    def test_order_per_chat_is_file_order(self):
        rows = []
        for i in range(1, 31):
            rows.append(mk_row(100 + i % 3, i, "in" if i % 2 else "out"))
        self.write_feed(*rows)
        door = Door(("partial", 7), "lose", ("only", [0, 1, 2, 5]), "ok", "ok")
        now = NOW
        for _ in range(8):
            self.tick(door, now=now)
            now = max(now, self.state()["next_at"])
        self.assertEqual(self.state()["count"], 30)
        sent = [r for c in door.calls for r in c[1]["rows"]]
        for chat in (100, 101, 102):
            want = [r["msg_id"] for r in rows if r["chat_id"] == chat]
            seen, got = set(), []
            for r in sent:
                if r["chat_id"] == chat and r["msg_id"] not in seen:
                    seen.add(r["msg_id"])
                    got.append(r["msg_id"])
            self.assertEqual(got, want, chat)
        for c in door.calls:                                        # внутри пачки — порядок файла
            mids = [r["msg_id"] for r in c[1]["rows"]]
            self.assertEqual(mids, sorted(mids))

    def test_duplicate_rows_in_feed_pass_on_one_key(self):
        a, b = mk_row(1, 1), mk_row(1, 2)
        data = self.write_feed(a, b, a, mk_row(1, 3))               # повтор апдейта после переподключения
        door = Door()
        self.tick(door)
        self.assertEqual(self.state()["offset"], len(data))
        self.assertEqual(len(door.store), 3)
        self.assertEqual(len(door.calls[0][1]["rows"]), 4)          # как есть — дубль тоже


class TestBadResponses(_Base):
    CASES = [
        ("status 500", ("status", 500)),
        ("status 302", ("status", 302)),
        ("не JSON", ("raw", b"<html>oops")),
        ("пустое тело", ("raw", b"")),
        ("нет accepted", ("json", {"ok": True})),
        ("accepted не список", ("json", {"accepted": "all"})),
        ("элемент из двух", ("json", {"accepted": [[1, 1]]})),
        ("bool вместо id", ("json", {"accepted": [[True, 1, "in"]]})),
        ("чужое направление", ("json", {"accepted": [[1, 1, "sideways"]]})),
        ("строка вместо id", ("json", {"accepted": [["1", 1, "in"]]})),
        ("кривой элемент среди верных", ("json", {"accepted": [[1, 1, "in"], [1, 2, "x"]]})),
        ("не объект", ("json", [[1, 1, "in"]])),
    ]

    def test_bad_response_confirms_nothing(self):
        for name, act in self.CASES:
            with self.subTest(name):
                self.setUp()
                self.write_feed(mk_row(1, 1), mk_row(1, 2))
                door = Door(act)
                self.assertEqual(self.tick(door), P.RC_FAIL)
                st = self.state()
                self.assertEqual((st["offset"], st["count"], st["fails"]), (0, 0, 1))
                self.assertTrue(st["last_error"])

    def test_parse_accepted_good(self):
        keys, why = P.parse_accepted(200, b'{"accepted": [[1, 2, "in"], [3, 4, "out"]]}')
        self.assertEqual((keys, why), ({(1, 2, "in"), (3, 4, "out")}, ""))
        self.assertEqual(P.parse_accepted(200, b'{"accepted": []}'), (set(), ""))

    def test_no_redirects(self):
        self.assertIsNone(P._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil"))


class TestRestartAndFile(_Base):
    def test_crash_mid_batch_then_restart_resends(self):
        rows = [mk_row(2, i) for i in range(1, 4)]
        data = self.write_feed(*rows)
        door = Door("crash")
        with self.assertRaises(Crash):
            self.tick(door)
        self.assertEqual(self.state(), P._fresh_state())            # курсор не тронут
        self.assertEqual(self.tick(door), P.RC_OK)                  # «перезапуск»: замок отпущен
        self.assertEqual(self.state()["offset"], len(data))
        self.assertEqual(len(door.store), 3)

    def test_crash_while_saving_cursor_keeps_old_cursor(self):
        rows = [mk_row(2, i) for i in range(1, 4)]
        self.write_feed(*rows)
        door = Door()
        real = os.fsync
        calls = {"n": 0}

        def boom(fd):
            calls["n"] += 1
            raise OSError("disk gone")
        with mock.patch.object(P.os, "fsync", boom):
            with self.assertRaises(OSError):
                self.tick(door)
        self.assertGreater(calls["n"], 0)
        self.assertEqual(self.state(), P._fresh_state())            # старый курсор цел и читаем
        self.assertIs(os.fsync, real)
        self.assertEqual(self.tick(door), P.RC_OK)
        self.assertEqual(self.state()["count"], 3)

    def test_torn_tail_not_sent_until_complete(self):
        rows = [mk_row(3, 1), mk_row(3, 2)]
        last = line(mk_row(3, 3))
        data = self.write_feed(*rows, tail=last[:-7])
        door = Door()
        self.tick(door)
        self.assertEqual(door.calls[0][1]["rows"], rows)
        self.assertEqual(self.state()["offset"], len(data) - len(last[:-7]))
        self.append_feed(last[-7:])
        self.tick(door)
        self.assertEqual(door.calls[1][1]["rows"], [mk_row(3, 3)])
        self.assertEqual(self.state()["offset"], len(self.feed_bytes()))

    def test_torn_fragment_inside_is_skipped_loudly(self):
        frag = line(mk_row(4, 1))[:20] + b"\n"                      # обрывок, закрытый писателем
        data = self.write_feed(mk_row(4, 0), frag, mk_row(4, 2))
        door = Door()
        self.assertEqual(self.tick(door), P.RC_OK)
        self.assertEqual([r["msg_id"] for r in door.calls[0][1]["rows"]], [0, 2])
        st = self.state()
        self.assertEqual((st["offset"], st["count"], st["skipped_bad"]), (len(data), 3, 1))
        self.assertTrue(any(s.startswith(P.LOUD) and "не ряд" in s for s in self.lines))

    def test_shortened_file_loud_stop_no_send(self):
        self.write_feed(*[mk_row(5, i) for i in range(1, 4)])
        door = Door()
        self.tick(door)
        self.write_feed(mk_row(5, 1))                                # файл стал короче курсора
        self.append_feed(b"")
        n = len(door.calls)
        self.assertEqual(self.tick(door), P.RC_STOP)
        self.assertEqual(len(door.calls), n)
        rec = self.alarm_rec()
        self.assertEqual((rec.get("alarm"), rec.get("kind")), (True, "stop"))
        loud = [s for s in self.lines if s.startswith(P.LOUD)]
        self.assertEqual(len(loud), 1)
        self.assertIn("короче курсора", loud[0])
        self.assertEqual(self.tick(door, now=NOW + 60), P.RC_STOP)   # громко не чаще раза в час
        self.assertEqual(len([s for s in self.lines if s.startswith(P.LOUD)]), 1)
        self.assertEqual(self.tick(door, now=NOW + P.REMIND_S), P.RC_STOP)
        self.assertEqual(len([s for s in self.lines if s.startswith(P.LOUD)]), 2)

    def test_replaced_file_same_size_is_stop(self):
        rows = [mk_row(6, i) for i in range(1, 4)]
        self.write_feed(*rows)
        door = Door()
        self.tick(door)
        self.write_feed(*[mk_row(7, i) for i in range(1, 4)], mk_row(7, 9))  # чужие байты под курсором
        self.assertEqual(self.tick(door), P.RC_STOP)
        self.assertEqual(len(door.calls), 1)
        self.assertIn("другие байты", self.alarm_rec().get("reason", ""))

    def test_corrupt_cursor_is_stop_not_restart(self):
        self.write_feed(mk_row(1, 1))
        with io.open(self.cursor, "wb") as f:
            f.write(b'{"offset": 12, "cou')
        door = Door()
        self.assertEqual(self.tick(door), P.RC_STOP)
        self.assertEqual(door.calls, [])
        self.assertIs(self.alarm_rec().get("alarm"), True)

    def test_busy_lock_no_send(self):
        self.write_feed(mk_row(1, 1))
        fd = P.take_lock(self.lock)
        self.assertIsNotNone(fd)
        try:
            door = Door()
            self.assertEqual(self.tick(door), P.RC_BUSY)
            self.assertEqual(door.calls, [])
        finally:
            P.drop_lock(fd)
        self.assertEqual(self.tick(Door()), P.RC_OK)


class TestBatchLimits(_Base):
    def test_rows_cap_100(self):
        self.write_feed(*[mk_row(8, i) for i in range(250)])
        door = Door()
        self.tick(door)
        self.assertEqual([len(c[1]["rows"]) for c in door.calls], [100, 100, 50])
        self.assertEqual(self.state()["count"], 250)

    def test_bytes_cap_256k(self):
        big = "я" * 3000                                             # ~6 КБ на ряд в UTF-8
        self.write_feed(*[mk_row(9, i, text=big) for i in range(90)])
        door = Door()
        self.tick(door)
        self.assertGreater(len(door.calls), 1)
        for c in door.calls:
            body = json.dumps(c[1], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.assertLessEqual(len(body), P.BATCH_BYTES)
        self.assertEqual(self.state()["count"], 90)

    def test_single_oversize_row_is_stop(self):
        self.write_feed(mk_row(9, 1), mk_row(9, 2, text="x" * (P.BATCH_BYTES + 10)), mk_row(9, 3))
        door = Door()
        self.assertEqual(self.tick(door), P.RC_STOP)
        self.assertEqual(self.state()["count"], 1)                   # первый прошёл, на втором стоп
        self.assertIn("больше пачки", self.alarm_rec().get("reason", ""))


class TestBoundaries(unittest.TestCase):
    def test_imports_are_small_and_offline(self):
        with io.open(os.path.join(ROOT, "tg_feed_push.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        for bad in ("sqlite3", "telethon", "suggest", "userbot_listen", "pc_orchestrator",
                    "bridge_http", "brain_writer", "subprocess", "shutil"):
            self.assertNotIn(bad, names)
        with io.open(os.path.join(ROOT, "tg_feed_push.py"), encoding="utf-8") as f:
            src = f.read()
        for bad in ("os.remove", "os.unlink", "rmtree", "os.rmdir"):
            self.assertNotIn(bad, src)

    def test_not_in_client_closure(self):
        import client_contour
        self.assertFalse(client_contour.is_client("tg_feed_push.py", repo=ROOT))
        self.assertTrue(client_contour.is_client("tg_feed.py", repo=ROOT))


if __name__ == "__main__":
    unittest.main()
