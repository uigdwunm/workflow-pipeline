# Spec: Unified worktree implementation execution

Status: `ready-for-agent`
Lifecycle: `completed`

## Problem Statement

`3实现` currently maintains two execution architectures. The ordinary-checkout
path holds a repository lease for the entire implementation, while the isolated
path uses a dedicated worktree and execution lease. This duplicates protocol
state and leaves the long repository lease with an identity-authorization
problem that cannot be solved by caller-supplied task metadata.

The isolated path already provides the required code isolation, but it is not a
drop-in replacement. Its current source-refresh rules permit active-source
evolution, formal integration revalidation binds candidates to their original
base, worktree document proposals can prevent cleanup, the execution lease
expires, and partial cleanup is not fully idempotent.

The project needs one worktree-only execution protocol that covers serial and
parallel implementation, treats active sources as immutable, publishes code and
documentation through short bounded critical sections, and recovers every local
side effect without a caller-identity Hook.

## Goals

- Run every implementation in one exact dedicated worktree and branch.
- Protect the exact committed source artifacts for the complete active lifetime.
- Replace expiring execution leases with durable CAS execution claims.
- Keep routine coding free of repository-wide coordination.
- Make Stage 3 the sole owner of final code integration.
- Make Stage 4 the sole owner of implementation-outcome document convergence
  and resource cleanup.
- Make provisioning, integration and cleanup recoverable after process failure.
- Delete the exclusive mode, long repository lease, identity-Hook design and all
  compatibility branches for old execution state.

## Non-goals

- Defending against malicious processes with the same filesystem permissions.
- Migrating or completing old handoff, lease, control or closure records.
- Changing requirements inside a running implementation.
- Automatically resolving material semantic conflicts.
- Performing push, pull-request, deployment or other remote writes.

## User Stories

1. As an implementation owner, I want serial and parallel runs to use the same
   worktree protocol so scheduling does not change safety or recovery semantics.
2. As an implementer, I want my exact source checkpoint to remain authoritative
   until completion so another Skill cannot silently change my requirements.
3. As a user with a new requirement, I want either a separately identified
   successor document or an explicit stop-and-restart, never an invisible
   pending mutation to the active source.
4. As an integrator, I want publication serialized against the exact target HEAD
   while all coding remains isolated from the primary checkout.
5. As an implementer facing conflicts, I want simple reconciliation to continue
   locally but design-invalidating changes to stop for a user decision.
6. As a closure owner, I want document convergence and cleanup to resume safely
   after any crash without force-deleting unknown work.
7. As a maintainer, I want obsolete modes, schemas and authorization branches
   removed rather than retained behind compatibility dispatch.

## Functional Decisions

### 1. Source checkpoint and active-source protection

- Activation requires one committed implementation-source checkpoint and an
  exact set of authoritative paths, blob IDs and SHA-256 digests.
- `supervision_protocol.py` owns a repository-scoped source-protection record
  keyed by source identity and active implementation IDs. The discussion ledger
  may reference its receipt but does not duplicate its authority.
- Source protection is installed before worktree provisioning and remains until
  every dependent implementation is archived or verifiably cancelled.
- All planning-document write paths inspect source protection. Document-lease
  acquisition rejects a protected path except for an authorized final closure
  convergence with no still-running dependent implementation.
- External edits are detected by exact blob comparison and produce source drift.
  Unrelated documents, new successor documents and changes outside the frozen
  artifact set do not affect the running implementation.
- A requested change to an active source must either create a new successor
  document or stop all dependent implementations before modifying and
  re-checkpointing the original. No `pending change` state or source-refresh ACK
  path remains.

### 2. Single lifecycle

The authoritative lifecycle is:

```text
PREPARED
→ QUEUED (optional)
→ PROVISIONING
→ ACTIVE
→ CANDIDATE
→ ACCEPTED
→ INTEGRATING
→ INTEGRATED
→ ARCHIVING
→ ARCHIVED
```

- `PREPARED` freezes source and installs protection.
- `QUEUED` is scheduling only and does not select another execution mode.
- `PROVISIONING` owns worktree/branch creation recovery.
- `CANDIDATE` names one exact tested and reviewed commit; `ACCEPTED` authorizes
  only that commit.
- A conflict or candidate rewrite returns to `ACTIVE` and invalidates previous
  review and acceptance.
- Pauses use a typed reason plus `resume_state`; they do not create many terminal
  blocked states.
- Before integration, verified cancellation follows
  `CANCELLING -> CANCELLED`. After integration, Stage 4 must finish or a separate
  implementation must revert the change.

### 3. Provisioning and execution ownership

- The controller chooses a unique branch and normalized worktree path, then CAS
  creates a non-expiring execution claim containing repository, path, branch,
  base commit, source checkpoint and implementation identity.
- `git worktree add` occurs only after reservation. Reconciliation inspects the
  claim, Git worktree registry, branch ref and filesystem to adopt one exact
  creation, retry when no effect occurred or stop on ambiguity.
- The implementation task verifies the exact platform working directory and
  binding before every substantive turn. It may write and commit only inside
  that worktree.
- Separate implementation worktrees may run concurrently. Scheduling may queue
  known unsafe overlap, but an inaccurate scope forecast never authorizes an
  unsafe merge; integration revalidation and conflict handling remain mandatory.
- Worktree creation, task-branch commits and normal cleanup use Git locking and
  exact claims, not a repository coordination lease.

### 4. Stage-3 code integration

- Candidate review and acceptance bind the exact implementation commit, test
  evidence and source checkpoint.
- The Stage-3 supervisor, not the implementation worker, performs final
  integration in the verified primary checkout.
