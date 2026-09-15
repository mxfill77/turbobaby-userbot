# -*- coding: utf-8 -*-
"""test_polzunki_pc.py — набор ПРИБОРА ПОЛЗУНКОВ сезонной скидки (задание 62-g, 15.09.2026).

ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЧЕТЫРЬМЯ КОНЦАМИ, названный заданием, живёт в :class:`TestNegative` и
каждый конец идёт В ПАРЕ с положительным контролем. Пара обязательна: прибор, который
НИКОГДА не поднимает сигнала, проходит любую отрицательную проверку идеально и стои́т ноль —
поэтому рядом с «та же картина второго сигнала не рождает» всегда стои́т «сменилась — родила».

  1. значение вне правил      → обязано выйти РАСХОЖДЕНИЕ  (контроль: на предписанном — СОВПАДАЕТ)
  2. пустой ответ двери       → обязано выйти НЕИЗВЕСТНО   (контроль: живой ответ судится числами)
  3. та же картина дважды     → второго сигнала нет        (контроль: сменилась — сигнал есть)
  4. возврат к предписанному  → сказано ОДИН раз и молчок  (контроль: первый раз сказано)

Прочие классы:

* :class:`TestRules` — что правила предписывают и ЧЕГО ОНИ НЕ ГОВОРЯТ. Месяц вне «июнь-октябрь»
  обязан дать НЕИЗВЕСТНО со словами, а не догадку: «10% — потолок» это предел, а не положение.
* :class:`TestPurity` — инвариант ``POLZUNKI_PURE``: слой решения не смеет завести часы, диск,
  сеть, окружение или подпроцессы. И инвариант ``POLZUNKI_READS_ONLY``: ни у решения, ни у рук
  нет ни одного `set_*`/`toggle_*`, ни двери наружу — прибор ползунков не трогает.
* :class:`TestLiveShape` — ФОРМА ФАКТОВ СНЯТА С ЖИВОГО ПОТРЕБИТЕЛЯ, а не придумана: голден
  повторяет ответ `price_freshness_run.live_handles`, снятый живой дверью 15.09.2026.
* :class:`TestState` — состояние живёт ВНЕ очереди и ВНЕ временной папки, пишется атомарно,
  помнит «с какого замера держится».
* :class:`TestVitrina` — строка прибора доезжает до раздела витрины «ЖДЁТ ВЛАДЕЛЬЦА», а при
  исходе СОВПАДАЕТ её там НЕТ (совпадает — прибор молчит).
* :class:`TestCaller` — ЖИВОЙ ВЫЗЫВАЮЩИЙ НАЗВАН: `maybe_polzunki` есть у демона, зовётся в
  витке и внесён в реестр ленивых листьев (иначе self-update выкатит руки без модуля).
"""
from __future__ import annotations

import ast
import io
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

import polzunki_pc as pp                                             # noqa: E402
import polzunki_pc_run as pr                                         # noqa: E402

# ═══ ЖИВОЙ ГОЛДЕН. Снят 15.09.2026 боевым съёмщиком `price_freshness_run.live_handles`
# (девять GET `quote_price`, окно 15.09..16.09): «H3 юнитов ответило 3, разных значений 1 →
# 0.15 · I3 → 0.15 · J3 → 0.25», отказов 0 из 9. Синтетическую форму сюда класть НЕЛЬЗЯ:
# полоса уже обжигалась на моке, разошедшемся с живым ответом (CLAUDE.md, «Форматы»).
LIVE_1509 = {"ok": True,
             "handles": {"H3": 0.15, "I3": 0.15, "J3": 0.25},
             "detail": {"H3": {"units": [("XSR 155СС GREEN", 0.15)], "distinct": [0.15]},
                        "I3": {"units": [("CBR 650R PHUKET 4505", 0.15)], "distinct": [0.15]},
                        "J3": {"units": [("NMAX 155CC GREY PHUKET 5960", 0.25)],
                               "distinct": [0.25]}}}
