# Карта guard-модели: suggest-черновики, прайс-блоки, текст клиента, браковка

Разведка 2026-07-23 (шаг 1/7, родитель 274). Grep по «suggest», «черновик»,
«прайс», «блок», «MT-03», «PCX», «Nmax». Формат: `файл:функция — роль`.
Логика кода не менялась.

## 1. Текст запроса клиента (откуда берётся вход)

- `userbot_listen.py:on_incoming` (158) — приём входящих ЛС клиента userbot-ом; точка входа контура.
- `suggest.py:on_client_message` (5634) — хендлер сообщения клиента: читает диалог, запускает генерацию черновика, кладёт в очередь модерации.
- `suggest.py:_fetch_messages` (969) / `_resolve_replies` (937) — чтение последних сообщений диалога из Telegram (с разворотом reply-цепочек).
- `suggest.py:transcript_from` (1023) — сборка транскрипта «Клиент:/Менеджер:» из сообщений; канонический источник текста для всех детектов.
- `suggest.py:read_transcript` (1228) — обёртка fetch + transcript.
- `suggest.py:_client_text` (1300) / `_manager_text` (1304) / `_client_messages` (1580) — выделение реплик клиента (или менеджера) из транскрипта.
- `suggest.py:last_client_message` (2443) — последняя (newest) реплика клиента — на ней работают интент-детекты.
- `suggest.py:extract_booking_hints` (2002) / `collected_facts` (2174) — из транскрипта: модель, даты, локация, депозит → hints для прайс-блока и промпта.

## 2. Сборка suggest-черновика

