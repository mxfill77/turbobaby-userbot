# TASK_PACKET — C0 Pricing Advisor repair

**ID:** `c0-pricing-advisor-repair-2026-08-31`

**Режим:** `code_green_fixture_only`

**Роль исполнителя:** исправить только подтверждённые дефекты; не заявлять независимую верификацию. Manus проверит результат отдельно.

## Разрешённый scope

Изменять разрешено только следующие файлы:

- `pricing_advisor.py`
- `test_pricing_advisor.py`
- `docs/artifacts/2026-08-31-c0-pricing-advisor-result.json`
- `todo.md`

Не изменять runtime, Bridge, CRM, Telegram, Google Sheets, client text, price source, moderation, очередь или production-конфигурацию. Не делать commit и push.

## Подтверждённые дефекты

Независимая проба Manus выявила:

1. `list_day_price = NaN/Inf` проходит как `status=ok`, становится `recommendation.target` и ломает строгую JSON-сериализацию.
2. `median_day_price_thb = NaN` ошибочно считается corroborating history и поднимает confidence до `high`.
3. `owner_policy.snapshot_max_age_days = NaN` отключает проверку stale snapshot.
4. Окно аренды, уже начавшееся до `as_of`, проходит как `ok`.
5. `confidence.level = "none"` не входит в утверждённую схему `high|medium|low|blocked`.
6. `history_stats.fresh_as_of` отвергается как неизвестный ключ; история должна иметь проверяемую свежесть и не должна повышать confidence, если stale/invalid.

## Обязательное исправление

Добавить fail-closed проверку конечности всех числовых входов, которые могут попасть в packet или участвовать в политике: деньги, проценты, возраст snapshot, медианы и иные числовые пороги. Ни при каких входах C0 не должен выдавать `NaN` или `Inf`; `json.dumps(packet, allow_nan=False)` обязан проходить для валидного и fail-closed результата.

Для rental window, где `date_start < as_of`, не выдавать target. Классифицировать по существующей границе `unknown/blocked` и добавить regression test. Не менять уже согласованную семантику действительно полностью прошедшего окна.

Заменить confidence levels на значения утверждённой схемы: `high`, `medium`, `low`, `blocked`. Для `status=blocked` использовать `blocked`; для `status=unknown` использовать `low`, если отдельная документация не задаёт более строгую approved-семантику. Добавить тест на полный whitelist.

Минимально расширить `history_stats` для `fresh_as_of`: принять ключ и отразить его в нормализованном history-блоке; проверить формат и не допускать stale/future/invalid history к corroboration или повышению confidence. История остаётся только `evidence_only` и никогда не меняет target. Не добавлять собственные часы: использовать только инъектированный `as_of`.

Добавить focused regression tests для всех шести дефектов, включая strict JSON serialization и отсутствие `none` в confidence. Сохранить существующие тесты. Пять известных NMAX smoke failures не чинить и не маскировать.

Сохранить публичную точку входа:

```python
recommend_price(quote_facts, dialog_facts, history_stats, owner_policy, *, as_of)
```

Сохранить инварианты: `mode="shadow"`, `approval_required=True`, target только `None` либо побитово переданная `list_day_price`, отсутствие network/fs/env/clock/write/send/runtime side effects, детерминированный результат.

## Проверки исполнителя

Запустить:

```powershell
venv\Scripts\python.exe -W error::ResourceWarning -m unittest -v test_pricing_advisor
venv\Scripts\python.exe -m unittest -q test_pricing_advisor test_price_source test_price_gate test_price_freshness test_pricing test_price_snapshot_collect test_price_snapshot_publish test_suggest test_moderation test_moderation_card
```

Ожидание для полного гейта: прежние `1037` тестов и ровно те же пять известных `test_suggest.TestRunLiveSmoke` failures, без новых падений. Проверить `git status --short` и подтвердить, что изменены только разрешённые файлы.

## RESULT_PACKET

Вернуть компактный JSON-artifact со статусом `reported_done`, списком changed files, SHA-256/blob hash двух исходных файлов, командами и результатами тестов, сравнением failures с baseline, remaining unknowns и явным указанием: `not independently verified`, `not committed`, `not pushed`.
