# Requirement Document Contract

Phases 0 and 1 always own exactly one mutable requirement document for the
current target. Phase 2 consumes one committed, frozen requirement document
as its only child requirement source. An explicit standalone Stage-2 request
may instead have its primary freeze confirmed conversation requirements under
[the conversation-source contract](../../solution-design/references/conversation-source.md),
without performing phases 0/1. Attached discussion sources retain this contract.
Phase 3 consumes only the planning artifacts
published by Phase 2.

## Resolve one document

- Phase 0 creates and maintains `docs/discussions/<root-slug>/topic.md` through
  the topic-document protocol.
- Phase 1 attached to a verified Phase-0 topic inherits that exact document and
  never creates or migrates another one.
- Standalone Phase 1 creates its document before the first substantive question.
  Use the repository's established requirement-document convention or
  `docs/problem-framing/<yyyy-mm-dd>-<short-target>.md` without overwriting an
  existing path or traversing a symbolic-link component.
- Moving Phase 1 to a dedicated task transfers write ownership of the existing
  document. Migration never creates a second requirement document.

An explicit Phase-0 or Phase-1 request, or an unambiguous contextual
confirmation to the immediately preceding Phase-0 or Phase-1 proposal,
authorizes only the bounded local creation and maintenance of this document.
It grants no implementation, remote-write, paid, or destructive authority.

## Keep current truth

The document is a semantic current-state snapshot, not a transcript or an audit
archive. After every material answer, clarification, confirmation, or
conclusion, update all affected sections before asking the next material
question.

Keep confirmed requirements, candidates, tentative assumptions, facts,
constraints, acceptance conditions, and unresolved questions visibly distinct.
Preference, exploration, silence, and lack of objection remain unconfirmed.

When a confirmed direction changes:

1. confirm the new direction;
2. identify the affected current requirements, scenarios, constraints, and
   acceptance conditions;
3. rewrite those sections to contain only the new coherent current state; and
4. retain at most one current concise change note when it prevents a likely future
   misunderstanding or repeated discussion.

Delete superseded reasoning and downstream detail that no longer constrains the
current target. Preserve any still-valid residue by rewriting it as a current
fact, constraint, non-goal, or requirement rather than historical commentary.
The Phase-0 ledger may retain operational history for recovery; that history
does not belong in the model-facing requirement document.

## Stop, resume, and freeze

While active, the document has `drafting` status. A paused current-task flow
resumes the same path. An explicitly abandoned flow records `abandoned` and is
ineligible for Phase 2; never delete it automatically.

An unverified document write stops questioning until the exact write is
reconciled. Only one task owns writes at a time. A retry or migration reuses the
same document identity and current bytes.

Phase 1 completes only after the document is internally consistent, contains no
unresolved question that could materially change Phase-2 output, passes the
repository-aware checks, is committed, and is frozen by path, commit, and
SHA-256. The Phase-2 child may not supplement its frozen source from chat
history, guess a missing requirement, or create a second requirement draft.
For a Phase-1 source, a material requirement gap returns to Phase 1, which
reopens and updates the same document; prior Phase-2
planning is then stale and must be regenerated.
