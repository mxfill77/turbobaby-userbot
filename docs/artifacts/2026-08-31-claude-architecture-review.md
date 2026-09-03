# Claude architecture review — captured recommendations

**Source:** the existing Claude project `TurboBaby AI Manager`, task `Architecture review framework`, submitted 2026-08-31. This is an external review, not a verified runtime fact and not permission to execute work.

## Review result visible in the task

- The intended progression H1 → V0 → C0 → C1 is directionally sound because it keeps the proposed work fixture-only and non-operational. It is incomplete as a release path.
- `V0 independent acceptance` cannot be closed by trust or by rerunning the producer. The reviewer needs an immutable, addressable copy of the exact packet plus a read-back check.
- `approval_required=true` does not by itself prove that the surrounding caller cannot act; C1 needs explicit negative/stop cases for missing or blocked facts.
- The person/lane that assembled a C1 packet must not be its accepting reviewer.
- The C1 draft recipient must be named. Until it is explicitly limited to the owner, a manager-visible draft is a higher-risk, separately approved exception rather than a detail of the packet.
- Whether the quote-flow is a synthetic fixture or a snapshot of a live case, whether C1 depends on live H1, and how an evidence reference may be resolved without re-identifying a client all remain `UNKNOWN`.
- A request to prove absence has three possible outcomes (`found`, `absent`, `unknown`) and is at least a medium task, never a simple one.
- Design of negative tests or a change to the observation/contract is complex work and remains in the architecture lane; only a post-spec mechanical implementation may be simple.

## Recommended staged route

| Gate | Deliverable only | Task class / suggested capacity | No-go boundary |
|---|---|---|---|
| G1 | decision/packet for an addressable V0 review route plus read-back criterion | medium; Manus 1.6 / Codex medium | no automatic Git/remote/package transfer |
| G2 | design of two negative C1 stop cases | complex; architecture review at Opus 5 xhigh/Max | no implementation |
| G4/G5 | C1 packet schema, anonymisation rules and result-address rule | complex design; simple only for a later approved mechanical implementation | no live data or send/write |
| C1-spec | single owner-reviewable specification | complex; architecture lane | no code/test/run |
| C1-run | one owner-approved fixture-only run with named-file comparison | medium | separate acceptance review afterwards |

## Status

The response was verified as completed in the Claude UI on 2026-08-31; the screen explicitly showed `Claude finished the response`. No code, tests, remote, package transfer, or live action was performed from the review.
