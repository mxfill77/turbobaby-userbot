# Замер чтения мозга СВОЕЙ полосой: чем объясняется разброс (08.08.2026)

READ-ONLY. Правок кода нет, коммита нет, пуша нет, гейт не гонялся. Временных файлов замер не
создал — каталог, отведённый под временное (`/tmp/tb_scratch/`), остался нетронут и на месте;
сырьё замера целиком лежит в этом артефакте.

Повод — здоровье-карточка 07.08.2026 16:50 UTC: `cc_log 207с⚠️(>10); review 5.7с;
project_state 2.2с; knowledge_base ✗(request_failed)`.

---

## 0. Каким путём меряли (и почему именно так)

Здоровье-проверка живёт в `health.py`, брейн-замер — `check_brain_latency()`:

```
health.py:41   _BRAIN_DOCS = [
health.py:42       ("cc_log",         {"name": "cc_log"}, 10),
health.py:43       ("review",         {"name": "review"}, 10),
health.py:44       ("project_state",  {"name": "project_state"}, 45),
health.py:45       ("knowledge_base", {"name": "knowledge_base"}, 40),
health.py:46   ]
health.py:47   _BRAIN_PROBE_MARGIN = 4   # сек сверх порога: «дочитал, но медленно» vs «не дочитал (timeout)»
...
health.py:339          bc = BridgeClient(timeout=thr + _BRAIN_PROBE_MARGIN)
health.py:342          t0 = time.time()
health.py:344          r = bc._call("read_doc", **kw)
```

Живой формат запуска — `deploy/splinter-health.service`:

```
ExecStart=/root/turbobaby-manager-bot/venv/bin/python3 /root/turbobaby-manager-bot/health.py --push
```
таймер: `OnBootSec=10min`, `OnUnitActiveSec=4h`, `Persistent=true`.

Мерили **ровно этой командой без `--push`** (пуш владельцу в замере не нужен и был бы шумом).
Пять отдельных процессов — как в проде, по одному на прогон; пауза между пробами — интервал
между запусками (08:16 → 08:18 UTC). Свой пробный скрипт не писался: замер = существующий путь.

Побочный факт: у `check_brain_latency` **свой BridgeClient на КАЖДЫЙ документ** (строка 339
внутри цикла) — то есть своя HTTP-сессия и свой счётчик транспорт-сбоев на документ.

---

## 1. Дословный вывод: пять проб (08.08.2026, 08:16–08:18 UTC)

Команда (пять раз, отдельными процессами):
`/root/turbobaby-manager-bot/venv/bin/python3 /root/turbobaby-manager-bot/health.py`

```
🩺 TurboBaby — здоровье системы (2026-08-08 08:16 UTC)
──────────────────────────────────────────────
✅ Splinter сервис  active/running, uptime 26ч49м, рестартов 0
✅ Bridge          alive v1.0.0
✅ Аудитор         подключён + LLM-надзор (старт-лог)
✅ Polling         Bot polling started
⚠️ Лог            посл. строка 32с назад; ошибок с старта: 130
✅ Brain read     cc_log 2.6с; review 2.4с; project_state 2.3с; knowledge_base 2.2с
✅ Кредит API     кредит ок (1-токенная проба прошла)
✅ WA webhook     active, handshake 200/probe42, очередь new=0
ℹ️ Прод           352f4e5 2026-08-08 04:30:03 +0000 проект ожиданий по ПК, мосту и moderbot: предел канала назван, пороги сняты с живых пауз
──────────────────────────────────────────────
⚠️ РАБОТАЕТ, но 130 ошибок в логе с старта — стоит глянуть
```
```
🩺 TurboBaby — здоровье системы (2026-08-08 08:16 UTC)
✅ Splinter сервис  active/running, uptime 26ч50м, рестартов 0
⚠️ Лог            посл. строка 19с назад; ошибок с старта: 130
✅ Brain read     cc_log 2.4с; review 4.6с; project_state 1.8с; knowledge_base 2.1с
⚠️ РАБОТАЕТ, но 130 ошибок в логе с старта — стоит глянуть
```
```
🩺 TurboBaby — здоровье системы (2026-08-08 08:16 UTC)
✅ Splinter сервис  active/running, uptime 26ч50м, рестартов 0
⚠️ Лог            посл. строка 40с назад; ошибок с старта: 130
✅ Brain read     cc_log 2.3с; review 1.9с; project_state 2.0с; knowledge_base 1.9с
```
```
🩺 TurboBaby — здоровье системы (2026-08-08 08:17 UTC)
✅ Splinter сервис  active/running, uptime 26ч51м, рестартов 0
⚠️ Лог            посл. строка 30с назад; ошибок с старта: 130
✅ Brain read     cc_log 2.2с; review 2.2с; project_state 2.1с; knowledge_base 2.1с
```
```
🩺 TurboBaby — здоровье системы (2026-08-08 08:18 UTC)
✅ Splinter сервис  active/running, uptime 26ч52м, рестартов 0
⚠️ Лог            посл. строка 3с назад; ошибок с старта: 130
✅ Brain read     cc_log 2.5с; review 1.9с; project_state 2.3с; knowledge_base 1.8с
```
(остальные строки прогонов 2–5 повторяли прогон 1 дословно, кроме uptime/свежести лога —
приведены различающиеся строки; полный первый прогон выше целиком.)

