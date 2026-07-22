# -*- coding: utf-8 -*-
"""
test_suggest.py — мок-тесты ступени ① SUGGEST. БЕЗ реального Telegram и БЕЗ реального
Anthropic: оба замоканы. Реальной отправки клиенту не происходит нигде.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_suggest -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import re
import json
import time
import asyncio
import datetime
import tempfile
import logging
import unittest
from unittest import mock

import suggest


def _cli_json(result="", main="claude-fable-5", main_in=2766, main_out=31, is_error=False):
    """Собрать stdout как у claude --output-format json: поле result + modelUsage с реальной головой
    (main, большой inputTokens) и служебным haiku (крошечный вход). Для тестов _cli_llm/_parse_cli_json."""
    return json.dumps({
        "type": "result", "is_error": is_error, "result": result,
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 505, "outputTokens": 13},
            main: {"inputTokens": main_in, "outputTokens": main_out},
        },
    })


# ------------------------------- моки Telegram -------------------------------

class FakeMsg:
    def __init__(self, mid):
        self.id = mid


class FakeAction:  # async context manager под `async with client.action(...)`
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeHistMsg:
    def __init__(self, sender_id, message, date=None):
        self.sender_id = sender_id
        self.message = message
        self.date = date


class FakeSender:
    def __init__(self, uid, username=None, first="Test", last=None, bot=False):
        self.id = uid
        self.username = username
        self.first_name = first
        self.last_name = last
        self.bot = bot


class FakeDialog:
    def __init__(self, title, did):
        self.title = title
        self.name = title
        self.id = did


class FakeClient:
    """Замена Telethon-клиента: пишет отправки в self.sent, отдаёт историю/диалоги."""
    def __init__(self, history=None, flood_exc=None, dialogs=None):
        self.history = history or []
        self.sent = []            # список (chat, text)
        self.flood_exc = flood_exc
        self.dialogs = dialogs or []
        self._next_id = 5000

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text, reply_to=None):
        if self.flood_exc is not None:
            raise self.flood_exc
        self._next_id += 1
        self.sent.append((chat, text))
        return FakeMsg(self._next_id)

    def iter_messages(self, entity, limit=50):
        async def gen():
            for m in self.history:
                yield m
        return gen()

    def iter_dialogs(self):
        async def gen():
            for d in self.dialogs:
                yield d
        return gen()


class FakeEvent:
    def __init__(self, client, reply_to, text, is_reply=True, sender=None, chat_id=-1002220000):
        self.client = client
        self.is_reply = is_reply
        self.reply_to_msg_id = reply_to
        self.raw_text = text
        self.sender = sender          # None → _sender_username вернёт None
        self.chat_id = chat_id        # куда идёт ack (группа модерации, не клиент)


async def _nosleep(_):
    return None


def _fake_llm(_system, _user):
    return "DRAFT ответа клиенту"


# ------------------------------- тесты ---------------------------------------

class TestPureLogic(unittest.TestCase):

    def test_detect_lang(self):
        self.assertEqual(suggest.detect_lang("Привет, сколько стоит?"), "ru")
        self.assertEqual(suggest.detect_lang("Hello, how much?"), "en")
        self.assertEqual(suggest.detect_lang("650K per day"), "en")

    def test_detect_lang_from_client_whole_dialog(self):
        # язык по ВСЕМ репликам клиента, латинское имя модели EN не триггерит
        ru = ("[менеджер]: Здравствуйте!\n[клиент]: Какие байки есть?\n"
              "[клиент]: NMAX с 10 июля\n[клиент]: Adv 350")
        self.assertEqual(suggest.detect_lang_from_client(ru), "ru")   # последняя латиница → всё равно RU
        # латинские названия моделей, но текст кириллицей → RU
        mix = "[клиент]: хочу adv 350 или nmax 155 на месяц"
        self.assertEqual(suggest.detect_lang_from_client(mix), "ru")
        # чистый английский → EN
        en = "[клиент]: hello, what bikes do you have for rent?"
        self.assertEqual(suggest.detect_lang_from_client(en), "en")
        # только модель+число (без осмысленных слов) → дефолт RU (RU-first магазин)
        self.assertEqual(suggest.detect_lang_from_client("[клиент]: Adv 350"), "ru")
        self.assertEqual(suggest.detect_lang_from_client("[клиент]: NMAX 155"), "ru")

    def test_detect_lang_stable_between_drafts(self):
        # два черновика одного клиента (растущий транскрипт) — язык не прыгает RU↔EN
        t1 = "[клиент]: Какие байки есть в аренду?"
        t2 = t1 + "\n[менеджер]: ...\n[клиент]: Adv 350"
        self.assertEqual(suggest.detect_lang_from_client(t1),
                         suggest.detect_lang_from_client(t2))
        self.assertEqual(suggest.detect_lang_from_client(t2), "ru")

    def test_greeting_already_sent(self):
        self.assertTrue(suggest.greeting_already_sent(
            "[менеджер]: Здравствуйте! Что арендуете?\n[клиент]: NMAX"))
        self.assertTrue(suggest.greeting_already_sent("[менеджер]: Hello! How can I help?"))
        # наше сообщение без приветствия → приветствия ещё не было
        self.assertFalse(suggest.greeting_already_sent(
            "[менеджер]: NMAX стоит 449฿/день\n[клиент]: ок"))
        # приветствие у КЛИЕНТА не считается нашим
        self.assertFalse(suggest.greeting_already_sent("[клиент]: Здравствуйте!"))

    def test_autogreeting_already_sent(self):
        # РЕАЛЬНЫЕ дословные автоприветствия Telegram Business (класс «е» ревизора), которые
        # _GREETING_RE НЕ ловит (не слово-привет) → нужен отдельный детект. Строка [менеджер]:.
        self.assertTrue(suggest.autogreeting_already_sent(
            "[менеджер]: Спасибо, что выбрали нас! Мы в БангТао и Камале.\n[клиент]: NMAX есть?"))
        self.assertTrue(suggest.autogreeting_already_sent(
            "[менеджер]: Уже смотрю ваше сообщение, отвечу через минуту"))
        # ё→е и пунктуация-агностично (парафразы форматирования владельца)
        self.assertTrue(suggest.autogreeting_already_sent(
            "[менеджер]: Спасибо,   что  выбрали  нас 🙏"))
        self.assertTrue(suggest.autogreeting_already_sent(
            "[менеджер]: Уже смотрю ваше сообщение."))  # регекс матчит префикс «сообщени»
        # НЕГАТИВЫ: автогритинг у КЛИЕНТА не наш; обычный наш ответ; слово-привет (это ловит
        # greeting_already_sent, но НЕ autogreeting); чистый первый контакт без нашей строки.
        self.assertFalse(suggest.autogreeting_already_sent(
            "[клиент]: Спасибо, что выбрали нас"))
        self.assertFalse(suggest.autogreeting_already_sent(
            "[менеджер]: NMAX стоит 449฿/день"))
        self.assertFalse(suggest.autogreeting_already_sent(
            "[менеджер]: Здравствуйте! Что арендуете?"))
        self.assertFalse(suggest.autogreeting_already_sent(
            "[клиент]: Какие марки и модели, какие цены на аренду?"))
        self.assertFalse(suggest.autogreeting_already_sent(""))

    def test_strip_greeting(self):
        # одиночные зачины RU: срез + восстановление первой заглавной остатка
        self.assertEqual(suggest.strip_greeting("Здравствуйте! Аренда 500฿/день"),
                         "Аренда 500฿/день")
        self.assertEqual(suggest.strip_greeting("Привет, хочу арендовать байк"),
                         "Хочу арендовать байк")
        self.assertEqual(suggest.strip_greeting("Добрый день, какие есть модели?"),
                         "Какие есть модели?")
        # business-автоприветствия (класс «е» ревизора) — реальные дословные тексты
        self.assertEqual(suggest.strip_greeting("Спасибо, что написали! Уже смотрю ваше сообщение"),
                         "Уже смотрю ваше сообщение")
        self.assertEqual(suggest.strip_greeting("Спасибо, что выбрали нас. NMAX свободен"),
                         "NMAX свободен")
        # комбинация зачинов подряд + эмодзи между ними
        self.assertEqual(
            suggest.strip_greeting("Здравствуйте! 😊 Спасибо, что выбрали нас. NMAX свободен"),
            "NMAX свободен")
        # EN-зачин
        self.assertEqual(suggest.strip_greeting("Hi there, bikes available"),
                         "There, bikes available")
        # НЕТ зачина → текст без изменений (кавычки/скобки не трогаем)
        self.assertEqual(suggest.strip_greeting("Хонда Клик 125 доступна"),
                         "Хонда Клик 125 доступна")
        self.assertEqual(suggest.strip_greeting("«Honda» в наличии"),
                         "«Honda» в наличии")
        # первый символ остатка — цифра: регистр не трогаем
        self.assertEqual(suggest.strip_greeting("Здравствуйте! 500฿ в сутки"),
                         "500฿ в сутки")
        # пустой/None-safe
        self.assertEqual(suggest.strip_greeting(""), "")

    def test_strip_greeting_for_window(self):
        # stripGreeting(draft, history) — гард повторного приветствия перед отправкой (#311→#56).
        draft = "Здравствуйте! NMAX свободен, 449฿/день"
        # ПОЗИТИВЫ (окно = продолжение) → зачин срезан:
        #  • автоприветствие Telegram Business (реальный дословный текст класса «е»)
        self.assertEqual(
            suggest.strip_greeting_for_window(
                draft, "[менеджер]: Спасибо, что выбрали нас! Мы в БангТао и Камале.\n"
                       "[клиент]: NMAX есть?"),
            "NMAX свободен, 449฿/день")
        #  • второе поколение автоприветствия владельца
        self.assertEqual(
            suggest.strip_greeting_for_window(
                draft, "[менеджер]: Уже смотрю ваше сообщение\n[клиент]: сколько NMAX?"),
            "NMAX свободен, 449฿/день")
        #  • наш прошлый ответ, начатый приветствием
        self.assertEqual(
            suggest.strip_greeting_for_window(
                draft, "[менеджер]: Здравствуйте! Что арендуете?\n[клиент]: NMAX"),
            "NMAX свободен, 449฿/день")
        #  • наш прошлый ответ БЕЗ приветствия — всё равно продолжение, второй раз не здороваемся
        self.assertEqual(
            suggest.strip_greeting_for_window(
                draft, "[менеджер]: Какие даты вас интересуют?\n[клиент]: с 20 июля"),
            "NMAX свободен, 449฿/день")
        #  • другой зачин черновика «Привет» на продолжении → тоже срезан
        self.assertEqual(
            suggest.strip_greeting_for_window(
                "Привет, NMAX свободен", "[менеджер]: Здравствуйте!\n[клиент]: NMAX?"),
            "NMAX свободен")
        #  • зачин черновика «Добрый день» на продолжении → срезан
        self.assertEqual(
            suggest.strip_greeting_for_window(
                "Добрый день, какие есть модели?",
                "[менеджер]: Спасибо, что написали! Уже смотрю\n[клиент]: что есть?"),
            "Какие есть модели?")
        # НЕГАТИВЫ (чистое окно) → черновик БЕЗ изменений, фирменное приветствие цело:
        #  • первый контакт: в окне только клиент
        self.assertEqual(
            suggest.strip_greeting_for_window(draft, "[клиент]: Здравствуйте, что есть из байков?"),
            draft)
        #  • приветствие/автогритинг у КЛИЕНТА не считается нашим
        self.assertEqual(
            suggest.strip_greeting_for_window(draft, "[клиент]: Спасибо, что выбрали нас — шучу 😄"),
            draft)
        #  • пустая история
        self.assertEqual(suggest.strip_greeting_for_window(draft, ""), draft)
        #  • нет зачина в черновике → как есть даже на продолжении
        self.assertEqual(
            suggest.strip_greeting_for_window(
                "NMAX свободен", "[менеджер]: Здравствуйте!\n[клиент]: ок"),
            "NMAX свободен")

    def test_strip_greeting_for_window_golden_529849022(self):
        # ГОЛДЕН живого окна 529849022 (шаг 3/5 родитель #56, класс «е»): история — статичное
        # автоприветствие Telegram Business («спасибо, что выбрали нас») + ДВА наших ответа,
        # затем strategy-регенерация выдала черновик с ПОВТОРНЫМ «Здравствуйте!». Окно — явное
        # продолжение (три строки [менеджер]:), поэтому гард обязан срезать зачин перед отправкой.
        history = (
            "[менеджер]: Здравствуйте, спасибо, что выбрали нас 🤝 Напишите, что хотели бы "
            "арендовать, с какого числа и на какой срок.\n"
            "[клиент]: Привет! Что есть из скутеров?\n"
            "[менеджер]: Из скутеров есть NMAX 155, ADV 350 и Forza 300. На какие даты смотрите?\n"
            "[клиент]: с 20 июля на две недели\n"
            "[менеджер]: Отлично, зафиксировал даты. Какая модель ближе?\n"
            "[клиент]: а по цене что посоветуете?")
        draft = "Здравствуйте! Подберём для вас байк по актуальному прайсу…"
        self.assertEqual(
            suggest.strip_greeting_for_window(draft, history),
            "Подберём для вас байк по актуальному прайсу…")

    def test_strip_internal_markers(self):
        # Шаг 3/5 родитель #55: чистая stripInternalMarkers режет строки с внутренними
        # служебными маркерами и оставляет полезный текст. По кейсу на каждый из 4 паттернов.
        #  • «Этап \d»
        self.assertEqual(
            suggest.stripInternalMarkers("NMAX свободен\nЭтап 3: ждём паспорт"),
            "NMAX свободен")
        #  • «менеджер ещё не назвал» (в т.ч. вариант «еще» без ё)
        self.assertEqual(
            suggest.stripInternalMarkers("Цена 500฿\nменеджер ещё не назвал модель"),
            "Цена 500฿")
        self.assertEqual(
            suggest.stripInternalMarkers("Цена 500฿\nменеджер еще не назвал сроки"),
            "Цена 500฿")
        #  • «собрано»
        self.assertEqual(
            suggest.stripInternalMarkers("XSR 155 в наличии\nсобрано: даты, модель"),
            "XSR 155 в наличии")
        #  • «[уточнить»
        self.assertEqual(
            suggest.stripInternalMarkers("Аренда доступна\n[уточнить: даты аренды]"),
            "Аренда доступна")
        #  • маркер в СЕРЕДИНЕ текста — вырезается вся строка, соседние целы
        self.assertEqual(
            suggest.stripInternalMarkers(
                "Здравствуйте!\nЭтап 2: ждём фото\nNMAX 449฿/день"),
            "Здравствуйте!\nNMAX 449฿/день")
        #  • НЕСКОЛЬКО маркеров сразу — все строки со следами вон, полезное цело
        self.assertEqual(
            suggest.stripInternalMarkers(
                "Этап 1\nCLICK 125 свободен\n[уточнить: сроки]\nсобрано: модель\n449฿/день"),
            "CLICK 125 свободен\n449฿/день")
        #  • НЕГАТИВ: текст без маркеров возвращается без изменений
        clean = "Здравствуйте! XSR 155 — 1685 THB/сутки. Свободен на ваши даты."
        self.assertEqual(suggest.stripInternalMarkers(clean), clean)

    def test_strip_internal_markers_golden_529849022(self):
        # ГОЛДЕН живого окна 529849022 (шаг 4/5 родитель #55): черновик — ОДНА строка,
        # где сразу ДВА внутренних маркера («менеджер ещё не назвал» + «Этап 1»). Клиент
        # такого видеть не должен: строка со следом стадии сделки режется ЦЕЛИКОМ, и, так как
        # весь черновик — эта строка, наружу не уходит НИЧЕГО (пустой результат). Фраза дословная.
        draft = ("Клиент готов бронировать по датам 7 июля на неделю, "
                 "менеджер ещё не назвал цену — сейчас Этап 1.")
        self.assertEqual(suggest.stripInternalMarkers(draft), "")

    def test_parse_approval(self):
        self.assertEqual(suggest.parse_approval("+"), ("approve", None))
        self.assertEqual(suggest.parse_approval("да"), ("approve", None))
        self.assertEqual(suggest.parse_approval("да 2")[0], "approve")
        self.assertEqual(suggest.parse_approval("-"), ("reject", None))
        self.assertEqual(suggest.parse_approval("нет"), ("reject", None))
        act, payload = suggest.parse_approval("Здравствуйте! NMAX 449฿/день")
        self.assertEqual(act, "edit")
        self.assertEqual(payload, "Здравствуйте! NMAX 449฿/день")

    def test_critical_facts_in_prompt(self):
        sysp = suggest.make_system_prompt("FAQ-тело", "ru")
        self.assertIn("CLICK 125", sysp)          # Click дословно
        self.assertIn("NMAX155 449/3000", sysp)   # прайс дословно
        self.assertIn("FAQ-тело", sysp)           # живой FAQ вклеен
        self.assertIn("русском", sysp)            # язык клиента

    def test_generate_draft_uses_injected_llm(self):
        d = suggest.generate_draft("[клиент]: привет", "ru", "FAQ", call_llm=_fake_llm)
        self.assertEqual(d, "DRAFT ответа клиенту")

    def test_prompt_forbids_leaking_stage_context(self):
        # Класс-фикс: промпт явно запрещает начинать ответ с описания ситуации/этапа и
        # помечает блок этапов как ВНУТРЕННИЙ (клиенту не показывать).
        sysp = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("ВНУТРЕННИЙ КОНТЕКСТ", sysp)          # блок этапов помечен как внутренний
        self.assertIn("НЕ начинай ответ с описания ситуации", sysp)
        self.assertIn("сейчас Этап N", sysp)                # запрет пометки стадии в тексте
        # инварианты целы
        self.assertIn("CLICK 125", sysp)                    # критфакты
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)             # ценовая политика
        self.assertIn("русском", sysp)                      # язык

    def test_strip_service_prefix_removes_stage_note(self):
        # Точный кейс бага: черновик начинается со служебной строки о стадии сделки.
        leaked = ("Клиент готов бронировать по датам 7 июля на неделю, менеджер ещё не назвал "
                  "цену — сейчас Этап 1.\nCBR 650R — 650฿/день, депозит 5000฿, свободен на эти даты.")
        out = suggest._strip_service_prefix(leaked)
        # служебные маркеры вычищены
        self.assertNotIn("Этап", out)
        self.assertNotIn("Клиент готов бронировать", out)
        self.assertNotIn("менеджер ещё не назвал", out)
        # реальный ответ (модель/цена/депозит) на месте
        self.assertIn("CBR 650R", out)
        self.assertIn("650฿/день", out)
        self.assertIn("депозит 5000฿", out)

    def test_strip_service_prefix_same_line(self):
        # Служебная пометка и ответ в ОДНОЙ строке — режем по концу служебного предложения.
        out = suggest._strip_service_prefix("Сейчас Этап 1. NMAX 449฿/день, свободен.")
        self.assertNotIn("Этап", out)
        self.assertTrue(out.startswith("NMAX 449"))

    def test_strip_service_prefix_leaves_clean_answer(self):
        # Нормальный ответ (приветствие/цена, без служебных маркеров) НЕ трогаем.
        clean = "Здравствуйте! NMAX на 7 дней — 449฿/день, депозит 3000฿. Какой район доставки?"
        self.assertEqual(suggest._strip_service_prefix(clean), clean)
        # Слово «цену» без служебного контекста тоже не должно триггерить срез.
        self.assertEqual(suggest._strip_service_prefix("Назову цену по датам."),
                         "Назову цену по датам.")

    def test_strip_service_prefix_never_empties(self):
        # Если весь черновик — только служебная строка: пусто НЕ возвращаем (отдаём как есть).
        only = "Клиент готов бронировать, менеджер ещё не назвал цену — сейчас Этап 1."
        self.assertEqual(suggest._strip_service_prefix(only), only)
        self.assertEqual(suggest._strip_service_prefix(""), "")

    def test_generate_draft_scrubs_service_prefix(self):
        # Сквозь generate_draft: LLM отдал служебный префикс → в черновике его нет, суть цела.
        def leaky(_s, _u):
            return ("Клиент готов бронировать — сейчас Этап 1.\n"
                    "Здравствуйте! CBR 650R — 650฿/день, депозит 5000฿.")
        d = suggest.generate_draft("[клиент]: CBR 650R на неделю с 7 июля", "ru", "FAQ",
                                   is_first_contact=True, call_llm=leaky)
        self.assertNotIn("Этап", d)
        self.assertNotIn("Клиент готов", d)
        self.assertTrue(d.startswith("Здравствуйте!"))     # приветствие сохранено
        self.assertIn("650฿/день", d)                      # цена/депозит целы
        self.assertIn("депозит 5000฿", d)

    def test_directive_in_prompt_preserves_invariants(self):
        # СТРАТЕГИЯ-директива входит в системный промпт ВЕРХНИМ приоритетом, но кап-цена и
        # критфакты (ценовая политика + CRITICAL_FACTS) НЕ ослабляются.
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          directive="дожимай на ADV, скажи что NMAX разберут")
        self.assertIn("ДИРЕКТИВА МЕНЕДЖЕРА", sysp)
        self.assertIn("дожимай на ADV", sysp)                    # директива на месте
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)      # кап-цена сохранена
        self.assertIn("CLICK 125", sysp)                         # критфакт дословно
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                  # ценовая политика (кап) на месте
        self.assertNotIn("ДИРЕКТИВА МЕНЕДЖЕРА",                  # без директивы блока нет
                         suggest.make_system_prompt("FAQ", "ru"))

    def test_generation_default_rule_in_prompt(self):
        # Глобальное правило поколений в системном промпте (кейс @cryptopeppa 15.07): по умолчанию
        # ТОЛЬКО актуальное поколение (New Gen), без годов и без «старый»; прежнее/года — по явному
        # запросу; инвариант наличия НЕ ослаблен (правило поколений про модель, не про наличие).
        sysp = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("ПОКОЛЕНИЯ МОДЕЛИ (ПО УМОЛЧАНИЮ — ТОЛЬКО АКТУАЛЬНОЕ)", sysp)
        self.assertIn("New Gen", sysp)
        self.assertIn("НЕ пиши года выпуска", sysp)                 # без годов поколений
        self.assertIn("«старый»", sysp)                             # без слова «старый»
        self.assertIn("ТОЛЬКО если клиент САМ явно про них спросил", sysp)
        self.assertIn("НАЛИЧИЕ, ДЕФИЦИТ И ОСОБЫЕ УСЛОВИЯ", sysp)    # гард наличия на месте (не ослаблен)
        self.assertIn("правило наличия выше остаётся в силе", sysp)

    def test_regenerate_draft_injects_directive(self):
        seen = {}
        def fake(system, user):
            seen["system"] = system; seen["user"] = user
            return "перегенерённый ответ"
        out = suggest.regenerate_draft("[клиент]: NMAX на месяц?", "ru", "FAQ", False,
                                       "ЦЕНА из Календаря: 500฿/день", "жёстче про депозит", call_llm=fake)
        # §243/6: NMAX+месяц собраны → в хвосте пометка модератору; тело ответа цело
        self.assertTrue(out.startswith("перегенерённый ответ"), out)
        self.assertIn("жёстче про депозит", seen["system"])      # директива в system
        self.assertIn("ЦЕНА из Календаря", seen["system"])       # кап сохранён
        self.assertIn("CLICK 125", seen["system"])               # критфакты сохранены
        self.assertEqual(seen["user"], "[клиент]: NMAX на месяц?")  # исходный транскрипт = user

    def test_strategy_regen_keeps_point_quote_price_from_block(self):
        # Класс #365 шаг 1/7: СТРАТЕГИЯ-перегенерация обязана донести ТОЧЕЧНЫЙ quote-блок (цифры из
        # Bridge) в финал — и НЕ пропустить итог, которого в блоке нет. Блок едет в pricing_note (IPC
        # несёт его в перегенерацию), политика промпта его описывает, пост-чек вайтлистит только цифры
        # Bridge. Голден живого окна 504608015: «NMAX 155 на 5 дней — 2245 ฿, депозит 3000 ฿».
        # (PRICE_SHEET-случай сборки кодом покрыт TestSheetUntouchableBlock.test_regenerate_draft_same_class.)
        note = suggest._wrap_single("ok", "NMAX 155 — за 5 дней 2245 ฿, депозит 3000 ฿")
        seen = {}
        def fake(system, user):
            seen["system"] = system
            # LLM честно приводит цифры блока ДОСЛОВНО и вдобавок выдумывает свой итог ВНЕ блока:
            return ("Отличный выбор — NMAX 155 на 5 дней 2245 ฿, депозит 3000 ฿. "
                    "Также за 7 дней это 9999 ฿ — выгодно.")
        out = suggest.regenerate_draft("[клиент]: NMAX 155 на 5 дней, посчитайте?", "ru", "FAQ",
                                       False, note, "дожимай на бронь", call_llm=fake)
        # политика промпта ЗНАЕТ о блоке: и сам quote-блок, и ценовая политика лежат в system
        self.assertIn("ЦЕНА из Календаря", seen["system"])          # точечный quote-блок в промпте
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", seen["system"])           # политика описывает блок
        self.assertIn("дожимай на бронь", seen["system"])           # директива стратегии
        # финал СОДЕРЖИТ цены из блока (Bridge), но НЕ выдуманный LLM итог вне блока
        self.assertIn("2245", out)                                  # итог-за-период из блока уцелел
        self.assertIn("3000", out)                                  # депозит из блока уцелел
        self.assertNotIn("9999", suggest.client_facing_text(out))   # число вне блока клиенту не течёт
        self.assertIn("Уточню у команды", out)                      # вне-блока итог → «уточню»-форма
        self.assertIn("[уточнить: цена 9999]", out)                 # пометка модератору

    def test_rate_limiter_hour_and_day(self):
        t = [1000.0]
        rl = suggest.RateLimiter(per_hour=2, per_day=3, now=lambda: t[0])
        self.assertTrue(rl.allow()[0]); rl.record()
        self.assertTrue(rl.allow()[0]); rl.record()
        self.assertFalse(rl.allow()[0])           # 2/час исчерпан
        t[0] += 3601                              # прошёл час
        self.assertTrue(rl.allow()[0]); rl.record()
        self.assertFalse(rl.allow()[0])           # 3/день исчерпан

    def test_pending_store_durable(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pending.jsonl")
            ps = suggest.PendingStore(p)
            ps.add(111, {"client_id": 7, "draft": "x"})
            self.assertEqual(ps.get(111)["client_id"], 7)
            # новый экземпляр читает журнал — pending переживает «рестарт»
            ps2 = suggest.PendingStore(p)
            self.assertEqual(ps2.get(111)["draft"], "x")
            ps2.pop(111)
            ps3 = suggest.PendingStore(p)
            self.assertIsNone(ps3.get(111))

    def test_edit_diff(self):
        self.assertEqual(suggest._edit_diff("abc", "abc"), "")
        self.assertEqual(suggest._edit_diff("abc", "abd"), "abd")


class TestSendGuards(unittest.TestCase):

    def setUp(self):
        suggest.reset_disabled()
        suggest.limiter = suggest.RateLimiter(6, 15)

    def test_send_success_no_real_wait(self):
        c = FakeClient()
        ok, reason = asyncio.run(
            suggest.send_to_client(c, 999, "привет", sleep=_nosleep, jitter=lambda: 0)
        )
        self.assertTrue(ok)
        self.assertEqual(c.sent, [(999, "привет")])

    def test_send_rate_limited(self):
        c = FakeClient()
        suggest.limiter = suggest.RateLimiter(1, 15)
        asyncio.run(suggest.send_to_client(c, 1, "a", sleep=_nosleep, jitter=lambda: 0))
        ok, reason = asyncio.run(
            suggest.send_to_client(c, 1, "b", sleep=_nosleep, jitter=lambda: 0)
        )
        self.assertFalse(ok)
        self.assertIn("лимит", reason)
        self.assertEqual(len(c.sent), 1)          # второй не ушёл

    def test_test_mode_blocks_send_to_client(self):
        # Defense-in-depth: даже прямой вызов send_to_client в TEST_MODE ничего не шлёт.
        old = suggest.SUGGEST_TEST_MODE
        suggest.SUGGEST_TEST_MODE = True
        try:
            c = FakeClient()
            ok, reason = asyncio.run(
                suggest.send_to_client(c, 1, "x", sleep=_nosleep, jitter=lambda: 0)
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "TEST_MODE")
            self.assertEqual(c.sent, [])          # клиенту ничего
        finally:
            suggest.SUGGEST_TEST_MODE = old

    def test_send_strips_internal_markers_before_send(self):
        # Шаг 2/5 родитель #55: гард исходящих в ТОЧКЕ отправки — клиенту уходит текст БЕЗ
        # внутренних маркеров («Этап N»/«собрано»/«[уточнить»), и факт среза виден в штатном логе
        # с ОКНОМ (client_id) и тем, ЧТО вырезано.
        c = FakeClient()
        draft = ("Здравствуйте! XSR 155 — 1685 THB/сутки.\n"
                 "Этап 3: ждём паспорт.\n"
                 "[уточнить: даты аренды]")
        with self.assertLogs("suggest", level="WARNING") as cm:
            ok, _ = asyncio.run(
                suggest.send_to_client(c, 777, draft, sleep=_nosleep, jitter=lambda: 0)
            )
        self.assertTrue(ok)
        sent_text = c.sent[0][1]
        self.assertNotIn("Этап 3", sent_text)             # внутренний маркер не течёт клиенту
        self.assertNotIn("[уточнить", sent_text)
        self.assertIn("1685", sent_text)                  # полезный текст цел
        blob = "\n".join(cm.output)
        self.assertIn("stripInternalMarkers", blob)       # штатный лог сработал
        self.assertIn("777", blob)                        # с указанием окна (client_id)
        self.assertIn("Этап 3", blob)                     # и что именно вырезано

    def test_send_clean_text_no_strip_log(self):
        # Чистый текст без маркеров уходит как есть и НЕ пишет предупреждение о срезе.
        c = FakeClient()
        log = logging.getLogger("suggest")
        with self.assertLogs(log, level="WARNING") as cm:
            log.warning("SUGGEST[sentinel] проба")        # sentinel: assertLogs требует ≥1 записи
            asyncio.run(
                suggest.send_to_client(c, 5, "XSR 155 — 1685 THB/сутки", sleep=_nosleep, jitter=lambda: 0)
            )
        self.assertEqual(c.sent, [(5, "XSR 155 — 1685 THB/сутки")])
        self.assertNotIn("stripInternalMarkers", "\n".join(cm.output))

    def test_flood_disables_suggest(self):
        class MyFlood(Exception):
            pass
        old = suggest.FLOOD_ERRORS
        suggest.FLOOD_ERRORS = (MyFlood,)
        try:
            c = FakeClient(flood_exc=MyFlood("PEER_FLOOD"))
            ok, reason = asyncio.run(
                suggest.send_to_client(c, 1, "a", sleep=_nosleep, jitter=lambda: 0)
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "flood")
            self.assertFalse(suggest.is_enabled() if suggest.SUGGEST_MODE else False)
            self.assertTrue(suggest._disabled)     # авто-стоп сработал
        finally:
            suggest.FLOOD_ERRORS = old
            suggest.reset_disabled()


class TestFullFlow(unittest.TestCase):
    """Мок-входящее → черновик на модерацию → ✅/правка/отклонение → отправка (замокана)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self._mode = suggest.SUGGEST_MODE
        self._test = suggest.SUGGEST_TEST_MODE
        self._mg = suggest.MOD_GROUP_ID
        self._pending = suggest.pending
        self._pairs = suggest.PAIRS_FILE
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.reset_disabled()
        suggest.MOD_GROUP_ID = -1009999999999
        suggest.pending = suggest.PendingStore(os.path.join(d, "pending.jsonl"))
        suggest.PAIRS_FILE = os.path.join(d, "pairs.jsonl")
        suggest.limiter = suggest.RateLimiter(6, 15)
        self.me = 42
        self.client = FakeClient(history=[
            FakeHistMsg(self.me, "Здравствуйте! Что хотите арендовать?"),
            FakeHistMsg(999, "Привет, сколько стоит NMAX на неделю?"),
        ])
        self.sender = FakeSender(999, username="client1")

    def tearDown(self):
        suggest.SUGGEST_MODE = self._mode
        suggest.SUGGEST_TEST_MODE = self._test
        suggest.MOD_GROUP_ID = self._mg
        suggest.pending = self._pending
        suggest.PAIRS_FILE = self._pairs
        suggest.reset_disabled()
        self._tmp.cleanup()

    def _incoming(self):
        return asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=_fake_llm, faq="FAQ-тело")
        )

    def _pairs_lines(self):
        with open(suggest.PAIRS_FILE, encoding="utf-8") as f:
            import json
            return [json.loads(x) for x in f if x.strip()]

    def test_incoming_posts_draft_and_pending(self):
        mid = self._incoming()
        self.assertIsNotNone(mid)
        # черновик ушёл в группу модерации (а НЕ клиенту)
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0][0], suggest.MOD_GROUP_ID)
        self.assertIn("DRAFT ответа клиенту", self.client.sent[0][1])
        self.assertIsNotNone(suggest.pending.get(mid))

    def test_generation_exception_posts_visible_note(self):
        # сбой генератора (claude CLI не найден) → видимая заметка в группу, НЕ молчаливый пропуск
        def boom(_s, _u):
            raise RuntimeError("claude CLI не найден (PATH/CLAUDE_BIN/AppData)")
        res = asyncio.run(suggest.on_client_message(self.client, self.sender, self.me,
                                                    call_llm=boom, faq="FAQ"))
        self.assertIsNone(res)
        notes = [t for t in self.client.sent if t[0] == suggest.MOD_GROUP_ID and "НЕ сгенерирован" in t[1]]
        self.assertEqual(len(notes), 1)
        self.assertIn("claude CLI не найден", notes[0][1])
        self.assertIn("@client1", notes[0][1])
        self.assertTrue(notes[0][1].lstrip().startswith("⚠️"))
        # карточки-черновика НЕ было, pending пуст
        self.assertFalse(any("Черновик ответа клиенту" in t[1] for t in self.client.sent))

    def test_empty_draft_posts_visible_note(self):
        # пустой вывод LLM → тоже видимая заметка (раньше молчали)
        res = asyncio.run(suggest.on_client_message(self.client, self.sender, self.me,
                                                    call_llm=lambda s, u: "", faq="FAQ"))
        self.assertIsNone(res)
        notes = [t for t in self.client.sent if "НЕ сгенерирован" in t[1]]
        self.assertEqual(len(notes), 1)
        self.assertIn("пустой вывод LLM", notes[0][1])
        self.assertIn("@client1", notes[0][1])

    def test_success_still_posts_card_no_note(self):
        # успех — карточка как раньше, никакой заметки о сбое
        mid = self._incoming()
        self.assertIsNotNone(mid)
        self.assertTrue(any("Черновик ответа клиенту" in t[1] for t in self.client.sent))
        self.assertFalse(any("НЕ сгенерирован" in t[1] for t in self.client.sent))

    def test_approve_sends_draft_to_client(self):
        mid = self._incoming()
        ev = FakeEvent(self.client, reply_to=mid, text="+")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertTrue(res["sent"])
        # отправка КЛИЕНТУ (id 999) с текстом черновика (среди отправок; после неё — ack в группу).
        # §243/6: NMAX+неделя собраны → в хвосте черновика пометка модератору; тело ответа цело.
        client_sends = [t for t in self.client.sent if t[0] == 999]
        self.assertTrue(any(txt.startswith("DRAFT ответа клиенту") for _, txt in client_sends),
                        client_sends)
        self.assertEqual(res["ack"], "✅ Отправлено клиенту")
        pairs = self._pairs_lines()
        self.assertEqual(pairs[-1]["action"], "approve")
        self.assertEqual(pairs[-1]["client"], "@client1")

    def test_edit_sends_edited_version(self):
        mid = self._incoming()
        edited = "Здравствуйте! NMAX 155 — 449฿/день, депозит 3000฿."
        ev = FakeEvent(self.client, reply_to=mid, text=edited)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "edit")
        client_sends = [t for t in self.client.sent if t[0] == 999]
        self.assertIn((999, edited), client_sends)
        pairs = self._pairs_lines()
        self.assertEqual(pairs[-1]["edit"], edited)      # правка зафиксирована

    def test_reject_does_not_send(self):
        mid = self._incoming()
        ev = FakeEvent(self.client, reply_to=mid, text="-")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "reject")
        self.assertFalse(res["sent"])
        self.assertEqual([t for t in self.client.sent if t[0] == 999], [])  # клиенту ничего
        self.assertEqual(res["ack"], "❌ Отклонено")
        self.assertIsNone(suggest.pending.get(mid))      # pending снят

    def test_test_mode_does_not_send_to_client(self):
        suggest.SUGGEST_TEST_MODE = True
        mid = self._incoming()
        ev = FakeEvent(self.client, reply_to=mid, text="+")
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertFalse(res["sent"])                    # TEST_MODE: клиенту НЕ ушло
        self.assertEqual([t for t in self.client.sent if t[0] == 999], [])
        self.assertIn("TEST_MODE", res["ack"])

    def test_reply_not_on_our_draft_ignored(self):
        self._incoming()
        ev = FakeEvent(self.client, reply_to=123456789, text="+")  # чужой reply
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertIsNone(res)

    def test_greeting_reflected_on_first_contact(self):
        # мок-LLM отражает, попросили ли в промпте приветствие; ЧИСТЫЙ первый контакт:
        # в истории ТОЛЬКО реплика клиента (нашего приветствия ещё не было).
        def refllm(system, _user):
            return "GREETED" if "ПЕРВЫЙ ответ" in system else "CONT"
        self.client.history = [FakeHistMsg(999, "Привет, сколько стоит NMAX на неделю?")]
        mid = asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=refllm, faq="FAQ")
        )
        rec = suggest.pending.get(mid)
        self.assertTrue(rec["first_contact"])
        self.assertTrue(rec["draft"].startswith("GREETED"), rec["draft"])  # +хвост «собрано: …»

    def test_no_regreet_when_greeting_in_history(self):
        # приветствие уже уходило (наше «Здравствуйте» в истории) → повторно НЕ здороваемся.
        def refllm(system, _user):
            return "GREETED" if "ПЕРВЫЙ ответ" in system else "CONT"
        self.client.history = [
            FakeHistMsg(self.me, "Здравствуйте! Что хотите арендовать?"),
            FakeHistMsg(999, "А сколько стоит NMAX на неделю?"),
        ]
        mid = asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=refllm, faq="FAQ")
        )
        rec = suggest.pending.get(mid)
        self.assertFalse(rec["first_contact"])
        self.assertTrue(rec["draft"].startswith("CONT"), rec["draft"])   # +хвост «собрано: …»

    def test_lang_stable_latin_model_name_stays_ru(self):
        # русский диалог + последняя реплика «Adv 350» (латиница) → черновик остаётся RU, НЕ EN.
        seen = {}
        def caplang(system, _user):
            seen["ru"] = "русском" in system
            return "ЧЕРНОВИК"
        self.client.history = [
            FakeHistMsg(self.me, "Здравствуйте! Что хотите арендовать?"),
            FakeHistMsg(999, "Какие байки есть в аренду?"),
            FakeHistMsg(999, "NMAX с 10 июля на месяц"),
            FakeHistMsg(999, "Adv 350"),
        ]
        mid = asyncio.run(
            suggest.on_client_message(self.client, self.sender, self.me,
                                      call_llm=caplang, faq="FAQ")
        )
        rec = suggest.pending.get(mid)
        self.assertEqual(rec["lang"], "ru")
        self.assertTrue(seen["ru"])


