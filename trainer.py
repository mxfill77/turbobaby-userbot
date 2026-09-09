# -*- coding: utf-8 -*-
"""
trainer.py — ГРУППА-ТРЕНАЖЁР клиентского бота (контур ПК).

Одна выделенная Telegram-группа «Тренеровка» (владелец + userbot + модербот) работает
как песочница: сообщения владельца проходят ПОЛНЫМ боевым клиентским пайплайном (suggest:
приветствие/прайс/зоны/collected_facts/generate_draft) — как от клиента «ТЕСТ-N», ответ
уходит ПРЯМО В ГРУППУ (без модерации, без карточки в «Модерацию»). Боевой поток реальных
клиентов НЕ ЗАДЕТ ничем: тренажёр активен ТОЛЬКО в этой группе (по chat_id).

Разделение процессов (Telegram-ограничение: user-account не шлёт inline-клавиатуру):
  • userbot (Telethon, user-account) — ЕДИНСТВЕННЫЙ, кто шлёт клиентские ответы в группу;
    держит СЕССИЮ тренажёра (накопительный транскрипт ТЕСТ-клиента) и понимает ТЕКСТ-команды
    (заново / до crm / урок: … / отмени урок N) — они работают ВСЕГДА, даже если модербот лёг;
  • модербот (Bot API) — ЕДИНСТВЕННАЯ роль в этой группе: панель inline-кнопок под ответом
    [🔄 Заново][📋 До CRM][🎓 Обучить] и обработка нажатий (права: только approver). Клиенту
    и в диалог ТЕСТ-клиента модербот текстов НЕ пишет.

Сессионное состояние тренажёра (N, накопительный транскрипт, последняя пара, гипотезы,
привязанный chat_id) хранится в moderation_ipc.meta (тот же кросс-процессный канал, что уже
используют userbot и модербот). Здесь только ТЕКСТ песочницы — ни адресата-клиента, ни записи
в CRM, ни секретов. Пометка источника правил «тренажёр» — в отдельном сайдкаре trainer_rules.json
(формат playbook.md НЕ меняем — его парсеры остаются стабильными).
"""

import os
import re
import json
import hashlib
import datetime
import logging

log = logging.getLogger("trainer")     # хендлеры вешает процесс-хозяин (userbot/moderbot)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- привязка группы --------------------------------------------------------
# По умолчанию резолвим по title (как MOD_GROUP). Можно жёстко задать id через env.
TRAINER_GROUP_NAME = os.getenv("TRAINER_GROUP_NAME", "Тренеровка").strip()
_tg = os.getenv("TRAINER_GROUP_ID", "").strip()
TRAINER_GROUP_ID_ENV = int(_tg) if _tg.lstrip("-").isdigit() else None

# --- ключи сессии в moderation_ipc.meta -------------------------------------
K_CHAT = "trainer_chat_id"
K_N = "trainer_n"
K_TRANSCRIPT = "trainer_transcript"
K_INCOMING = "trainer_incoming"
K_ANSWER = "trainer_answer"
K_HYPS = "trainer_hyps"
# МУЛЬТИВЫБОР гипотез: номера-тумблеры копятся здесь (JSON-список индексов), пока владелец не
# нажал «✔ Применить». Состояние кросс-процессное (кнопки жмут в модерботе) и переживает
# перезапуск процесса — иначе половина отмеченного терялась бы на ровном месте.
K_HYP_SEL = "trainer_hyp_sel"
# «✍ другое» БЕЗ ПРЕФИКСА: после нажатия ждём СЛЕДУЮЩЕЕ текстовое сообщение владельца и пишем
# его правилом ДОСЛОВНО. Флаг ожидания — «<unix_ts>|<username>»: ставит модербот (кнопка),
# снимает тот, кто первым увидел сообщение (userbot видит в группе ВСЁ, поэтому он и ловит).
K_PENDING = "trainer_pending_lesson"
PENDING_TTL_SEC = int(os.getenv("TRAINER_PENDING_TTL_SEC", "600") or "600")   # 10 минут
# АВТОР ОДНОГО СЛЕДУЮЩЕГО УРОКА (05.09.2026). Кандидат обязан нести имя того, кто его записал, а
# путь «✍ другое → свободный текст» разорван по процессам: имя видит userbot в `take_pending_lesson`,
# а урок применяет `apply_lesson` СЛЕДУЮЩЕЙ строкой — уже без имени. Ключ несёт «<unix_ts>|<username>»
# и живёт ОДИН раз: `take_actor` его читает и тут же гасит. Одноразовость и короткий TTL — не
# украшение: липкое имя однажды подписало бы чужой урок именем прошлого учителя, а это ровно та
# ложь, ради устранения которой автор и заводится.
K_ACTOR = "trainer_lesson_actor"
ACTOR_TTL_SEC = int(os.getenv("TRAINER_ACTOR_TTL_SEC", "900") or "900")       # 15 минут
# Монотонный токен «состояние диалога менялось»: каждый клиентский турн и сброс инкрементят его.
# Дебаунс-ответ, запланированный на токене S, исполняется, только если токен всё ещё S (иначе —
# пришёл более свежий турн / был сброс → устаревший ответ не постим). Кросс-процессный (в meta):
# сброс кнопкой модербота инвалидирует отложенный ответ userbot.
K_SEQ = "trainer_seq"

TRAINER_RULES_FILE = os.path.join(BASE_DIR, "trainer_rules.json")
TRAINER_SOURCE = "тренажёр"

# ------------------------------- шапка / подсказка ---------------------------

HEADER_MARK = "[тренажёр"


def header(n, k):
    """Шапка ответа бота в группе: [тренажёр | ТЕСТ-N | правил: K]."""
    return f"[тренажёр | ТЕСТ-{int(n)} | правил: {int(k)}]"


def has_header(text):
    """Строка/сообщение начинается с шапки тренажёра? (детектор ответа userbot для модербота)."""
    return bool(text) and text.lstrip().startswith(HEADER_MARK)


# Подсказка-строка под каждым ответом — дубль кнопок ТЕКСТОМ (работает и без модербота).
HINT = ("— команды: «заново» (новый ТЕСТ-клиент) · «до crm» (карточка брони) · "
        "«урок: …» (правило) · «отмени урок N»")


def render_answer(n, k, draft):
    """Полный текст ответа бота в группу: шапка + тело черновика + подсказка-строка."""
    return f"{header(n, k)}\n\n{(draft or '').strip()}\n\n{HINT}"


CRM_TEST_PREFIX = "🆕 БРОНЬ [ТЕСТ]"


def crm_card(body):
    """Карточка моста 2.1 из тренажёра — ВСЕГДА с пометкой ТЕСТ (в боевые «Входящие брони»
    и в CRM НЕ пишем; карточка живёт только в группе тренажёра)."""
    return f"{CRM_TEST_PREFIX}\n\n{(body or '').strip()}"


# ------------------------------- текст-команды -------------------------------

