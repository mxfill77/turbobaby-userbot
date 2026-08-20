# -*- coding: utf-8 -*-
"""
moderation_card.py — КАРТОЧКА МОДЕРАЦИИ: вопрос клиента, вариант ответа бота, ПОЧЕМУ именно
так, и три действия человека. Отправка клиенту — ТОЛЬКО по нажатию человека.

РАЗМОРОЗКА (решение владельца 19.08.2026, дословно): клиентский контур разморожен для отправки
с ОБЯЗАТЕЛЬНЫМ подтверждением человека; автоматическая отправка без подтверждения остаётся
замороженной. Этот модуль — про первую половину и НИ ОДНОЙ строкой про вторую: он НИЧЕГО не
отправляет и отправлять не умеет. Максимум, что он делает, — переводит строку очереди в
'ready', и делает это ИСКЛЮЧИТЕЛЬНО из явного действия человека (`decide`). Ни таймаута, ни
ретрая, ни ветки «никто не ответил — шлём сами» здесь нет: нет входа, из которого такая ветка
могла бы стартовать.

ЧЕТЫРЕ ВЕЩИ, РАДИ КОТОРЫХ МОДУЛЬ ЗАВЕДЁН

1. «ПОЧЕМУ» рядом с ответом. Без него карточка бесполезна: человек видит текст и не может
   сказать, где бот ошибся. Почему собирается НЕ пересказом черновика, а разбором `pricing_note`
   — той самой служебной ленты, которой userbot везёт в очередь основания цены. Блоки достаём
   ЖИВЫМИ экстракторами `suggest` (`_quote_block_from_note`/`_sheet_block_from_note`/…), своих
   копий регулярок здесь НЕТ — разъезжаться нечему.

2. «НЕ СЧИТАЮ» вместо выдуманного числа. Три класса названы владельцем: период через границу
   сезонов, срок от 30 суток, модель без цены. Каждый — ОТДЕЛЬНАЯ строка карточки, а не тихое
   умолчание и не «примерно столько».

3. Первый ответивший закрывает карточку. Замок живёт не здесь, а в СУБД
   (`moderation_ipc.claim_decision`, UPDATE с предусловием по статусу): любой захват, кроме
   первого, возвращает (False, строка-победитель), и опоздавший видит, что уже решено и кем.
   Двух сообщений одному клиенту не бывает ни при какой гонке — в том числе при повторном тапе
   по карточке, которую уже отправили (именно этой дорогой второе сообщение и уходило: у
   `set_decision` предусловия нет вовсе).

4. Карточка рождается на ЛЮБОЕ сообщение клиента. Самостоятельности «по уверенности» у бота
   нет и ручки такой здесь не заведено (порога/threshold/confidence в модуле нет ни одного).
   Черновик пустой — это НЕ причина промолчать: карточка честно скажет «бот ответа не собрал»
   и покажет, чего не хватило.

ЧЕГО МОДУЛЬ НЕ ДЕЛАЕТ (честно): он не постит карточку в Telegram и не рисует кнопки — это
работа `moderation_bot`, которого этот заход НЕ трогает и на живых ботов НЕ выкатывает. Здесь
только чистая логика и захват решения; точки подключения названы в
`docs/artifacts/2026-08-19-moderation-card.md`.
"""

import datetime

import price_source
import suggest

# ------------------------------- три действия человека ------------------------

ACT_SEND = "send"       # отправить как есть
ACT_EDIT = "edit"       # поправить текст и отправить
ACT_REJECT = "reject"   # отклонить, ничего не отправляя

ACTIONS = (
    (ACT_SEND, "✅ Отправить как есть"),
    (ACT_EDIT, "✏️ Поправить и отправить"),
    (ACT_REJECT, "❌ Отклонить"),
)


def actions():
    """Ровно три действия человека над карточкой. → кортеж (код, подпись)."""
    return ACTIONS


# ------------------------------- «почему»: разбор pricing_note ----------------

KIND_QUOTE = "quote"    # точечная цена по модели+датам (строка листа дословно)
KIND_SHEET = "sheet"    # прайс по всему парку
KIND_NONE = "none"      # числа нет вовсе

