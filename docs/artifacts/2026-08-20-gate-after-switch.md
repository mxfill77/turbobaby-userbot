# Полный гейт наборами на переключённом источнике цены (21.08.2026)

**Дерево:** `3b85c06` (2026-08-21 01:55). **Прогон начат:** 2026-08-21 02:31:33 (локальное).
**Основание:** коммит `45a38cf` переключил источник цены на записанное правило; полный гейт при
этом не гонялся (заходы 67 и 70 умерли на объёме). Здесь — только прогон и доклад. **Кода не
правим, красное — результат.**

## 0. Перепись наборов (до прогона)

Тест-модулей в git — **60**, тестов по грепу `^\s*(async )?def test` — **3897**
(греп-оценка; истина — строка `Ran N tests` каждого прогона).

| № | набор | модулей | тестов (греп) |
|---|---|---|---|
| 1 | **ПУТЬ ЦЕНЫ** — `price_source`, `price_gate`, `price_freshness`, `pricing` | 4 | 176 |
| 2 | **SUGGEST** — `suggest` (точка врезки сторожа) | 1 | 569 |
| 3 | **КЛИЕНТСКИЙ КОНТУР** — `client_contour`, `delivery`, `golden_llm`, `playbook_rules`, `intake_bridge`, `booking_draft`, `dialog_card`, `moderation` | 8 | 345 |
| 4 | **ГАРД** — `pretool_guard`, `guard_action_not_text`, `isolation_guard`, `isolation`, `probe_isolation`, `env_value_leak`, `utf8_output_guard`, `card_duty`, `card_terminal_log`, `moderation_card` | 10 | 578+ |
| 5 | **ОРКЕСТРАТОР** — `pc_orchestrator` | 1 | 823 |
| 6 | **ОЧЕРЕДЬ И ИСПОЛНЕНИЕ** — `pc_local_dec`, `selfupdate_gate`, `gate_selective`, `exec_position`, `result_judge_pc`, `result_ref`, `result_spill`, `series_pc`, `queue_snapshot_pc`, `status_truth`, `nonparse_ratchet`, `reviewer` | 12 | 396 |
| 7 | **МОЗГ · ЖУРНАЛ · МОСТ** — `brain_writer`, `brain_probe_pc`, `bridge_http`, `cowork_log_append`, `dispatch_notify`, `log_setup` | 6 | 260 |
| 8 | **УРОКИ И ТРЕНАЖЁР** — `lesson_router`, `lesson_store`, `lesson_step5`, `lesson_step6`, `lesson_urok`, `lesson_cycle`, `trainer`, `trainer_log`, `trainer_run` | 9 | 346 |
| 9 | **СРЕДА ПК · RC** — `expectations_pc`, `rc_supervisor`, `rc_auth_detect`, `pc_agent`, `pc_link_state`, `pc_sleep_alarm`, `pc_awake_clock`, `session_watch`, `userbot_reconnect` | 9 | 393 |

Порядок прогона: **1 первым** (там правка), дальше по номерам. Форма запуска одна на все:

```
TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest <модули>
```

## 1. Результаты по наборам (дописывается ПОСЛЕ КАЖДОГО)

| № | набор | тестов | красных | секунд | итог |
|---|---|---|---|---|---|
| 1 | ПУТЬ ЦЕНЫ | 176 | **20** | 25.9 | 🔴 FAILED (failures=20) |
| 2 | SUGGEST | 569 | **35** | 166.1 | 🔴 FAILED (failures=33, errors=2) |
| 3 | КЛИЕНТСКИЙ КОНТУР | 345 | **4** | 36.3 | 🔴 FAILED (failures=4, skipped=10) |
| 4 | ГАРД | 634 | **3** | 110.3 | 🔴 FAILED (failures=3) |
| 5 | ОРКЕСТРАТОР | 851 | **2** | 69.0 | 🔴 FAILED (failures=2) — прежние известные |
| 6 | ОЧЕРЕДЬ И ИСПОЛНЕНИЕ | 396 | **2** | 16.5 | 🔴 FAILED (failures=2) — прежние известные |
| 7 | МОЗГ · ЖУРНАЛ · МОСТ | 260 | 0 | 12.3 | 🟢 OK |
| 8 | УРОКИ И ТРЕНАЖЁР | 357 | 0 | 5.1 | 🟢 OK |
| 9 | СРЕДА ПК · RC | 393 | 0 | 1.2 | 🟢 OK |
| **ИТОГО** | **9 из 9 наборов** | **3981** | **66** | **442.7** | 6 наборов красных, 3 зелёных |


