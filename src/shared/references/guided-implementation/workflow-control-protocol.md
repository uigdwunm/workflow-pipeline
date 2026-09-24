# Workflow Control Protocol

Mechanical A/B execution uses [Workflow progression]({{resource:shared/references/workflow-progression.md}}). Supply semantic decisions and authenticated raw host responses; the shared adapter retains intents, exact attempts, receipts and recovery steps. The role, review and authorization rules below remain binding.

The Workflow Controller retains confirmation, routing, result acceptance and
recovery. Dedicated Discussion Task and Dedicated Problem Framing Task are visible
0/1 carriers. Stage 2 uses one native solution-designer; stage 3 one Implementation
Dispatcher and, by default, at least one real Execution Agent with accepted
implementation bytes. The bounded direct exception below changes only that
implementation-source branch; stage 4 uses one Closure Agent. A split child
is one independent Workflow Controller and inherits no preferences or authority.

`workflow_control.py` accepts bounded strict JSON with schema_version=4, action,
actor_ref, context and evidence. Context contains schema_version, controller_ref,
topic_ref, stage, carrier, preference, flow_authority, requirement_identity and
handoff_progress, optional top-level retired_handoffs, and at most one temporary successor_control slot. The caller authenticates actor_ref and persists the returned
context in the existing conversation or runner. No new registry or monitor exists.
Effects are plans, never tool receipts. Use actual native/task identities and
termination evidence; reconcile unknown outcomes before retry.

For Git-dependent transitions use read-only `workflow_control_git.py` with exact
envelope `{repository:<absolute cwd>,baseline:<frozen commit>,request:<request>}`.
It verifies actual binding, diff, file bytes, index, commits, publication ancestry
and cleanup. Self-reported verified_commit_hash or clean booleans are insufficient.
The attached `discussion_protocol.py workflow-control` uses the existing mutation
envelope plus action/evidence, derives the exact entry authority from the ledger,
checks its gate/binding, and verifies Git receive.
dedicated-stage permits only current-stage 0/1 requirement writing and bounded split
proposals. It does not replace the active controller Conversation Binding. Cancellation
revokes its write authority; next-stage rebinding supersedes the previous carrier.

Create or inherit one requirement document before substantive questions. `prepare`
freezes target, project, title, missing_context, configuration, next_step, archive_ref
(null before creation), gate_open, entry_authority and plan_id without creating a task. Disclose stage,
controller, document path/hash/version, exact target/title, task_count=1, missing
context and configuration reason/receipt. One combined confirmation covers stage entry
and task creation; no preliminary migration offer. `decide` takes plan_id and intent
confirm, stage-current, topic-current or cancel. Refusal reuses the document and
suppresses repeated offers for that scope. choose-dedicated is an explicit new choice.
A dedicated carrier never recursively creates another. Dedicated-stage acceptance
at phase 0 or 1 becomes active immediately after baseline, identity, pending-DW
and gate checks; child/continuation keeps later-turn authorization. A closed
gate blocks preparation, creation, writes and progression until
a later user-triggered continuation rechecks dependencies.

creation-result binds current plan attempt and only an actual ready task ref.
The attached caller first authorizes the exact wrapper carrier or binds the
exact dedicated-stage handoff; creation-result authenticates that prior binding. Pending
client IDs are unusable; cancelled or obsolete attempts cannot accept late results.
receive takes delivery_id, source_ref, attempt, requirement_identity, commit and
verified_commit_hash. Verify actual commit:path and current file. The frozen launch
identity remains in the plan; completion may advance hash/version on the same path.
Identical deliveries ACK; a received/accepted delivery cannot be replaced. accept
takes delivery_digest. Then confirm the successor and actual old task archive_ref.
successor-ready requires ref, stage, input_digest, role, binding_verified, activated,
confirmed, archive_ref and takeover_proof. The proof names the old ref/attempt,
adapter/host_ref, invocation_id/response_id, raw stop_receipt and reconciled
business_calls_digest. stop_receipt is the complete existing host observation:
event_id, action_id, provenance, action_resolution and the full B receipt. Its
normalized status must be stopped, its original raw response must be present,
and ref/attempt/role/configuration must match the frozen old plan. business_calls
contains the original host invocations and response fingerprints plus unresolved;
unresolved must be empty. Both stop call and response must occur in this exact
projection and its digest must match. Boolean stopped claims and opaque strings
cannot replace these records. C derives it from saved causal host observations and
rechecks stopped writers and outstanding calls; callers cannot supply derived
proofs. Interactive Controllers authenticate original tool evidence before
activation and again before takeover. Pure validation does not authenticate
arbitrary strings; Git and archive receipts never prove a writer stopped.
The same trusted Controller/host ingress as B/C authenticates actual tool origin;
this JSON protocol verifies causal consistency, not a cryptographic host identity.
If original raw responses or complete call reconciliation cannot be obtained,
interactive Controllers must stop before activation or successor-ready. Test
adapter records do not count as real host verification.

