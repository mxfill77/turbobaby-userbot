# BRIDGEDEPLOY86 — выкладка прод-моста @86: дверь договоров отдаёт PDF из «Подписанные/ГГГГ/ММ» и сама находит адреса (04.10.2026)

## ИТОГ

- **Прод-мост выложен: @85 → @86.** Развёртывание «ПРОД» (то же, id `AKfycb…`) переключено на **@86** в
  **2026-10-04 12:04:23 UTC** (`updateTime` API 12:04:23.504Z). Доступ и исполнение прежние
  (`ANYONE_ANONYMOUS` / `USER_DEPLOYING`), развёртываний 3, нового не создано. Описание версии и развёртывания:
  «04.10.2026: дверь договоров — PDF из Подписанные/ГГГГ/ММ, адреса поиском, сборка a280461».
- **sha256 `ContractDoor.js` прода @86:** `5ab3d4898d134322727d7c48c19f35015c377a48c6ef950e41c1d0563cb58bd5`
  (выкачка задеплоенной версии; = сборка `bridge_build_esign/`). @86 = каталог выкладки побайтно, 19/19.
- **Проверки — все прошли, отката не было:** `ping` ok · `read_doc pulse_pc` ok · `tx_find` `complete` ·
  **`contract_find bike=6908` → `one`, 1 строка, `complete`, `unread` пуст, адрес реестра найден поиском** ·
  **`contract_pdf` → ok, 899 930 байт, sha256 `a5b4ee986904…`, `folder_level` 3, оба адреса найдены поиском**.
  То есть PDF из «Подписанные/2026/ММ» агент теперь получает, а свойства `ESIGN_*` задавать не обязательно.
  Демон после переключения: «ящик Штаба: документов 500» в 12:24:48 UTC.
- **Зеркало:** ветка `bridge-doors-0310` (не main) = **`3c9466fecb0acb98732d5595903560ada76ad738`** на origin;
  `bridge_prod/` = выложенные 19 файлов, `MIRROR.json` @86; `bridge_build_esign/BUILD.json` —
  `delivered_as_version: 86`. Тесты ветки: **8/8 · 10/10 · 8/8 · 8/8, красных 0**.
- **Откат на будущее:** «ПРОД» на @85 — `tmp/bridge_deploy_0410b/ROLLBACK.txt`.

```
FACT: шаг 0 — ls-remote bridge-doors-0310 = a28046152539f435aa95df8f02faa05f5a519acb; HEAD клона тот же, дерево чистое;
      bridge_build_esign/ContractDoor.js = 5ab3d489…bd5; прочие 18 побайтно = bridge_prod/ и паспорту @85;
      отпечаток 12:01:22 UTC: прод @85 = зеркало = HEAD, 19/19, код 0.
FACT: шаг 1 — push/ 19 файлов (ContractDoor.js правлен, 18 как в проде @85), node --check ок, clasp status 19;
      .clasp.json: scriptId 12iXPD… из tmp/bridge_deploy_0410/snap/meta.json, rootDir push;
      отпечаток прямо перед push 12:02:06 UTC: прод @85 = зеркало = HEAD, код 0.
FACT: шаг 2 — clasp push 12:02:21–12:02:36 UTC «Pushed 19 files», HEAD = push/ 19/19;
      clasp version → «Created version 86» (createTime 12:03:51.203Z), @86 = push/ 19/19;
      clasp deploy -i <ПРОД> -V 86 -d «…» 12:04:16–12:04:23 UTC «@86», код 0; API: ПРОД 86, 12:04:23.504Z.
FACT: шаг 3 — 12:05:19 ping ok 1.0.0; 12:05:22 read_doc pulse_pc ok, длина 4528; 12:05:24 tx_find 5960 limit 5 ok,
      complete true (просмотрено 9, совпало 0); 12:05:33 contract_find bike=6908 limit 5 ok, outcome one, строк 1,
      complete true, unread [], config.registry search; 12:05:41 contract_pdf (pdf_id 126msA…) ok, size 899930,
      sha256 a5b4ee986904…, совпал с байтами ответа, folder_level 3, config registry search / signed_folder search.
FACT: шаг 4 — зеркало: положен ContractDoor.js, совпадало 18, MIRROR.json @86 (19 файлов, head_equals_prod true,
      снято 12:06:44 UTC), обратное чтение ок; BUILD.json delivered_as_version 86; коммит 3c9466fe… → origin
      (a280461..3c9466f); main = 9c4aac6f… не тронут.
```

## Шаги 0–2 — сверка и выкладка

| шаг | что | итог |
|---|---|---|
| 0 | ветка на origin | `a28046152539f435aa95df8f02faa05f5a519acb` = задание |
| 0 | `ContractDoor.js` сборки | `5ab3d4898d134322727d7c48c19f35015c377a48c6ef950e41c1d0563cb58bd5` = задание |
| 0 | прочие 18 файлов сборки | побайтно = `bridge_prod/` и паспорту зеркала @85 |
| 0 | отпечаток прода (12:01:22 UTC) | @85 = зеркало = HEAD, 19/19, код 0 (`s0_prod_diff.txt`) |
| 1 | `push/` | 19 файлов без `BUILD.json`; правлен только `ContractDoor.js`; `node --check` ок; `clasp status` 19 |
| 1 | отпечаток прямо перед push (12:02:06 UTC) | @85 = зеркало = HEAD, код 0 (`s1_prod_diff_pre.txt`) |
| 2 | `clasp push` | 19 файлов, код 0; HEAD = `push/` 19/19 |
| 2 | `clasp version` | **@86** |
| 2 | `clasp deploy -i <ПРОД> -V 86 -d …` | «@86», код 0; ПРОД = @86 в **12:04:23 UTC**; @86 = `push/` 19/19 |

