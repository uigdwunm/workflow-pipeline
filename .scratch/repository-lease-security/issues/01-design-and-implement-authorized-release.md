# Design and implement authorized repository-lease release

Status: `needs-triage`
Lifecycle: `open`

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

Blocked by: trusted caller-context capability decision.

## Comments

- Split from the design-discussion closeout so the completed original feature and the open release-safety work remain distinct facts.
