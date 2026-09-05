# 07 — Preserve dependency authority and validate the complete flow

Status: `completed`
Lifecycle: completed

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

- [x] Checkpoint and snapshot garbage-collection dry runs exclude evidence referenced by any active dependency basis, open or closed, and cancellation restores ordinary eligibility.
- [x] Current dependency records retain auditable accepted authority even when Recent Events is trimmed.
- [x] Stable error responses expose safe revision and dependency context for blocked, stale, cyclic, duplicate, unauthorized, unavailable-evidence, and phase-conflict cases.
- [x] End-to-end CLI tests cover concurrent revisions, exact idempotent replay, injected atomic-write failures, handoff creation recovery, stale release, direct-only invalidation, and Phase-2 cutoff.
- [x] Wrapper and repository-contract tests cover the fixed split proposal, dedicated-carrier authority boundary, local Skill references, operation registration, and schema fixtures.
- [x] The full repository validation command passes with no regressions across existing discussion, Phase Run, worktree, and documentation behavior.

## Closure evidence

- Accepted candidate: `7aab54330383ea8a7ff90815015f455f4efd4372`.
- Focused validation passed 108 tests; full repository validation passed all
  278 tests with state `valid`, and both independent review axes reported no
  actionable findings on the exact candidate.
