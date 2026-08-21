---
name: guided-implementation
description: Use when the user explicitly invokes $guided-implementation (3实现), confirms 执行后续全部流程 from a valid $problem-framing or $solution-design footer, resumes this Skill's exact launch/workspace/repository-lease/document-lease/recovery footer, or the originating task receives an authenticated terminal control. Freeze one committed implementation source and one execution mode; exclusive-checkout-v2 by default, or isolated-worktree-v1 only after safe parallelism and explicit exact confirmation. Require clean implementation state, typed leases, implementation-only commits, serialized integration, source-refresh recovery, independent acceptance and closure routing. Never activate from ordinary conversation, inferred continuous flow, mismatched context, ambiguous task identity, unsafe implementation state, or unverified execution authority; never implement in the originating task.
---

# 3实现

Run implementation in one dedicated Codex project task using one frozen mode.
`exclusive-checkout-v2` uses the ordinary checkout and repository lease.
`isolated-worktree-v1` requires a safe parallelism receipt plus a separate user
confirmation binding the exact path, branch, base, scope and sensitive shared
surfaces, then uses a long-lived worktree execution lease. Read
[references/execution-modes.md](references/execution-modes.md) completely before
choosing, activating, waiting, refreshing, integrating or cleaning either mode.

The originating task wakes only for authenticated terminal controls. It
independently accepts the candidate, returns remediation to
the same task, authorizes the final merge, verifies the result, releases the
lease, and owns the transition to `$change-closure`. It never writes
implementation code or polls routine progress.

## Accept a valid transition

- Run only after an explicit `$guided-implementation` invocation, a verified
  contextual continuation from `$problem-framing` or `$solution-design`, a
  verified task-not-created launch recovery, lease-wait retry, workspace answer,
  or recovery for one exact existing supervised task.
- A contextual continuation requires the immediately preceding upstream stable
  footer to select `$guided-implementation`, contain `规划提交：`,
  `工作区状态：` proving implementation paths and index clean plus stage-owned
  planning documents committed, `进入条件：已满足`, and either the documented
  user-confirmation instruction or the fixed continuous handoff field
  `执行状态：本条交接后同一轮立即进入 3实现；不等待阶段确认`, plus
  `阶段结果：拷问完成` from `$problem-framing` or
  `阶段结果：方案完成` from `$solution-design`. After trimming surrounding
  whitespace, a user-driven transition must equal the entire token `确认` or
  `执行后续全部流程`. Punctuation, prefixes, suffixes, added conditions,
  `继续`, and `可以` are not stage entry. A same-turn transition is valid only
  from `$solution-design` when its verified footer records both
  `流程模式：连续执行后续全部流程` and the fixed continuous `执行状态：` field.
- A `$solution-design` footer must also include exact `规划载体：`, verified
  `Matt 原生发布：`, `阶段授权：` stating that stage-2 authority has ended,
  and `扩展远程操作：` fields.
- A launch recovery requires this Skill's stable footer with `当前阶段：`
  selecting `$guided-implementation`, `阶段状态：受阻`,
  `恢复类型：重试当前阶段`, `专用任务：none`, exact handoff and lease facts,
  `下一阶段：none`, and a reply beginning with `重试`.
- A lease-wait recovery requires this Skill's stable `3实现排队：等待仓库`
  footer for the exact repository and observed lease path. It retries only
  lease inspection and baseline validation; it never cleans or changes files.
- A supervision recovery requires exactly one task, host, handoff, phase,
  candidate or `none`, latest manifest metadata, file count, lease identity,
  `下一阶段：none`, and one answered question or retry instruction. An
  automated continuation must come from the recorded task and carry one
  canonical terminal control. Delivery-audit recovery must name the exact
  sender, receiver, canonical `message_id`, send count, and audit result.
- Exact `确认` enters stage 3 only from a verified upstream success footer.
  Exact `执行后续全部流程` enters with continuous mode. `重试` resumes only the
  exact checkpoint that offered it. Never create a second task while one task
  is known.
