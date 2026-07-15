# -*- coding: utf-8 -*-
"""
lesson_router.py — обработчик задачи-урока для дирижёра (родитель 292, шаг 3/7).

Урок (реплай-обучение менеджера на карточку черновика) уже прошёл шаги 1–2:
moderation_core.process_lesson распознал замечание, submit_lesson поставил его ШТАТНОЙ
задачей в очередь дирижёра (from=Filipp-pcloc-dec) с полным контекстом (build_lesson_task).
Здесь — то, что делает дирижёр, забрав такую задачу: КЛАССИФИЦИРУЕТ замечание и МАРШРУТИЗИРУЕТ

  • СТИЛЬ    — тон/длина/формулировка/приветствие/эмодзи/канцелярит → дописать правило в
              «книгу правил» (playbook «Выученные правила», слой ПОВЕРХ STYLE_GUIDE);
  • ФАКТ/ЛОГИКА — неверная цена/факт/модель/дата/правило расчёта/FAQ → правка кода/критфактов
              /FAQ С ТЕСТОМ; это работа думателя → делегируем локальному планировщику
              (delegate=True) с явным требованием теста; сами код не трогаем;
  • НАДЗОР   — «ревизор должен ловить …» → дописать строку-класс в docs/revizor_checklist.md
              (живой чек-лист думателя-ревизора; правило подхватится следующим тиком);
  • НЕЯСНОЕ  — сигнал не распознан либо противоречив → карточка-уточнение ВЛАДЕЛЬЦУ в 1160,
              НЕ УГАДЫВАЕМ (лучше переспросить человека, чем внести мусор в правила/код).

КРАСНОЕ: Bridge / таблицы / деньги здесь НЕ трогаем. Стилевой append и надзорный append —
чисто ТЕКСТОВЫЕ правки доков (playbook / чек-лист); факт/логика уходит планировщику, который
сам ничего в деньги не пишет. Все побочные эффекты (запись доков, пуш в 1160) ИНЪЕКТИРУЕМЫ —
модуль без I/O проверяется юнитом; боевые дефолты подтягиваются лениво.
"""

import json
import os
import re

REPO = os.path.dirname(os.path.abspath(__file__))
REVIZOR_CHECKLIST = os.path.join(REPO, "docs", "revizor_checklist.md")

# --- распознавание задачи-урока (формат moderation_core.build_lesson_task) ----------------
# Первая строка билдера: «[урок:<kind> от @who] родитель 292 — …». Достаточно префикса «[урок:».
_LESSON_HEAD_RE = re.compile(r"^\s*\[урок:", re.IGNORECASE)
# Поля из тела задачи (мягкий парс — если формат чуть уедет, деградируем, а не падаем):
_REMARK_RE = re.compile(r"(?ms)^Замечание:\s*(.+?)\s*(?:^Исходный черновик:|\Z)")
_HEAD_META_RE = re.compile(r"^\s*\[урок:(?P<kind>[^\]]*?)\s+от\s+(?P<who>@?\S+)\]", re.IGNORECASE)
_WINDOW_RE = re.compile(r"(?im)^окно диалога:\s*(.+)$")
# Координата карточки черновика в модер-группе (шаг 4): подтверждение «урок принят…» уйдёт РЕПЛАЕМ
# именно на неё. Билдер шага 2 кладёт строку «карточка модер-группы: msg=<id>».
_CARD_RE = re.compile(r"(?im)^карточка модер-группы:\s*msg=(\S+)")
# Исходный черновик (последняя секция билдера) — питает LLM-классификатор (что именно правит замечание).
_DRAFT_RE = re.compile(r"(?ms)^Исходный черновик:\s*(.+?)\s*\Z")


def is_lesson_task(text):
    """True, если текст задачи — распознанный урок (шапка build_lesson_task «[урок:…]»)."""
    return bool(_LESSON_HEAD_RE.match(str(text or "")))


def parse_lesson_task(text):
    """Разобрать задачу-урок в dict {remark, kind, who, window}. Всё best-effort: недостающее —
    пустой строкой (классификатор работает по remark; остальное — только для карточки владельцу)."""
    t = str(text or "")
    m = _REMARK_RE.search(t)
    remark = (m.group(1).strip() if m else "")
    hm = _HEAD_META_RE.match(t)
    kind = (hm.group("kind").strip() if hm else "")
    who = (hm.group("who").strip() if hm else "")
    wm = _WINDOW_RE.search(t)
    window = (wm.group(1).strip() if wm else "")
    cm = _CARD_RE.search(t)
    card_msg_id = (cm.group(1).strip() if cm else "")
    if card_msg_id == "?":                           # плейсхолдер «неизвестна» → пусто (не реплаим в никуда)
        card_msg_id = ""
    dm = _DRAFT_RE.search(t)
    draft = (dm.group(1).strip() if dm else "")
    return {"remark": remark, "kind": kind, "who": who, "window": window,
            "card_msg_id": card_msg_id, "draft": draft}


# ------------------------------- классификация замечания ----------------------------------
# Порядок приоритета намеренно НАДЗОР → ФАКТ → СТИЛЬ: явное упоминание системы-ревизора («ревизор
# должен ловить…») — это надзор, даже если замечание попутно про приветствие; конкретный
# фактический дефект (цена/депозит/модель) важнее общего тона. Сигнала нет вовсе → НЕЯСНОЕ
# (не угадываем: пустое/размытое «плохо, переделай» уйдёт владельцу на уточнение).
STYLE = "style"
FACT = "fact"
SUPERVISION = "supervision"
UNCLEAR = "unclear"

# НАДЗОР — замечание про сам ДЕТЕКТ/ревизора/чек-лист (что бот-надзор должен ЛОВИТЬ).
_SUPERVISION_KW = (
    "ревизор", "ревизора", "ревизору", "надзор", "детект", "детектор", "чек-лист", "чеклист",
    "checklist", "supervis", "detector", "должен ловить", "должна ловить", "не поймал",
    "не отловил", "пропустил находку", "автоприветств", "класс дефект", "новый класс",
    "add to checklist", "should catch", "should flag", "should detect",
)
# ФАКТ/ЛОГИКА — неверные данные/расчёт/факт/FAQ (правится в коде/критфактах/FAQ, нужен тест).
_FACT_KW = (
    "цена", "цену", "цены", "стоимост", "тариф", "депозит", "скидк", "расч", "посчит", "считает",
    "сумма", "сумму", "неправильн", "неверн", "ошиб", "врёт", "врет", "путает", "перепутал",
    "факт", "модел", "марк", "байк", "скутер", "дата", "даты", "срок", "наличи", "парк",
    "доставк", "логик", "формул", "правило расч", "wrong", "incorrect", "price", "deposit",
    "discount", "model", "date", "fact", "calculation", "faq",
)
# СТИЛЬ — тон/длина/формулировка/приветствие/эмодзи/вежливость (правится правилом в playbook).
_STYLE_KW = (
    "тон", "груб", "сух", "холодн", "теплее", "живее", "по-человечески", "по человечески",
    "длинн", "коротк", "короч", "простын", "многослов", "лаконичн", "эмодзи", "смайл", "приветств",
    "здоровайся", "здороваться", "поздоровал", "канцелярит", "казённ", "казенн", "официоз",
    "пафос", "формулиров", "звучит", "фраз", "вежлив", "мягче", "тепло", "greeting", "tone",
    "wording", "phrasing", "shorter", "longer", "warmer", "friendlier", "polite", "emoji",
)


def _hits(low, kws):
    return sum(1 for k in kws if k in low)


