# Вывод Fable из работы → `claude-opus-5` (обе полосы)

Дата: 2026-07-30. Решение владельца: Fable не нужен, Opus 5 для наших задач лучше.
ПК: `D:\turbobaby-bot` (HEAD `49d08df`). VPS: `/root/turbobaby-manager-bot` (HEAD `fe5c8c8`, ветка `main`).

---

## ЧАСТЬ 1. Карта мест, где задана модель (по факту, только чтение)

### 1.1 ПК — переменные окружения (`.env`)

| № | Переменная | Значение СЕЙЧАС | Файл:строка | Fable? |
|---|---|---|---|---|
| 1 | `SUGGEST_MODEL` | `sonnet` | `.env:17` | нет |
| 2 | `SUGGEST_MODEL_FALLBACK` | `sonnet` | `.env:18` | нет |
| 3 | `THINKER_MODEL` | **`claude-fable-5`** | `.env:19` | **ДА** |
| 4 | `THINKER_FALLBACK` | `claude-opus-4-8` | `.env:20` | нет |

### 1.2 ПК — дефолты в коде (всплывают, если переменной нет)

| № | Что | Значение | Файл:строка | Fable? |
|---|---|---|---|---|
| 5 | дефолт `THINKER_MODEL` (дважды в строке: `getenv` + страховка `or`) | **`claude-fable-5`** | `pc_orchestrator.py:2018` | **ДА** |
| 6 | дефолт `THINKER_FALLBACK` | `claude-opus-4-8` | `pc_orchestrator.py:2019` | нет |
| 7 | `EXECUTOR_MODEL` — **хардкод**, env-ручки на ПК не читаются | `claude-opus-4-8` | `pc_orchestrator.py:665`, вызов `:685` | нет |
| 8 | `_MODEL_ALIAS_FULL` — нормализатор коротких алиасов (гард 404, класс #194) | `fable-5`/`fable5` → `claude-fable-5` | `pc_orchestrator.py:2011–2012` | **ДА (остаточный путь)** |
| 9 | дефолт `SUGGEST_MODEL` — **клиентский модуль** | **`fable`** | `suggest.py:242` | **ДА (мёртвый: перекрыт `.env`=sonnet)** |
| 10 | дефолт `SUGGEST_MODEL_FALLBACK` | `sonnet` | `suggest.py:243` | нет |
| 11 | `ANTHROPIC_MODEL` — путь платного API, не задействован (`SUGGEST_LLM_VIA_CLI=1`) | `claude-sonnet-4-6` | `suggest.py:231`, `collect_booking.py:58` | нет |
| 12 | модель интерактивных сессий репо | `claude-opus-5` | `.claude/settings.json:2` | нет (уже Opus 5) |
| 13 | документация дефолта репо — **дрейф** | `claude-fable-5` | `CLAUDE.md:11`, `.gitignore:3` | **ДА (текст)** |

### 1.3 ПК — кто именно зовёт эти модели

**Думатель** — единая точка `_thinker_exec` (`pc_orchestrator.py:2135`); команда собирается на
`:2165` (`--model THINKER_MODEL`) и `:2168` (`--fallback-model THINKER_FALLBACK`). Потребители:

| Вызов | Файл:строка | Тег |
|---|---|---|
| самопочинка задачи | `pc_orchestrator.py:2197` | `task-selfheal` |
| планировщик локальной цепи | `pc_orchestrator.py:2515` | `pcloc-dec-plan` |
| самопочинка шага цепи | `pc_orchestrator.py:2937` | `pcloc-selfheal` |
| адаптация плана | `pc_orchestrator.py:2965` | `pcloc-plan-adapt` |
| **ревизор диалогов** | `pc_orchestrator.py:4801` | `dialog-revizor` |
| классификатор уроков | `lesson_router.py:426` | `lesson-classify` |

**Прочие головы (не Fable):** исполнитель задач — `pc_orchestrator.py:685`
(`--model claude-opus-4-8`); клиентский suggest — `suggest.py:4912` (`--model sonnet`);
экстрактор брони — `booking_draft.py:80` (через `suggest.SUGGEST_MODEL` → sonnet).

### 1.4 VPS — переменные и код (`/root/turbobaby-manager-bot`)

| № | Что | Значение СЕЙЧАС | Место | Fable? |
|---|---|---|---|---|
| 14 | `ORCH_MODEL` | `claude-opus-5` | `.env:14` | нет |
| 15 | `ORCH_MODEL_FALLBACK` | **`claude-fable-5`** | `.env:15` | **ДА** |
| 16 | `EXECUTOR_MODEL` | `claude-opus-5` | `.env:19` | нет |
| 17 | `THINKER_MODEL` | `claude-opus-5` | `.env:68` | нет |
| 18 | `CLAUDE_MODEL` | `claude-sonnet-4-5` | `.env:11` | нет |
| 19 | `SPLINTER_MODEL_LIGHT/MAIN/HEAVY` | `claude-haiku-4-5` / `claude-sonnet-4-5` ×2 | `.env:70–72` | нет |
| 20 | дефолт `ORCH_MODEL` | **`fable`** (алиас → `claude-fable-5`) | `orchestrator_daemon.py:173` | **ДА** |
| 21 | дефолт `ORCH_MODEL_FALLBACK` | **`claude-fable-5`** | `orchestrator_daemon.py:174` | **ДА** |
| 22 | нормализатор алиасов | `fable` → `claude-fable-5` | `orchestrator_daemon.py:185` | **ДА (остаточный путь)** |

### 1.5 Фолбэки — на что переключается при сбое основной

- **ПК думатель:** `claude-fable-5` → `claude-opus-4-8` (кондуктор `--fallback-model`, один вызов CLI).
- **ПК suggest:** `sonnet` → `sonnet` (фолбэк = основная, вырожденный).
- **VPS дирижёр/исполнитель:** `claude-opus-5` → **`claude-fable-5`** ← Fable всплывает именно здесь.
- Фолбэк исполняет **сам CLI** в том же вызове (`--fallback-model`), своего ретрая нет.

### 1.6 Сколько вызовов ушло на Fable за сутки

**ЧЕСТНО: счётчика нет.** `_thinker_exec` при успехе **не логирует ничего** (только `warning`
при сбое, `pc_orchestrator.py:2145/2152/2174/2177`) — модель в его строках не печатается.
Единственная строка с моделью — `METRICS`, а она покрывает исполнителя и сессии, но **не думателя**.

Что известно по фактам:
- `fable` в логах ПК за 24 ч: **0 строк** (`pc_agent.log`, `pc_orchestrator.log`,
  `dispatch_notify.log`, `moderation_bot.log`, `userbot.log`, `cowork_hook.log`).
- Последняя `METRICS` с Fable — **24.07.2026 23:52**; все `METRICS` за сутки: `model=claude-opus-4-8`.
- **Но Fable за сутки вызывался:** ревизор отработал окна с активностью 30.07 в `01:20:08` и
  `13:12:22` (в `19:13:39` — 0 окон, вызова не было), 5 строк «находок». Каждое активное окно =
  минимум один `_thinker_exec` на `claude-fable-5`.
- Итог: **≥2 вызова Fable за сутки, точного числа нет** — не потому что вызовов не было, а потому
  что модель думателя нигде не считается. Сбоев думателя за сутки не зафиксировано (0 warning-строк).

### 1.7 Что читает модель из окружения ЖИВЫХ процессов (PEB, до правки)

| PID | Процесс | `THINKER_MODEL` | `THINKER_FALLBACK` | `SUGGEST_MODEL` |
|---|---|---|---|---|
| 18096 | `pc_orchestrator.py` (демон) | **claude-fable-5** | claude-opus-4-8 | sonnet |
| 9376 | `pc_agent.py` | **claude-fable-5** | claude-opus-4-8 | sonnet |
| 9988 | `moderation_bot.py` | **claude-fable-5** | claude-opus-4-8 | sonnet |
| 1656 | `userbot_listen.py` | **claude-fable-5** | claude-opus-4-8 | sonnet |

Переменные **не постоянные** (`HKCU\Environment` и Machine — пусто по всем шести именам):
значение унаследовано от предков, стартовавших со старым `.env`.

**Ловушка `override=False`:** `pc_orchestrator.py:90` зовёт `load_dotenv` без `override=True` —
унаследованное значение **побеждает** новый `.env`. Значит правка файла без чистого рестарта
не даст ничего (ровно класс #194). Демон (PID 18096) запущен из консоли (`parent=conhost.exe`),
рестарт штатно идёт через `schtasks /Run /TN pc_orchestrator` (`pc_agent.py:509`) — Планировщик
даёт **чистое окружение**, и `.env` выигрывает.

---

## ЧАСТЬ 2. План переключения

Единая пара на обеих полосах: **основная `claude-opus-5`, фолбэк `claude-opus-4-8`** (полные
идентификаторы — короткий алиас даёт HTTP 404, класс #194).

| # | Полоса | Место | Было | Станет |
|---|---|---|---|---|
| 1 | ПК | `.env:19` `THINKER_MODEL` | `claude-fable-5` | `claude-opus-5` |
| 2 | ПК | `pc_orchestrator.py:2018` дефолт | `claude-fable-5` ×2 | `claude-opus-5` ×2 |
| 3 | ПК | `CLAUDE.md:11–13`, `.gitignore:3` | `claude-fable-5` | `claude-opus-5` |
| 4 | VPS | `.env:15` `ORCH_MODEL_FALLBACK` | `claude-fable-5` | `claude-opus-4-8` |
| 5 | VPS | `orchestrator_daemon.py:173` дефолт | `fable` | `claude-opus-5` |
| 6 | VPS | `orchestrator_daemon.py:174` дефолт | `claude-fable-5` | `claude-opus-4-8` |
| 7 | VPS | `tests/test_orchestrator_model.py:34–35`, `test_approve_layers.py:121` | зеркалят `claude-fable-5` | зеркалят новое |

**Почему фолбэк VPS — `claude-opus-4-8`, а не `claude-opus-5`:** `tests/test_orchestrator_model.py:38`
требует `ORCH_MODEL != ORCH_MODEL_FALLBACK` («основная и фолбэк — разные модели»). Фолбэк, равный
основной, — вырожденный (при недоступности Opus 5 добивать нечем) и **красит гейт VPS, блокируя
push всему репозиторию**. Та же пара уже стоит у думателя ПК.

### Не трогаю (по границам задачи)

- `suggest.py:242` — дефолт `fable` в **клиентском** модуле. Правка файла запрещена границей;
  переменная `SUGGEST_MODEL=sonnet` уже стоит в `.env` и перекрывает дефолт, поэтому Fable оттуда
  **не всплывает**. Остаётся как мёртвая строка — вынести/поменять только по слову владельца.
- `EXECUTOR_MODEL=claude-opus-4-8` (ПК, `:665`) — **не Fable**, вне объёма «убрать Fable».
  Если нужен Opus 5 и у исполнителя — отдельное слово владельца.
- Нормализаторы алиасов (`pc_orchestrator.py:2011`, `orchestrator_daemon.py:185`) — это гард
  от 404, не выбор модели. **Остаточный риск:** застрявшее в окружении `THINKER_MODEL=fable-5`
  нормализуется в `claude-fable-5` и Fable вернётся. Закрывается чистым рестартом (см. проверку
  по PEB) — но если владелец хочет «намертво», строку можно перенаправить на Opus 5.
- `SUGGEST_TEST_MODE`, `pretool_guard`, `deny`-список, чужой WIP.

### Что придётся перезапустить и что при этом прервётся

| Процесс | Зачем | Что прервётся |
|---|---|---|
| **ПК: `pc_orchestrator.py` PID 18096** | `THINKER_MODEL` читается на импорте — иначе Fable живёт до перезагрузки | текущий тик демона: авто-фетч, цикл ревизора, исполнение задачи в очереди; очередь после старта продолжится |
| **ПК: `pc_agent.py` PID 9376** | его окружение несёт стухший `claude-fable-5` и он порождает подпроцессы `pc_orchestrator.py` (действия над цепью, `:616`), которые унаследуют Fable (`override=False`) | бот управления ~секунды offline; `userbot_listen`(1656)/`moderation_bot`(9988) — **сироты**, их рестарт не заденет |
| **VPS: `orchestrator-daemon.service`** | `.env` читается на старте | задача, идущая в очереди дирижёра |

**Не требуют рестарта:** `userbot_listen`, `moderation_bot` (живут на `SUGGEST_MODEL=sonnet`,
не меняется), `splinter.service` (свои `SPLINTER_MODEL_*`), RC-сессии.

---

## ЧАСТЬ 3. Что сделано по факту

Решения владельца 30.07.2026: пара **`claude-opus-5` → `claude-opus-4-8`** на обеих полосах;
нормализатор алиасов обязателен; исполнитель ПК тоже на Opus 5; `suggest.py` не трогать.

### Было → стало

| Полоса | Место | Было | Стало |
|---|---|---|---|
| ПК | `.env:19` `THINKER_MODEL` | `claude-fable-5` | `claude-opus-5` |
| ПК | `pc_orchestrator.py:2020` дефолт `THINKER_MODEL` | `claude-fable-5` ×2 | `claude-opus-5` ×2 |
| ПК | `pc_orchestrator.py:666` `EXECUTOR_MODEL` | `claude-opus-4-8` | `claude-opus-5` |
| ПК | `pc_orchestrator.py:2016` нормализатор | имена снятой → `claude-fable-5` | **все** имена снятой → `claude-opus-5` |
| ПК | `CLAUDE.md`, `.gitignore` | дефолт репо назван снятой моделью | `claude-opus-5` + правило пары |
| VPS | `.env:15` `ORCH_MODEL_FALLBACK` | `claude-fable-5` | `claude-opus-4-8` |
| VPS | `orchestrator_daemon.py:173` дефолт `ORCH_MODEL` | `fable` (короткий → 404) | `claude-opus-5` |
| VPS | `orchestrator_daemon.py:174` дефолт фолбэка | `claude-fable-5` | `claude-opus-4-8` |
| VPS | `orchestrator_daemon.py:195` `_MODEL_RETIRED` | не было | слой поверх алиасов → `claude-opus-5` |

Зеркальные тесты догнаны В ТОМ ЖЕ заходе на обеих полосах.
`suggest.py` не тронут (граница клиентского контура). Проверка «основная ≠ фолбэк» не ослаблена.

**Одна проверка уточнена, и это надо знать:** `tests/test_approve_layers.py` стерёг подстроку
`claude-opus-4-8` как «мёртвую модель» и тем самым запретил бы новый живой фолбэк. Мёртвой была
`claude-opus-4-8[1m]` (1M-вариант, 404) — страж уточнён до этого литерала. Смысл сохранён.

### Коммиты и гейт

| Полоса | Коммит | Гейт |
|---|---|---|
| ПК | `6f556f4` | полный прогон: **2294 теста, OK** (skipped=10) |
| VPS | `47978e4`, `d81a62d` (комментарии) | `gate.py`: **140 тестов зелёные**, оба раза |

### Проверка: модель ЖИВЫХ процессов (из окружения, не из файла)

ПК — чтение PEB (`OpenProcess` + `ReadProcessMemory`; на Windows запись `os.environ` попадает
в блок окружения процесса, поэтому PEB показывает ЭФФЕКТИВНОЕ значение):

| Процесс | PID | `THINKER_MODEL` из окружения |
|---|---|---|
| `pc_orchestrator.py` (перезапущен, было 18096) | **11976** | **claude-opus-5** |
| `pc_agent.py` (перезапущен, было 9376) | **6612** | **claude-opus-5** |
| `moderation_bot.py` (НЕ перезапускали — решение владельца) | 9988 | claude-fable-5 (стухшее, см. ниже) |
| `userbot_listen.py` (НЕ перезапускали) | 1656 | claude-fable-5 (стухшее, см. ниже) |
| `rc_supervisor.py` | 11168 | переменной нет вовсе |

**Почему стухшее значение у двух клиентских ботов безопасно.** Они сами думателя не зовут, но
`moderation_core.py:328` и `trainer.py:593` могут ЛЕНИВО подтянуть `pc_orchestrator` /
`lesson_router`. Проверено запуском с ровно таким грязным окружением:

```
THINKER_MODEL=claude-fable-5  → модуль поднялся с THINKER_MODEL=claude-opus-5
THINKER_MODEL=fable-5         → модуль поднялся с THINKER_MODEL=claude-opus-5
```

То есть нормализатор закрывает чёрный ход: даже неперезапущенный бот получит Opus 5.

VPS — `/proc/<pid>/environ` по этим именам **пуст**: демон читает `.env` сам (python-dotenv), в
окружение процесса значения не попадают. Живое значение процесса — его стартовый баннер:

```
12:47:14  === ДЕМОН СТАРТ … model=claude-opus-5, fallback=claude-fable-5,  executor_model=claude-opus-5 …
12:51:58  === ДЕМОН СТАРТ … model=claude-opus-5, fallback=claude-opus-4-8, executor_model=claude-opus-5 …
```

`orchestrator-daemon` перезапущен, `active`, MainPID 216271 → **217816**.

### Проверка: живой вызов

```
claude -p --model claude-opus-5   → rc=0, ответ 'OK', modelUsage: claude-opus-5
claude -p --model claude-opus-4-8 → rc=0, ответ 'OK', modelUsage: claude-opus-4-8   (фолбэк ЖИВОЙ, не 404)
_thinker_exec(...) боевым путём    → вернул 'OK' на THINKER_MODEL=claude-opus-5
```

### Где имя снятой головы осталось — и почему

- **Ключи снятия** (`pc_orchestrator.py:2016–2017`, `orchestrator_daemon.py:185/195` и тесты на
  них): убрать нельзя — тогда застрявшее значение уйдёт в `claude -p` как есть и вернёт 404.
  Слева это ключ, справа везде `claude-opus-5`.
- **Клиентский контур** (`suggest.py:241–242`, `test_suggest.py`): по прямому указанию владельца
  не трогаем; дефолт перекрыт `.env` (`SUGGEST_MODEL=sonnet`) и не стреляет.
- **Моки `modelUsage`** в тестах обеих полос: произвольные строки-ключи, выбор модели не задают.
- **История**: `docs/`, `docs/artifacts/`, логи, `tmp/write_brain.py`, история коммитов.
  `docs/revizor_recon.md:129` — датированный снимок разведки, он теперь устарел; переписывать
  снимок = подделывать запись, поэтому оставлен как есть.