### Сводка пяти проб

| документ | размер | успехов | мин | медиана | макс | текст отказа |
|---|---|---|---|---|---|---|
| cc_log | 410 863 Б | 5/5 | 2.2 с | 2.4 с | 2.6 с | отказов нет |
| review | 803 Б | 5/5 | 1.9 с | 2.2 с | **4.6 с** | отказов нет |
| project_state | 58 055 Б | 5/5 | 1.8 с | 2.1 с | 2.3 с | отказов нет |
| knowledge_base | 95 865 Б | 5/5 | 1.8 с | 2.1 с | 2.2 с | отказов нет |

20 чтений из 20 успешны, отказов ноль, повторов транспорта ноль (`bridge_client` пишет ретраи
через `log.warning`; в `health.py` логирование не настраивается — `basicConfig` есть только под
`__main__` самого `bridge_client` (стр. 1197), — значит WARNING ушёл бы в stderr через
lastResort и был бы виден. Не было ни одного).

**Честный предел серии:** окно 08:16–08:18 оказалось тихим. Пять проб воспроизводят НОРМУ
(≈2 с на любой документ), но не воспроизводят эпизод. Поэтому вторым замером взят живой корпус
того же пути — 44 боевых прогона `splinter-health.service` за 8 суток (§2). Единственная
аномалия серии — 4.6 с на **самом маленьком** документе (803 Б), то есть ровно тот же рисунок,
что в карточке владельца (review 803 Б — 5.7 с при project_state 58 КБ — 2.2 с).

---

## 2. Живой корпус того же пути: 44 прогона × 4 документа = 176 чтений

Команда: `journalctl -u splinter-health.service --since "2026-08-01 00:00" --no-pager -g "Brain read"`

