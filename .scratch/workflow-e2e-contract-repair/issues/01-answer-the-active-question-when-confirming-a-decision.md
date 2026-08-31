# 01 — Answer the active Question when confirming a Decision

Status: `ready-for-agent`

## What to build

Make one explicit Decision confirmation complete the current `0讨论` turn: it must atomically preserve the Decision and answer the unique active Question, keep independent Decisions valid when no Question is active, reject corrupt multi-active state without side effects, render only unfinished Questions as pending, and let the workflow set the next Question.

## Acceptance criteria

- [ ] With exactly one active Question, `confirm-decision` creates one confirmed Decision and changes that Question to `answered` with `answered_by_decision_id` equal to the new Decision identity in the same prepared topic update.
- [ ] With no active Question, `confirm-decision` still creates an independent Decision and does not change suspended, answered, or invalidated Questions.
- [ ] With more than one active Question, `confirm-decision` returns `state_corrupt` before creating a Decision or pending write and leaves revisions, events, document bytes, and durable records unchanged.
- [ ] Pending Questions renders only `active` and `suspended` Questions; answered Questions remain available through durable topic history and `read-topic`.
- [ ] Exact prepare/apply replay preserves the original Question-to-Decision link and cannot consume a Question that became active later.
- [ ] A process-level CLI regression on a newly bootstrapped topic completes set Question → apply → confirm Decision → apply → read with active count zero → set and apply the next Question.
- [ ] Focused tests cover Git and non-Git topics, zero/one/multiple active Questions, suspended rendering, idempotent replay, and outcome-uncertainty recovery; the full repository validation passes.
- [ ] A brand-new real `0讨论` subject completes explicit confirmation and advances to a different next Question; readback and the rendered document prove the first Question is answered, linked, and absent from Pending Questions before the next Question is set.
- [ ] No existing `docs/discussions/` test subject is reused, migrated, edited, or deleted.

Blocked by: None — can start immediately.
