# Workflow Control Protocol

Mechanical A/B execution uses [Workflow progression](../workflow-progression.md). Supply semantic decisions and authenticated raw host responses; the shared adapter retains intents, exact attempts, receipts and recovery steps. The role, review and authorization rules below remain binding.

The Workflow Controller retains confirmation, routing, result acceptance and
recovery. Dedicated Discussion Task and Dedicated Problem Framing Task are visible
0/1 carriers. Stage 2 uses one native solution-designer; stage 3 one Implementation
Dispatcher and optional Execution Agents; stage 4 one Closure Agent. A split child
is one independent Workflow Controller and inherits no preferences or authority.

`workflow_control.py` accepts bounded strict JSON with schema_version=2, action,
actor_ref, context and evidence. Context contains schema_version, controller_ref,
topic_ref, stage, carrier, preference, flow_authority, requirement_identity and
handoff_progress, plus at most one optional successor_control slot. The caller authenticates actor_ref and persists the returned
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
confirmed and archive_ref. Verify frozen input and binding and attached source
activation before archive. Phase advancement retains the pending old carrier and
accepted delivery. archive-result has ref/status; unknown/requested means readback
before mutation. Archive failure never restarts a successor already ready.

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
`input_digest`. For actions without such a field (including cancel, archive,
archive-result or choose-dedicated), add `control_plan_id` to evidence. Ambiguous
or conflicting selectors fail; task recency is not a selector.

After claim/ready and real source activation, use the old delivery's digest for
`successor-ready`, then archive the frozen old task. Only an actual
`archive-result(status=archived)` promotes the successor to the root. Unknown or
failed archive retains both slots and prevents further successor dispatch;
recover archive without recreating the active task. Cancelling the pending
successor preserves the old accepted result; a verified replacement authority
can reuse the bounded slot after cancellation/failure. With no old dedicated
carrier, use the normal single context.

## Downstream execution

Continuous 0/1 authorization freezes source_phase, stages=[2,3,4], exact scope,
controller and latest published stage-entry checkpoint. Phase 0 has phase_result_id
null; Phase 1 requires success. Prepare, ready and activate each recheck controller,
scope, gate, impacts and checkpoint. No (0,3) Phase Run exists. Qualified explicit
low-complexity standalone implementation needs complete behavior/failure, acceptance,
scope and testing seam; it is unattached and performs no ledger write.

start-dispatch freezes verified binding, allowed_paths, protected_paths,
authority_digest, testing_basis and configuration; dispatcher-bound records actual
native ref/attempt. The dispatcher alone integrates Git and reads full $implement
and $tdd, beginning with a real production-boundary red/green slice.
plan-execution reserves task_id, exact paths, read_only dependencies, behavior,
tests, git_operations=[] and execution-agent configuration before native spawn.
assign binds its actual agent_ref/task_id/allocation_digest. Expand directories and
name new files. Concurrent writers cannot overlap; shared files are serial.
Execution Agents never mutate index, commits, branches, merges, worktrees or cleanup.
execution-result reports stopped, diff/hashes, tests and Git state; the Git adapter
compares actual allocation snapshot. accept-execution rechecks bytes. candidate-ready
requires all writers stopped/accepted, frozen review-required checks and clean verified HEAD. The controller owns
two independent review axes on that exact candidate. Recovery proves all old writers
stopped, rechecks accepted hashes and revalidates remaining work. Preserve differences
on interruption/cancel; reconcile unknown native dispatch before replacement.

start-closure freezes candidate, dispatcher_ref, review, verification, binding,
implementation_paths, closure_paths, protected_paths and configuration. closure-bound
binds real ref/attempt. The Closure Agent changes only explicit documentation paths,
publishes and cleans up the same worktree. Code defects return to stage 3. The
controller verifies actual ancestry, scope and resource removal. A published merge
with partial cleanup remains cleanup-pending; cleanup-only never republishes.

select-configuration inputs role, required_capability, supported, user, frozen,
previous, receipt, can_override, inherited, upgrade_attempted. Supported entries
have model/effort/capability/cost/permission/visible_identity; unknown cost or capability is null; required_capability may also be null.
Use current target adapter evidence, explicit user choice, supported frozen choice,
then capable lower known cost only with comparable evidence. Otherwise retain a supported frozen/user choice or actual inheritance; if none is available, request a controller decision. Never invent numeric capability scores or prices from text descriptions. Unknown cost never means cheaper. Capability failure
allows one upgrade candidate; increased/unknown cost or permission/identity change
requires controller decision. Without override use actual runtime inheritance,
never a guessed model. Disclose important roles; ordinary execution records suffice.

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

New records use runner 5, C workflow-progress-v7, B workflow-stage-transfer-v3
and control schema 2. Old executable records fail with
legacy_run_requires_original_runtime before writes or host calls. Embedded old
control keeps the ledger outer version and bytes; resume with its original pinned
package. Never migrate checks strings into passed evidence.
