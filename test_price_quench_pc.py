# -*- coding: utf-8 -*-
"""Регресс прибора «ДОЛЯ ГАШЕНИЯ ЦЕНЫ» (задание 42-a, 12.09.2026).

Три объявленные цели файла:

  1. ЗАМОК ПРОТИВ ЛОЖНОГО ЗЕЛЁНОГО В ОБЕ СТОРОНЫ. Прибор не вправе ни назвать гашение
     благополучием, ни назвать благополучие гашением. Поэтому у каждой ветки есть парный кейс:
     «показал отказ, когда надо» и «промолчал, когда надо».
  2. ОТРИЦАТЕЛЬНЫЙ ТЕСТ — ПЕРВОГО КЛАССА, А НЕ ПРИЛОЖЕНИЕ. Намеренно созданное состояние
     «цена погашена, а прибор молчит» обязано дать ОТКАЗ (`TestNegativeQuenchMustBeSeen`).
     Без него заход задания 42-a не принимается, и без него прибор бесполезен: ослепший прибор
     молчит ровно так же, как исправный при собравшейся цене.
  3. ГРАНИЦА ЧИСТОТЫ ДЕРЖИТСЯ AST-РАЗБОРОМ, А НЕ ДОКСТРИНГОМ (`TestQuenchPure`). Решение
     обязано остаться без рук: модуль, обзаведшийся `open`/`os`/`time`, судит уже не факты.

Запуск: venv\\Scripts\\python.exe -m unittest test_price_quench_pc
"""
import ast
import os
import unittest

import price_quench_pc as pq

HERE = os.path.dirname(os.path.abspath(__file__))

# ═════════════════ ФАБРИКИ ФИКСТУР ════════════════════════════════════════════
# Цепочки собираются ТЕМИ ЖЕ полями, какие кладут руки (`price_quench_pc_probe.measure`):
# самодельный словарь в кейсе разошёлся бы с живым форматом молча — класс полосы.


def q(status="ok", sec=1.0, exc=None, noprice=None):
    """Запись о вызове `pricing.quote_for_model` в виде, в каком её кладут руки."""
    row = {"fn": "pricing.quote_for_model", "sec": sec}
    if exc:
        row["exc"] = exc
    else:
        row.update({"status": status, "has_quote": status == "ok", "noprice": noprice})
    return row


def gate(may=True, card=None):
    """Запись о вызове `price_gate.allow` — сторож свежести."""
    return {"fn": "price_gate.allow", "may": may, "card": card}


def rep(status="ok", noprice=None, exc=None):
    """Запись о вызове `price_source.reprice` — источник цены."""
    row = {"fn": "price_source.reprice"}
    if exc:
        row["exc"] = exc
    else:
        row.update({"status": status, "has_quote": status == "ok", "noprice": noprice})
    return row


def obs(road, at=1000.0):
    """Наблюдение в том виде, в каком его кладёт `remember`."""
    return {"at": at, "road": road, "why": "фикстура", "quoted": road == pq.ROAD_OK}


def st(rows, ok=True, err="", probe="NMAX 155|2026-10-06|2026-10-11"):
    """Состояние прибора в том виде, в каком его отдаёт `load_state`."""
    return {"ok": ok, "err": err, "obs": list(rows), "probe": probe,
            "last": (rows[-1] if rows else None), "series_started": 1000.0}


def facts(rows, **kw):
    return {"now": 2000.0, "quench": st(rows, **kw)}


# ═════════════════ ПОТРЕБНОЕ ЧИСЛО НАБЛЮДЕНИЙ ═════════════════════════════════

