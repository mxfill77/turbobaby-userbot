# -*- coding: utf-8 -*-
"""
test_door_price.py — замки решения владельца 12.09.2026: ЦЕНА КЛИЕНТУ ЕСТЬ ЧИСЛО ЖИВОЙ ДВЕРИ.

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА, НАЗВАННЫЕ ЗАДАНИЕМ (без них правка не принимается):
  (а) дверь дала цену → в ответе РОВНО её число, и подмены нет      — TestDoorNumberWins;
  (б) дверь молчит    → слова про человека и НИ ОДНОГО числа цены   — TestDoorSilent;
  (в) файл и дверь разошлись → строка в журнале ЕСТЬ, а клиенту
      ушло число ДВЕРИ                                              — TestDivergenceIsLogged.
Плюс две группы, без которых первые три зеленели бы и на сломанном коде:
  (г) границы: чего третий исход НЕ включает (занятость, кепка)     — TestBoundaries;
  (д) КОНТРФАКТ: на ТОМ ЖЕ входе прежний счёт по файлу давал ДРУГОЕ
      число — значит замок (а) меряет правку, а не совпадение       — TestCounterfact.

ПОЧЕМУ «ПОДМЕНЫ НЕТ» ПРОВЕРЯЕТСЯ `assertIs`, А НЕ РАВЕНСТВОМ ЧИСЕЛ. Равенство числа зеленеет
и тогда, когда котировку скопировали и переписали в неё то же самое значение, — то есть не
различает «не подменяли» и «подменили на совпавшее». `client_quote` обязан вернуть ТОТ ЖЕ
ОБЪЕКТ, и на фикстуре, где файл и дверь СОШЛИСЬ, только `assertIs` это и ловит.

ПРОВЕРКА ПОВТОРЯЕТ ЖИВОЙ ФОРМАТ ЗАПУСКА, а не идеализированный: hints собирает НАСТОЯЩИЙ
`suggest.extract_booking_hints` по транскрипту с репликой клиента, записку — НАСТОЯЩИЙ
`suggest.build_pricing_note`, счёт по файлу — БОЕВОЙ `price_source.json` из корня репо
(сочинённых от руки таблиц здесь нет: подогнанная под красивое число фикстура и есть тот
голден, который врёт). Живая дверь подменена гэттером — СЕТИ В ЭТОМ НАБОРЕ НЕТ ВОВСЕ, ни один
боевой мост не поднимается, клиенту не уходит ни строки, боевых таблиц не открывается.

ЧИСЛА ФИКСТУРЫ ВЗЯТЫ ИЗ ЖИВОГО ЗАМЕРА 12.09, А НЕ СОЧИНЕНЫ
(`docs/artifacts/2026-09-12-cena-tri-chisla-sezon.md`): дверь на XMAX 300 нового поколения
06.10.2026→11.10.2026 дала 704 ฿/сут и 3520 за 5 суток (сошлось с листом владельца до бата),
а файл на том же вопросе даёт 682 ฿/сут и 3410 за срок. Ровно эта пара и есть предмет замка (в).

`PRICE_GATE_TTL_MIN=0` в setUp — объявленный откат сторожа свежести, а не обход: без него
соседние ветки пошли бы к живому мосту за пробами ручек. Клиентское число сторож с 12.09 не
гасит вовсе, и это проверено отдельным замком (TestBoundaries).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_door_price -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import datetime
import logging
import os
import re
import unittest

import door_price
import price_source
import suggest

TODAY = datetime.date(2026, 9, 12)

# Окно живого замера 12.09: 5 суток ВНУТРИ одного периода файла (P2 «ОКТЯБРЬ»), чтобы третий
# исход границы сезонов не вмешался и не отобрал предмет замера.
DS, DE = "2026-10-06", "2026-10-11"

# Числа живого замера 12.09 (артефакт ЦЕНА tri-chisla-sezon 1209).
DOOR_DAY, DOOR_TOTAL = 704, 3520       # дверь = лист владельца, сошлось до бата
FILE_DAY, FILE_TOTAL = 682, 3410       # счёт по файлу на том же вопросе

TR = ("[клиент]: Здравствуйте! Нужен XMAX 300 с 6 по 11 октября, сколько будет стоить?")


class _Capture(logging.Handler):
    """Ловушка строк журнала. Меряем ПРОДУКТОВЫЙ журнал модуля, а не собственную переменную:
    задание требует строку В ЖУРНАЛЕ, и тест, следящий за возвратом функции, этого не проверил
    бы вовсе (прибор мог бы вернуть текст и не записать его никуда)."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class Base(unittest.TestCase):
    # Парк фейкового Календаря: имя юнита несёт метку NEW — по ней файл разводит поколения XMAX.
    FLEET_NAMES = ["XMAX 300CC NEW BLACK PHUKET 8969"]

    def setUp(self):
        self._env = {k: os.environ.get(k)
                     for k in ("PRICE_GATE_TTL_MIN", door_price.OFF_ENV,
                               "PRICE_SOURCE_SEASON", "PRICE_SHEET_RULE_ON")}
        os.environ["PRICE_GATE_TTL_MIN"] = "0"      # объявленный откат сторожа свежести
        for k in (door_price.OFF_ENV, "PRICE_SOURCE_SEASON", "PRICE_SHEET_RULE_ON"):
            os.environ.pop(k, None)
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        self._fresh()
        self.cap = _Capture()
        door_price.log.addHandler(self.cap)
        self._lvl = door_price.log.level
        door_price.log.setLevel(logging.INFO)

    def tearDown(self):
        door_price.log.removeHandler(self.cap)
        door_price.log.setLevel(self._lvl)
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        self._fresh()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _fresh(self):
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None, skipped=None)

    # ----------------------------- живая дверь, подменённая гэттером -----------------------
    def _getter(self, day=DOOR_DAY, total=DOOR_TOTAL, available=True, priced=True, ok=True):
        """Форма ответа скопирована с фикстур боевых тестов (`test_noprice_gate.Base._getter`).
        `priced=False` — дверь ОТВЕЧАЕТ, но БЕЗ ЦЕНЫ: живой случай, который до 12.09 сливался
        с общим «источник не ответил» и уходил в фолбэк без слов про человека."""
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            if not ok:
                return {"ok": False}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            data = {"deposit": 7000, "available": available, "days": days, "bike": bike,
                    "cap_active": False, "cap_price": None}
            if priced:
                data.update({"day_price": day, "total": total,
                             "text": "%s | дней: %s, стоимость: %s" % (bike, days, total)})
            return {"ok": True, "data": data}
        return fake

    def _hints(self):
        return suggest.extract_booking_hints(TR, today=TODAY)

    def _note(self, **kw):
        self._fresh()
        return suggest.build_pricing_note(self._hints(), lang="ru",
                                          getter=self._getter(**kw), today=TODAY)

    @staticmethod
    def _numbers(text):
        """Все числа текста — тем же взглядом, каким их видит пост-чек черновика."""
        return set(re.findall(r"\d+", text or ""))


