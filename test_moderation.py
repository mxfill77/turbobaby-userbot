# -*- coding: utf-8 -*-
"""
test_moderation.py — мок-тесты задачи-2 (бот-модератор). БЕЗ реального Telegram и БЕЗ
реального Anthropic. Отправки клиенту нигде не происходит (проверяем инварианты).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_moderation -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import json
import asyncio
import datetime
import tempfile
import unittest

import suggest
import moderation_ipc
import moderation_core


async def _nosleep(_):
    return None


class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self):
        self.sent = []

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text, reply_to=None):
        self.sent.append((chat, text))


# JSON-мок LLM по интенту
def _llm(intent, final="ИТОГ", answer="ОТВЕТ"):
    def call(_system, _user):
        return json.dumps({"intent": intent, "final_text": final, "answer": answer}, ensure_ascii=False)
    return call


class TestIPC(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()

    def tearDown(self):
        moderation_ipc.DB_PATH = self._old
        self._tmp.cleanup()

    def test_full_lifecycle(self):
        did = moderation_ipc.enqueue_draft({
            "client_id": 999, "client_ref": "@c", "lang": "ru",
            "incoming": "цена?", "draft": "черновик", "first_contact": True})
        self.assertEqual([r["id"] for r in moderation_ipc.fetch_new()], [did])
        moderation_ipc.mark_posted(did, card_msg_id=5001)
        self.assertEqual(moderation_ipc.draft_by_card(5001)["id"], did)
        self.assertEqual(moderation_ipc.fetch_new(), [])          # уже posted
        moderation_ipc.set_decision(did, "ready", final_text="ОК", decided_by="@danya")
        ready = moderation_ipc.fetch_ready()
        self.assertEqual(ready[0]["final_text"], "ОК")
        moderation_ipc.mark(did, "sent")
        self.assertEqual(moderation_ipc.fetch_ready(), [])

    def test_heartbeat_liveness(self):
        now = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=datetime.timezone.utc)
        moderation_ipc.heartbeat(now.isoformat())
        self.assertTrue(moderation_ipc.is_bot_alive(now=lambda: now, threshold=15))
        later = now + datetime.timedelta(seconds=60)
        self.assertFalse(moderation_ipc.is_bot_alive(now=lambda: later, threshold=15))

    def test_no_heartbeat_is_dead(self):
        self.assertFalse(moderation_ipc.is_bot_alive())


class TestInterpret(unittest.TestCase):
    def setUp(self):
        self._wl = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._wl

    def test_fast_approve_reject(self):
        self.assertEqual(moderation_core.interpret("D", "+", "FAQ")["intent"], "approve")
        self.assertEqual(moderation_core.interpret("D", "нет", "FAQ")["intent"], "reject")

    def test_cosmetic_edits_and_confirms(self):
        # КОСМЕТИКА: правит форму существующего черновика → need_confirm
        r = moderation_core.interpret("D", "сделай короче", "FAQ", call_llm=_llm("cosmetic", final="Кратко"))
        self.assertEqual(r["intent"], "cosmetic")
        self.assertTrue(r["need_confirm"])
        self.assertEqual(r["final_text"], "Кратко")

    def test_strategy_marks_regenerate(self):
        # СТРАТЕГИЯ: final_text пуст (перегенерация с нуля отдельно), need_confirm
        r = moderation_core.interpret("D", "дожимай на ADV", "FAQ", call_llm=_llm("strategy", final="неважно"))
        self.assertEqual(r["intent"], "strategy")
        self.assertTrue(r["need_confirm"])
        self.assertEqual(r["final_text"], "")     # НЕ патч старого текста

    def test_dictation_confirms(self):
        # ДИКТОВКА: текст менеджера почти как есть, НО повторное подтверждение обязательно
        r = moderation_core.interpret("D", "ответь дословно: приедем в 5", "FAQ",
                                      call_llm=_llm("dictation", final="Приедем в 5"))
        self.assertEqual(r["intent"], "dictation")
        self.assertTrue(r["need_confirm"])
        self.assertEqual(r["final_text"], "Приедем в 5")

    def test_question_answers(self):
        r = moderation_core.interpret("D", "что по ценам XMAX?", "FAQ", call_llm=_llm("question", answer="939฿/день"))
        self.assertEqual(r["intent"], "question")
        self.assertEqual(r["answer"], "939฿/день")

    def test_ambiguous_is_strategy(self):
        # двусмысленно/битый JSON → СТРАТЕГИЯ (глубже безопаснее), с подтверждением
        r = moderation_core.interpret("D", "мутный текст", "FAQ", call_llm=lambda s, u: "не json")
        self.assertEqual(r["intent"], "strategy")
        self.assertTrue(r["need_confirm"])

    def test_classifier_intent_not_length(self):
        # длинная косметика остаётся косметикой; короткая стратегия — стратегией (решает интент, не длина)
        long_cos = moderation_core.interpret("D", "пожалуйста " * 20 + "убери восклицательные знаки",
                                             "FAQ", call_llm=_llm("cosmetic", final="без !"))
        self.assertEqual(long_cos["intent"], "cosmetic")
        short_str = moderation_core.interpret("D", "жёстче", "FAQ", call_llm=_llm("strategy"))
        self.assertEqual(short_str["intent"], "strategy")


class TestDecisions(unittest.TestCase):
    def setUp(self):
        self._wl = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._wl

    DRAFT = {"id": 1, "draft": "черновик", "final_text": "кандидат"}

    def test_callback_yes_live_ready(self):
        d = moderation_core.process_callback(self.DRAFT, "yes", "danya", test_mode=False)
        self.assertEqual(d["decision"], "ready")
        self.assertEqual(d["final_text"], "черновик")

    def test_callback_yes_testmode_held(self):
        d = moderation_core.process_callback(self.DRAFT, "yes", "danya", test_mode=True)
        self.assertEqual(d["decision"], "test_held")     # DOUBLE-LOCK слой 1
        self.assertIn("не отправлено", d["card"].lower())

    def test_callback_send_uses_candidate(self):
        d = moderation_core.process_callback(self.DRAFT, "send", "danya", test_mode=False, candidate="кандидат")
        self.assertEqual(d["decision"], "ready")
        self.assertEqual(d["final_text"], "кандидат")

    def test_callback_no_reject(self):
        self.assertEqual(moderation_core.process_callback(self.DRAFT, "no", "d", False)["decision"], "rejected")

    def test_callback_more(self):
        self.assertEqual(moderation_core.process_callback(self.DRAFT, "more", "d", False)["decision"], "await_more")

    def test_whitelist_denies_callback(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        d = moderation_core.process_callback(self.DRAFT, "yes", "stranger", False)
        self.assertEqual(d["decision"], "denied")
        self.assertIn("⛔", d["card"])

    STRAT_DRAFT = {"id": 1, "draft": "черновик", "final_text": "кандидат",
                   "transcript": "[клиент]: NMAX?", "lang": "ru",
                   "first_contact": False, "pricing_note": "ЦЕНА из Календаря: 500฿/день"}

    def test_reply_cosmetic_confirm(self):
        d = moderation_core.process_reply(self.DRAFT, "короче", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("cosmetic", final="Кратко"))
        self.assertEqual(d["decision"], "confirm")       # обязательное подтверждение
        self.assertEqual(d["final_text"], "Кратко")
        self.assertEqual(d["level"], "cosmetic")

    def test_reply_dictation_confirm(self):
        # ДИКТОВКА больше НЕ авто-шлёт (раньше replacement уходил без подтверждения) → confirm
        d = moderation_core.process_reply(self.DRAFT, "ответь дословно: ок", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("dictation", final="Ок"))
        self.assertEqual(d["decision"], "confirm")
        self.assertEqual(d["final_text"], "Ок")

    def test_reply_strategy_regenerates_with_directive(self):
        # СТРАТЕГИЯ: перегенерация С НУЛЯ — regen получает ДИРЕКТИВУ и исходный контекст, НЕ патчит старое
        seen = {}
        def fake_regen(draft, faq, directive):
            seen["directive"] = directive
            seen["transcript"] = draft.get("transcript")
            return "НОВЫЙ ЧЕРНОВИК по стратегии"
        d = moderation_core.process_reply(self.STRAT_DRAFT, "дожимай на ADV", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("strategy"), regen=fake_regen)
        self.assertEqual(d["decision"], "confirm")
        self.assertEqual(d["final_text"], "НОВЫЙ ЧЕРНОВИК по стратегии")   # не старый черновик
        self.assertEqual(seen["directive"], "дожимай на ADV")             # реплика = директива
        self.assertEqual(seen["transcript"], "[клиент]: NMAX?")           # исходный контекст

    def test_reply_strategy_accumulates_window_directives(self):
        # client_windows (#365 шаг 3): директивы окна КОПЯТСЯ — вторая перегенерация видит ОБЕ.
        # Кейс живого провала: «новые по умолчанию, года только по запросу» терялась на СЛЕДУЮЩЕЙ
        # перегенерации (regen получал лишь последнюю реплику).
        seen = []
        def fake_regen(draft, faq, directive):
            seen.append(directive)
            return "R"
        d1 = "новые по умолчанию, года только по запросу"
        d2 = "жёстче про депозит"
        draft = dict(self.STRAT_DRAFT)   # directive пуст → первая правка окна
        r1 = moderation_core.process_reply(draft, d1, "d", "FAQ", test_mode=False,
                                           call_llm=_llm("strategy"), regen=fake_regen)
        # IPC-персист: кумулятив из решения ложится обратно в запись окна (как set_candidate в _apply)
        draft["directive"] = r1["directive"]
        r2 = moderation_core.process_reply(draft, d2, "d", "FAQ", test_mode=False,
                                           call_llm=_llm("strategy"), regen=fake_regen)
        # первая перегенерация — только d1; вторая — ОБЕ директивы окна подмешаны в промпт
        self.assertIn(d1, seen[0]); self.assertNotIn(d2, seen[0])
        self.assertIn(d1, seen[1]); self.assertIn(d2, seen[1])
        # решение хранит кумулятив (уйдёт в IPC → следующий цикл окна увидит обе)
        self.assertIn(d1, r2["directive"]); self.assertIn(d2, r2["directive"])

    def test_reconfirm_mandatory_all_levels(self):
        for intent in ("cosmetic", "dictation"):
            d = moderation_core.process_reply(self.DRAFT, "x", "d", "FAQ", test_mode=False,
                                              call_llm=_llm(intent, final="T"))
            self.assertEqual(d["decision"], "confirm", intent)   # ни один уровень не авто-шлёт
        d = moderation_core.process_reply(self.STRAT_DRAFT, "жёстче", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("strategy"), regen=lambda *a: "R")
        self.assertEqual(d["decision"], "confirm")

    def test_reply_edit_never_autosends_in_testmode(self):
        # даже в TEST_MODE правка не «test_held» на этапе reply — сперва повторное подтверждение
        d = moderation_core.process_reply(self.DRAFT, "ответь дословно: ок", "d", "FAQ", test_mode=True,
                                          call_llm=_llm("dictation", final="Ок"))
        self.assertEqual(d["decision"], "confirm")
        self.assertNotIn(d["decision"], ("ready", "test_held", "sent"))

    def test_reply_approve_still_sends(self):
        # быстрый путь «+» реплики → отправка как есть (без доп-подтверждения)
        d = moderation_core.process_reply(self.DRAFT, "+", "d", "FAQ", test_mode=False)
        self.assertEqual(d["decision"], "ready")
        self.assertEqual(d["final_text"], "черновик")

    def test_reply_question_answer(self):
        d = moderation_core.process_reply(self.DRAFT, "что по ценам?", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("question", answer="A"))
        self.assertEqual(d["decision"], "answer")
        self.assertEqual(d["answer"], "A")

    def test_reply_whitelist_denies(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        d = moderation_core.process_reply(self.DRAFT, "+", "stranger", "FAQ", test_mode=False)
        self.assertEqual(d["decision"], "denied")

    def test_confirm_carries_directive(self):
        # confirm-карточка несёт формулировку модератора → её сохранит IPC для «Запомнить»
        d = moderation_core.process_reply(self.DRAFT, "будь мягче", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("cosmetic", final="Мягко"))
        self.assertEqual(d["decision"], "confirm")
        self.assertEqual(d["directive"], "будь мягче")


class TestEndToEndMax2stix(unittest.TestCase):
    """СКВОЗНЫЕ голдены кейса @max2stix (#365 родитель4, шаг 5/6): весь strategy-путь
    process_reply → regen → suggest.regenerate_draft(+guard_availability), а НЕ отдельные функции.
    Правило-класс CLAUDE.md: дословная живая фраза клиента, не идеализация. Проверяем ПОВЕДЕНИЕ
    сборки при «плохом» LLM (теряет цену): цена/депозит ЗА КАЖДЫЙ юнит доходят клиенту КОДОМ; в
    финале нет «вернусь» (директива «сразу выдавай цены»), нет ГОДОВ поколений (только метка
    New Gen), нет утверждений о дефиците. Клиенту НИЧЕГО не шлётся (мок-LLM, гард read-only)."""

    # Живой парк @max2stix: 2 старых XMAX (дешевле, деп 5000) + 1 новый 2023+ (дороже, деп 7000).
    FLEET_NAMES = ["XMAX 300CC GREY PHUKET 4246", "XMAX 300CC BLUE PHUKET 4247",
                   "XMAX 300CC NEW 2023 PHUKET 7701"]
    OLD = (790, 4700, 23700, 5000, True, 8900)     # старое поколение: тариф, депозит, кап
    NEW = (939, 5600, 28170, 7000, True, 9900)     # новое поколение (2023+): дороже, деп 7000

    def setUp(self):
        self._wl = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = set()
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._wl
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _getter(self):
        """Живой Bridge (мок): цена ПО ЮНИТУ (новый 2023+ дороже). text — ЧИСТАЯ ценовая строка БЕЗ
        сырого имени юнита/года: поколение несёт МЕТКА (New Gen), а не год в клиентском теле."""
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            new = "2023" in bike or "NEW" in bike.upper()
            d1, d7, d30, dep, ca, cp = self.NEW if new else self.OLD
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{days} дн — {total} ฿"}}
        return fake

    _TODAY = datetime.date(2026, 7, 11)

    def _pricing_note(self, phrase):
        hints = suggest.extract_booking_hints(f"[клиент]: {phrase}", today=self._TODAY)
        self.assertEqual(hints["units_count"], 2, phrase)     # детект «пара» юнитов одной модели
        return suggest.build_pricing_note(hints, lang="ru", getter=self._getter(), today=self._TODAY)

    def _regen_factory(self, note, seen_systems, llm_body):
        """Продакшн-сборка strategy-перегенерации как единый regen(draft, faq, directive):
        suggest.regenerate_draft (цена Bridge КОДОМ, пост-чек) → guard_availability (read-only гард
        дефицита/наличия). llm_body(system, user) — «плохой» LLM, теряющий цену (её донесёт КОД)."""
        def _llm_capture(system, user):
            seen_systems.append(system)
            return llm_body(system, user)

        def regen(draft, faq, directive):
            out = suggest.regenerate_draft(draft.get("transcript"), draft.get("lang", "ru"), faq,
                                           draft.get("first_contact", False), note, directive,
                                           call_llm=_llm_capture)
            g = suggest.guard_availability(out, avail=self._getter()({"action": "quote_price",
                                           "bike": "XMAX 300", "date_start": "2026-07-16",
                                           "date_end": "2026-07-24"}), model="XMAX 300")
            self.assertIn(g["source"], ("clean", "draft"), g)   # чистый черновик — гард не сфолбэчил
            return g["text"]
        return regen

    def _assert_compliant(self, out, ctx=""):
        low = suggest.client_facing_text(out).lower()
        # (1) цена И депозит ЗА КАЖДЫЙ юнит доехали клиенту КОДОМ (LLM их потерял). БЕЗ запроса про
        # поколение — ТОЛЬКО актуальное (New Gen 939/7000); прежнее поколение (790/5000) не предлагаем.
        for num in ("939", "7000"):
            self.assertIn(num, out, f"{ctx}: нет цены/депозита {num} за каждый юнит:\n{out}")
        for old_num in ("790", "5000"):
            self.assertNotIn(old_num, out, f"{ctx}: прежнее поколение {old_num} утекло без запроса:\n{out}")
        self.assertIn("ЗА КАЖДЫЙ", out, f"{ctx}: пометка «за каждый» потеряна:\n{out}")
        self.assertIn("New Gen", out, f"{ctx}: поколение помечено не New Gen:\n{out}")
        # выдуманного общего итога за 2 шт. нет (код не суммирует — числа только из Bridge)
        for made_up in ("1580", "1878", "10000", "14000"):
            self.assertNotIn(made_up, out, f"{ctx}: выдуман итог {made_up}:\n{out}")
        # (2) без «вернусь» — директива «сразу выдавай цены»
        self.assertNotIn("вернус", low, f"{ctx}: обещание «вернусь» просочилось:\n{out}")
        # (3) без ГОДОВ поколений — поколение только меткой New Gen
        for yr in ("2020", "2021", "2022", "2023", "2024"):
            self.assertNotIn(yr, out, f"{ctx}: год поколения {yr} утёк клиенту:\n{out}")
        # (4) без утверждений о дефиците/срочности
        for scar in ("последн", "успевай", "разбирают", "не упуст", "только сегодня", "спеши",
                     "last one", "hurry"):
            self.assertNotIn(scar, low, f"{ctx}: дефицит/срочность просочились ({scar!r}):\n{out}")

    def test_pair_xmax_price_directive_end_to_end(self):
        # ГОЛДЕН (1): «пару скутеров xmax 16-24 июля» + директива «сразу выдавай цены» → черновик с
        # ценой ЗА КАЖДЫЙ, без «вернусь», без годов поколений, без дефицита. LLM теряет цену — её
        # доносит КОД. Дословная фраза клиента + парафразы RU (правило-класс CLAUDE.md).
        note = self._pricing_note("пару скутеров xmax 16-24 июля")
        seen = []
        # «плохой» LLM: следует директиве (сразу к ценам, без «вернусь»), но САМИ цифры теряет.
        drop = lambda s, u: "Отличный выбор! Пара XMAX 300 на 16–24 июля — вот цены за каждый скутер:"
        draft = {"id": 1, "transcript": "[клиент]: пару скутеров xmax 16-24 июля", "lang": "ru",
                 "first_contact": False, "directive": "", "pricing_note": note,
                 "draft": "старый черновик", "final_text": "старый черновик"}
        d = moderation_core.process_reply(draft, "сразу выдавай цены", "d", "FAQ", test_mode=False,
                                          call_llm=_llm("strategy"),
                                          regen=self._regen_factory(note, seen, drop))
        self.assertEqual(d["decision"], "confirm")
        self._assert_compliant(d["final_text"], "e2e (1)")
        # директива дошла до промпта, а инварианты (анти-«вернусь», дефицит, «за каждый») в системнике
        self.assertIn("сразу выдавай цены", seen[0])
        self.assertIn("вернусь с прайсом", seen[0])            # ANTI_LOOP: не обещай «вернусь»
        self.assertIn("дефицит", seen[0].lower())              # AVAILABILITY_INVARIANT_RULE
        self.assertIn("ЗА КАЖДЫЙ", seen[0])                    # инструкция N-юнитов в промпте

    def test_pair_xmax_two_directives_both_hold_end_to_end(self):
        # ГОЛДЕН (2): ДВЕ директивы окна подряд — держатся ОБЕ. Первая перегенерация видит d1, вторая —
        # d1 И d2 (кумулятив окна), финал по-прежнему compliant (цена за каждый / без «вернусь» /
        # без годов / без дефицита). Кейс: модератор уточняет стратегию в два приёма.
        note = self._pricing_note("пару скутеров xmax 16-24 июля")
        seen = []
        drop = lambda s, u: "Отличный выбор! Пара XMAX 300 на 16–24 июля — держите цены за каждый:"
        d1 = "сразу выдавай цены"
        d2 = "поколения помечай как New Gen, годы не пиши"
        draft = {"id": 1, "transcript": "[клиент]: пару скутеров xmax 16-24 июля", "lang": "ru",
                 "first_contact": False, "directive": "", "pricing_note": note,
                 "draft": "старый черновик", "final_text": "старый черновик"}
        r1 = moderation_core.process_reply(draft, d1, "d", "FAQ", test_mode=False,
                                           call_llm=_llm("strategy"),
                                           regen=self._regen_factory(note, seen, drop))
        draft["directive"] = r1["directive"]     # IPC-персист кумулятива обратно в окно (как _apply)
        r2 = moderation_core.process_reply(draft, d2, "d", "FAQ", test_mode=False,
                                           call_llm=_llm("strategy"),
                                           regen=self._regen_factory(note, seen, drop))
        # первая перегенерация — только d1; вторая — ОБЕ директивы окна в промпте
        self.assertIn(d1, seen[0]); self.assertNotIn(d2, seen[0])
        self.assertIn(d1, seen[1]); self.assertIn(d2, seen[1])
        # кумулятив ушёл в решение → следующий цикл окна увидит обе
        self.assertIn(d1, r2["directive"]); self.assertIn(d2, r2["directive"])
        # финал второй перегенерации по-прежнему compliant
        self._assert_compliant(r2["final_text"], "e2e (2)")


class TestRememberRule(unittest.TestCase):
    """Фаза 2: кнопка «📌 Запомнить как правило» — approver-гейт, дистилляция, append, дедуп, fail-safe."""

    def setUp(self):
        self._wl = suggest.APPROVER_USERNAMES
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.APPROVER_USERNAMES = self._wl

    def _distill(self, text):
        return lambda directive, call_llm=None: text

    def test_distill_rule_uses_llm(self):
        r = moderation_core.distill_rule("дожимай на ADV", call_llm=lambda s, u: "  Дожимай на ADV350  ")
        self.assertEqual(r, "Дожимай на ADV350")     # LLM-выход схлопнут/обрезан

    def test_remember_approver_appends(self):
        got = {}
        def appender(rule, now=None):
            got["rule"] = rule
            return "added"
        d = moderation_core.remember_rule({"directive": "жёстче про депозит"}, "danya",
                                          distill=self._distill("Жёстче про депозит"), appender=appender)
        self.assertEqual(d["decision"], "remembered")
        self.assertEqual(got["rule"], "Жёстче про депозит")           # дистиллят дописан
        self.assertIn("Записано в правила", d["card"])

    def test_remember_nonapprover_denied(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        d = moderation_core.remember_rule({"directive": "x"}, "stranger",
                                          distill=self._distill("R"), appender=lambda *a, **k: "added")
        self.assertEqual(d["decision"], "denied")
        self.assertIn("⛔", d["card"])

    def test_remember_duplicate_not_appended(self):
        d = moderation_core.remember_rule({"directive": "x"}, "danya",
                                          distill=self._distill("R"), appender=lambda *a, **k: "duplicate")
        self.assertEqual(d["decision"], "duplicate")
        self.assertIn("уже есть", d["card"].lower())

    def test_remember_append_error_is_failsafe(self):
        def boom(rule, now=None):
            raise IOError("disk full")
        d = moderation_core.remember_rule({"directive": "x"}, "danya",
                                          distill=self._distill("R"), appender=boom)
        self.assertEqual(d["decision"], "not_saved")                  # правка НЕ заблокирована
        self.assertIn("разово", d["card"].lower())

    def test_remember_no_directive(self):
        d = moderation_core.remember_rule({"directive": ""}, "danya",
                                          distill=self._distill("R"), appender=lambda *a, **k: "added")
        self.assertEqual(d["decision"], "no_directive")

    def test_remembered_rule_flows_into_next_prompt(self):
        # end-to-end: реальный appender → playbook → подмешивание в НОВЫЙ черновик
        with tempfile.TemporaryDirectory() as dd:
            pf = os.path.join(dd, "playbook.md")
            with open(pf, "w", encoding="utf-8") as f:
                f.write("# PB\n\n## Выученные правила\n- старое правило\n")
            save = suggest.PLAYBOOK_FILE
            suggest.PLAYBOOK_FILE = pf
            try:
                d = moderation_core.remember_rule(
                    {"directive": "всегда предлагай доставку сразу"}, "danya",
                    distill=self._distill("Всегда предлагай доставку сразу"))    # реальный appender
                self.assertEqual(d["decision"], "remembered")
                sysp = suggest.make_system_prompt("FAQ", "ru", playbook=suggest.load_playbook())
                self.assertIn("Всегда предлагай доставку сразу", sysp)           # выученное правило в промпте
            finally:
                suggest.PLAYBOOK_FILE = save

    def test_confirm_card_has_remember_button(self):
        import moderation_bot
        kb = moderation_bot._kb(moderation_bot._kb_confirm, 7)
        flat = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
        self.assertTrue(any("Запомнить" in t and c == "m:7:remember" for t, c in flat))


class TestDegradationAndExecutor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (moderation_ipc.DB_PATH, suggest.MODERBOT_TOKEN, suggest.SUGGEST_MODE,
                      suggest.SUGGEST_TEST_MODE, suggest.pending, suggest.PAIRS_FILE)
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.reset_disabled()
        suggest.limiter = suggest.RateLimiter(6, 15)
        suggest.PAIRS_FILE = os.path.join(self._tmp.name, "pairs.jsonl")

    def tearDown(self):
        (moderation_ipc.DB_PATH, suggest.MODERBOT_TOKEN, suggest.SUGGEST_MODE,
         suggest.SUGGEST_TEST_MODE, suggest.pending, suggest.PAIRS_FILE) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def test_bot_mode_inactive_without_token(self):
        suggest.MODERBOT_TOKEN = ""          # нет токена → деградация
        self.assertFalse(suggest.bot_mode_active())

    def test_bot_mode_active_with_token_and_heartbeat(self):
        suggest.MODERBOT_TOKEN = "x"
        moderation_ipc.heartbeat()           # свежий → жив
        self.assertTrue(suggest.bot_mode_active())

    def test_bot_mode_inactive_when_heartbeat_stale(self):
        suggest.MODERBOT_TOKEN = "x"
        old = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        moderation_ipc.heartbeat(old.isoformat())   # протух → мёртв
        self.assertFalse(suggest.bot_mode_active())

    def test_executor_sends_ready_row(self):
        did = moderation_ipc.enqueue_draft({"client_id": 999, "client_ref": "@c", "lang": "ru",
                                            "incoming": "?", "draft": "D", "first_contact": False})
        moderation_ipc.set_decision(did, "ready", final_text="ОТВЕТ", decided_by="@d")
        sent = []
        async def fake_send(client, cid, text, sleep=None, jitter=None):
            sent.append((cid, text)); return True, None
        n = asyncio.run(suggest.poll_and_send(FakeClient(), sender=fake_send))
        self.assertEqual(n, 1)
        self.assertEqual(sent, [(999, "ОТВЕТ")])
        self.assertEqual(moderation_ipc.get(did)["status"], "sent")

    def test_executor_double_lock_in_testmode(self):
        # SAFETY: даже если 'ready' оказался в TEST_MODE — send_to_client не отправит.
        suggest.SUGGEST_TEST_MODE = True
        did = moderation_ipc.enqueue_draft({"client_id": 999, "client_ref": "@c", "lang": "ru",
                                            "incoming": "?", "draft": "D", "first_contact": False})
        moderation_ipc.set_decision(did, "ready", final_text="ОТВЕТ")
        c = FakeClient()
        n = asyncio.run(suggest.poll_and_send(c, sleep=_nosleep, jitter=lambda: 0))
        self.assertEqual(c.sent, [])                     # клиенту НИЧЕГО
        self.assertEqual(moderation_ipc.get(did)["status"], "failed")


class TestTrainerLeakGuard(unittest.TestCase):
    """Протечка 22.07 14:25: mod_chat оказался отравлен id группы ТРЕНАЖЁРА → боевые карточки
    клиентов (@extthiwxer, кнопки Да/Отклонить/Бронь) уехали в «Тренеровку». ГОЛДЕНЫ: (1) белый
    список назначения — карточка модерации НЕ может быть отправлена в trainer-группу; (2) STAFF-SKIP —
    черновик внутреннего аккаунта карточкой НЕ постится вовсе; (3) mod_chat не учится в группе
    тренажёра (в т.ч. ДО привязки — по title)."""

    TRAINER_CHAT = -5193185299    # реальный id «Тренеровки» из инцидента

    def setUp(self):
        import trainer
        import moderation_bot
        self.trainer = trainer
        self.mb = moderation_bot
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (moderation_ipc.DB_PATH, suggest.TEAM_REGISTRY, moderation_bot.MOD_GROUP_ID)
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        moderation_ipc.set_meta(trainer.K_CHAT, str(self.TRAINER_CHAT))   # тренажёр привязан
        # Реальные id инцидента: Earth=@extthiwxer 8562625260, Пым=@Pleummmm 659135499
        suggest.TEAM_REGISTRY = {"usernames": {"pleummmm", "extthiwxer"},
                                 "user_ids": {8562625260, 659135499}, "group_ids": set()}

    def tearDown(self):
        (moderation_ipc.DB_PATH, suggest.TEAM_REGISTRY, self.mb.MOD_GROUP_ID) = self._save
        self._tmp.cleanup()

    def test_target_chat_never_trainer_group(self):
        # mod_chat отравлен id тренажёра (как в инциденте) → _target_chat обязан дать None
        self.mb.MOD_GROUP_ID = None
        moderation_ipc.set_meta("mod_chat", str(self.TRAINER_CHAT))
        self.assertIsNone(self.mb._target_chat(), "карточки в тренажёр уходить НЕ должны")
        # здоровый mod_chat (реальная «Модерация ответов») → работает
        moderation_ipc.set_meta("mod_chat", "-5031790861")
        self.assertEqual(self.mb._target_chat(), -5031790861)

    def test_target_chat_env_override_also_guarded(self):
        # даже если MOD_GROUP_ID из env ошибочно указали на тренажёр → None (белый список жёсткий)
        self.mb.MOD_GROUP_ID = self.TRAINER_CHAT
        self.assertIsNone(self.mb._target_chat())

    def test_poll_new_staff_skip_no_card(self):
        # ГОЛДЕН STAFF-SKIP: черновик РЕАЛЬНОГО внутреннего (@extthiwxer id 8562625260) уже в
        # очереди (как в инциденте — попал до ужесточения реестра) → карточка НЕ постится ВОВСЕ,
        # черновик закрывается rejected; обычный клиент из той же очереди — постится штатно.
        self.mb.MOD_GROUP_ID = -5031790861
        did_staff = moderation_ipc.enqueue_draft(
            {"client_id": 8562625260, "client_ref": "@extthiwxer", "lang": "ru",
             "incoming": "заказ на доставку", "draft": "D1", "first_contact": False})
        did_client = moderation_ipc.enqueue_draft(
            {"client_id": 999, "client_ref": "@client1", "lang": "ru",
             "incoming": "аренда", "draft": "D2", "first_contact": False})
        posted = []

        class Bot:
            async def send_message(self, chat, text, **kw):
                posted.append((chat, text))

                class M:
                    message_id = 700 + len(posted)
                return M()

        class Ctx:
            bot = Bot()

        asyncio.run(self.mb.job_poll_new(Ctx()))
        self.assertEqual(len(posted), 1, "карточка только у ОБЫЧНОГО клиента")
        self.assertIn("@client1", posted[0][1])
        self.assertEqual(moderation_ipc.get(did_staff)["status"], "rejected")   # закрыт, не висит new
        self.assertIn("STAFF-SKIP", moderation_ipc.get(did_staff)["reason"] or "")
        self.assertEqual(moderation_ipc.get(did_client)["status"], "posted")

    def test_group_message_does_not_learn_mod_chat_in_trainer(self):
        # в группе тренажёра (по chat_id И по title до привязки) mod_chat НЕ учится
        moderation_ipc.set_meta("mod_chat", "")

        class Chat:
            def __init__(self, cid, title):
                self.id, self.title, self.type = cid, title, "group"

        class Msg:
            reply_to_message = None
            text = "просто сообщение"
            message_id = 1
            voice = None

        class Upd:
            def __init__(self, cid, title):
                self.effective_message = Msg()
                self.effective_chat = Chat(cid, title)
                self.effective_user = None

        class Ctx:
            class bot:
                @staticmethod
                async def send_message(*a, **k):
                    pass

        # привязанная группа тренажёра
        asyncio.run(self.mb.on_group_message(Upd(self.TRAINER_CHAT, "Тренеровка"), Ctx()))
        self.assertFalse(moderation_ipc.get_meta("mod_chat"), "mod_chat не должен выучиться")
        # ДО привязки: chat_id ещё не в meta, но title = «Тренеровка» → тоже не учим (корень протечки)
        moderation_ipc.set_meta(self.trainer.K_CHAT, "")
        asyncio.run(self.mb.on_group_message(Upd(-777555, "Тренеровка"), Ctx()))
        self.assertFalse(moderation_ipc.get_meta("mod_chat"))
        # обычная рабочая группа → учится штатно
        asyncio.run(self.mb.on_group_message(Upd(-5031790861, "Модерация ответов"), Ctx()))
        self.assertEqual(moderation_ipc.get_meta("mod_chat"), "-5031790861")


class TestRoutingIntoIPC(unittest.TestCase):
    """on_client_message: bot-режим → в IPC (не в группу); деградация → в группу."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (moderation_ipc.DB_PATH, suggest.SUGGEST_MODE, suggest.MOD_GROUP_ID,
                      suggest.pending, suggest.bot_mode_active)
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        suggest.SUGGEST_MODE = True
        suggest.reset_disabled()
        suggest.MOD_GROUP_ID = -100777
        suggest.pending = suggest.PendingStore(os.path.join(self._tmp.name, "p.jsonl"))

    def tearDown(self):
        (moderation_ipc.DB_PATH, suggest.SUGGEST_MODE, suggest.MOD_GROUP_ID,
         suggest.pending, suggest.bot_mode_active) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    class _Sender:
        id = 999
        username = "client1"

    def _client(self):
        return FakeClient()

    def _hist(self):
        class M:
            def __init__(s, sid, msg): s.sender_id = sid; s.message = msg; s.date = None
        c = FakeClient()
        c._hist = [M(999, "привет")]

        def iter_messages(entity, limit=50):
            async def gen():
                for m in c._hist:
                    yield m
            return gen()
        c.iter_messages = iter_messages
        return c

    def test_botmode_routes_to_ipc(self):
        suggest.bot_mode_active = lambda: True
        c = self._hist()
        r = asyncio.run(suggest.on_client_message(c, self._Sender(), 42,
                                                  call_llm=lambda s, u: "ЧЕРНОВИК", faq="FAQ"))
        self.assertTrue(str(r).startswith("ipc:"))
        self.assertEqual(c.sent, [])                     # в группу НЕ постим (это делает бот)
        self.assertEqual(len(moderation_ipc.fetch_new()), 1)

    def test_degraded_routes_to_group(self):
        suggest.bot_mode_active = lambda: False
        c = self._hist()
        r = asyncio.run(suggest.on_client_message(c, self._Sender(), 42,
                                                  call_llm=lambda s, u: "ЧЕРНОВИК", faq="FAQ"))
        self.assertFalse(str(r).startswith("ipc:"))
        self.assertEqual(len(c.sent), 1)                 # постим в группу модерации сами
        self.assertEqual(c.sent[0][0], suggest.MOD_GROUP_ID)
        self.assertEqual(moderation_ipc.fetch_new(), [])  # в IPC ничего


