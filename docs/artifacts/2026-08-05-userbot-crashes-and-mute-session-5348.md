# Падения userbot 04–05.08 и немая сессия PID 5348

Замер 05.08.2026 09:57–10:07 (+07), ПК-полоса, только чтение + одно разрешённое снятие PID 5348.

## 1. Что ИМЕННО роняет userbot — дословно

Трейсбек из `logs/userbot_stderr.log` (смерть 05.08 09:31:14 локального времени):

```
Traceback (most recent call last):
  File "D:\turbobaby-bot\userbot_listen.py", line 550, in <module>
    asyncio.run(main())
  File "C:\Python312\Lib\asyncio\runners.py", line 194, in run
    return runner.run(main)
  File "C:\Python312\Lib\asyncio\runners.py", line 118, in run
    return self._loop.run_until_complete(task)
  File "C:\Python312\Lib\asyncio\base_events.py", line 687, in run_until_complete
    return future.result()
  File "D:\turbobaby-bot\userbot_listen.py", line 485, in main
    await client.start()
  File "D:\turbobaby-bot\venv\Lib\site-packages\telethon\client\auth.py", line 135, in _start
    await self.connect()
  File "D:\turbobaby-bot\venv\Lib\site-packages\telethon\client\telegrambaseclient.py", line 560, in connect
    if not await self._sender.connect(self._connection(
  File "D:\turbobaby-bot\venv\Lib\site-packages\telethon\network\mtprotosender.py", line 133, in connect
    await self._connect()
  File "D:\turbobaby-bot\venv\Lib\site-packages\telethon\network\mtprotosender.py", line 266, in _connect
    raise ConnectionError('Connection to Telegram failed {} time(s)'.format(self._retries))
ConnectionError: Connection to Telegram failed 5 time(s)
```

Две РАЗНЫЕ формы смерти, обе — один и тот же необработанный `ConnectionError`:

* **форма A (разрыв на ходу)** — `userbot.log` строки 54934–54985:
  `Server closed the connection: [WinError 121] Превышен таймаут семафора` →
  `Attempt 2 at connecting failed: ConnectionAbortedError: [WinError 1236] Подключение к сети было разорвано локальной системой` →
  `Attempt 3 ... OSError: [WinError 1231] Сетевая папка недоступна` → 5 раундов по 6 попыток →
  `Automatic reconnection failed 5 time(s)` → `Future exception was never retrieved` →
  `ConnectionError: Connection to Telegram failed 5 time(s)`;
* **форма B (не поднялся при старте)** — трейсбек выше, `client.start()` на строке 485.

Ни одной ошибки прикладного кода. `WinError 1236/1231` — коды **локального** сетевого стека Windows.

## 2. Падений за сутки и интервалы (локальное время, +07)

| # | смерть | подъём | лежал | Δ от прошлой смерти |
|---|---|---|---|---|
| 1 | 01:13:16 | 01:18:50 | 5м 33с | — |
| 2 | 01:19:56 | 01:38:47 | 18м 51с | 6м 39с |
| 3 | 01:39:53 | 07:50:31 | **370м 37с** | 19м 57с |
| 4 | 09:27:12 | 09:30:08 | 2м 55с | 7ч 47м 18с |
| 5 | 09:31:14 | 09:51:36 | 20м 22с | 4м 02с |

**Итого 5 падений.** Простой №3 (370м37с) — это и есть «вчерашние ~397 минут»: измерено 370м37с
от смерти 01:39:53 до подъёма 07:50:31; глушение сторожа началось раньше подъёма —
`01:58:44 ERROR контур-вотчдог: userbot умер 3 раз подряд — СТОП попыток, нужен разбор`.

## 3. Вердикт по гипотезе владельца: ПОДТВЕРЖДАЕТСЯ

Гипотеза «обрывы внешней сети ПК» подтверждена **тремя независимыми свидетелями в те же секунды**
(разные процессы, разные адресаты — Telegram DC, GitHub, Bridge на VPS, api.anthropic.com):

**Утренние падения (4 и 5) — обрыв Wi-Fi, доказан журналом Windows:**

* `Microsoft-Windows-WLAN-AutoConfig/Operational` 09:22:04–09:22:12: `11004 Wireless security stopped` →
  `8003 disconnected` → `8000/11001/11005/8001 connected` — переезд SSID **`Samgold 7` → `Bless_house_2.4GHz`**,
  адаптер Intel(R) Wireless-AC 9462. В эту же секунду userbot получает `WinError 121` и уходит в реконнект;
* `pc_orchestrator.log 09:22:08` — `авто-фетч: git fetch origin не удался — Could not resolve host: github.com`;
* `pc_orchestrator.log 09:21:49–09:38:45` — `get_pending(...) ошибка: URLError` (Bridge на VPS, свой хост);
* RC-сессия 09:46:46–09:49 — `getaddrinfo ENOTFOUND api.anthropic.com`, `ENOTFOUND http-intake.logs.us5.datadoghq.com`,
  `Could not resolve hostname github.com`. **DNS лежал целиком**, а не «Telegram недоступен».

Дальше сеть флапала ещё дважды: 09:38:45–09:49 переезды `Bless_house ↔ Samgold` с четырьмя
`8002 WLAN failed to connect` и `DHCPNACK от 192.168.1.1` в 09:49:16. Подъём userbot в 09:51:36 удался
ровно после того, как ассоциация устоялась.

