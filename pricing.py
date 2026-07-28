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
import socket
import logging
import urllib.request
import urllib.parse
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))

log = logging.getLogger("pricing")
log.setLevel(logging.INFO)
log.propagate = False
try:
    import log_setup                       # ротация + тестовый лог в temp (см. log_setup)
    _h = log_setup.rotating_handler(os.path.join(HERE, "pricing.log"))
    if _h is not None:
        log.addHandler(_h)
except Exception:
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
        "cap_active": src.get("cap_active"),   # низкий сезон: активен ценовой потолок
        "cap_price": src.get("cap_price"),     # цена потолка (฿/мес) для «аренда от <cap>»
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


# --------------------- ПОВТОР при разовом сбое транспорта ---------------------
# Мост — веб-приложение Apps Script: /exec отвечает 302 на googleusercontent, и цель
# редиректа ИНОГДА отдаёт 404. Это флакость транспорта, а не «адреса нет»: 28.07 запрос
# парка упал с 404, а живые пробы тем же адресом с этого же ПК дали HTTP 200
# (docs/artifacts/2026-07-28-journal-write-fixes.md §1). Поэтому 404 здесь ВРЕМЕННЫЙ и
# повторяется — ровно один раз, чтобы разовый отказ не доезжал до клиента.
_RETRY_HTTP = (404, 429, 500, 502, 503, 504)
RETRY_TRIES = int(os.getenv("BRIDGE_RETRY_TRIES", "2") or "2")
RETRY_PAUSE_SEC = float(os.getenv("BRIDGE_RETRY_PAUSE", "1") or "1")


def transient(e):
    """Временный ли сбой (стоит повторить). HTTPError проверяем ПЕРВЫМ: он наследник URLError."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in _RETRY_HTTP
    return isinstance(e, (urllib.error.URLError, TimeoutError, socket.timeout, OSError))


def get_retry(getter, params, tries=None, _sleep=None):
    """getter(params) с повтором при временном сбое. → (данные, None) | (None, исключение).
    Постоянные ошибки (401/403 и прочее) НЕ повторяем — второй заход даст тот же ответ."""
    n = RETRY_TRIES if tries is None else tries
    sleeper = _sleep if _sleep is not None else time.sleep
    last = None
    for i in range(max(1, n)):
        try:
            return getter(params), None
        except Exception as e:
            last = e
            if i + 1 >= n or not transient(e):
                break
            log.info(f"повтор после временного сбоя ({type(e).__name__}) — попытка {i + 2}/{n}")
            try:
                sleeper(RETRY_PAUSE_SEC)
            except Exception:
                pass
    return None, last


# ------------------------------- парк (fleet) --------------------------------

def fleet_status(_get=None, _now=None, _sleep=None):
    """Парк + ЧЕСТНЫЙ признак, получены ли данные. → (list[dict], ok: bool).

    ok=True  — источник ответил и ответ распознан. ПУСТОЙ список при ok=True означает
               «парк реально пуст», а не «мост лёг».
    ok=False — данные НЕ получены (нет конфига / сеть / ответ не-ok). Список при этом
               может быть непустым (отдаём стухший кэш), но считать его свежим нельзя.

    Разводит ровно те два случая, которые были неразличимы: fleet() отдавала [] и когда
    парк пуст, и когда мост упал, — вызывающий код видел одно и то же."""
    now = _now() if _now else time.time()
    c = _FLEET_CACHE
    if c["data"] is not None and (now - c["ts"]) < FLEET_TTL:
        return c["data"], True
    if not (BRIDGE_URL and BRIDGE_TOKEN) and _get is None:
        return (c["data"] or []), False
    getter = _get or _default_get
    data, err = get_retry(getter, {"action": "fleet", "token": BRIDGE_TOKEN}, _sleep=_sleep)
    if err is not None:
        log.info(f"fleet упал ({type(err).__name__}) — данные НЕ получены, отдаю кэш/пусто")
        return (c["data"] or []), False
    bikes = None
    if isinstance(data, dict) and data.get("ok"):
        src = data.get("data") if isinstance(data.get("data"), dict) else data
        if isinstance(src, dict) and isinstance(src.get("bikes"), list):
            bikes = src["bikes"]
    if bikes is None:
        log.info("fleet: ответ не-ok/без списка — данные НЕ получены, отдаю кэш/пусто")
        return (c["data"] or []), False
    c["ts"] = now
    c["data"] = bikes
    return bikes, True


def fleet(_get=None, _now=None):
    """Список байков парка. СОВМЕСТИМОСТЬ: отдаёт только список, поэтому сбой моста здесь
    неотличим от пустого парка. Новый код обязан звать fleet_status()."""
    bikes, _ok = fleet_status(_get=_get, _now=_now)
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


def quote_for_model(model, date_start, date_end, _get=None, _fleet=None, name_filter=None):
    """Резолв МОДЕЛЬ→конкретный байк и котировка. READ-ONLY (create_booking НЕ зовём).
    Возвращает {'status': ok|none_available|no_candidates|error, 'quote': dict|None}:
      ok            — нашли available байк, quote с ценой (использовать дословно);
      none_available— котировки получены, но все подходящие байки заняты на даты;
      no_candidates — нет модели/дат или в парке нет такой модели;
      error         — парк/котировки недоступны (ошибка/таймаут/не-ok).
    name_filter — необязательный предикат по ИМЕНИ юнита: сузить кандидатов до подмножества
    (напр. поколение XMAX «старое/New Gen» — квотируем только юниты своего поколения)."""
    if not (model and date_start and date_end):
        return {"status": "no_candidates", "quote": None}
    bikes = _fleet if _fleet is not None else fleet(_get=_get)
    if not bikes:
        return {"status": "error", "quote": None}
    cands = _candidates(model, bikes)
    if name_filter is not None:
        cands = [b for b in cands if name_filter(b.get("name"))]
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
