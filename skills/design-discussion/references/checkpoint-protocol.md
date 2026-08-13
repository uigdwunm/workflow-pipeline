# Discussion Checkpoint Protocol

Read this reference before pause, handoff, child-topic split, stage entry,
checkpoint recovery, repair, or snapshot GC.

## Freeze the intent

Call `prepare-checkpoint` only after every `DW-*` is completed. Provide one of
`pause`, `handoff`, `split`, `stage-entry`, or `implementation-source`, the
current revisions, a new UUIDv4, and the exact base ref. The returned `CP-*`
freezes the current topic-document bytes and digest, confirmed-decision digest,
sorted path set and digest, storage kind, and resolved Git base commit.

Only one checkpoint intent may be active. If draft bytes change while the
intent is still `prepared`, call `cancel-checkpoint`, preserve that identity as
cancelled, and prepare a new identity. If the older state is
`outcome-unknown`, reconcile it first.

## Publish Git checkpoints

Acquire `acquire-repository-coordination-lease` from
`supervision_protocol.py` using stage `design-discussion`, purpose
`checkpoint-publish`, the authenticated conversation/task owner, and a short
TTL. Pass the exact path, lease ID, and version to `publish-git-checkpoint`.
The protocol creates a documentation-only commit through a private temporary
index, without changing the caller's index, `HEAD`, or refs. The verified
commit identity is recorded durably in the discussion ledger and pinned by a
checkpoint ref below `refs/codex/design-discussion/checkpoints/`.

The commit must have the frozen parent, exact resulting tree, exact paths and
blobs, and these trailers:

```text
Codex-Discussion-Checkpoint: <CP-*>
Codex-Document-SHA256: <sha256>
Codex-Discussion-Decision-SHA256: <sha256>
Codex-Discussion-Paths-SHA256: <sha256>
```

When commit creation might have succeeded but result recording is uncertain,
record `outcome-unknown`, then call `reconcile-git-checkpoint`. Reconciliation
enumerates Git objects and adopts only one commit whose parent, tree, path set,
blobs, document bytes and all trailers fully match. Zero matches returns the
same intent to `prepared` so publication can be retried. Multiple matches stop
with an ambiguity error. Never infer adoption from a trailer or digest alone.

## Publish non-Git checkpoints

`publish-non-git-checkpoint` writes canonical snapshot bytes below the
coordination root at `checkpoints/sha256/<prefix>/<digest>`. An existing object
is reused only when its bytes match exactly; a mismatching content-addressed
object is corruption and must not be overwritten.

If snapshot creation may have succeeded before result recording,
`record-checkpoint-outcome-unknown` freezes GC and
`reconcile-non-git-checkpoint` recomputes the exact expected content address.
It adopts only matching bytes, or returns the same identity to `prepared` when
the object is absent.

## Repair and GC

Use `mark-checkpoint-broken` to preserve the original missing or invalid
identity and reason. `repair-checkpoint` accepts only a unique fully verified
replacement under an independently resolved `replacement_base_ref`; the
candidate cannot choose its own verification lineage. If
`register-active-checkpoint-source` shows an active
implementation, the repair request must include a fresh acknowledgement bound
to the checkpoint and original broken identity.

For non-Git snapshot cleanup, call `checkpoint-gc-dry-run` first. Pass its
exact candidate objects, candidate digest, and ledger revision to
`checkpoint-gc-confirm`. Any revision or list drift stops deletion. Referenced,
uncertain, or broken snapshots are never candidates; every candidate digest is
reverified before deletion.
