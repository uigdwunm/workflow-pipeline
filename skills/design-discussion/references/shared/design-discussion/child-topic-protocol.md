# Child Topic and Continuation Protocol

Before this role acts, execute [Package execution preflight](../package-execution.md) using this child’s current effective registry. Inherit and verify the controller’s fixed package identity and check each selected action before its side effects.

Read this reference only when the user asks to split a mature topic or continue
an unavailable conversation. This is the action authority for the Codex task
seam; do not duplicate its sequence in the root Skill.

Interpret user confirmation through
[`confirmation-contract.md`](confirmation-contract.md).

## Freeze one user-confirmed direction

Before external task creation, discuss the proposed child or continuation one
item at a time. Require explicit agreement on the goal, scope, relevant
confirmed decisions, open questions and why a separate conversation is useful.
Do not treat a suggestion to split as authorization to create a task.

Complete every pending `DW-*`, publish the latest verified `CP-*` with purpose
`split` for a child or `handoff` for a continuation, and include that checkpoint
in `authoritative_references`. Read
`../guided-implementation/thread-settings-protocol.md`
completely and use `thread-settings-v5` `resolve --current` to inherit the
current task's model and reasoning effort. A user-requested supported pair may
replace inheritance with source `user-requested-override`. Resolve settings
before calling `prepare-handoff` so an unavailable current Adapter or
`workflow_runtime_version_mismatch` creates no external task attempt. Then call
`prepare-handoff` exactly once:

- `handoff_kind: child` creates the child topic and parent relation before any
  external effect;
- `handoff_kind: continuation` retains the current `topic_id` and prepares a
  continuation relation;
- `scope`, `work_snapshot` and `authoritative_references` contain only the
  confirmed, allowlisted context needed by the target; and
- `initial_dependencies`, when confirmed, names only same-tree source/target
  endpoints, one exact requirement kind and semantic result per edge; its
  records are created atomically with the child identity and parent relation;
- the returned `H-*`, `A-*`, identity envelope and digests are the only task
  bootstrap authority. Never reconstruct or edit them in prose.

Show a fixed creation confirmation naming the saved Codex project, child versus
continuation, goal, scope, checkpoint, exact task count (`1`), model, reasoning
effort, settings source, and the absence of remote writes. Only a clear,
unconditional confirmation of that pending action authorizes `create_thread`.

## Create and bind one Codex task

After confirmation, use `verify --current` with the confirmed pair when the
settings were inherited. `status: changed` invalidates the block and supplies
the receipt for a fresh complete confirmation; unavailable verification stops
at the prepared handoff without task creation. A confirmed user-requested
override is instead revalidated from the unchanged block.
Then call `create_thread` once in the same saved project with the exact confirmed
`model` and `thinking` values. The
bootstrap prompt contains only the target role, exact project/tree/topic,
`handoff_id`, `attempt_id`, payload and reference digests, protocol path, and
these rules: locate identity, wait for the active binding if publication races
task startup, call `accept-handoff` as the first completed handoff action, and
perform no substantive discussion in that turn.

Use the exact `threadId` returned by `create_thread` as the authenticated
conversation reference. Immediately call `bind-handoff` with that reference
and the byte-for-byte `verified_identity` returned by `prepare-handoff`. Do not
guess a task ID from title, order, timestamps or transcript text.

After a verified bind, use `send_message_to_thread` only to deliver the exact
binding facts when the task has not yet completed acceptance. Use
`wait_threads` for one bounded progress snapshot; do not create another task
because the target is slow. The child first turn may only:

1. verify the identity envelope, payload digest, authoritative reference digest,
   checkpoint and active binding;
2. call `accept-handoff` with `turn_number: 1`; and
3. report `substantive_discussion_allowed: false` and stop.

On a later turn, the same authenticated child calls
`authorize-handoff-discussion` with a strictly larger `turn_number`. Only a
successful response with `substantive_discussion_allowed: true` permits the
ordinary one-question discussion loop. A first-turn attempt to authorize must
surface `handoff_next_turn_required`; never hide it by changing the turn number.

