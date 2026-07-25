# Влитие `rebuild-main` в `main` на VPS — разбор и рекомендация

**Дата:** 25.07.2026 · **Репо:** `/root/turbobaby-manager-bot` (VPS `splinter`) · **Режим:** read-only анализ, влитие НЕ выполнялось

---

## Исходное состояние (дословно)

```
  backup-24-07 4a17cf3 fix: test_guard_entity — красные литералы собираются из кусков в рантайме…
* main         6a6d7fc [origin/main: ahead 13, behind 1] tests: self-contained env for loop-break test
+ rebuild-main 26d2c6d (/root/rebuild-main-wt) [origin/main: ahead 12] test(models): ожидания моделей…
  wip-229-230  40ffd7a WIP 229/230 (untracked-тесты, снято с main)

  origin/main         7af280c гард: argv .py-скрипта — ДАННЫЕ, а не операция (_strip_script_cli_args)
  origin/rebuild-main 26d2c6d test(models): ожидания моделей приведены к боевому .env
```

- `merge-base(main, rebuild-main)` = **`9a8c47a`**
- уникальных коммитов: **main 13**, **rebuild-main 13**
- `main` предок ветки? **нет** (rc=1). Ветка предок main? **нет** (rc=1) → **деревья разошлись, fast-forward невозможен**
- `origin/main` (`7af280c`) **входит** в `rebuild-main`, но **отсутствует** в `main` (отсюда `behind 1`)

**Что это значит:** `rebuild-main` — не «ветка с фичей», а **пересборка** main: 13 коммитов main переиграны 13 коммитами ветки с теми же темами, и сверх того из версионного контроля вычищен мусор.

---

## Содержимое: `git diff --stat main rebuild-main`

```
575 files changed, 228 insertions(+), 257509 deletions(-)
```

Сводка `--name-status`: **568 `D`**, **1 `A`**, **6 `M`**.

### Появляется только в ветке (`A`)
```
tests/test_pretool_script_args_guard.py
```

### Изменяются (`M`, 6 файлов)
Из хвоста `--stat` видны пять:
```
tests/test_convert_loop_break.py     |  1 -
tests/test_executor_model.py         |  3 +-
tests/test_guard_cli_args.py         |  8 +-
tests/test_orchestrator_model.py     |  8 +-
tests/test_pretool_commit_msg.py     | 16 -
```
Шестой отсортирован раньше `registry_check.py.bak-reg113` и в обрезанный хвост не попал — почти
наверняка `.gitignore` (обе стороны несут свой `chore: ignore…`), но **дословно не подтверждён**.

⚠️ Строка `tests/test_convert_loop_break.py | 1 -` — это **ровно наш свежий фикс**
`os.environ.setdefault("ORCH_TEST_MODE", "1")`: переход main→ветка его снимает.

### Исчезает (`D`, 568 файлов) — что именно
Практически всё — мусор, попавший под версионный контроль:

| группа | примеры |
|---|---|
| бэкапы `*.bak-*` | `splinter.py.bak-*` (28 шт., по 4–5.9 тыс. строк), `orchestrator_daemon.py.bak-*` (23), `devbot.py.bak-*` (19), `CLAUDE.md.bak-*` (17), `pretool_guard.py.bak-*` (7), `gate.py.bak-*` (3), `.claude/settings*.bak*` (8) |
| одноразовые скрипты `_*.py` | `_cclog_*`, `_recon_*`, `_check*`, `_rev*`, `_caps*`, `_quote_*`, `_s2…_s7_*` — сотни файлов |
| каталоги-слепки | `_cctmp_pull/` (17), `_bridge_bak-quoteprice-20260703/` (16) |
| выгрузки | `_recon_crm.xlsx`, `_quote_crm_export.xlsx`, `_quote_calendar_full.csv`, `_delivery_6m.jsonl` |
| **живое состояние** | **`wa_queue.db` (Bin 20480 → 0)**, `spend_ledger.json.lock`, `verified_facts.lock`, `wallet_cache.json.lock`, `_scratch/.gitignore`, `_scratch/.gitkeep` |
| **содержательный документ** | **`docs/artifacts/2026-07-24-entity-port-todo.md`** |

Две последние строки — единственное, что нельзя выбрасывать не глядя.

---

## Коммиты main без эквивалента в ветке (`git cherry -v rebuild-main main`)

```
+ 88db1ae WIP: частичные изменения (git-m strip + na_reminders) — бэкап перед doctrinal list
+ ea1933e reviewer: submodule pointer → 3b0368c (ревизор черновиков, класс-фикс чеков 23.07)
+ a5c148e guard: fail-safe strip + trust by origin for repo-tracked files
+ f185099 test: разблокировать гейт — commit_msg под живую функцию … + entity-тест skip + артефакт порта
+ 651db0a chore: ignore runtime state and scratch files
+ 6a6d7fc tests: self-contained env for loop-break test          ← наш сегодняшний фикс
```

Остальные 7 из 13 помечены `-` — патч-эквивалент в ветке есть
(`f291c77→15a0095`, `4a17cf3→9ae6725`, `a72bb6d→06c1ab2`, `0bbca3d→53d2953`,
`56fbd69→a97ba75`, `1f6741b→2734b5c`, `1bc3051→f8ca4cd`).

Обратно (`git cherry -v main rebuild-main`) без эквивалента в main:
```
+ 7af280c гард: argv .py-скрипта — ДАННЫЕ, а не операция   (= origin/main)
+ 58207f2 guard: fail-safe strip + trust by origin for repo-tracked files   (своя версия темы a5c148e)
+ e116f34 chore: ignore runtime state and scratch files                     (своя версия темы 651db0a)
+ 48a83a8 test: порт из f185099 — entity-тест под skip                      (частичный порт f185099)
+ a718ffa test(guard cli args): ожидания приведены к фактическому поведению гарда
+ 26d2c6d test(models): ожидания моделей приведены к боевому .env
```

Три пары (`a5c148e`↔`58207f2`, `651db0a`↔`e116f34`, `f185099`↔`48a83a8`) — **одна тема, разное
содержимое**. При сбросе победит версия ветки; это осознанная замена, а не слепая потеря.

---

## Прочее состояние

```
stash@{0}: On main: guard-before-land
stash@{1}: On main: vps-local-24.07
stash@{2}: On main: local-wip-guard-doctrinal-20260723
stash@{3}: On main: WIP шагов 187/194 (OOM-обрыв): _POPEN-переход демона + guard-block + 4 теста …

worktrees: /root/turbobaby-manager-bot 6a6d7fc [main]
           /root/baseline-origin-wt    7af280c (detached HEAD)
           /root/rebuild-main-wt       26d2c6d [rebuild-main]   (дерево чисто, только ?? venv)
.env под версионным контролем: НЕТ
```

Четыре стэша сняты **с main** — после сброса их `pop` пойдёт на другую базу и может конфликтовать.

---

## Ответ прямо

**Безопасно ли переключить main на состояние ветки?** Голым `git reset --hard rebuild-main` — **нет**,
из-за трёх конкретных вещей:

1. **Пропадёт коммит `6a6d7fc`** — фикс флак-теста, сделанный сегодня (`test_convert_loop_break.py | 1 -`).
2. **Исчезнет `wa_queue.db`** (бинарь 20480 байт) — единственный удаляемый файл, похожий на живые данные,
   при активном `wa-webhook.service`. Нужно проверить, читает ли сервис его из каталога репо.
3. **Исчезнет `docs/artifacts/2026-07-24-entity-port-todo.md`** — единственный содержательный документ
   среди 568 удаляемых.

