# -*- coding: utf-8 -*-
"""
test_pc_awake_clock.py — ГОЛДЕНЫ «сон машины не тратит бюджет задачи» (разбор 01.08.2026).

ЖИВОЙ ЗАМЕР ЭТОГО ПК (01.08.2026 15:47, ctypes, read-only):
    GetTickCount64             = 224673.5 c = 62.41 ч   ← ровно это отдаёт time.monotonic() на Windows
    QueryUnbiasedInterruptTime = 192388.9 c = 53.44 ч   ← бодрствование, проспанного в нём нет
    разница                    =  32284.6 c =  8.97 ч   ← сон 30.07 03:43→12:41, ОДИН эпизод

Отсюда два факта, на которых стоит весь файл:
  1) на Windows `time.monotonic()` ВКЛЮЧАЕТ проспанное, а бюджет headless мерился именно им
     (`subprocess.run(timeout=…)` → CPython `Popen._remaining_time` → `_time()` = `time.monotonic`).
     Значит уснувшая посреди задачи машина ТРАТИЛА её 45 минут, ничего не делая;
  2) разница двух часов даёт СОН ЧИСЛОМ — гадать по скачку wall-clock больше не нужно.

Второй факт закрывает соседний класс. Детект «ПК проснулся» ловил скачок wall-clock между
витками главного цикла, а виток исполняет задачи СИНХРОННО — поэтому длинная задача выглядела
сном. Живые ложные карточки 01.08 (все три — работа, а не сон):
    03:28:47  3988 c = задачи 141 (1211 c) + 144 (2669 c) в одном витке
    14:06:03  3103 c = задача 158 (2700 c таймаута) + накладные
    15:20:40  2845 c = задача 160 (2700 c таймаута) + накладные
Ни в одном из этих окон машина не спала: суммарный сон с загрузки (30.07 01:2x) = 8.97 ч и весь
он приходится на единственный эпизод 30.07 утром.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_pc_awake_clock -v
"""

import os
import sys
import time
import subprocess
import unittest
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (как в соседних файлах)

import pc_orchestrator as o            # noqa: E402


# ── живые числа 01.08 из pc_orchestrator.log ──────────────────────────────────────────────────
JUMP_158 = 3103       # скачок витка вокруг задачи 158 (таймаут 14:00:21)
JUMP_160 = 2845       # скачок витка вокруг задачи 160 (таймаут 15:19:26)
JUMP_141_144 = 3988   # скачок витка с ДВУМЯ задачами подряд (03:28:47)
SLEEP_3007 = 32381    # настоящий сон 30.07 (8 ч 59 м) — единственный за эту загрузку
SLEEP_2607 = 11611    # настоящий сон 26.07 (3 ч 13 м)


class FakeClock:
    """Пара часов: настенные (сон в них ИДЁТ) и бодрствования (сон в них СТОИТ)."""

    def __init__(self):
        self.wall = 1000.0
        self.awake = 500.0        # эпоха НАРОЧНО другая: код обязан жить на разницах

    def advance(self, d_wall, d_awake):
        self.wall += d_wall
        self.awake += d_awake

    def wall_now(self):
        return self.wall

    def awake_now(self):
        return self.awake


class FakeProc:
    """Дочерний процесс-двойник. Каждый `communicate(timeout=…)` съедает шаг сценария
    (Δнастенных, Δбодрствования, завершился?); сценарий кончился — процесс ВИСИТ бодрым и
    съедает ровно выданный ему кусок (так ведёт себя настоящий зависший прогон)."""

    def __init__(self, clock, steps, out="работа сделана\nRESULT: ок", err=""):
        self.clock, self.steps, self.out, self.err = clock, list(steps), out, err
        self.killed = 0
        self.returncode = None
        self.grants = []

    def communicate(self, timeout=None):
        self.grants.append(timeout)
        if self.steps:
            d_wall, d_awake, done = self.steps.pop(0)
        else:
            d_wall = d_awake = float(timeout or 0)      # бодрый зависший прогон
            done = False
        self.clock.advance(d_wall, d_awake)
        if done or self.killed:
            self.returncode = 0
            return self.out, self.err
        raise subprocess.TimeoutExpired("claude", timeout or 0)

    def kill(self):
        self.killed += 1


