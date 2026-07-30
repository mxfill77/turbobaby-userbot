# BRAIN_MANIFEST — бэкап ДО правки реестра (откат) + вердикт по каналам

> **СТАТУС: ИСПОЛНЕНО 30.07.2026 18:35** — по «да» владельца (вариант А). Итог, дифф ДО→ПОСЛЕ и
> все живые ответы моста — в **§8** в конце файла. Разделы 5–6 описывают состояние ДО этого «да»
> (там ещё «не исполнено») — они оставлены как есть, чтобы видна была причина остановки.

**Дата:** 30.07.2026, сессия Dispatch (ПК `D:\turbobaby-bot`, ветка `main`, HEAD `a8f8822`).
**Что снято:** Script Property `BRAIN_MANIFEST` проекта Apps Script «TurboBaby Bridge»
(`12iXPDU_wxcyslItPW6X41ODuoVxx2smmlQBfhSwI6Lt42MTrYbv9HhOJ`) — ЦЕЛИКОМ, дословно.
**Чем снято:** экшен моста `list_brain` через доверенного писателя (`brain_writer.list_brain()`).
Секреты берёт сам писатель; в этом файле токенов нет. Прод НЕ трогался: ни `clasp push`,
ни `clasp deploy`, ни правки кода в Apps Script — только чтение свойства.

---

## 1. МАНИФЕСТ ДО (дословно, 27 записей)

```json
{
  "cc_log": "1464zaINaLnOwXMsHNaEyy-4FpuQCVTYF",
  "cc_log_archive": "1haE-9OFgs1-ZeH-fV1g1mIxxM3L_BLYq",
  "cc_userbot_log": "1yqzVrSFRN-1Zr3y6kprn71c-T-1xTl1qfO1BzA-Rihg",
  "cowork_log": "1s9Fy3xm4FB99ah9xovLjYIMOjnDKz3Jc",
  "cowork_log_archive": "1UC-fKIpjb2zwzrvCsbJjBxmgglmSlj46",
  "cowork_log_test": "1zGPT5DqNdUT8FOrOxsVI8-mI7sZjz3kU",
  "cowork_log_test_archive": "1HvhvlxKFPiHUYlMp41yQL0-50q4Dv_zq",
  "executors_map": "1NyfcErxNt09CH8JB-V0in9e-UQB-K4_uJrwZ-FG3Za8",
  "faq": "1tv8Y-K3gLyT9Y0mXyYXZLf2c2rEzPMLHPzvg4lGqs98",
  "folder_id": "1uWqHsxk7aEWoSNqaUBMmqkYOh2UKYLkY",
  "index": "1-bH3b6c_oamqST551iLxn-voSDragdV0rUZkFwgmAqc",
  "infra": "1z2wS0I0nm-dJGqF3RRpOXlEkTKwLOsuF",
  "knowledge_base": "1Mv_zi1P33jNM0MFUBX_UEf9CnzOvCFeZ",
  "orchestrator_plan": "1_ogUGFim24Ifw60mSsfcONFc8hXMPzw539oXTbhGggo",
  "orchestrator_safety": "1UB1MWs8ZQWDkwHYBqNgK7UyVD4dkPKYdEVYo3IM2-Zs",
  "park_list": "1jD3VJGeoET8Yf5ma6iZPmwwmuw_yIYyP2A4N5RAEZho",
  "payments_plan": "13WtxQaDLdixR4EsjUFimtNhESn9nFDtk",
  "project_state": "1fuptOFp2bqZva7eJRlEanaCO6eRkAO20",
  "pulse": "1v3ezYbEeI1kGNQFI6xi8mDhnM9uE3YSE",
  "review": "1vDfD_n8i-8cSvJmqqvvEaLZ1YC639-in",
  "review_archive": "1K0gPMOyM-ER7nweda8-f9MK3edbepYCniZpQy0HkpAA",
  "roadmap_master": "1Z70EpgGZmaYMaZ064sXQlCCP8z8sFyZWfzVPvRB4jWE",
  "rules": "1AnBAniKQtrpJQevVWlzxVrdb1n51aj1G",
  "sessions_log": "1gNFRnHv09SKeagkGA3moG0VNlzffECeh",
  "sessions_log_archive": "1D-iq-Rp1g_RC9uYzqsjPJtmw6TY0l6cV",
  "state_model": "1OGUzeb60UbzAaCOsLNR_aBKFy-blfdn-",
  "turbobaby_faq": "1tv8Y-K3gLyT9Y0mXyYXZLf2c2rEzPMLHPzvg4lGqs98"
}
```

