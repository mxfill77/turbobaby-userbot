# Три задачи 328 исчезли, RC не стартует, Drive молчит — общий корень

**Снято:** 28.07.2026, 17:42–17:52 BKK. **ТОЛЬКО ЧТЕНИЕ.** Ни один файл контура не изменён,
ни один процесс не рестартован, ssh на VPS **не использован** (красный, ждёт подтверждения).
Корень найден с ПК: очередь живёт на мосту, а мост отвечает.

---

## Итог одной строкой

**Полоса ПК цела, полоса VPS рвётся на АВТОРИЗАЦИИ.** Все три задачи темы 328 не «исчезли» —
они **упали в `failed`** с дословным результатом `claude -p упал (exit=1): Failed to authenticate:
OAuth session expired and could not be refreshed`. Локальный `claude -p` на ПК при этом
**жив** (два живых прогона, rc=0).

---

## 1. Три задачи — найдены, статус `failed` (чтение очереди Bridge с ПК, read-only)

`GET get_pending` по всем статусам, HTTP 200 по каждому. `new=0, in_progress=0,
needs_approval=0, approved=0, done=0, failed=3`:

```
########## status=failed | HTTP 200 | ok=True | error=None | items=3 ##########
  id=1  lane=vps  from=Filipp-328  created=2026-07-28T10:18:55.172Z  updated=2026-07-28T10:19:50.664Z
      task_text: ultrathink ⏎ Задача read-only. Ничего не менять, не коммитить, не рестартовать...
      result  : claude -p упал (exit=1): Failed to authenticate: OAuth session expired and could not be refreshed
  id=2  lane=vps  from=Filipp-328  created=2026-07-28T10:29:16.005Z  updated=2026-07-28T10:30:24.566Z
      task_text: ultrathink ⏎ Read-only зонд... Выполни ровно одну команду: date -u
      result  : claude -p упал (exit=1): Failed to authenticate: OAuth session expired and could not be refreshed
  id=3  lane=vps  from=Filipp-328  created=2026-07-28T10:32:35.723Z  updated=2026-07-28T10:33:08.254Z
      task_text: ultrathink ⏎ Read-only. Через Bridge read_doc (GET), BRIDGE_URL из .env сервиса splinter...
      result  : claude -p упал (exit=1): Failed to authenticate: OAuth session expired and could not be refreshed
```

Времена сходятся с симптомом дословно (UTC+7): **17:18:55 / 17:29:16 / 17:32:35** — те самые
17:18, 17:29, 17:32. Провалились через **55с / 68с / 33с** после постановки: это не «зависли»,
это мгновенный отказ авторизации.

Полоса всех трёх — `lane=vps`. **Задач полосы `pc` в очереди не было вовсе** — поэтому ПК-демон
и не должен был их брать.

## 2. Полоса ПК: авторизация ЖИВА (два живых прогона)

Дефолтная модель:

```
$ claude -p "верни ровно слово OK" --output-format text
OK
rc=0
```

Ровно команда исполнителя (`pc_orchestrator.py:610` — `--model claude-opus-4-8 --effort xhigh`)
⚠️ **устарело с 30.07: сегодня исполнитель идёт на `claude-opus-5`** (`EXECUTOR_MODEL`);
для воспроизведения пробы подставляй действующую модель, иначе проверишь снятую голову:

```
$ claude -p --model claude-opus-4-8 --effort xhigh "верни ровно слово OK"
OK
rc=0
```

`claude --version` → `2.1.218 (Claude Code)`, `C:\Users\mxfill1\.local\bin\claude.exe`.
Переменных окружения `ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN` **нет ни на одном уровне**
(процесс/User/Machine) — работа идёт по подписке через `.credentials.json`.

**Ключевой факт времени:** `C:\Users\mxfill1\.claude\.credentials.json` —
`CreationTime = LastWriteTime = 28.07.2026 15:47:14`. Файл сегодня **создан заново**, то есть на
ПК был выполнен свежий вход. Это объясняет, почему ПК здоров, а всё, что держит СТАРЫЙ токен, — нет.