```
Aug 01 00:30:18 ✅ Brain read     cc_log 1.8с; review 1.8с; project_state 2.0с; knowledge_base 2.6с
Aug 01 04:30:18 ✅ Brain read     cc_log 2.3с; review 1.9с; project_state 2.1с; knowledge_base 1.9с
Aug 01 08:31:02 ⚠️  Brain read     cc_log 37с⚠️(>10); review 2.4с; project_state 2.6с; knowledge_base 9.0с
Aug 01 12:30:18 ✅ Brain read     cc_log 2.2с; review 1.6с; project_state 1.9с; knowledge_base 1.8с
Aug 01 16:30:16 ✅ Brain read     cc_log 2.1с; review 1.7с; project_state 1.7с; knowledge_base 1.6с
Aug 01 20:30:53 ✅ Brain read     cc_log 1.8с; review 1.7с; project_state 2.0с; knowledge_base 1.6с
Aug 02 00:31:07 ✅ Brain read     cc_log 2.0с; review 1.6с; project_state 1.8с; knowledge_base 2.0с
Aug 02 04:31:52 ✅ Brain read     cc_log 6.3с; review 4.6с; project_state 11.0с; knowledge_base 4.2с
Aug 02 08:31:36 ✅ Brain read     cc_log 1.6с; review 1.8с; project_state 1.7с; knowledge_base 1.7с
Aug 02 12:32:04 ✅ Brain read     cc_log 1.8с; review 1.7с; project_state 2.0с; knowledge_base 1.7с
Aug 02 16:32:07 ✅ Brain read     cc_log 2.2с; review 1.8с; project_state 1.9с; knowledge_base 1.9с
Aug 02 20:34:38 ⚠️  Brain read     cc_log 17с⚠️(>10); review 2.9с; project_state 66с⚠️(>45); knowledge_base 2.3с
Aug 03 00:33:33 ✅ Brain read     cc_log 4.0с; review 1.9с; project_state 2.3с; knowledge_base 2.4с
Aug 03 04:34:02 ✅ Brain read     cc_log 2.3с; review 1.5с; project_state 2.0с; knowledge_base 1.9с
Aug 03 08:33:59 ✅ Brain read     cc_log 2.0с; review 1.9с; project_state 2.1с; knowledge_base 1.7с
Aug 03 12:34:09 ✅ Brain read     cc_log 2.0с; review 3.4с; project_state 1.9с; knowledge_base 2.5с
Aug 03 16:35:11 ✅ Brain read     cc_log 4.9с; review 2.4с; project_state 2.3с; knowledge_base 3.7с
Aug 03 20:35:26 ✅ Brain read     cc_log 9.8с; review 1.9с; project_state 1.8с; knowledge_base 2.1с
Aug 04 00:36:08 ✅ Brain read     cc_log 2.1с; review 1.8с; project_state 2.6с; knowledge_base 2.1с
Aug 04 04:36:19 ✅ Brain read     cc_log 3.7с; review 6.1с; project_state 2.8с; knowledge_base 2.1с
Aug 04 08:36:36 ✅ Brain read     cc_log 2.2с; review 1.9с; project_state 1.9с; knowledge_base 1.9с
Aug 04 12:36:26 ✅ Brain read     cc_log 2.1с; review 2.1с; project_state 2.1с; knowledge_base 2.5с
Aug 04 16:38:01 ✅ Brain read     cc_log 1.8с; review 1.9с; project_state 1.8с; knowledge_base 2.0с
Aug 04 20:37:08 ✅ Brain read     cc_log 2.1с; review 1.9с; project_state 1.9с; knowledge_base 2.1с
Aug 05 00:37:16 ✅ Brain read     cc_log 2.3с; review 2.1с; project_state 2.1с; knowledge_base 1.8с
Aug 05 04:37:43 ✅ Brain read     cc_log 2.0с; review 1.9с; project_state 1.9с; knowledge_base 2.1с
Aug 05 08:37:57 ✅ Brain read     cc_log 2.2с; review 2.2с; project_state 2.0с; knowledge_base 2.1с
Aug 05 12:37:58 ✅ Brain read     cc_log 2.3с; review 2.3с; project_state 2.0с; knowledge_base 1.9с
Aug 05 16:38:13 ✅ Brain read     cc_log 2.1с; review 3.2с; project_state 2.5с; knowledge_base 2.4с
Aug 05 20:39:09 ✅ Brain read     cc_log 2.8с; review 1.8с; project_state 1.8с; knowledge_base 2.0с
Aug 06 00:39:30 ✅ Brain read     cc_log 2.4с; review 1.9с; project_state 1.7с; knowledge_base 2.3с
Aug 06 04:39:47 ✅ Brain read     cc_log 2.4с; review 2.1с; project_state 1.9с; knowledge_base 2.3с
Aug 06 08:41:30 ⚠️  Brain read     cc_log 18с⚠️(>10); review 39с⚠️(>10); project_state 14.7с; knowledge_base 18.4с
Aug 06 12:41:13 ⚠️  Brain read     cc_log 51с⚠️(>10); review 1.9с; project_state 1.8с; knowledge_base 1.9с
Aug 06 16:40:27 ✅ Brain read     cc_log 2.5с; review 2.5с; project_state 2.2с; knowledge_base 2.1с
Aug 06 20:40:17 ✅ Brain read     cc_log 1.9с; review 1.6с; project_state 2.1с; knowledge_base 2.1с
Aug 07 00:40:19 ✅ Brain read     cc_log 2.1с; review 1.7с; project_state 1.9с; knowledge_base 2.4с
Aug 07 04:40:19 ✅ Brain read     cc_log 2.5с; review 2.2с; project_state 2.3с; knowledge_base 2.4с
Aug 07 08:41:08 ✅ Brain read     cc_log 2.5с; review 1.9с; project_state 1.8с; knowledge_base 2.1с
Aug 07 12:41:24 ✅ Brain read     cc_log 2.5с; review 2.2с; project_state 3.3с; knowledge_base 2.5с
Aug 07 16:50:16 ⚠️  Brain read     cc_log 207с⚠️(>10); review 5.7с; project_state 2.2с; knowledge_base ✗(request_failed)
Aug 07 20:41:43 ✅ Brain read     cc_log 3.1с; review 2.1с; project_state 8.8с; knowledge_base 1.9с
Aug 08 00:42:07 ✅ Brain read     cc_log 2.0с; review 2.1с; project_state 1.9с; knowledge_base 1.7с
Aug 08 04:43:09 ✅ Brain read     cc_log 2.3с; review 1.8с; project_state 2.3с; knowledge_base 2.1с
```

