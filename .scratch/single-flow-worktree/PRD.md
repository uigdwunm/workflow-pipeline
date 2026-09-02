# Spec: Carry one Flow Worktree from solution design through closure

Status: `ready-for-agent`

## Problem Statement

Stage 2 currently writes planning documents in the primary checkout. Stage 3
then requires that checkout to be clean before it creates an implementation
worktree, integrates the implementation immediately, and removes the worktree.
Stage 4 may create a second short-lived worktree for closure documents. As a
result, unrelated changes in the primary checkout can stop planning or the
Stage-2-to-Stage-3 transition, even though those changes are not part of the
workflow.

The workflow should become isolated when Stage 2 begins and should keep that
same Git worktree through Stage 4. Stage 3 must still work independently: when
it has no upstream Flow Worktree, it must create one itself. The change must not
add another execution state machine, lease, scheduler, remote action, or
planning-file policy.

## Solution

Create one Flow Worktree at Stage 2 entry. Stage 2 writes and commits only its
planning documents there, then automatically merges that planning commit into
the latest target branch. A successful merge requires no user confirmation. If
Git reports a real merge conflict, preserve the Flow Worktree and planning
commit, show the conflict to the user, and resume the same flow after the user
decides how to resolve it. When the planning merge succeeds, fast-forward the
Flow Worktree branch to the resulting target merge commit and hand the same
Worktree Binding to Stage 3.

Stage 3 reuses a valid upstream Worktree Binding. A standalone Stage 3 entry
with no upstream binding creates its own Flow Worktree from its committed
planning source. A retry always reuses the retained worktree. A handoff that
claims an upstream worktree but supplies a missing or invalid binding is an
anomaly; it never falls back to creating a replacement.

Stage 3 implements, tests, remediates, and produces an accepted candidate in
the Flow Worktree without integrating or cleaning it. Stage 4 reuses that
worktree, applies any closure-owned document changes, then performs the final
implementation-and-closure merge and non-force cleanup. A standalone Stage 4
entry without an upstream Flow Worktree keeps its existing behavior.

## User Stories

1. As a workflow user, I want Stage 2 to create an isolated worktree before it writes planning documents, so that unrelated primary-checkout changes do not interrupt planning.
2. As a workflow user, I want Stage 2 to merge completed planning documents into the latest target branch automatically, so that the implementation source is visible on the target branch before Stage 3.
3. As a workflow user, I want a successful planning merge to continue without another confirmation, so that ordinary delivery is not interrupted.
4. As a workflow user, I want a real planning merge conflict shown to me before either side is chosen, so that the workflow never guesses the intended resolution.
5. As a Stage-3 Originating Task, I want to reuse a verified upstream Flow Worktree, so that the full Stage-2-to-Stage-4 flow creates only one worktree.
6. As a standalone Stage-3 caller, I want Stage 3 to create its own Flow Worktree, so that implementation remains usable without Stage 2.
7. As a recovery caller, I want retries to continue the exact retained worktree, so that completed work and Git identity are preserved.
8. As a workflow user, I want an invalid claimed upstream binding to stop instead of creating a second worktree, so that one logical flow cannot split silently.
9. As a Stage-4 caller, I want closure to reuse the Flow Worktree and own the final merge and cleanup, so that implementation and closure remain one isolated delivery.
10. As a repository maintainer, I want Git commits, refs, and registered worktrees to remain the execution authority, so that the change introduces no parallel lifecycle store.

## Implementation Decisions

### One Flow Worktree with two valid creation routes

- Stage 2 creates a Flow Worktree before launching its solution-design child.
- Stage 3 creates a Flow Worktree only for an explicit standalone entry or a
  direct Stage-1-to-Stage-3 route with committed sources.
- A valid upstream Worktree Binding is always reused. A retry always reuses the
  retained binding. An expected but invalid binding stops as an anomaly.
- Stage 4 reuses an upstream Flow Worktree. Its existing standalone route
  remains available when no upstream flow is claimed.

### Stage 2 publishes planning without ending the flow

- Stage 2 keeps its current ownership of Spec, necessary ADRs, and optional
  Tickets. This change adds no duplicate-file, existing-file, or add-only rule.
- After the planning commit is accepted, call one Git operation dedicated to
  planning publication. It integrates the latest target branch into the Flow
  Worktree when necessary, attempts the planning merge, and returns the exact
  planning and target merge commits.
- A successful merge is automatic and silent. A target advance is refreshed
  and retried once. A failure before the target merge restores the exact
  accepted planning commit while the Flow Worktree remains unchanged; a new
  Flow Worktree edit is preserved and reported as a restore failure. A real
  content conflict preserves the Flow Worktree and routes the exact conflict
  to the user through the existing anomaly decision flow.
- Before either target refresh or target publication, reject only ignored
  paths that overlap incoming paths. Preserve unrelated ignored paths without
  blocking.
- Once the planning commit is present on the target branch, preserve that Git
  state and report a post-publication anomaly without rollback or republication.
- After publication, fast-forward the Flow Worktree branch to the planning
  merge commit. Stage 3 therefore starts from the same commit now visible on
  the target branch.
- An explicit Git failure that is not a merge conflict remains an execution
  anomaly; it is not relabeled as a user decision.

### Stage 3 accepts but does not integrate the implementation

- The Dedicated Implementation Task works only in the verified Flow Worktree,
  keeps planning sources read-only, follows Ticket order and TDD, and returns a
  clean candidate.
