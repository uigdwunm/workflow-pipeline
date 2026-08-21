# Closure Checkpoint Protocol

Use this reference only inside `$change-closure`. New `exclusive-checkout-v2`
runs use closure checkpoint version 2; new `isolated-worktree-v1` runs use
version 3. Legacy in-flight handoffs retain closure checkpoint version 1 and
their recorded worktree phase. Never convert one version to another.

## Contents

- Resume a retained closure
- Prepare a version-2 checkpoint
- Prepare an isolated version-3 checkpoint
- Record phase receipts
- Release immutable stage-3 evidence
- Legacy version-1 compatibility

## Resume a retained closure

Use recovery only when implementation and merge remain proven complete and one
exact mechanically recoverable document, verification, branch, lease,
legacy-worktree, remote, or evidence-cleanup action remains.

```text
归档结果：受阻
当前阶段：`$change-closure`
阶段状态：受阻
恢复类型：重试当前阶段
恢复条件：<exact condition>
恢复检查点：仓库=<path>; 执行模式=<exclusive-checkout-v2 | exclusive-checkout-v1 | legacy-worktree>; 基础分支=<branch>; 当前 HEAD=<sha>; 合并提交=<sha>; 归档提交=<sha or none>; Spec=<path or URL or none>; Tickets=<paths or URLs or none>; Worktree=<none or legacy path and state>; 实现分支=<branch and state>; 仓库租约=<path, id and state or legacy-not-applicable>; 文档租约=<path, id, version and state or none>; 保留文档=<exact unstaged paths>; 管理链接基线=<paths and targets>; 专用任务=<thread id>; 任务主机=<host id>; 归档检查点=<absolute JSON>; 归档版本=<1 | 2>; 归档阶段=<version-appropriate phase>; 归档检查点字节=<count>; 归档检查点SHA-256=<sha>; 清理检查点=<path or deleted-after-complete>; 交接文件=<path and state or deleted-after-validation>; 交接ID=<id>; 交接字节=<count>; 交接SHA-256=<sha>; 交接完成标记=<HANDOFF_COMPLETE:id>; 监督清单元数据=<manifest or deleted-after-validation>; 监督文件数=<count>; 监督清理状态=<state>; 远程操作=<results or none>
下一阶段：none
恢复方式：满足条件后回复 `重试`
```

On retry:

1. Run `inspect-closure-checkpoint` first. Require its immutable
   `closure_version`, execution mode, facts,
   and receipts to match the stage-3 report and actual repository.
2. Before `complete`, run `inspect-cleanup` and require its filesystem-derived
   state to match.
   Valid states are `intact`, `partial-supervision:<removed-prefix-count>`,
   `supervision-cleared`, `handoff-cleared`, and `complete`.
3. Resume only the next named action. Never repeat a verified document commit,
   branch deletion, lease release, legacy worktree removal, remote write, or
   evidence deletion.
4. Resolve ambiguous local or remote outcomes from actual state before retry.
5. Leave retry mode when implementation, merge, authority, ancestry, execution
   mode, or prepared facts changed.
6. If actual state proves exactly one next action completed before interruption,
   verify its complete postcondition and record only that phase.

Missing worktrees/branches and ambiguous lease release are not completed side
effects. Preserve the checkpoint and report the exact observed state. Never
advance from absence alone when the phase requires an `observed_before` fact.

Use `恢复类型：需要决策` for force cleanup, ambiguous remote state, changed
integration, document-format choices, or material judgment.

## Prepare a version-2 checkpoint

Derive the retained checkpoint path from the verified handoff ID. Reuse an
existing checkpoint only when every prepared fact matches. Never overwrite it
or create another identity.

When none exists:

1. Write one private cleanup-checkpoint JSON outside repository and both fixed
   runtime roots with exact cleanup version, handoff identity, handoff metadata,
   latest manifest metadata, and supervision count.
2. Run `inspect-cleanup --checkpoint <path>` and require `state: intact`.
3. Acquire and verify the short closure repository lease before creating the
   retained checkpoint. For `exclusive-checkout-v2`, then acquire and verify the
   document lease before the first document edit; never hold the document lease
   while waiting for repository authority.
4. Write one ordinary closure input JSON with exactly `cleanup_checkpoint` and
   `facts`. Set facts to:
   - `repository` and equal `checkout_path`;
   - `execution_mode: exclusive-checkout-v2` for new runs;
   - `base_branch`, `implementation_commit`, `merge_commit`, and
     `implementation_branch`;
   - exact `managed_links`;
   - `repository_lease` containing exact `path`, `lease_id`, `file_bytes`,
     `file_sha256`, and `complete`;
   - `source_task_id`, `source_host_id`, `spec_references`,
     `ticket_references`, and authorized `remote_actions`.
