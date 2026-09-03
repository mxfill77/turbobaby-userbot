# Автозапуск Claude Desktop (канал Dispatch) — разведка + план, 30.07.2026

Задача: поднять приложение Claude Desktop само после перезагрузки ПК, чтобы Dispatch
перестал зависеть от «открыто руками». **Приложение в рамках задачи не трогали** — только
чтение и план. Настройку автозапуска исполняет владелец (см. §NEEDS_APPROVAL).

## §1. Что уже есть (только чтение)

### 1.1 Задача планировщика TurboBabyRC — дословно (Export-ScheduledTask)
```xml
<RegistrationInfo>
  <Description>Claude Code Remote Control - osnovnoj kanal vladel'ca: wscript -> rc_remote_control.vbs
   (skrytaja konsol') -> rc_supervisor.py -> claude --remote-control turbobaby-pc v D:\turbobaby-bot</Description>
  <URI>\TurboBabyRC</URI>
</RegistrationInfo>
<Principals><Principal id="Author">
  <UserId>S-1-5-21-3727781399-3432839651-1010305476-1002</UserId>   <!-- = mxfillpc\mxfill1 -->
  <LogonType>InteractiveToken</LogonType>
</Principal></Principals>
<Triggers><LogonTrigger /></Triggers>   <!-- при ВХОДЕ пользователя, без задержки, без фильтра юзера -->
<Actions Context="Author"><Exec>
  <Command>C:\Windows\System32\wscript.exe</Command>
  <Arguments>//B //Nologo "D:\turbobaby-bot\rc_remote_control.vbs"</Arguments>
  <WorkingDirectory>D:\turbobaby-bot</WorkingDirectory>
</Exec></Actions>
```
State=Running, LastTaskResult=267009 (0x41301 = SCHED_S_TASK_RUNNING, норма).

**Вывод:** TurboBabyRC запускает НЕ приложение, а headless-канал RC (`claude --remote-control` через
vbs→supervisor). К GUI-приложению Claude Desktop отношения не имеет. Тригрер — вход пользователя,
принципал — интерактивный токен `mxfill1`.

### 1.2 Автозагрузка и Run-ключи — Claude нигде нет
- Startup (юзер): `desktop.ini`, `PinWin.lnk`. Claude — нет.
- Startup (общая): `AnyDesk.lnk`, `desktop.ini`, `Tailscale.lnk`. Claude — нет.
- HKCU\...\Run: EPLTarget(принтер), GoogleDriveFS, Samsung DeX, MicrosoftEdgeAutoLaunch. Claude — нет.
- HKCU\...\RunOnce: пусто.
- HKLM\...\Run: SecurityHealth, iCUE, Epson. HKLM\Wow6432\...\Run: EEventManager. Claude — нет.

### 1.3 Путь к приложению и как стартует сейчас
Claude Desktop — **MSIX/Store-пакет**, не обычный exe:
- PackageFullName: `Claude_1.24012.9.0_x64__pzs8sxrjxfjjc`
- PackageFamilyName: `Claude_pzs8sxrjxfjjc`
- InstallLocation: `C:\Program Files\WindowsApps\Claude_1.24012.9.0_x64__pzs8sxrjxfjjc`
- exe (GUI): `...\app\Claude.exe` — сейчас живёт пачкой PID, StartTime 30.07 01:19 (**подняты руками**).
- Канонический запуск (AUMID): `shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude` (Get-StartApps).
- Публикатор: `CN="Anthropic, PBC"`, подпись Developer.

(В процессах также `.local\bin\claude.EXE` и `AppData\Roaming\Claude\claude-code\2.1.219\claude.exe` —
это **CLI Claude Code / RC-сессии**, другой продукт, не Desktop.)

**Важно:** у MSIX-пакета в манифесте есть СВОЙ штатный автозапуск, но он выключен:
```xml
<desktop:Extension Category="windows.startupTask" Executable="app\Claude.exe" ...>
  <desktop:StartupTask TaskId="ClaudeStartup" Enabled="false" DisplayName="Claude" />
</desktop:Extension>
```
Фактическое состояние в реестре (`...\AppModel\SystemAppData\Claude_pzs8sxrjxfjjc\ClaudeStartup`):
`State=0` (Disabled), `UserEnabledStartupOnce=0` — владелец его ни разу не включал.

