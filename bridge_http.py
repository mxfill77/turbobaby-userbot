# -*- coding: utf-8 -*-
"""bridge_http.py — durable-транспорт ПК-полосы к мосту (Apps Script). Секретов не знает.

ЗАЧЕМ (класс «расписка теряется на редиректе», разбор 02.08.2026 —
docs/artifacts/2026-08-02-bridge-receipt-leg-not-token.md).

Мост НИКОГДА не отвечает телом сразу: `/exec` отдаёт **302** на второе плечо
`…/macros/echo?…`, и расписку несёт оно. Все три ПК-клиента (дирижёр, журнальный писатель,
писатель мозга) ходили голым `urllib.request.urlopen`, а он идёт по редиректу САМ и по
правилам HTTP переигрывает POST как **GET без тела** (CPython, `HTTPRedirectHandler.
redirect_request`: 301/302/303 + POST → GET, заголовки `Content-*` снимаются). Дальше цепочка
приводит голый GET обратно на `/exec` уже без query — и `doGet` честно отвечает «Invalid or
missing token». Отпечаток однозначен: поле `message` есть ТОЛЬКО у отказа `doGet` (`doPost`
отдаёт `{ok:false,error:'unauthorized'}` без него) — значит на нашу ЗАПИСЬ ответил ЧИТАТЕЛЬ.
Сама запись к тому моменту уже исполнена: `writeDoc_` зовёт `brainTextWrite_` и лишь ПОТОМ
собирает `{ok:true}`. Токен, адрес и деплой ни при чём — они одни и те же у чтения и записи,
а чтение проходит.

ФОРМА ВЗЯТА С VPS (`bridge_client._exchange` / `_fetch_redirect_target`, фикс 07.07.2026),
а не изобретена здесь: не ходить по редиректу автоматически (`allow_redirects=False`),
обходить цепочку руками GET-ом по `Location` (до 6 хопов), а сбой ВТОРОГО плеча
(404/429/5xx/таймаут) повторять. Повтор второго плеча безопасен даже после write-POST: сам
POST мост уже исполнил, этот GET лишь забирает готовый ответ.

ЧЕГО С VPS НЕ ВЗЯТО — СОЗНАТЕЛЬНО, И ЭТО НЕ НЕДОСМОТР. Там на `unauthorized` делается РОВНО
одна пересылка запроса, и обоснование прямо записано в `_durable_request`: «Bridge проверяет
токен ДО исполнения действия, значит запрос НЕ исполнен». Замер 02.08 эту посылку опровергает:
`write_doc` ИСПОЛНИЛСЯ, а расписку выдал `doGet`. Пересылка такого POST-а положила бы ВТОРУЮ
запись. Поэтому здесь POST не пересылается никогда, а `unauthorized` с отпечатком `doGet`
в ответе на POST — это не отказ, а ПОТЕРЯННАЯ РАСПИСКА (`BridgeReceiptLost`): судить обязан
ФАКТ (обратное чтение дока, перечитывание статуса задачи), а не расписка. Чтения пересылать
тоже незачем: у обоих читателей свой слой повторов (`cowork_log_append.with_retry`,
`brain_writer._retry_read`). Поправка самой VPS-полосы — отдельная задача, здесь VPS не трогаем.

ЧТЕНИЕ ДЕРЖИТ ОТКАЗ ВТОРОГО ПЛЕЧА САМО (правка 07.08.2026). Ключ расписки одноразовый, поэтому
переспрашивать его бесполезно — идемпотентный GET переспрашивается ЦЕЛИКОМ, здесь, в транспорте,
а не в личной обёртке каждого клиента. Подробности и замер — у `READ_TRIES`.

КОНТРАКТ. `request_json` отдаёт разобранный JSON, а транспортный сбой ПОДНИМАЕТ ИСКЛЮЧЕНИЕМ —
ровно как прежние `_get`/`_post` на голом urlopen. Поэтому вызывающие (спул журнала, обратное
чтение, `{"ok": False, "error": …}` дирижёра) работают как работали.
"""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 30
MAX_HOPS = 6              # столько же, сколько у VPS-образца
ECHO_TRIES = 3            # попыток забрать расписку со второго плеча
ECHO_PAUSE = 0.6          # база экспоненциальной паузы между ними, сек
_REDIRECT_CODES = (301, 302, 303, 307, 308)
# Временные коды второго плеча. 404 здесь ВРЕМЕННЫЙ (одноразовый ключ echo протухает/не успевает),
# и это не догадка: 28.07 строка журнала потерялась именно на нём, а живые пробы тем же адресом
# отвечали 200. Постоянные (401/403) не повторяем — повтор их не вылечит.
_ECHO_RETRY_CODES = (404, 429, 500, 502, 503, 504)
# ── ЧТЕНИЕ ОБЯЗАНО ПЕРЕЖИВАТЬ ОТКАЗ ВТОРОГО ПЛЕЧА (класс 07.08.2026) ─────────────────────────
# ЧЕМ ЛЕЧИТСЯ МЁРТВЫЙ КЛЮЧ РАСПИСКИ. `user_content_key` одноразовый и выдаётся ПЕРВЫМ плечом.
# Значит повтор ПО ТОМУ ЖЕ адресу echo (`_fetch_receipt`) мёртвый ключ не воскрешает — воскрешает
# только НОВЫЙ запрос ЦЕЛИКОМ: у него новый 302 и новый ключ. Для POST этого делать нельзя
# (вторая запись), для GET — можно и нужно: он идемпотентен по определению.
#
# ЭТО НЕ ТЕОРИЯ, А ЗАМЕР 07.08.2026 (проба на 40 одиночных чтений `get_pending`, лог
# `docs/artifacts/2026-08-07-queue-read-second-leg.md`): 30 OK, **7 отскоков** второго плеча
# обратно на `/exec` и **3 ответа 404** — четверть чтений падала, причём ПАЧКАМИ (#5–#7, #35–#38),
# а не поодиночке. Повтор целиком с паузой пачку переживает, повтор мёртвого ключа — нет.
#
# ПОЧЕМУ ЭТОГО НЕ БЫЛО С 02.08. Форма `ab5106d` писалась по инциденту ЗАПИСИ, и лекарство
# «новый запрос целиком» легло не сюда, а в личные обёртки писателей (`cowork_log_append.
# with_retry`, `brain_writer._retry_read`). У читателя очереди (`pc_orchestrator.Bridge._call`)
# своей обёртки нет ВООБЩЕ — один отскок ослеплял виток демона целиком. За 02:46–06:18 07.08
# это дало 43 отказа `get_pending` подряд в живом логе.
#
# ПЕРВОЕ ПЛЕЧО ЗДЕСЬ НЕ ПОВТОРЯЕМ СОЗНАТЕЛЬНО: его отказ — это «связи нет» либо отказ моста по
# существу (401/403), и он обязан дойти до вызывающего немедленно, чтобы `error_kind` назвал его
# правильно. Внешние рубежи писателей как раз про первое плечо и остаются на месте.
READ_TRIES = 3            # попыток ЦЕЛИКОМ НОВОГО запроса для идемпотентного GET
READ_PAUSE = 1.2          # база экспоненциальной паузы между ними, сек
# Отпечаток отказа ЧИТАТЕЛЯ моста (Bridge.js → doGet, строки 27-32). Держим строкой в нижнем
# регистре: сравниваем нечувствительно к регистру, чтобы правка текста на стороне моста не
# превратила «расписка потеряна» в «отказ записи» молча.
_DOGET_FINGERPRINT = "invalid or missing token"