### Распределение (44 прогона)

| документ | размер | успехов | мин | медиана | макс | чтений > 4 с |
|---|---|---|---|---|---|---|
| review | 803 Б | 44/44 | 1.5 с | **1.9 с** | 39 с | 4 (9.1 %) |
| project_state | 58 055 Б | 44/44 | 1.7 с | **2.0 с** | 66 с | 4 (9.1 %) |
| knowledge_base | 95 865 Б | 43/44 | 1.6 с | **2.1 с** | 18.4 с | 3 (7.0 %) |
| cc_log | 410 863 Б | 44/44 | 1.6 с | **2.3 с** | 207 с | 8 (18.2 %) |

Итого: медленных чтений (> 4 с) **19 из 175 успешных (10.9 %)**, отказов **1 из 176 (0.6 %)**.

---

## 3. Ответ на вопрос «отказ и задержка гуляют или сидят на одном документе?» — числом

**ГУЛЯЮТ. Размер ни при чём, и это видно тремя независимыми числами.**

1. **Медианы почти совпадают при разбросе размера в 511 раз.** review 803 Б → 1.9 с,
   cc_log 410 863 Б → 2.3 с. Пятикратный порядок величины по байтам стоит **+0.4 с** медианы.
   А разброс ВНУТРИ одного документа доходит до **×90** (cc_log: медиана 2.3 с, максимум 207 с).
   То есть время чтения определяется не документом, а моментом.

2. **В одном и том же прогоне маленький документ бывает медленнее большого.**
   `Aug 02 20:34: cc_log 17с; project_state 66с` — 58-килобайтный документ читался вчетверо
   дольше 410-килобайтного в ОДНОМ окне. То же в исходной карточке владельца:
   `review 803 Б — 5.7 с` при `project_state 58 КБ — 2.2 с`.
   Если бы решал размер, такое было бы невозможно ни разу; в корпусе это норма эпизода.

3. **Эпизоды — временны́е, а не документные.** Прогонов хотя бы с одним чтением > 4 с — **10 из 44
   (22.7 %)**. «Худший документ эпизода»: cc_log ×5, project_state ×3, review ×2, knowledge_base ×0
   (зато единственный отказ — его). В 2 эпизодах из 10 (Aug 02 04:31, Aug 06 08:41) просели
   ВСЕ ЧЕТЫРЕ документа сразу — этого документное объяснение не допускает вовсе.

