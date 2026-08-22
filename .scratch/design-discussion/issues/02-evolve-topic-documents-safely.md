# Evolve topic documents safely

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Let an active topic persist confirmed decisions, suspend and revisit questions, absorb a user's inserted idea and review the effects of a changed direction without losing or silently rewriting previously confirmed design.

## Acceptance criteria

- [x] Each topic document distinguishes confirmed decisions, candidate solutions, tentative assumptions, facts and pending questions, with stable decision identities and concise decision evolution.
- [x] Each topic has at most one active user-visible question; inserting a new idea suspends the prior question until the model explicitly resumes, adjusts or invalidates it.
- [x] A changed direction creates decision-scoped impacts and requires separate keep/adjust/replace/discard confirmation for each affected decision.
- [x] Every document change is prepared as an immutable pending write with before/after digests, requires both current document ownership and a verified shared document lease, and is verified before the lease is released.
- [x] Document-lease timeout or uncertain persistence preserves a recoverable confirmed-but-pending checkpoint and prevents further substantive discussion until reconciliation succeeds.
- [x] CLI-level tests cover ownership conflicts, stale leases, replay, orphaned or damaged pending writes, inserted ideas and decision-by-decision impact resolution.

Blocked by: 01 — Bootstrap persistent 0讨论 topics.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
