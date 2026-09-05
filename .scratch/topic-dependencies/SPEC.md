# Spec: Proactive topic splitting and Phase-0/1 topic dependency gates

Status: `completed`
Lifecycle: completed

Requirement source: `.scratch/topic-dependencies/PRD.md` at
`a46e2801801886c0915466c5aab6f6db55016fda`, SHA-256
`568f44a8fff6b4fc4ac0d01eaaf746d71417f9cd76256217b3932e762a63c7af`.

## Problem Statement

`0讨论` and `1拷问` already know how to hand a child Discussion Topic to a
separate Codex task, but they do not tell the Agent when an interruption is an
independent topic rather than part of the active requirement. They also cannot
represent a requirements-stage prerequisite between same-tree topics.

That leaves two failure modes. The current Discussion Topic can grow until its
goal becomes incoherent, or a newly split topic can begin substantive work
before the exact Phase-0/1 result it needs exists. Once a dependent topic has
accepted an upstream result, a later authoritative reversal can also leave it
working from a stale premise.

The workflow needs a narrow requirements-only dependency mechanism that keeps
structural parenthood, result return, and implementation coordination distinct;
preserves the existing child-task confirmation boundary; and remains safe under
concurrent tasks, retries, and uncertain task creation.

## Solution

Teach the Phase-0 and Phase-1 wrappers to recognize a stable four-part split
criterion and present one fixed split proposal. An accepted proposal prepares
discussion state only; the existing, separately confirmed child-task creation
flow remains unchanged.

Add Topic Dependencies as a dedicated, versioned section of the discussion
ledger. A dependency belongs to its dependent Discussion Topic and names one
same-tree prerequisite plus a semantic requirement kind and summary. Active,
closed dependencies derive a closed topic gate. The dependency engine validates
the whole active graph for cycles and owns four protocol interfaces:
`prepare-handoff` with initial dependencies, `evaluate-topic-gate`,
`release-topic-gate`, and `update-topic-dependency`.

Gate evaluation is user-triggered. It resolves current effective Phase-0/1
authority into a structured accepted basis without parsing the requirement
summary. When all closed dependencies are releasable, the Agent shows the exact
bases. User confirmation authorizes one atomic release that revalidates every
record and authority object before opening all current closed dependencies.

Accepted bases carry stable authority identities and decision digests. Existing
operations that authoritatively replace, discard, or reopen a conclusion use
those identities to close only directly affected gates in the same ledger
transaction. Unknown overlap closes the gate; proved disjoint decision sets do
not. There is no recursive cascade or mid-turn interruption.

Protocol guards prevent a closed topic from starting substantive Phase-0/1
work or activating a `0→1`, `0→2`, or `1→2` route. Child handoff acceptance and
read-only inspection remain allowed. Topic Dependencies become immutable and
are ignored by Stages 2–4 after the dependent topic enters Discussion Phase 2.

## User Stories