class TestNeedObservations(unittest.TestCase):
    """Порог не назначается круглым числом — он СЧИТАЕТСЯ из записанного замера (п.5 задания)."""

    def test_seed_measure_of_11_09_gives_189(self):
        """Доля 1 из 7 за 11.09 при полуширине 5 п.п. даёт 189 наблюдений.

        Число проверяется ЛИТЕРАЛОМ намеренно: молчаливый уезд формулы поменял бы обещание,
        данное владельцу в артефакте, и заметить это было бы нечем."""
        self.assertEqual(pq.need_observations(), 189)
        self.assertEqual(pq.need_observations(7, 1, 5.0), 189)

    def test_seven_observations_are_declared_insufficient(self):
        """Семь наблюдений МАЛО, и прибор обязан считать это числом, а не мнением."""
        self.assertGreater(pq.need_observations(), pq.SEED_OBS * 20)

    def test_degenerate_share_asks_for_the_most_observations(self):
        """Ноль гашений и сплошные гашения дисперсии не дают: берём самый дорогой случай p=0.5.

        Иначе прибор при первом же чистом наблюдении объявил бы порог осмысленным (n=0)."""
        worst = pq.need_observations(100, 50, 5.0)
        self.assertEqual(pq.need_observations(100, 0, 5.0), worst)
        self.assertEqual(pq.need_observations(100, 100, 5.0), worst)
        self.assertEqual(worst, 385)

    def test_tighter_halfwidth_costs_more_observations(self):
        """Точнее требование — больше наблюдений. Монотонность, без которой число ничего не значит."""
        self.assertGreater(pq.need_observations(7, 1, 2.5), pq.need_observations(7, 1, 5.0))
        self.assertLess(pq.need_observations(7, 1, 10.0), pq.need_observations(7, 1, 5.0))

    def test_garbage_input_does_not_pretend_to_be_a_number(self):
        self.assertEqual(pq.need_observations(0, 0, 5.0), 0)
        self.assertEqual(pq.need_observations("нет", None, 5.0), 0)


class TestConfig(unittest.TestCase):
    """Ручки прибора. Главная мина названа разведкой задания: доля — НЕ минуты."""

    def test_defaults_are_the_declared_numbers(self):
        cfg = pq.config({})
        self.assertEqual(cfg["every"], 30.0 * 60)
        self.assertEqual(cfg["keep"], 512)
        self.assertEqual(cfg["need"], 189)

    def test_keep_is_not_multiplied_by_sixty(self):
        """`limit_env` домножает на 60 по умолчанию — для НЕ-времени это обязан быть scale=1.

        Кейс существует потому, что ошибка тихая: кольцо на 512 стало бы кольцом на 30720, и
        заметить это было бы нечем до первого разбора состояния."""
        self.assertEqual(pq.config({"PRICE_QUENCH_KEEP": "64"})["keep"], 64)

    def test_zero_is_a_declared_rollback_not_garbage(self):
        self.assertEqual(pq.config({"PRICE_QUENCH_EVERY_MIN": "0"})["every"], 0.0)

    def test_garbage_falls_back_to_the_declared_default(self):
        self.assertEqual(pq.config({"PRICE_QUENCH_EVERY_MIN": "ой"})["every"], 30.0 * 60)


# ═════════════════ ДОРОГИ ОТКАЗА ══════════════════════════════════════════════

