# -*- coding: utf-8 -*-
"""Юниты ПРАВИЛА ПОРОГА: паспорт рядом со значением и механизм, который краснеет без него.

ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ, А НЕ ДЕКЛАРИРУЕТСЯ:

  1. МЕХАНИЗМ ПАДАЕТ ОТ СНЯТОЙ ЗАПИСИ. Это ГЛАВНЫЙ тест набора (требование п.2 задания Штаба
     06.09): у живого файла отнимается настоящий паспорт настоящего порога — и вердикт обязан
     стать КРАСНЫМ, назвав имя порога. Без этого теста «механизм краснеет» было бы обещанием,
     а не свойством.
  2. НЕЗНАНИЕ НЕ КРАСИТСЯ В ЗЕЛЁНЫЙ. Непрочитанный файл — КРАСНОЕ, а не «порогов там нет»;
     полупаспорт — КРАСНОЕ, а не «ну хоть что-то»; протухший замер — КРАСНОЕ.
  3. ЧИСЛО НЕ КРУГЛОЕ. Горизонт протухания сверяется с литералами СВОЕГО замера — двух
     наблюдённых протуханий полосы (16 и 24 суток). Разъедется код с замером — покраснеет
     тест, а не полоса через месяц.
  4. СЛЕД СРАБАТЫВАНИЯ ЕСТЬ И НЕСЁТ ЧИСЛО. Сработавший дедлайн обязан сказать, СКОЛЬКО он
     выбросил (п.5 задания). Проверяется живым построением с укороченным дедлайном, а не
     чтением кода.
  5. ХРАПОВИК. Число порогов без замера и число непокрытых кандидатов не смеют расти молча:
     это единственный замок против побега в ветку «замера нет», которая по построению не
     красная.

ЖИВОЙ ФОРМАТ (правило-класс CLAUDE.md): мок двери повторяет ответ `quote_price` дословно —
`{"ok": True, "data": {day_price, total, deposit, available, days, cap_*, text}}`, ровно так
его читает боевой `pricing._normalize`. Паспорта берутся из НАСТОЯЩИХ файлов дерева, а не
сочиняются: разъедется файл — покраснеют эти тесты, и это правильно.

БОЕВОГО НИЧЕГО НЕ КАСАЕТСЯ: файлы читаются, но не правятся (мутация живёт в памяти теста),
сети нет ни одной строкой, базы и таблиц нет.
"""
import datetime
import io
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_isolation  # noqa: F401  ДО suggest: офлайн-дверь (§5 обвязки)
import threshold_ledger as tl
import threshold_ledger_run as tlr
import suggest

TODAY = datetime.date(2026, 9, 6)