# Чего боту НЕ ХВАТИЛО. Каждая строка: (код, ДОСЛОВНЫЙ фрагмент живой инструкции
# suggest.build_pricing_note, человеческая причина для карточки, убивает ли цену).
# Фрагменты не сочинены: тест сверяет КАЖДЫЙ с исходником suggest — перепишут инструкцию,
# тест покраснеет громко, а карточка не начнёт молча терять «почему».
GAP_NO_DATES = "no_dates"
GAP_UNPARSED = "unparsed"
GAP_AMBIGUOUS = "ambiguous_model"
GAP_SANITY = "sanity"
GAP_BUSY = "none_available"
GAP_NO_QUOTE = "no_quote"
GAP_NO_SHEET = "no_sheet"
GAP_PAST_START = "past_start"
GAP_MIN_TERM = "min_term"

GAPS = (
    (GAP_NO_DATES, "дат аренды в диалоге НЕТ",
     "клиент не назвал даты аренды", True),
    (GAP_UNPARSED, "не удалось однозначно разобрать модель/даты",
     "модель или даты не разобрались", True),
    (GAP_AMBIGUOUS, "а в парке несколько вариантов",
     "названа серия, а не модель — в парке несколько вариантов с разной ценой", True),
    (GAP_SANITY, "расчёт по датам не сходится",
     "длительность из дат не сошлась с котировкой", True),
    (GAP_BUSY, "все подходящие байки заняты",
     "на эти даты свободных байков нет", True),
    (GAP_NO_SHEET, "точные цены из Календаря сейчас недоступны",
     "прайс по парку не собрался — Календарь не ответил", True),
    (GAP_NO_QUOTE, "точная цена из Календаря сейчас недоступна",
     "Календарь цену по этой модели не отдал", True),
    (GAP_PAST_START, "УЖЕ ПРОШЁЛ",
     "старт аренды, названный клиентом, уже прошёл", True),
    (GAP_MIN_TERM, "короче срок не оформляем",
     "срок короче минимального — предложен минимальный", False),
)

# ------------------------------- «не считаю» ----------------------------------

CANNOT_SEASON = "season_edge"    # период через границу сезонов
CANNOT_LONG = "long_term"        # срок от 30 суток
CANNOT_NO_PRICE = "no_price"     # модель без цены

# Порог месячного тарифа берём ЖИВОЙ константой suggest (единица кепки — «฿ за месяц»),
# а не своей копией: сменят порог там — поедет и карточка.
LONG_TERM_DAYS = suggest._CAP_MIN_DAYS

SEASON_ONE = "one"           # старт и конец в одном периоде
SEASON_CROSSES = "crosses"   # период пересекает границу сезонов
SEASON_UNKNOWN = "unknown"   # проверить нечем — ТРЕТИЙ исход, а не «всё хорошо»


def _s(value):
    """Поле карточки → строка. None приводим к пустой строке ЯВНО, отдельной веткой: «поля нет»
    и «поле пустое» карточка обязана уметь различать в вызывающем коде."""
    if value is None:
        return ""
    return str(value).strip()


def _get(rec, key):
    """Поле записи (строки очереди либо разбора диалога) → значение | None.

    Отсутствующая запись целиком (`None`) означает «разбора не было» — то есть «не знаю» по
    КАЖДОМУ полю. Пустой словарь-заглушки здесь СОЗНАТЕЛЬНО нет: он превратил бы «разбора не
    было» в «поля пустые», а это разные вещи (первое обязано доехать до карточки третьим
    исходом «сезон: не знаю», второе — нет)."""
    if rec is None:
        return None
    return rec.get(key)


def _date(iso):
    """'ГГГГ-ММ-ДД' → date | None. Не дата — это «не знаю», а НЕ сегодняшний день."""
    s = _s(iso)
    if len(s) < 10:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _season_block(note):
    """Служебная сезонная пометка из скобок pricing_note → текст | None."""
    m = suggest._SEASON_BLOCK_RE.search(note)
    if m is None:
        return None
    return m.group(1)


def _blocks(note):
    """Служебные блоки pricing_note ЖИВЫМИ экстракторами suggest. → dict."""
    return {
        "quote": suggest._quote_block_from_note(note),
        "sheet": suggest._sheet_block_from_note(note),
        "season": _season_block(note),
        "delivery": suggest._delivery_block_from_note(note),
        "delivery_ask": suggest._DELIVERY_ASK_BLOCK_RE.search(note) is not None,
    }


