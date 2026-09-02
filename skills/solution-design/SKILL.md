---
name: solution-design
description: Use when the user explicitly invokes $solution-design; confirms a stable current-task $problem-framing handoff; $problem-framing explicitly enters this stage after accepting and archiving a dedicated grilling task; replies to this Skill's launch, review, or anomaly block; or selects 执行后续全部流程 from a verified success footer. Create one Flow Worktree, orchestrate one context-isolated solution-designer subagent there, publish its committed planning files to the target, retain the same worktree for stages 3 and 4, and preserve exact confirmation and anomaly boundaries.
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
  followed after trimming whitespace by exact `确认` or
  `执行后续全部流程`;
- the authenticated post-archive handoff from `$problem-framing` containing the
  absolute draft, commit, hash, delivery ID, archived child identity, project,
  repository, planning target, authority, and flow mode;
- this Skill's immediately preceding launch, review, or anomaly block; or
- the trusted `solution_designer` runtime receiving the exact
  `solution-design-subagent-v1` bootstrap; or
- exact `执行后续全部流程` to this Skill's own successful footer.

Reject punctuation, prefixes, suffixes, added conditions, `继续`, and `可以` as
stage confirmation. Treat an unrelated approval as ordinary conversation.

## Establish the requirement source

Use one source:

- **Delivered draft:** mechanically require the authenticated post-archive
  handoff and read the immutable problem-framing draft as the complete
  requirement source. Do not re-audit the grilling or modify the draft.
- **Current-task handoff:** extract the already accepted target, background,
  scope, constraints, decisions, acceptance conditions, and planning target
  into the child bootstrap prompt. Do not create a second requirement or
  solution draft merely for delegation.

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

An effect outside the accepted boundary must be proposed before it enters the
solution. In stepwise mode, obtain the user's explicit decision at the solution
review or anomaly checkpoint. In continuous mode, prior authorization covers
only choices inside the accepted requirement source; a scope expansion still
stops as an anomaly. Never publish the expansion first and disclose it later.

Resolve every material product, scope, behavior, architecture, compatibility,
data, and testing-seam decision in stage 2. Leave stage 3 only ordinary
technical choices that repository evidence can resolve without changing the
confirmed boundary. If implementation would still need the user to choose a
material outcome, the solution is not complete.

## Track the flow mode

Use `逐阶段确认` for an explicit invocation, a phase-0 discussion route or exact
`确认`. Preserve an authenticated inherited mode only from `$problem-framing`.
Only exact `执行后续全部流程` from a verified successful stage-1 footer enables
`连续执行后续全部流程`.

- In `逐阶段确认`, require the fixed launch confirmation, solution review,
  optional Tickets review, and final confirmation before `3实现`.
- In `连续执行后续全部流程`, disclose the launch configuration without pausing,
  skip human review and additional quality gates, accept trusted child
  completion without independent content or mechanical review, and enter
  `3实现` immediately.

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
final confirmations. Continuous mode skips only the documented reviews and
quality gates; it does not widen target, permissions or side effects. Process
one trusted completion once and route any inconsistency through the anomaly
checkpoint instead of improvising or spawning a replacement.
## Complete the stage

After accepting the trusted child's clean planning commit, call
`publish-planning` with the exact stage-owned planning paths and read-only
requirement-source paths. A successful publication needs no user confirmation:
it retains the Flow Worktree for Stage 3; record the planning merge commit and
verify the Flow Worktree at that commit, then carry its exact binding into
Stage 3. The protocol retries one target race itself. A `planning_conflict` or
other publication anomaly stops through the existing anomaly protocol with the
same child, worktree, branch, and exact planning commit preserved. Do not
create a replacement. Do not add checks for pre-existing or duplicate planning
files.

In stepwise mode, show the fixed success footer from `references/templates.md`.
Exact `确认` enters `$guided-implementation`. Exact
`执行后续全部流程` switches to continuous mode and enters
`$guided-implementation`.

In continuous mode, emit the fixed continuous completion handoff from
`references/templates.md`, then invoke `$guided-implementation` in the same turn
with the preserved mode. Do not ask or end the turn for a stage confirmation.
Continuous mode may later enter `4归档` only through the existing verified
stage-3 transition.

Use the anomaly protocol for execution failures, unexpected
workspace state, and recovery decisions. Never repeat completed design, review,
or publication work.
