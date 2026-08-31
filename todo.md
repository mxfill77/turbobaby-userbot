
# C0 Independent Acceptance Follow-up

- [x] Reject non-finite numeric inputs (NaN/Inf) in list price, history stats, and owner policy; preserve strict JSON serializability.
      → `_is_finite` gate on every numeric input that can reach the packet or a threshold
      comparison (list price, client budget, all owner knobs, history median, derived delta);
      `to_json` now prints with `allow_nan=False` as a backstop.
- [x] Fail closed for past quote windows and validate history freshness without rejecting the
      approved fresh_as_of field.
      → new `unknown` code `quote_window_already_started` (fully-past window keeps its agreed
      `quote_window_expired`); `fresh_as_of` accepted, echoed, and required to prove freshness
      before history may corroborate.
- [x] Align confidence.level with the approved schema: high|medium|low|blocked; remove
      confidence=none.
      → `CONFIDENCE_LEVELS` whitelist; `blocked` for status=blocked, `low` for status=unknown.
- [x] Add negative tests for NaN/Inf, past dates, stale history, and confidence schema; preserve
      existing known failures without masking them.
      → +32 tests (69 → 101). The five known `test_suggest.TestRunLiveSmoke` NMAX failures are
      untouched and unmasked.
- [x] Re-run independent C0 checks and the existing acceptance gate before any commit.
      → `_scratch_manus_c0_checks.py` re-run clean; gate 1069 tests / 5 known failures.
      Independently re-run by Manus: C0 101/101 OK; full gate 1069 with exactly 5 known failures; probe clean. NOT committed, NOT pushed.
- [x] Keep C0 fixture-only: no runtime, Bridge, CRM, Telegram, Sheets, client messages, price
      writes, or autonomous publishing.
      → no module outside `test_pricing_advisor.py` imports `pricing_advisor`.

Источник: verified-not-yet-accepted findings from Manus independent edge-case probe, 2026-08-31.
Результат исполнителя: `docs/artifacts/2026-08-31-c0-pricing-advisor-result.json`.

---

# Project TODO

- [x] Preserve existing project TODO history if a prior TODO file is present.
      → На момент начала repair прежнего `todo.md` в рабочем дереве не было; файл создан как новая acceptance-история.

---

# Next Operating Model — model routing and client contour

- [x] Fix the operating roles: Manus = architecture/acceptance/release gates; Claude Code = implementation/tests; optional local GPT/Codex = bounded shadow architecture only.
- [x] Define a single TASK_PACKET / RESULT_PACKET lifecycle for every worker, with one existing queue and no new orchestrator or parallel queue.
- [x] Define explicit red-zone deny rules for prices, discounts, payments, contracts, CRM writes, client messages, secrets, restarts, and irreversible operations.
- [ ] Select one next change set for the evidence-first client contour; do not start multiple strategic projects.
- [ ] Prepare a compact worker packet and route implementation to Claude Code; Manus must not duplicate coding or test execution.
- [ ] Independently verify the reported result, scope, tests, evidence and release approval before any runtime connection.
- [x] Cancel Kimi 3: no connector, no API key, no worker role and no production access.

Source: owner request to minimize Manus execution while retaining architecture and independent acceptance, 2026-08-31.

# Minimal Control Plane — design-only assessment

- [ ] Compare the proposed Telegram-first control plane against PROJECT_CHARTER, CURRENT_STATE, EXECUTION_POLICY and TARGET_ARCHITECTURE.
- [ ] Define one canonical project-state structure using existing documents and Bridge artifacts; do not create a second task lifecycle or queue.
- [ ] Define progressive-disclosure inputs for Manus, Claude Code and optional Codex; keep business facts and red-zone authority outside model memory.
- [ ] Define Codex only as a review/fallback worker until repeatable read-only and structured-output evidence is proven.
- [ ] Prepare a design-only task packet for the minimal control plane; no production implementation, restart, secrets, client message, price write or CRM action.

# Telegram Intake / Economic Control Plane

