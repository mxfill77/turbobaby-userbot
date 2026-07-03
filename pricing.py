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
import json
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
