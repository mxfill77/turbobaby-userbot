# -*- coding: utf-8 -*-
"""
moderation_core.py — «мозг» бота-модератора: интерпретация reply менеджера и чистые
decision-функции (без Telegram/IPC I/O — легко мокать).

Интенты reply (относительно черновика):
  approve      — «+»/«да»/кнопка ✅        → отправить черновик как есть (без доп-подтв.)
  replacement  — готовый текст замены       → отправить ЕГО (без доп-подтв.)
  instruction  — правка («вместо X — Y», «короче», «добавь про депозит»)
                                             → применить к черновику → ПОДТВЕРЖДЕНИЕ обязательно
  command      — «отправь …»/«напиши что …» → составить текст → ПОДТВЕРЖДЕНИЕ обязательно
  reject       — «-»/«нет»/«отклони»        → отклонить
  question     — вопрос боту («что по ценам?») → ответить по FAQ, черновик НЕ трогать

SAFETY: сами функции клиенту ничего не шлют. В TEST_MODE решение об отправке даёт
статус 'test_held' (не 'ready') — userbot не отправит; плюс второй замок в userbot.send.
"""

import json

import suggest  # переиспользуем is_approver / parse_approval / модель / ключ


def _default_llm(system, user):
    import anthropic
    c = anthropic.Anthropic(api_key=suggest.ANTHROPIC_API_KEY)
    resp = c.messages.create(
        model=suggest.ANTHROPIC_MODEL,
        max_tokens=suggest.LLM_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


def _classify_system(faq):
    return (
        "Ты — помощник менеджера в модерации ответов клиентам мотопроката TurboBaby. "
        "Дан ЧЕРНОВИК ответа клиенту и РЕПЛИКА менеджера. Классифицируй реплику и верни "
        "СТРОГО JSON (без пояснений) с полями:\n"
        '{"intent":"replacement|instruction|command|question|reject",'
        ' "final_text":"...", "answer":"..."}\n'
        "- replacement: менеджер прислал ГОТОВЫЙ текст ответа клиенту → final_text = этот текст.\n"
        "- instruction: менеджер просит ПОПРАВИТЬ черновик (короче, вместо X — Y, добавь про …) "
        "→ final_text = черновик С ПРИМЕНЁННОЙ правкой.\n"
        "- command: менеджер велит НАПИСАТЬ/ОТПРАВИТЬ что-то по смыслу → final_text = составленный текст.\n"
        "- question: менеджер задаёт ВОПРОС тебе (не про правку) → answer = ответ по FAQ, final_text=\"\".\n"
        "- reject: менеджер отклоняет.\n"
        "Текст ответа клиенту держи на языке клиента, кратко, по FAQ.\n\nFAQ:\n" + (faq or "")
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


def interpret(draft, reply_text, faq, call_llm=None):
    """Определить интент реплики. Возвращает dict:
    {intent, final_text, answer, need_confirm}."""
    # быстрый путь: «+»/«да» и «-»/«нет» — без LLM
    action, _ = suggest.parse_approval(reply_text)
    if action == "approve":
        return {"intent": "approve", "final_text": draft, "answer": None, "need_confirm": False}
    if action == "reject":
        return {"intent": "reject", "final_text": None, "answer": None, "need_confirm": False}

    call_llm = call_llm or _default_llm
    data = _parse_json(call_llm(_classify_system(faq), _classify_user(draft, reply_text)))
    intent = (data.get("intent") or "").strip().lower()

    if intent == "replacement":
        return {"intent": "replacement", "final_text": data.get("final_text") or reply_text,
                "answer": None, "need_confirm": False}
    if intent in ("instruction", "command"):
        return {"intent": intent, "final_text": data.get("final_text") or "",
                "answer": None, "need_confirm": True}
    if intent == "question":
        return {"intent": "question", "final_text": None,
                "answer": data.get("answer") or "", "need_confirm": False}
    if intent == "reject":
        return {"intent": "reject", "final_text": None, "answer": None, "need_confirm": False}
    # неизвестно/двусмысленно → БЕЗОПАСНЫЙ путь: трактуем как правку с обязательным подтверждением
    return {"intent": "instruction", "final_text": data.get("final_text") or reply_text,
            "answer": None, "need_confirm": True}


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


def process_reply(draft, reply_text, username, faq, test_mode, call_llm=None):
    """Свободный reply менеджера. Возвращает decision-dict (без I/O). Whitelist действует."""
    if not suggest.is_approver(username):
        return {"decision": "denied", "final_text": None, "answer": None, "card": "⛔ Нет прав на approve"}

    res = interpret(draft["draft"], reply_text, faq, call_llm=call_llm)
    intent = res["intent"]

    if intent == "reject":
        return {"decision": "rejected", "final_text": None, "answer": None, "card": "❌ Отклонено"}
    if intent == "question":
        return {"decision": "answer", "final_text": None, "answer": res["answer"], "card": None}

    final = res["final_text"]
    if res["need_confirm"]:   # instruction/command → показать результат + кнопки подтверждения
        return {"decision": "confirm", "final_text": final, "answer": None,
                "card": f"Проверьте правку перед отправкой:\n\n{final}"}
    # approve / replacement → без доп-подтверждения (double-lock слой 1 внутри _finalize)
    out = _finalize(final, test_mode)
    out["answer"] = None
    return out
