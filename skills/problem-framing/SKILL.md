---
name: problem-framing
description: Use only when the user explicitly invokes $problem-framing to sharpen an idea, requirement change, or cautious small change; explicitly invokes $problem-framing 重试 after this Skill's stable recovery, repository-lease, document-lease, or pending-planning-commit footer; replies to this Skill's immediately preceding confirmation or workspace-decision block; or sends a verified $problem-framing 接收拷问交付 payload from a dedicated grilling task. Assess whether target-relevant context should remain in the current task or be consolidated into a user-confirmed draft and a separate same-project local grilling task, coordinate every repository documentation write through the shared CAS document lease while stage 3 owns Git and implementation paths, preserve grill-with-docs CONTEXT.md and ADR responsibilities, commit only exact stage-owned documentation after the repository lease is available, and offer the manual 执行后续全部流程 mode only in a successful final footer.
---

# 1拷问

Turn the user's target into a shared, implementation-relevant understanding.
Use Matt Pocock's discussion Skills for the questioning itself. Do not choose
the engineering implementation or write implementation code in this stage.
This restriction never permits deferring an unresolved behavior contract.

## Attach a verified discussion source

At entry, apply the discovery order in
[`../design-discussion/references/lifecycle-integration.md`](../design-discussion/references/lifecycle-integration.md).
Only attach when authenticated phase or handoff evidence, the active binding,
the exact topic document or stable footer, and read-only `discover-context`
resolve one `ledger` or authorized `document_only` context. On `none` or
`ambiguous`, make no discussion-protocol write and continue every existing
entry, draft, publication and footer rule below unchanged. Stop on a strong
identity conflict; do not ask the user to choose an internal identity.

In an attached context, 0 and 1 own one requirement document: the existing
`docs/discussions/<root-slug>/topic.md`. Do not create a problem-framing draft
or migrate that document to another path. The active topic conversation may
run 1 directly. When a dedicated grilling carrier is needed, the source topic
must first publish the latest effective `stage-entry` checkpoint and prepare a
`0->1` wrapper Phase Run with carrier kind `dedicated-grilling`. The carrier
must verify that exact checkpoint identity, claim its authorized `PA-*`, report
ready, and wait. It may ask questions or prepare a shared-topic write only
after the source topic rechecks evidence and activates the run. While active,
all durable requirement updates still use the topic's immutable `DW-*` payload
and `design-discussion` document-lease path; the dedicated carrier never owns a
second draft.

Before routing from 0 or 1, resolve every pending impact and publish the latest
effective 0/1 checkpoint. Use `prepare-wrapper-phase-run`, not the generic
Phase Run operation. Route `1->3` only when scope, behavior, failure semantics,
acceptance conditions and an adequate test seam are all complete. Its source
activation records phase 2 as `not_applicable` for the exact implementation
scope and creates no Spec, ADR, Ticket or planning commit. Continuous mode can
be selected only by the exact response to the successful stage-1 footer; a
stage-0 route is always stepwise.

## Enter the stage

- Start only from an explicit `$problem-framing` invocation.
- Accept `$problem-framing 重试` only after this Skill's immediately preceding
  stable recovery, document-lease timeout, or pending-planning-commit footer.
- Accept `$problem-framing 接收拷问交付` only as the first line of a dedicated
  delivery payload defined in
  [references/dedicated-grilling-protocol.md](references/dedicated-grilling-protocol.md).
- Treat replies to this Skill's immediately preceding confirmation or workspace
  decision block as continuations of that exact block. They never authorize an
  unlisted action or another stage.
- Outside these routes, plain `确认`, `继续`, `可以`, `重试`, or
  `执行后续全部流程` never invokes this explicit-only Skill.
- Accept ordinary natural language. Never require a parameter block or require
  the user to restate information already available in the task.
- If neither the invocation nor the available context identifies the target,
  ask one concise question.

## Use one confirmation contract

For every action that requires confirmation, use the fixed confirmation block
in [references/templates.md](references/templates.md). The only affirmative
confirmation token is `确认`.

A confirmation:

- binds only to the immediately preceding fixed block in the same task;
- authorizes only the exact listed actions;
- never crosses tasks or stages;
- becomes invalid after any edit, discussion, changed file or hash, model,
  reasoning effort, task identity, project, target, or configuration;
- after trimming surrounding whitespace, equals exactly `确认`; punctuation or
  any other suffix is not confirmation; and
- treats `确认，但……` or any added condition as a modification request.

