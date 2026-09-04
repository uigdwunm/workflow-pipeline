# 02 — Prepare gated topics from proactive split proposals

Status: `ready-for-agent`

## What to build

Give `0讨论` and `1拷问` one stable proactive-split interaction and let an
accepted child handoff atomically create its structural topic state and initial
requirements dependencies before the separately confirmed Codex task is
created.

## Blocked by

01 — Manage topic-owned requirements dependencies.

## Spec trace

- Implementation Decisions: Stable proactive split decision;
  `prepare-handoff` extension; owning modules and interfaces.
- Acceptance scenarios: 1, 2, 3, 8, 10, 12, 13, 16, 19.

## Acceptance criteria

- [ ] Phase-0 and Phase-1 wrapper contracts recommend a split only when all four confirmed independence criteria hold and display the complete fixed proposal before preparation.
- [ ] Accepting the proposal authorizes only exact discussion-state preparation; the existing task-creation confirmation remains separate and names one task.
- [ ] Attached Phase-0/1 flows create a same-tree Phase-0 child, while standalone Phase 1 proposes a new root without an executable cross-tree dependency.
- [ ] A dedicated grilling carrier can return a bounded proposal but cannot prepare, create, bind, change, release, or cancel the dependency itself.
- [ ] Child handoff preparation resolves source/target endpoint references internally and commits the topic, parent relation, handoff attempt, and all initial closed dependencies in one ledger transaction.
- [ ] Failure injection proves the transaction creates all records or none, and handoff failure/outcome-unknown/retry/late-bind paths reuse the same dependencies without duplication.
- [ ] Parent and continuation topology alone does not change Phase Run coverage evidence.
