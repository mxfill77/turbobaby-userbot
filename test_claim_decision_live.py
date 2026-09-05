# -*- coding: utf-8 -*-
"""
test_claim_decision_live.py — ЗАМКИ ЖИВОГО ПУТИ РЕШЕНИЯ (06.09.2026).

Почему отдельный набор, а не строки в `test_moderation_card`. Тот набор меряет `moderation_card`
— модуль, который ЖИВОЙ бот НЕ ИМПОРТИРУЕТ ни одной строкой. Он был зелёным, а клиент всё равно
мог получить второе сообщение: атомарный захват стоял в стороне от боевой дороги, а боевая
дорога (`moderation_bot.on_callback` → `_apply`) ставила решение `set_decision`'ом — UPDATE без
предусловия по статусу, из ЛЮБОГО состояния строки, включая уже отправленное. Здесь меряется
ИМЕННО живая дорога: вход — нажатие кнопки в `moderation_bot`, выход — живой отправитель
`suggest.poll_and_send`. Никакого Telegram и никакого Anthropic; клиенту не уходит ничего,
кроме как в фейковый список.

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА, названные заданием (без них правка не годится):
  (а) двое подтверждают ОДНОВРЕМЕННО → РОВНО одно решение и РОВНО одно сообщение — TestTwoAtOnce;
  (б) повторное подтверждение по УЖЕ ОТПРАВЛЕННОЙ карточке → второго сообщения нет — TestPressAfterDelivery;
  (в) отказ захвата НЕ роняет бота и НЕ оставляет карточку подвешенной — TestRefusalIsGraceful.

Четвёртая опора — предсмертный взгляд задания: захват сделали атомарным, а дедуп у отправителя
забыли, и дубль переехал на ступень дальше. Поэтому отправитель меряется ОТДЕЛЬНО (TestSenderDedup):
строка, снова ставшая 'ready' В ОБХОД кнопки, второго сообщения клиенту не даёт.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_claim_decision_live -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import ast
import asyncio
import io
import os
import tempfile
import threading
import unittest

import moderation_bot as mb
import moderation_core
import moderation_ipc
import suggest

REPO = os.path.dirname(os.path.abspath(__file__))
CLIENT_ID = 424242
CHAT_ID = -5031790861


async def _nosleep(_):
    return None


# ------------------------------- фейки Telegram -------------------------------

class FakeBot:
    """Всё, что бот сказал В ГРУППУ МОДЕРАЦИИ (не клиенту), оседает здесь."""

    def __init__(self):
        self.said = []

    async def send_message(self, chat, text, **kw):
        self.said.append((chat, text))

        class M:
            message_id = 900 + len(self.said)
        return M()


class FakeCtx:
    def __init__(self, bot):
        self.bot = bot


class FakeUser:
    def __init__(self, username):
        self.username = username


class FakeMessage:
    def __init__(self, chat_id, message_id):
        self.chat_id = chat_id
        self.message_id = message_id


class FakeQuery:
    """Нажатие кнопки. Считаем и ответ Telegram'у, и снятие клавиатуры с карточки."""

    def __init__(self, data, username, chat_id=CHAT_ID, message_id=5001):
        self.data = data
        self.from_user = FakeUser(username)
        self.message = FakeMessage(chat_id, message_id)
        self.answers = 0
        self.markups = []

    async def answer(self):
        self.answers += 1

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markups.append(reply_markup)


class FakeUpdate:
    def __init__(self, query):
        self.callback_query = query


class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self):
        self.sent = []

    def action(self, chat, kind):
        return FakeAction()

    async def send_message(self, chat, text, reply_to=None):
        self.sent.append((chat, text))


# ------------------------------- общая обвязка --------------------------------