After a modification or failed action, show a fresh complete block and require
`确认` again. The exact token `执行后续全部流程` is a final-footer mode selection,
not a confirmation method.

## Choose the context path

Before starting the Matt flow, assess the available task history for the current
target. Recommend a dedicated task when any of these materially threatens
reasoning quality or context completeness:

- earlier history has already been compacted, summarized, or made unavailable;
- substantial unrelated discussion surrounds the target-relevant context; or
- the relevant facts and decisions are spread across enough history that
  continuing would risk omission or degraded questioning.

Do not use a numeric token guess as the sole reason and do not recommend a new
task merely because the target is difficult.

Do not assess or offer migration until the target is identified well enough to
separate relevant context from unrelated history. If it is not, ask the entry
question first. Populate the migration offer from currently available task,
project, and repository context. For a value that is not yet available, state
`未解析（创建确认前解析）` instead of guessing; freeze every exact identity before
the later task-creation confirmation.

- If the context is still coherent and focused, continue in the current task.
- If it is long or mixed, show the fixed migration offer from
  [references/templates.md](references/templates.md).
- If the user declines, continue in the current task without creating a draft
  solely for migration.
- If the user replies `确认`, read
  [references/dedicated-grilling-protocol.md](references/dedicated-grilling-protocol.md)
  completely and follow its source-task protocol.

## Inspect the lease before the project baseline

Resolve the repository root read-only, then run `inspect-repository-lease`
before reading mutable checkout state or creating any draft.

- If the lease is available, establish the normal project baseline below.
- If the lease is held, do not inspect the owning task's mutable implementation
  paths, branch, index, or code status. Record the verified lease base branch
  and base `HEAD` as the pinned repository snapshot. Read implementation
  evidence only from committed Git objects pinned to that `HEAD`, such as with
  `git show` or `git cat-file`. Repository documentation writes remain allowed
  only through the shared document lease below.

## Establish the project baseline

Before any selected Matt flow or migration draft writes project documentation,
record the repository root, attached branch, `HEAD`, staged diff, and
`git status --porcelain=v1 -z --untracked-files=all`.

For standardized status, exclude only an untracked
`.agents/skills/<name>` entry that is a symbolic link resolving exactly to the
installed Skill directory for `<name>`. Resolve the expected target from the
active Skill registry instead of assuming a user-specific install root. Record
each excluded path and target. Never exclude a tracked change, non-symlink,
wrong target, parent directory, or any other `.agents` path.

The checkout is documentation-aware clean when the branch is attached, the
index contains no unrelated staged change, implementation paths contain no
unknown change, and every remaining unstaged or untracked entry is an exact
recognized documentation path or verified managed Skill link. Never silently
classify an unknown path as documentation.

## Coordinate document writes and repository commits

Read
[../guided-implementation/references/document-lease-protocol.md](../guided-implementation/references/document-lease-protocol.md)
completely before the first repository documentation write, document-lease
retry, renewal, or release.

Immediately before every documentation write, acquire the shared document
lease with `stage: problem-framing` and `purpose: document-write`. Use its
default immediate attempt plus ten 10-second retries. Proceed only on
`acquired: true`; on `state: timeout`, emit the protocol's fixed Chinese timeout
block and stop the dependent operation. Verify immediately before writing,
renew when necessary, and release immediately after the bounded edit. Never
hold it while waiting for the user or a Matt flow.

While stage 3 holds `exclusive-checkout-v2`, write only exact documentation
paths. Do not stage, commit, switch branches, stash, clean, reset, or touch
implementation paths. Keep using pinned committed objects for code evidence.
Record the active repository lease identity and document-lease ID/version in
the draft metadata.

At the completion gate, freeze the accepted draft bytes and recheck the
repository lease:

- if available, acquire the repository mutation authority required by this
  stage, revalidate the branch, descendant `HEAD`, documentation-aware status,
  target path, and native-document blobs, then stage and commit exact
  stage-owned documentation;
- if held, keep the completed in-repository draft unstaged and end with the
  pending-planning-commit footer from `references/templates.md`. Do not repeat
  questioning or the unchanged completion confirmation.

On `$problem-framing 重试`, inspect actual draft bytes, document lease, repository
lease, branch, `HEAD`, target paths, and native-document blobs. When the
repository lease becomes available and the frozen understanding remains valid,
commit and deliver the exact stage-owned documentation. A target collision,
divergent base, or changed shared document requires reconciliation; never
silently stage another task's documentation.

