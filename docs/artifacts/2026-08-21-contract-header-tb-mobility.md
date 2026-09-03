# Шапка шаблона договора → TB MOBILITY CO., LTD. (21.08.2026)

Шаблон: Google Doc «Шаблон», id `1Hh2GlpxJsa4DplbKYaM1X0Nt6DLCWYQb6znWcG61tOE`.
Резервная копия `1Nj0sJz0…` НЕ открывалась и НЕ менялась.
Временная папка захода: `_scratch_contracthdr_0821/`.

## 1. ШАГ-СТОП: на чём держится автозаполнение

Проверено по **живому** коду Apps Script (снят `clasp pull` 21.08 в
`_scratch_contracthdr_0821/bridge_live`, 17 файлов; `Contract.js` побайтно совпал с копией
`tmp/bridge_gs/Contract.js`).

**Автозаполнение держится ТОЛЬКО на метках вида `%имя%`.** Ни текста шапки, ни индексов
абзацев ни одна ветка не читает:

| место | что делает | зависимость |
|---|---|---|
| `Contract.js:12` | `var CONTRACT_TEMPLATE_ID = '1Hh2Glpx…'` | — |
| `Contract.js:413` (`makeContract_`, НОВЫЙ договор) | `DriveApp.getFileById(TEMPLATE).makeCopy(fname, folder)` → `b.replaceText('%'+key+'%', val)` ×26 | только метки |
| `Contract.js:223–241` (`fillContractDoc_`, ПЕРЕГЕН в тот же Doc) | `body.clear()` → цикл `for i=0..getNumChildren()-1` по ВСЕМ детям шаблона с разбором **по типу** (`PARAGRAPH`/`TABLE`/`LIST_ITEM`) → `body.replaceText('%'+key+'%', val)` | только метки |

Индексов нет: цикл проходит всё тело целиком, число детей берётся из самого шаблона
(`getNumChildren()`), а не задано константой. Текста шапки не читает никто — единственный
`getText()` во всём файле (строка 238) проверяет пустоту ведущего абзаца ПОСЛЕ `clear()`.

Дословные находки по пяти подстрокам — в §5.

## 2. Первые 10 элементов ДО правки

Тело: **30** элементов.

```
[00] PARAGRAPH  heading=TITLE  align=CENTER  indent=[57, 62.7, 57]  inline=TEXT
     "Motorbike Rental Agreement"            run 0..25 size=16 bold=True color=None font=Calibri
[01] PARAGRAPH  heading=TITLE  align=CENTER  indent=[56.7, 47.562992125984266, 56.7]  inline=TEXT
     "TURBOBABY"                             run 0..8  size=11 bold=True color=None font=Calibri
[02] LIST_ITEM  glyph=null nesting=0  heading=TITLE  align=CENTER  indent=[36, 47.562992125984266, 18]  inline=TEXT
     "13 4, Tambon Kamala, Amphoe Kathu, Chang Wat Phuket 83150, THAILAND"
                                             run 0..66 size=11 bold=True color=None font=Calibri
[03] PARAGRAPH  heading=NORMAL  align=CENTER  indent=[65.3, 80.15, 65.3]      ""   (пустой)
[04] TABLE      (10 строк)   ← таблица меток
[05] PARAGRAPH  align=LEFT  indent=[0, 66.1, 0]  inline=ANCHORED_IMAGE,ANCHORED_IMAGE,ANCHORED_DRAWING   ""
[06] TABLE      (1 строка)
[07] PARAGRAPH  ""   (пустой)
[08] PARAGRAPH  inline=ANCHORED_DRAWING   ""
[09] PARAGRAPH  ""   (пустой)
```

Метки `%имя%`: **всего 27, уникальных 26** (`%Pbt%` встречается дважды).

## 3. Правка

Снесены **три** элемента — `[00]`, `[01]`, `[02]` (третий был **элементом списка**
`LIST_ITEM` с `glyph=null`, не абзацем). Вставлены **восемь** новых, все:
`heading=NORMAL`, `align=CENTER`, `indent=[0, 0, 0]`, шрифт Calibri.

Замок правки: мост отказывал, если текст любого из трёх не совпадал ДОСЛОВНО со снимком ДО,
либо если внутри абзаца было что-то кроме `TEXT` (картинку снести нельзя ни при каких условиях).
Тексты в замок подавались не руками, а из `elements_before.json`.

## 4. Первые 10 элементов ПОСЛЕ правки (чтение назад)

Тело: **35** элементов (30 − 3 + 8).

