# Сработал ли автологон на загрузке 18.08.2026 20:27 — вывод задним числом

**ВЫВОД: СРАБОТАЛ.** Между стартом системы и началом сеанса — **5.004 секунды**. Ручного ввода
учётных данных в этот момент не было: жест Windows Hello (ПИН) в журнале один, и он на **120.1 с
ПОЗЖЕ** уже начавшегося сеанса — то есть это **разблокировка**, а не вход.

Сессия только читала. Ни реестр, ни Планировщик, ни процессы, ни база не трогались; пароль не
запрашивался, не читался и нигде не печатается. Замер: 18.08.2026 20:36–20:49 (+07:00),
учётная запись `mxfillpc\mxfill1`, **сессия НЕ повышена** (`IsInRole(Administrator) = False`).

---

## 0. Почему признак «стёрся» — и почему он на самом деле не стирался

Владелец увидел после перезагрузки экран блокировки и ввёл ПИН, решив, что автологон не сработал.
Это ожидаемо и **было предсказано в собственной инструкции** —
[`docs/artifacts/2026-08-18-autologon-howto.md:299`](2026-08-18-autologon-howto.md):

> **Не пугайся:** после автоматического входа экран будет **заперт** — это не отказ, так и задумано
> (задача `tb_lock_on_logon` запирает его через 5 секунд). Понять, сработало ли, по виду экрана
> **нельзя**.

Экран запирает **своя** задача Планировщика `\tb_lock_on_logon` (LogonTrigger, `Delay=PT5S`,
`LastRun 18.08 20:27:46 Result=0`) скриптом `lock_on_logon.vbs`. Поэтому «увидел ПИН-экран» не
является признаком отказа **ни в какую сторону**. Но по виду экрана — нельзя, а **по журналу
событий — можно**, и признак никуда не делся.

---

## 1. Переключатель автоматического входа в реестре сейчас — ВКЛЮЧЁН

Дословный вывод (`Get-ItemProperty` + `GetValueNames()`, только чтение):

```
AutoAdminLogon = [1]
DefaultUserName = [mxfill1]
DefaultDomainName = [MXFILLPC]
AutoLogonCount present = False
AutoLogonSID = [S-1-5-21-3727781399-3432839651-1010305476-1002]
ForceAutoLogon = []
--- value names present under Winlogon ---
AutoAdminLogon
AutoLogonSID
AutoRestartShell
Background
CachedLogonsCount
DebugServerCommand
DefaultDomainName
DefaultUserName
DisableBackButton
DisableCad
DisableLockWorkstation
EnableFirstLogonAnimation
EnableSIHostIntegration
ForceUnlockLogon
LastLogOffEndTimePerfCounter
LastUsedUsername
LegalNoticeCaption
LegalNoticeText
PasswordExpiryWarning
PowerdownAfterShutdown
PreCreateKnownFolders
ReportBootOk
scremoveoption
Shell
ShellAppRuntime
ShellCritical
ShellInfrastructure
ShutdownFlags
SiHostCritical
SiHostReadyTimeOut
SiHostRestartCountLimit
SiHostRestartTimeGap
USERINIT
VMApplet
WinStationsDisabled
```

Три факта, каждый со смыслом:

| факт | что значит |
|---|---|
| `AutoAdminLogon = 1` | переключатель **включён** |
| `DefaultUserName = mxfill1`, `DefaultDomainName = MXFILLPC` | цель — **та самая** локальная учётка (её профиль `C:\Users\mxfill1`, её SID `…-1002`) |
| `AutoLogonCount` **отсутствует** | автологон **не одноразовый**: при значении `>0` Windows отсчитывает входы и сама гасит переключатель. Его нет ⇒ вход будет повторяться на каждой загрузке |

---

## 2. Пароль в шифрохранилище системы — ПРЯМО ПРОВЕРИТЬ НЕЛЬЗЯ, косвенно ДОКАЗАН

**Открытым текстом в реестре его НЕТ** — и это правильно. Дословно:

```
=== DefaultPassword value present under Winlogon? ===
False
```

**Прямая проба хранилища LSA не удалась** — и не из-за прав администратора, а по устройству ACL:
ветка `HKLM\SECURITY` открыта только `SYSTEM`. Дословно:

