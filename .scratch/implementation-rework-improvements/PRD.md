# Spec: Reduce invalid rework in implementation workflows

Status: `ready-for-agent`

## Problem Statement

The workflow already asks Stage 2 to perform solution readiness and Stage 3 to
run TDD, but the instructions leave three gaps that make the same failure
mechanism recur. A readiness check can name an owner and a test seam without
proving that the proposed change matches the existing interface, state,
ordering, specification, and production call chain. The implementation
workflow can then begin with a mocked unit test that bypasses the changed
internal boundary and only discover the mismatch after several slices. When a
review or test failure returns after a fix, the protocol sends the task back to
remediation without requiring evidence about why the first fix was ineffective
or which sibling paths share the mechanism.

These are instruction-contract defects. The repair belongs in the existing
Stage 2 and Stage 3 references and their static contract tests. It must not
change business code, introduce a new Workflow Stage, add a separate review
artifact, impose a fixed remediation-round gate, or require broad end-to-end
coverage for every change.

## Solution

Strengthen the existing actors at their current seams:

1. Stage 2's `solution_designer` performs a bounded change-contract preflight
   before the existing Spec readiness check. It traces each affected entrypoint
   through the real production call chain, compares the proposed interface,
   state transitions, ordering, and failure semantics with repository evidence,
   and records any contradiction and its resolution in the Spec's existing
   decisions. A contradiction that cannot be resolved inside the frozen scope
   uses the existing anomaly contract.
2. Stage 3's Dedicated Implementation Task starts TDD with one representative
   vertical slice. The first behavior test goes through the changed real
   internal boundary and production wiring identified by the Spec. Test
   doubles may replace dependencies beyond that boundary, but may not replace
   the boundary or the caller path being changed. After the slice passes, the
   task expands the same implementation pattern to the remaining in-scope
   paths.
3. On a repeated failure mechanism, same-class regression, or repeated plan
   reversal, the Originating Task routes remediation to the same Dedicated
   Implementation Task with a diagnosis request before another fix. The task
   compares the previous failure and fix, states why the fix was ineffective,
   adds the smallest validation that would fail for the mechanism and pass for
   the repair, and inspects affected sibling paths before continuing. This is
   carried as execution evidence; no standalone diagnostic document or fixed
   number of rounds is added.

Both flow modes retain their current confirmations and publication behavior.
The rules are conditional on a material affected boundary or a repeated
mechanism, so simple changes do not acquire ceremonial matrices or extra
prompts.

## User Stories

1. As a solution designer, I want to verify the existing call chain and
   contract before finalizing a Spec, so that implementation does not discover
   contradictions late.
2. As an implementer, I want the first TDD slice to exercise the real changed
   internal boundary, so that a green test proves the behavior that production
   callers use.
3. As an implementer, I want to use doubles only beyond that boundary, so that
   external variability remains isolated without bypassing the behavior under
   change.
4. As an Originating Task, I want repeated failures to trigger a focused
   diagnosis, so that remediation addresses the mechanism rather than its
   latest symptom.
5. As a maintainer, I want sibling paths sharing a diagnosed mechanism checked
   before the next fix, so that the same regression is not rediscovered in the
   next slice.
6. As a workflow user, I want these checks to stay inside existing stages and
   review seams, so that routine work does not gain new gates or agents.

## Implementation Decisions

### 1. Add a change-contract preflight to existing Stage-2 readiness

Update `skills/solution-design/references/design-readiness.md` and the
Stage-2 child/protocol wording that invokes it.

For every material changed behavior, the Spec records a concise inventory in
its existing `Implementation Decisions` or `Testing Decisions` sections:

- affected entrypoints and their current callers;
- the owning module and public/internal Interface, including inputs, outputs,
  errors, and dependency seam;
- relevant state, persistence owner, transitions, and retry/recovery behavior;
- ordering or call-sequence assumptions that alter outcomes;
- the governing requirement/Spec statement and repository evidence from the
  real production call chain; and
