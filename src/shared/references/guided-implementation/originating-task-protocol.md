# Originating Task Protocol

Mechanical A/B execution uses [Workflow progression]({{resource:shared/references/workflow-progression.md}}). Supply semantic decisions and authenticated raw host responses; the shared adapter retains intents, exact attempts, receipts and recovery steps. The role, review and authorization rules below remain binding.

Before this role acts, execute [Package execution preflight]({{resource:shared/references/package-execution.md}}) using the inherited registration_input fields from the verified handoff. Pass them unchanged to entry_prepare.py with this role’s actual host identity and cwd; keep registration_identity as the frozen source identity. File evidence is reread by the adapter, while inline registry/context remain paired. Missing evidence stops before work. Inherit and verify the controller’s fixed package identity and check each selected action before its side effects.

The originating task binds the Flow Worktree, keeps at most one active Implementation Dispatcher, independently reviews its committed candidate, and passes
the retained flow to Stage 4. It does not implement code.

## Launch

For an inherited entry, confirm that every planning source is committed, list
the exact allowed implementation paths, and verify and reuse the Stage-2
handoff and Flow Worktree. A missing or invalid claimed binding is an anomaly
and never falls back to standalone.

For an explicit standalone entry, finish the standalone-authority gate in
`SKILL.md`, then call `start-worktree` once from the target `HEAD`. Freeze the
exact user request and implementation brief, record the returned base as the
standalone base and scope base, and use no planning source or protected source
unless the brief explicitly identifies one. The explicit invocation authorizes
only this bounded local implementation flow.

Launch one native Implementation Dispatcher in the verified Flow Worktree. For
an A/B handoff, use Workflow progression start and observe with the actual native receipt: they own
start-dispatch and dispatcher-bound, so do not invoke those transitions again.
The explicit standalone path without an A requirement retains those original
control primitives. Disclose current select-configuration evidence and reason. Include the
complete planning sources and dependency-ordered Tickets or the fixed
standalone brief, testing basis, flow mode, confirmed implementation scope,
existing-behavior changes, required collateral
changes, explicit out-of-scope items, documentation boundary and
remote-authority boundary in the launch prompt. Do not launch while a material
product, scope, behavior, architecture, compatibility, data, or testing-seam
decision remains unresolved.

## Intake

For foreground-carrier runs, use C's review-activity prepare/observe around each
independent Standards and Spec native call, retaining the exact candidate and
mechanical reviewer ref. Before waiting, continuing, pausing or closing, apply
the shared progression lifecycle contract. A running dispatcher receives wait;
a later business followup needs fresh original-identity proof. Keep the host
while a user decision or pause is pending, and include both review slots in the
native stop barrier before cancellation or final release.

A platform terminal result ends one execution turn; it does not complete Stage
3. Before acting on each result, the Originating Task records the exact HEAD
before and after the turn, verifies the Worktree Binding and Git state, and
classifies the result:

- `reviewable`: implementation scope is complete at a clean committed HEAD with
  all frozen review-required checks; independent review and final validation remain.
- `candidate`: the exact reviewed fixed point has the latest complete successful
  final attempt, authenticated raw results and no remaining planned work.
- `checkpoint`: planned work remains and the exact HEAD advanced to clean,
  committed, in-scope progress.
- `blocked`: a specific implementation-authority decision, changed protected
  source, or evidenced technical condition prevents the next edit. The amount
  of remaining work is not a blocker.
- `no-progress`: planned work remains, no specific blocker was reported, and
  the exact HEAD and clean worktree are unchanged.

Route `reviewable` to independent review; route `candidate` to final delivery acceptance. Continue a `checkpoint` in the same Implementation Dispatcher and worktree from the exact current HEAD and remaining
scope; this is ordinary continuation, not recovery. Route a material `blocked`
decision to the user and return an ordinary technical condition within the
confirmed scope to the same Implementation Dispatcher.

For `no-progress`, send one corrective continuation to the same task. Name the
next dependency-ready Ticket or exact remaining implementation slice and state
that its prior terminal result did not satisfy Stage 3. If the next result is
again `no-progress`, classify that executor as stalled; do not keep issuing
continuations to it.

