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
import math
import time
import logging
import urllib.request
import urllib.parse
import urllib.error

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
#   1) НАДЁЖНЫЕ координаты уже в самой ссылке (?q=lat,lng, @lat,lng, !3d…!4d…, %2C-кодирование,
#      обрезанный мессенджером хвост) — берём БЕЗ сети;
#   2) короткая ссылка (maps.app.goo.gl / goo.gl) — разворачиваем по HTTP-редиректу
#      (с таймаутом, БЕЗ исполнения JS) и парсим конечный URL; если в конечном URL координат
#      нет (place-ссылка), догружаем тело GET'ом и ищем в нём ТОЛЬКО точный пин `!3d…!4d…`
#      (метаданные place). Вьюпорт-центроид (`center=` статической карты / og:image) —
#      НЕнадёжен и НЕ используется (решение ASK #51): это центр картинки, а не точка клиента,
#      цену по нему не называем никогда;
#   3) надёжных координат нет ни в URL, ни точного пина в теле / битая ссылка / не-строка →
#      None (честный фолбэк [уточнить]: не выдумываем координаты и не берём вьюпорт-центроид).
# Никогда не роняет вызывающий код: любые сетевые исключения проглатываются → None.

MAPS_TIMEOUT = int(os.getenv("MAPS_RESOLVE_TIMEOUT", "8") or "8")
# Хосты коротких ссылок, которые надо разворачивать редиректом (без координат в URL).
_MAPS_SHORT_HOSTS = ("maps.app.goo.gl", "app.goo.gl", "goo.gl", "g.co")
# Сколько редиректов пройти при разворачивании (защита от циклов).
_MAPS_MAX_HOPS = 5
_MAPS_UA = "Mozilla/5.0 (compatible; TurboBaby/1.0)"
# Потолок чтения тела финальной страницы (place-ссылки без координат в URL): точный пин
# `!3d!4d` (метаданные place) лежит в начале документа — мегабайты не тянем. Вьюпорт-центроид
# `center=` статической карты в теле есть, но его НЕ используем (ASK #51).
_MAPS_BODY_CAP = int(os.getenv("MAPS_BODY_CAP_BYTES", "2000000") or "2000000")

# Пары координат в разных местах URL. Требуем десятичную точку (реальные гео-точки её
# всегда имеют) — так не ловим случайные «q=1,2» из мусора. Диапазоны валидируем отдельно.
_LATLON = r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)"
# ?q=/query=/ll=/destination=/daddr= — явно заданная КЛИЕНТОМ точка (надёжно).
# `center=` НАМЕРЕННО исключён (решение ASK #51): это центр статической карты/og:image —
# вьюпорт-центроид страницы, а НЕ пин клиента; по нему цену не называем.
_RE_MAPS_QUERY = re.compile(r"[?&](?:q|query|ll|sll|destination|daddr)=" + _LATLON, re.I)
# !3dLAT!4dLNG — точный пин места в data-хвосте развёрнутого URL / метаданных place (надёжно).
_RE_MAPS_3D4D = re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)")
# @LAT,LNG,zoom в САМОЙ ссылке клиента — надёжно (то, что клиент расшарил). В ТЕЛЕ страницы
# `@` не берём (там это вьюпорт карты) — тело парсит только точный пин, см. _parse_pin_from_body.
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
    """Достать НАДЁЖНЫЕ (lat, lon) из строки URL Google Maps или None. Сначала URL-декодируем
    (%2C→',', %2F→'/' и т.п.), затем пробуем надёжные форматы: ?q=/query=… → !3d…!4d… → @…,….
    Устойчив к обрезанному мессенджером хвосту. Вьюпорт-центроид `center=` НЕ парсим (ASK #51)."""
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


def _parse_pin_from_body(body):
    """Достать ТОЧНЫЙ пин места (lat, lon) из ТЕЛА развёрнутой place-страницы, либо None.
    Решение ASK #51: из тела берём ТОЛЬКО точный пин `!3d<lat>!4d<lon>` (метаданные place),
    а НЕ вьюпорт-центроид (`center=` статической карты / og:image — центр картинки, не точка
    клиента). Нет точного пина в теле → None → честный [уточнить] (цену по центроиду не
    называем никогда)."""
    if not body:
        return None
    try:
        dec = urllib.parse.unquote(body)
    except Exception:
        dec = body
    m = _RE_MAPS_3D4D.search(dec)
    if m:
        return _valid_latlon(m.group(1), m.group(2))
    return None


