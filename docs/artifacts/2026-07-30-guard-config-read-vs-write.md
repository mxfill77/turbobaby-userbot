# Гард: ЧТЕНИЕ конфига ≠ его ПРАВКА (четвёртая группа класса «класс по имени, а не по действию»)

Дата: 2026-07-30. Файлы: `pretool_guard.py`, `test_pretool_guard.py`.
Реплей: `tmp/replay_cfg_read.py`, разбор остатка: `tmp/why_cfg_cards.py`,
проба пятой группы: `tmp/probe_fifth_group.py`.

## 1. Что было сломано (живой факт, не гипотеза)

Задача 71 (30.07.2026) собрала ДВЕ карточки `edit_claude` «Хочу изменить конфиг Claude Code»
на командах, которые не пишут ни байта. Дословно из `pretool_guard.log`:

```
6368: 2026-07-30 22:25:52 | headless | Bash | ask | edit_claude | wc -l pc_orchestrator.py pretool_guard.py rc_supervisor.py rc_auth_detect.py task_metrics.py session_watch.py selfupdate_gate.py reviewer.py .claude/settings.json
6610: 2026-07-30 22:30:42 | headless | Bash | ask | edit_claude | sed -n '20,32p' .claude/settings.json
```

Счёт строк и печать диапазона. Класс присвоен по ИМЕНИ файла: перечень смотрелок в
`_RE_CFG_READ` был `cat|type|head|tail|more|less|grep|rg|findstr|ls|dir|git` — короче реальной
работы. Всё, чего в нём нет, признавалось правкой.

Это ЧЕТВЁРТАЯ группа одного класса. Три разведены по действию раньше:
наличие файла (`_env_probe_only`), окружение живого процесса (`_py_reads_process_env`),
база (`_sqlite_decide`: `select` ≠ `delete`).

## 2. Что сделано

**Правило прежнее и порядок прежний: есть признак ЗАПИСИ → красное; иначе нужен признак
ЧТЕНИЯ; нет ни одного — красное (fail-safe).** Изменились сами перечни.

1. **Смотрелки** (`_CFG_VIEW_CMDS` / `_CFG_VIEW_CMDLETS`): добавлены `wc`, `sed`, `awk`, `nl`,
   `jq`, `diff`, `cmp`, `stat`, `file`, `cut`, `tr`, `sort`, `uniq`, `column`, `od`, `xxd`,
   `md5sum/sha1sum/sha256sum`, `basename/dirname/realpath/readlink` и командлеты
   `Measure-Object`, `Select-Object`, `Sort-Object`, `Where-Object`, `ConvertFrom-Json`,
   `Format-List/Table`, `Out-String`, `Compare-Object`, `Get-FileHash`, `Resolve-Path`,
   `Split-Path`, `Get-Command`. Питон-чтение дополнено `read_text(`/`read_bytes(`.
2. **Писатели** (`_CFG_WRITE_CMDS` / `_CFG_WRITE_CMDLETS`): добавлены `copy`, `xcopy`,
   `robocopy`, `move`, `ren`, `rename`, `dd`, `ln`, `install`, `patch`, `sponge`, РЕДАКТОРЫ
   (`notepad`, `notepad++`, `wordpad`, `vim`, `vi`, `nano`, `emacs`), командлеты `Rename-Item`,
   `Clear-Content`, `Set-ItemProperty`, `Start-Process`; in-place-флаги смотрелок
   (`sed --in-place`, `sort -o`); питон-запись `write_text(`, `write_bytes(`, `shutil.copy*`,
   `shutil.move`, `os.replace/rename/remove/unlink`.
   Пара «смотрелка + её in-place-флаг» заведена намеренно: `sed` читает, `sed -i` пишет.
