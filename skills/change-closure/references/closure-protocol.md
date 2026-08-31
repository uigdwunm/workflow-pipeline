# Closure Protocol

Closure uses ordinary committed Git history. It owns no proposal store,
auxiliary workflow state or separate archival lifecycle. When Stage 3 carries
an attached discussion topic, closure participates in that topic's existing
Phase Run only to record the `3→4` lifecycle transition; it does not copy Git or
document-closure facts into the discussion ledger.

The implementation merge is authoritative when the reported merge commit is on
the target branch and contains the accepted candidate. Stage 3 has already
removed the implementation worktree and branch.

Closure-document changes are a second, short-lived worktree change. Their
candidate must be clean, contain the expected target, and change only declared
closure paths. `complete-worktree` integrates and cleans that worktree in one
operation. A later target change yields `target_changed`; update the retained
closure worktree from the new target, review the document result, and retry.

Git remains authoritative for implementation ancestry, closure commits,
worktrees and cleanup. The Phase Run remains authoritative only for route
authorization, the active attempt and `current_phase`.

If a pre-merge failure retains the documentation worktree, keep the same Phase
Run attempt active; `$change-closure 重试` continues both. If a known failure
has no external side effect and retains no worktree, call `fail-phase-run`; a
later authorized retry uses `retry-phase-run`. If the Git outcome is uncertain,
call `phase-outcome-unknown`, reconcile from Git, then call
`reconcile-phase-run` with the proven result.

Once Git closure is proven complete, including the no-document-change path,
recover only the remaining Phase Run calls after a discussion-ledger failure
and never repeat the Git closure. A verified documentation merge with cleanup
remaining is likewise cleaned rather than merged again. Explicit user
cancellation cancels the active Phase Run and leaves the topic at phase 3.
