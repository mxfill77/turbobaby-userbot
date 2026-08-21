# -*- coding: utf-8 -*-
"""Юниты ВРЕЗКИ сторожа свежести (`price_gate`) — слоя, который включает сторож вместе
с переключением источника цены на записанное правило (решение владельца 17.08.2026).

ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ, А НЕ ДЕКЛАРИРУЕТСЯ:
  1. НЕСВЕЖЕЕ И НЕПРОВЕРЯЕМОЕ ГАСЯТ ЦЕНУ ОДИНАКОВО. Ни одна ветка не превращает «не смог
     проверить» в разрешение назвать число — ни отказ двери, ни бросок изнутри, ни битый
     вердикт.
  2. ПРОБА ОГРАНИЧЕНА ВРЕМЕНЕМ. Девять GET делят один бюджет; исчерпан — проба бросает,
     и это исход `НЕИЗВЕСТНО`, а не зависший путь ответа. Замок заведён по живому случаю
     20.08: те же пробы шли 306с, 599с и один раз не вернулись (2026-08-20-silent-timeout.md).
  3. КЭШ НЕ ВРЁТ. Второй вопрос в окне TTL к мосту НЕ ХОДИТ (считаем вызовы), а инъекция
     фикстуры кэш не пишет — иначе тестовая ручка протекла бы в боевой путь того же процесса.

ЖИВОЙ ФОРМАТ (правило-класс CLAUDE.md): мок двери повторяет ответ `quote_price` — положение
ручки лежит в `season.global_discount` ответа, ровно оттуда его берут боевые руки
(`price_freshness_run.live_handles`). Слепок берётся из НАСТОЯЩЕГО `price_source.json`,
а не сочиняется: разъедется файл — покраснеют эти тесты, и это правильно.
"""
import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_isolation  # noqa: F401  ДО suggest: офлайн-дверь сторожа свежести (§5 обвязки)
import price_freshness
import price_freshness_run
import price_gate
import suggest

# Байк → ячейка ручки: разбор тот же, что у боевых проб.
_CELL_OF = {bike: cell for cell, bikes in price_freshness_run.PROBES for bike in bikes}

# Живое положение ручек из настоящего слепка (H3/I3/J3). Сходится — СВЕЖЕЕ.
_SNAP, _WHY = price_freshness_run.read_rule()


def door(handles, fail=(), boom=False):
    """Мок двери `quote_price`. `handles` — {ячейка: угол ручки}; `fail` — байки, на которых
    дверь отказывает; `boom` — дверь бросает (отказ транспорта, а не отрицательный ответ)."""
    calls = []

    def get(action, **kw):
        calls.append((action, kw.get("bike")))
        if boom:
            raise RuntimeError("мост не ответил")
        bike = kw.get("bike")
        if bike in fail:
            return {"ok": False, "error": "дверь отказала"}
        value = handles.get(_CELL_OF.get(bike))
        if value is None:
            return {"ok": True, "season": None}
        return {"ok": True, "season": {"global_discount": value}, "day_price": 317}

    get.calls = calls
    return get


class GateBase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (price_gate.TTL_ENV, price_gate.PROBE_ENV,
                                                      price_freshness.MAX_AGE_ENV)}
        price_gate.reset()

    def tearDown(self):
        price_gate.reset()
        for name, saved in self._saved.items():
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved

    def fresh_handles(self):
        self.assertIsNotNone(_SNAP, "слепок не прочитан: %s" % _WHY)
        return dict(_SNAP["handles"])

    def at_snapshot_date(self):
        """«Сегодня» = дата слепка: возрастная ветка улик не даёт, судим ручки."""
        return _SNAP["taken"]


# ───────────────────────── 1. три исхода доезжают до пути ответа ─────────────────────────

class TestOutcomes(GateBase):
    def test_agreed_handles_and_young_snapshot_allow_the_price(self):
        may, card = price_gate.allow(env={}, now=self.at_snapshot_date(),
                                     get=door(self.fresh_handles()))
        self.assertTrue(may)
        self.assertIsNone(card)

    def test_turned_handle_forbids_the_price_and_calls_the_owner(self):
        moved = self.fresh_handles()
        cell = sorted(moved)[0]
        moved[cell] = moved[cell] + 0.10          # владелец повернул ручку сезона
        may, card = price_gate.allow(env={}, now=self.at_snapshot_date(), get=door(moved))
        self.assertFalse(may)
        self.assertIn("ЦЕНА НЕ НАЗВАНА", card)
        self.assertIn("УСТАРЕЛО", card)
        self.assertIn(cell, card)

    def test_dead_door_forbids_the_price_too(self):
        may, card = price_gate.allow(env={}, now=self.at_snapshot_date(),
                                     get=door({}, boom=True))
        self.assertFalse(may)
        self.assertIn("ПРОВЕРИТЬ НЕ УДАЛОСЬ", card)

    def test_old_snapshot_forbids_the_price_even_with_agreed_handles(self):
        # Ручки сошлись, но слепок пережил объявленный круг пересмотра цен.
        old = self.at_snapshot_date() + datetime.timedelta(days=90)
        may, card = price_gate.allow(env={}, now=old, get=door(self.fresh_handles()))
        self.assertFalse(may)
        self.assertIn("УСТАРЕЛО", card)

    def test_silence_is_never_turned_into_permission(self):
        # Ни один из отказных входов не выдаёт разрешения назвать цену.
        for name, g in (("дверь бросает", door({}, boom=True)),
                        ("дверь отказывает", door(self.fresh_handles(),
                                                  fail=tuple(_CELL_OF))),
                        ("ручки в ответе нет", door({}))):
            may, card = price_gate.allow(env={}, now=self.at_snapshot_date(), get=g)
            self.assertFalse(may, name)
            self.assertTrue(card, name)


