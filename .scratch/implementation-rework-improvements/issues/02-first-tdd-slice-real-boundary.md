# 02 — Make the first TDD slice exercise the real internal boundary

Status: `ready-for-agent`

## What to build

Clarify Stage-3 implementation instructions so the Dedicated Implementation
Task starts with one representative behavior test through the changed internal
boundary and production caller wiring, then expands to sibling paths. Keep
doubles available only for dependencies beyond that boundary and report an
authority/testing-seam gap when no executable seam exists.

## Acceptance criteria

- [ ] Stage-3 Skill and execution reference define the first-slice ordering and
      production call path.
- [ ] The changed boundary and caller path cannot be replaced by a mock, stub,
      fake, or in-memory substitute.
- [ ] External/downstream dependency doubles remain allowed where the Spec
      requires variability.
- [ ] No fixed test count, new stage, universal integration-test mandate, or
      extra confirmation is added.
- [ ] Focused Stage-3 contract tests pass.

Blocked by: None — can start immediately.
