# Сверка ДОКУМЕНТЫ ↔ КОД (ПК-контур), 2026-07-30

Read-only аудит. Правок не делал и не предлагаю — по заданию. Каждая строка ниже
опирается на прочитанный `file:line` и дословный фрагмент кода; где проверить было
нечем, так и написано.

---

## §0. Затравка: почему это вообще делалось

`docs/dec_port_spec.md:89` (дословно): **«Полоса pc реапером НЕ трогается.»**

Живой факт того же дня (`pc_orchestrator.log`):

```
19:41:10  CLAIM id=61 in_progress
19:41:11  RUN id=61 (timeout=2700s, попытка 1/2)
19:52:28  singleton: устаревший лок (PID 18096 мёртв) — забираю      ← процесс-исполнитель умер
21:24:01  stuck-single: id=61 in_progress 6169с > 5400с → failed (ПК-ливнесс одиночки)
```

Задачу закрыл **свой, локальный реапер ПК** — `pc_orchestrator.py:2016
process_stuck_singles`, порог `pc_orchestrator.py:108 PC_SINGLE_STALE = 5400`,
фильтр полосы `:2035 [it for it in r.get("items", []) if _lane_ok(it)]`,
где `:476 return str(item.get("lane") or "") == LANE`, `:98 LANE = "pc"`.

Документ формально говорил про **VPS**-реапер (`process_orphans`, `ORPHAN_TTL`) — но
сформулирован абсолютом, без области действия, и лежит в репозитории ПК. Диагноз
строился на нём.

**Хронология, которая делает случай классом, а не опечаткой:**

| событие | коммит | время |
|---|---|---|
| в ПК-код добавлены `process_stuck_singles` + `PC_SINGLE_STALE` | `86d8b03` | 2026-07-11 21:43:11 |
| в репозиторий положен `docs/dec_port_spec.md` со словами «полоса pc реапером НЕ трогается» | `510aeac` | 2026-07-12 22:37:46 |

Документ был неверен **в момент коммита** — на 25 часов позже кода, который его
опровергает. Это не дрейф. Это рождение с ошибкой.

**И это не единственный такой случай.** `docs/task-2026-07-20-questions-filter.md:9-11`
утверждает: «Дословных строк «name of your apartment», «Hotel Name»… в репо ПК-контура
**НЕТ** (grep по всем файлам — ноль)» → и на этом строит главный вывод «менять тут нечего,
источник на VPS». Живой код: `suggest.py:2790` (`«Hotel Name: Cape Sienna…»`),
`suggest.py:2795` (`(?:hotel|apartment|accommodation|villa)\s+name\s*[:\-—]\s*\S`),
`suggest.py:2978` (`name\s+of\s+your\s+(?:apartment|hotel)`). Проверка:

```
git show --stat 965ba95   →  docs/task-2026-07-20-questions-filter.md | 121 ++++  (файл СОЗДАН)
                             suggest.py                               |  47 ++--
```

**Тот же коммит**, который положил документ «этих строк в репо нет», внёс эти строки в
`suggest.py`. Второй born-false документ.

---

## §1. Охват и счёт

Проверены: `docs/dec_port_spec.md`, `docs/guard-model.md`, `docs/ENV_PLAYBOOK.md`,
`docs/KB_PULSE.md`, `docs/RC_BRAIN_BRIDGE.md`, `docs/revizor_checklist.md`,
`docs/revizor_recon.md`, `docs/sales-method-2026-07-22.md`,
`docs/task-2026-07-20-questions-filter.md`, `docs/notes/greet-guard.md`,
`docs/notes/guard-quote-price.md`, `docs/task_notes/revizor-2026-07-13-zh.md`,
`CLAUDE.md`, `README.md` + **45 артефактов** `docs/artifacts/` (23.07–30.07).

Код-опора: `pc_orchestrator.py` (5958), `suggest.py` (7102), `pretool_guard.py` (2026),
`rc_supervisor.py`, `pc_agent.py`, `dispatch_notify.py`, `brain_writer.py`,
`cowork_log_append.py`, `moderation_ipc.py`, `pricing.py`, `delivery.py`,
`booking_draft.py`, `task_metrics.py`, `reviewer.py`, `.claude/settings.json`,
`pc_remote_control.task.xml`.

Итоговый счёт — в §9 (посчитан по строкам таблиц этого файла).

**Чего НЕ делал:** не читал `.env` и секреты (красная зона) — поэтому всё, что
зависит от `.env`, помечено отдельно; не запускал тесты и гейт; не дёргал
`schtasks /query` (красное по мандату этого прогона) — состояние живого Планировщика
взято только из трекаемого XML репозитория.

---

## §2. Расхождения по существу, ранжированные по вреду

Ранг = «на чём легче всего построить неверный диагноз, как сегодня».

### Ранг A — ложный диагноз о том, КТО ЗАКРЫЛ/УБИЛ задачу

