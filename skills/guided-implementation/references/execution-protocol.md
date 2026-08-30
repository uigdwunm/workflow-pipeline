# Dedicated Worktree Execution Protocol

The dedicated task accepts only the binding returned by `start-worktree` and
must run `verify-worktree` against its actual working directory before editing.

Before editing, use `thread-settings-v2` `verify --current` to confirm this task
is running as `gpt-5.6-terra` with reasoning effort `high`. Then read the
complete planning sources and Tickets and work only in the verified worktree.
Commit all task-owned code, tests, and implementation documentation. Planning
sources are read-only for this run.

Before reporting a candidate:

1. require a clean worktree;
2. run focused and full checks;
3. report the exact HEAD and changed paths;
4. complete Standards and Spec review; and
5. disclose findings and remaining risks.

If the originating task reports a newer unrelated target HEAD, merge that
target in this worktree, resolve conflicts here, rerun affected checks and
review, and report the new HEAD. A changed planning source, material semantic
conflict, or changed requirement stops for user direction.

The dedicated task never updates the target branch and never removes the
worktree or branch.
