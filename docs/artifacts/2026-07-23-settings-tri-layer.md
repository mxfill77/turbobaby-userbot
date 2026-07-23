# 2026-07-23 — Трёхслойный settings.json против шквала подтверждений (Dispatch/RC)

Задача: убрать шквал разрешений в интерактивных сессиях, доктрину не ослаблять.
Итоговый файл готов: `docs/artifacts/2026-07-23-settings-tri-layer.json` — он должен
СТАТЬ боевым `.claude/settings.json`. Применить его из headless нельзя: харнес Claude Code
блокирует запись settings-файлов как sensitive (это правильно — вектор само-эскалации),
поэтому последний шаг за владельцем.

## Как применить после «да» (две команды, PowerShell)

```powershell
Copy-Item D:\turbobaby-bot\.claude\settings.json D:\turbobaby-bot\.claude\settings.json.bak-2026-07-23
Copy-Item D:\turbobaby-bot\docs\artifacts\2026-07-23-settings-tri-layer.json D:\turbobaby-bot\.claude\settings.json
```

Затем в репо закоммитить `.claude/settings.json` (файл затрекан). Старый черновик
`.claude/settings.json.new` (промежуточный, без deny-слоя) после этого можно удалить —
он в git не затрекан.

## Схема трёх слоёв (precedence: deny > ask > hook > allow)

1. **allow — широкий, зелёная рутина без промптов**: `Bash`, `PowerShell`, `Read` целиком
   (чтение файлов и логов, grep/ls/wc/head/tail/cat/stat/which, git status|log|diff|add|commit|push,
   python/venv-запуски, py_compile, unittest); `Edit/Write` — репо, память агента
   (`.claude/projects/**/memory/**`), скретчпад (`Temp/claude/**`). Шквал давали в основном
   PowerShell-вызовы (в старом allow не было НИ одного PowerShell-правила — см. кладбище
   одноразовых «always allow» в settings.local.json) и чтение вне репо.
2. **ask — только доктринально красное** (дублирует список `_stays_red` гарда на случай,
   если хук не отработал): `.env`/`*.session` (чтение и правка), конфиг `.claude` (репо и
   пользовательский), sqlite3, clasp (живые таблицы/CRM), сеть (ssh/scp/curl/wget +
   Invoke-WebRequest/RestMethod/iwr/irm, WebFetch/WebSearch), kill/pkill/taskkill/Stop-Process,
   systemctl kill|stop (инертно на Windows, для симметрии со схемой 328), массовые удаления
   (rm -r*/Remove-Item), git push --force / reset --hard / clean. Правила в двух синтаксисах —
   glob (`Bash(sqlite3 *)`) и префикс (`Bash(sqlite3:*)`) — чтобы не зависеть от версии матчера.
3. **deny — абсолютное**: `rm -rf /`, `chmod -R 777 /`, `dd of=/dev/*`,
   `--dangerously-skip-permissions`, `--no-verify` (Bash и PowerShell). `bypassPermissions`
   НЕ включён (defaultMode остаётся acceptEdits).

Хук `pretool_guard.py` остаётся слоем СУЖЕНИЯ: он форсит ask на красном даже при широком
allow (hook-решение сильнее allow-правил) и в matcher включён PowerShell — в боевом файле
до применения PowerShell в matcher НЕТ, то есть сейчас PowerShell-инструмент вообще мимо
гарда. Это второй аргумент за применение.

## Дифф против боевого .claude/settings.json (суть)

- allow: кладбище одноразовых точечных правил (29 строк) → 11 широких; добавлены
  `PowerShell`, `Read`, память+скретчпад для Edit/Write.
- ask: слоя не было вовсе → 48 доктринальных правил.
- deny: слоя не было вовсе → 10 абсолютных запретов.
- hooks: matcher PreToolUse получает `PowerShell` (иначе основной инструмент ПК не гардится);
  остальные хуки (Notification/Stop/SessionEnd) без изменений.
- Notification-пинг (код dispatch_notify.py, применён отдельно от settings): при ожидании
  разрешения — карточка в тему Инбокс 1160 с ТЕКСТОМ ожидающей команды из transcript-а
  (раньше DM-first; исторический Termux-мост умер).

## Проверка классификацией (не исполнением) — закреплена тестами

`test_pretool_guard.TestShkvalClassification` (гейт): рутина (`wc -l`, `grep`, `cat`,
`stat`, `which python`, `git commit`, `-m py_compile/unittest`, Edit в репо) → defer в ОБЕИХ
ролях; красное (`.env`-чтение, `kill splinter`, `taskkill /IM splinter.exe /F`, запись в CRM
через gspread/clasp, `sqlite3 INSERT`, `git push --force`) → ask в ОБЕИХ ролях.
`TestSettingsThreeLayers` держит сам итоговый файл: широкий allow, ask без рутины, deny
на месте, bypassPermissions отсутствует, оба хука зарегистрированы.
