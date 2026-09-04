# Spec: Proactive topic splitting and Phase-0/1 topic dependency gates

Status: `ready-for-agent`

阶段结果：拷问完成
用户完成确认：已记录（2026-09-04）

## Problem Statement

`0讨论` and `1拷问` can receive a newly introduced change that is large enough
to deserve its own durable topic. The existing child-topic handoff can create
and bind that topic, but the workflow does not yet define when the Agent should
proactively recommend the split or how two topics should wait on one another
while requirements are still being discussed.

Without an explicit rule, the Agent may either absorb an independent change
into the current requirement, split too aggressively, or create a new task that
starts before the prerequisite discussion result exists. A later reversal of
an accepted upstream conclusion can also leave a directly dependent topic
continuing from a premise that is no longer valid.

## Outcome

Extend the existing discussion tree and child-topic handoff instead of adding a
new topic type. `0讨论` and `1拷问` may proactively recommend a separately named
topic when a newly introduced change has its own goal and outcome and would
materially interrupt the current topic.

Every created topic starts in Phase 0. A same-tree topic may carry one or more
Phase-0/1 dependency gates. A closed gate suspends that topic until the user
returns to its conversation and asks it to continue. The dependency is checked
then, not monitored continuously. Once released, the gate is not checked again
unless a directly upstream topic later overturns an accepted conclusion and
the dependency protocol closes the gate again.

Topic dependencies end at the requirements boundary. They never become
planning or implementation dependencies in Phases 2-4.

## Confirmed User Experience

### When to recommend a new topic

Recommend a split only when all of the following are true:

- the newly introduced content has an independent, nameable goal;
- it has its own scope or acceptance outcome;
- expanding it in the current conversation would materially interrupt the
  active `0讨论` or `1拷问`; and
- removing it from the current topic does not make the current topic
  semantically incomplete.

Do not recommend a split merely because work is difficult or large. Keep the
content in the current topic when it is only an implementation detail, an
inseparable part of the current acceptance contract, or a small clarification.

### Stable split proposal

The Agent presents one complete proposal before preparing a handoff:

```text
新话题建议

当前话题：<current topic>
建议拆出：<new topic goal>
判断依据：<why this is an independent discussion target>

话题归属：<current discussion tree | new root topic>
结构来源：<parent topic | none>
新话题入口：0讨论

依赖关系：
- <no dependency; both topics may continue>
or
- <dependent topic> depends on <prerequisite topic>
- 所需结果：<specific Phase-0/1 result>
- 初始门禁：关闭
- 放行触发：用户在依赖方话题中要求继续时检查

当前话题处理：<continue | wait for the new topic result>
结果回流：<absorb | impact review | no return required>

确认事项：接受以上拆分方向，并准备新话题身份、初始依赖和 handoff
确认后结果：更新讨论状态并展示独立的 Codex 任务创建确认；本次尚不创建任务
修改方式：说明需要调整的目标、归属、依赖或当前话题处理方式
确认方式：回复 `确认`
```

Accepting the split proposal authorizes only the confirmed discussion-state
preparation. The existing, separate child-task creation confirmation remains
the authority for `create_thread`.

### Behavior by source context

- In `0讨论`, the active topic may prepare the new child topic directly.
- In a current-task, discussion-attached `1拷问`, the new topic remains in the
  existing discussion tree.
- A standalone `1拷问` has no discussion tree and therefore recommends a new
  root topic. No executable cross-tree dependency is created.
- A dedicated grilling Phase Carrier may identify and explain the split, but it
  does not own the tree. After the user accepts the idea, it sends a bounded
  proposal to the Phase Source Task. The source task presents the authoritative
  preparation and creation confirmations. Information may cross tasks;
  authorization does not.

### Starting and resuming a gated topic

The child topic and its Codex task are created and bound immediately after the
normal confirmations, even when an initial dependency gate is closed. Its
first turn still performs only the existing handoff acceptance.

At the start of a later user-triggered turn in that topic:

1. Read the topic's current derived gate.
2. If the gate is open, perform the requested Phase-0/1 work normally.
3. If the gate is closed, evaluate every active closed dependency.
4. If any required result is unavailable, report the exact waiting condition
   and stop without performing the requested substantive work.
5. If all required results are available, show the user the results that would
   release the gate.
6. After the user confirms, revalidate those exact results, open the gate,
   record the accepted basis, and begin the requested work.

