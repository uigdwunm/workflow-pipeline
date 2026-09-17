# Workflow Pipeline

This context describes a Codex engineering workflow whose ordinary Stages may
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
publishing and cleaning a handed-off Flow Worktree after a verified
implementation result. It never repairs implementation code.
_Avoid_: Implementation repair

**Flow Worktree**:
The one isolated Git worktree created at Stage 2, or by a standalone Stage 3,
and retained through Stage 4 for one delivery. Stage 2 may publish its planning
checkpoint without ending the Flow Worktree.
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
The derived Phase-0/1 permission to begin substantive work or advance a topic.
It is closed while any active Topic Dependency is closed, is checked when the
user asks to continue, and is not consulted by Workflow Stages 2–4.
_Avoid_: Background monitor, implementation blocker

## Workflow roles

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
The unique native Stage 3 child that implements, delegates exact nonoverlapping
file assignments, integrates Git and produces a verified candidate in the one Flow
Worktree. It does not own user decisions, independent review or publication.
_Avoid_: Workflow Controller, Execution Agent

**Execution Agent**:
A native implementation child with an exact file allocation, behavioral target,
testing requirement and no Git mutation authority. It stops and returns bytes for
dispatcher acceptance.
_Avoid_: Implementation Dispatcher

**Closure Agent**:
The native Stage 4 child receiving the accepted candidate, review, verification,
binding and exact documentation scope. It publishes and cleans up; implementation
problems return to Stage 3.
_Avoid_: Workflow Controller

**Worktree Binding**:
The verified Git identity that ties one Flow Worktree to its repository, branch,
target, and committed ancestry across Stage handoffs and retries.
_Avoid_: Conversation Binding

Workflow Controller retains confirmation, routing and acceptance. Dedicated Discussion Task and Dedicated Problem Framing Task are visible 0/1 carriers. solution-designer, Implementation Dispatcher, Execution Agent and Closure Agent are native roles. See [Workflow Control Protocol](src/shared/references/guided-implementation/workflow-control-protocol.md).

## Package execution

**Skill Package**:
One generated, independently installable Stage entry and its complete internal resource closure. Shared source lives under `src/shared/`; stage source lives under `src/stages/`. Generated copies share the existing project/Git-derived coordination state, not package-specific ledgers or locks.

**Pinned Package Identity**:
The verified real root, entry, bundle digest and exact protocol compatibility key retained by one run. Current effective registry evidence establishes callability; filesystem discovery only diagnoses candidates. Updating registration evidence does not replace the pinned execution identity. See [Package execution](src/shared/references/package-execution.md).