_RESET_RE = re.compile(r"^\s*/?(заново|reset|сброс|новый\s+клиент)\s*$", re.IGNORECASE)
_CRM_RE = re.compile(r"^\s*/?(до\s*crm|в\s*crm|crm|до\s*срм)\s*$", re.IGNORECASE)
_LESSON_RE = re.compile(r"^\s*урок\s*[:\-—]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_CANCEL_RE = re.compile(r"^\s*отмен(?:и|ить)\s+урок\s+#?(\d+)\s*$", re.IGNORECASE)


def parse_command(text):
    """Разобрать сообщение владельца в группе тренажёра как КОМАНДУ управления.
    → (kind, payload): ('reset',None) | ('crm',None) | ('lesson', '<текст>') |
    ('cancel', <int N>) | (None, None) — обычная клиентская реплика (идёт в пайплайн)."""
    t = (text or "").strip()
    if not t:
        return (None, None)
    if _RESET_RE.match(t):
        return ("reset", None)
    m = _CANCEL_RE.match(t)
    if m:
        return ("cancel", int(m.group(1)))
    m = _LESSON_RE.match(t)
    if m:
        return ("lesson", m.group(1).strip())
    if _CRM_RE.match(t):
        return ("crm", None)
    return (None, None)


# ------------------------------- транскрипт песочницы ------------------------
# Копим сами (а НЕ перечитываем историю группы): Bot API не читает прошлые сообщения, и в
# составе группы есть модербот — его строки в клиентский транскрипт попадать НЕ должны.
# Формат строк совпадает с suggest.transcript_from: «[клиент]: …» / «[менеджер]: …».

def _clean_manager(text):
    """Из ответа бота убрать шапку тренажёра и подсказку-строку — в транскрипт кладём
    только клиентское тело реплики менеджера (как её увидел бы реальный клиент)."""
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith(HEADER_MARK):
            continue
        if s.startswith("— команды:"):
            continue
        out.append(ln)
    return "\n".join(out).strip()


def client_body(text, has_photo=False, geo_marker=None):
    """Тело клиентской реплики для транскрипта — ПО ТЕМ ЖЕ правилам, что suggest.transcript_from
    (ЛС-путь): текст (если есть) → иначе фото «[фото]» → иначе гео-маркер «[локация lat,lon]» →
    иначе «[медиа/без текста]». Врезка чинит тренажёрный путь: гео-ПИН и фото Telegram доходят
    до collected_facts и резолвера доставки МАРКЕРАМИ (иначе, собирая турн из event.raw_text,
    мы теряли вложения → пин не резолвился в зону, паспорт-фото не засчитывалось в трекер)."""
    t = (text or "").strip()
    if t:
        return text
    if has_photo:
        return "[фото]"
    if geo_marker:
        return geo_marker
    return "[медиа/без текста]"


def append_turn(transcript, role, text):
    """Дописать реплику в накопительный транскрипт. role: 'client' (владелец=ТЕСТ-клиент) |
    'manager' (ответ бота). Возвращает новый транскрипт (старый не мутируется)."""
    who = "клиент" if role == "client" else "менеджер"
    body = text if role == "client" else _clean_manager(text)
    body = (body or "").replace("\n", " ⏎ ").strip() or "[медиа/без текста]"
    line = f"[{who}]: {body}"
    return (transcript + "\n" + line) if transcript else line


def has_manager_turn(transcript):
    """Был ли уже ответ бота в этой сессии (для is_first_contact: приветствие один раз)."""
    return any(ln.startswith("[менеджер]:") for ln in (transcript or "").splitlines())


# ------------------------------- анти-тайский --------------------------------

def _is_thai_letter(ch):
    # Тайские буквы/гласные/тоны, НО НЕ знак бата ฿ (U+0E3F) — он легитимен в ценах.
    return ("ก" <= ch <= "ฺ") or ("เ" <= ch <= "๛")


def strip_thai(text):
    """Убрать тайские СИМВОЛЫ ЯЗЫКА из текста (класс регрессий «тайский в выводе»), сохранив
    знак бата ฿. Схлопывает образовавшиеся двойные пробелы. FAIL-SAFE-строка."""
    if not text:
        return text
    if not any(_is_thai_letter(c) for c in text):
        return text
    kept = "".join(c for c in text if not _is_thai_letter(c))
    # Подчистка «висячих» пробелов/скобок после вырезки.
    kept = re.sub(r"[ \t]{2,}", " ", kept)
    kept = re.sub(r"\(\s*\)", "", kept)
    return kept


# ------------------------------- гипотезы (🎓 Обучить) -----------------------

# КОНТЕКСТ ГИПОТЕЗ (задача 220, 05.09.2026). До этой правки сборщик видел РОВНО ДВЕ строки —
# реплику клиента и ответ бота — и одну строку роли в системной части: ни книги правил, ни фактов
# о компании. Замер живого случая 05.09 02:29 (KB_trainer_log, TEST-11): из четырёх гипотез две
# противоречили бизнесу — «спроси город и остров подачи, если сервис работает в нескольких
# локациях» (работаем только на Пхукете) и «уточняй точное время подачи/возврата» (времени в
# модели брони нет вовсе, срок считается днями). Лечим тем, ЧТО модель видит, а не вычёркиванием
# слов после неё: стоп-листа здесь нет и быть не должно.
#
# ВСЕ источники контекста — УЖЕ СУЩЕСТВУЮЩИЕ в коде и ЛОКАЛЬНЫЕ (ни одного нового хранилища):
#   • книга правил — suggest.load_playbook() (файл manager-bot/docs/playbook.md, тот самый, куда
#     пишет «✔ Применить» через suggest.append_playbook_rule);
#   • факты о компании — константы КЛИЕНТСКОГО промпта suggest.* (CANON_PARTS ниже): ровно то,
#     что бот обязан соблюдать, отвечая клиенту;
#   • парк — suggest._read_park_snapshot() (локальный снимок Лист1) + suggest.KNOWN_MODELS;
#   • поля брони — suggest._COLL_LABELS (что бот вообще выясняет у клиента);
#   • диалог — накопительный транскрипт сессии тренажёра (get_transcript), а не одна реплика.
#
# СЕТИ ЗДЕСЬ НЕТ СОЗНАТЕЛЬНО. hypotheses_prompt зовётся СИНХРОННО в обработчике модербота
# (moderation_bot.py: _trainer_callback, ветка «tr:teach»), а приложение собрано без
# concurrent_updates — любой сетевой вызов отсюда тормозил бы модерацию РЕАЛЬНЫХ клиентов.
# Поэтому берём только константы и локальные файлы: park_allowlist()/‌_delivery_zone_names()
# ходят в Bridge и здесь ЗАПРЕЩЕНЫ (районы доставки и так лежат в CRITICAL_FACTS дословно).

# Сколько символов накопленного транскрипта кладём в запрос (хвост — свежие турны).
HYPS_TRANSCRIPT_MAX = int(os.getenv("TRAINER_HYPS_TRANSCRIPT_MAX", "3000") or "3000")

# Константы клиентского промпта, несущие ФАКТЫ О КОМПАНИИ (в порядке подачи).
CANON_PARTS = (
    "CRITICAL_FACTS",             # депозит, прайс-ориентир, тарифы районов Пхукета, Click не сдаём
    "APPROVAL_WHITELIST_RULE",    # что вообще разрешено утверждать; сроки подготовки/выдачи — нет
    "AVAILABILITY_INVARIANT_RULE",
    "GENERATION_DEFAULT_RULE",
    "PICKUP_RULE",
    "EXPERIENCE_SAFETY_RULE",
    "RECEIPT_LEXICON_RULE",
)

# Две строки, которых В ВИДЕ КОНСТАНТЫ в коде нет, но факты в них — не новые: география взята из
# роли клиентского промпта (suggest.make_system_prompt: «менеджер проката мотобайков TurboBaby
# (Пхукет)») и из перечня районов доставки в CRITICAL_FACTS (все районы — Пхукет); «срок днями»
# следует из состава полей брони (_COLL_LABELS: модель/срок/даты/локация/паспорт/телефон/оплата —
# часов среди них нет) и из запрета APPROVAL_WHITELIST_RULE утверждать сроки подготовки и выдачи.
# Новым хранилищем это не является: ни файла, ни узла мозга под них не заводили.
CANON_GEO = ("ГДЕ РАБОТАЕМ: TurboBaby — прокат мотобайков на ПХУКЕТЕ. Другого города и другого "
             "острова у компании нет: и выдача, и доставка — только районы Пхукета (перечень "
             "районов с тарифами — в критичных фактах ниже).")
CANON_TIME = ("ЧТО ТОЧНО, А ЧТО ПРОМЕЖУТКОМ: точны тариф района доставки и цена/депозит из блока "
              "ЦЕНА по датам. Промежуток (ориентир, не обещание) — скидки за срок (~6-15% неделя, "
              "~15-25% две, ~35-50% месяц) и вилка аэропорта 590-690. Срок аренды считается ДНЯМИ: "
              "часов и точного времени подачи/возврата в брони нет вовсе, сроки подготовки и выдачи "
              "бот не называет и не фиксирует — их согласует менеджер.")


def _suggest_mod():
    """Модуль suggest ленивым импортом. Недоступен → None (тренажёр не падает)."""
    try:
        import suggest
        return suggest
    except Exception as e:                                    # pragma: no cover — среда без suggest
        log.warning("гипотезы: suggest не импортирован (%s) — канон недоступен", type(e).__name__)
        return None


def _park_models(mod):
    """Модели РЕАЛЬНОГО парка из ЛОКАЛЬНОГО снимка Лист1 — та же выжимка, что park_allowlist_status,
    но БЕЗ похода в Bridge (см. запрет сети выше). Ничего не прочитали → []."""
    try:
        keys = [mod._bike_key(n) for n in mod._read_park_snapshot()]
        return [disp for disp, key in mod.KNOWN_MODELS if any(key in k for k in keys)]
    except Exception:
        return []


def business_canon(mod=None):
    """ФАКТЫ О КОМПАНИИ одним текстом (бизнес-канон для сверки гипотез). Пусто → канона нет,
    и это ЧЕСТНЫЙ исход: вызывающий обязан сказать владельцу, что советует вслепую."""
    mod = _suggest_mod() if mod is None else mod
    if mod is None:
        return ""
    out = [CANON_GEO]
    models = _park_models(mod)
    if models:
        out.append("ЧЕМ ОПЕРИРУЕМ (реальный парк, Лист1): " + ", ".join(models)
                   + ". Моделей вне этого списка у нас нет.")
    try:
        fields = ", ".join(lbl[1][0] for lbl in mod._COLL_LABELS)
        out.append("ЧТО БОТ ВЫЯСНЯЕТ У КЛИЕНТА (весь состав брони): " + fields
                   + " — и ничего сверх этого списка.")
    except Exception:
        pass
    out.append(CANON_TIME)
    for name in CANON_PARTS:
        part = getattr(mod, name, "")
        if isinstance(part, str) and part.strip():
            out.append(part.strip())
    return "\n\n".join(out).strip()


def rules_book(mod=None):
    """Действующая книга правил ЦЕЛИКОМ (тот же файл, куда пишет «✔ Применить»). '' → книги нет."""
    mod = _suggest_mod() if mod is None else mod
    if mod is None:
        return ""
    try:
        return mod.load_playbook().strip()      # None/сбой → AttributeError ниже, исход '' честен
    except Exception:
        return ""


_BOOK_BULLET_RE = re.compile(r"^\s*[-*•]\s*(?:\(\d{4}-\d{2}-\d{2}\)\s*)?(.+)$")

# ЗАМОК СОРАЗМЕРНОСТИ. suggest._rules_similar считает похожими и те пары, где одна строка ЦЕЛИКОМ
# лежит внутри другой, — для append_playbook_rule это верно (короткое правило уже покрыто длинным),
# а для гипотезы даёт ложный повтор: слово «коротко» лежит внутри правила стиля «Коротко, вежливо,
# на языке клиента…», и гипотеза умирала бы об это вхождение. Повтором считаем только соразмерное
# совпадение: короткая сторона не меньше половины длинной. Порог назван здесь, а не размазан.
_DUP_SHARE_MIN = 0.5


def _repeat_of(hyp, rules, mod):
    """Правило книги, которое гипотеза повторяет (дословно или по смыслу), иначе None.
    Предикат — suggest._rules_similar (тот же, которым append_playbook_rule отвечает 'duplicate'),
    плюс замок соразмерности выше."""
    similar = getattr(mod, "_rules_similar", None) if mod is not None else None
    if similar is None:
        return None
    norm = getattr(mod, "_norm_rule", None) if mod is not None else None
    for r in rules:
        try:
            if not similar(hyp, r):
                continue
        except Exception:
            continue
        if norm is not None:
            a, b = norm(hyp), norm(r)
            if a and b:
                short, long = (a, b) if len(a) <= len(b) else (b, a)
                if len(short) < _DUP_SHARE_MIN * len(long):
                    continue                   # обрывок правила — не повтор
        return r
    return None


def book_rules_list(text=None, mod=None):
    """Правила книги списком — ВСЕ буллеты, а не только раздел «Выученные правила».
    Повтором считаем совпадение с ЛЮБЫМ действующим правилом: владельцу одинаково бесполезна
    гипотеза, дублирующая и выученное правило, и правило стиля, написанное рукой."""
    body = rules_book(mod) if text is None else (text or "")
    out = []
    for ln in body.splitlines():
        m = _BOOK_BULLET_RE.match(ln)
        if not m:
            continue
        r = m.group(1).strip()
        if len(r) >= 8 and not r.startswith("("):     # «(реквизиты… пока пусто)» — не правило
            out.append(r)
    return out


# Что не доехало в контекст ПОСЛЕДНЕГО запроса гипотез и что отсеялось при ПОСЛЕДНЕМ разборе.
# Кросс-функциональная память одного захода: в модерботе hypotheses_prompt → parse_hypotheses →
# hyps_messages зовутся подряд в ОДНОМ обработчике, а PTB собран без concurrent_updates (апдейты
# идут последовательно) — гонки между заходами тут нет. Флаги нужны, чтобы честность («советую
# вслепую») и отсев доехали до владельца БЕЗ правки вызывающего кода.
_LAST_MISSING = []
_LAST_DROPPED = []


def last_missing():
    """Чего не хватило в контексте последнего запроса гипотез: [] — контекст полный."""
    return list(_LAST_MISSING)


def last_dropped():
    """Что отсеяно при последнем разборе: [(гипотеза, причина)]."""
    return list(_LAST_DROPPED)


HYPS_MARK_KEEP = "ГИПОТЕЗЫ:"
HYPS_MARK_DROP = "ОТСЕЯНО:"

HYPS_BLIND_NOTE = ("⚠️ Советую ВСЛЕПУЮ: %s — сверить гипотезы с фактами компании и с книгой "
                   "правил было НЕЧЕМ. Каждую проверяй сам, прежде чем применять.")
HYPS_DROPPED_NOTE = "🧹 Скрыто гипотез: %d (%s) — они не показаны, потому что не годятся."


def hypotheses_prompt(incoming, answer, canon=None, rules=None, transcript=None):
    """(system, user) для LLM-тренера: 2–4 короткие гипотезы «что не так/что улучшить» в ОТВЕТЕ
    бота, каждая формой правила.

    ★ Задача 220: в запрос теперь входят ФАКТЫ О КОМПАНИИ (бизнес-канон), ДЕЙСТВУЮЩАЯ КНИГА ПРАВИЛ
    целиком и НАКОПЛЕННЫЙ транскрипт тренажёрного диалога. Материалы даны для ПРОВЕРКИ, а не для
    пересказа — так прямо и сказано в задании модели: иначе гипотезы выродятся в выписки из книги
    (это и есть предсмертный взгляд задания).

    None у canon/rules/transcript = «собери сам из живых источников» (так работает боевой путь,
    зовущий двумя позиционными аргументами). Явное значение — инъекция для тестов; '' — пусто.
    """
    mod = _suggest_mod()
    canon = business_canon(mod) if canon is None else (canon or "")
    rules = rules_book(mod) if rules is None else (rules or "")
    if transcript is None:
        try:
            transcript = get_transcript()
        except Exception:
            transcript = ""
    transcript = (transcript or "").strip()
    if len(transcript) > HYPS_TRANSCRIPT_MAX:                 # режем ГОЛОВУ: свежие турны важнее
        transcript = "…\n" + transcript[-HYPS_TRANSCRIPT_MAX:]

    missing = []
    if not canon:
        missing.append("фактов о компании")
    if not rules:
        missing.append("книги правил")
    global _LAST_MISSING
    _LAST_MISSING = missing

    head = ("Ты — тренер клиентского бота проката мотобайков на Пхукете. Ниже — факты о компании, "
            "действующая книга правил бота и диалог тренажёра; замечания нужны к ПОСЛЕДНЕМУ "
            "ответу бота.")
    task = ("Назови от 2 до 4 КОРОТКИХ гипотез, что в ПОСЛЕДНЕМ ОТВЕТЕ бота можно улучшить или "
            "что не так, КАЖДУЮ формой правила на будущее («делай так» / «не делай так»). По "
            "одной на строку, без нумерации и без вводных слов. Пиши по-русски.")
    if canon:
        guard_facts = ("1) ФАКТЫ О КОМПАНИИ. Гипотеза, противоречащая факту, НЕ ГОДИТСЯ: не "
                       "предлагай спрашивать или обещать то, чего у компании нет, чего она не "
                       "фиксирует или что уже решено фактом.")
    else:
        guard_facts = ("1) ФАКТОВ О КОМПАНИИ СЕЙЧАС НЕТ (источник не прочитан) — сверять гипотезу "
                       "с бизнесом нечем. Не выдумывай факты о компании и держись того, что видно "
                       "в самом диалоге.")
    if rules:
        guard_rules = ("2) КНИГА ПРАВИЛ. Гипотеза, дословно или по смыслу повторяющая правило, "
                       "которое в книге УЖЕ ЕСТЬ, НЕ ГОДИТСЯ: владелец его уже написал.")
    else:
        guard_rules = ("2) КНИГИ ПРАВИЛ СЕЙЧАС НЕТ (источник не прочитан) — проверить, не записано "
                       "ли правило раньше, невозможно.")
    guard = (
        "ПРЕЖДЕ ЧЕМ НАЗВАТЬ ГИПОТЕЗУ, проверь её по материалам выше.\n"
        + guard_facts + "\n" + guard_rules + "\n"
        "Материалы даны для ПРОВЕРКИ, а НЕ для пересказа: гипотеза обязана быть замечанием к "
        "ЭТОМУ ответу бота. Общие рассуждения, годные к любому ответу, и выписки из книги — не "
        "гипотезы.\n"
        "ФОРМАТ ОТВЕТА (строго, две секции):\n"
        + HYPS_MARK_KEEP + "\n<годные гипотезы, по одной на строку>\n"
        + HYPS_MARK_DROP + "\n<отвергнутая гипотеза — чему из фактов противоречит или какое "
        "правило книги повторяет; отвергать нечего — оставь секцию пустой>"
    )
    blocks = [head, task, guard]
    if canon:
        blocks.append("ФАКТЫ О КОМПАНИИ (бизнес-канон):\n" + canon)
    if rules:
        blocks.append("КНИГА ПРАВИЛ БОТА (действующая, целиком):\n" + rules)
    system = "\n\n".join(blocks)

    user_parts = []
    if transcript:
        user_parts.append("ДИАЛОГ ТРЕНАЖЁРА (накопленный транскрипт):\n" + transcript)
    user_parts.append("ПОСЛЕДНЯЯ ПАРА — замечания нужны к этому ответу бота:\n"
                      f"[клиент]: {incoming}\n[бот]: {answer}")
    return system, "\n\n".join(user_parts)


def _split_sections(text):
    """Ответ модели → (годные строки, строки секции «ОТСЕЯНО»). Маркеров нет → всё годное
    (FAIL-SAFE: старый плоский список разбирается ровно как раньше)."""
    keep, drop, cur = [], [], None
    for ln in (text or "").splitlines():
        s = ln.strip()
        low = s.lower().lstrip("#*- ").strip()
        if low.startswith(HYPS_MARK_KEEP.lower()):
            cur = keep
            s = s.split(":", 1)[1] if ":" in s else ""
        elif low.startswith(HYPS_MARK_DROP.lower()):
            cur = drop
            s = s.split(":", 1)[1] if ":" in s else ""
        if not s.strip():
            continue
        (keep if cur is None else cur).append(s)
    return keep, drop


def parse_hypotheses(text, limit=4, rules=None):
    """Разобрать ответ LLM в список гипотез (2–4). Срезает нумерацию/маркеры, дедупит, режет по limit.

    ★ Задача 220. Две новые вещи, обе БЕЗ сети и БЕЗ стоп-листа слов:
      • секция «ОТСЕЯНО» ответа модели владельцу НЕ показывается (её строки — это гипотезы,
        которые модель сама забраковала по фактам компании, сверяясь с каноном в контексте);
      • гипотеза, повторяющая правило, которое в книге УЖЕ ЕСТЬ, выбрасывается ДЕТЕРМИНИРОВАННО —
        предикатом suggest._rules_similar, тем самым, которым append_playbook_rule отвечает
        'duplicate'. То есть «повтор» здесь ровно то, что книга и так отказалась бы принять.
    rules=None → взять живую книгу; [] → сверку не делать (чистый разбор).
    Что выброшено и почему — в last_dropped() (доедет до владельца строкой в hyps_messages)."""
    keep_lines, drop_lines = _split_sections(text)
    dropped = [(s, "модель забраковала по фактам компании") for s in drop_lines]

    known = book_rules_list() if rules is None else list(rules or [])
    mod = _suggest_mod() if known else None

    out, seen = [], set()
    for ln in keep_lines:
        s = ln.strip().lstrip("-*•").strip()
        s = re.sub(r"^\(?\d+[.)]\s*", "", s).strip()
        if len(s) < 3:
            continue
        key = re.sub(r"\W+", "", s.lower())
        if key in seen:
            continue
        seen.add(key)
        hit = _repeat_of(s, known, mod) if known else None
        if hit:
            dropped.append((s, "повтор правила книги: «%s»" % hit[:120]))
            continue
        out.append(s)
        if len(out) >= limit:
            break
    global _LAST_DROPPED
    _LAST_DROPPED = dropped
    return out


TG_MSG_LIMIT = 4096                       # жёсткий лимит Telegram на длину текста сообщения
HYPS_TITLE = ("🎓 Что улучшить в ответе? Номера — ТУМБЛЕРЫ (тап = отметить ✅, повторный тап "
              "снимает). Отметь всё нужное и жми «✔ Применить». Или «✍️ другое» — напишешь "
              "правило своими словами следующим сообщением:")
HYPS_TITLE_CONT = "🎓 …продолжение списка гипотез:"
_HYPS_CUT = " …[обрезано]"


def _hyps_notes(blind=None, dropped=None):
    """Служебные строки перед списком: честность про недоступный канон и счёт скрытого.
    None → взять исход последнего захода (last_missing/last_dropped)."""
    miss = last_missing() if blind is None else list(blind or [])
    drop = last_dropped() if dropped is None else list(dropped or [])
    notes = []
    if miss:
        notes.append(HYPS_BLIND_NOTE % ("нет " + " и ".join(miss)))
    if drop:
        why = []
        n_book = sum(1 for _, r in drop if str(r).startswith("повтор правила книги"))
        if n_book:
            why.append("повтор правила книги: %d" % n_book)
        if len(drop) - n_book:
            why.append("против фактов компании: %d" % (len(drop) - n_book))
        notes.append(HYPS_DROPPED_NOTE % (len(drop), ", ".join(why)))
    return notes


def hyps_messages(hyps, limit=TG_MSG_LIMIT, blind=None, dropped=None):
    """ПОЛНЫЕ тексты гипотез нумерованным списком в САМОМ сообщении (кнопки — только номера
    [1]..[N], их подписи Telegram режет по ширине — читаемость живёт здесь). → список частей;
    клавиатуру вешать на ПОСЛЕДНЮЮ. Нумерация = индекс в hyps (тот же, что в callback_data).

    Лимит 4096: список длиннее части рвём ПО ГРАНИЦЕ гипотезы на несколько сообщений (ничего не
    теряем); только если ОДНА гипотеза длиннее целой части — усекаем её с явным маркером.

    ★ Задача 220: если канон/книга не прочитаны, ПЕРВОЙ частью идёт честное «советую вслепую» —
    молчаливого возврата к прежнему поведению быть не должно. Там же счёт скрытых гипотез (сами
    тексты не показываем — они и отсеяны затем, чтобы не попасть владельцу под палец)."""
    items = [strip_thai(str(h or "").strip()) for h in (hyps or [])]
    parts, cur = [], HYPS_TITLE
    for i, h in enumerate(items):
        block = f"{i + 1}. {h}"
        if len(cur) + 2 + len(block) > limit:          # не влезает в текущую часть → новая часть
            parts.append(cur)
            cur = HYPS_TITLE_CONT
            room = limit - len(cur) - 2
            if len(block) > room:                      # одна гипотеза длиннее части — честный маркер
                block = block[:max(0, room - len(_HYPS_CUT))] + _HYPS_CUT
        cur += "\n\n" + block
    parts.append(cur)
    notes = _hyps_notes(blind, dropped)
    if notes:                       # отдельной ПЕРВОЙ частью: клавиатура остаётся на последней
        parts.insert(0, "\n\n".join(notes)[:limit])
    return parts


# ------------------------------- пометка источника правил --------------------

# Книга хранит правило С ДАТОЙ-ПРЕФИКСОМ («- (2026-07-22) текст» — так кладёт
# suggest.append_playbook_rule), а пометку источника ставит СЫРОЙ текст урока, без даты.
#
# ЧЕСТНЫЕ ЧИСЛА (замер 04.09.2026 на боевом сайдкаре, 6 записей, и снимке книги
# docs/lesson_base/playbook-2026-09-03.md, 9 буллетов — sha256 снимка равен живой книге):
#   • путь, которым правило видит КОД (suggest.list_playbook_rules / remove_playbook_rule —
#     ОБА уже снимают дату сами, suggest.py:901 и :935): ДО 5 из 9, ПОСЛЕ 5 из 9 — НЕ ИЗМЕНИЛОСЬ;
#   • СЫРАЯ строка книги, поданная в эти функции напрямую: ДО 0 из 9, ПОСЛЕ 5 из 9.
# То есть боевого разрыва между пометкой и книгой на живом пути НЕ БЫЛО: «0 из 9» получается
# только сличением с сырой строкой, чего продовый вызов не делает. Правка здесь — НЕ починка
# сломанного, а СНЯТИЕ ЗАВИСИМОСТИ от того, что дату снял кто-то другой: сегодня контракт держит
# suggest, и молчаливая правка его парсера утащила бы пометку за собой. Ключ теперь свой.
# Не совпавшие 4 буллета источника не имеют ЗАКОННО: три написаны рукой, четвёртый — от 14.07,
# старше тренажёра. Шестая запись сайдкара («уже было») — сирота НЕ из-за даты: mark_source
# зовётся и на status='duplicate' (ниже по файлу), когда нового буллета в книге не появляется.
#
# Снимаем префикс в ОДНОМ месте — через _norm_rule ходят все четыре функции сайдкара
# (mark/rule_source/is_trainer_rule/unmark) и оба сличения правил с их номерами. Формат САМОЙ
# книги не трогаем: её парсеры (suggest) остаются стабильными.
# СТАРЫЙ ФОРМАТ САЙДКАРА ЧИТАЕТСЯ ПО-ПРЕЖНЕМУ: уже лежащие ключи писались текстом БЕЗ даты,
# а на тексте без даты новая нормализация тождественна прежней — ключ не меняется, записи не
# теряются (те же 5 из 9 до и после — это и есть доказательство тождественности на живых данных).
# Дефис буллета снимаем тем же местом: строка книги приходит и в виде «- (дата) …».
# Буллеты БЕЗ даты обязаны сходиться так же — для них обе регулярки холостые, ключ прежний.
_RE_RULE_BULLET = re.compile(r"^-\s*")
_RE_RULE_DATE = re.compile(r"^\(\d{4}-\d{2}-\d{2}\)\s*")


def _norm_rule(s):
    """Ключ правила: пробелы схлопнуты, регистр снят, СНЯТЫ дефис буллета и дата-префикс книги.
    Текст урока и буллет книги обязаны давать ОДИН ключ — иначе пометка источника теряется."""
    s = _RE_RULE_BULLET.sub("", " ".join(str(s or "").split()))
    return _RE_RULE_DATE.sub("", s).lower()


def _load_sources(path=None):
    path = path or TRAINER_RULES_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def mark_source(rule, source=TRAINER_SOURCE, path=None):
    """Пометить правило как поставленное из тренажёра (сайдкар; playbook.md не трогаем).
    FAIL-SAFE: ошибка записи молчит (пометка не критична для применения правила)."""
    path = path or TRAINER_RULES_FILE
    data = _load_sources(path)
    data[_norm_rule(rule)] = source
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)
        return True
    except Exception:
        return False