def price_facts(pricing_note):
    """Разбор служебной ленты цены в ФАКТЫ для карточки. → dict:
      kind      — quote | sheet | none (откуда взялось число и есть ли оно вообще);
      line      — та самая строка цены ДОСЛОВНО (или None, если числа нет);
      season    — сезонная пометка из ленты (или None);
      delivery  — строка доставки (или None); delivery_ask — зону не опознали, бот спросит район;
      gaps      — кортеж строк GAPS, чьи инструкции стоят в ленте (чего боту не хватило)."""
    note = _s(pricing_note)
    b = _blocks(note)
    gaps = tuple(g for g in GAPS if g[1] in note)
    if b["quote"] is not None:
        kind, line = KIND_QUOTE, b["quote"]
    elif b["sheet"] is not None:
        kind, line = KIND_SHEET, b["sheet"]
    else:
        kind, line = KIND_NONE, None
    return {"kind": kind, "line": line, "season": b["season"], "delivery": b["delivery"],
            "delivery_ask": b["delivery_ask"], "gaps": gaps}


def _period_name(period):
    """Период файла цен → человеческое имя для карточки («P5 ПИК»)."""
    key = _s(period.get("key"))
    name = _s(period.get("name"))
    if len(key) > 0 and len(name) > 0:
        return key + " " + name
    if len(name) > 0:
        return name
    return key


def season_span(iso_start, iso_end, doc=None):
    """Сезонные периоды на КРАЯХ срока аренды. → (вердикт, (имя старта, имя конца), пояснение).

    Вердикт SEASON_UNKNOWN — полноправный третий исход: таблицы периодов нет, дат нет, дата вне
    периодов файла. «Не смог проверить» НЕ превращается в «проверено и хорошо» ни одной веткой.

    Конец срока берём КАК НАЗВАН клиентом: в «Календаре» обе даты входят в срок. На самой
    границе это назовёт пересечением срок, задевающий её последним днём, — перекос СОЗНАТЕЛЬНО
    в сторону «спроси человека»: лишний вопрос дешевле неверной цены."""
    d = price_source.load() if doc is None else doc
    if d is None:
        return SEASON_UNKNOWN, None, "таблица периодов не прочиталась (price_source.json)"
    ds, de = _date(iso_start), _date(iso_end)
    if ds is None or de is None:
        return SEASON_UNKNOWN, None, "дат аренды нет — сезон не определяю"
    p1, p2 = price_source.period_of(d, ds), price_source.period_of(d, de)
    if p1 is None or p2 is None:
        return SEASON_UNKNOWN, None, "дата вне периодов файла цен"
    n1, n2 = _period_name(p1), _period_name(p2)
    if p1.get("key") == p2.get("key"):
        return SEASON_ONE, (n1, n2), n1
    return SEASON_CROSSES, (n1, n2), n1 + " → " + n2


def cannot_compute(facts, hints, doc=None):
    """Чего бот посчитать НЕ МОЖЕТ. → кортеж {code, line}; пусто — считать можно.

    Три класса названы владельцем 19.08.2026: период через границу сезонов, срок от 30 суток,
    модель без цены. Каждый попадёт в карточку ОТДЕЛЬНОЙ строкой."""
    out = []
    verdict, _names, detail = season_span(_get(hints, "iso_start"), _get(hints, "iso_end"), doc=doc)
    if verdict == SEASON_CROSSES:
        out.append({"code": CANNOT_SEASON,
                    "line": "период пересекает границу сезонов (" + detail +
                            ") — цена в файле берётся по дате НАЧАЛА, на такой срок её считает человек"})
    days = _get(hints, "hint_days")
    long_by_days = isinstance(days, int) and days >= LONG_TERM_DAYS
    if long_by_days or bool(_get(hints, "monthly")):
        if long_by_days:
            term = "срок " + str(days) + " сут"
        else:
            term = "запрошен месяц"
        out.append({"code": CANNOT_LONG,
                    "line": term + " — от " + str(LONG_TERM_DAYS) +
                            " суток тариф месячный, точную цену считает человек"})
    if facts["kind"] == KIND_NONE:
        killers = [g for g in facts["gaps"] if g[3]]
        if len(killers) > 0:
            why = "; ".join(g[2] for g in killers)
        else:
            why = "Календарь числа не дал"
        out.append({"code": CANNOT_NO_PRICE, "line": "цены нет — " + why})
    return tuple(out)


# ------------------------------- сборка карточки ------------------------------

NOT_COMPUTED = "не считаю"
FOOT = ("Отправка — ТОЛЬКО по нажатию человека. Карточка закрывается за первым ответившим; "
        "предела ожидания нет — висит, пока не ответят.")


def _head(draft):
    parts = ["🗂 КАРТОЧКА МОДЕРАЦИИ #" + _s(_get(draft, "id"))]
    ref = _s(_get(draft, "client_ref"))
    if len(ref) > 0:
        parts.append(ref)
    if _get(draft, "first_contact"):
        parts.append("первый контакт")
    return " · ".join(parts)


