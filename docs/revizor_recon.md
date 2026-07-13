# revizor_recon — РАЗВЕДКА (шаг 1/7, родитель 262)

Read-only разведка ПК-контура под задачу «ревизор». Кода не менял. Все пути —
`file:line` на HEAD от 2026-07-13. Три оси: (A) стор реплея/транскриптов
клиентских окон и связь окно→черновик→отправленное; (B) `_thinker_exec` и
enqueue «зелёных» задач дирижёру; (C) карточки инбокса 1160.

---

## A. СТОР КЛИЕНТСКИХ ОКОН: транскрипт → черновик → отправленное

### A.1 Единая таблица `drafts` в SQLite `moderation_ipc.db`

Источник правды по клиентским окнам на ПК — **одна таблица `drafts`** в
`moderation_ipc.db`. И черновик (до модерации), и одобренный отправленный текст,
и транскрипт окна лежат в одной строке. Схема — `moderation_ipc.py:91`:

```
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER, client_ref TEXT, lang TEXT, incoming TEXT,
    draft TEXT, first_contact INTEGER,
    status TEXT, card_msg_id INTEGER, final_text TEXT,
    decided_by TEXT, reason TEXT, created_ts TEXT, updated_ts TEXT,
    transcript TEXT, pricing_note TEXT, directive TEXT, client_name TEXT
)
```

Поля (по смыслу):
- `client_id` (INTEGER) — **peer_id клиента в Telegram = идентификатор окна**;
- `client_ref`, `client_name` — ссылка/имя клиента;
- `incoming` — последняя входящая фраза клиента;
- `transcript` — **транскрипт окна** в формате `"[роль]: текст"` (см. A.4), нужен
  для перегенерации/стратегии;
- `draft` — **ЧЕРНОВИК** от `suggest.generate_draft()` (текст ДО модерации);
- `pricing_note` — прайс-нота (стратег-данные для черновика);
- `first_contact` (INTEGER) — флаг первого контакта;
- `status` — стадия жизненного цикла (см. A.3);
- `card_msg_id` (INTEGER) — id сообщения карточки черновика в группе модерации;
- `final_text` — **ОТПРАВЛЕННЫЙ клиенту одобренный текст** (после модерации);
- `directive` — формулировка правки модератора (для «запомнить как правило»);
- `decided_by` — юзернейм модератора, принявшего решение;
- `reason` — диагноз при rejected/failed;
- `created_ts` / `updated_ts` — таймстемпы.

### A.2 Ключи связи окно → черновик → отправленное

- **Окно → черновики:** ключ `client_id`. Одно окно (диалог одного клиента) может
  иметь несколько строк `drafts` (по одной на реплику/вопрос).
  `SELECT * FROM drafts WHERE client_id=?`.
- **Черновик → карточка модерации:** `id` (draft_id, PK) ↔ `card_msg_id`
  (сообщение карточки в группе модерации). Обратный поиск —
  `draft_by_card(card_msg_id)` (`moderation_ipc.py:218`).
- **Черновик → отправленное:** в той же строке `draft` (черновик) и `final_text`
  (одобренный отправленный), связаны через `status`.

### A.3 Жизненный цикл строки (`status`) и функции чтения/записи

Все — в `moderation_ipc.py`:

| Функция | line | Что делает | status |
|---|---|---|---|
| `enqueue_draft(rec)` | 136 | userbot/suggest кладёт черновик (INSERT) | `new` |
| `fetch_new()` | 203 | SELECT новых для постинга ботом | читает `new` |
| `mark_posted(draft_id, card_msg_id)` | 223 | бот запостил карточку черновика | `posted` |
| `set_candidate(draft_id, final_text, directive)` | 229 | применена правка модератора, ждёт подтверждения | `pending_confirm` |
| `set_decision(draft_id, status, final_text, decided_by, reason)` | 238 | решение принято | `ready`/`test_held`/`rejected` |
| `fetch_ready()` | 208 | SELECT одобренных для отправки клиенту | читает `ready` |
| `mark(draft_id, status, reason)` | 248 | пометить итог отправки | `sent`/`failed` |
| `get(draft_id)` | 213 | SELECT по id | — |
| `draft_by_card(card_msg_id)` | 218 | SELECT по card_msg_id | — |