class BridgeTransportError(RuntimeError):
    """Транспортный отказ моста, который НЕЛЬЗЯ считать временным.

    Наследник `RuntimeError`, а НЕ `URLError`/`OSError`, — сознательно: `transient()` журнального
    писателя считает временными именно сетевые типы и повторяет вызов ЦЕЛИКОМ. Для write-POST
    такой повтор — вторая запись, поэтому наш отказ обязан выглядеть постоянным."""


class BridgeReceiptLost(BridgeTransportError):
    """POST исполнен, а расписку принёс `doGet` («Invalid or missing token»).

    Значит запрос где-то по цепочке стал голым GET. Это НЕ «мост отверг запись»: судьбу записи
    решает ФАКТ — обратное чтение дока (журнал, мозг) или перечитывание статуса задачи (очередь)."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """`allow_redirects=False` на языке urllib: ответ 3xx возвращается ВЫЗЫВАЮЩЕМУ как ответ.

    Штатный `HTTPRedirectHandler` не просто ходит по `Location` сам — он ещё и меняет метод
    (POST + 301/302/303 → GET, тело и заголовки `Content-*` выбрасываются). Ровно это и съедало
    токен записи. Возвращаем `fp`, а не `None`: `None` увёл бы разбор в `HTTPDefaultErrorHandler`
    и превратил редирект в исключение, а нам нужны заголовки ответа — по ним и идёт обход."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_302(self, req, fp, code, msg, headers):
        return fp

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def build_opener():
    """Свой opener без авто-редиректа.

    Глобальный `urlopen` НЕ подменяем: его дефолтным opener'ом в этом же процессе ходят другие
    вызовы (тот же пуш в Telegram у дирижёра), и молча менять им правила редиректа нельзя."""
    return urllib.request.build_opener(NoRedirect)