### 1.4 Прямой ответ: приложение само НЕ поднимается
Ни автозагрузка, ни Run-ключи, ни задача планировщика, ни собственный StartupTask пакета его не
стартуют. Живёт только потому, что открыто руками (PID стартовали 01:19). **Единственная точка отказа
канала Dispatch — да, подтверждена.**

## §2. План настройки (ничего не исполнено — только чтение выше)

Два законных пути. Рекомендация — **Вариант B** (единственный даёт задержку из п.6).

### Вариант A (проще всего, self-serve владельцем, БЕЗ моего действия)
Включить штатный автозапуск пакета: **Параметры → Приложения → Автозагрузка → «Claude» → Вкл**
(или Диспетчер задач → вкладка «Автозагрузка приложений» → Claude → Включить). Это переведёт
`ClaudeStartup` в State=Enabled. Плюс: родной механизм, ноль сторонних артефактов, переживает
обновления пакета. Минус: **задержку не настроить** — стартует сразу при входе.

### Вариант B (рекомендуемый — с задержкой, зеркалит TurboBabyRC)
Отдельная задача планировщика `TurboBabyDesktop`, тригрер «при входе» + задержка, запуск через
AppsFolder. Команда для владельца (исполнять из-под mxfill1):
```powershell
$act  = New-ScheduledTaskAction -Execute 'C:\Windows\explorer.exe' `
        -Argument 'shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude'
$trg  = New-ScheduledTaskTrigger -AtLogOn -User 'mxfillpc\mxfill1'
$trg.Delay = 'PT2M'                                   # см. п.6
$prn  = New-ScheduledTaskPrincipal -UserId 'mxfillpc\mxfill1' -LogonType Interactive -RunLevel Limited
$set  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'TurboBabyDesktop' -Action $act -Trigger $trg `
  -Principal $prn -Settings $set `
  -Description 'Autozapusk Claude Desktop (kanal Dispatch) cherez 2 min posle vhoda'
