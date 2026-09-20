# Stage transfer and dispatch

Use the pinned `scripts/stage_handoff.py` and `scripts/stage_dispatch.py`.
Both take one bounded strict JSON object on stdin. Protocol is
`workflow-stage-transfer-v2`. Success is `{ok:true,result:...}` (exit 0);
failure is `{ok:false,error:{code,operation,message,downstream_ready:false,
completed_evidence,recovery}}` (exit 1). A digest detects changes, never
authenticates a caller, receipt, user decision or semantic conclusion.

## Fixed sequence and owners

Read the complete [entry contract]({{resource:shared/references/entry-preparation.md}})
and [requirement contract]({{resource:shared/references/requirement-preparation.md}}).
The controller retains semantic decisions and acceptance. A scripted carrier
retains its external controller; a dispatcher may prepare only its authorized
execution allocations. 0/1 dedicated tasks stay user-visible tasks; downstream
native roles stay native. Never replace a native child with a task or CLI carrier.

C's [Workflow progression]({{resource:shared/references/workflow-progression.md}})
now owns this fixed sequence. Use its start/advance/observe/decide interface
instead of manually calling and copying every A/B object. It verifies A,
prepares/verifies handoff, persists dispatch record/checkpoint before the host
call, saves the raw response before binding, and performs receive/accept in order.
The host still executes/authenticates the actual call; the controller still owns
semantic decisions, phase authorization and acceptance. The exact individual
B interfaces below remain the underlying contract and diagnostic surface.

Python never invokes an unavailable native tool. The current native orchestration
adapter owns argument mapping, actual identity, stopped-writer observations and
governance disclosures/bookkeeping. Its private schema/version/session is not
copied into B. Effects and host_call are plans, never execution receipts.

Stage 2's pre-publication child completion is a planning candidate. Its existing
stage owner publishes once, retains that receipt, then submits completed transfer
evidence. B verifies planning/publication commits, local paths and the retained
flow. It does not publish, re-review prose, certify test execution or validate
remote Matt artifact URLs. Stage 4 publication/cleanup stay D-owned.

C's `receive-publication` is stage-owner composition: it preserves the original
stopped native candidate and raw host receipt separately from the later verified
Git publication/cleanup and original readiness decision. It is not a new native
response. B's existing receive and Controller acceptance still apply. Stage 4 B
`candidate_commit` is the accepted implementation, while D records the final
closure-document tip separately. No Stage 1→2 delivery schema or permission changes.

The existing foreground runner loop/schema remains C-owned; C consumes B's
portable result objects. Old records are not upgraded. Qualified standalone
implementation without an A requirement keeps its existing standalone-entry
gate/control path; never invent a requirement or attachment to fit B.

## Handoff requests

`prepare` requires exactly:

| Field | Source/meaning |
| --- | --- |
| protocol, operation | Protocol above; prepare |
| entry, expected_entry | A resolve request and complete current result |
| stage, role | 0–4 and existing role; stage 3 also allows execution-agent |
| requirement | Complete successful A document/frozen/attached result |
| predecessor | Null for independent entry, otherwise full B accepted result |
| target | Exact `{repository,branch}` |
| delivery | Null for source already on target, otherwise proof below |
| binding | Null at 0/1; actual Flow Worktree binding downstream |
| scope | `{baseline,owned_paths,protected_paths,implementation_paths,closure_paths}` |
| authorization | `{reference,flow_mode,scope_digest}` from confirmed checkpoint |
| configuration | Existing select-configuration input for the actual target role |
| semantic | `{objective,testing_basis,completion_criteria,constraints}` |

Flow mode is stepwise or continuous. Scope digest uses entry_prepare.digest;
the saved controller decision binds it, not a new permission token. Completion
criteria and constraints are nonempty text lists. Path lists are sorted, unique
exact repository-relative files; directories must be expanded and new files named.
Baseline is current downstream HEAD, except Stage 4 retains the accepted Stage-3
scope baseline and separately checks its candidate against current HEAD. Protected
paths cannot overlap current write paths; implementation and closure ownership
cannot overlap. At Stage 3, closure_paths is a future authorization, not a write
allocation: planning documents remain protected during implementation even when
listed for later closure. Stage 2→3 preserves that approved closure list. Stage 4
may remove only those previously approved closure paths from inherited protection;
all other protected paths remain protected. Frozen requirements can never be
closure-owned. Do not derive authorized scope from observed diff contents.