### Набор 1 — ПУТЬ ЦЕНЫ: 176 тестов, 20 красных, 25.9 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_price_source test_price_gate test_price_freshness test_pricing
Ran 176 tests in 25.878s
FAILED (failures=20)
```

**Все 20 — в `test_pricing`. В `test_price_source`, `test_price_gate`, `test_price_freshness`
красных ноль** (36+11+36 = 83 зелёных, ровно как в заходе `45a38cf`).

Дословный перечень красных:

```
FAIL: test_live_xsr155_resolved_quote_present (test_pricing.TestClass0ModelResolveAndTerm)
FAIL: test_note_none_available_no_number (test_pricing.TestHintsAndNote)
FAIL: test_note_quote_ok_carries_figure (test_pricing.TestHintsAndNote)
FAIL: test_at_min_days_no_min_message (test_pricing.TestPriceRulesV2)
FAIL: test_cap_active_but_total_below_cap_no_low_season (test_pricing.TestPriceRulesV2)
FAIL: test_cap_active_replaces_j_price_with_low_season (test_pricing.TestPriceRulesV2)
FAIL: test_cap_inactive_uses_j_text (test_pricing.TestPriceRulesV2)
FAIL: test_moto_below_min_offers_3_days (test_pricing.TestPriceRulesV2)
FAIL: test_multi_model_separate_prices (test_pricing.TestPriceRulesV2)
FAIL: test_ok_uses_quote_text_verbatim (test_pricing.TestPriceRulesV2)
FAIL: test_ok_without_text_falls_back_to_assembly (test_pricing.TestPriceRulesV2)
FAIL: test_scooter_below_min_offers_5_days (test_pricing.TestPriceRulesV2)
FAIL: test_class_offer_whitelists_only_computed_numbers (test_pricing.TestStep2ModelTermQuoteDepositPercent)
FAIL: test_foreign_model_card_blocked_by_postcheck (test_pricing.TestStep2ModelTermQuoteDepositPercent)
FAIL: test_golden_paraphrases_model_term_carry_deposit (test_pricing.TestStep2ModelTermQuoteDepositPercent)
FAIL: test_golden_percent_of_calculation (test_pricing.TestStep2ModelTermQuoteDepositPercent)
FAIL: test_golden_percent_paraphrases (test_pricing.TestStep2ModelTermQuoteDepositPercent)
FAIL: test_golden_xsr155_two_weeks_quote_with_deposit (test_pricing.TestStep2ModelTermQuoteDepositPercent)
FAIL: test_own_deposit_and_total_kept_by_postcheck (test_pricing.TestStep2ModelTermQuoteDepositPercent)
FAIL: test_phase_b_quote_ok_uses_figure (test_pricing.TestTwoPhaseDraft)
```

Тексты отказов (дословно, три образца — остальные того же вида):

```
AssertionError: '472' not found in 'ЦЕНА: точная цена из Календаря сейчас недоступна — НЕ
называй никакого числа (в т.ч. из FAQ); ответь, что уточнишь цену и вернёшься.'

AssertionError: 'Уточню у команды и вернусь.\n[уточнить: цена 6608]'
              != 'XSR 155: 6608 ฿ за 14 дней, депозит 7000 ฿.'

AssertionError: False is not true   # _run("NMAX <даты> почём?").startswith("HAS_PRICE")
```

**Задевает ли путь цены: ДА, это он и есть.** Красное — не случайный сосед: все 20 говорят одно —
котировка вернулась молчанием сторожа («цена недоступна») там, где тест ждал число. Ремонта тут
не делаем (запрет задания), но факт зафиксирован: **`test_pricing` не был приведён к
переключённому источнику** — в заходе `45a38cf` гонялись только `test_price_source`,
`test_price_freshness` и новый `test_price_gate`, а `test_pricing` в тот прогон не входил.

### Набор 2 — SUGGEST: 569 тестов, 35 красных, 166.1 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_suggest
Ran 569 tests in 166.095s
FAILED (failures=33, errors=2)
```

Красные по классам (дословные имена):