```
=== LSA secret store HKLM:\SECURITY\Policy\Secrets (presence only) ===
ERROR: System.Security.SecurityException :: Requested registry access is not allowed.
=== HKLM:\SECURITY readable at all? ===
ERROR: Requested registry access is not allowed.
```

Повышение прав тут **не помогло бы**: администратору эта ветка тоже закрыта, открыть её можно
только сменив владельца — а это правка, которая задачей запрещена. Поэтому наличие доказывается
**функционально, без единого касания к самому секрету**:

1. Способ вооружения объявлен в `autologon_arm.ps1` и описан в howto:
   `LsaStorePrivateData("DefaultPassword", …)` — «тот же системный вызов, которым пользуется
   Sysinternals `Autologon.exe`» (howto §2), плюс скрипт **сносит** `DefaultPassword` из реестра.
   Замеренное состояние реестра ровно такое: имени нет.
2. `AutoAdminLogon=1` **без** доступного пароля не входит, а **проваливается** в обычный экран
   логона. Именно так вела себя машина утром (§3, контроль).
3. Вечером вход состоялся автоматически за 5.0 с и **без жеста Hello** (§5). Пароль винлогон взял
   откуда-то — а в реестре его нет. Остаётся хранилище LSA.

**Итог: лежит.** Значение не читалось, не печаталось и никуда не писалось.

---

## 3. Старт системы, начало сеанса, разница — 5.004 с

### Дословный вывод — старт системы (`Microsoft-Windows-Kernel-General`, ID 12)

```
2026-08-18 20:27:34.852 | 12 | The operating system started at system time ‎2026‎-‎08‎-‎18T13:27:34.500000000Z.
2026-08-18 04:02:10.853 | 12 | The operating system started at system time ‎2026‎-‎08‎-‎17T21:02:10.500000000Z.
2026-08-18 04:01:17.105 | 12 | The operating system started at system time ‎2026‎-‎08‎-‎17T21:01:16.500000000Z.
2026-08-06 19:37:16.106 | 12 | The operating system started at system time ‎2026‎-‎08‎-‎06T12:37:15.500000000Z.
2026-07-30 01:16:53.860 | 12 | The operating system started at system time ‎2026‎-‎07‎-‎29T18:16:53.500000000Z.
```

### Дословный вывод — начало сеанса (`Microsoft-Windows-Winlogon`, ID 7001/7002, System)

```
2026-08-18 20:27:39.848 | ID=7001 | User Logon Notification for Customer Experience Improvement Program
2026-08-18 20:27:17.488 | ID=7002 | User Logoff Notification for Customer Experience Improvement Program
2026-08-18 14:08:50.117 | ID=7001 | User Logon Notification for Customer Experience Improvement Program
2026-08-18 04:00:08.372 | ID=7002 | User Logoff Notification for Customer Experience Improvement Program
2026-08-06 19:38:27.458 | ID=7001 | User Logon Notification for Customer Experience Improvement Program
```

### Дословный вывод — второй, независимый источник времени сеанса (`User Profile Service/Operational`)

```
2026-08-18 20:27:39.990 | ID=2  | Finished processing user logon notification on session 1.
2026-08-18 20:27:39.915 | ID=67 | Logon type: Regular Local profile location: C:\Users\mxfill1 Profile type: Regular
2026-08-18 20:27:39.912 | ID=5  | Registry file …\UsrClass.dat is loaded at HKU\S-1-5-21-…-1002_Classes.
2026-08-18 20:27:39.883 | ID=5  | Registry file C:\Users\mxfill1\ntuser.dat is loaded at HKU\S-1-5-21-…-1002.
2026-08-18 20:27:39.856 | ID=1  | Recieved user logon notification on session 1.
2026-08-18 20:27:17.483 | ID=4  | Finished processing user logoff notification on session 1.
2026-08-18 20:27:17.463 | ID=3  | Recieved user logoff notification on session 1.
2026-08-18 14:08:50.314 | ID=2  | Finished processing user logon notification on session 1.
2026-08-18 14:08:50.122 | ID=1  | Recieved user logon notification on session 1.
```

### Разница