def rule_source(rule, path=None):
    """Источник правила ('тренажёр') или None."""
    return _load_sources(path).get(_norm_rule(rule))


def is_trainer_rule(rule, path=None):
    return rule_source(rule, path) == TRAINER_SOURCE


def unmark_source(rule, path=None):
    """Снять пометку (при «отмени урок N», если правило было тренажёрным). FAIL-SAFE."""
    path = path or TRAINER_RULES_FILE
    data = _load_sources(path)
    if data.pop(_norm_rule(rule), None) is None:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)
        return True
    except Exception:
        return False


# ------------------------------- сессионное состояние ------------------------
# По умолчанию — через moderation_ipc.meta; get/set инъектируются в тестах (dict-backed).

def _default_get(k):
    import moderation_ipc
    return moderation_ipc.get_meta(k)


def _default_set(k, v):
    import moderation_ipc
    moderation_ipc.set_meta(k, "" if v is None else str(v))


def _to_int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def bound_chat_id(get=None):
    """Привязанный chat_id тренажёра: env-override → сохранённая привязка → None."""
    if TRAINER_GROUP_ID_ENV is not None:
        return TRAINER_GROUP_ID_ENV
    return _to_int((get or _default_get)(K_CHAT))


def is_trainer_chat(chat_id, get=None):
    """Этот chat_id — привязанная группа тренажёра? Ключ ИЗОЛЯЦИИ: только True → тренажёрный путь."""
    if chat_id is None:
        return False
    bid = bound_chat_id(get)
    return bid is not None and int(chat_id) == int(bid)


def title_matches(title):
    """Title группы совпадает с искомым «Тренеровка» (для ленивой привязки по первому сообщению)."""
    return (title or "").strip().casefold() == TRAINER_GROUP_NAME.casefold()


def bind_chat(chat_id, get=None, set=None):
    """Привязать группу: сохранить chat_id и, если N ещё не задан, начать с ТЕСТ-1. Идемпотентно."""
    get = get or _default_get
    set = set or _default_set
    set(K_CHAT, int(chat_id))
    if _to_int(get(K_N)) is None:
        set(K_N, 1)
    return int(chat_id)


def get_n(get=None):
    return _to_int((get or _default_get)(K_N), 1) or 1


def get_transcript(get=None):
    return (get or _default_get)(K_TRANSCRIPT) or ""


def get_last_pair(get=None):
    get = get or _default_get
    return (get(K_INCOMING) or "", get(K_ANSWER) or "")


def get_hyps(get=None):
    raw = (get or _default_get)(K_HYPS) or ""
    try:
        v = json.loads(raw) if raw else []
        return v if isinstance(v, list) else []
    except Exception:
        return []


def set_hyps(hyps, set=None):
    (set or _default_set)(K_HYPS, json.dumps(list(hyps or []), ensure_ascii=False))
    (set or _default_set)(K_HYP_SEL, "")      # новый список гипотез → отметки старого не тянем


# --- МУЛЬТИВЫБОР гипотез (кнопки-тумблеры) -----------------------------------
# Раньше тап по номеру СРАЗУ писал правило и закрывал тему: отметить две-три гипотезы за раз было
# нельзя, а промах пальцем сразу становился выученным правилом. Теперь номер — тумблер, а запись
# происходит только по «✔ Применить»; каждая отмеченная гипотеза идёт ОТДЕЛЬНЫМ правилом со своим
# номером (⇒ «отмени урок N» продолжает работать поштучно).

def _toggle(sel, i):
    """Чистый тумблер: индекс i есть в списке → убрать, нет → добавить. → НОВЫЙ сорт. список."""
    s = set(int(x) for x in (sel or []))
    i = int(i)
    s.discard(i) if i in s else s.add(i)
    return sorted(s)


def get_selection(get=None):
    """Отмеченные номера гипотез (0-based индексы) → список int. Битьё/пусто → []."""
    raw = (get or _default_get)(K_HYP_SEL) or ""
    try:
        v = json.loads(raw) if raw else []
    except Exception:
        return []
    return sorted({int(x) for x in v}) if isinstance(v, list) else []


def set_selection(sel, set=None):
    (set or _default_set)(K_HYP_SEL, json.dumps(sorted({int(x) for x in (sel or [])})))


