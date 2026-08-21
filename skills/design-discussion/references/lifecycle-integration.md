# Lifecycle Integration

Read this reference only when routing a verified discussion toward stages 1–4
or when another stage asks to discover discussion context.

The protocol can publish a verified `stage-entry` or `implementation-source`
checkpoint, but it does not yet expose discovery, route, reopen, Phase Run,
source refresh, implementation or closure mutations. Do not simulate those
later lifecycle operations with ledger edits.

Later integration may attach only from authenticated handoff or phase evidence,
an active binding, an exact design document or stable footer, and finally a
read-only locate operation. `none` and `ambiguous` remain zero-write legacy
paths. Legal forward transitions are `0→1`, `0→2`, `1→2`, `1→3`, `2→3` and
`3→4`; returning to phase 0 requires explicit reopen and affected-decision
review.

Until the necessary typed route operation exists, a checkpoint may preserve
the source but does not itself authorize launching or recording a downstream
stage. Report the topic's verified phase and stop.
