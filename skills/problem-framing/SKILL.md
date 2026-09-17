---
name: problem-framing
description: Use when the user explicitly invokes $problem-framing, clearly asks to begin 1拷问, or unambiguously accepts the immediately preceding proposal to begin it; also use for this Skill's retry, confirmation, workspace-decision, or verified dedicated-delivery continuations. Always create or inherit exactly one requirement document, keep it current throughout questioning, and commit and freeze it before handing it to $solution-design.
---

Before any stage action, read and execute [Package execution preflight](references/shared/package-execution.md). This entry is stage 1 (`problem-framing`). Pin this package first; check the current action before its side effects.


# 1拷问

Turn the user's target into a shared, implementation-relevant understanding.
Use Matt Pocock's discussion Skills for the questioning itself. Do not choose
the engineering implementation or write implementation code in this stage.
This restriction never permits deferring an unresolved behavior contract.

Read [Workflow Control Protocol](references/shared/guided-implementation/workflow-control-protocol.md) before role preparation or transition. This stage uses Dedicated Problem Framing Task and preserves the Workflow Controller.

## Foreground runner boundary

The foreground Stage 2→3→4 runner at
`the foreground runner in $guided-implementation` never supplies Phase-1
authority. Its saved state, stage artifacts, and a resumed user answer may
continue only the already frozen requirement named by that runner; they do not
authorize a new requirement draft, a change to the frozen requirement, or a
new user decision in this Skill.

From this Skill's verified successful continuous-flow handoff, the originating
conversation may start that runner once without another confirmation after it
resolves the frozen requirement identity, target/worktree authority, external
run-record path, and each stage's explicit model and reasoning effort. It must
pass those facts as the runner's confirmed input and let the runner launch
Stage 2; it does not create a new discussion, project, or requirement ledger.

Read the shared
[`references/shared/design-discussion/requirement-document-contract.md`](references/shared/design-discussion/requirement-document-contract.md)
before resolving or updating the requirement document. Phase 1 always has
exactly one such document and always hands it to Phase 2.

For a split proposal or a requirements gate, also read the shared
[`references/shared/design-discussion/split-gate-contract.md`](references/shared/design-discussion/split-gate-contract.md).
Keep the Phase-1-specific direction below when applying it.

## Attach a verified discussion source

At entry, apply the discovery order in
[`references/shared/design-discussion/lifecycle-integration.md`](references/shared/design-discussion/lifecycle-integration.md).
Only attach when authenticated phase or handoff evidence, the active binding,
the exact topic document or stable footer, and read-only `discover-context`
resolve one `ledger` or authorized `document_only` context. On `none`, make no
discussion-protocol write and create one standalone Phase-1 requirement
document before the first substantive question. Use the draft schema in
[references/problem-framing/templates.md](references/problem-framing/templates.md), resolve an unused path from
the repository convention or `docs/problem-framing/<yyyy-mm-dd>-<short-target>.md`,
and record the current task as its sole write owner. On `ambiguous`, stop before a
write rather than risk creating a second requirement authority. Stop on a strong
identity conflict; do not ask the user to choose an internal identity.

In an attached context, 0 and 1 share the existing
`docs/discussions/<root-slug>/topic.md`. Select the entry from the verified
`current_phase`, then follow
[the dedicated entry sequence](references/problem-framing/dedicated-grilling-protocol.md#source-task-prepare-and-create):

- From phase 0, use one `0->1` wrapper Phase Run, with
  `dedicated-grilling` for a dedicated task or `current-problem-framing` for
  the current task. Publish the latest stage-entry checkpoint first. A dedicated
  task claims its authorized `PA-*`, reports ready and waits for source activation.
  Execution is Stage 1 while `current_phase` remains 0 until finalization.
- At phase 1, a dedicated transfer uses `dedicated-stage(stage=1)`.
  Its verified `accept-handoff` grants immediate requirement work when the
  gate is open; no wrapper or later-turn authorization is needed.
- Standalone entry keeps its authenticated task identity and sole draft
  `write_owner`, with no discussion ledger operations.

Attached carriers write only the existing topic document through `DW-*` after
activation or acceptance. They create neither a second draft nor standalone
metadata in the topic document. The controller keeps coordination and intake;
while a carrier holds requirement write authority, the controller cannot also
prepare or apply requirement writes. Use the lifecycle reference for frozen
input, working/output evidence and completion order.

Before routing from 0 or 1, resolve every pending impact and publish the latest
effective 0/1 checkpoint. Use `prepare-wrapper-phase-run`, not the generic
Phase Run operation. Phase 1 routes only to Phase 2. A continuous request from 0 or 1 freezes the current checkpoint, source_phase and exact stages [2,3,4] scope.

Apply the shared split/gate contract above in Phase 1. A dedicated grilling carrier
may return only a bounded split proposal to its Phase Source Task; it
must never prepare, release, or change Topic Dependencies. The source topic
retains both confirmations and resumes Phase-1 questioning only after the
shared gate sequence permits substantive work.

## Enter the stage

- Start from an explicit `$problem-framing` invocation, a clear natural-language
  request to begin `1拷问`, or an unambiguous confirmation of the immediately
  preceding single proposal to begin it.
- Accept `$problem-framing 重试` or an unambiguous natural retry request only
  after this Skill's immediately preceding stable recovery or
  pending-planning-commit footer.
- Accept `$problem-framing 接收拷问交付` only as the first line of a dedicated
  delivery payload defined in
  [references/problem-framing/dedicated-grilling-protocol.md](references/problem-framing/dedicated-grilling-protocol.md).
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
[references/problem-framing/templates.md](references/problem-framing/templates.md) and interpret the reply
through the shared
[`references/shared/design-discussion/confirmation-contract.md`](references/shared/design-discussion/confirmation-contract.md).
Bind authority to the disclosed pending action and normalized intent, never to
a magic reply string.

## Choose the context path

The Workflow Controller first resolves the one requirement document and the exact entry authority, then prepares its dedicated-task plan. Disclose missing context, identity and configuration in one combined stage-entry/task-creation confirmation. There is no preliminary migration offer. Stage-current or topic-current refusal reuses this document and suppresses repeated offers. A dedicated carrier never recursively creates another. Read [references/problem-framing/dedicated-grilling-protocol.md](references/problem-framing/dedicated-grilling-protocol.md) before preparing an attached authority, binding or intake.

## Load repository rules only when needed

Before repository inspection, the first documentation write or commit, any
workspace decision, or a mechanical retry, read
[references/problem-framing/repository-and-recovery.md](references/problem-framing/repository-and-recovery.md).
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
retry rules in `references/problem-framing/repository-and-recovery.md`. The explicit invocation
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
进入条件：<已满足 | 后继不可用：具体依赖错误；当前阶段已完成>
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

After receive/accept, finalize an attached 0→1 wrapper and verify `current_phase: 1` before showing the dedicated success footer. A same-stage transfer has no wrapper to finalize. A stepwise or continuous
confirmation enters solution-design using the accepted result. Keep the old carrier
available until successor-ready verifies input/binding and applicable source
activation; then archive the frozen old task. Archive failure recovers archive only.
A dedicated-task delivery always routes to Phase 2.

After accepted delivery, successor-ready proves takeover before archive.
