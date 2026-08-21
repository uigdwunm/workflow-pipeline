---
name: design-discussion
description: Use when the user explicitly invokes $design-discussion, says 0讨论, or clearly asks to begin a sustained design discussion whose state must persist across conversations. Initialize and verify one durable root topic before asking the first substantive question, then guide the discussion one question at a time. Do not initialize for ordinary design consultation, incidental wording, or exploratory questions that do not explicitly request persistence.
---

# 0讨论

Maintain a durable, document-driven design discussion while keeping ordinary
consultation stateless.

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
idempotent replay is safe. Any error, conflict, incomplete response, invalid
path or failed reread stops persistent discussion; do not claim that 0讨论 has
started and do not ask a substantive design question.

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

For a confirmed substantive update, call `prepare-topic-update` with the
current ledger and topic revisions, the authenticated topic binding, a new
UUIDv4 idempotency key and exactly one typed mutation. Supported Ticket 02
mutations are `confirm-decision`, `set-active-question`, `insert-idea`,
`resolve-inserted-idea`, `change-direction` and `resolve-impact`. Reuse the
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
the authoritative ledger directly. Topic-document writes use the shared
document lease with stage `design-discussion` and purpose `document-write`.
The update sequence is:

1. `prepare-topic-update` creates one immutable `DW-*` payload with before and
   after SHA-256 digests and enters `confirmed-but-pending`.
2. Acquire the document lease from `supervision_protocol.py`. Pass its exact
   path, lease ID and version to `apply-document-write`; continue only when the
   protocol reports byte verification and `release_allowed: true`.
3. Release that exact lease through `supervision_protocol.py`, then pass its
   release path, lease ID and new version to `complete-document-write`.
4. Call `validate` or `read-topic` before continuing substantive discussion.

If an apply or release result is uncertain, call `reconcile-document-write`
with the same `DW-*` and current revisions. It compares the immutable payload,
the current document bytes and the authoritative supervision lease state. It
may keep the checkpoint pending, adopt verified applied bytes, or complete a
verified release; conflicting facts stop reconciliation.

Git projects store the shared document lease under `.git`; non-Git projects
use the supervision-owned lease under the project's `.codex` coordination
directory. Callers always use the exact path returned by the supervision CLI.

While any `DW-*` is not `completed`, do not prepare another substantive
update. Lease timeout, stale credentials, outcome uncertainty, missing or
damaged payloads, or release verification failure leave a recoverable
confirmed-but-pending checkpoint; reconcile it before asking the next design
question. Never edit or delete the payload to force recovery.

## Publish a verifiable checkpoint

Before pausing, handing off, splitting a topic, or entering a later stage,
read [references/checkpoint-protocol.md](references/checkpoint-protocol.md) and
publish one verified `CP-*`. `prepare-checkpoint` freezes the purpose, exact
topic-document bytes and SHA-256, decision digest, sorted path set, base ref,
resolved base commit for Git, and a new immutable identity. Do not reuse a
cancelled or superseded identity.

For Git projects, acquire the supervision-owned short repository coordination
lease with stage `design-discussion` and purpose `checkpoint-publish`, pass its
exact credential to `publish-git-checkpoint`, verify the returned commit, then
release the lease. This lease is separate from the document lease and grants
only the bounded checkpoint publication. For non-Git projects,
`publish-non-git-checkpoint` creates or reuses an immutable content-addressed
snapshot.

An uncertain Git result must become `outcome-unknown` and be reconciled before
any new checkpoint is prepared. A changed draft requires `cancel-checkpoint`
and a fresh `CP-*`. Repair records the broken identity permanently and maps it
only to one fully verified replacement; an active implementation source also
requires a fresh acknowledgement. Snapshot GC always begins with
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

## Judge maturity

Judge maturity from goal, scope, user scenarios, behavior, constraints,
exceptions, unresolved questions and next-stage inputs. Never infer maturity
from turn count or document length. Offer a later lifecycle route only when the
relevant protocol operation exists and its prerequisites are verified.
