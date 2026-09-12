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
        # ПЕРЕСЧИТАНО 06.09.2026 по решению владельца 06.09.2026-1 («ступень срока: граница
        # корзины — 14»). Было `(13, "7-13"), (14, "14-29")` — граница стояла на 13 и была
        # ЗАМЕРОМ, а не словом. Обе точки границы названы ЯВНО и с обеих сторон (14 и 15):
        # тест на одной точке пропустил бы сдвиг в любую сторону.
        for days, name in ((1, "1-3"), (3, "1-3"), (4, "4-6"), (7, "7-14"), (13, "7-14"),
                           (14, "7-14"), (15, "15-29"), (29, "15-29"), (30, "30+"), (120, "30+")):
            self.assertEqual(price_source.bucket_of(self.doc, days)["bucket"], name, days)

    def test_term_boundary_is_the_owner_word_and_says_so(self):
        """ЗАМОК ГРАНИЦЫ СРОКА (решение владельца 06.09.2026-1). До 06.09 граница жила ЗАМЕРОМ,
        и вернуть её на 13 можно было молча: соседний `test_buckets_cover_terms` покраснел бы,
        но сказал бы «ждали 7-14, пришло 7-13» — про то, ЧЬЁ это число, он не знает ничего.

        Здесь пришпилено ровно то, что владелец решил, и ровно то, чего он НЕ решал: сдвинуты
        ДВЕ цифры границы, множители всех пяти корзин остались замером и не тронуты."""
        term = self.doc["term"]
        by = {b["bucket"]: b for b in term["buckets"]}
        self.assertEqual(sorted(by), ["1-3", "15-29", "30+", "4-6", "7-14"])
        self.assertEqual(by["7-14"]["days_to"], 14)       # ← слово владельца
        self.assertEqual(by["15-29"]["days_from"], 15)    # ← оно же, второй половиной
        self.assertEqual(term["reference_bucket"], "7-14")
        # Множители — по-прежнему ЗАМЕР 15.08, решением владельца не тронуты ни один.
        self.assertEqual([by[k]["multiplier"] for k in ("1-3", "4-6", "7-14", "15-29", "30+")],
                         [0.97, 1.03, 1.0, 0.82, 0.53])
        # Файл обязан САМ называть, откуда взялась граница: иначе следующий читатель увидит
        # «7-14» рядом со словом ЗАМЕР и починит его обратно как опечатку.
        self.assertIn("06.09.2026-1", term["boundary_origin"])
        self.assertIn("граница корзины — 14", term["boundary_origin"])
        self.assertIn("СЛОВО ВЛАДЕЛЬЦА", term["origin"])

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
        #
        # ГОЛДЕН ПЕРЕСЧИТАН ВТОРОЙ РАЗ 06.09.2026, и причина НЕ в базе: решение владельца
        # 06.09.2026-1 сдвинуло границу корзины срока с 13 на 14, а этот живой случай стои́т
        # РОВНО НА НЕЙ — 14 суток. Множитель срока сменился 0.82 → 1.000, и только он:
        # 754 = 662 x 1.389 x 0.82, 920 = 662 x 1.389 x 1.000. База, сезон и формула не тронуты.
        #
        # ЦЕНА ЭТОГО СДВИГА НАЗВАНА ЧЕСТНО, а не спрятана в пересчёт. Клиент по этой броне
        # заплатил 798 ฿/сут. Прежний счёт промахивался на −44 (5.5 %), новый промахивается на
        # +122 (15.3 %) — то есть ПО ЭТОМУ ОДНОМУ случаю правка увела расчёт ДАЛЬШЕ от денег.
        # Тест это не скрывает и не смягчает: сосед `test_both_live_cases_moved_towards_what_
        # client_paid` продолжает мерить главное (счёт всё ещё ближе к уплаченному, чем лист:
        # 122 < 221) и остался ЗЕЛЁНЫМ без единой правки. Разбор — в артефакте 06.09.
        #
        # ГОЛДЕН ПЕРЕСЧИТАН ТРЕТИЙ РАЗ 07.09.2026 — решением владельца 07.09.2026-1 (срок через
        # границу периодов считается ПО ДНЯМ) и его ответом «полной» на вопрос о ступени. И вот
        # что тут важно назвать вслух: ЭТОТ ЖИВОЙ СЛУЧАЙ САМ ЛЕЖИТ ЧЕРЕЗ ГРАНИЦУ. 12-25.12.2025 —
        # это 3 суток P4 («до 20 декабря», множитель 1.389) и 11 суток P5 (пик, 1.467), то есть
        # ДВЕ суточные ставки: 920 и 971. Единственной среди них нет, поэтому `day_price` теперь
        # НЕ НАЗЫВАЕТСЯ ВОВСЕ (None) — среднее 13441/14 = 960 запрещено записанным правилом и
        # здесь не подставляется. Ступень срока осталась ОДНА и от полной длительности (корзина
        # 7-14, множитель 1.000) — ровно ответ владельца.
        #
        # ЦЕНА ЭТОГО СДВИГА НАЗВАНА ЧЕСТНО, как и в прошлый раз. Клиент заплатил 798 ฿/сут =
        # 11172 ฿ за срок. Счёт по дате начала промахивался на +1708, счёт по дням промахивается
        # на +2269 — то есть ПО ЭТОМУ ОДНОМУ случаю разбивка увела расчёт ДАЛЬШЕ от денег на
        # 561 ฿. Тест это не прячет; сосед `test_both_live_cases_moved_towards_what_client_paid`
        # продолжает мерить главное (правило всё ещё ближе к уплаченному, чем лист: 2269 < 3090).
        self.assertIsNone(res["quote"]["day_price"])       # 920 (P4) и 971 (P5) — «одной» нет
        self.assertEqual(res["quote"]["total"], 13441)     # 3 x 920 + 11 x 971

    def test_both_live_cases_moved_towards_what_client_paid(self):
        # Смысл всей ветки одной проверкой: 317→437 при уплаченных 464, 577→710 при 798.
        #
        # ЕДИНИЦА СРАВНЕНИЯ У ВТОРОГО СЛУЧАЯ СМЕНИЛАСЬ 07.09.2026, и не по вкусу: dlg120 лежит
        # ЧЕРЕЗ границу периодов, суточной ставки у него больше нет ни одной (см. соседний тест),
        # а СУММА ЗА СРОК есть и точна. Меряем её — против уплаченного за срок и против листа за
        # срок. Первый случай (dlg179) лежит ВНУТРИ одного периода, ставка у него по-прежнему
        # одна, и он мерится как мерился: правка на него не влияет ни битом.
        self.on()
        a = price_source.reprice({"status": "ok", "quote": live_quote()},
                                 "NMAX 155", "2026-01-29", "2026-02-05", suggest._bike_key)
        b = price_source.reprice(
            {"status": "ok", "quote": live_quote(577, 8082, 7000, True, 14, True, 9900, J120,
                                    bike="YAMAHA XMAX 300 NEW 2023-")},
            "XMAX 300", "2025-12-12", "2025-12-26", suggest._bike_key)
        self.assertLess(abs(a["quote"]["day_price"] - 464), abs(317 - 464))
        self.assertLess(abs(b["quote"]["total"] - 798 * 14), abs(8082 - 798 * 14))   # 2269 < 3090


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
        # ПЕРЕСЧИТАНО 26.08.2026 из листа, как и в TestGoldenLiveCases: 662 x 1.389 (P4).
        # ПЕРЕСЧИТАНО 06.09.2026 второй раз — множитель срока 0.82 → 1.000: срок 14 суток лёг в
        # опорную корзину по решению владельца 06.09.2026-1 (граница 13 → 14). Тот же голден и
        # та же причина, что у `TestGoldenLiveCases.test_dlg120_december_xmax_fourteen_days`.
        # 07.09.2026 СМЕНИЛСЯ СВИДЕТЕЛЬ, а не предмет: этот срок лежит через границу P4 → P5,
        # суточной ставки у него больше нет ни одной (разбивка по дням, решение 07.09.2026-1),
        # и опознание модели теперь доказывает СУММА. Она поколенческая ровно так же, как была
        # ставка: 13441 стои́т на базе 662 НОВОГО поколения, у старого (557) вышло бы другое.
        self.assertEqual(b["quote"]["total"], 13441)


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


