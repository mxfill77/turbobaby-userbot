# Причина провала unittest-гейта self-update (01.09.2026)

Задача 70. Только диагноз: ничего не чинилось, тесты не правились, пороги не трогались,
процессы не перезапускались.

## Вывод одной строкой (пункт 4 задания)

**Фикс гарда тесты НЕ ломал.** Гейт был красным ДО него — с коммита `2f015bb` (30.08.2026), и
self-update заблокирован независимо от коммита `3b911e5`. Три падения на коммите фикса — те же
самые три, что и на коммите `6cbdd15`, откуда демон не смог переехать; новых падений **ноль**.

## 1. Живой формат гейта — снят с кода, а не придуман

`pc_orchestrator.py:4059` → `_gate_unittests()`. Дословно то, что запускает демон:

```
D:\turbobaby-bot\venv\Scripts\python.exe -m unittest test_pc_orchestrator test_pc_local_dec
cwd = D:\turbobaby-bot
env = os.environ демона МИНУС THINKER_MODEL и THINKER_FALLBACK
timeout = 600 с
```

Именно этой командой (тот же интерпретатор, тот же cwd, та же вычистка двух ключей env) сняты
все четыре прогона ниже. Живая строка провала демона:

```
2026-09-01 11:14:06,218 INFO  self-update: изменилось pretool_guard.py, suggest.py (6cbdd15→3b911e5) — гоняю гейт
2026-09-01 11:14:26,736 ERROR self-update: unittest-гейт ПРОВАЛЕН (6cbdd15→3b911e5): … Ran 983 tests in 19.284s / FAILED (failures=3) — остаюсь на старом коде
```

(`pc_orchestrator.log`, строки 9722–9723.)

### Что упало — дословно, три имени и число

**983 теста, `FAILED (failures=3)`, ошибок (`ERROR`) ноль.** Имена:

| # | тест | ассерт | вывод |
|---|---|---|---|
| 1 | `test_pc_orchestrator.TestApprovalReachesExecutor.test_orch_runtime_covers_every_top_import_of_daemon` | `test_pc_orchestrator.py:841` | `Lists differ: ['proc_identity.py'] != []` — «верхние импорты демона вне `_ORCH_RUNTIME`» |
| 2 | `test_pc_orchestrator.TestSelfUpdateDepClosure.test_dirty_gate_watches_exactly_what_start_loads_unconditionally` | `test_pc_orchestrator.py:10706` | `Items in the first set but not the second: 'proc_identity.py'` |
| 3 | `test_pc_orchestrator.TestSelfUpdateDepClosure.test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap` | `test_pc_orchestrator.py:10713` | `Items in the first set but not the second: 'proc_identity.py'` |

Причина у трёх падений **одна**: `pc_orchestrator.py:63` несёт верхний (модульный) импорт
`import proc_identity`, а ни `_ORCH_RUNTIME` (`pc_orchestrator.py:5737`), ни названный остаток
`_ORCH_LAZY_UNCOVERED` (`pc_orchestrator.py:5807`) файла `proc_identity.py` не содержат. Три
теста смотрят на один и тот же разрыв с трёх сторон (прямые импорты · транзитивное замыкание ·
названный остаток), поэтому один пропуск даёт ровно три красных.

## 2. Тот же гейт на предыдущем коммите `6cbdd15`

Дерево коммита `6cbdd15` («feat(hq): add verified fixture-only context pack») развёрнуто
`git archive` в **`tmp/task70_gate/prev6cbdd15`** (ничего не удалялось, живое дерево не трогалось),
гейт запущен той же командой и тем же `venv`-интерпретатором с `cwd` = это дерево:

```
Ran 983 tests in 18.983s
FAILED (failures=3)
FAIL: test_orch_runtime_covers_every_top_import_of_daemon
FAIL: test_dirty_gate_watches_exactly_what_start_loads_unconditionally
FAIL: test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap
```

**Сравнение двух списков: НОВЫХ падений — 0, все три были и до фикса.**

Подтверждение вторым, независимым способом — по git, а не по прогону: коммит фикса гарда не
трогал ни одного файла, который гейт вообще исполняет.

```
git diff --stat 6cbdd15 8051768 -- pc_orchestrator.py test_pc_orchestrator.py test_pc_local_dec.py  → пусто
blob id pc_orchestrator.py      6cbdd15 = cfd6b95746cc  = HEAD cfd6b95746cc
blob id test_pc_orchestrator.py 6cbdd15 = ae7121ee21ca  = HEAD ae7121ee21ca
blob id test_pc_local_dec.py    6cbdd15 = 111ff1a3b90f  = HEAD 111ff1a3b90f
```

`3b911e5` трогал `pretool_guard.py`, `suggest.py`, `price_source.json`, `expectations_pc_run.py`
и свои тесты — ни один из них в команду гейта не входит.

