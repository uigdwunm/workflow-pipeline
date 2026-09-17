# Lifecycle Integration

Read this reference only when routing a verified discussion toward stages 1–4
or when another stage asks to discover discussion context.

The protocol publishes verified `stage-entry` and `implementation-source`
checkpoints and exposes typed discovery, route, reopen and Phase Run
operations. Do not simulate lifecycle operations with direct ledger edits.

Later integration may attach only from authenticated handoff or phase evidence,
an active binding, an exact design document or stable footer, and finally a
read-only locate operation. `none` and `ambiguous` remain zero-write standalone
paths with respect to discussion state. Standalone Stage 2 may prepare its own
conversation snapshot under
[the conversation-source contract](../../solution-design/conversation-source.md)
without creating or advancing a Discussion Topic. Legal forward transitions are `0→1`, `0→2`, `1→2`, `2→3` and
`3→4`; returning to phase 0 requires explicit reopen and affected-decision
review.

For wrapper stages 1 and 2, publish the latest effective 0/1 `stage-entry`
checkpoint, then call `prepare-wrapper-phase-run`. Carrier kinds and routes are
fixed: `current-problem-framing` or `dedicated-grilling` for `0→1`,
and `solution-designer` for `0→2` or `1→2`. A dedicated carrier is authorized by the source topic, verifies the
exact checkpoint identity, calls `claim-phase-carrier`, reports ready and waits
for source-topic activation before substantive work.

Use `prepare-phase-run` only for an existing topic-local, non-wrapper lifecycle
route. A prepared run follows one explicit sequence: the source topic calls
`authorize-phase-carrier`; a wrapper carrier calls `claim-phase-carrier`; the
carrier calls `phase-ready`; the source calls `phase-activate`; the carrier
calls `claim-phase-completion`; and the source calls `complete-phase-run` then
`finalize-phase-run`. Keep those actor boundaries separate.

For dedicated Stage 1, route by the verified source phase. At phase 0, prepare
only the `0→1` wrapper, then the Workflow Control plan. The source binds the
actual created task through `authorize-phase-carrier` before `creation-result`;
claim/ready/activate follows. At phase 1, use `dedicated-stage(stage=1)` followed
by plan/confirmation, bind/creation-result and `accept-handoff`; successful
acceptance grants immediate work without another Phase Run or later-turn step.
Same-type Stage-0 dedicated acceptance behaves identically. Ordinary child and
continuation handoffs retain first-turn acceptance and later-turn authorization.

For mutable `0→1` wrappers, `evidence` and source checkpoint identity are frozen
inputs. Activation initializes `working_evidence`; only verified DW preparation
and application advance it. Each DW binds its exact run/attempt and expected
before/after evidence. A file-written/ledger-uncommitted failure replays the
same DW. External edits or unrelated evidence drift require reconciliation;
never refresh hashes to bless them. Apply a pending DW to finish consistency
even if its mutation closed the gate; subsequent work still requires open gates.

After user completion confirmation, finish DW/impacts and commit the actual
output. Read `working_evidence` from `read-phase-run` and use it for
`claim-phase-completion`, which freezes `output_evidence` and stops writes.
When a dedicated Workflow Control plan exists, the controller must first
`receive` and `accept` its authenticated Git delivery, then call
`complete-phase-run` and `finalize-phase-run` with `output_evidence`.
Only finalization advances `current_phase` to 1. Its Phase Result preserves
input and output evidence separately; later stage-entry checkpoints use the
actual output and new topic revision. Identical accepted deliveries remain ACKs.
Same-stage receive also stops new requirement writes, but needs no finalization.
Other Phase Run routes retain their existing frozen-input checks.

The controller cannot prepare/apply requirement writes while a dedicated
carrier holds them. A wrapper waits through ready; revoked, completion-claimed
or delivery-frozen carriers cannot start new writes. Before activation, settle
pending DW and require any previous dedicated writer's accepted result.
An accepted Stage-0 task awaiting archive uses the bounded successor slot in
[Workflow Control](../guided-implementation/workflow-control-protocol.md).

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
Phase 2 treats that checkpoint as read-only and owns only Spec, ADR, Tickets and
its planning commit. A discussion-attached Phase 3 accepts only Phase-2 planning
artifacts and a verified `2→3` handoff; Phase 1 never routes directly to
implementation. An explicit standalone Stage-3 invocation is unattached, uses
its fixed implementation brief, and does not read, mutate, or advance the
discussion lifecycle. Pending impacts, non-latest checkpoints or any evidence
drift stop preparation or activation.

Continuous flow requires a clear natural-language request for continuous
remaining execution, normalized as `confirmation_intent: continuous`. Phase 0 and Phase 1 may authorize continuous stages [2,3,4] using source_phase, exact scope and the latest published checkpoint; Phase 0 uses phase_result_id null.
Interpret confirmation through
[`confirmation-contract.md`](confirmation-contract.md); external authority
boundaries remain unchanged.
When discovery returns `none` or `ambiguous`, do not initialize, bind, prepare
a checkpoint or create a Phase Run; continue the wrapper's standalone flow.
When an explicitly authorized `document_only` context is initialized, its
creation authorization follows the permanent ledger-receipt and exact-replay
rules in [ledger-protocol.md](ledger-protocol.md); replay reads the current
state and never reinitializes or rewinds it.
