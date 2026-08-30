# Closure Protocol

Closure uses ordinary committed Git history. It has no proposal store,
auxiliary workflow state or a separate archival lifecycle.

The implementation merge is authoritative when the reported merge commit is on
the target branch and contains the accepted candidate. Stage 3 has already
removed the implementation worktree and branch.

Closure-document changes are a second, short-lived worktree change. Their
candidate must be clean, contain the expected target, and change only declared
closure paths. `complete-worktree` integrates and cleans that worktree in one
operation. A later target change yields `target_changed`; update the retained
closure worktree from the new target, review the document result, and retry.

No historical workflow artifact is migrated or interpreted. Git commits and
the current worktree binding are the complete evidence needed by this stage.