- contradictions found and the in-scope decision that resolves each one.

The child inspects the implementation and existing tests needed to establish
that inventory before solution review or direct publication. A missing or
contradictory fact is repaired in the same Spec when the frozen requirement
allows it. If resolution would change the requirement, target, permissions, or
published plan, stop through `SOLUTION_DESIGN_ANOMALY`. A one-module change
with no shared policy may state that the inventory dimensions are not
applicable with a concrete reason; Interface and call-chain evidence remain
required.

The existing readiness checks remain the owning policy for module ownership,
Interface/seam, decision coverage, state coverage, and Testing Decisions. No
new review message, stage, or artifact is introduced. Completion evidence adds
one compact field pointing to the Spec's preflight/call-chain section so the
existing handoff proves the check occurred.

### 2. Make the first TDD slice prove the real internal boundary

Update `skills/guided-implementation/SKILL.md` and
`skills/guided-implementation/references/execution-protocol.md`.

Before implementing the remaining Tickets or sibling paths, the Dedicated
Implementation Task must:

1. read the Spec's owner, Interface, call-chain, and Testing Decisions;
2. choose one representative observable behavior that crosses the changed
   internal boundary through the production caller wiring;
3. write the smallest failing test at that boundary and make it pass with the
   minimal in-scope implementation; and
4. only then expand the proven implementation to equivalent in-scope paths.

The boundary under change and its production caller may not be replaced by a
mock, stub, fake, or in-memory substitute. Doubles remain allowed for external
systems or dependencies downstream of the chosen boundary when the Spec's
seam requires variability. If the repository offers no executable seam that
can exercise the changed boundary, the task reports an implementation-
authority/testing-seam gap before coding; it does not bypass the boundary to
obtain a green test.

The first-slice requirement is a local TDD ordering rule, not a new workflow
stage, fixed number of tests, universal integration-test mandate, or additional
user confirmation. Focused and full checks remain unchanged.

### 3. Require diagnosis before repeating a failure mechanism

Update `skills/guided-implementation/references/originating-task-protocol.md`
and the matching remediation wording in `SKILL.md` and
`references/execution-protocol.md`.

The Originating Task marks remediation as diagnosis-first when any of these
observable triggers occurs:

- the same failure mechanism is reported again after a fix;
- a same-class regression appears in another affected path; or
- successive review/test outcomes overturn the same implementation approach.

The next continuation to the same Dedicated Implementation Task carries the
trigger and the prior candidate/finding identities. Before editing, that task
must provide four pieces of execution evidence: (a) the prior failure and fix
compared side by side, (b) why the fix did not address the mechanism, (c) the
minimal effective validation at the affected real boundary, and (d) the sibling
paths inspected and their applicability. The task then runs the focused
validation, repairs in scope, reruns the affected and full checks, and commits
the replacement candidate as usual.

If the diagnosis shows that the accepted scope or plan is insufficient, stop
for the existing implementation-authority/anomaly decision before changing
code. If it shows an ordinary technical defect, continue in the same task and
Flow Worktree. Do not create a diagnostic file, add a fixed retry count, or
spawn an automatic replacement task; the existing stalled-task replacement
exception remains limited to terminal no-progress results.

### 4. Preserve ownership, handoffs, and authority

- Stage 2 remains the sole owner of Spec readiness and preflight evidence.
- The Dedicated Implementation Task remains the owner of TDD, implementation,
  focused/full checks, diagnosis, and candidate commits.
- The Originating Task remains the owner of review dispatch, trigger detection,
  candidate acceptance, and Stage-4 handoff; it does not edit implementation
  files.
- The existing Flow Worktree, Ticket order, review fixed points, anomaly path,
  and remote-authority boundaries remain unchanged.
- Continuous mode performs these conditional checks without adding user review
  prompts; stepwise mode retains its existing confirmation points.

