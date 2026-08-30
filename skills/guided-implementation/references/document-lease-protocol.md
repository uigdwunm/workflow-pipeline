# Document Lease Protocol

The document lease is a short cooperative CAS barrier for exact paths. It
grants no code, Git publication, branch, remote or task-identity authority.

- Every write acquisition supplies sorted unique paths.
- No Skill may edit an active implementation source.
- Stage 4 may overlap a source only for the final remaining dependent
  implementation; another active dependency blocks convergence.
- Verify path, ID and revision immediately before writing and release after the
  bounded operation.
- Current equals base means apply, current equals proposal means no-op, and
  every other byte identity is conflict. Never overwrite external changes.

Checkpoint publication may use the short repository coordination barrier with
purpose `checkpoint-publish`. Worktree creation and routine commits do not.
Target and documentation commits use the short publication slot plus expected
HEAD CAS.

Outcome proposals are immutable external evidence with exact before/after
identities. Dirty worktree documents are not proposal transport.
