# Spec: Require solution readiness inside Stage 2

Status: `ready-for-agent`

## Problem Statement

`2方案` delegates native Spec and Ticket production to one trusted
`solution_designer`, but its continuous mode currently treats prior workflow
authorization as a reason to skip both human review and additional quality
checks. A structurally complete `SOLUTION_DESIGN_COMPLETE` can therefore enter
`3实现` even when the Spec names the requested behavior but does not make its
module ownership, Interface, decision precedence, state transitions, or
highest-seam tests explicit.

This is most visible in cross-cutting changes. The requirements may be present,
yet Stage 3 must rediscover which module owns a rule and how combinations of
errors or terminal states behave. Review then finds several manifestations of
the same missing design decision across successive implementation candidates.

The repair belongs entirely to `2方案`. It must not alter Matt's native
`$to-spec`, `$ask-matt`, or `$to-tickets` Skills; add another Agent or Workflow
Stage; change user confirmation behavior; or move readiness work into Stages
1, 3, or 4.

## Solution

Deepen the existing `solution_designer` role so solution readiness is part of
its completion criteria. The same child reads one Stage-2 reference, uses the
native `$to-spec` and `$to-tickets` draft/review seams, and verifies applicable
readiness obligations before it asks for an existing stepwise confirmation or
publishes automatically in continuous mode.

Readiness has two checkpoints:

1. **Spec readiness**, before `SOLUTION_REVIEW_REQUIRED` or native Spec
   publication, verifies that the proposed solution gives cross-cutting
   behavior one authoritative module and a small Interface, resolves any
   order-sensitive decisions and durable state transitions, and chooses tests
   through the highest practical seam.
2. **Ticket traceability**, before `TICKETS_REVIEW_REQUIRED` or native Ticket
   publication, verifies that each implementation slice preserves the Spec's
   ownership and testing decisions rather than rediscovering or duplicating
   them. When native `$ask-matt` concludes that Tickets are unnecessary, this
   checkpoint is explicitly not applicable.

Both flow modes perform the same readiness work. Stepwise mode keeps its
existing launch, solution, Ticket, and Stage-3 confirmations. Continuous mode
still emits no review request and waits for no user response; it skips human
confirmation, not Stage-2 completion criteria.

An incomplete draft is ordinary Stage-2 work: the same child revises it before
publication. The existing anomaly path is used only when readiness cannot be
satisfied without changing the accepted requirement source, expanding scope,
changing permissions, or revising an already accepted or published plan.

## User Stories

1. As a workflow user, I want continuous mode to proceed without new prompts,
   so that selecting the full flow still runs unattended when no decision is
   needed.
2. As an implementer, I want each cross-cutting rule to name one authoritative
   module and Interface, so that callers do not independently reinterpret it.
3. As an implementer, I want order-sensitive errors and durable state outcomes
   resolved in the Spec, so that Stage 3 does not invent behavior while coding.
4. As a test author, I want Testing Decisions to use the highest practical
   seam and cover applicable decision rows, so that green tests exercise the
   same Interface as production callers.
5. As a Ticket consumer, I want each slice to trace back to the Spec's module
   and test decisions, so that Tickets do not fragment one rule across several
   implementations.
6. As a maintainer, I want simple changes to record concise not-applicable
   reasons instead of producing ceremonial matrices, so that readiness does
   not become planning overhead.
7. As a Stage-2 orchestrator, I want completion to carry readiness evidence, so
   that I can mechanically distinguish a finished design from a merely
   published document without redoing the design.

## Implementation Decisions

### 1. Keep one Stage-2 owner and the existing native Skills

- Keep exactly one context-isolated `solution_designer`; do not add a design
  reviewer or another dispatch.
- Continue to invoke the selected project's complete native `$to-spec`,
  `$ask-matt`, and `$to-tickets` behavior. Do not fork or patch those Skills.
- Keep the requirement source, document authority, Flow Worktree, publication,
  anomaly, and Stage Handoff contracts unchanged.
- Treat solution readiness as work performed by the existing Stage-2 child,
  not as a new artifact, Workflow Stage, user decision, or remote action.

### 2. Add one progressively disclosed readiness reference

Add `skills/solution-design/references/design-readiness.md`. The child reads it
before starting native `$to-spec`; the primary orchestrator does not need its
full contents during launch.