Multiple dependencies have AND semantics. The gate opens only when every
active dependency is releasable. Alternative `A OR B` dependency expressions
are not supported; the discussion must first choose one prerequisite.

An open gate is not reevaluated on ordinary later turns. Dependency checks do
not poll, monitor, wake, or notify tasks in the background.

### Relationship semantics

Keep three meanings separate:

- `parent` records where a topic was structurally split from;
- a topic dependency records which Phase-0/1 result must exist before a topic
  may proceed; and
- `absorb` or `impact` records how a child result affects another topic.

A parent relation does not imply a dependency. When a necessary parent
question is split out, create `parent depends on child` with the exact required
result. A purely independent branch has only the structural parent relation.
Likewise, intent to absorb a result does not itself imply a blocking
dependency.

If accepting an exact child result through the existing `absorb` flow also
satisfies a matching closed dependency, the same user confirmation may open
that gate atomically. An `impact` result leaves the gate closed until the
relevant Phase-0/1 impact review has produced an acceptable result.

### Parent question after a split

When the new idea suspended an active Phase-0 question, the split proposal also
freezes one existing question action:

- resume it when the two topics have no dependency;
- adjust it to the exact wait condition when the parent depends on the new
  topic; or
- invalidate it when the extracted topic replaces that question.

The confirmed action uses the existing `resume`, `adjust`, or `invalidate`
behavior; no new question state is introduced.

## Topic Dependency Model

### Scope and direction

Executable dependencies may connect only topics with the same `tree_id`.
Cross-tree and external dependencies remain user-managed documented
prerequisites and receive no hidden atomic coordination.

The direction is explicit: `B depends on A` means B owns a gate that waits for
A. A is not blocked by B. Every topic may own zero or more dependencies; topics
without a dependency may continue in parallel.

The active dependency graph must remain acyclic. Creation or modification of a
dependency that would introduce a cycle is rejected before any state or
external task creation occurs. The user may choose a direction, narrow a
required result, or extract a common prerequisite topic instead.

### Requirement and accepted basis

The dependency distinguishes what is required from what was eventually used:

- `requirement_kind` is one of `phase-0-checkpoint`, `phase-1-result`, or
  `confirmed-decision`;
- `requirement_summary` is the user-confirmed semantic result needed by the
  dependent topic; and
- `accepted_basis` is populated only when a closed gate is released. It freezes
  the exact current result, checkpoint and decision identities accepted in
  that release.

Gate evaluation uses only current effective evidence. A historical Phase
Result invalidated by reopen, a superseded checkpoint, or a replaced or
discarded decision does not satisfy a closed gate.

Creation does not need to guess future result identities. The semantic
requirement is frozen first; exact evidence is resolved and recorded at gate
release.

### Gate and relation state

Each dependency has two independent state dimensions:

- `relation_state`: `active` or `cancelled`;
- `gate_state`: `closed` or `open`.

The topic-level gate is derived rather than persisted separately. A topic is
closed when it owns at least one `active + closed` dependency; otherwise it is
open.

Cancelling or changing a dependency changes a confirmed requirement and
therefore requires confirmation in the dependent topic. A prerequisite topic
cannot change, cancel, or release another topic's dependency.

### Direct reverse invalidation

When a Phase-0/1 conclusion becomes authoritative and replaces or discards an
earlier conclusion, the same authoritative state change examines every direct,
still-existing Phase-0/1 dependent whose last `accepted_basis` references the
affected result.

- Affected dependents have the matching gate closed again.
- Unknown impact is treated as affected and closes the gate.
- Clearly unaffected dependents remain open.
- The dependency edge and prior accepted basis remain available for audit and
  the next release.

The invalidation is one hop only. Closing B because A changed does not inspect
or close C merely because C depends on B. C is examined only if B later
overturns one of B's own conclusions.

The upstream actor does not gain general write authority over the dependent
topic. The protocol may only close matching gates as the bounded consequence
of an authoritative conclusion change.

An already running dependent turn is not interrupted. The upstream operation
writes the closed gate, and the dependent observes it only at the start of its
next user-triggered turn. Ordinary optimistic-concurrency recovery remains
unchanged and is not extended with a dependency-specific mid-turn interrupt.

### Phase boundary

Topic dependencies are writable and enforceable only while the dependent
topic is in Discussion Phase 0 or 1.

