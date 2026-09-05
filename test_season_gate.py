# -*- coding: utf-8 -*-
"""
test_season_gate.py — замки ТРЕТЬЕГО ИСХОДА на границе сезонов: «не считаю, зову человека».

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА, названные заданием (без них правка не годится):
  (а) период ВНУТРИ одного сезона считается КАК ПРЕЖДЕ и карточки не рождает — TestInsideSeason;
  (б) период ЧЕРЕЗ границу даёт третий исход и карточку владельцу        — TestCrossesBoundary;
  (в) отправитель НЕДОСТУПЕН → третий исход НЕ превращается в число       — TestSenderDown.

ПРОВЕРКА ПОВТОРЯЕТ ЖИВОЙ ФОРМАТ ЗАПУСКА, а не идеализированный: hints собирает НАСТОЯЩИЙ
`suggest.extract_booking_hints` по транскрипту с репликой клиента, записку — НАСТОЯЩИЙ
`suggest.build_pricing_note`, периоды — БОЕВОЙ `price_source.json` из корня репо (сочинённых
от руки сезонных таблиц здесь нет: подогнанная под красивое число фикстура и есть тот голден,
который врёт). Отправка владельцу меряется ПОДМЕНЁННЫМ отправителем — боевой `dispatch_notify`
в этих тестах не поднимается ни разу (ленивый импорт `season_gate._default_sender`), клиенту
не уходит ни строки.

`PRICE_GATE_TTL_MIN=0` в setUp — объявленный откат сторожа свежести, а не обход: без него
котировка внутри сезона пошла бы к живому мосту за девятью пробами ручек.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_season_gate -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import datetime
import os
import re
import unittest

import moderation_card
import price_source
import season_gate
import suggest

TODAY = datetime.date(2026, 9, 1)

# Оба среза берём из БОЕВОГО price_source.json: P1 «ИЮНЬ-СЕНТЯБРЬ» 06-01…09-30, P2 «ОКТЯБРЬ»
# 10-01…10-31. Внутри сезона — 10…20 сентября; через границу — 25 сентября…5 октября.
IN_START, IN_END = "2026-09-10", "2026-09-20"
CROSS_START, CROSS_END = "2026-09-25", "2026-10-05"

TR_IN = "[клиент]: Здравствуйте! Нужен NMAX с 10 по 20 сентября, сколько будет стоить?"
TR_CROSS = "[клиент]: Здравствуйте! Нужен NMAX с 25 сентября по 5 октября, сколько будет стоить?"


class Boom(Exception):
    """Отправитель лёг. Ровно то, что обязано НЕ менять исход."""


class Base(unittest.TestCase):
    # Тарифы фейкового Календаря — форма ответа скопирована с фикстуры боевых тестов
    # (test_suggest.TestPricingV2._getter), сочинённой ленты здесь нет.
    TAR = {"NMAX 155": (450, 2800, 9000, 3000, False, 12000)}
    FLEET_NAMES = ["NMAX 155CC BLACK PHUKET 4255"]

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("PRICE_GATE_TTL_MIN", season_gate.OFF_ENV)}
        os.environ["PRICE_GATE_TTL_MIN"] = "0"      # объявленный откат сторожа свежести
        os.environ.pop(season_gate.OFF_ENV, None)
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        season_gate.reset()
        self.sent = []

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
        season_gate.reset()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _getter(self):
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
            d1, d7, d30, dep, ca, cp = self.TAR[key]
            total = {1: d1, 7: d7, 30: d30}.get(days, d1 * days)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                                         "deposit": dep, "available": True, "days": days,
                                         "cap_active": ca, "cap_price": cp,
                                         "text": f"{bike} {days}d {total}"}}
        return fake

    def _hints(self, transcript):
        return suggest.extract_booking_hints(transcript, today=TODAY)

    def _note(self, transcript):
        return suggest.build_pricing_note(self._hints(transcript), lang="ru",
                                          getter=self._getter(), today=TODAY)

    def _sender(self, boom=False):
        def send(text):
            if boom:
                raise Boom("отправитель лёг")
            self.sent.append(text)
        return send


# --------------------------- (а) внутри одного сезона -------------------------

class TestInsideSeason(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1. Период внутри сезона обязан считаться КАК ПРЕЖДЕ. Замок не на
    «примерно так же»: записка сравнивается ПОСИМВОЛЬНО с той же запиской при выключенной
    ветке (`SEASON_GATE_OFF=1`) — вклад третьего исхода здесь обязан быть РОВНО НУЛЕВЫМ."""

    def test_verdict_is_one_period(self):
        v, names, _d = season_gate.span(IN_START, IN_END)
        self.assertEqual(v, season_gate.SEASON_ONE)
        self.assertEqual(names[0], names[1])

    def test_note_identical_to_gate_off(self):
        with_gate = self._note(TR_IN)
        os.environ[season_gate.OFF_ENV] = "1"
        try:
            suggest._sheet_cache.update(key=None, ts=0.0, rows=None)
            without_gate = self._note(TR_IN)
        finally:
            os.environ.pop(season_gate.OFF_ENV, None)
        self.assertEqual(with_gate, without_gate)

    def test_price_still_named(self):
        note = self._note(TR_IN)
        self.assertNotIn("границу сезонов", note)
        self.assertNotIn("стык сезонов", note)
        # Цена названа: в записке есть числа, и они разрешены пост-чеку как пришедшие из Календаря.
        self.assertTrue(suggest._pc_wl_price_numbers(note), note[:200])

    def test_no_card_born(self):
        """Ни одной карточки: подменённый отправитель молчит, а боевой сюда не зовётся."""
        self.assertIsNone(season_gate.note_for(self._hints(TR_IN), sender=self._sender()))
        self.assertEqual(self.sent, [])


