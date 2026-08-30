# Integrate accepted candidates through a short target-branch publication CAS

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Replace frozen-base equality and mode-specific merge behaviour with one Stage-3
publication protocol. Keep coding in the implementation worktree, serialize only
the final target-branch mutation and return every conflict repair to the
original worktree.

## Acceptance criteria

- [x] Candidate review and acceptance bind one exact implementation commit,
      source checkpoint and verification record; changed candidate identity
      invalidates both review and acceptance.
- [x] The Stage-3 supervisor is the only actor that performs final integration
      and uses the verified primary checkout only for the bounded publication
      operation.
- [x] Publication refuses a dirty checkout, wrong branch, in-progress Git
      operation or mismatched expected target HEAD without stashing, cleaning,
      switching away from user work or otherwise changing the checkout.
- [x] A short target-branch publication slot serializes workflow publishers and
      an expected-HEAD CAS detects external ref movement immediately before the
      merge effect.
- [x] An advanced target branch no longer fails merely because it differs from
      the implementation's original base. The current target and source
      protection are revalidated, and the candidate is merged and verified
      against the exact current target when safe.
- [x] Merge failure or conflict aborts in the primary checkout and proves branch,
      HEAD, index, worktree and managed-document restoration before returning
      control to the original implementation worktree.
- [x] Textual/structural repair and local semantic adaptation that preserve
      scope, business behaviour and acceptance criteria can continue in the
      original worktree. Material semantic conflict, invalid assumptions or
      extensive redesign produces a typed user-decision pause.
- [x] Every repair integrates the latest target into the implementation branch,
      reruns tests and review, publishes a new candidate and requires new exact
      acceptance before retry.
- [x] Final integration verifies the merge commit parents, tree, target ref,
      candidate reachability and implementation-only paths before recording
      `INTEGRATED`.
- [x] Crash recovery around merge/commit distinguishes no effect, one uniquely
      matching merge and ambiguous outcome; only the unique match is adopted and
      no outcome-unknown operation is blindly retried.
- [x] Tests cover two concurrent publication attempts, external target movement,
      clean serial integration of parallel candidates, all three conflict
      classes, verification failure and commit-hook failure.

Blocked by: 02

## Comments

- Git commit hooks remain ordinary repository behaviour and are unrelated to the
  rejected caller-identity Hook design.