class TestFaqOrder(unittest.TestCase):
    """Порядок чтения FAQ: faq → turbobaby_faq → локальный файл."""

    def test_primary_faq_used(self):
        getter = lambda name: {"faq": "ЖИВОЙ-FAQ", "turbobaby_faq": "СТАРЫЙ"}.get(name)
        self.assertEqual(suggest.load_faq(getter=getter), "ЖИВОЙ-FAQ")

    def test_fallback_to_turbobaby(self):
        getter = lambda name: {"faq": "", "turbobaby_faq": "СТАРЫЙ-FAQ"}.get(name)
        self.assertEqual(suggest.load_faq(getter=getter), "СТАРЫЙ-FAQ")

    def test_fallback_to_local_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "faq.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("ЛОКАЛЬНЫЙ-FAQ")
            old = suggest.LOCAL_FAQ
            suggest.LOCAL_FAQ = p
            try:
                self.assertEqual(suggest.load_faq(getter=lambda n: ""), "ЛОКАЛЬНЫЙ-FAQ")
            finally:
                suggest.LOCAL_FAQ = old


class TestResolveGroup(unittest.TestCase):
    """Резолв группы модерации по имени через iter_dialogs."""

    def setUp(self):
        self._id = suggest.MOD_GROUP_ID
        self._name = suggest.MOD_GROUP_NAME

    def tearDown(self):
        suggest.MOD_GROUP_ID = self._id
        suggest.MOD_GROUP_NAME = self._name

    def test_resolve_by_name(self):
        suggest.MOD_GROUP_ID = None
        suggest.MOD_GROUP_NAME = "Модерация ответов"
        c = FakeClient(dialogs=[
            FakeDialog("Входящие", -1001110000),
            FakeDialog("Модерация ответов", -1002220000),
        ])
        gid = asyncio.run(suggest.resolve_mod_group(c))
        self.assertEqual(gid, -1002220000)
        self.assertEqual(suggest.MOD_GROUP_ID, -1002220000)

    def test_not_found_returns_none(self):
        suggest.MOD_GROUP_ID = None
        suggest.MOD_GROUP_NAME = "Нет такой группы"
        c = FakeClient(dialogs=[FakeDialog("Входящие", -1001110000)])
        gid = asyncio.run(suggest.resolve_mod_group(c))
        self.assertIsNone(gid)

    def test_explicit_id_skips_resolve(self):
        suggest.MOD_GROUP_ID = -1009990000
        suggest.MOD_GROUP_NAME = "Модерация ответов"
        c = FakeClient(dialogs=[])  # iter_dialogs не нужен — ID уже есть
        gid = asyncio.run(suggest.resolve_mod_group(c))
        self.assertEqual(gid, -1009990000)


class TestGreeting(unittest.TestCase):
    """Приветствие на первом контакте; продолжение — без него."""

    def test_prompt_greets_on_first_contact(self):
        p = suggest.make_system_prompt("FAQ", "ru", is_first_contact=True)
        self.assertIn("ПЕРВЫЙ ответ", p)
        self.assertIn("приветств", p.lower())

    def test_prompt_no_greet_on_continuation(self):
        p = suggest.make_system_prompt("FAQ", "ru", is_first_contact=False)
        self.assertIn("ПРОДОЛЖЕНИЕ", p)
        self.assertIn("НЕ здоровайся", p)

    def test_first_contact_detection(self):
        now = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=datetime.timezone.utc)
        me = 42
        # наш ответ 2ч назад (в окне 18ч) → продолжение (False)
        cont = [FakeHistMsg(999, "вопрос", date=now - datetime.timedelta(hours=1)),
                FakeHistMsg(42, "ответ", date=now - datetime.timedelta(hours=2))]
        self.assertFalse(suggest.first_contact_from(cont, me, hours=18, now=lambda: now))
        # наш ответ 2 дня назад (вне окна) → первый контакт (True)
        old = [FakeHistMsg(999, "вопрос", date=now - datetime.timedelta(hours=1)),
               FakeHistMsg(42, "ответ", date=now - datetime.timedelta(days=2))]
        self.assertTrue(suggest.first_contact_from(old, me, hours=18, now=lambda: now))
        # только клиентские сообщения → первый контакт
        only = [FakeHistMsg(999, "привет", date=now - datetime.timedelta(hours=1))]
        self.assertTrue(suggest.first_contact_from(only, me, hours=18, now=lambda: now))


class _ReplyMsg:
    """Сообщение с полями, которые смотрит reply-логика (id/reply_to_msg_id/photo/media)."""
    def __init__(self, mid, sender_id, message="", reply_to=None, photo=False,
                 media=False, geo=False, date=None):
        self.id = mid
        self.sender_id = sender_id
        self.message = message
        self.reply_to_msg_id = reply_to
        self.photo = object() if photo else None
        self.media = object() if media else None
        self.geo = object() if geo else None
        self.date = date


class _ReplyClient:
    """Мок клиента для _resolve_replies: get_messages(ids=) отдаёт цели из store (переписка
    вне окна). fail=True → get_messages падает (проверка fail-safe)."""
    def __init__(self, store, fail=False):
        self.store = store
        self.fail = fail

    async def get_messages(self, entity, ids=None):
        if self.fail:
            raise RuntimeError("boom")
        return [self.store.get(i) for i in (ids or [])]


class TestReplyContext(unittest.TestCase):
    """Голден 12.07: клиент шлёт «Вот» реплаями на данные из СТАРОЙ брони (Maps-ссылка, фото
    паспорта, телефон) — всё извлекается и попадает в контекст черновика."""

    MAPS = "https://maps.app.goo.gl/abc123XYZ"
    PHONE = "+66 81 234 5678"
    ME = 42

    def _window_and_store(self):
        # старая переписка (ВНЕ окна выборки) — цели reply клиента (id 999):
        store = {
            10: _ReplyMsg(10, 999, message=f"Моя вилла: {self.MAPS}"),
            11: _ReplyMsg(11, 999, photo=True),                # фото паспорта
            12: _ReplyMsg(12, 999, message=f"Мой номер {self.PHONE}"),
        }
        # окно (newest-first, как iter_messages): три «Вот» реплаями на 10/11/12 + наш вопрос
        window = [
            _ReplyMsg(23, 999, message="Вот", reply_to=12),
            _ReplyMsg(22, 999, message="Вот", reply_to=11),
            _ReplyMsg(21, 999, message="Вот", reply_to=10),
            _ReplyMsg(20, self.ME, message="Скиньте гео, паспорт и телефон"),
        ]
        return window, store

    def test_reply_pulls_geo_photo_phone_from_old_booking(self):
        window, store = self._window_and_store()
        asyncio.run(suggest._resolve_replies(_ReplyClient(store), None, window))
        tr = suggest.transcript_from(window, self.ME)
        self.assertIn(self.MAPS, tr)                 # гео-ссылка извлечена
        self.assertIn(self.PHONE, tr)                # телефон извлечён
        self.assertIn("паспорт", tr.lower())         # фото → пометка про паспорт
        # всё привязано к трём клиентским строкам-«Вот» (не потеряно в медиа-заглушке):
        embedded = [ln for ln in tr.splitlines()
                    if ln.startswith("[клиент]:") and "↩[в ответ на" in ln]
        self.assertEqual(len(embedded), 3)

    def test_reply_target_within_window(self):
        # цель reply лежит В окне — дозапрос не нужен, контент всё равно подтянут
        window = [
            _ReplyMsg(31, 999, message="Вот", reply_to=30),
            _ReplyMsg(30, 999, message=f"адрес {self.MAPS}"),
        ]
        asyncio.run(suggest._resolve_replies(_ReplyClient({}, fail=True), None, window))
        tr = suggest.transcript_from(window, self.ME)
        self.assertIn(self.MAPS, tr)

    def test_reply_resolve_failsafe(self):
        # дозапрос целей падает → без контекста reply, но черновик не рушится
        window, store = self._window_and_store()
        asyncio.run(suggest._resolve_replies(_ReplyClient(store, fail=True), None, window))
        tr = suggest.transcript_from(window, self.ME)   # не бросает
        self.assertNotIn("↩[в ответ на", tr)
        self.assertIn("Вот", tr)

    def test_non_reply_unaffected(self):
        window = [_ReplyMsg(41, 999, message="Привет, сколько стоит NMAX?")]
        asyncio.run(suggest._resolve_replies(_ReplyClient({}), None, window))
        tr = suggest.transcript_from(window, self.ME)
        self.assertEqual(tr, "[клиент]: Привет, сколько стоит NMAX?")


class TestCollectedTracker(unittest.TestCase):
    """§243/6: трекер собранного по окну диалога + reply-вложениям. Голден 12.07: три «Вот»
    реплаями на гео/фото-паспорт/телефон → собрано {geo,passport,phone}, черновик не переспрашивает,
    в хвосте пометка «собрано: гео ✅ паспорт ✅ тел ✅»."""

    MAPS = "https://maps.app.goo.gl/abc123XYZ"
    PHONE = "+66 81 234 5678"
    ME = 42

    def _three_vot_transcript(self):
        """Тот же голден-кейс, что TestReplyContext: три «Вот» реплаями подтянули данные СТАРОЙ брони."""
        store = {
            10: _ReplyMsg(10, 999, message=f"Моя вилла: {self.MAPS}"),
            11: _ReplyMsg(11, 999, photo=True),                # фото паспорта
            12: _ReplyMsg(12, 999, message=f"Мой номер {self.PHONE}"),
        }
        window = [
            _ReplyMsg(23, 999, message="Вот", reply_to=12),
            _ReplyMsg(22, 999, message="Вот", reply_to=11),
            _ReplyMsg(21, 999, message="Вот", reply_to=10),
            _ReplyMsg(20, self.ME, message="Скиньте гео, паспорт и телефон"),
        ]
        asyncio.run(suggest._resolve_replies(_ReplyClient(store), None, window))
        return suggest.transcript_from(window, self.ME)

    def test_golden_three_vot_collects_geo_passport_phone(self):
        tr = self._three_vot_transcript()
        facts = suggest.collected_facts(tr)
        self.assertTrue(facts["geo"], f"гео (Maps) не собрано:\n{tr}")
        self.assertTrue(facts["passport"], f"паспорт (фото) не собран:\n{tr}")
        self.assertTrue(facts["phone"], f"телефон не собран:\n{tr}")

    def test_lesson292_geo_only_confirms_location_not_details(self):
        """Урок №292 (родитель 152) + дефект #303/2 (лексика «факты ≠ артефакты»): клиент прислал
        ТОЛЬКО локацию (пин) — пример-подсказка подтверждает РОВНО локацию, но словом «учли» (локация
        = ИНФОРМАЦИЯ, не вложение), а НЕ «получили»; и НЕ хвалится «данные» (паспорта/тел/оплаты не
        было). Голден-фраза из живого окна (правило-класс CLAUDE.md): один пин виллы."""
        loc_only = f"[клиент]: Привет! Вот моя вилла: {self.MAPS}"
        facts = suggest.collected_facts(loc_only)
        self.assertTrue(facts["geo"])
        self.assertFalse(any(facts[k] for k in facts if k != "geo"), facts)
        note = suggest.collected_prompt_note(facts, "ru")
        self.assertIn("локацию учли", note)             # локация = информация → «учли», не «получили»
        self.assertNotIn("локацию получил", note)        # #303/2: про информацию «получили» НЕ пишем
        self.assertNotIn("данные получил", note)         # и НЕ приписывает несуществующие «данные»
        self.assertIn("подтверждай ТОЛЬКО перечисленное", note)  # явный запрет-приписка в промпте
        # реальное ВЛОЖЕНИЕ (фото паспорта) → «получили» законно; локация остаётся «учли»
        facts_more = dict(facts, passport=True)
        note_more = suggest.collected_prompt_note(facts_more, "ru")
        self.assertIn("фото получили", note_more)        # фото = вложение → «получили»
        self.assertIn("остальное учли", note_more)       # локация (инфо) — «учли», не «получили»

    def test_lesson292_step3_geo_only_verbatim_cryptopeppa_303(self):
        """Урок №292, шаг 3/6 (родитель 152): ДОСЛОВНАЯ гео-only фраза из ЖИВОГО окна @cryptopeppa
        (client_id=529849022, черновик #303). Клиент прислал ТОЛЬКО пин-локацию — короткую ссылку
        Google Maps (снята дословно из userbot-лога окна #303, сообщение от 2026-07-15). Живой провал:
        бот ответил «Локацию и данные получил», хотя паспорт/тел/оплату клиент НЕ присылал. Дефект
        #303/2 (лексика «факты ≠ артефакты»): локация — ИНФОРМАЦИЯ, её подтверждаем «учли», а «получили»
        оставляем реальным вложениям (фото/оплата). Golden-правило CLAUDE.md: дословная фраза клиента —
        на один лишь пин черновик подтверждает РОВНО локацию словом «учли», без «получили»/«данные»."""
        LIVE_PIN = "https://maps.app.goo.gl/c4G4B3sNrfJZBSue6?g_st=ac"   # дословно из окна #303
        facts = suggest.collected_facts(f"[клиент]: {LIVE_PIN}")
        self.assertTrue(facts["geo"], f"живой пин не собран как гео:\n{LIVE_PIN}")
        self.assertFalse(any(facts[k] for k in facts if k != "geo"), facts)  # ровно локация, «данных» нет
        note = suggest.collected_prompt_note(facts, "ru")
        self.assertIn("локацию учли", note)             # локация = информация → «учли»
        self.assertNotIn("локацию получил", note)        # #303/2: про информацию «получили» НЕ пишем
        self.assertNotIn("данные получил", note)         # и НЕ приписывает несуществующие «данные»
        # черновик по этому окну: LLM следует подсказке промпта → тело подтверждает локацию словом «учли»
        seen = {}

        def cap(system, user):
            seen["system"] = system
            return "Локацию учли, спасибо 🤝 Уточню детали по вашему адресу."

        d = suggest.generate_draft(f"[клиент]: {LIVE_PIN}", "ru", "FAQ", call_llm=cap)
        self.assertIn("локацию учли", seen["system"])        # промпт велит подтвердить локацию словом «учли»
        self.assertNotIn("локацию получил", seen["system"])  # и НЕ моделирует «получили» для информации
        self.assertNotIn("данные получил", seen["system"])   # и не инструктирует ложную квитанцию
        self.assertIn("ЛЕКСИКА КВИТАНЦИИ", seen["system"])   # #303/2: правило-лексика в промпте
        self.assertIn("[собрано: гео ✅]", d)                 # служебная пометка: собрана ровно гео
        client = suggest.client_facing_text(d)
        self.assertIn("Локацию учли", client)                # локацию подтверждает словом «учли»
        self.assertNotIn("получил", client.lower())          # но не «получил» (это про вложения)

    def test_golden_prompt_says_no_reask_and_confirm(self):
        tr = self._three_vot_transcript()
        facts = suggest.collected_facts(tr)
        p = suggest.make_system_prompt("FAQ", "ru", collected=facts)
        self.assertIn("УЖЕ ПОЛУЧЕНО", p)
        self.assertIn("НЕ переспрашивай", p)
        # three_vot = гео+паспорт(фото)+телефон: фото = вложение → «получили», локация/тел (инфо) → «учли»
        self.assertIn("фото получили", p)              # инструктируем фразу-подтверждение (вложение)
        self.assertIn("остальное учли", p)             # информацию — «учли», не «получили» (#303/2)
        for lbl in ("локация", "фото паспорта", "телефон"):
            self.assertIn(lbl, p)

    def test_golden_draft_has_manager_note_and_no_reask(self):
        tr = self._three_vot_transcript()
        # fake-LLM «отвечает как модель по инструкции»: подтверждает, не переспрашивает
        seen = {}

        def capture(system, user):
            seen["system"] = system
            return "Локацию и данные получил, спасибо! Осталось подобрать даты."

        d = suggest.generate_draft(tr, "ru", "FAQ", call_llm=capture)
        self.assertIn("[собрано: гео ✅ паспорт ✅ тел ✅]", d)   # СЛУЖЕБНАЯ пометка модератору в хвосте
        self.assertIn("УЖЕ ПОЛУЧЕНО", seen["system"])           # промпт нёс собранное
        # тело подтверждения не потеряно, пометка — отдельной хвостовой строкой
        self.assertIn("Локацию и данные получил", d)
        self.assertTrue(d.rstrip().endswith("[собрано: гео ✅ паспорт ✅ тел ✅]"))
        # шаг 4/7 #253: клиентский текст (тело ответа) СЛУЖЕБНОЙ пометки НЕ содержит
        client = suggest.client_facing_text(d)
        self.assertNotIn("собрано:", client)
        self.assertIn("Локацию и данные получил", client)

    def test_nothing_collected_leaves_draft_byte_for_byte(self):
        # чистый первый вопрос без данных → фактов нет → черновик и промпт без блока/пометки
        tr = "[клиент]: Здравствуйте! А что у вас есть?"
        facts = suggest.collected_facts(tr)
        self.assertFalse(any(facts.values()), facts)
        d = suggest.generate_draft(tr, "ru", "FAQ", call_llm=lambda s, u: "Здравствуйте! ...")
        self.assertNotIn("собрано:", d)
        self.assertNotIn("УЖЕ ПОЛУЧЕНО", suggest.make_system_prompt("FAQ", "ru", collected=facts))

    def test_model_and_dates_from_hints(self):
        tr = "[клиент]: NMAX с 7 по 14 июля"
        facts = suggest.collected_facts(tr)
        self.assertTrue(facts["model"])
        self.assertTrue(facts["term"])    # диапазон задаёт длительность
        self.assertTrue(facts["dates"])   # и конкретный старт
        self.assertFalse(facts["geo"])

    def test_term_without_start_is_not_dates(self):
        # §243 микро-фикс: одна ДЛИТЕЛЬНОСТЬ (без числа старта) → срок ✅, даты ❌.
        # Живой провал: «на 10 дней» помечался «даты ✅» без даты старта. + парафразы RU.
        for phr in ("на 10 дней", "на неделю", "на 2 недели", "на месяц", "на 5 дней"):
            f = suggest.collected_facts(f"[клиент]: {phr}")
            self.assertTrue(f["term"], f"срок не пойман: {phr}")
            self.assertFalse(f["dates"], f"даты ложно пойманы без старта: {phr}")

    def test_concrete_start_plus_term_sets_both(self):
        # старт (число+месяц / dd.mm / «с завтрашнего») + длительность/диапазон → срок ✅ и даты ✅.
        for phr in ("с 15 июля на 10 дней", "с 15.07 на 10 дней", "с завтрашнего на неделю",
                    "с 7 по 14 июля", "10.07-15.07", "с 3 августа на 5 дней"):
            f = suggest.collected_facts(f"[клиент]: {phr}")
            self.assertTrue(f["dates"], f"конкретный старт не пойман: {phr}")
            self.assertTrue(f["term"], f"срок не пойман: {phr}")

    def test_start_only_is_dates_not_term(self):
        # только старт, длительности нет → даты ✅, срок ❌.
        for phr in ("приеду 15 июля", "старт 15.07", "прилетаю завтра"):
            f = suggest.collected_facts(f"[клиент]: {phr}")
            self.assertTrue(f["dates"], f"старт не пойман: {phr}")
            self.assertFalse(f["term"], f"срок ложно пойман без длительности: {phr}")

    def test_manager_note_splits_term_and_dates(self):
        # пометка модератору различает срок/даты по обоим голденам.
        self.assertEqual(
            suggest.collected_manager_note(suggest.collected_facts("[клиент]: на 10 дней")),
            "[собрано: срок ✅]")
        self.assertEqual(
            suggest.collected_manager_note(suggest.collected_facts("[клиент]: с 15 июля на 10 дней")),
            "[собрано: срок ✅ даты ✅]")

    def test_payment_detected(self):
        self.assertTrue(suggest.collected_facts("[клиент]: Я уже оплатил депозит")["payment"])
        self.assertTrue(suggest.collected_facts("[клиент]: Перевёл предоплату, вот чек")["payment"])
        self.assertFalse(suggest.collected_facts("[клиент]: Сколько стоит аренда?")["payment"])

    def test_manager_note_en(self):
        facts = {"geo": True, "phone": True, "passport": False}
        self.assertEqual(suggest.collected_manager_note(facts, "en"), "[collected: geo ✅ phone ✅]")

    def test_phone_not_confused_with_dates(self):
        # «с 7 по 14» — это даты, НЕ телефон (короткие цифры)
        self.assertFalse(suggest.collected_facts("[клиент]: с 7 по 14 июля")["phone"])


class TestFilterBookingQuestions(unittest.TestCase):
    """Шаг 2/5 (родитель #241): filter_booking_questions вычёркивает из списка вопросов оформления
    брони пункты, данные по которым клиент УЖЕ дал в окне (по collected_facts). Голдены — реальные
    формулировки вопросов оформления (отель/адрес, даты, модель, паспорт, телефон, оплата)."""

    def test_geo_question_dropped_when_location_collected(self):
        qs = ["Как называется ваш отель / адрес проживания?",
              "На какие даты нужен байк?",
              "Какая модель вас интересует?"]
        facts = {"geo": True}
        kept = suggest.filter_booking_questions(qs, facts)
        self.assertNotIn(qs[0], kept)                 # гео уже дано → вопрос про отель/адрес вычеркнут
        self.assertEqual(kept, [qs[1], qs[2]])        # остальные (даты/модель не собраны) остаются, порядок цел

    def test_multiple_collected_fields_all_dropped(self):
        qs = ["Какую модель хотите арендовать?",
              "На сколько дней аренда?",
              "С какого числа вам нужен байк?",
              "Куда доставить — ваш адрес/отель?",
              "Пришлите, пожалуйста, фото паспорта",
              "Ваш номер телефона для связи?",
              "Каким способом удобно внести оплату?"]
        facts = {"model": True, "term": True, "dates": True, "geo": True,
                 "passport": True, "phone": True, "payment": True}
        self.assertEqual(suggest.filter_booking_questions(qs, facts), [])  # всё собрано → список пуст

    def test_english_questions_match_english_fields(self):
        qs = ["What is the name of your apartment / hotel address?",
              "Which model would you like?",
              "How many days do you need the bike for?"]
        facts = {"geo": True}
        kept = suggest.filter_booking_questions(qs, facts, "en")
        self.assertNotIn(qs[0], kept)                 # EN гео-вопрос («name of your apartment») вычеркнут
        self.assertEqual(kept, [qs[1], qs[2]])

    def test_compound_question_kept_when_one_field_unknown(self):
        # составной вопрос про гео (собрано) И депозит/оплату (НЕ собрано) — НЕ теряем: реальный вопрос остаётся
        qs = ["Подскажите ваш адрес и каким способом будете вносить оплату?"]
        facts = {"geo": True}
        self.assertEqual(suggest.filter_booking_questions(qs, facts), qs)

    def test_no_facts_returns_all(self):
        qs = ["Какая модель?", "Какие даты?"]
        self.assertEqual(suggest.filter_booking_questions(qs, {}), qs)          # ничего не собрано → все вопросы
        self.assertEqual(suggest.filter_booking_questions(qs, None), qs)

    def test_unmatched_question_is_kept(self):
        # вопрос, не сопоставимый ни с одним полем, — оставляем (не знаем, о чём он)
        qs = ["Планируете ли вы шлем для пассажира?"]
        facts = {"geo": True, "model": True}
        self.assertEqual(suggest.filter_booking_questions(qs, facts), qs)

    def test_dates_question_dropped_but_model_question_kept(self):
        qs = ["На какие даты бронируем?", "Какой байк выбираете?"]
        facts = {"dates": True}                       # даты есть, модель — нет
        kept = suggest.filter_booking_questions(qs, facts)
        self.assertEqual(kept, [qs[1]])

    def test_filtered_item_is_logged(self):
        qs = ["Как называется ваш отель?", "Какие даты?"]
        with self.assertLogs("suggest", level="INFO") as cm:
            suggest.filter_booking_questions(qs, {"geo": True})
        joined = "\n".join(cm.output)
        self.assertIn("вычеркнут вопрос", joined)     # факт вычёркивания залогирован
        self.assertIn("отель", joined)                # с текстом самого пункта
        self.assertIn("локация", joined)              # и с меткой собранного поля

    def test_empty_input_returns_empty(self):
        self.assertEqual(suggest.filter_booking_questions([], {"geo": True}), [])
        self.assertEqual(suggest.filter_booking_questions(None, {"geo": True}), [])


class TestFilterBookingQuestionsLiveWindow766498048(unittest.TestCase):
    """Шаг 3/5 (родитель #241): живой кейс окна client_id=766498048 сквозь ДЕТЕКТ (не facts-заглушку).
    Клиент ПЕРВЫМ сообщением заполнил поле-метку «Hotel Name: Cape Sienna Gourmet Hotel & Villas» →
    отель НАЗВАН. Правило-класс CLAUDE.md (golden-тесты детекта = ДОСЛОВНАЯ фраза клиента + mock=реальность):
    гоняем реальную фразу через collected_facts → filter_booking_questions, а не подставляем facts руками.
    Ожидание: вопрос «the name of your apartment/hotel» вычеркнут, незакрытые (даты, модель) остаются.
    Плюс контрольные кейсы «все поля даны» → пусто и «ничего не дано» → все вопросы остаются."""

    # ДОСЛОВНОЕ первое сообщение клиента из живого окна (родитель #241).
    LIVE_MSG = "[клиент]: Hotel Name: Cape Sienna Gourmet Hotel & Villas"
    # Реальный набор вопросов оформления брони (EN, как в окне: имя отеля + незакрытые даты/модель).
    QUESTIONS = [
        "Could you please tell me the name of your apartment/hotel?",
        "What dates would you like to rent the bike for?",
        "Which model are you interested in?",
    ]

    def test_hotel_name_given_hotel_question_dropped_dates_model_kept(self):
        # Сквозь детект: имя отеля названо → geo собран, срок/модель/даты НЕ собраны.
        facts = suggest.collected_facts(self.LIVE_MSG)
        self.assertTrue(facts["geo"], f"названный отель не распознан как локация:\n{self.LIVE_MSG}")
        self.assertFalse(facts["dates"], facts)
        self.assertFalse(facts["model"], facts)
        kept = suggest.filter_booking_questions(self.QUESTIONS, facts, "en")
        self.assertNotIn(self.QUESTIONS[0], kept)          # «name of your apartment/hotel» — вычеркнут
        self.assertEqual(kept, [self.QUESTIONS[1], self.QUESTIONS[2]])  # даты+модель остаются, порядок цел

    def test_all_fields_given_all_questions_dropped(self):
        # «Все поля даны»: имя отеля + модель + даты + срок + телефон + оплата + фото паспорта в окне.
        # Порядок как в живом окне (новейшее — последней строкой): деталь брони (модель+даты+срок)
        # идёт последней, поэтому extract_booking_hints берёт её как актуальную бронь.
        window = ("[клиент]: Hotel Name: Cape Sienna Gourmet Hotel & Villas\n"
                  "[клиент]: Мой номер +66 812345678\n"
                  "[клиент]: Уже оплатил депозит, вот чек\n"
                  "[клиент]: [фото] вероятно паспорт\n"
                  "[клиент]: Хочу Honda PCX на 10 дней с 25 июля")
        facts = suggest.collected_facts(window, today=datetime.date(2026, 7, 20))
        for k in ("geo", "model", "term", "dates", "phone", "payment", "passport"):
            self.assertTrue(facts[k], f"поле {k} не собрано в окне «все поля даны»:\n{facts}")
        qs = [
            "Could you please tell me the name of your apartment/hotel?",
            "Which model are you interested in?",
            "What dates would you like to rent for?",
            "How many days do you need the bike?",
            "Please send a photo of your passport",
            "What is your phone number?",
            "How would you like to pay the deposit?",
        ]
        self.assertEqual(suggest.filter_booking_questions(qs, facts, "en"), [])  # всё собрано → анкета пуста

    def test_nothing_given_all_questions_kept(self):
        # «Ничего не дано»: нейтральное приветствие без единого поля → ни один вопрос не вычёркиваем.
        facts = suggest.collected_facts("[клиент]: Hi! How does the rental work?")
        self.assertFalse(any(facts.values()), facts)       # ни одно поле не собрано
        self.assertEqual(suggest.filter_booking_questions(self.QUESTIONS, facts, "en"), self.QUESTIONS)


