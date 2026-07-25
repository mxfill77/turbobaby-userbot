# Странности инструментов проверки на VPS — разбор и частичная починка

**Дата:** 25.07.2026 · **Контур:** VPS `splinter` (5.223.94.179), репо `turbobaby-manager-bot`
**Режим:** диагностика 25.07 04:41 (read-only) + малый заход по починке 25.07 (шаг 7 оборвался)

---

## Странность 1: гейт при push из ветки проверяет дерево `main`

### Факты (read-only, 25.07 04:41)

Три worktree одного репо:

| путь | ветка | HEAD |
|---|---|---|
| `/root/turbobaby-manager-bot` | `main` | `1bc3051` |
| `/root/baseline-origin-wt` | detached | `7af280c` |
| `/root/rebuild-main-wt` | `rebuild-main` | `26d2c6d` |

`core.hooksPath = deploy/hooks` лежит в общем `.git/config` и путь **относительный** →
каждый worktree берёт свой файл хука. Файлы одинаковы байт-в-байт (sha1 `7b249ea6b4`),
и вся суть в 8-й строке:

```sh
exec /root/turbobaby-manager-bot/venv/bin/python3 /root/turbobaby-manager-bot/gate.py --for push --final
```

Путь **абсолютный, прибит к главному чекауту**. Дальше `gate.py:31`:

```python
ROOT = os.path.dirname(os.path.abspath(__file__))   # = /root/turbobaby-manager-bot
```

и от `ROOT` растёт всё остальное:

| строка `gate.py` | что берётся от ROOT |
|---|---|
| `:32` | `PY = ROOT/venv/bin/python3` |
| `:35` | `TESTS_DIR = GATE_TESTS_DIR or ROOT/tests` |
| `:50` | `load_dotenv(ROOT/.env)` |
| `:86`, `:164` | `env = dict(os.environ, PYTHONPATH=ROOT, PRETOOL_NOPUSH="1", ORCH_TEST_MODE="1")` |
| `:93`, `:127`, `:173`, `:188` | `subprocess.run(..., cwd=ROOT)` |

Ни `checkout`, ни `stash`, ни второго клона. Механизм проще: **гейт вообще не знает,
что идёт push** — хук не читает stdin (`<local ref> <local sha> <remote ref> <remote sha>`)
и не передаёт гейту ни рефов, ни корня. `_changed_py_files()` (`:127`) тоже делает git-вызов
с `cwd=ROOT`, поэтому «изменения» селективного режима = грязное рабочее дерево main.

Демоны живут там же (`orchestrator-daemon`, `splinter`: `WorkingDirectory=/root/turbobaby-manager-bot`) —
гейт защищает **живой чекаут**, а не отгрузку.

### Предложенная правка (НЕ применена — владелец снял хук из объёма)

Хук — единственное место, куда правка доезжает вместе с веткой (файл версионирован):

```sh
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
[ -n "$ROOT" ] || ROOT="$(pwd)"
PY="$ROOT/venv/bin/python3"; [ -x "$PY" ] || PY=/root/turbobaby-manager-bot/venv/bin/python3
GATE="$ROOT/gate.py";        [ -f "$GATE" ] || GATE=/root/turbobaby-manager-bot/gate.py
if [ -t 0 ]; then REFS=""; else REFS="$(cat)"; fi
exec env GATE_ROOT="$ROOT" GATE_PUSH_REFS="$REFS" GATE_PUSH_REMOTE="${1:-}" "$PY" "$GATE" --for push --final
```

плюс `gate.py:31` → `ROOT = os.environ.get("GATE_ROOT") or os.path.dirname(os.path.abspath(__file__))`,
плюс `_changed_py_files()` в селективном режиме считать от диапазона push
(`git diff --name-only <remote_sha>..<local_sha>`, при `remote_sha` = `000…` — от merge-base
с `origin/main`).

Оговорки: у `rebuild-main-wt` свой untracked `venv` и **свой `.env`** — после правки гейт
начнёт читать `.env` ветки. И правка подействует для ветки только после того, как ветка
её в себя вберёт (хук версионирован).

**Статус: НЕ ПРИМЕНЕНО.**

---

## Странность 2: тест краснеет пофайлово и зеленеет в окружении гейта

### Корень (общий класс)

`gate.py` гоняет тесты **как отдельные скрипты** — `venv/bin/python3 tests/test_X.py`
(`:93`, `:188`), а не через pytest. **pytest в venv не установлен вовсе** (факт прогона:
`No module named pytest`). Значит оба `conftest.py` не читаются никогда в боевом контуре:

- корневой `conftest.py` (26 строк) — ставит `PRETOOL_NOPUSH=1`;
- `tests/conftest.py` (42 строки) — ставит `ORCH_TEST_MODE=1`, `BRIDGE_URL=http://x`,
  `BRIDGE_TOKEN=x` и вешает autouse-фикстуру `ban_external_network`.

Всю эту подготовку в штатном прогоне даёт **только** `env` гейта
(`PYTHONPATH=ROOT, PRETOOL_NOPUSH=1, ORCH_TEST_MODE=1`). Отсюда правило:
любой тест, чьё поведение зависит от `ORCH_TEST_MODE`, но который сам его не ставит,
**обязан** расходиться между «пофайлово» и «в гейте».

