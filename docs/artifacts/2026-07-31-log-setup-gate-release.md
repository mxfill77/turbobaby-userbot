# Ворота клиентского контура: применение 3dd040b к userbot/moderbot — материал для «да»

Заход 3-й, 31.07.2026 09:19 (местное, UTC+7). Замер read-only, ничего не применялось.
Предыдущие два захода этот же файл вели; здесь всё перемерено заново, **две поправки к
прошлому разбору внизу** (§ «Поправки»).

## Итог одной строкой

ШАГ 1 **основание подтвердил**: в клиентском замыкании меняются ровно два файла — машинерия
ротации логов и одна строка докстринга. Ни промпт, ни цены, ни текст ответа не затронуты.
Применение упирается **не в ворота, а в мандат сессии**: рестарт живых клиентских ботов — красный
класс `kill`, и преамбула прямо запрещает трогать `userbot`/`moderation_bot`. Нужен один «да».

## ШАГ 1 — что на самом деле применяется

**Коммит `3dd040b` сам по себе к делу не относится:** он трогает один файл журнала
(`docs/artifacts/journal/2026-07-30-204333-done.md`, +14 строк). Применяется не он, а **состояние
диска целиком** — так и задумано, дословно из кода:

> `client_contour.release_reason()`: «решение принимается ПО КОММИТУ: рестарт применяет состояние
> диска целиком, "частично выкатить" нельзя, значит и одобрять надо коммит».

Боты подняты 30.07 13:15:03/13:15:07. Соседние коммиты: `fd5388f` (30.07 13:10) и `c00bb08`
(30.07 13:32) — значит живой код ботов = **`fd5388f`**, 34 коммита назад. Отслеживаемое дерево
равно `3dd040b` ровно (`git status --porcelain -uno` пуст), рестарт поднял бы именно его.

Боты импортируют только своё замыкание — 12 файлов (`client_contour.closure()`, `ok=True`):

```
booking_draft.py  delivery.py        lesson_router.py  log_setup.py
moderation_bot.py moderation_core.py moderation_ipc.py pricing.py
suggest.py        trainer.py         trainer_log.py    userbot_listen.py
```

Дифф ПО ЭТИМ 12 файлам, `fd5388f` → `3dd040b`:

```
 lesson_router.py |   2 +-
 log_setup.py     | 328 +++++++++++++++++++++++++++++++++++++++++++++++++++----
 2 files changed, 308 insertions(+), 22 deletions(-)
```

* **`lesson_router.py`** — одна строка ДОКСТРИНГА, смена имени модели в комментарии:
  `Fable5→fallback` → `THINKER_MODEL→фолбэк`. Исполняемого кода не меняет.
* **`log_setup.py`** — 6 хунков, все про ротацию: вторая ступень `_copytruncate` (перекат там, где
  `rename` падает WinError 32), `_signal_append`/`_report_rotate_note` (ROTATE-NOTE в файл-сигнал),
  `_reopen` (базовый `doRollover` закрывает поток до падения), двухступенчатый
  `SafeRotatingFileHandler.doRollover`, ручка `LOG_ROLLOVER_RETRY_SEC`.

**Независимая проверка «нет клиентского текста».** Не на глаз, а перечнем: все строковые литералы,
добавленные/удалённые диффом `log_setup.py` (26 штук, полный список) — это форматы логов
(`"%(asctime)s | %(message)s"`), имена ручек (`LOG_ROLLOVER_RETRY_SEC`, `TURBOBABY_LOG_OWNER`),
имена файлов (`userbot.log`, `log_rotation_errors.log`, `rc_remote_control.log`), режимы открытия
(`"a"`, `"r+b"`), регэксп `[^0-9A-Za-z_-]+` и служебные сообщения ротации
(`"ROTATE-FAIL %s | %s | %s: %s%s"`, `"rename отказал (%s), перекат прошёл copytruncate"`).
Ни одной строки, адресованной клиенту; ни числа, похожего на цену; ни куска промпта.

Не тронуты вовсе: `suggest.py`, `pricing.py`, `booking_draft.py`, `moderation_core.py`,
`moderation_ipc.py`, `trainer.py`, `trainer_log.py`, `delivery.py`, `userbot_listen.py`,
`moderation_bot.py`.

