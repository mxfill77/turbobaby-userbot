# -*- coding: utf-8 -*-
"""СУТОЧНЫЙ СПИСОК НАХОДОК РЕВИЗОРА С ОТБРАКОВКОЙ ПОСТРОЧНО (22.09.2026, задание Штаба 0015j-71d.2209).

ПОВОД — ЗАМЕР 71a (`docs/artifacts/2026-09-22-KARTOCHKISCHET-2209.md`): owner-карточек ревизора за
23.07–22.09 было 16, показов 33, операция не стояла ни за одной. Ответов 8: пять «принято» не купили
ничего, три «нет» записали вердикт «ложная», и эти находки больше не возвращались. Решение владельца
22.09: такие находки собирать и «решать их скопом» — списком раз в сутки, с кнопкой отбраковки у
КАЖДОЙ строки («Да можно»).

ЧТО МОДУЛЬ ДЕЛАЕТ. Держит ожидающие строки (находка = ключ повторяемости + отпечаток существа,
ровно те, что пишет в реестр нынешняя кнопка «нет» карточки), решает, пора ли слать список, рисует
его в окне сообщения и исполняет нажатие «❌ ложная». Ввода-вывода нет: файлы, часы, реестр и
отправку подаёт вызывающий (`pc_orchestrator`), поэтому всё голденится без моста и без Telegram.

ЗАМОК 1 — СКЛЕЙКА ТОЛЬКО ПРИ НУЛЕВОМ ПРИЗНАКЕ ОПЕРАЦИИ. Признак ставится ТОЛЬКО в месте события
(ветки демона, которые переводят находку во владельческую ради операции: деплой/рестарт прода и
правка клиентского контура) полем `op_src` с названным источником. Слово в тексте находки признак не
поднимает, а то же поле, пришедшее от думателя в сыром JSON, срезается до маршрутизации
(`strip_event_fields`). Находка с поднятым признаком в список не попадает: `split` отдаёт её
отдельной карточкой, как раньше.

ЗАМОК 2 — ОТБРАКОВКА ДОКАЗЫВАЕТСЯ ОБРАТНЫМ ЧТЕНИЕМ. Нажатие считается успехом только если после
записи реестр ЧИТАЕТСЯ с вердиктом «ложная» и тем же существом под тем же ключом. Иначе ответ —
отказ, а строка остаётся в списке.

ЗАМОК 3 — СТРОКА БЕЗ НАЖАТИЯ НЕ ПРОПАДАЕТ. Показ ничего не снимает: строка уходит из ожидания только
отбраковкой (кнопкой или вердиктом, пришедшим другим путём). Не влезшее в окно сообщения
называется числом и путём к полному тексту.
"""

import datetime
import hashlib

VERDICT_FALSE = "ложная"

# ИСТОЧНИКИ ПРИЗНАКА ОПЕРАЦИИ. Ставит их только демон, в ветке, где решение принято.
OP_DEPLOY = "деплой-рестарт"          # `_revizor_demote_deploy_task`: находка просит деплой/рестарт прода
OP_CLIENT = "клиентский-контур"       # `_revizor_demote_client_task`: находка просит правку живого бота
OP_SOURCES = frozenset((OP_DEPLOY, OP_CLIENT))
EVENT_FIELDS = ("op_src", "op")       # поля, которые думатель поставить не вправе

CB_PREFIX = "pachka:no:"              # callback_data кнопки; хвост — метка строки (12 hex)
MARK_LEN = 12

DEFAULT_HOUR = 9                      # час показа по местному времени
DEFAULT_TZ_HOURS = 7                  # Пхукет, UTC+7
DEFAULT_WINDOW_LINES = 20             # строк с кнопкой в одном сообщении
DEFAULT_WINDOW_CHARS = 3500           # потолок текста (Telegram режет на 4096)
DEFAULT_REMIND_DAYS = 7               # список без новых строк, но с висящими — не реже раза в N суток
EVIDENCE_MAX = 160                    # улика в строке списка (полная — в файле полного текста)


def _s(v):
    return "" if v is None else str(v).strip()


def op_of(f):
    """Признак операции находки: поднят ТОЛЬКО названным источником из места события."""
    return isinstance(f, dict) and _s(f.get("op_src")) in OP_SOURCES


def strip_event_fields(raw):
    """Сырой ответ думателя → находка без полей, которые ставит только демон."""
    d = dict(raw) if isinstance(raw, dict) else {}
    for k in EVENT_FIELDS:
        d.pop(k, None)
    return d


