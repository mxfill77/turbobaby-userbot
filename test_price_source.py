# -*- coding: utf-8 -*-
"""Юниты сезонного множителя из `price_source.json` (модуль `price_source` + врезка в suggest).

ТРИ ВЕЩИ, КОТОРЫЕ ЗДЕСЬ ДОКАЗЫВАЮТСЯ, А НЕ ДЕКЛАРИРУЮТСЯ:
  1. ОБЪЯВЛЕННЫЙ ОТКАТ = ПРЕЖНЕЕ ПОВЕДЕНИЕ. Не «похожее», а тот же объект котировки и та же
     строка клиенту. Переключение 20.08 перевернуло знак ручки: источник по умолчанию —
     ЗАПИСАННОЕ ПРАВИЛО, а к живому листу возвращает ровно опознанное слово из `_FALSE`;
     мусор и пустое значение — это ВКЛЮЧЕНО (`TestFlag`).
  2. БЕЗ ФАЙЛА — БЕЗ ЧИСЛА. Пропавший/пустой/битый/чужой схемы файл гасит цену в честный
     фолбэк «НЕ называй никакого числа», а не откатывается к слепому к сезону числу листа и не
     выдумывает своё (`test_negative_*`).
  3. ЗАНЯТОСТЬ ОСТАЁТСЯ ЗА ЖИВОЙ ДВЕРЬЮ. Файл её не знает и не подменяет: `available`,
     `none_available` и депозит доезжают из живой котировки нетронутыми.

Голдены 437 и 710 — не круглые числа из головы: это счёт по файлу для ДВУХ ЖИВЫХ случаев
прогона `e9231b3` (dlg 179 и 120), опубликованный в `docs/artifacts/2026-08-17-price-source-file.md`
(замок B). Разъедется файл — покраснеют они.

Мок Bridge копирует ЖИВОЙ формат (правило-класс CLAUDE.md): поля ровно те, что собирает
`pricing._normalize`, строка столбца J взята дословно из живого прогона.
"""
import datetime
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import test_isolation  # noqa: F401  ДО suggest: офлайн-дверь сторожа свежести (§5 обвязки)
import price_gate
import price_source
import pricing
import suggest

FLAG = price_source.ENV_FLAG

# Живые строки столбца J из прогона e9231b3 (артефакт 69a6c9f §5.2) — дословно.
J179 = "NMAX 155 | дней: 7, стоимость: 2217 (скидка за срок 6 %, 317 в день), депозит: 3000 бат"
J120 = ("XMAX 300 NEW | дней: 14, стоимость: 8082 (скидка за срок 18 %, 577 в день), "
        "депозит: 7000 бат")

FLEET = ["YAMAHA NMAX 155", "YAMAHA XMAX300 2020-2022", "YAMAHA XMAX 300 NEW 2023-",
         "YAMAHA XSR 155", "HONDA CLICK 125", "KAWASAKI NINJA 400"]


def live_quote(day_price=317, total=2217, deposit=3000, available=True, days=7,
               cap_active=True, cap_price=5000, text=J179, bike="YAMAHA NMAX 155"):
    """Котировка в том виде, в каком её отдаёт `pricing._normalize` (живой формат).

    `bike` — ИМЯ ЮНИТА, который живая дверь прокотировала. Оно здесь не украшение: дверь
    квотирует конкретный байк и называет его, поэтому котировка NMAX с моделью «XMAX» —
    фикстура, которой в проде не бывает (правило-класс: мок копирует живой формат)."""
    q = {"day_price": day_price, "total": total, "deposit": deposit, "available": available,
         "season": None, "bike": bike, "model": bike, "days": days,
         "cap_active": cap_active, "cap_price": cap_price}
    if text is not None:
        q["text"] = text
    return q


