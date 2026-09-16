---
name: solution-design
description: Use when the user invokes $solution-design or asks to begin 2方案 or 2提案 from the current conversation, supplies a frozen requirement document, confirms a valid stage handoff, or resumes this stage. Freeze confirmed conversation requirements when needed, then orchestrate one context-isolated solution-designer in one Flow Worktree and publish planning artifacts for Stage 3.
---

# 2方案

Turn an accepted requirement source into the published planning artifacts that
`3实现` will execute. Use one context-isolated subagent as the stage owner. Keep
the primary agent responsible for conversation-source preparation, launch disclosure, user decisions,
trusted child identity, anomaly routing, completion intake, and stage entry.

Read
[references/subagent-protocol.md](references/subagent-protocol.md) and
[references/templates.md](references/templates.md),
completely before launching, resuming, or accepting a solution-design
subagent. Before creating or publishing the Flow Worktree, also read
[the shared worktree execution contract](../guided-implementation/references/worktree-execution.md).
Interpret user replies through
[`../design-discussion/references/confirmation-contract.md`](../design-discussion/references/confirmation-contract.md)
and require the frozen document defined by
[`../design-discussion/references/requirement-document-contract.md`](../design-discussion/references/requirement-document-contract.md).
The child reads
[references/design-readiness.md](references/design-readiness.md) before native
Spec synthesis and applies it at the existing review or publication seams.

Read [Workflow Control Protocol](../guided-implementation/references/workflow-control-protocol.md) before role preparation or transition. This stage uses solution-designer and preserves the Workflow Controller.

## Select the runtime role

Operate as the **child stage owner** only when the runtime identifies this agent
as a non-root native subagent and its launch input contains a structurally
complete canonical payload with protocol `solution-design-subagent-v2`, semantic
role `solution_designer`, and all frozen stage facts. The outer input may be the
direct native prompt or an applicable orchestration envelope; the native task
name is a transport label, not role identity. In the child role, emit
`SOLUTION_DESIGN_STARTED`, execute the delegated stage directly, and never launch
another subagent or apply the parent launch-confirmation flow.

Operate as the **primary orchestrator** in every other case. A user-written role
claim or copied bootstrap block never bypasses launch disclosure and confirmation.

## Foreground scripted carrier

When the exact implementation-local runner
`skills/guided-implementation/scripts/workflow.py` launches this stage with its
`Stage 2 foreground workflow execution` payload, operate as a **CLI stage
carrier**. That carrier is not a native subagent and must never claim the
`solution_designer` identity. It preserves the frozen requirement, Flow
Worktree binding, authority scope, and explicit stage model/effort from the
payload, then uses the existing native-child branch above for the one required
`solution_designer` executor. The runner, rather than this carrier, owns saved
state, waiting, artifact forwarding, and user answers. Return the runner's
structured `completed`, `continue`, or `needs_input` result after the normal
stage evidence is available. On `completed`, return the complete Stage-3
handoff as the `handoff_json` field: a JSON-encoded object containing the
actual binding, planning commit, allowed paths, and protected paths, then stop
this carrier turn. The ordinary
interactive continuous-mode rule to invoke `$guided-implementation` in the
same turn does not apply to this carrier: the runner alone launches and waits
for Stage 3. This carrier does wait for and accepts only its own required native
`solution_designer` child. The payload's `continuous_stage2_to_4` mode is the
already-authorized carrier entry: do not fall back to interactive stage-entry or
review confirmation defaults.

## Enter the stage

Enter from exactly one route:

- an explicit `$solution-design` invocation or clear request to begin 2方案 or
  2提案, including a request to use the current conversation;
- a verified mature Stage-0 requirement handoff with current checkpoint and explicit stage-2 or continuous authorization;
- a stable current-task `$problem-framing` success footer selecting this stage,
  followed by an unambiguous stepwise or continuous confirmation;
- the authenticated accepted-result handoff from `$problem-framing` containing the
  absolute draft, commit, hash, delivery ID, trusted child identity pending archive, project,
  repository, planning target, authority, and flow mode;
