# Lifecycle Integration

Read this reference only when routing a verified discussion toward stages 1–4
or when another stage asks to discover discussion context.

Ticket 01 establishes a phase-0 root topic only. It does not expose discovery,
route, reopen, Phase Run, source refresh, implementation or closure mutations.
Do not simulate them with ledger edits.

Later integration may attach only from authenticated handoff or phase evidence,
an active binding, an exact design document or stable footer, and finally a
read-only locate operation. `none` and `ambiguous` remain zero-write legacy
paths. Legal forward transitions are `0→1`, `0→2`, `1→2`, `1→3`, `2→3` and
`3→4`; returning to phase 0 requires explicit reopen and affected-decision
review.

Until the necessary typed operation exists, report the topic's verified phase
and stop before launching or recording a downstream stage.