Отдельно про cc_log (8 медленных против 3–4 у соседей): он и самый большой, и **читается ПЕРВЫМ**
в списке `_BRAIN_DOCS`. Разделить «первый» и «большой» этим замером нельзя — назвал честно;
но на медиану ни то, ни другое не влияет (2.3 против 1.9 с).

---

## 4. Что на самом деле съедает время — анатомия эпизода 07.08 16:50 (дословно)

Команда: `journalctl -u splinter-health.service --since "2026-08-07 16:40" --until "2026-08-07 17:10" --no-pager`

```
Aug 07 16:41:13 splinter systemd[1]: Starting splinter-health.service - TurboBaby health check (пинг компонентов + пуш владельцу при проблеме)...
Aug 07 16:41:46 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:42:06 splinter python3[730882]: Bridge: echo-слой сбоит (HTTPSConnectionPool(host='script.googleusercontent.com', port=443): Read timed out. (read timeout=14)) — ретрай 2/3
Aug 07 16:42:21 splinter python3[730882]: Bridge: echo-слой сбоит (HTTPSConnectionPool(host='script.googleusercontent.com', port=443): Read timed out. (read timeout=14)) — ретрай 3/3
Aug 07 16:42:38 splinter python3[730882]: Bridge: echo-слой сбоит (HTTPSConnectionPool(host='script.googleusercontent.com', port=443): Read timed out. (read timeout=14)) — ретрай 2/3
Aug 07 16:43:02 splinter python3[730882]: Bridge: echo-слой сбоит (HTTPSConnectionPool(host='script.googleusercontent.com', port=443): Read timed out. (read timeout=14)) — ретрай 2/3
Aug 07 16:43:17 splinter python3[730882]: Bridge: echo-слой сбоит (HTTPSConnectionPool(host='script.googleusercontent.com', port=443): Read timed out. (read timeout=14)) — ретрай 3/3
Aug 07 16:43:21 splinter python3[730882]: Bridge request error (read_doc): HTTP 302
Aug 07 16:43:21 splinter python3[730882]: Bridge read_doc: request_failed — backoff-ретрай 2/3
Aug 07 16:43:49 splinter python3[730882]: Bridge: echo-слой сбоит (... Read timed out. (read timeout=14)) — ретрай 2/3
Aug 07 16:44:08 splinter python3[730882]: Bridge: echo-слой сбоит (... Read timed out. (read timeout=14)) — ретрай 2/3
Aug 07 16:44:23 splinter python3[730882]: Bridge: echo-слой сбоит (... Read timed out. (read timeout=14)) — ретрай 3/3
Aug 07 16:44:31 splinter python3[730882]: Bridge request error (read_doc): HTTP 302
Aug 07 16:44:31 splinter python3[730882]: Bridge read_doc: request_failed — backoff-ретрай 3/3
Aug 07 16:44:58 splinter python3[730882]: Bridge: echo-слой сбоит (... Read timed out. (read timeout=14)) — ретрай 2/3
Aug 07 16:45:58 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:46:30 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:47:03 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:47:05 splinter python3[730882]: Bridge request error (read_doc): HTTP 302
Aug 07 16:47:05 splinter python3[730882]: Bridge read_doc: request_failed — backoff-ретрай 2/3
Aug 07 16:47:39 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:48:13 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:48:47 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:48:50 splinter python3[730882]: Bridge request error (read_doc): HTTP 302
Aug 07 16:48:50 splinter python3[730882]: Bridge read_doc: request_failed — backoff-ретрай 3/3
Aug 07 16:49:43 splinter python3[730882]: Bridge: echo-слой сбоит (HTTP 404 на redirect-echo) — ретрай 2/3
Aug 07 16:50:14 splinter python3[730882]: Bridge request error (read_doc): HTTP 302
Aug 07 16:50:14 splinter python3[730882]: Bridge: 3 подряд транспорт-сбоев — пересоздаю HTTP-сессию (анти-клин)
Aug 07 16:50:16 splinter python3[730882]: ⚠️  Brain read     cc_log 207с⚠️(>10); review 5.7с; project_state 2.2с; knowledge_base ✗(request_failed)
Aug 07 16:50:16 splinter python3[730882]: [push] отправлен
Aug 07 16:50:16 splinter systemd[1]: splinter-health.service: Consumed 2.236s CPU time over 9min 3.114s wall clock time, 108.5M memory peak.
```

