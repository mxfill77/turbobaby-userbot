# -*- coding: utf-8 -*-
"""test_bridge_http.py — регресс класса «расписка теряется на редиректе» (02.08.2026).

Основание: docs/artifacts/2026-08-02-bridge-receipt-leg-not-token.md. Мост отвечает НЕ сразу:
`/exec` отдаёт 302 на второе плечо (echo), и расписку несёт оно. Голый `urllib.request.urlopen`
идёт по редиректу САМ и переигрывает POST как GET без тела — запись исполняется, а ответ на неё
выдаёт `doGet` («Invalid or missing token»), то есть расписка теряется, притом что токен, адрес
и деплой исправны.

ФИКСТУРА = ЖИВОЙ ФОРМАТ, а не удобная схема (правило-класс «мок обязан копировать живой формат»):
коды, заголовки и ТЕЛА взяты из живой пробы 02.08 — 302 Found + `Location` + `Content-Type:
text/html; charset=UTF-8` + `Server: GSE` на первом плече, `HTTP 404` на втором, дословный отказ
`doGet` с полем `message` (у `doPost` этого поля нет — на асимметрии и держится вывод «на запись
ответил читатель»). НЕЙТРАЛИЗОВАНЫ СОЗНАТЕЛЬНО только имена хостов (`bridge.test` вместо боевых):
живые литералы в тест-файле — красный класс по гарду ПК, а форма плеч от имени хоста не зависит.
Третье плечо (echo → снова `/exec` голым GET) — РЕКОНСТРУКЦИЯ, а не снятый трафик: живая проба
доказала отпечаток `doGet` в ответе на запись, а точную петлю Google не показала. Держим её
именно как реконструкцию: она воспроизводит наблюдаемый отпечаток, и большего от неё не требуется.

Сеть здесь не задевается: подменяется ручка протокола urllib, транспорт получает opener явным
параметром (тот же приём инъекции, что get/post у brain_writer).
"""
import email.message
import io
import json
import unittest
import urllib.error
import urllib.parse
import urllib.request
import urllib.response

import bridge_http
import brain_writer as bw
import cowork_log_append as cla
import pc_orchestrator as o

EXEC_URL = "https://bridge.test/exec"
ECHO_URL = "https://echo.bridge.test/macros/echo?user_content_key=FIXTURE&lib=FIXTURE"

# Дословный отказ ЧИТАТЕЛЯ моста (Bridge.js → doGet, строки 27-32). Поле `message` есть только
# у него; doPost отвечает `{ok:false, error:'unauthorized'}` без него.
DOGET_REFUSAL = {"ok": False, "error": "unauthorized",
                 "message": "Invalid or missing token", "_status": 401}
# Расписка удавшейся записи: `chars` — длина текста на стороне моста (её печатает писатель журнала).
WRITE_RECEIPT = {"ok": True, "chars": 236955}
READ_RECEIPT = {"ok": True, "text": "строка журнала\n"}


def _response(url, code, headers, body=b""):
    """Ответ в том виде, в каком его отдаёт конвейеру ручка urllib.

    `.msg` ставим руками: `addinfourl` делегирует неизвестные имена файлу-телу, а
    `HTTPErrorProcessor` читает именно `response.msg` — без него разбор падает на ровном месте
    (проверено пробой tmp/bridge-redirect-probe-0802a)."""
    h = email.message.Message()
    for k, v in headers.items():
        h[k] = v
    r = urllib.response.addinfourl(io.BytesIO(body), h, url, code)
    r.msg = {302: "Found", 404: "Not Found", 200: "OK"}.get(code, "OK")
    return r