class TestRoadOf(unittest.TestCase):
    """П.6 задания: прибор ОБЯЗАН различать дороги и записывать, какая сработала."""

    def test_quote_block_present_is_the_only_road_to_ok(self):
        road, why = pq.road_of([q("ok"), gate(True), rep("ok")], True)
        self.assertEqual(road, pq.ROAD_OK)
        self.assertIn("quote-блок", why)
        self.assertFalse(pq.is_quench(road))

    def test_bridge_timeout_is_named_as_exhausted_waiting(self):
        """Исчерпание времени ожидания моста — первая из двух дорог, названных заданием."""
        road, why = pq.road_of([q("error", sec=19.0)], False, timeout_sec=20.0, tries=2)
        self.assertEqual(road, pq.ROAD_BRIDGE)
        self.assertIn("исчерпание времени ожидания", why)
        self.assertIn("19.00", why)
        self.assertIn("20", why)

    def test_bridge_failure_that_is_not_a_timeout_says_so_out_loud(self):
        """Мост отказал БЫСТРО — это не таймаут, и прибор не вправе назвать его таймаутом.

        Живой случай: отрицательный тест 12.09 дал худший вызов 1.00 с при потолке 20 с."""
        road, why = pq.road_of([q("error", sec=1.0)], False, timeout_sec=20.0)
        self.assertEqual(road, pq.ROAD_BRIDGE)
        self.assertIn("НЕ исчерпание ожидания", why)

    def test_gate_refusal_is_the_second_named_road(self):
        """Отказ сторожа свежести — вторая дорога задания; причина сторожа едет в текст."""
        road, why = pq.road_of([q("ok"), gate(False, "ЦЕНА НЕ НАЗВАНА: лист не прочитан")], False)
        self.assertEqual(road, pq.ROAD_GATE)
        self.assertIn("сторож свежести", road)
        self.assertIn("лист не прочитан", why)
        self.assertTrue(pq.is_quench(road))

    def test_gate_refusal_without_a_card_still_names_the_road(self):
        road, why = pq.road_of([q("ok"), gate(False, None)], False)
        self.assertEqual(road, pq.ROAD_GATE)
        self.assertIn("причина сторожем не названа", why)

    def test_source_quench_is_named_only_when_the_bridge_delivered(self):
        road, why = pq.road_of([q("ok"), gate(True), rep("error", noprice="MODEL_HAS_NO_PRICE")],
                               False)
        self.assertEqual(road, pq.ROAD_SOURCE)
        self.assertIn("MODEL_HAS_NO_PRICE", why)

    def test_a_bridge_failure_is_never_blamed_on_the_source(self):
        """ПРИЧИННЫЙ ПОРЯДОК, НАЙДЕННЫЙ ОТРИЦАТЕЛЬНЫМ ТЕСТОМ 12.09, И ЕГО ЗАМОК.

        `price_source.reprice` при входе не-`ok` возвращает тот же объект нетронутым
        (`price_source.py:504-505`), то есть ПРОПУСКАЕТ чужую ошибку сквозь себя. Первая
        редакция разбора видела у него `status="error"` и называла дорогой источник цены,
        когда в действительности молчал мост. Кейс сторожит, что это не вернётся."""
        chain = [q("error", sec=1.0), gate(True), rep("error")]
        road, _why = pq.road_of(chain, False, timeout_sec=20.0)
        self.assertEqual(road, pq.ROAD_BRIDGE)

    def test_a_bridge_failure_is_never_blamed_on_the_gate_either(self):
        """Тот же замок для сторожа: он зовётся ПОСЛЕ моста и на мёртвом мосте уже ничего не решает."""
        road, _why = pq.road_of([q("error", sec=1.0), gate(False, "карточка")], False,
                                timeout_sec=20.0)
        self.assertEqual(road, pq.ROAD_BRIDGE)

    def test_exception_from_the_bridge_is_a_bridge_road(self):
        road, why = pq.road_of([q(exc="TimeoutError")], False, timeout_sec=20.0)
        self.assertEqual(road, pq.ROAD_BRIDGE)
        self.assertIn("TimeoutError", why)

    def test_busy_fleet_is_lawful_silence_and_not_a_quench(self):
        """Занятость парка гашением НЕ является: иначе доля мерила бы наполненность парка."""
        road, _why = pq.road_of([q("none_available")], False)
        self.assertEqual(road, pq.ROAD_BUSY)
        self.assertFalse(pq.is_quench(road))

    def test_missing_model_is_lawful_silence_too(self):
        road, _why = pq.road_of([q("no_candidates")], False)
        self.assertEqual(road, pq.ROAD_NOBODY)
        self.assertFalse(pq.is_quench(road))

    def test_unknown_road_must_say_what_was_missing(self):
        """«Неизвестно» без причины не принимается (п.6 задания дословно)."""
        road, why = pq.road_of([{"fn": "_safe_quote_for_model", "status": "ok"}], False)
        self.assertEqual(road, pq.ROAD_UNKNOWN)
        self.assertIn("_safe_quote_for_model", why)
        self.assertIn("ни одна дорога себя не назвала", why)

    def test_an_empty_chain_blames_the_missing_wrappers_by_name(self):
        """Живой случай 12.09: обёртки не встали, ценовой путь не вызывался вовсе.

        Прибор обязан сказать ИМЕННО это, а не «цена погасла» и не «всё хорошо»."""
        road, why = pq.road_of([], False)
        self.assertEqual(road, pq.ROAD_UNKNOWN)
        self.assertIn("обёртки не встали", why)

    def test_unknown_is_neither_quench_nor_wellbeing(self):
        self.assertFalse(pq.is_quench(pq.ROAD_UNKNOWN))
        self.assertFalse(pq.is_quench(pq.ROAD_OK))
        self.assertTrue(pq.is_quench(pq.ROAD_BRIDGE))
        self.assertTrue(pq.is_quench(pq.ROAD_GATE))
        self.assertTrue(pq.is_quench(pq.ROAD_SOURCE))


