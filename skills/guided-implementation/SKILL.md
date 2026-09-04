---
name: guided-implementation
description: Use when the user unambiguously confirms a valid $solution-design handoff, continues from its verified continuous-flow handoff, invokes $guided-implementation 重试 after this Skill's retained-worktree footer, or the originating task receives the dedicated implementation task's terminal result. Require and reuse the verified Phase-2 Flow Worktree and planning artifacts, run native $implement and $tdd there, and retain the accepted candidate for stage 4.
---

# 3实现

Every implementation uses one Flow Worktree and branch. Read
[references/worktree-execution.md](references/worktree-execution.md),
[references/originating-task-protocol.md](references/originating-task-protocol.md), and
[references/execution-protocol.md](references/execution-protocol.md) before the
corresponding action. Read
[references/thread-settings-protocol.md](references/thread-settings-protocol.md)
immediately before resolving task settings.
Interpret user replies through
[`../design-discussion/references/confirmation-contract.md`](../design-discussion/references/confirmation-contract.md).

## Entry

- Accept an unambiguous stepwise or continuous confirmation replying to a valid
  Phase-2 success footer, that footer's verified same-turn continuous handoff,
  or an unambiguous retry request (including `$guided-implementation 重试`) in the same task after this Skill's
  immediately preceding retained-worktree footer. A standalone invocation with
  no verified Phase-2 handoff does not enter implementation. On retry,
  verify the recorded binding and current Git state before acting; continue that
  worktree and never create a replacement.
- Track one `流程模式`. An ordinary unambiguous affirmation uses `逐阶段确认`;
  a clear request for automatic remaining execution or a verified upstream footer carrying
  `流程模式：连续执行后续全部流程` uses `连续执行后续全部流程`. Preserve it through
  launch, remediation, retry, completion and the Stage-4 handoff.
- Resolve the target branch, committed Spec/ADR/Ticket paths, protected
  requirement-draft path, and allowed implementation paths. Every protected source path must exist at the
  implementation scope base.
- Verify and reuse the Flow Worktree supplied by Stage 2. If the binding is
  missing, invalid, or points elsewhere, stop as an anomaly and do not create a
  replacement. Retry always reuses the retained binding.
- Uncommitted primary-checkout files stay outside the Flow Worktree and are
  never copied, staged, stashed, or removed.
- The originating task supervises and reviews. It never edits implementation
  files.

## Enforce the confirmed implementation boundary

Treat the committed Spec, ADR, Tickets, and exact Stage-2 handoff as the
complete implementation authority. Keep the requirement draft protected and
read-only, but do not use it to add or reinterpret implementation scope.
Implement a robust, coherent result
inside that boundary; the goal is not merely the smallest diff. Do not infer
authority from nearby code, an attractive refactor, or a possible future need.

Stage 3 should start with no unresolved material product, scope, behavior,
architecture, compatibility, data, or testing-seam decision. Ordinary technical
choices may be resolved from the accepted sources and repository conventions
when they do not change the confirmed behavior or scope.

Do not add an unplanned feature, business rule, configuration surface,
user-visible behavior, or side effect. Do not remove, replace, or change
existing behavior unless the confirmed plan makes that effect explicit. Leave
optional adjacent improvements untouched and report them as out of scope
instead of implementing them or turning them into blocking questions.

If an unexpected implementation fact makes a material expansion or unconfirmed
behavior change unavoidable, stop before the affected edit and return the exact
gap to the originating task. Treat the question as a planning omission, obtain
an explicit user decision through the originating task, and resume only with
updated authority. Never make the change first and disclose it at completion.

## Attach a discussion Phase Run when present

When entry carries one exact discussion topic and Phase Run, read
[`../design-discussion/references/lifecycle-integration.md`](../design-discussion/references/lifecycle-integration.md)
and preserve its actor boundary. The Stage-3 carrier verifies the checkpoint,
calls `claim-phase-carrier` and `phase-ready`, and waits for source-side
`phase-activate` before substantive work. After an accepted candidate in the
verified retained Flow Worktree it calls
`claim-phase-completion`; the source task calls `complete-phase-run` and
`finalize-phase-run`, then calls `read-topic` before Stage 4 begins. The read
must prove the same active, open topic is owned by the current task, has no
pending document write and is at `current_phase: 3`. Carry that exact discussion
identity, binding, Phase Result and the returned ledger/topic revisions in the
Stage-4 handoff. An unattached Stage-2 handoff creates no discussion Phase Run,
performs none of these calls and carries `none` for every discussion field.