## 3. Процессы ПК — все живы

```
PID 7264   24.07 16:49  venv\python.exe pc_agent.py
PID 6888   24.07 21:32  venv\python.exe rc_supervisor.py
PID 3780   26.07 01:12  venv\python.exe pc_orchestrator.py
PID 15932  28.07 17:14  venv\python.exe userbot_listen.py      (перезапущен демоном под 7a3c9b6)
PID 15816  28.07 17:19  venv\python.exe moderation_bot.py      (перезапущен демоном под 7a3c9b6)
```

Рестарты 17:14/17:19 — штатная реконсиляция после коммита `7a3c9b6` (17:08), не авария:

```
17:14:53 INFO реконсиляция детей: userbot рестартнут до 7a3c9b6c5, PID [15932]
17:19:11 INFO реконсиляция детей: moderbot рестартнут до 7a3c9b6c5, PID [15816]
```

Heartbeat демона свежий, вотчдог в состоянии `alive`, смертей детей `0`.

## 4. RC: процессы живы и соединены, но КАЖДЫЙ спавн сессии ловит 401 «revoked»

```
PID 17896  24.07 21:32  claude.EXE rc --debug-file rc_server_debug.log
PID 3284   28.07 17:32  claude.EXE --remote-control turbobaby-pc --debug-file rc_session_debug.log
```

Все вхождения `revoked` в RC-логах — дословно:

```
2026-07-24T19:24:48.303Z [code-session] Get session_01VfzYw5adZmjyCg5HMZepA2 failed 401: OAuth access token has been revoked.
2026-07-24T19:24:51.742Z [code-session] Get session_01VfzYw5adZmjyCg5HMZepA2 failed 401: OAuth access token has been revoked.
2026-07-28T08:01:15.323Z [code-session] Get session_01AyUG2oPb7pDG7EXarG1zb7 failed 401: OAuth access token has been revoked.
2026-07-28T08:01:21.011Z [code-session] Get session_016WXCF8f1VKM73LiBExHtYy failed 401: OAuth access token has been revoked.
2026-07-28T08:04:41.248Z [code-session] Get session_016WXCF8f1VKM73LiBExHtYy failed 401: OAuth access token has been revoked.
2026-07-28T10:34:39.144Z [code-session] Get session_01R4adykeqcbzKitUpSDNSRk failed 401: OAuth access token has been revoked.
```

**Сервер RC поднят 24.07 21:32 и с тех пор не перезапускался** — он держит в памяти токен той
эпохи, а вход на ПК переигран сегодня в 15:47. Последняя сессия (`cse_01R4ad…`, спавн 17:34:38)
жива, но **промпт так и не получила**: в её логе только `CCRClient: Heartbeat sent` каждые 20с,
а сервер тем временем пишет `GET .../work/poll -> 200 (no work, 100 consecutive empty polls)`.
Клиент при этом настойчиво переприсылает `initialize` (10:34:51, 10:35:21, 10:35:34, 10:36:38,
10:39:05, 10:43:04) — то есть телефон стучится, а сессия его не подхватывает.

**Оговорка честности:** 401 при спавне сам по себе не фатален — сессия `cse_016WXC…` (спавн 15:04,
тот же 401) отработала **44 хода** и в 17:08 сделала коммит `7a3c9b6`, завершившись в 17:10:56.
Значит для RC доказано «то же семейство отказов», но не доказана прямая причинно-следственная
связь с молчанием сессии 17:34.

## 5. Мост / Drive — ЖИВ (гипотеза «мост лёг» опровергнута)

Живая проба тем же адресом, что и у сервера (секретов не печатает):

```
base: https://script.google.com
GET  read_doc name=cc_log       HTTP 200 | ok=True  | text_chars=22568
GET  read_doc name=cowork_log   HTTP 200 | ok=True  | text_chars=751229
GET  get_balance                HTTP 200 | ok=False | error=unknown_action   (норма: get_balance живёт в doPost)
POST get_balance                HTTP 200 | ok=True  | keys=[_status, action, balance, ok, wallets]
```