def _wait(proc, clock, timeout=2700, slice_sec=60):
    return o._wait_awake(proc, timeout, slice_sec=slice_sec,
                         wall=clock.wall_now, awake=clock.awake_now)


class TestSonNeTratitByudzhet(unittest.TestCase):
    """РЕГРЕСС задачи: задача, в окне которой был сон, таймаута не получает."""

    def test_son_45_minut_posredi_zadachi_ne_daet_taymauta(self):
        c = FakeClock()
        # 10 мин работы → машина спит 45 мин (бодрствование не растёт) → 5 мин работы и итог
        p = FakeProc(c, [(600, 600, False), (2700, 0, False), (300, 300, True)])
        out, err, spent, slept = _wait(p, c)
        self.assertIn("RESULT: ок", out)
        self.assertEqual(p.killed, 0, "спящую машину убивать не за что")
        self.assertAlmostEqual(spent, 900, delta=1, msg="в бюджет ушло только бодрствование")
        self.assertAlmostEqual(slept, 2700, delta=1, msg="сон посчитан отдельно")

    def test_son_dlinnee_vsego_byudzheta_perezhivaetsya(self):
        """Сон 9 ч (живой эпизод 30.07) посреди задачи: настенных часов ушло 33 000 c при
        бюджете 2700 c, но работа продолжилась с того места, где встала."""
        c = FakeClock()
        p = FakeProc(c, [(120, 120, False), (SLEEP_3007, 0, False), (60, 60, True)])
        out, _, spent, slept = _wait(p, c)
        self.assertIn("RESULT: ок", out)
        self.assertAlmostEqual(spent, 180, delta=1)
        self.assertAlmostEqual(slept, SLEEP_3007, delta=1)
        self.assertGreater(c.wall - 1000.0, 2700, "настенных часов прошло БОЛЬШЕ бюджета")

    def test_zavisshiy_progon_obryvaetsya_kak_prezhde(self):
        """Вторая половина регресса: бодрый зависший прогон рвётся ровно по бюджету."""
        c = FakeClock()
        p = FakeProc(c, [])                     # никогда не завершается, машина бодрствует
        with self.assertRaises(TimeoutError):
            _wait(p, c, timeout=2700, slice_sec=60)
        self.assertAlmostEqual(c.awake - 500.0, 2700, delta=1, msg="ни секундой дольше бюджета")
        self.assertLessEqual(max(g for g in p.grants), 60, "кусок не больше шага переоценки")

    def test_bez_chasov_bodrstvovaniya_povedenie_prezhnee(self):
        """Запасной путь (часы бодрствования недоступны): awake == wall, обрыв ровно по бюджету —
        байт-в-байт прежнее поведение, а не тихое «ждём вечно»."""
        c = FakeClock()
        p = FakeProc(c, [])
        with self.assertRaises(TimeoutError):
            o._wait_awake(p, 600, slice_sec=60, wall=c.wall_now, awake=c.wall_now)
        self.assertAlmostEqual(c.wall - 1000.0, 600, delta=1)

    def test_son_v_poslednem_kuske_ne_ubivaet(self):
        """Сон пришёлся на кусок, в котором ребёнок и ответил: бюджет тратит только бодрствование."""
        c = FakeClock()
        p = FakeProc(c, [(2699, 2699, False), (5000, 1, True)])
        out, _, spent, slept = _wait(p, c)
        self.assertIn("RESULT: ок", out)
        self.assertLessEqual(spent, 2700)
        self.assertAlmostEqual(slept, 4999, delta=1)


