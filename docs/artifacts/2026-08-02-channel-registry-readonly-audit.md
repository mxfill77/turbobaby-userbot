# Реестр каналов: read-only перепроверка + два новых дефекта (02.08.2026)

Заход read-only: ничего не менялось, не коммитилось, боевых записей нет, `.env` не открывался.
Все числа — замер сегодняшний, все строки в кавычках — дословные.

## 1. Что в коммитах 81e524e и f18c744

**81e524e** «реестр каналов: имя инструмента вне реестра не проходит молча», 02.08 18:27:43.
3 файла, `478 insertions(+), 4 deletions(-)`: `pretool_guard.py` +96/−4, `test_pretool_guard.py`
+147, `docs/artifacts/2026-08-02-channel-registry-fail-closed.md` 239 строк.

**Матчер каналов правкой НЕ ТРОНУТ.** `.claude/settings.json` в диффе отсутствует; последний
коммит по этому файлу — `e769a82`, `PowerShell` в matcher добавлен ещё 23.07 (`ba339f9`). Живой
matcher сегодня:

```
matcher= 'Bash|PowerShell|Edit|Write|Read|MultiEdit|NotebookEdit'
   cmd= D:/turbobaby-bot/venv/Scripts/python.exe D:/turbobaby-bot/pretool_guard.py
```

Изменилась ПОСЛЕДНЯЯ СТРОКА `decide` (`pretool_guard.py:2700`). Было:

```python
    return ("defer", "", "")  # Grep/Glob/прочие read-only инструменты
```

Стало:

```python
    if channel_status(tool) == CHANNEL_GREEN:
        return ("defer", "", "")          # держатель НАЗВАН в реестре, см. `_TOOL_CHANNELS`
    # Канала нет в реестре: не «read-only по умолчанию», а неизвестный канал → fail-closed.
    return ("ask", KIND_UNKNOWN_TOOL, (tool or "").strip() or UNNAMED_TOOL)
```

Заведён реестр `_TOOL_CHANNELS` — 9 записей: 7 `CHANNEL_GUARD` (`Bash`, `Edit`, `MultiEdit`,
`NotebookEdit`, `PowerShell`, `Read`, `Write`) и 2 `CHANNEL_GREEN` (`Glob`, `Grep`), у каждой
записи НАЗВАННЫЙ держатель. Множество гардованных дословно совпадает с 7 именами живого matcher.

Два пояса под fail-closed: `KIND_UNKNOWN_TOOL` внесён в `_stays_red` (`:3193`) и в `_HARD_CARD`
(`:3389`), плюс в `_KIND_VOCAB` (`:2815`) и в `_ROLLBACK` (`:3314`). Без первого решение утекло бы
в `defer`, без второго — пустое имя инструмента дало бы `card_gate=False` → `journal`.

**Сторож заведён:** класс `TestChannelRegistry` в `test_pretool_guard.py`, 6 тестов. Список
каналов ВЫВОДИТСЯ, а не хранится литералом: гардованные имена сверяются с живым matcher из
`.claude/settings.json` и с именами, которые различает исходник `decide` (через `ast`), в обе
стороны. Прогоны из тела коммита: красный до (6 failures + 4 errors, `'defer' != 'ask'`), зелёный
после (6 OK), полный гейт 2808 OK (skipped=10).

**Неизвестное имя инструмента теперь** (замер, живой модуль):

| вход | `decide` вернул |
|---|---|
| `WebFetch` | `('ask', 'unknown_tool', 'WebFetch')` |
| `Task` | `('ask', 'unknown_tool', 'Task')` |
| `Grep` | `('defer', '', '')` |
| `tool_name` пуст | `('ask', 'unknown_tool', 'имя инструмента не названо')` |

`_stays_red('unknown_tool',…)` = `True`, в `_HARD_CARD` = `True`, в `_KIND_VOCAB` = `True`,
`card_decision` = `ask`. Карточка дословно:

```
🔴 Хочу выполнить операцию каналом вне реестра — инструмент WebFetch — разрешить?
Объект: WebFetch
Число: —
Откат: неизвестен — гард не знает, что делает этот инструмент
```

