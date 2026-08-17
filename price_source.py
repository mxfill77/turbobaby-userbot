# -*- coding: utf-8 -*-
"""СЕЗОННЫЙ МНОЖИТЕЛЬ ЦЕНЫ ИЗ ФАЙЛА `price_source.json`. Подключение на ФЛАГЕ.

ЗАЧЕМ. Живая дверь `quote_price` отдаёт цену при ТЕКУЩЕМ положении ручки листа, а ручка
не привязана к календарю: замер 17.08 — та же модель на 8 датах через все рубежи владельца
дала РАЗНЫХ ЦЕН 1 (`docs/artifacts/2026-08-17-bot-price-source.md`, `69a6c9f`, §3). Отсюда
измеренная деньгами цена слепоты: на двух живых расчётах бот назвал 317 и 577 ฿/сут при
уплаченных 464 и 798 — −31.7 % и −27.7 %. Файл сезон ЗНАЕТ (девять периодов границами дат
владельца) и на тех же двух случаях даёт 437 и 710 — ближе к уплаченному в 5.5 и 2.5 раза
(`docs/artifacts/2026-08-17-price-source-file.md`, замок B).

ЧТО ЭТОТ МОДУЛЬ ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ.
  • делает: заменяет ЧИСЛА уже полученной живой котировки на счёт по файлу
    `база(модель) × сезон(период даты НАЧАЛА) × ступень(корзина срока)`;
  • НЕ делает: не решает, отвечать ли и когда молчать; не ходит в сеть; не судит ЗАНЯТОСТЬ.
    Наличие байка остаётся за живой дверью `quote_price` — файл её не заменяет и заменить
    не может (`available` и выбор юнита приходят из `pricing.quote_for_model` и не трогаются).

БЕЗОПАСНЫЙ ДЕФОЛТ. Ручка — env `PRICE_SOURCE_SEASON`. Нет значения, пустое, мусор → СТАРОЕ
поведение, ветка мертва целиком (`reprice` отдаёт свой вход тем же объектом). Включает ровно
слово из `_TRUE`. Читается НА КАЖДОМ ВЫЗОВЕ, а не на импорте: снятие флага действует сразу.

БЕЗ ФАЙЛА — БЕЗ ЧИСЛА (отрицательный замок). При включённом флаге цена берётся ТОЛЬКО из
файла: файл пропал, пуст, битый, не той схемы, модель в нём «НЕ СУДИМО» или срок вне корзин →
котировка гасится в `{"status": "error", "quote": None}`, и вызывающий уходит в ШТАТНЫЙ фолбэк
«НЕ называй никакого числа». Выдумывать нечем по построению: чисел, кроме файла, у ветки нет.
"""

import datetime
import io
import json
import logging
import os

HERE = os.path.dirname(os.path.abspath(__file__))

log = logging.getLogger("price_source")

# Путь к источнику. Модульная переменная (а не env-ручка) СОЗНАТЕЛЬНО: подмена нужна тестам
# и отрицательному замку, а второй ручки в боевом контуре быть не должно.
PATH = os.path.join(HERE, "price_source.json")

ENV_FLAG = "PRICE_SOURCE_SEASON"
_TRUE = ("1", "true", "yes", "on", "да")

_SCHEMA = "turbobaby/price_source"
_cache = {"key": None, "doc": None}


def enabled():
    """Включена ли ветка. Мусор и пустое — это ВЫКЛЮЧЕНО (безопасный дефолт)."""
    return (os.getenv(ENV_FLAG) or "").strip().lower() in _TRUE


