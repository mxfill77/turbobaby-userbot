# -*- coding: utf-8 -*-
"""
moderation_core.py — «мозг» бота-модератора: интерпретация reply менеджера и чистые
decision-функции (без Telegram/IPC I/O — легко мокать).

ТРЁХУРОВНЕВАЯ интерпретация reply — бот определяет ГЛУБИНУ по НАМЕРЕНИЮ (не по длине):
  approve    — «+»/«да»/кнопка ✅ (быстрый путь, без LLM) → отправить как есть (без доп-подтв.)
  reject     — «-»/«нет» (быстрый путь)                   → отклонить
  cosmetic   — меняется КАК сказано, не суть («короче», «добавь про шлемы»)
                                → правим ФОРМУ существующего черновика         → ✅ обязательно
  strategy   — меняется ЧТО/ЗАЧЕМ («дожимай на ADV», «жёстче про депозит»)
                                → ПЕРЕГЕНЕРАЦИЯ С НУЛЯ: реплика = директива верхнего уровня
                                  поверх исходного контекста (транскрипт+FAQ+кап-цена)  → ✅ обязательно
  dictation  — «ответь дословно: …»         → текст менеджера почти как есть   → ✅ обязательно
  question   — вопрос боту («что по ценам?») → ответить по FAQ, черновик НЕ трогать
Двусмысленно → strategy (глубже безопаснее: менеджер подтвердит результат). ЛЮБОЙ уровень
правки → decision='confirm' (повторное подтверждение, НЕ авто-шлём).

SAFETY: сами функции клиенту ничего не шлют. В TEST_MODE решение об отправке даёт
статус 'test_held' (не 'ready') — userbot не отправит; плюс второй замок в userbot.send.
Инварианты цен/критфактов сохраняются в перегенерации (make_system_prompt).
"""

import json
import re

import suggest  # переиспользуем is_approver / parse_approval / модель / ключ


def _default_llm(system, user):
    """LLM классификатора — по ТОМУ ЖЕ маршруту, что генерация: claude CLI (подписка Max) при
    SUGGEST_LLM_VIA_CLI, иначе платный API. Иначе классификатор падал бы на «credit balance too low»."""
    fn = suggest._cli_llm if suggest.SUGGEST_LLM_VIA_CLI else suggest._default_llm
    return fn(system, user)


def _classify_system(faq):
    return (
        "Ты — помощник менеджера в модерации ответов клиентам мотопроката TurboBaby. Дан ЧЕРНОВИК "
        "ответа клиенту и РЕПЛИКА менеджера. Определи УРОВЕНЬ намерения реплики ПО СМЫСЛУ (НЕ по "
        "длине!) и верни СТРОГО JSON (без пояснений):\n"
        '{"intent":"cosmetic|strategy|dictation|question|reject","final_text":"...","answer":"..."}\n'
        "- cosmetic — меняется КАК сказано, НЕ суть («короче», «убери восклицательный», «добавь про "
        "шлемы», «мягче тон»). → final_text = ЧЕРНОВИК с применённой правкой (правь форму, логику/суть "
        "НЕ меняй).\n"
        "- strategy — меняется ЧТО и ЗАЧЕМ предлагать («дожимай на ADV», «жёстче про депозит», "
        "«предложи скидку за неделю», «не продавай — сначала выясни опыт», «скажи что NMAX разберут»). "
        "→ final_text=\"\" (черновик БУДЕТ ПЕРЕГЕНЕРИРОВАН С НУЛЯ отдельно; ты только классифицируй).\n"
        "- dictation — менеджер диктует ТОЧНЫЙ текст клиенту («ответь дословно: …», «напиши ему ровно "
        "так: …»). → final_text = текст менеджера, лишь слегка причёсанный, без отсебятины.\n"
        "- question — менеджер задаёт ВОПРОС тебе (не про правку черновика) → answer = ответ по FAQ, "
        "final_text=\"\".\n"
        "- reject — менеджер отклоняет.\n"
        "Сомневаешься между уровнями → выбирай strategy (глубже безопаснее: менеджер подтвердит "
        "результат). Для cosmetic/dictation: НЕ называй клиенту цену, которой нет в черновике; "
        "критичные факты держи дословно; текст на языке клиента, кратко.\n\n"
        + suggest.CRITICAL_FACTS + "\n\nFAQ:\n" + (faq or "")
    )


