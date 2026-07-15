# -*- coding: utf-8 -*-
"""
suggest.py — ступень ① SUGGEST для userbot @turbophuket.

На входящее ЛС клиента готовит ЧЕРНОВИК ответа (LLM), отправляет его НЕ клиенту,
а на МОДЕРАЦИЮ (группа «Модерация ответов»); после одобрения reply-командой
менеджера userbot САМ шлёт клиенту. Клиент видит только одобренное.

БЕЗОПАСНОСТЬ:
  • SUGGEST_MODE по умолчанию OFF → userbot ведёт себя как чистый Stage C.
  • Отправку делает ТОЛЬКО единый userbot-Telethon-процесс (второй клиент = бан-риск).
  • Гарды: пауза 30–60с, лимит 6/час и 15/день, только существующие входящие
    диалоги, никаких первых сообщений/рассылок, typing-статус.
  • FloodWait/PeerFlood → backoff + авто-стоп SUGGEST + запись в лог.

Вся содержательная логика — здесь и без жёсткой зависимости от живого Telegram/
Anthropic (обе стороны инъектируемы), чтобы покрывать мок-тестами. Точки врезки в
userbot_listen.py — тонкие (on_client_message / on_moderation_reply).
"""

import os
import re
import glob
import json
import time
import random
import shutil
import asyncio
import logging
import datetime
import tempfile
import subprocess

import pricing  # каркас получения точной цены из Календаря (Bridge); пусто → фолбэк
import delivery  # резолвер зоны/цены доставки по maps-ссылке клиента (Bridge); пусто → [уточнить]

log = logging.getLogger("suggest")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ------------------------------- конфиг (env) --------------------------------

def _flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "да")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (ValueError, AttributeError):
        return default


SUGGEST_MODE = _flag("SUGGEST_MODE", False)          # главный рубильник, по умолч. OFF
SUGGEST_TEST_MODE = _flag("SUGGEST_TEST_MODE", False)  # обкатка: черновики есть, отправки клиенту НЕТ

# ИЗОЛЯЦИЯ ТЕСТОВ: TESTING=1 (ставит test_isolation) → боевой IPC/токен недоступны по построению.
# moderation_ipc под TESTING уводит БД в temp и блокирует боевую очередь; здесь bot_mode_active
# читает уже изолированный (пустой) IPC → нет heartbeat → reply-режим → ничего не уходит наружу.
TESTING = _flag("TESTING", False)

_mg = os.getenv("MOD_GROUP_ID", "").strip()
MOD_GROUP_ID = int(_mg) if _mg.lstrip("-").isdigit() else None  # нет ID → резолв по имени/лог
MOD_GROUP_NAME = os.getenv("MOD_GROUP_NAME", "").strip()        # резолв группы по title, если ID пуст

RATE_PER_HOUR = _int("SUGGEST_RATE_HOUR", 6)
RATE_PER_DAY = _int("SUGGEST_RATE_DAY", 15)
PAUSE_MIN = _int("SUGGEST_PAUSE_MIN", 30)
PAUSE_MAX = _int("SUGGEST_PAUSE_MAX", 60)

# Первый контакт: нет НАШИХ (менеджера) сообщений за последние N часов → черновик с приветствием.
FIRST_CONTACT_HOURS = _int("SUGGEST_FIRST_CONTACT_HOURS", 18)


def _csv_set(name: str):
    """Разобрать список юзернеймов из env (разделитель — запятая/пробел), в lower без @."""
    raw = os.getenv(name, "") or ""
    return {p.strip().lstrip("@").lower() for p in re.split(r"[,\s]+", raw) if p.strip()}


# Право approve/edit/reject. ПУСТОЙ список = любой участник группы может approve.
APPROVER_USERNAMES = _csv_set("APPROVER_USERNAMES")

# Право «учить» бота (перехват реплая-правки/урока на карточке черновика, родитель 292) —
# СТРОЖЕ обычного approve: только владельцы-учителя (Филипп ×2 аккаунта, Даня, Даша).
# Источник — env INTAKE_APPROVERS (CSV юзернеймов); сами юзернеймы в .env, не в коде.
# Пусто → фолбэк на APPROVER_USERNAMES (обучение НЕ шире approve, а не «кто угодно»).
INTAKE_APPROVERS = _csv_set("INTAKE_APPROVERS")

# Токен бота-модератора (задача-2). Пусто → бот не поднимается, работает деградация
# (reply-режим userbot). Токен НИКОГДА не логируем и не коммитим.
MODERBOT_TOKEN = os.getenv("MODERBOT_TOKEN", "").strip()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6").strip()
LLM_MAX_TOKENS = _int("SUGGEST_MAX_TOKENS", 1000)
# Генерация через claude CLI (подписка Max) вместо платного API-ключа. Флаг .env SUGGEST_LLM_VIA_CLI=1.
SUGGEST_LLM_VIA_CLI = _flag("SUGGEST_LLM_VIA_CLI")
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude").strip()   # фолбэк-путь (может протухнуть при автообновлении)
CLI_TIMEOUT = _int("SUGGEST_CLI_TIMEOUT", 60)
CLI_MODEL = os.getenv("SUGGEST_CLI_MODEL", "sonnet").strip()   # legacy-алиас (совместимость); вытеснен SUGGEST_MODEL
# Голова suggest: основная модель + кондуктор-фолбэк. Фолбэк исполняет САМ CLI одним вызовом
# (--fallback-model): если основная недоступна/ошиблась — тем же процессом добивает фолбэком, свой
# ретрай не городим. Реально отработавшую голову достаём из modelUsage (--output-format json) и логируем.
# Дефолты разумны и без .env: fable как основная, прежняя sonnet как фолбэк.
SUGGEST_MODEL = os.getenv("SUGGEST_MODEL", "fable").strip() or "fable"
SUGGEST_MODEL_FALLBACK = os.getenv("SUGGEST_MODEL_FALLBACK", "sonnet").strip()

# Управляемый УРОВЕНЬ НАПОРА продаж — НАД базовой установкой (не ниже, как playbook). soft|normal|firm;
# дефолт normal = текущее поведение. firm активнее ведёт к брони/предоплате, но инварианты
# (цена/депозит/парк/без агрессии/без обмана) держатся. Источник — env SALES_PRESSURE (перечитывается
# при рестарте процесса); бизнес-config, а не контент → держим отдельно от playbook.
_SALES_PRESSURE_LEVELS = ("soft", "normal", "firm")
SALES_PRESSURE = os.getenv("SALES_PRESSURE", "normal").strip().lower()
if SALES_PRESSURE not in _SALES_PRESSURE_LEVELS:
    SALES_PRESSURE = "normal"

# Порядок чтения живого FAQ из Brain: «faq» (живой ключ) → «turbobaby_faq» → локальный файл.
FAQ_DOC_ORDER = [s.strip() for s in os.getenv("FAQ_DOCS", "faq,turbobaby_faq").split(",") if s.strip()]
BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()
LOCAL_FAQ = os.path.join(BASE_DIR, "manager-bot", "docs", "turbobaby_faq_v1.md")

PAIRS_FILE = os.path.join(BASE_DIR, "suggest_pairs.jsonl")      # лог обучения
PENDING_FILE = os.path.join(BASE_DIR, "suggest_pending.jsonl")  # pending-store (durable)

MAX_MESSAGES = 50  # глубина транскрипта диалога

# Telethon-ошибки флуда (ленивый импорт — модуль тестируется и без telethon).
try:
    from telethon.errors import FloodWaitError, PeerFloodError
    FLOOD_ERRORS = (FloodWaitError, PeerFloodError)
except Exception:  # pragma: no cover
    FLOOD_ERRORS = ()


# ----- критичные факты ДОСЛОВНО (в промпт всегда, чтобы перевод не искажал) ----
# Источник: turbobaby_faq_v1.md (прайс из CRM). Правило депозита и Click — жёсткие.
CRITICAL_FACTS = """КРИТИЧНЫЕ ФАКТЫ (не искажать, числа дословно):
Депозит: либо деньги, либо паспорт (НЕ оба). Принимаем: наличные баты, перевод
(в т.ч. USDT TRC20/BEP20, тайский счёт, Сбер/Тинькофф), либо паспорт вместо денег.
Прайс-ОРИЕНТИР ДЛЯ МЕНЕДЖЕРА (клиенту цену — в т.ч. «от X»/диапазон — НЕ называть;
точную называет только менеджер/Календарь по датам) (฿/день, депозит ฿):
- Скутеры: PCX150 349/3000; ADV150 449/3000; NMAX155 449/3000; PCX160 498/5000;
  ADV160 498/5000; FORZA300 690/5000; XMAX300(2020-2022) 790/5000;
  XMAX300(NEW 2023+) 939/7000; ADV350 998/7000; XADV750 2788/25000.
- Мотоциклы: XSR155 590/7000; CB300R 890/15000; REBEL300 890/15000; MT-03 1090/15000;
  NINJA400 1185/20000; VULCAN650S 1798/20000; CBR650R 1798/20000; CB650R 1798/20000;
  XSR900 1798/20000; R7 2298/25000.
- Скидки за срок: неделя ~6-15%, 2 недели ~15-25%, месяц ~35-50%.
Доставка по районам (฿): Патонг/Банг Тао/Сурин/Камала 290; Карон/Паклок 390;
Ката/Кату 490; Раваи/Найхарн/Чалонг 590; Аэропорт 590-690. Забор бесплатно.
HONDA CLICK 125 — НЕ сдаём. На запрос Click предлагать PCX150 / ADV150 / NMAX155.
Наличие байка на даты НЕ подтверждать без проверки — уточнить даты и сказать, что проверим."""


# ЖЁСТКОЕ правило про опыт/безопасность (в промпт ВСЕГДА, рядом с критфактами — высокий приоритет).
# Закрывает A.3-утечку: бот НЕ выносит СОБСТВЕННУЮ оценку безопасности/пригодности модели клиенту
# без опыта — только нейтральная пауза/уточнение опыта/передача менеджеру.
EXPERIENCE_SAFETY_RULE = (
    "\n\nОПЫТ И БЕЗОПАСНОСТЬ (ЖЁСТКО): если клиент явно без опыта управления мотоциклом (новичок) "
    "или просит мощный/крупный байк без подтверждённого опыта — ТЫ НЕ даёшь СОБСТВЕННУЮ оценку "
    "безопасности или пригодности модели. Запрещено писать клиенту: «эта модель опасна/небезопасна "
    "для вас», «не советую», «лучше не», «рискованно», «сложна в управлении для новичка»; не "
    "отговаривай и не подтверждай пригодность. Вместо оценки — нейтрально: мягко уточни опыт (на чём "
    "и как долго ездил) и/или скажи, что подберёшь подходящий вариант вместе с менеджером и вернёшься. "
    "Вывод «подходит/не подходит по опыту» и любую оценку рисков модели даёт ТОЛЬКО менеджер, не ты."
)


# БЕЛЫЙ СПИСОК УТВЕРЖДАЕМОГО (в промпт ВСЕГДА, рядом с критфактами — высокий приоритет).
# Бот вправе УТВЕРЖДАТЬ клиенту ТОЛЬКО факты из проверяемых источников: цены/депозиты из блока
# ЦЕНА/ПРАЙС (quote и сетка), перечень моделей реального парка (Лист1), минимальные сроки аренды,
# правило депозита. Всё ВНЕ списка (наличие КОНКРЕТНОГО байка на даты, цвет, комплектация, сроки
# подготовки/выдачи) — НЕ утверждать: «уточню у команды и вернусь» + пометка менеджеру в черновике
# вида «[уточнить: цвет ADV350]». Пометка в квадратных скобках адресована МОДЕРАТОРУ (он уточнит и
# правит перед одобрением) — _strip_service_prefix её не срезает (маркер «[внутренн», не «[уточнить»).
APPROVAL_WHITELIST_RULE = (
    "\n\nЧТО МОЖНО УТВЕРЖДАТЬ (БЕЛЫЙ СПИСОК, ЖЁСТКО): клиенту как ФАКТ подтверждай ТОЛЬКО: "
    "(1) цены и депозиты — исключительно из блока ЦЕНА/ПРАЙС выше (quote/сетка), дословно; "
    "(2) перечень моделей — только реальный парк (список ПАРК выше / Лист1); "
    "(3) минимальные сроки аренды (скутеры от 5 дней, мотоциклы от 3); "
    "(4) правило депозита (деньги ЛИБО паспорт, не оба). "
    "ВСЁ ОСТАЛЬНОЕ утверждать ЗАПРЕЩЕНО — в частности: наличие КОНКРЕТНОГО байка на нужные даты, "
    "цвет, комплектацию/опции, сроки подготовки или выдачи. Такое НЕ подтверждай и НЕ обещай: "
    "клиенту напиши «уточню у команды и вернусь» (на языке клиента), а в КОНЦЕ черновика ОТДЕЛЬНОЙ "
    "строкой добавь пометку менеджеру в квадратных скобках вида «[уточнить: цвет ADV350]» "
    "(коротко: что именно уточнить и по какой модели). Эта пометка — для менеджера-модератора, не "
    "для клиента; в самом тексте клиенту квадратных скобок и слова «[уточнить…]» не пиши."
)


# ИНВАРИАНТ НАЛИЧИЯ/ДЕФИЦИТА/ОСОБЫХ УСЛОВИЙ (в промпт ВСЕГДА, рядом с белым списком — высокий
# приоритет). У бота НЕТ данных о складе, дефиците и персональных условиях: наличие проверяет только
# Календарь/менеджер, спеццены/скидки назначает менеджер. APPROVAL_WHITELIST_RULE перекрывает наличие
# конкретного байка; здесь ДОБАВЛЕНЫ дефицит-срочность и особые условия (белый список их явно не
# называл) и запрет собран одним жёстким блоком. Программно страхует guard_availability (родитель4,
# шаг 4/6): клейм наличия/дефицита/спецусловий без данных → перегенерация, затем безопасный фолбэк.
AVAILABILITY_INVARIANT_RULE = (
    "\n\nНАЛИЧИЕ, ДЕФИЦИТ И ОСОБЫЕ УСЛОВИЯ (ЖЁСТКО, БЕЗ ДАННЫХ — НЕ УТВЕРЖДАТЬ): у тебя НЕТ данных о "
    "складе, дефиците и персональных условиях. Клиенту как ФАКТ ЗАПРЕЩЕНО: (1) подтверждать или "
    "отрицать наличие/занятость конкретного байка на даты («свободен»/«в наличии», «занят»/«нет в "
    "наличии»); (2) создавать дефицит и срочность («последний», «остался один», «почти всё "
    "разобрали», «разбирают», «успевайте», «только сегодня»); (3) обещать особые/персональные "
    "условия, спеццены или скидки «специально для вас». Ничего из этого не выдумывай и не намекай. "
    "Наличие проверяет Календарь/менеджер, особые условия назначает менеджер — вместо утверждения "
    "напиши, что уточнишь наличие по датам/модели и вернёшься, без чисел скидок и без обещаний."
)


# ПОКОЛЕНИЯ МОДЕЛИ — по умолчанию только актуальное (в промпт ВСЕГДА, рядом с инвариантом наличия).
# Живой провал @cryptopeppa (03:13 15.07): на «XMAX 300 на 5 дней» бот сам вываливал ОБА поколения с
# годами выпуска («старый 2020-2022 / новый 2023+») и спрашивал «старое или новое?», хотя клиент про
# поколения не спрашивал. Глобальное правило: по умолчанию предлагаем ЛИШЬ актуальное поколение (New
# Gen) — без годов и без слова «старый»; прежнее поколение и года — ТОЛЬКО по явному запросу клиента.
# Код-страховка того же класса: _model_products_for_quote отдаёт прежнее поколение лишь при old_gen_q.
# Наличие при этом НЕ ослабляем (AVAILABILITY_INVARIANT_RULE выше остаётся в силе).
GENERATION_DEFAULT_RULE = (
    "\n\nПОКОЛЕНИЯ МОДЕЛИ (ПО УМОЛЧАНИЮ — ТОЛЬКО АКТУАЛЬНОЕ): если у модели несколько поколений "
    "(напр. XMAX 300 — прежнее и New Gen), по умолчанию предлагай клиенту ТОЛЬКО актуальное "
    "поколение (New Gen) ОДНОЙ ценой. НЕ пиши года выпуска («2020-2022», «2023+», «2021 года» и "
    "т.п.), НЕ пиши слово «старый»/«старое поколение» и НЕ спрашивай клиента «старое или новое?» — "
    "он про поколения не спрашивал. Прежнее поколение и года упоминай ТОЛЬКО если клиент САМ явно "
    "про них спросил (напр. «а старый xmax есть?») — тогда приведи обе строки-поколения. Это правило "
    "про то, ЧТО предлагать (модель/поколение), а НЕ про наличие: наличие конкретного байка на даты "
    "по-прежнему НЕ утверждай (правило наличия выше остаётся в силе)."
)


# ЗАБОР БАЙКА В КОНЦЕ АРЕНДЫ — перк ОПЛАЧЕННОЙ доставки (в промпт ВСЕГДА, рядом с правилами доставки).
# Шаг 5/7 #22. Бесплатный забор законен ТОЛЬКО когда клиент оплатил доставку (мы привезли байк — мы
# же бесплатно заберём его в конце). Без доставки (клиент забирает/сдаёт сам, «точка без доставки»,
# самовывоз) бесплатного забора НЕТ: обратный забор стоит столько же, сколько доставка в ту же зону.
# Самопротиворечие «заберём бесплатно» вместе с «заберёте сами / самовывоз / без доставки» в одном
# ответе ЗАПРЕЩЕНО. Код-страховка того же класса — postcheck_free_pickup: снимает обещание бесплатного
# забора при сигнале самовывоза в черновике.
PICKUP_RULE = (
    "\n\nЗАБОР БАЙКА В КОНЦЕ АРЕНДЫ (ЖЁСТКО): бесплатный забор — это перк ОПЛАЧЕННОЙ доставки (мы "
    "привезли байк — мы же бесплатно заберём его в конце). Если клиент забирает и сдаёт байк САМ "
    "(самовывоз, доставку не берёт, «точка без доставки») — бесплатного забора НЕТ: обратный забор "
    "стоит столько же, сколько доставка в ту же зону. Бесплатный забор обещай ТОЛЬКО когда назвал "
    "клиенту цену ОПЛАЧЕННОЙ доставки по району. НИКОГДА не пиши самопротиворечие «заберём бесплатно» "
    "вместе с «заберёте сами / самовывоз / без доставки» в одном ответе: при самовывозе про "
    "бесплатный забор НЕ обещай (либо промолчи про забор, либо скажи, что забор платный — как "
    "доставка той же зоны)."
)


# --- СТИЛЬ РЕАЛЬНЫХ МЕНЕДЖЕРОВ + FEW-SHOT (шаг 5/7 родитель #253) ----------------
# Собрано из ЖИВОЙ базы переписок с клиентами (client_chats.jsonl, роль company =
# наш менеджер @turbophuket). Это ЭТАЛОН ТОНА/ФОРМУЛИРОВОК/ДЛИНЫ, а НЕ новый источник
# фактов: ценовую политику, критфакты (CRITICAL_FACTS), парк (Лист1) и правило
# опыта/безопасности (EXPERIENCE_SAFETY_RULE) ВЫШЕ он НЕ отменяет — они приоритетнее.
# Числа/цены в примерах — иллюстрация ФОРМАТА, а НЕ разрешение называть цену без дат:
# конкретную цену берём ТОЛЬКО из блока ЦЕНА, дословно, как велит ценовая политика.
STYLE_GUIDE = (
    "СТИЛЬ ОТВЕТА (как реально пишут наши менеджеры — держись этого тона и длины):\n"
    "• Коротко и по делу — обычно 1–3 короткие фразы, без «простыней»; на один вопрос — один ответ.\n"
    "• Живая разговорная речь, тепло и вежливо, без канцелярита и без пафоса.\n"
    "• Приветствие («Привет!» / «Здравствуйте!») — РОВНО ОДИН раз за диалог, в первом ответе; дальше сразу по сути, без повторного «здравствуйте».\n"
    "• Эмодзи — УМЕРЕННО: 0–1 на сообщение (🤝 😎 🙏 ☺️), не в каждой строке и не гроздьями.\n"
    "• Наличие конкретного байка / точные сроки готовности / цвет заранее НЕ обещаем: «сейчас уточним и вернёмся».\n"
    "• Про оплату спрашиваем мягко и открытым вопросом: «Оплата каким способом удобнее будет?», «Каким способом удобно внести — наличными батами или переводом?».\n"
    "• Про бронь: «на эти даты бронь закрепляется по 100% предоплате».\n"
    "• Доставку называем суммой по району и добавляем, что забор байка в конце аренды — бесплатный (это перк ОПЛАЧЕННОЙ доставки; при самовывозе бесплатный забор НЕ обещаем).\n"
    "• Про опыт спрашиваем БЕЗ оценок «опасно/не советую»: «нужно чуть понимание по опыту — какие модели были и как долго?».\n"
    # шаг 6/7 #253 — ПОВЕРХ стиля: не дублируем приветствие, не переспрашиваем уже данное, без канцелярита.
    "• Приветствие НЕ повторяем: если наше «Здравствуйте / Привет» в этом диалоге уже было — сразу по сути, без второго приветствия и без «ещё раз здравствуйте».\n"
    "• НЕ переспрашиваем уже данное: если клиент НАЗВАЛ или катал скутер (PCX, NMAX, ADV, Forza, XMAX и т.п.) — это и ЕСТЬ опыт на скутерах; НЕ спрашивай снова «на чём ездили / какой был опыт», а тепло сошлись на уже сказанном: «опыт на скутерах — отлично».\n"
    "• БЕЗ канцелярита: не пиши «Спасибо за информацию — записала», «принято к сведению», «зафиксировала запрос»; и если расчёт/цена уже готовы — не отписывайся «уточню и вернусь с расчётом», а сразу дай цифры и следующий шаг."
)

# Пары «клиент → НАШ реальный ответ» (из client_chats.jsonl; обезличено: убраны ссылки-карты,
# номера юнитов, агентские «комиссия/%»). Ориентир по ТОНУ и длине, НЕ источник цен/наличия.
STYLE_FEWSHOT_PAIRS = [
    ("Здравствуйте! Хочу арендовать байк",
     "Здравствуйте, спасибо, что выбрали нас 🤝 Напишите, пожалуйста, что хотели бы арендовать, "
     "с какого числа и на какой срок. И пару слов про опыт вождения — на чём и как долго ездили, "
     "так проще подобрать вариант 😎"),
    ("Сколько стоит аренда?",
     "Подскажите модель и даты — с какого по какое число, и посчитаю точную стоимость на этот период."),
    ("Скажите, какие байки у вас есть?",
     "Из скутеров — нмаксы 155, хмакс 300, форза 300, адв 350, х-адв 750. Из мотоциклов — xsr 155, "
     "cb300r, mt-03, ninja 400, cb650r, cbr650r, vulcan 650s. Что из этого интересно?"),
    ("А есть honda click?",
     "Клик мы не сдаём, но есть нмакс 155 — отличная замена, комфортнее и надёжнее. Подойдёт?"),
    ("Nmax с 8 по 14 марта, какая цена?",
     "NMAX 155CC | дней: 7, стоимость: 3 099 бат (443 в день), депозит: 3 000 бат."),
    ("А депозит какой и как вносить?",
     "Депозит — деньги либо паспорт, на выбор (не то и другое). Деньги можно наличными батами или "
     "переводом. Каким способом удобнее?"),
    ("Хочу забронировать на февраль",
     "На эти даты бронь закрепляется по 100% предоплате. Удобно внести сегодня? И продублируйте, "
     "пожалуйста, модель и даты."),
    ("Сколько доставка на Найхарн?",
     "Найхарн — 590 бат доставка, забор байка в конце аренды бесплатный."),
    ("Nmax на эти даты есть в наличии?",
     "Сейчас уточним по наличию на ваши даты и вернёмся ☺️"),
    ("Можно на 2 дня?",
     "Скутеры у нас от 5 дней, мотоциклы от 3. На какие даты рассматриваете?"),
    ("А если брать на подольше, скидка будет?",
     "Да, за срок скидка растёт: неделя ~6–15%, две недели ~15–25%, месяц ~35–50%. Назовите даты — "
     "посчитаю точно."),
    ("Хочу что-то мощное, около 650 кубов",
     "Тут нужно чуть понимание по опыту — на каких байках ездили и как долго? Так подберём подходящий "
     "вариант вместе."),
    ("Дороговато выходит(",
     "Понимаю. У нас честный и качественный сервис — байки в хорошем состоянии, поддержка на связи, "
     "останетесь довольны."),
    ("Можно продлить на тех же условиях?",
     "Давайте проверим по срокам и вернёмся — на какие даты хотите продлить?"),
    ("Байк готов? Когда можно забрать?",
     "Ребята уже готовят байк, будет готов к вашему приезду — можете выезжать."),
    ("Hi! How much is a scooter for a week?",
     "Hi! Which model and what dates — from when to when? I'll calculate the exact price for those days."),
    ("What about the deposit?",
     "Deposit is either cash or your passport — your choice, not both. Cash in Thai baht or a transfer works."),
    ("Is the NMAX available for these dates?",
     "Let me check availability for your dates and I'll get back to you ☺️"),
]


def _render_fewshot(pairs):
    """Пары → компактный текстовый блок для промпта (клиент → наш ответ)."""
    return "\n".join(f"— Клиент: {c}\n  Мы: {a}" for c, a in pairs)


STYLE_FEWSHOT = (
    "\n\n" + STYLE_GUIDE
    + "\n\nПРИМЕРЫ ЖИВЫХ ОТВЕТОВ (клиент → наш реальный ответ; ориентир по ТОНУ и длине, "
    "НЕ источник цен/наличия — цену бери только из блока ЦЕНА):\n"
    + _render_fewshot(STYLE_FEWSHOT_PAIRS)
)


# ------------------------------- парк (Лист1) --------------------------------
# Клиенту показываем ТОЛЬКО модели РЕАЛЬНОГО парка (Байки.xlsx Лист1). Источник правды:
# живой pricing.fleet() (Bridge action=fleet, отражает Лист1), фолбэк — локальный park_list.md.
PARK_LIST_FILE = os.path.join(BASE_DIR, "manager-bot", "docs", "park_list.md")

# Универсум известных моделей: (как показывать клиенту, ключ для матчинга в имени байка парка).
# В allowlist попадают ТОЛЬКО те, что реально в парке; вне парка (PCX/ADV150-160/Rebel/XSR900/R7/
# CB650R) — отсеиваются автоматически (их нет среди имён байков).
KNOWN_MODELS = [
    ("NMAX 155", "nmax155"), ("XMAX 300", "xmax300"), ("ADV 350", "adv350"),
    ("ADV 160", "adv160"), ("ADV 150", "adv150"), ("PCX 160", "pcx160"), ("PCX 150", "pcx150"),
    ("FORZA 300", "forza300"), ("XADV 750", "xadv750"),
    ("XSR 155", "xsr155"), ("XSR 900", "xsr900"),
    ("CBR 650R", "cbr650r"), ("CB 650R", "cb650r"), ("CB 300R", "cb300r"),
    ("REBEL 300", "rebel300"), ("MT-03", "mt03"), ("NINJA 400", "ninja400"),
    ("VULCAN 650S", "vulcan650s"), ("R7", "r7"), ("CLICK 125", "click125"),
]


def _bike_key(name):
    """Имя байка → alnum-ключ (снимаем CC/СС и не-alnum) для матчинга с моделью."""
    s = re.sub(r"(?i)[сc][сc]", "", str(name or ""))   # убрать CC и кириллич. СС
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _park_bike_names(getter=None):
    """Имена байков реального парка. Приоритет — живой Bridge pricing.fleet() (отражает Лист1),
    фолбэк — локальный park_list.md (снимок Байки.xlsx Лист1). → list[str] (или [] если нет источника)."""
    try:
        bikes = pricing.fleet(_get=getter)
        names = [b.get("name") for b in (bikes or []) if isinstance(b, dict) and b.get("name")]
        if names:
            return names
    except Exception as e:
        log.info(f"park: fleet упал ({type(e).__name__}) — пробую park_list.md")
    try:
        names = []
        with open(PARK_LIST_FILE, encoding="utf-8") as f:
            for ln in f:
                if not ln.strip().startswith("|"):
                    continue
                cols = [c.strip() for c in ln.strip().strip("|").split("|")]
                if len(cols) >= 2 and cols[1] and cols[1].lower() not in ("название", "name") \
                        and not set(cols[1]) <= set("- "):   # пропускаем шапку и разделитель |---|
                    names.append(cols[1])
        return names
    except Exception as e:
        log.info(f"park: park_list.md не прочитан ({type(e).__name__})")
    return []


def park_allowlist(getter=None):
    """Модели РЕАЛЬНОГО парка (Лист1) для показа клиенту. → list[str] отображаемых имён ИЛИ None.
    None = источник недоступен/пуст → НЕ ограничивать (FAIL-SAFE: бот отвечает как раньше, не онемел)."""
    names = _park_bike_names(getter=getter)
    if not names:
        return None
    keys = [_bike_key(n) for n in names]
    allow = [disp for disp, key in KNOWN_MODELS if any(key in k for k in keys)]
    return allow or None   # пусто (ни одного совпадения) → тоже fail-safe, не ограничиваем на мусоре


# --- ДЕТЕРМИНИРОВАННЫЙ РЕЗОЛВ МОДЕЛИ (Лист1/_bike_key + алиасы для ВСЕХ моделей) ---------------
# Класс-0 (родитель #253): «XSR 155» детектился в ЛУПНЫЙ canon «XSR» (терялось «155»), а
# pricing._candidates по substring матчил ВСЕ XSR-юниты (155 и 900) — неоднозначно, брал первый
# доступный ⇒ подставлялась ЧУЖАЯ карточка парка (в живом провале — MT-03/≈5166฿, дырой в цене-нот).
# Резолвим детект в КОНКРЕТНУЮ модель парка по _bike_key (тот же нормализатор, что park_allowlist —
# снимает CC/СС). Точная фраза → точная модель; неоднозначная серия (в парке и XSR155, и XSR900) →
# НЕ угадываем и НЕ подставляем — уточняем у клиента.
_KEY_DISPLAY = {key: disp for disp, key in KNOWN_MODELS}   # 'xsr155' -> 'XSR 155'

