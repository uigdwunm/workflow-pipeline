# Run implementation in two controlled modes

Status: `ready-for-agent`

## What to build

Extend `3实现` so a verified discussion plan can run either serially in the ordinary checkout or, after a separate concrete user decision, in an exact isolated worktree, with immutable source, typed leases and source-refresh recovery.

## Acceptance criteria

- [ ] Parallelism compares declared paths, modules, interfaces, database objects, dependencies, bases, branches and active worktrees and returns safe, blocked or unknown; changed inputs invalidate the result.
- [ ] Every run freezes one execution mode and a committed read-only implementation-source checkpoint; the mode cannot change after activation.
- [ ] `exclusive-checkout-v2` queues when the ordinary checkout is not exclusively available and never shares it with another active implementation.
- [ ] `isolated-worktree-v1` is created only after safe parallelism and explicit confirmation of the exact path, branch, base, scope and sensitive shared surfaces.
- [ ] The isolated run is bound to the exact worktree and a long-lived execution lease; platform inability to verify the working directory blocks instead of falling back or creating another worktree.
- [ ] Implementations may proceed in parallel, but integration into the shared base is serialized and revalidates current source, dependencies and active implementations.
- [ ] Source changes are decision/scope impacts; a no-impact refresh uses candidate, implementation acknowledgement and source-topic commit before resuming the same run.
- [ ] Supervision protocol tests cover repository coordination and worktree execution leases, dual-mode handoffs, exact binding, queueing, concurrent safe runs, stale source and legacy versions.

Blocked by: 05 — Coordinate phase runs and lifecycle routing; 06 — Integrate 1拷问 and 2方案 with discussion sources.

## Comments