def _classify_user(draft, reply_text):
    return f"ЧЕРНОВИК:\n{draft}\n\nРЕПЛИКА МЕНЕДЖЕРА:\n{reply_text}"


def _parse_json(raw):
    raw = (raw or "").strip()
    # вырезаем возможные ```json ... ```
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        s, e = raw.find("{"), raw.rfind("}")
        if 0 <= s < e:
            try:
                return json.loads(raw[s:e + 1])
            except Exception:
                return {}
        return {}


_LEVEL_RU = {"cosmetic": "косметика", "strategy": "стратегия", "dictation": "диктовка"}


def interpret(draft, reply_text, faq, call_llm=None):
    """ТРЁХУРОВНЕВАЯ классификация реплики ПО НАМЕРЕНИЮ (не по длине). Возвращает dict:
    {intent, final_text, answer, need_confirm}. intent ∈
    approve|reject|question|cosmetic|strategy|dictation.
      cosmetic  — правим форму существующего черновика (final_text готов);
      strategy  — перегенерация С НУЛЯ по директиве (final_text='' → перегенерит process_reply);
      dictation — текст менеджера почти как есть (final_text готов).
    Любой уровень правки → need_confirm=True (повторное ✅ обязательно). Двусмысленно → strategy."""
    # быстрый путь: «+»/«да» и «-»/«нет» — без LLM
    action, _ = suggest.parse_approval(reply_text)
    if action == "approve":
        return {"intent": "approve", "final_text": draft, "answer": None, "need_confirm": False}
    if action == "reject":
        return {"intent": "reject", "final_text": None, "answer": None, "need_confirm": False}

    call_llm = call_llm or _default_llm
    data = _parse_json(call_llm(_classify_system(faq), _classify_user(draft, reply_text)))
    intent = (data.get("intent") or "").strip().lower()

    if intent == "cosmetic":
        return {"intent": "cosmetic", "final_text": data.get("final_text") or draft,
                "answer": None, "need_confirm": True}
    if intent == "dictation":
        return {"intent": "dictation", "final_text": data.get("final_text") or reply_text,
                "answer": None, "need_confirm": True}
    if intent == "question":
        return {"intent": "question", "final_text": None,
                "answer": data.get("answer") or "", "need_confirm": False}
    if intent == "reject":
        return {"intent": "reject", "final_text": None, "answer": None, "need_confirm": False}
    # strategy И всё двусмысленное/неизвестное → СТРАТЕГИЯ (перегенерация; глубже безопаснее)
    return {"intent": "strategy", "final_text": "", "answer": None, "need_confirm": True}


# ------------------------- decision-функции (без I/O) ------------------------

def _finalize(final_text, test_mode):
    """Общий хвост: отправлять сейчас (ready) или держать (test_held). DOUBLE-LOCK слой 1."""
    if test_mode:
        return {"decision": "test_held", "final_text": final_text,
                "card": f"🧪 Итоговый текст: {final_text}\n(TEST_MODE — клиенту НЕ отправлено)"}
    return {"decision": "ready", "final_text": final_text,
            "card": "✅ Принято — userbot отправит клиенту."}


def process_callback(draft, action, username, test_mode, candidate=None):
    """Нажатие кнопки. draft — строка IPC. action ∈ yes|no|send|more.
    Возвращает decision-dict (без I/O). Whitelist действует на кнопки."""
    if not suggest.is_approver(username):
        return {"decision": "denied", "final_text": None, "card": "⛔ Нет прав на approve"}
    if action == "no":
        return {"decision": "rejected", "final_text": None, "card": "❌ Отклонено"}
    if action == "more":
        return {"decision": "await_more", "final_text": None,
                "card": "✏️ Пришлите правку ответом на это сообщение."}
    if action == "yes":       # ✅ на исходной карточке — черновик как есть
        return _finalize(draft["draft"], test_mode)
    if action == "send":      # ✅ Отправить на карточке подтверждения — кандидат
        return _finalize(candidate if candidate is not None else draft.get("final_text") or draft["draft"], test_mode)
    return {"decision": "noop", "final_text": None, "card": None}


