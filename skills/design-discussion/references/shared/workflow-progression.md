# Workflow progression

Use the pinned `scripts/workflow_progress.py CHECKPOINT` at the A/B seam in
both interactive and foreground-carrier execution. CHECKPOINT is the original
conversation or runner checkpoint, an absolute path outside the repository and
Flow Worktree. Do not create a second control ledger. The adapter owns only
`workflow_progress`, `workflow_requirements`, and `workflow_lifecycle` in that
outer object. The runner owns its other fields, retains a separate process lock,
and merges these members under the same short checkpoint lock.

The short lock waits up to five seconds using a monotonic deadline. A timeout
does not establish executor failure. The runner saves local CLI observations in
the existing turn-artifact directory (`*.carrier.json`), bound to the exact
record, stage, turn, launch nonce and request digest. A busy main-checkpoint save
while receiving an event does not interrupt the carrier. If the final save is
still busy, checkpoint_write_pending stops advancement; resume merges a verified
finished local turn into the original record, retaining current C-owned fields.
An unfinished or mismatched receipt cannot authorize a relaunch. These files are
transport evidence, not another control ledger or native stopped-writer proof.
Live completion and receipt recovery use the same turn finalization: clear
delivered `resume_progression`, `answer_pending_delivery` and
`progression_response`, then consume the verified saved result before invoking
another carrier. A queued `controller_decision` is removed only when C records
that exact decision as consumed; transport completion alone cannot discard it.

## One mechanical path

Send one bounded strict JSON object on stdin:

```json
{"protocol":"workflow-progress-v2","operation":"inspect","expected_revision":0}
```

`inspect` returns the current revision/status/pending matter without advancing.
Mutations require that exact revision. Full raw responses and complete A/B
objects remain in the checkpoint; output provides the unique next action.
The caller authenticates controller decisions and host receipts. JSON strings,
digests, child assertions, names and timestamps are not authentication.

- `prepare-requirement`: data is `{request: <complete A prepare or verify request>}`.
  C retains the exact intent before write/freeze, saves results, and reconciles
  the same intent on repeated calls. Content and existing completion/commit
  authorization remain Agent/controller decisions. Partial completed_evidence
  is retained with downstream_ready=false; it never becomes a frozen receipt.
- `start`: data is `{handoff: <B semantic prepare input>, control?: <original B control port>, requirement_transaction?: <exact A transaction ID>, next_stage?: <confirmed successor>}`.
  C resolves omitted expected_entry from A, reuses the exact saved A transaction
  when selected, and derives predecessor/requirement from its prior acceptance.
  Native 2→3→4 also derives the next control context when omitted; initial and
  dedicated entries require the original full port to preserve both slots.
  next_stage defaults to the next numbered stage, except continuous Stage 0
  uses Stage 2; an explicitly selected 0→2 route is also legal.
  C verifies A, prepares/verifies the handoff, prepares dispatch and persists
  record/checkpoint together. It returns `next_action=invoke-host` once. Supply
  semantic scope/configuration from the original approved checkpoint; C never
  infers permissions from the diff. On a successor pass the complete prior
  acceptance, same mode/pins, next stage and original current control port.
  A proven not-created attempt may instead restart the same stage with a fresh
  controller retry reference, unchanged work/target/binding and retained pins.
  Unknown creation and user cancellation cannot take this path. Dedicated
  retries must supply the current confirmed ledger port; C never resets slots.
- `advance`: continues mechanical processing. After a host action has been
  issued it returns exact lookup, never the mutation again. Save/use the first
  returned action. If that response is lost, inspect and reconcile; do not
  interpret its saved payload as permission to replay a host tool.
- `observe`: data is `{event_id, receipt, result? , action_id?, closure?}`.
  Receipt is B's complete authenticated receipt including raw. C saves it
  before bind/reconcile/receive. A result is B's `{delivery_id,status,payload}`.
  `action_id`, when supplied, must name the current issued action. Use stable
  event IDs: identical replay ACKs; conflicting replay fails.
- `decide`: data is `{decision_id,subject,answer,reference}` copied from the
  exact pending matter and controller decision. For acceptance answer is
  `accept`; for stage entry it is `confirm` or an explicitly authorized
  `continuous`. Other answers carry the user's actual semantic decision.
  Old answers cannot apply to another pending matter. Acceptance is a
  controller decision, not necessarily another human approval: continuous
  Stage 2 retains its trusted readiness intake without human content review.
- `pause`, `resume`, `cancel`: retain the attempt and side effects. Pausing and
  cancelling are requests until the host proves stopped writers. Cancellation
  never cleans files. Outstanding dispatcher writer effects require the
  original control recovery, not a fabricated stopped root receipt.
