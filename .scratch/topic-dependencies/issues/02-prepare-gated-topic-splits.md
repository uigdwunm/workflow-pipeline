# 02 — Prepare gated topics from proactive split proposals

Status: `completed`
Lifecycle: completed

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

- [x] Phase-0 and Phase-1 wrapper contracts recommend a split only when all four confirmed independence criteria hold and display the complete fixed proposal before preparation.
- [x] Accepting the proposal authorizes only exact discussion-state preparation; the existing task-creation confirmation remains separate and names one task.
- [x] Attached Phase-0/1 flows create a same-tree Phase-0 child, while standalone Phase 1 proposes a new root without an executable cross-tree dependency.
- [x] A dedicated grilling carrier can return a bounded proposal but cannot prepare, create, bind, change, release, or cancel the dependency itself.
- [x] Child handoff preparation resolves source/target endpoint references internally and commits the topic, parent relation, handoff attempt, and all initial closed dependencies in one ledger transaction.
- [x] Failure injection proves the transaction creates all records or none, and handoff failure/outcome-unknown/retry/late-bind paths reuse the same dependencies without duplication.
- [x] Parent and continuation topology alone does not change Phase Run coverage evidence.

## Closure evidence

- Accepted candidate: `7aab54330383ea8a7ff90815015f455f4efd4372`.
- Wrapper, handoff, recovery, atomicity, and coverage contracts passed the
  focused 108-test run and the 278-test full validation.
