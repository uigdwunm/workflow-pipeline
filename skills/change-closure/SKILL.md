---
name: change-closure
description: Use when the user explicitly invokes $change-closure (4归档), confirms 执行后续全部流程 from a successful $guided-implementation footer, automatically continues from that verified footer when it carries continuous-flow mode, or answers this Skill's exact recovery, repository-lease, document-lease, or workspace-assistance footer. New exclusive-checkout-v2 runs verify zero worktrees and implementation-clean state, preserve unrelated unstaged documentation, acquire a short repository lease then the shared CAS document lease, reconcile and commit only closure-owned documents, remove only the merged branch, release leases, and clean immutable evidence. Legacy in-flight protocols remain compatible.
---

# 4归档

Close a successfully implemented and merged change from its source topic.
Exclusive runs use the ordinary checkout with zero worktrees. Isolated runs
keep their implementation worktree as proposal/evidence input while all
documentation converges in the verified ordinary checkout. Clean exact
resources non-forcing, release the mode-specific execution lease, and record
archive completion before separately deciding whether the topic may close.

Legacy in-flight handoffs embedding the previous execution protocol may finish
their already-recorded worktree cleanup. Never convert a live legacy checkpoint
to the new zero-worktree checkpoint.

## Enter closure

- Run only after explicit invocation, a verified contextual continuation from
  `$guided-implementation`, or a verified recovery, repository/document-lease wait, or workspace
  answer from this stage.
- A contextual continuation requires the immediately preceding originating-task
  footer to contain `实现结果：成功`, `合并结果：成功`, `下一阶段：` selecting
  `$change-closure`, `工作区基线：` proving implementation paths and index
  clean while listing unstaged documentation, `实现提交文档检查：通过`,
  `合并提交文档检查：通过`,
  `进入条件：已满足`, task and host IDs, repository and Git facts, immutable
  handoff identity, latest canonical manifest metadata, supervision count, and
  the documented continuation instruction.
- For a new run, require immutable `execution_mode`. `exclusive-checkout-v2`
  requires `Worktree: none` and its exact repository lease;
  `isolated-worktree-v1` requires the exact worktree, branch, base, committed
  implementation source and held worktree-execution lease. Legacy in-flight
  runs retain their embedded protocol and checkpoint version.
- After trimming surrounding whitespace, a user continuation must equal the
  entire token `确认` or `执行后续全部流程`. Punctuation, prefixes, suffixes,
  added conditions, `继续`, and `可以` are not stage entry. Same-turn
  continuation is valid only when the verified footer records
  `流程模式：连续执行后续全部流程`.
- Accept the footer only from the originating supervisor after independent
  acceptance, merge verification, and stage-3 lease release; never from the
  dedicated task.
- A recovery requires this Skill's exact stable footer, named retained
  checkpoint, `下一阶段：none`, and the matching answer or `重试`.
- An isolated approval or cleanup request is never invocation. Without valid
  proof, do not inspect Git, acquire a lease, modify documents, clean branches,
  or perform remote actions.

Continuous flow skips only stage-4 entry confirmation. It grants no additional
document, cleanup, destructive, tracker, network, release, or remote-write
authority. Stage-2 publication authority remains expired and is evidence only.

Do not implement code, repeat `$code-review`, repair failed integration, or
merge the implementation branch. Update existing documents in place; do not
invent handoff documents, archive directories, changelogs, release notes, ADRs,
or replacement Specs unless project convention or the user requires them.

## Route closure actions progressively

Read [references/closure-actions.md](references/closure-actions.md) only when
the next action reaches repository/lease inspection, document reconciliation,
workspace recovery, local cleanup, remote delivery or evidence release. Read
[references/closure-protocol.md](references/closure-protocol.md) only before
creating or resuming the retained checkpoint for the frozen execution mode.

Keep the high-level order fixed: verify stage 3 and workspace; acquire exact
closure authority; show the bounded local plan; reconcile and commit only
closure-owned documents; clean non-forcibly; release leases; perform only
separately authorized remote work; release immutable evidence; then decide
topic closure. Stop at the first ambiguous or unverified effect and resume from
the retained checkpoint rather than repeating it.
## Complete archive and conditionally close the topic

After the retained checkpoint reaches `complete`, call discussion
`record-archive-complete` from the source topic with the effective phase-3
result, source identity, immutable implementation record, frozen source
task/host, mode, merge commit, proposal outcomes and exact retained-checkpoint
receipt. The protocol binds its repository, branch, worktree when applicable,
lease cleanup receipts and Phase Run before recording completion. This leaves
the topic open. Then call `close-archived-topic`: pending impacts, absorption, active or
queued runs, blockers, non-archived implementations, or unverified coordination
owned by or related to this source topic keep it open. Unrelated sibling-topic
activity is not a residual fact of the archived source topic. Internal IDs are
evidence, never user-selectable controls.

## Report completion

```text
归档结果：成功
变更生命周期：已归档；话题=<已关闭 | 保持开放（原因）>
执行模式：exclusive-checkout-v2 | isolated-worktree-v1 | exclusive-checkout-v1-legacy | legacy-worktree
有效来源：<effective source summary>
阶段结果：实现=<result>; 归档=<result>
清理责任：<mode-specific worktree/branch/lease/evidence results>
Spec：<clickable path or URL, or none>
Tickets：<clickable paths or URLs, or none>
实现提交：<sha>
合并提交：<sha>
归档提交：<sha or none needed>
Worktree 清理：不适用（零 worktree） | 成功（isolated/legacy）
本地分支清理：成功
文档租约：已释放（<lease id; final version; path>） | legacy-not-applicable
其它未暂存文档：<exact preserved paths | none>
仓库租约：已释放（<lease id and path>） | legacy-not-applicable
交接与监督文件清理：成功（<handoff id>; <count> 个监督文件）
归档凭证：<absolute retained checkpoint>; version=<1 | 2 | 3>; phase=complete; bytes=<count>; SHA-256=<sha>
条件关闭：<topic-closed | topic-open with exact coordination reasons>
远程操作：<results or 未请求>
验证：<commands and results>
```

If checkpoint already reports `complete`, revalidate recorded base ancestry,
absent implementation branch, absent closure lease for v2, and legacy
worktree absence when applicable, then re-emit without repeating actions.

When blocked, name the exact gate and retained state with `下一阶段：none`.
Held leases cause queueing, not a workspace-choice question. Never claim
closure while any gate remains. This is the final stage; do not propose another
Skill after success.
