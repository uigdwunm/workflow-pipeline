---
name: change-closure
description: Use when the user explicitly invokes $change-closure (4归档), confirms entry from a successful $guided-implementation footer, automatically continues from a verified continuous-flow footer, or invokes $change-closure 重试 in the same task after this Skill's retained-worktree footer. Reuse the inherited Flow Worktree, commit closure-owned planning updates there, then publish implementation and closure together and clean the flow; standalone closure creates a short-lived documentation worktree only when needed.
---

# 4归档

Stage 4 never repairs implementation code. Read
[references/closure-actions.md](references/closure-actions.md) and
[references/closure-protocol.md](references/closure-protocol.md) before acting.
Before verifying, creating, completing, or recovering a Flow Worktree, also read
[the shared worktree execution contract](../guided-implementation/references/worktree-execution.md).

## Enter

For a Stage-3 transition, require the exact repository, target branch, accepted
candidate, planning merge or standalone base commit, inherited Flow Worktree
binding, verification summary, review result, implementation path scope,
protected requirement-source paths, and closure-owned document paths. Verify
from Git that the clean inherited Flow Worktree `HEAD` is the accepted candidate
and record the target's current `HEAD`; the target-advance path below applies
when it differs from the handed-off publication base.

An explicit `$change-closure` invocation starts stepwise closure. A transition
from Stage 3 requires its complete handoff and either exact `确认` in
`逐阶段确认` or the verified field `流程模式：连续执行后续全部流程` for same-turn
entry. Punctuation, added conditions, `继续`, and `可以` do not enter closure.

If that handoff carries `讨论上下文：attached`, require its exact discussion
identity, actor binding, phase-3 result id and ledger/topic revisions. Read
[`../design-discussion/references/lifecycle-integration.md`](../design-discussion/references/lifecycle-integration.md)
and execute its topic-local Stage-4 route around the ordinary closure work.
Standalone Stage-4 entry performs no discussion discovery or protocol calls.
It claims no inherited implementation candidate or Flow Worktree.

Accept exact `$change-closure 重试` only in the same task after this Skill's
immediately preceding retained-worktree footer. Verify the recorded binding and
current Git state, then continue that Flow Worktree and the same active
Phase Run attempt without creating a replacement.

## Update documents

For an inherited flow, read closure-owned documents in the verified Flow
Worktree after the accepted implementation candidate. Update only
closure-owned Spec/Ticket/ADR/CONTEXT or tracker paths. A new requirement is not
a closure update and requires a separate planning decision.

Preserve each document's established representation. Invoke `$ask-matt` only
when the representation inside an existing closure-owned document is genuinely
unclear. Use its answer only to choose that representation; the accepted Stage-3
review remains authoritative and closure creates no new workflow artifact.

For an inherited flow, never create a second worktree. If document bytes change,
commit every closure-owned update in the inherited Flow Worktree. If no bytes
change, create no closure commit. In both cases call `complete-worktree` with
the clean final candidate, handed-off scope base, current expected target,
combined implementation and closure allowed paths, and protected requirement
sources. This publishes the accepted implementation plus closure updates and
removes the Flow Worktree and branch.

If the target advanced, inspect the committed delta. A changed protected source
or material semantic conflict requires user direction. Otherwise return the
same Flow Worktree to the existing implementation task to merge the target and
rerun affected/full checks, then have the Originating Task rerun both review
axes for the replacement candidate. Recheck any closure-document result and
retry final completion. Stage 4 never repairs implementation code itself.

For explicit standalone Stage 4, read documents from the current target. If
bytes change, call `start-worktree` for one short-lived documentation worktree,
call `verify-worktree`, edit and commit only the declared closure paths, then
call `complete-worktree`. If no bytes change, create no worktree or empty
commit.

For a failure that published no merge and retained the Flow Worktree,
emit the shared contract's retained-worktree footer with recovery command
`$change-closure 重试`. Do not emit it after a verified merge or for a cleanup-only
failure.

## Finish

Verify the final target HEAD, preserved primary-checkout state, and absence of
the completed Flow Worktree and branch. For an attached discussion, finish and
verify the `3→4` run only after those Git checks succeed, including the
no-document-change path. Local closure
grants no push, tracker update or other remote write; each requires separate
explicit authority. Report remote operations exactly; local completion never
implies a push.

Before the originating task reports the overall request complete, perform one
brief overall completion recheck in every flow mode. Review the user's request,
the work promised by the primary flow, and this task's execution record. Confirm
that every requested or promised item has an explicit completed outcome and that
nothing remains in progress, pending, identified as a next step, or otherwise
unhandled. A successful stage result proves only that stage; it never proves the
overall request complete by itself. If this recheck finds remaining work,
continue it within the existing authority or report the actual incomplete item
or blocker. Emit the success footer only after the recheck passes.

```text
归档结果：成功
整体复检：通过（用户要求和主流程承诺的工作均已完成，无待处理事项）
实现提交：<accepted candidate>
归档提交：<closure candidate commit | none>
最终合并：<merge commit | none when standalone and unchanged>
流程清理：Flow Worktree and branch removed | standalone worktree removed | not created
讨论阶段：<current_phase 4 | standalone>
远程操作：<performed actions | none>
```
