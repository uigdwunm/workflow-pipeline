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

On a later user-requested Phase-0/1 turn with a closed gate, call
`evaluate-topic-gate`. Report the exact waiting condition if blocked. If it is
releasable, show the selected current bases, obtain fresh confirmation, and
call `release-topic-gate` for the complete closed set before substantive work.
First-turn handoff acceptance, read-only inspection, and recovery remain
available while closed. Do not poll, notify, wake, or reevaluate an open gate;
Phase 2 and later do not consult Topic Dependencies.

The Phase Source Task owns split preparation and creation confirmations. A
dedicated grilling carrier may return only a bounded proposal; it never
prepares, releases, or changes Topic Dependencies.
