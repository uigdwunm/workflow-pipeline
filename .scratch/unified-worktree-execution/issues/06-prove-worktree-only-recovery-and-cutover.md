# Prove worktree-only execution, recovery and cutover end to end

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Add the final black-box and repository validation matrix proving that the single
worktree protocol covers serial and parallel use, source immutability,
publication, closure and crash recovery with no hidden legacy or Hook dependency.

## Acceptance criteria

- [x] One serial run completes
      `PREPARED -> PROVISIONING -> ACTIVE -> CANDIDATE -> ACCEPTED -> INTEGRATING
      -> INTEGRATED -> ARCHIVING -> ARCHIVED` using one worktree and claim.
- [x] Two independent implementations execute concurrently in different
      worktrees and publish serially against the evolving target branch.
- [x] Known unsafe overlap queues, while unexpected overlap is caught during
      integration and follows the conflict-repair protocol without corrupting
      either worktree or the primary checkout.
- [x] Source-protection tests cover every planning-document writer, multiple
      dependent implementations, successor documents, cancellation and external
      source drift.
- [x] Conflict tests separately prove textual repair, local semantic adaptation
      with new candidate acceptance, and material redesign paused for the user.
- [x] Dirty primary checkout, in-progress Git operation, concurrent publisher,
      external target-ref movement and failed verification leave user state
      byte-for-byte and ref-for-ref unchanged outside retained protocol evidence.
- [x] Failure injection surrounds source protection, claim creation, worktree
      creation, candidate acceptance, merge commit, integration receipt,
      document commit, worktree removal, branch removal, claim release and source
      protection release.
- [x] Every injected failure recovers to exactly one effect or a typed ambiguity;
      no duplicate merge, lost proposal, premature source write, force cleanup or
      automatic claim takeover occurs.
- [x] Old execution mode, repository lease, legacy handoff and legacy closure
      fixtures fail closed with `unsupported_stale_execution_state` and zero
      mutation.
- [x] The full `scripts/validate.sh` suite and repository validator pass, and a
      static contract check proves no active path depends on caller identity or
      an installed/configured managed Hook.

Blocked by: 01, 02, 03, 04, 05

## Comments

- Tests should exercise protocol CLIs as subprocesses and inspect observable Git
  refs, worktrees, files, state revisions and stable error codes.
