# Workflow Pipeline

This glossary describes a Codex engineering workflow whose ordinary Stages may
run independently or attach to a durable design discussion. This glossary owns
shared terminology; executable rules remain in the Skills and architectural
decisions remain under `docs/adr/`.

## Workflow progression

**Workflow Stage**:
One user-facing unit of work: 0 Discussion, 1 Problem Framing, 2 Solution
Design, 3 Guided Implementation, or 4 Change Closure. A Stage is work being
performed, not durable discussion state.
_Avoid_: Phase, when referring to the executable workflow unit

**Standalone Stage**:
A Workflow Stage performed without one uniquely verified Discussion Topic
attached. It has no discussion lifecycle state.

**Discussion-attached Stage**:
A Workflow Stage associated with one verified Discussion Topic and its active
Conversation Binding. Its Stage responsibilities remain distinct from the
Topic's lifecycle record.

**Discussion Phase**:
The recorded 0–4 lifecycle position of a durable Discussion Topic. A Phase
exists only in discussion context and may record a Workflow Stage as not
applicable rather than performed.
_Avoid_: Workflow Stage

**Phase Run**:
The durable, topic-local coordination record for one attempt to move a
Discussion Topic along a legal Phase transition. It records lifecycle authority
and outcome, not Git worktree execution.
_Avoid_: Implementation run, worktree run

**Change Closure**:
The Stage 4 responsibility of reconciling closure-owned documentation and
publishing and verifying the bound disposition of a Flow Worktree after a verified
implementation result. It never repairs implementation code.
_Avoid_: Implementation repair

**Flow Worktree**:
The one isolated Git worktree selected and bound at Stage 2, or by a standalone
Stage 3/4, for one delivery. Suitable existing workspaces are reused before creation;
reused and host-managed workspaces remain after delivery, while script-created
temporary resources follow their recorded deletion disposition. Stage 2 may publish its planning
checkpoint without ending the Flow Worktree. Stage 3 uses that fixed implementation
baseline throughout work and review; Stage 4 handles the current target only at
final publication. An unpublished final integration recovery retains acceptance
history and reuses the same Dispatcher/worktree for revalidation.
_Avoid_: Stage-2 worktree, implementation worktree, closure worktree

## Discussion continuity

**Discussion Topic**:
The durable unit of design discussion whose identity, document, decisions, and
lifecycle can continue across Codex tasks.
_Avoid_: Task, conversation

**Discussion Handoff**:
A durable transfer that authorizes another Codex task to continue a Discussion
Topic or own a child Discussion Topic.
_Avoid_: Stage Handoff

**Stage Handoff**:
The explicit completion evidence from one Workflow Stage used to enter the next
Workflow Stage.
_Avoid_: Discussion Handoff

**Downstream Scope Manifest**:
The sealed, per-file Stage-2 design result that assigns implementation or closure
ownership, operation, kind and committed Spec/Ticket basis. Its two path lists
are derived views. Before design finishes, null lists mean unresolved; an
explicit empty sealed list means no files for that owner. Generated entries
also bind a source-to-outputs inventory that is rechecked with the manifest.
_Avoid_: Launch path guess, observed Git diff as authority

**Scope Authority**:
The Controller-sealed current revision of the downstream manifest, requirement,
planning candidate and Flow Worktree binding. It supplements the immutable
Stage-2 launch and preserves earlier revisions as history. A proposed manifest
does not grant write permission.
_Avoid_: Rewritten launch scope

**Implementation Ticket**:
A delivery slice owning behavior, acceptance criteria and prerequisite dependencies.
Its function execution plan refines internal execution without replacing that contract.
_Avoid_: Function task, function plan as acceptance contract

**Function Execution Plan**:
The versioned internal plan for a Ticket's concrete functions, their capabilities,
approach, input/output and failure behavior, dependencies and shared verifications.
_Avoid_: Independent workflow, per-function acceptance contract

**Internal Plan Revision**:
A change owned by the original Workflow Controller to a function execution plan
within the accepted behavior contract and business responsibility boundary.
_Avoid_: Dispatcher plan edit, requirement change, old-run migration

**Behavior Slice**:
A complete observable behavior within an Implementation Ticket, with its
acceptance criteria, dependencies and verification boundary.
_Avoid_: Function count, independent Ticket

**Evidence Claim**:
An immutable review conclusion retaining its original candidate, verified source,
scope and stopped reviewer evidence.
_Avoid_: Rewritten report, current candidate approval

**Target Applicability**:
The verified relationship that permits an original Evidence Claim to cover a
later candidate when its contract, dependencies and relevant bytes remain valid.
_Avoid_: Copied approval, report candidate rewrite

