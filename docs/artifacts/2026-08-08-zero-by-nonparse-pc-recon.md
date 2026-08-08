# Класс «НУЛЬ ПО НЕРАЗБОРУ» на ПК-полосе — разведка списком (08.08.2026)

**Что за класс.** Читатель живого текста, у которого разбор не совпал, отдаёт наверх не ошибку,
а честный на вид нуль — «событий не было». Молчание источника и слепота читателя выглядят
снаружи одинаково.

**Откуда класс.** Пойман на полосе сервера (читатель планового тика: шаблон не совпал ни с одной
из 28 непустых строк, наверх ушло «тиков ещё не было», источник молчал 80.9 ч незамеченным).
**Туда не ходили и там ничего не проверяли** — ниже только ПК-полоса, свои координаты, свои
замеры. Правило репо «зеркало правила с чужой полосы обязано сверить его ПОСЫЛКУ» соблюдено:
посылка здесь проверена живыми пробами по каждому пункту, а не перенесена формой.

**Режим:** read-only. Правок нет, коммита нет, пуша нет, гейт не гонялся, процессы не тронуты,
боевых записей нет. Пробных скриптов не запускалось — только чтение кода и живых файлов.
`.env` и конфиги не читались: читался код, который их читает.
**Временный каталог:** `D:\turbobaby-bot\tmp\` — ничего не создавал и ничего не убирал, оставлен
как есть. Клиентский контур заморожен — его находки строкой в списке, в работу не брались.

---

## 0. Дословный вывод проб

### Проба 1 — реестр журнала `cowork_log.ledger` (источник `_journal_writes_between`)

```
=== lines total ===
500
=== FIRST LINE (first 300 chars) ===
{"ts": "2026-08-02T12:29:56.266874+00:00", "line": "DONE 2026-08-02 12:28 UTC: ARTIFACT два дефекта карточки 189 → docs/artifacts/2026-08-02-card-object-equals-rollback.md: замер до/после, что не ослаблено"}

=== LAST LINE (first 300 chars) ===
{"ts": "2026-08-08T07:34:19.078342+00:00", "line": "NOTE 2026-08-08 07:34 UTC: Orchestrator: взял задачу #403 (in_progress)"}

