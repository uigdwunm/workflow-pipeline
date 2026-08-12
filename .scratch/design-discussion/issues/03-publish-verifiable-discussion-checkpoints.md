# Publish verifiable discussion checkpoints

Status: `ready-for-agent`

## What to build

Make pause, handoff, splitting and stage entry depend on a durable, verifiable discussion checkpoint in both Git and non-Git projects, with safe reconciliation after uncertain writes or rewritten history.

## Acceptance criteria

- [ ] Git checkpoints freeze topic, purpose, exact document bytes, decision digests, path set, base reference and idempotency identity before a documentation-only commit.
- [ ] Checkpoint commits carry the approved machine trailers and are adopted after an uncertain outcome only when parent, tree, paths, blobs and digests uniquely match.
- [ ] A changed draft supersedes or cancels an uncommitted checkpoint intent without reusing its identity; an unresolved older uncertain checkpoint must be reconciled first.
- [ ] Non-Git projects create or reuse immutable content-addressed snapshots and refuse to overwrite a mismatching existing object.
- [ ] Checkpoint repair preserves the original broken fact and only maps to a unique fully verified replacement; active implementation sources require a fresh acknowledgement.
- [ ] Checkpoint GC is dry-run first, binds confirmation to an exact candidate list and ledger revision, and never deletes referenced or uncertain snapshots.
- [ ] Tests inject failure around prepared ledger state, commit/snapshot creation, result recording, rewritten history, repair and GC while preserving unrelated workspace content.

Blocked by: 02 — Evolve topic documents safely.

## Comments
