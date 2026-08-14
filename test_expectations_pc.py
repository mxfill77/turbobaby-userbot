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


def facts(rows=None, ok=True, hb=HB_LIVE, hb_ok=True, silence=None, busy=None, now=NOW, err=""):
    return {
        "now": now,
        "queue": {"ok": ok, "rows": list(rows or []), "dt": 1.0, "err": err},
        "heartbeat": {"ok": hb_ok, "raw": hb, "err": err},
        "silence": silence if silence is not None else {"measured": True, "awake": 0.0, "why": ""},
        "busy": busy,
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
                  busy={"task": "469", "since": NOW - 1900, "limit": 2700.0})
        self.assertEqual(ex.turn_state(f, ex.config({}), NOW)[0], ex.TURN_OK)
        self.assertEqual(ex.verdict(f), [])

    def test_declared_pass_answers_even_when_silence_is_not_measured(self):
        """ЖИВОЙ СЛУЧАЙ 11.08.2026, первый прогон наблюдателя: heartbeat отставал на 30 мин, потому
        что демон синхронно исполнял задачу 469. Тишина ещё не измерена (наблюдение первое), но
        штамп занятости — ПОЛОЖИТЕЛЬНЫЙ факт с потолком, и отвечать на него «неизвестно» значило бы
        прятать известное."""
        f = facts(hb=hb_at(1800), silence={"measured": False, "awake": None,
                                           "why": "прошлого наблюдения нет"},
                  busy={"task": "469", "since": NOW - 1700, "limit": 2700.0})
        st, info = ex.turn_state(f, ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_OK)
        self.assertEqual(info["busy"]["task"], "469")
        self.assertEqual(ex.verdict(f), [])
        # А тот же прогон БЕЗ штампа обязан честно сказать «неизвестно».
        self.assertEqual(ex.turn_state(dict(f, busy=None), ex.config({}), NOW)[0], ex.TURN_UNKNOWN)

    def test_pass_that_outlived_its_declared_budget_still_speaks(self):
        """ЗУБЫ ЦЕЛЫ: слепота ограничена сверху объявленным сроком + хвост оборота (55 мин)."""
        f = facts(hb=hb_at(4000), silence={"measured": True, "awake": 4000.0, "why": ""},
                  busy={"task": "469", "since": NOW - 3400, "limit": 2700.0})
        st, info = ex.turn_state(f, ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_SILENT)
        self.assertEqual(info["overdue"]["task"], "469")
        self.assertIn("свой kill не сработал", ex.render(ex.verdict(f)[0]))

    def test_dead_instance_stamp_blinds_no_longer_than_its_ceiling(self):
        """Штамп умершего экземпляра не оправдывает НИЧЕГО дольше 55 минут — иначе смерть посреди
        задачи выглядела бы работой вечно."""
        f = facts(hb=hb_at(99999), silence={"measured": True, "awake": 99999.0, "why": ""},
                  busy={"task": "353", "since": NOW - 86400, "limit": 2700.0})
        self.assertEqual(ex.turn_state(f, ex.config({}), NOW)[0], ex.TURN_SILENT)

    def test_sleep_of_the_machine_is_not_a_standstill(self):
        """Стенной возраст 9 часов, бодрствования — 3 минуты: это сон ПК, а не вставший демон."""
        f = facts(hb=hb_at(32400), silence={"measured": True, "awake": 180.0, "why": ""})
        st, info = ex.turn_state(f, ex.config({}), NOW)
        self.assertEqual(st, ex.TURN_OK)
        self.assertGreater(info["slept"], 30000)


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
        run_mod.busy_facts = lambda path=None: None

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
        d = {"467": {"at": "2026-08-10T20:59:34.763818+00:00", "pid": 2624,
                     "proc": "2624-1786299364", "child": 7368},
             "469": {"at": "2026-08-10T21:44:16.763480+00:00", "pid": 2624,
                     "proc": "2624-1786299364", "child": 14452},
             "12": "2026-08-01T00:00:00+00:00"}          # старый вид отметки — голая строка
        p = os.path.join(tempfile.mkdtemp(prefix="expect_pc_reg_"), "task_started.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        b = run_mod.busy_facts(p)
        self.assertEqual(b["task"], "469")
        self.assertEqual(b["limit"], ex.TASK_TIMEOUT_SEC)
        self.assertIsNone(run_mod.busy_facts(p + ".нет"))

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
        self.assertEqual(ex.KINDS, ("o1_pc_new", "o1_pc_run", "o2_pc_turn", "o3_pc_moderbot"))

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
        busy = {"task": "507", "since": NOW - 600.0, "limit": ex.TASK_TIMEOUT_SEC}
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
