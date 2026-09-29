# -*- coding: utf-8 -*-
"""Регресс трёх инструментов чтения агента-черновика (aimgr_tools, задание 0058-75a.3009).

Всё на ВЫДУМАННЫХ фикстурах: номера байков, модели и тексты записей придуманы, сети нет —
транспорт двери цены и чтение истории байка поданы инъекцией, мост не вызывается ни одной веткой.
Запуск: TURBOBABY_TEST_LOGS=1 python -m unittest test_aimgr_tools"""
import datetime
import os
import unittest
from unittest import mock

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import aimgr_tools as T
import pricing
import suggest

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date(2026, 9, 30)
FLEET = [{"name": "FORZA 350 1111"}, {"name": "FORZA 350 2222"}, {"name": "NMAX 155 3333"}]


def door(answers):
    """Выдуманная дверь цены: answers[имя юнита] → тело ответа (или исключение)."""
    def get(params):
        a = answers.get(params.get("bike"))
        if isinstance(a, Exception):
            raise a
        return a
    return get


def quote_ok(available=None, **kw):
    body = {"ok": True, "day_price": 500, "total": 2500}
    if available is not None:
        body["available"] = available
    body.update(kw)
    return body


class FreeBikesTest(unittest.TestCase):
    def setUp(self):
        p = [mock.patch.object(pricing, "PRICING_ACTION", "quote_price"),
             mock.patch.object(pricing, "BRIDGE_URL", "http://door.invalid"),
             mock.patch.object(pricing, "BRIDGE_TOKEN", "t"),
             mock.patch.object(pricing, "_FLEET_CACHE", {"ts": 0.0, "data": None})]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def run_door(self, answers, model="FORZA 350"):
        return T.free_bikes(model, ("2026-10-05", "2026-10-10"), get=door(answers), fleet=FLEET)

    def test_busy_unit_is_busy(self):
        r = self.run_door({"FORZA 350 1111": quote_ok(False), "FORZA 350 2222": quote_ok(False)})
        self.assertEqual(r["verdict"], T.BUSY)
        self.assertEqual([u["state"] for u in r["units"]], [T.BUSY, T.BUSY])

    def test_no_available_field_is_unchecked(self):
        r = self.run_door({"FORZA 350 1111": quote_ok(), "FORZA 350 2222": quote_ok()})
        self.assertEqual(r["verdict"], T.UNCHECKED)
        self.assertNotIn(r["verdict"], (T.FREE, T.BUSY))

    def test_one_free_unit_makes_model_free(self):
        r = self.run_door({"FORZA 350 1111": quote_ok(False), "FORZA 350 2222": quote_ok(True)})
        self.assertEqual(r["verdict"], T.FREE)
        self.assertEqual(r["free"], ["FORZA 350 2222"])

    def test_busy_plus_unchecked_is_not_busy(self):
        # Отличие от сетки: непрочитанный юнит мог быть свободен — «занят» клиенту не говорим.
        r = self.run_door({"FORZA 350 1111": quote_ok(False), "FORZA 350 2222": quote_ok()})
        self.assertEqual(r["verdict"], T.UNCHECKED)

    def test_door_silence_is_unchecked(self):
        r = self.run_door({"FORZA 350 1111": OSError("сеть"), "FORZA 350 2222": {"ok": False}})
        self.assertEqual(r["verdict"], T.UNCHECKED)
        self.assertEqual([u["state"] for u in r["units"]], [T.UNCHECKED, T.UNCHECKED])

    def test_fleet_unread_is_named(self):
        r = T.free_bikes("FORZA 350", ("2026-10-05", "2026-10-10"), get=lambda p: {"ok": False})
        self.assertEqual((r["verdict"], r["why"]), (T.UNCHECKED, "парк не прочитан"))


def ev(day, notes="", event_type="repair", status=""):
    return {"msg_date": day + " 10:00:00", "recorded_at": day + "T10:00:05Z", "event_type": event_type,
            "mileage": "", "notes": notes, "status": status}


def reader(events, services=()):
    return lambda bike: (events, list(services))


