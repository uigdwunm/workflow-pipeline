---
name: guided-implementation
description: Use when the user explicitly invokes $guided-implementation with a self-contained implementation request, unambiguously confirms a valid $solution-design handoff or Stage-3 recovery action, continues from its verified continuous-flow handoff, retries a retained worktree, or the originating task receives a dedicated implementation task result. Create or reuse one verified Flow Worktree, run native $implement and $tdd there, review the committed candidate, and retain it for stage 4.
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

- Accept an explicit `$guided-implementation` invocation, an unambiguous
  stepwise or continuous confirmation replying to a valid Phase-2 success
  footer, an unambiguous confirmation replying to this Skill's immediately
  preceding stalled-task replacement block, that footer's verified same-turn
  continuous handoff, or an unambiguous retry request (including
  `$guided-implementation 重试`) in the same task after this Skill's immediately
  preceding retained-worktree footer.
  On retry, verify the recorded binding and current Git state before acting;
  continue that worktree and never create a replacement Flow Worktree.
- Track one `流程模式`. An explicit standalone invocation or ordinary
  unambiguous affirmation uses `逐阶段确认`; a clear request for automatic
  remaining execution or a verified upstream footer carrying
  `流程模式：连续执行后续全部流程` uses `连续执行后续全部流程`. Preserve it through
  launch, remediation, retry, completion and the Stage-4 handoff.
- For an inherited entry, resolve the target branch, committed Spec/ADR/Ticket
  paths, protected requirement-draft path, and allowed implementation paths.
  Every protected source path must exist at the implementation scope base.
- Verify and reuse the Flow Worktree supplied by Stage 2. If an inherited
  binding is missing, invalid, or points elsewhere, stop as an anomaly and do
  not create a replacement. A standalone entry creates one Flow Worktree only
  after its implementation brief passes the gate below. Retry always reuses
  the retained binding.
- Uncommitted primary-checkout files stay outside the Flow Worktree and are
  never copied, staged, stashed, or removed.
- The originating task supervises and reviews. It never edits implementation
  files.

## Foreground scripted carrier

The implementation-local foreground runner at
`skills/guided-implementation/scripts/workflow.py` may launch Stage 3 only
with its complete frozen payload: requirement path/commit/hash, target
repository and retained Flow Worktree binding, authority scope, prior stage
artifacts, and explicit model and reasoning effort. This is a continuous
carrier entry, not an implied user decision or inherited desktop setting. The
carrier follows this Skill's normal dedicated implementation executor and the
Originating Task's independent Standards and Spec review roles. It returns a
structured `completed`, `continue`, or `needs_input` outcome to the runner;
the runner owns the session identity, checkpoints and progress stream. On
`completed`, return the complete Stage-4 handoff in the `handoff_json` field: a
JSON-encoded object containing the exact binding, accepted candidate, and both
review and verification evidence, then
stop this carrier turn. The ordinary interactive continuous-mode rule to invoke
`$change-closure` in the same turn does not apply to this carrier, so Stage 4
cannot be run twice by the carrier and runner.

The payload's `continuous_stage2_to_4` mode is the already-authorized carrier
entry. It preserves the payload's explicit model/effort pair for the carrier;
the dedicated native executor still follows the existing task-settings protocol.
Do not fall back to interactive stage-entry confirmations.

The runner leaves the user's existing `codex exec` approval and sandbox policy
in effect. It does not force a narrower sandbox that can prevent the existing
Flow Worktree protocol from updating Git metadata, and it does not add a bypass
flag.

## Establish standalone authority

For an explicit invocation without a Phase-2 handoff, inspect the repository
read-only and freeze one standalone implementation brief from the user's exact
request plus repository-resolved facts. The brief must identify the target,
observable result, included and excluded scope, failure semantics, acceptance
checks, testing seam, and allowed implementation paths. Repository conventions
may resolve ordinary technical details; they may not supply a missing product,
scope, behavior, architecture, compatibility, data, or testing decision.

Enter standalone only when the brief is self-contained and no material decision
remains. Preserve the exact request and fixed brief in the dedicated task launch
and both review axes; they are the complete implementation authority. Create no
requirement, Spec, ADR, or Ticket merely to satisfy this route.

Standalone entry is always discussion-unattached and performs no discussion
discovery or lifecycle operation. If the invocation claims an attached topic or
Phase-2 handoff but its evidence is missing or invalid, stop as an inherited-flow
anomaly instead of discarding that claim and continuing standalone.

If the gate fails, make no write and create no Flow Worktree or task. Emit:

```text
进入结果：未进入
当前阶段：3实现
入口类型：standalone
缺失条件：<the unresolved target, behavior, scope, failure, acceptance, or testing fact>
影响：未创建 Flow Worktree，未启动专用实现任务，未修改仓库
建议：<use $problem-framing when requirements are unresolved | use $solution-design when engineering design is unresolved>
继续方式：补充缺失条件后重新显式调用 `$guided-implementation`，或进入建议阶段
```

## Enforce the confirmed implementation boundary

