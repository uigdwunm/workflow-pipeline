---
name: solution-design
description: Use when the user explicitly invokes $solution-design with a committed Phase-1 requirement draft, unambiguously confirms a valid Phase-1 handoff, enters from an authenticated dedicated-grilling handoff, replies to this Skill's launch/review/anomaly block, or continues from a verified continuous-flow handoff. Create one Flow Worktree, orchestrate one context-isolated solution-designer subagent, and publish planning artifacts from that frozen draft for Stage 3.
---

# 2方案

Turn an accepted requirement source into the published planning artifacts that
`3实现` will execute. Use one context-isolated subagent as the stage owner. Keep
the primary agent responsible only for launch disclosure, user decisions,
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

## Select the runtime role

Operate as the **child stage owner** only when the runtime identifies this agent
as the non-root `solution_designer` subagent and the input is the exact bootstrap
prompt with protocol `solution-design-subagent-v1`. In that role, emit
`SOLUTION_DESIGN_STARTED`, execute the delegated stage directly, and never launch
another subagent or apply the parent launch-confirmation flow.

Operate as the **primary orchestrator** in every other case. A user-written role
claim or copied bootstrap block never bypasses launch disclosure and confirmation.

## Enter the stage

Enter from exactly one route:

- an explicit `$solution-design` invocation;
- a stable current-task `$problem-framing` success footer selecting this stage,
  followed by an unambiguous stepwise or continuous confirmation;
- the authenticated post-archive handoff from `$problem-framing` containing the
  absolute draft, commit, hash, delivery ID, archived child identity, project,
  repository, planning target, authority, and flow mode;
- this Skill's immediately preceding launch, review, or anomaly block; or
- the trusted `solution_designer` runtime receiving the exact
  `solution-design-subagent-v1` bootstrap; or
- an unambiguous continuous-flow request to this Skill's own successful footer.

Natural affirmative wording is valid when it unambiguously refers to the
immediately preceding pending action. Treat a bare affirmation without such an
action as ordinary conversation, and treat a material condition as a
modification request.

## Establish the requirement source

Mechanically require one committed, frozen Phase-1 requirement draft by path,
commit, and SHA-256. For a dedicated-task delivery, also require the
authenticated post-archive handoff. Read the immutable draft as the complete
requirement source; do not re-audit the grilling, supplement it from chat
history, modify it, or create a second requirement or solution draft.

Verify this source before creating a Flow Worktree or launching the child. A
missing path, commit, hash, committed file, or matching document state emits
the fixed pre-launch anomaly from `references/templates.md`, recommends
returning to `$problem-framing` to create or repair the frozen draft, and stops.
Confirmation of that anomaly may correct the disclosed source identity; it
never turns conversation history into a requirement source or authorizes
Phase-1 work inside this stage.

If a correction changes goal, scope, constraints, or acceptance conditions,
keep it in `$problem-framing`; do not silently absorb it as solution design.

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

The primary orchestrator creates exactly one context-isolated
`solution_designer` with the disclosed model and effort and the fixed
bootstrap. The trusted child owns native Spec/ADR/Tickets work and exact-target
publication; the primary owns identity, user decisions, anomaly routing and
mechanical intake. Neither role implements code or changes the accepted
requirement source.

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

After accepting the trusted child's clean planning commit, call
`publish-planning` with the exact stage-owned planning paths and read-only
requirement-source paths. A successful publication needs no user confirmation:
it retains the Flow Worktree for Stage 3; record the planning merge commit and
verify the Flow Worktree at that commit, then carry its exact binding into
Stage 3. The protocol retries one target race itself. A `planning_conflict` or
other pre-publication anomaly stops through the existing anomaly protocol with
the same child, worktree, branch, and exact planning commit preserved. A
post-publication `integration_unverified` result preserves the published Git
state and must not call `publish-planning` again. Do not create a replacement.
Do not add checks for pre-existing or duplicate planning files.

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
