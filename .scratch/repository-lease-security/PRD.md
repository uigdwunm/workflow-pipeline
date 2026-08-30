# Problem: Repository lease caller authorization and terminal freeze

Status: `needs-triage`
Lifecycle: `open`

## Problem

The stage-3 repository lease is currently released by presenting its immutable
file tuple. That proves which lease object is targeted, but not that the caller
is the originating task authorized to release it. A dedicated implementation
task that learns the tuple can therefore release the lease before terminal
handoff is safely delivered.

## Required outcome

- Bind release authority to trusted platform caller identity and the exact
  originating task.
- Require and atomically consume a one-time authorization tied to the verified
  terminal handoff and internal task closure.
- Persist a durable release receipt outside cleanup-owned evidence.
- Freeze mutation after terminal result preparation except for the exact
  delivery, bounded audit, conditional byte-identical resend, and termination.
- Define an explicit compatibility path for already-held v2 leases without
  rewriting historical facts.
- Prove the protocol with failure-injection tests and an isolated real-Codex
  smoke before release.

## Constraints

- Caller-provided task IDs, environment variables, paths, and message content
  are not trusted identity.
- Existing `discussion_protocol.py` and `supervision_protocol.py` remain the two
  authoritative state modules; this work must not add a third lifecycle state
  machine.
- This tracker entry contains no private session identifiers, user-specific
  filesystem paths, or incident transcript locations.

## Next gate

Complete security solution design and confirm that the chosen enforcement seam
receives authenticated caller context before implementation begins.
