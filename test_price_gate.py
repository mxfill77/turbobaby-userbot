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
import time
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


# ───────────────── 2б. ВТОРОЙ ЭТАЖ БЮДЖЕТА: СВОЙ ПРЕДЕЛ У КАЖДОЙ ПРОБЫ (06.09.2026) ─────────
#
# ЗАМЕР, НА КОТОРОМ СТОИТ ВЕСЬ РАЗДЕЛ (6 кругов × 9 проб живой дверью, боевой формат;
# `docs/artifacts/2026-09-06-бюджет-проб-сторожа-ручки-06.09.md`): одиночная проба — медиана
# 3.97с, p95 УСПЕШНОЙ 21.62с, худшая успешная 85.67с, три отказавшие 43.35 · 107.73 · 111.70с;
# девятка целиком — 36.4 · 47.9 · 60.7 · 129.6 · 182.4 · 192.6с, МЕДИАНА 95.15с. Прежний бюджет
# 90с стоял НИЖЕ медианы того, что обязан терпеть, и одна дверь на 111.7с больше него целиком.

def slow_door(handles, slow=(), pause=0.5):
    """Дверь, у которой ОДИН юнит отвечает долго. Остальные — мгновенно и верно."""
    calls = []

    def get(action, **kw):
        bike = kw.get("bike")
        calls.append(bike)
        if bike in slow:
            time.sleep(pause)
        return {"ok": True, "season": {"global_discount": handles.get(_CELL_OF.get(bike))},
                "day_price": 317}

    get.calls = calls
    return get


class TestPerProbeCeiling(GateBase):
    """Свой предел у пробы: одна медленная дверь больше не съедает обход целиком."""

    def _walk(self, get, budget, one):
        """Тот же состав, что и на боевом пути: обёртка поверх двери, обёртка — в руки."""
        caller = price_gate._bounded(get, budget, time.monotonic, one=one)
        return price_freshness_run.live_handles(get=caller)

    def test_one_slow_door_no_longer_starves_the_other_probes(self):
        # Медленный юнит — ПЕРВЫЙ в обходе, ровно как в живом случае 06.09.
        g = slow_door(self.fresh_handles(), slow=(price_freshness_run.PROBES[0][1][0],), pause=0.6)
        facts = self._walk(g, budget=0.9, one=0.15)
        self.assertEqual(len(g.calls), 9, "спрошены обязаны быть все девять юнитов")
        self.assertTrue(facts["ok"])
        # Категория читается по СОГЛАСИЮ уцелевших юнитов: брошена проба, а не ручка.
        self.assertEqual(sorted(c for c, v in facts["handles"].items() if v is not None),
                         ["H3", "I3", "J3"])

    def test_without_the_second_floor_the_same_door_eats_the_walk(self):
        """КОНТРФАКТ в юните: тот же вход, снят ОДИН этаж — и обход голодает, как до правки."""
        g = slow_door(self.fresh_handles(), slow=(price_freshness_run.PROBES[0][1][0],), pause=0.6)
        facts = self._walk(g, budget=0.5, one=0)      # объявленный откат второго этажа
        self.assertLess(len(g.calls), 9)
        self.assertIsNone(facts["handles"]["J3"], "последняя категория обязана остаться непрочитанной")

    def test_the_abandoned_probe_says_why_and_never_becomes_a_zero(self):
        g = slow_door(self.fresh_handles(), slow=tuple(_CELL_OF), pause=0.6)
        facts = self._walk(g, budget=9.0, one=0.15)
        self.assertFalse(facts["ok"])
        self.assertIn("не уложилась в свой предел", facts["error"])
        # Ни одна ручка не стала нулём: «не снялась» — это None, а не 0.0.
        self.assertEqual(set(facts["handles"].values()), {None})

    def test_the_rollback_of_the_second_floor_is_declared_by_a_number(self):
        self.assertEqual(price_gate.one_seconds({price_gate.ONE_ENV: "0"}), 0.0)
        self.assertEqual(price_gate.one_seconds({}), price_gate.ONE_DEFAULT)
        self.assertEqual(price_gate.one_seconds({price_gate.ONE_ENV: "мусор"}),
                         price_gate.ONE_DEFAULT)

    def test_both_thresholds_stand_on_the_measurement_and_not_on_a_round_number(self):
        """ПОРОГ СВЕРЯЕТСЯ С ЗАМЕРОМ ТОГО, ЧТО ОН ТЕРПИТ. Числа замера 06.09 — литералами
        здесь: разъедется код с замером — покраснеет этот тест, а не клиент."""
        p95_ok, worst_fail = 21.62, 111.70          # p95 успешной пробы / худшая отказавшая
        round_median, worst_round, capped_round = 95.15, 192.64, 128.15
        self.assertGreaterEqual(price_gate.ONE_DEFAULT, 2 * p95_ok,
                                "предел обязан нести двукратный запас от p95 УСПЕШНОЙ пробы")
        self.assertLess(price_gate.ONE_DEFAULT, worst_fail,
                        "предел выше худшей отказавшей пробы не срезает патологию вовсе")
        self.assertGreaterEqual(price_gate.PROBE_DEFAULT, 2 * capped_round,
                                "бюджет обязан нести двукратный запас от худшего круга")
        self.assertGreater(price_gate.PROBE_DEFAULT, worst_round,
                           "бюджет ниже измеренного худшего круга — гарантированная ложь")
        # ГЛАВНОЕ ЧИСЛО КЛАССА: порог ниже медианы того, что терпит, — не бдительность, а ложь.
        # Прежние 90с были ниже медианы круга 95.15с; новый обязан быть выше с запасом.
        self.assertGreater(price_gate.PROBE_DEFAULT, round_median * 2)


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


