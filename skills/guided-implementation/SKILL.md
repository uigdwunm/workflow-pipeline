---
name: guided-implementation
description: Use when the user explicitly invokes $guided-implementation (3实现), confirms entry from a valid upstream footer, resumes this Skill's exact worktree footer, or the originating task receives a verified terminal result. Create one dedicated Git worktree from committed planning sources, supervise implementation there, independently review one committed candidate, then integrate and clean it through the minimal worktree protocol. Never implement in the originating task or in the primary checkout.
---

# 3实现

Every implementation uses one dedicated Git worktree and branch. Read
[references/worktree-execution.md](references/worktree-execution.md),
[references/originating-task-protocol.md](references/originating-task-protocol.md), and
[references/execution-protocol.md](references/execution-protocol.md) before the
corresponding action. Read
[references/thread-settings-protocol.md](references/thread-settings-protocol.md)
immediately before resolving task settings.

## Entry

- Accept only explicit invocation, an exact upstream confirmation, a verified
  continuous-flow footer, or the exact retained worktree after an integration
  failure.
- Resolve the target branch, committed Spec/ADR/Ticket paths, and allowed
  implementation paths. Every source path must exist at the target HEAD.
- Commit tracked primary-checkout changes before starting. Untracked files stay
  outside the new worktree and are never copied, staged, stashed, or removed.
- The originating task supervises and reviews. It never edits implementation
  files.

## Start

Call `start-worktree` once with the canonical primary checkout, new worktree
path, new branch, target branch, sorted source paths, and sorted allowed paths.
The command creates the branch and worktree from the exact target HEAD and
returns their plain Git binding. It creates no lease, claim, queue entry, or
mutable run record.

Create one dedicated implementation task in that returned worktree. Pass the
binding in its prompt. The dedicated task must call `verify-worktree` with its
actual platform working directory before substantive work.

## Implement and review

- Work only inside the verified worktree.
- Commit every task-owned code, test, and implementation-document change.
- Do not modify the committed planning-source paths.
- Run focused and full checks, then perform independent Standards and Spec
  review against the candidate commit.
- Return the exact candidate commit, changed paths, checks, review findings, and
  remaining risks. Text claims never replace the originating task's Git checks.

If the target branch advances, inspect the committed changes since the binding
base. A changed source path stops for user direction. Otherwise merge the new
target HEAD into the implementation worktree, repair ordinary conflicts there,
rerun affected checks and both review axes, and use the resulting new HEAD as
the candidate.

## Complete

The originating task calls `complete-worktree` with the exact binding,
candidate, and current expected target HEAD. The command takes one internal
short publication lock, rechecks target and source commits, verifies that the
candidate contains the expected target and changes only allowed paths, creates
one no-fast-forward merge commit, and removes the clean worktree and merged
branch before returning success.

A pre-merge failure leaves the worktree and branch intact. A cleanup failure
reports the verified merge commit and the exact remaining resource; do not call
the implementation unsuccessful after its merge is already present.

After success, route only the merge result to Stage 4:

```text
实现结果：成功
合并结果：成功
实现提交：<candidate commit>
合并提交：<merge commit>
清理结果：implementation worktree and branch removed
下一阶段：`$change-closure`（4归档）
```