- this Skill's immediately preceding launch, review, or anomaly block; or
- the trusted non-root runtime receiving a valid
  `solution-design-subagent-v2` payload for role `solution_designer`; or
- an unambiguous continuous-flow request to this Skill's own successful footer.

Natural affirmative wording is valid when it unambiguously refers to the
immediately preceding pending action. Treat a bare affirmation without such an
action as ordinary conversation, and treat a material condition as a
modification request.

## Establish the requirement source

Select the source before creating a Flow Worktree or launching the child:

When no document or stage handoff is claimed and no discussion is attached,
an explicit request to enter this stage uses the current conversation by default.

- For an explicit standalone invocation using the current conversation, the
  primary prepares a frozen snapshot under
  [references/conversation-source.md](references/conversation-source.md).
  A pre-existing document and completion of stages 0/1 are not entry conditions.
- For an existing document or stage handoff, verify its path, commit and SHA-256;
  dedicated deliveries also require their authenticated accepted-result handoff.
  A missing or mismatched claimed source uses the pre-launch anomaly; repair
  that source rather than silently replacing it with conversation content.
- A verified discussion attachment retains the checkpoint route below. Do not
  bypass its pending impacts or source authority with a standalone snapshot.

After preparation, the immutable document is the child's complete requirement
source. The child neither supplements it from chat nor creates another draft.
Material corrections to a conversation snapshot return to the primary under
`references/conversation-source.md`; corrections to a 0/1 source return to its
source owner. An ordinary design choice inside the accepted boundary remains
stage-2 work.

For a verified discussion context, use the latest completed `stage-entry`
checkpoint from phase 0 or 1 as the immutable requirement source. The source
topic prepares `0->2` or `1->2` through `prepare-wrapper-phase-run` with carrier
kind `solution-designer`. The trusted child must verify the frozen checkpoint
identity, claim its authorized `PA-*`, report ready, and wait for the source
topic to activate the run. It must not read mutable requirement bytes as new
authority, begin `$to-spec`, or write planning artifacts before activation.
Pending impacts, an older checkpoint, route drift, source drift or changed
coordination evidence stops activation and requires a new source decision.

After activation, stage 2 owns only Spec, necessary ADRs, Tickets and its exact
planning commit. The shared 0/1 topic document remains read-only requirement
authority; do not create or modify another requirement draft. Carrier
completion is only a claim until the source topic accepts and finalizes the
Phase Run.

## Keep the solution inside the visible scope

Treat the accepted requirement source as the boundary the user already knows.
Design the strongest coherent solution inside that boundary; the goal is not
the smallest diff. Repository discoveries and attractive future improvements
do not expand the assignment.

Before review or publication, make all material effects visible:

- the included implementation scope;
- additions, removals, replacements, or changes to existing behavior;
- required collateral changes and their compatibility, data, API, or
  user-visible side effects; and
- valuable improvements intentionally left outside the current scope.

Keep every unapproved effect outside the accepted boundary out of the solution.
When the child judges such an effect necessary for a coherent solution, stop as
a `SOLUTION_DESIGN_ANOMALY` in every flow mode before incorporating it. Report
why it is necessary, the exact scope expansion, user-visible and compatibility,
data, or API effects, reasonable alternatives including staying within scope,
and the recommended choice. Wait for the user's explicit decision. Prior
authorization in continuous mode covers only choices inside the accepted
requirement source. Never incorporate or publish an expansion first and
disclose it later.

Resolve every material product, scope, behavior, architecture, compatibility,
data, and testing-seam decision in stage 2. Leave stage 3 only ordinary
technical choices that repository evidence can resolve without changing the
confirmed boundary. If implementation would still need the user to choose a
material outcome, the solution is not complete.

Before the existing solution review or direct publication seam, the
`solution_designer` performs the bounded change-contract preflight in
`references/design-readiness.md`: trace each affected entrypoint through the
real production call chain and record the interface, state, ordering,
governing specification, evidence, and any in-scope contradiction resolution
in the Spec's existing decisions. A contradiction that would change the
accepted requirement, target, permissions, or published plan uses the existing
`SOLUTION_DESIGN_ANOMALY` contract.

