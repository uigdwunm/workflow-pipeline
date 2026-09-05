# 05 — Reclose directly affected gates after conclusion changes

Status: `completed`
Lifecycle: completed

## What to build

When a prerequisite topic authoritatively replaces, discards, or reopens an
accepted Phase-0/1 conclusion, close only directly dependent gates whose
structured accepted basis is affected, preserving enough evidence for the next
release.

## Blocked by

- 03 — Evaluate and atomically release topic gates.
- 04 — Release matching gates while absorbing child results.

## Spec trace

- Implementation Decisions: Direct reverse invalidation; accepted basis.
- Acceptance scenarios: 6, 7, 8, 11, 13.

## Acceptance criteria

- [x] Authoritative adjust, replace, discard, and Phase reopen operations match dependencies by upstream topic, authority identity, result state, and decision ID/digest rather than free-text summaries.
- [x] Matching direct open gates close in the same transaction as the upstream authoritative change, retain their prior accepted basis, increment revision, and record the exact cause.
- [x] Unknown or incomplete provenance fails closed, while a complete disjoint decision set remains open and a keep action causes no invalidation.
- [x] Invalidation examines only dependencies whose direct prerequisite is the changing topic and never recursively closes grandchildren.
- [x] An injected invalidation failure prevents the upstream conclusion change from becoming authoritative.
- [x] A gate closed after a dependent turn has started does not interrupt that turn; the dependent observes it at the start of its next user-triggered turn.

## Closure evidence

- Accepted candidate: `7aab54330383ea8a7ff90815015f455f4efd4372`.
- Structured direct invalidation, fail-closed provenance, non-propagation,
  atomic failure, and next-turn observation passed focused and full validation.