def split(findings):
    """owner-находки → (в суточный список, отдельной карточкой как раньше).

    ЗАМОК 1: находка с поднятым признаком операции в список НЕ идёт. Без улики строки в списке не
    бывает (владельцу нечего было бы отбраковывать) — такая находка тоже остаётся карточке."""
    batch, separate = [], []
    for f in (findings if isinstance(findings, (list, tuple)) else ()):
        if op_of(f):
            separate.append(f)
            continue
        if not _s((f or {}).get("evidence")):
            separate.append(f)
            continue
        batch.append(f)
    return batch, separate


def mark_of(key, gist):
    """Метка строки: 12 hex от пары (ключ, существо) — та же пара, что ляжет в реестр."""
    return hashlib.sha1((_s(key) + "\n" + _s(gist)).encode("utf-8")).hexdigest()[:MARK_LEN]


def empty_store():
    return {"version": 1, "lines": [], "rejected": [], "last_list": {}}


def norm_store(d):
    """Состояние с диска → проверенная форма. Мусор внутри отбрасывается, а не роняет демона."""
    st = empty_store()
    if not isinstance(d, dict):
        return st
    for name in ("lines", "rejected"):
        v = d.get(name)
        if isinstance(v, list):
            st[name] = [dict(x) for x in v if isinstance(x, dict) and _s(x.get("mark"))]
    ll = d.get("last_list")
    if isinstance(ll, dict):
        st["last_list"] = dict(ll)
    return st


def add(store, findings, key_fn, gist_fn, now_iso):
    """Дописать находки в ожидание. → сколько строк добавлено. Дубль по метке строку не растит,
    отбракованная ранее метка не возвращается (вердикт её и так глушит, это второй рубеж)."""
    have = {x["mark"] for x in store["lines"]}
    gone = {x["mark"] for x in store["rejected"]}
    n = 0
    for f in findings or ():
        key, gist = _s(key_fn(f)), _s(gist_fn(f))
        if not key or not gist:
            continue
        m = mark_of(key, gist)
        if m in have or m in gone:
            continue
        have.add(m)
        store["lines"].append({"mark": m, "key": key, "gist": gist,
                               "class": _s(f.get("class")) or "?", "client_id": _s(f.get("client_id")),
                               "evidence": _s(f.get("evidence")), "added": _s(now_iso), "shown": 0})
        n += 1
    return n


def drop_decided(store, decided_fn):
    """Снять строки, по которым вердикт «ложная» уже стоит (пришёл другим путём). → сколько снято."""
    keep, gone = [], 0
    for x in store["lines"]:
        if decided_fn(x["key"], x["gist"]):
            gone += 1
            continue
        keep.append(x)
    store["lines"] = keep
    return gone


def local_day(now_ts, tz_hours):
    t = datetime.datetime.fromtimestamp(float(now_ts), datetime.timezone.utc) + \
        datetime.timedelta(hours=float(tz_hours))
    return t.strftime("%Y-%m-%d"), t.hour


def due(store, now_ts, hour=DEFAULT_HOUR, tz_hours=DEFAULT_TZ_HOURS, remind_days=DEFAULT_REMIND_DAYS):
    """Пора ли слать список → (да/нет, почему). Раз в местные сутки, не раньше часа `hour`; есть
    новые строки — шлём; только висящие — не реже раза в `remind_days` суток."""
    lines = store["lines"]
    if not lines:
        return False, "ожидающих строк нет"
    day, h = local_day(now_ts, tz_hours)
    if h < int(hour):
        return False, f"ещё не {int(hour):02d}:00 по местному"
    last = store["last_list"]
    if _s(last.get("day")) == day:
        return False, "список за эти сутки уже был"
    if any(int(x.get("shown") or 0) == 0 for x in lines):
        return True, "есть новые строки"
    try:
        age = (float(now_ts) - float(last.get("at"))) / 86400.0
    except (TypeError, ValueError):
        return True, "висящие строки, прошлый список не датирован"
    if age >= float(remind_days):
        return True, f"новых нет, висящие ждут {age:.1f} сут"
    return False, f"новых нет, висящие напомню через {float(remind_days) - age:.1f} сут"


def _line(i, x):
    ev = " ".join(_s(x.get("evidence")).split())
    if len(ev) > EVIDENCE_MAX:
        ev = ev[:EVIDENCE_MAX - 1] + "…"
    return f"{i}. [класс {x.get('class') or '?'}] окно {x.get('client_id') or '?'}: {ev}"


