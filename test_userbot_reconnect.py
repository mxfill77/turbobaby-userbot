# -*- coding: utf-8 -*-
"""
test_userbot_reconnect.py — ГОЛДЕНЫ УСТОЙЧИВОСТИ СОЕДИНЕНИЯ userbot (класс 05.08.2026).

Основание — замер суток 04–05.08: ПЯТЬ смертей userbot, все от одного необработанного
ConnectionError Telethon (`mtprotosender.py:266`, вход `userbot_listen.py` → `client.start()`),
причина внешняя (переезд ПК между Wi-Fi «Samgold 7» ↔ «Bless_house_2.4GHz»); один простой
длился 370 минут. Разбор: docs/artifacts/2026-08-05-userbot-crashes-and-mute-session-5348.md

Живого Telegram здесь НЕТ: клиент — двойник, пауза и часы инъектируются параметрами
`serve_forever`. Боевых файлов состояния тесты не касаются по построению: `userbot.log`
подменён рекордером (`ub.log`), `userbot.lock` не берётся (`acquire_lock` замокан),
`turbobaby_session.session` не открывается (TelegramClient не создаётся), `moderation_ipc.db`
уведён в temp импортом `test_isolation`. Клиент не может получить ни одного сообщения:
единственный канал наружу (IPC-поллер) здесь — фейк, и его считают, а не исполняют.

Что стережём:
  • обрыв НЕ роняет процесс и приводит к переподключению — ОБЕ живые формы смерти
    (форма A: разрыв на ходу, `run_until_disconnected`; форма B: не поднялся, `start`);
  • нормальная работа НЕ меняется: один `start()`, один `run_until_disconnected()`,
    разовая инициализация один раз, ни одной строки про переподключение;
  • экземпляр остаётся ОДИН: клиент не пересоздаётся, лок берётся один раз на весь простой,
    хендлер модерации и IPC-поллер (реальная отправка клиенту!) заводятся РОВНО ОДИН РАЗ;
  • тихого вечного цикла нет: пауза растёт до потолка, затяжной простой кричит ERROR;
  • НЕ сетевой отказ (баг кода) НЕ глотается — процесс падает, вотчдог остаётся вторым слоем.
"""

import test_isolation  # noqa: F401 — TESTING=1, боевой IPC заблокирован
import asyncio         # noqa: E402
import unittest        # noqa: E402
from unittest import mock  # noqa: E402

import suggest         # noqa: E402
import userbot_listen as ub  # noqa: E402


# ЖИВЫЕ ошибки инцидента, дословно из userbot.log/стдерра 04–05.08 (а не абстрактный Exception):
# так тест меряет тот же класс отказа, что убивал процесс.
ERR_START = ConnectionError("Connection to Telegram failed 5 time(s)")


def err_abort():
    return ConnectionAbortedError(
        1236, "[WinError 1236] Подключение к сети было разорвано локальной системой")


def err_share():
    return OSError(1231, "[WinError 1231] Сетевая папка недоступна")


def err_semaphore():
    return ConnectionResetError(121, "[WinError 121] Превышен таймаут семафора")


class LogRec:
    """Двойник `logging`-логгера: боевой userbot.log не задет, а строки можно проверять."""

    def __init__(self):
        self.lines = []

    def _add(self, level, msg, *a):
        self.lines.append((level, (msg % a) if a else str(msg)))

    def info(self, msg, *a, **kw):
        self._add("INFO", msg, *a)

    def warning(self, msg, *a, **kw):
        self._add("WARNING", msg, *a)

    def error(self, msg, *a, **kw):
        self._add("ERROR", msg, *a)

    def exception(self, msg, *a, **kw):
        self._add("ERROR", msg, *a)

    def text(self, level=None):
        return "\n".join(t for lv, t in self.lines if level in (None, lv))


class FakeMe:
    id = 777000111
    username = "turbophuket"
    premium = True


