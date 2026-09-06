# -*- coding: utf-8 -*-
"""
test_noprice_gate.py — замки ТРЕТЬЕГО ИСХОДА «МОДЕЛЬ БЕЗ ЦЕНЫ»: «не считаю, зову человека».

ЧЕТЫРЕ ГРУППЫ, названные заданием (без них правка не годится):
  (а) у модели цена ЕСТЬ → считается КАК ПРЕЖДЕ и карточки не рождает   — TestPriceExists;
  (б) у модели цены НЕТ → третий исход и карточка владельцу             — TestModelWithoutPrice;
  (в) отправитель НЕДОСТУПЕН → третий исход НЕ превращается в число     — TestSenderDown;
  (г) КОНТРФАКТ: снятая гарантия на том же входе даёт ДРУГОЙ ответ      — TestCounterfact;
  (д) граница класса: что третий исход НЕ включает и почему             — TestBoundaries.

ОТРИЦАТЕЛЬНЫЙ ТЕСТ, РАДИ КОТОРОГО ГРУППА (а) СУЩЕСТВУЕТ: живая пара «клиент сказал NMAX —
в файле написано NMAX 155» выглядит отсутствием цены (буквального ключа в файле нет), а на
деле цена ЕСТЬ и находится по имени прокотированного юнита. Прибор обязан на ней МОЛЧАТЬ.
Это та самая мина, стоившая первого замера 17.08, и здесь она заведена замком.

ПРОВЕРКА ПОВТОРЯЕТ ЖИВОЙ ФОРМАТ ЗАПУСКА, а не идеализированный: hints собирает НАСТОЯЩИЙ
`suggest.extract_booking_hints` по транскрипту с репликой клиента, записку — НАСТОЯЩИЙ
`suggest.build_pricing_note`, правило цены — БОЕВОЙ `price_source.json` из корня репо
(сочинённых от руки таблиц здесь нет: подогнанная под красивое число фикстура и есть тот
голден, который врёт). Отправка владельцу меряется ПОДМЕНЁННЫМ отправителем — боевой
`dispatch_notify` в этих тестах не поднимается ни разу (ленивый импорт `_default_sender`),
клиенту не уходит ни строки. Кругов тренажёра не гоняем вовсе: задача юнитовая.

МОДЕЛЬ БЕЗ ЦЕНЫ БЕРЁТСЯ ЖИВЫМ СПОСОБОМ: `REBEL 300` стои́т в `suggest.KNOWN_MODELS` (клиент
может её назвать) и НЕ стои́т в боевом `price_source.json` (замер 07.09: 14 строк файла, ни
одной REBEL). Это и есть форма класса в проде — байк в парк заведён, а строка правила на него
ещё нет. Фейковый парк здесь ровно затем, чтобы живая дверь такую модель прокотировала.

`PRICE_GATE_TTL_MIN=0` в setUp — объявленный откат сторожа свежести, а не обход: без него
котировка пошла бы к живому мосту за пробами ручек. Свежесть у класса своя граница, и она
проверена отдельным замком (TestBoundaries.test_stale_price_is_not_this_class).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_noprice_gate -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import datetime
import os
import re
import unittest

import noprice_gate
import price_source
import suggest

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.date(2026, 9, 1)

# Срок ВНУТРИ одного периода боевого файла (P1 «ИЮНЬ-СЕНТЯБРЬ» 06-01…09-30): третий исход
# границы сезонов не должен вмешиваться и отбирать у нас предмет замера.
DS, DE = "2026-09-10", "2026-09-20"

TR_NOPRICE = "[клиент]: Здравствуйте! Нужен Rebel 300 с 10 по 20 сентября, сколько будет стоить?"
TR_PRICED = "[клиент]: Здравствуйте! Нужен NMAX с 10 по 20 сентября, сколько будет стоить?"


class Boom(Exception):
    """Отправитель лёг. Ровно то, что обязано НЕ менять исход."""


def live_quote(bike="YAMAHA NMAX 155CC BLACK PHUKET 4255", day=317, total=2217, days=7):
    """Живая котировка формы `pricing.quote` — фикстура снята с боевых тестов, не сочинена."""
    return {"day_price": day, "total": total, "deposit": 3000, "available": True, "days": days,
            "bike": bike, "cap_active": False, "cap_price": None, "text": "%s %s" % (bike, total)}


class Base(unittest.TestCase):
    # Тарифы фейкового Календаря — форма ответа скопирована с фикстуры боевых тестов
    # (test_season_gate.Base.TAR), сочинённой ленты здесь нет.
    TAR = {"NMAX 155": 2800, "REBEL 300": 3900}
    FLEET_NAMES = ["NMAX 155CC BLACK PHUKET 4255", "REBEL 300CC RED PHUKET 7788"]

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("PRICE_GATE_TTL_MIN", noprice_gate.OFF_ENV)}
        os.environ["PRICE_GATE_TTL_MIN"] = "0"      # объявленный откат сторожа свежести
        os.environ.pop(noprice_gate.OFF_ENV, None)
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        self._fresh()
        noprice_gate.reset()
        self.sent = []

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        self._fresh()
        noprice_gate.reset()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _fresh(self):
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _getter(self, available=True):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            bk = suggest._bike_key(bike)
            key = next((k for k in self.TAR if suggest._bike_key(k) in bk), None)
            if key is None:
                return {"ok": False}
            total = self.TAR[key]
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                                         "deposit": 3000, "available": available, "days": days,
                                         "cap_active": False, "cap_price": 12000,
                                         "text": "%s %sd %s" % (bike, days, total)}}
        return fake

    def _hints(self, transcript):
        return suggest.extract_booking_hints(transcript, today=TODAY)

    def _note(self, transcript, available=True):
        self._fresh()
        return suggest.build_pricing_note(self._hints(transcript), lang="ru",
                                          getter=self._getter(available), today=TODAY)

    def _sender(self, boom=False):
        def send(text):
            if boom:
                raise Boom("отправитель лёг")
            self.sent.append(text)
        return send


# --------------------------- (а) у модели цена ЕСТЬ ---------------------------

class TestPriceExists(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1. Модель с ценой обязана считаться КАК ПРЕЖДЕ. Замок не на «примерно
    так же»: записка сравнивается ПОСИМВОЛЬНО с той же запиской при выключенной ветке
    (`NOPRICE_GATE_OFF=1`) — вклад третьего исхода здесь обязан быть РОВНО НУЛЕВЫМ."""

    def test_abbreviation_in_speech_is_not_absence_of_price(self):
        """ГЛАВНЫЙ отрицательный: буквального ключа «NMAX» в файле НЕТ (там «NMAX 155»), цена
        ВЫГЛЯДИТ отсутствующей — а находится по имени прокотированного юнита. Третий исход
        обязан молчать, и цена обязана прозвучать."""
        self.assertNotIn("nmax", [suggest._bike_key(m.get("model"))
                                  for m in price_source.load()["base"]["models"]])
        row, how = price_source.resolve_row(price_source.load(), "NMAX", suggest._bike_key,
                                            {"bike": self.FLEET_NAMES[0]})
        self.assertIsNotNone(row)
        self.assertIn("по имени юнита", how)
        note = self._note(TR_PRICED)
        self.assertNotIn("называет человек", note)
        self.assertTrue(suggest._pc_wl_price_numbers(note), note[:200])

    def test_note_identical_to_gate_off(self):
        with_gate = self._note(TR_PRICED)
        os.environ[noprice_gate.OFF_ENV] = "1"
        try:
            without_gate = self._note(TR_PRICED)
        finally:
            os.environ.pop(noprice_gate.OFF_ENV, None)
        self.assertEqual(with_gate, without_gate)

    def test_no_card_born(self):
        """Ни одной карточки: подменённый отправитель молчит, а боевой сюда не зовётся."""
        kind, _p, _q = suggest._resolve_model_price("NMAX", DS, DE, 10, False,
                                                    getter=self._getter())
        self.assertEqual(kind, "ok")
        self.assertIsNone(noprice_gate.note_for(kind, self._hints(TR_PRICED), model="NMAX",
                                                sender=self._sender()))
        self.assertEqual(self.sent, [])