# ───────────────────────── 2. бюджет времени и кэш ─────────────────────────

class TestBudgetAndCache(GateBase):
    def test_probe_budget_stops_the_walk_instead_of_hanging(self):
        clock = {"t": 0.0}

        def tick():
            return clock["t"]

        def slow(action, **kw):
            clock["t"] += 5.0                      # каждая проба «идёт» 5 секунд
            return {"ok": True, "season": {"global_discount": 0.15}}

        bounded = price_gate._bounded(slow, 12.0, tick)
        got = []
        for i in range(9):
            try:
                bounded("quote_price", bike="b%d" % i)
                got.append(i)
            except RuntimeError:
                break
        # Бюджет 12с при пробе 5с пускает три и обрывает — а не идёт все девять.
        self.assertEqual(got, [0, 1, 2])
        self.assertGreaterEqual(clock["t"], 12.0)

    def test_second_question_inside_the_window_does_not_touch_the_door(self):
        g = door(self.fresh_handles())
        clock = {"t": 100.0}
        env = {price_gate.TTL_ENV: "30"}
        # Боевой путь кэша: подменяем добытчика фактов, а не инъекцию (инъекция кэш не пишет).
        real = price_freshness_run.live_handles
        price_freshness_run.live_handles = lambda get=None: real(get=g)
        try:
            first = price_gate.verdict(env=env, now=self.at_snapshot_date(),
                                       clock=lambda: clock["t"])
            after_first = len(g.calls)
            clock["t"] += 60.0                     # минута — внутри окна 30 мин
            second = price_gate.verdict(env=env, now=self.at_snapshot_date(),
                                        clock=lambda: clock["t"])
            self.assertIs(second, first)
            self.assertEqual(len(g.calls), after_first)
            clock["t"] += 30 * 60.0                # окно истекло — дверь спрашивают снова
            price_gate.verdict(env=env, now=self.at_snapshot_date(),
                               clock=lambda: clock["t"])
            self.assertGreater(len(g.calls), after_first)
        finally:
            price_freshness_run.live_handles = real

    def test_injected_fixture_never_leaks_into_the_cache(self):
        price_gate.verdict(env={price_gate.TTL_ENV: "30"}, now=self.at_snapshot_date(),
                           get=door(self.fresh_handles()))
        self.assertIsNone(price_gate._cache["verdict"])

    def test_declared_rollback_costs_nothing(self):
        # TTL=0 — врезка мертва: сторож не спрашивается вовсе, дверь не трогается.
        g = door({}, boom=True)
        may, card = price_gate.allow(env={price_gate.TTL_ENV: "0"}, get=g)
        self.assertTrue(may)
        self.assertIsNone(card)
        self.assertEqual(g.calls, [])


# ───────────────────────── 3. форма гашения и врезка в путь ответа ─────────────────────────

class TestQuenchShape(GateBase):
    def test_quench_literal_matches_what_the_answer_path_returns(self):
        # `price_freshness` копирует форму гашения намеренно (не импортирует того, за кем
        # следит). Совпадение держится ЭТИМ тестом, а не обещанием.
        self.assertEqual(price_freshness.QUENCH, {"status": "error", "quote": None})

    def test_answer_path_asks_the_gate_before_pricing(self):
        seen = {"asked": False}

        def fake_allow(*a, **kw):
            seen["asked"] = True
            return False, "ЦЕНА НЕ НАЗВАНА: проба"

        def never(*a, **kw):
            raise AssertionError("счёт по файлу не должен звучать при запрете сторожа")

        real_allow, real_reprice = price_gate.allow, suggest.price_source.reprice
        price_gate.allow, suggest.price_source.reprice = fake_allow, never
        try:
            out = suggest._safe_quote_for_model(
                "NMAX 155", "2026-01-29", "2026-02-05",
                getter=lambda params: {"ok": True, "data": {
                    "day_price": 317, "total": 2217, "deposit": 3000, "available": True,
                    "days": 7, "cap_active": False, "cap_price": None}})
        finally:
            price_gate.allow, suggest.price_source.reprice = real_allow, real_reprice
        self.assertTrue(seen["asked"])
        self.assertEqual(out, price_freshness.QUENCH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
