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


# ------------------------------- пометка источника правил --------------------

def _norm_rule(s):
    return " ".join(str(s or "").split()).lower()


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


def apply_lesson(remark, append_rule=None, classify=None, mark=None):
    """Провести урок из тренажёра через существующий канал «урок:» (2-я ось):
      • behavior → append_playbook_rule + пометка источника «тренажёр»; применится со следующего
        черновика (playbook подмешивается в system-prompt) → dict(axis='behavior', status, card);
      • code → карточка владельцу «нужен код-фикс» (в playbook НЕ пишем) → dict(axis='code', card).
    append_rule/classify/mark инъектируются в тестах; иначе боевые suggest/lesson_router/сайдкар."""
    remark = " ".join(str(remark or "").split()).strip()
    if not remark:
        return {"axis": "behavior", "status": "error",
                "card": "⚠️ Пустой урок — нечего запоминать."}
    axis = (classify or classify_lesson)(remark)
    if axis == "code":
        card = ("🛠 Нужен код-фикс (из тренажёра) — правило поведения этим не выучить.\n"
                f"Готовый текст задачи: {remark}")
        return {"axis": "code", "card": card}
    if append_rule is None:
        import suggest
        append_rule = suggest.append_playbook_rule
    status = append_rule(remark)
    if status in ("added", "duplicate"):
        (mark or mark_source)(remark)
        card = (f"✅ Принято: {remark} → записано в книгу правил (источник «{TRAINER_SOURCE}»), "
                "применится со следующего ответа.")
    else:
        card = f"⚠️ Не удалось записать правило (status={status})."
    return {"axis": "behavior", "status": status, "card": card}


def cancel_lesson(n, remove_rule=None, list_rules=None, unmark=None):
    """«отмени урок N» — откат правила N (нумерация как в /rules). remove_rule/list_rules/unmark
    инъектируются; иначе боевой suggest + сайдкар. Возвращает dict(status, card)."""
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
