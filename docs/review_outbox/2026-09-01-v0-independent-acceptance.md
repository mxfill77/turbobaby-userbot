# ПАКЕТ НЕЗАВИСИМОЙ ПРИЁМКИ V0 — случай `h1-v0-fixture-shadow-20260831`

**Всё, что нужно для приёмки, лежит В ЭТОМ ТЕКСТЕ.** Тебе не нужен доступ
к файловой системе, к репозиторию, к сети и к каким-либо внешним объектам:
восемь файлов случая описаны манифестом, пять из них приведены дословно.
Если чего-то не хватает — это ответ «ОТКЛОНЕНО», а не повод искать снаружи.

## 1. Почему пакет выглядит именно так (две прошлые причины отказа)

Этот случай уже дважды не дошёл до содержательной приёмки, и оба раза —
не по содержанию:

1. **Доступ.** Предыдущий ревьюер получил ССЫЛКИ на файлы рабочего каталога,
   а его среда исполнения untracked-файлы рабочего дерева не видит. Он не смог
   открыть обязательный `TASK_PACKET.json` и ответил `REJECT` ДО проверки.
   Здесь ссылок нет: содержимое приехало текстом.
2. **Один абсолютный путь.** В `bundle.json` есть поле `workspace_root` со значением-путём
   машины-производителя. Контракт запрещает выпускать такой пакет наружу.
   Здесь это поле — и только оно — заменено литералом `<WORKSPACE_ROOT>`; см. §4.

## 2. Манифест (канонический JSON — от ЭТИХ байт взят хеш манифеста)

```json
{
 "captured_at_utc": "2026-09-01T11:31:45Z",
 "case_id": "h1-v0-fixture-shadow-20260831",
 "file_count": 8,
 "files": [
  {
   "byte_count": 475,
   "relative_path": "TASK_PACKET.json",
   "sha256": "309a509c5bf9e68c7bf7f312ece4833fbfaeb79259b9cfc4ab07f00f2ba3bc77"
  },
  {
   "byte_count": 1915,
   "relative_path": "bundle.json",
   "sha256": "a4919a4c827a8b1fa2059f339e504040a2c85ccb4f496a4b97214e1450a52356"
  },
  {
   "byte_count": 48,
   "relative_path": "candidate_result.json",
   "sha256": "4239934777e954cd5edf112be1b5fd125cb8307f76c456692bdc42d12ec9cdb6"
  },
  {
   "byte_count": 117,
   "relative_path": "h1_test_receipt.json",
   "sha256": "10f4d6370723753e7202c0acffe5de9291f793e1ff886ff11503c49384989c3e"
  },
  {
   "byte_count": 1839,
   "relative_path": "V0_RESULT_PACKET.json",
   "sha256": "36ad2fba8c2d3e86afece2d54c787bd54f10d49b0495422f9937ad8eb6b8dae3"
  },
  {
   "byte_count": 10105,
   "relative_path": "artifacts/h1_result.json",
   "sha256": "787150375a8b946815ccc271b14b0636a3ce7c2db6de970044804ecc7fdb2077"
  },
  {
   "byte_count": 18649,
   "relative_path": "candidate/hq_context_pack.py",
   "sha256": "93a8a0f1ac46572d0c22dbb243a4f19d92d84c8f428a6caf54b77cee6abe1148"
  },
  {
   "byte_count": 19185,
   "relative_path": "candidate/test_hq_context_pack.py",
   "sha256": "cbce6e5885db8285e0e5560f5aac5fdf394208dfc8ffc02e9e0b0807de80b307"
  }
 ],
 "path_form": "relative_to_case_root",
 "schema": "turbobaby.v0_acceptance_manifest/v1"
}
```

`manifest_sha256` = `04f761163a83ffca4ac49952731ca351dec1588ab69bab7a88c5b560c651c85b`

Считан так: `sha256` от UTF-8 байт того же объекта в канонической форме
(`sort_keys=True`, разделители `,`/`:`, без пробелов и переводов строк) —
то есть от текста выше, приведённого к одной строке. Отступы в блоке — для
чтения; хеш взят от канонической формы, а не от отформатированного вида.

## 3. Что означают поля манифеста

* `relative_path` — путь ВНУТРИ случая. Корня у случая в пакете нет намеренно:
  он назвал бы машину владельца, а приёмке не нужен.
* `sha256` — от ИСХОДНЫХ байт файла, до любой нормализации.
* `byte_count` — размер исходного файла.
* `captured_at_utc` — время снятия хешей (UTC, ISO-8601). Хеши сняты одним
  проходом, файлы между снятием и сборкой не менялись.

