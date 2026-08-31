# -*- coding: utf-8 -*-
"""pricing_advisor.py — C0: ЧИСТЫЙ СОВЕТНИК ПО ЦЕНЕ. ТЕНЕВОЙ РЕЖИМ, НАРУЖУ НЕ ХОДИТ.

ЧТО ЭТО. Одна чистая функция `recommend_price(...)`: на вход — УЖЕ СНЯТЫЕ факты (котировка,
разобранный диалог, статистика прошлых сделок, политика владельца) плюс инъектированный момент
`as_of`; на выход — машиночитаемый пакет-рекомендация схемы `turbobaby/price_recommendation/v1`.
Ни один вход не подтягивается неявно: у модуля нет ни одного источника данных, кроме аргументов.

ЧЕГО ЭТОТ МОДУЛЬ НЕ УМЕЕТ ПО УСТРОЙСТВУ (и это предмет теста, а не обещание):
  • он НЕ считает и НЕ выводит новую коммерческую цену. Единственное число, которое он вправе
    назвать целью, — это ПЕРЕДАННАЯ ему `list_day_price`. Инвариант всего C0 одной строкой:
    `target is None or target == quote_facts["list_day_price"]`;
  • у него нет сети, файлов, окружения, секретов, подпроцессов, очереди, мозга, ленты, базы,
    часов и случайных чисел. Список импортов верхнего уровня закрыт двумя именами (`json`, `re`)
    и держится тестом разбором дерева исходника;
  • он ничего не отправляет, не публикует, не бронирует и не коммитит. Итог — словарь; что с ним
    делать, решает вызывающий, и в пакете всегда стоит `approval_required = true`.

ГРАНИЦА «blocked» ПРОТИВ «unknown» (правило одной фразой, чтобы не спорить каждый раз):
  • **blocked** — факт ЕСТЬ и он дисквалифицирующий: валюта не та, юнит доказанно занят, слепок
    протух, политика владельца несогласована, в диалог приехал сырой/чувствительный ключ;
  • **unknown** — факта НЕТ или он неразрешим: модель не названа, поколение не разведено, нет
    дат/срока/цены/улик, наличие не подтверждено, окно котировки уже прошло или уже началось.
Оба исхода дают `target = null`. Ветки «не смог проверить → значит хорошо» нет ни одной.

ЧИСЛО ОБЯЗАНО БЫТЬ КОНЕЧНЫМ (`_is_finite`, заведено 31.08.2026 по независимой пробе). `NaN` и
`±Inf` — это `float`, и наивная проверка «число ли» их пропускает, после чего они ведут себя не
как числа, а как ГЛУШИТЕЛЬ СРАВНЕНИЙ: у `NaN` ложны разом `x <= 0`, `x > порог` и `abs(x) > порог`,
поэтому одно такое значение проходит все пороги подряд и садится в пакет. Пробой это дало три
разных ложных зелёных из одного корня: `list_day_price = NaN` становился ЦЕЛЬЮ, `NaN` в медиане
прошлых сделок засчитывался ПОДТВЕРЖДЕНИЕМ и поднимал уверенность до `high`, `NaN` в пороге
возраста слепка ОТКЛЮЧАЛ проверку протухания. Поэтому конечность проверяется у каждого числового
входа, способного попасть в пакет или в сравнение с порогом, — денег, процентов, возрастов, медиан
и бюджета клиента, — а не только у цены. Замок сверху: `to_json` печатает со `allow_nan=False`,
то есть при любом промахе выше текст пакета НЕ РОДИТСЯ вовсе вместо выдачи невалидного JSON.

УВЕРЕННОСТЬ НАЗЫВАЕТСЯ ТОЛЬКО СЛОВАМИ СХЕМЫ `high | medium | low | blocked` (`CONFIDENCE_LEVELS`).
Пятого слова нет: `blocked` — это отказ по дисквалифицирующему факту, `low` — всё, что не сложилось
(в том числе `unknown`), и потребителю пакета не нужно знать про шестое состояние, которого схема
не объявляла.

ДИАЛОГ ПРИНИМАЕТСЯ ТОЛЬКО ЦИФРАМИ И ФЛАГАМИ. У `dialog_facts` строковых значений НЕ БЫВАЕТ
вовсе — разрешены `bool`, число и `None`. Этим целый класс «сырой текст клиента протёк в пакет»
закрыт по устройству, а не проверкой на список слов: строке просто некуда лечь. Сверх того ключи
сверяются с белым списком, а незнакомый ключ ещё и классифицируется — чувствительный он или
просто чужой; наружу при этом уходит ТОЛЬКО наша метка категории, никогда не сам ключ и никогда
не значение.

ОТКУДА ВЗЯТЫ ИМЕНА И ЧИСЛА (копируем свою полосу, а не выдумываем схему):
  • статусы наличия `ok | none_available | no_candidates | error` — дословно исходы
    `pricing.quote_for_model`; проверенным считается ровно `ok`;
  • поколение не угадывается: `price_source._pick_generation` здесь зеркалится состоянием
    `generation_status`, и «не разведено» — это отказ, а не выбор старой строки листа. Цена
    ошибки названа деньгами в самом `price_source.py`: старое поколение XMAX стои́т 557 ฿/сут
    против 662 у нового;
  • порог возраста слепка 14 суток — копия `price_freshness.MAX_AGE_DEFAULT` вместе с её
    обоснованием (самый длинный объявленный владельцем круг пересмотра цен);
  • допуск ±1 сутки между сроком из котировки и сроком из слов клиента — копия
    `pricing.sanity_days_ok`.

ПРО СЛОВАРЬ ОТКАЗА (чтобы чужой греп не родил ложный диагноз). Слова `telegram`, `whatsapp`,
`passport`, `card` и им подобные в этом файле есть — все до единого внутри `SENSITIVE_TOKENS`,
то есть в списке того, что модуль ОТВЕРГАЕТ. Это словарь отказа, а не поверхность: ни импорта,
ни вызова, ни адреса за ними нет. Проверять надо не подстроку, а импорты и вызовы — тест так и
делает, разбором дерева исходника.

ЧЕГО ЗДЕСЬ СОЗНАТЕЛЬНО НЕТ: собственных границ цены. `floor`/`ceiling` не бывают ненулевыми
иначе как из явной и согласованной `owner_policy` — C0 не изобретает владельцу лимитов. Если
переданная цена листа выходит за его же границы, мы не подгоняем её ни в одну сторону: это
исход «unknown + manager_review», то есть работа человеку.

ЗАПУСК ПРОВЕРКИ (из корня репозитория):
    venv\\Scripts\\python.exe -m unittest -v test_pricing_advisor
"""

