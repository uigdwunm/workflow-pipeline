# Worktree Execution

`supervision_protocol.py` has exactly three public operations.

## `start-worktree`

Input fields are `repository`, `worktree`, `branch`, `target_branch`, sorted
`source_paths`, and sorted non-empty `allowed_paths`.

The primary checkout must be on `target_branch` with no tracked changes. The
command resolves the exact target HEAD, checks every source path at that commit,
and runs `git worktree add -b` from the resolved object ID. Git is the only
worktree registry; no separate ownership state is created.

## `verify-worktree`

Input fields are the returned `binding` and the task's actual `platform_cwd`.
The command verifies canonical paths, Git common directory, registered
worktree, branch, HEAD ancestry, source files, and target branch.

## `complete-worktree`

Input fields are `binding`, `candidate_commit`, and `expected_target_head`.
The candidate must be the clean worktree HEAD, contain the expected target, and
change at least one allowed path without changing a source path. The target
must still equal `expected_target_head`.

The command holds one process-local publication file lock only while checking
and updating the shared checkout. It creates a two-parent merge commit, removes
the worktree without force, deletes the merged branch with `git branch -d`, and
returns the candidate, merge commit, and changed paths.

When two candidates race from one base, one completes and the other receives
`target_changed`. The retained candidate incorporates the new target in its own
worktree, reruns verification and review, then retries with the new expected
HEAD. There is no scheduler or queue.