При этом мост **флакует** весь день — за 28.07 в `pc_orchestrator.log` девять `HTTPError` и один
`TimeoutError`:

```
11:09:38 WARNING get_pending(in_progress) ошибка: HTTPError
14:00:57 WARNING get_pending(new) ошибка: HTTPError
14:03:14 WARNING get_pending(in_progress) ошибка: HTTPError
14:51:19 WARNING get_pending(new) ошибка: HTTPError
15:14:23 WARNING get_pending(in_progress) ошибка: HTTPError
15:37:54 WARNING get_pending(in_progress) ошибка: TimeoutError
15:49:45 WARNING get_pending(in_progress) ошибка: HTTPError
16:23:52 WARNING get_pending(new) ошибка: HTTPError
16:25:04 WARNING get_pending(in_progress) ошибка: HTTPError
16:39:07 WARNING get_pending(in_progress) ошибка: HTTPError
```

Это известная флакость `/exec` (302 → `googleusercontent` → иногда 404), описанная в
`docs/artifacts/2026-07-28-journal-write-fixes.md §1`. **Задачи 328 она не роняла** — их убила
авторизация, а не транспорт.

## 6. Ложный след, который стоит снять: «ПК засыпает»

`pc_orchestrator.log` за день усеян `WARNING детект пробуждения ПК: скачок wall-clock 120–636с`.
**ПК сегодня не спал:** событий сна/пробуждения в журнале Windows за сегодня нет вовсе —

```
Get-WinEvent … ProviderName='Microsoft-Windows-Kernel-Power', StartTime=сегодня
ERR: No events were found that match the specified selection criteria.
```

(из power-событий за сегодня — только три `Kernel-General Id=1` в 10:23:15).

Значит «скачок wall-clock» — это **медленная итерация цикла**, а не сон: у `Bridge` таймаут
**90 секунд** (`pc_orchestrator.py:222`), и несколько подвисших вызовов подряд дают ту самую
многоминутную «дыру». Признак сна по скачку часов **врёт** и маскирует реальную причину.

---

## Развилка владельцу (всё — мутации, ни одна не сделана)

1. **VPS: переавторизовать `claude` на splinter.** Это единственное, что вернёт задачи 328.
   Требует ssh (сейчас красный) или консоли провайдера.
2. **RC на ПК: перезапустить сервер RC** (`PID 17896`, живёт с 24.07 со старым токеном).
   Ожидание: спавн сессий перестанет ловить 401. Риск — оборвутся три висящие сессии
   (`cse_01AyUG…`, `cse_016WXC…`, `cse_01R4ad…`), работы в них нет, только heartbeat.
3. **Разово: снять ложный признак сна** — судить о пробуждении не по скачку wall-clock, когда
   в цикле есть 90-секундные сетевые вызовы. Иначе класс #171 будет всплывать вечно.
4. **К сведению:** `cowork_log` дорос до **751 229 символов** (у `cc_log` — 22 568), а писатель
   переписывает док целиком. Прун для `cowork_log` не заведён (у `cc_log` есть `prune_cc_log`).

## Чего я НЕ проверял

- Ничего на VPS: ssh красный, подтверждения не было. Не проверены `systemctl status splinter`,
  `journalctl`, `ps aux | grep claude`, `dmesg`, время и содержимое `.credentials.json` сервера.
- **Порядок событий не доказан:** сломался ли аккаунт сам (а вход на ПК в 15:47 был лечением),
  или свежий вход на ПК отозвал токены остальных — по фактам с ПК различить нельзя. Решает
  время создания `.credentials.json` на VPS, а его читать не ходил.
- «Drive-коннектор Штаба» отдельно не проверялся. С ПК Drive через мост читается (HTTP 200),
  так что если коннектор ходит через ту же claude-сессию — корень тот же; если это отдельный
  google-грант — корень другой, и он не исследован.
