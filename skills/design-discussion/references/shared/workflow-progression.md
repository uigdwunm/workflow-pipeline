# Workflow progression

## Retired visible task archive recovery

C v10 keeps retired_handoffs in the current control projection across start and
retains original control_transactions and host evidence in immutable history.
successor-ready derives takeover_proof from the saved causal stop; unresolved
business calls, B/allocation transactions and stop barriers still block takeover
and next-stage start. The committed takeover is followed by one archive intent.
Only a confirmed handoff plus a persisted archive intent is cleanup scope.

Archive recovery names handoff_id and operation_id. Attached recovery reads the
original topic's latest ledger with its original Controller authority and updates
only that retired record; current native execution context is unchanged. Late
transactions enter the current collection without mutating historical snapshots.

Before host invocation, persist archive-result(status=issued) with its exact
adapter/invocation identity. A replayed effect never permits another invocation.
After an interruption with an unissued intent, query original host call history.
An authenticated archive call_lookup with operation_id, ref, adapter,
invocation_id, response_id, status:not-issued and raw can re-emit that same
operation. C retains and ACKs repeated lookup responses. Issued without result
or unknown requires read-archive-state; pending queries do not issue duplicates.
Failed/unknown readbacks wait for another user-triggered recovery. No timer or
background worker exists.

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
{"protocol":"workflow-progress-v11","operation":"inspect","expected_revision":0}
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
- `deliver-requirement`: data is `{request: <complete delivery prepare request>}`.
  C adds a delivery transaction to `workflow_requirements`, persists the request,
  intent and issued state before calling the
  [producer](requirement-delivery.md), then saves the
  delivered and verified results. Replay reconciles original objects first; safe
  unrelated target advancement saves a linked intent under the same transaction.
  Stops and unresolved side effects block new writes; only read-only reconciliation
  remains available until the original attempt resumes. A failed result remains
  downstream_ready=false and never becomes B acceptance.
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
- `observe`: data is `{event_id, receipt, result?, action_id?, provenance?, action_resolution?, closure?, publication_candidate?}`.
  Receipt is B's complete authenticated receipt including raw. C saves it
  before bind/reconcile/receive. A result is B's `{delivery_id,status,payload}`.
  To establish current resumability or stopped-writer proof, `action_id` must
  identify the actual issued invoke/continue/stop action or current read-only
  query that produced this tool response. The adapter authenticates that causal
  link; copying an ID onto old evidence is invalid. Use stable event IDs:
  identical replay ACKs; conflicting replay fails. Uncorrelated adverse facts
  revoke continuation, but cannot establish quiescence or resumability.
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

For current execution proof, `observe.provenance` is
`{call_ref,response_ref,action_id}`. The trusted adapter captures these references
at the actual invocation/response boundary and preserves them on replay, outside
the unmodified raw response. `call_ref` identifies one real invocation;
`response_ref` identifies one response within that adapter; the provenance
action is the saved C action which caused the invocation. C checks that the
outer action ID agrees, the action is current/unresolved, and the response and
call references have not been rebound to different content or causes. Response
identity is not raw-content equality: distinct calls may return identical raw.
An adapter must not manufacture a new provenance for cached evidence. If it
cannot establish the actual relationship, omit provenance; that observation
cannot grant proof. Unsupported or uncertain queries wait for explicit recovery.
These fields, hashes and checkpoint records are consistency evidence, not an
authenticator for arbitrary JSON. The existing trusted host/controller boundary
is still responsible for authenticating tool calls and Controller decisions.

Host ingress precedes business-payload validation and business dedup. New
stopped/unknown evidence for the original identity revokes permission even when
its provenance is absent, malformed or conflicting; it does not thereby prove
quiescence. An exact replay of a known response is an ACK. `host.last_stop`
retains the most recently ingressed nonduplicate stop separately from the
current projection. A fresh proven query can update current state without
erasing that stop. Creation ready binds identity only, never runtime liveness.

