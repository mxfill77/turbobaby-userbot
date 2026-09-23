# -*- coding: utf-8 -*-
"""
test_chatlog_servicing.py — регресс захвата форума Обслуживания в архив переписки (23.09.2026,
задание ОБСЛУЖФОРУМЖУРНАЛФОТО2309).

ЧЕГО ЭТИ ТЕСТЫ НЕ КАСАЮТСЯ: боевого архива `chatlog/`, боевой папки снимков, живых чатов и любой
базы. Хранилище уводится во временный каталог ручкой `CHATLOG_ROOT`, папка снимков — параметром
`photo_dir`, Telegram заменён подделкой загрузки без единого байта сети. Модуль логики telethon не
импортирует вовсе (держит `TestNothingGoesOut`).

ОТРИЦАТЕЛЬНЫЕ ТЕСТЫ ЗАДАНИЯ (п.4) — `TestNegative`:
  * повторный прогон не удваивает архив (дедуп `k`) и НЕ качает снимок второй раз;
  * нескачанное вложение оставляет строку с исходом НЕ СКАЧАН и причиной словами, файла нет;
  * сообщение без медиа — `media` пустой, файла нет;
  * не картинка — записи-снимка нет, файла нет.
"""

import io
import os
import ast
import json
import shutil
import asyncio
import tempfile
import datetime
import subprocess
import unittest

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import chatlog_store as store
import chatlog_servicing as sv

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = datetime.timezone.utc
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
NOW = datetime.datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


def fact(mid, text="", media=None, when=None, topic=4615, reply_in_topic=None, sender=555001,
         action=None, action_title=None, bot=False):
    """Факт в той форме, какую строит `chatlog_servicing_fetch.to_fact` из живого сообщения."""
    if topic == sv.GENERAL_ID:
        ft, top, rmid = False, None, reply_in_topic
    elif reply_in_topic is None:
        ft, top, rmid = True, None, topic          # сообщение прямо в тему
    else:
        ft, top, rmid = True, topic, reply_in_topic  # ответ внутри темы
    return {"mid": mid, "when": when or (NOW - datetime.timedelta(hours=1)),
            "sender_raw": str(sender), "bot": bot, "out": False, "text": text,
            "forum_topic": ft, "top_id": top, "reply_msg_id": rmid,
            "action": action, "action_title": action_title, "media": media, "album": None}


class FakeDownload(object):
    """Подделка `download_media`: пишет байты по пути, либо падает, либо отдаёт пустоту."""

    def __init__(self, payload=JPEG, fail=None, empty=False):
        self.payload, self.fail, self.empty = payload, fail, empty
        self.calls = []

    async def __call__(self, f, path):
        self.calls.append((f["mid"], path))
        if self.fail is not None:
            raise self.fail
        if self.empty:
            return None
        with open(path, "wb") as fh:
            fh.write(self.payload)
        return path


def run(facts, dl, **kw):
    return asyncio.run(sv.capture(facts, dl, **kw))