import json
import re

# ── Схема пакета и словарь исходов ────────────────────────────────────────────────────────────
SCHEMA = "turbobaby/price_recommendation/v1"
MODE = "shadow"
CURRENCY = "THB"

STATUS_OK = "ok"
STATUS_UNKNOWN = "unknown"
STATUS_BLOCKED = "blocked"
STATUSES = (STATUS_OK, STATUS_UNKNOWN, STATUS_BLOCKED)

ACTION_OFFER_LIST = "offer_list"
ACTION_OFFER_ALTERNATIVE = "offer_alternative"
ACTION_MANAGER_REVIEW = "manager_review"
ACTION_ASK_MISSING_FACT = "ask_missing_fact"
ACTIONS = (ACTION_OFFER_LIST, ACTION_OFFER_ALTERNATIVE, ACTION_MANAGER_REVIEW,
           ACTION_ASK_MISSING_FACT)

# ── Уверенность. Белый список схемы: пятого слова у C0 нет ни на одной ветке. ─────────────────
CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"
CONF_BLOCKED = "blocked"
CONFIDENCE_LEVELS = (CONF_HIGH, CONF_MEDIUM, CONF_LOW, CONF_BLOCKED)

RESULT_KEYS = ("schema", "status", "mode", "as_of", "facts", "history", "dialog",
               "recommendation", "confidence", "reason_codes", "unknowns", "evidence_refs",
               "approval_required")

# ── Исходы наличия. Дословно `pricing.quote_for_model`; проверенным зовём ровно первый. ───────
AVAIL_OK = "ok"
AVAIL_NONE_AVAILABLE = "none_available"
AVAIL_KNOWN = (AVAIL_OK, AVAIL_NONE_AVAILABLE, "no_candidates", "error")

# ── Поколение. Зеркало fail-closed из `price_source._pick_generation`. ────────────────────────
GEN_RESOLVED = "resolved"
GEN_NOT_APPLICABLE = "not_applicable"
GEN_AMBIGUOUS = "ambiguous"
GEN_STATES = (GEN_RESOLVED, GEN_NOT_APPLICABLE, GEN_AMBIGUOUS)

# ── Пороги. Каждый — либо копия живого числа полосы, либо явно наша страховка. ────────────────
SNAPSHOT_MAX_AGE_DAYS_DEFAULT = 14.0   # копия price_freshness.MAX_AGE_DEFAULT
LONG_TERM_DAYS_DEFAULT = 180           # из постановки задачи C0
DAYS_TOLERANCE = 1                     # копия допуска pricing.sanity_days_ok
# Порог «расхождение прошлых сделок с листом существенно». Это НЕ граница цены и не лимит
# владельца, а СПУСКОВОЙ КРЮЧОК показа человеку, поэтому ошибаться ему положено в сторону
# лишнего показа. Число не круглое от балды: живые ручки сезона листа стоят на 0.15/0.15/0.25
# (price_source.json → freshness.handles), значит расхождение меньше одной типовой ручки
# объясняется поворотом ручки и конфликтом не является.
HISTORY_CONFLICT_PCT_DEFAULT = 15.0
HISTORY_MIN_DEALS_DEFAULT = 3          # тоньше — это не улика, а шум
# Предел возраста прошлых сделок, при котором им ещё позволено ПОДТВЕРЖДАТЬ сегодняшний лист.
# Число не новое: это тот же круг пересмотра цен, что и у слепка (`price_freshness.MAX_AGE_DEFAULT`),
# и обоснование у него ровно то же. Сделка старше одного полного круга пересмотра могла быть закрыта
# по цене, которую владелец с тех пор уже поменял, — подтверждать ею сегодняшнюю цену нечестно.
# Ручка своя, а не общая со слепком, сознательно: владелец, отключивший проверку возраста слепка,
# не должен этим молча отключить и проверку возраста истории.
HISTORY_MAX_AGE_DAYS_DEFAULT = 14.0