**Счёт ДО, по факту:** записей **27** = 26 ключей-документов + `folder_id` (это папка Brain, не док).
Уникальных id — 25 (`faq` и `turbobaby_faq` держат один и тот же файл). Мёртвый ключ — один:
`roadmap_master` → `1Z70Epg…4jWE`. Ключей `booking_flow` / `collect_booking_spec` (в любом
регистре) в реестре **НЕТ**.

Сверка с описью 29–30.07: совпадает ключ в ключ и id в id (`docs/artifacts/2026-07-29-brain-registry-liveness.md`,
`docs/artifacts/2026-07-30-brain-inventory.md`). За сутки реестр не двигался.

### Оговорка по откату, важная

Этот файл сохраняет **значения**, но восстановить ключ `roadmap_master` обратно каналом ПК будет
**НЕЛЬЗЯ**: единственный экшен добавления, `register_brain_doc`, требует существующего файла
(`DriveApp.getFileById` → `file_not_found`), а файл `1Z70Epg…` удалён безвозвратно (корзина с 27.06,
~30 дн обратимости вышли). То есть снятие этого ключа — операция **односторонняя**; бэкап годится
как запись в историю, а не как кнопка «вернуть». Для добавляемых ключей откат честный: их можно
переписать/переставить тем же `register_brain_doc` (`overwrite:true`).

---

## 2. ЧТО МОЖЕТ ЖИВОЙ ПРОД — по факту, а не по локальной копии кода

Прод закреплён на **версии 76** (`clasp_prod_pins.json`), поэтому судить по HEAD нельзя. Снял
список POST-экшенов у самого прода (мост отдаёт его в ответ на неизвестный `action`) —
**62 экшена**, дословно:

```
add_transaction, add_event, delete_event, read_events, check_balance, get_balance, tx_summary,
void_last, service_upsert, service_list, service_set_pin, important_add, important_list,
important_due, important_touch, important_close, audit_log, audit_list, audit_update,
create_booking, activate_booking, close_booking, write_doc, migrate_journal, create_brain_plain,
log_write, read_write_log, issue_write_ticket, consume_write_ticket, set_fleet_oil,
set_fleet_service, prune_cc_log, setup_prune_trigger, prune_review, prune_review_size,
setup_review_prune_trigger, prune_sessions_log, setup_sessions_prune_trigger, prune_cowork_log,
setup_cowork_prune_trigger, prune_cowork_selftest, list_project_triggers, enqueue_task, claim_task,
complete_task, set_needs_approval, approve_task, task_heartbeat, get_pending,
upload_passport_photo, ocr_passport, save_passport, make_contract, seed_cost_models,
closing_upsert, closing_get, closing_list, state_set, state_get, state_list, trash_brain_file,
move_brain_file
```

Этот список — то, чем мост САМ себя объявляет (`Bridge.js`, ветка `default` в `doPost`); он
неполон: `register_brain_doc` в нём не объявлен, хотя `case` для него в роутере есть
(`Bridge.js:392`). Проверять наличие — только вызовом.

**Экшена, снимающего ключ из манифеста, в списке НЕТ — и в коде тоже нет ни одного.**
Все правки реестра в коде проекта делают четыре функции, и все они ключи только ДОБАВЛЯЮТ или
ПЕРЕВЕШИВАЮТ: `setupBrain()`, `registerBrainDoc_()`, `createBrainPlain_()`, `migrateJournalToPlain_()`.

---

## 3. Почему свойство «без редеплоя» правится не всегда

Script Properties читает и пишет **только код, живущий внутри проекта** (`PropertiesService`).
У Google нет REST-эндпоинта свойств: Apps Script API умеет `projects.getContent/updateContent`,
`versions`, `deployments`, `scripts.run` — свойств среди ресурсов нет. Поэтому:

| операция реестра | канал в проде | вердикт |
|---|---|---|
| ПРОЧИТАТЬ манифест | `list_brain` | ✅ есть, сделано (раздел 1) |
| ДОБАВИТЬ ключ на существующий файл **в папке Brain** | `register_brain_doc` | ✅ есть (граница: файл обязан лежать в Brain, ключ — только `[a-z0-9_]`) |
| ПЕРЕВЕСИТЬ ключ на другой id | `register_brain_doc` + `overwrite:true` | ✅ есть |
| **СНЯТЬ (удалить) ключ** | — | ❌ **канала нет**: ни экшена, ни функции, ни REST API свойств |

Отсюда: правка «свойство меняется без редеплоя» верна для ДОБАВЛЕНИЯ и ПЕРЕВЕШИВАНИЯ и неверна
для УДАЛЕНИЯ — удаление требует новой функции в коде проекта, то есть `clasp push` + исполнение
нового кода (прод закреплён на v76, значит нужен ещё и временный деплой, как 29.07). Задача
`clasp push`/`deploy` запретила → **остановка, решение владельца.**

---

## 4. ЖИВЫЕ ПРОБЫ 30.07 — что именно спрошено у прода

Все вызовы — через `brain_writer` (секреты у него), прод v76, `clasp` не запускался ни разу.

| # | проба | дословный ответ прода | вывод |
|---|---|---|---|
| 1 | GET `list_brain` | 27 записей (раздел 1) | реестр снят целиком, бэкап есть |
| 2 | POST неизвестный action | `actions:[…62…]` | канала снятия ключа НЕТ |
| 3 | POST `register_brain_doc name=booking_flow id=11HvMKG…` | `{"ok":false,"error":"not_in_brain","id":"11HvMKG…","title":"KB_booking_flow"}` | файл существует и мосту ВИДЕН (имя вернулось), но лежит **НЕ в папке Brain** → регистрация отбита ДО правки манифеста |
| 4 | POST `register_brain_doc name=collect_booking_spec id=1PZ7Te…` | `{"ok":false,"error":"not_in_brain","id":"1PZ7Te…","title":"KB_collect_booking_spec"}` | то же |
| 5 | GET `read_doc id=11HvMKG…` | ok, **6 501** симв., голова `# TurboBaby — Анализ диалогов: флоу брони и логика для userbot-сборщика` | ЖИВ |
| 6 | GET `read_doc id=1PZ7Te…` | ok, **4 553** симв., голова `collect_booking.py — СПЕЦИФИКАЦИЯ + готовый промпт-парсер (для Claude Code)` | ЖИВ |
| 7 | GET `read_doc name=booking_flow` и `name=KB_booking_flow` | оба `unknown_name` | по имени НЕ читаются (не зарегистрированы) |
| 8 | GET `list_brain` ПОВТОРНО, дифф с бэкапом раздела 1 | **дифф ПУСТ** | реестр за сессию не изменился ни на символ |

**Итог по правкам реестра: снято ключей 0, добавлено 0.** Счёт ключей ДО = ПОСЛЕ = **27**
(26 документов + `folder_id`).

## 5. Обе операции задачи — вердикт по факту

**(1) Снять `roadmap_master` — НЕ ИСПОЛНЕНО, канала нет.** Ни экшена, ни функции (раздел 2),
ни REST API свойств (раздел 3). Это не «не нашёл, как», а «его не существует».

**(2) Перенести два дока в КОРЕНЬ папки Brain — НЕ ИСПОЛНЕНО.** Мост move **умеет**
(`move_brain_file` есть в живом списке, проба 2), но его контракт — **Brain → Brain/`<подпапка>`**:

- источник: `if (!inBrain) return not_in_brain` — двигает только то, что уже лежит ПРЯМО в Brain;
  наши файлы вне Brain (живой факт, пробы 3–4) → отбой на входе;
- назначение: всегда `getOrCreateFolder_(brain, folder)`, то есть **подпапка** (дефолт `_archive`).
  Корень Brain как цель этот экшен выразить не может, а задача прямо требует корень, не `_archive`.

Код `BrainTrash.gs` **идентичен** в двух пуллах прод-кода (`tmp/bridge_v75` = v75 и `tmp/bridge_gs`
= HEAD/v76, `diff` пуст) → на какой бы из этих версий ни стоял прод, отбой тот же.
`move_brain_file` на живых id я НЕ вызывал: при исправном страже это дало бы тот же `not_in_brain`
(бесполезно), а при неисправном — увело бы файл в `_archive`, чего задача запретила.

