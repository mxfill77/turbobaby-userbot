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
import datetime
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
import price_freshness
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


class ClockTest(unittest.TestCase):
    """СЕДЬМАЯ ДВЕРЬ — КАЛЕНДАРЬ. Из-за него снимок жил ровно сутки: 111 ключей моста из 125
    несут ЖИВУЮ дату (`build_price_sheet_note`: якорь = ЗАВТРА), и назавтра продукт спрашивает
    мост ДРУГОЕ. Здесь доказывается, что `Clock` чинит ДЕНЬ, а не ослабляет КЛЮЧ."""

    MOMENT = datetime.datetime(2026, 8, 22, 20, 52, 37,
                               tzinfo=datetime.timezone(datetime.timedelta(hours=7)))

    def test_frozen_day_is_what_the_product_sees(self):
        real = suggest.today_phuket()
        clock = trainer_pin.Clock(self.MOMENT)
        with clock:
            self.assertEqual(suggest.today_phuket(), datetime.date(2026, 8, 22))
        self.assertEqual(suggest.today_phuket(), real, "часы обязаны вернуться живыми")

    def test_the_recorded_key_is_reproduced_not_relaxed(self):
        """Главное свойство: ключ остаётся ДОСЛОВНЫМ, с датой — совпадает он потому, что продукт
        считает его от ЗАМОРОЖЕННОГО дня. Якорь прайс-сетки = «завтра» (suggest.py:5001)."""
        with trainer_pin.Clock(self.MOMENT):
            anchor = (suggest.today_phuket() + datetime.timedelta(days=1)).isoformat()
        self.assertEqual(anchor, "2026-08-23",
                         "якорь обязан быть завтрашним ОТ ДНЯ СНИМКА, а не от сегодняшнего")

    def test_explicit_now_beats_the_freeze(self):
        """Инъекция вызывающего сильнее заморозки — иначе `Clock` втихую переписывал бы голдены,
        которые называют свой момент сами."""
        other = datetime.datetime(2026, 1, 15, 20, 0, tzinfo=datetime.timezone.utc)
        with trainer_pin.Clock(self.MOMENT):
            self.assertEqual(suggest.today_phuket(other), datetime.date(2026, 1, 16))

    def test_second_clock_of_the_product_is_frozen_too(self):
        """`price_freshness.judge` при `now=None` берёт `datetime.date.today()` (локаль машины),
        мимо Пхукета. Не заморозить его — и возраст слепка поехал бы каждые сутки."""
        snap = {"handles": {"H3": -7.0}, "taken": datetime.date(2026, 8, 2), "sheet": "тест"}
        live = {"ok": False, "handles": None, "error": "лист не опрашивался (юнит)"}
        with trainer_pin.Clock(self.MOMENT):
            v = price_freshness.judge(snap, live, max_age=14.0)
        self.assertEqual(v["age_days"], 20.0, "возраст обязан считаться от дня СНИМКА")

    def test_uninstall_returns_both_clocks(self):
        before = (suggest.now_phuket, price_freshness.judge)
        with trainer_pin.Clock(self.MOMENT):
            self.assertIsNot(suggest.now_phuket, before[0])
            self.assertIsNot(price_freshness.judge, before[1])
        self.assertIs(suggest.now_phuket, before[0])
        self.assertIs(price_freshness.judge, before[1])

    def test_naive_moment_refused(self):
        with self.assertRaises(ValueError):
            trainer_pin.Clock(datetime.datetime(2026, 8, 22, 20, 52, 37))
        with self.assertRaises(ValueError):
            trainer_pin.Clock("2026-08-22")

    def test_from_meta_prefers_the_stamped_moment(self):
        clock = trainer_pin.Clock.from_meta({trainer_pin.MOMENT_KEY: "2026-08-22T13:52:37+00:00",
                                             "снят": "2020-01-01 00:00:00"})
        self.assertEqual(clock.day(), datetime.date(2026, 8, 22))
        self.assertIn(trainer_pin.MOMENT_KEY, clock.why)

    def test_from_meta_reads_old_snapshot_by_snyat(self):
        """Снимки до 23.08 несут только `снят` — локальное время `time.strftime`. Читать его
        как UTC значило бы сдвинуть день на семь часов и промахнуться в ночных снимках."""
        clock = trainer_pin.Clock.from_meta({"снят": "2026-08-22 20:52:37"})
        self.assertEqual(clock.local_day, datetime.date(2026, 8, 22))

    def test_from_meta_without_any_clock_refuses(self):
        """Молча замерить НЕ ТОТ день хуже, чем не замерить: подстановки «возьмём сегодня» нет.
        «Меты нет вовсе» и «в мете нет часов» — РАЗНЫЕ отказы: слепой `meta or {}` слил бы их."""
        with self.assertRaises(ValueError):
            trainer_pin.Clock.from_meta({"head": "abc"})
        with self.assertRaises(ValueError):
            trainer_pin.Clock.from_meta(None, where="снимок без меты")

    def test_from_snapshot_opens_the_file_read_only(self):
        """`Clock` не пишет в снимок ни одной веткой — иначе он стал бы дорогой «переснять»."""
        path = _write(_tmp("pin.json"), {"version": trainer_pin.SNAPSHOT_VERSION, "bridge": {},
                                         "read_doc": {}, "head": {}, "playbook": "",
                                         "meta": {"снят": "2026-08-22 20:52:37"}})
        with io.open(path, "rb") as f:
            before = f.read()
        trainer_pin.Clock.from_snapshot(path).install().uninstall()
        with io.open(path, "rb") as f:
            self.assertEqual(f.read(), before, "снимок обязан остаться байт в байт прежним")
        self.assertFalse(hasattr(trainer_pin.Clock, "save"))

    def test_record_stamps_the_moment_into_meta(self):
        """Часы замера — свойство СНИМКА: их пишет сам `save`, забыть их нельзя."""
        for cls, path in ((trainer_pin.Pin, _tmp("pin.json")),
                          (trainer_pin.GatePin, _tmp("gate.json"))):
            obj = cls(path, "record")
            obj.save(meta={"как": "юнит"})
            with io.open(path, encoding="utf-8") as f:
                meta = json.load(f)["meta"]
            self.assertIn(trainer_pin.MOMENT_KEY, meta)
            clock = trainer_pin.Clock.from_meta(meta, where=cls.__name__)
            self.assertEqual(clock.day(), suggest.today_phuket(),
                             "штамп обязан назвать тот же день, в который снимок снят")


if __name__ == "__main__":
    unittest.main(verbosity=2)
