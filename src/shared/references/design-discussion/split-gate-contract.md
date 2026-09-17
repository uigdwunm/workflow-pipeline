# Split and requirements-gate contract

Read this reference when a Phase-0 or Phase-1 discussion considers splitting a
topic, or when a later user-requested turn has Topic Dependencies.

Recommend a split only when all four conditions hold: the new work has an
independent, nameable goal; it has its own scope or acceptance outcome; it
would materially interrupt the current discussion; and removing it does not
make the current topic incomplete. Before preparing state, show one complete
`新话题建议`: both goals, tree ownership and parent relation, Phase-0 entry,
the exact dependency direction and semantic result if any, current-topic
handling, result return, and the separate confirmation that creates one task.
The confirmed proposal prepares ledger state only.

For standalone `1拷问`, a split creates a new root topic only: it creates no
parent, continuation, or executable dependency edge.

On every later user-requested Phase-0/1 turn for a topic with Topic
Dependencies, first call `read-topic` and inspect its current
`derived_gate_state` before substantive work. This mandatory per-turn read
observes an upstream reclosure that occurred between turns. If the derived gate
is open, proceed without reevaluating evidence. If it is closed, call
`evaluate-topic-gate`. Report the exact waiting condition if blocked. If it is
releasable, show the selected current bases, obtain fresh confirmation, and
call `release-topic-gate` for the complete closed set before substantive work.
First-turn handoff acceptance, read-only inspection, and recovery remain
available while closed. Do not poll, notify, or wake an open gate; Phase 2 and
later do not consult Topic Dependencies.

The Phase Source Task owns split preparation and creation confirmations. A
dedicated grilling carrier may return only a bounded proposal; it never
prepares, releases, or changes Topic Dependencies.
Its authenticated return contains the exact seven fields defined by
[`{{resource:problem-framing/references/dedicated-grilling-protocol.md}}`]({{resource:problem-framing/references/dedicated-grilling-protocol.md}}); the source
restates that payload and obtains both normal confirmations before preparing,
creating, binding, or cancelling a handoff. A carrier may do none of those
operations, and every accepted split starts a new topic in `0讨论`.

## Dependency update consumer contract

Only the dependent topic's authenticated owner may call
`update-topic-dependency`, and only while that topic is in Phase 0 or 1. A
fresh user confirmation authorizes exactly one action-specific request:
`create` supplies prerequisite, requirement kind and summary; `replace` also
supplies the dependency identity and expected dependency revision; `cancel`
supplies only that identity and revision. Cycles, duplicate active endpoints,
stale revisions, and Phase-2-or-later changes are rejected without a write.
Topic Dependencies are historical and immutable in Phase 2; use the shared
gate evaluation/release contract instead of reconstructing dependency rules in
another Skill.
