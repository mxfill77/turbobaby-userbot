# Одометр ТО: текущий жёсткий блок убывания → план мягкого гейта (ЭТАП 1, read-only)

Дата: 2026-07-24. Код живёт в VPS-репо `turbobaby-manager-bot` (клон на ПК:
`D:\foreign\turbobaby-manager-bot`, HEAD 7af280c). В ПК-репо `turbobaby-bot` логики
одометра НЕТ — все правки будущего этапа = задачи VPS-контура (+ Bridge GAS).

## 1) Где убывание блокирует запись (файл:строка, дословно)

Три независимых слоя.

### Слой 1 — «сторож B» (фото-OCR), splinter.py

`_ask_mileage_confirm`, splinter.py:2520-2532 — floor и отказ вместо кнопки:

```python
    if new_km is not None:
        prev = last_mileage_in_topic(chat_id, topic_id, exclude_km=new_km)
        if prev and new_km < prev[0]:
            floor = prev[0]

    _PENDING_MILEAGE[(chat_id, topic_id)] = (str(mileage), bike or "", floor, bool(oil_hint))
    mark_awaiting(chat_id, topic_id)

    if floor is not None:
        await _send(context, chat_id=chat_id,
                    text=msg_mileage_drop(bike, new_km, floor),
                    message_thread_id=topic_id)
        return
```

`msg_mileage_drop`, splinter.py:2493-2495 (докстринг = правило):

```python
def msg_mileage_drop(bike, new_km, last_km):
    """Фикс B (сторож): распознанный пробег МЕНЬШЕ последнего известного — физически
    невозможно (одометр не убывает). Просим правильное число / переснять. В ТО НЕ пишем."""
```

`handle_mileage_confirm`, splinter.py:2582-2592 — «да»/число ниже floor не принимаются:

```python
    # Сторож (фикс B): не подтверждаем «да» и не принимаем число НИЖЕ последнего известного —
    # пробег не убывает. Держим pending (floor сохраняется), ждём корректную цифру/фото.
    if floor is not None:
        try:
            if int(str(num).replace(" ", "").replace(",", "")) < floor:
                await _send(context, chat_id=msg.chat_id,
                            text=msg_mileage_drop(bike, num, floor),
                            message_thread_id=key[1])
                return True
```

### Слой 2 — «сторож Б» (класс H): правка вниз только ВЛАДЕЛЕЦ, splinter.py

`handle_correction_confirm`, splinter.py:2656-2664:

```python
    if t in _CONFIRM_YES:
        # Класс H (сторож Б): только ВЛАДЕЛЕЦ подтверждает снижение одометра.
        # Пым или посторонний «да» — НЕ применяем (pending остаётся, ждём владельца).
        if not _is_owner(msg):
            uname = getattr(getattr(msg, "from_user", None), "username", None) or "?"
            log.warning(
                f"  🔒 сторож Б: confirm не от владельца (@{uname}) тема={topic_id} — pending сохранён"
            )
            return False
```

Кнопка [✅ Да], splinter.py:3420-3423:

```python
        if not is_owner_user(q.from_user):
            await _btn_answer(q, "Снижение пробега — только владелец / Owner confirms km decrease",
                              show_alert=True)
            log.warning(f"  🔒 сторож Б: fix-btn не от владельца (@{uname}) тема={topic_id} — кнопка живёт")
```

Единый гейт-токен `_odoguard_authorize`/`_odoguard_check`, splinter.py:2377-2418
(TTL 300 с, km должен совпасть, fail-safe: исключение → allow). `_apply_correction`,
splinter.py:2691-2695 — без авторизации блок:

```python
    if not _odoguard_check(chat_id, topic_id, new_km, caller="_apply_correction"):
        log.warning(
            f"  🔒 _apply_correction ЗАБЛОКИРОВАНА сторожем Б: {old_km}→{new_km} тема={topic_id}"
        )
        return
```

### Слой 3 — серверные сторожа Bridge (GAS, исходник /root/turbobaby-bridge-gs на VPS, в клоне его НЕТ)

- `set_fleet_oil` → err `oil_decreasing` (bridge_client.py:761-770: «Откат (новое<старого) …
  отказ без записи»); показ отказа: splinter.py:3176-3178, 3237-3239.
- `set_fleet_service` (кол.J/K/L) → err `km_decreasing` (bridge_client.py:771-778); показ:
  splinter.py:3325-3327 «новое {km_int} меньше прошлого {res.get('old_km')} — не записал».
- Двухфазный ТО намеренно на него опирается — splinter.py:5114-5115: «ШАГ 5 (КРАСНЫЙ): …
  Сторож km_decreasing НЕ трогаем (он на стороне set_fleet_*)».
