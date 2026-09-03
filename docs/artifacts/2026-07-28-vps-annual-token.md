# VPS-полоса 328: перевод исполнителя с подписочного входа на годовой токен (28.07.2026)

Класс тот же, что уронил RC-сервер этим же днём (`2026-07-28-rc-server-revoked-token.md`):
**долгоживущий процесс переживает смену OAuth-состояния владельца**. Лечение здесь другое —
не рестарт, а уход от подписочной сессии на токен, который не зависит от входов на ПК.

Время в логах демона — **UTC**; локальное = UTC+7.

## Симптом и корень

Три задачи серверной полосы легли в `failed`: `id=1` (10:19:45), `id=2` (10:30:16),
`id=3` (10:33:02) — у всех `claude -p exit=1` и `METRICS … outcome=failed`. В журнале демона
текста отказа НЕТ (пишется только код возврата), поэтому отказ снят **живым повтором** в том же
окружении:

```
rc=1
Failed to authenticate: OAuth session expired and could not be refreshed
```

Креды VPS: `/root/.claude/.credentials.json`, 281 байт, от 24.07 23:32 — **поля `expiresAt` в
файле нет вовсе**, остался только refresh-токен (годен до 17.08), и обновиться им не выходит.

## Где на самом деле живёт окружение исполнителя

Ловушка: у юнита `orchestrator-daemon.service` **`EnvironmentFile` не было вообще** — systemd
давал только `PATH`/`USER`/`HOME`. Окружение исполнителю собирает сам питон:

```
orchestrator_daemon.py:36-37  load_dotenv(REPO/.env)
orchestrator_daemon.py:892    child_env = dict(os.environ)
orchestrator_daemon.py:898-9  child_env.pop("ANTHROPIC_API_KEY"); child_env.pop("OPENAI_API_KEY")
orchestrator_daemon.py:958    _POPEN(cmd, cwd=REPO, env=child_env)
```

`ANTHROPIC_API_KEY` в `.env` есть (108 симв.), но демон **намеренно вырезает** его из дочернего
окружения, чтобы `claude -p` шёл по подписке, а не по платному API. Значит любой ключ, положенный
в окружение демона и НЕ попавший в список `pop`, доедет до `claude -p` — включая
`CLAUDE_CODE_OAUTH_TOKEN`.

## Как выпущен токен (метод, который стоит помнить)

`claude setup-token` — интерактивная команда без headless-режима (`--help` показывает только
`-h`). Два живых препятствия и их обход:

1. **Без TTY команда молчит.** Замер: 3 минуты в пайп — ноль байт (Ink не рендерит в не-терминал).
   Решение — `winpty` из Git for Windows (`-Xallow-non-tty -Xplain`): настоящая псевдо-консоль,
   а вызывающему остаются обычные пайпы. Каталог `C:\Program Files\Git\usr\bin` обязан быть в
   `PATH` — рядом лежит `msys-2.0.dll`.
2. **Enter не проходит через stdin-пайп.** Приложение читает в raw-режиме и ждёт **CR**; `\n`
   молча игнорируется — код виден звёздочками, но не подтверждён. Первый код из-за этого протух
   (`OAuth error: Request failed with status code 400`). Рабочий способ — `WriteConsoleInput`
   в консоль процесса: `FreeConsole()` → `AttachConsole(pid)` → `CONIN$` → событие `VK_RETURN`.
   Причём **отдельный** Enter проходит, а тот же CR в хвосте общего залпа символов — нет.
3. **Псевдо-консоль держит 80 колонок** (`COLUMNS=1000` winpty игнорирует) и **разрывает токен
   на две строки**. Наивный захват по регекспу `sk-ant-…` берёт только первую строку — 79 симв.
   вместо 108, токен нерабочий. Склеивать обязательно.

Итог: токен 108 символов, префикс `sk-ant-oat01`, срок — 1 год.

## Как подключён

Не в `.env` (он **644**, мир-читаемый, и лежит в git-репозитории), а отдельным файлом вне репо
плюс drop-in:

```
/root/.config/claude-executor.env                                   root:root 600, 133 б
    CLAUDE_CODE_OAUTH_TOKEN=<108 симв.>
/etc/systemd/system/orchestrator-daemon.service.d/10-oauth-token.conf  root:root 644
    [Service]
    EnvironmentFile=/root/.config/claude-executor.env
```

`systemctl daemon-reload` **до** рестарта — иначе юнит стартует со старым окружением и проверка
соврёт. После reload: `EnvironmentFiles=/root/.config/claude-executor.env (ignore_errors=no)`.

## Проверка живым форматом

Окружение собрано дословно по коду демона: `/proc/<MainPID>/environ` (реальное окружение живого
процесса) + `.env` по семантике `load_dotenv` (без перекрытия) + те же `pop`-ы. Модуль демона
НЕ импортировался — его импорт сорит в боевой `orchestrator_daemon.log`.

```
CLAUDE_CODE_OAUTH_TOKEN задан : True   длина 108   первые 12: sk-ant-oat01
ANTHROPIC_API_KEY вырезан     : True

/usr/bin/claude -p --model claude-opus-5 --fallback-model claude-fable-5 --effort xhigh \
  --output-format json --settings /root/turbobaby-manager-bot/headless_settings.json "…"

rc=0
{"type":"result","subtype":"success","result":"ok", … "modelUsage":{"claude-opus-5":{…}}}
```

Юниты: `orchestrator-daemon` 104073 → **130163**, active с 12:45:32 UTC, `NRestarts=0`, drop-in
виден в `systemctl status`. Не тронуты: `splinter` 78527, `wa-webhook` 60626, `caddy` 1248
(аптаймы 3–4 дня, PID прежние).

## Откат

`/root/.claude/.credentials.json` **НЕ удалён** — 281 б, 24.07 23:32,
sha256 `e8b3ab5a828b47f4f0a0c82be3f5b414a6ed803f6dc8786772b96e3f11a6962a`. Чтобы вернуться на
подписочный вход: убрать drop-in и `systemctl daemon-reload && systemctl restart orchestrator-daemon`.

## Побочные наблюдения

- `get_pending(status)` моста отвечает `ok=True`, но **0 строк на ЛЮБОЙ статус** (`failed`,
  `done`, `new`, `needs_approval`) — читать архивные строки очереди этим входом нельзя.
- Опасение «рестарт демона потеряет `process_na_reminders`» **не подтвердилось**: функция в
  дереве (`orchestrator_daemon.py:3366`, вызов `:3411`).
- Плановая заметка: токен годовой — истечёт около **28.07.2027**.