```
ERROR: test_a_asked_model_is_first_then_same_class_and_above (TestClassSuggestOffer)
ERROR: test_deposit_sum_default_no_passport               (TestPointQuoteCodeBlock)
FAIL:  test_b_busy_asked_model_gets_replacements_not_refusal      (TestClassSuggestOffer)
FAIL:  test_c_never_falls_below_class_when_nothing_free_above     (TestClassSuggestOffer)
FAIL:  test_d_heavy_class_only_when_client_named_power_size_experience (TestClassSuggestOffer)
FAIL:  test_build_note_carries_delivery_block                     (TestDeliveryCodeBlock)
FAIL:  test_regen_carries_delivery_by_code                        (TestDeliveryCodeBlock)
FAIL:  test_golden_future_dates_unchanged                         (TestPastStartDateGate)
FAIL:  test_golden_pickup_note_en                                 (TestPastStartDateGate)
FAIL:  test_golden_start_today_quotes_and_asks_pickup_time         (TestPastStartDateGate)
FAIL:  test_golden_start_tomorrow_quotes_and_asks_pickup_time      (TestPastStartDateGate)
FAIL:  test_golden_timezone_decides_gate_on_day_border             (TestPastStartDateGate)
FAIL:  test_golden_year_roll_dec_to_jan_range_is_future            (TestPastStartDateGate)
FAIL:  test_golden_year_roll_january_from_december_is_future       (TestPastStartDateGate)
FAIL:  test_build_note_carries_quote_block                        (TestPointQuoteCodeBlock)
FAIL:  test_deposit_passport_quote_block                          (TestPointQuoteCodeBlock)
FAIL:  test_golden_draft_carries_column_j_verbatim                (TestPointQuoteCodeBlock)
FAIL:  test_golden_quote_block_carries_column_j_verbatim          (TestPointQuoteCodeBlock)
FAIL:  test_prompt_strips_quote_block                             (TestPointQuoteCodeBlock)
FAIL:  test_strategy_regen_carries_price_by_code                  (TestPointQuoteCodeBlock)
FAIL:  test_strategy_regen_marker_inserts_canon_once              (TestPointQuoteCodeBlock)
FAIL:  test_strategy_regen_units_carry_per_each_by_code           (TestPointQuoteCodeBlock)
FAIL:  test_units_carry_block_per_each                            (TestPointQuoteCodeBlock)
FAIL:  test_pointwise_non_xmax_single_line_unchanged        (TestPriceSheetMinAcrossVariants)
FAIL:  test_pointwise_pair_xmax_explicit_old_gen_both       (TestPriceSheetMinAcrossVariants)
FAIL:  test_pointwise_pair_xmax_price_per_each              (TestPriceSheetMinAcrossVariants)
FAIL:  test_pointwise_quote_tail_scrubs_gen_year            (TestPriceSheetMinAcrossVariants)
FAIL:  test_pointwise_xmax_default_new_gen_only             (TestPriceSheetMinAcrossVariants)
FAIL:  test_pointwise_xmax_explicit_old_gen_shows_both      (TestPriceSheetMinAcrossVariants)
FAIL:  test_green_full_data_passes                                (TestRunLiveSmoke)
FAIL:  test_green_paraphrase_with_correct_numbers_now_passes       (TestRunLiveSmoke)
FAIL:  test_red_delivery_missing_fails                             (TestRunLiveSmoke)
FAIL:  test_red_j_line_not_verbatim_fails                          (TestRunLiveSmoke)
FAIL:  test_red_violations_flagged_with_diff_card                  (TestRunLiveSmoke)
FAIL:  test_send_delivers_probe_two_messages                       (TestRunLiveSmoke)
```

Тексты отказов, сгруппированные (`uniq -c` по строке исключения):

```
5  AssertionError: unexpectedly None
3  AssertionError: 'failed' != 'passed'
3  AssertionError: '1685' not found in 'ЦЕНА: точная цена из Календаря сейчас недоступна — НЕ
   называй никакого числа (в т.ч. из FAQ); ответь, что уточнишь цену и вернёшься.'
2  AssertionError: False is not true
2  AssertionError: 0 != 1
1  TypeError: argument of type 'NoneType' is not iterable
1  IndexError: list index out of range
1  AssertionError: unexpectedly None : сколько стоит xmax 16-24 июля
1  AssertionError: 'заняты' not found in 'ЦЕНА: точная цена … недоступна …'
1  AssertionError: 'ЦЕНА из Календаря' not found in 'ЦЕНА: точная цена … недоступна …'
1  AssertionError: 'Скидка за срок 15%' not found in '🧪 E2E-СМОУК ПРОВАЛЕН — дифф
   ОЖИДАНИЕ/ФАКТ: … — [строка J дословно] ожидание: (строка столбца J из quote-блока)
   факт: quote-блок не собран — цена не доехала'
1  AssertionError: '[QUOTE]' not found in "<полный промпт менеджера, 12 КБ>"
```