def _default_regen(draft, faq, directive):
    """СТРАТЕГИЯ: перегенерация черновика С НУЛЯ по директиве поверх исходного контекста из IPC
    (транскрипт+кап-цена сохранены в записи черновика). Модели — только реального парка (Лист1).
    Инъектируется в тестах."""
    try:
        allow = suggest.park_allowlist()
    except Exception:
        allow = None
    try:
        pb = suggest.load_playbook()
    except Exception:
        pb = ""
    transcript = draft.get("transcript") or ""
    lang = draft.get("lang") or "ru"
    note = draft.get("pricing_note") or ""
    first = bool(draft.get("first_contact"))

    def _make(d):
        return suggest.regenerate_draft(transcript, lang, faq, first, note, d,
                                        park_models=allow, playbook=pb)

    # ГАРД НАЛИЧИЯ ПЕРЕД ОТПРАВКОЙ (включён 23.08.2026) — ровно та сборка, которую держит сквозной
    # голден TestEndToEndMax2stix: regenerate_draft (цена КОДОМ) → guard_availability. До этой даты
    # гард в продукте не звался ни одной строкой; здесь он закрывает ВТОРУЮ дверь — путь
    # модераторской перегенерации, который generate_draft не проходит.
    return suggest.guard_availability(
        _make(directive), avail=suggest.availability_from_note(note), lang=lang,
        regenerate=lambda hard: _make(directive + "\n" + hard))["text"]


# ----------- кумулятивные директивы окна диалога (client_windows, #365 шаг 3) -----------
# Директивы модератора в рамках ОДНОГО окна диалога КОПЯТСЯ: каждая strategy-перегенерация
# подмешивает ВСЕ прежние директивы окна + новую, а не только последнюю. Кейс живого провала:
# «новые по умолчанию, года только по запросу» терялась при СЛЕДУЮЩЕЙ перегенерации, потому что
# regen получал лишь последнюю реплику. Накопитель — сама запись черновика в IPC (колонка
# directive): туда пишем кумулятивный блок, оттуда же читаем прежние директивы на новом цикле
# (set_candidate персистит, следующий reply приходит на тот же черновик и видит накопленное).
_WINDOW_DIRECTIVE_BULLET = "• "


def split_window_directives(blob):
    """Разобрать накопленный блок директив окна обратно в список (порядок старые→новые).
    Понимает и одиночную директиву без маркера (легаси/первая правка), и наш маркированный блок."""
    if not (blob or "").strip():
        return []
    lines = [ln.strip() for ln in blob.splitlines() if ln.strip()]
    if any(ln.startswith(_WINDOW_DIRECTIVE_BULLET) for ln in lines):
        out = []
        for ln in lines:
            if ln.startswith(_WINDOW_DIRECTIVE_BULLET):
                ln = ln[len(_WINDOW_DIRECTIVE_BULLET):].strip()
            if ln:
                out.append(ln)
        return out
    return [blob.strip()]   # одиночная директива (возможно многострочная) — как одна


