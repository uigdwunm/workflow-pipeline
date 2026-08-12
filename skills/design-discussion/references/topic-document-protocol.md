# Topic Document Protocol

Read this reference before creating a document-write payload or interpreting a
topic document.

## Authority

One topic owns one current design document under
`docs/discussions/<root-slug>/topic.md`. The document carries `project_id`,
`tree_id`, `topic_id`, the parent-topic snapshot and its own revision. The
ledger owns coordination state; the document owns the human-readable current
design.

## Required sections

Keep these sections distinct:

- Confirmed Decisions: only explicitly confirmed choices with stable decision
  identities.
- Candidate Solution: proposals still open to change.
- Tentative Assumptions: beliefs requiring confirmation or evidence.
- Facts: verified constraints and observations.
- Pending Questions: unresolved questions, exposing at most one to the user in
  an ordinary turn.
- Decision Evolution: a compact account of superseded or changed decisions.

Never promote preference, silence, exploration or lack of objection into
Confirmed Decisions. When a broad direction changes, confirm the new direction
first; later protocol operations must register and review each affected
decision individually.

## Write boundary

Do not edit the ledger or topic document ad hoc. Create the immutable pending
document-write payload through `discussion_protocol.py`, acquire and verify the
shared document lease with stage `design-discussion`, apply exactly the
authorized bytes, reread them, release the lease, and complete the payload.
Ticket 01 exposes bootstrap only, so stop if those later operations are not yet
available.