# Алиас (нормализованный _bike_key детекта) → КОНКРЕТНЫЙ ключ KNOWN_MODELS.
_MODEL_ALIAS = {
    "nmax": "nmax155", "nmax155": "nmax155",
    "xmax": "xmax300", "xmax300": "xmax300",
    "forza": "forza300", "forza300": "forza300",
    "xadv": "xadv750", "xadv750": "xadv750",
    "pcx150": "pcx150", "pcx160": "pcx160",
    "adv150": "adv150", "adv160": "adv160", "adv350": "adv350",
    "xsr155": "xsr155", "xsr900": "xsr900",
    "cb300": "cb300r", "cb300r": "cb300r",
    "cb650": "cb650r", "cb650r": "cb650r",
    "cbr": "cbr650r", "cbr650": "cbr650r", "cbr650r": "cbr650r",
    "rebel": "rebel300", "rebel300": "rebel300",
    "mt": "mt03", "mt03": "mt03",
    "ninja": "ninja400", "ninja400": "ninja400",
    "vulcan": "vulcan650s", "vulcan650": "vulcan650s", "vulcan650s": "vulcan650s",
    "r7": "r7", "click": "click125", "click125": "click125",
}
# СЕРИЯ без уточнения объёма → возможные ключи; конкретную выбираем ПЕРЕСЕЧЕНИЕМ с реальным парком.
_MODEL_SERIES = {
    "xsr": ("xsr155", "xsr900"),
    "adv": ("adv150", "adv160", "adv350"),
    "pcx": ("pcx150", "pcx160"),
    "cb":  ("cb300r", "cb650r"),
}


def _fleet_model_keys(getter=None):
    """Ключи KNOWN_MODELS, реально присутствующие в парке (Лист1) — по _bike_key имён байков.
    Источник недоступен/пуст → None (fail-safe: не резолвим агрессивно, пусть работает как раньше)."""
    names = _park_bike_names(getter=getter)
    if not names:
        return None
    keys = [_bike_key(n) for n in names]
    return {key for _, key in KNOWN_MODELS if any(key in k for k in keys)}


def resolve_park_model(canon, getter=None):
    """Детект модели (canon из _detect_model, напр. «XSR», «XSR 155», «MT-03») → КОНКРЕТНАЯ модель
    парка по Лист1/_bike_key + алиасам. Возвращает (status, display, key):
      'ok'        — однозначная модель парка (display='XSR 155', key='xsr155');
      'ambiguous' — серия (XSR/ADV/…), а в парке НЕСКОЛЬКО вариантов → уточнить у клиента
                    (display=list отображаемых имён, key=None); чужую карточку НЕ подставляем;
      'unknown'   — не распознали / парк недоступен / модель не из серии-развилки → (None, None):
                    fail-safe, вызывающий оставляет исходный canon и идёт прежним путём.
    Инвариант: специфичная фраза клиента резолвится в РОВНО свою модель; голый серийный корень при
    коллизии в парке НЕ угадывается."""
    k = _bike_key(canon)
    if not k:
        return "unknown", None, None
    present = _fleet_model_keys(getter=getter)
    if not present:                                    # парк недоступен → не резолвим (как раньше)
        return "unknown", None, None
    key = _MODEL_ALIAS.get(k)
    if key:
        if key in present:
            return "ok", _KEY_DISPLAY.get(key, canon), key
        return "unknown", None, None                   # известная модель, но НЕ в парке → прежний путь
    if k in _MODEL_SERIES:
        matches = [key for key in _MODEL_SERIES[k] if key in present]
        if len(matches) == 1:
            return "ok", _KEY_DISPLAY.get(matches[0], canon), matches[0]
        if len(matches) >= 2:
            return "ambiguous", [_KEY_DISPLAY.get(x, x) for x in matches], None
        return "unknown", None, None                   # серия не представлена в парке
    return "unknown", None, None


# ------------------------------- playbook (книга правил) ---------------------
# Растущая книга правил (стиль/факты/запреты/выученные правки) — локальный файл. Подмешивается в
# промпт СТРОГО НИЖЕ кап-цены и CRITICAL_FACTS (playbook их НЕ отменяет). Пополняется из одобренных
# правок модератора. Bridge не трогаем — источник локальный.
PLAYBOOK_FILE = os.path.join(BASE_DIR, "manager-bot", "docs", "playbook.md")


def load_playbook():
    """Текст книги правил из PLAYBOOK_FILE. Нет файла/пусто/ошибка чтения → '' (FAIL-SAFE:
    генерация не ломается, блок в промпте просто не появляется)."""
    try:
        with open(PLAYBOOK_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


_LEARNED_HEADER = "## Выученные правила"


def _norm_rule(s):
    """Нормализация правила для дедупа: буквы/цифры/пробелы, нижний регистр, схлопнутые пробелы."""
    return " ".join(re.sub(r"[^0-9a-zа-яё ]+", " ", str(s or "").lower()).split())


def _rules_similar(a, b):
    """Почти-идентичны? (для дедупа): Jaccard слов ≥ 0.6 ИЛИ одно содержится в другом."""
    na, nb = _norm_rule(a), _norm_rule(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta, tb = set(na.split()), set(nb.split())
    return (len(ta & tb) / len(ta | tb)) >= 0.6 if (ta and tb) else False


def _playbook_learned_rules(text):
    """Существующие правила из секции «Выученные правила» (без даты-префикса) — для дедупа."""
    out, inside = [], False
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("## "):
            inside = s.lower().startswith(_LEARNED_HEADER.lower())
            continue
        if inside and s.startswith("-"):
            r = re.sub(r"^\(\d{4}-\d{2}-\d{2}\)\s*", "", s.lstrip("-").strip())
            if r:
                out.append(r)
    return out


def append_playbook_rule(rule, now=None):
    """Дописать ВЫУЧЕННОЕ правило в playbook.md (секция «Выученные правила», APPEND с датой, не
    перезапись). Дедуп почти-идентичных. → 'added' | 'duplicate' | 'error'.
    FAIL-SAFE: файл недоступен/ошибка записи → 'error' (вызывающий не блокирует отправку черновика)."""
    rule = " ".join(str(rule or "").split()).strip()
    if not rule:
        return "error"
    try:
        with open(PLAYBOOK_FILE, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return "error"
    if any(_rules_similar(rule, e) for e in _playbook_learned_rules(text)):
        return "duplicate"
    date = (now or datetime.date.today()).isoformat()
    bullet = f"- ({date}) {rule}"
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().lower().startswith(_LEARNED_HEADER.lower())), None)
    if start is None:                       # секции нет — создаём в конце
        new_text = text.rstrip() + "\n\n" + _LEARNED_HEADER + "\n" + bullet + "\n"
    else:
        end = next((j for j in range(start + 1, len(lines)) if lines[j].strip().startswith("## ")),
                   len(lines))
        while end - 1 > start and not lines[end - 1].strip():   # вставляем после последнего правила
            end -= 1
        lines.insert(end, bullet)
        new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    try:
        with open(PLAYBOOK_FILE, "w", encoding="utf-8") as f:
            f.write(new_text)
        return "added"
    except Exception:
        return "error"


# ------------------------------- рантайм-стоп --------------------------------

_disabled = False  # флип при флуде — авто-стоп до перезапуска/сброса


def is_enabled() -> bool:
    """SUGGEST активен: включён флагом и не остановлен флуд-гардом."""
    return SUGGEST_MODE and not _disabled


def disable(reason: str) -> None:
    global _disabled
    _disabled = True
    log.warning(f"SUGGEST АВТО-СТОП: {reason}")


def reset_disabled() -> None:
    """Только для тестов/ручного сброса."""
    global _disabled
    _disabled = False


# ------------------------------- утилиты -------------------------------------

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def detect_lang(text: str) -> str:
    """Язык по одному тексту (примитив): есть кириллица → ru, иначе en.
    ВНИМАНИЕ: не использовать для выбора языка ответа на диалог — латинское НАЗВАНИЕ модели
    («Adv 350») ложно даёт en. Для диалога — detect_lang_from_client (по ВСЕМ репликам клиента)."""
    for ch in text or "":
        if "а" <= ch.lower() <= "я" or ch.lower() == "ё":
            return "ru"
    return "en"


_LATIN_WORD_RE = re.compile(r"[a-z]{2,}")


def detect_lang_from_client(transcript: str) -> str:
    """Язык ответа = преобладающий язык ВСЕХ реплик КЛИЕНТА в диалоге (не одной последней).
    Правила:
      • хоть немного кириллицы у клиента где угодно → ru (стабильно, без прыжков RU↔EN);
      • латинские НАЗВАНИЯ моделей (NMAX/ADV/CBR/XMAX/Forza/Vulcan/Ninja/XADV/MT/…), цифры и
        даты за признак EN НЕ считаем — их вырезаем перед проверкой;
      • осмысленные латинские слова (2+ буквы, не только модель+число) → en;
      • содержательного текста нет (только модель+числа) → ru (RU-first магазин, дефолт).
    Инвариант: «Adv 350» в русском диалоге → остаёмся RU (латиница модели EN не триггерит)."""
    joined = " ".join(_client_messages(transcript))   # реплики клиента, уже lowercased
    if re.search(r"[а-яё]", joined):
        return "ru"
    stripped = joined
    for tok in _MODEL_TOKENS:                          # убрать латинские названия моделей
        stripped = stripped.replace(tok, " ")
    return "en" if _LATIN_WORD_RE.search(stripped) else "ru"


def parse_approval(reply_text: str):
    """Разобрать reply менеджера. Возвращает (action, payload):
      approve — '+'/'да'/'да N' (payload=None): отправить черновик как есть;
      reject  — '-'/'нет'      (payload=None): отклонить;
      edit    — любой другой текст (payload=текст): отправить правленую версию.
    """
    t = (reply_text or "").strip()
    low = t.lower()
    if low in ("+", "да", "ок", "ok", "yes", "+1") or low.startswith("да "):
        return "approve", None
    if low in ("-", "нет", "no", "отклонить", "reject"):
        return "reject", None
    if not t:
        return "reject", None
    return "edit", t


# ------------------------------- транскрипт ----------------------------------

async def _resolve_replies(client, entity, msgs):
    """§243/5: у каждого сообщения-reply подтянуть РЕПЛАЕННОЕ сообщение (цель) → attr
    m._reply_target. Цель ищем сперва в уже выбранном окне (по id), иначе дозапрашиваем по id
    (клиент отвечает reply'ем на сообщение из СТАРОЙ переписки — оно вне окна). Read-only,
    fail-safe: любой сбой дозапроса → цель None (черновик от этого не падает). msgs — как
    отдал iter_messages."""
    by_id = {getattr(m, "id", None): m for m in msgs if getattr(m, "id", None) is not None}
    need = {}  # reply_to_id -> [сообщения-reply, ссылающиеся на него]
    for m in msgs:
        rid = getattr(m, "reply_to_msg_id", None)
        if not rid:
            continue
        target = by_id.get(rid)
        if target is not None:
            m._reply_target = target
        else:
            need.setdefault(rid, []).append(m)
    if not need:
        return msgs
    try:                                   # дозапрос целей вне окна (в т.ч. из старой переписки)
        fetched = await client.get_messages(entity, ids=list(need.keys()))
    except Exception as e:
        log.info(f"SUGGEST: дозапрос reply-целей не удался ({type(e).__name__}) — без контекста reply")
        return msgs
    for f in (fetched or []):
        if f is None:
            continue
        for m in need.get(getattr(f, "id", None), []):
            m._reply_target = f
    return msgs


async def _fetch_messages(client, entity, limit: int = MAX_MESSAGES):
    """Выбрать последние сообщения диалога (newest-first, как отдаёт iter_messages) и подтянуть
    цели reply-сообщений (см. _resolve_replies)."""
    msgs = []
    async for m in client.iter_messages(entity, limit=limit):
        msgs.append(m)
    await _resolve_replies(client, entity, msgs)
    return msgs


def _reply_target_snippet(rt, me_id: int):
    """§243/5: краткое СОДЕРЖИМОЕ реплаенного сообщения для контекста черновика: автор +
    гео-ссылка/адрес/телефон/текст и/или пометка о фото (на этапе брони — обычно паспорт).
    Пусто → None (нечего добавлять)."""
    if rt is None:
        return None
    who = "менеджер" if (getattr(rt, "sender_id", None) == me_id) else "клиент"
    parts = []
    if getattr(rt, "photo", None) is not None:
        parts.append("фото (вероятно паспорт)")
    if getattr(rt, "geo", None) is not None:          # локация-пин в reply-цели → маркер вложения
        parts.append("[локация]")
    txt = (getattr(rt, "message", None) or "").strip()
    if txt:
        parts.append(txt.replace("\n", " ⏎ "))
    elif not parts and getattr(rt, "media", None) is not None:
        parts.append("вложение")
    if not parts:
        return None
    return f"{who}: " + " · ".join(parts)


def transcript_from(msgs, me_id: int) -> str:
    """Текст диалога '[менеджер]/[клиент]: ...' в хронологическом порядке (old→new).
    Ручные ответы менеджера видны (они отправлены с этого же аккаунта, sender_id==me).
    §243/5: если сообщение — reply, дописываем содержимое реплаенного (гео/фото-паспорт/
    телефон/текст), чтобы «Вот» реплаем на данные из старой брони попало в контекст черновика."""
    lines = []
    for m in reversed(msgs):
        who = "менеджер" if (getattr(m, "sender_id", None) == me_id) else "клиент"
        body = (getattr(m, "message", None) or "").strip()
        if not body:                                  # медиа без подписи: различаем фото/локацию/прочее,
            if getattr(m, "photo", None) is not None:  # чтобы трекер ловил ВЛОЖЕНИЕ, а не слово-упоминание
                body = "[фото]"
            elif getattr(m, "geo", None) is not None:
                body = "[локация]"
            else:
                body = "[медиа/без текста]"
        body = body.replace("\n", " ⏎ ")
        snippet = _reply_target_snippet(getattr(m, "_reply_target", None), me_id)
        if snippet:
            body = f"{body} ↩[в ответ на — {snippet}]"
        lines.append(f"[{who}]: {body}")
    return "\n".join(lines)


def first_contact_from(msgs, me_id: int, hours=None, now=None) -> bool:
    """Первый контакт: НЕТ наших (менеджера) сообщений за последние `hours` часов.
    msgs — newest-first. now — инъекция для тестов."""
    hours = FIRST_CONTACT_HOURS if hours is None else hours
    now_dt = now() if now else datetime.datetime.now(datetime.timezone.utc)
    cutoff = now_dt - datetime.timedelta(hours=hours)
    for m in msgs:  # newest-first
        d = getattr(m, "date", None)
        if d is not None and d < cutoff:
            break  # дальше только более старые — выходим из окна
        if getattr(m, "sender_id", None) == me_id and d is not None:
            return False  # мы писали в окне → это продолжение диалога
    return True


_GREETING_RE = re.compile(
    r"^(здравствуй\w*|привет\w*|добр(?:ый|ое|ой)\s+(?:день|утро|вечер|ночи)|"
    r"hello|hi|hey|good\s+(?:morning|afternoon|evening|day)|welcome|greetings)\b",
    re.IGNORECASE)


def greeting_already_sent(transcript: str) -> bool:
    """Уже уходило НАШЕ (менеджера) приветствие в этом диалоге? (автоприветствие ИЛИ прошлый
    наш ответ). Сканируем строки [менеджер]: — если хоть одна НАЧИНАЕТСЯ с приветствия →
    приветствие уже состоялось, генератор не должен здороваться повторно (один раз на диалог)."""
    for ln in (transcript or "").split("\n"):
        if not ln.startswith("[менеджер]:"):
            continue
        body = ln[len("[менеджер]:"):].strip().lstrip("!.,:;-—–()«\"' \t")
        if _GREETING_RE.match(body):
            return True
    return False


# Зачины-приветствия для среза в НАЧАЛЕ нашего ответа (родитель #311, гард повторного
# приветствия). Мирует _GREETING_RE (suggest.py:690, слово-привет RU/EN + \w*/\b) и добавляет
# business-автоприветствия «Спасибо, что написали/выбрали нас» (класс «е» ревизора,
# _REVIZOR_AUTOGREETING_RES → pc_orchestrator.py:3003 — держать сигнатуры синхронными).
_GREETING_PREFIX_RE = re.compile(
    r"(здравствуй\w*"
    r"|привет\w*|приветствую"
    r"|добр(?:ый|ое|ой)\s+(?:день|утро|вечер|ночи)"
    r"|доброго\s+(?:дня|утра|вечера)"
    r"|спасибо,?\s+что\s+(?:написали|выбрали)(?:\s+нас|\s+нам)?"
    r"|hello|hi|hey|good\s+(?:morning|afternoon|evening|day)|welcome|greetings)\b",
    re.IGNORECASE)


def _skip_seps(s: str, i: int = 0) -> int:
    """Индекс первого «значимого» символа с позиции i: пропускаем пробелы, пунктуацию и
    эмодзи (всё, что не буква и не цифра). isalnum() — юникод-осведомлён (кириллица=буква)."""
    n = len(s)
    while i < n and not s[i].isalnum():
        i += 1
    return i


def strip_greeting(text: str) -> str:
    """Срезает вступительное приветствие в НАЧАЛЕ нашего ответа, сохраняя остальной текст и
    поднимая его первую букву в заглавную. Чистая функция (без побочек): вход→выход, ничего
    не мутирует и не пишет. Ловит комбинации нескольких зачинов подряд с любой пунктуацией/
    эмодзи между ними. Если приветствия в начале нет — текст возвращается БЕЗ изменений
    (в т.ч. с ведущими кавычками/скобками — их не трогаем).

    Примеры из ТЗ (родитель #311):
      "Здравствуйте! Аренда 500฿/день"      -> "Аренда 500฿/день"
      "Привет, хочу арендовать байк"        -> "Хочу арендовать байк"
      "Добрый день, какие есть модели?"     -> "Какие есть модели?"
      "Спасибо, что написали! Уже смотрю"   -> "Уже смотрю"
      "Здравствуйте! 😊 Спасибо, что выбрали нас. NMAX свободен"
                                            -> "NMAX свободен"   (комбинация зачинов + эмодзи)
      "Hi there, bikes available"           -> "There, bikes available"
      "Хонда Клик 125 доступна"             -> "Хонда Клик 125 доступна"  (нет зачина — как есть)
      "500฿ в сутки"                        -> "500฿ в сутки"  (первая — цифра, регистр не трогаем)
    """
    if not text:
        return text
    s = text
    stripped_any = False
    while True:
        start = _skip_seps(s)                        # эмодзи/пунктуация/пробелы перед зачином
        m = _GREETING_PREFIX_RE.match(s, start)
        if not m:
            break
        s = s[m.end():]                              # срезаем зачин ВМЕСТЕ с сепараторами до него
        stripped_any = True
    if not stripped_any:
        return text                                  # приветствия нет — исходный текст без правок
    s = s[_skip_seps(s):]                             # хвостовая пунктуация/эмодзи после зачина
    if s and s[0].isalpha():
        s = s[0].upper() + s[1:]                      # первую заглавную остатка восстанавливаем
    return s


# Сигнатуры автоприветствия Telegram Business (обе поколения владельца) — держать СИНХРОННЫМИ с
# _REVIZOR_AUTOGREETING_RES (pc_orchestrator.py:3003). Автогритинг уходит с нашего аккаунта мимо
# бота/модерации, но в транскрипте реальной истории виден как строка [менеджер]:.
_AUTOGREETING_RES = (
    re.compile(r"спасибо\W+что\W+выбрали\W+нас", re.IGNORECASE),
    re.compile(r"уже\W+смотрю\W+ваше\W+сообщени", re.IGNORECASE),
)


def autogreeting_already_sent(transcript: str) -> bool:
    """В окне уже уходило автоприветствие Telegram Business («Спасибо, что выбрали нас…» /
    «Уже смотрю ваше сообщение…»)? Сканируем строки [менеджер]: (автогритинг с нашего аккаунта).
    _GREETING_RE его НЕ ловит (не слово-привет), поэтому greeting_already_sent тут молчит — это
    root cause класса «е»: бот здоровается ПОВЕРХ автоприветствия. Детект детерминированный
    (ё→е, пунктуация-агностично); сигнатуры держать синхронными с _REVIZOR_AUTOGREETING_RES
    (pc_orchestrator.py:3003). Пусто → False."""
    for ln in (transcript or "").split("\n"):
        if not ln.startswith("[менеджер]:"):
            continue
        body = ln[len("[менеджер]:"):].replace("ё", "е")
        if any(rx.search(body) for rx in _AUTOGREETING_RES):
            return True
    return False


# --- детект «котируемой» брони на ПЕРВОМ же сообщении (родитель #271, класс «ж») ---------------
# Клиент ПЕРВОЙ репликой даёт модель + старт + срок ⇒ надо КОТИРОВАТЬ, а не слать анкету/список
# вопросов. Детерминированный детект (без сети/LLM): вытаскиваем ровно три обязательных поля.
def _detect_catalog_model(text: str):
    """Модель из КАТАЛОГА KNOWN_MODELS в тексте, нечувствительно к регистру/пробелам
    («xsr 155», «XSR155», «xsr  155» → 'XSR 155'). Матчим по _bike_key (тот же нормализатор, что
    park_allowlist/resolve_park_model — снимает регистр, пробелы, CC/СС и прочий не-alnum). При
    нескольких совпадениях берём самый ДЛИННЫЙ ключ (специфичнее). Модели каталога нет → None.
    Голый серийный корень («xsr» без объёма) не матчит — в каталоге только конкретные модели."""
    key_text = _bike_key(text)
    if not key_text:
        return None
    best = None
    for disp, key in KNOWN_MODELS:
        if key in key_text and (best is None or len(key) > len(best[1])):
            best = (disp, key)
    return best[0] if best else None


def detect_first_message_booking(text: str, today=None):
    """Из ОДНОГО сообщения клиента вытащить котируемую бронь: модель (каталог), дату старта
    («с 15 июля») и срок («на 2 недели» → 14 дней; поддержка дней/недель/месяцев). Возвращает
    {'model': 'XSR 155', 'startDate': 'YYYY-MM-DD', 'days': 14} ИЛИ None, если ХОТЯ БЫ ОДНОГО из
    трёх полей нет. Детерминированно (без сети/LLM), переиспользует _anchor_date/_parse_term."""
    today = today or datetime.date.today()
    t = (text or "").lower().replace("–", "-").replace("—", "-").replace("ё", "е")
    model = _detect_catalog_model(text)
    anchor = _anchor_date(t, today)          # дата старта («с 15 июля», «завтра», «с 20»)
    term = _parse_term(t)                    # (days, monthly) из «на N дней/недель/месяцев»
    if not (model and anchor and term):
        return None
    return {"model": model, "startDate": anchor.isoformat(), "days": term[0]}


async def read_transcript(client, entity, me_id: int, limit: int = MAX_MESSAGES) -> str:
    """Совместимость: выбрать сообщения и собрать транскрипт."""
    return transcript_from(await _fetch_messages(client, entity, limit), me_id)


def is_approver(username) -> bool:
    """Есть ли право approve/edit/reject. Пустой whitelist = можно любому."""
    if not APPROVER_USERNAMES:
        return True
    return (username or "").lstrip("@").lower() in APPROVER_USERNAMES


def is_intake_approver(username) -> bool:
    """Вправе ли username УЧИТЬ бота (правка/урок/не так на карточке черновика). Строже
    approve: только INTAKE_APPROVERS. Список не сконфигурирован (пуст) → фолбэк на общий
    approver-whitelist (обучение не шире approve), но НЕ распахиваем шире approve."""
    if not INTAKE_APPROVERS:
        return is_approver(username)
    return (username or "").lstrip("@").lower() in INTAKE_APPROVERS


# ------------------------------- FAQ / LLM -----------------------------------

def _bridge_read_doc(name: str):
    """read_doc GET по имени дока; вернуть текст или None."""
    if not (BRIDGE_URL and BRIDGE_TOKEN):
        return None
    import urllib.request
    import urllib.parse
    full = BRIDGE_URL + "?" + urllib.parse.urlencode(
        {"action": "read_doc", "name": name, "token": BRIDGE_TOKEN}
    )
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict) and data.get("ok"):
        for k in ("text", "content", "fileContent", "body"):
            if isinstance(data.get(k), str) and data[k].strip():
                return data[k]
    return None


def load_faq(getter=None) -> str:
    """Живой FAQ. Порядок: read_doc name=faq → read_doc name=turbobaby_faq → локальный файл.
    getter(name)->text|None — инъекция для тестов (заменяет чтение из Brain)."""
    read = getter if getter is not None else _bridge_read_doc
    for name in FAQ_DOC_ORDER:
        try:
            t = read(name)
        except Exception as e:
            log.warning(f"SUGGEST: read_doc FAQ '{name}' упал: {e}")
            t = None
        if t and t.strip():
            return t
    try:
        with open(LOCAL_FAQ, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# --- лёгкий парсер модель+даты из диалога (поля по образцу collect_booking) ---
_MODEL_TOKENS = [
    "nmax", "pcx", "adv350", "adv 350", "adv150", "adv 150", "adv160", "adv 160", "adv",
    "xmax", "forza", "xadv", "xsr", "cb300", "cb 300", "cb650", "cb 650",
    "cbr650", "cbr 650", "cbr", "rebel", "mt-03", "mt03", "mt 03", "ninja", "vulcan", "r7", "click",
]
_MONTHS = ("январ", "феврал", "март", "апрел", "мая", "июн", "июл", "август", "сентябр",
           "октябр", "ноябр", "декабр",
           "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def _client_text(transcript: str) -> str:
    return "\n".join(l for l in (transcript or "").split("\n") if l.startswith("[клиент]:")).lower()


_MONTH_RE = r"(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
_MON_MAP = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "мая": 5,
            "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
            "ноябр": 11, "декабр": 12}


def _mon(stem):
    return _MON_MAP.get(stem)


def _safe_date(y, m, d):
    try:
        return datetime.date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def _mk(day, month, today, year=None):
    """Дата ближайшего будущего (если год не задан явно)."""
    if year:
        y = int(year)
        if y < 100:
            y += 2000
        return _safe_date(y, month, day)
    dt = _safe_date(today.year, month, day)
    if dt is None:
        return None
    if dt < today:                       # прошло в этом году → следующий год (ТОЛЬКО для старта/якоря)
        dt = _safe_date(today.year + 1, month, day)
    return dt


def _day_anchor(day, today):
    """Дата с днём `day` ближайшего будущего, когда МЕСЯЦ не назван («с 20»): текущий месяц, а если
    день уже прошёл (или невалиден для месяца, напр. 31 фев) — ближайший следующий месяц. Год-ролл
    НЕ делаем как лечение (макс. 12 месяцев вперёд). Не нашли валидную дату → None."""
    if not (1 <= day <= 31):
        return None
    for k in range(0, 13):
        mo = (today.month - 1 + k) % 12 + 1
        yr = today.year + (today.month - 1 + k) // 12
        d = _safe_date(yr, mo, day)
        if d is not None and (k > 0 or d >= today):
            return d
    return None


def _resolve_range(d_s, m_s, d_e, m_e, today, y_s=None, y_e=None):
    """Собрать (start, end) из двух точек. Год-ролл end+1г ТОЛЬКО при реальном переходе через
    год (месяц конца < месяца начала, напр. дек→янв). Перепутанный порядок в одном месяце → SWAP
    (НИКОГДА не превращаем в ~360 дней)."""
    start = _mk(d_s, m_s, today, y_s)
    if start is None:
        return None, None
    if y_e:                                          # у конца явный год
        end = _mk(d_e, m_e, today, y_e)
        if end and end < start:
            return end, start                        # явные годы, но реверс → swap
        return start, end
    end = _safe_date(start.year, m_e, d_e)
    if end is None:
        return start, None
    if end >= start:
        return start, end
    if int(m_e) < int(m_s):                          # дек→янв: легитимный переход через год
        return start, _safe_date(start.year + 1, m_e, d_e)
    return end, start                                # тот же/поздний месяц, но end<start → перепутан → swap


def _parse_term(t):
    """Срок из слов → (days:int, monthly:bool) или None."""
    if re.search(r"\bна\s+месяц\b", t) or re.search(r"\bмесяц\b", t):
        return (30, True)
    m = re.search(r"на\s+(\d{1,3})\s*(нед|недел)", t)
    if m:
        return (int(m.group(1)) * 7, False)
    if re.search(r"\bна\s+недел|\bнеделю\b", t):
        return (7, False)
    m = re.search(r"на\s+(\d{1,3})\s*(дн|дня|дней|день|сут)", t)
    if m:
        return (int(m.group(1)), False)
    m = re.search(r"на\s+(\d{1,3})\s*(day|days|week|weeks|month)", t)
    if m:
        n, u = int(m.group(1)), m.group(2)
        if u.startswith("week"):
            return (n * 7, False)
        if u == "month":
            return (n * 30, True)
        return (n, False)
    return None


def _anchor_date(t, today):
    """Дата начала для срочных фраз («завтра на 3 дня», «на неделю с 5 июля»)."""
    if "послезавтра" in t:
        return today + datetime.timedelta(days=2)
    if "завтра" in t:
        return today + datetime.timedelta(days=1)
    if "сегодня" in t:
        return today
    m = re.search(r"с\s+(\d{1,2})\s+" + _MONTH_RE, t)
    if m:
        return _mk(int(m.group(1)), _mon(m.group(2)), today)
    m = re.search(r"с\s+(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), today, m.group(3) or None)
    m = re.search(r"(\d{1,2})\s+" + _MONTH_RE, t)
    if m:
        return _mk(int(m.group(1)), _mon(m.group(2)), today)
    m = re.search(r"(\d{1,2})[./](\d{1,2})", t)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), today)
    # ГОЛЫЙ ДЕНЬ БЕЗ МЕСЯЦА: «с 20 [на 2 недели]» → старт = 20-е ближайшего будущего (тек. месяц, а
    # если день уже прошёл — следующий месяц; НЕ следующий год). Фиксирует старт из трекера, когда
    # клиент дал только число+длительность — иначе цена-нот проваливалась в «уточни даты» (дыра, в
    # которую LLM подставлял чужую карточку). Не хватаем «с 20 по/до …» (это диапазон, не старт+срок).
    m = re.search(r"\bс\s+(\d{1,2})\b(?!\s*(?:по|до|-|\d|[./]))", t)
    if m:
        return _day_anchor(int(m.group(1)), today)
    return None