- `lifecycle`: data is `{request: <original phase operation envelope>}`.
  Supported seams are claim-phase-carrier, phase-ready, phase-activate,
  claim-phase-completion, complete-phase-run, finalize-phase-run,
  reconcile-phase-run and read-phase-run. The ledger enforces actor boundaries.
  C observes the original ready/active/completion state and emits a complete
  source-lifecycle or carrier-lifecycle action with the original actor,
  run/attempt, evidence, current revisions and saved idempotency key. Only that
  authenticated actor executes it through lifecycle; carriers never impersonate
  the source. Source complete requires B acceptance, saves the exact request, then chains
  finalize with returned revisions and a saved idempotency key. It verifies
  the original completed attempt. Carrier claim/ready and source activation
  remain different authenticated actors; C never impersonates either.

Use returned `next_action` with the current host adapter. It owns real tool
mapping, supported configuration, native governance and original raw evidence.
`wait-host` uses that adapter's notifications/cursors and bounded foreground
waits. `continue-host` resumes the exact ref; it is not a fresh dispatch.
`lookup-exact-action` requires request/attempt or exact pending identity.
Absent lookup support is unknown, not permission to search by title or reissue.
No background monitor or private governance schema is added.

## Dispatcher allocations

Pass the returned `checkpoint` to the native dispatcher alongside B's unchanged
bootstrap payload. Its `allocation` operation uses
`{allocation_id,operation,handoff? ,receipt? ,result? ,decision?}` as data.
allocation_id is the original stable local intent ID, not a native task identity.
Prepare uses the execution-agent B handoff input from the actual bound dispatcher;
C retains parent source, binding, baseline and protections. Repeating prepare
returns exact lookup, never another host create. Bind/reconcile/receive use B's
original full receipts/results. Accept takes the dispatcher's decision reference;
C derives the received delivery digest. Host configuration and governance remain
the current native adapter's responsibility.

These transport records live in the same original checkpoint. Only the existing
control.handoff_progress.executions roster owns allocations and writer state;
each B result updates that control atomically with its allocation record. Child
acceptance is never stage completion. Cancelled parents can reconcile unbound
allocations through the same exact not-created proof, without reviving late refs.
Interrupted allocation intake returns allocation-recovery to its original bound
dispatcher; another runtime must not impersonate that owner during A checks.
Pausing, paused, cancelling and cancelled states prohibit new preparation and
first issue of an already-prepared allocation. A saved runner stop request also
closes this gate before the shared suspend transition has run. Another local
allocation_id cannot bypass it. Rejection reports progression_suspended without
changing control or stop state. Existing exact lookup, stopping, result intake
and acceptance remain available subject to B's original validation.

## Decisions and recovery

Continuous and stepwise share the same operations and acceptance checks.
Stepwise retains launch, solution, optional Tickets and stage-entry decisions.
Continuous skips only the existing human gates, not readiness, independent
implementation reviews, phase activation or acceptance. Changed requirements,
targets, permissions and real semantic choices always return to the controller.
For an in-stage decision send needs_input with its exact question; C creates
the pending identity and retains it across resumed turns.

Stop intent takes priority at advance and effect issuance, including a saved
runner stop request before shared suspension. A valid reply during pause/cancel
is retained in `deferred_decisions` and returns `decision_deferred`; it does not
consume the pending matter or authorize continuation. Exact duplicate replies
ACK, while stale or conflicting replies leave the stop state intact. After
stopped proof, explicit resume may consume a paused reply in the original
carrier context; continuation still requires a live host observation. Cancel
marks saved replies as audit-only. Existing authorized controller recovery and
a fresh decision reference are required to apply an answer after cancellation;
replaying the saved answer cannot restore authority.

Running, idle, turn completion, continue, needs_input, technical_error, received
and accepted are distinct. A completed B delivery requires the host's actual
stopped-writer contract. A stopped/unknown identity cannot be blindly continued.
Repeated continuation with unchanged progress evidence blocks for diagnosis.
Technical errors stop without masquerading as a permission question.

For attached dedicated launch, B's reservation changes the ledger control view.
C verifies the complete handoff before reservation; before host launch it reuses
B's post-mutation owner/configuration verifier, compares unchanged source and
Git facts, rechecks gates and requires exact readback of the saved reservation.
It does not refresh requirements to bless drift.

The dispatch transaction is saved before ledger effects. After interruption
`resume` replays that exact envelope, including its original idempotency key;
unknown reservations without a recoverable request remain blocked. Original
observations remain available if binding fails or source facts drift. Preserve
known identities for stop/reconcile; they do not grant work on stale input.

