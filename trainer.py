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

def hypotheses_prompt(incoming, answer):
    """(system, user) для LLM-классификатора 1-й оси: 2–4 короткие гипотезы «что не так/что
    улучшить» в ОТВЕТЕ бота, каждая формой правила. Инъекция call_llm — в тестах."""
    system = (
        "Ты — тренер клиентского бота проката мотобайков на Пхукете. Ниже реплика клиента и "
        "ОТВЕТ бота. Назови от 2 до 4 КОРОТКИХ гипотез, что в ОТВЕТЕ можно улучшить или что не "
        "так, КАЖДУЮ формой правила на будущее («делай так» / «не делай так»). По одной на "
        "строку, без нумерации и без вводных слов. Пиши по-русски."
    )
    user = f"[клиент]: {incoming}\n[бот]: {answer}"
    return system, user


def parse_hypotheses(text, limit=4):
    """Разобрать ответ LLM в список гипотез (2–4). Срезает нумерацию/маркеры, дедупит, режет по limit."""
    out = []
    seen = set()
    for ln in (text or "").splitlines():
        s = ln.strip().lstrip("-*•").strip()
        s = re.sub(r"^\(?\d+[.)]\s*", "", s).strip()
        if len(s) < 3:
            continue
        key = re.sub(r"\W+", "", s.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


TG_MSG_LIMIT = 4096                       # жёсткий лимит Telegram на длину текста сообщения
HYPS_TITLE = ("🎓 Что улучшить в ответе? Номера — ТУМБЛЕРЫ (тап = отметить ✅, повторный тап "
              "снимает). Отметь всё нужное и жми «✔ Применить». Или «✍️ другое» — напишешь "
              "правило своими словами следующим сообщением:")
HYPS_TITLE_CONT = "🎓 …продолжение списка гипотез:"
_HYPS_CUT = " …[обрезано]"


def hyps_messages(hyps, limit=TG_MSG_LIMIT):
    """ПОЛНЫЕ тексты гипотез нумерованным списком в САМОМ сообщении (кнопки — только номера
    [1]..[N], их подписи Telegram режет по ширине — читаемость живёт здесь). → список частей;
    клавиатуру вешать на ПОСЛЕДНЮЮ. Нумерация = индекс в hyps (тот же, что в callback_data).

    Лимит 4096: список длиннее части рвём ПО ГРАНИЦЕ гипотезы на несколько сообщений (ничего не
    теряем); только если ОДНА гипотеза длиннее целой части — усекаем её с явным маркером."""
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


def apply_lessons(remarks, append_rule=None, classify=None, mark=None, list_rules=None):
    """Применить НЕСКОЛЬКО уроков разом: каждый — ОТДЕЛЬНЫМ правилом со своим номером.
    → dict(accepted=[(n, rule), …], duplicates=[…], code=[…], errors=[…], card='<одно сообщение>').
    Пустой список → карточка-предупреждение (нечего применять). Инъекции — как в apply_lesson."""
    items = [" ".join(str(r or "").split()).strip() for r in (remarks or [])]
    items = [r for r in items if r]
    if not items:
        return {"accepted": [], "duplicates": [], "code": [], "errors": [],
                "card": "⚠️ Ничего не отмечено — тапни номера гипотез и нажми «✔ Применить»."}
    accepted, duplicates, code, errors = [], [], [], []
    # ИСХОД ЗАЯВКИ ПО КАЖДОМУ КОД-УРОКУ. Раньше сводка печатала только сам текст
    # замечания — «дальше несите руками». Теперь у каждого свой исход (встала
    # заявка / не встала и почему), и владелец видит его в ТОЙ ЖЕ карточке.
    code_decs = {}
    for r in items:
        dec = apply_lesson(r, append_rule=append_rule, classify=classify, mark=mark,
                           list_rules=list_rules)
        if dec.get("axis") == "code":
            code.append(r)
            code_decs[r] = dec
        elif dec.get("status") == "added":
            accepted.append(r)
        elif dec.get("status") == "duplicate":
            duplicates.append(r)
        else:
            errors.append(r)
    # номера правил — из книги ПОСЛЕ записи (та же нумерация, что /rules и «отмени урок N»)
    numbers = {}
    try:
        if list_rules is None:
            import suggest
            list_rules = suggest.list_playbook_rules
        for row in list_rules() or []:
            numbers[_norm_rule(row.get("rule"))] = row.get("n")
    except Exception:
        numbers = {}

    def _num(r):
        n = numbers.get(_norm_rule(r))
        return f"#{n} — " if n else "• "

    lines = []
    if accepted:
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
        lines += ["• " + r for r in errors]
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
    return True


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


def _apply_lesson(remark, append_rule=None, classify=None, mark=None, list_rules=None):
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
        import suggest
        append_rule = suggest.append_playbook_rule
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
    else:
        card = f"⚠️ Не удалось записать правило (status={status})."
    return {"axis": "behavior", "status": status, "card": card, "n": num}


def apply_lesson(remark, append_rule=None, classify=None, mark=None, list_rules=None):
    """Провести урок из тренажёра через существующий канал «урок:» (2-я ось):
      • behavior → append_playbook_rule + пометка источника «тренажёр»; применится со следующего
        черновика (playbook подмешивается в system-prompt) → dict(axis='behavior', status, card,
        n=<номер правила в книге — тот же, что у кнопочного пути и «отмени урок N»>);
      • code → в playbook НЕ пишем; задача СОБИРАЕТСЯ по канону полосы (:func:`build_claim`),
        проходит ЧУЖИЕ ворота приёма ящика и уходит владельцу карточкой С КНОПКОЙ
        (:func:`code_fix_claim`) → dict(axis='code', card, placed, tid, why). Без «да»
        владельца в очередь не встаёт ничего.
    append_rule/classify/mark/list_rules инъектируются в тестах; иначе боевые suggest/lesson_router.
    НИКОГДА не бросает (обработчик группы не имеет права упасть на уроке): исключение внутри →
    status='error' + карточка-ошибка; след — в лог процесса, а карточку в TRN пишет вызывающий."""
    try:
        return _apply_lesson(remark, append_rule, classify, mark, list_rules)
    except Exception as e:
        log.warning("apply_lesson упал: %s: %s", type(e).__name__, e, exc_info=True)
        return {"axis": "behavior", "status": "error",
                "card": f"⚠️ Урок не применён — внутренняя ошибка ({type(e).__name__}), "
                        "см. лог процесса. Правило можно повторить."}


def _cancel_lesson(n, remove_rule=None, list_rules=None, unmark=None):
    """Ядро cancel_lesson (может бросить — снаружи fail-safe обёртка)."""
    if remove_rule is None or list_rules is None:
        import suggest
        remove_rule = remove_rule or suggest.remove_playbook_rule
        list_rules = list_rules or suggest.list_playbook_rules
    res = remove_rule(n)
    status = res.get("status")
    if status == "removed":
        (unmark or unmark_source)(res.get("rule"))
        card = f"↩️ Отменил урок #{res.get('n')}: {res.get('rule')} (осталось правил: {res.get('remaining')})."
    elif status == "not_found":
        card = f"⚠️ Урока #{n} нет — правил всего {len(list_rules())}."
    elif status == "empty":
        card = "⚠️ Книга правил пуста — отменять нечего."
    elif status == "ambiguous":
        card = "⚠️ Неоднозначно — уточни номер урока."
    else:
        card = f"⚠️ Не удалось отменить урок #{n} (status={status})."
    return {"status": status, "card": card}


def cancel_lesson(n, remove_rule=None, list_rules=None, unmark=None):
    """«отмени урок N» — откат правила N (нумерация как в /rules). remove_rule/list_rules/unmark
    инъектируются; иначе боевой suggest + сайдкар. Возвращает dict(status, card).
    НИКОГДА не бросает: исключение внутри → status='error' + карточка-ошибка (след — в лог
    процесса; карточку в TRN пишет вызывающий)."""
    try:
        return _cancel_lesson(n, remove_rule, list_rules, unmark)
    except Exception as e:
        log.warning("cancel_lesson(#%s) упал: %s: %s", n, type(e).__name__, e, exc_info=True)
        return {"status": "error",
                "card": f"⚠️ Урок #{n} не отменён — внутренняя ошибка ({type(e).__name__}), "
                        "см. лог процесса. Команду можно повторить."}