# ═════════════════ ДОЛЯ ═══════════════════════════════════════════════════════

class TestTally(unittest.TestCase):
    """Знаменатель доли назван явно — молчаливый знаменатель есть классическая ложь доли."""

    def test_denominator_counts_only_judged_observations(self):
        rows = [obs(pq.ROAD_OK), obs(pq.ROAD_BRIDGE), obs(pq.ROAD_UNKNOWN), obs(pq.ROAD_BUSY)]
        t = pq.tally(rows)
        self.assertEqual((t["total"], t["judged"], t["quenched"], t["ok"]), (4, 2, 1, 1))
        self.assertEqual((t["unknown"], t["lawful"]), (1, 1))
        self.assertAlmostEqual(t["share"], 0.5)

    def test_share_is_none_when_nothing_is_judged(self):
        """Ноль судимых наблюдений — это НЕ доля ноль: ноль читался бы как измеренный факт."""
        self.assertIsNone(pq.tally([obs(pq.ROAD_UNKNOWN)])["share"])
        self.assertIsNone(pq.tally([])["share"])

    def test_roads_are_counted_by_name(self):
        t = pq.tally([obs(pq.ROAD_GATE), obs(pq.ROAD_GATE), obs(pq.ROAD_BRIDGE)])
        self.assertEqual(t["roads"][pq.ROAD_GATE], 2)
        self.assertEqual(t["roads"][pq.ROAD_BRIDGE], 1)


class TestVerdict(unittest.TestCase):
    """Три исхода, и тревоги нет ни в одной ветке, пока замера порога нет (п.5)."""

    def test_growing_shows_the_share_and_names_what_is_missing(self):
        word, info = pq.verdict(facts([obs(pq.ROAD_OK), obs(pq.ROAD_BRIDGE)]), pq.config({}), 2000.0)
        self.assertEqual(word, pq.SHARE_GROWING)
        self.assertEqual(info["quenched"], 1)
        self.assertEqual(info["judged"], 2)
        self.assertEqual(info["need"], 189)
        self.assertIn("не хватает 187", info["why"])

    def test_no_alarm_word_exists_in_any_outcome(self):
        """Замок на п.5: слов тревоги у прибора нет вовсе, пока порог не назначен замером."""
        self.assertEqual(sorted([pq.SHARE_COUNTED, pq.SHARE_GROWING, pq.SHARE_UNKNOWN]),
                         sorted(["доля сосчитана", "копим наблюдения", "неизвестно"]))

    def test_counted_only_after_enough_judged_observations(self):
        rows = [obs(pq.ROAD_OK)] * 200
        word, info = pq.verdict(facts(rows), pq.config({}), 2000.0)
        self.assertEqual(word, pq.SHARE_COUNTED)
        self.assertIn("порог назначать можно", info["why"])

    def test_unreadable_state_is_unknown_and_never_wellbeing(self):
        """Ослепший прибор обязан сказать «неизвестно», а не «гашений не было»."""
        word, info = pq.verdict(facts([], ok=False, err="ValueError: битый json"),
                                pq.config({}), 2000.0)
        self.assertEqual(word, pq.SHARE_UNKNOWN)
        self.assertIn("битый json", info["why"])

    def test_no_fact_at_all_is_unknown(self):
        word, info = pq.verdict({"now": 1.0}, pq.config({}), 1.0)
        self.assertEqual(word, pq.SHARE_UNKNOWN)
        self.assertIn("нет вовсе", info["why"])

    def test_read_emptiness_is_unknown_not_zero_percent(self):
        """ПРОЧИТАННАЯ пустота — тоже «неизвестно»: бот мог не считать цену ни разу."""
        word, info = pq.verdict(facts([]), pq.config({}), 2000.0)
        self.assertEqual(word, pq.SHARE_UNKNOWN)
        self.assertIn("судить не по чему", info["why"])

    def test_only_lawful_silence_is_still_unknown(self):
        word, _info = pq.verdict(facts([obs(pq.ROAD_BUSY), obs(pq.ROAD_NOBODY)]),
                                 pq.config({}), 2000.0)
        self.assertEqual(word, pq.SHARE_UNKNOWN)


