# RC-сессии висли в Connecting: живой rc-server держал ОТОЗВАННЫЙ OAuth-токен (28.07.2026)

Класс: **долгоживущий процесс переживает повторный вход владельца**. Повторный
`claude auth login` отзывает прежний токен на стороне API, но процесс, поднятый ДО входа,
продолжает жить со своим токеном в памяти и `.credentials.json` больше не перечитывает.

Время в логах CLI — **UTC**, локальное = UTC+7.

## Симптом

Сессия с телефона создаётся, но вечно висит в `Connecting`, промпт не доходит.
Повторы: 17:34 и 18:18 локального.

## Улики (дословно)

Свежий вход владельца:

```
C:\Users\mxfill1\.claude\.credentials.json
CreationTime  : 28.07.2026 15:47:14
LastWriteTime : 28.07.2026 15:47:14
scopes        : user:file_upload user:inference user:mcp_servers user:profile user:sessions:claude_code
subscriptionType : max
expiresAt     : 2026-07-28T14:11:26Z        (= 21:11:26 локального)
```

RC-сервер при этом жил с позапрошлого дня — PID 17896, старт 24.07.2026 21:32:48,
родитель 6888 (`rc_supervisor.py`), среда зарегистрирована тогда же:

```
2026-07-24T14:32:49.651Z [bridge:init] Registered, server environmentId=env_01Q9k7xFQCS1z2umkrbjppMF
```

Сессия 17:34 (10:34Z) — сервер её ПОЛУЧИЛ, подтвердил, породил воркер и упёрся в 401:

```
10:34:37.512Z [bridge:api] GET .../work/poll -> 200 workId=cse_01R4adykeqcbzKitUpSDNSRk type=session
10:34:37.514Z [bridge:work] Acknowledging workId=cse_01R4adykeqcbzKitUpSDNSRk
10:34:38.370Z [bridge:api] POST .../work/cse_01R4adykeqcbzKitUpSDNSRk/ack -> 200
10:34:38.710Z [bridge:session] CCR v2: registered worker sessionId=[REDACTED] epoch=1 attempt=1
10:34:38.724Z [bridge:session] sessionId=[REDACTED] pid=19800
10:34:38.728Z [bridge:token] Scheduled token refresh ... (expires=2026-07-28T18:34:38.000Z, buffer=300s)
10:34:39.144Z [code-session] Get session_01R4adykeqcbzKitUpSDNSRk failed 401: OAuth access token has been revoked.
```

**Второе доказательство рядом с первым:** сервер планирует refresh на `expires=2026-07-28T18:34:38Z`,
а в свежем `.credentials.json` лежит `expiresAt=2026-07-28T14:11:26Z` — это РАЗНЫЕ токены.

Почему сессия всё же «создаётся» и висит: `/work/poll` ходит под отдельным
`environment_secret` и отвечает 200, воркер поднимается и подключается к SSE —
но метаданные с промптом тянет `Get session` под OAuth-токеном, и он отозван.
Воркер после этого только пульсирует:

```
11:17:01.870Z SSETransport: Connected
11:17:02.375Z CCRClient: Heartbeat sent      (и так каждые 20 с — ни одной рабочей строки)
```

По попытке 18:18 (11:18Z) в логе сервера **нет ничего**: последняя строка —
`11:12:15.132Z GET .../work/poll -> 200 (no work, 400 consecutive empty polls)`,
work до сервера не дошёл вовсе. Прямой 401 доказан только на 17:34.

## Лечение

Снять РОВНО процесс rc-server; сторож поднимет новый сам и тот перечитает свежие креды.
Механизм в `rc_supervisor.supervise_branch`: `proc.poll()` раз в `CHECK_INTERVAL=30` с →
пауза `RESTART_DELAY=15` с → пре-флайт `claude doctor` → `default_spawn`.

```
Stop-Process -Id 17896 -Force
```

Живой результат:

```
18:26:48,766 | [rc-server] процесс вышел (exit=4294967295) — рестарт через 15 с
18:27:05,078 | [rc-server] старт pid=1112: C:\Users\mxfill1\.local\bin\claude.EXE rc
2026-07-28T11:27:06.452Z [bridge:init] Registered, server environmentId=env_01V4gujaBbDTLNJN2no6peHj
2026-07-28T11:27:07.515Z [bridge:work] Starting poll loop spawnMode=same-dir maxSessions=32
2026-07-28T11:27:07.997Z [bridge:api] GET .../work/poll -> 200 (no work, 1 consecutive empty polls)
```

Грепом по новому логу: ни одной строки `401` / `revoked` / `Unauthorized`.

## Что при этом умирает — знать заранее

1. **Меняется environmentId.** Новый `claude rc` регистрирует НОВУЮ среду
   (`env_01Q9k7xFQCS1z2umkrbjppMF` → `env_01V4gujaBbDTLNJN2no6peHj`), старая мертва.
   На телефоне надо выбрать свежее Environment.
2. **`rc_server_debug.log` обнуляется:** `default_spawn` делает `open(spec["log"], "w")`
   перед стартом. Улику копировать ДО снятия — здесь в
   `tmp/rc_server_debug_20260728-before-restart.log` (4 218 531 байт).
3. **Воркеры остаются сиротами.** Windows не снимает детей вместе с родителем. На момент
   рестарта под 17896 висели три `claude --print --sdk-url …`: PID 19024 (`cse_01AyUG2o…`,
   15:01:14), 14888 (`cse_016WXCF8…`, 15:04:40), 19800 (`cse_01R4adyk…`, 17:34:38). Все —
   на отозванном токене, в логах только `Heartbeat sent`, живой работы ноль.

## Правило на будущее

После КАЖДОГО повторного `claude auth login` на ПК — рестартовать rc-server, иначе он
останется с отозванным токеном и будет молча глотать сессии. Признак в логе — ровно
строка `Get session_… failed 401: OAuth access token has been revoked` при нормальных
`work/poll -> 200`. Сторож этот отказ НЕ ловит: процесс жив, лог свежий, соединения есть,
предикат `channel_alive` зелёный — зомби-проба здесь бессильна по определению.