### Конкретный носитель: `tests/test_convert_loop_break.py`

Отбор по факту: единственный файл в `tests/`, чьё имя отвечает теме петли, и он же —
в списке «есть модульный `os.environ`, нет `ORCH_TEST_MODE`». Шапка до правки:

```python
import os, sys, datetime
sys.path.insert(0, "/root/turbobaby-manager-bot")
os.environ.setdefault("BRIDGE_URL", "http://x"); os.environ.setdefault("BRIDGE_TOKEN", "x")
os.environ.setdefault("PLAN_ADAPT", "0")  # изоляция от боевого .env (адаптация плана, кусок 2)
os.environ["CURATOR"] = "0"  # изоляция от боевого .env (куратор целей, родитель 231; …)
```

Изоляция от боевого `.env` есть, тестового режима — нет.

**Расхождение ДО правки (дословно, оба прогона из `/root/turbobaby-manager-bot`):**

| команда | итог |
|---|---|
| `venv/bin/python3 tests/test_convert_loop_break.py` | `ИТОГ: ЕСТЬ FAIL (18/20)`, `exit=1` |
| `PYTHONPATH=… PRETOOL_NOPUSH=1 ORCH_TEST_MODE=1 venv/bin/python3 tests/test_convert_loop_break.py` | `ИТОГ: ВСЕ PASS`, `exit=0` |

Красные ровно два чека, оба про тело ручной карточки:

```
  FAIL тело = «✋ ТРЕБУЕТСЯ РУЧНОЕ ДЕЙСТВИЕ (это не сбой)» + инструкция
  FAIL карточка красного действия сохранена в теле
```

### Правка (применена в рабочее дерево, одна строка)

```diff
 os.environ["CURATOR"] = "0"  # изоляция от боевого .env (куратор целей, родитель 231; …)
+os.environ.setdefault("ORCH_TEST_MODE", "1")
```

**Расхождение ПОСЛЕ правки:** оба прогона `ИТОГ: ВСЕ PASS`, `exit=0`,
вывод (без таймингов) **идентичен**.

### Что осталось классом

Правка чинит один файл. Структурный разрыв («подготовка окружения живёт в conftest,
который никто не читает») остаётся для всех остальных тестов. Родовое лечение —
бутстрап-раннер `tests/_bootstrap_run.py`, через который `gate.py` запускал бы каждый
тест; спроектирован, но в объём малого захода не вошёл.

---

## Побочный факт: откат тестов моделей красит дерево main

По решению владельца два теста моделей в главном дереве откачены (`git checkout --`):
их правка **дословно дублирует ветку** `rebuild-main` (`git diff rebuild-main -- <файл>` = 0 строк),
откаченный дифф сохранён в `/root/_gatefix_backup_20260725/discarded_model_tests.patch`.

Последствие зафиксировано прогоном сразу после отката:

```
tests/test_executor_model.py     -> exit=1 | ИТОГ: ЕСТЬ FAIL (17/18)
tests/test_orchestrator_model.py -> exit=1 | ИТОГ: ЕСТЬ FAIL (11/13)
```

Причина — известный класс: эти тесты **зеркалят литералами боевой `.env`**
(`ORCH_MODEL=claude-opus-5`, `ORCH_MODEL_FALLBACK=fable`, `EXECUTOR_MODEL=claude-opus-5`),
а `HEAD` главного дерева всё ещё ждёт `fable` / `claude-opus-4-8[1m]`. Пока `rebuild-main`
не влит, **гейт из главного дерева красный по этим двум тестам** — и это следствие
осознанного решения, а не дефект правки петли.

---

## Незакрытый хвост

Прогон 25.07 оборвался на шаге «гейт целиком»: вывод кончается эхом команды, результата
и завершающего маркера нет. Вероятная механика — `outG=$(timeout 240 … gate.py …)`:
`timeout` убивает сам гейт, но подстановка `$(…)` продолжает ждать осиротевших
детей-подпроцессов, держащих stdout, поэтому заход упёрся в потолок клиента, а удалённый
`bash -s` умер по обрыву ssh раньше, чем дошёл до коммита.

### Проверено чтением 25.07 22:13 UTC

- `git status --porcelain` = ` M tests/test_convert_loop_break.py` — **только правка петли,
  незакоммичена**; тесты моделей откачены и в статусе не значатся.
- `git log --all --oneline --grep='self-contained env for loop-break'` — **пусто**,
  коммита нет ни в одной ссылке. `HEAD` = `1bc3051` (не сдвинулся).
- Правка жива в рабочем дереве: `14:os.environ.setdefault("ORCH_TEST_MODE", "1")`;
  в `HEAD` этой строки нет.
- Тест обоими способами: `ИТОГ: ВСЕ PASS`, `exit=0` — расхождение снято.
- Осиротевших `tests/test_*` и висящего `gate.py` **нет**; `splinter` (PID 1275) и
  `orchestrator-daemon` (PID 41990) активны, `NRestarts=0`, load 0.20, свободно 6.3 ГБ.

**Открыто:** коммит `tests: self-contained env for loop-break test` не сделан —
правка ждёт в рабочем дереве главного чекаута. Гейт целиком так и не прогонялся;
напоминание: он будет красным по двум откаченным тестам моделей до влития `rebuild-main`.
