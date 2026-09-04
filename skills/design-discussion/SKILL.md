---
name: design-discussion
description: Use when the user explicitly invokes $design-discussion, says 0讨论, or clearly asks to begin a sustained design discussion whose state must persist across conversations. Initialize and verify one durable root topic before asking the first substantive question, then guide the discussion one question at a time. Do not initialize for ordinary design consultation, incidental wording, or exploratory questions that do not explicitly request persistence.
---

# 0讨论

Maintain a durable, document-driven design discussion while keeping ordinary
consultation stateless.

Read the shared
[`requirement-document-contract.md`](references/requirement-document-contract.md)
before creating or changing the requirement document, and use
[`confirmation-contract.md`](references/confirmation-contract.md) whenever a
workflow action needs user confirmation.

## Select the mode

Enter persistent mode only for one of these explicit signals:

- the user invokes `$design-discussion`;
- the user names the interface `0讨论`; or
- the user clearly asks to begin a sustained design discussion that must persist
  across conversations.

Treat ordinary consultation, incidental mentions of discussion, and requests
for a quick opinion as stateless. In stateless mode, do not call the protocol,
create a discussion document, create ledger state, or bind the conversation.

## Bootstrap before discussion

For persistent mode, resolve the canonical absolute project directory and one
stable root slug. Invoke
`scripts/discussion_protocol.py` through its stdin/stdout JSON interface with:

```json
{
  "protocol_version": 1,
  "operation": "bootstrap",
  "project_path": "<canonical absolute project directory>",
  "entry_mode": "explicit-skill | explicit-interface-name | explicit-sustained-design",
  "conversation_ref": "<current authenticated conversation reference>",
  "idempotency_key": "<new UUIDv4 for this invocation>",
  "root_slug": "<lowercase-hyphenated slug>"
}
```

Read exactly one JSON response from stdout. Continue only when it reports
`ok: true`, `state: started`, and `reread_verified: true`, with non-empty
project, tree, topic, ledger, binding and topic-document identities. Exact
idempotent replay proves only that this invocation created the same discussion
tree; it does not prove that the topic is still open in phase 0. When the
response has `idempotent_replay: true`, call `read-topic` with the returned
identities and current authenticated conversation before substantive
discussion. Continue the phase-0 loop only when that read reports
`current_phase: 0`, `phase_state: active`, and `topic_state: open`. Replay must
not rebind, reopen or otherwise mutate the topic. Any error, conflict,
incomplete response, invalid path or failed reread stops persistent discussion;
do not claim that 0讨论 has started and do not ask a substantive design
question.

Read [references/ledger-protocol.md](references/ledger-protocol.md) when
validating bootstrap state or interpreting protocol errors. Read
[references/topic-document-protocol.md](references/topic-document-protocol.md)
before changing the shared topic document.

## Run the one-question loop

After verified bootstrap:

1. Acknowledge the current context briefly.
2. Explain why one unresolved question matters.
3. Give one recommendation and its reason before asking.
4. Ask exactly one substantive question.
5. Treat preference, exploration, silence and lack of objection as unconfirmed.
6. Record a confirmed decision only after explicit confirmation.

If the user introduces a new idea, acknowledge it and suspend the current
question. Do not silently preserve, replace or discard an earlier decision.
For detailed document sections and confirmation boundaries, read
[references/topic-document-protocol.md](references/topic-document-protocol.md).

For a substantive document update, call `prepare-topic-update` with the
current ledger and topic revisions, the authenticated topic binding, a new
UUIDv4 idempotency key and exactly one typed mutation. Supported Ticket 02
mutations are `confirm-decision`, `set-active-question`, `insert-idea`,
`resolve-inserted-idea`, `change-direction`, `resolve-impact`, and
`refresh-requirement-narrative`. Use the refresh mutation after every material
answer or conclusion so the goal, scope, scenarios, facts, constraints, and
acceptance conditions remain a coherent current-state snapshot. Supply at most
one current direction-change note when it prevents likely misunderstanding.
Reuse the
same idempotency key only to replay the exact same request. Stable decision,
question, idea, impact and document-write identities come from the protocol;
do not construct or rewrite them in prose.