def _live(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


class TestLiveTree(unittest.TestCase):
    """Живое дерево: реестр снят с настоящих файлов, а не с фикстуры."""

    def setUp(self):
        self.out = tlr.run(today=TODAY)

    def test_live_tree_has_no_red(self):
        """Ни один порог в охвате не молчит о своём основании."""
        self.assertEqual([], [(r["file"], r["name"], r["state"]) for r in self.out["red"]],
                         "красное в живом дереве: запусти threshold_ledger_run.py")

    def test_the_whole_premise_list_is_covered(self):
        """п.3 задания: ПРОЙДЕН ВЕСЬ список премисы (§5 артефакта 06.09), поимённо."""
        want = {"PROBE_DEFAULT", "ONE_DEFAULT", "_SHEET_DEADLINE", "_CLASS_OFFER_DEADLINE",
                "_SHEET_WORKERS", "_CLASS_OFFER_WORKERS", "BRIDGE_LOOP_BUDGET", "_SHEET_TTL",
                "FLEET_TTL", "TTL_DEFAULT", "HTTP_TIMEOUT", "MAX_AGE_DEFAULT",
                "daemon.bc.timeout"}
        got = {r["name"] for r in self.out["rows"]}
        self.assertEqual(set(), want - got, "порог премисы остался без паспорта")

    def test_discovery_found_more_than_the_hand_made_list(self):
        """Открытие ищет ФОРМУ, а не имена: список имён отстаёт молча, форма — нет.

        Числом: рукой в премисе названо 13 порогов, открытие нашло больше — и находки
        (SCOOTER_MIN_DAYS, MOTO_MIN_DAYS) в списке премисы не значились вовсе."""
        got = {r["name"] for r in self.out["rows"]}
        self.assertIn("SCOOTER_MIN_DAYS", got)
        self.assertIn("MOTO_MIN_DAYS", got)
        self.assertGreater(self.out["total"], 13)

    def test_the_horizon_itself_carries_a_passport(self):
        """Проверяющий покрывает СЕБЯ: горизонт — тоже общий порог.

        Судья, стоящий на непроверенном числе, был бы ровно тем дефектом, который он ловит."""
        rows = {r["name"]: r for r in self.out["rows"]}
        self.assertIn("HORIZON_LATENCY_DAYS", rows)
        self.assertEqual(tl.OK, rows["HORIZON_LATENCY_DAYS"]["state"])

    def test_uncovered_is_named_by_number_and_the_number_is_true(self):
        """Молчаливого усечения нет: непокрытое названо ЧИСЛОМ, и число сверено с деревом.

        РАСТЯЖКА, А НЕ КОНСТАНТА: 06.09 замер дал 71, 07.09 тем же прибором — 79 (кандидатов
        73 → 81, покрыто поимённо те же два). Число в модуле пересчитано, а не подогнано; сам
        механизм не тронут ни строкой, что и показывает отрицательный тест ниже."""
        text = _live("pc_orchestrator.py")
        named = set(tl.COVERED_NAMES["pc_orchestrator.py"])
        outside = len([c for c in tl.scan_text(text) if c[0] not in named])
        self.assertEqual(tl.UNCOVERED["pc_orchestrator.py"], outside,
                         "число вне охвата разъехалось с деревом — поправь UNCOVERED")
        self.assertIn(str(outside), tl.UNCOVERED_NOTE)

    def test_the_uncovered_count_catches_a_new_threshold(self):
        """ОТРИЦАТЕЛЬНЫЙ: новый порог в непокрытом файле обязан двигать число и после пересчёта.

        Кормим тем же прибором (`scan_text`) синтетическим текстом — боевой файл не трогается."""
        base = "X_TIMEOUT = 900\n"
        one = len(tl.scan_text(base))
        self.assertEqual(one, 1, "прибор не увидел даже одного порога")
        self.assertEqual(len(tl.scan_text(base + "Y_BUDGET = 30\n")), one + 1,
                         "новый порог не изменил счёт — растяжка ослепла")
        # Поимённое покрытие вычитается ровно по имени, а не по похожести.
        cands = tl.scan_text(base + "Y_BUDGET = 30\n")
        self.assertEqual(len([c for c in cands if c[0] not in {"X_TIMEOUT"}]), one,
                         "вычитание покрытого имени работает не по имени")

    def test_ratchet_of_thresholds_without_measurement(self):
        """ХРАПОВИК. «Замера нет» — законный исход, и потому в него можно СБЕЖАТЬ. Замок один:
        число таких порогов не смеет расти. Заведён 06.09 на 12; в тот же день ЗАТЯНУТ до 11 —
        `_SHEET_DEADLINE` получил свой замер (12 повторов боевой сборки). Храповик, который не
        затягивается после выясненного порога, разрешает молча вернуться назад."""
        self.assertLessEqual(self.out["counts"][tl.NO_MEASURE], 11,
                             "порогов без замера стало больше — это регресс, а не мелочь")
        self.assertLessEqual(self.out["counts"][tl.NOT_A_THRESHOLD], 1)

    def test_numbers_of_the_report_are_reproducible(self):
        """п.6 задания: числа отчёта считает КОД, а не рука. Разъедутся — покраснеет тут."""
        c = self.out["counts"]
        self.assertEqual(21, self.out["total"])
        self.assertEqual(9, c[tl.OK])            # 8 при заведении реестра + _SHEET_DEADLINE 06.09
        self.assertEqual(11, c[tl.NO_MEASURE])   # 12 при заведении − выясненный _SHEET_DEADLINE
        self.assertEqual(1, c[tl.NOT_A_THRESHOLD])
        self.assertEqual(2, len(self.out["with_unit_limit"]))


class TestMechanismGoesRed(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЕ ТЕСТЫ — сердце задания. Механизм, который не падает от снятой записи,
    механизмом не является: он просто печатает то, что и так написано."""

    def test_removing_a_real_passport_turns_the_mechanism_red(self):
        """ГЛАВНЫЙ. У ЖИВОГО файла отнимается НАСТОЯЩИЙ паспорт настоящего порога.

        Мутация живёт в памяти теста: файл на диске не правится ни байтом."""
        text = _live("price_gate.py")
        # снимаем весь блок паспорта PROBE_DEFAULT: от строки с маркером до значения
        lines, out, killing = text.splitlines(), [], False
        for ln in lines:
            s = ln.strip()
            if s.startswith("#") and tl.MARK in s and "круг из ДЕВЯТИ проб" in s:
                killing = True
            if killing and s.startswith("#"):
                continue
            killing = False
            out.append(ln)
        self.assertLess(len(out), len(lines), "паспорт не найден — тест не проверяет ничего")

        got = tl.audit({"price_gate.py": "\n".join(out)}, today=TODAY)
        self.assertFalse(got["ok"], "снятая запись НЕ уронила механизм — он бесполезен")
        red = {r["name"]: r for r in got["red"]}
        self.assertIn("PROBE_DEFAULT", red)
        self.assertEqual(tl.NO_RECORD, red["PROBE_DEFAULT"]["state"])

        # И контрфакт по звену: тот же файл БЕЗ мутации — зелёный. Различие ровно одно.
        clean = tl.audit({"price_gate.py": text}, today=TODAY)
        self.assertTrue(clean["ok"], "нетронутый файл обязан быть зелёным")

    def test_half_a_passport_is_red_not_green(self):
        """Полупаспорт ХУЖЕ отсутствия: он выглядит доказательством. Значит — красный."""
        src = ("# ПАСПОРТ ПОРОГА: терпит=что-то | замер=есть | снят=2026-09-06\n"
               "FAKE_DEADLINE = 120\n")
        got = tl.audit({"x.py": src}, today=TODAY)
        self.assertFalse(got["ok"])
        self.assertEqual(tl.BROKEN, got["red"][0]["state"])
        self.assertIn("делится", got["red"][0]["why"])

    def test_a_measurement_older_than_the_horizon_is_red(self):
        """Замер протухает. Ровно это и случилось дважды за август, и никто не заметил."""
        src = ("# ПАСПОРТ ПОРОГА: терпит=девять проб | замер=шесть кругов живой дверью\n"
               "#   | снят=2026-08-21 | делится=9 проб | предел-единицы=НЕТ: голодает последний\n"
               "FAKE_BUDGET = 90\n")
        got = tl.audit({"x.py": src}, today=TODAY)
        self.assertFalse(got["ok"])
        self.assertEqual(tl.STALE, got["red"][0]["state"])
        self.assertEqual(16, got["red"][0]["age_days"])      # ровно то протухание 21.08 → 06.09

    def test_a_date_that_is_not_a_date_is_red(self):
        """«снят=на прошлой неделе» — не дата. Красное, а не «ну примерно понятно»."""
        src = ("# ПАСПОРТ ПОРОГА: терпит=x | замер=есть | снят=на прошлой неделе\n"
               "#   | делится=x | предел-единицы=НЕТ\n"
               "FAKE_TTL = 30\n")
        got = tl.audit({"x.py": src}, today=TODAY)
        self.assertEqual(tl.BROKEN, got["red"][0]["state"])

    def test_unreadable_file_is_red_not_skipped(self):
        """ТРЕТИЙ ИСХОД. Молчание источника исправностью не является — то же правило, что у О1-О4."""
        got = tl.audit({"gone.py": None}, today=TODAY)
        self.assertFalse(got["ok"])
        self.assertEqual(tl.UNREAD, got["red"][0]["state"])

    def test_no_measurement_is_honest_and_not_red(self):
        """Обратная сторона: честное «замера нет» НЕ красное — задание прямо запрещает менять
        такое значение, а значит запрещает и красить его в аварию."""
        src = ("# ПАСПОРТ ПОРОГА: терпит=сто quote | замер=НЕТ, число круглое | снят=—\n"
               "#   | делится=quote сборки | предел-единицы=НЕТ: выбросит хвост разом\n"
               "FAKE_DEADLINE = 120\n")
        got = tl.audit({"x.py": src}, today=TODAY)
        self.assertTrue(got["ok"])
        self.assertEqual(1, got["counts"][tl.NO_MEASURE])

    def test_a_new_round_number_without_a_passport_is_caught_the_same_day(self):
        """ЗАЧЕМ ВСЁ ЭТО. Новый порог, заведённый в покрытом файле, находится САМ — открытие
        ищет форму, а не имя из списка. Список имён отстал бы молча."""
        src = _live("price_gate.py") + "\nNEW_SHINY_TIMEOUT = 300\n"
        got = tl.audit({"price_gate.py": src}, today=TODAY)
        self.assertFalse(got["ok"])
        self.assertEqual(["NEW_SHINY_TIMEOUT"], [r["name"] for r in got["red"]])

    def test_the_docstring_example_is_not_taken_for_a_threshold(self):
        """Докстрока модуля показывает форму паспорта. Если бы разбор её читал, судья уронил
        бы сам себя собственным примером — и правило умерло бы в день заведения."""
        rows = tl.scan_text(_live("threshold_ledger.py"))
        self.assertEqual(["HORIZON_LATENCY_DAYS"], [n for n, _l, _v, _p in rows])


class TestHorizonStandsOnMeasurement(unittest.TestCase):
    """Горизонт — число, которым судят другие числа. Оно обязано быть измерено само."""

    def test_horizon_is_half_of_the_shortest_observed_rot(self):
        """СВЕРКА С ЛИТЕРАЛАМИ ЗАМЕРА. Два наблюдённых протухания собственной полосы:
          · WD_PRODUCT_SILENT 1800с — замер 12.08 (a8d0b39) → опровергнут 05.09 медианой
            сборки 1841с. Между ними 24 суток.
          · PRICE_GATE_PROBE_SEC 90с — замер 20-21.08 (45a38cf) → опровергнут 06.09 медианой
            круга 95.15с. Между ними 16 суток.
        Горизонт = ПОЛОВИНА короткейшего. Доктрина двукратного запаса та же, что у ONE/PROBE
        06.09, но обращённая: у потолка запас идёт ВВЕРХ от худшего, у срока годности — ВНИЗ
        от быстрейшего."""
        rot_wd = (datetime.date(2026, 9, 5) - datetime.date(2026, 8, 12)).days
        rot_gate = (datetime.date(2026, 9, 6) - datetime.date(2026, 8, 21)).days
        self.assertEqual(24, rot_wd)
        self.assertEqual(16, rot_gate)
        self.assertEqual(min(rot_wd, rot_gate) / 2.0, tl.HORIZON_LATENCY_DAYS)

    def test_the_weakness_of_the_basis_is_said_out_loud(self):
        """n=2 — основание СЛАБОЕ, и паспорт обязан звать его слабым, а не прятать за числом."""
        passport = [p for n, _l, _v, p in tl.scan_text(_live("threshold_ledger.py"))
                    if n == "HORIZON_LATENCY_DAYS"][0]
        self.assertIn("n=2", passport)
        self.assertIn("СЛАБОЕ", passport)

    def test_owner_word_does_not_rot_by_calendar(self):
        """Род «решение» календарём не судится: частоту пересмотра цен назвал владелец, и она
        протухнет, когда он назовёт другую, а не через восемь суток."""
        self.assertIsNone(tl.horizon_days(tl.KIND_DECISION))
        src = ("# ПАСПОРТ ПОРОГА: терпит=слепок | замер=слово владельца KB_business_rules\n"
               "#   | снят=2026-01-01 | делится=НЕ ДЕЛИТСЯ | предел-единицы=НЕ НУЖЕН\n"
               "#   | род=решение\n"
               "FAKE_MAX_DAYS = 14\n")
        got = tl.audit({"x.py": src}, today=TODAY)
        self.assertTrue(got["ok"], "слово владельца не обязано протухать по календарю")


class TestFiringLeavesATrace(unittest.TestCase):
    """п.5 задания: порог, который сработал и что-то ВЫБРОСИЛ, обязан оставить запись С ЧИСЛОМ.

    Мерится живым построением с укороченным дедлайном, а не чтением кода: до правки след был,
    но построчный и без числа — размер потери приходилось добывать раскопками журнала."""

    FLEET = ("ADV 350CC GREY BKK 798", "NMAX 155CC GREY PHUKET 5960",
             "XMAX 300CC BLUE PHUKET 5773")

    def _getter(self, slow=(), delay=0.0):
        """Живой формат `quote_price` дословно (см. pricing._normalize)."""
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            bike = params.get("bike", "")
            if any(s in bike.upper() for s in slow):
                time.sleep(delay)
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            total = 500 * max(days, 1)
            return {"ok": True, "data": {"day_price": 500, "total": total, "deposit": 3000,
                                         "available": True, "days": days, "cap_active": False,
                                         "cap_price": None, "text": f"{bike} {days}d {total}"}}
        return fake

    def test_sheet_deadline_says_how_many_quotes_it_threw_away(self):
        import unittest.mock as mock
        with mock.patch.object(suggest, "_SHEET_DEADLINE", 1):
            with self.assertLogs("suggest", level="WARNING") as caught:
                suggest.price_sheet("2026-07-15",
                                    getter=self._getter(slow=("ADV",), delay=6.0))
        said = "\n".join(caught.output)
        self.assertIn("ПОРОГ СРАБОТАЛ", said)
        self.assertIn("выбросил", said)
        # ЧИСЛО, а не слово «несколько»: строка обязана нести и выброшенное, и общее.
        self.assertRegex(said, r"выбросил \d+ quote из \d+")

    def test_a_sheet_that_met_the_deadline_stays_silent(self):
        """Контрфакт по звену: тот же вход, дедлайн боевой — предупреждения НЕТ. Иначе след
        стал бы шумом и его перестали бы читать."""
        with self.assertRaises(AssertionError):
            with self.assertLogs("suggest", level="WARNING"):
                suggest.price_sheet("2026-07-16", getter=self._getter())

    def test_refusals_of_the_probes_are_counted_not_silently_truncated(self):
        """Срез `[:6]` терял хвост отказов МОЛЧА: при девяти отказах владелец видел шесть и не
        знал, что их девять. Число отказов — единственный след бюджета проб сторожа."""
        import price_freshness_run as pfr

        def refusing(action, **kw):
            raise RuntimeError("проба не уложилась в свой предел (43с)")

        facts = pfr.live_handles(get=refusing)
        self.assertEqual(9, facts["refused"], "отказы обязаны считаться все, а не первые шесть")
        self.assertIn("всего отказов 9 из 9 проб", facts["error"])
        self.assertFalse(facts["ok"])          # и цену это по-прежнему гасит

    def test_a_probe_that_answered_leaves_no_refusal_count(self):
        """Контрфакт: дверь отвечает — поля `refused` нет вовсе, счётчик не выдумывается."""
        import price_freshness_run as pfr

        def answering(action, **kw):
            return {"ok": True, "season": {"global_discount": 0.15}}

        facts = pfr.live_handles(get=answering)
        self.assertNotIn("refused", facts)
        self.assertTrue(facts["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
