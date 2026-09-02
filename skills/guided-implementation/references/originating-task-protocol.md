# Originating Task Protocol

The originating task binds the Flow Worktree, launches one dedicated task,
independently reviews its committed candidate, and passes the retained flow to
Stage 4. It does not implement code.

## Launch

Confirm that every planning source is committed and list the exact allowed
implementation paths. A valid Stage-2 handoff must be verified and reused.
Standalone Stage 3 chooses a new branch and canonical worktree path and calls
`start-worktree`; a claimed but invalid upstream binding is an anomaly, not a
standalone fallback. Launch one task in the resulting Flow Worktree with the
verified binding. Launch the implementation task with the fixed pair
`model: gpt-5.6-terra` and `thinking: high`; pass `model` and `thinking`
explicitly after verifying the current runtime supports that pair. Include the
complete planning sources, dependency-ordered Tickets, testing basis, flow mode,
confirmed implementation scope, existing-behavior changes, required collateral
changes, explicit out-of-scope items, documentation boundary and
remote-authority boundary in the launch prompt. Do not launch while a material
product, scope, behavior, architecture, compatibility, data, or testing-seam
decision remains unresolved.

## Accept

This review contract is authoritative. Require a clean committed candidate plus
focused/full checks. The Originating Task pins the exact candidate commit and
the expected target-branch commit as one review fixed point, independently
inspects the candidate diff against that fixed point, and passes that same fixed
point and candidate to both Standards and Spec review axes. It dispatches the
Standards and Spec review axes independently and records their results
separately. Do not accept a candidate until both axes correspond to that exact
fixed point and candidate commit and have no unresolved actionable findings.
Reject an unplanned feature, behavior change, deletion, replacement, side
effect, or optional adjacent improvement even when its tests pass.
Return every actionable test or review finding to the same Dedicated
Implementation Task and verified worktree; the originating task does not edit
the implementation or create a replacement for ordinary remediation. Any
replacement candidate invalidates both review results. The Originating Task
reruns both axes against that replacement candidate before accepting it. Only
the Originating Task accepts the candidate; Stage 4 integrates it. Route only a material
unresolved decision to the user. If the target advanced without changing a
source path, the same Dedicated Implementation Task merges that target, runs
affected and full checks, and commits a replacement candidate. The Originating
Task then establishes its exact replacement fixed point and reruns both axes.

## Retain and hand off

After accepting the exact candidate, verify that it is still the clean Flow
Worktree `HEAD`. Do not update the target branch and do not remove the worktree
or branch. Pass its exact binding, candidate commit, planning merge or
standalone base commit, implementation and closure path scopes, protected
requirement-source paths, verification/review evidence, exact closure-document
updates, and flow mode to Stage 4. No claim, lease, proposal receipt, or closure
checkpoint is carried forward.

When Stage 3 is attached to a discussion topic, also pass the exact
project/tree/topic identity, actor binding, completed phase-3 result id and the
ledger/topic revisions returned by the required final `read-topic`; standalone
Stage 3 passes `none` for that entire group. Local completion grants no
remote-write authority.
