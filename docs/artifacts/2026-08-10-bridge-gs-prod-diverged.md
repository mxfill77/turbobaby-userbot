# Выкладка моста с контрактом клетки ТО — ОСТАНОВЛЕНА на шаге 2: прод ≠ база

**Дата:** 2026-08-10 (ПК, локально) / 2026-08-09 19:43–19:45 UTC (VPS, где мерялось)
**Итог:** `clasp push` **НЕ выполнялся**, `clasp redeploy` **НЕ выполнялся**. Прод расходится с
деревом, из которого делались правки, и расходится **в свою пользу**: в проде живёт работа,
которой нет в рабочей папке. Push стёр бы её. Шаги 3–5 задания не исполнялись — по инструкции
шага 2 («расходится → ничего не пушь, покажи разницу дословно и остановись»).

## Что где лежит (адреса, ничего не удалено)

| что | адрес на VPS |
|---|---|
| **копия-защита** (шаг 1, `cp -a`, вне исходной папки) | `/root/bridge-gs-safety-20260809-194233Z` |
| временная копия прода (`clasp pull`) | `/root/bridge-gs-prodpull-20260809-194334Z` |
| дословные диффы (5 файлов) | `/root/bridge-gs-diff-20260809-194233Z/*.diff` |
| рабочая папка (НЕ тронута) | `/root/turbobaby-bridge-gs` |
| **настоящая база прода** (найдена в ходе разбора) | `/root/turbobaby-bridge-merge-20260804` |

Копия-защита сверена: 45 файлов, 1 054 178 байт, `diff -r` с оригиналом пуст.
Рабочая папка не под git — копия действительно единственный откат.

## Факт расхождения

`clasp pull` отдал 17 файлов — набор имён совпал с рабочей папкой один-в-один. Совпало
**13 файлов из 17**. Разошлись четыре, и во всех четырёх **прод больше**:

| файл | прод, байт | локально, байт | строк разницы | кто больше |
|---|---:|---:|---:|---|
| `Archive.js` | 43 129 | 27 064 | 302 | **прод** (+16 065 Б) |
| `BrainTrash.js` | 6 823 | 2 947 | 65 | **прод** (+3 876 Б) |
| `ReadDocs.js` | 22 451 | 19 733 | 39 | **прод** (+2 718 Б) |
| `Bridge.js` | 23 832 | 22 733 | 26 | смешанно (см. ниже) |
| `ReadFleet.js` | — | — | 64 | **локально** — это и есть невыложённый контракт ТО |

Не перевод строк и не пробелы: CRLF нет ни там, ни там; `diff -w -B` разницу не убирает.

### Функции, которые есть ТОЛЬКО в проде (push удалил бы их)

- `Archive.js` (14): `brainPlainId_`, `coworkArchiveId_`, `coworkMaxBytes_`, `coworkPruneSelfTest_`,
  `coworkPruneSelfTestUnlocked_`, `coworkRotate_`, `pruneCcLogUnlocked_`, `pruneCoworkLog_`,
  `pruneCoworkLogTrigger`, `pruneCoworkLogUnlocked_`, `pruneReviewBySizeUnlocked_`,
  `pruneReviewUnlocked_`, `pruneSessionsLogUnlocked_`, `setupCoworkPruneTrigger`
- `BrainTrash.js` (2): `moveIntoBrain_`, `unregisterBrainDoc_`
- `ReadDocs.js` (2): `withBrainLock_`, `writeDocUnlocked_`
- `Bridge.js`: новых функций нет, но есть **маршруты** к вышеперечисленному —
  `move_into_brain`, `unregister_brain_doc`, `prune_cowork_log`, `setup_cowork_prune_trigger`,
  `prune_cowork_selftest`, а в списке `actions` ещё `register_brain_doc` и `edit_event`.

Обратного нет ни одного: функций, которые есть только локально, — **ноль** во всех четырёх файлах.

## Почему так вышло (корень, а не симптом)

Прод-деплой — `@78`, подпись версии: «v78 04.08: edit_event (правка строки события) +
исправление пробега + реестр мозга из HEAD». Выкладывался он **не из рабочей папки**:
`/root/turbobaby-bridge-merge-20260804` **байт-в-байт равен проду по всем 17 файлам**.

А контракт клетки ТО (`cellState_`) писался 09.08 в `/root/turbobaby-bridge-gs` — в дерево,
которое **отстало на весь мёрж от 04.08**. Проверено поиском по всему `/root`: строка
`cellState_` встречается только в `/root/turbobaby-bridge-gs` (и в снятой сегодня копии-защите),
плюс в двух местах репозитория как харнесс/скретч —
`tests/fleet_cells_harness.js` и `_scratch_cellcontract_0809/cells_before_after.js`.

Отсюда развилка, которую решает владелец, а не сессия:

1. **Перенести контракт на настоящую базу** — приложить правку `ReadFleet.js` + `Bridge.js`
   (она аккуратная и опциональная, см. дифф ниже) поверх `/root/bridge-gs-prodpull-…`
   или `/root/turbobaby-bridge-merge-20260804`, оттуда и пушить. Тогда мёрж 04.08 цел.
2. Пушить как есть — **отменяет ротацию `cowork_log`, замок записи в доки, `move_into_brain`,
   `unregister_brain_doc`, `register_brain_doc`, `edit_event`**. Так делать нельзя.

Сама правка контракта конфликта с прод-кодом не имеет: она добавляет опциональный параметр
`params.cells` и функцию `cellState_` в `ReadFleet.js`, ничего не переписывая. Обе точки касания
(`case 'fleet'` в `Bridge.js`, `getFleetStatus` в `ReadFleet.js`) в мёрже 04.08 не менялись —
перенос механический.

## Чего в этом заходе НЕ делалось