def _why_lines(facts, cannot, hints):
    """Блок «почему именно так»: цена, откуда, сезон, срок, доставка, чего не хватило."""
    out = []
    blocked = len(cannot) > 0
    if facts["kind"] == KIND_NONE or blocked:
        out.append("• цена: " + NOT_COMPUTED)
        if facts["line"] is not None:
            out.append("• сырьё Календаря (НЕ итог для клиента): " + facts["line"])
    else:
        out.append("• цена: " + facts["line"])
    if facts["kind"] == KIND_QUOTE:
        out.append("• откуда: Календарь бронирования, строка листа ДОСЛОВНО (код, не LLM)")
    elif facts["kind"] == KIND_SHEET:
        out.append("• откуда: прайс по всему парку из Календаря (код, не LLM)")
    else:
        out.append("• откуда: числа нет — в ленте цены стоит запрет называть цифру")
    verdict, _names, detail = season_span(_get(hints, "iso_start"), _get(hints, "iso_end"))
    if verdict == SEASON_ONE:
        out.append("• сезон: " + detail + " (по дате начала аренды), границу не пересекает")
    elif verdict == SEASON_CROSSES:
        out.append("• сезон: " + detail + " — ГРАНИЦА ПЕРЕСЕЧЕНА")
    else:
        out.append("• сезон: не знаю — " + detail)
    mark = _s(facts["season"])
    if len(mark) > 0:
        out.append("• пометка сезона из ленты: " + mark)
    ds, de = _s(_get(hints, "iso_start")), _s(_get(hints, "iso_end"))
    days = _get(hints, "hint_days")
    if len(ds) > 0 and len(de) > 0:
        term = "• срок: " + ds + " → " + de
        if isinstance(days, int):
            term += " (" + str(days) + " сут)"
        out.append(term)
    else:
        out.append("• срок: дат в диалоге нет")
    if facts["delivery"] is not None:
        out.append("• доставка: " + facts["delivery"])
    elif facts["delivery_ask"]:
        out.append("• доставка: зону по локации не опознал — бот спросит район")
    if len(facts["gaps"]) > 0:
        out.append("• чего боту не хватило: " + "; ".join(g[2] for g in facts["gaps"]))
    elif blocked:
        # Претензий у ленты цены нет, а считать всё равно нельзя: ограничение не в данных, а в
        # самом сроке/сезоне. Писать здесь «всё нужное было» рядом с «не считаю» — врать в лицо.
        out.append("• чего боту не хватило: лента цены претензий не заявила — ограничение ниже (⛔)")
    else:
        out.append("• чего боту не хватило: всё нужное было")
    return out


def build(draft, hints=None, doc=None, today=None):
    """Собрать карточку модерации по строке очереди. → dict {text, facts, cannot, hints}.

    Чистая функция: ни Telegram, ни очереди, ни сети (кроме чтения файла периодов с диска).
    hints инъектируются; по умолчанию считаются ЖИВЫМ разбором транскрипта из самой строки."""
    facts = price_facts(_get(draft, "pricing_note"))
    if hints is None:
        h = suggest.extract_booking_hints(_s(_get(draft, "transcript")), today=today)
    else:
        h = hints
    cannot = cannot_compute(facts, h, doc=doc)
    incoming = _s(_get(draft, "incoming"))
    if len(incoming) == 0:
        incoming = "(текста сообщения в карточке нет)"
    body = _s(_get(draft, "draft"))
    if len(body) == 0:
        body = "(бот ответа не собрал — отправлять нечего, поправьте текст или отклоните)"
    lines = [_head(draft), "", "💬 КЛИЕНТ: " + incoming, "", "🤖 БОТ ПРЕДЛАГАЕТ ОТПРАВИТЬ:", body,
             "", "🧾 ПОЧЕМУ ИМЕННО ТАК:"]
    lines.extend(_why_lines(facts, cannot, h))
    if len(cannot) > 0:
        lines.append("")
        for c in cannot:
            lines.append("⛔ НЕ СЧИТАЮ: " + c["line"])
    lines.append("")
    lines.append("ЧТО МОЖНО СДЕЛАТЬ: " + " · ".join(label for _code, label in ACTIONS))
    lines.append(FOOT)
    return {"text": "\n".join(lines), "facts": facts, "cannot": cannot, "hints": h}


def render(draft, hints=None, doc=None, today=None):
    """Текст карточки модерации (см. build)."""
    return build(draft, hints=hints, doc=doc, today=today)["text"]