def parse_date_range(text, today=None):
    """Диапазон дат из слов клиента → (iso_start, iso_end) или (None, None).
    Поддержка: «10.07-15.07», «с 5 по 10.07», «5–10 июля», «с 5 по 10 июля», «с 28 декабря по
    3 января» (год-ролл), «на неделю/месяц с 5 июля», «завтра на 3 дня», перепутанный порядок
    (swap). Год-ролл end+1г — ТОЛЬКО при реальном переходе через год, НЕ как лечение."""
    today = today or datetime.date.today()
    t = (text or "").lower().replace("–", "-").replace("—", "-").replace("ё", "е")
    dmw = [(int(m.group(1)), _mon(m.group(2))) for m in re.finditer(r"(\d{1,2})\s+" + _MONTH_RE, t)]
    dmy = re.findall(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", t)
    mwm = re.search(_MONTH_RE, t)
    mw = _mon(mwm.group(1)) if mwm else None
    rng = re.search(r"(?:с\s*)?(\d{1,2})\s*(?:-|по|до)\s*(\d{1,2})", t)
    term = _parse_term(t)

    s = e = None
    if len(dmw) >= 2:                                        # «28 декабря по 3 января»
        s, e = _resolve_range(dmw[0][0], dmw[0][1], dmw[1][0], dmw[1][1], today)
    elif len(dmy) >= 2:                                      # «10.07-15.07»
        s, e = _resolve_range(int(dmy[0][0]), int(dmy[0][1]), int(dmy[1][0]), int(dmy[1][1]),
                              today, dmy[0][2] or None, dmy[1][2] or None)
    elif rng and (mw or len(dmy) == 1):                     # «с 5 по 10 июля» / «с 5 по 10.07» / «5-10 июля»
        month = mw if mw else int(dmy[0][1])
        yhint = (dmy[0][2] or None) if len(dmy) == 1 else None
        s, e = _resolve_range(int(rng.group(1)), month, int(rng.group(2)), month, today, yhint, yhint)
    elif term:                                              # «на неделю с 5 июля» / «завтра на 3 дня»
        anchor = _anchor_date(t, today)
        if anchor:
            s = anchor
            e = anchor + datetime.timedelta(days=term[0])

    if not (s and e):
        return None, None
    if e < s:                                               # финальная страховка: swap, НЕ год-ролл
        s, e = e, s
    return s.isoformat(), e.isoformat()


def _client_messages(transcript: str):
    """Реплики КЛИЕНТА (не менеджера) в хронологическом порядке, lowercased."""
    return [ln[len("[клиент]:"):].strip().lower()
            for ln in (transcript or "").split("\n") if ln.startswith("[клиент]:")]


def _detect_model(text: str):
    for tok in _MODEL_TOKENS:
        if tok in text:
            return tok.upper().replace(" ", "")
    return None


def _detect_models(text: str):
    """ВСЕ модели, упомянутые в тексте (для запроса нескольких байков сразу). Порядок
    появления, без дублей; «ADV» отбрасываем, если есть более точная «ADV350» и т.п."""
    found = []
    for tok in _MODEL_TOKENS:
        if tok in text:
            canon = tok.upper().replace(" ", "")
            if canon not in found:
                found.append(canon)
    # снять префиксы, поглощённые более длинной моделью (ADV ⊂ ADV350, CBR ⊂ CBR650)
    return [c for c in found if not any(o != c and o.startswith(c) for o in found)]


# --- класс байка → минимальный срок аренды (правила цен v2, п.2) ---------------
# Скутеры сдаём от 5 дней (XSR155 тоже 5), мотоциклы — от 3.
SCOOTER_MIN_DAYS = 5
MOTO_MIN_DAYS = 3
_MOTO_PREFIXES = ("CB300", "CB650", "CBR", "REBEL", "MT", "NINJA", "VULCAN", "R7")
_SCOOTER_PREFIXES = ("PCX", "NMAX", "FORZA", "XMAX", "XADV", "ADV")


def bike_class(model):
    """(kind, min_days, label) для модели или None, если класс неизвестен.
    kind: 'scooter'|'moto'. label — как называть тип в сообщении клиенту."""
    m = re.sub(r"[^A-Z0-9]", "", (model or "").upper())
    if not m:
        return None
    if m.startswith("XSR"):
        if "900" in m:                       # XSR900 — большой мотоцикл (3)
            return ("moto", MOTO_MIN_DAYS, "мотоциклы")
        return ("scooter", SCOOTER_MIN_DAYS, "этот байк")   # XSR155 → минимум как у скутеров (5)
    for t in _MOTO_PREFIXES:
        if m.startswith(t):
            return ("moto", MOTO_MIN_DAYS, "мотоциклы")
    for t in _SCOOTER_PREFIXES:
        if m.startswith(t):
            return ("scooter", SCOOTER_MIN_DAYS, "скутеры")
    return None


# --- XMAX 300: ДВА ПОКОЛЕНИЯ = ДВА ПРОДУКТА --------------------------------------
# В парке (Лист1) XMAX двух поколений с РАЗНЫМИ тарифами: старое 2020-2022 (юниты БЕЗ токена «NEW»
# в имени, дешевле, депозит 5000) и новое 2023+ («…CC NEW …» в имени, дороже, депозит 7000). Раньше
# сетка/точечный quote схлопывали их МИНИМУМОМ по вариантам в одну строку — наценка нового поколения
# пропадала. Теперь это ДВА продукта отдельными строками, каждый с ЖИВЫМИ цифрами по своим юнитам;
# правило «минимум по вариантам» снято ТОЛЬКО для XMAX (для прочих моделей оно сохраняется).
_XMAX_KEY = _bike_key("XMAX 300")          # 'xmax300'


def _is_xmax_model(model) -> bool:
    """Модель — это XMAX (в любой форме: «XMAX 300», «XMAX300», голый canon «XMAX» из детекта).
    В парке XMAX только 300cc, поэтому ключа с префиксом 'xmax' достаточно."""
    return _bike_key(model).startswith("xmax")


def _xmax_is_new_gen(unit_name) -> bool:
    """Юнит XMAX нового поколения — в имени отдельный токен NEW (Лист1: «XMAX 300CC NEW …»)."""
    return bool(re.search(r"(?i)\bnew\b", str(unit_name or "")))


# Год выпуска поколения в СЫРОМ имени юнита (J-текст Bridge несёт имя вида «…XMAX 300 NEW 2023-…»)
# течёт клиенту в quote-хвост черновика. Поколение обязано нести МЕТКА (New Gen) — как в клиентском
# теле по правилу промпта (GEN_DEFAULT_RULE: «НЕ пиши года выпуска»), а год выпуска клиенту не
# показываем. Чистим год-в-имени (диапазон «2020-2022» / «2023+» / «2021 года» / одиночный 20xx),
# но НЕ цену: год-токен перед валютой (฿/бат/THB/baht) — это сумма, его не трогаем.
_GEN_YEAR_RE = re.compile(
    r"\b20\d\d(?:\s*[-–]\s*20\d\d|\s*\+|\s*года?)?(?!\s*(?:฿|бат|thb|baht))",
    re.I)


def _scrub_gen_year(text: str) -> str:
    """Убрать год выпуска поколения из клиентской строки цены (поколение несёт метка New Gen, не год).
    Цену не трогаем — год-токен перед валютой (฿/бат/THB) сохраняем. Осиротевший после выреза дефис
    («NEW 2023-» → «NEW -») и сдвоенные пробелы схлопываем."""
    s = _GEN_YEAR_RE.sub("", str(text or ""))
    s = re.sub(r"\s+[-–](?=\s|$)", "", s)          # висячий дефис от «2023-»
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


# Продукты XMAX: (метка клиенту, предикат по имени юнита). Порядок = порядок строк в сетке/quote.
_XMAX_PRODUCTS = (
    ("XMAX 300", lambda nm: not _xmax_is_new_gen(nm)),          # старое поколение (без «NEW»)
    ("XMAX 300 New Gen", _xmax_is_new_gen),                     # новое поколение 2023+
)


def _model_products_for_quote(model, want_old_gen=False):
    """Модель → продукты для ТОЧЕЧНОГО quote: [(метка, name_filter)]. ЕДИНЫЙ гэттер поколений (тот же
    в первичной генерации и в strategy-перегенерации — год поколения в клиентское тело не течёт нигде):
      • XMAX по умолчанию (want_old_gen=False) → ТОЛЬКО актуальное поколение (New Gen) ОДНОЙ строкой,
        без годов и без «старый/новый» вопроса (глобальное правило поколений, кейс @cryptopeppa 15.07);
      • XMAX + явный запрос клиента про прежнее поколение (want_old_gen=True, «а старый xmax есть?») →
        ОБА поколения отдельными строками (старое / New Gen), как каталог по явной просьбе;
      • прочие модели — один продукт с name_filter=None (поведение как раньше)."""
    if _is_xmax_model(model):
        prods = _XMAX_PRODUCTS if want_old_gen else _XMAX_PRODUCTS[1:]   # [1:] = только New Gen
        return [(label, pred) for label, pred in prods]
    return [(model, None)]


# Клиент ЯВНО просит ПРЕЖНЕЕ поколение (XMAX): «старый xmax», «старое поколение», «прежнее»,
# «не новый», «old (gen)», модель-годы 2018-2022. Только тогда показываем оба поколения; иначе
# по умолчанию — лишь актуальное (New Gen). Формы «стар…» перечислены явно, чтобы НЕ ловить «старт».
_OLD_GEN_RE = re.compile(
    r"стар(?:ый|ое|ую|ая|ого|ом|ым|ой|ше|еньк\w*)"        # старый/старое поколение, старше (не «старт»)
    r"|прежн\w+"                                            # прежнее поколение
    r"|\bold(?:er)?\b|old[-\s]?gen|previous\s+gen"         # old / older / old gen / previous gen
    r"|не\s+нов\w+|not\s+new"                               # не новый / not new
    r"|\b20(?:1[89]|2[0-2])\b",                             # модель-годы 2018-2022 (прежнее поколение)
    re.I)


def _asks_old_gen(newest: str, recent: str) -> bool:
    """Клиент ЯВНО просит прежнее поколение модели (см. _OLD_GEN_RE) → True. Ищем во ВСЁМ окне
    (newest+recent): запрос про «старый xmax» часто приходит РЕПЛИКОЙ ПОЗЖЕ дат первичной брони."""
    return bool(_OLD_GEN_RE.search(f"{newest or ''}\n{recent or ''}"))


def _asks_deposit_reduction_multi(newest: str, recent: str, models) -> bool:
    """Явный вопрос клиента про уменьшение депозита при нескольких байках (правила цен v2, п.4).
    newest — последняя реплика клиента, recent — склейка последних реплик, models — найденные модели."""
    dep = re.search(r"депозит|залог|deposit", newest or "")
    less = re.search(r"меньше|уменьш|сниз|скид|дешевл|пониз|lower|reduce|discount|less", newest or "")
    multi = re.search(r"нескольк|два\b|две\b|\bоба\b|\bобе\b|байка|байков|мотик|two|both|several",
                      recent or "")
    return bool(dep and less and (multi or len(models or []) >= 2))


# Клиент ЯВНО выбрал ПАСПОРТ как депозит (KB: депозит = деньги ЛИБО паспорт, не оба). Ловим ДВА
# сигнала В ОДНОЙ реплике — «паспорт» И депозит-контекст (залог/депозит/«вместо денег»): «фото
# паспорта пришлю» + отдельный вопрос «какой депозит?» НЕ должны давать ложный выбор паспорта.
_DP_PASSPORT_RE = re.compile(r"паспорт|passport", re.I)
_DP_DEPOSIT_CTX_RE = re.compile(
    r"залог|депозит|deposit|вместо\s+денег|вместо\s+деньг\w*|вместо\s+нал\w*"
    r"|instead\s+of\s+(?:the\s+)?(?:money|cash|deposit)", re.I)


def _deposit_passport_chosen(window) -> bool:
    """Клиент выбрал ПАСПОРТ вместо денежного депозита → True. window — реплики клиента (любой
    порядок); ОБА сигнала (паспорт + залог/депозит/«вместо денег») обязаны быть в ОДНОЙ реплике."""
    for msg in (window or []):
        s = msg or ""
        if _DP_PASSPORT_RE.search(s) and _DP_DEPOSIT_CTX_RE.search(s):
            return True
    return False


# Строка цены несёт сумму депозита из Bridge («депозит 3000 ฿», «депозит 3000 бат», «deposit 3000 THB»)
# — при выборе паспорта её меняем на «депозит: паспорт» без числа (тело ответа и quote-хвост не
# противоречат друг другу). Сама цена/итог не трогается — совпадение только на депозит-сумме.
_DEPOSIT_SUM_RE = re.compile(
    r"(?:депозит|залог|deposit)\w*\s*[:\-—]?\s*\d[\d\s]*\s*(?:฿|бат\w*|(?:thb|baht)\b)", re.I)


def _deposit_as_passport(phrase: str, lang: str = "ru") -> str:
    """Клиент выбрал ПАСПОРТ (KB: депозит = деньги ЛИБО паспорт, не оба): сумму депозита из Bridge в
    строке цены заменяем на «депозит: паспорт» (без числа). Нет суммы во фразе → дописываем явно."""
    repl = "deposit: passport" if lang == "en" else "депозит: паспорт"
    new, n = _DEPOSIT_SUM_RE.subn(repl, phrase or "")
    if n:
        return new
    base = (phrase or "").rstrip().rstrip(".")
    return (base + "; " + repl) if base else repl


def _explicit_monthly(text: str) -> bool:
    """monthly ТОЛЬКО по явному слову клиента, НЕ по длительности диапазона."""
    return bool(re.search(r"месяц|\bmonth\b|monthly", text or ""))


# «N юнитов ОДНОЙ модели»: клиент просит несколько единиц одной модели («пара XMAX», «два скутера»,
# «2 xmax»). Цена/депозит Bridge на такой запрос — ЗА КАЖДЫЙ юнит; общий итог НЕ выдумываем (числа
# только из quote, KB правила цен v2 п.4 «депозит при нескольких байках»). Слово-число («пара/два»)
# считаем ЮНИТАМИ лишь при байк/юнит-контексте (скутер/байк/шт) ИЛИ явной модели — «два дня/недели»
# (срок) исключаем хвост-проверкой. Цифра+существительное («2 скутера») самодостаточна.
_UNITS_DIGIT_RE = re.compile(
    r"\b([2-9]|1[0-9])\s*(?:шт\b|штук\w*|юнит\w*|единиц\w*|байк\w*|скутер\w*|мотоцикл\w*|мотик\w*"
    r"|bike|scooter|unit)", re.I)
_UNITS_CTX_RE = re.compile(
    r"скутер|байк|мотоцикл|мотик|штук|\bшт\b|юнит|единиц|\bbike|\bscooter|\bunit", re.I)
_UNITS_TIME_TAIL_RE = re.compile(
    r"\s*(?:дн\w*|недел\w*|нед\b|месяц\w*|сут\w*|day|week|month)", re.I)
_UNITS_WORD_MAP = (
    (re.compile(r"пар[ауеы]|\bpair\b|\bcouple\b", re.I), 2),
    (re.compile(r"\bдв[ае]\b|\bдвух\b|\bоб[ае]\b|\bboth\b|\btwo\b", re.I), 2),
    (re.compile(r"\bтр[её]х\b|\bтри\b|\bthree\b", re.I), 3),
    (re.compile(r"\bчетыр[её]х\b|\bчетыре\b|\bfour\b", re.I), 4),
)


def _units_count(newest: str, models):
    """Сколько ЮНИТОВ одной модели просит клиент («пара»/«два»/«2 скутера») → int ≥ 2 или None.
    Разные модели (len(models) ≥ 2) — это НЕ N юнитов одной модели, а разные модели (их считает
    путь буллетов) → None. Слово-число без байк/юнит-контекста и без модели («на два дня») НЕ в счёт."""
    t = newest or ""
    md = _UNITS_DIGIT_RE.search(t)
    if md:
        return int(md.group(1))
    if len(models or []) >= 2:
        return None
    has_ctx = bool(_UNITS_CTX_RE.search(t))
    single_model = bool(models) and len(models) == 1
    if not (has_ctx or single_model):
        return None
    for rx, n in _UNITS_WORD_MAP:
        m = rx.search(t)
        if m and not _UNITS_TIME_TAIL_RE.match(t[m.end():]):    # «два дня/недели» — срок, не юниты
            return n
    return None


def _units_count_note(n, lang="ru") -> str:
    """Довесок к блоку ЦЕНА: клиент просит N юнитов одной модели → цена/депозит Bridge выше = ЗА
    КАЖДЫЙ юнит; общий итог за N штук НЕ считаем и НЕ выдумываем (числа только из quote — п.4).
    Строка начинается с пробела (клеится к note, как DEPOSIT_MULTI_NOTE)."""
    if lang == "en":
        return (f" MULTIPLE UNITS: the client asks for {n} units of this model — the price AND "
                f"deposit above are PER UNIT (say «each»); do NOT compute or invent a grand total "
                f"for {n} units, the manager will confirm the total for the quantity.")
    return (f" НЕСКОЛЬКО ЮНИТОВ: клиент просит {n} ед. этой модели — цена И депозит выше указаны "
            f"ЗА КАЖДЫЙ юнит (так и подпиши: «за каждый»); общую сумму за {n} шт. сам НЕ считай и "
            f"НЕ выдумывай — итог по количеству уточнит менеджер.")


def _units_per_each_line(n, lang="ru") -> str:
    """Клиентская строка ХВОСТОМ точечного quote-блока N юнитов (compose_quote_draft): цена/депозит
    Bridge выше — ЗА КАЖДЫЙ юнит; итог за N шт. КОД не суммирует и не выдумывает (уточнит менеджер).
    В отличие от _units_count_note (инструкция LLM) это ДОСЛОВНЫЙ клиентский текст — попадает в тело
    как есть, если strategy-перегенерация теряет цену."""
    if lang == "en":
        return (f"Price and deposit above are PER UNIT (the client asks for {n}); the total for the "
                f"quantity will be confirmed by the manager.")
    return (f"Цена и депозит выше — ЗА КАЖДЫЙ юнит (клиент просит {n} шт.); итог по количеству "
            f"уточнит менеджер.")


# «сколько будет N%» / «N% это какая сумма» — клиент просит ПОСЧИТАТЬ процент от суммы расчёта
# (шаг 2/7 #253). Нужны ОБА: сам процент (число+% / «процентов» / percent) И вопрос-о-сумме
# («сколько/какая сумма/это сколько/how much»). Просто «скидка 10%» без вопроса-о-сумме сюда НЕ
# попадает (это не запрос посчитать). Процент считаем КОДОМ от total живого quote (не LLM).
_PCT_TOKEN_RE = re.compile(r"(\d{1,3})\s*(?:%|процент\w*|percent)", re.I)
_PCT_ASK_RE = re.compile(
    r"скольк|как(?:ая|ую|ой)\s+сумм|это\s+скольк|\bсумм\w*|\bэто\b|how\s+much|what.?s\b|составит|получ",
    re.I)


def _asks_percent_amount(newest: str, recent: str):
    """Клиент спрашивает «сколько будет N%» / «N% это какая сумма» → N (int 1..100) или None.
    Ищем в последней реплике, затем в окне; процент И вопрос-о-сумме обязаны быть вместе."""
    for src in (newest or "", recent or ""):
        mt = _PCT_TOKEN_RE.search(src)
        if mt and _PCT_ASK_RE.search(src):
            n = int(mt.group(1))
            if 1 <= n <= 100:
                return n
    return None


def _has_date_signal(text: str) -> bool:
    if re.search(r"\d{1,2}[.\-/]\d{1,2}", text or ""):
        return True
    if _parse_term(text):
        return True
    if re.search(r"с\s+\d{1,2}\s+(?:по|до)\s+\d{1,2}", text or ""):
        return True
    return any(mo in (text or "") for mo in _MONTHS)


def _has_start_signal(text: str) -> bool:
    """Конкретный СТАРТ аренды (в отличие от одной лишь ДЛИТЕЛЬНОСТИ): число+месяц («15 июля»),
    dd.mm («15.07», «с 15.07»), «с 15 …», «завтра/послезавтра/сегодня» (в т.ч. «с завтрашнего»).
    Одна длительность («на 10 дней», «на неделю») старта НЕ задаёт — сюда НЕ входит."""
    t = (text or "").lower()
    if re.search(r"послезавтра|завтра|сегодня", t):
        return True
    if re.search(r"\d{1,2}[.\-/]\d{1,2}", t):                 # 15.07 / 15/07
        return True
    if re.search(r"\d{1,2}\s+" + _MONTH_RE, t):               # 15 июля
        return True
    return False


# --- намерение «прайс по всему парку / все модели / прайс-лист» (сценарий price_sheet) --------
# Позитив: «цены на все модели», «прайс», «прайс-лист», «весь парк», «по всем байкам сколько».
# Негатив: «сколько стоит NMAX» (одна модель), «всё включено?», «все новые байки?» (нет цены/парка).
_PS_ALL_SCOPE = re.compile(
    r"вс[еёх]\w*\s+(?:модел|байк|скутер|мотоцикл|вариант)"
    r"|весь\s+парк|всего\s+парка|по\s+всему\s+парку|на\s+вс[её]\s+модел"
    r"|all\s+(?:the\s+)?(?:models|bikes|scooters|motos)|whole\s+fleet|entire\s+fleet"
    r"|full\s+(?:list|range|fleet)|every\s+(?:model|bike)", re.I)
_PS_PRICE_LIST = re.compile(
    r"прайс[\s\-]?лист|прайс[- ]?лист|\bпрайс\b|price\s*-?\s*list|pricelist"
    r"|список\s+(?:модел|байк|цен|тариф)|list\s+of\s+(?:models|bikes|prices)", re.I)
_PS_PRICE_WORD = re.compile(
    r"цен[аыуэ]|цены|стоимост|стоит|сколько|тариф|расцен|price|cost|how\s+much|\brate", re.I)

# --- смысловые признаки (классификация, а НЕ бесконечный список ключевиков) --------------------
# Клиент реально просит либо ПЕРЕЧЕНЬ моделей, либо ЦЕНЫ-В-ЦЕЛОМ (не по одной названной модели).
# Признак «перечень» = вопрос/предложение-цель + собирательное «модели/байки/марки».
_PS_ENUM_CUE = re.compile(
    r"как(?:ие|ой|их|ого)\b|что\s+(?:за|есть|у\s+вас|вы|предлаг)"
    r"|какой\s+выбор|ассортимент|перечень|каталог|модельн\w*\s+ряд|линейк"
    r"|предлаг\w*|сдаёте|сдаете|в\s+нали\w*"
    r"|\bwhat\b|\bwhich\b|list\s+of\b|range\s+of\b|(?:do\s+you|you)\s+(?:have|offer|rent)"
    r"|available|selection|catalog|line\s*-?up", re.I)
_PS_FLEET_NOUN = re.compile(
    r"марк\w*|модел\w*|байк\w*|скутер\w*|мотоцикл\w*|мопед\w*|мотик\w*|транспорт\w*|вариант\w*"
    r"|bikes?|scooters?|models?|brands?|mopeds?|motos?|motorcycles?|options?", re.I)
# Признак «цены-в-целом» = ценовое слово ВМЕСТЕ с арендой / сроком / вопросительным «сколько/какие».
_PS_RENTAL = re.compile(r"аренд\w*|прокат\w*|снять|снима\w*|rent\w*|hire|rental", re.I)
_PS_DURATION = re.compile(
    r"\bдень\b|\bдня\b|\bдней\b|сут(?:к|очн)\w*|недел\w*|месяц\w*|мес\b"
    r"|\bday\b|\bweek\b|\bmonth\b|daily|weekly|monthly", re.I)
_PS_ASK_CUE = re.compile(
    r"\bсколько\b|как(?:ие|ая|ов[аы])\b|\bпочём\b|\bпочем\b|\bчто\s+по\b"
    r"|\bhow\s+much\b|\bwhat\b|\bwhich\b", re.I)


def _asks_price_sheet(newest: str, recent: str) -> bool:
    """Детект СМЫСЛА «дай перечень моделей и/или цены по парку». Классификация, не список слов
    (ДЕТЕРМИНИРОВАННО, без LLM — покрывается юнитами напрямую):
      • явный «прайс»/«price list» ИЛИ «все модели/весь парк»+цена → True (как раньше);
      • иначе — вопрос про ПЕРЕЧЕНЬ моделей/каталог (какие модели/байки предлагаете) ИЛИ про
        ЦЕНЫ-В-ЦЕЛОМ (ценовое слово + аренда/срок/«сколько/какие») → True;
      • названа КОНКРЕТНАЯ модель (NMAX / ADV350 / …) И это НЕ каталог-вопрос → False:
        точечный/мульти-quote. КОРЕНЬ живого провала 22:27: модель-гард раньше сканировал ВСЁ
        окно (newest+recent) и БЕЗУСЛОВНО возвращал False — стоило клиенту раньше в окне упомянуть
        байк («интересует nmax»), как текущий каталог-вопрос «какие модели и цены?» терял сетку и
        уходил в гейт дат. Теперь явный каталог-запрос (enum) сетку НЕ отменяет, даже если рядом
        (в этой или прошлой реплике окна) названа модель — смещение к показу прайса.
    Негативы: одна модель без каталог-вопроса, приветствие, вопрос ТОЛЬКО про депозит,
    «всё включено в цену?»."""
    t = f"{newest or ''}\n{recent or ''}"
    if _PS_PRICE_LIST.search(t):
        return True
    if _PS_ALL_SCOPE.search(t) and _PS_PRICE_WORD.search(t):
        return True
    # Собирательный интерес: КАТАЛОГ/перечень моделей (enum) ИЛИ цены-в-целом (price_q).
    enum = bool(_PS_ENUM_CUE.search(t)) and bool(_PS_FLEET_NOUN.search(t))
    price_q = bool(_PS_PRICE_WORD.search(t)) and bool(
        _PS_RENTAL.search(t) or _PS_DURATION.search(t) or _PS_ASK_CUE.search(t))
    if not (enum or price_q):
        return False
    # Названа конкретная модель (в текущей ИЛИ прежних репликах окна) И НЕ каталог-вопрос → это
    # точечный/мульти-quote, а не сетка. Явный каталог-вопрос (enum) veto НЕ включает.
    if _detect_models(t.lower()) and not enum:
        return False
    return True


# --- подвыборка прайса: класс (scooter/moto) + рабочий объём cc, ДЕТЕРМИНИРОВАННО из СЛОВ клиента --
# Клиент сужает сетку: «скутеры 200+», «мотоциклы до 400». Разбор — строкой (не LLM): границы
# отдаём в filter_sheet_rows, отбор моделей делает КОД по данным сетки. Потому XADV 750 в «скутерах
# 200+» не теряется (у LLM-отбора терялся — живой черновик 12.07 15:46).
_SF_SCOOTER_RE = re.compile(r"скутер\w*|мопед\w*|scooter\w*|moped\w*", re.I)
_SF_MOTO_RE = re.compile(r"мотоцикл\w*|мотоцик\w*|мотик\w*|motorcycl\w*|\bmoto\b", re.I)
# нижняя граница: «от 200», «(не «не»)больше/более 200», «свыше 200», «200+», «200 и больше/выше»,
# «from/over/above 200». «не больше 200» — это ВЕРХНЯЯ граница, поэтому «больше/более» с «не» перед
# ними в min НЕ ловим (lookbehind).
_SF_MIN_RE = re.compile(
    r"(?:(?:от|свыше|начиная\s+(?:от|с)|from|over|above|starting)|(?<!не )(?:больше|более))\s*(\d{2,4})"
    r"|(\d{2,4})\s*(?:куб\w*\s*)?(?:\+|и\s*(?:больше|выше|более|старше)|and\s+(?:up|more|above|over))",
    re.I)
# верхняя граница: «до 400», «меньше/менее 400», «не больше/не более 400», «400 и меньше»,
# «under/below/up to/max 400».
_SF_MAX_RE = re.compile(
    r"(?:до|меньше|менее|не\s+(?:больше|более)|под|under|below|up\s*to|max(?:imum)?|less\s+than)\s*(\d{2,4})"
    r"|(\d{2,4})\s*(?:куб\w*\s*)?(?:и\s*)?(?:меньше|менее|ниже|or\s+less|and\s+(?:below|under))",
    re.I)
# Меньше 50 — это не рабочий объём (дата «до 15», срок «на 7 дней»): такую «границу» игнорируем.
_SF_CC_MIN_PLAUSIBLE = 50


def _parse_sheet_filter(newest: str, recent: str):
    """Подвыборка сетки из СЛОВ клиента: класс (scooter/moto из скутер/мотоцикл-слов) и границы cc.
    → dict {kind, cc_min, cc_max} или None (сужения нет). Оба класс-слова сразу (скутеры И мотоциклы)
    → kind=None (весь парк). Отбор моделей по границам делает КОД (filter_sheet_rows), НЕ LLM."""
    t = f"{newest or ''}\n{recent or ''}"
    sc, mo = bool(_SF_SCOOTER_RE.search(t)), bool(_SF_MOTO_RE.search(t))
    kind = "scooter" if (sc and not mo) else ("moto" if (mo and not sc) else None)

    def _bound(rx):
        for m in rx.finditer(t):
            v = m.group(1) or m.group(2)
            if v and int(v) >= _SF_CC_MIN_PLAUSIBLE:
                return int(v)
        return None

    cc_min, cc_max = _bound(_SF_MIN_RE), _bound(_SF_MAX_RE)
    if kind is None and cc_min is None and cc_max is None:
        return None
    return {"kind": kind, "cc_min": cc_min, "cc_max": cc_max}


# Несдаваемые модели: физически в парке (Лист1), но правило KB/CRITICAL_FACTS «НЕ сдаём» —
# в прайс по парку НЕ включаем (Honda Click 125). Ключи нормализованы как _bike_key.
_NON_RENTABLE_KEYS = {_bike_key("CLICK 125")}   # {'click125'}


BOOKING_WINDOW = 3  # сколько последних реплик клиента смотрим, чтобы дособрать ОДНУ бронь


def extract_booking_hints(transcript: str, today=None) -> dict:
    """Модель + даты ТОЛЬКО из ПОСЛЕДНЕЙ релевантной брони клиента (не из всего диалога).
    База — последняя реплика клиента; до 2 предыдущих его реплик смотрим лишь чтобы дособрать
    НЕДОСТАЮЩЕЕ той же брони. Разные модели / разные даты = разные брони: приоритет у последней,
    старую отбрасываем (не смешиваем). Возвращает {model, date_start, date_end, iso_start,
    iso_end, term_days, hint_days, monthly, has_dates}."""
    window = _client_messages(transcript)[-BOOKING_WINDOW:][::-1]  # новейшая первой
    newest = window[0] if window else ""
    recent = " ".join(window)

    model = iso_start = iso_end = term_days = None
    monthly = False

    for idx, msg in enumerate(window):
        m_model = _detect_model(msg)
        s, e = parse_date_range(msg, today)
        t = _parse_term(msg)
        if idx == 0:
            model = m_model
            if s and e:
                iso_start, iso_end = s, e
            if t:
                term_days = t[0]
            monthly = _explicit_monthly(msg)
            if model and (iso_start or term_days):
                break  # последняя реплика самодостаточна — назад не идём
            continue
        # предыдущая реплика окна: СТОП при признаках ДРУГОЙ брони (другая модель)
        if m_model and model and m_model != model:
            break
        # дособираем ТОЛЬКО недостающее (даты/срок), если у нас их ещё нет
        if iso_start is None and term_days is None:
            if s and e:
                iso_start, iso_end = s, e
                monthly = monthly or _explicit_monthly(msg)
            elif t:
                term_days = t[0]
                monthly = monthly or _explicit_monthly(msg)
        if model is None and m_model:
            model = m_model
        if model and (iso_start or term_days):
            break

    hint_days = None
    if iso_start and iso_end:
        try:
            hint_days = (datetime.date.fromisoformat(iso_end)
                         - datetime.date.fromisoformat(iso_start)).days
        except Exception:
            hint_days = None
    if hint_days is None:
        hint_days = term_days
    has_dates = bool(iso_start or term_days) or _has_date_signal(newest)
    has_start = bool(iso_start) or _has_start_signal(newest)   # конкретный старт, НЕ просто длительность

    # Несколько моделей в ОДНОМ запросе (правила цен v2, п.3) — берём из последней реплики;
    # одна/ноль → падаем на единственную разрешённую модель окна.
    newest_models = _detect_models(newest)
    if len(newest_models) >= 2:
        models = newest_models
    elif model:
        models = [model]
    else:
        models = newest_models
    deposit_multi_q = _asks_deposit_reduction_multi(newest, recent, models)
    price_sheet_q = _asks_price_sheet(newest, recent)
    sheet_filter = _parse_sheet_filter(newest, recent)
    percent_q = _asks_percent_amount(newest, recent)   # «сколько будет N%» → процент от суммы расчёта
    units_count = _units_count(newest, models)         # «пара/два юнита одной модели» → цена «за каждый»
    old_gen_q = _asks_old_gen(newest, recent)          # «а старый xmax есть?» → прежнее поколение по запросу
    deposit_passport_q = _deposit_passport_chosen(window)   # «паспорт в залог» → депозит без суммы

    # Гео для ДОСТАВКИ (шаг 5/7 #12): первая maps-ссылка в репликах клиента (новейшая первой). Ловим
    # ТОЛЬКО саму ссылку (упоминание «вилла/локация» без URL — не гео); питает резолвер доставки в
    # build_pricing_note. Локация «липкая» — берём по всему диалогу, не только по booking-окну.
    # Сканируем СЫРЫЕ строки клиента (не _client_messages: тот lowercase'ит — токен короткой goo.gl-
    # ссылки регистрозависим, разворот редиректом сломался бы).
    maps_link = None
    for ln in reversed((transcript or "").split("\n")):
        if ln.startswith("[клиент]:"):
            ml = delivery.extract_maps_link(ln[len("[клиент]:"):])
            if ml:
                maps_link = ml
                break

    return {"model": model, "models": models, "date_start": iso_start, "date_end": iso_end,
            "iso_start": iso_start, "iso_end": iso_end, "term_days": term_days,
            "hint_days": hint_days, "monthly": monthly, "has_dates": has_dates,
            "has_start": has_start,
            "deposit_multi_q": deposit_multi_q, "price_sheet_q": price_sheet_q,
            "sheet_filter": sheet_filter, "percent_q": percent_q, "units_count": units_count,
            "old_gen_q": old_gen_q, "deposit_passport_q": deposit_passport_q,
            "maps_link": maps_link}


# ------------------- §243/6: трекер собранного по диалогу + reply-вложениям -------------------
# Что клиент УЖЕ прислал (по окну диалога и подтянутым reply-вложениям из transcript_from): модель,
# срок/даты, гео, фото паспорта, телефон, оплата. Собранное НЕ переспрашиваем; в промпт кладём
# «уже получено — подтверди и не проси повторно», в черновик — пометку модератору «собрано: …».
# Скан по КЛИЕНТСКИМ строкам (reply-содержимое встроено в них через transcript_from).

# ★ ПРАВИЛО (шаг 3/7 #253): сущность ✅ ТОЛЬКО по ФАКТУ вложения/данных в сообщении клиента, а НЕ
# по слову-упоминанию. Гео — реальная ссылка/координаты/локация-пин; паспорт — реально пришедшее
# ФОТО (медиа-сообщение «[фото]» или reply на фото → маркер «вероятно паспорт»); телефон — реально
# присланный НОМЕР (цифры); оплата — ПОДТВЕРЖДЁННАЯ (прошедшее «оплатил/перевёл/внёс», «вот чек»,
# «скрин оплаты»), а НЕ вопрос «криптой можно?»/«оплатить криптой?» и НЕ назначение предоплаты.
# Слова «паспорт»/«вилла»/«оплата»/«предоплата»/«телефон» сами по себе (без вложения/цифр) — ВСЕГДА ❌.

# Гео: ссылка на карты / geo-координаты / локация-пин (маркер «[локация]» из вложения).
# Слово «вилла/апартаменты/локация» БЕЗ ссылки/пина — НЕ гео (упоминание ≠ данные).
_COLL_GEO = re.compile(
    r"maps\.app\.goo\.gl|goo\.gl/maps|google\.[a-z.]+/maps|maps\.google|geo:\s*-?\d"
    r"|@-?\d{1,2}\.\d{3,},-?\d{1,3}\.\d{3,}"
    r"|\[локаци\w*\]", re.I)
# Паспорт: ТОЛЬКО реально пришедшее фото — прямое медиа «[фото]» ИЛИ reply-маркер «вероятно паспорт».
# Слово «паспорт/passport/id» БЕЗ фото — НЕ считаем (упоминание ≠ вложение).
_COLL_PASSPORT = re.compile(r"вероятно\s+паспорт|\[фото\]|passport\s+photo", re.I)
# Телефон: реально присланный номер — международный (+..) ИЛИ длинный прогон цифр ИЛИ ключевик+цифры.
# Голое слово «телефон/номер» без цифр — НЕ считаем.
_COLL_PHONE = re.compile(r"\+\d[\d\s\-()]{7,}\d|\b\d{9,}\b"
                         r"|(?:тел|номер|phone|whats\s*app|whatsapp|вайбер|viber)\D{0,12}\d[\d\s\-()]{5,}\d",
                         re.I)
# Оплата: ТОЛЬКО подтверждённая — прошедшее действие оплаты/перевода/взноса ИЛИ чек/скрин оплаты.
# Вопрос «криптой можно?»/«оплатить криптой?», назначение предоплаты и голые «оплата/предоплата/
# перевод/депозит» — НЕ оплата (это упоминание/намерение/вопрос, а не факт совершённой оплаты).
_COLL_PAYMENT = re.compile(
    r"оплатил\w*|оплачен\w*|заплатил\w*|заплачен\w*|перев[её]л\w*"
    r"|(?:внёс|внес|внесла|внесли)\b"
    r"|вот\s+чек|чек\s+(?:об\s+)?оплат|скрин\w*\s+(?:оплат\w*|перевод\w*)"
    r"|\bpaid\b|already\s+paid|payment\s+(?:made|done|sent|completed)"
    r"|\btransferred\b|deposit\s+paid", re.I)


def collected_facts(transcript: str, hints: dict = None, today=None) -> dict:
    """§243/6: что клиент УЖЕ прислал по окну диалога + reply-вложениям (см. transcript_from).
    Ключи-булевы: model, term, dates, geo, passport, phone, payment. model/term/dates — из
    extract_booking_hints (переиспользуем ту же логику последней брони), остальное — детерминированный
    скан клиентских строк (reply-содержимое встроено в них). term = ДЛИТЕЛЬНОСТЬ известна («на 10 дней»,
    диапазон); dates = конкретный СТАРТ известен (число+месяц, «с завтрашнего», dd.mm). Слова длительности
    старта НЕ дают.
    ★ ШАГ 3/7 #253: geo/passport/phone/payment помечаем ✅ ТОЛЬКО по ФАКТУ вложения/данных в
    сообщении клиента (ссылка/пин, фото, номер, подтверждённая оплата/чек), а НЕ по слову-упоминанию —
    голые «паспорт/вилла/оплата/предоплата/телефон» и вопрос «криптой можно?» дают ❌ (см. _COLL_*).
    Ничего не найдено → все False (fail-safe: просто нет пометки)."""
    h = hints if hints is not None else extract_booking_hints(transcript, today=today)
    ctext = _client_text(transcript)   # только [клиент]: строки, lower, с встроенным reply-содержимым
    return {
        "model": bool(h.get("model")),
        "term":  bool(h.get("term_days") or (h.get("iso_start") and h.get("iso_end"))),
        "dates": bool(h.get("iso_start") or h.get("has_start")),
        "geo": bool(_COLL_GEO.search(ctext)),
        "passport": bool(_COLL_PASSPORT.search(ctext)),
        "phone": bool(_COLL_PHONE.search(ctext)),
        "payment": bool(_COLL_PAYMENT.search(ctext)),
    }


# Порядок и подписи собранного: (ключ, метка-для-промпта-RU/EN, короткая-метка-для-пометки-RU/EN).
_COLL_LABELS = [
    ("model",    ("модель",         "model"),    ("модель",  "model")),
    ("term",     ("срок",           "term"),     ("срок",    "term")),
    ("dates",    ("даты",           "dates"),    ("даты",    "dates")),
    ("geo",      ("локация",        "location"), ("гео",     "geo")),
    ("passport", ("фото паспорта",  "passport photo"), ("паспорт", "passport")),
    ("phone",    ("телефон",        "phone"),    ("тел",     "phone")),
    ("payment",  ("оплата",         "payment"),  ("оплата",  "payment")),
]


def collected_prompt_note(facts: dict, lang: str = "ru") -> str:
    """Блок в system-промпт: перечень уже полученного + запрет переспрашивать. Пусто → ''."""
    if not facts:
        return ""
    en = (lang == "en")
    got = [lbl[1 if en else 0] for key, lbl, _ in _COLL_LABELS if facts.get(key)]
    if not got:
        return ""
    if en:
        return ("\n\n★ ALREADY RECEIVED FROM THE CLIENT (in the dialog/attachments — do NOT ask "
                "again): " + ", ".join(got) + ". Briefly confirm you got it (e.g. «got your "
                "location and details») and move to the next step — never re-request what the "
                "client has already sent.")
    return ("\n\n★ УЖЕ ПОЛУЧЕНО ОТ КЛИЕНТА (есть в диалоге/вложениях — НЕ переспрашивай): "
            + ", ".join(got) + ". Коротко подтверди получение (напр. «локацию и данные получил») "
            "и переходи к следующему шагу — НЕ проси повторно то, что клиент уже прислал.")


def collected_manager_note(facts: dict, lang: str = "ru") -> str:
    """СЛУЖЕБНАЯ пометка модератору о собранном — в квадратных скобках, как «[уточнить: …]»
    (шаг 4/7 #253): «[собрано: гео ✅ паспорт ✅ тел ✅]». Скобки → это служебный канал карточки,
    а не тело ответа; client_facing_text её срезает (клиент «собрано:» не видит). Ничего не
    собрано → '' (пометки нет)."""
    if not facts:
        return ""
    en = (lang == "en")
    parts = [lbl[1 if en else 0] + " ✅" for key, _, lbl in _COLL_LABELS if facts.get(key)]
    if not parts:
        return ""
    head = "[collected: " if en else "[собрано: "
    return head + " ".join(parts) + "]"


# Инструкция про депозит при нескольких байках (правила цен v2, п.4).
DEPOSIT_MULTI_NOTE = ("ДЕПОЗИТ: клиент спрашивает про уменьшение депозита при нескольких байках "
                      "— сам скидку/снижение депозита НЕ предлагай и НЕ обещай; ответь ровно: "
                      "«уточню у менеджера».")


def _iso_plus(iso_date, n_days):
    """iso-дата + n_days дней → iso-строка или None."""
    try:
        return (datetime.date.fromisoformat(iso_date) + datetime.timedelta(days=int(n_days))).isoformat()
    except Exception:
        return None


def _safe_quote_for_model(model, ds, de, getter=None, name_filter=None):
    """pricing.quote_for_model без падений → всегда dict {status, quote}. name_filter сужает юниты
    (напр. поколение XMAX); getter инъектируется в тестах (боевой путь — None, живой Bridge)."""
    try:
        res = pricing.quote_for_model(model, ds, de, _get=getter, name_filter=name_filter)
    except Exception:
        res = None
    if not isinstance(res, dict):
        return {"status": "error", "quote": None}
    return res


def _client_price(q: dict) -> str:
    """Фраза ЦЕНЫ клиенту из quote. Правила цен v2, п.1 и п.5:
    кап (низкий сезон, total>cap_price) → «аренда от <cap> ฿/мес …» вместо J-цены (+депозит/наличие);
    иначе — поле text из quote ДОСЛОВНО (цена J); иначе — сборка из day_price/total/deposit."""
    cap_active = q.get("cap_active")
    cap_price = q.get("cap_price")
    total = q.get("total")
    if cap_active and cap_price is not None and total is not None and total > cap_price:
        parts = [f"аренда от {cap_price} ฿/мес — предложение низкого сезона"]
        if q.get("deposit") is not None:
            parts.append(f"депозит {q['deposit']} ฿")
        if q.get("available"):
            parts.append("свободен на эти даты")
        return "; ".join(parts)
    if isinstance(q.get("text"), str) and q["text"].strip():
        phrase = q["text"].strip()             # J-цена дословно (п.5)
        # шаг 2/7 #253: депозит модели ОБЯЗАН быть в котировке (клиент назвал модель+срок → его
        # депозит выводим из quote). J-текст Календаря депозит не всегда несёт — дописываем из
        # поля deposit, если его числа ещё нет во фразе (иначе не дублируем).
        dep = q.get("deposit")
        if dep is not None and str(dep) not in phrase:
            phrase += f"; депозит {dep} ฿"
        return phrase
    parts = []
    if q.get("day_price") is not None:
        parts.append(f"{q['day_price']} ฿/день")
    if q.get("total") is not None:
        parts.append(f"итого {q['total']} ฿")
    if q.get("deposit") is not None:
        parts.append(f"депозит {q['deposit']} ฿")
    if q.get("available"):
        parts.append("свободен на эти даты")
    return "; ".join(parts)


def _resolve_model_price(model, ds, de, hint_days, monthly, getter=None, name_filter=None):
    """Цена для ОДНОЙ модели по датам ds..de. Возвращает (kind, phrase, quote):
      ok    — цену использовать дословно (кап/J-текст/сборка внутри _client_price);
      min   — срок короче минимального: «<тип> сдаём от N дней» + цена на минимум;
      sanity/none/error — фолбэк без числа.
    quote — dict котировки, из которой собрана фраза (или None, если числа нет): нужен вызывающему
    для деривативов (процент от суммы этого расчёта, шаг 2/7 #253). name_filter сужает юниты модели
    (поколение XMAX); getter инъектируется в тестах."""
    cls = bike_class(model)
    # (п.2) минимальный срок аренды: короче → предлагаем минимум и цену на него
    if cls and hint_days is not None and hint_days < cls[1]:
        min_days, label = cls[1], cls[2]
        de_min = _iso_plus(ds, min_days)
        q = None
        if de_min:
            res = _safe_quote_for_model(model, ds, de_min, getter=getter, name_filter=name_filter)
            if res.get("status") == "ok" and res.get("quote") and \
                    pricing.sanity_days_ok(res["quote"].get("days"), min_days, monthly):
                q = res["quote"]
        base = f"{label} сдаём от {min_days} дней (короче срок не оформляем)"
        if q:
            return ("min", f"{base}; цена за {min_days} дн: {_client_price(q)}", q)
        return ("min", f"{base}; точную цену за {min_days} дн уточню и вернусь", None)
    res = _safe_quote_for_model(model, ds, de, getter=getter, name_filter=name_filter)
    status, q = res.get("status"), res.get("quote")
    if status == "ok" and q:
        # SANITY-ГАРД: сверяем days из quote с длительностью из слов клиента.
        if not pricing.sanity_days_ok(q.get("days"), hint_days, monthly):
            return ("sanity", "расчёт по датам не сходится (длительность подозрительная) — НЕ "
                              "называй никакого числа; ответь, что уточню цену по датам и вернусь", None)
        return ("ok", _client_price(q), q)
    if status == "none_available":
        return ("none", "на эти даты все подходящие байки заняты — НЕ называй числа; ответь, что "
                        "уточню наличие и цену на эти даты и вернусь", None)
    return ("error", "точная цена из Календаря сейчас недоступна — НЕ называй никакого числа "
                     "(в т.ч. из FAQ); ответь, что уточнишь цену и вернёшься", None)


def _wrap_single(kind: str, phrase: str) -> str:
    if kind == "ok":
        return ("ЦЕНА из Календаря бронирования (использовать ДОСЛОВНО, не пересчитывать и не "
                "округлять; это ЕДИНСТВЕННАЯ запрошенная модель — цены/депозиты ДРУГИХ моделей "
                "в этом ответе НЕ приводи): " + phrase + ".")
    return "ЦЕНА: " + phrase + "."


def _percent_amount(pct, total):
    """N% от суммы расчёта → целое число ฿ (округление к ближайшему), или None если сумма не задана."""
    try:
        return round(int(total) * int(pct) / 100)
    except (TypeError, ValueError):
        return None


def _percent_line(pct, q) -> str:
    """Инструкция-довесок к ЦЕНЕ: клиент спросил «сколько будет N%» → считаем N% от total этого
    quote КОДОМ и кладём готовое число в блок ЦЕНА (оно попадает в белый список пост-чека → LLM
    вправе его назвать). Нет total → просим уточнить без числа. Строка начинается с пробела."""
    total = q.get("total") if isinstance(q, dict) else None
    amount = _percent_amount(pct, total)
    if amount is None:
        return (f" Клиент спрашивает, сколько будет {pct}% — точную сумму назову после расчёта по "
                f"датам, БЕЗ числа.")
    return (f" Клиент спрашивает, сколько будет {pct}%: это {pct}% от суммы этого расчёта "
            f"({total} ฿) = {amount} ฿ — назови клиенту именно это число.")


# ============================ ПРАЙС ПО ВСЕМУ ПАРКУ (price_sheet) ============================
# Сценарий «клиент просит цены на ВСЕ модели / прайс-лист». Черновик обязан дать РЕАЛЬНЫЕ цифры
# (день/7 дней/месяц по каждой сдаваемой модели), а не переспрашивать модель/опыт и не обещать
# «пришлю позже». Источник — ТОЛЬКО живой Bridge (та же точка правды, что quote_price): по каждой
# модели allowlist три quote() на 1/7/30 дней от даты-якоря. Цифры рендерит КОД детерминированно
# (инвариант как у кап-подстановки) — LLM их не сочиняет и не переформатирует.
_SHEET_TERMS = ((1, "day"), (7, "week"), (30, "month"))     # дни аренды от даты-якоря → ключ ячейки
_SHEET_TTL = int(os.getenv("PRICE_SHEET_TTL_SEC", "180") or "180")   # кэш прайса, минуты (не сутки)
# Параллельность живых quote при построении сетки (класс-фикс регресса 20:59 после 551987e:
# минимум-по-вариантам квотирует ВСЕ живые юниты — ~12 моделей × ~3 юнита × 3 срока ≈ сотня GET;
# ПОСЛЕДОВАТЕЛЬНО по ~3.4с это 6м16с живого билда и замороженный Telethon-цикл. Пул сокращает
# стену до десятков секунд; само МНОЖЕСТВО quote НЕ ослаблено — меняется только конкурентность).
_SHEET_WORKERS = int(os.getenv("PRICE_SHEET_WORKERS", "8") or "8")
# Дедлайн построения целиком: юнит, не успевший к дедлайну (висящий HTTP и т.п.), пропускается
# С ЛОГОМ — сетка выходит из живых остальных, один битый юнит НЕ валит прайс целиком.
_SHEET_DEADLINE = int(os.getenv("PRICE_SHEET_DEADLINE_SEC", "120") or "120")
_sheet_cache = {"key": None, "ts": 0.0, "rows": None}


def _sheet_variants(model, bikes):
    """ВСЕ живые байки-варианты модели в парке (старый/новый юнит и т.п.). → list[str] имён (может
    быть пуст). Тариф в Календаре Bridge отдаёт по ИМЕНИ юнита, и у вариантов одной модели он может
    РАЗЛИЧАТЬСЯ (напр. XMAX старый 2020-2022 дешевле нового 2023+). Раньше брали одного представителя
    и завышали, если жив дешёвый старый юнит; теперь колонки сетки = МИНИМУМ по вариантам (см.
    price_sheet). Матчим ТЕМ ЖЕ _bike_key, что и park_allowlist (снимает CC/СС): иначе «CB 300R» не
    сойдётся с именем «CB 300CC R 9011» (pricing._candidates по _norm_alnum спотыкается о CC)."""
    mk = _bike_key(model)
    if not mk:
        return []
    out = []
    for b in (bikes or []):
        nm = b.get("name") if isinstance(b, dict) else None
        if nm and mk in _bike_key(nm):
            out.append(nm)
    return out


def _sheet_q_total(q):
    """Числовая величина суточной/недельной колонки квоты (сумма аренды) или None."""
    return q.get("total") if isinstance(q, dict) else None


def _sheet_q_month(q):
    """Числовая величина МЕСЯЧНОЙ колонки квоты — РОВНО как показывает _sheet_month_cell: кап-«от»
    имеет приоритет (cap_active и total>cap_price → cap_price), иначе сумма месяца. → число|None."""
    if not isinstance(q, dict):
        return None
    total = q.get("total")
    cap_active, cap_price = q.get("cap_active"), q.get("cap_price")
    if cap_active and cap_price is not None and total is not None and total > cap_price:
        return cap_price
    return total


def _sheet_min_variant(quotes, value_fn):
    """Из квот вариантов модели выбрать ту, что даёт МИНИМАЛЬНУЮ колонку (по value_fn); None-значения
    пропускаем. → квота-победитель (самосогласованный dict — рендер/кап берёт из неё) или None (нет
    ни одной цифры). Для одной-единственной модели-варианта == та самая квота (регресс без изменений)."""
    best = best_v = None
    for q in quotes:
        v = value_fn(q)
        if v is None:
            continue
        if best_v is None or v < best_v:
            best_v, best = v, q
    return best


def _sheet_products(model, variants):
    """Продукты модели для сетки: (метка, свои_варианты). XMAX → ДВА поколения (старое / New Gen)
    отдельными строками — минимум-по-вариантам для XMAX снят (тарифы поколений разные, минимум их
    схлопнул бы в одну цену). Прочие модели — ОДИН продукт со всеми вариантами (минимум-по-вариантам
    сохранён). Пустые поколения (нет живых юнитов) отбрасываем."""
    if _is_xmax_model(model):
        out = []
        for label, pred in _XMAX_PRODUCTS:
            vs = [v for v in variants if pred(v)]
            if vs:
                out.append((label, vs))
        return out
    return [(model, variants)]


def _sheet_min_deposit(term_quotes):
    """Минимальный депозит по ВСЕМ вариант-квотам модели (депозит — такая же колонка сетки: МИНИМУМ
    по вариантам; старый юнит XMAX 5000 vs новый 7000 → 5000). → число|None (нет депозита)."""
    deps = [q.get("deposit") for qs in term_quotes.values() for q in qs
            if isinstance(q, dict) and q.get("deposit") is not None]
    return min(deps) if deps else None


def price_sheet(ds, getter=None, _now=None, _fleet=None):
    """Прайс по всему СДАВАЕМОМУ парку на дату-якорь ds (iso). По каждой модели allowlist (кроме
    несдаваемых) — три quote() (1/7/30 дней) ЖИВЫМ Bridge. → list[{model,class,bike,cells}] или []
    (нет источника). Кэш _SHEET_TTL сек по ds (только для боевого пути; при инъекции getter — без кэша).
    getter/_now/_fleet инъектируются в тестах (боевой Bridge не дёргаем)."""
    now = _now() if _now else time.time()
    c = _sheet_cache
    if getter is None and _fleet is None and c["key"] == ds and c["rows"] is not None \
            and (now - c["ts"]) < _SHEET_TTL:
        return c["rows"]
    try:
        allow = park_allowlist(getter)
    except Exception:
        allow = None
    if not allow:
        return []
    models = [m for m in allow if _bike_key(m) not in _NON_RENTABLE_KEYS]
    bikes = _fleet if _fleet is not None else pricing.fleet(_get=getter)
    # Колонка какой ячейки чем минимизируется: сутки/неделя — по сумме, месяц — по кап-«от» величине.
    value_fn = {"day": _sheet_q_total, "week": _sheet_q_total, "month": _sheet_q_month}
    t0 = time.time()
    # План живых вызовов: (модель × срок × КАЖДЫЙ живой вариант). Квотируем ПАРАЛЛЕЛЬНО пулом
    # (класс-фикс 6м16с последовательного билда 20:59); memo (bike, de) страхует от дублей.
    plan = []                       # (label, variants, {key: [(bike, de), ...]})
    for m in models:
        variants = _sheet_variants(m, bikes)
        if not variants:
            continue
        # XMAX разворачиваем в два продукта-поколения (свои юниты у каждого); прочие — один продукт.
        for label, vs in _sheet_products(m, variants):
            per_term = {}
            for n, key in _SHEET_TERMS:
                de = _iso_plus(ds, n)
                per_term[key] = [(bike, de) for bike in vs] if de else []
            plan.append((label, vs, per_term))

    def _q_one(bike, de):
        """Один живой quote юнита; ЛЮБОЙ сбой → None с логом (битый юнит НЕ валит сетку)."""
        try:
            return pricing.quote(bike, ds, de, _get=getter)
        except Exception as e:
            log.info(f"SHEET: quote юнита {bike} {ds}..{de} упал ({type(e).__name__}) — пропущен")
            return None

    import concurrent.futures as _cf
    futs = {}                       # (bike, de) -> Future (memo в пределах построения)
    ex = _cf.ThreadPoolExecutor(max_workers=max(1, _SHEET_WORKERS))
    try:
        for _, _, per_term in plan:
            for pairs in per_term.values():
                for bd in pairs:
                    if bd not in futs:
                        futs[bd] = ex.submit(_q_one, *bd)
        deadline = t0 + _SHEET_DEADLINE
        results = {}
        for bd, fut in futs.items():
            try:                    # просрочка дедлайна/сбой юнита → None (skip с логом), не обвал
                results[bd] = fut.result(timeout=max(0.0, deadline - time.time()))
            except Exception:
                results[bd] = None
                log.info(f"SHEET: юнит {bd[0]} до {bd[1]} не уложился в дедлайн {_SHEET_DEADLINE}с — пропущен")
    finally:
        # НЕ ждём зависшие HTTP: невзятые в работу отменяем, взятые дотекут в фоне (демон-длинножитель).
        ex.shutdown(wait=False, cancel_futures=True)

    rows = []
    for label, variants, per_term in plan:
        # Порядок квот = порядок вариантов парка (детерминизм при равенстве колонок); None — выпал.
        term_quotes = {key: [q for q in (results.get(bd) for bd in pairs) if q is not None]
                       for key, pairs in per_term.items()}
        cells = {key: _sheet_min_variant(term_quotes[key], value_fn[key]) for _, key in _SHEET_TERMS}
        rows.append({"model": label, "class": bike_class(label), "bike": variants[0], "cells": cells,
                     "deposit": _sheet_min_deposit(term_quotes)})
    n_ok = sum(1 for v in results.values() if v is not None)
    log.info(f"SHEET: сетка построена за {time.time() - t0:.1f}с — {len(rows)} моделей, "
             f"{len(futs)} quote ({len(futs) - n_ok} пропущено), пул {_SHEET_WORKERS}")
    if getter is None and _fleet is None:
        c.update(key=ds, ts=now, rows=rows)
    return rows


def _sheet_total_cell(q):
    """Сумма аренды из quote ('4928 ฿') или None (нет цифры — не выдумываем)."""
    if not isinstance(q, dict):
        return None
    t = q.get("total")
    return f"{t} ฿" if t is not None else None


def _sheet_month_cell(q, lang="ru"):
    """Месячная ячейка с КАПОМ низкого сезона — предикат РОВНО как в _client_price (total>cap_price
    при cap_active → «от <cap> ฿»); иначе сумма месяца."""
    if not isinstance(q, dict):
        return None
    total = q.get("total")
    cap_active, cap_price = q.get("cap_active"), q.get("cap_price")
    if cap_active and cap_price is not None and total is not None and total > cap_price:
        return (f"from {cap_price} ฿" if lang == "en" else f"от {cap_price} ฿")
    return f"{total} ฿" if total is not None else None


def _sheet_deposit(cells):
    for key in ("day", "week", "month"):
        q = cells.get(key)
        if isinstance(q, dict) and q.get("deposit") is not None:
            return q["deposit"]
    return None


def _iso_to_human(ds):
    try:
        d = datetime.date.fromisoformat(ds)
        return f"{d.day:02d}.{d.month:02d}.{d.year}"
    except Exception:
        return ds or ""


def _sheet_min_term_line(lang="ru") -> str:
    """Строка минимального срока из РЕАЛЬНОГО источника (SCOOTER_MIN_DAYS/MOTO_MIN_DAYS —
    правила цен v2, п.2), не литералы-цифры. Явно: колонка «1 день» — суточный ТАРИФ, а не
    право взять байк на одни сутки (ниже мин-срока не оформляем)."""
    if lang == "en":
        return (f"Minimum rental: scooters from {SCOOTER_MIN_DAYS} days, "
                f"motorcycles from {MOTO_MIN_DAYS} (the «1 day» column is the daily tariff, "
                "not a one-day rental).")
    return (f"Минимальный срок: скутеры от {SCOOTER_MIN_DAYS} дней, "
            f"мотоциклы от {MOTO_MIN_DAYS} (колонка «1 день» — суточный тариф, а не аренда на один день).")


def _sheet_low_season(rows) -> bool:
    """Низкий сезон ПО ЖИВОМУ quote (а не по прибитой дате): хоть по одной модели активен кап
    (cap_active) ИЛИ Bridge вернул season со словом low/низк. Капы существуют ТОЛЬКО в низкий
    сезон (см. _sheet_month_cell / _client_price) → активный кап = низкий сезон."""
    for r in rows:
        for q in (r.get("cells") or {}).values():
            if not isinstance(q, dict):
                continue
            if q.get("cap_active"):
                return True
            s = q.get("season")
            if isinstance(s, str) and ("low" in s.lower() or "низ" in s.lower()):
                return True
    return False


# Конец низкого сезона — ДОКУМЕНТИРОВАННАЯ граница парка (низкий сезон 16 мая–31 окт), не выдумка
# per-quote. САМ ФАКТ низкого сезона выводим из живого quote (cap_active/season, см. _sheet_low_season);
# дату конца берём из этой бизнес-константы (у quote поля конца сезона нет — параллельную дату-логику
# не вводим, только фиксированную границу окна).
_LOW_SEASON_END = {"ru": "31 октября", "en": "31 October"}


def _sheet_season_note(rows, lang="ru"):
    """Сезонная пометка — СЛУЖЕБНАЯ, для МОДЕРАТОРА (шаг 4/7 #253): в квадратных скобках, как
    «[уточнить: …]». В КЛИЕНТСКОЕ тело её больше не кладём (клиент не должен читать «низкий сезон /
    до 31 октября» в ответе). НАЛИЧИЕ низкого сезона — из живого quote (cap_active/season), конец
    сезона — документированная граница парка (_LOW_SEASON_END). Высокий сезон → None (сезон не
    утверждаем)."""
    if not _sheet_low_season(rows):
        return None
    if lang == "en":
        return f"[season: low, prices valid until {_LOW_SEASON_END['en']}]"
    return f"[сезон: низкий, цены действуют до {_LOW_SEASON_END['ru']}]"


def render_price_sheet(rows, ds, lang="ru") -> str:
    """Детерминированный прайс-блок (КОД, не LLM): группы «Скутеры:»/«Мотоциклы:» (по bike_class,
    kind неизвестен → хвост без заголовка), внутри — КАРТОЧКА на каждый байк (формат aae1ed2):
    заголовок модели + строки Сутки/Неделя/Месяц + Депозит (число/паспорт), между карточками
    ПУСТАЯ строка. ПЛОСКИЙ ТЕКСТ БЕЗ markdown-символов (** в Telegram не рендерится — живой
    провал вёрстки 21:36, черновик #276). Числа берём как есть из quote-ячеек (кап уже в месячной,
    «от <cap>»); НЕ пересчитываем. Пустые модели (без единой цифры) пропускаем — не выдумываем.
    → текст или '' (нет цифр)."""
    en = (lang == "en")
    na = "—"
    groups = {"scooter": [], "moto": [], None: []}
    for r in rows:
        cells = r.get("cells") or {}
        d = _sheet_total_cell(cells.get("day"))
        w = _sheet_total_cell(cells.get("week"))
        mo = _sheet_month_cell(cells.get("month"), lang)
        if not (d or w or mo):
            continue
        # Депозит — МИНИМУМ по вариантам (row["deposit"], считается в price_sheet); фолбэк на скан
        # ячеек для совместимости, если строку собрали без него.
        dep = r.get("deposit")
        if dep is None:
            dep = _sheet_deposit(cells)
        card = [r["model"]]
        if en:
            card.append(f"• Daily: {d or na}")
            card.append(f"• Week (7 days): {w or na}")
            card.append(f"• Month: {mo or na}")
            if dep is not None:
                card.append(f"• Deposit: {dep} ฿ / passport")
        else:
            card.append(f"• Сутки: {d or na}")
            card.append(f"• Неделя (7 дней): {w or na}")
            card.append(f"• Месяц: {mo or na}")
            if dep is not None:
                card.append(f"• Депозит: {dep} ฿ / паспорт")
        cls = r.get("class")
        kind = cls[0] if cls else None          # bike_class → (kind, min_days, label); XSR155 —
        if kind not in ("scooter", "moto"):     # kind='scooter' (мин-срок 5д) → группа «Скутеры»
            kind = None                         # неизвестный класс → хвост без заголовка
        groups[kind].append("\n".join(card))
    parts = []
    if groups["scooter"]:
        parts.append(("Scooters:" if en else "Скутеры:") + "\n\n" + "\n\n".join(groups["scooter"]))
    if groups["moto"]:
        parts.append(("Motorcycles:" if en else "Мотоциклы:") + "\n\n" + "\n\n".join(groups["moto"]))
    if groups[None]:
        parts.append("\n\n".join(groups[None]))
    return "\n\n".join(parts)


# --- подвыборка строк сетки: класс + рабочий объём cc, ДЕТЕРМИНИРОВАННО (КОД, не LLM-отбор) --------
# Точка правды — те же rows из price_sheet, что и полная сетка (цифры/формат не трогаем, рендер тот
# же). cc берём из метки модели: 3–4-значное число (150/300/750 …). Двузначные хвосты серии («MT-03»,
# «R7») рабочим объёмом НЕ считаем → cc=None; в подвыборке такую строку НЕ выкидываем (fail-open),
# иначе повторили бы живой провал «XADV 750 потерялся в скутерах 200+».
_MODEL_CC_RE = re.compile(r"\d{3,4}")


def _model_cc(label):
    """Рабочий объём (cc) из метки модели: первое 3–4-значное число. → int|None (не прочли)."""
    m = _MODEL_CC_RE.search(str(label or ""))
    return int(m.group(0)) if m else None


def _cc_in_range(cc, cc_min, cc_max):
    """cc в границах [cc_min, cc_max] (любая граница None = без ограничения). cc не прочли (None) →
    True: класс-совпадающую модель из подвыборки НЕ выбрасываем (анти-drop, XADV терялся у LLM)."""
    if cc is None:
        return True
    if cc_min is not None and cc < cc_min:
        return False
    if cc_max is not None and cc > cc_max:
        return False
    return True


def filter_sheet_rows(rows, kind=None, cc_min=None, cc_max=None):
    """Детерминированная подвыборка строк сетки (та же точка правды — rows из price_sheet): по классу
    байка (scooter/moto из bike_class) и рабочему объёму (cc из метки). Модели отбирает КОД, НЕ LLM;
    формат/цифры строк не трогаем. kind/границы None = без соответствующего ограничения. cc не
    прочли → строку оставляем (fail-open: «скутеры 200+» обязаны сохранить XADV 750). → list (может
    быть [] — вызывающий решает, что делать с пустой подвыборкой)."""
    out = []
    for r in rows:
        cls = r.get("class")
        rkind = cls[0] if cls else None
        if kind is not None and rkind != kind:
            continue
        if not _cc_in_range(_model_cc(r.get("model")), cc_min, cc_max):
            continue
        out.append(r)
    return out


# Сетка = НЕПРИКОСНОВЕННЫЙ блок (класс-фикс вёрстки 21:36, черновик #276: LLM пересобирал
# карточки в однострочники и вставлял сырые ** — markdown в Telegram не рендерится). Блок везём
# ВНУТРИ pricing_note между служебными скобками (транспорт: сигнатуры целы, IPC-перегенерация
# несёт блок автоматически), НО в промпт LLM он НЕ попадает (make_system_prompt вырезает) —
# переписать прайс модели НЕЧЕГО по построению. LLM ставит метку [PRICE_SHEET], финал собирает
# КОД: intro + вывод render_price_sheet ДОСЛОВНО + outro (compose_sheet_draft).
_SHEET_OPEN, _SHEET_CLOSE = "<<<SHEET>>>", "<<<END_SHEET>>>"
_SHEET_BLOCK_RE = re.compile(re.escape(_SHEET_OPEN) + r"\n(.*?)\n" + re.escape(_SHEET_CLOSE), re.S)
# Сезонная СЛУЖЕБНАЯ пометка (шаг 4/7 #253) едет тем же транспортом, что и SHEET-блок — внутри
# служебных скобок pricing_note: в промпт LLM НЕ попадает (make_system_prompt вырезает), в
# клиентское тело НЕ попадает; финал приклеивает её ХВОСТОМ черновика для модератора.
_SEASON_OPEN, _SEASON_CLOSE = "<<<SEASON>>>", "<<<END_SEASON>>>"
_SEASON_BLOCK_RE = re.compile(re.escape(_SEASON_OPEN) + r"\n(.*?)\n" + re.escape(_SEASON_CLOSE), re.S)
_SHEET_MARKER = "[PRICE_SHEET]"
# Метка в ответе LLM: отдельной строкой (\W покрывает скобки/кавычки/пунктуацию вокруг)
# ИЛИ инлайн «[PRICE_SHEET]» — режем по первому попаданию.
_SHEET_MARKER_LINE_RE = re.compile(r"^\W*PRICE[_ ]?SHEET\W*$", re.I | re.M)
_SHEET_MARKER_INLINE_RE = re.compile(r"\[?PRICE[_ ]?SHEET\]?", re.I)

# ТОЧЕЧНЫЙ quote (одна модель+даты) едет тем же транспортом, что SHEET-блок — детерминированная
# строка ЦЕНЫ в служебных скобках pricing_note: в промпт LLM НЕ попадает (make_system_prompt
# вырезает), а в финал её вставляет КОД (compose_quote_draft). Так strategy-перегенерация несёт
# цену Bridge КОДОМ и НЕ зависит от того, «донёс» ли LLM цифры (класс тот же, что у сетки #276).
_QUOTE_OPEN, _QUOTE_CLOSE = "<<<QUOTE>>>", "<<<END_QUOTE>>>"
_QUOTE_BLOCK_RE = re.compile(re.escape(_QUOTE_OPEN) + r"\n(.*?)\n" + re.escape(_QUOTE_CLOSE), re.S)
# Метка [QUOTE] в ответе LLM (опциональная точка вставки): только явная скобочная форма, чтобы
# случайное слово «quote» в тексте её не триггерило.
_QUOTE_MARKER_RE = re.compile(r"\[\s*QUOTE\s*\]", re.I)

# Служебный блок ДОСТАВКИ (шаг 5/7 #12) — ТОТ ЖЕ транспорт, что <<<QUOTE>>>: цену доставки в финал
# вставляет КОД (compose_delivery_draft), из промпта блок ВЫРЕЗАЕТСЯ — LLM цифру доставки НЕ
# генерирует и НЕ правит. Отдельный блок (а НЕ внутри <<<QUOTE>>>), чтобы приклейка строки доставки
# не задваивала строку ЦЕНЫ аренды (у неё своё денежное число — guard compose_quote_draft иначе
# счёл бы весь quote-блок «недонесённым» и повторил бы цену аренды).
_DELIVERY_OPEN, _DELIVERY_CLOSE = "<<<DELIVERY>>>", "<<<END_DELIVERY>>>"
_DELIVERY_BLOCK_RE = re.compile(
    re.escape(_DELIVERY_OPEN) + r"\n(.*?)\n" + re.escape(_DELIVERY_CLOSE), re.S)


def _sheet_block_from_note(pricing_note):
    """Чистый прайс-блок из служебных скобок pricing_note → текст | None (не sheet-режим /
    старый формат note без скобок — прежний путь)."""
    m = _SHEET_BLOCK_RE.search(pricing_note or "")
    return m.group(1) if m else None


def compose_sheet_draft(llm_text, block, lang="ru"):
    """Детерминированная сборка финального черновика: intro (LLM) + блок ДОСЛОВНО + outro (LLM).
    Метку [PRICE_SHEET] ищем строкой, затем инлайн; НЕТ метки → LLM-текст ОТБРАСЫВАЕМ целиком
    (он мог переписать прайс) и ставим детерминированные intro/outro (fail-safe: сетка дословно
    доходит ВСЕГДА). Пустые intro/outro просто пропускаются."""
    en = (lang == "en")
    t = (llm_text or "").strip()
    m = _SHEET_MARKER_LINE_RE.search(t) or _SHEET_MARKER_INLINE_RE.search(t)
    if m:
        intro, outro = t[:m.start()].strip(), t[m.end():].strip()
    else:
        log.warning("SHEET: LLM не выдал метку [PRICE_SHEET] — интро/концовка детерминированные")
        intro = "Here is our current price list:" if en else "Актуальный прайс по нашему парку:"
        outro = ("Tell me which model you like and your dates — I will check availability."
                 if en else
                 "Подскажите, какая модель вас заинтересовала и на какие даты — проверю наличие.")
    parts = [p for p in (intro, block, outro) if p]
    return "\n\n".join(parts)


def _quote_block_from_note(pricing_note):
    """Детерминированная строка ЦЕНЫ точечного quote (из служебных скобок pricing_note) → текст |
    None (не точечный-quote-режим). Питает compose_quote_draft."""
    m = _QUOTE_BLOCK_RE.search(pricing_note or "")
    return m.group(1) if m else None


def compose_quote_draft(llm_text, block, lang="ru"):
    """Точечный quote-блок Bridge попадает в финал КОДОМ (как compose_sheet_draft для сетки), чтобы
    strategy-перегенерация НЕ зависела от того, донёс ли LLM цифры:
      • есть метка [QUOTE] → intro (LLM) + блок ДОСЛОВНО + outro (LLM);
      • метки нет, но ВСЕ денежные числа блока уже в клиентском теле (LLM донёс цену) → текст как
        есть, не дублируем;
      • метки нет и хотя бы одно денежное число блока потеряно (LLM цену не привёл) → приклеиваем
        блок хвостом (FAIL-SAFE: цена Bridge доходит клиенту ВСЕГДА).
    Сверяем ДЕНЕЖНЫЕ величины (extract_money_figures — та же точка правды, что пост-чек), а не сырые
    цифры: модельное число (NMAX 155) и счётчик срока (5 дней) на решение не влияют. В отличие от
    сетки, при отсутствии метки LLM-текст НЕ отбрасываем — точечная цена вплетена в живой ответ, а
    не самостоятельный блок; отбросить его = потерять весь ответ."""
    t = (llm_text or "").strip()
    b = (block or "").strip()
    if not b:
        return t
    m = _QUOTE_MARKER_RE.search(t)
    if m:
        intro, outro = t[:m.start()].strip(), t[m.end():].strip()
        return "\n\n".join(p for p in (intro, b, outro) if p)
    if not t:
        return b
    want = {f["value"] for f in extract_money_figures(b)}
    have = {f["value"] for f in extract_money_figures(client_facing_text(t))}
    if want and want <= have:                    # все денежные числа Bridge уже у клиента — не дублируем
        return t
    log.warning("QUOTE: LLM не привёл цену точечного quote — цена Bridge добавлена КОДОМ хвостом")
    return t + "\n\n" + b


def _delivery_block_from_note(pricing_note):
    """Клиентская строка ЦЕНЫ доставки из служебных скобок pricing_note → текст | None (не
    delivery-режим). Питает compose_delivery_draft."""
    m = _DELIVERY_BLOCK_RE.search(pricing_note or "")
    return m.group(1) if m else None


def compose_delivery_draft(llm_text, block, lang="ru"):
    """Строку ДОСТАВКИ Bridge в финал доносит КОД (тот же класс, что compose_quote_draft): цену
    доставки LLM не видит (блок вырезан из промпта make_system_prompt) → приклеиваем хвостом.
    Если денежное число доставки ПОЧЕМУ-ТО уже в клиентском теле — не дублируем (guard как у quote)."""
    t = (llm_text or "").strip()
    b = (block or "").strip()
    if not b:
        return t
    if not t:
        return b
    want = {f["value"] for f in extract_money_figures(b)}
    have = {f["value"] for f in extract_money_figures(client_facing_text(t))}
    if want and want <= have:                    # цена доставки уже у клиента — не дублируем
        return t
    return t + "\n\n" + b


def _delivery_quote_line(text, lang="ru", _resolve=None):
    """Клиентская строка ДОСТАВКИ по maps-ссылке клиента (несёт КОД, не LLM), либо None.
    None → ссылки нет ИЛИ доставку не определили (uncertain/[уточнить]): остаётся текущий честный
    путь — район уточнит менеджер, цифру НЕ выдумываем. Число даём ТОЛЬКО при zone/out_belt.
    _resolve — инъекция для тестов (по умолчанию delivery.resolve_delivery_from_text; сеть/Bridge)."""
    try:
        res = (_resolve or delivery.resolve_delivery_from_text)(text)
    except Exception as e:
        log.info(f"_delivery_quote_line: резолв упал ({type(e).__name__}) — без строки доставки")
        return None
    if not isinstance(res, dict) or res.get("status") not in ("zone", "out_belt"):
        return None
    price = res.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    n = int(price)
    if lang == "en":
        return f"Delivery — {n} ฿ (bike pickup at the end of the rental is free)."
    return f"Доставка — {n} ฿ (забор байка в конце аренды — бесплатный)."


def _delivery_note_block(hints, lang="ru"):
    """Служебный <<<DELIVERY>>>-блок для pricing_note (тот же транспорт, что <<<QUOTE>>>), либо ''
    если доставку не определили по maps-ссылке клиента. Ставится рядом с quote-блоком в
    build_pricing_note; в промпт не попадает, в финал его вставит compose_delivery_draft (КОД)."""
    text = hints.get("maps_link") if isinstance(hints, dict) else None
    line = _delivery_quote_line(text, lang)
    if not line:
        return ""
    return "\n" + _DELIVERY_OPEN + "\n" + line + "\n" + _DELIVERY_CLOSE


def _wrap_price_sheet(body, ds, lang="ru", default_anchor=False) -> str:
    """Обёртка pricing_note: инструкция LLM (вступление + метка [PRICE_SHEET] + концовка, БЕЗ цен —
    сетку вставит КОД дословно) + сам блок в служебных скобках (для compose_sheet_draft; в промпт
    не попадает). default_anchor — дат клиент НЕ назвал: якорь = ближайшая дата (завтра); даты НЕ
    переспрашиваем (сетка готова)."""
    human = _iso_to_human(ds)
    if lang == "en":
        anchor = (f"Dates not specified — the price is calculated from the nearest date ({human}, "
                  "starting tomorrow); if the client names exact dates you will recalculate. Do NOT "
                  "ask for dates — the price list is already prepared."
                  if default_anchor else f"Reference start date: {human}.")
        instr = (
            "PARK PRICE LIST from the Calendar is READY — the CODE will insert it VERBATIM in place "
            "of your [PRICE_SHEET] marker; you must NOT rewrite, reformat or repeat it. Build your "
            "reply as: (1) a short intro (1–2 sentences, NO prices, NO model lists); (2) the marker "
            "[PRICE_SHEET] on its OWN line; (3) a short outro (one sentence: offer to check "
            "availability for the client's dates/model). Do not write any price or deposit numbers "
            "anywhere — they are already in the list. " + anchor)
    else:
        anchor = (f"Даты аренды клиент не назвал — прайс посчитан от ближайшей даты ({human}, старт "
                  "завтра); назовёт точные даты — пересчитаешь. НЕ переспрашивай даты: сетка уже готова."
                  if default_anchor else f"Дата отсчёта: {human}.")
        instr = (
            "ПРАЙС ПО ПАРКУ из Календаря ГОТОВ — его вставит КОД ДОСЛОВНО на место твоей метки "
            "[PRICE_SHEET]; переписывать, переформатировать или повторять прайс НЕЛЬЗЯ. Построй "
            "ответ так: (1) короткое вступление (1–2 предложения, БЕЗ цен и БЕЗ перечисления "
            "моделей); (2) ОТДЕЛЬНОЙ строкой ровно метка [PRICE_SHEET] — на её месте появится "
            "прайс; (3) короткая концовка (одно предложение: предложи проверить наличие по "
            "модели/датам клиента). Числа цен и депозитов сам НЕ пиши нигде — они уже в прайсе. "
            + anchor)
    return instr + "\n" + _SHEET_OPEN + "\n" + body + "\n" + _SHEET_CLOSE


# Прайс запрошен, даты есть, но живой источник не отдал ни одной цифры → честный фолбэк БЕЗ чисел
# и БЕЗ переспроса модели (не выдумываем и не зацикливаемся).
_PRICE_SHEET_UNAVAILABLE = (
    "ПРАЙС ПО ПАРКУ: точные цены из Календаря сейчас недоступны. НЕ называй никаких чисел (в т.ч. "
    "из FAQ) и НЕ переспрашивай модель/опыт; ответь, что соберёшь актуальный прайс по паркам на эти "
    "даты и вернёшься в ближайшее время.")


def build_price_sheet_note(hints, lang="ru", getter=None, today=None):
    """Прайс-блок для промпта, если клиент просит прайс по парку. → строка ИЛИ None (интент не тот).
    None → обычный ценовой путь build_pricing_note. ДАТЫ НЕ ГЕЙТ (решение владельца): нет дат в
    диалоге → якорь = завтра (ближайшая дата старта), сетку считаем и выдаём САМИ, не переспрашивая.
    Даты в диалоге → считаем по ним. today инъектируется в тестах (боевой путь — реальная дата)."""
    if not hints.get("price_sheet_q"):
        return None
    ds = hints.get("iso_start")
    default_anchor = False
    if not ds:
        # НЕ спрашиваем даты: дефолтное окно от ближайшей даты (старт = завтра).
        base = today or datetime.date.today()
        ds = (base + datetime.timedelta(days=1)).isoformat()
        default_anchor = True
    rows = price_sheet(ds, getter=getter)
    # Подвыборка («скутеры 200+», «мотоциклы до 400») — КОД по данным сетки, не LLM-отбор. Пустая
    # подвыборка (ничего не совпало) → показываем ПОЛНУЮ сетку, не немеем и не выдумываем.
    sf = hints.get("sheet_filter")
    if sf:
        sub = filter_sheet_rows(rows, kind=sf.get("kind"),
                                cc_min=sf.get("cc_min"), cc_max=sf.get("cc_max"))
        if sub:
            rows = sub
    body = render_price_sheet(rows, ds, lang)
    if not body.strip():
        return _PRICE_SHEET_UNAVAILABLE
    # Детерминированные строки КОДА (не LLM): мин-срок из констант — хвостом ПОД карточками моделей.
    # Числа/формат карточек рендерит render_price_sheet. Сезонность (шаг 4/7 #253) в КЛИЕНТСКОЕ
    # тело больше НЕ кладём — она уходит модератору отдельным служебным каналом (SEASON-скобки ниже).
    block = body + "\n\n" + _sheet_min_term_line(lang)
    note = _wrap_price_sheet(block, ds, lang, default_anchor=default_anchor)
    season = _sheet_season_note(rows, lang)
    if season:
        note += "\n" + _SEASON_OPEN + "\n" + season + "\n" + _SEASON_CLOSE
    return note


def build_pricing_note(hints: dict, lang: str = "ru", getter=None, today=None) -> str:
    """Инструкция по цене для промпта. ИНВАРИАНТ: без котировки из Календаря — без числа.
    Правила цен v2: кап низкого сезона (п.1), минимальный срок (п.2), несколько моделей одной
    строкой каждая (п.3), депозит при нескольких байках (п.4), J-текст дословно (п.5).
    Сценарий price_sheet (прайс по всему парку) перехватывается ПЕРВЫМ."""
    # (п.4) депозит при нескольких байках — инструкция дописывается к ЛЮБОМУ исходу цены.
    dep = (" " + DEPOSIT_MULTI_NOTE) if hints.get("deposit_multi_q") else ""
    # «N юнитов одной модели» (пара XMAX): цена/депозит Bridge = ЗА КАЖДЫЙ, общий итог не выдумываем.
    _uc = hints.get("units_count")
    units = _units_count_note(_uc, lang) if (_uc and _uc >= 2) else ""
    sheet = build_price_sheet_note(hints, lang=lang, getter=getter, today=today)
    if sheet is not None:
        return sheet + dep
    if not hints.get("has_dates"):
        return ("ЦЕНА: дат аренды в диалоге НЕТ — попроси у клиента даты (начало/конец) и срок. "
                "НЕ называй НИКАКУЮ цену: ни точную, ни ориентир, ни «от X ฿», ни диапазон "
                "(«X–Y ฿»), ни «from X» — вообще никаких чисел цены, в т.ч. из FAQ. "
                "Цену назовём только после дат, из Календаря.") + dep
    ds, de = hints.get("iso_start"), hints.get("iso_end")
    models = hints.get("models") or ([hints["model"]] if hints.get("model") else [])
    if not (models and ds and de):
        # даты есть словами, но модель/полные даты не разобрались → фолбэк БЕЗ числа
        return ("ЦЕНА: не удалось однозначно разобрать модель/даты для Календаря — НЕ называй "
                "никакого числа (в т.ч. из FAQ); уточни модель и точные даты и скажи, что "
                "назовёшь цену по датам.") + dep
    hint_days, monthly = hints.get("hint_days"), hints.get("monthly")

    # ДЕТЕРМИНИРОВАННЫЙ РЕЗОЛВ модели в КОНКРЕТНУЮ модель парка (Лист1/_bike_key, алиасы) ДО quote:
    # специфичная фраза → своя модель (не лупный серийный корень, который матчил бы чужие юниты);
    # неоднозначная серия (в парке и XSR155, и XSR900) → НЕ угадываем и НЕ подставляем чужую
    # карточку, а просим клиента уточнить модель. 'unknown' (парк недоступен/модель вне серии) →
    # оставляем исходный canon (прежний путь, fail-safe).
    # Резолвим ТОЛЬКО серийные корни (XSR/ADV/PCX/CB) — именно они матчат несколько юнитов и рискуют
    # подставить чужую карточку. Конкретные модели (NMAX/MT-03/…) уже однозначны — их не трогаем.
    resolved = []
    for m in models:
        if _bike_key(m) in _MODEL_SERIES:
            st, disp, _key = resolve_park_model(m, getter=getter)
            if st == "ambiguous":
                opts = ", ".join(disp) if isinstance(disp, list) else str(disp)
                return ("ЦЕНА: клиент назвал серию (" + str(m) + "), а в парке несколько вариантов ("
                        + opts + ") с РАЗНОЙ ценой/классом — НЕ называй никакого числа и НЕ выбирай "
                        "модель сам (чужую карточку не подставляй); уточни у клиента, какая именно "
                        "модель нужна, и назови цену уже после уточнения.") + dep
            resolved.append(disp if (st == "ok" and disp) else m)
        else:
            resolved.append(m)
    models = resolved

    # Разворачиваем модели в ПРОДУКТЫ через ЕДИНЫЙ гэттер поколений: XMAX по умолчанию → ТОЛЬКО
    # актуальное поколение (New Gen) одной строкой, без годов; прежнее поколение (старое + New Gen
    # двумя строками) — лишь по явному запросу клиента (old_gen_q, «а старый xmax есть?»). Прочие
    # модели — один продукт как раньше. (label, модель_для_quote, name_filter-по-имени-юнита.)
    want_old_gen = bool(hints.get("old_gen_q"))
    products = [(label, m, nf) for m in models
                for label, nf in _model_products_for_quote(m, want_old_gen=want_old_gen)]

    # Клиент выбрал ПАСПОРТ как депозит (KB: деньги ЛИБО паспорт, не оба): сумму депозита из Bridge в
    # строке цены меняем на «депозит: паспорт» ДО _wrap_single — так и инструкция LLM (тело ответа), и
    # quote-хвост несут паспорт, а не число (иначе тело=«паспорт», хвост=«депозит 3000 ฿» — противоречие).
    passport_dep = bool(hints.get("deposit_passport_q"))

    if len(products) == 1:
        label, m, nf = products[0]
        kind, phrase, q = _resolve_model_price(m, ds, de, hint_days, monthly, getter=getter,
                                               name_filter=nf)
        if passport_dep:
            phrase = _deposit_as_passport(phrase, lang)
        note = _wrap_single(kind, phrase)
        # «сколько будет N%» → процент считаем КОДОМ от суммы ЭТОГО расчёта (шаг 2/7 #253); число
        # ложится в блок ЦЕНА → пройдёт пост-чек. Есть live-quote (ok/min) → считаем; иначе просим
        # уточнить без числа.
        pct = hints.get("percent_q")
        if pct:
            note += _percent_line(pct, q if kind in ("ok", "min") else None)
        note += dep + units
        # Точечный quote → детерминированную строку ЦЕНЫ кладём в служебные скобки: strategy-
        # перегенерация донесёт цену Bridge КОДОМ (compose_quote_draft в generate/regenerate), не
        # полагаясь на то, что LLM её перепишет. N ЮНИТОВ одной модели — блок несём ТОЖЕ, но с
        # пометкой «цена/депозит ЗА КАЖДЫЙ юнит» (итог за N шт. КОД НЕ суммирует — числа только из
        # Bridge). Процент — единой клиентской строки ЦЕНЫ нет (сумма вплетена в инструкцию), пропуск.
        if kind == "ok" and not pct:
            # Год поколения из сырого имени юнита (J-текст Bridge) в quote-хвост не течёт: поколение
            # несёт метка label (New Gen), год выпуска убираем — как в клиентском теле (см. _scrub_gen_year).
            qphrase = _scrub_gen_year(phrase)
            base = f"{label} — {qphrase}" if label else qphrase.rstrip(".")
            qline = (base + ". " + _units_per_each_line(_uc, lang)) if units else (base + ".")
            note += "\n" + _QUOTE_OPEN + "\n" + qline + "\n" + _QUOTE_CLOSE
            # ДОСТАВКА (шаг 5/7 #12): по maps-ссылке клиента считаем цену доставки КОДОМ и несём её
            # тем же транспортом рядом с quote-блоком; нет ссылки/зон/Bridge → '' (честный [уточнить]).
            note += _delivery_note_block(hints, lang)
        return note

    # (п.3) несколько продуктов (несколько моделей ИЛИ два поколения XMAX) — раздельная цена по
    # каждому, отдельной строкой в одном сообщении.
    bullets = []
    ok_lines = []
    for label, m, nf in products:
        kind, phrase, _q = _resolve_model_price(m, ds, de, hint_days, monthly, getter=getter,
                                                name_filter=nf)
        if passport_dep:
            phrase = _deposit_as_passport(phrase, lang)
        bullets.append(f"- {label}: {phrase}")
        if kind == "ok":                    # только строки с ЖИВОЙ ценой Bridge едут в quote-блок
            ok_lines.append(f"- {label}: {_scrub_gen_year(phrase)}")   # год поколения в хвост не течёт
    header = ("ЦЕНЫ ПО МОДЕЛЯМ (клиент запросил несколько / модель с вариантами) — назови КАЖДУЮ "
              "отдельной строкой в ОДНОМ сообщении, цену использовать ДОСЛОВНО, модели/варианты НЕ "
              "смешивай и НЕ суммируй:\n")
    note = header + "\n".join(bullets) + dep + units
    # N ЮНИТОВ одной модели с вариантами (пара XMAX: два поколения) → живые цены поколений тоже
    # несём в служебный quote-блок для strategy-пути; цена/депозит ЗА КАЖДЫЙ юнит, итог за N шт.
    # КОД НЕ суммирует (units set ТОЛЬКО при ОДНОЙ модели — _units_count даёт None на len(models)≥2).
    if units and ok_lines:
        block = "\n".join(ok_lines) + "\n" + _units_per_each_line(_uc, lang)
        note += "\n" + _QUOTE_OPEN + "\n" + block + "\n" + _QUOTE_CLOSE
        # ДОСТАВКА (шаг 5/7 #12): та же врезка рядом с quote-блоком и в multi-юнит случае.
        note += _delivery_note_block(hints, lang)
    return note


def _pressure_block(pressure) -> str:
    """Блок УРОВНЯ НАПОРА — НА УРОВНЕ базовой установки (высокий приоритет, выше playbook). normal → ''
    (базовое поведение без изменений). Инварианты (цена/депозит/парк/без обмана) НЕ ослабляет — они ниже
    и держатся; firm лишь расширяет допустимый диапазон настойчивости (не агрессии, не лжи)."""
    p = (pressure or "normal").strip().lower()
    if p == "firm":
        return (
            "\n\nУРОВЕНЬ НАСТОЙЧИВОСТИ: FIRM (активные продажи). Веди клиента к следующему шагу "
            "решительнее: ответив на вопрос, делай ЯВНЫЙ призыв к действию — предложи забронировать и "
            "внести предоплату, чтобы зафиксировать байк и даты, назови ближайший конкретный шаг. "
            "Подчёркивай выгоду и что популярные даты/байки быстро разбирают. Оставайся вежливым и "
            "ПРАВДИВЫМ: без агрессии и манипуляций, без выдуманного дефицита и лживых обещаний; "
            "ценовую политику, депозит и парк ниже НЕ нарушай."
        )
    if p == "soft":
        return (
            "\n\nУРОВЕНЬ НАСТОЙЧИВОСТИ: SOFT: максимально мягко, без нажима — информируй и отвечай на "
            "вопрос, инициативу брони оставляй клиенту, призыв к действию только по явному интересу."
        )
    return ""   # normal — базовое поведение без изменений (регресс не трогаем)


# АНТИ-ЛУП: против переспросов уже известного (симптом «лупили какая модель/какой опыт» при
# готовом ответе в диалоге) и против обещаний «пришлю прайс позже» при наличии блока ЦЕНА/ПРАЙС.
ANTI_LOOP_NOTE = (
    "\n\nБЕЗ ПЕРЕСПРОСОВ (СТРОГО): не спрашивай повторно то, что клиент УЖЕ сообщил или что видно "
    "из диалога — даты/период аренды, «нужны все модели / весь прайс», что клиент сам менеджер/агент, "
    "уже названный опыт. Есть в истории — бери оттуда, не переспрашивай. Если ниже передан блок "
    "ЦЕНА/ПРАЙС с цифрами — приведи эти цифры и НЕ обещай «пришлю прайс/цены позже», «подготовлю "
    "отдельно», «вернусь с прайсом»: нужные числа уже здесь, дай их сразу. Если передан блок ПРАЙС "
    "ПО ПАРКУ — вопрос про даты клиенту НЕ задавай: даты лишь уточняют расчёт, но НЕ нужны для "
    "выдачи сетки (она уже посчитана от ближайшей даты)."
)


def make_system_prompt(faq: str, lang: str, is_first_contact: bool = False, pricing_note: str = "",
                       directive: str = "", park_models=None, playbook: str = "", pressure=None,
                       collected=None) -> str:
    lang_name = "русском" if lang == "ru" else "английском"
    if is_first_contact:
        greet = (
            "\n\nЭто ПЕРВЫЙ ответ в этом диалоге — НАЧНИ ответ с фирменного приветствия из "
            "FAQ («Здравствуйте! …»), затем переходи к сути."
        )
    else:
        greet = (
            "\n\nЭто ПРОДОЛЖЕНИЕ диалога (клиент уже поздоровался/получил автоприветствие) — "
            "НЕ здоровайся повторно. НЕ начинай ответ с «Здравствуйте/Привет/Приветствую/Добрый "
            "день|утро|вечер/Спасибо, что написали|выбрали нас/Hello/Hi» и подобных зачинов — "
            "сразу отвечай по сути."
        )
    # Живой провал 20:59 (черновик #275): сетка ПРАЙС ПО ПАРКУ дошла до промпта, но policy ниже
    # («СТРОГО»: «Дат нет — сперва спроси даты», цена только из блока «ЦЕНА из Календаря») её не
    # знала — LLM 9/10 следовал политике и переспрашивал даты/опыт, выбрасывая готовую сетку.
    # Класс-фикс У ИСТОЧНИКА конфликта: когда pricing_note — прайс по парку, сама политика говорит
    # «это И ЕСТЬ цена из Календаря — выдай дословно, даты не переспрашивай». Обычный путь (ЦЕНА
    # по датам / нет цены) — прежний текст байт-в-байт.
    # Маркерный sheet-режим (блок в служебных скобках): сетку вставляет КОД, LLM цифры не видит и
    # не пишет — класс «модель прайс не переписывает никогда» гарантирован по построению (живой
    # провал вёрстки 21:36 #276: однострочники + сырые **). Legacy sheet (старая note без скобок,
    # напр. из IPC при перегенерации) — прежняя политика «приведи дословно».
    sheet_block = _sheet_block_from_note(pricing_note)
    sheet_mode = bool(pricing_note) and (sheet_block is not None
                                         or "ПРАЙС ПО ПАРКУ" in pricing_note
                                         or "PARK PRICE LIST" in pricing_note)
    if sheet_block is not None:
        policy = (
            "\n\nЦЕНОВАЯ ПОЛИТИКА (СТРОГО): прайс по всему парку УЖЕ посчитан из Календаря и "
            "будет вставлен КОДОМ ДОСЛОВНО на место метки [PRICE_SHEET] — сам НИКАКИЕ цифры цен "
            "и депозитов НЕ пиши (ни из FAQ, ни ориентиры, ни диапазоны). Твой ответ: короткое "
            "вступление БЕЗ цен и БЕЗ перечисления моделей → ОТДЕЛЬНОЙ строкой ровно [PRICE_SHEET] "
            "→ короткая концовка (предложи проверить наличие). Даты НЕ переспрашивай и НЕ жди их: "
            "сетка посчитана от ближайшей даты, точные даты лишь уточнят расчёт потом. Вопрос об "
            "опыте НЕ заменяет выдачу прайса. Про уменьшение депозита при нескольких байках сам "
            "НЕ предлагай и НЕ обещай; на прямой вопрос клиента ответь ровно «уточню у менеджера»."
        )
    elif sheet_mode:
        policy = (
            "\n\nЦЕНОВАЯ ПОЛИТИКА (СТРОГО): ниже передан ГОТОВЫЙ блок «ПРАЙС ПО ПАРКУ из "
            "Календаря» — это И ЕСТЬ цена из Календаря, уже посчитанная по всему парку. Приведи "
            "его цифры и строки мин-срока/сезона клиенту ДОСЛОВНО В ЭТОМ ЖЕ ответе. Даты НЕ "
            "переспрашивай и НЕ жди их: сетка посчитана от ближайшей даты, точные даты лишь "
            "уточнят расчёт потом. Вопрос об опыте НЕ заменяет выдачу прайса: сперва прайс, "
            "остальное можно уточнить после него. НИКАКИХ других цен (FAQ/ориентиры/диапазоны) "
            "не называй. Про уменьшение депозита при нескольких байках сам НЕ предлагай и НЕ "
            "обещай; на прямой вопрос клиента ответь ровно «уточню у менеджера»."
        )
    else:
        policy = (
            "\n\nЦЕНОВАЯ ПОЛИТИКА (СТРОГО): цену клиенту называй ТОЛЬКО если она передана ниже в "
            "блоке «ЦЕНА из Календаря». Пока такой цены нет (или дат в диалоге нет) — НЕ называй "
            "клиенту НИКАКУЮ цену: ни точную, ни ориентир, ни «от X», ни диапазон, ни «from», ни "
            "ставку из FAQ. Дат нет — сперва спроси даты. Цена недоступна — скажи, что уточнишь "
            "цену по датам и вернёшься, БЕЗ числа. Дневные ставки FAQ — ориентир ДЛЯ МЕНЕДЖЕРА, "
            "не для клиента. Про уменьшение депозита при нескольких байках сам НЕ предлагай и НЕ "
            "обещай; на прямой вопрос клиента ответь ровно «уточню у менеджера»."
        )
    if sheet_block is not None:
        stage1 = ("Этап 1 — ЦЕНА: выведи ОТДЕЛЬНОЙ строкой метку [PRICE_SHEET] — прайс на её "
                  "место вставит код; даты для этого НЕ нужны. ")
    elif sheet_mode:
        stage1 = ("Этап 1 — ЦЕНА: приведи прайс-сетку из блока «ПРАЙС ПО ПАРКУ» ДОСЛОВНО — это "
                  "и есть цена; даты для этого НЕ нужны. ")
    else:
        stage1 = "Этап 1 — ЦЕНА: назови цену по датам из Календаря (если она в блоке ЦЕНА выше). "
    scenario = (
        "\n\n[ВНУТРЕННИЙ КОНТЕКСТ — только для тебя, клиенту НЕ показывать; номера этапов и "
        "слова «этап»/«стадия» в самом ответе НЕ упоминать]\n"
        "ПОРЯДОК ДИАЛОГА (СТРОГО по этапам, не забегай вперёд):\n"
        + stage1 +
        "На этом этапе НЕ проси паспорт, апартаменты и шлемы — только цена и наличие.\n"
        "Этап 2 — ДОСТАВКА: когда клиент заинтересовался ценой — уточни район доставки и назови "
        "её стоимость по прайсу районов; добавь, что забор байка в конце аренды БЕСПЛАТНЫЙ ПРИ "
        "ОПЛАЧЕННОЙ доставке (если клиент забирает сам/самовывоз — бесплатного забора НЕ обещай).\n"
        "Этап 3 — БРОНЬ: только когда клиент готов бронировать — запроси качественное фото "
        "паспорта, название апартаментов (или ссылку Google Maps), количество шлемов и "
        "номер(а) телефона. Раньше этапа 3 документы/апартаменты/шлемы не запрашивай.\n"
        "Это разметка ТВОЕЙ логики: определи текущий этап про себя и действуй по нему — но в "
        "тексте клиенту про этапы/стадии/«менеджер не назвал цену» не пиши, отвечай сразу по сути."
    )
    # Маркерный sheet-режим: сам блок из промпта ВЫРЕЗАЕМ (LLM цифры не видит → переписать
    # нечего); остаётся инструкция + пометка, что сетку вставит код на место [PRICE_SHEET].
    pn_prompt = pricing_note
    if sheet_block is not None:
        pn_prompt = _SHEET_BLOCK_RE.sub(
            "(прайс-сетка уже посчитана — КОД вставит её ДОСЛОВНО на место метки [PRICE_SHEET])",
            pricing_note)
    # Сезонная СЛУЖЕБНАЯ пометка (шаг 4/7 #253) — только для модератора: из промпта LLM её ВЫРЕЗАЕМ,
    # чтобы LLM не выдал «низкий сезон / до 31 октября» в клиентское тело.
    if _SEASON_BLOCK_RE.search(pn_prompt or ""):
        pn_prompt = _SEASON_BLOCK_RE.sub("", pn_prompt).rstrip()
    # Точечный quote-блок — служебный транспорт для compose_quote_draft (цена Bridge вставится КОДОМ):
    # из промпта его ВЫРЕЗАЕМ (сама цена уже есть в инструкции ЦЕНА выше — дубль LLM не нужен).
    if _QUOTE_BLOCK_RE.search(pn_prompt or ""):
        pn_prompt = _QUOTE_BLOCK_RE.sub("", pn_prompt).rstrip()
    # Блок ДОСТАВКИ (шаг 5/7 #12) — служебный транспорт для compose_delivery_draft: из промпта его
    # ВЫРЕЗАЕМ, чтобы LLM цену доставки не видел (не сгенерировал/не переписал); вставит её КОД.
    if _DELIVERY_BLOCK_RE.search(pn_prompt or ""):
        pn_prompt = _DELIVERY_BLOCK_RE.sub("", pn_prompt).rstrip()
    price_block = ("\n\n" + pn_prompt) if pn_prompt else ""
    # СТРАТЕГИЯ-директива менеджера: высший приоритет по СОДЕРЖАНИЮ/логике/тону ответа, НО
    # ценовую политику и критичные факты НЕ отменяет (они ниже — незыблемы).
    directive_block = ""
    if (directive or "").strip():
        directive_block = (
            "\n\n★ ДИРЕКТИВА МЕНЕДЖЕРА (ВЫСШИЙ приоритет по стратегии/логике/тону — строй ответ "
            "именно так): " + directive.strip()
            + "\nВАЖНО: эта директива задаёт ЧТО и КАК предлагать, но НЕ отменяет ценовую политику "
            "и критичные факты ниже — цену клиенту только из блока ЦЕНА, критфакты дословно."
        )
    # ПАРК (Лист1): если allowlist реально получен — ЖЁСТКО ограничиваем модели, что бот называет
    # клиенту. Пусто/None (источник недоступен) → блока нет (FAIL-SAFE, ограничения не применяем).
    park_block = ""
    if park_models:
        park_block = (
            "\n\n★ ПАРК (СТРОГО): предлагай, перечисляй и упоминай клиенту ТОЛЬКО модели реального "
            "парка: " + ", ".join(park_models) + ". Любые ДРУГИЕ модели клиенту НЕ предлагай и НЕ "
            "называй — даже если они есть в справочнике/FAQ ниже или как «замена» (напр. PCX150/160, "
            "ADV150/160, Rebel300, XSR900, R7, CB650R — их НЕТ в парке). Этот список парка приоритетнее "
            "любых списков моделей из справочника."
        )
    # КНИГА ПРАВИЛ (playbook): СТРОГО НИЖЕ кап-цены и CRITICAL_FACTS — соблюдать, но НЕ отменяет их.
    # Пусто → блока нет (FAIL-SAFE, генерация цела).
    playbook_block = ""
    if (playbook or "").strip():
        playbook_block = (
            "\n\nКНИГА ПРАВИЛ (стиль/факты/запреты/выученные правки — СОБЛЮДАЙ; но она НЕ отменяет "
            "ценовую политику и критичные факты ВЫШЕ — те приоритетнее):\n" + playbook.strip()
        )
    # §243/6: СОБРАННОЕ по диалогу/вложениям — «уже получено, не переспрашивай». Пусто → блока нет.
    collected_block = collected_prompt_note(collected, lang) if collected else ""
    # УРОВЕНЬ НАПОРА — на уровне базовой установки (высокий приоритет, ВЫШЕ playbook «без давления»).
    pressure_block = _pressure_block(pressure if pressure is not None else SALES_PRESSURE)
    return (
        "Ты — менеджер проката мотобайков TurboBaby (Пхукет). По переписке с клиентом "
        f"составь ОДИН короткий, вежливый ответ на {lang_name} языке (язык клиента). "
        "Отвечай только на то, что ещё НЕ отвечено менеджером в диалоге; не повторяй уже "
        "сказанное; держи контекст сделки, но НИКОГДА не пересказывай его клиенту. Не "
        "выдумывай данные и наличие. Верни ТОЛЬКО текст ответа клиенту — без пояснений, "
        "без кавычек, без префиксов. НЕ начинай ответ с описания ситуации, стадии/этапа "
        "сделки или статуса клиента: никаких «Клиент готов…», «менеджер ещё не назвал "
        "цену», «сейчас Этап N», «Этап 1/2/3», «стадия…» — это ВНУТРЕННИЕ пометки для тебя, "
        "клиент их видеть НЕ должен. Ответ начинай СРАЗУ по сути (первый контакт → "
        "приветствие → суть; иначе → сразу суть)."
        + pressure_block
        + directive_block + park_block + collected_block + greet + policy + scenario + ANTI_LOOP_NOTE + price_block + "\n\n"
        + CRITICAL_FACTS + EXPERIENCE_SAFETY_RULE + APPROVAL_WHITELIST_RULE
        + AVAILABILITY_INVARIANT_RULE + GENERATION_DEFAULT_RULE + PICKUP_RULE + playbook_block
        + "\n\nFAQ и эталонные формулировки:\n" + (faq or "(FAQ недоступен — опирайся на критичные факты выше)")
        + STYLE_FEWSHOT
    )


def _default_llm(system: str, user: str) -> str:
    import anthropic
    c = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = c.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=LLM_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


REPO_ENV = os.path.join(BASE_DIR, ".env")   # .env репо по __file__ (НЕ по cwd/окружению)


def _env_file_claude_bin():
    """CLAUDE_BIN, прочитанный ПРЯМО из .env-файла репо (путь по __file__, не из окружения процесса).
    КОРЕНЬ БАГА: userbot запущен в СЕРВИС-контексте (services.exe→…→python) — %APPDATA% указывает на
    systemprofile, load_dotenv не подхватил .env, поэтому ни glob по %APPDATA%, ни env-var CLAUDE_BIN
    не срабатывали → «claude CLI не найден» ТОЛЬКО в бою. Чтение файла напрямую от окружения не зависит.
    Берём ТОЛЬКО строку CLAUDE_BIN (секреты не читаем/не логируем). → путь если существует, иначе ''."""
    try:
        # utf-8-sig: снимает BOM (PowerShell 5.1 `Set-Content -Encoding utf8` пишет его на первой
        # строке — иначе startswith мимо); errors=replace: одинокий не-utf8 байт на ЧУЖОЙ строке
        # не должен уронить парс до строки CLAUDE_BIN. Иначе фикс сервис-контекста тихо ломается.
        with open(REPO_ENV, encoding="utf-8-sig", errors="replace") as f:
            for ln in f:
                s = ln.strip()
                if s.startswith("CLAUDE_BIN=") and not s.startswith("#"):
                    val = s.split("=", 1)[1].strip().strip('"').strip("'")
                    if val and os.path.isabs(val) and os.path.isfile(val):
                        return val
    except Exception:
        pass
    return ""


def _claude_base_dirs():
    """Базовые папки версий claude-code. Включает:
    (1) обычные Roaming\\Claude\\claude-code по всем профилям — НО у Store/MSIX-установки Claude это
        ВИРТУАЛЬНЫЙ редирект (виден только в интерактивной сессии; процессы Планировщика/userbot
        получают FileNotFoundError → «claude CLI не найден в бою»);
    (2) РЕАЛЬНЫЙ путь MSIX-пакета AppData\\Local\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude\\
        claude-code — доступен из ЛЮБОГО контекста (это и есть фикс)."""
    out = []
    up = os.getenv("USERPROFILE")
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(up or home, "AppData", "Local")
    roots = []
    for r in (os.getenv("APPDATA"),
              (os.path.join(up, "AppData", "Roaming") if up else None),
              os.path.join(home, "AppData", "Roaming")):
        if r and r not in roots:
            roots.append(r)
            out.append(os.path.join(r, "Claude", "claude-code"))
    try:  # реальные MSIX-базы (Claude_<хэш-издателя> — wildcard, чтобы пережить смену пакета)
        for cc in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache",
                                         "Roaming", "Claude", "claude-code")):
            if cc not in out:
                out.append(cc)
    except Exception:
        pass
    return out