# --------------------------- (б) у модели цены НЕТ ----------------------------

class TestModelWithoutPrice(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2. Модель без строки в правиле даёт третий исход И карточку."""

    def test_verdict_is_no_row(self):
        """Состояние данных названо ИМЕНЕМ, а не догадкой: правило прочитано, живая дверь модель
        прокотировала, строки для неё в правиле нет."""
        res = suggest._safe_quote_for_model("REBEL", DS, DE, getter=self._getter())
        self.assertEqual(res["status"], "error")
        self.assertIsNone(res["quote"])
        self.assertEqual(res.get("noprice"), price_source.WHY_NO_ROW)
        self.assertEqual(noprice_gate.kind_of(res), noprice_gate.KIND_NO_ROW)
        kind, _p, _q = suggest._resolve_model_price("REBEL", DS, DE, 10, False,
                                                    getter=self._getter())
        self.assertEqual(kind, noprice_gate.KIND_NO_ROW)

    def test_client_gets_answer_without_any_number(self):
        note = self._note(TR_NOPRICE)
        self.assertIn("называет человек", note)
        # 1) ЧИСЛА НЕТ ВООБЩЕ: белый список пост-чека пуст, значит ЛЮБАЯ цифра в ответе LLM
        #    будет заклеймлена. Это и есть «без выдуманного числа», проверенное живым замком.
        self.assertEqual(suggest._pc_wl_price_numbers(noprice_gate.client_note("ru")), set())
        self.assertEqual(suggest.computed_price_figures(note), set())
        # 2) МОЛЧАНИЕ ЗАПРЕЩЕНО РОВНО ТАК ЖЕ, как выдуманное число: записка требует ответить...
        self.assertIn("МОЛЧАТЬ ТОЖЕ НЕЛЬЗЯ", note)
        # 3) ...назвать человека и не подставить вместо цены соседнюю модель.
        self.assertIn("СРЕДНИМ по классу", note)
        self.assertIn("Цены ДРУГИХ моделей вместо неё тоже НЕ называй", note)
        self.assertNotIn("[QUOTE]", note)

    def test_owner_card_carries_model_dates_reason(self):
        h = self._hints(TR_NOPRICE)
        out = noprice_gate.note_for(noprice_gate.KIND_NO_ROW, h, model="REBEL 300",
                                    sender=self._sender())
        self.assertIsNotNone(out)
        self.assertEqual(len(self.sent), 1)
        card = self.sent[0]
        self.assertIn(noprice_gate.CARD_HEAD, card)
        self.assertIn("REBEL", card.upper())                  # модель
        self.assertIn(DS, card)                               # даты
        self.assertIn(DE, card)
        self.assertIn("модели нет в записанном правиле", card)  # причина
        self.assertNotIn("Отправить как есть", card)          # кнопок нет: сообщает, а не спрашивает

    def test_one_card_per_case_not_per_message(self):
        """Клиент пишет трижды — карточка ОДНА. Иначе владелец получает поток вместо сигнала."""
        h = self._hints(TR_NOPRICE)
        for _ in range(3):
            self.assertIsNotNone(noprice_gate.note_for(noprice_gate.KIND_NO_ROW, h,
                                                       model="REBEL 300",
                                                       sender=self._sender()))
        self.assertEqual(len(self.sent), 1)

    def test_not_judged_row_is_the_same_class(self):
        """Второе состояние класса: строка в правиле ЕСТЬ, а базы в ней нет («НЕ СУДИМО»).
        Утверждение о модели такое же положительное — третий исход тот же."""
        doc = price_source.load()
        row = dict(doc["base"]["models"][0], judged=False, base_thb_per_day=None)
        doc2 = dict(doc, base={"models": [row]})
        day, info = price_source.day_price(doc2, row["model"], datetime.date(2026, 9, 10), 10,
                                           suggest._bike_key)
        self.assertIsNone(day)
        self.assertEqual(info.get("code"), price_source.WHY_NOT_JUDGED)
        self.assertEqual(noprice_gate.kind_of({"status": "error", "quote": None,
                                               "noprice": info["code"]}),
                         noprice_gate.KIND_NOT_JUDGED)

    def test_english_note_is_also_numberless(self):
        en = noprice_gate.client_note("en")
        self.assertEqual(suggest._pc_wl_price_numbers(en), set())
        self.assertIn("STAYING SILENT IS ALSO FORBIDDEN", en)


# --------------------------- (в) отправитель недоступен -----------------------

class TestSenderDown(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3. Отправитель лёг — исход прежний: ответ БЕЗ числа.

    Замок стоит именно здесь, потому что соблазн обратный: «человека позвать не смогли, значит
    ответим сами». Такой ветки нет ни одной, и тест это доказывает не чтением кода, а исходом."""

    def test_note_survives_dead_sender(self):
        h = self._hints(TR_NOPRICE)
        out = noprice_gate.note_for(noprice_gate.KIND_NO_ROW, h, model="REBEL 300",
                                    sender=self._sender(boom=True))
        self.assertIsNotNone(out)
        self.assertIn("называет человек", out)
        self.assertEqual(suggest._pc_wl_price_numbers(out), set())
        self.assertEqual(self.sent, [])                       # карточка не ушла — и это видно

    def test_raise_card_never_raises(self):
        self.assertFalse(noprice_gate.raise_card(self._hints(TR_NOPRICE), "REBEL 300",
                                                 "модели нет в правиле",
                                                 sender=self._sender(boom=True)))

    def test_no_digits_leak_into_client_text(self):
        """Ни одной цифры в тексте клиенту — иначе она стала бы разрешённой к произнесению ценой."""
        self.assertIsNone(re.search(r"\d", noprice_gate.client_note("ru")))
        self.assertIsNone(re.search(r"\d", noprice_gate.client_note("en")))


# --------------------------- (г) контрфакт ------------------------------------

class TestCounterfact(Base):
    """КОНТРФАКТ, названный заданием: при СНЯТОЙ гарантии тот же вход обязан дать ДРУГОЙ ответ.
    Совпал бы — гарантия не доказана, и это было бы записано словами, а не спрятано."""

    def test_same_input_different_answer(self):
        with_gate = self._note(TR_NOPRICE)
        os.environ[noprice_gate.OFF_ENV] = "1"
        try:
            without_gate = self._note(TR_NOPRICE)
        finally:
            os.environ.pop(noprice_gate.OFF_ENV, None)
        self.assertNotEqual(with_gate, without_gate)
        # ЧЕМ ИМЕННО разный: с гарантией клиенту сказано, что цену называет человек; без неё —
        # прежнее «уточню цену и вернусь», и человека не зовёт никто.
        self.assertIn("называет человек", with_gate)
        self.assertNotIn("называет человек", without_gate)
        self.assertIn("уточнишь цену и вернёшься", without_gate)
        # Числа не появляется НИ ТАМ, НИ ТАМ: вклад класса — позвать человека, а не спрятать
        # цифру. Сказать «без гарантии бот врал числом» было бы неправдой, и мы её не говорим.
        self.assertEqual(suggest._pc_wl_price_numbers(without_gate), set())

    def test_card_disappears_with_the_guarantee(self):
        h = self._hints(TR_NOPRICE)
        self.assertIsNone(noprice_gate.note_for(noprice_gate.KIND_NO_ROW, h, model="REBEL 300",
                                                sender=self._sender(),
                                                env={noprice_gate.OFF_ENV: "1"}))
        self.assertEqual(self.sent, [])


# --------------------------- (д) границы класса -------------------------------

class TestBoundaries(Base):
    """ЧТО ТРЕТИЙ ИСХОД НЕ ВКЛЮЧАЕТ. Без этих замков класс расползётся молча, а карточка
    владельцу превратится в поток — то есть в шум, который перестанут читать."""

    def test_stale_price_is_not_this_class(self):
        """«Цена протухла» и «цены нет» — РАЗНЫЕ вещи, и свежесть судит отдельный механизм
        (`price_gate`). Он стои́т РАНЬШЕ по потоку и гасит цену СВОЕЙ карточкой; при несвежести
        число в правиле ЕСТЬ. Разводятся они не словом, а по построению: гашение сторожа ключа
        `noprice` не несёт и до третьего исхода не доходит ни одной дорогой."""
        save = suggest.price_gate.allow
        os.environ["PRICE_GATE_TTL_MIN"] = "60"   # врезка сторожа включена
        try:
            suggest.price_gate.allow = lambda *a, **k: (False, "[устарело] ЦЕНА НЕ НАЗВАНА")
            res = suggest._safe_quote_for_model("NMAX", DS, DE, getter=self._getter())
        finally:
            suggest.price_gate.allow = save
            os.environ["PRICE_GATE_TTL_MIN"] = "0"
        self.assertEqual(res["status"], "error")
        self.assertNotIn("noprice", res)
        self.assertIsNone(noprice_gate.kind_of(res))

    def test_unreadable_rule_is_not_this_class(self):
        """Файла нет / файл битый — это «не смог проверить», а не «цены нет». Мёртвый файл
        поднял бы карточку на КАЖДЫЙ диалог, и один честный сигнал утонул бы."""
        save = price_source.PATH
        try:
            price_source.PATH = os.path.join(HERE, "нет-такого-файла-price_source.json")
            price_source._cache.update(key=None, doc=None)
            res = price_source.reprice({"status": "ok", "quote": live_quote()},
                                       "NMAX", DS, DE, suggest._bike_key)
        finally:
            price_source.PATH = save
            price_source._cache.update(key=None, doc=None)
        self.assertEqual(res["status"], "error")
        self.assertNotIn("noprice", res)
        self.assertIsNone(noprice_gate.kind_of(res))

    def test_ambiguous_model_is_not_this_class(self):
        """«CB» подходит трём строкам правила: цена ЕСТЬ, неизвестно лишь какая из трёх. Это
        «не угадываем», а не «цены нет» — человека этим не зовём."""
        doc = price_source.load()
        day, info = price_source.day_price(doc, "CB", datetime.date(2026, 9, 10), 10,
                                           suggest._bike_key)
        self.assertIsNone(day)
        self.assertEqual(info.get("code"), price_source.WHY_AMBIGUOUS)
        self.assertNotIn(price_source.WHY_AMBIGUOUS, price_source.MODEL_HAS_NO_PRICE)
        self.assertIsNone(noprice_gate.kind_of({"status": "error", "quote": None,
                                                "noprice": info["code"]}))

    def test_date_and_term_gaps_are_not_this_class(self):
        """Дата вне периодов и срок вне корзин — утверждения о ДАТЕ и СРОКЕ, а не о модели:
        число у неё при этом может быть.

        ЗАМЕР 07.09, названный вслух: в БОЕВОМ файле периоды заданы парами «ММ-ДД» и покрывают
        год ЦЕЛИКОМ (девять периодов, от 06-01 до 05-31), поэтому «дата вне периодов» живым
        файлом НЕ ДОСТИЖИМА ни одной датой — 2019 год честно попадает в P5. Отсюда доска здесь
        режется руками (периодов ноль), и это не подгонка: ветку надо проверить, а живого входа
        у неё нет. Срок вне корзин достижим по-настоящему — им и меряем."""
        doc = price_source.load()
        self.assertIsNotNone(price_source.period_of(doc, datetime.date(2019, 1, 1)))
        _d, out_date = price_source.day_price(dict(doc, season={"periods": []}), "NMAX 155",
                                              datetime.date(2026, 9, 10), 10, suggest._bike_key)
        self.assertEqual(out_date.get("code"), price_source.WHY_DATE_OUT)
        _d, out_term = price_source.day_price(doc, "NMAX 155", datetime.date(2026, 9, 10), 100000,
                                              suggest._bike_key)
        self.assertEqual(out_term.get("code"), price_source.WHY_BUCKET_OUT)
        for info in (out_date, out_term):
            self.assertNotIn(info["code"], price_source.MODEL_HAS_NO_PRICE)
            self.assertIsNone(noprice_gate.kind_of({"status": "error", "quote": None,
                                                    "noprice": info["code"]}))

    def test_busy_bike_is_not_this_class(self):
        """Байк занят — цена есть, нет НАЛИЧИЯ. Ответ остаётся прежним, карточки нет."""
        kind, _p, _q = suggest._resolve_model_price("NMAX", DS, DE, 10, False,
                                                    getter=self._getter(available=False))
        self.assertEqual(kind, "none")
        self.assertIsNone(noprice_gate.note_for(kind, self._hints(TR_PRICED), model="NMAX",
                                                sender=self._sender()))
        self.assertEqual(self.sent, [])

    def test_min_term_branch_untouched(self):
        """Ветка «срок короче минимального» — свой разговор и свой ответ; класс закрывается
        доказательством на ОДНОМ месте, и это место не там."""
        kind, phrase, _q = suggest._resolve_model_price("NMAX", DS, "2026-09-11", 1, False,
                                                        getter=self._getter())
        self.assertEqual(kind, "min")
        self.assertIsNone(noprice_gate.note_for(kind, self._hints(TR_PRICED), model="NMAX",
                                                sender=self._sender()))
        self.assertEqual(self.sent, [])
        self.assertIn("сдаём от", phrase)

    def test_declared_rollback_kills_branch(self):
        self.assertIsNone(noprice_gate.note_for(noprice_gate.KIND_NOT_JUDGED,
                                                self._hints(TR_NOPRICE), model="REBEL 300",
                                                sender=self._sender(),
                                                env={noprice_gate.OFF_ENV: "1"}))
        self.assertEqual(self.sent, [])

    def test_module_does_not_import_suggest(self):
        """Петля импорта: `suggest` тянет этот модуль верхним импортом. Появится здесь
        `import suggest` — петля вернётся, и замок обязан покраснеть."""
        with open(os.path.join(HERE, "noprice_gate.py"), encoding="utf-8") as f:
            body = f.read()
        self.assertIsNone(re.search(r"^\s*import suggest", body, re.M))
        self.assertIsNone(re.search(r"^\s*from suggest", body, re.M))

    def test_class_is_named_by_codes_not_by_russian_substring(self):
        """Решение стои́т на КОДЕ, а не на подстроке русской фразы: перепиши формулировку —
        и подстрочный разбор отвалился бы МОЛЧА, в сторону «класс не сработал»."""
        self.assertEqual(set(price_source.MODEL_HAS_NO_PRICE),
                         {price_source.WHY_NO_ROW, price_source.WHY_NOT_JUDGED})
        self.assertEqual(set(noprice_gate._KIND_OF_CODE), set(price_source.MODEL_HAS_NO_PRICE))
        for code in (price_source.WHY_AMBIGUOUS, price_source.WHY_DATE_OUT,
                     price_source.WHY_BUCKET_OUT, price_source.WHY_NO_MULT, None, "", "no_row "):
            self.assertIsNone(noprice_gate.kind_of({"noprice": code}), code)

    def test_rebel_is_the_live_shape_of_the_class(self):
        """Модель ФИКСТУРЫ взята ЖИВЫМ способом, а не сочинена: она есть в универсуме моделей
        (клиент может её назвать) и её нет в боевом правиле цены. Появится строка REBEL в
        файле — тест покраснеет, и это правильный красный: предмет замера исчез.

        ЧТО ЭТОТ ТЕСТ НЕ ДОКАЗЫВАЕТ (поправка 07.09.2026, замер живого парка). Прежняя
        формулировка звала REBEL 300 «живой формой класса в проде» — это НЕВЕРНО, и утверждения
        такого здесь нет ни одного: обе проверки ниже смотрят КАТАЛОГ и ФАЙЛ, а про ПАРК не
        спрашивают. Замер 07.09 (Bridge `action=fleet`, источник `live`, 38 юнитов): REBEL 300 в
        парке ОТСУТСТВУЕТ, и живая дверь на неё отвечает `no_candidates` — то есть до `reprice`
        дело не доходит вовсе, и третий исход на ней НЕ РОЖДАЕТСЯ. Это состояние из графы «НЕ
        включает» границы класса, а не вход в него. Живой вход класса требует ТРЁХ вещей разом:
        модель в каталоге (иначе речь клиента не резолвится), юнит в парке и свободен (иначе
        дверь отвечает раньше файла) и числа для неё в правиле нет. На 07.09 такого входа нет:
        13 моделей живого парка, у всех 13 строка с базой. Разбор и числа —
        docs/artifacts/2026-09-07-noprice-gate-станция-корпуса.md."""
        self.assertIn("REBEL 300", [name for name, _key in suggest.KNOWN_MODELS])
        keys = [suggest._bike_key(m.get("model")) for m in price_source.load()["base"]["models"]]
        self.assertNotIn("rebel300", keys)
        self.assertFalse([k for k in keys if k and k in "rebel300"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
