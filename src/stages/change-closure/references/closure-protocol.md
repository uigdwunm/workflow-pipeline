# Closure Protocol

Before this role acts, execute [Package execution preflight]({{resource:shared/references/package-execution.md}}) using this child’s current effective registry. Inherit and verify the controller’s fixed package identity and check each selected action before its side effects.

Closure uses ordinary committed Git history. It owns no proposal store,
auxiliary workflow state or separate archival lifecycle. When Stage 3 carries
an attached discussion topic, closure participates in that topic's existing
Phase Run only to record the `3→4` lifecycle transition; it does not copy Git or
document-closure facts into the discussion ledger.

For an inherited flow, Stage 3 has accepted the implementation candidate but
has not published or cleaned it. Its clean Flow Worktree and Git binding are the
authoritative execution state. Closure-document changes are additional commits
in that same worktree. `complete-worktree` integrates the final combined
candidate and cleans the Flow Worktree in one operation. A later target change
yields `target_changed`; the same Implementation Dispatcher integrates the
new target and reruns affected/full checks, the Originating Task reruns both
review axes, and Stage 4 rechecks closure-owned documents before retrying.

Standalone closure retains its previous narrow behavior: create one short-lived
documentation worktree only when document bytes change, integrate it with the
same operation, and otherwise create nothing.

Git remains authoritative for implementation ancestry, closure commits,
worktrees and cleanup. The Phase Run remains authoritative only for route
authorization, the active attempt and `current_phase`.

If a pre-merge failure retains the Flow Worktree, keep the same Phase
Run attempt active; `$change-closure 重试` continues both. If a known failure
has no external side effect and retains no worktree, call `fail-phase-run`; a
later authorized retry uses `retry-phase-run`. If the Git outcome is uncertain,
call `phase-outcome-unknown`, reconcile from Git, then call
`reconcile-phase-run` with the proven result.

Once Git closure is proven complete, including the inherited no-document-change path,
recover only the remaining Phase Run calls after a discussion-ledger failure
and never repeat the Git closure. A verified documentation merge with cleanup
remaining is likewise cleaned rather than merged again. Explicit user
cancellation cancels the active Phase Run and leaves the topic at phase 3.

The Closure Agent writes only the supplied documentation scope. The Workflow Controller uses workflow_control_git.py to verify candidate ancestry, changed paths and actual worktree/branch cleanup. Code defects return to stage 3. Partial cleanup retains the verified merge and uses cleanup-only, never another publication.
