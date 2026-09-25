# ADR-0010: Allow unaffected requirement discussion behind closed gates

Status: Accepted

## Context

ADR-0006 made any active closed Topic Dependency stop all substantive Phase-0/1
updates and later-turn child discussion. This prevents a dependent topic from
exploring requirements that do not rely on the missing upstream result. The
structured dependency record and accepted-basis invalidation remain useful at
the point where a topic publishes or completes requirements.

## Decision

A closed gate permits Phase-0/1 discussion updates and ordinary child or
continuation discussion after the normal handoff acceptance and later-turn
authorization. The owner reads the gate on each user-requested turn, names the
unresolved prerequisite, and keeps affected assumptions and conclusions open.
The protocol does not infer decision-level overlap from prose.

An open gate remains required when preparing and publishing a stage-entry
checkpoint, preparing a new child or dedicated-stage handoff, accepting and
delivering dedicated-stage work, submitting and absorbing Phase-0/1 child
results, and preparing, readying and activating Phase-0/1 routes. These checks
cover `0→1`,
`0→2`, `1→2` and continuous entry into Stage 2. Mutable `0→1` runs also
require an open gate before freezing, accepting or finalizing the requirement
result. A gate that closes after another route has activated does not interrupt
that run, as in ADR-0006. Existing pending-write, impact, identity and source
evidence checks still apply.

Dependency records, all-or-stop release, accepted authority identities and
digests, direct invalidation, and the ledger schema remain as defined in
ADR-0006. This decision supersedes only that ADR's blanket discussion guard.
All five packages declare `topic_gate=phase-0-1-discussion-v2` in their
compatibility key so a new discussion carrier cannot exchange work with an old
blanket-guard package under the same compatibility identity.

## Consequences

- Independent requirement details can advance while an upstream result is
  pending, including across ordinary topic handoffs.
- A discussion owner must identify which conclusions rely on the unresolved
  prerequisite. The protocol checks the gate at the named entry and result
  boundaries; it does not classify every intermediate decision or interrupt a
  route that already activated.
- Existing ledgers need no migration because the dependency record format and
  release semantics do not change.
