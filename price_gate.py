# -*- coding: utf-8 -*-
"""ВРЕЗКА СТОРОЖА СВЕЖЕСТИ В ПУТЬ ОТВЕТА. Тонкий слой ПРОВОДКИ, а не третья реализация.

ОСНОВАНИЕ — записанное решение владельца 17.08.2026, узел `KB_business_rules`, раздел
«ИСТОЧНИК ЦЕНЫ: ЦЕЛЕВАЯ КОНСТРУКЦИЯ»:

    ОБЯЗАТЕЛЬНОЕ УСЛОВИЕ ПЕРЕХОДА — СТОРОЖ СВЕЖЕСТИ. <…> бот при расхождении обязан
    НЕ НАЗЫВАТЬ цену и позвать владельца. Молчание уходит владельцу, вчерашняя цена
    уходит клиенту.

Слово «ОБЯЗАТЕЛЬНОЕ УСЛОВИЕ» здесь исполнено буквально: сторож включается ВМЕСТЕ с
переключением источника, а не следующим заходом. Поэтому модуль и заведён — до него врезки
не существовало вовсе (`price_freshness` объявлял себя «НЕ ПОДКЛЮЧЁН» своей же докстрокой).

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ. Не судит свежесть (судит `price_freshness.judge`), не снимает
ручки (снимает `price_freshness_run.live_handles`), не считает цену (считает `price_source`).
Здесь живёт ровно то, чего нет ни у чистой функции, ни у рук: КЭШ и БЮДЖЕТ ВРЕМЕНИ.

ЗАЧЕМ КЭШ — НЕ РАДИ ОПРЯТНОСТИ. Снятие ручек стоит девять GET к мосту (`live_handles`:
три категории × три юнита). Класть их в каждый ответ клиенту нельзя: тот же живой мост
20.08 отдал пробы за 306с, 599с и один раз не вернулся вовсе
(`docs/artifacts/2026-08-20-silent-timeout.md`). Вердикт снимается раз в окно `PRICE_GATE_TTL_MIN`
(дефолт 30 мин) и держится в памяти процесса.

КЭШИРУЕТСЯ И ОТКАЗ ТОЖЕ, и это НЕ небрежность. Не кэшируй мы `НЕИЗВЕСТНО` — каждое сообщение
клиента заново billось бы в мёртвую дверь, и молчаливый бот стал бы вдобавок медленным.
Осторожность от кэша не страдает: отказ означает молчание цены, а молчание, продлённое
на 30 минут, остаётся молчанием.

БЮДЖЕТ ВРЕМЕНИ — ВТОРОЙ ЗАМОК. Одолженный у демона клиент моста несёт сокет-таймаут в сотни
секунд; девять таких проб подряд повесили бы путь ответа на десятки минут. Пробы обёрнуты
дедлайном `PRICE_GATE_PROBE_SEC` (дефолт 20с на ВСЕ девять): исчерпан — следующая проба
бросает, `live_handles` зовёт это отказом двери, сторож — исходом `НЕИЗВЕСТНО`, и цена
не называется. Сорванная проба НИКОГДА не превращается в разрешение назвать цену.

ОТКАТ, ОБЪЯВЛЕННЫЙ ЧИСЛОМ: `PRICE_GATE_TTL_MIN=0` — врезка МЕРТВА целиком (сторож не
спрашивается, цена идёт как без него). Ноль здесь значим, поэтому парсер свой.
"""

import logging
import os
import time

import price_freshness
import price_freshness_run

log = logging.getLogger("price_gate")

TTL_ENV = "PRICE_GATE_TTL_MIN"
TTL_DEFAULT = 30.0

PROBE_ENV = "PRICE_GATE_PROBE_SEC"
PROBE_DEFAULT = 20.0

# Кэш вердикта. Живёт в памяти процесса: переживать перезапуск ему НЕЛЬЗЯ — снимок ручек
# на диске пережил бы и поворот ручки, а это ровно то враньё, от которого сторож и заведён.
_cache = {"until": 0.0, "verdict": None}


def reset():
    """Сбросить кэш. Нужен тестам и ручной проверке; боевой путь его не зовёт."""
    _cache.update(until=0.0, verdict=None)


def _num(env, name, default):
    """Пусто/мусор/отрицательное → дефолт; 0 значим. Тот же разбор, что у порога возраста."""
    source = env if isinstance(env, dict) else {}
    raw = source.get(name)
    if raw is None:
        return default
    text = str(raw).strip().replace(",", ".")
    if not text:
        return default
    try:
        value = float(text)
    except (TypeError, ValueError):
        return default
    return default if value < 0 else value


