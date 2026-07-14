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
import re
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


# ===========================================================================
# resolve_maps_link — из ссылки Google Maps достать (lat, lon) точки доставки
# ===========================================================================
#
# Клиент кидает точку в переписку короткой ссылкой (maps.app.goo.gl/…) или полным
# URL Google Maps. Чтобы посчитать зону/цену доставки по координатам, ссылку надо
# ПРЕВРАТИТЬ в (lat, lon). Порядок:
#   1) координаты уже в самой ссылке (?q=lat,lng, @lat,lng, !3d…!4d…, %2C-кодирование,
#      обрезанный мессенджером хвост) — берём БЕЗ сети;
#   2) короткая ссылка (maps.app.goo.gl / goo.gl) — разворачиваем по HTTP-редиректу
#      (с таймаутом, БЕЗ исполнения JS) и парсим конечный URL;
#   3) place-ссылка без координат / битая ссылка / не-строка → None (честный фолбэк,
#      как у зон: не выдумываем координаты).
# Никогда не роняет вызывающий код: любые сетевые исключения проглатываются → None.

MAPS_TIMEOUT = int(os.getenv("MAPS_RESOLVE_TIMEOUT", "8") or "8")
# Хосты коротких ссылок, которые надо разворачивать редиректом (без координат в URL).
_MAPS_SHORT_HOSTS = ("maps.app.goo.gl", "app.goo.gl", "goo.gl", "g.co")
# Сколько редиректов пройти при разворачивании (защита от циклов).
_MAPS_MAX_HOPS = 5

# Пары координат в разных местах URL. Требуем десятичную точку (реальные гео-точки её
# всегда имеют) — так не ловим случайные «q=1,2» из мусора. Диапазоны валидируем отдельно.
_LATLON = r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)"
# ?q=/query=/ll=/destination=/daddr=/center= — явно заданная точка.
_RE_MAPS_QUERY = re.compile(r"[?&](?:q|query|ll|sll|destination|daddr|center)=" + _LATLON, re.I)
# !3dLAT!4dLNG — точный пин места в data-хвосте развёрнутого URL.
_RE_MAPS_3D4D = re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)")
# @LAT,LNG,zoom — центр вьюпорта (менее точен, потому в самом конце приоритета).
_RE_MAPS_AT = re.compile(r"@" + _LATLON)


def _valid_latlon(lat, lon):
    """(lat, lon) как float в допустимых диапазонах, иначе None."""
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return (lat, lon)
    return None


def _parse_coords_from_url(url):
    """Достать (lat, lon) из строки URL Google Maps или None. Сначала URL-декодируем
    (%2C→',', %2F→'/' и т.п.), затем пробуем форматы по убыванию точности точки:
    ?q=/query=… → !3d…!4d… → @…,…. Устойчив к обрезанному мессенджером хвосту."""
    if not url:
        return None
    try:
        dec = urllib.parse.unquote(url)
    except Exception:
        dec = url
    for rx in (_RE_MAPS_QUERY, _RE_MAPS_3D4D, _RE_MAPS_AT):
        m = rx.search(dec)
        if m:
            r = _valid_latlon(m.group(1), m.group(2))
            if r:
                return r
    return None


def _is_short_maps_link(url):
    """URL — короткая ссылка Google Maps (maps.app.goo.gl / goo.gl / g.co)?"""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    host = host.split("@")[-1].split(":")[0]  # срезать креды/порт, если есть
    return host in _MAPS_SHORT_HOSTS or host.endswith(".app.goo.gl")


def _default_expand(url):
    """Развернуть короткую ссылку по цепочке HTTP-редиректов (БЕЗ исполнения JS),
    вернуть конечный URL. Тело не грузим — читаем только заголовок Location.
    Может кинуть (таймаут/сеть) — ловится в resolve_maps_link."""
    cur = url
    for _ in range(_MAPS_MAX_HOPS):
        req = urllib.request.Request(
            cur, method="HEAD",
            headers={"User-Agent": "Mozilla/5.0 (compatible; TurboBaby/1.0)"})
        # Не даём urllib молча ходить по редиректам — сами читаем Location.
        opener = urllib.request.build_opener(_NoRedirectHandler)
        resp = opener.open(req, timeout=MAPS_TIMEOUT)
        try:
            code = resp.getcode()
            if code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if not loc:
                    return cur
                cur = urllib.parse.urljoin(cur, loc)
                continue
            return resp.geturl()
        finally:
            resp.close()
    return cur


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Не следовать редиректам автоматически — вернуть 3xx-ответ вызывающему коду."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_maps_link(url, _expand=None):
    """Ссылка Google Maps → (lat, lon) точки доставки, либо None.
    Короткие maps.app.goo.gl/goo.gl разворачиваются HTTP-редиректом (с таймаутом, без JS);
    поддержаны форматы ?q=lat,lng, @lat,lng, !3d…!4d…, %2C-кодирование и обрезанные
    мессенджером хвосты. place-ссылка без координат / битая ссылка / не-строка → None.
    _expand — инъекция сети для тестов. НИКОГДА не роняет вызывающий код."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    # 1) координаты уже в ссылке — берём без сети
    coords = _parse_coords_from_url(url)
    if coords:
        return coords
    # 2) короткая ссылка — развернуть редиректом и распарсить конечный URL
    if _is_short_maps_link(url):
        expander = _expand or _default_expand
        try:
            final = expander(url)
        except Exception as e:
            log.info(f"resolve_maps_link: разворот не удался ({type(e).__name__}) — None")
            return None
        if isinstance(final, str):
            return _parse_coords_from_url(final)
    # 3) place-ссылка без координат / битая ссылка → None
    return None
