# Разведка: что сломается, если снять «anyone with the link» с «Актуальная Система Учета»

**Дата:** 26.08.2026, ПК `D:\turbobaby-bot`, ветка `main`. **Только чтение.**
Прав доступа Drive не менял ни на одном объекте, ничего не удалял, боевых записей не делал,
процессов не трогал, `.env` и конфиги не открывал. В папку «Паспорта» не заходил.

**Временное:** `D:\turbobaby-bot\tmp\drive_public_recon_20260826\` —
`bridge_head/` (свежий `clasp pull` моста, 18 файлов), `form_script/` (свежий `clasp pull`
проекта формы, 8 файлов), `main_folder_list.html`, `main_folder_entries.txt`,
`tg_chat_headers.txt`, `tg_link_lines.txt`. Ничего из этого не удалял.

---

## СВОДКА

**Главное — вопрос поставлен наоборот от риска.** Замером анонимного HTTP (без входа в аккаунт,
негативный контроль сработал) подтверждено: `19UHmWP1dsb1ADk8nwpZThOP0pCu_uhea` — это и есть
**«Актуальная Система Учета»** (заголовок страницы), и сейчас она **читается кем угодно по
ссылке**, вместе со всем содержимым, включая **CRM «менеджеру Байки»** (имена, телефоны,
депозиты клиентов) и **«Зарплаты»**. Экспорт CSV этих таблиц анонимно отдаёт HTTP 200.

**Ни одна машинная ветка на «anyone» не стоит.** Мост (`Bridge`) — Web App с
`executeAs: USER_DEPLOYING`, `access: ANYONE_ANONYMOUS`; это настройка **деплоя**, а не Drive:
код исполняется правами владельца и до Drive добирается независимо от общего доступа папки.
Репозиторий ПК ссылок Drive **не строит и наружу не отдаёт ни одной** — там только Bridge-URL и
Google Maps клиента.

**Ломается ручное потребление людьми,** и оно вcё — внутреннее либо разовое-наружное:
ссылка на договор в рабочую TG-группу, ссылка на папку учёта в WhatsApp-группу команды,
и три отданные наружу ссылки на файлы Brain (пакет аудита + два раздела пакета партнёру).

**Папка договоров и клиентские договоры под «anyone» НЕ лежат** — измерено: `1GX7SIqo…`
анонимно уводит на вход, и в листинге «Актуальной Системы Учета» её нет вовсе.
А вот **«Паспорта» и «Rental Agreement (File responses)» лежат прямо в ней** (id папки
«Паспорта» дословно совпал с `PASSPORT_FOLDER_ID` в коде).

**По п.4 честный ответ — НЕИЗВЕСТНО:** перечня явных reader/writer снять нечем (см. §4).

---

## ТАБЛИЦА: что перестанет работать и чем заменяется

| # | Что именно перестанет работать | Кто потребитель | Чем доказано | Чем заменяется |
|---|---|---|---|---|
| 1 | **Ничего в коде моста.** `make_contract`, `upload_passport_photo`, все чтения/записи листов | Bridge Web App | `appsscript.json`: `executeAs: USER_DEPLOYING`, `access: ANYONE_ANONYMOUS` — исполняется правами владельца; в 18 файлах моста **ноль** вызовов `setSharing/addViewer/addEditor/Access.*` | Замена не нужна. Ветка не задета |
| 2 | **Ничего в репозитории ПК.** userbot/suggest/moderation | клиентский контур | Полный свод по дереву: ссылок `drive.google.com` / `docs.google.com/(document\|spreadsheets\|forms\|file)` в коде — **0** (§1) | Замена не нужна |
| 3 | **Ссылка на готовый договор** в TG-группу «Входящие брони» | Пым и менеджеры (люди) | `Contract.js:421` → `getUrl()`; `splinter.py:6157,6163` шлёт `✅ Договор готов: {url}` в `GROUPS` `-1003997419806` | Явная выдача прав по адресам сотрудников на **папку договоров** (она и так не публичная — п.9). Сервисный аккаунт не поможет: открывает человек |
| 4 | **Ссылка на папку учёта в WhatsApp-группе команды** «Turbo baby accounting» | тайская команда | `whatsapp-bot/wa_export/019,021`: `drive.google.com/drive/folders/1v7oMvXq…` (`fromMe:false`), заголовок анонимно — «Reports 2026» | Явная выдача reader по адресам Google-аккаунтов сотрудников. У кого аккаунта нет — публикация отдельной копии/экспорта |
| 5 | **Три отданные наружу ссылки на файлы Brain** (пакет аудита v2, два раздела пакета партнёру) | внешний аудитор, партнёр | Журнал 05.08 и 15.08; анонимно открываются сейчас: `KB_audit_package_v2`, `KB_partner_envmap_brain` | Публикация **отдельной копии** вне «Актуальной Системы Учета» под свою ссылку — пакеты разовые, держать ради них публичным весь учёт нельзя |
| 6 | **Вся папка `TurboBaby Brain`** перестанет открываться по ссылке | внешние получатели пакетов; сам владелец с чужого устройства | `ReadDocs.js:15` `BRAIN_PARENT_ID = '19UHmWP1…'` — Brain **внутри** главной папки; анонимно заголовок «TurboBaby Brain» | Мосту не нужно (п.1). Людям — явная выдача, либо копия наружу |
| 7 | **Форма Rental Agreement — не ломается** | менеджер, заполняющий форму | Форма и её скрипт лежат в главной папке, но приём ответов правами Drive не управляется; загрузка файла в Google Forms **и так** требует входа в Google-аккаунт (§3) | Замена не нужна |
| 8 | **Открытие таблиц с телефона/чужого браузера без входа** — CRM, «Байки», «Цены», «Зарплаты» | владелец, менеджеры | Анонимный экспорт CSV: HTTP 200 у всех четырёх (§5) | Вход в свой Google-аккаунт + явная выдача reader/writer по адресам |
| 9 | **Папка готовых договоров — НЕ задета** | — | `1GX7SIqo…` анонимно → `accounts.google.com` (вход), и её нет в листинге главной папки | Замена не нужна; менять там ничего не требуется |
| 10 | **«Паспорта» и «Rental Agreement (File responses)»** — НЕ ПРОВЕРЯЛ | — | Обе папки **есть в листинге** «Актуальной Системы Учета» (§5). Заходить внутрь запрещено заданием | Проверить владельцу лично. Если публичны — это не «сломается», а «должно быть закрыто» |

---

## §1. Репозиторий ПК `D:\turbobaby-bot` — где строится/отдаётся ссылка Drive

**Нигде.** Свод по всему рабочему дереву (исключены `venv`, `node_modules`, выгрузки
`exporttg27052026`/`data_export`, `.git`), по шаблону
`drive\.google\.com|docs\.google\.com/(document|spreadsheets|forms|file)` и отдельно по
`getUrl(|webViewLink|usp=sharing|export=download`:

* в **коде** (`*.py`, `*.js`, `*.json`, `*.txt`, `*.html`) — **0 совпадений**;
* совпадения есть только в: `docs/artifacts/*` (тексты отчётов), снимках журнала
  (`_scratch_*/cowork_log*.txt`), выгрузке WhatsApp и выгрузке Telegram — это **данные, не код**.

Единственные «гугловые» ветки кода ПК:

* `pretool_guard.py:392`, `1130`, `1133` — распознавание боевого контура по хосту
  `script.google.com|sheets.googleapis.com` (гард, не выдача ссылок);
* `delivery.py:104-344`, `booking_draft.py:326-335`, `suggest.py:2950` — **входящие** ссылки
  Google **Maps** от клиента (гео доставки), к Drive отношения не имеют;
* `brain_writer.py`, `cowork_log_append.py`, `bridge_http.py`, `pricing.py:112` — HTTP к
  **Bridge Web App** (`/exec` → 302 на `googleusercontent`), а не ссылки на объекты Drive.

Действия моста `make_contract` / `upload_passport_photo` / `ocr_passport` из ПК-репозитория
**не зовутся ни разу** — их зовёт полоса VPS (§2, потребители).

### Косвенный замер: уходят ли ссылки Drive клиенту

* `client_chats.jsonl` (сырой корпус клиентских диалогов): вхождений `drive/docs.google.com` —
  **1**, и оно от **клиента** (`"who": "client"`). От компании — **0**.
* Выгрузка Telegram аккаунта менеджера `@turbophuket` (27.05.2026): 6 ссылок Drive/Docs.
  Две — в **«Избранном»** (`saved_messages`, сам себе, среди них ссылка на папку договоров
  `1GX7SIqo…` от 12.07.2024). Четыре — в личных чатах (`Jeggi по байкам +10%`, `Àrtem`, `Web`,
  `Арина`), и **все четыре присланы контрагентом**, не менеджером
  (`from_id` ≠ `user6879003264`).
* Выгрузка WhatsApp (129 чатов): 3 ссылки, **все входящие** (`fromMe:false`).

Вывод: **клиентам ссылки Drive не отдаются вовсе** — ни ботом, ни исторически человеком.

---

## §2. Apps Script моста (свежий `clasp pull`, `scriptId 12iXPDU_…`)

Взято **живой головой проекта** сегодня в `tmp/drive_public_recon_20260826/bridge_head`:
18 файлов (в прежнем локальном срезе `tmp/bridge_gs` их 17 — там нет `ServiceUndo.js`, а
`BotData.js` короче на 14 КБ; старый срез для этой задачи негоден).
**Прод при этом закреплён на версии 78** (`clasp_prod_pins.json`, проверено 04.08) — HEAD может
быть впереди; для вопроса о ссылках это ничего не меняет, ветки одни и те же.

### 2.1 Где формируется ссылка — ровно два места

**Договор** — `Contract.js`:

```js
 12: var CONTRACT_TEMPLATE_ID = '1Hh2GlpxJsa4DplbKYaM1X0Nt6DLCWYQb6znWcG61tOE';
 13: var CONTRACT_FOLDER_ID = '1GX7SIqoskzSuThS4e4EbjeX-B0N_CKxK';
...
413:      var copy = DriveApp.getFileById(CONTRACT_TEMPLATE_ID).makeCopy(fname, DriveApp.getFolderById(CONTRACT_FOLDER_ID));
...
421:    var url = DriveApp.getFileById(fileId).getUrl();
423:    return { ok: true, file_id: fileId, url: url, name: fname, regenerated: regenerated,
```

**Фото паспорта** — `Passport.js`:

```js
 12: var PASSPORT_FOLDER_ID = '1OhIwNe98D5zN6jp51GVTghnFrkd0nRPu';   // папка Drive «Паспорта»
...
 92:    var folder = DriveApp.getFolderById(PASSPORT_FOLDER_ID);
 93:    var file = folder.createFile(blob);
 94:    return { ok: true, file_id: file.getId(), url: file.getUrl() };
```

Больше `getUrl()` в мосте нет. `sheetFullAddr_` — **не ссылка**, а строка адреса:

```js
Config.js:108: function sheetFullAddr_(spreadsheetId, sheetName, a1Range) {
Config.js:109:   return String(spreadsheetId) + ' | ' + String(sheetName) + ' | ' + String(a1Range);
```

### 2.2 Предполагается ли открытие без входа в аккаунт

**Нет — нигде.** В 18 файлах моста **ноль** вхождений `setSharing`, `Access.`, `Permission.`,
`ANYONE`, `addViewer`, `addEditor`, `getSharing`, `getEditors`, `getViewers`. Мост права
**не выдаёт и не читает** — он молча полагается на то, что папка уже настроена. Из этого следуют
два факта:

1. снятие «anyone» мост **не заметит** и не сломает: `appsscript.json` даёт
   `"executeAs": "USER_DEPLOYING"` — код ходит в Drive правами владельца;
2. `"access": "ANYONE_ANONYMOUS"` — это **доступ к Web App**, отдельная настройка деплоя.
   Снятие общего доступа Drive её не трогает; защита эндпойнта — токен из Script Properties
   (`Config.js:70-80`). Путать эти две «anyone» — готовый ложный диагноз.

### 2.3 Куда мост кладёт объекты (что попадёт под снятие)

```js
Config.js:24:    PASSPORTS:  '1OhIwNe98D5zN6jp51GVTghnFrkd0nRPu',
Config.js:25:    CONTRACTS:  '1GX7SIqoskzSuThS4e4EbjeX-B0N_CKxK',
Config.js:26:    MAIN:       '19UHmWP1dsb1ADk8nwpZThOP0pCu_uhea',
```

```js
BotData.js:138:    const file = DriveApp.getFileById(ss.getId());
BotData.js:139:    const folder = DriveApp.getFolderById(CONFIG.FOLDERS.MAIN);
BotData.js:140:    folder.addFile(file);
```

```js
ReadDocs.js:15: var BRAIN_PARENT_ID = '19UHmWP1dsb1ADk8nwpZThOP0pCu_uhea'; // главная папка компании
ReadDocs.js:16: var BRAIN_FOLDER_NAME = 'TurboBaby Brain';
```

То есть **и `Bot Data`, и вся папка `Brain`** (KB-доки, `cowork_log`, выгруженные пакеты) —
внутри «Актуальной Системы Учета» и наследуют её общий доступ.

### 2.4 Кто потребляет `url` договора (полоса VPS, клон `D:\foreign\turbobaby-manager-bot`)

```py
splinter.py:6153:                res = bridge.make_contract(booking_key=bk, name=nm, date_start=ds)
splinter.py:6157:            log.info(f"  🆕 INTAKE: договор готов {res.get('name')} → {res.get('url')}")
splinter.py:6163:                        text=f"🐀 Splinter\n✅ Договор готов: {res.get('url')}\n{reminder}")
```

Адрес — `chat_id` того чата, где дана команда; для intake-ветки это `GROUPS`
(`splinter.py:161-168`), группа `-1003997419806` «TurboBaby — Входящие брони».
**Клиенту эта ссылка не уходит ни одной веткой.**
Фоновая перегенерация (`bot.py:1124`) ссылку вообще никуда не шлёт — только в лог.
Загрузка паспорта (`splinter.py:6059`) `url` из ответа **не использует** — берёт только `file_id`
и сразу отдаёт его в OCR.

---

## §3. Форма Rental Agreement

Проект скрипта формы снят свежим `clasp pull`
(`scriptId 17OTrEgB0Z8uPYNI6vuF3KzY8VRqyovQG80Igis2WOzeAAPy1aqccJ2yq`, 8 файлов) в
`tmp/drive_public_recon_20260826/form_script`.

**Сама форма:** `config.js:2` `formId: "1hGzz5rvGe-AUqBHTXI1f5H_TXZXFBJNoAEypHRy2mj4"` — в
листинге главной папки она называется **«мне и менеджеру Rental Agreement актуальное»**.

**Куда кладёт файлы:**

| что | куда | код |
|---|---|---|
| фото паспорта из поля `Passport` | **штатную папку ответов формы** — `Rental Agreement (File responses)`, она лежит В «Актуальной Системе Учета» (id `19Ndg2Wjiq4A20oYySoKJqzbpxi6gp0hDqreSxhR5PLqOIJT4UJqVBnPGdS-OACIYzh0THXyQ`) | скрипт файл **не перемещает**: берёт `fileId` из ответа (`main.js:659-676`) и отдаёт в EdenAI. `config.pasportfolderId` в боевом потоке **не используется** — единственное его вхождение вне `config.js` это ручная утилита `moveImagesToFolder()` в «удаление файлов.js» |
| готовый договор | папка `config.folderId = 1GX7SIqo…` (та же, что у моста) | `main.js:1130-1152` (`makeCopy()` → `destinationFolder.addFile()` → `getRootFolder().removeFile()`), плюс `moveDocumentToFolder` `main.js:1428-1439` |
| строка аренды | лист «клиенты» таблицы `config.sheetId = 1sL-rw0kl…` | `main.js` |

**Требует ли текущая настройка авторизации от отвечающего — ЧАСТИЧНО НЕИЗВЕСТНО.**

* Что измерено: файл формы `docs.google.com/forms/d/1hGzz5rv…/viewform` анонимно отдаёт
  **HTTP 401**. Опубликованной ссылки `/forms/d/e/…/viewform` именно этой формы в репозитории
  нет — та `1FAIpQLSf6Sfz75O…`, что лежит в выгрузке Telegram, при анонимной проверке оказалась
  **чужой формой** («Ownima beta test request»), присланной контрагентом; её за Rental Agreement
  принимать нельзя.
* Что говорит код: сбора адреса отвечающего **нет** — в `main.js`/`helper.js`/`config.js` ноль
  вхождений `RespondentEmail`, `getEmail`, `setCollectEmail`, `setRequireLogin`,
  `LimitOneResponsePerUser`. То есть галка «собирать адреса» скорее всего выключена.
* Что от прав Drive **не зависит вовсе**: у формы есть вопрос-загрузка файла (`Passport`), а
  Google для таких вопросов **всегда** требует входа в Google-аккаунт — это правило платформы,
  а не настройка папки. Поэтому **снятие «anyone» приём ответов не ломает**.
* Чем снять точный ответ: настройки самой формы (Настройки → Ответы) — только у владельца,
  read-only API формы отсюда недоступен.

**Побочно (не по заданию, но нашлось в живом коде формы):** в `config.js` боевой ключ EdenAI
лежит **открытым текстом прямо в коде проекта**. В мосте тот же ключ давно вынесен в Script
Properties (`Passport.js:105`), а у формы — нет. Про это предупреждали ещё 01.06.2026
(`manager-bot/docs/project_state.md:104`), в живом коде на 26.08.2026 не исправлено.

---

## §4. Кто имеет явные reader/writer — **НЕИЗВЕСТНО**

Ответ снять **нечем**. Что именно пробовал:

| канал | результат |
|---|---|
| Bridge (18 файлов моста) | экшена прав нет: `Bridge.js` — 79 маршрутов, ни одного про permissions; `getEditors/getViewers/getOwner` в мосте — **0 вхождений**. Добавить экшен = правка боевого кода, запрещено |
| Drive API из репозитория | клиента нет: `googleapiclient`/`google.oauth2`/`service_account`/`GOOGLE_APPLICATION_*` в коде ПК — **0** (единственные совпадения — строки-фикстуры в `test_pretool_guard.py:826`). Сервисного аккаунта нет |
| `clasp` (OAuth уже есть) | прав Drive не перечисляет ни одной командой; использовать его токен = читать файл с секретами, что запрещено заданием |
| коннектор Google Drive в сессии | не установлен: `list_connectors(["google","drive"])` → пусто; `mcp__*Google_Drive*` в реестре инструментов нет |
| браузер с живой сессией Google | `list_connected_browsers` → **пусто**, Chrome к сессии не подключён; встроенный браузер в аккаунт не залогинен |
| записи в репозитории/мозге | адресов с ролями нет: по `docs/`, `notes/`, `artifacts/`, `manager-bot/docs/` найдено 6 адресов, все — не про Drive (`git@github.com`, `noreply@anthropic.com`, `info@turbophuket.com`, `user@evil.example.com`, `%s@github.com`, `1turbobaby@gmail.com` в списке устройств RC) |

**Отдельно про «боевую таблицу Bot Data»: её id в репозитории отсутствует в принципе** — он
живёт только в Script Properties (`BotData.js:123` `props.getProperty('BOT_DATA_SHEET_ID')`), и
адресовать её отсюда не по чему. Хуже того, в главной папке лежит **33 файла с именем
`TurboBaby Bot Data`** (§5) — какой из них боевой, без Script Properties не определить.

**Чем снять (владельцу, read-only):** открыть папку «Актуальная Система Учета» и таблицу Bot
Data → «Настройки доступа» → выписать адреса и роли; либо, если нужен машинный список, дать
сессии read-only доступ (подключить Chrome с живым входом, либо коннектор Drive) — тогда
перечень снимается `permissions.list` без единой правки.

---

## §5. Замеры анонимного доступа (26.08.2026, без входа в аккаунт)

Метод: `curl` без cookies и без токена, по публичным URL. **Ничего не менялось и не
скачивалось на диск**, кроме одной HTML-страницы листинга в `tmp/…` (путь назван выше).
Негативный контроль сработал (папка договоров ушла на страницу входа) — значит проба
отличает открытое от закрытого, а не показывает 200 на всё подряд.

| объект | id | анонимно |
|---|---|---|
| **«Актуальная Система Учета»** (папка) | `19UHmWP1dsb1ADk8nwpZThOP0pCu_uhea` | **ОТКРЫТА** — HTTP 200, `<title>` = «Актуальная Система Учета» |
| `TurboBaby Brain` (папка) | `1uWqHsxk7aEWoSNqaUBMmqkYOh2UKYLkY` | **ОТКРЫТА** — 200, `<title>` = «TurboBaby Brain» |
| CRM «менеджеру Байки» | `1sL-rw0kl…` | **ЧИТАЕТСЯ** — экспорт CSV 200, `text/csv` |
| «Байки» (парк+доход) | `1ZBCmVvz…` | **ЧИТАЕТСЯ** — экспорт CSV 200 |
| «Цены» | `1tN1XY0C…` | **ЧИТАЕТСЯ** — экспорт CSV 200, 6382 байта |
| «Зарплаты» | `1hC7aA9o…` | **ЧИТАЕТСЯ** — экспорт CSV 200, `text/csv` |
| «Правила на русском» | `1c0c913U…` | **ОТКРЫТ** — 200 |
| «Шаблон» (шаблон договора) | `1Hh2Glpx…` | **ОТКРЫТ** — 200 |
| `KB_audit_package_v2` (пакет внешнего аудита) | `1WNSptrY…` | **ОТКРЫТ** — 200 |
| `KB_partner_envmap_brain` (пакет партнёру) | `1Hs9sXhO…` | **ОТКРЫТ** — 200 |
| «Reports 2026» (папка из WhatsApp) | `1v7oMvXq…` | **ОТКРЫТА** — 200 |
| **папка готовых договоров** | `1GX7SIqo…` | **ЗАКРЫТА** — уводит на `accounts.google.com`, `<title>` = «Google Drive: Sign-in» |
| форма Rental Agreement (file id) | `1hGzz5rv…` | **ЗАКРЫТА** — HTTP 401 |
| папка «Паспорта» | `1OhIwNe9…` | **НЕ ПРОВЕРЯЛ** — запрет задания |
| `Rental Agreement (File responses)` | `19Ndg2Wj…` | **НЕ ПРОВЕРЯЛ** — там те же паспортные сканы |

### Содержимое «Актуальной Системы Учета» (снято анонимно, `embeddedfolderview`)

**56 объектов.** Папки: `Rental Agreement (File responses)`, `TurboBaby Brain`, **`Паспорта`**
(id `1OhIwNe98D5zN6jp51GVTghnFrkd0nRPu` — дословно `PASSPORT_FOLDER_ID` из `Passport.js:12`),
`нововведение`, одна без имени.
Файлы: `Add new moto`, **33 × `TurboBaby Bot Data`**, `Байки`, `Зарплаты`, 2 × `Копия "Шаблон"`,
`Копия мне и менеджеру Rental Agreement актуальное`, `Обратная страничка TB Mobility`,
`Правила на русском`, `Учет_зарплат_сотрудников_шаблон.xlsx`, `Цены`, `Шаблон`,
`бэкап менеджеру Байки`, `календарь бронирования`, `менеджеру Байки`,
`мне и менеджеру Rental Agreement актуальное`, `обратная страничка`, `пустой бланк`,
`пустой бланк. копия`.

Два следствия:

1. **Папки договоров в этом списке нет** — она вне «Актуальной Системы Учета» (что и объясняет
   её закрытость). Снятие «anyone» с учёта договоров **не касается**.
2. **33 дубля `TurboBaby Bot Data`** — след ветки пересоздания
   (`BotData.js:121-161`: протух `BOT_DATA_SHEET_ID` → `SpreadsheetApp.create` → перенос в MAIN).
   К вопросу о ссылке отношения не имеет, но в остатки записать стоит: сегодня «боевая таблица
   Bot Data» — понятие, не адресуемое снаружи Script Properties.

---

## Что НЕ проверялось и почему

| не проверено | причина |
|---|---|
| Права reader/writer на папке и Bot Data (п.4) | нет ни одного read-only канала к правам Drive из этой сессии — §4 |
| Содержимое и доступность папок «Паспорта» и `Rental Agreement (File responses)` | прямой запрет задания |
| Настройки приёма ответов формы (сбор адресов, вход) | доступны только в UI формы у владельца |
| Живёт ли всё это в My Drive или на Общем диске | из кода не следует; снаружи не отличимо |
| Совпадает ли прод моста (@78) с HEAD, который я читал | к вопросу о ссылках не относится: ветки `getUrl()` в обеих одни и те же |

## Остатки

* **Приоритет выше исходного вопроса:** «Зарплаты» и CRM с ПДн клиентов читаются анонимно по
  ссылке. Это не «сломается при снятии» — это причина снимать.
* Боевой ключ EdenAI открытым текстом в `config.js` проекта формы (§3).
* 33 дубля `TurboBaby Bot Data` в главной папке (§5).