class FakeBridgeHost(urllib.request.HTTPSHandler):
    """Мост, как он отвечает вживую: `/exec` → 302 на echo, echo → расписка (или 404).

    echo_404 — сколько первых обращений к echo отвечают 404 (живой сбой 28.07 и 02.08 20:11:53).
    echo_bounces — echo отправляет обратно на `/exec` (реконструкция петли, съедавшей токен).
    """

    def __init__(self, receipt=None, echo_404=0, echo_bounces=False, exec_get=None,
                 echo_bounce_n=0):
        super().__init__()
        self.receipt = receipt if receipt is not None else WRITE_RECEIPT
        self.echo_404 = echo_404
        self.echo_bounces = echo_bounces
        # echo_bounce_n — у СКОЛЬКИХ ПЕРВЫХ КЛЮЧЕЙ расписки отскок, дальше ключи живые.
        #
        # СЧЁТ ИМЕННО ПО КЛЮЧАМ, А НЕ ПО ОБРАЩЕНИЯМ, — и это не стиль. `user_content_key` мост
        # выдаёт первым плечом, он одноразовый, и мёртвый ключ мёртв НАВСЕГДА: сколько раз ни
        # переспроси тот же адрес echo, ответом будет тот же отскок. Первая редакция фикстуры
        # считала отскоки глобально — и «лечилась» повтором того же ключа, то есть показывала
        # исправным ровно тот путь, который лежит вживую (правило-класс «мок обязан копировать
        # живой формат»: подогнанный мок хуже отсутствующего).
        #
        # Живой замер 07.08.2026: в серии из 40 одиночных чтений отскоки шли ПАЧКАМИ (#5–#7,
        # #35–#38), а не поодиночке — пачку обязан переживать НОВЫЙ запрос целиком.
        # Отдельным параметром от `echo_bounces` (вечный отскок) — чтобы не трогать регрессы
        # записи, стоящие на нём.
        self.echo_bounce_n = echo_bounce_n
        self.exec_get = exec_get if exec_get is not None else DOGET_REFUSAL
        self.legs = []          # (метод, адрес без query, было ли тело)
        self._issued = 0        # сколько ключей расписки выдало первое плечо
        self._minted = {}       # ключ → адрес /exec, который его выдал (цель отскока)

    def _mint(self, exec_url):
        """Первое плечо выдало НОВЫЙ одноразовый ключ расписки. → адрес второго плеча."""
        key = self._issued
        self._issued += 1
        # Цель отскока запоминаем ПОКЛЮЧЕВО. Живой замер 07.08: на ЧТЕНИИ отскок несёт ПОЛНЫЙ
        # исходный query (токен в нём цел), а у ЗАПИСИ его нет и быть не может — в POST query
        # не было. Значит запрет «не ходить по отскоку» обязан держаться на АДРЕСЕ, а не на
        # пустоте query, и фикстура проверяет именно это.
        self._minted[key] = exec_url
        return ECHO_URL + "&fix_key=%d" % key

    def _key_of(self, echo_url):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(echo_url).query)
        return int(q.get("fix_key", ["-1"])[0])

    def https_open(self, req):
        url = req.full_url
        method = req.get_method()
        self.legs.append((method, url.split("?")[0], bool(req.data)))
        if url.split("?")[0] == EXEC_URL:
            if method == "POST":
                return _response(url, 302,
                                 {"Location": self._mint(EXEC_URL),
                                  "Content-Type": "text/html; charset=UTF-8",
                                  "Server": "GSE",
                                  "Cache-Control": "no-cache, no-store, max-age=0, must-revalidate"})
            # GET на /exec: с токеном в query — читатель работает; голый (редирект съел query) —
            # дословный отказ doGet, тот самый, что пришёл на ЗАПИСЬ 02.08.
            if "token=" in url:
                return _response(url, 302,
                                 {"Location": self._mint(url),
                                  "Content-Type": "text/html; charset=UTF-8", "Server": "GSE"})
            return _response(url, 200, {"Content-Type": "application/json"},
                             json.dumps(self.exec_get).encode("utf-8"))
        if url.split("?")[0].startswith("https://echo.bridge.test/"):
            if self.echo_404 > 0:
                self.echo_404 -= 1
                raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
            if self.echo_bounces:
                return _response(url, 302, {"Location": EXEC_URL,
                                            "Content-Type": "text/html; charset=UTF-8"})
            key = self._key_of(url)
            if 0 <= key < self.echo_bounce_n:
                # Ключ МЁРТВ — и остаётся мёртвым при любом числе повторов по тому же адресу.
                # Заголовки с живой пробы 07.08: `Content-Type: application/binary`,
                # `Server: ESF`, тело пустое.
                return _response(url, 302, {"Location": self._minted.get(key, EXEC_URL),
                                            "Content-Type": "application/binary",
                                            "Server": "ESF"})
            return _response(url, 200, {"Content-Type": "application/json"},
                             json.dumps(self.receipt).encode("utf-8"))
        raise AssertionError("фикстура не знает адреса: " + url)

    def posts(self):
        return [leg for leg in self.legs if leg[0] == "POST"]


def _opener(host, durable=True):
    """Opener поверх фикстуры. durable=True — с нашим запретом авто-редиректа (как ходит
    исправленный транспорт); False — дефолтные ручки urllib (как ПК ходил до правки).
    ProxyHandler({}) — чтобы прокси-переменные машины не увели пробу в сеть."""
    extra = [bridge_http.NoRedirect] if durable else []
    return urllib.request.build_opener(host, urllib.request.ProxyHandler({}), *extra)