5. Store no credentials, bodies, command output, or supervision payloads.
6. Run:

   ```text
   python3 <guided-implementation-skill-root>/scripts/supervision_protocol.py create-closure-checkpoint \
     --input <absolute ordinary closure input JSON>
   ```

7. Require returned `closure_version: 2`, canonical private `0400` checkpoint
   under `${CC_SWITCH_RUNTIME_ROOT:-$HOME/.cc-switch/runtime}/change-closure-checkpoints`,
   phase `prepared`, exact bytes, and SHA-256. Never edit it by hand.

## Record phase receipts

Advance one phase at a time:

```text
python3 <guided-implementation-skill-root>/scripts/supervision_protocol.py advance-closure-checkpoint \
  --checkpoint <absolute retained checkpoint> \
  --phase <next phase> \
  [--result <absolute ordinary result JSON>]
```

Version-2 order and receipts:

- `documents-committed`: for `exclusive-checkout-v2`, `closure_commit`,
  `documents_updated`, `verification`, exact released `document_lease`
  (`lease_id`, `path`, final `version`, `state: available`), and
  `preserved_documents`; legacy modes retain the earlier three fields;
- `branch-removed`: exact branch `name`, `verified_absent: true`;
- `lease-released`: exact lease `lease_id`, `path`,
  `verified_absent: true` after `release-repository-lease` and an
  `inspect-repository-lease` result of `available`;
- `remote-verified`: exact prepared `actions`, observed `results`,
  `verified: true`;
- `evidence-cleanup` and `complete`: no result file; the tool derives cleanup
  state.

## Prepare an isolated version-3 checkpoint

Use the same `create-closure-checkpoint` command. When facts contain
`execution_mode: isolated-worktree-v1`, the tool immutably dispatches version 3
and requires the ordinary `checkout_path` equal `repository`, a distinct exact
`worktree_path`, exact documentation-proposal paths and the exact worktree
execution lease (`path`, `lease_id`, CAS `version`). Its phases are:

`prepared -> documents-committed -> worktree-removed -> branch-removed -> execution-lease-released -> remote-verified -> evidence-cleanup -> complete`.

At `prepared`, converge each proposal only in the ordinary checkout with
`converge-document-proposal`. The implementation worktree supplies immutable
proposal bytes/evidence only. `applied` and `no-op` may proceed; a
`document_proposal_conflict` preserves both sides and blocks.

Version-3 cleanup receipts are deliberately stronger than legacy receipts:

- `worktree-removed` requires exact path, `observed_before: present-clean`, and
  `verified_absent: true` after non-force removal;
- `branch-removed` requires exact name, `observed_before: present-merged`, and
  `verified_absent: true` after `git branch -d`;
- `execution-lease-released` requires exact ID/path, `state: available`, and
  exactly the next CAS version.

Publish one receipt after each verified postcondition. A missing worktree,
branch deletion refusal, or uncertain lease result stops before later cleanup.
On resume, inspect the checkpoint and actual authoritative state, then perform
only the first action without a receipt.

After each mutation, verify its postcondition and record exactly one receipt
before the next action. Stop when receipt publication fails.

## Release immutable stage-3 evidence

Perform only after documents, branch deletion, repository-lease release, and
authorized remote actions succeeded and phase is `remote-verified`:

1. Advance to `evidence-cleanup`; require cleanup state `intact`.
2. Run `inspect-cleanup` with the exact private checkpoint.
3. Run `advance-cleanup` one step at a time; at most one atomic mutation occurs
   per invocation: removing the next verified supervision entry, latest
   manifest, handoff, or empty handoff-ID directory. It never recurses or
   removes fixed roots.
4. Repeat only while successful until state `complete`.
5. Inspect once more, require `complete`, and advance the retained checkpoint
   to `complete` without a result file.
6. Run `inspect-closure-checkpoint`, retain the canonical complete receipt, and
   only then remove the ordinary cleanup input.

On failure, preserve remaining evidence and checkpoint. Never manually unlink,
weaken permissions, delete unexpected siblings, retry an ambiguous effect, or
recreate deleted evidence.

## Legacy version-1 compatibility

Use version 1 only when the verified stage-3 handoff embeds the previous
worktree execution protocol and the stage-3 footer names one real implementation
worktree. Preserve its exact prepared facts and phase order:

`prepared -> documents-committed -> worktree-removed -> branch-removed -> remote-verified -> evidence-cleanup -> complete`.

Version-1 facts retain `worktree_path` and omit execution mode, checkout path,
and repository lease. Its receipts remain:

- `documents-committed`;
- `worktree-removed` with exact path and `verified_absent: true`;
- `branch-removed`;
- `remote-verified`;
- derived evidence-cleanup and complete receipts.

Do not create a new version-1 checkpoint, add a worktree to version 2, remove
the worktree phase from version 3, or replace a retained checkpoint with another
version. The tool accepts version 1 only to finish published in-flight evidence.
