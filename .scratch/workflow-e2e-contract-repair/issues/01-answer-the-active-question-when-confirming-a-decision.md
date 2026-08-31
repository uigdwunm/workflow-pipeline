# 01 — Answer the active Question when confirming a Decision

Status: `completed`
Lifecycle: completed

## What to build

Make one explicit Decision confirmation complete the current `0讨论` turn: it must atomically preserve the Decision and answer the unique active Question, keep independent Decisions valid when no Question is active, reject corrupt multi-active state without side effects, render only unfinished Questions as pending, and let the workflow set the next Question.

## Acceptance criteria

- [x] With exactly one active Question, `confirm-decision` creates one confirmed Decision and changes that Question to `answered` with `answered_by_decision_id` equal to the new Decision identity in the same prepared topic update.
- [x] With no active Question, `confirm-decision` still creates an independent Decision and does not change suspended, answered, or invalidated Questions.
- [x] With more than one active Question, `confirm-decision` returns `state_corrupt` before creating a Decision or pending write and leaves revisions, events, document bytes, and durable records unchanged.
- [x] Pending Questions renders only `active` and `suspended` Questions; answered Questions remain available through durable topic history and `read-topic`.
- [x] Exact prepare/apply replay preserves the original Question-to-Decision link and cannot consume a Question that became active later.
- [x] A process-level CLI regression on a newly bootstrapped topic completes set Question → apply → confirm Decision → apply → read with active count zero → set and apply the next Question.
- [x] Focused tests cover Git and non-Git topics, zero/one/multiple active Questions, suspended rendering, idempotent replay, and outcome-uncertainty recovery; the full repository validation passes.
- [x] A brand-new real `0讨论` subject completes explicit confirmation and advances to a different next Question; readback and the rendered document prove the first Question is answered, linked, and absent from Pending Questions before the next Question is set.
- [x] No existing `docs/discussions/` test subject is reused, migrated, edited, or deleted.

Blocked by: None — can start immediately.

## Closure evidence

- Accepted candidate: `9bb676aa47bd3d6b3dd0715e7f850dfcc3c3294d`; implementation merge: `afbc1a42392f00fc50e4d2ade72b5b6028cb03b7`.
- Focused tests passed 4 cases; protocol/repository suites passed 115 tests; post-integration validation passed all 148 tests.
- Fresh topic `topic-f2b6f73fb5834837a45ebc8fe55ace63` answered Question `Q-6e16b3fcd5ab4620aa4243049a922bfc` through Decision `D-6644f132e37843a18ee777a5350a4e38`, reached zero active Questions, and then accepted next Question `Q-f52687b46f6848d9bb4224350514198d`.
- Final topic revision was 4, ledger revision was 11, there were no pending document writes, and no pre-existing `docs/discussions/` subject was changed.
