# ADR-0001: Separate discussion state from execution supervision

Status: Accepted

## Context

`design-discussion` needs durable topic, decision, relation, checkpoint, handoff, Phase Run and lifecycle state. The workflow also has a deterministic `supervision_protocol.py` that owns active-source protection, exact-path document leases, short repository publication coordination, worktree execution claims, immutable implementation handoffs, supervision messages and closure checkpoints.

Putting both domains into one script would couple frequent discussion-ledger changes to Git publication, worktree ownership and document-write coordination. Reimplementing those protocols inside the discussion script would create competing authorities for the same repository facts.

## Decision

Use two deterministic protocol authorities with a typed boundary:

- `discussion_protocol.py` exclusively owns the discussion ledger, topic and conversation identity, pending items, phase results, relations, impacts, discussion checkpoints, pending document writes, Phase Runs and lifecycle transitions.
- `supervision_protocol.py` exclusively owns implementation-source protection, exact-path document leases, short repository publication coordination, durable worktree execution claims, implementation handoffs, execution supervision, integration evidence and closure checkpoints.
- Each protocol exposes single-request JSON CLI operations with stable error codes. JSON is transport only and is not persisted as a second state source.
- Neither protocol edits, copies or independently derives the other protocol's authoritative state. Cross-domain operations pass exact, verifiable receipts such as a document lease ID/version or a Phase Run/source checkpoint identity.
- Callers release a ledger lock before invoking supervision, then submit the verified receipt through a new expected-revision transaction. This prevents one protocol from holding its lock across another protocol operation.

## Consequences

- Discussion evolution remains isolated from worktree ownership, publication and document-write coordination.
- There is one authority per state machine, avoiding duplicate lease or ledger implementations.
- Cross-domain workflows need explicit prepare/execute/commit checkpoints and must handle failure or outcome-unknown between them.
- Integration tests must exercise both CLIs together and verify that a receipt from one protocol cannot be forged, replayed for another repository/topic, or used after its version changes.
- Existing 1—4 flows can bypass the discussion protocol entirely when context discovery returns `none` or `ambiguous`.
- ADR-0002 removes the former long repository lease and dual execution-mode branches without changing this authority separation.