| # | док:строка | что написано | что в коде | вердикт | какой ложный диагноз |
|---|---|---|---|---|---|
| A1 | `dec_port_spec.md:89` | «Полоса pc реапером НЕ трогается» | `pc_orchestrator.py:2016` `process_stuck_singles`, `:108` `PC_SINGLE_STALE=5400`, `:2048` `bc.complete_task(tid,"failed",msg)` | РАСХОЖДЕНИЕ | «pc не реапится ⇒ задачу убил человек/Bridge/сбой». Сегодняшний случай |
| A2 | `dec_port_spec.md:89` | порог сироты `ORPHAN_TTL=600с` | `grep ORPHAN_TTL *.py` → **0**; ПК-порог `pc_orchestrator.py:108` = **5400** | РАСХОЖДЕНИЕ | «10 минут молчания = смерть» — на ПК 90 минут; задачу объявят мёртвой в 9 раз раньше |
| A3 | `dec_port_spec.md:87` | «Heartbeat — независимый поток каждые `HEARTBEAT_SEC`, бьёт `updated`» | потока нет (`grep threading` в `pc_orchestrator.py` → 0); `bc.task_heartbeat` зовётся ТОЛЬКО в ветке уроков `:1733`, `:1770`; `_write_heartbeat()` `:2066` пишет файл живости ДЕМОНА, не поле задачи | РАСХОЖДЕНИЕ | «`updated` протух ⇒ прогон мёртв». На ПК `updated` заморожен с момента claim у ЛЮБОГО живого прогона |
| A4 | `pc_orchestrator.py:511` (сам код) | код провала называется `heartbeat_timeout`, человекочитаемо «таймаут сердцебиения» (`:517`) | сердцебиения задачи на ПК не существует (см. A3); реапер ставит этот код по возрасту `updated` (`:2044`) | ИМЯ ВРЁТ | в логе `FAIL причина=heartbeat_timeout` читают «процесс жил и перестал биться», хотя мерили простой возраст |
| A5 | `dec_port_spec.md:90` | «`needs_approval`… ждёт Филиппа **без ограничения**» | `pc_orchestrator.py:1970` `process_approval_timeouts`, `:1980` `if (_age_sec(...) or 0) > APPROVAL_TTL:` → `failed` (`APPROVAL_TTL=1800`, `:101`). Исключение ровно одно: `:1978 _is_revizor_owner_card` | РАСХОЖДЕНИЕ | «красный шаг стоит с ночи, ждёт кнопки» — он failed через 30 минут, цепь давно остановлена |
| A6 | `dec_port_spec.md:90` | надзор ПК-цепи = `PC_STEP_TIMEOUT=3600с` | `grep PC_STEP_TIMEOUT *.py` → 1 попадание, и то комментарий `pc_orchestrator.py:2801` «детект „ПК молчит“ **не нужен**». Реально: `:108` 5400 (застрявший `in_progress`) и `:116 PC_CHAIN_STALE=900` (цепь стоит МЕЖДУ шагами) | НЕТ КОДА + ловушка | «40 минут молчания < 60 ⇒ штатно», хотя цепь уже 25 минут как досдвинул `process_stuck_chains` (`:3490`) |
| A7 | `dec_port_spec.md:89` | реапер идёт **ПЕРВЫМ** в цикле и тянет за собой хук цепи (`_maybe_dec_after` → сиблинги «⏭ пропущен») | `pc_orchestrator.py:2059-2060`: сначала `process_lesson_waits()`, реапер **второй**; в теле `:2044-2052` только `complete_task/_cowork/_notify_task` — хука цепи НЕТ | РАСХОДИТСЯ | ждут авто-halt цепи от реапера и каскад «⏭ пропущен» — их нет |
| A8 | `pc_orchestrator.py:104-107` (сам код) | «Порог **СТРОГО >** TASK_TIMEOUT (45 мин)… живой прогон под нож не попадёт» | `:1089 for attempt in (1, 2)` — до двух headless-попыток по `TASK_TIMEOUT=2700` каждая ⇒ верхняя граница живого прогона = **5400 = ровно PC_SINGLE_STALE**, запас нулевой. Практически спасает однопоточность демона, а не порог; повтор бывает только при `rc=0` и пустом stdout (`:1141-1144`) | ФОРМУЛИРОВКА ШИРЕ ФАКТА | «живой прогон реапер тронуть не может» — гарантии даёт не порог; при перехвате лока вторым процессом (а это ровно то, что было в 19:52) допущение перестаёт держаться |

### Ранг B — ложный диагноз о МОДЕЛИ (какая голова работала / какую ручку крутить)

