# -*- coding: utf-8 -*-
"""
test_chatlog_servicing_tick.py — регресс часов архива форума Обслуживания (30.09.2026, SVCCLOCK3009).

ЧЕГО ЭТИ ТЕСТЫ НЕ КАСАЮТСЯ: сети, Telegram, сессии, `.env`, боевого штампа
`tmp/chatlog_servicing_tick/state.json` и боевого замка архива. Заход заменён подделкой `runner`,
флаги из файла — словарём `file_env`, штамп и замок — путями во временном каталоге.

ЧЕТЫРЕ СЛУЧАЯ ЗАДАНИЯ (п.3): срок не наступил — захода нет; наступил — ровно один заход;
ошибка — исход записан, повтора до срока нет; флаг выключения — в Telegram не ходим ни разу.
Строку исхода в журнал демона держит `test_pc_orchestrator.TestSvcClockDemon`.
"""

import io
import os
import json
import shutil
import tempfile
import unittest

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import chatlog_servicing_tick as ct

HERE = os.path.dirname(os.path.abspath(__file__))
T0 = 1_790_000_000.0
OK_RES = {"written": 12, "dup": 83, "messages": 95, "topics": 6, "saved": 5, "not_downloaded": 1,
          "window": {"newest_utc": "2026-09-30T08:46:53+00:00", "oldest_utc": "2026-09-23T09:05:04+00:00"}}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turbobaby_TESTING_svcclock_")
        self.state = os.path.join(self.tmp, "state.json")
        self.lock = os.path.join(self.tmp, "archive.lock")
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tick(self, now, runner=None, environ=None, file_env=None, every=720):
        return ct.tick(now=now, path=self.state, runner=runner or self.ok_runner,
                       environ={} if environ is None else environ,
                       file_env={} if file_env is None else file_env,
                       every=every, lock_file=self.lock, clock=lambda: now)

    def ok_runner(self):
        self.calls.append("run")
        return 0, dict(OK_RES)

    def st(self):
        return ct.read_state(self.state)


class TestSrok(_Tmp):
    def test_srok_ne_nastupil_zahoda_net(self):
        ct.write_state({"seq": 3, "done_seq": 3, "ran_at": T0, "next_at": T0 + 720 * 60,
                        "outcome": "ok"}, self.state)
        before = self.st()
        r = self.tick(T0 + 719 * 60)
        self.assertEqual(self.calls, [], "заход пошёл раньше срока")
        self.assertFalse(r["ran"])
        self.assertEqual(self.st(), before, "штамп тронут без захода")

    def test_srok_nastupil_rovno_odin_zahod(self):
        r = self.tick(T0)                          # штампа нет — первый заход сразу
        self.assertEqual(self.calls, ["run"])
        self.assertEqual((r["ran"], r["outcome"], r["written"]), (True, "ok", 12))
        st = self.st()
        self.assertEqual((st["seq"], st["done_seq"], st["outcome"], st["written"]), (1, 1, "ok", 12))
        self.assertEqual(st["next_at"], T0 + 720 * 60)
        self.assertEqual(st["newest_utc"], "2026-09-30T08:46:53+00:00")
        self.tick(T0 + 180)                        # следующий оборот демона — захода нет
        self.assertEqual(self.calls, ["run"])
        self.tick(T0 + 720 * 60)                   # через 12 ч — второй
        self.assertEqual(self.calls, ["run", "run"])
        self.assertEqual(self.st()["total_written"], 24)

    def test_zayavka_do_zahoda(self):
        seen = {}

        def runner():
            seen.update(ct.read_state(self.state))
            return 0, dict(OK_RES)

        self.tick(T0, runner=runner)
        self.assertEqual((seen.get("seq"), seen.get("running"), seen.get("ran_at")), (1, True, T0))
        self.assertIsNone(seen.get("done_seq"), "заявка выдала себя за исход")
        self.assertFalse(self.st()["running"])

    def test_chasy_nazad_ne_zapirayut(self):
        ct.write_state({"seq": 1, "done_seq": 1, "ran_at": T0 + 10 ** 6}, self.state)
        self.tick(T0)
        self.assertEqual(self.calls, ["run"])

    def test_zamok_arhiva_zanyat_zahod_otlozhen_bez_zayavki(self):
        """Замок общий с chatlog_ingest: занят — ни захода, ни заявки; следующий оборот пойдёт."""
        import chatlog_ingest as ingest
        self.assertTrue(ingest.lock_take(self.lock, T0))
        try:
            r = self.tick(T0)
            self.assertEqual(self.calls, [])
            self.assertFalse(r["ran"])
            self.assertEqual(self.st(), {}, "заявка без захода заперла бы срок на 12 ч")
        finally:
            ingest.lock_drop(self.lock)
        self.tick(T0 + 180)
        self.assertEqual(self.calls, ["run"])
        self.assertFalse(os.path.exists(self.lock), "замок остался висеть")