## Bind the Flow Worktree

For a valid Stage-2 handoff, call `verify-worktree` with its exact binding and
use that Flow Worktree without creating another one. The worktree must be clean
at the reported planning merge commit. Stage 3 never calls `start-worktree` and
never creates a lease, claim, queue entry, or mutable run record.

Every entry must reuse the inherited Flow Worktree. An invalid binding must not
silently create a replacement.

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
  lint or build where applicable, and commit a clean candidate.
- The Dedicated Implementation Task must not dispatch Standards or Spec review
  agents or receive the parent Session or CLI. It reports the exact candidate
  commit, changed paths, focused and full checks, remaining risks, and any
  remediation candidate commits from the same verified worktree.
- Implementation commits contain no documentation. Commit only task-owned code,
  tests and required implementation artifacts. Report exact documentation paths
  and required updates as `待归档文档`; Stage 4 owns those edits and commits.
- Do not modify the committed planning-source paths.
- The dedicated task never asks the user directly. It resolves ordinary
  technical details from the confirmed sources and repository conventions. An
  unexpected material decision is a planning gap: return it to the originating
  task before the affected edit, and let that task obtain the user's explicit
  decision. Review or test remediation returns to the same dedicated task and
  worktree.
- Local stage entry grants no push, pull request, deployment, release, tracker or
  other remote write. Each such action requires separate explicit authority.
- The Originating Task owns `$code-review` and candidate acceptance. Stage 4
  owns final integration and cleanup. The candidate, review, and remediation
  contract is authoritative in
  [references/originating-task-protocol.md](references/originating-task-protocol.md).

If the target branch advances, inspect the committed changes since the binding
base. A changed source path stops for user direction. Otherwise merge the new
target HEAD into the Flow Worktree, repair ordinary conflicts there,
rerun affected checks, and commit the replacement candidate. The Originating
Task handles its review under the authoritative role contract.

## Hand off the retained Flow Worktree

After both review axes accept the exact clean candidate, Stage 3 does not merge
the implementation into the target and does not remove the worktree or branch.
It passes the retained Flow Worktree, candidate, planning merge commit,
implementation and closure path scopes, protected requirement-source paths,
and review evidence to Stage 4. Stage 4 owns the final publication and cleanup.

For a Stage-3 failure with the worktree retained, emit the retained-worktree
footer from `worktree-execution.md` with recovery command
`$guided-implementation 重试`. A successful Stage-3 handoff is not a failure and
uses the success footer below.

After success, emit a complete Stage-4 handoff:

```text
实现结果：成功
合并结果：待 4归档统一完成
专用任务：<thread id and host id>
目标仓库：<absolute repository path>
目标分支：<target branch>
实现依据：<committed source paths and commit>
实现提交：<candidate commit>
方案合并提交：<planning merge commit>
Flow Worktree：<exact retained binding>
验证：<focused and full checks>
审查：<Standards and Spec review result>
待归档文档：<exact paths and required updates | none>
清理结果：Flow Worktree and branch retained for Stage 4
流程模式：<逐阶段确认 | 连续执行后续全部流程>
讨论上下文：<unattached | attached>
讨论身份：<project path, project id, tree id and topic id | none>
讨论绑定：<actor conversation ref | none>
阶段 3 结果：<phase result id | none>
讨论版本：<ledger revision and topic revision at current_phase 3 | none>
远程操作：<separately authorized results | none>
下一阶段：`$change-closure`（4归档）
进入条件：已满足
继续方式：逐阶段确认时明确同意上述单一待执行事项；连续执行后续全部流程时同一轮立即进入 4归档
```

In `逐阶段确认`, stop after this footer; an unambiguous affirmation enters Stage 4. In
`连续执行后续全部流程`, emit the footer and invoke `$change-closure` in the same
turn. A blocker, failed verification or material decision always stops either
mode.