class BikeHistoryTest(unittest.TestCase):
    BIKE = "ADV 350 4444"

    def hist(self, events, services=(), **kw):
        return T.bike_history(self.BIKE, today=TODAY, read=reader(events, services), **kw)

    def test_steering_bearings_is_repair(self):
        r = self.hist([ev("2026-09-29", "рулевые подшипники стучат")])
        self.assertEqual(r["verdict"], T.REPAIR)
        self.assertIn("спросить тайцев о дате готовности", r["text"])

    def test_pads_to_metal_and_oil_overdue(self):
        svc = [{"bike": "ADV 350 4444", "service_type": "oil", "current_km": 9888, "last_service_km": 4000,
                "interval_km": 4000, "next_km": 8000, "status": "overdue"}]
        r = self.hist([ev("2026-09-28", "задние колодки до металла")], svc)
        self.assertEqual(r["verdict"], T.REPAIR)
        self.assertEqual(r["marks"], ["срочно масло (просрочено на 1888 км)"])
        self.assertIn("пометка: срочно масло", r["text"])

    def test_dirty_available_with_wash_mark(self):
        r = self.hist([ev("2026-09-29", "вернули, байк грязный", event_type="return")])
        self.assertEqual(r["verdict"], T.AVAILABLE)
        self.assertEqual(r["text"], "доступен, пометка: помыть")

    def test_wash_after_dirty_clears_mark(self):
        r = self.hist([ev("2026-09-30", "мойка: «готово»", event_type="wash"),
                       ev("2026-09-29", "байк грязный", event_type="return")])
        self.assertEqual(r["text"], "доступен")

    def test_only_august_records_ask_thai(self):
        r = self.hist([ev("2026-08-28", "масло заменено"), ev("2026-08-15", "тормоза сделаны")])
        self.assertEqual(r["verdict"], T.ASK_THAI)
        self.assertEqual(r["last_day"], "2026-08-28")

    def test_days_parameter_default_three(self):
        events = [ev("2026-09-26", "пробег записан")]
        self.assertEqual(self.hist(events)["verdict"], T.ASK_THAI)
        self.assertEqual(self.hist(events, days=5)["verdict"], T.AVAILABLE)

    def test_unread_source_is_unmeasured(self):
        def boom(bike):
            raise OSError("мост молчит")
        self.assertEqual(T.bike_history(self.BIKE, today=TODAY)["verdict"], T.UNMEASURED)
        self.assertEqual(T.bike_history(self.BIKE, today=TODAY, read=boom)["verdict"], T.UNMEASURED)
        self.assertEqual(self.hist(None)["verdict"], T.UNMEASURED)

    def test_other_bike_service_not_counted(self):
        svc = [{"bike": "PCX 160 5555", "service_type": "oil", "current_km": 9000, "next_km": 8000,
                "status": "overdue"}]
        r = self.hist([ev("2026-09-29", "пробег записан")], svc)
        self.assertEqual((r["verdict"], r["marks"]), (T.AVAILABLE, []))

    def test_bridge_reader_adapter(self):
        calls = []

        def call(action, **params):
            calls.append(action)
            if action == "read_events":
                return {"ok": True, "items": [ev("2026-09-29", "пробег записан")]}
            return {"ok": True, "items": []}
        self.assertEqual(T.bike_history(self.BIKE, today=TODAY, read=T.bridge_reader(call))["verdict"],
                         T.AVAILABLE)
        self.assertEqual(calls, ["read_events", "service_list"])
        bad = T.bridge_reader(lambda action, **p: {"ok": False, "items": []})
        self.assertEqual(T.bike_history(self.BIKE, today=TODAY, read=bad)["verdict"], T.UNMEASURED)

    # --- день события из живого msg_date (задание 0063-75f.3009) ---
    LIVE_MSG_DATE = "Tue Sep 29 2026 00:00:00 GMT+0700 (เวลาอินโดจีน)"   # живой вид, AIMGRLIVE3009 §4

    def test_live_msg_date_form_gives_event_day(self):
        # recorded_at на три дня раньше: если msg_date не разобран, запись выпадет из свежих.
        live = {"msg_date": self.LIVE_MSG_DATE, "recorded_at": "2026-09-26T04:25:08.567Z",
                "event_type": "photo", "mileage": "", "notes": "", "status": "recorded"}
        r = self.hist([live])
        self.assertEqual(r["last_day"], "2026-09-29")
        self.assertEqual(r["verdict"], T.AVAILABLE)
        self.assertEqual(r["day_src"], {"msg_date": 1, "recorded_at": 0})
        self.assertEqual(r["text"], "доступен")

    def test_parse_day_all_live_forms(self):
        d = datetime.date(2026, 9, 29)
        self.assertEqual(T._parse_day(self.LIVE_MSG_DATE), d)
        self.assertEqual(T._parse_day("Tue Sep 29 2026"), d)
        self.assertEqual(T._parse_day("2026-09-28T17:00:00.000Z"), d)    # полночь Пхукета в UTC
        self.assertEqual(T._parse_day("2026-09-29T04:25:08.567Z"), d)
        self.assertEqual(T._parse_day("2026-09-29 10:00:00"), d)
        self.assertEqual(T._parse_day("29.09.2026"), d)
        self.assertIsNone(T._parse_day("вчера"))
        self.assertIsNone(T._parse_day(""))

    def test_recorded_at_fallback_is_visible(self):
        for md in ("", "вчера"):
            e = {"msg_date": md, "recorded_at": "2026-09-28T18:30:00Z", "event_type": "photo",
                 "notes": "", "status": "recorded"}
            r = self.hist([e])
            self.assertEqual(r["last_day"], "2026-09-29")            # 18:30 UTC = 01:30 Пхукета
            self.assertEqual(r["day_src"], {"msg_date": 0, "recorded_at": 1})
            self.assertIn("день по recorded_at у 1 из 1 записей", r["text"])
            self.assertTrue(any("recorded_at" in w for w in r["why"]))

    def test_ready_fits_client_date(self):
        d = datetime.date
        self.assertIs(T.ready_fits(d(2026, 10, 5), d(2026, 10, 5)), True)
        self.assertIs(T.ready_fits(d(2026, 10, 7), d(2026, 10, 5)), False)
        self.assertIsNone(T.ready_fits(None, d(2026, 10, 5)))


