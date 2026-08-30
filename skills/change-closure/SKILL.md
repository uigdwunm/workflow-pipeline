---
name: change-closure
description: Use when the user explicitly invokes $change-closure (4归档), confirms entry from a successful $guided-implementation footer, automatically continues from a verified continuous-flow footer, or resumes this Skill's exact documentation-worktree failure. Verify the integrated commit, update only closure-owned planning documents in a short-lived worktree, commit and integrate them through the same minimal worktree protocol, then report final local and remote state.
---

# 4归档

Stage 4 never repairs implementation code. Read
[references/closure-actions.md](references/closure-actions.md) and
[references/closure-protocol.md](references/closure-protocol.md) before acting.

## Enter

Require the exact repository, target branch, accepted candidate, merge commit,
verification summary, review result, and closure-owned document paths. Verify
from Git that the target contains the candidate through the reported merge.

## Update documents

Read current target-branch documents after implementation integration. Update
only closure-owned Spec/Ticket/ADR/CONTEXT or tracker paths. A new requirement
is not a closure update and requires a separate planning decision.

If document bytes must change, create a short-lived documentation worktree with
the same `start-worktree` operation, using the closure paths as `allowed_paths`
and no implementation `source_paths`. Commit every document change in that
worktree and call `complete-worktree`. This uses the same expected-target check,
short publication lock, merge, and cleanup as Stage 3.

If no document change is needed, do not create a worktree or empty commit.

## Finish

Verify the final target HEAD, clean tracked primary checkout, absence of the
closure worktree and branch, and the implementation result already cleaned by
Stage 3. Report remote operations exactly; local completion never implies a
push.

```text
归档结果：成功
归档提交：<documentation merge commit | none>
实现清理：Stage 3 已验证
归档清理：documentation worktree and branch removed | not created
远程操作：<performed actions | none>
```