- Integration requires a clean primary checkout, no in-progress Git operation,
  the expected target branch, a short target-branch publication slot and an
  expected-HEAD CAS check immediately before mutation.
- User changes are never stashed, reset, cleaned or overwritten. An unavailable
  primary checkout produces a recoverable wait/block result.
- A clean merge runs final verification before committing. Publication verifies
  the target ref, merge parents, tree and implementation-only path constraints.
- On merge conflict or failure, the primary checkout aborts and proves exact
  restoration. Conflict work happens in the original implementation worktree by
  integrating the latest target branch there.
- Textual/structural reconciliation and local adaptation that preserves scope,
  business behaviour and acceptance criteria may proceed without a new user
  decision. Material semantic conflict, invalidated assumptions or extensive
  redesign pauses for the user.
- Every changed candidate reruns tests and review and requires new acceptance.
- A crash around merge commit is reconciled by exact target ref, parents and
  tree. Only a unique match may be adopted as `INTEGRATED`; ambiguity never
  triggers a second merge.

### 5. Stage-4 document convergence

- Stage 4 begins only from a verified `INTEGRATED` commit and cannot merge the
  implementation branch.
- Closure proposals are implementation-outcome documentation only. They cannot
  contain requirements introduced while implementation was active.
- Each write verifies the frozen before-blob, acquires a document lease for the
  exact paths, applies explicit apply/no-op/conflict semantics, and commits under
  the short target-branch publication slot and expected-HEAD CAS.
- A shared protected source is not modified while any dependent implementation
  is still running. Outcome proposals remain immutable closure evidence until a
  final convergence operation can evaluate all of them.
- Manual/external source edits and incompatible outcome proposals produce a
  convergence conflict; closure preserves evidence and asks for resolution
  rather than overwriting.

### 6. Idempotent cleanup and recovery

The isolated closure subphases are fixed:

```text
documents-committed
→ worktree-removed
→ branch-removed
→ execution-claim-released
→ archived
```

- Worktree removal requires the exact registered path, branch, commit and clean
  status and is non-forcing.
- Branch removal requires the exact ref and proven ancestry from the integrated
  commit and is non-forcing.
- If a resource is already absent after a crash, recovery adopts the effect only
  when retained before/after evidence proves it was the exact intended action.
  Missing or changed state without such proof remains ambiguous.
- The checkpoint CLI executes local cleanup effects itself and rejects
  caller-authored success receipts.
- Normal execution-claim release exists only as the last verified cleanup
  action. Administrative reconciliation is explicit, audited and never based on
  TTL expiry.
- Source protection is released only after document disposition and every
  dependent implementation's terminal state are proven.

### 7. Protocol removal and cutover

- Remove `exclusive-checkout-v2`, repository lease acquire/inspect/verify/release
  operations, renewal and queueing, and their state files and schemas.
- Remove execution-mode choice and mode-specific branches from discussion phase
  runs, guided-implementation handoffs, supervision messages, integration
  receipts, closure checkpoints and user footers.
- Remove repository-lease/document-lease priority rules that exist only for the
  long exclusive lease. Preserve exact-path document leases and the narrow
  target-branch publication slot.
- Remove expiring worktree-execution lease semantics and standalone ordinary
  release. Rename the concept to worktree execution claim in schemas and UI.
- Close the repository-lease caller-authorization work as superseded. Do not
  implement trusted caller context, one-time release authorization,
  repository-lease v3, terminal identity Hook or legacy migration.
- New code rejects old active protocol artifacts with a stable
  `unsupported_stale_execution_state` error. No converter or compatibility
  runner is provided.

### 8. Threat model

- The protocol prevents accidental ownership loss, stale writes, duplicate
  publication and cleanup races among cooperative Skills and tasks.
- Caller-supplied task IDs are labels, not security identity. Correctness comes
  from immutable source/candidate identity, exact environment binding, CAS and
  checkpoint-owned effects.
- Same-permission malicious processes remain out of scope. A future requirement
  to resist them needs platform identity or isolation and a separate ADR.

## Required Verification

- One serial implementation completes the full lifecycle with no repository
  lease or execution-mode branch.
- Two independent worktrees execute concurrently and integrate serially; changed
  target state is revalidated before the second publication.
- Textual, local-semantic and material-semantic conflict fixtures exercise direct
  repair, candidate reacceptance and user-decision pause respectively.
- Every document-writing Skill refuses a protected source, permits a separately
  identified successor document, and permits original-source revision only
  after all dependent implementations are cancelled and protection is released.
- Incorrect claim ID/version, duplicate acquisition and premature release fail
  without changing state; claims do not expire.
- Dirty primary checkout, target-ref race and concurrent publication leave user
  state untouched and produce stable recovery results.
- Failure injection surrounds claim creation, worktree creation, merge commit,
  document commit, worktree removal, branch removal and claim release. Recovery
  distinguishes no effect, uniquely proven prior effect and ambiguity.
- Multiple implementations sharing a source cannot write it prematurely and can
  converge all implementation-outcome proposals once the final dependency ends.
- Static and CLI tests prove no new path creates or accepts exclusive mode,
  repository lease, identity-Hook authorization or old compatibility state.

## Completion Criteria

- All implementation Tickets in this feature are completed and independently
  verified.
- `guided-implementation`, `change-closure`, discussion lifecycle integration,
  deterministic protocol CLIs, references and tests describe one identical
  worktree-only contract.
- Repository validation and the complete test suite pass from the integrated
  target branch.
- Searches for removed mode and repository-lease vocabulary return only retained
  historical planning records that explicitly point to ADR-0002.
- No implementation code, migration or remote action is performed as part of
  this planning Spec.