class TestDetectFirstMessageBooking(unittest.TestCase):
    """Родитель #271 шаг 2/5: детект котируемой брони на ПЕРВОМ сообщении — модель+старт+срок.
    Голдены из живого кейса 13.07 (класс «ж»: клиент дал всё сразу, надо котировать, а не анкету)
    + парафразы RU/EN (правило-класс CLAUDE.md: реальная фраза, не идеал) + негативы (нет одного
    из трёх полей → None). today фиксирован для детерминизма дат."""

    TODAY = datetime.date(2026, 7, 13)

    def _d(self, text):
        return suggest.detect_first_message_booking(text, today=self.TODAY)

    def test_live_case_xsr155(self):
        # дословно из живого провала 13.07: «xsr 155» + «с 15 июля» + «на 2 недели».
        r = self._d("Здравствуйте! Хочу xsr 155 с 15 июля на 2 недели")
        self.assertIsNotNone(r)
        self.assertEqual(r["model"], "XSR 155")
        self.assertEqual(r["startDate"], "2026-07-15")
        self.assertEqual(r["days"], 14)

    def test_model_case_and_space_insensitive(self):
        # «XSR155», «xsr  155», «Xsr 155» — все → 'XSR 155'.
        for m in ("XSR155", "xsr  155", "Xsr 155", "хочу XSR-155"):
            r = self._d(f"{m} с 15 июля на 2 недели")
            self.assertIsNotNone(r, f"модель не поймана: {m}")
            self.assertEqual(r["model"], "XSR 155", f"регистр/пробел сломали матч: {m}")

    def test_term_units_days_weeks_months(self):
        # срок в днях/неделях/месяцах → дни (единицы, что поддержаны _parse_term).
        cases = {"на 10 дней": 10, "на 3 недели": 21, "на неделю": 7, "на месяц": 30}
        for phr, days in cases.items():
            r = self._d(f"nmax 155 с 20 июля {phr}")
            self.assertIsNotNone(r, f"не собралось: {phr}")
            self.assertEqual(r["days"], days, f"срок неверен: {phr}")

    def test_start_variants(self):
        # разные формы старта: «с 15 июля», «с 15.07», «завтра», голое «с 20».
        for phr, iso in (("с 15 июля", "2026-07-15"), ("с 15.07", "2026-07-15"),
                         ("завтра", "2026-07-14"), ("с 20", "2026-07-20")):
            r = self._d(f"forza 300 {phr} на неделю")
            self.assertIsNotNone(r, f"старт не пойман: {phr}")
            self.assertEqual(r["startDate"], iso, f"дата старта неверна: {phr}")

    def test_none_when_no_model(self):
        # нет модели каталога (или голая серия «xsr» без объёма) → None.
        self.assertIsNone(self._d("Здравствуйте! с 15 июля на 2 недели"))
        self.assertIsNone(self._d("хочу xsr с 15 июля на 2 недели"))

    def test_none_when_no_start(self):
        # есть модель+срок, но нет даты старта → None (одна длительность не котируется).
        self.assertIsNone(self._d("xsr 155 на 2 недели"))

    def test_none_when_no_term(self):
        # есть модель+старт, но нет срока → None.
        self.assertIsNone(self._d("xsr 155 с 15 июля"))


class TestCollectedAttachmentOnly(unittest.TestCase):
    """Шаг 3/7 #253: сущность (гео/паспорт/тел/оплата) ✅ ТОЛЬКО по ФАКТУ вложения/данных в
    сообщении клиента; слово-упоминание — ВСЕГДА ❌. Голдены на каждую сущность (слово → ❌,
    вложение/номер/ссылка/скрин → ✅) + дословный диалог Ярославы 13.07 (паспорт/предоплата
    словами, вопрос про крипту, но НЕ фото и НЕ оплата) → паспорт ❌, оплата ❌."""

    MAPS = "https://maps.app.goo.gl/abc123XYZ"
    ME = 42

    # ---------- ГЕО: слово ❌, ссылка/координаты/пин ✅ ----------
    def test_geo_word_only_is_false(self):
        for phr in ("Живу на вилле в Патонге", "Апартаменты Karon Hill",
                    "Скину локацию позже", "My condo is near the beach", "отель Hilton"):
            self.assertFalse(suggest.collected_facts(f"[клиент]: {phr}")["geo"],
                             f"слово-упоминание жилья ложно = гео ✅: {phr}")

    def test_geo_link_or_coords_is_true(self):
        for phr in (f"Вот адрес {self.MAPS}", "geo: 7.89, 98.29",
                    "https://goo.gl/maps/xyz", "@7.8901,98.2999 моя вилла"):
            self.assertTrue(suggest.collected_facts(f"[клиент]: {phr}")["geo"],
                            f"реальная ссылка/координаты не = гео ✅: {phr}")

    def test_geo_location_pin_attachment_is_true(self):
        # прямой location-пин от клиента (медиа без текста) → «[локация]» → гео ✅
        tr = suggest.transcript_from([_ReplyMsg(50, 999, geo=True)], self.ME)
        self.assertTrue(suggest.collected_facts(tr)["geo"], f"пин-локация не собрана:\n{tr}")

    # ---------- ПАСПОРТ: слово ❌, фото ✅ ----------
    def test_passport_word_only_is_false(self):
        for phr in ("Паспорт с собой привезу", "Нужен ли паспорт для аренды?",
                    "Do you need my passport?", "У меня есть id card"):
            self.assertFalse(suggest.collected_facts(f"[клиент]: {phr}")["passport"],
                             f"слово «паспорт» ложно = паспорт ✅: {phr}")

    def test_passport_promise_to_send_is_false(self):
        # #242: ОБЕЩАНИЕ прислать фото/файл документа позже — намерение, не полученный документ → ❌.
        # Дословные живые формулировки RU/EN + парафразы (правило-класс CLAUDE.md: реальная фраза).
        for phr in ("Фото паспорта пришлю завтра", "Скину паспорт вечером",
                    "паспорт сфоткаю и отправлю позже", "документ вышлю как приеду",
                    "I'll send the passport photo tomorrow", "I'll send my passport later",
                    "will send passport photo in the evening"):
            self.assertFalse(suggest.collected_facts(f"[клиент]: {phr}")["passport"],
                             f"обещание прислать паспорт ложно = паспорт ✅: {phr}")

    def test_passport_direct_photo_is_true(self):
        # прямое фото от клиента (медиа без подписи) → «[фото]» → паспорт ✅
        tr = suggest.transcript_from([_ReplyMsg(51, 999, photo=True)], self.ME)
        self.assertTrue(suggest.collected_facts(tr)["passport"], f"прямое фото не = паспорт ✅:\n{tr}")

    def test_passport_reply_photo_is_true(self):
        # reply на фото из старой переписки → маркер «вероятно паспорт» → паспорт ✅
        store = {60: _ReplyMsg(60, 999, photo=True)}
        window = [_ReplyMsg(61, 999, message="Вот", reply_to=60)]
        asyncio.run(suggest._resolve_replies(_ReplyClient(store), None, window))
        tr = suggest.transcript_from(window, self.ME)
        self.assertTrue(suggest.collected_facts(tr)["passport"], f"reply-фото не = паспорт ✅:\n{tr}")

    # ---------- ТЕЛЕФОН: слово ❌, номер ✅ ----------
    def test_phone_word_only_is_false(self):
        for phr in ("Дам телефон позже", "Мой номер скину в вотсап",
                    "Call me on whatsapp", "телефон нужен?"):
            self.assertFalse(suggest.collected_facts(f"[клиент]: {phr}")["phone"],
                             f"слово «телефон/номер» без цифр ложно = тел ✅: {phr}")

    def test_phone_number_is_true(self):
        for phr in ("Мой номер +66 81 234 5678", "Телефон 89261234567", "whatsapp +79161112233"):
            self.assertTrue(suggest.collected_facts(f"[клиент]: {phr}")["phone"],
                            f"реальный номер не = тел ✅: {phr}")

    # ---------- ОПЛАТА: слово/вопрос ❌, подтверждённая/чек ✅ ----------
    def test_payment_word_or_question_is_false(self):
        # «криптой можно?» и назначение предоплаты — НЕ оплата (задача 3/7)
        for phr in ("Криптой можно?", "Можно оплатить криптой?", "Какая предоплата?",
                    "Нужна ли предоплата?", "Предоплата нужна чтобы зафиксировать байк?",
                    "А депозит это сколько?", "Как проходит оплата?",
                    "банковский перевод принимаете?"):
            self.assertFalse(suggest.collected_facts(f"[клиент]: {phr}")["payment"],
                             f"вопрос/назначение/слово ложно = оплата ✅: {phr}")

    def test_payment_confirmed_is_true(self):
        for phr in ("Я уже оплатил депозит", "Перевёл предоплату, вот чек",
                    "Оплату отправил, скрин перевода прикладываю", "Внёс предоплату",
                    "Already paid the deposit", "Payment sent"):
            self.assertTrue(suggest.collected_facts(f"[клиент]: {phr}")["payment"],
                            f"подтверждённая оплата не = оплата ✅: {phr}")

    # ---------- ДИАЛОГ ЯРОСЛАВЫ 13.07 → паспорт ❌, оплата ❌ ----------
    def test_yaroslava_1307_passport_and_payment_false(self):
        tr = (
            "[клиент]: Здравствуйте! Хочу арендовать NMAX с 15 июля на 10 дней\n"
            "[менеджер]: Здравствуйте! Депозит — деньги либо паспорт\n"
            "[клиент]: А паспорт обязательно оставлять? Можно копию?\n"
            "[клиент]: И какая предоплата нужна, чтобы забронировать?\n"
            "[клиент]: Криптой можно оплатить предоплату?"
        )
        facts = suggest.collected_facts(tr)
        self.assertFalse(facts["passport"], f"Ярослава: паспорт словом ложно ✅:\n{tr}")
        self.assertFalse(facts["payment"], f"Ярослава: предоплата/крипта-вопрос ложно ✅:\n{tr}")


class TestTrackerDocsPromiseNoPassportTick766498048(unittest.TestCase):
    """Шаг 2/5 (родитель #242) — класс «д» (документы обещаны, не присланы): живое окно
    client_id=766498048. Клиент документы В ОКНО НЕ прислал, лишь обещает «I'll send the necessary
    documents by tomorrow». Обещание = намерение, не полученный файл → трекер черновика обязан идти
    БЕЗ «✅» у пункта «паспорт» (правило §243/6: ✅ ставим на ФАКТ вложения, а не на слова о нём).
    Правило-класс CLAUDE.md: гоняем ДОСЛОВНУЮ фразу клиента сквозь ДЕТЕКТ (collected_facts →
    collected_manager_note/_append_collected_note), а не подставляем facts руками. В окне намеренно
    есть РЕАЛЬНО собранные гео (отель) и телефон — трекер непустой, и на его фоне отсутствие
    «паспорт ✅» — осмысленный голден, а не «просто ничего не собрано»."""

    # Дословная фраза-обещание документов из живого окна + реальные гео (имя отеля) и номер телефона.
    WINDOW = ("[клиент]: Hotel Name: Cape Sienna Gourmet Hotel & Villas\n"
              "[клиент]: My number +66 812345678\n"
              "[клиент]: I'll send the necessary documents by tomorrow")

    # RU-двойник того же класса «д»: клиент документ НЕ прислал, обещает «пришлю завтра».
    # Правило-класс CLAUDE.md: дословная живая формулировка + гео/тел собраны реально → трекер непустой.
    WINDOW_RU = ("[клиент]: Название отеля: Cape Sienna Gourmet Hotel & Villas\n"
                 "[клиент]: Мой номер +66 812345678\n"
                 "[клиент]: Документы пришлю завтра")

    ME = 42

    def test_docs_promise_passport_not_collected(self):
        # Сквозь детект: гео и телефон РЕАЛЬНО собраны, паспорт — только обещан → ❌.
        facts = suggest.collected_facts(self.WINDOW)
        self.assertTrue(facts["geo"], f"названный отель не = гео ✅:\n{self.WINDOW}")
        self.assertTrue(facts["phone"], f"реальный номер не = тел ✅:\n{self.WINDOW}")
        self.assertFalse(facts["passport"],
                         f"обещание прислать документы ложно = паспорт ✅:\n{self.WINDOW}")

    def test_tracker_note_has_no_passport_tick(self):
        # Трекер черновика (служебная пометка модератору): гео ✅ и тел ✅ есть, паспорт ✅ — НЕТ.
        facts = suggest.collected_facts(self.WINDOW)
        for lang, geo_lbl, phone_lbl, pass_lbl in (
                ("en", "geo ✅", "phone ✅", "passport ✅"),
                ("ru", "гео ✅", "тел ✅", "паспорт ✅")):
            note = suggest.collected_manager_note(facts, lang)
            self.assertIn(geo_lbl, note, note)
            self.assertIn(phone_lbl, note, note)
            self.assertNotIn(pass_lbl, note,
                             f"трекер {lang} содержит «паспорт ✅» на обещание документов: {note}")

    def test_draft_tail_tracker_has_no_passport_tick(self):
        # В хвосте черновика тот же трекер: пункт «паспорт» БЕЗ ✅ (пометка целиком без «passport ✅»).
        facts = suggest.collected_facts(self.WINDOW)
        draft = suggest._append_collected_note("Sure! We have a PCX available for you.", facts, "en")
        self.assertIn("[collected: geo ✅ phone ✅]", draft, draft)
        self.assertNotIn("passport ✅", draft,
                         f"хвост черновика содержит «passport ✅» на обещание документов:\n{draft}")

    def test_ru_promise_tomorrow_no_passport_tick(self):
        # НОВЫЙ кейс «обещал прислать завтра → паспорт без ✅»: RU-двойник живого окна.
        # «Документы пришлю завтра» = обещание, не файл → гео ✅ и тел ✅ есть, паспорт ✅ — НЕТ.
        facts = suggest.collected_facts(self.WINDOW_RU)
        self.assertTrue(facts["geo"], f"названный отель не = гео ✅:\n{self.WINDOW_RU}")
        self.assertTrue(facts["phone"], f"реальный номер не = тел ✅:\n{self.WINDOW_RU}")
        self.assertFalse(facts["passport"],
                         f"обещание «пришлю завтра» ложно = паспорт ✅:\n{self.WINDOW_RU}")
        note = suggest.collected_manager_note(facts, "ru")
        self.assertIn("гео ✅", note, note)
        self.assertIn("тел ✅", note, note)
        self.assertNotIn("паспорт ✅", note,
                         f"трекер содержит «паспорт ✅» на обещание «пришлю завтра»: {note}")

    def test_tracker_passport_tick_when_document_really_sent(self):
        # ПАРНЫЙ позитив (регрессия «не сломать»): то же окно, но документ РЕАЛЬНО прислан —
        # клиент вкладывает фото паспорта (не обещание) → в трекере «passport ✅» СТОИТ.
        # msgs newest-first для transcript_from; фото без подписи → «[фото]» → паспорт ✅.
        window = [
            _ReplyMsg(3, 999, photo=True),                                  # реальное фото документа
            _ReplyMsg(2, 999, message="My number +66 812345678"),
            _ReplyMsg(1, 999, message="Hotel Name: Cape Sienna Gourmet Hotel & Villas"),
        ]
        tr = suggest.transcript_from(window, self.ME)
        facts = suggest.collected_facts(tr)
        self.assertTrue(facts["passport"], f"реальное фото документа не = паспорт ✅:\n{tr}")
        note = suggest.collected_manager_note(facts, "en")
        self.assertIn("passport ✅", note,
                      f"трекер без «passport ✅» при реально присланном документе: {note}")
        draft = suggest._append_collected_note("Sure! We have a PCX available for you.", facts, "en")
        self.assertIn("passport ✅", draft,
                      f"хвост черновика без «passport ✅» при реально присланном документе:\n{draft}")