`inspect-host-state` is a persisted read-only query of the exact original ref,
request and attempt, with an action ID and generation. The adapter obtains a
new tool response after that query and returns a B-shaped `event=result` receipt
through `observe`, naming this query's action ID. It must preserve raw tool
provenance, not relabel a creation lookup or cached result. Unsupported lookup
is `unknown`; never recreate an identity. A negative query returns
`await-host-recovery`; explicit resume may query again after original recovery.
Normal uninterrupted invoke/continue responses can supply current proof without
an extra query. Creation `ready` binds identity only.

If a revoked generation still has an unresolved host action, the query retains
that exact action in `payload.unresolved_action`. The adapter must reconcile its
actual invocation outcome as well as the current original-identity state. Return
`action_resolution={action_id,outcome}` only from that reconciliation; terminal
outcomes are `completed`, `not-issued` or `cancelled`. C requires the exact action
ID and authenticated current query provenance before settling it. Idle/stopped
state without this evidence becomes unknown: an earlier uncertain mutation might
otherwise execute later. Missing or unsupported action lookup waits, and explicit
resume can query again. Neither the query nor its terminal outcome reissues the
old mutation. Ordinary same-generation unresolved actions still return exact
lookup instead of a new continuation.

Explicit resume refreshes an ended query at one common point after pause/cancel
admission and before business replay, accepted-stage or publication returns. It
archives that query in `host.query_history` before issuing a new query for the
same still-unsettled action. The accepted B result, Controller decisions and
retained action are unchanged. Ordinary advance and duplicate receipts do not
retry negative queries. An in-flight query keeps its ID; a completed accepted
stage with no unsettled action simply ACKs. Audit history never supplies current
proof: a response to the expired query cannot release the action.

Issuing a stop does not settle or discard earlier invocations. In the same
checkpoint save that records a new invoke/continue/stop action, C retains any
unresolved previous `host_action` in ordered `retained_host_actions`. The new
`host_action` describes the current call; its stopped receipt proves the current
writer stopped, not that older queued calls cannot run later. Repeated stop,
pause and cancellation preserve this responsibility. Stops are never delayed
while waiting for older call outcomes.

Recovery queries select the oldest unresolved retained action, then the current
action if still unresolved. The existing singular `unresolved_action` and
`action_resolution` envelope is unchanged: one authenticated query settles one
exact action. If more remain, C emits the next read-only query, retaining each
settlement as audit evidence. Missing/wrong/nonterminal outcomes wait; a stop
response cannot serve as another action's resolution. Only after all invocations
are settled can current proof authorize one continuation, new publication/intake,
or the next stage. Interrupted issuance and settlement retain the same identities.
This is internal v4 checkpoint bookkeeping, not a new host permission or state
store; existing pinned runs remain on their original package.

The `host` projection stores authenticated evidence separately from business
consumption. Pause, cancellation, stopped and unknown invalidate the previous
continuation generation. Replayed invocation responses and late action results cannot restore it.
Identical raw content from a different authenticated invocation is not a replay. Business-message dedup runs after independent host
processing, so a duplicate message cannot swallow a newer stopped fact.
Every `continue-host` issuance checks consumed business intent, original
authority/identity/source, stop intent and current unused proof. The same save
consumes proof and records issuance; repeating advance returns exact lookup.

