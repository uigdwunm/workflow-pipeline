# Topic Document Protocol

Read this reference before creating a document-write payload or interpreting a
topic document.

## Authority

One topic owns one current design document under
`docs/discussions/<root-slug>/topic.md`. The document carries `project_id`,
`tree_id`, `topic_id`, the parent-topic snapshot and its own revision. The
ledger owns coordination state; the document owns the human-readable current
design.

It also follows the shared
[`requirement-document-contract.md`](requirement-document-contract.md). The
ledger retains recovery history; the document presents current truth.

## Required sections

Keep these sections distinct:

- Goal, Background and Current State, Scope, Non-goals, and Users and Key
  Scenarios: the current requirement narrative maintained throughout Phases 0
  and 1.
- Confirmed Decisions: only explicitly confirmed choices with stable decision
  identities.
- Candidate Solution: proposals still open to change.
- Tentative Assumptions: beliefs requiring confirmation or evidence.
- Facts: verified constraints and observations.
- Constraints and Acceptance Conditions: the current design boundary and
  externally testable outcomes.
- Pending Questions: unresolved questions, exposing at most one to the user in
  an ordinary turn.
- Decision Evolution: at most one current concise direction-change note when it
  prevents likely misunderstanding. Omit ordinary
  confirmations, resolved impact mechanics, and obsolete downstream detail.

Never promote preference, silence, exploration or lack of objection into
Confirmed Decisions. When a broad direction changes, confirm the new direction
first; later protocol operations must register and review each affected
decision individually. Rewrite the current sections after those resolutions so
superseded requirements, scenarios, and acceptance details do not remain in the
model-facing document.

## Write boundary

Do not edit the ledger or topic document ad hoc. Create the immutable pending
document-write payload through `discussion_protocol.py`, then let the protocol
compare, atomically apply, reread, and complete the exact authorized bytes
while holding its own lock.

Use `prepare-topic-update` for one confirmed mutation, then
`apply-document-write` with the returned `document_write_id`. Every update
carries expected ledger and topic revisions plus a UUIDv4
idempotency key. Exact replay returns the original result; a reused key with
different parameters is a conflict.

Use `refresh-requirement-narrative` after every material answer,
clarification, confirmation, or conclusion to replace the structured current
goal, background, scope, non-goals, scenarios, assumptions, facts, constraints,
acceptance conditions, and optional one-item direction-change summary. Supply the complete current value of every field;
the operation replaces the prior narrative so obsolete detail cannot
accumulate. Decisions, inserted ideas, and active questions continue to use
their dedicated mutations.

Publishing the immutable payload and committing its ledger record are two
durability boundaries. If the process stops between them, the payload is not
authoritative by itself. Retry the exact original `prepare-topic-update`
request: the deterministic `DW-*` identity, typed mutation and rendered digest
must all match before the protocol adopts that payload and commits the single
ledger event. A changed request cannot take over it, and an unrelated orphan
blocks new payload publication. This recovery never requires manual deletion.

When the caller cannot prove whether apply persisted, retry the exact
`apply-document-write` request. The protocol rereads the immutable payload and
current document digest; exact already-applied bytes are adopted, the recorded
before bytes are atomically replaced, and any third state is rejected. It never
reconstructs intent from Markdown.

Treat any non-completed `DW-*` as a discussion freeze. Do not ask or persist a
new substantive question until `read-topic` can verify the payload, document
digest, and single active question. An orphan
from the prepare/ledger crash window requires the exact prepare replay above.
Missing or damaged payloads belonging to a ledger record stop on conflicting
facts; no payload is silently recreated
from the current Markdown.
