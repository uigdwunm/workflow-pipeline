# Provision every implementation with a durable worktree CAS claim

Status: `ready-for-agent`

## What to build

Replace mode-selected implementation setup with one worktree-only provisioning
protocol. Reserve execution ownership before Git worktree creation, bind the
implementation task to the exact resulting workspace and recover safely across
every provisioning failure boundary.

## Acceptance criteria

- [ ] New Stage-3 handoffs contain one worktree branch/path/base/source binding
      and no execution-mode choice or ordinary-checkout implementation path.
- [ ] Provisioning CAS-creates a durable execution claim containing repository,
      normalized worktree path, implementation branch, base commit, source
      checkpoint and implementation identity before `git worktree add`.
- [ ] The execution claim has no TTL, expiry takeover or renewal protocol.
      Competing acquisition and changed ID/version fail without mutation.
- [ ] Provisioning reconciliation compares the claim, filesystem, Git worktree
      registry and branch ref and returns only exact adoption, safe retry with no
      prior effect, or typed ambiguity.
- [ ] The implementation task proves that its platform working directory,
      repository common directory, worktree, branch, base and claim all match
      before substantive work; failure never falls back to another checkout.
- [ ] Serial scheduling uses queued worktree runs, while safe independent runs
      use different worktrees concurrently under the same protocol.
- [ ] Routine worktree creation and implementation commits do not acquire a
      repository-wide coordination lease.
- [ ] Ordinary standalone claim release is unavailable to implementation tasks.
      Only closure and explicit administrative reconciliation can reach release
      logic.
- [ ] Failure-injection tests cover claim publication, branch creation,
      worktree registration, filesystem creation, handoff delivery and exact
      resumption after each partial effect.

Blocked by: 01

## Comments

- The claim is cooperative CAS coordination, not authenticated task identity.
