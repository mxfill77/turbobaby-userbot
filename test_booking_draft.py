# -*- coding: utf-8 -*-
"""
test_booking_draft.py — мок-тесты O3 куска 1 «Кнопка Бронь». БЕЗ реального claude/Bridge/
Telegram: экстрактор (call_llm), список парка (allowlist) и котировка (quote_fn) инъектируются.
Боевой контур не трогаем. Сценарии а–е из задания.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_booking_draft -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import json
import unittest
from unittest import mock

import booking_draft


ALLOW = ["NMAX 155", "XMAX 300", "ADV 350", "PCX 150", "ADV 160", "FORZA 300"]


def _llm(payload):
    """Фабрика call_llm: возвращает JSON-строку экстракции (как отдал бы claude)."""
    def _call(_transcript):
        return json.dumps(payload, ensure_ascii=False)
    return _call


def _quote_ok(day_price=449):
    return mock.Mock(return_value={"status": "ok", "quote": {"day_price": day_price,
                                                             "deposit": 3000, "available": True,
                                                             "days": 5}})


class TestBookingCard(unittest.TestCase):

    # (а) полный диалог → карточка со всеми полями A–V, цена от Bridge (пометка)
    def test_a_full_card_price_from_bridge(self):
        ex = {"model": "NMAX 155", "name": "Иван", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15 10:00", "price_day": "449", "deposit": "3000",
              "helmets": "2", "contact": "@ivan", "note": "доставка Патонг"}
        qf = _quote_ok(449)
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW, quote_fn=qf)
        for tag in ("A=Бронь", "B=OFF", "C=NMAX 155", "D=Иван", "E=10.07.2026",
                    "F=15.07.2026 , 10:00", "S=3000", "T=2", "U=@ivan", "V=доставка Патонг"):
            self.assertIn(tag, card, f"нет колонки: {tag}")
        self.assertIn("цена Bridge", card)          # пометка источника цены
        self.assertIn("449", card)
        self.assertTrue(qf.called)                   # Bridge-котировка вызвана
        self.assertIn("Авто-запись будет в куске 2", card)
        self.assertNotIn("⚠️", card)                 # полный диалог — без пропусков

    # (б) модель не из парка → ⚠️ + ближайшие похожие
    def test_b_model_not_in_park(self):
        ex = {"model": "PCX 160", "name": "Оля", "date_from": "2026-08-01",
              "date_to_datetime": "2026-08-06", "price_day": None, "deposit": "паспорт",
              "helmets": "1", "contact": "@olya", "note": "самовывоз Раваи"}
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW,
                                               quote_fn=_quote_ok())
        self.assertIn("⚠️", card)
        self.assertIn("не из парка", card)
        self.assertIn("ближайшие", card)
        self.assertIn("PCX 150", card)               # похожая модель из парка предложена

    # (в) HONDA CLICK 125 → жёсткий блок «не сдаём», quote НЕ зовётся
    def test_c_click125_hard_block(self):
        ex = {"model": "Honda Click 125", "name": "Петя", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-20", "price_day": "250", "deposit": "3000",
              "helmets": "1", "contact": "@petya", "note": ""}
        qf = _quote_ok()
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW, quote_fn=qf)
        self.assertIn("ОТКЛОНЕНА", card)
        self.assertIn("НЕ сдаём", card)
        self.assertFalse(qf.called)                  # цену не запрашиваем для заблокированной модели
        self.assertNotIn("A=Бронь", card)            # не показываем как валидную бронь

    # (г) «залог 7000 и паспорт» → ⚠️ конфликт (ИЛИ, не оба)
    def test_d_deposit_conflict(self):
        ex = {"model": "NMAX 155", "name": "Аня", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-16", "price_day": "449", "deposit": "7000 и паспорт",
              "helmets": "2", "contact": "@anya", "note": "Патонг"}
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW,
                                               quote_fn=_quote_ok(449))
        self.assertIn("⚠️", card)
        self.assertIn("залог", card.lower())
        self.assertIn("ИЛИ-ИЛИ", card)

    # (д) дат нет → E/F = «—», quote_price НЕ зовётся
    def test_e_no_dates_no_quote(self):
        ex = {"model": "NMAX 155", "name": "Макс", "date_from": None,
              "date_to_datetime": None, "price_day": "449", "deposit": "паспорт",
              "helmets": "1", "contact": "@max", "note": "Патонг"}
        qf = _quote_ok()
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW, quote_fn=qf)
        self.assertIn("E=—", card)
        self.assertIn("F=—", card)
        self.assertFalse(qf.called)                  # без дат Bridge не трогаем

    # доп: конфликт озвученной цены с Bridge → показываем ОБЕ с ⚠️
    def test_price_conflict_shows_both(self):
        ex = {"model": "NMAX 155", "name": "Ким", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15", "price_day": "400", "deposit": "3000",
              "helmets": "1", "contact": "@kim", "note": "Патонг"}
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW,
                                               quote_fn=_quote_ok(449))
        self.assertIn("Bridge 449", card)
        self.assertIn("в диалоге 400", card)
        self.assertIn("расходится", card)

    def test_deposit_passport_only_ok(self):
        kind, shown = booking_draft.classify_deposit("паспорт")
        self.assertEqual(kind, "passport")
        kind, _ = booking_draft.classify_deposit("5000")
        self.assertEqual(kind, "money")

    # (1.1) сценарий экзамена: модель распознана неточно, но в черновике треда — «NMAX 155»;
    # контакт @cryptopeppa в метаданных; клиент не назвался, но есть профиль; Bridge даёт цену.
    def test_exam_scenario_meta_and_thread_model(self):
        transcript = "[клиент]: привет, хочу nmax на 10.07-15.07"
        ex = {"model": "Yamaha Nmax", "name": None, "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15", "price_day": None, "deposit": "паспорт",
              "helmets": "1", "contact": None, "note": "Патонг"}
        meta = {"client_ref": "@cryptopeppa", "client_name": "Пётр"}
        qf = _quote_ok(337)
        card = booking_draft.make_booking_card(transcript, call_llm=_llm(ex), allowlist=ALLOW,
                                               quote_fn=qf, meta=meta)
        self.assertIn("C=NMAX 155 (из черновика)", card)     # подхват нормализованной модели треда
        self.assertIn("цена Bridge", card)                   # Bridge при валидной модели+датах
        self.assertIn("337", card)
        self.assertTrue(qf.called)
        self.assertIn("U=@cryptopeppa", card)                # контакт из метаданных
        self.assertIn("D=Пётр (из профиля — уточни)", card)  # имя из профиля с пометкой

    def test_u_falls_back_to_client_ref_id(self):
        ex = {"model": "NMAX 155", "name": "Лена", "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15", "price_day": "449", "deposit": "паспорт",
              "helmets": "1", "contact": None, "note": "Патонг"}
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW,
                                               quote_fn=_quote_ok(449), meta={"client_ref": "id777"})
        self.assertIn("U=id777", card)

    def test_d_missing_when_no_name_no_profile(self):
        ex = {"model": "NMAX 155", "name": None, "date_from": "2026-07-10",
              "date_to_datetime": "2026-07-15", "price_day": "449", "deposit": "паспорт",
              "helmets": "1", "contact": "@x", "note": "Патонг"}
        card = booking_draft.make_booking_card("dlg", call_llm=_llm(ex), allowlist=ALLOW,
                                               quote_fn=_quote_ok(449), meta={"client_ref": "@x"})
        self.assertIn("D=—", card)
        self.assertIn("нет имени (D)", card)


class TestButtonsIntact(unittest.TestCase):
    """(е-часть) существующие кнопки целы + новая «📋 Бронь» на исходной карточке."""

    def test_initial_kb_has_booking_and_existing(self):
        import moderation_bot
        kb = moderation_bot._kb(moderation_bot._kb_initial, 7)
        flat = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
        self.assertTrue(any("Да" in t and c == "m:7:yes" for t, c in flat))
        self.assertTrue(any("Отклонить" in t and c == "m:7:no" for t, c in flat))
        self.assertTrue(any("Бронь" in t and c == "m:7:booking" for t, c in flat))

    def test_confirm_kb_unchanged(self):
        import moderation_bot
        kb = moderation_bot._kb(moderation_bot._kb_confirm, 7)
        flat = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
        self.assertTrue(any(c == "m:7:send" for _, c in flat))       # Отправить
        self.assertTrue(any(c == "m:7:remember" for _, c in flat))   # Запомнить
        self.assertTrue(any(c == "m:7:more" for _, c in flat))       # Ещё правка


if __name__ == "__main__":
    unittest.main()