def toggle_selection(i, get=None, set=None):
    """Тумблер номера i в сохранённом выборе. → новый список отметок."""
    sel = _toggle(get_selection(get or _default_get), i)
    set_selection(sel, set or _default_set)
    return sel


def selected_hypotheses(get=None):
    """Тексты отмеченных гипотез в порядке номеров (устаревшие индексы молча отбрасываем)."""
    get = get or _default_get
    hyps = get_hyps(get)
    return [hyps[i] for i in get_selection(get) if 0 <= i < len(hyps)]


def apply_lessons(remarks, append_rule=None, classify=None, mark=None, list_rules=None,
                  regress=None, who=None, get=None, set=None):
    """Применить НЕСКОЛЬКО уроков разом: каждый — ОТДЕЛЬНЫМ правилом со своим номером.
    → dict(accepted=[(n, rule), …], duplicates=[…], code=[…], errors=[…], card='<одно сообщение>').
    Пустой список → карточка-предупреждение (нечего применять). Инъекции — как в apply_lesson."""
    items = [" ".join(str(r or "").split()).strip() for r in (remarks or [])]
    items = [r for r in items if r]
    if not items:
        return {"accepted": [], "duplicates": [], "code": [], "errors": [],
                "card": "⚠️ Ничего не отмечено — тапни номера гипотез и нажми «✔ Применить»."}
    accepted, duplicates, code, errors = [], [], [], []
    # АВТОР РЕШАЕТСЯ ОДИН РАЗ НА НАЖАТИЕ, а не на урок. Запись автора ОДНОРАЗОВАЯ (`take_actor`),
    # и если бы её забирал каждый `apply_lesson`, то при двух отмеченных гипотезах второй урок
    # остался бы без автора — то есть одно нажатие дало бы два разных исхода. Одно нажатие —
    # один автор у всех отмеченных.
    author = (who or "").strip() or (take_actor(get=get, set=set) if append_rule is None else "") or ""
    # ИСХОД ЗАЯВКИ ПО КАЖДОМУ КОД-УРОКУ. Раньше сводка печатала только сам текст
    # замечания — «дальше несите руками». Теперь у каждого свой исход (встала
    # заявка / не встала и почему), и владелец видит его в ТОЙ ЖЕ карточке.
    code_decs = {}
    cand_n = {}
    for r in items:
        dec = apply_lesson(r, append_rule=append_rule, classify=classify, mark=mark,
                           list_rules=list_rules, regress=regress, who=author or None, get=get)
        if dec.get("axis") == "code":
            code.append(r)
            code_decs[r] = dec
        elif dec.get("status") in ("added", STATUS_CANDIDATE):
            accepted.append(r)
            if dec.get("n") is not None:
                cand_n[_norm_rule(r)] = dec.get("n")
        elif dec.get("status") == "duplicate":
            duplicates.append(r)
        else:
            errors.append(r)
            code_decs[r] = dec               # причина отказа словами — в ту же карточку
    # номера уроков: у боевого пути — номера КАНДИДАТОВ из базы уроков; у старого синка (книга) —
    # из книги ПОСЛЕ записи (та же нумерация, что /rules и «отмени урок N»).
    numbers = dict(cand_n)
    if append_rule is not None or list_rules is not None:
        try:
            if list_rules is None:
                import suggest
                list_rules = suggest.list_playbook_rules
            for row in list_rules() or []:
                numbers[_norm_rule(row.get("rule"))] = row.get("n")
        except Exception:
            numbers = dict(cand_n)

    def _num(r):
        n = numbers.get(_norm_rule(r))
        return f"#{n} — " if n else "• "

    lines = []
    if accepted:
        if append_rule is None:
            # БОЕВОЙ путь: это КАНДИДАТЫ. Говорим это прямо — «применятся со следующего ответа»
            # было бы враньём: по кандидату бот не отвечает никому, пока не названа причина.
            lines.append(f"✅ Записано кандидатов: {len(accepted)} (автор @{author}). Бот по ним "
                         "ПОКА НЕ отвечает: чтобы урок начал действовать, назови «почему» — "
                         "причина не подставляется:")
        else:
            lines.append(f"✅ Принято уроков: {len(accepted)} (источник «{TRAINER_SOURCE}», применятся "
                         "со следующего ответа):")
        lines += [_num(r) + r for r in accepted]
    if duplicates:
        lines.append("↩️ Уже было в книге правил (не задваиваем):")
        lines += [_num(r) + r for r in duplicates]
    if code:
        lines.append("🛠 Нужен код-фикс — правилом поведения этого не выучить:")
        for r in code:
            dec = code_decs.get(r) or {}
            tail = ("→ заявка #%s ждёт твоего «да» (без него не исполнится)" % dec.get("tid")
                    if dec.get("placed")
                    else "→ в очередь НЕ встала: %s" % (dec.get("why") or "причина не названа"))
            lines.append("• %s %s" % (r, tail))
    if errors:
        lines.append("⚠️ Не удалось записать:")
        # Причина отказа — В ТОЙ ЖЕ строке. «Не удалось» без причины не чинится ничем: отказ прав,
        # отсутствие автора и отсутствие пары клиент/бот лечатся тремя разными действиями.
        for r in errors:
            dec = code_decs.get(r) or {}
            why = (dec.get("card") or "").strip().splitlines()
            lines.append("• %s%s" % (r, (" → " + why[0]) if why else ""))
    return {"accepted": [(numbers.get(_norm_rule(r)), r) for r in accepted],
            "duplicates": duplicates, "code": code, "errors": errors,
            "card": "\n".join(lines)}


# --- «✍ другое» БЕЗ ПРЕФИКСА (pending-ожидание свободного текста) -------------
# Раньше кнопка просила владельца НАПЕЧАТАТЬ «урок: …» — то есть вручную поставить префикс, чего
# голосовой ввод не делает вовсе. Теперь кнопка ставит флаг ожидания, а СЛЕДУЮЩЕЕ текстовое
# сообщение владельца становится правилом ЦЕЛИКОМ и ДОСЛОВНО (опечатки/расшифровку НЕ правим).
# TTL 10 минут: забытое ожидание не должно однажды съесть случайную реплику как правило.

def start_pending_lesson(username, now=None, set=None):
    """Включить ожидание свободного текста от ЭТОГО владельца. → метка времени (int)."""
    import time as _t
    ts = int(now if now is not None else _t.time())
    (set or _default_set)(K_PENDING, f"{ts}|{(username or '').lstrip('@')}")
    return ts


def pending_lesson(now=None, get=None):
    """Активное ожидание → username (может быть ''), иначе None (нет ожидания / истёк TTL)."""
    import time as _t
    raw = (get or _default_get)(K_PENDING) or ""
    if "|" not in raw:
        return None
    ts, _, user = raw.partition("|")
    ts = _to_int(ts)
    if ts is None:
        return None
    now = int(now if now is not None else _t.time())
    return user if (now - ts) <= PENDING_TTL_SEC else None


def clear_pending_lesson(set=None):
    (set or _default_set)(K_PENDING, "")


def take_pending_lesson(username, now=None, get=None, set=None):
    """Забрать ожидание, если оно активно и принадлежит ЭТОМУ пользователю: снимает флаг и
    отдаёт True (текст можно писать правилом). Иначе False, флаг не трогаем.
    Кто первым забрал — тот и применяет (двойного правила не будет: append дедупит)."""
    get = get or _default_get
    set = set or _default_set
    who = pending_lesson(now, get)
    if who is None:
        return False
    if who and (username or "").lstrip("@").lower() != who.lower():
        return False
    clear_pending_lesson(set)
    # Имя забравшего кладём на ОДИН следующий урок: применение приедет отдельным вызовом и без
    # username (userbot зовёт `apply_lesson(text)`), а кандидату автор обязателен.
    set_actor(username or who, now=now, set=set)
    return True


# --- автор одного следующего урока (кросс-процессный, одноразовый) ------------

def set_actor(username, now=None, set=None):
    """Запомнить, КТО пишет следующий урок. → метка времени (int). Перезапись затирает прежнего:
    последний назвавшийся и есть автор ближайшего урока."""
    import time as _t
    ts = int(now if now is not None else _t.time())
    (set or _default_set)(K_ACTOR, f"{ts}|{(username or '').lstrip('@')}")
    return ts


def peek_actor(now=None, get=None):
    """Автор следующего урока, НЕ гася запись → username или None (нет записи / пусто / истёк TTL).
    Пустое имя даёт None: «есть запись без имени» и «есть автор» — разные вещи."""
    import time as _t
    raw = (get or _default_get)(K_ACTOR) or ""
    if "|" not in raw:
        return None
    ts, _, user = raw.partition("|")
    ts = _to_int(ts)
    if ts is None or not user.strip():
        return None
    now = int(now if now is not None else _t.time())
    return user if (now - ts) <= ACTOR_TTL_SEC else None


def take_actor(now=None, get=None, set=None):
    """Забрать автора ОДИН раз: читает и гасит запись. → username или None.
    Гасим ВСЕГДА, даже если TTL уже истёк: протухшее имя не должно ждать следующего урока."""
    get = get or _default_get
    set = set or _default_set
    who = peek_actor(now, get)
    if (get(K_ACTOR) or ""):
        set(K_ACTOR, "")
    return who


def set_transcript(transcript, incoming=None, set=None):
    """Персистнуть НАКОПИТЕЛЬНЫЙ транскрипт (клиентский турн добавлен, ответа ещё нет).
    incoming (если задан) — последняя ТЕКСТовая реплика клиента (для гипотез «🎓»)."""
    set = set or _default_set
    set(K_TRANSCRIPT, transcript)
    if incoming is not None:
        set(K_INCOMING, incoming)


def get_seq(get=None):
    return _to_int((get or _default_get)(K_SEQ), 0) or 0


def bump_seq(get=None, set=None):
    """Инкремент токена состояния диалога (новый клиентский турн / сброс). → новый токен."""
    get = get or _default_get
    set = set or _default_set
    s = get_seq(get) + 1
    set(K_SEQ, s)
    return s


def record_turn(incoming, transcript, answer, set=None):
    """Зафиксировать один обмен: накопительный транскрипт + последняя пара клиент/бот
    (для кнопок «До CRM»/«Обучить», которые работают в процессе модербота)."""
    set = set or _default_set
    set(K_TRANSCRIPT, transcript)
    set(K_INCOMING, incoming)
    set(K_ANSWER, answer)


def reset(get=None, set=None):
    """Полный сброс серверного контекста ТЕСТ-клиента: накопительный транскрипт (⇒ collected_facts
    и память диалога пусты — они ДЕРИВАТ транскрипта), последняя пара, гипотезы; N += 1.
    Возвращает новый N (номер нового ТЕСТ-клиента)."""
    get = get or _default_get
    set = set or _default_set
    n = get_n(get) + 1
    set(K_N, n)
    set(K_TRANSCRIPT, "")
    set(K_INCOMING, "")
    set(K_ANSWER, "")
    set(K_HYPS, "")
    set(K_HYP_SEL, "")             # отметки тумблеров старого ТЕСТ-клиента не переезжают
    set(K_PENDING, "")             # и ожидание «✍ другое» тоже снимаем (иначе съест первую реплику)
    set(K_SEQ, get_seq(get) + 1)   # инвалидируем отложенный дебаунс-ответ старого ТЕСТ-клиента
    return n


# ------------------------------- урок: (2-я ось behavior/code) ---------------

def classify_lesson(remark):
    """Классифицировать урок 2-й осью (behavior|code|unsure) через lesson_router. Ленивый импорт
    (lesson_router тяжёлый). FAIL-SAFE: сбой классификатора → 'behavior' (урок пойдёт в playbook,
    а не потеряется). Возвращает 'behavior' | 'code'."""
    try:
        import lesson_router
        dec = lesson_router.classify_lesson_type(remark)
        t = (dec or {}).get("type")
    except Exception:
        t = None
    return "code" if t == "code" else "behavior"