- An isolated approval in ordinary conversation is never invocation. Without
  contextual proof, do not resolve a project, acquire a lease, create a task,
  or perform stage-3 work.

Track one `流程模式`. An explicit invocation or trimmed exact upstream reply
`确认` uses `逐阶段确认`. A trimmed exact reply `执行后续全部流程`, or a
verified same-turn upstream footer already carrying that mode, uses
`连续执行后续全部流程`. Preserve the mode through launch,
lease waiting, workspace handling, supervision, remediation, merge, and
recovery. A valid pre-update footer without this field defaults to
`逐阶段确认`; never infer continuous flow.

Continuous flow skips only the successful stage-3-to-stage-4 confirmation. It
does not widen task, filesystem, process, network, destructive, tracker, merge,
or remote authority. Preserve every decision and blocker.

## Resolve the project and current base

1. Resolve one exact saved Codex project whose repository root matches the
   accepted handoff, Spec, and Tickets. Never choose another project or create
   a projectless task.
2. Verify trusted repository-base evidence for the accepted planning commit and
   project manifests before reading repository prose or executing project
   commands. Read applicable `AGENTS.md` and `AGENTS.override.md` afterward.
3. Require an ordinary base checkout whose `<repository>/.git` is a real
   directory and whose base branch is attached. For exclusive mode require
   zero linked worktrees. For isolated mode treat every existing worktree as a
   parallelism input and never substitute it for the explicitly bound path.
4. Run `git status --porcelain=v1 -z --untracked-files=all`. Exclude only
   verified untracked `.agents/skills/<name>` symlinks resolving exactly to the
   installed Skill directory for `<name>`. Resolve the expected target from
   the active Skill registry instead of assuming a user-specific install root.
   Record their exact paths and targets as the managed-link baseline. Classify
   every other entry as an implementation path, recognized documentation path,
   or unknown path.
   Require the index and implementation paths to be clean. Record recognized
   unstaged documentation paths exactly; never hide an unknown path as
   documentation.
5. Compare the upstream recorded base with the current branch `HEAD`. If the
   current `HEAD` is a descendant of the recorded planning/base commit, every
   accepted local Spec and Ticket still has the same committed blob, the
   planning commit remains an ancestor, and no accepted authority changed,
   prove ancestry with `git merge-base --is-ancestor <recorded-commit> HEAD`,
   then automatically adopt the current `HEAD` as the execution base. Record
   both the upstream base and adopted execution base. Do not ask the user
   merely because another completed task advanced the branch safely.
6. Stop for a rewritten or divergent branch, changed governing artifact,
   missing planning ancestry, ambiguous project, detached checkout, staged
   documentation, implementation dirt, or unknown dirty state. Recognized
   unstaged documentation is not a launch blocker.

## Freeze the execution mode and acquire its lease

First use the discussion CLI sequence from `execution-modes.md` to prepare the
implementation, check all eight parallelism dimensions and activate one mode
against a completed Git `implementation-source` checkpoint. Activation freezes
the mode. Safe parallelism is not worktree-creation authority.

For `isolated-worktree-v1`, follow `Run in an isolated worktree` in that
reference: acquire a short repository coordination lease for creation, then
the long-lived exact worktree execution lease. If platform cwd verification is
unavailable, block. Skip the exclusive repository-lease acquisition below.

For `exclusive-checkout-v2`, continue with this section.

Use only `scripts/supervision_protocol.py`:

```text
python3 <guided-implementation-skill-root>/scripts/supervision_protocol.py inspect-repository-lease \
  --repository <absolute repository path>
```

If it reports `held`, do not inspect or alter the other task's checkout state.
End with:

```text
3实现排队：等待仓库
当前阶段：`$guided-implementation`
阶段状态：等待
恢复类型：等待仓库租约
目标仓库：<absolute path>
租约文件：<absolute path>
租约所有者：<verified task and host IDs>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
专用任务：none
下一阶段：none
恢复方式：租约释放后回复 `重试`；无需选择 commit、stash、清理或丢弃
```