**(3) Регистрация после переноса возможна, но ключи будут строчными.** `registerBrainDoc_`
требует `/^[a-z0-9_]+$/`, поэтому ключ `KB_booking_flow` невозможен в принципе (`bad_name`).
Ключи будут `booking_flow` и `collect_booking_spec`, чтение — `read_doc name=booking_flow`
(так и планировалось записью `review_archive`:54). Это ограничение прода, а не выбор сессии.

## 6. МАШИННЫЙ ПУТЬ — готов, ждёт «да» владельца

### Вариант А (рекомендую): один `clasp push` + ВРЕМЕННЫЙ деплой, прод не двигаем

Ровно тот приём, которым 29.07 проверяли ротацию: `clasp push` льёт код в HEAD, прод закреплён на
**v76** и остаётся на нём; для исполнения нового кода поднимается **временный** деплой, после
работы — `clasp undeploy`. Прод-URL и версия прода не меняются вообще.

Добавить в `BrainTrash.gs` (или `ReadDocs.gs`) две функции и два `case` в `doPost`:

```js
/** Снять ключ из BRAIN_MANIFEST. Односторонняя операция — см. оговорку об откате. body:{name, confirm:true} */
function unregisterBrainDoc_(body) {
  var p = body || {};
  var name = String(p.name == null ? '' : p.name).trim();
  if (!name) return { ok: false, error: 'need_name' };
  if (name === 'folder_id') return { ok: false, error: 'protected', message: 'folder_id не снимаем' };
  if (p.confirm !== true) return { ok: false, error: 'need_confirm' };
  var man = getBrainManifest_();
  if (!(name in man)) return { ok: true, name: name, already_absent: true, total: Object.keys(man).length };
  var oldId = man[name];
  delete man[name];
  PropertiesService.getScriptProperties().setProperty(BRAIN_PROP_KEY, JSON.stringify(man));
  return { ok: true, name: name, removed_id: oldId, total: Object.keys(man).length };
}

/** Перенести существующий KB_*-файл В КОРЕНЬ папки Brain (наружу→внутрь; move_brain_file умеет только наоборот). */
function moveIntoBrain_(body) {
  var p = body || {};
  var id = String(p.id || '').trim();
  if (!id) return { ok: false, error: 'no_id' };
  try {
    var f = DriveApp.getFileById(id);
    var title = f.getName();
    if (title.indexOf('KB_') !== 0) return { ok: false, error: 'not_kb_file', id: id, title: title };
    var ps = f.getParents();
    while (ps.hasNext()) {
      if (ps.next().getId() === BRAIN_FOLDER_ID) return { ok: true, id: id, title: title, already_in_brain: true };
    }
    f.moveTo(DriveApp.getFolderById(BRAIN_FOLDER_ID));   // корень Brain, НЕ _archive
    return { ok: true, id: id, title: title, moved_to: BRAIN_FOLDER_ID };
  } catch (err) { return { ok: false, error: 'move_failed', id: id, message: String(err) }; }
}
```

```js
      case 'unregister_brain_doc':
        return jsonResponse(Object.assign({ action }, unregisterBrainDoc_(body)));
      case 'move_into_brain':
        return jsonResponse(Object.assign({ action }, moveIntoBrain_(body)));
```

Порядок исполнения (5 вызовов, всё уже обёрнуто в `brain_writer`, кроме двух новых экшенов):
`move_into_brain`×2 → `register_brain_doc`×2 (заработает, файлы уже в Brain) →
`unregister_brain_doc name=roadmap_master confirm=true` → `list_brain` + `read_doc name=…`×2 на сверку.
Ожидаемый счёт ключей: **28** (27 − `roadmap_master` + 2 спеки).

### Вариант Б: Drive-UI автоматикой в браузере владельца

Технически возможно (переложить файл в папку Brain в веб-Drive), но это UI-путь мимо канала,
без обратной сверки и без аудита `log_write`. **Не рекомендую** и без прямого «да» не делаю.

### Что отклонено осознанно

- `create_brain_plain` + `register_brain_doc` (создать в Brain plain-копию и зарегистрировать её) —
  формально работает БЕЗ правки кода, но это **создание документа и дубль текста**, а задача
  запретила и создавать доки, и множить тексты. Отклонено.
- «Перевесить» `roadmap_master` на живой файл через `overwrite:true` — ключ остался бы в реестре,
  вопрос не решён, зато появился бы ложный алиас. Отклонено.

