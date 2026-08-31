# Spec: Repair the workflow E2E contracts

Status: `ready-for-agent`

## Problem Statement

Two workflow contracts are individually plausible but fail when exercised through real conversations.

First, `0讨论` asks one active question and records an explicit answer through the `confirm-decision` mutation, but that mutation currently creates only a Decision. It does not consume the Question that the Decision answers. The topic therefore retains an active Question, the rendered document continues to show it under Pending Questions, and the next `set-active-question` fails with `active_question_conflict`. The protocol records the decision but cannot complete the ordinary ask → confirm → ask-next loop.

Second, Stage 3 currently tells the Dedicated Implementation Task to run the Standards and Spec review axes. Those reviews require the authority to dispatch independent review agents, while native sub-agent governance is Session-local and is not inherited or delegated to a child task. The responsibility is therefore assigned to an actor that cannot reliably perform it. The Originating Task already owns supervision, acceptance, and integration, so it must also own review orchestration.

These gaps must be repaired without adding a separate question-closing operation, migrating old test topics, changing the governance plugin, or broadening the implementation workflow.

## Solution

Deepen the existing `confirm-decision` mutation into the complete domain action “confirm a Decision and answer the unique active Question, when one exists.” In the same prepared topic update, the protocol creates the Decision and, if exactly one Question is active, changes that Question to `answered` and records `answered_by_decision_id` with the new Decision identity. With no active Question, the mutation remains a valid independent Decision. More than one active Question is corrupt authority state: the mutation returns `state_corrupt` before creating a Decision or pending document write. Pending Questions renders only Questions whose state is `active` or `suspended`; answered Questions remain in ledger/read results as durable history but disappear from that user-facing section.

Move Standards/Spec review orchestration to the Originating Task. The Dedicated Implementation Task verifies its Worktree Binding, implements in Ticket order with TDD, runs focused and full validation, commits a clean candidate, and reports the candidate and evidence. The Originating Task pins that candidate, runs the two native review axes independently, and sends every actionable finding back to the same Dedicated Implementation Task and worktree. Any new candidate commit invalidates both prior review results, so the Originating Task reruns both axes against the new candidate. It accepts and integrates only a candidate for which both review axes correspond to that exact commit and have no unresolved actionable findings.

The implementation is accepted only after two fresh end-to-end exercises: a real `0讨论` conversation proving ask → confirm → durable answer → next question, and a separate standalone `1拷问 → 2方案 → 3实现 → 4归档` run proving that the Originating Task owns review dispatch and remediation. Both exercises use newly created test subjects; existing `docs/discussions/` test topics are neither migrated nor cleaned up.

## User Stories

1. As a `0讨论` participant, I want confirming the recommendation for the current Question to answer that Question durably, so that the discussion can advance.
2. As a `0讨论` participant, I want the next Question to be set after my confirmation, so that the one-question loop does not deadlock.
3. As a `0讨论` participant, I want answered Questions removed from Pending Questions, so that the document shows only work that is still pending.
4. As a `0讨论` participant, I want a suspended Question to remain visible, so that an inserted idea does not hide unfinished discussion work.
5. As a protocol caller, I want `confirm-decision` to remain valid when no Question is active, so that independent Decisions are still supported.
6. As a protocol caller, I want multiple active Questions to fail as `state_corrupt` without a partial Decision or document write, so that corrupted authority is not made worse.
7. As a protocol caller, I want the answered Question to reference the Decision that answered it, so that the durable reasoning chain can be inspected.
8. As a recovery caller, I want the deeper mutation to retain the existing immutable payload, idempotent replay, and atomic apply behavior, so that uncertain outcomes remain recoverable.
9. As an Originating Task, I want a Dedicated Implementation Task to return a clean, validated candidate, so that I can review a fixed Git object.
10. As an Originating Task, I want to dispatch Standards and Spec reviews myself, so that review uses authority available in my Session.
11. As an Originating Task, I want review results bound to an exact candidate commit, so that later fixes cannot reuse stale approval.
12. As an Originating Task, I want actionable findings repaired in the same Dedicated Implementation Task and worktree, so that implementation ownership and Git state remain stable.
13. As an Originating Task, I want both review axes rerun whenever the candidate changes, so that the accepted evidence describes the integrated code.
14. As a Dedicated Implementation Task, I want to focus on implementation, TDD, validation, candidate commits, and remediation, so that I do not depend on governance authority I cannot inherit.
15. As a workflow user, I want a fresh discussion-level E2E to prove the repaired Question lifecycle, so that unit-level success does not mask conversation failure.
16. As a workflow user, I want a separate standalone 1→2→3→4 E2E to prove review ownership, remediation, integration, and closure, so that Stage boundaries work together.
17. As a repository maintainer, I want old test topics left unchanged and new tests to use new subjects, so that compatibility is proved without hidden migration.

