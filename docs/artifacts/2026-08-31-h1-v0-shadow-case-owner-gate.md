# H1 → V0 fixture-only shadow case — owner gate

## Факты на 31.08.2026

- H1 (`hq_context_pack.py`) закреплён локальным коммитом `6cbdd155`; его
  31 fixture-only unittest проверен локально. Он не подключён к Claude HQ,
  Brain, Drive, Bridge, очереди или runtime.
- V0 (`content_product_verifier.py`) закреплён коммитами `93b0ed2` и
  `acba7e5`. После исправления четырёх границ независимый статический review
  Manus дал `ACCEPT`; локальный набор H1+V0 дал 49 тестов OK (один
  symlink-тест пропущен из-за прав текущего Windows-аккаунта).
- Redacted scan шести ранее flagged tracked paths уже был проведён без вывода
  значений. Git-history remediation, remote, private remote и credential
  activity остаются отдельными `UNKNOWN` и не входят в этот шаг.

## Рекомендация независимого reviewer

**Маршрут B:** один локальный H1-backed shadow-case, построенный только из
уже безопасных fixture artifacts. Он впервые связывает H1 и V0, но не делает
никакого вывода о живом Claude HQ или клиентском контуре.

## Что именно должно быть отдельно одобрено

Разрешить ровно один disposable local fixture-only run со следующими
границами:

1. В изолированной временной папке создать immutable `TASK_PACKET`, входной
   bundle и один `V0_RESULT_PACKET`.
2. Входы ограничены текущими локальными `hq_context_pack.py`,
   `test_hq_context_pack.py` и H1 RESULT_PACKET, плюс детерминированным JSON
   receipt, составленным только из уже записанной H1 test-summary. Каждый
   файл передаётся V0 по относительному пути и SHA-256.
3. V0 читает эти declared artifacts и возвращает только
   `PROVEN|DISPROVEN|UNKNOWN`. H1 candidate code и H1 tests заново не
   запускаются; V0 не получает CLI-интеграции или доступ к любому живому
   источнику.
4. Никаких изменений в репозитории, Git config/history/remote, `.env`,
   Claude HQ, Brain, Drive, Bridge, Telegram, CRM, Sheets, queue, runtime
   или сервисах. Никакого push, release или restart.
5. После run — один независимый read-only review только TASK_PACKET, bundle,
   V0_RESULT_PACKET и хешей. Любой лишний файл, read error, hash mismatch или
   выход за scope означает `UNKNOWN`/`DISPROVEN`, а не повторный запуск.

## Что этот run докажет и чего не докажет

При `PROVEN` он докажет только, что V0 может детерминированно проверить
заранее ограниченный H1 fixture evidence bundle. Он **не** докажет пользу H1
на реальных HQ cases, экономию контекста, корректность live routing, полноту
V0 для иных evidence formats или любой runtime/live эффект.

## Решение владельца

До отдельного одобрения не создавать staging, TASK_PACKET, bundle или output.
Одобрение должно относиться только к описанному disposable fixture-only run и
не является разрешением на remote, live integration или работу с секретами.
