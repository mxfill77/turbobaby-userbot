# Перепись: чем СЕЙЧАС являются тренажёр и moderbot (read-only)

**Дата замера:** 2026-08-11, ~04:25–04:35 (локальное).
**HEAD на замере:** `e265f98c31ba2393d8a20dfcf5f1a712e0adb4a4`, ветка `main`.
**Основание:** владелец рассматривает объединение тренажёра и moderbot в один инструмент
отладки. Решения нет. Здесь только факты, ничего не проектируется.
**Запуски:** тренажёр и moderbot НЕ запускались, код не менялся, коммита нет, гейт не гонялся,
в группы и диалоги не писалось, БД не читалась (только mtime), временных файлов не создавалось —
единственный созданный файл этот артефакт.

---

## 0. Главная поправка к постановке: «тренажёр» — это ДВЕ разные вещи

| | А. Группа-тренажёр | Б. Безголовый прогон |
|---|---|---|
| код | `trainer.py` (696) + `trainer_log.py` (457) | `trainer_run.py` (590) |
| процесс | СВОЕГО НЕТ — библиотека внутри userbot и moderbot | отдельный CLI, руками |
| выход | сообщение в группу | машинный вердикт-файл + код возврата |
| корпус | нет (что владелец наберёт) | 12 кейсов в `trainer_cases.json` |
| вердикт | нет | есть, его и читают ворота |

Дальше А и Б разбираются раздельно: слить их в один разговор — значит потерять, что вердикт
отдаёт только Б, а живёт в боевых ботах только А.

---

## 1. Где живёт код, чем запускается, жив ли

### Инвентарь (дословный вывод)

```
$ for f in …; do … git ls-files --error-unmatch … ; wc -l ; stat -c '%y' ; done
trainer.py               TRACKED   696 lines  mtime=2026-07-23 13:03:13.954128900 +0700
trainer_run.py           TRACKED   590 lines  mtime=2026-07-30 23:11:35.144621300 +0700
trainer_log.py           TRACKED   457 lines  mtime=2026-07-23 12:58:55.383415300 +0700
trainer_cases.json       TRACKED   114 lines  mtime=2026-07-31 19:30:40.015914300 +0700
trainer_rules.json       UNTRACKED 7 lines  mtime=2026-08-10 01:13:44.207474800 +0700
trainer_log_doc.json     TRACKED   5 lines  mtime=2026-07-23 13:01:05.112705500 +0700
moderation_bot.py        TRACKED   661 lines  mtime=2026-07-23 13:05:28.337831000 +0700
moderation_core.py       TRACKED   499 lines  mtime=2026-07-16 03:17:48.237546600 +0700
moderation_ipc.py        TRACKED   293 lines  mtime=2026-07-15 12:46:58.887506800 +0700
```

```
$ for f in …; do git log -1 --format='%h %ad %s' --date=short -- "$f"; done
=== trainer.py ===
758f1de 2026-07-23 полнота лога тренажёра: ретрай+спул TRN-строк, все ветки обучения в лог, №№ текстовым урокам, fail-safe apply/cancel, archive_id, провал коммита урока не блокирует авто-фетч
=== trainer_run.py ===
6478a39 2026-07-30 тренажёр отдаёт вердикт: второе основание ворот клиентского контура заведено
=== trainer_log.py ===
758f1de 2026-07-23 полнота лога тренажёра: …
=== trainer_cases.json ===
55aad22 2026-07-31 класс-фикс трёх находок по клиентским черновикам: год выпуска не уходит клиенту, два проверяльщика перестали красить выполненное правило
=== moderation_bot.py ===
758f1de 2026-07-23 полнота лога тренажёра: …
=== moderation_core.py ===
34492c9 2026-07-16 #112 шаг5/8: /rules — показ книги с номерами + удаление правила репликой (номер|текст) с подтверждением
=== moderation_ipc.py ===
26bcddf 2026-07-15 блок команды в конвейере: реестр внутренних аккаунтов КОДОМ до LLM (инцидент @Pleummmm 15.07 11:23)
```

### Живые процессы (дословный вывод)