3. **`>` внутри кавычек — данные, а не перенаправление** (`_redirect_text`). Живой журнал:
   чтение `~/.claude.json` питоном краснело из-за строки `'=>'` в `print`; `awk 'NR>10'` — то же
   самое. Кавычки ВЛОЖЕННОГО ШЕЛЛА (`bash -c "…"`, `powershell -Command "…"`) не маскируются:
   там в кавычках лежит команда — проверено тестом.
4. **Числовая цель `>` исключена** (`awk 'NR>10 && NR<20'`). Доказуемо безопасно ДЛЯ ЭТОГО
   признака: путь конфига числом не бывает, а `echo x > 10` создаёт файл `10` и конфига не
   касается.
5. **Прозрачность лога**: чистое чтение конфига пишется видом `cfg_read`, а не безликим
   `unknown`/пустым. Та же доктрина, что у `sqlite_read` и `env_probe`. Решения это не меняет:
   `unknown` и так не красный (`_stays_red`).
6. Слово `code` в перечень редакторов НЕ положено: это обычное английское слово, оно красило
   `echo "=== fable in code ==="` — та же ошибка «по подстроке», которую чиним. Редактор, которого
   нет в перечне, остаётся красным сам собой: у него нет признака ЧТЕНИЯ.
7. Мёртвый `_RE_CFG_VIEW` (только определялся, нигде не звался) удалён.

## 3. ЗАМЕР — реплей живого журнала

Методика: строки `pretool_guard.log` превращаются обратно во вход хука и гоняются через
`decide_for_role()` → `card_or_journal()`, как в `main()`. Ничего не исполняется.
Оговорка честности: в журнале команда обрезана 300 символами — искажение ОДИНАКОВО для обеих
версий, поэтому дельта сопоставима, абсолютные числа могут отличаться от живых.

| срез | решений | карточек HEAD → НОВОЕ | группа `edit_claude` HEAD → НОВОЕ | прочие виды |
|---|---|---|---|---|
| **2026-07-30 (сутки)** | 2007 | **33 → 31** | **2 → 0** | дельта 0 у всех |
| 2026-07-29 | 1194 | 29 → 29 | 3 → 3 (всё — инструмент Write/Edit) | дельта 0 у всех |
| весь журнал | 7029 | 211 → 207 | 20 → 16 | дельта 0 у всех |

Проба из 28 команд (14 чтений / 14 правок) — расхождений с ожиданием **0**.

## 4. Доказательство, что ПРАВКА по-прежнему спрашивает

```
  команда: echo '{}' > .claude/settings.json
🔴 Хочу изменить конфиг Claude Code (.claude): .claude/settings.json — разрешить?
Объект: .claude/settings.json
Число: —
Откат: git checkout -- .claude/settings.json (файл под git)
Команда: echo '{}' > .claude/settings.json
```

Имя файла стоит в объекте карточки — как и требовалось. Спрашивают также: `>>`, `cp`, `copy`,
`mv`, `sed -i`, `sed --in-place`, `notepad`, `vim`, `tee`, `Set-Content`, `Rename-Item`,
`Clear-Content`, `Copy-Item` в `~/.claude/hooks`, `json.dump(… ,'w')`, `write_text(`,
`bash -c "cat x > .claude/settings.json"`, `powershell -Command "Set-Content …"`.
Инструмент Write/Edit по конфигу смягчения не получает вовсе — в реплее это 7 живых строк
журнала, оставшихся красными.

**Остаток 16 карточек `edit_claude` по всему журналу разобран по причинам** (`tmp/why_cfg_cards.py`):
7 — инструмент Write/Edit по конфигу (верно); 4 — `cat > …scratchpad/x.py <<'PYEOF'` (см. §5,
группа 7); 2 — настоящий `sed -i` в клоне сервера; 3 — артефакт реплея: обрезка лога на 300
символах оставляет НЕЗАКРЫТУЮ кавычку, `shlex` падает, маскировка кавычек не применяется.

## 5. ПЯТАЯ группа: где класс всё ещё по имени, а не по действию