# ═══════════ УРОК ОСИ «КОД» → ЗАЯВКА В ОЧЕРЕДЬ ПК КНОПКОЙ ВЛАДЕЛЬЦА ══════════
# (заведено 04.09.2026; до этого дня исход «код» был ТУПИКОМ — см. ниже числом)
#
# ЧТО ЗДЕСЬ БЫЛО ДО ЭТОГО ЗАХОДА. Классификатор относил замечание к оси «код»,
# `_apply_lesson` печатал владельцу карточку «нужен код-фикс» со строкой «Готовый
# текст задачи: <замечание>» — и ветка КОНЧАЛАСЬ. Никуда эта строка не уходила:
# задачу дальше нёс человек руками, перепечатывая её в очередь. Замер того же дня:
# книга правил (`manager-bot/docs/playbook.md`) не менялась с 23.07 — петля
# «увидел плохой ответ — исправил» простояла полтора месяца, и живых следов свежих
# уроков в ней нет ни одного.
#
# ПОЧЕМУ ЗАЯВКА, А НЕ ЗЕЛЁНЫЙ РЯД. Прямой запрет задания: «без „да“ владельца в
# очередь не встаёт НИЧЕГО; ветки, которая ставит задачу молча, быть не должно».
# Поэтому дорога здесь ОДНА и она проходит через человека: ряд встаёт сразу в
# `needs_approval` (enqueue → claim → set_needs_approval), владелец видит карточку
# С КНОПКОЙ и ПОЛНЫЙ текст того, что исполнится, и до его «да» не исполняется ни
# одна ветка. `enqueue_pc_task` без перевода в `needs_approval` — то есть зелёный
# ряд, который подберёт `process_new`, — здесь не зовётся НИГДЕ, и это закреплено
# обходом AST в наборе (`test_trainer`), а не обещанием в комментарии.
#
# ПОЧЕМУ НЕ В ПАПКУ МОЗГА. Второй прямой запрет задания. Ящик Штаба
# (`shtab_box`) в папку не пишет ни одной веткой — это его самый дорогой инвариант,
# и он держится тестом. Задача отсюда встаёт ПРЯМО В ОЧЕРЕДЬ, как это уже делают
# другие источники полосы (ступени B и E, ревизор).
#
# ЗАПРЕТЫ И ФОРМА АДРЕСА БЕРУТСЯ У ЯЩИКА, А НЕ ПИШУТСЯ ЗАНОВО СВОИМИ СЛОВАМИ.
# Это предсмертный взгляд задания, названный дословно: «провалится тем, что запреты
# и форму адреса напишут в тренажёре заново — и через месяц ворота ящика начнут
# отвергать всё, что тренажёр собирает». Поэтому блок запретов приезжает
# ДОСЛОВНО из `shtab_box.PROHIBITIONS`, строка полосы — из `shtab_box.LANE_LINE`,
# а строка адреса СОБИРАЕТСЯ ИЗ ОБРАЗЦА `shtab_box.ADDRESS` подстановкой даты и
# слов. Разъедься образец с нашими подстановками — сборка честно ОТКАЖЕТ
# (:func:`address_line` вернёт None), а не начнёт молча выдавать адрес прежней
# формы: молчаливое расхождение и есть та самая смерть через месяц.
#
# И ГЛАВНОЕ: СОБРАННОЕ ПРОГОНЯЕТСЯ ЧЕРЕЗ ЧУЖИЕ ВОРОТА ПЕРЕД ПОКАЗОМ. Судит
# `shtab_box.check` — та же функция, которой ящик судит задания Штаба, а не наша
# копия той же формы. Не прошло — карточки с кнопкой владелец не видит ВООБЩЕ, а
# видит слова, чего в ней не хватило (причина — чужая, дословно от ворот).

TASK_FROM = "Filipp-trainer"          # метка ряда: видно в очереди, откуда взялась заявка
# Маркер первой строки ряда. Он НЕ совпадает ни с одним маркером, по которому демон
# зовёт ряд «заявкой, принятой к сведению» (`[заявка-ревью`, `[разведка-заявка`,
# `[ревизор-находки]`), и это условие, а не совпадение: на те три «да» означает
# «прочитал», а нам нужно «выполняй». Несовпадение закреплено тестом.
CLAIM_MARK = "[тренажёр-код-фикс]"
CODE_CARD_MARK = "🛠 Нужен код-фикс"   # первые слова карточки в группе — во ВСЕХ ветках исхода
QUOTE_MAX = 700                        # потолок одной цитаты в премисе (тело ряда ≤ 6000)
SLUG_WORDS = 6                         # слов замечания в словах адреса
SLUG_MAX = 60

_SLUG_DROP = re.compile(r"[^0-9A-Za-zА-Яа-яЁё\- ]+")
# Подстановки в ОБРАЗЕЦ адреса ящика. Ищем то, что образец обязан содержать по
# своей же форме («за <дд.мм>» и хвост «со словами …»), и требуем, чтобы ОБЕ
# подстановки сработали: не сработала хоть одна — форма у ящика уехала, и собирать
# адрес наугад нельзя.
_ADDR_DAY_RE = re.compile(r"за\s+\d{1,2}\.\d{1,2}(?:\.\d{4})?")
_ADDR_WORDS_RE = re.compile(r"со\s+словами\s+.*\Z", re.S)


def _box():
    """Ленивый импорт ящика — ЕДИНСТВЕННОГО источника формы запретов, полосы и адреса.

    Ленивый сознательно: `trainer` импортируют боевые userbot и модербот, а ящик
    тянет за собой судью закрытия и клиент моста. Импорт на модульном уровне
    сделал бы цену чужого дерева обязательной для процессов, которым ящик не нужен.
    """
    import shtab_box
    return shtab_box


def day_ddmm(now=None):
    """Дата полосы «дд.мм» (местное время). Часы инъектируются в тестах."""
    now = now or datetime.datetime.now()
    return "%02d.%02d" % (now.day, now.month)


def _slug(remark, words=SLUG_WORDS, cap=SLUG_MAX):
    """Замечание → короткий хвост для слов адреса (буквы, цифры, дефис, пробелы)."""
    clean = _SLUG_DROP.sub(" ", str(remark or "")).lower()
    parts = [p for p in clean.split() if p][:int(words)]
    return " ".join(parts)[:int(cap)].strip()


def address_words(remark, day):
    """Слова адреса результата — УНИКАЛЬНЫЕ для этого шага. → str ('' — собрать не из чего).

    Уникальность обязательна: судья закрытия прежнего состояния папки не знает и
    позеленел бы на артефакте, написанном вчера. Поэтому в словах стои́т дата ПЛЮС
    суть замечания, а не общее слово «код-фикс», которое повторится на втором же
    уроке.
    """
    tail = _slug(remark)
    if not tail:
        return ""
    return "код-фикс из тренажёра %s %s" % (str(day or ""), tail)


def address_line(day, words, sample=None):
    """Строка адреса результата ФОРМОЙ ЯЩИКА. → str | None (форма образца уехала).

    Второго экземпляра формы здесь нет: берём живой образец `shtab_box.ADDRESS` и
    подставляем в него СВОЮ дату и СВОИ слова. ``None`` — образец перестал отвечать
    подстановкам, и это честный отказ собрать, а не тихий адрес прежней формы.
    """
    line = _box().ADDRESS if sample is None else str(sample)
    words = str(words or "").strip()
    if not words:
        return None
    line, hit_day = _ADDR_DAY_RE.subn("за %s" % str(day or ""), line, count=1)
    line, hit_words = _ADDR_WORDS_RE.subn("со словами %s" % words, line, count=1)
    if not (hit_day and hit_words):
        return None
    return line.strip()


def task_key(remark, day):
    """Ключ заявки для ЧУЖИХ ворот (`shtab_box.KEY_RE`: латиница, цифры, . _ -). → str.

    Ключ здесь — формальность ворот, а не имя задания ящика: маркер `[от Штаба …]`
    мы не ставим и дедуп ящика не трогаем ни строкой. Но ворота судят и ключ, и
    подсунуть им заведомо битый значило бы получить отказ не по делу.
    """
    digest = hashlib.sha256(str(remark or "").encode("utf-8")).hexdigest()[:8]
    return "trainer-%s-%s" % (str(day or "").replace(".", ""), digest)


def build_claim(remark, incoming=None, answer=None, now=None, n=None, day=None):
    """Замечание владельца + живая пара клиент/бот → ТЕКСТ РЯДА ОЧЕРЕДИ. → dict.

    Поля: ``ok``, ``text`` (то, что исполнится после «да»), ``key``, ``why``
    (словами — для владельца, когда собрать не вышло), ``gate`` (машинная причина
    чужих ворот).

    ЧТО ОБЯЗАНО БЫТЬ В ТЕЛЕ (требование задания, три вещи):

    * стандартный блок запретов — ДОСЛОВНО из `shtab_box.PROHIBITIONS`;
    * премиса с ТЕМ САМЫМ ответом бота, который признан плохим, и с текстом
      клиентского вопроса — иначе исполнитель чинит код по пересказу;
    * адрес результата ТОЙ формой, которую читает судья закрытия.

    ТРЕТИЙ ИСХОД ЧЕСТЕН: собрать не из чего (пустое замечание, нет живой пары,
    форма адреса уехала) → ``ok=False`` со словами, и наверху это ОТКАЗ СЛОВАМИ, а
    не карточка с полупустым телом. Ровно это требует отрицательный тест задания.
    """
    box = _box()
    remark = " ".join(str(remark or "").split()).strip()
    if not remark:
        return {"ok": False, "text": "", "key": "", "gate": "empty_remark",
                "why": "замечание пустое — задачу собирать не из чего"}
    incoming = str(incoming or "").strip()
    answer = str(answer or "").strip()
    if not (incoming and answer):
        # ПРЕМИСА БЕЗ ЖИВОЙ ПАРЫ — ЭТО ПЕРЕСКАЗ, А НЕ ФАКТ. Задача, у которой нет
        # ни вопроса клиента, ни признанного плохим ответа, отправила бы исполнителя
        # чинить код по чужому мнению. Лучше сказать словами, чем поставить такую.
        missing = " и ".join([w for w, v in (("текст клиентского вопроса", incoming),
                                             ("ответ бота", answer)) if not v])
        return {"ok": False, "text": "", "key": "", "gate": "no_pair",
                "why": ("в премису нечего положить: нет %s. Задача без того самого ответа и "
                        "того самого вопроса — пересказ, а не факт; собирать её не станем. "
                        "Напиши как клиент, дождись ответа бота и повтори урок" % missing)}
    day = str(day or day_ddmm(now))
    words = address_words(remark, day)
    addr = address_line(day, words)
    if not addr:
        return {"ok": False, "text": "", "key": "", "gate": "no_address_form",
                "why": ("форма адреса результата у ящика (shtab_box.ADDRESS) перестала отвечать "
                        "нашим подстановкам — собрать адрес наугад нельзя. Чинится правкой "
                        "тренажёра под новую форму, а не обходом")}
    key = task_key(remark, day)
    text = "\n".join([
        "%s ЗАЯВКА НА ЗАДАЧУ · тренажёр ТЕСТ-%s · %s" % (CLAIM_MARK, n if n is not None else "?",
                                                         day),
        "Источник: группа-тренажёр, урок оси «код» (классификатор lesson_router). Ставит "
        "тренажёр с полосы ПК, исполняет полоса ПК.",
        "БЕЗ твоего «да» задача не исполняется ни одной веткой: «да <номер>» — уходит в работу, "
        "«нет <номер>» — терминальный отказ.",
        "",
        "ЦЕЛЬ. Ответ бота, признанный владельцем плохим, правилом поведения не лечится "
        "(классификатор отнёс замечание к оси «код»). Исправить КОД так, чтобы на том же "
        "вопросе клиента ответ стал верным.",
        "",
        box.PROHIBITIONS,
        "",
        box.LANE_LINE,
        "",
        "ПРЕМИСА — ПРОВЕРИТЬ ПЕРВЫМ ДЕЙСТВИЕМ, НЕ ВЕРИТЬ НА СЛОВО. Снято тренажёром %s." % day,
        "1. Клиент (ТЕСТ-%s) написал ДОСЛОВНО: «%s»" % (n if n is not None else "?",
                                                        incoming[:QUOTE_MAX]),
        "2. Бот ответил ДОСЛОВНО: «%s»" % answer[:QUOTE_MAX],
        "   ← ИМЕННО этот ответ владелец назвал плохим.",
        "3. Замечание владельца ДОСЛОВНО: «%s»" % remark[:QUOTE_MAX],
        "4. Замечание отнесено к оси «код», поэтому в книгу правил бота оно НЕ записано: "
        "правилом поведения этого не выучить.",
        "",
        "ЧТО СДЕЛАТЬ",
        "1. Признак сделанности: первым действием открыть адрес результата и сказать, отвечены "
        "ли в нём вопросы этого задания. Отвечены — назвать дату и остановиться.",
        "2. Проверить премису своими командами: воспроизвести названный ответ на названном "
        "вопросе, а не поверить цитате.",
        "3. Назвать файлом и строкой то место кода, которое родило этот ответ.",
        "4. Исправить код так, чтобы на том же вопросе ответ стал верным.",
        "5. Закрыть правку тестом, который на ПРЕЖНЕМ коде красный, а на новом зелёный.",
        "6. Прогнать набор тестов затронутого места. Полный набор полосы НЕ гнать.",
        "",
        "АРИФМЕТИКА. Единица одна: правка кода с тестом. Потолок 2700 с, целимся в 1500.",
        "",
        "ВРЕМЕННОЕ живёт в _scratch_trainer_codefix_%s/ и больше нигде." % day.replace(".", ""),
        "",
        addr,
    ])
    # ЧУЖИЕ ВОРОТА — ПЕРЕД ПОКАЗОМ, А НЕ ПОСЛЕ. Судит `shtab_box.check`: та же
    # функция, которой ящик судит задания Штаба. Своей копии тех же проверок здесь
    # нет ни одной — она разошлась бы с оригиналом молча, и через месяц ворота
    # отвергали бы всё, что тренажёр собирает.
    ok, reason, why = box.check({"key": key, "body": text})
    if not ok:
        return {"ok": False, "text": "", "key": key, "gate": reason,
                "why": "ворота приёма ящика не пропустили задачу: %s" % why}
    return {"ok": True, "text": text, "key": key, "gate": "", "why": ""}