When available, write one ordinary private input outside the repository with
exact repository, adopted base branch and `HEAD`, and originating task/host
IDs, then run `acquire-repository-lease`. Record its exact path, lease ID, byte
count, SHA-256 and completion marker. The tool publishes one canonical `0400`
lease file inside the real `.git` directory without replacement.

Immediately rerun the documentation-aware status, branch, `HEAD`, managed-link and
artifact checks after acquisition. Release the lease and retry from inspection
if a race changed only the clean descendant base before any handoff or branch
effect. Stop for dirty state, divergence, changed authority, or an ambiguous
effect. Never overwrite, chmod, recreate, or manually delete a lease.

Exclusive acquisitions use mode `exclusive-checkout-v2`. The lease is cooperative
but mandatory for Git state, implementation-path mutation, branch switching,
staging, commits, stash, reset, clean, merge, and branch removal. It does not
grant or deny documentation writes.

Read [references/document-lease-protocol.md](references/document-lease-protocol.md)
completely. Stages 1 through 4 coordinate every repository documentation write
through the shared CAS document lease. While stage 3 owns the repository lease,
other requirements may question, read committed objects pinned to the lease
base, and edit exact documentation paths under the document lease. They must
not inspect mutable implementation state, run project commands against it,
stage, commit, switch branches, or perform workspace cleanup.

Stage 3 may also acquire the document lease with `purpose: document-write` to
update documentation, but it leaves every such path unstaged for stage 4. For
candidate commits, branch switching, merge preparation, merge commits, and
final Git verification, acquire `purpose: git-stability-barrier`, perform no
document write, and release immediately afterward. Use the protocol's default
ten 10-second retries and fixed timeout report.

## Resolve unknown workspace blockers

Use workspace assistance only when no valid lease is held by another task and
the documentation-aware status contains unknown user-owned changes. Read and follow
`references/originating-task-protocol.md` section `Resolve unknown workspace
blockers`. Generic approval authorizes inspection and a proposal only; exact
preservation choices must be selected before acting, and destructive actions
require separate exact current-turn authority.

Never treat a held repository lease as a dirty-workspace decision. Queue behind
the lease without asking the user to commit, stash, move, or discard another
task's work.

## Build and publish the implementation authority

Read `references/originating-task-protocol.md` sections `Build the
implementation authority`, `Publish the immutable handoff`, and `Build the
dedicated-task bootstrap`. Read `references/execution-protocol.md` completely
and use the mode dispatch in `references/execution-modes.md`.

Build one complete `handoff_version: 4` envelope from accepted decisions and
verified evidence. Include exact project identity, upstream and adopted base,
the frozen execution mode, committed read-only implementation-source
checkpoint, parallelism receipt, exact checkout/worktree binding, matching
repository or worktree-execution lease, implementation-branch rules, recognized unstaged
documentation paths, document-lease protocol, Work Item order,
Requirement Sources, acceptance conditions, testing seams, artifact
provenance, trusted repository evidence, least-privilege capability scope, and
the fact that stage-2 publication authority has ended.

Reject every uncommitted planning authority, unreadable or ambiguous source,
and unaccepted third-party content before task creation. Recognized unstaged
documentation is workspace state, not implementation authority. Keep committed
local authority as path, Git-state, blob, and SHA-256 evidence. Snapshot only
remote material already accepted by the current flow. Never include credentials
or reconstruct missing authority from memory.

Use only `supervision_protocol.py create-handoff` to publish one immutable
`handoff.json` containing the exact execution protocol. Do not manually
serialize, replace, repair, chmod, relocate, or delete it. If publication or
task creation fails, preserve both handoff and lease and use the exact launch
recovery checkpoint. An ambiguous creation result must resolve task identity
before retrying.

## Create one dedicated task

Create exactly one Codex project task with:

- the saved project matching the repository;
- environment: the exact ordinary checkout or isolated worktree frozen in the
  handoff;
- model: `gpt-5.6-terra`;
- reasoning effort: `high`;
- title: `3实现 · <concise change name>`;
- a short bootstrap containing only the dedicated role, authenticated source
  identity, handoff path and integrity facts, matching execution-lease facts,
  mandatory `verify-handoff` plus mode-specific lease verification commands, and instruction to
  follow the embedded execution protocol.