For dedicated dispatch with two control slots, authorization also requires
`control_plan_id` from the exact confirmed ledger plan. B selects that slot before
checking stage, requirement, configuration or reservation, while passing the full
context to the ledger. Missing/ambiguous/wrong selection does not promote the
successor, replace the predecessor, or archive anything. A single-slot caller may
also pin control_plan_id; omitting it retains the existing single-slot interface.

For attached stages 2–4, authorization additionally requires
`phase:{run_id,attempt_id}` from the original Phase Run. B reads that exact run:
Stage 2 must name its wrapper's source checkpoint; later stages must already
belong to the authorized active current carrier. Native Stage-2 binding authorizes
that precise returned designer in the existing wrapper, without claim/ready or
activation. Those original carrier/source steps remain mandatory before work.

Attached 0/1 may use requirement:null: current A entry/read-topic supplies the
mutable document identity, not phase activation or completion. Downstream work
requires a successful frozen Git source. Non-Git Stage 0 keeps snapshot authority.

The sealed stage-input retains the whole request, controller_ref,
requirement_identity, source_commit, delivery_facts and selection.
`verify` and `render` take exactly `{protocol,operation,handoff}`. Verify checks
current facts without replacing pins or authorization. Render returns payload
and readable JSON text; it does not revalidate or authorize execution. Embed this
unchanged machine payload in the existing role-specific bootstrap instructions;
preserve the complete role protocol and semantic readiness evidence.
Render also accepts a complete B accepted result and returns a completion payload
and text. For stages 2–4 it additionally returns the existing runner-compatible
stage_result (including handoff_json and control_checkpoint), avoiding manual
footer/hash/identity reconstruction. Rendering historical acceptance does not
replace a successor's freshness verification.
The Stage-3 completion projection's protected_paths is prepared for Stage 4:
it excludes only the already-approved closure_paths. Its embedded accepted_transfer
keeps the full Stage-3 protected scope for implementation intake/acceptance checks.

## Separate requirement delivery producer

B never merges, cherry-picks or copies requirements. It checks target Git objects
and actual receiver bytes. A partial completed_evidence is never a frozen source.
A discussion CP alone is not target-branch delivery.

If source and delivery commits differ, proof is exactly `{commit,paths,receipt}`
from the authenticated delivery owner. The reachable delivery commit must have
one parent, change only those Markdown paths, include the requirement, and match
the source commit for every listed document. B preserves both commits and verifies
all owned documents at the explicit target independently of the source entry.
The shared read-only target check requires the frozen Git blobs, regular
non-executable working files and their exact stage-0 index entries at receive,
accept and successor freshness checks.
The source commit need not enter target history, and the delivery commit need not
enter detached source history. Use the Stage-1
[delivery producer]({{resource:shared/references/requirement-delivery.md}}) through C
to establish this unchanged proof. Matching bytes without provenance is insufficient.

Missing delivery blocks downstream readiness. Recheck after worktree creation
to detect target movement. Unrelated primary-checkout modifications are preserved
and are not an obstacle to read-only delivery verification.

## Dispatch control and receipts

Prepare takes `{protocol,operation,handoff,control}`. Other operations take
`{protocol,operation,record,control}` plus the fields below. Control is exactly
`{context,discussion}`. Context is the existing current control checkpoint.
Discussion is null for conversation/runner execution control; attached dedicated
0/1 must supply its saved mutation envelope without operation/action:
protocol_version, project_path, project_id, tree_id, actor_topic_id,
actor_conversation_ref, expected_ledger_revision, expected_topic_revision,
idempotency_key. Persist this exact intent before its mutation.

B uses the original discussion adapter. `read-topic.workflow_control` exposes
its authoritative readback, and topic_document_path supplies the document path.
No second ledger exists. A repeated dedicated reservation returns unknown without
host_call: recover the saved request and inspect the exact attempt before launch.

Bind/reconcile add receipt with exactly:

```text
{adapter,receipt_ref,request_digest,attempt,role,event,status,ref,pending_id,configuration,raw}
```