def _post_body(action="write_doc"):
    return {"action": action, "token": "TOK", "name": "cowork_log", "text": "новая строка"}


class DefectClass(unittest.TestCase):
    """Сам класс — на настоящей машинерии urllib, без нашего кода. Этот тест обязан
    оставаться ЗЕЛЁНЫМ и после правки: он описывает не дефект нашего модуля, а поведение
    стандартной библиотеки, ради которого правка и делается."""

    def test_bare_urlopen_replays_post_as_bare_get(self):
        host = FakeBridgeHost(echo_bounces=True)
        r = _opener(host, durable=False).open(
            urllib.request.Request(EXEC_URL, data=json.dumps(_post_body()).encode("utf-8"),
                                   headers={"Content-Type": "application/json"}, method="POST"))
        body = json.loads(r.read().decode("utf-8"))
        # три плеча: запись → echo → ГОЛЫЙ GET на /exec без тела и без query
        self.assertEqual([(m, u) for m, u, _ in host.legs],
                         [("POST", EXEC_URL), ("GET", "https://echo.bridge.test/macros/echo"),
                          ("GET", EXEC_URL)])
        self.assertFalse(host.legs[-1][2], "редирект переиграл POST как GET БЕЗ тела")
        self.assertEqual(body.get("message"), "Invalid or missing token")   # ответил doGet


class DurableTransport(unittest.TestCase):
    """Форма VPS на ПК: не идти по редиректу, обходить цепочку руками, повторять ВТОРОЕ плечо."""

    def test_post_walks_chain_by_hand_and_keeps_receipt(self):
        host = FakeBridgeHost()
        data = bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertTrue(data.get("ok"), data)
        self.assertEqual(data.get("chars"), WRITE_RECEIPT["chars"])
        self.assertEqual(len(host.posts()), 1, "POST уходит РОВНО один раз")
        self.assertEqual([(m, u) for m, u, _ in host.legs],
                         [("POST", EXEC_URL), ("GET", "https://echo.bridge.test/macros/echo")])

    def test_echo_bounce_never_becomes_a_bare_get_on_exec(self):
        """Прямой регресс отпечатка 02.08: возврат цепочки на свой же `/exec` — ловушка.

        Голым GET туда ходить нельзя (нет ни тела, ни токена — ответит `doGet` ложным отказом),
        поэтому обход НЕ идёт, а исход честно объявляется неизвестным."""
        host = FakeBridgeHost(echo_bounces=True)
        with self.assertRaises(bridge_http.BridgeReceiptLost):
            bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                     opener=_opener(host), sleeper=lambda s: None)
        self.assertNotIn(("GET", EXEC_URL), [(m, u) for m, u, _ in host.legs],
                         "на свой /exec голым GET не ходим ни разу")
        self.assertEqual(len(host.posts()), 1, "и запись не переотправляем")

    def test_echo_404_is_retried_and_the_write_is_not_repeated(self):
        # живой сбой: 20:11:53 второе плечо ответило 404, строка ушла в спул как «не записано»
        host = FakeBridgeHost(echo_404=2)
        data = bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertTrue(data.get("ok"), data)
        self.assertEqual(len(host.posts()), 1, "повторяем ТОЛЬКО забор расписки, не запись")
        self.assertEqual(len([l for l in host.legs if l[0] == "GET"]), 3)   # 404, 404, расписка

    def test_dead_echo_never_duplicates_the_write(self):
        host = FakeBridgeHost(echo_404=99)
        with self.assertRaises(urllib.error.HTTPError):
            bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                     opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(len(host.posts()), 1, "второе плечо мертво — запись НЕ переотправляем")

    def test_read_walks_the_same_chain(self):
        host = FakeBridgeHost(receipt=READ_RECEIPT)
        data = bridge_http.request_json(EXEC_URL, "GET",
                                        params={"action": "read_doc", "token": "TOK",
                                                "name": "cowork_log"},
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(data.get("text"), READ_RECEIPT["text"])
        self.assertEqual([m for m, _, _ in host.legs], ["GET", "GET"])   # /exec + echo

    def test_redirect_without_location_is_an_honest_error(self):
        class NoLocation(FakeBridgeHost):
            def https_open(self, req):
                self.legs.append((req.get_method(), req.full_url.split("?")[0], bool(req.data)))
                return _response(req.full_url, 302, {"Content-Type": "text/html"})
        host = NoLocation()
        with self.assertRaises(bridge_http.BridgeTransportError):
            bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                     opener=_opener(host), sleeper=lambda s: None)

    def test_hop_cap_stops_a_looping_chain(self):
        class Loop(FakeBridgeHost):
            def https_open(self, req):
                self.legs.append((req.get_method(), req.full_url.split("?")[0], bool(req.data)))
                return _response(req.full_url, 302, {"Location": ECHO_URL})
        host = Loop()
        with self.assertRaises(bridge_http.BridgeTransportError):
            bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                     opener=_opener(host), sleeper=lambda s: None)
        self.assertLessEqual(len(host.legs), bridge_http.MAX_HOPS + 1)