def ttl_minutes(env=None):
    return _num(env, TTL_ENV, TTL_DEFAULT)


def probe_seconds(env=None):
    return _num(env, PROBE_ENV, PROBE_DEFAULT)


def _bridge_caller():
    """Клиент моста, одолженный у демона. Секреты берёт САМ одолженный модуль — здесь их
    не читают и не видят (запрет класса 328). Тот же способ, что у рук сторожа."""
    import queue_snapshot_pc as qs
    daemon = qs._guard_test_logs(qs._daemon)
    return daemon.bc._get


def _bounded(caller, budget, clock):
    """Обёртка дедлайном: девять проб делят ОДИН бюджет на всех.

    Бюджет исчерпан → проба БРОСАЕТ. Бросок здесь безопаснее возврата пустого ответа:
    `live_handles` ловит его как отказ двери и кладёт ручку в «НЕ СНЯЛАСЬ», а пустой ответ
    пришлось бы отличать от настоящего нуля ручки."""
    deadline = clock() + budget

    def call(action, **kw):
        if clock() >= deadline:
            raise RuntimeError("бюджет пробы исчерпан (%.0fс)" % budget)
        return caller(action, **kw)

    return call


def _judge(env, now, get, clock):
    """Один честный заход к сторожу. Никогда не бросает: отказ — это ИСХОД, а не авария."""
    try:
        snap, why = price_freshness_run.read_rule()
    except Exception as exc:                      # noqa: BLE001 — нужен сам факт отказа
        snap, why = None, "правило не прочитано (%s)" % type(exc).__name__
    caller = get
    if caller is None:
        try:
            caller = _bounded(_bridge_caller(), probe_seconds(env), clock)
        except Exception as exc:                  # noqa: BLE001
            facts = {"ok": False, "handles": None,
                     "error": "клиент моста не поднялся (%s)" % type(exc).__name__}
            return price_freshness_run.report(snap, why, facts, env=env, now=now)[0]
    try:
        facts = price_freshness_run.live_handles(get=caller)
    except Exception as exc:                      # noqa: BLE001
        facts = {"ok": False, "handles": None,
                 "error": "ручки не снялись (%s)" % type(exc).__name__}
    return price_freshness_run.report(snap, why, facts, env=env, now=now)[0]


def verdict(env=None, now=None, get=None, clock=None):
    """Вердикт сторожа с кэшем. НИКОГДА не бросает и никогда не возвращает None.

    `get` инъектируется тестом и ручной пробой; при инъекции кэш НЕ ЧИТАЕТСЯ и НЕ ПИШЕТСЯ —
    иначе фикстура протекла бы в боевой путь того же процесса.
    """
    tick = clock if clock is not None else time.monotonic
    source = env if env is not None else dict(os.environ)
    if get is not None:
        return _judge(source, now, get, tick)

    ttl = ttl_minutes(source) * 60.0
    if ttl > 0 and _cache["verdict"] is not None and tick() < _cache["until"]:
        return _cache["verdict"]
    try:
        out = _judge(source, now, None, tick)
    except Exception as exc:                      # noqa: BLE001 — последний рубеж
        out = price_freshness.judge(None, {"ok": False, "handles": None,
                                           "error": "сторож не отработал (%s)" % type(exc).__name__},
                                    now=now)
    if ttl > 0:
        _cache.update(until=tick() + ttl, verdict=out)
    return out


def allow(env=None, now=None, get=None, clock=None):
    """ЕДИНСТВЕННОЕ, что зовёт путь ответа: можно ли называть цену.

    → (may_quote, card). `card` — текст владельцу; при разрешении он None.
    ВЫКЛЮЧЕННАЯ ВРЕЗКА (`PRICE_GATE_TTL_MIN=0`) отдаёт (True, None) НЕ СПРАШИВАЯ сторожа:
    объявленный откат обязан быть дешёвым, иначе им не воспользуются в аварии.
    """
    source = env if env is not None else dict(os.environ)
    if ttl_minutes(source) <= 0:
        return True, None
    v = verdict(env=source, now=now, get=get, clock=clock)
    action = price_freshness.bot_action(v)
    if action["name_price_to_client"]:
        return True, None
    return False, action["owner_card"]