The durable takeover atomically retains a top-level retired_handoffs record,
sets handoff_completed and promotes an existing successor slot. Without a slot,
the old progress becomes handoff-complete until the normal next-stage start.
History never grants writer authority. After this commit, C automatically
requests archive once; interactive Controllers do the same. Failed/unknown
archive reports “交接已完成；旧任务尚未确认归档” and does not block business.

archive requires handoff_id. archive-result requires handoff_id, operation_id,
ref, status and receipt; optional control_plan_id must agree. Intent effects name
the exact original ref and operation_id. Before the host call, persist an
archive-result(status=issued) with its adapter and invocation_id; raw must retain
{operation_id, operation, ref}. A normal receipt must match that invocation.
Receipts retain adapter, invocation_id, response_id, ref, raw and no_write.
Issued-without-result/unknown requires read-archive-state on demand. A pending
intent/query is acknowledged without another effect; reconcile its exact original
call. Only a not-archived readback or failed/no_write permits another archive.
Same receipts ACK; conflicts persist without reversing archived or business.
No background retry, queue, migration or unarchive is implemented.

## Entry authority and bounded successor control

`prepare` freezes one typed `entry_authority` in the plan digest:

- Standalone: `{kind: "standalone"}` with no discussion identity.
- Attached: exactly `kind`, `project_id`, `tree_id`, `topic_id`, `controller_ref`,
  `source_phase`, `stage`, `run_id` and `attempt_id`. `kind=dedicated-stage`
  names the handoff and eligible attempt, with source_phase=stage in {0,1}.
  `kind=wrapper-phase-run` names the `0→1` dedicated-grilling run and attempt,
  with source_phase=0 and stage=1.

The discussion adapter requires one eligible source. Prepare the handoff or
wrapper before its control plan. Execution `stage` selects the role and is not
overwritten by the topic's `current_phase`; that phase remains 0 throughout
wrapper Stage-1 work. A replacement attempt requires a fresh plan. Read current
binding/state before each action.

`receive` authenticates the exact plan source and attempt, same-path nonregressed
version and actual Git commit/hash. Same-stage requires its active handoff;
wrapper requires authorized completion-claimed/completion-pending/completed
output. Pending DW blocks delivery. Same-stage receipt freezes requirement
writes; wrapper claim already froze them. Accept before wrapper complete/finalize.
Finalization's topic revision increment preserves the receipt. `cancel` revokes
only the plan-bound attempt, never historical siblings or a newer retry;
received/accepted results cannot be cancelled through this action.

For an accepted dedicated Stage 0 still awaiting archive, prepare its `0→1`
Stage-1 plan in one `successor_control` context inside the existing checkpoint.
The root retains the old result and archive evidence; the successor keeps its
own plan, binding and delivery. No nested successor, queue or registry exists.
With two slots, select by exact `plan_id` / `attempt` or `delivery_digest` /
`input_digest`. For actions without such a field (including cancel or choose-dedicated), add `control_plan_id` to evidence. Ambiguous
or conflicting selectors fail; task recency is not a selector.

After claim/ready, actual source activation and fresh stop/call proof, use the
old delivery digest for successor-ready. The same ledger commit freezes the old
handoff and promotes Stage 1. Archive updates only that retired record and never
selects a business slot. Failed/unknown archive leaves Stage 1 able to proceed.
Cancelling an unactivated successor preserves the old accepted result; a verified
replacement authority can reuse its temporary slot. With no old dedicated
carrier, use the normal single context.


## Downstream execution

