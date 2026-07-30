# RC-контур «встал при заблокированном ПК» — диагноз + план закрытия навсегда

Дата: 2026-07-24 ~17:40 (+07). Read-only ЭТАП 1 завершён; ЭТАП 2 (правка задачи) — ждёт красной кнопки владельца.

## TL;DR диагноз (одной фразой)

**ПК НЕ спал** (сон idle=Never, десктоп, 0 событий Kernel-Power 42/107 за 45 ч), задача **НЕ останавливалась** (Running, батарея/idle-стопы на десктопе спят), процесс **НЕ падал по OS-исключению** (0 записей Application Error про claude). Контур встал по классу **«живой процесс при мёртвом канале»**: у СТАРОГО одно-режимного супервизора (сессия `--remote-control`, старт 22.07 22:35, MSIX 2.1.217) НЕ БЫЛО пробы живости — websocket RC тихо отвалился ночью в простое, процесс жил зомби ~30 ч, пока CLI сам не вышел (exit=-1) в **05:04:05**. Блокировка экрана — совпадение (ночь = самый долгий тихий простой = самый вероятный тихий обрыв), а не причина. **Прямая причина уже закрыта** сегодняшним деплоем dual-mode + liveness-probe (коммит 2ffa1fa, 16:36/16:49) — проба ловит зомби за ≤20 мин (живой факт: гашения 17:10:36, 17:31:06, 17:31:23). ЭТАП 2 (repeating-триггер задачи) — страховка на воскрешение САМОГО супервизора.

## Факты ЭТАПА 1 (дословные выводы — в сессии)

### 1. Журнал Windows (сон/пробуждение, падения)
- **System, Kernel-Power/Power-Troubleshooter с 22.07 20:00:** 360 System-событий, самое раннее 22.07 20:14:41 (лог покрывает всё окно). Событий сна/резюма (Id 42/107/506/507/566/567/505/105) — **НОЛЬ**. Единственные Kernel-Power: 16:49:28 Id=109 «shutdown transition → Power Action **Reboot** / Reason: **Kernel API**» (софт-ребут перед деплоем dual-mode) и 16:49:41 Id=172 «Connectivity state in standby: Disconnected, Reason: NIC compliance» (загрузочный отчёт возможностей NIC, не переход сна).
- **Application, Application Error/WER/Hang с 22.07:** записи есть только про `WindowsWcpOtherFailure3` (CBS/servicing, `ntsystem.cpp SysDeleteValue`) — к Claude отношения нет. Строк с `claude`/`Claude` в дампе ошибок — **0** (grep). ⇒ процесс RC **не крешил** по OS-исключению.

### 2. Точное время смерти (хвост `rc_remote_control.log`)
```
2026-07-22 22:35:33  старт сессии: …\claude-code\2.1.217\claude.exe --remote-control turbobaby-pc   (СТАРЫЙ одно-режим)
2026-07-24 05:04:05  сессия завершилась (exit=4294967295 = 0xFFFFFFFF = -1) — рестарт 15 с   ← «встал ночью»
2026-07-24 05:04:23  старт сессии (2.1.217) снова
2026-07-24 14:32:14  сессия завершилась (exit=-1)
2026-07-24 15:50:26  супервизор (pid 12996) — ещё одно-режим (.local\bin\claude --remote-control)
2026-07-24 16:36:12  супервизор (pid 17264, ветки=['rc-server','named-channel'])   ← НОВЫЙ dual-mode
2026-07-24 16:49:53  супервизор (pid 10020, dual) — текущий
2026-07-24 16:50:05  [rc-server] pid 6272 ; [named-channel] pid 5560
2026-07-24 17:10:36  [named-channel] ЗОМБИ (лог >1200 с И 0 conn) — гашу 5560 → 16992   ← проба работает
2026-07-24 17:31:06  [rc-server]     ЗОМБИ — гашу 6272 → 16084
2026-07-24 17:31:23  [named-channel] ЗОМБИ — гашу 16992 → 12208
```
Кто умер первым: **канал** (websocket) — задолго до 05:04, но старый супервизор этого не видел (пробы не было); **процесс** вышел сам в 05:04:05. exit=-1 = самовыход CLI, не убийство извне (kill дал бы 1) и не OS-креш (дал бы 0xC0000005 + Application Error 1000).

