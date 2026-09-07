# 01 — Verify the Stage-2 change contract before solution review

Status: `ready-for-agent`

## What to build

Strengthen Stage-2 readiness so the `solution_designer` records affected
interfaces, state, ordering, governing specification, and real production
call-chain evidence, resolving in-scope contradictions before the existing
review/publication seam. Carry a compact preflight reference in completion
handoffs and add focused static contract coverage.

## Acceptance criteria

- [ ] `design-readiness.md` defines the bounded change-contract preflight and
      its evidence requirements.
- [ ] Stage-2 protocol/template wording places preflight before review or
      publication and carries its completion evidence.
- [ ] In-scope contradictions are repaired by the same child; scope or plan
      changes use the existing anomaly path.
- [ ] No new review message, stage, agent, or standalone readiness artifact is
      introduced.
- [ ] Focused Stage-2 contract tests and `./scripts/validate.sh` pass.

Blocked by: None — can start immediately.