Stage entry authorizes this one task only, not another checkout, another task,
remote writes, pushes, pull requests, releases, or tracker mutations. After
successful creation, record task, host, handoff, lease, and `流程模式`; end the
launch turn immediately. Never poll implementation progress.

## Supervise by terminal push

Read `Exchange supervision controls` in both protocol references. Use only the
deterministic publication and canonical control commands. Every message
must be source-authenticated, direction-correct, and bound to the handoff and
latest cumulative manifest. Authenticate child-to-parent delivery against the
recorded dedicated task and parent-to-child delivery against the initial
delegation wrapper's `source_thread_id`; never trust identity copied into a
message body.

Every new cross-task message in either direction must be generated by
`create-control` as one canonical v6 Chinese message card with
`message_format: supervision-chinese-card-v1`. The visible message uses a fixed
Chinese title and labels for direction, action, message ID, handoff ID, reply
message, manifest, file count, checkpoint, commits, merge result, and summary.
It contains no JSON and does not repeat delivery instructions. The script binds
the card to `protocol: supervision-duplex-observe-v1`, one derived
`message_id`, the exact final payload package, a five-second delivery check,
one resend limit, and at-most-once processing. Write the summary and checkpoint
in concise Chinese. Put detailed reports and instructions in the verified
payload. Reject a changed card, unsupported route, or v1-v5 downgrade under a
current card-format handoff. Allow an older control only for an
already-published handoff embedding that exact older format.

For every such message, execute the normative
`supervision-delivery-observe-v1` state machine in
`references/execution-protocol.md` exactly. Send only terminal controls with
`send_message_to_thread`. After each send, wait five seconds, read the exact
authenticated target task once, and inspect only byte-identical canonical
message plus `message_id` presence. Resend once only after proven absence. The
receiver authenticates the platform source, runs `verify-control`, and applies
each `message_id` at most once; it sends no delivery ACK. Forbid every transcript
read except the state machine's exact-target delivery audit. Do not duplicate
or locally vary its delay, audit, resend, terminal-state, or duplicate-effect
rules.

Supported terminal statuses remain `PARENT_REVIEW_REQUIRED`,
`PARENT_DECISION_REQUIRED`, `PARENT_BLOCKED`, and `MERGE_RESULT`; parent actions
remain `PARENT_REMEDIATION`, `PARENT_DECISION`,
`PARENT_ACCEPTED_FOR_MERGE`, and `PARENT_RETRY_MERGE`.

## Decide, accept, and remediate

For a decision or blocker, use the Requirement Source, project instructions,
and capability scope first. Ask the user only for a genuinely absent material
product, scope, security, permission, external-side-effect, or irreversible
decision. The dedicated task never asks the user directly.

For a candidate, independently verify the exact mode-specific execution lease, current
implementation branch, candidate commit, implementation-only commit range,
clean implementation paths and index, exact unstaged documentation paths,
document-write receipts, changed paths,
acceptance evidence, focused and full checks, typecheck/lint/build where
applicable, both review axes, unresolved risks, planning-artifact blobs, and
managed-link baseline. Do not require the base branch `HEAD` to equal the
upstream stage-entry `HEAD`; safe drift was already adopted before lease
acquisition, and the lease now prevents compliant mid-run writes.

If acceptance fails, send exact checks back to the same dedicated task. Never
edit implementation code in the originating task or create a replacement task
merely because review failed. Stop for user direction when the same material failure repeats without new evidence
or remediation would broaden authority.

Keep the same mode-specific execution lease through every decision, blocker,
remediation, acceptance, integration and merge checkpoint. Exclusive mode cannot safely lend the checkout to
another task while this implementation branch may need recovery. Other tasks
that require checkout mutation queue without asking the user to clean this
repository; problem framing and solution design may continue under the rules
above, and repository documentation work may continue under the document
lease. Release the repository or worktree-execution lease only at its verified
mode-specific cleanup checkpoint after a successful merge or an
explicitly authorized and mechanically proven cancellation that restores the
clean base branch.

