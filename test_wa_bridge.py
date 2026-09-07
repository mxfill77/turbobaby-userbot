# -*- coding: utf-8 -*-
"""САМОТЕСТЫ ВХОДЯЩЕГО МОСТА WhatsApp (WA-2b, 07.09.2026).

Всё на МОКАХ: ни одного живого HTTP, ни одного вызова модели, ни одной боевой БД (moderation_ipc
уводится на временный файл — тот же приём, что в test_moderation, и его тривайр изоляции сам
свалит тест при промахе мимо tmp). Клиенту не уходит ничего ни в одной ветке: отправки в модуле
нет вовсе, а замок канала в suggest проверяется отдельно.

Формат записей забора — ЖИВОЙ формат прода: ключи ровно те, что отдаёт `WAQueue.pull` на VPS
(id, from, name, type, text, ts, source, msg_type, echo, history, kind, disposition), правка
сервера от 07.09.2026. Правило-класс CLAUDE.md: мок обязан повторять живой формат, иначе он
зеленеет молча.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import moderation_ipc
import suggest
import wa_bridge


def rec(rid, kind, text="привет", frm="+66812345678", name="Иван", echo=0, history=0,
        msg_type="text", **extra):
    """Запись очереди в ЖИВОМ формате двери pull (VPS wa_webhook.WAQueue.pull, 07.09.2026)."""
    r = {"id": rid, "from": frm, "name": name, "type": msg_type, "text": text,
         "ts": 1757000000 + rid, "source": "d360", "msg_type": msg_type,
         "echo": echo, "history": history, "kind": kind,
         "disposition": {"inbound": "card", "echo": "context", "history": "context",
                         "receipt": "count", "unknown": "review"}.get(kind, "review")}
    r.update(extra)
    return r


class WABridgeCase(unittest.TestCase):
    """Общая обвязка: своя БД в tmp, свой «черновик» вместо модели, свой транспорт вместо сети."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_db = moderation_ipc.DB_PATH
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        # секрет тестовый и заведомо не боевой: без него мост не поднимается вовсе (is_enabled),
        # и ветки забора/ack просто не выполнялись бы. Наружу он не уходит — транспорт замокан.
        self._old_secret = wa_bridge.WA_PULL_SECRET
        wa_bridge.WA_PULL_SECRET = "тестовый-секрет-не-боевой"
        self.drafted = []          # какие транскрипты дошли до клиентского пути
        self.acked = []            # какие id ушли в ack
        self.pulled_urls = []

    def tearDown(self):
        wa_bridge.WA_PULL_SECRET = self._old_secret
        moderation_ipc.DB_PATH = self._old_db
        self._tmp.cleanup()

    # --- моки ---
    def drafter(self, draft="Здравствуйте! Подскажите даты аренды."):
        def _d(transcript, client_id=None, client_ref=None, client_name=None, first=False):
            self.drafted.append({"transcript": transcript, "client_id": client_id,
                                 "client_ref": client_ref, "client_name": client_name,
                                 "first": first})
            if draft is None:
                return None
            return {"client_id": client_id, "client_ref": client_ref, "lang": "ru",
                    "incoming": transcript.rsplit("\n", 1)[-1], "draft": draft,
                    "first_contact": first, "transcript": transcript,
                    "pricing_note": "", "client_name": client_name}
        return _d

    def transport(self, items, ack_ok=True):
        def _t(method, url, payload=None, timeout=None):
            self.pulled_urls.append((method, url))
            if method == "GET":
                return {"ok": True, "count": len(items), "items": items}
            self.acked.append(list((payload or {}).get("ids") or []))
            return {"ok": True, "acked": len(payload.get("ids", []))} if ack_ok else {"ok": False,
                                                                                     "error": "boom"}
        return _t

    def cards(self):
        return moderation_ipc.fetch_new()