**f18c744** «тело журнальной записи о реестре каналов (вынос по LINE_MAX)», 18:29:41. 1 файл,
14 строк: `docs/artifacts/journal/2026-08-02-112910-done.md`. Кода не касается — это тело
журнальной строки, вынесенное писателем автоматически: **868 символов при пороге 600**.

**189c9a0** (HEAD, 18:45:57) — тот же класс: сторож читал `.claude/settings.json` и не видел
второй файл прав. Боевой `pretool_guard.py` не тронут ни строкой, правится только тест
(+66/−8). Гейт 2809 OK (было 2808).

## 2. DEFER: пропускает

`defer` **ПРОПУСКАЕТ операцию**. Не спрашивает и не блокирует. Место в коде — `main`,
`pretool_guard.py:4041-4045`:

```python
    if deny is not None:
        _emit_deny(deny)
    if card is not None:
        _emit_ask(card, kind)
    sys.exit(0)  # defer / journal
```

Механика: карточка печатается только через `_emit_ask` (`:3990-3995`, единственное место, где
хук выводит `"permissionDecision": "ask"`), отказ — только через `_emit_deny` (`:4003-4008`,
`"deny"`). На `defer` не вызывается ни то, ни другое: процесс молча выходит с кодом 0 **без
единой строки stdout**. Хук, не напечатавший `permissionDecision`, решения не принимает вовсе —
дальше решает glob-слой прав, где в `allow` стоят `Bash`, `Bash(*)`, `PowerShell`,
`PowerShell(*)`, `Read`. То есть `defer` = «гард промолчал» = операция исполняется.

Ровно поэтому прежняя последняя строка `decide` (`return ("defer","","")` любому имени вне семи)
и была дырой, а не мягкой веткой.

## 3. Живой факт: реестр в проде, но выстрелить ему сегодня негде

**Гард — в проде.** Файл на диске совпадает с HEAD побайтно:

```
git hash-object pretool_guard.py   → 067b8c2a95369170059b0e61892b61519d97b105
git rev-parse HEAD:pretool_guard.py→ 067b8c2a95369170059b0e61892b61519d97b105
git status --porcelain pretool_guard.py test_pretool_guard.py → (пусто)
HEAD = 189c9a076af6bfebf5aa922c94246498e3a00266,  wc -l pretool_guard.py = 4049
```

Хук — отдельный процесс на КАЖДЫЙ вызов инструмента (команда в matcher запускает
`pretool_guard.py` заново), поэтому новый код действует с первой же секунды после коммита;
замер выше сделан импортом того же живого файла.

**Демон — на СТАРОМ коде в памяти.** Процессы (read-only, ничего не трогалось):

| PID | процесс | старт |
|---|---|---|
| 18892 | `pc_orchestrator.py` | 02.08.2026 12:38:32 |
| 12124 | `pc_agent.py` | 31.07.2026 12:58:42 |
| 12444 | `rc_supervisor.py` | 31.07.2026 0:26:07 |
| 10080 | `moderation_bot.py` | 31.07.2026 12:58:50 |
| 19284 | `userbot_listen.py` | 31.07.2026 16:07:18 |

Демон PID **18892** поднят self-update'ом `00c41dc→321dc3d` в 12:38:32 — за **5 ч 49 мин** до
коммита 81e524e. Он держит `pretool_guard` в памяти (`pc_orchestrator.py:61 import pretool_guard`),
а импорт был из дерева `321dc3d`:

```
git show 321dc3d:pretool_guard.py | grep -c "unknown_tool\|_TOOL_CHANNELS"  → 0
grep -c "unknown_tool\|_TOOL_CHANNELS" pretool_guard.py                     → 5
```

Маркеров нового кода в памяти демона **нет**. И не появится сам собой: self-update сравнивает
блоб ТОЛЬКО одного файла — `_blob_hash()` = `git rev-parse HEAD:pc_orchestrator.py`
(`pc_orchestrator.py:2729`), а все три коммита реестра `pc_orchestrator.py` не касались. Пока
демон не перезапустится, его `kinds_from_card` / `is_top_tier` / `approval_covers` про вид
`unknown_tool` не знают.

