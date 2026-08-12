# Archive integrated results safely

Status: `ready-for-agent`

## What to build

Make `4归档` close out either implementation mode from the source topic: converge documentation against the latest base, preserve recoverable evidence, clean resources non-destructively and close the topic only when all coordination work is finished.

## Acceptance criteria

- [ ] Archive validates the effective3 result, source chain, merge ancestry, execution mode, implementation record, leases, documentation proposals and retained checkpoint before acting.
- [ ] Isolated-mode documentation is converged in a verified base checkout using apply/no-op/conflict three-way semantics; the implementation worktree remains proposal/evidence input only.
- [ ] Resource cleanup is checkpointed and ordered, uses non-force worktree/branch operations, releases the exact execution lease and never hides a missing or ambiguous side effect.
- [ ] `exclusive-checkout-v2`, `isolated-worktree-v1` and legacy retained checkpoints dispatch to their immutable protocols without migration.
- [ ] Archive completion and topic closure are separate facts; pending impacts, absorption, active/queued runs, blockers or unverified coordination keep the topic open.
- [ ] Source-topic footers report effective sources, phase results, cleanup responsibility and conditional close result without exposing internal IDs as user controls.
- [ ] Fault tests cover proposal apply/no-op/conflict, partial cleanup and resume, missing worktree, branch deletion refusal, lease release uncertainty, archive-complete/topic-open and final close.

Blocked by: 07 — Run implementation in two controlled modes.

## Comments
