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

    def __init__(self, receipt=None, echo_404=0, echo_bounces=False, exec_get=None):
        super().__init__()
        self.receipt = receipt if receipt is not None else WRITE_RECEIPT
        self.echo_404 = echo_404
        self.echo_bounces = echo_bounces
        self.exec_get = exec_get if exec_get is not None else DOGET_REFUSAL
        self.legs = []          # (метод, адрес без query, было ли тело)

    def https_open(self, req):
        url = req.full_url
        method = req.get_method()
        self.legs.append((method, url.split("?")[0], bool(req.data)))
        if url.split("?")[0] == EXEC_URL:
            if method == "POST":
                return _response(url, 302,
                                 {"Location": ECHO_URL,
                                  "Content-Type": "text/html; charset=UTF-8",
                                  "Server": "GSE",
                                  "Cache-Control": "no-cache, no-store, max-age=0, must-revalidate"})
            # GET на /exec: с токеном в query — читатель работает; голый (редирект съел query) —
            # дословный отказ doGet, тот самый, что пришёл на ЗАПИСЬ 02.08.
            if "token=" in url:
                return _response(url, 302,
                                 {"Location": ECHO_URL, "Content-Type": "text/html; charset=UTF-8",
                                  "Server": "GSE"})
            return _response(url, 200, {"Content-Type": "application/json"},
                             json.dumps(self.exec_get).encode("utf-8"))
        if url.split("?")[0].startswith("https://echo.bridge.test/"):
            if self.echo_404 > 0:
                self.echo_404 -= 1
                raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
            if self.echo_bounces:
                return _response(url, 302, {"Location": EXEC_URL,
                                            "Content-Type": "text/html; charset=UTF-8"})
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
        self.assertFalse(cla.transient(bridge_http.BridgeTransportError("проба")))


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


if __name__ == "__main__":
    unittest.main()
