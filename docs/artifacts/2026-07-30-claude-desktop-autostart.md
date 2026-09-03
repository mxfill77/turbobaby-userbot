# Автозапуск Claude Desktop (канал Dispatch) — разведка + предложение

Дата: 2026-07-30. Статус: **ASK** — создание автозапуска требует одобрения владельца
(класс `schtasks` / запись вне репо). Ниже — факты разведки, рекомендованная настройка
(готова к применению) и честные ограничения.

## Часть 1 — что уже есть (только чтение)

### 1. Задача Планировщика `TurboBabyRC` (дословно)
- **Запускает:** `C:\Windows\System32\wscript.exe //B //Nologo "D:\turbobaby-bot\rc_remote_control.vbs"`
  (WorkingDirectory `D:\turbobaby-bot`). Цепочка: wscript → rc_remote_control.vbs →
  rc_supervisor.py → `claude --remote-control turbobaby-pc`.
- **Событие:** `LogonTrigger` (при входе любого пользователя), `Enabled=true`.
- **От кого:** `UserId=mxfill1`, `InteractiveToken`, `RunLevel=LeastPrivilege`.
- **Включена:** да. State=Running. LastRunTime 30.07.2026 1:17:17, LastTaskResult 267009
  (0x41301 = «задача выполняется», норма).
- **ВАЖНО:** это RC-канал (управление с телефона), а **НЕ** Claude Desktop. Desktop-приложение
  эта задача не поднимает.

Ещё есть задачи `pc_orchestrator` (Running) и `pc_orchestrator_watchdog` (Ready) — тоже
headless-контур, не Desktop-приложение. Все три висят на `LogonTrigger` того же mxfill1.

### 2. Автозагрузка и Run-ключи
- User Startup (`…\AppData\Roaming\…\Startup`): `PinWin.lnk`, `desktop.ini`. **Claude нет.**
- All-Users Startup (`C:\ProgramData\…\Startup`): `AnyDesk.lnk`, `Tailscale.lnk`, `desktop.ini`. **Claude нет.**
- HKCU\…\Run: Epson (×2), GoogleDriveFS, Samsung DeX, MicrosoftEdgeAutoLaunch. **Claude нет.**
- HKLM\…\Run: SecurityHealth, CORSAIR iCUE, Epson (×2). **Claude нет.**
- HKCU\…\RunOnce: пусто. StartupApproved\Run и \StartupFolder: пусто (MSIX-StartupTask не зарегистрирован).

### 3. Путь к Claude Desktop и как стартует сейчас
- Exe: `C:\Program Files\WindowsApps\Claude_1.24012.9.0_x64__pzs8sxrjxfjjc\app\Claude.exe`
- Пакет MSIX/Store: `Claude_pzs8sxrjxfjjc`; **AUMID: `Claude_pzs8sxrjxfjjc!Claude`**.
- Сейчас: главное окно PID 8060, командная строка — просто `"…\Claude.exe"` без спец-аргументов,
  без родителя-автозапуска. **Открыто вручную владельцем.**
- Dispatch живёт ВНУТРИ приложения: дочерний headless-claude (PID 9828) с инструментами
  `mcp__dispatch__send_message`/`list_projects`. Закрыли приложение — Dispatch умер.

### 4. Прямой ответ
**НЕТ, Claude Desktop сам не поднимается.** Работает только потому, что открыт руками. Это и есть
единственная точка отказа канала Dispatch.

## Часть 2 — рекомендованная настройка (НЕ применена, ждёт «да»)

**Метод: задача Планировщика** (не папка автозагрузки). Причина: только Планировщик даёт
**задержку логона** (требование п.6); папка/Run-ключ срабатывают сразу при загрузке shell без
управления задержкой. Плюс — консистентно с уже проверенным паттерном TurboBabyRC.

**Задержка: 2 минуты (PT2M)** после входа. Почему: на холодной загрузке / выходе из сна+гибернации
(этот ПК спит) должны успеть подняться сетевой стек, DHCP и Tailscale (сам в All-Users Startup),
иначе Dispatch не достучится до Anthropic API и до Telegram-моста; диск в этот момент нагружен
другими автозапусками (GoogleDriveFS, iCUE, Epson). 2 мин — безопасный запас; вместе с
`RunOnlyIfNetworkAvailable` не стартует раньше готовности сети.

**Запуск MSIX-приложения** делается через AUMID, а не прямым вызовом Claude.exe из WindowsApps
(тому нужна идентичность пакета / активация в контейнере):
`explorer.exe shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude`