Continuous 0/1 authorization freezes source_phase, stages=[2,3,4], exact scope,
controller and latest published stage-entry checkpoint. Phase 0 has phase_result_id
null; Phase 1 requires success. Prepare, ready and activate each recheck controller,
scope, gate, impacts and checkpoint. No (0,3) Phase Run exists. Qualified explicit
low-complexity standalone implementation needs complete behavior/failure, acceptance,
scope and testing seam; it is unattached and performs no ledger write.

start-dispatch freezes verified binding, allowed_paths, protected_paths,
authority_digest, testing_basis, configuration and implementation_policy; dispatcher-bound records actual
native ref/attempt. Both modes retain the complete $implement and $tdd workflow.
Under default full, the dispatcher integrates Git; its first Execution Agent
implements a real production-boundary red/green slice, and later implementation
and repair edits also use allocations.
plan-execution reserves task_id, exact paths, read_only dependencies, behavior,
tests, git_operations=[] and execution-agent configuration before native spawn.
assign binds its actual agent_ref/task_id/allocation_digest. Expand directories and
name new files. Before each allocation, the Git adapter compares every
unallocated implementation path with the frozen baseline or latest accepted
Execution Agent fingerprint; unexplained edits block dispatch. Concurrent writers
cannot overlap; shared files are serial. During controlled dispatcher recovery,
the exact Controller-assumed snapshot may seed a replacement allocation after
historical unaccepted ownership is explicitly released; its eventual candidate
still requires accepted Execution Agent delivery for every changed path.
At native bind, Git rechecks the assigned paths and Git HEAD/index against the
prepared snapshot. The created child receives write authority only from that
successful bound receipt; its completed result must echo the bound release.
Execution Agents never mutate index, commits, branches, merges, worktrees or cleanup.
execution-result reports stopped, diff/hashes, tests and Git state; the Git adapter
compares actual allocation snapshot. accept-execution rechecks bytes and file modes. candidate-ready
in full mode requires at least one accepted Execution Agent, every changed implementation path
covered by the latest accepted allocation with matching final bytes and mode, all writers stopped,
frozen review-required checks and clean verified HEAD. The controller owns
two independent review axes on that exact candidate. Recovery proves all old writers
stopped, rechecks accepted hashes and revalidates remaining work. Preserve differences
on interruption/cancel; reconcile unknown native dispatch before replacement.
For a path with serial accepted allocations, recovery uses the last accepted
fingerprint as its current owner rather than rejecting its earlier history.

start-closure freezes candidate, dispatcher_ref, review, verification, binding,
implementation_paths, closure_paths, protected_paths and configuration. closure-bound
binds real ref/attempt. The Closure Agent changes only explicit documentation paths,
publishes and cleans up the same worktree. Code defects return to stage 3. The
controller verifies actual ancestry, scope and resource removal. A published merge
with partial cleanup remains cleanup-pending; cleanup-only never republishes.

select-configuration inputs role, required_capability, supported, user, frozen,
previous, receipt, can_override, inherited, upgrade_attempted, and optional
preferred and preference_reason. Supported entries
have model/effort/capability/cost/permission/visible_identity; unknown cost or capability is null; required_capability may also be null.
Use current target adapter evidence, explicit user choice and supported frozen choice.
For a Stage-3 dispatcher or Execution Agent with override support and no such choice,
the controller or dispatcher supplies an exact preferred pair and task-specific
preference_reason. The Stage-3 handoff requires the complete current native adapter
inventory, independently reads the account's visible paginated Codex `model/list` catalog,
and blocks a nondefault automatic choice while an account-visible default may be eligible.
The native declaration cannot establish that an account-visible default is unavailable;
such a mismatch blocks automatic fallback. The catalog intersection used for the
original selection is frozen with the handoff, so a newly listed default does not
rewrite an already prepared choice.
Select the pair from the
supported intersection not known to be inadequate with the default
pool: gpt-6-luna/high, gpt-6-luna/xhigh, gpt-6-sol/medium, gpt-6-sol/high and
gpt-6-sol/xhigh. A default with known capability below the role requirement is
not eligible. If that intersection is empty, select an exact pair from the
user's supported configurations with the reason recorded as a fallback. A missing
or unsupported preference blocks dispatch. For other roles, use capable lower known
cost only with comparable evidence, then actual inheritance or a controller decision.
Never invent numeric capability scores or prices from text descriptions. Unknown cost never means cheaper. Capability failure
allows one upgrade candidate; increased/unknown cost or permission/identity change
requires controller decision. Without override use actual runtime inheritance,
never a guessed model. Explicit user and frozen choices may lie outside the default
pool when supported. Disclose important roles; ordinary execution records suffice.