**Сработать реестру сегодня негде.** В `pretool_guard.log` слово `unknown_tool` встречается
**3 раза**, и все три — тексты команд-проб и грепов (строки 2582, 2618, 2661), НИ ОДНОГО живого
решения. Причина честная и названа в самом коммите: имя вне matcher до гарда не доходит —
хук на нём просто не запускается. Fail-closed сегодня = готовность к расширению matcher плюс
сторож, который это расширение заметит.

## 4. Два новых дефекта (только названы, не чинились)

### Д-1. Карточка называет один файл, откат — другой. ПОДТВЕРЖДЁН

Живой случай, задача 189. `pretool_guard.log:2636`:

```
2026-08-02 18:47:52 | headless | Bash | ask | edit_claude | venv/Scripts/python.exe cowork_log_append.py - <<'EOF' DONE Dispatch 18:46: …
```

Карточка ушла владельцу, `dispatch_notify.log:6256` дословно:

```
2026-08-02 18:47:53,650 | итог: channel=DM ok=True | 🔴 Хочу изменить конфиг Claude Code (.claude): .claude/settings.local.json — разрешить?
```

Голова карточки и строка `Объект:` называют `.claude/settings.local.json` (объект приходит из
`m.group(0)` матча `_RE_CLAUDE_CFG_CMD`, `pretool_guard.py:2518`). А строку отката `_card`
(`:3526`) берёт из словаря БЕЗ оглядки на объект — `_ROLLBACK["edit_claude"]`, `:3307`:

```python
    "edit_claude": "Откат: git checkout -- .claude/settings.json (файл под git)",
```

Замер (`g._rollback('edit_claude','','')`) вернул её дословно. Итог: **в одной карточке два
разных файла**, и утверждение «файл под git» для НАЗВАННОГО объекта ложно:

```
git check-ignore -v .claude/settings.local.json → .gitignore:8:.claude/*
git ls-files .claude/                          → .claude/settings.json   (один файл)
```

Цена не косметическая: владелец, выполнивший предложенный откат буквально, откатит ДРУГОЙ файл —
отслеживаемый `settings.json`, — а названный в карточке объект не изменится вовсе, потому что он
git'у не известен. Это тот самый класс, который чинили 31.07 (`6250c22` «объект карточки — цель
операции, а не её имя; откат относится к этой же операции»): для `py_write` и `clasp_deploy`
откат тогда стали собирать ПО ОПЕРАЦИИ (`_rollback`, `:3467-3486`), а `edit_claude` остался на
статичном литерале — класс закрыт не на всех видах.

Почему сторож молчал: `test_every_red_kind_carries_a_rollback_line`
(`test_pretool_guard.py:1432`) проверяет только префикс — `self.assertTrue(g._rollback(kind,
"x").startswith("Откат: "), kind)`, — то есть НАЛИЧИЕ строки, а не совпадение файла с объектом.
А во всех тестах `edit_claude` объектом стоит только отслеживаемый `.claude/settings.json`
(например `:2312`, `:2339`), локальный файл не встречается ни разу.

### Д-2. Вид `edit_claude` сработал на ИМЕНИ ФАЙЛА В ТЕКСТЕ записи. ПОДТВЕРЖДЁН

Операция была записью строки в журнал (`cowork_log_append.py`), а карточка объявила её правкой
конфига Claude Code. Причина — две, и обе в коде.

**(а) Ветка судит по подстроке скан-текста** (`pretool_guard.py:2516-2518`):

```python
    m = _RE_CLAUDE_CFG_CMD.search(scan)
    if m and "edit_claude" not in skip_kinds and not _is_pure_config_read(cmd):
        return ("ask", "edit_claude", m.group(0))
```

Проверки «это упоминание, а не обращение» здесь нет — в отличие от вида `env`, которому такую
проверку завели 01.08 (`f4c3cff`, `_env_reach` → `env_mention`/`env_probe`). Смягчить может
только `_is_pure_config_read`, а он fail-safe: нет признака ЧТЕНИЯ — красное; `cowork_log_append.py`
смотрелкой конфига не является, поэтому вернул `False`. Зелёная ветка безопасных скриптов
`_RE_SAFE_SCRIPTS` (`:1096`, в перечне есть `cowork_log_append`) стоит на **7 строк НИЖЕ** —
`:2523`, — и до неё разбор не доходит.