# ────────────────── 5б. РАЗБИВКА СРОКА ПО ДНЯМ (решение 07.09.2026-1) ──────────────────

class TestSplitByDays(FlagBase):
    """Срок через границу периодов считается ПО ДНЯМ, ступень — от ПОЛНОЙ длительности.

    Основание — решение владельца `07.09.2026-1` и его ОТВЕТ от 07.09.2026 на первый
    оставшийся вопрос, дословно: «полная скидка что бы разница была конечно же не такая».
    Оба лежат в узле `KB_business_rules`, оба процитированы докстрокой `price_source.term_total`.

    Голдены 5023 и 8124 — не круглые числа из головы: это счёт ЖИВЫМ файлом для примера самого
    решения (NMAX 155, 31 сутки с 06.10.2026), опубликованный в правиле. 5023 — ответ владельца
    («полной»), 8124 — отвергнутое им чтение («куска»). Разъедется файл — покраснеют они.
    """

    Q = {"bike": "YAMAHA NMAX 155", "model": "YAMAHA NMAX 155"}
    OFF = price_source.SPLIT_OFF_ENV

    def setUp(self):
        FlagBase.setUp(self)
        self.on()
        self._split = os.environ.get(self.OFF)
        os.environ.pop(self.OFF, None)          # разбивка — ДЕФОЛТ, поэтому ручку СНИМАЕМ
        self.doc = price_source.load()

    def tearDown(self):
        if self._split is None:
            os.environ.pop(self.OFF, None)
        else:
            os.environ[self.OFF] = self._split
        FlagBase.tearDown(self)

    def _term(self, iso="2026-10-06", days=31, model="NMAX 155", env=None, doc=None):
        return price_source.term_total(doc or self.doc, model, datetime.date.fromisoformat(iso),
                                       days, suggest._bike_key, quote=self.Q, env=env)

    # ── п.2 задания: ступень одна и от ПОЛНОЙ длительности ──
    def test_step_comes_from_the_full_term_not_from_the_piece(self):
        # Ответ владельца числом: 26 суток в P2 + 5 суток в P3, но корзина у обоих кусков —
        # «30+» (ступень ПОЛНОГО срока в 31 сутки). Чтение «по куску» дало бы корзины 15-29 и
        # 4-6, то есть 8124 ฿ вместо 5023 — на 3101 ฿ (61.7 %) дороже НА ОДНОЙ БРОНИ.
        total, info = self._term()
        self.assertEqual(info["bucket"], "30+")
        self.assertEqual(total, 5023)
        self.assertNotEqual(total, 8124)

    def test_each_day_goes_by_the_multiplier_of_its_own_period(self):
        total, info = self._term()
        self.assertEqual(info["segments"], [("P2", 26, 158), ("P3", 5, 183)])
        self.assertEqual(sum(n for _k, n, _r in info["segments"]), 31)   # ни одни сутки не потеряны
        self.assertEqual(26 * 158 + 5 * 183, total)
        self.assertTrue(info["split"])

    # ── п.3 задания: округление ПОСУТОЧНОЕ, и оно вынуждено п.4 ──
    def test_rounding_is_per_day_not_at_the_end(self):
        # 298 × 1.000 × 0.53 = 157.94. Посуточно: round(157.94) × 31 = 4898 — ровно то число,
        # что бот называет сегодня. Округли мы в конце: round(157.94 × 31) = 4896, и срок
        # ВНУТРИ одного периода разошёлся бы с сегодняшней ценой на 2 ฿. Отсюда порядок.
        total, info = self._term(iso="2026-09-20", days=31)    # P1 → P2, ставка одна и та же
        self.assertEqual(info["day_prices"], [158])
        self.assertEqual(total, 4898)
        self.assertNotEqual(total, int(round(298 * 1.0 * 0.53 * 31)))

    # ── п.4 задания: срок внутри одного периода обязан дать ТУ ЖЕ цену ──
    def test_every_window_inside_one_period_keeps_the_old_number(self):
        # Числом, а не словом: весь год × три срока. «Та же цена» = ровно прежняя формула
        # int(round(цена_суток × срок)), которой жил модуль до 07.09.2026.
        same = crossed = 0
        d0 = datetime.date(2026, 1, 1)
        for i in range(365):
            ds = d0 + datetime.timedelta(days=i)
            for n in (1, 7, 30):
                keys = {price_source.period_of(self.doc, ds + datetime.timedelta(days=j))["key"]
                        for j in range(n)}
                day, _i = price_source.day_price(self.doc, "NMAX 155", ds, n,
                                                 suggest._bike_key, quote=self.Q)
                total, _t = price_source.term_total(self.doc, "NMAX 155", ds, n,
                                                    suggest._bike_key, quote=self.Q)
                if len(keys) == 1:
                    self.assertEqual(total, int(round(day * n)), "%s %d сут" % (ds, n))
                    same += 1
                else:
                    crossed += 1
        self.assertGreater(same, 500)      # выборка не выродилась в пустую
        self.assertGreater(crossed, 100)   # и границы в ней есть, иначе замок ничего не сторожит

    # ── п.6 задания: КОНТРФАКТ. Выключенная разбивка обязана дать ДРУГОЙ ответ ──
    def test_declared_rollback_gives_the_pre_change_answer(self):
        on, _i = self._term()
        off, info_off = self._term(env={self.OFF: "1"})
        self.assertEqual(on, 5023)
        self.assertEqual(off, 4898)        # счёт по периоду ДАТЫ НАЧАЛА, побайтно как до правки
        self.assertNotEqual(on, off)       # совпали бы — разбивка не доказана
        self.assertFalse(info_off["split"])

    def test_rollback_word_is_read_from_the_environment_on_every_call(self):
        res = {"status": "ok", "quote": live_quote(days=31, cap_active=False, cap_price=None)}
        self.assertEqual(price_source.reprice(res, "NMAX 155", "2026-10-06", "2026-11-06",
                                              suggest._bike_key)["quote"]["total"], 5023)
        os.environ[self.OFF] = "1"
        back = price_source.reprice(res, "NMAX 155", "2026-10-06", "2026-11-06",
                                    suggest._bike_key)["quote"]
        self.assertEqual(back["total"], 4898)
        self.assertEqual(back["day_price"], 158)

    def test_garbage_in_the_rollback_word_keeps_the_split_on(self):
        # Неразбор падает на сторону РЕШЕНИЯ ВЛАДЕЛЬЦА: выключает опознанное слово, не опечатка.
        for v in ("", "  ", "мусор", "2", "оff"):
            self.assertFalse(price_source.split_off({self.OFF: v}), v)
        for v in ("1", "true", "ON", " да ", "yes"):
            self.assertTrue(price_source.split_off({self.OFF: v}), v)

    # ── п.7 задания: две ставки — значит ОДНОЙ ставки нет, и выдумывать её нечем ──
    def test_single_daily_rate_is_named_only_when_it_is_single(self):
        two = price_source.reprice({"status": "ok", "quote": live_quote(days=31, cap_active=False,
                                                                        cap_price=None)},
                                   "NMAX 155", "2026-10-06", "2026-11-06", suggest._bike_key)
        self.assertEqual(two["quote"]["total"], 5023)
        self.assertIsNone(two["quote"]["day_price"])     # 158 и 183 — «одной» среди них нет
        one = price_source.reprice({"status": "ok", "quote": live_quote(days=31, cap_active=False,
                                                                        cap_price=None)},
                                   "NMAX 155", "2026-09-20", "2026-10-21", suggest._bike_key)
        self.assertEqual(one["quote"]["day_price"], 158)  # граница P1 → P2: ставка одна на всех
        self.assertEqual(one["quote"]["total"], 4898)

    def test_no_average_over_the_period_ever_reaches_the_client(self):
        # Записанное правило запрещает СРЕДНЕЕ прямым текстом. Замок предметный: в клиентской
        # фразе срока через границу суточной ставки нет ВООБЩЕ — ни настоящей, ни выведенной
        # делением (5023 / 31 = 162), а итог остаётся ТОЧНЫМ.
        res = price_source.reprice({"status": "ok", "quote": live_quote(days=31, cap_active=False,
                                                                        cap_price=None)},
                                   "NMAX 155", "2026-10-06", "2026-11-06", suggest._bike_key)
        phrase = suggest._client_price(res["quote"])
        self.assertNotIn("฿/день", phrase)
        self.assertNotIn("162", phrase)
        self.assertIn("5023", phrase)

    # ── п.5 задания: ТРЕТИЙ ИСХОД не снесён, а усилен ──
    def _holed_file(self):
        """Файл цены с ДЫРОЙ в календаре: 11-19 января не принадлежат ни одному периоду."""
        body = json.dumps({
            "schema": "turbobaby/price_source",
            "base": {"models": [{"model": "NMAX 155", "sheet_model": "YAMAHA NMAX 155",
                                 "judged": True, "base_thb_per_day": 298, "class": "скутер"}]},
            "season": {"periods": [
                {"key": "PA", "name": "А", "from": "01-01", "to": "01-10",
                 "multiplier": {"скутер": 1.0}},
                {"key": "PB", "name": "Б", "from": "01-20", "to": "12-31",
                 "multiplier": {"скутер": 1.5}}]},
            "term": {"buckets": [{"bucket": "1-3", "days_from": 1, "days_to": 3, "multiplier": 1.0},
                                 {"bucket": "4+", "days_from": 4, "days_to": 400,
                                  "multiplier": 0.53}]}}, ensure_ascii=False)
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(body)
        price_source.PATH = p
        price_source._cache.update(key=None, doc=None)
        self.addCleanup(lambda: os.path.exists(p) and os.unlink(p))
        return price_source.load()

    def test_a_day_without_a_period_in_the_middle_of_the_term_still_refuses(self):
        doc = self._holed_file()
        self.assertIsNotNone(doc)
        # КОНТРОЛЬ: фикстура сама по себе считает — иначе замок доказывал бы поломку фикстуры.
        ok, _i = self._term(iso="2026-01-01", days=3, doc=doc)
        self.assertEqual(ok, 894)                                  # 298 × 1.0 × 1.0 × 3 суток
        # ОТКАЗ: старт 08.01 в периоде PA, но 11.01 внутри срока не принадлежит ни одному.
        total, info = self._term(iso="2026-01-08", days=7, doc=doc)
        self.assertIsNone(total)
        self.assertEqual(info["code"], price_source.WHY_DATE_OUT)

    def test_that_refusal_reaches_the_answer_path_as_silence_not_as_a_number(self):
        self._holed_file()
        res = price_source.reprice({"status": "ok", "quote": live_quote(days=7, text=None)},
                                   "NMAX 155", "2026-01-08", "2026-01-15", suggest._bike_key)
        self.assertEqual(res["status"], "error")
        self.assertIsNone(res["quote"])
        # И это НЕ класс «модель без цены»: цена у модели есть, непосчитаем ДЕНЬ.
        self.assertNotIn("noprice", res)

    def test_the_split_made_this_lock_stronger_not_weaker(self):
        # Названо вслух, потому что это единственное место, где правка МЕНЯЕТ третий исход.
        # До 07.09 период спрашивался ТОЛЬКО у даты начала — день без периода в СЕРЕДИНЕ срока
        # не видела ни одна ветка, и цена называлась по соседнему периоду молча. Объявленный
        # откат эту слепоту возвращает вместе с прежним счётом, и здесь это зафиксировано.
        doc = self._holed_file()
        blind, _i = self._term(iso="2026-01-08", days=7, doc=doc, env={self.OFF: "1"})
        self.assertIsNotNone(blind)                       # прежнее поведение: дыры не видит
        seeing, info = self._term(iso="2026-01-08", days=7, doc=doc)
        self.assertIsNone(seeing)                         # новое поведение: видит и отказывает
        self.assertEqual(info["code"], price_source.WHY_DATE_OUT)

    def test_unreadable_table_still_kills_the_price_after_the_split(self):
        price_source.PATH = os.path.join(HERE, "нет-такого-файла-price_source.json")
        price_source._cache.update(key=None, doc=None)
        res = price_source.reprice({"status": "ok", "quote": live_quote(days=31)},
                                   "NMAX 155", "2026-10-06", "2026-11-06", suggest._bike_key)
        self.assertEqual(res["status"], "error")
        self.assertIsNone(res["quote"])
        self.assertNotIn("5023", json.dumps(res, ensure_ascii=False))

    # ── ГЛАВНЫЙ ЗАПРЕТ ЗАДАЧИ: условие применения потолка НЕ ТРОНУТО ──
    def test_the_cap_condition_itself_is_not_touched_by_the_split(self):
        # Второй вопрос решения 07.09.2026-1 ПЕРЕОТКРЫТ и ждёт замера, поэтому признак
        # «низкий сезон» остаётся ровно сегодняшним — множитель периода ДАТЫ НАЧАЛА == 1.000.
        # Меняется только СУММА, которую этому признаку подают.
        _t, info = self._term()                                  # 06.10 → P2 (1.000) + P3 (1.16)
        per = price_source.period_of(self.doc, datetime.date(2026, 10, 6))
        self.assertEqual(info["low_season"], float(per["multiplier"]["скутер"]) == 1.0)
        self.assertTrue(info["low_season"])                      # хотя пять суток НЕ низкого
        self.assertEqual(info["segments"][1][0], "P3")
        _t2, info2 = self._term(iso="2026-11-30", days=31)       # старт вне дна → признака нет
        self.assertFalse(info2["low_season"])

    def test_the_split_feeds_the_cap_a_new_sum_and_that_is_visible(self):
        # Следствие, названное числом, а не спрятанное: кепка NMAX 155 в файле — 5000 ฿/мес.
        # Счёт по дате начала давал 4898 (под кепкой), счёт по дням даёт 5023 (над ней).
        # Условие не менялось ни одной строкой — изменилась сумма, и предикат сработал.
        q = live_quote(days=31, cap_active=True, cap_price=5000, text=None)
        res = price_source.reprice({"status": "ok", "quote": q},
                                   "NMAX 155", "2026-10-06", "2026-11-06", suggest._bike_key)
        self.assertEqual(res["quote"]["total"], 5023)
        self.assertEqual(res["quote"]["cap_price"], 5000)
        self.assertTrue(suggest._cap_applies(res["quote"]))
        off = dict(res["quote"], total=4898)
        self.assertFalse(suggest._cap_applies(off))