## 7. Что изменено на ПК за сессию

`brain_writer.py` — **+3 обёртки, НЕ закоммичено** (по границе задачи «код не коммитить»):
`list_brain()` (GET `list_brain`), `register_doc()` (POST `register_brain_doc`, ответ отдаётся как
есть — `not_in_brain` это факт, а не авария), `bridge_post_actions()` (список экшенов живого прода)
+ CLI `--list-brain` / `--register КЛЮЧ=ID` / `--probe-actions`. Зачем в писателе: реестр отдаётся
только экшеном моста, а разовому скрипту нельзя самому брать `BRIDGE_TOKEN` (класс 328) — это ровно
тот блокер, о который упёрлась задача 58 и сессия 30.07 (`2026-07-30-brain-dead-pointer-cleanup.md`,
§2.2). Решение владельца: закоммитить обёртки или откатить их (`git checkout -- brain_writer.py`).

Больше на ПК не изменено ничего: `clasp` не запускался (ни `push`, ни `deploy`, ни `pull`), Brain-доки
не правились, файлы Drive не двигались и не создавались, `.env` не открывался, клиентский контур не тронут.

*(состояние раздела 7 — на момент остановки; что изменилось после «да» — в §8.7)*

---

# §8. ИСПОЛНЕНО 30.07 18:00–18:35 — по «да» владельца (вариант А)

## 8.1 Порядок и живые ответы моста (все — дословно)

| # | вызов | дословный ответ |
|---|---|---|
| 1 | `clasp pull` в чистый каталог `tmp/bridge_head` | 17 файлов; **дифф с копией 29.07 — ПУСТ во всех 17** → HEAD не двигался с того пуша, то есть HEAD = код прод-версии 76 |
| 2 | `clasp push` (патч на 2 файла) | `Pushed 17 files.` — залито в **HEAD**, прод остался закреплён на `@76` |
| 3 | проба деплоя `@HEAD` (он у проекта уже был) | **HTTP 401** — закрытый доступ, скриптом не позвать → временный деплой всё же нужен |
| 4 | `clasp deploy` | `Created version 77.` + временный деплой `AKfycbxC…kg @77` (прод-деплой не тронут) |
| 5 | `move_into_brain id=11HvMKG…` | `{"ok":true,"title":"KB_booking_flow","moved_from":"_archive","moved_to":"1uWqHsxk7aEWoSNqaUBMmqkYOh2UKYLkY","into_brain":true}` |
| 6 | `move_into_brain id=1PZ7Te…` | `{"ok":true,"title":"KB_collect_booking_spec","moved_from":"_archive","moved_to":"1uWqHsxk…","into_brain":true}` |
| 7 | `register_brain_doc booking_flow=11HvMKG…` | `{"ok":true,"registered":true,"total":28}` |
| 8 | `register_brain_doc collect_booking_spec=1PZ7Te…` | `{"ok":true,"registered":true,"total":29}` |
| 9 | `unregister_brain_doc roadmap_master confirm=true` | `{"ok":true,"removed_id":"1Z70EpgGZmaYMaZ064sXQlCCP8z8sFyZWfzVPvRB4jWE","unregistered":true,"total_before":29,"total":28}` |
| 10 | `unregister_brain_doc folder_id` (проба стража) | `{"ok":false,"error":"protected","message":"folder_id — адрес папки Brain, не снимаем"}` |
| 11 | `clasp undeploy AKfycbxC…kg` | `Undeployed.` → `clasp deployments`: снова **2 деплоя**, прод `AKfycb…hXNOqw @76` |

**НАЙДЕНО ПО ХОДУ, важное:** оба дока лежали **не «неизвестно где», а в `Brain/_archive`** —
подпапке самой папки Brain (`moved_from: "_archive"`). Отсюда и `not_in_brain` у
`register_brain_doc`: его страж смотрит ТОЛЬКО прямых родителей, а прямым родителем был `_archive`.
Опись 30.07 писала «лежат ВНЕ папки TurboBaby Brain» — точнее будет «в подпапке `_archive`, а не
в корне». Кто и когда их туда отправил — по каналу не спросить; вероятнее всего ревизия 16.07,
которая двигала в `_archive` план-листы.

## 8.2 Реестр ПОСЛЕ — целиком (28 записей = 27 доков + `folder_id`)

