# Originating Task Protocol

The originating task owns source freezing, lifecycle CAS, independent
acceptance, target publication and closure routing. It never implements code.

## Launch

Verify committed planning artifacts and clean implementation paths. Create and
protect the source checkpoint, prepare the lifecycle, queue or provision one
claim, verify the exact worktree, activate, and publish one version-5 handoff.
The handoff contains repository, source checkpoint and worktree
branch/path/base/scope/claim. It contains no selector or primary-checkout
implementation path.

Launch the dedicated implementation task with the fixed `gpt-5.6-terra` / `high`
pair and pass both `model` and `thinking` explicitly. The task still has to
verify those settings and its exact claim before substantive work.

## Accept and publish

Require one exact implementation-only candidate, passing verification and two
review axes. Freeze and accept that identity. Only the originating supervisor
calls `publish-accepted-candidate` with the exact current target HEAD.

Repairable conflicts return to the same worktree. Material changes to scope,
business behaviour or acceptance pause for the user; the plan is never silently
replaced.

## Close

An integrated publication plus committed document convergence may create one
worktree closure checkpoint. Keep worktree, branch, claim and evidence until its
exact cleanup phase. Old active artifacts are not migrated: `cutover-preflight`
returns `unsupported_stale_execution_state` without changing their bytes.
