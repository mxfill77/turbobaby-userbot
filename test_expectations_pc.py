# -*- coding: utf-8 -*-
"""Регресс СЛОЯ ОЖИДАНИЙ ПОЛОСЫ ПК (О1, О2) + инвариант EXPECT_PC_PURE.

Три вещи, ради которых этот файл существует:
  1. ЗАМОК ПРОТИВ ЛОЖНОГО ЗЕЛЁНОГО покрыт В ОБЕ СТОРОНЫ. «Проверить невозможно» обязано давать
     «НЕИЗВЕСТНО», а не «жив»; «проверить возможно» — обязано давать настоящий вердикт. Каждая
     дырка в фактах проверена отдельным кейсом, и рядом стои́т кейс, где та же ветка при полном
     факте говорит по существу. Инвариант молчания без второй половины — это молчание, а не замок.
  2. ФОРМАТ ФАКТОВ = ЖИВОЙ ФОРМАТ. Фикстуры дословно повторяют прод: heartbeat —
     `2026-08-10T21:42:25.869518+00:00` (снят с боевого файла 11.08.2026), строка очереди — поля
     моста (`id/status/lane/from/created/updated/task_text`), отметка старта — словарь
     `{"at","pid","proc","child"}` из `pc_orchestrator.task_started.json`. Идеализированной схемы
     «как удобно тесту» здесь нет ни одной.
  3. ГРАНИЦА ДЕРЖИТСЯ УСТРОЙСТВОМ, А НЕ ДОКСТРИНГОМ: ast-разбор доказывает, что решение не умеет
     ничего, кроме арифметики над переданными фактами, а руки не умеют мутировать очередь и
     перезапускать процессы. И проверяется, что инвариант ЛОВИТ внесённое нарушение.

Запуск — тем же способом, что и весь гейт репозитория (способ запуска — часть формата):
    venv\\Scripts\\python.exe -m unittest test_expectations_pc
"""
import ast
import datetime
import json
import os
import sys
import tempfile
import types
import unittest

import expectations_pc as ex
import expectations_pc_run as run_mod

REPO = os.path.dirname(os.path.abspath(__file__))
EX_SRC = os.path.join(REPO, "expectations_pc.py")
RUN_SRC = os.path.join(REPO, "expectations_pc_run.py")

HB_LIVE = "2026-08-10T21:42:25.869518+00:00"     # ДОСЛОВНО из боевого pc_orchestrator.heartbeat
NOW = 1786398500.0                       # «сейчас» = 2026-08-10T21:48:20Z, через 354с после HB_LIVE


def row(tid, status, since, lane="pc", frm="Filipp-328-dev", text="", free=None):
    """Строка снимка очереди в том виде, в каком её кладут руки (`queue_facts`)."""
    r = {"id": tid, "status": status, "lane": lane, "from": frm, "since": since, "text": text}
    if free is not None:
        r["free_wait"] = free
    return r


def stamp(declared_ago=None, now=NOW, ok=True, err=""):
    """Факт штампа занятости в ЖИВОМ виде, который кладут руки (`busy_facts`): ВРЕМЯ ПРАВКИ
    реестра отметок, а не поле внутри него. `declared_ago=None` = реестр прочитан, объявлять
    нечего; `ok=False` = реестр не прочитан (третий исход)."""
    return {"ok": ok, "since": None if declared_ago is None else now - declared_ago,
            "limit": ex.TASK_TIMEOUT_SEC, "err": err}


def facts(rows=None, ok=True, hb=HB_LIVE, hb_ok=True, silence=None, busy=None, now=NOW, err=""):
    return {
        "now": now,
        "queue": {"ok": ok, "rows": list(rows or []), "dt": 1.0, "err": err},
        "heartbeat": {"ok": hb_ok, "raw": hb, "err": err},
        "silence": silence if silence is not None else {"measured": True, "awake": 0.0, "why": ""},
        # По умолчанию — ПРОЧИТАННЫЙ пустой реестр, а не отсутствие факта: в проде руки кладут
        # сюда словарь ВСЕГДА, и `None` означает «факта нет вовсе» (отдельный, третий исход).
        "busy": stamp(now=now) if busy is None else busy,
    }


def hb_at(seconds_ago, now=NOW):
    """ISO-время оборота, отстоящее от `now` на seconds_ago — в ЖИВОМ формате heartbeat."""
    import datetime
    return datetime.datetime.fromtimestamp(now - seconds_ago,
                                           datetime.timezone.utc).isoformat()


class TestConfig(unittest.TestCase):
    """Пороги: дефолты замера, подмена окружением, ноль как ОБЪЯВЛЕННЫЙ откат."""

    def test_defaults_are_the_measured_numbers(self):
        c = ex.config({})
        self.assertEqual((c["new"], c["run"], c["turn"]), (2700.0, 6000.0, 1200.0))

    def test_env_overrides_and_garbage_falls_back(self):
        self.assertEqual(ex.config({"EXPECT_PC_TURN_MIN": "30"})["turn"], 1800.0)
        self.assertEqual(ex.config({"EXPECT_PC_TURN_MIN": "30,5"})["turn"], 1830.0)
        self.assertEqual(ex.config({"EXPECT_PC_TURN_MIN": "мусор"})["turn"], 1200.0)
        self.assertEqual(ex.config({"EXPECT_PC_TURN_MIN": ""})["turn"], 1200.0)

    def test_zero_kills_the_branch_entirely(self):
        """Ноль — объявленный откат, а не «дефолт»: ветка обязана умереть ДО чтения фактов."""
        f = facts([row(1, "in_progress", NOW - 99999)], hb=hb_at(99999),
                  silence={"measured": True, "awake": 99999.0, "why": ""})
        self.assertEqual(ex.verdict(f, ex.config({})) and True, True)   # с дефолтами вердикт есть
        cfg0 = ex.config({"EXPECT_PC_NEW_MIN": "0", "EXPECT_PC_RUN_MIN": "0",
                          "EXPECT_PC_TURN_MIN": "0"})
        self.assertEqual(ex.verdict(f, cfg0), [])
        self.assertEqual(ex.turn_state(f, cfg0, NOW)[0], ex.TURN_UNKNOWN)


class TestO1New(unittest.TestCase):
    """О1, ветка «строку не берут при свободном исполнителе»."""

    def test_fires_on_pure_wait_over_limit(self):
        v = ex.verdict(facts([row(53, "new", NOW - 30000, free=2800)]))
        self.assertEqual([x["kind"] for x in v], ["o1_pc_new"])
        self.assertEqual(v[0]["key"], "o1n|53|%d" % int(NOW - 30000))

    def test_silent_while_lane_busy(self):
        """Строка, ждущая за исполняемой задачей, ждёт ЗАКОННО — полоса одноворкерная."""
        v = ex.verdict(facts([row(53, "new", NOW - 30000, free=2800),
                              row(52, "in_progress", NOW - 600)]))
        self.assertEqual([x["kind"] for x in v], [])

    def test_silent_when_pure_wait_below_limit(self):
        """Возраст строки велик, а ЧИСТОЕ ожидание мало: правило судит второе (замер 22.07–11.08 —
        по возрасту это 16 ложных флагов за 20 суток при пороге 30 мин)."""
        self.assertEqual(ex.verdict(facts([row(53, "new", NOW - 30000, free=900)])), [])

    def test_approved_is_judged_and_needs_approval_is_not(self):
        """`approved` ждёт исполнителя (судим), `needs_approval` ждёт ЖИВОГО ВЛАДЕЛЬЦА (не судим)."""
        self.assertEqual([x["kind"] for x in
                          ex.verdict(facts([row(70, "approved", NOW - 30000, free=2800)]))],
                         ["o1_pc_new"])
        self.assertEqual(ex.verdict(facts([row(70, "needs_approval", NOW - 99999, free=99999)])), [])

    def test_step_of_chain_waiting_for_sibling_is_silent(self):
        """Зеркало гварда последовательности демона: шаг ждёт сиблинга — это не нарушение."""
        rows = [row(80, "new", NOW - 30000, text="[шаг 2/3 родитель 77]", free=2800),
                row(79, "needs_approval", NOW - 100, text="[шаг 1/3 родитель 77]")]
        self.assertEqual(ex.verdict(facts(rows)), [])

    def test_other_lane_is_not_ours(self):
        self.assertEqual(ex.verdict(facts([row(53, "new", NOW - 30000, lane="vps", free=99999)])),
                         [])


class TestO1Run(unittest.TestCase):
    """О1, ветка «строка замерла в работе»."""

    def test_fires_over_limit(self):
        v = ex.verdict(facts([row(353, "in_progress", NOW - 6100)]))
        self.assertEqual([x["kind"] for x in v], ["o1_pc_run"])

    def test_silent_inside_the_lanes_own_budget(self):
        """45 мин прогона + 45 мин повтора + реапер одиночек 90 мин — всё это ЗАКОННО."""
        for age in (2600, 2712, 5400, 5990):
            self.assertEqual(ex.verdict(facts([row(353, "in_progress", NOW - age)])), [],
                             "возраст %sс не должен рождать заметку" % age)


class TestO2Turn(unittest.TestCase):
    """О2 — демон даёт РЕЗУЛЬТАТ, а не просто существует."""

    def test_fresh_heartbeat_is_ok(self):
        st, info = ex.turn_state(facts(hb=hb_at(120)), ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_OK)
        self.assertLess(info["wall"], 1200)

    def test_silent_when_awake_silence_over_limit(self):
        f = facts(hb=hb_at(4000), silence={"measured": True, "awake": 3900.0, "why": ""})
        st, info = ex.turn_state(f, ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_SILENT)
        v = ex.verdict(f)
        self.assertEqual([x["kind"] for x in v], ["o2_pc_turn"])
        self.assertEqual(v[0]["key"], "o2t|%d" % int(ex.parse_iso(hb_at(4000))))

    def test_running_pass_is_work_not_a_standstill(self):
        """Урок сервера, воспроизведённый здесь: идущий ОБЪЯВЛЕННЫЙ заход тишину оправдывает.
        Контрфакт своей полосы — без этого замка порог 20 мин даёт 25 ложных заметок за 20 суток."""
        f = facts(hb=hb_at(2000), silence={"measured": True, "awake": 2000.0, "why": ""},
                  busy=stamp(1900))
        self.assertEqual(ex.turn_state(f, ex.config({}), NOW)[0], ex.TURN_OK)
        self.assertEqual(ex.verdict(f), [])

    def test_declared_pass_answers_even_when_silence_is_not_measured(self):
        """ЖИВОЙ СЛУЧАЙ 11.08.2026, первый прогон наблюдателя: heartbeat отставал на 30 мин, потому
        что демон синхронно исполнял задачу 469. Тишина ещё не измерена (наблюдение первое), но
        штамп занятости — ПОЛОЖИТЕЛЬНЫЙ факт с потолком, и отвечать на него «неизвестно» значило бы
        прятать известное."""
        f = facts(hb=hb_at(1800), silence={"measured": False, "awake": None,
                                           "why": "прошлого наблюдения нет"},
                  busy=stamp(1700))
        st, info = ex.turn_state(f, ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_OK)
        self.assertEqual(info["busy"]["age"], 1700.0)
        self.assertEqual(ex.verdict(f), [])
        # А тот же прогон БЕЗ штампа обязан честно сказать «неизвестно».
        self.assertEqual(ex.turn_state(dict(f, busy=None), ex.config({}), NOW)[0], ex.TURN_UNKNOWN)

    def test_pass_that_outlived_its_declared_budget_still_speaks(self):
        """ЗУБЫ ЦЕЛЫ: слепота ограничена сверху объявленным сроком + хвост оборота (55 мин)."""
        f = facts(hb=hb_at(4000), silence={"measured": True, "awake": 4000.0, "why": ""},
                  busy=stamp(3400))
        st, info = ex.turn_state(f, ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_SILENT)
        self.assertEqual(info["overdue"]["age"], 3400.0)
        line = ex.render(ex.verdict(f)[0])
        self.assertIn("свой kill не сработал", line)
        # И ни одного «id=» в этой фразе: предмет отметки — время, а не имя строки очереди.
        self.assertNotIn("id=", line)

    def test_dead_instance_stamp_blinds_no_longer_than_its_ceiling(self):
        """Штамп умершего экземпляра не оправдывает НИЧЕГО дольше 55 минут — иначе смерть посреди
        задачи выглядела бы работой вечно."""
        f = facts(hb=hb_at(99999), silence={"measured": True, "awake": 99999.0, "why": ""},
                  busy=stamp(86400))
        self.assertEqual(ex.turn_state(f, ex.config({}), NOW)[0], ex.TURN_SILENT)

    def test_sleep_of_the_machine_is_not_a_standstill(self):
        """Стенной возраст 9 часов, бодрствования — 3 минуты: это сон ПК, а не вставший демон."""
        f = facts(hb=hb_at(32400), silence={"measured": True, "awake": 180.0, "why": ""})
        st, info = ex.turn_state(f, ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_OK)
        self.assertGreater(info["slept"], 30000)


# ДОСЛОВНО из боевого `pc_orchestrator.task_started.json` (снято 18.08.2026). Ключевое здесь —
# ПЕРЕВЁРНУТЫЙ порядок: у ИДУЩЕЙ задачи 40 поле `at` от 15.08, потому что её номер уже лежал в
# реестре и `_task_started_mark` ушёл в ранний возврат; свежайшая ОТМЕТКА принадлежит задаче 34,
# закрытой тремя часами раньше (`dur_s=475.70 outcome=done`, её headless 15580 в системе нет).
REG_LIVE_1708 = {
    "34": {"at": "2026-08-17T16:24:16.926550+00:00", "pid": 5540,
           "proc": "5540-1786975731", "child": 15580},
    "40": {"at": "2026-08-15T17:27:44.115170+00:00", "pid": 21216,
           "proc": "21216-1786723500", "child": 22100},
}


