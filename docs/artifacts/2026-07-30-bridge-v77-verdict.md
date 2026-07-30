# Судьба версии 77 моста: дифф v76 → HEAD, риски обеих развилок, откат

**Дата:** 30.07.2026, сессия Dispatch (headless, ПК `D:\turbobaby-bot`, ветка `main`, HEAD `fa54ce7`).
**Режим:** ТОЛЬКО ЧТЕНИЕ. `clasp` не запускался ни разу (ни `pull`, ни `push`, ни `deploy`),
мост не вызывался, прод не двигался. Решение — за владельцем.

---

## Итог одной строкой

Дифф v76 → HEAD — **ровно три хунка в двух файлах, всё добавлением** (+74 строки, −1 заменённая
строка списка экшенов). Постороннего в нём **нет**. Рекомендация: **перевести прод на v77** —
код уже отработал живьём 7 вызовами, `appsscript.json` не изменён (новых OAuth-scope нет),
откат — одна команда за секунды.

---

## 0. Откуда взят «прод-код v76», если clasp не запускался

Цепочка из двух независимых фиксаций (обе — в артефактах этого репо):

1. 29.07: `clasp push` из `tmp/bridge_gs` → 17 файлов → `clasp deploy` → **создана версия 76**
   (`docs/artifacts/2026-07-29-cowork-log-rotation.md`, §5).
2. 30.07 17:52: **свежий `clasp pull` в чистый каталог** `tmp/bridge_head` → 17 файлов,
   **дифф с копией 29.07 ПУСТ во всех 17** (`docs/artifacts/2026-07-30-brain-manifest-backup.md`,
   §8.1 строка 1) → HEAD не двигался с того пуша, то есть `tmp/bridge_gs` = код версии 76.

Затем в `tmp/bridge_head` легла правка 2 файлов, `clasp push`, `clasp deploy` → **версия 77**,
временный деплой снят. Значит **`tmp/bridge_gs` = v76 (прод), `tmp/bridge_head` = HEAD = v77**.

**Граница честности:** живой HEAD сейчас заново не сверялся — это `clasp pull` (сеть/класс `clasp`),
вне read-only объёма. Если после 17:54 кто-то правил проект в веб-IDE Apps Script, локальная копия
об этом не знает. Ниже — дифф локальных снимков, а не свежий ответ Google.

## 1. Что вообще изменилось: 15 из 17 файлов побайтово те же

sha256 (первые 12 символов), v76 → HEAD:

| файл | v76 | HEAD | |
|---|---|---|---|
| Archive.js | `92e21884efe9` | `92e21884efe9` | SAME |
| Booking.js | `a95bbdc9ba6b` | `a95bbdc9ba6b` | SAME |
| BotData.js | `2f98d24e7e82` | `2f98d24e7e82` | SAME |
| **BrainTrash.js** | `3bf6a09714f4` | `3f2bc8d6455f` | **DIFF** |
| **Bridge.js** | `7ef7e1ccd49c` | `835793681555` | **DIFF** |
| Closing.js | `8fce3a78e21d` | `8fce3a78e21d` | SAME |
| Config.js | `7bdbea01c9e4` | `7bdbea01c9e4` | SAME |
| Contract.js | `521e9ce134c8` | `521e9ce134c8` | SAME |
| Delivery.js | `f52830a40c1b` | `f52830a40c1b` | SAME |
| Passport.js | `664a72b09664` | `664a72b09664` | SAME |
| QuotePrice.js | `4a8d3d46a124` | `4a8d3d46a124` | SAME |
| ReadClients.js | `22569868065e` | `22569868065e` | SAME |
| **ReadDocs.js** | `d2ec0a7e7b93` | `d2ec0a7e7b93` | SAME |
| ReadFinance.js | `45f4b9f23e8a` | `45f4b9f23e8a` | SAME |
| ReadFleet.js | `c4d94b43c025` | `c4d94b43c025` | SAME |
| ServicePending.js | `5851524c4a42` | `5851524c4a42` | SAME |
| **appsscript.json** | `41c4e8f0e37a` | `41c4e8f0e37a` | **SAME** |

`scriptId` в обоих `.clasp.json` один и тот же (`12iXPDU_…HhOJ`) — сравниваются копии ОДНОГО проекта.

