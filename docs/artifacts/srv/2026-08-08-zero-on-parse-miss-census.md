# «НУЛЬ ПО НЕРАЗБОРУ» — перепись читателей живого текста на полосе VPS (08.08.2026)

Read-only разведка. Правок кода нет, коммита нет, пуша нет, гейт не гонялся, ничего не удалялось.
Рабочее дерево git по окончании чистое (`git status --short --untracked-files=no` — пусто).
Временных файлов заход не создавал вовсе; каталог, который был бы для них назван и оставлен на
месте, — `/tmp/tb_scratch/zeroparse_0808/` (создавать не пришлось: перепись собрана чтением кода и
живых файлов).

**КЛАСС.** Читатель живого текста, у которого шаблон не совпал, отдаёт наверх не ошибку, а честный
на вид нуль. Снаружи молчание источника и слепота читателя выглядят одинаково.

---

## 1. ЯКОРЬ — строкой в переписи, наравне с остальными

| поле | значение |
|---|---|
| место | `devbot.py:2442` `_revisor_line` (шаблон `_REV_NOTE_RE`, `devbot.py:2367`; источник подаёт `_cowork_text`, `devbot.py:2528`) |
| источник | `cowork_log` — журнал ПК-контура, Bridge `read_doc(name=cowork_log)` |
| отличает «источник пуст» от «строки есть, разобрано 0» | **ЧАСТИЧНО, и это главное свойство якоря.** Недоступность ИСТОЧНИКА отличает честно (`text is None` → «cowork_log недоступен — тик ревизора неизвестен»). Промах ШАБЛОНА — нет: пустой журнал и 28 непустых строк, ни одна из которых не совпала, дают одну и ту же фразу |
| что уходит наверх при неразборе | статус-строка `👁 надзор: ревизор: тиков ещё не было` (не исключение, не пометка, не нуль-как-нуль) |
| совпадает ли шаблон с сегодняшним живым текстом | **НЕТ.** Проба сделана заходом 08.08 04:31 живой функцией на живом тексте: строк со словом `ревизор:` — 28, совпадений — 0. Причина в самом шаблоне: между `NOTE` и двоеточием он допускает `[^:]{0,40}`, а живой формат — `NOTE 2026-08-04 19:31 UTC: Orchestrator: ревизор: …`, где двоеточия стоят в самом времени (артефакт `2026-08-08-expectations-pc-bridge-moderbot.md`, стр. 74–75). Тик молчал 80.9 ч, и это никого не разбудило |

**Почему класс выжил именно здесь — три проверенных факта.**

1. Читатель **единственный**: живого кода репозитория с `cowork_log` — только `devbot.py` и
   `scripts/cowork_log_append.py` (последний журнал ДОПИСЫВАЕТ, тик не разбирает; при сбое чтения
   он не пишет вслепую, а печатает запись в stderr со словами «Запись НЕ потеряна»).
2. У читателя **есть тест, и он зелёный**: `tests/test_status_summary.py:140–159`. Фикстура в нём —
   `"NOTE Orchestrator: ревизор: 2 окон с активностью…"`, то есть формат БЕЗ времени, который живой
   журнал не производит. В гейте 180 файлов тестов; ни один не сравнивает шаблон с прод-текстом.
3. Читатель **выглядит честным**: у него есть отдельная ветка «источник недоступен». Обходчик этой
   разведки (проход 1) так и записал его — в раздел «честные развилки, образцы для подражания».
   **Проход 1 ошибся ровно в ту сторону, в какую ошибается класс:** проверил различение ИСТОЧНИКА и
   не заметил отсутствия различения ШАБЛОНА.

---

## 2. Перепись, ярус A — места, которые САМИ берут живой текст из источника

Три поля у каждого: **[различает?]** · **[что наверх при неразборе]** · **[живая проба]**.
«Живая проба» — одна дешёвая read-only команда на реальном файле/выводе; дословный вывод всех
команд — в §7.

### Канал 1. `splinter.log` (39.8 МБ, 225 036 строк, без ротации)
| место | различает | при неразборе наверх | живая проба |
|---|---|---|---|
| `health.py:122` `_read_log_lines` | нет | `[]` при любой ошибке | ✅ файл читается |
| `health.py:167` `check_log_health` | **частично**: `if not lines` → `False, "лог пуст/недоступен"`; но счётчик ошибок при промахе `_parse_log_ts` даёт **`errors = 0` и `ok=True`** | «ошибок с старта: 0» — нуль по неразбору | ✅ живая строка `2026-08-08 07:37:07,218 [INFO] …` шаблону `(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})` соответствует |
| `health.py:148/162` `check_auditor` / `check_polling` | нет | `False` + текст «нет ✅ в старт-логе» — **направление в тревогу**, не в нуль | ✅ формат лога живой |
| `devbot.py:2006` `_g_errors` | нет, но **называет знаменатель** — «(строк N)» рядом с числом ошибок | «ERROR 0, WARNING 0 (строк N)» | ✅ `grep -c "\[ERROR\]"` = **4033** — шаблон совпадает |
| `expectations_run.py:95` `log_tail` | нет: нет файла → `""`, пустой файл → `""` | пустой текст | ✅ хвост читается |
| `expectations.py:203` `tick_facts` (ярус B над этим текстом) | нет | `{"tick": None, "log": None}` → `_o2_splinter` возвращает `[]` = молчание | ✅ **проба из прода**: `reports/2026-08-08/expect-o2d-…md` показывает `"tick": 1786164454.0` — разбор живого хвоста сегодня работает |

