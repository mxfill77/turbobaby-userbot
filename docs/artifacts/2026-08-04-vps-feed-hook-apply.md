# Хук ленты (PostToolUse) применён на VPS — вариант A, 04.08.2026 17:01 UTC

Сделано с ПК по ssh (агент на сервере в `.claude/` писать не может — deny двух слоёв,
`orchestrator_daemon.py:164` «в .claude/ headless писать не может (гейт движка)»).
Разрешение владельца дано, красное подтверждено.

## 1. Дифф подготовленного файла против ЖИВЫХ настроек (дословно)

`diff -u .claude/settings.json _feed_new_settings.json` (224 → 236 строк):

```diff
@@ -32,6 +32,17 @@
           }
         ]
       }
+    ],
+    "PostToolUse": [
+      {
+        "matcher": "Bash",
+        "hooks": [
+          {
+            "type": "command",
+            "command": "/root/turbobaby-manager-bot/venv/bin/python3 /root/turbobaby-manager-bot/posttool_feed.py"
+          }
+        ]
+      }
     ]
   },
   "permissions": {
@@ -220,5 +231,6 @@
       "/root/turbobaby-bridge-gs",
       "/root/.claude/projects/-root-turbobaby-manager-bot/memory"
     ]
-  }
+  },
+  "_comment_feed": "PREPARED для cp _feed_new_settings.json .claude/settings.json … Отличие от живого файла РОВНО одно: hooks.PostToolUse matcher=Bash → posttool_feed.py … permissions НЕ тронуты. Тест: tests/test_feed_channel.py."
 }
```

Строчный дифф спрятать правку ключа может, поэтому дельта проверена ещё и **структурно**
(рекурсивное сравнение JSON, не подстрок):

```
STRUCTURAL DELTA entries: 2
    ADDED /hooks/PostToolUse
    ADDED /_comment_feed
REST_IDENTICAL_after_removing_two: True     ← убрать эти два ключа → файлы тождественны
PreToolUse_identical: True                  permissions_identical: True
allow 146→146   deny 22→22
permissionDecision_in_PostToolUse: False    ← прав выдать физически нечем
```

Дельта = ровно санкционированные две вещи. Ни гарда, ни существующих блоков она не касается.

## 2. Откат

`/root/turbobaby-manager-bot/.claude/settings.json.bak-2026-08-04`
sha256 `3b1838fe75cce691d4e3335f1daac8f5ad312f1aec8805de4091794500ded753` (= живой файл ДО правки).
Старые копии не тронуты; вторая линия отката — git (`_feed_new_settings.json` и старый
`settings.json` оба в индексе).

## 3. Применение

Стейджинг в свой каталог `_scratch_feedhook_0804pc/` → валидация JSON → `mv` (атомарный
rename в том же ФС, а не `cp` поверх живого файла: сессия, стартующая в этот момент, не
должна прочитать половину файла). Автооткат при любом расхождении был вшит в тот же вызов —
не понадобился.

| файл | до | после |
|---|---|---|
| `.claude/settings.json` | `3b1838fe…` | `ebbc6f5b…` (= sha подготовленного файла) |
| `pretool_guard.py` | `27d943b1…` | `27d943b1…` — **дельта 0** |

## 4. Как хук перечитан БЕЗ рестарта splinter

Перечитывать нечего: хуки читаются на СТАРТЕ процесса `claude`, а долгоживущей сессии,
которая держала бы настройки в памяти, на полосе нет — демон порождает **новый `claude -p`
на каждую задачу** (`orchestrator_daemon.py`, «исполнить через claude -p», cwd=репо).
Значит первая же сессия после 17:01 UTC читает новый файл. splinter и демон не трогались:
`ActiveEnterTimestamp` splinter = 06:40:41 UTC (за 10 часов до правки), демона = 16:48:27 UTC
(его собственный рестарт до начала работы), оба `active`.

## 5. Доказательство живым фактом

**Блок в живых настройках** (чтение назад с сервера):

```
READBACK hooks keys: ['Notification', 'PostToolUse', 'PreToolUse', 'UserPromptSubmit']
READBACK PostToolUse: [{"hooks": [{"command": "…/venv/bin/python3 …/posttool_feed.py",
                                   "type": "command"}], "matcher": "Bash"}]
READBACK _comment_feed present: True
permissions / PreToolUse / UserPromptSubmit / Notification unchanged vs backup: True
```

**Хук отвечает — пойман В МОМЕНТ РАБОТЫ.** Следа хук не оставляет намеренно (таблица классов
шага 1 пуста, лога у него нет, транскрипты движка исполнение хуков не пишут — проверено), поэтому
доказательство снято сэмплером процессов (20 мс) поверх свежей сессии, которая сделала вызовы Bash:

```
483766 /bin/sh -c /root/turbobaby-manager-bot/venv/bin/python3 …/posttool_feed.py
483767 /root/turbobaby-manager-bot/venv/bin/python3 …/posttool_feed.py
484048 /bin/sh -c …/posttool_feed.py
484049 …/venv/bin/python3 …/posttool_feed.py
```

Движок САМ породил хук — ровно тем argv, что записан в настройках. Сессия при этом ответила `ok`
и завершилась `exit=0`: stdout хук не занял, решение не подменил.

**Заметок не ушло ни одной**: каталогов состояния `/tmp/cc_feed_seen` и тестового нет вовсе —
`send()` не вызывался. Так и задумано: список событий не подключён (шаг 2), проба шла изолированно
(`PRETOOL_NOPUSH=1`), карточка владельцу не выписывалась.

## 6. Остатки (не мои правки, но замерены)

- **Свежая сессия `claude` вне демона на VPS не стартует**: `~/.claude/.credentials.json` от
  24.07 протух («OAuth session expired»). Демон живёт не им, а `EnvironmentFile=/root/.config/claude-executor.env`
  (systemd-юнит) — поэтому его задачи идут нормально. Проба запускалась `systemd-run` с тем же
  EnvironmentFile: секретный файл при этом никем не читался и не печатался.
- **Дерево VPS грязное**: `.claude/settings.json` числится модифицированным (правка применена, но
  не закоммичена — коммит владельцем не санкционирован). Риск класса «отброс рабочего дерева»:
  чужой `git checkout .` вернёт настройки без хука.
- В `settings.json` живут 9 предупреждений движка вида «`Write(path)` не матчится, нужен
  `Edit(path)`» — они были и ДО правки (в блоке permissions, который не тронут), к дельте
  отношения не имеют.