class FlagBase(unittest.TestCase):
    def setUp(self):
        self._flag = os.environ.get(FLAG)
        self._ttl = os.environ.get(price_gate.TTL_ENV)
        # ВРЕЗКА СТОРОЖА ГЛУШИТСЯ ОБЪЯВЛЕННЫМ ОТКАТОМ, а не моком. Иначе юнит источника цены
        # полез бы девятью GET в ЖИВОЙ мост: 20.08 такие пробы шли по 306с и 599с, а один раз
        # не вернулись вовсе. Предмет этих тестов — счёт по файлу; свежесть судит test_price_gate.
        os.environ[price_gate.TTL_ENV] = "0"
        price_gate.reset()
        self._path = price_source.PATH
        price_source._cache.update(key=None, doc=None)

    def tearDown(self):
        price_source.PATH = self._path
        price_source._cache.update(key=None, doc=None)
        price_gate.reset()
        for name, saved in ((FLAG, self._flag), (price_gate.TTL_ENV, self._ttl)):
            if saved is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = saved

    def on(self):
        """Источник — ЗАПИСАННОЕ ПРАВИЛО. Это дефолт, поэтому ручку СНИМАЕМ, а не ставим."""
        os.environ.pop(FLAG, None)

    def off(self, value="0"):
        """Объявленный откат к живому листу — ровно опознанным словом."""
        os.environ[FLAG] = value


# ──────────────── 1. ручка: дефолт — ПРАВИЛО, выключает опознанное слово ────────────────

class TestFlag(FlagBase):
    def test_no_handle_means_the_recorded_rule(self):
        # Переключение 20.08: отсутствие ручки — это ВКЛЮЧЕНО, а не выключено.
        os.environ.pop(FLAG, None)
        self.assertTrue(price_source.enabled())

    def test_only_named_words_roll_back_to_the_sheet(self):
        for v in ("0", "нет", "off", "OFF", " false ", "no", "выкл"):
            self.off(v)
            self.assertFalse(price_source.enabled(), v)

    def test_garbage_stays_on_the_rule_not_on_the_sheet(self):
        # Неразбор падает на сторону ПРАВИЛА: цена ошибки «ушли на лист» названа деньгами
        # (занижение пика 27.7–31.7 %), цена ошибки «остались на правиле» прикрыта сторожем.
        for v in ("", "   ", "мусор", "PRICE", "2", "оff"):
            self.off(v)
            self.assertTrue(price_source.enabled(), v)

    def test_rolled_back_returns_the_very_same_object(self):
        # Не «равный», а ТОТ ЖЕ: при откате ветка не исполняется вовсе.
        self.off()
        res = {"status": "ok", "quote": live_quote()}
        self.assertIs(price_source.reprice(res, "NMAX 155", "2026-01-29", "2026-02-05",
                                           suggest._bike_key), res)

    def test_handle_read_on_every_call_not_on_import(self):
        res = {"status": "ok", "quote": live_quote()}
        self.off()
        self.assertIs(price_source.reprice(res, "NMAX 155", "2026-01-29", "2026-02-05",
                                           suggest._bike_key), res)
        self.on()
        self.assertIsNot(price_source.reprice(res, "NMAX 155", "2026-01-29", "2026-02-05",
                                              suggest._bike_key), res)


# ───────────────────────────── 2. периоды и корзины ИЗ ФАЙЛА ─────────────────────────────

