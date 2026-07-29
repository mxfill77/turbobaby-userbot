# Улика: НЕМАЯ RC-сессия PID 21216 (`cse_01Xa5KTXp3RbcmFFXqeoAcJx`), 29.07.2026

Сессия «опись мозга» **не стартовала вовсе**: процесс жив, heartbeat идёт, работы — ноль.
Владелец узнал через 2 часа и только потому, что спросил. Ни один существующий предикат
(`channel_alive` сторожа, вотчдог контура, heartbeat) этот отказ не видит — по всем ним
сессия здорова.

Снимок сделан **при живом процессе** (не снимал — см. «Судьба улики» в конце).
Время в логах CLI — UTC; местное = UTC+7.

## Паспорт процесса (снято 29.07 ~18:30 местного)

| поле | значение |
|---|---|
| PID | **21216** (ЖИВ на момент снимка) |
| образ | `claude.exe`, версия 2.1.218 |
| родитель | PID 20552 = `claude.EXE rc --debug-file D:\turbobaby-bot\rc_server_debug.log` (ветка `rc-server` сторожа) |
| старт | 29.07.2026 **15:36:37** местного = 08:36:37Z |
| прожила к снимку | ~2 ч 53 мин |
| CPU за всё время | **12.47 с** (≈4.3 с/час — цена одного heartbeat-цикла) |
| память / потоки | 40.9 МБ WS / 23 потока |
| cse-id (work) | `cse_01Xa5KTXp3RbcmFFXqeoAcJx` |
| sessionId (uuid) | `bc8c785c-4ecf-4290-8986-6104acbe5cf5` |
| реестр сессии | `C:\Users\mxfill1\.claude\sessions\21216.json` (`kind":"interactive"`, `entrypoint":"sdk-cli"`, `startedAt":1785314198425`) |

Командная строка (дословно):

```
C:\Users\mxfill1\.local\bin\claude.exe --print
  --sdk-url https://api.anthropic.com/v1/code/sessions/cse_01Xa5KTXp3RbcmFFXqeoAcJx
  --session-id cse_01Xa5KTXp3RbcmFFXqeoAcJx
  --input-format stream-json --output-format stream-json --replay-user-messages
  --debug-file D:\turbobaby-bot\rc_server_debug-cse_01Xa5KTXp3RbcmFFXqeoAcJx.log
```

Отсюда же — **точная связка** для любого детектора: `--debug-file` в cmdline даёт лог сессии
без единой догадки (не по времени, не по имени файла).

## Транскрипт — ОТСУТСТВУЕТ

```
C:\Users\mxfill1\.claude\projects\d--turbobaby-bot\bc8c785c-4ecf-4290-8986-6104acbe5cf5.jsonl → MISSING
```

Поиск по ВСЕМУ дереву `~/.claude` на маску `bc8c785c*` — ноль файлов. Это не «лежит в другом
каталоге», это «не создан».

## Содержимое лога сессии: 529 heartbeat и НОЛЬ работы

Файл `rc_server_debug-cse_01Xa5KTXp3RbcmFFXqeoAcJx.log`, 724 строки:

| маркер | мёртвая 21216 | здоровая `cse_0141ZBzVF2i38mwDeuCTsdP6` |
|---|---|---|
| `CCRClient: Heartbeat sent` | **529** | 1011 |
| `[API REQUEST] /v1/messages` | **0** | 108 |
| `Stream started - received first chunk` | **0** | 73 |
| `tool_use` | **0** | есть |
| `Hooks: Found … hooks in registry` | **0** | 71 |
| `LSP Diagnostics: …` | **0** | 71 |

Первый heartbeat `08:37:00.071Z`, последний на момент снимка `11:33:04.621Z` — ровно каждые
20 с, без единого пропуска. **Сторож видит идеально здоровый процесс.**

## Где именно оборвалось: промпт НЕ ДОШЁЛ

Инициализация прошла ПОЛНОСТЬЮ и штатно (`[STARTUP] setup() completed in 26ms`, скиллы,
права, MCP), транспорт поднялся:

```
08:36:39.095Z SSETransport: SSE URL = …/sessions/cse_01Xa5KTXp3RbcmFFXqeoAcJx/worker/events/stream
08:36:39.097Z CCR v2 internal event writer registered for transcript persistence
08:36:39.387Z SSETransport: Connected
08:36:39.724Z CCRClient: initialized, epoch=1
```

А дальше — тишина в 16 минут, и первое событие оказалось служебным:

```
08:52:22.324Z SSETransport: Event seq=2 … event_type=control_request  ← +16 мин после Connected
09:00:29.268Z SSETransport: Event seq=4 … event_type=control_request
09:36:39.244Z [ERROR] SSETransport: Stream read error: The socket connection was closed unexpectedly
10:36:41.152Z SSETransport: Opening …/events/stream?from_sequence_num=6
```

Для сравнения, ЗДОРОВАЯ сессия того же дня получает первое событие **через 0.3 с** после
`Connected`:

```
05:30:39.112Z SSETransport: Connected
05:30:39.120Z SSETransport: Event seq=2 … session=8d59bc5d-…   (+8 мс)
```

**Вывод:** сессия исправна, работы ей не принесли. Отказ на стороне ДОСТАВКИ задания, а не
исполнения. Поэтому лечить рестартом сессии бессмысленно — нужен именно СИГНАЛ владельцу.

## Почему транскрипт — правильный признак

`CCR v2 internal event writer registered for transcript persistence` пишется в ОБЕИХ сессиях —
писатель регистрируется всегда. Но ФАЙЛ `<sessionId>.jsonl` появляется только когда писателю
есть что записать, то есть на первом реальном событии. Живой замер по корпусу 13 сессий за
сутки: **12 из 12 работавших создали транскрипт за 1.7–3.1 с** после старта процесса;
единственная без транскрипта — эта. Разрыв: 3.1 с против 10 300+ с — **три порядка**.

## Судьба улики

**НЕ СНЯЛ.** Процесс живой и безвредный (4 с CPU в час, 41 МБ), а живая улика ценнее снятой:
на ней проверяется детектор немоты в бою. Снимать — по слову владельца:

```powershell
Stop-Process -Id 21216            # либо: taskkill /PID 21216 /F
```

Родитель (`rc-server`, PID 20552) при этом НЕ трогается — канал RC остаётся жив.