1. As a user in `0讨论`, I want the Agent to recommend a separate topic only for an independent goal, so that useful focus is preserved without needless fragmentation.
2. As a user in `1拷问`, I want the same split rule to apply, so that requirement clarification does not absorb a separate outcome.
3. As a user, I want the complete split direction shown before state preparation, so that I can review the goal, ownership, dependency, current-topic behavior, and result return together.
4. As a user, I want accepting a split proposal to prepare discussion state without creating a Codex task, so that external task creation keeps its own confirmation boundary.
5. As a user, I want every new topic to start in Discussion Phase 0, so that no child silently skips requirements work.
6. As a user, I want a split topic to remain in the current discussion tree when the source is an attached Phase-0 or Phase-1 flow, so that same-tree coordination is possible.
7. As a user running standalone `1拷问`, I want a split to become a new root topic without an executable cross-tree edge, so that the workflow does not imply coordination it cannot enforce.
8. As a Phase Carrier, I want to send only a bounded split proposal back to the Phase Source Task, so that information crosses tasks without transferring authorization.
9. As a Phase Source Task, I want to own the authoritative preparation and creation confirmations, so that the discussion tree has one mutation authority.
10. As a user, I want a structural parent relation to remain independent of dependency direction, so that ancestry does not accidentally block work.
11. As a user, I want `B depends on A` to close only B's gate, so that A and unrelated topics can continue.
12. As a user, I want a topic to depend on several prerequisites with AND semantics, so that work begins only after every required result exists.
13. As a user, I want unsupported OR expressions rejected, so that a dependency does not hide an unresolved choice.
14. As a user, I want dependency cycles rejected before any state or task creation, so that the discussion tree cannot deadlock.
15. As a user, I want a gated child task to be created and bound normally, so that I can see and return to it even while it waits.
16. As a child topic owner, I want first-turn handoff acceptance to succeed while the gate is closed, so that identity and authority are established without starting substantive work.
17. As a returning user, I want the gate checked at the start of my requested turn, so that I see the current prerequisite state rather than a stale cached answer.
18. As a returning user, I want an exact waiting explanation when evidence is unavailable, so that I know which topic and result must complete.
19. As a returning user, I want the exact current evidence shown before release, so that I can decide whether it satisfies the semantic requirement.
20. As a returning user, I want one confirmation to release all currently closed dependencies atomically, so that a partially open topic never begins work.
21. As a returning user, I want a changed release proposal to require fresh review, so that stale evidence is never accepted silently.
22. As a topic owner, I want an open gate to stay open on ordinary later turns, so that dependencies are not continuously polled.
23. As a topic owner, I want a directly upstream authoritative reversal to close an affected open gate, so that the next turn cannot use a stale premise.
24. As a topic owner, I want clearly unrelated upstream changes not to close my gate, so that stable work is not blocked unnecessarily.
25. As a topic owner, I want unknown overlap to fail closed, so that incomplete provenance cannot silently preserve authority.
26. As a user, I want reverse invalidation to stop after one hop, so that a change does not cascade through topics whose own conclusions did not change.
27. As a user with work already running, I do not want a dependency change to interrupt that turn, so that existing optimistic-concurrency behavior remains the recovery mechanism.
28. As a user, I want an exact absorbed child result to release a matching parent dependency in the same confirmation, so that result return does not require a redundant gate action.
29. As a dependent topic owner, I want to create, replace, or cancel my own dependency after confirmation while still in Phase 0 or 1, so that requirements can evolve explicitly.
30. As a prerequisite topic owner, I do not want authority to release, cancel, or redefine another topic's dependency, so that gate ownership remains local.
31. As a topic owner, I want a closed gate to block Phase-0/1 mutations and routes to Phase 1 or 2, so that the phase boundary cannot bypass the prerequisite.
32. As an implementation-stage actor, I want Topic Dependencies ignored after Phase 1, so that they do not become planning or implementation blockers.
33. As an operator, I want task-creation retries to reuse the initial dependency transaction, so that uncertain external outcomes cannot duplicate dependency records.
34. As an operator, I want referenced accepted evidence retained through compaction and checkpoint garbage collection, so that an active dependency remains auditable and releasable.
35. As a maintainer, I want older ledgers to load without a manual migration, so that deployment does not strand existing discussion trees.
36. As a maintainer, I want structural relations excluded from Phase Run coverage evidence, so that adding an independent child does not create false requirement drift.
37. As a maintainer, I want Topic Dependencies excluded from the implementation-coordination digest, so that requirements gates and active implementations remain separate domains.
38. As a maintainer, I want black-box tests at the JSON CLI seam, so that authorization, atomicity, retries, and migration are verified as observable protocol behavior.

## Implementation Decisions

### Owning modules and interfaces

- The discussion protocol remains the sole durable authority. A dedicated Topic
  Dependency component inside its core owns record validation, graph checks,
  derived-gate calculation, evidence resolution, atomic release, dependency
  mutation, and direct reverse invalidation. Handoff, checkpoint, Phase Run, and
  topic-update components call this owner through narrow functions; they do not
  inspect or mutate dependency records independently.
- The ledger state component owns schema-aware parsing and rendering. It exposes
  the Topic Dependencies section as a normal record collection and validates it
  during every ledger load before an operation observes state.
