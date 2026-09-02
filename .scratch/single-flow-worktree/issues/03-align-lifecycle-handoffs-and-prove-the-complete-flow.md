# 03 — Align lifecycle handoffs and prove the complete flow

Status: ready-for-agent

## What to build

Align the discussion-attached lifecycle, stage templates, domain documentation,
and repository validation with the retained Flow Worktree. Prove both the full
Stage-2-to-Stage-4 path and standalone Stage 3 without adding new coordination
state or remote behavior.

## Acceptance criteria

- [x] Stage-2 completion templates carry the exact Worktree Binding, planning commit, planning merge commit, publication result, and retained-worktree state.
- [x] Stage-3 completion templates carry the accepted candidate and retained binding and assign final publication and cleanup to Stage 4.
- [x] Stage-4 completion templates report the final merge, implementation and closure commits, and exact cleanup result.
- [x] Attached `2→3` Phase Runs may complete after candidate acceptance in the retained Flow Worktree, while `3→4` completes only after final Git publication and cleanup.
- [x] Discussion state remains separate from Git worktree execution and receives no copied binding, lease, queue, or execution lifecycle.
- [x] README, dependency guidance, CONTEXT terminology, ADRs, progressive references, and static contract tests describe one consistent lifecycle.
- [ ] A fresh standalone `1拷问 → 2方案 → 3实现 → 4归档` exercise uses exactly one Flow Worktree from Stage 2 through final cleanup.
- [ ] A fresh standalone Stage-3 exercise creates exactly one Flow Worktree and hands it to Stage 4 for final cleanup.
- [ ] Planning publication with an unrelated target advance continues without user input; a controlled real conflict preserves state and resumes only after user confirmation.
- [x] `./scripts/validate.sh` passes with no undocumented remote action, replacement worktree, or compatibility path.

Blocked by: 02 — Reuse or create the Flow Worktree through closure.

## Comments

- 2026-09-02: Automated protocol and static contract checks cover the Git
  mechanics, but no fresh Agent workflow exercise was recorded. Reopened the
  three end-to-end acceptance items instead of treating lower-level simulation
  as completion evidence.