Итоговый путь статусов:
`new → posted → (pending_confirm) → ready → sent` (или `rejected`/`failed`;
в TEST_MODE вместо `ready` — `test_held`: решение принято, отправка заблокирована).

**Отправленный клиенту текст** = `final_text` при `status IN (ready, sent, test_held)`.
**Черновик** = `draft` при любом статусе.

### A.4 Кто с таблицей работает (продьюсеры/консьюмеры)

- **suggest.py** (генератор черновиков):
  - `suggest.py:3125` — `moderation_ipc.enqueue_draft(rec)` кладёт черновик;
  - `suggest.py:2954` — `async def poll_and_send(...)` — цикл отправки клиенту:
    `suggest.py:2963` `moderation_ipc.fetch_ready()` → берёт `final_text` →
    отправляет → `mark(draft_id, sent|failed)`.
- **moderation_bot.py** (карточки модерации): `mark_posted` (~:119),
  `set_candidate` (~:144), `set_decision` (~:156).

### A.5 «Реплей» окна

Два разных смысла слова «replay» в репо:
- **`live_replay_1207.py`** — это **тест-харнесс живого прогона**, не прод-стор.
  Прогоняет ДОСЛОВНЫЕ фразы реального окна 12.07 через настоящий `suggest`
  (реальный `claude` CLI, Bridge замокан стаб-getter'ом парка) и проверяет гарды
  черновика (XMAX двумя строками; нет утверждений о цвете/наличии; нет
  переспросов уже собранного). Транскрипт задаётся строкой формата `"[роль]: текст"`
  (`live_replay_1207.py:69-76, 95-100`), гоняется через
  `suggest.generate_draft(transcript, lang, faq, ...)`. Формат транскрипта тот же,
  что колонка `drafts.transcript`.
- **Лог обучения** `suggest_pairs.jsonl` (`suggest.py:121` `PAIRS_FILE`;
  запись — `suggest.py:2849`): пары «входящее → черновик → отправленное/правка».
  Восстанавливаемая история окна помимо БД.

### A.6 `client_watch.json` — это НЕ стор окон

`pc_orchestrator.client_watch.json` — снимок **вотчдога** живости дочерних
процессов (pc_agent/userbot/moderation_bot), не транскрипт-стор. Формат:
`{ts, cooldown, max_deaths, children:{<proc>:{deaths, halted, last_raise}}}`.
К клиентским окнам отношения не имеет — не путать.

---

## B. `_thinker_exec` и enqueue задач дирижёру (pc_orchestrator.py)

### B.1 `_thinker_exec` — «думатель» (headless-генератор)

- **Определение:** `pc_orchestrator.py:999` — `def _thinker_exec(prompt, timeout, tag):`
- **Суть:** запускает `claude -p` как **чистый генератор без инструментов** —
  диагностика/план/вердикт, НИЧЕГО не исполняет.
- **Команда** (`pc_orchestrator.py:1015`):
  ```
  claude -p <prompt> --model THINKER_MODEL --output-format json
         --max-turns 1 --allowed-tools ""  [--fallback-model THINKER_FALLBACK]
  ```
- **Изоляция:** cwd = `tempfile.gettempdir()` (не репо → не читает
  `.claude/settings.json`/`pretool_guard`); `ANTHROPIC_API_KEY` вычищен из env
  (идёт по подписке Max).
- **Модели:** `THINKER_MODEL = claude-fable-5` (:909),
  `THINKER_FALLBACK = claude-opus-4-8` (:910) — ПОЛНЫЕ id (иначе 404, см.
  memory claude-cli-model-aliases).
- **Возврат:** текст ответа (распакован из JSON-конверта `env_j["result"]`,
  ~:1031) **или `None`** при любом сбое (запуск/таймаут/exit≠0/claude не найден)
  → fail-safe, upstream оставляет прежнее поведение.
- **Таймауты по сценарию:** `STEP_SELFHEAL_TIMEOUT=180` (:894),
  `PC_DEC_PLAN_TIMEOUT=600` (:1148), `PLAN_ADAPT_TIMEOUT=180` (:1163).

Четыре сценария вызова (по `tag`):
1. **план** (`"pcloc-dec-plan"`): `PLANNER_PREAMBLE + ТЗ` → нумерованный список шагов;
2. **самопочинка шага** (`"pcloc-selfheal"`): цель+план+упавший шаг+ошибка →
   `{"verdict":"retry|halt","fixed_step":...,"reason":...}`;
3. **самопочинка одиночки** (`"task-selfheal"`): текст задачи+ошибка → то же, ключ
   `fixed_task`;
4. **адаптация плана** (`"pcloc-plan-adapt"`): цель+план+результаты done →
   `{"verdict":"keep|adjust|finish","adjusted_steps":[...],"reason":...}`.

### B.2 Очередь дирижёра — на Bridge (HTTP), lane=`pc`

Очередь **не в памяти и не в sqlite демона** — источник правды на внешнем Bridge
(HTTP API). Клиент — `BridgeClient` (`pc_orchestrator.py:153`), `BRIDGE_URL`/
`BRIDGE_TOKEN` из env. Все задачи ПК-дирижёра жёстко в `LANE = "pc"` (:57).

Элемент очереди (dict из `get_pending`):
`{id:int, status:str, from:str, task_text:str, result:str}`.
Статусы (`_LOC_STATUSES`, :1355):
`("new","in_progress","needs_approval","approved","done","failed")`.

API очереди (`BridgeClient`):
- `get_pending(status, lane=LANE)` (:177) — снять задачи статуса;
- `claim_task(tid)` (:180) — забрать в исполнение (`new → in_progress`);
- `complete_task(tid, status, result)` (:183) — завершить (`done`/`failed`);
- `set_needs_approval(tid, what, topic)` (:186) — пометить красное (см. C);
- `enqueue_task(frm, text, lane)` (:193) — поставить задачу.

Enqueue-обёртки дирижёра:
- `enqueue_pc_task(text, frm="Filipp", bridge=None)` (:688) — прямой enqueue
  lane=pc (synthetic-задачи цепи прямым каналом: enqueue→claim→complete done);
- `_loc_enqueue(text)` (:1364) — дирижёр ставит служебную задачу цепи;
- `_loc_release(pid, j, total, text, k=0)` (:1542) — релиз следующего шага цепи:
  enqueue `"[шаг j/total родитель pid] [коррекция плана k] <text>"`.

### B.3 «Зелёные» задачи и поток данных

«Зелёная» задача = проходит headless без ручного одобрения (в отличие от
«красной» → `needs_approval` → кнопка, см. C). Термин «green» в коде дословно не
используется; зелёное — это обычный путь `new → in_progress → done`. Красное
изымается детектом `_detect_needs_approval` ДО исполнения.

Маркеры цепи (restart-proof, состояние цепи целиком восстанавливается из очереди):
- `_STEP_RE = ^\[шаг (\d+)/(\d+) родитель (\d+)\]` (:1151);
- `_SUM_RE`, `_ADAPT_CARD_RE`, `_CARD_RE`, `_HEAL_RE` (:1153–1157) — служебные
  synthetic-задачи (сводка/коррекция/карточка/самопочинка).

Поток план→очередь:
```
РОДИТЕЛЬ from=Filipp-pcloc-dec  → process_new → _local_dec_plan
  → _thinker_exec(PLANNER_PREAMBLE+ТЗ, "pcloc-dec-plan")   [план]
  → _plan_steps(out) → _loc_release(tid, 1, N, steps[0])
  → _loc_enqueue("[шаг 1/N родитель pid] …") → enqueue_pc_task → Bridge (lane=pc, new)
```
Надзор цепи (`process_local_chains` → `_loc_chain_tick`, sequential — в очереди
максимум ОДИН активный шаг цепи):
- `last_step=done` → `_loc_after_done` → (PLAN_ADAPT=1) `_thinker_exec` ADAPT →
  keep=релиз следующего / adjust=новые шаги через `_loc_release` / finish=карточка;
- `last_step=failed` → `_loc_after_fail` → (STEP_SELFHEAL=1) `_thinker_exec`
  SELFHEAL → retry=перерождение шага через `_loc_release` / halt=карточка+сводка.

Демон — единственный исполнитель: `claim_task → run_task/_local_dec_plan →
complete_task` каждый цикл. Состояние в памяти демона — только дедуп-кэши
(`_loc_summarized` :1356, `_loc_adapt_finish` :1358, `_loc_adapted` :1360).

---

## C. Карточки инбокса 1160 (NEEDS_APPROVAL)

### C.1 Что такое 1160

**1160 — Telegram topic_id единого инбокса одобрений** (обе полосы vps+pc).
Задаётся на VPS переменной `INBOX_TOPIC_ID` (прод=1160). Если задана и ≠0 —
devbot постит ВСЕ карточки одобрений в тему 1160; если не задана/0 — старые темы
vps→328, pc→829 (`docs/dec_port_spec.md:95`).

На ПК-стороне: `pc_orchestrator.py:68`
`NEEDS_APPROVAL_TOPIC = int(os.getenv("PC_NA_TOPIC", "829") or "829")` —
локальный дефолт 829, переопределяется `PC_NA_TOPIC` в .env (в проде = 1160).

### C.2 Механика: детект красного → карточка

1. Headless `claude` в `run_task` выводит красное одним из трёх способов:
   - stdout-маркер `NEEDS_APPROVAL: <что·куда·последствия>` (`NA_MARKER` :103);
   - файл-маркер гарда: `pretool_guard.py` форсит ask, штампуя токеном
     `<token>\x1f<текст>` (`MARKER_SEP="\x1f"` :106);
   - фолбэк-фразы («требует подтверждения», «нет прав на approve» …).
2. `_detect_needs_approval(out, marker_content, run_token)`
   (`pc_orchestrator.py:249`) парсит красное; `run_token` фильтрует чужие/старые
   карточки (страховка от «призрака»). Возврат — дескриптор `what` (≤`RESULT_MAX=4500`).
3. Демон НЕ исполняет → `set_needs_approval(tid, what, topic=NEEDS_APPROVAL_TOPIC)`
   (`pc_orchestrator.py:186`) → задача уходит в `needs_approval`.
4. Карточку в тему 1160 несёт **devbot** (VPS, фон-job `report_results`, ~45с) с
   кнопками ✅/❌ (+ «да N»/«нет N»). Approve живёт `APPROVED_TTL=1800с` (30 мин).

### C.3 Формат карточки

Текст карточки (для гардовых красных — `pretool_guard.py:_card`, ~:305):
```
🔴 <действие + объект> — разрешить?
Команда: <сырая команда>        (опционально, для прозрачности)
```
Для красного в цепи — дескриптор вида `NEEDS_APPROVAL: op=other | <что·куда·последствия>`.
Особый случай: конверт одобренной заявки снова упёрся в красное → терминальный
`failed` с ручной картой «✋ ТРЕБУЕТСЯ РУЧНОЕ ДЕЙСТВИЕ» БЕЗ approve-кнопки
(разрыв петли ре-конвертов).

### C.4 Связь карточки с задачей/окном и продолжение цепи

- Ключ карточки = `tid` (id задачи/шага в очереди Bridge), статус `needs_approval`,
  `what` = текст карточки, `topic` = 1160.
- **Sequential-цепь ждёт «да»:** тик pc-цепи при `needs_approval` → `return`
  (карточка уже в инбоксе), следующий шаг не релизится.
- **Approve** → devbot ставит `approved` → `process_approved` (:627) ре-ран с
  нотой «[ОДОБРЕНО ЧЕЛОВЕКОМ]» → `done` → тик релизит следующий шаг.
- **Reject** («нет N»/❌) → devbot финализирует `failed «отклонено Филиппом»` →
  следующий new-шаг видит failed-сиблинга → сам `failed «⏭ пропущен…»` + сводка;
  на pc отказ гасит думателя самопочинки (префикс-гейт).
- Не путать с `drafts.card_msg_id` — это отдельная карточка **клиентского черновика**
  в группе модерации (ось A), другой контур.

---

## Сводка топологии

```
Клиентское окно (client_id=peer_id)
  └─ moderation_ipc.db :: drafts (одна строка на реплику)
       transcript ─ draft ─ pricing_note ─ status ─ card_msg_id ─ final_text
       new → posted → [pending_confirm] → ready → sent   (реплей: suggest_pairs.jsonl)

Дирижёр (pc_orchestrator.py)
  Bridge queue lane=pc {id,status,from,task_text,result}
  _thinker_exec (claude -p, генератор) → план/самопочинка/адаптация
  enqueue_pc_task / _loc_enqueue / _loc_release → шаги "[шаг i/N родитель pid]"
  красное → set_needs_approval(tid, what, topic=1160) → карточка devbot ✅/❌
```
