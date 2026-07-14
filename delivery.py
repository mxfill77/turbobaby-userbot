# -*- coding: utf-8 -*-
"""
delivery.py — клиент зон доставки из Bridge (экшен delivery_zones_get). КАРКАС.

ИНВАРИАНТ: список зон доставки берётся ТОЛЬКО из Bridge (единый источник правды).
Bridge недоступен / ошибка / таймаут / не-ok → get_delivery_zones() возвращает None,
и резолвер зон уходит в честный фолбэк «[уточнить]» (не выдумывает зону/цену доставки).

Транспорт — тот же, что у pricing.fleet/quote: read-only GET к BRIDGE_URL с action+token,
БЕЗ побочных эффектов. Зоны кэшируются в памяти на DELIVERY_ZONES_TTL секунд (~12 мин),
чтобы не долбить Bridge на каждый диалог. Не роняет вызывающий код: любые исключения
проглатываются, короткий лог в delivery.log.
"""

import os
import json
import time
import logging
import urllib.request
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))

log = logging.getLogger("delivery")
log.setLevel(logging.INFO)
log.propagate = False
try:
    _h = logging.FileHandler(os.path.join(HERE, "delivery.log"), encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    log.addHandler(_h)
except Exception:
    pass

BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()
HTTP_TIMEOUT = int(os.getenv("DELIVERY_TIMEOUT", "20") or "20")
# TTL кэша зон: ~12 мин по умолчанию (зоны меняются редко; резолвер зовётся часто).
ZONES_TTL = int(os.getenv("DELIVERY_ZONES_TTL_SEC", "720") or "720")
ZONES_ACTION = os.getenv("DELIVERY_ZONES_ACTION", "delivery_zones_get").strip() or "delivery_zones_get"

_ZONES_CACHE = {"ts": 0.0, "data": None}


def _default_get(params):
    """GET к Bridge (read-only), вернуть распарсенный JSON. Может кинуть — ловится выше."""
    full = BRIDGE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _extract_zones(data):
    """Достать список зон из ответа Bridge ({ok, data:{zones:[...]}} либо {ok, zones:[...]}).
    Вернуть list зон или None (None = не-ok/нет списка → фолбэк выше)."""
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    src = data.get("data") if isinstance(data.get("data"), dict) else data
    zones = src.get("zones") if isinstance(src, dict) else None
    if not isinstance(zones, list):
        return None
    return zones


def get_delivery_zones(_get=None, _now=None):
    """Зоны доставки из Bridge (read-only GET action=delivery_zones_get), кэш на ZONES_TTL сек.
    Возвращает list[зон] при успехе, либо None при недоступности/ошибке (резолвер тогда даст
    «[уточнить]»). _get/_now — инъекция для тестов. НИКОГДА не пишет и не роняет вызывающий код.

    Кэшируется ТОЛЬКО успешный ответ: при ошибке отдаём свежий кэш (если ещё в TTL), иначе None —
    провал Bridge не «залипает» как None в кэше и не затирает валидные зоны."""
    now = _now() if _now else time.time()
    c = _ZONES_CACHE
    if c["data"] is not None and (now - c["ts"]) < ZONES_TTL:
        return c["data"]
    if not (BRIDGE_URL and BRIDGE_TOKEN) and _get is None:
        log.info("нет BRIDGE_URL/TOKEN — фолбэк (None)")
        return c["data"] if (c["data"] is not None and (now - c["ts"]) < ZONES_TTL) else None
    getter = _get or _default_get
    try:
        data = getter({"action": ZONES_ACTION, "token": BRIDGE_TOKEN})
    except Exception as e:
        log.info(f"get_delivery_zones упал ({type(e).__name__}) — фолбэк (None/кэш)")
        return c["data"] if (c["data"] is not None and (now - c["ts"]) < ZONES_TTL) else None
    zones = _extract_zones(data)
    if zones is None:
        log.info("get_delivery_zones не-ok/без списка zones — фолбэк (None/кэш)")
        return c["data"] if (c["data"] is not None and (now - c["ts"]) < ZONES_TTL) else None
    c["ts"] = now
    c["data"] = zones
    log.info(f"get_delivery_zones ok: {len(zones)} зон")
    return zones