- The Originating Task keeps its current candidate verification and Standards
  and Spec review responsibilities. Remediation returns to the same task and
  worktree, and a changed candidate invalidates prior review evidence.
- Stage 3 completion records the accepted candidate and retained Worktree
  Binding. It does not call final completion, merge implementation to the
  target, delete the branch, or remove the worktree.

### Stage 4 owns final publication and cleanup

- Stage 4 verifies the accepted implementation candidate in the handed-off
  Flow Worktree and applies only required closure-owned document changes there.
- If no closure document changes are needed, Stage 4 creates no empty commit.
- Stage 4 integrates the latest target when necessary, reruns the checks and
  reviews required by affected implementation changes, publishes the final
  candidate, and removes the merged branch and worktree without force.
- A failure before a verified final merge retains the same Flow Worktree for
  `$change-closure 重试`. A cleanup-only failure reports the verified merge and
  remaining resource without repeating publication.

### Keep one deep Git interface

`supervision_protocol.py` remains the sole Git-worktree module and exposes four
operations:

1. `start-worktree` creates a Flow Worktree for Stage 2 or standalone Stage 3.
2. `verify-worktree` re-derives its Git identity at every stage handoff and retry.
3. `publish-planning` merges the Stage-2 planning checkpoint without cleanup and advances the Flow Worktree to the resulting target merge.
4. `complete-worktree` performs the final Stage-4 publication and cleanup.

The binding carries stable Git identity. Stage-specific allowed and protected
paths remain explicit in the corresponding call and handoff; no durable run,
lease, queue, proposal store, or compatibility dispatcher is added.

### Preserve discussion and remote-authority separation

- Discussion Phase Runs continue to authorize lifecycle transitions only. They
  do not become worktree state and receive no Git execution record.
- An attached Stage 3 may complete its `2→3` Phase Run after the implementation
  candidate is accepted in the retained Flow Worktree. Stage 4 completes
  `3→4` only after final Git publication and cleanup succeed.
- Planning publication, implementation, closure, and local integration grant no
  push, pull request, deployment, release, tracker, or other remote authority.

## Testing Decisions

### Primary seam: worktree protocol CLI

- Start a Stage-2 Flow Worktree while the primary checkout contains unrelated
  tracked and untracked changes; verify those bytes remain outside the new
  worktree and unchanged in the primary checkout.
- Publish a planning commit after the target branch advances on unrelated
  paths; verify automatic refresh, successful planning merge, retained
  worktree, and Flow Worktree HEAD at the planning merge commit.
- Produce a real planning conflict; verify no target merge or cleanup occurs,
  the original worktree remains registered, and the response identifies the
  conflicting paths for the user-decision flow.
- Verify concurrent target advancement is retried through the existing short
  publication lock and compare-and-swap checks rather than treated as a
  terminal conflict.
- Place ignored primary-checkout bytes at a candidate path; verify publication
  stops before merge, preserves those bytes, and retains the exact planning
  commit for retry. Verify unrelated ignored bytes do not block publication.
- Complete a final Stage-4 candidate and verify the target merge parents and
  tree, non-force worktree removal, and merged branch deletion.

### Stage contract tests

- Verify Stage 2 launches its child with an exact Worktree Binding, publishes
  planning automatically after acceptance, and hands the retained binding and
  planning merge commit to Stage 3.
- Verify inherited Stage 3 reuses the binding, standalone Stage 3 creates one,
  invalid claimed bindings stop, and every retry reuses the retained worktree.
- Verify Stage 3 reports an accepted candidate without claiming target merge or
  cleanup, and Stage 4 owns final publication and cleanup.
- Verify standalone Stage 4 behavior remains available when no upstream Flow
  Worktree is claimed.

### End-to-end acceptance

- Run a standalone `1拷问 → 2方案 → 3实现 → 4归档` effort. Observe exactly one
  Flow Worktree from Stage 2 through Stage 4, automatic planning publication,
  implementation review/remediation in the same worktree, and final cleanup.
- Run standalone Stage 3 with a committed source. Observe Stage 3 create one
  Flow Worktree, Stage 4 reuse it, and final cleanup succeed.
- Repeat the Stage-2 planning publication with an unrelated target advance and
  with a controlled conflict. The first path continues without a user decision;
  the second preserves state and resumes only after the explicit decision.
- Run `./scripts/validate.sh` after all contract and protocol tests pass.

## Out of Scope

- Creating a worktree for Stage 0 or Stage 1.
- Adding planning-file existence, duplication, or add-only validation.
- Changing which Spec, ADR, or Ticket content Stage 2 produces.
- Adding a lease, scheduler, queue, durable execution state machine, proposal
  store, compatibility mode, or automatic replacement worktree.
- Changing model selection, reasoning effort, review axes, Ticket ordering,
  TDD, implementation scope, or remote-write authority.
- Automatically resolving a real merge conflict or overwriting user work.
- Adding push, pull-request, deployment, release, or tracker behavior.
- Implementing code or tests during this planning change.

## Delivery Order

1. Add the Flow Worktree Git operations and Stage-2 planning publication.
2. Change Stage 3 and Stage 4 to reuse or create according to their exact entry
   route and to defer final publication and cleanup to Stage 4.
3. Align attached discussion transitions, stage templates, documentation, and
   end-to-end contract coverage.