The foreground scripted runner alone advances while active. Confirmed input includes
controller_ref and per-stage model/reasoning_effort/selection_input. Handoffs contain
controller_ref, role_ref and control_checkpoint exactly
{controller_ref,stage,role_ref,state:"completed",role_kind}. Stage role_kind is
solution-designer, implementation-dispatcher or closure-agent. Stage 3 includes exact implementation_paths/closure_paths/protected_paths and
the strict review/final-verification projection below. Nonempty text checks alone
never establish reviewability or delivery.
Stage 4 includes changed_paths, same accepted candidate_commit/binding, merge_commit,
ancestry and both cleanup facts. Partial cleanup returns continue or technical_error.
continue returns to C's next action in the same foreground host; a running role
is waited on, and only current proof permits followup to its exact identity.
needs_input keeps that host while the Controller answers the exact pending
matter. Interactive carriers do not also advance stages while the runner owns
progression. Native role, carrier thread, host process and completed stage are
distinct identities and outcomes.

execution-dispatch-result reconciles a pending task_id/allocation_digest with unknown
(read-native-state) or authenticated not-created (release allocation). A late ref
cannot bind a released allocation. Never treat an empty ref as stopped proof.

## Bounded direct implementation

Only the Controller may select `semantic.implementation_policy` before Stage-3
Dispatcher launch. Omission is `{"mode":"full"}`; explicit full has only `mode`.
An explicit direct policy has exactly `mode:"direct"` and `assessment`. The
assessment contains `reference`, `controller_ref`, `requirement_identity`,
`scope_digest`, `baseline`, `responsibility`, `implementation_paths`, `test_paths`,
`behavior_ref`, `acceptance_ref`, `checks`, `testing_seam`, `conditions` and
`exclusions`. Bind the decision to the original Controller, frozen requirement,
exact authorized scope and baseline. Name exact located implementation/test files
within that scope and actual behavior, acceptance and focused-check evidence.
Assessment `checks` must name commands from the frozen validation plan's
`review_required` list; they do not replace that plan or final validation.

All four `conditions` must contain `satisfied:true` and a nonempty factual
`evidence`: `behavior_fixed` (behavior and acceptance settled, no design gap),
`single_responsibility` (one responsibility without cross-boundary change),
`focused_verification` (a sufficient focused seam without multi-party integration),
and `locations_known` (located changes without exploratory expansion).
Each `exclusions` entry must contain `present:false` and factual `evidence`:
`state_machine`, `concurrency`, `recovery`, `migration`, `public_interface`,
`cross_module_interface`, `data_format`, `permissions`, and `workflow_state`.
Unknown facts, a failed condition or any exclusion select full. File/line counts,
a claim that work is simple, or Dispatcher self-assessment never establish direct.
User-requested full takes priority. A malformed/conflicting explicit direct input
is rejected; correct it to full or supply existing evidence rather than silently
discarding it. Full needs no direct assessment. This feature itself changes
recovery and persisted contracts and therefore must be implemented through full.

The sealed handoff and start-dispatch attempt freeze this decision. Only Stage 3's
implementation-dispatcher semantic input accepts it; Stage 2/4 and Execution Agent
requests reject it. An interactive or discussion-attached Controller may select
direct when it can supply the frozen assessment. The foreground scripted carrier
uses full because its confirmed runner input has no original Controller direct
decision to verify. The Controller
briefly discloses the concrete reason within existing authorization, without a
new user approval. Scripts verify structure, identity, scope and evidence linkage;
they do not infer low semantic risk or test sufficiency from a diff. The Controller
reads the frozen plan and relevant production code, subject to independent review.