### Канал 2. `orchestrator_claims.jsonl` (159 строк)
| место | различает | при неразборе наверх | живая проба |
|---|---|---|---|
| `status_truth.py:235` `claim_started` | нет | `None` — «файла нет» = «id не найден» = «ts битый» | ✅ 159 строк, у всех есть `"id"` и `"updated"` |
| `devbot.py:300` `_drain_claim_events` | нет, но есть **резервный источник** (снимок `in_progress`) — смягчение | `[]` | ✅ там же |
| `expectations_run.py:85` `claims_ts` | нет | `None`; берёт mtime — шаблона нет вовсе, значит промахнуться нечем | ✅ файл существует |
| `orchestrator_daemon.py:298` `write_claim_event` | нет | ротация по размеру; `except OSError: pass` — содержимое строк не разбирается | ✅ |

### Канал 3. `cc_log_ledger.jsonl` (144 записи)
`status_truth.py:163` `ledger_entries` — **не различает**: нет файла / битая строка / чужое окно →
`[]`; текст карточки честно говорит «следов не найдено», а не «работы не было» — смягчение словом,
не значением. Живая проба: 144 строки с `"ts"`, в хвосте есть `"head"` — шаблон совпадает.

### Канал 4. `cowork_log` — §1 (ЯКОРЬ)

### Канал 5. Brain-доки через `read_doc`
`cclog.py` (4 обращения), `brain_sync.py` (3), `registry_check.py` (8), `devbot._g_cclog`/`_g_brain`
(6), `splinter.py` (5), `health.check_brain_latency` (2). Здесь класс **не доказан**: читатели
проверяют `r.get("ok")` до разбора (`devbot.py:2000`), а `brain_sync.check` сверяет **длину в code
points** git ⇄ drive — сравнение с измеренной величиной, а не с шаблоном.
`cclog._insert_under_vrezka` при промахе по ═-строке кладёт запись просто сверху — запись не
теряется, теряется место.

### Канал 6. Очередь через мост (`get_pending` / `get_pending_multi`)
Обращений: демон **31**, devbot **14**, `expectations_run` **1**.
| место | различает | при неразборе наверх |
|---|---|---|
| `orchestrator_daemon.py:2848` `_queue_items` | **ДА** — `None` (сбой) ≠ `[]` (пусто) | `None` |
| `orchestrator_daemon.py:3540` `_pc_fetch_items` | **ДА** — `None` ≠ `[]` | `None` |
| `devbot.py:2068` `_queue_snapshot` | **ДА** — `None`, вызывающий печатает «⚠️ очередь не опросилась» | `None` |
| `expectations_run.py:110` `queue_facts` | **ДА** — флаг `{"ok": …}`; `_rows()` при `ok=False` отдаёт `None`, и О1 молчит | `{"ok": False, "rows": []}` |
| `orchestrator_daemon.py:1766` `_dec_siblings` | **НЕТ, и это самое тяжёлое место канала** | `if not r.get("ok"): continue` → `[]`. Следствия проверены по коду: `_dec_step_blocked` → `False` (guard последовательности молча снят), `_dec_after_step` → «все шаги финальны» → досрочная сводка |
| `orchestrator_daemon.py:4354` `process_orphans` | нет | `if not r.get("ok"): return` молча; плюс `age is None` → сирота не подбирается никогда |

**Дальний конец того же канала — сам мост.** `BotData.js:324` `getPending_` при пустом листе
(`last < 2`) и при «ни одна строка не подошла под статус» отдаёт ОДИНАКОВОЕ `{ok:true, items:[]}`,
и **знаменателя не возвращает**, хотя соседние эндпоинты того же файла его возвращают
(`readWriteLog_:248`, `stateList_:1264` — поле `total`). Проверено: на полосе VPS поле `total`
не читает **никто** (`grep "get(\"total\")" *.py` — пусто).

### Канал 7. `/proc`
`prod_drift._boot` (btime), `prod_drift.started_at` (поле 22), `prod_drift.live`
(cgroup + cmdline), `orchestrator_daemon._mem_available_mb` (`MemAvailable:`),
`_live_claude_count`, `_live_claude_rss_mb` (`VmRSS:`).
Все — **не различают**: «файл нечитаем» и «строки нет» дают `None`/`0`.
Цена названа в самом коде: `orchestrator_daemon.py:754` `_mem_gate_check` — докстринг говорит
«False = памяти достаточно / **/proc нечитаем (fail-safe)**», то есть гейт памяти при слепоте
просто пропускается; то же у `_proc_gate_check`.
**Живая проба:** прямое чтение `/proc` из этой сессии ЗАПРЕЩЕНО песочницей рабочих каталогов
(дословный отказ — §7, команды 8–9), поэтому догадку не ставлю. Пробой служит артефакт
наблюдателя, снятый в проде сегодня: `reports/2026-08-08/expect-o2d-…md` несёт
`"proc": {"pid": 728503, "started": …}` для демона и `{"pid": 715906, …}` для splinter — значит
`prod_drift.live` и `started_at` на живом `/proc` сегодня разбираются верно.