def classify_lesson_remark(remark):
    """Классифицировать замечание менеджера → (route, reason). route ∈ style|fact|supervision|
    unclear. Пустое/безсигнальное («плохо, переделай») → unclear (НЕ угадываем — уйдёт владельцу).
    Приоритет НАДЗОР→ФАКТ→СТИЛЬ: явный маркер старшей категории выигрывает у попутных слов младшей."""
    low = " ".join(str(remark or "").split()).lower()
    if not low:
        return UNCLEAR, "пустое замечание"
    sup, fact, sty = _hits(low, _SUPERVISION_KW), _hits(low, _FACT_KW), _hits(low, _STYLE_KW)
    if sup:
        return SUPERVISION, f"надзор-сигнал ({sup})"
    if fact:
        return FACT, f"факт/логика-сигнал ({fact})"
    if sty:
        return STYLE, f"стиль-сигнал ({sty})"
    return UNCLEAR, "ни стиль, ни факт, ни надзор не распознаны"


# ============================= ВТОРАЯ ОСЬ УРОКА: type = behavior | code | unsure ===========
# Родитель 112, шаг 1/8. Ось classify_lesson_remark (style/fact/supervision/unclear) отвечает
# на «КУДА писать правку» (книга правил / чек-лист / код+тест / владельцу). Эта, ВТОРАЯ и
# ОРТОГОНАЛЬНАЯ, ось отвечает на «ЧТО за урок ПО ПРИРОДЕ» — независимо от адресата:
#   • behavior — ПОВЕДЕНИЕ бота: тон, формулировки, ЧТО говорить/НЕ говорить, дефолты
#                предложения, порядок вопросов (правится текстом-правилом, код не трогается);
#   • code     — МАШИНА: цифры, гарды, инварианты, резолверы, транспорт, интеграции
#                (правится в коде с тестом — тут «данные», а не «манера»);
#   • unsure   — сигнал слабый/смешанный/пустой → НЕ угадываем тип (при низкой уверенности в типе
#                отдаём unsure, как и в первой оси «лучше переспросить, чем внести мусор»).
# Она keyword-детерминированная (зеркалит classify_lesson_remark) — юнит без claude/сети. Пример
# спеки: «не пиши "данные получил"» = behavior (манера/что не говорить); «цена из столбца J» =
# code (цифра из конкретного поля). Оси НЕЗАВИСИМЫ: НАДЗОР-урок «ревизор должен ловить цену» по
# первой оси = supervision, по этой = code (правка машины-детектора); СТИЛЬ обычно = behavior.
BEHAVIOR = "behavior"
CODE = "code"
UNSURE = "unsure"

# behavior — ПОВЕДЕНИЕ/манера: тон, формулировки, что говорить/не говорить, дефолты предложения,
# порядок вопросов, приветствие/эмодзи/длина (правится правилом-текстом, не кодом).
_BEHAVIOR_KW = (
    # тон / формулировка / манера
    "тон", "формулиров", "формулируй", "звучит", "фраз", "вежлив", "груб", "сух", "холодн",
    "теплее", "живее", "по-человечески", "по человечески", "канцелярит", "казённ", "казенн",
    "официоз", "пафос", "приветств", "здоровайся", "здороваться", "эмодзи", "смайл", "мягче",
    "тепло", "длинн", "коротк", "короче", "лаконичн", "многослов", "простын", "звучание",
    # что говорить / чего НЕ говорить (манера речи, не данные)
    "не пиши", "не говори", "не упоминай", "не обещай", "не предлагай", "не надо писать",
    "не отвечай", "говори", "скажи", "пиши", "что сказать", "что говорить", "как отвечать",
    # дефолты предложения / порядок вопросов
    "дефолт", "по умолчанию", "умолчанию", "предлож", "порядок вопрос", "сначала спроси",
    "сначала уточни", "спроси", "переспрос", "первым делом спроси",
    # EN
    "tone", "wording", "phrasing", "greeting", "emoji", "shorter", "longer", "warmer",
    "friendlier", "polite", "don't say", "do not say", "don't write", "don't mention",
    "default", "order of questions", "ask first", "say ",
)
# code — МАШИНА: цифры/расчёт, поля-столбцы листа, гарды, инварианты, резолверы, транспорт,
# интеграции (правится в коде с тестом — «данные», а не «манера речи»).
_CODE_KW = (
    # цифры / расчёт
    "цифр", "число", "числ", "цена", "цену", "цены", "тариф", "депозит", "сумм", "процент",
    "скидк", "расч", "посчит", "считает", "формул", "коэффициент", "округл",
    # таблица / поля / столбцы (конкретный источник данных)
    "столбец", "столбц", "колонк", "ячейк", "поле ", "строк листа", "таблиц",
    # гарды / инварианты / резолверы / детект-логика
    "гард", "guard", "инвариант", "invariant", "резолвер", "resolver", "резолв", "детект",
    "detector", "regex", "регэксп", "регуляр", "парс", "parser", "валидац",
    # транспорт / интеграции
    "транспорт", "transport", "доставк", "интеграци", "integration", "api", "endpoint",
    "bridge", "webhook", "вебхук", "quote", "sheet", "json", "http", "редирект", "функци",
    "модуль", "код", "баг", "гейт",
    # EN числа/поля/машина
    "column", "number", "digit", "price", "deposit", "amount", "invariant", "resolver",
)


def _type_result(t, conf, reason, beh, cod):
    """Собрать результат второй оси: тип + уверенность + причина + счётчики попаданий по осям
    (для рапорта/отладки). Инвариант: behavior|code ⇒ confidence=high; unsure ⇒ confidence=low."""
    return {"type": t, "confidence": conf, "reason": reason,
            "behavior_hits": beh, "code_hits": cod}


def classify_lesson_type(remark):
    """Вторая ось урока → {type, confidence, reason, behavior_hits, code_hits}. type ∈ behavior|
    code|unsure. Ортогональна classify_lesson_remark: та говорит КУДА писать, эта — ЧТО за урок
    (манера бота vs машина). Правило спеки «при низкой уверенности — type=unsure»: уверенный тип
    отдаём ТОЛЬКО при чистом/резко-доминирующем сигнале одной оси (confidence=high); пусто/смешанно/
    паритет → unsure (confidence=low, не угадываем). «не пиши …» → behavior; «цена из столбца J» → code."""
    low = " ".join(str(remark or "").split()).lower()
    if not low:
        return _type_result(UNSURE, "low", "пустое замечание", 0, 0)
    beh, cod = _hits(low, _BEHAVIOR_KW), _hits(low, _CODE_KW)
    if beh == 0 and cod == 0:
        return _type_result(UNSURE, "low", "ни behavior-, ни code-сигналов не распознано", beh, cod)
    if cod == 0:                                     # чистый behavior-сигнал
        return _type_result(BEHAVIOR, "high", f"behavior-сигнал ({beh}), code нет", beh, cod)
    if beh == 0:                                     # чистый code-сигнал
        return _type_result(CODE, "high", f"code-сигнал ({cod}), behavior нет", beh, cod)
    # обе оси зацепились: уверенный тип только при РЕЗКОМ перевесе (≥2×), иначе тип неясен → unsure.
    if beh > cod and beh >= 2 * cod:
        return _type_result(BEHAVIOR, "high", f"перевес behavior ({beh} vs code {cod})", beh, cod)
    if cod > beh and cod >= 2 * beh:
        return _type_result(CODE, "high", f"перевес code ({cod} vs behavior {beh})", beh, cod)
    return _type_result(UNSURE, "low", f"смешанный сигнал (behavior {beh} ≈ code {cod}) — не угадываем",
                        beh, cod)