- The protocol registry exposes the three new operation names and the extended
  `prepare-handoff` request. All four dependency-facing interfaces continue to
  use the existing bounded stdin JSON / single stdout JSON boundary, the common
  authenticated actor envelope, the five-second tree lock, expected ledger and
  topic revisions for writes, UUIDv4 idempotency keys, and exact-key validation.
- The `0讨论`, `1拷问`, and dedicated-grilling wrapper instructions own proactive
  recommendation and confirmation wording. They consume protocol results but do
  not derive ledger state or bypass the existing task-creation confirmation.
- The child-topic handoff contract owns the split preparation sequence. Gate
  release is checked only on a later user-triggered turn and is not folded into
  first-turn handoff acceptance.

### Stable proactive split decision

- The Agent recommends a split only when the new content has an independent
  nameable goal, has its own scope or acceptance outcome, would materially
  interrupt the current `0讨论` or `1拷问`, and can be removed without making the
  current topic incomplete.
- The wrapper uses the fixed `新话题建议` block from the requirement source. The
  proposal names structural ownership, initial dependency direction and exact
  semantic requirement, current-topic handling, result-return behavior, and the
  separate task-creation step.
- An inserted Phase-0 question remains represented by the existing suspended
  question state. Once the split direction is confirmed, its action is exactly
  one existing `resume`, `adjust`, or `invalidate`; no new question state is
  introduced.
- A dedicated grilling carrier can send a bounded proposal containing goal,
  scope, split rationale, proposed root/child ownership, proposed dependencies,
  current-topic behavior, and result-return behavior to the Phase Source Task.
  The carrier cannot call dependency or handoff mutations. The Phase Source Task
  must restate the authoritative fixed proposal and obtain both existing
  confirmations.

### Ledger schema and migration

- New ledgers use schema version 3. The ordered `Topic Dependencies` section is
  inserted after `Relations and Coverage` and before `Dependencies and Active
  Implementations`. This preserves the visible distinction between result
  relationships, requirements gates, and implementation coordination.
- Schema versions 1 and 2 remain readable. The loader parses their legacy
  section order, applies the existing creation-receipt hydration when needed,
  and supplies an empty Topic Dependencies collection in memory. A read does not
  rewrite the ledger. The next successful mutating transaction renders the
  complete state as version 3. Bootstrap and authorized document-only
  initialization write version 3 directly. Unknown later versions fail closed.
- Every Topic Dependency record has exactly these scalar fields:

```yaml
dependency_id: DEP-<uuid hex>
record_revision: 1
dependent_topic_id: topic-...
prerequisite_topic_id: topic-...
requirement_kind: phase-0-checkpoint
requirement_summary: A user-confirmed semantic requirement
relation_state: active
gate_state: closed
accepted_basis_json: null
gate_reason_json: "{canonical JSON object}"
```

- `requirement_kind` is one of `phase-0-checkpoint`, `phase-1-result`, or
  `confirmed-decision`. `relation_state` is `active` or `cancelled`.
  `gate_state` is `closed` or `open`. A cancelled record retains its last gate
  state and accepted basis for audit, but contributes to neither the derived
  gate nor cycle detection.
- `accepted_basis_json` is null until first release. Thereafter it is canonical
  JSON containing `basis_version`, the dependency and prerequisite identities,
  the requirement kind, one exact authority descriptor, and a sorted
  `decision_authority` array of `{decision_id, sha256}` pairs. Checkpoint
  descriptors include checkpoint ID, record revision, published identity, and
  decision digest. Phase-result descriptors include result ID, record revision,
  state, Phase Run ID, and affected decision IDs. Confirmed-decision descriptors
  contain the exact active decision-set digest. An absorbed child-result basis
  additionally names the child-result ID and its frozen underlying authority.
- `gate_reason_json` is canonical JSON identifying the current transition:
  initial handoff, explicit create/replace, atomic release, or direct upstream
  invalidation. It includes the causative handoff, dependency update, release,
  topic-update, or reopen identity and the ledger revision. Recent Events keeps
  full history; the current record keeps only current state plus the last
  accepted basis.
- Topic-level gate state is never stored. It is derived as closed when at least
  one record for that dependent topic is `active + closed`; otherwise it is
  open.