- [ ] Map the existing Telegram owner-command path, Bridge queue, pc_agent and pc_orchestrator without reading secrets or customer messages.
- [ ] Define a deterministic intake classifier that keeps routine requests local and routes only explicit architecture/review jobs to Manus/Codex.
- [ ] Define a canonical state and evidence update rule: only verified results may update CURRENT_STATE; no model chat becomes the source of truth.
- [ ] Define task-routing rules for Claude Code, optional Codex and Manus by scope, risk and evidence need; do not decide routing by free-form chat text alone.
- [ ] Keep a single existing queue and lifecycle; prohibit recursive worker spawning, autonomous retries and background AI polling.
- [ ] Prepare and independently verify one code-green implementation packet for the existing Telegram control path, with no client send, price/discount, CRM write, secrets, restart, push or release.
- [ ] Produce a concise external-review report for ChatGPT after the design and verification evidence are complete.

# HQ Claude Code / Brain-Bridge Reconstruction

- [ ] Pause CP0-A and C1 implementation routing until the changing headquarters Claude Code role is reconstructed from facts.
- [ ] Map the HQ Claude Code application, Claude Remote Control, Telegram topics, Bridge/Brain docs, pc_agent, pc_orchestrator and VPS routes in read-only mode.
- [ ] Identify the canonical current state, task intake, task dispatch, approval, evidence and journal writers; distinguish reported facts from unknowns.
- [ ] Compare the reconstructed route to PROJECT_CHARTER, CURRENT_STATE, EXECUTION_POLICY and TARGET_ARCHITECTURE without rewriting the current control plane.
- [ ] Publish a design-only reconciliation and select one next change set only after owner review.

# Claude HQ / Minimal Context Automation

- [x] Cancel the PlotCode branch: the intended HQ interface is the existing Claude application / Claude Code Remote Control.
- [ ] Treat the existing Claude application / Claude Code Remote Control as the candidate HQ interface only; do not make its chat the source of truth.
- [ ] Inventory the exact Claude HQ project/session, local project path, Remote Control boundary and authentication boundary in read-only mode.
- [ ] Define a deterministic context-pack manifest that reads only approved project-state and task/evidence artifacts; exclude secrets, PII, raw chats and runtime logs by default.
- [ ] Define a single handoff from Claude-HQ-approved task packet to the existing Bridge/pc_orchestrator/Claude Code path; prohibit a second queue and automatic worker loops.
- [ ] Define a low-friction owner experience: Telegram or Claude HQ request → prepared draft packet → one explicit approval → execution → evidence card.
- [ ] Prepare a design-only implementation packet only after factual PlotCode capability and boundary evidence exists.

# H1 External Review Amendments

- [ ] Add manifest SHA-256, source hashes and deterministic excerpt range metadata to the H1 evidence contract; do not include volatile timestamps in the canonical pack hash.
- [ ] Specify required versus optional sources: missing/unreadable required source must block; optional omission is allowed only with explicit `omitted[]` reason.
- [ ] Prohibit model summarisation and silent excerpt truncation in H1; the pack must contain only labelled deterministic excerpts and exact bounds.
- [ ] Create a separate blocking backlog item for a content-product verifier: normal `RESULT:` plus exit code must not be treated as verified product evidence.
- [ ] Add an adversarial cross-lane H1 acceptance test: evidence appears in VPS while PC is empty/not_checked or VPS is omitted; H1 must return coverage/unknown and may never assert «следов нет».
- [ ] After 20–30 real HQ cases, measure context-pack size, HQ decision quality, unknown/blocked rate and operator interventions before changing the 12k cap or model settings.

# Claude HQ Verification Loop Reconstruction

- [x] Pause H1 handoff and CP0-A planning until the existing Claude application verification workflow has been reconstructed from the recent TurboBaby.ai project chats.
      → H1 handoff remained paused during the read-only reconstruction; no Telegram/Bridge/runtime change was performed.
- [x] Read recent Claude-HQ TurboBaby.ai chats in authorized read-only mode and map the actual loop: execution anywhere → `проверить результативность` → targeted next prompt.
      → Reviewed the accessible principal HQ threads 11.4, 11.5, 11.6, 12, 13 prelaunch, 13.5 and 15; a full undisclosed chat corpus was not exported and remains out of scope.
- [x] Correlate HQ chat conclusions with Devbot/Telegram/dispatch/Bridge artifacts and committed code; distinguish reported chat claims from independently evidenced outcomes.
      → Correlated against current frame and targeted RC/pc_orchestrator contracts; historic chat claims were kept as reported unless current code/docs corroborated them.
- [x] Identify repeatable prompt patterns, context sources, decision points and all points of manual copy/paste or lost evidence.
      → `frame → pulse → both lane journals → handover → result check → next addressed prompt`; gaps are recorded in `HQ_VERIFICATION_LOOP_GAP_ANALYSIS_2026-08-31.md`.
