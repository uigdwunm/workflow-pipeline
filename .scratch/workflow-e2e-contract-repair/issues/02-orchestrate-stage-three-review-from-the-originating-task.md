# 02 — Orchestrate Stage-3 review from the Originating Task

Status: `ready-for-agent`

## What to build

Make the Originating Task own Standards and Spec review orchestration for each exact Stage-3 candidate, while the same Dedicated Implementation Task owns implementation, TDD, validation, candidate commits, and remediation. Invalidate and rerun both axes after every candidate change, then prove the complete role boundary through a fresh standalone 1→2→3→4 run.

## Acceptance criteria

- [ ] Stage-3 instructions assign the Dedicated Implementation Task only implementation, Ticket-ordered TDD, focused/full validation, clean candidate commits, risk reporting, and remediation in its verified worktree.
- [ ] Stage-3 instructions prohibit the Dedicated Implementation Task from dispatching Standards or Spec review agents and do not pass it the parent Session or CLI.
- [ ] The Originating Task independently verifies the candidate boundary and dispatches the Standards and Spec axes against the same fixed point and exact candidate commit.
- [ ] Every actionable test or review finding returns to the same Dedicated Implementation Task and Worktree Binding; that task fixes it, reruns appropriate checks, and commits a replacement candidate.
- [ ] Any candidate commit change invalidates both prior axis results, and the Originating Task reruns Standards and Spec review before accepting the replacement.
- [ ] Only the Originating Task accepts the candidate, integrates it, and reports separate Standards/Spec evidence in the Stage-4 handoff.
- [ ] Repository-level contract tests enforce the actor split, exact-candidate binding, same-task remediation, mandatory two-axis rerun, Ticket order, Worktree Binding, planning-source, remote-authority, target-advance, and Stage-4 handoff invariants.
- [ ] Native Standards and Spec definitions and reviewer prompts remain unchanged, and no governance plugin code or cross-Session authority-transfer mechanism is added.
- [ ] A separate new standalone effort completes 1拷问 → 2方案 → 3实现 → 4归档 with no Discussion Topic attachment: the Dedicated Implementation Task submits a candidate, the Originating Task dispatches both reviews, one controlled actionable finding returns to the same task and produces a new candidate, both axes rerun against it, integration succeeds, and Stage 4 completes.
- [ ] The E2E evidence records the fixed point, reviewed candidate identities, separate axis results, remediation task identity, focused/full checks, merge commit, and Stage-4 outcome; full repository validation passes.

Blocked by: 01 — Answer the active Question when confirming a Decision.