class _Rooted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turbobaby_TESTING_servicing_")
        self.root = os.path.join(self.tmp, "chatlog")
        self.photos = os.path.join(self.tmp, "snimki")
        self._save = os.environ.get(store.ROOT_ENV)
        os.environ[store.ROOT_ENV] = self.root
        store._SALT_CACHE.pop(self.root, None)

    def tearDown(self):
        store._SALT_CACHE.pop(self.root, None)
        if self._save is None:
            os.environ.pop(store.ROOT_ENV, None)
        else:
            os.environ[store.ROOT_ENV] = self._save
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self, day="2026-09-23"):
        p = os.path.join(self.root, sv.GROUP_SLUG, day[:4], day[5:7], day + ".jsonl")
        if not os.path.exists(p):
            return []
        with io.open(p, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def photo_files(self):
        out = []
        for dp, _dn, fns in os.walk(self.photos):
            out.extend(os.path.join(dp, fn) for fn in fns)
        return out


# ─────────────────────────── отрицательные тесты задания ───────────────────────────

class TestNegative(_Rooted):
    def test_repeat_run_does_not_double_archive(self):
        """Повторный прогон: 0 новых строк, все — дубли по `k`, снимок НЕ качается второй раз."""
        facts = [fact(101, "масло поменяли"), fact(102, "фото пробега", media=sv.PHOTO)]
        dl = FakeDownload()
        first = run(facts, dl, photo_dir=self.photos)
        self.assertEqual(first["written"], 2)
        self.assertEqual(len(dl.calls), 1)
        before = self.lines()
        second = run(facts, dl, photo_dir=self.photos)
        self.assertEqual(second["written"], 0)
        self.assertEqual(second["dup"], 2)
        self.assertEqual(len(dl.calls), 1, "снимок скачан второй раз")
        self.assertEqual(second["photo_already_archived"], 1)
        self.assertEqual(self.lines(), before, "архив изменился от повторного прогона")
        self.assertEqual(len(self.photo_files()), 1)
        self.assertEqual([r["k"] for r in before], ["m101", "m102"])

    def test_failed_download_leaves_line_with_reason(self):
        """Сорванная загрузка: строка ЕСТЬ, исход НЕ СКАЧАН, причина словами, файла нет."""
        dl = FakeDownload(fail=ConnectionError("сеть оборвалась"))
        res = run([fact(201, "вот чек", media=sv.PHOTO)], dl, photo_dir=self.photos)
        self.assertEqual(res["not_downloaded"], 1)
        self.assertEqual(res["saved"], 0)
        recs = self.lines()
        self.assertEqual(len(recs), 1)
        m = recs[0]["media"]
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["outcome"], sv.NOT_DOWNLOADED)
        self.assertIn("ConnectionError", m[0]["reason"])
        self.assertIn("Telegram не отдал файл", m[0]["reason"])
        self.assertIsNone(m[0]["file"])
        self.assertNotIn(sv.SAVED, json.dumps(m, ensure_ascii=False))
        self.assertEqual(self.photo_files(), [])

    def test_empty_or_zero_or_foreign_bytes_are_not_saved(self):
        """Пустой путь, нулевые байты, чужая сигнатура — три разных НЕ СКАЧАН, ни одного СОХРАНЁН."""
        cases = [(FakeDownload(empty=True), "пустой путь"),
                 (FakeDownload(payload=b""), "нулевой длины"),
                 (FakeDownload(payload=b"<html>not a picture</html>"), "сигнатура")]
        for i, (dl, word) in enumerate(cases):
            run([fact(300 + i, media=sv.PHOTO)], dl, photo_dir=self.photos)
        recs = self.lines()
        self.assertEqual(len(recs), 3)
        for rec, (_dl, word) in zip(recs, cases):
            self.assertEqual(rec["media"][0]["outcome"], sv.NOT_DOWNLOADED)
            self.assertIn(word, rec["media"][0]["reason"])

    def test_message_without_media_has_empty_media_and_no_file(self):
        dl = FakeDownload()
        res = run([fact(401, "замена колодок завтра")], dl, photo_dir=self.photos)
        self.assertEqual(res["no_media"], 1)
        rec = self.lines()[0]
        self.assertEqual(rec["media"], [])
        self.assertEqual(dl.calls, [])
        self.assertFalse(os.path.exists(self.photos), "папка снимков заведена без снимка")

    def test_not_a_photo_gives_no_photo_record_and_no_file(self):
        """Видео/голос/стикер: вида-маркер остаётся (как у других источников), записи-снимка и
        файла НЕТ, загрузка не зовётся."""
        dl = FakeDownload()
        res = run([fact(501, media="video"), fact(502, media="sticker")], dl, photo_dir=self.photos)
        self.assertEqual(res["not_a_photo"], 2)
        self.assertEqual(dl.calls, [])
        for rec in self.lines():
            self.assertEqual(len(rec["media"]), 1)
            self.assertNotIn("outcome", rec["media"][0])
            self.assertNotIn("file", rec["media"][0])
            self.assertNotEqual(rec["media"][0]["kind"], sv.PHOTO)
        self.assertEqual(self.photo_files(), [])


# ─────────────────────────── снимок сохранён ───────────────────────────

