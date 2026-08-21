# Publish verifiable discussion checkpoints

Status: `ready-for-agent`

## What to build

Make pause, handoff, splitting and stage entry depend on a durable, verifiable discussion checkpoint in both Git and non-Git projects, with safe reconciliation after uncertain writes or rewritten history.

## Acceptance criteria

- [x] Git checkpoints freeze topic, purpose, exact document bytes, decision digests, path set, base reference and idempotency identity before a documentation-only commit.
- [x] Checkpoint commits carry the approved machine trailers and are adopted after an uncertain outcome only when parent, tree, paths, blobs and digests uniquely match.
- [x] A changed draft supersedes or cancels an uncommitted checkpoint intent without reusing its identity; an unresolved older uncertain checkpoint must be reconciled first.
- [x] Non-Git projects create or reuse immutable content-addressed snapshots and refuse to overwrite a mismatching existing object.
- [x] Checkpoint repair preserves the original broken fact and only maps to a unique fully verified replacement; active implementation sources require a fresh acknowledgement.
- [x] Checkpoint GC is dry-run first, binds confirmation to an exact candidate list and ledger revision, and never deletes referenced or uncertain snapshots.
- [x] Tests inject failure around prepared ledger state, commit/snapshot creation, result recording, rewritten history, repair and GC while preserving unrelated workspace content.

Blocked by: 02 — Evolve topic documents safely.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
