# Run implementation in two controlled modes

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Extend `3实现` so a verified discussion plan can run either serially in the ordinary checkout or, after a separate concrete user decision, in an exact isolated worktree, with immutable source, typed leases and source-refresh recovery.

## Acceptance criteria

- [x] Parallelism compares declared paths, modules, interfaces, database objects, dependencies, bases, branches and active worktrees and returns safe, blocked or unknown; changed inputs invalidate the result.
- [x] Every run freezes one execution mode and a committed read-only implementation-source checkpoint; the mode cannot change after activation.
- [x] `exclusive-checkout-v2` queues when the ordinary checkout is not exclusively available and never shares it with another active implementation.
- [x] `isolated-worktree-v1` is created only after safe parallelism and explicit confirmation of the exact path, branch, base, scope and sensitive shared surfaces.
- [x] The isolated run is bound to the exact worktree and a long-lived execution lease; platform inability to verify the working directory blocks instead of falling back or creating another worktree.
- [x] Implementations may proceed in parallel, but integration into the shared base is serialized and revalidates current source, dependencies and active implementations.
- [x] Source changes are decision/scope impacts; a no-impact refresh uses candidate, implementation acknowledgement and source-topic commit before resuming the same run.
- [x] Supervision protocol tests cover repository coordination and worktree execution leases, dual-mode handoffs, exact binding, queueing, concurrent safe runs, stale source and legacy versions.

Blocked by: 05 — Coordinate phase runs and lifecycle routing; 06 — Integrate 1拷问 and 2方案 with discussion sources.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
- Historical completion only: ADR-0002 and
  `.scratch/unified-worktree-execution/PRD.md` supersede the dual-mode execution
  decision for future implementation.