def render(store, full_path, window_lines=DEFAULT_WINDOW_LINES, window_chars=DEFAULT_WINDOW_CHARS):
    """Список в окне сообщения → dict(text, marks, shown, hidden, new, carried, full).

    Порядок: сначала новые, потом висящие (от старых к свежим). Не влезшее в окно НЕ режется
    молча: хвост сообщения называет число не показанных строк и путь к полному тексту."""
    lines = store["lines"]
    new = [x for x in lines if int(x.get("shown") or 0) == 0]
    old = [x for x in lines if int(x.get("shown") or 0) > 0]
    order = new + old
    head = (f"🔍 Ревизор: суточный список находок — {len(order)} строк (новых {len(new)}, "
            f"перенесено {len(old)}). Кнопка ❌N = строка N «ложная»: вердикт в реестр, больше не "
            f"придёт. Без нажатия строка остаётся и придёт в следующем списке.")
    full = "\n".join([head] + [_line(i, x) + f"  [метка {x['mark']}]"
                               for i, x in enumerate(order, 1)])
    body, marks = [], []
    tail_room = 260
    used = len(head) + tail_room
    for i, x in enumerate(order, 1):
        ln = _line(i, x)
        if len(marks) >= int(window_lines) or used + len(ln) + 1 > int(window_chars):
            break
        body.append(ln)
        marks.append(x["mark"])
        used += len(ln) + 1
    hidden = len(order) - len(marks)
    parts = [head] + body
    if hidden:
        parts.append(f"⚠️ НЕ ПОКАЗАНО строк: {hidden} из {len(order)} (окно сообщения: "
                     f"{int(window_lines)} строк / {int(window_chars)} знаков). Они не потеряны и "
                     f"придут в следующих списках; полный текст: {full_path}")
    return {"text": "\n".join(parts), "marks": marks, "shown": len(marks), "hidden": hidden,
            "new": len(new), "carried": len(old), "full": full}


def markup(marks, per_row=5):
    """Кнопки под списком: ❌N на каждую показанную строку, по `per_row` в ряд."""
    btns = [{"text": f"❌{i}", "callback_data": CB_PREFIX + m} for i, m in enumerate(marks, 1)]
    return {"inline_keyboard": [btns[i:i + per_row] for i in range(0, len(btns), per_row)]}


def mark_shown(store, marks, now_ts, now_iso, day):
    """После отправки: строкам — счётчик показов, списку — день и время."""
    ms = set(marks)
    for x in store["lines"]:
        if x["mark"] in ms:
            x["shown"] = int(x.get("shown") or 0) + 1
    store["last_list"] = {"day": day, "at": float(now_ts), "iso": _s(now_iso), "shown": len(ms)}


def reject(store, mark, write_verdict, read_verdict, now_iso):
    """Нажатие «❌ ложная» по метке → (ok, текст ответа). Ничего не пишет сам: вердикт пишет
    `write_verdict(key, gist) -> (ok, msg)`, проверяет обратным чтением `read_verdict(key) ->
    (вердикт, существо)`. ЗАМОК 2: успех — только когда прочитанное совпало с записанным."""
    m = _s(mark).lower()
    hit = next((x for x in store["lines"] if x["mark"] == m), None)
    if hit is None:
        done = next((x for x in store["rejected"] if x["mark"] == m), None)
        if done is not None:
            return True, (f"строка {m} уже отбракована ({done.get('at') or 'время не записано'}) — "
                          f"вердикт «ложная» в реестре стоит, повторно не пишу")
        return False, (f"⛔ ОТКАЗ: строки {m} в ожидающем списке нет — вердикт НЕ записан "
                       f"(легло 0 из 1). Список мог устареть: пришлю свежий в следующий показ")
    ok, msg = write_verdict(hit["key"], hit["gist"])
    got_v, got_g = read_verdict(hit["key"])
    landed = bool(ok) and _s(got_v) == VERDICT_FALSE and _s(got_g) == hit["gist"]
    if not landed:
        why = _s(msg) or "запись не подтверждена"
        if ok:
            why = (f"запись сказала «ок», но обратное чтение реестра дало «{_s(got_v) or 'нет записи'}»"
                   f"{'' if _s(got_g) == hit['gist'] else ' без нашего существа'}")
        return False, (f"⛔ ОТКАЗ: нажатие по строке {m} принято, но вердикт «ложная» в реестр НЕ "
                       f"лёг (легло 0 из 1): {why}. Строка остаётся в списке")
    store["lines"] = [x for x in store["lines"] if x["mark"] != m]
    store["rejected"] = (store["rejected"] + [{"mark": m, "key": hit["key"], "at": _s(now_iso)}])[-500:]
    return True, (f"✅ строка {m}: вердикт «ложная» записан в реестр по ключу {hit['key']} и "
                  f"прочитан обратно (легло 1 из 1). В следующем списке её не будет")
