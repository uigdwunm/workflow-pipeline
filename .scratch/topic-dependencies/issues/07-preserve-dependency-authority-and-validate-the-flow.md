# 07 — Preserve dependency authority and validate the complete flow

Status: `ready-for-agent`

## What to build

Keep every authority object needed by an active Topic Dependency through
checkpoint cleanup and bounded event history, then verify the complete split,
wait, release, invalidation, recovery, and phase-boundary flow against repository
contracts.

## Blocked by

- 02 — Prepare gated topics from proactive split proposals.
- 04 — Release matching gates while absorbing child results.
- 05 — Reclose directly affected gates after conclusion changes.
- 06 — Enforce the requirements-only gate boundary.

## Spec trace

- Implementation Decisions: Phase Run evidence and retention; validation and
  errors; Testing Decisions.
- Acceptance scenarios: 17, 18, 19, 20.

## Acceptance criteria

- [ ] Checkpoint and snapshot garbage-collection dry runs exclude evidence referenced by any active dependency basis, open or closed, and cancellation restores ordinary eligibility.
- [ ] Current dependency records retain auditable accepted authority even when Recent Events is trimmed.
- [ ] Stable error responses expose safe revision and dependency context for blocked, stale, cyclic, duplicate, unauthorized, unavailable-evidence, and phase-conflict cases.
- [ ] End-to-end CLI tests cover concurrent revisions, exact idempotent replay, injected atomic-write failures, handoff creation recovery, stale release, direct-only invalidation, and Phase-2 cutoff.
- [ ] Wrapper and repository-contract tests cover the fixed split proposal, dedicated-carrier authority boundary, local Skill references, operation registration, and schema fixtures.
- [ ] The full repository validation command passes with no regressions across existing discussion, Phase Run, worktree, and documentation behavior.
