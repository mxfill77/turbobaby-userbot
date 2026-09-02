# Порт entity/soft-block гарда — TODO (2026-07-24)

## Правило и почему важно

Доктрина контура: **боевую запись в живые таблицы (Лист1/CRM) можно одобрять («да») ТОЛЬКО для
ТЕСТ-сущностей** (клиент/байк с именем «ТЕСТ…») в write-смоках; реальная сущность → только
владелец. Плюс различие **hard-block** (approve НЕВОЗМОЖЕН) vs **soft-block** (нужно «да»).

Функции для этого НАПИСАНЫ (в `stash@{1}` = vps-local-24.07), но **НИ РАЗУ НЕ ВЫЗЫВАЮТСЯ** —
защита фактически НЕ работает. Голден `tests/test_guard_тест_entity.py` их ждёт (21 assert) →
падал 19 errors и блокировал ВЕСЬ гейт (несвязанные деплои тоже). С 2026-07-24 тест помечен
**skip** (`setUpModule` → `SkipTest`). Зелёным он НЕ сделан — он ждёт этого порта.

## Что портировать (из `stash@{1}`)

### pretool_guard.py (~20 строк)
- Константы (`stash@{1}:298-302`):
  - `_ENTITY_CLIENT_RE`, `_ENTITY_PLATE_RE` — регэкспы `client=…` / `plate=…` из blob;
  - `_ENTITY_HITS_CLIENT = frozenset(("confirmed","create_booking","activate_booking","closing_upsert"))`;
  - `_ENTITY_HITS_PLATE  = frozenset(("set_fleet_oil","set_fleet_service"))`.
- `_extract_first_entity(hit, blob)` (`stash@{1}:305-314`) — извлечь клиента/байк по hit.
- `_is_test_entity(entity)` (`stash@{1}:317-319`) — True если сущность начинается с «тест».
- `_guard_write_marker(..., blocktype=None)` (`stash@{1}:146-162`) — +параметр `blocktype`
  (маркер несёт `blocktype="hard"` | `None`).

### orchestrator_daemon.py (~3-8 строк)
- `_guard_is_hard(data)` (`stash@{1}:246-248`) — True если маркер несёт `blocktype="hard"`.
- Вызов-обвязка в `run_task`: читать маркер → `_guard_is_hard` → hard=`deny` / soft=`needs_approval`
  (частичная обвязка вызова — в `stash@{2}:1032`).

## Чего НЕ хватает (ключевое!)

Даже в `stash@{1}` функции **определены, но не вызваны**: `_extract_first_entity`/`_is_test_entity`
НЕ подключены к classify/card-пути, `_guard_is_hard` не вызывается в `run_task`. **Порт = не
только скопировать функции, но и ВРЕЗАТЬ вызовы**:
- в classify/main `pretool_guard`: извлечь сущность из blob; `_is_test_entity` → мягче (approve
  в смоках), иначе hard для боевой сущности;
- в `run_task` оркестратора: `_guard_is_hard(marker)` решает `deny` vs `needs_approval`.

## Объём и источник

- **~25-30 строк, 5 блоков, 2 файла** (`pretool_guard.py` + `orchestrator_daemon.py`).
- Источник: `git stash show -p 'stash@{1}'`; частичная обвязка `_guard_is_hard` — в `stash@{2}`.
- Ортогонально `_strip_script_cli_args` (регионы 146-319 vs 474-713 не пересекаются) — портируется
  поверх текущей линии без конфликта.

## Снятие skip

Убрать `setUpModule` из `tests/test_guard_тест_entity.py` = выполнить порт выше и убедиться, что
21 assert зелёный ПО-НАСТОЯЩЕМУ (сейчас 19 из них падают на отсутствующих символах).