_OPENER = None


def _default_opener():
    global _OPENER
    if _OPENER is None:
        _OPENER = build_opener()
    return _OPENER


def _status(resp):
    code = getattr(resp, "status", None)
    if code is None:
        try:
            code = resp.getcode()
        except Exception:
            code = None
    return code


def _location(resp):
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get("Location") or headers.get("location")
    except Exception:
        return None


def _close(resp):
    try:
        resp.close()
    except Exception:
        pass


def _request(url, method, params=None, payload=None):
    """Запрос ПЕРВОГО плеча. GET — токен в query (его ждёт `doGet`), POST — тело JSON (его ждёт
    `doPost`). Метод указываем ЯВНО: он же читается в разборе ответа."""
    if str(method).upper() == "GET":
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        return urllib.request.Request(full, method="GET")
    return urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                  headers={"Content-Type": "application/json"}, method="POST")


def _echo_retryable(code):
    return code in _ECHO_RETRY_CODES or (isinstance(code, int) and code >= 500)


def _endpoint(url):
    """Опознаватель конечной точки: схема+хост+путь без query. Query отбрасываем нарочно —
    вопрос «это снова наш `/exec`?» решает адрес, а не параметры (у обратного хода их и нет)."""
    p = urllib.parse.urlsplit(url)
    return (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/").lower())


def _leg_error(method, text):
    """Отказ ПОСЛЕ того, как запрос ушёл, — с разной правдой для чтения и записи.

    GET идемпотентен: его исход известен («ответа нет»), и переспросить целиком безопасно —
    поэтому `BridgeTransportError`, который вызывающий вправе повторить. POST — нет: мутация
    МОГЛА исполниться до того, как умерло плечо расписки, поэтому `BridgeReceiptLost`, и
    повторять его нельзя ни здесь, ни у вызывающего. Разделение сделано ТИПОМ, а не текстом,
    чтобы `cowork_log_append.transient` могло опираться на него без разбора строк.

    АДРЕСОВ ЗДЕСЬ НЕ ПЕЧАТАЕМ: текст уходит в лог, в спул и в журнал, а полный `/exec` несёт
    id деплоя. Плечо называем словом, а не ссылкой."""
    if str(method).upper() == "GET":
        return BridgeTransportError(text)
    return BridgeReceiptLost(text + " Действие МОГЛО исполниться — судить обязан ФАКТ, "
                                    "слепой повтор запрещён.")


_BOUNCE_TEXT = ("второе плечо моста (echo) бросает обратно на наш же /exec — ключ расписки мёртв. "
                "Дальше идти нельзя: туда придёт голый GET без тела и без токена, и doGet ответит "
                "ложным «Invalid or missing token».")
# ВОЗВРАТ НА СВОЙ ЖЕ `/exec` — не плечо расписки, а ловушка (закрыта на ПК 02.08.2026).
# Живой отпечаток разбора: на нашу ЗАПИСЬ ответил `doGet`. Механически это значит одно — по
# цепочке мы пришли обратно на `/exec`, но уже голым GET. Ветка заведена по фикстуре и в тот же
# день СРАБОТАЛА ВЖИВУЮ (15:44 UTC, чтение журнала) — то есть петля реальна, а не реконструкция.
# ФОРМА VPS ЭТОГО НЕ ЛОВИТ: там обход идёт по любому `Location` (`_exchange`), а отпечаток
# `doGet` разбирается как обычный `unauthorized` и рождает пересылку запроса. Точка касания для
# отдельной задачи VPS-полосы; здесь мы её просто не повторяем.


def _fetch_receipt(url, timeout, opener, sleeper, origin, method, tries=ECHO_TRIES):
    """GET по `Location` — забрать расписку со ВТОРОГО плеча, с повторами на 404/429/5xx/таймаут
    и на возврате к своему же `/exec`.

    ПОВТОР ЗДЕСЬ БЕЗОПАСЕН ДАЖЕ ПОСЛЕ ЗАПИСИ (образец VPS `_fetch_redirect_target`, ночной
    инцидент 07.07): сам POST мост уже исполнил, этот GET лишь забирает готовый ответ. Именно
    поэтому durability write-пути даёт ЭТОТ повтор, а не повтор записи."""
    last = None
    for attempt in range(max(1, tries)):
        if attempt:
            sleeper(ECHO_PAUSE * (2 ** (attempt - 1)))
        try:
            resp = opener.open(urllib.request.Request(url, method="GET"), timeout=timeout)
        except urllib.error.HTTPError as e:
            if not _echo_retryable(e.code):
                raise
            last = e
            continue
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
            last = e
            continue
        code = _status(resp)
        if _echo_retryable(code):
            _close(resp)
            last = urllib.error.HTTPError(url, code, "второе плечо моста",
                                          getattr(resp, "headers", None), None)
            continue
        if code in _REDIRECT_CODES and _endpoint(
                urllib.parse.urljoin(url, _location(resp) or "")) == origin:
            _close(resp)
            # ОТСКОК = ключ расписки МЁРТВ, а не «не поспел»: он одноразовый и выдан первым
            # плечом. Для GET переспрашивать его бессмысленно — лечит только новый запрос
            # целиком (внешний цикл `exchange`), поэтому отдаём отказ СРАЗУ и не жжём 1,8 с
            # пауз на заведомо мёртвом адресе. Для POST внешнего повтора нет и не будет
            # (вторая запись), так что там прежнее поведение — переспросить всё же стоит.
            err = _leg_error(method, _BOUNCE_TEXT)
            if str(method).upper() == "GET":
                raise err
            last = err
            continue
        return resp        # расписка либо честный следующий хоп — дальше решает внешний цикл
    raise last


def _walk(url, method, params, payload, timeout, op, slp, max_hops, answered):
    """Один проход цепочки. → финальный ответ.

    `answered` — список-флаг: как только первое плечо ответило редиректом, сюда падает отметка.
    По ней внешний цикл отличает «до моста не дошли» (повторять не наше дело) от «отказало ВТОРОЕ
    плечо» (для GET лечится новым запросом целиком)."""
    origin = _endpoint(url)
    resp = op.open(_request(url, method, params, payload), timeout=timeout)
    cur, hops = url, 0
    while _status(resp) in _REDIRECT_CODES:
        answered.append(True)
        loc = _location(resp)
        _close(resp)
        if not loc:
            raise _leg_error(method, "мост ответил %s без Location — цепочку расписки не пройти."
                                     % _status(resp))
        if hops >= max_hops:
            raise _leg_error(method, "цепочка редиректов моста длиннее %d хопов — обрываю "
                                     "(вслепую по ней ходить нельзя: POST станет голым GET)."
                                     % max_hops)
        cur = urllib.parse.urljoin(cur, loc)
        if _endpoint(cur) == origin:
            raise _leg_error(method, _BOUNCE_TEXT)   # первое плечо ведёт назад — повторять нечего
        resp = _fetch_receipt(cur, timeout, op, slp, origin, method)
        hops += 1
    return resp


def _read_retryable(exc):
    """Стоит ли переспрашивать ЦЕЛИКОМ идемпотентное чтение после отказа второго плеча.

    `BridgeReceiptLost` — никогда и ни при каких условиях (у GET он и не рождается, но проверка
    стоит первой: он наследник `BridgeTransportError`, и молчаливое расширение типов не смеет
    открыть повтор мутации). `HTTPError` проверяется раньше `URLError`/`OSError` — он их
    наследник; постоянные коды второго плеча (401/403) повторять по-прежнему незачем."""
    if isinstance(exc, BridgeReceiptLost):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return _echo_retryable(exc.code)
    return isinstance(exc, (BridgeTransportError, urllib.error.URLError,
                            TimeoutError, socket.timeout, OSError))


def exchange(url, method, params=None, payload=None, timeout=DEFAULT_TIMEOUT,
             opener=None, sleeper=None, max_hops=MAX_HOPS, tries=None):
    """Один обмен с мостом с РУЧНЫМ обходом цепочки. → финальный ответ (его читает вызывающий).

    ЧТЕНИЕ ПЕРЕЖИВАЕТ ОТКАЗ ВТОРОГО ПЛЕЧА: `GET` переспрашивается ЦЕЛИКОМ (`READ_TRIES`) — новый
    запрос получает новый одноразовый ключ расписки, см. блок у `READ_TRIES`. Мутация не
    переспрашивается НИКОГДА: `n` для не-GET жёстко равно 1, и это не настройка.

    opener/sleeper/tries — точки инъекции для тестов (тот же приём, что get/post у brain_writer):
    сеть в проверках не задевается ни разу."""
    op = opener or _default_opener()
    slp = sleeper or time.sleep
    n = 1 if str(method).upper() != "GET" else max(1, READ_TRIES if tries is None else tries)
    for attempt in range(n):
        answered = []
        try:
            return _walk(url, method, params, payload, timeout, op, slp, max_hops, answered)
        except Exception as e:
            if attempt + 1 >= n or not answered or not _read_retryable(e):
                raise
            slp(READ_PAUSE * (2 ** attempt))


def _doget_refusal(data):
    """Отпечаток отказа ЧИТАТЕЛЯ: `doGet` кладёт в тело поле `message`, `doPost` — нет
    (Bridge.js: doGet 27-32 против doPost 234; асимметрия одинакова во всех трёх снимках моста
    на ПК). На ней и держится вывод «на запись ответил читатель»."""
    if not isinstance(data, dict) or data.get("ok"):
        return False
    if str(data.get("error") or "").strip().lower() != "unauthorized":
        return False
    return _DOGET_FINGERPRINT in str(data.get("message") or "").lower()


# ── ОТКАЗ НАЗЫВАЕТСЯ ПО СУЩЕСТВУ, А НЕ ИМЕНЕМ КЛАССА (класс 05.08.2026) ──────────────────────
# ЗАМЕР по живому `pc_orchestrator.log` (5211 строк, 22.07→05.08, разбор
# docs/artifacts/2026-08-05-three-states-one-root.md): слово «сеть» — 0 совпадений, голых имён
# исключений в строках отказа моста — 89. Обрыв 04.08 18:02–19:18 UTC записан как
# `get_pending(new) ошибка: URLError` — ИМЕНЕМ КЛАССА, из которого штаб дважды построил ложный
# диагноз «я сломался» вместо «связи нет».
#
# ГЛАВНОЕ РАЗЛИЧЕНИЕ ЗДЕСЬ — НЕ красота текста, а один вопрос: ДОШЛИ ЛИ МЫ ДО МОСТА ВООБЩЕ.
#   • `HTTPError` — мост/фронт ОТВЕТИЛ кодом. Связь ЖИВА, отказ по существу (401 и 500 — разные
#     новости, и код больше не теряется);
#   • `BridgeTransportError`/`BridgeReceiptLost` — ответ пришёл (редирект/тело), обмен не
#     завершён. Связь ЖИВА. Это НЕ теория: таких в логе 354 за 02–05.08 при полностью живой
#     сети (включая сегодняшние), и считать их обрывом значило бы 354 ложных «связи нет»;
#   • `URLError`/`TimeoutError`/`ConnectionError`/прочий `OSError` — до моста не дошли ВОВСЕ.
#     Ровно эти имена стоят в 37 отказах внутри окна обрыва 04.08.
KIND_NETWORK = "network"   # связи нет: запрос не доехал до моста
KIND_HTTP = "http"         # мост ответил кодом — связь есть
KIND_BRIDGE = "bridge"     # ответ пришёл, обмен не завершён — связь есть
KIND_OTHER = "other"       # не опознали. Имя «unknown» СОЗНАТЕЛЬНО не берём: у ПК уже есть класс
                           # ложных диагнозов вокруг `unknown`/`unknown_tool` гарда — не пересекаем


def error_kind(exc):
    """Класс отказа моста ОДНИМ словом (см. блок выше). → KIND_*.

    Порядок проверок — не стиль: `HTTPError` наследует `URLError`, `URLError` — `OSError`,
    `TimeoutError` — тоже `OSError`. Частное обязано проверяться раньше общего, иначе живой
    отказ «мост ответил 401» уехал бы в «связи нет»."""
    if isinstance(exc, urllib.error.HTTPError):
        return KIND_HTTP
    if isinstance(exc, BridgeTransportError):        # и наследник BridgeReceiptLost
        return KIND_BRIDGE
    if isinstance(exc, (urllib.error.URLError, TimeoutError, socket.timeout,
                        ConnectionError, OSError)):
        return KIND_NETWORK
    return KIND_OTHER


def _reason_text(exc):
    """Причина отказа словами: у `URLError` она лежит в `.reason` (там и сидит `gaierror
    [Errno 11001] getaddrinfo failed`), у остальных — сам текст исключения."""
    r = getattr(exc, "reason", None)
    return (str(r) if r is not None else str(exc)) or type(exc).__name__


def explain(exc, timeout=None):
    """Отказ моста ОДНОЙ строкой по существу: что произошло, дошли ли до моста, что это значит.

    АДРЕСОВ НЕ ПЕЧАТАЕМ (то же правило, что у `_leg_error`): текст уходит в лог, в спул и в
    журнал, а полный `/exec` несёт id деплоя. Плечо называем словом."""
    kind, name = error_kind(exc), type(exc).__name__
    if kind == KIND_HTTP:
        return ("мост ОТВЕТИЛ HTTP %s (%s) — связь есть, отказ по существу"
                % (getattr(exc, "code", "?"), str(getattr(exc, "reason", "") or name)[:120]))
    if kind == KIND_BRIDGE:
        return "мост ответил, но обмен не завершён (%s): %s — связь есть" % (name, str(exc)[:300])
    if kind == KIND_NETWORK:
        lim = "" if timeout is None else ", предел ожидания %sс" % int(timeout)
        return ("СВЯЗИ НЕТ: до моста не дошли — %s [%s%s]"
                % (_reason_text(exc)[:200], name, lim))
    return "отказ не опознан (%s): %s" % (name, str(exc)[:300])


def request_json(url, method, params=None, payload=None, timeout=DEFAULT_TIMEOUT,
                 opener=None, sleeper=None, tries=None):
    """Вызов моста → разобранный JSON. Транспортный сбой — ИСКЛЮЧЕНИЕ (контракт прежних
    `_get`/`_post` на голом urlopen сохранён дословно)."""
    resp = exchange(url, method, params=params, payload=payload, timeout=timeout,
                    opener=opener, sleeper=sleeper, tries=tries)
    try:
        raw = resp.read().decode("utf-8", "replace")
    finally:
        _close(resp)
    is_post = str(method).upper() != "GET"
    try:
        data = json.loads(raw)
    except ValueError:
        if is_post and _DOGET_FINGERPRINT in raw.lower():
            raise BridgeReceiptLost("на POST ответил читатель моста (не-JSON с отпечатком "
                                    "«Invalid or missing token») — расписка потеряна")
        raise _leg_error(method, "ответ моста не разобрать как JSON (%d символов)." % len(raw))
    if is_post and _doget_refusal(data):
        raise BridgeReceiptLost(
            "на POST ответил doGet («Invalid or missing token») — по дороге запрос стал голым "
            "GET. Это ПОТЕРЯ РАСПИСКИ, а не отказ записи: действие могло исполниться, судить "
            "обязан факт (обратное чтение дока / статус задачи), повтор вслепую запрещён")
    return data