```
$ Get-CimInstance Win32_Process -Filter "Name like '%python%'" | … | Format-List
ProcessId    : 1432   CreationDate : 06.08.2026 19:38:27  cmd : "…\venv\Scripts\python.exe" pc_agent.py
ProcessId    : 1772   CreationDate : 06.08.2026 19:38:33  cmd : "…\python.exe"  "D:\turbobaby-bot\rc_supervisor.py"
ProcessId    : 7828   CreationDate : 06.08.2026 19:38:42  cmd : …\python.exe D:\turbobaby-bot\userbot_listen.py
ProcessId    : 11968  CreationDate : 06.08.2026 19:43:59  cmd : …\python.exe D:\turbobaby-bot\moderation_bot.py
ProcessId    : 2624   CreationDate : 10.08.2026 1:16:04   cmd : …\python.exe D:\turbobaby-bot\pc_orchestrator.py
```

```
$ cat moderation_bot.lock → 11968
$ cat userbot.lock        → 7828
```

### А. Группа-тренажёр — ЗАГРУЖЕНА, НО ПРОСТАИВАЕТ

Своего процесса нет. Импортируется двумя живыми ботами:
`userbot_listen.py:44-45` (`import trainer` / `import trainer_log`),
`moderation_bot.py:36-37` (то же). Оба хозяина живы с **06.08.2026 19:38:42 / 19:43:59**,
PID совпадают с локами.

Дата последнего ФАКТИЧЕСКОГО прогона группы: **следов нет ни одного**.

```
$ head -1 userbot.log
2026-06-09T15:17:35.466750+00:00 | --- userbot_listen ЗАПУСК (ЭТАП C: слушаю, НЕ отвечаю) ---
$ grep -c 'ТЕСТ-\|тренаж\|trainer\|TRN ' userbot.log
0
$ grep -c 'ТЕСТ-\|trainer\|TRN ' logs/userbot_stderr.log
0
$ grep -c '^2026-07' userbot.log   → 1169
$ grep -c '^2026-08' userbot.log   → 130
```

Почему «grep → 0» здесь ЗАКОННОЕ доказательство (правило CLAUDE.md о минах требует назвать
дерево и канал):
* `userbot_listen.py:71-75` — `logging.basicConfig(level=logging.INFO, handlers=[<userbot.log>,
  StreamHandler()])` вешается на КОРНЕВОЙ логгер, поэтому `logging.getLogger("trainer")` и
  `getLogger("trainer_log")` (trainer.py:30, trainer_log.py:99) попадают и в `userbot.log`, и в
  `logs/userbot_stderr.log`. Обе полосы дали ноль.
* Каждое событие группы обязано пройти `_trn_log` (`userbot_listen.py:267-279`) →
  `trainer_log.safe_append`, а тот на успехе пишет `log.info("trainer_log: доставлено ok…")`
  (`trainer_log.py:443,445`). Значит молчаливого прогона быть не может — начиная с 23.07,
  когда `trainer_log` был заведён (758f1de).
* Окно лога непрерывное: `userbot.log` начинается **2026-06-09**, ротированных архивов рядом нет
  (`ls userbot.log*` → один файл), т.е. голова не срезалась.

Единственные упоминания тренажёра в логе модербота — не прогон, а отказ маршрутизации:

```
$ grep -c 'тренаж\|trainer\|ТРЕНАЖ' moderation_bot.log → 4
6746:2026-07-22 14:52:05,868 | mod_chat=-5193185299 указывает на группу ТРЕНАЖЁРА — боевые карточки туда НЕ шлю (белый список назначения); жду перепривязки mod_chat.
6747:2026-07-22 14:52:05,919 | … (то же)
6750:2026-07-22 14:52:16,879 | … (то же)
6751:2026-07-22 14:52:16,943 | … (то же)
```

**Итог по А: код живёт в двух живых процессах, но группа не использовалась. Последний след
группы в любом логе — 2026-07-22 14:52, и это отказ `_target_chat`, а не прогон.**

Отдельно про `trainer_rules.json` mtime **2026-08-10 01:13:44** — это НЕ живой урок, это след
теста. Файл пишут ровно две функции (`trainer.py:279 mark_source`, `:302 unmark_source`), и
`test_trainer.py:646` зовёт `trainer.apply_lessons(["уже было"], append_rule=lambda r:
"duplicate", classify=lambda r: "behavior", list_rules=lambda: [])` **без `mark=`**; при
`status == "duplicate"` `trainer.py:633` делает `(mark or mark_source)(remark)` → пишет БОЕВОЙ
сайдкар. Ключ `"уже было"` лежит в файле дословно — это фикстура теста, а не правило владельца.
`test_trainer.py:316-322` переводит путь в temp только для класса `TestLessonAppliesAndCancels`,
`TestMultiSelectLessons` не защищён. (Констатация, не правка: код не менялся.)