| величина | значение |
|---|---|
| старт системы (событие 12, `TimeCreated`) | **2026-08-18 20:27:34.852** |
| старт системы (по тексту самого события, UTC→местное) | 2026-08-18 20:27:34.500 |
| начало сеанса (Winlogon 7001) | **2026-08-18 20:27:39.848** |
| начало сеанса (User Profile Service ID=1) | **2026-08-18 20:27:39.856** |
| **разница** | **5.004 с** (по 7001 — 4.996 с; по тексту события 12 — 5.356 с) |

Третий независимый свидетель того же старта — служба журнала: `6005 The Event log service was
started` в 20:27:37.673 и рядом `6013 The system uptime is 3 seconds`, что даёт старт ≈20:27:34.67.

### КОНТРОЛЬ на той же машине в тот же день: как выглядит РУЧНОЙ вход

Утренняя перезагрузка была **обновленческой** — вот дословно:

```
2026-08-18 04:00:05.844 | ID=1074 | The process C:\WINDOWS\uus\AMD64\MoUsoCoreWorker.exe (MXFILLPC) has initiated the Перезапустить of computer MXFILLPC on behalf of user NT AUTHORITY\СИСТЕМА for the following reason: Операционная система: Установка пакета обновления (Запланирован Reason Code: 0x80020010
2026-08-18 04:01:17.105 | ID=12   | The operating system started at system time 2026-08-17T21:01:16.5Z
2026-08-18 04:01:57.048 | ID=1074 | The process C:\WINDOWS\servicing\TrustedInstaller.exe (MXFILLPC) has initiated the Перезапустить … Операционная система: Обновление (Запланированное) Reason Code: 0x80020003
2026-08-18 04:02:10.853 | ID=12   | The operating system started at system time 2026-08-17T21:02:10.5Z
```

Старт 04:02:10.853 → сеанс 14:08:50.122. **Разница 36 399.27 с (10 ч 06 мин 39 с).** Вот так
выглядит «человек дошёл до машины». Разрыв между двумя загрузками одного дня — 5.0 с против
36 399 с, то есть в **7273 раза**; выбирать между ними не приходится.

А вечерняя перезагрузка — своя, руками:

```
2026-08-18 20:27:15.899 | ID=1074 | The process …\StartMenuExperienceHost.exe (MXFILLPC) has initiated the Перезапустить of computer MXFILLPC on behalf of user mxfillpc\mxfill1 … Shutdown Type: Перезапустить
2026-08-18 20:27:19.668 | ID=6006 | The Event log service was stopped.
2026-08-18 20:27:20.826 | ID=109  | The kernel power manager has initiated a shutdown transition. Action: Power Action Reboot
2026-08-18 20:27:37.673 | ID=6005 | The Event log service was started.
2026-08-18 20:27:37.673 | ID=6013 | The system uptime is 3 seconds.
```

---

## 4. Старт оркестратора и клиентских детей относительно старта системы

Дословный вывод (`Win32_Process`, `CreationDate`, отсортировано):

```
2026-08-18 20:27:37.015 | PID=1052  | PPID=664   | winlogon.exe |
2026-08-18 20:27:40.901 | PID=8392  | PPID=1784  | python.exe | "D:\turbobaby-bot\venv\Scripts\python.exe" pc_agent.py
2026-08-18 20:27:41.266 | PID=3720  | PPID=8564  | explorer.exe | C:\WINDOWS\Explorer.EXE
2026-08-18 20:27:47.983 | PID=13288 | PPID=8500  | python.exe | "…\python.exe" "D:\turbobaby-bot\rc_supervisor.py"
2026-08-18 20:27:55.544 | PID=13736 | PPID=8392  | python.exe | …\python.exe D:\turbobaby-bot\userbot_listen.py
2026-08-18 20:27:58.031 | PID=5240  | PPID=13288 | claude.exe | …\claude.EXE --remote-control turbobaby-pc …
2026-08-18 20:27:58.031 | PID=4044  | PPID=13288 | claude.exe | …\claude.EXE rc …
2026-08-18 20:30:35.360 | PID=2996  | PPID=1052  | LogonUI.exe |
2026-08-18 20:33:03.221 | PID=15988 | PPID=1784  | python.exe | "…\python.exe" D:\turbobaby-bot\pc_orchestrator.py
2026-08-18 20:33:52.581 | PID=10948 | PPID=15988 | python.exe | …\python.exe D:\turbobaby-bot\moderation_bot.py
```

