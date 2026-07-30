# -*- coding: utf-8 -*-
"""
test_pc_sleep_alarm.py — голдены СИГНАЛА О ДОЛГОМ СНЕ ПК (инцидент 30.07.2026).

ЖИВОЙ ИНЦИДЕНТ. 30.07 ПК проспал 8 ч 58 м (S3, 03:43:15→12:41:21 по журналу Windows) — вместе с
машиной спал userbot_listen, клиентский бот был недоступен всю ночь. За 7 суток сон съел 12 ч 10 м.
Причина НЕ таймаут простоя (standby/hibernate-timeout уже 0 и от сети, и от батарей), а ЯВНОЕ
усыпление из меню Пуск: Kernel-Power 187 ApiCaller=StartMenuExperienceHost.exe, Kernel-Power 42
Reason=4 (Application API). Демон скачок wall-clock видел и раньше, но писал только строку в свой
лог — её никто не читает. Теперь: строка в журнал + карточка в тему 328 с числом накопившихся задач.

ЧИСЛА ПОРОГА — ИЗ ЖИВОГО ЛОГА, НЕ ИЗ ГОЛОВЫ. Замер по pc_orchestrator.log за 22–30.07, 369
срабатываний детекта пробуждения:
  • >600 с  → 26 срабатываний, из них 24 ЛОЖНЫХ (демон исполняет задачу СИНХРОННО в главном
              цикле, поэтому честный виток растягивается; максимум ложного — 1776 с);
  • >1800 с → РОВНО 2, и это ровно два настоящих сна: 11611 с (26.07) и 32381 с (30.07).
Дефолт порога = TASK_TIMEOUT+POLL_SEC (2760 с) — длиннее честного витка по построению.
Голдены гоняют ИМЕННО эти живые числа, а не круглые синтетические: и оба настоящих сна, и
худший ложняк 1776 с, и всю выборку ложняков >600 с целиком.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_pc_sleep_alarm -v
"""

import os
import unittest

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (как в test_pc_orchestrator)

import pc_orchestrator as o            # noqa: E402


# ── живые числа из pc_orchestrator.log ────────────────────────────────────────────────────────
SLEEP_3007 = 32381    # настоящий сон 30.07, детект в 12:42:20 (8 ч 59 м 41 с по wall-clock)
SLEEP_2607 = 11611    # настоящий сон 26.07, детект в 13:15:02 (3 ч 13 м 31 с)
WORST_FALSE = 1776    # самый длинный ЛОЖНЫЙ скачок (длинная задача), 23.07 17:29:22
# все ложняки >600 с из живого лога — ни один не имеет права поднять карточку
FALSE_JUMPS = [1339, 1566, 1776, 1048, 1267, 1569, 945, 980, 710, 1179, 1393, 657,
               1313, 680, 737, 1612, 932, 1494, 1195, 951, 887, 741, 636, 1769]


class Spy:
    """Считает вызовы журнала и сигнала — «РОВНО ОДНА строка / РОВНО ОДИН сигнал» проверяем счётом."""

    def __init__(self):
        self.journal = []
        self.signals = []

    def j(self, line):
        self.journal.append(line)

    def n(self, topic, text):
        self.signals.append((topic, text))


def fake_backlog(n):
    return lambda: n