class _ModBase(unittest.TestCase):
    """Общий каркас: SUGGEST включён, tmp-хранилища, восстановление глобалей."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
                      suggest.APPROVER_USERNAMES, suggest.pending, suggest.PAIRS_FILE)
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = True         # безопасно по умолчанию: клиенту не уходит
        suggest.APPROVER_USERNAMES = set()        # пустой whitelist
        suggest.reset_disabled()
        suggest.limiter = suggest.RateLimiter(6, 15)
        suggest.pending = suggest.PendingStore(os.path.join(self._tmp.name, "p.jsonl"))
        suggest.PAIRS_FILE = os.path.join(self._tmp.name, "pairs.jsonl")

    def tearDown(self):
        (suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
         suggest.APPROVER_USERNAMES, suggest.pending, suggest.PAIRS_FILE) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def _add_pending(self, mid=555, draft="D"):
        suggest.pending.add(mid, {
            "client_id": 999, "client_ref": "@c", "lang": "ru",
            "incoming": "x", "draft": draft, "first_contact": True, "ts": "t",
        })
        return mid


class TestApproverWhitelist(_ModBase):

    def test_in_whitelist_passes(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        mid = self._add_pending()
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text="+", sender=FakeSender(1, username="Danya"), chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")
        self.assertIsNone(suggest.pending.get(mid))       # решение принято, pending снят

    def test_not_in_whitelist_denied(self):
        suggest.APPROVER_USERNAMES = {"danya"}
        mid = self._add_pending()
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text="+", sender=FakeSender(2, username="stranger"), chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "denied")
        self.assertEqual(res["reason"], "not_approver")
        self.assertTrue(any("⛔" in t[1] for t in c.sent if t[0] == -100))  # видимый ⛔
        self.assertEqual([t for t in c.sent if t[0] == 999], [])            # клиенту ничего
        self.assertIsNotNone(suggest.pending.get(mid))     # pending НЕ снят — approver ещё решит

    def test_empty_whitelist_allows_any(self):
        suggest.APPROVER_USERNAMES = set()
        mid = self._add_pending()
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text="+", sender=FakeSender(3, username="whoever"), chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        self.assertEqual(res["action"], "approve")


class TestModerationAcks(_ModBase):
    """NO-SILENT-PATHS: каждая ветка reply даёт видимый ack в группу; safety цел."""

    def _run(self, text, test_mode):
        suggest.SUGGEST_TEST_MODE = test_mode
        mid = self._add_pending(mid=556, draft="D")
        c = FakeClient()
        ev = FakeEvent(c, reply_to=mid, text=text, chat_id=-100)
        res = asyncio.run(suggest.on_moderation_reply(ev, jitter=lambda: 0, sleep=_nosleep))
        acks = [t[1] for t in c.sent if t[0] == -100]
        client_sends = [t for t in c.sent if t[0] == 999]
        return res, acks, client_sends

    def test_reject_ack(self):
        res, acks, cs = self._run("-", True)
        self.assertTrue(any("❌ Отклонено" in a for a in acks))
        self.assertEqual(cs, [])

    def test_approve_testmode_ack_no_client_send(self):
        res, acks, cs = self._run("+", True)                 # SAFETY
        self.assertTrue(any("TEST_MODE" in a for a in acks))
        self.assertEqual(cs, [])                             # клиенту НИЧЕГО в TEST_MODE

    def test_edit_testmode_ack_no_client_send(self):
        res, acks, cs = self._run("Здравствуйте, вот цена", True)
        self.assertTrue(any("Правка принята" in a for a in acks))
        self.assertEqual(cs, [])

    def test_approve_live_ack_and_sends(self):
        res, acks, cs = self._run("+", False)
        self.assertTrue(any("Отправлено клиенту" in a for a in acks))
        self.assertIn((999, "D"), cs)                        # live: клиенту ушло

    def test_every_branch_gets_ack(self):
        for text in ("+", "-", "правка текстом"):
            _, acks, _ = self._run(text, True)
            self.assertTrue(acks, f"reply «{text}» остался без ack")


class TestCliLlm(unittest.TestCase):
    """Генерация через claude CLI (подписка Max). Всё замокано — реальный claude не дёргаем."""

    def setUp(self):
        self._api = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-SECRET-must-not-leak"   # должен быть вычищен из env CLI

    def tearDown(self):
        if self._api is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._api

    def test_cli_success_returns_stdout(self):
        captured = {}

        class P:
            returncode = 0
            stdout = _cli_json(result="  Здравствуйте! NMAX свободен.\nRESULT: ок  ")
            stderr = ""

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["cwd"] = kw.get("cwd")
            captured["env"] = kw.get("env")
            captured["timeout"] = kw.get("timeout")
            return P()
        with mock.patch.object(suggest, "SUGGEST_MODEL", "fable"), \
             mock.patch.object(suggest, "SUGGEST_MODEL_FALLBACK", "sonnet"), \
             mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", side_effect=fake_run):
            out = suggest._cli_llm("SYS", "USER")
        self.assertEqual(out, "Здравствуйте! NMAX свободен.\nRESULT: ок")   # поле result из JSON, .strip()
        # ЧИСТЫЙ генератор: --allowed-tools '' + нейтральный cwd (НЕ репо) + модель/промпты на месте
        self.assertIn("--allowed-tools", captured["cmd"])
        i = captured["cmd"].index("--allowed-tools")
        self.assertEqual(captured["cmd"][i + 1], "")
        self.assertIn("--system-prompt", captured["cmd"])
        self.assertIn("SYS", captured["cmd"])
        self.assertIn("USER", captured["cmd"])
        # кондуктор: основная модель fable + фолбэк sonnet + JSON-выхлоп (одним вызовом CLI)
        self.assertEqual(captured["cmd"][captured["cmd"].index("--model") + 1], "fable")
        self.assertEqual(captured["cmd"][captured["cmd"].index("--fallback-model") + 1], "sonnet")
        self.assertEqual(captured["cmd"][captured["cmd"].index("--output-format") + 1], "json")
        self.assertNotEqual(os.path.normcase(captured["cwd"] or ""), os.path.normcase(suggest.BASE_DIR))

    def test_cli_reports_fallback_model_from_modelusage(self):
        # кондуктор: основная модель недоступна → CLI сам добил фолбэком; реально отработавшую голову
        # (sonnet, БОЛЬШОЙ inputTokens) достаём из modelUsage, а не служебный haiku (крошечный вход).
        text, real = suggest._parse_cli_json(
            _cli_json(result="OK", main="claude-sonnet-5", main_in=3077, main_out=4))
        self.assertEqual(text, "OK")
        self.assertEqual(real, "claude-sonnet-5")   # НЕ haiku, хотя у него output больше (12 > 4)

    def test_cli_parse_is_error_returns_empty(self):
        text, real = suggest._parse_cli_json(_cli_json(result="что-то", is_error=True))
        self.assertEqual(text, "")                   # is_error → пустой текст (upstream пропустит)

    def test_cli_parse_broken_json_returns_empty(self):
        self.assertEqual(suggest._parse_cli_json("не json"), ("", ""))

    def test_cli_env_has_no_api_key(self):
        def fake_run(cmd, **kw):
            self.assertIsNotNone(kw.get("env"))
            self.assertNotIn("ANTHROPIC_API_KEY", kw["env"])   # ключ НЕ утёк → CLI по подписке
            self.assertNotIn("OPENAI_API_KEY", kw["env"])

            class P:
                returncode = 0
                stdout = _cli_json(result="ok")
                stderr = ""
            return P()
        with mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", side_effect=fake_run):
            self.assertEqual(suggest._cli_llm("S", "U"), "ok")

    def test_cli_timeout_returns_empty(self):
        def fake_run(cmd, **kw):
            raise suggest.subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        with mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", side_effect=fake_run):
            self.assertEqual(suggest._cli_llm("S", "U"), "")   # таймаут → пусто (upstream пропустит)

    def test_cli_nonzero_returns_empty(self):
        class P:
            returncode = 1
            stdout = ""
            stderr = "boom"
        with mock.patch.object(suggest, "_resolve_claude", return_value=r"C:\claude\claude.exe"), \
             mock.patch.object(suggest.subprocess, "run", return_value=P()):
            self.assertEqual(suggest._cli_llm("S", "U"), "")

    def test_cli_not_found_raises(self):
        with mock.patch.object(suggest, "_resolve_claude", return_value=None):
            with self.assertRaises(RuntimeError):
                suggest._cli_llm("S", "U")

    def test_generate_draft_uses_cli_when_flag_on(self):
        calls = {"cli": 0, "api": 0}
        with mock.patch.object(suggest, "SUGGEST_LLM_VIA_CLI", True), \
             mock.patch.object(suggest, "_cli_llm", lambda s, u: calls.__setitem__("cli", calls["cli"] + 1) or "черновик"), \
             mock.patch.object(suggest, "_default_llm", lambda s, u: calls.__setitem__("api", 1) or "нет"):
            d = suggest.generate_draft("[клиент]: привет", "ru", "FAQ")
        self.assertEqual(d, "черновик")
        self.assertEqual((calls["cli"], calls["api"]), (1, 0))   # флаг ON → CLI, платный API не тронут

    def test_injected_call_llm_wins_over_flag(self):
        # мок-call_llm инъектируется поверх флага — прежние тесты не ломаются
        with mock.patch.object(suggest, "SUGGEST_LLM_VIA_CLI", True):
            d = suggest.generate_draft("[клиент]: привет", "ru", "FAQ", call_llm=_fake_llm)
        self.assertTrue(d)

    # --- ретрай резолва: транзиентный isfile==False не убивает черновик с первой осечки ---
    def test_resolve_retries_then_succeeds(self):
        seq = [None, r"C:\claude\claude.exe"]     # первый проход пусто, второй — нашли
        with mock.patch.object(suggest, "_resolve_claude_once", side_effect=seq), \
             mock.patch.object(suggest.time, "sleep") as slp:
            self.assertEqual(suggest._resolve_claude(retries=1, retry_sleep=2.0), r"C:\claude\claude.exe")
            slp.assert_called_once()               # была пауза перед второй попыткой

    def test_resolve_exhausted_returns_none(self):
        with mock.patch.object(suggest, "_resolve_claude_once", return_value=None), \
             mock.patch.object(suggest.time, "sleep"):
            self.assertIsNone(suggest._resolve_claude(retries=1, retry_sleep=0))

    def test_cli_llm_recovers_via_retry(self):
        # первая осечка isfile → вторая удача: _cli_llm НЕ падает, зовёт CLI (кейс теста 00:33)
        class P:
            returncode = 0
            stdout = _cli_json(result="Здравствуйте! NMAX свободен.")
            stderr = ""
        with mock.patch.object(suggest, "_resolve_claude_once", side_effect=[None, r"C:\claude\claude.exe"]), \
             mock.patch.object(suggest.time, "sleep"), \
             mock.patch.object(suggest.subprocess, "run", return_value=P()):
            self.assertEqual(suggest._cli_llm("S", "U"), "Здравствуйте! NMAX свободен.")

    # --- резолв из .env-файла: спасает сервис-контекст (%APPDATA%=systemprofile) ---
    def test_resolve_reads_claude_bin_from_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8") as f:
                f.write("BRIDGE_TOKEN=secret-не-читаем\nCLAUDE_BIN=" + exe + "\n")
            with mock.patch.object(suggest, "REPO_ENV", envf), \
                 mock.patch.object(suggest, "CLAUDE_BIN", "claude"), \
                 mock.patch.object(suggest.shutil, "which", return_value=None), \
                 mock.patch.object(suggest, "_claude_base_dirs", return_value=[os.path.join(d, "nope")]):
                self.assertEqual(suggest._resolve_claude_once(), exe)          # взято ИЗ .env-файла
                self.assertEqual(suggest._resolve_claude(retry_sleep=0), exe)  # и полный резолв тоже

    def test_env_file_bin_ignores_missing_and_comments(self):
        with tempfile.TemporaryDirectory() as d:
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8") as f:
                f.write("# CLAUDE_BIN=C:\\commented\\claude.exe\nCLAUDE_BIN=C:\\nope\\claude.exe\n")
            with mock.patch.object(suggest, "REPO_ENV", envf):
                self.assertEqual(suggest._env_file_claude_bin(), "")           # путь не существует → ''

    def test_env_file_bin_handles_utf8_bom_first_line(self):
        # PowerShell 5.1 `Set-Content -Encoding utf8` пишет BOM; CLAUDE_BIN первой строкой не должен теряться
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8-sig") as f:                   # ← с BOM
                f.write("CLAUDE_BIN=" + exe + "\nBRIDGE_TOKEN=x\n")
            with mock.patch.object(suggest, "REPO_ENV", envf):
                self.assertEqual(suggest._env_file_claude_bin(), exe)

    def test_env_file_bin_survives_stray_nonutf8_on_other_line(self):
        # одинокий не-utf8 байт на ЧУЖОЙ строке не должен уронить парс до валидной строки CLAUDE_BIN
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "wb") as f:
                f.write(b"SOMEKEY=\xff\xfe bad\r\nCLAUDE_BIN=" + exe.encode("utf-8") + b"\r\n")
            with mock.patch.object(suggest, "REPO_ENV", envf):
                self.assertEqual(suggest._env_file_claude_bin(), exe)

    def test_msix_package_path_found_when_roaming_virtual(self):
        # Store/MSIX: Roaming\Claude невидим процессам Планировщика; реальный бинарь в
        # AppData\Local\Packages\Claude_*\LocalCache\... — резолвер обязан его найти.
        with tempfile.TemporaryDirectory() as d:
            local = os.path.join(d, "Local")
            cc = os.path.join(local, "Packages", "Claude_abc123", "LocalCache",
                              "Roaming", "Claude", "claude-code")
            pkg = os.path.join(cc, "2.1.197"); os.makedirs(pkg)
            exe = os.path.join(pkg, "claude.exe"); open(exe, "w").close()
            # (1) _claude_base_dirs включает MSIX-базу (по LOCALAPPDATA)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": local}, clear=False):
                self.assertTrue(any("Claude_abc123" in b and "Packages" in b
                                    for b in suggest._claude_base_dirs()))
            # (2) резолв находит реальный MSIX-бинарь (базы = только MSIX, .env/which пусты)
            with mock.patch.object(suggest.shutil, "which", return_value=None), \
                 mock.patch.object(suggest, "CLAUDE_BIN", "claude"), \
                 mock.patch.object(suggest, "REPO_ENV", os.path.join(d, "no.env")), \
                 mock.patch.object(suggest, "_claude_base_dirs", return_value=[cc]):
                self.assertEqual(suggest._resolve_claude_once(), exe)

    def test_service_context_reproduction_fixed(self):
        # РЕПРО живого бага: %APPDATA%=systemprofile, env-var CLAUDE_BIN нет → раньше None. Теперь .env спасает.
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "claude.exe"); open(exe, "w").close()
            envf = os.path.join(d, ".env")
            with open(envf, "w", encoding="utf-8") as f:
                f.write("CLAUDE_BIN=" + exe + "\n")
            svc = r"C:\Windows\system32\config\systemprofile\AppData\Roaming\Claude\claude-code"
            with mock.patch.object(suggest, "REPO_ENV", envf), \
                 mock.patch.object(suggest, "CLAUDE_BIN", "claude"), \
                 mock.patch.object(suggest.shutil, "which", return_value=None), \
                 mock.patch.object(suggest, "_claude_base_dirs", return_value=[svc]):
                self.assertEqual(suggest._resolve_claude(retry_sleep=0), exe)


class TestParkModelsAllowlist(unittest.TestCase):
    """Модели клиенту — ТОЛЬКО реальный парк (Лист1). allowlist из fleet()/park_list.md + fail-safe."""

    def setUp(self):
        self._pf = suggest.PARK_LIST_FILE
        suggest.pricing._FLEET_CACHE["data"] = None      # сброс кэша fleet между тестами
        suggest.pricing._FLEET_CACHE["ts"] = 0

    def tearDown(self):
        suggest.PARK_LIST_FILE = self._pf
        suggest.pricing._FLEET_CACHE["data"] = None

    def _fleet(self, names):
        return lambda params: {"ok": True, "data": {"bikes": [{"name": n} for n in names]}}

    def test_allowlist_from_fleet_park_only(self):
        # реальный парк (имена как в Байки.xlsx Лист1) → только эти модели; «нет в парке» отсеяны
        names = ["NMAX 155CC BLACK PHUKET 4255", "XMAX 300CC GREY PHUKET 4246",
                 "ADV 350CC BLACK PHUKET 5849", "CB 300CC R 9011", "CBR 650R PHUKET 4505",
                 "MT-03 300СС BLUE PHUKET 5068", "CLICK 125CC PHUKET 5580"]
        allow = suggest.park_allowlist(getter=self._fleet(names))
        for m in ("NMAX 155", "XMAX 300", "ADV 350", "CB 300R", "CBR 650R", "MT-03"):
            self.assertIn(m, allow)
        for m in ("PCX 150", "PCX 160", "ADV 150", "ADV 160", "REBEL 300", "XSR 900", "R7", "CB 650R"):
            self.assertNotIn(m, allow)          # ← «нет в парке» НЕ попадают

    def test_prompt_hard_restricts_to_park(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", park_models=["NMAX 155", "XMAX 300", "ADV 350"])
        self.assertIn("ПАРК (СТРОГО)", sysp)
        self.assertIn("NMAX 155", sysp)
        self.assertIn("ADV 350", sysp)
        self.assertIn("НЕ предлагай", sysp)
        self.assertIn("PCX150", sysp)           # названы как «нет в парке — не предлагать»
        # без allowlist — блока НЕТ (fail-safe)
        self.assertNotIn("ПАРК (СТРОГО)", suggest.make_system_prompt("FAQ", "ru"))

    def test_failsafe_no_source_no_restriction(self):
        # fleet пуст + park_list.md отсутствует → None → бот отвечает как раньше (не онемел)
        suggest.PARK_LIST_FILE = os.path.join(tempfile.gettempdir(), "no_such_park_zzz.md")
        empty = lambda params: {"ok": False}
        self.assertIsNone(suggest.park_allowlist(getter=empty))
        self.assertNotIn("ПАРК (СТРОГО)", suggest.make_system_prompt("FAQ", "ru", park_models=None))
        d = suggest.generate_draft("[клиент]: NMAX?", "ru", "FAQ", call_llm=_fake_llm, park_models=None)
        self.assertTrue(d)                       # ← отвечает, ограничения нет

    def test_fallback_to_park_list_md_when_fleet_empty(self):
        with tempfile.TemporaryDirectory() as dd:
            pf = os.path.join(dd, "park_list.md")
            with open(pf, "w", encoding="utf-8") as f:
                f.write("| № | Название | Номер | Статус |\n|---|---|---|---|\n"
                        "| 1 | NMAX 155CC BLACK 4255 | 4255 | ДОМА |\n"
                        "| 2 | ADV 350CC GREY 798 | 798 | ДОМА |\n")
            suggest.PARK_LIST_FILE = pf
            allow = suggest.park_allowlist(getter=lambda p: {"ok": False})   # fleet пуст → файл
            self.assertIn("NMAX 155", allow)
            self.assertIn("ADV 350", allow)
            self.assertNotIn("PCX 150", allow)

    def test_price_cap_and_critfacts_intact_with_park(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          park_models=["NMAX 155"])
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)   # кап-цена цела
        self.assertIn("CLICK 125", sysp)                       # критфакт цел
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                # ценовая политика цела
        self.assertIn("ПАРК (СТРОГО)", sysp)

    def test_generate_draft_threads_park_into_prompt(self):
        cap = {}
        def capture(system, user):
            cap["s"] = system
            return "ok"
        suggest.generate_draft("[клиент]: что есть?", "ru", "FAQ", call_llm=capture,
                               park_models=["NMAX 155", "XMAX 300"])
        self.assertIn("ПАРК (СТРОГО)", cap["s"])
        self.assertIn("XMAX 300", cap["s"])


class TestPlaybook(unittest.TestCase):
    """Растущая книга правил (playbook): загрузка + подмешивание НИЖЕ кап-цены/критфактов + fail-safe."""

    def setUp(self):
        self._pf = suggest.PLAYBOOK_FILE

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._pf

    def test_seeded_playbook_has_park_rule(self):
        # боевой файл засеян первым правилом (модели только парка; Click не предлагать)
        txt = suggest.load_playbook()
        self.assertTrue(txt)
        self.assertIn("реального парка", txt)
        self.assertIn("Click 125", txt)

    def test_load_playbook_missing_returns_empty(self):
        suggest.PLAYBOOK_FILE = os.path.join(tempfile.gettempdir(), "no_such_playbook_zzz.md")
        self.assertEqual(suggest.load_playbook(), "")     # нет файла → '' (fail-safe)

    def test_playbook_block_below_critfacts_and_above_faq(self):
        sysp = suggest.make_system_prompt("ТЕЛО-FAQ", "ru", playbook="ПРАВИЛО-X: говорить мягко")
        self.assertIn("КНИГА ПРАВИЛ", sysp)
        self.assertIn("ПРАВИЛО-X", sysp)
        # СТРОГО НИЖЕ критфактов, ВЫШЕ общего FAQ:
        self.assertLess(sysp.index("КРИТИЧНЫЕ ФАКТЫ"), sysp.index("КНИГА ПРАВИЛ"))
        self.assertLess(sysp.index("КНИГА ПРАВИЛ"), sysp.index("FAQ и эталонные"))

    def test_style_fewshot_present_below_faq(self):
        # STYLE_FEWSHOT (шаг 5/7 #253): эталон тона из client_chats.jsonl всегда в промпте,
        # САМЫМ НИЗОМ (после FAQ) — тон, а не источник фактов/цен.
        sysp = suggest.make_system_prompt("ТЕЛО-FAQ", "ru")
        self.assertIn("СТИЛЬ ОТВЕТА", sysp)
        self.assertIn("ПРИМЕРЫ ЖИВЫХ ОТВЕТОВ", sysp)
        # реальные обороты менеджеров из живой базы:
        self.assertIn("спасибо, что выбрали нас", sysp)
        self.assertIn("Оплата каким способом удобнее", sysp)
        self.assertIn("бронь закрепляется по 100% предоплате", sysp)
        # приоритет: критфакты/парк-политика ВЫШЕ примеров, примеры — самым низом (после FAQ)
        self.assertLess(sysp.index("КРИТИЧНЫЕ ФАКТЫ"), sysp.index("СТИЛЬ ОТВЕТА"))
        self.assertLess(sysp.index("FAQ и эталонные"), sysp.index("ПРИМЕРЫ ЖИВЫХ ОТВЕТОВ"))
        # 15–20 пар few-shot, как требует задача
        self.assertGreaterEqual(len(suggest.STYLE_FEWSHOT_PAIRS), 15)
        self.assertLessEqual(len(suggest.STYLE_FEWSHOT_PAIRS), 20)

    def test_style_fewshot_no_price_without_dates_leak(self):
        # Примеры НЕ должны протаскивать формат сетки, который код прячет от LLM (симв. ฿),
        # и НЕ отменяют ценовую политику «нет дат — нет цены».
        sysp = suggest.make_system_prompt("FAQ", "ru")
        self.assertNotIn("• Сутки:", suggest.STYLE_FEWSHOT)
        self.assertNotIn("฿", suggest.STYLE_FEWSHOT)
        self.assertIn("НЕ называй клиенту НИКАКУЮ цену", sysp)  # кап-политика на месте

    def test_style_step6_no_regreet_noreask_no_kanc(self):
        # шаг 6/7 #253: ПОВЕРХ STYLE_GUIDE — не здороваться повторно, не переспрашивать
        # уже данное (клиент назвал скутер = опыт на скутерах), убрать канцелярит.
        sysp = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("Приветствие НЕ повторяем", sysp)
        self.assertIn("опыт на скутерах — отлично", sysp)
        self.assertIn("БЕЗ канцелярита", sysp)
        self.assertIn("Спасибо за информацию", sysp)          # пример канцелярита-запрета вшит
        # правила лежат в стилевом блоке (ниже FAQ и критфактов), стиль не подменяет факты
        self.assertLess(sysp.index("СТИЛЬ ОТВЕТА"), sysp.index("Приветствие НЕ повторяем"))
        self.assertLess(sysp.index("КРИТИЧНЫЕ ФАКТЫ"), sysp.index("БЕЗ канцелярита"))

    def test_playbook_empty_skipped_generation_ok(self):
        # пусто → блока нет, генерация цела (fail-safe)
        self.assertNotIn("КНИГА ПРАВИЛ", suggest.make_system_prompt("FAQ", "ru", playbook=""))
        d = suggest.generate_draft("[клиент]: NMAX?", "ru", "FAQ", call_llm=_fake_llm, playbook="")
        self.assertTrue(d)

    def test_playbook_does_not_override_price_and_critfacts(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          playbook="ПРАВИЛО-Y")
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)   # кап-цена цела
        self.assertIn("CLICK 125", sysp)                       # критфакт цел
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                # ценовая политика цела
        self.assertIn("НЕ отменяет", sysp)                     # playbook прямо помечен как неприоритетный

    def test_generate_draft_threads_playbook_into_prompt(self):
        cap = {}
        def capture(system, user):
            cap["s"] = system
            return "ok"
        suggest.generate_draft("[клиент]: привет", "ru", "FAQ", call_llm=capture,
                               playbook="ПРАВИЛО-Z: без давления")
        self.assertIn("КНИГА ПРАВИЛ", cap["s"])
        self.assertIn("ПРАВИЛО-Z", cap["s"])


class TestPlaybookAppend(unittest.TestCase):
    """Фаза 2: дозапись выученного правила в playbook.md (append + дата + дедуп + fail-safe)."""

    SEED = ("# Playbook\n\n## Стиль общения\n- коротко\n\n## Выученные правила\n"
            "- Предлагать клиенту только модели реального парка; Honda Click 125 не предлагать.\n")

    def setUp(self):
        self._pf = suggest.PLAYBOOK_FILE
        self._tmp = tempfile.TemporaryDirectory()
        self._file = os.path.join(self._tmp.name, "playbook.md")
        with open(self._file, "w", encoding="utf-8") as f:
            f.write(self.SEED)
        suggest.PLAYBOOK_FILE = self._file

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._pf
        self._tmp.cleanup()

    def _read(self):
        with open(self._file, encoding="utf-8") as f:
            return f.read()

    def test_append_adds_rule_with_date_in_learned_section(self):
        import datetime as _dt
        st = suggest.append_playbook_rule("Всегда уточняй район доставки заранее", now=_dt.date(2026, 7, 6))
        self.assertEqual(st, "added")
        txt = self._read()
        self.assertIn("- (2026-07-06) Всегда уточняй район доставки заранее", txt)   # с датой
        self.assertIn("только модели реального парка", txt)                          # append, не перезапись
        self.assertLess(txt.index("## Выученные правила"), txt.index("Всегда уточняй район"))

    def test_append_dedup_near_identical(self):
        st = suggest.append_playbook_rule(
            "предлагать клиенту только модели реального парка honda click 125 не предлагать")
        self.assertEqual(st, "duplicate")
        self.assertEqual(self._read().count("реального парка"), 1)   # дубль НЕ дописан

    def test_append_failsafe_on_missing_target(self):
        suggest.PLAYBOOK_FILE = os.path.join(self._tmp.name, "nope", "playbook.md")  # каталога нет
        self.assertEqual(suggest.append_playbook_rule("правило"), "error")

    def test_learned_rule_then_shows_in_prompt(self):
        suggest.append_playbook_rule("Всегда предлагай шлем в подарок на неделю аренды")
        sysp = suggest.make_system_prompt("FAQ", "ru", playbook=suggest.load_playbook())
        self.assertIn("шлем в подарок", sysp)
        self.assertIn("КНИГА ПРАВИЛ", sysp)

    def test_rule_limit_fifo_evicts_oldest(self):
        # лимит числа правил: при переполнении новое ВЫТЕСНЯЕТ самые ранние (FIFO), кап держится.
        save = suggest.PLAYBOOK_MAX_RULES
        suggest.PLAYBOOK_MAX_RULES = 3
        try:
            # секция уже несёт 1 seed-правило («реального парка»); добьём сверх капа пятью РАЗНЫМИ.
            for r in ("всегда предлагай шлем подарком при недельной аренде",
                      "уточняй район доставки до финального расчёта цены",
                      "не употребляй канцелярит в приветствии клиента",
                      "предлагай длительный прокат со скидкой сразу",
                      "напоминай про залог только после выбора модели"):
                self.assertEqual(suggest.append_playbook_rule(r), "added")
            rules = suggest._playbook_learned_rules(self._read())
            self.assertEqual(len(rules), 3)                       # кап соблюдён
            joined = " || ".join(rules).lower()
            self.assertIn("залог только после выбора", joined)    # новейшее осталось
            self.assertNotIn("реального парка", joined)           # самое старое (seed) вытеснено
            self.assertNotIn("шлем подарком", joined)             # ранние вытеснены
            self.assertNotIn("район доставки до финального", joined)
        finally:
            suggest.PLAYBOOK_MAX_RULES = save

    def test_written_rule_reaches_both_generation_paths(self):
        # шаг 2/8: записанное правило попадает в промпт СЛЕДУЮЩЕЙ генерации в ОБОИХ путях —
        # первичка (generate_draft) и strategy-перегенерация (regenerate_draft). «Следующая генерация»
        # читает книгу заново через load_playbook — ровно как боевые вызовы (suggest.py / moderation_core).
        self.assertEqual(suggest.append_playbook_rule("Всегда уточняй район доставки перед расчётом"), "added")
        pb = suggest.load_playbook()
        self.assertIn("район доставки перед расчётом", pb)

        cap = {}
        def capture(system, user):
            cap["s"] = system
            return "ok"

        # путь 1 — первичка
        suggest.generate_draft("[клиент]: привет", "ru", "FAQ", call_llm=capture, playbook=pb)
        self.assertIn("КНИГА ПРАВИЛ", cap["s"])
        self.assertIn("район доставки перед расчётом", cap["s"])

        # путь 2 — strategy-перегенерация (директива поверх исходного контекста)
        cap.clear()
        suggest.regenerate_draft("[клиент]: привет", "ru", "FAQ", False, "", "сделай теплее",
                                 call_llm=capture, playbook=pb)
        self.assertIn("КНИГА ПРАВИЛ", cap["s"])
        self.assertIn("район доставки перед расчётом", cap["s"])


class TestPlaybookConflict(unittest.TestCase):
    """Родитель 112, шаг 3/8: конфликт/замена выученного правила. Конфликт = та же ТЕМА, ОБРАТНАЯ
    полярность (одно с «не», другое без) — то, что дедуп НЕ ловит. Замена «оставить новое» убирает
    старое и дописывает новое. Всё во ВРЕМЕННЫЙ playbook (боевой не трогаем)."""

    SEED = ("# Playbook\n\n## Стиль общения\n- коротко\n\n## Выученные правила\n"
            "- (2026-07-01) Здоровайся дважды в одном диалоге для теплоты\n"
            "- (2026-07-02) Всегда предлагай шлем в подарок на неделю аренды\n")

    def setUp(self):
        self._pf = suggest.PLAYBOOK_FILE
        self._tmp = tempfile.TemporaryDirectory()
        self._file = os.path.join(self._tmp.name, "playbook.md")
        with open(self._file, "w", encoding="utf-8") as f:
            f.write(self.SEED)
        suggest.PLAYBOOK_FILE = self._file

    def tearDown(self):
        suggest.PLAYBOOK_FILE = self._pf
        self._tmp.cleanup()

    def _read(self):
        with open(self._file, encoding="utf-8") as f:
            return f.read()

    def test_conflict_detected_on_polarity_flip(self):
        # «не здоровайся дважды» ↔ записанное «здоровайся дважды» — та же тема, обратный смысл → клэш
        clash = suggest.find_playbook_conflict("не здоровайся дважды в одном диалоге")
        self.assertIn("Здоровайся дважды", clash)

    def test_no_conflict_for_same_polarity_or_other_topic(self):
        # та же полярность (не конфликт, это дедуп-территория) и другая тема → клэша нет
        self.assertEqual("", suggest.find_playbook_conflict("здоровайся дважды в одном диалоге"))
        self.assertEqual("", suggest.find_playbook_conflict("не предлагай скидку без менеджера"))

    def test_conflict_empty_rule_is_safe(self):
        self.assertEqual("", suggest.find_playbook_conflict("   "))

    def test_conflict_reads_only_learned_section_not_style_guide(self):
        # клэш ищем среди ВЫУЧЕННЫХ правил, а не в статичном «Стиль общения» (там «коротко» — не трогаем)
        self.assertEqual("", suggest.find_playbook_conflict("не коротко, а развёрнуто"))

    def test_replace_keeps_new_removes_old(self):
        # «оставить новое»: убрать старое конфликтующее правило, дописать новое (обратной полярности)
        st = suggest.replace_playbook_rule("Здоровайся дважды в одном диалоге для теплоты",
                                           "Не здоровайся дважды в одном диалоге",
                                           now=datetime.date(2026, 7, 16))
        self.assertEqual("replaced", st)
        txt = self._read()
        self.assertIn("Не здоровайся дважды", txt)                       # новое записано
        self.assertNotIn("Здоровайся дважды в одном диалоге для теплоты", txt)  # старое убрано
        self.assertIn("шлем в подарок", txt)                            # соседнее правило не тронуто

    def test_replace_without_matching_old_just_appends(self):
        # старого не нашли (пусто/нет похожего) → просто дописать новое, статус 'added'
        st = suggest.replace_playbook_rule("", "Совсем новое правило про пунктуальность",
                                           now=datetime.date(2026, 7, 16))
        self.assertEqual("added", st)
        self.assertIn("Совсем новое правило про пунктуальность", self._read())

    def test_replace_new_rule_survives_despite_dedup(self):
        # КЛЮЧ: без удаления старого новое (обратная полярность) осело бы как duplicate. Проверяем,
        # что после replace новое ПРИСУТСТВУЕТ (значит удаление-до-append сработало).
        suggest.replace_playbook_rule("Здоровайся дважды в одном диалоге для теплоты",
                                      "Не здоровайся дважды в одном диалоге")
        rules = " || ".join(suggest._playbook_learned_rules(self._read())).lower()
        self.assertIn("не здоровайся дважды", rules)

    def test_replace_empty_new_is_error(self):
        self.assertEqual("error", suggest.replace_playbook_rule("что-то", "   "))

    def test_replace_failsafe_on_missing_target(self):
        suggest.PLAYBOOK_FILE = os.path.join(self._tmp.name, "nope", "playbook.md")
        self.assertEqual("error", suggest.replace_playbook_rule("старое", "новое"))


class TestSalesPressureAndSafety(unittest.TestCase):
    """Хвост 2 — рычаг SALES_PRESSURE НАД базой; Хвост 1 — жёсткое правило опыт/безопасность."""

    def setUp(self):
        self._sp = suggest.SALES_PRESSURE

    def tearDown(self):
        suggest.SALES_PRESSURE = self._sp

    def test_pressure_firm_block_and_cta(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pressure="firm")
        self.assertIn("НАСТОЙЧИВОСТИ", sysp)
        self.assertIn("FIRM", sysp)
        self.assertIn("предоплат", sysp.lower())          # явный CTA к предоплате

    def test_pressure_normal_no_block_regression(self):
        suggest.SALES_PRESSURE = "normal"
        normal = suggest.make_system_prompt("FAQ", "ru", pressure="normal")
        default = suggest.make_system_prompt("FAQ", "ru")  # дефолт из глобали (normal)
        self.assertNotIn("НАСТОЙЧИВОСТИ", normal)          # normal НЕ добавляет блок
        self.assertEqual(normal, default)                  # регресс: дефолт == явный normal

    def test_pressure_soft_block(self):
        self.assertIn("SOFT", suggest.make_system_prompt("FAQ", "ru", pressure="soft"))

    def test_pressure_invalid_falls_to_normal(self):
        self.assertEqual(suggest._pressure_block("garbage"), "")
        self.assertNotIn("НАСТОЙЧИВОСТИ", suggest.make_system_prompt("FAQ", "ru", pressure="garbage"))

    def test_pressure_default_reads_global(self):
        suggest.SALES_PRESSURE = "firm"
        self.assertIn("FIRM", suggest.make_system_prompt("FAQ", "ru"))   # без аргумента → глобаль

    def test_firm_keeps_invariants(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note="ЦЕНА из Календаря: 500฿/день",
                                          park_models=["NMAX 155"], pressure="firm")
        self.assertIn("ЦЕНА из Календаря: 500฿/день", sysp)   # кап-цена цела
        self.assertIn("CLICK 125", sysp)                       # критфакт цел
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)                # политика цела
        self.assertIn("★ ПАРК (СТРОГО)", sysp)                 # парк цел
        self.assertIn("НЕ нарушай", sysp)                      # firm явно защищает инварианты

    def test_experience_safety_rule_always_and_positioned(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", playbook="ПРАВИЛО-X")
        self.assertIn("ОПЫТ И БЕЗОПАСНОСТЬ", sysp)             # жёсткое правило A.3 есть всегда
        self.assertIn("ТОЛЬКО менеджер", sysp)
        # рядом с критфактами: ПОСЛЕ CRITICAL_FACTS, ВЫШЕ playbook
        self.assertLess(sysp.index("КРИТИЧНЫЕ ФАКТЫ"), sysp.index("ОПЫТ И БЕЗОПАСНОСТЬ"))
        self.assertLess(sysp.index("ОПЫТ И БЕЗОПАСНОСТЬ"), sysp.index("КНИГА ПРАВИЛ"))

    def test_approval_whitelist_rule_always_and_positioned(self):
        sysp = suggest.make_system_prompt("FAQ", "ru", playbook="ПРАВИЛО-X")
        self.assertIn("БЕЛЫЙ СПИСОК", sysp)                    # белый список утверждаемого есть всегда
        self.assertIn("уточню у команды и вернусь", sysp)      # деградация вместо утверждения
        self.assertIn("[уточнить: цвет ADV350]", sysp)         # формат пометки менеджеру
        self.assertIn("цвет, комплектацию", sysp)              # цвет/комплектация — запрещено утверждать
        # рядом с критфактами: ПОСЛЕ CRITICAL_FACTS/ОПЫТ, ВЫШЕ playbook
        self.assertLess(sysp.index("ОПЫТ И БЕЗОПАСНОСТЬ"), sysp.index("БЕЛЫЙ СПИСОК"))
        self.assertLess(sysp.index("БЕЛЫЙ СПИСОК"), sysp.index("КНИГА ПРАВИЛ"))

    def test_approval_whitelist_note_not_stripped_as_service(self):
        # пометка «[уточнить: …]» адресована модератору — _strip_service_prefix её НЕ режет
        # (её маркер — «[внутренн», не «[уточнить»); ведущий срез трогает только стадию сделки.
        draft = "Уточню у команды и вернусь.\n[уточнить: цвет ADV350]"
        self.assertEqual(suggest._strip_service_prefix(draft), draft)


class TestDraftPostcheck(unittest.TestCase):
    """Пост-чек черновика (шаг 4/7 #243): утверждения о цвете/наличии/цене ВНЕ белого списка
    переписываются в «уточню»-форму + пометка модератору. Голдены — ДОСЛОВНЫЕ живые провалы 12.07
    (правило-класс CLAUDE.md: тест детекта = реальная фраза клиента/бота, не идеализация)."""

    # три дословных провала 12.07, каждый ДОЛЖЕН блокироваться пост-чеком
    G_COLOR = "выбора по цвету, к сожалению, нет"
    G_AVAIL_COLOR = "светлого ADV 350 сейчас нет — есть чёрный"
    G_PRICE = "9000 бат за 10 дней"

    def _client_part(self, out):
        """Часть, видимая клиенту (до пометки модератору «[уточнить: …]»)."""
        return out.split("[уточнить", 1)[0]

    def test_golden_color_blocked(self):
        out = suggest.postcheck_draft(self.G_COLOR, "ru", pricing_note="")
        self.assertNotEqual(out, self.G_COLOR)                       # переписан
        self.assertIn("уточню у команды", out.lower())               # деградация вместо утверждения
        self.assertNotIn("цвет", self._client_part(out).lower())     # цвет клиенту не утверждаем
        self.assertIn("[уточнить: цвет", out)                        # пометка модератору

    def test_golden_avail_color_blocked(self):
        out = suggest.postcheck_draft(self.G_AVAIL_COLOR, "ru", pricing_note="")
        self.assertNotEqual(out, self.G_AVAIL_COLOR)
        self.assertIn("уточню у команды", out.lower())
        cp = self._client_part(out).lower()
        self.assertNotIn("чёрн", cp)                                 # цвет не утверждаем
        self.assertNotIn("сейчас нет", cp)                           # наличие не утверждаем
        self.assertIn("ADV350", out)                                 # модель в пометке модератору

    def test_golden_price_total_blocked(self):
        # 9000 нет в белом источнике цен (pricing_note без него) → итог-за-период клеймится
        note = "ЦЕНА из Календаря: ADV 350 — 998฿/день."
        out = suggest.postcheck_draft(self.G_PRICE, "ru", pricing_note=note)
        self.assertNotEqual(out, self.G_PRICE)
        self.assertIn("уточню у команды", out.lower())
        self.assertNotIn("9000", self._client_part(out))             # выдуманный итог клиенту не уходит
        self.assertIn("[уточнить: цена 9000]", out)

    def test_price_from_whitelist_kept(self):
        # тот же итог, но ЧИСЛО есть в белом источнике (пришло из Календаря) → НЕ трогаем
        note = "ЦЕНА из Календаря: ADV 350 — за 10 дней 9000฿."
        text = "ADV 350 — 9000 бат за 10 дней."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note=note), text)

    def test_per_day_price_not_flagged(self):
        # суточный тариф из котировки (не итог-за-период) пост-чек не переписывает
        text = "NMAX на 7 дней — 449฿/день, депозит 3000฿."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note=""), text)

    def test_clean_draft_byte_for_byte(self):
        # нет нарушений → вход возвращается БАЙТ-В-БАЙТ (fail-safe, регресс не трогаем)
        text = "Здравствуйте! Подскажите даты аренды — подберём вариант."
        self.assertIs(suggest.postcheck_draft(text, "ru", pricing_note=""), text)

    def test_park_enumeration_allowed(self):
        # перечень моделей парка «есть NMAX и ADV 350» — в белом списке, не клеймим
        text = "У нас есть NMAX 155 и ADV 350."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note=""), text)

    def test_surrounding_text_preserved(self):
        # нарушение в середине — соседние законные предложения целы, переписан только виновный сегмент
        text = "Здравствуйте! Светлого ADV 350 сейчас нет. Чем ещё помочь?"
        out = suggest.postcheck_draft(text, "ru", pricing_note="")
        self.assertTrue(out.startswith("Здравствуйте!"))
        self.assertIn("Чем ещё помочь?", out)
        self.assertIn("Уточню у команды и вернусь.", out)
        self.assertNotIn("Светлого", self._client_part(out))

    def test_llm_tiebreak_flags_disputed(self):
        # спорный «ADV 350 будет к пятнице» (готовность-на-дату, склад-RE молчит) → LLM-тайбрейк
        def judge(_s, _u):
            return '{"violation": true, "kind": "avail"}'
        text = "ADV 350 точно готов будет к пятнице."
        out = suggest.postcheck_draft(text, "ru", pricing_note="", call_llm=judge)
        self.assertIn("уточню у команды", out.lower())
        self.assertNotIn("пятниц", self._client_part(out).lower())

    def test_llm_tiebreak_clears_disputed(self):
        # тот же спорный, но LLM говорит «не нарушение» → НЕ трогаем (fail-safe на сомнении)
        def judge(_s, _u):
            return '{"violation": false, "kind": "none"}'
        text = "ADV 350 готов вам помочь с выбором."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note="", call_llm=judge), text)

    # --- ГОЛДЕН: дыра пост-чека чисел закрыта (#310) — итог-за-период с ЛЮБЫМ предлогом клеймится ---
    # Живой провал окна 504608015 (аудит 13:22): черновик писал «на 5 дней — 2 245 ฿» (наивное 449×5)
    # при quote-итоге 1685 ฿. Узкий _PC_PERIOD ловил только «за N дней» → «на 5 дней» проскакивал мимо
    # пост-чека. Разбор денег свёрнут в extract_money_figures (точка правды одна), период ловится при
    # любой формулировке. Голдены — ДОСЛОВНАЯ живая фраза (правило-класс CLAUDE.md) + парафразы RU/EN.
    PN_504608015 = "ЦЕНА из Календаря: NMAX 155 — 337 ฿/день, за 5 дней 1685 ฿, депозит 3000 ฿."

    def test_golden_window_504608015_na_period_blocked(self):
        # ДОСЛОВНАЯ живая фраза провала: предлог «на», а НЕ «за» — раньше проскакивала
        draft = "NMAX 155 на 5 дней — 2 245 ฿ (449 ฿/день)"
        out = suggest.postcheck_draft(draft, "ru", pricing_note=self.PN_504608015)
        self.assertNotEqual(out, draft)                              # черновик переписан
        client = self._client_part(out)
        self.assertNotIn("2245", client.replace(" ", ""))           # наивный итог клиенту НЕ уходит
        self.assertNotIn("449", client)                             # весь сегмент → «уточню», без чисел
        self.assertIn("уточню у команды", out.lower())
        self.assertIn("[уточнить: цена 2245]", out)                 # пометка модератору

    def test_golden_window_legit_total_1685_kept(self):
        # тот же срок «на 5 дней», но ИСТИННЫЙ итог 1685 из quote → НЕ трогаем (негатив, регресс)
        draft = "NMAX 155 на 5 дней — 1 685 ฿ (337 ฿/день)."
        self.assertEqual(suggest.postcheck_draft(draft, "ru", pricing_note=self.PN_504608015), draft)

    def test_period_total_paraphrases_blocked(self):
        # парафразы того же провала: «—» без предлога, «N дней:», обратный порядок, EN «for … days»,
        # падежи (суток) — ВСЕ формулировки итога-за-срок обязаны клеймиться пост-чеком
        for draft in ("Итого 2 245 ฿ за 5 дней.",
                      "На 5 дней получится 2 245 бат.",
                      "— 5 дней — 2 245 ฿.",
                      "5 дней: 2 245 ฿.",
                      "NMAX 155 for 5 days — 2 245 THB",
                      "За период выйдет 2245฿ за 5 суток."):
            out = suggest.postcheck_draft(draft, "ru", pricing_note=self.PN_504608015)
            self.assertNotEqual(out, draft, draft)                  # переписан
            self.assertNotIn("2245", self._client_part(out).replace(" ", ""), draft)
            self.assertIn("уточню у команды", out.lower(), draft)

    def test_daily_rate_not_flagged_after_widening(self):
        # РЕГРЕСС фикса дыры: суточная ставка «449 ฿/день» рядом с «на 7 дней» — НЕ итог-за-период,
        # пост-чек не клеймит её даже теперь, когда «на N дней» распознаётся
        text = "NMAX на 7 дней — 449 ฿/день, депозит 3000 ฿."
        self.assertEqual(suggest.postcheck_draft(text, "ru", pricing_note=""), text)


class TestFreePickupPostcheck(unittest.TestCase):
    """Пост-чек забора (шаг 5/7 #22): бесплатный забор в конце аренды — ТОЛЬКО при ОПЛАЧЕННОЙ
    доставке. Без доставки/самовывоз забор платный как доставка зоны; самопротиворечие «заберём
    бесплатно — заберите сами» запрещено. Голдены — реальные формулировки менеджера/бота, а не
    идеализация (правило-класс CLAUDE.md)."""

    def _fp(self, low):
        return bool(suggest._FP_FREE_RE.search(low))

    # --- ЯДРО ШАГА: точка без доставки → нет обещания бесплатного забора ---
    def test_self_pickup_strips_free_pickup(self):
        # Самопротиворечие в одном черновике: «заберёте сами» + «забор бесплатный».
        draft = ("Байк заберёте сами по адресу магазина. Забор байка в конце аренды бесплатный. "
                 "Бронируем?")
        out = suggest.postcheck_free_pickup(draft, "ru")
        self.assertNotIn("бесплатн", out.lower())          # обещание бесплатного забора снято
        self.assertIn("заберёте сами", out.lower())        # остальной текст на месте
        self.assertIn("Бронируем?", out)

    def test_point_without_delivery_no_free_pickup(self):
        # «Точка без доставки»: delivery_paid=False → бесплатного забора не обещаем даже без слова
        # «самовывоз» в тексте (клиент прислал точку, но доставку не берёт).
        draft = "Отлично, вот наш адрес. Забор байка в конце аренды — бесплатный."
        out = suggest.postcheck_free_pickup(draft, "ru", delivery_paid=False)
        self.assertNotIn("бесплатн", out.lower())
        self.assertNotIn("забор", out.lower())

    def test_paren_clause_stripped_keeps_price(self):
        # Клауза в скобках вырезается, цена доставки и точка остаются.
        draft = "Заберёте сами. Доставка — 590 ฿ (забор байка в конце аренды — бесплатный)."
        out = suggest.postcheck_free_pickup(draft, "ru")
        self.assertIn("590", out)
        self.assertNotIn("бесплатн", out.lower())
        self.assertIn("Доставка — 590 ฿.", out)

    def test_comma_clause_stripped(self):
        # Живая формулировка: «... доставка, забор ... бесплатный» при самовывозе → хвост срезан.
        draft = "Самовывоз из Камалы, забор байка в конце аренды бесплатный."
        out = suggest.postcheck_free_pickup(draft, "ru")
        self.assertNotIn("бесплатн", out.lower())
        self.assertIn("Самовывоз из Камалы", out)

    def test_en_self_pickup_strips_free_pickup(self):
        draft = "You'll pick it up yourself at our shop. Bike pickup at the end of the rental is free."
        out = suggest.postcheck_free_pickup(draft, "en")
        self.assertNotIn("free", out.lower())
        self.assertIn("pick it up yourself", out.lower())

    # --- НЕГАТИВЫ: законный бесплатный забор при оплаченной доставке НЕ трогаем ---
    def test_paid_delivery_free_pickup_untouched(self):
        # Доставку клиент берёт (нет сигнала самовывоза) → бесплатный забор легитимен, вход байт-в-байт.
        draft = "Найхарн — 590 бат доставка, забор байка в конце аренды бесплатный."
        self.assertEqual(suggest.postcheck_free_pickup(draft, "ru"), draft)

    def test_code_delivery_line_untouched(self):
        # КОД-строка доставки (compose_delivery_draft) — оплаченная зона, без самовывоза: не трогаем.
        draft = "Доставка — 290 ฿ (забор байка в конце аренды — бесплатный)."
        self.assertEqual(suggest.postcheck_free_pickup(draft, "ru"), draft)

    def test_no_free_pickup_mention_untouched(self):
        # Нет обещания бесплатного забора вовсе → вход без изменений (fail-safe).
        draft = "Самовывоз из нашего магазина по адресу, работаем с 9 до 20."
        self.assertIs(suggest.postcheck_free_pickup(draft, "ru"), draft)

    def test_regex_catches_live_phrasings(self):
        # Детект обещания на реальных/парафразных формулировках (позитивы) и контрпримерах (негативы).
        pos = [
            "забор байка в конце аренды — бесплатный",
            "забор в конце аренды бесплатный",
            "заберём байк бесплатно в конце аренды",
            "bike pickup at the end of the rental is free",
            "free pickup at the end",
        ]
        for p in pos:
            self.assertTrue(self._fp(p), p)
        neg = [
            "доставка 590 бат",                     # только доставка, без забора
            "депозит 3000 бат или паспорт",         # депозит
            "заберёте байк сами по адресу",         # самовывоз без слова «бесплатный»
            "we deliver for a fee",                 # платная доставка, не забор
        ]
        for n in neg:
            self.assertFalse(self._fp(n), n)


class TestGuardAvailability(unittest.TestCase):
    """guard_availability (родитель4, шаг 4/6): гард ПЕРЕД отправкой — бот НЕ утверждает наличие/
    дефицит/особые условия без данных. Форма по образцу удалённого guard_quote_price: клейм без
    данных → перегенерация с жёсткой директивой → после N неудач безопасный фолбэк. Голдены —
    реальные формулировки бота-«продавца» (дефицит/срочность/спецусловия), а не идеализация."""

    Q_FREE = {"status": "ok"}                 # Bridge: подходящий байк свободен на даты
    Q_BUSY = {"status": "none_available"}     # Bridge: все юниты заняты на даты

    def test_state_from_various_data(self):
        self.assertIs(suggest.availability_state(True), True)
        self.assertIs(suggest.availability_state(False), False)
        self.assertIs(suggest.availability_state({"available": True}), True)
        self.assertIs(suggest.availability_state({"available": False}), False)
        self.assertIs(suggest.availability_state(self.Q_FREE), True)
        self.assertIs(suggest.availability_state(self.Q_BUSY), False)
        self.assertIsNone(suggest.availability_state(None))
        self.assertIsNone(suggest.availability_state({"status": "error"}))

    def test_claims_detected_by_kind(self):
        kinds = lambda t: {c["kind"] for c in suggest.availability_claims(t)}
        self.assertIn("avail_neg", kinds("Этой модели сейчас нет в наличии."))
        self.assertIn("avail_pos", kinds("NMAX 155 свободен на ваши даты."))
        self.assertIn("scarcity", kinds("Остался последний байк, успевайте!"))
        self.assertIn("special", kinds("Сделаю особые условия специально для вас."))
        # чистый черновик — клеймов нет
        self.assertEqual(suggest.availability_claims("Здравствуйте! Назовите даты аренды."), [])

    def test_neg_not_double_counted_as_pos(self):
        # «нет в наличии» — только avail_neg, «в наличии» внутри отрицания НЕ считаем за avail_pos
        cl = suggest.availability_claims("Этой модели нет в наличии.")
        self.assertEqual([c["kind"] for c in cl], ["avail_neg"])

    def test_no_claims_clean_passthrough(self):
        # нет клеймов наличия → черновик БАЙТ-В-БАЙТ (fail-safe), source='clean'
        draft = "Здравствуйте! Подскажите даты — подберём вариант и уточним цену по Календарю."
        r = suggest.guard_availability(draft)
        self.assertEqual(r["source"], "clean")
        self.assertTrue(r["ok"])
        self.assertIs(r["text"], draft)
        self.assertEqual(r["violations"], [])

    def test_avail_pos_supported_by_data_passes(self):
        # «свободен на даты» + Bridge подтвердил ok → утверждение подкреплено данными, source='draft'
        draft = "NMAX 155 свободен на ваши даты."
        r = suggest.guard_availability(draft, avail=self.Q_FREE, model="NMAX 155")
        self.assertEqual(r["source"], "draft")
        self.assertEqual(r["text"], draft)

    def test_avail_neg_supported_by_data_passes(self):
        # «занят на даты» + Bridge none_available → подкреплено, не трогаем
        draft = "К сожалению, этот байк занят на ваши даты."
        r = suggest.guard_availability(draft, avail=self.Q_BUSY, model="NMAX 155")
        self.assertEqual(r["source"], "draft")

    def test_avail_pos_without_data_is_violation(self):
        # то же «свободен», но данных наличия НЕТ (avail=None) → нарушение инварианта
        bad = suggest.availability_violations("NMAX 155 свободен на ваши даты.", None)
        self.assertTrue(any(b["kind"] == "avail_pos" for b in bad))

    def test_avail_pos_contradicts_data_is_violation(self):
        # «свободен», а Bridge говорит none_available → противоречие данным = нарушение
        bad = suggest.availability_violations("NMAX 155 свободен на ваши даты.", self.Q_BUSY)
        self.assertTrue(any(b["kind"] == "avail_pos" for b in bad))

    def test_scarcity_always_violation_even_with_data(self):
        # дефицит/срочность источника данных НЕ имеют → нарушение ДАЖE при наличии quote
        bad = suggest.availability_violations("Остался последний, успевайте забронировать!", self.Q_FREE)
        self.assertTrue(any(b["kind"] == "scarcity" for b in bad))

    def test_special_always_violation(self):
        # особые/персональные условия назначает менеджер → всегда нарушение
        bad = suggest.availability_violations("Сделаю вам персональную скидку, только для вас.", self.Q_FREE)
        self.assertTrue(any(b["kind"] == "special" for b in bad))

    def test_regenerate_fixes_violation(self):
        # клейм без данных → перегенерация с жёсткой директивой отдаёт чистый текст, source='regen'
        bad = "Остался последний NMAX 155, успевайте — свободен на ваши даты!"
        good = "Уточню наличие NMAX 155 по вашим датам у команды и вернусь."
        calls = []

        def regen(directive):
            calls.append(directive)
            return good
        r = suggest.guard_availability(bad, avail=None, model="NMAX 155", regenerate=regen, max_retries=2)
        self.assertEqual(r["source"], "regen")
        self.assertEqual(r["attempts"], 1)
        self.assertEqual(r["text"], good)
        self.assertIn("НЕ утверждай наличие", calls[0])              # директива несёт запрет

    def test_fallback_after_n_failures(self):
        # перегенерация упорно продолжает клеймить → после N попыток безопасный фолбэк
        bad = "Остался последний байк, успевайте!"
        attempts = {"n": 0}

        def regen(directive):
            attempts["n"] += 1
            return "Точно последний, разбирают быстро — успевайте!"   # всё ещё дефицит
        r = suggest.guard_availability(bad, avail=None, model="NMAX 155", regenerate=regen, max_retries=2)
        self.assertEqual(r["source"], "fallback")
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(attempts["n"], 2)
        # фолбэк безопасен: обещает уточнить и сам НЕ содержит клеймов наличия/дефицита
        self.assertEqual(suggest.availability_claims(r["text"]), [])
        self.assertIn("уточню наличие", r["text"].lower())

    def test_no_regenerate_goes_to_fallback(self):
        # нарушение без regenerate-колбэка → сразу безопасный фолбэк
        r = suggest.guard_availability("Остался последний, успевайте!", avail=None,
                                       model="NMAX 155", max_retries=0)
        self.assertEqual(r["source"], "fallback")
        self.assertEqual(suggest.availability_claims(r["text"]), [])

    def test_fallback_text_is_clean_ru_en(self):
        # оба фолбэка (RU/EN) сами не триггерят гард (иначе луп) — клеймов наличия нет
        self.assertEqual(suggest.availability_claims(suggest.availability_fallback(model="NMAX 155")), [])
        self.assertEqual(
            suggest.availability_claims(suggest.availability_fallback(model="NMAX 155", lang="en")), [])

    def test_en_special_and_scarcity_flagged(self):
        # EN-формулировки дефицита/спецусловий тоже ловятся (парк англоязычных клиентов)
        bad = suggest.availability_violations("Last one, hurry! Special offer just for you.", None)
        self.assertTrue(any(b["kind"] == "scarcity" for b in bad))
        self.assertTrue(any(b["kind"] == "special" for b in bad))


class TestExtractMoneyFigures(unittest.TestCase):
    """extract_money_figures (шаг 2/5 #310): разбор денежных чисел из ТЕКСТА ответа бота с
    классификацией rate/total/deposit/amount. Голдены — ДОСЛОВНЫЕ живые ответы бота (правило-класс
    CLAUDE.md), включая провал окна 504608015 «NMAX 155 на 5 дней — 2 245 ฿ (449 ฿/день)»."""

    def _kinds(self, text):
        """{kind: {values}} для удобных проверок по классу."""
        d = {}
        for f in suggest.extract_money_figures(text):
            d.setdefault(f["kind"], set()).add(f["value"])
        return d

    def test_golden_window_504608015(self):
        # ДОСЛОВНЫЙ живой провал: итог за период 2245, суточная ставка 449; «155» (модель) и
        # «5» (срок) деньгами НЕ считаются
        d = self._kinds("NMAX 155 на 5 дней — 2 245 ฿ (449 ฿/день)")
        self.assertIn(2245, d.get("total", set()))
        self.assertIn(449, d.get("rate", set()))
        all_vals = {v for vs in d.values() for v in vs}
        self.assertNotIn(155, all_vals)      # номер модели — не деньги
        self.assertNotIn(5, all_vals)        # счётчик срока — не деньги

    def test_correct_quote_period_total(self):
        # корректный ответ с period-total из quote (1685) вместо наивного 449×5
        d = self._kinds("NMAX 155 на 5 дней — 1 685 ฿ (337 ฿/день)")
        self.assertIn(1685, d.get("total", set()))
        self.assertIn(337, d.get("rate", set()))

    def test_deposit_classified(self):
        d = self._kinds("Итого 2245 ฿ за 5 дней, депозит 3000 бат.")
        self.assertIn(2245, d.get("total", set()))
        self.assertIn(3000, d.get("deposit", set()))

    def test_deposit_without_currency(self):
        # «депозит 3000» без символа валюты — контекст депозита достаточен
        d = self._kinds("Депозит 3000, оплата при получении.")
        self.assertIn(3000, d.get("deposit", set()))

    def test_rate_glued_currency(self):
        # «449฿/день» без пробела — терпимость к слитному формату
        d = self._kinds("Аренда 449฿/день.")
        self.assertIn(449, d.get("rate", set()))

    def test_rate_in_words_per_day(self):
        # «в день» словами вместо «/день»
        d = self._kinds("449 бат в день, депозит 3000 бат.")
        self.assertIn(449, d.get("rate", set()))
        self.assertIn(3000, d.get("deposit", set()))

    def test_bare_number_tolerated(self):
        # голое «3000» без валюты и контекста — извлекаем как денежную сумму
        d = self._kinds("3000")
        self.assertIn(3000, d.get("amount", set()))

    def test_thousand_separators(self):
        # разделители тысяч: пробел, запятая, точка — все дают одно число
        for s in ("2 245 ฿", "2,245 ฿", "2.245 ฿"):
            d = self._kinds(s)
            all_vals = {v for vs in d.values() for v in vs}
            self.assertIn(2245, all_vals, s)

    def test_english_answer(self):
        d = self._kinds("NMAX 155 for 5 days — 1 685 THB (337 THB/day)")
        self.assertIn(1685, d.get("total", set()))
        self.assertIn(337, d.get("rate", set()))
        all_vals = {v for vs in d.values() for v in vs}
        self.assertNotIn(155, all_vals)
        self.assertNotIn(5, all_vals)

    def test_day_count_not_money(self):
        # «на 7 дней» — 7 счётчик срока, не деньги; ставка 449 всё же извлекается
        d = self._kinds("NMAX на 7 дней — 449 ฿/день, депозит 3000 ฿.")
        all_vals = {v for vs in d.values() for v in vs}
        self.assertNotIn(7, all_vals)
        self.assertIn(449, d.get("rate", set()))
        self.assertIn(3000, d.get("deposit", set()))

    def test_model_cc_not_money(self):
        # объём двигателя «155 cc» и номер модели — не деньги
        d = self._kinds("NMAX 155 cc — отличный выбор.")
        self.assertEqual(suggest.extract_money_figures("NMAX 155 cc — отличный выбор."), [])
        self.assertEqual(d, {})

    def test_empty_and_none(self):
        self.assertEqual(suggest.extract_money_figures(""), [])
        self.assertEqual(suggest.extract_money_figures(None), [])

    def test_order_preserved_and_raw(self):
        figs = suggest.extract_money_figures("на 5 дней — 2 245 ฿ (449 ฿/день)")
        self.assertEqual([f["value"] for f in figs], [2245, 449])   # порядок появления
        self.assertEqual(figs[0]["value"], 2245)
        self.assertTrue(figs[0]["raw"].startswith("2"))             # raw — исходный токен


class TestPriceSheet(unittest.TestCase):
    """Прайс по всему парку: детект намерения, детерминированный рендер день/7/месяц из ЖИВОГО
    формата ячеек Bridge, капы в месячной колонке, allowlist-фильтр (CLICK/не-в-парке), анти-луп.
    Bridge замокан getter'ом — боевой Календарь НЕ трогаем."""

    # тариф по модели: (day_total, week_total, month_total, deposit, cap_active, cap_price)
    TAR = {
        "NMAX 155": (450, 2800, 9000, 5000, True, 8500),      # месяц 9000>cap8500 → «от 8500»
        "ADV 350": (749, 4928, 14606, 7000, True, 10900),     # месяц капнут
        "CB 300R": (757, 4716, 12491, 15000, True, 9900),     # мото, месяц капнут
        "XMAX 300": (700, 4200, 13000, 7000, False, 20000),   # кап НЕ активен → сумма месяца
    }
    FLEET_NAMES = ["NMAX 155CC BLACK PHUKET 4255", "ADV 350CC BLACK PHUKET 5849",
                   "CB 300CC R 9011", "XMAX 300CC GREY PHUKET 4246",
                   "CLICK 125CC PHUKET 5580"]   # CLICK физически в парке, но НЕ сдаём

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

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
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    # Реальное сообщение клиента (дословно, 21:11) — провалившийся живой тест 68cfc31.
    # Правило-класс: фразы для тестов детекта = РЕАЛЬНЫЕ клиентские, не идеализированные.
    LIVE_PHRASE = ("Какие марки и модели байков вы предлагаете? Какие у вас цены на аренду? "
                   "(Стоимость за день, неделю и месяц для разных моделей.) Требуется ли депозит?")

    # ---- ЭТАП 2: детект намерения (позитив/негатив) ----
    def test_detect_positive(self):
        for s in [# старые явные формы
                  "цены на все модели на 15.07-15.08", "пришлите прайс", "прайс-лист по всему парку",
                  "сколько стоит аренда всех байков", "all models price for a month", "price list please",
                  "дайте цены по всем моделям",
                  # ЖИВАЯ фраза клиента + парафразы RU/EN (перечень моделей / цены-в-целом)
                  self.LIVE_PHRASE,
                  "какие модели байков у вас есть?",
                  "сколько стоит аренда байка в день и в неделю?",
                  "что по ценам на прокат скутеров?",
                  "подскажите стоимость аренды на месяц",
                  "какие у вас расценки на прокат?",
                  "what bikes do you have?",
                  "what are your rental prices?",
                  "how much is the rental per day, week and month?",
                  "price per day, week or month for different models?"]:
            self.assertTrue(suggest._asks_price_sheet(s.lower(), s.lower()), s)

    def test_detect_negative(self):
        for s in [# явная одна модель / нет цены-парка
                  "сколько стоит NMAX", "цена на ADV350 на месяц", "всё включено в цену?",
                  "у вас все байки новые?", "какой депозит", "можно два байка?",
                  # одна названная модель → точечный quote, приветствие, ТОЛЬКО депозит
                  "сколько стоит nmax на неделю", "какие цены на nmax?",
                  "здравствуйте!", "добрый день, вы работаете?",
                  "нужен ли депозит?", "какой залог?"]:
            self.assertFalse(suggest._asks_price_sheet(s.lower(), s.lower()), s)

    def test_live_phrase_end_to_end_yields_grid_no_date_question(self):
        # ЖИВАЯ фраза клиента дословно → hints.price_sheet_q True → сетка СРАЗУ, без вопроса про даты/модель.
        hints = suggest.extract_booking_hints(f"[клиент]: {self.LIVE_PHRASE}",
                                              today=datetime.date(2026, 7, 11))
        self.assertTrue(hints["price_sheet_q"])          # детект на реальной фразе сработал
        self.assertIsNone(hints["model"])                # конкретной модели нет → не точечный quote
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)            # сетка по парку
        self.assertIn("• Сутки: 450 ฿", note)              # реальные цифры сразу
        self.assertNotIn("Попроси", note)                # НЕ просим даты
        self.assertNotIn("НЕ называй НИКАКУЮ цену", note)  # НЕ ушли в гейт дат

    def test_live_2227_prior_model_mention_still_yields_grid(self):
        # КОРЕНЬ живого провала 22:27 (код aae1ed2): та же прайс-фраза, но клиент РАНЬШЕ в окне
        # упомянул модель («интересует nmax»). Старый модель-гард сканировал ВСЁ окно и глушил
        # сетку → бот спрашивал даты. Теперь каталог-вопрос сетку сохраняет. Реальный путь боя
        # (extract_booking_hints по многосообщенческому транскрипту), НЕ идеализированная одиночка.
        tr = ("[клиент]: привет, интересует nmax\n"
              "[менеджер]: Здравствуйте!\n"
              f"[клиент]: {self.LIVE_PHRASE}")
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])              # каталог-вопрос перебивает старую модель окна
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)            # сетка, а не гейт дат
        self.assertIn("• Сутки: 450 ฿", note)              # реальные цифры сразу
        self.assertNotIn("Попроси", note)                # НЕ просим даты
        self.assertNotIn("НЕ называй НИКАКУЮ цену", note)

    def test_live_2227_two_message_first_contact_yields_grid(self):
        # Первое обращение ДВУМЯ сообщениями: сперва «хочу adv», следом каталог+цены — тоже сетка.
        tr = ("[клиент]: здравствуйте, хочу adv\n"
              f"[клиент]: {self.LIVE_PHRASE}")
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])

    def test_catalog_question_with_model_example_yields_grid(self):
        # Каталог-вопрос в ОДНОМ сообщении с моделью-примером → всё равно сетка (смещение к показу).
        for s in ["какие модели предлагаете, например nmax, и какие цены?",
                  "what models do you have, like adv, and prices?"]:
            self.assertTrue(suggest._asks_price_sheet(s.lower(), s.lower()), s)

    def test_pointed_single_model_in_window_stays_negative(self):
        # Точечный диалог по ОДНОЙ модели без каталог-вопроса — сетку НЕ включаем (негатив цел),
        # даже если ценовые слова размазаны по окну.
        self.assertFalse(suggest._asks_price_sheet("а на неделю?", "сколько стоит nmax а на неделю?"))
        self.assertFalse(suggest._asks_price_sheet("какие цены на nmax?", "какие цены на nmax?"))

    def test_hints_flag_set_on_episode(self):
        h = suggest.extract_booking_hints("[клиент]: нужны цены на все модели на 15.07-15.08")
        self.assertTrue(h["price_sheet_q"])
        self.assertTrue(h["has_dates"])

    # ---- ЭТАП 1+3: детерминированный рендер + allowlist-фильтр ----
    def test_rows_exclude_click_and_out_of_park(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        models = [r["model"] for r in rows]
        self.assertIn("NMAX 155", models)
        self.assertIn("CB 300R", models)          # мото матчится (CC-стрип)
        self.assertNotIn("CLICK 125", models)     # несдаваемая — исключена
        for m in ("PCX 150", "REBEL 300", "XSR 900", "R7"):
            self.assertNotIn(m, models)           # не-в-парке отсутствуют

    def test_render_deterministic_numbers(self):
        # ЭТАП-формат: карточка на байк (заголовок + Сутки/Неделя/Месяц + Депозит), цифры из quote.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("NMAX 155\n• Сутки: 450 ฿\n• Неделя (7 дней): 2800 ฿\n"
                      "• Месяц: от 8500 ฿\n• Депозит: 5000 ฿ / паспорт", block)
        self.assertIn("ADV 350\n• Сутки: 749 ฿\n• Неделя (7 дней): 4928 ฿\n"
                      "• Месяц: от 10900 ฿\n• Депозит: 7000 ฿ / паспорт", block)

    def test_render_blank_line_between_bike_cards(self):
        # между карточками моделей — ПУСТАЯ строка; внутри карточки пустых строк нет (карточка цельная).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("\n\n", block)                                     # разделитель-пустая строка есть
        cards = block.split("\n\n")
        self.assertGreaterEqual(len(cards), 2)                           # ≥2 модели → ≥2 карточки
        for c in cards:
            self.assertNotIn("\n\n", c)                                  # карточка цельная (без пустых строк)
            self.assertTrue(c.splitlines()[0].strip())                   # первая строка карточки — модель

    def test_month_cap_applied_only_when_over_cap(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        # XMAX кап НЕ активен → показываем СУММУ месяца (13000), не «от»
        self.assertIn("XMAX 300\n• Сутки: 700 ฿\n• Неделя (7 дней): 4200 ฿\n• Месяц: 13000 ฿", block)
        self.assertNotIn("• Месяц: от 13000 ฿", block)

    def test_month_cap_en_prefix(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "en")
        self.assertIn("• Month: from 8500 ฿", block)    # EN: «from», не «от»
        self.assertIn("NMAX 155\n• Daily: 450 ฿\n• Week (7 days): 2800 ฿\n"
                      "• Month: from 8500 ฿\n• Deposit: 5000 ฿ / passport", block)
        self.assertNotIn("месяц", block)                # RU-строк нет
        self.assertNotIn("Сутки", block)

    # ---- ЭТАП 2: анти-луп в note (новый класс: блок в скобках, инструкция — метка) ----
    def test_note_has_numbers_no_promise_no_reask(self):
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("450 ฿", note)                    # реальные цифры в блоке (внутри скобок)
        self.assertIn("[PRICE_SHEET]", note)            # инструкция про метку для LLM
        self.assertIn("ДОСЛОВНО", note)                 # инвариант: вставит КОД дословно
        self.assertIsNotNone(suggest._sheet_block_from_note(note))   # блок извлекаем для сборки

    def test_note_no_dates_yields_sheet_no_date_question(self):
        # прайс-интент БЕЗ дат → сетка СРАЗУ (день/7/месяц), НИ ОДНОГО вопроса про даты.
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "has_dates": False}, lang="ru",
            getter=self._getter(), today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("• Сутки: 450 ฿", note)              # реальные цифры сразу, без гейта дат
        self.assertNotIn("Попроси", note)                # НЕ просим даты у клиента
        self.assertIn("старт завтра", note)              # дефолтный якорь = ближайшая дата
        self.assertIn("12.07.2026", note)                # завтра от 11.07 (инъекция today)
        self.assertIn("НЕ переспрашивай даты", note)     # анти-луп: даты не блокируют выдачу

    def test_dates_in_history_used_for_anchor(self):
        # даты в истории (15.07–15.08) → расчёт по ним (якорь = дата из диалога, не «завтра»).
        hints = suggest.extract_booking_hints(
            "[клиент]: нужны цены на все модели на 15.07-15.08", today=datetime.date(2026, 7, 11))
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        self.assertIn("Дата отсчёта: 15.07.2026", note)  # якорь из истории
        self.assertIn("• Сутки: 450 ฿", note)
        self.assertNotIn("старт завтра", note)           # не дефолтный якорь

    def test_min_term_line_ru_and_en(self):
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        ru = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        self.assertIn("Минимальный срок: скутеры от 5 дней, мотоциклы от 3", ru)
        en = suggest.build_pricing_note(hints, lang="en", getter=self._getter())
        self.assertIn("scooters from 5 days, motorcycles from 3", en)

    def test_min_term_line_from_real_constants(self):
        # строка мин-срока строится из SCOOTER_MIN_DAYS/MOTO_MIN_DAYS, не из литералов.
        self.assertEqual((suggest.SCOOTER_MIN_DAYS, suggest.MOTO_MIN_DAYS), (5, 3))
        line = suggest._sheet_min_term_line("ru")
        self.assertIn(f"скутеры от {suggest.SCOOTER_MIN_DAYS} дней", line)
        self.assertIn(f"мотоциклы от {suggest.MOTO_MIN_DAYS}", line)

    def test_season_note_low_from_quote(self):
        # кап активен в моке → СЛУЖЕБНАЯ пометка низкого сезона выведена из живого quote (наличие
        # сезона — не хардкод); конец сезона — документированная граница парка «до 31 октября».
        # Шаг 4/7 #253: пометка в квадратных скобках (служебный канал модератора).
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=self._getter())
        self.assertEqual(suggest._season_service_note(note),
                         "[сезон: низкий, цены действуют до 31 октября]")

    def test_season_note_en(self):
        # EN-аналог служебной сезонной пометки низкого сезона.
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="en", getter=self._getter())
        self.assertEqual(suggest._season_service_note(note),
                         "[season: low, prices valid until 31 October]")

    def test_season_note_not_in_client_sheet_block(self):
        # Шаг 4/7 #253: сезонность УБРАНА из клиентского тела — в прайс-блоке (что вставится клиенту
        # ДОСЛОВНО) ни «низкого сезона», ни «31 октября» больше нет; сезон — только служебной пометкой.
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=self._getter())
        block = suggest._sheet_block_from_note(note)
        self.assertIn("NMAX 155", block)                 # карточки на месте
        self.assertNotIn("сезон", block.lower())         # сезонности в клиентском теле нет
        self.assertNotIn("31 октября", block)

    def test_season_note_absent_when_no_cap(self):
        # ни у одной модели кап не активен → сезонную пометку НЕ утверждаем.
        def getter(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            bk = suggest._bike_key(bike)
            key = next((k for k in self.TAR if suggest._bike_key(k) in bk), None)
            if key is None:
                return {"ok": False}
            d1, d7, d30, dep, _ca, _cp = self.TAR[key]
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": False,
                    "cap_price": None, "text": f"{bike} {days}d {total}"}}
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=getter)
        self.assertIn("ПРАЙС ПО ПАРКУ", note)            # сетка всё равно есть
        self.assertEqual(suggest._season_service_note(note), "")  # служебной пометки сезона нет
        self.assertNotIn("31 октября", note)             # и конец сезона не называем

    def test_month_cap_reflected_in_note(self):
        # кап низкого сезона отражён в месячной колонке промпт-блока («от <cap>»).
        note = suggest.build_pricing_note(
            {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True},
            lang="ru", getter=self._getter())
        self.assertIn("• Месяц: от 8500 ฿", note)        # NMAX капнут (кап в месячной ячейке карточки)

    def test_numbers_come_only_from_quote(self):
        # инвариант: числа в блоке = суммы из quote (код подставляет, LLM не трогает).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        for total in (450, 2800, 749, 4928, 700, 4200):   # day/week totals из TAR
            self.assertIn(f"{total} ฿", block)

    def test_anti_loop_forbids_date_question_on_price(self):
        # ANTI_LOOP явно запрещает вопрос про даты при переданном ПРАЙС ПО ПАРКУ.
        self.assertIn("вопрос про даты клиенту НЕ задавай", suggest.ANTI_LOOP_NOTE)
        self.assertIn("ПРАЙС ПО ПАРКУ", suggest.ANTI_LOOP_NOTE)

    def test_note_unavailable_no_numbers_no_reask(self):
        empty = lambda p: {"ok": False}                 # Bridge не отдаёт цифр
        note = suggest.build_pricing_note({"price_sheet_q": True, "iso_start": "2026-07-15",
                                           "has_dates": True}, lang="ru", getter=empty)
        self.assertIn("недоступны", note)
        self.assertNotIn("450", note)
        self.assertNotIn("฿", note)

    # ---- база: ANTI_LOOP всегда в промпте, инварианты целы ----
    def test_anti_loop_in_base_prompt(self):
        sysp = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("БЕЗ ПЕРЕСПРОСОВ", sysp)
        self.assertIn("НЕ обещай", sysp)
        # ценовая политика и критфакты целы (f0b9316 / инварианты)
        self.assertIn("ЦЕНОВАЯ ПОЛИТИКА", sysp)
        self.assertIn("CLICK 125", sysp)

    def test_sheet_block_hidden_from_prompt_marker_instead(self):
        # НОВЫЙ класс (вёрстка 21:36 #276): сетку вставляет КОД — из промпта LLM блок ВЫРЕЗАН
        # (переписать нечего по построению), вместо него метка-инструкция [PRICE_SHEET].
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note=note)
        self.assertNotIn("• Сутки: 450 ฿", sysp)        # цифры сетки LLM НЕ видит
        self.assertNotIn("<<<SHEET>>>", sysp)           # служебные скобки в промпт не текут
        self.assertNotIn("<<<SEASON>>>", sysp)          # сезонные служебные скобки тоже
        self.assertNotIn("низкий", sysp.lower())        # LLM сезонность не видит → не выдаст клиенту
        self.assertIn("[PRICE_SHEET]", sysp)            # метка-инструкция на месте
        self.assertIn("БЕЗ ПЕРЕСПРОСОВ", sysp)
        # а извлечённый блок для сборки — дословный, с цифрами
        block = suggest._sheet_block_from_note(note)
        self.assertIn("• Сутки: 450 ฿", block)

    def test_golden_client_text_free_of_service_notes(self):
        # ГОЛДЕН шаг 4/7 #253: клиентский текст (тело ответа) НЕ содержит «собрано:» и служебных
        # пометок ([уточнить:…]/[собрано:…]/[сезон:…]); сезонность и статус собранного уходят
        # модератору отдельным служебным каналом. Собран прайс низкого сезона + собранные факты.
        tr = ("[клиент]: какие модели и какие цены на аренду?\n"
              "[клиент]: вот моя локация https://maps.google.com/?q=7.9,98.3\n"
              "[клиент]: +66 812345678")
        hints = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter())
        self.assertNotEqual(suggest._season_service_note(note), "")  # предпосылка: низкий сезон есть

        def llm(system, user):
            return "Актуальный прайс по нашему парку:\n[PRICE_SHEET]\nПодскажите модель и даты."

        d = suggest.generate_draft(tr, "ru", "FAQ", pricing_note=note, call_llm=llm)
        # служебная часть карточки (полный черновик) — пометки ДЛЯ модератора видны
        self.assertIn("[сезон: низкий, цены действуют до 31 октября]", d)
        self.assertIn("[собрано:", d)
        # клиентский текст — тело БЕЗ служебных пометок
        client = suggest.client_facing_text(d)
        self.assertNotIn("собрано:", client)
        self.assertNotIn("[сезон", client)
        self.assertNotIn("[уточнить", client)
        self.assertNotIn("[собрано", client)
        self.assertNotIn("низкого сезона", client)
        self.assertNotIn("31 октября", client)
        self.assertIn("Актуальный прайс", client)        # тело ответа цело
        self.assertIn("NMAX 155", client)                # прайс-карточки на месте


