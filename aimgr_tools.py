# -*- coding: utf-8 -*-
"""aimgr_tools.py — инструменты ЧТЕНИЯ для будущего агента-черновика клиентам (этап 1, 30.09.2026).

Задание Штаба 0058-75a.3009. Инструменты: `free_bikes` (наличие по двери цены), `bike_history`
(события и ТО байка из Bot Data), `rules` (книга правил ровно в том виде, в каком она идёт в промпт).
Задание 0063-75f.3009: `bike_history` берёт день события из живого `msg_date` (строка JS-даты), и
четвёртый — `bike_topic_msgs` (последние сообщения темы байка из архива форума «Обслуживание» на ПК,
возраст архива первой строкой).
Задание 0068-75k.3009 (пять пределов AIMGRACCEPT3009 §5): возраст архива — от последнего УДАЧНОГО
захода часов по их штампу, время последней строки — отдельно; «темы нет» при известном покрытии —
«в теме тихо с <начало>»; `bike_history` видит тему (последнее сообщение, слова ремонта за N суток,
«в ремонте» по событиям ИЛИ теме); слова ремонта старше окна, но не старше 7 суток — пометка без
смены вердикта; тема по номеру с расходящимся именем — пометка с обоими именами.

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
import json
import os
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
# Живой вид msg_date (замер AIMGRLIVE3009 §4): `String(Date)` Apps Script —
# «Tue Sep 29 2026 00:00:00 GMT+0700 (<пояс по-тайски>)». День в нём — календарный день таблицы,
# берётся как напечатан (ячейка-дата, время всегда 00:00:00; пересчёт пояса сдвинул бы её день).
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
_DAY_JS = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
                     re.IGNORECASE)
# ISO со временем и поясом (recorded_at «2026-09-29T04:25:08.567Z»; Date, прошедший через JSON) —
# это миг, а не день: день берётся ПХУКЕТСКИЙ, иначе запись 17:00–24:00 UTC ушла бы на день назад.
_ISO_TZ = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})")
PHUKET = datetime.timezone(datetime.timedelta(hours=7))


def plate_of(text):
    """Номер байка — зеркало `plateOf_` моста: последняя группа из 3+ цифр, не кубатура."""
    nums = [n for n in re.findall(r"\d{3,}", str(text or "").lower()) if n not in _CC]
    return nums[-1] if nums else ""


def _parse_day(s):
    """Строка даты любого из живых видов → день (date) по Пхукету или None (не разобрано)."""
    s = str(s or "")
    try:
        m = _ISO_TZ.search(s)
        if m:
            z = m.group(7)
            tz = datetime.timezone.utc if z.upper() == "Z" else datetime.timezone(
                (1 if z[0] == "+" else -1) * datetime.timedelta(hours=int(z[1:3]), minutes=int(z[-2:])))
            t = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)),
                                  int(m.group(5)), int(m.group(6) or 0), tzinfo=tz)
            return t.astimezone(PHUKET).date()
        m = _DAY_ISO.search(s)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _DAY_JS.search(s)
        if m:
            return datetime.date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        m = _DAY_RU.search(s)
        if m:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    return None


def _event_day_src(ev):
    """День события → (день, источник). Источник — `msg_date` (день сообщения в теме); запасной
    `recorded_at` (день ЗАПИСИ строки) — только когда msg_date нет или он не разобран."""
    for key in ("msg_date", "recorded_at"):
        d = _parse_day((ev or {}).get(key))
        if d:
            return d, key
    return None, None


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


REPAIR_NOTE_DAYS = 7          # слова ремонта старше окна, но не старше 7 суток — пометка, не вердикт


def _ago(day, today):
    return (today - day).days


def _topic_part(t, today, days):
    """Ответ `bike_topic_msgs` → сводка темы для `bike_history` (без вердикта): последнее сообщение,
    слова ремонта за `days` суток, свежие записи людей, слова ремонта старше окна (≤ 7 суток)."""
    part = {"verdict": (t or {}).get("verdict", UNKNOWN), "id": None, "name": None, "last": None,
            "repair": [], "repair_old": [], "fresh_human": 0, "why": list((t or {}).get("why") or ()),
            "quiet_since": (t or {}).get("quiet_since"), "name_gap": (t or {}).get("name_gap")}
    if part["verdict"] not in (FOUND, EMPTY):
        return part
    part["id"], part["name"] = t["topic"]["id"], t["topic"]["name"]
    part["last"] = t.get("last")
    for v in t.get("rows") or ():
        ts = _row_ts(v)
        if ts is None:
            continue
        age = _ago(ts.astimezone(PHUKET).date(), today)
        word = _REPAIR_RE.search(v.get("text") or "")
        hit = {"ts": ts.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"), "bot": v.get("bot"),
               "day": ts.astimezone(PHUKET).date(), "word": word.group(0).lower() if word else None}
        if age <= days:
            if word:
                part["repair"].append(hit)
            if not v.get("bot"):
                part["fresh_human"] += 1
        elif age <= REPAIR_NOTE_DAYS and word:
            part["repair_old"].append(hit)
    return part


def _topic_text(p, days):
    if p["verdict"] == QUIET:
        return "тема: тихо с %s UTC" % p["quiet_since"]
    if p["verdict"] not in (FOUND, EMPTY):
        return "тема: %s — %s" % (UNKNOWN, "; ".join(p["why"]) or "не прочитана")
    last = p["last"] or {}
    who = "бот" if last.get("bot") else "человек"
    say = "" if last.get("bot") else " · " + ((last.get("text") or "").replace("\n", " ") or "—")
    rep = ", ".join("%s UTC (%s)" % (h["ts"], "бот" if h["bot"] else "человек") for h in p["repair"])
    return "тема «%s»: последнее %s UTC · %s · фото: %s%s; слова ремонта за %d сут: %s" % (
        p["name"], _hm(last.get("ts")), who, "да" if last.get("photo") else "нет", say, days, rep or "нет")


def bike_history(bike, *, today=None, read=None, days=FRESH_DAYS, topic="archive", now=None):
    """Последние события и ТО байка → вердикт для агента.

    read(bike) → (события, строки_обслуживания): события — `items` ответа `read_events`, строки —
    `items` ответа `service_list` (все байки; фильтр по номеру здесь). Без `read`, при исключении
    или events=None → «не измерено»: молчание источника свежестью и «доступен» не является.
    days — сколько дней запись считается свежей (по умолчанию 3).
    День записи — из `msg_date` (живой вид — строка JS-даты), запасной `recorded_at` — только когда
    msg_date нет или он не разобран; сколько записей датировано запасным, видно в `day_src`, `why`
    и в хвосте `text`.
    topic(bike) → ответ `bike_topic_msgs` (по умолчанию — архив Обслуживания на ПК, окно 8 суток);
    None — тема не читается (прежнее поведение). Тема ВИДНА в ответе: последнее сообщение и слова
    ремонта за `days` суток; «в ремонте» — слово ремонта в событиях ИЛИ в теме за `days` суток;
    свежая запись человека в теме — свежая запись (тайцы — только когда свежего нет нигде). Слова
    ремонта старше окна, но не старше 7 суток — пометка «было упоминание ремонта <дата>, уточнить»,
    вердикт от неё не меняется. Тема по номеру с другим именем — пометка с обоими именами.
    → {"bike","verdict","text","marks","why","fresh","last_day","day_src","topic"}"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = today or now.astimezone(PHUKET).date()
    out = {"bike": bike, "verdict": UNMEASURED, "text": UNMEASURED, "marks": [], "why": [],
           "fresh": 0, "last_day": None, "day_src": {"msg_date": 0, "recorded_at": 0}, "topic": None}
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
    dated = []
    for ev in events:
        if str((ev or {}).get("status") or "").lower() == "void":
            continue
        d, src = _event_day_src(ev)
        if d:
            dated.append((d, ev))
            out["day_src"][src] += 1
    dated.sort(key=lambda p: p[0], reverse=True)
    fallback = out["day_src"]["recorded_at"]
    tail = (" (день по recorded_at у %d из %d записей: msg_date нет или не разобран)"
            % (fallback, len(dated)) if fallback else "")
    if fallback:
        out["why"].append(tail.strip(" ()"))
    if events and not dated:
        out["why"].append("даты записей не читаются")
        return out
    out["last_day"] = dated[0][0].isoformat() if dated else None
    fresh = [(d, ev) for d, ev in dated if _ago(d, today) <= days]
    out["fresh"] = len(fresh)
    out["marks"] = _service_marks(bike, services)
    tp = None
    if topic == "archive":
        topic = lambda b: bike_topic_msgs(b, days=REPAIR_NOTE_DAYS + 1, limit=0, now=now)  # noqa: E731
    if topic is not None:
        try:
            tp = _topic_part(topic(bike), today, days)
        except Exception as e:                   # noqa: BLE001 — тема не прочитана = НЕИЗВЕСТНО
            tp = _topic_part({"verdict": UNKNOWN, "why": ["тема не прочитана: %s" % type(e).__name__]},
                             today, days)
        out["topic"] = tp
    t_repair, t_fresh = (tp["repair"], tp["fresh_human"]) if tp else ([], 0)
    if not fresh and not t_fresh:
        out["verdict"] = ASK_THAI
        out["why"].append("нет записей за %d дн. ни в событиях (последняя: %s), ни от людей в теме"
                          % (days, out["last_day"] or "нет"))
    elif t_repair or any(_REPAIR_RE.search(str(ev.get("notes") or "")) for _d, ev in fresh):
        out["verdict"] = REPAIR
        out["why"].append("свежая запись о тормозах/подшипниках/мастерской (%s)"
                          % ("тема" if t_repair else "события"))
    else:
        out["verdict"] = AVAILABLE
        for _d, ev in fresh:                     # newest-first: мойка раньше грязи снимает пометку
            if str(ev.get("event_type") or "") == "wash":
                break
            if _DIRTY_RE.search(str(ev.get("notes") or "")):
                out["marks"].insert(0, "помыть")
                break
    old = [d for d, ev in dated if days < _ago(d, today) <= REPAIR_NOTE_DAYS
           and _REPAIR_RE.search(str(ev.get("notes") or ""))]
    old += [h["day"] for h in (tp or {}).get("repair_old") or ()]
    if old:
        out["marks"].append("было упоминание ремонта %s, уточнить" % max(old).strftime("%d.%m"))
    gap = (tp or {}).get("name_gap")
    if gap:
        out["marks"].append("тема по номеру %s: имя темы «%s», имя юнита «%s» — уточнить"
                            % (gap["plate"], gap["topic"], gap["bike"]))
    sep = ", " if out["verdict"] == AVAILABLE else "; "
    out["text"] = out["verdict"] + (sep + "пометка: " + ", ".join(out["marks"]) if out["marks"] else "") + tail
    if tp:
        out["text"] += "; " + _topic_text(tp, days)
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