The reference defines five checks. Each meaning has one authoritative home in
that file; `SKILL.md`, the protocol, and templates point to the check instead
of copying its rules.

1. **Module ownership** — behavior spanning more than one module or entrypoint
   names one authoritative module. Other modules' responsibilities are limited
   to adapting inputs, invoking the Interface, or consuming its result.
2. **Interface and seam** — the Spec states what callers must know, keeps
   internal test seams private, prefers an existing high seam, and uses
   production and test Adapters at the same real seam when an external
   dependency varies.
3. **Decision coverage** — when several signals can classify one outcome or
   ordering changes behavior, the Spec gives an unambiguous precedence table
   or equivalent compact representation.
4. **State coverage** — when the change mutates durable state across more than
   one terminal outcome, the Spec maps applicable entrypoints and outcomes to
   their state transitions and atomicity requirements.
5. **Testing and Ticket traceability** — Testing Decisions exercise observable
   behavior through the chosen Interface; when Tickets exist, their acceptance
   criteria cover the applicable ownership, decision, state, and test
   obligations without redefining them.

The reference requires the smallest representation that is unambiguous. A
simple bullet is sufficient for a one-path decision. A table is required only
when multiple dimensions or precedence interact.

### 3. Make applicability evidence-based

Every check is either satisfied by an exact Spec section or marked
`not applicable` with a concrete reason. Blanket `not applicable` is invalid.

- Module ownership may be not applicable when the change stays within one
  existing module and introduces no rule shared by another entrypoint.
- Decision coverage may be not applicable when there are no competing signals,
  ordered fallbacks, retries, permissions, or error categories.
- State coverage may be not applicable when the change has no durable mutable
  state or only one unchanged terminal transition.
- Ticket traceability is not applicable only when native `$ask-matt` concludes
  that the complete Spec fits one implementation context without Tickets.
- Interface and Testing Decisions always remain applicable because native
  `$to-spec` already requires a testing seam.

Readiness does not require new top-level sections in Matt's Spec template.
Evidence belongs in the existing `Implementation Decisions` and
`Testing Decisions` sections, using module names rather than volatile file
paths.

### 4. Place checks before existing review and publication seams

Update `skills/solution-design/references/subagent-protocol.md` so the child
performs Spec readiness after it has a complete native `$to-spec` draft and
before either `SOLUTION_REVIEW_REQUIRED` or publication.

- A failed check before review/publication causes the same child to revise the
  draft and rerun only the failed readiness checks.
- In stepwise mode, the existing solution review is shown only after readiness
  passes. The user confirms the same proposal as today; no extra confirmation
  is introduced.
- In continuous mode, readiness passes internally and publication proceeds in
  the same run without a user-visible review message.
- A user-requested edit invalidates the prior readiness evidence together with
  the existing review ID and requires readiness to run again on the revised
  draft.

After native `$ask-matt`, perform Ticket traceability at the native Ticket
draft/quiz seam and before review or publication. A Ticket-only defect is
revised normally. If the check exposes a missing or contradictory decision in
an already accepted or published Spec, use the existing anomaly path rather
than silently rewriting the plan.

### 5. Clarify continuous mode without changing user interaction

Update `skills/solution-design/SKILL.md` and the child bootstrap wording in
`references/templates.md`:

- Continuous mode skips `SOLUTION_REVIEW_REQUIRED`,
  `TICKETS_REVIEW_REQUIRED`, and the final Stage-3 confirmation.
- Continuous mode still performs native synthesis, readiness, publication,
  local planning commit, and existing mechanical worktree checks.
- Readiness revision inside the accepted requirement boundary is ordinary
  Stage-2 work, not an anomaly and not a reason to contact the user.
- A material choice, changed requirement, scope expansion, permission change,
  or published-plan revision keeps using the existing anomaly mechanism.

Remove only the current statements that continuous mode skips additional
quality gates or forbids completeness/testing checks. Preserve every existing
authority and transition restriction.

### 6. Carry compact readiness evidence at completion

Extend `SOLUTION_DESIGN_COMPLETE`, the stepwise success footer, and the
continuous Stage Handoff with these fields:

```text
方案就绪检查：通过
模块与 Interface：<Spec section | not applicable with reason>
决策覆盖：<Spec section | not applicable with reason>
状态覆盖：<Spec section | not applicable with reason>
测试 seam：<Spec Testing Decisions section>
Tickets 追踪：<Ticket evidence | not applicable after native decision>
```

