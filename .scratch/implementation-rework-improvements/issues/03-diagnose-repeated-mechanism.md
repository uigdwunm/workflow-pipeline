# 03 — Diagnose repeated failure mechanisms before remediation

Status: `ready-for-agent`

## What to build

Extend the Originating Task and Dedicated Implementation Task remediation
contract with diagnosis-first triggers for repeated mechanisms, same-class
regressions, and repeated plan reversals. Require comparison of the prior fix,
cause analysis, minimal effective validation, and sibling-path inspection before
the next in-scope edit in the same Flow Worktree.

## Acceptance criteria

- [ ] The protocol names all three observable triggers and carries prior
      candidate/finding identity into the same-task continuation.
- [ ] Diagnosis evidence explains why the previous fix was ineffective, adds a
      minimal effective validation at the real boundary, and checks affected
      sibling paths before another fix.
- [ ] Scope/plan gaps stop through the existing authority/anomaly path; ordinary
      defects continue in the same task and worktree.
- [ ] No diagnostic document, fixed retry-round gate, or automatic replacement
      agent is introduced.
- [ ] Focused contract tests and `./scripts/validate.sh` pass.

Blocked by: None — can start immediately.