### 3. Дерево процессов (PPID по факту)
```
svchost.exe 1844 (Планировщик, PPID 1128=services)  ─→  wscript 7308  ─→  rc_supervisor.py 10020
                                                                              ├─ claude rc                 16084  (rc-server)
                                                                              └─ claude --remote-control … 12208  (named-channel)
Claude Desktop 10964 ← explorer 9444   (ОТДЕЛЬНОЕ дерево)
```
Супервизор **самостоятелен** — потомок Планировщика, НЕ Electron/Claude Desktop. Рестарт приложения Claude его не трогает. Дублей нет: ровно один wscript/супервизор/rc-server/named-channel. Синглтон-мьютекс + `MultipleInstances=IgnoreNew` работают.

### 4. Энергосбережение
- `powercfg /a`: доступны **S3, Гибернация, Быстрый запуск**; **S0 Modern Standby — НЕ поддерживается прошивкой**. (Память «Modern Standby» неверна — это классический S3.)
- Активная схема **Высокая производительность**: `STANDBYIDLE` (сон после) = **0x0 (Никогда)** и от сети, и от батарей; `HIBERNATEIDLE` = **0x0 (Никогда)**. Десктоп (Win32_Battery: нет; ChassisTypes=3).
- ⇒ ПК **не может уснуть по простою** — что и подтверждено нулём событий сна. `powercfg /requests` требует админа (не выполнено — не критично).

### 5. Задача `TurboBabyRC` — ДО
```
Status: Running | Last Result: 267009 (0x41301 = SCHED_S_TASK_RUNNING) | Logon Mode: Interactive only | Run As: mxfill1
LogonType=InteractiveToken | Триггеры: только <LogonTrigger/>
DisallowStartIfOnBatteries=true | StopIfGoingOnBatteries=true | ExecutionTimeLimit=PT0S
MultipleInstancesPolicy=IgnoreNew | RestartOnFailure Count=3 Interval=PT1M
IdleSettings: Duration=PT10M WaitTimeout=PT1H StopOnIdleEnd=true RestartOnIdle=false  (спят: нет RunOnlyIfIdle)
Action: wscript //B //Nologo rc_remote_control.vbs  (vbs: sh.Run …,0,True → wscript ЖДЁТ супервизора)
```
Единственная дыра: **только logon-триггер**. Если всё дерево супервизора умрёт и 3 попытки RestartOnFailure выйдут — воскрешать НЕЧЕМ до следующего входа.

## Текущее состояние контура (проверка ФАКТОМ)
- **rc-server pid 16084 — ЖИВ/ЗДОРОВ**: `rc_server_debug.log` свеж (age ~73 с), среда `env_011LX3o9XPho74yGb6h6omA7` зарегистрирована, poll-loop идёт («100 consecutive empty polls» в 17:40:43). 0 ESTABLISHED в моменте — норма (класс «0 TCP ≠ зомби», лог свежий ⇒ жив).
- **named-channel pid 12208 — ЖИВ**, но лог тихий с 17:31:40 (стартовый бёрст → тишина в ожидании телефона) → каждые ~20 мин перевыбирается пробой (косметический флап, контур не роняет).
- Побочно: `rc_server_debug.log` → «Session creation failed 400: GitHub repository access check failed — re-authorize GitHub in settings». Устройство держится, но старт сессии с телефона упрётся в GitHub-реавторизацию (отдельный вопрос).

## ЭТАП 2 — план (правка задачи, КРАСНОЕ, ждёт кнопки)