### 5. Limit the implementation paths

The implementation may change only these existing instruction and test paths:

- `skills/solution-design/SKILL.md`
- `skills/solution-design/references/design-readiness.md`
- `skills/solution-design/references/subagent-protocol.md`
- `skills/solution-design/references/templates.md`
- `skills/guided-implementation/SKILL.md`
- `skills/guided-implementation/references/execution-protocol.md`
- `skills/guided-implementation/references/originating-task-protocol.md`
- one focused Stage-2/Stage-3 contract test module under
  `skills/solution-design/scripts/` or `skills/guided-implementation/scripts/`

No business implementation, native Matt Skill, governance plugin, Context
glossary, ADR, deployment, or external tracker is changed.

## Testing Decisions

### Stage-2 contract seam

Extend or add a focused static contract test that reads the published
Stage-2 files and asserts:

- readiness names the affected interface, state, ordering, specification, and
  real production call-chain preflight;
- contradictions are resolved in-scope or routed through the existing anomaly
  contract before review/publication;
- completion/handoff evidence carries the preflight result;
- both flow modes retain the same review/confirmation behavior; and
- no second design agent, stage, or standalone readiness artifact is added.

The test should assert stable obligations and section relationships rather than
entire paragraphs.

### Stage-3 contract seam

Extend or add a focused static contract test for the Stage-3 Skill and
references. It must prove that:

- the first TDD slice uses the changed real internal boundary and production
  caller path before sibling expansion;
- doubles are limited to dependencies beyond that boundary;
- an unavailable real seam is reported as an authority/testing gap rather than
  bypassed; and
- repeated failure, same-class regression, and plan reversal trigger the four
  diagnosis evidence items before another fix, while ordinary remediation and
  stalled-task replacement remain distinct.

The test also guards that no fixed remediation round gate, automatic replacement
agent, or new Workflow Stage is introduced.

### Repository validation

Run `./scripts/validate.sh` and the focused contract tests. The validator must
continue to pass dependency/reference reachability, repository documentation
checks, and all existing discovered tests. No live Stage 1–4 run or universal
end-to-end harness is required for this instruction-only change.

## Acceptance Criteria

- Stage-2 instructions require a real call-chain/contract preflight and resolve
  contradictions before the existing solution review or publication seam.
- Stage-3 instructions require a representative first TDD slice through the
  changed internal boundary and prohibit doubles that bypass it.
- Repeated mechanisms, same-class regressions, and repeated plan reversals
  require diagnosis, minimal effective validation, and sibling-path inspection
  before another fix in the same task.
- Existing stages, agents, confirmations, Flow Worktree behavior, and remote
  permissions remain intact.
- Static contract tests and `./scripts/validate.sh` pass.

## Out of Scope

- Any business-code or test-suite behavior change outside instruction-contract
  tests.
- A new Workflow Stage, independent design/review artifact, fixed retry or
  remediation-round limit, automatic agent replacement, or universal E2E rule.
- Changes to native `$to-spec`, `$tdd`, `$implement`, `$code-review`,
  `$ask-matt`, `$to-tickets`, or the subagent-governance plugin.
- Requiring every simple change to map every readiness dimension or run a broad
  integration test.
- Changes to Ticket tracker state, discussion lifecycle, ADRs, deployment,
  release, push, pull request, or other external writes.

## Further Notes

No ADR is required. The plan deepens existing Stage-2 readiness and Stage-3
implementation/remediation responsibilities without changing the accepted
Flow Worktree, actor, or authority architecture recorded by ADR-0001,
ADR-0002, and ADR-0005.

## Delivery Order

1. Update the Stage-2 readiness reference, protocol/template evidence, and its
   static contract test.
2. Update the Stage-3 first-slice TDD wording and static contract test.
3. Update the same-task remediation protocol and extend the Stage-3 contract
   assertions; run repository validation.