# Совместимость: часть кода ссылается на _CLAUDE_BASE (первый корень).
_CLAUDE_BASE = (_claude_base_dirs() or
                [os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Claude", "claude-code")])[0]


def _resolve_claude_once():
    """Один проход резолва (без ретраев). Порядок: PATH-шим → CLAUDE_BIN из окружения (если жив) →
    CLAUDE_BIN ПРЯМО из .env-файла (спасает сервис-контекст) → новейшая версия claude-code по ВСЕМ
    профилям → кандидаты иных схем установки. → путь|None."""
    w = shutil.which("claude")
    if w and os.path.isfile(w):
        return w
    if CLAUDE_BIN and os.path.isabs(CLAUDE_BIN) and os.path.isfile(CLAUDE_BIN):
        return CLAUDE_BIN
    envbin = _env_file_claude_bin()             # ← КЛЮЧ: не зависит от %APPDATA%/load_dotenv/cwd
    if envbin:
        return envbin
    try:
        cands = []
        for base in _claude_base_dirs():
            for d in glob.glob(os.path.join(base, "*")):
                exe = os.path.join(d, "claude.exe")
                if os.path.isfile(exe):
                    nums = re.findall(r"\d+", os.path.basename(d))
                    cands.append((tuple(int(n) for n in nums) if nums else (0,), exe))
        if cands:
            cands.sort()
            return cands[-1][1]
    except Exception:
        pass
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude.cmd"),
              os.path.join(local, "Programs", "claude", "claude.exe")):
        if os.path.isfile(c):
            return c
    return None


def _resolve_claude(retries=1, retry_sleep=2.0):
    """Версионно-независимый резолв claude CLI (порт pc_orchestrator.resolve_claude, коммит 45bdd75 —
    не импортируем pc_orchestrator, чтобы не тянуть его logging.basicConfig/Bridge в userbot).
    НЕ сдаёмся с первой осечки: транзиентный os.path.isfile()==False на 236-МБ claude.exe
    (AV-скан/локация файла — кейс теста 00:33 и задачи #35) не должен убивать черновик — короткий
    ретрай (2 попытки, пауза ~2с). → путь (str) | None (после ретраев — реально нигде нет)."""
    for i in range(retries + 1):
        p = _resolve_claude_once()
        if p:
            return p
        if i < retries:
            log.warning("SUGGEST: claude CLI не найден (проход %s/%s) — транзиент? повтор через %sс",
                        i + 1, retries + 1, retry_sleep)
            time.sleep(retry_sleep)
    return None


def _real_model_from_json(data: dict) -> str:
    """Реально отработавшая ГОЛОВА из modelUsage (--output-format json). CLI под капотом дёргает
    служебный haiku-помощник (заголовок сессии и т.п.) — у него всегда крошечный фикс-вход (~505 ткн),
    поэтому реальную голову опознаём по МАКСИМУ inputTokens, а НЕ по outputTokens: при фолбэке основная
    модель может дать МЕНЬШЕ output, чем служебный haiku (замер: sonnet=4 vs haiku=12) — по output лог
    соврал бы про модель. Нет modelUsage → ''."""
    mu = data.get("modelUsage") if isinstance(data, dict) else None
    if not isinstance(mu, dict) or not mu:
        return ""
    try:
        return max(mu.items(), key=lambda kv: (kv[1] or {}).get("inputTokens", 0))[0]
    except Exception:
        return ""


def _parse_cli_json(raw: str):
    """Разобрать --output-format json от claude CLI. → (text, real_model). text — поле result (готовый
    ответ клиенту), real_model — реально отработавшая голова (см. _real_model_from_json). Битый JSON или
    is_error → ('', real_model): upstream трактует пустой текст как 'пустой черновик' → пропуск."""
    try:
        data = json.loads(raw or "")
    except Exception:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    real = _real_model_from_json(data)
    if data.get("is_error"):
        return "", real
    return (data.get("result") or "").strip(), real


def _cli_llm(system: str, user: str) -> str:
    """Генерация через claude CLI по подписке Max (не платный API-ключ → нет 'credit balance too low').
    ЧИСТЫЙ генератор текста, НЕ агент: --allowed-tools '' + нейтральный cwd (НЕ репо) → CLI не читает
    .claude/settings.json+pretool_guard и не дёргает инструменты. ANTHROPIC_API_KEY вычищен из env,
    иначе CLI пошёл бы по платному ключу. Голова: SUGGEST_MODEL с кондуктором-фолбэком SUGGEST_MODEL_FALLBACK
    (--fallback-model — САМ CLI одним вызовом добивает фолбэком, свой ретрай не нужен). --output-format json:
    достаём поле result и реально отработавшую голову (modelUsage) в лог. Таймаут/ошибка → '' (upstream:
    'пустой черновик' → пропуск)."""
    cbin = _resolve_claude()
    if not cbin:
        raise RuntimeError("claude CLI не найден (PATH/CLAUDE_BIN/AppData) — генерация через CLI невозможна")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # ← ключ НЕ утекает: CLI идёт по подписке Max
    env.pop("OPENAI_API_KEY", None)
    cmd = [cbin, "-p", user, "--system-prompt", system, "--model", SUGGEST_MODEL,
           "--output-format", "json", "--allowed-tools", ""]
    if SUGGEST_MODEL_FALLBACK:                   # кондуктор: фолбэк исполняет сам CLI в этом же вызове
        cmd += ["--fallback-model", SUGGEST_MODEL_FALLBACK]
    try:
        p = subprocess.run(
            cmd,
            cwd=tempfile.gettempdir(),          # нейтральный cwd: без settings.json/pretool_guard репо
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.warning("SUGGEST: claude CLI таймаут %sс — пустой черновик", CLI_TIMEOUT)
        return ""
    if p.returncode != 0:
        log.warning("SUGGEST: claude CLI rc=%s — %s", p.returncode, (p.stderr or "").strip()[-300:])
        return ""
    text, real = _parse_cli_json(p.stdout or "")
    log.info("SUGGEST: голова=%s (просили %s, фолбэк %s)",
             real or "?", SUGGEST_MODEL, SUGGEST_MODEL_FALLBACK or "—")
    return text


# --- защита от утечки служебного контекста в тело черновика -------------------
# LLM иногда предваряет ответ ВНУТРЕННЕЙ пометкой о стадии сделки («Клиент готов…,
# менеджер ещё не назвал цену — сейчас Этап 1»). Промпт это запрещает (см.
# make_system_prompt), но как дешёвая страховка срезаем ВЕДУЩИЙ служебный сегмент.
# Консервативно: трогаем ТОЛЬКО начало и только при явных внутренних маркерах —
# приветствие/цена/суть (без этих маркеров) не затрагиваются; пусто не возвращаем.
_SERVICE_MARKERS = re.compile(
    r"(?:сейчас\s+этап|это\s+этап|\bэтап\s*№?\s*\d|клиент\s+(?:сейчас\s+)?готов\s+брониров|"
    r"менеджер\s+(?:ещё|еще)?\s*не\s+назвал|(?:внутренн\w*\s+)?контекст\s+сделк|"
    r"стади\w*\s+сделк|служебн\w*\s+пометк|\[\s*внутренн)",
    re.IGNORECASE,
)


def _strip_service_prefix(text: str) -> str:
    """Срезать ведущую служебную строку/предложение о стадии сделки, если LLM его всё же
    вставил в начало. НИКОГДА не возвращает пусто при непустом входе (иначе потеряли бы
    весь ответ — лучше отдать как есть, модератор увидит и отклонит)."""
    t = (text or "").strip()
    if not t:
        return t
    for _ in range(3):                      # до 3 ведущих служебных сегментов подряд
        nl = t.find("\n")
        first = t if nl < 0 else t[:nl]
        if not _SERVICE_MARKERS.search(first):
            break                           # ведущего служебного маркера нет — стоп
        if nl >= 0:                         # служебное отдельной строкой → срезаем строку
            rest = t[nl + 1:].strip()
        else:                               # служебное и ответ на ОДНОЙ строке → режем по концу предложения
            m = re.search(r"[.!?…]\s+(?=\S)", first)
            rest = first[m.end():].strip() if m else ""
        if not rest:                        # осмысленного продолжения нет — не вычищаем в пусто
            return t
        t = rest
    return t


# --- ПОСТ-ЧЕК ЧЕРНОВИКА: утверждения ВНЕ белого списка → «уточню»-форма ----------
# Шаг 4/7 (родитель #243). Промпт УЖЕ запрещает утверждать цвет/наличие/цену вне данных
# (APPROVAL_WHITELIST_RULE), но LLM 12.07 всё равно выдавал живые провалы: «выбора по цвету, к
# сожалению, нет», «светлого ADV 350 сейчас нет — есть чёрный», «9000 бат за 10 дней». Пост-чек —
# детерминированная СТРАХОВКА поверх промпта: сканируем ГОТОВЫЙ черновик по сегментам, клеймим
# ЯВНЫЕ нарушения кодом (цвет — любое утверждение о цвете; наличие — «сейчас нет / занят / свободен
# на …»; цена — ИТОГ «<N> бат за <M> дней», которого нет в белом источнике цен), спорные («есть
# ADV 350» — парк-перечень ИЛИ наличие-на-даты?) отдаём LLM-тайбрейку. Нарушение → сегмент
# переписываем в «Уточню у команды и вернусь», а в КОНЦЕ добавляем пометку модератору
# «[уточнить: …]» (её _strip_service_prefix не режет — маркер «[внутренн», не «[уточнить»).
# FAIL-SAFE: нарушений нет → возвращаем ВХОД байт-в-байт; LLM-тайбрейк недоступен/спорно → НЕ трогаем.

# Цвет: клиенту УТВЕРЖДАТЬ нельзя вообще (не в белом списке) → любой цвет-токен = нарушение.
# \b перед основами, чтобы «прекрасный» (⊃ «красн») и т.п. не ловились.
_PC_COLOR_RE = re.compile(
    r"\bцвет\w*|\bcolou?rs?\b|"
    r"\bч[ёе]рн(?:ый|ого|ому|ым|ом|ая|ой|ую|ое|ые|ых|еньк\w*)|"
    r"\bбел(?:ый|ого|ая|ое|ые|ым|ой|еньк\w*)|\bсветл\w*|\bтёмн\w*|\bтемн\w*|"
    r"\bкрасн(?:ый|ого|ому|ым|ом|ая|ой|ую|ое|ые|ых|еньк\w*)|\bсин(?:ий|его|яя|юю|ее|им\w*|их)|"
    r"\bголуб(?:ой|ого|ому|ым|ом|ая|ой|ую|ое|ые|ых)|\bзелён\w*|\bзелен(?:ый|ого|ому|ым|ом|ая|ой|ую|ое|ые|ых)|"
    r"\bсеребр\w*|\bсер(?:ый|ого|ая|ое|ые|ым|ой)|\bзолот\w*|\bоранж\w*|\bжёлт\w*|\bжелт\w*|"
    r"\bкорич\w*|\bфиолет\w*|\bрозов\w*|\bбордов\w*|"
    r"\bblack\b|\bwhite\b|\bred\b|\bblue\b|\bgreen\b|\bsilver\b|\bgold(?:en)?\b|\borange\b|\byellow\b|\bbrown\b",
    re.I)

# Наличие КОНКРЕТНОГО байка на даты/сейчас — вне белого списка. Ловим явные состояния склада
# (не «есть <модель>» вообще — это парк-перечень, он РАЗРЕШЕН; такой спорный случай → LLM-тайбрейк).
_PC_AVAIL_RE = re.compile(
    r"сейчас\s+нет|уже\s+нет|пока\s+нет|нет\s+в\s+нали\w+|не\s+в\s+нали\w+|нету\b|"
    r"не\s+остал\w+|разобрал\w*|\bзанят\w*|недоступ\w*|нет\s+свободн\w*|осталось\s+\d|"
    r"свободен\s+на\b|свободна\s+на\b|доступен\s+на\b|доступна\s+на\b|в\s+наличии\s+на\b|"
    r"есть\s+в\s+нали\w+|в\s+наличии\s+есть|"
    r"sold\s*out|not\s+available|unavailable|out\s+of\s+stock|already\s+booked|in\s+stock",
    re.I)

# Цена/депозит клиенту — ТОЛЬКО дословно из белого источника (quote/сетка). Разбор денежных чисел
# черновика (итог-за-период / суточная ставка / депозит) делаем ЕДИНОЙ функцией extract_money_figures
# (ниже): она отделяет суточную ставку «449฿/день» (kind='rate', НЕ клеймим) от итога-за-срок
# (kind='total') и депозита, распознаёт срок при любой формулировке («за/на/—/for N дней», «N дней:»).
# Клеймление чисел вне белого источника — в _pc_classify. Прежний узкий _PC_PRICE_TOTAL_RE («за» без
# «на»/«—»/«for») пропускал живой итог окна 504608015 «на 5 дней — 2 245 ฿» — заменён этим разбором
# (#310: гард guard_quote_price свёрнут в пост-чек, точка правды разбора денег одна).

# Разбиение на сегменты С СОХРАНЕНИЕМ разделителей (точный round-trip): границы предложений и
# переводы строк. Тире «—» границей НЕ считаем — «светлого ADV 350 сейчас нет — есть чёрный»
# должно остаться ОДНИМ сегментом и клеймиться целиком.
_PC_SPLIT_RE = re.compile(r'([.!?…]+["»)\]]?\s+|\n+)')


def _pc_num(s):
    """Число из строки-денежного-токена → int (снимаем пробелы/разделители тысяч) или None."""
    d = re.sub(r"\D", "", str(s or ""))
    return int(d) if d else None


def _pc_wl_price_numbers(pricing_note: str):
    """Белый источник цен — множество чисел из блока ЦЕНА/ПРАЙС (quote/сетка). Итог в черновике,
    совпавший с этим множеством, считаем законным (пришёл из Календаря). Пусто → пустое множество
    (тогда ЛЮБОЙ итог-за-период = утверждение вне данных)."""
    return {v for tok in re.findall(r"\d[\d.,\s]*\d|\d", pricing_note or "")
            for v in (_pc_num(tok),) if v is not None}


# --- Разбор денежных чисел из ТЕКСТА ответа бота (шаг 2/5 родитель #310) --------------------
# extract_money_figures: достаёт ВСЕ денежные числа ответа с грубой классификацией — суточная
# ставка «฿/день» (rate), итог за период (total), депозит (deposit), прочая денежная сумма
# (amount). Единая точка правды разбора денег: питает пост-чек черновика (_pc_classify клеймит
# total/deposit вне белого источника). Живой провал окна 504608015 «NMAX 155 на 5 дней — 2 245 ฿
# (449 ฿/день)» → total=2245, rate=449; при этом «155» (модель) и «5» (срок) деньгами НЕ считаются.
# Терпима к форматам «2 245 ฿», «449฿/день», «3000», «депозит 3000 бат», «1685 THB», «337 THB/day».
# Каждый фиг несёт has_cur — был ли валютный токен рядом с числом (пост-чек клеймит total/deposit
# только с валютой, отсекая счётчики вроде «на 5 дней для 2 гостей»).
_MF_CUR = r"(?:฿|бат\w*|baht\w*|thb)"
_MF_NUM = r"\d[\d\s .,]*\d|\d"
_MF_SCAN_RE = re.compile(r"(?<![\d.,])(" + _MF_NUM + r")\s*(" + _MF_CUR + r")?", re.I)
# ставка «/день»: между числом и «день» ОБЯЗАТЕЛЕН коннектор (/ | в | за | per | a) — иначе
# «5 дней» (счётчик срока) ложно попал бы в суточную ставку.
_MF_RATE_TAIL = re.compile(
    r"^\s*(?:/\s*|за\s+|в\s+|per\s+|a\s+)(?:฿|бат\w*|baht\w*|thb)?\s*(?:день|дн\w*|сут\w*|day)\b",
    re.I)
# Период-фраза «N дней/недель/месяцев». Предлог за/на/for/в — НЕОБЯЗАТЕЛЕН: живые итоги-за-срок
# пишут по-разному — «за 5 дней», «на 5 дней», «— 5 дней», «for 5 days», «5 дней:». Узкое «за»
# пропускало живой провал окна 504608015 «на 5 дней — 2 245 ฿». Падежи покрыты дн\w*/недел\w*/мес\w*.
_MF_PERIOD = re.compile(
    r"(?:за|на|for|в)?\s*\d{1,3}\s*(?:дн\w*|день|сут\w*|недел\w*|нед\b|мес\w*|months?|weeks?|days?)",
    re.I)
_MF_DEPOSIT = re.compile(r"депозит|залог|deposit", re.I)
_MF_TOTAL_KW = re.compile(r"итог\w*|всего|total|сумм\w*", re.I)
_MF_MODEL_TAIL = re.compile(
    r"(?:nmax|adv|pcx|click|forza|xmax|vario|aerox|filano|scoopy|grand|fino|burgman|lead|zoomer)"
    r"\s*$", re.I)
_MF_UNIT_HEAD = re.compile(
    r"^\s*(?:cc\b|куб\w*|км\b|km\b|%|час\w*|год\b|лет\b|шт\b|дн\w*|день|сут\w*|недел\w*|нед\b|"
    r"мес\w*|day|week|month)", re.I)


def extract_money_figures(text: str):
    """Денежные числа из текста ответа → список dict(kind, value, raw) в порядке появления.
    kind ∈ {'rate','deposit','total','amount'}. Модельные числа (NMAX 155) и счётчики срока
    (5 дней) деньгами НЕ считаются. value — int (пробелы/разделители тысяч сняты)."""
    s = text or ""
    out = []
    for m in _MF_SCAN_RE.finditer(s):
        value = _pc_num(m.group(1))
        if value is None:
            continue
        has_cur = bool(m.group(2))
        before = s[max(0, m.start(1) - 28):m.start(1)]
        after = s[m.end():m.end() + 18]
        is_rate = bool(_MF_RATE_TAIL.search(after))
        near = before.lower() + " " + after.lower()
        is_deposit = bool(_MF_DEPOSIT.search(near))
        is_total = bool(_MF_PERIOD.search(near) or _MF_TOTAL_KW.search(before.lower()))
        # без валюты и вне явного денежного контекста — отсекаем модельные числа (NMAX 155)
        # и единицы/счётчики (5 дней, 155 cc); голое число оставляем только если оно похоже
        # на цену (итог-за-период ИЛИ >=1000).
        if not has_cur and not is_rate and not is_deposit:
            if _MF_MODEL_TAIL.search(before) or _MF_UNIT_HEAD.match(after):
                continue
            if not is_total and value < 1000:
                continue
        if is_rate:
            kind = "rate"
        elif is_deposit:
            kind = "deposit"
        elif is_total:
            kind = "total"
        else:
            kind = "amount"
        out.append({"kind": kind, "value": value, "raw": m.group(0).strip(), "has_cur": has_cur})
    return out


def _pc_segments(text: str):
    """Список [сегмент, разделитель] с точным round-trip: ''.join(s+p) == text."""
    parts = _PC_SPLIT_RE.split(text or "")
    return [[parts[i], parts[i + 1] if i + 1 < len(parts) else ""] for i in range(0, len(parts), 2)]


def _pc_is_ask_form(low: str) -> bool:
    """Сегмент уже в «уточню»-форме / это пометка модератору → повторно не клеймим."""
    return ("уточн" in low or "[уточнить" in low
            or "check with the team" in low or "get back to you" in low)


def _pc_maybe(low: str, model) -> bool:
    """Спорный случай для LLM-тайбрейка: назван КОНКРЕТНЫЙ байк + слово владения/готовности, но
    без явного склад-состояния (детерминированные RE выше не сработали). «Есть ADV 350» — это
    парк-перечень (разрешено) ИЛИ наличие-на-даты (запрещено)? Решает LLM."""
    return bool(model) and bool(
        re.search(r"\bесть\b|имеется|доступ\w*|свободн\w*|в\s+наличии|привез\w*|\bготов\w*", low))


def _pc_llm_tiebreak(seg: str, transcript, call_llm):
    """LLM-тайбрейк спорного сегмента. → 'color'|'avail'|'price' (нарушение) или None (чисто/сбой).
    FAIL-SAFE: любая ошибка/непарс → None (не переписываем на сомнении)."""
    sys = (
        "Ты контролёр черновика менеджера аренды. Менеджеру НЕЛЬЗЯ УТВЕРЖДАТЬ клиенту как факт: "
        "(1) цвет байка; (2) наличие КОНКРЕТНОГО байка на даты/сейчас; (3) точную цену/итог вне "
        "прайса. РАЗРЕШЕНО: перечень моделей парка, депозит-правило, минимальные сроки, цены "
        "дословно из прайса. Верни СТРОГО JSON без пояснений: "
        "{\"violation\": true|false, \"kind\": \"color|avail|price|none\"}.")
    try:
        raw = call_llm(sys, "Фраза менеджера: " + (seg or ""))
        m = re.search(r"\{.*\}", raw or "", re.S)
        data = json.loads(m.group(0)) if m else {}
        if data.get("violation"):
            k = str(data.get("kind", "")).lower()
            return k if k in ("color", "avail", "price") else "avail"
    except Exception:
        return None
    return None


def _pc_classify(seg: str, allowed, call_llm=None, transcript=None):
    """Нарушения в сегменте → список (kind, mention). Пусто = сегмент чист.
    Детерминированно: цвет/наличие/итог-цена-вне-данных; спорное → LLM-тайбрейк (если дан call_llm)."""
    low = (seg or "").lower()
    if not low.strip() or _pc_is_ask_form(low):
        return []
    models = _detect_models(low)
    model = models[0] if models else None
    found = []
    if _PC_COLOR_RE.search(seg):
        found.append(("color", model))
    if _PC_AVAIL_RE.search(low):
        found.append(("avail", model))
    # Цена/депозит: разбор чисел делегируем extract_money_figures (единая точка правды разбора денег,
    # #310 — сюда свёрнут гард guard_quote_price). Клеймим ИТОГ-за-период (kind='total') и ДЕПОЗИТ с
    # валютным токеном, чьё число вне белого источника; суточную ставку («449฿/день» → kind='rate') и
    # голые немонетарные числа НЕ трогаем. Итог ловится при любой формулировке срока («за/на/—/for N
    # дней», «N дней:») — _MF_PERIOD распознаёт период без обязательного предлога. Валютный токен рядом
    # обязателен (has_cur) — как в прежнем _PC_PRICE_TOTAL_RE: отсекает счётчики («на 5 дней для 2
    # гостей»). Депозит сверяем ТОЛЬКО при непустом белом источнике (есть live-quote), как прежде.
    for fig in extract_money_figures(seg):
        if not fig.get("has_cur"):
            continue
        if fig["kind"] == "total" and fig["value"] not in allowed:
            found.append(("price", str(fig["value"])))
        elif fig["kind"] == "deposit" and allowed and fig["value"] not in allowed:
            found.append(("price", str(fig["value"])))
    if found:
        return found
    if call_llm and _pc_maybe(low, model):
        k = _pc_llm_tiebreak(seg, transcript, call_llm)
        if k:
            return [(k, model if k != "price" else None)]
    return []


def _pc_manager_note(issues, en: bool) -> str:
    """Пометка модератору «[уточнить: …]» из накопленных нарушений (для клиента её не видно —
    _strip_service_prefix не режет, а модератор уточнит и правит перед одобрением)."""
    label = {"color": "цвет" if not en else "colour",
             "avail": "наличие" if not en else "availability",
             "price": "цена" if not en else "price"}
    parts = []
    for kind in ("color", "avail", "price"):
        ms = [m for k, m in issues if k == kind]
        if not ms:
            continue
        uniq = []
        for m in ms:
            if m and m not in uniq:
                uniq.append(m)
        parts.append(label[kind] + ((" " + " ".join(uniq)) if uniq else ""))
    return ("[уточнить: " + "; ".join(parts) + "]") if parts else ""


def postcheck_draft(draft: str, lang: str = "ru", pricing_note: str = "",
                    call_llm=None, transcript: str = None) -> str:
    """Пост-чек ГОТОВОГО черновика: утверждения о цвете/наличии/цене ВНЕ белого списка → «уточню».
    Нарушений нет → вход возвращается БАЙТ-В-БАЙТ (fail-safe). Сегмент-нарушение переписываем в
    «Уточню у команды и вернусь», в конце — пометка модератору «[уточнить: …]»."""
    text = draft or ""
    if not text.strip():
        return draft
    allowed = _pc_wl_price_numbers(pricing_note)
    en = (lang == "en")
    ask = "Let me check with the team and get back to you." if en else "Уточню у команды и вернусь."
    issues, out, changed, prev_ask = [], [], False, False
    for seg, sep in _pc_segments(text):
        hits = _pc_classify(seg, allowed, call_llm=call_llm, transcript=transcript)
        if not hits:
            out.append([seg, sep])
            prev_ask = False
            continue
        changed = True
        issues.extend(hits)
        if prev_ask:                     # схлопываем подряд идущие «уточню» в одно
            continue
        newsep = "\n" if "\n" in sep else (" " if sep else "")
        out.append([ask, newsep])
        prev_ask = True
    if not changed:
        return draft
    body = "".join(s + p for s, p in out).strip()
    note = _pc_manager_note(issues, en)
    return (body + "\n" + note) if note else body


# --- ПОСТ-ЧЕК ЗАБОРА: бесплатный забор ТОЛЬКО при оплаченной доставке (шаг 5/7 #22) ----------
# Пара к PICKUP_RULE (детерминированная страховка поверх промпта). «Забор байка в конце аренды —
# бесплатный» — это перк ОПЛАЧЕННОЙ доставки (мы привезли — мы же бесплатно забрали). Без доставки
# (клиент забирает сам / самовывоз / «точка без доставки») бесплатного забора НЕТ: обратный забор
# стоит как доставка той же зоны. Самопротиворечие «заберём бесплатно» + «заберёте сами / самовывоз /
# без доставки» в одном черновике ЗАПРЕЩЕНО. Пост-чек снимает ОБЕЩАНИЕ бесплатного забора, если в
# черновике есть сигнал самовывоза ЛИБО delivery_paid явно False. Нет обещания / доставка оплачена
# (и самовывоза в тексте нет) → вход БАЙТ-В-БАЙТ (fail-safe).
# ВАЖНО: КОД-строку доставки (compose_delivery_draft, «Доставка — N ฿ (забор … бесплатный)») этот чек
# НЕ трогает — он идёт по LLM-тексту ДО приклейки dblock, где бесплатный забор при ОПЛАЧЕННОЙ доставке
# легитимен; сам dblock КОД добавляет только при резолвнутой (то есть оплачиваемой) зоне.

# Обещание БЕСПЛАТНОГО ЗАБОРА (в любом порядке слов): «забор … бесплатный» / «бесплатный … забор» /
# EN «pickup … free» / «free … pickup». Границы клаузы — без точек/переводов строки внутри.
# Основа «забор/забрать» в любой форме: «забор», «заберём/заберёте», «забираем» — но НЕ «забота»/«забыть».
_FP_TAKE = r"(?:забор\w*|забер[её]\w*|забир\w*)"
_FP_FREE_RE = re.compile(
    _FP_TAKE + r"[^.\n]*?бесплатн\w*"
    r"|бесплатн\w*[^.\n]*?" + _FP_TAKE +
    r"|(?:bike\s+)?pick[\s-]?up\w*[^.\n]*?\bfree\b"
    r"|\bfree\b[^.\n]*?pick[\s-]?up\w*", re.I)

# Сигнал САМОВЫВОЗА / «точки без доставки»: клиент забирает-сдаёт байк сам, доставку не берёт.
_FP_SELF_RE = re.compile(
    r"самовывоз\w*|забер[её]те\s+сам\w*|забер[её]шь\s+сам\w*|забира\w*\s+сам\w*"
    r"|приед\w*\s+сам\w*|приед\w*\s+за\s+байк\w*|за\s+байком\s+сам\w*|без\s+доставк\w*"
    r"|self[\s-]?pick[\s-]?up|pick[\s-]?(?:it\s+)?up\s+(?:it\s+)?yourself"
    r"|collect[\s-]?(?:it\s+)?yourself|come\s+(?:and\s+)?(?:pick|get)", re.I)

# Клауза-обещание внутри сегмента, которую можно ВЫРЕЗАТЬ, оставив остальное: (а) в скобках
# «(забор … бесплатный)» либо (б) хвост после запятой/тире «, забор … бесплатный».
_FP_CLAUSE_RE = re.compile(
    r"[ \t]*[（(][^()（）\n]*?(?:" + _FP_TAKE + r"[^()（）\n]*?бесплатн\w*"
    r"|бесплатн\w*[^()（）\n]*?" + _FP_TAKE + r"|pick[\s-]?up\w*[^()（）\n]*?free|free[^()（）\n]*?pick[\s-]?up\w*)"
    r"[^()（）\n]*?[)）]"
    r"|[ \t]*[,;—–-]+[ \t]*(?:" + _FP_TAKE + r"[^.\n]*?бесплатн\w*|бесплатн\w*[^.\n]*?" + _FP_TAKE +
    r"|(?:bike\s+)?pick[\s-]?up\w*[^.\n]*?\bfree\b|\bfree\b[^.\n]*?pick[\s-]?up\w*)",
    re.I)


def postcheck_free_pickup(draft: str, lang: str = "ru", delivery_paid=None) -> str:
    """Пост-чек черновика на противоречие ЗАБОРА (шаг 5/7 #22): бесплатный забор в конце аренды —
    ТОЛЬКО при ОПЛАЧЕННОЙ доставке. Обещание бесплатного забора снимаем, если в черновике есть сигнал
    САМОВЫВОЗА (заберёте сами / самовывоз / без доставки) ЛИБО delivery_paid явно False. Иначе вход
    возвращается БАЙТ-В-БАЙТ (fail-safe): нет обещания, либо доставка оплачена и самовывоза нет."""
    text = draft or ""
    if not text.strip() or not _FP_FREE_RE.search(text):
        return draft
    # Снимаем обещание ТОЛЬКО при противоречии: самовывоз в тексте ИЛИ доставка явно НЕ оплачена.
    contradiction = bool(_FP_SELF_RE.search(text)) or (delivery_paid is False)
    if not contradiction:
        return draft
    out, changed = [], False
    for seg, sep in _pc_segments(text):
        if not _FP_FREE_RE.search(seg):
            out.append([seg, sep])
            continue
        cleaned = _FP_CLAUSE_RE.sub("", seg)            # вырезаем клаузу-обещание, оставляя остальное
        cleaned = re.sub(r"[（(]\s*[)）]", "", cleaned)   # осевшие пустые скобки
        cleaned = re.sub(r"[ \t]*[,;—–-]+[ \t]*$", "", cleaned.rstrip())  # хвостовой разделитель
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        if _FP_FREE_RE.search(cleaned) or not cleaned.strip():
            # клауза не выделилась ⇒ сегмент — само обещание целиком: выбрасываем его (и разделитель)
            changed = True
            continue
        if cleaned != seg:
            changed = True
        out.append([cleaned, sep])
    if not changed:
        return draft
    body = "".join(s + p for s, p in out).strip()
    return body if body else draft


def _append_collected_note(draft: str, facts: dict, lang: str = "ru") -> str:
    """§243/6: дописать в хвост черновика СЛУЖЕБНУЮ пометку модератору «[собрано: гео ✅ …]».
    Ничего не собрано → черновик БАЙТ-В-БАЙТ (fail-safe). Хвост → _strip_service_prefix не режет."""
    note = collected_manager_note(facts, lang)
    if not note:
        return draft
    return ((draft or "").rstrip() + "\n" + note) if (draft or "").strip() else draft


def _season_service_note(pricing_note: str) -> str:
    """Сезонная СЛУЖЕБНАЯ пометка «[сезон: …]» из служебных скобок pricing_note → текст | ''
    (нет низкого сезона / не sheet-режим)."""
    m = _SEASON_BLOCK_RE.search(pricing_note or "")
    return m.group(1).strip() if m else ""


def _append_season_note(draft: str, pricing_note: str) -> str:
    """Шаг 4/7 #253: дописать в хвост черновика СЛУЖЕБНУЮ сезонную пометку «[сезон: …]» (для
    модератора; client_facing_text её срезает — в клиентском теле сезонности нет). Нет пометки →
    черновик БАЙТ-В-БАЙТ (fail-safe)."""
    note = _season_service_note(pricing_note)
    if not note:
        return draft
    return ((draft or "").rstrip() + "\n" + note) if (draft or "").strip() else draft


# Служебные пометки модератору в теле черновика — квадратные скобки с известным маркером,
# КАЖДАЯ отдельной строкой в хвосте ([уточнить: …]/[собрано: …]/[сезон: …] и EN-аналоги). Клиент
# их видеть не должен: client_facing_text срезает такие строки перед отправкой (шаг 4/7 #253).
_SERVICE_NOTE_LINE_RE = re.compile(
    r"^[ \t]*\[(?:уточнить|собрано|collected|сезон|season)\b[^\n]*\][ \t]*$", re.I | re.M)


def client_facing_text(draft: str) -> str:
    """Текст, который увидит КЛИЕНТ: черновик БЕЗ служебных пометок модератору
    ([уточнить: …]/[собрано: …]/[сезон: …]). Пометки живут в служебной части карточки, не в теле
    ответа. Пустой вход → как есть (fail-safe)."""
    if not (draft or "").strip():
        return draft
    out = _SERVICE_NOTE_LINE_RE.sub("", draft)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# ===================== ГАРД НАЛИЧИЯ/ДЕФИЦИТА/ОСОБЫХ УСЛОВИЙ (родитель4, шаг 4/6) =====================
# Пара к AVAILABILITY_INVARIANT_RULE: детерминированная страховка ПЕРЕД отправкой, по образцу удалённого
# guard_quote_price (#310) — та же форма (сверка текста ↔ данные → перегенерация → фолбэк). Инвариант:
# бот НЕ утверждает наличие/занятость конкретного байка, дефицит/срочность и особые/персональные условия
# БЕЗ данных. Данные о наличии — только из Bridge (quote.available / quote_for_model.status); дефицита и
# спецусловий у бота источника НЕТ вовсе → такие клеймы нарушают инвариант ВСЕГДА. Нарушение → ответ НЕ
# уходит: лог (окно/модель/даты/клеймы) + перегенерация с жёсткой директивой (regenerate(directive)); после
# N неудач — безопасный фолбэк «уточню наличие и вернусь». Нет клеймов наличия → черновик байт-в-байт.
# Пост-чек postcheck_draft клеймит наличие переписыванием сегмента в «уточню» (мягко, для модератора);
# этот гард — жёсткий пояс с перегенерацией/фолбэком, дополняет пост-чек, а не дублирует его.

# Наличие ОТРИЦАЕТСЯ (занят/нет/разобрали) — состояние склада, вне данных без Bridge.
_AV_NEG_RE = re.compile(
    r"сейчас\s+нет|уже\s+нет|пока\s+нет|нет\s+в\s+нали\w+|не\s+в\s+нали\w+|нету\b|"
    r"не\s+остал\w+|разобран\w*|\bзанят\w*|недоступ\w*|нет\s+свободн\w*|"
    r"sold\s*out|not\s+available|unavailable|out\s+of\s+stock|already\s+booked", re.I)
# Наличие УТВЕРЖДАЕТСЯ (свободен/в наличии/доступен на даты) — тоже вне данных без Bridge.
_AV_POS_RE = re.compile(
    r"есть\s+в\s+нали\w+|в\s+нали\w+\b|свободен\b|свободна\b|доступен\b|доступна\b|"
    r"\bavailable\b|in\s+stock", re.I)
# ДЕФИЦИТ/СРОЧНОСТЬ — источника данных НЕТ, клейм запрещён ВСЕГДА.
_AV_SCARCITY_RE = re.compile(
    r"послед\w+\s+(?:байк\w*|штук\w*|шт\b|один|одна|экземпляр\w*)|остал\w+\s+(?:один|одна|1\b)|"
    r"осталось\s+\d|мало\s+остал\w+|почти\s+(?:всё|все)\s+разобрал\w*|разбира\w+|успева\w+|"
    r"успей\w*|спеши\w+|торопи\w+|только\s+сегодня\b|"
    r"last\s+one|only\s+\d+\s+left|almost\s+gone|selling\s+fast|hurry|going\s+fast|only\s+today", re.I)
# ОСОБЫЕ/ПЕРСОНАЛЬНЫЕ УСЛОВИЯ и спеццены — назначает менеджер, у бота источника НЕТ, запрещено ВСЕГДА.
_AV_SPECIAL_RE = re.compile(
    r"особ\w+\s+услови\w+|специальн\w+\s+(?:услови\w+|предложени\w+|цен\w+)|"
    r"персональн\w+\s+(?:скидк\w+|цен\w+|услови\w+)|скидк\w+\s+(?:специально\s+)?(?:для\s+вас|лично\s+вам)|"
    r"только\s+для\s+вас|эксклюзив\w*|"
    r"special\s+(?:offer|deal|conditions?|price|discount)|just\s+for\s+you|exclusive\b|personal\s+discount",
    re.I)


def availability_claims(draft: str) -> list:
    """Клеймы наличия/дефицита/особых условий в ТЕКСТЕ ответа → список dict(kind, raw) в порядке
    появления. kind ∈ {'avail_pos','avail_neg','scarcity','special'}. avail_pos не считаем внутри
    отрицания («нет в наличии» = только avail_neg, не avail_pos). Пусто → клеймов нет."""
    s = draft or ""
    claims, neg_spans = [], []
    for m in _AV_NEG_RE.finditer(s):
        neg_spans.append((m.start(), m.end()))
        claims.append({"kind": "avail_neg", "raw": m.group(0).strip()})
    for m in _AV_POS_RE.finditer(s):
        span = (m.start(), m.end())
        if any(span[0] < e and s0 < span[1] for s0, e in neg_spans):
            continue                                   # «в наличии» внутри «нет в наличии» — уже avail_neg
        pre = s[max(0, m.start() - 6):m.start()].lower()
        if re.search(r"\b(?:не|нет)\s*$", pre):        # непосредственно отрицаемое «свободен» и т.п.
            continue
        claims.append({"kind": "avail_pos", "raw": m.group(0).strip()})
    for m in _AV_SCARCITY_RE.finditer(s):
        claims.append({"kind": "scarcity", "raw": m.group(0).strip()})
    for m in _AV_SPECIAL_RE.finditer(s):
        claims.append({"kind": "special", "raw": m.group(0).strip()})
    return claims


def availability_state(avail):
    """Состояние наличия из данных Bridge → True (свободен) / False (занят) / None (данных нет).
    Принимает bool, quote-dict (поле available), результат quote_for_model ({status}). Дефицит и
    особые условия НИКОГДА не подтверждаются данными — их обрабатывает availability_violations."""
    if isinstance(avail, bool):
        return avail
    if isinstance(avail, dict):
        if isinstance(avail.get("available"), bool):
            return avail["available"]
        st = avail.get("status")
        if st == "ok":
            return True
        if st == "none_available":
            return False
    return None


def _av_unsupported(claims, state) -> list:
    """Клеймы, НЕ подтверждённые данными: avail_pos ок только при state=True, avail_neg только при
    state=False; scarcity/special — данных-источника нет → всегда нарушение."""
    bad = []
    for c in claims:
        if c["kind"] == "avail_pos" and state is True:
            continue
        if c["kind"] == "avail_neg" and state is False:
            continue
        bad.append(c)
    return bad


def availability_violations(draft: str, avail=None) -> list:
    """Клеймы наличия/дефицита/особых условий из текста, НЕ подтверждённые данными Bridge (avail).
    Пусто = черновик согласован (клеймов нет ИЛИ наличие точно подтверждено данными)."""
    return _av_unsupported(availability_claims(draft), availability_state(avail))


def _availability_hard_directive(lang: str = "ru") -> str:
    """Директива-верхнего-уровня для перегенерации: запрет утверждать наличие/дефицит/особые условия
    без данных, вместо этого — «уточню наличие и вернусь». В regenerate_draft(directive=…)."""
    if lang == "en":
        return ("Do NOT assert availability of a specific bike (\"free\"/\"in stock\", "
                "\"booked\"/\"not available\"), do NOT create scarcity or urgency (\"last one\", "
                "\"almost gone\", \"hurry\", \"only today\") and do NOT promise any special or "
                "personal conditions, prices or discounts — there is NO data for any of that. "
                "Instead say you will check availability for the client's dates/model with the team "
                "and get back. Write no other availability, scarcity or special-offer claims.")
    return ("НЕ утверждай наличие конкретного байка («свободен»/«в наличии», «занят»/«нет в "
            "наличии»), НЕ создавай дефицит и срочность («последний», «почти разобрали», "
            "«успевайте», «только сегодня») и НЕ обещай никакие особые/персональные условия, "
            "спеццены или скидки — данных на это НЕТ. Вместо этого напиши, что уточнишь наличие по "
            "датам/модели у команды и вернёшься. Других утверждений о наличии, дефиците или "
            "спецпредложениях не пиши.")


def availability_fallback(model=None, ds=None, de=None, lang: str = "ru") -> str:
    """Детерминированный безопасный фолбэк, если перегенерация не смогла: обещаем проверить наличие
    и вернуться, без утверждений о складе/дефиците/скидках. Сам не содержит клеймов наличия."""
    who = (" " + str(model)) if model else ""
    if lang == "en":
        return f"Let me check availability{who} for your dates with the team and get back to you."
    return f"Уточню наличие{who} по вашим датам у команды и вернусь."


def guard_availability(draft: str, avail=None, model=None, ds=None, de=None, lang: str = "ru",
                       regenerate=None, max_retries: int = 2, window=None) -> dict:
    """Гард ПЕРЕД отправкой: черновик не должен УТВЕРЖДАТЬ наличие/дефицит/особые условия без данных.
    Нет клеймов наличия → отдаём черновик как есть (source='clean'). Клеймы, подтверждённые данными
    Bridge (avail) → как есть (source='draft'). Клейм БЕЗ данных → лог + перегенерация с жёсткой
    директивой (regenerate(directive)->str, до max_retries раз); после N неудач → безопасный фолбэк
    (source='fallback'). Возвращает dict(text, ok, source∈{clean,draft,regen,fallback}, attempts,
    violations). По образцу удалённого guard_quote_price (#310)."""
    state = availability_state(avail)
    claims = availability_claims(draft)
    if not claims:
        return {"text": draft, "ok": True, "source": "clean", "attempts": 0, "violations": []}
    bad = _av_unsupported(claims, state)
    if not bad:
        return {"text": draft, "ok": True, "source": "draft", "attempts": 0, "violations": []}

    def _log(stage, items, text):
        log.warning(
            "guard_availability VIOLATION [%s] окно=%s модель=%s даты=%s..%s | клеймы=%s | draft=%r",
            stage, window, model, ds, de,
            [{"kind": b["kind"], "raw": b["raw"]} for b in items], (text or "")[:200])

    _log("initial", bad, draft)
    directive = _availability_hard_directive(lang)
    attempts, last_bad = 0, bad
    if callable(regenerate):
        for _ in range(max(0, int(max_retries))):
            attempts += 1
            try:
                cand = regenerate(directive)
            except Exception:
                log.warning("guard_availability: перегенерация упала (окно=%s), попытка %s", window, attempts)
                break
            cand_bad = availability_violations(cand, avail)
            if not cand_bad:
                return {"text": cand, "ok": True, "source": "regen", "attempts": attempts,
                        "violations": bad}
            last_bad = cand_bad
            _log(f"regen#{attempts}", cand_bad, cand)

    fb = availability_fallback(model, ds, de, lang)
    log.warning("guard_availability: после %s попыток → безопасный фолбэк (окно=%s): %r",
                attempts, window, fb)
    return {"text": fb, "ok": bool(fb), "source": "fallback", "attempts": attempts,
            "violations": last_bad}


def generate_draft(transcript: str, lang: str, faq: str,
                   is_first_contact: bool = False, pricing_note: str = "", call_llm=None,
                   park_models=None, playbook: str = "") -> str:
    """Сгенерировать черновик. call_llm(system, user)->str инъектируется в тестах; иначе по флагу
    SUGGEST_LLM_VIA_CLI — claude CLI (подписка Max) либо _default_llm (платный API-ключ).
    park_models — allowlist моделей реального парка (Лист1); None → без ограничения (fail-safe).
    playbook — книга правил (ниже кап-цены/критфактов); '' → без блока (fail-safe)."""
    call_llm = call_llm or (_cli_llm if SUGGEST_LLM_VIA_CLI else _default_llm)
    # §243/6: что клиент УЖЕ прислал (модель/даты/гео/паспорт/тел/оплата) — не переспрашиваем.
    facts = collected_facts(transcript)
    system = make_system_prompt(faq, lang, is_first_contact, pricing_note,
                                park_models=park_models, playbook=playbook, collected=facts)
    out = _strip_service_prefix(call_llm(system, transcript))
    # Пост-чек ДО сборки сетки: сканируем LLM-текст (intro/outro), дословный прайс-блок КОДА не
    # трогаем. Утверждения цвет/наличие/цена вне белого списка → «уточню»-форма + пометка модератору.
    out = postcheck_draft(out, lang, pricing_note=pricing_note, call_llm=call_llm, transcript=transcript)
    # Забор бесплатный ТОЛЬКО при оплаченной доставке: снимаем самопротиворечие «заберём бесплатно —
    # заберите сами» из LLM-текста ДО приклейки КОД-блока доставки (там забор при оплате легитимен).
    out = postcheck_free_pickup(out, lang)
    # Сетка = неприкосновенный блок: финал собирает КОД (intro + render ДОСЛОВНО + outro).
    block = _sheet_block_from_note(pricing_note)
    if block is not None:
        out = compose_sheet_draft(out, block, lang)
    # Точечный quote: цену Bridge в финал доносит КОД (fail-safe, если LLM её не привёл).
    qblock = _quote_block_from_note(pricing_note)
    if qblock is not None:
        out = compose_quote_draft(out, qblock, lang)
    # Цену доставки (если резолвер её посчитал) в финал доносит КОД тем же классом, что quote.
    dblock = _delivery_block_from_note(pricing_note)
    if dblock is not None:
        out = compose_delivery_draft(out, dblock, lang)
    out = _append_collected_note(out, facts, lang)
    return _append_season_note(out, pricing_note)


def regenerate_draft(transcript: str, lang: str, faq: str, is_first_contact: bool,
                     pricing_note: str, directive: str, call_llm=None, park_models=None,
                     playbook: str = "") -> str:
    """СТРАТЕГИЯ-перегенерация черновика С НУЛЯ: реплика модератора идёт как ДИРЕКТИВА ВЕРХНЕГО
    УРОВНЯ поверх ИСХОДНОГО клиентского контекста (транскрипт+FAQ+кап-цена), а НЕ как патч к старому
    тексту. Инварианты (ценовая политика/критфакты/парк/playbook) сохраняются — они в make_system_prompt."""
    call_llm = call_llm or (_cli_llm if SUGGEST_LLM_VIA_CLI else _default_llm)
    facts = collected_facts(transcript)
    system = make_system_prompt(faq, lang, is_first_contact, pricing_note,
                                directive=directive, park_models=park_models, playbook=playbook,
                                collected=facts)
    out = _strip_service_prefix(call_llm(system, transcript))
    # Тот же пост-чек, что в generate_draft (до сборки сетки): цвет/наличие/цена вне данных → «уточню».
    out = postcheck_draft(out, lang, pricing_note=pricing_note, call_llm=call_llm, transcript=transcript)
    # Тот же пост-чек забора, что в generate_draft: бесплатный забор ТОЛЬКО при оплаченной доставке.
    out = postcheck_free_pickup(out, lang)
    # Тот же класс, что в generate_draft: сетку в финал вставляет КОД, не LLM.
    block = _sheet_block_from_note(pricing_note)
    if block is not None:
        out = compose_sheet_draft(out, block, lang)
    # Точечный quote-блок: strategy-перегенерация несёт цену Bridge КОДОМ — не зависит от того,
    # донёс ли LLM цифры (главная цель шага; сетка была покрыта, точечный quote — теперь).
    qblock = _quote_block_from_note(pricing_note)
    if qblock is not None:
        out = compose_quote_draft(out, qblock, lang)
    # Тот же класс: strategy-перегенерация несёт цену доставки КОДОМ (не зависит от LLM).
    dblock = _delivery_block_from_note(pricing_note)
    if dblock is not None:
        out = compose_delivery_draft(out, dblock, lang)
    out = _append_collected_note(out, facts, lang)
    return _append_season_note(out, pricing_note)


# ------------------------------- rate-limit ----------------------------------

class RateLimiter:
    """Лимит отправок: не больше per_hour в час и per_day в сутки. now — инъекция."""

    def __init__(self, per_hour: int, per_day: int, now=time.time):
        self.per_hour = per_hour
        self.per_day = per_day
        self.now = now
        self._sends = []

    def allow(self):
        t = self.now()
        self._sends = [s for s in self._sends if t - s < 86400]
        in_hour = [s for s in self._sends if t - s < 3600]
        if len(self._sends) >= self.per_day:
            return False, f"дневной лимит {self.per_day} исчерпан"
        if len(in_hour) >= self.per_hour:
            return False, f"часовой лимит {self.per_hour} исчерпан"
        return True, None

    def record(self):
        self._sends.append(self.now())


limiter = RateLimiter(RATE_PER_HOUR, RATE_PER_DAY)


# ------------------------------- pending-store -------------------------------

class PendingStore:
    """map moderation_msg_id → {client_id, draft, lang, incoming}. In-memory + durable
    jsonl-журнал событий (add/del), переживает рестарт процесса."""

    def __init__(self, path: str = PENDING_FILE):
        self.path = path
        self._d = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ev = json.loads(line)
                    if ev.get("ev") == "add":
                        self._d[int(ev["mid"])] = ev["rec"]
                    elif ev.get("ev") == "del":
                        self._d.pop(int(ev["mid"]), None)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"SUGGEST: pending _load: {e}")

    def _append(self, obj):
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning(f"SUGGEST: pending _append: {e}")

    def add(self, mid: int, rec: dict):
        self._d[int(mid)] = rec
        self._append({"ev": "add", "mid": int(mid), "rec": rec})

    def get(self, mid: int):
        return self._d.get(int(mid))

    def pop(self, mid: int):
        rec = self._d.pop(int(mid), None)
        if rec is not None:
            self._append({"ev": "del", "mid": int(mid)})
        return rec


pending = PendingStore()


# ------------------------------- лог обучения --------------------------------

def _edit_diff(draft: str, final: str) -> str:
    """Компактный след правки: '' если не правили, иначе финал (перекрывает черновик)."""
    return "" if (final or "").strip() == (draft or "").strip() else final


def record_pair(rec: dict, path: str = None):
    """Запись пары обучения в suggest_pairs.jsonl:
    {ts, client, lang, incoming, draft, action, final_sent, edit}."""
    path = path or PAIRS_FILE
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"SUGGEST: record_pair: {e}")