# ------------------------------- LLM-классификатор урока (THINKER_MODEL) -------------------
# Родитель 334, шаг 1/6: думательная классификация урока по тому же _thinker_exec-паттерну, что и
# самопочинка/ревизор (read-only, --max-turns 1, --allowed-tools '', нейтральный cwd → ничего не
# исполняет, файлы не читает — судит строго по данным). Вход — замечание модератора ДОСЛОВНО +
# черновик ответа + окно диалога; выход СТРОГО ОДИН JSON-объект
# {"reading","class":СТИЛЬ|ФАКТ|НАДЗОР,"confidence":high|low,"plan"}. Это надстройка НАД keyword-
# классификатором classify_lesson_remark: даёт трактовку и план, разводит размытые формулировки,
# которых не берут ключевые слова. FAIL-SAFE: любой сбой думателя / брак JSON после РОВНО одного
# ретрая → None (вызывающий откатывается на keyword-классификатор — поведение не хуже прежнего).
# Думатель ИНЪЕКТИРУЕМ (юнит подставляет фейк); боевой дефолт лениво тянет pc_orchestrator._thinker_exec.
LESSON_CLASSIFY_TIMEOUT = int(os.getenv("LESSON_CLASSIFY_TIMEOUT", "120") or "120")   # думатель — короткий ответ

# Русские ярлыки класса из вывода думателя (спека 334) → внутренние маршруты модуля (style/fact/supervision).
_LLM_CLASS_TO_ROUTE = {"СТИЛЬ": STYLE, "ФАКТ": FACT, "НАДЗОР": SUPERVISION}

LESSON_CLASSIFY_PREAMBLE = (
    "Ты — думательный слой дирижёра TurboBaby, который КЛАССИФИЦИРУЕТ урок от модератора: менеджер "
    "поправил черновик ответа клиенту и оставил замечание. Твоя задача — ТОЛЬКО понять, чего касается "
    "замечание, и отнести его к ОДНОМУ классу; ты НИЧЕГО не исполняешь, инструментов нет, файлы не "
    "читаешь — суди строго по данным ниже.\n"
    "Классы (выбери РОВНО один):\n"
    "  СТИЛЬ  — тон/длина/формулировка/приветствие/эмодзи/вежливость/канцелярит (правится правилом в "
    "книге стиля, код не трогается);\n"
    "  ФАКТ   — неверные данные/расчёт/цена/депозит/модель/дата/наличие/логика/FAQ (правится в коде/"
    "критфактах/FAQ, нужен тест);\n"
    "  НАДЗОР — замечание про сам бот-надзор/ревизора/детект/чек-лист: что система ДОЛЖНА ЛОВИТЬ "
    "(дописывается класс-правило в чек-лист ревизора).\n"
    "Если сигнал размытый или противоречивый — выбери НАИБОЛЕЕ близкий класс, но поставь "
    "confidence=low (низкую уверенность позже перепроверит человек). confidence=high — только когда "
    "класс очевиден из самого замечания.\n"
    "Ответь СТРОГО ОДНИМ JSON-объектом, без текста до/после, без markdown-обёртки:\n"
    '{"reading":"<трактовка замечания одной фразой>","class":"СТИЛЬ"|"ФАКТ"|"НАДЗОР",'
    '"confidence":"high"|"low","plan":"<что именно поправит, 1-2 строки>"}\n\n'
)
# Нудж на РЕТРАЙ: думатель без состояния, второй вызов получает базовый промпт + напоминание строгости.
_LESSON_RETRY_NUDGE = ("ПРЕДЫДУЩИЙ ОТВЕТ был невалиден. Верни СТРОГО ОДИН JSON-объект с полями "
                       "reading/class/confidence/plan (class ∈ СТИЛЬ|ФАКТ|НАДЗОР) и НИЧЕГО больше.\n\n")


def build_lesson_classify_prompt(remark, draft="", window=""):
    """Промпт думателя-классификатора: статичная преамбула (роль + классы + контракт вывода) + КОНКРЕТНЫЕ
    данные урока — замечание модератора ДОСЛОВНО, черновик ответа, окно диалога. Пустые поля → «(нет)»."""
    return (LESSON_CLASSIFY_PREAMBLE +
            "ЗАМЕЧАНИЕ МОДЕРАТОРА (дословно):\n" + (str(remark or "").strip() or "(пусто)") + "\n\n" +
            "ЧЕРНОВИК ОТВЕТА (его правит замечание):\n" + (str(draft or "").strip() or "(нет)") + "\n\n" +
            "ОКНО ДИАЛОГА (контекст):\n" + (str(window or "").strip() or "(нет)") + "\n")