class TestLessonInterception(unittest.TestCase):
    """Родитель 292, шаг 1: перехват реплая-обучения на карточку черновика — триггер
    («правка:»/«урок:»/«не так:»), парс замечания + окна диалога, права INTAKE_APPROVERS."""

    DRAFT = {"id": 7, "draft": "черновик", "client_id": 555, "client_ref": "@petya"}

    def setUp(self):
        self._save = (suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES)
        # учителя: Филипп ×2 аккаунта, Даня, Даша
        suggest.INTAKE_APPROVERS = {"filipp", "filipp_alt", "danya", "dasha"}
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES = self._save

    # --- триггер (parse_lesson) ---
    def test_trigger_variants_parsed(self):
        for text, kind, remark in (
            ("правка: не пиши цену первой", "правка", "не пиши цену первой"),
            ("Урок: всегда уточняй даты", "урок", "всегда уточняй даты"),
            ("не так: тон слишком сухой", "не так", "тон слишком сухой"),
            ("  ПРАВКА:  лишний пробел  ", "правка", "лишний пробел"),
        ):
            p = moderation_core.parse_lesson(text)
            self.assertIsNotNone(p, text)
            self.assertEqual(p["kind"], kind, text)
            self.assertEqual(p["remark"], remark, text)

    def test_non_trigger_is_none(self):
        # обычные реплики (не уроки) не перехватываются
        for text in ("сделай короче", "+", "нет", "что по ценам?", "правка без двоеточия",
                     "перезвони и уточни", ""):
            self.assertIsNone(moderation_core.parse_lesson(text), text)

    def test_empty_remark_still_lesson(self):
        p = moderation_core.parse_lesson("урок:")
        self.assertEqual(p, {"kind": "урок", "remark": ""})

    # --- права (process_lesson) ---
    def test_non_lesson_passes_through(self):
        d = moderation_core.process_lesson(self.DRAFT, "сделай короче", "danya")
        self.assertEqual(d["decision"], "not_lesson")   # обычный reply-путь не трогаем

    def test_approver_lesson_accepted_with_window(self):
        d = moderation_core.process_lesson(self.DRAFT, "правка: не дави ценой", "danya")
        self.assertEqual(d["decision"], "lesson")
        self.assertEqual(d["kind"], "правка")
        self.assertEqual(d["remark"], "не дави ценой")
        self.assertEqual(d["window"], 555)              # окно диалога = client_id карточки
        self.assertEqual(d["draft_id"], 7)

    def test_all_four_teachers_allowed(self):
        for who in ("filipp", "filipp_alt", "danya", "dasha"):
            d = moderation_core.process_lesson(self.DRAFT, "урок: уточняй опыт", who)
            self.assertEqual(d["decision"], "lesson", who)

    def test_stranger_lesson_denied_politely(self):
        d = moderation_core.process_lesson(self.DRAFT, "правка: пиши мягче", "stranger")
        self.assertEqual(d["decision"], "denied")
        self.assertIn("только Филипп", d["card"])
        self.assertNotIn("window", d)                   # чужому окно/замечание не отдаём

    def test_stranger_non_lesson_still_passes_through(self):
        # у чужого обычный reply не блокируется этим перехватом (его отсекает process_reply-whitelist)
        d = moderation_core.process_lesson(self.DRAFT, "+", "stranger")
        self.assertEqual(d["decision"], "not_lesson")

    def test_intake_approver_stricter_than_approve(self):
        # approve открыт всем (APPROVER пуст), но учить всё равно нельзя вне INTAKE_APPROVERS
        self.assertTrue(suggest.is_approver("stranger"))
        self.assertFalse(suggest.is_intake_approver("stranger"))
        self.assertTrue(suggest.is_intake_approver("@Danya"))   # @ и регистр нормализуются

    def test_intake_falls_back_to_approve_when_unset(self):
        suggest.INTAKE_APPROVERS = set()
        suggest.APPROVER_USERNAMES = {"danya"}
        self.assertTrue(suggest.is_intake_approver("danya"))
        self.assertFalse(suggest.is_intake_approver("stranger"))