- `clasp push` — нет. `clasp redeploy` — нет. Прод по-прежнему `@78`, тот же, что был до захода.
- Шаг 4 (живая проверка версии и трёх исходов чтения Лист1, распределение 152 клеток ТО
  «после») **неисполним**: выката не было, «после» не существует. Числа «до» — значение 96,
  неразличимый ноль 56 — остаются текущим состоянием прода.
- Шаг 5 (откат) не потребовался: ни один потребитель моста не мог измениться, потому что
  ничего не выкладывалось. Копия-защита цела и не тронута.
- Живые таблицы не открывались на запись; `.env` не открывался; splinter не трогался;
  ничего не удалено.

**Побочный след захода, честно:** в `/root` от кривого экранирования в диагностической команде
остался пустой файл `]x27` (0 байт, 09.08 19:44). Не удалён — запрет на удаление в этом заходе
общий. Снять по слову владельца.

---

# Разница ДОСЛОВНО

Диффы ниже сняты как `diff -u <локальное> <прод>`, то есть:
**`-` = есть только в рабочей папке** (наша невыложённая правка),
**`+` = есть только в проде** (то, что push бы стёр).


## Bridge.js

```diff
--- /root/turbobaby-bridge-gs/Bridge.js	2026-08-09 09:03:49.160454110 +0000
+++ /root/bridge-gs-prodpull-20260809-194334Z/Bridge.js	2026-08-09 19:43:38.279664458 +0000
@@ -47,13 +47,11 @@
         });
 
       // --- Парк (38 байков, статусы) ---
-      // params.cells=1 — попросить РАЗМЕТКУ клеток ТО (value/empty/text, см. cellState_).
-      // Без параметра ответ БАЙТ-В-БАЙТ прежний: разметка добавляется, ничего не заменяет.
       case 'fleet':
         return jsonResponse({
           ok: true,
           action: 'fleet',
-          data: getFleetStatus(params.cells)
+          data: getFleetStatus()
         });
 
       // --- Активные аренды + сводка ---
@@ -394,6 +392,14 @@
       case 'move_brain_file':
         return jsonResponse(Object.assign({ action }, moveBrainFile_(body)));
 
+      // === Перенос KB_*-файла НАРУЖУ→ВНУТРЬ: в КОРЕНЬ папки Brain (защита not_kb_file) ===
+      case 'move_into_brain':
+        return jsonResponse(Object.assign({ action }, moveIntoBrain_(body)));
+
+      // === Снять ключ из BRAIN_MANIFEST (односторонняя; нужен confirm:true, folder_id защищён) ===
+      case 'unregister_brain_doc':
+        return jsonResponse(Object.assign({ action }, unregisterBrainDoc_(body)));
+
         case 'write_doc':
   return jsonResponse(Object.assign({ action }, writeDoc_(body)));
 
@@ -467,6 +473,18 @@
       case 'setup_review_prune_trigger':
         return jsonResponse(Object.assign({ action }, setupReviewPruneTrigger()));
 
+      // --- Ротация cowork_log: ручной прогон. Триггер ставится ОТДЕЛЬНЫМ экшеном ниже,
+      //     сам по себе этот вызов расписание не включает. ---
+      case 'prune_cowork_log':
+        return jsonResponse(Object.assign({ action }, pruneCoworkLog_()));
+
+      case 'setup_cowork_prune_trigger':
+        return jsonResponse(Object.assign({ action }, setupCoworkPruneTrigger()));
+
+      // Самопроверка ротации на ТЕСТОВЫХ доках (живой cowork_log только читается).
+      case 'prune_cowork_selftest':
+        return jsonResponse(Object.assign({ action }, coworkPruneSelfTest_(body)));
+
       // === Зоны доставки (одноразовый init — POST) ===
       case 'delivery_zones_init':
         return jsonResponse(Object.assign({ action }, deliveryZonesInit_()));
@@ -480,7 +498,7 @@
           ok: false,
           error: 'unknown_action',
           message: `Unknown POST action: ${action}`,
-          actions: ['add_transaction', 'add_event', 'delete_event', 'read_events', 'check_balance', 'get_balance', 'tx_summary', 'void_last', 'service_upsert', 'service_list', 'service_set_pin', 'important_add', 'important_list', 'important_due', 'important_touch', 'important_close', 'audit_log', 'audit_list', 'audit_update', 'create_booking', 'activate_booking', 'close_booking', 'write_doc', 'migrate_journal', 'create_brain_plain', 'log_write', 'read_write_log', 'issue_write_ticket', 'consume_write_ticket', 'set_fleet_oil', 'set_fleet_service', 'prune_cc_log', 'setup_prune_trigger', 'prune_review', 'prune_review_size', 'setup_review_prune_trigger', 'prune_sessions_log', 'setup_sessions_prune_trigger', 'list_project_triggers', 'enqueue_task', 'claim_task', 'complete_task', 'set_needs_approval', 'approve_task', 'task_heartbeat', 'get_pending', 'upload_passport_photo', 'ocr_passport', 'save_passport', 'make_contract', 'seed_cost_models', 'closing_upsert', 'closing_get', 'closing_list', 'state_set', 'state_get', 'state_list', 'trash_brain_file', 'move_brain_file']
+          actions: ['add_transaction', 'add_event', 'delete_event', 'read_events', 'check_balance', 'get_balance', 'tx_summary', 'void_last', 'service_upsert', 'service_list', 'service_set_pin', 'important_add', 'important_list', 'important_due', 'important_touch', 'important_close', 'audit_log', 'audit_list', 'audit_update', 'create_booking', 'activate_booking', 'close_booking', 'write_doc', 'migrate_journal', 'create_brain_plain', 'log_write', 'read_write_log', 'issue_write_ticket', 'consume_write_ticket', 'set_fleet_oil', 'set_fleet_service', 'prune_cc_log', 'setup_prune_trigger', 'prune_review', 'prune_review_size', 'setup_review_prune_trigger', 'prune_sessions_log', 'setup_sessions_prune_trigger', 'prune_cowork_log', 'setup_cowork_prune_trigger', 'prune_cowork_selftest', 'list_project_triggers', 'enqueue_task', 'claim_task', 'complete_task', 'set_needs_approval', 'approve_task', 'task_heartbeat', 'get_pending', 'upload_passport_photo', 'ocr_passport', 'save_passport', 'make_contract', 'seed_cost_models', 'closing_upsert', 'closing_get', 'closing_list', 'state_set', 'state_get', 'state_list', 'trash_brain_file', 'move_brain_file', 'register_brain_doc', 'move_into_brain', 'unregister_brain_doc', 'edit_event']
         }, 400);
     }
 
```

