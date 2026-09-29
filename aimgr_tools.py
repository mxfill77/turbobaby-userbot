# -*- coding: utf-8 -*-
"""aimgr_tools.py — три инструмента ЧТЕНИЯ для будущего агента-черновика клиентам (этап 1, 30.09.2026).

Задание Штаба 0058-75a.3009. Инструменты: `free_bikes` (наличие по двери цены), `bike_history`
(события и ТО байка из Bot Data), `rules` (книга правил ровно в том виде, в каком она идёт в промпт).

ГРАНИЦА С КЛИЕНТСКИМ КОНТУРОМ — В ОДНУ СТОРОНУ. Этот модуль МОЖЕТ читать `suggest`/`pricing`
(ленивым импортом внутри функций: одна мерка с боевым путём, своих копий правил нет), но ни
`suggest`, ни `userbot_listen`, ни модерация его НЕ импортируют — клиентский бот о нём не знает.
Замок — `test_aimgr_tools.ClosureTest`: замыкание импортов клиентского контура не содержит имени
этого модуля.

ЧЕГО ЗДЕСЬ НЕТ НИ ОДНОЙ ВЕТКОЙ: записи куда-либо, отправки наружу, своего чтения таблиц. Сеть
ходит только через ИНЪЕКЦИЮ (`get` двери цены, `read` истории байка); без инъекции `bike_history`
честно отвечает «не измерено», а не идёт в мост сам. `quote_price` как отдельный инструмент и
`ask_thai` — вне этапа (премиса П5).

ПРАВИЛО ВЛАДЕЛЬЦА 27.09 — порядок источников наличия: сначала таблица (`free_bikes`), потом тема
байка в «Обслуживании» (`bike_history`), тайцы — только если в теме нет свежих записей. Слова
владельца 29.09 о тормозах, подшипниках, мастерской: уточнить у тайцев дату готовности и, если
она соответствует дате клиента, сообщить клиенту, будет или нет возможность (`ready_fits`).
"""

import datetime
import re

# ------------------------------- 1. free_bikes ---------------------------------------------------
FREE, BUSY, UNCHECKED = "свободен", "занят", "не проверено"


def _unit_state(q):
    """Котировка юнита → FREE | BUSY | UNCHECKED. Мерка ОДНА с сеткой клиентского пути
    (`suggest._sheet_unit_free`): поле `available` читается там, здесь только имя исхода.
    Котировки нет (дверь не ответила / без цены) → UNCHECKED, а не «занят»."""
    if q is None:
        return UNCHECKED
    import suggest
    got = suggest._sheet_unit_free(q)
    if got is None:
        return UNCHECKED
    return FREE if got else BUSY


def _model_state(states):
    """Вердикт по модели из исходов юнитов.

    НАМЕРЕННОЕ ОТЛИЧИЕ от `suggest._sheet_availability`: там «занят + не проверено» → busy, потому
    что сетке нужно одно — брать ли модель в подбор, и оба исхода её одинаково исключают. Агент же
    СКАЖЕТ клиенту «занят», и юнит, которого мы не прочитали, мог быть свободен. Поэтому BUSY — только
    когда ВСЕ юниты прочитаны и заняты; любая непрочитанность без свободного → UNCHECKED."""
    if any(s == FREE for s in states):
        return FREE
    if states and all(s == BUSY for s in states):
        return BUSY
    return UNCHECKED


def free_bikes(model, dates, *, get=None, fleet=None, quote=None):
    """Свободна ли МОДЕЛЬ на даты клиента по ответу двери цены по юнитам.

    dates = (дата_начала, дата_конца) ISO. Пересечение с листом «клиенты» считает МОСТ
    (QuotePrice.js → поле `available`); здесь его заново не пишем, только читаем ответ.
    get — инъекция транспорта `pricing` (тесты); fleet — готовый список парка; quote(name) —
    готовая котировка юнита (по умолчанию `pricing.quote` той же двери).
    → {"model", "dates", "verdict": FREE|BUSY|UNCHECKED, "units": [{"bike","state"}], "free", "why"}"""
    import pricing
    ds, de = (list(dates or ()) + [None, None])[:2]
    out = {"model": model, "dates": (ds, de), "verdict": UNCHECKED, "units": [], "free": [], "why": ""}
    if not (model and ds and de):
        out["why"] = "нет модели или дат"
        return out
    if fleet is None:
        fleet, ok = pricing.fleet_status(_get=get)
        if not ok:
            out["why"] = "парк не прочитан"
            return out
    cands = pricing._candidates(model, fleet)
    if not cands:
        out["why"] = "модели нет в парке"
        return out
    ask = quote or (lambda name: pricing.quote(name, ds, de, _get=get))
    for b in cands:
        name = b.get("name")
        try:
            q = ask(name)
        except Exception:                        # noqa: BLE001 — сбой юнита = не проверено
            q = None
        out["units"].append({"bike": name, "state": _unit_state(q)})
    states = [u["state"] for u in out["units"]]
    out["verdict"] = _model_state(states)
    out["free"] = [u["bike"] for u in out["units"] if u["state"] == FREE]
    out["why"] = "юнитов %d: свободно %d, занято %d, не проверено %d" % (
        len(states), states.count(FREE), states.count(BUSY), states.count(UNCHECKED))
    return out