# ═════════════════ ОТРИЦАТЕЛЬНЫЙ ТЕСТ ════════════════════════════════════════

class TestNegativeQuenchMustBeSeen(unittest.TestCase):
    """НАМЕРЕННО СОЗДАННОЕ «ЦЕНА ПОГАШЕНА, А ПРИБОР МОЛЧИТ» → ПРИБОР ОБЯЗАН ПОКАЗАТЬ ОТКАЗ.

    Без этого класса заход задания 42-a не принимается. Сети он не трогает: подкладывается
    ровно та цепочка вызовов, какую даёт живой мёртвый мост (замер 12.09, `--negative`)."""

    DEAD_BRIDGE_CHAIN = [q("error", sec=1.0), gate(True), rep("error")]

    def test_a_dead_bridge_is_seen_as_a_quench_with_a_named_road(self):
        road, why = pq.road_of(self.DEAD_BRIDGE_CHAIN, False, timeout_sec=20.0, tries=2)
        self.assertTrue(pq.is_quench(road), "погашенная цена прочитана как НЕ гашение")
        self.assertEqual(road, pq.ROAD_BRIDGE)
        self.assertTrue(why.strip(), "дорога названа без доказательства")

    def test_the_quench_reaches_the_share_and_is_not_swallowed(self):
        """Мало увидеть гашение — оно обязано доехать до ДОЛИ. Кейс ловит потерю в знаменателе."""
        road, _why = pq.road_of(self.DEAD_BRIDGE_CHAIN, False, timeout_sec=20.0)
        word, info = pq.verdict(facts([obs(pq.ROAD_OK), obs(road)]), pq.config({}), 2000.0)
        self.assertEqual(word, pq.SHARE_GROWING)
        self.assertEqual(info["quenched"], 1)
        self.assertAlmostEqual(info["share_pp"], 50.0)

    def test_the_line_shown_to_a_human_cannot_hide_the_quench(self):
        """Гашение обязано быть ВИДНО в строке, которую читает человек, а не только в словаре."""
        _word, info = pq.verdict(facts([obs(pq.ROAD_OK), obs(pq.ROAD_GATE)]), pq.config({}), 2000.0)
        line = pq.render_line(pq.SHARE_GROWING, info)
        self.assertIn("1 из 2", line)
        self.assertIn(pq.ROAD_GATE, line)

    def test_a_silent_instrument_is_a_defect_and_the_test_says_so(self):
        """ПОДЛОГ В ОБРАТНУЮ СТОРОНУ: если дорога не названа, прибор обязан сказать «неизвестно»,
        а не тихое благополучие. Молчание здесь — дефект, и кейс его ловит."""
        road, why = pq.road_of([{"fn": "price_gate.allow", "may": True}], False)
        self.assertEqual(road, pq.ROAD_UNKNOWN)
        self.assertNotEqual(road, pq.ROAD_OK)
        self.assertIn("не назвала", why)