class TestPriceSheetMinAcrossVariants(unittest.TestCase):
    """Правило-класс: минимум-по-вариантам для НЕ-XMAX-моделей (несколько живых юнитов одной модели →
    каждая колонка сетки = МИНИМУМ по вариантам через ЖИВОЙ quote). XMAX — ИСКЛЮЧЕНИЕ (родитель 243,
    шаг 1/7): два поколения = ДВА продукта отдельными строками («XMAX 300» старое / «XMAX 300 New Gen»
    новое 2023+), минимум-по-вариантам для XMAX СНЯТ (иначе наценка нового поколения пропала бы —
    старый провал показывал единый «от 8900»). Bridge замокан getter'ом (Календарь НЕ трогаем),
    цифры приходят ТОЛЬКО из мок-quote своих юнитов, в коде НЕ хардкодятся."""

    # 3 живых СТАРЫХ XMAX (2020-2022, дешевле) + 1 НОВЫЙ (2023+, дороже) + одиночная NMAX для регресса.
    OLD_XMAX = ["XMAX 300CC GREY PHUKET 4246", "XMAX 300CC BLUE PHUKET 4247",
                "XMAX 300CC BLACK PHUKET 4248"]
    NEW_XMAX = ["XMAX 300CC NEW 2023 PHUKET 7701"]
    FLEET_NAMES = OLD_XMAX + NEW_XMAX + ["NMAX 155CC BLACK PHUKET 4255"]

    # тариф: (day_total, week_total, month_total, deposit, cap_active, cap_price)
    OLD = (790, 4700, 23700, 5000, True, 8900)     # старый XMAX: кап 8900, депозит 5000
    NEW = (939, 5600, 28170, 7000, True, 9900)     # новый XMAX: кап 9900, депозит 7000
    NMAX = (450, 2800, 9000, 5000, True, 8500)     # одиночная модель — регресс (не должна измениться)

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _tar_for(self, bike):
        """Тариф ПО ИМЕНИ юнита (не только по модели): новый 2023+ дороже старого; NMAX — своя."""
        if "NMAX" in bike.upper():
            return self.NMAX
        if "2023" in bike or "NEW" in bike.upper():
            return self.NEW
        return self.OLD

    def _getter(self):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            d1, d7, d30, dep, ca, cp = self._tar_for(bike)
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    def test_xmax_grid_splits_old_and_new_gen(self):
        # ГОЛДЕН (родитель 243, шаг 1/7): живы старые XMAX (790/8900/деп5000) рядом с новым
        # (939/9900/деп7000) → в сетке ДВЕ отдельные строки со СВОИМИ цифрами (минимум НЕ схлопывает).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("XMAX 300\n• Сутки: 790 ฿\n• Неделя (7 дней): 4700 ฿\n"
                      "• Месяц: от 8900 ฿\n• Депозит: 5000 ฿ / паспорт", block)       # старое поколение
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿\n• Неделя (7 дней): 5600 ฿\n"
                      "• Месяц: от 9900 ฿\n• Депозит: 7000 ฿ / паспорт", block)       # новое поколение
        # обе строки живы и цены РАЗНЫЕ (не единый минимум)
        self.assertIn("790 ฿", block)
        self.assertIn("939 ฿", block)

    def test_live_phrase_grid_shows_both_xmax_gens(self):
        # Живой прогон «какие модели и цены» (дословная фраза клиента) → в сетке ОБА поколения XMAX.
        phrase = ("Какие марки и модели байков вы предлагаете? Какие у вас цены на аренду? "
                  "(Стоимость за день, неделю и месяц для разных моделей.) Требуется ли депозит?")
        hints = suggest.extract_booking_hints(f"[клиент]: {phrase}", today=datetime.date(2026, 7, 11))
        self.assertTrue(hints["price_sheet_q"])
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("XMAX 300\n• Сутки: 790 ฿", note)             # старое поколение
        self.assertIn("• Месяц: от 8900 ฿", note)
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿", note)     # новое поколение
        self.assertIn("• Месяц: от 9900 ฿", note)

    def test_single_variant_model_unchanged(self):
        # РЕГРЕСС: одиночная модель (один вариант в парке) — цифры БЕЗ изменений (мин по одному == он сам).
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("NMAX 155\n• Сутки: 450 ฿\n• Неделя (7 дней): 2800 ฿\n"
                      "• Месяц: от 8500 ฿\n• Депозит: 5000 ฿ / паспорт", block)

    def test_pointwise_xmax_default_new_gen_only(self):
        # ГОЛДЕН (кейс @cryptopeppa 03:13 15.07): БЕЗ запроса про поколение точечный quote по XMAX
        # даёт ОДНУ цену New Gen — без старого поколения, без годов, без «старый/новый» вопроса.
        # Реальные фразы клиента (правило-класс: golden-тест детекта = дословное сообщение).
        for phrase in ("XMAX 300 на 5 дней с 15 июля",
                       "сколько стоит xmax на неделю с 15 июля?",
                       "аренда xmax 15.07-22.07 сколько?",
                       "цена xmax на неделю с 15 июля",
                       "почём xmax на неделю с 15 июля?",
                       "xmax на неделю с 15 июля какая цена?"):
            hints = suggest.extract_booking_hints(f"[клиент]: {phrase}",
                                                  today=datetime.date(2026, 7, 11))
            self.assertFalse(hints["old_gen_q"], phrase)            # прежнее поколение НЕ запрошено
            note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                              today=datetime.date(2026, 7, 11))
            self.assertIn("New Gen", note, phrase)                  # актуальное поколение в ответе
            self.assertNotIn("- XMAX 300:", note, phrase)           # строки старого поколения НЕТ
            self.assertNotIn("4700", note, phrase)                  # цифра старого поколения не течёт
            # квотирован ТОЛЬКО новый юнит (New Gen) — цена актуального поколения (5600 за неделю / 939 сут)
            self.assertTrue("5600" in note or "939" in note, phrase)

    def test_pointwise_xmax_explicit_old_gen_shows_both(self):
        # ГОЛДЕН (кейс @cryptopeppa): ЯВНЫЙ запрос «а старый xmax есть?» (репликой ПОЗЖЕ дат первичной
        # брони) → ОБЕ строки-поколения со СВОИМИ цифрами (старое 4700 vs New Gen 5600). Года допустимы.
        for older in ("а старый xmax есть?",
                      "а прежнее поколение xmax есть?",
                      "есть xmax старой версии?",
                      "интересует xmax 2021 года",
                      "is there an old xmax?"):
            tr = f"[клиент]: xmax на неделю с 15 июля какая цена?\n[клиент]: {older}"
            hints = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
            self.assertTrue(hints["old_gen_q"], older)              # прежнее поколение запрошено ЯВНО
            note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                              today=datetime.date(2026, 7, 11))
            self.assertIn("- XMAX 300: ", note, older)             # строка старого поколения
            self.assertIn("- XMAX 300 New Gen: ", note, older)     # строка нового поколения
            self.assertIn("7d 4700", note, older)                  # неделя старого — своя цифра
            self.assertIn("7d 5600", note, older)                  # неделя нового — РАЗНАЯ цифра
            # старую строку квотировал ТОЛЬКО старый юнит, новую — ТОЛЬКО новый (поколения не смешаны)
            old_line = next(l for l in note.splitlines() if l.startswith("- XMAX 300:"))
            new_line = next(l for l in note.splitlines() if l.startswith("- XMAX 300 New Gen:"))
            self.assertNotIn("NEW", old_line.upper())
            self.assertIn("NEW", new_line.upper())

    def test_pointwise_non_xmax_single_line_unchanged(self):
        # РЕГРЕСС: точечный quote по НЕ-XMAX (NMAX) — одна строка _wrap_single, без разворота вариантов.
        hints = suggest.extract_booking_hints("[клиент]: сколько стоит nmax на неделю с 15 июля?",
                                              today=datetime.date(2026, 7, 11))
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ЦЕНА из Календаря", note)          # одиночная модель → _wrap_single
        self.assertNotIn("- NMAX", note)                  # НЕ развёрнута в bullets
        self.assertNotIn("New Gen", note)                 # у NMAX поколений нет
        self.assertNotIn("ЗА КАЖДЫЙ", note)               # одна единица — пометки «за каждый» нет

    def test_pointwise_pair_xmax_price_per_each(self):
        # ГОЛДЕН #365 шаг 2/7 + правило поколений (кейс @cryptopeppa): «пару скутеров xmax 16-24 июля»
        # БЕЗ запроса про поколение → New Gen ОДНОЙ строкой, цена И депозит ЗА КАЖДЫЙ юнит; общий итог
        # за 2 шт. НЕ выдуман. Дословная фраза клиента + парафразы RU (правило-класс CLAUDE.md).
        for phrase in ("пару скутеров xmax 16-24 июля",
                       "два xmax на 16-24 июля",
                       "нужны два скутера xmax с 16 по 24 июля",
                       "2 скутера xmax с 16 по 24 июля",
                       "хотим пару xmax на 16-24.07"):
            hints = suggest.extract_booking_hints(f"[клиент]: {phrase}",
                                                  today=datetime.date(2026, 7, 11))
            self.assertEqual(hints["units_count"], 2, phrase)     # детект N юнитов одной модели
            self.assertFalse(hints["old_gen_q"], phrase)          # прежнее поколение НЕ запрошено
            note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                              today=datetime.date(2026, 7, 11))
            self.assertIn("ЗА КАЖДЫЙ", note, phrase)              # цена/депозит помечены «за каждый»
            self.assertIn("New Gen", note, phrase)               # только актуальное поколение
            self.assertNotIn("- XMAX 300:", note, phrase)        # строки старого поколения НЕТ
            self.assertNotIn("790", note, phrase)                # цена старого поколения не течёт
            self.assertIn("939", note, phrase)                   # цена нового поколения из Bridge
            self.assertIn("7000", note, phrase)                  # депозит нового из Bridge
            # ВЫДУМАННОГО общего итога за 2 шт. в блоке НЕТ (код не суммирует и не умножает):
            self.assertNotIn("1878", note, phrase)               # 2×939 не выдумано
            self.assertNotIn("14000", note, phrase)              # 2×7000 (депозит) не выдумано
            # #365 родитель4 шаг3/6: живая цена New Gen едет в служебный quote-блок → strategy-путь
            # донесёт её КОДОМ; пометка «за каждый» и в блоке, итог за 2 шт. по-прежнему не выдуман.
            block = suggest._quote_block_from_note(note)
            self.assertIsNotNone(block, phrase)
            self.assertIn("939", block, phrase)                  # новое поколение — своя цена
            self.assertIn("ЗА КАЖДЫЙ", block, phrase)            # цена/депозит за каждый юнит
            self.assertNotIn("1878", block, phrase)              # итог за 2 шт. не выдуман и в блоке

    def test_pointwise_quote_tail_scrubs_gen_year(self):
        # ГОЛДЕН (родитель 22, шаг 3/7): J-текст Bridge несёт СЫРОЕ имя юнита с годом поколения
        # («XMAX 300CC NEW 2023 PHUKET 7701»); в quote-хвост черновика год НЕ течёт — поколение несёт
        # МЕТКА New Gen (как в клиентском теле), а год выпуска убран. Реальные фразы клиента + парафразы.
        for phrase in ("сколько стоит xmax 16-24 июля",
                       "xmax с 16 по 24 июля почём?",
                       "цена на xmax 16-24 июля",
                       "аренда xmax 16-24.07 сколько",
                       "почём xmax на 16-24 июля?"):
            hints = suggest.extract_booking_hints(f"[клиент]: {phrase}",
                                                  today=datetime.date(2026, 7, 11))
            self.assertFalse(hints["old_gen_q"], phrase)          # прежнее поколение НЕ запрошено
            note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                              today=datetime.date(2026, 7, 11))
            block = suggest._quote_block_from_note(note)
            self.assertIsNotNone(block, phrase)                   # quote-блок собран
            self.assertIn("New Gen", block, phrase)               # поколение несёт метка (New Gen)
            self.assertIn("939", block, phrase)                   # живая цена Bridge цела
            for yr in ("2020", "2021", "2022", "2023", "2024"):   # ГОД поколения в хвост НЕ утёк
                self.assertNotIn(yr, block, f"{phrase}: год {yr} утёк в quote-хвост:\n{block}")

    def test_pointwise_pair_xmax_explicit_old_gen_both(self):
        # Пара XMAX + ЯВНЫЙ запрос про прежнее поколение → ОБА поколения раздельными строками, цена/
        # депозит «за каждый», итог за 2 шт. не выдуман. Года по явной просьбе клиента допустимы.
        # «пару…» — в ПОСЛЕДНЕЙ (новейшей) реплике: детект N юнитов читает только её; «старый» —
        # раньше в окне (old_gen_q сканирует всё окно newest+recent).
        tr = "[клиент]: а старый xmax тоже есть?\n[клиент]: пару скутеров xmax 16-24 июля"
        hints = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertEqual(hints["units_count"], 2)
        self.assertTrue(hints["old_gen_q"])
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("- XMAX 300: ", note)                      # оба поколения — раздельными строками
        self.assertIn("- XMAX 300 New Gen: ", note)
        self.assertIn("790", note)                               # старое поколение — своя цена
        self.assertIn("939", note)                               # новое поколение — своя цена
        self.assertIn("ЗА КАЖДЫЙ", note)
        self.assertNotIn("1580", note)                           # 2×790 не выдумано
        self.assertNotIn("1878", note)                           # 2×939 не выдумано

    def test_units_count_detect_real_phrases(self):
        # Правило-класс CLAUDE.md: детект на РЕАЛЬНОЙ фразе клиента + парафразы RU/EN + негативы.
        for phrase, n in (("пару скутеров xmax 16-24 июля", 2),
                          ("два xmax на 16-24 июля", 2),
                          ("нужны два скутера xmax", 2),
                          ("2 скутера xmax с 16 по 24 июля", 2),
                          ("хочу пару байков", 2),
                          ("two xmax scooters, july 16-24", 2),
                          ("нужно 3 скутера", 3)):
            h = suggest.extract_booking_hints(f"[клиент]: {phrase}", today=datetime.date(2026, 7, 11))
            self.assertEqual(h["units_count"], n, phrase)
        for phrase in ("сколько стоит xmax 16-24 июля",    # одна единица — не «за каждый»
                       "xmax на два дня",                   # «два дня» — срок аренды, не юниты
                       "здравствуйте!",                     # приветствие
                       "какой депозит на xmax?",            # только про депозит
                       "nmax и xmax на 16-24 июля"):        # разные модели, не N юнитов одной
            h = suggest.extract_booking_hints(f"[клиент]: {phrase}", today=datetime.date(2026, 7, 11))
            self.assertIsNone(h["units_count"], phrase)

    def test_min_helpers_pick_cheapest_column_independently(self):
        # Юнит на редьюсер: сутки/неделя — по сумме, месяц — по кап-«от», депозит — общий минимум.
        old = {"total": 23700, "deposit": 5000, "cap_active": True, "cap_price": 8900}
        new = {"total": 28170, "deposit": 7000, "cap_active": True, "cap_price": 9900}
        self.assertEqual(suggest._sheet_q_month(old), 8900)
        self.assertEqual(suggest._sheet_q_month(new), 9900)
        self.assertIs(suggest._sheet_min_variant([new, old], suggest._sheet_q_month), old)
        self.assertEqual(suggest._sheet_min_deposit({"month": [new, old]}), 5000)
        # без цап — величина месяца = сумма (min из сумм)
        self.assertEqual(suggest._sheet_q_month({"total": 13000, "cap_active": False}), 13000)