**Вывод ШАГА 1: основание верно — это ротация, а не текст для клиентов. Обратного не нашлось.**
Полный дифф обоих файлов — в конце этого файла, § «Дифф целиком».

## Почему ворота вообще сработали (они не ошиблись)

```
release_reason('3dd040b') = None      owner_approved = False
trainer_status            = «прогона не было»
closure ok=True files=12  is_client('log_setup.py') = True
```

`log_setup.py` **действительно** лежит в транзитивном import-замыкании обоих клиентских процессов,
поэтому признак честно назвал его клиентским. Ворота отработали ровно по своему назначению —
чинить тут нечего, нужно основание пропуска.

Оснований ровно два: `owner` («да» владельца) и `trainer` (зелёный прогон тренажёра). Тренажёр
задание само называет неприменимым (он проверяет качество ответов), и это верно. Остаётся `owner`.

**Записать это «да» за владельца я не могу и не буду.** `client_contour.approve()` технически
доступен, но модуль прямо предупреждает: «кто может писать этот файл, тот открывает клиентский
контур в обход "да" владельца». Ворота держатся на том, что ключ ровно один; подделать ключ — это
не «снять одну остановку», а снести правило. Границы задания требуют ворота не ослаблять.

## ШАГ 2 — кого перезапускает и что прервётся

| процесс | PID | старт | код |
|---|---|---|---|
| `userbot_listen.py` | **1656** | 30.07.2026 13:15:03 | fd5388f |
| `moderation_bot.py` | **9988** | 30.07.2026 13:15:07 | fd5388f |
| `pc_agent.py` | **6612** | 30.07.2026 19:53:11 | — |

`pc_agent` в списке не по ошибке: правило самообновления (`pc_orchestrator.py:3646`) на
`log_setup.py` даёт `("userbot", "moderbot", "pc_agent")` — хендлер вешается на импорте, значит
новый порог подхватывается ТОЛЬКО рестартом. Перезапуск `pc_agent` роняет на несколько секунд
управление с телефона.

### Состояние диалога на 09:19

Последнее сообщение клиента — **01:23:53 UTC (08:23 местного)**, @olaaaaayy55: «Здравствуйте
хотела бы арендовать у вас мопед…». По нему построен черновик **#372 → IPC**. После этой строки
клиентского трафика в `userbot.log` **нет вовсе** — тишина 56 минут.

Черновик висит нерешённым, но **рестарт его не потеряет**: `moderation_ipc` — это sqlite на диске
(`moderation_ipc.db`, таблица `drafts` с колонкой `status`, индекс `idx_status`), очередь
переживает перезапуск и разбирается заново по `status='new'`. Цена рестарта здесь — секунды
недоступности, а не обрыв очереди. И `SUGGEST_TEST_MODE=True` означает, что клиенту всё равно
никто не пишет автоматически: #372 ждёт человека, а не бота.

## ШАГ 4 — второй слой на месте

Значение прочитано **не из файла конфига**, а из строки, которую каждый процесс напечатал **о
самом себе в момент старта**. На Windows блок окружения фиксируется при спавне и после не
меняется, поэтому стартовая строка и есть окружение процесса. (Прямое чтение PEB чужого процесса
штатными средствами Windows недоступно, а `.env` читать запрещено — класс `env`.)

```
userbot_listen.py  PID 1656 (спавн 13:15:03):
  2026-07-30T06:15:05.311758+00:00 | SUGGEST ВКЛЮЧЁН (TEST_MODE=True, mod_group=-5031790861,
                                     bot-режим=да, лимиты 6/ч 15/д).     # 06:15 UTC = 13:15 местного
moderation_bot.py  PID 9988 (спавн 13:15:07):
  2026-07-30 13:15:08,642 | moderation_bot ЗАПУСК (TEST_MODE=True, mod_chat=-5031790861).
                            Клиенту не пишу; решения — в IPC.
```

**У обоих `TEST_MODE=True` — слой держит.** Не трогал и трогать не буду.

## ШАГ 5 — ротация: что доказано без рестарта

* Голдены нового кода зелёные: `venv/Scripts/python.exe -m unittest test_log_setup` →
  **Ran 34 tests, OK** (2.206 с), включая ветки отказа `rename`, отката на `copytruncate` и
  backoff-паузы.