class FakeClient:
    """Минимальный двойник TelegramClient: играет заданный сценарий отказов и считает вызовы.

    Форма важна: `start()` и `run_until_disconnected()` — ровно те две точки, где живой Telethon
    бросает наружу ConnectionError (первая — форма B из разбора, вторая — форма A, когда сдался
    внутренний авто-реконнект). None в сценарии = «на этом круге всё хорошо»."""

    def __init__(self, start_errors=(), run_errors=()):
        self.start_errors = list(start_errors)
        self.run_errors = list(run_errors)
        self.starts = 0
        self.runs = 0
        self.disconnects = 0
        self.handlers = []

    async def start(self):
        self.starts += 1
        if self.start_errors:
            err = self.start_errors.pop(0)
            if err is not None:
                raise err
        return self

    async def run_until_disconnected(self):
        self.runs += 1
        if self.run_errors:
            err = self.run_errors.pop(0)
            if err is not None:
                raise err
        return None                      # штатная остановка (disconnect/Ctrl+C)

    async def get_me(self):
        return FakeMe()

    async def get_entity(self, uid):
        raise AssertionError("тест не ходит в сеть за сущностями")

    def add_event_handler(self, cb, event=None):
        self.handlers.append(cb)

    def is_connected(self):
        return True

    async def disconnect(self):
        self.disconnects += 1


_FLAGS = ("_startup_done", "_moderation_hooked", "_ipc_poller", "_ME_ID")


class Base(unittest.IsolatedAsyncioTestCase):
    """Общая обвязка: лог — рекордер, разовые замки модуля сброшены и восстановлены."""

    def setUp(self):
        self.rec = LogRec()
        self._log = ub.log
        ub.log = self.rec
        # getattr с дефолтом — СОЗНАТЕЛЬНО: до правки этих замков в модуле нет, и красный обязан
        # приходить от ПОВЕДЕНИЯ (процесс умер), а не от падения обвязки в setUp.
        self._saved = {k: getattr(ub, k, None) for k in _FLAGS}
        ub._startup_done = False
        ub._moderation_hooked = False
        ub._ipc_poller = None
        self.slept = []

    def tearDown(self):
        ub.log = self._log
        for k, v in self._saved.items():
            setattr(ub, k, v)

    async def sleep(self, sec):
        """Инъекция паузы: время не тратим, длительности копим."""
        self.slept.append(sec)

    def clock(self):
        """Инъекция часов: время = сумма уже «проспанного» (детерминированно, без sleep)."""
        return float(sum(self.slept))

    def once(self):
        """Двойник разовой инициализации: в сеть не ходим, но замок держим ТОТ ЖЕ, что в бою —
        внутри самой функции. Так `calls` считает РЕАЛЬНЫЕ инициализации, а не входы: цикл зовёт
        `_startup_once` после каждого удачного входа СОЗНАТЕЛЬНО (сорвавшаяся на сети инициализация
        обязана повториться целиком), и голден обязан мерить именно идемпотентность."""
        calls = []

        async def _fake(client):
            if ub._startup_done:
                return
            calls.append(client)
            ub._startup_done = True
        return calls, _fake


# ───────────────── 1. ОБРЫВ НЕ РОНЯЕТ ПРОЦЕСС (обе живые формы) ─────────────────