## Authorize and verify the merge

After acceptance, send `PARENT_ACCEPTED_FOR_MERGE` naming the exact candidate.
For isolated mode first acquire `purpose: serial-integration`, rerun
`revalidate-integration` for current source, dependencies and active
implementations, then integrate exactly one candidate as specified by
`execution-modes.md`. Keep the isolated worktree, branch and execution lease
until 4归档. For exclusive mode, continue with the ordinary-checkout merge below.

The dedicated task reruns `verify-handoff`, `verify-control`, and
`verify-repository-lease`; confirms the implementation branch and checkout are
implementation-clean; acquires a `git-stability-barrier` document lease,
requires the implementation range and staged paths to contain no documentation,
switches the same checkout to the recorded base branch, confirms its `HEAD`
still equals the lease base, and runs
`git merge --no-ff --no-commit <implementation-branch>`.

On conflict or merge failure, abort without resolving or editing code and prove
the base state was restored. If the merge applies, run final verification,
verify the staged paths contain no documentation, and commit only when it
passes. Release the document barrier immediately after the stable Git
operation. Do not push, delete the implementation branch, stage documentation,
perform closure, or release the repository lease from the dedicated task.

After a verified `MERGE_RESULT`, the originating task directly verifies the
base branch, merge commit, candidate ancestry, implementation-clean checkout,
unstaged documentation list and hashes, managed links, supervision evidence,
and exact lease. In exclusive mode only, run `release-repository-lease` and
require `state: available` plus `verified_absent: true`. In isolated mode keep
the exact execution lease, worktree and branch held for 4归档 and report them as
pending cleanup.

## Complete stage 3

Emit:

```text
实现结果：成功
合并结果：成功
专用任务：<thread id>
任务主机：<host id>
目标仓库：<absolute repository path>
执行模式：<exclusive-checkout-v2 | isolated-worktree-v1>
原始工作区：<absolute repository path>
基础分支：<branch>
上游记录 HEAD：<sha>
执行基础 HEAD：<adopted sha>
实现分支：<branch>
Worktree：<none | exact isolated path retained for 4归档>
实现依据类型：Ticket | Spec | 1拷问小改动
实现依据：<clickable document reference or concise confirmed handoff>
Spec：<clickable path or URL, or none>
Tickets：<clickable paths or URLs, or none>
实现提交：<sha>
合并提交：<sha>
验证：<commands and results>
实现提交文档检查：通过；实现提交不包含文档
合并提交文档检查：通过；合并提交不包含文档
待归档文档：<exact paths, final SHA-256, document lease id/version, purpose and reason | none>
其它未暂存文档：<exact paths and ownership facts | none>
工作区基线：实现区与暂存区干净；文档区仅含上述未暂存改动；管理链接已验证
执行租约：<repository lease released with verified_absent=true | worktree execution lease held with exact id/version/path>
未解决风险：<risks or none>
交接文件：<absolute handoff.json path>
交接ID：<handoff id>
交接字节：<byte count>
交接SHA-256：<full-file sha256>
交接完成标记：HANDOFF_COMPLETE:<handoff id>
监督清单元数据：<exact canonical manifest_file JSON object>
监督文件数：<count>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
下一阶段：`$change-closure`（4归档）
进入条件：已满足
默认授权：更新并提交本地归档文档，安全清理已合并的实现分支、交接文件与监督文件
远程操作：未授权
继续方式：逐阶段确认时，回复完整的 `确认` 仅进入 4归档，或回复完整的 `执行后续全部流程` 立即进入 4归档；连续执行时无需回复，立即进入 4归档
```

In `逐阶段确认`, accept only trimmed whole-string `确认` or
`执行后续全部流程`; reject punctuation, prefixes, suffixes, added conditions,
`继续`, and `可以`. In
`连续执行后续全部流程`, immediately invoke `$change-closure` in the same
originating task. Preserve handoff and supervision files through every blocked,
rejected, or ambiguous result. Legacy in-flight handoffs embedding the previous
worktree protocol may finish under their embedded protocol; never convert them
mid-run.