Raw retains the original tool response. Event is create for bind, lookup for
reconcile, result for receive. Creation status is ready, pending, unknown or
not-created. Only ready carries ref, and it cannot also carry pending_id.
For a resolved identity, configuration is the host-verified `{model,effort}` and
must match the frozen selection; unresolved identities use null. Review and test
references must come from the controller's actual verified evidence, not child
assertions that independent reviewers existed or checks passed.
Transport failure without proof of non-creation is unknown. The trusted host
authenticates the request/response relationship before calling Python; JSON,
hashes, receipt strings and self-reported booleans cannot establish it. This CLI
is a trusted-controller boundary, not an authenticator for untrusted receipts.

Attached ready 0/1 binding reads the exact handoff/wrapper attempt, applies
bind-handoff or authorize-phase-carrier if unbound, then records creation-result.
It does not claim/accept the handoff on behalf of the child or activate a phase.
The carrier retains its own claim/ready obligations. A binding failure preserves
the host creation and completed binding receipts in error.completed_evidence,
always downstream_ready=false. Reconcile this attempt; never recreate the task.

Unknown/pending outcomes retain the attempt. Exact host lookup is required;
if unavailable, remain unknown. Never select a candidate by title, recency,
timestamp or child text. Not-created cancels only the pending attempt/allocation;
late receipts cannot revive it. Identical replay ACKs, conflicting content fails.
After dispatcher cancellation, reconcile still accepts exact lookup evidence for
an unbound execution allocation: unknown/pending retains it; not-created releases
it through the existing execution-dispatch-result transition. A repeated proven
release ACKs, including after a lost response. Late ready cannot bind or revive
the cancelled allocation. The dispatcher remains cancelled; recovery still needs
the existing proof that every old writer stopped.
Save a known identity even if source later drifts; it is not permission to work
on stale input. Replacement authorization stays with the controller.

## Results and acceptance

Receive adds receipt and `result={delivery_id,status,payload}`. Business status
is completed, continue, needs_input or technical_error. Completed requires host
status stopped according to the adapter's stopped-writer contract, not idle or
single-turn completion. The host must reconcile/stop writers before reporting it.
Noncompleted observations retain the same bound attempt.

Completed payloads are strict:

| Stage/role | Fields |
| --- | --- |
| 0/1 | artifacts, checks, requirement (successful A result), delivery (proof or null) |
| 2 | artifacts, checks, planning_commit, planning_merge_commit, planning_paths |
| 3 dispatcher | artifacts, checks, candidate_commit, review, verification |
| execution-agent | changed_paths, file_hashes, tests |
| 4 | artifacts, checks, candidate_commit, merge_commit, changed_paths, cleanup |

Artifacts/checks are nonempty text lists, not proof that commands ran. Review uses
the existing two-independent-reviewer schema on the exact candidate. Execution
results use actual allocation snapshots and peer attribution checks. Stage 1
completion requires target delivery. Stage 4 cleanup is verified from Git and
resources; partial cleanup preserves publication and does not permit republishing.

Accept adds `decision={reference,delivery_digest}` from the controller's confirmed
checkpoint. Received Git facts are checked again. Only the sealed accepted result
can be a successor predecessor. Allocation acceptance is not stage completion.
Accepted replay ACKs without mutation; prospective consumption separately checks
freshness. B never dispatches a successor, archives tasks or runs a recovery loop.

## Compatibility and verification

Discussion source and Phase reads use A's pinned `discussion_project` through
prepare, bind, receive and accept. Successors retain the original project/tree/topic.
Control envelopes must already name that project and attachment; mismatches are
rejected, not rewritten. Flow bindings prove execution Git identity and share the
discussion's common directory; they do not select its owner. Stage-4 intake reads
the surviving discussion project without refreshing the removed execution checkout.

Packages add stage_transfer=workflow-stage-transfer-v2 to their exact compatibility
key. Old runs keep original pinned runtime/records; no migration. Full A evidence
stays outside strict control context. Only necessary design and launch transitions
extend the existing control checkpoint.

Real Git/A/ledger tests validate local evidence. Fixture host responses validate
the adapter contract, not actual native tool behavior. Real creation, asynchronous
ready resolution, stopped-writer proof and lost-response lookup need separate
field acceptance. Do not launch business tasks just to manufacture that evidence.
