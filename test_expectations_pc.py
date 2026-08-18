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
import json
import os
import tempfile
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
         ok=True, err="", now=NOW):
    """Факт о модерботе ровно того вида, что отдают руки (`moderbot_facts`): mtime продукта,
    номер из лока, проба процесса и возраст запуска. Идеализированной схемы здесь нет."""
    return {"ok": ok, "mtime": (now - age) if ok else None, "pid": pid, "opened": opened,
            "started": started, "lock_mtime": None if started is None else started + skew,
            "err": err}


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

    def test_fresh_product_of_a_foreign_writer_is_never_work(self):
        """ЗАМОК ПРОТИВ ЛОЖНОГО ЗЕЛЁНОГО: файл общий. Свежий mtime при отсутствующем процессе
        значит «писал кто-то другой» (userbot черновиком, тренажёр сессией), а не «бот работает»."""
        f = mfacts(mod=modf(age=2.0, opened=False))
        st, info = ex.moderbot_state(f, self.cfg, NOW)
        self.assertEqual(st, ex.MOD_UNKNOWN)
        self.assertNotEqual(st, ex.MOD_OK)
        self.assertIn("писал не модербот", info["why"])
        self.assertEqual(ex.verdict(f, self.cfg), [], "незнание не приговор")

    def test_foreign_writer_becomes_a_violation_once_silence_accumulates(self):
        """Вторая половина того же замка: молчание не вечно. Чужая запись счётчик не обнуляет,
        поэтому через два наблюдения выходит честное нарушение — с названным состоянием процесса."""
        f = mfacts(mod=modf(age=2.0, opened=False), silence=silent_for(1200.0))
        self.assertEqual(ex.moderbot_state(f, self.cfg, NOW)[0], ex.MOD_IDLE)
        note = ex.render(ex.verdict(f, self.cfg)[0])
        self.assertIn("из лока в системе нет", note)
        self.assertIn("делал кто-то другой", note)

    def test_reused_pid_is_not_an_author(self):
        """Номер переиспользован Windows'ом: процесс есть, но запущен не тогда, когда написан лок."""
        f = mfacts(mod=modf(age=2.0, started=NOW - 30.0, skew=-400000.0))
        self.assertIs(ex.moderbot_writer(f), False)
        self.assertEqual(ex.moderbot_state(f, self.cfg, NOW)[0], ex.MOD_UNKNOWN)
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
            ("время записи не разобрано", mfacts(mod=dict(modf(), mtime="не время"))),
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
        st2 = {"mod": {"mtime": 1.0, "awake": 900.0, "silent": 300.0, "since": NOW - 900}}
        r = run_mod.update_mod_silence(st2, modf(ok=False), 1000.0, NOW)
        self.assertFalse(r["measured"], "продукт не прочитан — копить нечего и обнулять нечего")
        r = run_mod.update_mod_silence({"mod": {"mtime": 1.0, "awake": 5000.0, "silent": 300.0}},
                                       modf(), 10.0, NOW)
        self.assertFalse(r["measured"], "часы пошли назад — машина перезагрузилась")

    def test_step_is_capped_by_one_observation(self):
        st = {"mod": {"mtime": 7.0, "awake": 0.0, "silent": 0.0, "since": NOW - 99999}}
        r = run_mod.update_mod_silence(st, dict(modf(), mtime=7.0), 99999.0, NOW)
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
        """Живой формат: mtime продукта берётся stat'ом (база НЕ открывается), номер — из лока."""
        d = tempfile.mkdtemp(prefix="expect_pc_mod_")
        ipc, lock = os.path.join(d, "moderation_ipc.db"), os.path.join(d, "moderation_bot.lock")
        with open(ipc, "wb") as f:
            f.write(b"SQLite format 3\x00")
        with open(lock, "w", encoding="utf-8") as f:
            f.write("%d\n" % os.getpid())            # живой вид лока: номер и перевод строки
        m = run_mod.moderbot_facts(ipc, lock)
        self.assertTrue(m["ok"])
        self.assertEqual(m["pid"], os.getpid())
        self.assertTrue(m["opened"])
        self.assertIsNotNone(m["mtime"])
        self.assertIs(ex.moderbot_writer({"moderbot": m}), True)
        self.assertFalse(run_mod.moderbot_facts(ipc + ".нет", lock)["ok"])
        # Лока нет → продукт прочитан, но автор НЕ подтверждён: «неизвестно», не «работает».
        no_lock = run_mod.moderbot_facts(ipc, lock + ".нет")
        self.assertTrue(no_lock["ok"])
        self.assertIsNone(ex.moderbot_writer({"moderbot": no_lock}))
        # База не открывается ни одной веткой наблюдателя: инструмента для этого нет вовсе.
        with open(RUN_SRC, encoding="utf-8") as f:
            names = [a.name for n in ast.walk(ast.parse(f.read()))
                     if isinstance(n, ast.Import) for a in n.names]
        self.assertNotIn("sqlite3", names)


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

    def test_only_one_kid_of_three_has_an_instrument_and_this_is_said_aloud(self):
        """Двух детей из трёх здесь не судит НИКТО, и это сама новость, а не пустая графа."""
        rows = ex.kids_state(kfacts(), ex.config({}), NOW)
        judged = [k for k in rows if k["why"] != ex.KIDS_NO_INSTRUMENT]
        self.assertEqual([k["name"] for k in judged], [ex.KIDS_JUDGED])
        for k in rows:
            if k["name"] != ex.KIDS_JUDGED:
                self.assertEqual(k["state"], ex.MOD_UNKNOWN)
                self.assertNotEqual(k["state"], ex.MOD_IDLE, "третье состояние слито со вторым")


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
        """«Проверить не удалось» доезжает до строки третьим словом, а не вторым: свежая чужая
        запись в общем файле — это НЕ «работы нет» и НЕ «работает»."""
        f = kfacts(mod=modf(age=2.0, opened=False))
        rows = {k["name"]: k["state"] for k in ex.kids_state(f, self.cfg, NOW)}
        self.assertEqual(rows["moderation_bot"], ex.MOD_UNKNOWN)
        line = ex.render_kids(ex.kids_pulse_due(f, self.cfg, NOW)[1])
        self.assertIn("moderation_bot — %s" % ex.MOD_UNKNOWN, line)
        self.assertIn("писал не модербот", line)
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
                     "client_facts", "awake_seconds"):
            self.addCleanup(setattr, run_mod, name, getattr(run_mod, name))
        self.real_mod_facts = run_mod.moderbot_facts       # НАСТОЯЩЕЕ чтение файлов, не заглушка
        run_mod.busy_facts = lambda path=None: {"ok": True, "since": None,
                                                "limit": ex.TASK_TIMEOUT_SEC, "err": ""}
        run_mod.trace_facts = lambda path=None, attempt=None: {
            "ok": True, "ts": NOW - 600.0, "line": "", "attempt": attempt, "err": ""}
        run_mod.client_facts = lambda: {"ok": True, "sent": 0, "armed": 0, "attempted": 0,
                                        "total": 0, "last_sent": None,
                                        "pairs": {"ok": True, "sent": 0, "err": "", "last": None}}

    def _entity(self, age, author=False):
        """ПРОВЕРОЧНАЯ СУЩНОСТЬ — настоящие файлы, читаемые настоящими руками: продукт со своим
        mtime и лок со своим номером. Боевые `moderation_ipc.db` и `moderation_bot.lock` не
        тронуты, боевой модербот не поднят и не погашен.

        `author=True` — локом становится НАШ СОБСТВЕННЫЙ процесс (номер + его настоящее время
        запуска): только так проба авторства отвечает True по-честному, живым ядром, а не моком.
        `author=False` — номер, которого в системе нет: Windows не раздаёт номера, не кратные
        четырём."""
        db = os.path.join(self.dir, "moderation_ipc.db")
        lock = os.path.join(self.dir, "moderation_bot.lock")
        with open(db, "w", encoding="utf-8") as f:
            f.write("проверочная сущность, не боевая база")
        os.utime(db, (NOW - age, NOW - age))
        pid = os.getpid() if author else 999999
        with open(lock, "w", encoding="utf-8") as f:
            f.write(str(pid))
        born = run_mod.process_probe(pid)[1] if author else (NOW - age)
        if born:
            os.utime(lock, (born, born))
        real = self.real_mod_facts
        return lambda ipc=None, lock_=None, _r=real, _d=db, _l=lock: _r(_d, _l)

    def _run(self, awake, now):
        run_mod.heartbeat_facts = lambda path=None: {"ok": True, "raw": hb_at(300.0, now),
                                                     "err": ""}
        run_mod.awake_seconds = lambda: awake
        return run_mod.run(dry=False, now=now, getter=lambda status: {"ok": True, "items": []},
                           notifier=lambda t: (self.noted.append(t), True)[1],
                           pulser=lambda t: (self.pulsed.append(t), True)[1])

    def test_dead_kid_on_a_real_entity_is_published_and_not_dropped(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЦЕЛИКОМ, от файла до строки. Продукт проверочной сущности не
        двигался 50 минут, номер из лока в системе отсутствует — два наблюдения копят тишину,
        и периодическая строка обязана НАЗВАТЬ ребёнка неработающим, а не пропасть."""
        run_mod.moderbot_facts = self._entity(age=3000.0)
        self._run(awake=100000.0, now=NOW)                    # первое наблюдение: счётчик заведён
        out = self._run(awake=102000.0, now=NOW + 2000.0)     # второе: тишины накоплено 2000с
        self.assertEqual(dict((k["name"], k["state"]) for k in out["kids"])["moderation_bot"],
                         ex.MOD_IDLE)
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


if __name__ == "__main__":
    unittest.main()
