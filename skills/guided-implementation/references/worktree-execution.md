# Worktree Execution

`supervision_protocol.py` has exactly four public operations. One Flow
Worktree may carry stages 2, 3, and 4. Git is the only worktree registry; no
separate ownership state is created.

## `start-worktree`

Input fields are `repository`, `worktree`, `branch`, and `target_branch`. The
primary checkout must be on `target_branch`, and its `HEAD` must equal that
branch. Uncommitted primary-checkout changes do not block creation and are not
copied, staged, stashed, or removed. The command resolves the exact target
`HEAD` and runs `git worktree add -b` from that object ID.

Stage 2 calls this operation before its first planning write. Stage 3 reuses a
valid inherited binding or calls this operation once after a standalone request
passes its implementation-authority gate. An absent or invalid claimed binding
is an anomaly and must not fall back to a new worktree. Retry always reuses the
retained binding.

## `verify-worktree`

Input fields are the returned `binding` and the task's actual `platform_cwd`.
The command verifies canonical paths, Git common directory, registered
worktree, branch, base ancestry, and target branch. Allowed and protected path
scopes belong to a publication or completion attempt, not to the stable
binding.

## `publish-planning`

Input fields are `binding`, `planning_commit`, sorted non-empty
`allowed_paths`, and sorted `protected_paths`. The planning commit must be the
clean Flow Worktree `HEAD`. The command reads the latest target while holding
the short publication lock. If the target advanced without changing a
protected source, it merges that target into the Flow Worktree and validates
the resulting candidate against the declared planning paths. Ignored Flow
Worktree paths that overlap incoming target paths stop before this refresh;
unrelated ignored paths remain untouched.

An ordinary successful merge publishes the planning candidate to the target
with one no-fast-forward merge commit, fast-forwards the Flow Worktree to that
merge commit, and retains the Flow Worktree and branch for Stage 3. It does not
ask for confirmation. A real content conflict returns `planning_conflict`,
restores the original clean planning commit, retains the same worktree and
branch, and requires a user decision through the stage anomaly flow. The
operation does not check whether planning files previously existed or resemble
other files; it validates only the declared path boundary.

Unrelated unstaged changes in the primary checkout are preserved. Staged
primary-checkout changes and ignored paths that overlap candidate paths stop
before the target merge; unrelated ignored paths remain untouched. If any
pre-publication check or merge fails, the Flow Worktree returns to the exact
accepted `planning_commit`, so the same request remains retryable. A Flow
Worktree edit that appears during publication is preserved and returns
`integration_restore_failed` instead of being reset. A target race is refreshed
and retried once while the lock is held; a second race or another Git failure
returns the exact error with that retained state. Once `planning_commit` is an
ancestor of the target, any later error is `integration_unverified`: preserve
the Git state and do not publish or roll back the planning candidate again.

## `complete-worktree`

Input fields are `binding`, `candidate_commit`, `expected_target_head`,
`scope_base_commit`, sorted non-empty `allowed_paths`, and sorted
`protected_paths`. The candidate must be the clean Flow Worktree `HEAD`, contain
the expected target, descend from the scoped base, change at least one allowed
path, and leave protected paths unchanged. The target must still equal
`expected_target_head`.

Stage 4 is the normal owner of this operation. It creates one no-fast-forward
merge commit containing the accepted implementation and any closure-document
updates, removes the worktree without force, deletes the merged branch with
`git branch -d`, and returns the candidate, merge commit, and changed paths.

The command holds one process-local publication file lock only while checking
and updating the shared checkout. If the lock is unavailable for five seconds,
it returns `publication_busy` without changing or removing the worktree.
Unrelated unstaged primary-checkout changes are preserved; staged changes stop
publication. Ignored paths that overlap candidate paths also stop publication
before Git can overwrite them; unrelated ignored paths do not block it.

When two candidates race from one base, one completes and the other receives
`target_changed`. The Dedicated Implementation Task merges the new target into
the retained Flow Worktree, runs affected and full checks, and commits a
replacement candidate. The Originating Task establishes the new review fixed
point and reruns both Standards and Spec axes before retrying. There is no
scheduler or queue.

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
`$change-closure 重试` for Stage 4. Stage-2 failures use its existing anomaly
and same-child resume flow. Recovery first verifies the recorded binding and
current Git state, then either corrects and retries in that worktree or asks for
a source decision. It never creates a replacement worktree. A verified merge,
`flow_advance_failed`, or `cleanup_failed` result uses its merge commit and
remaining-resource report instead of this footer.
