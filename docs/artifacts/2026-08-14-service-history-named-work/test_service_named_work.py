# -*- coding: utf-8 -*-
"""ЗАМОК В ОБЕ СТОРОНЫ: в историю обслуживания идёт НАЗВАННАЯ работа, а не ярлык.

Живой случай 14.08.2026, байк NMAX RED WHITE 9548: механик сделал замену аккумулятора,
в историю легло «прочие работы — 36474 км». Фраза механика взята ДОСЛОВНО (правило репо:
голден детекта — реальные слова клиента/механика, а не идеализированная формулировка).

Сторона 1: работа названа → в `add_event.notes` идут слова механика.
Сторона 2: работа не названа → идёт ярлык `_SP_KIND_LABEL` (и по нему видно, что имени не было).
Третье: РАСЧЁТ не тронут — регистры oil/gear/abs/airfilter пишутся ярлыком/ключом, как и писались.

Сети нет: bridge замокан целиком, _send/_sp_ceiling_stop перехвачены.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BRIDGE_URL", "http://x")
os.environ.setdefault("BRIDGE_TOKEN", "x")
import splinter as S  # noqa: E402

CHAT = -1002751134848
TOPIC = 77
BIKE = "NMAX RED WHITE 9548"
ODO = 36474


class FakeBridge:
    """Мок моста. `note` заявки — тот самый сегмент Z4 `WORKS:{…}`, который кладёт фаза 2."""

    def __init__(self, note=""):
        self.note = note
        self.events = []
        self.fleet = []
        self.upserts = []
        self.pending_get_calls = 0

    # ── чтение заявки (слова механика лежат здесь) ──
    def service_pending_get(self, chat_id, topic_id, bike):
        self.pending_get_calls += 1
        return {"ok": True, "item": {"note": self.note}}

    def service_pending_close(self, **kw):
        return {"ok": True}

    def find_bike(self, name):
        return {"name": BIKE}

    # ── запись ──
    def add_event(self, **kw):
        self.events.append(kw)
        return {"ok": True, "saved": True}

    def set_fleet_service(self, **kw):
        self.fleet.append(kw)
        return {"ok": True}

    def set_fleet_oil(self, **kw):
        self.fleet.append(kw)
        return {"ok": True}

    def service_upsert(self, **kw):
        self.upserts.append(kw)
        return {"ok": True}


async def _noop_send(*a, **kw):
    return None


async def _no_ceiling(*a, **kw):
    """Верхняя граница пробега встала 14.08 — её эта правка не трогает; в тесте не мешаем."""
    return False


def run_write(bridge, done, odo=ODO):
    S._send = _noop_send
    S._send_retry = _noop_send
    S._sp_ceiling_stop = _no_ceiling
    S._SVC_WRITE_DEDUP.clear()
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        S._sp_write_done(None, bridge, CHAT, TOPIC, BIKE, done, str(odo), confirmed_by="@owner"))


def notes_of(bridge):
    return [e["notes"] for e in bridge.events]


class NamedWorkGoesToHistory(unittest.TestCase):
    """Сторона 1 — механик работу НАЗВАЛ."""

    def test_live_case_9548_battery(self):
        b = FakeBridge(note="WORKS:{замена аккумулятора}")
        written, failed = run_write(b, ["other"])
        self.assertEqual(failed, [])
        self.assertEqual(written, ["other"])
        self.assertEqual(notes_of(b), ["замена аккумулятора — 36474 км"])
        self.assertNotIn("прочие работы", notes_of(b)[0])

    def test_msg_id_and_kind_unchanged(self):
        """Ключ идемпотентности — по КЛЮЧУ работы, а не по её словам: дедуп моста цел."""
        b = FakeBridge(note="WORKS:{замена аккумулятора}")
        run_write(b, ["other"])
        self.assertEqual(b.events[0]["msg_id"], f"sp:{CHAT}:{TOPIC}:other:{ODO}")
        self.assertEqual(b.events[0]["event_type"], "repair")
        self.assertEqual(b.events[0]["mileage"], str(ODO))

    def test_named_pads(self):
        b = FakeBridge(note="WORKS:{поменял передние колодки}")
        run_write(b, ["pads"])
        self.assertEqual(notes_of(b), ["поменял передние колодки — 36474 км"])

    def test_each_kind_takes_its_own_words(self):
        b = FakeBridge(note="WORKS:{замена аккумулятора; поменял колодки}")
        run_write(b, ["pads", "other"])
        self.assertEqual(notes_of(b),
                         ["поменял колодки — 36474 км", "замена аккумулятора — 36474 км"])

    def test_pending_read_once_per_run(self):
        """Слова читаются ЛЕНИВО и один раз на заход, а не на каждую позицию."""
        b = FakeBridge(note="WORKS:{замена аккумулятора; поменял колодки}")
        run_write(b, ["pads", "other", "chain"])
        self.assertEqual(b.pending_get_calls, 1)


class UnnamedWorkFallsBackToLabel(unittest.TestCase):
    """Сторона 2 — механик работу НЕ назвал: идёт ярлык, и это видно."""

    def test_no_words_in_note(self):
        b = FakeBridge(note="")
        run_write(b, ["other"])
        self.assertEqual(notes_of(b), ["прочие работы — 36474 км"])

    def test_words_of_another_kind_only(self):
        b = FakeBridge(note="WORKS:{поменял колодки}")
        run_write(b, ["other"])
        self.assertEqual(notes_of(b), ["прочие работы — 36474 км"])

    def test_bridge_read_failed_is_fail_safe(self):
        """Заявку прочитать не удалось → поведение ДОСЛОВНО прежнее (ярлык), а не пустая строка."""
        b = FakeBridge(note="WORKS:{замена аккумулятора}")

        def boom(*a, **kw):
            raise RuntimeError("мост молчит")

        b.service_pending_get = boom
        written, failed = run_write(b, ["other"])
        self.assertEqual(failed, [])
        self.assertEqual(notes_of(b), ["прочие работы — 36474 км"])

    def test_chain_label(self):
        b = FakeBridge(note="")
        run_write(b, ["chain"])
        self.assertEqual(notes_of(b), ["цепь — 36474 км"])


class CalculationUntouched(unittest.TestCase):
    """РАСЧЁТ НЕ ТРОГАЕМ: масло/редуктор/ABS/фильтр считаются по ярлыку (ключу), а не по словам."""

    def test_registers_still_written_by_kind(self):
        b = FakeBridge(note="WORKS:{поменял моторное масло; масло редуктора}")
        written, failed = run_write(b, ["oil", "gear"])
        self.assertEqual(failed, [])
        self.assertEqual(written, ["oil", "gear"])
        self.assertEqual(b.events, [], "регистр — не событие: строк истории тут быть не должно")
        self.assertEqual([u["service_type"] for u in b.upserts], ["oil", "gear"])
        self.assertEqual([u["last_service_km"] for u in b.upserts], [ODO, ODO])

    def test_register_run_does_not_read_pending(self):
        """Колоночная запись за слова механика обращением к мосту НЕ платит."""
        b = FakeBridge(note="WORKS:{поменял моторное масло}")
        run_write(b, ["oil"])
        self.assertEqual(b.pending_get_calls, 0)

    def test_mixed_register_and_event(self):
        b = FakeBridge(note="WORKS:{поменял моторное масло; замена аккумулятора}")
        written, failed = run_write(b, ["oil", "other"])
        self.assertEqual(failed, [])
        self.assertEqual(written, ["oil", "other"])
        self.assertEqual([u["service_type"] for u in b.upserts], ["oil"])
        self.assertEqual(notes_of(b), ["замена аккумулятора — 36474 км"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