def _parse_lesson_class_json(text):
    """Строгий парс ответа думателя → {reading, class, confidence, plan, route} или None (fail-safe →
    ретрай/откат). Терпим мусор-обёртку вокруг JSON (от первой { до последней }), но class ОБЯЗАН быть
    из СТИЛЬ|ФАКТ|НАДЗОР — иначе брак → None. confidence вне high|low консервативно приводим к low
    (не теряем урок, но и не доверяем как явному). reading/plan — свободный текст, нормализуем в строку."""
    t = str(text or "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    cls = str(d.get("class") or "").strip().upper()
    route = _LLM_CLASS_TO_ROUTE.get(cls)
    if route is None:                                # класс не из enum → брак (ретрай/откат), не угадываем
        return None
    conf = str(d.get("confidence") or "").strip().lower()
    if conf not in ("high", "low"):
        conf = "low"
    return {"reading": _one_line(d.get("reading")), "class": cls, "confidence": conf,
            "plan": _one_line(d.get("plan")), "route": route}


def _default_lesson_thinker(prompt):
    """Боевой думатель классификатора = pc_orchestrator._thinker_exec (тот же кондуктор Fable5→fallback,
    read-only, ничего не исполняет). Ленивый импорт: тяжёлый pc_orchestrator тянем ТОЛЬКО на реальном
    вызове (и разрываем цикл импорта — pc_orchestrator сам импортит lesson_router). Сбой → None."""
    import pc_orchestrator
    return pc_orchestrator._thinker_exec(prompt, LESSON_CLASSIFY_TIMEOUT, "lesson-classify")


def classify_lesson_llm(remark, draft="", window="", think=None):
    """Думательная классификация урока на THINKER_MODEL. Вход: замечание ДОСЛОВНО + черновик + окно.
    → dict {reading, class(СТИЛЬ|ФАКТ|НАДЗОР), confidence(high|low), plan, route} или None (fail-safe).
    РОВНО один ретрай: первый вызов — базовый промпт; если ответ не распарсился/брак — второй вызов с
    напоминанием строгости; повторный брак / сбой думателя → None (вызывающий откатится на keyword-
    классификатор). think инъектируется в тестах (реальный claude не дёргаем)."""
    think = think or _default_lesson_thinker
    base = build_lesson_classify_prompt(remark, draft, window)
    for prompt in (base, _LESSON_RETRY_NUDGE + base):     # база + РОВНО один ретрай с нуджем
        try:
            out = think(prompt)
        except Exception:                                # noqa: BLE001 — сбой думателя не роняет дирижёра
            out = None
        parsed = _parse_lesson_class_json(out) if out is not None else None
        if parsed is not None:
            return parsed
    return None


# ------------------------------- надзорный append (чек-лист ревизора) ---------------------
# Русский алфавит для авто-выбора буквы нового класса (поле "class" находки ревизора = буква).
_ALPHABET = "абвгдежзиклмнопрстуфхцчшщэюя"
_CHECK_CLASS_RE = re.compile(r"^-\s*\[класс\s+(\S+)\]", re.IGNORECASE)


def _next_class_letter(text):
    """Следующая свободная буква класса по уже занятым в чек-листе (макс+1 по алфавиту). Все буквы
    заняты / не распарсили → '' (вызывающий подставит fallback-метку)."""
    used = set()
    for ln in str(text or "").splitlines():
        m = _CHECK_CLASS_RE.match(ln.strip())
        if m:
            used.add(m.group(1).strip().lower())
    idx = -1
    for i, ch in enumerate(_ALPHABET):
        if ch in used:
            idx = i
    for ch in _ALPHABET[idx + 1:]:
        if ch not in used:
            return ch
    return ""


def _one_line(s):
    return " ".join(str(s or "").split()).strip()


def append_checklist_class(remark, path=None):
    """Дописать НОВЫЙ класс-правило «- [класс X] <замечание>;» в docs/revizor_checklist.md (живой
    чек-лист думателя-ревизора; правило попадёт в преамбулу СЛЕДУЮЩЕГО тика без правки кода). Только
    ТЕКСТ чек-листа — Bridge/таблицы/деньги не касаемся. Дедуп по нормализованному тексту.
    → 'added' | 'duplicate' | 'error' (FAIL-SAFE: файл недоступен → 'error', вызывающий не падает)."""
    rule = _one_line(remark)
    if not rule:
        return "error"
    p = path or REVIZOR_CHECKLIST
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return "error"
    norm = rule.rstrip(";.").lower()
    for ln in text.splitlines():
        m = _CHECK_CLASS_RE.match(ln.strip())
        if m and norm and norm in ln.lower():
            return "duplicate"
    letter = _next_class_letter(text) or "новый"
    bullet = f"- [класс {letter}] {rule.rstrip(';')};"
    # Вставляем после последней строки-класса (перед возможным хвостом файла), иначе — в конец.
    lines = text.splitlines()
    last = max((i for i, ln in enumerate(lines) if _CHECK_CLASS_RE.match(ln.strip())), default=None)
    if last is None:
        new_text = text.rstrip() + "\n" + bullet + "\n"
    else:
        lines.insert(last + 1, bullet)
        new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(new_text)
        return "added"
    except Exception:
        return "error"


# ------------------------------- стилевой append (книга правил) ---------------------------
def _default_append_style(rule):
    """Боевой стилевой sink: правило → playbook «Выученные правила» (слой ПОВЕРХ STYLE_GUIDE в
    промпте клиентского бота). Ленивый импорт suggest — тяжёлый модуль тянем только на реальном
    уроке. → 'added' | 'duplicate' | 'error'."""
    import suggest
    return suggest.append_playbook_rule(rule)


def _default_notify_owner(_card):
    """Дефолт-заглушка канала владельца (1160): в standalone канала нет → False (не доставлено).
    Демон-дирижёр инъектирует боевой sink (_deliver_owner_card → send_critical, инбокс 1160)."""
    return False


def _normalize_delivery(raw):
    """Нормализовать ответ owner-sink → (delivered: bool, channel: str). Боевой sink демона
    (_deliver_owner_card → dispatch_notify.send_critical) ПОДТВЕРЖДАЕТ доставку и отдаёт КАНАЛ
    ('инбокс 1160' / 'личка-фолбэк') кортежем (channel, ok) → рапорт честный «доставлено через X».
    Юнит-фейки/дефолт возвращают bool → канал неизвестен (''). Строка трактуется как имя канала.
    Так закрыт разрыв: fire-and-forget-sink возвращал None ⇒ delivered=False ⇒ ложное «недоступен»
    при реально дошедшей карточке — теперь sink обязан вернуть настоящий статус доставки."""
    if isinstance(raw, tuple) and len(raw) == 2:
        channel, ok = raw
        return bool(ok), (str(channel).strip() if ok else "")
    if isinstance(raw, str):
        s = raw.strip()
        return bool(s), s
    return bool(raw), ""


# ------------------------------- карточка-уточнение владельцу (1160) -----------------------
def build_owner_clarification_card(parsed, reason=""):
    """Карточка владельцу для НЕЯСНОГО урока: что за замечание, по какому окну, почему не поняли —
    и просьба переформулировать (правка:/урок:/не так:) конкретнее. НЕ угадываем за владельца."""
    remark = _one_line(parsed.get("remark")) or "(без текста)"
    who = parsed.get("who") or "?"
    window = parsed.get("window") or "?"
    tail = f" — {reason}" if reason else ""
    return ("🤔 Неясный урок — нужна расшифровка (не угадываю)\n"
            f"От: {who} · {window}\n"
            f"Замечание: {remark}\n"
            f"Не понял, это про СТИЛЬ, ФАКТ/ЛОГИКУ или НАДЗОР{tail}. "
            "Переформулируй конкретнее (правка:/урок:/не так:) — что именно поправить.")


# ------------------------------- подтверждение урока ПОСЛЕ коммита (шаг 4) -----------------
# Итог задачи-урока = ЗАКОММИЧЕННЫЙ диф (правка кода/чек-листа + golden-тест на дословную фразу
# клиента + живой реплей окна). ТОЛЬКО после реального коммита moderation_bot шлёт РЕПЛАЕМ на
# карточку черновика в модер-группу: «урок принят: <суть> → <куда записан>, применится со следующего
# ответа». Инвариант шага 4: подтверждение НЕ уходит до коммита — гейт ack_after_commit ниже даёт
# текст ТОЛЬКО когда commit_ref — доказанный реальный коммит (иначе None: молчим).
_REVIZOR_REL = os.path.join("docs", "revizor_checklist.md")   # tracked-файл; ← куда пишет НАДЗОР-урок
# «Куда записан» для карточки-подтверждения (человекочитаемо) + tracked-файлы урока для коммита.
_ROUTE_WHERE = {
    STYLE: "книгу правил (слой «Выученные правила» поверх STYLE_GUIDE)",
    SUPERVISION: "чек-лист ревизора",
    FACT: "код/критфакты/FAQ + golden-тест",
}
_ROUTE_COMMIT_PATHS = {
    SUPERVISION: [_REVIZOR_REL],   # чек-лист отслеживается git → есть что коммитить
    STYLE: [],                     # playbook manager-bot gitignored (свой контур, читается «вживую»)
    FACT: [],                      # диф+тест коммитит планировщик-думатель (делегирование)
}


def build_lesson_ack(subject, where):
    """Текст подтверждения учителю (реплаем на карточку черновика в модер-группе) — формат родителя
    292: «урок принят: <суть> → <куда записан>, применится со следующего ответа». Чистая функция;
    зовётся ТОЛЬКО после коммита (см. ack_after_commit) — сам факт коммита здесь НЕ проверяет."""
    subj = _one_line(subject) or "(см. окно диалога)"
    dst = _one_line(where) or "книгу правил"
    return f"✅ Урок принят: {subj} → записан в {dst}, применится со следующего ответа."


def _default_git_verify(ref):
    """Боевая проверка: ref — реальный коммит в REPO? `git rev-parse --verify --quiet <ref>^{commit}`
    даёт код 0 только для существующего коммита. Без git/при сбое → False (нет доказательства)."""
    import subprocess
    try:
        p = subprocess.run(["git", "rev-parse", "--verify", "--quiet", str(ref) + "^{commit}"],
                           cwd=REPO, capture_output=True, text=True, timeout=15)
        return p.returncode == 0
    except Exception:
        return False


def verify_commit(ref, run=None):
    """True, если ref — ДОКАЗАННО реальный коммит (а не выдуманная строка/пустышка). Раннер
    инъектируется (юнит без git). FAIL-SAFE: пусто/исключение → False (нет коммита → нет подтверждения)."""
    ref = str(ref or "").strip()
    if not ref:
        return False
    run = run or _default_git_verify
    try:
        return bool(run(ref))
    except Exception:
        return False


def ack_after_commit(commit_ref, subject, where, verify=None):
    """ГЕЙТ подтверждения урока (родитель 292, шаг 4). Вернуть текст «урок принят…» ТОЛЬКО если
    commit_ref — доказанный реальный коммит; нет коммита / непроверяемый ref → None. Так реализован
    инвариант «сначала коммит — потом подтверждение в модер-группу» (подтверждение НЕ уходит до
    коммита). verify инъектируется (юнит проверяет обе ветки без git)."""
    if not str(commit_ref or "").strip():
        return None                                  # нет ref → коммита не было → молчим (не зовём verify)
    verify = verify if verify is not None else verify_commit
    if not verify(commit_ref):
        return None
    return build_lesson_ack(subject, where)


# ------------------------------- реплай-понимание в модер-группу (родитель 334, шаг 2) -----
def build_lesson_understanding(reading, plan):
    """Реплай учителю в модер-группу на сообщение с уроком для confidence=high: дирижёр берёт урок В
    РАБОТУ СРАЗУ и тут же отвечает, ЧТО понял и ЧТО делает — «Понял так: <reading>. Делаю: <plan>».
    Пустые reading/plan → нейтральная заглушка (формат не роняем). Чистая функция."""
    r = _one_line(reading) or "суть замечания (см. окно диалога)"
    p = _one_line(plan) or "беру урок в работу по штатному пайплайну"
    return f"👍 Понял так: {r}. Делаю: {p}"


def _default_reply_moderation(_card_msg_id, _text):
    """Дефолт-заглушка реплая в модер-группу: в standalone канала нет → False (НЕ доставлено). Боевой
    sink инъектирует ДЕМОН (_reply_moderation_lesson: реплай через MODERBOT_TOKEN на карточку черновика,
    ретрай, проверка message_id). КОНТРАКТ: truthy = реплай ДОСТАВЛЕН (боевое = message_id); falsy = НЕ
    доставлен → low-урок падает failed с диагнозом, а не висит молча (инцидент #102). Для high-ветки
    understanding и так лежит в результате задачи — сбой реплая урок не теряет."""
    return False


# ------------------------------- ветка confidence=low: подтверждение в модер-группе (шаг 3) -----
# confidence=low (класс размыт) — урок В РАБОТУ НЕ БЕРЁМ вслепую. Реплаем в ТУ ЖЕ модер-группу на
# сообщение с уроком спрашиваем учителя: «Понял так: <reading> — верно? Ответь "да" или поправь одним
# сообщением», и СОХРАНЯЕМ состояние ожидания (pending_low). По ответу возобновляем (resume_low_lesson):
# «да» от INTAKE_APPROVERS → урок в работу как high (маршрут уже согласован учителем); текстовая
# поправка → РОВНО одна пере-классификация (high → в работу; снова low/брак → «отложил, разберёт
# владелец» + карточка в 1160). Все sink'и инъектируемы; текстовые билдеры — чистые функции.
def build_lesson_low_confirm(reading):
    """Реплай учителю в модер-группу для confidence=low: короткая трактовка + просьба подтвердить «да»
    ИЛИ поправить одним сообщением. Пустой reading → нейтральная заглушка (формат не роняем)."""
    r = _one_line(reading) or "суть замечания (см. окно диалога)"
    return f'🤔 Понял так: {r} — верно? Ответь "да" или поправь одним сообщением.'


def build_lesson_low_defer():
    """Реплай учителю, когда после его поправки урок ВСЁ РАВНО неясен (снова low): честно откладываем —
    разберёт владелец (карточка-уточнение ушла ему в 1160). Не угадываем за учителя."""
    return "🤷 Отложил — по-прежнему неясно после поправки, разберётся владелец (карточка ушла ему)."


# Таймаут ожидания ответа учителя на low-уточнение (шаг 4): молчит >24ч → урок НЕ теряем, отдаём владельцу.
LESSON_LOW_WAIT_TIMEOUT = int(os.getenv("LESSON_LOW_WAIT_TIMEOUT", str(24 * 3600)) or str(24 * 3600))


def _now_epoch():
    """Текущее epoch-время (боевой дефолт штампа created_at / часов таймаута). Инъекция now в тестах
    подменяет время — сам time тянем лениво, чтобы модуль оставался без I/O на импорте."""
    import time
    return time.time()


def low_wait_age_sec(pending, now=None):
    """Возраст low-ожидания в секундах по pending['created_at'] (epoch). Нет/битая метка → 0.0 (не
    таймаутим вслепую — свежее состояние). now инъектируется в тестах (подмена времени)."""
    try:
        created = float((pending or {}).get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0
    if created <= 0:
        return 0.0
    now = _now_epoch() if now is None else float(now)
    return max(0.0, now - created)


# Короткие согласия учителя («да/верно/точно…») отделяем от ТЕКСТОВОЙ поправки: строгие маркеры
# согласия + допустимые «наполнители» рядом (всё/так/конечно). Поправка — любой ответ, где строгого
# маркера нет (в т.ч. «нет, дело в цене»). Регистр/пунктуация/эмодзи по краям снимаются.
_LESSON_AFFIRM_STRONG = frozenset((
    "да", "да-да", "ага", "угу", "верно", "точно", "именно", "правильно", "подтверждаю",
    "yes", "yep", "yeah", "correct", "right", "ок", "окей", "ok", "okay",
))
_LESSON_AFFIRM_FILLER = frozenset(("всё", "все", "так", "конечно", "именно", "точно", "супер", "отлично"))
# Согласия БЕЗ строгого маркера, но однозначные как фраза (иначе «так»/«всё» сами по себе размыты).
_LESSON_AFFIRM_PHRASES = frozenset(("всё так", "все так", "так и есть", "всё верно", "все верно"))
_AFFIRM_STRIP = ".,!?…-—\"'«»()[]{} \t👍✅🙂"


def is_lesson_affirmative(text):
    """True, если ответ учителя — короткое СОГЛАСИЕ («да», «верно», «да, всё так»), а НЕ текстовая
    поправка. Согласие = известная фраза-согласие ЛИБО ≥1 строгий маркер и все слова из согласий/
    наполнителей (≤4 слова); иначе поправка (пере-классифицируем). «нет, …» / «плохо, переделай» → False."""
    toks = [t.strip(_AFFIRM_STRIP) for t in _one_line(text).lower().split()]
    toks = [t for t in toks if t]
    if not toks or len(toks) > 4:
        return False
    if " ".join(toks) in _LESSON_AFFIRM_PHRASES:
        return True
    if not any(t in _LESSON_AFFIRM_STRONG for t in toks):
        return False
    return all(t in _LESSON_AFFIRM_STRONG or t in _LESSON_AFFIRM_FILLER for t in toks)


def _lesson_llm_enabled():
    """Боевой рубильник LLM-классификатора урока (по умолчанию OFF → поведение байт-в-байт keyword-
    прежнее; тесты, инъектирующие classify, включают путь явно вне зависимости от флага)."""
    return str(os.getenv("LESSON_LLM_ROUTE", "")).strip().lower() in ("1", "true", "yes", "on")


# ------------------------------- обработчик задачи-урока ----------------------------------
def _dispatch_route(route, text, parsed, remark, reason, a_style, a_check, notify, _ack):
    """Исполнить УЖЕ ВЫБРАННЫЙ маршрут урока (SUPERVISION/STYLE/FACT/UNCLEAR) — общее ядро и для
    keyword-, и для LLM-high-классификации. → dec-dict (контракт см. handle_lesson_task)."""
    subj = _ack["ack_subject"]
    if route == SUPERVISION:
        try:
            res = a_check(remark)
        except Exception as e:                       # noqa: BLE001 — sink не должен ронять дирижёра
            res = "error"; reason = f"{reason}; sink: {e}"
        if res == "added":
            return {"route": route, "delegate": False, "status": "done", "reason": reason, **_ack,
                    "result": f"🔍 НАДЗОР-урок: правило дописано в чек-лист ревизора — «{subj}»"}
        if res == "duplicate":
            # правило уже в чек-листе (закоммичено ранее) → коммитить нечего, но урок применён
            return {"route": route, "delegate": False, "status": "done", "reason": reason, **_ack,
                    "commit_paths": [], "result": f"🔍 НАДЗОР-урок: такое правило в чек-листе уже есть — «{subj}»"}
        return {"route": route, "delegate": False, "status": "failed", "reason": reason, **_ack,
                "commit_paths": [], "result": f"⚠️ НАДЗОР-урок не записан в чек-лист (sink={res}) — повтори: «{subj}»"}

    if route == STYLE:
        try:
            res = a_style(remark)
        except Exception as e:                       # noqa: BLE001
            res = "error"; reason = f"{reason}; sink: {e}"
        if res == "added":
            return {"route": route, "delegate": False, "status": "done", "reason": reason, **_ack,
                    "result": f"📝 СТИЛЬ-урок: правило добавлено в книгу правил — «{subj}»"}
        if res == "duplicate":
            return {"route": route, "delegate": False, "status": "done", "reason": reason, **_ack,
                    "result": f"📝 СТИЛЬ-урок: такое правило уже есть — «{subj}»"}
        return {"route": route, "delegate": False, "status": "failed", "reason": reason, **_ack,
                "commit_paths": [], "result": f"⚠️ СТИЛЬ-урок не записан (sink={res}) — повтори: «{subj}»"}

    if route == FACT:
        # ФАКТ/ЛОГИКА: правка FAQ/критфактов/кода + ТЕСТ — работа думателя. Делегируем локальному
        # планировщику (декомпозиция в шаги). Явно требуем тест и запрещаем Bridge/деньги.
        note = ("\n\n[дирижёр: это ФАКТ/ЛОГИКА-урок] Разложи в шаги и поправь причину в коде/"
                "критфактах/FAQ. ОБЯЗАТЕЛЬНО добавь юнит-тест с ДОСЛОВНОЙ фразой клиента из окна "
                "(golden-правило CLAUDE.md). Bridge/таблицы/деньги НЕ трогай.")
        return {"route": route, "delegate": True, "reason": reason, **_ack,
                "delegate_text": str(text or "") + note,
                "status": "done", "result": "🛠 ФАКТ/ЛОГИКА-урок → локальному планировщику (правка+тест)"}

    # UNCLEAR — не угадываем: карточка-уточнение владельцу в 1160. Рапорт ЧЕСТНЫЙ: доставку берём
    # из ответа sink'а (delivered + канал), а не гадаем. Раньше боевой sink был fire-and-forget и
    # возвращал None → всегда «недоступен», хотя карточка реально доходила (обходным каналом).
    card = build_owner_clarification_card(parsed, reason)
    channel = ""
    try:
        delivered, channel = _normalize_delivery(notify(card))
    except Exception as e:                           # noqa: BLE001
        delivered, channel = False, ""; reason = f"{reason}; notify: {e}"
    if delivered:
        via = f" — доставлено через {channel}" if channel else ""
        result = f"🤔 Неясный урок → карточка-уточнение владельцу в 1160{via} (не угадываю)"
    else:
        result = "🤔 Неясный урок: канал 1160 недоступен — карточка не доставлена, замечание в логе"
    return {"route": UNCLEAR, "delegate": False, "status": "done", "reason": reason,
            "result": result, "card": card, "delivered": delivered, "channel": channel}


def _ack_material(route, remark, card):
    """Материал подтверждения (шаг 4) для применённой ветки: суть/куда записан/tracked-пути/координата
    карточки. Един для keyword-, LLM-high- и resume-путей."""
    return {"ack_subject": _one_line(remark), "ack_where": _ROUTE_WHERE.get(route, "книгу правил"),
            "commit_paths": list(_ROUTE_COMMIT_PATHS.get(route, [])), "card_msg_id": card or ""}


def _apply_high(route, text, parsed, remark, reason, reading, plan, cls,
                a_style, a_check, notify, reply_mod, card):
    """Взять урок В РАБОТУ СРАЗУ по LLM-маршруту (high или подтверждённый low): ответить учителю реплаем
    в модер-группу «Понял так: <reading>. Делаю: <plan>», затем исполнить маршрут штатным пайплайном.
    Реплай инъектируем; его сбой урок НЕ теряет (understanding остаётся в результате). → dec с high-полями."""
    _ack = _ack_material(route, remark, card)
    understanding = build_lesson_understanding(reading, plan)
    try:
        reply_mod(card, understanding)
    except Exception as e:                           # noqa: BLE001 — реплай не должен ронять дирижёра
        reason = f"{reason}; mod-reply: {e}"
    dec = _dispatch_route(route, text, parsed, remark, reason, a_style, a_check, notify, _ack)
    dec["confidence"] = "high"
    dec["understanding"] = understanding
    dec["reading"] = _one_line(reading)
    dec["plan"] = _one_line(plan)
    dec["llm_class"] = cls
    return dec


def _enter_low_wait(llm, text, parsed, remark, reply_mod, notify, now=None):
    """confidence=low: НЕ берём урок в работу вслепую. РЕПЛАЕМ в ТУ ЖЕ модер-группу (на карточку черновика,
    там где учитель написал правку) спрашиваем «Понял так: <reading> — верно?» с ПРОВЕРКОЙ ФАКТА ДОСТАВКИ.
    Боевой reply_mod возвращает message_id (truthy) при доставленном реплае — тогда возвращаем СОСТОЯНИЕ
    ОЖИДАНИЯ (pending_low, status='waiting'): демон держит урок живым и по ответу учителя зовёт
    resume_low_lesson. created_at (epoch) штампуем для 24ч-таймаута (check_low_wait_timeout).

    КЛАСС-ФИКС инцидента #102 (переспрос не доходил до учителя, а демон писал «жду да/поправку»): если
    reply_mod вернул FALSY (нет message_id) или УПАЛ (API-ошибка / нет канала) — это УЖЕ после ретраев в
    боевом sink — урок НЕ оставляем тихо ждать: status='failed' с ДИАГНОЗОМ + карточка-уточнение владельцу
    в 1160 (пусть разберёт вручную). «Тихое ожидание при недоставленном переспросе» = баг, который мы чиним.
    now инъектируется в тестах; reply_mod/notify инъектируемы (голден без сети/Telegram)."""
    reading = _one_line(llm.get("reading"))
    plan = _one_line(llm.get("plan"))
    cls = llm.get("class")
    card = parsed.get("card_msg_id") or ""
    confirm = build_lesson_low_confirm(reading)
    # (a) ДОСТАВКА переспроса реплаем + проверка факта. reply_mod уже ретраит внутри (боевой sink);
    #     сюда доходит финальный вердикт: truthy (доставлено, боевое = message_id) или falsy/исключение.
    delivered, diag = False, ""
    try:
        delivered = bool(reply_mod(card, confirm))
        if not delivered:
            diag = "Bot API не подтвердил доставку (нет message_id)"
    except Exception as e:                           # noqa: BLE001 — sink не должен ронять дирижёра
        delivered, diag = False, f"реплай упал: {e}"

    # (b) НЕ ДОСТАВЛЕНО → урок НЕ висит молча: failed с диагнозом + карточка-уточнение владельцу в 1160.
    if not delivered:
        reason = (f"LLM-low ({cls}): ПЕРЕСПРОС «верно?» НЕ доставлен в модер-группу ({diag}) — "
                  "урок отдан владельцу, тихое ожидание не допускаем (инцидент #102)")
        owner_parsed = dict(parsed); owner_parsed["remark"] = remark or parsed.get("remark", "")
        owner_card = build_owner_clarification_card(
            owner_parsed, f"переспрос «верно?» не дошёл до учителя ({diag}) — реши вручную")
        channel = ""
        try:
            deliv_owner, channel = _normalize_delivery(notify(owner_card))
        except Exception as e:                       # noqa: BLE001
            deliv_owner, channel = False, ""; reason = f"{reason}; notify: {e}"
        if deliv_owner:
            via = f" — карточка владельцу в 1160{(' через ' + channel) if channel else ''}"
        else:
            via = " — карточка владельцу НЕ доставлена (замечание в логе)"
        return {"route": llm.get("route"), "confidence": "low", "delegate": False, "status": "failed",
                "reason": reason, "confirm": confirm, "card_msg_id": card, "reading": reading,
                "plan": plan, "llm_class": cls, "delivered": deliv_owner, "channel": channel, "card": owner_card,
                "result": (f"⚠️ Переспрос «верно?» по low-уроку ({cls}) НЕ доставлен в модер-группу "
                           f"({diag}){via} — урок не висит молча")}

    # (c) ДОСТАВЛЕНО → штатное ожидание ответа учителя (pending_low на диск, задача in_progress под защитой).
    pending = {"text": str(text or ""), "route": llm.get("route"), "class": cls, "reading": reading,
               "plan": plan, "remark": remark, "draft": parsed.get("draft", ""),
               "window": parsed.get("window", ""), "who": parsed.get("who", ""), "card_msg_id": card,
               "created_at": _now_epoch() if now is None else float(now)}
    reason = f"LLM-low: класс {cls} — спросил учителя «верно?» реплаем в модер-группе (ДОСТАВЛЕНО), жду ответа"
    return {"route": llm.get("route"), "confidence": "low", "delegate": False, "status": "waiting",
            "reason": reason, "confirm": confirm, "pending_low": pending, "card_msg_id": card,
            "reading": reading, "plan": plan, "llm_class": cls,
            "result": (f"🤔 Урок неуверенно классифицирован ({cls}) → спросил учителя «верно?» "
                       "реплаем в модер-группе (доставлено), жду «да»/поправку")}


def resume_low_lesson(pending, reply_text, is_approver, classify=None, append_style=None,
                      append_checklist=None, notify_owner=None, reply_moderation=None):
    """Возобновить обработку низко-уверенного урока по ОТВЕТУ учителя в модер-группе (шаг 3). pending —
    сохранённое _enter_low_wait состояние. Ветви:
      • «да» от INTAKE_APPROVERS (is_approver=True) → урок В РАБОТУ как high по маршруту из pending
        (учитель согласился с трактовкой); «да» НЕ от аппрувера → игнор, ждём уполномоченного;
      • текстовая поправка → РОВНО одна пере-классификация (classify по тексту поправки):
          – вернулся high → в работу как high с НОВЫМ прочтением;
          – снова low / брак → реплай «отложил, разберёт владелец» + карточка-уточнение владельцу в 1160.
    Все sink'и инъектируемы (юнит без Telegram/1160/claude). FAIL-SAFE как в handle_lesson_task."""
    a_style = append_style or _default_append_style
    a_check = append_checklist or append_checklist_class
    notify = notify_owner or _default_notify_owner
    reply_mod = reply_moderation or _default_reply_moderation
    pending = pending or {}
    text = str(pending.get("text") or "")
    parsed = parse_lesson_task(text) if text else {}
    card = pending.get("card_msg_id") or parsed.get("card_msg_id") or ""

    # (1) короткое «да» → согласие с трактовкой. Применяем ТОЛЬКО от INTAKE_APPROVERS (гейт спеки).
    if is_lesson_affirmative(reply_text):
        if not is_approver:
            return {"route": pending.get("route"), "confidence": "low", "status": "waiting",
                    "delegate": False, "resumed": "ignored_non_approver",
                    "reason": "«да» не от INTAKE_APPROVERS — подтверждение игнорируется, жду уполномоченного",
                    "result": "🤔 Подтверждение «да» пришло не от аппрувера — жду ответа уполномоченного"}
        route = pending.get("route")
        remark = pending.get("remark") or parsed.get("remark") or ""
        dec = _apply_high(route, text, parsed, remark, f"LLM-low → approver подтвердил «да» (класс {pending.get('class')})",
                          pending.get("reading"), pending.get("plan"), pending.get("class"),
                          a_style, a_check, notify, reply_mod, card)
        dec["resumed"] = "approved"
        return dec

    # (2) текстовая поправка → РОВНО одна пере-классификация по тексту поправки (+ прежний контекст).
    correction = _one_line(reply_text)
    run_classify = classify or classify_lesson_llm
    try:
        llm2 = run_classify(correction, pending.get("draft", ""), pending.get("window", ""))
    except Exception:                                # noqa: BLE001 — сбой думателя не роняет дирижёра
        llm2 = None
    if llm2 and llm2.get("confidence") == "high" and llm2.get("route"):
        # поправка прояснила класс → в работу как high с НОВЫМ прочтением; поправку кладём в замечание
        # (её и запишет STYLE/НАДЗОР-sink; FACT-планировщик увидит её в augmented-тексте задачи).
        text2 = text + f"\n\n[поправка учителя]: {correction}" if text else correction
        parsed2 = dict(parsed); parsed2["remark"] = correction
        dec = _apply_high(llm2["route"], text2, parsed2, correction,
                          f"LLM-low → поправка учителя → high класс {llm2.get('class')}",
                          llm2.get("reading"), llm2.get("plan"), llm2.get("class"),
                          a_style, a_check, notify, reply_mod, card)
        dec["resumed"] = "reclassified_high"
        return dec

    # (3) снова low / брак → «отложил, разберёт владелец» реплаем + карточка-уточнение владельцу в 1160.
    defer = build_lesson_low_defer()
    reason = f"поправка учителя снова неясна ({(llm2 or {}).get('confidence', 'брак')}) → отложено владельцу"
    try:
        reply_mod(card, defer)
    except Exception as e:                           # noqa: BLE001
        reason = f"{reason}; mod-reply: {e}"
    owner_parsed = dict(parsed); owner_parsed["remark"] = correction or parsed.get("remark", "")
    owner_card = build_owner_clarification_card(owner_parsed, "повторно неясно после поправки учителя")
    channel = ""
    try:
        delivered, channel = _normalize_delivery(notify(owner_card))
    except Exception as e:                           # noqa: BLE001
        delivered, channel = False, ""; reason = f"{reason}; notify: {e}"
    if delivered:
        via = f" — доставлено через {channel}" if channel else ""
        result = f"🤷 Урок снова неясен после поправки → отложен владельцу в 1160{via} (не угадываю)"
    else:
        result = "🤷 Урок снова неясен после поправки: канал 1160 недоступен — карточка не доставлена, замечание в логе"
    return {"route": UNCLEAR, "confidence": "low", "delegate": False, "status": "done",
            "resumed": "deferred_to_owner", "reason": reason, "defer": defer, "card": owner_card,
            "delivered": delivered, "channel": channel, "result": result}


def check_low_wait_timeout(pending, now=None, notify_owner=None, timeout=None):
    """Тик таймаута low-ожидания (шаг 4). Учитель молчит на уточнение «верно?» дольше timeout
    (по умолчанию LESSON_LOW_WAIT_TIMEOUT=24ч)? → урок НЕ теряем: карточка-уточнение владельцу в 1160
    (как для неясного) + СНЯТИЕ ожидания (clear_wait=True — демон удаляет pending). Демон зовёт это
    штатным тиком по каждому висящему pending_low. → dec при срабатывании либо None, если:
      • ещё рано (возраст < timeout) — ждём ответа учителя;
      • нет метки времени (created_at) — не таймаутим вслепую;
      • таймаут уже сработал по этому состоянию (pending['timed_out']) — ИДЕМПОТЕНТНОСТЬ: повторный
        тик карточку НЕ задваивает (метим pending ДО доставки).
    notify_owner/now инъектируемы (юнит без Telegram/1160, с подменой времени). FAIL-SAFE как в
    handle_lesson_task: канал 1160 упал → урок в результате/логе (не теряем)."""
    pending = pending or {}
    if pending.get("timed_out"):                     # уже сняли по таймауту → карточку не задваиваем
        return None
    timeout = LESSON_LOW_WAIT_TIMEOUT if timeout is None else timeout
    if low_wait_age_sec(pending, now) < timeout:
        return None                                  # ещё ждём ответа учителя (или метки времени нет → 0)
    pending["timed_out"] = True                      # метим ДО доставки → повторный тик уже вернёт None
    notify = notify_owner or _default_notify_owner
    hours = max(1, int(timeout) // 3600)
    owner_parsed = {"remark": pending.get("remark", ""), "who": pending.get("who", ""),
                    "window": pending.get("window", ""), "card_msg_id": pending.get("card_msg_id", "")}
    card = build_owner_clarification_card(owner_parsed, f"учитель не ответил на уточнение за {hours}ч")
    channel = ""
    reason = f"low-ожидание: нет ответа учителя >{hours}ч → отдано владельцу, ожидание снято"
    try:
        delivered, channel = _normalize_delivery(notify(card))
    except Exception as e:                           # noqa: BLE001 — доставка не должна ронять тик демона
        delivered, channel = False, ""; reason = f"{reason}; notify: {e}"
    if delivered:
        via = f" — доставлено через {channel}" if channel else ""
        result = (f"⏳ Урок без ответа учителя {hours}ч → карточка-уточнение владельцу в 1160{via} "
                  "(ожидание снято, урок не потерян)")
    else:
        result = (f"⏳ Урок без ответа учителя {hours}ч: канал 1160 недоступен — карточка не доставлена, "
                  "замечание в логе (ожидание снято)")
    return {"route": UNCLEAR, "confidence": "low", "delegate": False, "status": "done",
            "resumed": "timeout_to_owner", "clear_wait": True, "reason": reason, "card": card,
            "delivered": delivered, "channel": channel, "result": result}


def handle_lesson_task(text, append_style=None, append_checklist=None, notify_owner=None,
                       classify=None, reply_moderation=None):
    """Классифицировать урок и МАРШРУТИЗИРОВАТЬ. → dict:
      route      — style|fact|supervision|unclear;
      delegate   — True ТОЛЬКО для fact/логика (вызывающий отдаёт задачу планировщику; правка
                   кода/критфактов/FAQ + ТЕСТ — работа думателя, не наша);
      delegate_text — augmented-текст с требованием теста (для планировщика), иначе None;
      status/result — для НЕ-delegate веток: как закрыть задачу (done/failed) и текст карточки;
      ack_subject/ack_where/commit_paths/card_msg_id — материал для подтверждения учителю ПОСЛЕ
                   коммита (шаг 4): суть урока, «куда записан», tracked-файлы к коммиту и координата
                   карточки в модер-группе для реплая. Само подтверждение шлётся ТОЛЬКО после коммита.
    Классификация двухслойна (родитель 334): LLM-думатель classify (шаг 1) ПОВЕРХ keyword. Если
    думатель вернул confidence=high — урок берётся В РАБОТУ СРАЗУ по LLM-маршруту, а учителю уходит
    РЕПЛАЕМ в модер-группу «Понял так: <reading>. Делаю: <plan>» (шаг 2, sink reply_moderation);
    дальше — ШТАТНЫЙ пайплайн (диф→коммит→«урок принят») без изменений, БЕЗ карточки-уточнения
    «переформулируй конкретнее» (для high её не шлём — класс очевиден). confidence=low / None / сбой
    думателя / рубильник LESSON_LLM_ROUTE выключен → откат на keyword-классификатор (поведение прежнее,
    включая карточку-уточнение владельцу для неясного). Результат high несёт confidence/reading/plan/
    understanding/llm_class для рапорта. Побочки инъектируемы (юнит подставляет фейки); боевые дефолты —
    playbook / чек-лист / 1160 / реплай модер-группы. FAIL-SAFE: сбой любого sink → failed-карта."""
    parsed = parse_lesson_task(text)
    remark = parsed["remark"]
    a_style = append_style or _default_append_style
    a_check = append_checklist or append_checklist_class
    notify = notify_owner or _default_notify_owner
    reply_mod = reply_moderation or _default_reply_moderation
    card = parsed.get("card_msg_id") or ""

    # LLM-классификатор (шаг 1) ПОВЕРХ keyword. Инъекция classify = явное намерение теста → идём в LLM
    # всегда; без инъекции — только при боевом рубильнике (иначе байт-в-байт прежний keyword-путь и
    # НИКАКОГО claude-подпроцесса в юнитах). Сбой думателя → llm=None → откат (поведение не хуже).
    use_llm = classify is not None or _lesson_llm_enabled()
    run_classify = classify or classify_lesson_llm
    llm = None
    if use_llm:
        try:
            llm = run_classify(remark, parsed.get("draft", ""), parsed.get("window", ""))
        except Exception:                            # noqa: BLE001 — сбой думателя не роняет дирижёра
            llm = None

    # confidence=high (шаг 2): урок берётся В РАБОТУ СРАЗУ по LLM-маршруту — реплай «Понял так… Делаю…»
    # в модер-группу + штатный пайплайн, БЕЗ карточки-уточнения (класс очевиден).
    if llm and llm.get("confidence") == "high" and llm.get("route"):
        return _apply_high(llm["route"], text, parsed, remark, f"LLM-high: класс {llm.get('class')}",
                           llm.get("reading"), llm.get("plan"), llm.get("class"),
                           a_style, a_check, notify, reply_mod, card)

    # confidence=low (шаг 3): класс размыт — НЕ угадываем и НЕ берём вслепую. Реплаем в ТУ ЖЕ модер-группу
    # спрашиваем учителя «верно?» и сохраняем состояние ожидания (resume_low_lesson доведёт по ответу).
    if llm and llm.get("confidence") == "low" and llm.get("route"):
        return _enter_low_wait(llm, text, parsed, remark, reply_mod, notify)

    # Откат на keyword-классификатор: LLM выключен / думатель вернул None (брак/сбой). Поведение прежнее,
    # включая карточку-уточнение владельцу для неясного (высокоуверенного LLM-сигнала не было).
    route, reason = classify_lesson_remark(remark)
    _ack = _ack_material(route, remark, card)
    return _dispatch_route(route, text, parsed, remark, reason, a_style, a_check, notify, _ack)