# ────────── 4. ДВА РАЗНЫХ ИСХОДА СТОРОЖА РАЗЛИЧИМЫ В ЖУРНАЛЕ И В КАРТОЧКЕ (06.09.2026) ──────
#
# ПОЛИТИКА НЕ ТРОНУТА: цену не называет ни одна из причин. Меняется ровно то, что владелец
# по строке журнала видит, ЧТО случилось и ЧТО делать. До 06.09 обе причины несли один хвост
# «нужно пересобрать price_source.json» — то есть непрочитанная дверь советовала собрать
# слепок с той самой двери, которую прочитать не удалось.

class TestTwoReasonsAreTold(GateBase):
    def _cards(self):
        moved = self.fresh_handles()
        cell = sorted(moved)[0]
        moved[cell] = moved[cell] + 0.10
        _, turned = price_gate.allow(env={}, now=self.at_snapshot_date(), get=door(moved))
        _, dead = price_gate.allow(env={}, now=self.at_snapshot_date(), get=door({}, boom=True))
        return turned, dead

    def test_the_two_reasons_carry_different_names_and_different_actions(self):
        turned, dead = self._cards()
        self.assertIn(price_freshness.KIND_DIVERGED, turned)
        self.assertIn(price_freshness.KIND_NOT_READ, dead)
        self.assertIn("пересобрать price_source.json", turned)
        self.assertIn("НЕ НАДО", dead)
        self.assertNotEqual(price_freshness.KIND_ACTION[price_freshness.KIND_DIVERGED],
                            price_freshness.KIND_ACTION[price_freshness.KIND_NOT_READ])

    def test_the_name_stands_first_so_the_journal_line_is_greppable(self):
        """Строка журнала продукта — это «price_gate: <карточка дословно>». Имя причины обязано
        быть В НАЧАЛЕ карточки, иначе греп по журналу различал бы причины на глаз, а не счётом."""
        for card in self._cards():
            self.assertTrue(card.startswith("["), card[:40])
            line = "price_gate: %s (модель NMAX)" % card
            self.assertIn("] ЦЕНА НЕ НАЗВАНА", line)

    def test_neither_reason_ever_names_the_price(self):
        """Разделение — про ДИАГНОСТИКУ, а не про политику: обе причины по-прежнему гасят цену."""
        moved = self.fresh_handles()
        moved[sorted(moved)[0]] = moved[sorted(moved)[0]] + 0.10
        for g in (door(moved), door({}, boom=True)):
            may, card = price_gate.allow(env={}, now=self.at_snapshot_date(), get=g)
            self.assertFalse(may)
            self.assertTrue(card)

    def test_a_broken_verdict_is_named_unread_and_not_diverged(self):
        """Чужой/битый вердикт не смеет притвориться ДОКАЗАННЫМ устареванием: доказательства
        у него нет, и владельцу нельзя советовать пересборку слепка на пустом месте."""
        action = price_freshness.bot_action({"state": "чужое"})
        self.assertFalse(action["name_price_to_client"])
        self.assertIn(price_freshness.KIND_NOT_READ, action["owner_card"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