Всё остальное из 568 — мусор, и его вычистка и есть смысл ветки.

**Слияние или сброс? — СБРОС.** Причины:

- Ветка **уже содержит `origin/main` (`7af280c`)**, а main его не содержит. Сегодня `main` в принципе
  **не отгружается** (`ahead 13, behind 1` = расхождение с origin/main). После сброса на ветку push
  станет обычным fast-forward `7af280c → 26d2c6d`, без `--force`.
- `merge` сохранит **обе** истории, включая 7 патч-идентичных пар коммитов (каждая чистка в логе дважды),
  и почти наверняка даст конфликты modify/delete там, где main трогал файлы, удалённые веткой.
- Ветка — «пересборка main» по смыслу и названию; сброс приводит main ровно к тому состоянию, ради
  которого её собирали.

---

## Подготовка ветки — ВЫПОЛНЕНО 25.07 (сброс не делался)

Три коммита в `rebuild-main` (ветка ушла вперёд `origin/rebuild-main` на 3, не отгружалась):

```
7f75b98 chore: игнорировать боевую базу wa_queue.db (живёт на диске, в git не хранится)
8303f07 docs: вернуть артефакт entity-port-todo из main (потерян при пересборке ветки)
18bdd14 tests: self-contained env for loop-break test          (cherry-pick 6a6d7fc, rc=0)
26d2c6d test(models): ожидания моделей приведены к боевому .env
```

`git check-ignore -v wa_queue.db` → `.gitignore:55:wa_queue.db`.
Снимок живой базы на всякий случай: `/root/_gatefix_backup_20260725/wa_queue.db.snapshot-20260725`
(20480 байт, mtime `Jul 23 13:16` — база не переписывалась с 23.07).

**Гейт на ветке (дословно):**
```
✅ ГЕЙТ (полный): 130 тестов зелёные (46.7с) — прод-операция «check» разрешена.
```
`exit=0`, красных строк нет.

### Оценка остальных четырёх коммитов main — по факту

| коммит | вердикт | доказательство |
|---|---|---|
| `88db1ae` WIP-бэкап | **не нужен** | его `--stat` — это и есть добавление `.claude/settings.json.bak`, `CLAUDE.md.bak-*` и прочего мусора, ровно того, что ветка вычистила |
| `ea1933e` submodule pointer → 3b0368c | **не нужен, спорный вопрос снят** | `git ls-tree main -- _pcport185` и то же по ветке — **обе пусты**, `.gitmodules` в main нет: указатель добавлен `ea1933e` и снят `651db0a`, в итоге его нет ни там, ни там |
| `a5c148e` guard: fail-safe strip | **не нужен** | `git diff --quiet main rebuild-main -- pretool_guard.py` → **идентичен**; ветка несёт то же содержимое через `58207f2` |
| `651db0a` chore: ignore runtime state | **не нужен** | `git diff main rebuild-main -- .gitignore` до правки — **пусто**, файлы идентичны; ветка несёт то же через `e116f34` |
| `f185099` | **частично НУЖЕН** | артефакт перенесён (`8303f07`); entity-тест портирован веткой (`48a83a8`); **но секция (5) `tests/test_pretool_commit_msg.py` есть в main и отсутствует в ветке** |

Недостающая в ветке секция — регресс известного класса false-red #281
(`python gate.py && git commit -m "fixed set_fleet_oil"` → должен быть `defer`, не `red`),
5 компаунд-кейсов. Это единственная содержательная потеря покрытия при сбросе.

### Проверка факта после правок (дословно)

```
$ git diff --diff-filter=D --name-only main rebuild-main | grep -E 'wa_queue|docs/artifacts'
_cclog_wa_queue.py
_check_wa_queue.py
_check_wa_queue2.py
_check_wa_queue_ro.py
_log_wa_queue.py
wa_queue.db

$ git diff --diff-filter=D --name-only main rebuild-main | wc -l   -> 567
$ git diff --stat main rebuild-main | tail -1
 574 files changed, 230 insertions(+), 257458 deletions(-)
   1 A  /  567 D  /  6 M
```

**Артефакты из списка удаляемых ушли ✓. `wa_queue.db` — НЕТ ✗.**

Причина названа фактом: `git -C /root/turbobaby-manager-bot ls-files --error-unmatch wa_queue.db`
→ `wa_queue.db`. Файл **ещё отслеживается в main**, а `git reset --hard` сносит из рабочего дерева
всё, что есть в текущем индексе и отсутствует в целевом дереве — `.gitignore` целевой ветки на это
не влияет (игнор действует только на НЕотслеживаемые файлы). Правка на стороне ветки этот случай
закрыть не может в принципе.

**Лечение — одна команда в main НЕПОСРЕДСТВЕННО перед сбросом:**
```
git -C /root/turbobaby-manager-bot rm --cached wa_queue.db
```
файл на диске остаётся, становится неотслеживаемым, `reset --hard` его не трогает,
а новый `.gitignore` ветки не даст ему вернуться.

### Новое, ранее не видное: расходится боевой код

Полный список `M` (раньше был обрезан выводом):
```
.gitignore                          (наш новый wa_queue.db)
orchestrator_daemon.py              ← БОЕВОЙ КОД ДЕМОНА
tests/test_executor_model.py
tests/test_guard_cli_args.py
tests/test_orchestrator_model.py
tests/test_pretool_commit_msg.py
```

`orchestrator_daemon.py` — это то, что запускает `orchestrator-daemon.service`
(`ExecStart=… /root/turbobaby-manager-bot/orchestrator_daemon.py`). Сброс подменит боевой файл
версией ветки. **Дифф не смотрели** — это обязательный шаг перед сбросом.

---

## Попытка сброса 25.07 23:03 — ВЫПОЛНЕН И ОТКАЧЕН АВТОМАТИЧЕСКИ

### Что прошло

- Секция (5) `tests/test_pretool_commit_msg.py` перенесена в ветку (`f20e642`, 1 ханк — значит она
  и была единственным отличием файла, перенос точечный). Гейт на ветке:
  `✅ ГЕЙТ (полный): 130 тестов зелёные (47.8с) — прод-операция «check» разрешена.` `exit=0`.
- Страховка: ветка `backup-main-2507` = `6a6d7fc`; копия базы
  `/root/_wa_backup_20260725/wa_queue.db`, sha256 `e548d59e…96c0` — совпала.
- **Отступление от плана, оправданное фактом:** вместо «удалить сбросом и вернуть из копии» база
  снята с индекса (`git rm --cached wa_queue.db`) — потому что скан `/proc` показал
  `PID 1281 comm=python3 держит wa_queue.db открытым` (это `wa-webhook.service`). Удаление файла
  оставило бы вебхук писать в осиротевший inode. Файл на диске не исчезал ни на секунду,
  sha256 до и после сброса идентичны.
- Сброс: `HEAD is now at f20e642`, `rc=0`. `git status --porcelain` — пусто. Фикс петли и артефакт
  на месте. Локи и `_scratch` воссозданы.

### Почему откатили

```
❌ ГЕЙТ: КРАСНЫЕ ТЕСТЫ (1/130): test_guard_cli_args.py
   ДЕПЛОЙ «check» ЗАБЛОКИРОВАН (полный).
```
`exit=1` → сработал автооткат: `git reset --hard backup-main-2507` → `HEAD is now at 6a6d7fc`, `rc=0`.

