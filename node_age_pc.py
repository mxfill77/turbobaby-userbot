"""Возраст перед содержимым — одно правило показа узлов мозга на полосе ПК (задание 63-r, 17.09.2026).

═══ ЗАЧЕМ ════════════════════════════════════════════════════════════════════

Замер 63-p (`docs/artifacts/2026-09-17-VOZRAST-uzlov-chitateli-1709.md`): из 8 мест, показывающих
человеку текст узла, время снятия с возрастом называло ОДНО, отказывали при устаревшем НОЛЬ. Живой
случай — кнопка «статус» показала узел `pulse` восьмидневной давности как текущий. Показ, который не
называет возраста, подаёт старое за сейчас, и читающему нечем это отличить.

═══ ПРАВИЛО ══════════════════════════════════════════════════════════════════

R1 — показ узла начинается строкой «<узел> · снято <время UTC> · <возраст> назад».
R2 — возраст больше порога → вместо содержимого строка «УСТАРЕЛ … — содержимое не показано».
     Штамп не найден или не разобран → «возраст неизвестен — содержимое не показано».
R3 — порог = p99 промежутков записи узла по замеру, вверх до минуты. ПОРОГ БЕЗ ЗАМЕРА НЕ СТАВИТСЯ:
     у узла без замеренного порога — только строка возраста, отказа нет, и это сказано в самой
     строке («порог не замерен»).

═══ ТРИ ИСХОДА, И ТРЕТИЙ — НЕ СВЕЖЕСТЬ ═══════════════════════════════════════

`свежо` · `устарело` · `неизвестно`. Мост времени изменения документа НЕ отдаёт (`ReadDocs.js` →
`{ok, name, id, text}`), поэтому возраст берётся только из штампа, который писатель сам положил в
текст. Нет штампа, штамп не разбирается, штамп из будущего — это `неизвестно`, и у узла с порогом оно
отказывает так же, как `устарело`: отсутствие метки доказывает только отсутствие метки.

Узел без порога (`limit=None`) и узел вне таблицы свежесть НЕ судят вовсе: их исход
:data:`NOT_JUDGED`, содержимое показывается, а строка возраста говорит, почему суда нет. Это не
четвёртый исход свежести, а названный отказ её судить.

Модуль ЧИСТЫЙ: моста, диска и часов не зовёт сам (часы — только при `now=None`). Судит тот, кто
показывает: `vitrina_pc_run.read_shtab` (A5) и `brain_writer --probe` (A6).
"""
import calendar
import math
import re
import time

MEASURE_63P = "docs/artifacts/2026-09-17-VOZRAST-uzlov-chitateli-1709.md"
MEASURE_63R = "docs/artifacts/2026-09-17-USTAREL-porog-modul-1709.md"

FRESH = "свежо"
STALE = "устарело"
UNKNOWN = "неизвестно"
NOT_JUDGED = "не судится"

FUTURE_SKEW_SEC = 300.0     # штамп позже «сейчас» больше чем на 5 мин — не возраст, а сбой часов/штампа

_TYPES = "DONE|NOTE|ASK|PLAN|BLOCKED|WAITING|SKIPPED"
_DT = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)"