## ReadFleet.js

```diff
--- /root/turbobaby-bridge-gs/ReadFleet.js	2026-08-09 09:05:17.447210517 +0000
+++ /root/bridge-gs-prodpull-20260809-194334Z/ReadFleet.js	2026-08-09 19:43:38.279664458 +0000
@@ -24,17 +24,8 @@
 
 /**
  * Возвращает полный статус парка.
- *
- * withCells (необязательный, по умолчанию выключен) — ДОБАВИТЬ к каждому байку поле `cells`
- * с РАЗМЕТКОЙ состояния клеток ТО: value / empty / text (см. cellState_). Параметр опционален
- * НАМЕРЕННО: без него ответ БАЙТ-В-БАЙТ прежний, поэтому ни один сегодняшний потребитель
- * (get_fleet в промпт модели, getDailyPulse, canonicalBikeName_, Contract.js, getIdleBikes)
- * не получает ни одного лишнего байта. Разметку просит только тот, кому нужно РАЗЛИЧИЕ
- * «пусто / прочерк / значение», а не голое число.
  */
-function getFleetStatus(withCells) {
-  const wantCells = (withCells === true || withCells === 1 ||
-                     withCells === '1' || withCells === 'true');
+function getFleetStatus() {
   const sheet = SpreadsheetApp.openById(CONFIG.SHEETS.FLEET).getSheetByName('Лист1');
   if (!sheet) throw new Error('Лист1 не найден в "Байки"');
 
@@ -90,7 +81,7 @@
     
     const status = row[1];  // B — Статус (формула)
     
-    const bike = {
+    bikes.push({
       number: parseNumber(row[0]),               // A — # (порядковый)
       status: String(status || '').trim(),       // ДОМА / В аренде / ...
       name: String(name).trim(),
@@ -112,21 +103,7 @@
         pay_per_day: parseNumber(row[22]),       // W
         debt: parseNumber(row[23]),              // X
       } : null,
-    };
-
-    // Разметка клеток ТО — ТОЛЬКО по явной просьбе (см. шапку функции).
-    // Читается СЫРОЕ значение row[i], то самое, которое parseNumber схлопывает в 0.
-    if (wantCells) {
-      bike.cells = {
-        mileage: cellState_(row[7]),             // H
-        oil_last_km: cellState_(row[8]),         // I
-        gear_last_km: cellState_(row[9]),        // J
-        abs_last_km: cellState_(row[10]),        // K
-        airfilter_last_km: cellState_(row[11]),  // L
-      };
-    }
-
-    bikes.push(bike);
+    });
   }
 
   // === Сводка ===
@@ -482,41 +459,6 @@
 }
 
 /**
- * СОСТОЯНИЕ КЛЕТКИ до схлопывания в число — три исхода вместо одного нуля (09.08.2026).
- *
- * parseNumber выше отдаёт 0 в ТРЁХ разных случаях: клетка ПУСТА (строка 476), в клетке
- * НЕ-ЧИСЛО — прочерк, слово, дата (строка 481), и в клетке НАСТОЯЩИЙ НОЛЬ. Наверх все три
- * приезжают неразличимо, поэтому «не измерено» нельзя выразить в принципе: перепись
- * docs/artifacts/2026-08-08-park-overdue-35-of-38-census.md — 12 просрочек из 35 фантомные.
- *
- * cellState_ НЕ ЧИНИТ значения и НЕ судит их: −5000 км, ноль и «35200 Km, 05.07.2026» доезжают
- * такими, какие они есть. Он отвечает ровно на один вопрос — ЧТО в клетке лежало:
- *   value  в клетке число (в т.ч. добытое из живого текста «฿ 12 345») → num = ровно то же
- *          число, которое вернул бы parseNumber;
- *   empty  содержимого нет вовсе (пусто / одни пробелы);
- *   text   содержимое ЕСТЬ, но числом оно не стало (прочерк, слово, дата).
- * Инвариант, ради которого функция написана рядом с parseNumber, а не поодаль:
- *   state === 'value'  ⇔  parseNumber(val) добыт ИЗ СОДЕРЖИМОГО и равен num;
- *   state !== 'value'  ⇔  parseNumber(val) === 0 ФОЛЛБЭКОМ, а не по факту.
- * Инвариант проверяется на ЭТОМ ЖЕ живом файле харнессом tests/fleet_cells_harness.js —
- * поэтому вычистка НЕ должна разойтись с parseNumber. Класс символов здесь на один
- * короче: у parseNumber неразрывный пробел выписан отдельным escape-ом, а в JS его
- * и без того включает `\s` — классы равносильны, и равенство доказано харнессом.
- *
- * raw — сырое содержимое строкой, чтобы про «не-число» можно было СКАЗАТЬ, что именно лежит.
- */
-function cellState_(val) {
-  if (val === null || val === undefined || val === '') return { state: 'empty', raw: '' };
-  if (typeof val === 'number') return { state: 'value', num: val, raw: String(val) };
-  const raw = String(val);
-  if (raw.trim() === '') return { state: 'empty', raw: raw };
-  const cleaned = raw.replace(/[฿$\s,]/g, '').replace(',', '.');
-  const n = parseFloat(cleaned);
-  if (isNaN(n)) return { state: 'text', raw: raw };
-  return { state: 'value', num: n, raw: raw };
-}
-
-/**
  * Форматирует дату в ISO string или null.
  */
 function formatDate(val) {
```