### Канал 8. systemd (вывод команды)
`health.check_service` — разбор `systemctl show` в `k=v`, дата из `ExecMainStartTimestamp` по
`split()` + `strptime`. Не различает: команда не отработала → `props` пуст → «uptime ?, рестартов ?»,
ровно то же, что при смене формата. Направление частично в тревогу: `ok = (state == "active")`.
**Живая проба:** `SubState=running / NRestarts=0 / ExecMainStartTimestamp=Fri 2026-08-07 05:26:13 UTC`
— формат шаблону соответствует.
`orchestrator_daemon._restart_probe` — блоки `systemctl show run-*.service`, игла по подстроке
`"restart orchestrator-daemon"`; при любом сбое `(False, False, None)`, то есть «чужого рестарта
нет», неотличимо от «systemctl не ответил».
`orchestrator_daemon._exec_restart_splinter` — `is-active` сравнивается со строкой `active`; пустой
stdout и `inactive` дают одинаковый failed (направление в тревогу — верное).

### Канал 9. Вывод `git`
| место | различает | при неразборе |
|---|---|---|
| `prod_drift.commits_since` | нет — `_git` при сбое отдаёт `None`, `if not out: return []`, и это же `[]` значит «коммитов не было» | `[]` → свидетель W2 не подтверждён → молчим |
| `status_truth.commits_in_window` | нет | `[]` — «улик нет» |
| `health.check_commit` | **ДА** — `bool(out)` → «git недоступен» отдельной фразой | текст |
| `gate._changed_py_files` | **ДА по последствию** — `[]` переключает на ПОЛНЫЙ сьют, а не на тишину | `[]` → fail-safe |
| `invariants_check._git_tracked_top_level` | **ДА, образцово** — `None` (git недоступен) ≠ `set()`; вызывающий печатает «git недоступен — отслеживаемость не проверена» | `None` + note |
**Живая проба:** `git rev-parse origin/main` → `352f4e5…` (ref существует), `git log -2 --name-only
--format=…` отдаёт живую форму «строка-заголовок + строки файлов» — разбор `commits_since` ей
соответствует.

### Канал 10. Файлы состояния в `/tmp` (8 каналов)
`cc_expect_pulse`, `cc_expect_seen`, `cc_feed_seen`, `cc_guard_block`, `cc_repeat_ask`,
`cc_pretool_dedup`/`cc_pretool_guard`, `cc_drift_seen`, `cc_revizor_frozen.json`.
Читатели: `expectations_run.read_pulse`/`load_state`, `orchestrator_daemon._guard_marker_read`/
`_guard_markers_sweep`/`_drift_seen`, `devbot._revizor_load`, `repeat_ask:191`, `posttool_feed:473`,
`pretool_guard`.
**Ни один не различает** «файла нет» / «пустой» / «битый JSON» — везде `None`/`{}`/`False`/`0`.
Направления выбраны осознанно и записаны: `_drift_seen` → `False` = «не говорили» (заметка
повторится), `_guard_is_hard` → `False` = мягкий путь, `_drift_mark` при битом файле **молча теряет
старые ключи**.
**Живые пробы (дословно в §7):** `pulse.json` = `{"ts": 1786174312.28…, "n": 871, "pid": 728503,
"started": 1786094661.74…}` ✅; `cc_expect_seen/state.json` = `{"waits": {}, "open": {}, "tasks": []}` ✅;
`cc_repeat_ask/400.json` = `{"pending": {…}, "ok": {}, "born": …, "ts": …}` ✅;
`cc_feed_seen/385.json` = `{"n": 1, "seen": [...], "capped": false}` ✅; `cc_guard_block` — пуст.

### Канал 11. Транскрипты сессий (`/root/.claude/projects/-root-turbobaby-manager-bot`, 143 файла)
`guard_replay.iter_commands` — построчный `json.loads`, битая строка пропускается молча → счётчик
команд занижается неотличимо от правды. `notify_hook._last_tool_use` — при нечитаемом транскрипте
честный фоллбэк на текст события. **Живая проба:** каталог существует, `.jsonl` свежие.

### Канал 12. stdout `claude -p` (продукт исполнителя)
`_run_task_impl` (json-конверт → `except: pass` → сырой stdout), `_detect_na` (**различает**: три
исхода `marker`/`quote`/`phrase`), `_na_declaration` (**различает**: `None` ≠ `""`), `_parse_steps`
(**не различает**: «планировщик молчал» и «вернул текст без нумерации» → `[]`; смягчено тем, что
вызывающий вклеивает весь `out` в текст failed), `_parse_thinker_json` / `_parse_adapt_json` /
`_parse_curator_json` (**не различают**: мусор, пустой ответ, чужой вердикт → `None` → fail-safe),
`task_metrics.extract_tokens` (**различает образцово** — см. §5).
Отдельно назван факт по коду: `orchestrator_daemon.py:1660` — при `rc = 0` и ПУСТОМ stdout задача
закрывается **`done`** с текстом «(claude -p вернул пустой вывод)»: зелёный статус на нулевом
продукте.

### Канал 13. json-состояние в репозитории
`_vf_load` (реестр фактов) — `{}` и при «файла нет», и при битом json; следующий `_vf_save`
**молча затирает битый реестр**. `spend_ledger._load`, `wallet_cache._load` — та же форма.
**Живая проба:** `enqueue_dedup.json`, `wallet_cache.json`, `spend_ledger.json` читаются, форма
соответствует коду.

### Канал 14. Ответ инструмента (`PostToolUse`)
`posttool_feed:682` `json.load(sys.stdin)` + распознавание формы ответа (словарь = исполнилось,
строка `Error: Exit code N` = отказ). Форма ИЗМЕРЕНА по транскриптам — редкий случай, когда
контракт снят с прода, а не выдуман.

### Канал 15. Исходники репозитория как текст (ast)
`prod_drift._imports` — `SyntaxError` → `[]` → замыкание меньше → наблюдение уже, молча.
`invariants_check` (5 проверок чистоты), `repeat_ask.body_signature`.