(`PID=1784` — `svchost.exe`, то есть Планировщик; `PID=8500` — `wscript.exe //B //Nologo
"D:\turbobaby-bot\rc_remote_control.vbs"`.)

| процесс | PID | старт | от старта системы | от начала сеанса | от ПИН-а (20:29:39.996) |
|---|---|---|---|---|---|
| `pc_agent.py` | 8392 | 20:27:40.901 | **+6.05 с** | +1.05 с | **−119.10 с** |
| `rc_supervisor.py` | 13288 | 20:27:47.983 | **+13.13 с** | +8.13 с | **−112.01 с** |
| `userbot_listen.py` | 13736 | 20:27:55.544 | **+20.69 с** | +15.69 с | **−104.45 с** |
| RC-консоль (`claude.exe` ×2) | 5240 / 4044 | 20:27:58.031 | +23.18 с | +18.18 с | −101.97 с |
| `pc_orchestrator.py` | 15988 | 20:33:03.221 | **+328.37 с** | +323.37 с | +203.23 с |
| `moderation_bot.py` | 10948 | 20:33:52.581 | +377.73 с | +372.73 с | +252.59 с |

**Это третье, самостоятельное доказательство автологона.** `\pc_agent` и `\TurboBabyRC` — задачи с
триггером **LogonTrigger**; без входа в систему они не срабатывают ФИЗИЧЕСКИ. Дословно из
`Get-ScheduledTask` (только чтение):

```
--- \pc_agent | State=Running | User=mxfill1 | RunLevel=Limited
      TRIGGER: MSFT_TaskLogonTrigger | Delay= | Enabled=True
      ACTION : D:\turbobaby-bot\venv\Scripts\python.exe pc_agent.py
      LastRun=08/18/2026 20:27:40 | LastResult=267009
--- \pc_orchestrator | State=Running | User=mxfill1 | RunLevel=Limited
      TRIGGER: MSFT_TaskTimeTrigger | Delay= | Enabled=True
      ACTION : …\python.exe D:\turbobaby-bot\pc_orchestrator.py
      LastRun=08/18/2026 20:33:03 | LastResult=267009 | NextRun=01/01/2099 00:00:00
--- \pc_orchestrator_watchdog | State=Ready | User=mxfill1 | RunLevel=Limited
      TRIGGER: MSFT_TaskTimeTrigger | Repetition=PT5M | Enabled=True
      ACTION : …\python.exe D:\turbobaby-bot\pc_orchestrator.py --watchdog
      LastRun=08/18/2026 20:43:01 | LastResult=0 | NextRun=08/18/2026 20:48:00
--- \tb_lock_on_logon | State=Ready | User=mxfill1 | RunLevel=Limited
      TRIGGER: MSFT_TaskLogonTrigger | Delay=PT5S | Enabled=True
      ACTION : C:\Windows\System32\wscript.exe //B //Nologo "D:\turbobaby-bot\lock_on_logon.vbs"
      LastRun=08/18/2026 20:27:46 | LastResult=0
--- \TurboBabyRC | State=Running | User=mxfill1 | RunLevel=Limited
      TRIGGER: MSFT_TaskLogonTrigger | Delay= | Enabled=True
      ACTION : C:\Windows\System32\wscript.exe //B //Nologo "D:\turbobaby-bot\rc_remote_control.vbs"
      LastRun=08/18/2026 20:27:40 | LastResult=267009
```

Логон-триггерные задачи отработали в 20:27:40 и 20:27:46 — **за две минуты до того, как кто-либо
предъявил машине хоть одну учётку**. Сеанс, в котором это произошло, создан не человеком.

### Почему оркестратор пришёл на 5.5 минуты позже — и почему это НЕ отказ

Оркестратор **не логон-триггерный вовсе**. Его поднимает пятиминутный вотчдог, дословно из
`pc_orchestrator.log`:

```
7284:2026-08-18 20:33:03,190 WARNING watchdog: продукта нет (тишина продукта 300с из 1800с) и процессов демона НЕТ — поднимаю через schtasks
7285:2026-08-18 20:33:03,221 WARNING watchdog: schtasks /Run /TN pc_orchestrator → rc=0 | …
7286:2026-08-18 20:33:03,606 WARNING singleton: устаревший лок (PID 7336 мёртв) — забираю
7288:2026-08-18 20:33:04,659 INFO === ДЕМОН СТАРТ (lane=pc, poll=60s, task_timeout=2700s, approval_ttl=2760s, claude=C:\Users\mxfill1\.local\bin\claude.EXE, commit=538bbbd) ===
```

Тики вотчдога стоя́т на стенных часах с шагом 5 мин (по логу: 20:33:03, 20:38:03, 20:43:03,
20:48:03). Загрузка в 20:27:34 попала между тиками, первый после неё — 20:33:03. Отсюда +328 с.

**Здесь же — мина в собственной инструкции.** Howto §«ЧТО ПРОВЕРИТЬ» (строки 300–301) велит ждать
7 минут и признаком успеха называет строки `pc_orchestrator.log` **раньше момента разблокировки**.
Владелец разблокировал на 2-й минуте — и по этому признаку получил бы **ложное «не сработал»**:
оркестратор объявился в 20:33:03, то есть на 203 с ПОЗЖЕ ПИН-а. Признак не врёт только при полном
7-минутном ожидании, и запас у него всего ~1.5 мин (5.5 мин худшего случая против 7 обещанных).
Правильный признак — **логон-триггерные дети** (`pc_agent`/`TurboBabyRC`/userbot): они встают на
+6…+21 с и от фазы вотчдога не зависят. Кода не правил — называю как находку.

---

## 5. Событие ручного ввода учётных данных за это окно — В МОМЕНТ ВХОДА НЕТ

Проверялось двумя журналами, доступными без повышения прав.

### `Microsoft-Windows-Winlogon/Operational` — весь день 18.08 (без шума 811/812), дословно

```
2026-08-18 14:08:50.083 | ID=1 | Authentication started.
2026-08-18 14:08:50.106 | ID=2 | Authentication stopped. Result 0
2026-08-18 20:27:39.768 | ID=1 | Authentication started.
2026-08-18 20:27:39.800 | ID=2 | Authentication stopped. Result 0
```

Вечерняя аутентификация **началась через 4.916 с после старта системы и заняла 32 мс**.

### `Microsoft-Windows-HelloForBusiness/Operational` — весь день 18.08, дословно

```
2026-08-18 02:28:16.487 | ID=5002 | A user is signing into the device with the following gesture information: Type: Invalid Subtype: No Bio
2026-08-18 02:28:16.768 | ID=5001 | A user signed into the device with the following information: Username: СИСТЕМА User SID: S-1-5-18 Credential Type: Software Key Deployment Type: Key Trust
2026-08-18 04:01:35.382 | ID=8002 | Successfully loaded an existing hardware Windows Hello container. …
2026-08-18 04:01:35.708 | ID=8025 | The Microsoft Passport Container service started successfully.
2026-08-18 04:01:38.065 | ID=8025 | The Microsoft Passport service started successfully.
2026-08-18 04:01:38.171 | ID=5000 | TPM Manufacturer: Intel Version: 2.0 …
2026-08-18 04:02:24.275 | ID=8002 | Successfully loaded an existing hardware Windows Hello container. …
2026-08-18 04:02:24.572 | ID=8025 | The Microsoft Passport Container service started successfully.
2026-08-18 04:02:27.660 | ID=8025 | The Microsoft Passport service started successfully.
2026-08-18 04:02:27.781 | ID=5000 | TPM Manufacturer: Intel Version: 2.0 …
2026-08-18 14:08:49.993 | ID=5002 | A user is signing into the device with the following gesture information: Type: Invalid Subtype: No Bio
2026-08-18 14:08:50.115 | ID=5001 | A user signed into the device with the following information: Username: СИСТЕМА User SID: S-1-5-18 …
2026-08-18 20:17:55.679 | ID=5002 | A user is signing into the device with the following gesture information: Type: Invalid Subtype: No Bio
2026-08-18 20:17:55.894 | ID=5001 | A user signed into the device with the following information: Username: СИСТЕМА User SID: S-1-5-18 …
2026-08-18 20:24:39.631 | ID=5002 | A user is signing into the device with the following gesture information: Type: Invalid Subtype: No Bio
2026-08-18 20:27:38.799 | ID=8025 | The Microsoft Passport service started successfully.
2026-08-18 20:27:39.329 | ID=5000 | TPM Manufacturer: Intel Version: 2.0 Firmware Version: 403.1.0.0 Is Ready: true
2026-08-18 20:27:39.367 | ID=8002 | Successfully loaded an existing hardware Windows Hello container. ID: {15d33f0c-…} Has Cached Logon Key: true State: Okay
2026-08-18 20:27:39.730 | ID=8025 | The Microsoft Passport Container service started successfully.
2026-08-18 20:29:39.800 | ID=5002 | A user is signing into the device with the following gesture information: Type: Invalid Subtype: No Bio
2026-08-18 20:29:39.996 | ID=5001 | A user signed into the device with the following information: Username: СИСТЕМА User SID: S-1-5-18 Credential Type: Software Key Deployment Type: Key Trust
```

