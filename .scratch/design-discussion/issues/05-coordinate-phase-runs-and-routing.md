# Coordinate phase runs and lifecycle routing

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Give a discussion topic a deterministic lifecycle coordinator that discovers safe context, allows only the six approved forward routes, separates stage intent from execution attempts and stable results, and supports recovery without duplicate active runs.

## Acceptance criteria

- [x] Context discovery returns `ledger`, `document_only`, `none` or `ambiguous` using the approved evidence priority; strong identity conflict stops, while none/ambiguous cause zero discussion-state writes.
- [x] Document-only context imports only verifiable documents and results, leaves coordination facts unknown, and initializes local state only after a user-authorized mutation.
- [x] Only `0→1`, `0→2`, `1→2`, `1→3`, `2→3` and `3→4` can move forward; returning to0 requires explicit reopen and affected-result review.
- [x] Phase Run intent and external attempts use monotonic identities and implement prepare, setup-pending, ready, active, completion-claimed, completion-pending, completed, blocked, failed, outcome-unknown and cancelled semantics.
- [x] External carriers cannot perform substantive work before ready/activate or claim completion after cancellation, supersession or authorization loss.
- [x] Source, route, impact, coverage, dependency or coordination drift invalidates stale activation and footer decisions with stable error codes.
- [x] Tests cover every legal and illegal transition, retry/reconcile path, duplicate terminal signal, late carrier and single-revision/single-event invariant.

Blocked by: 03 — Publish verifiable discussion checkpoints; 04 — Split and continue discussion topics.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