### `prepare-handoff` extension

The operation accepts an optional bounded `initial_dependencies` array only for
a child handoff. Its exact item shape is:

```json
{
  "dependent_endpoint": "source | target",
  "prerequisite_topic_ref": "source | target | topic-<same-tree id>",
  "requirement_kind": "phase-0-checkpoint | phase-1-result | confirmed-decision",
  "requirement_summary": "one user-confirmed semantic result"
}
```

`source` and `target` are resolved inside the transaction after the deterministic
child identity is known; callers never construct the child identity. The
dependent endpoint may only be the source or newly created target. The
prerequisite may also be another existing same-tree topic. The list is capped at
64 items, contains no self-edge or duplicate dependent/prerequisite pair, and
does not admit Boolean expressions.

Before any record is written, the dependency owner validates all referenced
topics, same-tree membership, dependent Phase 0/1 eligibility, allowed kinds,
the caller's source-topic authority, and acyclicity of the whole active graph
with the proposed edges included. For a child handoff, creation of the topic,
parent relation, handoff record and attempt, and dependency records remains one
ledger transaction. Idempotent replay returns the same dependency IDs; a
failure commits none of them and occurs before external task creation.

The response adds a sorted `initial_dependencies` array containing each created
record's ID, revision, resolved endpoints, requirement fields, and closed state.
The existing identity envelope and task payload digest also cover the normalized
initial dependency set so bind and retry cannot switch it.

### `evaluate-topic-gate`

The read-only request uses the common query envelope and accepts an optional
`basis_selection` array:

```json
{
  "protocol_version": 1,
  "operation": "evaluate-topic-gate",
  "project_path": "<canonical absolute project>",
  "project_id": "project-...",
  "tree_id": "tree-...",
  "actor_topic_id": "topic-...",
  "actor_conversation_ref": "<active conversation>",
  "basis_selection": [
    {
      "dependency_id": "DEP-...",
      "authority_id": "CP-... | PH-... | omitted for confirmed-decision",
      "decision_ids": ["D-..."]
    }
  ]
}
```

The key is omitted for discovery evaluation. When present, it contains each
active closed dependency exactly once. A checkpoint or Phase Result selection
names exactly one candidate authority and any subset of its frozen decision
authority; an empty decision subset is allowed when the semantic result lives
only in the immutable artifact and intentionally causes conservative future
invalidation. A confirmed-decision selection omits `authority_id` and names one
or more current confirmed decisions. The Agent selects candidates from protocol
output by applying the user-confirmed semantic summary; protocol code validates
identities and currentness but never interprets prose.

The caller must own the dependent topic. The response always includes current
ledger/topic revisions, `derived_gate_state`, and a dependency array sorted by
dependency ID. Its top-level `state` is:

- `open` when no active closed dependency exists;
- `releasable` when every active closed dependency resolves to current effective
  evidence; or
- `blocked` when at least one requirement has no current effective evidence or
  cannot be attained without changing the requirement.

For each closed dependency the response includes its record revision, semantic
requirement, prerequisite topic phase/state, evidence status, a
user-displayable candidate catalog, and either a normalized `proposed_basis`
for a supplied valid selection or a stable waiting reason. Discovery and
selection are both deterministic and do not parse `requirement_summary`:

- `phase-0-checkpoint` lists completed effective Phase-0 checkpoints with their
  published identities and frozen decision authority;
- `phase-1-result` lists completed effective `0→1` Phase Results with their
  affected-decision authority; and
- `confirmed-decision` lists current active confirmed decisions with their
  summaries and per-decision digests.

A discovery response is `releasable` when at least one candidate exists for
every closed dependency and `blocked` otherwise. A selected releasable response
also includes `release_set` and `release_set_sha256`. The set contains every
active closed dependency exactly once with its record revision and proposed
basis. The digest is over canonical JSON plus the current ledger and
dependent-topic revisions. A blocked response may show available evidence but
has no releasable set. The wrapper performs discovery, chooses the narrow exact
authority that matches the semantic requirement, reruns evaluation with that
selection, and shows only the resulting proposed bases in the user confirmation.