# ------------------------------- отправка ------------------------------------

async def send_to_client(client, client_id, text, sleep=None, jitter=None):
    """Отправка клиенту с гардами. Возвращает (ok, reason). При флуде — авто-стоп.
    Defense-in-depth: в TEST_MODE отправка клиенту заблокирована и ЗДЕСЬ (второй слой,
    помимо гейта в on_moderation_reply) — чтобы никакой путь не смог написать клиенту."""
    if SUGGEST_TEST_MODE:
        log.info("SUGGEST[TEST_MODE] send_to_client заблокирован — клиенту ничего не уходит.")
        return False, "TEST_MODE"
    sleep = sleep or asyncio.sleep
    jitter = jitter or (lambda: random.uniform(PAUSE_MIN, PAUSE_MAX))

    ok, reason = limiter.allow()
    if not ok:
        log.warning(f"SUGGEST: отправка отклонена — {reason}")
        return False, reason
    try:
        async with client.action(client_id, "typing"):
            await sleep(jitter())
            await client.send_message(client_id, text)
    except FLOOD_ERRORS as e:  # FloodWait/PeerFlood → стоп
        disable(f"flood при отправке: {e}")
        return False, "flood"
    except Exception as e:
        log.warning(f"SUGGEST: ошибка отправки: {e}")
        return False, str(e)
    limiter.record()
    return True, None


