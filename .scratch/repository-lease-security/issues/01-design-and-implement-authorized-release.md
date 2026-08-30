# Design and implement authorized repository-lease release

Status: `wontfix`
Lifecycle: `completed`

Historical record: this ticket was closed without implementation after the
long-lived repository lease was removed. The current implementation architecture
is defined by `docs/adr/0002-unify-implementation-execution-on-isolated-worktrees.md`.

## What to resolve

Design the trusted caller seam, repository lease v3 state, one-time release
authorization, retained release receipt, terminal freeze, v2 compatibility,
governance closure contract, and real-platform smoke procedure described by the
feature PRD.

## Acceptance criteria

- [ ] The design identifies a platform-authenticated caller identity that is not controlled by the invoking model or shell command.
- [ ] A dedicated implementation task cannot authorize or release the originating task's lease even with the complete lease tuple.
- [ ] Exact authorization replay is idempotent and changed replay fails closed.
- [ ] Terminal preparation blocks unrelated mutation until delivery is resolved.
- [ ] Missing legacy leases remain explicitly unreceipted instead of being normalized as successful releases.
- [ ] Protocol failure injection and isolated real-platform smoke cover release, replay, late delivery, and unavailable enforcement.

Superseded by: `docs/adr/0002-unify-implementation-execution-on-isolated-worktrees.md`.

## Comments

- Split from the design-discussion closeout so the completed original feature and the open release-safety work remain distinct facts.
- Closed without implementation on 2026-08-30 because the accepted replacement
  architecture deletes the long-lived repository lease and its caller-identity
  authorization problem. No compatibility or migration path is required.