## 4. Единственная нормализация — названа поимённо

В `bundle.json` заменено значение ОДНОГО поля `workspace_root` на литерал `<WORKSPACE_ROOT>`.
Больше в пакете не изменено ничего: остальные семь файлов приведены
или прохешированы в исходном виде.

* sha256 ИСХОДНОГО `bundle.json` (он же в манифесте): `a4919a4c827a8b1fa2059f339e504040a2c85ccb4f496a4b97214e1450a52356`
* sha256 ПОКАЗАННОГО ниже нормализованного текста: `6a7b23cfbfb8e54df8702bf04dcdfbafc9304a23f5f28402e459ae1e6c6d2346`

Эти два хеша РАЗНЫЕ, и так и должно быть. Проверяемость от этого не страдает:
нормализованный текст сам объявляет шесть хешей других файлов случая (§6), и
все шесть обязаны сойтись с манифестом. Подмена содержимого `bundle.json`
сверх одного поля разошлась бы с ними немедленно.

Почему заменено, а не вырезано: удаление поля изменило бы форму объекта,
и ревьюер не увидел бы, что поле вообще было. Молчаливое сокрытие хуже
объявленной замены.

## 5. Содержимое пяти управляющих файлов (дословно)

### `TASK_PACKET.json` (475 б, sha256 `309a509c5bf9e68c7bf7f312ece4833fbfaeb79259b9cfc4ab07f00f2ba3bc77`)

```json
{"required_content_gates":[{"artifact_id":"h1_result","gate_id":"H1_REPORTED_DONE","params":{"equals":"reported_done","field":"reported_status"},"type":"json_field_equals"},{"artifact_id":"h1_result","gate_id":"H1_TESTS_31_OK","params":{"equals":31,"field":"test_summary.ok"},"type":"json_field_equals"},{"artifact_id":"h1_result","gate_id":"H1_FIXTURE_ONLY","params":{"equals":true,"field":"scope_compliance.no_network"},"type":"json_field_equals"}],"schema_version":"v0.1"}
```

### `bundle.json` (1915 б, sha256 `a4919a4c827a8b1fa2059f339e504040a2c85ccb4f496a4b97214e1450a52356`)

Показан НОРМАЛИЗОВАННЫЙ текст (см. §4): хеш выше — от исходных байт,
хеш показанного текста — в §4. Расхождение объявлено, а не спрятано.

```json
{"allowed_changed_paths":["candidate/hq_context_pack.py","candidate/test_hq_context_pack.py"],"baseline_manifest":[{"path":"candidate/hq_context_pack.py","sha256":null},{"path":"candidate/test_hq_context_pack.py","sha256":null}],"candidate_manifest":[{"max_bytes":200000,"path":"candidate/hq_context_pack.py","sha256":"93a8a0f1ac46572d0c22dbb243a4f19d92d84c8f428a6caf54b77cee6abe1148"},{"max_bytes":200000,"path":"candidate/test_hq_context_pack.py","sha256":"cbce6e5885db8285e0e5560f5aac5fdf394208dfc8ffc02e9e0b0807de80b307"}],"case_id":"h1-v0-fixture-shadow-20260831","content_gates":[{"artifact_id":"h1_result","gate_id":"H1_REPORTED_DONE","params":{"equals":"reported_done","field":"reported_status"},"type":"json_field_equals"},{"artifact_id":"h1_result","gate_id":"H1_TESTS_31_OK","params":{"equals":31,"field":"test_summary.ok"},"type":"json_field_equals"},{"artifact_id":"h1_result","gate_id":"H1_FIXTURE_ONLY","params":{"equals":true,"field":"scope_compliance.no_network"},"type":"json_field_equals"}],"required_artifacts":[{"artifact_id":"h1_result","content_type":"json","max_bytes":200000,"path":"artifacts/h1_result.json","required":true,"sha256":"787150375a8b946815ccc271b14b0636a3ce7c2db6de970044804ecc7fdb2077"}],"result_packet":{"content_type":"json","path":"candidate_result.json","sha256":"4239934777e954cd5edf112be1b5fd125cb8307f76c456692bdc42d12ec9cdb6"},"schema_version":"v0.1","task_packet":{"content_type":"json","path":"TASK_PACKET.json","sha256":"309a509c5bf9e68c7bf7f312ece4833fbfaeb79259b9cfc4ab07f00f2ba3bc77"},"test_evidence":[{"declared_command_id":"recorded-h1-fixture-unittest","exit_code":0,"expected_summary":{"errors":0,"failed":0,"passed":31},"stdout_path":"h1_test_receipt.json","stdout_sha256":"10f4d6370723753e7202c0acffe5de9291f793e1ff886ff11503c49384989c3e","test_id":"h1_hq_context_pack"}],"workspace_root":"<WORKSPACE_ROOT>"}
```