Direct dispatch requires both the Flow Worktree and target branch at the same
clean original baseline; existing unexplained
implementation bytes cannot acquire direct authority. Direct write authority
starts only after actual Dispatcher binding. `executions`
stays empty and plan-execution is rejected until conversion. The same Dispatcher
performs implement/tdd, checks and commits; the Controller remains a nonwriter.
Candidate evidence retains `direct_provenance` from the original causal native
result/stop, policy digest, Dispatcher ref/attempt, original baseline and actual
candidate. On the causal stopped observation, C freezes the real Git snapshot;
candidate-ready rejects any later byte or Git-state drift. Git verifies that snapshot,
clean exact HEAD, allowed cumulative paths and complete content/presence/mode
fingerprints against commit objects. Baseline/target ancestry, current target,
protected bytes and frozen review checks still apply. Direct start-dispatch freezes
`implementation_target_head` to the original baseline that was verified at admission;
it does not reread the target after that check. Target movement requires conversion to full
before integration. Candidate paths keep the original target-diff semantics, with
the original implementation baseline still required as an ancestor. Self-reported clean/hash,
child prose or an empty roster cannot substitute for these facts. Provenance
establishes authorized role and observed Git state, not forensic keyboard authorship;
unexplained external changes remain an anomaly.

Both independent review axes and final validation retain the same exact-candidate
checks. The Dispatcher cannot be either reviewer. Remediation first stops old
review activity and invalidates the old candidate; direct work continues only
while the original eligibility remains true. Every replacement candidate requires
new affected checks, both review axes and final validation. Target movement,
integration, conflicts or recovery cannot broaden direct authority.

### One-way conversion and inherited bytes

On loss of eligibility, the Dispatcher stops edits and reports the concrete new
fact. The Controller uses C `control` with action `escalate-implementation` in
`implementing`, after the original causal stopped receipt and all native calls,
allocations, reviews, validation and publication activity are reconciled. If a
candidate already entered review/validation, use existing invalidate-candidate
first. Evidence is `{dispatcher_ref,attempt,reference,reason,assessment_reference}`;
the authenticated Controller receipt matches `controller_ref` and `reference`.
C derives the original stop and snapshot and retains the exact control transaction.
Git checks the stopped snapshot's HEAD, branch, index and original base. If the
target advances after that stop, its old commit must remain an ancestor of the
current target; only this target field may differ from the stopped snapshot.
Git also checks
all allowed/protected content/presence/mode, including staged, unstaged and
untracked changes separately. Unknown or out-of-scope changes block rather than
becoming inherited authority.

Successful conversion records immutable assessment/revocation and snapshot history,
sets full and records continuation intent for the same Dispatcher, attempt,
Flow Worktree and original `git_baseline_commit`. Preserve dirty index, files
and commits; no reset, stash,
new Dispatcher or widened scope. Identical completed transactions ACK; drift or
conflicting replay is rejected. Direct cannot be reinstated in that attempt.
A snapshot preserves unaccepted bytes; it is never accepted implementation.
C advance inspects the original host and requires current same-ref resumability
before issuing continue-host; conversion itself is not a native continuation receipt.
Conversion while paused preserves pause and returns no follow-up action, including
on an identical retry. Only explicit resume permits inspection and continuation;
conversion cannot revive cancellation.

Only paths pending revalidation from that conversion, or retained direct-source
replacement recovery, may use explicit allocation `adopt_paths`. They must be a
subset of exact assigned paths and bind `adoption_snapshot_digest` and complete
inherited fingerprints. The real bound Execution Agent receives the cumulative
original-baseline change, behavior/acceptance target and tests. Its native payload
includes `inherited_implementation` with `baseline`, `snapshot`, `snapshot_digest`,
`adopt_paths`, `pending_paths` and `accepted:false`; inherited bytes still require
verification. It reports actual
new `changed_paths` separately from `adopted_paths`; result fingerprints cover
their union. Even without new edits, adoption needs its real stopped result,
checks of inherited behavior and matching current bytes/modes at acceptance.
Ordinary allocations have no adoption authority. Existing whole-delta, peer,
nonoverlap, bound-release and Git-unchanged checks remain mandatory.

Full candidate intake still requires real accepted Execution delivery for every
cumulatively changed path, including direct commits. A restored baseline path
needs no invented delivery; retain its provenance history. Dispatcher Git
integration waits for relevant writers to stop. Pause/resume retains policy and
exact identity only with unchanged evidence; cancel cannot be revived by conversion.
Unknown native or publication outcomes retain their original lookup path.