class TestSaved(_Rooted):
    def test_saved_photo_links_file_in_media(self):
        dl = FakeDownload()
        res = run([fact(601, "одометр", media=sv.PHOTO)], dl, photo_dir=self.photos)
        self.assertEqual(res["saved"], 1)
        self.assertEqual(res["saved_bytes"], len(JPEG))
        m = self.lines()[0]["media"][0]
        self.assertEqual(m["outcome"], sv.SAVED)
        self.assertEqual(m["bytes"], len(JPEG))
        self.assertEqual(m["type"], "jpeg")
        files = self.photo_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(m["file"], files[0].replace("\\", "/"))
        # имя: дата/время Пхукета, тема, номер сообщения; месяц — каталогом
        self.assertTrue(os.path.basename(files[0]).endswith("_t4615_msg601.jpg"), files[0])
        self.assertIn(os.sep + "2026-09" + os.sep, files[0])

    def test_file_already_on_disk_is_reused_not_redownloaded(self):
        """Обрыв между загрузкой и записью строки: файл лежит, строки нет. Повтор берёт файл."""
        path = sv.photo_path(NOW - datetime.timedelta(hours=1), 701, 4615, self.photos)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as fh:
            fh.write(JPEG)
        dl = FakeDownload()
        res = run([fact(701, media=sv.PHOTO)], dl, photo_dir=self.photos)
        self.assertEqual(dl.calls, [])
        self.assertEqual(res["photo_reused"], 1)
        self.assertEqual(self.lines()[0]["media"][0]["outcome"], sv.SAVED)

    def test_budget_exhausted_is_not_downloaded_with_reason(self):
        ticks = iter([0.0, 10.0, 10.0])
        dl = FakeDownload()
        res = run([fact(801, media=sv.PHOTO)], dl, photo_dir=self.photos, budget_s=5,
                  clock=lambda: next(ticks))
        self.assertEqual(dl.calls, [])
        self.assertEqual(res["not_downloaded"], 1)
        self.assertIn("бюджет", self.lines()[0]["media"][0]["reason"])

    def test_dry_writes_nothing_and_downloads_nothing(self):
        dl = FakeDownload()
        res = run([fact(901, "x", media=sv.PHOTO), fact(902, "y")], dl, photo_dir=self.photos,
                  dry=True)
        self.assertTrue(res["dry"])
        self.assertEqual(res["would_write"], 2)
        self.assertEqual(res["photo_would_download"], 1)
        self.assertEqual(dl.calls, [])
        # Соль псевдонимов заводится при первом псевдониме и в сухом прогоне (как у
        # `chatlog_ingest --dry`); данных же — ни строки дня, ни индекса, ни манифеста.
        self.assertFalse(os.path.exists(os.path.join(self.root, sv.GROUP_SLUG)))
        self.assertFalse(os.path.exists(os.path.join(self.root, store.INDEX_DIR)))
        self.assertFalse(os.path.exists(os.path.join(self.root, store.MANIFEST)))
        self.assertFalse(os.path.exists(self.photos))


# ─────────────────────────── темы, окно, люди, манифест ───────────────────────────

class TestShape(_Rooted):
    def test_topic_is_a_field_named_from_topics_map(self):
        facts = [fact(1001, "в теме", topic=4615), fact(1002, "ответ", topic=4615, reply_in_topic=1001),
                 fact(1003, "в общей", topic=sv.GENERAL_ID), fact(1004, "новая", topic=9999)]
        run(facts, FakeDownload(), photo_dir=self.photos, topics_map={4615: "ADV350 6699 black new"})
        recs = {r["mid"]: r for r in self.lines()}
        self.assertEqual(recs[1001]["topic_id"], 4615)
        self.assertEqual(recs[1001]["topic_name"], "ADV350 6699 black new")
        self.assertIsNone(recs[1001]["reply_to"], "сообщение прямо в тему — не ответ")
        self.assertEqual(recs[1002]["topic_id"], 4615)
        self.assertEqual(recs[1002]["reply_to"], 1001)
        self.assertEqual(recs[1003]["topic_name"], sv.GENERAL_NAME)
        self.assertEqual(recs[1004]["topic_name"], "тема 9999")

    def test_topic_created_in_window_names_unknown_topic(self):
        facts = [fact(5000, action=sv.ACTION_TOPIC_CREATE, action_title="PCX 160 1234"),
                 fact(5001, "первое", topic=5000)]
        res = run(facts, FakeDownload(), photo_dir=self.photos, topics_map={})
        self.assertEqual(res["service"], 1)
        recs = self.lines()
        self.assertEqual(len(recs), 1, "служебное сообщение легло в архив перепиской")
        self.assertEqual(recs[0]["topic_name"], "PCX 160 1234")

    def test_window_seven_days(self):
        self.assertTrue(sv.in_window(NOW - datetime.timedelta(days=6, hours=23), NOW))
        self.assertFalse(sv.in_window(NOW - datetime.timedelta(days=7, seconds=1), NOW))
        self.assertFalse(sv.in_window(None, NOW))
        self.assertEqual((sv.WINDOW_DAYS, sv.WINDOW_LIMIT), (7, 300))

    def test_no_person_in_line(self):
        """Автор — только псевдоним от `str(sender_id)`, та же форма, что у `export_all.py`."""
        run([fact(1101, "звони @vasya_ivanov +66 81 234 5678", sender=123456789)],
            FakeDownload(), photo_dir=self.photos)
        rec = self.lines()[0]
        self.assertEqual(rec["who_ref"], store.who_ref("123456789"))
        body = json.dumps(rec, ensure_ascii=False)
        for bad in ("123456789", "vasya_ivanov", "812345678"):
            self.assertNotIn(bad, body, bad)
        for field in ("name", "username", "phone", "first_name"):
            self.assertNotIn(field, rec)

    def test_manifest_gets_servicing_forum(self):
        run([fact(1201, "a", when=datetime.datetime(2026, 9, 20, 3, 0, tzinfo=UTC)),
             fact(1202, "b", when=datetime.datetime(2026, 9, 22, 3, 0, tzinfo=UTC))],
            FakeDownload(), photo_dir=self.photos)
        g = store.read_manifest()["groups"][sv.GROUP_SLUG]
        self.assertEqual(g["group_id"], -1002751134848)
        self.assertEqual(g["kind"], "forum")
        self.assertEqual(g["sources"], [sv.SOURCE])
        self.assertEqual((g["first_day"], g["last_day"], g["messages"]),
                         ("2026-09-20", "2026-09-22", 2))

    def test_return_carries_numbers_only(self):
        """Возврат захода уходит в stdout → в отчёт очереди. Текста переписки в нём нет."""
        res = run([fact(1301, "СЕКРЕТНАЯ_ФРАЗА_ТЕСТА", media=sv.PHOTO)],
                  FakeDownload(fail=RuntimeError("x")), photo_dir=self.photos)
        self.assertNotIn("СЕКРЕТНАЯ_ФРАЗА_ТЕСТА", json.dumps(res, ensure_ascii=False))

    def test_topics_map_reader_has_third_outcome(self):
        got, why = sv.load_topics(os.path.join(self.tmp, "нет_такого.json"))
        self.assertIsNone(got)
        self.assertTrue(why)
        live, err = sv.load_topics()          # чтение карты тем — только чтение
        self.assertIsNone(err)
        self.assertTrue(live)
        self.assertTrue(all(isinstance(k, int) for k in live))


