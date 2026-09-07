# -*- coding: utf-8 -*-
"""Регресс СЧЁТА СЕРИИ полосы ПК + три инварианта.

Пять вещей, ради которых этот файл существует:
  1. ПРАВИЛО ЧИСТОТЫ НЕ СВОЁ, А СУДЬИНО — и это сверено С ЖИВЫМ ЛИТЕРАЛОМ, а не с копией:
     набор импортирует судью адреса и требует, чтобы `ДОКАЗАН` счётчика был ТЕМ ЖЕ словом, а
     оба остальных его вердикта чистыми НЕ признавались. Разойдутся — падение здесь, а не
     молчаливое расхождение двух счётов (класс полосы: два экземпляра одного слова).
  2. ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПРОГОНЯЕТСЯ СКВОЗЬ ЖИВОГО СУДЬЮ, а не на литерале вердикта: адрес
     корня уводится В ПУСТОТУ (файла нет · файл пуст · хеша нет), вердикт добывается настоящим
     `judge()`, и уже он подаётся в счёт. Проверять счётчик на самодельном слове значило бы
     проверять свою же выдумку.
  3. ГОЛДЕНЫ ПРИЗНАКА СЛУЖЕБНОСТИ — ДОСЛОВНЫЕ ТЕКСТЫ ЖИВОЙ ОЧЕРЕДИ (правило-класс полосы):
     `[ревизор дата=… класс=…]` и `[ревизор-находки] сводная карточка находок ревизора` (эта
     строка стоя́ла в очереди ряд #4 в день захода), а в негативах — настоящие тексты рабочих
     заданий, включая ведущую строку `ultrathink`, с которой они приходят.
  4. ФОРМАТ МЕТОК = ЖИВОЙ ФОРМАТ ОЧЕРЕДИ: `2026-08-14T15:30:22.664Z` (с суффиксом `Z`) против
     метки включения вида `2026-08-17 09:00:00` (так отдаёт `datetime('now')`). Смесь именно
     такая и есть в бою, поэтому она в наборе, а не идеализированная одна форма.
  5. ГРАНИЦЫ ДЕРЖАТСЯ УСТРОЙСТВОМ: ast-разбор доказывает, что чистый слой не умеет ничего кроме
     арифметики над переданным, что разностей времени на Python в модуле НОЛЬ и что сам счётчик
     ИЗ БОЕВОГО ХОДА НЕ ЗОВЁТСЯ. И проверяется, что каждый инвариант ЛОВИТ внесённое нарушение.

Запуск — тем же способом, что и весь гейт (способ запуска — часть формата):
    venv\\Scripts\\python.exe -m unittest test_series_pc
"""
import ast
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

import result_judge_pc as rj
import series_pc as sp

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "series_pc.py")
MODULE = "series_pc"

# ── ЖИВЫЕ МЕТКИ ПОЛОСЫ. Слева формат очереди (`Z`), справа формат `datetime('now')`. ──────────
SINCE = "2026-08-17 09:00:00"
BEFORE = "2026-08-17T08:59:59.000Z"
AT1 = "2026-08-17T09:00:01.000Z"
AT2 = "2026-08-17T09:00:02.000Z"
AT3 = "2026-08-17T09:00:03.000Z"
AT4 = "2026-08-17T09:00:04.000Z"
NOW = "2026-08-17 10:00:00"

# ── ДОСЛОВНЫЕ ТЕКСТЫ ЖИВОЙ ОЧЕРЕДИ (снимок 11.08.2026 и живая очередь 17.08.2026) ────────────
SRV_REVIZOR = ("[ревизор дата=2026-07-28 класс=б] класс б, окно 45349667 "
               "(партнёрский чат Slava): тред про АВТО (японский седан)")
SRV_FINDINGS = "[ревизор-находки] сводная карточка находок ревизора"
SRV_CLASS_E = ("[ревизор дата=2026-07-29 класс=е] Класс е, окно client_id=1716492857: "
               "после автоприветствия Telegram Business")
WORK_SERIES = "ЗАВЕСТИ СЧЁТ СЕРИИ НА ПОЛОСЕ ПК. Зеркало VPS. Одна цель."
WORK_JUDGE = "СУДЬЯ АДРЕСА: построить его и НЕ ПОДКЛЮЧАТЬ. Пункт 2 контракта,"
WORK_GUARD = "РАЗВЕДКА ГАРДА ПОЛОСЫ ПК: по чему он судит класс операции и что"
WORK_ULTRA = "ultrathink\n\nБАЗОВАЯ ЛИНИЯ БОТА: прогнать 62 строгие пары эталона и посчитать"


def entry(key, verdict, root, closed_at):
    return sp.make_entry(key, verdict, root, closed_at)


def fresh_state():
    return sp.zero_state(SINCE)


# ═════════════════════ 1. ПРАВИЛО ЧИСТОТЫ — ТО ЖЕ, ЧТО У СУДЬИ ═══════════════════════════════

