# TASK_PACKET v1 — B1.2a candidate metadata coherence

```yaml
task_id: b1-2a-candidate-metadata-coherence
owner: Filipp
architect: Manus
executor: Claude Code
mode: code_green_fixture_only
verdict_on_b1_2: needs_revision
```

## Verified defect

The live B1.2 candidate passed the current structural verifier, but its metadata contradicts its evidence:

1. `freshness.window` remains the inherited `15.09.2026..16.09.2026`, while `publication.source_window` is the actual B1.2 observation window `2026-09-07..2026-09-14`.
2. Fixture handle evidence says `units_agreed` H3/I3/J3 = `4/3/7`, but both `freshness.handles` and `publication.handles` retain inherited `3/3/3`.

The candidate must not be accepted or published while these fields disagree.

## Allowed files

Modify only:

1. `price_snapshot_publish.py`;
2. `test_price_snapshot_publish.py`;
3. `test_price_snapshot_collect.py`;
4. create `docs/artifacts/2026-08-31-b1-2a-result.json`.

Do not commit or push.

## Required behavior

`build_candidate` must copy the actual fixture `source_window` into `freshness.window`. Its `freshness.handles` must preserve stable descriptive fields such as `category` from the previous snapshot while replacing observed fields from the fixture, including `global_discount`, `units_agreed` and `taken_from`. `publication.handles` must be an exact deep copy of the resulting `freshness.handles`.

`verify_candidate` must fail closed when `freshness.window != publication.source_window`, when publication/freshness handle blocks differ, or when any required observed handle field is missing. Do not change price calculation, model rows or hash canonicalization beyond the metadata now correctly included in the hash.

## Tests

Add positive and negative locks for both mismatch classes. Regenerate the live candidate locally from the existing `_scratch_b1_2/live_fixture.json` only; do **not** call Bridge again. Verify that:

- `freshness.window == publication.source_window == fixture.source_window`;
- H3/I3/J3 `units_agreed` equals `4/3/7`;
- source `price_source.json` blob is unchanged;
- the corrected candidate verifies with a new canonical hash;
- the same five known NMAX `TestRunLiveSmoke` failures remain the only full-gate red.

Run:

```text
venv\Scripts\python.exe -W error::ResourceWarning -m unittest -v test_price_snapshot_publish test_price_snapshot_collect
venv\Scripts\python.exe -m unittest -q test_price_snapshot_collect test_price_snapshot_publish test_price_source test_price_gate test_price_freshness test_pricing test_suggest test_moderation test_moderation_card
```

## Forbidden

No Bridge/Telegram/Drive/Sheet/VPS/network call. No change to `price_source.json`, prices, `pricing.py`, runtime, service, process or production. No commit, push, pull, fetch, checkout, reset, restart, deployment or publish.

## RESULT_PACKET

Write `docs/artifacts/2026-08-31-b1-2a-result.json` using `turbobaby/result_packet/v1`. Include `changed_files`, both test commands and counts, source blob before/after, old candidate hash, corrected candidate hash, corrected candidate path, `unknowns`, `approval_needed`, and concise `summary`. Status is executor-reported only.

Start stdout with `FACT: reported B1.2a correction; independent verification pending`. Finish with `RESULT: <status and result packet path>`.
