# Remove dual-mode and obsolete repository-lease authorization protocols

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Complete the cutover to ADR-0002 after the replacement lifecycle, provisioning,
integration and closure paths exist. Delete the old mode, lease and compatibility
surfaces instead of leaving dead dispatch branches.

## Acceptance criteria

- [x] `exclusive-checkout-v2` and ordinary-checkout implementation code are
      removed from Skills, references, deterministic protocol CLIs, schemas,
      handoffs, supervision messages, footers, closure checkpoints and tests.
- [x] Long repository lease acquire/inspect/verify/release, renewal, queueing,
      state files and repository/document lease priority rules are removed.
- [x] Worktree execution lease vocabulary and expiring holder state are replaced
      by the durable execution-claim contract everywhere.
- [x] Execution-mode fields and branches are removed from new discussion phase
      runs and implementation records; callers do not select a constant mode.
- [x] Old handoff, repository-lease, control and closure versions have no runtime
      compatibility dispatcher or migration path.
- [x] Cutover preflight returns stable `unsupported_stale_execution_state` for
      an old active artifact and performs zero mutation. It never normalizes the
      artifact as released or completed.
- [x] Trusted caller context, one-time repository-lease release authorization,
      repository-lease v3 and managed identity-Hook code or documentation are
      absent from the active architecture.
- [x] ADR-0001 and user-facing documentation are updated where necessary to
      describe the remaining authority boundary and single worktree lifecycle
      without rewriting historical completion records.
- [x] Repository-wide static checks find removed vocabulary only in explicitly
      retained historical planning records that point readers to ADR-0002.

Blocked by: 02, 03, 04

## Comments

- `.scratch/repository-lease-security/` is closed as superseded by this plan; it
  is not an implementation dependency.