### `release-topic-gate`

The mutation request adds the exact `release_set` and `release_set_sha256` from
the confirmed evaluation to the common mutation envelope. The protocol requires
the actor to own the dependent topic, requires the set to equal all current
active closed dependencies, verifies its digest and record revisions, recomputes
every current effective basis, and compares canonical bytes. Any ledger,
dependency, result, decision, or checkpoint drift returns a stale-evaluation
error without opening any dependency.

On success, one transaction changes every included `gate_state` to `open`,
increments every dependency revision, stores the exact proposed basis as
`accepted_basis_json`, records a release reason and events, and returns the open
derived gate plus the accepted bases. Empty or partial release is rejected.
Exact idempotent replay returns the prior success.

### `update-topic-dependency`

The dependent topic owner may call one confirmed action in Phase 0 or 1:

```json
{
  "action": "create | replace | cancel",
  "dependency_id": "required for replace or cancel",
  "expected_dependency_revision": "required for replace or cancel",
  "prerequisite_topic_id": "required for create or replace",
  "requirement_kind": "required for create or replace",
  "requirement_summary": "required for create or replace"
}
```

These fields accompany the common mutation envelope. Create derives a stable
`DEP-*` identity from the idempotency key. Replace keeps the dependency ID,
increments its revision, installs the new prerequisite and requirement, and
closes it while retaining the prior accepted basis for audit. Cancel increments
the revision and changes only `relation_state` to cancelled. Create and replace
validate ownership, same-tree identity, phase eligibility, duplicates and the
whole active graph before writing. A dependency cannot be changed after Phase 1
unless the topic first returns through the existing explicit reopen path.

### Accepted child-result release

- Child-result submission freezes the child's current underlying Phase-0/1
  authority in the result claim: exact checkpoint/result identity where present,
  current decision IDs and digests, and the child topic phase. This is generated
  by the protocol, not supplied as trusted prose by the child.
- Parent acceptance with `effect: absorb` may include a sorted
  `dependency_releases` array. Each item names an active closed dependency plus
  the exact checkpoint/result authority and decision IDs selected from the
  child result's frozen authority. The dependency must be owned by the parent,
  point directly to that child, and match its requirement kind. The one
  confirmed transaction records the absorb relation and opens the named
  matching gates. Omitting the array keeps the existing absorb behavior and
  leaves gates closed.
- `effect: impact` never releases a gate. A later impact review may create an
  acceptable current parent conclusion, after which ordinary evaluation and
  release applies.

### Direct reverse invalidation

- An accepted basis is matched structurally, never by searching its summary.
  The dependency owner compares the upstream topic ID, exact authority identity
  and record state, and the intersection between changed decision IDs and the
  basis's decision-authority IDs and digests.
- Authoritative `resolve-impact` actions `adjust`, `replace`, and `discard`
  compute the changed decision ID and close every direct active open dependency
  whose basis references it in the same ledger transaction. `keep` does not.
- `reopen-phase` changes completed Phase Results to review-pending. In that same
  transaction it closes direct gates whose basis references an invalidated
  result or any affected decision. A referenced authority that cannot be proved
  current is unknown impact and closes; a disjoint complete decision set remains
  open.
- Closing increments the dependency revision, preserves `accepted_basis_json`,
  and records the causative result/decision IDs in `gate_reason_json`. Only
  dependencies whose `prerequisite_topic_id` equals the mutating topic are
  inspected. The helper never recurses to dependents of those dependents.
- If reverse invalidation cannot be committed, the upstream conclusion mutation
  cannot become authoritative. The ordinary tree lock and atomic ledger replace
  make this all-or-stop.

### Phase and turn guards

- `read-topic` reports the derived gate and active closed dependency summaries.
  Phase-0 and Phase-1 wrappers call `evaluate-topic-gate` at the beginning of a
  later user-triggered turn before substantive work. They stop on `blocked`,
  show and confirm a release on `releasable`, and continue directly on `open`.
- First-turn `accept-handoff` is allowed while closed. Later
  `authorize-handoff-discussion` verifies the derived gate before granting
  substantive discussion. Read-only handoff and topic operations remain allowed.