# ─────────────── 6. ВРЕЗКИ В КЛИЕНТСКИЙ ПУТЬ ОТВЕТА БОЛЬШЕ НЕТ (12.09.2026) ───────────────
#
# ЧТО ЗДЕСЬ ПРОВЕРЯЛОСЬ ДО 12.09 И ПОЧЕМУ ПЕРЕВЁРНУТО. Класс замерял врезку счёта по файлу в
# путь ответа: флаг поднят → число файла ЗАМЕНЯЕТ число двери, а дословная строка столбца J
# снимается. Решение владельца 12.09.2026 (узел `business_rules`, блок ДВЕРЬ-ИСТОЧНИК-1209,
# дословно «можно ориентироваться на этот лист») эту врезку СНЯЛО: клиенту уходит число ЖИВОЙ
# ДВЕРИ, а файл остался для внутренних прикидок и для прибора расхождения.
#
# ОСНОВАНИЕ НЕ МНЕНИЕ, А ЗАМЕР (`docs/artifacts/2026-09-12-cena-tri-chisla-sezon.md`): на
# XMAX 300 нового поколения дверь дала 704 ฿/сут и сошлась с листом владельца ДО БАТА, файл дал
# 682, а на карточке из трёх моделей недобор составил 765 ฿.
#
# САМ СЧЁТ ПО ФАЙЛУ НЕ ТРОНУТ И ПРОВЕРЯЕТСЯ ВЫШЕ ЭТОГО КЛАССА, всеми прежними замками
# (`reprice` считает ровно как считал — секции 1-5 этого файла зелены без единой правки).
# Перевёрнут РОВНО вопрос «доезжает ли его число до клиента», и ответ на него теперь НЕТ.
# Клиентский источник целиком меряет `test_door_price.py`.

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

    def test_flag_on_no_longer_replaces_the_number_nor_drops_column_j(self):
        """ПЕРЕВЁРНУТО 12.09.2026. Прежде здесь стояло «437 ฿/день, итого 3059 ฿ и 317 в ответе
        НЕТ» — то есть число ФАЙЛА вместо числа двери. Теперь наоборот: клиенту уходит 317/2217
        двери, дословная строка столбца J цела, а счёта по файлу в ответе нет ни одной цифрой.

        Замок сильнее равенства чисел: 437 и 3059 — это те САМЫЕ числа, которые файл на этом
        входе и считает (проверено секциями 1-5 этого файла), поэтому их отсутствие в ответе
        доказывает снятую подмену, а не сломанную фикстуру."""
        self.on()
        note = self._note()
        self.assertIn(J179, note)                     # J-строка листа ДОСЛОВНО
        self.assertIn("317", note)                    # суточная ставка ДВЕРИ
        self.assertIn("2217", note)                   # итог ДВЕРИ
        self.assertNotIn("437 ฿/день", note)          # счёта по файлу в ответе нет
        self.assertNotIn("итого 3059 ฿", note)
        # Файл при этом по-прежнему СЧИТАЕТ — молчит он в ответе, а не в себе:
        doc = price_source.load()
        total, _info = price_source.term_total(doc, "NMAX 155", datetime.date(2026, 1, 29), 7,
                                               suggest._bike_key)
        self.assertEqual(total, 3059)

    def test_availability_still_decided_by_the_live_door(self):
        self.on()
        note = self._note(available=False)
        self.assertIn("все подходящие байки заняты", note)
        self.assertNotIn("437", note)

    def test_broken_file_no_longer_silences_the_client_price(self):
        """ПЕРЕВЁРНУТО 12.09.2026, и это самая важная из трёх перемен. Прежде битый файл гасил
        ответ в молчание — законно, пока число клиенту давал ФАЙЛ. Теперь число даёт ДВЕРЬ, и
        молчать из-за чужой поломки значило бы оставить файлу власть над клиентским ответом
        ровно там, где решение владельца её сняло.

        «Не выдумывает» СОХРАНЕНО целиком: в ответе звучат 317/2217 — числа, ПРИШЕДШИЕ ИЗ
        ДВЕРИ, — и ни одного числа файла (437/3059), потому что считать их нечем вовсе."""
        self.on()
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write("{битый файл")
        self.addCleanup(lambda: os.path.exists(p) and os.unlink(p))
        price_source.PATH = p
        price_source._cache.update(key=None, doc=None)
        self.assertIsNone(price_source.load())        # файл правда не читается
        note = self._note()
        self.assertIn("317", note)                    # цена ДВЕРИ прозвучала
        self.assertIn("2217", note)
        for n in ("437", "3059"):                     # выдумки нет: чисел файла нет
            self.assertNotIn(n, note)

    def test_deposit_and_park_model_survive(self):
        """Депозит и имя модели парка — слово ЖИВОЙ ДВЕРИ и им остаются. Форма строки депозита
        сменилась вместе с источником: до 12.09 её собирал `_client_price` из поля `deposit`
        («депозит 3000 ฿»), потому что `reprice` снимал J-текст; теперь J-текст цел и несёт
        депозит своими словами («депозит: 3000 бат»). Число то же и пришло с той же двери."""
        self.on()
        note = self._note()
        self.assertIn("депозит: 3000 бат", note)
        self.assertIn("NMAX 155", note)


if __name__ == "__main__":
    unittest.main()