class TestObryvNeRonyaet(Base):

    async def test_main_perezhivaet_obryv(self):
        """ГЛАВНЫЙ регресс, и он НЕ опирается на новое API: тот же вход, что в бою (`main()`),
        с клиентом, который рвётся обеими формами. Процесс обязан ДОЙТИ ДО ВОЗВРАТА, а не
        выбросить ConnectionError наружу — до правки именно он убивал userbot (5 раз за сутки)."""
        cl = FakeClient(start_errors=[ERR_START, None], run_errors=[err_abort(), None])
        with mock.patch.object(ub, "TelegramClient", lambda *a, **kw: cl), \
             mock.patch.object(ub, "acquire_lock", lambda: True), \
             mock.patch.object(ub, "release_lock", lambda: None), \
             mock.patch.object(ub, "asyncio", _AsyncioNoSleep(self.slept)):
            await ub.main()                      # ← до правки здесь ConnectionError наружу
        self.assertEqual(cl.starts, 3, "два обрыва → три входа на одном и том же клиенте")
        self.assertEqual(cl.runs, 2)
        self.assertEqual(cl.disconnects, 1, "disconnect один — на выходе, а не на каждом круге")

    async def test_forma_a_razryv_na_hodu(self):
        """Форма A: связь порвалась на ходу — `run_until_disconnected` бросил ConnectionError.
        Процесс обязан ЖИТЬ и переподключиться, а не умереть на строке 485."""
        cl = FakeClient(run_errors=[ERR_START, None])
        calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        self.assertEqual(cl.starts, 2, "после обрыва обязан быть ВТОРОЙ вход")
        self.assertEqual(cl.runs, 2)
        self.assertEqual(self.slept, [ub.RECONNECT_DELAY_MIN], "пауза перед повтором обязана быть")
        self.assertIn("ПОТЕРЯНА", self.rec.text("WARNING"))
        self.assertIn("ВОССТАНОВЛЕНА", self.rec.text("INFO"))

    async def test_forma_b_ne_podnyalsya_pri_starte(self):
        """Форма B (трейсбек разбора): `client.start()` бросил ConnectionError на старте."""
        cl = FakeClient(start_errors=[ERR_START, err_abort(), None])
        calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        self.assertEqual(cl.starts, 3)
        self.assertEqual(cl.runs, 1, "до штатной работы дошли ровно один раз")
        self.assertEqual(self.slept, [ub.RECONNECT_DELAY_MIN, ub.RECONNECT_DELAY_MIN * 2])
        self.assertEqual(len(calls), 1, "разовая инициализация — один раз, а не на каждый вход")

    async def test_zhivye_kody_windows(self):
        """Коды локального сетевого стека из инцидента — тот же класс, что ConnectionError."""
        for err in (err_abort(), err_share(), err_semaphore(), TimeoutError("timed out"),
                    asyncio.TimeoutError()):
            with self.subTest(err=type(err).__name__):
                self.slept = []
                cl = FakeClient(run_errors=[err, None])
                _calls, fake = self.once()
                with mock.patch.object(ub, "_startup_once", fake):
                    await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
                self.assertEqual(cl.starts, 2)

    async def test_ne_setevoy_otkaz_ne_glotaetsya(self):
        """Баг кода вечным циклом НЕ прячем: падаем, вотчдог остаётся вторым слоем."""
        cl = FakeClient(run_errors=[RuntimeError("баг прикладного кода")])
        _calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            with self.assertRaises(RuntimeError):
                await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        self.assertEqual(cl.starts, 1, "не сетевой отказ повторять нельзя")


# ───────────────── 2. НОРМАЛЬНАЯ РАБОТА НЕ МЕНЯЕТСЯ ─────────────────

class TestNormaNeMenyaetsya(Base):

    async def test_bez_obryva_vsyo_kak_ranshe(self):
        cl = FakeClient()
        calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        self.assertEqual((cl.starts, cl.runs), (1, 1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.slept, [], "без обрыва пауз быть не должно")
        for word in ("ПОТЕРЯНА", "ВОССТАНОВЛЕНА", "переподключ", "СВЯЗИ С TELEGRAM НЕТ"):
            self.assertNotIn(word, self.rec.text(), f"тихий прогон не пишет «{word}»")

    async def test_shtatnaya_ostanovka_ne_pereotkryvaet_krug(self):
        """`run_until_disconnected` вернулся штатно (disconnect/Ctrl+C) → выходим, а не крутим."""
        cl = FakeClient(run_errors=[None, ERR_START])
        _calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        self.assertEqual(cl.runs, 1, "штатный возврат = остановка, второго круга быть не должно")


# ───────────────── 3. ЭКЗЕМПЛЯР ОСТАЁТСЯ ОДИН ─────────────────

class TestEkzemplyarOdin(Base):

    async def test_klient_i_lok_odni_na_ves_prostoy(self):
        """Переподключение НЕ плодит второй экземпляр: один TelegramClient, один захват лока."""
        cl = FakeClient(run_errors=[ERR_START, None])
        made = []
        calls, fake = self.once()

        def factory(*a, **kw):
            made.append((a, kw))
            return cl

        with mock.patch.object(ub, "TelegramClient", factory), \
             mock.patch.object(ub, "acquire_lock", lambda: True), \
             mock.patch.object(ub, "release_lock", lambda: None), \
             mock.patch.object(ub, "_startup_once", fake), \
             mock.patch.object(ub, "asyncio", _AsyncioNoSleep(self.slept)):
            await ub.main()
        self.assertEqual(len(made), 1, "клиент создаётся РОВНО один — сессия одна")
        self.assertEqual(cl.starts, 2, "переподключение прошло на ТОМ ЖЕ объекте")
        self.assertEqual(cl.disconnects, 1, "disconnect — один, на выходе")
        self.assertEqual(len(calls), 1)

    async def test_startup_once_ne_dubliruet_hendler_i_poller(self):
        """Второй вызов разовой инициализации обязан быть ПУСТЫМ: второй on_moderation = двойная
        модерация, второй IPC-поллер = ДВОЙНАЯ отправка клиенту."""
        cl = FakeClient()
        poller_runs = []

        async def fake_poller(client):
            poller_runs.append(client)

        async def fake_resolve(client):
            return -1001234567890

        saved = (suggest.SUGGEST_MODE, suggest.MODERBOT_TOKEN)
        suggest.SUGGEST_MODE, suggest.MODERBOT_TOKEN = True, "fake-token"
        try:
            with mock.patch.object(ub, "_suggest_ipc_poller", fake_poller), \
                 mock.patch.object(suggest, "resolve_mod_group", fake_resolve):
                await ub._startup_once(cl)
                await ub._startup_once(cl)
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        finally:
            suggest.SUGGEST_MODE, suggest.MODERBOT_TOKEN = saved
            task = ub._ipc_poller
            if task is not None:
                task.cancel()
        self.assertEqual(cl.handlers.count(ub.on_moderation), 1, "хендлер модерации — ОДИН")
        self.assertEqual(len(poller_runs), 1, "IPC-поллер (канал наружу) — ОДИН")
        self.assertEqual(ub._ME_ID, FakeMe.id)


class TestIzolyaciyaProb(Base):
    """Мера «клиенты не получили НИ ОДНОГО сообщения из-за проб»: единственный канал наружу —
    боевая очередь IPC, тривайр `test_isolation` считает попытки её открыть. Норма — 0."""

    async def test_naruzhu_nol_obrashcheniy(self):
        cl = FakeClient(run_errors=[ERR_START, None])
        _calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        self.assertEqual(test_isolation.outbound_prod_attempts(), 0)


class _AsyncioNoSleep:
    """Подмена модуля `asyncio` внутри userbot_listen: `sleep` не спит, остальное настоящее.
    Нужна там, где `serve_forever` вызывается через `main()` — без инъекции параметром."""

    def __init__(self, slept):
        self._slept = slept

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, sec, *a, **kw):
        self._slept.append(sec)