The repository lease is required for staging, commits, branch/workspace
actions, and transition checks. It is not required for a document-lease-owned
documentation edit.

## Run the discussion flow

1. Invoke `$ask-matt` with the current target and only its relevant context.
2. Follow the Matt flow it recommends within this stage's discussion boundary.
3. Expect `$grill-with-docs` for ordinary ideas, requirement changes, and
   cautious small changes. Allow `$ask-matt` to select another Matt workflow
   for difficult bugs, broad work, research, prototypes, or domain work.
4. Preserve every selected Skill's native behavior and documentation rules.

While stage 3 holds the repository lease, do not enter a Matt workflow that
requires mutable implementation-path inspection, project commands, prototype
code, or Git mutation. Repository documentation writes remain available only
through the document lease. Record unavailable implementation activity, use
pinned committed objects for safe investigation, and continue questioning. If
a material behavior question cannot be resolved safely, stage 1 cannot complete
until evidence becomes available.

Do not duplicate Matt's questioning, investigation, ADR, research, prototype,
or domain-modeling behavior. Do not invoke `$to-spec`, `$to-tickets`, or
`$implement` from this stage. Treat those recommendations only as routing
signals for a later stage.

## Resolve behavior before deferring implementation

Interpret the stage boundary by effect, not by whether a question mentions a
client, server, tool, prompt, API, schema, or UI.

Stage 1 must settle every decision that can change:

- user-visible results, messages, choices, or interaction flow;
- whether, when, or how many times the model or a tool is invoked;
- continuation, termination, retry, cancellation, or failure semantics;
- authorization, validation, safety, and recovery behavior;
- business or session state transitions, ordering, atomicity, or idempotency;
- the authoritative source or responsible component when that choice changes
  any behavior above; or
- acceptance conditions and externally testable outcomes.

Do not defer one of these decisions merely because the alternatives are named
after technical carriers such as `show_content`, a client renderer, a server
event, or a new result type. First freeze the observable behavior and semantic
responsibility. Never reopen that decision by listing incompatible carriers as
stage-2 alternatives.

Stage 2 may choose only implementation mechanics that preserve the frozen
behavior contract, such as file and module boundaries, internal adapters, exact
field or endpoint names, code sequence, and test organization. For every
deferred item, record the invariant behavior it must preserve and why the
remaining alternatives cannot change stage-1 semantics. If that cannot be
shown, keep questioning in stage 1.

When a migration draft exists, continuously improve it throughout the entire
questioning process. The goal is the best attainable complete draft, not a
verbatim transcript. Keep these document authorities distinct:

- the draft is the complete target-specific narrative and cross-task handoff;
- `CONTEXT.md` is the canonical domain terminology source;
- ADRs own hard-to-reverse architectural decisions; and
- stage-2 Specs and Tickets are the formal planning artifacts.

Summarize and link native documents from the draft instead of copying them
wholesale. Later user corrections override older statements.

## Commit stage-owned documentation

After shared understanding is reached:

1. Freeze the exact accepted draft bytes and record the completion confirmation.
2. Collect only durable documentation created or modified by the selected Matt
   flows and this stage's draft. Exclude prototype code, implementation files,
   generated output, and unrelated user files.
3. Do not commit a path that was already staged, unstaged, or untracked in the
   recorded baseline. Report it as pre-existing user work.
4. If any unrelated staged change exists, do not stage or commit stage-owned
   files.
5. Inspect the repository lease. When it is held, leave the frozen stage-owned
   documents unstaged, emit the pending-planning-commit footer, and do not
   repeat completed questioning.
6. When it is available, stage only exact stage-owned documentation paths,
   verify the
   staged diff contains nothing else, and commit with a concise discussion
   documentation message. The explicit invocation authorizes this exact local
   documentation-only commit.
7. If no durable document changed, record `规划提交：none`; never create an empty
   commit.
8. Recompute documentation-aware status. Do not offer the next stage while the
   index, implementation paths, or this stage's document paths remain dirty.
   Unrelated recognized documentation paths may remain unstaged and must not be
   included in this stage's commit.

## Resolve pre-existing workspace work

When unrelated or pre-existing work blocks a documentation commit or clean
transition, inspect status and diffs read-only, separate stage-owned files from
user work, and ask:

```text
工作区处理：需要决策
当前阶段：`$problem-framing`
阶段状态：受阻
恢复类型：协助处理工作区
待处理改动：<exact staged, unstaged and untracked paths and states>
恢复检查点：仓库=<absolute path>; 分支=<branch>; HEAD=<sha>; 规划文档=<exact paths or none>; 共同理解=<concise accepted understanding>
下一阶段：none
待决问题：检测到已有工作区改动。是否由我协助处理这些改动，以便继续 1拷问？我会先保留并说明处理方案，不会擅自丢弃内容。
恢复方式：回答上述问题；无需重新调用 Skill
```

A generic approval authorizes read-only inspection and a concrete proposal,
not a silent choice among commit, stash, relocation, or discard. If the answer
does not select an exact result, show only reasonable preservation choices and
ask one material question.

Before acting, state the working directory, exact commands, flags, pathspecs,
affected paths, commit message, and whether untracked files enter a stash.
Reset, clean, checkout replacement, overwrite, deletion, force, or another
unrecoverable discard require separate explicit current-turn authorization for
the exact targets.

After the selected action, revalidate repository, branch, `HEAD`, baseline
ownership, documentation authority, accepted understanding, and standardized
status. Resume automatically only when they remain valid.

## Retry a mechanical stage-owned failure

Use retry only after shared understanding is complete and one exact document
lease, planning commit, delivery, intake, archive, or transition gate is
mechanically recoverable, or after the pending-planning-commit footer. Never
repeat completed questioning. Record the fixed
failure checkpoint from [references/templates.md](references/templates.md).

On `$problem-framing 重试`, inspect actual state first and resume from the last
successful checkpoint. Retry a clearly side-effect-free operation
automatically at most once. Never retry a write with uncertain side effects
until its actual state has been verified.

## Choose the next stage in the current task

For the non-migrated current-task flow, select exactly one stage when evidence
is sufficient:

- choose `$solution-design` when engineering design, contracts, architecture,
  decomposition, or a multi-step implementation plan is still needed;
- choose `$guided-implementation` only when the change is small and local and
  behavior, constraints, affected area, acceptance conditions, and an adequate
  testing seam are already clear.

When choosing `$solution-design`, resolve and disclose the exact configured
planning carrier and target. Explain that entering `2方案` authorizes Matt's
standard native `$to-spec` and approved `$to-tickets` actions only on that
target. Do not add another Matt publication gate and do not carry this authority
into stages 3 or 4.

## Complete the current-task flow

After the stage-owned documentation commit and documentation-aware checks pass, end with a
short conclusion and this stable footer:

```text
阶段结果：拷问完成
共同理解：<concise understanding>
规划提交：<sha | none>
工作区状态：实现区与暂存区干净；本阶段文档已提交；其它文档改动未纳入
规划载体：<exact local convention, GitHub owner/repository, or tracker target | none for direct implementation>
Matt 原生动作：<publish Spec; review and publish Tickets when needed; apply native labels and blocking links | none for direct implementation>
阶段授权：<entering 2方案 authorizes only those standard actions on that exact target | not applicable>
下一阶段：`$solution-design`（2方案） | `$guided-implementation`（3实现）
进入条件：已满足
交接来源：当前任务中已确认的需求、约束、范围和验收条件
确认事项：进入上述推荐阶段
确认方式：回复 `确认`
流程模式：回复 `执行后续全部流程` 进入推荐阶段，并在无需用户决策时自动顺序执行剩余阶段
```

Only an exact reply to this successful footer may enter the next stage. `确认`
enters the selected stage in stepwise mode. `执行后续全部流程` enters it with
`流程模式：连续执行后续全部流程`.

Continuous mode exists only at this final successful footer. It has no role in
context migration, drafting, task creation, questioning, completion approval,
or delivery. Preserve every blocker, authority, ambiguity, validation, and
material-decision gate. Treat corrections, objections, cancellation, or
changed requirements as continued problem framing and emit a fresh footer.

## Receive a dedicated-task delivery

For `$problem-framing 接收拷问交付`, read the dedicated protocol and templates
completely. Perform only the specified mechanical identity, commit, path, hash,
completion-marker, user-confirmation, and documentation-aware checks. Do not
semantically re-review the grilling result.

After successful intake, show the dedicated success footer. Handle its reply in
this Skill; do not let `$solution-design` activate directly from the pre-archive
footer. Only exact `确认` archives the dedicated grilling task and then explicitly
enters `$solution-design` in stepwise mode. The exact final-footer mode selection
`执行后续全部流程` archives it and then explicitly enters `$solution-design` with
continuous mode. A dedicated-task delivery never routes directly to
`$guided-implementation`.