* Запись в лог цен проходит: `pricing.log` = **12 612 байт**, последняя строка сегодня 08:25:11
  (`quote ok bike=VULCAN 650CC S PHUKET 5065 … day=764 total=22920`).
* Потолок задан и он конечный: `LOG_MAX_BYTES` 5 МБ × (`LOG_BACKUPS` 3 + 1) ≈ **20 МБ на лог**
  (`log_setup.py:44-46,60-62`).
* Бесконтрольный рост — исторический, и он уже уехал в архив: `pricing.2026-07-22.log` =
  **23 302 269 байт** (та самая цифра, которую докстринг `_copytruncate` называет как замер 31.07).
* Отказов ротации не зафиксировано: `log_rotation_errors.log` **не существует**.

Честная оговорка: живые боты пока на СТАРОМ `log_setup` (`fd5388f`), поэтому вторая ступень
`copytruncate` в них ещё не активна — это ровно то, что даст рестарт. `userbot.log` сейчас
**2 836 869 байт**, до порога 5 МБ ещё есть запас, так что срочности «вот-вот сорвётся» нет.

## Поправки к разбору 09:07 (заход 2)

1. **«Черновик #372 висит — в `moderation_bot.log` после него нет ни одной строки решения»** —
   вывод был получен из лога, который решений НЕ ПИШЕТ ВООБЩЕ. В файле 7033 строки, из них 265 —
   старт/`lock`, остальные — сетевой шум PTB/APScheduler, и последняя такая строка датирована
   **28.07**. Отсутствие «строки решения» после #372 не значит ничего: её там не было бы и в
   штатной работе. Класс: «отрицание по логу, который такого не логирует».
2. **«Рестарт `moderation_bot` сейчас — это обрыв модерации на неотвеченном клиенте»** — перебор.
   Очередь дисковая (sqlite `drafts`), рестарт её не теряет. Формулировка завышала цену действия.

Сам вывод обоих заходов («без "да" не применяю») от поправок не меняется — меняется цена вопроса:
она **ниже**, чем была описана.

## Что нужно от владельца

Одно «да» на класс `kill` — рестарт PID **1656** и **9988** (и **6612** по правилу
самообновления). Это же «да» и есть штатное основание `owner` для ворот на коммит `3dd040b`;
само правило при этом не трогается.

Откат, если что: `git checkout fd5388f` и поднять ботов тем же способом.

---

## Дифф целиком

### `lesson_router.py`

```diff
@@ -419,7 +419,7 @@ def _parse_lesson_class_json(text):
 
 
 def _default_lesson_thinker(prompt):
-    """Боевой думатель классификатора = pc_orchestrator._thinker_exec (тот же кондуктор Fable5→fallback,
+    """Боевой думатель классификатора = pc_orchestrator._thinker_exec (тот же кондуктор THINKER_MODEL→фолбэк,
     read-only, ничего не исполняет). Ленивый импорт: тяжёлый pc_orchestrator тянем ТОЛЬКО на реальном
     вызове (и разрываем цикл импорта — pc_orchestrator сам импортит lesson_router). Сбой → None."""
     import pc_orchestrator
```

### `log_setup.py`

Полный текст диффа — ниже (генерируется командой
`git diff fd5388f 3dd040b -- log_setup.py`):