> **⚠️ СТАТУС НА 31.07.2026: по-прежнему НЕ ПРИМЕНЕНО — но проверять это надо не так, как
> проверяли до сих пор. Три ловушки подряд, все три сегодня разобраны:**
>
> **1. Не тот файл.** Рядом лежат ТРИ XML, и они про РАЗНЫЕ задачи:
> - `pc_remote_control.task.xml` (трекаемый, корень репо) — `<URI>\pc_remote_control`,
>   **установочный шаблон**;
> - `docs/artifacts/2026-07-24-TurboBabyRC.before.xml` — `<URI>\TurboBabyRC`, **дамп ЖИВОЙ
>   задачи** до правки;
> - `docs/artifacts/2026-07-24-TurboBabyRC.hardened.xml` — `<URI>\TurboBabyRC`, предлагаемый
>   вариант.
>
> Живая задача установлена как **`\TurboBabyRC`**. Значит содержимое трекаемого шаблона
> **ничего не говорит** о настройках живой задачи: вывод «в репо не применено ⇒ страховки нет»
> построен не на том файле. Сверять — только через Планировщик (класс `schtasks`, нужен «да»).
>
> **2. Ловушка грепа: `PT10M` есть ВЕЗДЕ.** Он присутствует и в трекаемом шаблоне, и в
> `before.xml` — там это `IdleSettings/Duration`, к repeating-триггеру отношения не имеющий.
> Искать «применено ли упрочнение» по подстроке `PT10M` — гарантированно ошибиться.
> Настоящая дельта упрочнения (есть в `hardened.xml`, нет в `before.xml`): **`<TimeTrigger>`**
> и **`StartWhenAvailable=true`**.
>
> **3. Ловушка кодировки: `grep` по этим XML молча возвращает НОЛЬ.** Оба артефакта — экспорт
> Планировщика в **UTF-16**, ASCII-паттерн в них не матчится. «Grep → 0» здесь значит
> «не та кодировка», а НЕ «этого в файле нет». Читать их надо инструментом, который понимает
> UTF-16 (`Get-Content -Raw`), а не `grep`. Это ровно тот же класс, что «grep по репо → 0»
> ≠ «этого нет».

Через `Register-ScheduledTask -Xml` (сохраняет все поля; НЕ через schtasks-пересборку). Живой супервизор pid 10020 **не трогаем** — определение обновится, новые поля вступят при следующем старте; дублей не будет (IgnoreNew + мьютекс).

| Поле | ДО | ПОСЛЕ |
|---|---|---|
| Триггеры | только `<LogonTrigger/>` | `<LogonTrigger/>` **+ `<TimeTrigger>` Repetition `PT10M`, бессрочно** |
| DisallowStartIfOnBatteries | true | **false** |
| StopIfGoingOnBatteries | true | **false** |
| StopOnIdleEnd | true | **false** (+ RestartOnIdle=false) |
| RestartOnFailure Count | 3 | **99** (Interval PT1M) |
| StartWhenAvailable | (нет) | **true** |
| ExecutionTimeLimit | PT0S | PT0S (уже бессрочно) |
| MultipleInstancesPolicy | IgnoreNew | IgnoreNew (уже верно) |
| LogonType / Run As | InteractiveToken / mxfill1 | **без изменений** |

**НЕ меняем LogonType на «whether user logged on or not»**: интерактивной RC-сессии нужен живой TTY скрытой консоли (session ≠ 0). S4U/Password убьют TTY (класс «мёртвый TTY»). Компромисс: контур живёт, пока владелец залогинен (десктоп всегда включён, вход есть); repeating-триггер воскрешает В ПРЕДЕЛАХ сессии. При logoff интерактивный RC невозможен в принципе.

**Питание/сон — правок НЕ требуется:** сон уже Never, Modern Standby нет, десктоп. Шаг 8 (network-in-standby) неприменим.

**Отвязка от Electron (шаг 7) — не требуется:** супервизор уже самостоятелен (потомок Планировщика).

Если `Register-ScheduledTask` упрётся в права админа — СТОП, показать ошибку, выдать владельцу точную elevated-команду.