# ─────────────────────────── пароли и ключи ───────────────────────────

class TestSecrets(unittest.TestCase):
    def test_labeled_secret_masked_label_kept(self):
        for raw, gone in (("пароль от вайфая: turbo2026!", "turbo2026!"),
                          ("wifi password=Qwerty77", "Qwerty77"),
                          ("pin 4521 от сейфа", "4521"),
                          ("пин-код: 0000", "0000"),
                          ("รหัสผ่าน: abc12345", "abc12345")):
            out = store.scrub(raw)
            self.assertNotIn(gone, out, raw)
            self.assertIn(store.SECRET_MARK, out, raw)

    def test_bare_keys_masked(self):
        tok = "123456789:" + "A" * 35
        for raw in ("бот " + tok, "ключ sk-ant-" + "x" * 24, "ghp_" + "a" * 30):
            out = store.scrub(raw)
            self.assertIn(store.SECRET_MARK, out, raw)
        self.assertNotIn("AAAAAAAAAA", store.scrub("бот " + tok))

    def test_work_words_survive(self):
        """«код ошибки», «ключ от байка», «pass the bike» — смысл архива, а не секрет."""
        for keep in ("код ошибки P0301", "ключ от байка у Сома", "pass the bike to Joe",
                     "пароль от вайфая спроси у админа", "цена 4500 бат"):
            self.assertEqual(store.scrub(keep), keep, keep)

    def test_secret_scrub_is_idempotent(self):
        for raw in ("пароль: turbo2026", "pin 4521", "token=" + "123456789:" + "B" * 35):
            once = store.scrub(raw)
            self.assertEqual(store.scrub(once), once, raw)


# ─────────────────────────── наружу ничего ───────────────────────────

class TestNothingGoesOut(unittest.TestCase):
    OUT_CALLS = ("send_message", "send_file", "forward_messages", "forward_to", "edit_message",
                 "delete_messages", "reply", "respond", "send_read_acknowledge", "pin_message",
                 "start")

    def _calls(self, name):
        with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        return {n.func.attr for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}

    def test_fetch_has_no_outgoing_calls(self):
        calls = self._calls("chatlog_servicing_fetch.py")
        for bad in self.OUT_CALLS:
            self.assertNotIn(bad, calls, bad)

    def test_logic_has_no_outgoing_calls_and_no_telethon(self):
        calls = self._calls("chatlog_servicing.py")
        for bad in self.OUT_CALLS:
            self.assertNotIn(bad, calls, bad)
        with io.open(os.path.join(HERE, "chatlog_servicing.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertNotIn("telethon", mods)

    def test_photo_dir_is_git_path_not_tmp_not_chatlog(self):
        d = os.path.relpath(sv.PHOTO_DIR, HERE).replace("\\", "/")
        self.assertEqual(d, "docs/obsluzhivanie-snimki")
        try:
            r = subprocess.run(["git", "check-ignore", "-q", d + "/2026-09/x_msg1.jpg"],
                               cwd=HERE, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.skipTest("git недоступен: %s" % type(e).__name__)
        self.assertEqual(r.returncode, 1, "папка снимков попала под .gitignore")


if __name__ == "__main__":
    unittest.main()