**Причина — та самая, найденная 25.07 04:41 и выведенная тогда из объёма:**
`tests/test_guard_cli_args.py` хардкодит `ROOT = "/root/turbobaby-manager-bot"`. Тест из ветки
проверяет не своё дерево, а **абсолютный путь главного чекаута**. Пока по этому пути лежало
содержимое main, версия теста из ветки была зелёной. Сброс подменил содержимое того же пути —
и тест покраснел. Вердикт гейта зависел от содержимого чужого дерева; это и выстрелило.

### Второй блокер — из обязательного просмотра боевого кода

`git diff main rebuild-main -- orchestrator_daemon.py` → **3 insertions, 44 deletions**.
В ветке ОТСУТСТВУЕТ то, что есть в main:

- константы `NA_LIFETIME` (24ч hard-cap) и `NA_REMINDER_SEC` (3ч), множество `_na_reminded`;
- **вся функция `process_na_reminders()`** — напоминание владельцу о `needs_approval` старше 3ч
  и авто-`failed` по истечении 24ч (класс 23.07.2026), и её вызов в `cycle()`;
- абзац `APPROVAL_PREAMBLE`: «ПОРЯДОК: cc_log DONE пишется ТОЛЬКО ПОСЛЕ успешного завершения
  операции (exit 0 финальной команды, включая git push). НЕ пиши DONE до git push…»;
- `na_lifetime=%ss` в стартовом баннере демона.

Это содержимое живёт в main-коммите `88db1ae` («WIP … git-m strip + **na_reminders**»), который
раньше был помечен «не нужен» по одному лишь `--stat` (в первых 10 строках были только `.bak`).
Оценка была неполной: коммит несёт и мусор, и боевую функцию. Сброс молча снял бы её с демона.

### Состояние после отката (проверено)

```
main = 6a6d7fc, git status --porcelain пуст
rebuild-main = f20e642 (ahead origin/rebuild-main = 4, не пушено)
backup-main-2507 = 6a6d7fc
splinter PID 1275, orchestrator-daemon PID 41990, wa-webhook PID 1281 — все active, NRestarts=0
wa_queue.db sha256 e548d59e…96c0 — не изменился
```

⚠️ **Побочное следствие отката:** `git reset --hard` при возврате переписал `wa_queue.db`
(содержимое байт-в-байт, но mtime `Jul 24 23:04` вместо `Jul 23 13:16`, то есть файл был пересоздан).
`wa-webhook` (PID 1281) держал его открытым — его дескриптор теперь указывает на старый inode.
Данные не потеряны, но **вебхук нужно перезапустить** (или убедиться, что он открывает БД
на каждый запрос). Не делалось — служба живая, решение за владельцем.

### Что требуется до следующей попытки

1. Починить `tests/test_guard_cli_args.py` в ветке: `ROOT` — от `__file__`
   (`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`), временные `.py` шага-2 —
   в `tempfile.mkdtemp()`, а не в дерево репо. Пока корень захардкожен, гейт меряет чужое дерево.
2. Перенести в ветку из `88db1ae` **только** боевую часть `orchestrator_daemon.py`
   (`process_na_reminders` + константы + вызов в `cycle()` + абзац преамбулы + баннер), без `.bak`-мусора.
3. Перепроверить: гейт зелёный И в ветке, И в главном дереве **после** сброса.
4. Отдельно: перезапуск `wa-webhook` после любой операции, переписывающей `wa_queue.db`.

---

## Попытка №2 (25.07 23:14) — правка не применена, сброса не было

Патч остановился на собственной проверке `«хардкод пути остался в файле»`. Причина: хардкодов
в `tests/test_guard_cli_args.py` **два**, а не один:

```
15:sys.path.insert(0, "/root/turbobaby-manager-bot")
27:ROOT = "/root/turbobaby-manager-bot"
```

Строка 15 важнее строки 27: из-за неё тест в ветке импортировал `pretool_guard` **из главного
чекаута**, а не из своего дерева. То есть версия гарда, которую проверял «зелёный гейт ветки»,
физически бралась из main. Это же объясняет, почему `pretool_guard.py` «идентичен» не спасал:
тест вообще не смотрел на файл своего worktree.

Остальные части патча отработали как задумано (проверено выводом): `dir=ROOT` найден ровно 1 раз
(строка 169), в блоке `TestScriptBodyStep2` заменено 2 вхождения `ROOT → _TMP_ROOT` (строки 169 и 174),
`import os`/`import tempfile` на месте. Файл **не записывался** (проверка стоит до записи) и был
дополнительно восстановлен `git checkout --`; ветка и main не изменились.

**Корректная правка (готова, не применена):**
```python
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)     # было: sys.path.insert(0, "/root/turbobaby-manager-bot")
...
ROOT = _REPO                  # было: ROOT = "/root/turbobaby-manager-bot"
_TMP = tempfile.TemporaryDirectory(prefix="guard_cli_args_")
_TMP_ROOT = _TMP.name         # временные .py шага-2 — сюда, а не в дерево репо
```

### ⚠️ Подтверждено фактом: дескриптор вебхука осиротел

```
--- deskriptor vebhuka PID 1281 na wa_queue.db:
lrwx------ 1 root root 64 Jul 24 12:03 3 -> /root/turbobaby-manager-bot/wa_queue.db (deleted)
```

Метка `(deleted)` — не гипотеза, а факт: откат 23:04 пересоздал файл, и дескриптор `wa-webhook`
остался на **удалённом inode**. Лечится рестартом `wa-webhook.service`.

#### Спасательная операция 25.07 23:24 — потерь НЕТ

Копия снята через живой дескриптор (`cp /proc/1281/fd/3`) в `/root/_wa_rescue_20260725/`.
Ключевой факт — `stat -L /proc/1281/fd/3`:

```
Inode: 294164      Links: 0
Access: 2026-07-24 15:33:35     Modify: 2026-07-23 13:16:14
Change: 2026-07-24 23:04:46     Birth:  2026-07-23 13:16:14
```

`Modify` = `Birth` = 23.07 13:16 → **в призрак не писали ни разу с момента его создания**;
`Change` 23:04:46 — это лишь отвязка ссылки (`Links: 0`) при откате, не запись.
sha256 призрака и файла на диске совпадают: `e548d59e…96c0`.

Содержимое обеих баз (открыты read-only через `sqlite3`):

```
объекты: index idx_wa_status, index idx_wa_wamid, table sqlite_sequence, table wa_inbox
записей в wa_inbox: 0
колонки: id, ts_queued, channel, from_number, name, msg_type, text, media_id,
         ts_msg, echo, history, status, raw, wamid
ts_queued: min=None max=None      записей в sqlite_sequence: 0
```

`sqlite_sequence` пуст → счётчик AUTOINCREMENT ни разу не поднимался, то есть **вставок в
`wa_inbox` не было никогда**. Очередь пуста, спасать было нечего.

**Поэтому прежняя формулировка «входящие WhatsApp теряются прямо сейчас» — преувеличение.**
Дефект (осиротевший дескриптор) реален, но ущерб нулевой: трафика в очередь не было.

База возвращена на штатное место (`inode 294407`, sha256 прежний), `git status --porcelain` main —
пуст, дерево чисто. Сервисы не перезапускались: `wa-webhook` 1281, `splinter` 1275,
`orchestrator-daemon` 41990, `NRestarts=0` у всех.

Код (`wa_webhook.py`): `db = WAQueueDB(env["queue_db"])` создаётся один раз на старте (стр. 523),
подключение — `sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)` (стр. 113).
Переиспользуется ли это соединение в `enqueue()` или открывается заново — по одному grep не видно.
Рестарт `wa-webhook` всё равно желателен (снимет стухший дескриптор), но не срочен: терять нечего.

