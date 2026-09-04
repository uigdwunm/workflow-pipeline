# ADR-0004: Require planning before implementation

Status: Superseded by ADR-0005
Supersedes: ADR-0003

Phases 0 and 1 maintain exactly one current requirement document. Phase 2 reads
one committed, frozen version of that document and publishes the Spec, necessary
ADRs, and useful Tickets. Phase 3 accepts only that verified Phase-2 planning
handoff and reuses the Flow Worktree created by Phase 2.

Remove the `1→3` route and standalone Stage-3 worktree creation. A direct
implementation path makes the requirement document serve two incompatible
roles: input to design in the normal flow and an implementation plan in the
shortcut. Requiring Phase 2 gives Stage 3 one consistent authority and keeps
requirement decisions out of implementation.

Stage 2 remains responsible for creating and publishing the Flow Worktree.
Stage 3 verifies and reuses it, and Stage 4 publishes implementation and closure
updates and cleans it. An absent or invalid Stage-2 handoff is an anomaly rather
than permission to synthesize a replacement plan or worktree.