- Закрытие аренды `close_booking` → err `odometer_back` при km_end < Q (bridge_client.py:622-624;
  живой гейт в /root/turbobaby-bridge-gs/Booking.js, голден-харнесс
  tests/booking_gs_harness.js:271-277). Fail-closed: `km_required` (нет km_end) и
  `odo_unverifiable` (Q пуст/нечисловой) — splinter.py:6130-6148, ветка прочих ошибок
  6149-6156 («❌ Закрытие не прошло… Статус в CRM не менял — заверши руками»).

## 2) Текущее правило целиком

- **Что считается убыванием**: фото — распознанное число < последнего известного пробега темы
  (`last_mileage_in_topic`, splinter.py:946-983: буфер `_RECENT_PHOTOS`, TTL
  `MILEAGE_TTL_DAYS`=7 дней, приоритет самому свежему high-confidence). Колонки I/J/K/L —
  новое < текущего значения ячейки (сервер). Закрытие аренды — km_end < Q строки CRM.
- **Что происходит**: фото-убывание — флаг «похоже на ошибку распознавания», в ТО НЕ пишем,
  pending с floor держится до корректной цифры; «да» не помогает. Текстовая коррекция вниз —
  переспрос, применяется ТОЛЬКО по «да»/кнопке владельца (Пым не может), авторизация живёт
  5 минут. Серверные записи — отказ без записи (oil_decreasing/km_decreasing/odometer_back),
  закрытие аренды при неувязке — «заверши руками».
- **Аудит-след**: только процесс-лог splinter.log (log.warning «🔒 сторож Б БЛОК…»,
  log.info «🔑 авторизован … source=…», «коррекция ПРИМЕНЕНА») + сообщения в теме Telegram.
  Отдельного журнала правок одометра в листах НЕТ.

## 3) Путь OCR приборки

1. Фото в сервис-теме → `_download_photo` → `claude.vision(VISION_BIKE_SYSTEM, img)`,
   splinter.py:5313; промпт splinter.py:482-505 (ODO цифра-за-цифрой, `mileage_confidence`
   high/low, «Лучше null, чем выдумка»).
2. Разбор кладётся в буфер темы `_remember_recent_photo` (5317), альбом агрегируется (5319).
3. splinter.py:5505-5509 — **шаг подтверждения человеком ЕСТЬ**:

```python
    if vis.get("mileage") and not parsed.get("mileage") and _conf_ok:
        # Пробег с ФОТО приборки → СНАЧАЛА подтверждаем цифру (vision врёт на LCD) + сторож B,
        # затем _after_mileage решит: спросить кнопками или просто квитанция.
        await _ask_mileage_confirm(context, chat_id, topic_id, bike,
                                   str(vis.get("mileage")), oil_hint=oil_hint)
```

   Подтверждение: кнопка [✅ ใช่/Да] или текст «да»/правильное число
   (`handle_mileage_confirm`, 2555-2612). Запись в ТО (`_run_service_tracker` →
   `bridge.service_upsert`, 2992-3014) — только ПОСЛЕ подтверждения и сторожа B.
   conf=low в запись не попадает (`_km_conf_ok`, 5394-5395; 5581-5587 — просим переснять).
   Пробег ТЕКСТОМ (человек руками) идёт сразу в `_after_mileage` без переспроса (5510-5513).
4. Нюанс: `bridge.add_event(... mileage=str(mileage) ...)` (5423-5428) пишет событие с СЫРОЙ
   (ещё не подтверждённой) цифрой vision в лист «события» — событийный лог, не ТО-регистр.

## 4) Аудит «было/стало/кто/фото»

- **Успешная правка вниз**: Telegram «✅ Пробег исправлен: {old} → {new}» (splinter.py:2706-2710),
  log.info c old→new (2711), source `btn:@user`/`text:@user` в `_odoguard_authorize` (2387,
  2671, 3435). В листы уходит только НОВОЕ значение (`service_upsert`); строки-истории
  «было/стало/кто/фото» НЕТ (`_apply_correction` не зовёт `add_event`).
- **При отказе**: НЕ пишется никуда, кроме процесс-лога (log.warning 2401/2405/2409,
  2661-2663, 3423, 6153) и сообщения/алерта в теме. Лист «события» отказ не видит.
- Ближайший аналог аудита — `bridge.add_event` (msg_date, group, bike, event_type, fuel,
  mileage, photos, notes, msg_id, sender): пишется на фото/событие (5423), на repair-строки
  (5158-…, 1912), но НЕ на правку одометра и НЕ на отказ.

## 5) Существующие тесты (VPS-репо tests/)

- `test_class_h_guard.py` — сторож Б: обход без авторизации блокируется (голден 15.07),
  авторизованная правка проходит, протухший токен, km-mismatch, fail-safe исключения,
  owner-only «да» текстом и кнопкой, warning-лог при блоке (49-276).