Что здесь видно и чего в карточке НЕ было:

* **`2.236s CPU за 9 мин 3 с настенного времени`** — процесс 543 секунды ЖДАЛ сеть. Ни байта
  «медленного разбора», ни следа «износа документа».
* **Отказ knowledge_base стоил не меньше 256 с** (первый ретрай его лестницы 16:45:58 → последний
  `HTTP 302` в 16:50:14), а карточка показала его как `✗(request_failed)` — **без времени вообще**.
  Самый дорогой документ прогона выглядел в отчёте дешевле остальных.
* **Настоящая ошибка — `HTTP 302`, и она в карточку не попала.** `health.py:348` берёт только
  `r.get("error")`, то есть `request_failed`; поле `message` (`"HTTP 302"`) отбрасывается.
* **Анти-клин сработал за 2 секунды до конца** — бесполезно (см. §5, п. 5).

Второй эпизод для сверки (Aug 06 12:41, `cc_log 51с`), команда
`journalctl -u splinter-health.service --since "2026-08-06 12:38" --until "2026-08-06 12:42" --no-pager`:

```
Aug 06 12:40:06 splinter systemd[1]: Starting splinter-health.service ...
Aug 06 12:40:38 splinter python3[685152]: Bridge: echo-слой сбоит (HTTPSConnectionPool(host='script.google.com', port=443): Read timed out. (read timeout=14)) — ретрай 2/3
Aug 06 12:40:53 splinter python3[685152]: Bridge: echo-слой сбоит (HTTPSConnectionPool(host='script.google.com', port=443): Read timed out. (read timeout=14)) — ретрай 3/3
Aug 06 12:41:13 splinter python3[685152]: ⚠️  Brain read     cc_log 51с⚠️(>10); review 1.9с; project_state 1.8с; knowledge_base 1.9с
Aug 06 12:41:13 splinter systemd[1]: splinter-health.service: Consumed 2.047s CPU time over 1min 7.729s wall clock time, 104M memory peak.
```
Те же 2 с CPU, 51 с чтения = два read-timeout по 14 с плюс хопы. Ни один документ, кроме
первого читанного, не пострадал — окно закрылось за минуту.

Настенное время всех 44 прогонов (`journalctl … -g "wall clock"`): норма 10.4–21 с;
выбросы 27.8, 30.1, 30.4, 56.5, 67.7, 75.2, 94.2, 102.6 с и **543.1 с** (07.08 16:50).
CPU во ВСЕХ прогонах — 1.77–2.24 с, то есть ровно константа.

Ретраи в общем логе бота (`splinter.log`, тот же клиент, другие потребители):
`grep -c "backoff-ретрай"` → **2845**; `grep -c "echo-слой сбоит"` → **2537**.
Класс не редкий и не про мозг: последние строки — `get_pending: timeout — backoff-ретрай 2/3`,
`get_pending: request_failed — backoff-ретрай 2/3`.

---

## 5. Код своего пути чтения (пункт 3 ТЗ) — строками, не пересказом

**Повтор ВСЕГО запроса при неуспехе — ЕСТЬ.** Чтение идёт `read_doc` → `_call` → `_durable_request`
с `retry_full=True`:

```
bridge_client.py:439   def _call(self, action: str, **params) -> dict:
bridge_client.py:440       """Выполняет GET запрос к Bridge (read-only → полный ретрай безопасен)."""
bridge_client.py:441       query = {"token": self.token, "action": action, **params}
bridge_client.py:443       return self._durable_request("GET", action, params=query, retry_full=True)
```
```
bridge_client.py:413       max_attempts = max(1, self.retry_attempts) if retry_full else 1
bridge_client.py:416       while True:
bridge_client.py:417           attempt += 1
bridge_client.py:418           data = self._one_exchange(method, action, params=params, body=body)
bridge_client.py:421           self._note_transport(failed=err in ("timeout", "request_failed"))
bridge_client.py:422           if data.get("ok"):
bridge_client.py:423               return data
bridge_client.py:432           if err in ("timeout", "request_failed") and attempt < max_attempts:
bridge_client.py:433               log.warning(f"Bridge {action}: {err} — backoff-ретрай "
bridge_client.py:434                           f"{attempt + 1}/{max_attempts}")
bridge_client.py:435               self._backoff(attempt - 1)
bridge_client.py:436               continue
bridge_client.py:437           return data
```

**Сколько попыток.** Три, значение читается из окружения (сам файл секретов не открывался —
приведён код, который его читает):
```
bridge_client.py:252       self.retry_attempts = _env_num("BRIDGE_RETRY_ATTEMPTS", 3, int)   # попыток всего (1 = без ретраев)
bridge_client.py:253       self.retry_base = _env_num("BRIDGE_RETRY_BASE", 0.6, float)       # base экспоненциальной паузы, сек
bridge_client.py:254       self.retry_jitter = _env_num("BRIDGE_RETRY_JITTER", 0.4, float)   # верх jitter, сек
bridge_client.py:255       self.wedge_limit = _env_num("BRIDGE_WEDGE_LIMIT", 3, int)         # N подряд транспорт-сбоев → новая сессия
```
Живой журнал подтверждает значение в проде: `backoff-ретрай 2/3` и `3/3`.

**Какая пауза.**
```
bridge_client.py:283   def _backoff(self, attempt: int):
bridge_client.py:284       """Экспоненциальная пауза + jitter перед повтором (attempt с 0)."""
bridge_client.py:285       time.sleep(self.retry_base * (2 ** attempt) + random.uniform(0, self.retry_jitter))
```
то есть 0.6–1.0 с перед 2-й попыткой и 1.2–1.6 с перед 3-й. **Пауза здесь несущественна** —
время съедают не паузы, а сами обмены.

**Что уходит наверх после исчерпания.** Строка 437 возвращает результат ПОСЛЕДНЕГО обмена —
`{"ok": False, "error": "request_failed", "message": "HTTP 302"}` (это `_one_exchange`):
```
bridge_client.py:345       if r.status_code >= 400 or r.status_code in _REDIRECT_CODES:
bridge_client.py:346           log.error(f"Bridge request error ({action}): HTTP {r.status_code}")
bridge_client.py:347           return {"ok": False, "error": "request_failed",
bridge_client.py:348                   "message": f"HTTP {r.status_code}"}
```
а `health.py:348` печатает только `error`:
```
health.py:346          if not r.get("ok"):
health.py:348              parts.append(label + " ✗(" + str(r.get("error", "?")) + ")")
```
→ владелец видит `✗(request_failed)`; «HTTP 302» и потраченные минуты не показываются.

**Второй, ВЛОЖЕННЫЙ повтор — плечо эхо-редиректа:**
```
bridge_client.py:293       for attempt in range(max(1, self.retry_attempts)):
bridge_client.py:294           if attempt:
bridge_client.py:295               log.warning(f"Bridge: echo-слой сбоит ({last_err}) — ретрай {attempt + 1}/{self.retry_attempts}")
bridge_client.py:296               self._backoff(attempt - 1)
bridge_client.py:298           r = self._session.get(url, timeout=self.timeout, allow_redirects=False)
bridge_client.py:302           if r.status_code == 404 or r.status_code >= 500:
```
и цепочка хопов:
```
bridge_client.py:85    _REDIRECT_CODES = (301, 302, 303, 307, 308)
bridge_client.py:322       while r.status_code in _REDIRECT_CODES and hops < 6:
bridge_client.py:327           r = self._fetch_redirect_target(cur_url)
bridge_client.py:328           hops += 1
```

