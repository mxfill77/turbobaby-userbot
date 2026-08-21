# Полный гейт наборами после трёх правок цены (21.08.2026)

**Дерево:** `9dc1f5d` (2026-08-21 05:35). **Прогон начат:** 2026-08-21 07:41 (локальное).
**Задание:** только прогон и доклад. Кода НЕ правим, голденов НЕ трогаем, сторож и ручку
источника цены НЕ трогаем. Красное — это результат, а не повод чинить.

**База сравнения:** [`2026-08-20-gate-after-switch.md`](2026-08-20-gate-after-switch.md) —
дерево `3b85c06`, 9 наборов из 9, **3981 тест, 66 красных**, из них **60 пути цены**.

## 0. Что легло между базой и этим прогоном (замер `git log 3b85c06..HEAD`)

Коммитов девять, из них **правок кода — две**, остальные семь — артефакты и тела журнальных
записей. Поправка к основанию задания: **`45a38cf` (переключение источника) в базу УЖЕ ВХОДИЛ**
(`git merge-base --is-ancestor 45a38cf 3b85c06` → истина), то есть прошлые 66 красных уже
измерены НА переключённом источнике. Новых правок, отделяющих этот прогон от базы, — две:

| коммит | время | что | заявленный эффект |
|---|---|---|---|
| `3309f3c` | 04:10 | бюджет сторожа свежести 20с → 90с | 60 красных → 50 |
| `8fd55d1` | 04:47 | голдены цены пересчитаны по ЗАПИСАННОМУ ПРАВИЛУ: 34 обновлено, 16 отложено | 50 → 16 |
| ×7 | — | `ce464ee`, `8758695`, `c8b0d09`, `95d2370`, `e081ba4`, `c6e7887`, `9dc1f5d` | артефакты/журнал, кода не касались |

Ожидание перед прогоном (арифметика заявленных эффектов, НЕ замер): путь цены ≈ **16**,
не-ценовых прежних ≈ **6** (2 dep-closure + 2 храповик + 1 `client_contour` + 1 маркер-изоляция).

## 1. Наборы (состав тот же, что в базе — сверять построчно)

| № | набор | модулей |
|---|---|---|
| 1 | **ПУТЬ ЦЕНЫ** — `price_source`, `price_gate`, `price_freshness`, `pricing` | 4 |
| 2 | **SUGGEST** — `suggest` | 1 |
| 3 | **КЛИЕНТСКИЙ КОНТУР** — `client_contour`, `delivery`, `golden_llm`, `playbook_rules`, `intake_bridge`, `booking_draft`, `dialog_card`, `moderation` | 8 |
| 4 | **ГАРД** — `pretool_guard`, `guard_action_not_text`, `isolation_guard`, `isolation`, `probe_isolation`, `env_value_leak`, `utf8_output_guard`, `card_duty`, `card_terminal_log`, `moderation_card` | 10 |
| 5 | **ОРКЕСТРАТОР** — `pc_orchestrator` | 1 |
| 6 | **ОЧЕРЕДЬ И ИСПОЛНЕНИЕ** — `pc_local_dec`, `selfupdate_gate`, `gate_selective`, `exec_position`, `result_judge_pc`, `result_ref`, `result_spill`, `series_pc`, `queue_snapshot_pc`, `status_truth`, `nonparse_ratchet`, `reviewer` | 12 |
| 7 | **МОЗГ · ЖУРНАЛ · МОСТ** — `brain_writer`, `brain_probe_pc`, `bridge_http`, `cowork_log_append`, `dispatch_notify`, `log_setup` | 6 |
| 8 | **УРОКИ И ТРЕНАЖЁР** — `lesson_router`, `lesson_store`, `lesson_step5`, `lesson_step6`, `lesson_urok`, `lesson_cycle`, `trainer`, `trainer_log`, `trainer_run` | 9 |
| 9 | **СРЕДА ПК · RC** — `expectations_pc`, `rc_supervisor`, `rc_auth_detect`, `pc_agent`, `pc_link_state`, `pc_sleep_alarm`, `pc_awake_clock`, `session_watch`, `userbot_reconnect` | 9 |

