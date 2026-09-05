# 04 — Release matching gates while absorbing child results

Status: `completed`
Lifecycle: completed

## What to build

Allow one confirmed child-result absorption to both record its existing parent
coverage effect and release explicitly selected matching parent dependencies,
without treating impacts or unrelated results as prerequisites.

## Blocked by

- 02 — Prepare gated topics from proactive split proposals.
- 03 — Evaluate and atomically release topic gates.

## Spec trace

- Implementation Decisions: Accepted child-result release.
- Acceptance scenario: 10.

## Acceptance criteria

- [x] Child-result submission freezes protocol-derived current Phase-0/1 authority, including exact result/checkpoint identity and relevant decision digests, rather than trusting result prose.
- [x] Parent absorption may name matching dependency releases with exact selected authority, and the absorb relation plus every requested gate release commits atomically.
- [x] Each released dependency is active, closed, owned by the parent, directly points to that child, and matches the frozen authority's requirement kind.
- [x] Omitting dependency releases preserves existing absorption behavior and leaves gates unchanged; recording an impact never releases a gate.
- [x] Stale, partial, mismatched, or unauthorized release selections fail without changing either result state, coverage, or gate state.

## Closure evidence

- Accepted candidate: `7aab54330383ea8a7ff90815015f455f4efd4372`.
- Frozen child authority, atomic absorption/release, omission behavior, and
  rejection paths passed the focused 108-test run and full validation.