class TestOshibka(_Tmp):
    def test_isklyuchenie_zapisano_povtora_net(self):
        tajna = "механик написал номер клиента +66 81 000 0000"

        def padaet():
            self.calls.append("run")
            raise ConnectionError(tajna)

        r = self.tick(T0, runner=padaet)
        self.assertEqual((r["ran"], r["outcome"], r["error"]), (True, "error", "ConnectionError"))
        st = self.st()
        self.assertEqual((st["done_seq"], st["outcome"], st["error"], st["errors"]),
                         (1, "error", "ConnectionError", 1))
        with io.open(self.state, encoding="utf-8") as f:
            self.assertNotIn("+66", f.read(), "текст исключения лёг в штамп")
        self.tick(T0 + 180, runner=padaet)         # повтора нет до срока
        self.tick(T0 + 719 * 60, runner=padaet)
        self.assertEqual(self.calls, ["run"], "ошибка открыла срок заново")
        self.assertFalse(os.path.exists(self.lock), "замок остался после падения")

    def test_kod_vozvrata_zapisan_slovami(self):
        r = self.tick(T0, runner=lambda: (3, None))
        st = self.st()
        self.assertEqual((r["outcome"], st["outcome"], st["error"]), ("error", "error", "код 3"))
        self.assertIn("не авторизованы", st["why"])
        self.assertNotIn("written", st, "у ошибки нет счёта строк")

    def test_schet_proshlogo_zahoda_ne_vydaetsya_za_novyy(self):
        self.tick(T0)
        self.tick(T0 + 720 * 60, runner=lambda: (5, None))
        st = self.st()
        self.assertEqual(st["outcome"], "error")
        self.assertNotIn("written", st)
        self.assertEqual(st["total_written"], 12)


class TestFlag(_Tmp):
    def test_flag_vyklyucheniya_v_telegram_ne_hodim(self):
        r = self.tick(T0, environ={ct.OFF_ENV: "1"})
        self.assertEqual(self.calls, [], "флаг не остановил захват")
        self.assertEqual((r["ran"], r["outcome"]), (False, "skip"))
        st = self.st()
        self.assertEqual((st["outcome"], st["done_seq"]), ("skip", 1))
        self.assertIn(ct.OFF_ENV, st["why"])
        self.tick(T0 + 180, environ={ct.OFF_ENV: "1"})
        self.assertEqual(self.st()["seq"], 1, "пропуск пишется каждый оборот — это шум, а не исход")

    def test_flag_iz_fayla_glavnee_okruzheniya(self):
        self.tick(T0, environ={ct.OFF_ENV: "1"}, file_env={ct.OFF_ENV: "0"})
        self.assertEqual(self.calls, ["run"], "свежий .env не перебил унаследованное окружение")
        self.tick(T0 + 720 * 60, environ={}, file_env={ct.OFF_ENV: "1"})
        self.assertEqual(self.calls, ["run"], "строка в .env не выключила часы без рестарта")

    def test_obshchiy_flag_gasit_i_eti_chasy(self):
        self.tick(T0, environ={ct.COMMON_OFF_ENV: "1"})
        self.assertEqual(self.calls, [])

    def test_period_nol_vyklyuchaet(self):
        self.assertEqual(ct.every_min("0"), 0)
        r = self.tick(T0, every=0)
        self.assertEqual((self.calls, r["outcome"]), ([], "skip"))


class TestRuchki(unittest.TestCase):
    def test_period_pol_i_defolt(self):
        self.assertEqual(ct.every_min(""), ct.DEFAULT_EVERY_MIN)
        self.assertEqual(ct.every_min("7"), ct.MIN_EVERY_MIN, "опечатка превратила часы в долбёжку")
        self.assertEqual(ct.every_min("мусор"), ct.DEFAULT_EVERY_MIN)
        self.assertEqual(ct.DEFAULT_EVERY_MIN, 720)

    def test_shtamp_testa_ne_boevoy(self):
        self.assertTrue(ct.DEFAULT_STATE.endswith(os.path.join("tmp", "chatlog_servicing_tick",
                                                               "state.json")))
        self.assertEqual(ct.state_path("x.json"), "x.json")

    def test_due_chistaya(self):
        import inspect
        src = inspect.getsource(ct.due)
        for zapret in ("os.", "io.", "open(", "time.", "getenv", "environ"):
            self.assertNotIn(zapret, src)

    def test_zahod_eto_tot_zhe_fetch_run(self):
        """Боевой заход — ровно `chatlog_servicing_fetch.run`, по копии сессии; своей сети здесь нет."""
        import inspect
        src = inspect.getsource(ct._default_runner)
        self.assertIn("fetch.run(sv.WINDOW_DAYS, sv.WINDOW_LIMIT, False, sv.BUDGET_S)", src)
        with io.open(os.path.join(HERE, "chatlog_servicing_tick.py"), encoding="utf-8") as f:
            whole = f.read()
        for zapret in ("TelegramClient", "send_message", "turbobaby_session"):
            self.assertNotIn(zapret, whole)


class TestDemonZovetChasy(unittest.TestCase):
    """Вызывающий обязан БЫТЬ: исполняемая строка спавна в `_chatlog_capture`, после замка тестов."""

    def test_spavn_posle_zamka_testov_i_bez_importa(self):
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as f:
            src = f.read()
        i = src.index("def _chatlog_capture(")
        telo = src[i:src.index("\ndef ", i + 10)]
        kod = [s.split("#", 1)[0] for s in telo.split("\n")]
        kod = "\n".join(kod)
        self.assertIn('os.path.join(REPO, "chatlog_servicing_tick.py")', kod)
        self.assertIn("_svc_clock_report()", kod)
        self.assertLess(kod.index('if "unittest" in sys.modules:'),
                        kod.index('os.path.join(REPO, "chatlog_servicing_tick.py")'),
                        "замок тестов стои́т после спавна — гейт стал бы заходить в Telegram")
        self.assertNotIn("import chatlog_servicing_tick", src)


if __name__ == "__main__":
    unittest.main()