Форма запуска одна на все: `TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest <модули>`.
Порядок: **1 первым** (там правки), дальше по номерам. Весь гейт одной командой НЕ запускается —
два прежних захода умерли именно на этом.

## 2. Результаты по наборам (таблица дописывается ПОСЛЕ КАЖДОГО)

Сводная таблица — в **§4** (собирается в конце). Ниже — по одному разделу на набор, каждый
дописан на диск СРАЗУ после своего прогона, до запуска следующего.

---

### Набор 1 — ПУТЬ ЦЕНЫ: 176 тестов, **4 красных** (было 20), 42.7 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_price_source test_price_gate test_price_freshness test_pricing
Ran 176 tests in 42.713s
FAILED (failures=4)
```

Все 4 — в `test_pricing.TestPriceRulesV2`. `test_price_source`, `test_price_gate`,
`test_price_freshness` — зелёные целиком, как и в базе.

```
FAIL: test_cap_active_replaces_j_price_with_low_season (test_pricing.TestPriceRulesV2)
FAIL: test_cap_inactive_uses_j_text                    (test_pricing.TestPriceRulesV2)
FAIL: test_multi_model_separate_prices                 (test_pricing.TestPriceRulesV2)
FAIL: test_ok_uses_quote_text_verbatim                 (test_pricing.TestPriceRulesV2)
```

**Новых красных нет: все 4 входили в прежние 20.** 20 → 4, снято 16.

**Природа отказа СМЕНИЛАСЬ, и это главное наблюдение набора.** В базе красное звучало
«цена недоступна» (сторож гасил котировку). Сейчас котировка **СОБРАНА** — падает сверка
ожидаемого ТЕКСТА с посчитанным:

```
AssertionError: '6300 ฿ за 7 дней' not found in '… <<<QUOTE>>>
NMAX — 298 ฿/день; итого 2086 ฿; свободен на эти даты. <<<END_QUOTE>>>'

AssertionError: 'аренда от 15000 ฿/мес' not found in '… <<<QUOTE>>>
NMAX — 158 ฿/день; итого 4740 ฿; депозит 7000 ฿; свободен на эти даты. <<<END_QUOTE>>>'

AssertionError: 'Ровно так: 900 ฿/день, 6300 ฿ за неделю' not found in '… <<<QUOTE>>>
NMAX — 298 ฿/день; итого 2086 ฿; свободен на эти даты. <<<END_QUOTE>>>'

AssertionError: '6300 ฿ NMAX' not found in 'ЦЕНЫ ПО МОДЕЛЯМ … - NMAX: 298 ฿/день; итого
2086 ฿; свободен на эти даты - PCX: точная цена из Календаря сейчас недоступна …'
```

То есть сторож больше не молчит (правка `3309f3c` подействовала), а остаток — ожидания
голденов эпохи живого листа (класс «16 отложенных», `2026-08-21-goldens-deferred.md`).
Ничего не чиним — фиксируем.

### Набор 2 — SUGGEST: 569 тестов, **10 красных** (было 35), 160.8 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_suggest
Ran 569 tests in 160.842s
FAILED (failures=10)
```

```
FAIL: test_golden_draft_carries_column_j_verbatim        (test_suggest.TestPointQuoteCodeBlock)
FAIL: test_golden_quote_block_carries_column_j_verbatim  (test_suggest.TestPointQuoteCodeBlock)
FAIL: test_pointwise_pair_xmax_explicit_old_gen_both     (test_suggest.TestPriceSheetMinAcrossVariants)
FAIL: test_pointwise_xmax_explicit_old_gen_shows_both    (test_suggest.TestPriceSheetMinAcrossVariants)
FAIL: test_green_full_data_passes                        (test_suggest.TestRunLiveSmoke)
FAIL: test_green_paraphrase_with_correct_numbers_now_passes (test_suggest.TestRunLiveSmoke)
FAIL: test_red_delivery_missing_fails                    (test_suggest.TestRunLiveSmoke)
FAIL: test_red_j_line_not_verbatim_fails                 (test_suggest.TestRunLiveSmoke)
FAIL: test_red_violations_flagged_with_diff_card         (test_suggest.TestRunLiveSmoke)
FAIL: test_send_delivers_probe_two_messages              (test_suggest.TestRunLiveSmoke)
```

