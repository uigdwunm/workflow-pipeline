# Closure Actions

1. For an attached discussion, enter the topic-local Stage-4 route in
   [`lifecycle-integration.md`]({{resource:design-discussion/references/lifecycle-integration.md}})
   before closure work. Standalone closure skips this step.
2. For an inherited flow, call `verify-worktree` and verify the accepted
   implementation candidate as its clean `HEAD`; for standalone closure, read
   the current target and create no worktree yet.
3. Read closure-owned documents from that verified state.
4. Decide exact document bytes and paths; stop on a new requirement or semantic
   conflict.
5. For an inherited flow, commit changed closure paths in the same Flow
   Worktree; create no closure commit when bytes do not change. Then call
   `complete-worktree` once for the final candidate in either case.
6. For standalone closure with changed bytes, call `start-worktree`, call
   `verify-worktree`, edit and commit only closure paths, then call
   `complete-worktree`. With no changed bytes, create no worktree or commit.
7. Verify final target and Flow Worktree cleanup. For an attached discussion, finish the
   topic-local Stage-4 route; then report local and remote outcomes.

An integration failure before merge preserves the Flow Worktree for
correction. A `cleanup_failed` result already contains a merge commit and must
be handled as cleanup, not retried as another merge.