**Задевает ли путь цены: ДА, весь набор красных — один класс.** Опознавательный признак виден
дословно: «quote-блок **не собран — цена не доехала**» и подстановка вместо числа фразы сторожа
«точная цена из Календаря сейчас недоступна». Классы `TestPointQuoteCodeBlock`,
`TestPriceSheetMinAcrossVariants`, `TestPastStartDateGate`, `TestRunLiveSmoke`,
`TestDeliveryCodeBlock` падают не по своей теме (доставка, даты, поколения XMAX), а потому, что
**через них проходит котировка**: врезка `price_gate` в `_safe_quote_for_model` гасит цену, и
следом рассыпаются все голдены, где ожидалось число или блок `[QUOTE]`.

### Набор 3 — КЛИЕНТСКИЙ КОНТУР: 345 тестов, 4 красных, 36.3 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_client_contour test_delivery \
    test_golden_llm test_playbook_rules test_intake_bridge test_booking_draft test_dialog_card test_moderation
Ran 345 tests in 36.308s
FAILED (failures=4, skipped=10)
```

```
FAIL: test_zhivye_vnutrennie_fayly (test_client_contour.TestZhivoyKontur)
      AssertionError: True is not false : brain_writer.py обязан быть внутренним
FAIL: test_computable_case_declares_nothing (test_dialog_card.TestCannotCompute)
      AssertionError: Tuples differ: ({'code': 'no_price', 'line': 'цены нет — …л'},) != ()
FAIL: test_pair_xmax_price_directive_end_to_end (test_moderation.TestEndToEndMax2stix)
      AssertionError: '939' not found in 'Отличный выбор! Пара XMAX 300 на 16–24 июля — вот цены за каждый скутер: …'
FAIL: test_pair_xmax_two_directives_both_hold_end_to_end (test_moderation.TestEndToEndMax2stix)
      AssertionError: '939' not found in 'Отличный выбор! Пара XMAX 300 на 16–24 июля — держите цены за каждый: …'
```

**Задевает ли путь цены: 3 из 4 — да** (`no_price` в карточке диалога и дважды не доехавшая цена
XMAX 939 в сквозном модерационном прогоне — тот же класс, что наборы 1–2). Четвёртый,
`test_client_contour.test_zhivye_vnutrennie_fayly` («`brain_writer.py` обязан быть внутренним»), —
**НЕ про цену**, это перепись состава клиентского контура. Зелёными целиком прошли `test_delivery`
(90), `test_golden_llm` (10), `test_playbook_rules` (24), `test_intake_bridge` (30),
`test_booking_draft` (12).

### Набор 4 — ГАРД: 634 теста, 3 красных, 110.3 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_pretool_guard test_guard_action_not_text \
    test_isolation_guard test_isolation test_probe_isolation test_env_value_leak test_utf8_output_guard \
    test_card_duty test_card_terminal_log test_moderation_card
Ran 634 tests in 110.295s
FAILED (failures=3)
```

Мина отчёта: в выводе **две** строки `Ran` — одна чужая. Внутренний прогон
`test_pretool_guard.TestMarkerIsolation` поднимает подсуиту отдельным процессом
(`Ran 1244 tests in 62.291s / FAILED (failures=2, skipped=1)`, строки 327–329 файла), и её
`FAIL:` попадают в общий текст. Считать надо **внешние 634/3**, иначе набор читается как 5 красных.

Внешние красные:

```
FAIL: test_live_marker_stays_empty_after_full_suite (test_pretool_guard.TestMarkerIsolation)
      AssertionError: 1 != 0    ← падает ПОТОМУ, что упала поднятая им подсуита
FAIL: test_computable_case_declares_nothing (test_moderation_card.TestCannotCompute)
      AssertionError: Tuples differ: ({'code': 'no_price', 'line': 'цены нет — …л'},) != ()
FAIL: test_why_carries_price_verbatim_and_its_source (test_moderation_card.TestWhyBlock)
      AssertionError: 'none' != 'quote'
```

Красные подсуиты (они же — предвестники набора 5, обе про `brain_writer.py`):

```
FAIL: test_foreign_file_change_keeps_version (test_pc_orchestrator.TestSelfUpdateDepClosure) (foreign='brain_writer.py')
      AssertionError: '566d726c2111a857b094ab453001969002b1f252' != 'f1e3eb1b8ce8487617ad76e14cc420765d3b7ac5'
FAIL: test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap (test_pc_orchestrator.TestSelfUpdateDepClosure)
      AssertionError: Items in the first set but not the second: …
```