### Б. Безголовый прогон — МЁРТВ С 31.07, НИЧЕМ НЕ ЗАПУСКАЕТСЯ

`trainer_run.py` — CLI, запускается ТОЛЬКО руками
(`venv/Scripts/python.exe trainer_run.py`, docstring :51-53). Планировщика, демона или хука,
который его зовёт, в репозитории нет:

```
$ grep -rn "trainer_run" --include=*.py --include=*.md --include=*.json --include=*.xml . \
    | grep -v manager-bot | grep -v '^./tmp' | grep -v '^./test_' | grep -v docs/artifacts
./client_contour.py:311:TRAINER_RUNNER = "trainer_run.py"
./client_contour.py:378:    Канал ровно тот, что был заложен интерфейсом (a8f8822): безголовый раннер `trainer_run.py`
./nonparse_baseline.json:147:    "trainer_run.py": {
./pc_orchestrator.client_trainer_green.json:15,35,56:   "runner": "trainer_run.py",
./pc_orchestrator.py:5370: # комментарий
./pc_orchestrator.py:5441: # комментарий
./trainer_cases.json:3: "about": …
./trainer_run.py: (сам себя)
```

Все упоминания — строковые константы, комментарии и записи вердикта. **Вызова нет ни одного.**

Дата последнего запуска — **2026-07-31 19:40:01** (поле `last.when` вердикт-файла; mtime файла
`Jul 31 19:40` совпадает). Предыдущий — 2026-07-30 23:21:56. **Оба КРАСНЫЕ.**

### Модербот — ЖИВ

Процесс PID 11968 с 06.08.2026 19:43:59, лок совпадает. Последняя строка `moderation_bot.log` —
`2026-08-10 10:43:41` (пропуск тика планировщика); лог пишется только на аномалии, поэтому
свежесть работы подтверждается иначе: `moderation_ipc.db` mtime **2026-08-11 04:28** (≈3 минуты
до замера) — в неё каждые 5 секунд бьёт `job_heartbeat` (`moderation_bot.py:116`).

---

## 2. Что читает и что пишет

### А. Группа-тренажёр

**Читает:**
* `moderation_ipc.meta` — таблица `meta(k TEXT PRIMARY KEY, v TEXT)` (`moderation_ipc.py:108`),
  БД `moderation_ipc.db` (1 699 840 байт, mtime 2026-08-11 04:28). Девять ключей
  (`trainer.py:42-61`): `trainer_chat_id`, `trainer_n`, `trainer_transcript`,
  `trainer_incoming`, `trainer_answer`, `trainer_hyps`, `trainer_hyp_sel`,
  `trainer_pending_lesson`, `trainer_seq`.
* `trainer_rules.json` (`trainer.py:63`, `_load_sources` :270-276).
* `trainer_log_doc.json` — file id Brain-дока и архива (читается на каждом вызове).
* через `suggest` — `manager-bot/docs/playbook.md`, `turbobaby_faq_v1.md`, `park_list.md`
  и живой Bridge.

**Пишет — и это главное: ДА, пишет далеко за пределы журнала.**

| куда | чем | что это |
|---|---|---|
| `moderation_ipc.meta` (sqlite) | `trainer._default_set` (:325) | сессия ТЕСТ-N, транскрипт, гипотезы |
| `trainer_rules.json` | `trainer.py:279/302` | пометка «правило поставлено из тренажёра» |
| **`manager-bot/docs/playbook.md`** | `trainer.py:632 append_rule` → `suggest.append_playbook_rule` | **БОЕВАЯ КНИГА ПРАВИЛ**: принятый урок подмешивается в system-prompt КАЖДОГО клиентского черновика. Файл вне git (`git ls-files manager-bot` → 0), 5045 байт, mtime 2026-07-23 01:26 |
| Brain-док `KB_trainer_log` | `trainer_log.safe_append` (Bridge read_doc/write_doc) | построчный лог событий, `doc_id 13kp-54bzN2Xua8R5VN4EaXR7IElTKg4O`, архив `1lOco1SI58UNT0a4-TBuU3yYeZzbPkWUP` |
| Telegram | `_trainer_send` (userbot) + панель кнопок (moderbot) | ТОЛЬКО в привязанную группу |

Спул недоставленных строк `trainer_log.spool` (`.gitignore:57`) **отсутствует** — незакрытых
доставок нет.

**Группа:** `TRAINER_GROUP_NAME = "Тренеровка"` (`trainer.py:36`), привязка ленивая по title,
жёсткая изоляция по chat_id (`userbot_listen.py:407-408`). Живой id виден в логе модербота:
`-5193185299`.

