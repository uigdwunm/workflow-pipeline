# ADR-0002: Unify implementation execution on isolated worktrees

Status: Accepted

## Context

Stage 3 currently has two execution modes. `exclusive-checkout-v2` performs
implementation in the ordinary checkout while holding a repository lease for
the whole implementation. `isolated-worktree-v1` performs implementation in a
dedicated worktree, holds a worktree execution lease, and uses shorter
repository-coordination leases around selected shared operations.

The dual-mode design makes handoff, source validation, integration, closure,
recovery, testing and user-facing status branch on execution mode. The long
repository lease also has an unresolved authorization problem: release proves
the exact lease file tuple but not the identity of the caller presenting it.
Solving that problem as a security boundary would require platform-authenticated
identity or isolation rather than a task ID supplied by the caller.

Implementation sources are committed before Stage 3 and frozen by immutable
checkpoint identity and exact artifact blobs. Ordinary checkout edits therefore
do not alter the bytes used by a running worktree, but source-document mutation
can still invalidate authority or create archive convergence conflicts.

There are no active runs or historical protocol consumers that require the old
handoff, lease, control or closure schemas to remain executable.

## Decision

### One execution architecture

All new Stage-3 implementations run in a dedicated Git worktree and on a
dedicated implementation branch. The protocol has no execution-mode choice.
New handoffs and checkpoints do not carry a mode enum whose only valid value is
worktree execution.

`exclusive-checkout-v2`, the long-lived repository lease, their mode-specific
handoff and closure branches, and compatibility dispatch for their retained
state are removed. Old state is not migrated. If a cutover preflight finds an
old active lease, handoff or closure checkpoint, it stops with an explicit
unsupported-stale-state result for manual inspection.

### Immutable implementation sources

Before provisioning a worktree, the implementation freezes a committed source
checkpoint and the exact authoritative artifact set: repository-relative path,
blob identity and digest. A source-protection record associates that set with
every active implementation ID that depends on it. This protection is runtime
lifecycle state, not a post-checkpoint edit to the source document.

Every Skill that can write an authoritative planning document must inspect the
protection record. A document lease must reject protected paths unless the
request is the authorized closure write for the final active dependency. A
manual or external edit that bypasses the protocol is detected by exact blob
comparison and becomes source drift; it is never overwritten automatically.

When a user requests a source change during implementation, there are only two
valid routes:

1. create a new successor requirement or design document with its own identity,
   leaving the current implementation on its frozen source; or
2. stop every implementation that depends on the source, release protection
   after verified cancellation cleanup, update the source, publish a new
   checkpoint and start a new implementation identity.

There is no intermediate pending-change mechanism. Closure proposals may record
the outcome of completed implementation, but may not introduce a new
requirement.

### Worktree execution claim

Each run reserves its intended worktree path, branch, repository, base commit,
source checkpoint and implementation identity using a durable CAS-protected
worktree execution claim. The claim is created before `git worktree add`, so a
crash during provisioning can be reconciled against one durable intent.

The claim does not expire automatically. It is coordination under a cooperative
same-user threat model, not caller authentication. Normal release is not a
standalone worker action: the closure checkpoint CLI performs verified cleanup
and releases the exact claim by CAS as its final resource action. A separate
administrative recovery operation may reconcile or release an abandoned claim
only after inspecting the frozen identity and Git state.

Normal worktree creation, implementation commits and cleanup rely on the claim,
exact binding checks and Git's own locking. They do not acquire a repository-wide
lease.

### Integration is a Stage-3 publication operation

Implementation coding remains confined to its worktree. After one exact
candidate commit is tested, reviewed and accepted, the Stage-3 supervisor uses
the verified primary checkout only for the final publication operation. It must
be clean, free of an in-progress Git operation and at the expected target branch
state. The supervisor acquires a short target-branch publication slot and uses
an expected-HEAD CAS boundary; it never stashes, discards or rewrites user work.

If a merge conflict occurs, the primary checkout aborts and proves restoration.
Resolution returns to the original implementation worktree:

- textual or structural conflicts may be resolved directly when both intended
  behaviours remain intact;
- local semantic adaptation may be rewritten by the implementation when scope,
  business behaviour and acceptance criteria remain unchanged; and
- material semantic conflict, invalidated design assumptions or a largely
  unusable plan pauses for user decision.

Every resolution creates a new candidate, reruns proportionate tests and review,
and requires acceptance of the new exact commit before another publication
attempt. An outcome-unknown integration is reconciled from the target ref,
commit parents and tree; it is never blindly retried.

### Documentation convergence and cleanup are Stage 4

Stage 4 consumes a verified integrated commit. It writes only implementation
outcome documentation. Before writing, it acquires document leases for exact
paths, verifies their frozen blobs and obtains the same short target-branch
publication slot and expected-HEAD CAS used for a documentation commit.

If more than one active implementation depends on a protected document, the
document remains unchanged until all such implementations have reached a state
that permits convergence. Their outcome proposals remain immutable closure
evidence; the final convergence operation applies, rejects or reports conflict
for each proposal before releasing source protection.

Cleanup is checkpointed and ordered:

`documents-committed -> worktree-removed -> branch-removed -> execution-claim-released -> archived`.

Each step is idempotent. Recovery distinguishes absent because the exact prior
action succeeded from absent or changed for an unknown reason. It adopts only a
uniquely proven prior effect; ambiguity pauses without force cleanup. The
execution claim is released last.

### State machine

The single lifecycle is:

`PREPARED -> QUEUED? -> PROVISIONING -> ACTIVE -> CANDIDATE -> ACCEPTED -> INTEGRATING -> INTEGRATED -> ARCHIVING -> ARCHIVED`.

An integration conflict returns to `ACTIVE`. A pause records a typed reason and
the resumable prior state rather than creating many permanent blocked states.
Cancellation before integration follows `CANCELLING -> CANCELLED`; an integrated
change cannot be called cancelled and must finish archival or be reverted by a
separate change.

### Security boundary

CAS prevents accidental release and concurrent ownership races among cooperative
workflow participants. It does not defend against a malicious process with the
same filesystem permissions. If that stronger threat model is required later,
the platform must provide unforgeable identity or isolation. A managed Git Hook
is neither required by this design nor sufficient for that stronger boundary.

## Consequences

- Serial execution is one queued worktree at a time; parallel execution is
  multiple independent worktrees with serialized publication. No capability is
  lost by deleting the ordinary-checkout mode.
- Implementation no longer holds repository-wide coordination during coding.
  Shared coordination is limited to target-branch publication and exact
  document writes.
- Handoff, supervision, closure and recovery have one schema and one state
  machine.
- Source mutation becomes an explicit lifecycle transition instead of a refresh
  of an active implementation.
- Long repository-lease caller authorization, one-time release authorization,
  repository-lease v3 and a managed identity Hook are unnecessary.
- Implementation must replace, rather than merely relabel, the existing isolated
  path because current integration revalidation, proposal cleanup, lease expiry
  and partial-cleanup recovery do not satisfy this decision.

## Superseded decisions

This ADR supersedes only the Stage-3/Stage-4 execution-mode, source-refresh,
lease, integration and compatibility decisions in the completed
`.scratch/design-discussion/` feature. Its discussion lifecycle and the
protocol-authority separation in ADR-0001 otherwise remain in force. The new
implementation plan is `.scratch/unified-worktree-execution/PRD.md`.
