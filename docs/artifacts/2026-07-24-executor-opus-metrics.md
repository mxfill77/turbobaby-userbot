# Исполнитель ПК → Opus 4.8 + первый реальный METRICS-замер (тема 328, 24.07.2026)

Задача очереди id=395 (headless, демон PID 3940, commit 463d409). Коммит правки: **4754793** (запушен).

## 1. Фактические значения моделей (конфиг vs команда запуска)

| Ручка | Где живёт | Значение в конфиге | Что РЕАЛЬНО подставлялось в команду (до правки) |
|---|---|---|---|
| ORCH_MODEL | нигде на ПК (имя VPS-полосы) | — (кода-читателя нет: grep по репо — 0 вхождений) | ничего — ручка мёртвая |
| EXECUTOR_MODEL | нигде на ПК (имя VPS-полосы) | — (0 вхождений) | ничего — ручка мёртвая |
| EXECUTOR_EFFORT | нигде на ПК (VPS; на ПК — repo-settings `effortLevel`) | `xhigh` (repo-settings) | `--effort xhigh` — передаётся ЯВНО (run_claude, коммит 463d409) |
| SUGGEST_MODEL | env, `suggest.py:193`, дефолт `fable` | fable (+фолбэк sonnet) | `--model fable --fallback-model sonnet` (suggest.py:4401) |
| THINKER_MODEL | env, `pc_orchestrator.py`, дефолт `claude-fable-5` | claude-fable-5 (+фолбэк claude-opus-4-8) | `--model claude-fable-5` (нормализация полного id, класс #194) |
| модель исполнителя | ФАКТИЧЕСКИ: repo-settings ключ `model` | `claude-fable-5` | **флага --model НЕ БЫЛО** → claude -p (cwd=REPO) молча брал repo-settings → fable |

## 2. Расхождение «запрошен sonnet — берётся fable»

`run_claude` собирал argv `claude -p --effort xhigh <prompt>` БЕЗ `--model`. CLI, запущенный с cwd=REPO,
берёт модель из repo-settings (`model: claude-fable-5`). Env-ручки ORCH_MODEL/EXECUTOR_MODEL — имена
VPS-полосы; ПК-код их не читает ни в одном файле, поэтому «запрошенный» через них sonnet никуда не
подставлялся — ручка мёртвая, исполнялся fable. Пруф прошлого замера (лог демона):
`METRICS task=990724 ... model=claude-fable-5 effort=xhigh ... dur_s=14 outcome=done`.

## 3. Что изменено (коммит 4754793)

> **⚠️ УСТАРЕЛО 30.07.2026 — головы в этом артефакте СНЯТЫ. Не разбирай по нему расход
> и качество.** Решением владельца 30.07 обе полосы переведены на **`claude-opus-5`**
> (запасная — `claude-opus-4-8`). Сегодня в коде: `EXECUTOR_MODEL = "claude-opus-5"`,
> `THINKER_MODEL` — дефолт `claude-opus-5`. Утверждение «SUGGEST_MODEL и THINKER_MODEL остаются
> на Fable» **более неверно**: имена снятой головы нормализатор моделей уводит на `claude-opus-5`.
> Верна только методика замера, но не имена моделей в ней.

- `pc_orchestrator.py`: константа `EXECUTOR_MODEL = "claude-opus-4-8"` (полный id — короткий алиас
  даёт HTTP 404, класс #194); `run_claude` теперь передаёт `--model claude-opus-4-8 --effort xhigh`
  ЯВНО; строка METRICS пишет ту же константу (минус `_repo_model`, читавший repo-settings).
- Правка САМОГО repo-settings (ключ `model`) не выполнялась — это красная зона pretool_guard
  (конфиг .claude = вектор само-эскалации); интерактивные сессии репо остаются на Fable.
- SUGGEST_MODEL и THINKER_MODEL НЕ тронуты: прайс-бот и думатель остаются на Fable (свои `--model`).
- EXECUTOR_EFFORT: остаётся `xhigh` (из repo-settings, как был).
- Тесты: голден argv исполнителя (`--model claude-opus-4-8`, `--effort xhigh`, prompt последним)
  + assert `model=claude-opus-4-8` в METRICS-тесте.

## 4. Рестарт демона штатным потоком

Штатный поток на ПК — self-update: коммит меняет блоб `pc_orchestrator.py` → демон в конце ТЕКУЩЕГО
тика гоняет гейт → эстафета новому процессу. Демон однопоточный и заблокирован на этой задаче (я —
его headless-ребёнок id=395), поэтому рестарт стартует сразу ПОСЛЕ её завершения, в этом же тике —
ДО того, как старый код успел бы взять следующую задачу. Обе ступени гейта прогнаны локально заранее:
unittest `test_pc_orchestrator + test_pc_local_dec` — **530 тестов OK**; `self_update_ok()` → `(True, 'ok')`.

## 5. Реальный замер (read-only сводка журнала, исполнена run_task с новым кодом)

Строка METRICS ДОСЛОВНО (pc_orchestrator.log):

```
2026-07-24 23:49:01,467 INFO METRICS task=990328 lane=pc model=claude-opus-4-8 effort=xhigh start=2026-07-24T16:48:21+00:00 end=2026-07-24T16:49:01+00:00 dur_s=40 outcome=done attempts=1 selfheals=0 tokens_in=na tokens_out=na
```

Модель **claude-opus-4-8**, усилия **xhigh**, длительность **40с** (реальный headless-прогон,
outcome=done). Контрольная задача **id=396** поставлена в очередь Bridge — её исполнит уже
перезапущенный демон (карточка владельцу процитирует self-update на 4754793 и свежий METRICS).