Читается это так. **5002 = человек предъявил жест (ПИН), 5001 = вход по жесту состоялся.** Пара
5002+5001 встречается ровно там, где человек действительно трогал машину: 02:28 (разблокировка),
**14:08:49.993 — ровно на ручном входе после обновленческой перезагрузки**, 20:17:55
(разблокировка), 20:29:39 (разблокировка). Одинокая 5002 в 20:24:39 без пары — жест, не давший
входа, за 2.5 мин до перезагрузки.

**В 20:27:39 пары 5002/5001 НЕТ.** Всё, что там есть, — запуск служб Passport, проба TPM и
загрузка контейнера Hello, то есть инфраструктура, а не человек. `Username: СИСТЕМА / S-1-5-18` в
5001 — контекст записи провайдера (события пишет SYSTEM), а не имя входящего; различает эти
события не поле имени, а **сам факт наличия жеста**.

Итог: **ближайший к входу жест — в 20:29:39.800, то есть на 119.94 с ПОЗЖЕ момента, когда сеанс
уже существовал** (и на 118.9 с позже старта `pc_agent`). Это разблокировка запертого экрана, а не
создание сеанса. После 20:29:41 жестов больше нет ни одного — станция снова заперта с 20:30:35
(`LogonUI.exe PID=2996 created 2026-08-18 20:30:35.360`, жив на момент замера 20:49:36) и с тех пор
весь контур работает **на запертой машине**.

### Чего проверить НЕ удалось и почему это не меняет вывода

Журнал `Security` (события 4624 «Logon Process», 4800/4801 «блокировка/разблокировка») с
непривилегированной сессии не читается — дословно:

```
=== Security log readable? (4624 needs admin) ===
ERROR: Exception :: No events were found that match the specified selection criteria.
```

Он дал бы прямое имя механизма входа. Но вывод от него не зависит: он опирается на **5.004 с**,
на **отсутствие жеста в момент входа** и на **логон-триггерные задачи, отработавшие за 2 минуты до
единственной учётки**.

### Честный остаток: одна конкурирующая гипотеза и почему она отвергнута

Такую же картину (вход через секунды после старта + запертый экран) даёт **ARSO** — штатное
«автоматическое возобновление сеанса после ПОЛЬЗОВАТЕЛЬСКОЙ перезагрузки». Отличать её важно:
ARSO работает **только** после перезагрузки из живого сеанса и **не работает после обесточивания
или холодного пуска**, то есть даёт куда более слабую гарантию.

Против ARSO — три замера, все выше по тексту:

1. **Утренний контроль.** Перезагрузка 04:00–04:02 была **обновленческой** (MoUsoCoreWorker →
   TrustedInstaller от имени `NT AUTHORITY\СИСТЕМА`) — это ГЛАВНЫЙ сценарий ARSO, ради него она и
   сделана. Автоматического входа не случилось: сеанс начался через 10 ч 06 мин, **с жестом ПИН**.
   Значит ARSO на этой машине сеанса не поднимает.