# ---------------------- оркестрация (зовётся из хендлеров) -------------------

async def post_draft(client, draft: str, client_ref: str):
    """Отправить черновик на модерацию. Есть MOD_GROUP_ID → в группу, иначе в лог.
    Возвращает moderation_msg_id (int) — ключ pending. При логе — синтетический id."""
    header = f"✏️ Черновик ответа клиенту {client_ref}\n(reply: «+»/«да» — отправить, свой текст — правка, «-» — отклонить)\n\n"
    if MOD_GROUP_ID is not None:
        msg = await client.send_message(MOD_GROUP_ID, header + draft)
        return int(getattr(msg, "id", 0))
    # нет группы (или обкатка без группы) — черновик в лог, синтетический id
    mid = int(time.time() * 1000) % 2147483647
    log.info(f"SUGGEST[draft→log mid={mid}] {client_ref}: {draft}")
    return mid


async def post_mod_note(client, text: str):
    """Служебная заметка в группу «Модерация» (НЕ клиенту!). Конец слепоты: любой сбой генерации
    виден сразу в группе, без раскопок лога. Есть MOD_GROUP_ID → в группу, иначе в лог. Не падает."""
    try:
        if MOD_GROUP_ID is not None:
            await client.send_message(MOD_GROUP_ID, text)
        else:
            log.warning(f"SUGGEST[note→log] {text}")
    except Exception as e:
        log.warning(f"SUGGEST: post_mod_note упал: {e} | {text}")