**Coverage Certificate**:
The complete review coverage for one candidate, combining applicable local
Behavior Slice, Ticket acceptance and independent delivery review claims.
_Avoid_: Final validation, collection of passing reports

**Current Window**:
The bounded active workflow facts needed for current permissions and recovery.
_Avoid_: Complete run history, second control authority

**Committed Archive**:
Closed workflow facts retained with their original authority and evidence for
exact replay and recovery after leaving the Current Window.
_Avoid_: Deleted evidence, newly granted permission

**Pre-Stage-3 Scope Correction**:
The original Controller may correct an accepted Stage-2 manifest only before
any saved Stage-3 preparation, launch intent or session. Later omissions block
the affected work; they do not roll back the workflow or create another writer
attempt. Old runs retain their original pinned runtime.
_Avoid_: Retroactive path authorization

**Planning Complete**:
The accepted Stage-2 planning result, independent of whether implementation may
start. **Handoff Ready** is the stricter derived state requiring sealed scope,
verified publication, stopped writers, Controller acceptance and applicable
Phase Run finalization.
_Avoid_: Accepted means dispatchable

**Conversation Binding**:
The recorded association granting one Codex task or conversation active
authority over a Discussion Topic.
_Avoid_: Worktree Binding

**Topic Dependency**:
A requirements-stage prerequisite owned by its dependent Discussion Topic. It
names one same-tree prerequisite and is mutable only in Discussion Phase 0 or
1; it is not a structural relation or an implementation dependency.
_Avoid_: Parent relation, implementation dependency

**Topic Gate**:
The derived Phase-0/1 permission to publish stage-entry authority or advance a
topic. It is closed while any active Topic Dependency is closed. A closed gate
allows discussion of unaffected requirements, while dependent conclusions stay
unresolved. It is checked when the user asks to continue and is not consulted
by Workflow Stages 2–4.
_Avoid_: Background monitor, implementation blocker

## Workflow roles

**Workflow Controller**:
The original authority retaining user decisions, routing, acceptance and internal
function-plan revision for one workflow.
_Avoid_: Implementation Dispatcher, foreground carrier

**Phase Source Task**:
The Codex task that holds source-side authority for a Phase Run: it authorizes
activation and accepts and finalizes the result.
_Avoid_: Source Task without a Phase Run context, Phase Carrier

**Phase Carrier**:
The Codex task or actor authorized to perform the target Workflow Stage work for
a Phase Run and submit its completion claim. A route may assign the same task
as both Phase Source Task and Phase Carrier.
_Avoid_: Carrier without a Phase Run context, Phase Source Task

**Originating Task**:
The Stage 3 Codex task that owns user decisions, supervises isolated execution,
and accepts the implementation candidate for handoff to Stage 4. It does not
implement the change or perform final publication.
_Avoid_: Implementation Dispatcher

**Implementation Dispatcher**:
The unique native Stage 3 child that schedules and accepts real Execution Agent
deliveries, validates and integrates a candidate in the one Flow Worktree. In new
protocol runs it owns neither implementation edits nor function-plan revision.
_Avoid_: Workflow Controller, Execution Agent

**Execution Agent**:
A native implementation child with current function-plan references, an exact file
allocation, behavioral target and testing requirement. It owns implementation and
repair bytes, with no planning or Git mutation authority.
_Avoid_: Implementation Dispatcher

**Closure Agent**:
The native Stage 4 child receiving the accepted candidate, review, verification,
binding and exact documentation scope. It publishes and cleans up; implementation
problems return to Stage 3.
_Avoid_: Workflow Controller

**Worktree Binding**:
The verified Git identity that ties one Flow Worktree to its repository, branch,
target, committed ancestry and evidence-backed retain/delete lifecycle across Stage
handoffs and retries. Its origin and management mode do not alone grant deletion authority.
_Avoid_: Conversation Binding

Workflow Controller retains confirmation, routing and acceptance. Dedicated Discussion Task and Dedicated Problem Framing Task are visible 0/1 carriers. solution-designer, Implementation Dispatcher, Execution Agent and Closure Agent are native roles. See [Workflow Control Protocol](src/shared/references/guided-implementation/workflow-control-protocol.md).

## Package execution

**Skill Package**:
One generated, independently installable Stage entry and its complete internal resource closure. Shared source lives under `src/shared/`; stage source lives under `src/stages/`. Generated copies share the existing project/Git-derived coordination state, not package-specific ledgers or locks.

**Pinned Package Identity**:
The verified real root, entry, bundle digest and exact protocol compatibility key retained by one run. Current effective registry evidence establishes callability; filesystem discovery only diagnoses candidates. Updating registration evidence does not replace the pinned execution identity. See [Package execution](src/shared/references/package-execution.md).