class LiveBase(unittest.TestCase):
    """Очередь — своя временная копия схемы. БОЕВАЯ база модерации не открывается ВОВСЕ:
    тривайр `moderation_ipc._conn` считает попытки, и норма прогона — ноль (проверяем)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._save = (moderation_ipc.DB_PATH, suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
                      suggest.APPROVER_USERNAMES, moderation_ipc.prod_ipc_open_attempts)
        moderation_ipc.DB_PATH = os.path.join(self._tmp.name, "ipc.db")
        moderation_ipc.init_db()
        suggest.SUGGEST_MODE = True
        suggest.SUGGEST_TEST_MODE = False
        suggest.APPROVER_USERNAMES = set()     # пустой whitelist = решать может любой approver
        suggest.reset_disabled()

    def tearDown(self):
        self.assertEqual(moderation_ipc.prod_ipc_open_attempts, self._save[4],
                         "тест полез в БОЕВОЙ moderation_ipc.db")
        (moderation_ipc.DB_PATH, suggest.SUGGEST_MODE, suggest.SUGGEST_TEST_MODE,
         suggest.APPROVER_USERNAMES, _) = self._save
        suggest.reset_disabled()
        self._tmp.cleanup()

    def card(self, draft="Здравствуйте! NMAX свободен, 590฿/сутки."):
        """Строка очереди в том же виде, в каком её оставляет боевой путь: черновик положен
        userbot'ом, карточка запощена ботом (status='posted', card_msg_id заполнен)."""
        did = moderation_ipc.enqueue_draft({
            "client_id": CLIENT_ID, "client_ref": "@testclient", "lang": "ru",
            "incoming": "сколько стоит nmax?", "draft": draft, "first_contact": True})
        moderation_ipc.mark_posted(did, 5000 + did)
        return did

    def press(self, draft_id, who, action="yes", bot=None, message_id=5001):
        """ЖИВОЕ нажатие кнопки: ровно тот вход, что у боевого модербота."""
        bot = bot if bot is not None else FakeBot()
        q = FakeQuery(f"m:{draft_id}:{action}", who, message_id=message_id)
        asyncio.run(mb.on_callback(FakeUpdate(q), FakeCtx(bot)))
        return q, bot

    def sent_by_live_executor(self):
        """Сколько сообщений УШЛО БЫ клиенту прямо сейчас — живым отправителем userbot."""
        got = []

        async def fake_send(_client, cid, text, sleep=None, jitter=None):
            got.append((cid, text))
            return True, None

        asyncio.run(suggest.poll_and_send(FakeClient(), sender=fake_send, sleep=_nosleep,
                                          jitter=lambda: 0))
        return got


# ================== (а) ДВОЕ ПОДТВЕРЖДАЮТ ОДНОВРЕМЕННО ========================