class TestRunClaude(unittest.TestCase):
    """Боевая проводка: run_claude ждёт по часам бодрствования и убивает ТОЛЬКО зависшего."""

    def _argv_env(self):
        return ("промпт", 2700, o.REPO, {"X": "1"})

    def test_son_ne_ubivaet_rebenka(self):
        c = FakeClock()
        holder = {}

        def fake_popen(argv, **kw):
            holder["argv"], holder["kw"] = argv, kw
            return FakeProc(c, [(60, 60, False), (3600, 0, False), (60, 60, True)])

        with mock.patch.object(o, "resolve_claude", lambda: r"C:\x\claude.exe"):
            rc, out, err = o.run_claude(*self._argv_env(), popen=fake_popen,
                                        waiter=lambda p, t: o._wait_awake(
                                            p, t, slice_sec=60, wall=c.wall_now, awake=c.awake_now))
        self.assertEqual(rc, 0)
        self.assertIn("RESULT: ок", out)
        self.assertEqual(holder["kw"].get("encoding"), "utf-8")   # прежний контракт кодировки
        self.assertEqual(holder["kw"].get("errors"), "replace")

    def test_zavisshiy_ubit_i_taymaut_proshel_naverh(self):
        c = FakeClock()
        proc = FakeProc(c, [])

        with mock.patch.object(o, "resolve_claude", lambda: r"C:\x\claude.exe"):
            with self.assertRaises(TimeoutError):
                o.run_claude(*self._argv_env(), popen=lambda argv, **kw: proc,
                             waiter=lambda p, t: o._wait_awake(
                                 p, t, slice_sec=60, wall=c.wall_now, awake=c.awake_now))
        self.assertEqual(proc.killed, 1, "зависший ребёнок обязан быть убит")


class TestAwakeMonotonic(unittest.TestCase):
    """Сами часы: монотонны, в своей эпохе, на Windows строго не больше смещённых."""

    def test_monotonna_i_chislo(self):
        a = o.awake_monotonic()
        b = o.awake_monotonic()
        self.assertIsInstance(a, float)
        self.assertGreaterEqual(b, a)

    def test_ne_bolshe_smeshchennyh_chasov(self):
        """Инвариант Windows: бодрствование с загрузки ≤ времени с загрузки. На запасном пути
        (не Windows / вызов не удался) обе величины — time.monotonic(), равенство тоже проходит."""
        self.assertLessEqual(o.awake_monotonic(), time.monotonic() + 1.0)

    @unittest.skipUnless(sys.platform == "win32", "часы бодрствования есть только на Windows")
    def test_na_etom_pk_chasy_nastoyaschie(self):
        """Замер 01.08: разница смещённых и несмещённых часов = 8.97 ч (сон 30.07). Если бы
        QueryUnbiasedInterruptTime не резолвилась, awake_monotonic отдавала бы time.monotonic()
        и разницы не было бы вовсе — тогда правка не работает, и это надо знать."""
        self.assertTrue(o.awake_clock_available(), "часы бодрствования не поднялись")


class TestDetektSnaMeritSonAneRabotu(unittest.TestCase):
    """Соседний класс: карточка «ПК спал» — по ИЗМЕРЕННОМУ сну, а не по скачку витка."""

    def setUp(self):
        self.journal, self.signals = [], []

    def _report(self, gap, slept):
        return o.report_long_sleep(gap, slept=slept, backlog_fn=lambda: 3,
                                   journal=self.journal.append,
                                   notifier=lambda t, x: self.signals.append((t, x)))

    def test_tri_zhivyh_lozhnyaka_0108_molchat(self):
        for jump in (JUMP_141_144, JUMP_158, JUMP_160):
            self.assertIsNone(self._report(jump, slept=0), "скачок %s — работа, не сон" % jump)
        self.assertEqual((self.journal, self.signals), ([], []))

    def test_nastoyaschiy_son_signalit_i_pokazyvaet_zamer(self):
        out = self._report(SLEEP_3007 + 140, slept=SLEEP_3007)
        self.assertIsNotNone(out)
        self.assertIn("8 ч 59 м", out)
        self.assertIn(str(SLEEP_3007), out)
        self.assertEqual((len(self.journal), len(self.signals)), (1, 1))

    def test_son_2607_tozhe_signalit(self):
        self.assertIsNotNone(self._report(SLEEP_2607 + 30, slept=SLEEP_2607))

    def test_son_est_no_korotkiy_molchit(self):
        """Машина спала 10 минут внутри длинного витка — ниже порога, карточки нет."""
        self.assertIsNone(self._report(JUMP_160, slept=600))

    def test_bez_zamera_povedenie_prezhnee(self):
        """slept=None (часы бодрствования недоступны) → прежний разбор по скачку, чтобы на
        запасном пути громкий сигнал не пропал молча."""
        self.assertIsNotNone(self._report(JUMP_160, slept=None))
        self.assertIsNone(self._report(1776, slept=None))       # худший ложняк прежней шкалы


if __name__ == "__main__":
    unittest.main(verbosity=2)