# ═══ ТАБЛИЦА: узел → как достать штамп → порог ════════════════════════════════
#
# `stamp` — регулярка с ОДНОЙ группой времени; `pick` — какой штамп из найденных брать: `first`
# (штамп шапки) или `max` (журнал: свежая строка, порядок строк не доверяем); `day` — штамп несёт
# только сутки; `limit` — порог в секундах или None; `why` — откуда число.
NODES = {
    "pulse": {
        "stamp": re.compile(r"^\s*" + _DT + r"\b", re.M), "pick": "first", "day": False,
        "limit": 3074 * 60,
        "why": "p99 промежутков записи 184398 с → 3074 мин, замер 63-p (261 запись за 30 сут)",
    },
    "pulse_pc": {
        "stamp": re.compile(r"числа менялись последний раз:\s*" + _DT + r"\s*UTC"), "pick": "first",
        "day": False,
        "limit": 58 * 60,
        "why": "p99 промежутков записи 3461 с → 58 мин, замер 63-p (1333 записи за 14.9 сут)",
    },
    # СЛЕПКИ ОЧЕРЕДИ — БЕЗ ОТКАЗА. Их писатели пишут ПО СМЕНЕ: штамп значит «когда изменилось», а не
    # «когда сверено». Порог 63-p (47/62 мин) ставится ТОЛЬКО вместе с R4 — писатель переписывает
    # слепок и при неизменной подписи. R4 в этом заходе не поставлен; без него p99 (76 мин / 7 ч)
    # давал бы ложный «устарел» 24.6 % / 22.7 % времени при живых писателях.
    "queue_state_pc": {
        "stamp": re.compile(r"^(?:снято|последний верный снимок):\s*" + _DT + r"\s*UTC", re.M),
        "pick": "first", "day": False, "limit": None,
        "why": "порог 47 мин только при R4 (сверка раз в 2170 с), R4 не поставлен",
    },
    "queue_state": {
        "stamp": re.compile(r"^(?:снято|последний верный снимок):\s*" + _DT + r"\s*UTC", re.M),
        "pick": "first", "day": False, "limit": None,
        "why": "порог 62 мин только при R4 на сервере, R4 не поставлен",
    },
    # ЖУРНАЛЫ И СЛОВА ШТАБА — пороги сняты заходом 63-r (п.4 задания) тем же методом, по штампам
    # записей; числа и корпус — в MEASURE_63R. Не снялся порог → limit None, только возраст.
    "cowork_log": {
        "stamp": re.compile(r"^(?:%s)\s+%s\s*UTC" % (_TYPES, _DT), re.M), "pick": "max",
        "day": False,
        "limit": 162 * 60,
        "why": "p99 промежутков записи 9720 с → 162 мин, замер 63-r (5727 записей за 51.0 сут)",
    },
    "cc_log": {
        "stamp": re.compile(r"^(?:%s)\s+%s\s*UTC" % (_TYPES, _DT), re.M), "pick": "max",
        "day": False,
        "limit": 2233 * 60,
        "why": "p99 промежутков записи 133980 с → 2233 мин, замер 63-r (659 записей за 62.5 сут)",
    },
    # ПОРОГ НЕ СНЯЛСЯ: слова Штаба за 14.9 сут бэкапов витрины сменились 4 раза (3 промежутка, и
    # часть смен — правки самой витрины, а не записи Штаба). p99 по трём числам = максимум, законом
    # это не является → только возраст, без отказа.
    "shtab_vitrina": {
        "stamp": re.compile(r"^\s*дата\s*=\s*(\d{4}-\d{2}-\d{2})\s*$", re.M), "pick": "first",
        "day": True, "limit": None,
        "why": "порог не снялся: 3 промежутка записи за 14.9 сут, замер 63-r",
    },
}

# Места, которые показывают НЕ состояние, под правило не попадают (63-p §5 R3): справочник
# `knowledge_base` (A4, сервер), архивный вынос `build_dec_port_spec.py` (A7), номер редакции
# `review_pack_build` (A8). Их здесь нет сознательно, а не по забывчивости.


def _now(now):
    return time.time() if now is None else float(now)


def _parse(raw, day):
    """Строка штампа → секунды эпохи UTC | None. Сутки → КОНЕЦ суток (возраст не завышаем)."""
    try:
        if day:
            base = calendar.timegm(time.strptime(raw, "%Y-%m-%d"))
            # Дата без часов покрывает сутки в любом поясе; самый поздний момент, который она
            # может значить, — начало следующих суток UTC. От него возраст — НИЖНЯЯ граница.
            return float(base + 86400)
        fmt = "%Y-%m-%d %H:%M:%S" if raw.count(":") == 2 else "%Y-%m-%d %H:%M"
        return float(calendar.timegm(time.strptime(raw, fmt)))
    except (ValueError, TypeError, OverflowError):
        return None


def stamps_all(node, text):
    """Все штампы узла в тексте → список секунд эпохи (неразобранные пропущены). Для замера."""
    spec = NODES.get(node)
    if spec is None or not isinstance(text, str):
        return []
    out = []
    for hit in spec["stamp"].finditer(text):
        ts = _parse(hit.group(1), spec["day"])
        if ts is not None:
            out.append(ts)
    return out


def stamp_of(node, text, now=None):
    """Штамп узла → (секунды | None, как штамп написан | "", причина незнания | "")."""
    spec = NODES.get(node)
    if spec is None:
        return None, "", "узел %s вне таблицы возраста" % node
    if not isinstance(text, str) or not text.strip():
        return None, "", "текст узла пуст"
    hits = [h.group(1) for h in spec["stamp"].finditer(text)]
    if not hits:
        return None, "", "штамп времени в тексте не найден"
    parsed = [(_parse(raw, spec["day"]), raw) for raw in hits]
    good = [(ts, raw) for ts, raw in parsed if ts is not None]
    if spec["pick"] == "first":
        ts, raw = parsed[0]
        if ts is None:
            return None, raw, "штамп «%s» не разобран" % raw
    else:
        if not good:
            return None, hits[0], "штамп «%s» не разобран" % hits[0]
        ts, raw = max(good)
    if ts - _now(now) > FUTURE_SKEW_SEC:
        return None, raw, "штамп «%s» позже текущего времени" % raw
    return ts, raw, ""