def join_window_directives(items):
    """Собрать накопленные директивы окна в ОДИН блок для хранения/подмешивания в перегенерацию.
    Одна директива — как есть (без маркера, чтобы «Запомнить как правило» видел чистую формулировку);
    несколько — маркированным списком (перегенерация обязана учесть ВСЕ директивы окна)."""
    items = [i.strip() for i in items if (i or "").strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return "\n".join(_WINDOW_DIRECTIVE_BULLET + i for i in items)


def merge_window_directives(prior_blob, new_directive):
    """Кумулятив директив окна: прежние (из draft.directive) + новая, без повторов, порядок сохранён
    (старые→новые). Возвращает блок для regen И для персиста обратно в запись окна."""
    items = split_window_directives(prior_blob)
    nd = (new_directive or "").strip()
    if nd and nd not in items:
        items.append(nd)
    return join_window_directives(items)


def process_reply(draft, reply_text, username, faq, test_mode, call_llm=None, regen=None):
    """Свободный reply менеджера, ТРЁХУРОВНЕВЫЙ. Возвращает decision-dict (без I/O). Whitelist.
    call_llm — классификатор (interpret); regen(draft,faq,directive)->str — СТРАТЕГИЯ-перегенерация
    (по умолчанию _default_regen). Любой уровень правки → 'confirm' (повторное ✅ обязательно)."""
    if not suggest.is_approver(username):
        return {"decision": "denied", "final_text": None, "answer": None, "card": "⛔ Нет прав на approve"}

    res = interpret(draft["draft"], reply_text, faq, call_llm=call_llm)
    intent = res["intent"]

    if intent == "reject":
        return {"decision": "rejected", "final_text": None, "answer": None, "card": "❌ Отклонено"}
    if intent == "question":
        return {"decision": "answer", "final_text": None, "answer": res["answer"], "card": None}
    if intent == "approve":   # «+»/«да» реплика — отправить как есть (без доп-подтверждения)
        out = _finalize(draft["draft"], test_mode)
        out["answer"] = None
        return out

    # cosmetic / strategy / dictation → правка. СТРАТЕГИЯ перегенерит с нуля по директиве.
    directive = reply_text
    if intent == "strategy":
        regen = regen or _default_regen
        # client_windows: директивы окна КУМУЛЯТИВНЫ — перегенерация видит ВСЕ прежние директивы
        # окна (из draft.directive) + новую, иначе прежняя директива окна терялась на след. цикле.
        directive = merge_window_directives(draft.get("directive"), reply_text)
        final = (regen(draft, faq, directive) or "").strip()
    else:
        final = res["final_text"]

    # ЛЮБОЙ уровень правки → повторное подтверждение (стратегия/диктовка/косметика = обязательный ✅).
    # directive = формулировка(-и) модератора: стратегия копит кумулятив окна, косметика/диктовка —
    # разовая реплика. Сохранится в IPC (для кнопки «Запомнить как правило» и накопления окна).
    return {"decision": "confirm", "final_text": final, "answer": None, "level": intent,
            "directive": directive,
            "card": f"[{_LEVEL_RU.get(intent, intent)}] Проверьте перед отправкой:\n\n{final}"}


# ------------------- перехват реплая-обучения на карточку (родитель 292) ------
# Менеджер отвечает на карточку черновика с ПРЕФИКСОМ-триггером — это НЕ обычная правка
# текущего черновика, а ЗАМЕЧАНИЕ-урок в копилку обучения. Окно диалога = client_id из
# карточки (revizor_recon §A.2: ключ окно→черновики). Права СТРОЖЕ approve: учить может
# только INTAKE_APPROVERS (Филипп ×2, Даня, Даша); прочим — вежливый отказ реплаем.
LESSON_TRIGGERS = ("правка:", "урок:", "не так:")


def parse_lesson(reply_text):
    """Если реплай начинается с триггера обучения ('правка:'/'урок:'/'не так:') — вернуть
    {'kind': <триггер без двоеточия>, 'remark': <текст замечания после триггера>}; иначе None.
    Регистр и ведущие пробелы игнорируем; пустое замечание допустимо (remark='')."""
    t = (reply_text or "").lstrip()
    low = t.lower()
    for trig in LESSON_TRIGGERS:
        if low.startswith(trig):
            return {"kind": trig[:-1].strip(), "remark": t[len(trig):].strip()}
    return None


def process_lesson(draft, reply_text, username):
    """Реплай-обучение на карточку черновика (родитель 292, шаг 1). Возвращает decision-dict
    (без I/O):
      not_lesson — реплай НЕ начинается с триггера обучения (обрабатывает обычный reply-путь);
      denied     — триггер есть, но username не в INTAKE_APPROVERS → вежливый отказ реплаем;
      lesson     — распознан урок от учителя: kind/remark + window (client_id окна диалога)."""
    parsed = parse_lesson(reply_text)
    if parsed is None:
        return {"decision": "not_lesson"}
    if not suggest.is_intake_approver(username):
        return {"decision": "denied",
                "card": "🙅 Учить бота (правка/урок/не так) могут только Филипп, Даня и Даша. "
                        "Спасибо, что заметили — передайте им, поправим."}
    return {"decision": "lesson", "kind": parsed["kind"], "remark": parsed["remark"],
            "window": draft.get("client_id"), "draft_id": draft.get("id"),
            "card": f"📝 Принял замечание ({parsed['kind']}) по окну "
                    f"{draft.get('client_ref') or draft.get('client_id')} — учту."}


# ------------------- постановка урока в очередь дирижёра (родитель 292, шаг 2) ------
# Распознанный урок (process_lesson → decision='lesson') становится ШТАТНОЙ задачей в очереди
# дирижёра — from=Filipp-pcloc-dec (родитель локальной декомпозиции). Прямо НИЧЕГО не исполняем
# и гейт очереди НЕ обходим: только enqueue (существующий канал pc_orchestrator.enqueue_pc_task),
# claim/исполнение остаётся на демоне-дирижёре его обычным поллингом. Контекст задачи = текст
# замечания + ИСХОДНЫЙ черновик + окно диалога (client_id/ref из карточки, id черновика).
LESSON_TASK_FROM = "Filipp-pcloc-dec"   # зеркалит pc_orchestrator.PC_LOCAL_DEC_FROM (родитель pcloc-dec)


def build_lesson_task(lesson, draft, username=None):
    """Собрать ПОЛНЫЙ текст задачи-урока для очереди дирижёра из решения process_lesson и карточки.
    Контекст (родитель 292): kind/замечание + исходный черновик + окно диалога (client_id/ref, id
    черновика) + координата карточки в модер-группе (card_msg_id — точка реплая для подтверждения
    «урок принят…» после коммита, шаг 4). Чистая функция (без I/O) — юнит проверяет полноту контекста."""
    kind = lesson.get("kind") or "урок"
    remark = (lesson.get("remark") or "").strip() or "(без текста — см. окно диалога)"
    window = lesson.get("window") if lesson.get("window") is not None else draft.get("client_id")
    ref = draft.get("client_ref") or (f"client_id={window}" if window is not None else "?")
    draft_id = lesson.get("draft_id") if lesson.get("draft_id") is not None else draft.get("id")
    original = (draft.get("draft") or draft.get("final_text") or "").strip() or "(пусто)"
    who = f"@{username}" if username else "?"
    # Координата карточки черновика в модер-группе (родитель 292, шаг 4): подтверждение «урок
    # принят…» ПОСЛЕ коммита moderation_bot пришлёт РЕПЛАЕМ именно на неё. Нет card_msg_id → «?».
    card = draft.get("card_msg_id")
    card_ref = f"msg={card}" if card is not None else "msg=?"
    return (f"[урок:{kind} от {who}] родитель 292 — замечание менеджера в копилку обучения\n"
            f"окно диалога: {ref} (client_id={window}) · черновик #{draft_id}\n"
            f"карточка модер-группы: {card_ref}\n"
            f"Замечание: {remark}\n"
            f"Исходный черновик: {original}")


def _default_lesson_enqueue(task_text, frm):
    """Боевой enqueue урока: ленивый импорт pc_orchestrator (не тянем тяжёлый модуль в бот на
    каждый апдейт — только при реальном уроке). → (ok, id|None, err|None)."""
    import pc_orchestrator          # ленивый — событие редкое, импорт тут не грузит бота
    return pc_orchestrator.enqueue_pc_task(task_text, frm=frm)


def submit_lesson(draft, reply_text, username, enqueue=None):
    """Обёртка над process_lesson (родитель 292, шаг 2): распознанный урок → ШТАТНАЯ задача в
    очереди дирижёра (from=Filipp-pcloc-dec) с полным контекстом. Прямо ничего не исполняем,
    гейт не обходим — только enqueue. `enqueue(task_text, frm) -> (ok, id, err)` инъектируется
    (юнит подставляет фейк; прод берёт _default_lesson_enqueue). Возвращает decision-dict:
      not_lesson/denied — как process_lesson (в очередь ничего не ставим);
      lesson — плюс поля queued(bool)/task_id/task_text; enqueue-fail не роняет обработчик, а
               помечает queued=False и дополняет карточку (замечание не потеряно — повторят)."""
    dec = process_lesson(draft, reply_text, username)
    if dec.get("decision") != "lesson":
        return dec
    task_text = build_lesson_task(dec, draft, username)
    enq = enqueue if enqueue is not None else _default_lesson_enqueue
    dec["task_text"] = task_text
    try:
        ok, tid, err = enq(task_text, LESSON_TASK_FROM)
    except Exception as e:                      # noqa: BLE001 — сеть/Bridge не должны ронять бот
        ok, tid, err = False, None, str(e)
    dec["queued"] = bool(ok)
    dec["task_id"] = tid
    if not ok:
        dec["card"] += f"\n⚠️ В очередь дирижёра не встало ({_clip_err(err)}) — повторите позже."
    return dec


def _clip_err(err, n=120):
    s = str(err or "ошибка")
    return s if len(s) <= n else s[:n - 1] + "…"


# ------------------------- /rules: показ и удаление выученных правил (родитель 112, шаг 5/8) ----
# Учитель управляет КНИГОЙ ПРАВИЛ прямо из модер-группы: `/rules` показывает выученные правила С
# НОМЕРАМИ, удаление — РЕПЛАЕМ на эту карточку по номеру ИЛИ тексту («удали 3» / «удали <текст>» /
# голый «3»). Итог удаления — ПОДТВЕРЖДЕНИЕ ответным сообщением (что убрано и сколько осталось), а
# не молчаливая правка. Права строже approve — книгой правит только INTAKE_APPROVERS (как уроками).
# Дедуп/лимит книги живут в хранилище (suggest.append_playbook_rule), удаление — точечное по одному.
# Функции чистые (без Telegram): показ/удаление инъектируемы, юнит гоняет их без файла/бота.
RULES_SHOW_TRIGGERS = ("/rules", "правила")
RULES_DELETE_VERBS = ("удали", "удалить", "убери", "убрать", "удаление", "delete", "del", "rm")
_RULES_NUM_RE = re.compile(r"^[#№]?\s*\d+$")
_RULES_WORD_RE = re.compile(r"^(правило|правила|правил|rule)\s*[№#]?\s*", re.IGNORECASE)


def is_rules_command(text):
    """True, если реплика — команда показа книги правил (`/rules`, регистр/пробелы терпимы; голое
    слово «правила» тоже считаем командой). Аргументы после /rules игнорируем."""
    t = (text or "").strip().lower()
    return t == "правила" or t == "/rules" or t.startswith("/rules ") or t.startswith("/rules@")


def render_rules_card(username, list_rules=None):
    """Карточка `/rules`: выученные правила книги С НОМЕРАМИ (номер = селектор для удаления). Права
    строже approve — смотреть/править книгу могут только INTAKE_APPROVERS. list_rules инъектируем
    (юнит без файла; прод — suggest.list_playbook_rules). → текст карточки."""
    if not suggest.is_intake_approver(username):
        return "🙅 Смотреть и править книгу правил могут только Филипп, Даня и Даша."
    rules = (list_rules or suggest.list_playbook_rules)()
    if not rules:
        return "📖 Книга правил пуста — выученных правил пока нет."
    out = ["📖 Выученные правила (удалить — реплаем «удали N» или «удали <текст>»):"]
    for r in rules:
        date = f" ({r['date']})" if r.get("date") else ""
        out.append(f"{r['n']}.{date} {r['rule']}")
    return "\n".join(out)


def parse_rules_delete(reply_text):
    """Селектор удаления из реплая на карточку `/rules`: номер ИЛИ текст правила. Формы: «удали 3» /
    «удалить правило 3» / «убери <текст>» / «delete 3» / голый «3». Ведущий глагол и слово «правило»
    отбрасываем. Голый ТЕКСТ без глагола НЕ считаем удалением (не роняем случайный реплай в удаление)
    — только голый номер однозначен. → строка-селектор ЛИБО None (реплай не про удаление)."""
    t = " ".join((reply_text or "").split()).strip()
    if not t:
        return None
    low = t.lower()
    verb = next((v for v in RULES_DELETE_VERBS if low == v or low.startswith(v + " ")), None)
    if verb is not None:
        rest = _RULES_WORD_RE.sub("", t[len(verb):].strip()).strip()
        return rest or None
    if _RULES_NUM_RE.match(t):                       # голый номер на карточке-списке — однозначно удаление
        return t.lstrip("#№ ").strip()
    return None


def process_rules_delete(reply_text, username, remove=None):
    """Удаление правила из книги реплаем на карточку `/rules` — по номеру или тексту, С ПОДТВЕРЖДЕНИЕМ
    ответным сообщением (карточка-итог: что убрано и сколько осталось). Права строже approve (только
    INTAKE_APPROVERS). remove инъектируем (юнит без файла; прод — suggest.remove_playbook_rule).
    → decision-dict (без I/O):
      not_rules — реплай не про удаление правила (обычный reply-путь обрабатывает дальше);
      denied    — селектор есть, но username не в INTAKE_APPROVERS → вежливый отказ;
      removed/not_found/ambiguous/empty/error — итог удаления + карточка-подтверждение."""
    selector = parse_rules_delete(reply_text)
    if selector is None:
        return {"decision": "not_rules"}
    if not suggest.is_intake_approver(username):
        return {"decision": "denied",
                "card": "🙅 Править книгу правил могут только Филипп, Даня и Даша."}
    remove = remove or suggest.remove_playbook_rule
    try:
        res = remove(selector)
    except Exception as e:                           # noqa: BLE001 — сбой хранилища не роняет бот
        return {"decision": "error",
                "card": f"⚠️ Не удалось удалить правило ({_clip_err(e)}) — попробуйте позже."}
    st = (res or {}).get("status")
    if st == "removed":
        return {"decision": "removed", "n": res.get("n"), "rule": res.get("rule"),
                "card": f"🗑 Удалил правило #{res.get('n')}: «{res.get('rule')}». "
                        f"Осталось правил: {res.get('remaining')}."}
    if st == "ambiguous":
        opts = "; ".join(f"#{m['n']} «{m['rule']}»" for m in res.get("matches", []))
        return {"decision": "ambiguous",
                "card": f"🤔 Под «{selector}» подходит несколько правил: {opts}. "
                        f"Уточни номером — удалю одно."}
    if st == "empty":
        return {"decision": "empty", "card": "📖 Книга правил пуста — удалять нечего."}
    if st == "not_found":
        return {"decision": "not_found",
                "card": f"❓ Не нашёл правила «{selector}» в книге. Пришли /rules и удаляй по номеру."}
    return {"decision": "error",
            "card": "⚠️ Не удалось удалить правило (книга недоступна) — попробуйте позже."}


# ------------------------- захват правки в playbook (Фаза 2) ------------------

def _distill_system():
    return (
        "Ты ведёшь КНИГУ ПРАВИЛ менеджера мотопроката TurboBaby. Дана РЕПЛИКА-ДИРЕКТИВА менеджера "
        "(как отвечать клиентам). Сформулируй её как ОДНО короткое устойчивое ПРАВИЛО для бота: "
        "1-2 строки, повелительно, по-русски, без воды и без кавычек. Верни ТОЛЬКО текст правила."
    )


def distill_rule(directive, call_llm=None):
    """Дистилляция формулировки модератора в короткое правило (тот же LLM-маршрут, что классификатор
    — claude CLI/Max). → строка правила (или '' при пустом/ошибке)."""
    call_llm = call_llm or _default_llm
    out = call_llm(_distill_system(), (directive or "").strip())
    return " ".join((out or "").split()).strip()


def remember_rule(draft, username, call_llm=None, distill=None, appender=None, now=None):
    """Кнопка «📌 Запомнить как правило»: approver-гейт → дистилляция ДИРЕКТИВЫ модератора → APPEND
    в playbook. Возвращает decision-dict (без I/O карточки — постит moderation_bot). distill/appender
    инъектируются в тестах. FAIL-SAFE: сбой записи → 'not_saved' (правка уже применена к черновику)."""
    if not suggest.is_approver(username):
        return {"decision": "denied", "rule": None, "card": "⛔ Нет прав на запись правил"}
    directive = (draft.get("directive") or "").strip()
    if not directive:
        return {"decision": "no_directive", "rule": None,
                "card": "⚠️ Нет директивы для правила (нажмите после своей правки-реплики)"}
    distill = distill or distill_rule
    try:
        rule = distill(directive, call_llm=call_llm)
    except Exception:
        rule = ""
    rule = " ".join((rule or "").split()).strip() or directive   # фолбэк: сама формулировка модератора
    appender = appender or suggest.append_playbook_rule
    try:
        status = appender(rule, now=now)
    except Exception:
        status = "error"
    if status == "added":
        return {"decision": "remembered", "rule": rule, "card": f"📌 Записано в правила: {rule}"}
    if status == "duplicate":
        return {"decision": "duplicate", "rule": rule, "card": "📌 Уже есть похожее правило"}
    return {"decision": "not_saved", "rule": rule,
            "card": "⚠️ Правило НЕ сохранилось — правка применена разово (к текущему черновику)"}