### Канал 16. Живые рабочие таблицы через мост
`splinter._o3_overdue_scan:4594`, `splinter._hb_bookings_today:4314`, `devbot._g_overdue:2029`,
`invariants_check` (6 проверок: `FLEET_OIL_GEAR`, `CRM_OVERDUE`, `CRM_NO_BOOKING_ID`, `CRM_DEPOSIT`,
`FLEET_CLICK_125`, `OIL_VS_CURRENT_ODO`, `BOT_DATA_VS_SHEET`, `QUEUE_LONG_IP`).

**Самое дорогое место переписи после якоря — здесь, и оно проверено дословно:**
`_o3_overdue_scan` при падении `fleet()` возвращает `{"overdue": []}` (строка 4613, `log.exception`
в журнал есть), а `_g_overdue` на пустом списке печатает владельцу **«🔧 Просрочек ТО нет 👍»**.
То есть упавший мост и здоровый парк из 38 байков выглядят для владельца одинаково.
`_hb_bookings_today` — то же: `[]` при сбое чтения `clients`, и фильтр держится на живом формате
(`status == "бронь"` в нижнем регистре, `date_start` по первым 10 символам).

---

## 3. Перепись, ярус B — места, которым живой текст ПРИНОСЯТ

У них нет своего источника, но именно они отдают наверх число/список/статус, поэтому сверить
шаблон с сегодняшним текстом они не могут ФИЗИЧЕСКИ — только вернуть знаменатель вызывающему.

| место | различает | при неразборе наверх |
|---|---|---|
| `report_digest.gist_line` / `fact_line` / `trunc_line` | нет | `""` — «строки FACT в отчёте не было» и «FACT записан в неузнанной позиции» неразличимы |
| `revizor_route.route` | **ДА по последствию** — находок нет → `card` = тело БАЙТ-В-БАЙТ, владелец видит всё | `{"frozen": [], "card": text, "changed": False}` |
| `revizor_route._tally` | **ДА** — класс не разобрался → честное `"?"`, а не пропуск | `"?"` |
| `curator_ops.operations` / `curator_claim` (через `_curator_human_place:3327`) | нет | `[]` → развода нет, пункт едет одной карточкой (fail-safe в сторону владельца) |
| `curator_event` (через `_curator_event_seen:2914`) | нет | `None` → задача ставится КАК ПРЕЖДЕ; правило умеет только НЕ ставить |
| `card_duty.verdict` | **ДА, образцово** — «неизвестная форма / сбой правила / пустые факты → HOLD»; закрытие требует ПОЛОЖИТЕЛЬНОГО доказательства | `HOLD` |
| `_curator_human_items:3026` | нет | `[]` — «тело пустое», «формат старый», «карточка обрезана» одинаковы; частично закрыто фоллбэком на `task_text` в `4114` |
| `_parse_numbered:3650` / `_pc_current_plan:3674` | нет | `{}` / `({}, 0, None)` — «мост упал», «родителя нет», «нумерации нет» |
| `_age_sec:3514` | нет | `None`; **опасен не он, а потребители**: `4616` пишет `(_age_sec(...) or 0)` — неразобранное время становится «0 секунд», и напоминание по карточке и hard-cap не срабатывают НИКОГДА |

---

## 4. ЧИСЛА — два независимых прохода

**Единица счёта названа заранее, иначе числа несопоставимы:** «ветка» = одна точка разбора внутри
функции; «место» = функция-читатель целиком.

### Проход 1 — ПО МОДУЛЯМ (единица: ВЕТКА)
Три независимых обхода кода + мои чтения.

| файл | веток | из них «отличает пусто от неразбора» = ДА |
|---|---|---|
| `orchestrator_daemon.py` (4853 стр.) | **117** | **11** (`_delete_targets`, `_delete_is_red`, `_na_declaration`, `_detect_na`, пост-проверка маркера гарда, ветка kind в `_run_task_impl`, `_queue_items`, `_curator_used`, `_curator_human_split`, `_pc_fetch_items`, `_convert_curator_human_approved`) |
| `devbot.py` (2773 стр.) | **66** | обходчик назвал «честными развилками» 8 мест на три файла — и **ошибся минимум в одном: якорь `_revisor_line` записан в честные** |
| `splinter.py` (6961 стр.) | **55** | там же |
| `bot.py` (1495 стр.) | **27** | там же |
| **подытог: четыре больших файла** | **265** | **ДА ≈ 19 (7 %)** → слепых ≈ 246 (93 %) |
| **малые модули** (31 файл: `expectations*`, `prod_drift`, `health`, `status_truth`, `task_metrics`, `gate`, `invariants_check`, `registry_check`, `guard_replay`, `notify_hook`, `cclog`, `brain_sync`, `bridge_client`, `report_digest`, `revizor_route`, `curator_*`, `card_duty`, `repeat_ask`, `posttool_feed`, `pretool_guard`, `spend_ledger`, `wallet_cache`, `memory`, `auditor`, `wa_webhook`, `diag_status_truth`, `mem_probe`, `bridge_deploy`) | **204** | **82 (40 %)** → слепых **122 (60 %)** |
| **ВСЕГО по проходу 1** | **469** | **ДА ≈ 101 (22 %)** → **слепых ≈ 368 (78 %)** |