BOOK = """# Книга (выдуманная)

## Стиль общения
- Коротко и вежливо.

## Выученные правила
- (2026-07-01) правило А
- (2026-07-02) правило Б
- правило В
"""


class RulesTest(unittest.TestCase):
    def patch(self, bullets, read_ok=True):
        for x in (mock.patch.object(suggest, "load_playbook_file", lambda: BOOK.strip()),
                  mock.patch.object(suggest, "active_lesson_bullets", lambda path=None: (bullets, read_ok))):
            x.start()
            self.addCleanup(x.stop)

    def test_text_is_exactly_prompt_playbook(self):
        self.patch(("- (2026-09-01) правило Б", "- (2026-09-02) правило Г"))
        r = T.rules()
        self.assertEqual(r["text"], suggest.load_playbook())
        self.assertIn("правило Г", r["text"])
        self.assertNotIn("правило А", r["text"])
        self.assertEqual([n for n, _s, _t in r["numbered"]], [1, 2, 3])
        self.assertEqual(r["numbered"][2][1], "Выученные правила")

    def test_divergence_counted(self):
        self.patch(("- (2026-09-01) правило Б", "- правило В", "- правило Г", "- правило Д"))
        r = T.rules()
        self.assertEqual((r["book_learned"], r["base_active"], r["only_in_book"], r["only_in_base"]),
                         (3, 4, 1, 2))

    def test_base_unread_divergence_unmeasured(self):
        self.patch((), read_ok=False)
        r = T.rules()
        self.assertEqual(r["source"], "книга-снимок")
        self.assertIsNone(r["only_in_book"])
        self.assertEqual(r["text"], BOOK.strip())


ARCHIVE = os.path.join(HERE, "fixtures", "aimgr_servicing")       # выдуманный архив, 4 дня, 4 темы
NOW = datetime.datetime(2026, 9, 24, 0, 0, tzinfo=datetime.timezone.utc)
AGE_HEAD = "возраст архива: последняя строка 2026-09-23 08:46 UTC — 0.6 сут назад"


class BikeTopicMsgsTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"CHATLOG_ROOT": ARCHIVE})
        p.start()
        self.addCleanup(p.stop)

    def msgs(self, bike, **kw):
        kw.setdefault("now", NOW)
        return T.bike_topic_msgs(bike, **kw)

    def assert_age_first(self, r):
        first = r["text"].splitlines()[0]
        self.assertTrue(first.startswith(AGE_HEAD), first)
        self.assertIn("порога-отказа нет", first)

    def test_found_rows_age_first_then_envelope(self):
        import chatlog_find
        r = self.msgs("ADV 350 GREY 798")
        self.assertEqual(r["verdict"], T.FOUND)
        self.assert_age_first(r)
        lines = r["text"].splitlines()
        self.assertEqual(lines[1], chatlog_find.ENVELOPE)
        self.assertEqual(r["topic"], {"id": 64, "name": "ADV 350 GREY 798", "rows_all": 3})
        self.assertEqual(r["counts"], {"window": 3, "shown": 3, "photo": 1, "human_text": 1})
        self.assertEqual([v["ts"][:10] for v in r["rows"]], ["2026-09-21", "2026-09-22", "2026-09-23"])
        self.assertEqual([(v["photo"], v["bot"]) for v in r["rows"]], [(True, False), (False, True), (False, False)])
        self.assertEqual(r["rows"][0]["files"],
                         ["docs/obsluzhivanie-snimki/2026-09/2026-09-21_17-00-00_t64_msg101.jpg"])
        self.assertIn("человек · фото: да, docs/obsluzhivanie-snimki/", lines[3])
        self.assertIn("бот · фото: нет · выдуманный ответ бота", lines[4])

    def test_limit_keeps_latest(self):
        r = self.msgs("ADV 350 GREY 798", limit=2)
        self.assertEqual((r["counts"]["window"], r["counts"]["shown"]), (3, 2))
        self.assertEqual([v["ts"][:10] for v in r["rows"]], ["2026-09-22", "2026-09-23"])

    def test_fleet_name_found_by_plate(self):
        r = self.msgs("ADV 350CC GREY BKK 798")
        self.assertEqual((r["verdict"], r["topic"]["id"]), (T.FOUND, 64))

    def test_topic_present_window_empty(self):
        r = self.msgs("NMAX 155 BLACK GOLD 4255", days=7)
        self.assertEqual(r["verdict"], T.EMPTY)
        self.assert_age_first(r)
        self.assertEqual(r["text"].splitlines()[1], "тема «NMAX 155 BLACK GOLD 4255»: сообщений нет за 7 суток")
        self.assertEqual(r["rows"], [])

    def test_no_topic_is_unknown_not_empty(self):
        r = self.msgs("PCX 160 9999")
        self.assertEqual(r["verdict"], "НЕИЗВЕСТНО")
        self.assert_age_first(r)
        self.assertTrue(r["text"].splitlines()[1].startswith("НЕИЗВЕСТНО: темы у байка в архиве нет"))
        self.assertNotIn("сообщений нет", r["text"])

    def test_archive_unread_is_unknown(self):
        with mock.patch.dict(os.environ, {"CHATLOG_ROOT": os.path.join(ARCHIVE, "нет_такого")}):
            r = self.msgs("ADV 350 GREY 798")
        self.assertEqual(r["verdict"], "НЕИЗВЕСТНО")
        self.assertEqual(r["text"].splitlines()[0], "возраст архива: неизвестен — последняя строка не найдена")
        self.assertIn("НЕИЗВЕСТНО: архив не прочитан", r["text"])
        self.assertNotIn("сообщений нет", r["text"])

        def boom():
            raise OSError("диск")
        r = self.msgs("ADV 350 GREY 798", read=boom)
        self.assertEqual(r["verdict"], "НЕИЗВЕСТНО")
        self.assertIn("архив не прочитан: OSError", r["text"])
        self.assertNotIn("сообщений нет", r["text"])

    def test_ambiguous_plate_is_unknown(self):
        r = self.msgs("PCX 160 4685")
        self.assertEqual(r["verdict"], "НЕИЗВЕСТНО")
        self.assertIn("подходят 2 темы", r["text"])
        self.assertEqual(r["rows"], [])


class ClosureTest(unittest.TestCase):
    def test_client_closure_does_not_import_module(self):
        import client_contour
        cl = client_contour.closure(repo=HERE)
        self.assertTrue(cl.ok, cl.reason)
        self.assertIn("suggest.py", cl.files)
        self.assertNotIn("aimgr_tools.py", cl.files)


if __name__ == "__main__":
    unittest.main()