Unrecoverable Dispatcher replacement uses the controlled recovery below, not
ordinary conversion. A successor is full and never inherits direct eligibility.
Direct-origin ownership remains unaccepted until real Execution Agents adopt and
verify it; original baseline, independent review and final validation remain.
Historical ordinary allocation release/revalidation rules are unchanged.

## Prepared freshness and execution attribution

Before deciding a prepared plan, the attached caller compares its frozen identity
against the current document path, bytes and revision. A changed document invalidates
confirmation without creating a task. Retire a stale same-stage handoff and
prepare a fresh handoff baseline before preparing a new plan; do not reuse its
old payload or authorize a replacement attempt with the old plan.
This prospective check never replaces an already authenticated delivery receipt;
identical accepted receipts remain ACK-only after later phase/document changes.

The Git allocation snapshot covers every allowed implementation path, including
file presence, content and mode. At execution-result the adapter compares the whole
current delta to that snapshot before considering the assigned subset. Reported
changed_paths and file_hashes must exactly match the actual assigned delta. Unassigned
changes fail immediately. Changes in an active nonoverlapping peer allocation may
be received provisionally with pending_peer_refs; this is not acceptance. Collect
that peer's authenticated stopped result with matching paths, bytes and mode before
accept-execution. Recheck the full delta and every referenced peer result at acceptance;
missing or drifted attribution fails, preserving the worktree for reconciliation.
This allows nonoverlapping results to arrive in either order without silently attributing
one writer's changes to another. Serial follow-up allocations snapshot the current
complete state after the prior writer stopped.

## Stage-3 validation contract (schema 2)

`start-dispatch` requires `validation_plan` in addition to testing_basis. The
Controller derives and approves this exact plan from the accepted testing basis.
Both `review_required` and `final_required` are nonempty lists of unique checks
with `id`, `category`, `command`, bound absolute `cwd`, `pass_condition`,
`allowed_skips`, and `environment`. Review categories are focused/affected; final
categories are full/environment, including at least one full check. An environment
check names its required fingerprint; otherwise record an explicit
`environment_not_applicable` basis. Plan digests identify approved bytes, not
execution or semantic authority. Changing a plan is an existing authority anomaly.

C's bounded `control` channel accepts candidate-ready, review-converged,
validation-start, validation-result, validation-retry and invalidate-candidate.
Git enriches candidate-ready with actual clean HEAD, paths and fingerprints.
The candidate binds dispatcher ref/attempt, commit, expected_target_head, binding,
plan_digest and checks. C review-activity consumes exactly those review checks.
Each review axis binds candidate, expected_target_head, plan_digest, reviewer_ref,
accepted status and result_ref naming its real stopped native response. The
original Controller's convergence decision includes both raw native receipts.

validation-start records attempt_id, original dispatcher_ref/carrier_attempt,
candidate/target/plan/review digests, full required list and actual source snapshot
before issuing same-identity continue-host. Results contain each frozen check's
fields plus status, exit_code, start_commit/end_commit, environment_fingerprint,
output_ref and output_digest. C supplies the separate authenticated source receipt:
adapter/call_ref/response_ref/raw, with raw attempt_id, checks, original dispatcher_ref,
source_unchanged and stopped observations from actual tools. The caller must
authenticate these observations; model prose, a digest or a path is not proof.
The Controller reads raw output against pass_condition and allowed suite skips.
The entire required check cannot be skipped even when an internal suite skip is
allowed. Untrusted, unknown or incomplete observations never pass.

For a procedure that produces an explicit acceptance judgment rather than a
process exit code, freeze `category: environment` and `pass_condition: accepted`;
describe the procedure in `command` and bind its environment fingerprint. A
trusted `status: passed` observation may then retain `exit_code: null`. Other
conditions require an actual zero exit code; a nonzero exit, skipped/unknown
verdict, changed procedure or wrong fingerprint never becomes a pass.

State order is implementing → reviewable → reviewing → final-validation-pending
→ validating → deliverable. A failed/incomplete final result records
final-validation-failed. validation-retry names its latest failed attempt and
Controller reference; it preserves all previous attempts and repeats the complete
final list. invalidate-candidate names the old candidate, reason and Controller
reference, preserves validation_history, and returns to implementing only after
old calls/reviewers stop. An accepted delivery cannot be invalidated in place.