2. **Что изменилось между 04:02 и 20:27** — ровно одно: владелец вооружил автологон
   (`AutoAdminLogon=1` + пароль в LSA). Про ARSO сегодня никто ничего не переключал.
3. **Реестровых следов включённой ARSO нет:** `AutoLogonChecked` — ABSENT, `ARSOUserConsent` —
   ABSENT, `UserARSOExclusionList` — ABSENT, политика `DisableAutomaticRestartSignOn` — ABSENT.

Разделить механизмы на 100% можно было бы полем «Logon Process» события 4624 — оно в закрытом
`Security`. Формулирую честно: **ARSO отвергнута тремя независимыми замерами, но не абсолютным
свидетелем.** Практическое следствие, которое из-за этого стоит проверить отдельно: поведение при
**холодном пуске** (обесточивание) — там ARSO не помогла бы, а `AutoAdminLogon` обязан сработать.
Ни одной перезагрузки ради этого не делалось.

---

## 6. ВЫВОД — **СРАБОТАЛ**

По правилу задачи: разница мала (**5.004 с**), признаков ручного ввода в этот момент нет.

Четыре независимых свидетеля одного и того же:

| # | свидетель | число |
|---|---|---|
| 1 | старт системы → начало сеанса | **5.004 с** (контроль того же дня — 36 399 с) |
| 2 | жест Hello в момент входа | **отсутствует**; ближайший — на **+119.94 с** и является разблокировкой |
| 3 | логон-триггерные задачи `\pc_agent`, `\TurboBabyRC` | отработали **20:27:40**, за **119 с ДО** единственной учётки |
| 4 | переключатель + хранилище | `AutoAdminLogon=1`, цель `MXFILLPC\mxfill1`, `AutoLogonCount` нет, пароля в реестре нет |

## 7. Причина отказа

Не требуется: отказа не было.

---

## ОТДЕЛЬНО: лок `moderation_bot` с мёртвым номером при свежем канале

### Что в файле СЕЙЧАС — номер ЖИВОЙ, аномалии больше нет

```
=== contents of lock files ===
moderation_bot.lock -> [10948]
pc_agent.lock -> [8392]
pc_orchestrator.lock -> [15988]
userbot.lock -> [13736]

=== precise mtimes ===
moderation_bot.lock  | mtime=2026-08-18 20:33:53.058650600 +0700 | size=5
pc_agent.lock        | mtime=2026-08-18 20:27:51.315754100 +0700 | size=4
pc_orchestrator.lock | mtime=2026-08-18 20:33:03.606080300 +0700 | size=5
userbot.lock         | mtime=2026-08-18 20:27:56.325319800 +0700 | size=5
moderation_ipc.db    | mtime=2026-08-18 20:42:44.764762900 +0700 | size=1814528
```

`PID=10948` — живой `moderation_bot.py`, создан 20:33:52.581, родитель — оркестратор 15988. Лок
написан через 0.477 с после старта процесса. Всё сходится.

### Какой номер там БЫЛ — **15760**, и когда файл был написан — **14:38:55**

Аномалия была реальной и жила в окне **20:27:34 → 20:33:53**. Мёртвый номер назван самим ботом
дословно (`moderation_bot.log`, строки 7112–7115):

```
2026-08-18 14:38:55,801 | устаревший moderation_bot.lock (PID 11968 мёртв) — забираю.
2026-08-18 14:38:56,511 | moderation_bot ЗАПУСК (TEST_MODE=True, mod_chat=-5031790861). Клиенту не пишу; решения — в IPC.
2026-08-18 20:33:53,058 | устаревший moderation_bot.lock (PID 15760 мёртв) — забираю.
2026-08-18 20:33:53,758 | moderation_bot ЗАПУСК (TEST_MODE=True, mod_chat=-5031790861). Клиенту не пишу; решения — в IPC.
```

| вопрос | ответ |
|---|---|
| номер из файла (в окне аномалии) | **15760** — экземпляр, поднятый **18.08 в 14:38:56** и убитый перезагрузкой 20:27 |
| время записи файла тогда | **2026-08-18 14:38:55** (лок пишется сразу после захвата у предыдущего мертвеца, PID 11968) |
| номер и время записи сейчас | **10948**, **2026-08-18 20:33:53.0586** |