Stage-3 `start` preserves the implementation policy from the sealed handoff;
omission means full. The foreground scripted carrier has no frozen Controller
direct decision in its confirmed runner input and therefore uses full. Read
[Bounded direct implementation](guided-implementation/workflow-control-protocol.md#bounded-direct-implementation)
for admission and candidate-source rules. C retains the original native
Dispatcher result/stop and Git snapshot for direct candidate provenance; zero
allocations never close the stop barrier on their own.

## Dispatcher allocations

Allocations require full mode. Direct converts through the original control
transaction before any allocation; explicit inherited-byte adoption then uses
the same B allocation path, result and acceptance, with separate changed/adopted
paths and the frozen adoption snapshot.

Pass the returned `checkpoint` to the native dispatcher alongside B's unchanged
bootstrap payload. Its `allocation` operation uses
`{allocation_id,operation,handoff? ,receipt? ,result? ,decision?}` as data.
allocation_id is the original stable local intent ID, not a native task identity.
Prepare uses the execution-agent B handoff input from the actual bound dispatcher;
C retains parent source, binding, authority baseline and protections. Each new
allocation snapshots current HEAD, which must descend from that baseline without
changing original protected paths. The Git adapter still receives the parent's
frozen authority baseline for prepare, receive and acceptance; it never replaces
that boundary with a later allocation snapshot. Repeating prepare
returns exact lookup, never another host create. Bind/reconcile/receive use B's
original full receipts/results. Accept takes the dispatcher's decision reference;
C derives the received delivery digest. Host configuration and governance remain
the current native adapter's responsibility.
Execution children wait for the `write_release` returned only after a successful
native bind. B checks assigned-file freshness at bind and requires that exact
release in the completed child result. A failed bind retains its original native
creation receipt for reconciliation and cannot authorize child writes.

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

Both `technical_error` and `no_progress` persist as `business_block`, binding
the trigger and original stage, attempt, handoff, ref and Controller. Its
`subject` includes the exact `block_id`. `resume`, new host facts, bind/reconcile,
later business results and publication bookkeeping cannot clear it. New business
messages observed while blocked remain audit/dedup evidence, not deferred work
that can spring into effect after recovery. Blocking does not mutate host facts
during B replay. The continuation gate independently requires no business block.

The original trusted Controller may call `recover-business` with exactly:

```text
{decision_id,subject,reference,diagnosis,instruction,expected_progress}
```

Copy `subject` from the active block. All other fields are nonempty strings
from an actual Controller decision, not a child's claim. The Controller reviews
the diagnosis, corrective instruction and expected progress; C verifies identity,
current control authority and absence of unresolved B/control/allocation
transactions. This operation authorizes only the original identity and scope;
it does not replace a dispatcher, fabricate executions, or change A/B authority.
Normal workflow execution acquires no additional human approval gate.

The existing `control/recover-dispatch` remains a separate, explicit Controller
recovery under B's stopped-writer, accepted-byte and replacement rules. Its C
journal binds the active business block ID before calling B; only successful
recovery of that exact block clears it and records the control decision in
business history. It cannot clear a subsequently created block or serve as a
design-stage recovery. This preserves existing replacement authority; the new
`recover-business` operation never grants replacement authority.

Recovery atomically records `recovery_decisions` and `business_history`, clears
the exact block, starts a new no-progress counting episode and revokes old host
proof. It records one continuation intent containing the reviewed instruction;
it does not call a host. The original carrier subsequently uses advance/resume,
revalidates source, scope and Phase and obtains a fresh original-identity query
before continuation. Identical decisions ACK without reissuing; stale or
conflicting decisions cannot clear a later block. Error history is retained.
Publication already started permits only original publication finalization,
never a new host continuation. Publication results cannot clear business blocks.

During pause/pausing, a valid decision is saved as `deferred_business_recovery`.
It neither clears the block nor unpauses. Only explicit resume after real stopped
proof consumes it; cancellation never does. An interruption between saving
explicit unpause and consuming the decision resumes that same decision. Ordinary
resume without a recovery decision leaves both kinds of business failure blocked.

The foreground runner exposes `recover-business RUN_RECORD DECISION_INPUT` to
record the decision in the original Controller context. It rereads the consumed
C decision before saving transport intent. It does not launch a CLI carrier;
subsequent explicit resume carries the existing C recovery into the same CLI
session. That CLI session is not the native designer/dispatcher/closure identity.

For attached dedicated launch, B's reservation changes the ledger control view.
C verifies the complete handoff before reservation; before host launch it reuses
B's post-mutation owner/configuration verifier, compares unchanged source and
Git facts, rechecks gates and requires exact readback of the saved reservation.
It does not refresh requirements to bless drift.

The dispatch transaction is saved before ledger effects. `transaction` retains
the exact B request and original envelope; `transaction_source` retains its
original observation or controller decision. `transaction_result` is saved
before consumption. Consumption atomically updates control, dispatch, business
step, pending/decision and dedup state, then clears these three journal fields.
After interruption `resume` replays only a missing result using the exact
envelope, including its original idempotency key. A saved result is consumed
without another B call. Neither route re-enters live host ingress or changes
current host facts. New business input cannot overwrite an unresolved request.
Historical continue retains business intent while awaiting current proof;
needs_input preserves its pending decision; technical_error remains blocked
until original controller recovery. Completed deliveries can be reconciled
without rerunning their author, with current quiescence checked separately
before operations that require it. Pause/cancel take priority over consumption;
explicit unpause is saved before replay. Cancellation never resumes a journal.

After interruption,
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
archive, archive-result, candidate/review/validation, escalate-implementation or
recover-dispatch seam. For escalation, retain the original authenticated
Controller decision and causal stopped-byte snapshot in that transaction.
Identical completed requests ACK without a new snapshot or followup issuance;
unknown outcomes reconcile the saved request. Stop intent and unresolved native,
B, allocation, review or validation work keep conversion closed.
Execution dispatch, result and acceptance belong exclusively to `allocation`'s
native B bind/receive/accept path; the public control action rejects them. C saves the original port and evidence, invokes its
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

Lifecycle envelopes and historical Phase readbacks retain A's `discussion_project`,
including after Flow removal. Source and Carrier still execute their own operations;
paths do not authenticate them. Pending envelopes and keys remain unchanged on retry.
Final completion rereads the original ledger and surviving repository's published
Git facts, without recreating a Flow. The discussion owner must remain available.

New runner records use outer version 6 and pin workflow-progress-v11 /
workflow-stage-transfer-v7 / flow-worktree-v2 packages with control compatibility
key 6. Earlier progress protocols and runner records require their original
runtime and pinned packages; neither new APIs nor registry refresh migrate
or replace records. Complete
registration is required for live actions. No installation is implied.

Confirmed input retains the existing frozen_requirement display tuple
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

The owner holds one app-server stdio connection across carrier turns and stage
boundaries. `continue` permits only C's next action in that same host. A running
native role is waited on; it does not authorize another invoke or a business
followup. Carrier turn completion, native completion, B acceptance and Git
publication are separate facts. Stable running waits do not consume the
unchanged-business-progress counter. Reads use bounded intervals up to 60 seconds.

The `run_record` must live outside the repository and Flow Worktree in an
owner-only directory (mode `0700`). The runner creates a missing record directory
with that mode, rejects an existing directory accessible to other users, and
checks the run lock before starting a host. Stage result artifacts use atomic
replacement rather than following existing links; raw host event and diagnostic
files are created with owner-only permissions and reject symbolic links. Old
runs continue with their original pinned runtime.

`workflow.py pause RECORD` and `cancel RECORD` queue an exact request when an
owner holds RunLock. The owner reads it outside RPC/checkpoint locks, steers an
active carrier or delivers it at the next turn boundary, and consumes C's
original stop/reconciliation actions. Paused and needs_input keep the foreground
host. Normal close occurs only after final verified C/B/D completion or a closed
cancellation barrier. Emergency process-group cleanup is transport cleanup and
never native stopped proof. Unknown lookup leaves the original request pending.
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
With a live owner, an answer returns `queued` after saving its exact ID, subject,
and value; only the original carrier consumes it through C decide. Identical
answers ACK, different answers conflict, and obsolete IDs are rejected. An
app-server approval remains queued while pause/cancel is pending. The owner
rechecks that priority under the checkpoint lock around only the first
nonblocking write attempt, after journaling intent. A first positive byte count
commits the frame. EAGAIN with zero bytes releases the lock before a bounded
readiness wait, then rechecks stop priority before trying again. A stop recorded
before the first byte defers the response and steers the original carrier.
After commitment, only the remaining suffix is sent, outside the checkpoint
lock; a later stop can still be saved while the pipe is backpressured. Every
outbound frame has a bounded write deadline. Timeout or pipe failure records
the actual byte count as uncertain, retains the original request, and prevents
further writes on that connection. Neither a partial frame nor a failed write
is replayed as a whole message, and neither proves native stopped state.
An answer alone does not unpause. `resume RECORD` without an answer explicitly
queues release of the original paused subject; it is applied only after the
stop barrier closes. Cancellation has priority over both forms. Requests remain
in the original checkpoint if the owner disappears before consuming them.

No-owner resume first reads the original durable transport receipts and finished
carrier result. It does not create an app-server or replay a sent request.
Missing current original-identity/action evidence returns await-host-recovery.
An untouched prelaunch can start; a completed run revalidates and ACKs. Old
records, missing fixed packages and incompatible registrations remain read-only.

The confirmed `host` object contains `transport: app-server-stdio`, the exact
`cli_version`, Controller provenance `source: {kind: controller-current-config,
controller_ref, receipt}`, the actual `thread` request settings and `effective`
permission readback. Thread settings include approvalPolicy, config,
runtimeWorkspaceRoots and exactly one of sandbox/permissions. The confirmed
roots include repository, Flow and Git metadata. Effective settings retain
approvalPolicy, sandbox and runtimeWorkspaceRoots, plus applicable reviewer,
permission-profile/provider facts. Per-stage model/effort keep their existing
owner. The adapter disables model fallback and compares actual thread settings
before its first business turn. Unsupported settings stop; there is no exec
fallback or inferred default permission policy.
The checked CLI's thread/start named-profile readback exposes only id/extends;
permissionProfile/list adds availability, not resolved permission rules.
config/read returns cwd configuration layers and configRequirements/read returns
selection constraints, neither a thread-bound complete effective profile. This
adapter therefore rejects an explicit permissions selector whose complete rules
cannot be checked. A default activePermissionProfile identity does not reject a
sandbox configuration with complete effective sandbox readback.
It preserves the Controller's selection and reports unsupported configuration;
it does not translate the profile into a broader sandbox mode.

RPC IDs belong to one connection. The adapter journals intent before sending,
retains original responses, and accepts only the matching carrier thread/turn's
successful terminal event and full final message. Native notifications retain
their own original thread/turn, including late events from an earlier parent
turn. subAgentActivity started plus item/completed ends a creation tool item;
it does not prove child completion. Commentary and deltas remain progress.
Server requests preserve exact ID/method/params/host instance in the original
checkpoint; the Controller supplies the exact RPC result JSON for that pending
matter. No request is automatically approved or replayed into another host.

`transport-lost` revokes current proof and retains identities, unresolved calls,
accepted B data and publication facts. It cannot clear a business_block. The
runner's host nonce, PID and locks locate evidence; they are not live proof.

### Native stop barrier and review activity

The stop barrier derives targets from the original dispatch, control executions,
allocation transactions, the current candidate's two review_activity slots, and
every retained review_history binding. Historical receipts identify original
roles; current lookup and newly observed activity still govern their stop
proof. Reconcile late historical reviewer activity against its exact original
candidate/axis/action, without reopening allocation or treating that result as
a review of the current candidate.
An unbound allocation or unresolved create/continue/stop call keeps it open.
Creation-ready may settle the creation call without proving the child's turn
completed. A stopped root does not settle its executions, reviewers or descendants.
Original parents retain their governing responsibilities; an unavailable parent
permits only supported exact-ref lookup/control, never an impersonated actor.

`review-activity` belongs only to the original Stage-3 Originating Task. Prepare
data is `{operation: prepare, axis, candidate, actor_ref, verification}`; axis
is standards/spec and verification is `{candidate, checks}`. C verifies the
clean exact Git candidate and returns one invoke-review action_id. The original
task performs its existing independent review call once. Observe data retains
the same axis/candidate/actor_ref/action_id and `receipt: {adapter, call_ref,
response_ref, status, ref, raw}`. The caller authenticates that actual native
response. Status is running/stopped/unknown/not-created. Unknown creation keeps
the action open; a stopped response requires the mechanically bound ref.
Duplicates ACK, conflicting receipts fail, and a new candidate waits for both
old axes to stop before retaining their receipts in review_history. These slots
grant no file allocation, implementation or candidate-acceptance authority.

`lifecycle-state` receives the current adapter's complete raw descendant lookup:
`{instance, carrier_thread, sequence, pages, events}`. Each page retains its
thread/list request and response, exact ancestor, all sourceKinds (including unknown),
modelProviders=[] to include every provider, and an explicit
archived selector. Complete archived=false pagination precedes complete
archived=true pagination; neither query may add cwd/provider/project filters.
C rejects missing partitions, interrupted pagination and duplicate identities
across pages, including a thread moving between archive partitions. Listing is
an observation, not quiescence: original bindings, terminal parent calls,
closed unresolved actions and current event generations remain mandatory.
Late spawn/activity events revoke prior evidence, and a failed lookup cannot
reuse an older snapshot to close the stop barrier. Archived does not mean stopped.
Events retain raw subAgentActivity notifications. The
foreground adapter includes all source kinds, because omitted sourceKinds would
hide native children. C mechanically associates already-bound canonical refs
with agentThreadId from those events and checks current idle status for every
known target. notLoaded/history, missing targets, unknown extra descendants and
unfinished native tool items keep the barrier open. No model-reported tree_closed
boolean is accepted. A new native event invalidates the snapshot; `host-event`
retains that raw event and recomputes the projection. The runner emits snapshots
after current native stop facts and retains unsupported lookup as a recovery
condition rather than polling or substituting a new host.

`observe` may carry this same raw snapshot in `lifecycle` with an authenticated
native response. Snapshot evidence is a projection, not a second role registry.
Publication and new B acceptance require the complete barrier; Stage 3 also
matches both stopped review refs to its exact accepted candidate. Final release
rechecks C/B/D completion. Process exit alone satisfies none of those conditions.

Explicit low-complexity standalone Stage 3 without an A requirement retains
its existing standalone-entry/control route; do not synthesize a document to
fit B. Non-Git 0/1 retains snapshot authority and is not Git downstream input.
Real native creation, async ready, stopped proofs and cross-host lookup require
separate host acceptance; adapter-double tests do not certify those capabilities.

## Review-first Stage 3

The original Controller uses `control` with authenticated receipt for
candidate-ready → review-converged → validation-start → validation-result.
See [Workflow Control Protocol](guided-implementation/workflow-control-protocol.md)
for exact evidence. C persists the final attempt and continuation intent before
same-dispatcher followup. reviewable/reviewing return review actions and never an
implementation-writing continuation. Partial writes reuse the existing exact
control transaction; unknown host actions are reconciled, never reissued.
An unresolved control transaction also blocks later control decisions, B intake,
review starts, allocations and publication. Recover only its exact saved request
and port before changing business state; a lost response cannot be cleared or
overwritten by candidate invalidation. Definitive read-only validation rejection
is retained separately from an unknown outcome and does not block a corrected
request. Cancellation, pause and raw stopped-role observations retain priority.
Explicit unpause durably restores the suspended step and original resume intent
before reconciling pending control work. A control result or identical-result ACK
does not finish resume: complete the existing deferred-decision/result handling,
then use the original host's resumability and unresolved-call gates for followup.
A validation-start action that already issued its followup is consumed once.
Verify resumed validation through actual B receive/accept, not merely a
`deliverable` control state. A stopped identity alone never proves resumability.
Paused or cancelled final results are retained for reconciliation and cannot
advance acceptance. All historical reviewers and execution allocations remain
inside the native stop barrier. Original transport loss still blocks recovery.

## Registration and dispatch recovery

Every saved entry retains the original host registration identity and either its
registry_input reference or paired inline snapshot/context. A revalidation reads
current file evidence without replacing package pins. The rendered native payload
contains registration_input fields for entry_prepare.py; pass those fields with
the actual role identity/cwd. See [Entry preparation](entry-preparation.md).

The Stage-3 controlled-recovery actions and their exact evidence contract are in
[Workflow Control Protocol](guided-implementation/workflow-control-protocol.md#controlled-stage-3-recovery).
C owns the only durable intent and result journal. Follow invoke-recovery-host once,
lookup-recovery-host after uncertainty, and activate-dispatch-recovery only after
current stops, calls, authority and Git bytes still match. The successor's prepared
state is read-only. Resume reconciles the same intent instead of creating a role.
Original host loss remains await-host-recovery with retained refs and missing
proofs. Stages 2/4 retain their existing same-identity/publication paths.
