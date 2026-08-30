# ADR-0002: Run every implementation in an isolated worktree

Status: Accepted

## Context

Stage 1 and Stage 2 commit the documents that authorize implementation before
Stage 3 starts. A worktree created from that commit sees stable source bytes;
later edits in another checkout do not alter the running worktree.

Git already owns the facts needed to identify an implementation: repository,
base commit, branch, worktree path, candidate commit, and target branch. A
second durable execution lifecycle would duplicate those facts and add recovery
and compatibility branches without improving isolation.

## Decision

### Three operations

`supervision_protocol.py` has exactly three public commands:

1. `start-worktree` verifies that the primary checkout has no tracked or staged
   changes, verifies every authoritative source path exists in the committed
   base, then creates one dedicated branch and worktree.
2. `verify-worktree` re-derives the repository common directory, registered
   worktree, branch, base ancestry, target branch, and source paths from Git.
3. `complete-worktree` validates the clean candidate and integrates it, then
   removes the worktree and implementation branch.

There is no execution mode, durable claim, lease, queue, handoff schema,
lifecycle state machine, proposal store, or compatibility dispatcher.

### Source and scope checks

The source-path set is committed before worktree creation. At completion, each
source path must still match the base commit. The candidate may change only the
explicit allowed implementation paths, and the worktree and index must be
clean. Untracked files in the primary checkout neither enter the worktree nor
block its creation.

### Integration and concurrency

`complete-worktree` takes one internal short-lived file lock only while it
rechecks and updates the shared target branch. It requires the target to equal
the expected commit, performs a no-fast-forward merge, verifies the merge
parents and tree, and then releases the lock.

Independent worktrees may code concurrently. If one completion advances the
target, another completion fails with `target_changed` and preserves its
worktree. That task merges the current target into its own worktree, resolves
any conflict there, reruns the relevant tests and review, and retries with the
new expected target. There is no waiting queue.

### Failure and cleanup

Failures before a verified merge preserve the worktree and branch for ordinary
inspection and retry. After a verified merge, the protocol removes the exact
registered worktree and then its merged branch. A cleanup failure reports the
merge commit and the remaining resource; it does not invent a separate
recovery state.

Stage 4 does not clean Stage-3 resources. If closure needs a documentation
change, it uses the same three-command worktree flow with documentation paths
as the allowed change set. If no documentation changes are needed, it performs
no Git mutation.

## Consequences

- Running implementations are isolated by committed worktree state, so ongoing
  document edits in other checkouts do not affect them.
- Coordination exists only for the brief target-branch publication window.
- Git inspection is sufficient to understand and repair an interrupted run.
- Old runtime artifacts are not recognized, migrated, or retained as a hidden
  execution path.