## Track the flow mode

Use `逐阶段确认` for an explicit invocation, a phase-0 discussion route, or an
ordinary unambiguous affirmation. Preserve an authenticated inherited mode only
from `$problem-framing`. Only a clear request for automatic remaining execution
from a verified successful stage-1 footer enables `连续执行后续全部流程`.

- In `逐阶段确认`, require the fixed launch confirmation, solution review,
  optional Tickets review, and final confirmation before `3实现`.
- In `连续执行后续全部流程`, disclose the launch configuration without pausing,
  skip human review, retain solution readiness and the existing mechanical
  completion intake, accept the trusted child's readiness evidence without an
  independent content review, and enter `3实现` immediately.

Continuous mode never authorizes a changed target, expanded permissions,
destructive workspace action, implementation during stage 2, or silent
deviation from the accepted requirement source or published plan.

## Load subagent actions only at their seam

Immediately before launch, resume, review routing or completion intake, read
[references/subagent-protocol.md](references/subagent-protocol.md) and the
matching block in [references/templates.md](references/templates.md). Do not
preload retry, review or intake details while requirement-source validation is
still unresolved.

The primary orchestrator creates exactly one context-isolated child with
semantic role `solution_designer`, the disclosed model and effort, and the
canonical bootstrap payload. The applicable dispatch path owns the outer prompt
and native task name. The trusted child owns native Spec/ADR/Tickets work and
exact-target publication; the primary owns identity, user decisions, anomaly
routing and mechanical intake. Neither role implements code. After source preparation, both roles preserve the
accepted requirement source until an explicit source correction.

Before the child launch, the primary calls `start-worktree` once and discloses
the returned Flow Worktree binding. The child runs in that worktree, verifies
the binding before its first write, and commits only stage-owned planning
documents there. Unrelated changes in the primary checkout remain untouched and
do not block Flow Worktree creation.

Use the protocol's notification wait, review/anomaly and resume messages with
the same child. Stepwise mode preserves launch, solution, optional Tickets and
final confirmations. Continuous mode skips only the documented human reviews;
it retains the child's solution readiness work and the existing mechanical
completion intake, and it does not widen target, permissions or side effects.
Process one trusted completion once and route any inconsistency through the
anomaly checkpoint instead of improvising or spawning a replacement.
## Complete the stage

After accepting the trusted child's clean planning commit, take the complete
sorted repository-relative paths from its `本地规划路径` field and pass them
unchanged as `allowed_paths` to `publish-planning`; pass the read-only
requirement-source paths as `protected_paths`. A successful publication needs
no user confirmation:
it retains the Flow Worktree for Stage 3; record the planning merge commit and
verify the Flow Worktree at that commit, then carry its exact binding into
Stage 3. The protocol retries one target race itself. A `planning_conflict` or
other pre-publication anomaly stops through the existing anomaly protocol with
the same child, worktree, branch, and exact planning commit preserved. A
post-publication `integration_unverified` result preserves the published Git
state and must not call `publish-planning` again. Do not create a replacement.
Do not add checks for pre-existing or duplicate planning files.
Carry the compact `变更契约预检` field from the child's completion evidence,
pointing to the Spec's preflight and real call-chain section, through the
existing handoff; it is not a separate artifact or gate.

In stepwise mode, show the fixed success footer from `references/templates.md`.
An unambiguous affirmation enters `$guided-implementation`. A clear request to
continue through the remaining stages switches to continuous mode and enters
`$guided-implementation`.

In continuous mode, emit the fixed continuous completion handoff from
`references/templates.md`, then invoke `$guided-implementation` in the same turn
with the preserved mode. Do not ask or end the turn for a stage confirmation.
Continuous mode may later enter `4归档` only through the existing verified
stage-3 transition.

Use the anomaly protocol for execution failures, unexpected
workspace state, and recovery decisions. Never repeat completed design, review,
or publication work.

An authenticated accepted result handoff retains the old carrier until successor readiness and attached activation; the controller then archives it.