**(б) Тело heredoc не вырезалось из скан-текста из-за токена `-`.** `_scan_text` (`:2004`) зовёт
`_strip_heredoc`, а тот оставляет тело под сканом целиком, если заголовок «исполняет stdin»
(`:1941`): `if _RE_HEREDOC.search(line) and _line_runs_code(line): return cmd`. Решает
`_seg_runs_stdin_as_code` (`:1881`), и порядок его проверок таков:

```python
        if "-" in rest or "-c" in rest:
            return True                  # `python -` / инлайн-код: stdin и есть программа
        if "-m" in rest or any(t.lower().endswith(".py") for t in rest):
            return False                 # цель ИМЕНОВАНА → stdin ей ДАННЫЕ, а не код
```

Замер на живой форме вызова:

```
_seg_runs_stdin_as_code('venv/Scripts/python.exe cowork_log_append.py -')  → True
_seg_runs_stdin_as_code('venv/Scripts/python.exe cowork_log_append.py')    → False
```

То есть фикс 01.08 работает ровно до тех пор, пока в строке нет `-`. А `-` здесь — СЕНТИНЕЛ
самого писателя журнала (`cowork_log_append.py:377-383`: «Текст: argv, а `-` — СЕНТИНЕЛ stdin»),
и именно эту форму предписано брать для кириллицы. Проверка «`-` в аргументах» стоит ВЫШЕ
проверки «цель `.py` именована», хотя `cowork_log_append.py` — уже названная цель, и `-` — её
собственный аргумент, а не режим интерпретатора.

Итог: журнальная строка, лишь НАЗВАВШАЯ путь локального файла прав в своём тексте, дала красную
карточку. Задача 189 умерла в `needs_approval` (`pc_orchestrator.log:3483-3485`, METRICS
`dur_s=1155.67 outcome=needs_approval attempts=1`) на записи в журнал — вторая смерть того же
класса после задачи 87.

Контрольный случай, отделяющий подстроку от операции: в 18:49:32 (`pretool_guard.log:2638`)
такая же команда `cowork_log_append.py - <<'EOF' NOTE …`, где путь описан СЛОВАМИ, прошла как
`defer`. Разницу дала ровно строка текста, а не операция.

*(Этот заход по той же причине писал журнальную строку без литерального пути — иначе упёрся бы
в собственный предмет проверки. Путь оставлен только здесь, в артефакте.)*

## Итог (5 строк)

1. `81e524e` matcher НЕ трогал: заменена последняя строка `decide` — вместо `("defer","","")`
   любому чужому имени теперь `("ask","unknown_tool",<имя>)`, реестр `_TOOL_CHANNELS` 7+2,
   поддержан `_stays_red` + `_HARD_CARD`; сторож `TestChannelRegistry` (6 тестов) выводит список
   из живого matcher и из `ast` исходника. `f18c744` — только тело журнальной записи, 868 симв.
2. `defer` = **ПРОПУСК**: `main` (`:4045`) делает `sys.exit(0)` без вывода, ни `_emit_ask`, ни
   `_emit_deny` не зовутся, и решение переходит к glob-слою прав. Не вопрос и не блок.
3. Гард в проде (блоб файла = блоб HEAD `067b8c2`, хук — новый процесс на каждый вызов), а демон
   PID 18892 держит в памяти `pretool_guard` из `321dc3d`, где маркеров нового кода 0 из 5;
   self-update следит только за блобом `pc_orchestrator.py`. Живых решений `unknown_tool` — 0.
4. Д-1 ПОДТВЕРЖДЁН: объект карточки `.claude/settings.local.json`, откат — статичный литерал про
   `.claude/settings.json` «(файл под git)», тогда как объект под `.gitignore:8`; тест сторожит
   только префикс «Откат: ».
5. Д-2 ПОДТВЕРЖДЁН: `edit_claude` судит по подстроке скан-текста (без разделения
   упоминание/обращение, которое есть у `env`), а тело heredoc не вырезается, потому что
   `_seg_runs_stdin_as_code` проверяет `-` РАНЬШЕ именованной `.py`-цели: `True` против `False`
   на одной и той же команде с `-` и без него.