**Находка, которую видно только на полном числе: болезнь возрастная, а не общая.** В четырёх
больших долгоживущих файлах различают 7 % веток, в малых модулях — 40 %. Различают именно те,
что писались последними и под правило («различитель» у них заведён намеренно): `card_duty`,
`pretool_guard` (`amb` ≠ green, `_read_file` `None` ≠ `""`, `_call_args` `None` ≠ `{}`),
`bridge_deploy` (четыре различимые причины провала смока), `wa_webhook` (400 «JSON не разобрался»
против 200 «разобрался, событий 0»), `mem_probe` (рядом со счётчиком — `scanned_bytes`,
`regions`, `unreadable`), `diag_status_truth` (недоступность источника названа ДО чисел).

**Три места из этого обхода стоят рядом с якорем — они проверены мной дословно (§8):**

* `auditor.daily_report:340` — `items = res.get("items", []) if isinstance(res, dict) else []`.
  Исключение отделено честно («не смог прочитать журнал»), а вот **ответ моста не-dict либо
  `ok:false` даёт `items = []` → `flagged` пуст → зелёный отчёт «проверено действий 0, расхождений
  нет ✅»**. Надзиратель сам отдаёт честный на вид нуль.
* `bridge_deploy._node_harnesses_ok:43` — `glob.glob(TESTS_DIR/*_harness.js)`; **пустой glob (не тот
  каталог, переименованный харнесс) → `fails = []` → «✅ Node-харнессы зелёные» при НУЛЕ прогонов.**
  Соседний `_gate_ok` в том же файле судит по коду возврата и потому этой болезнью не болен.
* `pretool_guard.card_gate:1374` — объект не извлёкся → карточки владельцу НЕТ, только строка
  `card_skipped` в журнал гарда: «признак сработал на подстроке» и «настоящая операция, чей объект
  парсер не достал» одинаково молчат. Смягчено соседом `_entity_blocktype:1442`, где молчание
  парсера трактуется как отказ (`hard`), а не как разрешение.

Рядом — `spend_ledger` (`topup = 0` от потерянного файла неотличим от «не пополняли», и порог
предупреждения молчит навсегда), `wallet_cache.get_balance_with_fallback` («мост не ответил И кэша
нет» = «баланс правда пуст» → «Баланс: 0 ฿»), `memory.active_rules` (пустой список правил, на
котором `auditor.check_action` объявляет «галлюцинация: правила в memory.db нет»),
`diag_status_truth.cclog_entries` (строка чужого формата молча выбрасывается, счётчика
нераспознанных нет).

### Проход 2 — ПО КАНАЛАМ (единица: ФУНКЦИЯ-читатель)
Обход другой траекторией: от источника к читателям, не от файла к веткам.

| канал | функций-читателей |
|---|---|
| `splinter.log` | 3 (+2 потребителя яруса B) |
| `orchestrator_claims.jsonl` | 4 |
| `cc_log_ledger.jsonl` | 2 |
| `cowork_log` | **1** (якорь) |
| Brain-доки `read_doc` | 7 |
| очередь моста | 10 |
| `/proc` | 6 |
| systemd | 3 |
| `git` | 6 |
| `/tmp`-состояние | 9 |
| транскрипты | 2 |
| stdout `claude -p` | 8 |
| json-состояние репо | 4 |
| ответ инструмента | 2 |
| исходники (ast) | 3 |
| живые таблицы | 11 |
| **ИТОГО** | **≈81 функция по 16 каналам** |

**Оба числа названы, и они НЕ обязаны совпасть: 265 веток ≠ 81 функция — это разные единицы, и
совпадение было бы здесь подозрительным, а не убедительным.** Сопоставимо другое, и оно устойчиво
в обоих проходах:

* **слепых — большинство: 368 из 469 веток (78 %) в проходе 1**, и внутри этого числа видно
  расслоение — 93 % в четырёх старых больших файлах против 60 % в малых модулях; в проходе 2 из
  ≈81 функции честных различителей девять, они поимённо названы в §5;
* **каналов 16; хотя бы один слепой читатель доказан у 15 из 16.** Единственное исключение —
  Brain-доки: там читатели проверяют `ok` до разбора, а `brain_sync` сверяет длину в code points;
  слепого читателя я в этом канале не доказал (это «не доказан», а не «его нет»);
* **проверено живой пробой — 12 каналов из 16** (§2, дословные выводы в §7). Не проверено:
  `cowork_log` (проба взята из захода 08.08, повторять не требовалось), `/proc` (**песочница
  рабочих каталогов этой сессии запрещает читать `/proc` — дословный отказ в §7; догадку не
  ставлю, пробой служит артефакт наблюдателя из прода**), Brain-доки и живые таблицы (чтение
  требует запуска скрипта и обращения к мосту — заходом запрещено).

---

## 5. Где класс уже закрыт — образцы из ЭТОГО репозитория

Правило не надо изобретать: оно существует девятью отдельными реализациями, ни разу не названное
общим именем.

1. `invariants_check.check_fleet_oil_gear:220` — **три исхода, и каждый произносится вслух**:
   `bikes is None` → note «fleet() не вернул данные — проверка пропущена»; `not rented` → note
   «нулевая выборка (нормально вне сезона)»; иначе — флаги. Это и есть искомое правило целиком.
2. `invariants_check._git_tracked_top_level:608` — `None` ≠ `set()`, и вызывающий печатает
   «отслеживаемость не проверена».
3. `task_metrics._sum_fields:155` — возвращает **пару `(сумма, был ли хоть один ключ)`**;
   докстринг: «отличаем 0 токенов от нет данных»; наружу уходит `None`, а не `0`.
4. `expectations_run.queue_facts:110` — флаг `ok`; `expectations._rows` при `ok=False` отдаёт
   `None`, и ветка молчит осознанно.