### Б. Безголовый прогон

**Читает:** `trainer_cases.json`; git (`rev-parse HEAD`, `diff --name-only HEAD`); `.env` через
`load_dotenv` (`trainer_run.py:79`, ради живого формата запуска); ЖИВОЙ Bridge (read-only GET
прайса/зон/FAQ); `manager-bot/docs/{playbook,turbobaby_faq_v1,park_list}.md` через `suggest`.

**Пишет ровно два места:** `pc_orchestrator.client_trainer_green.json` (`write_verdict` :450,
атомарно через `.tmp` + `os.replace`) и, по флагу `--report`, markdown-файл. Плюс stdout.

**Куда НЕ пишет** (границы объявлены в docstring :14-21 и выполнены кодом): боевую
`moderation_ipc.db` уводит в `%TEMP%/trainer_run_ipc.db` ДО любого обращения
(`isolate_ipc` :111-127); Telegram, очередь модерации, `KB_trainer_log` — ничего.
**В `cowork_log` не пишет вообще** — `cowork_log_append` он не импортирует.

### Модербот

**Читает/пишет** `moderation_ipc.db`, три таблицы (`moderation_ipc.py:91,108,116`):

* `drafts(id, client_id, client_ref, lang, incoming, draft, first_contact, status, card_msg_id,
  final_text, decided_by, reason, created_ts, updated_ts, transcript, pricing_note, directive,
  client_name)` — карточки черновиков и РЕШЕНИЯ;
* `meta(k, v)` — heartbeat, `mod_chat`, все `trainer_*`;
* `intake(id, text, status, created_ts, updated_ts, posted_msg_id, reason, client_id)` — мост
  «Заявка → INTAKE».

**Группы:** боевая группа модерации `MOD_GROUP_ID` из env либо `meta.mod_chat`
(`moderation_bot.py:60-79`), с белым списком назначения — совпадение цели с chat_id тренажёра
даёт `None` (защита от протечки 22.07); плюс группа тренажёра для панели кнопок.
**Клиенту модербот не пишет НИКОГДА** (docstring :16); отправку делает userbot.

---

## 3. Чем кончается прогон

| | итог |
|---|---|
| **А. Группа** | **Ни вердикта, ни отчёта, ни файла.** Кончается СООБЩЕНИЕМ в группу: ответ ТЕСТ-клиенту (`trainer.render_answer` → `_trainer_send`, `userbot_listen.py:333-334`) либо карточка урока (`apply_lesson` → `dec["card"]`). Кода возврата нет — это не прогон, а диалог. Побочный след — строки TRN в `KB_trainer_log`. |
| **Б. Прогон** | **МАШИННЫЙ ВЕРДИКТ.** stdout `ИТОГ: RED/GREEN — кейсов N/M, чеков K/L, прогонов R, дерево …` (:565-568); запись в `pc_orchestrator.client_trainer_green.json`; коды возврата (docstring :54-55): `0` — зелёный записан, `1` — красный, `2` — прогон не состоялся (нет корпуса, git молчит, HEAD уехал). По `--report` ещё markdown-таблица (`report_md` :488). |
| **Модербот** | Прогона нет — это демон. Каждый цикл кончается РЕШЕНИЕМ в `drafts.status`: `ready` / `test_held` / `rejected` (docstring :14-15, `_apply` :157). Вердикта, отчёта и артефакта не производит. |

---

## 4. Набор кейсов

| | корпус | сколько | последнее пополнение |
|---|---|---|---|
| А. Группа | **нет** — корпусом служит то, что владелец наберёт в группу | — | — |
| Б. Прогон | `trainer_cases.json` (114 строк) | **12** | коммит `55aad22`, **2026-07-31**; mtime 2026-07-31 19:30:40; sha256 `98ad5e3e2c256542` |
| Модербот | **нет** | — | — |

Число 12 не декоративное: `client_contour.py:314 TRAINER_MIN_CASES = 12` — порог зелёного.
Кейсы 1–12 покрывают: первый контакт, прайс-интент, гео-пин доставки, контрпример прайса,
новичок+мощный байк, неупомянутый опыт, месячный кап, EN-клиент, фото паспорта, минимальный
срок, вопрос о наличии, тайские буквы. Источник каждой фразы назван полем `source` —
все дословные (правило-класс «голдены детекта = реальные фразы»).