**Новых красных нет: все 10 сверены по именам с базовыми 35 и входят в них.** 35 → 10, снято 25.
Отдельно: **ошибок (`ERROR`) стало ноль** — в базе их было 2
(`test_a_asked_model_is_first_then_same_class_and_above`,
`test_deposit_sum_default_no_passport`), обе погашены. Целиком позеленели классы
`TestClassSuggestOffer` (4), `TestPastStartDateGate` (6), `TestDeliveryCodeBlock` (2);
`TestPointQuoteCodeBlock` 9 → 2, `TestPriceSheetMinAcrossVariants` 6 → 2,
`TestRunLiveSmoke` остался красным целиком (6 из 6).

_Мина замера, названная честно:_ набор случайно прогнан дважды (второй проход дал те же
`569 / failures=10` за 198.152 с — разброс времени от нагрузки, состав красных идентичен).
В таблицу берём первый проход.

### Набор 3 — КЛИЕНТСКИЙ КОНТУР: 345 тестов, **3 красных** (было 4), 52.4 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_client_contour test_delivery \
    test_golden_llm test_playbook_rules test_intake_bridge test_booking_draft test_dialog_card test_moderation
Ran 345 tests in 52.399s
FAILED (failures=3, skipped=10)
```

```
FAIL: test_zhivye_vnutrennie_fayly (test_client_contour.TestZhivoyKontur)
      AssertionError: True is not false : brain_writer.py обязан быть внутренним

FAIL: test_pair_xmax_price_directive_end_to_end (test_moderation.TestEndToEndMax2stix)
      AssertionError: 'fallback' not found in ('clean', 'draft') : {'text': 'Уточню наличие
      XMAX 300 по вашим датам у команды и вернусь.', 'ok': True, 'source': 'fallback',
      'attempts': 0, 'violations': [{'kind': 'avail_pos', 'raw': 'свободен'}]}

FAIL: test_pair_xmax_two_directives_both_hold_end_to_end (test_moderation.TestEndToEndMax2stix)
      AssertionError: 'fallback' not found in ('clean', 'draft') : {'text': 'Уточню наличие
      XMAX 300 по вашим датам у команды и вернусь.', 'ok': True, 'source': 'fallback',
      'attempts': 0, 'violations': [{'kind': 'avail_pos', 'raw': 'свободен'}]}
```

**Новых красных нет: все 3 входили в базовые 4.** Погас `test_dialog_card.TestCannotCompute.
test_computable_case_declares_nothing` (тот, что объявлял `no_price`) — 4 → 3.

Пути цены здесь **2 из 3** (обе XMAX-пары); третий (`brain_writer.py` обязан быть внутренним) —
не про цену, это перепись состава контура. **Природа обоих ценовых отказов СМЕНИЛАСЬ:** в базе
было «`939` not found» (число не доехало), сейчас число доезжает, но ответ уходит в `fallback`
по нарушению `avail_pos` («свободен») — предмет спора переехал с цены на утверждение о наличии.

### Набор 4 — ГАРД: 634 теста, **1 красный** (было 3), 120.1 с

Мина отчёта из базы **подтверждена и здесь**: в выводе две строки `Ran` — внутренний прогон
`test_pretool_guard.TestMarkerIsolation` поднимает подсуиту отдельным процессом. Считаем
**внешние** (строка 333 файла), иначе набор читается как 3 красных.

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_pretool_guard test_guard_action_not_text \
    test_isolation_guard test_isolation test_probe_isolation test_env_value_leak test_utf8_output_guard \
    test_card_duty test_card_terminal_log test_moderation_card
Ran 1244 tests in 71.674s      ← ПОДСУИТА, чужая строка
FAILED (failures=2, skipped=1) ← ПОДСУИТА
Ran 634 tests in 120.131s      ← НАШ СЧЁТ
FAILED (failures=1)            ← НАШ СЧЁТ
```

Внешний красный — один, и он **производный, не про цену**:

```
FAIL: test_live_marker_stays_empty_after_full_suite (test_pretool_guard.TestMarkerIsolation)
      AssertionError: 1 != 0
```

Красен он потому, что красна поднятая им подсуита, а её два падения — те же
`brain_writer`-зависимости, что и в наборе 5:

```
FAIL: test_foreign_file_change_keeps_version (test_pc_orchestrator.TestSelfUpdateDepClosure) (foreign='brain_writer.py')
FAIL: test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap (test_pc_orchestrator.TestSelfUpdateDepClosure)
```

**Новых красных нет; ушли оба ценовых.** Из базовых 3 погасли обе карточки модератора:
`test_moderation_card.TestCannotCompute.test_computable_case_declares_nothing` (`no_price`) и
`test_moderation_card.TestWhyBlock.test_why_carries_price_verbatim_and_its_source`
(`'none' != 'quote'` — обоснование цены снова знает свой источник). **Пути цены в наборе
теперь НОЛЬ** (было 2 из 3).

### Набор 5 — ОРКЕСТРАТОР: 851 тест, **2 красных** (было 2), 62.7 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_pc_orchestrator
Ran 851 tests in 62.655s
FAILED (failures=2)
```

```
FAIL: test_foreign_file_change_keeps_version (test_pc_orchestrator.TestSelfUpdateDepClosure) (foreign='brain_writer.py')
      AssertionError: '566d726c2111a857b094ab453001969002b1f252' != 'f1e3eb1b8ce8487617ad76e14cc420765d3b7ac5'
FAIL: test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap (test_pc_orchestrator.TestSelfUpdateDepClosure)
      AssertionError: Items in the first set but not the second:
      'price_gate.py'
      'queue_snapshot_pc.py'
      'price_freshness_run.py'
      'price_freshness.py'
      'brain_writer.py'
```

**ХРАПОВИК №1 «замыкание зависимостей self-update» — НА МЕСТЕ, число НЕ ВЫРОСЛО.** В базе список
незаявленных ленивых зависимостей был **пять** имён — сейчас **те же пять, буква в букву**
(порядок вывода множества иной, состав идентичен). Три правки этого захода к нему не добавили
ничего: `3309f3c` правил уже названный `price_freshness.py`, `8fd55d1` трогал только голдены.

Практическое следствие прежнее (не чиним, только называем): ступень self-update не считает
`price_gate.py` / `price_freshness.py` своими, значит их правка не поднимет версию демона.

### Набор 6 — ОЧЕРЕДЬ И ИСПОЛНЕНИЕ: 396 тестов, **2 красных** (было 2), 17.2 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_pc_local_dec test_selfupdate_gate \
    test_gate_selective test_exec_position test_result_judge_pc test_result_ref test_result_spill \
    test_series_pc test_queue_snapshot_pc test_status_truth test_nonparse_ratchet test_reviewer
Ran 396 tests in 17.189s
FAILED (failures=2)
```

```
FAIL: test_no_growth_against_baseline (test_nonparse_ratchet.TestRatchet)
      AssertionError: Lists differ: [...] != []   First list contains 8 additional elements.
      'anonymize_corpus_stable.py: было читателей 0/точек 0, стало 3/3'
      'card_terminal_log.py: было читателей 0/точек 0, стало 10/27'
      'expectations_pc.py: было читателей 0/точек 0, стало 28/79'
      'expectations_pc_run.py: было читателей 0/точек 0, стало 10/30'
      'pretool_guard.py: было читателей 97/точек 145, стало 98/146'
      'price_source.py: было читателей 0/точек 0, стало 4/5'
      … (всего 8 файлов)
FAIL: test_totals_do_not_grow (test_nonparse_ratchet.TestRatchet)
      AssertionError: 994 not less than or equal to 845
```

**ХРАПОВИК №2 «неразбор» — НА МЕСТЕ, число НЕ ВЫРОСЛО.** База: 8 файлов, **994 против отметки
845**. Сейчас: **8 файлов, 994 против 845** — совпадение точное. Он красен на `main` ДО любой
правки и меряет ЛИНИЮ (базовую отметку), а не этот заход; три правки цены вклада в него не внесли.
Пути цены не задевает.

Зелёными прошли все 11 остальных модулей набора.

### Набор 7 — МОЗГ · ЖУРНАЛ · МОСТ: 260 тестов, **0 красных**, 12.4 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_brain_writer test_brain_probe_pc \
    test_bridge_http test_cowork_log_append test_dispatch_notify test_log_setup