Ещё факт preflight: `git ls-files --error-unmatch wa_queue.db` → `rc=0`, то есть **снятие базы
с индекса откатилось вместе с `reset --hard`**. Перед каждым новым сбросом `git rm --cached`
нужно повторять.

---

## Снимок состояния 25.07 23:32 (сброса по-прежнему НЕТ)

```
main         = 6a6d7fc     rebuild-main = f20e642
git rev-list --left-right --count main...rebuild-main  ->  13  17
git status --porcelain -> пусто
```

**Гейт на main — красный, и ровно по предсказанной причине:**
```
❌ ГЕЙТ: КРАСНЫЕ ТЕСТЫ (2/129): test_executor_model.py, test_orchestrator_model.py
   ДЕПЛОЙ «check» ЗАБЛОКИРОВАН (полный).
```
Это следствие отката двух тестов моделей 25.07: их приведение к боевому `.env`
(`claude-opus-5` / `fable`) живёт только в ветке (`26d2c6d`). Пока ветка не влита,
**main не отгружается** — ни push, ни любая гейтованная прод-операция.

**Мусор на месте** (сброса не было):

| | main | rebuild-main |
|---|---|---|
| файлов под git | **762** | **196** |
| `*.bak*` | 143 | 6 |
| одноразовые `_*` | 435 | 7 |

На диске в корне: 129 `.bak`, 407 `_*`. При сбросе удалилось бы **567** файлов.
(В ветке остаточно ещё 6 `.bak` и 7 `_*` — чистка не стопроцентная.)

## Вебхук возвращён на живой файл — 25.07 23:32

`systemctl restart wa-webhook.service`, `rc=0`. Было PID 1281 → стало **58567**,
`active (running)`, `NRestarts=0`, старт `23:32:58`. Старый процесс мёртв.

```
fd 3 -> /root/turbobaby-manager-bot/wa_queue.db   inode=294407
inode файла на диске: 294407        (призрачный был 294164)
```
Метки `(deleted)` больше нет, inode совпал. `wa_inbox` = 0 записей, `sqlite_sequence` = 0,
sha256 базы прежний, `git status` чист. Дескриптор открывается **на старте процесса** —
значит рестарт действительно был единственным способом переоткрыть файл.

### Почему за 40+ часов не пришло ни одного сообщения

- журнал `wa-webhook` за 42 часа — **одна строка**: `Jul 24 12:03:02 Started …`;
  приложение не оставило в journald ни одной собственной записи (ни ошибок enqueue, ни баннера);
- `journalctl -u caddy` за тот же период — только TLS/ACME-обслуживание `wa.turbophuket.com`,
  ни одной строки о входящем запросе (оговорка: access-лог у Caddy по умолчанию выключен,
  так что это не строгое доказательство);
- сокет живой: `LISTEN 0.0.0.0:8765 users:(("python3",pid=1281,fd=4))`;
- ключи в `.env` присутствуют: `WA_VERIFY_TOKEN`, `WA_APP_SECRET`, `WA_WEBHOOK_PORT`,
  `WA_360_SANDBOX_KEY`, `WA_D360_PATH_SECRET`;
- решающее: `sqlite_sequence` пуст → в `wa_inbox` **никогда** не было вставок.

Вывод: сторона приёма исправна (процесс жив, порт слушается, TLS выдан) — **провайдер просто
ничего не присылает**. Проверять надо в консоли Meta/360dialog: оформлена ли подписка на webhook,
верифицирован ли callback URL, привязан ли номер. Не проверено также, не пишет ли приложение
свой лог в файл вместо stdout — это следующий дешёвый шаг.

### Поправка 25.07 00:23 — приложение пишет в ФАЙЛ, journald тут не судья

Юнит перенаправляет вывод:
```
StandardOutput=append:/root/turbobaby-manager-bot/wa_webhook.log
StandardError=append:/root/turbobaby-manager-bot/wa_webhook.log
```
Файл существует: `7899 байт, mtime Jul 25 00:14` — то есть **писался сегодня**, через 42 минуты
после рестарта. Значит формулировка «приложение не оставило ни одной записи» неверна: она была
верна только про journald. **Сам лог я вывел в списке, но не прочитал** — решающие строки
(handshake, отказы подписи, D360 POST) остались непросмотренными.

### Что проверено сквозняком и работает

| проверка | факт |
|---|---|
| DNS | `wa.turbophuket.com` → `5.223.94.179` = внешний IP машины |
| Caddy | слушает `:80`/`:443`, конфиг целиком: `wa.turbophuket.com { reverse_proxy 127.0.0.1:8765 }` |
| upstream | `caddy_reverse_proxy_upstreams_healthy{upstream="127.0.0.1:8765"} 1` |
| приложение | `LISTEN 0.0.0.0:8765 users:(("python3",pid=58567,fd=4))` |
| TLS | `CN=wa.turbophuket.com`, `notAfter=Oct 13 14:04:13 2026 GMT` |
| firewall | `ufw: inactive`, `iptables -S INPUT` → `-P INPUT ACCEPT`, nftables пуст |
| внешняя проба | `curl https://wa.turbophuket.com/` → **HTTP 404**, `ip=5.223.94.179`, `tls=0`, тело `Not found` |

Тело `Not found` отдаёт сам питон-обработчик — значит цепочка **интернет → Caddy → TLS →
reverse_proxy → приложение** проходит целиком.

### Мои пробы били не туда

Маршруты из шапки `wa_webhook.py`:
```
GET  /wa-webhook                              — Meta hub.verify-token handshake
POST /wa-webhook                              — события, проверка X-Hub-Signature-256
POST /wa-webhook/d360/<WA_D360_PATH_SECRET>   — 360dialog v1, авторизация секретом в пути
```
Я пробовал `/`, `/webhook`, `/wa` — все три честно дали 404, но **`/wa-webhook` не пробовался**.
Так что 404 в пробах ничего не говорят о боевом эндпоинте.

### Что доказательством НЕ является

- тишина в journald — вывод уходит в файл;
- тишина в журнале Caddy — в Caddyfile нет директивы `log`, access-лог выключен, файлов
  в `/var/log/caddy/` нет;
- `caddy_http_requests_total` — метрика **не экспортируется** (per-server metrics не включены),
  до и после проб её в `/metrics` нет.

### Схема очереди — прежний вывод подтверждён

```
CREATE TABLE wa_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, ts_queued …, wamid TEXT)
CREATE UNIQUE INDEX idx_wa_wamid ON wa_inbox(wamid)
AUTOINCREMENT у wa_inbox? True     COUNT(*) = 0     sqlite_sequence = []
```
`AUTOINCREMENT` подтверждён → пустой `sqlite_sequence` действительно означает **ни одной вставки
за всё время**. Альтернативных файлов очереди нет, `Environment=` в юните пуст (никакого
`WA_QUEUE_DB`-переопределения).

**Незакрытое:** прочитать `/root/turbobaby-manager-bot/wa_webhook.log` (7899 байт) и пробить
`GET /wa-webhook` — только это отделяет «не шлют» от «шлют, но мы отбиваем на подписи».

---

## Попытка №3 (25.07 00:29) — сброс сделан, гейт красный, автооткат. Диагноз уточнён

### Предсказатель дал ЗЕЛЁНЫЙ, а гейт — КРАСНЫЙ

Перед сбросом прогнал гард-тест ветки дважды:

```
(A) корень = /root/turbobaby-manager-bot (мусорное дерево)   Ran 19 tests   OK   exit=0
(B) корень = /root/rebuild-main-wt       (очищенное дерево)  Ran 19 tests   OK   exit=0
```