Проба `tmp/probe_fifth_group.py` — ничего не исполняется, только вердикт. Найдены ТРИ места.
Ни одно здесь НЕ чинилось: у каждого свой класс и свой радиус, чинить их надо отдельной правкой.

### 5.1 `py_write` — по ИМЕНИ функции в тексте
```
КАРТОЧКА | ask py_write | печатаем ИМЯ функции, не зовём | python -c "print('create_booking')"
```
Точка касания: `pretool_guard.py`, `_scan_python` → `for tok in _RED_PY_TOKENS: if tok in blob`.
Признак — голая подстрока; действие тут ВЫЗОВ (`create_booking(`), а не упоминание. Цена: любой
докстринг, комментарий или `def create_booking` в читаемом теле стоит владельцу карточки, а
`_stays_red` держит `py_write` красным безусловно.

### 5.2 `live_sheet` — по СЛОВУ, и при этом ИНВЕРСИЯ
```
КАРТОЧКА | ask live_sheet | ищем слово gspread по репозиторию | python -c "print('gspread' in open('suggest.py').read())"
молча    | ask live_sheet | НАСТОЯЩЕЕ обращение к листу       | python -c "import gspread; gspread.open('Лист1')"
```
Поиск слова даёт карточку, а НАСТОЯЩЕЕ обращение к живому листу уходит в журнал МОЛЧА — это уже
не шум, а дыра. Точки касания: цикл `_RED_CMD` в `_decide_bash_body` заполняет `obj` только для
`delete/kill/network/sqlite`, для `live_sheet` объект остаётся пустым; `_card_fields` берёт
`_extract_host(cmd)`, которого в питон-форме нет; `live_sheet` не входит в `_HARD_CARD`, поэтому
правило «нет объекта → журнал» карточку проглатывает. Ветка `_scan_python`
(`_LIVE_SHEET_TOKENS`) объект заполняет — отсюда перекос.

### 5.3 HEREDOC — тело скрипта считается операцией
```
КАРТОЧКА | ask edit_claude | пишем ЧЕРНОВИК в scratchpad; путь конфига лежит в ТЕЛЕ скрипта
                           | cat > tmp/probe.py <<'PYEOF' ⏎ print(json.load(open(".claude/settings.json"))) ⏎ PYEOF
```
Перенаправление настоящее, но пишется ЧЕРНОВИК, а путь конфига — данные в теле heredoc. Карточка
называет неверный объект («хочу изменить `.claude/settings.json`»). В журнале это 4 строки одной
сессии. Точка касания: `_scan_text` вырезает аргументы .py-скриптов, текст `git -m` и поисковый
шаблон, но тела heredoc НЕ вырезает. Радиус большой: `_scan_text` питает ВСЕ красные ветви —
поэтому правка отдельная.

### Оставлено намеренно (граница задачи)
* **`.env` — класс `env` по упоминанию пути.** `wc -l .env` даёт карточку, хотя это счёт строк.
  Прямой запрет владельца «секреты спрашивают всегда» бьёт соображения шума; разведена только
  проверка НАЛИЧИЯ и чтение окружения процесса. Не трогали.
* **Мелочь прозрачности лога:** `test -f .env && echo yes` проходит молча, но пишется как
  `defer | unknown`, а не `env_probe` — метка `env_probe` ставится только когда `_decide_bash_body`
  вернул `defer` с пустым видом. Точка касания: `_decide_bash`, ветка `if probe and action ==
  "defer" and not kind` (для `cfg_read` рядом учтён и случай `ask/unknown`). На решение не влияет.

## 6. Гейт

`venv/Scripts/python.exe -m unittest test_pretool_guard test_pc_orchestrator` → **801 тест, OK**
(из них `test_pretool_guard` — 223, новых 9 в классе `TestConfigReadVsWrite`).
Голдены нового класса — ДОСЛОВНЫЕ строки живого провала задачи 71, как требует свод (тесты
детекта = реальные фразы, не идеализированные).