- `suggest.py:make_system_prompt` (3658) — system-промпт LLM: FAQ, pricing_note (служебные блоки вырезает — LLM цифры сетки/quote/доставки не видит), playbook, собранные факты, следующий шаг.
- `suggest.py:generate_draft` (4932) — вызов LLM → сырой черновик.
- `suggest.py:regenerate_draft` (4981) — перегенерация черновика по директиве модератора (edit-путь).
- `suggest.py:compose_sheet_draft` (3097) — детерминированная финальная сборка sheet-режима: intro (LLM) + прайс-сетка ДОСЛОВНО кодом + outro (LLM).
- `suggest.py:compose_quote_draft` (3124) — точечный quote-блок Bridge в финал ТОЛЬКО КОДОМ и ВСЕГДА (вариант Б #274: маркерный режим, LLM цифр не видит; метка [QUOTE] → точка вставки, нет метки → хвостом).
- `suggest.py:compose_delivery_draft` (3161) — приклейка канонической строки доставки кодом ВСЕГДА (LLM блок не видит; вариант Б #274 — дедуп по числам снят, пропуск только для тарифа, уже названного клиенту в окне).
- `suggest.py:post_draft` (5453) — постинг готового черновика в группу модерации.
- `suggest.py:poll_and_send` (5516) / `send_to_client` (5419) — poll ready-решений из IPC и отправка утверждённого текста клиенту.
- `suggest.py:client_facing_text` (4709) / `_strip_internal_markers_for_send` (4747) — очистка служебных маркеров перед отправкой клиенту.

## 3. Блоки прайса (в т.ч. модели MT-03 / PCX / Nmax)

- `suggest.py:_detect_model` (1586) / `_detect_models` (1593) — детект названий моделей (NMAX/PCX/MT-03/…) в тексте клиента.
- `suggest.py:extract_requested_models` (2048) — шаг 2/7: ЯВНО запрошенные модели/класс из текста клиента (словарь синонимов латиница+кириллица, «160 кубов»→класс 150–160cc; границы «от 200 кубов» — не класс; None если явного запроса нет).
- `suggest.py:_catalog_scope_asked` (2098) / `filter_rows_by_requested` (3171) — шаг 3/7: гвард-модель. Хинт `requested_models` (из ПОСЛЕДНЕЙ реплики, не окна — класс 22:27) в `extract_booking_hints`; каталог-вопрос модель-пример НЕ сужает (cc-класс — сужает). `build_price_sheet_note` фильтрует сетку: карточки чужих моделей не включаются; None → прежнее поведение; пустое пересечение → полная сетка (не немеем).
- `suggest.py:_detect_catalog_model` (1197) — модель из каталог-контекста первого сообщения.
- `suggest.py:resolve_park_model` (548) — canon → конкретная модель парка (`nmax`→`nmax155`, `pcx`→`(pcx150,pcx160)`); защита от подстановки чужой карточки (живой провал MT-03/≈5166฿).
- `suggest.py:_bike_key` (459) / `_park_bike_names` (465) / `park_allowlist` (491) / `_fleet_model_keys` (538) — нормализация имён и allowlist только реально имеющихся в парке моделей.
- `suggest.py:bike_class` (1614) — скутер/мотоцикл по префиксам (`_SCOOTER_PREFIXES`: PCX, NMAX, FORZA, XMAX, XADV, ADV) — группировка сетки.
- `suggest.py:_safe_quote_for_model` (2550) / `_resolve_model_price` (2623) — живой quote Bridge по модели+датам (fail-safe на ошибке).
- `suggest.py:_client_price` (2562) / `_quote_j_line` (2597) — клиентская строка цены и дословная строка столбца J листа.
- `suggest.py:price_sheet` (2783) — сетка прайса по всему парку на дату (через Bridge).
- `suggest.py:render_price_sheet` (2948) — детерминированный рендер прайс-блока (КОД, не LLM): группы «Скутеры:»/«Мотоциклы:».
- `suggest.py:filter_sheet_rows` (3025) / `_model_cc` (3007) — фильтр сетки по классу/кубатуре из запроса клиента.
- `suggest.py:_asks_price_sheet` (1913) / `_parse_sheet_filter` (1968) — детект интента «прайс по парку» (голдены = реальные фразы клиента, см. CLAUDE.md).
- `suggest.py:build_price_sheet_note` (3438) — блок сетки для pricing_note, если клиент просит прайс по парку (гейт дат сетку НЕ блокирует — решение владельца).
- `suggest.py:build_pricing_note` (3485) / `_build_pricing_note_core` (3493) — общий блок ЦЕНА/ПРАЙС: quote кодом → готовые числа в промпт → белый список пост-чека.
- `suggest.py:_wrap_price_sheet` (3309) — упаковка сетки в служебные скобки (транспорт до compose_sheet_draft, в промпт LLM не попадает).
- `suggest.py:_sheet_block_from_note` (3090) / `_quote_block_from_note` (3117) / `_delivery_block_from_note` (3154) — извлечение служебных блоков из pricing_note при финальной сборке.
- `suggest.py:_units_count` (1784) / `_units_per_each_line` (1818) — N юнитов одной модели: цена/депозит «за каждый» хвостом quote-блока.

## 4. Браковка черновиков (решение человека)

- `suggest.py:parse_approval` (918) — разбор ответа менеджера: «+»=approve, «-»/«нет»/«no»/«отклонить»=reject, иной текст=edit.
- `suggest.py:on_moderation_reply` (5753) — reply менеджера в группе модерации → approve/edit/reject; reject → черновик не отправляется.
- `suggest.py:is_approver` (1233) — право approve/edit/reject по APPROVER-whitelist (пустой = любой участник).
- `moderation_core.py:interpret` (87) — LLM-классификация реплики менеджера: intent ∈ approve|reject|question|cosmetic|strategy|dictation; «-»/«нет» — быстрый путь reject без LLM.
- `moderation_core.py:process_callback` (132) — кнопки карточки: `no` → decision `rejected` («❌ Отклонено»).
- `moderation_core.py:process_reply` (216) — полный цикл решения по черновику из текстовой реплики (intent reject → rejected).
- `moderation_bot.py:job_poll_new` (123) — постинг новых черновиков карточками; авто-браковка internal staff (STAFF-SKIP) → `rejected`, чтобы не висели `new`.
- `moderation_bot.py:on_callback` (446) / `on_group_message` (476) / `_apply` (157) — приём решения модератора и запись `rejected` в IPC.
- `moderation_ipc.py:set_decision` (239) / `mark` (249) — запись финального статуса черновика `ready | test_held | rejected` в SQLite-очередь; `rejected` = забракован, отправки не будет.

## 5. Автоматическая браковка / гварды кодом (до модерации)

- `suggest.py:model_claim_mismatch` / `_claimed_models` / `_sheet_block_models` — шаг 4/7: браковка по расхождению «в тексте черновика ЗАЯВЛЕНА модель (модель + ценовое слово в ОДНОМ предложении, «дам развёрнуто по Nmax»), а карточки прайс-блока — чужой (MT-03)». Врезка в `on_client_message` бракует ШТАТНЫМ каналом отбраковки-до-модерации (как сбой генерации): причина в лог + видимая заметка 🛑 в группу, карточки/IPC нет. Голое упоминание/служебный хвост/полная сетка с заявленной моделью — не браковка.
- `test_suggest.py:TestWindow7562315636` — шаг 5/7: сквозной ГОЛДЕН окна 7562315636 через `on_client_message` («Пришлите прайс: интересуют 160 кубов и PCX»): здоровый путь — в карточке модератора ТОЛЬКО NMAX 155 + PCX 160 (чужие блоки отфильтрованы, шаг 3); LLM котирует MT-03 при чистом блоке ИЛИ нота привезла блок MT-03 при заявке «по Nmax» (живая форма окна) → черновик ЗАБРАКОВАН (шаг 4).
- `suggest.py:postcheck_draft` (4367) — пост-чек цен: денежные числа черновика сверяются с белым списком pricing_note; чужая цифра → манагер-нота/блокировка сегмента.
- `suggest.py:_pc_wl_price_numbers` (4193) / `extract_money_figures` (4234) / `_pc_classify` (4312) — белый список цен и классификация денежных сегментов (с LLM-тайбрейком).
- `suggest.py:guard_availability` (4885) / `availability_violations` (4852) / `availability_fallback` (4876) — гвард наличия: недоказанные заявления «свободен/в наличии» → замена на fallback.
- `suggest.py:postcheck_free_pickup` (4439) — гвард ложной «бесплатной доставки» при платной зоне.
- `suggest.py:drop_price_deflection` (4635) / `ensure_price_figure` (4663) — гвард отговорок «пришлю прайс отдельно» при готовом блоке ЦЕНА: число обязано дойти клиенту.
- `suggest.py:drop_answered_questions` (4520) / `drop_repeated_delivery` (4558) — вычистка переспросов уже данного.
- `suggest.py:is_internal_sender` (147) / `is_internal_user_id` (160) / `is_internal_chat` (169) — жёсткий блок КОДОМ ДО LLM: на своих (team_registry) черновики не генерятся (инцидент 15.07).
- `suggest.py:is_enabled` (864) / `disable` (869) / `reset_disabled` (875) — kill-switch всего suggest-контура с причиной.

## Статус цепи #274 (2026-07-23, шаги 6–7)

- Шаг 6/7 (деплой + проверка окна) — выполнен фактом штатной автоматики: авто-фетч
  + реконсиляция детей рестартнули userbot/moderation_bot на 758f1de (13:16–13:17);
  окно 7562315636 закрыто сквозными голденами `TestWindow7562315636` (полный гейт
  1754 OK), живой надзор окна — пост-релизный ревизор (чек-лист #92).
- Шаг 7/7 (обязательный смоук `runLiveSmoke`) — первый прогон как у демона
  (`pc_orchestrator._exec_smoke_step`, тулза `run_chain_smoke.py`): **failed**,
  2/2 систематика — LLM (sonnet-5) перефразирует строку J/доставки с сохранением
  цифр, дедуп #365 склейку пропускает → инвариант #92 «дословно» в проде не
  выполняется. НЕ регрессия #274. Развилка (А/Б/В/Г):
  `docs/artifacts/2026-07-23-smoke-verbatim-quote.md`.
- Развилка решена владельцем: **вариант Б** — маркерный quote-режим (строку J и
  строку доставки собирает КОД из листа, LLM видит только метку `[QUOTE]`; дедуп
  #365 снят, #92 «дословно» по построению). Реализация и цена решения:
  `docs/artifacts/2026-07-23-smoke-verbatim-quote-variant-b.md`; смоук
  `run_chain_smoke.py` после фикса — PASS 2/2.

## Смежное (не suggest, но черновики/прайс)

- `booking_draft.py` — карточка брони из диалога (collect_booking, ЭТАП 1: только печать, без отправки).
- `trainer.py` + `lesson_router.py:build_invariant_reject_ack` (1135) — тренажёрный контур: браковка поправок учителя, нарушающих инварианты (напр. «ниже прайса»).
- `delivery.py` — зоны/цены доставки (позиционный формат `[name,lat,lon,price,radius]`, см. CLAUDE.md).