class TestPriceSheetManyVariantsRobust(unittest.TestCase):
    """Класс-голден живого регресса 20:59 (после 551987e): реальный парк = МНОГО юнитов на модель
    (~сотня живых quote на сетку). Требования класса: (1) битый юнит (quote кидает/молчит) НЕ валит
    сетку — пропуск с логом, колонки из живых остальных; (2) построение параллельное и ограничено
    дедлайном — на большом парке укладывается в разумную стену (последовательно 20:59 было 6м16с);
    (3) минимум-по-вариантам НЕ ослаблен."""

    # Парк как живой: XMAX 3 старых (кап 8900) + 6 новых (кап 9900), ADV 6 юнитов, NMAX одиночка.
    OLD_XMAX = [f"XMAX 300CC OLD-{i} PHUKET 400{i}" for i in range(3)]
    NEW_XMAX = [f"XMAX 300CC NEW-{i} PHUKET 870{i}" for i in range(6)]
    ADV = [f"ADV 350CC UNIT-{i} PHUKET 580{i}" for i in range(6)]
    FLEET_NAMES = OLD_XMAX + NEW_XMAX + ADV + ["NMAX 155CC BLACK PHUKET 4255"]
    OLD = (790, 4700, 23700, 5000, True, 8900)
    NEW = (939, 5600, 28170, 7000, True, 9900)
    ADVT = (749, 4928, 14606, 7000, True, 10900)
    NMAX = (450, 2800, 9000, 5000, True, 8500)

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def _getter(self, broken=(), slow=(), delay=0.0):
        """Мок Bridge: broken — юниты, чей quote КИДАЕТ; slow — юниты со сном delay (для дедлайна)."""
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET_NAMES]}}
            bike = params.get("bike", "")
            if any(b in bike for b in broken):
                raise RuntimeError(f"битый юнит {bike} (живой формат строки Лист1 сломал quote)")
            if any(s in bike for s in slow):
                time.sleep(delay)
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            up = bike.upper()
            tar = (self.NMAX if "NMAX" in up else self.ADVT if "ADV" in up
                   else self.NEW if "NEW" in up else self.OLD)
            d1, d7, d30, dep, ca, cp = tar
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    def test_broken_unit_skipped_grid_survives(self):
        # ГОЛДЕН класса: один старый XMAX бит (quote кидает) → строка старого поколения ЦЕЛА
        # (живы 2 других старых, 790/8900), New Gen тоже жив (939/9900), соседи не задеты.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter(broken=("OLD-1",)))
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("XMAX 300\n• Сутки: 790 ฿\n• Неделя (7 дней): 4700 ฿\n"
                      "• Месяц: от 8900 ฿\n• Депозит: 5000 ฿ / паспорт", block)
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿", block)
        self.assertIn("ADV 350\n• Сутки: 749 ฿", block)
        self.assertIn("NMAX 155\n• Сутки: 450 ฿", block)

    def test_all_old_units_broken_only_new_gen_row_survives(self):
        # ВСЕ старые XMAX биты → строка старого поколения выпадает (нет живых юнитов), остаётся
        # только «XMAX 300 New Gen» (939/9900) — честно из живых, не пустота и не обвал.
        rows = suggest.price_sheet("2026-07-15",
                                   getter=self._getter(broken=("OLD-0", "OLD-1", "OLD-2")))
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertIn("XMAX 300 New Gen\n• Сутки: 939 ฿", block)
        self.assertIn("• Месяц: от 9900 ฿", block)
        self.assertNotIn("XMAX 300\n• Сутки: 790", block)  # старое поколение выпало (все юниты биты)
        self.assertNotIn("XMAX 300\n• Сутки: 939", block)  # New Gen НЕ подменяет строку старого
        self.assertIn("NMAX 155\n• Сутки: 450 ฿", block)   # соседи не задеты

    def test_many_variants_build_is_parallel_fast(self):
        # Класс «разумное время»: 16 юнитов × 3 срока (48 quote) по 0.2с сна каждый. Последовательно
        # это 9.6с; пул (8 воркеров) обязан уложиться заметно быстрее. Порог щедрый — не флапает.
        t0 = time.time()
        rows = suggest.price_sheet("2026-07-15",
                                   getter=self._getter(slow=("XMAX", "ADV", "NMAX"), delay=0.2))
        dt = time.time() - t0
        self.assertTrue(rows)
        self.assertLess(dt, 6.0, f"параллельный билд занял {dt:.1f}с — пул не работает")

    def test_deadline_expired_units_skipped_not_hang(self):
        # Юниты, висящие ДОЛЬШЕ дедлайна, пропускаются: сетка выходит из успевших, без зависания.
        import unittest.mock as mock
        with mock.patch.object(suggest, "_SHEET_DEADLINE", 1):
            t0 = time.time()
            rows = suggest.price_sheet("2026-07-15",
                                       getter=self._getter(slow=("ADV",), delay=8.0))
            dt = time.time() - t0
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        self.assertLess(dt, 7.0, f"дедлайн не сработал: билд {dt:.1f}с")
        self.assertIn("XMAX 300", block)                   # успевшие модели в сетке
        self.assertNotIn("ADV 350\n• Сутки: 749 ฿", block)  # висящий юнит не дождались — без цифр


class TestSheetAwarePricePolicy(unittest.TestCase):
    """Класс-голден второй ноги живого регресса 20:59 (черновик #275): сетка ДОШЛА до промпта, но
    ценовая политика («Дат нет — сперва спроси даты», цена только из блока «ЦЕНА из Календаря»)
    конфликтовала с блоком «ПРАЙС ПО ПАРКУ» — LLM 9/10 переспрашивал даты/опыт, выбрасывая сетку.
    Фикс у источника: при sheet-режиме политика сама велит выдать прайс дословно без переспроса."""

    SHEET_NOTE = ("ПРАЙС ПО ПАРКУ из Календаря — приведи цифры И строки мин-срока/сезона ДОСЛОВНО…\n"
                  "XMAX 300\n• Сутки: 593 ฿\n• Месяц: от 8900 ฿")

    def test_sheet_mode_policy_orders_grid_not_date_question(self):
        p = suggest.make_system_prompt("faq", "ru", pricing_note=self.SHEET_NOTE)
        self.assertIn("ГОТОВЫЙ блок «ПРАЙС ПО ПАРКУ из Календаря»", p)
        self.assertIn("Даты НЕ переспрашивай", p)
        self.assertIn("Вопрос об опыте НЕ заменяет выдачу прайса", p)
        self.assertNotIn("Дат нет — сперва спроси даты", p)      # конфликт-источник УБРАН
        self.assertIn("приведи прайс-сетку из блока «ПРАЙС ПО ПАРКУ» ДОСЛОВНО", p)  # этап 1

    def test_sheet_mode_en_note_triggers_too(self):
        p = suggest.make_system_prompt("faq", "en",
                                       pricing_note="PARK PRICE LIST from the Calendar …\nXMAX …")
        self.assertIn("ГОТОВЫЙ блок «ПРАЙС ПО ПАРКУ из Календаря»", p)
        self.assertNotIn("Дат нет — сперва спроси даты", p)

    def test_regular_price_note_policy_unchanged(self):
        # РЕГРЕСС: обычный ценовой путь (ЦЕНА по датам / вообще без цены) — прежняя политика.
        for note in ("", "ЦЕНА из Календаря: XMAX 300 на 15.07–20.07 — 3500 ฿ …"):
            p = suggest.make_system_prompt("faq", "ru", pricing_note=note)
            self.assertIn("цену клиенту называй ТОЛЬКО если она передана ниже", p)
            self.assertIn("Дат нет — сперва спроси даты", p)
            self.assertNotIn("ГОТОВЫЙ блок «ПРАЙС ПО ПАРКУ", p)
            self.assertIn("Этап 1 — ЦЕНА: назови цену по датам из Календаря", p)


class TestSheetUntouchableBlock(unittest.TestCase):
    """Класс-голден вёрстки 21:36 (#276): сетка = НЕПРИКОСНОВЕННЫЙ блок. LLM цифры не видит и не
    переписывает; финал собирает КОД: intro + render_price_sheet ДОСЛОВНО + outro. Вёрстка —
    плоский текст: группы «Скутеры:»/«Мотоциклы:», карточка-на-байк, пустые строки, БЕЗ markdown."""

    ROWS = [
        {"model": "NMAX 155", "class": ("scooter", 5, "этот байк"), "bike": "NMAX 155CC X",
         "cells": {"day": {"total": 450}, "week": {"total": 2800},
                   "month": {"total": 9000, "cap_active": True, "cap_price": 8500}},
         "deposit": 3000},
        {"model": "XMAX 300", "class": ("scooter", 5, "этот байк"), "bike": "XMAX 300CC X",
         "cells": {"day": {"total": 790}, "week": {"total": 4700},
                   "month": {"total": 23700, "cap_active": True, "cap_price": 8900}},
         "deposit": 5000},
        {"model": "CB 300R", "class": ("moto", 3, "мотоциклы"), "bike": "CB 300CC R",
         "cells": {"day": {"total": 757}, "week": {"total": 4716},
                   "month": {"total": 12491, "cap_active": True, "cap_price": 9900}},
         "deposit": 15000},
    ]

    def test_render_groups_flat_no_markdown(self):
        # ГОЛДЕН вёрстки: группы, карточки, пустые строки, ноль markdown-символов.
        block = suggest.render_price_sheet(self.ROWS, "2026-07-15", "ru")
        self.assertNotIn("**", block)                          # сырой markdown = живой провал #276
        self.assertNotIn("__", block)
        self.assertIn("Скутеры:\n\nNMAX 155\n• Сутки: 450 ฿", block)
        self.assertIn("Мотоциклы:\n\nCB 300R\n• Сутки: 757 ฿", block)
        self.assertLess(block.index("Скутеры:"), block.index("Мотоциклы:"))   # порядок групп
        # карточка-на-байк + пустая строка между карточками внутри группы
        self.assertIn("• Депозит: 3000 ฿ / паспорт\n\nXMAX 300\n• Сутки: 790 ฿", block)
        self.assertIn("• Месяц: от 8900 ฿", block)             # цифры целы (регресс минимума)

    def test_render_groups_en(self):
        block = suggest.render_price_sheet(self.ROWS, "2026-07-15", "en")
        self.assertIn("Scooters:\n\nNMAX 155\n• Daily: 450 ฿", block)
        self.assertIn("Motorcycles:\n\nCB 300R", block)
        self.assertNotIn("**", block)

    def test_compose_marker_variants(self):
        # Сборка кодом: метка отдельной строкой / в скобках с точкой / инлайн — intro+блок+outro.
        block = "Скутеры:\n\nXMAX 300\n• Сутки: 790 ฿"
        for marker in ("[PRICE_SHEET]", "PRICE_SHEET", "[price sheet].", "«[PRICE_SHEET]»"):
            out = suggest.compose_sheet_draft(f"Здравствуйте!\n{marker}\nПодскажите модель.",
                                              block, "ru")
            self.assertEqual(out, "Здравствуйте!\n\n" + block + "\n\nПодскажите модель.", marker)

    def test_compose_inline_marker(self):
        block = "XMAX 300\n• Сутки: 790 ฿"
        out = suggest.compose_sheet_draft("Вот прайс: [PRICE_SHEET] Жду вопросов.", block, "ru")
        self.assertEqual(out, "Вот прайс:\n\n" + block + "\n\nЖду вопросов.")

    def test_compose_no_marker_fallback_deterministic(self):
        # LLM не выдал метку (или переписал прайс своим текстом) → его текст ОТБРАСЫВАЕМ,
        # каркас детерминированный, блок дословно. Сетка доходит ВСЕГДА.
        block = "XMAX 300\n• Сутки: 790 ฿\n• Месяц: от 8900 ฿"
        bad_llm = "XMAX 300 — 790 ฿/сутки, **дешево**"        # однострочник + markdown, без метки
        out = suggest.compose_sheet_draft(bad_llm, block, "ru")
        self.assertIn(block, out)                              # блок дословно
        self.assertNotIn("**", out)                            # LLM-переписывание не протекло
        self.assertIn("Актуальный прайс", out)
        out_en = suggest.compose_sheet_draft(bad_llm, block, "en")
        self.assertIn("Here is our current price list:", out_en)

    def test_generate_draft_assembles_by_code(self):
        # Уровень generate_draft: fake-LLM ставит метку → финал собран КОДОМ, блок дословный.
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        rows = self.ROWS
        with mock.patch.object(suggest, "price_sheet", return_value=rows):
            note = suggest.build_pricing_note(hints, lang="ru", getter=lambda p: {"ok": False})
        block = suggest._sheet_block_from_note(note)
        self.assertIsNotNone(block)
        fake = lambda system, user: "Здравствуйте! Вот наш прайс:\n[PRICE_SHEET]\nКакая модель интересна?"
        draft = suggest.generate_draft("[клиент]: цены?", "ru", "FAQ",
                                       pricing_note=note, call_llm=fake)
        self.assertIn(block, draft)                            # рендер ДОСЛОВНО в черновике
        self.assertTrue(draft.startswith("Здравствуйте! Вот наш прайс:"))
        # шаг 4/7 #253: хвост черновика — СЛУЖЕБНАЯ сезонная пометка (низкий сезон в ROWS), а
        # тело ответа клиенту (без пометок) заканчивается фразой концовки LLM.
        self.assertTrue(draft.rstrip().endswith("[сезон: низкий, цены действуют до 31 октября]"))
        self.assertTrue(suggest.client_facing_text(draft).endswith("Какая модель интересна?"))
        self.assertNotIn("[PRICE_SHEET]", draft)               # метка заменена
        self.assertNotIn("**", draft)

    def test_generate_draft_llm_rewrite_cannot_leak(self):
        # LLM вопреки всему переписал прайс однострочниками с ** и без метки → его текст отброшен,
        # финал = детерминированный каркас + блок дословно. Модель прайс не переписывает НИКОГДА.
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        with mock.patch.object(suggest, "price_sheet", return_value=self.ROWS):
            note = suggest.build_pricing_note(hints, lang="ru", getter=lambda p: {"ok": False})
        block = suggest._sheet_block_from_note(note)
        fake = lambda system, user: "**Скутеры**\n- XMAX 300 — 790 ฿/сутки, от 8900 ฿/месяц"
        draft = suggest.generate_draft("[клиент]: цены?", "ru", "FAQ",
                                       pricing_note=note, call_llm=fake)
        self.assertIn(block, draft)
        self.assertNotIn("**", draft)
        self.assertNotIn("790 ฿/сутки", draft)                 # однострочник LLM не протёк

    def test_regenerate_draft_same_class(self):
        # Перегенерация (СТРАТЕГИЯ-директива) — тот же класс сборки кодом.
        hints = {"price_sheet_q": True, "iso_start": "2026-07-15", "has_dates": True}
        with mock.patch.object(suggest, "price_sheet", return_value=self.ROWS):
            note = suggest.build_pricing_note(hints, lang="ru", getter=lambda p: {"ok": False})
        block = suggest._sheet_block_from_note(note)
        fake = lambda system, user: "Добрый день!\n[PRICE_SHEET]\nНа связи."
        draft = suggest.regenerate_draft("[клиент]: цены?", "ru", "FAQ", False,
                                         note, "мягче тон", call_llm=fake)
        self.assertIn(block, draft)
        self.assertNotIn("[PRICE_SHEET]", draft)


class TestDepositPassportChosen(unittest.TestCase):
    """#22 шаг4/7: детект выбора ПАСПОРТА как депозита (KB: деньги ЛИБО паспорт, не оба). Правило-класс
    CLAUDE.md: живые фразы клиента + парафразы RU/EN в позитивах, контрпримеры в негативах."""

    def test_positives(self):
        for phr in ("паспорт в залог оставлю",
                    "давайте депозит паспортом",
                    "можно вместо денег паспорт оставить?",
                    "оставлю паспорт вместо депозита",
                    "залог паспортом, наличные не хочу",
                    "passport as deposit is fine",
                    "can I leave my passport instead of the cash deposit?"):
            self.assertTrue(suggest._deposit_passport_chosen([phr]), phr)

    def test_negatives(self):
        # фото паспорта для брони (не выбор депозита) / вопрос про депозит без паспорта / выбор ДЕНЕГ /
        # приветствие / паспорт вне депозит-контекста
        for phr in ("пришлю фото паспорта для брони",
                    "какой депозит?",
                    "депозит 3000 нормально, внесу наличными",
                    "привет, какие цены?",
                    "паспорт готовлю к поездке"):
            self.assertFalse(suggest._deposit_passport_chosen([phr]), phr)

    def test_two_signals_must_share_one_message(self):
        # «фото паспорта» и «какой депозит?» в РАЗНЫХ репликах окна → НЕ выбор паспорта (ложь ушла).
        self.assertFalse(suggest._deposit_passport_chosen(
            ["какой депозит?", "фото паспорта уже отправил"]))

    def test_deposit_as_passport_replaces_sum(self):
        self.assertEqual(
            suggest._deposit_as_passport("NMAX 155 — 337 ฿/день, за 5 дней 1685 ฿, депозит 3000 ฿"),
            "NMAX 155 — 337 ฿/день, за 5 дней 1685 ฿, депозит: паспорт")
        # разные форматы суммы депозита (бат / THB) — тоже в паспорт, итог/ставка целы
        self.assertNotIn("3000", suggest._deposit_as_passport("итого 1685 ฿; депозит 3000 бат"))
        self.assertIn("депозит: паспорт", suggest._deposit_as_passport("депозит 3000 бат"))
        self.assertEqual(suggest._deposit_as_passport("deposit 7000 THB", "en"), "deposit: passport")
        # нет суммы депозита во фразе → дописываем явно
        self.assertEqual(suggest._deposit_as_passport("NMAX на 5 дней — 1685 ฿."),
                         "NMAX на 5 дней — 1685 ฿; депозит: паспорт")


class TestPointQuoteCodeBlock(unittest.TestCase):
    """#365 (родитель 4, шаг 2/6): ТОЧЕЧНЫЙ quote несёт цену Bridge в финал КОДОМ — как сетка
    PRICE_SHEET. strategy-перегенерация больше НЕ зависит от того, «донёс» ли LLM цифры: цена лежит
    в служебных скобках pricing_note (в промпт не течёт — вырезается), а compose_quote_draft вставляет
    её КОДОМ (метка [QUOTE] → точка вставки; цифры уже в тексте → не дублируем; цену LLM потерял →
    приклеиваем блок хвостом, fail-safe)."""

    FLEET = ["NMAX 155CC BLACK PHUKET 4255", "ADV 350CC BLACK PHUKET 5849"]

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None

    def _getter(self):
        # Одна модель, ЧИСТАЯ котировка Bridge: итог за срок 1685 ฿, депозит 3000 ฿, без капа и без
        # J-текста (сборка day/total/deposit) — детерминированная строка ЦЕНЫ (числа только из Bridge).
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            if suggest._bike_key("NMAX 155") not in suggest._bike_key(params.get("bike", "")):
                return {"ok": False}
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            return {"ok": True, "data": {"day_price": 337, "total": 1685, "deposit": 3000,
                    "available": True, "days": days, "cap_active": False, "cap_price": 0}}
        return fake

    def _hints(self, **extra):
        h = {"has_dates": True, "iso_start": "2026-07-15", "iso_end": "2026-07-20",
             "model": "NMAX 155", "hint_days": 5}
        h.update(extra)
        return h

    def _note(self, **extra):
        return suggest.build_pricing_note(self._hints(**extra), lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))

    # Живой формат СТОЛБЦА J Календаря: строка ЦЕНЫ со «скидкой за срок N%» в скобках + депозит
    # словами (депозит уже в строке — реассемблировать нечего). Правило-класс CLAUDE.md: мок
    # внешнего источника КОПИРУЕТ живой формат прода (текст J дословно), а не идеализированную схему.
    J_LINE = "2400 ฿ за 5 дней (Скидка за срок 15%, 480 ฿ в день); депозит 3000 ฿"

    def _getter_j(self):
        # Тот же одиночный NMAX, но Bridge отдаёт ГОТОВУЮ строку столбца J (со «Скидкой за срок N%»)
        # в поле text — как живой Календарь; депозит уже словами в тексте (дописки не будет).
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            if suggest._bike_key("NMAX 155") not in suggest._bike_key(params.get("bike", "")):
                return {"ok": False}
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            return {"ok": True, "data": {"day_price": 480, "total": 2400, "deposit": 3000,
                    "available": True, "days": days, "cap_active": False, "cap_price": 0,
                    "text": self.J_LINE}}
        return fake

    def _note_j(self, **extra):
        return suggest.build_pricing_note(self._hints(**extra), lang="ru", getter=self._getter_j(),
                                          today=datetime.date(2026, 7, 11))

    def test_golden_quote_block_carries_column_j_verbatim(self):
        # ГОЛДЕН #92 шаг 2/6: лист с ценой И скидкой за срок → строка столбца J едет в служебный
        # quote-блок ПОСИМВОЛЬНО, включая процент «(Скидка за срок 15%, … в день)». Код не
        # переформатирует и не опускает скидку (транспорт шага 1/6 #92, коммит 1db63e7).
        block = suggest._quote_block_from_note(self._note_j())
        self.assertIsNotNone(block)                          # блок собран
        self.assertIn(self.J_LINE, block)                    # строка J посимвольно (со скидкой за срок)
        self.assertIn("(Скидка за срок 15%, 480 ฿ в день)", block)   # процент срока цел дословно

    def test_golden_draft_carries_column_j_verbatim(self):
        # ГОЛДЕН #92 шаг 2/6: тот же J-текст доходит до ЧЕРНОВИКА клиента дословно — LLM цену/скидку
        # потерял, но строка столбца J пришла КОДОМ (compose/regenerate, fail-safe), не переформатирована.
        note = self._note_j()
        def drop_price(system, user):
            return "Готов помочь с NMAX — уточню детали и вернусь."   # LLM цену/скидку потерял
        out = suggest.regenerate_draft("[клиент]: nmax на 5 дней с 15 июля, сколько?", "ru", "FAQ",
                                       False, note, "дожимай на бронь", call_llm=drop_price)
        client = suggest.client_facing_text(out)
        self.assertIn(self.J_LINE, client)                   # строка столбца J дословно у клиента
        self.assertIn("Скидка за срок 15%", client)          # процент срока не потерян и не переформатирован
        self.assertNotIn("<<<QUOTE>>>", out)                 # сырые служебные скобки не утекли клиенту

    def test_build_note_carries_quote_block(self):
        # Чистый точечный quote «ok» → в pricing_note детерминированная строка ЦЕНЫ в служебных скобках.
        note = self._note()
        block = suggest._quote_block_from_note(note)
        self.assertIsNotNone(block)                          # блок собран
        self.assertIn("1685", block)                         # итог за срок из Bridge
        self.assertIn("3000", block)                         # депозит из Bridge
        self.assertIn("ЦЕНА из Календаря", note)             # инструкция LLM (прежний путь) цела

    def test_prompt_strips_quote_block(self):
        # Служебный блок в промпт LLM НЕ течёт (как SHEET): цифры цены остаются в инструкции ЦЕНА,
        # но сырых скобок/дубля блока в system нет.
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note=self._note())
        self.assertNotIn("<<<QUOTE>>>", sysp)
        self.assertNotIn("<<<END_QUOTE>>>", sysp)
        self.assertIn("ЦЕНА из Календаря", sysp)             # цена всё же в промпте (в инструкции)
        self.assertIn("1685", sysp)

    def test_deposit_sum_default_no_passport(self):
        # РЕЖИМ «сумма из Bridge» (паспорт НЕ выбран): и инструкция LLM, и quote-хвост несут число
        # депозита 3000 — прежнее поведение цело.
        note = self._note()
        block = suggest._quote_block_from_note(note)
        self.assertIn("депозит 3000 ฿", block)               # сумма депозита из Bridge в хвосте
        self.assertIn("3000", note)                          # и в инструкции LLM (теле ответа)
        self.assertNotIn("паспорт", block.lower())

    def test_deposit_passport_quote_block(self):
        # РЕЖИМ «паспорт»: клиент выбрал паспорт как депозит → в quote-хвосте «депозит: паспорт»
        # БЕЗ суммы; итог за срок (1685) цел. Тело ответа (инструкция) и хвост НЕ противоречат.
        note = self._note(deposit_passport_q=True)
        block = suggest._quote_block_from_note(note)
        self.assertIsNotNone(block)
        self.assertIn("депозит: паспорт", block)             # паспорт без суммы в хвосте
        self.assertNotIn("3000", block)                      # сумма депозита Bridge подавлена
        self.assertIn("1685", block)                         # цена/итог за срок цел
        # тело ответа (инструкция LLM) тоже несёт паспорт, а НЕ число — противоречия нет
        self.assertNotIn("3000", note)
        self.assertIn("паспорт", note)

    def test_units_carry_block_per_each(self):
        # #365 родитель4 шаг3/6: N юнитов одной модели → quote-блок несём ТОЖЕ, но цена/депозит в
        # нём помечены ЗА КАЖДЫЙ юнит (итог за N шт. КОД не выдумывает — числа только из Bridge).
        note_units = self._note(units_count=2)
        block = suggest._quote_block_from_note(note_units)
        self.assertIsNotNone(block)                          # блок собран (страт-путь несёт цену)
        self.assertIn("1685", block)                         # цена за КАЖДЫЙ юнит из Bridge
        self.assertIn("3000", block)                         # депозит за КАЖДЫЙ юнит из Bridge
        self.assertIn("ЗА КАЖДЫЙ", block)                    # пометка «за каждый» в самом блоке
        self.assertIn("ЗА КАЖДЫЙ", note_units)               # и в инструкции LLM
        self.assertNotIn("3370", block)                      # 2×1685 итог НЕ выдуман
        self.assertNotIn("6000", block)                      # 2×3000 депозит НЕ выдуман

    def test_percent_skips_block(self):
        # Процент — единой клиентской строки ЦЕНЫ нет (сумма вплетена в инструкцию) → блок НЕ собираем.
        note_pct = self._note(percent_q=50)
        self.assertIsNone(suggest._quote_block_from_note(note_pct))

    def test_compose_marker_intro_block_outro(self):
        # Метка [QUOTE] → intro (LLM) + блок ДОСЛОВНО + outro (LLM).
        block = "NMAX 155 — 337 ฿/день; итого 1685 ฿; депозит 3000 ฿."
        out = suggest.compose_quote_draft("Отличный выбор!\n[QUOTE]\nЖду ответа.", block, "ru")
        self.assertEqual(out, "Отличный выбор!\n\n" + block + "\n\nЖду ответа.")

    def test_compose_no_dup_when_carried(self):
        # LLM донёс все цифры блока → возвращаем текст как есть (без дубля).
        block = "NMAX 155 — 337 ฿/день; итого 1685 ฿; депозит 3000 ฿."
        carried = "NMAX 155 на 5 дней — 1685 ฿, депозит 3000 ฿ (337 ฿/день). Бронируем?"
        self.assertEqual(suggest.compose_quote_draft(carried, block, "ru"), carried)

    def test_compose_failsafe_appends_lost_price(self):
        # LLM потерял цену → блок Bridge приклеен хвостом (цена доходит клиенту ВСЕГДА).
        block = "NMAX 155 — 337 ฿/день; итого 1685 ฿; депозит 3000 ฿."
        lost = "Уточню цену по датам и вернусь."
        out = suggest.compose_quote_draft(lost, block, "ru")
        self.assertTrue(out.startswith(lost))
        self.assertIn(block, out)
        self.assertIn("1685", out)

    def test_strategy_regen_carries_price_by_code(self):
        # ЯДРО ШАГА: strategy-перегенерация — LLM цену НЕ привёл, но цифры Bridge дошли до финала
        # КОДОМ (compose_quote_draft), а не потерялись. Промпт нёс директиву и инструкцию цены, но
        # НЕ сырой служебный блок.
        note = self._note()
        seen = {}
        def drop_price(system, user):
            seen["system"] = system
            return "Готов помочь с NMAX — уточню детали и вернусь."   # LLM цену потерял
        out = suggest.regenerate_draft("[клиент]: nmax на 5 дней с 15 июля, сколько?", "ru", "FAQ",
                                       False, note, "дожимай на бронь", call_llm=drop_price)
        self.assertIn("1685", out)                           # итог из Bridge дошёл КОДОМ
        self.assertIn("3000", out)                           # депозит из Bridge дошёл КОДОМ
        self.assertIn("дожимай на бронь", seen["system"])    # директива стратегии в промпте
        self.assertNotIn("<<<QUOTE>>>", seen["system"])      # сырой блок в промпт не утёк

    def test_strategy_regen_units_carry_per_each_by_code(self):
        # N юнитов: strategy-перегенерация теряет цену → блок с ценой/депозитом ЗА КАЖДЫЙ юнит
        # доходит клиенту КОДОМ (итог за 2 шт. по-прежнему не выдуман).
        note = self._note(units_count=2)
        def drop_price(system, user):
            return "Отличный выбор — уточню детали и вернусь."     # LLM цену потерял
        out = suggest.regenerate_draft("[клиент]: пару nmax на 5 дней с 15 июля, сколько?", "ru",
                                       "FAQ", False, note, "дожимай", call_llm=drop_price)
        self.assertIn("1685", out)                           # цена за каждый юнит дошла КОДОМ
        self.assertIn("3000", out)                           # депозит за каждый юнит дошёл КОДОМ
        self.assertIn("ЗА КАЖДЫЙ", out)                      # пометка «за каждый» у клиента
        self.assertNotIn("3370", out)                        # 2×1685 итог не выдуман

    def test_strategy_regen_no_dup_when_llm_carries(self):
        # LLM честно привёл цифры Bridge → код НЕ дублирует их (обратная совместимость с прежним путём).
        note = self._note()
        def carry(system, user):
            return "NMAX на 5 дней — 1685 ฿, депозит 3000 ฿ (337 ฿/день). Бронируем?"
        out = suggest.regenerate_draft("[клиент]: nmax на 5 дней с 15 июля, сколько?", "ru", "FAQ",
                                       False, note, "дожимай", call_llm=carry)
        self.assertEqual(suggest.client_facing_text(out).count("1685"), 1)   # без дубля цены
        self.assertEqual(suggest.client_facing_text(out).count("3000"), 1)   # без дубля депозита