Снято `list_brain` через **ПРОД** (`@76`), не через временный деплой: Script Properties у проекта
одни, поэтому прод видит ровно то же свойство.

```json
{
  "booking_flow": "11HvMKGZRdSoRjojvAcmpN6_KJzkc1eRlIIx12JW6DQU",
  "cc_log": "1464zaINaLnOwXMsHNaEyy-4FpuQCVTYF",
  "cc_log_archive": "1haE-9OFgs1-ZeH-fV1g1mIxxM3L_BLYq",
  "cc_userbot_log": "1yqzVrSFRN-1Zr3y6kprn71c-T-1xTl1qfO1BzA-Rihg",
  "collect_booking_spec": "1PZ7TeEQJLtzrw66Ll3PZBXIw-K70dnkh-3ClxDEMW48",
  "cowork_log": "1s9Fy3xm4FB99ah9xovLjYIMOjnDKz3Jc",
  "cowork_log_archive": "1UC-fKIpjb2zwzrvCsbJjBxmgglmSlj46",
  "cowork_log_test": "1zGPT5DqNdUT8FOrOxsVI8-mI7sZjz3kU",
  "cowork_log_test_archive": "1HvhvlxKFPiHUYlMp41yQL0-50q4Dv_zq",
  "executors_map": "1NyfcErxNt09CH8JB-V0in9e-UQB-K4_uJrwZ-FG3Za8",
  "faq": "1tv8Y-K3gLyT9Y0mXyYXZLf2c2rEzPMLHPzvg4lGqs98",
  "folder_id": "1uWqHsxk7aEWoSNqaUBMmqkYOh2UKYLkY",
  "index": "1-bH3b6c_oamqST551iLxn-voSDragdV0rUZkFwgmAqc",
  "infra": "1z2wS0I0nm-dJGqF3RRpOXlEkTKwLOsuF",
  "knowledge_base": "1Mv_zi1P33jNM0MFUBX_UEf9CnzOvCFeZ",
  "orchestrator_plan": "1_ogUGFim24Ifw60mSsfcONFc8hXMPzw539oXTbhGggo",
  "orchestrator_safety": "1UB1MWs8ZQWDkwHYBqNgK7UyVD4dkPKYdEVYo3IM2-Zs",
  "park_list": "1jD3VJGeoET8Yf5ma6iZPmwwmuw_yIYyP2A4N5RAEZho",
  "payments_plan": "13WtxQaDLdixR4EsjUFimtNhESn9nFDtk",
  "project_state": "1fuptOFp2bqZva7eJRlEanaCO6eRkAO20",
  "pulse": "1v3ezYbEeI1kGNQFI6xi8mDhnM9uE3YSE",
  "review": "1vDfD_n8i-8cSvJmqqvvEaLZ1YC639-in",
  "review_archive": "1K0gPMOyM-ER7nweda8-f9MK3edbepYCniZpQy0HkpAA",
  "rules": "1AnBAniKQtrpJQevVWlzxVrdb1n51aj1G",
  "sessions_log": "1gNFRnHv09SKeagkGA3moG0VNlzffECeh",
  "sessions_log_archive": "1D-iq-Rp1g_RC9uYzqsjPJtmw6TY0l6cV",
  "state_model": "1OGUzeb60UbzAaCOsLNR_aBKFy-blfdn-",
  "turbobaby_faq": "1tv8Y-K3gLyT9Y0mXyYXZLf2c2rEzPMLHPzvg4lGqs98"
}
```

## 8.3 ДИФФ ДО → ПОСЛЕ — ровно три строки

```diff
+  "booking_flow": "11HvMKGZRdSoRjojvAcmpN6_KJzkc1eRlIIx12JW6DQU",
+  "collect_booking_spec": "1PZ7TeEQJLtzrw66Ll3PZBXIw-K70dnkh-3ClxDEMW48",
-  "roadmap_master": "1Z70EpgGZmaYMaZ064sXQlCCP8z8sFyZWfzVPvRB4jWE",
```

Это машинный `diff -u` бэкапа §1 против живого снимка ПОСЛЕ; **других изменённых строк в нём нет**,
то есть все прочие **25 пар «ключ → id» целы посимвольно**, включая `folder_id`, дубль-алиас
`turbobaby_faq` (тот же id, что у `faq`) и медленный `review_archive`.

