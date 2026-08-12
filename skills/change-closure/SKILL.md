---
name: change-closure
description: Use when the user explicitly invokes $change-closure (4归档), confirms 执行后续全部流程 from a successful $guided-implementation footer, automatically continues from that verified footer when it carries continuous-flow mode, or answers this Skill's exact recovery, repository-lease, document-lease, or workspace-assistance footer. New exclusive-checkout-v2 runs verify zero worktrees and implementation-clean state, preserve unrelated unstaged documentation, acquire a short repository lease then the shared CAS document lease, reconcile and commit only closure-owned documents, remove only the merged branch, release leases, and clean immutable evidence. Legacy in-flight protocols remain compatible.
---

# 4归档

Close a successfully implemented and merged change. New runs use the project's
one existing checkout and create zero Git worktrees. Reconcile established
planning documents, commit only closure-owned documentation, safely delete the
fully merged implementation branch, release the short closure lease, perform
only separately authorized remote delivery, and release exact stage-3 evidence
last.

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
- For a new run, also require `执行模式：独占 Git 与实现区（零 worktree；文档区使用 document lease）`,
  `Worktree：none`, and `仓库租约：已释放` with exact verified release
  evidence. For a legacy in-flight run, require its real worktree path and
  embedded legacy protocol instead.
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

## Normalize repository status

Run `git status --porcelain=v1 -z --untracked-files=all`. Exclude only verified
untracked `.agents/skills/<name>` symlinks whose exact paths and targets match
the stage-3 managed-link baseline. Classify every other path as implementation,
recognized documentation, or unknown. Require the index and implementation
paths to be clean. Preserve and list recognized unstaged documentation. Never
exclude an unknown path, tracked implementation change, non-symlink, wrong
target, parent directory, or other `.agents` path.

## Resolve and verify the change state

1. Read applicable project instructions.
2. Read the complete stage-3 report and verify implementation and merge results,
   execution mode, repository, base and implementation branches, commits,
   verification, risks, task/host IDs, exact `handoff.json`, manifest,
   supervision count, managed links, and Spec/Ticket references.
3. Require implementation and merge success. Closure cannot turn incomplete
   integration into success.
4. For `exclusive-checkout-v2`, require `Worktree: none`, the ordinary checkout
   on the recorded base branch, the merge commit and implementation commit as
   ancestors of current `HEAD`, proven with `git merge-base --is-ancestor`,
   implementation-clean status, exact unstaged-document list and hashes, and
   exact managed links. Do not require current
   `HEAD` to equal the stage-3 merge commit: if it is a clean descendant and
   governing planning artifacts are unchanged, automatically adopt it. Do not
   ask the user merely because another completed task advanced the branch after
   stage 3 released its lease.
5. Stop for rewritten/divergent history, missing merge ancestry, changed
   governing artifact, unknown dirty state, or ambiguous implementation branch.
6. For an in-flight `exclusive-checkout-v1` zero-worktree handoff, finish under
   its embedded clean-checkout protocol and existing closure checkpoint facts.
   For an older worktree handoff, follow its worktree cleanup facts and closure
   v1 checkpoint. Never convert an in-flight handoff to v2 semantics.

## Acquire the short closure lease

Before the first repository mutation, run:

```text
python3 <guided-implementation-skill-root>/scripts/supervision_protocol.py inspect-repository-lease \
  --repository <absolute repository path>
```

If another lease is held, do not inspect or alter its task's checkout state and
do not ask the user to clean it. End with:

```text
4归档排队：等待仓库
当前阶段：`$change-closure`
阶段状态：等待
恢复类型：等待仓库租约
目标仓库：<absolute path>
租约文件：<absolute path>
租约所有者：<verified task and host IDs>
归档检查点：<path and phase or none>
下一阶段：none
恢复方式：租约释放后回复 `重试`；无需选择 commit、stash、清理或丢弃
```