Treat either the committed Spec/ADR/Tickets and exact Stage-2 handoff, or the
fixed standalone implementation brief, as the complete implementation
authority. For an inherited flow, keep the requirement draft protected and
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
gap to the originating task. Treat the question as an implementation-authority
gap, obtain an explicit user decision through the originating task, and resume
only with updated authority. Never make the change first and disclose it at
completion.

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
Stage-4 handoff. An unattached Stage-2 handoff or standalone Stage-3 entry
creates no discussion Phase Run, performs none of these calls and carries
`none` for every discussion field.

## Bind the Flow Worktree

For a valid Stage-2 handoff, call `verify-worktree` with its exact binding and
use that Flow Worktree without creating another one. The worktree must be clean
at the reported planning merge commit. For a qualified standalone entry, call
`start-worktree` once from the exact target `HEAD`; record that commit as the
standalone base and implementation scope base. Neither route creates a lease,
claim, queue entry, or mutable run record.

An inherited entry must reuse its Flow Worktree. An invalid claimed binding
must not silently become a standalone entry or create a replacement.

Keep at most one active dedicated implementation task. Launch the initial task
in the returned worktree and pass the binding, inherited planning sources and
ordered Tickets or the fixed standalone brief, testing basis, flow mode and
exact authority boundaries in its prompt. The dedicated task must call
`verify-worktree` with its actual platform working directory before substantive
work.

## Implement and review

- When Tickets are present, read each completely, topologically sort `Blocked
  by`, preserve source document order among simultaneously ready Tickets, and
  implement them in that order.
- Before implementing remaining Tickets or sibling paths, start TDD with one
  representative observable behavior through the changed internal boundary and
  its production caller wiring. Read the Spec's owner, Interface, call-chain,
  and Testing Decisions; write the smallest failing test at that boundary and
  make it pass with the minimal in-scope implementation. Only then expand the
  proven pattern to equivalent in-scope paths. The changed boundary and caller
  path may not be replaced by a mock, stub, fake, or in-memory substitute;
  doubles remain allowed only for external or downstream dependencies beyond
  that boundary. If no executable seam can exercise it, report an
  implementation-authority/testing-seam gap before coding instead of bypassing
  the boundary.
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
- Do not modify committed planning-source paths. A standalone flow has none.
- The dedicated task never asks the user directly. It resolves ordinary
  technical details from the confirmed sources and repository conventions. An
  unexpected material decision is an implementation-authority gap: return it
  to the originating task before the affected edit, and let that task obtain
  the user's explicit decision. Review or test remediation returns to the same
  dedicated task and worktree.
- If the same failure mechanism recurs after a fix, a same-class regression
  appears in another affected path, or successive review/test outcomes
  overturn the same implementation approach, the Originating Task marks the
  continuation diagnosis-first and carries the prior candidate/finding
  identities. Before another edit, the task compares the prior failure and
  fix, explains why the mechanism remained, names the minimal effective
  validation at the real boundary, and inspects affected sibling paths. It
  then validates, repairs in scope, and reruns affected and full checks in the
  same task and Flow Worktree. Scope or plan gaps use the existing authority or
  anomaly path; ordinary defects continue here. No diagnostic document, fixed
  remediation-round gate, automatic replacement, or new Workflow Stage is
  added.
- Local stage entry grants no push, pull request, deployment, release, tracker or
  other remote write. Each such action requires separate explicit authority.
- The Originating Task owns `$code-review` and candidate acceptance. Its Spec
  axis uses the published planning artifacts for an inherited flow and the
  fixed implementation brief for a standalone flow. Stage 4 owns final
  integration and cleanup. The candidate, review, and remediation
  contract is authoritative in
  [references/originating-task-protocol.md](references/originating-task-protocol.md).
- A dedicated task result ends one execution turn, not Stage 3. The Originating
  Task applies the result-intake classifications and continuation rules in the
  authoritative originating-task protocol before review or recovery.

If the target branch advances, inspect the committed changes since the binding
base. A changed source path stops for user direction. Otherwise merge the new
target HEAD into the Flow Worktree, repair ordinary conflicts there,
rerun affected checks, and commit the replacement candidate. The Originating
Task handles its review under the authoritative role contract.

## Hand off the retained Flow Worktree

After both review axes accept the exact clean candidate, Stage 3 does not merge
the implementation into the target and does not remove the worktree or branch.
It passes the retained Flow Worktree, candidate, planning merge or standalone
base commit, implementation and closure path scopes, protected
requirement-source paths,
and review evidence to Stage 4. Stage 4 owns the final publication and cleanup.

For a Stage-3 failure with the worktree retained, emit the retained-worktree
footer from `worktree-execution.md` with recovery command
`$guided-implementation 重试`. A stalled dedicated task awaiting replacement
uses the decision block in `originating-task-protocol.md` instead. A successful
Stage-3 handoff is not a failure and uses the success footer below.

After success, emit a complete Stage-4 handoff:

```text
实现结果：成功
合并结果：待 4归档统一完成
专用任务：<thread id and host id>
目标仓库：<absolute repository path>
目标分支：<target branch>
实现依据：<committed source paths and commit | fixed standalone implementation brief>
实现提交：<candidate commit>
方案合并提交：<planning merge commit | standalone base commit>
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
