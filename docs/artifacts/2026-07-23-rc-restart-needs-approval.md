# 2026-07-23 — Рестарт TurboBabyRC упёрся в красную карточку гарда (нужно «да» владельца)

Задача 361→рестарт: RC-канал мёртв при живом процессе. Headless-сессия дошла до
`schtasks /end /tn TurboBabyRC` и была остановлена ПК-гардом: `schtasks` (всё, кроме
`/query`) — доктринально красное в ОБЕИХ ролях (`pretool_guard._stays_red`, голден
`test_pretool_guard.py:364` пинует ровно `schtasks /End /TN TurboBabyRC`). Обход
(Stop-/Start-ScheduledTask мимо токен-матчера) не искался — это класс «обход гарда».

## ФАКТ ДО (снято 23.07.2026 ~23:08, читающие команды)

Зомби подтверждён: у claude.exe PID 6120 **0 TCP-соединений** при живом процессе.

Дерево TurboBabyRC (Win32_Process, дословно):

```
ProcessId       : 23284
ParentProcessId : 2848
Name            : wscript.exe
CreationDate    : 22.07.2026 22:35:31
CommandLine     : "C:\Windows\System32\wscript.exe" //B //Nologo "D:\turbobaby-bot\rc_remote_control.vbs"

ProcessId       : 23116
ParentProcessId : 23284
Name            : python.exe
CreationDate    : 22.07.2026 22:35:31
CommandLine     : "D:\turbobaby-bot\venv\Scripts\python.exe"  "D:\turbobaby-bot\rc_supervisor.py"

ProcessId       : 6120
ParentProcessId : 23116
Name            : claude.exe
CreationDate    : 22.07.2026 22:35:33
CommandLine     : C:\Users\mxfill1\AppData\Local\...\claude-code\2.1.217\claude.exe --remote-control turbobaby-pc
```

`schtasks /query /tn TurboBabyRC /v /fo LIST` (ключевое):

```
TaskName:      \TurboBabyRC
Status:        Running
Last Run Time: 22.07.2026 23:33:53
Last Result:   -2147020576   (0x800710E0 — запрос на запуск был отклонён: инстанс уже работал)
Task To Run:   C:\Windows\System32\wscript.exe //B //Nologo "D:\turbobaby-bot\rc_remote_control.vbs"
Schedule Type: At logon time
Run As User:   mxfill1
```

## Готовые команды после «да» (исполнить руками или перезапустить задачу после карве-аута)

```powershell
schtasks /end /tn TurboBabyRC
Start-Sleep -Seconds 10
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'rc_supervisor|remote-control' -or $_.Name -eq 'wscript.exe' } | Select-Object ProcessId, Name, CreationDate   # выжившие НЕ добивать
schtasks /run /tn TurboBabyRC
Start-Sleep -Seconds 60
Get-CimInstance Win32_Process -Filter "Name='claude.exe'" | Where-Object { $_.CommandLine -match 'remote-control' } | Select-Object ProcessId, CreationDate, CommandLine
# живость канала = ЧИСЛО TCP-соединений нового PID (0 = снова зомби):
(Get-NetTCPConnection -OwningProcess <новый PID> -ErrorAction SilentlyContinue | Measure-Object).Count
Get-Content D:\turbobaby-bot\rc_remote_control.log -Tail 30
```

## Развилка владельцу

1. **«да» на карточку / руками** — выполнить блок выше (одноразово).
2. **Карве-аут в гарде** (если рестарт СВОЕГО сервиса штатным потоком объявлен зелёным
   насовсем): узкое исключение в `pretool_guard._stays_red` для
   `schtasks /(end|run) /tn TurboBabyRC` + голдены; текущий голден 364 сознательно
   пинует обратное — менять надо оба конца.
3. Отдельный корень из задачи 361 остаётся: `rc_supervisor` проверяет канал только
   перед стартом и зомби (процесс жив, TCP=0) не рестартует — лечится ливнесс-пробой
   в супервизоре, тогда рестарты Планировщиком вообще не понадобятся.