- `test_fix_btn_log.py` — лог тапа кнопки ДО `_apply_correction`; bike и km в логе.
- `test_mileage_ttl.py` — TTL `last_mileage_in_topic` (класс C): свежая/старая/несколько.
- `test_service_pending.py` — двухфазный ТО: intake→заявка, фаза 2, B1 «фото-одометр доводит
  заявку» (210), кнопка Пыма только trusted (146-163), E2b мозг-гейт, E3-валидация.
- `test_oil_backdated.py` — масло задним числом: одометр НЕ трогается, валидация
  N ≤ текущий одометр, `detect_mileage_correction` не путается с масло-нарративами.
- `test_booking_close_gs.py` + `booking_gs_harness.js` — закрытие аренды: `odometer_back`
  (271-277, 324), `km_required`, `odo_unverifiable`, живой формат Q «35000 Km, 20.07.2026».
- `test_return_close.py`:166-170 — сплинтер-ветка «❌ Закрытие не прошло: odometer_back».
- `test_servicing.py`, `test_works.py`, `test_uxd.py`, `test_handover*.py`,
  `test_layer1_disentangle.py` — мокают `_ask_mileage_confirm` (флоу вокруг).
- **Дыра**: прямых тестов фото-сторожа B (floor→`msg_mileage_drop`) нет — floor встречается
  только как `floor: None` (test_service_pending.py:404).

## 6) Точки правки под целевое правило (НЕ применены — ЭТАП 1)

Целевое: убывание ≤500 км — механик подтверждает сам; >500 км ИЛИ второй раз подряд —
эскалация Пым/владелец; при закрытии аренды правка вниз только через Пыма/владельца;
аудит-след всегда.

1. **splinter.py `_ask_mileage_confirm` 2515-2532**: считать `delta = prev - new_km`.
   0 < delta ≤ 500 → НЕ отказ, а обычная confirm-кнопка с пометкой «меньше прошлого на
   {delta} км — подтверди» (механик сам); floor остаётся в токене для аудита. delta > 500 →
   нынешний флаг + карточка эскалации Пыму/владельцу (аналог `_return_close_card`).
2. **splinter.py `handle_mileage_confirm` 2584-2592**: floor-ветку заменить на ту же
   классификацию delta; добавить счётчик подряд-убываний по (chat_id, topic_id)
   (новый dict `_DROP_STREAK` рядом с `_PENDING_MILEAGE`): второе подряд убывание →
   эскалация независимо от delta, счётчик сбрасывается ростом/подтверждением.
3. **splinter.py `handle_correction_confirm` 2656-2664 и fix-btn 3415-3424**: вместо
   безусловного `_is_owner` — порог: ≤500 и первый раз → принимает и механик/доверенный;
   >500 или повтор → как сейчас, только owner (Пым — по решению владельца: цель называет
   «Пым/владелец» — расширить `_is_owner` до `_is_trusted` в эскалационной ветке).
   `_odoguard_authorize/_odoguard_check` 2377-2418 — расширить токен полями role/delta.
4. **Bridge GAS (вне клона, /root/turbobaby-bridge-gs)**: `set_fleet_oil`/`set_fleet_service`
   оставить жёсткими (кол.I/J/K/L — «последнее ТО», их убывание = задним числом, свой флоу)
   ЛИБО добавить параметр санкции `allow_decrease`/`confirmed_by`; проброс —
   bridge_client.py:761-778.
5. **Закрытие аренды**: splinter.py:6149-6156 — ветку `odometer_back` превратить из
   «заверши руками» в карточку эскалации Пыму/владельцу («да» → повтор `close_booking` с
   override); сервер Booking.js — параметр `km_end_override` только по owner-каналу;
   новые кейсы в booking_gs_harness.js. Это ровно целевой пункт «правка вниз при закрытии —
   только через Пыма/владельца».
6. **Аудит всегда**: единый хелпер `_odo_audit(...)` → `bridge.add_event(event_type=
   "odo_correction"|"odo_reject", mileage=new, notes="было {old} → стало {new}, delta -{d},
   кто {sender}, причина {...}", msg_id=фото/сообщения)` — звать из: `_apply_correction`
   (2687), floor-блока (2528-2532), блока сторожа Б (2401-2411 через caller), отказов
   set_fleet_* (3176/3237/3325) и `odometer_back` (6149). Тогда «было/стало/кто/фото»
   живёт в листе «события», а не только в splinter.log.
7. **Тесты**: по правилу-классу CLAUDE.md — голдены на РЕАЛЬНЫХ фразах механика
   (дословное сообщение из живого провала + парафразы RU/TH); новый
   `test_mileage_soft_gate.py`: delta≤500 механик сам, delta>500 эскалация, 2 подряд,
   закрытие через Пыма, аудит-строка при отказе; кейсы харнесса на override закрытия.

Исполнение — VPS-контур (splinter.py + Bridge GAS + tests), ПК-репо не трогается.
