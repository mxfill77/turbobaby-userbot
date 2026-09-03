# Замок файла в каталоге пакета Claude Desktop — разведка полосы 328 (26.08.2026)

Только чтение. Ничего не удалялось, не запускалось пробными скриптами, процессы не трогались,
конфиги и файлы секретов не читались.

---

## ГЛАВНЫЙ ОТВЕТ (ШАГ 6): **НЕТ**

Исполнитель полосы 328, вся его родительская цепочка и демон-оркестратор ПК **НЕ зависят от
файлов каталога УСТАНОВКИ пакета** `C:\Program Files\WindowsApps\Claude_1.37937.1.0_x64__pzs8sxrjxfjjc\`.

Доказательство по путям, а не по названиям — четыре независимых основания:

1. **Живая цепочка исполнителя этой самой задачи** (снята первым делом, ШАГ 1): ни одного звена
   в WindowsApps. Исполнитель — `C:\Users\mxfill1\.local\bin\claude.EXE`, самодостаточный бинарь
   263 931 552 байта от 24.07.2026, не симлинк (LinkType пуст).
2. **Резолвер бинаря** `pc_orchestrator._resolve_claude_once` — PATH-first, и PATH сегодня
   отдаёт именно `.local\bin`: `(Get-Command claude).Source` → `C:\Users\mxfill1\.local\bin\claude.exe`.
3. **Код полосы каталог установки не знает вовсе**: греп по всем `*.py` репозитория на
   `WindowsApps|Program Files\Claude|Claude_pzs8sxrjxfjjc|CoworkVMService` → **0 совпадений**.
4. **Эмпирика самого инцидента** — сильнейшее из четырёх. За окно 26.08 пакет обновлялся **дважды**
   и приложение **дважды принудительно гасилось** (`ForceApplicationShutdownOption`, 01:40 и 08:50),
   а демон всё это время **давал обороты**: строки лога в 01:17, 03:35–03:43, 05:28–06:10, 07:19,
   11:07–11:42. Демон умер **только на перезагрузке** владельца, а не на обновлении пакета.

### Оговорка, без которой ответ «НЕТ» был бы враньём

К каталогу **ДАННЫХ** пакета (`AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code`)
привязка **есть, живая и намеренная** — `pc_orchestrator._claude_base_dirs()`, строки 1479–1494.
Сегодня она спит, потому что PATH выигрывает раньше. Но она **уже срабатывала боем**: самая первая
строка живого лога демона —

```
2026-07-22 23:11:31,140 INFO === ДЕМОН СТАРТ (lane=pc, poll=60s, task_timeout=2700s, approval_ttl=1800s,
claude=C:\Users\mxfill1\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.197\claude.exe,
commit=67c1b94) ===
```

То есть 22.07 полоса 328 исполнялась бинарём, живущим **внутри данных пакета Claude**. Сегодня —
нет (все старты с 20.08 и пост-перезагрузочный 11:53:04 показывают `.local\bin\claude.EXE`).

---

## ШАГ 7. Отрицательный тест на собственный вывод

Мой «НЕТ» окажется ложным, если зависимость идёт не через исполняемый файл, а через **общие данные**:
(а) резолвер провалится мимо PATH — и полоса поедет на бинаре из каталога данных пакета, который
MSIX-удаление/сброс сносит вместе с пакетом (вектор доказан живым логом 22.07, см. выше);
(б) `~/.claude/.credentials.json` — **один файл на Desktop и на CLI** (7064 байта, изменён 26.08 09:48:52,
не читался); если подписочный токен обновляет именно Desktop, снос приложения убьёт авторизацию
полосы 328, не тронув ни одного её exe — **это я не проверял, исход НЕИЗВЕСТНО**;
(в) наблюдения «полоса пережила пакет» есть только для *обновления и принудительного гашения*
(сегодня, дважды); полосы с **удалённым** пакетом никто не наблюдал — там мой ответ выведен из
устройства, а не из замера.

---

## ЧТО БЫЛО НА САМОМ ДЕЛЕ (ШАГ 5): обновление ШЛО, и исход не «неизвестно»

Журналы **не провернулись**: в `AppXDeploymentServer/Operational` за 48 ч лежит **1196 событий**,
самое раннее — 24.08 12:39:31. Окно инцидента восстановлено целиком.

| время (26.08) | что произошло |
|---|---|
| **01:28:47** | старт Add: `Claude-61bfd4a1….msix`, опции `NormalPriorityRequest` и **`DeferRegistrationWhenPackagesAreInUse`** |
| 01:28:52 | `1.34493.1.0` → `1.37937.0.0`, источник `downloads.claude.ai/releases/win32/x64/1.37937.0/` |
| **01:29:31** | Id=638 «Packages were not updated because affected apps are still running. Running apps: {Claude_pzs8sxrjxfjjc!Claude}» + Id=658 **отложенная регистрация** |
| **01:40:02** | Register **с `ForceApplicationShutdownOption`** → 01:40:03 успех |
| **01:40:04** | **ЗАМОК ФАЙЛА:** Id=471 `error 0x12C: Deleting file \\?\C:\Program Files\WindowsApps\Deleted\Claude_1.34493.1.0_…\app\icudtl.dat failed` и то же по `resources.pak` |
| 02:33–08:40 | тот же отказ удаления повторяется **17 раз парами** (icudtl.dat + resources.pak), последний 08:40:20 |
| **08:40:05** | старт второго Add: `Claude-edbd3c34….msix`, `1.37937.0.0` → **`1.37937.1.0`** |
| **08:40:20** | снова Id=638 + Id=658: «Marking package {Claude_1.37937.1.0} for deferred registration because {Claude_1.37937.0.0} **is still running**» |
| **08:50:51** | Register c `ForceApplicationShutdownOption` → 08:50:52 успех |
| **11:45:30** | Register `ForceTargetApplicationShutdownOption,RepairAppRegistrationOption`, Id=649 «**Trying to repair ACLs** … ACLs repaired successfully … Register next time should succeed» |
| **11:46:39** | **та же починка ACL повторно**, через 69 секунд |
| **11:46:47** | владелец перезагружает ПК руками (Id=1074, StartMenuExperienceHost.exe, on behalf of mxfillpc\mxfill1) |
| 11:47:04 | загрузка; 11:47:20 Desktop поднялся уже на `1.37937.1.0` |
| 11:53:02 | вотчдог: «процессов демона НЕТ — поднимаю через schtasks»; 11:53:04 демон стартовал, commit=2d66cf0 |

**Механизм замка назван.** В каталоге пакета живёт **служба Windows**:

```
Name        : CoworkVMService
DisplayName : Claude
State       : Running
StartMode   : Auto
PathName    : "C:\Program Files\WindowsApps\Claude_1.37937.1.0_x64__pzs8sxrjxfjjc\app\resources\cowork-svc.exe"
```

и её собственные предупреждения в `Application` (26.08 01:40:02, 01:40:04, 08:50:51, 08:50:52,
11:45:30, 11:45:31, 11:46:40, 11:47:18) прямо говорят, почему замок не отпускался:

```
Claude VM Service: failed to disarm SCM recovery actions for this stop; if the stop overruns,
the service may be auto-restarted during package servicing: open service: Access is denied.
Claude VM Service: failed to configure SCM recovery actions; if the service crashes it will not
restart until the machine reboots: open service: Access is denied.
```

Автозапускающаяся служба, чей бинарь лежит ВНУТРИ каталога пакета, не смогла снять себе
SCM-recovery на время обслуживания пакета — то есть могла быть поднята обратно прямо в момент
подмены каталога. Это и есть материальная причина, по которой две подмены подряд ушли в
`deferred registration`, а два файла старой версии так и не удалились.

**Остаточный след жив до сих пор:** Id=493 «There were 25 additional files that failed to be
deleted under the folder `\\?\C:\Program Files\WindowsApps\Deleted`» повторяется каждые ~6 минут
и после перезагрузки — 11:53:13, 11:59:13, 12:05:13, 12:11:13. Счётчик за сутки гулял 25 → 27 → 29 → 27 → 25.

---

## ШАГ 1. Цепочка собственного процесса до корня (дословно)

```
PID   : 15900
PPID  : 9628
Name  : powershell.exe
Path  : C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe
Start : 26.08.2026 12:34:22

