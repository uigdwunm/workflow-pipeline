# 01 — Create and publish the Stage-2 Flow Worktree

Status: ready-for-agent

## What to build

Make Stage 2 create one Flow Worktree before planning begins, keep all planning
writes inside it, and publish the accepted planning commit to the latest target
branch without removing the worktree. Successful publication continues without
another user decision; an actual merge conflict preserves the flow and reports
the exact conflict for user resolution.

## Acceptance criteria

- [ ] The worktree protocol can create a Stage-2 Flow Worktree from the exact target commit while unrelated primary-checkout changes remain untouched and outside it.
- [ ] The returned Worktree Binding contains the canonical repository, Git common directory, worktree, flow branch, target branch, and base commit needed for later verification.
- [ ] Stage 2 launches its solution-design child against that exact binding and confines local planning writes and commits to the Flow Worktree.
- [ ] Planning publication automatically incorporates an unrelated target advance, merges the accepted planning commit, and advances the retained Flow Worktree to the target planning merge commit.
- [ ] Successful planning publication requires no additional user confirmation.
- [ ] A real merge conflict publishes no target merge, removes no resource, preserves the planning commit and worktree, and returns the exact conflicting paths to the existing user-decision flow.
- [ ] A non-conflict Git failure remains an execution anomaly and does not silently retry an uncertain side effect.
- [ ] No planning-file existence, duplication, or add-only rule is introduced.
- [ ] Protocol and Stage-2 contract tests cover creation, verification, unrelated target advancement, conflict retention, concurrent target advancement, and retry.

Blocked by: None — can start immediately.