# ── Белые списки входов. Незнакомый ключ = отказ, а не «пропустим мимо». ──────────────────────
QUOTE_KEYS = ("model", "unit_name", "generation", "generation_status", "list_day_price",
              "currency", "availability", "date_start", "date_end", "days", "snapshot_on",
              "evidence_refs")
DIALOG_KEYS = ("cheaper_requested", "discount_requested", "bundle_requested", "agent_case",
               "units_requested", "term_days", "stated_budget_thb_per_day")
DIALOG_FLAGS = ("cheaper_requested", "discount_requested", "bundle_requested", "agent_case")
HISTORY_KEYS = ("deals_count", "median_day_price_thb", "window_days", "source", "fresh_as_of")
POLICY_KEYS = ("floor_thb_per_day", "ceiling_thb_per_day", "discount_approved",
               "discount_max_pct", "snapshot_max_age_days", "long_term_days",
               "history_conflict_pct", "history_min_deals", "history_max_age_days")

# ── Чувствительные категории. Ключ диалога, попавший сюда, отказывает заходу целиком; наружу
#    уходит ИМЯ КАТЕГОРИИ из этого словаря, а не ключ клиента и тем более не его значение. ─────
SENSITIVE_TOKENS = {
    "raw_text": ("raw", "text", "transcript", "verbatim", "message", "messages", "msg",
                 "chat", "body", "content", "quote", "reply", "utterance"),
    "identity": ("name", "firstname", "lastname", "surname", "username", "nick", "nickname",
                 "handle", "user", "client", "customer", "person", "profile"),
    "contact": ("phone", "tel", "mobile", "whatsapp", "telegram", "viber", "instagram",
                "wechat", "email", "mail", "contact", "contacts"),
    "document": ("passport", "document", "doc", "id", "idcard", "visa", "licence", "license",
                 "driver"),
    "nationality": ("nationality", "citizenship", "citizen", "country", "nation", "origin",
                    "ethnicity", "race"),
    "language": ("language", "lang", "locale", "speaks", "translated"),
    "address": ("address", "addr", "street", "hotel", "villa", "room", "geo", "gps", "lat",
                "lon", "lng", "coords", "coordinates", "location", "district", "zone"),
    "payment": ("payment", "pay", "card", "iban", "cvv", "bank", "crypto", "wallet", "paypal",
                "invoice"),
    "device": ("device", "ip", "useragent", "ua", "browser", "imei", "mac", "fingerprint"),
    "wealth": ("wealth", "rich", "income", "salary", "affluence", "solvency", "vip", "premium",
               "expensive", "poor"),
}
SENSITIVE_CATEGORIES = tuple(sorted(SENSITIVE_TOKENS))

REDACTED = "<redacted>"

_SAFE_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]{0,32}$")
_SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9Ѐ-ӿ_.:/#()\[\]@,\- ]{1,200}$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH_LEN = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


# ─────────────────────────────── мелкие чистые помощники ──────────────────────────────────────

_INF = float("inf")


def _is_number(value):
    """Число ли это. `bool` числом НЕ считаем: True прошёл бы как 1 и назвал бы цену."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value):
    """Число И ОНО КОНЕЧНО. Это, а не `_is_number`, — сито для всего, что попадёт в пакет.

    Проверка написана сравнениями, а не библиотекой: календаря здесь нет по той же причине, по
    которой нет и математического модуля — список импортов закрыт и держится тестом. `NaN` ловится
    единственным своим свойством (он не равен самому себе), бесконечности — прямым сравнением.

    ЗАЧЕМ ЭТО ОТДЕЛЬНОЕ СИТО. `NaN` опасен не тем, что он «плохое число», а тем, что он ЛОЖЕН В
    ЛЮБОМ СРАВНЕНИИ: и `x <= 0`, и `x > порог` разом неверны, поэтому проверка «не меньше нуля и не
    больше порога» пропускает его как образцовое значение. Ровно так один `NaN` проходил у нас три
    разные заставы подряд."""
    if not _is_number(value):
        return False
    if value != value:          # NaN — единственное значение, не равное самому себе
        return False
    return -_INF < value < _INF


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_from_civil(y, m, d):
    """Сутки от 1970-01-01 чистой арифметикой (алгоритм Хиннанта).

    Календарной библиотеки здесь нет намеренно: у неё в соседнем методе живёт «сейчас», а этот
    модуль обязан быть проверяемо слеп к текущему моменту. Инъектированный `as_of` — единственный
    момент, который C0 знает."""
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _parse_day(value):
    """«ГГГГ-ММ-ДД» (допускается хвост после «T») → номер суток или None. Дата обязана быть
    настоящей: 2026-02-30 и 2026-13-01 отвергаются, високосность считается."""
    if not isinstance(value, str):
        return None
    head = value.split("T", 1)[0].strip()
    hit = _DATE_RE.match(head)
    if hit is None:
        return None
    y, m, d = int(hit.group(1)), int(hit.group(2)), int(hit.group(3))
    if not 1 <= m <= 12:
        return None
    limit = _MONTH_LEN[m - 1] + (1 if (m == 2 and _leap(y)) else 0)
    if not 1 <= d <= limit:
        return None
    return _days_from_civil(y, m, d)


def _category_of_key(key):
    """Чувствительная категория ключа или None. Разбор по ТОКЕНАМ, а не по подстроке: подстрока
    ловила бы «units_requested» на «unit» и врала бы каждый заход."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", str(key).lower()) if t]
    for category in SENSITIVE_CATEGORIES:
        marks = SENSITIVE_TOKENS[category]
        for token in tokens:
            if token in marks:
                return category
    return None


