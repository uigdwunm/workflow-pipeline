# 01 — Manage topic-owned requirements dependencies

Status: `ready-for-agent`

## What to build

Let a dependent Discussion Topic create, replace, cancel, persist, and inspect a
same-tree Phase-0/1 prerequisite through the discussion protocol. Existing
ledgers must keep working and upgrade without a separate migration command.

## Blocked by

None — can start immediately.

## Spec trace

- Implementation Decisions: Ledger schema and migration; owning modules and
  interfaces; `update-topic-dependency`; validation and errors.
- Acceptance scenarios: 8, 9, 17.

## Acceptance criteria

- [ ] New ledgers contain the dedicated Topic Dependencies section and older schema-version-1/2 ledgers remain readable, upgrading to version 3 only on the next successful mutation.
- [ ] The dependent topic owner can create, replace, and cancel a dependency in Discussion Phase 0 or 1 with optimistic revisions and exact idempotent replay.
- [ ] Reads expose the derived open/closed gate and current dependency summaries without persisting a duplicate topic-level state.
- [ ] Cross-tree edges, self-edges, duplicate active endpoint pairs, unsupported requirement kinds, stale revisions, unauthorized actors, and post-Phase-1 changes fail without mutation.
- [ ] Whole-tree active-edge cycle detection rejects both direct and multi-hop cycles before any record is committed.
- [ ] CLI tests cover record corruption, canonical embedded authority JSON, graph invariants, migration, and reread durability at the public JSON seam.