- The protocol rejects substantive topic-document preparation, child handoff
  preparation, dependency-changing checkpoints, and Stage-entry checkpoint
  preparation while the actor topic gate is closed. Pause/read/recovery actions
  that do not advance requirements remain available.
- Wrapper and generic Phase Run preparation for `0→1`, `0→2`, or `1→2` require
  an open derived gate. Wrapper carrier readiness and source activation recheck
  it. If a gate closes after activation, that already-started turn/run is not
  interrupted; completion remains governed by existing optimistic concurrency.
- Finalization into Phase 2 seals Topic Dependencies as historical. Stages 2–4
  do not consult them. Existing impact and reopen operations remain the only way
  to return later work to requirements discussion.

### Phase Run evidence and retention

- `Relations and Coverage` evidence includes only active relation kinds that can
  alter accepted result coverage. Structural `parent` and `continuation`
  relations are excluded. Existing `absorbs` coverage remains included.
- The existing Phase Run `dependency` evidence dimension continues to hash only
  `Dependencies and Active Implementations`. Topic Dependencies never enter any
  Phase Run evidence digest; gate safety comes from the explicit prepare,
  readiness, and activation guards.
- Checkpoint/snapshot garbage collection excludes authority objects referenced
  by the accepted basis of any active Topic Dependency, whether its gate is open
  or closed. Cancellation makes the dependency non-retaining. Current records
  retain enough structured authority even if Recent Events is compacted.

### Validation and errors

- Ledger validation checks exact record fields and types, unique dependency IDs,
  existing same-tree endpoints, no self-edge, unique active endpoint pairs,
  allowed state pairs, canonical embedded JSON, coherent accepted bases, and an
  acyclic active graph. Corruption fails every operation before state is used.
- New stable errors distinguish `topic_gate_closed`,
  `topic_dependency_cycle`, `topic_dependency_duplicate`,
  `topic_dependency_state_conflict`, `topic_dependency_phase_conflict`,
  `topic_dependency_ownership_conflict`, `topic_dependency_evidence_unavailable`,
  and `topic_gate_evaluation_stale`. Each response retains the existing error
  envelope and includes safe current revision or dependency context when useful.
- Validation limits all lists and prose fields using existing protocol bounds,
  rejects duplicate JSON keys and unsupported fields, and never accepts a
  caller-supplied topic ID for a not-yet-created child.

## Testing Decisions

- The principal seam is the discussion protocol's stdin/stdout JSON CLI. Tests
  invoke complete requests and assert responses plus reread ledger behavior;
  they do not test private helper call order. This is the highest existing seam
  that covers schema, authorization, locking, atomic writes, idempotency and
  recovery together.
- Focused state-codec tests are justified only for version-1/2 read compatibility
  and exact version-3 rendering corruption cases that cannot be constructed
  through a public operation. Existing black-box test helpers remain the prior
  art for all other cases.
- Wrapper contract tests inspect the shipped Skill and progressive-reference
  text to ensure both `0讨论` and `1拷问` carry the stable split criteria,
  confirmation boundary, gate-at-turn-start behavior, dedicated-carrier return
  path, and post-Phase-1 cutoff.
- Repository validation and the full repository validation command remain the
  final integration seam. New operation registration, progressive references,
  schema fixtures, external Skill declarations, and forbidden retired markers
  must still pass.

Acceptance coverage at the CLI seam:

1. Prepare an independent child with no dependencies and verify parent topology no longer changes coverage evidence; bind and accept it normally.
2. Prepare `parent depends on child` and verify topic, relation, handoff, attempt, and closed dependency appear in one revision; inject failure before ledger replacement and verify none appear.
3. Prepare `child depends on parent`, bind and accept the child, then verify first-turn acceptance succeeds but later discussion authorization and substantive topic mutation fail closed.
4. Evaluate a missing prerequisite as blocked with exact waiting details and no ledger write.
5. Publish the required current checkpoint/result/decision authority, evaluate as releasable, confirm the exact set, release, reread, and begin the previously blocked operation.
6. Change a dependency or its evidence between evaluation and release and verify stale release opens nothing; exact replay after success remains idempotent.
7. Release two dependencies together and verify all-open success; make one stale and verify all remain closed.
8. Attempt self-edges, duplicate edges, cross-tree endpoints, OR-like kinds, and direct and multi-hop cycles; verify rejection before handoff/task-attempt state appears.
9. Replace and cancel as the dependent owner; reject the prerequisite owner, stale dependency revisions, and updates after Phase 1; verify explicit reopen restores mutation eligibility.
10. Accept an exact absorbed child result with matching release IDs and verify absorb plus release is atomic; verify impact and omitted IDs leave the gate closed.
11. Adjust, replace, and discard an accepted upstream decision and verify direct matching gates reclose with prior bases retained; verify `keep` and disjoint decisions leave them open.
12. Reopen an upstream Phase Result and verify matching direct gates close but a grandchild gate does not; verify the dependent observes closure only on its next user-triggered turn.
13. Inject reverse-invalidation write failure and verify the upstream authoritative mutation is not published.
14. Verify a closed gate blocks topic updates, handoff preparation, stage-entry checkpoints, carrier readiness, and `0→1`/`0→2`/`1→2` activation while leaving read, recovery, and handoff acceptance available.
15. Close a gate after Phase Run activation and verify the current run can finish, while the next Phase-0/1 turn observes the gate; verify finalization to Phase 2 makes the historical dependency non-enforcing and immutable.
16. Create a child from attached `1拷问` and verify same-tree topology; verify standalone `1拷问` produces no executable cross-tree dependency; verify a dedicated carrier cannot prepare or release a dependency.
17. Load representative schema-version-1 and version-2 ledgers without mutation, then perform one authorized write and verify a version-3 ledger with an empty Topic Dependencies section and unchanged prior authority.
18. Keep a checkpoint referenced by an active accepted basis out of GC candidates, cancel the dependency, and verify it becomes eligible under the existing GC rules.
19. Exercise known failure, outcome-unknown, retry, late-arrival, and duplicate-bind child-task paths and verify the original initial dependency records are reused, never duplicated.
20. Run the complete repository validation suite to cover all existing discussion, Phase Run, worktree, documentation, and dependency-registration behavior.

## Out of Scope

- Polling, monitoring, notifications, task wakeups, or background gate checks.
- Interrupting a dependent turn or active Phase Run that already began.
- Transitive invalidation or automatic cascade through descendants.
- Executable cross-tree or cross-project dependencies.
- OR expressions and arbitrary Boolean prerequisite formulas.
- Gates or dependency mutations in Stages 2, 3, or 4.
- Creating a new topic directly in Phase 1.
- Inferring dependencies from parenthood, absorption, impact, or implementation
  parallelism.
- Replacing the existing child-task creation, binding, recovery, result-return,
  checkpoint, or Phase Run protocols.
- A general-purpose dependency engine outside the discussion ledger.

## Further Notes

- The semantic requirement summary is presentation and confirmation context; it
  is never an executable selector. Stable record identities and hashes are the
  only basis for release, staleness detection, and reverse invalidation.
- The implementation should prefer one dependency-engine boundary consumed by
  handoff, topic update, checkpoint, and Phase Run owners. Duplicating graph or
  gate logic across those callers would make authorization and recovery paths
  diverge.
- This design introduces one necessary ADR because the separate ledger domain,
  explicit guard model, and non-propagation into Phase Run evidence are durable
  architectural boundaries rather than local implementation details.

## Closure

- Planning source: `263de5870ff717cec3a97891889dbd44475f761c`;
  implementation fixed point: `2e369033174ae223ab93d7cbaea09aa0fac730ab`.
- Accepted implementation candidate:
  `7aab54330383ea8a7ff90815015f455f4efd4372`.
- Independent Standards and Spec reviews both examined that exact fixed
  point/candidate pair and reported no actionable findings.
- Focused discussion-protocol validation passed 108 tests. Full repository
  validation passed all 278 tests with repository state `valid` and no issues;
  `git diff --check` was clean.
- The frozen requirement source remained unchanged. No remote operation was
  performed.
