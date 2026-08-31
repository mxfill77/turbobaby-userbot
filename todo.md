
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