def age_words(sec):
    """Секунды → «N с» / «N мин» / «N ч M мин» / «N сут M ч»."""
    sec = max(0, int(sec))
    if sec < 90:
        return "%d с" % sec
    if sec < 90 * 60:
        return "%d мин" % int(round(sec / 60.0))
    if sec < 48 * 3600:
        return "%d ч %d мин" % (sec // 3600, (sec % 3600) // 60)
    return "%d сут %d ч" % (sec // 86400, (sec % 86400) // 3600)


def _limit_words(limit):
    return "%d мин" % int(round(limit / 60.0))


def age_line(node, text, now=None):
    """СТРОКА ВОЗРАСТА — первая строка любого показа узла. → str, никогда не пустая."""
    return decide(node, text, now)["head"]


def decide(node, text, now=None):
    """Показывать ли содержимое узла. → dict.

    ``outcome`` — :data:`FRESH` / :data:`STALE` / :data:`UNKNOWN` у узла с порогом, :data:`NOT_JUDGED`
    у узла без порога и вне таблицы; ``show`` — показывать ли текст; ``head`` — строка возраста (при
    ``show=False`` она же — строка отказа, ставится ВМЕСТО содержимого); ``age`` — секунды | None;
    ``limit`` — порог | None.
    """
    now = _now(now)
    spec = NODES.get(node)
    ts, raw, why = stamp_of(node, text, now)
    limit = spec["limit"] if spec else None
    age = None if ts is None else max(0.0, now - ts)
    shown = ("%s (известны только сутки)" % raw) if (spec and spec["day"] and raw) else (
        "%s UTC" % raw if raw else "")
    got = {"node": node, "ts": ts, "age": age, "limit": limit, "why": why}
    if spec is None:
        got.update(outcome=NOT_JUDGED, show=True,
                   head="%s · вне таблицы возраста (не узел состояния) — свежесть не судится" % node)
        return got
    if limit is None:
        if ts is None:
            head = "%s · возраст неизвестен: %s · порог не замерен — свежесть не судится" % (node, why)
        else:
            head = "%s · снято %s · %s назад%s · порог не замерен — свежесть не судится" % (
                node, shown, age_words(age), " (не меньше)" if spec["day"] else "")
        got.update(outcome=NOT_JUDGED, show=True, head=head)
        return got
    if ts is None:
        got.update(outcome=UNKNOWN, show=False,
                   head="⚠️ возраст %s неизвестен (%s) — содержимое не показано" % (node, why))
        return got
    if age > limit:
        got.update(outcome=STALE, show=False,
                   head="⚠️ %s УСТАРЕЛ: снят %s, %s назад (порог %s, замер %s) — содержимое не показано"
                        % (node, shown, age_words(age), _limit_words(limit), MEASURE_63P))
        return got
    got.update(outcome=FRESH, show=True,
               head="%s · снято %s · %s назад (порог %s)" % (node, shown, age_words(age),
                                                             _limit_words(limit)))
    return got


def render(node, text, now=None):
    """Показ целиком: строка возраста и содержимое — либо строка отказа ВМЕСТО содержимого."""
    got = decide(node, text, now)
    return (got["head"] + "\n" + text) if got["show"] else got["head"]


# ═══ ЗАМЕР ПОРОГА (метод 63-p: промежутки между последовательными записями) ══════

def gap_stats(stamps):
    """Штампы записей → промежутки и квантили (ближайший ранг). → dict | None (меньше 2 штампов)."""
    uniq = sorted(set(float(s) for s in stamps))
    if len(uniq) < 2:
        return None
    gaps = sorted(b - a for a, b in zip(uniq, uniq[1:]))

    def q(p):
        return gaps[max(0, int(math.ceil(p * len(gaps))) - 1)]

    return {"records": len(uniq), "gaps": len(gaps), "days": (uniq[-1] - uniq[0]) / 86400.0,
            "p50": q(0.50), "p95": q(0.95), "p99": q(0.99), "max": gaps[-1],
            "first": uniq[0], "last": uniq[-1]}


def limit_from(stats, min_gaps=100):
    """p99 вверх до минуты, в секундах. Промежутков меньше ``min_gaps`` → None: p99 по такой выборке —
    это просто максимум, и ставить его порогом значит выдать один случай за закон."""
    if not stats or stats["gaps"] < min_gaps:
        return None
    return int(math.ceil(stats["p99"] / 60.0)) * 60