- [x] Define the smallest improvement that preserves Claude HQ as the verification hub, Brain as canonical memory and existing execution channels.
      → H1 is a pure deterministic context-pack builder only; it does not alter Claude HQ, Telegram, Bridge, the queue or runtime.

# Verified HQ Loop Gaps — H1 Inputs

- [ ] Require `active_objective`, task/lane coverage and checked/not_checked status in every H1 context pack to prevent reviewer-led scope drift and a one-lane false negative.
- [ ] Require a provenance bundle before any absence claim: source, freshness/snapshot time, query/check, expected evidence and declared blind spots.
- [ ] Keep `reported`, `verified`, `released`, `blocked` and `unknown` distinct in H1 output; no HQ self-report or task `done` state may be rendered as verified.
- [ ] Keep exact routing metadata separate from free-text effort/prompt content to prevent route-prefix ambiguity and accidental cross-channel dispatch.
- [ ] Preserve a bounded historical-handover digest, not raw chat replay; record version/hash and source excerpts only.

# H1 Atomic Handoff Recovery

- [ ] Record that browser delivery fragmented the immutable H1 packet into separate HQ chat messages; do not treat any resulting HQ proposal as an H1 executor instruction.
- [ ] Keep H1 fixture-only and local-manifest based; reject the proposed live Brain/Bridge source reader as a separate future H2 scope.
- [ ] Verify one atomic existing executor path before a second H1 submission; do not retry Bridge enqueue or multi-message chat delivery blindly.
- [ ] If no atomic existing executor path is available, return H1 technical `UNKNOWN` with evidence instead of expanding source access or creating a new queue.

# Claude Code CLI Read-only Diagnosis

- [ ] Inspect installed Claude Code CLI version, exact non-interactive flags, permission-mode support, hooks and process boundary without changing `.claude`, `.Codex` or user settings.
- [ ] Reproduce a minimal write-permission preflight only in an isolated disposable folder; do not access the TurboBaby repository or execute code there.
- [ ] Identify why read-only `claude -p` succeeds while approved unattended H1 write/test runs produce no worker, output or files.
- [ ] Provide one evidence-backed recovery path; request separate owner approval before any config, restart, installer, access or production change.

# H1 Autonomous Restricted Run

- [ ] Replace the owner-visible terminal attempt with one non-interactive `dontAsk` execution using path-specific Read/Write/Edit permissions and one exact unittest command.
- [ ] Cap the H1 run to its immutable packet, one executor process and no automatic retry; record timeout or denied tool as `unknown`, not a reason to broaden scope.
- [ ] Independently inspect only the three allowed H1 outputs plus test evidence before updating any project state or considering a follow-up.

# H1 Disposable-Staging Recovery

- [ ] Run H1 only in an isolated disposable staging directory that contains AGENTS.md and the immutable H1 packet, not the existing headquarters repository/configuration.
- [ ] Permit the worker to create only three H1 outputs in staging and execute only the named unittest command; reject any other staged file as an unsuccessful packet.
- [ ] Inspect code, tests, reported evidence and staging scope before copying exactly the three approved outputs into `D:\turbobaby-bot`.
- [ ] Delete the disposable staging directory only after independent acceptance or explicit owner decision; do not treat cleanup as H1 completion.

# H1 Prompt Transport Repair

- [ ] Persist the immutable H1 executor instruction as a UTF-8 staging-only prompt file; do not use a multiline PowerShell here-string in the Claude Code invocation.
- [ ] Invoke exactly one Claude Code worker by piping that file through stdin with the existing path-specific allowlist and `medium` effort; no new model call or auto-retry after it.
- [ ] Treat any denied tool, timeout, empty result or extra staging file as a reported `unknown`; do not change H1 requirements or broaden permissions.

# H1 HQ Try-again Reconciliation

- [ ] Record owner evidence that the HQ chat received a partial packet, was manually retried, and produced a proposal without an explicit execution trigger.
- [ ] Verify H1 execution only from the three allowed outputs, test stdout and worker/task state; a Claude HQ answer, prompt or Try again response is never sufficient evidence.
- [ ] Close the current H1 attempt as `reported_done`, `reported_failed` or `unknown/not_executed` without a fourth implicit rerun.

# H1 Staging Verdict