=== top-level keys seen ===
    500 {"ts"
```

```
=== count lines containing "ts": ===
500
=== count lines containing "line": ===
500
=== A. self-line filter: lines whose 'line' starts TYPE date time UTC: Orchestrator: ===
136
=== B. lines with a well-formed stamp at all ===
500
=== C. date span of ledger ===
"ts": "2026-08-02T12:29:56.266874+00:00"
"ts": "2026-08-08T07:34:19.078342+00:00"
```

Писатель реестра (`cowork_log_append.py`):
```
163:LEDGER_KEEP = 500          # кап: окно следов у демона — часы, а не месяцы
164:LEDGER_LINE_MAX = 300      # в реестре нужен опознавательный кусок, а не весь текст записи
```

**Вывод:** шаблон ЖИВ. 500/500 строк несут оба ключа, 500/500 — корректный штамп,
фильтр служебных строк демона ловит 136. Окно реестра — 02.08 12:29 → 08.08 07:34 UTC (кап 500).

### Проба 2 — метка ревизора (источник `_revizor_read_state` → `_revizor_tick_label`)

```
=== revizor state file on disk ===
-rw-r--r-- 1 mxfill1 197121 2336 2026-07-31 14:24 pc_orchestrator.approvals.jsonl
-rw-r--r-- 1 mxfill1 197121    2 2026-08-05 00:07 pc_orchestrator.revizor_spool.json
-rw-r--r-- 1 mxfill1 197121   74 2026-08-08 13:55 pc_orchestrator.revizor_state.json
-rw-r--r-- 1 mxfill1 197121 5218 2026-08-08 14:34 pc_orchestrator.task_started.json
-rw-r--r-- 1 mxfill1 197121   69 2026-08-08 14:33 pc_orchestrator.watchdog_state.json
=== revizor_state CONTENT ===
{"last_run": "2026-08-08T06:54:13.710599+00:00", "ts": 1786172053.7105992}
```

**Вывод:** метка жива, `last_run` сегодняшний → фраза наверх честная. Но ветка «тиков ещё не
было» отдаётся ТЕМ ЖЕ `{}` при битом/нечитаемом файле — см. п. 4 списка.

### Проба 3 — лог демона `pc_orchestrator.log` (источник `diag_status_truth.read_daemon_log`)

```
=== last 3 raw lines of pc_orchestrator.log (cut 160) ===
2026-08-08 13:55:30,272 INFO ревизор: owner-карточка создана (tid=401, инбокс 829)
2026-08-08 14:34:08,760 INFO CLAIM id=403 in_progress
2026-08-08 14:34:09,166 INFO RUN id=403 (timeout=2700s, попытка 1/2)

=== total lines ===
5752
=== _L_TS match count (ts,ms LEVEL body) ===
5736
=== _L_CLAIM: bodies starting CLAIM id= ===
160
=== _L_TERM heads present? ===
193
```

**Вывод:** шаблон ЖИВ — 5736 из 5752 строк (99.7%), CLAIM 160, терминальных голов 193.

### Проба 4 — срез журнала и снимки замерщика (кэши двух диагностик)

```
=== tmp/ slice files ===
-rw-r--r-- 1 mxfill1 197121 139494 2026-07-30 21:06 tmp/journal_slice.txt
```
```
=== journal_measure snapshots on disk (its cache) ===
-rw-r--r-- 1 mxfill1 197121  183273 2026-07-31 01:29 tmp/cowork_log.snapshot.txt
-rw-r--r-- 1 mxfill1 197121 1142016 2026-07-31 01:30 tmp/cowork_log_archive.snapshot.txt
```

**Вывод:** оба кэша ПРОТУХЛИ. `tmp/journal_slice.txt` — от 30.07 21:06, то есть **9 суток**
при окне замера `WINDOW_HOURS = 48.0`. Снимки `journal_measure` — от 31.07 01:29/01:30,
**8 суток**. Оба читаются молча и по умолчанию (без `--fetch` / `--refresh`).

### Проба 5 — лог ветки rc-server (источник `rc_auth_detect`) — **ГЛАВНАЯ НАХОДКА**

```
=== RC logs on disk ===
-rw-r--r-- 1 mxfill1 197121 184145 2026-08-06 19:38 rc_remote_control.log
-rw-r--r-- 1 mxfill1 197121  33003 2026-08-08 14:30 rc_server_debug.log
-rw-r--r-- 1 mxfill1 197121  23500 2026-08-08 13:38 rc_session_debug.log
=== live probe ===
--- rc_server_debug.log: lines=292
    'failed 401: OAuth access token has been revoked' = 0
    'status=failed' = 0
    'duration=Ns' = 0
    'session_/cse_<id>' = 0
--- rc_session_debug.log: lines=127
    'failed 401: OAuth access token has been revoked' = 0
    'status=failed' = 0
    'duration=Ns' = 0
    'session_/cse_<id>' = 0
```

Что в этом логе на самом деле:
```
=== rc_server_debug.log: first 4 lines (cut 150) ===
2026-08-06T12:38:45.776Z [DEBUG] MDM settings load completed in 33ms
2026-08-06T12:38:45.812Z [DEBUG] Broken symlink or missing file encountered for settings.json at path: C:\Program Files\ClaudeCode\managed-settings.js
2026-08-06T12:38:45.861Z [DEBUG] Error log sink initialized
2026-08-06T12:38:45.865Z [DEBUG] Error log sink initialized

=== last 4 lines (cut 150) ===
2026-08-08T07:03:09.910Z [DEBUG] [bridge:api] GET .../work/poll -> 200 (no work, 27100 consecutive empty polls)
2026-08-08T07:12:26.934Z [DEBUG] [bridge:api] GET .../work/poll -> 200 (no work, 27200 consecutive empty polls)
2026-08-08T07:21:42.401Z [DEBUG] [bridge:api] GET .../work/poll -> 200 (no work, 27300 consecutive empty polls)
2026-08-08T07:30:59.342Z [DEBUG] [bridge:api] GET .../work/poll -> 200 (no work, 27400 consecutive empty polls)

=== distinct line-shape prefixes (first 40 chars, top 12) ===
    287 ####-##-##T##:##:##.###Z [DEBUG] [bridge
      2 ####-##-##T##:##:##.###Z [DEBUG] Error l
      2 ####-##-##T##:##:##.###Z [DEBUG] Broken 
      1 ####-##-##T##:##:##.###Z [DEBUG] MDM set
```

Что детектор УМЕЕТ считать (`rc_auth_detect.py`), и что из этого уходит наверх:

```python
REVOKED_PHRASE = "failed 401: OAuth access token has been revoked"
_ID_RE = re.compile(r"(?:session|cse)_([A-Za-z0-9]{6,})")
_DURATION_RE = re.compile(r"duration=(\d+)s")
_FAILED_MARK = "status=failed"
...
def feed(lines, state):
    for line in lines or []:
        state["seen"] = state.get("seen", 0) + 1      # ← счётчик прочитанного ЕСТЬ
...
def should_restart(state, needed=None):
    n = FAILS_NEEDED if needed is None else needed
    return int((state or {}).get("failures", 0)) >= max(1, n)   # ← и он тут НЕ читается
```

**Вывод:** ни один из четырёх маркеров детектора не встречается ни разу на 292 живых строках
(лог писан сегодня в 14:30) и ни разу на 127 строках именованной ветки. Живой формат —
`<ISO>Z [DEBUG] [bridge:api] …`, идентификаторов сессий, `duration=`, `status=failed` в нём нет
вовсе. Счётчик прочитанных строк `seen` в состоянии ЕСТЬ, но ни `should_restart`, ни `describe`
его не читают: наверх уходит `failures = 0`, что супервизор понимает как «авторизация цела».
Отличить «протухших кредов не было» от «формат лога мне незнаком» невозможно ни по одному полю,
которое поднимается выше.

**Честная граница вывода:** проба доказывает отсутствие ОПОЗНАВАТЕЛЬНЫХ ПРИЗНАКОВ формата;
она НЕ доказывает, что событие протухших кредов было пропущено. Утверждение ровно такое:
у читателя сегодня нет ни одного способа отличить два состояния.

### Проба 6 — `userbot.log` (источник `pc_agent.summarize_log`) + **исправление собственной ошибки**

Первый замер был НЕВЕРЕН и вот почему: `awk -F' \\| '` трактует `|` как альтернативу regex,
поэтому разделителем стал пробел, и «0 совпадений при 55184 строках с ≥4 полями» — артефакт
моего инструмента, а не факт о логе. Верный замер:

```
=== CORRECTED probe: summarize_log pattern on live userbot.log ===
total lines:      57081
lines with ' | ': 1543
message lines (^<ts> | @user|idNNN):  585
lines with >=3 ' | ' separators:      585
```

**Вывод:** шаблон ЖИВ — 585 строк сообщений опознаются. Ошибка сохранена в отчёте намеренно:
это ровно тот же класс, только в измерительном инструменте — «неразбор дал честный на вид нуль».

### Проба 7 — чек-лист ревизора (`_revizor_checklist`, `lesson_router._next_class_letter`)

Сначала я прогнал ПРИДУМАННЫЙ шаблон и получил `0`. Затем взял настоящий из кода
(`_CHECK_CLASS_RE = re.compile(r"^-\s*\[класс\s+(\S+)\]", re.IGNORECASE)`):

```
=== live probe: _CHECK_CLASS_RE against docs/revizor_checklist.md ===
7
--- the matching lines ---
- [класс а]
- [класс б]
- [класс в]
- [класс г]
- [класс д]
- [класс е]
- [класс ж]
--- total non-comment lines starting with '-' ---
8
```

**Вывод:** шаблон ЖИВ (7 из 8). И второй раз за одну сессию «grep → 0» оказался фактом о
грепающем, а не об источнике.

### Проба 8 — реестр отметок старта (`_task_started_read` / `_started_at_raw`)

```
=== task_started.json: newest entries ===
…"391": {"at": "2026-08-07T18:37:53.979130+00:00", "pid": 7396, "proc": "7396-1786096892", "child": 9280}, "403": {"at": "2026-08-08T07:34:08.769406+00:00", "pid": 7396, "proc": "7396-1786096892", "child": 14628}}
=== how many values are dicts (new form) vs strings (old form) ===
7
105
```

**Вывод:** ОБЕ формы значений живы одновременно — 105 старых голых ISO-строк и 7 новых словарей.
Двухформатный читатель `_started_at_raw` нужен по факту, а не «на всякий случай».

### Проба 9 — сборщик улик работы (`_git_commits_between`, `_diff_names`)

```
=== live git log in a 24h window (the evidence collector) ===
3
=== approvals ledger live shape ===
{"task": 89, "decision": "approve", "by": "Filipp", "origin": "agent", "lane": "pc", "reply": "да, безобидная проверка", "kinds": [], "top":
=== watchdog_state.json ===
{"state": "alive", "last_alert": 0.0, "raises": [1786020181.6389165]}
```

**Вывод:** разделитель `\x1f` живой, окно 07–08.08 даёт 3 коммита. Реестр ответов и метка
вотчдога — валидный JSON ожидаемой формы.

### Проба 10 — механическая подпись класса по всей полосе

```
=== silent-swallow sites (except → return []/0/""/None/continue/pass) in non-test modules ===
266
=== count of the (X or "").split*/splitlines() collapse idiom, non-test modules ===
313
```

Первичка честна, потребитель её ломает:
```python
def _git_out(args):
    """git в REPO → stdout.strip() | None (тихо: git недоступен/ошибка — self-update просто молчит)."""
```
```python
def _diff_names(old_commit, new_commit):
    """Файлы, изменённые между двумя коммитами (old..new). git молчит/ошибка/нет диффа → []."""
    out = _git_out(["diff", "--name-only", f"{old_commit}..{new_commit}"])
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
```
`_git_out` честно различает `None` (git не ответил) и `""` (ответил пустым) — и ровно эта
разница уничтожается идиомой `(out or "")` у 313 мест полосы.

### Проба 11 — опровержение чужого вывода (независимый проход ошибся)

Независимый проход утверждал: «`rc_supervisor.py` не импортирует `rc_auth_detect`, значит
детектор test-only». Проверка:

```
118:# протухшие креды — детектором rc_auth_detect, непригодный вход — гейтом doctor.
130:               "max_age": SERVER_LIVENESS_MAX_AGE, "auth_watch": True}
140:    import rc_auth_detect
142:    rc_auth_detect = None
476:    _auth = auth if auth is not None else rc_auth_detect
478:    _auth_on = bool(spec.get("auth_watch") and _auth is not None and getattr(_auth, "ENABLED", True))
504:        auth_state = _auth.new_state() if _auth_on else None
517:                    auth_state = _auth.scan(spec["log"], auth_state)
518:                    if _auth.should_restart(auth_state):
520:                                 spec["label"], _auth.describe(auth_state), getattr(proc, "pid", "?"))
521:                        _notify("♻️ Канал Remote Control: " + _auth.describe(auth_state) +
```

**Вывод:** детектор БОЕВОЙ, зовётся в вечном цикле надзора ветки rc-server каждую проверку.
Находка пробы 5 — живая, а не спящая. (Третий раз за сессию «не нашёл» значило «плохо искал».)

---

## 1. Список мест: источник · различает ли пусто/неразбор · что уходит наверх · живая проба

Область прохода А (моя): читатели ПОСТОЯННОГО живого источника (лог, журнал/реестр, файл
состояния, stdout команды, тело моста), отдающие наверх число/список/статус. Клиентский контур
исключён (заморожен, ниже отдельной строкой).

| # | место | пусто ≠ 0-разобрано | наверх при неразборе | живая проба сегодня |
|---|---|---|---|---|
| 1 | `pc_orchestrator._journal_writes_between` ← `cowork_log.ledger` | **НЕТ** — `FileNotFoundError → []`, битая строка → `continue` молча; логируется только I/O-сбой | `[]` | **ДА** — 500/500 ключи+штамп, self-фильтр 136 → шаблон жив |
| 2 | `pc_orchestrator._git_commits_between` ← `git log` | **НЕТ** — `(out or "")` глотает `None` от `_git_out` | `[]` | **ДА** — окно 07–08.08 → 3 коммита, `\x1f` жив |
| 3 | `pc_orchestrator._work_evidence` + `fail_result` | **НЕТ** — «сборщик упал» и «улик нет» неразличимы | в `result` задачи: «Следов работы в окне … нет (коммитов 0, записей журнала 0)» | **ДА** (складывается из 1+2, обе живы) |
| 4 | `pc_orchestrator._revizor_read_state` → `_revizor_tick_label` | **НЕТ** — нет файла / битый json / нечитаем → один `{}` | «надзор: ревизор — **тиков ещё не было**» (та же фраза, что в серверном инциденте) | **ДА** — файл жив, `last_run` 2026-08-08T06:54 |
| 5 | `pc_orchestrator._revizor_db_rows` ← sqlite `drafts` | частично — есть `log.warning`, наверх `[]` | `[]` | нет — sqlite красное, не трогал |
| 6 | `revizor_tick` (лог-строка) | **ДА** — печатает «всего строк drafts %d» рядом с числом окон | — | образец |
| 7 | `pc_orchestrator._revizor_checklist` ← `docs/revizor_checklist.md` | **НЕТ** — ошибка чтения и пустое тело → один `DEFAULT` | `REVIZOR_CHECKLIST_DEFAULT` | **ДА** — файл жив (8155 б) |
| 8 | `pc_orchestrator._task_started_read` | **ДА** — `FileNotFoundError` отдельной веткой, прочее → warning | `{}` | **ДА** — 105 строковых + 7 dict-значений сосуществуют |
| 9 | `pc_orchestrator._diff_names` → `_selfupdate_restart_children` | **НЕТ** — докстринг дословно: «git молчит/ошибка/нет диффа → []» | `''` = «никого не рестартили» | **ДА** — git отвечает |
| 10 | `pc_orchestrator._count_claude_procs` | **ДА** — `None` ≠ `0`, fail-open + warning | `None` | образец |
| 11 | `pc_orchestrator._find_pids_by_script` | **ДА** — ТРИ исхода: `[pids]` / `[]` / `None` | `None` | эталон (закрытый класс #171) |
| 12 | `pc_orchestrator._pid_alive_probe` ← `tasklist` | **ДА** — `None` при rc≠0 | `None` | образец |
| 13 | `pc_orchestrator._dirty_tracked` | **ДА** — «не знаю» ≠ «чисто» | `None` | образец |
| 14 | `pc_orchestrator._changed_files_since` | **НЕТ** — 4 причины сводятся в одну | `[]` | — |
| 15 | `pc_orchestrator._answer_record` ← `approvals.jsonl` | **ДА** — счётчик `dropped` в лог | `True/False` | **ДА** — первая строка валидна |
| 16 | `pc_orchestrator._git_out` | **ДА** — `None` ≠ `""` | `None` | образец, **разрушаемый потребителями** |
| 17 | `rc_auth_detect.read_new` | частично — флаг `truncated` есть, но сбой stat/read → `[], offset, False` | `[]` | **ДА** |
| 18 | `rc_auth_detect.feed`/`should_restart`/`describe` ← `rc_server_debug.log` | **НЕТ** — `seen` в state есть, наверх его не читает НИКТО | `failures = 0` → «авторизация цела» | **ДА — 0 совпадений всех 4 маркеров на 292 живых строках** |
| 19 | `rc_supervisor.established_conns` ← `netstat` | **НЕТ** — исключение → `0` | `0` | — |
| 20 | `rc_supervisor.log_age` | **ДА** — `None` ≠ `0` | `None` | образец |
| 21 | `rc_supervisor.rc_ready` ← `claude doctor` | **ДА** — два разных `True` с разным detail | — | образец |
| 22 | `pc_agent.summarize_log` ← `userbot.log` | **НЕТ** — нет файла и 0 разобрано дают одинаковое «сообщений: 0» (владельцу в телефон) | `msgs=0, senders=0, «(пусто)»` | **ДА** — 585 из 57081 строк опознаны |
| 23 | `pc_agent._find_userbot_pids` / `_find_moderbot_pids` ← CIM | **НЕТ** — исключение → `[]` = «процесс мёртв» | `[]` | — (на том же ПК п.11 этот класс уже закрыл третьим исходом) |
| 24 | `pc_agent._read_watch_snapshot` + `_supervision_label` | **ДА** — «демон молчит» / «снимок протух» / «нет в снимке» — три разные фразы | — | образец |
| 25 | `session_watch.live_pids` | **ДА** — `ok`-флаг | `(False, set())` | образец |
| 26 | `session_watch.notify` ← stdout ребёнка | **ДА** — `"unknown"` ≠ `"none"` | `("unknown", False)` | образец |
| 27 | `session_watch.log_has_activity` | **НЕТ** — нет пути / файл нечитаем / маркера нет → один `False` (докстринг это признаёт) | `False` | — |
| 28 | `session_watch.read_sessions` / `load_state` | **НЕТ** — недописанный JSON → `continue` молча | `[]` / `{"signalled": {}}` | — |
| 29 | `diag_status_truth.read_daemon_log` ← `pc_orchestrator.log` | **НЕТ** — несовпавшая строка → `continue` | `({}, {}, set())` | **ДА** — 5736/5752 (99.7%), шаблон жив |
| 30 | `diag_status_truth.read_journal` ← `tmp/journal_slice.txt` | **НЕТ** | `[]` | **ДА — срез от 30.07, 9 суток при окне 48 ч** |
| 31 | `diag_status_truth.fetch_slice` ← мозг | **НЕТ** — `len(kept)=0` и при пустом доке, и при 0 совпавших | `0` | косвенно (формат строки жив, п.1) |
| 32 | `diag_status_truth.read_commits` ← `git log` | **НЕТ** — `(p.stdout or "")`: git не запустился ≡ коммитов нет | `[]` | — |
| 33 | `diag_status_truth.main` | частично — печатает «строк журнала: N» рядом | «ЦЕНА КЛАССА: **0 из 0** разобранных провалов (**0%**)» | **ДА** (через 29+30) |
| 34 | `journal_measure.snapshot` / `entries` ← `tmp/*.snapshot.txt` | **НЕТ** — кэш предпочитается молча | `[]` | **ДА — снимки от 31.07, 8 суток** |
| 35 | `journal_measure.entry_date` (забор `_MIN_D.._MAX_D`) | **НЕТ** | `None` | **ДА — забор истекает 2026-08-31, сегодня 08.08: 23 дня до молчаливого обнуления всех дат** |
| 36 | `journal_measure.main` | **ДА** — «ни одной записи с распознанной датой — нечего мерить», `exit 1` | — | образец |
| 37 | `lesson_router._next_class_letter` ← `docs/revizor_checklist.md` | **НЕТ** — «все буквы заняты» и «не распарсил» дают один `''` | `''` | **ДА** — 7 из 8 строк, шаблон жив |
| 38 | `cowork_log_append.stamp_line` | **НЕТ** по устройству — неопознанный тип становится `DONE` | `DONE` | известно и записано в CLAUDE.md |
| 39 | `dispatch_notify._summary_from_transcript` / `_last_tool_command` | **НЕТ** — сбой файла и «блока нет» → `""` | `""` | **нет — источник `~/.claude/projects` ВНЕ проекта, пробу не делал (класс outside)** |
| 40 | `dispatch_notify._session_facts` | **ДА** — флаг `seen_usage` | — | образец |
| 41 | `gate_selective.decide` | **ДА** — `full:failsafe` ≠ `off` | режим строкой | образец |
| 42 | `card_duty._kind` / `_obj_index` / `facts` | частично — только `has_card_shape` | `""` / `-1` | — (ветка пустого объекта на ПК мертва с 02.08) |
| 43 | `task_metrics.extract_tokens` / `_sum_fields` | **ДА** — флаг `seen`, `None` ≠ `0` | `(None, None)` | образец |
| 44 | `task_metrics.selfheal_count` | **НЕТ** — нет маркера ≡ маркер со значением 0 | `0` | — |
| 45 | `bridge_http.request_json` | **ДА** — исключение + число символов; `BridgeReceiptLost` отдельным типом | raise | эталон |
| 46 | `brain_writer` (якорь + обратное чтение) | **ДА** — raise с числом вхождений | raise | эталон |
| 47 | `trainer_run.load_cases` | **ДА** — `raise ValueError("кейсов нет")` | raise | самый громкий загрузчик полосы |
| 48 | `pretool_guard.kinds_from_card` / `object_from_card` | **НЕТ** — сознательно, fail-closed (нет разбора → нет пропуска) | `frozenset()` / `""` | безопасное направление |

**Клиентский контур — строкой, в работу не брался (заморожен).** `suggest._park_bike_names_status`
различает три состояния источника (`PARK_EMPTY` / `PARK_NONE` / `PARK_UNCONFIGURED`) и является
ЛУЧШИМ образцом на полосе; `suggest.remove_playbook_rule` даёт пять статусов, включая `empty` ≠
`not_found`; `suggest._smoke_checks` явно разводит «quote-блок не собран» и «не найдено дословно».
Слепые там же: `load_playbook`/`_playbook_learned_rules`/`find_playbook_conflict`/
`list_playbook_rules` → `""`/`[]`; `pricing._normalize` и `delivery._extract_zones` дают один
`None` на «не-ok» и на «ok, но чисел/списка нет»; `_cli_llm` → `""` и на таймаут, и на rc≠0.

---

## 2. Числа: два независимых прохода

| | проход А (ручной, по постоянным источникам, ядро полосы, клиентский контур исключён) | проход Б (независимый механический свип по всем местам разбора, включая парсеры ответов LLM и замороженный клиентский контур) |
|---|---|---|
| **всего мест** | **48** | **182** |
| **слепых** | **24** (плюс 4 частичных, 20 образцов) | **~102** |
| **проверено живой пробой** | **16** | **0** (проход был чисто кодовым) |

**Числа НЕ сошлись, и это не ошибка счёта — это разная область и разная зернистость.**
Проход А считает МЕСТО (читатель одного постоянного источника), проход Б — ТОЧКУ РАЗБОРА
(каждый `re`/`json.loads`/`split`), и вдобавок берёт парсеры одноразовых ответов модели и
клиентский контур. Доля слепых при этом совпала почти в точности: **24/48 = 50.0 %** против
**102/182 = 56.0 %** — то есть в какой бы зернистости ни считать, слепа примерно половина мест.
Это и есть устойчивое число.

Все 16 живых проб — из прохода А; проход Б живых файлов не трогал. Проход Б дал ОДНО ложное
утверждение (`rc_auth_detect` якобы test-only), опровергнутое пробой 11; проход А дал ДВЕ
собственные ошибки шаблона (пробы 6 и 7), обе исправлены и оставлены в отчёте.

Механическая подпись класса по полосе: **266** мест `except → return []/0/""/None/continue`
и **313** применений идиомы `(X or "").splitlines()/.split()`. Второе число важнее первого:
именно оно уничтожает уже существующее различение (`_git_out` честно отдаёт `None`, а
потребитель превращает его в пустоту).

---

## 3. Правило, закрывающее весь список разом

> **Читатель живого текста обязан поднимать наверх ДВА числа — сколько единиц источника прочитано
> и сколько из них разобрано; «разобрано 0 при прочитано >0» — это отказ читателя, а не молчание
> источника, и ни один потребитель не вправе сводить «не знаю» (`None`, исключение) к пустоте
> идиомой `(x or "")` / `except: return []`.**

Правило только названо. Не применялось: правок кода в этой сессии нет.

---

## 4. Остатки (в работу не брались)

- **`tmp_selfupdate_deps_20260807_q3/red_probe/`** — untracked копия репо в рабочем дереве.
  `grep -rn` по ней выдаёт вторые экземпляры `rc_auth_detect.py`, `rc_supervisor.py`,
  `test_pretool_guard.py`. Новая мина того же рода, что `manager-bot/` и
  `docs/artifacts/2026-07-23-guard-v3-files/` — просится строкой в таблицу мин в CLAUDE.md.
  Каталог оставлен на месте, ничего не удалялось.
- Точки касания второй полосы (класс-фикс полагается на обе, здесь НЕ делался): `task_metrics.py`
  копируется байт-в-байт в оба репо; `_git_out`/`_diff_names` и «нуль улик работы» имеют
  зеркала в серверном оркестраторе.
