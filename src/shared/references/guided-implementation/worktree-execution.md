# Worktree Execution

`supervision_protocol.py` has exactly four public operations. One Flow
Worktree may carry stages 2, 3, and 4. Git is the only worktree registry; no
separate ownership state is created.

## Command input

Every operation reads exactly one bounded UTF-8 JSON object from standard
input. The command interface has no input-path argument: callers send the
request bytes directly to stdin, or redirect a saved request file to stdin when
replay is useful. A temporary file, `/dev/stdin`, `/dev/fd/*`, a FIFO, or
process substitution is never part of the protocol interface.

For example:

```bash
python3 supervision_protocol.py start-worktree <<'JSON'
{"branch":"codex/example","repository":"/absolute/repository","target_branch":"main","worktree":"/absolute/worktree"}
JSON
```

Input decoding, size checks, and schema validation complete before an operation
performs any Git action.

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
before the target merge; candidate filenames are checked literally even when
they contain Git pathspec syntax. Unrelated ignored paths remain untouched. If any
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
updates, removes the worktree without force, and deletes the unchanged merged
branch through a guarded Git reference transaction. It retains the non-force
merged/upstream/checked-out protections described below, and returns the candidate,
merge commit, and changed paths.

The command holds one process-local publication file lock only while checking
and updating the shared checkout. If the lock is unavailable for five seconds,
it returns `publication_busy` without changing or removing the worktree.
Unrelated unstaged primary-checkout changes are preserved; staged changes stop
publication. Ignored paths that overlap candidate paths also stop publication
before Git can overwrite them; unrelated ignored paths do not block it.

When two candidates race from one base, one completes and the other receives
`target_changed`. The Implementation Dispatcher merges the new target into
the retained Flow Worktree, runs affected checks, and commits a
replacement candidate. The Originating Task establishes the new review fixed
point and reruns both Standards and Spec axes, then authorizes the same
dispatcher to complete the frozen final validation before retrying. There is no
scheduler or queue.

## Exact publication recovery (flow-worktree-v2)

The stage owner uses workflow-progress-v11 for durable publication. The Python
publishers accept a `record` callback that persists each immutable fact in the
existing owner checkpoint before the next effect. A callback failure stops the
operation; lost response recovery reads Git before deciding what remains. Raw
publish commands are mechanical primitives, not an authorization or durability
boundary; do not bypass C with them during a managed run.

`reconcile-publication` reads `{operation,request,facts}` from stdin. `operation`
is the original publish-planning or complete-worktree, `request` is unchanged,
and `facts` is the exact saved ordered callback history. It does not mutate Git.
Results distinguish not-published, prepared, flow-advance-pending,
planning_published, cleanup-pending and completed. A matching publication requires
its exact two parents, candidate tree and presence on the target's first-parent
history; current target HEAD need not still be the merge. An uncertain result,
unmatched ancestry or unfinished merge blocks retry.

`cleanup-only` consumes the same transaction, requires a verified final
publication, and removes only remaining unchanged resources. Resource device/inode,
Git directory and branch ref/reflog evidence prevent deleting a replacement
Worktree or a recreated/moved branch, including a recreated branch at the same
commit. New files or changes are preserved. Cleanup uses non-forced worktree
removal and a conditional reference deletion, never pruning, force deletion or resetting. Publication
locks remain bounded and exclude cleanup; the original stopped-writer contract
and fresh identity/content checks apply at cleanup. The lock coordinates these
publishers, not arbitrary outside Git writers.

After Worktree removal and its durable callback, deletion uses `git update-ref
--stdin`: start, delete with the original candidate as old OID, verify each merge
reference, prepare, then commit. Only after prepare holds the branch and merge-ref
locks does it recheck original ref/reflog resource identity and deletion conditions.
A same-OID recreation before prepare fails the identity check; a competing ordinary
Git ref writer after prepare cannot acquire the lock. Conditions include candidate
merged into the existing upstream (or HEAD when unresolved/unset), the original
merge still on target, no other Worktree using the branch, and no active history
operation. History-operation checks conservatively include all registered/admin
worktrees, including detached rebase/bisect/sequencer state. Symbolic branch refs
are not deleted. Loose and packed refs use the same identity guard.

An uncommitted transaction is aborted by closing stdin; prepared locks belong to
Git and are released by its abort path. A lock conflict never removes another
process's lock. A timeout or lost acknowledgement remains cleanup-pending until
actual Git state is reconciled. After a committed deletion with a lost receipt,
absence is verified rather than issuing another deletion. A new same-name branch
or reflog is preserved and blocks completion. Unsupported transaction commands fail
closed. The implementation is exercised against Git 2.54.0 (Apple Git-157), using
the documented [reference transaction protocol](https://github.com/git/git/blob/v2.54.0/Documentation/git-update-ref.adoc)
and [non-force branch deletion conditions](https://github.com/git/git/blob/v2.54.0/builtin/branch.c).

Cleanup completion covers the bound Worktree, branch ref and reflog. Branch
configuration is deliberately preserved: this operation does not claim to clear
all branch settings, and never removes later user configuration.

C's `resume` invokes the same reconciler with a durable callback and the original
readiness authority. Proven unissued/unpublished work may run; prepared work may
finish its original merge; published work may only advance the planning Flow or
clean the final Flow. Clearing a recorded operation or changing its candidate to
force a retry is forbidden. V1 records are not migrated; run their original package.

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
