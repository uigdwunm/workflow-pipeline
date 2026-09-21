# Closure Actions

1. For an attached discussion, enter the topic-local Stage-4 route in
   [`../shared/design-discussion/lifecycle-integration.md`](../shared/design-discussion/lifecycle-integration.md)
   before closure work. Standalone closure skips this step.
2. For an inherited flow, call `verify-worktree` and verify the accepted
   implementation candidate as its clean `HEAD`; for standalone closure, read
   the current target and create no worktree yet.
3. Read closure-owned documents from that verified state.
4. Decide exact document bytes and paths; stop on a new requirement or semantic
   conflict.
5. For an inherited flow, commit changed closure paths in the same Flow
   Worktree; create no closure commit when bytes do not change. Return the exact
   final candidate, artifacts/checks and stop writing. The stage owner authenticates
   the native stopped receipt and complete lifecycle barrier, records C `publication_candidate` and the original
   readiness decision, then consumes C `publication` to call `complete-worktree`
   for that candidate. This preserves Closure Agent responsibility for the final
   documentation candidate and publication request; C executes only its mechanical
   effects under the original Controller authority.
6. For standalone closure with changed bytes, call `start-worktree`, call
   `verify-worktree`, edit and commit only closure paths, then call
   `complete-worktree`. With no changed bytes, create no worktree or commit.
7. For the managed flow use C `reconcile-publication` to inspect uncertain results
   and `resume` to perform only remaining authorized effects. Use `receive-publication`
   to retain separate native and Git evidence sources and deliver the result to B;
   retain B's accepted implementation candidate rather than substituting the final
   documentation tip. Complete the original Controller acceptance.
   Verify final target and Flow Worktree cleanup. For an attached discussion, finish the
   topic-local Stage-4 route; then report local and remote outcomes.

An integration failure before merge preserves the Flow Worktree for
correction. A `cleanup_failed` result already contains a merge commit and must
be handled as cleanup, not retried as another merge.