An accepted result is historical evidence. Identical receipts/answers ACK;
successor consumption verifies current A/B/Git/phase facts separately. The
Stage-3 accepted transfer retains implementation protection. Its rendered
Stage-4 projection removes only approved closure_paths; never use that projection
to accept implementation.

## Publication and lifecycle boundaries

Before local Git publication, `observe` accepts `publication_candidate` with
`{candidate_commit, artifacts, checks}` and the original native writer's exact
authenticated `event=result,status=stopped` receipt. This is a candidate, not a
completed B delivery. C checks the clean candidate, original scope, control,
package/source identity and attempt. Stage 4 additionally proves the accepted
implementation is its ancestor and the increment changes only closure_paths.
The existing two-axis implementation review stays attached to the implementation
commit; it is not silently transferred to new implementation bytes.

C returns a `publication-readiness` pending decision naming the candidate digest,
stage, handoff and attempt. The original Controller supplies `decide` with
`answer=accept` and its existing readiness reference. The adapter authenticates
that decision just as it authenticates other controller decisions. A string or
digest is not permission. This records the existing review/authorization, not an
additional user approval: continuous mode consumes its existing authorization,
stepwise mode retains its existing review gates. Changed candidates invalidate
readiness; resumed writers invalidate the stopped snapshot. Remote Matt artifact
publication retains its original owner and rules.

`publication`: data is `{candidate_commit, reference, planning_paths?, expected_target_head?}`.
C consumes the saved readiness decision, returns this exact next-action input,
and saves the derived request before calling the existing Stage-2 publish-planning
or Stage-4 complete-worktree primitive. Stage 4 retains the target observed at
readiness. Supervision callbacks append refresh, prepared (exact target/candidate
and resource identities), merged and cleanup facts to this same checkpoint before
the next effect. No second journal, queue or run authority is created.

Repeated publication input returns its saved receipt or reconciliation requirement.
`reconcile-publication` with empty data inspects original Git facts without
publishing, advancing the Flow or cleaning; it is available during pause/cancel.
`resume` first reconciles the original action and may perform only its remaining
authorized effects. Unknown outcomes never mean unpublished. Exact merge parents
and tree, retained target ancestry and original scope establish publication;
candidate ancestry alone does not. New target/candidate/permission conflicts stay
blocked. Proven publication permits Flow advancement or cleanup, never republishing.
Stop intent bars those writes until explicit authorized resume. No background retry
is scheduled. The original error and ordered facts remain recovery evidence.

`receive-publication` with empty data composes the completed B payload from the
original native artifacts/checks and independently reverified publication/cleanup.
It retains `publication.intake.kind=stage-owner-publication`, the composing entry
actor, original candidate/stop/readiness records, Git facts and the exact B message.
It does not add a host observation, alter raw receipt bytes, or claim the stopped
child performed later Git actions. Stage 4's B `candidate_commit` remains the
accepted implementation; the closure tip is separately recorded in publication.
B receives and verifies this composed delivery, and the original Controller still
accepts it before the original Phase Run is completed. Readiness is not final B
acceptance. Interrupted intake replays its exact saved B/ledger envelope.

If B's receive result is already saved but C's local step was not, resume,
advance and receive-publication finish that local consumption without calling B
again. An existing pending acceptance keeps its exact ID and subject. Before B
accept, C retains the complete original Controller decision in
`publication.acceptance_decision`; it cannot be replaced while the action is
unresolved. A saved B acceptance finishes local consumption using that same
decision. An unsaved response instead replays the original transaction envelope.
Existing accepted results and successor decisions are not overwritten, and the
pause/cancel gates precede local recovery as well as new side effects.

Explicit `resume` normalizes a completed pause before replaying or consuming an
outstanding B transaction. The stored `paused` state must retain stopped-writer
evidence; `pausing` continues the original stop/lookup path instead. Release of
the pause is saved before a transaction recovery can return early, while its
original envelope, pending matter and Controller decision remain unchanged.
Deferred answers/results without a pending transaction keep their existing intake
path; pause release is included in that path's durable save rather than discarding
the deferred input in a separate checkpoint first. Ordinary `advance` and repeated
answers never release a pause. Runner stop requests, cancellation priority and
original allocation/control recovery routing remain authoritative.

Stage 4 completion and the runner's final/duplicate completed response recheck B
acceptance, closure control, original Phase completion, precise publication and
actual resource removal. Historical Stage-2 worktrees are not required to exist:
their accepted predecessor links and committed ancestry are retained. An idle
process, effect plan, missing result or publication-only receipt never establishes
overall completion. Standalone stages have no synthetic discussion lifecycle.