```
[00] PARAGRAPH NORMAL CENTER [0,0,0] "TURBOBABY"                                20pt bold #000000 Calibri
[01] PARAGRAPH NORMAL CENTER [0,0,0] "PREMIUM MOTORBIKE RENTAL · PHUKET"         8pt      #666666 Calibri
[02] PARAGRAPH NORMAL CENTER [0,0,0] ""  inline=HORIZONTAL_RULE
[03] PARAGRAPH NORMAL CENTER [0,0,0] "TB MOBILITY CO., LTD. · บริษัท ทีบี โมบิลิตี้ จำกัด · Head Office"
       0..23  9pt #000000 Calibri  "TB MOBILITY CO., LTD. · "
       24..50 9pt #000000 Sarabun  "บริษัท ทีบี โมบิลิตี้ จำกัด"
       51..64 9pt #000000 Calibri  " · Head Office"
[04] PARAGRAPH NORMAL CENTER [0,0,0] "Reg./Tax ID 0835569013728 · 13/4 Moo 6, Kamala, Kathu, Phuket 83150, Thailand"
                                                                                 9pt      #666666 Calibri
[05] PARAGRAPH NORMAL CENTER [0,0,0] "turbophuket.com"                           9pt      #666666 Calibri
[06] PARAGRAPH NORMAL CENTER [0,0,0] ""  inline=HORIZONTAL_RULE
[07] PARAGRAPH NORMAL CENTER [0,0,0] "MOTORBIKE RENTAL AGREEMENT"               14pt bold #000000 Calibri
[08] PARAGRAPH NORMAL CENTER [65.3, 80.15, 65.3] ""   ← прежний пустой абзац, не тронут
[09] TABLE (10 строк)                                  ← таблица меток, не тронута
```

**Списочных элементов в шапке нет:** все восемь — тип `PARAGRAPH`; `list_items_fixed: []`
(чинить было нечего). Прежний `LIST_ITEM` ушёл вместе с третьей строкой старой шапки.

**Метки: 27 до / 27 после**, уникальных 26 до / 26 после, поимённо совпадают включая `%Pbt% x2`.

## 5. Дословные находки по пяти подстрокам

По репозиторию ПК `D:\turbobaby-bot` (файлы кода `*.py *.js *.gs *.json`):

- `1Hh2Glpx` — в git: **0**; в рабочем дереве: только копии моста в `tmp/` (5 шт.), строка
  `var CONTRACT_TEMPLATE_ID = '1Hh2Glpx…'`, и упоминания в текстах мозга/KB.
- `makeCopy`, `replaceText` — только `tmp/*/Contract.js` (копии моста) и комментарий
  `tmp/srv*/bridge_client.py:… # === Договор (этап B3): шаблон → replaceText → папка договоров`.
- `TURBOBABY` — только имя приставки переменных окружения (`*_TEST_LOGS`, `*_LOG_OWNER`) и
  бренд в тексте `suggest.py`. К шапке отношения не имеет.
- `Kamala` — район доставки: `Contract.js:23` (`DF_TARIFF`, читает **note CRM**, не документ),
  `booking_draft.py:309`, `anonymize_corpus.py:72`, тестовые ссылки.

По проектам Apps Script аккаунта info@turbophuket.com: `clasp list` → **один** проект,
«TurboBaby Bridge». Находки в его живом HEAD — см. §1 и раздел «Ложный ноль» ниже.

### Ложный ноль (записать в класс)

Первый прогон пяти подстрок по свежескачанному HEAD дал **0 совпадений по всем пяти**.
Причина — не отсутствие: рабочий каталог Bash уехал в `bridge_live` предыдущей командой
`cd … && clasp pull`, глоб `$D/*.js` не раскрылся. Повтор по АБСОЛЮТНОМУ пути дал находки.
Ровно тот класс, что описан в CLAUDE.md («grep → 0» без ответа «в каком дереве искал»).

## 6. Инструмент и уборка

Штатного канала структурной правки Doc у ПК нет: единственный путь к Google — мост
(Apps Script), а его 89 маршрутов такой операции не содержат (`write_doc` перезаписывает док
целиком — таблица и картинки погибли бы).

Сделано так: в HEAD скрипт-проекта временно добавлен модуль `TmpContractHeader.js` (чтение
элементов + правка с замком) и **7 строк** в диспетчер `Bridge.js` (два маршрута). Прод
закреплён на НОМЕРЕ версии (@79) — заливка в HEAD его не двигает (`clasp_prod_pins.json`).
`/exec` HEAD-деплоя отдаёт HTTP 401 (закрыт логином Google), поэтому под работу был поднят
ОТДЕЛЬНЫЙ временный версионный деплой и снят сразу после.

Уборка проверена фактом, а не расписками:

- `clasp pull` в чистый каталог → `md5sum` 17 файлов **совпал побайтно** со снимком ДО правки;
- `clasp deployments` → снова **2** деплоя: `@HEAD` и прод `@79`;
- ping по ШТАТНОМУ адресу моста: `ok=True, version=1.0.0, TurboBaby Bridge alive`;
- временный маршрут на штатном адресе: `ok=False, error=unknown_action` — прод его не знал и
  не знает.

В истории проекта остались версии 80 и 81 (снимки, версии Apps Script не удаляются); ни одна
из них никем не обслуживается.
