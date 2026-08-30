# Originating Task Protocol

Use this reference only in the originating supervising task. The dedicated
Terra/high task follows the execution protocol embedded in its verified
handoff. Read only the named section immediately before performing that action.

## Contents

- Retry a blocked launch
- Resolve unknown workspace blockers
- Build the implementation authority
- Publish the immutable handoff
- Build the dedicated-task bootstrap
- Exchange supervision controls
- Preserve a supervision decision checkpoint

## Retry a blocked launch

Use launch recovery only when no dedicated task was created and the exact
condition can be revalidated mechanically. Preserve an acquired repository
lease; never create a second lease for the same checkpoint.

```text
3实现启动：受阻
当前阶段：`$guided-implementation`
阶段状态：受阻
恢复类型：重试当前阶段
恢复条件：<exact condition>
恢复检查点：仓库=<absolute path>; 分支=<branch or unresolved>; 上游记录 HEAD=<sha or unresolved>; 执行基础 HEAD=<sha or unresolved>; 规划提交=<sha or none>; 工作项模式=<mode>; 规划文档=<paths or URLs and states>; 管理链接基线=<exact paths and targets or unresolved>; 仓库租约=<path, id, bytes, sha256 and state or none>; 交接文件=<absolute path or none>; 交接ID=<id or none>; 交接字节=<count or none>; 交接SHA-256=<sha or none>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
专用任务：none
下一阶段：none
恢复方式：满足条件后回复 `重试`
```

On retry:

1. Revalidate project, ordinary checkout, branch, current `HEAD`, safe
   descendant-base adoption, documentation-aware status, managed links, planning
   commit, artifact blobs, and complete authority.
2. Inspect or verify the recorded lease. Never overwrite it. If no lease was
   acquired, acquire one only after implementation paths and the index are clean,
   recognized unstaged documentation is recorded, and current authority is complete.
3. If a handoff exists, rerun exact handoff verification and require metadata,
   lease, accepted facts, and embedded protocol hash to match. Never overwrite
   it.
4. Create at most one task. Resolve ambiguous creation before another attempt.
5. Do not implement, create a worktree, switch to an implementation branch, or
   enter a later stage during originating-task recovery.

Pre-existing user work is not a mechanical retry. A valid lease held by another
task is a queue state, not workspace assistance.

## Resolve unknown workspace blockers

Use this only when no other valid repository lease explains the occupied
checkout and unknown user-owned work blocks launch or recovery. Inspect status
and diffs read-only and offer assistance instead of directing the user to clean
manually.

Before a dedicated task exists, use:

```text
工作区处理：需要决策
当前阶段：`$guided-implementation`
阶段状态：受阻
恢复类型：协助处理工作区
专用任务：none
待处理改动：<exact staged, unstaged and untracked paths and states>
恢复检查点：仓库=<absolute path>; 分支=<branch>; 上游记录 HEAD=<sha>; 当前 HEAD=<sha>; 规划提交=<sha or none>; 规划文档=<paths or URLs and states>; 管理链接基线=<exact paths and targets>; 仓库租约=<none or exact released state>; 交接文件=<path and identity or none>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
下一阶段：none
待决问题：检测到无法归属给其它有效租约的工作区改动。是否由我协助保留并处理这些改动，以便继续 3实现？我不会擅自丢弃内容。
恢复方式：回答上述问题；无需重新调用 Skill
```

When a dedicated task exists, preserve its exact task, host, handoff, lease,
manifest, branch, candidate, and authenticated-source checkpoint. The lease
normally remains held because zero-worktree recovery cannot safely lend the
same checkout to another task.

A generic approval authorizes read-only inspection and a concrete proposal,
not a silent choice among commit, stash, relocation, or discard. If the user
did not choose an exact result, present only reasonable preservation options
and ask the material choice. State working directory, commands, flags,
pathspecs, paths, commit message, and stash scope before acting.

Reset, clean, checkout replacement, overwrite, deletion, force, or any other
unrecoverable discard requires separate explicit current-turn authority for
exact targets. Never mix planning or implementation artifacts into unrelated
user-work commits.

After handling, revalidate repository, branch, current `HEAD`, safe base
adoption, managed links, planning authority, handoff, lease, and supervision
phase. Automatically resume only when all facts remain valid. Never hide drift
merely to make status clean.

## Build the implementation authority

Build one `handoff_version: 4` envelope containing:

- source stage and exact project identity;
- Work Item mode and complete ordered Work Items;
- one complete Requirement Source per Work Item;
- confirmed testing seams, verification commands, accepted decisions,
  constraints, out-of-scope items, and risks;