def _default_place(text, daemon=None):
    """Ряд-заявка в очередь + карточка владельцу С КНОПКОЙ. → (ok, id|None, причина).

    Порядок тот же, что у всех заявок полосы (ступени B и E, owner-карточка
    ревизора): ``enqueue`` → ``claim`` → ``set_needs_approval``, синхронно. Ряд
    минует ``new`` НАМЕРЕННО: в ``new`` его подобрал бы штатный `process_new` — и
    задача исполнилась бы БЕЗ «да», ровно то, что задание запрещает.

    ЗАМОК ОТ ТЕСТОВ: при ``TESTING`` живая заявка не ставится ВООБЩЕ. Набор
    тренажёра гоняют с ``TESTING=1``, и один недомоканный вызов положил бы в боевую
    очередь настоящий ряд — с карточкой владельцу среди ночи.
    """
    if os.getenv("TESTING"):
        return False, None, ("TESTING=1 — живую заявку в очередь не ставим "
                             "(боевой ряд из тестового прогона)")
    d = daemon
    if d is None:
        import pc_orchestrator as d
    ok, tid, err = d.enqueue_pc_task(text, frm=TASK_FROM)
    if not ok:
        return False, None, str(err or "enqueue отклонён")
    d.bc.claim_task(tid)
    res = d.bc.set_needs_approval(tid, text, topic=d.NEEDS_APPROVAL_TOPIC)
    if not (isinstance(res, dict) and res.get("ok")):
        why = str((res or {}).get("error_text") or (res or {}).get("error")
                  or "мост не ответил распиской")
        return False, tid, why[:200]
    return True, tid, ""


def code_fix_claim(remark, incoming=None, answer=None, now=None, n=None, place=None, pair=None,
                   get=None):
    """Урок оси «код» → ЗАЯВКА владельцу карточкой с кнопкой. → dict(placed, tid, card, why).

    Живая пара клиент/бот берётся из сессии тренажёра (та же, что у кнопок «До CRM»
    и «Обучить»), если её не передали явно. ``place`` инъектируется в тестах.

    НИКОГДА НЕ БРОСАЕТ: обработчик группы не имеет права упасть на уроке. Любой
    сбой → карточка со словами, начинающаяся теми же словами «нужен код-фикс», —
    владелец видит исход, а не тишину.
    """
    try:
        if incoming is None or answer is None:
            got = (pair or get_last_pair)(get) if get is not None else (pair or get_last_pair)()
            incoming = got[0] if incoming is None else incoming
            answer = got[1] if answer is None else answer
        built = build_claim(remark, incoming, answer, now=now, n=n)
        if not built["ok"]:
            return {"placed": False, "tid": None, "why": built["why"], "gate": built["gate"],
                    "card": ("%s (из тренажёра) — правилом поведения этого не выучить.\n"
                             "НО задачу оркестратору собрать НЕ УДАЛОСЬ, карточку с кнопкой не "
                             "показываю: %s.\nЗамечание не потеряно — вот оно дословно: %s"
                             % (CODE_CARD_MARK, built["why"], remark))}
        ok, tid, err = (place or _default_place)(built["text"])
        if not ok:
            return {"placed": False, "tid": tid, "why": err, "gate": "not_placed",
                    "card": ("%s (из тренажёра) — правилом поведения этого не выучить.\n"
                             "Задача СОБРАНА и чужие ворота приёма ящика её пропустили, но в "
                             "очередь она НЕ встала: %s.\nГотовый текст задачи:\n%s"
                             % (CODE_CARD_MARK, err, built["text"]))}
        return {"placed": True, "tid": tid, "why": "", "gate": "",
                "card": ("%s (из тренажёра) — правилом поведения этого не выучить.\n"
                         "Задача собрана по канону полосы, прошла чужие ворота приёма ящика и "
                         "ждёт ТВОЕГО решения карточкой с кнопкой: заявка #%s.\n"
                         "«да %s» — уходит в работу · «нет %s» — терминальный отказ. "
                         "Без «да» не исполнится ни одна ветка.\n"
                         "Что именно встанет в очередь — целиком в карточке; здесь начало:\n%s…"
                         % (CODE_CARD_MARK, tid, tid, tid, built["text"][:600]))}
    except Exception as e:                                  # noqa: BLE001 — см. докстринг
        log.warning("code_fix_claim упал: %s: %s", type(e).__name__, e, exc_info=True)
        return {"placed": False, "tid": None, "why": "%s: %s" % (type(e).__name__, e),
                "gate": "crash",
                "card": ("%s (из тренажёра) — правилом поведения этого не выучить.\n"
                         "Задачу собрать не вышло (внутренняя ошибка %s, см. лог процесса).\n"
                         "Замечание не потеряно — вот оно дословно: %s"
                         % (CODE_CARD_MARK, type(e).__name__, remark))}


def n_for_claim(get=None):
    """Номер ТЕСТ-клиента для премисы. FAIL-SAFE: сессия недоступна → None, а не падение."""
    try:
        return get_n(get or _default_get)
    except Exception:
        return None


def _rule_number(rule, list_rules=None):
    """Номер правила в книге ПОСЛЕ записи (та же нумерация, что /rules и «отмени урок N») или
    None. FAIL-SAFE: сбой чтения книги не роняет урок — просто карточка без номера."""
    try:
        if list_rules is None:
            import suggest
            list_rules = suggest.list_playbook_rules
        for row in list_rules() or []:
            if _norm_rule(row.get("rule")) == _norm_rule(rule):
                return row.get("n")
    except Exception:
        return None
    return None


def _default_regress(rule, n=None, act=None):
    """Регрессия урока ОТДЕЛЬНЫМ ОТСОЕДИНЁННЫМ процессом (`lesson_regress.spawn`).

    Импорт ЛЕНИВЫЙ и внутри try: путь урока не имеет права зависеть от того, лежит ли на диске
    прибор — «записано» владельцу дороже, чем «измерено».

    `act` — КАКОЕ движение мерим («записан» / «снят»). Оно едет до самой строки исхода, потому что
    прибор говорит владельцу «Урок #N записан · регрессия…», и на отмене эта строка была бы
    ложью о своём же поводе."""
    try:
        import lesson_regress
    except Exception as e:                                  # noqa: BLE001 — см. докстринг
        log.warning("регрессия урока недоступна: %s: %s", type(e).__name__, e)
        return {"spawned": False, "why": "%s: %s" % (type(e).__name__, e)}
    return lesson_regress.spawn(rule, n=n, act=act)


def _regress_call(regress, rule, n, act=None):
    """ЗАПИСЬ УРОКА НЕ ЗАМЕДЛЯЕТСЯ И НЕ ОТМЕНЯЕТСЯ ПРИБОРОМ (правило задания 231).

    Два замка в одной строке кода:
      • НЕ ЖДЁТ — внутри `spawn` стои́т Popen без ожидания и без чтения потоков; корпус (≈7 мин)
        живёт в чужом процессе, а владелец получает «✅ Принято» тогда же, когда получал вчера;
      • НЕ РОНЯЕТ — любой сбой запуска проглатывается здесь, а не поднимается наружу: правило уже
        ЛЕЖИТ в книге, и падение нашего прибора не имеет права превратить успешную запись в
        карточку «урок не применён».
    → dict(spawned, why) — для тестов и лога, карточке владельца это ничего не добавляет.

    ОБЕ СТОРОНЫ ХОДЯТ ОДНОЙ ДВЕРЬЮ: `act` отличает записанный урок от снятого, а замки «не ждёт»
    и «не роняет» одни и те же — отмена не имеет права ни подвиснуть на приборе, ни отмениться
    из-за него (урок УЖЕ снят в базе к моменту вызова)."""
    try:
        call = regress or _default_regress
        # `act` ДОКЛАДЫВАЕТСЯ ТОЛЬКО КОГДА НАЗВАН: сторона записи зовёт прибор ровно так же, как
        # звала вчера (двухаргументно), и ни одна её инъекция от этой правки не меняется.
        return call(rule, n, act=act) if act else call(rule, n)
    except Exception as e:                                  # noqa: BLE001 — см. докстринг
        log.warning("регрессия урока не запущена: %s: %s", type(e).__name__, e, exc_info=True)
        return {"spawned": False, "why": "%s: %s" % (type(e).__name__, e)}


# ============ УРОК ВЛАДЕЛЬЦА → КАНДИДАТ В БАЗЕ УРОКОВ (05.09.2026) ==========================
# ЧТО ПОМЕНЯЛОСЬ И ЗАЧЕМ. Кнопка «🎓 Обучить» и команда «урок:» писали строку текста в ПЛОСКУЮ
# книгу (`suggest.append_playbook_rule` → manager-bot/docs/playbook.md). У такой записи нет ни
# автора, ни времени, ни причины, ни номера в базе — откатить её можно только вырезав строку, а
# ответить «кто и почему это записал» нечем вовсе. Теперь урок ложится КАНДИДАТОМ в `lesson_store`
# со всеми пятью полями (вопрос клиента, ответ бота, как правильно, кто записал, когда), а плоская
# книга кнопкой НЕ ПИШЕТСЯ ни байтом.
#
# ПРИЧИНЫ («почему») У КАНДИДАТА НЕТ, И ОНА НЕ ПОДСТАВЛЯЕТСЯ. Ни из текста урока, ни из вопроса
# клиента, ни из чего-либо ещё: подставленное «почему» отвечает на вопрос «что написано», а не
# «почему так правильно», и владелец потом не отличит свою причину от машинной. Кандидат лежит в
# состоянии «причина не названа» и становится действующим ОТДЕЛЬНЫМ действием — `lesson_store.promote`,
# который без непустой причины отказывает.
#
# ПРАВО берётся из ТОЧКИ ЗАПИСИ, закрытой fail-closed 05.09 (`moderation_core.may_write_rule`:
# пустой список прав = НИКОМУ), а не из общей проверки модерации `suggest.is_approver` (у той на
# пустом списке ответ «да», и трогать её нельзя — радиус на все кнопки модерации).
STATUS_CANDIDATE = "candidate"
STATUS_DENIED = "denied"
STATUS_NO_AUTHOR = "no_author"
STATUS_NO_PAIR = "no_pair"


def _default_may_write(username):
    from moderation_core import may_write_rule      # ленивый: тяжёлый модуль не тянем в импорт
    return may_write_rule(username)


def _default_add_candidate(**kw):
    import lesson_store
    return lesson_store.add_candidate(**kw)


def lesson_candidate(remark, who=None, question=None, bot_answer=None, when=None,
                     may_write=None, add_candidate=None, get=None, now=None):
    """Урок владельца → КАНДИДАТ в базе уроков. → dict(status, card, n, who).

    Исходы называются РАЗНЫМИ словами, потому что чинятся они по-разному:
      `candidate`  — записан, номер в `n`;
      `denied`     — прав на запись нет (в том числе «список прав пуст» — fail-closed);
      `no_author`  — некому приписать урок: имени не назвал ни вызывающий, ни сессия. НЕ пишем:
                     кандидат без автора не отзывается разрезом «автор» и не проверяется;
      `no_pair`    — в сессии тренажёра нет пары «вопрос клиента / ответ бота», а кандидат без
                     них — это снова строка текста, ровно та, от которой уходим;
      `error`      — хранилище отказало (причина словами от `LessonRejected`).

    Ни один исход не пишет в плоскую книгу и не зовёт `suggest.append_playbook_rule`."""
    remark = " ".join(str(remark or "").split()).strip()
    if not remark:
        return {"status": "error", "n": None, "who": None,
                "card": "⚠️ Пустой урок — нечего запоминать."}
    get = get or _default_get
    author = (who or "").strip() or take_actor(now=now, get=get) or ""
    author = author.lstrip("@").strip()
    if not author:
        return {"status": STATUS_NO_AUTHOR, "n": None, "who": None,
                "card": "⚠️ Урок НЕ записан: не назван автор. Кандидат обязан нести имя того, кто "
                        "его записал — иначе его нечем ни проверить, ни отозвать."}
    if not (may_write or _default_may_write)(author):
        return {"status": STATUS_DENIED, "n": None, "who": author,
                "card": "⛔ Нет прав на запись уроков — кандидат НЕ создан."}
    if question is None or bot_answer is None:
        pair_q, pair_a = get_last_pair(get)
        question = pair_q if question is None else question
        bot_answer = pair_a if bot_answer is None else bot_answer
    if not (str(question or "").strip() and str(bot_answer or "").strip()):
        return {"status": STATUS_NO_PAIR, "n": None, "who": author,
                "card": "⚠️ Урок НЕ записан: нет пары «вопрос клиента / ответ бота». Напиши как "
                        "ТЕСТ-клиент, дождись ответа бота и повтори — кандидату нужен предмет."}
    try:
        n = (add_candidate or _default_add_candidate)(
            question=str(question), bot_answer=str(bot_answer), correct=remark,
            who=author, why="", when=when, now=now)          # why="" — причину НЕ выдумываем
    except Exception as e:                                   # noqa: BLE001 — обработчик не падает
        log.warning("кандидат урока не записан: %s: %s", type(e).__name__, e, exc_info=True)
        reason = getattr(e, "reason", None) or f"{type(e).__name__}: {e}"
        return {"status": "error", "n": None, "who": author,
                "card": f"⚠️ Урок НЕ записан в базу уроков: {reason}"}
    return {"status": STATUS_CANDIDATE, "n": n, "who": author,
            "card": f"✅ Записан кандидат #{n} (автор @{author}): {remark}\n"
                    "📌 Причина не названа — пока это КАНДИДАТ, бот по нему НЕ отвечает. "
                    "Действующим станет отдельным действием, и только когда назовёшь «почему»."}


