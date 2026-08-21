# Lifecycle Integration

Read this reference only when routing a verified discussion toward stages 1–4
or when another stage asks to discover discussion context.

The protocol publishes verified `stage-entry` and `implementation-source`
checkpoints and exposes typed discovery, route, reopen and Phase Run
operations. Do not simulate lifecycle operations with direct ledger edits.

Later integration may attach only from authenticated handoff or phase evidence,
an active binding, an exact design document or stable footer, and finally a
read-only locate operation. `none` and `ambiguous` remain zero-write legacy
paths. Legal forward transitions are `0→1`, `0→2`, `1→2`, `1→3`, `2→3` and
`3→4`; returning to phase 0 requires explicit reopen and affected-decision
review.

For wrapper stages 1 and 2, publish the latest effective 0/1 `stage-entry`
checkpoint, then call `prepare-wrapper-phase-run`. Carrier kinds and routes are
fixed: `current-problem-framing` or `dedicated-grilling` for `0→1`,
`solution-designer` for `0→2` or `1→2`, and `guided-implementation` for
`1→3`. A dedicated carrier is authorized by the source topic, verifies the
exact checkpoint identity, calls `claim-phase-carrier`, reports ready and waits
for source-topic activation before substantive work.

Phase 1 always updates the existing topic document through its `DW-*` and
document-lease protocol. Phase 2 treats that checkpoint as read-only and owns
only Spec, ADR, Tickets and its planning commit. A `1→3` wrapper run requires
true completeness for scope, behavior, failures, acceptance conditions and
test seam; activation atomically records phase 2 `not_applicable` for the exact
scope without planning artifacts. Pending impacts, non-latest checkpoints or
any evidence drift stop preparation or activation.

Continuous flow has one origin: exact `执行后续全部流程` on a successful phase-1
footer. Phase 0 never propagates continuous mode. Existing confirmations,
external authority boundaries and legacy in-flight protocols remain unchanged.
When discovery returns `none` or `ambiguous`, do not initialize, bind, prepare
a checkpoint or create a Phase Run; continue the wrapper's legacy flow.