Maintain at most one active Implementation Dispatcher. Progression chooses the
next action: wait for a running original; continue an explicitly resumable original
only after its calls and business block are reconciled. A terminal result alone
does not prove resumability or authorize a replacement.

If the original cannot resume and the retained host proves all old writers stopped,
use the controlled recovery sequence in
[Workflow Control Protocol](workflow-control-protocol.md). It accepts owned committed
and uncommitted work through a frozen Git snapshot, exact Controller decision and
one durable dispatch intent. The successor remains read-only until recover-dispatch
activates its real ref. Preserve ambiguous calls and unknown identity for lookup;
host loss remains await-host-recovery. Ordinary in-scope recovery requires the
Controller's decision, not a new end-user permission gate. Scope, permission or
requirement changes still require the existing user decision.

## Accept

This review contract is authoritative. Require a clean committed candidate plus
focused and affected checks. The Originating Task pins the exact candidate commit and
the expected target-branch commit as one review fixed point, independently
inspects the candidate diff against that fixed point, and passes that same fixed
point and candidate to both Standards and Spec review axes. Give the Spec axis
the committed planning artifacts for an inherited flow or the exact fixed
standalone brief. It dispatches the Standards and Spec review axes independently
and records their results
separately. Do not accept a candidate until both axes correspond to that exact
fixed point and candidate commit and have no unresolved actionable findings.
Then record `review-converged` with semantic result references matching the two
stopped native responses. Persist one `validation-start` attempt and consume its
followup on the original dispatcher. Only the latest complete passed final attempt
permits formal B acceptance; inspect raw outputs and every frozen check condition.
Reject an unplanned feature, behavior change, deletion, replacement, side
effect, or optional adjacent improvement even when its tests pass.
Return every actionable test or review finding to the same Implementation Dispatcher and verified worktree; the originating task does not edit
the implementation or create a replacement for ordinary remediation. Any
replacement candidate invalidates both review results. The Originating Task
reruns both axes against that replacement candidate before accepting it. Only
the Originating Task accepts the candidate; Stage 4 integrates it. Route only a material
unresolved decision to the user. If the target advanced without changing a
source path, the same Implementation Dispatcher merges that target, runs
affected checks, and commits a replacement candidate. The Originating
Task then establishes its exact replacement fixed point and reruns both axes.

## Diagnosis-first remediation

Mark the next remediation as diagnosis-first when any observable trigger is
present: the same failure mechanism is reported again after a fix; a
same-class regression appears in another affected path; or successive
review/test outcomes overturn the same implementation approach. Continue to the
same Implementation Dispatcher and Flow Worktree, carrying the trigger and
the prior candidate and finding identities in the request.

Before another edit, require execution evidence that: (a) compares the prior
failure and fix side by side; (b) explains why that fix did not address the
mechanism; (c) identifies the smallest effective validation at the affected
real boundary; and (d) lists the affected sibling paths inspected and whether
the mechanism applies to each. The task then runs that focused validation,
repairs in scope, reruns the affected checks, and commits the
replacement candidate as usual.

If diagnosis shows that the accepted scope or plan is insufficient, stop
through the existing implementation-authority or anomaly decision before
changing code. An ordinary technical defect continues in the same task and
worktree. Do not create a diagnostic document, impose a fixed remediation
round gate, spawn an automatic replacement, or add a new Workflow Stage; the
stalled-task replacement exception above still requires the complete
original-host recovery proof.

## Retain and hand off

After accepting the exact candidate, verify that it is still the clean Flow
Worktree `HEAD`. Do not update the target branch and do not remove the worktree
or branch. Pass its exact binding, candidate commit, planning merge or
standalone base commit,
implementation and closure path scopes, protected
requirement-source paths, verification/review evidence, exact closure-document
updates, and flow mode to Stage 4. No claim, lease, proposal receipt, or closure
checkpoint is carried forward.

When Stage 3 is attached to a discussion topic, also pass the exact
project/tree/topic identity, actor binding, completed phase-3 result id and the
ledger/topic revisions returned by the required final `read-topic`; an
unattached Stage-2 or standalone Stage-3 flow passes `none` for that entire
group. Local completion grants no remote-write authority.