**Задевает ли путь цены: 2 из 3 внешних — да** (`no_price` и `'none' != 'quote'` в карточке
модератора: обоснование «почему такая цена» осталось без источника котировки). Третий,
маркер-изоляция, — **производный**: он красен не сам по себе, а потому что подсуита внутри него
красна теми же двумя `brain_writer`-падениями, что и `test_client_contour` в наборе 3. Гард как
таковой (`test_guard_action_not_text`, `test_isolation_guard`, `test_probe_isolation`,
`test_env_value_leak`, `test_utf8_output_guard`, `test_card_duty`, `test_card_terminal_log`) —
**зелёный целиком**.

### Набор 5 — ОРКЕСТРАТОР: 851 тест, 2 красных, 69.0 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_pc_orchestrator
Ran 851 tests in 68.979s
FAILED (failures=2)
```

```
FAIL: test_foreign_file_change_keeps_version (test_pc_orchestrator.TestSelfUpdateDepClosure) (foreign='brain_writer.py')
      AssertionError: '566d726c2111a857b094ab453001969002b1f252' != 'f1e3eb1b8ce8487617ad76e14cc420765d3b7ac5'
FAIL: test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap (test_pc_orchestrator.TestSelfUpdateDepClosure)
      AssertionError: Items in the first set but not the second:
      'queue_snapshot_pc.py'
      'brain_writer.py'
      'price_freshness_run.py'
      'price_gate.py'
      'price_freshness.py'
```

**Это те самые «два прежних известных красных» — тесты те же, но содержимое второго ВЫРОСЛО.**
Известное состояние: линия замыкания зависимостей self-update не знает **трёх** файлов. Замер
сейчас — **пять**, и два добавочных названы поимённо: **`price_gate.py`** (новый модуль врезки из
`45a38cf`) и **`price_freshness.py`**. То есть переключение источника цены расширило известный
красный, а не создало третий: номер падения прежний, список внутри длиннее на два имени.

**Задевает ли путь цены: да — именами файлов** (`price_gate.py`, `price_freshness.py` в списке
незаявленных зависимостей), но не логикой котировки. Практическое следствие (не чиним, только
называем): ступень self-update не считает эти файлы своими, значит их правка **не поднимет версию
демона** и не запустит перевыкатку.

### Набор 6 — ОЧЕРЕДЬ И ИСПОЛНЕНИЕ: 396 тестов, 2 красных, 16.5 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_pc_local_dec test_selfupdate_gate \
    test_gate_selective test_exec_position test_result_judge_pc test_result_ref test_result_spill \
    test_series_pc test_queue_snapshot_pc test_status_truth test_nonparse_ratchet test_reviewer
Ran 396 tests in 16.492s
FAILED (failures=2)
```

```
FAIL: test_no_growth_against_baseline (test_nonparse_ratchet.TestRatchet)
      AssertionError: Lists differ: ['anonymize_corpus_stable.py: было читател…5/5'] != []
      First list contains 8 additional elements.
      'anonymize_corpus_stable.py: было читателей 0/точек 0, стало 3/3'
      'card_terminal_log.py: было читателей 0/точек 0, стало 10/27'  … (всего 8 файлов)
FAIL: test_totals_do_not_grow (test_nonparse_ratchet.TestRatchet)
      AssertionError: 994 not less than or equal to 845
```

**Это второй известный красный — храповик неразбора, красный на `main` ДО любой правки этого
захода.** Он меряет ЛИНИЮ (базовую отметку), а не наш коммит: линия не знает 8 файлов, итог
994 против отметки 845. Свой вклад храповику здесь не приписываем.

**Задевает ли путь цены: нет.** Ни `price_*`, ни `suggest` в первых показанных именах нет; предмет
падения — базовая отметка неразбора по репозиторию целиком. Зелёными прошли `test_pc_local_dec`,
`test_selfupdate_gate`, `test_gate_selective`, `test_exec_position`, `test_result_judge_pc`,
`test_result_ref`, `test_result_spill`, `test_series_pc`, `test_queue_snapshot_pc`,
`test_status_truth`, `test_reviewer`.

### Набор 7 — МОЗГ · ЖУРНАЛ · МОСТ: 260 тестов, 0 красных, 12.3 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_brain_writer test_brain_probe_pc \
    test_bridge_http test_cowork_log_append test_dispatch_notify test_log_setup