Оба зелёные → пошёл на сброс. После сброса гейт:

```
❌ ГЕЙТ: КРАСНЫЕ ТЕСТЫ (1/130): test_guard_cli_args.py
```

**Значит прежнее объяснение неполно.** «Вердикт зависит от содержимого дерева, на которое
указывает захардкоженный корень» — недостаточно: с корнем на очищенном дереве тест прошёл.
И правка хардкода сама по себе гейт бы не позеленила.

Чем мой предсказатель отличался от того, как тест зовёт `gate.py`:

| | предсказатель | gate.py (`:86`, `:93`) |
|---|---|---|
| файл | копия в `/tmp/wa_pred/` | `tests/test_guard_cli_args.py` в дереве репо |
| cwd | `/root/rebuild-main-wt` | `ROOT` |
| env | пусто | `PYTHONPATH=ROOT`, `PRETOOL_NOPUSH=1`, `ORCH_TEST_MODE=1` |

То есть я снова наступил на тот же класс, что записан в CLAUDE.md: **мок обязан копировать
живой формат прогона**. Предсказатель не воспроизвёл окружение раннера — и соврал.

**Решающий следующий шаг (один read-only прогон, сброс не нужен):** позвать тест ровно как гейт —
из каталога `tests/` очищенного дерева, с `cwd=<корень>` и `PYTHONPATH/PRETOOL_NOPUSH/ORCH_TEST_MODE`,
с `verbosity=2` — и получить имя падающей проверки. Пока оно неизвестно, чинить нечего.

### Операционно всё прошло чисто

- `backup-main-2507` = `6a6d7fc` (уже указывала на текущий main), копия базы обновлена.
- `git rm --cached wa_queue.db` → `rc=0`; сброс `HEAD is now at f20e642`, `rc=0`.
- **База пережила сброс нетронутой:** `inode 294407` до и после, sha256 `e548d59e…96c0` — окна
  отсутствия файла не возникло. Приём с `rm --cached` работает как задумано.
- Откат `git reset --hard backup-main-2507` → `HEAD is now at 6a6d7fc`, `rc=0`. Откат **пересоздал**
  базу (`inode 294407 → 294405`, содержимое то же), поэтому скрипт сразу перезапустил вебхук:
  PID `60626`, `fd -> …/wa_queue.db inode=294405` = inode файла на диске. **Осиротевшего
  дескриптора в этот раз нет.**
- `wa-webhook` перезапускался дважды (58567 → 59732 → 60626), оба раза штатно, `NRestarts=0`.
- `splinter` 1275 и `orchestrator-daemon` 41990 не тронуты.
- После отката `grep -c 'def process_na_reminders'` = **1** — функция снова на месте в дереве main.

Итоговое состояние: `main = 6a6d7fc` (чисто), `rebuild-main = f20e642`, ветки
`backup-main-2507`, `backup-24-07`, `wip-229-230` на месте.

---

## Попытка воспроизведения 25.07 00:39 — матрица не сработала, но найден главный след

### Как `gate.py` зовёт тесты (дословно, `:86`–`:94`)

```python
env = dict(os.environ, PYTHONPATH=ROOT, PRETOOL_NOPUSH="1", ORCH_TEST_MODE="1")
tests = sorted(glob.glob(os.path.join(TESTS_DIR, "test_*.py")))
r = subprocess.run([PY, t], cwd=ROOT, env=env,
                   capture_output=True, text=True, timeout=90)
```
`ROOT = os.path.dirname(os.path.abspath(__file__))`, `PY = ROOT/venv/bin/python3`,
`TESTS_DIR = GATE_TESTS_DIR or ROOT/tests`.

### Матрица из 11 прогонов не дала ничего — дефект драйвера

Все 11 прогонов вернули одно и то же:
```
Ran 0 tests in 0.000s
NO TESTS RAN            [exit=5]
```
Причина в моём драйвере: он исполнял пропатченный исходник через
`exec(compile(src, path, "exec"), {"__name__": "__main__"})`. `unittest.main()` ищет тест-классы
в `sys.modules["__main__"]`, а там оставался сам драйвер — классов нет, прогонять нечего.
Нужен `runpy.run_path(..., run_name="__main__")` (он временно подставляет настоящий модуль
в `sys.modules`) либо, что здесь проще, просто запустить пропатченную копию файлом.

Правильный дешёвый эксперимент на один заход: та же `sed`-копия в `/tmp`, но **с окружением
гейта** — `cd <дерево> && PYTHONPATH=<дерево> PRETOOL_NOPUSH=1 ORCH_TEST_MODE=1 venv/bin/python3
/tmp/…/test_guard_cli_args.py`. Именно эта комбинация ни разу не проверялась: прошлый
предсказатель гонял ту же копию, но с пустым env. Тест не использует `__file__`, поэтому
запуск из `/tmp` полностью честен.

### Главный след: версии теста расходятся ровно в одной проверке

`diff tests/test_guard_cli_args.py` (main против ветки), sha1 `dad648ba26df` против `5b9ee16328e9`,
181 против 183 строк — единственное отличие в `test_compound_gate_then_git_commit`:

```
main:   self.assertEqual(kind, "green",
            f"gate && git commit -m '<OP>' → green, получил {kind!r}")

ветка:  # Сегмент git commit зелёный (_strip_git_msg), но тело gate.py гард
        # дочитывает и метит неоднозначным → вся связка "ambiguous", не "green".
        # NB: только из корня репо; из другого cwd тела нет и выйдет "green".
        self.assertEqual(kind, "ambiguous",
            f"gate && git commit -m '<OP>' → ambiguous, получил {kind!r}")
```

Это коммит `a718ffa` («ожидания приведены к фактическому поведению гарда»). Ветка ждёт
`ambiguous` и **сама предупреждает, что результат зависит от cwd**. Это первый подозреваемый на
красноту после сброса — но **не доказано**: `gate.py` в обоих деревьях байт-в-байт одинаков
(его нет в списке `M`), так что механизм расхождения пока не объяснён.

---

## ПРИЧИНА КРАСНОТЫ НАЙДЕНА — 25.07 00:58

### Спорная проверка — дословно из обеих версий

Команда в обеих версиях **одна и та же**:
```python
cmd = "venv/bin/python3 gate.py && git commit -m 'фикс: " + _CB + "'"
kind, _, _ = PG.classify(cmd, ROOT)
```
Расходятся только ожидания:
```python
main   (:139):  self.assertEqual(kind, "green",     "… → green, получил {kind!r}")
ветка  (:141):  self.assertEqual(kind, "ambiguous", "… → ambiguous, получил {kind!r}")
```
`gate.py` и `pretool_guard.py` в обоих деревьях **байт-в-байт одинаковы** (`sha1 4a8a35e4e7`,
`cmp` → одинаковы), `gate.py` отслеживается git в обоих. То есть дело не в коде и не в мусоре.

### Четыре прогона — все зелёные, и это само по себе улика

| прогон | файл теста | cwd | ROOT | env гейта | итог |
|---|---|---|---|---|---|
| A | ветки, как есть | WT | main | да | `Ran 19 tests … OK` |
| B | ветки, корень → чистое дерево | WT | WT | да | `Ran 19 tests … OK` |
| C | **main, как есть** | **MAIN** | MAIN | да | `Ran 19 tests … OK` |
| D | ветки, корень → чистое | WT | WT | нет | `Ran 19 tests … OK` |

### Зонд: гард на одном и том же входе

Обёртка над `PG.classify` показала вердикт для каждого входа. Спорная команда:

