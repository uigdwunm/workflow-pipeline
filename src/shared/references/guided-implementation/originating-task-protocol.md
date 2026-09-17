# Originating Task Protocol

Before this role acts, execute [Package execution preflight]({{resource:shared/references/package-execution.md}}) using this child’s current effective registry. Inherit and verify the controller’s fixed package identity and check each selected action before its side effects.

The originating task binds the Flow Worktree, keeps at most one active Implementation Dispatcher, independently reviews its committed candidate, and passes
the retained flow to Stage 4. It does not implement code.

## Launch

For an inherited entry, confirm that every planning source is committed, list
the exact allowed implementation paths, and verify and reuse the Stage-2
handoff and Flow Worktree. A missing or invalid claimed binding is an anomaly
and never falls back to standalone.

For an explicit standalone entry, finish the standalone-authority gate in
`SKILL.md`, then call `start-worktree` once from the target `HEAD`. Freeze the
exact user request and implementation brief, record the returned base as the
standalone base and scope base, and use no planning source or protected source
unless the brief explicitly identifies one. The explicit invocation authorizes
only this bounded local implementation flow.

Launch one native Implementation Dispatcher in the verified Flow Worktree. Use
start-dispatch with current select-configuration evidence, disclose configuration
and reason, then dispatcher-bound with the actual native receipt. Include the
complete planning sources and dependency-ordered Tickets or the fixed
standalone brief, testing basis, flow mode, confirmed implementation scope,
existing-behavior changes, required collateral
changes, explicit out-of-scope items, documentation boundary and
remote-authority boundary in the launch prompt. Do not launch while a material
product, scope, behavior, architecture, compatibility, data, or testing-seam
decision remains unresolved.

## Intake

A platform terminal result ends one execution turn; it does not complete Stage
3. Before acting on each result, the Originating Task records the exact HEAD
before and after the turn, verifies the Worktree Binding and Git state, and
classifies the result:

- `candidate`: the confirmed scope is complete, the exact HEAD is a clean
  committed candidate, focused and full checks are reported, and no planned
  work remains.
- `checkpoint`: planned work remains and the exact HEAD advanced to clean,
  committed, in-scope progress.
- `blocked`: a specific implementation-authority decision, changed protected
  source, or evidenced technical condition prevents the next edit. The amount
  of remaining work is not a blocker.
- `no-progress`: planned work remains, no specific blocker was reported, and
  the exact HEAD and clean worktree are unchanged.

Route a `candidate` to Accept. Continue a `checkpoint` in the same Implementation Dispatcher and worktree from the exact current HEAD and remaining
scope; this is ordinary continuation, not recovery. Route a material `blocked`
decision to the user and return an ordinary technical condition within the
confirmed scope to the same Implementation Dispatcher.

For `no-progress`, send one corrective continuation to the same task. Name the
next dependency-ready Ticket or exact remaining implementation slice and state
that its prior terminal result did not satisfy Stage 3. If the next result is
again `no-progress`, classify that executor as stalled; do not keep issuing
continuations to it.

Maintain at most one active Implementation Dispatcher. Before proposing a
successor for a stalled executor, require its terminal result, stop tracking it,
reverify the same Worktree Binding and current verified clean HEAD, and confirm
that no protected source changed. Emit:

```text
流程异常：需要用户决策
异常类型：实现任务无进展
当前阶段：3实现
原调度者：<exact task identity>
当前检查点：<verified clean HEAD>
已完成：<committed scope>
未完成：<remaining scope>
阻塞检查：未发现需要用户决策的具体阻塞
建议：在同一 Flow Worktree 和当前检查点创建一个替代实现任务
确认后行为：停止跟踪原任务；替代任务先检查已有提交，再继续剩余范围；不创建替代 Flow Worktree
确认方式：明确同意上述单一待执行事项；如需调整可直接说明
```

After confirmation, launch one successor with the original authority boundary,
the exact completed and remaining scope, and the same Flow Worktree and current
verified clean HEAD. The successor inspects existing commits before editing.
This exception replaces only a terminal stalled executor; ordinary
continuation and remediation stay with the same task. A changed or dirty Git
state is a separate anomaly and must be resolved before launch.

## Accept

This review contract is authoritative. Require a clean committed candidate plus
focused/full checks. The Originating Task pins the exact candidate commit and
the expected target-branch commit as one review fixed point, independently
inspects the candidate diff against that fixed point, and passes that same fixed
point and candidate to both Standards and Spec review axes. Give the Spec axis
the committed planning artifacts for an inherited flow or the exact fixed
standalone brief. It dispatches the Standards and Spec review axes independently
and records their results
separately. Do not accept a candidate until both axes correspond to that exact
fixed point and candidate commit and have no unresolved actionable findings.
Reject an unplanned feature, behavior change, deletion, replacement, side
effect, or optional adjacent improvement even when its tests pass.
Return every actionable test or review finding to the same Implementation Dispatcher and verified worktree; the originating task does not edit
the implementation or create a replacement for ordinary remediation. Any
replacement candidate invalidates both review results. The Originating Task
reruns both axes against that replacement candidate before accepting it. Only
the Originating Task accepts the candidate; Stage 4 integrates it. Route only a material
unresolved decision to the user. If the target advanced without changing a
source path, the same Implementation Dispatcher merges that target, runs
affected and full checks, and commits a replacement candidate. The Originating
Task then establishes its exact replacement fixed point and reruns both axes.

## Diagnosis-first remediation

Mark the next remediation as diagnosis-first when any observable trigger is
present: the same failure mechanism is reported again after a fix; a
same-class regression appears in another affected path; or successive
review/test outcomes overturn the same implementation approach. Continue to the
same Implementation Dispatcher and Flow Worktree, carrying the trigger and
the prior candidate and finding identities in the request.

Before another edit, require execution evidence that: (a) compares the prior
failure and fix side by side; (b) explains why that fix did not address the
mechanism; (c) identifies the smallest effective validation at the affected
real boundary; and (d) lists the affected sibling paths inspected and whether
the mechanism applies to each. The task then runs that focused validation,
repairs in scope, reruns the affected and full checks, and commits the
replacement candidate as usual.

If diagnosis shows that the accepted scope or plan is insufficient, stop
through the existing implementation-authority or anomaly decision before
changing code. An ordinary technical defect continues in the same task and
worktree. Do not create a diagnostic document, impose a fixed remediation
round gate, or spawn an automatic replacement; the stalled-task replacement
exception above remains limited to terminal no-progress results.

## Retain and hand off

After accepting the exact candidate, verify that it is still the clean Flow
Worktree `HEAD`. Do not update the target branch and do not remove the worktree
or branch. Pass its exact binding, candidate commit, planning merge or
standalone base commit,
implementation and closure path scopes, protected
requirement-source paths, verification/review evidence, exact closure-document
updates, and flow mode to Stage 4. No claim, lease, proposal receipt, or closure
checkpoint is carried forward.

When Stage 3 is attached to a discussion topic, also pass the exact
project/tree/topic identity, actor binding, completed phase-3 result id and the
ledger/topic revisions returned by the required final `read-topic`; an
unattached Stage-2 or standalone Stage-3 flow passes `none` for that entire
group. Local completion grants no remote-write authority.
