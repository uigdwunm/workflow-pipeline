# Converge implementation outcomes and clean resources idempotently

Status: `ready-for-agent`

## What to build

Redesign Stage 4 around protected source documents, immutable outcome proposals
and checkpoint-owned cleanup. Eliminate dirty worktree proposals and make every
partial cleanup result mechanically recoverable without force operations.

## Acceptance criteria

- [ ] Stage 4 accepts only a verified `INTEGRATED` result and cannot merge or
      otherwise republish the implementation branch.
- [ ] Implementation-outcome document proposals are stored as immutable typed
      evidence outside the implementation worktree, including exact before/after
      identities. The implementation worktree does not retain unstaged source
      document edits as proposal transport.
- [ ] Proposal validation rejects new requirements or source changes that were
      not part of the frozen implementation authority.
- [ ] Document convergence verifies exact source blobs, acquires document leases
      for exact paths and applies explicit apply/no-op/conflict semantics.
- [ ] A documentation commit uses the short target-branch publication slot and
      expected-HEAD CAS and never overwrites manual/external changes.
- [ ] When several implementations share a source, no closure modifies that
      source while another dependent implementation remains active. The final
      eligible convergence evaluates every retained outcome proposal before
      releasing source protection.
- [ ] Closure phases are exactly `documents-committed -> worktree-removed ->
      branch-removed -> execution-claim-released -> archived`, and the execution
      claim is the final resource released.
- [ ] Worktree cleanup checks exact registration, path, branch, commit and clean
      status; branch cleanup checks the exact ref and proven integration ancestry.
      Both use non-force Git operations.
- [ ] If worktree or branch removal succeeded but checkpoint persistence failed,
      recovery can adopt the uniquely proven absence and advance. Unexplained
      absence, changed identity and ambiguous effects stop without cleanup.
- [ ] The checkpoint CLI performs cleanup side effects and claim release itself
      and rejects caller-authored success receipts.
- [ ] Tests cover shared-source convergence, apply/no-op/conflict, external
      document edits, documentation commit races, dirty worktree refusal, every
      cleanup crash boundary, already-completed effects and administrative
      abandoned-claim reconciliation.

Blocked by: 01, 03

## Comments

- A user-requested new requirement is not a closure proposal; it follows the
  successor-document or stop-and-restart rule from Ticket 01.