- A topic may not perform substantive Phase-0/1 work or leave its current
  Phase-0/1 position while its derived gate is closed.
- Before any `0→1`, `0→2`, or `1→2` route, every active dependency gate must
  be open.
- After entry to Phase 2, Topic Dependencies are immutable historical evidence
  and are not consulted by Phase-2/3/4 execution.
- Later source changes use the existing impact and reopen protocols rather
  than reopening a topic dependency gate.
- To change a dependency after Phase 1, first return through the existing
  explicit impact/reopen path.

A prerequisite topic may close normally when the required accepted result is
durably preserved. Explicit abandonment without that result is allowed only
with the existing affected dependents reported as unable to proceed until the
user changes or cancels their requirement. Garbage collection and compaction
must retain evidence still referenced by an active dependency.

## Ledger Contract

Add a dedicated ledger section rather than reusing implementation coordination:

```text
## Topic Dependencies
```

`Dependencies and Active Implementations` retains its existing implementation
coordination meaning. Topic dependencies must not enter its global Phase Run
evidence digest.

A minimal Topic Dependency record contains:

```yaml
dependency_id: DEP-...
record_revision: 1
dependent_topic_id: topic-B
prerequisite_topic_id: topic-A
requirement_kind: phase-1-result
requirement_summary: A has frozen the authentication behavior contract
relation_state: active
gate_state: closed
accepted_basis_json: null
gate_reason_json:
  kind: initial-dependency
  handoff_id: H-...
```

`accepted_basis_json` retains the most recently accepted basis even after a
gate is closed again. `gate_reason_json` identifies the authoritative creation
or invalidation event. Full history remains in Recent Events rather than being
embedded in the current record.

Creating a child topic with initial dependencies is one ledger transaction:
the topic, structural parent relation, handoff intent, attempt, and dependency
records either all commit before `create_thread`, or none do.

Structural `parent` and `continuation` relations do not contribute to Phase Run
coverage evidence. Coverage evidence retains only relations whose meaning can
change the current topic's accepted result, including actual result coverage
or absorption. This lets a discussion-attached `1拷问` prepare an independent
same-tree topic without turning tree topology into requirement drift.

## Protocol Interface

### `prepare-handoff`

Accept an optional bounded `initial_dependencies` list. Validate same-tree
identity, allowed Phase-0/1 requirement kinds, ownership, duplicate edges and
the whole-tree acyclic invariant before committing any record.

### `evaluate-topic-gate`

Read-only. On a user-triggered attempt to work in a gated topic, return one of:

- `open`: no active closed dependency exists;
- `releasable`: all active closed dependencies have current effective evidence;
- `blocked`: at least one required result is unavailable or no longer
  attainable without a requirement change.

For `releasable`, return the exact proposed accepted basis and the revisions to
which a later release must bind.

### `release-topic-gate`

After the user confirms the unchanged proposed release, revalidate all exact
evidence and revisions, atomically open every active closed dependency, and
record each accepted basis. Partial release does not start substantive work;
the operation is all-or-stop for the topic's current closed dependency set.

### `update-topic-dependency`

Allow the dependent topic owner in Phase 0 or 1 to create, replace, or cancel a
dependency after explicit confirmation. Recheck identity, ownership, stage,
duplicate-edge and acyclic invariants. A dependency whose gate would apply to
already-published Phase-0/1 authority requires the existing impact/reopen path
instead of creating contradictory state.

### Existing conclusion mutations

The existing confirmed-decision replacement, discard, direction-change and
impact-resolution operations own direct reverse invalidation. When a newly
authoritative conclusion invalidates a recorded accepted basis, the same
ledger transaction closes the affected direct gates. There is no general
caller-visible `regate` operation.

### Existing lifecycle routes

Phase-0/1 maturity and route operations reject a closed derived topic gate.
Phases 2-4 do not read Topic Dependencies. A successful Phase-1 footer can
therefore continue to use existing stepwise or continuous-flow behavior
without adding a later dependency check.

## Ownership and Authorization

- The split proposal confirmation authorizes only the exact topic structure,
  initial dependencies and handoff preparation shown to the user.
- The existing task-creation confirmation separately authorizes exactly one
  `create_thread` call with the frozen project and task settings.
- The dependent topic owns dependency definition changes and gate release.
- A prerequisite conclusion change authorizes only protocol-driven closure of
  directly affected gates.