- exact planning carrier and Matt publication evidence plus the fact that
  stage-2 authorization has ended;
- upstream recorded branch/`HEAD`, planning commit, current adopted execution
  base, and evidence proving safe descendant adoption when they differ;
- one frozen `execution_mode`, the exact completed Git `implementation-source`
  checkpoint identity and SHA-256, and its current parallelism receipt;
- for `exclusive-checkout-v2`, `worktree_count: 0`, the exact ordinary checkout,
  implementation-branch rules and repository lease; for
  `isolated-worktree-v1`, the explicitly confirmed path, branch, base, scope
  digest, sensitive shared surfaces and long-lived worktree execution lease;
- implementation-clean status, exact recognized unstaged documentation paths,
  and exact managed-link baseline at lease acquisition;
- repository-lease path, ID, byte count, SHA-256, completion marker, base
  branch, base `HEAD`, and originating owner task/host;
- trusted repository-base evidence or exact-SHA attestation;
- every Spec and Ticket artifact with location, readability, Git state, blob,
  and SHA-256 when available;
- provenance proving every Requirement Source was accepted by this flow,
  directly supplied, or exactly attested;
- least-privilege capability scope: writable exact checkout/worktree only while
  its repository or worktree execution lease is verified for Git and
  implementation paths; document
  writes only while the shared document lease is verified; append-only
  supervision files, read-only supporting paths, allowed
  Git/project/task-control commands, and network `none` unless an exact
  host/action is separately authorized;
- exact Work Item count, artifact count, and embedded execution-protocol
  SHA-256; and
- `unresolved_material_questions: none`.

The Requirement Source is the complete Ticket plus relevant Spec sections, the
complete Spec, or the accepted direct small-change facts. Do not copy the full
conversation.

Record a local artifact only when it is a committed regular non-symlink file
whose accepted blob remains unchanged at the adopted base. Require exact
current-flow provenance. Reject uncommitted, unreadable, symlinked, ambiguous,
or unreviewed third-party material. For accepted remote material, include its
real URL, accepted snapshot, hash, and publication evidence without fetching a
new body in stage 3. Never include credentials.

The envelope is complete only when the project, Work Items, Requirements,
testing seams, ordinary checkout, safe adopted base, lease, trusted repository,
capability scope, artifacts, Ticket graph, and protocol identity are verified,
no material question remains, and canonical handoff size fits 524288 bytes.

## Publish the immutable handoff

Use only:

`<guided-implementation-skill-root>/scripts/supervision_protocol.py`

with runtime root:

`${CC_SWITCH_RUNTIME_ROOT:-$HOME/.cc-switch/runtime}/guided-implementation-handoffs`

1. Read `execution-protocol.md` completely and finish authority, provenance,
   base-adoption, lease, and capability judgments first.
2. Write one ordinary UTF-8 JSON input outside repository and runtime root with
   exactly `envelope`, `work_items`, and `artifacts`.
3. Run `create-handoff --input <path>`.
4. Require exit `0`; record path, bytes, SHA-256, and
   `HANDOFF_COMPLETE:<handoff-id>`.
5. Delete only the ordinary input after successful publication. Never create,
   rewrite, chmod, relocate, or repair a runtime file manually.

Preserve handoff and its mode-specific execution lease through ambiguous task creation,
implementation, acceptance, merge, and closure blockers. They are recovery
authority.

## Build the dedicated-task bootstrap

Build a short prompt containing only:

1. the dedicated execution role, one originating supervisor, and prohibition
   on asking the user or handing implementation to another top-level task;
2. handoff path, ID, bytes, SHA-256, and completeness marker;
3. repository-lease path, ID, bytes, SHA-256, and completion marker;
4. prohibition on Git, project execution, implementation, branch switching,
   worktrees, and network until exact `verify-handoff` and the handoff's
   mode-specific lease verification command succeed; isolated verification
   must include the exact platform cwd;
5. instruction to derive supervisor identity only from authenticated
   `source_thread_id`, read the complete decoded `execution_protocol` returned
   by `verify-handoff`, and follow it verbatim without relying on the parent
   conversation or implicit instruction inheritance; and
6. an opening transport instruction that ordinary progress is never sent and
   every terminal control uses canonical send-message, waits five seconds,
   checks the authenticated target task for the exact message, and resends the
   byte-identical message once only after proven absence, with no ACK exchange.

Use this role wording, substituting only immutable facts and commands:

```text
You are the dedicated execution task.

An originating supervising task owns user communication, material decisions,
independent acceptance, merge authorization, and repository-lease release.
Complete the implementation defined by the immutable handoff inside the one
existing leased checkout. Create no Git worktree.

Use only the platform delegation wrapper's authenticated source_thread_id as
the supervisor identity. Do not ask the user directly. Ordinary progress must
not be sent. CROSS-TASK DELIVERY RULE: never send an ACK. When the embedded
protocol requires a terminal status, create its canonical Chinese message card
and send those exact bytes to source_thread_id. The card must begin with
`【3实现消息｜…】`, use the script-generated Chinese labels, contain no JSON, and
keep detailed content in the verified supervision payload. Wait five seconds,
read that exact target task once for the byte-identical card and message ID,
and resend the same bytes once only if the audit proves absence. Audit once
more after the resend, process each received message ID at most once, and only
then end the turn.

The successful `verify-handoff` result contains the complete decoded
`execution_protocol`. Read that field completely before dispatching any worker
or performing implementation, and obey it verbatim. It is the explicit
instruction transfer into this new task; do not rely on the originating
conversation or implicit inheritance.
```

Do not copy the envelope or protocol into the bootstrap.

Create the dedicated Codex project task exactly once with the saved project,
environment `local`, model `gpt-5.6-terra`, reasoning effort `high`, and the
bootstrap above. Pass those values explicitly as `model` and `thinking`; never
omit them, inherit the originating task's settings, or substitute another
pair. Record the returned task and host identities as the only trusted
dedicated task. If the current `create_thread` capability does not advertise
that exact pair, use the launch-blocked recovery without a creation attempt.

## Exchange supervision controls

Before sending or accepting a supervision message, read the complete exchange
section of `execution-protocol.md`. Use its script-only payload publication,
cumulative manifest, canonical control, direction, status/kind, and delivery
audit rules
unchanged.

The sender keeps payload and control-input files outside repository and runtime
root. Every new cross-task message in both directions uses the same canonical
v6 Chinese card with
`message_format: supervision-chinese-card-v1`. Its visible title and fields are
Chinese; it contains no JSON and omits delivery-policy boilerplate. The script
still binds it to `protocol: supervision-duplex-observe-v1`, one derived
`message_id`, `delivery_check_delay_seconds: 5`, and
`delivery_resend_limit: 1`. One exact-target audit after the delay, one
byte-identical resend only after proven absence, no ACK exchange, and
at-most-once duplicate effects remain machine-enforced. Existing v1-v5 messages
remain valid only for handoffs that already embedded their exact older format.

Authenticate message source before `verify-control`. After successful
verification and before acceptance, implementation, remediation, decision, or
merge, record the verified `message_id`; do not repeat its effect if the same
message is delivered again.

For every outbound message, execute `supervision-delivery-observe-v1` from
`execution-protocol.md` exactly; do not reproduce, override, or locally vary the
state machine. Its exact-target delivery audit is the only permitted transcript
read. Preserve its resulting checkpoint. A verified message or delivery audit
is transport evidence only and cannot widen authority.

## Preserve a supervision decision checkpoint

When user input is genuinely required, keep the same task and lease and end:

```text
3实现监督：需要决策
当前阶段：`$guided-implementation`
专用任务：<thread id>
任务主机：<host id>
监督阶段：<implementation | parent-acceptance | remediation | final-merge>
待决问题：<one exact question>
恢复检查点：仓库=<path>; 执行模式=exclusive-checkout-v2; 基础分支=<branch>; 上游记录 HEAD=<sha>; 执行基础 HEAD=<sha>; 当前分支=<branch>; 实现分支=<branch or none>; 候选提交=<sha or none>; 仓库租约=<path, id, bytes, sha256, complete and held>; 文档改动=<exact paths, hashes and lease receipts or none>; 交接文件=<path>; 交接ID=<id>; 交接字节=<count>; 交接SHA-256=<sha>; 交接完成标记=<HANDOFF_COMPLETE:id>; 监督清单元数据=<exact manifest_file JSON object or none>; 监督文件数=<count>; 消息来源=<verified dedicated task id>; 最近控制ID=<sha256 or none>; 送达状态=<confirmed | unconfirmed | not-applicable>
流程模式：<逐阶段确认 | 连续执行后续全部流程>
下一阶段：none
恢复方式：回答上述问题；主对话会继续同一个专用任务和仓库租约
```

On re-entry, locate the exact task, rerun handoff, lease, and supervision
verification, and resume only that phase. Put answers, remediation, decisions,
or merge retries in a new parent-to-child package and send only the canonical
Chinese message card. Never create another task, another lease, a worktree,
repeat an accepted candidate, or retry an ambiguous merge.
