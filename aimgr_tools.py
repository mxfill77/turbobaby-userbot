# -*- coding: utf-8 -*-
"""aimgr_tools.py — инструменты ЧТЕНИЯ для будущего агента-черновика клиентам (этап 1, 30.09.2026).

Задание Штаба 0058-75a.3009. Инструменты: `free_bikes` (наличие по двери цены), `bike_history`
(события и ТО байка из Bot Data), `rules` (книга правил ровно в том виде, в каком она идёт в промпт).
Задание 0063-75f.3009: `bike_history` берёт день события из живого `msg_date` (строка JS-даты), и
четвёртый — `bike_topic_msgs` (последние сообщения темы байка из архива форума «Обслуживание» на ПК,
возраст архива первой строкой).

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


def bike_history(bike, *, today=None, read=None, days=FRESH_DAYS):
    """Последние события и ТО байка → вердикт для агента.

    read(bike) → (события, строки_обслуживания): события — `items` ответа `read_events`, строки —
    `items` ответа `service_list` (все байки; фильтр по номеру здесь). Без `read`, при исключении
    или events=None → «не измерено»: молчание источника свежестью и «доступен» не является.
    days — сколько дней запись считается свежей (по умолчанию 3).
    День записи — из `msg_date` (живой вид — строка JS-даты), запасной `recorded_at` — только когда
    msg_date нет или он не разобран; сколько записей датировано запасным, видно в `day_src`, `why`
    и в хвосте `text`.
    → {"bike","verdict","text","marks","why","fresh","last_day","day_src"}"""
    today = today or datetime.datetime.now(PHUKET).date()
    out = {"bike": bike, "verdict": UNMEASURED, "text": UNMEASURED, "marks": [], "why": [],
           "fresh": 0, "last_day": None, "day_src": {"msg_date": 0, "recorded_at": 0}}
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
    out["text"] = out["verdict"] + (sep + "пометка: " + ", ".join(out["marks"]) if out["marks"] else "") + tail
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
# `chatlog_find.search_days`, выдача — в её конверте ENVELOPE. Архив сам не обновляется (захват
# `chatlog_servicing_fetch` — руками), поэтому ПЕРВАЯ строка ответа — возраст архива.
FOUND = "есть сообщения"
EMPTY = "сообщений нет"
UNKNOWN = "НЕИЗВЕСТНО"
TOPIC_GROUP = "servicing"
TOPIC_DAYS = 7
TOPIC_LIMIT = 20


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


def _age_line(last, now):
    if last is None:
        return "возраст архива: неизвестен — последняя строка не найдена"
    return ("возраст архива: последняя строка %s UTC — %.1f сут назад (порога-отказа нет: не измерено, "
            "какая давность ещё годна)" % (last.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                                          (now - last).total_seconds() / 86400.0))


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


def bike_topic_msgs(bike, *, days=TOPIC_DAYS, limit=TOPIC_LIMIT, now=None, read=None):
    """Последние сообщения темы байка из архива форума «Обслуживание» за `days` суток.

    Тема ищется по имени байка: сначала точное имя темы, потом номер (`plate_of`, зеркало моста).
    read() → (строки, причина_отказа) — инъекция для тестов; по умолчанию дверь `chatlog_find`.
    Три исхода:
      FOUND — сообщения в окне есть: последние `limit` штук по времени (старые → новые);
      EMPTY — тема в архиве есть, в окне пусто: «сообщений нет за N суток»;
      UNKNOWN — архив не прочитан или темы у байка в архиве нет: НЕИЗВЕСТНО с причиной (молчание
      архива пустотой не является: он хранит только скачанные окна).
    Первая строка `text` — ВСЕГДА возраст архива (время последней строки, сколько суток назад).
    → {"bike","verdict","text","age","last_ts","topic","rows","counts","why"}"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = {"bike": bike, "verdict": UNKNOWN, "text": "", "age": None, "last_ts": None, "topic": None,
           "rows": [], "counts": {"window": 0, "shown": 0, "photo": 0, "human_text": 0}, "why": []}
    try:
        rows, why = (read or _read_archive)()
    except Exception as e:                       # noqa: BLE001 — не прочитан = НЕИЗВЕСТНО
        rows, why = None, "архив не прочитан: %s" % type(e).__name__
    stamped = [(t, r) for t, r in ((_row_ts(r), r) for r in (rows or ())) if t]
    last = max((t for t, _r in stamped), default=None)
    out["last_ts"] = last.isoformat() if last else None
    out["age"] = _age_line(last, now)
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
        if not hit:
            out["why"].append("темы у байка в архиве нет (%s; тем в архиве %d) — архив хранит только скачанные "
                              "окна, это не «пусто»" % ("номер %s" % plate if plate else "в имени нет номера",
                                                         len(topics)))
        elif len(hit) > 1:
            out["why"].append("байку подходят %d темы архива — какая его, не решить" % len(hit))
        else:
            tid = hit[0]
            mine = sorted(((t, r) for t, r in stamped if r.get("topic_id") == tid), key=lambda p: p[0])
            name = str(mine[-1][1].get("topic_name") or "")
            out["topic"] = {"id": tid, "name": name, "rows_all": len(mine)}
            since = now - datetime.timedelta(days=days)
            win = [_msg_view(r) for t, r in mine if t >= since]
            shown = win[-limit:] if limit else win
            out["rows"] = shown
            out["counts"] = {"window": len(win), "shown": len(shown),
                             "photo": sum(1 for v in win if v["photo"]),
                             "human_text": sum(1 for v in win if not v["bot"] and v["text"].strip())}
            out["verdict"] = FOUND if win else EMPTY
    lines = [out["age"]]
    if out["verdict"] == UNKNOWN:
        lines.append("%s: %s" % (UNKNOWN, "; ".join(out["why"])))
    elif out["verdict"] == EMPTY:
        lines.append("тема «%s»: сообщений нет за %d суток" % (out["topic"]["name"], days))
    else:
        import chatlog_find
        c = out["counts"]
        lines.append(chatlog_find.ENVELOPE)
        lines.append("тема «%s»: за %d суток сообщений %d (показаны последние %d), с фото %d, людей с текстом %d"
                     % (out["topic"]["name"], days, c["window"], c["shown"], c["photo"], c["human_text"]))
        lines.extend(_msg_line(v) for v in out["rows"])
    out["text"] = "\n".join(lines)
    return out