# ═════════════════ КАДЕНЦИЯ ══════════════════════════════════════════════════

class TestProbeDue(unittest.TestCase):
    """Частоту решает прибор, а не звонящий (форма `srv_delivery.due`)."""

    def test_first_ever_probe_is_due(self):
        due, why = pq.probe_due(None, pq.config({}), 1000.0)
        self.assertTrue(due)
        self.assertIn("впервые", why)

    def test_too_soon_is_not_due_and_says_both_numbers(self):
        due, why = pq.probe_due(1000.0, pq.config({}), 1060.0)
        self.assertFalse(due)
        self.assertIn("60", why)
        self.assertIn("1800", why)

    def test_after_the_floor_it_is_due(self):
        due, _why = pq.probe_due(1000.0, pq.config({}), 1000.0 + 1800.0)
        self.assertTrue(due)

    def test_zero_kills_the_branch_before_anything_else(self):
        due, why = pq.probe_due(None, pq.config({"PRICE_QUENCH_EVERY_MIN": "0"}), 1000.0)
        self.assertFalse(due)
        self.assertIn("выключена порогом", why)

    def test_clock_running_backwards_is_not_treated_as_too_soon(self):
        """Часы поехали назад — это незнание, и честнее промерить, чем считать возраст минусом."""
        due, why = pq.probe_due(2000.0, pq.config({}), 1000.0)
        self.assertTrue(due)
        self.assertIn("назад", why)


# ═════════════════ СТРОКА ДЛЯ ВИТРИНЫ ════════════════════════════════════════

class TestRenderLine(unittest.TestCase):
    """Окно витрины тесное (свободно 287 симв. замером 12.09) — цена строки обязана быть известна."""

    def test_the_line_fits_the_measured_free_space_of_the_showcase(self):
        """287 — ЗАМЕР, а не догадка: `TEXT_MAX` 3900 против сообщения 4192 на живом обороте.

        Кейс существует, чтобы строка не выросла молча: выросшая строка выдавит из витрины
        ЦЕЛЫЙ соседний раздел, и обрезка назовёт его вслух — но потеря всё равно случится."""
        rows = [obs(pq.ROAD_OK)] * 40 + [obs(pq.ROAD_GATE)] * 3 + [obs(pq.ROAD_BRIDGE)] * 2
        _word, info = pq.verdict(facts(rows), pq.config({}), 2000.0)
        line = pq.render_line(pq.SHARE_GROWING, info)
        self.assertLessEqual(len(line), 287, "строка доли не влезает в замеренное окно витрины")

    def test_unknown_line_names_the_reason(self):
        line = pq.render_line(pq.SHARE_UNKNOWN, {"why": "состояние не прочитано"})
        self.assertIn("НЕИЗВЕСТНО", line)
        self.assertIn("состояние не прочитано", line)

    def test_unknown_line_never_claims_a_reason_it_does_not_have(self):
        line = pq.render_line(pq.SHARE_UNKNOWN, {})
        self.assertIn("причина не названа", line)


# ═════════════════ ЧИСТОТА РЕШЕНИЯ ═══════════════════════════════════════════

_ALLOWED_IMPORTS = ()                      # решению не нужен НИ ОДИН импорт, и это проверяется
_FORBIDDEN_CALLS = ("open", "exec", "eval", "compile", "__import__", "input")
_FORBIDDEN_ATTR_ROOTS = ("os", "sys", "time", "subprocess", "shutil", "socket", "requests",
                         "urllib", "pathlib", "tempfile", "sqlite3", "bridge_http", "suggest",
                         "pricing", "price_gate", "price_source", "logging")
