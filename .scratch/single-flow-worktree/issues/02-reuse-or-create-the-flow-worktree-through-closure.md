# 02 — Reuse or create the Flow Worktree through closure

Status: ready-for-agent

## What to build

Make Stage 3 reuse a valid Stage-2 Flow Worktree, create one only for a genuine
standalone Stage-3 entry, and retain the same worktree for Stage 4. Move final
implementation-and-closure publication and non-force cleanup to Stage 4 while
preserving the existing implementation, review, remediation, and recovery
contracts.

## Acceptance criteria

- [ ] Stage 3 reuses a valid upstream Worktree Binding and does not create a second worktree.
- [ ] An explicit standalone Stage 3 or direct Stage-1-to-Stage-3 route with committed sources creates exactly one Flow Worktree.
- [ ] A handoff that claims an upstream worktree but supplies a missing or invalid binding stops as an anomaly without creating a replacement.
- [ ] `$guided-implementation 重试` and ordinary remediation continue the exact retained task, branch, and worktree.
- [ ] Stage 3 preserves planning sources, Ticket order, TDD, focused/full checks, Originating Task review ownership, exact-candidate evidence, and same-task remediation.
- [ ] Stage 3 completion reports the accepted candidate and retained binding without claiming target integration or cleanup.
- [ ] Stage 4 reuses the upstream Flow Worktree, writes only closure-owned documents when needed, creates no empty closure commit, and owns final publication and cleanup.
- [ ] `$change-closure 重试` reuses the same retained Flow Worktree after a pre-merge failure, while a verified merge with cleanup remaining is not republished.
- [ ] Standalone Stage 4 without an upstream Flow Worktree preserves its current behavior.
- [ ] Contract and protocol tests cover inherited, standalone, invalid-binding, retry, no-document-change, final-merge, and cleanup-failure paths.

Blocked by: 01 — Create and publish the Stage-2 Flow Worktree.

