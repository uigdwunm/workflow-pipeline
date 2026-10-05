# Solution Readiness

Read before drafting the Spec. Use the file-ownership and first TDD-slice
instructions while designing; complete the checks before review or publication.
The same `solution_designer` owns this stage-2 work, without a separate review,
message, agent, artifact, or user checkpoint.

## Bounded change-contract preflight

Before the existing Spec readiness check, perform a bounded preflight for each
material changed behavior. Record one concise inventory in the Spec's existing
`Implementation Decisions` or `Testing Decisions` section. The inventory must
identify:

- each affected entrypoint and its current production callers;
- the owning module and the public or internal interface, including inputs,
  outputs, failures, and the dependency seam;
- relevant state, its persistence owner, transitions, and retry or recovery
  behavior;
- ordering or call-sequence assumptions that can change the outcome;
- the governing requirement or Spec statement, together with repository
  evidence from the real production call chain; and
- every contradiction found and the in-scope decision that resolves it.

Inspect the implementation and existing tests needed to establish that
inventory before solution review or direct publication. Repair a missing or
contradictory fact in the same Spec when the frozen requirement and scope allow
it. If resolving it would change the accepted requirement, target, permissions,
or published plan, stop through the existing `SOLUTION_DESIGN_ANOMALY`
contract. A one-module change with no shared policy may mark the state,
ordering, or decision dimensions not applicable only with a concrete reason;
the interface and real production call-chain evidence are always required.

Completion evidence points to the Spec section containing this preflight and
its call-chain evidence. This is a compact handoff field, not a new readiness
artifact or review gate.

## Spec readiness

Complete this check after drafting the Spec and before the existing solution
review or direct publication seam. Record each applicable result in the Spec's
existing Implementation Decisions or Testing Decisions; do not add ceremonial
sections when the existing structure already carries the decision.

Resolve every Stage-1-deferred engineering question in the Spec before this
check passes. Choose and justify the mechanism against the frozen behavior and
constraints; do not pass an open mechanism to Stage 3. For example, after the
requirement confirms retry without repeated side effects, specify the
idempotency key, transaction boundary, and recovery mechanism here. If a
mechanism changes data retention, permission, charges, required user actions,
or another requirement promise, stop through the existing
`SOLUTION_DESIGN_ANOMALY` route for a user decision. The frozen requirement
remains read-only until its source owner resolves that decision.

1. **Module ownership:** Every changed behavior has one named owning module or
   component. State where shared policy lives and which callers consume it.
2. **Interface and seam:** Define the boundary through which each owner is used,
   including inputs, outputs, failures, and the dependency seam needed to test
   it without unrelated infrastructure.
3. **Decision coverage:** For competing signals, permissions, retries, errors,
   or fallbacks, define precedence and the result of each material branch.
4. **State coverage:** For durable or multi-step state, define the relevant
   states, transitions, persistence owner, and retry or recovery behavior.
5. **Testing decisions:** Map each material acceptance condition and design
   branch to the most direct observable test seam and test level.

When implementation is required, record the first representative TDD slice in
Testing Decisions: the production caller, the changed boundary, and the
observable failing test through their real wiring. Keep that caller path and
boundary real; doubles may replace external dependencies downstream of it.
If no executable seam exists, resolve the necessary in-scope seam change in the
Spec and include its files in the manifest, or return the existing anomaly
before publication when the accepted boundary cannot cover it.

Use evidence-based `not applicable` only when the requirement genuinely has no
such dimension:

- module ownership may be not applicable only for a change wholly contained in
  one existing module with no shared policy;
- decision coverage may be not applicable only when there are no competing
  signals, permissions, retries, error choices, or fallbacks; and
- state coverage may be not applicable only when there is no durable state and
  no changed transition.

Interface and testing evidence are always required. For a simple change, one
concise sentence may satisfy a check; do not invent matrices, abstractions, or
extension points merely to fill the evidence.

If a check exposes an omission inside the accepted requirement boundary, the
child revises the draft and repeats only the affected checks. It does not pass
an incomplete draft to the existing review or publication seam.

## Ticket traceability

After `$ask-matt` decides whether Tickets are useful, complete this check before
the combined review before planning publication, or direct publication in continuous mode.

- When Tickets are useful, every material implementation slice must trace to
  the owning Spec decision and acceptance condition, preserve required order or
  blocking relationships, and include the relevant test seam.
- Tickets may be `not applicable` only when the native `$ask-matt` decision says
  they are unnecessary. The complete Spec must still contain the implementation
  and testing basis.
- Ticket drafting may expose a missing Spec decision, but Tickets must not
  silently redesign it. Repair the Spec first, then regenerate the affected
  Ticket content.

Before the Stage-2 scope seal, put one versioned `execution-plan-v1` JSON block
in each implementation Ticket. Retain its delivery behavior, ACs and Blocked by
outside the block. Give each function step a stable ID, exact path/symbol and
operation, capability, ordered approach, key inputs/outputs/failures, declared
dependencies and behavior verification references. Several related functions
may share one verification. Cover non-function sources, tests and generated
source-to-output relationships with artifact steps and the manifest. Check
existing Python symbols and their real production callers; mark new functions
`add` and name the caller they will join. Resolve missing references, duplicate
IDs, dependency cycles and uncovered implementation paths before publication.
If native Tickets are unnecessary, place the same block for one logical slice
in the Spec. The committed plan source and its document hash are part of the
sealed readiness evidence; a later private plan change needs the Controller's
versioned revision transaction.

## Downstream file manifest

While drafting the Spec/Tickets, plan every downstream file required to execute
and close the design. Put exact repository-relative paths, operations and their
decision basis in those artifacts. Assign owner and kind by the file's purpose:

- Implementation owns `source`, `test` and required `generated` artifacts,
  including migrations and configuration.
- Closure owns ordinary explanatory documentation as `document`, including
  usage/technical guides, hand-written READMEs, CHANGELOGs and later updates to
  planning documents.
- Markdown consumed at runtime, such as a Prompt, is `source`. A generated
  README is `generated`. The `.md` suffix does not determine ownership.
- Plan a changed source and its generated outputs together. When the source is
  also in the manifest, assign its outputs the same owner. List exact
  `source_paths` and a complete source-to-outputs map; every mapped output of a
  manifest-owned source must be declared, with the same source set as the map.

A planning-only completion has `implementation_required=false` and both owner
lists empty. If there is no implementation but documentation
work remains, return `SOLUTION_DESIGN_ANOMALY` before planning publication with
the exact pending paths and operations. Preserve the draft and return control
to the original Controller, which applies existing entry and authorization
rules for standalone Stage 4. The designer does not launch closure or report
the pending work complete.

Before committing the planning candidate, ensure owners do not overlap, frozen
requirements stay protected, and each entry traces to a Spec/Ticket section.
An empty closure list is explicit; null downstream lists mean unfinished design.
After the clean candidate exists, completion cites its actual commit and section;
do not write the candidate's own future hash into the Spec. Generated output
paths are exact and sorted.

The Controller may seal a complete in-requirement file refinement. A new
behavior, permission, side effect, or change to an accepted design goes through
the existing anomaly and decision path before a revised manifest is proposed.

## Repair and anomaly boundary

Draft repair inside the accepted requirement scope is ordinary stage-2 work by
the same child. A user edit invalidates the affected readiness evidence and the
child reruns only those checks before returning a fresh existing review.

Use the existing anomaly contract when readiness reveals that continuing would
change the accepted goal, requirement scope, constraints, acceptance
conditions, permissions, or target. Also use it when repairing an already
accepted or published Spec would change the committed plan. Do not add a new
review protocol for readiness.