class TestPeriods(FlagBase):
    def setUp(self):
        FlagBase.setUp(self)
        self.doc = price_source.load()

    def test_file_loads(self):
        self.assertIsNotNone(self.doc)
        self.assertEqual(self.doc["schema"], "turbobaby/price_source")

    def test_every_day_of_year_belongs_to_exactly_one_period(self):
        # Дыра в календаре = молчание бота в эти дни; пересечение = произвол порядка.
        d = datetime.date(2026, 1, 1)
        while d.year == 2026:
            hit = [p["key"] for p in self.doc["season"]["periods"]
                   if price_source.period_of({"season": {"periods": [p]}}, d)]
            self.assertEqual(len(hit), 1, "%s → %s" % (d, hit))
            d += datetime.timedelta(days=1)

    def test_peak_crosses_new_year(self):
        for md, key in ((("12-14"), "P4"), (("12-15"), "P5"), (("01-29"), "P5"),
                        (("02-05"), "P5"), (("02-06"), "P6"), (("05-15"), "P8"),
                        (("05-16"), "P9"), (("10-31"), "P2"), (("11-01"), "P3")):
            mm, dd = (int(x) for x in md.split("-"))
            got = price_source.period_of(self.doc, datetime.date(2026, mm, dd))
            self.assertEqual(got["key"], key, md)

    def test_buckets_cover_terms(self):
        for days, name in ((1, "1-3"), (3, "1-3"), (4, "4-6"), (7, "7-13"), (13, "7-13"),
                           (14, "14-29"), (30, "30+"), (120, "30+")):
            self.assertEqual(price_source.bucket_of(self.doc, days)["bucket"], name, days)

    def test_low_season_multiplier_is_exactly_one(self):
        # На этом равенстве стои́т признак низкого сезона (и решение о кепке) — оно не косметика.
        for key in ("P1", "P2"):
            p = next(x for x in self.doc["season"]["periods"] if x["key"] == key)
            self.assertEqual(set(p["multiplier"].values()), {1.0})


# ───────────────────────────── 3. счёт: живые случаи 179 и 120 ─────────────────────────────

