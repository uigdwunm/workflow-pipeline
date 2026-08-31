# Worktree Execution

`supervision_protocol.py` has exactly three public operations.

## `start-worktree`

Input fields are `repository`, `worktree`, `branch`, `target_branch`, sorted
`source_paths`, and sorted non-empty `allowed_paths`. `source_paths` may be empty
when the caller has no protected read-only source, as in a Stage-4 documentation
worktree; Stage 3 requires at least one committed planning source.

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
and updating the shared checkout. If the lock is unavailable for five seconds,
it returns `publication_busy` without changing or removing the worktree. It
creates a two-parent merge commit, removes the worktree without force, deletes
the merged branch with `git branch -d`, and returns the candidate, merge commit,
and changed paths.

When two candidates race from one base, one completes and the other receives
`target_changed`. The Dedicated Implementation Task merges the new target into
the retained worktree, runs affected and full checks, and commits a replacement
candidate. The Originating Task establishes the new review fixed point from that
target and exact replacement candidate, reruns both Standards and Spec axes,
and only then retries with the new expected HEAD. There is no scheduler or
queue.

## Retained-worktree recovery

Use this footer only after independently verifying that no merge was published
and the exact worktree and branch remain registered:

```text
执行结果：未完成
错误码：<exact protocol error code>
保留工作区：<canonical worktree path>
保留分支：<branch>
目标分支：<target branch>
候选提交：<candidate commit | none>
恢复入口：在当前任务中使用 `<stage-specific retry command>`
```

The retry command is `$guided-implementation 重试` for Stage 3 and
`$change-closure 重试` for Stage 4. Recovery first verifies the recorded binding
and current Git state, then either corrects and retries in that worktree or asks
for a source decision. It never creates a replacement worktree. A verified
merge or `cleanup_failed` result uses its merge commit and remaining-resource
report instead of this footer.
