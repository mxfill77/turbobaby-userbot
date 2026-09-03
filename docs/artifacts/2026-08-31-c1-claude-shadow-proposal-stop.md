GPT_CODEX_PROPOSAL/v1
task_id: c1-fixture-only-evidence-packet
goal: STOP — подготовка C1 TASK_PACKET не может быть доказательно завершена без пяти обязательных архитектурных источников.
selected_single_flow: unknown; владелец не выбрал один существующий quote-flow, а его каноническая карта недоступна.
facts_already_available: `pricing_advisor.py` и `test_pricing_advisor.py` существуют; C0 RESULT_PACKET сообщает о fixture-only shadow contract (`mode=shadow`, `approval_required=true`, target только null или входной list price) и о пяти известных несвязанных NMAX failures в широком gate.
facts_missing_or_unknown: `PROJECT_CHARTER.md`, `CURRENT_STATE.md`, `EXECUTION_POLICY.md`, `TARGET_ARCHITECTURE.md`, `PRICING_ADVISOR_ARCHITECTURE.md` отсутствуют в локальном рабочем дереве. Точный поиск Drive по четырём именам не нашёл их. В Drive найден только `KB_MASTER`, но он не был прочитан: это не именованный разрешённый C1 source.
existing_source_of_truth: STOP: canonical architecture source is unavailable in the approved read scope.
candidate_module_and_tests: STOP: read-only repository map plus missing architecture constraints required; нельзя уверенно назвать C1 module/test paths.
allowed_files: none for implementation until a new immutable C1 TASK_PACKET names only confirmed paths.
forbidden_actions: no pricing, discount, availability, booking, contract, payment, CRM, Sheets, Drive write, Telegram, Bridge, queue, runtime import, send, source access expansion, Git mutation, test execution, or production claim.
input_contract: STOP; must be defined only after owner supplies the five canonical architecture documents or separately authorizes a bounded canonical substitute and one selected fixture-only quote-flow.
output_contract: after STOP is resolved, one manager-facing draft packet with anonymised evidence_refs, reason_codes, human-review routing, no client text and no write/send capability.
reason_codes_and_stop_conditions: STOP_MISSING_ARCHITECTURE; STOP_UNSELECTED_FLOW; STOP_UNCONFIRMED_CANDIDATE_PATHS; any missing fact or source means human review, never a price proposal.
evidence_refs: only deterministic snapshot/quote/policy IDs and hashes, never contacts, raw dialog text or secret-bearing logs.
required_test_cases: to be specified from the approved flow; at minimum unknown/blocked produces no target, C0 shadow invariants remain intact, determinism, no side effects, and exact allowed-file scope.
baseline_expectation: no source or runtime change; no statement of verified, accepted, released or production readiness.
rollback: not applicable; this proposal made no product change.
questions_for_owner: provide the five named canonical documents, or separately approve a tightly bounded read-only extract from a named canonical source such as `KB_MASTER`; then choose one existing fixture-only quote-flow.
confidence_in_plan: low