**ГЛАВНОЕ ЧИСЛО ЭТОГО ПУНКТА: суммарного дедлайна у чтения НЕТ.** Потолок одного `_call` —
3 попытки × (1 обмен + 6 хопов × 3 попытки эхо) × `timeout` = **до 57 × timeout**.
Для cc_log (`timeout = 10 + 4 = 14 с`) это **до ≈ 798 с**, для knowledge_base (`40 + 4 = 44 с`) —
**до ≈ 2508 с (42 минуты)**. При этом сам зонд считает свой бюджет равным `порог + 4 с`:
```
health.py:47   _BRAIN_PROBE_MARGIN = 4   # сек сверх порога: «дочитал, но медленно» vs «не дочитал (timeout)»
```
Наблюдённый максимум — 207 с, то есть до потолка ещё далеко: класс не выбран, а только начат.

**Пятый факт — анти-клин на этом пути бесполезен.**
```
bridge_client.py:277           if self._consec_transport_fails >= self.wedge_limit:
bridge_client.py:280               self._new_session()
```
`wedge_limit` = 3, а попыток тоже 3, и счётчик живёт в ЭКЗЕМПЛЯРЕ клиента, который
`check_brain_latency` создаёт заново на каждый документ (`health.py:339`). Значит порог достижим
ровно на последней попытке — новая сессия рождается уже после провала. Живое подтверждение:
`16:50:14 … пересоздаю HTTP-сессию` — за 2 секунды до отчёта.

---

## 6. Пункт 4 ТЗ: есть ли на СВОЕЙ полосе повтор GET целиком

**ЕСТЬ.** Слово: *есть*. Строка: `bridge_client.py:443` —
`return self._durable_request("GET", action, params=query, retry_full=True)`,
исполняется лестницей `bridge_client.py:413` (`max_attempts = max(1, self.retry_attempts) if retry_full else 1`)
и `bridge_client.py:432–436` (условие + backoff + `continue`).

Когда появилось (`git log --oneline -S"retry_full" -- bridge_client.py`):
```
d78d5fa отказ расписки на write-POST больше не пересылается: исход неизвестен — читать факт, а не повторять
4f892d7 fix(bridge): DURABLE-слой против intermittent 404 Google (хвост §7, инцидент 07.07 18:44–20:00)
```
и (`-S"_fetch_redirect_target"`) — тем же коммитом:
```
4f892d7 fix(bridge): DURABLE-слой против intermittent 404 Google (хвост §7, инцидент 07.07 18:44–20:00)
```

То есть **у нашей полосы оба повтора — и внешний (весь GET), и внутренний (плечо эхо) — стоят с
07.07.2026**, за месяц до соседской правки от 06.08. На чужую полосу не ходили; здесь закрывать
нечего. Закрывать нужно ОБРАТНОЕ: не «добавить повтор», а **ограничить его суммарным дедлайном** —
именно перемножение двух уже существующих лестниц и даёт 207 с при заявленных 14 с.

---

## 7. Пункт 5 ТЗ — что мерить дальше, одной строкой

**Мерить не одно суммарное время чтения, а КАЖДЫЙ обмен внутри `_call`: (номер попытки, номер
хопа, код ответа, длительность) — если первый обмен отдаёт 200 за ~2 с, а минуты набегают на
наших повторах, виноват клиент; если каждый хоп отвечает 302/404 и 200 не приходит вовсе,
отвечает мост.**

---

## 8. Границы и честные пределы этого замера

* Пять собственных проб попали в тихое окно: они воспроизводят норму (≈2 с), эпизод — нет.
  Вывод о разбросе держится на живом корпусе того же пути (44 прогона, §2), а не на пятёрке.
* Разделить у cc_log вклад «самый большой» и «читается первым» этим замером нельзя.
* Причина отказов моста (почему эхо-слой отдаёт 404/302 сериями) НЕ установлена — измерено
  только то, как на это реагирует наш клиент.
* Ничего не удалялось, не перезапускалось, не писалось в рабочие таблицы. Единственная запись —
  этот артефакт и строка в `cc_log`.