## Implementation Decisions

### 1. `confirm-decision` owns the answer transition

- Keep the mutation name and request shape unchanged. Do not add `answer-question`, `close-question`, or another caller-visible operation.
- Derive active Questions from the topic snapshot before appending any new record. Exactly zero, one, and more than one active Questions are distinct domain cases.
- With zero active Questions, create the confirmed Decision exactly as today and do not alter any Question.
- With one active Question, create the Decision and update that Question in the same ledger transaction and immutable document-write payload. Set `state` to `answered` and `answered_by_decision_id` to the newly created Decision identity.
- With more than one active Question, return `state_corrupt` before publishing a payload, appending a Decision, incrementing the revision, or recording an event.
- Suspended Questions do not count as active and are not answered implicitly. They retain their existing inserted-idea resolution path.
- Exact idempotent replay returns the original mutation result and must not answer another Question that became active later.

### 2. Question history and rendering remain separate concerns

- Keep answered Question records in the ledger and in `read-topic` Question history. This is a state transition, not deletion.
- Render Pending Questions from `active` and `suspended` Questions only. Omit both `answered` and `invalidated` Questions from that section.
- The read model continues to expose `active_question_count` and the single `active_question`; after a Decision answers the only active Question, those values are `0` and `null`.
- Existing Question records require no migration. The new link is required only for Questions transitioned to `answered` by the new behavior.

### 3. Preserve the existing topic-write transaction

- Decision creation, Question transition, rendered document bytes, one topic revision, one ledger revision/event, and one pending document write are produced by the existing `prepare-topic-update` boundary.
- Existing apply, retry, digest, ownership, and outcome-uncertainty contracts remain unchanged.
- The caller still applies the returned document write and then calls `read-topic` before continuing the conversation.

### 4. The Originating Task owns both review axes

- Treat the complete Stage-3 native workflow as split across its established actors: the Dedicated Implementation Task owns implementation/TDD/validation/candidate commits; the Originating Task owns the final native `$code-review` orchestration, candidate acceptance, and integration.
- The Dedicated Implementation Task must not dispatch Standards or Spec review agents and must not receive the parent Session or CLI as a workaround.
- The Dedicated Implementation Task reports the exact candidate commit, changed paths, focused and full checks, and remaining risks before review.
- The Originating Task first independently verifies the candidate and diff boundary, then invokes Standards and Spec review against the same fixed point and candidate. The axes remain independent and are reported separately.
- Every actionable review finding returns to the same Dedicated Implementation Task and verified worktree. That task fixes through TDD as appropriate, reruns affected and full checks, and commits a replacement candidate.
- Review evidence is keyed to the candidate commit. Any candidate change invalidates both axes, even if a change appears relevant to only one finding; the Originating Task reruns both.
- Only the Originating Task may declare the candidate accepted and call worktree completion/integration.

### 5. Align Stage-3 instructions and executable contracts

- Update the Stage-3 Skill and its role-specific references so that no instruction still assigns `$code-review` dispatch to the Dedicated Implementation Task.
- Preserve Ticket topological order, Worktree Binding verification, read-only planning sources, implementation-only commits, remote-authority boundaries, target-advance recovery, and Stage-4 handoff fields.
- Update repository-level static contract tests to assert the new role split, exact-candidate review binding, same-task remediation, and mandatory rerun after a candidate change.
- Do not modify `subagent-governance`, native reviewer prompts, or the Standards/Spec definitions.

### 6. Delivery order

- Ticket 01 repairs the discussion domain action and proves its automated and live conversation behavior.
- Ticket 02 is blocked by Ticket 01, repairs Stage-3 orchestration, and runs the final separate standalone 1→2→3→4 acceptance on a repository state that already contains the discussion fix. This makes the final E2E gate cover the complete repaired workflow baseline.