_FORBIDDEN_NAMES = ("Popen", "system", "remove", "unlink", "rmtree", "kill", "taskkill",
                    "schtasks", "claim_task", "complete_task", "enqueue_task")


def pure_findings(src):
    """Разбор ast, не подстрока: имя в комментарии, строке и докстринге кодом не является."""
    bad = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += ["import %s" % a.name for a in node.names if a.name not in _ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "") not in _ALLOWED_IMPORTS:
                bad.append("from %s import" % node.module)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _FORBIDDEN_CALLS:
                bad.append("вызов %s()" % fn.id)
            if isinstance(fn, ast.Attribute):
                root = fn.value
                if isinstance(root, ast.Name) and root.id in _FORBIDDEN_ATTR_ROOTS:
                    bad.append("%s.%s()" % (root.id, fn.attr))
                if fn.attr in _FORBIDDEN_NAMES:
                    bad.append("вызов .%s()" % fn.attr)
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            bad.append("имя %s" % node.id)
    return bad


class TestQuenchPure(unittest.TestCase):
    """FAIL-CLOSED: файла нет / не парсится → провал, а не тишина."""

    def _src(self):
        with open(os.path.join(HERE, "price_quench_pc.py"), "r", encoding="utf-8") as fh:
            return fh.read()

    def test_the_decision_module_has_no_hands(self):
        self.assertEqual(pure_findings(self._src()), [],
                         "живой price_quench_pc.py обзавёлся руками — он больше не судит факты")

    def test_it_imports_nothing_at_all(self):
        """Ни одного импорта. Решение, которому понадобился `time`, уже меряет мир, а не факты."""
        tree = ast.parse(self._src())
        got = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(got, [], "решение обзавелось импортом")

    def test_the_invariant_catches_an_injected_violation(self):
        """Инвариант, который не ловит нарушение, — это молчание, а не замок."""
        for bad in ("import os\n", "import time\n", "from os import path\n",
                    "x = open('f')\n", "y = eval('1')\n", "z = time.time()\n",
                    "w = suggest.build_pricing_note({})\n", "v = Popen(['x'])\n"):
            self.assertTrue(pure_findings(bad), "подлог %r не поймал инвариант" % bad)
        self.assertEqual(pure_findings("def f(a):\n    return a + 1\n"), [])


class TestHandsExist(unittest.TestCase):
    """У рук границы шире, но зубов у них нет: очередь и процессы прибору не принадлежат."""

    def _hands(self):
        with open(os.path.join(HERE, "price_quench_pc_probe.py"), "r", encoding="utf-8") as fh:
            return fh.read()

    def test_hands_never_touch_the_queue_or_processes(self):
        src = self._hands()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, _FORBIDDEN_NAMES,
                                 "руки прибора доли обзавелись зубами: .%s()" % node.func.attr)
        for word in ("claim_task", "complete_task", "enqueue_task", "taskkill", "schtasks",
                     "subprocess", "sqlite3"):
            self.assertNotIn(word, src, "в руках прибора появилось слово %r" % word)

    def test_hands_do_not_read_secrets_or_configs(self):
        """`.env` и конфиги прибор не читает ни одной строкой — он читает КОД, который их читает.

        ПРОВЕРЯЮТСЯ ИМЕНА, А НЕ ПОДСТРОКА «.env», И ЭТО НЕ НЕБРЕЖНОСТЬ. Первая редакция кейса
        искала подстроку и падала на СВОЁМ ЖЕ докстринге («.env и конфиги не читать» — запрет
        задания, набранный прозой). На этой полосе класс уже закрыт решением 01.08.2026:
        упоминание имени секрета в тексте чтением не является. Читают `open`/`load_env`, а не
        слова о них."""
        src = self._hands()
        for word in ("BRIDGE_TOKEN", "BRIDGE_URL", "load_env", "dotenv"):
            self.assertNotIn(word, src, "руки прибора потянулись к секретам: %r" % word)


if __name__ == "__main__":
    unittest.main(verbosity=2)