class TestDeliveryCodeBlock(unittest.TestCase):
    """#12 (шаг 5/7): цена ДОСТАВКИ по maps-ссылке клиента едет в финал КОДОМ — тем же транспортом,
    что <<<QUOTE>>>. Резолвер (extract_maps_link→resolve_maps_link→zones→resolve_delivery) даёт
    число ТОЛЬКО при zone/out_belt; нет ссылки/зон/Bridge → честный [уточнить] (строки нет).
    LLM цифру доставки НЕ видит (блок вырезан из промпта) и НЕ генерирует."""

    FLEET = ["NMAX 155CC BLACK PHUKET 4255"]

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN, suggest.delivery.resolve_delivery_from_text)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN, suggest.delivery.resolve_delivery_from_text) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None

    def _stub_delivery(self, result):
        # подменяем сетевой резолвер: при непустом тексте (есть maps_link) → заданный result.
        suggest.delivery.resolve_delivery_from_text = lambda text, **kw: (result if text else None)

    def _getter(self):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            if suggest._bike_key("NMAX 155") not in suggest._bike_key(params.get("bike", "")):
                return {"ok": False}
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            return {"ok": True, "data": {"day_price": 337, "total": 1685, "deposit": 3000,
                    "available": True, "days": days, "cap_active": False, "cap_price": 0}}
        return fake

    def _hints(self, **extra):
        h = {"has_dates": True, "iso_start": "2026-07-15", "iso_end": "2026-07-20",
             "model": "NMAX 155", "hint_days": 5,
             "maps_link": "https://www.google.com/maps?q=7.88,98.33"}
        h.update(extra)
        return h

    def _note(self, **extra):
        return suggest.build_pricing_note(self._hints(**extra), lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))

    ZONE = {"status": "zone", "zone": "Раваи", "price": 815, "distance_km": 1.0, "marker": None}
    UNCERTAIN = {"status": "uncertain", "zone": None, "price": None,
                 "distance_km": None, "marker": "[уточнить]"}

    # ------------------------------- helpers ---------------------------------

    def test_delivery_line_zone_ru(self):
        line = suggest._delivery_quote_line("x", "ru", _resolve=lambda t: self.ZONE)
        self.assertIn("815", line)
        self.assertIn("Доставка", line)

    def test_delivery_line_zone_en(self):
        line = suggest._delivery_quote_line("x", "en", _resolve=lambda t: self.ZONE)
        self.assertIn("815", line)
        self.assertIn("Delivery", line)

    def test_delivery_line_out_belt(self):
        r = {"status": "out_belt", "zone": None, "price": 1490, "distance_km": 8.0, "marker": None}
        self.assertIn("1490", suggest._delivery_quote_line("x", "ru", _resolve=lambda t: r))

    def test_delivery_line_uncertain_none(self):
        # нет координат/зон/Bridge → [уточнить] → строки НЕ даём (цену не выдумываем)
        self.assertIsNone(suggest._delivery_quote_line("x", "ru", _resolve=lambda t: self.UNCERTAIN))

    def test_delivery_line_no_link_none(self):
        self.assertIsNone(suggest._delivery_quote_line("x", "ru", _resolve=lambda t: None))

    def test_delivery_line_resolver_raises_none(self):
        def boom(t):
            raise RuntimeError("bridge down")
        self.assertIsNone(suggest._delivery_quote_line("x", "ru", _resolve=boom))

    # ------------------------- build_pricing_note ----------------------------

    def test_build_note_carries_delivery_block(self):
        self._stub_delivery(self.ZONE)
        note = self._note()
        dblock = suggest._delivery_block_from_note(note)
        self.assertIsNotNone(dblock)                         # блок доставки собран
        self.assertIn("815", dblock)                         # цена зоны из резолвера
        self.assertIsNotNone(suggest._quote_block_from_note(note))   # quote-блок аренды НЕ сломан
        self.assertIn("1685", note)                          # цена аренды на месте

    def test_build_note_uncertain_no_delivery_block(self):
        self._stub_delivery(self.UNCERTAIN)
        self.assertIsNone(suggest._delivery_block_from_note(self._note()))

    def test_build_note_no_link_no_delivery_block(self):
        self._stub_delivery(self.ZONE)
        note = suggest.build_pricing_note(self._hints(maps_link=None), lang="ru",
                                          getter=self._getter(), today=datetime.date(2026, 7, 11))
        self.assertIsNone(suggest._delivery_block_from_note(note))

    def test_units_carry_delivery_block(self):
        self._stub_delivery(self.ZONE)
        note = self._note(units_count=2)
        self.assertIn("815", suggest._delivery_block_from_note(note) or "")

    # ------------------------- prompt / compose ------------------------------

    def test_prompt_strips_delivery_block(self):
        # LLM цифру доставки НЕ видит: ни сырых скобок, ни самого числа 290 в system-промпте.
        self._stub_delivery(self.ZONE)
        sysp = suggest.make_system_prompt("FAQ", "ru", pricing_note=self._note())
        self.assertNotIn("<<<DELIVERY>>>", sysp)
        self.assertNotIn("<<<END_DELIVERY>>>", sysp)
        self.assertNotIn("815", sysp)                        # цена доставки в промпт не утекла

    def test_compose_delivery_appends_by_code(self):
        block = "Доставка — 290 ฿ (забор байка в конце аренды — бесплатный)."
        out = suggest.compose_delivery_draft("NMAX на 5 дней — 1685 ฿, депозит 3000 ฿.", block, "ru")
        self.assertIn("290", out)
        self.assertIn(block, out)

    def test_compose_delivery_no_dup_when_present(self):
        block = "Доставка — 290 ฿ (забор байка в конце аренды — бесплатный)."
        carried = "NMAX — 1685 ฿. Доставка — 290 ฿ до вашей виллы. Бронируем?"
        self.assertEqual(suggest.compose_delivery_draft(carried, block, "ru"), carried)

    # ------------------------- end-to-end (regen) ----------------------------

    def test_regen_carries_delivery_by_code(self):
        # ЯДРО ШАГА: strategy-перегенерация — LLM цену доставки НЕ привёл, но 290 дошло КОДОМ, и
        # цена аренды НЕ задвоилась (доставка своим блоком, не внутри quote).
        self._stub_delivery(self.ZONE)
        note = self._note()
        seen = {}
        def drop(system, user):
            seen["system"] = system
            return "NMAX на 5 дней — 1685 ฿, депозит 3000 ฿ (337 ฿/день). Бронируем?"
        out = suggest.regenerate_draft(
            "[клиент]: nmax на 5 дней с 15 июля, вот локация https://www.google.com/maps?q=7.88,98.33",
            "ru", "FAQ", False, note, "дожимай", call_llm=drop)
        self.assertIn("815", out)                            # доставка дошла КОДОМ
        self.assertEqual(suggest.client_facing_text(out).count("1685"), 1)   # аренда НЕ задвоена
        self.assertNotIn("815", seen["system"])              # LLM цену доставки не видел

    # ---------------------- extract_booking_hints (гео) ----------------------

    def test_hints_extracts_maps_link_realistic(self):
        # реальная фраза клиента с локацией: ссылку кладём в maps_link (регистр токена сохранён)
        tr = ("[клиент]: nmax на неделю с 15 июля\n"
              "[клиент]: вот моя вилла, кину локацию https://maps.app.goo.gl/aZ9xQ2 спасибо")
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertEqual(h["maps_link"], "https://maps.app.goo.gl/aZ9xQ2")

    def test_hints_no_link_when_only_mention(self):
        # «локация/вилла» словами без ссылки → maps_link None (упоминание ≠ гео)
        h = suggest.extract_booking_hints("[клиент]: моя вилла в Раваи, локация рядом с пляжем",
                                          today=datetime.date(2026, 7, 11))
        self.assertIsNone(h["maps_link"])


def _has_thai_letters(s):
    """Тайские буквы/гласные/тоны в строке (класс регрессий «тайский в выводе»), НО НЕ знак бата
    ฿ (U+0E3F) — он легитимен в ценах. Возвращает True при наличии тайских СИМВОЛОВ языка."""
    return any(("ก" <= ch <= "ฺ") or ("เ" <= ch <= "๛") for ch in (s or ""))


class TestDeliveryZoneResolveInDraft(unittest.TestCase):
    """Дефект #303/1 (@cryptopeppa 20.07 23:59): зона доставки резолвится по maps-ссылке ИЛИ гео-пину
    → тариф СРАЗУ в черновик, район у клиента НЕ переспрашиваем. Только при неопределённой зоне
    (точка вне зон / нет координат) — фолбэк-вопрос с перечнем районов. Зоны — ЖИВОЙ формат Bridge."""

    with open(os.path.join(suggest.BASE_DIR, "fixtures", "delivery_zones_get.live.json"),
              encoding="utf-8") as _f:
        ZONES = json.load(_f)["zones"]           # позиционный список листа [name,lat,lon,price,radius]
    RAWAI = (7.771, 98.327)                       # якорь зоны Раваи (цена 590 из фикстуры)
    FAR = (13.7563, 100.5018)                     # Бангкок — заведомо вне всех зон Пхукета

    def setUp(self):
        self._save = (suggest.delivery.resolve_delivery_from_text,
                      suggest.delivery.resolve_delivery_from_coords,
                      suggest.delivery.get_delivery_zones)
        suggest.delivery.get_delivery_zones = lambda *a, **k: self.ZONES

    def tearDown(self):
        (suggest.delivery.resolve_delivery_from_text,
         suggest.delivery.resolve_delivery_from_coords,
         suggest.delivery.get_delivery_zones) = self._save

    def _hints(self, **extra):
        h = {"has_dates": False, "model": None, "models": [], "maps_link": None, "geo_pin": None,
             "deposit_multi_q": False, "units_count": None, "old_gen_q": False,
             "deposit_passport_q": False, "percent_q": None, "sheet_filter": None,
             "price_sheet_q": False, "iso_start": None, "iso_end": None, "hint_days": None,
             "monthly": False}
        h.update(extra)
        return h

    def _resolve_at(self, lat, lon):
        # реальный resolve_delivery на живых зонах — по заданной точке (мок «мок = живой формат»)
        return lambda text=None, *a, **k: suggest.delivery.resolve_delivery(lat, lon, self.ZONES)

    # ------------------------- Maps-ссылка → зона → тариф --------------------------

    def test_maps_link_resolves_zone_into_draft_no_district_question(self):
        # ЖИВОЙ КЕЙС @cryptopeppa: у бота есть maps-ссылка → зона+цена в черновик, район НЕ спрашиваем.
        suggest.delivery.resolve_delivery_from_text = self._resolve_at(*self.RAWAI)
        note = suggest.build_pricing_note(self._hints(maps_link="https://maps.app.goo.gl/x"), lang="ru")
        dblock = suggest._delivery_block_from_note(note)
        self.assertIsNotNone(dblock)                         # строка доставки собрана
        self.assertIn("590", dblock)                         # цена зоны Раваи из Bridge
        self.assertIn("Раваи", dblock)                       # имя зоны названо (не безымянно)
        p = suggest.make_system_prompt("FAQ", "ru", pricing_note=note)
        self.assertIn("район у клиента НЕ переспрашивай", p)  # зона определена → район не спрашиваем
        self.assertNotIn("уточни район доставки и назови", p)  # старый фолбэк-вопрос НЕ активен
        self.assertNotIn("<<<DELIVERY>>>", p)                # сырой служебный маркер не утёк
        self.assertNotIn("Доставка в Раваи — 590", p)        # КЛИЕНТСКУЮ строку доставки LLM не видит

    def test_maps_link_out_belt_no_zone_name(self):
        # точка в периферийном поясе (за радиусом зоны, ≤ +5 км) → цена пояса без имени зоны
        suggest.delivery.resolve_delivery_from_text = lambda text=None, *a, **k: {
            "status": "out_belt", "zone": None, "price": 1490, "distance_km": 8.0, "marker": None}
        note = suggest.build_pricing_note(self._hints(maps_link="https://x"), lang="ru")
        dblock = suggest._delivery_block_from_note(note)
        self.assertIn("1490", dblock)
        self.assertIn("Доставка — 1490", dblock)             # без « в <зона>» (пояс — имени зоны нет)

    # ------------------------------ Гео-пин → зона → тариф ----------------------------

    def test_geo_pin_resolves_zone_into_draft(self):
        # клиент кинул ЛОКАЦИЮ (пин) — координаты в hints.geo_pin → зона/цена в черновик (реальный
        # resolve_delivery_from_coords через Bridge-зоны), маркер маршрут той же формы, что ссылка.
        note = suggest.build_pricing_note(self._hints(geo_pin=self.RAWAI), lang="ru")
        dblock = suggest._delivery_block_from_note(note)
        self.assertIsNotNone(dblock)
        self.assertIn("590", dblock)
        self.assertIn("Раваи", dblock)

    def test_transcript_geo_pin_marker_extracts_coords_and_geo_fact(self):
        # transcript_from пишет пин как «[локация lat,lon]»; hints достаёт coords, трекер — гео-факт
        tr = "[клиент]: nmax на 5 дней\n[клиент]: [локация 7.771000,98.327000]"
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertEqual(h["geo_pin"], (7.771, 98.327))
        self.assertTrue(suggest.collected_facts(tr)["geo"])  # маркер-пин с координатами ловится как гео

    def test_geo_pin_preferred_over_link(self):
        # есть и пин, и ссылка → берём ПИН (явную расшаренную точку); coords резолвятся первыми
        suggest.delivery.resolve_delivery_from_text = self._resolve_at(*self.FAR)   # ссылка «увела бы»
        note = suggest.build_pricing_note(
            self._hints(geo_pin=self.RAWAI, maps_link="https://x"), lang="ru")
        dblock = suggest._delivery_block_from_note(note)
        self.assertIn("Раваи", dblock)                       # зона по ПИНУ, не по ссылке

    # -------------------- вне зон / нет координат → фолбэк с перечнем ---------------------

    def test_out_of_zones_falls_back_to_district_question_with_list(self):
        # точка далеко вне всех зон (Бангкок) → uncertain → фолбэк-вопрос С ПЕРЕЧНЕМ районов, БЕЗ цены
        suggest.delivery.resolve_delivery_from_text = self._resolve_at(*self.FAR)
        note = suggest.build_pricing_note(self._hints(maps_link="https://maps.app.goo.gl/far"), lang="ru")
        self.assertIsNone(suggest._delivery_block_from_note(note))   # цену НЕ выдумываем
        ask = suggest._DELIVERY_ASK_BLOCK_RE.search(note)
        self.assertIsNotNone(ask)                                    # фолбэк-блок собран
        self.assertIn("Раваи", ask.group(1))                        # перечень районов из Bridge
        self.assertIn("Патонг", ask.group(1))
        p = suggest.make_system_prompt("FAQ", "ru", pricing_note=note)
        self.assertIn("в какой РАЙОН", p)                            # спрашиваем район
        self.assertIn("Раваи", p)                                    # с перечнем районов
        self.assertNotIn("<<<DELIVERY_ASK>>>", p)                   # сырые маркеры не утекли

    def test_link_without_coords_falls_back_with_list(self):
        # ссылка была, но координат из неё не достали → resolve_delivery(None,None,None) = uncertain
        suggest.delivery.resolve_delivery_from_text = lambda text=None, *a, **k: (
            suggest.delivery.resolve_delivery(None, None, None) if text else None)
        note = suggest.build_pricing_note(self._hints(maps_link="https://x"), lang="ru")
        self.assertIsNone(suggest._delivery_block_from_note(note))
        self.assertIsNotNone(suggest._DELIVERY_ASK_BLOCK_RE.search(note))

    def test_uncertain_without_bridge_zones_generic_ask(self):
        # зоны Bridge недоступны → фолбэк-вопрос без перечня (общий), но БЕЗ падения/цены
        suggest.delivery.get_delivery_zones = lambda *a, **k: None
        suggest.delivery.resolve_delivery_from_text = lambda text=None, *a, **k: (
            suggest.delivery.resolve_delivery(None, None, None) if text else None)
        note = suggest.build_pricing_note(self._hints(maps_link="https://x"), lang="ru")
        ask = suggest._DELIVERY_ASK_BLOCK_RE.search(note)
        self.assertIsNotNone(ask)
        self.assertNotIn("наши районы", ask.group(1))               # зон нет → без перечня

    def test_no_location_keeps_default_delivery_stage(self):
        # ни пина, ни ссылки → доставку не трогаем: прежний общий путь Этапа 2 (уточни район)
        note = suggest.build_pricing_note(self._hints(), lang="ru")
        self.assertIsNone(suggest._delivery_block_from_note(note))
        self.assertIsNone(suggest._DELIVERY_ASK_BLOCK_RE.search(note))
        p = suggest.make_system_prompt("FAQ", "ru", pricing_note=note)
        self.assertIn("уточни район доставки", p)                    # прежний общий путь Этапа 2

    # --------------------------------- анти-тайский ----------------------------------

    def test_no_thai_in_delivery_line(self):
        res = {"status": "zone", "zone": "Раваи", "price": 590, "distance_km": 1.0, "marker": None}
        self.assertFalse(_has_thai_letters(suggest._delivery_client_line(res, "ru")))
        self.assertFalse(_has_thai_letters(suggest._delivery_client_line(res, "en")))

    def test_no_thai_in_ask_block(self):
        self.assertFalse(_has_thai_letters(suggest._delivery_ask_block("ru", _zones=self.ZONES)))
        self.assertFalse(_has_thai_letters(suggest._delivery_ask_block("en", _zones=self.ZONES)))

    def test_no_thai_in_generated_draft_with_zone(self):
        # сквозной черновик с резолвом зоны: клиентское тело без тайских БУКВ (฿ легитимен)
        suggest.delivery.resolve_delivery_from_text = self._resolve_at(*self.RAWAI)
        note = suggest.build_pricing_note(self._hints(maps_link="https://x"), lang="ru")
        d = suggest.generate_draft("[клиент]: вот локация https://x", "ru", "FAQ",
                                   pricing_note=note, call_llm=lambda s, u: "Доставку подтверждаю.")
        self.assertFalse(_has_thai_letters(suggest.client_facing_text(d)))


class TestReceiptLexiconDefect303(unittest.TestCase):
    """Дефект #303/2 «факты ≠ артефакты»: «получили» — ТОЛЬКО про реальное вложение (фото/оплата);
    информацию (в т.ч. выбор паспорта как залога БЕЗ фото) подтверждаем «понял/учли», не «получили»."""

    def test_lexicon_rule_in_prompt(self):
        p = suggest.make_system_prompt("FAQ", "ru")
        self.assertIn("ЛЕКСИКА КВИТАНЦИИ", p)
        self.assertIn("НЕ пиши «паспорт получили»", p)

    def test_passport_deposit_intent_without_photo_is_not_received(self):
        # «депозит паспортом» БЕЗ фото → passport-факт False (намерение ≠ полученный документ)
        tr = "[клиент]: nmax на 5 дней с 15 июля, депозит оставлю паспортом"
        facts = suggest.collected_facts(tr, today=datetime.date(2026, 7, 11))
        self.assertFalse(facts["passport"])                  # нет фото → не «получен»
        note = suggest.collected_prompt_note(facts, "ru")
        self.assertNotIn("паспорт получили", note)           # намерение-паспорт не звучит как «получили»
        self.assertNotIn("паспорт", note.lower())            # «паспорт» в собранном (got) не появляется
        # пример-подтверждение по инфо-только (модель/срок/даты) — «всё учли», без «получили»
        self.assertEqual(suggest._confirm_example(facts, en=False), "всё учли")

    def test_confirm_example_geo_only_uses_uchli(self):
        facts = {"geo": True}
        self.assertEqual(suggest._confirm_example(facts, en=False), "локацию учли")
        self.assertNotIn("получил", suggest._confirm_example(facts, en=False))

    def test_confirm_example_photo_uses_poluchili(self):
        facts = {"passport": True, "geo": True}
        ex = suggest._confirm_example(facts, en=False)
        self.assertIn("фото получили", ex)                   # вложение → «получили»
        self.assertIn("остальное учли", ex)                  # локация (инфо) → «учли»


class TestLaconicSocialProofDefect303(unittest.TestCase):
    """Дефект #303/3: соц-доказательство (точки/отзывы) — только в ПЕРВОМ приветствии; в продолжении
    диалога его не дублируем (лаконичность)."""

    def test_first_contact_prompt_has_no_suppression(self):
        p = suggest.make_system_prompt("FAQ", "ru", is_first_contact=True)
        self.assertNotIn("НЕ повторяй СОЦ-ДОКАЗАТЕЛЬСТВО", p)

    def test_continuation_prompt_suppresses_social_proof(self):
        p = suggest.make_system_prompt("FAQ", "ru", is_first_contact=False)
        self.assertIn("СОЦ-ДОКАЗАТЕЛЬСТВО", p)
        self.assertIn("отзыв", p.lower())
        self.assertIn("только в первом приветствии", p.lower())


class TestSheetSubselection(unittest.TestCase):
    """Подвыборки сетки («скутеры 200+», «мотоциклы до 400») — ДЕТЕРМИНИРОВАННЫЙ отбор КОДОМ из тех
    же rows, что и полная сетка (та же точка правды), БЕЗ LLM-отбора моделей. Класс-голден живого
    провала 12.07 15:46: XADV 750 (скутер 750cc) в «скутерах 200+» у LLM-отбора ТЕРЯЛСЯ. Тесты
    детекта на РЕАЛЬНОЙ фразе клиента («скутеры 200+») + парафразах RU/EN + негативах."""

    # тариф по модели: (day_total, week_total, month_total, deposit, cap_active, cap_price)
    TAR = {
        "NMAX 155": (450, 2800, 9000, 5000, True, 8500),       # скутер 155cc
        "ADV 350": (749, 4928, 14606, 7000, True, 10900),      # скутер 350cc
        "XADV 750": (2788, 18000, 55000, 25000, False, 0),     # скутер 750cc — тот, что «терялся»
        "CB 300R": (757, 4716, 12491, 15000, True, 9900),      # мотоцикл 300cc
        "CBR 650R": (1200, 7000, 22000, 20000, False, 0),      # мотоцикл 650cc
    }
    FLEET_NAMES = ["NMAX 155CC BLACK PHUKET 4255", "ADV 350CC BLACK PHUKET 5849",
                   "XADV 750CC GREY PHUKET 4290", "CB 300CC R 9011", "CBR 650R PHUKET 4505"]

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

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
            total = {1: d1, 7: d7, 30: d30}.get(days, d1)
            return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
                    "deposit": dep, "available": True, "days": days, "cap_active": ca,
                    "cap_price": cp, "text": f"{bike} {days}d {total}"}}
        return fake

    # ---- рабочий объём из метки модели ----
    def test_model_cc_from_label(self):
        self.assertEqual(suggest._model_cc("XADV 750"), 750)
        self.assertEqual(suggest._model_cc("NMAX 155"), 155)
        self.assertEqual(suggest._model_cc("CB 300R"), 300)
        self.assertEqual(suggest._model_cc("XMAX 300 New Gen"), 300)   # New Gen без цифр не мешает
        self.assertIsNone(suggest._model_cc("MT-03"))                  # 2-значный хвост серии — не cc
        self.assertIsNone(suggest._model_cc("R7"))

    # ---- детерминированная подвыборка rows (та же точка правды, что и сетка) ----
    def test_filter_scooters_200plus_keeps_xadv(self):
        # ГЛАВНОЕ требование задачи: XADV 750 (скутер 750cc) в «скутерах 200+» ПРИСУТСТВУЕТ.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        sub = suggest.filter_sheet_rows(rows, kind="scooter", cc_min=200)
        models = [r["model"] for r in sub]
        self.assertIn("XADV 750", models)          # 750cc скутер НЕ потерян (живой провал 12.07)
        self.assertIn("ADV 350", models)
        self.assertNotIn("NMAX 155", models)       # 155 < 200 — отсечён
        self.assertNotIn("CB 300R", models)        # мотоцикл — не тот класс
        self.assertNotIn("CBR 650R", models)

    def test_filter_moto_up_to_400(self):
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        sub = suggest.filter_sheet_rows(rows, kind="moto", cc_max=400)
        models = [r["model"] for r in sub]
        self.assertIn("CB 300R", models)           # 300 ≤ 400
        self.assertNotIn("CBR 650R", models)       # 650 > 400 — отсечён
        self.assertNotIn("XADV 750", models)       # скутер — не тот класс
        self.assertNotIn("NMAX 155", models)

    def test_filter_unknown_cc_kept_failopen(self):
        # cc не прочли (MT-03 / R7) → строку класса НЕ выкидываем (fail-open, корень провала XADV).
        rows = [{"model": "MT-03", "class": ("moto", 3, "мотоциклы"), "cells": {"day": {"total": 700}}}]
        self.assertEqual([r["model"] for r in suggest.filter_sheet_rows(rows, kind="moto", cc_max=400)],
                         ["MT-03"])
        self.assertEqual([r["model"] for r in suggest.filter_sheet_rows(rows, kind="moto", cc_min=200)],
                         ["MT-03"])

    def test_full_sheet_unchanged_regression(self):
        # РЕГРЕСС сетки: без подвыборки rows = ВЕСЬ парк, формат/цифры целы.
        rows = suggest.price_sheet("2026-07-15", getter=self._getter())
        block = suggest.render_price_sheet(rows, "2026-07-15", "ru")
        for m in ("NMAX 155", "ADV 350", "XADV 750", "CB 300R", "CBR 650R"):
            self.assertIn(m, block)
        self.assertIn("XADV 750\n• Сутки: 2788 ฿", block)   # цифры/формат карточки не тронуты
        self.assertLess(block.index("Скутеры:"), block.index("Мотоциклы:"))

    # ---- детект подвыборки из СЛОВ клиента (реальная фраза + парафразы RU/EN + негативы) ----
    def test_parse_scooters_min_positive(self):
        # «скутеры 200+» — дословная формулировка задачи (живой черновик 12.07 15:46) + парафразы.
        for s in ["скутеры 200+",
                  "какие есть скутеры от 200 кубов?",
                  "интересуют скутеры больше 200",
                  "покажите скутеры свыше 200 кубов",
                  "scooters from 200cc please",
                  "what scooters over 200 do you have?"]:
            f = suggest._parse_sheet_filter(s.lower(), s.lower())
            self.assertIsNotNone(f, s)
            self.assertEqual(f["kind"], "scooter", s)
            self.assertEqual(f["cc_min"], 200, s)
            self.assertIsNone(f["cc_max"], s)

    def test_parse_moto_max_positive(self):
        for s in ["мотоциклы до 400",
                  "какие мотоциклы до 400 кубов?",
                  "мотоцикл не больше 400",
                  "мотоциклы меньше 400 кубов",
                  "motorcycles under 400cc",
                  "what motorcycles up to 400 do you have?"]:
            f = suggest._parse_sheet_filter(s.lower(), s.lower())
            self.assertIsNotNone(f, s)
            self.assertEqual(f["kind"], "moto", s)
            self.assertEqual(f["cc_max"], 400, s)
            self.assertIsNone(f["cc_min"], s)

    def test_parse_negatives(self):
        # Ни класса-подвыборки, ни границы cc → None (полная сетка). Даты/сроки за cc НЕ считаем.
        for s in ["какие цены на все модели",
                  "сколько стоит nmax",
                  "пришлите прайс-лист",
                  "нужны все модели на 15.07-15.08",     # даты, не cc; нет класс-слова
                  "какие байки у вас есть?",              # «байк» — не scooter/moto
                  "какие есть скутеры и мотоциклы?"]:     # оба класса → весь парк
            self.assertIsNone(suggest._parse_sheet_filter(s.lower(), s.lower()), s)

    # ---- сквозной путь: hints → build_pricing_note (КОД фильтрует, LLM не отбирает) ----
    def test_end_to_end_scooters_200plus_keeps_xadv(self):
        tr = "[клиент]: какие есть скутеры от 200 кубов и цены на аренду?"
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])
        self.assertEqual(h["sheet_filter"], {"kind": "scooter", "cc_min": 200, "cc_max": None})
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("ПРАЙС ПО ПАРКУ", note)
        self.assertIn("XADV 750", note)            # 750cc скутер в подвыборке — не потерян
        self.assertIn("ADV 350", note)
        self.assertNotIn("NMAX 155", note)         # 155 < 200 — отфильтрован КОДОМ
        self.assertNotIn("CB 300R", note)          # мотоцикл — не тот класс
        self.assertNotIn("CBR 650R", note)

    def test_end_to_end_moto_up_to_400(self):
        tr = "[клиент]: какие мотоциклы до 400 кубов и почём аренда?"
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])
        self.assertEqual(h["sheet_filter"], {"kind": "moto", "cc_min": None, "cc_max": 400})
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        self.assertIn("CB 300R", note)             # 300 ≤ 400
        self.assertNotIn("CBR 650R", note)         # 650 > 400
        self.assertNotIn("XADV 750", note)         # скутер — не тот класс
        self.assertNotIn("NMAX 155", note)

    def test_end_to_end_no_filter_full_grid_regression(self):
        tr = "[клиент]: пришлите прайс по всему парку"
        h = suggest.extract_booking_hints(tr, today=datetime.date(2026, 7, 11))
        self.assertTrue(h["price_sheet_q"])
        self.assertIsNone(h["sheet_filter"])
        note = suggest.build_pricing_note(h, lang="ru", getter=self._getter(),
                                          today=datetime.date(2026, 7, 11))
        for m in ("NMAX 155", "ADV 350", "XADV 750", "CB 300R", "CBR 650R"):
            self.assertIn(m, note)                 # без подвыборки — весь парк