# ------------------------------- 4. bike_topic_msgs ---------------------------------------------
# ИСТОЧНИК (AIMGRTOPIC3009): архив форума «Обслуживание» на ПК — `chatlog/servicing/ГГГГ/ММ/*.jsonl`,
# строка = сообщение темы: ts (ISO с поясом), topic_id, topic_name (= имя байка), text (очищен
# `chatlog_store.scrub`), media (фото: исход СОХРАНЁН и путь к файлу), bot, who_ref. Читается ДВЕРЬЮ
# `chatlog_find.search_days`, выдача — в её конверте ENVELOPE. ПЕРВАЯ строка ответа — возраст архива.
# Архив пополняют ЧАСЫ (`chatlog_servicing_tick`, SVCCLOCK3009) — их штамп
# `tmp/chatlog_servicing_tick/state.json` хранит ran_at / outcome / newest_utc / oldest_utc последнего
# завершённого захода. Возраст считается от удачного ЗАХОДА (тишина форума — не отставание архива),
# время последней строки — отдельно. Захват читает ВЕСЬ форум от новых к старым, поэтому окно
# удачного захода [oldest_utc, заход] скачано без разрыва по всем темам — на этом стоит «тихо».
FOUND = "есть сообщения"
EMPTY = "сообщений нет"
QUIET = "тихо"
UNKNOWN = "НЕИЗВЕСТНО"
TOPIC_GROUP = "servicing"
TOPIC_DAYS = 7
TOPIC_LIMIT = 20
STAMP_ENV = "CHATLOG_SERVICING_TICK_STATE"      # то же имя, что у самих часов
DEFAULT_STAMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", "chatlog_servicing_tick", "state.json")