def _is_short_maps_link(url):
    """URL — короткая ссылка Google Maps (maps.app.goo.gl / goo.gl / g.co)?"""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    host = host.split("@")[-1].split(":")[0]  # срезать креды/порт, если есть
    return host in _MAPS_SHORT_HOSTS or host.endswith(".app.goo.gl")


def _fetch_body(url):
    """GET тела финальной страницы (для place-ссылок, где координат в URL нет вовсе). Ищем в
    теле ТОЛЬКО точный пин `!3d!4d` (метаданные place); вьюпорт-центроид `center=` статической
    карты игнорируем (ASK #51). Читаем не более _MAPS_BODY_CAP байт, возвращаем текст.
    Может кинуть (таймаут/сеть) — ловится выше."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": _MAPS_UA})
    with urllib.request.urlopen(req, timeout=MAPS_TIMEOUT) as r:
        return r.read(_MAPS_BODY_CAP).decode("utf-8", "replace")


def _default_expand(url):
    """Развернуть короткую ссылку по цепочке HTTP-редиректов (HEAD, БЕЗ исполнения JS) до
    конечного URL. Если НАДЁЖНЫЕ координаты уже в конечном URL (?q=/@/!3d!4d) — возвращаем URL
    как есть. Если их в URL НЕТ (place-ссылка: share по объекту `data=…!1s<feature-id>…`) —
    догружаем тело GET'ом и возвращаем «URL\\n+тело» одной строкой; из тела resolve_maps_link
    берёт ТОЛЬКО точный пин `!3d!4d` (метаданные place), а вьюпорт-центроид `center=` статической
    карты игнорирует (ASK #51). Может кинуть (таймаут/сеть) — ловится в resolve_maps_link."""
    cur = url
    final = None
    # Не даём urllib молча ходить по редиректам — сами читаем Location.
    opener = urllib.request.build_opener(_NoRedirectHandler)
    for _ in range(_MAPS_MAX_HOPS):
        req = urllib.request.Request(
            cur, method="HEAD",
            headers={"User-Agent": _MAPS_UA})
        try:
            resp = opener.open(req, timeout=MAPS_TIMEOUT)
        except urllib.error.HTTPError as e:
            # 3xx (и любой не-2xx) прилетает от opener'а как HTTPError, а НЕ возвращается:
            # с _NoRedirectHandler редирект-хендлер отдаёт None ⇒ urllib поднимает HTTPError.
            # Сам объект ошибки И ЕСТЬ ответ (у него есть .getcode()/.headers/.close()) —
            # из него и читаем код + Location. Без этого разворот падал на ПЕРВОМ же 302
            # (живой goo.gl отдаёт ровно `302 Found` + `Location: …/maps/place/…`), и вся
            # цепочка коротких ссылок молча деградировала в None ⇒ ложный [уточнить].
            resp = e
        try:
            code = resp.getcode()
            if code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("Location")
                if not loc:
                    final = cur
                    break
                cur = urllib.parse.urljoin(cur, loc)
                continue
            final = resp.geturl()
            break
        finally:
            resp.close()
    if final is None:
        final = cur
    # надёжные координаты уже в URL → тело не нужно (быстрый путь, без второго запроса)
    if _parse_coords_from_url(final):
        return final
    # place-ссылка без координат в URL — точный пин (!3d!4d) может быть в теле: догружаем тело
    # GET'ом (вьюпорт-центроид center= при разборе тела игнорируется, ASK #51)
    try:
        body = _fetch_body(final)
    except Exception as e:
        log.info(f"_default_expand: тело не скачалось ({type(e).__name__}) — только URL")
        return final
    return final + "\n" + body if body else final


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Не следовать редиректам автоматически — вернуть 3xx-ответ вызывающему коду."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_maps_link(url, _expand=None):
    """Ссылка Google Maps → (lat, lon) точки доставки, либо None.
    Короткие maps.app.goo.gl/goo.gl разворачиваются HTTP-редиректом (с таймаутом, без JS);
    надёжные форматы ?q=lat,lng, @lat,lng, !3d…!4d…, %2C-кодирование и обрезанные мессенджером
    хвосты. place-ссылка без координат в URL — из ТЕЛА развёрнутой страницы берём ТОЛЬКО точный
    пин `!3d!4d` (метаданные place); вьюпорт-центроид `center=` статической карты/og:image
    НЕнадёжен и игнорируется (решение ASK #51: цену по центроиду не называем). Надёжных координат
    нет ни в URL, ни точного пина в теле / битая ссылка / не-строка → None.
    _expand — инъекция сети для тестов (возвращает конечный URL, а для place-ссылок — «URL\\n+тело»).
    НИКОГДА не роняет вызывающий код."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    # 1) надёжные координаты уже в ссылке — берём без сети
    coords = _parse_coords_from_url(url)
    if coords:
        return coords
    # 2) короткая ссылка — развернуть редиректом и распарсить
    if _is_short_maps_link(url):
        expander = _expand or _default_expand
        try:
            final = expander(url)
        except Exception as e:
            log.info(f"resolve_maps_link: разворот не удался ({type(e).__name__}) — None")
            return None
        if isinstance(final, str):
            # _default_expand отдаёт «конечный URL\n+тело» для place-ссылок. Разделяем источники:
            # из URL — надёжные координаты клиента; из ТЕЛА — ТОЛЬКО точный пин (!3d!4d), вьюпорт
            # (center= статической карты) не берём (ASK #51).
            url_part, _sep, body_part = final.partition("\n")
            coords = _parse_coords_from_url(url_part)
            if coords:
                return coords
            if body_part:
                return _parse_pin_from_body(body_part)
    # 3) place-ссылка без надёжных координат / битая ссылка → None
    return None


# URL любой ссылки в свободном тексте реплики клиента (из неё выдёргиваем maps-ссылку).
_URL_IN_TEXT_RE = re.compile(r"https?://\S+", re.I)
# Хвостовая пунктуация/кавычки, налипающие на URL в переписке — срезаем перед разбором.
_URL_TRAILING = ").,;>»\"'«"


def _looks_like_maps_url(u):
    """URL — ссылка Google Maps? Короткая (maps.app.goo.gl/goo.gl/g.co) ИЛИ полный
    google.*/maps / maps.google.* . Прочие ссылки (bit.ly, сайт виллы) — нет."""
    if _is_short_maps_link(u):
        return True
    try:
        host = urllib.parse.urlparse(u).netloc.lower().split("@")[-1].split(":")[0]
        path = (urllib.parse.urlparse(u).path or "").lower()
    except Exception:
        return False
    if host.startswith("maps.google."):
        return True
    if host == "google.com" or host.startswith("www.google.") or host.startswith("google.") or ".google." in host:
        return "/maps" in path
    return False


def extract_maps_link(text):
    """Первая ссылка Google Maps в свободном тексте (реплика клиента) → URL | None.
    Ловим ТОЛЬКО саму ссылку (http(s)://…maps…): упоминание «вилла/локация» без URL — не гео.
    Не роняет вызывающий код (не-строка/мусор → None)."""
    if not isinstance(text, str):
        return None
    for m in _URL_IN_TEXT_RE.finditer(text):
        u = m.group(0).rstrip(_URL_TRAILING)
        if _looks_like_maps_url(u):
            return u
    return None


# ===========================================================================
# resolve_delivery — (lat, lon) точки + зоны из Bridge → зона/цена доставки | [уточнить]
# ===========================================================================
#
# Чистая функция (без сети, без I/O, детерминирована). Логика:
#   1) haversine-дистанция от точки до якоря каждой зоны;
#   2) ближайший якорь, чья дистанция ≤ его радиус_км → цена ЭТОЙ зоны
#      (на границе двух зон побеждает БЛИЖАЙШИЙ якорь — не «первый в списке»);
#   3) иначе если есть якорь с дистанцией ≤ радиус_км + OUT_BELT_KM (5 км) →
#      периферийный пояс: OUT_BELT_PRICE (1490);
#   4) иначе (далеко / вне Пхукета / нет валидных якорей / битая точка) → маркер [уточнить].
#
# FAIL-SAFE (не ослаблять): любая неоднозначность — нет координат, зона без якоря/радиуса,
# зона в радиусе но без цены, вообще нет валидных зон — уводит в «[уточнить]», а НЕ в
# случайную цену. Лучше переспросить, чем назвать неверную стоимость доставки.

# Ширина периферийного пояса за границей зоны (км) и цена доставки в нём.
OUT_BELT_KM = float(os.getenv("DELIVERY_OUT_BELT_KM", "5") or "5")
OUT_BELT_PRICE = int(os.getenv("DELIVERY_OUT_BELT_PRICE", "1490") or "1490")
# Маркер честного фолбэка — то же слово, что и по всему контуру доставки.
DELIVERY_MARKER = "[уточнить]"

_EARTH_R_KM = 6371.0088


def _haversine_km(lat1, lon1, lat2, lon2):
    """Дистанция по большому кругу между двумя гео-точками, км."""
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def _zone_as_dict(z):
    """Привести зону к dict-схеме. Живой Bridge отдаёт зоны ПОЗИЦИОННЫМ списком
    `[name, lat, lon, price, radius]` (как сырые строки листа: см. fixtures/
    delivery_zones_get.live.json — `["Раваи", 7.771, 98.327, 590, 4]`), а не словарём.
    Разворачиваем такой список в те же ключи, что у dict-схемы, чтобы _zone_anchor/
    _zone_radius_km/_zone_price/имя работали единообразно на ОБОИХ форматах.
    dict — возвращаем как есть; список <5 полей или прочий мусор → {} (зона отфильтруется
    как битая якорем/радиусом — fail-safe, а не выдуманная цена)."""
    if isinstance(z, dict):
        return z
    if isinstance(z, (list, tuple)) and len(z) >= 5:
        name, lat, lon, price, radius = z[0], z[1], z[2], z[3], z[4]
        return {"name": name, "lat": lat, "lon": lon,
                "price": price, "radius_km": radius}
    return {}


def _zone_anchor(z):
    """Якорь зоны (lat, lon) в допустимых диапазонах, либо None. Терпим к схеме:
    позиционный список `[name,lat,lon,price,radius]` (живой Bridge), плоские lat/lon(lng),
    вложенный anchor/center-словарь, либо пара-список coords."""
    z = _zone_as_dict(z)
    if not z:
        return None
    lat = z.get("lat", z.get("latitude"))
    lon = z.get("lon", z.get("lng", z.get("longitude")))
    if lat is None or lon is None:
        for key in ("anchor", "center", "coords", "point"):
            sub = z.get(key)
            if isinstance(sub, dict):
                lat = sub.get("lat", sub.get("latitude"))
                lon = sub.get("lon", sub.get("lng", sub.get("longitude")))
                break
            if isinstance(sub, (list, tuple)) and len(sub) == 2:
                lat, lon = sub[0], sub[1]
                break
    return _valid_latlon(lat, lon)


def _zone_radius_km(z):
    """Радиус зоны в км (float > 0), либо None при отсутствии/мусоре.
    Принимает позиционный список Bridge и dict-схему (см. _zone_as_dict)."""
    z = _zone_as_dict(z)
    r = z.get("radius_km", z.get("radius", z.get("r_km")))
    try:
        r = float(r)
    except (TypeError, ValueError):
        return None
    return r if r > 0 else None


def _zone_price(z):
    """Цена доставки зоны (число), либо None при отсутствии/мусоре.
    Принимает позиционный список Bridge и dict-схему (см. _zone_as_dict)."""
    z = _zone_as_dict(z)
    p = z.get("price", z.get("price_thb", z.get("cost")))
    if isinstance(p, bool):  # bool — подкласс int, но не цена
        return None
    return p if isinstance(p, (int, float)) else None


def resolve_delivery(lat, lon, zones, cfg=None):
    """Точка доставки (lat, lon) + список зон Bridge → результат зоны/цены доставки.

    Возвращает dict:
      {"status": "zone",     "zone": <имя>, "price": <цена зоны>, "distance_km": <d>, "marker": None}
      {"status": "out_belt", "zone": None,  "price": OUT_BELT_PRICE, "distance_km": <d>, "marker": None}
      {"status": "uncertain","zone": None,  "price": None, "distance_km": <d|None>, "marker": "[уточнить]"}

    cfg — необязательный dict-оверрайд: out_belt_km, out_belt_price, marker.
    Чистая: без сети/I/O, детерминирована. Fail-safe: любая неоднозначность → uncertain."""
    cfg = cfg or {}
    out_belt_km = float(cfg.get("out_belt_km", OUT_BELT_KM))
    out_belt_price = cfg.get("out_belt_price", OUT_BELT_PRICE)
    marker = cfg.get("marker", DELIVERY_MARKER)

    def _uncertain(dist=None):
        return {"status": "uncertain", "zone": None, "price": None,
                "distance_km": dist, "marker": marker}

    pt = _valid_latlon(lat, lon)
    if pt is None:                       # нет/битая координата → честный фолбэк
        return _uncertain()
    if not isinstance(zones, (list, tuple)) or not zones:
        return _uncertain()              # зон нет (Bridge недоступен) → фолбэк

    plat, plon = pt
    # Собираем валидные якоря: (дистанция, радиус, цена, имя). Битые зоны молча пропускаем.
    anchors = []
    for z in zones:
        zd = _zone_as_dict(z)            # позиционный список Bridge → dict-схема
        a = _zone_anchor(zd)
        r = _zone_radius_km(zd)
        if a is None or r is None:
            continue
        d = _haversine_km(plat, plon, a[0], a[1])
        anchors.append((d, r, _zone_price(zd), zd.get("name")))
    if not anchors:
        return _uncertain()              # ни одного валидного якоря → фолбэк

    anchors.sort(key=lambda t: t[0])     # по возрастанию дистанции — ближайший первым
    nearest_d = anchors[0][0]

    # 1) ближайший якорь, покрывающий точку своим радиусом → цена его зоны.
    for d, r, price, name in anchors:    # уже отсортированы: первый покрывающий = ближайший
        if d <= r:
            if price is None:            # покрыт, но цена зоны неизвестна → fail-safe
                return _uncertain(round(d, 3))
            return {"status": "zone", "zone": name, "price": price,
                    "distance_km": round(d, 3), "marker": None}

    # 2) периферийный пояс: любой якорь в пределах радиус + OUT_BELT_KM.
    for d, r, _price, _name in anchors:
        if d <= r + out_belt_km:
            return {"status": "out_belt", "zone": None, "price": out_belt_price,
                    "distance_km": round(d, 3), "marker": None}

    # 3) далеко / вне Пхукета → честный фолбэк.
    return _uncertain(round(nearest_d, 3))


# ===========================================================================
# resolve_delivery_from_text — оркестратор пайплайна доставки от текста клиента
# ===========================================================================
#
# Склеивает три ступени в один вход для конвейера черновика: свободный текст реплики клиента →
# extract_maps_link → resolve_maps_link → get_delivery_zones(Bridge) → resolve_delivery.
# Контракт для вызывающего кода (build_pricing_note):
#   • в тексте НЕТ maps-ссылки            → None  (доставку НЕ трогаем: остаётся текущий честный
#                                                   путь — район уточняет менеджер);
#   • ссылка есть, но координаты/зоны/Bridge не дались → dict status=uncertain, marker=[уточнить]
#                                                   (цену НЕ выдумываем);
#   • координаты + зоны Bridge            → dict status=zone/out_belt с ценой доставки.
# FAIL-SAFE: любое исключение внутри → None (конвейер просто не добавит строку доставки).


def resolve_delivery_from_text(text, _get_zones=None, _resolve_maps=None, cfg=None):
    """Текст клиента → результат resolve_delivery, либо None если maps-ссылки в тексте нет.
    _get_zones/_resolve_maps/cfg — инъекция для тестов. НИКОГДА не роняет вызывающий код."""
    try:
        url = extract_maps_link(text)
        if not url:
            return None
        coords = (_resolve_maps or resolve_maps_link)(url)
        if not coords:                       # ссылка была, но координат нет → честный [уточнить]
            return resolve_delivery(None, None, None, cfg=cfg)
        zones = (_get_zones or get_delivery_zones)()
        return resolve_delivery(coords[0], coords[1], zones, cfg=cfg)
    except Exception as e:
        log.info(f"resolve_delivery_from_text упал ({type(e).__name__}) — None")
        return None


def resolve_delivery_from_coords(lat, lon, _get_zones=None, cfg=None):
    """ГОТОВЫЕ координаты точки доставки (гео-ПИН Telegram: клиент кинул локацию, а не ссылку) →
    результат resolve_delivery, либо None если координаты битые/не заданы (пина по сути нет).
    Зоны берём из Bridge (get_delivery_zones); их недоступность → resolve_delivery честно даст
    uncertion ([уточнить]) — цену по пину не выдумываем. _get_zones/cfg — инъекция для тестов.
    Тот же контракт, что resolve_delivery_from_text (dict status=zone/out_belt/uncertain), но вход —
    уже координаты, а не текст со ссылкой. НИКОГДА не роняет вызывающий код."""
    try:
        pt = _valid_latlon(lat, lon)
        if pt is None:                       # нет/битые координаты → пина нет (не [уточнить], а None)
            return None
        zones = (_get_zones or get_delivery_zones)()
        return resolve_delivery(pt[0], pt[1], zones, cfg=cfg)
    except Exception as e:
        log.info(f"resolve_delivery_from_coords упал ({type(e).__name__}) — None")
        return None
