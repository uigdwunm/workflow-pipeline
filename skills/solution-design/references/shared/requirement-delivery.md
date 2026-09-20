# Frozen requirement delivery

Stage 1 owns delivery after semantic confirmation and A freeze/CP, before a
completed footer or B receive. At entry record the source checkout, explicit
target checkout/branch, exact owned Markdown paths and current delivery actor in
the existing controller checkpoint. Prefer preparing ordinary requirements in
that target. A detached source retains its own entry and commit identity.

Use C's `deliver-requirement` with `data={request: PREPARE}`. The prepare request
for `scripts/requirement_delivery.py` is:

```text
{protocol:"requirement-delivery-v1",operation:"prepare",entry:ENTRY,
 requirement:FROZEN_RESULT,target:{repository:TARGET_CHECKOUT,branch:TARGET_BRANCH},
 owned_paths:SORTED_MARKDOWN_PATHS,authorization:ORIGINAL_CONTROLLER_REFERENCE}
```

ENTRY is the current Stage-1 entry request; FROZEN_RESULT is the complete A result,
not a supplied hash. The owned paths include the requirement and stay within the
freeze receipt's ownership (at most 32). An attached Git CP owns exactly its
checkpoint document. Non-Git CP retains its snapshot and cannot satisfy Git
Stage-2 entry. Stage 0 gains no delivery authority.

When the original trusted checkpoint already holds a different successful delivery,
prepare may include `previous` with that complete saved result. The producer
revalidates its actor, source, exact target/path set and actual bounded proof before
reuse. A plain matching hash or reconstructed proof cannot replace this evidence.

The CLI's four operations are prepare, deliver, reconcile and verify. Deliver and
read-only reconcile require `entry` and the complete saved `intent`; verify requires
`entry` and the complete original `result`. All use bounded strict JSON and the
entry adapter's success/error envelope. A seal protects integrity; the trusted
controller checkpoint and authenticated actor establish provenance. Never build
an intent or receipt from document text.

C persists the request, prepared intent, issued state, delivered result and verified
result in `workflow_requirements`. The existing conversation/runner checkpoint
remains the only transaction store. Pause, cancel and outstanding side effects
block further writes; reconciliation can still read actual outcomes. Resume uses
the same actor, authorization, source and pinned package.

Delivery holds the existing repository publication lock, reads source Git blobs,
and commits only owned paths with hooks and filters enabled. Source history is
never integrated. Target paths must match the unique merge-base or already match
source, with clean owned index/worktree. Only the authenticated source checkout
may contribute its own already-frozen working document. Unrelated staged,
unstaged and untracked work is preserved; same-name untracked target files are
conflicts. All paths are checked before writes.

Reconcile checks actual objects before retrying. A trailer selects candidates;
the exact parent, bounded nonempty diff, all owned bytes/modes and target ancestry
prove reuse. Partial writes and staging may contain only original or expected
states. Off-target or ambiguous objects stop. Identical target bytes without
source ancestry or the original delivery proof remain `delivery_unverified`;
never create an empty commit. Unrelated target advancement before side effects
can produce a linked new intent in the same transaction; conflicting advancement
retains the old intent for the original controller's decision.

Success is `state:verified`, carrying source_commit, delivery_commit, target,
target_head, requirement_identity, paths, operation_id and `delivery` (the existing
`{commit,paths,receipt}` proof, or null when source is reachable on target).
It proves target delivery, not B acceptance, Phase completion or successor authority.
Post-commit drift preserves finite completed_evidence with downstream_ready=false;
retain that object and reconcile after its owner restores the required facts.

On any unsuccessful delivery emit:

```text
阶段结果：需求已确认，交付待解决
来源提交：<frozen source commit or original non-Git snapshot>
交付目标：<repository and branch>
文档范围：<exact paths and requirement hash>
恢复依据：<original checkpoint transaction and intent; completed_evidence if any>
下一阶段：none
```

On success consume the actual result in the existing footer: include both commits,
target, paths/hash and proof/checkpoint reference. Keep the document's historical
“拷问完成” marker as semantic confirmation only. Dedicated messages keep their
source-commit/hash delivery ID and add the actual target proof. The controller
still owns receive/accept, source Phase completion, successor ready/activate and
archive. Wrapper claim/CP order follows the existing lifecycle contract.

Stepwise mode waits only at the existing Stage-2 choice; continuous mode proceeds
after verified delivery without another integration confirmation. Stage 2 verifies
source at its source context and delivery at the explicit target, then creates
its usual Flow Worktree and rechecks target proof plus the Flow document. Stage 2
does not complete a missing Stage-1 delivery.