class TestCleanRuleIsBorrowed(unittest.TestCase):

    def test_the_word_is_literally_the_judges_word(self):
        self.assertEqual(sp.PROVEN, rj.PROVEN, "слово чистоты разошлось с судьёй")

    def test_only_the_proven_verdict_is_clean(self):
        self.assertIs(sp.is_clean(rj.PROVEN), True)
        self.assertIs(sp.is_clean(rj.DISPROVEN), False, "«НЕ ДОКАЗАН» признан чистым")
        self.assertIs(sp.is_clean(rj.UNKNOWN), False, "«НЕИЗВЕСТНО» признан чистым")

    def test_all_three_verdicts_of_the_judge_are_covered(self):
        """Слов у судьи ровно три — иначе счёт судил бы по неполному списку и не знал об этом."""
        self.assertEqual(len(rj.VERDICTS), 3)
        clean = [v for v in rj.VERDICTS if sp.is_clean(v)]
        self.assertEqual(clean, [rj.PROVEN], "чистым признано не одно слово: %s" % clean)

    def test_the_counter_does_not_reimplement_the_strength_order(self):
        """Порядок силы живёт у судьи. Появись он здесь — два порядка разойдутся молча."""
        with io.open(SRC, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
        for one in ("STRENGTH", "combine", "stronger"):
            self.assertNotIn(one, names, "счётчик повторил порядок силы: %s" % one)
        body = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for word in (rj.DISPROVEN, rj.UNKNOWN):
            live = [s for s in body if word in s and not s.lstrip().startswith(MODULE)]
            self.assertEqual([s for s in live if len(s) < 40], [],
                             "слово «%s» заведено литералом в счётчике" % word)


# ═════════════════════ 2. ПРИЗНАК СЛУЖЕБНОСТИ ════════════════════════════════════════════════

class TestServiceSign(unittest.TestCase):

    def test_live_service_roots_are_caught(self):
        for text in (SRV_REVIZOR, SRV_FINDINGS, SRV_CLASS_E):
            self.assertIs(sp.is_service(text), True, "служебный корень не пойман: %s" % text[:40])

    def test_live_working_roots_are_not_caught(self):
        for text in (WORK_SERIES, WORK_JUDGE, WORK_GUARD, WORK_ULTRA):
            self.assertIs(sp.is_service(text), False, "рабочий корень пойман: %s" % text[:40])

    def test_the_sign_is_not_merely_any_bracket(self):
        """Признак — НАЗВАННЫЕ ярлыки контура, а не «текст начинается со скобки». Иначе рабочая
        задача, начатая скобкой, молча выпала бы из счёта."""
        self.assertIs(sp.is_service("[уточнить] владелец просил посчитать заново"), False)
        self.assertIs(sp.is_service("[ЗАДАЧА] поднять линию бота"), False)

    def test_absent_root_is_not_service(self):
        """Корня нет → служебным он не становится: «не знаю» не равно «своё»."""
        self.assertIs(sp.is_service(None), False)
        self.assertIs(sp.is_service(""), False)


# ═════════════════════ 3. СЧЁТ: С НУЛЯ, ПРОШЛОЕ МИМО, ДВАЖДЫ НЕ СЧИТАЕМ ══════════════════════

class TestFold(unittest.TestCase):

    def test_the_count_starts_at_zero(self):
        st = fresh_state()
        for field in (sp.STREAK, sp.RECORD, sp.CHAINS, sp.BREAKS, sp.SERVICE):
            self.assertEqual(st[field], 0, "включение дало не ноль в %s" % field)
        self.assertEqual(st[sp.SEEN], [])
        self.assertEqual(st[sp.SINCE], SINCE)

    def test_the_past_is_not_counted(self):
        """148 цепочек истории, поданных в момент включения, дают РОВНО НОЛЬ."""
        old = [entry("%d@2026-08-01T00:00:00.000Z" % i, rj.PROVEN, WORK_JUDGE, BEFORE)
               for i in range(148)]
        out, turn = sp.fold(fresh_state(), old, NOW)
        self.assertEqual(out[sp.CHAINS], 0)
        self.assertEqual(out[sp.STREAK], 0)
        self.assertEqual(out[sp.RECORD], 0)
        self.assertEqual(turn["past"], 148)
        self.assertEqual(out[sp.SEEN], [], "прошлое засчитано в учтённые")

    def test_a_clean_chain_raises_the_streak_and_the_record(self):
        out, turn = sp.fold(fresh_state(), [
            entry("11@" + AT1, rj.PROVEN, WORK_JUDGE, AT1),
            entry("12@" + AT2, rj.PROVEN, WORK_SERIES, AT2),
            entry("13@" + AT3, rj.PROVEN, WORK_GUARD, AT3)], NOW)
        self.assertEqual((out[sp.STREAK], out[sp.RECORD], out[sp.CHAINS]), (3, 3, 3))
        self.assertEqual((out[sp.BREAKS], turn["clean"]), (0, 3))
        self.assertEqual(out[sp.UPDATED], NOW)

    def test_both_non_proven_verdicts_break_the_streak(self):
        for word in (rj.DISPROVEN, rj.UNKNOWN):
            st, _ = sp.fold(fresh_state(), [entry("21@" + AT1, rj.PROVEN, WORK_JUDGE, AT1),
                                            entry("22@" + AT2, rj.PROVEN, WORK_GUARD, AT2)], NOW)
            self.assertEqual(st[sp.STREAK], 2)
            out, turn = sp.fold(st, [entry("23@" + AT3, word, WORK_SERIES, AT3)], NOW)
            self.assertEqual(out[sp.STREAK], 0, "«%s» серию не оборвал" % word)
            self.assertEqual(out[sp.RECORD], 2, "рекорд потерян при обрыве")
            self.assertEqual((out[sp.BREAKS], turn["broken"]), (1, 1))
            self.assertIn("23@", out[sp.WHY])
            self.assertIn(word, out[sp.WHY])

    def test_the_record_survives_and_the_streak_regrows(self):
        st = fresh_state()
        st, _ = sp.fold(st, [entry("%d@%s" % (i, AT1), rj.PROVEN, WORK_JUDGE,
                                   "2026-08-17T09:0%d:00.000Z" % i) for i in range(1, 5)], NOW)
        self.assertEqual((st[sp.STREAK], st[sp.RECORD]), (4, 4))
        st, _ = sp.fold(st, [entry("90@" + AT2, rj.UNKNOWN, WORK_JUDGE, AT2)], NOW)
        self.assertEqual((st[sp.STREAK], st[sp.RECORD]), (0, 4))
        st, _ = sp.fold(st, [entry("91@" + AT3, rj.PROVEN, WORK_JUDGE, AT3)], NOW)
        self.assertEqual((st[sp.STREAK], st[sp.RECORD]), (1, 4))

    def test_the_same_chain_is_never_counted_twice(self):
        one = entry("31@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)
        st, _ = sp.fold(fresh_state(), [one], NOW)
        again, turn = sp.fold(st, [one, one], NOW)
        self.assertEqual((again[sp.CHAINS], again[sp.STREAK]), (1, 1))
        self.assertEqual(turn["again"], 2)

    def test_the_key_carries_the_created_stamp_because_row_numbers_are_reused(self):
        """Номер ряда — строка листа и ПЕРЕИСПОЛЬЗУЕТСЯ (замер: корпус 11.08 доходит до 468, живая
        очередь 17.08 — 2..46). Один номер с ДРУГОЙ меткой создания — ДРУГАЯ цепочка."""
        st, _ = sp.fold(fresh_state(), [entry("7@2026-08-01T00:00:00.000Z", rj.PROVEN,
                                              WORK_JUDGE, AT1)], NOW)
        out, _ = sp.fold(st, [entry("7@2026-08-17T00:00:00.000Z", rj.PROVEN, WORK_SERIES, AT2)],
                         NOW)
        self.assertEqual(out[sp.CHAINS], 2, "переиспользованный номер съел вторую цепочку")

    def test_a_chain_without_a_close_stamp_waits_and_is_not_marked_seen(self):
        """Открытая цепочка (или цепочка с неразобранной меткой) не чистая и НЕ обрыв — она ждёт.
        И не помечается учтённой, иначе свой исход она бы уже не принесла."""
        for stamp in (None, "не-метка-вовсе"):
            out, turn = sp.fold(fresh_state(),
                                [entry("41@x", rj.PROVEN, WORK_JUDGE, stamp)], NOW)
            self.assertEqual((out[sp.CHAINS], out[sp.BREAKS], out[sp.STREAK]), (0, 0, 0))
            self.assertEqual(turn["waiting"], 1, "метка %r не дала «ждёт»" % stamp)
            self.assertEqual(out[sp.SEEN], [], "ждущая цепочка помечена учтённой")
        st, _ = sp.fold(fresh_state(), [entry("41@x", rj.PROVEN, WORK_JUDGE, None)], NOW)
        out, _ = sp.fold(st, [entry("41@x", rj.PROVEN, WORK_JUDGE, AT1)], NOW)
        self.assertEqual(out[sp.CHAINS], 1, "дождавшаяся цепочка в счёт не попала")

    def test_the_order_of_folding_does_not_depend_on_the_order_of_feeding(self):
        rows = [entry("51@" + AT1, rj.PROVEN, WORK_JUDGE, AT1),
                entry("52@" + AT2, rj.UNKNOWN, WORK_GUARD, AT2),
                entry("53@" + AT3, rj.PROVEN, WORK_SERIES, AT3)]
        straight, _ = sp.fold(fresh_state(), rows, NOW)
        back, _ = sp.fold(fresh_state(), list(reversed(rows)), NOW)
        self.assertEqual(straight, back, "порядок подачи изменил счёт")
        self.assertEqual((straight[sp.STREAK], straight[sp.RECORD], straight[sp.BREAKS]),
                         (1, 1, 1))

    def test_the_seen_tail_is_bounded(self):
        many = [entry("%d@%s" % (i, AT1), rj.PROVEN, WORK_JUDGE, AT1)
                for i in range(sp.SEEN_MAX + 40)]
        out, _ = sp.fold(fresh_state(), many, NOW)
        self.assertEqual(len(out[sp.SEEN]), sp.SEEN_MAX)
        self.assertEqual(out[sp.CHAINS], sp.SEEN_MAX + 40, "цепочки потеряны вместе с ключами")

    def test_fold_does_not_mutate_what_it_was_given(self):
        st = fresh_state()
        rows = [entry("61@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)]
        before_state = json.dumps(st, ensure_ascii=False, sort_keys=True)
        before_rows = json.dumps(rows, ensure_ascii=False, sort_keys=True)
        sp.fold(st, rows, NOW)
        self.assertEqual(json.dumps(st, ensure_ascii=False, sort_keys=True), before_state)
        self.assertEqual(json.dumps(rows, ensure_ascii=False, sort_keys=True), before_rows)


# ═════════════════════ ЗАМОК B: АДРЕС В ПУСТОТУ В ЧИСТЫЕ НЕ ПОПАДАЕТ ═════════════════════════

class TestVoidAddressNeverCounts(unittest.TestCase):
    """Вердикт добывается ЖИВЫМ судьёй, а не назначается литералом: иначе набор проверял бы
    собственную выдумку, а не правило."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="turbobaby_series_")
        self.ctx = rj.Ctx(repo=self.tmp)

    def _judge(self, ref):
        return rj.judge(ref, self.ctx)["verdict"]

    def test_three_kinds_of_void_all_break_the_series(self):
        empty = os.path.join(self.tmp, "пусто.md")
        with io.open(empty, "w", encoding="utf-8") as handle:
            handle.write("")
        cases = [("файла нет", "file нет-такого-файла-нигде.md"),
                 ("файл пуст", "file пусто.md"),
                 ("хеша нет", "commit 0000000000000000000000000000000000000123")]
        st = fresh_state()
        st, _ = sp.fold(st, [entry("71@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)], NOW)
        self.assertEqual(st[sp.STREAK], 1)
        for number, (name, ref) in enumerate(cases):
            verdict = self._judge(ref)
            self.assertIn(verdict, (rj.DISPROVEN, rj.UNKNOWN),
                          "«%s» дал зелёный вердикт: %s" % (name, verdict))
            self.assertIs(sp.is_clean(verdict), False, "«%s» признан чистым" % name)
            out, turn = sp.fold(st, [entry("8%d@%s" % (number, AT2), verdict, WORK_GUARD, AT2)],
                                NOW)
            self.assertEqual(out[sp.STREAK], 0, "«%s» серию не оборвал" % name)
            self.assertEqual(turn["broken"], 1)

    def test_the_same_road_with_a_real_product_does_count(self):
        """Обратная сторона: если продукт по адресу ЕСТЬ, цепочка чистая. Замок, проверенный
        только с одной стороны, — молчание: он бы прошёл и у счётчика, который не считает ничего."""
        real = os.path.join(self.tmp, "есть.md")
        with io.open(real, "w", encoding="utf-8") as handle:
            handle.write("тело артефакта")
        verdict = self._judge("file есть.md")
        self.assertEqual(verdict, rj.PROVEN)
        out, turn = sp.fold(fresh_state(), [entry("99@" + AT1, verdict, WORK_JUDGE, AT1)], NOW)
        self.assertEqual((out[sp.STREAK], out[sp.CHAINS], turn["clean"]), (1, 1, 1))


# ═════════════════════ ЗАМОК C: СЛУЖЕБНЫЕ НЕ ПОДНИМАЮТ СЕРИЮ ═════════════════════════════════

class TestServiceRootsDoNotMoveTheSeries(unittest.TestCase):

    def test_seven_service_roots_all_proven_raise_nothing(self):
        """Число взято с корпуса: признак ловит 7 корней из 148. Подаём все семь ДОКАЗАННЫМИ —
        самый выгодный для контура случай — и серия обязана остаться нулём."""
        rows = []
        for number, text in enumerate([SRV_REVIZOR, SRV_FINDINGS, SRV_CLASS_E, SRV_FINDINGS,
                                       SRV_FINDINGS, SRV_FINDINGS, SRV_REVIZOR]):
            rows.append(entry("s%d@%s" % (number, AT1), rj.PROVEN, text,
                              "2026-08-17T09:0%d:00.000Z" % number))
        out, turn = sp.fold(fresh_state(), rows, NOW)
        self.assertEqual((out[sp.STREAK], out[sp.RECORD], out[sp.CHAINS]), (0, 0, 0))
        self.assertEqual((out[sp.SERVICE], turn["service"]), (7, 7))

    def test_a_service_root_neither_raises_nor_breaks_an_existing_series(self):
        st, _ = sp.fold(fresh_state(), [entry("a1@" + AT1, rj.PROVEN, WORK_JUDGE, AT1),
                                        entry("a2@" + AT2, rj.PROVEN, WORK_GUARD, AT2)], NOW)
        self.assertEqual(st[sp.STREAK], 2)
        out, _ = sp.fold(st, [entry("a3@" + AT3, rj.DISPROVEN, SRV_FINDINGS, AT3)], NOW)
        self.assertEqual((out[sp.STREAK], out[sp.BREAKS]), (2, 0),
                         "служебный корень сдвинул серию")
        out, _ = sp.fold(out, [entry("a4@" + AT4, rj.PROVEN, WORK_SERIES, AT4)], NOW)
        self.assertEqual(out[sp.STREAK], 3, "серия не продолжилась через служебную")

    def test_service_chains_are_counted_apart_and_not_hidden(self):
        out, _ = sp.fold(fresh_state(), [entry("b1@" + AT1, rj.PROVEN, SRV_FINDINGS, AT1)], NOW)
        self.assertEqual(out[sp.SERVICE], 1, "отсев служебных не виден числом")
        self.assertIn("служебных мимо 1", sp.render(out))


# ═════════════════════ ЗАМОК D: ПРЕЖНИЕ ЧИСЛА ЦЕЛЫ ══════════════════════════════════════════

class TestFrozenPast(unittest.TestCase):

    def test_the_old_shadow_numbers_are_in_the_module_and_in_the_file(self):
        for where, box in (("модуль", sp.PAST_SHADOW), ("файл", fresh_state()[sp.FROZEN])):
            self.assertEqual(box["теневой счёт"],
                             {"серия": 0, "рекорд": 22, "доказанных": 103, "из цепочек": 148},
                             "прежние теневые числа изменились (%s)" % where)
            self.assertEqual(box["настоящий счёт"],
                             {"серия": 0, "рекорд": 17, "зелёных": 93, "из цепочек": 148},
                             "прежние настоящие числа изменились (%s)" % where)

    def test_the_old_numbers_carry_the_definition_they_were_counted_by(self):
        words = sp.PAST_SHADOW["определение"]
        for mark in ("служебные корни ВХОДИЛИ", "ВЫВЕДЕН"):
            self.assertIn(mark, words, "пометка определения не называет «%s»" % mark)
        self.assertIn("da4c065", sp.PAST_SHADOW["источник"], "источник прежних чисел не назван")

    def test_the_frozen_block_never_mixes_with_the_live_count(self):
        out, _ = sp.fold(fresh_state(), [entry("c1@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)], NOW)
        self.assertEqual(out[sp.RECORD], 1, "рекорд сложился с прежним (22)")
        self.assertEqual(out[sp.FROZEN]["теневой счёт"]["рекорд"], 22, "прежний рекорд стёрт")
        self.assertIn("не складывать", sp.render(out))


# ═════════════════════ РУКИ: ФАЙЛ СЧЁТА, ВКЛЮЧЕНИЕ, ОТКАТ ═══════════════════════════════════

class TestHands(unittest.TestCase):

    def setUp(self):
        """Набор ВЛАДЕЕТ своим окружением, а не наследует его. Это не аккуратность, а замок A:
        «сьют с включённым и выключенным счётом даёт 0 расхождений». Унаследуй эти проверки
        ручку отката из окружения — прогон под `SERIES_PC_OFF=1` дал бы другой результат, и
        замок перестал бы что-либо доказывать. Поведение выключенной ветки проверяется НЕ
        окружением прогона, а прямой подачей `env=` (см. `test_the_off_switch_kills_...`)."""
        self.dir = tempfile.mkdtemp(prefix="turbobaby_series_state_")
        self.path = os.path.join(self.dir, "series_pc.state.json")
        self.was = os.environ.get("SERIES_PC_STATE")
        self.was_off = os.environ.get("SERIES_PC_OFF")
        os.environ["SERIES_PC_STATE"] = self.path
        os.environ.pop("SERIES_PC_OFF", None)

    def tearDown(self):
        for name, value in (("SERIES_PC_STATE", self.was), ("SERIES_PC_OFF", self.was_off)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_begin_creates_the_file_at_zero(self):
        self.assertIsNone(sp.load_state(), "счёт есть до включения")
        got = sp.begin(now=SINCE)
        self.assertEqual(got["action"], "заведён")
        self.assertTrue(os.path.isfile(self.path))
        st = sp.load_state()
        self.assertEqual((st[sp.STREAK], st[sp.RECORD], st[sp.CHAINS], st[sp.BREAKS]),
                         (0, 0, 0, 0))
        self.assertEqual(st[sp.SINCE], SINCE)

    def test_begin_refuses_to_wipe_an_existing_count(self):
        sp.begin(now=SINCE)
        sp.update([entry("d1@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)], now=NOW)
        again = sp.begin(now="2026-08-18 09:00:00")
        self.assertEqual(again["action"], "уже заведён")
        self.assertEqual(sp.load_state()[sp.RECORD], 1, "рекорд стёрт повторным включением")
        forced = sp.begin(now="2026-08-18 09:00:00", force=True)
        self.assertEqual(forced["action"], "заведён")
        self.assertEqual(sp.load_state()[sp.RECORD], 0)

    def test_update_without_a_count_file_does_nothing(self):
        got = sp.update([entry("d2@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)], now=NOW)
        self.assertEqual(got["action"], "счёт не заведён")
        self.assertFalse(os.path.exists(self.path))

    def test_dry_run_does_not_touch_the_file(self):
        sp.begin(now=SINCE)
        got = sp.update([entry("d3@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)], now=NOW, dry=True)
        self.assertEqual(got["action"], "сухой прогон")
        self.assertEqual(got["state"][sp.CHAINS], 1)
        self.assertEqual(sp.load_state()[sp.CHAINS], 0, "сухой прогон записал счёт")

    def test_the_off_switch_kills_the_branch_whole(self):
        env = {"SERIES_PC_OFF": "1"}
        self.assertEqual(sp.begin(now=SINCE, env=env)["action"], "выключено")
        self.assertFalse(os.path.exists(self.path), "выключенная ветка создала файл")
        sp.begin(now=SINCE)
        got = sp.update([entry("d4@" + AT1, rj.PROVEN, WORK_JUDGE, AT1)], now=NOW, env=env)
        self.assertEqual(got["action"], "выключено")
        self.assertEqual(sp.load_state()[sp.CHAINS], 0, "выключенная ветка сложила счёт")

    def test_config_reads_the_switch_the_same_way_as_its_neighbours(self):
        for raw in ("1", "true", "да", "0 1"):
            self.assertIs(sp.config({"SERIES_PC_OFF": raw})["off"], True, raw)
        for raw in ("", "0", "   "):
            self.assertIs(sp.config({"SERIES_PC_OFF": raw})["off"], False, repr(raw))
        self.assertIs(sp.config({})["off"], False)

    def test_a_broken_state_file_is_not_read_as_an_empty_count(self):
        """Мусор в файле → «счёта нет», а НЕ «счёт с нулями»: второе стёрло бы рекорд молча."""
        with io.open(self.path, "w", encoding="utf-8") as handle:
            handle.write("[не словарь вовсе")
        self.assertIsNone(sp.load_state())
        self.assertEqual(sp.update([], now=NOW)["action"], "счёт не заведён")

    def test_the_cli_says_what_it_did(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sp.main(["--begin"])
            sp.main([])
        text = out.getvalue()
        self.assertIn("заведён", text)
        self.assertIn("серия сейчас   0", text)
        self.assertIn("рекорд         0", text)

    def test_the_cli_feeds_from_a_file_in_the_live_shape(self):
        sp.begin(now=SINCE)
        feed = os.path.join(self.dir, "feed.json")
        with io.open(feed, "w", encoding="utf-8") as handle:
            json.dump([{"key": "e1@" + AT1, "verdict": rj.PROVEN, "root": WORK_JUDGE,
                        "closed_at": AT1},
                       {"key": "e2@" + AT2, "verdict": rj.PROVEN, "root": SRV_FINDINGS,
                        "closed_at": AT2}], handle, ensure_ascii=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sp.main(["--feed", feed])
        self.assertIn("сложено", out.getvalue())
        st = sp.load_state()
        self.assertEqual((st[sp.CHAINS], st[sp.STREAK], st[sp.SERVICE]), (1, 1, 1))

    def test_render_of_a_missing_count_does_not_pretend(self):
        self.assertEqual(sp.render(None), "файла счёта нет")


# ═════════════════════ ИНВАРИАНТ SERIES_PC_JULIANDAY ════════════════════════════════════════

class TestJulianDayOnly(unittest.TestCase):
    """ЗАМОК МЕТОДА. Разности времени считает SQLite, а не Python, — и это проверяется
    устройством модуля, а не обещанием: ни одного узла вычитания в дереве разбора."""

    def _tree(self):
        with io.open(SRC, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_not_a_single_python_subtraction_in_the_module(self):
        subs = [n.lineno for n in ast.walk(self._tree())
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub)]
        self.assertEqual(subs, [], "вычитание на Python в строках %s" % subs)

    def test_no_time_library_is_imported_at_all(self):
        banned = {"datetime", "time", "calendar"}
        seen = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                seen.extend(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                seen.append((node.module or "").split(".")[0])
        self.assertEqual(banned.intersection(seen), set(), "импорт времени: %s" % seen)
        attrs = [n.attr for n in ast.walk(self._tree()) if isinstance(n, ast.Attribute)]
        for name in ("timedelta", "total_seconds", "monotonic", "perf_counter", "mktime"):
            self.assertNotIn(name, attrs, name)

    def test_every_sql_literal_is_a_select(self):
        bad = []
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                head = node.value.strip().upper()
                for word in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "PRAGMA",
                             "ATTACH"):
                    if head.startswith(word + " "):
                        bad.append((node.lineno, node.value))
        self.assertEqual(bad, [], "не читающий SQL в счётчике: %s" % bad)

    def test_the_only_database_is_memory(self):
        """Файл под время НЕ ОТКРЫВАЕТСЯ: единственный аргумент `connect` — `:memory:`."""
        opened = [n.args[0].value for n in ast.walk(self._tree())
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "connect" and n.args
                  and isinstance(n.args[0], ast.Constant)]
        self.assertTrue(opened, "вызовов connect не нашлось — инвариант пуст")
        self.assertEqual(set(opened), {":memory:"}, "счётчик открыл базу файлом: %s" % opened)

    def test_julianday_is_what_actually_counts_the_difference(self):
        self.assertAlmostEqual(sp.days_between("2026-08-16 00:00:00", "2026-08-15 00:00:00"),
                               1.0, places=6)
        self.assertAlmostEqual(sp.days_between("2026-08-15 00:00:00", "2026-08-16 00:00:00"),
                               -1.0, places=6)

    def test_it_reads_the_live_stamp_shapes_of_this_lane(self):
        """Метка очереди (`…Z`), метка `datetime('now')` (пробел) и метка со смещением — одна
        шкала. Молчаливая потеря суффикса или пояса дала бы ошибку знака на границе включения."""
        self.assertAlmostEqual(sp.days_between("2026-08-17T00:00:00.000Z", "2026-08-16 00:00:00"),
                               1.0, places=6)
        self.assertAlmostEqual(sp.days_between("2026-08-17T07:00:00+07:00",
                                               "2026-08-17 00:00:00"), 0.0, places=6)

    def test_an_unreadable_stamp_answers_i_do_not_know(self):
        self.assertIsNone(sp.days_between("не метка", "2026-08-16 00:00:00"))
        self.assertIsNone(sp.days_between("2026-08-16 00:00:00", None))
        self.assertIsNone(sp.not_before("мусор", SINCE))
        self.assertIs(sp.not_before(AT1, SINCE), True)
        self.assertIs(sp.not_before(BEFORE, SINCE), False)

    def test_the_invariant_catches_an_injected_subtraction(self):
        """Инвариант, не ловящий подлог, — молчание. Вносим вычитание и требуем находку."""
        tree = ast.parse("def f(a, b):\n    return a - b\n")
        subs = [n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub)]
        self.assertTrue(subs, "проверка вычитания не ловит вычитание")


# ═════════════════════ ИНВАРИАНТ SERIES_PC_PURE ═════════════════════════════════════════════

_PURE = ("is_clean", "is_service", "make_entry", "zero_state", "_order", "_seen_tail", "fold",
         "render", "days_between", "not_before")
# `sqlite3` НЕ в запретных СОЗНАТЕЛЬНО: это единственный инструмент времени полосы (замок
# метода), и отдельный инвариант выше требует, чтобы база была только `:memory:`, а SQL —
# только `SELECT`. `os`/`open`/`log_setup` — руки, им в чистом слое места нет.
_BANNED_ROOTS = frozenset(("os", "sys", "subprocess", "socket", "urllib", "requests", "http",
                           "time", "datetime", "shutil", "pathlib", "tempfile", "log_setup",
                           "bridge_http", "brain_writer", "pc_orchestrator", "logging"))
_BANNED_CALLS = frozenset(("open", "exec", "eval", "compile", "__import__", "input", "print",
                           "now_stamp"))
# Слова, которыми в этой системе МЕНЯЮТ мир, — их не должно быть во ВСЁМ модуле.
_BANNED_ANYWHERE = frozenset(("claim_task", "complete_task", "enqueue_task", "approve_task",
                              "task_heartbeat", "set_needs_approval", "write_doc", "taskkill",
                              "schtasks", "rmtree", "unlink", "kill", "system", "Popen"))


def pure_findings(tree, names):
    """→ места, где ЧИСТЫЙ слой умеет больше арифметики над переданным. Пусто = граница цела."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in names:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                    and inner.func.id in _BANNED_CALLS:
                out.append("%s:%d вызов «%s»" % (node.name, inner.lineno, inner.func.id))
            if isinstance(inner, ast.Name) and inner.id in _BANNED_ROOTS:
                out.append("%s:%d имя «%s»" % (node.name, inner.lineno, inner.id))
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str) \
                    and inner.value.strip().lower() == "now":
                out.append("%s:%d чтение часов" % (node.name, inner.lineno))
    return out


class TestPureLayer(unittest.TestCase):

    def _tree(self):
        with io.open(SRC, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_the_pure_layer_owns_no_tools(self):
        found = pure_findings(self._tree(), _PURE)
        self.assertEqual(found, [], "чистый слой умеет лишнее: %s" % found)

    def test_every_named_pure_function_actually_exists(self):
        """Инвариант по списку имён молчит, если имя опечатано. Сверяем со живым модулем."""
        for name in _PURE:
            self.assertTrue(callable(getattr(sp, name, None)), "нет функции %s" % name)

    def test_the_invariant_catches_an_injected_impurity(self):
        cases = [("чтение файла", "def fold(a):\n    return open(a).read()\n"),
                 ("часы внутри", "def fold(a):\n    return q('now')\n"),
                 ("окружение", "def fold(a):\n    return os.environ\n")]
        for name, src in cases:
            self.assertTrue(pure_findings(ast.parse(src), ("fold",)),
                            "подлог «%s» инвариант не поймал" % name)
        self.assertEqual(pure_findings(ast.parse("def fold(a):\n    return a + 1\n"), ("fold",)),
                         [], "законный код инвариант флагать не должен")

    def test_the_module_as_a_whole_cannot_move_the_world(self):
        with io.open(SRC, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = set(n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute))
        names.update(n.id for n in ast.walk(tree) if isinstance(n, ast.Name))
        hit = _BANNED_ANYWHERE.intersection(names)
        self.assertEqual(hit, set(), "счётчик умеет менять мир: %s" % hit)

    def test_the_counter_never_reads_the_queue_or_the_brain(self):
        seen = []
        tree = self._tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                seen.extend(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                seen.append((node.module or "").split(".")[0])
        for one in ("bridge_http", "urllib", "socket", "requests", "brain_writer",
                    "cowork_log_append", "pc_orchestrator", "moderation_ipc"):
            self.assertNotIn(one, seen, "счётчик пошёл наружу: %s" % one)


# ═════════════════════ ИНВАРИАНТ SERIES_COUNTER_WIRING_IS_NAMED ═════════════════════════════
# ХОД ЦЕПИ НЕ ТРОНУТ — числом, а не обещанием. Проза освобождена по той же границе, что и у
# судьи (действие, не подстрока).
#
# ЧТО УСТАРЕЛО И ПОЧЕМУ (02.09.2026). Инвариант звался `SERIES_COUNTER_UNWIRED` и требовал НОЛЬ
# обращений из боевого кода: счётчик был построен и сознательно не подключён (пункт 2 контракта
# 17.08 — «построить и НЕ ПОДКЛЮЧАТЬ»). 02.09.2026 владелец счётчик ПОДКЛЮЧИЛ: коммиты `8238d91`
# и `64acb2b` завели сводку контура, и она берёт у счётчика ПРИЗНАК СЛУЖЕБНОГО КОРНЯ
# (`contour_digest.py:124` — `import series_pc`). Требовать сегодня ноль обращений значит
# требовать отката решения владельца, а не охранять предмет.
#
# ПРЕДМЕТ ОХРАНЫ ТОТ ЖЕ, И СТРОГОСТЬ НЕ СНИЖЕНА. Опасность была не в самом факте вызова, а в том,
# что счётчик въедет в боевой ход НЕЗАМЕТНО и оттуда — куда попало. Поэтому список мест, которым
# звать счётчик РАЗРЕШЕНО, назван ПОИМЁННО и коротко; любой другой боевой файл, назвавший
# счётчик, роняет тест ровно как прежде. Список — это ПЕРЕЧЕНЬ ОСОЗНАННЫХ РЕШЕНИЙ, и пятое имя
# в нём заводят так же осознанно, как заводили второе.
_WIRING_ALLOWED = {
    # 02.09.2026, `8238d91`/`64acb2b`: сводка контура берёт признак служебного корня. Литералом
    # его набирать нельзя — два экземпляра одной регулярки расходятся молча.
    "contour_digest.py": "сводка контура: признак служебного корня",
    # руки сводки: имя счётчика стои́т в осях движения (`AXIS3_PATHS`) — строкой, не импортом.
    "contour_digest_run.py": "руки сводки: имя файла в осях движения",
    # демон: имя в СПИСКЕ МОДУЛЕЙ self-update (лист куста `maybe_contour_digest`), строкой.
    # Импорта счётчика у демона нет и быть не должно — это проверяется отдельно, см. ниже.
    "pc_orchestrator.py": "self-update: лист import-замыкания сводки",
}

def prod_sources(repo):
    """Боевые `*.py` В КОРНЕ (не рекурсивно, `test_*.py` не в счёте), ТОЛЬКО ОТСЛЕЖИВАЕМЫЕ git.

    Отслеживаемые — потому что в дереве полосы лежат мины: `manager-bot/` (июньский снимок VPS),
    архивные копии гарда, `_tmp_*.py` и `_scratch_*` разведки. Тот же метод, каким считает
    страж неразбора и инвариант судьи, — три стража обязаны мерить ОДНО множество."""
    try:
        done = subprocess.run(["git", "ls-files", "*.py"], cwd=repo, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    out = []
    for line in done.stdout.splitlines():
        name = line.strip()
        if len(name) == 0 or "/" in name or name.startswith("test_"):
            continue
        if name == MODULE + ".py":
            continue
        out.append(os.path.join(repo, name))
    return out


def docstring_nodes(tree):
    """Узлы-докстринги: текст в них — ПРОЗА, а не код (см. тот же приём у инварианта судьи)."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if len(body) == 0 or not isinstance(body[0], ast.Expr):
            continue
        first = body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.add(id(first))
    return out


def wiring_findings(src, where="<строка>"):
    """→ список мест, где боевой код зовёт счётчик. Пустой = счётчик не подключён."""
    out = []
    tree = ast.parse(src)
    prose = docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) in prose:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == MODULE:
                    out.append("%s:%d импорт «%s»" % (where, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == MODULE:
                out.append("%s:%d импорт из «%s»" % (where, node.lineno, node.module))
        elif isinstance(node, ast.Name) and node.id == MODULE:
            out.append("%s:%d имя «%s» в коде" % (where, node.lineno, MODULE))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and MODULE in node.value:
            out.append("%s:%d строка с именем «%s»" % (where, node.lineno, MODULE))
    return out


class TestCounterWiringIsNamed(unittest.TestCase):
    """FAIL-CLOSED: git не ответил или файлов ноль → провал, а не «нарушений нет»."""

    def test_only_the_named_places_call_the_counter(self):
        """Счётчик зовут РОВНО названные места. Новое имя в боевом ходе — красный гейт."""
        files = prod_sources(REPO)
        self.assertIsNotNone(files, "git ls-files не ответил — множество боевых файлов неизвестно")
        self.assertGreater(len(files), 30, "боевых файлов подозрительно мало: %d" % len(files))
        found = {}
        for path in files:
            with io.open(path, encoding="utf-8") as handle:
                hits = wiring_findings(handle.read(), os.path.basename(path))
            if hits:
                found[os.path.basename(path)] = hits
        stranger = sorted(set(found) - set(_WIRING_ALLOWED))
        self.assertEqual(stranger, [], "СЧЁТЧИК ВЪЕХАЛ В НЕНАЗВАННОЕ МЕСТО: %s"
                         % {k: found[k] for k in stranger})
        # И обратное: место, названное разрешённым, обязано счётчик ДЕЙСТВИТЕЛЬНО звать. Иначе
        # список тихо превратится в амнистию «на будущее» и перестанет быть перечнем решений.
        idle = sorted(set(_WIRING_ALLOWED) - set(found))
        self.assertEqual(idle, [], "имя в списке разрешённых, а вызова нет — список протух: %s"
                         % idle)

    def test_the_named_list_catches_a_stranger(self):
        """ОТРИЦАТЕЛЬНЫЙ: подключение из НЕназванного файла ловится и после правки.

        Кормим тем же прибором, каким считает живой тест, — синтетическим боевым файлом. Боевого
        дерева правка не касается ни байтом."""
        for name, src in (("прямой импорт", "import series_pc\n"),
                          ("переименование", "import series_pc as sp\n"),
                          ("частичный", "from series_pc import fold\n"),
                          ("динамика строкой", "m = __import__('series_pc')\n")):
            with self.subTest(name):
                hits = wiring_findings(src, "userbot_listen.py")
                self.assertTrue(hits, "подлог «%s» не пойман" % name)
                self.assertNotIn("userbot_listen.py", _WIRING_ALLOWED,
                                 "чужак попал в список разрешённых")

    def test_the_invariant_catches_an_injected_wiring(self):
        for name, src in [("прямой импорт", "import series_pc\n"),
                          ("переименование", "import series_pc as sp\n"),
                          ("частичный", "from series_pc import fold\n"),
                          ("динамика строкой", "m = __import__('series_pc')\n")]:
            self.assertTrue(wiring_findings(src), "подлог «%s» не пойман" % name)
        self.assertEqual(wiring_findings('"""про series_pc словами"""\nX = 1\n'), [],
                         "проза помечена подключением")

    def test_the_daemon_names_the_counter_but_never_imports_it(self):
        """Демон знает имя счётчика РОВНО ОДНИМ способом — строкой в списке модулей self-update.

        ЧТО УСТАРЕЛО. Сторож требовал, чтобы имени счётчика в демоне не было ВООБЩЕ («попади имя
        в import-замыкание, любая правка счётчика перезапускала бы демона»). 02.09.2026 сводка
        контура поехала ленивым `import contour_digest_run` из `maybe_contour_digest`, и её лист
        `series_pc.py` обязан стоять в списке self-update — иначе демон крутил бы старый код
        сводки. Перезапуск на правку счётчика стал ЗАКОННОЙ ценой подключения, и её платит
        владелец, а не молчание.

        ПРЕДМЕТ ОХРАНЫ ЖИВ И СТРОЖЕ: имя разрешено ровно как ДАННЫЕ (строковый литерал в списке),
        а вот ИМПОРТА счётчика демоном по-прежнему быть не должно ни одного — иначе счётчик въехал
        бы в память живого демона и стал бы частью его хода, а не листом чужого куста."""
        with io.open(os.path.join(REPO, "pc_orchestrator.py"), encoding="utf-8") as handle:
            text = handle.read()
        tree = ast.parse(text)
        prose = docstring_nodes(tree)
        imports, names, literals = [], [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and id(node) in prose:
                continue
            if isinstance(node, ast.Import):
                imports.extend(node.lineno for a in node.names
                               if a.name.split(".")[0] == MODULE)
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == MODULE:
                    imports.append(node.lineno)
            elif isinstance(node, ast.Name) and node.id == MODULE:
                names.append(node.lineno)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and MODULE in node.value:
                literals.append((node.lineno, node.value))
        self.assertEqual(imports, [], "демон ИМПОРТИРУЕТ счётчик: строки %s" % imports)
        self.assertEqual(names, [], "имя счётчика стои́т в коде демона: строки %s" % names)
        self.assertEqual([v for _l, v in literals], [MODULE + ".py"],
                         "имя счётчика в демоне не только строкой списка self-update: %s"
                         % literals)

    def test_the_daemon_check_catches_a_real_import(self):
        """ОТРИЦАТЕЛЬНЫЙ: настоящий импорт счётчика демоном обязан ловиться, строка — нет."""
        self.assertTrue(wiring_findings("import series_pc\n", "pc_orchestrator.py"))
        self.assertTrue(wiring_findings("from series_pc import fold\n", "pc_orchestrator.py"))
        self.assertEqual(wiring_findings('"""лист куста series_pc.py"""\nX = 1\n',
                                         "pc_orchestrator.py"), [],
                         "проза о счётчике помечена подключением")

    def test_the_counter_does_not_name_the_judge_in_live_code(self):
        """Судья лежит НЕ ПОДКЛЮЧЁННЫМ. Счётчик объясняет родство правила ПРОЗОЙ (это законно и
        обязательно), но в живом коде имени судьи быть не должно — иначе счётчик втащил бы его
        в боевое дерево и снял чужой замок."""
        with io.open(SRC, encoding="utf-8") as handle:
            text = handle.read()
        tree = ast.parse(text)
        prose = docstring_nodes(tree)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and id(node) in prose:
                continue
            if isinstance(node, ast.Name) and node.id == "result_judge_pc":
                found.append(node.lineno)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "result_judge_pc" in node.value:
                found.append(node.lineno)
            if isinstance(node, ast.Import):
                found.extend(node.lineno for a in node.names
                             if a.name.split(".")[0] == "result_judge_pc")
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "result_judge_pc":
                    found.append(node.lineno)
        self.assertEqual(found, [], "имя судьи в живом коде счётчика: строки %s" % found)


if __name__ == "__main__":
    unittest.main()
