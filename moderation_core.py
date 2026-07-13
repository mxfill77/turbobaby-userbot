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
    return suggest.regenerate_draft(
        draft.get("transcript") or "", draft.get("lang") or "ru", faq,
        bool(draft.get("first_contact")), draft.get("pricing_note") or "", directive,
        park_models=allow, playbook=pb)


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
    if intent == "strategy":
        regen = regen or _default_regen
        final = (regen(draft, faq, reply_text) or "").strip()
    else:
        final = res["final_text"]

    # ЛЮБОЙ уровень правки → повторное подтверждение (стратегия/диктовка/косметика = обязательный ✅).
    # directive = формулировка модератора: сохранится в IPC для кнопки «Запомнить как правило».
    return {"decision": "confirm", "final_text": final, "answer": None, "level": intent,
            "directive": reply_text,
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
    черновика). Чистая функция (без I/O) — легко проверяется юнитом на полноту контекста."""
    kind = lesson.get("kind") or "урок"
    remark = (lesson.get("remark") or "").strip() or "(без текста — см. окно диалога)"
    window = lesson.get("window") if lesson.get("window") is not None else draft.get("client_id")
    ref = draft.get("client_ref") or (f"client_id={window}" if window is not None else "?")
    draft_id = lesson.get("draft_id") if lesson.get("draft_id") is not None else draft.get("id")
    original = (draft.get("draft") or draft.get("final_text") or "").strip() or "(пусто)"
    who = f"@{username}" if username else "?"
    return (f"[урок:{kind} от {who}] родитель 292 — замечание менеджера в копилку обучения\n"
            f"окно диалога: {ref} (client_id={window}) · черновик #{draft_id}\n"
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