`control`: data is `{action,evidence,receipt}` for the original successor-ready,
archive, archive-result, execution-result, accept-execution, execution-dispatch-result
or recover-dispatch seam. C saves the original port and evidence, invokes its
existing ledger/Git/control adapter, and retains effects and actual result. Host
receipt authentication remains mandatory. Identical completed calls ACK, and
a lost response replays the exact original mutation envelope.

Stage 2 retains candidate intake before publish-planning; only verified planning
publication forms B's completed delivery. C does not implement Git publication. Preserve the
original publication receipt; integration_unverified must be reconciled before
any retry, never republished.

For Stage 4 partial publication, `observe` may additionally carry `closure`,
the existing closure-result evidence. C invokes the existing Git/control check,
retains the actual merge and cleanup facts and its cleanup-only effects. Only
cleanup remains legal after publication. Never turn a missing complete B result
into permission to run complete-worktree again. D/its existing protocol executes
and reconciles publication/cleanup; implementation defects before publication
retain the existing return-to-Stage-3 path and review requirements. C returns
implementation-recovery with the original dispatcher ref and binding, and
blocks further closure while that controller-owned recovery is unresolved.
It does not reopen an accepted B delivery or invent a replacement attempt.

Phase completion follows B acceptance and must finish before successor work or
runner completion. 0/1 dedicated task claim/binding, bounded successor slots,
successor-ready and archive-result remain original ledger/control operations;
archiving never precedes activation, and failure never relaunches a successor.
There is no new 0/1 discussion executor. Missing Stage1→2 delivery remains an
explicit dependency; C cannot produce a merge proof or infer completion.

## Foreground carrier and compatibility

New runner records retain outer version 3 and pin workflow-progress-v2 /
flow-worktree-v2 packages. A workflow-progress-v1 member is rejected by v2; retain
its original runtime and pinned packages. Version 1/2 runner records likewise
require their original runtime; neither new APIs nor registry refresh migrate
or replace records. Complete
registration is required for live actions. No installation is implied.

Version-3 confirmed input retains the existing frozen_requirement display tuple
and additionally requires `requirement`, the complete successful A frozen or
attached Git checkpoint result. Its original path, commit, hash and positive
version must match; partial completed_evidence cannot be substituted. The carrier
reads this full object at `confirmed.requirement` in its checkpoint. It does not
guess a version or reconstruct an A receipt from the old three-field tuple.
The runner prompt carries checkpoint references for full predecessor results and
pending action responses, keeping large B evidence out of command-line arguments.

The CLI carrier reads `progression_checkpoint` from its runner payload and uses
this adapter for its native role. It never writes the outer record directly.
The external controller saves an exact `controller_decision` in the runner
record; the original carrier applies it through C decide in its own runtime.
Likewise, `resume_progression` directs reconciliation back to that carrier.
The parent never impersonates the carrier's A entry identity. Pure stage-entry
decisions remain controller-owned. Repeated unchanged CLI progress is bounded
and stops for reconciliation instead of resuming forever.
CLI session identity is not a native role identity. Only the runner advances
the outer stage route; interactive carriers advance through this same contract
in their existing task. Return C's exact `completion.stage_result`; the runner
checks persisted B acceptance and current Git facts, not a hand-written footer.

`workflow.py pause RECORD` requests a safe turn boundary; `cancel RECORD`
requests native-stop/reconciliation through C and terminates the known local
CLI process group. Terminating that group does not prove native/remote writers
stopped. Their original control recovery remains mandatory before resume.
A later pause cannot override cancellation.
A completed pause is still eligible for cancellation: the runner delivers that
upgrade to C, and shared advance/resume recover a saved cancellation request
before returning a paused result. The existing control cancellation uses retained
stopped evidence and marks deferred answers as cancelled; no extra manual cancel
or fabricated dispatcher recovery is needed to reach the cancelled state.
The shared pause/cancel entrypoints enforce this same priority, including
repeated requests and late observations. Only the existing controller recovery
with stopped-writer evidence can restore cancelled authority. The outer runner
reports pausing until C records a stopped pause; a finished CLI turn alone is
insufficient. Before any carrier is launched, or with verified non-creation,
there is no running writer to stop.

`workflow.py resume RECORD [ANSWER] --decision-id ID` binds a pending matter.
Without an answer resume only reconciles an eligible technical/checkpoint state.
Completed runs ACK without relaunching. A completed turn is persisted before
intake so a process crash does not rerun the same completed stage.

Explicit low-complexity standalone Stage 3 without an A requirement retains
its existing standalone-entry/control route; do not synthesize a document to
fit B. Non-Git 0/1 retains snapshot authority and is not Git downstream input.
Real native creation, async ready, stopped proofs and cross-host lookup require
separate host acceptance; adapter-double tests do not certify those capabilities.
