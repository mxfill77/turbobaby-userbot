# TASK_PACKET v1 — B1.2 live read-only price candidate

```yaml
task_id: b1-2-live-read-dry-run
owner: Filipp
architect: Manus
executor: Claude Code
mode: code_green_plus_bridge_get_only
goal: >
  Add a thin read-only adapter that obtains the current fleet and quote facts through the
  existing Bridge GET path, converts them into the explicit fixture accepted by
  price_snapshot_publish.py, and performs exactly one live dry-run candidate build.
status_semantics: executor_returns_reported_only; Manus_verifies_afterwards
```

## Allowed scope

Claude Code may read `CLAUDE.md`, `price_snapshot_publish.py`, `price_source.json`, `pricing.py`, `price_freshness_run.py`, their tests, and the minimum directly imported helpers needed to understand the existing GET path.

It may create or modify only:

1. `price_snapshot_collect.py` — thin adapter; GET-only; no publish path;
2. `test_price_snapshot_collect.py` — injected fake-GET tests;
3. `docs/artifacts/2026-08-31-b1-2-result.json` — final machine-readable executor report.

The final live candidate and human diff report must be written under `_scratch_b1_2/`, which is evidence workspace only and must remain untracked.

## Mandatory reuse

Use the existing `pricing.fleet_status` / `pricing.quote` Bridge GET road and the pure `price_snapshot_publish.build_candidate` / `verify_candidate` functions. Do not add an HTTP client, endpoint, queue, daemon, scheduler, pricing formula, model registry or secret reader.

Use the existing `base.models` keys as the complete product set. `XMAX 300 / 2020-2022` and `XMAX 300 / 2023-` are separate products. Generation must be derived from the already recorded `sheet_model`/`unit_marker` and actual live unit name; unknown or ambiguous generation is a hard failure. Do not guess.

The reference window is exactly seven days and must be explicit in the output fixture. Collect all matching fleet units for every product; if two units of one product return different day prices, do not average or select one — fail the candidate. Availability, deposit and cap are evidence only and must not be copied into low-season base.

## Forbidden

Despite any general preamble, this task must **not** commit, push, pull, fetch, checkout, reset, amend, merge or rebase. It must not change `price_source.json`, `price_source.py`, `price_gate.py`, `price_freshness.py`, `pricing.py`, `suggest.py`, `.env`, `.claude`, Google Sheet, CRM, Drive, Telegram, Bridge data, VPS, services or processes. No POST is allowed. No restart, deployment, client message, booking, payment, contract, price publication or approval simulation is allowed.

If the adapter cannot prove that it is using only GET `fleet` and GET `quote_price`, stop with `unknown`; do not improvise.

## Required tests

Injected fake-GET tests must prove: no POST surface exists; Bridge/fleet failure returns unknown and produces no candidate; unknown product blocks; missing product blocks; XMAX generations remain separate; unit disagreement blocks; output cannot target `price_source.json`; and the existing runtime file is byte-identical before/after.

Run:

```text
venv\Scripts\python.exe -W error::ResourceWarning -m unittest -v test_price_snapshot_collect test_price_snapshot_publish
venv\Scripts\python.exe -m unittest -q test_price_snapshot_collect test_price_snapshot_publish test_price_source test_price_gate test_price_freshness test_pricing test_suggest test_moderation test_moderation_card
```

Expected full-gate baseline is `5 failures`, all and only the known NMAX `TestRunLiveSmoke` cases. Any additional failure is a task failure.

## Live dry-run

After injected tests pass, perform one live GET-only run through the existing Bridge. Save:

```text
_scratch_b1_2/live_fixture.json
_scratch_b1_2/live_candidate.json
_scratch_b1_2/live_report.json
```

Record before/after `git hash-object price_source.json`; hashes must be identical. Do not publish the candidate.

## RESULT_PACKET v1

Write `docs/artifacts/2026-08-31-b1-2-result.json` with exactly these top-level fields:

```json
{
  "schema": "turbobaby/result_packet/v1",
  "task_id": "b1-2-live-read-dry-run",
  "status": "reported_done|reported_failed|unknown|needs_owner_decision",
  "changed_files": [],
  "tests": [{"command": "", "ran": 0, "failures": 0, "known_failures": []}],
  "source_blob_before": "",
  "source_blob_after": "",
  "bridge_actions_observed": [],
  "candidate_path": "",
  "candidate_sha256": "",
  "report_path": "",
  "unknowns": [],
  "approval_needed": [],
  "summary": ""
}
```

The packet is executor-reported evidence, not verification. Do not label it `verified` or `released`.

Start stdout with `FACT: reported B1.2 execution; independent verification pending`. Finish stdout with one line `RESULT: <status and result packet path>`.