class ReceiptIsNotRefusal(unittest.TestCase):
    """Отпечаток `doGet` в ответе на POST = ПОТЕРЯННАЯ РАСПИСКА, а не отказ записи."""

    def test_doget_refusal_to_post_raises_receipt_lost(self):
        host = FakeBridgeHost(receipt=DOGET_REFUSAL)
        with self.assertRaises(bridge_http.BridgeReceiptLost):
            bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                     opener=_opener(host), sleeper=lambda s: None)

    def test_dopost_refusal_stays_a_refusal(self):
        # у отказа doPost поля `message` НЕТ — это настоящий отказ записи, подменять его нельзя
        host = FakeBridgeHost(receipt={"ok": False, "error": "unauthorized"})
        data = bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(data, {"ok": False, "error": "unauthorized"})

    def test_same_body_on_a_read_is_a_plain_answer(self):
        # читателю doGet отвечает по существу: превращать это в «потерянную расписку» нельзя
        host = FakeBridgeHost(receipt=DOGET_REFUSAL)
        data = bridge_http.request_json(EXEC_URL, "GET", params={"action": "read_doc",
                                                                "token": "BAD"},
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(data.get("error"), "unauthorized")

    def test_receipt_lost_is_not_transient_for_the_journal_writer(self):
        # `with_retry` повторяет ТОЛЬКО временные сбои; слепой повтор write_doc дал бы вторую
        # запись там, где первая уже легла (посылка VPS «unauthorized ⇒ не исполнено» неверна)
        self.assertFalse(cla.transient(bridge_http.BridgeReceiptLost("проба")))

    def test_read_side_failure_is_transient_and_the_write_side_never_is(self):
        """Живой случай 02.08 15:44 UTC: echo бросило ЧТЕНИЕ журнала обратно на /exec. Мёртвый
        ключ расписки лечится новым запросом целиком — GET идемпотентен (форма VPS retry_full)."""
        self.assertTrue(cla.transient(bridge_http._leg_error("GET", "плечо умерло")))
        self.assertFalse(cla.transient(bridge_http._leg_error("POST", "плечо умерло")))

    def test_post_never_yields_a_bare_transport_error(self):
        """Тип — единственный признак, по которому вызывающий решает «повторять или нет».
        Поэтому у POST любой отказ ПОСЛЕ отправки обязан быть `BridgeReceiptLost`."""
        for text in ("без Location", "хопы кончились", "не JSON"):
            self.assertIsInstance(bridge_http._leg_error("POST", text),
                                  bridge_http.BridgeReceiptLost, text)

    def test_leg_error_prints_no_addresses(self):
        """Текст плечевого отказа едет в лог, спул и журнал — полного /exec с id деплоя в нём
        быть не должно (граница задания владельца)."""
        for method in ("GET", "POST"):
            self.assertNotIn("http", str(bridge_http._leg_error(method, bridge_http._BOUNCE_TEXT)))


class AllThreeClients(unittest.TestCase):
    """«Дать всем трём то же, что у VPS»: журнальный писатель, писатель мозга и дирижёр.

    Здесь ходим через ЖИВЫЕ функции клиентов (у них своего sleeper-параметра нет), поэтому
    паузу между попытками забрать расписку обнуляем — иначе гейт платит секундами за сон."""

    def setUp(self):
        pause = bridge_http.ECHO_PAUSE
        bridge_http.ECHO_PAUSE = 0
        self.addCleanup(setattr, bridge_http, "ECHO_PAUSE", pause)

    def test_journal_writer_survives_the_live_chain(self):
        host = FakeBridgeHost(echo_404=1)
        r = cla.post(EXEC_URL, _post_body(), opener=_opener(host))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(len(host.posts()), 1)

    def test_journal_reader_survives_the_live_chain(self):
        host = FakeBridgeHost(receipt=READ_RECEIPT, echo_404=1)
        r = cla.get(EXEC_URL, {"action": "read_doc", "token": "TOK", "name": "cowork_log"},
                    opener=_opener(host))
        self.assertEqual(r.get("text"), READ_RECEIPT["text"])

    def test_brain_writer_survives_the_live_chain(self):
        host = FakeBridgeHost(echo_404=1)
        r = bw._post(EXEC_URL, _post_body(), opener=_opener(host))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(len(host.posts()), 1)
        host2 = FakeBridgeHost(receipt=READ_RECEIPT, echo_404=1)
        self.assertEqual(bw._get(EXEC_URL, {"action": "read_doc", "token": "TOK"},
                                 opener=_opener(host2)).get("text"), READ_RECEIPT["text"])

    def test_orchestrator_bridge_survives_the_live_chain(self):
        host = FakeBridgeHost(receipt={"ok": True}, echo_404=1)
        b = o.Bridge(url=EXEC_URL, token="TOK", opener=_opener(host))
        self.assertTrue(b._post("claim_task", id=7).get("ok"))
        self.assertEqual(len(host.posts()), 1)

    def test_orchestrator_reports_lost_receipt_as_such(self):
        # дирижёр исключений наружу не отдаёт — но и «unauthorized» врать больше не будет
        host = FakeBridgeHost(receipt=DOGET_REFUSAL)
        b = o.Bridge(url=EXEC_URL, token="TOK", opener=_opener(host))
        r = b._post("claim_task", id=7)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("error"), "BridgeReceiptLost")