class TestGoldenLiveCases(FlagBase):
    def test_dlg179_january_nmax_seven_days(self):
        self.on()
        res = price_source.reprice({"status": "ok", "quote": live_quote()},
                                   "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key)
        q = res["quote"]
        self.assertEqual(res["status"], "ok")
        self.assertEqual(q["day_price"], 437)      # 298 x 1.467 (P5 пик) x 1.000
        self.assertEqual(q["total"], 3059)
        self.assertNotIn("text", q)                # чужая строка J снята, а не оставлена рядом
        self.assertEqual(q["deposit"], 3000)       # депозит — из живой котировки
        self.assertTrue(q["available"])            # наличие — из живой двери

    def test_dlg120_december_xmax_fourteen_days(self):
        self.on()
        res = price_source.reprice(
            {"status": "ok", "quote": live_quote(577, 8082, 7000, True, 14, True, 9900, J120,
                                    bike="YAMAHA XMAX 300 NEW 2023-")},
            "XMAX 300", "2025-12-12", "2025-12-26", suggest._bike_key)
        # ГОЛДЕН ПЕРЕСЧИТАН 26.08.2026 (поколения разведены, 2d66cf0): живая котировка приходит
        # по юниту НОВОГО поколения («YAMAHA XMAX 300 NEW 2023-»), и база у него теперь своя.
        # Число ПЕРЕСЧИТАНО ИЗ ЛИСТА: 662 — клетка «7 суток» строки листа этого поколения (замер
        # дверью quote_price 26.08). Прежние 710/9940 стояли на СЛИТОЙ базе 623, снятой 26.08.
        self.assertEqual(res["quote"]["day_price"], 754)   # 662 x 1.389 (P4) x 0.82
        self.assertEqual(res["quote"]["total"], 10556)

    def test_both_live_cases_moved_towards_what_client_paid(self):
        # Смысл всей ветки одной проверкой: 317→437 при уплаченных 464, 577→710 при 798.
        self.on()
        a = price_source.reprice({"status": "ok", "quote": live_quote()},
                                 "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key)
        b = price_source.reprice(
            {"status": "ok", "quote": live_quote(577, 8082, 7000, True, 14, True, 9900, J120,
                                    bike="YAMAHA XMAX 300 NEW 2023-")},
            "XMAX 300", "2025-12-12", "2025-12-26", suggest._bike_key)
        self.assertLess(abs(a["quote"]["day_price"] - 464), abs(317 - 464))
        self.assertLess(abs(b["quote"]["day_price"] - 798), abs(577 - 798))


# ───────────────────────── 3-бис. опознание модели (мина первого замера) ─────────────────────────

class TestModelResolution(FlagBase):
    """Замер 17.08 первым проходом ПОГАСИЛ цену на обеих живых парах, и погасил МОЛЧА: в путь
    ответа модель приходит обрывком речи («NMAX», «XMAX»), а в файле она «NMAX 155». Эти юниты
    стоя́т ровно на том, чтобы промах опознания больше не выглядел как «бот смолчал»."""

    # Имена ЖИВЫХ юнитов парка (снято 17.08, блок low_season_caps.sheet_model файла)
    LIVE_UNITS = {
        "YAMAHA NMAX 155": "NMAX 155", "YAMAHA XMAX300 2020-2022": "XMAX 300",
        "YAMAHA XMAX 300 NEW 2023-": "XMAX 300", "YAMAHA XSR 155": "XSR 155",
        "HONDA CB 300R": "CB 300", "YAMAHA MT-03 300": "MT-03 300",
        "KAWASAKI NINJA 400": "NINJA 400", "KAWA VULCAN 650S": "VULCAN 650",
        "HONDA CBR 650R": "CBR 650R", "HONDA CB 650R": "CB 650R",
        "HONDA FORZA 300": "FORZA 300", "HONDA ADV 350": "ADV 350",
        "HONDA XADV 750": "XADV 750", "HONDA CLICK 125": "CLICK 125",
    }

    def test_every_live_unit_name_resolves_to_exactly_one_file_row(self):
        doc = price_source.load()
        for unit, want in self.LIVE_UNITS.items():
            row, how = price_source.resolve_row(doc, "", suggest._bike_key, {"bike": unit})
            self.assertIsNotNone(row, "%s → %s" % (unit, how))
            self.assertEqual(row["model"], want, unit)

    def test_client_speech_shorthand_resolves_by_family(self):
        doc = price_source.load()
        for short, want in (("NMAX", "NMAX 155"), ("ADV", "ADV 350"),
                            ("XADV", "XADV 750"), ("NINJA", "NINJA 400"), ("XSR", "XSR 155")):
            row, how = price_source.resolve_row(doc, short, suggest._bike_key)
            self.assertIsNotNone(row, "%s → %s" % (short, how))
            self.assertEqual(row["model"], want, short)
        # После разведения поколений голое XMAX неоднозначно и не угадывается. Живой путь передаёт
        # имя прокотированного юнита и проверяется соседним test_live_unit_name_wins_over_shorthand.
        row, how = price_source.resolve_row(doc, "XMAX", suggest._bike_key)
        self.assertIsNone(row)
        self.assertIn("не угадываем", how)

    def test_ambiguous_shorthand_is_not_guessed(self):
        doc = price_source.load()
        row, how = price_source.resolve_row(doc, "CB", suggest._bike_key)
        self.assertIsNone(row)
        self.assertIn("не угадываем", how)

    def test_live_unit_name_wins_over_shorthand(self):
        # Даже если из речи пришло «XMAX», считаем по юниту, который прокотировала живая дверь.
        doc = price_source.load()
        row, how = price_source.resolve_row(doc, "XMAX", suggest._bike_key,
                                            {"bike": "YAMAHA XMAX 300 NEW 2023-"})
        self.assertEqual(row["model"], "XMAX 300")
        self.assertIn("юнита", how)

    def test_shorthand_from_the_live_run_gives_the_published_numbers(self):
        # РОВНО ТО, ЧТО ПРИШЛО В ЖИВОМ ПРОГОНЕ: hints.model = «NMAX» / «XMAX».
        self.on()
        a = price_source.reprice(
            {"status": "ok", "quote": live_quote()},
            "NMAX", "2026-01-29", "2026-02-05", suggest._bike_key)
        self.assertEqual(a["quote"]["day_price"], 437)
        q = live_quote(577, 8082, 7000, True, 14, True, 9900, J120,
                                    bike="YAMAHA XMAX 300 NEW 2023-")
        q["bike"] = "YAMAHA XMAX 300 NEW 2023-"
        b = price_source.reprice({"status": "ok", "quote": q},
                                 "XMAX", "2025-12-12", "2025-12-26", suggest._bike_key)
        # ПЕРЕСЧИТАНО 26.08.2026 из листа, как и в TestGoldenLiveCases: 662 x 1.389 (P4) x 0.82.
        self.assertEqual(b["quote"]["day_price"], 754)


# ──────────── 3б. разведение поколений XMAX: ЦЕНОЙ, а не только именем строки ────────────

class TestGenerationSplit(FlagBase):
    """ЗАМОК РАЗВЕДЕНИЯ ПОКОЛЕНИЙ XMAX 300 (решение владельца 26.08.2026, ПОДТВЕРЖДЕНО 01.09.2026
    поимённо: «XMAX: точно разделить поколения»).

    ЗАЧЕМ ОТДЕЛЬНЫЙ КЛАСС, а не строка в соседнем. До 01.09 разведение не стерёг НИ ОДИН тест
    уровня правила, хотя жило оно с 26.08 (2d66cf0): соседний
    `test_every_live_unit_name_resolves_to_exactly_one_file_row` сверяет у ОБЕИХ строк листа
    только `model == 'XMAX 300'` — и остался бы ЗЕЛЁНЫМ, слейся поколения обратно в одну строку.
    Разведение держалось словом артефакта и разовым прибором из scratch-каталога, которого гейт
    не гоняет (остаток №6 артефакта `2026-08-26-xmax-generations-split.md`). Цена молчаливого
    отката названа деньгами там же, §8: одна цена на два товара с разницей залога 2000 ฿ —
    завышение старого поколения на 66 ฿/сут и недобор 39 ฿/сут по новому, а новых юнитов в парке
    7 из 10.

    ЧИСЕЛ ЦЕНЫ В ЭТОМ КЛАССЕ НЕТ НИ ОДНОГО, и это не опрятность. Ожидания считаются ИЗ САМОГО
    ФАЙЛА: пересъёмка клеток листа (она законна и уже была у соседних строк CLICK 125 и FORZA 300)
    красила бы замок, который стережёт не число, а РАЗЛИЧИЕ. Голдены на конкретные 557/662 живут
    выше — `TestGoldenLiveCases` и `test_shorthand_from_the_live_run_gives_the_published_numbers`.
    """

    # Живые имена юнитов парка: старое поколение лист метки не даёт, новому даёт слово NEW.
    OLD_UNIT = "XMAX 300CC BLUE PHUKET 5773"
    NEW_UNIT = "XMAX 300CC NEW BLACK PHUKET 8969"
    ANCHOR = datetime.date(2026, 9, 7)      # низкий сезон, опорная корзина 7-13 суток

    def _rows(self):
        doc = price_source.load()
        self.assertIsNotNone(doc, "файл правила не прочитан")
        rows = [m for m in doc["base"]["models"] if m.get("model") == "XMAX 300"]
        return doc, rows

    def _split(self, rows):
        """(строка без метки, строка с меткой) — само устройство листа, а не наша догадка."""
        plain = [r for r in rows if not r.get("unit_marker")]
        marked = [r for r in rows if r.get("unit_marker")]
        self.assertEqual((len(plain), len(marked)), (1, 1))
        return plain[0], marked[0]

    def test_file_holds_two_generation_rows_with_different_bases(self):
        # Слияние обратно в одну строку — КРАСНОЕ. И различие требуется ЦЕНОЙ: две строки с одной
        # базой были бы тем же дефектом, только с более приличным видом.
        _, rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r.get("generation") for r in rows}), 2)
        self.assertEqual(len({r.get("sheet_model") for r in rows}), 2)
        old, new = self._split(rows)
        self.assertNotEqual(old["base_thb_per_day"], new["base_thb_per_day"])
        self.assertLess(old["base_thb_per_day"], new["base_thb_per_day"])   # наценка нового

    def test_owner_decision_is_recorded_on_both_generation_rows(self):
        # Решение владельца живёт В ФАЙЛЕ ПРАВИЛА, а не только в артефакте: следующая пересборка
        # базы обязана видеть, что разведение — РЕШЕНИЕ, а не догадка сборщика.
        _, rows = self._rows()
        for r in rows:
            self.assertIn("XMAX: точно разделить поколения", r.get("owner_decision") or "",
                          r.get("generation"))

    def test_live_unit_names_resolve_each_to_its_own_generation(self):
        doc, rows = self._rows()
        old, new = self._split(rows)
        for unit, want in ((self.OLD_UNIT, old), (self.NEW_UNIT, new)):
            row, how = price_source.resolve_row(doc, "XMAX", suggest._bike_key, {"bike": unit})
            self.assertIsNotNone(row, "%s → %s" % (unit, how))
            self.assertEqual(row.get("generation"), want.get("generation"), unit)

    def test_two_generations_cost_different_money_on_the_same_window(self):
        # ДЕНЬГАМИ, а не строкой файла. Окно одно, сезон один, корзина срока одна — значит цена
        # суток обязана разойтись РОВНО отношением баз: сезон и срок поколения не различают.
        doc, rows = self._rows()
        old, new = self._split(rows)
        got = {}
        for unit in (self.OLD_UNIT, self.NEW_UNIT):
            day, info = price_source.day_price(doc, "XMAX", self.ANCHOR, 7,
                                               suggest._bike_key, quote={"bike": unit})
            self.assertIsNotNone(day, "%s → %s" % (unit, info))
            got[unit] = day
        self.assertNotEqual(got[self.OLD_UNIT], got[self.NEW_UNIT])
        self.assertLess(got[self.OLD_UNIT], got[self.NEW_UNIT])
        expected_new = got[self.OLD_UNIT] * (float(new["base_thb_per_day"])
                                             / float(old["base_thb_per_day"]))
        self.assertLessEqual(abs(got[self.NEW_UNIT] - expected_new), 1.5,
                             "%s против %s" % (got, expected_new))

    def test_file_marker_and_product_splitter_call_the_same_units_new(self):
        # ДВА механизма, одна правда. Метку поколения держит файл правила (`unit_marker`), а
        # продуктовую строку клиенту — `suggest._xmax_is_new_gen`. Разъедутся молча — клиент
        # увидит строку «XMAX 300 New Gen» с ценой СТАРОГО поколения, и наоборот.
        doc, rows = self._rows()
        _, new = self._split(rows)
        for unit, want_new in ((self.OLD_UNIT, False), (self.NEW_UNIT, True)):
            row, how = price_source.resolve_row(doc, "XMAX", suggest._bike_key, {"bike": unit})
            self.assertIsNotNone(row, "%s → %s" % (unit, how))
            self.assertEqual(row.get("generation") == new.get("generation"), want_new, unit)
            self.assertEqual(bool(suggest._xmax_is_new_gen(unit)), want_new, unit)

    def test_speech_without_a_marker_gets_no_generation_and_no_price(self):
        # FAIL-CLOSED. В обрывке речи клиента («XMAX») метки не бывает НИКОГДА, и молчаливый выбор
        # строки без метки назвал бы цену старого байка за новый — ровно тот дефект, который заход
        # 26.08 и чинил (артефакт §10, поймано гейтом). Молчание дешевле чужой карточки.
        doc, _ = self._rows()
        row, how = price_source.resolve_row(doc, "XMAX", suggest._bike_key)
        self.assertIsNone(row)
        self.assertIn("не угадываем", how)
        day, info = price_source.day_price(doc, "XMAX", self.ANCHOR, 7, suggest._bike_key)
        self.assertIsNone(day, info)