# Та же форма, но с положением ручек, ИЗМЕРЕННЫМ 13.09 (I3 стоял 0.0 при предписанных 0.15).
LIVE_1309 = {"ok": True, "handles": {"H3": 0.15, "I3": 0.0, "J3": 0.25}}
DOOR_SILENT = {"ok": False, "handles": None, "error": "клиент моста не поднялся (RuntimeError)"}

SEPT = 1789000000.0        # 2026-09-10 UTC — внутри «июнь-октябрь»
APRIL = 1776000000.0       # 2026-04-12 UTC — вне: правила чисел не называют


def _src(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _when(ts):
    return pr.when_of(ts)


def _idents(name):
    """Все ИМЕНА, которыми файл пользуется в коде (обход AST). → set.

    Нужен там, где греп по исходнику врёт: собственная докстрока прибора НАЗЫВАЕТ запрещённые
    двери словами, и подстрочная проверка проваливалась бы на объявлении самого запрета.
    """
    tree = ast.parse(_src(name), filename=name)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
            out |= {a.asname for a in node.names if a.asname}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


class _Tmp(unittest.TestCase):
    """Своё место для состояния: боевого файла не касается ни один тест набора."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="polzunki_")
        self.path = pr.state_path(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def tick(self, facts, now, **kw):
        return pr.tick(root=self.root, now=now, facts=facts, **kw)


# ═════════════════════════ ОТРИЦАТЕЛЬНЫЙ ТЕСТ ════════════════════════════════

class TestNegative(_Tmp):
    """Четыре конца задания. У каждого рядом положительный контроль."""

    # ── конец 1: значение вне правил ──
    def test_value_outside_the_rules_gives_divergence(self):
        rep = self.tick(LIVE_1309, SEPT)
        self.assertEqual(pp.DIVERGED, rep["state"])
        self.assertEqual(pp.MOVED, rep["kind"])
        self.assertIn("I3", rep["text"])
        self.assertIn("0.15", rep["text"], "предписанное число обязано быть В ТЕКСТЕ")

    def test_control_value_on_the_prescribed_gives_match(self):
        rep = self.tick(LIVE_1509, SEPT)
        self.assertEqual(pp.MATCH, rep["state"])
        self.assertEqual("", rep["kind"], "совпадает — прибор молчит")

    # ── конец 2: пустой ответ двери ──
    def test_empty_door_answer_gives_unknown_not_match(self):
        rep = self.tick(DOOR_SILENT, SEPT)
        self.assertEqual(pp.UNKNOWN, rep["state"])
        self.assertNotEqual(pp.MATCH, rep["state"])
        self.assertIn(pp.WHY_DOOR_SILENT, rep["verdict"]["why"])

    def test_control_empty_handles_dict_is_not_a_match_either(self):
        """Пустой словарь ручек — это НЕ «ручек ноль, значит все сошлись»."""
        rep = self.tick({"ok": True, "handles": {}}, SEPT)
        self.assertEqual(pp.UNKNOWN, rep["state"])

    def test_control_one_read_handle_diverged_beats_two_unread(self):
        """Расхождение сильнее незнания соседки — иначе молчащая ручка прячет съехавшую."""
        rep = self.tick({"ok": True, "handles": {"H3": None, "I3": 0.0, "J3": None}}, SEPT)
        self.assertEqual(pp.DIVERGED, rep["state"])
        self.assertIn("не снялись", rep["verdict"]["why"], "непрочитанные обязаны быть названы")

    # ── конец 3: та же картина дважды ──
    def test_same_picture_twice_makes_no_second_signal(self):
        first = self.tick(LIVE_1309, SEPT)
        second = self.tick(LIVE_1309, SEPT + 60)
        self.assertTrue(first["acted"])
        self.assertFalse(second["acted"], "та же картина второго сигнала не рождает")
        self.assertEqual("", second["kind"])
        self.assertEqual(pp.DIVERGED, second["state"], "молчание НЕ меняет исхода")

    def test_control_changed_picture_makes_a_new_signal(self):
        self.tick(LIVE_1309, SEPT)
        moved = self.tick({"ok": True, "handles": {"H3": 0.15, "I3": 0.05, "J3": 0.25}}, SEPT + 60)
        self.assertTrue(moved["acted"])
        self.assertEqual(pp.MOVED, moved["kind"])

    # ── конец 4: возврат к предписанному ──
    def test_return_to_prescribed_is_said_once_and_then_silence(self):
        self.tick(LIVE_1309, SEPT)
        back = self.tick(LIVE_1509, SEPT + 60)
        again = self.tick(LIVE_1509, SEPT + 120)
        self.assertTrue(back["acted"])
        self.assertEqual(pp.RETURNED, back["kind"])
        self.assertIn("ВЕРНУЛИ", back["text"])
        self.assertFalse(again["acted"], "о возврате говорится ОДИН раз")
        self.assertIsNone(again["vitrina"], "после сказанного возврата витрина молчит")

    def test_control_first_ever_match_says_nothing_at_all(self):
        """Прибор, впервые увидевший исправную полосу, обязан быть НЕМЫМ."""
        rep = self.tick(LIVE_1509, SEPT)
        self.assertFalse(rep["acted"])
        self.assertIsNone(rep["vitrina"])

    def test_the_hand_run_of_the_negative_test_passes_all_ends(self):
        """`--negative` — те же концы руками; боевое состояние им не трогается."""
        rows, _reports = pr._negative(root=self.root)
        bad = [r for r in rows if r[1] != r[3] or r[2] != r[4]]
        self.assertEqual([], bad, "провалившиеся концы: %r" % (bad,))
        self.assertFalse(os.path.exists(self.path), "боевое состояние отрицательным не тронуто")


# ═════════════════════════ ПРАВИЛА ═══════════════════════════════════════════

class TestRules(unittest.TestCase):

    def test_low_season_months_prescribe_numbers(self):
        must, why = pp.prescribed(_when(SEPT))
        self.assertEqual({"H3": 0.15, "I3": 0.15, "J3": 0.25}, must)
        self.assertEqual("", why)

    def test_spring_names_a_ceiling_not_a_position_so_the_outcome_is_unknown(self):
        must, why = pp.prescribed(_when(APRIL))
        self.assertIsNone(must, "потолок скидки положением ручки не является")
        self.assertIn(pp.WHY_NO_RULES, why)
        self.assertIn("ПОТОЛОК", why.upper())

    def test_rules_silence_beats_live_numbers(self):
        """Правила спрашиваются ПЕРВЫМИ: живые числа незнания предписания не чинят."""
        got = pp.judge(LIVE_1509, when=_when(APRIL))
        self.assertEqual(pp.UNKNOWN, got["state"])
        self.assertIn(pp.WHY_NO_RULES, got["why"])

    def test_the_rule_quote_is_kept_verbatim(self):
        self.assertIn("25% на скутеры и 15% на мотоциклы", pp.RULE_QUOTE)
        self.assertIn("июнь-октябрь", pp.RULE_QUOTE)

    def test_two_motorcycle_handles_get_one_and_the_same_number(self):
        """Разного числа для «Моты 1» и «Моты 2» правила не предписывают ни строкой."""
        must, _why = pp.prescribed(_when(SEPT))
        self.assertEqual(must["H3"], must["I3"])

    def test_tolerance_is_finer_than_a_step_of_the_handle(self):
        self.assertLess(pp.TOL, 0.01, "допуск обязан быть мельче шага ручки в 1 п.п.")
        near = pp.judge({"ok": True, "handles": {"H3": 0.15 + 1e-12, "I3": 0.15, "J3": 0.25}},
                        when=_when(SEPT))
        self.assertEqual(pp.MATCH, near["state"], "дребезг двоичного числа расхождением не является")
        step = pp.judge({"ok": True, "handles": {"H3": 0.14, "I3": 0.15, "J3": 0.25}},
                        when=_when(SEPT))
        self.assertEqual(pp.DIVERGED, step["state"], "поворот на 1 п.п. обязан быть виден")


# ═════════════════════════ ТЕКСТ ДЛЯ ЧЕЛОВЕКА ════════════════════════════════

class TestText(unittest.TestCase):

    def setUp(self):
        self.verdict = pp.judge(LIVE_1309, when=_when(SEPT))

    def test_the_text_carries_a_question_not_a_verdict(self):
        text = pp.text(self.verdict, pp.MOVED, since_words="2026-09-06 04:00 UTC", rounds=3)
        self.assertIn("?", text, "текст обязан спрашивать, а не приговаривать")
        self.assertIn("под конкретного клиента", text)
        self.assertIn("Решает человек", text)

    def test_the_text_never_orders_to_move_the_handle(self):
        text = pp.text(self.verdict, pp.MOVED, since_words="2026-09-06 04:00 UTC", rounds=1)
        for word in ("верни ", "поставь", "выставь", "обязан вернуть"):
            self.assertNotIn(word, text.lower(), "прибор не распоряжается ручкой")

    def test_the_text_names_three_numbers_and_the_time(self):
        """Что стои́т · что предписано · с какого замера держится — все три обязаны быть."""
        text = pp.text(self.verdict, pp.MOVED, since_words="2026-09-06 04:00 UTC", rounds=3)
        self.assertIn("0.15", text)
        self.assertIn("I3 «Моты 2» 0 →", text)
        self.assertIn("2026-09-06 04:00 UTC", text)
        self.assertIn("замеров подряд 3", text)

    def test_unknown_says_in_words_what_exactly_is_unknown(self):
        got = pp.judge(DOOR_SILENT, when=_when(SEPT))
        text = pp.text(got, pp.BLIND, since_words="2026-09-06 04:00 UTC")
        self.assertIn(pp.WHY_DOOR_SILENT, text)
        self.assertIn("ничего не предписывает", text)

    def test_held_words_never_turn_missing_time_into_a_number(self):
        self.assertIn("неизвестно", pp.held_words("", 0))


# ═════════════════════════ ФОРМА ЖИВЫХ ФАКТОВ ════════════════════════════════

class TestLiveShape(unittest.TestCase):

    def test_the_golden_repeats_the_live_consumer_shape(self):
        """Ключи голдена — те, что читает боевой съёмщик, и берутся у него же."""
        import price_freshness_run as pfr

        cells = [cell for cell, _bikes in pfr.PROBES]
        self.assertEqual(sorted(cells), sorted(LIVE_1509["handles"]))
        self.assertEqual(sorted(cells), sorted(c for c, _n, _k in pp.CELLS))

    def test_the_device_has_no_second_reader_of_the_door(self):
        """Своего разбора ответа двери у прибора нет: поле читает чужой боевой съёмщик."""
        self.assertNotIn("global_discount", _src("polzunki_pc.py"))
        self.assertNotIn("quote_price", _src("polzunki_pc.py"))
        self.assertIn("live_handles", _src("polzunki_pc_run.py"))

    def test_bool_is_not_a_number_for_a_handle(self):
        got = pp.judge({"ok": True, "handles": {"H3": True, "I3": 0.15, "J3": 0.25}},
                       when=_when(SEPT))
        self.assertIsNone(got["live"]["H3"], "True числом ручки не является")


# ═════════════════════════ ЧИСТОТА И ТОЛЬКО ЧТЕНИЕ ═══════════════════════════

class TestPurity(unittest.TestCase):

    _WORLD = ("time", "os", "sys", "json", "io", "subprocess", "socket", "urllib",
              "requests", "random", "sqlite3", "shutil")

    def test_polzunki_pure(self):
        """POLZUNKI_PURE: слой решения знает только `datetime` — ни часов, ни диска, ни сети."""
        tree = ast.parse(_src("polzunki_pc.py"), filename="polzunki_pc.py")
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual({"datetime"}, names, "чистый слой завёл мир: %r" % sorted(names))
        for bad in self._WORLD:
            self.assertNotIn(bad, names)

    def test_polzunki_reads_only(self):
        """POLZUNKI_READS_ONLY: ни одной записи в лист и ни одной двери наружу.

        Считаются ИМЕНА В КОДЕ (обход AST), а не подстроки текста: греп по исходнику ловил бы
        собственную докстроку, где эти двери НАЗВАНЫ как запрещённые, и запрет проваливался бы
        ровно там, где он объявлен вслух.
        """
        names = _idents("polzunki_pc.py") | _idents("polzunki_pc_run.py")
        for bad in ("set_season", "set_price", "set_discount", "toggle_season", "write_doc",
                    "dispatch_notify", "send_topic_strict", "deliver", "sendMessage",
                    # `brain_writer` закрывает и запись в мозг, и его `append` разом: имя
                    # модуля в коде обязано отсутствовать, а `list.append` — законный метод,
                    # и запрещать его как подстроку значило бы запретить питон.
                    "brain_writer", "write_text", "apply"):
            self.assertNotIn(bad, names, "прибор завёл запись или дверь наружу: %s" % bad)

    def test_the_device_never_deletes_anything(self):
        names = _idents("polzunki_pc.py") | _idents("polzunki_pc_run.py")
        for bad in ("remove", "unlink", "rmtree", "rmdir"):
            self.assertNotIn(bad, names)

    def test_the_device_does_not_read_secrets(self):
        """Секреты моста берёт САМ одолженный клиент; прибор их не читает и не видит."""
        names = _idents("polzunki_pc_run.py")
        for bad in ("load_env", "BRIDGE_TOKEN", "getenv"):
            self.assertNotIn(bad, names)
        self.assertNotIn(".env", _src("polzunki_pc_run.py").replace("os.environ", ""))


# ═════════════════════════ СОСТОЯНИЕ ═════════════════════════════════════════

class TestState(_Tmp):

    def test_state_lives_outside_the_queue_and_outside_tmp(self):
        path = pr.state_path(HERE)
        self.assertEqual(HERE, os.path.dirname(path), "состояние обязано лежать в корне полосы")
        low = path.replace("\\", "/").lower()
        self.assertNotIn("/tmp/", low)
        self.assertNotIn("queue", low)

    def test_state_remembers_the_three_things_the_task_asked_for(self):
        self.tick(LIVE_1309, SEPT)
        got = json.load(io.open(self.path, encoding="utf-8"))
        self.assertEqual({"H3": 0.15, "I3": 0.0, "J3": 0.25}, got["seen"])
        self.assertEqual(SEPT, got["seen_at"])
        self.assertEqual(pp.MOVED, got["signal_kind"])

    def test_held_since_survives_while_the_picture_stands(self):
        self.tick(LIVE_1309, SEPT)
        self.tick(LIVE_1309, SEPT + 3600)
        got = json.load(io.open(self.path, encoding="utf-8"))
        self.assertEqual(SEPT, got["held_at"], "неподвижная ручка моложе не становится")
        self.assertEqual(2, got["held_rounds"])

    def test_held_since_resets_when_the_handle_moves(self):
        self.tick(LIVE_1309, SEPT)
        self.tick(LIVE_1509, SEPT + 3600)
        got = json.load(io.open(self.path, encoding="utf-8"))
        self.assertEqual(SEPT + 3600, got["held_at"])
        self.assertEqual(1, got["held_rounds"])

    def test_unreadable_state_is_an_empty_state_not_a_crash(self):
        with io.open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{не json")
        rep = self.tick(LIVE_1309, SEPT)
        self.assertEqual(pp.DIVERGED, rep["state"])

    def test_dry_run_writes_nothing(self):
        self.tick(LIVE_1309, SEPT, write=False)
        self.assertFalse(os.path.exists(self.path), "сухой ход состояния НЕ пишет")

    def test_the_floor_saves_the_door_but_never_the_verdict(self):
        """Пол каденции спрашивается ДО двери и только когда фактов не подали."""
        self.tick(LIVE_1309, SEPT)
        early = pr.tick(root=self.root, now=SEPT + 60)
        self.assertIn("рано", early["why"])
        self.assertFalse(early["asked"], "в дверь на раннем обороте не ходим")

    def test_the_switch_kills_the_device_entirely(self):
        rep = pr.tick(root=self.root, now=SEPT, facts=LIVE_1309, env={pr.OFF_ENV: "1"})
        self.assertFalse(rep["acted"])
        self.assertIn("выключен", rep["why"])
        self.assertFalse(os.path.exists(self.path))


# ═════════════════════════ ВИТРИНА ═══════════════════════════════════════════

class TestVitrina(_Tmp):

    def test_the_row_reaches_the_owner_section_of_the_showcase(self):
        import vitrina_pc as vp
        import vitrina_pc_run as vr

        self.tick(LIVE_1309, SEPT)
        rows = vr.slider_rows(self.root, now=SEPT + 60)
        self.assertEqual(1, len(rows))
        self.assertIn("РАСХОЖДЕНИЕ", rows[0])
        part = vp.part_owner([], sliders=rows)
        self.assertTrue(any("РАСХОЖДЕНИЕ" in r for r in part), part)

    def test_the_showcase_is_silent_when_the_handles_agree(self):
        import vitrina_pc_run as vr

        self.tick(LIVE_1509, SEPT)
        self.assertEqual([], vr.slider_rows(self.root, now=SEPT + 60),
                         "совпадает — прибор молчит, и витрина о нём ничего не пишет")

    def test_the_showcase_says_so_when_the_device_never_reported(self):
        import vitrina_pc_run as vr

        rows = vr.slider_rows(self.root, now=SEPT)
        self.assertEqual(1, len(rows))
        self.assertIn("НЕИЗВЕСТНО", rows[0], "молчание прибора «всё хорошо» не значит")

    def test_the_old_owner_section_is_not_weakened(self):
        """Без строк прибора раздел выглядит РОВНО как до 15.09."""
        import vitrina_pc as vp

        self.assertEqual(["решения владельца не ждёт ничего"], vp.part_owner([]))
        self.assertEqual(["решения владельца не ждёт ничего"], vp.part_owner([], sliders=[]))
        self.assertEqual(1, len(vp.part_owner(None)))

    def test_a_stale_device_does_not_show_its_past_as_the_present(self):
        import vitrina_pc_run as vr

        self.tick(LIVE_1309, SEPT)
        rows = vr.slider_rows(self.root, now=SEPT + 10 * 24 * 3600)
        self.assertIn("могли устареть", rows[0])

    def test_the_showcase_does_not_import_the_device(self):
        """Витрина читает ФАЙЛ, а не код: её виток обязан остаться без сети."""
        src = _src("vitrina_pc_run.py") + _src("vitrina_pc.py")
        self.assertNotIn("import polzunki", src)


# ═════════════════════════ ЖИВОЙ ВЫЗЫВАЮЩИЙ ══════════════════════════════════

class TestCaller(unittest.TestCase):

    def test_the_daemon_has_the_call_and_it_stands_in_the_turn(self):
        src = _src("pc_orchestrator.py")
        self.assertIn("def maybe_polzunki(", src)
        self.assertIn("maybe_polzunki()", src, "прибор, которого никто не зовёт, работой не является")

    def test_the_device_is_registered_as_a_lazy_leaf(self):
        """Не внесённый в реестр лист роняет сторожа замыкания; внесённый дважды — тоже."""
        import pc_orchestrator as o

        for name in ("polzunki_pc.py", "polzunki_pc_run.py"):
            self.assertIn(name, o._ORCH_LAZY_UNCOVERED)
            self.assertEqual(1, list(o._ORCH_LAZY_UNCOVERED).count(name))
            self.assertNotIn(name, o._ORCH_RUNTIME,
                             "ленивый лист в безусловном замыкании — демон грузил бы его на старте")

    def test_the_caller_sends_nothing_outward(self):
        """У заглядыша нет ни одной двери наружу: выход — витрина и журнал."""
        src = _src("pc_orchestrator.py")
        body = src.split("def maybe_polzunki(")[1].split("\ndef ")[0]
        for bad in ("deliver(", "send_topic", "notify(", "sendMessage"):
            self.assertNotIn(bad, body)
        self.assertIn("_cowork(", body, "строка журнала — единственный второй выход")

    def test_the_freshness_guard_has_no_schedule_and_that_is_said_out_loud(self):
        """Пункт 4 задания исполнен ВСЛУХ: почему расписание взято не у сторожа свежести."""
        src = _src("pc_orchestrator.py")
        body = src.split("ПОЛЗУНКИ ГЛОБАЛЬНОЙ СКИДКИ")[1][:2500]
        self.assertIn("Расписания у сторожа свежести НЕТ", body)
        self.assertIn("TurboBabyPriceQuench", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