```
 4. cmd: venv/bin/python3 gate.py && git commit -m 'фикс: create_booking'
    свой='ambiguous' hit='ambiguous' | второй='ambiguous' hit='ambiguous'
```
— из **cwd = `/root/rebuild-main-wt`** результат `ambiguous` при ЛЮБОМ корне (и main, и чистом).
Значит содержимое дерева-корня тут ни при чём: мусорное и очищенное дают одно и то же.

А прогон **C** прошёл, то есть из **cwd = `/root/turbobaby-manager-bot`** та же команда даёт
**`green`** — иначе ожидание main'а (`green`) провалилось бы.

### Отсюда причина, полностью объясняющая красноту

1. Из главного чекаута команда классифицируется как **`green`** (доказано: прогон C зелёный).
2. Версия ветки ждёт **`ambiguous`** для той же команды (доказано диффом).
3. После сброса тест ветки оказывается в главном чекауте: cwd = `ROOT` = `/root/turbobaby-manager-bot`
   → гард отдаёт `green`, ожидание `ambiguous` **проваливается** →
   `❌ ГЕЙТ: КРАСНЫЕ ТЕСТЫ (1/130): test_guard_cli_args.py`. Ровно то, что наблюдалось дважды.

**Чьё ожидание верное: main'а.** Ветка ошибается.

**Почему разошлись:** коммит `a718ffa` («ожидания приведены к фактическому поведению гарда»)
снимал «фактическое поведение» **внутри `/root/rebuild-main-wt`** — а это *связанный* worktree
(`.git` там файл-указатель, а не каталог). В нём проверка «доверять файлу как отслеживаемому в
репозитории» (`a5c148e`/`58207f2`, «trust by origin for repo-tracked files») не срабатывает, гард
дочитывает тело `gate.py` и метит `ambiguous`. В главном чекауте она срабатывает — и ответ `green`.
То есть ожидание ветки — артефакт калибровки в связанном worktree.

Механизм различия (linked worktree против главного) — обоснованная гипотеза, а не измерение:
прямо не проверялось, какая именно ветка кода гарда даёт расхождение. Причинная цепочка до
красноты при этом доказана данными полностью.

**Оговорка по строгости:** сочетание «файл теста ветки + cwd главного дерева» не прогонялось —
это один дешёвый прогон, который превратил бы вывод из логического в наблюдаемый.

### Побочная находка: шаг 2 тоже привязан к дереву

```
 8. cmd: venv/bin/python3 tmppzlnnklk.py
    свой='red' hit='set_fleet_oil' | второй='ambiguous' hit='ambiguous'  <<< РАСХОДЯТСЯ
```
Временный `.py` шага 2 существует физически только в том дереве, где создан, — поэтому
`TestScriptBodyStep2` тоже даёт разные вердикты по корням. Сейчас это не мешает (тест смотрит
только «свой» корень), но это вторая мина того же класса: её снимает перевод временных файлов
в `tempfile.mkdtemp()`.

### Что чинить перед следующим сбросом

Одна строка в ветке — вернуть ожидание main'а:
```python
self.assertEqual(kind, "green", f"gate && git commit -m '<OP>' → green, получил {kind!r}")
```
плюс убрать вводящий в заблуждение NB-комментарий (он утверждает обратное наблюдаемому).

---

# ✅ СБРОС ВЫПОЛНЕН — 25.07 01:12. main = rebuild-main, гейт зелёный

### Правка ожидания (коммит `a5598b0`)

Дифф между версиями теста был ровно 2 ханка и ровно одно ожидание `ambiguous` — проверено перед
правкой. `git checkout main -- tests/test_guard_cli_args.py` → файлы совпали побайтно.

```diff
     def test_compound_gate_then_git_commit(self):
-        # Сегмент git commit зелёный (_strip_git_msg), но тело gate.py гард
-        # дочитывает и метит неоднозначным → вся связка "ambiguous", не "green".
-        # NB: только из корня репо; из другого cwd тела нет и выйдет "green".
+        # gate.py зелёный + git commit с <OP> в сообщении → оба сегмента зелёные
         cmd = "venv/bin/python3 gate.py && git commit -m 'фикс: " + _CB + "'"
         kind, _, _ = PG.classify(cmd, ROOT)
-        self.assertEqual(kind, "ambiguous",
+        self.assertEqual(kind, "green",
```
`[rebuild-main a5598b0] tests: expect prod-cwd verdict for compound gate case — 1 file changed, 3 insertions(+), 5 deletions(-)`

### Гипотеза стала наблюдением — через инверсию

| контекст | ожидание теста | итог |
|---|---|---|
| было: тест ждал `ambiguous` | связанный worktree → зелено | главный чекаут → **красно** |
| стало: тест ждёт `green` | связанный worktree → **красно** | главный чекаут → **зелено** |

Гейт ветки после правки: `❌ ГЕЙТ: КРАСНЫЕ ТЕСТЫ (1/130): test_guard_cli_args.py` — и это
**ожидаемо**. Предсказатель (файл теста ветки, `cwd` главного дерева, окружение гейта):
`test_compound_gate_then_git_commit … ok`, `Ran 19 tests … OK`, `exit=0`. Вердикт переворачивается
ровно по контексту worktree — механизм подтверждён, а не выведен.

### Сброс

```
git rm --cached wa_queue.db            → rc=0
git reset --hard rebuild-main          → HEAD is now at a5598b0, rc=0
git status --porcelain                 → пусто
```
**База не тронута вообще:** `inode 294405` до и после, sha256 `e548d59e…96c0` совпал.
`wa-webhook` PID `60626`, `fd 3 -> /root/turbobaby-manager-bot/wa_queue.db inode=294405` —
метки `(deleted)` нет, **перезапуск не потребовался**.

### Гейт на main

```
✅ ГЕЙТ (полный): 130 тестов зелёные (47.2с) — прод-операция «check» разрешена.
```
`exit=0`. Откат не потребовался.

### Итог

| | было | стало |
|---|---|---|
| `main` | `6a6d7fc` | **`a5598b0`** (= `rebuild-main`) |
| файлов под git | 762 | **196** |
| гейт на main | `❌ КРАСНЫЕ (2/129)` | **`✅ 130 зелёных`** |

Службы: `splinter` 1275, `orchestrator-daemon` 41990, `wa-webhook` 60626 — все `active`,
`NRestarts=0`. Ветки: `backup-main-2507` = `6a6d7fc` (точка отката), `backup-24-07`, `wip-229-230`.

### Три хвоста

1. **`process_na_reminders` ушла из боевого дерева** (`grep -c` = 0). `splinter` и
   `orchestrator-daemon` **не перезапускались**, поэтому напоминания о `needs_approval` >3ч и
   hard-cap 24ч пока работают из памяти процесса — и **исчезнут при первом же рестарте демона**.
   Восстановить: перенести боевую часть `88db1ae` из `backup-main-2507` отдельным коммитом.
2. **Гейт в `/root/rebuild-main-wt` теперь постоянно красный** на этом тесте — свойство связанного
   worktree. Ветка влита, worktree можно удалить (`git worktree remove`), и вопрос снимется.
3. **`main` больше не расходится с `origin/main`** — измерено 25.07 01:34:
   `git rev-list --left-right --count origin/main...main` → `0  17` (у origin нет ничего, чего нет
   у main), `git merge-base --is-ancestor origin/main main` → **да**. Push будет обычным
   fast-forward `7af280c → a5598b0`, без `--force`. Хвост закрыт.

### Проверка 25.07 01:34 (read-only) — состояние держится

