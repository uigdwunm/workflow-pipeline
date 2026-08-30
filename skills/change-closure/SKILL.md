---
name: change-closure
description: Use when the user explicitly invokes $change-closure (4归档), confirms entry from a successful $guided-implementation footer, automatically continues from a verified continuous-flow footer, or invokes $change-closure 重试 in the same task after this Skill's retained-worktree footer. Verify the integrated commit, update only closure-owned planning documents in a short-lived worktree, commit and integrate them through the same minimal worktree protocol, then report final local and remote state.
---

# 4归档

Stage 4 never repairs implementation code. Read
[references/closure-actions.md](references/closure-actions.md) and
[references/closure-protocol.md](references/closure-protocol.md) before acting.
Before creating, completing, or recovering a documentation worktree, also read
[the shared worktree execution contract](../guided-implementation/references/worktree-execution.md).

## Enter

Require the exact repository, target branch, accepted candidate, merge commit,
verification summary, review result, and closure-owned document paths. Verify
from Git that the target contains the candidate through the reported merge.

An explicit `$change-closure` invocation starts stepwise closure. A transition
from Stage 3 requires its complete handoff and either exact `确认` in
`逐阶段确认` or the verified field `流程模式：连续执行后续全部流程` for same-turn
entry. Punctuation, added conditions, `继续`, and `可以` do not enter closure.

Accept exact `$change-closure 重试` only in the same task after this Skill's
immediately preceding retained-worktree footer. Verify the recorded binding and
current Git state, then continue that documentation worktree without creating a
replacement.

## Update documents

Read current target-branch documents after implementation integration. Update
only closure-owned Spec/Ticket/ADR/CONTEXT or tracker paths. A new requirement
is not a closure update and requires a separate planning decision.

Preserve each document's established representation. Invoke `$ask-matt` only
when the representation inside an existing closure-owned document is genuinely
unclear. Use its answer only to choose that representation; the accepted Stage-3
review remains authoritative and closure creates no new workflow artifact.

If document bytes must change, create a short-lived documentation worktree with
the same `start-worktree` operation, using the closure paths as `allowed_paths`
and no implementation `source_paths`. Before editing, call `verify-worktree`
with the returned binding and that worktree as the actual working directory.
Commit every document change in that verified worktree and call
`complete-worktree`. This uses the same expected-target check, short publication
lock, merge, and cleanup as Stage 3.

If no document change is needed, do not create a worktree or empty commit.

For a failure that published no merge and retained the documentation worktree,
emit the shared contract's retained-worktree footer with recovery command
`$change-closure 重试`. Do not emit it after a verified merge or for a cleanup-only
failure.

## Finish

Verify the final target HEAD, clean tracked primary checkout, absence of the
closure worktree and branch, and the implementation result already cleaned by
Stage 3. Local closure grants no push, tracker update or other remote write;
each requires separate explicit authority. Report remote operations exactly;
local completion never implies a push.

```text
归档结果：成功
归档提交：<documentation merge commit | none>
实现清理：Stage 3 已验证
归档清理：documentation worktree and branch removed | not created
远程操作：<performed actions | none>
```