class TestPorog(unittest.TestCase):
    """Порог: настоящий сон поднимает карточку, длинная задача — нет."""

    def test_defolt_poroga_dlinnee_chestnogo_vitka(self):
        # дефолт обязан быть НЕ МЕНЬШЕ, чем самый длинный честный виток демона
        self.assertGreaterEqual(o.SLEEP_ALARM_SEC, o.TASK_TIMEOUT + o.POLL_SEC)
        self.assertGreater(o.SLEEP_ALARM_SEC, WORST_FALSE)   # худший ложняк заведомо ниже порога

    def test_nastoyaschiy_son_3007_signalit(self):
        sp = Spy()
        out = o.report_long_sleep(SLEEP_3007, backlog_fn=fake_backlog(7),
                                  journal=sp.j, notifier=sp.n)
        self.assertIsNotNone(out)
        self.assertEqual(len(sp.journal), 1, "в журнал ровно ОДНА строка")
        self.assertEqual(len(sp.signals), 1, "сигнал ровно ОДИН")

    def test_nastoyaschiy_son_2607_signalit(self):
        sp = Spy()
        self.assertIsNotNone(o.report_long_sleep(SLEEP_2607, backlog_fn=fake_backlog(0),
                                                 journal=sp.j, notifier=sp.n))
        self.assertEqual((len(sp.journal), len(sp.signals)), (1, 1))

    def test_hudshiy_lozhnyak_molchit(self):
        sp = Spy()
        out = o.report_long_sleep(WORST_FALSE, backlog_fn=fake_backlog(3),
                                  journal=sp.j, notifier=sp.n)
        self.assertIsNone(out)
        self.assertEqual(sp.journal, [], "мелкий скачок — НИ ОДНОЙ строки")
        self.assertEqual(sp.signals, [], "мелкий скачок — НИ ОДНОГО сигнала")

    def test_vse_zhivye_lozhnyaki_molchat(self):
        """Вся выборка ложняков >600 с из живого лога: ноль карточек. Порог 600 дал бы 24 спама."""
        sp = Spy()
        for gap in FALSE_JUMPS:
            o.report_long_sleep(gap, backlog_fn=fake_backlog(1), journal=sp.j, notifier=sp.n)
        self.assertEqual(sp.journal, [])
        self.assertEqual(sp.signals, [])

    def test_backlog_ne_zaprashivaetsya_pri_tishine(self):
        """Тишина обязана быть БЕЗ побочек: очередь в Bridge даже не дёргаем."""
        calls = []

        def backlog():
            calls.append(1)
            return 5

        o.report_long_sleep(WORST_FALSE, backlog_fn=backlog, journal=lambda _: None,
                            notifier=lambda *_: None)
        self.assertEqual(calls, [], "при тишине очередь не опрашиваем")

    def test_granitsa_poroga_strogaya(self):
        for gap, must_fire in ((999, False), (1000, False), (1001, True), (5000, True)):
            sp = Spy()
            out = o.report_long_sleep(gap, threshold=1000, backlog_fn=fake_backlog(2),
                                      journal=sp.j, notifier=sp.n)
            self.assertEqual(out is not None, must_fire, "gap=%s порог=1000" % gap)
            self.assertEqual(len(sp.signals), 1 if must_fire else 0)


class TestKartochka(unittest.TestCase):
    """Содержимое карточки: число задач и длительность сна обязаны быть В ТЕКСТЕ."""

    def test_chislo_zadach_v_tekste(self):
        sp = Spy()
        o.report_long_sleep(SLEEP_3007, backlog_fn=fake_backlog(7), journal=sp.j, notifier=sp.n)
        topic, text = sp.signals[0]
        self.assertEqual(topic, 328, "сигнал идёт в тему постановки задач")
        self.assertIn("7", text)
        self.assertIn("new+approved", text)

    def test_dlitelnost_sna_v_tekste(self):
        sp = Spy()
        o.report_long_sleep(SLEEP_3007, backlog_fn=fake_backlog(0), journal=sp.j, notifier=sp.n)
        self.assertIn("8 ч 59 м", sp.signals[0][1])   # 32381 с = 8 ч 59 м 41 с
        self.assertIn(str(SLEEP_3007), sp.signals[0][1])

    def test_stroka_zhurnala_odnostrochnaya(self):
        """Контракт журнала 28.07: ОДНА запись = ОДНА строка. Перенос рвёт разбор по заголовкам."""
        sp = Spy()
        o.report_long_sleep(SLEEP_3007, backlog_fn=fake_backlog(4), journal=sp.j, notifier=sp.n)
        self.assertNotIn("\n", sp.journal[0])
        self.assertIn("4", sp.journal[0])

    def test_most_nedostupen_ne_vydaetsya_za_nol(self):
        """backlog=None (мост лёг) → в карточке честное «не смог посчитать», а НЕ «0 задач»."""
        sp = Spy()
        o.report_long_sleep(SLEEP_3007, backlog_fn=lambda: None, journal=sp.j, notifier=sp.n)
        text = sp.signals[0][1]
        self.assertIn("не смог посчитать", text)
        self.assertNotIn("накопилось в очереди: 0", text)
        self.assertEqual((len(sp.journal), len(sp.signals)), (1, 1), "сигнал всё равно уходит")