class TestTwoAtOnce(LiveBase):
    """Главный отрицательный тест задания: два модератора на ОДНОЙ карточке нажимают ✅ в один
    момент. Решение обязано быть РОВНО одно, сообщение клиенту — РОВНО одно."""

    def test_two_simultaneous_presses_give_one_decision_and_one_message(self):
        did = self.card()
        barrier = threading.Barrier(2)
        said, errors = [], []
        lock = threading.Lock()

        def press(who):
            bot = FakeBot()
            q = FakeQuery(f"m:{did}:yes", who)
            barrier.wait()
            try:
                asyncio.run(mb.on_callback(FakeUpdate(q), FakeCtx(bot)))
            except Exception as e:              # noqa: BLE001 — гонка обязана быть БЕЗ сбоев
                with lock:
                    errors.append(f"{type(e).__name__}: {e}")
                return
            with lock:
                said.extend(t for _c, t in bot.said)

        threads = [threading.Thread(target=press, args=(w,)) for w in ("danya", "dasha")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], "гонка дала сбой вместо честного отказа")
        self.assertEqual(len(said), 2, said)                      # оба человека получили ответ
        winners = [t for t in said if "Принято" in t]
        losers = [t for t in said if "уже закрыта" in t]
        self.assertEqual(len(winners), 1, said)                   # решение РОВНО одно
        self.assertEqual(len(losers), 1, said)                    # второму сказано, что опоздал
        self.assertEqual(len(moderation_ipc.fetch_ready()), 1)
        row = moderation_ipc.get(did)
        self.assertEqual(row["status"], "ready")
        self.assertIn(row["decided_by"], ("@danya", "@dasha"))
        self.assertIn(row["decided_by"], losers[0])               # видно, КЕМ закрыта
        sent = self.sent_by_live_executor()
        self.assertEqual(len(sent), 1, sent)                      # клиенту РОВНО одно
        self.assertEqual(sent[0][0], CLIENT_ID)

    def test_eight_simultaneous_presses_still_send_exactly_one(self):
        """Тот же замок под настоящей нагрузкой: восемь потоков, синхронный старт по барьеру."""
        did = self.card()
        n = 8
        barrier = threading.Barrier(n)
        said, errors = [], []
        lock = threading.Lock()

        def press(who):
            bot = FakeBot()
            q = FakeQuery(f"m:{did}:yes", who)
            barrier.wait()
            try:
                asyncio.run(mb.on_callback(FakeUpdate(q), FakeCtx(bot)))
            except Exception as e:              # noqa: BLE001
                with lock:
                    errors.append(f"{type(e).__name__}: {e}")
                return
            with lock:
                said.extend(t for _c, t in bot.said)

        threads = [threading.Thread(target=press, args=(f"mod{i}",)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], "гонка дала сбой вместо честного отказа")
        self.assertEqual(len([t for t in said if "Принято" in t]), 1, said)
        self.assertEqual(len([t for t in said if "уже закрыта" in t]), n - 1, said)
        self.assertEqual(len(self.sent_by_live_executor()), 1)

    def test_reject_racing_approve_gives_one_outcome(self):
        """Одновременные ❌ и ✅ — тоже гонка: исход обязан быть один, а не «и отклонено, и ушло»."""
        did = self.card()
        barrier = threading.Barrier(2)
        said = []
        lock = threading.Lock()

        def press(who, action):
            bot = FakeBot()
            q = FakeQuery(f"m:{did}:{action}", who)
            barrier.wait()
            asyncio.run(mb.on_callback(FakeUpdate(q), FakeCtx(bot)))
            with lock:
                said.extend(t for _c, t in bot.said)

        threads = [threading.Thread(target=press, args=a)
                   for a in (("danya", "yes"), ("dasha", "no"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len([t for t in said if "уже закрыта" in t]), 1, said)
        self.assertIn(moderation_ipc.get(did)["status"], ("ready", "rejected"))
        self.assertLessEqual(len(self.sent_by_live_executor()), 1)


# ============ (б) ПОВТОРНОЕ ПОДТВЕРЖДЕНИЕ ПО УЖЕ ОТПРАВЛЕННОЙ КАРТОЧКЕ ========

class TestPressAfterDelivery(LiveBase):
    """Дорога, которой второе сообщение уходило ЖИВЬЁМ: кнопки после решения не гасли, а
    `set_decision` предусловия не имел — повторный тап снова ставил 'ready', и отправитель
    (fetch_ready → send → mark, без дедупа) слал ВТОРОЕ."""

    def test_second_press_after_delivery_sends_nothing(self):
        did = self.card()
        self.press(did, "danya")
        self.assertEqual(len(self.sent_by_live_executor()), 1)
        self.assertEqual(moderation_ipc.get(did)["status"], "sent")

        q, bot = self.press(did, "danya")                 # тот же человек жмёт ЕЩЁ раз
        texts = [t for _c, t in bot.said]
        self.assertEqual(len(texts), 1, texts)
        self.assertIn("уже закрыта", texts[0])
        self.assertIn("уже отправлено клиенту", texts[0])
        self.assertNotIn("Принято", texts[0])             # обычного «Принято» опоздавший не видит
        self.assertEqual(moderation_ipc.fetch_ready(), [])
        self.assertEqual(self.sent_by_live_executor(), [])   # ВТОРОГО сообщения нет

    def test_week_old_card_pressed_by_second_moderator_sends_nothing(self):
        """Карточка недельной давности: первый решил давно, второй нашёл её в истории и нажал."""
        did = self.card()
        self.press(did, "danya")
        self.sent_by_live_executor()
        _q, bot = self.press(did, "dasha")
        self.assertIn("уже закрыта", " ".join(t for _c, t in bot.said))
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_buttons_are_removed_from_a_closed_card(self):
        """Кнопки с закрытой карточки снимаются — и у победителя, и у опоздавшего."""
        did = self.card()
        q1, _b1 = self.press(did, "danya")
        self.assertEqual(q1.markups, [None], "кнопки решённой карточки остались на месте")
        q2, _b2 = self.press(did, "dasha")
        self.assertEqual(q2.markups, [None], "кнопки закрытой карточки остались у опоздавшего")


# ============ (в) ОТКАЗ ЗАХВАТА НЕ РОНЯЕТ БОТА И НЕ ВЕШАЕТ КАРТОЧКУ ===========

class TestRefusalIsGraceful(LiveBase):
    """Отказ захвата — ШТАТНЫЙ исход, а не авария: обработчик доходит до конца, человек получает
    внятный текст, строка очереди остаётся в определённом состоянии."""

    def test_refusal_does_not_raise_and_does_not_hang_the_card(self):
        did = self.card()
        self.press(did, "danya")
        before = moderation_ipc.get(did)

        q, bot = self.press(did, "dasha")                 # заведомо проигрышное нажатие

        self.assertEqual(q.answers, 1, "обработчик не дошёл даже до ответа Telegram")
        self.assertEqual(len(bot.said), 1, "опоздавшему не сказали НИЧЕГО — карточка подвисла")
        after = moderation_ipc.get(did)
        self.assertEqual(after["status"], before["status"])          # состояние не поехало
        self.assertEqual(after["decided_by"], before["decided_by"])  # победитель не перезаписан
        self.assertEqual(after["final_text"], before["final_text"])
        self.assertNotIn(after["status"], ("", None))                # и не «подвешено»

    def test_refusal_on_a_vanished_row_is_honest_not_a_crash(self):
        """Строки очереди больше нет (её убрали) — бот обязан сказать это, а не упасть."""
        row = {"id": 999999, "draft": "текст", "card_msg_id": 5001, "client_id": CLIENT_ID}
        bot = FakeBot()
        dec = moderation_core.process_callback(row, "yes", "danya", False)
        dec["_by"] = "@danya"
        out = asyncio.run(mb._apply(FakeCtx(bot), CHAT_ID, row, dec))
        self.assertEqual(out, "closed")
        self.assertIn("строки очереди больше нет", bot.said[0][1])
        self.assertEqual(self.sent_by_live_executor(), [])

    def test_denied_press_leaves_the_card_open(self):
        """Отказ ПРАВ — не решение: карточка остаётся открытой, кнопки на месте (общую проверку
        прав эта задача не трогает — здесь только доказано, что захват её не подменил)."""
        did = self.card()
        suggest.APPROVER_USERNAMES = {"danya"}
        q, bot = self.press(did, "chuzhoy")
        self.assertIn("Нет прав", bot.said[0][1])
        self.assertEqual(q.markups, [], "кнопки сняли с карточки, по которой не решали")
        self.assertEqual(moderation_ipc.get(did)["status"], "posted")
        self.assertEqual(self.sent_by_live_executor(), [])


# ================== ПРЕДСМЕРТНЫЙ ВЗГЛЯД: ДЕДУП ОТПРАВИТЕЛЯ ====================

class TestSenderDedup(LiveBase):
    """Задание назвало способ провалиться: захват сделали атомарным, а дедуп у отправителя
    забыли — дубль переехал на ступень дальше и остался живым. Здесь отправитель меряется
    ОТДЕЛЬНО от кнопки."""

    def test_row_returned_to_ready_behind_the_button_is_not_sent_twice(self):
        did = self.card()
        self.press(did, "danya")
        self.assertEqual(len(self.sent_by_live_executor()), 1)
        # Строку СНОВА ставят в 'ready' МИМО кнопки (старый путь/ретрай/правка БД):
        moderation_ipc.set_decision(did, "ready", final_text="ВТОРОЕ", decided_by="@kto-to")
        self.assertEqual(len(moderation_ipc.fetch_ready()), 1)     # отправитель её ВИДИТ
        self.assertEqual(self.sent_by_live_executor(), [])         # и всё равно НЕ шлёт
        self.assertEqual(moderation_ipc.get(did)["status"], "ready")

    def test_two_pollers_at_once_send_exactly_one(self):
        """Два оборота поллинга НАКЛАДЫВАЮТСЯ: отправка идёт через `await`, поэтому один и тот же
        'ready' попадает в ОБЕ выборки, и при одном нажатии клиент получал два сообщения.

        Барьер стои́т ровно там, где случается беда, — СРАЗУ ПОСЛЕ выборки: обе стороны уже держат
        строку на руках и только потом идут её брать. Просто запустить два поллера мало — они
        разъезжаются во времени, и тест зеленеет на любом коде (замерено контрфактом 06.09:
        без замков он был ЕДИНСТВЕННЫМ зелёным из семи, то есть не ловил ничего)."""
        did = self.card()
        self.press(did, "danya")
        got, errors = [], []
        lock = threading.Lock()
        overlap = threading.Barrier(2)
        real_fetch = moderation_ipc.fetch_ready

        def fetch_then_wait_for_the_other(path=None):
            rows = real_fetch(path=path)
            try:
                overlap.wait(timeout=15)     # обе стороны держат ОДНУ И ТУ ЖЕ строку
            except threading.BrokenBarrierError:
                pass
            return rows

        async def fake_send(_client, cid, text, sleep=None, jitter=None):
            with lock:
                got.append((cid, text))
            return True, None

        def poller():
            try:
                asyncio.run(suggest.poll_and_send(FakeClient(), sender=fake_send,
                                                  sleep=_nosleep, jitter=lambda: 0))
            except Exception as e:              # noqa: BLE001
                with lock:
                    errors.append(f"{type(e).__name__}: {e}")

        moderation_ipc.fetch_ready = fetch_then_wait_for_the_other
        try:
            threads = [threading.Thread(target=poller) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
        finally:
            moderation_ipc.fetch_ready = real_fetch
        self.assertEqual(errors, [])
        self.assertEqual(len(got), 1, got)      # клиенту РОВНО одно, а не два

    def test_claim_for_send_is_atomic_at_the_queue_layer(self):
        did = self.card()
        moderation_ipc.set_decision(did, "ready", final_text="ОК", decided_by="@danya")
        won1, r1 = moderation_ipc.claim_for_send(did)
        won2, _r2 = moderation_ipc.claim_for_send(did)
        self.assertTrue(won1)
        self.assertFalse(won2)
        self.assertEqual(r1["status"], moderation_ipc.STATUS_SENDING)

    def test_delivery_stamp_is_written_once_and_never_rewritten(self):
        did = self.card()
        self.press(did, "danya")
        self.sent_by_live_executor()
        first = moderation_ipc.get(did)["sent_ts"]
        self.assertTrue(first, "момент первой доставки не записан — дедупу не на чём стоять")
        moderation_ipc.mark(did, "sent", reason="повтор")
        self.assertEqual(moderation_ipc.get(did)["sent_ts"], first)

    def test_claim_for_send_refuses_a_row_that_was_already_delivered(self):
        did = self.card()
        self.press(did, "danya")
        self.sent_by_live_executor()
        moderation_ipc.set_decision(did, "ready", final_text="ВТОРОЕ")
        won, row = moderation_ipc.claim_for_send(did)
        self.assertFalse(won)
        self.assertEqual(row["status"], "ready")     # строку не тронули, просто не взяли


# ============================ ИНВАРИАНТЫ ЖИВОГО ПУТИ ==========================

def _calls(path):
    """Имена вызываемых атрибутов (obj.attr(...)) в файле — по AST, а не грепом."""
    with io.open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            out.append(node.func.attr)
    return out


class TestLivePathInvariants(unittest.TestCase):
    """Замки от возврата класса: живой бот обязан ходить через захват, а отправитель — через
    дедуп. Голая проверка поведения этого не удержит — поведение можно вернуть «временно»."""

    def test_live_bot_claims_and_never_sets_decision_blindly(self):
        calls = _calls(os.path.join(REPO, "moderation_bot.py"))
        self.assertIn("claim_decision", calls, "живой бот перестал захватывать решение")
        self.assertNotIn("set_decision", calls,
                         "в живом боте вернулся UPDATE без предусловия по статусу")

    def test_live_sender_claims_before_sending(self):
        calls = _calls(os.path.join(REPO, "suggest.py"))
        self.assertIn("claim_for_send", calls, "у отправителя пропал дедуп — дубль вернулся")

    def test_claim_cannot_reopen_a_delivered_or_decided_row(self):
        for st in ("sent", "failed", "ready", "test_held", "rejected",
                   moderation_ipc.STATUS_SENDING, moderation_ipc.STATUS_SUPERSEDED):
            self.assertNotIn(st, moderation_ipc.CLAIMABLE_FROM,
                             f"из статуса {st} карточку снова можно «решить»")

    def test_late_comer_text_is_one_for_the_whole_contour(self):
        """Карточка и живой бот говорят опоздавшему ДОСЛОВНО одно и то же (одна функция)."""
        import moderation_card
        row = {"status": "sent", "decided_by": "@danya", "updated_ts": "2026-09-06T10:00:00+00:00"}
        self.assertEqual(moderation_card.render_closed(row), moderation_core.render_closed(row))
        self.assertIn("уже отправлено клиенту", moderation_core.render_closed(row))
        self.assertIn("@danya", moderation_core.render_closed(row))

    def test_sending_status_is_visible_to_the_late_comer(self):
        row = {"status": moderation_ipc.STATUS_SENDING, "decided_by": "@danya"}
        self.assertIn("отправляется прямо сейчас", moderation_core.render_closed(row))


if __name__ == "__main__":
    unittest.main(verbosity=2)
