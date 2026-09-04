# Solution Readiness

The same `solution_designer` that drafts the Spec owns these checks. They are
part of stage-2 design work, not a separate review, message, agent, artifact, or
user checkpoint.

## Spec readiness

Complete this check after drafting the Spec and before the existing solution
review or direct publication seam. Record each applicable result in the Spec's
existing Implementation Decisions or Testing Decisions; do not add ceremonial
sections when the existing structure already carries the decision.

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
the existing Tickets review or direct publication seam.

- When Tickets are useful, every material implementation slice must trace to
  the owning Spec decision and acceptance condition, preserve required order or
  blocking relationships, and include the relevant test seam.
- Tickets may be `not applicable` only when the native `$ask-matt` decision says
  they are unnecessary. The complete Spec must still contain the implementation
  and testing basis.
- Ticket drafting may expose a missing Spec decision, but Tickets must not
  silently redesign it. Repair the Spec first, then regenerate the affected
  Ticket content.

## Repair and anomaly boundary

Draft repair inside the accepted requirement scope is ordinary stage-2 work by
the same child. A user edit invalidates the affected readiness evidence and the
child reruns only those checks before returning a fresh existing review.

Use the existing anomaly contract when readiness reveals that continuing would
change the accepted goal, requirement scope, constraints, acceptance
conditions, permissions, or target. Also use it when repairing an already
accepted or published Spec would change the committed plan. Do not add a new
review protocol for readiness.