Владелец прислал ту же задачу повторно; повторный прогон **не делался** — шаг 4 исходного скрипта
(`git branch -f backup-main-2507` на текущий main) затёр бы точку отката `6a6d7fc`, единственную
именованную ссылку, где ещё лежит `process_na_reminders`. Вместо этого — подтверждение чтением:

```
main = a5598b0 = rebuild-main,  status --porcelain пуст
backup-main-2507 = 6a6d7fc  (цела)
tests/test_guard_cli_args.py:139  self.assertEqual(kind, "green", …)
✅ ГЕЙТ (полный): 130 тестов зелёные (47.2с) — прод-операция «check» разрешена.   exit=0
файлов под git 196 (остаточно 6 *.bak*, 7 _*)
process_na_reminders: в дереве 0, в backup-main-2507 — 1
splinter 1275 / orchestrator-daemon 41990 / wa-webhook 60626 — active, NRestarts=0
wa_queue.db 20480 б, inode 294405; fd вебхука → тот же inode, метки (deleted) нет
```

---

# 🚀 ОТГРУЖЕНО В GitHub — 25.07 01:44

```
✅ ГЕЙТ (полный): 130 тестов зелёные (47.2с) — прод-операция «push» разрешена.
To https://github.com/mxfill77/turbobaby-manager-bot.git
   7af280c..4ec1790  main -> main
[push rc=0]
```
Fast-forward, без `--force`. `git ls-remote origin` → `refs/heads/main = 4ec17903…`, совпадает с
локальным; `ahead/behind` = `0 0`. Хук `pre-push` прогнал гейт с `--final` — зелёный, отгрузка
разрешена самим контуром. Отгружено 19 коммитов.

### `process_na_reminders` — НУЖЕН, не остаток (и возвращён, `58bc1b5`)

Доказательство не по коду VPS (там ссылок не осталось — функция была удалена чисто), а по
соседнему контуру: **в `_pcport185/pc_orchestrator.py` живёт та же фича** —
`NA_LIFETIME`, `_NA_REMINDER_SEC`, `_na_reminded`, hard-cap 24ч → `failed` — **и у неё есть тесты**
(`_pcport185/test_pc_orchestrator.py:388, 400, 403, 415, 2485, 2781`). То есть это осознанный
элемент контура, продублированный на ПК, а не мусор.

Возврат сделан точечно: `git diff --numstat backup-main-2507 -- orchestrator_daemon.py` дал ровно
`3  44` (проверено ДО правки), значит вся разница файла — это и есть фича. `git checkout
backup-main-2507 -- orchestrator_daemon.py` → `ast OK` → `44 insertions(+), 3 deletions(-)`.

⚠️ **Находка:** в дереве лежит `tests/__pycache__/test_na_reminder.cpython-314.pyc`, а исходника
`tests/test_na_reminder.py` **нет**. Значит тест этой фичи существовал и потерян при пересборке —
функция вернулась без покрытия. Проверить `git show backup-main-2507:tests/test_na_reminder.py`.

### Остатки сняты с отслеживания (`4ec1790`)

Снято 8 файлов (на диске все на месте, проверено поштучно): 6 бэкапов `tests/*.bak*`
и 2 одноразовых — `_allow_new_settings.json`, `_claspsplit_build.py`.

**Оставлены 5 `_*`, потому что на них ссылается живой код:**
`_claspsplit_new_settings.json` и `_claspsplit_new_settings.local.json` (CLAUDE.md,
`_claspsplit_build.py`), `_envfix_probe.py` (`tests/test_settings_allowlist.py`),
`_failopen_new_settings.json` (`tests/test_settings_failopen.py`),
`_restarts_new_settings.json` (CLAUDE.md, `tests/test_guard_own_restarts.py`).

Уточнение по причине: правило `.gitignore` **не действует на уже отслеживаемые файлы** — это верно.
Но моя проверка `git check-ignore` была неинформативной: без `--no-index` она **пропускает
отслеживаемые пути**, поэтому вернула пусто для всех 13. Как следствие в `.gitignore` добавлены
8 явных путей, из которых 6 дублируют существующее правило `*.bak*` — безвредно, но избыточно.

⚠️ Мелкая асимметрия: `_claspsplit_build.py` снят с отслеживания, а порождаемые им
`_claspsplit_new_settings*.json` оставлены. На свежем клоне будут настройки без скрипта, который их
собирает. Файл на диске цел — при желании вернуть одной командой.

### Итог миграции

| | было (24.07) | стало |
|---|---|---|
| `main` | `6a6d7fc`, расходился с origin (`ahead 13, behind 1`) | **`4ec1790`, = `origin/main`** |
| файлов под git | 762 | **188** |
| `*.bak*` / `_*` под git | 143 / 435 | **0 / 5** |
| гейт на main | `❌ КРАСНЫЕ (2/129)` | **`✅ 130 зелёных`** |
| `process_na_reminders` | в дереве | **в дереве** (возвращена) |

Службы не перезапускались: `splinter` 1275, `orchestrator-daemon` 41990, `wa-webhook` 60626 —
`active`, `NRestarts=0`. **Теперь рестарт демона безопасен** — фича в дереве, тихой потери не будет.

---

## Механизм измерен 25.07 02:37 — решает РАБОЧИЙ КАТАЛОГ, а не дерево

Прежняя оговорка («linked worktree против главного — обоснованная гипотеза, а не измерение»)
закрыта. Матрица на той же команде `venv/bin/python3 gate.py && git commit -m 'фикс: <OP>'`:

```
cwd=turbobaby-manager-bot   ROOT=turbobaby-manager-bot   -> kind='green'
cwd=turbobaby-manager-bot   ROOT=rebuild-main-wt         -> kind='green'
cwd=rebuild-main-wt         ROOT=turbobaby-manager-bot   -> kind='ambiguous'   <-- падает
cwd=rebuild-main-wt         ROOT=rebuild-main-wt         -> kind='ambiguous'   <-- падает
```

**`ROOT` не влияет вообще** — обе колонки одинаковы. Флип даёт исключительно `cwd`:

```
cwd=turbobaby-manager-bot  'gate.py' → /root/turbobaby-manager-bot/gate.py   внутри ROOT(main)=True
cwd=rebuild-main-wt        'gate.py' → /root/rebuild-main-wt/gate.py         внутри ROOT(main)=False
```

Падение воспроизведено дословно (гейт ветки, `exit=1`):
```
FAIL: test_compound_gate_then_git_commit (__main__.TestGitMsgGreen.test_compound_gate_then_git_commit)
  File "/root/rebuild-main-wt/tests/test_guard_cli_args.py", line 139, in test_compound_gate_then_git_commit
    self.assertEqual(kind, "green", …)
AssertionError: 'ambiguous' != 'green'
 : gate && git commit -m '<OP>' → green, получил 'ambiguous'
```
Тот же файл из главного дерева — `Ran 19 tests … OK`, `exit=0`.

**Что именно ломает.** Относительный путь `gate.py` резолвится от `cwd`. Из главного чекаута
гард признаёт файл доверенным по происхождению и тело не читает → `green`. Из связанного
worktree — не признаёт, дочитывает тело и метит `ambiguous`. При этом:

- `gate.py`, `pretool_guard.py` и сам тест в обоих деревьях **байт-в-байт одинаковы** (`cmp`);
- `git ls-files --error-unmatch gate.py` успешен **из обоих** каталогов;
- единственное структурное отличие: в main `.git` — **каталог**, в worktree — **файл-указатель**.