5. `devbot._queue_snapshot:2068` — `None` при сбое, вызывающий печатает «⚠️ очередь не опросилась».
6. `gate.run_tests:110` — судит по **коду возврата**, а не по тексту вывода: смена формата
   unittest не может сделать гейт зелёным. `_changed_py_files` при пустоте переключает на ПОЛНЫЙ
   сьют, `_affected_test_files` нечитаемый тест ВКЛЮЧАЕТ.
7. `bridge_client._one_exchange:331` — любой промах разбора получает ИМЯ (`json_parse_error`,
   `unauthorized`, `request_failed`, `receipt_unknown`), а не пустой успех; `_receipt_leg` при
   неузнанной форме честно отдаёт `"?"`.
8. `card_duty.verdict:236` — «закрытие требует ПОЛОЖИТЕЛЬНОГО доказательства», неизвестная форма →
   HOLD.
9. `devbot._g_errors:2006` — **называет знаменатель**: «ERROR 4033, WARNING N (строк 225036)».
   Единственное место переписи, где нуль пришёл бы вместе с числом осмотренных строк.

---

## 6. Правило одной строкой (только названо, не применено)

> **Нуль без знаменателя не отдаётся: читатель живого текста возвращает наверх пару «сколько
> осмотрено — сколько разобрано», и случай «осмотрено > 0, разобрано 0» есть отдельный
> третий исход «шаблон разошёлся с источником», который произносится вслух, а не выдаётся за
> тишину источника.**

---

## 7. Остатки и честные пределы

* **Место якоря не чинилось** — по условию захода оно включено в перепись строкой, наравне с
  остальными.
* **`/proc` этой сессии недоступен** (песочница рабочих каталогов), поэтому три места
  `prod_drift` проверены не прямой пробой, а артефактом наблюдателя из прода.
* **Обход малых модулей завершился ПОСЛЕ первой записи артефакта** — его числа (204 ветки, 122
  слепых) внесены дополнением в §4, три головных находки перепроверены мной дословно (§8).
  Раскладка «22 % различают» получена сложением трёх обходов с разной строгостью классификации,
  поэтому она ±, а не точная; устойчиво в ней направление, а не второй знак.
* **Зеркало ПК-полосы не смотрели вовсе** — `pc_orchestrator` живёт в ПК-репозитории, на этой
  машине его нет; при этом производитель тика ревизора — именно ПК.
* **Живой факт, замеченный по ходу и не входящий в перепись:** `expectations.timer` в проде
  **активен** (сработал в 07:42:57, «5ms ago»), хотя канон CLAUDE.md от 07.08 говорит «в проде
  наблюдателя нет» — строка устарела. Тем же тиком открыт эпизод `o2_daemon`: «демон не даёт
  оборота 11 мин при пороге 10» — и произошло это потому, что демон занят исполнением ЭТОЙ задачи
  (одно-воркерная полоса, долгий заход). Соседний подкласс: «пульса нет» читается как «оборота
  нет», хотя оборот идёт и просто длинный.
* **Два живых источника без единого читателя:** `moderation_bot.log` (0 байт с 11.07) и
  `wa_webhook.log` (18 КБ, свежий) — `grep` по коду не нашёл ни одного места, которое их читает.
  Обратная сторона того же класса: не только читатель без источника, но и источник без читателя.

---

## 8. Приложение: дословный вывод каждой команды