## ReadDocs.js

```diff
--- /root/turbobaby-bridge-gs/ReadDocs.js	2026-06-24 14:44:43.776084471 +0000
+++ /root/bridge-gs-prodpull-20260809-194334Z/ReadDocs.js	2026-08-09 19:43:38.279664458 +0000
@@ -115,6 +115,36 @@
   doc.saveAndClose();
 }
 
+// ============================================================
+//  ЗАМОК ЗАПИСИ В ДОКИ — тот же LockService, что держит очередь задач (BotData.gs).
+//  Зачем: любая запись в Brain-док — это read-modify-write ВНУТРИ одного вызова
+//  (прочитали текст → порезали → записали обратно). Без замка два таких вызова
+//  переплетаются, и тот, кто записал вторым, затирает работу первого.
+//  Закрывает две гонки: писатель↔писатель (два write_doc) и писатель↔ротация
+//  (write_doc, приехавший в середину прореживания). Вторая появится сама, как
+//  только владелец включит time-trigger ротации, — замок ставим ЗАРАНЕЕ.
+//
+//  Замок ОБЩИЙ с очередью задач (LockService.getScriptLock() — на проект один).
+//  Следствие честно: длинная ротация задерживает enqueue/claim_task. Поэтому
+//  ротация и живёт по ночному расписанию (~13:xx UTC), а не в рабочем цикле.
+//  tryLock (а не waitLock): занято → аккуратный ok:false, а не исключение-500.
+// ============================================================
+var BRAIN_LOCK_WAIT_MS = 30000;
+
+function withBrainLock_(label, fn) {
+  var lock = LockService.getScriptLock();
+  if (!lock.tryLock(BRAIN_LOCK_WAIT_MS)) {
+    return { ok: false, error: 'locked', op: label,
+             message: 'Доки заняты другой записью (' + label + '): ждали ' +
+                      BRAIN_LOCK_WAIT_MS + ' мс. Ничего не изменено, повторите запрос.' };
+  }
+  try {
+    return fn();
+  } finally {
+    lock.releaseLock();
+  }
+}
+
 /**
  * Миграция журнала (cc_log/review) с Google-Doc на PLAIN-TEXT файл (фикс износа DocumentApp).
  * Создаёт text/plain файл с тем же именем, переливает ВЕСЬ текст, СТАРЫЙ Doc переименовывает
@@ -322,8 +352,17 @@
  *
  * body: { name | id, text }. Только перезапись по существующему id из манифеста:
  * не создаёт документы, не меняет манифест/doc_id. Пустой text запрещён (защита от затирки).
+ *
+ * Под замком withBrainLock_ (см. выше): пока идёт эта запись, ни другой write_doc, ни
+ * ротация в док не войдут. ОГОВОРКА: замок закрывает гонку ВНУТРИ Bridge. Связку
+ * «read_doc с ПК → правка на ПК → write_doc» он НЕ закрывает — это два разных HTTP-вызова,
+ * между ними замка нет; там от потери правок защищает обратная сверка в brain_writer.
  */
 function writeDoc_(body) {
+  return withBrainLock_('write_doc', function () { return writeDocUnlocked_(body); });
+}
+
+function writeDocUnlocked_(body) {
   try {
     var b = body || {};
     var id = b.id;
```

## BrainTrash.js