Ближайшее к «набору кейсов» у остальных — юнит-тесты, но это не корпус прогона:

```
test_moderation.py    919 строк, 66 тестов, последний коммит 9c42681 2026-07-22
test_trainer.py       74 теста, f7cfb25 2026-08-02
test_trainer_log.py   32 теста, 758f1de 2026-07-23
test_trainer_run.py   28 тестов, 6478a39 2026-07-30
```

---

## 5. Пересечение кода

**Пересечение не «есть» — оно структурное: модербот ЯВЛЯЕТСЯ кнопочной половиной тренажёра.**

```
$ grep -nE "^(import|from) " …
=== moderation_bot.py ===
32:import suggest            (после load_dotenv — читает конфиг из окружения)
33:import moderation_core
34:import moderation_ipc
35:import booking_draft
36:import trainer            (ГРУППА-ТРЕНАЖЁР: панель кнопок под ответом userbot)
37:import trainer_log        (ЛОГ ТРЕНАЖЁРА в мозг)
=== trainer.py ===  moderation_ipc (:320,:325), suggest (:460,:606,:628,:666), lesson_router (:593)
=== trainer_log.py === только stdlib + urllib (Bridge) — общих модулей НЕТ
=== trainer_run.py === client_contour (:89), suggest (:90), trainer (:91), moderation_ipc (:124)
=== moderation_core.py === suggest (:27), pc_orchestrator (:328, ленивый)
```

Поимённо, числом строк (`wc -l`):

| модуль | строк | кто им пользуется |
|---|---|---|
| `suggest.py` | **7193** | тренажёр (А), прогон (Б), модербот, модер-ядро — общий у ВСЕХ |
| `moderation_ipc.py` | **293** | тренажёр (А), прогон (Б, в temp), модербот — общий |
| `lesson_router.py` | **1644** | только тренажёр (А), лениво :593 |
| `client_contour.py` | **490** | только прогон (Б) |
| `booking_draft.py` | **624** | только модербот |
| `moderation_core.py` | **499** | только модербот — тренажёр его не касается |
| `trainer.py` | **696** | обе полосы: А (userbot+moderbot) и Б (прогон) |
| `trainer_log.py` | **457** | только А (userbot+moderbot); прогон его НЕ импортирует |

Сколько тренажёра лежит ВНУТРИ ботов:

* `moderation_bot.py` (661 строка): блок тренажёра — строки **290–472**
  (`_kb_trainer_panel` :290, `_kb_trainer_hyps` :299, `_trn_log` :323, `_trainer_callback` :346,
  конец :472) = **183 строки, 28% файла**; `grep -c trainer` → **54**.
* `userbot_listen.py` (648 строк): `grep -c trainer` → **74**; функции `_trainer_send` :244,
  `_trainer_generate` :252, `_trn_log` :267, `_trainer_client_turn` :289,
  `_trainer_debounced_reply` :302, `_trainer_crm` :342, `_trainer_reset` :368,
  `on_trainer_group` :376.

Дубль между полосами А и Б назван самим кодом: `trainer_run.generate` (:212-226) — ДОСЛОВНАЯ
копия вызовов `userbot_listen._trainer_generate` (:252-263), и расхождение ловит AST-сверка
`test_trainer_run.test_pipeline_ne_razoshelsya`.

---

## 6. ГЛАВНЫЙ ВОПРОС: кто и где читает вердикт тренажёра

### Место в коде ЕСТЬ. Вот оно, строкой.

Цепочка потребления, снизу вверх:

1. **`pc_orchestrator.py:5420`** — `reason = (reason_fn or client_contour.release_reason)(commit)`
   внутри `_client_block` (объявлена `:5406`). Дальше `:5421-5428`:
   ```
   if reason:
       st.pop(kk, None)
       log.info("ворота контура (%s): основание пропуска «%s» — коммит %s применяем к %s", …)
       (cowork or _cowork)("ворота клиентского контура ОТКРЫТЫ по основанию «%s»: …")
       return []                      ← пустой список = «применять МОЖНО»
   ```
   Это и есть ворота: `return []` пропускает коммит к живым `userbot`/`moderbot`.
2. **`client_contour.py:457-458`** — `if trainer_green(commit, trainer_path, env): return "trainer"`.
3. **`client_contour.py:446-448`** `trainer_green` → **`:375 trainer_verdict`** → читает
   **`:308 TRAINER_GREEN_FILE = pc_orchestrator.client_trainer_green.json`**.