# ───────────────────────────── 4. отрицательные замки ─────────────────────────────

class TestNegative(FlagBase):
    def _with_file(self, body):
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(body)
        price_source.PATH = p
        price_source._cache.update(key=None, doc=None)
        self.addCleanup(lambda: os.path.exists(p) and os.unlink(p))
        return p

    def _dead(self, res):
        self.assertEqual(res["status"], "error")
        self.assertIsNone(res["quote"])

    def test_missing_file_kills_the_price(self):
        self.on()
        price_source.PATH = os.path.join(HERE, "нет-такого-файла-price_source.json")
        price_source._cache.update(key=None, doc=None)
        self._dead(price_source.reprice({"status": "ok", "quote": live_quote()},
                                        "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key))

    def test_empty_file_kills_the_price(self):
        self.on()
        self._with_file("")
        self._dead(price_source.reprice({"status": "ok", "quote": live_quote()},
                                        "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key))

    def test_broken_json_kills_the_price(self):
        self.on()
        self._with_file('{"schema": "turbobaby/price_source", "base": {"models": [')
        self._dead(price_source.reprice({"status": "ok", "quote": live_quote()},
                                        "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key))

    def test_foreign_schema_kills_the_price(self):
        self.on()
        self._with_file(json.dumps({"schema": "чужое", "base": {"models": [1]},
                                    "season": {"periods": [1]}, "term": {"buckets": [1]}}))
        self._dead(price_source.reprice({"status": "ok", "quote": live_quote()},
                                        "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key))

    def test_empty_models_kill_the_price(self):
        self.on()
        self._with_file(json.dumps({"schema": "turbobaby/price_source", "base": {"models": []},
                                    "season": {"periods": [{"key": "P1"}]},
                                    "term": {"buckets": [{}]}}))
        self._dead(price_source.reprice({"status": "ok", "quote": live_quote()},
                                        "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key))

    def test_model_not_judged_kills_the_price(self):
        # CLICK 125 и FORZA 300 получили опубликованные базы из листа 30.08. Неизвестная модель
        # по-прежнему обязана гасить цену целиком: молчание, а не выдумка.
        self.on()
        # Юнит в котировке — ТОТ ЖЕ, что запрошен: живая дверь квотирует конкретный байк
        # и называет его, котировки «NMAX под именем CLICK» в проде не бывает.
        for model, unit in (("SUZUKI НЕТ ТАКОЙ", "SUZUKI НЕТ ТАКОЙ 1"),):
            self._dead(price_source.reprice({"status": "ok", "quote": live_quote(bike=unit)},
                                            model, "2026-01-29", "2026-02-05", suggest._bike_key))

    def test_unparsable_start_date_kills_the_price(self):
        self.on()
        self._dead(price_source.reprice({"status": "ok", "quote": live_quote()},
                                        "NMAX 155", "не-дата", "2026-02-05", suggest._bike_key))

    def test_dead_branch_never_returns_the_old_blind_number(self):
        # Главное отрицательного замка: в гашёной котировке чисел НЕТ ВООБЩЕ.
        self.on()
        price_source.PATH = os.path.join(HERE, "нет-такого-файла.json")
        price_source._cache.update(key=None, doc=None)
        res = price_source.reprice({"status": "ok", "quote": live_quote()},
                                   "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key)
        self.assertNotIn("317", json.dumps(res, ensure_ascii=False))
        self.assertNotIn("2217", json.dumps(res, ensure_ascii=False))

    def test_non_ok_statuses_are_left_alone(self):
        # Занятость и сбои судит живая дверь — файл в них не вмешивается ни при каком флаге.
        self.on()
        for st in ("none_available", "no_candidates", "error"):
            res = {"status": st, "quote": None}
            self.assertIs(price_source.reprice(res, "NMAX 155", "2026-01-29", "2026-02-05",
                                               suggest._bike_key), res)


# ───────────────────────────── 5. кепки ─────────────────────────────

class TestCaps(FlagBase):
    def test_cap_applies_only_in_low_season(self):
        self.on()
        # Июль (P1, множитель 1.000), месяц, живая ручка включена → кепка ФАЙЛА в игре.
        res = price_source.reprice(
            {"status": "ok", "quote": live_quote(337, 10110, 3000, True, 30, True, 5000, None)},
            "NMAX 155", "2026-07-01", "2026-07-31", suggest._bike_key)
        self.assertTrue(res["quote"]["cap_active"])
        self.assertEqual(res["quote"]["cap_price"], 5000)   # величина из файла

    def test_cap_is_switched_off_outside_low_season(self):
        # Январь: кепка низкого сезона погасила бы всю сезонную поправку — ровно тот недобор,
        # ради которого ветка заведена. Признак снимается, число остаётся сезонным.
        self.on()
        res = price_source.reprice(
            {"status": "ok", "quote": live_quote(317, 2217, 3000, True, 7, True, 5000, J179)},
            "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key)
        self.assertFalse(res["quote"]["cap_active"])
        self.assertIsNone(res["quote"]["cap_price"])
        self.assertEqual(res["quote"]["day_price"], 437)

    def test_cap_position_comes_from_the_live_door_not_from_the_file(self):
        # Файл сам это требует: истории у ручки нет, положение читается живым quote_price.
        self.on()
        res = price_source.reprice(
            {"status": "ok", "quote": live_quote(337, 10110, 3000, True, 30, False, 0, None)},
            "NMAX 155", "2026-07-01", "2026-07-31", suggest._bike_key)
        self.assertFalse(res["quote"]["cap_active"])

    def test_two_generation_caps_are_not_guessed(self):
        doc = price_source.load()
        cap, why = price_source.cap_for(doc, "XMAX 300", suggest._bike_key, live_cap_price=None)
        self.assertIsNone(cap)
        cap, _ = price_source.cap_for(doc, "XMAX 300", suggest._bike_key, live_cap_price=9900)
        self.assertEqual(cap, 9900)


# ───────────────────────────── 6. врезка в путь ответа ─────────────────────────────

class TestWiredIntoAnswerPath(FlagBase):
    FLEET = FLEET

    def setUp(self):
        FlagBase.setUp(self)
        self._save = (pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN)
        pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN = "quote_price", "u", "t"
        pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (pricing.PRICING_ACTION, pricing.BRIDGE_URL, pricing.BRIDGE_TOKEN) = self._save
        pricing._FLEET_CACHE["data"] = None
        FlagBase.tearDown(self)

    def _getter(self, available=True, text=J179, model_key="nmax155"):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            if model_key not in suggest._bike_key(params.get("bike", "")):
                return {"ok": False}
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            data = {"day_price": 317, "total": 2217, "deposit": 3000, "available": available,
                    "days": days, "cap_active": True, "cap_price": 5000}
            if text is not None:
                data["text"] = text
            return {"ok": True, "data": data}
        return fake

    def _note(self, **kw):
        hints = {"has_dates": True, "iso_start": "2026-01-29", "iso_end": "2026-02-05",
                 "model": "NMAX 155", "hint_days": 7}
        return suggest.build_pricing_note(hints, lang="ru", getter=self._getter(**kw),
                                          today=datetime.date(2026, 1, 20))

    def test_flag_off_keeps_column_j_verbatim(self):
        self.off()
        self.assertIn(J179, self._note())

    def test_flag_on_replaces_the_number_and_drops_column_j(self):
        self.on()
        note = self._note()
        self.assertIn("437 ฿/день", note)
        self.assertIn("итого 3059 ฿", note)
        self.assertNotIn("317", note)
        self.assertNotIn(J179, note)

    def test_availability_still_decided_by_the_live_door(self):
        self.on()
        note = self._note(available=False)
        self.assertIn("все подходящие байки заняты", note)
        self.assertNotIn("437", note)

    def test_broken_file_makes_the_bot_silent_not_inventive(self):
        self.on()
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("{битый файл")
        self.addCleanup(lambda: os.path.exists(p) and os.unlink(p))
        price_source.PATH = p
        price_source._cache.update(key=None, doc=None)
        note = self._note()
        self.assertIn("НЕ называй никакого числа", note)
        for n in ("317", "2217", "437", "3059"):
            self.assertNotIn(n, note)

    def test_deposit_and_park_model_survive(self):
        self.on()
        note = self._note()
        self.assertIn("депозит 3000 ฿", note)
        self.assertIn("NMAX 155", note)


if __name__ == "__main__":
    unittest.main()