# ------------------------------- 2. bike_history ------------------------------------------------
# ИСТОЧНИК (П4): Bot Data — отдельная таблица «TurboBaby Bot Data» (мост, BotData.js: id в Script
# Properties `BOT_DATA_SHEET_ID`), листы «события» (читает `read_events`, newest-first, по номеру
# байка) и «обслуживание» (читает `service_list`, одна строка = один вид ТО одного байка; статус
# `overdue`, когда current_km ≥ next_km). Пишет в оба Splinter.
AVAILABLE = "доступен"
ASK_THAI = "спросить тайцев"
REPAIR = "в ремонте: спросить тайцев о дате готовности, сравнить с датой клиента"
UNMEASURED = "не измерено"
FRESH_DAYS = 3

# Слова владельца 29.09: тормоза, подшипники, мастерская (RU/EN/TH).
_REPAIR_RE = re.compile(r"тормоз|колодк|подшип|мастерск|brake|bearing|workshop|garage"
                        r"|เบรก|เบรค|ลูกปืน|อู่", re.IGNORECASE)
_DIRTY_RE = re.compile(r"грязн|помыть|dirty|needs? (?:a )?wash|สกปรก", re.IGNORECASE)
_CC = {"125", "150", "155", "300", "350", "400", "500", "650", "700", "750", "900"}
_DAY_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DAY_RU = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def plate_of(text):
    """Номер байка — зеркало `plateOf_` моста: последняя группа из 3+ цифр, не кубатура."""
    nums = [n for n in re.findall(r"\d{3,}", str(text or "").lower()) if n not in _CC]
    return nums[-1] if nums else ""


def _event_day(ev):
    for key in ("msg_date", "recorded_at"):
        s = str((ev or {}).get(key) or "")
        m = _DAY_ISO.search(s)
        try:
            if m:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            m = _DAY_RU.search(s)
            if m:
                return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
    return None


def _num(v):
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _service_marks(bike, services):
    """Строки «обслуживание» этого байка → пометки «срочно масло/ТО» с перепробегом в км."""
    marks, want = [], plate_of(bike)
    for row in services or ():
        if not want or plate_of(row.get("bike")) != want:
            continue
        cur, nxt = _num(row.get("current_km")), _num(row.get("next_km"))
        over = (cur - nxt) if (cur is not None and nxt) else None
        if row.get("status") != "overdue" and not (over is not None and over >= 0):
            continue
        kind = str(row.get("service_type") or "").strip()
        tail = " (просрочено на %d км)" % over if over is not None and over >= 0 else ""
        marks.append(("срочно масло" if kind == "oil" else "срочно ТО: %s" % (kind or "?")) + tail)
    return marks