Три места, где ворота вызываются: `pc_orchestrator.py:5549` (авто-обновление после задачи),
`:5663` (реконсиляция после self-update), `:5930`.
Единственный потребитель вердикта во всём репозитории — эта цепочка. Больше вердикт не читает
никто.

### Но по факту ворота по тренажёру НЕ ОТКРЫВАЛИСЬ ни разу в боевой работе

Вердикт-файл сегодня, дословно (первые строки):

```json
{
 "green": {},
 "red": {
  "6478a39": { … "result": "red", "checks_passed": 219, "checks_total": 222,
               "cases": 10, "cases_total": 12, "runs": 2, "clean": true,
               "corpus_sha": "d7251f926ba60f61", "when": "2026-07-30 23:21:56",
               "failed": ["5/1 обязательное упоминание", "7/1 нет утверждений о наличии",
                          "7/2 нет утверждений о наличии"] },
  "55aad22": { … "result": "red", "checks_passed": 221, "checks_total": 224,
               "cases": 10, "cases_total": 12, "runs": 2, "clean": true,
               "corpus_sha": "98ad5e3e2c256542", "when": "2026-07-31 19:40:01",
               "failed": ["7/1 нет утверждений о наличии", "7/2 нет утверждений о наличии",
                          "10/1 обязательное упоминание"] }
 },
 "last": { … "result": "red" … }
}
```

**`"green": {}` — коробка зелёных ПУСТА.** Обе записи красные, обе на 10 кейсах из 12, обе
спотыкаются об одни и те же чеки («нет утверждений о наличии», «обязательное упоминание»).

Живой лог демона:

```
$ grep -o 'основание пропуска «[a-z]*»' pc_orchestrator.log | sort | uniq -c
      3 основание пропуска «owner»
      2 основание пропуска «trainer»
$ grep -c "ворота контура" pc_orchestrator.log → 27
```

Оба «trainer» — из ОДНОЙ пробы, помеченной в самом логе как проба:

```
2026-07-30 23:32:51,590 ERROR ворота контура (живая проверка ворот): применение f683cdc4e8… к «userbot,moderbot» ОСТАНОВЛЕНО — клиентские файлы: suggest.py
2026-07-30 23:32:51,599 INFO  ворота контура (живая проверка ворот): основание пропуска «trainer» — коммит f683cdc4e8… применяем к userbot,moderbot
2026-07-30 23:34:26,504 INFO  ворота контура (живая проверка ворот): основание пропуска «trainer» — коммит 3a097a94fc… применяем к userbot,moderbot
2026-07-30 23:35:25,083 INFO  id=72 NEEDS_APPROVAL
```

Все ОСТАЛЬНЫЕ 27 срабатываний ворот — «ОСТАНОВЛЕНО» либо «owner»:

```
2514:2026-07-31 21:13:27 ERROR … применение 3124e25 к «userbot,moderbot» ОСТАНОВЛЕНО — клиентские файлы: suggest.py
4860:2026-08-05 01:01:14 ERROR … применение 921c9c6fa … ОСТАНОВЛЕНО — клиентские файлы: log_setup.py
5243:2026-08-05 11:14:51 ERROR … применение dfc8d6f9b к «userbot» ОСТАНОВЛЕНО — клиентские файлы: userbot_listen.py
5259:2026-08-05 11:53:51 ERROR … применение 8b2862632 к «userbot» ОСТАНОВЛЕНО — …
1824:2026-07-30 14:41:30 INFO  … основание пропуска «owner» — коммит 4528917 применяем к userbot,moderbot
```

### Прямой ответ

Место есть и оно рабочее — доказано пробой 30.07. Но **за всё время боевой работы вердикт
тренажёра ни разу не пропустил ни одного коммита**: единственные два «trainer» в логе — проба,
устроенная в тот же вечер, когда механизм заводили; зелёного вердикта на диске нет ни одного;
последний реальный прогон (31.07) красный; сам раннер с тех пор ни разу не запускался и ничем
не запускается.

**Практический смысл: вердикт СЕГОДНЯ фактически никем не потребляется.** Ворота клиентского
контура держатся на одном основании — «да» владельца (3 срабатывания из 5). Второе основание
заведено, проверено и простаивает. Для разговора об объединении это значит, что зелёное
тренажёра сегодня ничего не открывает и ничего не стоит — цена ошибки в нём пока нулевая, но
и польза тоже.

---

## 7. Может ли прогон быть неполным или на другом коммите и всё равно дать зелёное

### ЗАКРЫТО: «на другом коммите» — нет

