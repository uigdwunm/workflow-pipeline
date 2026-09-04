# 04 — Release matching gates while absorbing child results

Status: `ready-for-agent`

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

- [ ] Child-result submission freezes protocol-derived current Phase-0/1 authority, including exact result/checkpoint identity and relevant decision digests, rather than trusting result prose.
- [ ] Parent absorption may name matching dependency releases with exact selected authority, and the absorb relation plus every requested gate release commits atomically.
- [ ] Each released dependency is active, closed, owned by the parent, directly points to that child, and matches the frozen authority's requirement kind.
- [ ] Omitting dependency releases preserves existing absorption behavior and leaves gates unchanged; recording an impact never releases a gate.
- [ ] Stale, partial, mismatched, or unauthorized release selections fail without changing either result state, coverage, or gate state.