**`appsscript.json` не изменён** — это ключевое: `timeZone`, `runtimeVersion: V8`,
`webapp.executeAs: USER_DEPLOYING`, `webapp.access: ANYONE_ANONYMOUS` те же, явного `oauthScopes`
в манифесте нет (scope выводятся из кода). Новый код зовёт только `DriveApp` и `PropertiesService`,
которые в v76 уже используются (`moveBrainFile_` → `f.moveTo` + `getFolderById`;
`setProperty(BRAIN_PROP_KEY, …)` встречается в v76 **11 раз**) → **новых scope нет, повторная
авторизация владельцем при переводе не потребуется.**

## 2. ДИФФ ДОСЛОВНО

### 2.1 `BrainTrash.js` — +65 строк, −0. Чистый дописок в КОНЕЦ файла

```diff
--- tmp/bridge_gs/BrainTrash.js	2026-07-29 07:13:25.243809200 +0700
+++ tmp/bridge_head/BrainTrash.js	2026-07-30 17:54:30.528912700 +0700
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

### 2.2 `Bridge.js` — +9 строк, −1 (заменена одна строка списка экшенов)

```diff
--- tmp/bridge_gs/Bridge.js	2026-07-29 07:44:43.130463900 +0700
+++ tmp/bridge_head/Bridge.js	2026-07-30 17:54:47.071234200 +0700
@@ -386,6 +386,14 @@
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

@@ -484,7 +492,7 @@
           ok: false,
           error: 'unknown_action',
           message: `Unknown POST action: ${action}`,
-          actions: [… 62 имени …, 'trash_brain_file', 'move_brain_file']
+          actions: [… те же 62 …, 'move_brain_file', 'register_brain_doc', 'move_into_brain', 'unregister_brain_doc']
         }, 400);
     }