The primary still trusts the exact child returned by `spawn_agent`. Completion
intake mechanically requires these non-empty fields and preserves the existing
rule against independently redoing semantic design review. Readiness evidence
changes the completion schema; it does not make the primary a second designer.

Use the existing `solution-design-subagent-v1` protocol name. This is a stricter
completion contract within the same Stage and does not create a second runtime
protocol or migration path.

### 7. Limit implementation paths

The implementation may change only:

- `skills/solution-design/SKILL.md`
- `skills/solution-design/references/subagent-protocol.md`
- `skills/solution-design/references/templates.md`
- new `skills/solution-design/references/design-readiness.md`
- one focused Stage-2 contract test under
  `skills/solution-design/scripts/`

Do not edit generated project Skill links under `.agents/`, Matt Skill sources,
other Workflow Stage Skills, `CONTEXT.md`, ADRs, repository validation logic,
or unrelated planning artifacts.

## Testing Decisions

### Primary seam: Stage-2 instruction contract

Add one focused Python contract test discovered by the existing repository
validator. It reads the four Stage-2 instruction files as published agent
inputs and asserts observable protocol obligations rather than exact paragraphs:

- both flow modes require Spec readiness before review/publication;
- Ticket traceability occurs before Ticket review/publication when Tickets
  exist;
- continuous mode explicitly skips user review messages while retaining
  readiness;
- stepwise mode retains its existing four confirmation points and adds none;
- completion and both success handoffs carry the six readiness evidence
  fields;
- the readiness reference is reachable from the child execution path;
- no instruction assigns readiness to Stage 1, 3, or 4 or invokes a second
  design Agent.

Avoid asserting full prose or line order. Test stable markers and required
relationships so wording improvements do not cause unrelated failures.

### Repository validation

Run `./scripts/validate.sh`. It must pass Skill validation, the new focused
contract test, all existing discovered tests, dependency registration, and
reference reachability.

No live Stage 1→4 exercise, remote publication, real tracker write, or new E2E
harness is required for this instruction-only change.

## Acceptance Criteria

- Continuous mode requests no additional user confirmation and still proceeds
  automatically after readiness passes.
- Stepwise launch, solution review, Ticket review, and Stage-3 confirmation
  remain unchanged.
- A complex Spec cannot reach review/publication without applicable ownership,
  Interface, decision, state, and highest-seam testing evidence.
- A simple Spec may use specific not-applicable reasons without ceremonial
  matrices or new documents.
- Ticket drafts cannot reach review/publication without tracing applicable
  Spec decisions, unless native `$ask-matt` concluded Tickets are unnecessary.
- Draft defects inside the accepted requirement boundary are revised by the
  same child without an anomaly or user message.
- Material requirement, scope, permission, or accepted/published-plan changes
  still stop through the existing anomaly contract.
- Completion and Stage Handoff include compact readiness evidence.
- Matt native Skills, all other Workflow Stages, shared terminology, ADRs,
  worktree behavior, publication authority, and remote permissions are
  unchanged.
- `./scripts/validate.sh` passes.

## Out of Scope

- Modifying or forking `$to-spec`, `$ask-matt`, `$to-tickets`, or their
  templates.
- Adding an independent design reviewer, another subagent, a new Workflow
  Stage, a new user confirmation, or a new persistent readiness artifact.
- Changing `$problem-framing`, `$guided-implementation`, `$code-review`,
  `$change-closure`, discussion lifecycle behavior, or governance tooling.
- Re-ranking implementation review findings or changing Stage-3 remediation.
- Requiring decision tables, state matrices, ADRs, or Tickets when their
  evidence-based applicability conditions are not met.
- Publishing remotely, creating a PR, deploying, releasing, or implementing
  the planned Skill changes during this planning task.

## Further Notes

- No ADR is needed. The change strengthens the existing Stage-2 owner's
  completion criteria without moving authority, adding a role, or changing the
  Flow Worktree lifecycle.
- No separate Tickets are created in this planning task. The bounded change is
  expected to fit one implementation context; if implementation later proves
  otherwise, native `$ask-matt` can make that decision without altering this
  Spec.
- `codebase-design` influenced the ownership, Interface, seam, and locality
  criteria. `writing-for-agents` influenced the single-source readiness
  reference, progressive disclosure, and checkable completion fields.