Причина «мёртвого номера» — не сбой, а **устройство лока**: `moderation_bot.py:592-617` снимает
файл только через `release_lock()` в нормальном выходе; перезагрузка убивает процесс мимо этой
ветки, и файл переживает питание с номером покойника. Ровно то же случилось с оркестратором:
`singleton: устаревший лок (PID 7336 мёртв) — забираю` в 20:33:03.606. Заведомой чистки локов при
загрузке в контуре нет — забирает их следующий экземпляр, а он в тот раз пришёл только в 20:33.

### Кто на самом деле писал в канал

`moderation_ipc.db` — **общий** файл, и это записано в коде прямым текстом
(`expectations_pc.py:662-668`):

> Наблюдаемый факт — mtime ОБЩЕГО файла `moderation_ipc.db`, а писателей у него несколько:
> модербот тиком, userbot черновиками, тренажёр сессией. Свежесть файла поэтому доказывает
> «кто-то писал», а не «модербот работает».

| окно | модербот | кто двигал mtime |
|---|---|---|
| 20:27:34 → 20:33:52 | **мёртв** (лок держал 15760) | `userbot_listen.py` **PID 13736** (жив с 20:27:55.544) и работающий внутри него `suggest.py`: `init_db()` — `userbot_listen.py:206,631`; `fetch_ready`/`mark`/`fetch_pending_intake`/`enqueue_draft` — `suggest.py:6944-7169` |
| с 20:33:52 по сейчас | **жив, PID 10948** | его собственный тик `job_heartbeat` → `moderation_ipc.heartbeat()` (`moderation_bot.py:118`), шаг 5 с |

Почему даже ЧТЕНИЯ двигают mtime: соединение открывается и **закрывается на каждый вызов**
(`moderation_ipc._conn`), поэтому закрытие чекпойнтит WAL в главный файл —
`expectations_pc.py:131-132`. То есть клиентского трафика userbot-а достаточно, чтобы файл
выглядел свежим при мёртвом модерботе.

### Ложного зелёного контур НЕ выдал — замок сработал

Именно этот случай в О3 предусмотрен (`expectations_pc.py:658-700`): `moderbot_writer()` возвращает
**False**, когда процесса с номером из лока в системе нет, и вердикт становится **не «жив»**, а:

> «запись в IPC есть и она не стара, но процесса с номером из лока в системе нет — писал не модербот»

Подтверждение из живого состояния наблюдателя `tmp/expect_pc/state.json` (замер 20:39): попытка
`kids` в **20:27:40.391** несёт подпись
`pc_agent=неизвестно|userbot=неизвестно|moderation_bot=неизвестно` — **«неизвестно», а не «жив»**.
На момент замера 20:49:34 база свежая (`moderation_ipc.db mtime = 20:49:34.766`) при живом
авторе 10948, то есть третий исход уже закрыт по-честному.

Ничего не поднималось и не гасилось.

---

## Что этот документ НЕ доказывает (честно)

1. **Холодный пуск не проверен.** Доказана загрузка после перезагрузки из живого сеанса.
   Обесточивание/выключение по питанию — отдельная проверка, машину для неё никто не трогал.
2. **Имя механизма входа взято косвенно.** Прямое поле «Logon Process» живёт в `Security`, который
   без повышения прав не читается (§5).
3. **Пароль в LSA доказан функционально, а не пробой хранилища** (§2). Ветка `HKLM\SECURITY`
   закрыта всем, кроме SYSTEM.
4. **Признак успеха в howto §«ЧТО ПРОВЕРИТЬ» остаётся хрупким** — он завязан на фазу вотчдога и
   при разблокировке раньше 7-й минуты даёт ложное «не сработал». Код и документ не правились.

---

*Замер 18.08.2026 20:36–20:49 (+07:00), `mxfillpc\mxfill1`, сессия не повышена. Только чтение:
реестр не правился, Планировщик не менялся, процессы не поднимались и не гасились, база не
открывалась (только `stat`), пароль не запрашивался, не читался и не сохранялся.*