```

Третий хунк дословно — это одна строка на ~1,4 КБ; изменение в ней **только на хвосте**: к 62 именам
дописаны `'register_brain_doc', 'move_into_brain', 'unregister_brain_doc'` (62 → 65). Порядок и
написание прежних 62 имён совпадают посимвольно (иначе `diff` показал бы иное — сверено полным
выводом, он приведён в отчёте сессии).

## 3. Есть ли в диффе что-то, кроме трёх новых действий — ПРЯМОЙ ОТВЕТ: НЕТ

Поимённо, чтобы не было сомнений:

- **Чужих правок — 0.** 15 файлов побайтово идентичны, включая `ReadDocs.js` (там живут `write_doc`,
  `withBrainLock_`, `registerBrainDoc_`, `getBrainManifest_`) и `Archive.js` (вся ротация журналов).
- **Экспериментов/мусора — 0.** В новом коде нет ни `Logger.log`, ни `console.*`, ни закомментированного
  кода, ни временных флагов. Только объявления двух функций + JSDoc; **исполняемого кода на верхнем
  уровне не добавлено** (значит загрузка проекта побочных эффектов не получила).
- **Стенд `_harness.js` в проект НЕ уехал.** Он есть только в `tmp/bridge_gs`, исключён
  `.claspignore`, и в свежем пулле HEAD его нет вовсе — оба пуша отчитались «Pushed 17 files»
  (16 `.js` + `appsscript.json`), стенда среди них не было.
- **Дублей имён нет** — в Apps Script все файлы делят одну глобальную область, дубль молча
  переопределил бы функцию. `grep` по всем 16 файлам: `moveIntoBrain_` и `unregisterBrainDoc_`
  объявлены **по одному разу** и вызываются по одному разу (`Bridge.js:391`, `:395`).
- **Дублей `case` нет:** `move_into_brain` (390) и `unregister_brain_doc` (394) в `switch` встречаются
  единожды.
- **Синтаксис:** `node --check` на обоих изменённых файлах — зелёный (пере проверено этой сессией).
- **Пред-существующая кривизна, НЕ из этого диффа** (чтобы не приняли за новое): у `case 'write_doc'`
  (Bridge.js:397-398) сбитый отступ — так было и в v76, дифф её не трогает.

Третье «действие» в формулировке задачи — `register_brain_doc` — это **не новый код**: `case` для него
есть и в v76 (`Bridge.js:392` там, `:400` на HEAD), и живые пробы 30.07 подтвердили это ответом прода
`not_in_brain`. Новое — только то, что мост наконец **объявляет** его в списке `actions`.

## 4. Не меняют ли новые действия поведение существующих — проверено

| что проверял | как | результат |
|---|---|---|
| `read_doc` (GET) | `ReadDocs.js` побайтово тот же (sha `d2ec0a7e7b93`); `doGet`-роутер в диффе не встречается | **не изменён** |
| `write_doc` | тот же `ReadDocs.js`; `case 'write_doc'` в диффе только как контекстная строка (без `+`/`-`) | **не изменён** |
| очередь задач (`enqueue/claim/complete/heartbeat/approve`) | ни одна из этих меток в диффе не фигурирует; `withBrainLock_` (общий замок очереди) живёт в неизменённом `ReadDocs.js` | **не изменена** |
| события/финансы (`add_event`, `delete_event`, `read_events`, `add_transaction`…) | те же файлы `Archive.js`/`BotData.js`/`Booking.js` побайтово | **не изменены** |
| ротации `prune_*` и триггеры | `Archive.js` побайтово тот же | **не изменены** |
| порядок разбора запроса | новые `case` вставлены ВНУТРЬ того же `switch (action)`, каждая ветка со `return` → **провала (fallthrough) нет**; `switch` — точное сравнение, порядок меток на прежние ветки не влияет | **не изменён** |
| авторизация | `verifyToken(body.token)` стоит ДО `switch` (Bridge.js:233) и не тронут → новые экшены за тем же токеном | **не изменена** |
| целость реестра при снятии ключа | `getBrainManifest_()` (ReadDocs.js:285) — чистое чтение свойства + `JSON.parse`, никаких дефолтов не примешивает; `unregisterBrainDoc_` делает `delete man[name]` и пишет остальное как есть | **мерж, не перезатирание** — подтверждено живым диффом реестра ДО→ПОСЛЕ: ровно 3 строки, остальные 25 пар целы |
| изоляция нового кода | `grep` по телам двух новых функций: ни `DocumentApp`, ни `LockService`, ни `SpreadsheetApp`, ни очереди, ни календаря | только `DriveApp` + `PropertiesService` |

**Единственное изменение поведения существующего экшена** — тело ответа на **неизвестный** action
(`error: 'unknown_action'`, HTTP 400): в списке `actions` стало 65 имён вместо 62. На ПК этот список
никто не парсит на состав: `brain_writer.bridge_post_actions()` (`brain_writer.py:286`) отдаёт его как
есть, тестов, сверяющих список или число 62, в репозитории нет (`grep` по `test_*.py` — 0 совпадений).

## 5. Риск ПЕРЕВЕСТИ прод на v77

| риск | оценка | почему |
|---|---|---|
| Регресс существующих действий | **очень низкий** | 15/17 файлов побайтово те же; дифф — дописок в конец файла + 2 ветки `switch` (см. §4) |
| Требуется повторная авторизация scope | **нет** | `appsscript.json` не изменён; новых сервисов Google код не зовёт |
| Код не работает | **проверен живьём** | v77 уже отработала 30.07 через временный деплой: `move_into_brain`×2 → `ok`, `register_brain_doc`×2 → `ok, total 28/29`, `unregister_brain_doc` → `ok, total 28`, страж `folder_id` → `protected` (7 боевых вызовов, ответы в `2026-07-30-brain-manifest-backup.md` §8.1) |
| Прод-URL/деплой сменится | **нет** | перевод = `clasp deploy -i <тот же id> -V 77`: id деплоя и адрес `/exec` те же, меняется только версия за ними. Новую версию НЕ создаём — v77 уже существует в истории проекта |
| Расширение прав держателя токена | **есть, и это главный минус** | после перевода `unregister_brain_doc` доступен на боевом URL постоянно, а не только в окне временного деплоя. В `REDZONE_LOCK` (токен-замок 4.2, Bridge.js:242-249) он **не внесён** — то есть `origin=agent` пройдёт без одноразового билета, защита только `confirm:true` + токен моста. Оговорка: соседи `trash_brain_file`, `move_brain_file`, `write_doc`, `register_brain_doc` в замок тоже не внесены, так что это **не новый класс дыры**, а тот же уровень; и снятие ключа у ЖИВОГО файла обратимо (`register_brain_doc` с тем же id, все id есть в бэкапе §1/§8.2 артефакта) |
| Гонка при правке реестра | **есть, унаследованная** | `unregisterBrainDoc_` делает read-modify-write свойства **без** `withBrainLock_` — ровно как `registerBrainDoc_`/`createBrainPlain_` в v76. Не регресс, но и не улучшение |
| Пин рассинхронизируется | **устраняется тем же коммитом** | `clasp_prod_pins.json` обязан получить `pinned_version: 77`, `checked_at`, `evidence`, `rollback: -V 76`; иначе строка отката в реестре пинов будет врать (гард читает файл и требует, чтобы он был под git и чистым) |

## 6. Риск НЕ переводить (тоже риск)

1. **Каждая будущая правка реестра = 4 операции `clasp` вместо 0:** `push` → `deploy` → вызовы →
   `undeploy`. Сегодня это стоило владельцу трёх «да» и создало версию 77 «на выброс».
2. **Временный деплой — публичный анонимный вход.** У проекта `access: ANYONE_ANONYMOUS`; временный
   деплой поднимает **второй** боевой URL с полными правами моста. Проба деплоя `@HEAD` дала **401**
   (закрытый) — значит каждый раз нужен именно новый анонимный, и он живёт до `clasp undeploy`.
   **Забытый `undeploy` = лишний открытый эндпоинт**, о котором никто не помнит. Это и есть «лишняя
   точка отказа» из постановки, и она страшнее самого перевода.
3. **HEAD навсегда разошёлся с продом.** Любая будущая сессия, которая сделает `clasp push` из
   старого локального каталога, **молча откатит** новые действия. Сегодня от этого спасла ручная
   сверка «пулл vs копия 29.07»; ритуалом это не закреплено.
4. **Прод продолжает врать о своих возможностях.** Неполный список `actions` — уже сработавшая мина:
   разбор 30.07 прочитал его и заключил «канала регистрации нет», хотя `register_brain_doc` в v76
   есть. Исправление этой лжи лежит **только в v77**. Пока прод на v76, следующая разведка повторит
   ту же ошибку — это класс, а не случай.
5. **Инвентарь версий копится:** каждый временный деплой добавляет номер версии; история проекта
   заполняется версиями, которые никогда не были продом.

## 7. Откат

```
clasp deploy -i AKfycb…hXNOqw -V 76 -d "откат на 76"
```

Одна команда, **секунды** (ровно так 29.07 переводили 75→76, обратная строка была заготовлена там же).
Версии Apps Script **неизменяемы**: v76 — застывший снимок прод-кода, откат возвращает его дословно,
адрес `/exec` и id деплоя не меняются, Script Properties (реестр) от версии кода не зависят и
откатом не двигаются. Второй шаг — вернуть `pinned_version: 76` в `clasp_prod_pins.json`.

**Что откатом НЕ вернётся** (и не нуждается): правки реестра 30.07 — они уже в Script Property,
живут отдельно от кода. То есть откат кода не сломает `booking_flow`/`collect_booking_spec` и не
воскресит `roadmap_master`.

---

## РЕКОМЕНДАЦИЯ (решение за владельцем, сессия сама НЕ переводит)

**Перевести прод на v77.** Дифф проверяемо аддитивный (15/17 файлов побайтово те же, `appsscript.json`
не изменён, новый код — два дописанных в конец файла обработчика без исполняемого верхнего уровня),
поведение `read_doc`/`write_doc`/очереди/событий не задето, код уже отработал 7 боевыми вызовами, а
цена ошибки — одна команда отката за секунды. Против стоит не риск перевода, а риск его отсутствия:
временный деплой — это каждый раз новый **анонимный публичный URL**, который надо не забыть снять, и
постоянный расход внимания владельца на 3 «да».

Команда владельцу (две строки, вторая — фиксация пина):

```
clasp deploy -i AKfycb…hXNOqw -V 77 -d "новые действия реестра"
clasp deployments          # сверить: прод-деплой стоит на @77, деплоев по-прежнему 2
```
затем `clasp_prod_pins.json`: `pinned_version: 77`, `checked_at: 2026-07-30`,
`evidence: docs/artifacts/2026-07-30-bridge-v77-verdict.md`, `rollback: clasp deploy -i AKfycb…hXNOqw -V 76`.

**Отдельным решением, НЕ блокирующим перевод:** внести `unregister_brain_doc` (а заодно
`trash_brain_file`/`move_brain_file`) в `REDZONE_LOCK` токен-замка 4.2, чтобы agent-вызов требовал
одноразовый билет. Это правка кода → новая версия, поэтому не мешаем ей с переводом.

## Чего эта сессия НЕ проверяла (честно)

- **Живой HEAD и живой список деплоев заново не снимались** — это `clasp` (сеть), вне read-only
  объёма задачи. Дифф построен на локальных снимках, чья тождественность прод-коду v76 доказана
  двумя записями в артефактах (§0), а не свежим ответом Google.
- **v77 сама по прод-URL не вызывалась** (её вызывали 30.07 через временный деплой, он снят).
- **Полный гейт репозитория не гонялся** — правок кода в этой задаче нет вовсе.
