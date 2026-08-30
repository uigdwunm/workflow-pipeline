---
name: guided-implementation
description: Use when the user explicitly invokes $guided-implementation (3实现), gives an exact stage-entry confirmation to a valid upstream footer, continues from its verified continuous-flow handoff, invokes $guided-implementation 重试 in the same task after this Skill's retained-worktree footer, or the originating task receives the dedicated implementation task's terminal result. Create one dedicated Git worktree from committed planning sources, run the native $implement, $tdd and $code-review workflow there, independently accept one committed candidate, then integrate and clean it through the minimal worktree protocol. Never implement in the originating task or in the primary checkout.
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

- Accept only explicit invocation, exact `确认` or `执行后续全部流程` replying to a
  valid upstream success footer, that footer's verified same-turn continuous
  handoff, or exact `$guided-implementation 重试` in the same task after this
  Skill's immediately preceding retained-worktree footer. Punctuation, prefixes,
  suffixes, added conditions, `继续`, and `可以` do not enter this stage. On retry,
  verify the recorded binding and current Git state before acting; continue that
  worktree and never create a replacement.
- Track one `流程模式`. Explicit invocation and exact `确认` use `逐阶段确认`;
  exact `执行后续全部流程` or a verified upstream footer carrying
  `流程模式：连续执行后续全部流程` uses `连续执行后续全部流程`. Preserve it through
  launch, remediation, retry, completion and the Stage-4 handoff.
- Resolve the target branch, committed requirement/Spec/ADR/Ticket paths, and
  allowed implementation paths. Stage 3 must pass at least one source path, and
  every source path must exist at the target HEAD.
- Commit tracked primary-checkout changes before starting. Untracked files stay
  outside the new worktree and are never copied, staged, stashed, or removed.
- The originating task supervises and reviews. It never edits implementation
  files.

## Attach a discussion Phase Run when present

When entry carries one exact discussion topic and Phase Run, read
[`../design-discussion/references/lifecycle-integration.md`](../design-discussion/references/lifecycle-integration.md)
and preserve its actor boundary. The Stage-3 carrier verifies the checkpoint,
calls `claim-phase-carrier` and `phase-ready`, and waits for source-side
`phase-activate` before substantive work. After a verified merge it calls
`claim-phase-completion`; the source task calls `complete-phase-run` and
`finalize-phase-run` before Stage 4 begins. Standalone entry creates no Phase
Run and performs none of these calls.

## Start

Call `start-worktree` once with the canonical primary checkout, new worktree
path, new branch, target branch, sorted non-empty source paths, and sorted
allowed paths. The command creates the branch and worktree from the exact target
HEAD and returns their plain Git binding. It creates no lease, claim, queue
entry, or mutable run record.

Create one dedicated implementation task in that returned worktree. Pass the
binding, planning sources, ordered Tickets, testing basis, flow mode and exact
authority boundaries in its prompt. The dedicated task must call
`verify-worktree` with its actual platform working directory before substantive
work.

## Implement and review

- Read every Ticket completely, topologically sort `Blocked by`, preserve source
  document order among simultaneously ready Tickets, and implement them in that
  order.
- Work only inside the verified worktree. Invoke the complete native
  `$implement` workflow, including `$tdd`, focused and full checks, typecheck,
  lint or build where applicable, and `$code-review` on the committed candidate.
- Implementation commits contain no documentation. Commit only task-owned code,
  tests and required implementation artifacts. Report exact documentation paths
  and required updates as `待归档文档`; Stage 4 owns those edits and commits.
- Do not modify the committed planning-source paths.
- The dedicated task never asks the user directly. It returns material decisions
  to the originating task, which asks the user when needed. Review or test
  remediation returns to the same dedicated task and worktree.
- Local stage entry grants no push, pull request, deployment, release, tracker or
  other remote write. Each such action requires separate explicit authority.
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

For a failure that published no merge and retained the worktree, emit the
retained-worktree footer from `worktree-execution.md` with recovery command
`$guided-implementation 重试`. Do not emit it after a verified merge or for a
cleanup-only failure.

After success, emit a complete Stage-4 handoff:

```text
实现结果：成功
合并结果：成功
专用任务：<thread id and host id>
目标仓库：<absolute repository path>
目标分支：<target branch>
实现依据：<committed source paths and commit>
实现提交：<candidate commit>
合并提交：<merge commit>
验证：<focused and full checks>
审查：<Standards and Spec review result>
待归档文档：<exact paths and required updates | none>
清理结果：implementation worktree and branch removed
流程模式：<逐阶段确认 | 连续执行后续全部流程>
远程操作：<separately authorized results | none>
下一阶段：`$change-closure`（4归档）
进入条件：已满足
继续方式：逐阶段确认时回复 `确认`；连续执行后续全部流程时同一轮立即进入 4归档
```

In `逐阶段确认`, stop after this footer; only exact `确认` enters Stage 4. In
`连续执行后续全部流程`, emit the footer and invoke `$change-closure` in the same
turn. A blocker, failed verification or material decision always stops either
mode.
