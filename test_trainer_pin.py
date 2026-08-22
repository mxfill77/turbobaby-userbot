# -*- coding: utf-8 -*-
"""Юниты ПИНА ГРАНИЦЫ тренажёра — и в первую очередь ШЕСТОЙ ДВЕРИ (`trainer_pin.GatePin`).

ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ, А НЕ ДЕКЛАРИРУЕТСЯ:
  1. ПРОМАХ СНИМКА ДВЕРИ = НЕИЗВЕСТНО, А НЕ КРАСНОЕ. Пустой снимок сторожа гасит цену (так и
     должен вести себя ПРОДУКТ — решение владельца 17.08), но ПРИБОР при этом обязан пометить
     кейс «неизвестно», а не записать молчание границы в вину коду. Это зеркало правила
     «МОЛЧАЩАЯ ГОЛОВА = НЕИЗВЕСТНО» (`trainer_run`, 22.08) на шестую дверь.
  2. ПЯТНО ЛИПКОЕ. Вердикт сторожа кэшируется на `PRICE_GATE_TTL_MIN`: за набор дверь дёргается
     один раз, а обслуживает восемь кейсов. Кейс, обслуженный КЭШИРОВАННЫМ вердиктом, рождённым
     промахом, — тоже «неизвестно», иначе семь кейсов из восьми звались бы измеренными.
  3. В REPLAY СЕТИ НЕТ ВОВСЕ. Живой клиент моста не поднимается ни одной веткой: `real`
     фабрики не зовётся, и это проверяется взрывающейся заглушкой.
  4. ПЕРЕЗАПИСЬ СНИМКА ЗАПРЕЩЕНА КОДОМ. Без этого замка «переигрывать, пока не позеленеет»
     стало бы техникой, а не нарушением.
  5. `pin_head=False` НЕ ТРОГАЕТ ГОЛОВУ. Режим честного живого числа обязан оставить обе точки
     входа головы на месте — иначе «живая голова» была бы словом, а не фактом.

Секретов тест не читает и не видит: дверь в replay — это словарь на диске.
"""
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_isolation  # noqa: F401  ДО suggest: офлайн-дверь сторожа свежести
import price_freshness_run
import price_gate
import suggest
import trainer_pin


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="trainer_pin_"), name)


def _write(path, doc):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return path


def _boom():
    raise AssertionError("живой клиент моста поднялся в replay — сеть из замера НЕ убрана")


class GatePinSnapshotTest(unittest.TestCase):
    """Замки самого снимка: версия и запрет перезаписи."""

    def test_record_refuses_to_overwrite(self):
        path = _write(_tmp("gate.json"), {"version": trainer_pin.GATE_SNAPSHOT_VERSION,
                                          "gate": {}, "meta": {}})
        with self.assertRaises(FileExistsError):
            trainer_pin.GatePin(path, "record")

    def test_replay_rejects_foreign_version(self):
        path = _write(_tmp("gate.json"), {"version": 99, "gate": {}, "meta": {}})
        with self.assertRaises(ValueError):
            trainer_pin.GatePin(path, "replay")

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            trainer_pin.GatePin(_tmp("gate.json"), "переиграть")