```

### п.6 Задержка — 2 минуты (PT2M), обоснование
При входе гонятся Tailscale (лежит в общей автозагрузке — туннель до Bridge поднимается ~10–40 с),
GoogleDriveFS (монтирует диск), Edge/Samsung DeX/iCUE — плюс DHCP/DNS. 2 минуты уверенно перекрывают
подъём туннеля и монтирование диска, а Dispatch всё равно возвращается в течение ~2 мин после входа.
1 минуты, скорее всего, тоже хватит; 2 — безопасный запас (цена запаса ничтожна: раньше канал жил
только при ручном открытии). Задержку умеет **только Вариант B**; A стартует мгновенно.

### п.7 ЧЕСТНО: «перезагрузились, но никто не вошёл» — НЕ работает
И A, и B срабатывают **только при интерактивном входе пользователя** (`LogonTrigger`/StartupTask
привязаны к входу). Claude Desktop — GUI-приложение Electron, оно физически не может работать без
интерактивного сеанса (нет рабочего стола — нет приложения). Значит: **ПК перезагрузился, вход не
выполнен → Dispatch не поднимется.** Более того — **тем же ограничением болен и существующий канал
RC** (TurboBabyRC: `LogonTrigger` + `InteractiveToken`): весь ПК-контур сегодня зависит от входа в
систему. Закрыть это полностью может лишь **автовход в систему (autologon)** учётки mxfill1 — он хранит
пароль и грузит разблокированный рабочий стол, это компромисс безопасности и **отдельное решение
владельца** (не делаю).

## §3. Проверка
### п.8 Как показать созданную запись дословно (после создания владельцем)
```powershell
Export-ScheduledTask -TaskName 'TurboBabyDesktop'          # весь XML записи
Get-ScheduledTaskInfo -TaskName 'TurboBabyDesktop' | Format-List State,LastRunTime,LastTaskResult,NextRunTime
```
Проверять именно так — по XML записи, а не по «команда прошла без ошибки». Пока не создано — вывод пуст.

### п.9 Живое доказательство — только после перезагрузки
После ребута → войти → подождать ≥2 мин (задержка), затем смотреть:
1. `Get-Process claude | ? { $_.Path -like '*WindowsApps*Claude*' }` — GUI-процессы есть, StartTime ≈ вход+2 мин.
2. Канал Dispatch ожил: возобновились heartbeat/карточки сессий; свежая строка в `cowork_log`;
   отрабатывает хвост-хук SessionEnd (карточка в 1160).
3. `Get-ScheduledTaskInfo -TaskName TurboBabyDesktop` → LastTaskResult 0x0, LastRunTime ≈ время входа.

## §10. Предложение (НЕ реализовано): детект «приложение открыто, а связь молча отвалилась» (случай 28.07)
По аналогии с детектором немоты сессий (`aed3e4e`): хост — тик `pc_orchestrator` (единственный
надёжно выживающий процесс, уже держит контур-вотчдог); сигнал — ровно один на инцидент (состояние
на диске, пометка только по подтверждённой доставке); канал — тема 328 через `dispatch_notify --topic`.
Предикат-аналог (И, а не ИЛИ): **процесс Claude Desktop жив** (`Claude.exe` из WindowsApps присутствует)
**И** от канала нет исходящих признаков жизни дольше порога N.

**Ключевое отличие и минусы (честно):**
1. **Нет чистого положительного сигнала.** У немоты он был: транскрипт создаётся ДО первого запроса
   к модели, поэтому «транскрипта нет вовсе» ≠ «думает». Здесь простаивающее приложение (владелец
   ничего не диспатчит) и приложение с молча отвалившейся связью выглядят одинаково — оба дают ноль
   трафика. Значит нужен **отдельный маяк живости** от самого канала (Dispatch/приложение периодически
   пингует Bridge или пишет «channel alive»-метку), и детектор срабатывает, когда процесс жив, а метка
   протухла дольше N. Без маяка — ложняки каждую тихую ночь.
2. **Порог размытее.** У немоты был разрыв 3 с против 10300 с (97x) — брать порог легко. Тут разрыва
   нет без маяка; порог придётся подбирать (15–30 мин?) и валидировать на корпусе «тихо, но здорово»,
   чтобы ложных было ~0 — эта корпусная работа и есть основная цена.
3. **Что считать «каналом».** Dispatch живёт ВНУТРИ GUI-приложения как интерактивные сессии; возможно,
   нет всегда-живого процесса, шлющего heartbeat (как у RC). Если Dispatch «жив» только при открытой
   сессии, то «нет трафика» — это норма покоя, и детектор не различит. Тогда сперва нужен лёгкий
   всегда-живой heartbeat Dispatch — и он же, по сути, и есть настоящий фикс (маяк делает обрыв
   детектируемым).
4. **Судьба-в-одной-лодке с оборвавшимся аплинком.** Если 28.07 отвалилась сеть/Bridge, ПК-детектор
   тем же аплинком и не доставит карточку. Фолбэк — Telegram (каскад `send_critical`), но при полном
   обрыве сети локально доставить нечем. Поэтому самый надёжный детектор «ПК-канал молча умер» — на
   стороне **VPS/Bridge** (он видит, что ПК перестал опрашивать), а не на ПК. ПК-детектор — лишь
   дополнение; основной сторож этого класса должен жить на VPS (симметрично «второму слою» из aed3e4e).
5. **Гигиена сигнала** переиспользуется из aed3e4e: одна карточка на инцидент, состояние на диске,
   пометка по подтверждённой доставке, маршрут `dispatch_notify --topic 328` — риск низкий, водопровод
   уже есть.

Итог по §10: детект возможен, но не «в лоб» — сначала маяк живости канала (иначе ложняки), а самый
надёжный сторож этого класса — на VPS, а не на ПК. Не реализовывать без решения владельца.