class TestFmtSleep(unittest.TestCase):
    def test_zhivye_dlitelnosti(self):
        self.assertEqual(o.fmt_sleep(SLEEP_3007), "8 ч 59 м")
        self.assertEqual(o.fmt_sleep(SLEEP_2607), "3 ч 13 м")
        self.assertEqual(o.fmt_sleep(WORST_FALSE), "29 м 36 с")
        self.assertEqual(o.fmt_sleep(45), "45 с")
        self.assertEqual(o.fmt_sleep(0), "0 с")
        self.assertEqual(o.fmt_sleep(-5), "0 с")


class TestBacklog(unittest.TestCase):
    """_pc_backlog: считает new+approved СВОЕЙ полосы, чужие не трогает, сбой моста → None."""

    def test_summa_new_i_approved_tolko_svoya_polosa(self):
        data = {
            "new": {"ok": True, "items": [{"lane": "pc"}, {"lane": "pc"}, {"lane": "vps"}, {}]},
            "approved": {"ok": True, "items": [{"lane": "pc"}, {"lane": "vps"}]},
        }
        self.assertEqual(o._pc_backlog(getter=lambda st: data[st]), 3)

    def test_pustaya_ochered_eto_nol_a_ne_none(self):
        empty = {"ok": True, "items": []}
        self.assertEqual(o._pc_backlog(getter=lambda st: empty), 0)

    def test_sboy_mosta_daet_none(self):
        self.assertIsNone(o._pc_backlog(getter=lambda st: {"ok": False, "error": "URLError"}))

    def test_sboy_na_vtorom_statuse_tozhe_none(self):
        def getter(st):
            return {"ok": True, "items": [{"lane": "pc"}]} if st == "new" else {"ok": False}
        self.assertIsNone(o._pc_backlog(getter=getter))

    def test_ochered_ne_mutiruetsya(self):
        """Счётчик обязан быть read-only: только get_pending, никаких complete/claim."""
        seen = []

        def getter(st):
            seen.append(st)
            return {"ok": True, "items": []}

        o._pc_backlog(getter=getter)
        self.assertEqual(seen, ["new", "approved"])


class TestBoevayaProvodka(unittest.TestCase):
    """Боевые дефолты: без инъекций сигнал идёт в _cowork и _notify_topic, а не куда-то ещё."""

    def test_defolty_zovut_zhurnal_i_temu(self):
        called = {}
        real_cowork, real_topic, real_backlog = o._cowork, o._notify_topic, o._pc_backlog
        try:
            o._cowork = lambda line: called.setdefault("journal", line)
            o._notify_topic = lambda topic, text: called.setdefault("signal", (topic, text))
            o._pc_backlog = lambda: 2
            out = o.report_long_sleep(SLEEP_3007)
        finally:
            o._cowork, o._notify_topic, o._pc_backlog = real_cowork, real_topic, real_backlog
        self.assertIsNotNone(out)
        self.assertIn("journal", called)
        self.assertEqual(called["signal"][0], o.SLEEP_ALARM_TOPIC)

    def test_tema_po_umolchaniyu_328(self):
        self.assertEqual(o.SLEEP_ALARM_TOPIC, 328)


if __name__ == "__main__":
    unittest.main(verbosity=2)
