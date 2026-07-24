# RC-режим и список Devices/Environments в приложении Claude

Диагностика read-only + один контролируемый второй экземпляр. 2026-07-24 16:05 (+07:00).
Живой коннектор PID 19656 и сторож НЕ тронуты. Второй экземпляр — PID 1304 (породил сам).

## 1. Факт: `rc` — это АЛИАС `remote-control`

`claude remote-control --help` и `claude rc --help` дают **побайтово одинаковый** вывод,
у обоих строка `USAGE  claude remote-control [options]`. Значит `rc` = псевдоним
подкоманды `remote-control`. Разница НЕ между `rc` и `remote-control`.

## 2. Настоящая развилка: подкоманда `rc` vs top-level флаг `--remote-control <имя>`

Из `claude --help` (top-level):
```
-n, --name <name>            Set a display name for this session
--remote-control [name]      Start an interactive session with Remote Control enabled (optionally named)
--remote-control-session-name-prefix <prefix>   Prefix for auto-generated RC session names (default: hostname)
```

Из `claude remote-control --help` (подкоманда, = `rc`):
```
DESCRIPTION
  Remote Control runs as a persistent server that accepts multiple concurrent
  sessions in the current directory. One session is pre-created on start...
  ...--spawn=session for the classic single-session mode...
```

| Форма | Что это по справке | Что запускает |
|---|---|---|
| `claude --remote-control turbobaby-pc` (сторож, PID 19656) | «interactive session with Remote Control enabled» — **одиночная интерактивная сессия** | одну именованную сессию `turbobaby-pc` |
| `claude rc` / `claude remote-control` (PID 1304) | «persistent **server**», same-dir, capacity 32 | **постоянный сервер-Environment**, poll-loop |

## 3. Эмпирика: что делает `claude rc` при старте (debug-лог PID 1304)

```
[bridge:init] bridgeId=1e8fa593-... dir=D:\turbobaby-bot branch=main
              gitRepoUrl=https://github.com/mxfill77/turbobaby-userbot.git machine=mxfillpc
[bridge:api] POST /v1/environments/bridge  -> 200  environment_id=env_015Cc2khCF5as9YfVeD2Vhvz
[bridge:api] >>> {"machine_name":"mxfillpc","directory":"D:\\turbobaby-bot","branch":"main",
                 "git_repo_url":"...","max_sessions":32,"metadata":{"worker_type":"claude_code"}}
[bridge:api] <<< {"environment_id":"env_015Cc2...","organization_uuid":"30c614de-...","environment_secret":"[REDACTED]"}
[bridge:init] Registered, server environmentId=env_015Cc2khCF5as9YfVeD2Vhvz
[bridge] Session creation failed with status 400: GitHub repository access check failed — re-authorize GitHub in settings
[bridge:work] Starting poll loop spawnMode=same-dir maxSessions=32 environmentId=env_015Cc2...
```

stdout PID 1304:
```
Remote Control v2.1.218 · Spawn mode: same-dir · Max concurrent sessions: 32
Environment ID: env_015Cc2khCF5as9YfVeD2Vhvz
·✔︎· Ready · turbobaby-userbot · main   Capacity: 0/32
Code anywhere with the Claude mobile app or https://claude.ai/code?environment=env_015Cc2khCF5as9YfVeD2Vhvz
space to show QR code · w to toggle spawn mode
```

Ключевое: подкоманда `rc` делает `POST /v1/environments/bridge` → **«Registered, server
environmentId=env_…»** и печатает URL `claude.ai/code?environment=env_…` + QR. Это и есть
объект, который приложение показывает как **Environment/Device**. Регистрация —
одноразовая при старте, **без кода привязки и без интерактивного ввода** (авторизуется молча
из сохранённых кред аккаунта). В логе живого коннектора (`rc_remote_control.log`) слов
device/register/pair/устройств/привяз — **0** (тот лог пишет сторож, не claude; там только
события «старт/завершение сессии»).

## 4. Прямой ответ

**Да** — постоянная запись в списке Devices/Environments приложения создаётся режимом
`claude rc` (подкоманда remote-control = «persistent server»): именно он регистрирует
**Environment** машины (`machine_name=mxfillpc`, env_…) и держит его в poll-loop.
Форма `--remote-control turbobaby-pc`, которую крутит сторож, по справке — «interactive
**session**» (классическая одиночная сессия), а не постоянный сервер-Environment; поэтому
список Devices может выглядеть пустым, хотя управление живой сессией с телефона работает.

Оговорка честности: эмпирически доказано, что `rc` регистрирует Environment. Что
`--remote-control <имя>` НЕ регистрирует такой же Environment — прямо не снято (у PID 19656
нет debug-лога, трогать его нельзя). Вывод про режимы опирается на текст справки + факт
регистрации у `rc`.

## 5. Побочный факт (важный): GitHub-блокер сессий

Даже в режиме `rc` Environment зарегистрировался, но пред-создание рабочей сессии в каталоге
упало: **`Session creation failed with status 400: GitHub repository access check failed —
re-authorize GitHub in settings`** (оттого `Capacity: 0/32`). Т.е. чтобы с телефона реально
поднимались сессии в этом каталоге, помимо режима `rc` нужно **переавторизовать GitHub в
настройках Claude Code**.

## Развилка для владельца
1. Проверить сейчас в приложении: появился ли Environment/Device `turbobaby-userbot` /
   `env_015Cc2khCF5as9YfVeD2Vhvz` (PID 1304 ещё жив — это визуальное подтверждение вопроса).
2. Если да и это нужное поведение — обсудить перевод сторожа с `--remote-control turbobaby-pc`
   на подкоманду `rc` (постоянный Environment вместо одиночной сессии).
3. Переавторизовать GitHub в настройках — иначе сессии на устройстве не создаются (400).
4. Остановить второй экземпляр по своему PID: `Stop-Process -Id 1304` (НИКОГДА не 19656/сторож).