```
$ ls -la /root/turbobaby-manager-bot
(полный листинг корня; существенное: CLAUDE.md 263737, splinter.log 39794926 Aug 8 07:34,
orchestrator_daemon.log 1938492, orchestrator_claims.jsonl 125746, cc_log_ledger.jsonl 54233,
moderation_bot.log 0 Jul 11, wa_webhook.log 18659 Aug 8 04:43, verified_facts.json 17710,
enqueue_dedup.json 156, spend_ledger.json 50, wallet_cache.json 143)

$ wc -l /root/turbobaby-manager-bot/*.py
    368 auditor.py / 1495 bot.py / 141 brain_sync.py / 1215 bridge_client.py / 228 bridge_deploy.py
    282 card_duty.py / 292 cclog.py / 866 claude_client.py / 26 conftest.py / 176 curator_claim.py
    172 curator_event.py / 170 curator_ops.py / 2773 devbot.py / 127 diag_status_truth.py
     94 _envfix_probe.py / 584 expectations.py / 392 expectations_run.py / 266 gate.py
    211 guard_replay.py / 524 health.py / 1620 invariants_check.py / 467 memory.py / 126 mem_probe.py
     16 notify_clear_hook.py / 80 notify_hook.py / 411 notify.py / 4853 orchestrator_daemon.py
    694 posttool_feed.py / 2604 pretool_guard.py / 427 prod_drift.py / 198 prompts.py
    498 registry_check.py / 281 repeat_ask.py / 107 report_digest.py / 211 revizor_route.py
    231 spend_ledger.py / 6961 splinter.py / 359 status_truth.py / 240 task_metrics.py
     95 wallet_cache.py / 562 wa_webhook.py / 31443 total

$ tail -c 700 /root/turbobaby-manager-bot/splinter.log
2026-08-08 07:37:04,315 [INFO] apscheduler.executors.default: Running job "devbot_report (trigger:
interval[0:00:45], next run at: 2026-08-08 07:37:49 UTC)" (scheduled at 2026-08-08 07:37:04.311034+00:00)
2026-08-08 07:37:07,218 [INFO] apscheduler.executors.default: Job "devbot_report …" executed successfully

$ wc -l /root/turbobaby-manager-bot/splinter.log
225036

$ grep -c "\[ERROR\]" /root/turbobaby-manager-bot/splinter.log
4033

$ grep -c "\"id\":" /root/turbobaby-manager-bot/orchestrator_claims.jsonl
159
$ grep -c "\"updated\":" /root/turbobaby-manager-bot/orchestrator_claims.jsonl
159

$ tail -c 900 /root/turbobaby-manager-bot/orchestrator_daemon.log
2026-08-08 06:21:40,989 INFO METRICS task=400 lane=vps type=read mode=prod src=orchestrator_daemon.py
model=claude-haiku-4-5-20251001,claude-opus-5 effort=xhigh start=2026-08-08T06:12:23+00:00
end=2026-08-08T06:21:40+00:00 dur_s=557.31 outcome=done attempts=1 selfheals=0
tokens_in=4416197 tokens_out=29032
2026-08-08 07:33:25,969 INFO NEW id=402 from=Filipp-328-dev text=ultrathink …
2026-08-08 07:33:28,542 INFO ИСПОЛНЕНИЕ id=402 через claude -p (timeout=2700s)

$ cat /proc/728503/cgroup
cat in '/proc/728503/cgroup' was blocked. For security, Claude Code may only concatenate files from
the allowed working directories for this session: '/root/turbobaby-manager-bot',
'/root/turbobaby-bridge-gs', '/root/.claude/projects/-root-turbobaby-manager-bot/memory'.

$ grep "^btime" /proc/stat
grep in '/proc/stat' was blocked. For security, Claude Code may only search for patterns in files
from the allowed working directories for this session: …

$ git -C /root/turbobaby-manager-bot rev-parse origin/main
352f4e50349853ca8d3ff35ff74d30d20b79863a

$ git -C /root/turbobaby-manager-bot log origin/main -2 --name-only --format="%h %ct %s"
352f4e5 1786163403 проект ожиданий по ПК, мосту и moderbot: предел канала назван, пороги сняты с живых пауз
docs/artifacts/2026-08-08-expectations-pc-bridge-moderbot.md
4e80276 1786137010 слой ожиданий: в проде наблюдателя нет — статус назван честно, команда установки в каноне
CLAUDE.md

$ tail -c 300 /root/turbobaby-manager-bot/cc_log_ledger.jsonl
head": "DONE 2026-08-08 06:20 UTC (local): KB_MASTER: блок «СОСТОЯНИЕ НА 08.08.2026 …»}
$ grep -c "\"ts\":" /root/turbobaby-manager-bot/cc_log_ledger.jsonl
144

$ ls -la /tmp/cc_expect_pulse /tmp/cc_expect_seen /tmp/cc_guard_block /tmp/cc_feed_seen /tmp/cc_repeat_ask
/tmp/cc_expect_pulse: pulse.json (82 б, Aug 8 07:31)
/tmp/cc_expect_seen:  state.json (38 б, Aug 8 07:32)
/tmp/cc_feed_seen:    15 файлов (282…385.json)
/tmp/cc_guard_block:  пуст
/tmp/cc_repeat_ask:   29 файлов (320…900005.json, auditdupA.json)

$ cat /tmp/cc_expect_pulse/pulse.json
{"ts": 1786174312.2816997, "n": 871, "pid": 728503, "started": 1786094661.7472813}

$ cat /tmp/cc_expect_seen/state.json
{"waits": {}, "open": {}, "tasks": []}

$ cat /tmp/cc_repeat_ask/400.json /tmp/cc_feed_seen/385.json
{"pending": {"e807a6105d827f3c": {"fp": "d2380fe07cb6238e", "sig": "cf3648c285bc3b68"}}, "ok": {},
 "born": 1786170008.4398274, "ts": 1786170008.5279152}{"n": 1, "seen": ["c56033791cc52251"], "capped": false}

$ cat enqueue_dedup.json wallet_cache.json spend_ledger.json
{"tg:-1003853365891:5591:b696f3690b45": {"id": 402, "ts": 1786174394.8358305},
 "tg:-1003853365891:5592:44c52f25257a": {"id": 403, "ts": 1786174398.2963817}}
{"Money Cashflow": {"THB": 1000.0}, "Самоорганизация": {"THB": 4.0}}
{"topup": 0.0, "spend": 9.875005, "warned": false}

$ systemctl show splinter --property=SubState,NRestarts,ExecMainStartTimestamp
SubState=running
NRestarts=0
ExecMainStartTimestamp=Fri 2026-08-07 05:26:13 UTC

$ systemctl is-active expectations.timer
active

$ systemctl list-timers expectations.timer --no-pager
NEXT LEFT LAST                         PASSED UNIT               ACTIVATES
-       - Sat 2026-08-08 07:42:57 UTC 5ms ago expectations.timer expectations.service

$ systemctl is-active orchestrator-daemon splinter wa-webhook
active
active
active

$ ls -la /root/turbobaby-manager-bot/reports/2026-08-08 /root/turbobaby-manager-bot/reports/revizor
2026-08-08: expect-o2d-1786094661-1786162396.md, expect-o2d-1786094661-1786163597.md,
            task-11.md, task-398.md, task-399.md, task-400.md, task-60.md
revizor:    2026-08-08-0655.md

$ (чтение) reports/2026-08-08/expect-o2d-1786094661-1786163597.md
# Ожидание нарушено: o2_daemon · возраст 14 мин (порог 10 мин) · can_task=False
🔔 демон не даёт оборота · VPS · последний оборот cycle() 14 мин назад …
"queue": {"ok": true, "rows": [{"id": 399, "status": "in_progress", "lane": "vps", …}]}
"daemon": {"pulse": {"ts": 1786163597.58, "n": 752, "pid": 728503, …},
           "proc": {"pid": 728503, "started": 1786094661.38}, "claims": 1786163674.97}
"splinter": {"tick": 1786164454.0, "log": 1786164454.0,
             "proc": {"pid": 715906, "started": 1786080373.66}}

$ grep -n -m 12 "" reports/2026-08-08/expect-o2d-1786094661-1786174312.md
# Ожидание нарушено: o2_daemon · обнаружено 2026-08-08 07:42:57 UTC
- возраст нарушения: 11 мин (порог 10 мин) · can_task=False
🔔 демон не даёт оборота · VPS · последний оборот cycle() 11 мин назад (порог 10 мин) ·
процесс жив (PID 728503), но продукта не даёт …

$ (чтение) reports/revizor/2026-08-08-0655.md
# Находки ревизора по замороженному контуру — 1 · окно: по 08.08 06:55 UTC (первая сводка)
- • [класс д] окно 323217300: [клиентский контур, нужна твоя отмашка; клиентские файлы: suggest.py] …

$ grep -rl "cowork_log" /root/turbobaby-manager-bot --include=*.py
живой код: devbot.py, scripts/cowork_log_append.py; остальное — tests/ и копии в _scratch_*

$ grep -n -A 14 "^def _dec_siblings" orchestrator_daemon.py
1775-        if not r.get("ok"):
1776-            continue

$ grep -n "_age_sec(task.get(\"updated\")) or 0" orchestrator_daemon.py
4616:        age = (_age_sec(task.get("updated")) or 0)

$ grep -n "claude -p вернул пустой вывод" orchestrator_daemon.py
1660:    return "done", _with_phrase_note(out or "(claude -p вернул пустой вывод)", _phrase_note)

$ grep -n -A 10 "^def _mem_gate_check" orchestrator_daemon.py
757-    False = памяти достаточно / /proc нечитаем (fail-safe) / MEM_MIN_MB=0 (выключен).

$ grep -n -A 16 "^def _g_overdue" devbot.py
2033-    ov = splinter._o3_overdue_scan(bridge).get("overdue") or []
2034-    if not ov:
2035-        return "🔧 Просрочек ТО нет 👍"

$ grep -n -A 22 "def _o3_overdue_scan" splinter.py
4610-        bikes = ((bridge.fleet().get("data") or {}).get("bikes")) or []
4611-    except Exception:
4612-        log.exception("  → O3 scan: fleet упал")
4613-        return {"overdue": []}

$ grep -n -A 12 "def _hb_bookings_today" splinter.py
4318-        rows = ((bridge._call("clients", filter="all").get("data") or {}).get("clients")) or []
4319-    except Exception:
4320-        log.exception("  → HB: чтение clients упало")
4321-        return []

$ grep -rn "get(\"total\")" /root/turbobaby-manager-bot/*.py
(пусто — знаменатель, который отдаёт мост, не читает никто)

$ grep -n -e "^function " /root/turbobaby-bridge-gs/BotData.js
… 232 readWriteLog_ (отдаёт total) · 324 getPending_ (total НЕ отдаёт) · 1252 stateList_ (отдаёт total) …

$ grep -n "@register(" invariants_check.py
16 зарегистрированных инвариантов (FLEET_OIL_GEAR, CRM_*, QUEUE_LONG_IP, OIL_VS_CURRENT_ODO,
BOT_DATA_VS_SHEET, SCRATCHPAD_WRITERS, SCRATCH_UNTRACKED, *_PURE, PROD_DRIFT_READONLY, EXPECTATIONS_PURE)

$ ls -1 /root/turbobaby-manager-bot/tests/test_*.py | wc -l
180

$ grep -rln "_revisor_line" tests
tests/test_status_summary.py    (фикстура: "NOTE Orchestrator: ревизор: 2 окон…" — БЕЗ времени)

$ grep -n "moderation_bot" *.py     → только словари процессов в pretool_guard/posttool_feed
$ grep -n "wa_webhook.log" *.py     → (пусто)

$ git -C /root/turbobaby-manager-bot status --short --untracked-files=no
(пусто — дерево не тронуто)

$ date -u "+%Y-%m-%d %H:%M:%S UTC"
2026-08-08 07:46:36 UTC

$ ls -la docs/artifacts/2026-08-08-zero-on-parse-miss-census.md
-rw-r--r-- 1 root root 44048 Aug  8 07:54 …/2026-08-08-zero-on-parse-miss-census.md

$ grep -n -A 12 "def daily_report" auditor.py
340-        try:
341-            res = self.bridge.audit_list(since=since_iso)
342-            items = res.get("items", []) if isinstance(res, dict) else []
343-        except Exception as e:
344-            return f"🐀 Аудит: не смог прочитать журнал ({e})"
346-        flagged = [it for it in items if str(it.get("verdict")) not in ("ok", "")]
347-        total = len(items)
348-        if not flagged:

$ grep -n -A 12 "def _node_harnesses_ok" bridge_deploy.py
45-    harnesses = sorted(glob.glob(os.path.join(TESTS_DIR, "*_harness.js")))
46-    fails = []
47-    for h in harnesses:
48-        r = subprocess.run(["node", h], capture_output=True, text=True, timeout=60, cwd=ROOT)
51-        if r.returncode != 0:
52-            fails.append(os.path.basename(h) + ": " + (r.stderr or r.stdout)[:120])
53-    return not fails, fails
```
