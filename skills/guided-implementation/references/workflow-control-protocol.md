# Workflow Control Protocol

The Workflow Controller retains confirmation, routing, result acceptance and
recovery. Dedicated Discussion Task and Dedicated Problem Framing Task are visible
0/1 carriers. Stage 2 uses one native solution-designer; stage 3 one Implementation
Dispatcher and optional Execution Agents; stage 4 one Closure Agent. A split child
is one independent Workflow Controller and inherits no preferences or authority.

`workflow_control.py` accepts bounded strict JSON with schema_version=1, action,
actor_ref, context and evidence. Context contains schema_version, controller_ref,
topic_ref, stage, carrier, preference, flow_authority, requirement_identity and
handoff_progress. The caller authenticates actor_ref and persists the returned
context in the existing conversation or runner. No new registry or monitor exists.
Effects are plans, never tool receipts. Use actual native/task identities and
termination evidence; reconcile unknown outcomes before retry.

For Git-dependent transitions use read-only `workflow_control_git.py` with exact
envelope `{repository:<absolute cwd>,baseline:<frozen commit>,request:<request>}`.
It verifies actual binding, diff, file bytes, index, commits, publication ancestry
and cleanup. Self-reported verified_commit_hash or clean booleans are insufficient.
The attached `discussion_protocol.py workflow-control` uses the existing mutation
envelope plus action/evidence, checks real gate and handoff, and verifies Git receive.
dedicated-stage permits only current-stage 0/1 requirement writing and bounded split
proposals. It does not replace the active controller Conversation Binding. Cancellation
revokes its write authority; next-stage rebinding supersedes the previous carrier.

Create or inherit one requirement document before substantive questions. `prepare`
freezes target, project, title, missing_context, configuration, next_step, archive_ref
(null before creation), gate_open and plan_id without creating a task. Disclose stage,
controller, document path/hash/version, exact target/title, task_count=1, missing
context and configuration reason/receipt. One combined confirmation covers stage entry
and task creation; no preliminary migration offer. `decide` takes plan_id and intent
confirm, stage-current, topic-current or cancel. Refusal reuses the document and
suppresses repeated offers for that scope. choose-dedicated is an explicit new choice.
A dedicated carrier never recursively creates another. A child first accepts its
handoff; a closed gate blocks preparation, creation, writes and progression until
a later user-triggered continuation rechecks dependencies.

creation-result binds current attempt and only an actual ready task ref. Pending
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
compares actual allocation snapshot. accept-execution rechecks bytes. candidate
requires all writers stopped/accepted and clean verified HEAD. The controller owns
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
solution-designer, implementation-dispatcher or closure-agent. Stage 3 includes exact
implementation_paths/closure_paths/protected_paths, independent review axes
{candidate,reviewer_ref,status:"accepted"}, verification {candidate,checks}.
Stage 4 includes changed_paths, same accepted candidate_commit/binding, merge_commit,
ancestry and both cleanup facts. Partial cleanup returns continue or technical_error.
continue resumes the same session; needs_input returns to the controller. Interactive
carriers do not also advance stages while the runner owns progression.

execution-dispatch-result reconciles a pending task_id/allocation_digest with unknown
(read-native-state) or authenticated not-created (release allocation). A late ref
cannot bind a released allocation. Never treat an empty ref as stopped proof.

## Prepared freshness and execution attribution

Before deciding a prepared plan, the attached caller compares its frozen identity
against the current document path, bytes and revision. A changed document invalidates
confirmation without creating a task; a new prepare freezes the updated identity.
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
