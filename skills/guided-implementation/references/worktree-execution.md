# Worktree Execution

All Stage-3 runs use the same worktree protocol.

## Freeze

Prepare normalized scope, one branch, one canonical worktree path and one
committed source checkpoint. Protect every source artifact before activation.

## Queue or provision

A conflicting run remains `QUEUED`. A safe run enters `PROVISIONING` and calls:

```text
supervision_protocol.py reserve-worktree-execution-claim --input <binding>
supervision_protocol.py provision-claimed-worktree --input <claim-receipt>
supervision_protocol.py reconcile-worktree-provisioning --input <claim-receipt>
```

Reservation precedes branch and filesystem effects. The claim is non-expiring
cooperative CAS state, not task authentication. Reconciliation yields only
`exact-adoption`, `safe-retry`, or `worktree_provisioning_ambiguous`.

## Verify and run

Verify claim bytes and revision, exact platform cwd, Git common directory,
worktree registration, branch and base ancestry. Independent runs use different
worktrees concurrently. Serial work uses the same protocol after waiting in
`QUEUED`. Routine commits need no repository-wide coordination.

Normal claim release is unavailable to implementation tasks. Stage 4 or the
explicit administrative abandoned-claim operation releases it.