def load(path=None):
    """Файл → dict или None. None означает «источника нет» и гасит цену, а не открывает
    дорогу старому числу. Кэш по (путь, mtime, размер): правка файла подхватывается сама."""
    p = path or PATH
    try:
        st = os.stat(p)
        key = (p, st.st_mtime, st.st_size)
    except OSError:
        _cache.update(key=None, doc=None)
        return None
    if _cache["key"] == key:
        return _cache["doc"]
    doc = None
    try:
        with io.open(p, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and raw.get("schema") == _SCHEMA \
                and isinstance(raw.get("base"), dict) \
                and isinstance(raw["base"].get("models"), list) and raw["base"]["models"] \
                and isinstance(raw.get("season"), dict) \
                and isinstance(raw["season"].get("periods"), list) and raw["season"]["periods"] \
                and isinstance(raw.get("term"), dict) \
                and isinstance(raw["term"].get("buckets"), list) and raw["term"]["buckets"]:
            doc = raw
        else:
            log.info("price_source: файл %s не той схемы — цены из него не берём", p)
    except Exception as e:
        log.info("price_source: файл %s не прочитан (%s) — цены из него не берём",
                 p, type(e).__name__)
    _cache.update(key=key, doc=doc)
    return doc


def _md(s):
    """'12-15' → (12, 15) или None."""
    try:
        mm, dd = str(s).split("-")
        return (int(mm), int(dd))
    except Exception:
        return None


def period_of(doc, day):
    """Дата НАЧАЛА аренды → период владельца (dict) или None. Границы берутся ИЗ ФАЙЛА, здесь
    их копии нет: перепишут календарь в файле — поедет и эта ветка. Периоды через новый год
    (`crosses_year`) сравниваются двумя половинами — иначе пик 15.12–05.02 не совпал бы ни с
    одним днём."""
    md = (day.month, day.day)
    for p in doc["season"]["periods"]:
        f, t = _md(p.get("from")), _md(p.get("to"))
        if not f or not t:
            continue
        if p.get("crosses_year"):
            if md >= f or md <= t:
                return p
        elif f <= md <= t:
            return p
    return None


def bucket_of(doc, days):
    """Срок в сутках → корзина ступени (dict) или None (срок вне корзин → цены не даём)."""
    for b in doc["term"]["buckets"]:
        try:
            if float(b["days_from"]) <= days <= float(b["days_to"]):
                return b
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _models(doc, key_fn):
    """Ключ модели → её строка блока base. Ключ считает ФУНКЦИЯ ВЫЗЫВАЮЩЕГО (`suggest._bike_key`):
    своего словаря синонимов здесь нет и разъехаться нечему."""
    out = {}
    for m in doc["base"]["models"]:
        k = key_fn(m.get("model"))
        if k:
            out[k] = m
    return out


def _caps(doc, key_fn):
    """Ключ модели → список кепок файла. У XMAX 300 их две (поколения) — см. cap_for."""
    out = {}
    for m in (doc.get("low_season_caps") or {}).get("models") or []:
        k = key_fn(m.get("model"))
        if k:
            out.setdefault(k, []).append(m)
    return out


def resolve_row(doc, model, key_fn, quote=None):
    """Строка файла для ЭТОЙ котировки → (row, чем опознали) | (None, почему нет).

    МИНА, СТОИВШАЯ ПЕРВОГО ЗАМЕРА 17.08. В путь ответа модель приходит ОБРЫВКОМ РЕЧИ КЛИЕНТА
    («NMAX», «XMAX», «CBR»), а в файле она названа полностью («NMAX 155»). Прямое равенство
    ключей даёт промах на КАЖДОЙ живой паре, и промах этот МОЛЧАЛИВЫЙ: цена просто гаснет, и
    заход рапортует «бот смолчал» вместо «файл не подключился». Поэтому модель опознаётся не
    равенством, а ТЕМ ЖЕ, что назвала живая дверь, — ИМЕНЕМ ПРОКОТИРОВАННОГО ЮНИТА
    («YAMAHA NMAX 155»): это её собственный ответ на вопрос «какой байк считаем», гадать не
    нужно. Обрывок речи остаётся ПОСЛЕДНИМ запасом и работает семейным правилом мерки
    («ADV» = «ADV 350», но не «XADV 750»).

    НЕОДНОЗНАЧНОСТЬ НЕ РАЗРЕШАЕТСЯ УГАДЫВАНИЕМ: «CB» подходит трём строкам файла — цены не
    будет вовсе. Молчание дешевле чужой карточки.
    """
    keyed = [(key_fn(m.get("model")), m) for m in doc["base"]["models"]]
    keyed = [(k, m) for k, m in keyed if k]
    q = quote if isinstance(quote, dict) else {}
    for src, how in ((q.get("bike"), "по имени юнита живой котировки"),
                     (q.get("model"), "по модели живой котировки"),
                     (model, "по модели из речи клиента")):
        nk = key_fn(src)
        if not nk:
            continue
        hit = [m for k, m in keyed if k in nk]                 # «nmax155» внутри «yamahanmax155»
        if len(hit) == 1:
            return hit[0], "%s «%s»" % (how, src)
        fam = [m for k, m in keyed if k == nk or k.startswith(nk)]   # «nmax» → «NMAX 155»
        if len(fam) == 1:
            return fam[0], "%s «%s» (семейство)" % (how, src)
        if len(hit) > 1 or len(fam) > 1:
            return None, "«%s» подходит нескольким строкам файла — не угадываем" % src
    return None, "модели нет в файле"


def day_price(doc, model, start, days, key_fn, quote=None):
    """Цена суток ПО ФАЙЛУ → (число|None, разбор). Разбор печатается в лог и в замеры; на
    клиентский текст он не влияет ни одной буквой."""
    info = {"model": model, "start": str(start), "days": days}
    row, how = resolve_row(doc, model, key_fn, quote)
    info["matched"] = how
    if row is None:
        info["why"] = how
        return None, info
    if not row.get("judged") or row.get("base_thb_per_day") is None:
        info["why"] = "НЕ СУДИМО (порог наблюдений файла)"
        return None, info
    per = period_of(doc, start)
    if per is None:
        info["why"] = "дата вне периодов файла"
        return None, info
    buck = bucket_of(doc, days)
    if buck is None:
        info["why"] = "срок вне корзин файла"
        return None, info
    try:
        season = float(per["multiplier"][row["class"]])
        term = float(buck["multiplier"])
        base = float(row["base_thb_per_day"])
    except (KeyError, TypeError, ValueError):
        info["why"] = "в файле нет множителя для класса/корзины"
        return None, info
    info.update(base=base, period=per.get("key"), period_name=per.get("name"),
                season=season, bucket=buck.get("bucket"), term=term,
                cls=row.get("class"), low_season=(season == 1.0),
                file_model=row.get("model"))
    return int(round(base * season * term)), info


def cap_for(doc, model, key_fn, live_cap_price=None):
    """Кепка НИЗКОГО СЕЗОНА для модели → (число|None, причина).

    Две вещи названы вслух, потому что обе — готовый ложный диагноз:
      • у XMAX 300 в файле ДВЕ строки капа (поколения 8900 и 9900), а база одна. Развести их
        нечем: поколение живёт в имени юнита, а не в модели. Совпало с живым `cap_price` —
        берём его строку; не совпало — кепку НЕ применяем вовсе (молча взятая «одна из двух»
        была бы выдумкой);
      • ПОЛОЖЕНИЕ ручки («включена ли») из файла НЕ берётся: файл сам это запрещает —
        «его положение надо ЧИТАТЬ живым quote_price на момент расчёта», истории у ручки нет.
        Отсюда: величина — из файла, признак активности — из живой котировки.
    """
    rows = [r for r in _caps(doc, key_fn).get(key_fn(model), []) if r.get("cap_thb_per_month")]
    if not rows:
        return None, "кепки в файле нет"
    if len(rows) == 1:
        return rows[0]["cap_thb_per_month"], "кепка файла"
    same = [r for r in rows if r["cap_thb_per_month"] == live_cap_price]
    if len(same) == 1:
        return same[0]["cap_thb_per_month"], "кепка файла (поколение опознано живым cap_price)"
    return None, "у модели несколько кепок (поколения), развести нечем — кепку не применяем"


def _days_of(q, ds, de):
    """Срок аренды: сначала `days` живой котировки, иначе разность ISO-дат. None — не судим."""
    try:
        d = int(q.get("days"))
        if d >= 1:
            return d
    except (TypeError, ValueError):
        pass
    try:
        return (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
    except Exception:
        return None


def reprice(res, model, ds, de, key_fn):
    """ЕДИНСТВЕННАЯ ТОЧКА ВРЕЗКИ. Вход и выход — тот же контракт, что у
    `pricing.quote_for_model`: {'status': …, 'quote': dict|None}.

    Флаг снят → возвращается ТОТ ЖЕ объект (не копия), ветка не исполняется вовсе.
    Флаг стои́т и котировка живая → числа заменяются счётом по файлу:
      day_price / total  — из файла;
      text               — СНИМАЕТСЯ: это дословная строка столбца J с ЧУЖИМИ числами, оставить
                           её рядом с новой ценой значило бы соврать клиенту двумя цифрами
                           сразу. Дальше работает уже существующая ветка сборки
                           `_client_price` (day/total/депозит/наличие) — нового текста не
                           заводится ни одного;
      депозит и НАЛИЧИЕ  — из живой котировки, файл их не знает и не подменяет;
      кепка              — величина из файла и ТОЛЬКО в низкий сезон (множитель ровно 1.000):
                           это «предложение низкого сезона» по словам самого файла, а вне дна
                           она бы гасила сезонную поправку — тот самый недобор, ради которого
                           ветка и заводится.
    Файл недоступен/не судит эту модель → {'status': 'error', 'quote': None}: молчание, а не
    старое число и не выдумка.
    """
    if not enabled():
        return res
    if not isinstance(res, dict) or res.get("status") != "ok":
        return res
    q = res.get("quote")
    if not isinstance(q, dict):
        return res
    dead = {"status": "error", "quote": None}
    doc = load()
    if doc is None:
        log.info("price_source: источника нет — цену гасим (модель %s)", model)
        return dead
    days = _days_of(q, ds, de)
    if not days:
        log.info("price_source: срок не определён — цену гасим (модель %s)", model)
        return dead
    try:
        start = datetime.date.fromisoformat(str(ds))
    except Exception:
        log.info("price_source: дата старта не разобрана (%r) — цену гасим", ds)
        return dead
    day, info = day_price(doc, model, start, days, key_fn, quote=q)
    if day is None:
        log.info("price_source: цены по файлу нет (%s) — цену гасим (модель %s)",
                 info.get("why"), model)
        return dead
    out = dict(q)
    out["day_price"] = day
    out["total"] = int(round(day * days))
    out.pop("text", None)
    cap, why = (None, "вне низкого сезона — кепка низкого сезона не применяется")
    if info.get("low_season") and q.get("cap_active"):
        # Кепку ищем по ИМЕНИ СТРОКИ ФАЙЛА (уже опознанной), а не по обрывку из речи клиента.
        cap, why = cap_for(doc, info.get("file_model"), key_fn, live_cap_price=q.get("cap_price"))
    out["cap_active"] = bool(cap)
    out["cap_price"] = cap
    log.info("price_source: %s → %s %s %sсут → %s = %s x %s (%s) x %s [%s]; кепка: %s; опознано %s",
             model, info.get("file_model"), ds, days, day, info.get("base"), info.get("season"),
             info.get("period"), info.get("term"), info.get("bucket"), why, info.get("matched"))
    return {"status": "ok", "quote": out}