* `trainer_run.py:558-562` — HEAD перечитывается ПОСЛЕ прогона:
  ```
  head_after = head_commit()
  if head_after != commit:
      print(f"ПРОГОН НЕ ЗАСЧИТАН: HEAD уехал за время прогона …")
      return 2
  ```
  вердикт не строится вовсе.
* `client_contour.py:412-414` — ворота сверяют коммит ЦЕЛИКОМ, не по префиксу:
  ```
  rc = str(rec.get("commit") or "").strip().lower()
  cc = str(commit or "").strip().lower()
  if short(rc) != c or (len(rc) == 40 and len(cc) == 40 and rc != cc):
      return False, f"вердикт снят на ДРУГОМ коммите ({short(rc) or '?'}), а выкатывается {c}"
  ```

### ЗАКРЫТО: `--only` не надувает результат

`trainer_run.py:548-551` — `total` берётся ДО фильтра:
```
    total = len(cases)
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        cases = [c for c in cases if str(c.get("id")) in want]
```
а `:436` требует `passed == cases_total >= 12`. `--only 3` → `cases_total = 12`, `passed ≤ 1` →
красный. Порядок этих двух строк и есть замок.

### ОТКРЫТО: полноту прогона объявляет КОРПУС, а корпус — данные

Четыре строки, дающие ответ:

1. `trainer_run.py:392` —
   ```
   "ok": all(c["ok"] for c in checks if not c.get("skipped")),
   ```
   `all([])` == `True`: кейс, у которого ВСЕ чеки названы в `skip`, зачитывается пройденным при
   НУЛЕ живых чеков.
2. `trainer_run.py:412-414` —
   ```
   if c.get("skipped"):
       continue                       # снят с причиной — в счёт не идёт
   checks_all += 1
   ```
   снятые чеки не попадают в знаменатель.
3. `trainer_run.py:279` (docstring `case_checks`) — «Применимость объявляет сам кейс (`expect`)»:
   цена, сетка, доставка добавляются только `if want.get(...)`. Кейс с `"expect": {}` и без
   `forbid`/`require_any`/`expect_facts` получает лишь 7 универсальных чеков (так устроен
   живой кейс 11).
4. `trainer_run.py:436-437` — единственный глобальный предохранитель:
   ```
   green = (passed == cases_total >= client_contour.TRAINER_MIN_CASES
            and checks_all > 0 and checks_ok == checks_all …)
   ```
   `checks_all > 0` — «хоть один чек», а не «столько же, сколько вчера».

Следствие арифметикой: 11 кейсов со всеми чеками в `skip` + 1 кейс с одним живым чеком дают
`checks_ok == checks_all == 2`, `cases 12/12`, `runs 2`, `clean true` → **ЗЕЛЁНЫЙ**. Сегодня
живых чеков 224; зелёное на двух чеках выглядит в файле неотличимо, потому что запись вердикта
(`build_verdict` :438-448) поля «сколько снято» **не содержит вовсе** — ворота его не видят.

Механизм не гипотетический: `skip` уже используется в живом корпусе (кейс 2, `trainer_cases.json:21-23`,
снят чек «депозит без противоречий» с причиной).

Единственная защита ворот здесь — `client_contour.py:428-430`:
```
    sha = corpus_sha(cases_path)
    if not sha or str(rec.get("corpus_sha") or "") != sha:
        return False, "корпус кейсов не тот, на котором снят вердикт"
```
но `corpus_sha` считается с файла **на диске в момент чтения ворот** — то есть с того же
урезанного корпуса. Правка `trainer_cases.json` делает дерево грязным (`clean=false` → красный),
однако **закоммиченная** правка возвращает дерево в чистое, и урезанный корпус становится
эталоном для самого себя. Причём коммит, трогающий только `trainer_cases.json`, воротами даже
не удерживается: `_client_paths` (`pc_orchestrator.py:5386-5398`) считает клиентским по графу
импортов и карте процессов, а json-корпус ни туда, ни туда не входит.

### ОТКРЫТО: `clean: true` относится ТОЛЬКО к отслеживаемым файлам

