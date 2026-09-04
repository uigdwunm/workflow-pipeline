# ADR-0006: Model Phase-0/1 prerequisites as topic-owned gates

Status: Accepted

## Context

Discussion Topics can be split structurally and coordinated through handoffs,
result absorption, impacts, and Phase Runs. None of those relationships means
that one topic must wait for another requirements result:

- `parent` and `continuation` describe structure and conversation continuity;
- `absorbs` and impacts describe how one result affects another topic; and
- `Dependencies and Active Implementations` describes delivery coordination.

Reusing any of them for requirements prerequisites would couple unrelated
authority domains. It would also make adding a structural child change Phase Run
coverage, or let a Phase-0/1 prerequisite leak into implementation execution.

Requirements gates must survive task handoff and retries, fail closed on stale
accepted evidence, and remain safe under concurrent topic owners. At the same
time, they must not introduce background monitoring, transitive invalidation,
or authority for an upstream topic to edit the dependent topic generally.

## Decision

Add a dedicated `Topic Dependencies` collection to the discussion ledger and
upgrade its schema to version 3 with read-compatible, write-upgrade migration
from versions 1 and 2.

Each active dependency is owned by its dependent Discussion Topic and has an
independent relation state and gate state. Topic-level openness is derived: one
active closed dependency closes the topic. Dependencies are permitted and
enforced only while the dependent topic is in Discussion Phase 0 or 1.

The discussion protocol remains the single mutation authority. One dependency
component owns graph validation, evidence resolution, atomic release, mutation,
and direct reverse invalidation. Its public behavior is exposed through an
extended child-handoff preparation operation plus read-only gate evaluation,
confirmed gate release, and confirmed dependency update operations.

Release is user-triggered and all-or-stop across the current closed set. A
release freezes structured authority identities and decision digests as the
accepted basis. Requirement summaries are never parsed to establish identity or
overlap.

When an authoritative Phase-0/1 operation replaces, discards, or reopens a
conclusion, it closes directly dependent matching gates in the same ledger
transaction. Unknown overlap closes; proved-disjoint decision authority remains
open. Invalidation is one hop and does not interrupt work that already began.

Structural `parent` and `continuation` relations do not contribute to Phase Run
coverage evidence. Topic Dependencies do not contribute to the existing Phase
Run implementation-dependency digest. Instead, explicit guards require an open
derived gate before substantive Phase-0/1 mutations, later-turn child discussion
authorization, and `0→1`, `0→2`, or `1→2` preparation/readiness/activation.
After Phase 2 entry, Topic Dependencies are immutable historical evidence and
are not consulted by Stages 2–4.

## Consequences

- Structural topology, result effects, requirements prerequisites, and
  implementation coordination keep distinct meanings and authorization rules.
- Child topic creation can atomically establish initial gates without combining
  that local preparation authority with the separate external task-creation
  confirmation.
- Open gates avoid repeated polling, while structured accepted bases allow
  precise or conservative direct invalidation without semantic text matching.
- The dependency graph stays acyclic and supports AND semantics only; users must
  resolve alternatives before recording a prerequisite.
- Existing ledgers remain readable and migrate on their next successful write;
  new code must maintain schema-aware section parsing and validation.
- Several existing operations gain a common gate guard or invalidation hook, but
  they do not gain ownership of dependency records.
- Checkpoint retention must account for authority referenced by active
  dependencies.
- No new scheduler, monitor, wakeup, cross-tree coordinator, implementation
  blocker, or transitive propagation mechanism is introduced.
