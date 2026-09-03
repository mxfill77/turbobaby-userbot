# Project operating frame and capacity policy — decision record

**Decision:** use [`docs/PROJECT_OPERATING_FRAME.md`](../PROJECT_OPERATING_FRAME.md) as the coordination frame from 2026-08-31.

## Evidence used

- Current repository contract in `CLAUDE.md`: Claude Code/PC runtime remains `claude-opus-5` at `xhigh`; changing the PC executor is not an environment-only change.
- C1 task definition: `docs/tasks/gpt-codex-shadow-pilot-c1.md` confines the next proposal to a fixture-only, evidence-first manager draft and explicitly forbids runtime, send, write, price, and live actions.
- Current local commit history includes H1 (`6cbdd15`), S1 (`768729b`), C0 acceptance (`fcebdea`), V0 (`93b0ed2`) and V0 hardening (`acba7e5`).  Those commit facts do not establish release or live integration.
- Official OpenAI model guidance checked on 2026-08-31 supports a workload-based Sol/Terra/Luna choice and selective reasoning effort; it does not remove project approval boundaries.  https://developers.openai.com/api/docs/guides/latest-model

## Decision boundaries

The frame changes documentation and coordination only.  It does not modify `.claude/settings.json`, daemon models, code, tests, Git state, remotes, Drive, Bridge, services, live systems, or credentials.

## Claude context check

The relevant existing Claude project is **TurboBaby AI Manager**.  It is already open and visibly configured as `Opus 5 · Max`; a second project chat would duplicate context.  Its visible project frame is consistent with this decision record on evidence-first status, TRAINING/LIVE separation, and the fact that capacity never overrides an approval gate.  No Claude message was sent in this check.

## Outstanding owner decision

For C1, provide only the five named architecture sections from the canonical KB in a bounded/read-safe form, or approve a named canonical substitute.  Afterwards review the C1 `TASK_PACKET` before any implementation.