```diff
--- /root/turbobaby-bridge-gs/BrainTrash.js	2026-06-27 21:12:36.382350899 +0000
+++ /root/bridge-gs-prodpull-20260809-194334Z/BrainTrash.js	2026-08-09 19:43:38.279664458 +0000
@@ -56,3 +56,68 @@
     return { ok: false, error: 'move_failed', id: id, message: String(err) };
   }
 }
+
+/**
+ * Перенести существующий KB_*-файл В КОРЕНЬ папки Brain (НАРУЖУ→ВНУТРЬ).
+ * Зачем отдельно от moveBrainFile_: тот двигает только то, что УЖЕ лежит в Brain, и только в
+ * ПОДПАПКУ (страж not_in_brain на входе + getOrCreateFolder_ на выходе). Обратного направления
+ * у моста не было вообще, поэтому живые Brain-доки, созданные штабом вне папки, реестром не
+ * видятся: register_brain_doc отбивает их тем же not_in_brain.
+ * Защита: только файлы с именем KB_* (не тащим в мозг случайное), идемпотентность (уже в Brain →
+ * no-op). moveTo делает Brain ЕДИНСТВЕННЫМ родителем — прежняя папка файл теряет. body: { id }.
+ */
+function moveIntoBrain_(body) {
+  var p = body || {};
+  var id = String(p.id || '').trim();
+  if (!id) return { ok: false, error: 'no_id' };
+  try {
+    var f = DriveApp.getFileById(id);
+    var title = f.getName();
+    if (title.indexOf('KB_') !== 0) return { ok: false, error: 'not_kb_file', id: id, title: title };
+    var parents = [], ps = f.getParents();
+    while (ps.hasNext()) parents.push(ps.next());
+    for (var i = 0; i < parents.length; i++) {
+      if (parents[i].getId() === BRAIN_FOLDER_ID) {
+        return { ok: true, id: id, title: title, already_in_brain: true };
+      }
+    }
+    var was = parents.map(function (x) { return x.getName(); }).join(' | ');
+    f.moveTo(DriveApp.getFolderById(BRAIN_FOLDER_ID));   // КОРЕНЬ Brain, не _archive
+    return { ok: true, id: id, title: title, moved_from: was || '(без родителя)',
+             moved_to: BRAIN_FOLDER_ID, into_brain: true };
+  } catch (err) {
+    return { ok: false, error: 'move_failed', id: id, message: String(err) };
+  }
+}
+
+/**
+ * СНЯТЬ ключ из BRAIN_MANIFEST (единственная операция реестра, которой у моста не было:
+ * setupBrain/registerBrainDoc_/createBrainPlain_/migrateJournalToPlain_ ключи только добавляют
+ * или перевешивают, а Script Properties снаружи не правятся — REST-эндпоинта свойств у Apps
+ * Script API нет). Мержит: трогает РОВНО один ключ, остальные пары остаются как есть.
+ * ОДНОСТОРОННЯЯ: вернуть ключ на удалённый файл нельзя (registerBrainDoc_ требует существующий
+ * файл), поэтому нужен явный confirm:true. folder_id защищён — без него ослепнет весь канал.
+ * body: { name, confirm:true }.
+ */
+function unregisterBrainDoc_(body) {
+  var p = body || {};
+  var name = String(p.name == null ? '' : p.name).trim();
+  if (!name) return { ok: false, error: 'need_name' };
+  if (name === 'folder_id') {
+    return { ok: false, error: 'protected', message: 'folder_id — адрес папки Brain, не снимаем' };
+  }
+  if (p.confirm !== true) {
+    return { ok: false, error: 'need_confirm', name: name,
+             message: 'операция односторонняя: нужен confirm:true' };
+  }
+  var man = getBrainManifest_();
+  var before = Object.keys(man).length;
+  if (!(name in man)) {
+    return { ok: true, name: name, already_absent: true, total: before };
+  }
+  var oldId = man[name];
+  delete man[name];
+  PropertiesService.getScriptProperties().setProperty(BRAIN_PROP_KEY, JSON.stringify(man));
+  return { ok: true, name: name, removed_id: oldId, unregistered: true,
+           total_before: before, total: Object.keys(man).length };
+}
```

## Archive.js

