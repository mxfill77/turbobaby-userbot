# 2026-07-24 — именованный RC-канал: рестарт не потребовался (самовосстановление сторожем)

Задача (headless, тема 328): «перезапустить ТОЛЬКО именованный RC-канал `--remote-control
turbobaby-pc`, сервер устройства и rc_supervisor не трогать; факт — сессии висят в Connecting,
канал занят зависшей ssh-командой без таймаута».

**Итог: шаг kill НЕ выполнялся и не потребовался.** За секунды до старта задачи (задача
запущена 19:35:31) rc_supervisor сам погасил зависший именованный канал по ливнесс-пробе
(19:35:34, pid=1404) и поднял новый (19:35:50, pid=7820). Новый канал держит 3 established
к релею Anthropic, зависших ssh-детей нет. Убивать свежеподнятый канал = заново рвать только
что восстановленные сессии (и красное действие taskkill-класса) — оснований нет.

## 1. Снимок процессов (19:36:58, Win32_Process, дословно релевантное)

```
ProcessId       : 10020
ParentProcessId : 7308
Name            : python.exe
CreationDate    : 24.07.2026 16:49:47
CommandLine     : "D:\turbobaby-bot\venv\Scripts\python.exe"  "D:\turbobaby-bot\rc_supervisor.py"

ProcessId       : 11268
ParentProcessId : 10020
Name            : claude.exe
CreationDate    : 24.07.2026 19:35:28
CommandLine     : C:\Users\mxfill1\.local\bin\claude.EXE rc --debug-file D:\turbobaby-bot\rc_server_debug.log

ProcessId       : 7820
ParentProcessId : 10020
Name            : claude.exe
CreationDate    : 24.07.2026 19:35:50
CommandLine     : C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc --debug-file D:\turbobaby-bot\rc_session_debug.log
```

Прочее в выборке: дерево Claude Desktop (GUI, PID 10964 + служебные), внутренняя
claude-code-сессия Desktop (PID 9428) и сам headless-процесс этой задачи (PID 1220) — не
тронуты. Единственный процесс с `--remote-control turbobaby-pc` — **7820** (HALT-проверка
шага 2 пройдена: других кандидатов нет).

## 2. Соединения именованного канала PID 7820 (19:38:39, дословно)

```
LocalAddress                          LocalPort RemoteAddress       RemotePort       State
------------                          --------- -------------       ----------       -----
::                                        53246 ::                           0       Bound
2403:6200:8871:966f:448a:fad:4342:541     53246 2606:50c0:8003::154        443 Established
0.0.0.0                                   53245 0.0.0.0                      0       Bound
0.0.0.0                                   53243 0.0.0.0                      0       Bound
192.168.1.108                             53245 160.79.104.10              443 Established
192.168.1.108                             53243 160.79.104.10              443 Established
```

Итого: 3 Established (релей Anthropic, 443) + 3 Bound.

## 3. Зависшая ssh-команда

- Детей у PID 7820 — **нет** (Win32_Process ParentProcessId=7820 → пусто).
- `ssh.exe` в системе — **нет вообще** (Win32_Process Name='ssh.exe' → пусто).

Зависшая ssh жила в старом канале pid=1404 и умерла вместе с ним при гашении 19:35:34.

## 4. Хвост rc_remote_control.log — последние 20 строк (UTF-8, дословно)

```
2026-07-24 17:31:06,542 | [rc-server] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=6272, рестарт
2026-07-24 17:31:22,878 | [rc-server] старт pid=16084: C:\Users\mxfill1\.local\bin\claude.EXE rc
2026-07-24 17:31:23,542 | [named-channel] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=16992, рестарт
2026-07-24 17:31:39,836 | [named-channel] старт pid=12208: C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc
2026-07-24 17:51:40,326 | [named-channel] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=12208, рестарт
2026-07-24 17:51:56,769 | [named-channel] старт pid=4344: C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc
2026-07-24 18:12:27,204 | [named-channel] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=4344, рестарт
2026-07-24 18:12:43,575 | [named-channel] старт pid=15472: C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc
2026-07-24 18:33:13,990 | [named-channel] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=15472, рестарт
2026-07-24 18:33:30,293 | [named-channel] старт pid=16984: C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc
2026-07-24 18:47:54,736 | [rc-server] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=16084, рестарт
2026-07-24 18:48:11,040 | [rc-server] старт pid=15372: C:\Users\mxfill1\.local\bin\claude.EXE rc
2026-07-24 18:54:00,744 | [named-channel] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=16984, рестарт
2026-07-24 18:54:17,050 | [named-channel] старт pid=8944: C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc
2026-07-24 19:14:47,468 | [named-channel] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=8944, рестарт
2026-07-24 19:15:03,746 | [named-channel] старт pid=1404: C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc
2026-07-24 19:35:12,145 | [rc-server] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=15372, рестарт
2026-07-24 19:35:28,478 | [rc-server] старт pid=11268: C:\Users\mxfill1\.local\bin\claude.EXE rc
2026-07-24 19:35:34,167 | [named-channel] ЗОМБИ: лог протух (>1200 с) И 0 соединений — гашу pid=1404, рестарт
2026-07-24 19:35:50,465 | [named-channel] старт pid=7820: C:\Users\mxfill1\.local\bin\claude.EXE --remote-control turbobaby-pc
```

## 5. Контрольная проверка (19:40:44, ~5 мин после старта канала — окно 90 с выдержано)

```
   Id ProcessName StartTime
   -- ----------- ---------
 7820 claude      24.07.2026 19:35:50
11268 claude      24.07.2026 19:35:28
10020 python      24.07.2026 16:49:47

established: 3
дети PID 7820: (нет)
```

Новых строк ЗОМБИ/старт в логе после 19:35:50 нет — сторож канал 7820 не пересоздавал
(его проба гасит только «лог протух >1200 с И 0 соединений», а у 7820 соединения живые).

## 6. Наблюдение (не действие)

Весь день канал циклился ливнесс-пробой каждые ~20 мин (17:31 → 17:51 → 18:12 → 18:33 →
18:54 → 19:14 → 19:35 — семь рестартов named-channel, три rc-server): каждый экземпляр
доживал до «лог протух >1200 с И 0 соединений» и гасился. Это штатная работа пробы из
2ffa1fa при простое канала; пока телефонные сессии держат соединения — рестартов не будет.
Если сессии на телефоне всё ещё показывают Connecting — переоткрыть их: канал на ПК живой
и подключён к релею.