- [x] Record that the disposable H1 candidate passed 31 staging unittests, but its RESULT_PACKET states `reported_status=unknown` and lacks the packet-required candidate file SHA-256 values.
      → Observed after H1; resolved only by approved H1-E1 / H1-E2 evidence steps.
- [x] Do not copy the H1 staging candidate into `D:\turbobaby-bot` while its reported result is `unknown`, even if its local tests are green.
      → No transfer occurred before the final independent read-back.
- [x] Prepare a separate minimal evidence-completion decision: either permit a single deterministic candidate-hash command in a disposable runner or close H1 unknown; do not amend the builder or broaden source access.
      → H1-E1 was owner-approved and completed; H1-E2 resolved its sole stale-evidence inconsistency.

# H1-E1 Hash Semantics

- [ ] Define the RESULT_PACKET JSON entry in `candidate_file_sha256` as an explicit pre-update input hash; no post-update self-hash of a JSON document is computed or claimed.
- [ ] Keep the two Python candidate hashes as hashes of their unchanged final staging content and record result-artifact pre/post hashes separately in external verification evidence if needed.

# H1-E1 Evidence Consistency Gap

- [x] Record that H1-E1 correctly added the two Python hashes and a structured `pre_update_input_hash` object for the JSON artifact, but retained stale `unknowns` and status rationale saying the hashes were missing.
- [x] Treat `reported_status=reported_done` plus unresolved `unknowns` as internally inconsistent reported evidence; do not promote or transfer H1 candidate files.
- [x] Prepare a minimal E2 decision that may edit only the staging RESULT_PACKET to reconcile stale evidence, or otherwise close H1 as unknown; no H1 code/test/command rerun or source-scope expansion.
      → H1-E2 owner-approved and completed as a JSON-only staging edit.

# H1 Evidence-cleanup Stop Rule

- [x] Execute exactly H1-E2 once, then perform a single independent read-back of the full H1 artifact and issue `PROVEN|DISPROVEN|UNKNOWN`.
      → **PROVEN for fixture-only staging candidate**; see `H1_INDEPENDENT_VERIFICATION_2026-08-31.md`.
- [x] Do not create H1-E3 or later self-repair packets for cosmetic RESULT_PACKET cleanup; log any newly found inconsistency as a separate process-quality defect.

# H1 Approved Local Transfer

- [x] Copy exactly verified `hq_context_pack.py`, `test_hq_context_pack.py`, and `docs/artifacts/2026-08-31-h1-hq-context-pack-result.json` from disposable staging to `D:\turbobaby-bot` under owner approval.
- [x] Verify source and destination SHA-256 equality, H1 unittest success in the working repository and absence of unintended changed product files.
      → Three destination hashes match staging; `python -m unittest test_hq_context_pack -v` returns 31 tests OK; git scope contains the three intended untracked H1 paths only.
- [ ] Keep H1 as local verified code only after transfer; do not commit, push, restart, release or attach it to any live source without separate approval.

# H1 Approved Local Commit

- [ ] Create exactly one local commit under owner approval with only `hq_context_pack.py`, `test_hq_context_pack.py`, `docs/artifacts/2026-08-31-h1-hq-context-pack-result.json`, and `todo.md`.
- [ ] Verify the index contains no other tracked or untracked path before committing; no push, restart, release or external action is authorized.

# GPT/Codex Shadow Pilot / C1

- [x] Define local GPT/Codex as a replaceable shadow architect only: no production access, secrets, red-zone actions, release, or self-verification.
- [ ] Select one C1 technical change set for a single quote-flow evidence packet; keep existing CRM, Sheets, Drive, Telegram, Bridge, PC and VPS.
- [x] Prepare a GPT_CODEX_SHADOW_PILOT packet with input manifest, output schema, stop conditions, and acceptance criteria.
- [ ] Route implementation and all executable tests to Claude Code through the existing handoff channel; do not create a new queue or orchestrator.
- [ ] Verify reported C1 output independently and compare GPT/Codex's proposal against PROJECT_CHARTER, CURRENT_STATE, EXECUTION_POLICY and TARGET_ARCHITECTURE.
- [ ] Keep GPT/Codex in manual local shadow mode until its exact CLI/auth boundary and the owner-approved data policy are confirmed.
- [x] Install the official Codex CLI on Windows and confirm ChatGPT-managed login; run only in an isolated read-only staging directory.
- [x] Record the first Codex shadow result as `STOP/low confidence`: it did not invent a quote-flow or source of truth when the staged files were insufficient.
