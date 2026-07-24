# 2026-07-24 — Обновление claude CLI: диагностика и план починки (read-only)

Задача из темы 328: claude застрял на 2.1.217, автообновление падает с 22.07, doctor ругается
на `C:\Users\mxfill1\.local\bin\claude.exe`. Ниже — дословные факты, прямой ответ «почему»,
минимальный план (НЕ выполнялся: всё вне проекта, нужен «да» владельца).

## 1. Факты дословно

### 1.1 `.local\bin` и native-дерево

- `Test-Path C:\Users\mxfill1\.local\bin` → **True**, но каталог **ПУСТ**: `Get-ChildItem -Force`
  → 0 элементов. Никакого `claude.exe` там нет — «размер и дата» снимать не с чего.
- Всё дерево `.local` создано в одну секунду **22.07.2026 21:55:28** (`bin`, `share`, `state`).
- `C:\Users\mxfill1\.local\share\claude\versions\2.1.217` — **ФАЙЛ 0 байт** (Mode `-a----`,
  CreationTime = LastWriteTime = 22.07.2026 21:55:28). Здесь должен был лежать бинарь.
- `C:\Users\mxfill1\.local\state\claude\locks` — пуст (застрявших локов нет).
- PATH: HKCU `Path` = `...Python314...;WindowsApps;.dotnet\tools;VS Code\bin;twilio-cli;WinGet\Links;Roaming\npm;Roaming\nvm;nodejs;` — `.local\bin` **нет**; в HKLM `Path` совпадений с `.local` **нет**; в PATH текущего процесса — False.

### 1.2 Рабочий коннектор PID 12952

```
ProcessId       : 12952
ParentProcessId : 23116   (rc_supervisor)
Name            : claude.exe
ExecutablePath  : C:\Users\mxfill1\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.217\claude.exe
CommandLine     : ...\2.1.217\claude.exe --remote-control turbobaby-pc
CreationDate    : 24.07.2026 14:32:30
```

Это каталог **Claude Desktop** (MSIX-пакет `Claude_pzs8sxrjxfjjc`, виртуализированный Roaming).
Рядом лежат ровно две версии: `2.1.215` и `2.1.217` (LastWriteTime обеих 22.07.2026 16:09:05) —
новые версии сюда кладёт Desktop, и с 22.07 он ничего не подкладывал.

### 1.3 `claude doctor` — полный вывод (запущен рабочим бинарём)

```
Claude Code doctor

Running: native (2.1.217)
Commit: 9963b018d22c
Platform: win32-x64
Path: C:\Users\mxfill1\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.217\claude.exe
Config install method: unknown
Search: OK (bundled)
Auto-updates: enabled
Auto-update channel: latest
Last update attempt: failed (install_failed) — 2026-07-22

Remote Control
Control this session from claude.ai/code or the Claude mobile app

3 warnings found
- Native installation exists but C:\Users\mxfill1\.local\bin is not in your PATH
  Fix: Add it by opening: System Properties → Environment Variables → Edit User PATH → New → Add the path above. Then restart your terminal.
- Running native installation but config install method is 'unknown'
  Fix: Run claude install to update configuration
- claude command at C:\Users\mxfill1\.local\bin\claude.exe missing or broken
  Fix: Run claude install to repair the installation.
```

### 1.4 «Лог установщика»

Отдельного файла-лога установщик **не оставляет** — проверено: в `~/.claude` (глубина 2, включая
`daemon`, `cache`) нет ни одного `*install*`/`*update*`-лога, `%TEMP%\claude` — только
scratchpad'ы сессий, `rc_remote_control.log` — 0 вхождений install/update/native.
Единственный след — `C:\Users\mxfill1\.claude\.last-update-result.json` (162 байта,
LastWriteTime **22.07.2026 21:55:50**, с тех пор не перезаписывался), дословно:

```
{"timestamp":"2026-07-22T14:55:50.731Z","path":"native","outcome":"failed","status":"install_failed","version_from":"2.1.217","version_to":null,"error_code":null}
```

Рядом по времени: `~/.claude/history.jsonl` — единственная запись, интерактивный `exit` в
**22.07 21:55:56** (проект D:\turbobaby-bot) — попытка установки прошла и упала внутри той
самой минуты. В `~/.claude.json` ключей `installMethod`/`autoUpdat*` **нет вовсе**
(Select-String пуст) — отсюда doctor'овское «Config install method: unknown».

## 2. Почему обновление не проходит (прямой ответ)

Корень — **не тот способ установки**, плюс оставленная им **битая полу-установка**:

1. Рабочий бинарь управляется Claude Desktop (версионный каталог в MSIX LocalCache). Сам себя
   он там не обновляет — новые версии подкладывает Desktop, а тот после 22.07 16:09 ничего не
   принёс. Поэтому версия «застряла» на 2.1.217.
2. 22.07 в 21:55 CLI попытался (единственный раз) развернуть **native-инсталл** в `~/.local`:
   создал скелет каталогов, начал класть бинарь `versions\2.1.217` — и оборвался на **0 байт**;
   shim `~/.local/bin/claude.exe` не создан вовсе. Итог: `install_failed`, `version_to: null`,
   `error_code: null` — причина обрыва записи нигде не залогирована (лога у инсталлера нет).
3. Новых попыток с тех пор **не было** (`.last-update-result.json` не трогался с 21:55:50
   22.07) — RC/headless-процессы установку не докатывают. А doctor теперь видит «Native
   installation exists» (пустой скелет) и честно судит её как битую.
4. По вариантам из задачи: **битый файл — ДА** (0-байтовый `versions\2.1.217`, отсутствующий
   shim); **нет прав — признаков нет** (все каталоги созданы юзером, locks пуст); **занят
   процессом — НЕТ** (целевой `.local\bin\claude.exe` не существует — блокировать нечего,
   рабочий процесс живёт в другом каталоге); **не тот способ установки — ДА, корень**.
5. Отягчающее: даже успешный native-инсталл не дал бы команду `claude` — `.local\bin` нет ни в
   user-, ни в machine-PATH (то самое «where.exe claude пуст» из c272d57).

## 3. Минимальный план починки (НЕ выполнялся — вне проекта, исполняет человек после «да»)

1. **Дочинить native-инсталл рабочим бинарём** (Fix №2 и №3 из doctor):
   `& "C:\Users\mxfill1\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code\2.1.217\claude.exe" install`
   — докачает свежий бинарь в `~/.local/share/claude/versions/<new>`, создаст shim
   `~/.local/bin/claude.exe`, пропишет installMethod. Выполнять в терминале и сохранить stdout
   (прошлая попытка не оставила лога). Проверка: `claude doctor` без «missing or broken»,
   `.last-update-result.json` → success.
2. **Добавить `C:\Users\mxfill1\.local\bin` в user-PATH** (HKCU\Environment; Fix №1 из doctor).
   Проверка: `where.exe claude` находит shim.
3. **Отдельная развилка (по желанию):** перепиновать RC-контур со стухающего версионного
   Desktop-каталога `...\claude-code\2.1.217\` на вечный shim `.local\bin\claude.exe`
   (правка `.env`/`CLAUDE_BIN` + рестарт задачи `TurboBabyRC`) — закрывает класс
   «CLAUDE_BIN стухшая версия»; обе операции красные, только по одобрению. Без шага 3 RC
   продолжит жить на Desktop-версии — работоспособно, но версия зависит от обновлений Desktop.

Риск шага 1: пишет только в профиль юзера (`~/.local`), рабочий PID 12952 не трогает. Если
`install` снова даст 0 байт — в момент записи смотреть антивирус/сеть (теперь будет дословный
stdout попытки).