```diff
--- /root/turbobaby-bridge-gs/Archive.js	2026-06-19 16:57:34.340294435 +0000
+++ /root/bridge-gs-prodpull-20260809-194334Z/Archive.js	2026-08-09 19:43:38.279664458 +0000
@@ -12,6 +12,11 @@
  *   prune_cc_log        — разовый прогон pruneCcLog_() (ручной/из bridge_client).
  *   setup_prune_trigger — установить ежедневный time-trigger (идемпотентно).
  * Триггер вызывает pruneCcLogTrigger() (стабильное имя хендлера).
+ *
+ * 29.07.2026 в этот же файл добавлена ротация cowork_log (см. блок ниже):
+ *   prune_cowork_log / setup_cowork_prune_trigger / prune_cowork_selftest.
+ * ВСЕ записи в доки идут под общим замком withBrainLock_ (ReadDocs.gs) — тем же
+ * LockService, что держит очередь задач.
  */
 
 var CCLOG_KEY = 'cc_log';
@@ -43,9 +48,22 @@
   return id;
 }
 
-/** Разбить текст на записи по заголовкам (KEY YYYY-MM-DD, время опц.). Индексный разрез — лоссless. */
+/**
+ * Разбить текст на записи по заголовкам (KEY YYYY-MM-DD, время опц.). Индексный разрез — лоссless.
+ *
+ * Контракт строки журнала: <ТИП> <ГГГГ-ММ-ДД ЧЧ:ММ UTC>: <текст>. ASK добавлен 29.07.2026 —
+ * это тип «развилка-остановка, нужен выбор человека» (правило журнала в CLAUDE.md: DONE для
+ * завершения, ASK для развилки). До правки ASK-запись НЕ была заголовком: она прилипала к
+ * предыдущей записи как её хвост и уезжала в архив вместе с ней — то есть незакрытый вопрос
+ * владельцу мог уехать в архив, не будучи отдельной записью.
+ *
+ * Якорь НАМЕРЕННО обрывается на дате, а не тянется до « UTC:». Этот разрез общий для ЧЕТЫРЁХ
+ * прореживаний (cc_log, review, review_size, sessions_log), а в их доках лежат легаси-строки
+ * без времени и без UTC. Ужесточение под новый контракт сместило бы границы записей в живых
+ * доках — поэтому меняем ТОЛЬКО набор типов, ничего больше.
+ */
 function ccLogSplit_(text) {
-  var re = /^(PLAN|DONE|NOTE|BLOCKED|WAITING|SKIPPED)\s+\d{4}-\d{2}-\d{2}/gm;
+  var re = /^(PLAN|DONE|NOTE|ASK|BLOCKED|WAITING|SKIPPED)\s+\d{4}-\d{2}-\d{2}/gm;
   var starts = [], m;
   while ((m = re.exec(text)) !== null) {
     starts.push(m.index);
@@ -64,8 +82,13 @@
 /**
  * Ядро: если cc_log > порога — оставить новейшие записи (сумма <= порога), остальные перенести
  * в архив (newest-first, под шапкой архива). Идемпотентно: если уже компактный — no-op.
+ * Под общим замком записи (withBrainLock_) — читаем и пишем док как одну неделимую операцию.
  */
 function pruneCcLog_() {
+  return withBrainLock_('prune_cc_log', pruneCcLogUnlocked_);
+}
+
+function pruneCcLogUnlocked_() {
   var manifest = getBrainManifest_();
   var ccId = manifest[CCLOG_KEY];
   if (!ccId) return { ok: false, error: 'no_cc_log' };
@@ -207,9 +230,13 @@
  * порядок), остальные переносятся в архив (newest-first, под шапкой). Делёж — ccLogSplit_
  * (тот же индексный лоссless-разрез). Гард безопасности: КАЖДЫЙ keep_header должен найтись
  * ровно — иначе НИЧЕГО не пишем (рассинхрон классификации = отказ, не порча дока).
- * body: { keep_headers: [string,...] }.
+ * body: { keep_headers: [string,...] }. Под общим замком записи (withBrainLock_).
  */
 function pruneReview_(body) {
+  return withBrainLock_('prune_review', function () { return pruneReviewUnlocked_(body); });
+}
+
+function pruneReviewUnlocked_(body) {
   var b = body || {};
   var keepHeaders = b.keep_headers;
   if (!keepHeaders || Object.prototype.toString.call(keepHeaders) !== '[object Array]') {
@@ -297,6 +324,10 @@
 //  Endpoint: prune_review_size (ручной прогон); триггер: pruneReviewTrigger (ежедневно).
 // ============================================================
 function pruneReviewBySize_() {
+  return withBrainLock_('prune_review_size', pruneReviewBySizeUnlocked_);
+}
+
+function pruneReviewBySizeUnlocked_() {
   var manifest = getBrainManifest_();
   var revId = manifest[REVIEW_KEY];
   if (!revId) return { ok: false, error: 'no_review' };
@@ -424,8 +455,13 @@
   return id;
 }
 
-/** Прорежать sessions_log по размеру (новейшие записи ≤ порога, старые → архив). plain-text. */
+/** Прорежать sessions_log по размеру (новейшие записи ≤ порога, старые → архив). plain-text.
+ *  Под общим замком записи (withBrainLock_). */
 function pruneSessionsLog_() {
+  return withBrainLock_('prune_sessions_log', pruneSessionsLogUnlocked_);
+}
+
+function pruneSessionsLogUnlocked_() {
   var manifest = getBrainManifest_();
   var sId = manifest[SESSIONS_KEY];
   if (!sId) return { ok: false, error: 'no_sessions_log' };
@@ -483,6 +519,260 @@
            schedule: 'ежедневно ~13:20 UTC', max_bytes: sessionsMaxBytes_() };
 }
 
+// ============================================================
+//  РОТАЦИЯ cowork_log (журнал Dispatch/Cowork) — по образцу prune_cc_log.
+//
+//  Отличие от cc_log, НАМЕРЕННОЕ: cowork_log — док, в который дозаписывают СВЕРХУ
+//  (cowork_log_append кладёт новую строку в голову, новейшее наверху). Поэтому шапку
+//  «📦 …» в источник НЕ впечатываем — иначе следующая дозапись легла бы ВЫШЕ шапки и
+//  та уползла бы в середину журнала. Источник остаётся ровно тем, чем был: preamble
+//  (как правило пустой) + новейшие записи. Шапка живёт только в АРХИВЕ, куда никто,
+//  кроме ротации, не пишет. Это шаблон pruneSessionsLog_, а не pruneCcLog_.
+//
+//  Архив — plain-text файл (не Google-Doc): cowork_log исторически дорастал до 750 КБ,
+//  а DocumentApp на таких размерах и тормозит, и изнашивает структуру (см. migrateJournalToPlain_).
+//
+//  Порог — Script Property COWORK_MAX_BYTES, меняется без редеплоя.
+//  Триггер НЕ включается этим кодом: setupCoworkPruneTrigger() существует, но не вызвана.
+// ============================================================
+var COWORK_KEY = 'cowork_log';
+var COWORK_ARCHIVE_KEY = 'cowork_log_archive';
+var COWORK_ARCHIVE_NAME = 'KB_cowork_log_archive';
+var COWORK_ARCH_MARK = '════';
+var COWORK_MAXBYTES_PROP = 'COWORK_MAX_BYTES';
+var COWORK_MAXBYTES_DEFAULT = 50000;
+var COWORK_TRIGGER_HANDLER = 'pruneCoworkLogTrigger';
+var COWORK_ARCH_HEADER =
+  '📦 KB_cowork_log_archive — АРХИВ журнала Dispatch/Cowork (старые записи cowork_log, newest-first).\n' +
+  'Ротация по размеру: записи сверх порога COWORK_MAX_BYTES переносятся сюда. Ничего не удаляется.\n' +
+  '════════════════════════════════════════════════════════════════════\n';
+
+/** Порог из Script Property COWORK_MAX_BYTES (символов), дефолт 50000. Меняется без редеплоя. */
+function coworkMaxBytes_() {
+  var v = PropertiesService.getScriptProperties().getProperty(COWORK_MAXBYTES_PROP);
+  var n = parseInt(v, 10);
+  return (isFinite(n) && n > 0) ? n : COWORK_MAXBYTES_DEFAULT;
+}
+
+/** id plain-text файла в папке Brain по ключу манифеста; нет — создать и зарегистрировать. */
+function brainPlainId_(key, name) {
+  var manifest = getBrainManifest_();
+  if (manifest[key]) return manifest[key];
+  if (!manifest.folder_id) throw new Error('no folder_id в манифесте — сначала setupBrain');
+  var folder = DriveApp.getFolderById(manifest.folder_id);
+  var file = folder.createFile(name, '', 'text/plain');
+  var id = file.getId();
+  manifest[key] = id;
+  PropertiesService.getScriptProperties().setProperty(BRAIN_PROP_KEY, JSON.stringify(manifest));
+  return id;
+}
+
+function coworkArchiveId_() {
+  return brainPlainId_(COWORK_ARCHIVE_KEY, COWORK_ARCHIVE_NAME);
+}
+
+/**
+ * ЯДРО ротации — работает по ЯВНЫМ id, поэтому одинаково гоняется и на живом cowork_log,
+ * и на тестовом доке (см. coworkPruneSelfTest_). Один и тот же код на обеих дорожках:
+ * проверка не «повторяет логику», а исполняет ту самую.
+ *
+ * Порядок записи: СНАЧАЛА архив, ПОТОМ источник. Если второй записи не случится, записи
+ * останутся в ОБОИХ доках (дубль, видно глазами), а не пропадут — потеря хуже дубля.
+ */
+function coworkRotate_(srcId, archId, maxBytes) {
+  var text = brainTextRead_(srcId);
+  var before = text.length;
+  if (before <= maxBytes) {
+    return { ok: true, moved: 0, kept: 0, before_chars: before, kept_chars: before,
+             archive_id: archId, max_bytes: maxBytes, note: 'cowork_log уже компактный' };
+  }
+
+  var sp = ccLogSplit_(text);
+  var entries = sp.entries;
+  if (entries.length <= 1) {
+    return { ok: true, moved: 0, kept: entries.length, before_chars: before, kept_chars: before,
+             entries: entries.length, archive_id: archId, max_bytes: maxBytes,
+             note: 'мало записей, не делю' };
+  }
+
+  // entries newest-first: держим новейшие, пока сумма записей <= порога
+  var keep = [], acc = 0;
+  for (var i = 0; i < entries.length; i++) {
+    if (acc + entries[i].length > maxBytes && keep.length > 0) break;
+    keep.push(entries[i]); acc += entries[i].length;
+  }
+  var move = entries.slice(keep.length);
+  if (move.length === 0) {
+    return { ok: true, moved: 0, kept: keep.length, before_chars: before, kept_chars: before,
+             entries: entries.length, archive_id: archId, max_bytes: maxBytes,
+             note: 'нечего переносить' };
+  }
+  var moveText = move.join('');
+
+  // === архив: перенесённые ПЕРЕД старым содержимым, под шапкой (шапка переживает прогоны) ===
+  var archText = brainTextRead_(archId);
+  var archHeader, archRest;
+  var idx = archText.indexOf(COWORK_ARCH_MARK);
+  if (archText.indexOf('📦') === 0 && idx >= 0) {
+    var nl = archText.indexOf('\n', idx);
+    archHeader = archText.substring(0, nl + 1);
+    archRest = archText.substring(nl + 1).replace(/^\n+/, '');
+  } else {
+    archHeader = COWORK_ARCH_HEADER;
+    archRest = archText;
+  }
+  var newArch = archHeader + '\n' + moveText + (archRest ? '\n' + archRest : '');
+  brainTextWrite_(archId, newArch);
+
+  // === источник: преамбула как была + оставленные новейшие. Шапку НЕ впечатываем. ===
+  var keepText = (sp.preamble || '') + keep.join('');
+  brainTextWrite_(srcId, keepText);
+
+  return { ok: true, entries: entries.length, kept: keep.length, moved: move.length,
+           before_chars: before, kept_chars: keepText.length,
+           moved_chars: moveText.length, archive_id: archId,
+           archive_chars: newArch.length, max_bytes: maxBytes };
+}
+
+/** Ротация ЖИВОГО cowork_log. Под общим замком записи. Идемпотентна (no-op если компактный). */
+function pruneCoworkLog_() {
+  return withBrainLock_('prune_cowork_log', pruneCoworkLogUnlocked_);
+}
+
+function pruneCoworkLogUnlocked_() {
+  var manifest = getBrainManifest_();
+  var srcId = manifest[COWORK_KEY];
+  if (!srcId) return { ok: false, error: 'no_cowork_log' };
+  var archId = coworkArchiveId_();   // создаём+регистрируем архив при ЛЮБОМ вызове, даже no-op
+  return coworkRotate_(srcId, archId, coworkMaxBytes_());
+}
+
+/** Обёртка для time-trigger (стабильное имя хендлера). Ошибки не валят триггер — пишем в лог. */
+function pruneCoworkLogTrigger() {
+  try {
+    var r = pruneCoworkLog_();
+    Logger.log('pruneCoworkLogTrigger: ' + JSON.stringify(r));
+  } catch (err) {
+    Logger.log('pruneCoworkLogTrigger ОШИБКА: ' + err);
+  }
+}
+
+/**
+ * Установить ежедневный time-trigger ротации cowork_log (~13:30 UTC). Идемпотентно.
+ * НЕ ВЫЗЫВАЕТСЯ НИОТКУДА в этом коде — включение триггера остаётся решением владельца
+ * (endpoint setup_cowork_prune_trigger). Пока не вызвана — ротация только ручная.
+ */
+function setupCoworkPruneTrigger() {
+  var props = PropertiesService.getScriptProperties();
+  if (!props.getProperty(COWORK_MAXBYTES_PROP)) {
+    props.setProperty(COWORK_MAXBYTES_PROP, String(COWORK_MAXBYTES_DEFAULT));
+  }
+  try { coworkArchiveId_(); } catch (e) {}
+
+  var triggers = ScriptApp.getProjectTriggers();
+  var removed = 0;
+  for (var i = 0; i < triggers.length; i++) {
+    if (triggers[i].getHandlerFunction() === COWORK_TRIGGER_HANDLER) {
+      ScriptApp.deleteTrigger(triggers[i]); removed++;
+    }
+  }
+  // ~13:30 UTC — после cc_log(13:00)/review(13:10)/sessions(13:20): доки не пересекаются,
+  // и общий замок записи не держится четырьмя ротациями одновременно.
+  ScriptApp.newTrigger(COWORK_TRIGGER_HANDLER).timeBased().everyDays(1).atHour(13).nearMinute(30).create();
+  return { ok: true, handler: COWORK_TRIGGER_HANDLER, removed_old: removed,
+           schedule: 'ежедневно ~13:30 UTC', max_bytes: coworkMaxBytes_() };
+}
+
+
+// ============================================================
+//  САМОПРОВЕРКА РОТАЦИИ — на ТЕСТОВЫХ доках, живой cowork_log только ЧИТАЕТСЯ.
+//
+//  Фикстура снимается с прода (правило CLAUDE.md «мок обязан копировать живой формат»):
+//  тестовый док засевается ПЕРВЫМИ K записями живого cowork_log — дословно, вместе с их
+//  реальными заголовками, кириллицей и хвостами. Никакой идеализированной схемы.
+//
+//  Живой cowork_log в этой функции НЕ ПИШЕТСЯ ни при каком исходе — только brainTextRead_.
+//  body: { entries?: K=12, max_bytes?: M (деф. половина засеянного), archive_preview?: N=4000 }
+// ============================================================
+var COWORK_TEST_SRC_KEY = 'cowork_log_test';
+var COWORK_TEST_SRC_NAME = 'KB_cowork_log_TEST';
+var COWORK_TEST_ARCH_KEY = 'cowork_log_test_archive';
+var COWORK_TEST_ARCH_NAME = 'KB_cowork_log_TEST_archive';
+
+function coworkPruneSelfTest_(body) {
+  return withBrainLock_('prune_cowork_selftest', function () {
+    return coworkPruneSelfTestUnlocked_(body || {});
+  });
+}
+
+function coworkPruneSelfTestUnlocked_(b) {
+  var manifest = getBrainManifest_();
+  var liveId = manifest[COWORK_KEY];
+  if (!liveId) return { ok: false, error: 'no_cowork_log' };
+
+  var wantEntries = parseInt(b.entries, 10);
+  if (!isFinite(wantEntries) || wantEntries <= 0) wantEntries = 12;
+  var preview = parseInt(b.archive_preview, 10);
+  if (!isFinite(preview) || preview <= 0) preview = 4000;
+
+  // --- фикстура с прода: живой док только читаем ---
+  var liveText = brainTextRead_(liveId);
+  var liveSp = ccLogSplit_(liveText);
+  if (liveSp.entries.length === 0) {
+    return { ok: false, error: 'live_has_no_entries', live_chars: liveText.length };
+  }
+  var seedEntries = liveSp.entries.slice(0, wantEntries);
+  var seedText = seedEntries.join('');
+
+  var maxBytes = parseInt(b.max_bytes, 10);
+  if (!isFinite(maxBytes) || maxBytes <= 0) maxBytes = Math.floor(seedText.length / 2);
+
+  // --- тестовые доки: пересеваем начисто, чтобы прогон был воспроизводимым ---
+  var testSrcId = brainPlainId_(COWORK_TEST_SRC_KEY, COWORK_TEST_SRC_NAME);
+  var testArchId = brainPlainId_(COWORK_TEST_ARCH_KEY, COWORK_TEST_ARCH_NAME);
+  brainTextWrite_(testSrcId, seedText);
+  brainTextWrite_(testArchId, '');
+
+  var srcBefore = brainTextRead_(testSrcId).length;
+
+  // --- ТО ЖЕ ядро, что гоняет живую ротацию ---
+  var res = coworkRotate_(testSrcId, testArchId, maxBytes);
+
+  var srcAfterText = brainTextRead_(testSrcId);
+  var archAfterText = brainTextRead_(testArchId);
+
+  // --- проверка сохранности: ни один символ не потерян и не задвоен ---
+  var lossless = (srcAfterText + archAfterText.substring(COWORK_ARCH_HEADER.length + 1)
+                    .replace(/^\n+/, '')) .length;
+
+  return {
+    ok: true,
+    live_doc_untouched: true,
+    live_id: liveId,
+    live_chars: liveText.length,
+    live_entries_total: liveSp.entries.length,
+    seeded_entries: seedEntries.length,
+    max_bytes: maxBytes,
+    test_src_id: testSrcId,
+    test_archive_id: testArchId,
+    src_chars_before: srcBefore,
+    src_chars_after: srcAfterText.length,
+    archive_chars_after: archAfterText.length,
+    rotate_result: res,
+    sum_after_vs_before: (srcAfterText.length + archAfterText.length) + ' vs ' + srcBefore,
+    entries_after_in_src: ccLogSplit_(srcAfterText).entries.length,
+    entries_after_in_archive: ccLogSplit_(archAfterText).entries.length,
+    lossless_chars: lossless,
+    archive_text: archAfterText.length > preview
+      ? archAfterText.substring(0, preview) + '\n…[обрезано в отчёте, в доке целиком]'
+      : archAfterText,
+    src_text_after: srcAfterText.length > preview
+      ? srcAfterText.substring(0, preview) + '\n…[обрезано в отчёте, в доке целиком]'
+      : srcAfterText
+  };
+}
+
+
 /** Аудит: список всех prune-триггеров проекта (handler + расписание из кода). */
 function listProjectTriggers_() {
   var trs = ScriptApp.getProjectTriggers();
@@ -493,7 +783,9 @@
     known: {
       pruneCcLogTrigger: { schedule: '~13:00 UTC', max_bytes: ccLogMaxBytes_() },
       pruneReviewTrigger: { schedule: '~13:10 UTC', max_bytes: reviewMaxBytes_() },
-      pruneSessionsLogTrigger: { schedule: '~13:20 UTC', max_bytes: sessionsMaxBytes_() }
+      pruneSessionsLogTrigger: { schedule: '~13:20 UTC', max_bytes: sessionsMaxBytes_() },
+      pruneCoworkLogTrigger: { schedule: '~13:30 UTC', max_bytes: coworkMaxBytes_(),
+                               installed: handlers.indexOf('pruneCoworkLogTrigger') >= 0 }
     }
   };
 }
```