When available, acquire a new `exclusive-checkout-v2` lease with current base
branch/`HEAD` and this originating task/host identity. Record path, ID, bytes,
SHA-256, and completion marker. Immediately revalidate branch, `HEAD`, status,
managed links, merge ancestry, artifacts, and handoff. Never overwrite or
manually delete a lease.

Then read
[../guided-implementation/references/document-lease-protocol.md](../guided-implementation/references/document-lease-protocol.md)
completely. After the local closure plan is authorized and immediately before
the first document write, acquire the document lease with
`stage: change-closure` and `purpose: document-write`. Use the default immediate
attempt plus ten 10-second retries. On timeout, preserve the repository lease
and closure checkpoint, emit the fixed Chinese document-lock timeout block, and
stop. Verify before writing, renew when necessary, and release immediately
after the closure-owned document commit or verified no-op. Never hold the
document lease while waiting for user input, and never stage a document before
the document lease is verified.

## Resolve unknown workspace blockers

Use workspace assistance only when no valid other lease explains the occupied
checkout and documentation-aware status contains unknown user-owned changes.

```text
工作区处理：需要决策
当前阶段：`$change-closure`
阶段状态：受阻
恢复类型：协助处理工作区
待处理改动：<exact staged, unstaged and untracked paths and states>
恢复检查点：仓库=<path>; 基础分支=<branch>; 当前 HEAD=<sha>; 实现分支=<branch>; Worktree=<none or legacy path>; 实现提交=<sha>; 合并提交=<sha>; Spec=<path or URL or none>; Tickets=<paths or URLs or none>; 仓库租约=<none or exact state>; 交接ID=<id>; 监督清单元数据=<manifest>; 归档凭证=<path or none>; 归档阶段=<phase or none>
下一阶段：none
待决问题：检测到无法归属给其它有效租约的工作区改动。是否由我协助保留并处理这些改动，以便继续 4归档？我不会擅自丢弃内容。
恢复方式：回答上述问题；无需重新调用 Skill
```

Generic approval authorizes inspection and a proposal only. Require an exact
preservation choice before acting, disclose the working directory, exact
commands, flags, pathspecs, affected paths, commit message, and stash scope,
and require separate current-turn authority for reset, clean, overwrite,
deletion, force, or other unrecoverable discard. Revalidate all closure facts
afterward.

## Reconcile the Spec and Tickets

Read complete published documents and compare them with accepted Work Item
evidence, final commits, verification, and risks. When no Spec or Tickets exist,
skip reconciliation and create no replacement document.

For Matt Tickets, check a criterion only when evidence proves it; preserve
order, titles, blocking edges, and wording. For Matt Specs, preserve existing
sections and record only material deviations, final decisions, verification,
and remaining risks in the established completion area. For another format,
preserve it. Invoke `$ask-matt` only to choose a representation inside existing
documents when genuinely unclear. Never invoke `$to-spec` or `$to-tickets`.
Remote documents require explicit authorization before writing.

## Show and authorize the local closure plan

Before mutation, show:

- Spec/Ticket references or verified absence;
- exact document changes;
- repository, execution mode, base, implementation branch, commits, and no
  worktree for new runs;
- exact files to stage and closure commit message, or `none`;
- non-force implementation-branch deletion command;
- repository-lease facts and release command;
- cleanup and retained checkpoint paths and planned phases;
- requested and unrequested remote actions; and
- verification commands and results.

Accepted stage entry authorizes the shown standard local document commit,
non-force branch deletion, and exact lease release when every gate passes. Ask
again for remote writes, unknown dirty files, unexpected documents, changed
targets, force options, or wider scope.

## Prepare and resume the retained checkpoint

Read `references/closure-protocol.md`. For a new zero-worktree run, create or
reuse closure checkpoint v2 with phases:

`prepared -> documents-committed -> branch-removed -> lease-released -> remote-verified -> evidence-cleanup -> complete`.

Prepared facts include repository, checkout path, execution mode, base branch,
implementation and merge commits, implementation branch, managed links,
closure lease metadata, task/host IDs, Spec/Ticket references, and authorized
remote actions.

