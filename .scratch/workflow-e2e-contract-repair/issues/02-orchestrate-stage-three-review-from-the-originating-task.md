# 02 — Orchestrate Stage-3 review from the Originating Task

Status: `completed`
Lifecycle: completed

## What to build

Make the Originating Task own Standards and Spec review orchestration for each exact Stage-3 candidate, while the same Dedicated Implementation Task owns implementation, TDD, validation, candidate commits, and remediation. Invalidate and rerun both axes after every candidate change, then prove the complete role boundary through a fresh standalone 1→2→3→4 run.

## Acceptance criteria

- [x] Stage-3 instructions assign the Dedicated Implementation Task only implementation, Ticket-ordered TDD, focused/full validation, clean candidate commits, risk reporting, and remediation in its verified worktree.
- [x] Stage-3 instructions prohibit the Dedicated Implementation Task from dispatching Standards or Spec review agents and do not pass it the parent Session or CLI.
- [x] The Originating Task independently verifies the candidate boundary and dispatches the Standards and Spec axes against the same fixed point and exact candidate commit.
- [x] Every actionable test or review finding returns to the same Dedicated Implementation Task and Worktree Binding; that task fixes it, reruns appropriate checks, and commits a replacement candidate.
- [x] Any candidate commit change invalidates both prior axis results, and the Originating Task reruns Standards and Spec review before accepting the replacement.
- [x] Only the Originating Task accepts the candidate, integrates it, and reports separate Standards/Spec evidence in the Stage-4 handoff.
- [x] Repository-level contract tests enforce the actor split, exact-candidate binding, same-task remediation, mandatory two-axis rerun, Ticket order, Worktree Binding, planning-source, remote-authority, target-advance, and Stage-4 handoff invariants.
- [x] Native Standards and Spec definitions and reviewer prompts remain unchanged, and no governance plugin code or cross-Session authority-transfer mechanism is added.
- [x] A separate new standalone effort completes 1拷问 → 2方案 → 3实现 → 4归档 with no Discussion Topic attachment: the Dedicated Implementation Task submits a candidate, the Originating Task dispatches both reviews, one controlled actionable finding returns to the same task and produces a new candidate, both axes rerun against it, integration succeeds, and Stage 4 completes.
- [x] The E2E evidence records the fixed point, reviewed candidate identities, separate axis results, remediation task identity, focused/full checks, merge commit, and Stage-4 outcome; full repository validation passes.

Blocked by: 01 — Answer the active Question when confirming a Decision.

## Closure evidence

- Main implementation fixed point: `c7a7bcd4ed9dfb0a5edc9594afd2c3a5cc9cf2e7`; accepted candidate: `9bb676aa47bd3d6b3dd0715e7f850dfcc3c3294d`; merge: `afbc1a42392f00fc50e4d2ade72b5b6028cb03b7`.
- Main final Standards and Spec reviews independently used that exact fixed point/candidate pair and reported no actionable findings. Post-integration `./scripts/validate.sh` passed all 148 tests.
- Standalone E2E planning fixed point: `8cec98b49b6277a15397e6b1b0ca18bea8626660`. Both axes rejected initial candidate `179c610461bd2be159670cecf3ac5a6d7595077d` for `README.md` mode drift `100644 -> 100755`.
- The same Dedicated Implementation Task `/root/sg_strict_act_as_the_sole_dedicated_implementatio_t_d5469d7196d5` remediated the finding in the same worktree and produced replacement candidate `ad6d89e654890a8b0da4afc52adafaa064157efd` without dispatching review agents.
- Originating Task reran both axes against the exact replacement; both reported no actionable findings. Implementation merge `9784aaa70cb15963e85da294f36c8db3b9878dbb` and Stage-4 closure merge `098ea2b7611e4644aea3c88edecadb5af9e05473` completed, with 148 tests passing at both gates and all temporary worktrees, branches, protocol files, and six governed tasks cleaned or closed.
- Remote operations: none.