Ran 260 tests in 12.379s
OK
```

### Набор 8 — УРОКИ И ТРЕНАЖЁР: 357 тестов, **0 красных**, 5.2 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_lesson_router test_lesson_store \
    test_lesson_step5 test_lesson_step6 test_lesson_urok test_lesson_cycle test_trainer test_trainer_log test_trainer_run
Ran 357 tests in 5.170s
OK
```

### Набор 9 — СРЕДА ПК · RC: 393 теста, **0 красных**, 1.3 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_expectations_pc test_rc_supervisor \
    test_rc_auth_detect test_pc_agent test_pc_link_state test_pc_sleep_alarm test_pc_awake_clock \
    test_session_watch test_userbot_reconnect
Ran 393 tests in 1.254s
OK
```

---

## 3. Итог: 9 наборов из 9, 3981 тест, **22 красных** (было 66)

Гейт прогнан **целиком**, ни один набор не остался (07:41 → 07:54:02, чистое время тестов
**474.7 с**). Ни одного зависания, ни одного таймаута.

### 3.1. Сумма тестов: 3981 против прежних 3981 — совпадение точное

Ни одного теста не прибавилось и не убыло: `8fd55d1` переписывал **ожидания внутри**
существующих голденов, новых случаев не заводил. Это независимая проверка того, что набор
тот же самый, а не урезанный.

### 3.2. Красных 22 против прежних 66 — снято 44

| | база `3b85c06` | сейчас `9dc1f5d` | дельта |
|---|---|---|---|
| путь цены | **60** | **16** | −44 |
| не-ценовые | 6 | 6 | 0 |
| **всего** | **66** | **22** | **−44** |

Весь выигрыш — на пути цены; ни один не-ценовой красный не погас и не появился.
Заявленная арифметика правок (60 → 50 бюджетом сторожа `3309f3c`, 50 → 16 голденами `8fd55d1`)
**подтверждена замером на конечной точке: 16.**

### 3.3. Осталось на пути цены — 16 из прежних 60, дословно

Все 16 — прежние (входят в базовые 60), сгруппированы по набору:

```
# набор 1 — test_pricing.TestPriceRulesV2 (4)
test_cap_active_replaces_j_price_with_low_season
test_cap_inactive_uses_j_text
test_multi_model_separate_prices
test_ok_uses_quote_text_verbatim

# набор 2 — test_suggest.TestPointQuoteCodeBlock (2)
test_golden_draft_carries_column_j_verbatim
test_golden_quote_block_carries_column_j_verbatim

# набор 2 — test_suggest.TestPriceSheetMinAcrossVariants (2)
test_pointwise_pair_xmax_explicit_old_gen_both
test_pointwise_xmax_explicit_old_gen_shows_both

# набор 2 — test_suggest.TestRunLiveSmoke (6)
test_green_full_data_passes
test_green_paraphrase_with_correct_numbers_now_passes
test_red_delivery_missing_fails
test_red_j_line_not_verbatim_fails
test_red_violations_flagged_with_diff_card
test_send_delivers_probe_two_messages