Значит проверка доверия опирается не на `git ls-files`, а на признак, который в связанном
worktree не срабатывает. **Оговорка:** сам код проверки доверия в `pretool_guard.py` я не читал —
это единственный незакрытый шаг; поведение измерено, реализация выведена.

**Практический вывод:** тест по своей природе привязан к рабочему каталогу и не может быть
зелёным в обоих контекстах одновременно. Он зелёный там, где работает боевой гейт (главный
чекаут). Ветка влита, `rebuild-main` = `a5598b0` отстала от `main` = `4ec1790` на 2 коммита —
worktree `/root/rebuild-main-wt` больше не нужен, и его удаление (`git worktree remove`) снимает
вопрос совсем. Альтернатива — учить гард распознавать связанные worktree, но это правка боевого гарда.

---

## Рабочий каталог сборки удалён — 25.07 02:49 (ветка оставлена)

Предусловия проверены фактом до удаления: `merge-base --is-ancestor rebuild-main main` → `rc=0`,
`git log rebuild-main --not main` → **пусто**, в каталоге из неотслеживаемого только `venv`,
ссылок в юнитах systemd нет, процессов с рабочим каталогом внутри нет.

Различия деревьев (`git diff --name-status main rebuild-main`): **8 A + 2 M**. Восемь «только в
ветке» — это те самые бэкапы и одноразовые скрипты, снятые с отслеживания коммитом `4ec1790`;
**все восемь проверены поштучно и лежат на диске главного дерева**. Два `M` — `.gitignore` и
`orchestrator_daemon.py`, где main впереди (возврат `process_na_reminders`). То есть различия
целиком «main впереди ветки», содержимое ветки не теряется.

```
$ git -C /root/turbobaby-manager-bot worktree remove --force /root/rebuild-main-wt
[worktree remove rc=0]     каталог на диске: удалён (было 4.4M)
worktrees после: /root/turbobaby-manager-bot 4ec1790 [main]
                 /root/baseline-origin-wt    7af280c (detached HEAD)
ветка цела:      rebuild-main a5598b0 [behind 2]      достижима из main: ДА
```

Гейт после удаления: `✅ ГЕЙТ (полный): 130 тестов зелёные (46.4с) — прод-операция «check» разрешена.`

Остался ещё один отработавший каталог — `/root/baseline-origin-wt` (detached на `7af280c`,
это `origin/main` до миграции). Кандидат на такую же уборку, отдельным решением.

---

## Вопрос о рестарте демона закрыт — 25.07 02:58

Замер, который стоило сделать раньше: **боевой код за всю миграцию не изменился ни на байт.**

```
блоб orchestrator_daemon.py на коммите старта демона (6a6d7fc): d3510887ba2a
блоб orchestrator_daemon.py в HEAD (4ec1790):                   d3510887ba2a
-> СОВПАДАЮТ: код демона в памяти РАВЕН коду в дереве

git diff --name-status 6a6d7fc HEAD -- '*.py' ':(exclude)tests/' ':(exclude)_*'
    (пусто = боевой код не менялся)
```

Возврат `process_na_reminders` (`58bc1b5`) вернул файл ровно к тому блобу, с которым демон
стартовал 24.07 21:19. Значит **рестарт не нужен вовсе** — не «безопасен», а не требуется:
обновлять нечего. Записей авто-обновления в журнале демона нет ни одной, и это ожидаемо.

Вся миграция (762 → 188 файлов под git) свелась к чистке версионного контроля и правкам тестов;
ни один боевой модуль не тронут. `git status --porcelain` главного дерева — **0 строк**,
`main` = `origin/main` (`0 0`).

### Молчание журнала демона разобрано 25.07 03:06 — демон работает молча

**Цикл жив, ничего не пропускается.** Замер через один интервал опроса (70 с при `POLL_SEC=60`):

```
состояние: S (sleeping)   wchan: hrtimer_nanosleep   потоков=1
за 70 с:  прочитано 186 948 байт · записано 6 832 байт · 375 системных чтений · 14 записей · 6 тиков CPU
живые соединения: ESTAB → [2404:6800:4003:c03::8a]:443  (fd 4)
                  ESTAB → [2404:6800:4003:c05::84]:443  (fd 5)
лог изменился: НЕТ
```

Два установленных HTTPS к Google — это опрос Bridge (Apps Script). Демон спит в
`time.sleep(1)` дробного сна (строки 3360–3363), просыпается, ходит в Bridge и ничего не находит.
`cycle()` логирует **только события**, поэтому тишина — штатная.

**Дескриптор лога цел** (проверено тем же приёмом, что вскрыл историю с базой вебхука):
`fd 3 -> orchestrator_daemon.log`, `inode=292895` = inode файла на диске, метки `(deleted)` нет,
позиция `1442831` = размеру файла. Лог под `.gitignore:27:*.log`, git его не отслеживает —
мои сбросы его не касались. Перенаправления вывода в юните нет; ротации в коде нет вовсе.

**Следы задач 391–399 — все в этом же файле** (строки 13181–13228). Последний след `399`:
`21:06:07 NEEDS_APPROVAL id=399 bridge_ok=True`, после — ничего.

**Ветка напоминаний:** `orchestrator_daemon.py:3290 def process_na_reminders()`, вызывается из
`cycle()` **строкой 3331** (4-й по счёту, до `process_approved`). Условия: `age > NA_REMINDER_SEC`
(10800 с = 3 ч) и `tid not in _na_reminded` → пуш в личку; `age > NA_LIFETIME` (86400 с = 24 ч) →
`failed`. Интервал — `POLL_SEC = 60`.

**Почему не сработало напоминание по 399 (6 ч в ожидании):** остаётся два прочтения, и по одному
только ssh-заходу их не различить —
1. задача уже НЕ в `needs_approval` (владелец закрыл её, либо devbot закрыл по «нет» — так и
   сказано в докстроке функции), тогда всё правильно;
2. `bc.get_pending("needs_approval")` возвращает `ok=False`.

⚠️ **И вот это — настоящая находка:** второй случай выходит **молча**. Строки 3295–3296:
```python
    r = bc.get_pending("needs_approval")
    if not r.get("ok"):
        return
```
Ни строки в лог. То есть отказ Bridge именно в этой ветке невидим, и напоминания просто
перестанут приходить без единого следа. Слепое пятно вне зависимости от текущей причины.

Различить одной командой (секретов не печатает, клиент берёт их сам):
```
venv/bin/python3 -c "import bridge_client as b; r=b.BridgeClient().get_pending('needs_approval'); print('ok=',r.get('ok'),'items=',len(r.get('items') or []))"
```

**Порядок (не выполнено):**

1. Перенести в `rebuild-main` то, чего в ней нет: `git cherry-pick 6a6d7fc` (фикс петли); принять решение
   по трём парам-двойникам (`a5c148e`/`58207f2`, `651db0a`/`e116f34`, `f185099`/`48a83a8`) — сравнить дословно.
2. Спасти из main: `docs/artifacts/2026-07-24-entity-port-todo.md`; решить судьбу `wa_queue.db`
   (скорее всего — файл оставить на диске, из индекса убрать, добавить в `.gitignore`).
3. Страховка сверх reflog: `git branch backup-main-20260725 main`.
4. `git -C /root/turbobaby-manager-bot reset --hard rebuild-main`, затем рестарт `splinter` и
   `orchestrator-daemon` (главное дерево — живой чекаут обоих).
5. Гейт после сброса: два теста моделей позеленеют (ожидания ветки = боевой `.env`); проверить, что
   перенесённый фикс петли на месте.
6. Стэши `stash@{0..3}` разбирать **после** сброса и по одному — база под ними сменится.