An inserted idea suspends the active question. After the idea is understood,
explicitly resolve that question as `resume`, `adjust` or `invalidate`. A
changed direction first lists affected decision identities, then confirms one
`keep`, `adjust`, `replace` or `discard` action per impact. Never batch several
affected decisions into one resolution.

## Coordinate writes and later actions

All durable discussion state belongs to `discussion_protocol.py`; do not patch
the authoritative ledger directly. The update sequence is:

1. `prepare-topic-update` creates one immutable `DW-*` payload with before and
   after SHA-256 digests and enters `confirmed-but-pending`.
2. `apply-document-write` compares the current bytes with the immutable
   payload, atomically writes the document, verifies it, and marks the `DW-*`
   completed while holding the discussion lock.
3. Call `read-topic` before continuing substantive discussion.

If an apply result is uncertain, retry the exact `apply-document-write`
request. It accepts either the recorded before digest or the exact payload
digest, so it can finish the ledger record without rewriting already-applied
bytes. Any third digest is a conflict.

While any `DW-*` is not `completed`, do not prepare another substantive
update. Outcome uncertainty or a missing or damaged payload leaves a
confirmed-but-pending checkpoint; retry the exact apply before asking the next
design question. Never edit or delete the payload to force recovery.

## Publish a verifiable checkpoint

Before pausing, handing off, splitting a topic, or entering a later stage,
read [references/checkpoint-protocol.md](references/checkpoint-protocol.md) and
publish one verified `CP-*`. `prepare-checkpoint` freezes the purpose, exact
topic-document bytes and SHA-256, decision digest, sorted path set, base ref,
resolved base commit for Git, and a new immutable identity. Do not reuse a
cancelled or superseded identity.

For Git projects, call `publish-git-checkpoint`; it performs its private-index
and dedicated-ref update while holding the discussion lock, without changing
the ordinary checkout. Verify the returned commit. For non-Git projects,
`publish-non-git-checkpoint` creates or reuses an immutable content-addressed
snapshot.

An uncertain Git result must become `outcome-unknown` and be reconciled before
any new checkpoint is prepared. A changed draft requires `cancel-checkpoint`
and a fresh `CP-*`. Repair records the broken identity permanently and maps it
only to one fully verified replacement. Snapshot GC always begins with
`checkpoint-gc-dry-run`; confirmation must bind the exact candidate array and
ledger revision returned by that dry run.

Read the applicable reference only when the action is requested:

- [references/child-topic-protocol.md](references/child-topic-protocol.md) for
  child topics or continuation;
- [references/lifecycle-integration.md](references/lifecycle-integration.md)
  for routing into stages 1–4;
- [references/templates.md](references/templates.md) for stable topic and
  interaction formats.

If the required deterministic operation is unavailable, stop and state that
the discussion remains at its last verified durable state.

For a child topic or continuation, the referenced protocol is executable now:
complete the parent confirmation and checkpoint, prepare the handoff, obtain the
separate exact task-creation confirmation, create and bind one real Codex task,
enforce first-turn acceptance and later-turn authorization, then route the
scoped result or impacts back to the parent. Do not replace this sequence with
bootstrap or an informal prompt-only handoff.

## Proactive topic split and requirements gates

Recommend a separate topic only when the new content has an independent,
nameable goal, its own scope or acceptance outcome, would materially interrupt
the active discussion, and can be removed without making the current topic
incomplete. Show one complete `新话题建议` before preparing state: current and
new goals, tree ownership, parent relation, Phase-0 entry, any exact dependency
direction and semantic result, current-topic handling, result return, and the
separate task-creation confirmation. A confirmed proposal prepares only the
ledger; it never creates a task.

At the start of a later user-requested Phase-0/1 turn, call
`evaluate-topic-gate`. Stop with its waiting details when blocked. When it is
releasable, show the exact selected bases and call `release-topic-gate` only
after a fresh user confirmation; then continue. Do not poll, notify, wake, or
reevaluate an already-open gate. Phase 2 and later do not consult Topic
Dependencies.

## Judge maturity

Judge maturity from goal, scope, user scenarios, behavior, constraints,
exceptions, unresolved questions and next-stage inputs. Never infer maturity
from turn count or document length. Offer a later lifecycle route only when the
relevant protocol operation exists and its prerequisites are verified.