async def resolve_mod_group(client):
    """Если MOD_GROUP_ID пуст и задано MOD_GROUP_NAME — найти группу по title через
    iter_dialogs и подставить её id. Возвращает id или None. Не падает: не нашли →
    лог + None (черновики пойдут в лог, как при отсутствии ID)."""
    global MOD_GROUP_ID
    if MOD_GROUP_ID is not None:
        return MOD_GROUP_ID
    if not MOD_GROUP_NAME:
        return None
    try:
        async for d in client.iter_dialogs():
            title = getattr(d, "title", None) or getattr(d, "name", None)
            if title == MOD_GROUP_NAME:
                MOD_GROUP_ID = int(d.id)
                log.info(f"SUGGEST: MOD_GROUP resolved: {MOD_GROUP_ID} (по имени «{MOD_GROUP_NAME}»).")
                return MOD_GROUP_ID
        log.warning(
            f"SUGGEST: группа «{MOD_GROUP_NAME}» не найдена среди диалогов — "
            f"черновики пойдут в ЛОГ (approve недоступен, отправки клиенту нет)."
        )
    except Exception as e:
        log.warning(f"SUGGEST: resolve_mod_group упал: {e} — черновики в лог.")
    return None


def bot_mode_active():
    """Активен ли бот-модератор (задача-2): есть токен И свежий heartbeat в IPC.
    Нет → деградация в reply-режим userbot (текущее поведение). Без токена IPC не трогаем."""
    if not MODERBOT_TOKEN:
        return False
    try:
        import moderation_ipc
        return moderation_ipc.is_bot_alive()
    except Exception as e:
        log.warning(f"SUGGEST: проверка bot-mode упала ({e}) — деградация в reply-режим.")
        return False


