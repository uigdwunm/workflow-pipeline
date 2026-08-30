# Protect active implementation sources and establish the single lifecycle

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Add the authoritative source-protection record and single implementation
lifecycle required by ADR-0002. Protection must work for every Stage-3 run,
including flows without a discussion ledger, and must prevent planning-document
writes from mutating the source of an active implementation.

## Acceptance criteria

- [x] A prepared implementation binds one committed source checkpoint and an
      exact non-empty artifact set containing repository-relative path, Git blob
      identity and SHA-256 digest.
- [x] `supervision_protocol.py` owns one repository-scoped source-protection
      record with CAS revision, source identity and the complete set of active
      implementation IDs; discussion state references its verified receipt
      without copying authority.
- [x] Source protection is created before worktree provisioning and remains
      active until every dependent implementation is archived or verifiably
      cancelled.
- [x] Every planning-document lease/write entry point rejects a protected path
      before mutation. The only exception is exact Stage-4 convergence after no
      still-running dependent implementation remains.
- [x] A marker is never implemented by editing source-document bytes after the
      checkpoint. Manual or external byte changes produce typed source drift and
      are never overwritten.
- [x] A requested source change supports only a separately identified successor
      document or verified cancellation of all dependent implementations before
      source revision and a new checkpoint. No pending-change or source-refresh
      ACK state remains.
- [x] One implementation record enforces
      `PREPARED -> QUEUED? -> PROVISIONING -> ACTIVE -> CANDIDATE -> ACCEPTED ->
      INTEGRATING -> INTEGRATED -> ARCHIVING -> ARCHIVED`, typed resumable pauses,
      and pre-integration `CANCELLING -> CANCELLED`.
- [x] CLI tests cover multiple implementations sharing one source, unrelated
      document writes, successor documents, external source mutation, premature
      convergence, cancellation and invalid lifecycle transitions.

## Comments

- ADR: `docs/adr/0002-unify-implementation-execution-on-isolated-worktrees.md`.
- Spec: `.scratch/unified-worktree-execution/PRD.md`.