`trainer_run.py:148-156`:
```
def dirty_tracked():
    """ОТСЛЕЖИВАЕМЫЕ правки против HEAD → список путей | None (git не ответил). …"""
    rc, out = _git(["diff", "--name-only", "HEAD"])
```
`git diff --name-only HEAD` не показывает untracked. А главные входы пайплайна лежат ВНЕ git:
```
suggest.py:258  LOCAL_FAQ      = …/manager-bot/docs/turbobaby_faq_v1.md
suggest.py:492  PARK_LIST_FILE = …/manager-bot/docs/park_list.md
suggest.py:667  PLAYBOOK_FILE  = …/manager-bot/docs/playbook.md
$ git ls-files manager-bot | wc -l → 0
$ ls -la manager-bot/docs/playbook.md → 5045  Jul 23 01:26
```
`playbook.md` подмешивается в system-prompt каждого черновика (`generate` :223 → `load_playbook`)
и переписывается на «w» боевым `append_playbook_rule` — в том числе КАЖДЫМ принятым уроком
группы-тренажёра. Значит: **тот же коммит + другой playbook = другие черновики, и в обоих
случаях в вердикте будет `"clean": true`.** Вердикт удостоверяет «коммит X плюс что оказалось
на диске в ту минуту», а называется «коммит X».

К этому же классу — живой Bridge (docstring :27-29): цены, зоны и FAQ берутся из листа в момент
прогона; это сделано осознанно (ожидания чеков считаются из того же `pricing_note`), но
воспроизводимости из коммита не даёт.

### ОТКРЫТО: у вердикта нет подписи, и раннер не привилегирован

`client_contour.py:378-381` говорит это сам:
> «Ворота файлу НЕ ВЕРЯТ НА СЛОВО — кто может писать этот файл, тот открывает клиентский контур
> в обход «да» владельца».

`pc_orchestrator.client_trainer_green.json` — обычный JSON под `.gitignore:54` (маска
`pc_orchestrator.*.json`), без подписи и без владельца. Запись доказывает себя ТОЛЬКО
собственными полями. Раннер к этому файлу отношения не имеет: `trainer_run.py:526`
`ap.add_argument("--out", default=VERDICT_FILE)` — путь параметр, поле `"runner"` в записи
самодекларируемое. Кто умеет посчитать `sha256(trainer_cases.json)[:16]` — умеет написать
зелёную запись на любой коммит.

### Ответ одной строкой

**«На другом коммите» — нет, закрыто двумя проверками. «Неполным» — ДА:** полнота объявляется
самим корпусом (`skip` + `expect`), снятые чеки не попадают ни в числитель, ни в знаменатель, и
в вердикте нет поля, по которому ворота могли бы это заметить; плюс `clean` слеп к untracked, а
там лежит `playbook.md`, формирующий каждый черновик.

---

## Сводка

1. «Тренажёр» — это ДВЕ вещи: группа-тренажёр (`trainer.py` 696 + `trainer_log.py` 457,
   без своего процесса, живёт внутри userbot PID 7828 и moderbot PID 11968) и безголовый прогон
   (`trainer_run.py` 590, отдельный CLI). Только вторая отдаёт вердикт; только первая пишет в бой.
2. Группа загружена, но простаивает: `userbot.log` за 2026-06-09…2026-08-11 без ротации даёт
   **0** совпадений `ТЕСТ-|тренаж|trainer|TRN`, при том что каждое событие обязано логироваться
   через `trainer_log.py:443/445`. Последний след группы — 2026-07-22, и это отказ маршрутизации.
   Прогон мёртв с **2026-07-31 19:40:01**, вердикт **красный**, запускается только руками —
   вызывающего кода в репозитории нет.
3. Модербот жив (PID 11968 с 06.08), пишет `drafts`/`meta`/`intake` в `moderation_ipc.db`
   (mtime 2026-08-11 04:28), клиенту не пишет никогда, корпуса кейсов не имеет, вердикта
   не производит. 183 из его 661 строки (28%) — это код тренажёрной панели: он и есть
   кнопочная половина тренажёра, а не соседний инструмент.
4. **Вердикт читает ровно одно место — `pc_orchestrator.py:5420` → `client_contour.py:457-458`
   → `:375 trainer_verdict`. Оно рабочее, но за всю боевую жизнь пропустило НОЛЬ коммитов:
   `"green": {}` пусто, два «основание пропуска «trainer»» в логе — проба 30.07, помеченная
   «живая проверка ворот». Сегодня вердикт фактически не потребляется.**
5. Неполный прогон может дать зелёное: `trainer_run.py:392` (`all([])`), `:412-414` (снятое не
   считается), `:436-437` (порог — «хоть один чек»); `clean` слеп к untracked
   (`:148-156` против `suggest.py:667` — `playbook.md` вне git). На другом коммите — не может
   (`:558-562`, `client_contour.py:412-414`).