| # | док:строка | что написано | что в коде | вердикт | какой ложный диагноз |
|---|---|---|---|---|---|
| B1 | `CLAUDE.md:17-18` | «Так на ПК (`THINKER_MODEL`, `EXECUTOR_MODEL`)» — как env-ручки | `pc_orchestrator.py:851-852` «Env-ручки ORCH_MODEL / EXECUTOR_MODEL / EXECUTOR_EFFORT — имена VPS-полосы, **ПК-код их НЕ читает**»; `:860 EXECUTOR_MODEL = "claude-opus-5"` — константа; `grep 'getenv("EXECUTOR' *.py` → 0 | РАСХОДИТСЯ | «поправил `EXECUTOR_MODEL` в .env» — не переключит ничего; `grep` по .env даст ложное подтверждение «настроено» |
| B2 | `CLAUDE.md:16` | «запасная — `claude-opus-4-8`» для обеих ручек ПК | у думателя есть: `:2247 THINKER_FALLBACK`, `:2396 cmd += ["--fallback-model", THINKER_FALLBACK]`. У исполнителя **нет**: `:880 argv = [cbin,"-p","--model",EXECUTOR_MODEL,"--effort",eff,prompt]` | РАСХОДИТСЯ (половина) | «отказ основной головы подхватит запасная» — для headless-исполнителя это провал задачи, а не фолбэк |
| B3 | `CLAUDE.md:21` | «Клиентский `suggest` живёт на своей паре (`SUGGEST_MODEL=sonnet`)» | `suggest.py:242 SUGGEST_MODEL = os.getenv("SUGGEST_MODEL", "fable").strip() or "fable"`, `:243` фолбэк `sonnet`, комментарий `:241` «Дефолты разумны и без .env: **fable как основная**». Нормализатора здесь нет: `grep _norm_model_id suggest.py` → 0; имя уходит прямо в `--model` (`:4912`) и в `booking_draft.py:80` | РАСХОДИТСЯ (при пустом .env) | «имена снятой головы больше не всплывут» — для клиентского контура неверно: короткий алиас `fable` → HTTP 404 (класс #194). Истина зависит от `.env`, который в этом прогоне не читался |
| B4 | `dec_port_spec.md:71` | думатель: `--model ORCH_MODEL (.env, дефолт «fable»)` + фолбэк `claude-opus-4-8[1m]` | `pc_orchestrator.py:2246 THINKER_MODEL = _norm_model_id(os.getenv("THINKER_MODEL","claude-opus-5")) or "claude-opus-5"`; `:2247` фолбэк `claude-opus-4-8` **без** `[1m]`; `ORCH_MODEL` в ПК-коде не читается | РАСХОДИТСЯ | правят несуществующую ручку; «дефолт fable» противоречит решению владельца 30.07 |
| B5 | `revizor_recon.md:129` | «`THINKER_MODEL = claude-fable-5`» — как действующее | `:2246` = `claude-opus-5`; `:2237-2239 _MODEL_ALIAS_FULL` уводит `fable`/`fable-5`/`fable5`/`claude-fable-5` → `claude-opus-5` | УСТАРЕЛО, противоречит решению 30.07 | «думатель тупит — это Fable, переключим модель». Переключать нечего; настоящие причины молчания (`_claude_budget_gate(wait_sec=0)`, `:2380`) в доке не описаны |
| B6 | `artifacts/2026-07-28-platform-recon.md:8`, `…-default-mode-recon.md:72`, `…-oauth-root-recon.md:53` | исполнитель = `pc_orchestrator.py:610` → `--model claude-opus-4-8` | `:860` = `claude-opus-5`, argv `:880` | УСТАРЕЛО (2 дня) | разбор расхода/качества исполнителя пойдёт по снятой голове |
| B7 | `artifacts/2026-07-24-executor-opus-metrics.md:26,31` | «`EXECUTOR_MODEL = "claude-opus-4-8"`»; «SUGGEST_MODEL и THINKER_MODEL **остаются на Fable**» | `:860` opus-5, `:2246` opus-5 | УСТАРЕЛО | то же |
| B8 | `.claude/settings.json.new:2`, `.claude/settings.json.bak-2026-07-23:2` | `"model": "claude-fable-5"` | живой `.claude/settings.json:2` = `"claude-opus-5"` | МИНА ДЛЯ ГРЕПА | `grep -rn '"model"' .claude/` даёт три ответа, два из них — снятая голова |
| B9 | `ENV_PLAYBOOK.md:31` | ручка модели на ПК = `.claude/settings.json → model` | `pc_orchestrator.py:2342-2355 repo_thinking_settings` читает ТОЛЬКО `effortLevel` и `env.MAX_THINKING_TOKENS`; модель headless — константа `:860` | РАСХОДИТСЯ | правка `model` в settings.json меняет только интерактивные сессии |
| B10 | `ENV_PLAYBOOK.md` (весь свод) | — | действующая голова `claude-opus-5` в своде правил обоих репо **не названа ни разу** | ПРОБЕЛ | свод, по которому сверяются обе полосы, молчит о главном решении |

### Ранг C — ложный диагноз о ЖУРНАЛЕ (запись есть/нет, формат, потери)

| # | док:строка | что написано | что в коде | вердикт | какой ложный диагноз |
|---|---|---|---|---|---|
| C1 | `CLAUDE.md:55-58`, `RC_BRAIN_BRIDGE.md:15` | «Формат строки — **точный**: `DONE RC <дата> <время>: …`» | `cowork_log_append.py:165 _RE_STAMPED = ^(?:DONE\|NOTE\|ASK\|…)\s+\d{4}-\d{2}-\d{2}\b` — после типа обязана стоять ДАТА, а стоит `RC` ⇒ не совпало ⇒ `:179 return "%s %s: %s" % (m.group(1), stamp, …)` вставляет UTC-штамп МЕЖДУ типом и `RC` | РАСХОДИТСЯ | в мозг ложится `DONE <UTC>: RC <дата>…`; поиск RC-итогов по `^DONE RC` даёт ноль → вывод «RC-сессии журнал не пишут» |
| C2 | `CLAUDE.md:75`, `ENV_PLAYBOOK.md:16` | тип записи `ARTIFACT` | `cowork_log_append.py:162 LOG_TYPES = ("DONE","NOTE","ASK","PLAN","BLOCKED","WAITING","SKIPPED")` — `ARTIFACT` нет ⇒ `:180 return "DONE %s: %s"` | РАСХОДИТСЯ | ARTIFACT ложится как `DONE …: ARTIFACT …`; ритуал «ARTIFACT, потом DONE» в мозге выглядит как два DONE; статистика по типам врёт |
| C3 | `CLAUDE.md:36` | образец `"DONE Dispatch <время>: …"` | `:179` — «Dispatch» и локальное время уезжают в ТЕЛО после UTC-штампа | РАСХОДИТСЯ | заголовки записей выглядят иначе, чем учит ритуал |
| C4 | `CLAUDE.md:41`, `RC_BRAIN_BRIDGE.md:34` | «Вызов делай с таймаутом (~30с)» | внутренний бюджет больше: `:58` и `:65` `timeout=30` на КАЖДЫЙ запрос, `:27 RETRY_TRIES=2`, `:28` пауза, два запроса (read+write) ⇒ до ~122с. Спул наполняется только в `except` (`:266`) | РАСХОДИТСЯ (опасно) | внешний `timeout 30` убивает процесс ДО `except`: строка не уходит ни в мозг, ни в спул — **тихая потеря записи**, а сессия честно скажет «журнал повис» |
| C5 | `artifacts/2026-07-28-journal-write-fixes.md:43-46` | «**НЕ СДЕЛАНО**, на решение владельца: один повтор на транспортных ошибках» | `cowork_log_append.py:26-28` `_RETRY_HTTP`/`RETRY_TRIES`/`RETRY_PAUSE_SEC`, `:73 def transient`, `:80 def with_retry` | УСТАРЕЛО (сделано) | развилка читается открытой → сделают ретрай второй раз, поверх спула (риск дубля, класс `cowork-log-false-401`) |

### Ранг D — ложный диагноз о ГАРДЕ и КРАСНОМ

| # | док:строка | что написано | что в коде | вердикт | какой ложный диагноз |
|---|---|---|---|---|---|
| D1 | `dec_port_spec.md:98` | «КРАСНОЕ НЕ ОСЛАБЛЕНО… approve обхода гейта НЕ создаёт» | `pc_orchestrator.py:1082-1085` `env[APPROVED_KINDS_ENV]=",".join(kinds)` + `APPROVED_CLAUSE` (`:249-255`: «гард на этот класс в этом запуске карточку **не поставит**»); гард: `pretool_guard.py:1743-1744 if kind in owner_approved_kinds(env): return ("approved", kind, obj)` | РАСХОДИТСЯ | «после approve гард по-прежнему непробиваем» — «да» адресно снимает гард с НАЗВАННОГО класса в ре-ране |
| D2 | `artifacts/2026-07-29-guard-card-noise.md:197` | «`card_or_journal()` считает объект **и** число» | `pretool_guard.py:1916 return bool((obj or "").strip()) or kind in _HARD_CARD` — число не участвует вовсе | УСТАРЕЛО (перекрыто 29.07) | «цифра в команде родит карточку» — это и был закрытый баг |
| D3 | `artifacts/2026-07-29-one-card-rule-both-lanes.md:249-253` | «на ПК `sqlite3 <файл> "SELECT…"` красная (вид `sqlite`)» | `pretool_guard.py:673 _sqlite_decide`; `:1446-1447 if sq is None or sq[0] == "sqlite_read": continue`; `:1708` `sqlite_read` → `_stays_red` False | УСТАРЕЛО (30.07) | «ПК строже сервера на sqlite» — для чтения больше нет; ждут карточку, её нет |
| D4 | `artifacts/2026-07-25-pc-permission-cards-narrowed.md:116` | «`clasp push` → ask (живые таблицы всегда спрашивают)» | `.claude/settings.json:40,47` — `Bash(clasp push:*)` и `PowerShell(clasp push *)` **в allow**; гард `pretool_guard.py:550-551 clasp_push_pinned` | РАСХОДИТСЯ | «правка живых таблиц не пройдёт молча» — по закреплённому пину проходит |
| D5 | `docs/guard-model.md` (имя файла) | — | документ **вообще не про `pretool_guard`**: слова `pretool_guard` в нём нет; это карта клиентского контура (suggest/moderation) | ЛОВУШКА ИМЕНИ | по имени берут «карту гарда разрешений» и не находят там ничего про классы/карточки; перечни классов гарда (`pretool_guard.py:163-181`, `:242-264`) не сведены **ни в одном** доке |
| D6 | `CLAUDE.md:96-97` | канал зелёный ПО ИМЕНИ модуля (`_RE_SAFE_SCRIPTS`) | `pretool_guard.py:710 _RE_SAFE_SCRIPTS = (cowork_log_append\|dispatch_notify\|brain_writer)\.py`, `:1476-1477 → defer`. Но выше стоят `:1463` (`.env` → ask) и `:1469-1474` (`edit_claude`, `outside`) | СОВПАДАЕТ, НО НЕПОЛНО | «доверенный писатель пройдёт всегда» — имя не спасает от веток, стоящих раньше |
| D7 | `artifacts/2026-07-25-approve-layers-repair.md` | «слой 2 переведён с ТЕМ на ОПЕРАЦИИ» | это только VPS. На ПК `pc_orchestrator.py:2593-2596 _HEADLESS_IMPOSSIBLE_RE = …\|лист\s*1\|\bcrm\b\|зарплат\|байки\|транзакц\|проводк\|деньг\|касс\|удал(?:и\|ени\|яе\|ён)\|календар` — **до сих пор ТЕМЫ** | ЗЕРКАЛЬНАЯ ДЫРА (свод п.9) | «фикс общий» — на ПК «покажи отчёт по деньгам» по-прежнему краснеет темой. Точки касания: `:2593` и `:2653` |

### Ранг E — ложный диагноз о КЛИЕНТСКОМ КОНТУРЕ

| # | док:строка | что написано | что в коде | вердикт | какой ложный диагноз |
|---|---|---|---|---|---|
| E1 | `task-2026-07-20-questions-filter.md:9-11` | строк «name of your apartment»/«Hotel Name» в репо НЕТ, источник на VPS | `suggest.py:2790`, `:2795`, `:2978`; голдены `test_suggest.py:1743-1758` | РАСХОДИТСЯ (born-false, см. §0) | правку отправят на VPS мимо живого регекса |
| E2 | `guard-model.md:12` | транскрипт собирается как «**Клиент:/Менеджер:**» — «канонический источник для всех детектов» | `suggest.py:1127 lines.append(f"[{who}]: {body}")`, `who` = `менеджер`/`клиент` (`:1112`); фильтры `:1765 l.startswith("[клиент]:")`, `:1771 "[менеджер]:"` | РАСХОДИТСЯ | фикстура «по карте» даёт `_client_text() == ""` **молча**: детекты вернут False, тест зелёный, прод мёртв — ровно класс «мок ≠ реальность» из CLAUDE.md |
| E3 | `guard-model.md:53` | `parse_approval`: «+»=approve, «-»/«нет»=reject, **иной текст=edit** | `suggest.py:1010` approve ещё и `("да","ок","ok","yes","+1")` + `low.startswith("да ")`; `:1015-1016 if not t: return "reject", None` — **пустая реплика = reject**. Тот же дефект в докстринге самой функции (`:1002-1005`) | РАСХОДИТСЯ | «менеджер отправил пусто ⇒ уйдёт как правка» / «черновик потерялся в IPC» — он штатно отклонён |
| E4 | `guard-model.md:61` | `mark` пишет `ready\|test_held\|rejected` | `moderation_ipc.py:239-241` — это про `set_decision` (там `assert`); `mark` (`:249-250`) имеет ДРУГОЙ алфавит `sent\|failed\|test_held` и **ассерта нет**; `moderation_bot.py:138` пишет `rejected` именно через `mark` | РАСХОДИТСЯ | «`set_decision` пропустил мусорный статус» — правят ассерт, который ни при чём |
| E5 | `notes/guard-quote-price.md:48-49` | «в блок ЦЕНА попадает и суточная (449), и итог (1685) — **у LLM перед глазами оба числа**» | на главной ветке `suggest.py:4343 marker_mode = (kind == "ok" and not pct)` → `_quote_marker_note()` (`:3326-3331`: «сам НИКАКИЕ числа… НЕ называй»), а служебный `<<<QUOTE>>>`-блок из промпта **вырезается**: `:4566-4567 pn_prompt = _QUOTE_BLOCK_RE.sub("", pn_prompt)`. Числа вставляет КОД | РАСХОДИТСЯ | «LLM видит 449 и умножает» → пойдут убирать `day_price` из `_client_price`, а на этой ветке LLM его не видит (остаточный источник — `CRITICAL_FACTS`, `suggest.py:280`, склейка `:4675`) |
| E6 | `notes/greet-guard.md:62-70` | «РАЗРЫВ (root cause класса «е»): автоприветствие не ловится → бот здоровается повторно» — как ЖИВОЙ дефект | закрыто тремя слоями: `suggest.py:1228 _AUTOGREETING_RES`, `:1234 autogreeting_already_sent` (вызов `:6932`), детерминированный срез `:6987-6992 strip_greeting`, зеркало strategy-пути `:6286 strip_greeting_for_window` | УСТАРЕЛО | чинят закрытую дыру; настоящая причина сегодня — либо третье поколение текста автогритинга (в `_AUTOGREETING_RES` две сигнатуры), либо путь мимо `on_client_message` |
| E7 | `sales-method-2026-07-22.md:59` | воронка: «даты → паспорт → телефон → время/точка → подтверждение» | `suggest.py:3124-3136`: `dates` → `dates_fix` (`:3127`) → `model` (sheet) → **`if not ready: return "offer"`** (`:3128-3129`) → `passport` → `phone` → `pickup` → `confirm` | НЕПОЛНО | шаг «Бронируем?» и `dates_fix` невидимы по доке — их сочтут дефектом воронки |
| E8 | `sales-method-2026-07-22.md:40` | «Доставка: сумма по району + **от вас бесплатный забор**» (безусловно) | `suggest.py:4528-4530` — бесплатный забор ТОЛЬКО при оплаченной доставке, «при самовывозе бесплатного забора НЕ обещай»; страховка `postcheck_free_pickup` `:5670` | РАСХОДИТСЯ | цитата дока = ровно та фраза, которую код считает дефектом |
| E9 | `sales-method:42-46` ↔ `suggest.py:3177` | док: чек-лист брони (паспорт+апартаменты+шлемы+телефон); он же перенесён в промпт `suggest.py:4547-4549` | одновременно `:3177` «Задай **РОВНО ОДИН** вопрос за ответ»; детерминированной страховки нет — `ensure_closing_question` (`:5716`) только ДОПИСЫВАЕТ вопрос, лишние не режет | ВНУТРЕННИЙ КОНФЛИКТ ПРОМПТА | «бот прислал анкету — сломался фильтр вопросов»; сломан не фильтр, конфликтуют два живых блока одного промпта |
| E10 | `revizor_recon.md:12-13` | «Источник правды по клиентским окнам — **ОДНА** таблица `drafts`» | `moderation_ipc.py:91 CREATE TABLE drafts`, **но `:116 CREATE TABLE intake`** (мост «Заявка → INTAKE») и `:108 meta` | УСТАРЕЛО (неполно) | окно ищут в `drafts`, не находят → «данных нет», хотя они в `intake` |
| E11 | `revizor_recon.md:101,266` | `suggest_pairs.jsonl` — «восстанавливаемая история окна помимо БД» | `ls suggest_pairs.jsonl` → **No such file**; рядом живёт `suggest_pending.jsonl` | РАСХОДИТСЯ (файла нет) | «реплей окна есть» — в момент разбора инцидента восстанавливать будет не из чего |
| E12 | `artifacts/2026-07-26-reviewer-wired.md:109-110` | «литерала `sent` в коде модерации нет — ревизоры отбирают вхолостую» | `suggest.py:6802 moderation_ipc.mark(r["id"], "sent" if ok else "failed", …)`; `reviewer.py:244 WHERE status IN ('sent','ready','test_held')` | РАСХОДИТСЯ | ложный «остаток» уводит в несуществующий баг |

### Ранг F — ложный диагноз о RC-полосе и Планировщике

| # | док:строка | что написано | что в коде | вердикт | какой ложный диагноз |
|---|---|---|---|---|---|
| F1 | `artifacts/2026-07-24-rc-churn-liveness.md:30,42-44`, `…-rc-env-cleanup.md:51`, `…-rc-named-restart-selfheal.md:101` | пороги живости общие, `LIVENESS_MAX_AGE=1200`, отдельного порога у серверной ветки нет; страйков нет | `rc_supervisor.py:96 SERVER_LIVENESS_MAX_AGE=3600`, `:119 NAMED_LIVENESS_MAX_AGE=43200`, `:124 LIVENESS_STRIKES=3`, `:129-132 spec["max_age"]`; 1200 остался «дефолтом для ветки без своего ключа» (`:36`) | УСТАРЕЛО (фикс `2e0c214`, 24.07 20:55) | «сторож гасит канал каждые 20 минут» — сегодня неверно по построению |
| F2 | `artifacts/2026-07-29-rc-token-staleness-prevention.md:5` | «**НЕ ПРИМЕНЕНО**: решение за владельцем (варианты а/б)» | вариант (а) применён: модуль `rc_auth_detect.py` (167 строк, `:71 ENABLED = os.getenv("RC_AUTH_WATCH","1") != "0"`), проводка `rc_supervisor.py:139-142`, `:478`, `:515-530` | УСТАРЕЛО | «детектора нет, надо писать» → продублируют существующий модуль |
| F3 | `artifacts/2026-07-30-rc-connecting-not-stale-creds.md:88-102` | развилка владельцу; рекомендация — вариант 1 «снять пробу с ветки канала» | взят вариант 3 (поднять порог): проба у ветки ОСТАЛАСЬ, `rc_supervisor.py:368 probe_detail` гоняется на обеих ветках, `:119` = 43200 | УСТАРЕЛО / решено иначе | исполнение старой рекомендации снимет ловлю настоящего трупа канала |
| F4 | `artifacts/2026-07-24-rc-lock-standstill.md:61-76` + готовый `2026-07-24-TurboBabyRC.hardened.xml` (в нём `<TimeTrigger>`, `PT10M`, `<StartWhenAvailable>true`) | усиление задачи Планировщика | трекаемый `pc_remote_control.task.xml:55-57` — только `<LogonTrigger />`; `:39-40` `DisallowStartIfOnBatteries/StopIfGoingOnBatteries = true`; `:43-44 RestartOnFailure Count=3`; `StartWhenAvailable` отсутствует | НЕ ПРИМЕНЕНО (в репо) | пара «артефакт + hardened.xml» читается как выполненная работа; страховки нет. Живой Планировщик в этом прогоне не опрашивался (`schtasks` — красное) |
| F5 | `RC_BRAIN_BRIDGE.md` (весь док) | ритуал журнала RC | ни одного порога живучести: `rc_supervisor.py:58 RESTART_DELAY=15`, `:62 NOT_READY_DELAY=300`, `:79 CHECK_INTERVAL=30`, `:84 1200`, `:87 GRACE_SECONDS=180`, `:90 RC_LIVENESS`, `:96 3600`, `:119 43200`, `:124 STRIKES=3` — **в нормативных доках нет ни одного** | ПРОБЕЛ | памятка RC не описывает ни один механизм живучести канала; диагноз строится по артефактам, а они устарели (F1–F3) |

### Ранг G — ложный диагноз о ДЕКОМПОЗИЦИИ и очереди

| # | док:строка | что написано | что в коде | вердикт | какой ложный диагноз |
|---|---|---|---|---|---|
| G1 | `dec_port_spec.md:30,31,73` | метка родителя цепи `Filipp-328-dec` / `Filipp-pc-dec` | `grep "Filipp-328-dec" *.py` → **0**; своя метка `pc_orchestrator.py:2526 PC_LOCAL_DEC_FROM = "Filipp-pcloc-dec"`; `Filipp-pc-dec` в коде — только как ЧУЖОЕ, которое надзор намеренно игнорирует (`:2855`, тест `test_pc_local_dec.py:566`) | РАСХОДИТСЯ | греп очереди по метке из доки даёт пусто → «цепей нет / декомпозиция не работает» |
| G2 | `dec_port_spec.md:87` | `TASK_TIMEOUT=600с` для «задача:», `TASK_TIMEOUT_DEV=2700` для «тз:», выбор `_task_timeout` | `pc_orchestrator.py:100 TASK_TIMEOUT = ... "2700"` — один порог на всё; `grep TASK_TIMEOUT_DEV` → 0, `grep _task_timeout` → 0 | РАСХОДИТСЯ (то же ИМЯ, ×4,5 значение) | «задача висит 20 мин ⇒ должна была отвалиться по 600 ⇒ демон завис» — у неё ещё 25 минут штатного бюджета |
| G3 | `dec_port_spec.md:43,82` | закрытие шагов картами `♻️ заменён коррекцией плана` / `⏭ закрыт досрочно` | `pc_orchestrator.py:2538-2539` константы объявлены, **использований 0** (только сверка литералов `test_pc_orchestrator.py:3512-3513`): sequential-релиз не оставляет шагов, которые надо закрывать | МЁРТВЫЕ КОНСТАНТЫ | ищут в очереди ♻️-карты как след коррекции плана; реальный след — `[коррекция плана родитель pid]` (`:3375`) |
| G4 | `dec_port_spec.md:73` | «После 🩹-done шага адаптация НЕ зовётся (guard по номеру)» | такого guard'а нет: `_loc_chain_tick:3395` берёт перерождённый шаг (тот же `i`, старший id) → `:3406 _loc_after_done` → `:3338` consult срабатывает | РАСХОДИТСЯ | «после самопочинки думатель адаптации молчит» — он зовётся |
| G5 | `dec_port_spec.md:45,96` | guard последовательности в `process_new` (`_DEC_WAIT_STATUSES`, `_earlier_new_sibling`) | обоих имён в коде 0; `pc_orchestrator.py:2518-2519` прямым текстом «у `process_new` **нет guard'а последовательности**»; порядок держит sequential-релиз | НЕТ КОДА | ищут защиту, которой нет, и не видят настоящий механизм |
| G6 | `dec_port_spec.md:95` | `INBOX_TOPIC_ID` задан → **единый инбокс 1160 для ОБЕИХ полос** | `pc_orchestrator.py:310 def set_needs_approval(self, tid, what, topic=NEEDS_APPROVAL_TOPIC)`; `:117 NEEDS_APPROVAL_TOPIC = ... "829"`. `INBOX_TOPIC_ID` (`:118`) используется для инцидентов (`:1532`), не для красных карточек | РАСХОДИТСЯ | «красное с ПК придёт в 1160» — по дефолту уходит в 829 |
| G7 | `pc_orchestrator.py:5285`, `:5086` (докстринги кода) | «ОДНА сводная owner-карточка в **инбокс 1160** (`NEEDS_APPROVAL_TOPIC`)» | `NEEDS_APPROVAL_TOPIC` по дефолту **829** (`:117`); 1160 — это `INBOX_TOPIC_ID` (`:118`) | КОММЕНТАРИЙ ПРОТИВОРЕЧИТ КОНСТАНТЕ | истина зависит от `PC_NA_TOPIC` в `.env`, который в этом прогоне не читался; читатель кода получает утверждение, не подтверждаемое дефолтом |
| G8 | `dec_port_spec.md:95` ↔ `pc_orchestrator.py:117,310` | карточку несёт **devbot** (док, и `:2807`) ↔ «Splinter постит карточку» (`:117`, `:310`) | — | ПРОТИВОРЕЧИЕ ВНУТРИ КОДА | ищут не тот процесс, когда карточка не пришла |
| G9 | `dec_port_spec.md` (весь) | — | ПК-механизмы вне доки: `PC_CHAIN_STALE=900` + `process_stuck_chains` (`:3490`); обязательный смоук-шаг СВЕРХ `MAX_STEPS` (`:2660-2678`, `:2777`); уступка ревизорской цепи (`:3328-3337`) | ПРОБЕЛ | «N шагов в сводке ≠ длине плана» и «цепь молча стоит» выглядят как дефекты |
| G10 | `revizor_recon.md:150,158` | класс очереди — `BridgeClient` (`pc_orchestrator.py:153`) | `grep BridgeClient pc_orchestrator.py` → **0**; живой класс `:276 class Bridge`. `BridgeClient` существует только в `manager-bot/bridge_client.py:15` — нетрекаемом клоне от 02.06 | РАСХОДИТСЯ (опасно) | грепающий по имени из доки попадает в июньский клон VPS и чинит чужой код, думая, что смотрит ПК-дирижёра |
| G11 | `revizor_recon.md:151` | «Все задачи **жёстко** в `LANE = "pc"` (:57)» | `pc_orchestrator.py:98 LANE = os.getenv("PC_LANE", "pc")` — env-переопределяемо | РАСХОДИТСЯ | «полосу подменить нельзя» — `PC_LANE` в чужом окружении уведёт очередь |

---

## §3. Гниль адресов: `file:line` как отдельный класс

Содержание доков чаще право, чем нет; **сгнили адреса**. Из ~55 ссылок `file:line`
в `revizor_recon.md` и `task_notes/revizor-2026-07-13-zh.md` в цель попадают две
(`topics_map.json:14`, `moderation_ipc.py:91`).

Сдвиг по `suggest.py` (док → факт): `on_client_message` 5634→**6899** ·
`generate_draft` 4932→**6169** · `post_draft` 5453→**6718** ·
`postcheck_draft` 4367→**5346** · `price_sheet` 2783→**3446** ·
`is_approver` 1233→**1697** · `transcript_from` 1023→**1107**.
Сдвиг растёт от +80 в начале файла до **+1300** в конце; у recon-доков 13.07 — до **+3890**.

**Худший вид:** адрес существует и выглядит осмысленно.
`revizor-2026-07-13-zh.md:29` шлёт за приветствием на `suggest.py:2104` — там сейчас
`def _detect_model`. Три свежих артефакта ссылаются на `pc_orchestrator.py:2016` в трёх
разных смыслах («нормализатор моделей», «признак sqlite3», «реапер») — по факту там
`def process_stuck_singles`.

**Скорость гниения измерена.** Артефакт `artifacts/greeting-guard-notes.md` (создан
30.07 в 01:30, лежит **вне** `docs/artifacts/`, не в git) даёт `generate_draft` = 6110,
`on_client_message` = 6840. Через **14 минут** коммит `4528917` (30.07 01:44) добавил в
`suggest.py` ровно **59 строк** (`git show --stat` → `59 insertions(+)`) — и все три
адреса артефакта уехали ровно на +59. Ссылка `<файл>:<номер>` живёт часы; ссылка
`<файл>:<имя функции/константы>` пережила всё.

---

## §4. Устаревшие упоминания сред, которых у нас нет

### 4.1. Кода нет в этом репозитории (VPS-полоса)

| сущность | где упомянута | доказательство отсутствия |
|---|---|---|
| `orchestrator_daemon.py` (источник всей `dec_port_spec`) | `dec_port_spec.md` целиком | файла нет в трекаемом дереве |
| `process_orphans`, `ORPHAN_TTL`, `TASK_TIMEOUT_DEV`, `_task_timeout`, `APPROVED_TTL`, `PC_STEP_TIMEOUT`, `PC_SILENT_MARK`, `_DEC_WAIT_STATUSES`, `_earlier_new_sibling`, `get_pending_multi`, `report_results` | `dec_port_spec.md:29,41,45,52,87,89,90,91,95,96`; `revizor_recon.md:230` | grep по `*.py` → 0 у каждого |
| `ORCH_MODEL`, `ORCH_MODEL_FALLBACK`, `EXECUTOR_EFFORT` | `CLAUDE.md:18`, `ENV_PLAYBOOK.md:31-32` | читаются нулём кода; `pc_orchestrator.py:851-852` прямо: «имена VPS-полосы, ПК-код их НЕ читает» |
| `cclog`, `cclog … --pulse` | `CLAUDE.md:61`, `ENV_PLAYBOOK.md:19`, `RC_BRAIN_BRIDGE.md:41-50` | `git ls-files ':(glob)cclog*'` → пусто |
| `splinter.py`, `devbot.py`, `bot.py` (Bridge), `gate.py`, `auditor.py`, `wa_webhook.py`, `bridge_client.py`, `claude_client.py` | `docs/artifacts/2026-07-2[3-9]-vps-*.md` | ни одного в `git ls-files` |
| `orchestrator_daemon.log` | `ENV_PLAYBOOK.md:190` | файла нет (`pc_orchestrator.log` и `pretool_guard.log` — есть) |
| баннер демона `model=… fallback=… effort=…` | `ENV_PLAYBOOK.md:45-47` | ПК-баннер `pc_orchestrator.py:5691-5701` таких полей не печатает — по логу старта ПК модель исполнителя не видна |
| контур WhatsApp/360dialog (`wa_queue.db`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET`, `WA_360_SANDBOX_KEY`, …) | `artifacts/2026-07-25-vps-rebuild-main-merge-plan.md` | 0 в ПК-коде — среда целиком чужая |
| `systemd`-юниты (`splinter.service`, `orchestrator-daemon.service`, `bridge.service`, `nginx`) | `dec_port_spec.md`, `vps-*` артефакты | на ПК механизм иной — `schtasks` (`pc_orchestrator.py:96`, `pc_agent.py:509`) |
| `_repo_tracked()` (гард VPS), «файл 907→930 строк» | `KB_PULSE.md:11-19` | на ПК другое имя `_is_repo_tracked` (`pretool_guard.py:1145`) и другой размер (2026 строк) |
| `_ENTITY_*`, `_extract_first_entity`, `_is_test_entity` | `artifacts/2026-07-26-entity-guard-and-ssh-key.md:28-32` | 0 в ПК-гарде — класс-фикс живёт на одной полосе (нарушение свода п.9) |

### 4.2. Сущностей нет нигде (мертвы по факту)

| сущность | где упомянута | доказательство |
|---|---|---|
| `lesson_urok.py` | `artifacts/2026-07-30-revizor-client-contour-recon.md:139` | файла нет; есть только `test_lesson_urok.py` — модуль назван по имени своего теста |
| `suggest_pairs.jsonl` | `revizor_recon.md:101,266` | `ls` → No such file (при живом `record_pair`, `suggest.py:6671`) |
| `moderation.db` | `artifacts/2026-07-29-guard-source-write-noise.md:149` | живая БД называется `moderation_ipc.db` |
| `guard_quote_price`, `quote_price_numbers`, `quote_price_mismatches`, `_quote_hard_directive`, `price_fallback_from_quote`, `_PC_PERIOD`, `_PC_PRICE_TOTAL_RE`, `_PC_DEPOSIT_RE` | `notes/guard-quote-price.md:77-125` | 0 определений; живая замена — `_MF_PERIOD` (`suggest.py:5190`), `extract_money_figures` (`:5203`) |
| `filter_booking_questions` | `task-2026-07-20-questions-filter.md` (как живой фильтр) | определена (`suggest.py:2992`), но **из прода не вызывается** (только тесты); живой фильтр — `drop_answered_questions` (`:5752`, вызовы `:6218`, `:6274`) |
| среды RC `env_0135pmoZ…`, `env_01Abfcn6…`, … | `artifacts/2026-07-24-rc-*.md` | мертвы по построению: `rc_supervisor.default_spawn` (`:393-408`) поднимает ветку заново, `claude rc` регистрирует НОВУЮ среду на каждый старт |
| PID-снимки (`6272`, `15112`, `13120`, `17896`, супервизоры `10020`/`6888`) | те же артефакты | снимки суток 24.07 |
| версия CLI `2.1.217` | `artifacts/2026-07-24-cli-update-fix.md:14,31,39` | на диске `2.1.218` |

### 4.3. Мины внутри рабочего дерева — опаснее отсутствия

| что | факт | чем опасно |
|---|---|---|
| **`D:\turbobaby-bot\manager-bot\`** | снимок VPS-кода от **01–02.06.2026** (`bot.py`, `splinter.py`, `bridge_client.py`, `claude_client.py`, `memory.db`, свой `.git`, свой `venv` с `openai/`), под `.gitignore:22`, `git ls-files manager-bot` → **0** | 1) любой `grep -rn` по репо молча смешивает две реальности (см. G10); 2) **боевой `suggest.py` читает из него фолбэки**: `:258 LOCAL_FAQ = …/manager-bot/docs/turbobaby_faq_v1.md`, `:492 PARK_LIST_FILE = …/manager-bot/docs/park_list.md` — оба файла на месте, датированы 02.06. При падении Bridge клиент получит **июньский срез парка/FAQ** как живой |
| `docs/artifacts/2026-07-23-guard-v3-files/pretool_guard.py` | полная копия гарда на 23.07 — **872 строки** против живых **2026** | `grep -rn` по гарду выдаёт две версии; вывод «правила X в гарде нет» получается из архивной копии. Материальная причина класса «чинишь не тот гард» |
| `.claude/settings.json.new`, `.claude/settings.json.bak-2026-07-23` | обе держат `"model": "claude-fable-5"` | см. B8 |
| 20 скриптов в `tmp/`, читающих конфиг с секретами | `grep -ln "BRIDGE_TOKEN\|load_env\|load_dotenv" tmp/*.py` → 20 файлов | ровно тот анти-паттерн, который `CLAUDE.md` объявила ЗАПРЕЩЁННЫМ (класс 328) — запрет в доке есть, следы в дереве остались |
| `artifacts/greeting-guard-notes.md` (корень репо) | артефакт вне `docs/artifacts/`, не в git | нарушает собственное правило артефактов CLAUDE.md; второй, расходящийся источник по теме `notes/greet-guard.md` |
| 22 артефакта в `docs/artifacts/` не в git | `git ls-files docs/artifacts` = 75, файлов = 90, untracked = 22 | на другой машине/полосе этих разборов не существует |

### 4.4. Названо мёртвым, а живо

- **Google Apps Script / clasp** — жив: `pretool_guard.py:177`, `:251-254`, `:432-456`,
  реестр пинов `clasp_prod_pins.json`, 7 правил в `.claude/settings.json:34-47`.
  Bridge и Brain — это Apps Script; часть «мёртвых» переменных (`COWORK_MAX_BYTES`,
  `BRAIN_FOLDER_ID`, `QUEUE_COL`) живёт в Script Properties и грепом по `*.py` не
  находится никогда.
- **Темы Telegram 1160 / 829 / 328** — все три живы: `dispatch_notify.py:104`, `:110`;
  `pc_orchestrator.py:117`, `:118`, `:153`; `session_watch.py:85`.
- **OpenAI/GPT** — в доках 0 упоминаний; в коде только внутри протухшего клона
  `manager-bot/`.

---

## §5. Документ молчит: модули без единого упоминания в нормативных доках

`rc_supervisor.py`, `session_watch.py`, `selfupdate_gate.py`, `gate_selective.py`,
`client_contour.py`, `diag_status_truth.py`, `reviewer.py`, `scout.py` — **0**
упоминаний в `docs/*.md` + `docs/notes/*.md` + `CLAUDE.md` (считано грепом).

Это обратная сторона того же дефекта: `session_watch.py` — детектор немоты сессий,
родившийся из инцидента 29.07 (сессия жила 2 ч 53 мин с нулём запросов к модели);
`reviewer.py` — детерминированные чеки черновиков. Оба живут в проде
(`pc_orchestrator.py:5449 import reviewer as _rv`), но диагностировать их поведение
по нормативным докам нельзя — их там нет.

`README.md` — 8 строк, из них 5 «smoke pc-полоса» от 04–05.07; ни один процесс не описан
при 165 коммитах в код после его последней правки.

---

## §6. Классы дефекта (что чинить как класс, а не как строку)

1. **Born-false — документ неверен в момент коммита.** `dec_port_spec.md:89` (код на
   25 ч раньше), `task-2026-07-20-questions-filter.md:9-11` (код в ТОМ ЖЕ коммите).
   Признак: утверждение вида «в репо этого нет / это не трогается» без указания полосы
   и даты замера.
2. **Абсолют без области действия.** Утверждение про VPS, прочитанное на ПК, где есть
   одноимённый механизм с другим порогом: реапер (A1–A2), `TASK_TIMEOUT` (G2),
   `APPROVED_TTL`/`APPROVAL_TTL` (A5), heartbeat (A3–A4).
   **Это самый вредный класс: имя совпадает, поведение — нет.**
3. **Гниль адресов.** `file:line` живёт часы (§3). Опаснее всего, когда адрес попадает
   в осмысленный чужой код.
4. **Артефакт как открытая развилка.** Документ пишет «НЕ ПРИМЕНЕНО / решение за
   владельцем», правка давно в коде (F2, F3, C5, `2026-07-24-rc-churn-liveness`,
   `2026-07-24-odometer-soft-gate`, `2026-07-23-guard-v3-ambiguous-defer`).
   Следующая сессия делает работу второй раз — или отменяет чужое решение.
5. **Зеркальная дыра (свод п.9).** Класс-фикс приземлён на одной полосе: слой 2
   «темы→операции» (D7), entity-гард (§4.1).
6. **Противоречие внутри кода.** `NEEDS_APPROVAL_TOPIC` = 829 при докстринге «инбокс
   1160» (G7); devbot против Splinter (G8); докстринг `parse_approval` против её
   собственного тела (E3).
7. **Свежесть ≠ верность.** Ошибочны не «старые» доки, а те, что писались за 4–24 часа
   до правки соседней задачи. За 30.07 `pc_orchestrator.py` вырос на ~450 строк двумя
   коммитами — и все адреса артефактов 28–30.07 уехали на 200–480 строк.

---

## §7. Индекс дрейфа: сколько коммитов в код прошло после последней правки дока

| документ | заморожен | коммитов в профильный код после |
|---|---|---|
| `README.md` | 2026-07-05 | **165** (`pc_orchestrator`+`suggest`+`pc_agent`) |
| `docs/revizor_recon.md`, `docs/revizor_checklist.md` | 2026-07-13 | **98** (`pc_orchestrator`+`suggest`) |
| `docs/dec_port_spec.md` | 2026-07-12 | **59** (`pc_orchestrator.py`) |
| `docs/notes/greet-guard.md`, `docs/notes/guard-quote-price.md` | 2026-07-14 | **47** (`suggest.py`) |
| `docs/task-2026-07-20-questions-filter.md` | 2026-07-20 | **25** (`suggest.py`) |
| `docs/ENV_PLAYBOOK.md` | 2026-07-25 | **21** |
| `docs/sales-method-2026-07-22.md` | 2026-07-22 | **18** |
| `docs/RC_BRAIN_BRIDGE.md` | 2026-07-25 | **11** |
| `docs/guard-model.md` | 2026-07-23 | **10** (`pretool_guard.py`) |
| `docs/KB_PULSE.md` | 2026-07-25 | **7** |

Исключение, подтверждающее правило: **`docs/revizor_checklist.md` — единственный
полностью актуальный документ.** Причина механическая, а не дисциплинарная: его
**читает код на каждом тике** (`pc_orchestrator.py:4769 _revizor_checklist`,
`:4779` срез HTML-комментариев, `:4783` fail-safe на `REVIZOR_CHECKLIST_DEFAULT`), а
дефолт в коде обязан быть его копией. Программная сверка тела файла с
`REVIZOR_CHECKLIST_DEFAULT` (`:4709-4735`) дала расхождение **только в двух пустых
строках**.

---

## §8. Что совпало (не всё плохо)

Совпали по существу и проверены дословно, в частности:
регексы и маркеры декомпозера (`_STEP_RE`, `_SUM_RE`, `_HEAL_TASK_RE`, `_CONVERT_RE`,
`_REJECT_PREFIX`, `_PLAN_LINE_RE`, `_HEADLESS_IMPOSSIBLE_RE` по составу) ·
`MAX_STEPS=8` · `PLAN_ADAPT_MAX=2` · `STEP_SELFHEAL_TIMEOUT=180` ·
`PC_DEC_PLAN_TIMEOUT=600` · все обрезки промптов (1500/2000/1200/400/300/200) ·
`RESULT_MAX=4500` · весь контракт `brain_writer` (бэкап ДО записи `:349`, якорь ровно
один `:426-428`, идемпотентность `:420-421`, обратное чтение `:359-372`) ·
`.claude/settings.json` против `CLAUDE.md` (модель, `xhigh`, `alwaysThinkingEnabled`,
`MAX_THINKING_TOKENS=31999`) · хук `SessionEnd` → `dispatch_notify.py:501` «✅ Code-сессия
завершена» → `send_critical` (1160 → личка) → `:549 _cowork` · `EFFORT_LEVELS`
и `DEFAULT_EFFORT=xhigh` · `task_metrics.py` байт-в-байт с VPS-полосой (sha256 сверен) ·
16 зон доставки позиционным списком в живой фикстуре · `_NoRedirectHandler`/`HTTPError` ·
«слов «гейт»/«тесты»/«коммит» в признаках нет» (`task_metrics.py:62-84`) ·
`KNOWN_MODELS` (20 пар) · `_SCOOTER_PREFIXES` · `resolve_park_model` ·
воронка `next_step`/`ensure_closing_question` · весь `revizor_checklist.md`.

Две оговорки к «совпало»:
- `guard-model.md:81` «полный гейт **1754 OK**» — сегодня `def test_` в `test_*.py` **2338**.
  Сверять «гейт зелёный» по этому числу нельзя (тесты в этом прогоне не запускались).
- совпадение содержания при сгнившем адресе — не совпадение для того, кто идёт по ссылке (§3).

---

## §9. Счёт

Числа ниже — сводка, а не гипотеза: точные посчитаны по строкам таблиц ЭТОГО файла,
приблизительные помечены `≈`.

| | |
|---|---|
| нормативных документов пройдено | **14** (`docs/*.md`, `docs/notes/`, `docs/task_notes/`, `CLAUDE.md`, `README.md`) |
| артефактов прочитано построчно | **45** (23.07–30.07) |
| проверяемых утверждений сверено | **≈400** строк сверочных таблиц шести проходов + **20** собственных проверок; часть пересекается между проходами, поэтому уникальных — не менее **350** (нижняя граница, точный дедуп не считал) |
| **расхождений по существу** (порог / поведение / дефолт / «кто что трогает» / «что запрещено») | **66** — точный счёт: 58 строк §2 + 8 строк §4.2 |
| устаревших упоминаний сред и сущностей, которых у нас нет | **17** — точный счёт: 11 строк §4.1 (VPS-полоса) + 6 строк §4.3 (мины внутри дерева) |
| сгнившие адреса `file:line` при верном содержании | **десятки**; поимённо не считал, замеры и худшие случаи — в §3 |
| документов, полностью актуальных | **1** — `docs/revizor_checklist.md` (§7) |
| документов, неверных **с момента коммита** | **2** — `dec_port_spec.md:89`, `task-2026-07-20-questions-filter.md:9-11` |
| живых модулей без единого упоминания в нормативных доках | **8** (§5) |

Правок по заданию не предлагаю.