def _apply_lesson(remark, append_rule=None, classify=None, mark=None, list_rules=None,
                  regress=None, who=None, get=None):
    """Ядро apply_lesson (может бросить — снаружи fail-safe обёртка)."""
    remark = " ".join(str(remark or "").split()).strip()
    if not remark:
        return {"axis": "behavior", "status": "error",
                "card": "⚠️ Пустой урок — нечего запоминать."}
    axis = (classify or classify_lesson)(remark)
    if axis == "code":
        # ИСХОД «КОД» БОЛЬШЕ НЕ ТУПИК (04.09.2026). Раньше здесь печаталась строка
        # «Готовый текст задачи: <замечание>» — и ветка кончалась: дальше задачу нёс
        # человек руками. Теперь задача СОБИРАЕТСЯ по канону полосы, проходит ЧУЖИЕ
        # ворота приёма ящика и уходит владельцу карточкой С КНОПКОЙ. В очередь без
        # его «да» не встаёт ничего.
        got = code_fix_claim(remark, n=n_for_claim())
        return {"axis": "code", "card": got["card"], "placed": got["placed"],
                "tid": got["tid"], "why": got["why"]}
    if append_rule is None:
        # БОЕВОЙ МАРШРУТ (05.09.2026): урок ложится КАНДИДАТОМ в базу уроков. Плоская книга здесь
        # не пишется ни байтом и `suggest.append_playbook_rule` не зовётся ни одной веткой.
        dec = lesson_candidate(remark, who=who, get=get)
        return {"axis": "behavior", "status": dec["status"], "card": dec["card"],
                "n": dec.get("n"), "who": dec.get("who")}
    # СТАРЫЙ СИНК (плоская книга) ЖИВ ТОЛЬКО ПО ЯВНОМУ ИМЕНИ: боевые вызовы `append_rule` не
    # передают, поэтому сюда не попадают. Ветка оставлена целой сознательно — перенос уже
    # накопленных правил книги в базу уроков это ОТДЕЛЬНОЕ задание, и до него код, умеющий
    # писать книгу, нужен живым и покрытым регрессом. Удалять её здесь нельзя.
    status = append_rule(remark)
    num = None
    if status in ("added", "duplicate"):
        (mark or mark_source)(remark)
        # номер — как у кнопочного пути (apply_lessons): текстовый «урок: …» равноправен,
        # владелец должен видеть #N сразу, чтобы «отмени урок N» работал без похода в /rules
        num = _rule_number(remark, list_rules)
        tag = f"урок #{num}: " if num else ""
        card = (f"✅ Принято: {tag}{remark} → записано в книгу правил (источник «{TRAINER_SOURCE}»), "
                "применится со следующего ответа.")
        # ПОСЛЕ ЗАПИСИ И ПОСЛЕ КАРТОЧКИ — регрессия: корпус прогонится в ЧУЖОМ процессе и отдельной
        # строкой скажет, не сломало ли новое правило то, что вчера было зелёным. Нажатие
        # владельца этим не удлиняется ни на прогон (см. `_regress_call`).
        # ТОЛЬКО `added`: у `duplicate` книга не изменилась ни на символ, и гнать по ней 7 минут
        # корпуса нечего — измерять было бы ровно то же самое, что уже измерено.
        if status == "added":
            _regress_call(regress, remark, num)
    else:
        card = f"⚠️ Не удалось записать правило (status={status})."
    return {"axis": "behavior", "status": status, "card": card, "n": num}


def apply_lesson(remark, append_rule=None, classify=None, mark=None, list_rules=None,
                 regress=None, who=None, get=None):
    """Провести урок из тренажёра через существующий канал «урок:» (2-я ось):
      • behavior (БОЕВОЙ путь, `append_rule` не назван) → КАНДИДАТ в базу уроков `lesson_store`
        (:func:`lesson_candidate`): вопрос клиента, ответ бота, как правильно, кто записал, когда;
        «почему» пусто и НЕ подставляется. Бот по кандидату не отвечает — действующим он станет
        отдельным действием с непустой причиной. Плоская книга НЕ пишется;
      • behavior (`append_rule` назван ЯВНО — старый синк, живой только в регрессе и для будущего
        переноса книги) → append_playbook_rule + пометка источника «тренажёр»; применится со следующего
        черновика (playbook подмешивается в system-prompt) → dict(axis='behavior', status, card,
        n=<номер правила в книге — тот же, что у кнопочного пути и «отмени урок N»>);
      • code → в playbook НЕ пишем; задача СОБИРАЕТСЯ по канону полосы (:func:`build_claim`),
        проходит ЧУЖИЕ ворота приёма ящика и уходит владельцу карточкой С КНОПКОЙ
        (:func:`code_fix_claim`) → dict(axis='code', card, placed, tid, why). Без «да»
        владельца в очередь не встаёт ничего.
    Записанный урок оси «поведение» ЗАПУСКАЕТ РЕГРЕССИЮ (`lesson_regress.spawn`, 05.09.2026):
    корпус прогоняется в ОТДЕЛЬНОМ отсоединённом процессе и отдельной строкой говорит владельцу
    исход тремя голосами. Нажатие не удлиняется и при упавшем приборе урок всё равно записан.
    append_rule/classify/mark/list_rules/regress инъектируются в тестах; иначе боевые
    suggest/lesson_router/lesson_regress.
    НИКОГДА не бросает (обработчик группы не имеет права упасть на уроке): исключение внутри →
    status='error' + карточка-ошибка; след — в лог процесса, а карточку в TRN пишет вызывающий."""
    try:
        return _apply_lesson(remark, append_rule, classify, mark, list_rules, regress, who, get)
    except Exception as e:
        log.warning("apply_lesson упал: %s: %s", type(e).__name__, e, exc_info=True)
        return {"axis": "behavior", "status": "error",
                "card": f"⚠️ Урок не применён — внутренняя ошибка ({type(e).__name__}), "
                        "см. лог процесса. Правило можно повторить."}


# ====== ОТМЕНА УРОКА ПО НОМЕРУ → ОТЗЫВ В БАЗЕ УРОКОВ (06.09.2026) ==========================
# ЧТО ПОМЕНЯЛОСЬ И ПОЧЕМУ ЭТО НЕ КОСМЕТИКА. С 06.09 (коммит 8bfb55b) секция «Выученные правила»
# в промпте берётся из БАЗЫ уроков (`suggest.load_playbook` → `lesson_store.active`), а плоская
# книга стала СНИМКОМ. «отмени урок N» при этом продолжала вырезать буллет ИЗ КНИГИ — то есть
# отвечала владельцу «↩️ Отменил урок #N», ничего не меняя в ответе бота: урок оставался
# действующим в базе и продолжал ехать в каждый промпт. Успешная карточка при неснятом уроке
# опаснее отказа, поэтому команда переведена на `lesson_store.withdraw` — единственный законный
# отзыв. Своего пути к таблице здесь нет и быть не должно.
#
# ЧЕТЫРЕ ИСХОДА НАЗЫВАЮТСЯ РАЗНЫМИ СЛОВАМИ, потому что чинятся по-разному:
#   `withdrawn`  — снят сейчас (строка ОСТАЛАСЬ, поменялось только состояние);
#   `already`    — этот номер уже снят раньше; повторная отмена НИЧЕГО не ломает и не врёт;
#   `not_found`  — такого номера в базе нет вовсе (внятный отказ, а не тихое «ок»);
#   `no_store`   — таблицы уроков нет на диске (снимать нечего, и это не то же самое, что `not_found`).
# Плюс `denied`/`no_author` до всякого касания таблицы и `error` — падение внутри.
#
# ПРАВО — ТО ЖЕ САМОЕ, ЧТО У ПЕРЕВОДА КАНДИДАТА В ДЕЙСТВУЮЩИЕ (`moderation_core.may_write_rule`,
# fail-closed: пустой список прав = НИКОМУ), и это решение о симметрии, а не осторожность:
# перевод кандидата и отзыв действующего — два конца одной ручки, которой владелец меняет то, по
# чему бот отвечает ВСЕМ клиентам. Разрешить отзыв шире, чем перевод, значило бы отдать посторонним
# право стереть правило, записать которое они не вправе.
#
# НОМЕР ЗДЕСЬ — НОМЕР БАЗЫ, А НЕ НОМЕР БУЛЛЕТА /rules. На день перевода они совпадают (переезд
# книги дал урокам базы номера 1…9 в порядке буллетов), но расходятся с первым же новым уроком:
# база нумерует навсегда и номеров не переиспользует, а книга перенумеровывает буллеты при каждом
# удалении. Свести ПОКАЗ и СНЯТИЕ на базу — отдельное решение, названное в `suggest.py:1046`;
# до него карточка НАЗЫВАЕТ ТЕКСТ снятого урока, чтобы промах номером был виден сразу.
ACT_WITHDRAWN = "снят"                 # что за движение ушло в регрессию (add-сторона — «записан»)

STATUS_WITHDRAWN = "withdrawn"
STATUS_ALREADY = "already"
STATUS_NOT_FOUND = "not_found"
STATUS_NO_STORE = "no_store"


def _default_withdraw(number, path=None, now=None):
    import lesson_store
    return lesson_store.withdraw(number=number, path=path, now=now)


def _default_find_lesson(number, path=None):
    """Урок базы по номеру → (Lesson|None, есть ли таблица). Только ЧТЕНИЕ: нужно ради текста
    урока (карточка и снимок-книга) и ради состояния (уже снят?). Отзыв всё равно делает
    `withdraw` — второго писателя таблицы здесь нет."""
    import lesson_store
    store = lesson_store.load(path)
    for les in store.lessons:
        if les.number == int(number):
            return les, store.exists
    return None, store.exists


def _book_forget(text, remove_rule=None, list_rules=None, unmark=None):
    """Снятый урок → убрать его буллет из КНИГИ-СНИМКА (и пометку источника). → dict(status, n).

    ЗАЧЕМ ЭТО ВООБЩЕ ЕСТЬ, если ответ читается из базы: у чтения базы есть ТРЕТИЙ ИСХОД —
    «база не прочитана» (`suggest.active_lesson_bullets` → `read_ok=False`), и тогда в промпт
    идёт книга-снимок. Оставленный в ней буллет вернул бы снятый урок в ответ ровно в тот момент,
    когда база недоступна, — то есть отмена держалась бы только на исправности файла.

    СОВПАДЕНИЕ ТОЛЬКО ТОЧНОЕ (нормализованный текст равен), и это не придирчивость: нечёткий
    матчинг `_rules_similar` (Jaccard ≥ 0.6) на живой книге может указать на СОСЕДНЕЕ правило, а
    цена промаха здесь — молча вырезанная чужая строка. Совпало не ровно одно → книгу НЕ ТРОГАЕМ
    и говорим об этом (`skipped`/`ambiguous`), потому что не тронуть дешевле, чем испортить."""
    body = " ".join(str(text or "").split())
    if not body:
        return {"status": "no_text", "n": None}
    import suggest
    list_rules = list_rules or suggest.list_playbook_rules
    remove_rule = remove_rule or suggest.remove_playbook_rule
    key = _norm_rule(body)
    hits = [r for r in (list_rules() or []) if _norm_rule(r.get("rule")) == key]
    if len(hits) == 0:
        return {"status": "skipped", "n": None}          # в книге-снимке этого правила нет
    if len(hits) > 1:
        return {"status": "ambiguous", "n": None}        # одинаковых два — не гадаем
    res = remove_rule(hits[0]["n"]) or {}
    if res.get("status") == "removed":
        (unmark or unmark_source)(res.get("rule") or body)
    return {"status": res.get("status"), "n": res.get("n")}