```diff
diff --git a/log_setup.py b/log_setup.py
index 4d24db0..e7882ed 100644
--- a/log_setup.py
+++ b/log_setup.py
@@ -2,7 +2,7 @@
 """
 log_setup.py — единая ротация логов ПК-контура + РАЗВЕДЕНИЕ тестовых и боевых логов.
 
-ДВЕ БОЛИ, которые лечит модуль (обе — живой факт аудита 22:27):
+ТРИ БОЛИ, которые лечит модуль (первые две — живой факт аудита 22:27, третья — 31.07.2026):
 
 1) РОТАЦИИ НЕ БЫЛО НИГДЕ. Все модули вешали голый `logging.FileHandler` — файл рос вечно:
    pc_agent.log 28.7 МБ, pricing.log 22.2 МБ, pc_orchestrator.log 11.2 МБ. На диске C: при этом
@@ -13,18 +13,44 @@ log_setup.py — единая ротация логов ПК-контура + Р
    настоящих действий владельца, и они попали в разбор «последних 20 Allow» как реальные
    события. Разведение здесь: под тестом ЛЮБОЙ лог уезжает в temp, боевой файл не трогается.
 
+3) ОДИН ФАЙЛ — ТРИ ПРОЦЕССА, И ПЕРЕКАТ НЕ ПРОХОДИЛ НИКОГДА. `pricing.py` — модуль-библиотека:
+   он живёт ВНУТРИ `userbot_listen`, `moderation_bot` и `pc_orchestrator` (все три тянут
+   `suggest`), и каждый вешал СВОЙ `RotatingFileHandler` на ОДИН `pricing.log`. На Windows
+   перекат = `os.rename` боевого файла; файл, открытый другим процессом, не переименовывается
+   (WinError 32), исключение уходит в `Handler.handleError` — у демона без stderr в никуда, —
+   а САМА ЗАПИСЬ ТЕРЯЕТСЯ. Замер 31.07.2026: `pricing.log` замер на 23 302 269 байт со штампом
+   22.07 21:39 — девять суток КАЖДАЯ строка котировки молча выбрасывалась, а «лог не растёт»
+   выглядело как здоровье. Проба тем же днём: `pricing.log` и `delivery.log` заняты другим
+   процессом, `dispatch_notify.log`/`pretool_guard.log` свободны.
+
+   Лечится ЗДЕСЬ, двумя независимыми механизмами:
+   * `handler_path` — файл РАЗВОДИТСЯ ПО ВЛАДЕЛЬЦУ: канонический `pricing.log` принадлежит
+     процессу-хозяину (см. `log_owner`), любой ДРУГОЙ процесс пишет `pricing.<владелец>.log`.
+     Один файл — один держатель, спорить за rename больше некому;
+   * `_copytruncate` — перекат ВСЁ-ТАКИ ПРОХОДИТ там, где `rename` невозможен;
+   * `SafeRotatingFileHandler` — отказ переката больше НЕ проглатывается и не стоит записей.
+
+   Разведение по владельцу лечит только тех, кого можно развести. Один и тот же входной скрипт,
+   запущенный ДВАЖДЫ (хуки `dispatch_notify.py` бегут по нескольку разом), получает одну и ту же
+   кличку и снова спорит за канон — имя тут помочь не может в принципе. Для этого случая и живёт
+   вторая ступень: `_copytruncate`.
+
 Признак теста берём ПО ФАКТУ окружения, а не по вежливой договорённости «не забудь выставить»:
 TESTING=1 (общий рубильник репо, ставится `test_isolation`), TURBOBABY_TEST_LOGS=1 (явный
 переключатель только логов) или PYTEST_CURRENT_TEST (взводит сам pytest). Порядок наследования
 env вниз по дереву процессов делает признак верным и для subprocess-тестов (гард запускается
 именно так).
 
-Пороги настраиваются env: LOG_MAX_BYTES (по умолчанию 5 МБ), LOG_BACKUPS (по умолчанию 3).
+Пороги настраиваются env: LOG_MAX_BYTES (по умолчанию 5 МБ), LOG_BACKUPS (по умолчанию 3),
+LOG_ROLLOVER_RETRY_SEC (пауза перед повтором сорвавшегося переката, по умолчанию 300 с).
 Итого потолок на один лог = MAX × (BACKUPS + 1) ≈ 20 МБ.
 """
 
 import os
+import re
 import sys
+import time
+import shutil
 import logging
 import tempfile
 from logging.handlers import RotatingFileHandler
@@ -33,9 +59,25 @@ HERE = os.path.dirname(os.path.abspath(__file__))
 
 DEFAULT_MAX_BYTES = 5 * 1024 * 1024
 DEFAULT_BACKUPS = 3
+DEFAULT_ROLLOVER_RETRY_SEC = 300
 
 TEST_PREFIX = "turbobaby_TESTING_"
 
+# Общий файл-сигнал: сюда уходит КАЖДЫЙ отказ переката, из любого процесса. Смысл — чтобы
+# «ротация не работает» было видно в ОДНОМ месте, а не только внутри распухшего лога, который
+# ровно поэтому никто и не открывает.
+ROTATE_ERROR_LOG = "log_rotation_errors.log"
+
+# Владелец канонического имени лога — там, где имя файла НЕ совпадает с именем входного скрипта.
+# Две живых пары; всё остальное совпадает (pc_agent.py→pc_agent.log и т.д.), и голден
+# TestCanonicalLogNameHasOneOwner держит этот список честным.
+LOG_OWNERS = {
+    "userbot.log": "userbot_listen",
+    "rc_remote_control.log": "rc_supervisor",
+}
+
+_OWNER_SAFE_RE = re.compile(r"[^0-9A-Za-z_-]+")
+
 
 def _int_env(name, default):
     try:
@@ -52,6 +94,10 @@ def backups():
     return _int_env("LOG_BACKUPS", DEFAULT_BACKUPS)
 
 
+def rollover_retry_sec():
+    return _int_env("LOG_ROLLOVER_RETRY_SEC", DEFAULT_ROLLOVER_RETRY_SEC)
+
+
 def _started_as_test_runner():
     """True ⇔ САМ ПРОЦЕСС запущен как тест-раннер (`python -m unittest …`, `pytest …`).
 
@@ -93,34 +139,123 @@ def is_test_context(env=None):
 def log_path(filename, env=None):
     """Куда РЕАЛЬНО писать лог `filename`. Боевой прогон → файл в репо; тест → одноимённый файл
     в temp с префиксом. Абсолютный путь на входе уважаем (берём только имя для тест-ветки)."""
+    filename = os.fspath(filename)
     name = os.path.basename(filename)
     if is_test_context(env):
         return os.path.join(tempfile.gettempdir(), TEST_PREFIX + name)
     return filename if os.path.isabs(filename) else os.path.join(HERE, name)
 
 
-def rotating_handler(filename, fmt="%(asctime)s | %(message)s", level=logging.INFO, env=None):
-    """RotatingFileHandler по разрешённому пути. Падение на открытии файла НЕ роняет вызывающего
-    (лог вторичен): вернём None, и модуль просто останется без файлового хендлера."""
-    path = log_path(filename, env)
+# ─────────────── РАЗВЕДЕНИЕ ФАЙЛА ПО ПРОЦЕССАМ (боль 3) ──────────────────────────────────────
+
+def owner_tag(argv0=None, env=None):
+    """Кличка ПРОЦЕССА — по имени входного скрипта (`userbot_listen`, `moderation_bot`,
+    `pc_orchestrator`). Именно она, а не PID: PID даёт новый файл на каждый рестарт и через
+    неделю каталог не читается, а имя входа стабильно и сразу отвечает «чей это лог».
+    Перекрывается env `TURBOBABY_LOG_OWNER` — для запусков через обёртку/`-c`, где argv[0]
+    ничего не говорит."""
+    e = os.environ if env is None else env
     try:
-        h = RotatingFileHandler(path, maxBytes=max_bytes(), backupCount=backups(),
-                                encoding="utf-8")
+        forced = (e.get("TURBOBABY_LOG_OWNER") or "").strip()
+        raw = forced or os.path.basename(os.fspath(
+            sys.argv[0] if argv0 is None else argv0) or "")
+        if not raw or raw.startswith("-"):          # `python -c …`, интерактив — владельца нет
+            return "misc"
+        stem = os.path.splitext(raw)[0]
+        if stem == "__main__":                      # `python -m unittest` и подобные
+            return "misc"
+        return _OWNER_SAFE_RE.sub("-", stem).strip("-_").lower() or "misc"
     except Exception:
-        return None
-    h.setFormatter(logging.Formatter(fmt))
-    h.setLevel(level)
-    return h
+        return "misc"
 
 
-def rotate_if_needed(path, limit=None, keep=None):
-    """Ротация для писателей БЕЗ модуля logging (гард пишет строку через open(..., 'a')).
-    Сдвигает path.N → path.N+1 и path → path.1, оставляя `keep` бэкапов. Никогда не бросает."""
-    lim = max_bytes() if limit is None else limit
-    n = backups() if keep is None else keep
+def log_owner(name):
+    """Чей КАНОНИЧЕСКОЕ (без суффикса) имя файла. По умолчанию — одноимённый скрипт
+    (`pc_agent.log` → `pc_agent`), исключения объявлены в LOG_OWNERS. Для файла, который никакому
+    входному скрипту не принадлежит (`pricing.log` — модуль-библиотека, процесса `pricing` нет),
+    каноническое имя не занимает НИКТО: все писатели уходят под суффикс, и спорить за rename
+    становится некому."""
+    base = os.path.basename(os.fspath(name))
+    declared = LOG_OWNERS.get(base.lower())
+    if declared:
+        return declared
+    return os.path.splitext(base)[0].lower()
+
+
+def handler_name(name, owner=None):
+    """Имя файла для ДЕРЖАЩЕГО хендлера этого процесса: хозяину — каноническое, всем прочим —
+    `<имя>.<владелец>.log`."""
+    base = os.path.basename(os.fspath(name))
+    who = owner or owner_tag()
+    if who == log_owner(base):
+        return base
+    stem, ext = os.path.splitext(base)
+    return "%s.%s%s" % (stem, who, ext)
+
+
+def handler_path(filename, env=None, owner=None):
+    """Путь для файлового хендлера — `log_path` + разведение по владельцу.
+
+    Разводим ТОЛЬКО здесь, а не в `log_path`, и это не мелочь. Держит файл (и мешает rename)
+    лишь тот, кто ОТКРЫЛ ЕГО НАДОЛГО, — то есть хендлер. Писатели в режиме open→write→close
+    (гард: `_log` → `open(p,'a')`; `dispatch_notify._write_session_metrics` → дозапись в
+    `pc_orchestrator.log`) файл не держат и ротации не мешают, поэтому им НЕЛЬЗЯ менять адрес:
+    иначе единая лента аудита гарда развалилась бы на файл-на-процесс без всякой пользы."""
+    p = os.fspath(filename)
+    head, base = os.path.split(p)
+    split = handler_name(base, owner)
+    return log_path(os.path.join(head, split) if head else split, env)
+
+
+# ─────────────── ОТКАЗ ПЕРЕКАТА НЕ ПРОГЛАТЫВАЕТСЯ ────────────────────────────────────────────
+
+_ROTATE_FAILURES = []            # последние отказы переката этого процесса (для диагностики)
+_ROTATE_FAILURES_MAX = 50
+
+
+def rotation_failures():
+    """Копия списка отказов переката в ЭТОМ процессе. Пусто ⇔ перекат ни разу не срывался."""
+    return list(_ROTATE_FAILURES)
+
+
+def _copytruncate(path, dest):
+    """ПЕРЕКАТ ТАМ, ГДЕ `rename` НЕВОЗМОЖЕН: архив — копией, боевой файл — усечением на месте.
+
+    Почему это вообще работает, когда `os.replace` падает WinError 32. Python открывает лог
+    через `_wopen`/`_SH_DENYNO`, то есть отдаёт соседям `FILE_SHARE_READ|FILE_SHARE_WRITE`,
+    но НЕ `FILE_SHARE_DELETE`. Переименование требует у файла права DELETE — его нет, отсюда
+    отказ. А чтение и усечение просят ровно то, что соседи разрешили, — и проходят.
+    Замер 31.07.2026 на боевом `pricing.log` (держали три процесса): `os.replace` → WinError 32,
+    `copy2`+`truncate(0)` → успех, 23 302 269 байт уехали в архив.
+
+    Почему это ЛЕЧИТ, а не просто «уменьшает файл». Чужой хендлер держит СВОЙ поток в режиме
+    `"a"`: после усечения его `shouldRollover` меряет `seek(0,2)` → 0 < порога → перекат больше
+    НЕ ЗАПРАШИВАЕТСЯ, и запись ложится в файл. Стенд 31.07: до усечения строка терялась
+    (`handleError`, `Message: 'quote before truncate'`), после — легла. То есть соседей,
+    застрявших в вечном отказе, это поднимает БЕЗ их рестарта.
+
+    РАЗМЕН НАЗВАН ВСЛУХ: между `copy2` и `truncate` есть окно в миллисекунды, и строки, попавшие
+    в него, не попадут ни в архив, ни в новый файл. Это осознанно: терять миллисекунду записей
+    раз в 5 МБ несопоставимо дешевле, чем терять ВСЕ записи вечно, как было девять суток.
+    Возвращает (переложили?, исключение|None). Не бросает."""
+    try:
+        shutil.copy2(path, dest)
+        with open(path, "r+b") as f:
+            f.truncate(0)
+        return True, None
+    except Exception as exc:
+        return False, exc
+
+
+def _rotate(path, lim, n):
+    """Сам сдвиг path.N → path.N+1 и path → path.1. Возвращает (сдвинули?, исключение|None) —
+    вызывающий решает, докладывать об отказе или молчать. Не бросает.
+
+    Если `rename` не проходит (файл держит другой процесс), пробуем `_copytruncate`: перекат
+    важнее способа переката. Наружу в этом случае уходит успех, а не отказ."""
     try:
         if not os.path.isfile(path) or os.path.getsize(path) <= lim:
-            return False
+            return False, None
         oldest = "%s.%d" % (path, n)
         if os.path.isfile(oldest):
             os.remove(oldest)
@@ -128,7 +263,158 @@ def rotate_if_needed(path, limit=None, keep=None):
             src, dst = "%s.%d" % (path, i), "%s.%d" % (path, i + 1)
             if os.path.isfile(src):
                 os.replace(src, dst)
-        os.replace(path, path + ".1")
-        return True
+        try:
+            os.replace(path, path + ".1")
+        except OSError as exc:
+            ok, exc2 = _copytruncate(path, path + ".1")
+            if not ok:
+                # докладываем ИСХОДНУЮ причину (отказ rename), а не вторичную: она диагностична
+                return False, exc
+            _report_rotate_note(path, "rename отказал (%s), перекат прошёл copytruncate"
+                                      % exc.__class__.__name__)
+        return True, None
+    except Exception as exc:
+        return False, exc
+
+
+def _signal_append(msg):
+    """Дописать строку в общий файл-сигнал. САМ НЕ РОТИРУЕТ — иначе `_rotate` → `_report_*` →
+    `_rotate` замкнулись бы в петлю. Никогда не бросает."""
+    try:
+        with open(log_path(ROTATE_ERROR_LOG), "a", encoding="utf-8") as f:
+            f.write(msg + "\n")
     except Exception:
-        return False
+        pass
+
+
+def _report_rotate_note(path, what):
+    """Перекат ПРОШЁЛ, но обходным путём. Не отказ — в `rotation_failures()` не кладём и stderr
+    не шумим; но в файл-сигнал строка идёт: у copytruncate есть окно потери в миллисекунды, и
+    знать, что перекат идёт через него, надо. Объём — одна строка на 5 МБ лога."""
+    msg = "ROTATE-NOTE %s | %s | %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), path, what)
+    _signal_append(msg)
+    return msg
+
+
+def _report_rotate_failure(path, exc, extra=""):
+    """Отказ переката ВИДЕН: строка в общий `log_rotation_errors.log`, строка в stderr и запись
+    в памяти процесса (`rotation_failures()`). Прежнее поведение — голый `except: pass` — и
+    сделало девятисуточную немоту `pricing.log` незаметной. Сам никогда не бросает: лечение не
+    имеет права стоить дороже болезни."""
+    msg = "ROTATE-FAIL %s | %s | %s: %s%s" % (
+        time.strftime("%Y-%m-%d %H:%M:%S"), path, exc.__class__.__name__, exc, extra)
+    try:
+        _ROTATE_FAILURES.append(msg)
+        del _ROTATE_FAILURES[:-_ROTATE_FAILURES_MAX]
+    except Exception:
+        pass
+    try:
+        _rotate(log_path(ROTATE_ERROR_LOG), 512 * 1024, 1)   # молча: петлю рвёт _signal_append
+    except Exception:
+        pass
+    _signal_append(msg)
+    try:
+        if sys.stderr is not None:
+            sys.stderr.write(msg + "\n")
+    except Exception:
+        pass
+    return msg
+
+
+class SafeRotatingFileHandler(RotatingFileHandler):
+    """RotatingFileHandler, у которого сорвавшийся перекат ВИДЕН и НЕ СТОИТ ЗАПИСЕЙ.
+
+    Штатный ведёт себя так: `emit` → `doRollover` кидает WinError 32 → `except` → `handleError`
+    → строка потеряна. И так на КАЖДОЙ записи, пока файл держит чужой процесс, то есть вечно.
+
+    Здесь у переката ДВЕ ступени, и первая почти всегда достаточна:
+    1) `rename` — штатный, дешёвый;
+    2) `_copytruncate` — когда `rename` невозможен (файл держит сосед). Перекат ВСЁ-ТАКИ
+       ПРОХОДИТ: порог соблюдён, бэкап на месте, и застрявшие соседи оживают без рестарта.
+       В файл-сигнал уходит ROTATE-NOTE — обходной путь не прячем.
+
+    Если сорвались ОБЕ: (а) запись всё равно ложится в файл — растущий лог лучше немого;
+    (б) ROTATE-FAIL в файл-сигнал, в stderr и в сам лог (первый раз за эпизод); (в) следующая
+    попытка откладывается на `LOG_ROLLOVER_RETRY_SEC` — иначе перекат дёргался бы на каждой
+    строке. Размен назван вслух: в этом (теперь редком) случае файл РАСТЁТ сверх порога —
+    тихая потеря диагностики хуже видимого роста, а видимый рост чинится по сигналу."""
+
+    def __init__(self, *args, **kwargs):
+        RotatingFileHandler.__init__(self, *args, **kwargs)
+        self._rollover_retry_after = 0.0
+        self._rollover_failed = False
+
+    def _reopen(self):
+        """Базовый `doRollover` закрывает поток ДО падения `rename` — вернуть его на место."""
+        if self.stream is None:
+            try:
+                self.stream = self._open()
+            except Exception:
+                self.stream = None
+
+    def doRollover(self):
+        now = time.time()
+        if now < self._rollover_retry_after:
+            return                        # пауза после отказа: про него уже сказано, не шумим
+        try:
+            RotatingFileHandler.doRollover(self)
+        except Exception as exc:
+            # Ступень 2. Базовый уже сдвинул бэкапы и освободил слот `.1` — упал он на самом
+            # переименовании боевого файла, ровно туда и кладём копию.
+            ok = False
+            if self.backupCount > 0:
+                ok, _ = _copytruncate(self.baseFilename, self.baseFilename + ".1")
+            if ok:
+                self._reopen()
+                self._rollover_failed = False
+                self._rollover_retry_after = 0.0
+                _report_rotate_note(self.baseFilename,
+                                    "rename отказал (%s), перекат прошёл copytruncate"
+                                    % exc.__class__.__name__)
+                return
+            self._rollover_retry_after = now + rollover_retry_sec()
+            first = not self._rollover_failed          # маркер В САМ ЛОГ — раз за эпизод
+            self._rollover_failed = True
+            msg = _report_rotate_failure(
+                self.baseFilename, exc,
+                " | не прошли ни rename, ни copytruncate; лог продолжит расти, повтор через %d с"
+                % rollover_retry_sec())
+            self._reopen()
+            if first and self.stream is not None:
+                try:
+                    self.stream.write("!! %s\n" % msg)
+                    self.stream.flush()
+                except Exception:
+                    pass
+        else:
+            self._rollover_failed = False
+            self._rollover_retry_after = 0.0
+
+
+def rotating_handler(filename, fmt="%(asctime)s | %(message)s", level=logging.INFO, env=None,
+                     owner=None):
+    """Ротируемый хендлер по разрешённому пути, РАЗВЕДЁННОМУ по процессу-владельцу. Падение на
+    открытии файла НЕ роняет вызывающего (лог вторичен): вернём None, и модуль просто останется
+    без файлового хендлера."""
+    path = handler_path(filename, env, owner)
+    try:
+        h = SafeRotatingFileHandler(path, maxBytes=max_bytes(), backupCount=backups(),
+                                    encoding="utf-8")
+    except Exception:
+        return None
+    h.setFormatter(logging.Formatter(fmt))
+    h.setLevel(level)
+    return h
+
+
+def rotate_if_needed(path, limit=None, keep=None):
+    """Ротация для писателей БЕЗ модуля logging (гард пишет строку через open(..., 'a')).
+    Сдвигает path.N → path.N+1 и path → path.1, оставляя `keep` бэкапов. Никогда не бросает,
+    но и НЕ МОЛЧИТ: отказ уходит в `_report_rotate_failure`. Возврат — «сдвинули ли файл»,
+    контракт прежний (False и «не пора», и «не смогли»), поэтому смотреть надо сигнал."""
+    lim = max_bytes() if limit is None else limit
+    n = backups() if keep is None else keep
+    ok, exc = _rotate(path, lim, n)
+    if exc is not None:
+        _report_rotate_failure(path, exc, " | писатель без logging (append-режим)")
+    return ok
```