PID   : 9628
PPID  : 13496
Name  : claude.exe
Path  : C:\Users\mxfill1\.local\bin\claude.EXE
Start : 26.08.2026 12:33:50

PID   : 13496
PPID  : 1836
Name  : python.exe
Path  : D:\turbobaby-bot\venv\Scripts\python.exe
Start : 26.08.2026 11:53:02

PID   : 1836
PPID  : 1156
Name  : svchost.exe
Path  :
Start : 26.08.2026 11:47:10

PID   : 1156
PPID  : 784
Name  : services.exe
Path  :
Start : 26.08.2026 11:47:09

PID   : 784
PPID  : 996
Name  : wininit.exe
Path  :
Start : 26.08.2026 11:47:09

PID   : 996
PPID  : 1156
Name  : svchost.exe
Path  :
Start : 26.08.2026 11:47:10
```

Командные строки ключевых звеньев:

```
PID=13496 PPID=1836 NAME=python.exe START=08/26/2026 11:53:02
  EXE: D:\turbobaby-bot\venv\Scripts\python.exe
  CMD: "D:\turbobaby-bot\venv\Scripts\python.exe" D:\turbobaby-bot\pc_orchestrator.py

PID=1836 PPID=1156 NAME=svchost.exe START=08/26/2026 11:47:10
  EXE:
  CMD:
```

**Ответ ШАГА 1: ни одно звено не лежит внутри каталога пакета Claude в WindowsApps.**
Хвост цепочки (996 → 1156) — переиспользованный PID: настоящий родитель `wininit.exe` (smss.exe)
давно мёртв, обход остановлен замком от зацикливания. Корень цепочки полосы 328 — **Планировщик
заданий** (`svchost.exe` PID 1836, служба `Schedule`), а не оболочка и не Desktop.

## ШАГ 2. Процессы ПО ПУТИ каталога пакета (дословно, сокращено до Claude)

Корень приложения и 10 его детей — все из каталога установки:

```
ProcessId : 13172   ParentProcessId : 3624 (C:\WINDOWS\Explorer.EXE)
ExecutablePath : C:\Program Files\WindowsApps\Claude_1.37937.1.0_x64__pzs8sxrjxfjjc\app\Claude.exe
CommandLine    : "C:\Program Files\WindowsApps\Claude_1.37937.1.0_x64__pzs8sxrjxfjjc\app\Claude.exe"
CreationDate   : 26.08.2026 11:47:20

дети того же пути (PPID=13172): 13344 (11:47:21), 14484 / 14516 / 14848 / 15104 (11:47:25),
6164 / 12908 (11:47:29), 4952 / 15528 (12:06:26)
```

Дети Desktop'а **вне** каталога установки, но внутри данных пакета:

```
PID 3156  PPID 13172  C:\Users\mxfill1\AppData\Roaming\Claude\claude-code\2.1.246\claude.exe  12:12:13
PID 17036 PPID 13172  C:\Users\mxfill1\AppData\Roaming\Claude\claude-code\2.1.246\claude.exe  12:12:48
PID 15988 PPID 7336   C:\Users\mxfill1\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\ChromeNativeHost\chrome-native-host.exe  12:06:36
```

Процессы полосы 328 и RC — **другой путь, другой родитель**:

```
PID 9628  PPID 13496 (демон)          C:\Users\mxfill1\.local\bin\claude.EXE   ← исполнитель ЭТОЙ задачи
PID 6120  PPID 13968 (rc_supervisor)  C:\Users\mxfill1\.local\bin\claude.EXE   rc --debug-file …
PID 11352 PPID 13968 (rc_supervisor)  C:\Users\mxfill1\.local\bin\claude.EXE   --remote-control turbobaby-pc …
PID 5916  PPID 6120                   C:\Users\mxfill1\.local\bin\claude.exe   --print --sdk-url … cse_01EXDKihKQWtHsTcoSxCDHHq
PID 13968 PPID 8540                   D:\turbobaby-bot\venv\Scripts\python.exe  "D:\turbobaby-bot\rc_supervisor.py"
```

Примечание о путях-двойниках: `C:\Users\mxfill1\AppData\Roaming\Claude` **физически не существует**
(`Test-Path` → ABSENT). Путь у PID 3156/17036 — MSIX-редирект, реальные файлы лежат в
`…\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code` (EXISTS, LastWrite
26.08 11:47:59). Ровно эту разницу и лечит комментарий в `pc_orchestrator._claude_base_dirs()`.

## ШАГ 3. Установленные пакеты Claude

```
Name                   : Claude
PackageFullName        : Claude_1.37937.1.0_x64__pzs8sxrjxfjjc
Version                : 1.37937.1.0
InstallLocation        : C:\Program Files\WindowsApps\Claude_1.37937.1.0_x64__pzs8sxrjxfjjc
Status                 : Ok
PackageUserInformation : {}
IsDevelopmentMode      : False
SignatureKind          : Developer
```

`Get-AppxPackage -Name *Anthropic*` → пусто.

**Сравнение с эталоном 1.37937.1.0: РАВНА.** Ни выше, ни ниже — совпадает точно.

**Следы других версий — ЕСТЬ, четыре штуки** (сняты из журнала, т.к. прямой листинг каталога
запрещён ACL: `Get-ChildItem 'C:\Program Files\WindowsApps'` → `Access to the path … is denied`,
`…\Deleted` → `Access is denied`):

| версия | след |
|---|---|
| `1.28929.0.0` | Id=1230: «These hardlinks did not have packages in repository: \Program Files\WindowsApps\Claude_1.28929.0.0_x64__pzs8sxrjxfjjc\app\…» — осиротевшие хардлинки без пакета в репозитории |
| `1.30096.0.0` | то же событие, тот же список |
| `1.34493.1.0` | застряла в `WindowsApps\Deleted\Claude_1.34493.1.0_x64__pzs8sxrjxfjjc557a7295-…`, два файла (`app\icudtl.dat`, `app\resources.pak`) не удаляются с 01:40:04 |
| `1.37937.0.0` | промежуточная, прожила 01:40 → 08:50, вытеснена; в `Get-AppxPackage` её уже нет |

Недоустановленной **текущей** версии нет: `1.37937.1.0` имеет `Status : Ok`.

Отдельный, к Claude не относящийся отказ развёртывания в том же окне (не путать): пакет
`MdOdrMcpFilterPackage_1.0.0.0_neutral__cw5n1h2txyewy` — `0x80073CF9` (24.08 22:21:52, 25.08 23:17:21)
и `0x80073D0B` «уже установлен с другим внешним расположением» (26.08 11:47:15, уже после загрузки).

## ШАГ 4. Журналы Windows за 48 часов

Окно замера: **24.08.2026 12:36:36 → 26.08.2026 12:36:36**.

### Перезагрузки и инициатор (`System`, Id 1074/1076/6005/6006/6008/41/109) — ровно одна

```
--- 2026-08-26 11:46:47 | Id=1074 | User32
The process C:\WINDOWS\SystemApps\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy\StartMenuExperienceHost.exe
(MXFILLPC) has initiated the Перезапустить of computer MXFILLPC on behalf of user mxfillpc\mxfill1
for the following reason: Другое (Незапланированное)
 Reason Code: 0x0
 Shutdown Type: Перезапустить
 Comment:

--- 2026-08-26 11:46:52 | Id=6006 | EventLog
The Event log service was stopped.

--- 2026-08-26 11:46:53 | Id=109 | Microsoft-Windows-Kernel-Power
The kernel power manager has initiated a shutdown transition.
Action: Power Action Reboot
Event Code: 0x0
Reason: Kernel API

--- 2026-08-26 11:47:10 | Id=6005 | EventLog
The Event log service was started.
```

Маркер подтверждён: **StartMenuExperienceHost + «on behalf of user mxfill1» = перезагрузка руками
владельца через меню Пуск**, не обновление Windows, не аварийное завершение (6008 нет, 41 нет).
`LastBootUpTime: 08/26/2026 11:47:04`.

### Развёртывание пакетов (`AppXDeploymentServer/Operational`)

Всего 1196 событий за 48 ч; со словом Claude — **184**, из них не-Verbose — **84**. Ключевые
приведены в таблице ШАГА 5 выше. Ошибок уровня Error по пакету Claude — **ноль**; всё, что было,
прошло как `Warning` (Id=658 отложенная регистрация, Id=1230 осиротевшие хардлинки, Id=493
неудалённые файлы) и `Information` (Id=471 отказ удаления, Id=638 «apps are still running»).
Все `Error` в логе за окно принадлежат `MdOdrMcpFilterPackage`, `Microsoft.WindowsStore` и
`Microsoft.StorePurchaseApp`, а не Claude.

### Ошибки приложения Claude (`Application`, уровни 1–3)

Найдено **8**, все — `CoworkVMService`, все `Warning`, тексты приведены в разделе «Механизм замка»
выше. Крэшей `Application Error` / `.NET Runtime` по Claude.exe за 48 ч **нет**.

### События среды приложений (`AppModel-Runtime/Admin`)

1019 событий за окно; по содержанию — рутинные Id=210/211/217 (создание Desktop AppX-контейнеров и
добавление в них процессов) для WindowsTerminal, Client.CBS, WindowsStore. Аномалий по Claude нет.

---

## Сводка чисел

| что | число |
|---|---|
| звеньев цепочки исполнителя внутри WindowsApps | **0 из 7** |
| совпадений `WindowsApps\|Claude_pzs8sxrjxfjjc\|CoworkVMService` в `*.py` репо | **0** |
| процессов из каталога установки пакета | **11** (все — Desktop, ни одного из полосы 328) |
| версия пакета против эталона 1.37937.1.0 | **равна** |
| следов иных версий | **4** (1.28929.0.0, 1.30096.0.0, 1.34493.1.0, 1.37937.0.0) |
| событий AppX за 48 ч / со словом Claude / не-Verbose | **1196 / 184 / 84** |
| перезагрузок за 48 ч | **1**, руками владельца, 11:46:47 |
| обновлений пакета в окне инцидента | **2** (01:28→01:40, 08:40→08:50), оба через `deferred registration` |
| отказов удаления файлов старой версии (Id=471, парами) | **17 пар**, 01:40:04 → 08:40:20 |
| часов, в которые демон писал лог сквозь окно | 01, 03, 05, 06, 07, 11 — **обороты не прерывались** |

## Остатки (не сделано осознанно)

- Каталоги `WindowsApps` и `WindowsApps\Deleted` листингом не сняты — ACL отказывает текущей
  сессии; состав версий восстановлен по журналу. Снятие потребовало бы повышения.
- `~/.claude/.credentials.json` **не читался** (файл секретов) — поэтому вопрос «обновляет ли
  подписочный токен именно Desktop» остаётся **НЕИЗВЕСТНО**, см. ШАГ 7(б).
- Поведение полосы 328 при **удалённом** пакете не наблюдалось: за окно пакет обновлялся и
  гасился, но не сносился.
