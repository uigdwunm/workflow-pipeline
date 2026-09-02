# Lifecycle Integration

Read this reference only when routing a verified discussion toward stages 1–4
or when another stage asks to discover discussion context.

The protocol publishes verified `stage-entry` and `implementation-source`
checkpoints and exposes typed discovery, route, reopen and Phase Run
operations. Do not simulate lifecycle operations with direct ledger edits.

Later integration may attach only from authenticated handoff or phase evidence,
an active binding, an exact design document or stable footer, and finally a
read-only locate operation. `none` and `ambiguous` remain zero-write standalone
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

Use `prepare-phase-run` only for an existing topic-local, non-wrapper lifecycle
route. A prepared run follows one explicit sequence: the source topic calls
`authorize-phase-carrier`; a wrapper carrier calls `claim-phase-carrier`; the
carrier calls `phase-ready`; the source calls `phase-activate`; the carrier
calls `claim-phase-completion`; and the source calls `complete-phase-run` then
`finalize-phase-run`. Keep those actor boundaries separate.

For Stage 4 attached by the exact successful Stage-3 handoff, the source and
carrier are the same current task. After the Stage-4 entry confirmation and
before closure work, call `read-topic` with the handed-off identity and binding.
Require the exact active, open topic at `current_phase: 3`, no pending document
write, the handed-off phase-3 result and the current ledger/topic revisions.
Then call `prepare-phase-run` for `3→4` with carrier kind `change-closure`,
followed by `authorize-phase-carrier`, `phase-ready` and `phase-activate` from
that current task. Once active, perform the ordinary Stage-4 Git and document closure
in the inherited Flow Worktree. After Git verifies the final target and cleanup,
call `claim-phase-completion`,
`complete-phase-run` and `finalize-phase-run`, then call `read-topic` again to
confirm `current_phase: 4`. The no-document-change path follows the same
completion sequence. Standalone Stage 4 performs none of these operations.

Use `cancel-phase-run`, `fail-phase-run`, or
`revoke-phase-authorization` for known source-side outcomes. Use
`phase-outcome-unknown` followed by `reconcile-phase-run` when an external
carrier result is uncertain; retry only through `retry-phase-run` after the
prior attempt is eligible. Use `read-phase-run` to inspect one run and
`reopen-phase` only for the explicit affected-decision review required to
return a topic to phase 0.

Phase 1 always updates the existing topic document through its `DW-*` protocol.
Phase 2 treats that checkpoint as read-only and owns
only Spec, ADR, Tickets and its planning commit. A `1→3` wrapper run requires
true completeness for scope, behavior, failures, acceptance conditions and
test seam; activation atomically records phase 2 `not_applicable` for the exact
scope without planning artifacts. Pending impacts, non-latest checkpoints or
any evidence drift stop preparation or activation.

Continuous flow has one origin: exact `执行后续全部流程` on a successful phase-1
footer. Phase 0 never propagates continuous mode. Existing confirmations,
external authority boundaries remain unchanged.
When discovery returns `none` or `ambiguous`, do not initialize, bind, prepare
a checkpoint or create a Phase Run; continue the wrapper's standalone flow.
When an explicitly authorized `document_only` context is initialized, its
creation authorization follows the permanent ledger-receipt and exact-replay
rules in [ledger-protocol.md](ledger-protocol.md); replay reads the current
state and never reinitializes or rewinds it.