Готовая команда создания (для исполнения человеком после «да»):
```powershell
$AUMID  = 'Claude_pzs8sxrjxfjjc!Claude'
$action = New-ScheduledTaskAction -Execute 'explorer.exe' -Argument "shell:AppsFolder\$AUMID"
$trig   = New-ScheduledTaskTrigger -AtLogOn -User 'mxfill1'
$trig.Delay = 'PT2M'
$prin   = New-ScheduledTaskPrincipal -UserId 'mxfill1' -LogonType Interactive -RunLevel Limited
$set    = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable -StartWhenAvailable `
            -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
            -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'TurboBabyDesktop' -Action $action -Trigger $trig `
            -Principal $prin -Settings $set `
            -Description 'Autostart Claude Desktop (+Dispatch) at logon, 2-min delay for net/disk'
```

### 7. Честное ограничение (владельцу знать обязательно)
Логон-триггер (как и папка автозагрузки, и Run-ключ) срабатывает **только после интерактивного
входа пользователя**. Claude Desktop — GUI-приложение (Electron), ему нужен живой рабочий стол; оно
**не может** стартовать до входа или как служба. **Авто-вход в Windows НЕ настроен**
(`AutoAdminLogon` и `DefaultUserName` пусты — проверено). Значит: **если ПК перезагрузился и никто
не вошёл — приложение НЕ поднимется.** Причём это касается и уже существующих TurboBabyRC /
pc_orchestrator — они на том же LogonTrigger. Весь ПК-контур сейчас предполагает, что после ребута
кто-то войдёт. Сделать по-настоящему «без рук» можно только включив авто-вход Windows (хранимые
учётные данные mxfill1 → машина логинится сама → тогда логон-задача срабатывает). Это отдельное
решение владельца с очевидным минусом по безопасности (пароль/беспарольный вход на машине) — сам
его НЕ трогаю.

## Часть 3 — проверка
8. После создания вывести запись дословно:
   ```powershell
   Export-ScheduledTask -TaskName 'TurboBabyDesktop'
   Get-ScheduledTaskInfo -TaskName 'TurboBabyDesktop' | Format-List *
   ```
9. **Живое доказательство — только после перезагрузки и входа mxfill1.** Смотреть тогда:
   (а) `Get-ScheduledTaskInfo TurboBabyDesktop` — LastRunTime свежий, LastTaskResult 0x0;
   (б) `Get-Process Claude` — процесс появился примерно через 2 мин после входа;
   (в) главное — Dispatch реально отвечает (карточка в Telegram / канал SessionEnd).
   «Задача отработала без ошибки» НЕ равно «канал жив» — мерить по факту ответа Dispatch.

## Часть 10 — детектор «связь молча отвалилась» (28.07), предложение, НЕ реализовано
Автозапуск закрывает «приложение не открыто», но не случай 28.07: приложение открыто, а связь молча
умерла. По аналогии с детектором немоты (aed3e4e: «сессия жива + шлёт heartbeat, а работы нет →
карточка в 328») нужен внешний сторож с **двухфакторным** признаком:

- **Фактор 1 — liveness:** процесс Claude.exe пакета `Claude_pzs8sxrjxfjjc` жив (необходимо, но
  недостаточно — 28.07 процесс был жив).
- **Фактор 2 — свежесть канала:** heartbeat со стороны Dispatch продвинулся за окно N. Варианты:
  - *пассивно (как aed3e4e):* сторож раз в N мин смотрит возраст последней Dispatch-строки в логе;
    процесс жив, а heartbeat не двигался > порога → подозрение на обрыв → карточка в 328/личку;
  - *активно:* сторож шлёт лёгкий пинг через мост и ждёт ack за таймаут; нет ack 2 цикла → обрыв.

**Минусы (честно):**
1. «Молчание» неоднозначно: тихий обрыв vs. владелец просто ничего не спрашивает → ложные срабатывания.
   Нужен Dispatch-heartbeat, тикающий НЕЗАВИСИМО от активности владельца, — его сначала надо завести.
2. Активный пинг тратит токены/лимит и может влезть в живую работу владельца в приложении (тот же ЗАПРЕТ).
3. Ложные карточки приучают игнорировать алерты; ложные пропуски оставляют дыру — порог надо калибровать.
4. Детектор только УВЕДОМЛЯЕТ, но не чинит: авто-перезапуск приложения запрещён (в нём может идти живая
   работа владельца), поэтому в отличие от автозапуска само-лечения тут быть не может.