class TestLessonEnqueue(unittest.TestCase):
    """Родитель 292, шаг 2: распознанный урок → ШТАТНАЯ задача в очереди дирижёра
    (from=Filipp-pcloc-dec) с ПОЛНЫМ контекстом (замечание + исходный черновик + окно диалога).
    Прямого исполнения нет, гейт не обходим — проверяем только сам факт enqueue и его контекст."""

    # живая карточка: окно 555 (@petya), исходный черновик — «дословный» текст менеджеру,
    # card_msg_id=9099 — координата карточки в модер-группе (реплай-точка для подтверждения
    # «урок принят…» после коммита). draft_by_card грузит его в прод (SELECT * → card_msg_id).
    DRAFT = {"id": 7, "draft": "Аренда от 1200฿/сутки, беру?", "client_id": 555,
             "client_ref": "@petya", "card_msg_id": 9099}

    def setUp(self):
        self._save = (suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES)
        suggest.INTAKE_APPROVERS = {"filipp", "filipp_alt", "danya", "dasha"}
        suggest.APPROVER_USERNAMES = set()
        self.calls = []
        # фейк-enqueue: захватывает (task_text, frm) и отдаёт (ok, id, err) как enqueue_pc_task
        self.fake = lambda text, frm: (self.calls.append((text, frm)) or (True, 4242, None))

    def tearDown(self):
        suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES = self._save

    def test_lesson_enqueues_task_with_full_context(self):
        # реплай-триггер от учителя → ровно одна задача в очереди с полным контекстом
        d = moderation_core.submit_lesson(self.DRAFT, "правка: не дави ценой первой", "danya",
                                          enqueue=self.fake)
        self.assertEqual(d["decision"], "lesson")
        self.assertTrue(d["queued"])
        self.assertEqual(d["task_id"], 4242)
        self.assertEqual(len(self.calls), 1)              # ровно один enqueue — прямого исполнения нет
        text, frm = self.calls[0]
        self.assertEqual(frm, "Filipp-pcloc-dec")         # штатный родитель локального дирижёра
        # полный контекст: замечание + исходный черновик + окно диалога (id/ref) + автор
        self.assertIn("не дави ценой первой", text)       # текст замечания
        self.assertIn("Аренда от 1200฿/сутки", text)      # ИСХОДНЫЙ черновик дословно
        self.assertIn("555", text)                        # окно диалога (client_id из карточки)
        self.assertIn("@petya", text)                     # ref окна
        self.assertIn("#7", text)                         # id черновика
        self.assertIn("@danya", text)                     # кто учит
        self.assertIn("правка", text)                     # тип урока
        self.assertIn("msg=9099", text)                   # message_id карточки — для ответа-подтверждения

    def test_not_lesson_does_not_enqueue(self):
        d = moderation_core.submit_lesson(self.DRAFT, "сделай короче", "danya", enqueue=self.fake)
        self.assertEqual(d["decision"], "not_lesson")
        self.assertEqual(self.calls, [])                  # обычный reply в очередь дирижёра не идёт

    def test_denied_stranger_does_not_enqueue(self):
        d = moderation_core.submit_lesson(self.DRAFT, "урок: пиши мягче", "stranger", enqueue=self.fake)
        self.assertEqual(d["decision"], "denied")
        self.assertEqual(self.calls, [])                  # чужой урок не ставим в очередь

    def test_empty_remark_still_enqueues_window(self):
        # пустое замечание допустимо — задача всё равно несёт окно/черновик (учитель уточнит в окне)
        d = moderation_core.submit_lesson(self.DRAFT, "урок:", "filipp", enqueue=self.fake)
        self.assertEqual(d["decision"], "lesson")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("client_id=555", self.calls[0][0])

    def test_enqueue_failure_does_not_crash_and_flags_card(self):
        # Bridge/сеть отвалились → обработчик не падает, замечание помечено непоставленным
        boom = lambda text, frm: (False, None, "enqueue отклонён Bridge")
        d = moderation_core.submit_lesson(self.DRAFT, "не так: сухой тон", "danya", enqueue=boom)
        self.assertEqual(d["decision"], "lesson")
        self.assertFalse(d["queued"])
        self.assertIsNone(d["task_id"])
        self.assertIn("не встало", d["card"])

    def test_enqueue_exception_swallowed(self):
        def raiser(text, frm):
            raise RuntimeError("bridge down")
        d = moderation_core.submit_lesson(self.DRAFT, "правка: тон", "danya", enqueue=raiser)
        self.assertFalse(d["queued"])                     # исключение проглочено → queued=False
        self.assertIn("bridge down", d["card"])

    def test_build_lesson_task_pure(self):
        # чистый билдер контекста без enqueue — та же полнота, отдельно проверяема
        les = moderation_core.process_lesson(self.DRAFT, "правка: уточняй даты", "danya")
        text = moderation_core.build_lesson_task(les, self.DRAFT, "danya")
        for frag in ("уточняй даты", "Аренда от 1200฿/сутки", "555", "@petya", "#7", "msg=9099"):
            self.assertIn(frag, text, frag)

    def test_card_msg_id_preserved_and_roundtrips_for_ack(self):
        # message_id карточки должен ДОЖИТЬ в payload и распарситься обратно потребителем
        # (lesson_router) — иначе подтверждение «урок принят…» уйдёт «в никуда». Это связка
        # билдер↔потребитель по РЕАЛЬНОМУ полю (правило-класс «тест ≠ реальность»).
        import lesson_router
        d = moderation_core.submit_lesson(self.DRAFT, "урок: пиши мягче", "danya", enqueue=self.fake)
        self.assertTrue(d["queued"])
        text = self.calls[0][0]
        self.assertIn("карточка модер-группы: msg=9099", text)   # координата в payload
        parsed = lesson_router.parse_lesson_task(text)           # обратный разбор потребителем
        self.assertEqual(parsed["card_msg_id"], "9099")          # id дожил → реплай-точка есть

    def test_no_card_msg_id_yields_no_reply_target(self):
        # карточка без message_id → билдер кладёт плейсхолдер, потребитель НЕ реплаит в никуда
        # (parse_lesson_task гасит 'msg=?' в пустую строку) — fail-safe, а не мусорный реплай.
        import lesson_router
        draft = dict(self.DRAFT); draft.pop("card_msg_id")
        d = moderation_core.submit_lesson(draft, "урок: пиши мягче", "danya", enqueue=self.fake)
        self.assertTrue(d["queued"])
        parsed = lesson_router.parse_lesson_task(self.calls[0][0])
        self.assertEqual(parsed["card_msg_id"], "")              # нет id → нет реплай-точки


if __name__ == "__main__":
    unittest.main(verbosity=2)