class TestTeamRegistryBlock(unittest.TestCase):
    """ЖЁСТКИЙ блок команды КОДОМ ДО LLM (родитель: инцидент @Pleummmm 15.07 11:23 — userbot
    сгенерил черновик на окно ОФИС-МЕНЕДЖЕРА). ГОЛДЕН: сообщение от участника реестра → НУЛЕВАЯ
    реакция конвейера (ни _fetch_messages, ни LLM, ни черновика/приветствия, ни отправки).
    НЕГАТИВ: обычный клиент → конвейер работает штатно. Слой 2: intake от внутреннего id не
    постится во «Входящие брони»."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = self._tmp.name
        self._reg = suggest.TEAM_REGISTRY
        self._mode = suggest.SUGGEST_MODE
        self._test = suggest.SUGGEST_TEST_MODE
        self._mg = suggest.MOD_GROUP_ID
        self._pending = suggest.pending
        self._pairs = suggest.PAIRS_FILE
        self._appr = suggest.APPROVER_USERNAMES
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.reset_disabled()
        suggest.MOD_GROUP_ID = -1009999999999
        suggest.pending = suggest.PendingStore(os.path.join(d, "pending.jsonl"))
        suggest.PAIRS_FILE = os.path.join(d, "pairs.jsonl")
        suggest.limiter = suggest.RateLimiter(6, 15)
        # Реестр из живого инцидента: офис-менеджер @Pleummmm (Пым) + коллега Earth + аккаунт
        # только по id (username нет) + внутренняя рабочая группа.
        suggest.TEAM_REGISTRY = {"usernames": {"pleummmm", "earth"},
                                 "user_ids": {770099}, "group_ids": {-1005550000}}
        self.me = 42

    def tearDown(self):
        suggest.TEAM_REGISTRY = self._reg
        suggest.SUGGEST_MODE = self._mode
        suggest.SUGGEST_TEST_MODE = self._test
        suggest.MOD_GROUP_ID = self._mg
        suggest.pending = self._pending
        suggest.PAIRS_FILE = self._pairs
        suggest.APPROVER_USERNAMES = self._appr
        suggest.reset_disabled()
        self._tmp.cleanup()

    def _run(self, sender, text):
        """Прогнать on_client_message; вернуть (res, client, llm_calls). counting_llm считает
        КАЖДЫЙ заход в генерацию — для блока команды список ОБЯЗАН остаться пустым."""
        calls = []

        def counting_llm(_s, _u):
            calls.append(1)
            return "DRAFT ответа клиенту"

        client = FakeClient(history=[
            FakeHistMsg(sender.id, text),
            FakeHistMsg(self.me, "Здравствуйте! Что хотите арендовать?"),
        ])
        res = asyncio.run(suggest.on_client_message(
            client, sender, self.me, call_llm=counting_llm, faq="FAQ"))
        return res, client, calls

    # ---- ГОЛДЕН: участник реестра → нулевая реакция (РЕАЛЬНЫЕ формы идентичности) ----
    def test_office_manager_username_zero_pipeline(self):
        # РЕАЛЬНЫЙ офис-менеджер @Pleummmm из окна 15.07 11:23; текст — нарочно «клиентская» фраза
        # (прайс+модели), но ИДЕНТИЧНОСТЬ перевешивает: конвейер не реагирует НИКАК.
        res, client, calls = self._run(
            FakeSender(770777, username="Pleummmm", first="Пым"),
            "Скинь прайс на аренду и какие модели свободны на след неделю?")
        self.assertIsNone(res)
        self.assertEqual(client.sent, [])         # ни в группу модерации, ни клиенту
        self.assertEqual(calls, [])               # LLM НЕ вызывался — блок ДО генерации
        self.assertIsNone(suggest.pending.get(1))  # pending пуст

    def test_office_manager_username_case_insensitive(self):
        res, client, calls = self._run(FakeSender(770778, username="PLEUMMMM"),
                                       "Какие цены на аренду скутеров?")
        self.assertIsNone(res)
        self.assertEqual(client.sent, [])
        self.assertEqual(calls, [])

    def test_second_team_member_username(self):
        res, client, calls = self._run(FakeSender(770779, username="Earth", first="Earth"),
                                       "@turbophuket глянь бронь на завтра")
        self.assertIsNone(res)
        self.assertEqual(client.sent, [])
        self.assertEqual(calls, [])

    def test_team_member_by_id_without_username(self):
        # аккаунт без username, но id в реестре → тоже блок (коллега пишет из лички)
        res, client, calls = self._run(FakeSender(770099, username=None, first="Даня"),
                                       "Привет, аренда есть на неделю?")
        self.assertIsNone(res)
        self.assertEqual(client.sent, [])
        self.assertEqual(calls, [])

    def test_approver_counts_as_internal(self):
        # staff-approver (из .env APPROVER_USERNAMES) — внутренний даже без записи в team_registry.json
        suggest.APPROVER_USERNAMES = {"managerx"}
        res, client, calls = self._run(FakeSender(770780, username="managerx"), "тест из офиса")
        self.assertIsNone(res)
        self.assertEqual(calls, [])

    def test_earth_changed_username_blocked_by_id(self):
        # ГОЛДЕН инцидента 22.07 14:25: Earth СМЕНИЛ username — в реестре был стейл 'earth', живой
        # @extthiwxer по username НЕ совпадает, фильтр промахнулся → черновик родился и уехал в
        # тренажёр. Фикс: ГЛАВНЫЙ КЛЮЧ — id. Идентичности РЕАЛЬНЫЕ (из боевых draft-строк IPC):
        # Earth = @extthiwxer id 8562625260; Пым = @Pleummmm id 659135499. Блок ПО ID при ЛЮБОМ
        # username → ноль реакций.
        suggest.TEAM_REGISTRY = {"usernames": {"pleummmm", "earth"},   # намеренно СТЕЙЛ-username
                                 "user_ids": {8562625260, 659135499}, "group_ids": set()}
        res, client, calls = self._run(
            FakeSender(8562625260, username="extthiwxer", first="extthiwxer"),
            "Здравствуйте! Хочу арендовать байк на неделю")
        self.assertIsNone(res)
        self.assertEqual(client.sent, [])         # ни черновика, ни приветствия — никуда
        self.assertEqual(calls, [])               # LLM не вызывался
        # Пым по id — так же, даже если username сменится
        res2, client2, calls2 = self._run(FakeSender(659135499, username="somethingnew"), "привет")
        self.assertIsNone(res2)
        self.assertEqual(client2.sent, [])
        self.assertEqual(calls2, [])

    def test_live_registry_file_has_earth_and_pym_ids(self):
        # РЕГРЕСС ДАННЫХ: боевой team_registry.json обязан держать ОБА живых id (ключ — id,
        # username вторичен). Читаем реальный файл, как его читает рантайм.
        reg = suggest._load_team_registry()
        self.assertIn(8562625260, reg["user_ids"], "Earth (id) выпал из реестра")
        self.assertIn(659135499, reg["user_ids"], "Пым (id) выпал из реестра")
        self.assertIn("extthiwxer", reg["usernames"])

    # ---- НЕГАТИВ: обычный клиент → конвейер РАБОТАЕТ (черновик на модерацию, не клиенту) ----
    def test_ordinary_client_pipeline_runs(self):
        res, client, calls = self._run(FakeSender(999, username="client1"),
                                       "Привет, сколько стоит NMAX на неделю?")
        self.assertIsNotNone(res)
        self.assertTrue(calls)                                                 # LLM вызван
        self.assertTrue(any(c[0] == suggest.MOD_GROUP_ID for c in client.sent))  # черновик в группу
        self.assertEqual([c for c in client.sent if c[0] == 999], [])          # клиенту — ничего

    def test_ordinary_client_by_id_runs(self):
        res, _client, calls = self._run(FakeSender(123456, username=None, first="Гость"),
                                        "какие байки есть в аренду?")
        self.assertIsNotNone(res)
        self.assertTrue(calls)

    # ---- helper-функции реестра ----
    def test_registry_helpers(self):
        self.assertTrue(suggest.is_internal_sender(FakeSender(1, username="pleummmm")))
        self.assertTrue(suggest.is_internal_sender(FakeSender(1, username="@Earth")))
        self.assertTrue(suggest.is_internal_sender(FakeSender(770099)))           # по id
        self.assertFalse(suggest.is_internal_sender(FakeSender(2, username="client1")))
        self.assertFalse(suggest.is_internal_sender(None))
        self.assertTrue(suggest.is_internal_chat(-1005550000))
        self.assertFalse(suggest.is_internal_chat(-1))
        self.assertFalse(suggest.is_internal_chat(None))
        self.assertTrue(suggest.is_internal_user_id(770099))
        self.assertFalse(suggest.is_internal_user_id(999))
        self.assertFalse(suggest.is_internal_user_id(None))

    def test_registry_loader_failsafe(self):
        # нет файла / битый JSON → пустой реестр (не падаем)
        empty = suggest._load_team_registry(os.path.join(self._tmp.name, "нет-такого.json"))
        self.assertEqual(empty, {"usernames": set(), "user_ids": set(), "group_ids": set()})

    # ---- СЛОЙ 2: intake от внутреннего id НЕ постится во «Входящие брони» ----
    def test_intake_second_layer_skips_internal(self):
        import moderation_ipc
        uniq = "🆕 БРОНЬ [реестр-тест-внутр] окно офис-менеджера"
        iid = moderation_ipc.save_intake_candidate(uniq, client_id=770099)
        moderation_ipc.confirm_intake(iid)
        posts = []

        async def poster(_c, gid, text):
            posts.append((gid, text))
            return 111

        asyncio.run(suggest.poll_and_post_intake(FakeClient(), poster=poster))
        self.assertNotIn(uniq, [t for _, t in posts])                 # наружу НЕ ушло
        self.assertEqual(moderation_ipc.get_intake(iid)["status"], "skipped")

    def test_intake_second_layer_posts_ordinary_client(self):
        import moderation_ipc
        uniq = "🆕 БРОНЬ [реестр-тест-клиент] обычный client1"
        iid = moderation_ipc.save_intake_candidate(uniq, client_id=999)
        moderation_ipc.confirm_intake(iid)
        posts = []

        async def poster(_c, gid, text):
            posts.append((gid, text))
            return 222

        asyncio.run(suggest.poll_and_post_intake(FakeClient(), poster=poster))
        self.assertIn(uniq, [t for _, t in posts])                    # обычный клиент — постим
        self.assertEqual(moderation_ipc.get_intake(iid)["status"], "posted")


class TestRunLiveSmoke(unittest.TestCase):
    """#92 шаг 3/6: e2e-смоук runLiveSmoke под TEST_MODE — сквозная проверка транспорта QUOTE/
    DELIVERY на ЖИВОМ формате прода: строка столбца J ДОСЛОВНО, доставка = цена ЗОНЫ (Раваи 590),
    год поколения не утёк, при полных данных нет «вернусь/уточним», нет утверждений о наличии,
    депозит без противоречий. Провал → status='failed' + карточка с диффом ОЖИДАНИЕ/ФАКТ. Оба
    внешних источника замоканы ЖИВЫМ форматом (правило-класс CLAUDE.md): позиционные зоны листа
    (fixtures/delivery_zones_get.live.json) через РЕАЛЬНЫЙ delivery.resolve_delivery + строка
    столбца J дословно в поле text котировки."""

    FLEET = ["NMAX 155CC BLACK PHUKET 4255", "ADV 350CC BLACK PHUKET 5849"]
    TODAY = datetime.date(2026, 7, 15)
    J_LINE = "2400 ฿ за 5 дней (Скидка за срок 15%, 480 ฿ в день); депозит 3000 ฿"

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None

    def _getter(self):
        # Bridge отдаёт ГОТОВУЮ строку столбца J (со «Скидкой за срок N%») в поле text — как живой
        # Календарь; депозит уже словами в тексте. Правило-класс: мок = живой формат прода.
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            if suggest._bike_key("NMAX 155") not in suggest._bike_key(params.get("bike", "")):
                return {"ok": False}
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            return {"ok": True, "data": {"day_price": 480, "total": 2400, "deposit": 3000,
                    "available": True, "days": days, "cap_active": False, "cap_price": 0,
                    "text": self.J_LINE}}
        return fake

    def _resolve(self):
        # ЖИВОЙ формат зон Bridge: ПОЗИЦИОННЫЙ список листа [name,lat,lon,price,radius] из фикстуры,
        # прогнанный через РЕАЛЬНЫЙ delivery.resolve_delivery на координатах зоны Раваи → 590.
        with open(os.path.join(suggest.BASE_DIR, "fixtures",
                               "delivery_zones_get.live.json"), encoding="utf-8") as f:
            zones = json.load(f)["zones"]

        def resolve(text):
            return suggest.delivery.resolve_delivery(7.771, 98.327, zones)
        return resolve

    def _run(self, **kw):
        return asyncio.run(suggest.runLiveSmoke(today=self.TODAY, require_test_mode=False, **kw))

    def test_green_full_data_passes(self):
        # ЯДРО: полная проба (модель+даты+пин) → все 6 чеков зелёные, карточки нет.
        def clean(system, user):
            return "Здравствуйте! Рассчитал аренду NMAX на ваши даты 👍 Детали ниже."
        r = self._run(getter=self._getter(), resolve_delivery=self._resolve(), call_llm=clean)
        self.assertEqual(r["status"], "passed", r["card"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["card"], "")
        self.assertTrue(all(c["ok"] for c in r["checks"]))
        client = suggest.client_facing_text(r["draft"])
        self.assertIn(self.J_LINE, client)                       # строка J посимвольно у клиента
        self.assertIn("Доставка в Раваи — 590 ฿", client)        # доставка = цена ЗОНЫ Раваи (с именем зоны)

    def test_red_violations_flagged_with_diff_card(self):
        # Живой контур «промахнулся» (wait_draft отдаёт черновик с остаточными нарушениями): год,
        # реаск, наличие, конфликт депозита — красные; транспорт J/доставки цел → эти зелёные.
        async def bad(win):
            return ("Здравствуйте! Байк 2023 года свободен и в наличии 👍\n"
                    "NMAX — 2400 ฿ за 5 дней (Скидка за срок 15%, 480 ฿ в день); депозит 3000 ฿.\n"
                    "Ещё депозит 5000 ฿ или паспорт.\n"
                    "Доставка в Раваи — 590 ฿ (при оплаченной доставке забор байка в конце аренды бесплатный).\n"
                    "Уточню детали и вернусь.")
        r = self._run(getter=self._getter(), resolve_delivery=self._resolve(), wait_draft=bad)
        self.assertEqual(r["status"], "failed")
        self.assertFalse(r["ok"])
        by = {c["name"]: c["ok"] for c in r["checks"]}
        self.assertTrue(by["строка J дословно"])                 # транспорт J донёс строку дословно
        self.assertTrue(by["доставка = цена зоны"])              # доставка = цена зоны
        self.assertFalse(by["нет годов"])                        # «2023 года» — красный
        self.assertFalse(by["нет «вернусь/уточним» при полных данных"])
        self.assertFalse(by["нет утверждений о наличии"])        # «свободен/в наличии»
        self.assertFalse(by["депозит без противоречий"])         # 3000 vs 5000
        self.assertIn("ОЖИДАНИЕ/ФАКТ", r["card"])                # карточка с диффом
        self.assertIn("нет годов", r["card"])
        self.assertIn("2023", r["card"])

    def test_red_j_line_not_verbatim_fails(self):
        # Черновик ПЕРЕСОБРАЛ строку J (потерял «Скидку за срок») → чек «строка J дословно» красный,
        # в карточке ожидание — дословная строка столбца J.
        async def reworded(win):
            return ("NMAX — 2400 ฿ за 5 дней; депозит 3000 ฿.\n"          # «Скидка за срок» вырезана
                    "Доставка в Раваи — 590 ฿ (при оплаченной доставке забор байка в конце аренды бесплатный).")
        r = self._run(getter=self._getter(), resolve_delivery=self._resolve(), wait_draft=reworded)
        self.assertFalse(r["ok"])
        by = {c["name"]: c["ok"] for c in r["checks"]}
        self.assertFalse(by["строка J дословно"])
        self.assertTrue(by["доставка = цена зоны"])
        self.assertIn("Скидка за срок 15%", r["card"])           # ожидание — дословная строка J

    def test_red_delivery_missing_fails(self):
        # Черновик БЕЗ строки доставки → чек «доставка = цена зоны» красный.
        async def nodelivery(win):
            return "NMAX — 2400 ฿ за 5 дней (Скидка за срок 15%, 480 ฿ в день); депозит 3000 ฿."
        r = self._run(getter=self._getter(), resolve_delivery=self._resolve(), wait_draft=nodelivery)
        self.assertFalse(r["ok"])
        by = {c["name"]: c["ok"] for c in r["checks"]}
        self.assertTrue(by["строка J дословно"])
        self.assertFalse(by["доставка = цена зоны"])
        self.assertIn("доставки нет в черновике", r["card"])

    def test_send_delivers_probe_two_messages(self):
        # Проба уходит в ТЕСТОВОЕ окно ДВУМЯ сообщениями (модель+даты, затем пин отдельно).
        sent = []

        async def send(win, text):
            sent.append((win, text))

        def clean(system, user):
            return "Ок, детали ниже."
        r = self._run(getter=self._getter(), resolve_delivery=self._resolve(), call_llm=clean,
                      send=send, test_window="@testwin")
        self.assertEqual(r["status"], "passed", r["card"])
        self.assertEqual(len(sent), 2)                           # два отдельных сообщения
        self.assertTrue(all(w == "@testwin" for w, _ in sent))
        self.assertIn("NMAX", sent[0][1])                        # 1-е: модель + даты
        self.assertIn("google.com/maps", sent[1][1])             # 2-е: пин отдельно

    def test_send_failure_is_failed_step_not_crash(self):
        # Сбой доставки пробы → status='failed' + карточка с причиной (шаг помечается failed), не краш.
        async def boom(win, text):
            raise RuntimeError("нет коннекта к тестовому окну")
        r = self._run(getter=self._getter(), resolve_delivery=self._resolve(), send=boom,
                      test_window="@testwin")
        self.assertEqual(r["status"], "failed")
        self.assertFalse(r["ok"])
        self.assertIn("send упал", r["card"])

    def test_skip_when_test_mode_off(self):
        # БЕЗОПАСНОСТЬ: без SUGGEST_TEST_MODE смоук НИЧЕГО не делает (skipped ≠ провал) — живого
        # клиента проба не касается.
        sent = []

        async def send(win, text):
            sent.append(text)
        with mock.patch.object(suggest, "SUGGEST_TEST_MODE", False):
            r = asyncio.run(suggest.runLiveSmoke(today=self.TODAY, send=send, test_window="@w",
                                                 getter=self._getter(), resolve_delivery=self._resolve()))
        self.assertEqual(r["status"], "skipped")
        self.assertTrue(r["ok"])                                 # skip не считается провалом
        self.assertEqual(r["draft"], "")
        self.assertEqual(sent, [])                               # в окно ничего не ушло


class TestPastStartDateGate(unittest.TestCase):
    """ЖИВОЙ ДЕФЕКТ (тренажёр ТЕСТ-7, 22.07): клиент «с 20 по 25 июля», сегодня 22 июля → бот МОЛЧА
    посчитал котировку (parse_date_range заролил старт на 2027-07-20 — год-ролл _mk сработал как
    «лечение» прошедшей даты). Гейт: старт РАНЬШЕ сегодняшнего дня (по ПХУКЕТУ) → переспрос БЕЗ
    цены; старт сегодня/завтра → штатная котировка + уточнение времени подачи; будущее — без
    изменений; «в декабре про январь» — БУДУЩЕЕ (год-ролл цел). Формат мока Bridge — живой
    (quote_price c day_price/total/deposit/days), как в TestPointQuoteCodeBlock."""

    FLEET = ["NMAX 155CC BLACK PHUKET 4255"]
    TODAY = datetime.date(2026, 7, 22)          # живая дата инцидента

    def setUp(self):
        self._save = (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
                      suggest.pricing.BRIDGE_TOKEN)
        suggest.pricing.PRICING_ACTION = "quote_price"
        suggest.pricing.BRIDGE_URL = "https://x"
        suggest.pricing.BRIDGE_TOKEN = "t"
        suggest.pricing._FLEET_CACHE["data"] = None
        suggest.pricing._FLEET_CACHE["ts"] = 0

    def tearDown(self):
        (suggest.pricing.PRICING_ACTION, suggest.pricing.BRIDGE_URL,
         suggest.pricing.BRIDGE_TOKEN) = self._save
        suggest.pricing._FLEET_CACHE["data"] = None

    def _getter(self):
        def fake(params):
            if params.get("action") == "fleet":
                return {"ok": True, "data": {"bikes": [{"name": n} for n in self.FLEET]}}
            if suggest._bike_key("NMAX 155") not in suggest._bike_key(params.get("bike", "")):
                return {"ok": False}
            ds, de = params.get("date_start"), params.get("date_end")
            days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
            return {"ok": True, "data": {"day_price": 337, "total": 1685, "deposit": 3000,
                    "available": True, "days": days, "cap_active": False, "cap_price": 0}}
        return fake

    def _note(self, text, today=None, lang="ru"):
        today = today or self.TODAY
        hints = suggest.extract_booking_hints(text, today=today)
        return hints, suggest.build_pricing_note(hints, lang=lang, getter=self._getter(), today=today)

    def _assert_no_price(self, note):
        """Ни одной суммы/тарифа: нет «N ฿/бат/baht/THB», нет чисел ЦЕНОВОГО порядка (в белом списке
        пост-чека только дни/месяцы дат, ничего ≥100) — значит ЛЮБОЙ итог, который сочинит LLM,
        будет заклеймлён postcheck_draft."""
        self.assertIsNone(re.search(r"\d[\d\s.,]*\s*(?:฿|бат|baht|thb)", note, re.I),
                          f"в переспросе появилась сумма: {note}")
        big = {n for n in suggest._pc_wl_price_numbers(note) if n >= 100}
        self.assertEqual(big, set(), f"в белый список пост-чека попало число цены: {note}")
        for n in ("337", "1685", "3000"):                 # числа мока Bridge не просочились
            self.assertNotIn(n, note)

    # ---------------- ГОЛДЕН 1: прошедшие даты → переспрос БЕЗ цены ----------------
    def test_golden_past_dates_ask_without_price(self):
        # ДОСЛОВНАЯ фраза живого кейса (правило-класс CLAUDE.md: голден детекта = реальное
        # сообщение клиента, а не идеализированная формулировка).
        hints, note = self._note("[клиент]: Хочу nmax с 20 по 25 июля, сколько будет?")
        self.assertEqual(hints["date_status"], "past")
        self.assertEqual(hints["start_seen"], "2026-07-20")      # прочтение клиента, не ролл 2027
        self.assertIn("УЖЕ ПРОШЁЛ", note)
        self.assertIn("20 июля", note)                            # называем ИМЕННО ту дату
        self.assertIn("22 июля", note)                            # и сегодняшнюю (Пхукет)
        self.assertIn("Уточните, пожалуйста, даты", note)          # дословный пример переспроса
        self.assertIn("август", note)                              # вариант «следующий месяц»
        self.assertNotIn("2027", note)                             # молчаливый год-ролл не всплыл
        self._assert_no_price(note)

    def test_golden_past_dates_paraphrases(self):
        # Парафразы того же живого смысла (RU/EN) — гейт на СТАРТЕ, а не на формулировке.
        for text in ("[клиент]: nmax 20.07-25.07 сколько?",
                     "[клиент]: аренда nmax с 20 июля на неделю, цена?",
                     "[клиент]: сколько стоит nmax с 21 по 30 июля?"):
            with self.subTest(text=text):
                hints, note = self._note(text)
                self.assertEqual(hints["date_status"], "past")
                self.assertIn("УЖЕ ПРОШЁЛ", note)
                self._assert_no_price(note)

    def test_golden_past_dates_en(self):
        hints, note = self._note("[клиент]: nmax 20.07-25.07, how much?", lang="en")
        self.assertEqual(hints["date_status"], "past")
        self.assertIn("IN THE PAST", note)
        self.assertIn("20 July", note)
        self.assertIn("Could you confirm the dates", note)
        self.assertIn("August", note)
        self._assert_no_price(note)

    def test_golden_past_dates_draft_has_no_price(self):
        # СКВОЗЬ ЧЕРНОВИК: LLM всё же сочинил сумму → пост-чек клеймит её (белый список цен пуст,
        # потому что котировки не было). Цена клиенту не уезжает молча.
        _h, note = self._note("[клиент]: Хочу nmax с 20 по 25 июля, сколько будет?")
        draft = suggest.postcheck_draft("Аренда NMAX — 1685 ฿ за 5 дней.", "ru", pricing_note=note)
        self.assertIn("[уточнить:", draft)                         # пометка модератору появилась
        self.assertNotIn("1685 ฿", suggest.client_facing_text(draft))   # сумма клиенту НЕ уехала

    # ---------------- ГОЛДЕН 2: сегодня/завтра → котировка + время подачи ----------------
    def test_golden_start_today_quotes_and_asks_pickup_time(self):
        hints, note = self._note("[клиент]: Хочу nmax с 22 по 27 июля, сколько будет?")
        self.assertEqual(hints["date_status"], "today")
        self.assertIn("1685", note)                                # штатная котировка на месте
        self.assertIn("ЦЕНА из Календаря", note)
        self.assertIn("ВРЕМЯ ПОДАЧИ", note)                        # + уточнение времени подачи
        self.assertIn("СЕГОДНЯ", note)
        self.assertNotIn("УЖЕ ПРОШЁЛ", note)

    def test_golden_start_tomorrow_quotes_and_asks_pickup_time(self):
        hints, note = self._note("[клиент]: Хочу nmax с 23 по 28 июля, сколько будет?")
        self.assertEqual(hints["date_status"], "tomorrow")
        self.assertIn("1685", note)
        self.assertIn("ВРЕМЯ ПОДАЧИ", note)
        self.assertIn("ЗАВТРА", note)

    def test_golden_pickup_note_en(self):
        _h, note = self._note("[клиент]: nmax 23.07-28.07, how much?", lang="en")
        self.assertIn("1685", note)
        self.assertIn("PICK-UP TIME", note)
        self.assertIn("tomorrow", note)

    # ---------------- ГОЛДЕН 3: будущее — штатная котировка (регресс) ----------------
    def test_golden_future_dates_unchanged(self):
        hints, note = self._note("[клиент]: Хочу nmax с 28 июля по 5 августа, сколько будет?")
        self.assertEqual(hints["date_status"], "future")
        self.assertIn("1685", note)
        self.assertIn("337", note)
        self.assertNotIn("УЖЕ ПРОШЁЛ", note)
        self.assertNotIn("ВРЕМЯ ПОДАЧИ", note)                     # подача не при чём — старт не скоро
        self.assertIsNotNone(suggest._quote_block_from_note(note))  # quote-блок цел (транспорт #92)

    def test_golden_future_note_byte_for_byte_as_before_gate(self):
        # РЕГРЕСС: на будущем старте нота ПОБАЙТОВО равна ноте, собранной без участия гейта
        # (hints без date_status — как их собирает вызывающий вручную).
        hints, note = self._note("[клиент]: Хочу nmax с 28 июля по 5 августа, сколько будет?")
        bare = {k: v for k, v in hints.items() if k not in ("date_status", "start_seen")}
        note_bare = suggest.build_pricing_note(bare, lang="ru", getter=self._getter(),
                                               today=self.TODAY)
        self.assertEqual(note, note_bare)

    # ---------------- ГОЛДЕН 4: ГОД-РОЛЛ (декабрь → январь) = БУДУЩЕЕ ----------------
    def test_golden_year_roll_january_from_december_is_future(self):
        # Клиент в декабре пишет «с 5 по 10 января» → это ЯНВАРЬ СЛЕДУЮЩЕГО года (+14 дней), а НЕ
        # прошлое: ближайшее вхождение — будущее. Котируем штатно, переспроса быть НЕ должно.
        dec = datetime.date(2025, 12, 22)
        hints, note = self._note("[клиент]: nmax с 5 по 10 января, сколько?", today=dec)
        self.assertEqual((hints["iso_start"], hints["iso_end"]), ("2026-01-05", "2026-01-10"))
        self.assertEqual(hints["date_status"], "future")
        self.assertEqual(hints["start_seen"], "2026-01-05")
        self.assertIn("1685", note)                                # цена названа
        self.assertNotIn("УЖЕ ПРОШЁЛ", note)

    def test_golden_year_roll_dec_to_jan_range_is_future(self):
        # «с 28 декабря по 3 января» в декабре — переход через год, старт в БУДУЩЕМ (штатная ветка).
        dec = datetime.date(2025, 12, 22)
        hints, note = self._note("[клиент]: nmax с 28 декабря по 3 января", today=dec)
        self.assertEqual((hints["iso_start"], hints["iso_end"]), ("2025-12-28", "2026-01-03"))
        self.assertEqual(hints["date_status"], "future")
        self.assertIn("1685", note)

    def test_nearest_occurrence_rule_both_directions(self):
        # Правило «ближайшее вхождение» посимвольно: −2 дня → прошлое, −351 день → следующий год.
        jul = datetime.date(2026, 7, 22)
        self.assertEqual(suggest.start_date_status("2027-07-20", jul), "past")     # ролл «лечил» прошлое
        self.assertEqual(suggest.start_date_status("2027-01-20", jul), "future")   # −183 дня → следующий год
        dec = datetime.date(2025, 12, 22)
        self.assertEqual(suggest.start_date_status("2026-01-05", dec), "future")   # год-ролл цел
        # ЯВНЫЙ год клиента снимает неоднозначность — читаем буквально, ближайшее вхождение не ищем.
        self.assertEqual(suggest.start_date_status("2027-07-20", jul, "с 20 по 25 июля 2027"), "future")
        self.assertEqual(suggest.start_date_status("2026-07-20", jul, "20.07.2026-25.07.2026"), "past")
        self.assertIsNone(suggest.start_date_status(None, jul))                    # нет старта → гейт молчит
        self.assertIsNone(suggest.start_date_status("не дата", jul))

    # ---------------- ГОЛДЕН 5: ТАЙМЗОНА — решаем по Пхукету, не по UTC ----------------
    def test_golden_timezone_phuket_wins_on_day_border(self):
        # ГРАНИЦА СУТОК: UTC 21.07 18:30 = 22.07 01:30 на Пхукете. По UTC «сегодня» = 21 июля, по
        # Пхукету — 22 июля.
        utc_now = datetime.datetime(2026, 7, 21, 18, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(utc_now.date(), datetime.date(2026, 7, 21))               # UTC-дата
        self.assertEqual(suggest.today_phuket(utc_now), datetime.date(2026, 7, 22))  # дата Пхукета
        self.assertEqual(suggest.now_phuket(utc_now).hour, 1)                       # 01:30 по Пхукету

    def test_golden_timezone_decides_gate_on_day_border(self):
        # Тот же момент, живой запрос «с 21 по 26 июля»: по UTC старт = «сегодня» (котировали бы),
        # по ПХУКЕТУ он УЖЕ ПРОШЁЛ → переспрос БЕЗ цены. Решение принимается по Пхукету.
        utc_now = datetime.datetime(2026, 7, 21, 18, 30, tzinfo=datetime.timezone.utc)
        text = "[клиент]: nmax с 21 по 26 июля, сколько?"
        _h_utc, note_utc = self._note(text, today=utc_now.date())                  # КАК БЫЛО БЫ по UTC
        self.assertIn("1685", note_utc)                                            # по UTC — котировка
        hints, note = self._note(text, today=suggest.today_phuket(utc_now))        # как ДОЛЖНО быть
        self.assertEqual(hints["date_status"], "past")
        self.assertIn("УЖЕ ПРОШЁЛ", note)
        self.assertIn("21 июля", note)
        self._assert_no_price(note)

    def test_today_phuket_offset_is_utc_plus_7_without_tzdata(self):
        # Смещение фиксированное +07:00 (Таиланд без DST) — голден зелёный на ЛЮБОМ интерпретаторе,
        # в т.ч. без пакета tzdata (системный python этого ПК его не имеет).
        self.assertEqual(suggest.PHUKET_TZ.utcoffset(None), datetime.timedelta(hours=7))
        utc_now = datetime.datetime(2026, 1, 15, 23, 10, tzinfo=datetime.timezone.utc)  # зима: DST нет
        self.assertEqual(suggest.now_phuket(utc_now).hour, 6)
        self.assertEqual(suggest.today_phuket(utc_now), datetime.date(2026, 1, 16))
        naive = datetime.datetime(2026, 1, 15, 23, 10)      # наивный трактуем как UTC, не как локаль ПК
        self.assertEqual(suggest.today_phuket(naive), datetime.date(2026, 1, 16))

    # ---------------- ПРАЙС ПО ПАРКУ: сетку не блокируем, но и по прошлому не считаем ----------------
    def test_price_sheet_past_dates_keeps_grid_and_asks_dates(self):
        # Владельческое «даты НЕ гейт» цело: прайс выдаём (анти-луп), но якорь — ближайшая дата,
        # а не прошедшая/заролленная, плюс просьба уточнить даты.
        hints = suggest.extract_booking_hints(
            "[клиент]: пришлите цены на все модели, аренда с 20 по 25 июля", today=self.TODAY)
        self.assertEqual(hints["date_status"], "past")
        note = suggest.build_pricing_note(hints, lang="ru", getter=self._getter(), today=self.TODAY)
        self.assertIn("ПРАЙС ПО ПАРКУ", note)                       # сетка на месте
        self.assertIn("старт завтра", note)                          # якорь = ближайшая дата
        self.assertIn("уже прошло", note)                            # и просьба уточнить даты
        self.assertNotIn("2027", note)                               # по заролленному году не считали

    # ---------------- анти-тайский ----------------
    def test_no_thai_in_gate_texts(self):
        for lang in ("ru", "en"):
            _h, past = self._note("[клиент]: nmax 20.07-25.07, сколько?", lang=lang)
            _h2, pickup = self._note("[клиент]: nmax 23.07-28.07, сколько?", lang=lang)
            self.assertFalse(_has_thai_letters(past), f"тайские буквы в переспросе ({lang})")
            self.assertFalse(_has_thai_letters(pickup), f"тайские буквы в подаче ({lang})")
            self.assertFalse(_has_thai_letters(suggest._past_start_sheet_note(
                datetime.date(2026, 7, 20), self.TODAY, lang)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