### `candidate_result.json` (48 б, sha256 `4239934777e954cd5edf112be1b5fd125cb8307f76c456692bdc42d12ec9cdb6`)

```json
{"reported_claim":"reported_done","unknowns":[]}
```

### `h1_test_receipt.json` (117 б, sha256 `10f4d6370723753e7202c0acffe5de9291f793e1ff886ff11503c49384989c3e`)

```json
{"errors":0,"exit_code":0,"failed":0,"passed":31,"source":"recorded_h1_result_packet","test_id":"h1_hq_context_pack"}
```

### `V0_RESULT_PACKET.json` (1839 б, sha256 `36ad2fba8c2d3e86afece2d54c787bd54f10d49b0495422f9937ad8eb6b8dae3`)

```json
{"case_id":"h1-v0-fixture-shadow-20260831","forbidden_actions_observed":[],"gates":[{"evidence_refs":["TASK_PACKET.json","candidate_result.json"],"gate_id":"V0_SCHEMA","reason_code":"ok","status":"PASS"},{"evidence_refs":["candidate/hq_context_pack.py","candidate/test_hq_context_pack.py"],"gate_id":"V0_SCOPE_EXACT","reason_code":"ok","status":"PASS"},{"evidence_refs":["candidate/hq_context_pack.py","candidate/test_hq_context_pack.py"],"gate_id":"V0_HASH_INTEGRITY","reason_code":"ok","status":"PASS"},{"evidence_refs":["TASK_PACKET.json"],"gate_id":"V0_TASK_BINDING","reason_code":"task_declares_content_gates","status":"PASS"},{"evidence_refs":["h1_result"],"gate_id":"V0_ARTIFACT_READBACK","reason_code":"ok","status":"PASS"},{"evidence_refs":["h1_result"],"gate_id":"H1_REPORTED_DONE","reason_code":"ok","status":"PASS"},{"evidence_refs":["h1_result"],"gate_id":"H1_TESTS_31_OK","reason_code":"ok","status":"PASS"},{"evidence_refs":["h1_result"],"gate_id":"H1_FIXTURE_ONLY","reason_code":"ok","status":"PASS"},{"evidence_refs":[],"gate_id":"V0_TEST_EVIDENCE","reason_code":"ok","status":"PASS"},{"evidence_refs":[],"gate_id":"V0_STATUS_COHERENCE","reason_code":"ok","status":"PASS"},{"evidence_refs":[],"gate_id":"V0_DETERMINISM","reason_code":"canonical_json","status":"PASS"}],"input_refs":{"candidate_manifest_sha256":"67799da292f40b5e4f1efad6ae5557b8dadc09e8dd6e4949e5295cb77ba047c3","result_packet_sha256":"4239934777e954cd5edf112be1b5fd125cb8307f76c456692bdc42d12ec9cdb6","task_packet_sha256":"309a509c5bf9e68c7bf7f312ece4833fbfaeb79259b9cfc4ab07f00f2ba3bc77"},"next_action":"none","reason_code":"all_gates_passed","reported_claim":"reported_done","schema_version":"turbobaby.content_product_verifier/v0.1","unknowns":[],"verdict":"PROVEN","verified_scope":["candidate/hq_context_pack.py","candidate/test_hq_context_pack.py"]}
```

## 6. Перекрёстная сверка — то, что ты можешь проверить САМ, сравнением строк

Шесть из восьми хешей манифеста подтверждены ИЗНУТРИ самого случая: их
объявляют `bundle.json` и `V0_RESULT_PACKET.json`, приведённые выше дословно.
Это не моё слово: это сравнение двух строк в двух разных файлах пакета.

