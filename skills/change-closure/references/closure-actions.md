# Closure Actions

1. Verify the reported implementation merge and candidate ancestry from Git.
2. Read closure-owned documents at the current target HEAD.
3. Decide exact document bytes and paths; stop on a new requirement or semantic
   conflict.
4. When bytes change, call `start-worktree` for a new closure branch/worktree,
   edit and commit only those paths, then call `complete-worktree` with the
   current target HEAD.
5. When bytes do not change, create no worktree and no commit.
6. Verify final target and cleanup, then report local and remote outcomes.

An integration failure before merge preserves the documentation worktree for
correction. A `cleanup_failed` result already contains a merge commit and must
be handled as cleanup, not retried as another merge.
