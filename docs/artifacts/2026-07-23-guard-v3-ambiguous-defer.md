# Guard в3 (VPS-репо): ambiguous → defer — ГОТОВО К «ДА»

**Дата:** 2026-07-23 · **Задача:** три правки pretool_guard в клоне `D:\foreign\turbobaby-manager-bot` + тесты + push origin/main.

## Почему нужно «да» владельца

Headless-сессия упёрлась в красную карточку гарда ПК: **запись за пределами проекта**
(`D:\foreign\…` не входит в allow Edit/Write: репо/память/скретчпад). По доктрине задачи обход не
искал: правки подготовлены и ПРОВЕРЕНЫ в песочнице своего репо, применение — за владельцем.

## Состояние трёх правок

1. **restart|start своих → зелёное; stop|kill → deny** — УЖЕ в клоне (чужой коммит `f9e0227`, в2).
2. **ambiguous сам по себе НЕ красный** — НЕ было в коде; сделано здесь (в3, этот артефакт).
3. **Termux выпилен из сообщений/докстрингов** — УЖЕ в клоне (`f9e0227`; легаси-формат cc_log и
   regex registry_check заморожены сознательно, это миграция формата — не трогал).

## Суть в3 (файлы готовы целиком)

- `decision()`: ambiguous → `None` (defer), как green; ask остаётся ТОЛЬКО у red, deny у block.
- `main()`: green/ambiguous → defer ДО карточек; конверт «не распознал операцию» и дедуп ×N
  упразднены (хелперы `_dedup_*`/`_edit` оставлены — их контракт держит `test_guard_inbox` (5)).
- Красное только по доктрине, и оно **усилено** двумя закрытыми дырами, которые открылись бы
  с ambiguous-defer:
  - sqlite-write (`UPDATE`/`DELETE FROM`/`INSERT INTO`/`DROP TABLE` + `.db` ≠ memory.db) теперь
    ловится и в heredoc/stdin — проверка поднята ДО ambiguous-выходов (шаг 1б `_analyze`);
  - ambiguous-звено компаунда больше не заслоняет red-скан читаемой части:
    `python3 red.py && python3 нет_такого.py` → **red**, а не defer (ambiguous копится флагом,
    red/sqlite проверяются после чтения всего читаемого).
- Сбой классификации целиком → defer (доктринальное красное держит ask/deny-слой settings);
  fail-safe разбора прежний (сбой разбора цепи → скан по сырой команде, краснее).

## Тесты (классификацией, красные литералы конкатенацией)

`tests/test_pretool_hardblock.py` §13/13б — **прогнано на ПК: 79/79 PASS** (лог
`tmp/guard_v3_sandbox/hardblock_run.txt`), включая все кейсы ТЗ:

| Кейс ТЗ | Итог |
|---|---|
| `systemctl restart splinter` | зелёное (defer) |
| `systemctl start orchestrator-daemon` | зелёное (defer) |
| `systemctl stop splinter` | deny без карточки |
| `pkill -9 splinter` | deny без карточки |
| `kill -SIGKILL 1` | deny (PID 1) |
| `python3 - <<PYEOF` с чтением очереди | зелёное (defer) |
| `cat .env` | deny (env_hard_block) |

Плюс 13б: heredoc с денежной операцией → red; heredoc `UPDATE` чужой `.db` → red (sqlite);
тот же `UPDATE` в memory.db → defer (доктрина 02.07 цела); heredoc с секретами/гашением боевого →
block; red.py && нечитаемый.py → red. VPS-only тесты (`infoflags`, `probe_dedup`, `commit_msg`)
перелочены на в3 статически (py_compile OK) — их прогонит гейт на VPS.

## Применение (после «да», на ПК)

```powershell
cd D:\turbobaby-bot
Copy-Item docs\artifacts\2026-07-23-guard-v3-files\pretool_guard.py D:\foreign\turbobaby-manager-bot\pretool_guard.py
Copy-Item docs\artifacts\2026-07-23-guard-v3-files\tests\*.py D:\foreign\turbobaby-manager-bot\tests\
D:\turbobaby-bot\venv\Scripts\python.exe D:\foreign\turbobaby-manager-bot\tests\test_pretool_hardblock.py   # ждём 79/79 PASS
cd D:\foreign\turbobaby-manager-bot
git add pretool_guard.py tests/test_pretool_hardblock.py tests/test_pretool_infoflags.py tests/test_pretool_probe_dedup.py tests/test_pretool_commit_msg.py
git commit -F D:\turbobaby-bot\docs\artifacts\2026-07-23-guard-v3-files\commit_msg.txt
git push origin main
python D:\turbobaby-bot\cowork_log_append.py "DONE Dispatch: guard в3 применён и запушен, хеш <из git log>, голден 79/79 PASS"
```

Альтернатива — тот же перечень файлов накатить патчем: `git apply docs/artifacts/2026-07-23-guard-v3-files/guard-v3.patch` из корня клона.

## Файлы артефакта

- `2026-07-23-guard-v3-files/pretool_guard.py` — готовый гард в3;
- `2026-07-23-guard-v3-files/tests/…` — 4 готовых теста;
- `2026-07-23-guard-v3-files/guard-v3.patch` — тот же дифф одним патчем (51+/34− гард, 96 строк тестов);
- `2026-07-23-guard-v3-files/commit_msg.txt` — готовое сообщение коммита.

**Push в origin/main НЕ делался** — клон не тронут, на VPS ничего не выполнялось.