class ReadSurvivesSecondLeg(unittest.TestCase):
    """Регресс класса 07.08.2026: ЧТЕНИЕ очереди обязано переживать отказ второго плеча.

    ЖИВОЙ ПОВОД. С 02:46 до 06:18 07.08 в `pc_orchestrator.log` — 43 отказа подряд вида
    `get_pending(new|in_progress) ошибка: BridgeTransportError … второе плечо моста (echo)
    бросает обратно на наш же /exec`. Мост при этом ИСПРАВЕН: проба того же адреса тем же
    токеном в 06:40 дала 30 успехов на 40 одиночных чтений (7 отскоков + 3 ответа 404).

    ПОЧЕМУ ЗАПИСЬ ПЕРЕЖИЛА, А ЧТЕНИЕ НЕТ — при ОДНОМ И ТОМ ЖЕ плече и одной и той же правке
    `ab5106d`: лекарство «переспросить ЦЕЛИКОМ» легло тогда в личные обёртки писателей
    (`cowork_log_append.with_retry`, `brain_writer._retry_read`), а сверх них у записи есть ещё
    спул. У читателя очереди (`pc_orchestrator.Bridge._call`) обёртки нет ВООБЩЕ и спула быть не
    может — один отскок ослеплял виток демона целиком. Поэтому повтор чтения переезжает в САМ
    транспорт, где им пользуются все три клиента, а не двое из трёх.

    Паузы обнуляем: цена сна не должна попадать в гейт."""

    READ_Q = {"action": "get_pending", "token": "TOK", "status": "new", "lane": "pc"}
    QUEUE = {"ok": True, "items": [{"id": "777", "lane": "pc", "task": "проба"}]}

    def setUp(self):
        for name in ("ECHO_PAUSE", "READ_PAUSE"):
            old = getattr(bridge_http, name)
            setattr(bridge_http, name, 0)
            self.addCleanup(setattr, bridge_http, name, old)

    def _exec_legs(self, host):
        return [leg for leg in host.legs if leg[1] == EXEC_URL]

    # ── (а) чтение проходит ПРИ ОТКАЗЕ второго плеча ────────────────────────────────────────
    def test_read_survives_a_burst_of_echo_bounces(self):
        host = FakeBridgeHost(receipt=READ_RECEIPT, echo_bounce_n=2)
        data = bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q,
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(data.get("text"), READ_RECEIPT["text"])
        self.assertEqual(len(self._exec_legs(host)), 3,
                         "мёртвый ключ лечит НОВЫЙ запрос целиком, а не повтор того же echo")

    def test_read_survives_an_echo_that_is_404_for_a_whole_attempt(self):
        # ECHO_TRIES=3 попыток по тому же ключу — все 404, значит спасает только новый запрос
        host = FakeBridgeHost(receipt=READ_RECEIPT, echo_404=bridge_http.ECHO_TRIES)
        data = bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q,
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(data.get("text"), READ_RECEIPT["text"])
        self.assertEqual(len(self._exec_legs(host)), 2)

    def test_queue_read_of_the_orchestrator_survives_the_bounce(self):
        """Тот самый вызов, который лежал: `Bridge.get_pending` через живой класс дирижёра."""
        host = FakeBridgeHost(receipt=self.QUEUE, echo_bounce_n=2)
        b = o.Bridge(url=EXEC_URL, token="TOK", opener=_opener(host))
        r = b.get_pending("new")
        self.assertTrue(r.get("ok"), r)
        self.assertEqual([it["id"] for it in r["items"]], ["777"])

    def test_bounce_no_longer_burns_tries_on_the_dead_key(self):
        """Отскок = ключ МЁРТВ. Переспрашивать его на чтении нельзя — это просто трата секунд."""
        host = FakeBridgeHost(receipt=READ_RECEIPT, echo_bounce_n=1)
        bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q,
                                 opener=_opener(host), sleeper=lambda s: None)
        echo = [leg for leg in host.legs if leg[1] != EXEC_URL]
        self.assertEqual(len(echo), 2, "по одному обращению к echo на каждый полный запрос")

    def test_read_gives_up_honestly_when_the_second_leg_is_dead_for_good(self):
        host = FakeBridgeHost(receipt=READ_RECEIPT, echo_bounce_n=99)
        with self.assertRaises(bridge_http.BridgeTransportError):
            bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q,
                                     opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(len(self._exec_legs(host)), bridge_http.READ_TRIES,
                         "повтор ограничен: вечного цикла в витке демона быть не должно")

    # ── (б) НОРМАЛЬНОЕ чтение работает как прежде ───────────────────────────────────────────
    def test_happy_read_still_costs_exactly_two_legs(self):
        host = FakeBridgeHost(receipt=READ_RECEIPT)
        data = bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q,
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(data.get("text"), READ_RECEIPT["text"])
        self.assertEqual([m for m, _, _ in host.legs], ["GET", "GET"],
                         "на исправном мосту повтор не смеет добавить ни одного запроса")

    def test_a_refusal_on_the_merits_is_not_retried(self):
        """`ok:false` от самого моста — ОТВЕТ, а не сбой плеча: переспрашивать его нельзя."""
        host = FakeBridgeHost(receipt=DOGET_REFUSAL)
        data = bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q,
                                        opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(data.get("error"), "unauthorized")
        self.assertEqual(len(self._exec_legs(host)), 1)

    def test_a_dead_first_leg_reaches_the_caller_at_once(self):
        """«Связи нет» обязано доходить немедленно: повтор первого плеча — не наше дело, и
        `error_kind` не смеет узнать об обрыве на 3 паузы позже."""
        class DeadFirstLeg(FakeBridgeHost):
            def https_open(self, req):
                self.legs.append((req.get_method(), req.full_url.split("?")[0], bool(req.data)))
                raise urllib.error.URLError("[Errno 11001] getaddrinfo failed")
        host = DeadFirstLeg()
        with self.assertRaises(urllib.error.URLError):
            bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q,
                                     opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(len(host.legs), 1)

    def test_tries_is_an_injection_point_not_a_hidden_default(self):
        host = FakeBridgeHost(receipt=READ_RECEIPT, echo_bounce_n=99)
        with self.assertRaises(bridge_http.BridgeTransportError):
            bridge_http.request_json(EXEC_URL, "GET", params=self.READ_Q, tries=1,
                                     opener=_opener(host), sleeper=lambda s: None)
        self.assertEqual(len(self._exec_legs(host)), 1)

    # ── граница: у ЗАПИСИ повтора целиком нет и не появилось ────────────────────────────────
    def test_a_write_is_never_repeated_whole_no_matter_the_leg(self):
        """Инвариант `ab5106d`: мутация уходит РОВНО один раз. Повтор чтения не смеет его сдвинуть."""
        for kwargs in ({"echo_bounces": True}, {"echo_bounce_n": 99}, {"echo_404": 99}):
            host = FakeBridgeHost(**kwargs)
            with self.assertRaises(Exception):
                bridge_http.request_json(EXEC_URL, "POST", payload=_post_body(),
                                         opener=_opener(host), sleeper=lambda s: None)
            self.assertEqual(len(host.posts()), 1, kwargs)
            self.assertEqual(len(self._exec_legs(host)), 1, kwargs)


if __name__ == "__main__":
    unittest.main()
