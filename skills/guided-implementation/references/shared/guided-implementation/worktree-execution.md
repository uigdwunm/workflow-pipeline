# Worktree Execution

`supervision_protocol.py` validates and publishes one bound workspace. One Flow
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

## Select and bind one workspace

Use this sequence for Stage 2 and standalone Stage 3/4; inherited stages and
retries verify their exact original binding. An invalid inherited binding is a
recovery condition, not permission to create a replacement.

1. Inspect the current independent worktree, then suitable attached worktrees.
   Check Git identity, baseline, branch, task ownership and active writers. A clean
   directory alone does not prove it is free. Preserve unrelated work; choose another
   workspace when a different task owns it. Ask only if ownership of this task's
   existing work cannot be resolved from evidence.
2. Reuse a suitable workspace. When none is available, use the host's managed
   creation tool with the exact target commit. Record its actual result in the
   existing Controller checkpoint. An uncertain result requires inspecting the
   original operation and attachments before another create. For a detached return,
   create the task branch only after exclusivity and state checks; preserve HEAD.
3. Call `bind-worktree` after any required source preparation and target delivery.
   It reads `{repository,target_branch,worktree,branch,base_commit,allowed_paths,
   lifecycle}` and returns the verified binding. `allowed_paths` accounts for all
   existing task commits since the explicit base, including each incoming commit's
   paths and merge deltas, not only the final tree diff. A change later reverted still
   belongs to incoming history; never choose a newer base to hide it. The registered worktree must share the Git common directory, be separate
   from the target checkout, and contain no unrelated history or ambiguous edits.
   At Stage 2, pass only stage-owned planning paths as `allowed_paths`; the frozen
   requirement is protected even when its source commit has identical bytes to the
   delivered target. A separately delivered source checkout is unsuitable while
   its freeze commit is outside target history; preserve it and select another
   workspace or create one from the verified target before synchronizing any
   candidate. Treat an unbound workspace rejected for that verified split or
   incoming protected history as unsuitable. Stage-2 handoff checks the protected
   history again before the designer's first write.
4. Only when the host capability is unavailable and applicable rules allow it, or
   the user explicitly requests script creation, use `start-worktree`. The Controller
   chooses this mechanical route under existing authority; it does not ask the user
   to resolve an internal tool preference.

`lifecycle` has exactly `origin` (`created` or `reused`), `manager` (`host` or
`git`), `disposition` (`retain` or `delete`), `evidence_ref` and `creation_receipt`.
The evidence reference points to actual selection and host evidence saved in the
existing Controller checkpoint, not a caller's exclusivity assertion.
For initial C `start` with a retained workspace, supply `workspace_evidence` as
`{controller_ref,reference,worktree,receipts:[{tool,request,response}]}`. Keep the
actual tool requests and raw responses covering selection and occupancy; the
reference and path must match the binding. C persists it at
`workflow_progress.workspace_evidence` and carries it unchanged through stages
and retries. Authentication and the suitability decision belong to the trusted
host/Controller; Git cannot prove another conversation is idle. Script-created
workspaces use the exact creation receipt returned in their binding, persisted
in that same checkpoint slot. Raw supervision CLI inputs do not grant authority.
`bind-worktree` accepts only `retain` and a null creation receipt. Host-created
and reused workspaces retain both worktree and branch. `start-worktree` supplies
`created/git/delete` and an immutable Git resource identity receipt; preserve the
whole binding. No new ownership registry is introduced. Missing lifecycle evidence
cannot authorize deletion. Later host archival is a separate host operation, not a
condition of workflow completion.

For a clean workspace behind the target, synchronize the target before binding
when its updates do not conflict with task-owned work; preserve task commits and
inspect real conflicts through the existing decision path. The immutable binding
does not authorize discarding content to make a workspace eligible.

The foreground runner freezes the actual selected workspace path in confirmed
input. Select/reuse or host-create it before starting the runner, and retain
`workspace_evidence` in confirmed input for C's first Stage-2 start. A host-returned
path cannot be substituted midway through a running record. An explicitly
authorized script fallback may reserve its exact not-yet-created path; the
Stage-2 carrier then creates that path once and records the returned binding.

## `start-worktree`

Input fields are `repository`, `worktree`, `branch`, and `target_branch`. The
primary checkout must be on `target_branch`, and its `HEAD` must equal that
branch. Uncommitted primary-checkout changes do not block creation and are not
copied, staged, stashed, or removed. The command resolves the exact target
`HEAD` and runs `git worktree add -b` from that object ID.

Use only the script-creation fallback above, after the applicable stage authority
gate. Stage 2 binds before the designer's first planning write. Stage 3 and 4
inherit that binding unchanged. Retry always reuses the original binding.

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
updates, then verifies the binding's disposition. `retain` preserves and verifies
the same worktree and branch with the candidate included in the target; it does
not reset the branch. `delete` removes the script-created temporary worktree without
force and deletes its unchanged merged branch through the guarded transaction below.
The cleanup result records `disposition` (`retained` or `deleted`),
`disposition_verified:true`, and actual `worktree_removed` and `branch_removed`
booleans. Completion requires verified delivery and disposition, not unconditional
absence of resources. The operation also returns candidate, merge and changed paths.

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

## Exact publication recovery (flow-worktree-v3)

The stage owner uses workflow-progress-v18 for durable publication. The Python
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
publication, and completes only the original disposition. A retained binding is
reverified without deletion; recovery cannot change retain to delete. For a delete
binding it removes only remaining unchanged resources. Resource device/inode,
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