**Ночные падения (1–3) — тоже сеть, но БЕЗ события адаптера:** в окне 00:00–09:00 в WLAN-журнале
единственная запись — 02:18:10 (переезд SSID), к смертям 01:13/01:19/01:39 отношения не имеет.
Линк держался, внешняя связность — нет: `01:19:22 git fetch ... Failed to connect to github.com port 443
after 21115 ms` (DNS резолвился, TCP молчал) и подряд `get_pending: URLError/TimeoutError` к VPS
с 01:20 по 01:38. Это выше адаптера — роутер/провайдер.

**Сон ПК исключён замером:** `Kernel-Power` id 42/107/109/506/507 за 04.08 18:00 – 05.08 10:10 — событий
НЕТ; `Power-Troubleshooter` — пусто. Записи демона «детект пробуждения ПК: скачок wall-clock … из них
**сна 0с**» — это заголодавший на блокирующих сокетах цикл, а не гибернация. Известный класс #171
(ложная смерть вотчдога на пробуждении) здесь ни при чём.

## 4. Сторож: когда он реально заглохнет

`pc_orchestrator.py:5419 _client_watch_step` — счётчик смертей **обнуляется на ЛЮБОМ тике, где процесс
жив** (строка 5424: `if alive: return "alive", {…"deaths": 0, "halted": False}`). Глушение
(`halted=True`, строка 5431: `if st["deaths"] >= max_deaths`) требует **трёх подряд мёртвых тиков без
единого живого между ними** — именно так вышло ночью (1/3 в 01:19:00, 2/3 в 01:38:58, стоп в 01:58:44).

Замер сейчас: `pc_orchestrator.client_watch.json` (09:59:09) →
`"userbot": {"deaths": 0, "halted": false}`, `max_deaths: 3`, `cooldown: 900`.
**Счётчик уже сброшен в 0** — «третья смерть» не заряжена; она перезарядится только если бот снова
умрёт трижды подряд, не дожив ни до одной живой проверки.

## 5. Немая сессия PID 5348 — доказательства и снятие

`claude.exe --print --sdk-url …/cse_01CsJu7GuMdJNTe6P4sxJi1S --session-id cse_01CsJu7GuMdJNTe6P4sxJi1S`,
старт 09:39:41, родитель 21404 (`claude.EXE rc`), тот — потомок 12444 (`rc_supervisor.py`).

**(а) Немая — ДА:**

* транскрипт `bridge-transcript-cse_01CsJu7GuMdJNTe6P4sxJi1S.jsonl` — **0 байт**, создан 09:39:41,
  `LastWriteTime` = 09:39:41 (ни одной записи за 25 минут);
* в отладочном логе сессии (42.7 КБ) **ноль** маркеров обращения к модели:
  `/v1/messages`=0, `stream_event`=0, `assistant`=0, `tool_use`=0, `tokens`=0, `Starting query`=0.
  Три совпадения по `Request|model=|prompt` — строки инициализации (правило permissions,
  `verifyAutoModeGateAccess model=claude-opus-5`, MCP-capabilities `hasPrompts`);
* живёт ровно heartbeat: 52 × `CCRClient: Heartbeat sent`, 15 × `Heartbeat failed`, 54 × `SSETransport`
  (переподключения), больше в логе после инициализации ничего нет;
* CPU за 25 минут — 3.1 с;
* демон это уже зафиксировал сам:
  `09:51:48,965 ERROR НЕМАЯ сессия: pid=5348 sid=3984a0ec прожила 725с без транскрипта и tool_use`.

**(б) Следов работы — НЕТ:** `git diff --stat` пусто, `git diff --cached --stat` пусто, последний коммит
921c9c6 от 01:00:04. Файлов репо, изменённых после 09:39, — 13, и все чужие (журнал/спул cowork,
состояния оркестратора, `moderation_ipc.db`, `turbobaby_session.session`, `userbot.lock`); ни одного
файла кода. Дочерний процесс ровно один — `conhost.exe` PID 20192 (консольный хост, не работа).

**(в) Ботов не заденет:** userbot 17504 ← 6376 (`pc_orchestrator.py`), moderbot 10080 ← 12124
(`pc_agent.py`), оркестратор 6376 ← 12904, pc_agent 12124 ← 2832, rc_supervisor 12444 ← 9264,
RC-канал 2324 и RC-сервер 21404 ← 12444. Ни один не в поддереве 5348.

**Снято:** `Stop-Process -Id 5348 -Force` (без `-T`, ровно один PID). После: `Get-Process -Id 5348` —
не найден, `Win32_Process ProcessId=5348` — отсутствует. Все шесть python-процессов контура живы,
`userbot.log` пишется (mtime 10:06:39 при замере 10:07:13), полоса разблокирована — в 10:07 демон
уже поднял headless-исполнителя PID 10124 (родитель 6376).

## 6. Что осталось владельцу (НЕ делалось — контур заморожен)

* **Задача 291 упала не из-за 5348.** `CLAIM id=291 in_progress` 09:46:32 → `RUN` 09:46:33 →
  `FAIL причина=exec_error окно=05.08 02:46–02:51 UTC следы: коммитов=0, записей журнала=0` →
  `COMPLETE id=291 status=failed` 09:51:34. Окно провала совпадает секунда в секунду с DNS-обвалом
  09:46:46–09:49. Переставлять — за владельцем.
* **Корень падений — Wi-Fi, а не код.** Пока ПК ходит через флапающую пару точек
  `Samgold 7` / `Bless_house_2.4GHz`, падения повторятся: за сутки 5 штук, три подряд глушат сторожа.
  Развилка владельца: увести ПК на кабель / зафиксировать одну точку / поднять
  `connection_retries` Telethon и обернуть `client.start()` в вечный retry (правка кода —
  клиентский контур заморожен, сюда не лезли).