class TestFalseAlarmOfTheSeventeenth(unittest.TestCase):
    """ОБА ОТРИЦАТЕЛЬНЫХ ТЕСТА — НА ЖИВОМ ФАЙЛЕ И ЖИВОЙ ХРОНОЛОГИИ, а не на похожей выдумке.

    Разбор [`docs/artifacts/2026-08-17-false-alarm-turn.md`]: 17.08 в 19:15 UTC О2 крикнула «демон
    ПК не даёт оборота» ровно в ту минуту, когда демон СИНХРОННО исполнял задачу 40 (взята
    18:58:01, сдана 19:37:26 — 2365 с при потолке 2700, `outcome=done`). Реестр отметок был тронут
    спавном headless за 17 минут до тика, но самой свежей ОТМЕТКОЙ `at` осталась закрытая задача
    34. Сторож самого демона на ТОМ ЖЕ файле в ту же минуту писал «демон работает, не трогаю».
    Ложных тревог по этой причине — 4 из 4 за всю жизнь наблюдателя, верных — ноль."""

    HB_1 = "2026-08-17T18:55:01+00:00"              # последний оборот poll_once до тика
    MT_1 = "2026-08-17T18:58:02.402896+00:00"       # RUN id=40: спавн headless → правка реестра
    TICK_1 = "2026-08-17T19:15:16.926550+00:00"     # окно тика по пересечению двух улик (probe2)
    # Пятая тревога — та, что была ПРЕДСКАЗАНА за девять минут до события и сбылась в ту же секунду
    # тем же ключом эпизода `o2t|1786997057` (§9 разбора), уже во время самой разведки.
    HB_2 = "2026-08-17T20:04:17.037191+00:00"
    MT_2 = "2026-08-17T20:06:02.402896+00:00"       # RUN id=41
    TICK_2 = "2026-08-17T20:24:29.445954+00:00"     # `first` из tmp/expect_pc/state.json

    def _registry(self, mtime_iso, records=None):
        """Живой реестр на диске с назначенным временем ПРАВКИ → путь."""
        p = os.path.join(tempfile.mkdtemp(prefix="expect_pc_1708_"), "task_started.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(REG_LIVE_1708 if records is None else records, f, ensure_ascii=False)
        mt = ex.parse_iso(mtime_iso)
        os.utime(p, (mt, mt))
        return p

    def _facts(self, hb_iso, mt_iso, tick_iso):
        """Факты тика ЦЕЛИКОМ через боевые руки: штамп занятости снимает сам `busy_facts`."""
        now = ex.parse_iso(tick_iso)
        wall = now - ex.parse_iso(hb_iso)
        return facts(hb=hb_iso, now=now,
                     # сна машины в этом эпизоде не было — тишина бодрствования равна стенной
                     silence={"measured": True, "awake": wall, "why": ""},
                     busy=run_mod.busy_facts(self._registry(mt_iso))), now

    def test_the_fixture_is_poisoned_by_the_old_subject(self):
        """ЗАМОК ФИКСТУРЫ: по ПРЕЖНЕМУ предмету (поле `at`) она обязана давать тревогу. Без этой
        проверки отрицательный тест ниже мог бы зеленеть по недоразумению, а не по починке."""
        newest = max(ex.parse_iso(v["at"]) for v in REG_LIVE_1708.values())
        for tick, gap in ((self.TICK_1, 10260.0), (self.TICK_2, 14412.5)):
            age = ex.parse_iso(tick) - newest
            self.assertAlmostEqual(age, gap, delta=1.0)          # дословно из probe2 / probe4
            self.assertGreater(age, ex.TASK_TIMEOUT_SEC + ex.BUSY_GRACE_SEC,
                               "самая свежая ОТМЕТКА старше потолка доверия — это и была тревога")

    def test_long_legal_work_raises_no_alarm(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1: идёт долгая ЗАКОННАЯ работа дольше порога → тревоги НЕТ.
        Ровно тот случай, на котором прибор соврал 17.08, и оба его окна."""
        cfg = ex.config({})
        for hb, mt, tick, age in ((self.HB_1, self.MT_1, self.TICK_1, 1034.5),
                                  (self.HB_2, self.MT_2, self.TICK_2, 1107.0)):
            f, now = self._facts(hb, mt, tick)
            st, info = ex.turn_state(f, cfg, now)
            self.assertGreater(info["wall"], cfg["turn"], "тишина ЧЕСТНО перешла порог — %s" % tick)
            self.assertEqual(st, ex.TURN_OK, "идущая работа остановкой не является — %s" % tick)
            self.assertAlmostEqual(info["busy"]["age"], age, delta=1.0)
            self.assertEqual(ex.verdict(f, cfg), [], "владельцу не уходит ничего — %s" % tick)

    def test_no_work_and_no_turn_does_raise_the_alarm(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2: работы нет и оборота нет дольше порога → тревога ЕСТЬ.
        Зубы целы: тот же файл, но объявление работы НЕ ПОЗЖЕ последнего оборота — значит тот
        заход этим оборотом уже замкнут, и молчание ПОСЛЕ него он не оправдывает."""
        cfg = ex.config({})
        f, now = self._facts(self.HB_1, "2026-08-17T18:40:00+00:00", self.TICK_1)
        st, info = ex.turn_state(f, cfg, now)
        self.assertEqual(st, ex.TURN_SILENT)
        v = ex.verdict(f, cfg)
        self.assertEqual([x["kind"] for x in v], ["o2_pc_turn"])
        line = ex.render(v[0])
        self.assertIn("демон ПК не даёт оборота", line)
        # Захода не объявлено вовсе → фразы про несработавший kill в карточке быть не должно.
        self.assertIsNone(info.get("overdue"))
        self.assertNotIn("свой kill не сработал", line)

    def test_the_third_state_is_a_fresh_turn_without_any_work(self):
        """ТРЕТЬЕ СОСТОЯНИЕ ИЗ ТРЁХ: работы нет, но оборот СВЕЖИЙ — это тоже молчание, и приходит
        оно другой дорогой (порог тишины), а не через штамп."""
        cfg = ex.config({})
        f, now = self._facts("2026-08-17T19:10:00+00:00", "2026-08-17T18:40:00+00:00", self.TICK_1)
        st, info = ex.turn_state(f, cfg, now)
        self.assertEqual(st, ex.TURN_OK)
        self.assertLess(info["wall"], cfg["turn"])
        self.assertIsNone(info.get("busy"), "свежий оборот штампа не спрашивает вовсе")
        self.assertEqual(ex.verdict(f, cfg), [])


class TestThirdOutcome(unittest.TestCase):
    """ЗАМОК ПРОТИВ ЛОЖНОГО ЗЕЛЁНОГО — обе стороны. Каждая дырка в фактах даёт НЕИЗВЕСТНО, и
    рядом стои́т кейс, где та же ветка при полном факте отвечает по существу."""

    def test_unknown_never_says_alive(self):
        cfg = ex.config({})
        holes = [
            ("факта о heartbeat нет вовсе", dict(facts(), heartbeat=None)),
            ("файл не прочитан", facts(hb_ok=False, hb="", err="FileNotFoundError")),
            ("время в heartbeat не разобрано", facts(hb="не время вовсе")),
            ("тишина не измерена (первый прогон)",
             facts(hb=hb_at(4000), silence={"measured": False, "awake": None,
                                            "why": "прошлого наблюдения нет"})),
            ("часов бодрствования нет",
             facts(hb=hb_at(4000), silence={"measured": False, "awake": None,
                                            "why": "часов бодрствования на этой машине нет"})),
            # ТРЕТИЙ ИСХОД ШТАМПА ЗАНЯТОСТИ. Реестр отметок не прочитан — значит отличить идущую
            # работу от остановки нечем, и это «неизвестно», а не приговор и не «в порядке».
            ("реестр отметок не прочитан",
             facts(hb=hb_at(4000), silence={"measured": True, "awake": 3900.0, "why": ""},
                   busy=stamp(ok=False, err="PermissionError: [Errno 13] Permission denied"))),
            ("факта о штампе занятости нет вовсе",
             dict(facts(hb=hb_at(4000), silence={"measured": True, "awake": 3900.0, "why": ""}),
                  busy=None)),
        ]
        for name, f in holes:
            st, info = ex.turn_state(f, cfg, NOW)
            self.assertEqual(st, ex.TURN_UNKNOWN, "«%s» обязано быть НЕИЗВЕСТНО" % name)
            self.assertNotEqual(st, ex.TURN_OK)
            self.assertTrue(info.get("why"), "у незнания обязана быть названа причина")
            self.assertEqual(ex.verdict(f, cfg), [], "незнание не приговор: заметки быть не должно")

    def test_the_same_branch_speaks_when_the_fact_is_whole(self):
        """Вторая половина замка: молчание обязано кончаться там, где факт появился."""
        cfg = ex.config({})
        self.assertEqual(ex.turn_state(facts(hb=hb_at(60)), cfg, NOW)[0], ex.TURN_OK)
        self.assertEqual(ex.turn_state(
            facts(hb=hb_at(4000), silence={"measured": True, "awake": 3900.0, "why": ""}),
            cfg, NOW)[0], ex.TURN_SILENT)

    def test_queue_without_snapshot_is_unknown_not_moving(self):
        cfg = ex.config({})
        st, info = ex.queue_state(facts(ok=False, err="BridgeTransportError"), cfg, NOW)
        self.assertEqual(st, ex.QUEUE_UNKNOWN)
        self.assertIn("BridgeTransportError", info["why"])
        self.assertEqual(ex.queue_state(dict(facts(), queue=None), cfg, NOW)[0], ex.QUEUE_UNKNOWN)
        # и наоборот: снимок есть → состояние называется по существу
        self.assertEqual(ex.queue_state(facts([row(1, "new", NOW - 60, free=0)]), cfg, NOW)[0],
                         ex.QUEUE_OK)
        self.assertEqual(ex.queue_state(facts([row(1, "in_progress", NOW - 60000)]), cfg, NOW)[0],
                         ex.QUEUE_STUCK)

    def test_unknown_does_not_close_an_open_episode(self):
        """Молчание источника выздоровлением не является — ни у очереди, ни у оборота."""
        cfg = ex.config({})
        keys = ["o1r|353|1", "o2t|1"]
        blind = facts(ok=False, hb_ok=False, err="мост молчит")
        self.assertEqual(ex.closures(blind, cfg, keys), [])
        # А при доказанном факте — закрываются оба.
        healthy = facts([], hb=hb_at(30))
        self.assertEqual(sorted(ex.closures(healthy, cfg, keys)), sorted(keys))

    def test_unknown_turn_does_not_close_o2(self):
        cfg = ex.config({})
        f = facts([], hb=hb_at(4000), silence={"measured": False, "awake": None, "why": "нет"})
        self.assertEqual(ex.closures(f, cfg, ["o2t|1"]), [])
        self.assertEqual(ex.closures(f, cfg, ["o1n|5|1"]), ["o1n|5|1"])   # очередь-то прочитана


class TestSilenceCounter(unittest.TestCase):
    """Накопление тишины в часах бодрствования — то место, где сон отделяется от молчания."""

    def test_first_observation_measures_nothing(self):
        st = {}
        out = run_mod.update_silence(st, {"ok": True, "raw": HB_LIVE}, 1000.0, NOW)
        self.assertFalse(out["measured"])
        self.assertIn("прошлого наблюдения нет", out["why"])

    def test_accumulates_only_while_the_turn_does_not_change(self):
        st = {}
        run_mod.update_silence(st, {"ok": True, "raw": HB_LIVE}, 1000.0, NOW)
        out = run_mod.update_silence(st, {"ok": True, "raw": HB_LIVE}, 1600.0, NOW + 600)
        self.assertTrue(out["measured"])
        self.assertAlmostEqual(out["awake"], 600.0)
        out = run_mod.update_silence(st, {"ok": True, "raw": "2026-08-10T22:00:00+00:00"},
                                     2200.0, NOW + 1200)
        self.assertAlmostEqual(out["awake"], 0.0, msg="новый оборот обязан обнулить тишину")

    def test_one_observation_counts_no_more_than_the_cap(self):
        """Наблюдатель мог не работать час — о том, крутился ли демон, мы не знаем ничего."""
        st = {}
        run_mod.update_silence(st, {"ok": True, "raw": HB_LIVE}, 1000.0, NOW)
        out = run_mod.update_silence(st, {"ok": True, "raw": HB_LIVE}, 1000.0 + 36000, NOW + 36000)
        self.assertAlmostEqual(out["awake"], run_mod.STEP_CAP_SEC)

    def test_reboot_is_not_silence(self):
        st = {}
        run_mod.update_silence(st, {"ok": True, "raw": HB_LIVE}, 90000.0, NOW)
        out = run_mod.update_silence(st, {"ok": True, "raw": HB_LIVE}, 12.0, NOW + 600)
        self.assertFalse(out["measured"])
        self.assertIn("перезагрузилась", out["why"])

    def test_no_awake_clock_means_unknown(self):
        out = run_mod.update_silence({}, {"ok": True, "raw": HB_LIVE}, None, NOW)
        self.assertFalse(out["measured"])
        self.assertIn("часов бодрствования", out["why"])


class TestWaitCounter(unittest.TestCase):
    """Чистое ожидание копится ТОЛЬКО при свободной полосе (иначе — 16 ложных за 20 суток)."""

    def test_busy_lane_does_not_grow_the_counter(self):
        st = {}
        f = facts([row(53, "new", NOW - 3000), row(52, "in_progress", NOW - 600)])
        run_mod.update_waits(st, f, NOW)
        run_mod.update_waits(st, f, NOW + 600)
        self.assertAlmostEqual(list(st["waits"].values())[0]["free"], 0.0)

    def test_free_lane_grows_and_is_capped(self):
        st = {}
        f = facts([row(53, "new", NOW - 3000)])
        run_mod.update_waits(st, f, NOW)
        run_mod.update_waits(st, f, NOW + 600)
        self.assertAlmostEqual(f["queue"]["rows"][0]["free_wait"], 600.0)
        run_mod.update_waits(st, f, NOW + 600 + 99999)
        self.assertAlmostEqual(f["queue"]["rows"][0]["free_wait"], 600.0 + run_mod.STEP_CAP_SEC)

    def test_no_snapshot_does_not_touch_counters(self):
        st = {"waits": {"53|1": {"free": 100.0, "seen": NOW}}}
        run_mod.update_waits(st, facts(ok=False), NOW + 600)
        self.assertEqual(st["waits"]["53|1"]["free"], 100.0)


class TestNotes(unittest.TestCase):
    """Форма заметки: без кнопок, без «да», с ОБОИМИ числами и с названной границей."""

    def test_o1_new_names_both_numbers(self):
        n = ex.render(ex.verdict(facts([row(53, "new", NOW - 30000, free=2800)]))[0])
        self.assertIn("53", n)
        self.assertIn("при СВОБОДНОЙ полосе", n)
        self.assertIn("порог 45 мин", n)
        self.assertTrue(n.endswith(ex.TAIL))
        for word in ("«да»", "кнопк", "approve"):
            self.assertNotIn(word, n.lower())

    def test_o2_note_names_sleep_separately(self):
        f = facts(hb=hb_at(4000), silence={"measured": True, "awake": 3900.0, "why": ""})
        n = ex.render(ex.verdict(f)[0])
        self.assertIn("по часам бодрствования", n)
        self.assertIn("сон машины", n)
        self.assertIn("жив ли процесс — по этому факту не сужу", n)

    def test_close_note_names_what_recovered(self):
        self.assertIn("демон ПК снова даёт оборот", ex.render_close("o2t|1"))
        self.assertIn("очередь ПК снова движется", ex.render_close("o1n|53|1"))


class TestRunHands(unittest.TestCase):
    """Руки: один эпизод — одна заметка, закрытие объявляется, состояние живёт в СВОЁМ каталоге."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="expect_pc_test_")
        os.environ["CC_EXPECT_PC_DIR"] = self.dir
        self.sent = []
        self.addCleanup(os.environ.pop, "CC_EXPECT_PC_DIR", None)
        # Реестр отметок старта тоже инъектируем: тест, читающий БОЕВОЙ файл, зелен или красен от
        # того, что сейчас делает демон, — ровно тот класс «тест ≠ формат», который здесь и чиним.
        self.addCleanup(setattr, run_mod, "busy_facts", run_mod.busy_facts)
        # Подмена отдаёт ПРОЧИТАННЫЙ пустой реестр, а не `None`: «объявлять нечего» и «прочитать
        # не удалось» — разные исходы, и вторым руки уводило бы каждый кейс в «неизвестно».
        run_mod.busy_facts = lambda path=None: {"ok": True, "since": None,
                                                "limit": ex.TASK_TIMEOUT_SEC, "err": ""}
        # ПУЛЬСОВЫЙ КАНАЛ ГЛУШИМ НА ВЕСЬ КЛАСС, а не аргументом в каждом вызове: сайтов `run()`
        # здесь семь, и восьмой, дописанный завтра, снова ушёл бы в ЖИВОЙ журнал владельца — так и
        # родился класс 19.08.2026 (21 строка из 26 за сутки — тест-происхождения). Подменяем
        # МОДУЛЬНОЕ ИМЯ: руки берут его поздним поиском (`(pulser or send_pulse)`), поэтому замена
        # накрывает и будущие вызовы. Замок канала стои́т отдельно и ниже — здесь гигиена класса,
        # писавшегося до появления пульсовой ветки и о ней не знавшего.
        self.pulsed = []
        self.addCleanup(setattr, run_mod, "send_pulse", run_mod.send_pulse)
        run_mod.send_pulse = lambda line: (self.pulsed.append(line), True)[1]
        # ФАКТ О6 ИНЪЕКТИРУЕМ ПО ТОЙ ЖЕ ПРИЧИНЕ, ЧТО И РЕЕСТР ОТМЕТОК ВЫШЕ (02.09.2026): он
        # читает ЖИВЫЕ процессы этой машины, и кейсы про О1/О2 краснели бы от того, давно ли
        # владелец рестартил бота. Подмена отдаёт ПРОЧИТАННЫЙ факт «процесс моложе правки» —
        # то есть настоящее «свежо», а не отсутствие раздела: отсутствие раздела эту ветку
        # выключает целиком, и кейсы проверяли бы молчание вместо вердикта.
        self.addCleanup(setattr, run_mod, "code_facts", run_mod.code_facts)
        run_mod.code_facts = lambda *a, **k: {
            n: {"ok": True, "entry": e, "files": 5, "newest": NOW - 7200.0,
                "newest_file": "io_utf8.py", "reason": "", "gap": [], "mapped": 2, "pid": 100,
                "opened": True, "started": NOW - 60.0, "lock_mtime": NOW - 60.0, "err": ""}
            for n, e in ex.CODE_ENTRIES}

    def _note(self, text):
        """Канал теста. Возвращает True — как боевой: «заметка ушла» и «не ушла» руки различают
        по возврату, и подмена, всегда молчащая False, проверяла бы только ветку fail-safe."""
        self.sent.append(text)
        return True

    def _getter(self, rows):
        def get(status):
            return {"ok": True, "items": [r for r in rows if r["status"] == status]}
        return get

    def _hb(self, iso):
        run_mod.heartbeat_facts = lambda path=None: {"ok": True, "raw": iso, "err": ""}

    def test_healthy_contour_says_nothing(self):
        self.addCleanup(setattr, run_mod, "heartbeat_facts", run_mod.heartbeat_facts)
        self._hb(hb_at(60))
        out = run_mod.run(now=NOW, getter=self._getter([]), notifier=self._note)
        self.assertEqual((out["verdicts"], out["notes"], self.sent), (0, [], []))
        self.assertEqual(out["turn"], ex.TURN_OK)

    def test_one_episode_one_note_then_a_closure(self):
        self.addCleanup(setattr, run_mod, "heartbeat_facts", run_mod.heartbeat_facts)
        self._hb(hb_at(60))
        stuck = [{"id": 353, "status": "in_progress", "lane": "pc", "from": "Filipp-328-dev",
                  "updated": "2026-08-10T18:00:00.000Z", "created": "2026-08-10T18:00:00.000Z",
                  "task_text": "ultrathink ..."}]
        out1 = run_mod.run(now=NOW, getter=self._getter(stuck), notifier=self._note)
        self.assertEqual(len(out1["notes"]), 1)
        out2 = run_mod.run(now=NOW + 600, getter=self._getter(stuck), notifier=self._note)
        self.assertEqual(out2["notes"], [], "повтор заметки об одном эпизоде — это шум")
        out3 = run_mod.run(now=NOW + 1200, getter=self._getter([]), notifier=self._note)
        self.assertEqual(len(out3["closed"]), 1)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("ожидание снова выполняется", self.sent[1])

    def test_note_that_did_not_leave_is_not_marked(self):
        """Заметка не ушла → эпизод НЕ помечен: скажем на следующем прогоне (fail-safe)."""
        self.addCleanup(setattr, run_mod, "heartbeat_facts", run_mod.heartbeat_facts)
        self._hb(hb_at(60))
        stuck = [{"id": 353, "status": "in_progress", "lane": "pc", "from": "x",
                  "updated": "2026-08-10T18:00:00.000Z", "task_text": ""}]
        out = run_mod.run(now=NOW, getter=self._getter(stuck), notifier=lambda t: False)
        self.assertEqual(out["notes"], [])
        with open(os.path.join(self.dir, "state.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f).get("open"), {})

    def test_dead_bridge_gives_unknown_and_no_notes(self):
        self.addCleanup(setattr, run_mod, "heartbeat_facts", run_mod.heartbeat_facts)
        self._hb(hb_at(60))

        def boom(status):
            raise RuntimeError("BridgeTransportError")
        out = run_mod.run(now=NOW, getter=boom, notifier=self._note)
        self.assertEqual((out["queue"], out["notes"], self.sent), (ex.QUEUE_UNKNOWN, [], []))

    def test_state_never_lands_in_the_daemons_files(self):
        self.addCleanup(setattr, run_mod, "heartbeat_facts", run_mod.heartbeat_facts)
        self._hb(hb_at(60))
        run_mod.run(now=NOW, getter=self._getter([]), notifier=self._note)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "state.json")))
        self.assertEqual(run_mod._dir(), self.dir)


class TestNoteChannelAnswerIsAPair(unittest.TestCase):
    """06.09.2026: `send_note` РАЗБИРАЕТ пару `(channel, ok)`, а не берёт `bool()` от неё.

    Класс, который здесь заперт: непустой кортеж истинен ВСЕГДА, поэтому `bool(("none", False))`
    — это `True`. До правки отказ канала числился успехом, руки помечали эпизод сказанным
    (`open[key] = {"said": now}`) при ненаписанной заметке, и дальше заметка О1–О6 не повторялась
    НИКОГДА (`if was is not None: continue`), а О7 молчала до пола повтора — сутки. Ветка
    `if not send(...): continue  # не помечаем` была недостижима при живом канале.

    КАНАЛ ЗДЕСЬ ПОДСТАВНОЙ, И ЭТО НЕ ГИГИЕНА, А УСТРОЙСТВО ПРОВЕРКИ. Боевой `dispatch_notify` в
    процесс не импортируется вовсе: `send_note` зовёт `import dispatch_notify` ВНУТРИ себя, то
    есть берёт то, что лежит в `sys.modules`, — а там на время кейса стои́т модуль без токена,
    без сокета и без единой ветки отправки. Из проверки не уходит НИЧЕГО ни одной дорогой."""

    def _channel(self, answer):
        """Подменить модуль канала ответом `answer` и вернуть список позванных текстов."""
        seen = []
        fake = types.ModuleType("dispatch_notify")

        def deliver(text, reply_markup=None, declared=None):
            seen.append(text)
            return answer

        fake.deliver = deliver
        prev = sys.modules.get("dispatch_notify")

        def restore():
            if prev is None:
                sys.modules.pop("dispatch_notify", None)
            else:
                sys.modules["dispatch_notify"] = prev

        self.addCleanup(restore)
        sys.modules["dispatch_notify"] = fake
        return seen

    def test_channel_refusal_is_not_a_delivery(self):
        """Живая ветка отказа `deliver` — «бота нет»: `("none", False)`."""
        seen = self._channel(("none", False))
        self.assertIs(run_mod.send_note("заметка ожидания"), False)
        self.assertEqual(seen, ["заметка ожидания"], "канал обязан быть позван ровно один раз")

    def test_both_channels_down_is_not_a_delivery(self):
        """Фолбэк в личку тоже лёг — канал называет себя, но признак остаётся ложью."""
        self._channel(("DM", False))
        self.assertIs(run_mod.send_note("заметка ожидания"), False)

    def test_a_delivered_note_is_still_a_delivery(self):
        """Вторая половина замка: инвариант без неё запрещал бы успех вообще."""
        self._channel(("inbox", True))
        self.assertIs(run_mod.send_note("заметка ожидания"), True)

    def test_a_shape_that_is_not_a_pair_is_not_a_delivery(self):
        """Форма ответа, отличная от пары, — НЕ успех: распаковка срывается, ветка отдаёт False.
        Сторона ошибки выбрана: непомеченный эпизод скажется на следующем прогоне, а ложное
        «сказано» не чинится ничем."""
        self._channel(True)
        self.assertIs(run_mod.send_note("заметка ожидания"), False)

    def test_the_form_is_read_from_the_code_not_from_the_docstring(self):
        """Замок формы: в `send_note` есть распаковка пары и нет `bool()` прямо от вызова."""
        with open(RUN_SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "send_note"]
        self.assertEqual(len(fn), 1)
        pairs = [n for n in ast.walk(fn[0])
                 if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple)
                 and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "deliver"]
        self.assertEqual(len(pairs), 1, "пара канала обязана разбираться, а не сворачиваться")
        wrapped = [n for n in ast.walk(fn[0])
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "bool" and n.args and isinstance(n.args[0], ast.Call)]
        self.assertEqual(wrapped, [], "bool() от вызова канала — это и есть чинимый класс")

    def test_every_other_call_site_parses_the_pair_too(self):
        """Храповик на соседей: в `dispatch_notify` КАЖДЫЙ вызов `deliver` разбирает пару.
        Замер 06.09.2026 — 6 вызовов из 6; седьмым был `expectations_pc_run.send_note`."""
        with open(os.path.join(REPO, "dispatch_notify.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        calls, unpacked = 0, 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                fname = getattr(node.value.func, "id", getattr(node.value.func, "attr", ""))
                if fname == "deliver":
                    calls += 1
                    unpacked += int(isinstance(node.targets[0], ast.Tuple))
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "bool":
                inner = node.args[0] if node.args else None
                if isinstance(inner, ast.Call) and getattr(inner.func, "attr", "") == "deliver":
                    calls += 1                       # свёрнутая пара — вызов есть, разбора нет
        self.assertEqual((calls, unpacked), (6, 6))


class TestPulseChannelMutedInTestRun(unittest.TestCase):
    """СКВОЗНОЙ ЗАМОК 19.08.2026: боевые руки наблюдателя, позванные ИЗ прогона тестов, не рождают
    ни одной записи в ЖУРНАЛЕ ВЛАДЕЛЬЦА.

    Форма вызова здесь РОВНО ТА, что текла: `run()` БЕЗ `pulser=`, канал по умолчанию боевой
    (`send_pulse` → `dispatch_notify._cowork`). Поэтому класс НЕ глушит `send_pulse`, в отличие от
    соседа выше: предмет проверки — сам замок канала, а заглушённый канал проверял бы заглушку.

    Наблюдаем ПОСЛЕДНЮЮ дверь наружу — спавн процесса записи. Тревога РЕГИСТРИРУЕТСЯ, а не
    бросается: `_cowork` и ветка публикации ловят исключения себе в живот, и брошенный
    AssertionError был бы съеден вместе с дефектом."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="expect_pc_mute_")
        os.environ["CC_EXPECT_PC_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "CC_EXPECT_PC_DIR", None)
        for name in ("heartbeat_facts", "busy_facts"):
            self.addCleanup(setattr, run_mod, name, getattr(run_mod, name))
        run_mod.busy_facts = lambda path=None: {"ok": True, "since": None,
                                                "limit": ex.TASK_TIMEOUT_SEC, "err": ""}
        run_mod.heartbeat_facts = lambda path=None: {"ok": True, "raw": hb_at(60), "err": ""}

    def test_live_hands_from_a_test_run_spawn_no_journal_writer(self):
        import dispatch_notify                                       # noqa: PLC0415
        seen = []
        sub = dispatch_notify.subprocess
        self.addCleanup(setattr, sub, "Popen", sub.Popen)
        sub.Popen = lambda *a, **k: (seen.append(a), type("P", (), {"pid": 0})())[1]

        out = run_mod.run(now=NOW, getter=lambda status: {"ok": True, "items": []},
                          notifier=lambda t: True)

        # НЕ ВАКУУМ: ветка публикации обязана СРАБОТАТЬ и упереться в замок канала. Без этой
        # строки тест был бы зелен и от того, что предмет перестал задеваться вовсе («мок,
        # переставший задевать ветку, хуже отсутствующего»): состояние здесь пустое, значит
        # строке о детях ПОРА безусловно, а «не ушла» = канал ответил отказом.
        self.assertEqual(out["kids_pulse"], "не ушла")
        self.assertEqual(seen, [], "прогон тестов родил запись в ЖУРНАЛЕ ВЛАДЕЛЬЦА")


class TestBridgeDoesNotLeakTestFlag(unittest.TestCase):
    """ВТОРАЯ ПОЛОВИНА ЗАМКА 19.08.2026, которой не было 12 суток: боевая форма вызова НЕ метит
    процесс тест-флагом, и канал журнала после неё НЕ заглушён.

    Сосед выше проверяет «из-под тестов наружу не течёт» — и он зелен. Мёртвым же был обратный
    склон: `_bridge()` ставил `TURBOBABY_TEST_LOGS=1` НАВСЕГДА (`setdefault`, возврата нет), а
    боевой `run(getter=None)` зовёт его ПЕРВЫМ, до всякой публикации. К моменту `send_pulse`
    наблюдатель выглядел тест-прогоном для `dispatch_notify._cowork` и получал глухой отказ без
    спула. Двенадцать суток немоты, диагноз — `docs/artifacts/2026-09-01-pulse-silence-diagnosis.md`.

    ПОЧЕМУ ПРЕДМЕТ — ОКРУЖЕНИЕ, А НЕ СПАВН ПИСАТЕЛЯ. Спавна отсюда не увидеть НИКОГДА и по
    честной причине: мы сами исполняемся тест-раннером, и `log_setup._started_as_test_runner()`
    глушит канал по `argv[0]` независимо от флага. Утверждать «в бою запись пойдёт» изнутри
    прогона — это подгонять голден под удобство. Проверяем ровно ту переменную, которую портил
    дефект: после боевой формы окружение НЕ содержит следа флага и САМО ПО СЕБЕ канал не глушит
    (`is_test_context` с явным env, минуя признак способа запуска).

    Демон подменён: живой импорт `pc_orchestrator` в юните — это чужой боевой лог, боевые
    секреты и сеть к мосту. Подмена стои́т на `_daemon`, то есть цепочка `run → queue_facts →
    _bridge → _guard_test_logs` проходится ЦЕЛИКОМ и настоящая."""

    def setUp(self):
        import log_setup                                              # noqa: PLC0415
        self.log_setup = log_setup
        self.dir = tempfile.mkdtemp(prefix="expect_pc_leak_")
        os.environ["CC_EXPECT_PC_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "CC_EXPECT_PC_DIR", None)
        # Флаг мог быть выставлен раннером-соседом по гейту (`test_brain_writer` ставит его на
        # импорте модуля). Снимаем на время класса и возвращаем КАК БЫЛО: иначе предмет проверки
        # зависел бы от порядка файлов в прогоне.
        key = "TURBOBABY_TEST_LOGS"
        had, prev = key in os.environ, os.environ.get(key)
        self.addCleanup(lambda: os.environ.__setitem__(key, prev) if had
                        else os.environ.pop(key, None))
        os.environ.pop(key, None)
        for name in ("heartbeat_facts", "busy_facts", "_daemon"):
            self.addCleanup(setattr, run_mod, name, getattr(run_mod, name))
        run_mod.busy_facts = lambda path=None: {"ok": True, "since": None,
                                                "limit": ex.TASK_TIMEOUT_SEC, "err": ""}
        run_mod.heartbeat_facts = lambda path=None: {"ok": True, "raw": hb_at(60), "err": ""}
        self.pulsed = []
        self.addCleanup(setattr, run_mod, "send_pulse", run_mod.send_pulse)
        run_mod.send_pulse = lambda line: (self.pulsed.append(line), True)[1]

    def _fake_daemon(self):
        """Демон-подмена, ЗАПОМИНАЮЩАЯ значение флага на момент импорта. Без этой отметки тест
        зеленел бы и от «флаг не ставится вовсе» — а он обязан стоять на импорте."""
        self.seen_flag = os.environ.get("TURBOBABY_TEST_LOGS")

        class _BC:
            get_pending = staticmethod(lambda status: {"ok": True, "items": []})
        return type("D", (), {"bc": _BC})

    def test_live_form_leaves_no_test_flag_behind(self):
        run_mod._daemon = self._fake_daemon
        out = run_mod.run(now=NOW, getter=None, notifier=lambda t: True)

        self.assertEqual(self.seen_flag, "1", "импорт демона пошёл БЕЗ увода логов в temp")
        # ПРЕДМЕТ — ЗНАЧЕНИЕ КЛЮЧА, А НЕ ЧЛЕНСТВО В `os.environ`. Разница не стилистическая:
        # `assertNotIn(key, os.environ)` печатает при провале ВЕСЬ словарь окружения, а в нём
        # живут боевые токены моста и трёх ботов. Замок, чей отказ вываливает секреты в лог
        # гейта, дороже дефекта, который он ловит (свод §6: токены называть, а не цитировать).
        self.assertIsNone(os.environ.get("TURBOBABY_TEST_LOGS"),
                          "боевая форма пометила процесс тест-флагом — пульс снова глохнет на себе")
        # СВОЁ отделено от ЧУЖОГО: `TESTING`/`PYTEST_CURRENT_TEST` заявляет о себе САМ прогон
        # (`test_isolation`, соседи по гейту), и оставь мы их в снимке — замок был бы вечно красен
        # по причине, к дефекту не относящейся. Предмет здесь ровно один: не глушит ли журнал ТО,
        # что оставил после себя наблюдатель. Явный env к тому же минует признак способа запуска.
        left = {k: v for k, v in os.environ.items()
                if k not in ("TESTING", "PYTEST_CURRENT_TEST")}
        self.assertFalse(self.log_setup.is_test_context(left),
                         "окружение после боевой формы САМО глушит журнал владельца")
        # НЕ ВАКУУМ: ветка публикации обязана быть ЗАДЕТА, иначе замок сторожит пустоту.
        self.assertEqual(out["kids_pulse"], "ушла")

    def test_flag_returns_even_if_the_import_explodes(self):
        """Падение импорта не смеет оставить флаг стоять: иначе один сбой демона глушит пульс
        до конца жизни процесса — ровно та же немота, только с другого входа."""
        def boom():
            raise ImportError("демон не импортировался")
        with self.assertRaises(ImportError):
            run_mod._guard_test_logs(boom)
        self.assertIsNone(os.environ.get("TURBOBABY_TEST_LOGS"))   # значение, а не членство

    def test_flag_value_that_was_there_is_returned_as_it_was(self):
        """Возврат КАК БЫЛО, а не удаление: заявленный вызывающим флаг — его решение, и стирать
        чужое заявление мы не вправе."""
        os.environ["TURBOBABY_TEST_LOGS"] = "0"
        inside = run_mod._guard_test_logs(lambda: os.environ.get("TURBOBABY_TEST_LOGS"))
        self.assertEqual((inside, os.environ.get("TURBOBABY_TEST_LOGS")), ("1", "0"))


class TestLiveFormat(unittest.TestCase):
    """Формат фактов снят с прода, а не идеализирован."""

    def test_heartbeat_live_string_parses_to_utc(self):
        ts = ex.parse_iso(HB_LIVE)
        self.assertIsNotNone(ts)
        # Та же строка без зоны обязана читаться как UTC, а не как местное время ПК (UTC+7):
        # ошибка в семь часов здесь стоила бы ложной заметки на каждом прогоне.
        self.assertEqual(ex.parse_iso("2026-08-10T21:42:25.869518"), ts)
        self.assertEqual(ex.parse_iso("2026-08-10T21:42:25.869518Z"), ts)
        self.assertIsNone(ex.parse_iso("мусор"))
        self.assertIsNone(ex.parse_iso(""))

    def test_busy_stamp_reads_the_live_registry_shape(self):
        """Предмет штампа — ВРЕМЯ ПРАВКИ реестра, и содержимое на него не влияет ничем."""
        d = {"467": {"at": "2026-08-10T20:59:34.763818+00:00", "pid": 2624,
                     "proc": "2624-1786299364", "child": 7368},
             "469": {"at": "2026-08-10T21:44:16.763480+00:00", "pid": 2624,
                     "proc": "2624-1786299364", "child": 14452},
             "12": "2026-08-01T00:00:00+00:00"}          # старый вид отметки — голая строка
        p = os.path.join(tempfile.mkdtemp(prefix="expect_pc_reg_"), "task_started.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.utime(p, (NOW - 500.0, NOW - 500.0))
        b = run_mod.busy_facts(p)
        self.assertEqual((b["ok"], b["limit"]), (True, ex.TASK_TIMEOUT_SEC))
        self.assertAlmostEqual(b["since"], NOW - 500.0, delta=0.01)
        # ПРИЗНАК НАСТОЯЩЕЙ ПОЧИНКИ (предсмертный взгляд разведки 18.08): вердикт не меняется,
        # если из реестра выкинуть ВСЕ поля `at` — предметом стал факт «файл тронут», а не поле.
        with open(p, "w", encoding="utf-8") as f:
            json.dump({k: {"pid": 2624} for k in d}, f)
        os.utime(p, (NOW - 500.0, NOW - 500.0))
        self.assertAlmostEqual(run_mod.busy_facts(p)["since"], NOW - 500.0, delta=0.01)

    def test_missing_registry_is_read_but_unreadable_one_is_unknown(self):
        """ТРИ ИСХОДА У РУК. Реестра нет → ПРОЧИТАНО, объявлять нечего. Реестр есть, но не
        читается → `ok=False`, и решение обязано сказать «неизвестно», а не «в порядке».
        Развилка дословно повторяет `pc_orchestrator._task_started_read`."""
        p = os.path.join(tempfile.mkdtemp(prefix="expect_pc_reg2_"), "task_started.json")
        gone = run_mod.busy_facts(p)
        self.assertEqual((gone["ok"], gone["since"], gone["err"]), (True, None, ""))
        self.assertEqual(ex.busy_state(facts(busy=gone), NOW), (ex.BUSY_IDLE, None))
        # «Реестр БЫЛ, но не прочитан» — отдельный исход, и он НЕ становится «объявлять нечего».
        bad = {"ok": False, "since": None, "limit": ex.TASK_TIMEOUT_SEC,
               "err": "PermissionError: [Errno 13] Permission denied"}
        self.assertEqual(ex.busy_state(facts(busy=bad), NOW), (ex.BUSY_UNKNOWN, None))

    def test_queue_rows_come_from_bridge_fields(self):
        rows = [{"id": 469, "status": "in_progress", "lane": "pc", "from": "Filipp-328-dev",
                 "created": "2026-08-10T21:28:30.548Z", "updated": "2026-08-10T21:44:16.763Z",
                 "task_text": "ultrathink ЦЕЛЬ: ..."}]
        q = run_mod.queue_facts(lambda st: {"ok": True,
                                            "items": [r for r in rows if r["status"] == st]})
        self.assertTrue(q["ok"])
        self.assertEqual(q["rows"][0]["since"], ex.parse_iso("2026-08-10T21:44:16.763Z"))

    def test_partial_snapshot_is_no_snapshot(self):
        """Мост ответил не по всем статусам → ok=False целиком: половина снимка хуже пустого."""
        def half(status):
            return {"ok": status != "approved", "items": []}
        self.assertFalse(run_mod.queue_facts(half)["ok"])


# ══════════════════════════════════════════════════════════════════════════════════════════
#  О3 — МОДЕРБОТ: «ЖИВ» И «ДЕЛАЕТ РАБОТУ» РАЗВЕДЕНЫ, И ОБА НАПРАВЛЕНИЯ ПОКРЫТЫ
# ══════════════════════════════════════════════════════════════════════════════════════════
MOD_PID = 11968                          # живой номер из moderation_bot.lock (замер 11.08.2026)
MOD_START = NOW - 400000.0               # процесс запущен задолго до наблюдения (06.08 19:43:59)
MOD_LOCK_SKEW = 1.4                      # ЖИВОЕ расхождение «старт процесса → запись лока», с


def modf(age=3.0, pid=MOD_PID, opened=True, started=MOD_START, skew=MOD_LOCK_SKEW,
         ok=True, err="", now=NOW, channel=2.0):
    """Факт о модерботе ровно того вида, что отдают руки (`moderbot_facts`): время СВОЕГО продукта
    (ключ `meta['heartbeat']`, а не mtime общего файла — правка 18.08.2026), номер из лока, проба
    процесса, возраст запуска и ОТДЕЛЬНО возраст ОБЩЕГО канала.

    `channel` по умолчанию свеж НАМЕРЕННО: в проде общий файл двигается каждые ~5 с (замер 18.08 —
    38 сдвигов за 190 с, пишут трое), и фикстура, в которой он стар, прятала бы главный класс."""
    return {"ok": ok, "own": (now - age) if ok else None, "pid": pid, "opened": opened,
            "started": started, "lock_mtime": None if started is None else started + skew,
            "err": err, "channel": None if channel is None else now - channel, "raw": None}


def silent_for(seconds, since=NOW - 1800.0):
    return {"measured": True, "awake": float(seconds), "since": since, "why": ""}


def mfacts(mod=None, silence=None, now=NOW):
    """Факты с наполненной веткой О3. Остальные ветки — как в базовой фикстуре."""
    f = facts(now=now)
    f["moderbot"] = modf(now=now) if mod is None else mod
    f["mod_silence"] = ({"measured": False, "awake": 0.0, "since": now,
                         "why": "своя запись в IPC — тишина обнулена"}
                        if silence is None else silence)
    return f


class TestO3Moderbot(unittest.TestCase):
    """Предмет О3 — ПРОДУКТ пятисекундного тика, а не существование процесса."""

    def setUp(self):
        self.cfg = ex.config({})

    def test_fresh_own_product_is_work(self):
        st, info = ex.moderbot_state(mfacts(), self.cfg, NOW)
        self.assertEqual(st, ex.MOD_OK)
        self.assertEqual(ex.verdict(mfacts(), self.cfg), [])
        self.assertLess(info["wall"], self.cfg["mod"])

    def test_live_pid_without_product_is_a_violation(self):
        """ГЛАВНЫЙ КЕЙС ЗАКОНА: процесс жив, номер на месте, продукта нет → это НЕ «жив»."""
        f = mfacts(mod=modf(age=1800.0), silence=silent_for(1500.0))
        st, info = ex.moderbot_state(f, self.cfg, NOW)
        self.assertEqual(st, ex.MOD_IDLE)
        v = [x for x in ex.verdict(f, self.cfg) if x["kind"] == "o3_pc_moderbot"]
        self.assertEqual(len(v), 1)
        note = ex.render(v[0])
        self.assertIn("не делает свою работу", note)
        self.assertIn("PID %d жив" % MOD_PID, note)
        self.assertIn("живой PID работающим сервисом не является", note)
        self.assertTrue(info["who"])

    def test_dead_kid_with_a_fresh_record_is_death_and_not_unknown(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЖИВОГО СЛУЧАЯ 18.08.2026, чистая половина. Модербота унесло
        перезагрузкой (процесса с номером из лока в системе НЕТ), а запись возрастом 400 с ещё
        свежа — и по общему каналу, и по его собственному ключу. ПРЕЖДЕ здесь стояло «неизвестно»
        (замер: вердикт 20:27 местного), то есть заведомо мёртвый ребёнок объявлялся непроверяемым.
        Теперь приговор выносит проба процесса: «работы нет», и ни «неизвестно», ни «делает работу»
        отсюда не выходит ни одной ветвью."""
        f = mfacts(mod=modf(age=400.0, opened=False, channel=1.0))
        st, info = ex.moderbot_state(f, self.cfg, NOW)
        self.assertEqual(st, ex.MOD_IDLE)
        self.assertNotEqual(st, ex.MOD_UNKNOWN)
        self.assertNotEqual(st, ex.MOD_OK)
        self.assertTrue(info["dead"])
        self.assertIn("из лока в системе нет", info["why"])
        self.assertLess(info["wall"], self.cfg["mod"], "продукт МОЛОЖЕ порога — как 18.08")
        self.assertLess(info["channel_age"], 5.0, "общий канал свеж — как 18.08")
        # И это НАРУШЕНИЕ, а не молчание: владелец узнаёт о смерти сразу, а не через 15 минут.
        self.assertEqual([x["kind"] for x in ex.verdict(f, self.cfg)], ["o3_pc_moderbot"])

    def test_the_common_channel_is_information_and_never_a_green(self):
        """ОБЩАЯ ЗАПИСЬ В КАНАЛ БОЛЬШЕ НЕ ДОКАЗЫВАЕТ ЖИВОСТЬ НИКОМУ. Свежий общий файл при
        молчащем СВОЁМ ключе — это «работы нет», а не «работает»: писателей у файла трое."""
        f = mfacts(mod=modf(age=1800.0, channel=1.0), silence=silent_for(1500.0))
        st, info = ex.moderbot_state(f, self.cfg, NOW)
        self.assertEqual(st, ex.MOD_IDLE)
        self.assertLess(info["channel_age"], 5.0)
        note = ex.render(ex.verdict(f, self.cfg)[0])
        self.assertIn("делал кто-то другой", note)
        self.assertIn("СВЕДЕНИЕ", note)
        # Обратная сторона: убери общий канал вовсе — вердикт не изменится ни на букву.
        blind = mfacts(mod=modf(age=1800.0, channel=None), silence=silent_for(1500.0))
        self.assertEqual(ex.moderbot_state(blind, self.cfg, NOW)[0], ex.MOD_IDLE)

    def test_silence_becomes_a_violation_even_while_the_common_file_keeps_moving(self):
        """Вторая половина того же замка: молчание не вечно. Чужая запись счётчик не обнуляет,
        поэтому через два наблюдения выходит честное нарушение — с названным состоянием процесса."""
        f = mfacts(mod=modf(age=2.0, opened=False), silence=silent_for(1200.0))
        self.assertEqual(ex.moderbot_state(f, self.cfg, NOW)[0], ex.MOD_IDLE)
        note = ex.render(ex.verdict(f, self.cfg)[0])
        self.assertIn("из лока в системе нет", note)
        self.assertIn("делал кто-то другой", note)

    def test_reused_pid_is_a_death_too(self):
        """Номер переиспользован Windows'ом: процесс есть, но запущен не тогда, когда написан лок.
        Значит прежний владелец номера НЕ РАБОТАЕТ — и это тот же приговор, что «номера нет»."""
        f = mfacts(mod=modf(age=2.0, started=NOW - 30.0, skew=-400000.0))
        self.assertIs(ex.moderbot_writer(f), False)
        self.assertEqual(ex.moderbot_state(f, self.cfg, NOW)[0], ex.MOD_IDLE)
        # А ЖИВАЯ пара «старт → лок» (расхождение 1.4с) автором быть обязана.
        self.assertIs(ex.moderbot_writer(mfacts()), True)

    def test_sleep_is_not_silence(self):
        """Возраст по стенным часам больше порога, но бодрствования накопилось мало: машина спала.
        Контрфакт корпуса — стенные часы дали бы здесь 2 ложные заметки (сны 11611с и 32381с)."""
        f = mfacts(mod=modf(age=32381.0), silence=silent_for(300.0))
        st, info = ex.moderbot_state(f, self.cfg, NOW)
        self.assertEqual(st, ex.MOD_OK)
        self.assertGreater(info["slept"], 30000.0)
        self.assertEqual(ex.verdict(f, self.cfg), [])

    def test_unknown_never_says_work(self):
        """Каждая дырка в фактах — отдельным кейсом. Ни одна не ведёт к «делает работу»."""
        holes = [
            ("факта о модерботе нет вовсе", dict(mfacts(), moderbot=None)),
            ("stat не удался", mfacts(mod=modf(ok=False, err="FileNotFoundError: …"))),
            ("время записи не разобрано", mfacts(mod=dict(modf(), own="не время"))),
            ("лока нет — автор не подтверждён", mfacts(mod=modf(pid=None))),
            ("проба процесса не состоялась", mfacts(mod=modf(opened=None))),
            ("возраст запуска не добыт (отказ доступа)", mfacts(mod=modf(started=None))),
            ("продукт стар, а чем набран возраст — не измерено",
             mfacts(mod=modf(age=4000.0),
                    silence={"measured": False, "awake": None, "since": NOW,
                             "why": "прошлого наблюдения нет"})),
            ("часов бодрствования нет",
             mfacts(mod=modf(age=4000.0),
                    silence={"measured": False, "awake": None, "since": NOW,
                             "why": "часов бодрствования на этой машине нет"})),
        ]
        for name, f in holes:
            st, info = ex.moderbot_state(f, self.cfg, NOW)
            self.assertEqual(st, ex.MOD_UNKNOWN, "«%s» обязано быть НЕИЗВЕСТНО" % name)
            self.assertNotEqual(st, ex.MOD_OK, "«%s» не смеет читаться как работа" % name)
            self.assertTrue(info.get("why"), "у незнания обязана быть названа причина: %s" % name)
            self.assertEqual([x for x in ex.verdict(f, self.cfg)
                              if x["kind"] == "o3_pc_moderbot"], [], name)

    def test_the_same_branch_speaks_when_the_fact_is_whole(self):
        """Вторая половина замка: молчание обязано кончаться там, где факт появился."""
        self.assertEqual(ex.moderbot_state(mfacts(), self.cfg, NOW)[0], ex.MOD_OK)
        self.assertEqual(ex.moderbot_state(mfacts(mod=modf(age=1800.0),
                                                  silence=silent_for(1500.0)),
                                           self.cfg, NOW)[0], ex.MOD_IDLE)

    def test_threshold_zero_kills_the_branch(self):
        """Объявленный откат: порог 0 — ветка мертва ДО чтения фактов."""
        cfg = ex.config({"EXPECT_PC_MOD_MIN": "0"})
        self.assertEqual(cfg["mod"], 0.0)
        f = mfacts(mod=modf(age=99999.0), silence=silent_for(99999.0))
        st, info = ex.moderbot_state(f, cfg, NOW)
        self.assertEqual(st, ex.MOD_UNKNOWN)
        self.assertIn("выключена порогом", info["why"])
        self.assertEqual(ex.verdict(f, cfg), [])

    def test_threshold_is_fifteen_minutes_and_lives_above_one_observer_period(self):
        """Порог снят с СОБСТВЕННЫХ пауз модербота (max законной 5.02с) и намеренно больше одного
        периода наблюдателя (600с): заметка требует ДВУХ плохих наблюдений — замок против окна
        рестарта, которого нет ни в одном корпусе."""
        self.assertEqual(ex.config({})["mod"], 900.0)
        self.assertGreater(ex.config({})["mod"], run_mod.STEP_CAP_SEC / 2)
        self.assertEqual(ex.moderbot_state(mfacts(mod=modf(age=899.0),
                                                  silence=silent_for(899.0)),
                                           self.cfg, NOW)[0], ex.MOD_OK)
        self.assertEqual(ex.moderbot_state(mfacts(mod=modf(age=901.0),
                                                  silence=silent_for(901.0)),
                                           self.cfg, NOW)[0], ex.MOD_IDLE)

    def test_one_episode_one_note_even_when_the_file_keeps_moving(self):
        """Ключ эпизода — НАЧАЛО тишины, а не последняя запись: иначе в самом дорогом случае
        (бот мёртв, файл двигает чужой) владелец получал бы заметку каждые десять минут."""
        a = mfacts(mod=modf(age=2.0, opened=False), silence=silent_for(1200.0, since=NOW - 1200))
        b = mfacts(mod=modf(age=1.0, opened=False), silence=silent_for(1800.0, since=NOW - 1200))
        self.assertEqual(ex.verdict(a, self.cfg)[0]["key"], ex.verdict(b, self.cfg)[0]["key"])

    def test_episode_closes_only_on_proven_work(self):
        key = ex.verdict(mfacts(mod=modf(age=1800.0), silence=silent_for(1500.0)),
                         self.cfg)[0]["key"]
        blind = mfacts(mod=modf(ok=False, err="PermissionError"))
        self.assertEqual(ex.closures(blind, self.cfg, [key]), [],
                         "перестать видеть модербота не значит дождаться выздоровления")
        self.assertEqual(ex.closures(mfacts(mod=modf(age=2.0, opened=False)), self.cfg, [key]), [],
                         "чужая свежая запись эпизод не закрывает")
        self.assertEqual(ex.closures(mfacts(), self.cfg, [key]), [key])
        self.assertIn("снова делает свою работу", ex.render_close(key))


class TestO3Hands(unittest.TestCase):
    """Руки О3: счётчик тишины и проба процесса. Живой формат, а не идеализированный."""

    def test_own_write_resets_and_foreign_write_does_not(self):
        st = {}
        m1 = modf(age=2.0, now=1000.0)
        self.assertFalse(run_mod.update_mod_silence(st, m1, 100.0, 1000.0)["measured"])
        m2 = modf(age=2.0, now=1600.0)                       # mtime сдвинулся, автор подтверждён
        r = run_mod.update_mod_silence(st, m2, 700.0, 1600.0)
        self.assertFalse(r["measured"])
        self.assertIn("своя запись", r["why"])
        m3 = modf(age=2.0, now=2200.0, opened=False)         # mtime сдвинулся, но автор ОПРОВЕРГНУТ
        r = run_mod.update_mod_silence(st, m3, 1300.0, 2200.0)
        self.assertTrue(r["measured"])
        self.assertEqual(r["awake"], 600.0, "чужая запись счётчик обнулять не смеет")
        m4 = modf(age=2.0, now=2800.0, opened=False)
        self.assertEqual(run_mod.update_mod_silence(st, m4, 1900.0, 2800.0)["awake"], 1200.0)

    def test_silence_needs_a_waking_clock_and_a_previous_look(self):
        st = {}
        r = run_mod.update_mod_silence(st, modf(), None, NOW)
        self.assertFalse(r["measured"])
        self.assertIn("часов бодрствования", r["why"])
        st2 = {"mod": {"own": 1.0, "awake": 900.0, "silent": 300.0, "since": NOW - 900}}
        r = run_mod.update_mod_silence(st2, modf(ok=False), 1000.0, NOW)
        self.assertFalse(r["measured"], "продукт не прочитан — копить нечего и обнулять нечего")
        r = run_mod.update_mod_silence({"mod": {"own": 1.0, "awake": 5000.0, "silent": 300.0}},
                                       modf(), 10.0, NOW)
        self.assertFalse(r["measured"], "часы пошли назад — машина перезагрузилась")

    def test_step_is_capped_by_one_observation(self):
        st = {"mod": {"own": 7.0, "awake": 0.0, "silent": 0.0, "since": NOW - 99999}}
        r = run_mod.update_mod_silence(st, dict(modf(), own=7.0), 99999.0, NOW)
        self.assertEqual(r["awake"], run_mod.STEP_CAP_SEC,
                         "наблюдатель мог не работать сутки — выдумывать за них молчание нельзя")

    def test_process_probe_asks_and_does_not_touch(self):
        """Проба на СЕБЕ: процесс есть и возраст запуска правдоподобен. И ни одного права тронуть —
        `os.kill(pid, 0)` на Windows зовёт TerminateProcess, поэтому его здесь нет вовсе."""
        opened, started = run_mod.process_probe(os.getpid())
        self.assertTrue(opened)
        self.assertIsNotNone(started)
        self.assertLess(abs(started - __import__("time").time()), 86400.0)
        # Номер, которого в системе быть не может (PID Windows кратны 4): «жив» отсюда не выйдет.
        self.assertNotEqual(run_mod.process_probe(999983)[0], True)
        self.assertEqual(run_mod.process_probe("не число"), (None, None))
        # Ищем ВЫЗОВ, а не подстроку: имя запрещённой операции в докстринге — это объявленный
        # запрет, а не его нарушение (то же правило исполняющей позиции, что в гарде).
        with open(RUN_SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        self.assertEqual([n.lineno for n in ast.walk(tree)
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                          and n.func.attr == "kill"], [])

    def test_moderbot_facts_read_the_live_files(self):
        """ЖИВОЙ ФОРМАТ, А НЕ ИДЕАЛИЗИРОВАННЫЙ: настоящая база SQLite с настоящей таблицей `meta`
        и значением ровно того вида, что пишет `moderation_ipc._now_iso()`. Прежняя фикстура была
        файлом из шестнадцати байт заголовка — на ней ключ прочитать нельзя было бы никогда."""
        d = tempfile.mkdtemp(prefix="expect_pc_mod_")
        ipc, lock = os.path.join(d, "moderation_ipc.db"), os.path.join(d, "moderation_bot.lock")
        con = sqlite3.connect(ipc)
        con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO meta (k, v) VALUES ('heartbeat', ?)",
                    (datetime.datetime.fromtimestamp(NOW - 3.0, datetime.timezone.utc)
                     .isoformat(),))
        con.commit()
        con.close()
        with open(lock, "w", encoding="utf-8") as f:
            f.write("%d\n" % os.getpid())            # живой вид лока: номер и перевод строки
        m = run_mod.moderbot_facts(ipc, lock)
        self.assertTrue(m["ok"], m["err"])
        self.assertEqual(m["pid"], os.getpid())
        self.assertTrue(m["opened"])
        self.assertAlmostEqual(m["own"], NOW - 3.0, places=3)
        self.assertIs(ex.moderbot_writer({"moderbot": m}), True)
        # ОБЩИЙ КАНАЛ ПРОЧИТАН ОТДЕЛЬНЫМ ПОЛЕМ И ОСТАЛСЯ СВЕДЕНИЕМ: он есть, но вердикта не даёт.
        self.assertIsNotNone(m["channel"])
        self.assertNotEqual(m["channel"], m["own"])
        # ЧТЕНИЕ НИЧЕГО НЕ ТРОНУЛО — доказано контрольной суммой до и после, снятой самим глазом.
        self.assertIs(m["sha_same"], True)
        self.assertFalse(run_mod.moderbot_facts(ipc + ".нет", lock)["ok"])
        # Лока нет → продукт прочитан, но процесс НЕ подтверждён: «неизвестно», не «работает».
        no_lock = run_mod.moderbot_facts(ipc, lock + ".нет")
        self.assertTrue(no_lock["ok"])
        self.assertIsNone(ex.moderbot_writer({"moderbot": no_lock}))
        # База не открывается ни РУКАМИ, ни РЕШЕНИЕМ: инструмента для этого нет ни там, ни там —
        # `sqlite3` живёт в ОДНОМ файле слоя (глаз), и разрешение получено сужением замка.
        with open(RUN_SRC, encoding="utf-8") as f:
            names = [a.name for n in ast.walk(ast.parse(f.read()))
                     if isinstance(n, ast.Import) for a in n.names]
        self.assertNotIn("sqlite3", names)

    def test_the_key_of_the_own_product_is_the_one_the_writer_writes(self):
        """FAIL-CLOSED ПРОТИВ ПЕРЕИМЕНОВАНИЯ: имя ключа держится у наблюдателя КОПИЕЙ, и если
        писатель когда-нибудь назовёт его иначе, обязано покраснеть здесь — а не молча
        превратиться в вечное «неизвестно» о живом ребёнке. Читаем ИСХОДНИК писателя, а не импорт:
        наблюдатель не поднимает наблюдаемого ради строки."""
        with open(os.path.join(REPO, "moderation_ipc.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "heartbeat"]
        self.assertEqual(len(fn), 1, "писатель heartbeat не найден")
        sql = " ".join(n.value for n in ast.walk(fn[0])
                       if isinstance(n, ast.Constant) and isinstance(n.value, str))
        self.assertIn("'%s'" % eye.MOD_HB_KEY, sql,
                      "имя ключа у наблюдателя разошлось с писателем")
        # И пишет его РОВНО ОДНА строка кода — на этом стои́т всё авторство: ключ подписан.
        self.assertEqual(sql.count("INSERT INTO meta"), 1)

    def test_kid_facts_give_each_child_its_own_source(self):
        """У каждого ребёнка своя пара «продукт + лок», и ни один из них не общий."""
        for name in ex.KIDS:
            sign = ex.KID_SIGNS[name]
            if name == ex.KIDS_JUDGED:
                continue
            product, lock = run_mod.KID_FILES[name]
            self.assertTrue(product.endswith(sign["src"].replace("/", os.sep)),
                            "источник %s разошёлся с таблицей признаков" % name)
            self.assertTrue(lock.endswith("%s.lock" % ("userbot" if name == "userbot" else name)))
        products = {p for p, _ in run_mod.KID_FILES.values()}
        self.assertEqual(len(products), len(run_mod.KID_FILES), "продукт у детей общий")
        self.assertNotIn(run_mod.MOD_IPC_FILE, products, "общий канал попал в личные признаки")
        # Продукта нет, а лок есть → проба всё равно делается: смерть доказывается ею, а не файлом.
        d = tempfile.mkdtemp(prefix="expect_pc_own_")
        lock = os.path.join(d, "userbot.lock")
        with open(lock, "w", encoding="utf-8") as f:
            f.write("999999")
        got = run_mod.own_facts(os.path.join(d, "нет.session"), lock, "userbot")
        self.assertFalse(got["ok"])
        self.assertEqual(got["pid"], 999999)
        self.assertIs(ex.kid_writer(got), False)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  О4 — СЛЕД ЖИЗНИ НАРУЖУ. ТЕСТ В ОБЕ СТОРОНЫ: ЖИВ БЕЗ ЗАДАЧ → МОЛЧАНИЕ; НЕ ДАЁТ ОБОРОТА → ЗВУЧИТ
# ══════════════════════════════════════════════════════════════════════════════════════════
LEDGER_LIVE = ('{"ts": "2026-08-13T13:26:15.579101+00:00", "line": "NOTE 2026-08-13 13:26 UTC: '
               'Orchestrator: ревизор: 1 окон, чисто"}')      # ДОСЛОВНО из cowork_log.ledger


def lfacts(trace_age=7 * 3600.0, hb_age=354.0, trace_ok=True, attempt=None, busy=None,
           silence=None, now=NOW):
    """Факты с наполненной веткой О4: возраст ПОСЛЕДНЕГО следа наружу + всё, чем доказывают оборот."""
    f = facts(hb=hb_at(hb_age, now), busy=busy, silence=silence, now=now)
    f["trace"] = {"ok": trace_ok, "ts": (now - trace_age) if trace_ok else None,
                  "line": "NOTE Orchestrator: ревизор: 1 окон, чисто", "attempt": attempt,
                  "err": "" if trace_ok else "FileNotFoundError: реестра следов нет"}
    return f


class TestO4LifeTrace(unittest.TestCase):
    """Предмет О4 — ПРОДУКТ ЖИЗНИ, а не наличие работы. Тишина по бездействию обязана отличаться
    от тишины по отказу, и различает их ровно пульс."""

    def setUp(self):
        self.cfg = ex.config({})

    def test_threshold_is_the_measured_six_hours(self):
        self.assertEqual((self.cfg["life"], self.cfg["life_retry"]), (21600.0, 1800.0))
        # Замок выбора: ДВА полных окна пульса обязаны уместиться в измеренные 16 ч серверного О4.
        self.assertLessEqual(2 * self.cfg["life"], 16 * 3600.0)

    def test_alive_without_tasks_leaves_a_trace(self):
        """ПЕРВАЯ СТОРОНА: ПК жив, задач нет 7 часов → пульс уходит, и серверное О4 молчит."""
        f = lfacts()
        self.assertEqual(ex.life_state(f, self.cfg, NOW)[0], ex.LIFE_PROVEN)
        due, info = ex.pulse_due(f, self.cfg, NOW)
        self.assertTrue(due)
        line = ex.render_pulse(info)
        self.assertTrue(line.startswith("NOTE "), "тип не опознан → писатель сделает из пульса DONE")
        self.assertIn("контур жив", line)
        self.assertIn("след ЖИЗНИ, а не отчёт о работе", line)
        self.assertLessEqual(len(line), 600, "длиннее LINE_MAX — писатель вынесет тело файлом")
        # И при этом О4 не завела ни одного НОВОГО нарушения: вердикт остался про О1–О3.
        self.assertEqual(ex.verdict(f, self.cfg), [])
        # У О4 нет СВОЕГО вида нарушения вовсе: она ПРОИЗВОДИТ пульс, а не судит. Проверяем именно
        # это, а не длину списка видов: приколоченный кортеж краснел бы на каждом новом ожидании,
        # обвиняя его в поломке О4 (так и вышло при заведении О5 — видов стало шесть).
        self.assertEqual([k for k in ex.KINDS if k.startswith("o4")], [])
        self.assertIn("o3_pc_moderbot", ex.KINDS)

    def test_dead_contour_stays_silent_so_the_server_can_speak(self):
        """ВТОРАЯ СТОРОНА: оборота нет (демон встал) → пульса НЕТ, и серверное О4 звучит честно.
        Это главный кейс: пульс «на всякий случай» отнял бы у него зубы."""
        f = lfacts(hb_age=99999.0, silence={"measured": True, "awake": 99999.0, "why": ""})
        self.assertEqual(ex.life_state(f, self.cfg, NOW)[0], ex.LIFE_UNKNOWN)
        due, info = ex.pulse_due(f, self.cfg, NOW)
        self.assertFalse(due)
        self.assertIn("наружу говорить нечего", info["why"])
        # А О2 при этом говорит по существу — молчание О4 не отнимает заметку у владельца.
        self.assertEqual([v["kind"] for v in ex.verdict(f, self.cfg)], ["o2_pc_turn"])

    def test_declared_pass_is_work_for_o2_but_not_a_proof_for_o4(self):
        """Ветка «оправдано объявленным заходом» законна для О2 и ЗАПРЕЩЕНА для О4: штамп
        доказывает, что демон объявил заход, а не что он замкнул виток."""
        busy = stamp(600.0)
        f = lfacts(hb_age=1800.0, busy=busy,
                   silence={"measured": True, "awake": 1800.0, "why": ""})
        self.assertEqual(ex.turn_state(f, self.cfg, NOW)[0], ex.TURN_OK)      # О2 — работа
        self.assertEqual(ex.life_state(f, self.cfg, NOW)[0], ex.LIFE_UNKNOWN)  # О4 — не доказано
        self.assertFalse(ex.pulse_due(f, self.cfg, NOW)[0])

    def test_every_hole_in_the_facts_is_unknown_and_never_a_pulse(self):
        """ЗАМОК ПРОТИВ ЛОЖНОГО ЗЕЛЁНОГО: ни одна дырка не даёт пульса, и у каждой своя причина."""
        holes = [
            ("heartbeat не прочитан", facts(hb_ok=True, hb="")),
            ("время в heartbeat не разобрано", facts(hb="мусор")),
            ("тишина не измерена", facts(hb=hb_at(99999.0),
                                         silence={"measured": False, "why": "часов нет"})),
        ]
        for name, base in holes:
            base["trace"] = {"ok": True, "ts": NOW - 7 * 3600.0, "attempt": None, "err": ""}
            if not base["heartbeat"]["raw"]:
                base["heartbeat"]["ok"] = False
            with self.subTest(name):
                self.assertEqual(ex.life_state(base, self.cfg, NOW)[0], ex.LIFE_UNKNOWN)
                self.assertFalse(ex.pulse_due(base, self.cfg, NOW)[0])
        # Реестр следов не прочитан — тоже молчание: не зная возраста следа, пульс пришлось бы
        # слать каждый тик. Причина названа, а не схлопнута в «следов нет».
        f = lfacts(trace_ok=False)
        due, info = ex.pulse_due(f, self.cfg, NOW)
        self.assertFalse(due)
        self.assertIn("реестр следов не прочитан", info["why"])

    def test_fresh_trace_needs_no_pulse(self):
        """Работа уже оставила след — дублировать его пульсом значит делать из журнала кардиограмму."""
        due, info = ex.pulse_due(lfacts(trace_age=600.0), self.cfg, NOW)
        self.assertFalse(due)
        self.assertIn("след свежий", info["why"])

    def test_retry_floor_holds_the_channel(self):
        """Пол повтора: попытка 5 минут назад молчит, 40 минут назад — говорит."""
        self.assertFalse(ex.pulse_due(lfacts(attempt=NOW - 300.0), self.cfg, NOW)[0])
        self.assertTrue(ex.pulse_due(lfacts(attempt=NOW - 2400.0), self.cfg, NOW)[0])

    def test_sleep_of_the_machine_is_named_in_the_pulse(self):
        """Машина спала: оборот стар по стенным часам, но свеж по бодрствованию. Пульс уходит и
        НАЗЫВАЕТ сон — иначе «оборот 9 ч назад» читалось бы как поломка."""
        f = lfacts(hb_age=32381.0, silence={"measured": True, "awake": 300.0, "why": ""})
        due, info = ex.pulse_due(f, self.cfg, NOW)
        self.assertTrue(due)
        self.assertIn("сон машины", ex.render_pulse(info))

    def test_zero_kills_the_branch_before_any_fact_is_read(self):
        cfg0 = ex.config({"EXPECT_PC_LIFE_MIN": "0"})
        self.assertEqual(ex.life_state(lfacts(), cfg0, NOW)[0], ex.LIFE_UNKNOWN)
        self.assertFalse(ex.pulse_due(lfacts(), cfg0, NOW)[0])
        # А выключенное О2 забирает у О4 ДОКАЗАТЕЛЬСТВО — и это тоже молчание, а не «жив».
        cfg_turn0 = ex.config({"EXPECT_PC_TURN_MIN": "0"})
        self.assertEqual(ex.life_state(lfacts(), cfg_turn0, NOW)[0], ex.LIFE_UNKNOWN)


class TestO4Hands(unittest.TestCase):
    """Руки О4: живой формат реестра, отметка попытки, сухой прогон канала не трогает."""

    def setUp(self):
        self.cfg = ex.config({})
        self.dir = tempfile.mkdtemp(prefix="expect_pc_led_")

    def _ledger(self, *raw):
        p = os.path.join(self.dir, "cowork_log.ledger")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(raw) + "\n")
        return p

    def test_reads_the_live_ledger_line(self):
        """Формат снят с прода дословно: последняя разобранная отметка — и есть последний след."""
        t = run_mod.trace_facts(self._ledger('{"ts": "2026-08-01T00:00:00+00:00", "line": "старая"}',
                                             LEDGER_LIVE))
        self.assertTrue(t["ok"])
        self.assertEqual(t["ts"], ex.parse_iso("2026-08-13T13:26:15.579101+00:00"))

    def test_zero_by_nonparse_is_never_silent(self):
        """НУЛЬ ПО НЕРАЗБОРУ: файл есть, но ни одной отметки времени — это ok=False с причиной,
        а не «следов нет» (последнее означало бы «пора пульсовать» и врало бы наружу каждый тик)."""
        t = run_mod.trace_facts(self._ledger("не json", '{"line": "без ts"}'))
        self.assertFalse(t["ok"])
        self.assertIn("ни одной разобранной отметки", t["err"])
        self.assertFalse(run_mod.trace_facts(os.path.join(self.dir, "нет.ledger"))["ok"])

    def test_attempt_is_marked_even_when_the_write_failed(self):
        """Отличие от заметки: провальная попытка ПОМЕЧАЕТСЯ. Иначе лежащий мост получал бы пульс
        каждые 10 минут, а в журнал сыпался бы мусор."""
        st, sent = {}, []
        got, _ = run_mod.maybe_pulse(st, lfacts(), self.cfg, NOW,
                                     lambda line: sent.append(line) or False)
        self.assertEqual((got, len(sent)), ("не ушёл", 1))
        self.assertEqual(st["life"]["attempt"], NOW)
        self.assertFalse(st["life"]["ok"])
        # И следующий прогон в пределах пола повтора канал уже не трогает.
        got2, why = run_mod.maybe_pulse(st, lfacts(attempt=st["life"]["attempt"], now=NOW + 300.0),
                                        self.cfg, NOW + 300.0, lambda line: sent.append(line))
        self.assertIsNone(got2)
        self.assertEqual(len(sent), 1)
        self.assertIn("пол повтора", why)

    def test_dry_run_never_touches_the_channel(self):
        """Сухой прогон: решение считается, канал молчит — то же правило, что у заметок."""
        sent = []
        out = run_mod.run(dry=True, now=NOW, getter=lambda st: {"ok": True, "items": []},
                          notifier=lambda t: sent.append(t),
                          pulser=lambda line: sent.append(line))
        self.assertEqual(sent, [])
        self.assertIn("life", out)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  О5 — КЛИЕНТАМ НЕ УХОДИТ НИЧЕГО. Судится РЕЗУЛЬТАТ (число отправленных), а не флаг.
#  Здесь же ОТРИЦАТЕЛЬНЫЙ КОНТРОЛЬ: прибор, не умеющий сказать «УШЛО», негоден целиком.
# ══════════════════════════════════════════════════════════════════════════════════════════
import sqlite3                                                        # noqa: E402
import client_silence_pc as eye                                       # noqa: E402

EYE_SRC = os.path.join(REPO, "client_silence_pc.py")
# Глаголы, которыми в SQL МЕНЯЮТ мир. Ищутся по границе слова, а не подстрокой: в живом запросе
# прибора стои́т колонка `updated_ts`, и наивный `"UPDATE" in sql` объявил бы её записью.
_SQL_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "REPLACE", "ALTER",
                    "ATTACH", "VACUUM", "COMMIT", "PRAGMA")
# Поля, которых у прибора не должно быть НИ ОДНОГО: тело сообщения и опознание клиента.
_BODY_FIELDS = ("draft", "final_text", "incoming", "client_id", "client_ref", "transcript",
                "text", "directive", "client_name")


def cf(sent=0, ok=True, err="", pairs_sent=0, pairs_ok=True, pairs_err="", pairs_last=None,
       last=None, armed=0, attempted=0, total=470, now=NOW, blind=None):
    """Факт О5 в том виде, в каком его кладут руки (`client_silence_pc.client_facts`). Форма
    сверяется с ЖИВЫМ читателем отдельным тестом, а не обещанием (`test_fixture_is_the_live_shape`)."""
    f = {"now": now,
         "client": {"ok": ok, "err": err, "db": "тест.db", "sent": sent, "attempted": attempted,
                    "armed": armed, "total": total, "statuses": {"posted": 434}, "last_sent": last,
                    "pairs": {"ok": pairs_ok, "err": pairs_err, "path": "тест.jsonl",
                              "sent": pairs_sent, "last": pairs_last, "lines": 7}}}
    if blind is not None:
        f["client_unknown"] = blind
    return f


def blind_for(seconds, measured=True, since=NOW - 9999.0, why="источник истины не прочитан"):
    """Накопленное НЕЗНАНИЕ в том виде, в каком его копят руки (`update_client_unknown`)."""
    return {"measured": measured, "awake": seconds, "since": since, "why": why}


class TestO5Config(unittest.TestCase):
    def test_default_is_the_named_hour(self):
        """Порог НАЗНАЧЕН заданием (60 мин), а не измерен, — и назван так прямо в шапке модуля."""
        self.assertEqual(ex.config({})["client"], 3600.0)
        self.assertEqual(ex.config({"EXPECT_PC_CLIENT_MIN": "10"})["client"], 600.0)
        self.assertEqual(ex.config({"EXPECT_PC_CLIENT_MIN": "мусор"})["client"], 3600.0)

    def test_zero_kills_both_outcomes_of_the_branch(self):
        """Откат объявленный: ноль убивает ветку ЦЕЛИКОМ — и «ушло», и «ослеп»."""
        cfg0 = ex.config({"EXPECT_PC_CLIENT_MIN": "0"})
        self.assertEqual(ex._o5(cf(sent=5), cfg0, NOW), [])
        self.assertEqual(ex._o5(cf(ok=False, blind=blind_for(99999.0)), cfg0, NOW), [])
        self.assertEqual(ex.client_state(cf(sent=5), cfg0, NOW)[0], ex.CLIENT_UNKNOWN)
        # И с дефолтом та же фактура вердикт ДАЁТ — иначе тест ноля ничего не доказывает.
        self.assertEqual(len(ex._o5(cf(sent=5), ex.config({}), NOW)), 1)


class TestO5Decision(unittest.TestCase):
    """ТРИ ИСХОДА, и каждая дырка покрыта В ОБЕ СТОРОНЫ: рядом с «молчит» стои́т «говорит»."""

    def setUp(self):
        self.cfg = ex.config({})

    def test_read_zero_is_quiet(self):
        st, info = ex.client_state(cf(sent=0), self.cfg, NOW)
        self.assertEqual((st, info["sent"], ex.client_spoken(info)), (ex.CLIENT_SILENT, 0, 0))
        self.assertEqual(ex._o5(cf(sent=0), self.cfg, NOW), [])

    def test_read_nonzero_is_a_refusal_with_a_number(self):
        st, info = ex.client_state(cf(sent=3, last="2026-08-17T19:00:00Z"), self.cfg, NOW)
        self.assertEqual(st, ex.CLIENT_SPOKE)
        self.assertEqual(ex.client_spoken(info), 3)
        v = ex._o5(cf(sent=3, last="2026-08-17T19:00:00Z"), self.cfg, NOW)
        self.assertEqual((len(v), v[0]["kind"], v[0]["spoken"]), (1, "o5_pc_client_sent", 3))
        self.assertEqual(v[0]["last"], "2026-08-17T19:00:00Z")

    def test_unreadable_source_is_unknown_and_never_quiet(self):
        """САМАЯ ДОРОГАЯ ошибка ветки: у ожидания, чей результат — ноль, слепота выглядит как
        благополучие. «Не смог прочитать» обязано быть НЕИЗВЕСТНО, и ни одна дорога отсюда к
        «тихо» не ведёт."""
        st, info = ex.client_state(cf(ok=False, err="база занята"), self.cfg, NOW)
        self.assertEqual(st, ex.CLIENT_UNKNOWN)
        self.assertIn("база занята", info["why"])
        self.assertIsNone(info["sent"])
        for hole in (cf(ok=False), {"now": NOW}, {"now": NOW, "client": "не словарь"}):
            self.assertEqual(ex.client_state(hole, self.cfg, NOW)[0], ex.CLIENT_UNKNOWN)
        # И вторая половина замка: при ЦЕЛОМ факте та же ветка говорит по существу.
        self.assertEqual(ex.client_state(cf(sent=0), self.cfg, NOW)[0], ex.CLIENT_SILENT)

    def test_unparsed_counter_is_unknown_not_zero(self):
        st, info = ex.client_state(cf(sent="не число"), self.cfg, NOW)
        self.assertEqual(st, ex.CLIENT_UNKNOWN)
        self.assertIn("не разобран", info["why"])

    def test_second_witness_can_only_get_louder(self):
        """ОДНОСТОРОННЯЯ ГРОМКОСТЬ. Reply-режим в очередь не пишет вовсе, поэтому реестр вправе
        ДОБАВИТЬ нарушение даже при непрочитанной очереди — и не вправе ничего снять."""
        st, info = ex.client_state(cf(ok=False, pairs_sent=1), self.cfg, NOW)
        self.assertEqual(st, ex.CLIENT_SPOKE)              # база молчит, реестр говорит → ОТКАЗ
        self.assertIsNone(info["sent"])
        self.assertEqual(info["second"], 1)
        # Очередь показала ноль, реестр — отправку: это тоже ОТКАЗ (тот самый живой сценарий).
        self.assertEqual(ex.client_state(cf(sent=0, pairs_sent=1), self.cfg, NOW)[0], ex.CLIENT_SPOKE)
        # А НЕПРОЧИТАННЫЙ реестр вердикт очереди НЕ отменяет и в «неизвестно» не превращает —
        # но и молчать о себе не смеет: причина названа в фактах.
        st2, info2 = ex.client_state(cf(sent=0, pairs_ok=False, pairs_err="реестр не прочитан"),
                                     self.cfg, NOW)
        self.assertEqual(st2, ex.CLIENT_SILENT)
        self.assertIsNone(info2["second"])
        self.assertIn("не прочитан", info2["pairs_err"])

    def test_spoken_is_a_floor_not_a_sum(self):
        """Свидетели НЕ независимы: bot-режим пишет и в очередь, и в реестр. Сумма удвоила бы одну
        отправку, поэтому наружу идёт МАКСИМУМ — честный пол «не меньше этого»."""
        self.assertEqual(ex.client_spoken({"sent": 2, "second": 2}), 2)
        self.assertEqual(ex.client_spoken({"sent": 0, "second": 1}), 1)
        self.assertEqual(ex.client_spoken({"sent": None, "second": 4}), 4)
        self.assertIsNone(ex.client_spoken({"sent": None, "second": None}))

    def test_the_flag_is_not_the_subject(self):
        """СПОСОБ: судится ЧИСЛО. В фактах О5 нет ни флага, ни процесса, ни строки кода — и
        вердикт «ушло» рождается ОДНИМ числом, без всякого участия замков."""
        f = cf(sent=1)
        self.assertEqual(sorted(f["client"].keys()),
                         ["armed", "attempted", "db", "err", "last_sent", "ok", "pairs",
                          "sent", "statuses", "total"])
        self.assertEqual(ex.client_state(f, self.cfg, NOW)[0], ex.CLIENT_SPOKE)


class TestO5Notes(unittest.TestCase):
    """Форма заметки: число, время и НАЗВАННЫЙ маршрут. Плюс асимметрия закрытия эпизодов."""

    def setUp(self):
        self.cfg = ex.config({})
        self.v = ex._o5(cf(sent=2, last="2026-08-17T19:00:00Z", pairs_sent=2,
                           pairs_last="2026-08-17T19:00:01Z"), self.cfg, NOW)[0]

    def test_note_carries_the_number_and_the_time(self):
        note = ex.render(self.v)
        self.assertTrue(note.startswith("🚨"))
        self.assertIn("ОТПРАВЛЕНО КЛИЕНТУ: 2", note)
        self.assertIn("2026-08-17T19:00:00Z", note)
        self.assertIn("2026-08-17T19:00:01Z", note)
        self.assertIn(ex.TAIL, note)

    def test_route_of_the_red_news_is_named_by_the_text(self):
        """Адрес красной новости не должен зависеть от ДЕФОЛТА признака маршрута: он назван самим
        сообщением. Проверяется ЖИВЫМ признаком (`dispatch_notify.awaits_reply`), а не догадкой."""
        import dispatch_notify
        self.assertTrue(dispatch_notify.awaits_reply(ex.render(self.v)))
        blind = ex._o5(cf(ok=False, blind=blind_for(9999.0)), self.cfg, NOW)[0]
        self.assertTrue(dispatch_notify.awaits_reply(ex.render(blind)))

    def test_unknown_note_never_reads_as_wellbeing(self):
        note = ex.render(ex._o5(cf(ok=False, err="база занята", blind=blind_for(9999.0)),
                                self.cfg, NOW)[0])
        self.assertIn("НЕ ПРОВЕРЕНО", note)
        self.assertIn("слепота", note)
        self.assertIn("база занята", note)

    def test_sent_episode_never_closes_but_blindness_does(self):
        """Отправленное клиенту не отменяется — у «ушло» закрытия нет ВОВСЕ. У слепоты есть, и
        закрывает её ПРОЧИТАННЫЙ источник (безразлично, что он показал)."""
        healthy = cf(sent=0)
        self.assertEqual(ex.closures(healthy, self.cfg, [self.v["key"]]), [])
        blind = ex._o5(cf(ok=False, blind=blind_for(9999.0)), self.cfg, NOW)[0]
        self.assertEqual(ex.closures(healthy, self.cfg, [blind["key"]]), [blind["key"]])
        # А пока источник не читается — эпизод слепоты НЕ закрывается: молчание не выздоровление.
        still = cf(ok=False, blind=blind_for(9999.0))
        self.assertEqual(ex.closures(still, self.cfg, [blind["key"]]), [])
        self.assertIn("снова читается", ex.render_close(blind["key"]))

    def test_a_new_send_is_a_new_episode(self):
        """Одна заметка на эпизод — но НОВАЯ отправка обязана дать новую: ключ несёт числа."""
        one = ex._o5(cf(sent=1), self.cfg, NOW)[0]["key"]
        two = ex._o5(cf(sent=2), self.cfg, NOW)[0]["key"]
        same = ex._o5(cf(sent=1), self.cfg, NOW + 99999.0)[0]["key"]
        self.assertNotEqual(one, two)
        self.assertEqual(one, same)


class TestO5Blindness(unittest.TestCase):
    """Вторая ветка О5: прибор обязан кричать о СВОЕЙ слепоте, иначе «тихо» становится вечным."""

    def setUp(self):
        self.cfg = ex.config({})

    def test_blindness_shorter_than_the_threshold_is_silent(self):
        self.assertEqual(ex._o5(cf(ok=False, blind=blind_for(3599.0)), self.cfg, NOW), [])

    def test_blindness_longer_than_the_threshold_speaks(self):
        v = ex._o5(cf(ok=False, blind=blind_for(3601.0)), self.cfg, NOW)
        self.assertEqual((len(v), v[0]["kind"]), (1, "o5_pc_client_unknown"))
        self.assertEqual(v[0]["awake"], 3601.0)

    def test_unmeasured_blindness_never_speaks(self):
        """Три честных «не измерено» — и ни одно не превращается ни в заметку, ни в «тихо»."""
        for blind in (None, blind_for(99999.0, measured=False), blind_for(None), {"measured": True}):
            f = cf(ok=False, blind=blind)
            self.assertEqual(ex._o5(f, self.cfg, NOW), [], "слепота %r заговорила" % blind)
            self.assertEqual(ex.client_state(f, self.cfg, NOW)[0], ex.CLIENT_UNKNOWN)

    def test_hands_count_blindness_by_the_waking_clock(self):
        """Сон машины слепотой не является: копится разница часов БОДРСТВОВАНИЯ, шаг ограничен
        одним наблюдением, а прочитанный источник обнуляет счётчик безусловно."""
        st = {}
        bad = {"ok": False, "err": "занято"}
        first = run_mod.update_client_unknown(st, bad, 1000.0, NOW)
        self.assertEqual((first["measured"], first["awake"]), (False, 0.0))   # прошлого нет
        second = run_mod.update_client_unknown(st, bad, 1600.0, NOW + 600.0)
        self.assertEqual((second["measured"], second["awake"]), (True, 600.0))
        # Сон: стенные часы ушли на сутки, часы бодрствования — на 10 минут.
        third = run_mod.update_client_unknown(st, bad, 2200.0, NOW + 87000.0)
        self.assertEqual(third["awake"], 1200.0)
        # Шаг ограничен: наблюдателя не было час — в слепоту идёт не больше одного шага.
        fourth = run_mod.update_client_unknown(st, bad, 99999.0, NOW + 90000.0)
        self.assertEqual(fourth["awake"], 1200.0 + run_mod.STEP_CAP_SEC)
        # Прочитанный источник обнуляет — и «неизвестно» после этого не копится.
        good = run_mod.update_client_unknown(st, {"ok": True}, 99999.0, NOW + 90600.0)
        self.assertEqual((good["awake"], good["since"]), (0.0, None))

    def test_no_waking_clock_is_unknown_and_never_a_note(self):
        st = {}
        got = run_mod.update_client_unknown(st, {"ok": False}, None, NOW)
        self.assertFalse(got["measured"])
        self.assertIsNone(got["awake"])
        self.assertIn("сон от слепоты не отличить", got["why"])
        self.assertEqual(ex._o5(cf(ok=False, blind=got), ex.config({}), NOW), [])

    def test_clock_going_backwards_restarts_the_count(self):
        st = {"client": {"awake": 5000.0, "silent": 4000.0, "since": NOW - 4000.0}}
        got = run_mod.update_client_unknown(st, {"ok": False}, 12.0, NOW)
        self.assertEqual((got["measured"], got["awake"]), (False, 0.0))
        self.assertIn("назад", got["why"])


class TestO5NegativeControl(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПЕРВИЧНОГО СВИДЕТЕЛЯ. Прибор, который не умеет сказать «УШЛО», негоден:
    его «тихо» не значит ничего, и дальше идти нельзя.

    СХЕМА НЕ СОЧИНЕНА: тестовая база создаётся ЖИВЫМ создателем схемы (`moderation_ipc.init_db`) на
    временном пути — то есть колонка в колонку та же, что в боевой очереди. Идеализированного
    «как удобно тесту» здесь нет ни одного поля (правило полосы «мок копирует живой формат»).
    БОЕВАЯ база не открывается ни на чтение, ни на запись: путь только временный."""

    def setUp(self):
        self.cfg = ex.config({})
        self.dir = tempfile.mkdtemp(prefix="expect_pc_o5_")
        self.db = os.path.join(self.dir, "НЕ_БОЕВАЯ_negative.db")
        import moderation_ipc
        moderation_ipc.init_db(self.db)
        self.assertNotEqual(os.path.abspath(self.db), os.path.abspath(eye.PROD_DB))

    def _insert(self, status, updated="2026-08-17T19:00:00Z"):
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO drafts (client_id, client_ref, status, created_ts, updated_ts)"
                        " VALUES (?,?,?,?,?)",
                        (0, "ТЕСТОВАЯ-СУЩНОСТЬ-О5", status, "2026-08-17T18:00:00Z", updated))
            con.commit()
        finally:
            con.close()

    def test_the_same_base_without_a_sent_row_is_quiet(self):
        """Первая половина: на законном фоне прибор МОЛЧИТ — иначе «отказ» ничего не доказывает."""
        self._insert("test_held")
        self._insert("posted")
        f = {"now": NOW, "client": eye.client_facts(self.db, os.path.join(self.dir, "нет.jsonl"))}
        st, info = ex.client_state(f, self.cfg, NOW)
        self.assertEqual((st, info["sent"], info["total"]), (ex.CLIENT_SILENT, 0, 2))
        self.assertEqual(ex._o5(f, self.cfg, NOW), [])

    def test_a_sent_row_on_a_test_entity_gives_a_refusal(self):
        """ВТОРАЯ половина и главная: состояние «отправленное клиенту есть» → прибор даёт ОТКАЗ,
        называет ЧИСЛО и ВРЕМЯ. Промолчал бы — прибор негоден."""
        self._insert("test_held")
        self._insert("sent")
        f = {"now": NOW, "client": eye.client_facts(self.db, os.path.join(self.dir, "нет.jsonl"))}
        st, info = ex.client_state(f, self.cfg, NOW)
        self.assertEqual(st, ex.CLIENT_SPOKE)
        self.assertEqual(info["sent"], 1)
        self.assertEqual(info["last"], "2026-08-17T19:00:00Z")
        v = ex._o5(f, self.cfg, NOW)
        self.assertEqual((len(v), v[0]["kind"], v[0]["spoken"]), (1, "o5_pc_client_sent", 1))
        self.assertIn("ОТПРАВЛЕНО КЛИЕНТУ: 1", ex.render(v[0]))
        # Вторая отправка — новый эпизод и новая заметка (число в ключе).
        self._insert("sent", updated="2026-08-17T20:00:00Z")
        f2 = {"now": NOW, "client": eye.client_facts(self.db, os.path.join(self.dir, "нет.jsonl"))}
        v2 = ex._o5(f2, self.cfg, NOW)
        self.assertEqual(v2[0]["spoken"], 2)
        self.assertNotEqual(v2[0]["key"], v[0]["key"])

    def test_armed_and_failed_are_reported_but_are_not_a_send(self):
        """Границу называем числом: `ready`/`failed` означают «замки поехали», но ОТПРАВКОЙ не
        являются — вердикт рождает только счётчик отправленных."""
        self._insert("ready")
        self._insert("failed")
        f = {"now": NOW, "client": eye.client_facts(self.db, os.path.join(self.dir, "нет.jsonl"))}
        st, info = ex.client_state(f, self.cfg, NOW)
        self.assertEqual((st, info["armed"], info["attempted"]), (ex.CLIENT_SILENT, 1, 1))
        self.assertEqual(ex._o5(f, self.cfg, NOW), [])

    def test_fixture_is_the_live_shape(self):
        """Форма фикстуры этого файла = форма ЖИВОГО читателя, ключ в ключ. Иначе регресс зелен на
        схеме «как удобно тесту», а прод отдаёт другое."""
        live = eye.client_facts(self.db, os.path.join(self.dir, "нет.jsonl"))
        self.assertEqual(sorted(live.keys()), sorted(cf()["client"].keys()))
        self.assertEqual(sorted(live["pairs"].keys()), sorted(cf()["client"]["pairs"].keys()))

    def test_the_second_witness_reads_the_live_line_format(self):
        """Реестр попыток: маркер снят с ЖИВОГО писателя (`json.dumps(..., ensure_ascii=False)`),
        а не набран руками — идеализированное «"sent":true» прибор бы не увидел."""
        p = os.path.join(self.dir, "pairs.jsonl")
        live_line = json.dumps({"ts": "2026-08-17T19:00:00Z", "client": "тест",
                                "sent": True, "reason": None}, ensure_ascii=False)
        with open(p, "w", encoding="utf-8") as f:
            f.write(live_line + "\n")
            f.write(json.dumps({"ts": "2026-08-17T18:00:00Z", "sent": False},
                               ensure_ascii=False) + "\n")
            f.write("не json вовсе\n")
        got = eye.pairs_counts(p)
        self.assertEqual((got["ok"], got["sent"], got["lines"]), (True, 1, 3))
        self.assertEqual(got["last"], "2026-08-17T19:00:00Z")
        self.assertIn(eye.PAIRS_SENT_MARK, live_line)

    def test_a_missing_register_is_a_positive_fact_but_a_broken_one_is_not(self):
        """Файла нет = «ни одной попытки не записано» (писатель зовётся на КАЖДУЮ попытку). А вот
        нечитаемый реестр — дырка, и она называется, а не молчит."""
        absent = eye.pairs_counts(os.path.join(self.dir, "нет.jsonl"))
        self.assertEqual((absent["ok"], absent["sent"]), (True, 0))
        self.assertIn("ни одной попытки", absent["err"])
        self.assertFalse(eye.pairs_counts(self.dir)["ok"])          # каталог вместо файла


class TestO5EyeIsReadOnly(unittest.TestCase):
    """ЗАМОК ИСТОЧНИКА: глаз О5 — единственный файл слоя с `sqlite3`, и за это у него отобрано всё
    остальное. Держится УСТРОЙСТВОМ (ast + живой отказ), а не докстрингом."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="expect_pc_ro_")
        with open(EYE_SRC, encoding="utf-8") as f:
            self.src = f.read()
        self.tree = ast.parse(self.src)

    def test_every_connection_is_opened_read_only(self):
        """Каждое соединение строится ТОЛЬКО через `ro_uri` и ТОЛЬКО с `uri=True`. Голая строка
        пути (то есть режим чтения-записи) не пройдёт этот тест ни в одной ветке."""
        found = 0
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "connect":
                continue
            found += 1
            where = "строка %d" % node.lineno
            self.assertTrue(node.args, "%s: connect без аргументов" % where)
            first = node.args[0]
            self.assertTrue(isinstance(first, ast.Call) and isinstance(first.func, ast.Name)
                            and first.func.id == "ro_uri",
                            "%s: путь подан не через ro_uri — это соединение на запись" % where)
            self.assertEqual([k.arg for k in node.keywords if k.arg == "uri"], ["uri"],
                             "%s: без uri=True режим mode=ro просто не читается" % where)
        self.assertEqual(found, 1, "соединений в глазу ровно одно — и оно read-only")

    def test_the_recipe_really_forbids_writing_live(self):
        """Живой отказ, а не вера в буквы: через рецепт `ro_uri` SQLite ЗАПИСЬ НЕ ПУСКАЕТ."""
        p = os.path.join(self.dir, "проба.db")
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE t (x INTEGER)")
        con.commit()
        con.close()
        self.assertIn("mode=ro", eye.ro_uri(p))
        ro = sqlite3.connect(eye.ro_uri(p), uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                ro.execute("INSERT INTO t (x) VALUES (1)")
            self.assertIn("readonly", str(ctx.exception).lower())
            self.assertEqual(ro.execute("SELECT COUNT(*) FROM t").fetchone()[0], 0)
        finally:
            ro.close()

    def test_no_write_verb_in_any_sql_of_the_eye(self):
        """Ни одного глагола записи в SQL. По ГРАНИЦЕ СЛОВА: в живом запросе стои́т `updated_ts`,
        и подстрочный поиск объявил бы колонку записью — ложный замок хуже отсутствующего."""
        import re as _re
        sqls = []
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in ("execute", "executemany", "executescript"):
                continue
            self.assertTrue(node.args, "строка %d: execute без SQL" % node.lineno)
            parts = [s.value for s in ast.walk(node.args[0])
                     if isinstance(s, ast.Constant) and isinstance(s.value, str)]
            self.assertTrue(parts, "строка %d: SQL собран не из литералов — прочитать нельзя"
                            % node.lineno)
            sqls.append((node.lineno, " ".join(parts)))
        self.assertTrue(sqls)
        rx = _re.compile(r"\b(%s)\b" % "|".join(_SQL_WRITE_VERBS))
        for lineno, sql in sqls:
            self.assertIsNone(rx.search(sql.upper()),
                              "строка %d: глагол записи в SQL глаза: %s" % (lineno, sql))
        # Контроль замка: он ОБЯЗАН ловить внесённое нарушение, иначе это молчание, а не замок.
        self.assertTrue(rx.search("INSERT INTO drafts (status) VALUES ('sent')"))
        self.assertIsNone(rx.search("SELECT status, MAX(UPDATED_TS) FROM DRAFTS"))

    def test_hands_still_do_not_import_sqlite3(self):
        """ПРЕЖНИЙ ЗАМОК ЦЕЛ: разрешение получено сужением, а не ослаблением. `sqlite3` живёт в
        ОДНОМ файле слоя, и это не руки и не решение."""
        with open(RUN_SRC, encoding="utf-8") as f:
            run_src = f.read()
        with open(EX_SRC, encoding="utf-8") as f:
            ex_src = f.read()
        for src, name in ((run_src, "руки"), (ex_src, "решение")):
            names = [a.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import)
                     for a in n.names]
            self.assertNotIn("sqlite3", names, "%s обзавелись sqlite3" % name)
        self.assertIn("import sqlite3", self.src)

    def test_the_eye_never_hands_out_a_message_body(self):
        """НАРУЖУ — ТОЛЬКО ЧИСЛА И ВРЕМЕНА. Ни тела сообщения, ни опознания клиента: у прибора для
        них нет ни поля в ответе, ни колонки в SQL."""
        # Имя файла НАМЕРЕННО не содержит искомых слов: путь уезжает в ответ прибора, и «тело.db»
        # провалило бы собственную проверку своим же именем.
        p = os.path.join(self.dir, "probe.db")
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, client_id INTEGER, "
                    "client_ref TEXT, draft TEXT, final_text TEXT, status TEXT, updated_ts TEXT)")
        con.execute("INSERT INTO drafts (client_id, client_ref, draft, final_text, status, "
                    "updated_ts) VALUES (77, 'секрет', 'тело', 'тело', 'sent', '2026-08-17T19:00:00Z')")
        con.commit()
        con.close()
        got = eye.client_facts(p, os.path.join(self.dir, "нет.jsonl"))
        flat = json.dumps(got, ensure_ascii=False)
        self.assertEqual(got["sent"], 1)
        for bad in ("тело", "секрет", "77"):
            self.assertNotIn(bad, flat, "прибор вынес наружу «%s»" % bad)
        for field in _BODY_FIELDS:
            self.assertNotIn(field, got)

    def test_the_key_is_read_only_and_the_checksum_is_taken_before_and_after(self):
        """ВТОРОЙ ПРЕДМЕТ ГЛАЗА — СВОЙ КЛЮЧ МОДЕРБОТА, и читается он ровно теми же правилами:
        `mode=ro` и контрольная сумма ДО и ПОСЛЕ. Проверяется тремя способами сразу, потому что
        ни один по отдельности не полон:
          1. сумма файла ДО чтения и ПОСЛЕ совпала И совпала с посчитанной снаружи — значит наше
             чтение не тронуло ни байта;
          2. соединение по тому же рецепту ЖИВЬЁМ отбивает запись (это и есть доказательство,
             когда сумма разойдётся: у боевого файла свой писатель с тиком 5 с);
          3. наружу уехали только время и число — ни одного тела сообщения."""
        p = os.path.join(self.dir, "ipc.db")
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        con.execute("CREATE TABLE drafts (id INTEGER PRIMARY KEY, draft TEXT, status TEXT, "
                    "updated_ts TEXT)")
        con.execute("INSERT INTO meta (k, v) VALUES ('heartbeat', '2026-08-18T15:09:38.981963+00:00')")
        con.execute("INSERT INTO drafts (draft, status, updated_ts) "
                    "VALUES ('тело клиента', 'ready', '2026-08-18T15:00:00Z')")
        con.commit()
        con.close()
        before = eye.sha256_file(p)
        got = eye.moderbot_heartbeat(p)
        after = eye.sha256_file(p)
        self.assertTrue(got["ok"], got["err"])
        self.assertEqual(got["raw"], "2026-08-18T15:09:38.981963+00:00")
        self.assertEqual((got["sha_before"], got["sha_after"]), (before, after))
        self.assertIs(got["sha_same"], True, "чтение ключа тронуло файл")
        self.assertEqual(before, after)
        # Общий канал прочитан, но он ОТДЕЛЬНОЕ поле: решение обязано различать предмет и сведение.
        self.assertIsNotNone(got["channel"])
        self.assertNotIn("тело клиента", json.dumps(got, ensure_ascii=False))
        # Живой отказ на запись по тому же рецепту — на той же самой базе.
        ro = sqlite3.connect(eye.ro_uri(p), uri=True)
        try:
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                ro.execute("UPDATE meta SET v = 'подделка' WHERE k = 'heartbeat'")
            self.assertIn("readonly", str(ctx.exception).lower())
        finally:
            ro.close()
        self.assertEqual(eye.sha256_file(p), before, "отбитая запись всё же изменила файл")
        # ТРИ ДЫРКИ — ТРИ ЧЕСТНЫХ ОТКАЗА, и ни один не притворяется прочитанным ключом.
        empty = os.path.join(self.dir, "пустая.db")
        con = sqlite3.connect(empty)
        con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        con.commit()
        con.close()
        self.assertFalse(eye.moderbot_heartbeat(empty)["ok"])
        self.assertIn("meta", eye.moderbot_heartbeat(empty)["err"])
        self.assertFalse(eye.moderbot_heartbeat(os.path.join(self.dir, "нет.db"))["ok"])
        no_table = os.path.join(self.dir, "безmeta.db")
        sqlite3.connect(no_table).close()
        self.assertFalse(eye.moderbot_heartbeat(no_table)["ok"])

    def test_prod_is_unreadable_under_testing(self):
        """Зеркало тривайра `moderation_ipc._conn`: под TESTING=1 боевая база не читается — но НЕ
        падением, а честным «не прочитан», который даёт исход НЕИЗВЕСТНО. Тихого зелёного тут нет
        ни на одной дороге."""
        was = os.environ.get("TESTING")
        try:
            os.environ["TESTING"] = "1"
            got = eye.db_counts(eye.PROD_DB)
            self.assertFalse(got["ok"])
            self.assertIn("изоляция тестов", got["err"])
            self.assertEqual(ex.client_state({"now": NOW, "client": eye.client_facts(eye.PROD_DB)},
                                             ex.config({}), NOW)[0], ex.CLIENT_UNKNOWN)
            # И ключ модербота — тем же запретом и с тем же исходом: под гейтом боевая база не
            # читается, а «не прочитан» даёт НЕИЗВЕСТНО, а не «работы нет».
            hb = eye.moderbot_heartbeat(eye.PROD_DB)
            self.assertFalse(hb["ok"])
            self.assertIn("изоляция тестов", hb["err"])
            self.assertIsNone(hb["sha_before"], "под изоляцией боевой файл даже не хешируется")
            # А чужой (временный) путь запрет НЕ трогает — иначе тест не смог бы ничего проверить.
            self.assertIsNone(eye.isolation_block(os.path.join(self.dir, "чужая.db")))
        finally:
            if was is None:
                os.environ.pop("TESTING", None)
            else:
                os.environ["TESTING"] = was


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ПУБЛИКАЦИЯ ВЕРДИКТА О ДЕТЯХ (18.08.2026). ГЛАВНЫЙ КЕЙС — ОТРИЦАТЕЛЬНЫЙ: РЕБЁНОК МЁРТВ →
#  ПЕРИОДИЧЕСКАЯ СТРОКА ОБЯЗАНА СКАЗАТЬ «РАБОТЫ НЕТ», А НЕ ПРОПАСТЬ ВМЕСТЕ С НИМ
# ══════════════════════════════════════════════════════════════════════════════════════════
def kfacts(mod=None, mod_silence=None, last=None, trace_age=600.0, hb_age=354.0, now=NOW):
    """Факты со ВСЕМ, что нужно публикации: ветка О3 (кого судим), ветка О4 (чем доказан оборот)
    и память о прошлой строке (когда говорили наружу и ЧТО сказали)."""
    f = lfacts(trace_age=trace_age, hb_age=hb_age, now=now)
    f["moderbot"] = modf(now=now) if mod is None else mod
    f["mod_silence"] = ({"measured": False, "awake": 0.0, "since": now,
                         "why": "своя запись в IPC — тишина обнулена"}
                        if mod_silence is None else mod_silence)
    f["kids_last"] = {"attempt": None, "sig": None} if last is None else last
    return f


def _watchdog_kids():
    """Имена детей ИЗ ИСХОДНИКА демона — `ast`, а не импорт: наблюдатель не импортирует
    наблюдаемого, и тест не смеет поднимать боевой модуль ради списка строк."""
    with open(os.path.join(REPO, "pc_orchestrator.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_client_watch_specs":
            out = []
            for d in ast.walk(node):
                if isinstance(d, ast.Dict):
                    for k, v in zip(d.keys, d.values):
                        if isinstance(k, ast.Constant) and k.value == "name" \
                                and isinstance(v, ast.Constant):
                            out.append(v.value)
            return out
    return []


class TestKidsRoster(unittest.TestCase):
    """Перечень детей — не литература: он обязан совпасть с тем, кого демон реально поднимает."""

    def test_roster_is_exactly_the_daemon_watchdog_list(self):
        """FAIL-CLOSED: новый ребёнок у контур-вотчдога обязан ПОКРАСНЕТЬ здесь, а не молча
        выпасть из публикации. Список читается из живого исходника демона, а не из фикстуры."""
        kids = _watchdog_kids()
        self.assertTrue(kids, "имена детей в pc_orchestrator._client_watch_specs не найдены")
        self.assertEqual(list(ex.KIDS), kids)
        self.assertIn(ex.KIDS_JUDGED, ex.KIDS)

    def test_every_kid_has_its_own_source_and_its_own_limit(self):
        """ГЛАВНАЯ ПРАВКА 18.08.2026 ОДНОЙ ПРОВЕРКОЙ: общего признака на всех больше нет. У каждого
        ребёнка СВОЙ источник и СВОЙ предел свежести, КРАТНЫЙ ЕГО СОБСТВЕННОМУ периоду, — и числа
        эти замерены (`docs/artifacts/2026-08-18-children-product-recon.md` + живая проба 18.08),
        а не назначены."""
        cfg = ex.config({})
        self.assertEqual(sorted(ex.KID_SIGNS), sorted(ex.KIDS), "признак заведён не всем детям")
        seen = {}
        for name in ex.KIDS:
            sign = ex.KID_SIGNS[name]
            limit = ex.kid_limit(name, cfg)
            self.assertGreater(limit, 0.0, "%s остался без предела" % name)
            self.assertEqual(limit, sign["tick"] * sign["ticks"],
                             "предел %s не кратен его собственному периоду" % name)
            seen[name] = (sign["src"], sign["tick"], limit)
        self.assertEqual(len({s for s, _, _ in seen.values()}), len(ex.KIDS),
                         "источник у детей общий — ровно этого и не должно быть")
        # Числа названы поимённо: 10 периодов по 30 с · 5 по 60 с · 180 по 5 с (порог О3).
        self.assertEqual(seen["pc_agent"], ("pc_agent.log", 30.0, 300.0))
        self.assertEqual(seen["userbot"], ("turbobaby_session.session", 60.0, 300.0))
        self.assertEqual(seen["moderation_bot"][1:], (5.0, 900.0))
        self.assertEqual(ex.kid_limit(ex.KIDS_JUDGED, cfg), cfg["mod"],
                         "у модербота завелось ВТОРОЕ число о том же ребёнке")

    def test_a_sign_that_moves_only_under_load_is_never_liveness(self):
        """ПРАВИЛО, А НЕ ПОЖЕЛАНИЕ: признак, который двигается под нагрузкой и замирает в простое,
        живостью не является — он меряет клиента, а не ребёнка, и врёт ровно в тихую ночь. Такой
        признак обязан давать «проверить не удалось» И НИКОГДА «работы нет»: звать мёртвым того,
        чей признак просто не обязан шевелиться, слой права не имеет.

        Проверяется ВНЕСЁННЫМ нарушением, а не верой в таблицу: подменяем признак живого ребёнка
        на нагрузочный и требуем третьего исхода при заведомо свежем продукте."""
        was = dict(ex.KID_SIGNS["pc_agent"])
        try:
            ex.KID_SIGNS["pc_agent"] = dict(was, src="userbot.log", idle_proof=False)
            f = kfacts()
            f["kids"] = {"pc_agent": {"fact": modf(age=1.0), "silence": silent_for(0.0)}}
            st, info = ex.kid_state("pc_agent", f, ex.config({}), NOW)
            self.assertEqual(st, ex.MOD_UNKNOWN)
            self.assertNotEqual(st, ex.MOD_IDLE, "нагрузочный признак вынес приговор")
            self.assertIn("замирает в простое", info["why"])
        finally:
            ex.KID_SIGNS["pc_agent"] = was

    def test_the_rejected_signs_are_named_in_code_and_none_of_them_is_used(self):
        """Отвергнутые признаки перечислены В КОДЕ, а не в докладе, и ни один не пробрался в
        источники: запретить «на словах» значит не запретить."""
        used = {s["src"] for s in ex.KID_SIGNS.values()}
        for bad in ex.KID_REJECTED:
            self.assertNotIn(bad, used, "отвергнутый признак %s всё-таки используется" % bad)
            self.assertTrue(ex.KID_REJECTED[bad].strip(), "у отказа не названа причина: %s" % bad)
        # Общий канал назван отвергнутым ПОИМЁННО — с него и началась слепота 18.08.
        self.assertIn("moderation_ipc.db", ex.KID_REJECTED)
        self.assertIn("userbot.log", ex.KID_REJECTED)
        self.assertIn("moderation_bot.log", ex.KID_REJECTED)


class TestKidsPublication(unittest.TestCase):
    """Периодическая строка о детях: что в ней сказано, когда она выходит и когда молчит."""

    def setUp(self):
        self.cfg = ex.config({})

    def test_period_is_the_six_hours_borrowed_from_o4_with_its_lock(self):
        self.assertEqual((self.cfg["kids"], self.cfg["kids_retry"]), (21600.0, 1800.0))
        # Тот же замок выбора, что у О4: ДВА полных окна обязаны уместиться в измеренные 16 ч
        # серверного О4 — одна потерянная строка не стоит соседу ложной тревоги.
        self.assertLessEqual(2 * self.cfg["kids"], 16 * 3600.0)

    def test_the_instrument_itself_is_untouched(self):
        """ЦЕЛЬ ЗАХОДА — ГОЛОС, А НЕ ПРИБОР: порог О3 прежний, вид нарушения прежний, и своего
        вида нарушения у публикации нет вовсе — она ПРОИЗВОДИТ строку, а не судит."""
        self.assertEqual(self.cfg["mod"], 900.0)
        self.assertIn("o3_pc_moderbot", ex.KINDS)
        self.assertEqual([k for k in ex.KINDS if "kid" in k], [])

    def test_every_kid_is_named_with_its_own_state(self):
        due, info = ex.kids_pulse_due(kfacts(), self.cfg, NOW)
        self.assertTrue(due)
        line = ex.render_kids(info)
        for name in ex.KIDS:
            self.assertIn(name, line, "ребёнок %s в строке не назван" % name)
        self.assertIn("moderation_bot — %s" % ex.MOD_OK, line)

    def test_the_word_alive_is_never_said_about_a_kid(self):
        """Закон полосы: «жив» и «работает» — разные слова, и первого этот слой не говорит.
        Три состояния задания живут здесь словами О3, а не переводом в «жив/не жив»."""
        rows = ex.kids_state(kfacts(), self.cfg, NOW)
        self.assertEqual(sorted({k["state"] for k in rows}), sorted({ex.MOD_OK, ex.MOD_UNKNOWN}))
        line = ex.render_kids(ex.kids_pulse_due(kfacts(), self.cfg, NOW)[1])
        self.assertNotIn("— жив", line)
        self.assertNotIn("не жив", line)

    def test_dead_kid_is_named_dead_and_never_dropped(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ, чистая половина: продукта нет 25 минут при живом PID → строка
        обязана СКАЗАТЬ «работы нет», а не исчезнуть вместе с ребёнком."""
        f = kfacts(mod=modf(age=1800.0), mod_silence=silent_for(1500.0))
        rows = {k["name"]: k["state"] for k in ex.kids_state(f, self.cfg, NOW)}
        self.assertEqual(rows["moderation_bot"], ex.MOD_IDLE)
        due, info = ex.kids_pulse_due(f, self.cfg, NOW)
        self.assertTrue(due, "мёртвый ребёнок отменил периодическую строку")
        line = ex.render_kids(info)
        self.assertIn("moderation_bot — %s" % ex.MOD_IDLE, line)
        self.assertIn("своего продукта нет", line)
        # И громкость при этом ОТДЕЛЬНАЯ: заметка владельцу — своя ветка и свой канал.
        self.assertEqual([v["kind"] for v in ex.verdict(f, self.cfg)], ["o3_pc_moderbot"])

    def test_third_outcome_survives_into_the_line_as_itself(self):
        """«Проверить не удалось» доезжает до строки третьим словом, а не вторым. Живой случай
        третьего исхода: продукт свеж, но пробу процесса сделать НЕ УДАЛОСЬ — значит отличить
        «работает» от «умер мгновение назад» нечем, и это ни «работы нет», ни «работает»."""
        f = kfacts(mod=modf(age=2.0, opened=None))
        rows = {k["name"]: k["state"] for k in ex.kids_state(f, self.cfg, NOW)}
        self.assertEqual(rows["moderation_bot"], ex.MOD_UNKNOWN)
        self.assertNotEqual(rows["moderation_bot"], ex.MOD_IDLE, "третий исход слит со вторым")
        line = ex.render_kids(ex.kids_pulse_due(f, self.cfg, NOW)[1])
        self.assertIn("moderation_bot — %s" % ex.MOD_UNKNOWN, line)
        self.assertIn("не подтверждено", line)
        self.assertIn("«проверить не удалось»", line)

    def test_line_takes_the_existing_pulse_form_and_not_a_new_one(self):
        """Форма не изобретена: тип `NOTE` первым словом (иначе писатель молча сделает `DONE`),
        та же голова, та же полоса, тот же разделитель и ДОСЛОВНО тот же сегмент об обороте,
        по которому сосед разбирает пульс демона."""
        info = ex.kids_pulse_due(kfacts(), self.cfg, NOW)[1]
        line = ex.render_kids(info)
        self.assertTrue(line.startswith(ex.PULSE_HEAD + " · ПК · "))
        self.assertIn("контур жив: оборот poll_once", line)
        self.assertIn("контур жив: оборот poll_once", ex.render_pulse(info))
        self.assertNotIn("🔔", line, "строка не тревога — значка заметки в ней быть не может")
        # Число детей на разбор влиять не должно: сегментов всегда пять, дети — внутри своего.
        self.assertEqual(len(line.split(" · ")), 5)

    def test_the_line_fits_the_writer_even_when_every_reason_is_huge(self):
        """Замок длины: строка длиннее 600 уедет ТЕЛОМ В ФАЙЛ, и сосед перестанет видеть детей
        ровно тогда, когда о них есть что сказать. Имена и состояния не режутся никогда."""
        info = {"limit": 21600.0, "turn": {"wall": 300.0},
                "kids": [{"name": n, "state": ex.MOD_UNKNOWN, "why": "п" * 400}
                         for n in ex.KIDS]}
        line = ex.render_kids(info)
        self.assertLessEqual(len(line), ex.KIDS_LINE_MAX)
        for name in ex.KIDS:
            self.assertIn("%s — %s" % (name, ex.MOD_UNKNOWN), line)

    def test_it_speaks_because_time_passed_not_because_something_happened(self):
        """СМЫСЛ ЗАХОДА ОДНОЙ ПРОВЕРКОЙ: состояние НЕ менялось, событий нет — строка всё равно
        выходит, как только истёк период. Именно этого не хватало соседу, чтобы поднять ожидание."""
        sig = ex.kids_signature(ex.kids_state(kfacts(), self.cfg, NOW))
        fresh = kfacts(last={"attempt": NOW - 3600.0, "sig": sig})
        due, info = ex.kids_pulse_due(fresh, self.cfg, NOW)
        self.assertFalse(due)
        self.assertIn("строка о детях свежая", info["why"])
        old = kfacts(last={"attempt": NOW - 7 * 3600.0, "sig": sig})
        due, info = ex.kids_pulse_due(old, self.cfg, NOW)
        self.assertTrue(due)
        self.assertEqual(info["why"], "период вышел")

    def test_a_change_of_state_speaks_before_the_period_but_not_below_the_floor(self):
        """Упавший ребёнок не ждёт шести часов; мигающий — не долбит канал каждые десять минут."""
        was = ex.kids_signature(ex.kids_state(kfacts(), self.cfg, NOW))
        dead = dict(mod=modf(age=1800.0), mod_silence=silent_for(1500.0))
        f = kfacts(last={"attempt": NOW - 3600.0, "sig": was}, **dead)
        due, info = ex.kids_pulse_due(f, self.cfg, NOW)
        self.assertTrue(due)
        self.assertEqual(info["why"], "состояние детей сменилось")
        soon = kfacts(last={"attempt": NOW - 300.0, "sig": was}, **dead)
        due, info = ex.kids_pulse_due(soon, self.cfg, NOW)
        self.assertFalse(due)
        self.assertIn("пол повтора", info["why"])

    def test_no_turn_means_no_line_so_the_neighbour_keeps_its_fangs(self):
        """ГЛАВНЫЙ ЗАМОК: серверное О4 считает следом ЛЮБУЮ строку ПК. Периодическая строка,
        уходящая при вставшем демоне, отняла бы у соседа зубы — поэтому оборот не доказан значит
        молчание, и это молчание само по себе новость."""
        f = kfacts(hb_age=99999.0)
        f["silence"] = {"measured": True, "awake": 99999.0, "why": ""}
        due, info = ex.kids_pulse_due(f, self.cfg, NOW)
        self.assertFalse(due)
        self.assertIn("наружу говорить нечего", info["why"])
        # Штамп объявленного захода для О2 — работа, а для публикации доказательством НЕ является
        # (тот же запрет, что у О4: заход объявлен ≠ виток замкнут).
        busy = kfacts(hb_age=1800.0)
        busy["busy"] = stamp(600.0)
        busy["silence"] = {"measured": True, "awake": 1800.0, "why": ""}
        self.assertEqual(ex.turn_state(busy, self.cfg, NOW)[0], ex.TURN_OK)
        self.assertFalse(ex.kids_pulse_due(busy, self.cfg, NOW)[0])

    def test_zero_kills_the_branch_before_any_fact_is_read(self):
        cfg0 = ex.config({"EXPECT_PC_KIDS_MIN": "0"})
        due, info = ex.kids_pulse_due(kfacts(), cfg0, NOW)
        self.assertFalse(due)
        self.assertIn("выключена порогом", info["why"])
        # А выключенное О3 не уносит строку с собой: она честно скажет «неизвестно» обо всех.
        cfg_mod0 = ex.config({"EXPECT_PC_MOD_MIN": "0"})
        rows = ex.kids_state(kfacts(), cfg_mod0, NOW)
        self.assertEqual({k["state"] for k in rows}, {ex.MOD_UNKNOWN})
        self.assertTrue(ex.kids_pulse_due(kfacts(), cfg_mod0, NOW)[0])


class TestKidsHands(unittest.TestCase):
    """Руки публикации: ЖИВОЙ файл проверочной сущности, свой счётчик попыток, свой канал.

    Боевые процессы здесь не поднимаются и не гасятся ни одной строкой: «мёртвый ребёнок» — это
    отдельный файл в своём временном каталоге, а не убитый модербот."""

    def setUp(self):
        self.cfg = ex.config({})
        self.dir = tempfile.mkdtemp(prefix="expect_pc_kids_")
        os.environ["CC_EXPECT_PC_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "CC_EXPECT_PC_DIR", None)
        self.pulsed, self.noted = [], []
        # Все руки, ходящие на диск и в мост, подменяются НА ВРЕМЯ теста: тест, читающий боевые
        # файлы, зелен или красен от того, что сейчас делает демон.
        for name in ("heartbeat_facts", "busy_facts", "trace_facts", "moderbot_facts",
                     "client_facts", "awake_seconds", "kid_facts"):
            self.addCleanup(setattr, run_mod, name, getattr(run_mod, name))
        self.real_mod_facts = run_mod.moderbot_facts       # НАСТОЯЩЕЕ чтение файлов, не заглушка
        self.real_own_facts = run_mod.own_facts
        # ДВОЕ ОСТАЛЬНЫХ ДЕТЕЙ ПО УМОЛЧАНИЮ БЕЗ ФАКТА: тест, читающий ЖИВЫЕ `pc_agent.log` и
        # `turbobaby_session.session`, был бы зелен или красен от того, что прямо сейчас делает
        # боевой контур. Свои проверочные сущности им даёт `_kid_entity` там, где они и нужны.
        run_mod.kid_facts = lambda name: None
        run_mod.busy_facts = lambda path=None: {"ok": True, "since": None,
                                                "limit": ex.TASK_TIMEOUT_SEC, "err": ""}
        run_mod.trace_facts = lambda path=None, attempt=None: {
            "ok": True, "ts": NOW - 600.0, "line": "", "attempt": attempt, "err": ""}
        run_mod.client_facts = lambda: {"ok": True, "sent": 0, "armed": 0, "attempted": 0,
                                        "total": 0, "last_sent": None,
                                        "pairs": {"ok": True, "sent": 0, "err": "", "last": None}}

    def _lock(self, path, author, age):
        """Лок проверочной сущности. `author=True` — НАШ СОБСТВЕННЫЙ процесс (номер + его
        настоящее время запуска): только так проба отвечает True по-честному, живым ядром, а не
        моком. `author=False` — номер, которого в системе нет (Windows не раздаёт номера, не
        кратные четырём), то есть ДОКАЗАННАЯ смерть."""
        pid = os.getpid() if author else 999999
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(pid))
        born = run_mod.process_probe(pid)[1] if author else (NOW - age)
        if born:
            os.utime(path, (born, born))
        return pid

    def _entity(self, age, author=False, channel=1.0):
        """ПРОВЕРОЧНАЯ СУЩНОСТЬ МОДЕРБОТА — настоящая база SQLite с настоящей таблицей `meta`,
        читаемая настоящими руками через настоящий `mode=ro`. Боевые `moderation_ipc.db` и
        `moderation_bot.lock` не тронуты, боевой модербот не поднят и не погашен.

        `age` — возраст ЕГО СОБСТВЕННОГО ключа; `channel` — возраст ОБЩЕГО файла. Они РАЗНЫЕ
        намеренно: ровно их расхождение и есть класс 18.08 («канал свеж, а его продукта нет»)."""
        db = os.path.join(self.dir, "moderation_ipc.db")
        lock = os.path.join(self.dir, "moderation_bot.lock")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO meta (k, v) VALUES ('heartbeat', ?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                    (datetime.datetime.fromtimestamp(NOW - age, datetime.timezone.utc)
                     .isoformat(),))
        con.commit()
        con.close()
        os.utime(db, (NOW - channel, NOW - channel))
        self._lock(lock, author, age)
        real = self.real_mod_facts
        return lambda ipc=None, lock_=None, _r=real, _d=db, _l=lock: _r(_d, _l)

    def _kid_entity(self, name, age, author=False):
        """ПРОВЕРОЧНАЯ СУЩНОСТЬ ребёнка-файла: его личный продукт со своим mtime и его личный лок.
        Живые `pc_agent.log` и `turbobaby_session.session` не читаются и не пишутся."""
        product = os.path.join(self.dir, "%s.продукт" % name)
        lock = os.path.join(self.dir, "%s.lock" % name)
        with open(product, "w", encoding="utf-8") as f:
            f.write("проверочная сущность %s" % name)
        os.utime(product, (NOW - age, NOW - age))
        self._lock(lock, author, age)
        return product, lock

    def _run(self, awake, now):
        run_mod.heartbeat_facts = lambda path=None: {"ok": True, "raw": hb_at(300.0, now),
                                                     "err": ""}
        run_mod.awake_seconds = lambda: awake
        return run_mod.run(dry=False, now=now, getter=lambda status: {"ok": True, "items": []},
                           notifier=lambda t: (self.noted.append(t), True)[1],
                           pulser=lambda t: (self.pulsed.append(t), True)[1])

    def test_dead_kid_on_a_real_entity_is_published_and_not_dropped(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЦЕЛИКОМ, от файла до строки, И ЭТО ЖИВОЙ СЛУЧАЙ 18.08.2026: свой
        ключ ребёнка молчит 50 минут, ОБЩИЙ файл при этом свежий (1 с — его двигает сосед), а
        номера из лока в системе нет. Строка обязана НАЗВАТЬ ребёнка неработающим, а не пропасть
        вместе с ним и не спрятаться в «неизвестно».

        И приговор выносится ПЕРВЫМ наблюдением: проба процесса накопления тишины не требует —
        до 18.08 здесь нужны были два наблюдения, а в живом случае второго не случилось бы вовсе
        (модербота подняли через 6 мин 18 с)."""
        run_mod.moderbot_facts = self._entity(age=3000.0, author=False, channel=1.0)
        out = self._run(awake=100000.0, now=NOW)              # ПЕРВОЕ наблюдение — и уже приговор
        rows = dict((k["name"], k) for k in out["kids"])
        self.assertEqual(rows["moderation_bot"]["state"], ex.MOD_IDLE)
        self.assertNotEqual(rows["moderation_bot"]["state"], ex.MOD_UNKNOWN)
        self.assertLess(rows["moderation_bot"]["channel_age"], 5.0,
                        "общий файл обязан быть свежим — иначе это не тот класс")
        self.assertEqual(out["kids_pulse"], "ушла")
        line = self.pulsed[-1]
        self.assertIn("moderation_bot — %s" % ex.MOD_IDLE, line)
        self.assertTrue(line.startswith(ex.PULSE_HEAD))
        for name in ex.KIDS:
            self.assertIn(name, line)
        # ГРОМКОСТЬ ОТДЕЛЬНО: заметка владельцу ушла своим каналом, а строка журнала в него не
        # попала ни разу — периодическая запись тревогой не является.
        self.assertTrue(any("не делает свою работу" in n for n in self.noted))
        self.assertEqual([n for n in self.noted if n.startswith(ex.PULSE_HEAD)], [])
        # Второе наблюдение состояния не меняет и ВТОРОЙ строки не рождает: период не вышел.
        out2 = self._run(awake=102000.0, now=NOW + 2000.0)
        self.assertEqual(dict((k["name"], k["state"]) for k in out2["kids"])["moderation_bot"],
                         ex.MOD_IDLE)
        self.assertIsNone(out2["kids_pulse"])
        self.assertEqual(len(self.pulsed), 1)

    def test_each_kid_is_judged_by_its_own_sign_end_to_end(self):
        """ЦЕЛЬ ЗАХОДА ЦЕЛИКОМ, ОТ ФАЙЛА ДО СТРОКИ: три ребёнка, три РАЗНЫХ источника и три РАЗНЫХ
        исхода в ОДНОЙ строке — «одно слово на всех» больше не бывает.

          pc_agent       свой продукт свеж, процесс жив           → делает работу
          userbot        процесса из лока в системе НЕТ           → работы нет (и сразу, не через порог)
          moderation_bot своего ключа не прочитать, лока нет      → неизвестно

        Боевых процессов не поднято и не погашено ни одного: всё это файлы во временном каталоге."""
        made = {"pc_agent": self._kid_entity("pc_agent", age=1.0, author=True),
                "userbot": self._kid_entity("userbot", age=1.0, author=False)}
        real_own = self.real_own_facts
        run_mod.kid_facts = lambda name, _m=made, _r=real_own: (
            None if _m.get(name) is None else _r(_m[name][0], _m[name][1], name))
        gone = os.path.join(self.dir, "нет")
        run_mod.moderbot_facts = (lambda ipc=None, lock=None, _r=self.real_mod_facts, _g=gone:
                                  _r(_g + ".db", _g + ".lock"))
        out = self._run(awake=100000.0, now=NOW)
        rows = {k["name"]: k for k in out["kids"]}
        self.assertEqual(rows["pc_agent"]["state"], ex.MOD_OK)
        self.assertEqual(rows["userbot"]["state"], ex.MOD_IDLE)
        self.assertEqual(rows["moderation_bot"]["state"], ex.MOD_UNKNOWN)
        self.assertEqual(len({k["state"] for k in out["kids"]}), 3,
                         "три ребёнка — три состояния, а не одно слово на всех")
        line = self.pulsed[-1]
        self.assertIn("pc_agent — %s" % ex.MOD_OK, line)
        self.assertIn("userbot — %s (процесса из лока в системе нет)" % ex.MOD_IDLE, line)
        self.assertIn("moderation_bot — %s" % ex.MOD_UNKNOWN, line)
        self.assertLessEqual(len(line), ex.KIDS_LINE_MAX)
        # У каждого в отчёте назван СВОЙ источник — «работы нет» без имени признака непроверяемо.
        self.assertEqual(rows["pc_agent"]["src"], "pc_agent.log")
        self.assertEqual(rows["userbot"]["src"], "turbobaby_session.session")

    def test_a_working_kid_is_published_too_and_the_attempt_is_remembered(self):
        """Вторая сторона: ребёнок РАБОТАЕТ — строка всё равно выходит (иначе молчание живого
        ребёнка не отличить от молчания журнала), а повтор держится собственным счётчиком."""
        run_mod.moderbot_facts = self._entity(age=1.0, author=True)
        out = self._run(awake=100000.0, now=NOW)
        self.assertEqual(out["kids_pulse"], "ушла")
        self.assertIn("moderation_bot — %s" % ex.MOD_OK, self.pulsed[-1])
        self.assertEqual(len(self.pulsed), 1)
        st = run_mod.load_state()
        self.assertEqual(st["kids"]["sig"],
                         "pc_agent=%s|userbot=%s|moderation_bot=%s"
                         % (ex.MOD_UNKNOWN, ex.MOD_UNKNOWN, ex.MOD_OK))
        # Через десять минут — молчим: период не вышел, состояние прежнее.
        out2 = self._run(awake=100600.0, now=NOW + 600.0)
        self.assertIsNone(out2["kids_pulse"])
        self.assertIn("строка о детях свежая", out2["kids_why"])
        self.assertEqual(len(self.pulsed), 1)

    def test_dry_run_touches_no_channel_at_all(self):
        run_mod.moderbot_facts = self._entity(age=1.0, author=True)
        run_mod.heartbeat_facts = lambda path=None: {"ok": True, "raw": hb_at(300.0, NOW), "err": ""}
        run_mod.awake_seconds = lambda: 100000.0
        out = run_mod.run(dry=True, now=NOW, getter=lambda status: {"ok": True, "items": []},
                          notifier=lambda t: (self.noted.append(t), True)[1],
                          pulser=lambda t: (self.pulsed.append(t), True)[1])
        self.assertEqual((self.pulsed, self.noted), ([], []))
        self.assertEqual(out["kids_pulse"], "нужна (сухой прогон — не пишем)")
        self.assertIn("дети контура", out["kids_line"])

    def test_the_channel_is_the_journal_and_never_the_owner_card(self):
        """Замок канала читается из КОДА, а не из докстринга: руки публикации зовут писателя
        журнала и не зовут доставку карточек."""
        with open(RUN_SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "maybe_kids_pulse"]
        self.assertEqual(len(fn), 1)
        names = {n.id for n in ast.walk(fn[0]) if isinstance(n, ast.Name)}
        self.assertIn("send_pulse", names)
        self.assertNotIn("send_note", names)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ИНВАРИАНТ EXPECT_PC_PURE — граница держится отсутствием инструментов, а не докстрингом
#  (зеркало CARD_DUTY_PURE этой полосы и EXPECTATIONS_PURE серверной)
# ══════════════════════════════════════════════════════════════════════════════════════════
_ALLOWED_IMPORTS = frozenset(("datetime", "re"))
_FORBIDDEN_CALLS = frozenset((
    "open", "exec", "eval", "compile", "__import__", "input", "globals", "vars", "setattr",
))
_FORBIDDEN_ATTR_ROOTS = frozenset((
    "os", "sys", "subprocess", "shutil", "socket", "requests", "urllib", "pathlib", "tempfile",
    "sqlite3", "bridge_http", "pc_orchestrator", "bc", "builtins", "logging", "time",
))
# Слова, которыми в этой системе МЕНЯЮТ мир. Ожидание только наблюдает: ни одного из них в коде
# решения быть не может — ни как вызова, ни как имени.
_FORBIDDEN_NAMES = frozenset((
    "claim_task", "complete_task", "enqueue_task", "task_heartbeat", "approve_task",
    "schtasks", "taskkill", "Popen", "system", "remove", "unlink", "rmtree", "kill",
))


def pure_findings(src):
    """→ список (адрес, чем плохо). Пустой = модуль решения чист. Разбор ast, не подстрока:
    имя в комментарии, строке и докстринге кодом не является."""
    out = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in _ALLOWED_IMPORTS:
                    out.append(("строка %d" % node.lineno,
                                "импорт «%s» вне списка %s — у решения появились руки"
                                % (a.name, sorted(_ALLOWED_IMPORTS))))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in _ALLOWED_IMPORTS:
                out.append(("строка %d" % node.lineno,
                            "импорт из «%s» вне списка — у решения появились руки" % node.module))
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _FORBIDDEN_CALLS:
                out.append(("строка %d" % node.lineno,
                            "вызов «%s» в модуле, который обязан быть чистым решением" % fn.id))
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                    and fn.value.id in _FORBIDDEN_ATTR_ROOTS:
                out.append(("строка %d" % node.lineno,
                            "обращение к «%s.%s» — модуль решения не смеет трогать мир"
                            % (fn.value.id, fn.attr)))
            if isinstance(fn, ast.Attribute) and fn.attr in _FORBIDDEN_NAMES:
                out.append(("строка %d" % node.lineno,
                            "вызов «%s» — ожидание НАБЛЮДАЕТ, а не действует" % fn.attr))
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            out.append(("строка %d" % node.lineno,
                        "имя «%s» в коде ожидания: менять мир оно не вправе" % node.id))
    return out


class TestExpectPcPure(unittest.TestCase):
    """FAIL-CLOSED: файла нет / не парсится → провал, а не тишина."""

    def test_live_decision_module_is_clean(self):
        with open(EX_SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(pure_findings(src), [], "живой expectations_pc.py обзавёлся руками")

    def test_exactly_two_imports(self):
        with open(EX_SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(len(imports), 2, "импортов у решения ровно два: datetime и re")

    def test_the_invariant_catches_an_injected_violation(self):
        """Инвариант, который не ловит нарушение, — это молчание, а не замок. Шесть подлогов."""
        cases = [
            ("руки: файл", "import os\n"),
            ("канал: мост", "import bridge_http\n"),
            ("исполнение", "import re\nx = eval('1')\n"),
            ("мутация очереди", "import re\ndef f(bc):\n    return bc.complete_task(1, 'failed')\n"),
            ("перезапуск", "import re\ndef f(s):\n    return s.schtasks('/Run')\n"),
            ("подпроцесс", "import re\nimport subprocess\n"),
        ]
        for name, src in cases:
            self.assertTrue(pure_findings(src), "подлог «%s» инвариант не поймал" % name)
        self.assertEqual(pure_findings("import re\nimport datetime\nX = re.compile('a')\n"), [],
                         "законный код инвариант флагать не должен")


class TestHandsHaveNoTeeth(unittest.TestCase):
    """У РУК границы шире (им нужны файлы и мост), но действовать они всё равно не вправе."""

    def test_runner_never_mutates_the_queue_or_restarts_anything(self):
        with open(RUN_SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in _FORBIDDEN_NAMES:
                bad.append("строка %d: %s" % (node.lineno, node.func.attr))
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in ("subprocess", "shutil", "sqlite3"):
                        bad.append("строка %d: импорт %s" % (node.lineno, a.name))
        self.assertEqual(bad, [], "руки наблюдателя обзавелись действием")

    def test_runner_reads_the_queue_with_get_only(self):
        with open(RUN_SRC, encoding="utf-8") as f:
            src = f.read()
        for verb in ("claim_task", "complete_task", "enqueue_task", "task_heartbeat"):
            self.assertNotIn(verb + "(", src, "мутация очереди в наблюдателе: %s" % verb)
        self.assertIn("get_pending", src)

    def test_no_escalation_task_branch_exists_at_all(self):
        """У серверного оригинала есть сильная ветка «задача в очередь». Здесь её НЕТ ВОВСЕ —
        запрет владельца 11.08.2026: ожидание только наблюдает."""
        self.assertFalse(hasattr(ex, "task_text"))
        with open(RUN_SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        # Ищем ВЫЗОВ, а не слово: «ни claim, ни enqueue» в докстринге — это обещание кода, а не
        # его нарушение (то же правило исполняющей позиции, что в гарде).
        calls = [n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertEqual([c for c in calls if "enqueue" in c or "task" == c], [])


    # ═══════════ О6: ДОСТАВКА ПРАВОК СЧИТАЕТСЯ ЗАМЫКАНИЕМ ИМПОРТОВ (02.09.2026) ═════════════


def code_fact(now=NOW, files=5, newest_ago=None, started_ago=None, ok=True, reason="",
              opened=True, pid=7092, mapped=2, gap=("io_utf8.py",), newest_file="io_utf8.py",
              entry="pc_agent.py", err="", since_ago=None):
    """Факт О6 в том виде, в каком его кладут руки (`code_facts`). Оба времени — СТЕННЫЕ метки
    (mtime файла и момент запуска процесса), потому что расхождение «диск новее памяти» есть
    разность двух абсолютных величин, а не накопленное молчание.

    ТРЕТЬЕ ЧИСЛО `since` (05.09.2026) — самая РАННЯЯ невзятая правка, от неё считается ожидание.
    По умолчанию оно равно `newest`: это случай ОДНОЙ свежей правки, и в нём обе величины
    совпадают. Умолчание стои́т здесь намеренно — руки в проде кладут `since` ВСЕГДА, когда
    процесс отстаёт, и фикстура обязана повторять живую форму, а не удобную. Кому нужен
    разъезд (много правок подряд) — называет `since_ago` явно."""
    newest = None if newest_ago is None else now - newest_ago
    started = None if started_ago is None else now - started_ago
    if since_ago is not None:
        since = now - since_ago
    elif newest is not None and started is not None and newest > started:
        since = newest
    else:
        since = None
    return {"ok": ok, "entry": entry, "files": files, "reason": reason,
            "newest": newest, "newest_file": newest_file,
            "gap": list(gap) if gap else None, "mapped": mapped,
            "pid": pid, "opened": opened, "since": since,
            "started": started, "lock_mtime": started, "err": err}


def code_facts_of(now=NOW, **per_process):
    """Факты по ВСЕМ наблюдаемым: не названные явно считаются свежими (процесс моложе правки),
    чтобы кейс говорил ровно об одном процессе, а не обо всех сразу."""
    fresh = code_fact(now=now, newest_ago=7200.0, started_ago=60.0)
    code = {n: dict(fresh) for n in ex.CODE_WATCHED}
    code.update(per_process)
    return {"now": now, "code": code}


class TestO6ClosureIsTheSourceOfTruth(unittest.TestCase):
    """ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА ЗАДАНИЯ, дословно и по одному на кейс."""

    def setUp(self):
        self.cfg = ex.config({})

    # 1. коммит в модуль, которого нет в карте, но который входит в замыкание → пометка
    def test_a_commit_into_a_module_the_map_does_not_know_marks_the_process(self):
        f = code_facts_of(pc_agent=code_fact(newest_ago=60.0, started_ago=7200.0,
                                             newest_file="io_utf8.py", mapped=2, files=5))
        state, info = ex.code_state("pc_agent", f, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_STALE)
        self.assertAlmostEqual(info["behind"], 7140.0, places=3)
        keys = [v["key"] for v in ex.verdict(f, self.cfg) if v["kind"] == "o6_pc_code_stale"]
        self.assertEqual(keys, ["o6c|pc_agent|%d" % int(NOW - 7200.0)])
        text = ex.render([v for v in ex.verdict(f, self.cfg)
                          if v["kind"] == "o6_pc_code_stale"][0])
        self.assertIn("io_utf8.py", text)             # имя файла — не «какой-то модуль»
        self.assertIn("знает 2", text)                # улика: карта знает 2 из 5

    # 2. процесс МОЛОЖЕ коммита → не помечается
    def test_a_process_younger_than_the_edit_is_not_marked(self):
        f = code_facts_of(pc_agent=code_fact(newest_ago=7200.0, started_ago=60.0))
        self.assertEqual(ex.code_state("pc_agent", f, self.cfg, NOW)[0], ex.CODE_FRESH)
        self.assertEqual([v for v in ex.verdict(f, self.cfg) if str(v["kind"]).startswith("o6")], [])

    def test_a_gap_smaller_than_the_threshold_is_not_yet_news(self):
        """Доставка законно занимает время (у демона измеренные 28 с) — порог не украшение."""
        f = code_facts_of(pc_agent=code_fact(newest_ago=3540.0, started_ago=3600.0))   # отстал 60 с
        self.assertEqual(ex.code_state("pc_agent", f, self.cfg, NOW)[0], ex.CODE_FRESH)
        f2 = code_facts_of(pc_agent=code_fact(newest_ago=1740.0, started_ago=3600.0))  # отстал 31 мин
        self.assertEqual(ex.code_state("pc_agent", f2, self.cfg, NOW)[0], ex.CODE_STALE)

    # 3. замыкание посчитать не удалось → НЕИЗВЕСТНО, и пометка СТАВИТСЯ, а не снимается
    def test_an_uncomputable_closure_is_unknown_and_the_mark_is_set_not_dropped(self):
        f = code_facts_of(pc_agent=code_fact(ok=False, reason="pc_agent.py: SyntaxError",
                                             newest_ago=None, started_ago=600.0))
        state, info = ex.code_state("pc_agent", f, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_UNKNOWN)
        self.assertIn("SyntaxError", info["why"])
        kinds = [v["kind"] for v in ex.verdict(f, self.cfg)]
        self.assertIn("o6_pc_code_unknown", kinds,
                      "«не смог посчитать» обязано СТАВИТЬ пометку — молчание здесь и есть дефект")
        self.assertEqual(ex.closures(f, self.cfg, ["o6u|pc_agent"]), [],
                         "незнание не смеет ЗАКРЫТЬ уже открытую пометку")

    def test_every_hole_in_the_facts_leads_to_the_mark_and_never_to_silence(self):
        """Каждая дырка проверена отдельно: направление fail-safe у О6 обратное остальным веткам."""
        holes = {
            "факта нет вовсе": {},
            "граф не построился": code_fact(ok=False, reason="каталог не читается"),
            "самой свежей правки нет": code_fact(newest_ago=None, started_ago=600.0),
            "процесса по локу нет": code_fact(newest_ago=60.0, started_ago=600.0, opened=False),
            "момент запуска не добыт": code_fact(newest_ago=60.0, started_ago=None, opened=True),
        }
        for why, fact in holes.items():
            f = code_facts_of()
            f["code"]["pc_agent"] = fact if fact else None
            if not fact:
                f["code"].pop("pc_agent")
            self.assertEqual(ex.code_state("pc_agent", f, self.cfg, NOW)[0], ex.CODE_UNKNOWN, why)
            self.assertIn("o6_pc_code_unknown", [v["kind"] for v in ex.verdict(f, self.cfg)], why)

    # 4. процесс жив и отвечает, но старше коммита → расхождение ВИДНО
    def test_a_live_answering_process_older_than_the_edit_shows_the_gap(self):
        """Живой PID свежим кодом не является — ровно тот же закон, что у О3 про «жив ≠ работает»."""
        f = code_facts_of(pc_agent=code_fact(newest_ago=1200.0, started_ago=186000.0,
                                             opened=True, pid=7092))
        state, info = ex.code_state("pc_agent", f, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_STALE)
        self.assertAlmostEqual(info["behind"], 184800.0, places=3)
        text = ex.render([v for v in ex.verdict(f, self.cfg)
                          if v["kind"] == "o6_pc_code_stale"][0])
        self.assertIn("2 сут", text)                        # «на сколько» названо числом
        self.assertIn("7092", text)                         # и о ком именно речь
        for word in ("упал", "умер", "не работает"):
            self.assertNotIn(word, text, "О6 судит ПАМЯТЬ процесса, а не его здоровье")

    def test_the_verdict_is_recomputed_every_run_and_never_goes_silent(self):
        """ГЛАВНОЕ ОТЛИЧИЕ ОТ ПРЕЖНЕЙ ПОМЕТКИ: она была СОБЫТИЕМ и звучала один раз. Здесь —
        состояние: тот же факт даёт вердикт на каждом из десяти подряд прогонов.

        ПОПРАВКА 05.09.2026 — И ОНА НЕ ОСЛАБЛЕНИЕ, А УТОЧНЕНИЕ. Тест держал РОД вердикта
        (`o6_pc_code_stale`) наравне с его наличием, и это молча запрещало расхождению взрослеть:
        ожидание, перешагнувшее срок, обязано СМЕНИТЬ голос на тревогу, иначе восьмичасовой затык
        03.09 звучал бы той же строкой, что и минутное ожидание. Теперь тест держит РОВНО своё:
        слой не молчит НИ НА ОДНОМ витке (проверяется по семейству О6, а не по одному роду) и
        ключ на каждое СОСТОЯНИЕ ровно один. Сама смена голоса проверяется отдельно —
        `TestVersionNewsNotAlarm.test_the_same_gap_grows_from_news_into_alarm`."""
        seen = set()
        # Факт НЕПОДВИЖЕН (процесс не перезапускался, файл не правился), а «сейчас» едет вперёд —
        # ровно так выглядят десять подряд прогонов наблюдателя над одним и тем же расхождением.
        fixed = code_fact(now=NOW, newest_ago=1200.0, started_ago=186000.0)
        for i in range(10):
            now = NOW + i * 600.0
            f = code_facts_of(now=now, pc_agent=dict(fixed))
            v = [x for x in ex.verdict(f, self.cfg)
                 if str(x["kind"]).startswith("o6") and x.get("name") == "pc_agent"]
            self.assertEqual(len(v), 1, "виток %d промолчал при живом расхождении" % i)
            seen.add(v[0]["key"])
        # Ключей ровно два и они названы поимённо: один на ожидание, один на тревогу. Больше двух
        # значило бы, что заметка повторяется каждые десять минут; один — что взросления нет.
        self.assertEqual(sorted(seen), ["o6c|pc_agent|%d" % int(NOW - 186000.0),
                                        "o6l|pc_agent|%d" % int(NOW - 186000.0)],
                         "на воплощение процесса ровно один ключ НА СОСТОЯНИЕ: %s" % sorted(seen))

    def test_the_episode_closes_only_on_proven_freshness(self):
        stale = code_facts_of(pc_agent=code_fact(newest_ago=60.0, started_ago=7200.0))
        key = "o6c|pc_agent|%d" % int(NOW - 7200.0)
        self.assertEqual(ex.closures(stale, self.cfg, [key]), [], "расхождение живо — не закрываем")
        blind = code_facts_of(pc_agent=code_fact(ok=False, reason="каталог не читается"))
        self.assertEqual(ex.closures(blind, self.cfg, [key]), [], "слепота выздоровлением не является")
        restarted = code_facts_of(pc_agent=code_fact(newest_ago=7200.0, started_ago=60.0))
        self.assertEqual(ex.closures(restarted, self.cfg, [key]), [key],
                         "процесс перезапущен и доказанно свеж — эпизод обязан закрыться")

    def test_zero_kills_the_branch_before_any_fact_is_read(self):
        off = ex.config({ex.CODE_MIN_ENV: "0"})
        self.assertEqual(off["code"], 0.0)
        f = code_facts_of(pc_agent=code_fact(newest_ago=60.0, started_ago=186000.0))
        self.assertEqual([v for v in ex.verdict(f, off) if str(v["kind"]).startswith("o6")], [])
        self.assertEqual(ex.code_state("pc_agent", f, off, NOW)[0], ex.CODE_UNKNOWN)

    def test_the_watched_list_is_processes_and_names_match_the_kids(self):
        """Перечень — ПРОЦЕССЫ, а не файлы, и имена детей ОДНИ И ТЕ ЖЕ во всех строках слоя."""
        self.assertEqual(set(ex.KIDS) - set(ex.CODE_WATCHED), set(),
                         "ребёнок, о котором говорит О3, обязан судиться и О6")
        self.assertIn("pc_orchestrator", ex.CODE_WATCHED)
        for name, entry in ex.CODE_ENTRIES:
            self.assertTrue(os.path.isfile(os.path.join(REPO, entry)),
                            "входной точки «%s» процесса «%s» на диске нет" % (entry, name))


class TestO6Hands(unittest.TestCase):
    """РУКИ О6: замыкание СЧИТАЕТСЯ тем же обходом, что держит ворота, а не берётся списком."""

    def test_hands_call_the_same_closure_the_client_gate_uses(self):
        seen = []

        def fake_closure(repo, entries=None, cut=None):
            seen.append((entries, cut))
            import client_contour as cc
            return cc.Closure(frozenset({entries[0], "io_utf8.py"}), frozenset(), True, "ok")

        out = run_mod.code_facts(closure_fn=fake_closure,
                                 stat_fn=lambda p: type("S", (), {"st_mtime": 100.0})(),
                                 lock_fn=lambda rec, lock, whose: rec,
                                 map_fn=lambda p: set())
        self.assertEqual(sorted(out), sorted(ex.CODE_WATCHED))
        self.assertEqual([c for _e, c in seen], [()] * len(ex.CODE_WATCHED),
                         "срез на чужих процессах обязан быть СНЯТ: вопрос «что грузит ЭТОТ "
                         "процесс», а не «увидит ли это клиент»")
        self.assertEqual(out["pc_agent"]["files"], 2)
        self.assertTrue(out["pc_agent"]["ok"])

    def test_an_unstattable_closure_file_makes_the_answer_untrustworthy(self):
        def boom(path):
            raise OSError("файла нет")

        out = run_mod.code_facts(
            closure_fn=lambda r, entries=None, cut=None: __import__("client_contour").Closure(
                frozenset({entries[0]}), frozenset(), True, "ok"),
            stat_fn=boom, lock_fn=lambda rec, lock, whose: rec, map_fn=lambda p: set())
        self.assertFalse(out["pc_agent"]["ok"])
        self.assertIn("не прочитан", out["pc_agent"]["reason"])

    def test_the_map_gap_rides_along_as_evidence_and_never_as_an_argument(self):
        """Улика едет рядом с вердиктом: «карта знает N из M» — то самое отставание в числах."""
        out = run_mod.code_facts(
            closure_fn=lambda r, entries=None, cut=None: __import__("client_contour").Closure(
                frozenset({entries[0], "io_utf8.py", "log_setup.py"}), frozenset(), True, "ok"),
            stat_fn=lambda p: type("S", (), {"st_mtime": 100.0})(),
            lock_fn=lambda rec, lock, whose: rec,
            map_fn=lambda p: {"pc_agent"} if os.path.basename(p) in
            ("pc_agent.py", "log_setup.py") else set())
        self.assertEqual(out["pc_agent"]["mapped"], 2)
        self.assertEqual(out["pc_agent"]["gap"], ["io_utf8.py"])
        # У демона имени в карте нет ВОВСЕ — и это не ноль, а отсутствие вопроса.
        self.assertIsNone(out["pc_orchestrator"]["mapped"])

    def test_the_live_closure_of_the_agent_is_wider_than_the_map_right_now(self):
        """ЖИВОЙ замер по коду репозитория, а не по фикстуре: если карта догонит замыкание —
        тест обязан покраснеть и заставить пересчитать числа, а не молча протухнуть."""
        import client_contour as cc
        cl = cc.closure(REPO, entries=("pc_agent.py",), cut=())
        self.assertTrue(cl.ok, cl.reason)
        # 03.09.2026: замыкание агента 5 → 7. Приехало ОДНОЙ строкой `import deploy_voice`
        # (голос подъёма мимо ворот), и за ней транзитивно `client_contour` — тот же признак,
        # которым судят ворота. Тест сработал ровно так, как задуман: не протух молча, а
        # заставил пересчитать. Числа таблицы О6 в шапке expectations_pc.py обновлены там же.
        # 06.09.2026: замыкание агента 7 → 8. Опять ОДНОЙ строкой — `import decision_waits`
        # (`pc_agent.py:58`, коммит `aa0de0e` «Открытые решения владельца не теряются в ночь»).
        # Растяжка сработала второй раз тем же способом: карта `_FILE_PROCESS_RULES` про
        # `decision_waits.py` не знает ни одним правилом, то есть отставание карты выросло с
        # 5 файлов до 6 — и это ровно тот факт, ради которого О6 считает ЗАМЫКАНИЕ, а не карту.
        # Числа таблицы О6 в шапке expectations_pc.py пересчитаны там же (7→8, 5→6).
        self.assertEqual(sorted(cl.files),
                         ["client_contour.py", "decision_waits.py", "deploy_voice.py",
                          "io_utf8.py", "log_setup.py", "pc_agent.py", "proc_identity.py",
                          "selfupdate_gate.py"])

    def test_the_live_closure_check_catches_a_moved_tree(self):
        """ОТРИЦАТЕЛЬНЫЙ: растяжка обязана краснеть на ЛЮБОМ сдвиге замыкания и после пересчёта.

        Кормим тем же прибором (`client_contour.closure`), но с ДРУГОЙ входной точкой — замыкание
        обязано отличаться от списка агента. Сравнение остаётся посписочным и полным: и лишний
        файл, и пропавший роняют его одинаково."""
        import client_contour as cc
        agent = sorted(cc.closure(REPO, entries=("pc_agent.py",), cut=()).files)
        for name, moved in (("лишний файл", agent + ["чужой.py"]),
                            ("пропавший файл", agent[:-1]),
                            ("другая входная точка",
                             sorted(cc.closure(REPO, entries=("pc_orchestrator.py",),
                                               cut=()).files))):
            with self.subTest(name):
                self.assertNotEqual(agent, moved, "сдвиг «%s» не пойман" % name)

    def test_the_run_says_it_every_single_pass(self):
        """п.3 задания: расхождение говорится КАЖДЫЙ виток, пока живо, — и попадает в состояние
        на диск, а не только в stdout, который у задачи Планировщика уходит в никуда."""
        with tempfile.TemporaryDirectory() as d:
            os.environ["CC_EXPECT_PC_DIR"] = d
            try:
                for _ in range(2):
                    out = run_mod.run(dry=True, getter=lambda st: {"ok": False, "items": []})
                    self.assertEqual([r["name"] for r in out["code"]], list(ex.CODE_WATCHED))
                    for r in out["code"]:
                        self.assertIn(r["state"],
                                      (ex.CODE_FRESH, ex.CODE_STALE, ex.CODE_UNKNOWN))
            finally:
                os.environ.pop("CC_EXPECT_PC_DIR", None)


class TestVersionNewsNotAlarm(unittest.TestCase):
    """ОБНОВЛЕНИЕ ВЕРСИИ ГОВОРИТ НОВОСТЬЮ, А НЕ АВАРИЕЙ (задача 229, 05.09.2026).

    Предмет — ТРИ РАЗНЫХ СОСТОЯНИЯ ОДНОГО РАСХОЖДЕНИЯ, у каждого свой голос:
      НОВОСТЬ        — на диске новее, срок ожидания не вышел: обновление ожидается;
      ПОДТВЕРЖДЕНИЕ  — процесс перезапустился на новую версию (до правки не звучало ВОВСЕ);
      ТРЕВОГА        — срок вышел, обновления нет: названы ожидание, срок и что известно о причине.

    ПРЕДСМЕРТНЫЙ ВЗГЛЯД ЗАДАНИЯ ДЕРЖИТСЯ ЗДЕСЬ: провал этой правки выглядел бы как смягчённая
    вместе с новостью тревога — восьмичасовой затык 03.09 проехал бы мимо владельца тихой строкой.
    Поэтому у набора два конца: «штатное обновление НЕ ТРЕВОЖИТ» и «настоящий затык ГРОМОК», и
    ослабить один, не покраснев другим, нельзя."""

    def setUp(self):
        self.cfg = ex.config({})
        # Срок ожидания в конфиге — ровно тот, что объявлен измерением, а не подогнанный под тест.
        self.wait = self.cfg["code_wait"]

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1 ЗАДАНИЯ ──────────────────────────────────────────────────────────
    def test_a_normal_update_raises_no_alarm_and_does_produce_a_confirmation(self):
        """«Обновление прошло штатно — тревоги НЕТ, а подтверждение ЕСТЬ.»

        Живой сюжет ночи 05.09: коммит лёг, демон обновился ближайшим витком (замер: медиана
        полного окна доставки 222 с). ДВЕ половины проверяются вместе, потому что порознь каждая
        зеленела бы и при сломанной другой: молчащий слой прошёл бы первую, шумящий — вторую."""
        started = NOW - 186000.0
        stale = code_facts_of(pc_agent=code_fact(newest_ago=120.0, started_ago=186000.0))
        key = "o6c|pc_agent|%d" % int(started)
        kinds = [v["kind"] for v in ex.verdict(stale, self.cfg) if str(v["kind"]).startswith("o6")]
        self.assertNotIn("o6_pc_code_late", kinds,
                         "коммит двухминутной давности тревогой НЕ является: демон обновится сам")
        self.assertIn("o6_pc_code_stale", kinds, "и молчать тоже нельзя — это новость")

        # ...через 222 с (медиана измеренного окна) демон передал эстафету: процесс СВЕЖ.
        after = code_facts_of(now=NOW + 222.0,
                              pc_agent=code_fact(now=NOW + 222.0, newest_ago=342.0,
                                                 started_ago=1.0))
        self.assertEqual(ex.code_state("pc_agent", after, self.cfg, NOW + 222.0)[0], ex.CODE_FRESH)
        self.assertEqual(ex.closures(after, self.cfg, [key]), [key],
                         "перезапуск на свежий код обязан ЗАКРЫТЬ эпизод")
        # ПОДТВЕРЖДЕНИЕ — то, чего не было вовсе: закрытие обязано СКАЗАТЬ про обновление версии.
        text = ex.render_close(key, "ПК",
                               ex.code_close_detail(key, after, self.cfg, NOW + 222.0))
        self.assertIn("ОБНОВЛЕНИЕ СОСТОЯЛОСЬ", text)
        self.assertIn("работает на новой версии", text)
        self.assertIn("было воплощение от", text)     # «с какой…
        self.assertIn("стало от", text)               # …на какую» — обе точки названы
        for word in ("🔔", "🚨", "СТАРЫЙ КОД", "не работает"):
            self.assertNotIn(word, text, "подтверждение — хорошая новость и звучать обязана так")

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2 ЗАДАНИЯ ──────────────────────────────────────────────────────────
    def test_an_update_that_never_happened_within_the_deadline_does_raise_the_alarm(self):
        """«Обновление не случилось за назначенный срок — тревога ЕСТЬ.»

        ЖИВОЙ СЛУЧАЙ, А НЕ ВЫДУМАННЫЙ: 03.09.2026 самообновление стояло 8.3 ч (дерево грязное),
        худшее ожидание коммита — 487.1 мин. Именно он обязан звучать бедой; если после смягчения
        новости эта проверка зеленеет молчанием — правка неверна, и тест обязан это поймать."""
        waited = 487.1 * 60.0                                   # ЖИВОЕ число затыка 03.09
        f = code_facts_of(pc_agent=code_fact(newest_ago=waited, started_ago=waited + 3600.0),
                          su=None)
        f["su"] = {"ok": True, "kind": "dirty", "at": NOW - waited,
                   "what": "2026-09-04 03:31:19 ERROR ... дерево ГРЯЗНОЕ ... pc_orchestrator.py"}
        v = [x for x in ex.verdict(f, self.cfg) if x["kind"] == "o6_pc_code_late"]
        self.assertEqual(len(v), 1, "восьмичасовой затык ОБЯЗАН быть тревогой, а не новостью")
        self.assertEqual(v[0]["key"], "o6l|pc_agent|%d" % int(NOW - waited - 3600.0))
        text = ex.render(v[0])
        self.assertIn("🚨", text)                                # громко
        self.assertIn("НЕ СЛУЧИЛОСЬ", text)
        self.assertIn("8 ч 7 мин", text)                        # сколько ждём — числом
        self.assertIn("срок был 90 мин", text)                  # какой был срок — числом
        self.assertIn("дерево", text)                           # что известно о причине
        # И запрет владельца переживает повышение громкости: ветка перезапусков не заводит.
        self.assertIn("рестарта не делаю", text)
        for word in ("перезапускаю", "перезапущу", "рестартую"):
            self.assertNotIn(word, text, "наблюдатель ничего не перезапускает — запрет владельца")

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3 ЗАДАНИЯ ──────────────────────────────────────────────────────────
    def test_an_unreadable_source_says_unknown_and_never_says_fine(self):
        """«Источник состояния недоступен — говорится «неизвестно», а не «в порядке».»

        Проверяются ОБА источника этой ветки, потому что недоступны они порознь: замыкание (без
        него не судится расхождение) и самообновление (без него не называется причина)."""
        # 1. Источник расхождения слеп → пометка СТОИТ, и слово сказано.
        blind = code_facts_of(pc_agent=code_fact(ok=False, reason="каталог не читается",
                                                 newest_ago=None))
        state, info = ex.code_state("pc_agent", blind, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_UNKNOWN)
        kinds = [v["kind"] for v in ex.verdict(blind, self.cfg)]
        self.assertIn("o6_pc_code_unknown", kinds)
        self.assertNotIn("o6_pc_code_stale", kinds)
        for word in ("в порядке", "всё хорошо", "обновление ожидается"):
            self.assertNotIn(word, ex.render({"kind": "o6_pc_code_unknown", "name": "pc_agent",
                                              "why": info.get("why")}))
        # 2. Источник ПРИЧИНЫ слеп, а расхождение просрочено → тревога звучит, причина названа
        #    незнанием. Молчания здесь нет ни на одной дороге.
        for su, expect in ((None, ex.SU_BLIND),
                           ({"ok": False, "reason": "FileNotFoundError"}, ex.SU_BLIND),
                           ({"ok": True, "kind": None}, ex.SU_QUIET)):
            f = code_facts_of(pc_agent=code_fact(newest_ago=7200.0, started_ago=11000.0))
            if su is not None:
                f["su"] = su
            self.assertEqual(ex.su_state(f)[0], expect)
            v = [x for x in ex.verdict(f, self.cfg) if x["kind"] == "o6_pc_code_late"]
            self.assertEqual(len(v), 1, "просрочка обязана звучать и при слепом источнике причины")
            text = ex.render(v[0])
            self.assertIn("НЕИЗВЕСТНА" if expect is ex.SU_QUIET else "не читается", text)
            for word in ("в порядке", "всё хорошо"):
                self.assertNotIn(word, text)

    # ── ОСТАЛЬНЫЕ ГАРАНТИИ ────────────────────────────────────────────────────────────────────
    def test_the_news_voice_carries_no_alarm_words_at_all(self):
        """п.3: новость звучит СПОКОЙНО. Список запрещённых слов — дословно прежний заголовок и
        его родня: до правки владелец получал «🔔 процесс несёт СТАРЫЙ КОД» на любое расхождение,
        включая минутное, и читал это ошибкой."""
        f = code_facts_of(pc_agent=code_fact(newest_ago=300.0, started_ago=186000.0))
        v = [x for x in ex.verdict(f, self.cfg) if x["kind"] == "o6_pc_code_stale"][0]
        text = ex.render(v)
        for word in ("🚨", "СТАРЫЙ КОД", "НЕ СЛУЧИЛОСЬ", "упал", "умер", "не работает",
                     "авария", "сбой,"):
            self.assertNotIn(word, text, "новость не смеет звучать аварией: %s" % word)
        self.assertIn("подхватит её ближайшим витком", text)
        self.assertIn("это штатный ход, а не сбой", text)
        # Числа второй оси названы ОБА, иначе «подождём» не проверить.
        self.assertIn("срок ожидания 90 мин", text)
        # И старая улика на месте: смягчение голоса не украло ни одного факта.
        self.assertIn("замыкание импортов", text)
        self.assertIn("рестарта не делаю", text)

    def test_the_same_gap_grows_from_news_into_alarm_and_says_so_once_each(self):
        """п.5: одно состояние — одно сообщение. РОВНО ОДНА новость и РОВНО одна тревога на весь
        путь расхождения, а не заметка каждые десять минут и не тишина после первой."""
        started, newest = NOW - 186000.0, NOW - 60.0
        said, kinds = [], []
        for i in range(40):                                   # 40 тиков по 10 мин = 6 ч 40 мин
            now = NOW + i * 600.0
            fact = code_fact(now=now, newest_ago=now - newest, started_ago=now - started)
            f = code_facts_of(now=now, pc_agent=fact)
            f["su"] = {"ok": True, "kind": "gate", "what": "unittest-гейт ПРОВАЛЕН"}
            v = [x for x in ex.verdict(f, self.cfg)
                 if str(x["kind"]).startswith("o6") and x.get("name") == "pc_agent"]
            self.assertEqual(len(v), 1, "тик %d промолчал при живом расхождении" % i)
            kinds.append(v[0]["kind"])
            if v[0]["key"] not in said:
                said.append(v[0]["key"])
        self.assertEqual(said, ["o6c|pc_agent|%d" % int(started),
                                "o6l|pc_agent|%d" % int(started)],
                         "владелец обязан получить РОВНО две строки: новость, потом тревогу")
        self.assertEqual(kinds[0], "o6_pc_code_stale")
        self.assertEqual(kinds[-1], "o6_pc_code_late")
        # Перелом ровно на измеренном сроке: 90 мин ожидания = 9 тиков наблюдателя.
        flip = kinds.index("o6_pc_code_late")
        self.assertEqual(flip, 9, "голос обязан меняться на 90-й минуте ожидания, а не раньше")
        self.assertNotIn("o6_pc_code_stale", kinds[flip:], "назад в новость тревога не отыгрывает")

    def test_a_red_gate_and_an_unnamed_cause_are_different_news(self):
        """п.6: «не обновился, потому что гейт красный» и «причина неизвестна» — разные новости, и
        вторая ОПАСНЕЕ. Замер 05.09: у гейта 23 живых отказа в логе, и это НАЗВАННАЯ причина."""
        def alarm(su):
            f = code_facts_of(pc_agent=code_fact(newest_ago=7200.0, started_ago=11000.0))
            f["su"] = su
            return ex.render([x for x in ex.verdict(f, self.cfg)
                              if x["kind"] == "o6_pc_code_late"][0])

        red = alarm({"ok": True, "kind": "gate", "why": "гейт самообновления провален",
                     "what": "unittest-гейт ПРОВАЛЕН (6700489→f709d68)"})
        quiet = alarm({"ok": True, "kind": None})
        self.assertIn("причина НАЗВАНА механизмом: гейт красный", red)
        self.assertIn("f709d68", red, "названная причина обязана приехать УЛИКОЙ, а не словом")
        self.assertIn("ПРИЧИНА НЕИЗВЕСТНА, и это хуже названного отказа", quiet)
        self.assertNotEqual(red, quiet, "две разные новости не смеют звучать одинаково")
        self.assertNotIn("гейт красный", quiet)

    def test_the_deadline_is_the_measured_rhythm_and_not_a_round_guess(self):
        """п.4: срок взят из ИЗМЕРЕННОГО ритма витка. Тест держит связь числа с замером: 90 мин
        обязаны перекрывать измеренный максимум доставки (2331.5 с), объявленный самим кодом
        потолок TASK_TIMEOUT + POLL_SEC + худший гейт = 3347 с И — главное — худшее ЗАКОННОЕ
        ожидание, собранное из ритма САМОГО витка: самый длинный измеренный виток 3927 с (n=470
        строк демона «виток растянулся», 30.7 суток) + худший гейт 587.1 = 4514.1 с. Именно
        последнее и есть «срок из измеренного ритма витка». Уронят порог ниже — покраснеет."""
        self.assertEqual(ex.CODE_WAIT_DEFAULT, 90.0)
        self.assertEqual(self.wait, 5400.0)
        measured_max, code_ceiling = 2331.5, 2700.0 + 60.0 + 587.1
        self.assertGreater(self.wait, measured_max * 2,
                           "срок обязан лежать выше измеренного максимума с запасом")
        self.assertGreater(self.wait, code_ceiling,
                           "срок обязан перекрывать потолок, объявленный самим кодом демона")
        # РИТМ ВИТКА, замер 05.09: объявленный потолок витка (2760 с) измерением ПЕРЕКРЫТ —
        # 3927 > 2760. Срок обязан стоять над худшим законным ожиданием, а не над объявленным.
        longest_turn, worst_gate = 3927.0, 587.1
        self.assertGreater(longest_turn, 2700.0 + 60.0,
                           "измеренный виток длиннее объявленного потолка — потолок это ПОЛ")
        self.assertGreater(self.wait, longest_turn + worst_gate,
                           "срок обязан перекрывать самый длинный ИЗМЕРЕННЫЙ виток плюс гейт")
        self.assertEqual(ex.TASK_TIMEOUT_SEC, 2700.0, "потолок задачи — часть основания срока")
        # КОНТРФАКТ ИЗМЕРЕНИЯ: ни одна из 69 законных доставок в него не попадает (макс 38.9 мин),
        # то есть ложных тревог на живом корпусе ноль.
        for legit in (56.9, 222.0, 2077.4, measured_max):
            f = code_facts_of(pc_agent=code_fact(newest_ago=legit, started_ago=legit + 90000.0))
            self.assertFalse(ex.code_state("pc_agent", f, self.cfg, NOW)[1]["late"],
                             "законная доставка %.1f с не смеет звать тревогу" % legit)

    def test_zero_on_the_wait_axis_kills_the_alarm_and_keeps_the_news(self):
        """Объявленный откат ТОЛЬКО тревоги. Глушить обе разом нельзя: это вернуло бы прежнюю
        интонацию молчанием, а «сигнал не глушить» — прямой запрет задания."""
        off = ex.config({ex.CODE_WAIT_ENV: "0"})
        self.assertEqual(off["code_wait"], 0.0)
        f = code_facts_of(pc_agent=code_fact(newest_ago=86400.0, started_ago=90000.0))
        kinds = [v["kind"] for v in ex.verdict(f, off) if str(v["kind"]).startswith("o6")]
        self.assertNotIn("o6_pc_code_late", kinds)
        self.assertIn("o6_pc_code_stale", kinds, "новость обязана пережить откат тревоги")
        # А общий откат О6 по-прежнему убивает ветку целиком — прежнее поведение не тронуто.
        dead = ex.config({ex.CODE_MIN_ENV: "0"})
        self.assertEqual([v for v in ex.verdict(f, dead) if str(v["kind"]).startswith("o6")], [])

    def test_a_fresh_commit_by_someone_else_does_not_reset_our_wait(self):
        """САМАЯ ОПАСНАЯ ДЫРА ЭТОЙ ПРАВКИ, и найдена она ЖИВЫМ прогоном, а не рассуждением.

        Ожидание, считанное от САМОЙ СВЕЖЕЙ правки, обнуляется любым чужим коммитом в замыкание. У
        userbot в замыкании 67 файлов и правки идут по нескольку раз в час, поэтому такой счёт не
        дошёл бы до 90 минут НИКОГДА — процесс, не перезапускавшийся двое суток, вечно звучал бы
        спокойной новостью «подхватит ближайшим витком». Это ровно то смягчение, которое съедает
        настоящий отказ, и задание запрещает его прямым словом.

        ЖИВОЙ ЗАМЕР 05.09 06:04, который это и вскрыл: userbot запущен 03.09 06:02, отстал на
        2 сут 1 ч, а `now - newest` у него = 6 мин 15 с."""
        started = NOW - 177496.0                      # ЖИВОЙ userbot: запущен 03.09 06:02
        f = code_facts_of(userbot=dict(
            code_fact(newest_ago=375.0, started_ago=177496.0, files=67),
            # ...и самая РАННЯЯ невзятая правка — двухсуточной давности, ровно как в проде.
            since=NOW - 172800.0))
        state, info = ex.code_state("userbot", f, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_STALE)
        self.assertTrue(info["wait_exact"])
        self.assertAlmostEqual(info["waited"], 172800.0, places=3)
        self.assertTrue(info["late"], "двое суток без перезапуска — это ТРЕВОГА, а не новость")
        v = [x for x in ex.verdict(f, self.cfg)
             if x["kind"] == "o6_pc_code_late" and x["name"] == "userbot"]
        self.assertEqual(len(v), 1)
        self.assertIn("ЖДЁМ УЖЕ 2 сут 0 ч", ex.render(v[0]))

        # КОНТРФАКТ ТОЙ ЖЕ СТРОКОЙ: считай мы от `newest` — тревоги не было бы вовсе.
        blind = code_facts_of(userbot=code_fact(newest_ago=375.0, started_ago=177496.0, files=67))
        blind["code"]["userbot"].pop("since", None)
        state2, info2 = ex.code_state("userbot", blind, self.cfg, NOW)
        self.assertFalse(info2["wait_exact"])
        self.assertAlmostEqual(info2["waited"], 375.0, places=3)
        self.assertFalse(info2["late"], "контрфакт: по `newest` двухсуточный затык МОЛЧИТ")
        # ...и потому собственная глухота обязана быть НАЗВАНА в самой заметке.
        quiet = [x for x in ex.verdict(blind, self.cfg)
                 if str(x["kind"]).startswith("o6") and x["name"] == "userbot"][0]
        self.assertIn("ЗАНИЖАЮ", ex.render(dict(quiet, kind="o6_pc_code_late", waited=375.0,
                                                wait_limit=self.wait, wait_exact=False)))

    def test_the_hands_measure_since_from_the_earliest_untaken_edit(self):
        """РУКИ считают `since` тем же обходом и по тому же правилу: самая ранняя правка, которая
        НОВЕЕ момента запуска. Взятые процессом файлы в счёт не идут — иначе ожидание считалось бы
        от кода, который давно в памяти."""
        stamps = {"pc_agent.py": 100.0, "io_utf8.py": 500.0}      # запуск будет между ними

        def stat_fn(path):
            return type("S", (), {"st_mtime": stamps.get(os.path.basename(path), 900.0)})()

        def lock_fn(rec, lock, whose):
            rec["started"], rec["pid"], rec["opened"] = 300.0, 7092, True
            return rec

        out = run_mod.code_facts(
            closure_fn=lambda r, entries=None, cut=None: __import__("client_contour").Closure(
                frozenset({entries[0], "io_utf8.py"}), frozenset(), True, "ok"),
            stat_fn=stat_fn, lock_fn=lock_fn, map_fn=lambda p: set())
        rec = out["pc_agent"]
        self.assertEqual(rec["newest"], 500.0)
        self.assertEqual(rec["since"], 500.0, "правка ДО запуска (100.0) ожиданием не является")
        # Всё замыкание старше запуска → отставать не от чего, и `since` честно пуст.
        stamps["io_utf8.py"] = 200.0
        out2 = run_mod.code_facts(
            closure_fn=lambda r, entries=None, cut=None: __import__("client_contour").Closure(
                frozenset({entries[0], "io_utf8.py"}), frozenset(), True, "ok"),
            stat_fn=stat_fn, lock_fn=lock_fn, map_fn=lambda p: set())
        self.assertIsNone(out2["pc_agent"]["since"])

    def test_a_clock_skew_never_invents_an_overdue_update(self):
        """mtime «из будущего» (часы разъехались, файл принесён с другой машины) не смеет родить
        просрочку: `waited` зажат в ноль. Тревога на перекосе часов была бы ложной громкостью."""
        f = code_facts_of(pc_agent=code_fact(newest_ago=-3600.0, started_ago=186000.0))
        state, info = ex.code_state("pc_agent", f, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_STALE)
        self.assertEqual(info["waited"], 0.0)
        self.assertFalse(info["late"])

    def test_the_confirmation_never_fires_without_proven_freshness(self):
        """ПОДТВЕРЖДЕНИЕ — не вежливость, а факт: сказать «обновился» можно только на доказанной
        свежести. Слепота и живое расхождение обязаны молчать здесь обе."""
        key = "o6l|pc_agent|%d" % int(NOW - 186000.0)
        for why, f in (
            ("расхождение живо", code_facts_of(pc_agent=code_fact(newest_ago=60.0,
                                                                  started_ago=186000.0))),
            ("прибор слеп", code_facts_of(pc_agent=code_fact(ok=False, reason="каталог не читается"))),
        ):
            self.assertEqual(ex.closures(f, self.cfg, [key]), [], why)
            self.assertEqual(ex.code_close_detail(key, f, self.cfg, NOW), "", why)

    def test_every_o6_kind_is_declared_in_the_registry(self):
        """Новый род обязан стоять в `KINDS` и иметь СВОЙ заголовок: род без заголовка печатался бы
        безымянным «нарушение ожидания» — то есть громкость терялась бы молча."""
        self.assertIn("o6_pc_code_late", ex.KINDS)
        for kind in ("o6_pc_code_stale", "o6_pc_code_late", "o6_pc_code_unknown"):
            self.assertIn(kind, ex.NOTE_HEAD)
            self.assertNotEqual(ex.NOTE_HEAD[kind], "🔔 ожидание нарушено")
        self.assertNotEqual(ex.NOTE_HEAD["o6_pc_code_stale"], ex.NOTE_HEAD["o6_pc_code_late"])


class TestVersionNewsHands(unittest.TestCase):
    """РУКИ ВТОРОЙ ОСИ: причина берётся из ДОСЛОВНЫХ строк демона, и разбор кренится в громкость."""

    def test_the_named_causes_are_read_from_the_daemons_own_words(self):
        tail = "\n".join([
            "2026-09-04 05:41:30,415 INFO self-update: 65f2bbb→6700489 — гейт пройден, новый",
            "2026-09-04 06:36:58,147 ERROR self-update: unittest-гейт ПРОВАЛЕН (6700489→f709d68): x",
        ])
        out = run_mod.su_facts(tail=tail)
        self.assertTrue(out["ok"])
        self.assertEqual(out["kind"], "gate")
        self.assertIn("f709d68", out["what"])
        self.assertIsNotNone(out["at"], "у причины обязано быть время — иначе её не проверить")
        self.assertEqual(ex.su_state({"su": out})[0], ex.SU_GATE)

        dirty = run_mod.su_facts(tail="2026-09-04 03:31:19,403 ERROR self-update демона: дерево "
                                      "ГРЯЗНОЕ — авто-рестарт ЗАПРЕЩЁН; файлы: pc_orchestrator.py")
        self.assertEqual(dirty["kind"], "dirty")
        self.assertEqual(ex.su_state({"su": dirty})[0], ex.SU_DIRTY)

    def test_the_last_word_wins_even_when_it_is_the_quiet_one(self):
        """Механизм отказал, а потом обновился — отказ БОЛЬШЕ НЕ ПРИЧИНА. Иначе владелец получил бы
        вчерашнюю причину к сегодняшнему затыку, и это хуже честного «не знаю»."""
        tail = "\n".join([
            "2026-09-04 06:36:58,147 ERROR self-update: unittest-гейт ПРОВАЛЕН (a→b): x",
            "2026-09-04 16:08:14,707 INFO self-update: 6700489→2adfb02 — гейт пройден, новый",
        ])
        out = run_mod.su_facts(tail=tail)
        self.assertIsNone(out["kind"])
        self.assertEqual(ex.su_state({"su": out})[0], ex.SU_QUIET)

    def test_the_parse_leans_to_the_loud_side(self):
        """ЧЕСТНАЯ ЦЕНА РАЗБОРА. Демон перепишет формулировку лога — причина станет неназванной,
        а НЕ «в порядке»: сломавшийся разбор обязан усиливать сигнал, а не глушить его."""
        out = run_mod.su_facts(tail="2026-09-04 06:36:58,147 ERROR самообновление сломалось иначе")
        self.assertTrue(out["ok"], "файл прочитан — слепотой это не является")
        self.assertIsNone(out["kind"])
        self.assertEqual(ex.su_state({"su": out})[0], ex.SU_QUIET,
                         "непонятая строка обязана давать САМЫЙ ГРОМКИЙ исход, а не тихий")

    def test_a_missing_log_is_blindness_and_says_so(self):
        out = run_mod.su_facts(path=os.path.join(REPO, "нет-такого-файла-2026-09-05.log"))
        self.assertFalse(out["ok"])
        self.assertIn("FileNotFoundError", out["reason"])
        self.assertEqual(ex.su_state({"su": out})[0], ex.SU_BLIND)

    def test_the_live_run_carries_the_cause_fact_every_pass(self):
        """ЖИВОЙ прогон рук: факт причины кладётся ВСЕГДА, как и факт замыкания. Отсутствие раздела
        `su` означало бы, что тревога навсегда останется без причины и никто этого не заметит."""
        with tempfile.TemporaryDirectory() as d:
            os.environ["CC_EXPECT_PC_DIR"] = d
            try:
                st = run_mod.load_state()
                now = datetime.datetime.now().timestamp()
                facts = run_mod.snapshot(st, now, lambda s: {"ok": False, "items": []})
            finally:
                os.environ.pop("CC_EXPECT_PC_DIR", None)
        self.assertIn("su", facts)
        self.assertIsInstance(facts["su"], dict)
        self.assertIn(ex.su_state(facts)[0], (ex.SU_GATE, ex.SU_DIRTY, ex.SU_QUIET, ex.SU_BLIND))


class TestLockNameIsNeverCut(unittest.TestCase):
    """ИМЯ ЛОКА НЕ ОБРЕЗАЕТСЯ (05.09.2026). Живой дефект: сообщение владельцу говорило «лок
    pc_agent не прочитан: [Errno 2] No such file or directory: 'D:\\\\turbobaby-bot\\\\pc_» —
    имя файла кончалось на третьей букве. Механизм — НЕ склейка пути (путь собирался верно), а
    ОБРЕЗАНИЕ ПО ДЛИНЕ: `str(e)[:60]`, где 57 символов съедал сам путь через `repr` с удвоенными
    разделителями. Обрезаны были ВСЕ ЧЕТЫРЕ имени полосы, и два из них («pc_agent.lock» и
    «pc_orchestrator.lock») давали неразличимый огрызок «pc_»."""

    def setUp(self):
        self.cfg = ex.config()
        self.d = tempfile.mkdtemp(prefix="expect_pc_lockname_")

    def _err_for(self, path, whose):
        rec = {"pid": None, "opened": None, "started": None, "lock_mtime": None, "err": ""}
        run_mod._read_lock(rec, path, whose)
        return rec["err"]

    def test_every_watched_lock_names_its_file_whole(self):
        """ВСЕ ЧЕТЫРЕ имени, а не то одно, на котором дефект заметили."""
        for name, lock in run_mod.CODE_LOCKS.items():
            err = self._err_for(os.path.join(self.d, os.path.basename(lock)), name)
            self.assertIn(os.path.basename(lock), err,
                          "имя лока «%s» не доехало до владельца целиком" % name)
            self.assertIn("не прочитан", err, "третий исход обязан говориться словами")
            self.assertNotIn("D:\\\\", err, "путь через repr снова съедает длину сообщения")

    def test_a_longer_name_survives_the_very_same_cut(self):
        """ПРЕДСМЕРТНЫЙ ВЗГЛЯД ЗАДАНИЯ: правильное имя, вписанное строкой рядом с битым, спасло бы
        только сегодняшние четыре, а следующий процесс с ДЛИННЫМ именем сломался бы ровно так же.
        Имя берётся у пути одним куском и стои́т ДО среза — поэтому переживает любую длину."""
        long_name = ("pc_orchestrator_secondary_watchdog_of_the_watchdog"
                     "_and_of_the_agent_and_of_the_userbot.lock")
        self.assertGreater(len(long_name), max(60, run_mod.ERR_TAIL),
                           "образец обязан быть длиннее ЛЮБОГО среза в этом файле")
        err = self._err_for(os.path.join(self.d, long_name), "процесс_с_очень_длинным_именем")
        self.assertIn(long_name, err)

    def test_the_reason_is_cut_but_the_name_is_not(self):
        """Режется ПРИЧИНА, и её потеря диагноза не отнимает: имя файла уже названо."""
        class Verbose(OSError):
            def __str__(self):
                return "причина " * 40
        rec = {"pid": None, "opened": None, "started": None, "lock_mtime": None, "err": ""}
        try:
            raise Verbose()
        except OSError as e:
            rec["err"] = "лок %s (файл %s) не прочитан: %s" % (
                "pc_agent", "pc_agent.lock", run_mod._why_short(e, "неважно"))
        self.assertIn("pc_agent.lock", rec["err"])
        self.assertLessEqual(len(rec["err"].split("не прочитан: ")[1]), run_mod.ERR_TAIL)

    def test_the_lock_name_has_exactly_one_source_in_the_module(self):
        """ОДИН ИСТОЧНИК ИМЕНИ. Литерал «*.lock» живёт в модуле рук РОВНО в `LOCK_FILES` и нигде
        больше: пока копий несколько, они расходятся молча, а разошедшаяся копия звучит у владельца
        как слепота прибора, а не как опечатка."""
        with open(os.path.join(REPO, "expectations_pc_run.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and n.value.endswith(".lock") and "\n" not in n.value and " " not in n.value]
        self.assertEqual(sorted(names), sorted(run_mod.LOCK_FILES.values()),
                         "имя лока написано в модуле дважды — источник больше не один")
        # И все три потребителя берут путь у него, а не собирают сами.
        self.assertEqual(run_mod.MOD_LOCK_FILE, run_mod.lock_path("moderation_bot"))
        for name, (_product, lock) in run_mod.KID_FILES.items():
            self.assertEqual(lock, run_mod.lock_path(name))
        self.assertEqual(run_mod.CODE_LOCKS,
                         {n: run_mod.lock_path(n) for n in run_mod.LOCK_FILES})
        self.assertEqual(sorted(run_mod.CODE_LOCKS), sorted(ex.CODE_WATCHED),
                         "наблюдаемых и локов обязано быть поровну")

    # ── ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА ЗАДАНИЯ (п.7), по одному на исход ──────────────────────────
    def test_no_lock_at_all_keeps_the_third_outcome_and_the_mark(self):
        """ЛОКА НЕТ ПО-НАСТОЯЩЕМУ → «не смог посчитать» остаётся отдельным исходом, пометка
        СТАВИТСЯ и не закрывается. Честность наблюдателя правкой не ослаблена."""
        err = self._err_for(os.path.join(self.d, "pc_agent.lock"), "pc_agent")
        f = code_facts_of(pc_agent=code_fact(newest_ago=60.0, started_ago=None,
                                             opened=None, pid=None, err=err))
        state, info = ex.code_state("pc_agent", f, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_UNKNOWN)
        self.assertIn("не добыт", info["why"])
        self.assertIn("pc_agent.lock", info["why"], "владелец обязан узнать, КАКОЙ файл искать")
        notes = [v for v in ex.verdict(f, self.cfg) if v["name"] == "pc_agent"]
        self.assertEqual([v["kind"] for v in notes], ["o6_pc_code_unknown"])
        self.assertEqual(ex.closures(f, self.cfg, [notes[0]["key"]]), [],
                         "слепота выздоровлением не является — эпизод не закрывается")

    def test_a_lock_with_a_fresh_process_takes_the_mark_off(self):
        """ЛОК ЕСТЬ И ПРОЦЕСС СВЕЖИЙ → замыкание СЧИТАЕТСЯ, и пометка СНИМАЕТСЯ."""
        lock = os.path.join(self.d, "pc_agent.lock")
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("%d\n" % os.getpid())
        rec = {"pid": None, "opened": None, "started": None, "lock_mtime": None, "err": ""}
        run_mod._read_lock(rec, lock, "pc_agent")
        self.assertEqual(rec["err"], "", "живой лок обязан читаться без единой жалобы")
        self.assertEqual(rec["pid"], os.getpid())
        self.assertIs(rec["opened"], True)
        f = code_facts_of(pc_agent=code_fact(newest_ago=7200.0, started_ago=60.0))
        self.assertEqual(ex.code_state("pc_agent", f, self.cfg, NOW)[0], ex.CODE_FRESH)
        self.assertEqual(ex.closures(f, self.cfg, ["o6u|pc_agent"]), ["o6u|pc_agent"])

    def test_a_lock_with_a_stale_process_still_raises_the_mark(self):
        """ЛОК ЕСТЬ И ПРОЦЕСС СТАРЫЙ → пометка ставится ПО ДЕЛУ, а не по слепоте прибора."""
        f = code_facts_of(pc_agent=code_fact(newest_ago=60.0, started_ago=186000.0))
        state, info = ex.code_state("pc_agent", f, self.cfg, NOW)
        self.assertEqual(state, ex.CODE_STALE)
        self.assertGreater(info["behind"], 0)
        notes = [v for v in ex.verdict(f, self.cfg) if v["name"] == "pc_agent"]
        self.assertEqual(len(notes), 1)
        self.assertIn(notes[0]["kind"], ("o6_pc_code_stale", "o6_pc_code_late"))
        self.assertEqual(ex.closures(f, self.cfg, [notes[0]["key"]]), [])

    def test_the_live_locks_either_read_or_name_themselves(self):
        """ЖИВОЙ замер по боевым файлам: какой бы из четырёх процессов сейчас ни лежал, отказ обязан
        называть свой файл. Тест не требует, чтобы процессы были живы, — он требует, чтобы прибор
        не врал о своей способности их измерить."""
        for name, lock in run_mod.CODE_LOCKS.items():
            rec = {"pid": None, "opened": None, "started": None, "lock_mtime": None, "err": ""}
            run_mod._read_lock(rec, lock, name)
            if rec["err"]:
                self.assertIn(os.path.basename(lock), rec["err"])
            else:
                self.assertIsInstance(rec["pid"], int)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  О7 — ЦЕНА НАЗЫВАЕТСЯ КЛИЕНТУ ЧИСЛОМ (05.09.2026)
#
#  ВЕСЬ НАБОР СТОИТ НА ПОДМЕНЁННОМ ЖУРНАЛЕ, а не на боевом: боевой `logs/userbot_stderr.log`
#  здесь не читается и не пишется ни одной строкой, канал отправки подменён заглушкой, карточек
#  владельцу не уходит. Строки фикстур — ДОСЛОВНЫЕ из живого журнала 05.09 (гасилка — та самая,
#  что молчала семь часов; пересчёт — форма `price_source.py:413` со словом «кепка:»).
# ══════════════════════════════════════════════════════════════════════════════════════════
PRICE_GATE_LINE = (
    "price_gate: ЦЕНА НЕ НАЗВАНА: записанное правило цены УСТАРЕЛО. ручки разошлись со слепком: "
    "H3 0.15\\u21920.0 Клиенту цена не ушла. Нужно пересобрать price_source.json и снять слепок "
    "ручек заново. (ПРАЙС-СЕТКА ПАРКА — цен не называем ни по одной модели)")
PRICE_QUOTE_LINE = (
    "price_source: NMAX → NMAX 2026-09-05 7сут → 400 = 400.0 x 1.0 (P1) x 1.0 [7-13]; "
    "кепка: полный месяц; опознано по листу через ключ «NMAX PHUKET 1234»")
# Форма-НЕУДАЧА того же префикса (`price_source.py:387`). Пересчётом она не является, и весь
# смысл разделителя «кепка:» в том, чтобы она не закрывала эпизод.
PRICE_QUOTE_FAIL = "price_source: источника нет — цену гасим (модель NMAX)"


def price_stamp_line(ts):
    """Отметка времени журнала продукта В ЖИВОМ ВИДЕ: «2026-09-05T09:23:25+00:00 | @кто | …».
    Именно у неё ветка берёт час — своей отметки у ценовых строк нет."""
    return "%s | @vladi_vk | VK | сколько стоит" % (
        datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
        .replace(microsecond=0).isoformat())


class TestPriceFacts(unittest.TestCase):
    """РУКИ О7: подменённый журнал → факт. Боевого журнала здесь нет ни в одном кейсе."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="expect_pc_price_")
        self.log = os.path.join(self.dir, "userbot_stderr.log")

    def write(self, lines):
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return self.log

    def test_gate_without_quote_after_it_is_read_as_mute_with_its_hour(self):
        """ЖИВОЙ СЛУЧАЙ 05.09 ЦЕЛИКОМ: пересчёт был вчера, гасилка сегодня, после неё пусто."""
        p = self.write([price_stamp_line(NOW - 86400), PRICE_QUOTE_LINE,
                        price_stamp_line(NOW - 3600), PRICE_GATE_LINE, PRICE_GATE_LINE,
                        "SUGGEST: черновик #588 → IPC (bot-режим)"])
        f = run_mod.price_facts(path=p)
        self.assertTrue(f["ok"])
        self.assertEqual((f["gates"], f["quotes"]), (2, 1))
        self.assertAlmostEqual(f["since"], NOW - 3600, delta=1.0)
        self.assertAlmostEqual(f["last_ok"], NOW - 86400, delta=1.0)
        self.assertTrue(f["exact"])
        self.assertIn("ручки разошлись со слепком", f["gate_why"])

    def test_quote_after_gate_resets_the_silence_to_nothing(self):
        """Пересчёт ПОСЛЕ гасилки обрывает молчание: ни начала, ни причины, ни счётчика гасилок."""
        p = self.write([price_stamp_line(NOW - 7200), PRICE_GATE_LINE,
                        price_stamp_line(NOW - 60), PRICE_QUOTE_LINE])
        f = run_mod.price_facts(path=p)
        self.assertTrue(f["ok"])
        self.assertIsNone(f["since"])
        self.assertIsNone(f["gate_why"])
        self.assertEqual(f["gates"], 0)
        self.assertAlmostEqual(f["last_ok"], NOW - 60, delta=1.0)

    def test_since_is_the_FIRST_gate_of_the_silence_not_the_last(self):
        """Владельцу нужен час, когда цена ЗАМОЛЧАЛА, а не час очередного гашения."""
        p = self.write([price_stamp_line(NOW - 20000), PRICE_QUOTE_LINE,
                        price_stamp_line(NOW - 10000), PRICE_GATE_LINE,
                        price_stamp_line(NOW - 100), PRICE_GATE_LINE])
        f = run_mod.price_facts(path=p)
        self.assertAlmostEqual(f["since"], NOW - 10000, delta=1.0)
        self.assertEqual(f["gates"], 2)

    def test_failed_price_source_line_is_not_a_quote(self):
        """ФОРМА-НЕУДАЧА ТОГО ЖЕ ПРЕФИКСА НЕ ЗАКРЫВАЕТ ЭПИЗОД. Без разделителя «кепка:» строка
        «источника нет — цену гасим» обнулила бы молчание тем самым следом, который его создаёт."""
        p = self.write([price_stamp_line(NOW - 5000), PRICE_GATE_LINE,
                        price_stamp_line(NOW - 50), PRICE_QUOTE_FAIL])
        f = run_mod.price_facts(path=p)
        self.assertEqual(f["quotes"], 0)
        self.assertAlmostEqual(f["since"], NOW - 5000, delta=1.0)

    def test_missing_log_is_not_ok_and_names_itself(self):
        """Файла нет → `ok=False` с названной причиной, а не пустой факт, читаемый как «тихо»."""
        f = run_mod.price_facts(path=os.path.join(self.dir, "нет-такого.log"))
        self.assertFalse(f["ok"])
        self.assertTrue(f["err"])

    def test_gate_above_the_first_stamp_says_its_hour_is_not_exact(self):
        """Гасилка выше первой отметки хвоста: час взят у соседа, и неточность ОБЪЯВЛЕНА."""
        p = self.write([PRICE_GATE_LINE, price_stamp_line(NOW - 900),
                        "SUGGEST: черновик #1 → IPC (bot-режим)"])
        f = run_mod.price_facts(path=p)
        self.assertFalse(f["exact"])
        self.assertAlmostEqual(f["since"], NOW - 900, delta=1.0)

    def test_broken_bytes_do_not_swallow_the_gate_line(self):
        """Кривой байт в соседней строке не смеет отнять у владельца гасилку."""
        with open(self.log, "wb") as f:
            f.write((price_stamp_line(NOW - 4000) + "\n").encode("utf-8"))
            f.write(b"\xff\xfe SUGGEST: \xc0\xe1\xf0\xe0\xea\xe0\xe4\xe0\xe1\xf0\xe0\n")
            f.write((PRICE_GATE_LINE + "\n").encode("utf-8"))
        f = run_mod.price_facts(path=self.log)
        self.assertTrue(f["ok"])
        self.assertEqual(f["gates"], 1)

    def test_the_live_log_is_either_read_or_names_its_refusal(self):
        """ЖИВОЙ ЗАМЕР по боевому пути — ТОЛЬКО ЧТЕНИЕ. Тест не требует, чтобы цена молчала или
        звучала: он требует, чтобы прибор не врал о своей способности прочитать журнал."""
        f = run_mod.price_facts()
        if f["ok"]:
            self.assertIsInstance(f["gates"], int)
            self.assertIsInstance(f["quotes"], int)
        else:
            self.assertIn("userbot_stderr.log", f["err"] + f["path"])


def price_facts_of(since_ago=None, quotes=1, gates=0, ok=True, err="", exact=True,
                   why=PRICE_GATE_LINE[len("price_gate: "):], now=NOW, last_ok_ago=None):
    """Факт О7 в том виде, в каком его кладут руки (`price_facts`)."""
    return {"ok": ok, "since": None if since_ago is None else now - since_ago,
            "gate_why": None if since_ago is None else why, "gates": gates, "quotes": quotes,
            "last_ok": None if last_ok_ago is None else now - last_ok_ago,
            "exact": exact, "path": "подменённый журнал", "err": err}


class TestPriceState(unittest.TestCase):
    """РЕШЕНИЕ О7: три исхода, и ни одна дорога не ведёт к «называется» иначе как через
    ПРОЧИТАННЫЙ след успешного пересчёта."""

    def setUp(self):
        self.cfg = ex.config({})

    def test_defaults_are_the_declared_numbers(self):
        """Порог — объявленный срок жизни кэша сторожа (30 мин), пол повтора — сутки."""
        self.assertEqual((self.cfg["price"], self.cfg["price_repeat"]), (1800.0, 86400.0))

    def test_mute_longer_than_the_threshold_is_a_verdict(self):
        f = {"now": NOW, "price": price_facts_of(since_ago=7 * 3600, gates=2, last_ok_ago=86400)}
        state, info = ex.price_state(f, self.cfg, NOW)
        self.assertEqual(state, ex.PRICE_MUTE)
        self.assertAlmostEqual(info["age"], 7 * 3600, delta=1.0)
        v = [x for x in ex.verdict(f, self.cfg) if x["kind"] == "o7_pc_price_mute"]
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["key"], "o7p|price")

    def test_quote_after_gate_is_silence_not_a_verdict(self):
        f = {"now": NOW, "price": price_facts_of(since_ago=None, quotes=519, last_ok_ago=60)}
        self.assertEqual(ex.price_state(f, self.cfg, NOW)[0], ex.PRICE_OK)
        self.assertEqual([x for x in ex.verdict(f, self.cfg)
                          if x["kind"] == "o7_pc_price_mute"], [])

    def test_unreadable_log_is_unknown_and_never_ok(self):
        """ГЛАВНЫЙ ЗАМОК: непрочитанный журнал — «неизвестно», а не «цена называется»."""
        f = {"now": NOW, "price": price_facts_of(ok=False, err="OSError: отказано")}
        state, info = ex.price_state(f, self.cfg, NOW)
        self.assertEqual(state, ex.PRICE_UNKNOWN)
        self.assertNotEqual(state, ex.PRICE_OK)
        self.assertIn("не прочитан", info["why"])
        self.assertEqual(ex.verdict(f, self.cfg), [])

    def test_no_fact_at_all_is_unknown(self):
        self.assertEqual(ex.price_state({"now": NOW}, self.cfg, NOW)[0], ex.PRICE_UNKNOWN)

    def test_empty_tail_without_any_price_line_is_unknown(self):
        """Прочитанная ПУСТОТА тоже «неизвестно»: бот мог сутки не получить ни одного вопроса
        о цене, и молчание журнала об этом не говорит ничего."""
        f = {"now": NOW, "price": price_facts_of(since_ago=None, quotes=0, gates=0)}
        state, info = ex.price_state(f, self.cfg, NOW)
        self.assertEqual(state, ex.PRICE_UNKNOWN)
        self.assertIn("судить не по чему", info["why"])

    def test_gate_younger_than_the_threshold_waits_and_does_not_say_ok(self):
        """Гасилка моложе порога — ещё не приговор, но и НЕ «называется»: сторож переспросит лист
        сам, а объявить цену выданной здесь значило бы соврать ровно в момент гашения."""
        f = {"now": NOW, "price": price_facts_of(since_ago=300, gates=1)}
        state, info = ex.price_state(f, self.cfg, NOW)
        self.assertEqual(state, ex.PRICE_UNKNOWN)
        self.assertIn("порог", info["why"])
        self.assertEqual(ex.verdict(f, self.cfg), [])

    def test_zero_threshold_kills_the_branch_entirely(self):
        """Ноль — ОБЪЯВЛЕННЫЙ откат: ветка умирает ДО чтения фактов."""
        cfg0 = ex.config({"EXPECT_PC_PRICE_MIN": "0"})
        f = {"now": NOW, "price": price_facts_of(since_ago=99999, gates=9)}
        self.assertEqual(ex.price_state(f, cfg0, NOW)[0], ex.PRICE_UNKNOWN)
        self.assertEqual(ex.verdict(f, cfg0), [])

    def test_closure_needs_a_proven_quote_and_nothing_else(self):
        """ЗАКРЫВАЕТ ТОЛЬКО ДОКАЗАННЫЙ ПЕРЕСЧЁТ. Ни ослепший журнал, ни ожидание порога, ни
        «гасилок стало меньше» эпизод не закрывают: молчание источника выздоровлением не является."""
        live = {"now": NOW, "price": price_facts_of(since_ago=7 * 3600, gates=2)}
        blind = {"now": NOW, "price": price_facts_of(ok=False, err="OSError: отказано")}
        young = {"now": NOW, "price": price_facts_of(since_ago=300, gates=1)}
        healed = {"now": NOW, "price": price_facts_of(since_ago=None, quotes=5, last_ok_ago=30)}
        self.assertEqual(ex.closures(live, self.cfg, ["o7p|price"]), [])
        self.assertEqual(ex.closures(blind, self.cfg, ["o7p|price"]), [])
        self.assertEqual(ex.closures(young, self.cfg, ["o7p|price"]), [])
        self.assertEqual(ex.closures(healed, self.cfg, ["o7p|price"]), ["o7p|price"])

    def test_the_note_carries_the_hour_the_reason_and_BOTH_branches(self):
        """Заметка — ИЗВЕЩЕНИЕ, а не карточка: час, причина словами сторожа, две ветки дословно
        и ни одной кнопки. И ни одного обещания что-то починить."""
        f = {"now": NOW, "price": price_facts_of(since_ago=7 * 3600, gates=2, last_ok_ago=86400)}
        note = ex.render(ex.verdict(f, self.cfg)[0])
        self.assertIn("ЦЕНА КЛИЕНТАМ НЕ НАЗЫВАЕТСЯ", note)
        self.assertIn("цена молчит с", note)
        self.assertIn("ручки разошлись со слепком", note)
        for branch in ex.PRICE_BRANCHES:
            self.assertIn(branch, note)
        self.assertIn("вернуть ручку", note)
        self.assertIn("price_snapshot_collect.py", note)
        self.assertIn("не раньше чем через сутки", note)
        self.assertIn(ex.TAIL, note)
        for forbidden in ("да/нет", "кнопк", "перезапущу", "верну ручку сам"):
            self.assertNotIn(forbidden, note)

    def test_inexact_hour_is_declared_in_the_note(self):
        f = {"now": NOW, "price": price_facts_of(since_ago=7 * 3600, gates=1, exact=False)}
        self.assertIn("ОСТОРОЖНО", ex.render(ex.verdict(f, self.cfg)[0]))

    def test_close_line_names_the_result_not_the_owners_branch(self):
        line = ex.render_close("o7p|price")
        self.assertIn("ЦЕНА СНОВА НАЗЫВАЕТСЯ", line)
        self.assertNotIn("ручк", line)          # какую ветку выбрал владелец — прибор не знает

    def test_the_predeath_look_snapshot_age_is_never_the_subject(self):
        """ПРЕДСМЕРТНЫЙ ВЗГЛЯД ЗАДАНИЯ, ЗАКРЫТЫЙ ДВАЖДЫ — поведением и текстом.

        Живой случай 05.09: слепку `price_source.json` было 3.0 суток при законных 14, то есть
        прибор, судящий по ВОЗРАСТУ, промолчал бы ровно там, где цена молчала семь часов. Здесь
        доказано, что возраст не участвует вовсе: тот же вердикт при сколь угодно свежем слепке,
        и ни слова о слепке в коде обеих функций решения."""
        f = {"now": NOW, "price": price_facts_of(since_ago=7 * 3600, gates=2),
             # Всё «здоровое» рядом: слепок свежайший, возраст законный, мост отвечает.
             "price_snapshot": {"snapshot_on": "2026-09-05", "age_days": 0.0, "max_age_days": 14.0}}
        self.assertEqual(ex.price_state(f, self.cfg, NOW)[0], ex.PRICE_MUTE)
        with open(EX_SRC, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        for fn in ("price_state", "_o7"):
            node = [n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == fn][0]
            body = ast.get_source_segment(src, node) or ""
            for word in ("age_days", "max_age_days", "snapshot_on", "price_source.json"):
                self.assertNotIn(word, body,
                                 "%s не смеет судить по возрасту слепка (%s)" % (fn, word))


class TestPriceHands(unittest.TestCase):
    """РУКИ О7 в прогоне: одна заметка на эпизод, повтор не раньше суток, витрина в итоге.

    ОТПРАВКА ПОДМЕНЕНА ЦЕЛИКОМ (`notifier`/`pulser` — заглушки), состояние живёт во временном
    каталоге (`CC_EXPECT_PC_DIR`): боевой `tmp/expect_pc/state.json` не читается и не пишется,
    владельцу не уходит ни одной строки."""

    def setUp(self):
        self.cfg = ex.config({})
        self.dir = tempfile.mkdtemp(prefix="expect_pc_price_run_")
        os.environ["CC_EXPECT_PC_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "CC_EXPECT_PC_DIR", None)
        self.noted = []
        self.addCleanup(setattr, run_mod, "snapshot", run_mod.snapshot)
        self.log = os.path.join(self.dir, "userbot_stderr.log")

    def _facts_at(self, now, since_ago, quotes=1, gates=2):
        base = {"now": now,
                "queue": {"ok": True, "rows": [], "dt": 1.0, "err": ""},
                "heartbeat": {"ok": True, "raw": hb_at(300.0, now), "err": ""},
                "silence": {"measured": True, "awake": 0.0, "why": ""},
                "busy": {"ok": True, "since": None, "limit": ex.TASK_TIMEOUT_SEC, "err": ""},
                "moderbot": None, "mod_silence": None, "kids": {},
                "trace": {"ok": True, "ts": now - 600.0, "line": "", "attempt": None, "err": ""},
                "client": {"ok": True, "sent": 0, "armed": 0, "attempted": 0, "total": 0,
                           "last_sent": None,
                           "pairs": {"ok": True, "sent": 0, "err": "", "last": None}},
                "client_unknown": {"measured": True, "awake": 0.0, "since": None},
                "kids_last": {"attempt": None, "sig": None}, "code": {}, "su": {"ok": False},
                "price": price_facts_of(since_ago=since_ago, quotes=quotes, gates=gates, now=now)}
        return base

    def _run(self, now, since_ago, quotes=1, gates=2):
        run_mod.snapshot = lambda st, n=None, getter=None: self._facts_at(now, since_ago,
                                                                         quotes, gates)
        out = run_mod.run(dry=False, now=now, getter=lambda status: {"ok": True, "items": []},
                          notifier=lambda t: (self.noted.append(t), True)[1],
                          pulser=lambda t: True)
        # ТОЛЬКО СВОИ КЛЮЧИ. Крафтовые факты этого набора не описывают О6 (там `code: {}`), и
        # её честное «замыкание не посчитано» едет в тот же список. Чужие ключи здесь не предмет:
        # набор О7 обязан краснеть от О7, а не от соседней ветки.
        for slot in ("notes", "repeats", "closed"):
            out[slot] = [k for k in (out.get(slot) or []) if k.startswith("o7p")]
        self.noted = [t for t in self.noted if "ЦЕНА" in t]
        return out

    def test_one_note_per_episode_then_a_repeat_after_a_day_then_a_closure(self):
        """ВЕСЬ ЖИЗНЕННЫЙ ПУТЬ ЭПИЗОДА ОДНИМ КЕЙСОМ, потому что порознь он и разошёлся бы:
        первая заметка → молчание через час → повтор через сутки → закрытие пересчётом."""
        out = self._run(NOW, since_ago=7 * 3600)
        self.assertEqual(out["notes"], ["o7p|price"])
        self.assertEqual(len(self.noted), 1)

        out = self._run(NOW + 3600, since_ago=8 * 3600)          # тот же эпизод час спустя
        self.assertEqual(out["notes"], [])
        self.assertEqual(out["repeats"], [])
        self.assertEqual(len(self.noted), 1, "повтор раньше суток — это шум, а не настойчивость")

        out = self._run(NOW + 86400 + 60, since_ago=31 * 3600)   # сутки выстояны
        self.assertEqual(out["repeats"], ["o7p|price"])
        self.assertEqual(len(self.noted), 2)

        out = self._run(NOW + 90000, since_ago=None, quotes=9, gates=0)
        self.assertEqual(out["closed"], ["o7p|price"])
        self.assertIn("ЦЕНА СНОВА НАЗЫВАЕТСЯ", self.noted[-1])

    def test_repeat_floor_of_zero_kills_the_repeat_but_not_the_first_note(self):
        cfg_off = {"EXPECT_PC_PRICE_REPEAT_MIN": "0"}
        old = dict(os.environ)
        os.environ.update(cfg_off)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        self.assertEqual(self._run(NOW, since_ago=7 * 3600)["notes"], ["o7p|price"])
        self.assertEqual(self._run(NOW + 200000, since_ago=60 * 3600)["repeats"], [])
        self.assertEqual(len(self.noted), 1)

    def test_the_showcase_line_lives_exactly_as_long_as_the_episode(self):
        """ВИТРИНА. Пока молчание живо — слово «молчит» с часом и причиной; вернулся пересчёт —
        тем же замером строка становится «называется». Снимать её руками нечем и не нужно."""
        out = self._run(NOW, since_ago=7 * 3600)
        self.assertEqual(out["price"], ex.PRICE_MUTE)
        self.assertIsNotNone(out["price_since"])
        self.assertIn("ручки разошлись со слепком", out["price_gate_why"])
        out = self._run(NOW + 90000, since_ago=None, quotes=9, gates=0)
        self.assertEqual(out["price"], ex.PRICE_OK)

    def test_blind_log_keeps_the_episode_open_and_says_unknown(self):
        """ОСЛЕПШИЙ ЖУРНАЛ НЕ ЗАКРЫВАЕТ ЭПИЗОД и не превращается в «называется»."""
        self._run(NOW, since_ago=7 * 3600)
        run_mod.snapshot = lambda st, n=None, getter=None: dict(
            self._facts_at(NOW + 3600, None), price=price_facts_of(ok=False, err="OSError: нет"))
        out = run_mod.run(dry=False, now=NOW + 3600,
                          getter=lambda status: {"ok": True, "items": []},
                          notifier=lambda t: (self.noted.append(t), True)[1], pulser=lambda t: True)
        self.assertEqual(out["price"], ex.PRICE_UNKNOWN)
        self.assertEqual(out["closed"], [])
        self.assertEqual(len(self.noted), 1)

    def test_the_instrument_reads_the_log_in_exactly_one_place(self):
        """ПРИБОР НЕ В ДВУХ ЭКЗЕМПЛЯРАХ: журнал продукта в слое читает ровно одна функция.
        Второй читатель разошёлся бы с первым порогом или разделителем — и владелец получил бы
        два разных ответа об одной цене."""
        with open(RUN_SRC, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        readers = [n.name for n in tree.body
                   if isinstance(n, ast.FunctionDef)
                   and "PRICE_GATE_MARK" in (ast.get_source_segment(src, n) or "")]
        self.assertEqual(readers, ["price_facts"])
        self.assertEqual(src.count("PRICE_LOG_FILE"), 2)     # объявление + единственное чтение


class TestQueueBlindIsTheThirdOutcome(TestRunHands):
    """О1, ТРЕТИЙ ИСХОД: отказ моста больше не ГАСИТ сторожа очереди (заведено 15.09.2026).

    ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗДЕСЬ ДВУСТОРОННИЙ, и обе стороны обязательны:
      · мост отвечает НЕ ok → прибор обязан сказать НЕИЗВЕСТНО и не промолчать;
      · мост отвечает нормально → прибор обязан МОЛЧАТЬ.
    Одна сторона без другой ничего не доказывает: прибор, который кричит всегда, проходит первую
    и проваливает вторую, а прежний (молчавший всегда) — ровно наоборот.

    БОЕВОЙ КАНАЛ И КАНАЛ ПРОВЕРКИ РАЗЛИЧАЮТСЯ: заметки собирает `self._note`, а живое имя
    `run_mod.send_note` подменено ловушкой `self.leaked` — наружу из проверки не уходит НИЧЕГО,
    и если завтра кто-то позовёт боевой канал мимо аргумента, тест покажет это списком."""

    TICK = 600.0                                     # период наблюдателя: задача Планировщика, 10 мин

    def setUp(self):
        super().setUp()
        # ЧАСЫ БОДРСТВОВАНИЯ ИНЪЕКТИРУЕМ: настоящие читают живую машину, и кейс краснел бы от
        # того, давно ли владелец её будил. Отдаём ИЗМЕРЕННЫЕ секунды, а не None: None — это
        # отдельный, третий исход самих часов, и он проверяется своим кейсом.
        self.awake = 10000.0
        self.addCleanup(setattr, run_mod, "awake_seconds", run_mod.awake_seconds)
        run_mod.awake_seconds = lambda: self.awake
        # ЛОВУШКА БОЕВОГО КАНАЛА. Возвращает True — как боевой: подмена, всегда молчащая False,
        # проверяла бы ветку fail-safe вместо утечки.
        self.leaked = []
        self.addCleanup(setattr, run_mod, "send_note", run_mod.send_note)
        run_mod.send_note = lambda text: (self.leaked.append(text), True)[1]
        self.addCleanup(setattr, run_mod, "heartbeat_facts", run_mod.heartbeat_facts)
        self._hb(hb_at(60))

    def _dead(self, status):
        """Мост ответил, но обмен не завершён — ЖИВОЙ класс отскока второго плеча, дословно."""
        raise RuntimeError("мост ответил, но обмен не завершён (BridgeTransportError): "
                           "второе плечо моста (echo) бросает обратно на наш же /exec")

    def _ticks(self, n, getter, start=0.0):
        """n наблюдений подряд с шагом периода наблюдателя. → список итогов прогонов.

        Оборот демона держим свежим НА КАЖДОМ тике: иначе О2 заговорит о молчании, которого мы
        не ставили, — и кейс про очередь краснел бы от соседней ветки."""
        outs = []
        for i in range(n):
            when = NOW + start + i * self.TICK
            self.awake = 10000.0 + start + i * self.TICK
            self._hb(hb_at(60, now=when))
            outs.append(run_mod.run(now=when, getter=getter, notifier=self._note))
        return outs

    # СУДИМ ТОЛЬКО СВОЮ ВЕТКУ. Слой держит семь ожиданий разом, и «весь слой молчит» — не то
    # утверждение, которое здесь проверяется: предмет кейсов — голос О1 о снимке очереди.
    @staticmethod
    def _o1u(outs):
        return [k for o in outs for k in o["notes"] if str(k).startswith("o1u|")]

    def _said(self):
        return [t for t in self.sent if "не вижу очереди полосы" in t]

    def _closed(self, outs):
        return [k for o in outs for k in o["closed"] if str(k).startswith("o1u|")]

    # ── сторона 1: признак выглядит правильным, результата нет ────────────────────────────────
    def test_bridge_not_ok_makes_the_watchman_say_unknown_and_not_be_silent(self):
        outs = self._ticks(8, self._dead)             # 7 шагов × 600 с = 4200 с > отсрочки 3648 с
        self.assertEqual([o["queue"] for o in outs], [ex.QUEUE_UNKNOWN] * 8)
        self.assertEqual(len(self._o1u(outs)), 1,
                         "один сигнал на эпизод — не на строку и не на оборот")
        self.assertEqual(len(self._said()), 1)
        said = self._said()[0]
        self.assertIn("не вижу очереди полосы", said)
        self.assertIn("причина последнего промаха", said)
        self.assertIn("BridgeTransportError", said, "причина обязана ехать в самой заметке")
        # НЕИЗВЕСТНОСТЬ — НЕ КРАСНОЕ: приговора очереди в тексте нет ни одного.
        for word in ("стои́т", "мёртв", "авария", "не работает"):
            self.assertNotIn(word, said.lower().replace("ё", "ё"))
        self.assertEqual(self.leaked, [], "из проверки наружу не ушло ничего")

    def test_the_verdict_is_not_a_violation_and_does_not_name_a_single_task(self):
        """Третий исход не превращается в нарушение: видов О1 о СТРОКАХ здесь нет ни одного."""
        outs = self._ticks(8, self._dead)
        kinds = [k for o in outs for k in o["notes"] if str(k).startswith("o1")]
        self.assertEqual(kinds, self._o1u(outs), "видов О1 о СТРОКАХ здесь быть не может: "
                                                 "строк мы не видели ни одной")
        # Тот же снимок глазами `queue_state`: «неизвестно», а НЕ «стои́т».
        self.assertNotIn(ex.QUEUE_STUCK, [o["queue"] for o in outs])

    # ── сторона 2: мост отвечает нормально — прибор обязан молчать ────────────────────────────
    def test_bridge_ok_keeps_the_watchman_silent(self):
        outs = self._ticks(8, self._getter([]))
        self.assertEqual((self._o1u(outs), self._said(), self.leaked), ([], [], []))
        self.assertEqual([o["queue"] for o in outs], [ex.QUEUE_OK] * 8)
        self.assertEqual(outs[-1]["queue_episodes"], 0, "слепоты не было — эпизодов ноль")

    # ── п.3: громкость отделена от вердикта ──────────────────────────────────────────────────
    def test_an_episode_shorter_than_the_delay_is_counted_but_never_shown(self):
        """Эпизод, погасший быстрее отсрочки, В СЧЁТ ИДЁТ, а владельцу не показывается.

        Это и есть требуемое разделение: по замеру 15.09 таких эпизодов шестнадцать в сутки, и
        показать их все значило бы провалить приёмку шумом."""
        short = self._ticks(3, self._dead)            # 1200 с слепоты — короче отсрочки
        self.assertEqual(self._o1u(short), [])
        self.assertEqual(short[-1]["queue_episodes"], 1, "посчитан")
        self.assertEqual(short[-1]["queue_misses"], 3)
        healed = self._ticks(1, self._getter([]), start=3 * self.TICK)
        self.assertEqual((self._said(), self.leaked), ([], []))
        self.assertEqual(healed[0]["queue_episodes"], 1, "память об эпизоде не стирается")
        self.assertEqual(healed[0]["queue_misses"], 0, "промахи подряд обнулены снимком")

    def test_a_second_episode_speaks_again_and_the_first_one_closes(self):
        """ОДИН сигнал на ЭПИЗОД, а не один на всю жизнь наблюдателя: новая слепота — новый ключ."""
        self._ticks(8, self._dead)
        self.assertEqual(len(self._said()), 1)
        back = self._ticks(1, self._getter([]), start=8 * self.TICK)
        self.assertEqual(len(self._closed(back)), 1, "снимок получен — эпизод закрыт доказанно")
        self.assertTrue(any("ожидание снова выполняется" in t for t in self.sent))
        again = self._ticks(8, self._dead, start=9 * self.TICK)
        self.assertEqual(len(self._o1u(again)), 1)
        self.assertEqual(again[-1]["queue_episodes"], 2)
        self.assertEqual(self.leaked, [])

    # ── контрфакт: со снятой правкой тот же вход даёт ДРУГОЙ ответ ────────────────────────────
    def test_counterfactual_without_the_patch_the_same_input_is_silent(self):
        """СНЯТАЯ ПРАВКА — это `_o1`, выходящий на `rows is None` без третьего исхода, то есть
        ровно тот код, что стоял до 15.09.2026. Тот же вход обязан дать ДРУГОЙ ответ."""
        blind = {"measured": True, "awake": 9999.0, "since": NOW - 9999.0,
                 "why": "мост ответил без ok на new", "episodes": 3, "misses": 4}
        f = dict(facts(ok=False, err="мост ответил без ok на new"), queue_blind=blind)
        self.assertEqual([v["kind"] for v in ex.verdict(f)], ["o1_pc_queue_unknown"])
        self.addCleanup(setattr, ex, "_o1_blind", ex._o1_blind)
        ex._o1_blind = lambda facts, cfg, now: []     # ← правка снята
        self.assertEqual(ex.verdict(f), [], "до правки тот же вход давал молчание")

    def test_zero_kills_the_voice_and_keeps_the_count(self):
        """Ноль в ручке — ОБЪЯВЛЕННЫЙ откат голоса. Счёт эпизодов руками он не трогает: память
        о слепоте дороже её громкости, и «тихо» не обязано значить «не считаем»."""
        blind = {"measured": True, "awake": 9999.0, "since": NOW - 9999.0,
                 "why": "мост молчит", "episodes": 3, "misses": 4}
        f = dict(facts(ok=False), queue_blind=blind)
        self.assertEqual(ex.verdict(f, ex.config({"EXPECT_PC_QBLIND_MIN": "0"})), [])
        outs = self._ticks(3, self._dead)
        self.assertEqual(outs[-1]["queue_episodes"], 1)

    def test_unmeasured_blindness_never_becomes_a_verdict(self):
        """«Не смог измерить» не превращается ни в приговор, ни в благополучие: пометки нет, а
        эпизод при этом ПОСЧИТАН (см. кейс выше). Третий исход есть и у самих часов."""
        for blind in ({"measured": False, "awake": None, "since": NOW, "why": "часов нет"},
                      {"measured": False, "awake": 0.0, "since": NOW, "why": "первое наблюдение"},
                      None, "мусор"):
            f = dict(facts(ok=False), queue_blind=blind)
            self.assertEqual(ex.verdict(f), [], repr(blind))

    def test_the_hands_count_the_episode_even_without_an_awake_clock(self):
        """Часов бодрствования нет → длину не измерить, но ФАКТ слепоты обязан быть посчитан."""
        st = {}
        got = run_mod.update_queue_blind(st, {"ok": False, "err": "мост молчит"}, None, NOW)
        self.assertEqual((got["measured"], got["episodes"], got["misses"]), (False, 1, 1))
        self.assertEqual(st["qblind"]["episodes"], 1)

    # ── п.3: у порога есть ЗАПИСЬ ЗАМЕРА и поведение при её протухании ────────────────────────
    def test_the_delay_is_measured_not_round_and_carries_its_measurement(self):
        self.assertAlmostEqual(ex.config({})["qblind"], 3648.0)
        for round_guess in (1800.0, 3600.0, 5400.0, 7200.0):
            self.assertNotAlmostEqual(ex.config({})["qblind"], round_guess)
        m = ex.QBLIND_MEASURE
        for field in ("at", "log", "episodes", "median", "max", "tick"):
            self.assertIn(field, m)
        # ЧИСЛО ОБЯЗАНО БЫТЬ ВЫВЕДЕНО ИЗ ЗАМЕРА, А НЕ СТОЯТЬ РЯДОМ С НИМ: максимум замкнутого
        # эпизода плюс один собственный оборот наблюдателя.
        self.assertAlmostEqual(ex.config({})["qblind"], m["max"] + m["tick"])
        self.assertGreater(m["max"], m["median"], "замер обязан нести и разброс")

    def test_a_stale_measurement_shouts_in_words_and_does_not_move_the_threshold(self):
        """ПОВЕДЕНИЕ ПРИ ПРОТУХШЕМ ЗАМЕРЕ: порог сам не меняется ни на секунду, а возраст
        основания едет СЛОВАМИ в той же заметке. Тихо подкрученное число хуже старого."""
        base = ex.qblind_measure_age(NOW)
        self.assertIsNotNone(base)
        fresh = NOW + 86400.0 * 5
        stale = NOW + 86400.0 * (ex.QBLIND_STALE_DAYS + 400)
        self.assertFalse(ex.qblind_measure_stale(fresh))
        self.assertTrue(ex.qblind_measure_stale(stale))
        blind = {"measured": True, "awake": 9999.0, "since": NOW, "why": "мост молчит",
                 "episodes": 1, "misses": 2}
        say = {}
        for name, when in (("fresh", fresh), ("stale", stale)):
            f = dict(facts(ok=False, now=when), queue_blind=blind)
            v = ex.verdict(f)[0]
            say[name] = ex.render(v)
            self.assertAlmostEqual(v["limit"], 3648.0, msg="порог не смеет двигаться сам")
        self.assertIn("ПЕРЕСНИМИ", say["stale"])
        self.assertNotIn("ПЕРЕСНИМИ", say["fresh"])
        self.assertIn(ex.QBLIND_MEASURE["at"], say["fresh"])

    def test_the_watchman_names_its_own_border_out_loud(self):
        """Границу прибора называет САМ прибор, а не только артефакт: в докстринге ветки сказано,
        чего она НЕ наблюдает. Без этого «молчит» и «не смотрит» снова станут одним словом."""
        doc = ex._o1_blind.__doc__ or ""
        for must in ("НЕ НАБЛЮДАЕТ", "ПОЧЕМУ молчит мост", "СОБСТВЕННУЮ СМЕРТЬ"):
            self.assertIn(must, doc)


if __name__ == "__main__":
    unittest.main()
