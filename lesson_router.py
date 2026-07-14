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
    return {"remark": remark, "kind": kind, "who": who, "window": window, "card_msg_id": card_msg_id}


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


# ------------------------------- обработчик задачи-урока ----------------------------------
def handle_lesson_task(text, append_style=None, append_checklist=None, notify_owner=None):
    """Классифицировать урок и МАРШРУТИЗИРОВАТЬ. → dict:
      route      — style|fact|supervision|unclear;
      delegate   — True ТОЛЬКО для fact/логика (вызывающий отдаёт задачу планировщику; правка
                   кода/критфактов/FAQ + ТЕСТ — работа думателя, не наша);
      delegate_text — augmented-текст с требованием теста (для планировщика), иначе None;
      status/result — для НЕ-delegate веток: как закрыть задачу (done/failed) и текст карточки;
      ack_subject/ack_where/commit_paths/card_msg_id — материал для подтверждения учителю ПОСЛЕ
                   коммита (шаг 4): суть урока, «куда записан», tracked-файлы к коммиту и координата
                   карточки в модер-группе для реплая. Само подтверждение шлётся ТОЛЬКО после коммита.
    Побочки инъектируемы (юнит подставляет фейки); боевые дефолты — playbook / чек-лист / 1160.
    FAIL-SAFE: сбой любого sink → failed-карта (замечание НЕ теряем — видно владельцу, повторят)."""
    parsed = parse_lesson_task(text)
    remark = parsed["remark"]
    route, reason = classify_lesson_remark(remark)
    a_style = append_style or _default_append_style
    a_check = append_checklist or append_checklist_class
    notify = notify_owner or _default_notify_owner
    # Материал подтверждения (шаг 4) — одинаков для всех применённых веток; шлётся ТОЛЬКО после коммита.
    subj = _one_line(remark)
    card = parsed.get("card_msg_id") or ""
    _ack = {"ack_subject": subj, "ack_where": _ROUTE_WHERE.get(route, "книгу правил"),
            "commit_paths": list(_ROUTE_COMMIT_PATHS.get(route, [])), "card_msg_id": card}

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