async def poll_and_send(client, sender=None, jitter=None, sleep=None):
    """Исполнитель userbot: разобрать решения бота (status='ready') из IPC и отправить
    клиенту. ЕДИНСТВЕННАЯ точка отправки клиенту в bot-режиме. Double-lock: в TEST_MODE
    send_to_client откажет (сюда 'ready' в TEST_MODE и не должен попасть — бот ставит
    'test_held'). Возвращает число обработанных."""
    if not is_enabled():
        return 0
    try:
        import moderation_ipc
        rows = moderation_ipc.fetch_ready()
    except Exception as e:
        log.warning(f"SUGGEST: poll_and_send: IPC недоступен ({e}).")
        return 0
    n = 0
    _send = sender or send_to_client
    for r in rows:
        final = r.get("final_text") or ""
        if not final:
            moderation_ipc.mark(r["id"], "failed", reason="пустой final_text")
            continue
        ok, reason = await _send(client, r["client_id"], final, sleep=sleep, jitter=jitter)
        moderation_ipc.mark(r["id"], "sent" if ok else "failed", reason=reason)
        record_pair({
            "ts": _now_iso(), "client": r.get("client_ref"), "lang": r.get("lang"),
            "incoming": r.get("incoming"), "draft": r.get("draft"),
            "action": "bot_decision", "final_sent": final if ok else "",
            "edit": _edit_diff(r.get("draft") or "", final), "first_contact": bool(r.get("first_contact")),
            "sent": ok, "reason": reason, "via": "moderation_bot",
        })
        n += 1
    return n


# ------------------- O3-2c: пост «🆕 БРОНЬ» во «Входящие брони» ----------------
# bot-to-bot невидимость: карточку INTAKE не видит бот-аккаунт → постить должен USERBOT
# (этот Telethon user-аккаунт). Адресат — СТРОГО INBOX_GROUP_ID (внутренняя группа), НИКОГДА
# не клиент. С ПК только ПОСТ во «Входящие», НИКАКОЙ прямой записи в CRM. Финальное «да» —
# живой авторизатор в группе. Триггер-префикс поста — «🆕 БРОНЬ».
INBOX_GROUP_ID = int(os.getenv("INBOX_GROUP_ID", "-1003997419806") or "-1003997419806")
INTAKE_POST_PREFIX = "🆕 БРОНЬ"


async def _last_client_photo(client, client_id, limit=50):
    """O3-2.1: последнее ВХОДЯЩЕЕ фото клиента в личке (паспорт шлют на этапе брони) →
    Message | None. Read-only; ЛЮБОЙ сбой → None (fail-safe: пост карточки от фото не зависит)."""
    try:
        msgs = await client.get_messages(client_id, limit=limit)
        for m in msgs:                                # newest-first
            if getattr(m, "photo", None) is not None and not getattr(m, "out", False):
                return m
    except Exception as e:
        log.info(f"SUGGEST: поиск фото у id{client_id} не удался ({type(e).__name__}) — пропуск")
    return None


async def poll_and_post_intake(client, poster=None, group_id=None, photo_finder=None,
                               forwarder=None):
    """Исполнитель userbot: забрать подтверждённые заявки (intake status='pending') и ПОСТ во
    «Входящие брони» этим userbot-аккаунтом. SAFETY: адресат строго INBOX_GROUP_ID (константа),
    zero-out клиента сохранён; постим ТОЛЬКО текст, начинающийся с «🆕 БРОНЬ» (иначе INTAKE не
    распознает). O3-2.1: если в записи есть client_id и клиент присылал фото (паспорт) — фото
    пересылается СРАЗУ ЗА карточкой (окно привязки Splinter 5 мин); нет фото/сбой → как раньше,
    INTAKE-страж честно попросит сам. poster(client, group_id, text)->msg_id,
    photo_finder(client, client_id)->Message|None, forwarder(client, gid, msg) — инъекции для
    тестов. → число обработанных."""
    try:
        import moderation_ipc
        rows = moderation_ipc.fetch_pending_intake()
    except Exception as e:
        log.warning(f"SUGGEST: poll_and_post_intake: IPC недоступен ({e}).")
        return 0
    gid = group_id if group_id is not None else INBOX_GROUP_ID
    n = 0
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text.startswith(INTAKE_POST_PREFIX):   # защита: не тот формат — не постим наружу
            moderation_ipc.mark_intake(r["id"], "failed", reason="текст не начинается с 🆕 БРОНЬ")
            continue
        try:
            if poster is not None:
                mid = await poster(client, gid, text)
            else:
                sent = await client.send_message(gid, text)   # userbot-аккаунт → внутренняя группа
                mid = getattr(sent, "id", None)
            moderation_ipc.mark_intake(r["id"], "posted", posted_msg_id=mid)
            log.info(f"SUGGEST: заявка intake #{r['id']} → Входящие брони (msg {mid}).")
            n += 1
        except Exception as e:
            moderation_ipc.mark_intake(r["id"], "failed", reason=" ".join(str(e).split())[:200])
            log.warning(f"SUGGEST: пост заявки intake #{r['id']} упал: {type(e).__name__}: {e}")
            continue
        # O3-2.1: фото паспорта из диалога — СРАЗУ ЗА карточкой. Best-effort ПОСЛЕ mark posted:
        # сбой пересылки НЕ трогает статус заявки (карточка уже стоит, страж попросит фото сам).
        # SAFETY: forward строго в gid (внутренняя группа), клиенту ничего не шлём.
        cid = r.get("client_id")
        if cid:
            try:
                photo = await (photo_finder or _last_client_photo)(client, cid)
                if photo is not None:
                    if forwarder is not None:
                        await forwarder(client, gid, photo)
                    else:
                        await client.forward_messages(gid, photo)
                    log.info(f"SUGGEST: intake #{r['id']} — фото из диалога переслано за карточкой.")
            except Exception as e:
                log.warning(f"SUGGEST: intake #{r['id']} пересылка фото не удалась "
                            f"({type(e).__name__}) — страж попросит сам.")
    return n


async def on_client_message(client, sender, me_id, call_llm=None, faq=None):
    """Врезка в on_incoming: собрать диалог → черновик → на модерацию.
    bot-режим → в IPC (бот запостит карточку с кнопками); иначе reply-режим (в группу)."""
    if not is_enabled():
        return None
    client_id = sender.id
    client_ref = f"@{sender.username}" if getattr(sender, "username", None) else f"id{sender.id}"
    msgs = await _fetch_messages(client, sender)
    transcript = transcript_from(msgs, me_id)
    # Приветствие ОДИН РАЗ на диалог. Два независимых сигнала из истории окна:
    #  • is_first_bot_reply — это первый ответ бота (нет наших сообщений) И слово-приветствие
    #    ещё не уходило прошлым нашим ответом;
    #  • has_autogreeting — в окне уже было автоприветствие Telegram Business «спасибо, что
    #    выбрали нас…» (класс «е»; _GREETING_RE его не ловит, поэтому нужен отдельный детект).
    # Здороваемся только когда это первый ответ бота И автогритинга не было.
    is_first_bot_reply = first_contact_from(msgs, me_id) and not greeting_already_sent(transcript)
    has_autogreeting = autogreeting_already_sent(transcript)
    first = is_first_bot_reply and not has_autogreeting
    last_client_line = ""
    for ln in reversed(transcript.split("\n")):
        if ln.startswith("[клиент]:"):
            last_client_line = ln[len("[клиент]:"):].strip()
            break
    # Язык — по ВСЕМУ клиенту, не по последней реплике: латинское название модели («Adv 350»)
    # в русском диалоге НЕ должно переключать ответ на EN.
    lang = detect_lang_from_client(transcript)
    faq = faq if faq is not None else load_faq()
    # Двухфазная цена: даты есть → пробуем Календарь (pricing.quote), иначе/None → фолбэк.
    # В ПОТОКЕ (to_thread): построение сетки — живые HTTP-quote; синхронно оно морозило Telethon
    # event loop (живой провал 20:52→20:59: 6м16с заморозки → «Security error… Too many messages
    # had to be ignored» от Telegram). Поток снимает заморозку; сам билд ускорен пулом в price_sheet.
    price_note = await asyncio.to_thread(
        build_pricing_note, extract_booking_hints(transcript), lang=lang)
    try:                                   # allowlist парка (Лист1); недоступен → None (fail-safe)
        allow = park_allowlist()
    except Exception as e:
        allow = None
        log.info(f"SUGGEST: park_allowlist упал ({type(e).__name__}) — без ограничения моделей")
    try:                                   # книга правил; недоступна → '' (fail-safe)
        pb = load_playbook()
    except Exception:
        pb = ""
    try:
        draft = generate_draft(transcript, lang, faq, is_first_contact=first,
                               pricing_note=price_note, call_llm=call_llm, park_models=allow, playbook=pb)
    except Exception as e:   # сбой генератора (напр. claude CLI не найден / API-ошибка) — НЕ молчим
        reason = " ".join(str(e).split())[:200] or type(e).__name__
        log.warning(f"SUGGEST: сбой генерации для {client_ref}: {reason}")
        await post_mod_note(client, f"⚠️ Черновик НЕ сгенерирован для {client_ref}: {reason}")
        return None
    if not draft:            # пустой вывод LLM — раньше молчали, теперь видно в группе (конец слепоты)
        log.warning(f"SUGGEST: пустой черновик для {client_ref} — пропускаю.")
        await post_mod_note(client, f"⚠️ Черновик НЕ сгенерирован для {client_ref}: пустой вывод LLM")
        return None
    # Гард повторного приветствия (родитель #311): LLM порой здоровается вопреки промпту. Если это
    # НЕ первый ответ бота ИЛИ уже было автоприветствие Business — детерминированно срезаем зачин
    # из черновика перед отправкой (belt-and-suspenders поверх промпта «не здоровайся повторно»).
    if (not is_first_bot_reply) or has_autogreeting:
        stripped = strip_greeting(draft)
        if stripped != draft:
            why = "автоприветствие Business в окне" if has_autogreeting else "не первый ответ бота"
            log.info(f"SUGGEST: срезано повторное приветствие в черновике для {client_ref} ({why}).")
            draft = stripped

    rec = {
        "client_id": client_id, "client_ref": client_ref, "lang": lang,
        "incoming": last_client_line, "draft": draft, "first_contact": first,
        # контекст для СТРАТЕГИЯ-перегенерации (реплика модератора → директива поверх этого):
        "transcript": transcript, "pricing_note": price_note,
        # first_name профиля — для D-колонки карточки «Бронь», если клиент в тексте не назвался:
        "client_name": getattr(sender, "first_name", None),
    }
    # bot-режим: кладём в IPC, карточку с кнопками запостит moderation_bot.
    if bot_mode_active():
        try:
            import moderation_ipc
            did = moderation_ipc.enqueue_draft(rec)
            log.info(f"SUGGEST: черновик #{did} → IPC (bot-режим) для {client_ref}.")
            return f"ipc:{did}"
        except Exception as e:
            log.warning(f"SUGGEST: IPC-enqueue упал ({e}) — деградация в reply-режим.")
    # reply-режим (деградация / бот не поднят): постим в группу сами + pending.
    mid = await post_draft(client, draft, client_ref)
    rec["ts"] = _now_iso()
    pending.add(mid, rec)
    return mid


async def _sender_username(event):
    """Юзернейм автора reply (для проверки прав). Без падения."""
    s = getattr(event, "sender", None)
    if s is None and hasattr(event, "get_sender"):
        try:
            s = await event.get_sender()
        except Exception:
            s = None
    return getattr(s, "username", None)


async def _ack(event, draft_msg_id, text):
    """Видимый ответ userbot'а на сообщение-черновик в ГРУППЕ МОДЕРАЦИИ (не клиенту)."""
    try:
        chat = getattr(event, "chat_id", None)
        if chat is None:
            chat = getattr(getattr(event, "message", None), "chat_id", None)
        await event.client.send_message(chat, text, reply_to=draft_msg_id)
    except Exception as e:
        log.warning(f"SUGGEST: не смог отправить ack модерации: {e}")


async def on_moderation_reply(event, sender=None, jitter=None, sleep=None):
    """Врезка во второй хендлер: reply менеджера в группе модерации → approve/edit/reject.
    NO-SILENT-PATHS: каждый reply получает видимый ответ userbot'а (reply на черновик).
    Права approve/edit/reject — по APPROVER-whitelist. sender/jitter/sleep — инъекция для тестов."""
    if not is_enabled():
        return None
    reply_to = None
    if getattr(event, "is_reply", False):
        reply_to = getattr(event, "reply_to_msg_id", None)
    if reply_to is None:
        return None
    rec = pending.get(reply_to)
    if rec is None:
        return None  # reply не на наш черновик

    # Права: не-approver → ⛔ и НЕ трогаем pending (пусть approver ещё сможет решить).
    username = await _sender_username(event)
    if not is_approver(username):
        await _ack(event, reply_to, "⛔ Нет прав на approve")
        return {"action": "denied", "sent": False, "reason": "not_approver", "final": None}

    action, payload = parse_approval(getattr(event, "raw_text", "") or getattr(event, "text", ""))
    final = None
    sent = False
    reason = None
    if action == "reject":
        ack = "❌ Отклонено"
    else:
        final = rec["draft"] if action == "approve" else payload
        if SUGGEST_TEST_MODE:
            # SAFETY: в TEST_MODE send_to_client НЕ вызываем — клиенту ничего не уходит.
            reason = "TEST_MODE"
            log.info(f"SUGGEST[TEST_MODE] одобрено, НЕ шлю клиенту {rec['client_ref']}: {final}")
            ack = ("🧪 Принято (TEST_MODE — клиенту НЕ отправлено)" if action == "approve"
                   else "✅ Правка принята 🧪 (TEST_MODE — клиенту НЕ отправлено)")
        else:
            _send = sender or send_to_client
            sent, reason = await _send(event.client, rec["client_id"], final, sleep=sleep, jitter=jitter)
            if action == "approve":
                ack = "✅ Отправлено клиенту" if sent else f"⚠️ Не отправлено: {reason}"
            else:
                ack = ("✅ Правка принята — отправлено клиенту" if sent
                       else f"✅ Правка принята, ⚠️ не отправлено: {reason}")

    await _ack(event, reply_to, ack)
    record_pair({
        "ts": _now_iso(), "client": rec["client_ref"], "lang": rec["lang"],
        "incoming": rec["incoming"], "draft": rec["draft"], "action": action,
        "final_sent": final if sent else ("" if action == "reject" else final),
        "edit": _edit_diff(rec["draft"], final or ""),
        "first_contact": rec.get("first_contact"), "sent": sent, "reason": reason, "ack": ack,
    })
    pending.pop(reply_to)
    return {"action": action, "sent": sent, "reason": reason, "final": final, "ack": ack}
