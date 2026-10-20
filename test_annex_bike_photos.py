# -*- coding: utf-8 -*-
"""Регресс `annex_bike_photos` без сети: карта байк → тема, ручной отбор снимков темы, архив,
и что наружу файл не пишет ничего."""

import io
import os
import ast
import json
import asyncio
import zipfile
import datetime
import tempfile
import subprocess
import unittest

import annex_bike_photos as abp

HERE = os.path.dirname(os.path.abspath(__file__))


class TestBikeMap(unittest.TestCase):
    def test_38_rows_in_annex_order(self):
        self.assertEqual([no for no, _n, _t in abp.BIKES], list(range(1, 39)))
        names = [n for _no, n, _t in abp.BIKES]
        self.assertEqual(len(set(names)), 38)

    def test_topics_exist_unique_and_carry_same_number(self):
        with io.open(os.path.join(HERE, "topics_map.json"), encoding="utf-8") as f:
            topics = {int(k): v for k, v in json.load(f).items()}
        tids = [t for _no, _n, t in abp.BIKES if t is not None]
        self.assertEqual(len(tids), len(set(tids)), "одна тема на два байка")
        for no, name, tid in abp.BIKES:
            if tid is None:
                continue
            self.assertIn(tid, topics, "№%d: темы %d нет в topics_map.json" % (no, tid))
            self.assertEqual(abp.trailing_number(topics[tid]), abp.trailing_number(name),
                             "№%d: номер темы «%s» не совпал с «%s»" % (no, topics[tid], name))

    def test_only_25_has_no_topic(self):
        self.assertEqual([no for no, _n, t in abp.BIKES if t is None], [25])

    def test_override_names_topic_for_bike_without_one(self):
        self.assertEqual(abp.parse_topic_overrides(["25=455"]), {25: 455})
        p = dict((no, t) for no, _n, t in abp.plan({25: 455}))
        self.assertEqual(p[25], 455)
        self.assertEqual(p[1], 62)


class TestAnnexMapAgrees(unittest.TestCase):
    """Две карты одних и тех же 38 байков — для фото (здесь) и для книг (annex4/annex_map.json) —
    обязаны совпадать по номеру и имени дословно, иначе фото ляжет к чужой книге."""

    def test_same_numbers_and_names(self):
        with io.open(os.path.join(HERE, "annex4", "annex_map.json"), encoding="utf-8") as f:
            amap = json.load(f)
        self.assertEqual([(b["no"], b["name"]) for b in amap["bikes"]],
                         [(no, n) for no, n, _t in abp.BIKES])

    def test_every_attached_book_page_has_vin_and_source(self):
        with io.open(os.path.join(HERE, "annex4", "annex_map.json"), encoding="utf-8") as f:
            amap = json.load(f)
        for b in amap["bikes"]:
            if b["status"] == "ok":
                self.assertTrue(b.get("vin") and b.get("reg_src", {}).get("drive_id"), b["no"])
            else:
                self.assertNotIn("reg_src", b, b["no"])


class _Msg:
    def __init__(self, mid, photo):
        self.id = mid
        self.photo = object() if photo else None
        self.document = None


class _Client:
    def __init__(self, msgs):
        self.msgs = msgs
        self.calls = []

    def iter_messages(self, entity, **kw):
        self.calls.append(kw)
        seq = list(reversed(self.msgs)) if kw.get("reverse") else list(self.msgs)
        seq = seq[: kw.get("limit")]

        async def gen():
            for m in seq:
                yield m
        return gen()


class TestTopicPhotos(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_filters_photos_by_hand_and_stops_at_want(self):
        msgs = [_Msg(10, False), _Msg(9, True), _Msg(8, False), _Msg(7, True), _Msg(6, True)]
        c = _Client(msgs)
        got = self._run(abp.topic_photos(c, "e", 62, 2, reverse=False,
                                         is_image=lambda m: m.photo is not None))
        self.assertEqual([m.id for m in got], [9, 7])
        self.assertEqual(c.calls[0]["reply_to"], 62)
        self.assertNotIn("filter", c.calls[0], "filter= с reply_to Telethon молча игнорирует")

    def test_reverse_takes_earliest(self):
        msgs = [_Msg(10, True), _Msg(9, True), _Msg(2, True), _Msg(1, False)]
        got = self._run(abp.topic_photos(_Client(msgs), "e", 1, 1, reverse=True,
                                         is_image=lambda m: m.photo is not None))
        self.assertEqual([m.id for m in got], [2])

    def test_scan_cap_bounds_reading(self):
        c = _Client([_Msg(i, False) for i in range(100, 0, -1)])
        got = self._run(abp.topic_photos(c, "e", 5, 3, reverse=False, is_image=lambda m: False,
                                         scan_cap=7))
        self.assertEqual(got, [])
        self.assertEqual(c.calls[0]["limit"], 7)


class TestFilesAndZip(unittest.TestCase):
    def test_photo_name_is_phuket_date_and_message_id(self):
        when = datetime.datetime(2026, 10, 19, 20, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(abp.photo_name(when, 123), "2026-10-20_m123.jpg")

    def test_bike_dir_is_numbered_slug(self):
        d = abp.bike_dir(7, "MT-03 300CC BLUE PHUKET 5068", out_dir="/x")
        self.assertEqual(os.path.basename(d), "07_MT_03_300CC_BLUE_PHUKET_5068")

    def test_zip_keeps_folder_per_bike(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "annex_bike_photos")
            os.makedirs(os.path.join(src, "01_A"))
            with open(os.path.join(src, "01_A", "2026-10-20_m1.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff")
            zp = os.path.join(t, "a.zip")
            self.assertEqual(abp.make_zip(src, zp), 1)
            with zipfile.ZipFile(zp) as z:
                self.assertEqual(z.namelist(), ["annex_bike_photos/01_A/2026-10-20_m1.jpg"])

    def test_out_dir_is_ignored_tmp(self):
        rel = os.path.relpath(abp.OUT_DIR, HERE).replace("\\", "/")
        self.assertEqual(rel, "tmp/annex_bike_photos")
        try:
            r = subprocess.run(["git", "check-ignore", "-q", rel + "/01_x/a.jpg"], cwd=HERE,
                               capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.skipTest("git недоступен: %s" % type(e).__name__)
        self.assertEqual(r.returncode, 0, "снимки байков не должны попадать в git")


class TestNothingGoesOut(unittest.TestCase):
    OUT_CALLS = ("send_message", "send_file", "forward_messages", "forward_to", "edit_message",
                 "delete_messages", "reply", "respond", "send_read_acknowledge", "pin_message",
                 "start")

    def test_no_outgoing_calls(self):
        with io.open(os.path.join(HERE, "annex_bike_photos.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        calls = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for bad in self.OUT_CALLS:
            self.assertNotIn(bad, calls, bad)


if __name__ == "__main__":
    unittest.main()