class TestKindField(WABridgeCase):
    """Вид записи берётся ПОЛЕМ сервера, а незнакомое никогда не становится клиентским."""

    def test_five_kinds_read_from_field(self):
        for k in ("inbound", "echo", "history", "receipt"):
            self.assertEqual(wa_bridge.kind_of(rec(1, k)), k)
        self.assertEqual(wa_bridge.kind_of(rec(1, "reaction")), "unknown")

    def test_missing_kind_is_unknown_not_inbound(self):
        # СТРАХОВКА ОТ СТАРОГО СЕРВЕРА: поля различителя нет вовсе → unknown, а не «наверное клиент»
        old = rec(7, "inbound")
        old.pop("kind")
        self.assertEqual(wa_bridge.kind_of(old), "unknown")

    def test_kind_case_and_garbage(self):
        self.assertEqual(wa_bridge.kind_of({"kind": "INBOUND"}), "inbound")
        self.assertEqual(wa_bridge.kind_of({"kind": None}), "unknown")
        self.assertEqual(wa_bridge.kind_of({}), "unknown")
        self.assertEqual(wa_bridge.kind_of(None), "unknown")


class TestInbound(WABridgeCase):
    """Входящее клиента → контекст + карточка модерации с меткой WA."""

    def test_inbound_makes_card_with_wa_label(self):
        counters, ack_ids = wa_bridge.process_batch(
            [rec(11, "inbound", "Здравствуйте! Nmax на 5 дней?")],
            drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(counters["inbound"], 1)
        self.assertEqual(ack_ids, [11])
        rows = self.cards()
        self.assertEqual(len(rows), 1)
        # заголовок карточки модербота — «✏️ Черновик клиенту {client_ref}»
        self.assertEqual(rows[0]["client_ref"], "WA · +66812345678 · Иван")
        head = "✏️ Черновик клиенту " + rows[0]["client_ref"]
        self.assertEqual(head, "✏️ Черновик клиенту WA · +66812345678 · Иван")
        self.assertTrue(suggest.is_wa_client_ref(rows[0]["client_ref"]))

    def test_inbound_goes_through_the_telegram_client_path(self):
        # путь ОДИН: черновик собирается тем же телом, и ему приходит транскрипт того же формата
        wa_bridge.process_batch([rec(12, "inbound", "Сколько стоит ADV 350?")],
                                drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(len(self.drafted), 1)
        self.assertEqual(self.drafted[0]["transcript"], "[клиент]: Сколько стоит ADV 350?")
        self.assertTrue(self.drafted[0]["first"])          # менеджер в окне ещё не говорил
        self.assertEqual(self.drafted[0]["client_id"], -66812345678)
        self.assertEqual(self.drafted[0]["client_name"], "Иван")

    def test_no_draft_means_no_card_and_no_ack(self):
        # клиентский путь отказал (парк не получен / пустой вывод) — карточки нет, id не подтверждён
        counters, ack_ids = wa_bridge.process_batch(
            [rec(13, "inbound")], drafter=self.drafter(draft=None), path=moderation_ipc.DB_PATH)
        self.assertEqual(counters["no_draft"], 1)
        self.assertEqual(self.cards(), [])
        self.assertEqual(ack_ids, [13])                    # решение принято: круг не крутим

    def test_second_delivery_of_same_id_does_not_double_context(self):
        # сервер выдаёт строку в АРЕНДУ и при потере ack выдаст снова — дубля быть не должно
        r = rec(14, "inbound", "Привет")
        for _ in range(2):
            wa_bridge.process_batch([r], drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(wa_bridge.context_transcript("+66812345678",
                                                      path=moderation_ipc.DB_PATH),
                         "[клиент]: Привет")


class TestEcho(WABridgeCase):
    """Эхо (ручной ответ менеджера с телефона) → только контекст, черновик НЕ порождаем."""

    def test_echo_no_card_context_grows(self):
        counters, ack_ids = wa_bridge.process_batch(
            [rec(21, "echo", "Добрый день! Сейчас посчитаю.", echo=1)],
            drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(counters["echo"], 1)
        self.assertEqual(self.cards(), [])                 # карточки нет
        self.assertEqual(self.drafted, [])                 # модель не звалась вовсе
        self.assertEqual(ack_ids, [21])
        self.assertEqual(wa_bridge.context_transcript("+66812345678", path=moderation_ipc.DB_PATH),
                         "[менеджер]: Добрый день! Сейчас посчитаю.")

    def test_echo_then_inbound_is_not_first_contact(self):
        wa_bridge.process_batch([rec(22, "echo", "Здравствуйте!", echo=1),
                                 rec(23, "inbound", "А депозит какой?")],
                                drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(len(self.drafted), 1)
        self.assertFalse(self.drafted[0]["first"])         # менеджер в окне уже говорил
        self.assertEqual(self.drafted[0]["transcript"],
                         "[менеджер]: Здравствуйте!\n[клиент]: А депозит какой?")


class TestHistory(WABridgeCase):
    """История (досинхрон) → контекст пачкой, карточек ноль."""

    def test_history_batch_fills_context_without_cards(self):
        batch = [rec(31, "history", "Здравствуйте, нужен байк", history=1),
                 rec(32, "history", "Да, конечно — на какие даты?", history=1, echo=1),
                 rec(33, "history", "С 10 по 15", history=1)]
        counters, ack_ids = wa_bridge.process_batch(batch, drafter=self.drafter(),
                                                   path=moderation_ipc.DB_PATH)
        self.assertEqual(counters["history"], 3)
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.drafted, [])
        self.assertEqual(ack_ids, [31, 32, 33])
        self.assertEqual(wa_bridge.context_transcript("+66812345678", path=moderation_ipc.DB_PATH),
                         "[клиент]: Здравствуйте, нужен байк\n"
                         "[менеджер]: Да, конечно — на какие даты?\n"
                         "[клиент]: С 10 по 15")

    def test_history_then_inbound_uses_whole_context(self):
        wa_bridge.process_batch([rec(34, "history", "Нужен NMAX", history=1),
                                 rec(35, "inbound", "Ну так что?")],
                                drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(len(self.cards()), 1)
        self.assertEqual(self.drafted[0]["transcript"],
                         "[клиент]: Нужен NMAX\n[клиент]: Ну так что?")


class TestReceiptAndUnknown(WABridgeCase):
    """Квитанция — только счётчик; неопознанное — отказ, клиентским сообщением НЕ считаем."""

    def test_receipt_is_counter_only(self):
        counters, ack_ids = wa_bridge.process_batch(
            [rec(41, "receipt", "delivered", msg_type="status")],
            drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(counters["receipt"], 1)
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.drafted, [])
        self.assertEqual(wa_bridge.load_context("+66812345678", path=moderation_ipc.DB_PATH), [])
        self.assertEqual(ack_ids, [41])

    def test_receipt_word_delivered_from_client_is_not_a_receipt(self):
        # судим ПО ПОЛЮ, а не по тексту: клиент, написавший «delivered», остаётся клиентом
        wa_bridge.process_batch([rec(42, "inbound", "delivered")],
                                drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(len(self.cards()), 1)

    def test_unknown_is_refused_not_client(self):
        counters, ack_ids = wa_bridge.process_batch(
            [rec(43, "reaction", "👍")], drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(counters["unknown"], 1)
        self.assertEqual(counters["inbound"], 0)
        self.assertEqual(self.cards(), [])
        self.assertEqual(self.drafted, [])
        self.assertEqual(wa_bridge.load_context("+66812345678", path=moderation_ipc.DB_PATH), [])
        self.assertEqual(ack_ids, [43])


class TestAckDiscipline(WABridgeCase):
    """ack — только после успеха, и ни одного вызова, если подтверждать нечего."""

    def test_ack_not_sent_when_processing_failed(self):
        def boom(*a, **kw):
            raise RuntimeError("модель упала")
        res = wa_bridge.poll_once(transport=self.transport([rec(51, "inbound")]),
                                  drafter=boom, path=moderation_ipc.DB_PATH)
        self.assertEqual(res["counters"]["failed"], 1)
        self.assertEqual(res["ack_ids"], [])
        self.assertEqual(self.acked, [])                   # POST /ack не звался ВООБЩЕ
        self.assertEqual([m for m, _ in self.pulled_urls], ["GET"])

    def test_ack_only_the_successful_ids(self):
        calls = {"n": 0}

        def flaky(transcript, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("первая упала")
            return self.drafter()(transcript, **kw)
        res = wa_bridge.poll_once(
            transport=self.transport([rec(52, "inbound"), rec(53, "inbound", frm="+66899999999")]),
            drafter=flaky, path=moderation_ipc.DB_PATH)
        self.assertEqual(res["ack_ids"], [53])
        self.assertEqual(self.acked, [[53]])
        self.assertEqual(res["counters"]["failed"], 1)

    def test_ack_sent_after_success(self):
        res = wa_bridge.poll_once(transport=self.transport([rec(54, "echo", echo=1)]),
                                  drafter=self.drafter(), path=moderation_ipc.DB_PATH)
        self.assertEqual(self.acked, [[54]])
        self.assertEqual(res["acked"], 1)

    def test_giving_up_after_max_tries_is_loud_not_silent(self):
        def boom(*a, **kw):
            raise RuntimeError("не чинится")
        last = None
        for _ in range(3):
            last = wa_bridge.process_batch([rec(55, "inbound")], drafter=boom,
                                           path=moderation_ipc.DB_PATH, max_tries=3)
        self.assertEqual(last[1], [55])                    # на третьем заходе подтверждаем
        self.assertEqual(last[0]["gave_up"], 1)

    def test_empty_pull_makes_no_ack_call(self):
        res = wa_bridge.poll_once(transport=self.transport([]), drafter=self.drafter(),
                                  path=moderation_ipc.DB_PATH)
        self.assertTrue(res["ok"])
        self.assertEqual(self.acked, [])


class TestPullContract(WABridgeCase):
    """Забор: адрес, лимит, тихий отказ и НИ ОДНОГО секрета в тексте наружу."""

    def setUp(self):
        super().setUp()
        self._old_secret = wa_bridge.WA_PULL_SECRET
        wa_bridge.WA_PULL_SECRET = "s3cr3t-путь"

    def tearDown(self):
        wa_bridge.WA_PULL_SECRET = self._old_secret
        super().tearDown()

    def test_pull_url_and_limit(self):
        many = [rec(100 + i, "receipt") for i in range(30)]
        ok, items, err = wa_bridge.pull(transport=self.transport(many))
        self.assertTrue(ok)
        self.assertEqual(len(items), 20)                   # не больше 20 записей за заход
        self.assertEqual(self.pulled_urls[0][1],
                         "https://wa.turbophuket.com/wa-queue/pull/s3cr3t-путь")

    def test_ack_url_carries_secret_in_path(self):
        # живой контракт двери: POST /wa-queue/ack/<секрет>, тело {"ids": [...]}
        wa_bridge.ack([1, 2], transport=self.transport([]))
        self.assertEqual(self.pulled_urls[-1],
                         ("POST", "https://wa.turbophuket.com/wa-queue/ack/s3cr3t-путь"))
        self.assertEqual(self.acked, [[1, 2]])

    def test_network_failure_is_quiet_and_secret_free(self):
        def dead(*a, **kw):
            raise OSError("connect to https://wa.turbophuket.com/wa-queue/pull/s3cr3t-путь failed")
        ok, items, err = wa_bridge.pull(transport=dead)
        self.assertFalse(ok)
        self.assertEqual(items, [])
        self.assertNotIn("s3cr3t-путь", err)               # секрет наружу не течёт ни строкой
        self.assertIn("<secret>", err)

    def test_backoff_grows_and_does_not_spam(self):
        def dead(*a, **kw):
            raise OSError("сеть легла")
        slept = []
        wa_bridge.run_forever(transport=dead, drafter=self.drafter(),
                              path=moderation_ipc.DB_PATH, sleep=slept.append, rounds=4)
        self.assertEqual(len(slept), 3)
        self.assertTrue(slept[0] < slept[-1], slept)       # бэкофф растёт
        self.assertLessEqual(slept[-1], wa_bridge.BACKOFF_MAX)

    def test_bridge_off_without_secret(self):
        wa_bridge.WA_PULL_SECRET = ""
        self.assertFalse(wa_bridge.is_enabled())
        ok, items, err = wa_bridge.pull(transport=self.transport([rec(1, "inbound")]))
        self.assertFalse(ok)
        self.assertEqual(self.pulled_urls, [])             # без секрета — ни одного вызова наружу


class TestSendStub(WABridgeCase):
    """Отправка — заглушка; одобренный WA-черновик не уезжает Telegram-каналом."""

    def test_stub_says_the_exact_line(self):
        ok, reason = wa_bridge.send_to_wa("+66812345678", "текст")
        self.assertFalse(ok)
        self.assertEqual(reason, "WA-отправка отключена (ключ не задан)")
        self.assertEqual(reason, suggest.WA_SEND_DISABLED_MSG)

    def test_wa_ref_is_recognised(self):
        self.assertTrue(suggest.is_wa_client_ref("WA · +66812345678 · Иван"))
        self.assertFalse(suggest.is_wa_client_ref("@marcoit41"))
        self.assertFalse(suggest.is_wa_client_ref("id504608015"))
        self.assertFalse(suggest.is_wa_client_ref(None))

    def test_approved_wa_draft_is_not_sent_to_telegram(self):
        import asyncio
        did = moderation_ipc.enqueue_draft({
            "client_id": -66812345678, "client_ref": "WA · +66812345678 · Иван", "lang": "ru",
            "incoming": "цена?", "draft": "черновик", "first_contact": True})
        moderation_ipc.set_decision(did, "ready", final_text="ответ клиенту", decided_by="Филипп")
        sent = []

        async def sender(client, cid, text, **kw):
            sent.append((cid, text))
            return True, "ok"
        old_mode, old_token = suggest.SUGGEST_MODE, suggest.MODERBOT_TOKEN
        suggest.SUGGEST_MODE = "on"
        try:
            asyncio.run(suggest.poll_and_send(client=None, sender=sender))
        finally:
            suggest.SUGGEST_MODE, suggest.MODERBOT_TOKEN = old_mode, old_token
        self.assertEqual(sent, [])                         # клиенту НЕ ушло ничего
        row = moderation_ipc.get(did)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["reason"], "WA-отправка отключена (ключ не задан)")


class TestContextStore(WABridgeCase):
    """Контекст по номеру — как по @username в Telegram: тот же формат, своя адресация."""

    def test_number_normalised_and_id_negative(self):
        self.assertEqual(wa_bridge.number_of({"from": "66 81 234-5678"}), "+66812345678")
        self.assertEqual(wa_bridge.number_of({"from": ""}), "")
        self.assertEqual(wa_bridge.client_id_of("+66812345678"), -66812345678)

    def test_ref_without_name(self):
        self.assertEqual(wa_bridge.client_ref("+66812345678"), "WA · +66812345678")

    def test_transcript_format_matches_telegram(self):
        wa_bridge.append_context("+66811111111", "клиент", "Привет", rec_id=1,
                                 path=moderation_ipc.DB_PATH)
        wa_bridge.append_context("+66811111111", "менеджер", "Здравствуйте", rec_id=2,
                                 path=moderation_ipc.DB_PATH)
        got = wa_bridge.context_transcript("+66811111111", path=moderation_ipc.DB_PATH)
        self.assertEqual(got, "[клиент]: Привет\n[менеджер]: Здравствуйте")
        # тот же разбор, которым живёт клиентский путь Telegram
        self.assertEqual(suggest.last_client_message(got), "Привет")

    def test_two_numbers_do_not_mix(self):
        wa_bridge.append_context("+66811111111", "клиент", "А", rec_id=1, path=moderation_ipc.DB_PATH)
        wa_bridge.append_context("+66822222222", "клиент", "Б", rec_id=2, path=moderation_ipc.DB_PATH)
        self.assertEqual(wa_bridge.context_transcript("+66811111111", path=moderation_ipc.DB_PATH),
                         "[клиент]: А")
        self.assertEqual(wa_bridge.context_transcript("+66822222222", path=moderation_ipc.DB_PATH),
                         "[клиент]: Б")

    def test_media_without_text_is_marked(self):
        wa_bridge.append_context("+66833333333", "клиент", "", rec_id=1, path=moderation_ipc.DB_PATH)
        self.assertEqual(wa_bridge.context_transcript("+66833333333", path=moderation_ipc.DB_PATH),
                         "[клиент]: [без текста / медиа]")

    def test_broken_store_degrades_to_empty(self):
        moderation_ipc.set_meta(wa_bridge.ctx_key("+66844444444"), "{не json",
                                path=moderation_ipc.DB_PATH)
        self.assertEqual(wa_bridge.load_context("+66844444444", path=moderation_ipc.DB_PATH), [])


class TestMixedBatch(WABridgeCase):
    """Пачка всех пяти видов разом: карточка ровно одна, счётчики честные."""

    def test_five_kinds_in_one_pull(self):
        batch = [rec(61, "history", "старое", history=1),
                 rec(62, "echo", "наш ответ", echo=1),
                 rec(63, "receipt", "read", msg_type="status"),
                 rec(64, "reaction", "👍"),
                 rec(65, "inbound", "Сколько за неделю?")]
        res = wa_bridge.poll_once(transport=self.transport(batch), drafter=self.drafter(),
                                  path=moderation_ipc.DB_PATH)
        self.assertEqual(res["counters"]["history"], 1)
        self.assertEqual(res["counters"]["echo"], 1)
        self.assertEqual(res["counters"]["receipt"], 1)
        self.assertEqual(res["counters"]["unknown"], 1)
        self.assertEqual(res["counters"]["inbound"], 1)
        self.assertEqual(len(self.cards()), 1)             # карточка ТОЛЬКО на входящее
        self.assertEqual(self.acked, [[61, 62, 63, 64, 65]])
        self.assertEqual(self.drafted[0]["transcript"],
                         "[клиент]: старое\n[менеджер]: наш ответ\n[клиент]: Сколько за неделю?")


class TestNoThai(WABridgeCase):
    """Анти-тайский: в текстах модуля (лог/заглушка/адрес) тайских букв нет."""

    THAI = tuple(range(0x0E01, 0x0E5C))

    def _has_thai(self, s):
        return any(ord(ch) in self.THAI for ch in str(s or ""))

    def test_module_texts_have_no_thai(self):
        for s in (wa_bridge.SEND_DISABLED_MSG, suggest.WA_SEND_DISABLED_MSG,
                  suggest.WA_CLIENT_REF_PREFIX, wa_bridge.client_ref("+66812345678", "Иван"),
                  wa_bridge.ctx_key("+66812345678")):
            self.assertFalse(self._has_thai(s), s)

    def test_source_has_no_thai_letters(self):
        with open(wa_bridge.__file__, encoding="utf-8") as f:
            self.assertFalse(self._has_thai(f.read()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