For a legacy in-flight run, retain closure checkpoint v1 and its
`worktree-removed` phase. Inspect the retained checkpoint first on every retry,
match its version and facts to stage 3, and resume only the named next action.
Never repeat a verified commit, branch deletion, lease release, legacy worktree
removal, remote write, or evidence deletion.

Use `恢复类型：需要决策` for force cleanup, ambiguous effects, changed
integration, document-format choices, or material judgment. Use `重试` only for
one mechanically recoverable action.

## Commit closure documentation

At `prepared`:

1. Require the exact document lease remains verified.
2. Apply only approved format-preserving updates, including final review of
   exact stage-3 document writes and hashes.
3. Verify the complete diff belongs to this closure. For a shared file whose
   full diff includes another requirement, stage nothing and reconcile.
4. Stage only exact closure-owned document pathspecs and commit concisely;
   never stage code, generated files, unrelated docs, other tasks' pending
   documents, or user work. Never use `git add .`, `git add -A`, or a whole
   documentation directory.
5. Do not create an empty commit.
6. Run required documentation and project verification.
7. Release the exact document lease after the verified commit or no-op. Require
   `state: available` and the returned incremented version.
8. Advance to `documents-committed` only after commit/no-op and document-lease
   release are proven.

## Clean local implementation state

For zero-worktree checkpoint v2, at `documents-committed`:

1. Repeat lease, ancestry, base cleanliness, managed links, and branch checks.
2. Run `git branch -d <implementation-branch>` without `-D`.
3. Verify the branch is absent and base/closure commits unchanged; advance to
   `branch-removed`.
4. Require the checkout still clean on the base branch, then run exact
   `release-repository-lease` with recorded metadata.
5. Run `inspect-repository-lease` and require `state: available`; record exact
   `lease_id`, path, and `verified_absent: true`; advance to `lease-released`.

Never force-delete a branch or manually remove the lease. Failure leaves
closure incomplete but does not invalidate implementation or merge.

For a legacy v1 checkpoint only, perform its recorded non-force worktree removal
and branch deletion exactly as the embedded protocol requires.

## Perform separately authorized remote delivery

At `lease-released` for v2, or `branch-removed` for legacy v1, perform only
current-turn authorized remote actions. Verify each action and advance to
`remote-verified`. Record empty actions/results with `verified: true` when none
were prepared. Resolve ambiguous remote outcomes from actual external state
before retrying.

## Release immutable stage-3 evidence

At `remote-verified`:

1. Advance to `evidence-cleanup` before deleting evidence.
2. Run `inspect-cleanup`, then `advance-cleanup` one atomic step at a time.
3. Require filesystem-derived state to reach `complete`, inspect again, and
   advance retained checkpoint to `complete`.
4. Retain the complete closure checkpoint; remove only the ordinary cleanup
   input afterward. The protocol never removes the fixed runtime root.

Never manually unlink, weaken permissions, remove unexpected siblings, repeat
an ambiguous effect, or recreate deleted evidence.

## Report completion

```text
归档结果：成功
变更生命周期：已关闭
执行模式：独占 Git 与实现区（零 worktree；文档区使用 document lease） | exclusive-checkout-v1-legacy | legacy-worktree
Spec：<clickable path or URL, or none>
Tickets：<clickable paths or URLs, or none>
实现提交：<sha>
合并提交：<sha>
归档提交：<sha or none needed>
Worktree 清理：不适用（零 worktree） | 成功（legacy）
本地分支清理：成功
文档租约：已释放（<lease id; final version; path>） | legacy-not-applicable
其它未暂存文档：<exact preserved paths | none>
仓库租约：已释放（<lease id and path>） | legacy-not-applicable
交接与监督文件清理：成功（<handoff id>; <count> 个监督文件）
归档凭证：<absolute retained checkpoint>; version=<1 | 2>; phase=complete; bytes=<count>; SHA-256=<sha>
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
