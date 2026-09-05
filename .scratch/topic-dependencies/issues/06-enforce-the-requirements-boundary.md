# 06 — Enforce the requirements-only gate boundary

Status: `ready-for-agent`
Lifecycle: completed

## What to build

Prevent a closed topic from leaving or doing substantive work in Discussion
Phase 0 or 1 while keeping Topic Dependencies out of delivery coordination and
making them immutable, non-enforcing history after entry to Phase 2.

## Blocked by

- 03 — Evaluate and atomically release topic gates.
- 05 — Reclose directly affected gates after conclusion changes.

## Spec trace

- Implementation Decisions: Phase and turn guards; Phase Run evidence and
  retention.
- Acceptance scenarios: 13, 14, 15.

## Acceptance criteria

- [x] A closed derived gate blocks substantive topic updates, child-handoff preparation, relevant checkpoint preparation, and every `0→1`, `0→2`, or `1→2` preparation/readiness/activation path.
- [x] Read-only inspection, gate evaluation/release, handoff acceptance, and non-advancing recovery actions remain available while closed.
- [x] Wrapper carrier readiness and source activation recheck the gate, but a gate that closes after activation does not interrupt or retroactively invalidate that active run.
- [x] Finalization to Discussion Phase 2 makes dependencies immutable and removes all gate consultation from Stages 2–4; returning through explicit reopen restores Phase-0 mutation rules.
- [x] Parent and continuation relations are excluded from coverage evidence, actual absorption remains included, and Topic Dependencies never enter the implementation-coordination dependency digest.
- [x] Existing successful Phase-1 stepwise and continuous footers still enter Stage 2 without a new post-Phase-1 dependency check.

## Closure evidence

- Accepted candidate: `7aab54330383ea8a7ff90815015f455f4efd4372`.
- Phase/turn gate guards, allowed closed-gate operations, Phase-2 cutoff, and
  evidence-domain separation passed focused and full validation.