# ------------------------------- решение человека -----------------------------

_STATUS_RU = {
    "ready": "отправляем клиенту",
    "test_held": "придержано (TEST_MODE — клиенту НЕ уходит)",
    "rejected": "отклонено",
    "sent": "уже отправлено клиенту",
    "failed": "отправка не удалась",
    "new": "ещё не решено",
    "posted": "ещё не решено",
    "pending_confirm": "ждёт подтверждения правки",
    "superseded": "предложение устарело — клиент написал снова",
}

# Статус устаревшего предложения (единица «разговор», 20.08.2026). Держим ЛИТЕРАЛОМ, а не
# импортом очереди: карточка обязана оставаться чистой от I/O-модуля, а тест сверяет литерал с
# живым `moderation_ipc.STATUS_SUPERSEDED` — разъехаться молча им нечем.
SUPERSEDED = "superseded"


def render_closed(row):
    """Что видит опоздавший: карточка закрыта, кем и чем. Второго сообщения не будет."""
    if row is None:
        return "🔒 Карточка закрыта: строки очереди больше нет. Ничего не отправлено этим нажатием."
    status = _s(row.get("status"))
    if status == SUPERSEDED:
        # Отдельная ветка, а не строка словаря: здесь НЕТ решившего человека, и говорить
        # «решил кто-то из модераторов» было бы враньём. Устарело не решением, а событием.
        return ("♻️ Это предложение УСТАРЕЛО: клиент написал снова, и бот собрал ответ заново.\n"
                "Отправить устаревшее нельзя — решайте по свежему предложению в карточке "
                "этого разговора. Клиенту сейчас не ушло ничего.")
    who = _s(row.get("decided_by"))
    if len(who) == 0:
        who = "кем-то из модераторов"
    when = _s(row.get("updated_ts"))
    out = "🔒 Карточка уже закрыта: " + _STATUS_RU.get(status, status) + " · решил " + who
    if len(when) > 0:
        out += " · " + when
    return out + ".\nВторое сообщение клиенту не уйдёт — решение принимает первый ответивший."


def decide(draft, action, username, text=None, test_mode=False, ipc=None):
    """Действие человека над карточкой: send | edit | reject. → decision-dict.

    НИЧЕГО не отправляет: максимум — переводит строку очереди в 'ready' (в TEST_MODE —
    в 'test_held', и тогда отправитель откажет вторым замком). Захват атомарный, поэтому
    победитель ровно один при любой гонке; проигравший получает decision='closed' и карточку
    «уже закрыто, кем». Предела ожидания нет: время в решение не входит ни одним аргументом."""
    by = "@" + _s(username)
    if len(_s(username)) == 0:
        by = "?"
    if action not in (ACT_SEND, ACT_EDIT, ACT_REJECT):
        return {"decision": "noop", "final_text": None, "by": by,
                "card": "🤔 Не понял действие — есть только: " +
                        " · ".join(label for _code, label in ACTIONS)}
    if not suggest.is_approver(username):
        return {"decision": "denied", "final_text": None, "by": by,
                "card": "⛔ Нет прав на отправку клиенту — карточка осталась открытой."}
    if action == ACT_REJECT:
        status, final = "rejected", None
    else:
        if action == ACT_EDIT:
            final = _s(text)
        else:
            candidate = _s(_get(draft, "final_text"))
            if len(candidate) > 0:
                final = candidate
            else:
                final = _s(_get(draft, "draft"))
        if len(final) == 0:
            return {"decision": "empty", "final_text": None, "by": by,
                    "card": "⚠️ Отправлять нечего: текст пустой. Карточка осталась открытой — "
                            "поправьте текст и отправьте, либо отклоните."}
        status = "test_held" if test_mode else "ready"
    queue = ipc
    if queue is None:
        import moderation_ipc
        queue = moderation_ipc
    won, row = queue.claim_decision(_get(draft, "id"), status, final_text=final, decided_by=by,
                                    reason=action)
    if not won:
        return {"decision": "closed", "final_text": None, "by": by, "row": row,
                "card": render_closed(row)}
    if status == "rejected":
        card = "❌ Отклонено (" + by + ") — клиенту НЕ отправлено ничего."
    elif status == "test_held":
        card = ("🧪 Принято (" + by + "), но TEST_MODE — клиенту НЕ отправлено.\n" + final)
    else:
        card = ("✅ Отправляем клиенту (" + by + "):\n" + final)
    return {"decision": status, "final_text": final, "by": by, "row": row, "card": card}
