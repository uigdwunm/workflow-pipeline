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

## Coordinate writes and later actions

All durable discussion state belongs to `discussion_protocol.py`; do not patch
the authoritative ledger directly. Topic-document writes use the shared
document lease with stage `design-discussion`. Ticket 01 establishes bootstrap
only; do not invent update operations that the protocol does not expose.

Read the applicable reference only when the action is requested:

- [references/child-topic-protocol.md](references/child-topic-protocol.md) for
  child topics or continuation;
- [references/lifecycle-integration.md](references/lifecycle-integration.md)
  for routing into stages 1–4;
- [references/templates.md](references/templates.md) for stable topic and
  interaction formats.

If the required deterministic operation is unavailable, stop and state that
the discussion remains at its last verified durable state.

## Judge maturity

Judge maturity from goal, scope, user scenarios, behavior, constraints,
exceptions, unresolved questions and next-stage inputs. Never infer maturity
from turn count or document length. Offer a later lifecycle route only when the
relevant protocol operation exists and its prerequisites are verified.