If a topic has a closed gate, first-turn acceptance remains allowed. On a later
user-triggered turn, apply the shared
[split and requirements-gate contract](split-gate-contract.md) before
authorizing substantive discussion; this child protocol adds only the
first-turn acceptance exception.

## Return and absorb results

The active child submits one scoped result through `submit-child-result`. It
constructs `authority_selection` from exactly one current-topic candidate
returned by `read-topic` as `current_authority_candidates`; this is separate
from `evaluate-topic-gate`, whose candidates describe prerequisites. It uses `authority_kind`, its required `authority_identity` (or
`null` for `confirmed-decision`), and canonically sorted unique `decision_ids`.
When any authority is current, omitting this selection is rejected; when none
is current, the child submits the explicit no-authority result permitted by the
protocol. The resulting frozen descriptor and SHA-256 decision pairs are the
only authority a parent may later use.

The parent reads the claim, presents the frozen descriptor and proposed effect,
and obtains confirmation. For an absorb, construct
`dependency_releases` as a canonically sorted, duplicate-free list of exact
`dependency_id`, `authority_kind`, `authority_identity`, and `decision_ids`
selections that match the child's frozen authority. Omitting releases is valid:
it absorbs the result without opening a gate. A nonempty list is accepted only
with `effect: absorb`; the gate release, accepted basis, handoff absorption,
and result receipt are one ledger transaction, so any rejection or failure
leaves all of them unchanged.

After that confirmation, `record-child-result` follows these rules:

- `record-child-result` with `effect: absorb` is allowed only when the result
  scope is contained by the frozen child scope; it records explicit coverage;
- a cross-parent, sibling or out-of-scope effect uses `effect: impact`; and
- every resulting pending impact is reviewed separately through the ordinary
  impact protocol before parent decisions or lifecycle routing change.

The parent never copies a child conclusion directly into its document. Any
confirmed parent document change still uses a new immutable `DW-*` and the
normal atomic apply operation.

## Continue an unavailable conversation

A continuation uses the same preparation, confirmation, `create_thread`, bind,
accept and later-authorize sequence. `bind-handoff` must observe exactly one
active old binding owned by the source conversation. In its single ledger
transaction it activates `continuation_of`, marks the old binding superseded,
and creates the new active binding while retaining the same `topic_id`.

Never claim that the old conversation was restored. If the old active binding,
owner or provenance changed, stop on the protocol error and prepare no new
handoff until current authority is resolved.

## Creation recovery

- **Explicitly not created:** call `record-handoff-failure`, then
  `retry-handoff` with `forced: false`. The new monotonic `A-*` is the only
  eligible attempt and requires a fresh task-creation confirmation.
- **Unknown result:** call `record-handoff-outcome-unknown`. Inspect the exact
  external result. Use `reconcile-handoff-attempt` with `still-unknown` or
  `not-created`; never blindly retry.
- **Late task:** while the uncertain attempt remains binding-eligible, bind its
  verified real `threadId`. A cancelled, failed or superseded attempt must fail
  with `handoff_late_arrival`.
- **Forced replacement:** only after a user explicitly authorizes the risk,
  call `cancel-handoff-attempt` when applicable and `retry-handoff` with
  `forced: true` plus the exact authorization. This permanently revokes the old
  attempt before creating the next task.
- **Duplicate bind:** treat the first committed active binding as authority.
  Read the handoff and stop; never try to make both tasks active.
- **Ambiguous external effect:** preserve the handoff and attempt, report the
  exact recovery checkpoint, and create nothing else.

At every recovery step, reread `read-handoff` and use the returned ledger and
record revisions. Reusing an idempotency key is valid only for an exact replay.

The child is one independent Workflow Controller. It inherits no parent preference or continuous authorization. Its first turn only accepts the handoff; a closed gate blocks preparation, creation, writes and entry until a later user-triggered continuation rechecks dependencies.