# --------------------------- (б) через границу сезонов ------------------------

class TestCrossesBoundary(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2. Период через границу даёт третий исход И карточку."""

    def test_verdict_is_crosses(self):
        v, names, detail = season_gate.span(CROSS_START, CROSS_END)
        self.assertEqual(v, season_gate.SEASON_CROSSES)
        self.assertNotEqual(names[0], names[1])
        self.assertIn("→", detail)

    def test_client_gets_answer_without_any_number(self):
        note = self._note(TR_CROSS)
        self.assertIn("границу сезонов", note)
        # 1) ЧИСЛА НЕТ ВООБЩЕ: белый список пост-чека пуст, значит ЛЮБАЯ цифра в ответе LLM
        #    будет заклеймлена. Это и есть «без выдуманного числа», проверенное живым замком.
        self.assertEqual(suggest._pc_wl_price_numbers(season_gate.client_note("ru")), set())
        self.assertEqual(suggest.computed_price_figures(note), set())
        # 2) МОЛЧАНИЕ ЗАПРЕЩЕНО: записка требует произнести ответ...
        self.assertIn("МОЛЧАТЬ ТОЖЕ НЕЛЬЗЯ", note)
        # 3) ...и назвать человека, а не среднее по периоду.
        self.assertIn("считает человек", note)
        self.assertIn("СРЕДНИМ по периоду", note)
        self.assertNotIn("[QUOTE]", note)

    def test_owner_card_carries_dates_model_reason(self):
        h = self._hints(TR_CROSS)
        out = season_gate.note_for(h, sender=self._sender())
        self.assertIsNotNone(out)
        self.assertEqual(len(self.sent), 1)
        card = self.sent[0]
        self.assertIn(season_gate.CARD_HEAD, card)
        self.assertIn(CROSS_START, card)                      # даты
        self.assertIn(CROSS_END, card)
        self.assertIn("NMAX", card.upper())                   # модель
        self.assertIn("по дате НАЧАЛА", card)                 # причина
        self.assertNotIn("Отправить как есть", card)          # кнопок нет: сообщает, а не спрашивает

    def test_one_card_per_case_not_per_message(self):
        """Клиент пишет трижды — карточка ОДНА. Иначе владелец получает поток вместо сигнала."""
        h = self._hints(TR_CROSS)
        for _ in range(3):
            self.assertIsNotNone(season_gate.note_for(h, sender=self._sender()))
        self.assertEqual(len(self.sent), 1)

    def test_english_note_is_also_numberless(self):
        en = season_gate.client_note("en")
        self.assertEqual(suggest._pc_wl_price_numbers(en), set())
        self.assertIn("STAYING SILENT IS ALSO FORBIDDEN", en)


# --------------------------- (в) отправитель недоступен -----------------------

class TestSenderDown(Base):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3. Отправитель лёг — исход прежний: ответ БЕЗ числа.

    Замок стоит именно здесь, потому что соблазн обратный: «человека позвать не смогли, значит
    ответим сами». Такой ветки нет ни одной, и тест это доказывает не чтением кода, а исходом."""

    def test_note_survives_dead_sender(self):
        h = self._hints(TR_CROSS)
        out = season_gate.note_for(h, sender=self._sender(boom=True))
        self.assertIsNotNone(out)
        self.assertIn("границу сезонов", out)
        self.assertEqual(suggest._pc_wl_price_numbers(out), set())
        self.assertEqual(self.sent, [])                       # карточка не ушла — и это видно

    def test_raise_card_never_raises(self):
        self.assertFalse(season_gate.raise_card(self._hints(TR_CROSS), "P1 → P2",
                                                sender=self._sender(boom=True)))

    def test_no_digits_leak_into_client_text(self):
        """Ни одной цифры в тексте клиенту — иначе она стала бы разрешённой к произнесению ценой."""
        self.assertIsNone(re.search(r"\d", season_gate.client_note("ru")))
        self.assertIsNone(re.search(r"\d", season_gate.client_note("en")))


# --------------------------- границы решения ----------------------------------

class TestBoundaries(Base):
    """Чего ветка НЕ делает — тоже замок, иначе тихо расползётся."""

    def test_unknown_does_not_raise_card(self):
        """Таблицы периодов нет → путь ПРЕЖНИЙ и карточки нет: мёртвый файл поднял бы её на
        КАЖДЫЙ диалог, и один честный сигнал утонул бы в потоке."""
        h = self._hints(TR_CROSS)
        self.assertEqual(season_gate.span(CROSS_START, CROSS_END, doc=None)[0],
                         season_gate.SEASON_CROSSES)          # живой файл на месте
        v, names, _d = season_gate.span("не дата", CROSS_END, doc={"season": {"periods": []}})
        self.assertEqual(v, season_gate.SEASON_UNKNOWN)
        self.assertIsNone(names)
        self.assertIsNone(season_gate.note_for(h, doc={"season": {"periods": []}},
                                               sender=self._sender()))
        self.assertEqual(self.sent, [])

    def test_declared_rollback_kills_branch(self):
        self.assertIsNone(season_gate.note_for(self._hints(TR_CROSS), sender=self._sender(),
                                               env={season_gate.OFF_ENV: "1"}))
        self.assertEqual(self.sent, [])

    def test_guard_has_one_implementation(self):
        """Карточка модерации и путь ответа судят границу ОДНИМ кодом. Разъедутся — скажут
        владельцу и клиенту разное об одних и тех же датах."""
        self.assertIs(moderation_card.season_span.__globals__["season_gate"], season_gate)
        for a, b in ((IN_START, IN_END), (CROSS_START, CROSS_END), ("", "")):
            self.assertEqual(moderation_card.season_span(a, b), season_gate.span(a, b))
        self.assertEqual(moderation_card.SEASON_CROSSES, season_gate.SEASON_CROSSES)

    def test_module_does_not_import_suggest(self):
        """Петля импорта: `moderation_card` тянет `suggest`, `suggest` тянет этот модуль.
        Появится здесь `import suggest` — петля вернётся, и замок обязан покраснеть."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "season_gate.py"),
                  encoding="utf-8") as f:
            body = f.read()
        self.assertIsNone(re.search(r"^\s*import suggest", body, re.M))
        self.assertIsNone(re.search(r"^\s*from suggest", body, re.M))

    def test_live_periods_file_is_the_source(self):
        """Границы берутся из ФАЙЛА владельца, а не из копии в коде: их правка обязана ехать
        в вердикт сама. Своих дат-констант в модуле нет ни одной."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "season_gate.py"),
                  encoding="utf-8") as f:
            body = f.read()
        self.assertIsNone(re.search(r'"\d\d-\d\d"', body))
        self.assertIsNotNone(price_source.load())


if __name__ == "__main__":
    unittest.main(verbosity=2)