- A dedicated grilling carrier may send a proposal to its Phase Source Task but
  cannot prepare, create, bind, change, release or cancel a topic dependency.
- No confirmation or authority silently crosses tasks.

## Acceptance Scenarios

1. A Phase-0 user introduces a substantial independent change. The Agent
   recommends a child topic; after both existing confirmations, the child is
   created in Phase 0 and both topics continue without a dependency.
2. A parent requires a child conclusion. Initial creation atomically records
   `parent depends on child`; the parent gate closes while unrelated topic
   records remain unchanged.
3. A child requires its parent's Phase-1 result. The child task is created and
   accepts its handoff but performs no substantive work when the user first
   asks it to continue before that result exists.
4. The user later returns to the child. Gate evaluation finds the current
   result, the user confirms the proposed release, and the child begins work
   with an exact accepted basis.
5. Later turns in that child do not re-evaluate the open dependency.
6. The prerequisite topic replaces an accepted decision in Phase 0 or 1. The
   directly affected child's gate closes in the same authoritative state
   change, but an already-running child turn is not interrupted.
7. On the child's next user-triggered turn, it observes the closed gate and
   stops before substantive work. After a new acceptable result exists, the
   normal evaluate-and-release flow records a new accepted basis.
8. A grandchild is not closed merely because its parent was closed by an
   upstream change. It is checked only if its direct prerequisite later
   overturns one of its own conclusions.
9. Multiple initial dependencies use AND semantics and are released
   atomically. OR expressions are rejected.
10. An attempted dependency cycle is rejected before handoff preparation or
    external task creation.
11. A dedicated grilling carrier proposes a new topic to the Phase Source Task;
    only the source task can obtain the preparation and creation confirmations.
12. A standalone Phase 1 recommends a new root topic and records no executable
    cross-tree dependency.
13. Structural child creation during an attached Phase 1 does not drift Phase
    Run coverage merely by adding `parent` topology.
14. A closed gate blocks substantive Phase-0/1 work and every `0→1`, `0→2`, or
    `1→2` route; after Phase 2 Topic Dependencies are neither mutable nor
    consulted.
15. Cancelling or abandoning a prerequisite cannot silently release a
    dependent gate, and referenced accepted evidence is not garbage-collected.

## Failure and Recovery

- All mutations use the existing expected ledger/record revisions,
  idempotency-key contract, tree lock, atomic replacement and Recent Events.
- Failure before the atomic handoff/dependency transaction creates neither the
  topic nor an external task attempt.
- Failure after preparation but before task creation follows the existing
  handoff recovery rules; retry does not duplicate dependency records.
- An uncertain `create_thread` outcome remains governed by the existing
  attempt reconciliation and cannot be retried merely because the child is
  gated.
- A stale release confirmation or changed accepted basis stops and requires a
  fresh evaluation and user confirmation.
- A reverse invalidation failure cannot publish the upstream conclusion as
  authoritative while leaving known affected direct gates open.

## Out of Scope

- Real-time dependency polling, notifications, task wakeups or monitors.
- Interrupting a dependent topic's already-started turn.
- Transitive dependency invalidation or automatic cascade through descendants.
- Executable cross-tree or cross-project dependency coordination.
- OR expressions or arbitrary Boolean dependency formulas.
- Dependency gates in solution design, implementation or change closure.
- Direct creation of a new topic in Phase 1; every new topic starts in Phase 0.
- Treating structural parenthood, result absorption or implementation
  parallelism as an implicit topic dependency.
- Replacing the existing child-topic task creation, binding, recovery or result
  return protocols.

## Next Stage Inputs

Solution design should specify:

- the ledger schema version change and migration for `Topic Dependencies`;
- the exact JSON request and response shapes for the four dependency-facing
  interfaces;
- how conclusion mutations identify accepted-basis overlap without semantic
  free-text parsing;
- the precise Phase-0/1 entry and exit guards in both `design-discussion` and
  `problem-framing`;
- the dedicated-carrier proposal delivery and source-task authorization path;
- the Phase Run evidence filter that excludes structural topology; and
- CLI black-box, concurrency, idempotency, recovery and repository-validation
  test slices.

## Comments

- 2026-09-04: Requirements were refined interactively. The final model uses a
  persistent one-shot gate, user-triggered release checks, direct-only reverse
  invalidation, no mid-turn interruption, and no dependency behavior after
  Phase 1.