| # | файл манифеста | хеш в манифесте | где ещё объявлен, дословно |
|---|---|---|---|
| 1 | `candidate/hq_context_pack.py` | `93a8a0f1ac46…` | `bundle.json` → `candidate_manifest[0].sha256` |
| 2 | `candidate/test_hq_context_pack.py` | `cbce6e5885db…` | `bundle.json` → `candidate_manifest[1].sha256` |
| 3 | `artifacts/h1_result.json` | `787150375a8b…` | `bundle.json` → `required_artifacts[0].sha256` |
| 4 | `candidate_result.json` | `4239934777e9…` | `bundle.json` → `result_packet.sha256` И `V0_RESULT_PACKET.json` → `input_refs.result_packet_sha256` |
| 5 | `TASK_PACKET.json` | `309a509c5bf9…` | `bundle.json` → `task_packet.sha256` И `V0_RESULT_PACKET.json` → `input_refs.task_packet_sha256` |
| 6 | `h1_test_receipt.json` | `10f4d6370723…` | `bundle.json` → `test_evidence[0].stdout_sha256` |

**Оставшиеся два хеша изнутри случая НЕ подтверждаются, и это надо знать:**

* `bundle.json` (`a4919a4c827a…`) — верхнее объявление, само себя не хеширует.
* `V0_RESULT_PACKET.json` (`36ad2fba8c2d…`) — вывод прибора. Его хеш записан ВНЕ случая:
  в отчёте о прогоне от 31.08.2026 стоит `36ad2fba8c2d3e86afece2d54c787bd54f10d49b0495422f9937ad8eb6b8dae3`.
  Это внешняя привязка по времени, но её источник — тот же производитель,
  и независимой она не является. Считай её слабой уликой, а не доказательством.

Ещё три сверки, тоже сравнением строк, без всяких хешей:

* три гейта из `TASK_PACKET.json` → `required_content_gates` обязаны совпадать
  с `bundle.json` → `content_gates` (идентификаторы `H1_REPORTED_DONE`,
  `H1_TESTS_31_OK`, `H1_FIXTURE_ONLY`) и все три обязаны стоять в списке гейтов
  `V0_RESULT_PACKET.json`;
* `verified_scope` в `V0_RESULT_PACKET.json` обязан совпадать с
  `allowed_changed_paths` в `bundle.json` и быть НЕ пустым;
* `case_id` обязан быть один и тот же в манифесте, в `bundle.json` и в
  `V0_RESULT_PACKET.json`.

## 7. Чего в пакете НЕТ и что он НЕ доказывает

* **Кандидатский код не приложен** (два файла, 37 834 б): по контракту приёмки
  ревьюер код не запускает. От них в пакете хеш и размер.
* **`artifacts/h1_result.json` (10 105 б) приложен только хешем.** Значит три
  содержательных гейта (`reported_status`, `test_summary.ok == 31`,
  `scope_compliance.no_network`) ты проверить по тексту НЕ можешь — только то,
  что они объявлены и что прибор назвал их пройденными. Это слепое пятно
  пакета, названное вслух.
* **Расписка тестов — не прогон.** `h1_test_receipt.json` составлен из уже
  записанной сводки прошлого прогона, а не снят заново.
* **`PROVEN` здесь означает узкое.** Что прибор детерминированно проверил
  заранее ограниченный набор фикстур — и ничего о пользе кандидата, о живом
  контуре, о клиентах и о поведении в бою.
* **`baseline_manifest` объявлен исполнителем** (обе `sha256` — `null`), то есть
  «что было до» прибор знает со слов проверяемого.

## ТРИ ВОПРОСА

1. **ПРИНИМАЕШЬ ли ты этот пакет как читаемый и внутренне непротиворечивый?**
   Ответ ровно одним словом в первой строке: `ПРИНЯТО` или `ОТКЛОНЕНО`. Проверь
   перед этим: (а) в манифесте ровно восемь позиций и это те же восемь файлов,
   что перечислены в §2; (б) ни в одной строке пакета нет абсолютного пути;
   (в) ВСЕ шесть перекрёстных сверок §6 сходятся ЗНАК В ЗНАК; (г) три сверки без
   хешей тоже сходятся. Любое расхождение — `ОТКЛОНЕНО`, и назови, какое именно:
   файл, поле, ожидаемое и найденное значение.
2. **Что этот пакет НЕ доказывает такого, что читатель может ошибочно счесть
   доказанным?** Назови не больше трёх пунктов, каждый — со ссылкой на то место
   пакета, из которого это видно.
3. **Где дыра в самом устройстве этой приёмки?** Что можно подложить в такой
   пакет, чтобы получить от тебя `ПРИНЯТО` незаслуженно, — и какой ОДНОЙ
   добавкой в пакет эта дыра закрывается?

Чего не видно из текста — говори «неизвестно». «Выглядит нормально» ответом
на вопрос 1 не является: нужен один из двух вердиктов и разбор.