# набор 3 — test_moderation.TestEndToEndMax2stix (2)
test_pair_xmax_price_directive_end_to_end
test_pair_xmax_two_directives_both_hold_end_to_end
```

**Перекрёстная сверка, и она сошлась ТОЧНО.** Разведка
[`2026-08-21-goldens-deferred.md`](2026-08-21-goldens-deferred.md) назвала 16 отложенных
голденов поимённо. Извлечение имён из неё (`grep -oE 'test_[a-z0-9_]+' | sort -u` → 21 имя,
из них 5 — имена модулей, 16 — имена тестов) даёт список, совпадающий с этими 16
**имя в имя, 16 из 16**. То есть остаток гейта на пути цены — РОВНО отложенное множество,
без единого лишнего и без единого недостающего.

### 3.4. Прежние два храповика: оба на месте, **ни один не вырос**

| храповик | база | сейчас | вырос? |
|---|---|---|---|
| замыкание зависимостей self-update (`TestSelfUpdateDepClosure`, 2) | 5 незаявленных имён | **те же 5**: `price_gate.py`, `queue_snapshot_pc.py`, `price_freshness_run.py`, `price_freshness.py`, `brain_writer.py` | **НЕТ** |
| неразбор (`test_nonparse_ratchet`, 2) | 8 файлов, **994 против отметки 845** | 8 файлов, **994 против 845** | **НЕТ** |

Оба красны на `main` до этого захода и меряют линию, а не правки цены. Третьей известной пары
не появилось.

### 3.5. Новых красных — НОЛЬ

Каждое из 22 имён сверено с базовым перечнем 66: **все 22 входят в базу, новых нет ни одного.**
22 = 16 (путь цены) + 6 не-ценовых, и не-ценовая шестёрка поимённо та же, что в базе:

```
test_client_contour.TestZhivoyKontur.test_zhivye_vnutrennie_fayly           (brain_writer внутренний)
test_pretool_guard.TestMarkerIsolation.test_live_marker_stays_empty_after_full_suite  (производный)
test_pc_orchestrator.TestSelfUpdateDepClosure.test_foreign_file_change_keeps_version
test_pc_orchestrator.TestSelfUpdateDepClosure.test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap
test_nonparse_ratchet.TestRatchet.test_no_growth_against_baseline
test_nonparse_ratchet.TestRatchet.test_totals_do_not_grow
```

Ушло за заход 44 красных, из них полностью позеленели: `test_price_gate`, `test_price_source`,
`test_price_freshness` (были зелёными и остались), классы `TestClassSuggestOffer`,
`TestPastStartDateGate`, `TestDeliveryCodeBlock`, `TestHintsAndNote`,
`TestStep2ModelTermQuoteDepositPercent`, `TestTwoPhaseDraft`, `TestClass0ModelResolveAndTerm`,
`test_dialog_card.TestCannotCompute`, `test_moderation_card.TestCannotCompute`,
`test_moderation_card.TestWhyBlock`. Ошибок (`ERROR`) во всём гейте **ноль** (в базе было 2).

### 3.6. Что этот прогон НЕ говорит

Прежняя оговорка базы в силе: гейт гнался **без живого моста**. Разница с базой в том, что
теперь источником служит записанное правило, и котировка собирается офлайн — поэтому красное
сменило природу: не «цена недоступна» (молчание сторожа), а «ожидаемый ТЕКСТ голдена не равен
посчитанному». Живой проверки котировки на работающем мосту этот заход не включал.

**Ничего не чинилось:** правок кода нет, голдены не тронуты, сторож свежести и ручка источника
цены не тронуты, ботов не перезапускали, в рабочие таблицы не писали, клиентам не отправляли.

## 4. Сводная таблица (все 9 наборов)

| № | набор | тестов | красных | было в базе | секунд | итог |
|---|---|---|---|---|---|---|
| 1 | ПУТЬ ЦЕНЫ | 176 | **4** | 20 | 42.7 | 🔴 FAILED (failures=4) |
| 2 | SUGGEST | 569 | **10** | 35 | 160.8 | 🔴 FAILED (failures=10) |
| 3 | КЛИЕНТСКИЙ КОНТУР | 345 | **3** | 4 | 52.4 | 🔴 FAILED (failures=3, skipped=10) |
| 4 | ГАРД | 634 | **1** | 3 | 120.1 | 🔴 FAILED (failures=1) |
| 5 | ОРКЕСТРАТОР | 851 | **2** | 2 | 62.7 | 🔴 FAILED (failures=2) — храповик №1 |
| 6 | ОЧЕРЕДЬ И ИСПОЛНЕНИЕ | 396 | **2** | 2 | 17.2 | 🔴 FAILED (failures=2) — храповик №2 |
| 7 | МОЗГ · ЖУРНАЛ · МОСТ | 260 | 0 | 0 | 12.4 | 🟢 OK |
| 8 | УРОКИ И ТРЕНАЖЁР | 357 | 0 | 0 | 5.2 | 🟢 OK |
| 9 | СРЕДА ПК · RC | 393 | 0 | 0 | 1.3 | 🟢 OK |
| **ИТОГО** | **9 из 9** | **3981** | **22** | **66** | **474.7** | 6 красных наборов, 3 зелёных |