def bike_history(bike, *, today=None, read=None, days=FRESH_DAYS):
    """Последние события и ТО байка → вердикт для агента.

    read(bike) → (события, строки_обслуживания): события — `items` ответа `read_events`, строки —
    `items` ответа `service_list` (все байки; фильтр по номеру здесь). Без `read`, при исключении
    или events=None → «не измерено»: молчание источника свежестью и «доступен» не является.
    days — сколько дней запись считается свежей (по умолчанию 3).
    → {"bike","verdict","text","marks","why","fresh","last_day"}"""
    today = today or datetime.date.today()
    out = {"bike": bike, "verdict": UNMEASURED, "text": UNMEASURED, "marks": [], "why": [],
           "fresh": 0, "last_day": None}
    if read is None:
        out["why"].append("источник не подключён")
        return out
    try:
        events, services = read(bike)
    except Exception as e:                       # noqa: BLE001 — не прочитан = не измерено
        out["why"].append("источник не прочитан: %s" % type(e).__name__)
        return out
    if events is None:
        out["why"].append("события не прочитаны")
        return out
    if services is None:
        out["why"].append("обслуживание не прочитано — пометок ТО нет, это не «в норме»")
    dated = [(d, ev) for d, ev in ((_event_day(ev), ev) for ev in events
                                   if str((ev or {}).get("status") or "").lower() != "void") if d]
    dated.sort(key=lambda p: p[0], reverse=True)
    if events and not dated:
        out["why"].append("даты записей не читаются")
        return out
    out["last_day"] = dated[0][0].isoformat() if dated else None
    fresh = [(d, ev) for d, ev in dated if (today - d).days <= days]
    out["fresh"] = len(fresh)
    out["marks"] = _service_marks(bike, services)
    if not fresh:
        out["verdict"] = ASK_THAI
        out["why"].append("в теме нет записей за %d дн. (последняя: %s)" % (days, out["last_day"] or "нет"))
    elif any(_REPAIR_RE.search(str(ev.get("notes") or "")) for _d, ev in fresh):
        out["verdict"] = REPAIR
        out["why"].append("свежая запись о тормозах/подшипниках/мастерской")
    else:
        out["verdict"] = AVAILABLE
        for _d, ev in fresh:                     # newest-first: мойка раньше грязи снимает пометку
            if str(ev.get("event_type") or "") == "wash":
                break
            if _DIRTY_RE.search(str(ev.get("notes") or "")):
                out["marks"].insert(0, "помыть")
                break
    sep = ", " if out["verdict"] == AVAILABLE else "; "
    out["text"] = out["verdict"] + (sep + "пометка: " + ", ".join(out["marks"]) if out["marks"] else "")
    return out


def ready_fits(ready_day, client_day):
    """Слова владельца 29.09: дата готовности от тайцев против даты клиента.
    → True (успевает: сообщить, что будет) / False (не успевает) / None (дату не назвали)."""
    if not (ready_day and client_day):
        return None
    return ready_day <= client_day


def bridge_reader(call, limit=8):
    """Адаптер к мосту для `bike_history`: call(action, **params) → dict ответа. Этапом 1 НЕ
    вызывается ни одной веткой — мост в этом задании не зовём; тесты подают выдуманный call."""
    def read(bike):
        ev = call("read_events", bike=bike, limit=limit) or {}
        sv = call("service_list") or {}
        events = ev.get("items") if ev.get("ok") else None
        services = sv.get("items") if sv.get("ok") else None
        return events, services
    return read


# ------------------------------- 3. rules -------------------------------------------------------
def _same(rule):
    return " ".join(str(rule or "").split())


def rules():
    """Книга правил РОВНО в том виде, в каком `suggest.load_playbook` отдаёт её в промпт, с номерами.

    `text` — байт в байт результат `load_playbook()`; `numbered` — каждый буллет с номером и
    разделом. Расхождение книги-снимка и базы уроков — числом: сколько выученных правил есть только
    в книге и только в базе (сверка текста без даты-префикса). База не прочитана → в промпт идёт
    книга, и расхождение «не измерено» (None), а не ноль."""
    import suggest
    text = suggest.load_playbook()
    numbered, section = [], ""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            section = s.lstrip("#").strip()
        elif s.startswith("-"):
            numbered.append((len(numbered) + 1, section, s))
    book = [_same(r) for r in suggest._playbook_learned_rules(suggest.load_playbook_file())]
    bullets, read_ok = suggest.active_lesson_bullets()
    out = {"text": text, "numbered": numbered, "count": len(numbered),
           "source": "база" if read_ok else "книга-снимок", "book_learned": len(book),
           "base_active": None, "only_in_book": None, "only_in_base": None}
    if read_ok:
        base = [_same(r) for r in suggest._playbook_learned_rules(
            suggest._LEARNED_HEADER + "\n" + "\n".join(bullets))]
        out["base_active"] = len(base)
        out["only_in_book"] = sum(1 for r in book if r not in set(base))
        out["only_in_base"] = sum(1 for r in base if r not in set(book))
    return out