| счёт | ДО | ПОСЛЕ |
|---|---|---|
| записей в свойстве | 27 | **28** |
| из них `folder_id` (папка) | 1 | 1 |
| ключей-документов | 26 | **27** |
| уникальных id доков | 25 | **26** |
| мёртвых ключей | **1** (`roadmap_master`) | **0** |
| живых доков вне реестра | ≥4 | **2** (`KB_trainer_log` + его архив `1lOco1SI…`, не трогал) |

## 8.4 Сверка чтением ПО ИМЕНИ (через прод, не через временный деплой)

```
read_doc name=booking_flow          → ok, 6501 символов, «# TurboBaby — Анализ диалогов: флоу брони и логика для userbot-сборщика»
read_doc name=collect_booking_spec  → ok, 4553 символов, «collect_booking.py — СПЕЦИФИКАЦИЯ + готовый промпт-парсер (для Claude Code)»
read_doc name=roadmap_master        → {"ok":false,"error":"unknown_name","message":"Нет имени \"roadmap_master\" в манифесте…"}
```

Длины совпали с описью 30.07 (6 501 / 4 553) — переносом текст не тронут. Про мёртвый ключ ответ
сменился с `read_failed` (ключ был, файла не было) на **`unknown_name`** — это и есть доказательство,
что ключа в реестре больше нет.

**Ключи строчные — ограничение прода, не выбор:** `registerBrainDoc_` требует `/^[a-z0-9_]+$/`,
поэтому `KB_booking_flow` невозможен (`bad_name`). Читать — `read_doc name=booking_flow` и
`name=collect_booking_spec` (как и планировалось записью `review_archive`:54).

## 8.5 Прод: не двигался

```
до работы : 2 деплоя — AKfycbza…nI9m @HEAD (закрытый, 401) и AKfycb…hXNOqw @76 ← ПРОД
после     : 2 деплоя — те же самые; прод по-прежнему @76
```

`clasp_prod_pins.json` не правил: `pinned_version` = 76 остался верным. В истории версий проекта
осталась **версия 77** (её создаёт `clasp deploy`; `undeploy` снимает деплой, а не версию) — прод
на неё не переводился и её код от прод-кода отличается только двумя новыми экшенами. HEAD теперь
= v77-код: если владелец захочет иметь новые экшены в проде, это отдельное решение
(`clasp deploy -i AKfycb…hXNOqw -V 77`), сегодня НЕ делалось.

## 8.6 Что добавлено в код Apps Script (HEAD, 2 файла)

- `BrainTrash.gs`: `moveIntoBrain_()` — перенос `KB_*`-файла в **корень** Brain (страж `not_kb_file`,
  идемпотентность `already_in_brain`, в ответе — прежняя папка); `unregisterBrainDoc_()` — снятие
  ключа (нужен `confirm:true`, `folder_id` защищён, прочие ключи мержатся, а не затираются).
- `Bridge.gs`: два `case` в `doPost` + в объявляемый список экшенов дописаны `move_into_brain`,
  `unregister_brain_doc` и **`register_brain_doc`** (последний существовал, но себя не объявлял —
  из-за этого разбор 30.07 читал список как «канала регистрации нет»).

Локальная копия с патчем — `tmp/bridge_head/` (пулл HEAD + правка, `node --check` зелёный на обоих
файлах). Прод-код (v76) остался без изменений.

## 8.7 Что изменено на ПК

`brain_writer.py` — **+5 обёрток** (`list_brain`, `register_doc`, `move_into_brain`,
`unregister_doc`, `bridge_post_actions` + общий транспорт `_brain_admin_post`) и CLI-ключи
`--list-brain / --register КЛЮЧ=ID / --move-into-brain ID / --unregister КЛЮЧ / --probe-actions`.
Секреты по-прежнему берёт только сам писатель — вызывающие скрипты токена не видят (класс 328).
Гейт по своим файлам: `python -m unittest test_brain_writer` → **26 тестов OK**. Полный прогон 34
тест-модулей НЕ гонял осознанно: в дереве незакрытая работа параллельной сессии
(`pc_orchestrator.py`, `pretool_guard.py`), полный гейт мерил бы её, а не мою правку.
Коммит — поимённо `brain_writer.py` + этот артефакт; чужие файлы не тронуты и не закоммичены.
