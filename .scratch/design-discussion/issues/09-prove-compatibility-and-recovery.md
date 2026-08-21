# Prove compatibility and end-to-end recovery

Status: `ready-for-agent`

## What to build

Complete the feature with an executable compatibility and recovery matrix that proves the new optional 0讨论 lifecycle does not weaken existing 1—4 behavior, authorization or workspace safety.

## Acceptance criteria

- [x] The repository validation command discovers and runs every Skill and protocol test, checks dependency registration, progressive-reference integrity and user-specific path leakage.
- [x] End-to-end scenarios cover root discussion through 1/2/3/4, direct 0→2, direct 1→3, parent/child absorption and coverage, `no-code-integration`, two safe isolated implementations and serial shared-base integration.
- [x] Fault injection covers every boundary between ledger transaction, lease, Git/worktree, external carrier creation, ready/activate, terminal claim, documentation commit and cleanup.
- [x] The suite verifies no automatic unconfirmed worktree, no double-active run or binding, no stale-source integration, no documentation in implementation commits, no force cleanup of unknown work and no premature topic close.
- [x] `none`/`ambiguous` discovery fixtures prove protocol-level zero writes plus mechanically observable legacy contract invariants: unchanged 1—4 entry paths, confirmations, draft/commit ownership, task roles and stable success/block/recovery footers, without requiring discussion identity. This does not claim byte-for-byte equivalence of generative conversation text.
- [x] Legacy v2, v1 and older worktree handoff/checkpoint fixtures continue through their embedded protocols without automatic conversion.
- [x] Project documentation explains 0讨论, optional lifecycle routes, dependencies and validation without duplicating protocol references or changing external-write authority.

Blocked by: 06 — Integrate 1拷问 and 2方案 with discussion sources; 07 — Run implementation in two controlled modes; 08 — Archive integrated results safely.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
- 2026-08-21 后续加固将过度的“生成式控制流逐字节等价”承诺收敛为三层可执行证明：discover-context 的 canonical zero-write、Skill/关键 legacy contract invariants、以及 none/ambiguous 下规范化 1—4 action sequence；全量验证 153 项通过。