class GatePinReplayTest(unittest.TestCase):
    """Поведение двери при попадании и при промахе."""

    def setUp(self):
        self._real_caller = price_gate._bridge_caller
        self._real_allow = price_gate.allow
        price_gate._bridge_caller = _boom          # сеть в replay обязана быть недостижима
        self.addCleanup(self._restore)
        price_gate.reset()

    def _restore(self):
        price_gate._bridge_caller = self._real_caller
        price_gate.allow = self._real_allow
        price_gate.reset()

    def _snap(self, pairs):
        """Снимок двери руками — ключ считается ТЕМ ЖЕ правилом, что в бою (`GatePin._key`)."""
        data = {"version": trainer_pin.GATE_SNAPSHOT_VERSION, "gate": {}, "meta": {}}
        for action, kw, value in pairs:
            key = json.dumps({"action": action, "params": kw}, ensure_ascii=False, sort_keys=True)
            data["gate"][key] = {"action": action, "params": kw, "value": value}
        return _write(_tmp("gate.json"), data)

    def test_hit_returns_recorded_answer_and_never_touches_network(self):
        kw = {"bike": "NMAX 155CC GREY PHUKET 5960",
              "date_start": price_freshness_run.WINDOW[0],
              "date_end": price_freshness_run.WINDOW[1]}
        answer = {"ok": True, "season": {"global_discount": -7.0}}
        gp = trainer_pin.GatePin(self._snap([("quote_price", kw, answer)]), "replay")
        with gp:
            got = price_gate._bridge_caller()("quote_price", **kw)
        self.assertEqual(got, answer)
        self.assertEqual(gp.misses, 0)
        self.assertFalse(gp.tainted)
        self.assertEqual(gp.calls, 1)

    def test_miss_raises_pinmiss_and_stains_the_verdict(self):
        gp = trainer_pin.GatePin(self._snap([]), "replay")
        with gp:
            caller = price_gate._bridge_caller()
            with self.assertRaises(trainer_pin.PinMiss):
                caller("quote_price", bike="NMAX", date_start="a", date_end="b")
        self.assertEqual(gp.misses, 1)
        self.assertTrue(gp.tainted, "промах обязан пометить вердикт как рождённый промахом")

    def test_empty_snapshot_gives_unknown_not_red(self):
        """Продукт молчит ценой (так и надо), а ПРИБОР зовёт кейс «неизвестно», а не красным."""
        gp = trainer_pin.GatePin(self._snap([]), "replay")
        with gp:
            gp.case(1)
            may, card = price_gate.allow(env={})
            self.assertFalse(may, "отказ двери не смеет стать разрешением назвать цену")
            self.assertTrue(card)
            self.assertIn(1, gp.unknown, "кейс со спросом сторожа при промахе = НЕИЗВЕСТНО")
        self.assertEqual(gp.asks, 1)
        self.assertGreaterEqual(gp.misses, 9, "девять проб сторожа обязаны быть посчитаны")

    def test_stain_reaches_cases_served_by_cache(self):
        """Второй кейс к двери НЕ ходит (кэш TTL), но вердикт тот же — значит тоже «неизвестно»."""
        gp = trainer_pin.GatePin(self._snap([]), "replay")
        with gp:
            gp.case(1)
            price_gate.allow(env={})
            first = gp.misses
            gp.case(2)
            price_gate.allow(env={})
            self.assertEqual(gp.misses, first, "кэш обязан избавить от второго похода к двери")
            self.assertIn(2, gp.unknown, "кейс на КЭШИРОВАННОМ грязном вердикте — тоже неизвестно")

    def test_new_run_clears_stain_and_product_cache(self):
        gp = trainer_pin.GatePin(self._snap([]), "replay")
        with gp:
            gp.case(1)
            price_gate.allow(env={})
            self.assertTrue(gp.tainted)
            gp.new_run()
            self.assertFalse(gp.tainted)
            self.assertEqual(gp.unknown, set())
            gp.case(1)
            price_gate.allow(env={})
            self.assertGreaterEqual(gp.misses, 18, "после сброса кэша дверь переигрывается заново")

    def test_uninstall_returns_originals(self):
        gp = trainer_pin.GatePin(self._snap([]), "replay")
        with gp:
            pass
        self.assertIs(price_gate._bridge_caller, _boom)
        self.assertIs(price_gate.allow, self._real_allow)


class PinHeadFlagTest(unittest.TestCase):
    """Режим честного живого числа: внешние двери из снимка, голова ЖИВАЯ."""

    def _empty_five(self):
        return _write(_tmp("pin.json"), {"version": trainer_pin.SNAPSHOT_VERSION, "bridge": {},
                                         "read_doc": {}, "head": {}, "playbook": "", "meta": {}})

    def test_pin_head_false_leaves_both_entry_points_live(self):
        before = (suggest._cli_llm, suggest._default_llm)
        pin = trainer_pin.Pin(self._empty_five(), "replay", pin_head=False)
        with pin:
            self.assertIs(suggest._cli_llm, before[0])
            self.assertIs(suggest._default_llm, before[1])
        self.assertIs(suggest._cli_llm, before[0])
        self.assertIs(suggest._default_llm, before[1])

    def test_pin_head_true_wraps_them(self):
        before = (suggest._cli_llm, suggest._default_llm)
        pin = trainer_pin.Pin(self._empty_five(), "replay")
        with pin:
            self.assertIsNot(suggest._cli_llm, before[0])
            self.assertIsNot(suggest._default_llm, before[1])
        self.assertIs(suggest._cli_llm, before[0])
        self.assertIs(suggest._default_llm, before[1])

    def test_stats_names_who_answered_for_the_head(self):
        self.assertEqual(trainer_pin.Pin(self._empty_five(), "replay",
                                         pin_head=False).stats()["голова"], "ЖИВАЯ")
        self.assertEqual(trainer_pin.Pin(self._empty_five(), "replay").stats()["голова"],
                         "из снимка")


if __name__ == "__main__":
    unittest.main(verbosity=2)