def _cancel_lesson(n, who=None, may_write=None, withdraw=None, find=None, remove_rule=None,
                   list_rules=None, unmark=None, regress=None, get=None, now=None, path=None):
    """Ядро cancel_lesson (может бросить — снаружи fail-safe обёртка)."""
    num = _to_int(n)
    if num is None:
        return {"status": "error", "n": None, "who": None,
                "card": f"⚠️ Не разобрал номер урока в «{n}» — назови число: «отмени урок 7»."}
    # АВТОР — ДО всякого касания таблицы: право судится по имени, а безымянного движения у
    # действующего урока быть не может.
    #
    # ЧИТАЕМ НЕ ГАСЯ (`peek_actor`), и у этого выбора названы обе цены. Гашение отняло бы автора
    # у следующего «урок: …» — запись автора одноразовая и приготовлена ИМЕННО для урока, а
    # отмена не вправе портить чужое движение. Плата за peek — имя «липнет» на свой TTL (15 мин):
    # внутри окна отмену подпишет тот, кто нажимал кнопку. Радиус этой платы ограничен тем, что
    # отзыв НЕ подписывает строку именем вовсе (в состоянии живут разрез и штамп), а на входе в
    # команду стои́т ещё и проверка вызывающего. Липкое имя тут решает «пускать ли», а не «кто
    # это сделал», и потому не создаёт той лжи, ради которой запись автора одноразова.
    author = (who or "").strip().lstrip("@") or (peek_actor(now=now, get=get) or "")
    if not author:
        return {"status": STATUS_NO_AUTHOR, "n": num, "who": None,
                "card": f"⛔ Урок #{num} НЕ отменён: не видно, КТО отменяет. Отзыв действующего "
                        "урока идёт под тем же правом, что и перевод кандидата в действующие, а "
                        "право судится по имени. Нажми «🎓 Обучить»/«✍ другое» в модерботе (кнопка "
                        "называет автора) и повтори команду."}
    if not (may_write or _default_may_write)(author):
        return {"status": STATUS_DENIED, "n": num, "who": author,
                "card": f"⛔ Нет прав на отмену уроков — урок #{num} НЕ снят. Отзыв действующего "
                        "урока требует того же права, что и запись."}

    les, exists = (find or _default_find_lesson)(num, path)
    res = (withdraw or _default_withdraw)(num, path=path, now=now)
    text = " ".join(str(getattr(les, "correct", "") or "").split())
    short = text if len(text) <= 120 else text[:119] + "…"

    if res.marked:
        book = _book_forget(text, remove_rule=remove_rule, list_rules=list_rules, unmark=unmark)
        reg = _regress_call(regress, text or ("урок #%d" % num), num, act=ACT_WITHDRAWN)
        card = (f"↩️ Урок #{num} СНЯТ: {short}\n"
                f"📌 Строка из базы НЕ удалена (было строк {res.lines_before}, стало "
                f"{res.lines_after}) — она осталась с пометкой «снят», и по ней видно, когда и "
                "каким разрезом его сняли. Бот по этому уроку больше не отвечает.")
        if book.get("status") == "removed":
            card += " Буллет книги-снимка убран заодно."
        elif book.get("status") == "ambiguous":
            card += " В книге-снимке таких буллетов несколько — её не трогал, поправь руками."
        return {"status": STATUS_WITHDRAWN, "n": num, "who": author, "rule": text,
                "lines_before": res.lines_before, "lines_after": res.lines_after,
                "book": book, "regress": reg, "card": card}

    if res.already:
        state = str(getattr(les, "state", "") or "")
        return {"status": STATUS_ALREADY, "n": num, "who": author, "rule": text,
                "lines_before": res.lines_before, "lines_after": res.lines_after,
                "card": f"✅ Урок #{num} уже отменён раньше (состояние «{state}») — повторная "
                        f"отмена ничего не меняет и ничего не ломает: {short}"}

    if not exists:
        return {"status": STATUS_NO_STORE, "n": num, "who": author,
                "card": f"⚠️ Базы уроков нет на диске — отменять нечего, урок #{num} не снят. "
                        "Это НЕ «урока нет»: таблица не заведена вовсе."}

    return {"status": STATUS_NOT_FOUND, "n": num, "who": author,
            "lines_before": res.lines_before, "lines_after": res.lines_after,
            "card": f"⚠️ Урока #{num} в базе НЕТ — ничего не снято. Номер здесь — номер БАЗЫ "
                    "уроков (его называет карточка записи), а не порядковый номер буллета /rules."}


def cancel_lesson(n, who=None, may_write=None, withdraw=None, find=None, remove_rule=None,
                  list_rules=None, unmark=None, regress=None, get=None, now=None, path=None):
    """«отмени урок N» — ОТЗЫВ УРОКА #N В БАЗЕ (`lesson_store.withdraw`, разрез «урок»).

    N — номер БАЗЫ уроков. Право — `moderation_core.may_write_rule` (то же, что у перевода
    кандидата в действующие; пустой список прав = НИКОМУ). Строка таблицы НЕ УДАЛЯЕТСЯ: меняется
    только состояние, и `lines_before`/`lines_after` в ответе это доказывают числом.
    → dict(status, card, n, who, …) со статусами `withdrawn` | `already` | `not_found` |
    `no_store` | `denied` | `no_author` | `error`.

    may_write/withdraw/find/remove_rule/list_rules/unmark/regress/get/path инъектируются в тестах;
    иначе боевые `moderation_core` + `lesson_store` + `suggest` + `lesson_regress`.
    НИКОГДА не бросает: исключение внутри → status='error' + карточка-ошибка (след — в лог
    процесса; карточку в TRN пишет вызывающий)."""
    try:
        return _cancel_lesson(n, who, may_write, withdraw, find, remove_rule, list_rules,
                              unmark, regress, get, now, path)
    except Exception as e:
        log.warning("cancel_lesson(#%s) упал: %s: %s", n, type(e).__name__, e, exc_info=True)
        return {"status": "error", "n": _to_int(n), "who": None,
                "card": f"⚠️ Урок #{n} не отменён — внутренняя ошибка ({type(e).__name__}), "
                        "см. лог процесса. Команду можно повторить."}


# ====== ПЕРЕВОД КАНДИДАТА В ДЕЙСТВУЮЩИЕ И ОТКАТ ПЕРЕВОДА (09.09.2026) =======================
# ЧЕГО НЕ ХВАТАЛО, НАЗВАНО ЧИСЛОМ, А НЕ СЛОВОМ «недоделано». `lesson_store.promote` живёт с
# 05.09, и до 09.09 его не звал НИ ОДИН файл дерева, кроме тестов (`test_lesson_write`,
# `test_lesson_cancel`, `test_lesson_read` — грепом по дереву верхнего уровня совпадений вне них
# ноль). Следствие было не косметическим: кнопка «🎓 Обучить» писала КАНДИДАТА, кандидата не
# читает `active()`, а перевести его в действующие было НЕЧЕМ — то есть вердикт владельца не
# становился правилом ни при каком его старании, и петля обучения не замыкалась вовсе.
#
# ПЕРЕВОД — ОТДЕЛЬНОЕ ДЕЙСТВИЕ, А НЕ ХВОСТ ВЕРДИКТА, и это решение, а не удобство. Нажатие
# «Обучить» по-прежнему кладёт кандидата и НЕ включает ничего (`lesson_candidate` не зовёт
# `promote` ни одной веткой — заперто тестом). Цена ошибки у записи и у включения разная:
# записать замечание должно быть дёшево, а начать отвечать по нему ВСЕМ клиентам — дорого, и
# второе требует отдельного «да» с причиной.
#
# ПРАВО — ТО ЖЕ, ЧТО У ЗАПИСИ И У ОТМЕНЫ: `moderation_core.may_write_rule` (fail-closed, пустой
# список прав = НИКОМУ). Своего списка здесь нет и быть не должно: три конца одной ручки, которой
# владелец меняет то, по чему бот отвечает всем клиентам, обязаны судиться одним механизмом.
#
# СЛЕД ИЗ ЧЕТЫРЁХ ЧАСТЕЙ (автор, время, номер, откат) пишет ХРАНИЛИЩЕ, а не эта функция:
# `lesson_store.promote` требует имя автора сам и сам дописывает строку следа. Здесь только
# гейт права, вопрос-ответ владельцу словами и карточка.
STATUS_PROMOTED = "promoted"
STATUS_NO_WHY = "no_why"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_REFUSED = "refused"


def _default_promote(number, why, who, path=None, now=None):
    import lesson_store
    return lesson_store.promote(number, why=why, who=who, path=path, now=now)


def _default_rollback(number, who, path=None, now=None):
    import lesson_store
    return lesson_store.rollback(number, who=who, path=path, now=now)


def _actor_for(who, get=None, now=None):
    """Имя того, кто делает движение правилом → строка (пусто = не видно КТО).

    Читаем НЕ ГАСЯ (`peek_actor`), ровно по доводу `_cancel_lesson`: запись автора одноразова и
    приготовлена для следующего «урок: …», а перевод не вправе портить чужое движение."""
    name = (who or "").strip().lstrip("@")
    if name:
        return name
    return (peek_actor(now=now, get=get) or "").strip().lstrip("@")


def _promote_lesson(n, why=None, who=None, may_write=None, promote=None, get=None, now=None,
                    path=None):
    """Ядро promote_lesson (может бросить — снаружи fail-safe обёртка)."""
    num = _to_int(n)
    if num is None:
        return {"status": "error", "n": None, "who": None,
                "card": f"⚠️ Не разобрал номер урока в «{n}» — назови число: "
                        "«урок включи 7: причина словами»."}
    author = _actor_for(who, get=get, now=now)
    if not author:
        return {"status": STATUS_NO_AUTHOR, "n": num, "who": None,
                "card": f"⛔ Урок #{num} НЕ включён: не видно, КТО включает. Перевод кандидата в "
                        "действующие идёт под тем же правом, что запись и отмена, а право "
                        "судится по имени."}
    if not (may_write or _default_may_write)(author):
        return {"status": STATUS_DENIED, "n": num, "who": author,
                "card": f"⛔ Нет прав на перевод уроков — урок #{num} НЕ включён. Включение "
                        "требует того же права, что и запись."}
    # ПРИЧИНА СПРАШИВАЕТСЯ ЗДЕСЬ ТОЖЕ, и это не дубль проверки хранилища: владельцу нужен внятный
    # ответ на СВОИХ словах, а хранилище проверит то же самое ещё раз — оно не верит никому.
    reason_text = " ".join(str(why or "").split()).strip()
    res = (promote or _default_promote)(num, reason_text, author, path=path, now=now)
    if not res.ok:
        status = STATUS_NO_WHY if "причина не названа" in res.reason else STATUS_REFUSED
        return {"status": status, "n": num, "who": author, "reason": res.reason,
                "card": f"⛔ Урок #{num} НЕ включён: {res.reason}."}
    return {"status": STATUS_PROMOTED, "n": num, "who": author, "why": res.why,
            "lines_before": res.lines_before, "lines_after": res.lines_after,
            "card": f"✅ Урок #{num} ВКЛЮЧЁН — бот отвечает по нему со следующего ответа.\n"
                    f"📌 Причина: {res.why}\n"
                    f"📌 След: включил @{author}, {res.stamp}, номер {num}. Вернуть как было — "
                    f"«урок откати {num}» (строк было {res.lines_before}, стало "
                    f"{res.lines_after} — ничего не потеряно)."}


def promote_lesson(n, why=None, who=None, may_write=None, promote=None, get=None, now=None,
                   path=None):
    """«урок включи N: причина» — ПЕРЕВОД КАНДИДАТА #N В ДЕЙСТВУЮЩИЕ (`lesson_store.promote`).

    N — номер БАЗЫ уроков (его называет карточка записи кандидата). Право —
    `moderation_core.may_write_rule` (то же, что у записи и у «отмени урок N»; пустой список
    прав = НИКОМУ). Причина ОБЯЗАТЕЛЬНА и не подставляется ниоткуда: пустая → отказ словами.
    → dict(status, card, n, who, …) со статусами `promoted` | `no_why` | `refused` | `denied` |
    `no_author` | `error`.

    may_write/promote/get/now/path инъектируются в тестах; иначе боевые `moderation_core` +
    `lesson_store`. НИКОГДА не бросает: исключение внутри → status='error' + карточка-ошибка."""
    try:
        return _promote_lesson(n, why, who, may_write, promote, get, now, path)
    except Exception as e:
        log.warning("promote_lesson(#%s) упал: %s: %s", n, type(e).__name__, e, exc_info=True)
        return {"status": "error", "n": _to_int(n), "who": None,
                "card": f"⚠️ Урок #{n} не включён — внутренняя ошибка ({type(e).__name__}), "
                        "см. лог процесса. Команду можно повторить."}


def _rollback_promotion(n, who=None, may_write=None, rollback=None, get=None, now=None,
                        path=None):
    """Ядро rollback_promotion (может бросить — снаружи fail-safe обёртка)."""
    num = _to_int(n)
    if num is None:
        return {"status": "error", "n": None, "who": None,
                "card": f"⚠️ Не разобрал номер урока в «{n}» — назови число: «урок откати 7»."}
    author = _actor_for(who, get=get, now=now)
    if not author:
        return {"status": STATUS_NO_AUTHOR, "n": num, "who": None,
                "card": f"⛔ Откат перевода урока #{num} НЕ сделан: не видно, КТО откатывает."}
    if not (may_write or _default_may_write)(author):
        return {"status": STATUS_DENIED, "n": num, "who": author,
                "card": f"⛔ Нет прав — откат перевода урока #{num} НЕ сделан."}
    res = (rollback or _default_rollback)(num, author, path=path, now=now)
    if not res.ok:
        return {"status": STATUS_REFUSED, "n": num, "who": author, "reason": res.reason,
                "card": f"⛔ {res.reason}."}
    return {"status": STATUS_ROLLED_BACK, "n": num, "who": author, "state": res.state,
            "lines_before": res.lines_before, "lines_after": res.lines_after,
            "card": f"↩️ Перевод урока #{num} ОТКАЧЕН: состояние вернулось в «{res.state}», "
                    f"причина — в то же значение, что была до включения. Бот по этому уроку "
                    f"больше не отвечает.\n"
                    f"📌 След: откатил @{author}, {res.stamp}, номер {num} (строк было "
                    f"{res.lines_before}, стало {res.lines_after})."}


def rollback_promotion(n, who=None, may_write=None, rollback=None, get=None, now=None, path=None):
    """«урок откати N» — ОТКАТ ПЕРЕВОДА урока #N (`lesson_store.rollback`).

    Возвращает РОВНО то состояние строки, которое было до перевода (состояние и «почему» теми же
    байтами), а не «снимает» урок: снятие — это «отмени урок N», и оно кладёт третье, НОВОЕ
    состояние `снят(...)`, которого до перевода не было. Право — то же `may_write_rule`.
    → dict(status, card, n, who, …) со статусами `rolled_back` | `refused` | `denied` |
    `no_author` | `error`. НИКОГДА не бросает."""
    try:
        return _rollback_promotion(n, who, may_write, rollback, get, now, path)
    except Exception as e:
        log.warning("rollback_promotion(#%s) упал: %s: %s", n, type(e).__name__, e, exc_info=True)
        return {"status": "error", "n": _to_int(n), "who": None,
                "card": f"⚠️ Откат перевода урока #{n} не сделан — внутренняя ошибка "
                        f"({type(e).__name__}), см. лог процесса. Команду можно повторить."}