def _norm_name(s):
    return " ".join(str(s or "").lower().split())


def _row_ts(r):
    try:
        t = datetime.datetime.fromisoformat(str(r.get("ts") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def _read_archive(group=TOPIC_GROUP):
    """Дверь архива → (строки, None) или (None, причина). Файлов дня нет / строк нет → причина."""
    import chatlog_find
    import chatlog_store
    days = chatlog_store.walk_days(group)
    if not days:
        return None, "архив не прочитан: файлов дня группы %s нет в %s" % (group, chatlog_store.root())
    rows = chatlog_find.search_days([], group=group)
    if not rows:
        return None, "архив не прочитан: файлов дня %d, строк не прочитано ни одной" % len(days)
    return rows, None


def _hm(t):
    """Миг (datetime или ISO-строка) → «ГГГГ-ММ-ДД ЧЧ:ММ» UTC."""
    if not isinstance(t, datetime.datetime):
        t = _row_ts({"ts": t})
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") if t else "?"


def _read_stamp(path=None):
    """Штамп часов архива → (dict, None) или (None, причина). Только чтение файла."""
    path = path or (os.getenv(STAMP_ENV) or "").strip() or DEFAULT_STAMP
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return None, "штампа часов нет (%s)" % path
    except (OSError, ValueError) as e:
        return None, "штамп часов не прочитан: %s" % type(e).__name__
    return (st, None) if isinstance(st, dict) else (None, "штамп часов не словарь")


def _clock_at(st):
    """Штамп → (миг последнего УДАЧНОГО захода, None) или (None, причина).

    Поля исхода штампа (outcome, newest/oldest_utc, done_at, duration_s) — всегда про заход
    № done_seq. Пока идёт новый заход (running / seq ≠ done_seq), `ran_at` — уже ЕГО заявка, и миг
    удачного берётся как done_at − duration_s (старт того захода)."""
    if st.get("outcome") != "ok":
        return None, "последний завершённый заход часов №%s — %s%s: миг удачного захода штамп не хранит" % (
            st.get("done_seq") or "?", st.get("outcome") or "исхода нет",
            " (%s)" % (st.get("why") or st.get("error")) if (st.get("why") or st.get("error")) else "")
    try:
        if st.get("seq") == st.get("done_seq") and not st.get("running"):
            at = float(st["ran_at"])
        else:
            at = float(st["done_at"]) - float(st["duration_s"])
        return datetime.datetime.fromtimestamp(at, datetime.timezone.utc), None
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None, "в штампе нет времени удачного захода"


def _age_line(last, now, at=None, at_why=None):
    """Первая строка ответа: возраст по удачному ЗАХОДУ часов, последняя строка — отдельно."""
    if at is not None:
        head = "возраст архива: последний удачный заход часов %s UTC — %.1f сут назад" % (
            _hm(at), (now - at).total_seconds() / 86400.0)
    else:
        head = "возраст архива: %s — %s" % (UNKNOWN, at_why or "штамп часов не прочитан")
    if last is None:
        return head + "; последняя строка не найдена"
    return head + ("; последняя строка %s UTC — %.1f сут назад (порога-отказа нет: не измерено, какая "
                   "давность ещё годна)" % (_hm(last), (now - last).total_seconds() / 86400.0))


def _coverage(st, at, last):
    """→ ((начало, конец), None) или (None, причина). Покрытие без разрыва — окно удачного захода:
    от старейшего взятого сообщения (`oldest_utc`) до мига захода. Архив обязан это окно содержать
    (последняя строка не старше `oldest_utc`), иначе читаем не тот архив, что описывает штамп."""
    if at is None:
        return None, "удачного захода часов не видно"
    old = _row_ts({"ts": (st or {}).get("oldest_utc")})
    if old is None:
        return None, "штамп не называет начало окна захода (oldest_utc)"
    if last is None or last < old:
        return None, "в архиве нет строк окна последнего захода (последняя строка %s, окно с %s)" % (
            _hm(last) if last else "—", _hm(old))
    return (old, at), None


def _name_gap(topic_name, bike):
    """Слова имени темы, которых нет в имени юнита (сверка без пробелов/дефисов: «X-Max» = «XMAX»)."""
    squashed = re.sub(r"[\W_]+", "", str(bike or "").lower())
    return [w for w in re.findall(r"[^\W_]+", str(topic_name or "").lower()) if w not in squashed]


def _msg_view(r):
    photos = [m for m in (r.get("media") or []) if (m or {}).get("kind") == "photo"]
    files = [m.get("file") for m in photos if m.get("file")]
    return {"ts": r.get("ts"), "text": r.get("text") or "", "photo": bool(photos), "files": files,
            "bot": bool(r.get("bot"))}


def _msg_line(v):
    t = _row_ts(v)
    head = t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC" if t else str(v["ts"])
    who = "бот" if v["bot"] else "человек"
    ph = ("фото: да, " + ", ".join(v["files"])) if v["files"] else ("фото: да, файла нет" if v["photo"] else "фото: нет")
    return "%s · %s · %s · %s" % (head, who, ph, v["text"].replace("\n", " ") or "—")


def bike_topic_msgs(bike, *, days=TOPIC_DAYS, limit=TOPIC_LIMIT, now=None, read=None, stamp=None):
    """Последние сообщения темы байка из архива форума «Обслуживание» за `days` суток.

    Тема ищется по имени байка: сначала точное имя темы, потом номер (`plate_of`, зеркало моста).
    read() → (строки, причина_отказа) — инъекция для тестов; по умолчанию дверь `chatlog_find`.
    stamp() → (штамп часов, причина_отказа) — инъекция; по умолчанию файл штампа (только чтение).
    Четыре исхода:
      FOUND — сообщения в окне есть: последние `limit` штук по времени (старые → новые);
      EMPTY — тема в архиве есть, в окне пусто: «сообщений нет за N суток»;
      QUIET — темы у байка в архиве нет, а покрытие без разрыва известно по штампу часов:
      «в теме тихо с <начало покрытия>»;
      UNKNOWN — архив не прочитан, или темы нет и покрытие НЕ известно: НЕИЗВЕСТНО с причиной
      (молчание архива пустотой не является: он хранит только скачанные окна).
    Первая строка `text` — ВСЕГДА возраст архива: от последнего удачного захода часов (штамп не
    прочитан — НЕИЗВЕСТНО с причиной), время последней строки — отдельно.
    Тема найдена по номеру, а в её имени есть слова, которых нет в имени юнита, — `name_gap` и
    строка с обоими именами. `last` — последнее сообщение темы вне зависимости от окна.
    → {"bike","verdict","text","age","clock_at","coverage","last_ts","topic","last","rows","counts",
       "name_gap","quiet_since","why"}"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = {"bike": bike, "verdict": UNKNOWN, "text": "", "age": None, "clock_at": None, "coverage": None,
           "last_ts": None, "topic": None, "last": None, "rows": [],
           "counts": {"window": 0, "shown": 0, "photo": 0, "human_text": 0}, "name_gap": None,
           "quiet_since": None, "why": []}
    try:
        rows, why = (read or _read_archive)()
    except Exception as e:                       # noqa: BLE001 — не прочитан = НЕИЗВЕСТНО
        rows, why = None, "архив не прочитан: %s" % type(e).__name__
    stamped = [(t, r) for t, r in ((_row_ts(r), r) for r in (rows or ())) if t]
    last = max((t for t, _r in stamped), default=None)
    out["last_ts"] = last.isoformat() if last else None
    try:
        st, st_why = (stamp or _read_stamp)()
    except Exception as e:                       # noqa: BLE001 — штамп не прочитан = НЕИЗВЕСТНО
        st, st_why = None, "штамп часов не прочитан: %s" % type(e).__name__
    at, at_why = _clock_at(st) if st is not None else (None, st_why)
    out["clock_at"] = at.isoformat() if at else None
    out["age"] = _age_line(last, now, at, at_why)
    cov, cov_why = _coverage(st, at, last)
    out["coverage"] = [cov[0].isoformat(), cov[1].isoformat()] if cov else None
    if rows is None or not stamped:
        out["why"].append(why or "архив не прочитан: у строк нет читаемого времени")
    else:
        topics = {}
        for t, r in stamped:
            topics.setdefault(r.get("topic_id"), set()).add(str(r.get("topic_name") or ""))
        want, plate = _norm_name(bike), plate_of(bike)
        hit = [tid for tid, names in topics.items() if want and want in {_norm_name(n) for n in names}]
        if not hit and plate:
            hit = [tid for tid, names in topics.items() if any(plate_of(n) == plate for n in names)]
        if not hit and plate and cov:
            bare = sum(1 for names in topics.values() if not any(plate_of(n) for n in names))
            out["verdict"] = QUIET
            out["quiet_since"] = _hm(cov[0])
            out["why"].append("темы с номером %s в архиве нет, а архив покрывает %s → %s UTC без разрыва "
                              "(последний удачный заход часов); позже захода — не скачано; тем без номера "
                              "в имени %d — среди них тему байка не опознать"
                              % (plate, _hm(cov[0]), _hm(cov[1]), bare))
        elif not hit:
            out["why"].append("темы у байка в архиве нет (%s; тем в архиве %d) — архив хранит только скачанные "
                              "окна, это не «пусто»; покрытие не известно: %s" % (
                                  "номер %s" % plate if plate else "в имени нет номера", len(topics),
                                  cov_why or "в имени нет номера"))
        elif len(hit) > 1:
            out["why"].append("байку подходят %d темы архива — какая его, не решить" % len(hit))
        else:
            tid = hit[0]
            mine = sorted(((t, r) for t, r in stamped if r.get("topic_id") == tid), key=lambda p: p[0])
            name = str(mine[-1][1].get("topic_name") or "")
            out["topic"] = {"id": tid, "name": name, "rows_all": len(mine)}
            out["last"] = _msg_view(mine[-1][1])
            by_name = want in {_norm_name(r.get("topic_name")) for _t, r in mine}
            gap = [] if by_name else _name_gap(name, bike)
            if gap:
                out["name_gap"] = {"plate": plate, "topic": name, "bike": bike, "words": gap}
            since = now - datetime.timedelta(days=days)
            win = [_msg_view(r) for t, r in mine if t >= since]
            shown = win[-limit:] if limit else win
            out["rows"] = shown
            out["counts"] = {"window": len(win), "shown": len(shown),
                             "photo": sum(1 for v in win if v["photo"]),
                             "human_text": sum(1 for v in win if not v["bot"] and v["text"].strip())}
            out["verdict"] = FOUND if win else EMPTY
    lines = [out["age"]]
    gap = out["name_gap"]
    gap_line = ("тема найдена по номеру %s: имя темы «%s», имя юнита «%s» (нет в имени юнита: %s) — та ли "
                "это тема, уточнить" % (gap["plate"], gap["topic"], gap["bike"], ", ".join(gap["words"]))
                if gap else None)
    if out["verdict"] == UNKNOWN:
        lines.append("%s: %s" % (UNKNOWN, "; ".join(out["why"])))
    elif out["verdict"] == QUIET:
        lines.append("в теме тихо с %s UTC: %s" % (out["quiet_since"], "; ".join(out["why"])))
    elif out["verdict"] == EMPTY:
        lines.append("тема «%s»: сообщений нет за %d суток (последнее %s UTC)"
                     % (out["topic"]["name"], days, _hm(out["last"]["ts"])))
        lines.extend([gap_line] if gap_line else [])
    else:
        import chatlog_find
        c = out["counts"]
        lines.append(chatlog_find.ENVELOPE)
        lines.append("тема «%s»: за %d суток сообщений %d (показаны последние %d), с фото %d, людей с текстом %d"
                     % (out["topic"]["name"], days, c["window"], c["shown"], c["photo"], c["human_text"]))
        lines.extend([gap_line] if gap_line else [])
        lines.extend(_msg_line(v) for v in out["rows"])
    out["text"] = "\n".join(lines)
    return out
