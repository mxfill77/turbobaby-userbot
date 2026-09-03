# H1 → V0 fixture-only shadow case — result

## Исполнение

Один owner-approved disposable run создан в
`C:\Users\mxfill1\AppData\Local\Temp\h1_v0_shadow_8em4wjvh`.

- Состав staging после run: ровно восемь ожидаемых файлов — `TASK_PACKET.json`,
  `bundle.json`, `candidate_result.json`, `h1_test_receipt.json`,
  `V0_RESULT_PACKET.json`, `artifacts/h1_result.json`,
  `candidate/hq_context_pack.py`, `candidate/test_hq_context_pack.py`.
- Локальный V0 verdict: `PROVEN`, `reason_code=all_gates_passed`.
- SHA-256 `V0_RESULT_PACKET.json`:
  `36ad2fba8c2d3e86afece2d54c787bd54f10d49b0495422f9937ad8eb6b8dae3`.
- H1 candidate code и H1 tests в этом run не запускались. В рабочем репозитории
  H1/V0-файлы не изменялись.

## Независимая приёмка

Manus получил ограничение ровно на эти восемь файлов и не запускал код. Его
verdict — **REJECT**: в его execution environment обязательный staging
`TASK_PACKET.json` по указанному пути недоступен/не найден. Поэтому он не мог
проверить allowlist, declared paths/SHA, V0 output или отсутствие forbidden
actions.

Это не содержательное опровержение V0 и не доказательство его независимого
принятия. Итог этого shadow-case: **local `PROVEN`, independent acceptance
`UNKNOWN`**.

## Stop rule и следующий выбор владельца

Никакой повторный run не запускается автоматически. Отдельным решением можно
либо закрыть этот единичный case как `UNKNOWN`, либо разрешить только новый
review-only transport уже существующих восьми файлов в папку, доступную Manus,
с повторной сверкой SHA-256 и без повторного H1/V0 execution. Это не является
разрешением на remote, live integration, secrets или клиентские данные.

## Review-only transport attempt

Владелец отдельно разрешил copy-only mirror восьми файлов в
`D:\turbobaby-bot\_shadow_review_h1_v0_20260831`. Все восемь destination
SHA-256 совпали с исходной staging. Manus всё равно не увидел обязательный
`TASK_PACKET.json` и повторно вернул `REJECT` до содержательной проверки.

Следовательно, его execution environment не видит текущие untracked файлы
рабочего каталога. Создавать local commit только для доставки evidence не
разрешено этим owner gate и не делается автоматически. Staging и mirror
сохранены без изменений; итог независимой приёмки остаётся `UNKNOWN`.