The strict verification projection contains candidate, expected_target_head,
validation_plan, plan_digest, review_digest, review_decision, attempts and
original dispatcher_ref. B receive and accept compare it against deliverable
control evidence; completed cannot manufacture candidate readiness. Stage-4 entry
and C publication reuse the strict validator and target checks. Implementation I
retains its final evidence; closure descendant D may change only closure_paths.
No implementation contract, tests, generated package or protected source belongs
to closure scope. After a recorded merge, reconcile publication/cleanup instead
of demanding a new validation or merge.

New records use runner 6, C workflow-progress-v10, B workflow-stage-transfer-v6
and control compatibility key 6 (JSON schema 4). Old executable records fail with
legacy_run_requires_original_runtime before writes or host calls. Embedded old
control keeps the ledger outer version and bytes; resume with its original pinned
package. Never migrate checks strings into passed evidence.

## Controlled Stage-3 recovery

Use C's `control` operation; retain its transaction journal and `handoff_progress.recovery`.
Read entry/package authority first, reconcile the original native calls, then
check current host identity and all old writers, and finally inspect real Git.
Running means wait. Idle or stopped requires fresh explicit same-identity
resumability before followup. A business block still requires recover-business.
Unknown identity/calls require lookup. Original host loss remains
await-host-recovery; no new app-server substitutes for that host. Stop/cancel
intent and existing publication reconciliation take precedence.

1. `prepare-dispatch-recovery` evidence names dispatcher_ref, attempt, reference,
   reason, stop_receipts and call_receipts. The Controller receipt repeats its
   controller_ref/reference. C resolves references to saved authenticated native
   observations and a complete current descendant lookup. The original stop
   response must prove resumable:false and dispatch_available:true. Child claims
   and stopped status alone do not qualify. Git freezes HEAD, branch, index, base,
   target and full allowed/protected fingerprints, including absence and mode.
   Accepted allocations must match original result fingerprints; unaccepted
   allocations retain ownership and require revalidation. The Controller may
   assume only the exact unallocated dispatcher changes. Protected/out-of-scope
   changes and ambiguous allocations block without cleaning or committing.
2. `dispatch-recovery-intent` takes recovery_id, snapshot_digest and decision.
   Decision has reference, authority_digest, attempt, remaining_paths and
   assume_paths (the exact dispatcher-owned paths). C saves one intent before
   returning invoke-recovery-host. Create only that prepared, read-only role.
   Repeated requests return lookup-recovery-host; unknown never creates again.
3. `dispatch-recovery-result` takes recovery_id and the actual receipt with
   adapter, call_ref, response_ref, intent_id, status, ref and raw. The C receipt
   is the same object. Status is ready, unknown or not-created. Ready carries a
   new real ref and raw write_authority:false; no writing is yet authorized.
   A confirmed not-created result permits retry of that same intent.
   A late original creation receipt may still be retained after host loss; it
   adds the prepared ref to recovery evidence and grants no activation authority.
4. Refresh the original host's complete stopped-writer lookup, including the
   prepared successor. `recover-dispatch` takes recovery_id, snapshot_digest,
   the identical decision and replacement_ref. C checks authority/stops/calls
   again; Git rejects any snapshot drift. Control atomically activates one new
   attempt and preserves prior refs, allocations and receipts. Exact repeats ACK;
   conflicts cannot activate again or clear a later block. Reconcile an interrupted
   control transaction through the original C record.

The successor receives ownership, remaining_paths and revalidate. Historical
allocations remain reserved until their original results are accepted or explicitly
released by the Controller after stopped ownership revalidation. Use release-recovery-allocation with recovery_id, snapshot_digest, agent_ref and reference; its Controller receipt repeats controller_ref/reference. Git must still match that allocation’s prepared fingerprints before control cancels its old reservation. Previous tests
remain diagnostic history. The new attempt must produce a clean reviewable
candidate, converge independent Standards/Spec review, then perform current final
validation. A recovered dirty tree is never candidate evidence. Deterministic
transport doubles prove admission/refusal only; real-host recovery remains a
separate acceptance claim, unsupported when the original host lacks that API.