### Мина, которую здесь легко не заметить

Три упавших теста читают исходник **по АБСОЛЮТНОМУ пути** `o.REPO` = `D:\turbobaby-bot`
(`test_pc_orchestrator.py:822`, `:10701`), а не по своему дереву. Значит «прогнать гейт на другом
коммите», разложив его копию в сторонке, в общем случае **невозможно**: тесты всё равно измерят
живое `D:\turbobaby-bot`. Здесь это ничего не портит ровно потому, что `pc_orchestrator.py`
побайтно один и тот же в `6cbdd15` и в HEAD (blob id выше), — но вывод «гейт зелёный на ветке»
из такого прогона в другом случае был бы born-false. Тот же класс, что и хардкод ROOT в
гард-тесте VPS.

Отсюда же следствие про сам механизм: `_gate_unittests` гоняет тесты **по диску**, то есть по
НОВОМУ коду. Дедлока нет: коммит, добавляющий `proc_identity.py` в `_ORCH_RUNTIME`, гейт увидит
уже исправленным. Но пока такого коммита нет — красное блокирует ЛЮБОЙ новый коммит, чем бы тот
ни был.

## 3. Три прогона подряд одной командой

| прогон | дерево | тестов | итог | время |
|---|---|---|---|---|
| live #1 | живое (HEAD `8051768`) | 983 | `FAILED (failures=3)` | 18.3 с |
| live #2 | живое | 983 | `FAILED (failures=3)` | 17.6 с |
| live #3 | живое | 983 | `FAILED (failures=3)` | 17.7 с |
| prev | `tmp/task70_gate/prev6cbdd15` | 983 | `FAILED (failures=3)` | 19.0 с |

**Числа совпали, списки имён совпали 3/3 (сверка `diff` по отсортированным именам).** Признака
хождения тестов в живую сеть в этих прогонах нет.

Отдельно называю **вторую, ДРУГУЮ полосу провалов**, которую эти прогоны не воспроизвели: за
22–31.08 гейт падал **10 раз по таймауту 600 с** («unittest-гейт не запустился: … timed out after
600 seconds»), и во всех 10 случаях в изменившемся наборе присутствовал `suggest.py` (10 из 10).
Сегодня набор тоже содержал `suggest.py`, а прогон уложился в 18–20 с — значит корреляция
класс не объясняет, и корень зависаний остаётся **неизвестным**. Это остаток, а не диагноз.

## 4. Когда родилось красное — замер

| факт | значение |
|---|---|
| `import proc_identity` внесён в демона | `2f015bb`, 30.08.2026 (`git log -S` по `pc_orchestrator.py` — ровно один коммит) |
| в родителе `5aaef57` импорта нет | `git show 5aaef57:pc_orchestrator.py | grep -c` → **0**; в `2f015bb` → **1** |
| `_ORCH_RUNTIME` последний раз правился | `e3d32ba`, 07.08.2026 — то есть **до** появления файла |
| `proc_identity` в `_ORCH_RUNTIME` на HEAD | **0** совпадений |
| `2f015bb` — предок `6cbdd15`? | **да**: работающий демон уже несёт дефект в себе |

Первый заход гейта после `2f015bb` (30.08 20:31:49, `99f7817→2f015bb`) вердикта не дал: через
6 минут демона не стало, его поднял watchdog через `schtasks`, и новый процесс стартовал уже с
`commit=a0e56d2`. Поэтому в лог красное «failures=3» попало только 01.09 — но родилось оно 30.08.

## 5. Общая картина блокировки (контекст, не диагноз)

- Последний **успешный** self-update: `2026-08-22 14:01:53`, `0be1ea6→bcc3f63`. После него —
  ни одного за 10 суток.
- Текущий демон стартовал `2026-08-31 19:43:06` с `commit=6cbdd15` — то есть переехал **рестартом**
  (watchdog/schtasks), а не эстафетой self-update.
- Механизм отказа устойчив: `maybe_self_update` запоминает провалившийся блоб в `_SU_REJECTED_BLOB`
  и не перегоняет гейт до следующего коммита — каждый новый коммит платит одним прогоном и
  получает тот же отказ.

## 6. Чего в этой работе нет (честно)

Ничего не починено: `_ORCH_RUNTIME` не расширен, тесты не тронуты, порогов не меняли, демон не
перезапускался, ничего не выкатывалось. Клиентский контур не трогался. Временное — только в
`tmp/task70_gate/` (4 файла вывода прогонов + развёрнутое дерево `prev6cbdd15`), не удалялось.

## Как повторить замер

```
cd D:\turbobaby-bot
env -u THINKER_MODEL -u THINKER_FALLBACK ./venv/Scripts/python.exe -m unittest test_pc_orchestrator test_pc_local_dec
```