# ───────────────── 4. ТИХОГО ВЕЧНОГО ЦИКЛА НЕТ ─────────────────

class TestNeTihiyCikl(Base):

    async def test_pauza_rastyot_i_upiraetsya_v_potolok(self):
        cl = FakeClient(start_errors=[ERR_START] * 12 + [None])
        _calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        self.assertEqual(self.slept[0], ub.RECONNECT_DELAY_MIN)
        self.assertEqual(self.slept[1], ub.RECONNECT_DELAY_MIN * 2, "пауза обязана расти")
        self.assertEqual(max(self.slept), ub.RECONNECT_DELAY_MAX, "и упираться в потолок")
        self.assertEqual(self.slept[-1], ub.RECONNECT_DELAY_MAX)
        self.assertTrue(all(s <= ub.RECONNECT_DELAY_MAX for s in self.slept))

    async def test_zatyazhnoy_prostoy_krichit_error(self):
        """Долгий простой ВИДЕН: ERROR в логе — но не на каждой попытке, а раз в порог."""
        cl = FakeClient(start_errors=[ERR_START] * 30 + [None])
        _calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        errors = [t for lv, t in self.rec.lines if lv == "ERROR"]
        self.assertTrue(errors, "затяжной простой обязан кричать ERROR")
        self.assertIn("СВЯЗИ С TELEGRAM НЕТ", errors[0])
        total = sum(self.slept)
        self.assertLessEqual(len(errors), int(total // ub.RECONNECT_ALARM_SEC) + 1,
                             "крик раз в порог, а не на каждой попытке")

    async def test_pervyy_obryv_govorit_srazu(self):
        """Первая же потеря связи ВИДНА сразу (не ждём порога тревоги)."""
        cl = FakeClient(run_errors=[err_semaphore(), None])
        _calls, fake = self.once()
        with mock.patch.object(ub, "_startup_once", fake):
            await ub.serve_forever(cl, sleep=self.sleep, clock=self.clock)
        first = self.rec.lines[0]
        self.assertEqual(first[0], "WARNING")
        self.assertIn("ПОТЕРЯНА", first[1])
        self.assertIn("WinError 121", first[1], "в логе — живая причина, а не «ошибка сети»")


if __name__ == "__main__":
    unittest.main()
