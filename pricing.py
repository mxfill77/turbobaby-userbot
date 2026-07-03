# -*- coding: utf-8 -*-
"""
pricing.py — получение ТОЧНОЙ цены из «Календаря бронирования» через Bridge. КАРКАС.

ИНВАРИАНТ: клиенту никогда не уходит выдуманная/FAQ-цена как финальная. Точная цена
берётся ТОЛЬКО из Календаря (Bridge-экшен). Любая неясность → quote() возвращает None,
и вызывающий код уходит в честный фолбэк «уточню цену и вернусь».

Bridge-экшен quote_price ПОКА НЕ построен (отдельный трек). Поэтому quote() зовёт его
ТОЛЬКО если объявлен env PRICING_ACTION (непустой); иначе / ошибка / таймаут / не-ok →
None. Read-only GET, БЕЗ побочных эффектов (никаких create_booking/записи). Не роняет
вызывающий код: любые исключения проглатываются, короткий лог в pricing.log.
"""

import os
import re
import json
import time
import logging
import urllib.request
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

log = logging.getLogger("pricing")
log.setLevel(logging.INFO)
log.propagate = False
try:
    _h = logging.FileHandler(os.path.join(HERE, "pricing.log"), encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    log.addHandler(_h)
except Exception:
    pass

PRICING_ACTION = os.getenv("PRICING_ACTION", "").strip()   # пусто → фаза (б) всегда фолбэк
BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()
HTTP_TIMEOUT = int(os.getenv("PRICING_TIMEOUT", "20") or "20")
FLEET_TTL = int(os.getenv("FLEET_TTL_SEC", "300") or "300")   # кэш парка, чтобы не долбить Bridge
_FLEET_CACHE = {"ts": 0.0, "data": None}


def _default_get(params):
    """GET к Bridge (read-only), вернуть распарсенный JSON. Может кинуть — ловится выше."""
    full = BRIDGE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _normalize(data):
    """Привести ответ Bridge к {day_price,total,deposit,available,season} или None."""
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    src = data.get("data") if isinstance(data.get("data"), dict) else data
    out = {
        "day_price": src.get("day_price"),
        "total": src.get("total"),
        "deposit": src.get("deposit"),
        "available": src.get("available"),
        "season": src.get("season"),
        "bike": src.get("bike"),
        "model": src.get("model"),
        "days": src.get("days"),
        "text": src.get("text"),
    }
    # Нужна хотя бы одна осмысленная цифра цены, иначе это не котировка.
    if out["day_price"] is None and out["total"] is None:
        return None
    return out


def quote(bike, date_start, date_end, _get=None):
    """Точная цена из Календаря по модели+датам. Возвращает dict-котировку или None.
    None = штатный фолбэк (экшен не объявлен / ошибка / не-ok / нет цифр). _get — инъекция
    для тестов. НИКОГДА не пишет и не роняет вызывающий код."""
    if not PRICING_ACTION:
        # Bridge-экшен ещё не построен — штатный путь фолбэка.
        return None
    if not (BRIDGE_URL and BRIDGE_TOKEN):
        log.info("нет BRIDGE_URL/TOKEN — фолбэк (None)")
        return None
    params = {
        "action": PRICING_ACTION, "token": BRIDGE_TOKEN,
        "bike": bike or "", "date_start": date_start or "", "date_end": date_end or "",
    }
    getter = _get or _default_get
    try:
        data = getter(params)
    except Exception as e:
        log.info(f"quote упал ({type(e).__name__}) — фолбэк (None)")
        return None
    q = _normalize(data)
    if q is None:
        log.info(f"quote не-ok/без цифр для bike={bike} {date_start}..{date_end} — фолбэк (None)")
    else:
        log.info(f"quote ok bike={bike} {date_start}..{date_end}: day={q.get('day_price')} total={q.get('total')}")
    return q


# ------------------------------- парк (fleet) --------------------------------

def fleet(_get=None, _now=None):
    """Список байков парка (read-only GET action=fleet), кэш на FLEET_TTL секунд.
    Возвращает list[dict] (или [] при недоступности). _get/_now — инъекция для тестов."""
    now = _now() if _now else time.time()
    c = _FLEET_CACHE
    if c["data"] is not None and (now - c["ts"]) < FLEET_TTL:
        return c["data"]
    if not (BRIDGE_URL and BRIDGE_TOKEN) and _get is None:
        return c["data"] or []
    getter = _get or _default_get
    try:
        data = getter({"action": "fleet", "token": BRIDGE_TOKEN})
    except Exception as e:
        log.info(f"fleet упал ({type(e).__name__}) — отдаю кэш/пусто")
        return c["data"] or []
    bikes = None
    if isinstance(data, dict) and data.get("ok"):
        src = data.get("data") if isinstance(data.get("data"), dict) else data
        if isinstance(src, dict) and isinstance(src.get("bikes"), list):
            bikes = src["bikes"]
    if bikes is None:
        return c["data"] or []
    c["ts"] = now
    c["data"] = bikes
    return bikes


def sanity_days_ok(quote_days, hint_days, monthly=False):
    """СТРАХОВОЧНЫЙ ГАРД (независим от парсера): согласуется ли days из quote с длительностью
    из слов клиента. Кривые даты (напр. 360 дней при 5) НЕ должны доехать до клиента.
    False → цену НЕ вставляем, честный фолбэк. Логируем WARNING в pricing.log."""
    try:
        qd = int(quote_days)
    except (TypeError, ValueError):
        log.warning(f"SANITY: quote без валидного days={quote_days!r} — режу цену, фолбэк")
        return False
    if qd < 1:
        log.warning(f"SANITY: days={qd} < 1 — режу цену, фолбэк")
        return False
    if qd > 45 and not monthly:
        log.warning(f"SANITY: days={qd} > 45 без месячного запроса — режу цену, фолбэк")
        return False
    if hint_days is not None:
        try:
            hd = int(hint_days)
        except (TypeError, ValueError):
            hd = None
        if hd is not None and abs(qd - hd) > 1:
            log.warning(f"SANITY: quote days={qd} vs слова клиента {hd} (расхождение >1) — режу цену, фолбэк")
            return False
    return True


def _norm_alnum(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _candidates(model, bikes):
    """Байки, у кого нормализованная модель входит в нормализованное имя."""
    m = _norm_alnum(model)
    if not m:
        return []
    return [b for b in bikes if m in _norm_alnum(b.get("name"))]


def quote_for_model(model, date_start, date_end, _get=None, _fleet=None):
    """Резолв МОДЕЛЬ→конкретный байк и котировка. READ-ONLY (create_booking НЕ зовём).
    Возвращает {'status': ok|none_available|no_candidates|error, 'quote': dict|None}:
      ok            — нашли available байк, quote с ценой (использовать дословно);
      none_available— котировки получены, но все подходящие байки заняты на даты;
      no_candidates — нет модели/дат или в парке нет такой модели;
      error         — парк/котировки недоступны (ошибка/таймаут/не-ok)."""
    if not (model and date_start and date_end):
        return {"status": "no_candidates", "quote": None}
    bikes = _fleet if _fleet is not None else fleet(_get=_get)
    if not bikes:
        return {"status": "error", "quote": None}
    cands = _candidates(model, bikes)
    if not cands:
        return {"status": "no_candidates", "quote": None}
    saw_quote = False
    for b in cands:
        q = quote(b.get("name"), date_start, date_end, _get=_get)
        if q is None:
            continue
        saw_quote = True
        if q.get("available"):
            return {"status": "ok", "quote": q}
    if saw_quote:
        return {"status": "none_available", "quote": None}
    return {"status": "error", "quote": None}
