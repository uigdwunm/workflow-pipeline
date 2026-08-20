# Execution Modes

Use this reference whenever a discussion-backed Phase Run enters `3实现`.
The discussion protocol owns the implementation plan, parallelism receipt,
source checkpoint and source-refresh state. The supervision protocol owns every
repository, coordination and worktree-execution lease and the integration
checkpoint. Never copy either state machine into the other protocol.

## Contents

- Freeze the run
- Run in the ordinary checkout
- Run in an isolated worktree
- Revalidate and integrate
- Refresh an unaffected source
- Recover and release

## Freeze the run

1. Use `prepare-implementation-run` with normalized repository-relative paths,
   modules, interfaces, database objects, dependencies, base commit, branch and
   canonical worktree path.
2. Use `check-implementation-parallelism`. A `safe` result is only a technical
   result; it is not permission to create a worktree. `blocked` stops the
   isolated launch. `unknown` requires more evidence and never falls back to
   the ordinary checkout.
3. Freeze one completed Git `implementation-source` checkpoint. It must include
   the exact commit identity and content SHA-256 and remain read-only while the
   implementation is active.
4. Call `activate-implementation-run` once. The selected mode and source become
   immutable. A changed worktree observation makes the receipt stale and
   requires a new check. Never change mode after activation.

New handoffs use `handoff_version: 4`. `verify-handoff` dispatches v1-v3
handoffs under their embedded legacy protocol and does not upgrade them.

## Run in the ordinary checkout

`exclusive-checkout-v2` remains the default when no isolated arrangement was
explicitly confirmed.

1. Run `check-execution-availability`. Queue while any linked worktree,
   worktree-execution lease, serial integration or closure critical section is
   active. After every wait, rerun the check; an earlier result grants no
   authority.
2. Acquire and continuously verify the existing long-lived repository lease.
3. Bind the handoff to the ordinary checkout, zero worktrees, base branch,
   base commit, implementation branch and exact repository-lease identity.
4. Follow the existing exclusive-checkout branch, document barrier, commit and
   merge protocol.

Never share the ordinary checkout with another active implementation and never
convert a queued run to isolated mode without a new discussion decision and a
new, not-yet-activated run.

## Run in an isolated worktree

`isolated-worktree-v1` requires a `safe` receipt and one explicit confirmation
whose bytes bind all of:

- canonical absolute worktree path;
- implementation branch and base commit;
- normalized scope SHA-256;
- every sensitive shared surface; and
- the fact that worktree creation is authorized for this run only.

Create the worktree only while holding a short repository coordination lease.
Immediately acquire the long-lived execution lease:

```text
supervision_protocol.py acquire-worktree-execution-lease --input <input>
```

The input binds repository, topic, Phase Run, implementation, base, branch,
worktree path, scope digest, sensitive surfaces, owner and TTL. Before Git,
project execution, implementation mutation, test execution or commit, verify:

```text
supervision_protocol.py verify-worktree-execution-lease \
  --file <lease> --id <id> --version <version> --platform-cwd <exact cwd>
```

If the platform cannot prove the exact working directory, stop. Do not use the
ordinary checkout, create a replacement worktree or infer equivalence from the
branch name. The base may become an ancestor of later implementation commits,
but the worktree path and branch remain exact.

Two isolated implementations may run only when each has its own exact worktree
and execution lease and the discussion receipt remains `safe`.

## Revalidate and integrate

Parallel implementation does not authorize parallel integration. Acquire a
repository coordination lease with stage `guided-implementation` and purpose
`serial-integration`, then run:

```text
supervision_protocol.py revalidate-integration --input <input>
```

The expected and current snapshots must match for source identity, dependency
receipt and active-implementation receipt. Any stale dimension blocks merge.
Also rerun the discussion parallelism/source validation, candidate checks and
document barrier checks against the current shared base. Integrate exactly one
candidate, release the coordination lease, then allow the next candidate to
revalidate.

An integrated isolated run keeps its worktree, branch, execution lease and
document proposal until `4归档`; successful integration is not cleanup
authority.

## Refresh an unaffected source

A source change first records `no-impact`, `affected` or `unknown` against the
implementation's decisions and scope. `affected` and `unknown` remain paused.
For `no-impact` only:

1. `prepare-source-refresh` records a candidate pointing to a different,
   completed Git `implementation-source` checkpoint.
2. The exact implementation carrier calls `ack-source-refresh` with its
   implementation identity and candidate source identity.
3. The source topic calls `commit-source-refresh` and resumes the same
   implementation ID. No new run, mode switch or worktree is created.

## Recover and release

Use `reconcile-worktree-execution-lease` after an outcome-unknown boundary.
`outcome=active` re-verifies the exact platform cwd and binding;
`outcome=released` performs the exact CAS release. A missing or mismatched cwd
is a blocker, not evidence that the lease may be replaced.

Normal isolated cleanup is non-forcing: verify integration/closure evidence,
remove only the exact clean worktree and merged branch, release the exact
execution lease, and preserve unknown work. Exclusive mode continues to use
the repository-lease release protocol. In both modes, release the document or
coordination barrier immediately after its bounded critical section.