def _safe_label(key):
    """Имя чужого ключа, которое НЕ СТЫДНО показать: короткое, из букв, не чувствительное.
    Всё прочее уходит меткой `<redacted>` — вплоть до того, что клиент положил телефон в ИМЯ
    ключа, а не в значение."""
    if isinstance(key, str) and _SAFE_LABEL_RE.match(key) and _category_of_key(key) is None:
        return key
    return REDACTED


def _safe_text(value, limit=200):
    """Строка-улика безопасной формы или None. Ни переводов строк, ни длинных полотен — сюда
    кладут ссылки на артефакты и имена листов, а не переписку."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit:
        return None
    return text if _SAFE_TEXT_RE.match(text) else None


def _as_mapping(value):
    """(словарь, ошибка). None считаем «не передано» и заменяем пустым; не-словарь — отказ."""
    if value is None:
        return {}, None
    if isinstance(value, dict):
        return value, None
    return {}, "input_not_mapping"


# ──────────────────────────────── разбор политики владельца ───────────────────────────────────

def _read_policy(raw, blocked):
    """Политика владельца → нормализованные ручки. Несогласованность = `blocked`, а не «возьмём
    дефолт»: молча подставить свой порог вместо кривого владельцева — это и есть изобретение
    лимита, которое C0 запрещено."""
    policy = {
        "floor": None, "ceiling": None, "discount_approved": False, "discount_max_pct": None,
        "snapshot_max_age_days": SNAPSHOT_MAX_AGE_DAYS_DEFAULT,
        "long_term_days": LONG_TERM_DAYS_DEFAULT,
        "history_conflict_pct": HISTORY_CONFLICT_PCT_DEFAULT,
        "history_min_deals": HISTORY_MIN_DEALS_DEFAULT,
        "history_max_age_days": HISTORY_MAX_AGE_DAYS_DEFAULT,
        "bounds_declared": False,
    }
    for key in raw:
        if key not in POLICY_KEYS:
            blocked.append("owner_policy_unknown_key:%s" % _safe_label(key))

    def positive(key, target, allow_zero=False):
        """Числовая ручка владельца. Конечность проверяется ПЕРВОЙ и отдельным ситом: `NaN` ложен
        разом в `value < 0` и в `value <= 0`, поэтому без неё он проходил бы сюда как законный
        порог — и дальше глушил бы уже ту проверку, которой служит (класс дефекта 3)."""
        value = raw.get(key)
        if value is None:
            return
        if not _is_finite(value) or (value < 0 if allow_zero else value <= 0):
            blocked.append("owner_policy_incoherent:%s" % key)
            return
        policy[target] = value

    positive("floor_thb_per_day", "floor")
    positive("ceiling_thb_per_day", "ceiling")
    positive("snapshot_max_age_days", "snapshot_max_age_days", allow_zero=True)
    positive("history_max_age_days", "history_max_age_days", allow_zero=True)
    positive("history_conflict_pct", "history_conflict_pct")

    approved = raw.get("discount_approved")
    if approved is not None:
        if not isinstance(approved, bool):
            blocked.append("owner_policy_incoherent:discount_approved")
        else:
            policy["discount_approved"] = approved

    pct = raw.get("discount_max_pct")
    if pct is not None:
        if not _is_finite(pct) or not 0 < pct <= 100:
            blocked.append("owner_policy_incoherent:discount_max_pct")
        else:
            policy["discount_max_pct"] = pct

    for key, target in (("long_term_days", "long_term_days"),
                        ("history_min_deals", "history_min_deals")):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            blocked.append("owner_policy_incoherent:%s" % key)
        else:
            policy[target] = value

    if policy["floor"] is not None and policy["ceiling"] is not None:
        if policy["floor"] > policy["ceiling"]:
            blocked.append("owner_policy_incoherent:floor_above_ceiling")
            policy["floor"] = None
            policy["ceiling"] = None
    policy["bounds_declared"] = policy["floor"] is not None or policy["ceiling"] is not None
    return policy


# ────────────────────────────────── разбор фактов котировки ───────────────────────────────────

def _read_quote(raw, as_of_day, policy, blocked, unknown):
    """Факты котировки → нормализованный блок `facts`. Пишет коды в blocked/unknown по границе,
    объявленной в докстроке модуля."""
    facts = {
        "model": None, "unit_name": None, "generation": None, "generation_status": None,
        "availability": None, "date_start": None, "date_end": None, "days": None,
        "list_day_price": None, "currency": None, "snapshot_on": None,
        "snapshot_age_days": None,
    }
    for key in raw:
        if key not in QUOTE_KEYS:
            blocked.append("quote_facts_unknown_key:%s" % _safe_label(key))

    # МОДЕЛЬ И ЮНИТ. Имя юнита не обязательно, но если оно есть — оно нужной формы.
    facts["model"] = _safe_text(raw.get("model"), 80)
    if facts["model"] is None:
        unknown.append("model_missing")
    if raw.get("unit_name") is not None:
        facts["unit_name"] = _safe_text(raw.get("unit_name"), 80)
        if facts["unit_name"] is None:
            unknown.append("unit_name_invalid")

    # ПОКОЛЕНИЕ. Не разведено — цены не даём (зеркало price_source._pick_generation).
    state = raw.get("generation_status")
    generation = raw.get("generation")
    if state is None:
        unknown.append("generation_status_missing")
    elif state not in GEN_STATES:
        unknown.append("generation_status_invalid")
    else:
        facts["generation_status"] = state
        if state == GEN_AMBIGUOUS:
            unknown.append("generation_ambiguous")
        elif state == GEN_RESOLVED:
            facts["generation"] = _safe_text(generation, 40)
            if facts["generation"] is None:
                unknown.append("generation_missing")
        elif generation not in (None, ""):
            unknown.append("generation_inconsistent")

    # НАЛИЧИЕ. Проверенным считаем ровно `ok`; доказанную занятость — отказом.
    availability = raw.get("availability")
    if availability is None:
        unknown.append("availability_missing")
    elif availability not in AVAIL_KNOWN:
        unknown.append("availability_unknown_value")
    else:
        facts["availability"] = availability
        if availability == AVAIL_NONE_AVAILABLE:
            blocked.append("availability_none_available")
        elif availability != AVAIL_OK:
            unknown.append("availability_not_proven")

    # ЦЕНА ЛИСТА. Единственное число, которое C0 вправе назвать целью, — и потому единственное,
    # где промах сита стои́т дороже всего: `NaN` здесь становился ЦЕЛЬЮ (`price <= 0` для него
    # ложно), а пакет с целью-`NaN` не печатается строгим JSON вовсе.
    price = raw.get("list_day_price")
    if price is None:
        unknown.append("list_price_missing")
    elif not _is_finite(price):
        unknown.append("list_price_invalid")
    elif price <= 0:
        unknown.append("list_price_not_positive")
    else:
        facts["list_day_price"] = price

    # ВАЛЮТА. Отсутствует — «нет факта»; не та — дисквалифицирующий факт.
    currency = raw.get("currency")
    if currency is None:
        unknown.append("currency_missing")
    elif currency != CURRENCY:
        facts["currency"] = _safe_text(currency, 16) or REDACTED
        blocked.append("currency_not_thb")
    else:
        facts["currency"] = currency

    # ДАТЫ И СРОК.
    start_day = _parse_day(raw.get("date_start"))
    end_day = _parse_day(raw.get("date_end"))
    if raw.get("date_start") is None or raw.get("date_end") is None:
        unknown.append("dates_missing")
    elif start_day is None or end_day is None:
        unknown.append("dates_invalid")
    else:
        facts["date_start"] = raw["date_start"]
        facts["date_end"] = raw["date_end"]
        if end_day < start_day:
            unknown.append("dates_reversed")
        if as_of_day is not None:
            # ОКНО ОТНОСИТЕЛЬНО МОМЕНТА. Два разных исхода, и путать их нельзя:
            #   • окно ЗАКОНЧИЛОСЬ до `as_of` — старое согласованное значение `quote_window_expired`,
            #     семантику которого правка не трогает;
            #   • окно ЕЩЁ ИДЁТ, но НАЧАЛОСЬ до `as_of` — цену суток на такое окно C0 не называет:
            #     часть срока уже в прошлом, значит котировка описывает не тот срок, который
            #     осталось прожить, и сойтись с ним она может только случайно. Это ровно «факт
            #     неразрешим», то есть `unknown` по объявленной границе, а не дисквалификация.
            if end_day < as_of_day:
                unknown.append("quote_window_expired")
            elif start_day < as_of_day:
                unknown.append("quote_window_already_started")

    days = raw.get("days")
    if days is None:
        unknown.append("term_missing")
    elif not isinstance(days, int) or isinstance(days, bool):
        unknown.append("term_invalid")
    elif days < 1:
        unknown.append("term_not_positive")
    else:
        facts["days"] = days
        # Срок обязан сойтись с окном. Календарь владельца считает G3 = ABS(F3-F2), то есть
        # ровно разность дат, — этой же меркой меряем и мы.
        if start_day is not None and end_day is not None and end_day - start_day != days:
            unknown.append("dates_days_mismatch")

    # СВЕЖЕСТЬ СЛЕПКА.
    snapshot_day = _parse_day(raw.get("snapshot_on"))
    if raw.get("snapshot_on") is None:
        unknown.append("snapshot_missing")
    elif snapshot_day is None:
        unknown.append("snapshot_invalid")
    else:
        facts["snapshot_on"] = raw["snapshot_on"]
        if as_of_day is not None:
            age = as_of_day - snapshot_day
            facts["snapshot_age_days"] = age
            if age < 0:
                blocked.append("snapshot_in_future")
            elif policy["snapshot_max_age_days"] > 0 and age > policy["snapshot_max_age_days"]:
                blocked.append("snapshot_stale")

    return facts


def _read_evidence(raw, unknown):
    """Ссылки-улики: непустой список строк безопасной формы, порядок сохраняем, дубли снимаем."""
    refs = raw.get("evidence_refs")
    if refs is None:
        unknown.append("evidence_missing")
        return []
    if not isinstance(refs, (list, tuple)) or not refs:
        unknown.append("evidence_invalid")
        return []
    out = []
    bad = False
    for item in refs:
        text = _safe_text(item)
        if text is None:
            bad = True
            continue
        if text not in out:
            out.append(text)
    if bad:
        unknown.append("evidence_invalid")
    if not out:
        unknown.append("evidence_missing")
    return out


# ──────────────────────────────────── разбор диалога ──────────────────────────────────────────

def _read_dialog(raw, blocked):
    """Факты диалога → флаги и числа. Строк здесь не бывает ни в одном значении."""
    dialog = {
        "cheaper_requested": False, "discount_requested": False, "bundle_requested": False,
        "agent_case": False, "units_requested": 1, "term_days": None,
        "stated_budget_thb_per_day": None,
    }
    for key in raw:
        if key in DIALOG_KEYS:
            continue
        category = _category_of_key(key)
        if category is not None:
            blocked.append("dialog_sensitive_key:%s" % category)
        else:
            blocked.append("dialog_unknown_key:%s" % _safe_label(key))

    for key in DIALOG_FLAGS:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, bool):
            blocked.append("dialog_value_invalid:%s" % key)
        else:
            dialog[key] = value

    units = raw.get("units_requested")
    if units is not None:
        if not isinstance(units, int) or isinstance(units, bool) or units < 1:
            blocked.append("dialog_value_invalid:units_requested")
        else:
            dialog["units_requested"] = units

    term = raw.get("term_days")
    if term is not None:
        if not isinstance(term, int) or isinstance(term, bool) or term < 1:
            blocked.append("dialog_value_invalid:term_days")
        else:
            dialog["term_days"] = term

    budget = raw.get("stated_budget_thb_per_day")
    if budget is not None:
        # Тот же класс, что и цена листа: бюджет уходит в эхо диалога и сравнивается с целью, а
        # `NaN` ложен и в `budget <= 0`, и в `budget >= target` — то есть тихо ложится в пакет.
        if not _is_finite(budget) or budget <= 0:
            blocked.append("dialog_value_invalid:stated_budget_thb_per_day")
        else:
            dialog["stated_budget_thb_per_day"] = budget

    return dialog


# ───────────────────────────────── разбор прошлых сделок ──────────────────────────────────────

def _read_history(raw, facts, policy, as_of_day, blocked, notes):
    """Статистика сделок → блок улик. НИКОГДА не заменяет цену листа: её роль — только
    подтвердить или поднять руку.

    ОТСЮДА ГЛАВНОЕ ПРО СТАТУС: кривая или тонкая статистика статус НЕ МЕНЯЕТ. Улика, которой мы
    не поверили, — это не пропавший факт котировки; уронить из-за неё готовую рекомендацию в
    `unknown` значило бы дать прошлым сделкам власть, которой у них в C0 нет. Такие находки
    уходят пометкой в `reason_codes`. Незнакомый КЛЮЧ — другое дело: он отвергается наравне с
    чужими ключами прочих входов, потому что может нести чужие данные.

    ПРО СВЕЖЕСТЬ (`fresh_as_of`, заведено 31.08.2026). Ключ принимается и отражается в блоке, но
    ПОДНЯТЬ уверенность история вправе только доказав свою свежесть. Асимметрия здесь намеренная и
    односторонняя: непроверяемая свежесть (кривой формат, дата из будущего, возраст сверх круга
    пересмотра цен, отсутствующий момент сверки) снимает право ПОДТВЕРЖДАТЬ, но не снимает права
    ПОДНЯТЬ РУКУ — расхождение сверх порога остаётся конфликтом и уводит заход к человеку. Обе
    ветки ведут в одну сторону: сомнительная улика может добавить проверки человеком и не может
    добавить машине уверенности. Момент сверки — только инъектированный `as_of`; своих часов у
    истории нет ровно так же, как у остального модуля."""
    history = {
        "deals_count": None, "median_day_price_thb": None, "window_days": None,
        "source": None, "fresh_as_of": None, "fresh_age_days": None,
        "used_as": "evidence_only", "delta_pct": None, "conflict": False,
        "corroborates": False,
    }
    for key in raw:
        if key not in HISTORY_KEYS:
            blocked.append("history_unknown_key:%s" % _safe_label(key))

    count = raw.get("deals_count")
    if count is not None:
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            notes.append("history_invalid:deals_count")
        else:
            history["deals_count"] = count

    window = raw.get("window_days")
    if window is not None:
        if not isinstance(window, int) or isinstance(window, bool) or window < 1:
            notes.append("history_invalid:window_days")
        else:
            history["window_days"] = window

    median = raw.get("median_day_price_thb")
    if median is not None:
        # `NaN` здесь стоил дороже, чем кажется: он не только садился в пакет, но и проходил
        # проверку конфликта (`abs(NaN) > порог` ложно) — то есть попадал в ветку «расхождения нет»
        # и ПОДНИМАЛ уверенность до `high`. Ложное подтверждение опаснее ложной тревоги.
        if not _is_finite(median) or median <= 0:
            notes.append("history_invalid:median_day_price_thb")
        else:
            history["median_day_price_thb"] = median

    if raw.get("source") is not None:
        history["source"] = _safe_text(raw.get("source"), 64) or REDACTED

    # СВЕЖЕСТЬ ИСТОРИИ. Ключа нет — старое поведение: улика без объявленной свежести подтверждать
    # по-прежнему вправе. Ключ ЕСТЬ — он обязан выдержать проверку, иначе право подтверждать снято.
    fresh_ok = True
    fresh_raw = raw.get("fresh_as_of")
    if fresh_raw is not None:
        fresh_day = _parse_day(fresh_raw)
        if fresh_day is None:
            notes.append("history_invalid:fresh_as_of")
            fresh_ok = False
        else:
            history["fresh_as_of"] = fresh_raw
            if as_of_day is None:
                # Сверять не с чем: момент не инъектирован или кривой. «Не смог проверить» —
                # это не «проверено и хорошо», поэтому право подтверждать снимается.
                notes.append("history_freshness_unverified")
                fresh_ok = False
            else:
                age = as_of_day - fresh_day
                history["fresh_age_days"] = age
                if age < 0:
                    notes.append("history_fresh_as_of_in_future")
                    fresh_ok = False
                elif (policy["history_max_age_days"] > 0
                      and age > policy["history_max_age_days"]):
                    notes.append("history_stale")
                    fresh_ok = False

    price = facts.get("list_day_price")
    if history["median_day_price_thb"] is not None and price:
        delta = (history["median_day_price_thb"] - price) / float(price) * 100.0
        if not _is_finite(delta):
            # Оба входа конечны, но их отношение вышло за пределы `float`. Наружу такое число не
            # уходит ни при каких условиях: пакет обязан оставаться строгим JSON.
            notes.append("history_invalid:median_day_price_thb")
        else:
            history["delta_pct"] = round(delta, 2)
            thin = (history["deals_count"] or 0) < policy["history_min_deals"]
            if thin:
                notes.append("history_thin")
            elif abs(delta) > policy["history_conflict_pct"]:
                history["conflict"] = True
            elif fresh_ok:
                history["corroborates"] = True
    return history


# ───────────────────────────────────── главная функция ────────────────────────────────────────

def recommend_price(quote_facts, dialog_facts, history_stats, owner_policy, *, as_of):
    """Факты → пакет-рекомендация. ЧИСТАЯ функция: входы не изменяются, внешних источников нет.

    `as_of` — обязательный именованный аргумент вида «ГГГГ-ММ-ДД» (допустим ISO-хвост после «T»).
    Он единственный момент времени, который знает модуль; собственных часов у C0 нет.

    Возвращает JSON-сериализуемый словарь схемы `turbobaby/price_recommendation/v1`. Цель
    (`recommendation.target`) либо `None`, либо ПОБИТОВО переданная `list_day_price` — третьего
    значения у C0 нет ни на одной ветке."""
    blocked = []     # факт есть и он дисквалифицирующий → status blocked
    unknown = []     # факта нет или он неразрешим → status unknown
    notes = []       # находка, статуса НЕ меняющая (улики прошлых сделок) → только reason_codes

    quote_raw, err = _as_mapping(quote_facts)
    if err:
        blocked.append("input_not_mapping:quote_facts")
    dialog_raw, err = _as_mapping(dialog_facts)
    if err:
        blocked.append("input_not_mapping:dialog_facts")
    history_raw, err = _as_mapping(history_stats)
    if err:
        blocked.append("input_not_mapping:history_stats")
    policy_raw, err = _as_mapping(owner_policy)
    if err:
        blocked.append("input_not_mapping:owner_policy")

    as_of_day = _parse_day(as_of)
    as_of_echo = as_of if as_of_day is not None else None
    if as_of_day is None:
        blocked.append("as_of_invalid")

    policy = _read_policy(policy_raw, blocked)
    facts = _read_quote(quote_raw, as_of_day, policy, blocked, unknown)
    evidence = _read_evidence(quote_raw, unknown)
    dialog = _read_dialog(dialog_raw, blocked)
    history = _read_history(history_raw, facts, policy, as_of_day, blocked, notes)

    # Срок из слов клиента против срока котировки — тем же допуском ±1, что и pricing.sanity_days_ok.
    if dialog["term_days"] is not None and facts["days"] is not None:
        if abs(dialog["term_days"] - facts["days"]) > DAYS_TOLERANCE:
            unknown.append("term_mismatch")

    codes = list(notes)
    floor = policy["floor"]
    ceiling = policy["ceiling"]

    if blocked:
        status = STATUS_BLOCKED
        action = ACTION_MANAGER_REVIEW
        target = None
    elif unknown:
        status = STATUS_UNKNOWN
        action = ACTION_ASK_MISSING_FACT
        target = None
    else:
        status = STATUS_OK
        target = facts["list_day_price"]
        codes.append("list_price_used")
        review = []
        if dialog["units_requested"] > 1:
            review.append("multi_unit")
        if facts["days"] >= policy["long_term_days"]:
            review.append("long_term")
        if dialog["discount_requested"]:
            review.append("discount_requested")
        if dialog["bundle_requested"]:
            review.append("bundle_requested")
        if dialog["agent_case"]:
            review.append("agent_case")
        if dialog["cheaper_requested"] and (policy["discount_approved"]
                                            or policy["discount_max_pct"] is not None):
            # Скидка владельцем объявлена — но считать её C0 не умеет и не должен.
            review.append("owner_discount_policy_present")
        if history["conflict"]:
            review.append("history_vs_list_conflict")

        budget = dialog["stated_budget_thb_per_day"]
        if budget is not None:
            codes.append("budget_above_list" if budget >= target else "budget_below_list")

        if review:
            action = ACTION_MANAGER_REVIEW
            codes.extend(review)
        elif dialog["cheaper_requested"]:
            action = ACTION_OFFER_ALTERNATIVE
            codes.append("cheaper_requested_no_owner_discount")
        elif budget is not None and budget < target:
            action = ACTION_OFFER_ALTERNATIVE
            codes.append("budget_below_list_offer_alternative")
        else:
            action = ACTION_OFFER_LIST

        # ГРАНИЦЫ ВЛАДЕЛЬЦА. Цена листа вне его же границ — не повод её двигать: это работа
        # человеку, и цель мы не называем вовсе.
        outside = ((floor is not None and target < floor)
                   or (ceiling is not None and target > ceiling))
        if outside:
            status = STATUS_UNKNOWN
            action = ACTION_MANAGER_REVIEW
            target = None
            unknown.append("list_price_outside_owner_bounds")
            codes.append("list_price_outside_owner_bounds")

    if history["corroborates"]:
        codes.append("history_corroborates")
    if policy["bounds_declared"]:
        codes.append("owner_bounds_declared")

    # Уверенность объявляется ПОСЛЕ того, как собраны все коды: иначе «high» приезжал бы со
    # списком причин, в котором нет той самой причины (история подтвердила).
    # Слова — только из `CONFIDENCE_LEVELS`: `blocked` дисквалифицирующему факту, `low` — всему
    # остальному, что не сложилось. Отдельного слова для `unknown` схема не объявляла, и выдумывать
    # его здесь значило бы отдать потребителю значение, которого он не ждёт.
    if status == STATUS_BLOCKED:
        level = CONF_BLOCKED
    elif status == STATUS_UNKNOWN:
        level = CONF_LOW
    elif action == ACTION_OFFER_LIST:
        level = CONF_HIGH if history["corroborates"] else CONF_MEDIUM
    else:
        level = CONF_LOW
    if status == STATUS_BLOCKED:
        confidence_reasons = sorted(set(blocked))
    elif status == STATUS_UNKNOWN:
        confidence_reasons = sorted(set(unknown))
    else:
        confidence_reasons = sorted(set(codes))

    reason_codes = sorted(set(codes) | set(blocked) | set(unknown))

    return {
        "schema": SCHEMA,
        "status": status,
        "mode": MODE,
        "as_of": as_of_echo,
        "facts": facts,
        "history": history,
        "dialog": dialog,
        "recommendation": {
            "floor": floor,
            "target": target,
            "ceiling": ceiling,
            "currency": CURRENCY,
            "action": action,
        },
        "confidence": {"level": level, "reasons": confidence_reasons},
        "reason_codes": reason_codes,
        "unknowns": sorted(set(unknown)),
        "evidence_refs": evidence,
        "approval_required": True,
    }


def to_json(packet):
    """Канонический текст пакета: один вход + один `as_of` дают побайтово одну строку.

    `allow_nan=False` — это ЗАМОК, а не украшение. По умолчанию `json` печатает `NaN` и `Infinity`
    словами, которых в JSON нет вовсе: такой текст молча уезжает потребителю и падает уже у него,
    вдали от причины. Здесь же промах сита конечности где-то выше по коду обрывает работу прямо
    на месте — пакет с нечисловым числом просто не получает текста."""
    return json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)