Ran 260 tests in 12.306s
OK
```

Задевает ли путь цены: не задевает — набор зелёный целиком.

### Набор 8 — УРОКИ И ТРЕНАЖЁР: 357 тестов, 0 красных, 5.1 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_lesson_router test_lesson_store \
    test_lesson_step5 test_lesson_step6 test_lesson_urok test_lesson_cycle test_trainer test_trainer_log test_trainer_run
Ran 357 tests in 5.053s
OK
```

Задевает ли путь цены: не задевает — набор зелёный целиком.

### Набор 9 — СРЕДА ПК · RC: 393 теста, 0 красных, 1.2 с

```
$ TURBOBABY_TEST_LOGS=1 venv/Scripts/python.exe -m unittest test_expectations_pc test_rc_supervisor \
    test_rc_auth_detect test_pc_agent test_pc_link_state test_pc_sleep_alarm test_pc_awake_clock \
    test_session_watch test_userbot_reconnect
Ran 393 tests in 1.203s
OK
```

Задевает ли путь цены: не задевает — набор зелёный целиком.

---

## 2. Итог: 9 наборов из 9, 3981 тест, 66 красных

Гейт прогнан **целиком**, ни один набор не остался (прогон 02:31:33 → 02:46:08, чистое время
тестов **442.7 с**). Все 60 модулей отработали, ни одного зависания и ни одного таймаута.

### 2.1. Разбор 66 красных по происхождению

| красных | что это | путь цены |
|---|---|---|
| **60** | котировка гаснет сторожем: `test_pricing` 20, `test_suggest` 35, `test_dialog_card` 1, `test_moderation` 2, `test_moderation_card` 2 | **ДА, прямо** |
| **2** | `test_pc_orchestrator.TestSelfUpdateDepClosure` — незаявленные ленивые зависимости | косвенно (именами `price_gate.py`, `price_freshness.py`) |
| **2** | `test_nonparse_ratchet` — базовая отметка неразбора по репо | нет |
| **1** | `test_client_contour.test_zhivye_vnutrennie_fayly` — «`brain_writer.py` обязан быть внутренним» | нет |
| **1** | `test_pretool_guard.TestMarkerIsolation` — производный: красна поднятая им подсуита | нет |

### 2.2. Прежние известные красные — те же или новые?

**Обе известные пары НА МЕСТЕ, обе ВЫРОСЛИ, третьей известной пары не появилось.** Новыми
являются 60 красных пути цены плюс 2 не-ценовых (`client_contour`, маркер-изоляция).

* **Храповик неразбора** (`test_nonparse_ratchet`, 2) — тот же, что был красен на `main` до
  захода. Вырос: линия не знает **8** файлов, итог **994 против отметки 845**.
* **Замыкание зависимостей** (`TestSelfUpdateDepClosure`, 2) — тоже прежний, но список внутри
  вырос с прежних имён до **пяти**, и одно из добавочных — **`price_gate.py`**, созданный
  коммитом `45a38cf`.

### 2.3. Почему 60 красных именно там, где они есть (атрибуция, без ремонта)

`git show --stat 45a38cf` — коммит тронул **пять** файлов:

```
 price_gate.py        | 174 ++++++++++  (новый)
 price_source.py      |  27 ++++--
 suggest.py           |  26 ++++--
 test_price_gate.py   | 223 ++++++++++  (новый)
 test_price_source.py |  59 ++++----
```

Тестовых файлов среди них **два, и оба зелёные**. А `test_pricing.py`, `test_suggest.py`,
`test_moderation.py`, `test_moderation_card.py`, `test_dialog_card.py` коммит **не трогал** — их
голдены остались от эпохи живого листа и ждут числа там, где новый источник отвечает молчанием
сторожа. Это и есть материальная причина всех 60: **не поломка врезки, а неприведённые к новому
источнику ожидания**. Красные `brain_writer`-класса (`client_contour`, dep-closure,
маркер-изоляция) к `45a38cf` отношения не имеют вовсе — этот файл коммит не касался.

### 2.4. Чего этот прогон НЕ говорит

Что цена сломана «в проде», он не говорит: набор гнался **без живого моста**, и врезка
`price_gate` в такой среде обязана отвечать `may_quote=False` — это её заявленное поведение
(«отказ никогда не становится разрешением»). Красное здесь означает, что **тесты не отличают
законное молчание от поломки**, а не что бот перестал называть цену клиенту. Живая проверка
котировки на работающем мосту в этот заход не входила.

Ничего не чинилось: правок кода в заходе нет, ручка источника цены и сторож свежести не тронуты.