## Шаг 3 — проверки (по одному вызову, только чтение)

| проба | итог | UTC |
|---|---|---|
| `ping` | ok, 1.0.0, «TurboBaby Bridge alive» | 12:05:19 |
| `read_doc pulse_pc` | ok, длина **4528** | 12:05:22 |
| `tx_find 5960 limit 5` | ok, `checked.complete: true` (просмотрено 9, совпало 0) | 12:05:24 |
| `contract_find bike=6908 limit 5` | ok · `outcome: one` · строк **1** · `checked.complete: true` · `unread: []` · `config.registry: search` · `pick.pdf_ready: true` | 12:05:33 |
| `contract_pdf` по `pick.pdf_id` (`126msA…`) | ok · `size` **899 930** · sha256 `a5b4ee986904…` (совпал с байтами ответа) · **`folder_level: 3`** · `config: {registry: search, signed_folder: search}` | 12:05:41 |

`folder_level: 3` — PDF лежит в «Подписанные/2026/ММ», то есть ровно тот случай, который дверь @85 отвергала.
Свойства проекта `ESIGN_*` по-прежнему не заданы: оба адреса дверь нашла поиском по имени, отказов
`registry_ambiguous` / `no_*_config` / `not_in_signed_folder` нет. Имена, телефоны и содержимое договора не
печатались и не сохранялись; base64 декодирован только в памяти для сверки sha256.

**Демон.** Строка после переключения: **`2026-10-04 19:24:48 ящик Штаба: документов 500, взято 0, отложено 500`**
(местное = 12:24:48 UTC; так же, как последняя строка ДО переключения в 18:55:51 местного). Раньше неё мост демон
уже звал: `19:04:34 CLAIM id=47` и `19:04:36 CLAIM id=46` (через 11–13 с после переключения), `19:15:16 COMPLETE
id=46 status=failed` — записи очереди через мост прошли. Провал #46 — свой (гард остановил `git add` в той задаче),
к мосту не относится. Строк `unknown_action` и ERROR от моста после переключения нет.

## Шаг 4 — зеркало и тесты

- Свежий отпечаток @86 (`snap_after/`) → `mirror_sync.plan` / `passport` / `verify` из клона как есть → положен
  `ContractDoor.js`, совпадало 18; `MIRROR.json`: `prod_version` 86, 19 файлов, `head_equals_prod` true,
  `pulled_utc` 2026-10-04 12:06:44 UTC; обратное чтение ok. Снимок @86 побайтно = `push/` (проверено до записи).
- `bridge_build_esign/BUILD.json`: `delivered_as_version: 86` и «выложено» (по образцу `bridge_build_doors`);
  формат файла воспроизведён байт в байт до правки, `build_sha256` не тронуты.
- Тесты ветки после сведения: `test_bridge_doors.py` **8/8** (мутантов 5), `test_contract_door.py` **10/10**
  (34), `test_tx_find.py` **8/8** (26), `test_esign_door.py` **8/8** (7). **Красных 0.**
- Коммит **`3c9466fecb0acb98732d5595903560ada76ad738`**, push `a280461..3c9466f bridge-doors-0310`, код 0;
  ls-remote: `bridge-doors-0310` = `3c9466fe…`, `main` = `9c4aac6f…`.

## Что не сделано

1. **Зеркало в main сервера осталось @84** (ветка `bridge-doors-0310` в main не влита — main по заданию не
   трогается). Серверный `deploy/bridge_prod_diff.py` из main скажет «прод впереди» (@86 против @84).
2. **Свойства `ESIGN_REGISTRY_ID` / `ESIGN_SIGNED_FOLDER_ID` не заданы** — после этой выкладки и не нужны: дверь
   находит адреса поиском. Цена — поиск в Drive на каждом вызове (`getFilesByName` / `getFoldersByName`).
3. Временное развёртывание @82 (20.08) живо — не трогалось.

## Отступления — названы

- Один вызов в шаге 2 владелец сначала отклонил (составная команда с относительным путём `../`), затем написал
  «Ошибся надо разрешить». Команда разделена на две, пути полные; ничего не исполнилось дважды.
- Сверочные скрипты — ПК-копии штатных `deploy/bridge_prod_diff.py` / `bridge_prod_recon.py` из прошлой выкладки
  (`tmp/bridge_deploy_0410/tools/`, разница с оригиналом — `pc_copies.diff`: только пути, учётки clasp 2.x и
  запись снимка без CRLF).
- На ПК clasp 2.4.2: прод переключается `clasp deploy -i <id> -V <N> -d <описание>` (это `deployments.update` того
  же развёртывания; `redeploy` есть только в clasp 3).

## Временное — `tmp/bridge_deploy_0410b/` (ничего не удалялось)

`.clasp.json` (заряжен на проект — оставлен), `push/` (19), `snap/`, `snap_pre/`, `snap_after/` (выкачки прода),
`tools/` (`step0_build.py`, `step1_push_dir.py`, `api_state.py`, `probe86.py`, `mirror_sync_pc86.py`), `s0_*`,
`s1_*`, `s2_*`, `s3_*`, `s4_*`, `push_manifest.json`, `ROLLBACK.txt`, `mirror_commit_msg.txt`.

BRIDGEDEPLOY86