# ───────────────────── (а) ДВЕРЬ ДАЛА ЦЕНУ → В ОТВЕТЕ РОВНО ЕЁ ЧИСЛО ─────────────────────

class TestDoorNumberWins(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1. Число двери доезжает до ответа, и подмены НЕ происходит."""

    def test_quote_object_is_returned_as_is(self):
        """Подмены нет ПО ПОСТРОЕНИЮ: возвращается ТОТ ЖЕ объект, а не копия с числами."""
        res = {"status": "ok", "quote": {"day_price": DOOR_DAY, "total": DOOR_TOTAL,
                                         "deposit": 7000, "available": True, "days": 5,
                                         "bike": self.FLEET_NAMES[0], "text": "J-строка"}}
        out = door_price.client_quote(res, "XMAX 300", DS, DE, suggest._bike_key)
        self.assertIs(out, res)                      # НЕ равенство — тождество объекта
        self.assertEqual(out["quote"]["total"], DOOR_TOTAL)
        self.assertEqual(out["quote"]["day_price"], DOOR_DAY)

    def test_answer_path_keeps_the_door_number(self):
        """Живой путь ответа целиком: число двери на месте, числа файла в котировке НЕТ."""
        res = suggest._safe_quote_for_model("XMAX 300", DS, DE, getter=self._getter())
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["quote"]["total"], DOOR_TOTAL)
        self.assertEqual(res["quote"]["day_price"], DOOR_DAY)
        self.assertNotEqual(res["quote"]["total"], FILE_TOTAL)
        kind, phrase, q = suggest._resolve_model_price("XMAX 300", DS, DE, 5, False,
                                                       getter=self._getter())
        self.assertEqual(kind, "ok")
        self.assertIn(str(DOOR_TOTAL), phrase)
        self.assertNotIn(str(FILE_TOTAL), phrase)
        self.assertEqual(q["total"], DOOR_TOTAL)     # котировка для деривативов — тоже дверина

    def test_client_note_carries_the_door_number_and_not_the_file_one(self):
        """Записка клиенту (боевая сборка) несёт число двери и НЕ несёт числа файла."""
        note = self._note()
        self.assertIn(str(DOOR_TOTAL), note)
        self.assertNotIn(str(FILE_TOTAL), note)
        self.assertNotIn("считает человек", note)    # цена есть → третий исход молчит
        # Белый список пост-чека держит ЧИСЛА (int), а не их строки: цена двери РАЗРЕШЕНА к
        # произнесению, цена файла — нет, и ровно это здесь и меряется.
        allowed = suggest._pc_wl_price_numbers(note)
        self.assertIn(DOOR_TOTAL, allowed)
        self.assertNotIn(FILE_TOTAL, allowed)

    def test_j_text_of_the_door_survives(self):
        """Дословная строка столбца J больше НЕ снимается: до 12.09 её снимал `reprice`
        (`out.pop("text")`), потому что рядом вставал чужой счёт. Чужого счёта нет — строка
        листа доезжает до клиента как есть, ради чего правило цен v2 п.5 и писалось."""
        res = suggest._safe_quote_for_model("XMAX 300", DS, DE, getter=self._getter())
        self.assertTrue(str(res["quote"].get("text") or "").strip())
        self.assertIn(str(DOOR_TOTAL), res["quote"]["text"])


# ───────────────────── (б) ДВЕРЬ МОЛЧИТ → СЛОВА ПРО ЧЕЛОВЕКА, НОЛЬ ЧИСЕЛ ─────────────────

class TestDoorSilent(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2. Дверь не ответила / ответила без цены — бот НЕ считает сам."""

    def test_dead_door_is_named_third_outcome(self):
        for res in (None, {}, {"status": "error", "quote": None}, {"status": "ok", "quote": None}):
            out = door_price.client_quote(res, "XMAX 300", DS, DE, suggest._bike_key)
            self.assertEqual(out["status"], "error", res)
            self.assertIsNone(out["quote"], res)
            self.assertEqual(door_price.kind_of(out), door_price.KIND_DOOR_NO_PRICE, res)

    def test_door_answered_without_a_price_is_also_the_third_outcome(self):
        """ДВЕРЬ ОТВЕТИЛА `ok` И БЕЗ ЦЕНЫ — свой живой случай, а не «источник не ответил»."""
        res = suggest._safe_quote_for_model("XMAX 300", DS, DE, getter=self._getter(priced=False))
        self.assertEqual(res["status"], "error")
        self.assertEqual(door_price.kind_of(res), door_price.KIND_DOOR_NO_PRICE)
        kind, _phrase, q = suggest._resolve_model_price("XMAX 300", DS, DE, 5, False,
                                                        getter=self._getter(priced=False))
        self.assertEqual(kind, door_price.KIND_DOOR_NO_PRICE)
        self.assertIsNone(q)

    def test_answer_says_a_human_counts_it(self):
        """Слова про человека ЗВУЧАТ — молчание запрещено наравне с выдумкой."""
        _kind, phrase, _q = suggest._resolve_model_price("XMAX 300", DS, DE, 5, False,
                                                         getter=self._getter(priced=False))
        self.assertIn("считает человек", phrase)
        self.assertIn("МОЛЧАТЬ ТОЖЕ НЕЛЬЗЯ", phrase)

    def test_not_a_single_price_number_is_allowed(self):
        """НИ ОДНОГО ЧИСЛА: белый список пост-чека пуст, значит ЛЮБАЯ цифра ответа LLM будет
        заклеймлена. Это и есть «без выдуманного числа», проверенное живым замком, а не
        обещанием. Отдельно проверено, что цифр нет и в самой строке про человека: попади
        туда хоть одна, она стала бы РАЗРЕШЁННОЙ к произнесению ценой."""
        self.assertEqual(self._numbers(door_price.HUMAN_LINE), set())
        self.assertEqual(self._numbers(door_price.HUMAN_LINE_EN), set())
        self.assertEqual(suggest._pc_wl_price_numbers(door_price.client_note("ru")), set())
        note = self._note(priced=False)
        self.assertIn("считает человек", note)
        self.assertEqual(suggest._pc_wl_price_numbers(note), set())
        for n in (DOOR_DAY, DOOR_TOTAL, FILE_DAY, FILE_TOTAL):
            self.assertNotIn(str(n), note)

    def test_no_number_appears_even_though_the_file_could_count_it(self):
        """ГЛАВНЫЙ отрицательный группы: файл на этот вопрос ЦЕНУ ЗНАЕТ (он и дал бы 3410),
        а бот всё равно не называет её — потому что молчит ДВЕРЬ. Ровно эта ветка и есть
        «бот НЕ считает сам»: до 12.09 подставить сюда счёт по файлу было одной строкой."""
        total, _day, why = door_price.file_total(
            "XMAX 300", DS, DE, suggest._bike_key,
            {"days": 5, "bike": self.FLEET_NAMES[0]})
        self.assertEqual(total, FILE_TOTAL, why)     # файл ЗНАЕТ число
        note = self._note(priced=False)
        self.assertNotIn(str(FILE_TOTAL), note)      # и оно НЕ прозвучало


# ───────────────── (в) ФАЙЛ И ДВЕРЬ РАЗОШЛИСЬ → СТРОКА В ЖУРНАЛЕ, ЧИСЛО ДВЕРИ ────────────

class TestDivergenceIsLogged(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3. Расхождение перестаёт быть молчаливым, но ответа НЕ меняет."""

    def _diverged(self):
        return [ln for ln in self.cap.lines if ln.startswith("ЦЕНА РАСХОЖДЕНИЕ")]

    def test_line_is_written_and_client_gets_the_door_number(self):
        res = suggest._safe_quote_for_model("XMAX 300", DS, DE, getter=self._getter())
        lines = self._diverged()
        self.assertEqual(len(lines), 1, self.cap.lines)
        line = lines[0]
        self.assertIn("XMAX", line)                        # модель
        self.assertIn(DS, line)                            # даты — обе
        self.assertIn(DE, line)
        self.assertIn(str(DOOR_TOTAL), line)               # ОБА числа
        self.assertIn(str(FILE_TOTAL), line)
        self.assertIn("%+d" % (FILE_TOTAL - DOOR_TOTAL), line)   # и разница со знаком
        # А КЛИЕНТУ УШЛО ЧИСЛО ДВЕРИ — ответ не остановлен и не подменён:
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["quote"]["total"], DOOR_TOTAL)

    def test_agreement_to_the_baht_is_silent(self):
        """Сошлись до бата → строки НЕТ. Прибор, пишущий всегда, — это шум, а не прибор."""
        suggest._safe_quote_for_model("XMAX 300", DS, DE,
                                      getter=self._getter(day=FILE_DAY, total=FILE_TOTAL))
        self.assertEqual(self._diverged(), [])

    def test_one_baht_is_already_a_divergence(self):
        """Порог РОВНО ОДИН БАТ, как названо решением: 3411 против 3410 уже строка."""
        self.assertEqual(door_price.DIVERGENCE_MIN_BAHT, 1)
        suggest._safe_quote_for_model("XMAX 300", DS, DE,
                                      getter=self._getter(day=FILE_DAY, total=FILE_TOTAL + 1))
        self.assertEqual(len(self._diverged()), 1, self.cap.lines)

    def test_instrument_never_touches_the_answer(self):
        """СОРВАВШИЙСЯ ПРИБОР НЕ МЕНЯЕТ ОТВЕТА НИ ОДНОЙ ВЕТКОЙ: прибор, уронивший цену
        клиенту, хуже отсутствующего прибора."""
        save = price_source.term_total
        try:
            price_source.term_total = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("файл лёг"))
            res = suggest._safe_quote_for_model("XMAX 300", DS, DE, getter=self._getter())
        finally:
            price_source.term_total = save
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["quote"]["total"], DOOR_TOTAL)
        self.assertEqual(self._diverged(), [])

    def test_rollback_word_silences_the_instrument_only(self):
        """Объявленный откат `DOOR_PRICE_OFF=1` гасит ПРИБОР, а не источник: число двери
        по-прежнему уходит клиенту без подмены. Ветки «вернуть счёт по файлу в ответ» нет."""
        os.environ[door_price.OFF_ENV] = "1"
        try:
            res = suggest._safe_quote_for_model("XMAX 300", DS, DE, getter=self._getter())
        finally:
            os.environ.pop(door_price.OFF_ENV, None)
        self.assertEqual(self._diverged(), [])
        self.assertEqual(res["quote"]["total"], DOOR_TOTAL)


# ───────────────────────────── (г) ГРАНИЦЫ КЛАССА ────────────────────────────────────────

class TestBoundaries(Base):
    """Чего третий исход НЕ включает. Каждая строка — готовый ложный диагноз."""

    def test_busy_bike_is_not_the_third_outcome(self):
        """ЗАНЯТОСТЬ — ответ ПРО НАЛИЧИЕ, и он проходит НАСКВОЗЬ нетронутым: у него свой
        разговор и своя фраза, цену он не судит вовсе."""
        res = {"status": "none_available", "quote": None}
        out = door_price.client_quote(res, "XMAX 300", DS, DE, suggest._bike_key)
        self.assertIs(out, res)
        self.assertIsNone(door_price.kind_of(out))
        kind, phrase, _q = suggest._resolve_model_price(
            "XMAX 300", DS, DE, 5, False, getter=self._getter(available=False))
        self.assertEqual(kind, "none")
        self.assertNotIn("считает человек", phrase)

    def test_stale_file_no_longer_silences_the_client_number(self):
        """СТОРОЖ СВЕЖЕСТИ ФАЙЛА КЛИЕНТСКОЕ ЧИСЛО БОЛЬШЕ НЕ ГАСИТ — названо вслух, потому что
        до 12.09 гасил. Сторож, отказавший наотрез, обязан оставить ответ двери целым: его
        предмет — старение СНИМКА, а снимок клиентских чисел больше не даёт."""
        save = suggest.price_gate.allow
        try:
            suggest.price_gate.allow = lambda *a, **k: (False, "[устарело] ЦЕНА НЕ НАЗВАНА")
            res = suggest._safe_quote_for_model("XMAX 300", DS, DE, getter=self._getter())
        finally:
            suggest.price_gate.allow = save
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["quote"]["total"], DOOR_TOTAL)

    def test_has_price_reads_the_same_fields_as_the_client_phrase(self):
        """`has_price` спрашивает РОВНО те поля, из которых клиентская фраза берёт число.
        Разойдись они — бот либо промолчал бы при живой цене, либо назвал бы пустоту."""
        self.assertFalse(door_price.has_price(None))
        self.assertFalse(door_price.has_price({}))
        self.assertFalse(door_price.has_price({"available": True, "deposit": 7000, "days": 5}))
        self.assertFalse(door_price.has_price({"text": "   "}))
        self.assertTrue(door_price.has_price({"text": "J-строка 3520"}))
        self.assertTrue(door_price.has_price({"day_price": DOOR_DAY}))
        self.assertTrue(door_price.has_price({"total": DOOR_TOTAL}))
        self.assertTrue(door_price.has_price({"cap_price": 9900}))
        # Ноль — это ЦЕНА (и неправдоподобная), а не отсутствие числа: сливать их нельзя.
        self.assertTrue(door_price.has_price({"total": 0}))

    def test_module_does_not_import_suggest(self):
        """Петли импорта нет и быть не должно: `suggest` импортирует `door_price` сверху."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "door_price.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("import suggest", src)


# ───────────────────────────── (д) КОНТРФАКТ ─────────────────────────────────────────────

class TestCounterfact(Base):
    """Замок (а) обязан мерить ПРАВКУ, а не совпадение чисел. Контрфакт: тот же вход, прежний
    счёт по файлу — и число ДРУГОЕ. Без этой группы (а) зеленел бы и на неснятой подмене."""

    def test_old_path_would_have_replaced_the_number(self):
        q = {"day_price": DOOR_DAY, "total": DOOR_TOTAL, "deposit": 7000, "available": True,
             "days": 5, "bike": self.FLEET_NAMES[0], "cap_active": False, "cap_price": None,
             "text": "J-строка %s" % DOOR_TOTAL}
        old = price_source.reprice({"status": "ok", "quote": dict(q)}, "XMAX 300", DS, DE,
                                   suggest._bike_key)
        self.assertEqual(old["status"], "ok")
        self.assertEqual(old["quote"]["total"], FILE_TOTAL)      # прежний путь: число ФАЙЛА
        self.assertIsNone(old["quote"].get("text"))              # и J-строка СНЯТА
        new = door_price.client_quote({"status": "ok", "quote": q}, "XMAX 300", DS, DE,
                                      suggest._bike_key)
        self.assertEqual(new["quote"]["total"], DOOR_TOTAL)      # новый путь: число ДВЕРИ
        self.assertTrue(new["quote"]["text"])                    # J-строка ЦЕЛА
        self.assertNotEqual(FILE_TOTAL, DOOR_TOTAL)              # числа правда разные

    def test_file_is_still_available_for_internal_estimates(self):
        """Файл НЕ отменён и НЕ удалён: решение владельца оставило его для внутренних прикидок,
        и прибор расхождения стои́т ровно на нём. Пропади он — прибор ослепнет молча."""
        total, day, why = door_price.file_total("XMAX 300", DS, DE, suggest._bike_key,
                                                {"days": 5, "bike": self.FLEET_NAMES[0]})
        self.assertEqual((total, day), (FILE_TOTAL, FILE_DAY), why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
