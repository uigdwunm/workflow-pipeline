---
name: problem-framing
description: Use when the user explicitly invokes $problem-framing, clearly asks to begin 1拷问, or unambiguously accepts the immediately preceding proposal to begin it; also use for this Skill's retry, confirmation, workspace-decision, or verified dedicated-delivery continuations. Always create or inherit exactly one requirement document, keep it current throughout questioning, and commit and freeze it before handing it to $solution-design.
---

# 1拷问

Turn the user's target into a shared, implementation-relevant understanding.
Use Matt Pocock's discussion Skills for the questioning itself. Do not choose
the engineering implementation or write implementation code in this stage.
This restriction never permits deferring an unresolved behavior contract.

Read the shared
[`requirement-document-contract.md`](../design-discussion/references/requirement-document-contract.md)
before resolving or updating the requirement document. Phase 1 always has
exactly one such document and always hands it to Phase 2.

## Attach a verified discussion source

At entry, apply the discovery order in
[`../design-discussion/references/lifecycle-integration.md`](../design-discussion/references/lifecycle-integration.md).
Only attach when authenticated phase or handoff evidence, the active binding,
the exact topic document or stable footer, and read-only `discover-context`
resolve one `ledger` or authorized `document_only` context. On `none`, make no
discussion-protocol write and create one standalone Phase-1 requirement
document before the first substantive question. Use the draft schema in
[references/templates.md](references/templates.md), resolve an unused path from
the repository convention or `docs/problem-framing/<yyyy-mm-dd>-<short-target>.md`,
and record the current task as its sole write owner. On `ambiguous`, stop before a
write rather than risk creating a second requirement authority. Stop on a strong
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
all durable requirement updates still use the topic's immutable `DW-*` payload;
the dedicated carrier never owns a second draft.

Before routing from 0 or 1, resolve every pending impact and publish the latest
effective 0/1 checkpoint. Use `prepare-wrapper-phase-run`, not the generic
Phase Run operation. Phase 1 routes only to Phase 2. A natural-language request
for continuous execution can be selected only from the successful stage-1
footer; a stage-0 route is always stepwise.

Apply the same proactive split rule as `0讨论`: recommend a new topic only for
an independently nameable goal with its own acceptance outcome that materially
interrupts this framing and can be removed without making this topic incomplete.
The source topic presents the complete fixed `新话题建议` and retains both
preparation and task-creation confirmations. A dedicated grilling carrier may
return only a bounded split proposal to its Phase Source Task; it must never
prepare, release, or change Topic Dependencies. On a later gated turn,
evaluate and, after confirmation, release all current closed dependencies
before substantive Phase-1 work. Do not add polling or a Phase-2 dependency
check.

## Enter the stage

- Start from an explicit `$problem-framing` invocation, a clear natural-language
  request to begin `1拷问`, or an unambiguous confirmation of the immediately
  preceding single proposal to begin it.
- Accept `$problem-framing 重试` or an unambiguous natural retry request only
  after this Skill's immediately preceding stable recovery or
  pending-planning-commit footer.
- Accept `$problem-framing 接收拷问交付` only as the first line of a dedicated
  delivery payload defined in
  [references/dedicated-grilling-protocol.md](references/dedicated-grilling-protocol.md).
- Treat replies to this Skill's immediately preceding confirmation or workspace
  decision block as continuations of that exact block. They never authorize an
  unlisted action or another stage.
- A bare affirmation with no immediately preceding pending action remains
  ordinary conversation and invokes nothing.
- Accept ordinary natural language. Never require a parameter block or require
  the user to restate information already available in the task.
- If neither the invocation nor the available context identifies the target,
  ask one concise question.

## Use contextual confirmation

For every action that requires confirmation, use the fixed block in
[references/templates.md](references/templates.md) and interpret the reply
through the shared
[`confirmation-contract.md`](../design-discussion/references/confirmation-contract.md).
Bind authority to the disclosed pending action and normalized intent, never to
a magic reply string.

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
question first, then create the requirement document before the first
substantive question. Populate the migration offer from currently available task,
project, and repository context. For a value that is not yet available, state
`未解析（创建确认前解析）` instead of guessing; freeze every exact identity before
the later task-creation confirmation.

- If the context is still coherent and focused, continue in the current task
  with the standalone document already created for this target.
- If it is long or mixed, show the fixed migration offer from
  [references/templates.md](references/templates.md).
- If the user declines, continue in the current task with the same document.
- If the user clearly confirms the pending migration action, read
  [references/dedicated-grilling-protocol.md](references/dedicated-grilling-protocol.md)
  completely and follow its source-task protocol.

## Load repository rules only when needed

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

If a material behavior question cannot be resolved from safe evidence, stage 1
cannot complete until evidence is available.

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

Continuously improve the one requirement document throughout the entire
questioning process. After every material answer, clarification, confirmation,
or conclusion, update all affected sections before asking the next material
question. The goal is the best attainable current-state document, not a
verbatim transcript or decision archive. Keep these document authorities distinct:

- the draft is the complete target-specific narrative and cross-task handoff;
- `CONTEXT.md` is the canonical domain terminology source;
- ADRs own hard-to-reverse architectural decisions; and
- stage-2 Specs and Tickets are the formal planning artifacts.

Summarize and link native documents from the draft instead of copying them
wholesale. Later confirmed corrections rewrite the current content and remove
superseded downstream detail. Retain at most one concise direction-change note
when it prevents likely misunderstanding; otherwise remove obsolete history.

## Commit and recover through the routed reference

After shared understanding, follow the exact stage-owned commit, workspace and
retry rules in `references/repository-and-recovery.md`. The explicit invocation
authorizes only that bounded local documentation commit; it never widens into
implementation or destructive cleanup.

## Choose the next stage in the current task

When evidence is sufficient, choose `$solution-design` and resolve and disclose the exact configured
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
需求草案：<absolute path>
规划提交：<committed draft sha>
草案 SHA-256：<committed draft hash>
工作区状态：实现区与暂存区干净；本阶段文档已提交；其它文档改动未纳入
规划载体：<planning target for 2方案>
Matt 原生动作：publish Spec; review and publish Tickets when needed; apply native labels and blocking links
阶段授权：entering 2方案 authorizes only those standard actions on that exact target
下一阶段：`$solution-design`（2方案）
进入条件：已满足
交接来源：上述已提交并冻结的需求草案
确认事项：进入 2方案
确认方式：明确同意上述单一待执行事项；如需调整可直接说明
流程模式：可明确要求逐阶段确认，或在无需用户决策时连续执行剩余阶段
```

Interpret a reply to this successful footer through the shared confirmation
contract. A simple unambiguous affirmation enters Phase 2 in stepwise mode. A
clear request to continue through the remaining stages enters it with
`流程模式：连续执行后续全部流程`.

Continuous mode originates only at this final successful footer. It has no role in
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
footer. An unambiguous affirmation archives the dedicated grilling task and
enters `$solution-design` in stepwise mode. A clear request for continuous
remaining execution archives it and enters `$solution-design` with continuous
mode. A dedicated-task delivery always routes to Phase 2.