## Testing Decisions

### Primary automated seam: discussion protocol CLI

- Use the existing process-level `discussion_protocol.py` seam with temporary Git and non-Git projects. Tests send typed JSON through stdin and assert JSON responses, durable ledger/read state, topic document bytes, revisions, events, and pending writes rather than calling internal mutation helpers.
- Add one vertical regression using a newly bootstrapped topic: set an active Question → apply its write → confirm a Decision → apply its write → read the topic and observe Decision linkage, Question `answered`, `answered_by_decision_id`, `active_question_count: 0`, and no answered Question under Pending Questions → set and apply the next Question successfully.
- Cover the zero-active case as an independent Decision with no Question transition.
- Cover a suspended Question to prove it remains suspended and rendered when an independent Decision is confirmed.
- Cover a deliberately corrupted fixture with multiple active Questions: `confirm-decision` returns `state_corrupt`, and ledger revision, event count, document bytes, Decisions, and pending-write set remain unchanged.
- Preserve existing idempotency and outcome-uncertainty tests, adding assertions that replay of the same prepared confirmation keeps the original Question-to-Decision link and does not consume a later Question.

### Secondary automated seam: Stage-3 contract validation

- Use repository-level static validation for the generated-workflow instructions. Assert that the Dedicated Implementation Task is assigned implementation, TDD, focused/full checks, candidate commits, and remediation, but not review-agent dispatch.
- Assert that the Originating Task dispatches both Standards and Spec axes against one exact candidate, returns findings to the same Dedicated Implementation Task, invalidates prior evidence when the candidate changes, reruns both axes, and alone accepts/integrates.
- Retain the existing Worktree Binding, dependency ordering, Stage-4 handoff, and remote-authority contract tests.

### Conversation-level E2E acceptance

- Create a brand-new persistent `0讨论` subject. Let the workflow set exactly one active Question, answer it through a real explicit confirmation, complete the returned document write, and continue until the workflow sets a different next Question. Verify by `read-topic` and the rendered topic document that the first Question is `answered`, links to the Decision, is absent from Pending Questions, and active count reached zero before the next Question was set.
- Do not reuse, edit, migrate, or delete any pre-existing `docs/discussions/` subject. The E2E subject has a new identity and slug.

### Standalone 1→2→3→4 E2E acceptance

- In a separate fresh test effort with no Discussion Topic attachment, execute `1拷问`, publish `2方案`, enter `3实现`, and finish `4归档`.
- Verify the Dedicated Implementation Task produces the candidate without dispatching review agents; the Originating Task dispatches Standards and Spec reviews; at least one controlled actionable finding is returned to the same Dedicated Implementation Task and produces a new candidate; both axes are rerun against that new commit; the Originating Task accepts and integrates it; Stage 4 consumes the verified handoff and closes the effort.
- Record the fixed point, every candidate commit reviewed, both axis results, remediation task identity, merge commit, focused/full checks, and Stage-4 outcome as acceptance evidence.

## Out of Scope

- Adding a separate operation to answer, close, or consume a Question.
- Answering suspended Questions implicitly or changing inserted-idea resolution semantics.
- Deleting answered Questions or rewriting historical Decision/Question records.
- Migrating, normalizing, or cleaning existing test topics or other `docs/discussions/` content.
- Modifying `subagent-governance`, transferring a parent Session/CLI to a child task, or making governance authority inheritable.
- Changing the Standards or Spec review axes, their prompts, or their independent reporting semantics.
- Changing worktree creation/integration, discussion Phase Run authority, tracker behavior, remote permissions, PRs, deployment, or release behavior.
- Implementing code or tests during this 2方案 stage.

## Further Notes

- No new ADR is required. The Question transition deepens an existing domain operation, and the review-role change follows the existing `Originating Task` responsibility for supervision, acceptance, and integration. Neither changes the accepted protocol-authority or isolated-worktree decisions in ADR-0001 and ADR-0002.
- `CONTEXT.md` already defines `Originating Task`, `Dedicated Implementation Task`, and `Worktree Binding`; this Spec uses those terms without introducing a competing vocabulary.
