---
name: problem-framing
description: Use only when the user explicitly invokes $problem-framing to sharpen an idea, requirement change, or cautious small change; explicitly invokes $problem-framing 重试 after this Skill's stable recovery, document-lease, source-protection, or pending-planning-commit footer; replies to this Skill's immediately preceding confirmation or workspace-decision block; or sends a verified $problem-framing 接收拷问交付 payload from a dedicated grilling task. Coordinate repository documentation writes through exact-path CAS leases, refuse active implementation sources, preserve CONTEXT.md and ADR responsibilities, and commit only exact stage-owned documentation through the short checkpoint-publication barrier.
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

## Load repository coordination only when needed

Before repository inspection, the first documentation write or commit, any
workspace decision, or a mechanical retry, read
[references/repository-and-recovery.md](references/repository-and-recovery.md).
Do not preload it for a current-task question that needs no repository action.

## Run the discussion flow

1. Invoke `$ask-matt` with the current target and only its relevant context.
2. Follow the Matt flow it recommends within this stage's discussion boundary.
3. Expect `$grill-with-docs` for ordinary ideas, requirement changes, and
   cautious small changes. Allow `$ask-matt` to select another Matt workflow
   for difficult bugs, broad work, research, prototypes, or domain work.
4. Preserve every selected Skill's native behavior and documentation rules.

While stage 3 is active, inspect implementation work only through committed
objects. Never enter another implementation worktree or edit a protected source
document. Unprotected documentation writes remain available through the exact
document lease. If a material behavior question cannot be resolved from safe
evidence, stage 1 cannot complete until evidence becomes available.

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

## Commit and recover through the routed reference

After shared understanding, follow the exact stage-owned commit, workspace and
retry rules in `references/repository-and-recovery.md`. The explicit invocation
authorizes only that bounded local documentation commit; it never widens into
implementation or destructive cleanup.

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
