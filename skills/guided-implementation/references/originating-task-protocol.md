# Originating Task Protocol

The originating task creates the worktree, launches one dedicated task,
independently reviews its committed candidate, and completes integration. It
does not implement code.

## Launch

Confirm that every planning source is committed and list the exact allowed
implementation paths. Choose a new branch and canonical worktree path, call
`start-worktree`, and launch one task in that worktree with the returned
binding. Launch the implementation task with the fixed pair
`model: gpt-5.6-terra` and `thinking: high`; pass `model` and `thinking`
explicitly after verifying the current runtime supports that pair. Include the
complete planning sources, dependency-ordered Tickets, testing basis, flow mode,
confirmed implementation scope, existing-behavior changes, required collateral
changes, explicit out-of-scope items, documentation boundary and
remote-authority boundary in the launch prompt. Do not launch while a material
product, scope, behavior, architecture, compatibility, data, or testing-seam
decision remains unresolved.

## Accept

Require a clean committed candidate plus focused/full checks. The Originating
Task pins the exact candidate commit and independently inspects its diff
against the expected target. It dispatches the Standards and Spec review axes
independently against that exact candidate commit and records their results
separately. Do not accept a candidate until both axes correspond to that exact
commit and have no unresolved actionable findings.
Reject an unplanned feature, behavior change, deletion, replacement, side
effect, or optional adjacent improvement even when its tests pass.
Return every actionable test or review finding to the same Dedicated
Implementation Task and verified worktree; the originating task does not edit
the implementation or create a replacement for ordinary remediation. Any
replacement candidate invalidates both review results. The Originating Task
reruns both axes against that replacement candidate before accepting it. Only
the Originating Task accepts and integrates the candidate. Route only a material
unresolved decision to the user. If the target advanced without changing a
source path, have the same task merge that target in its worktree and repeat
checks and review.

## Integrate and clean

Call `complete-worktree` only after accepting the exact candidate. Success
means the merge commit is on the target and the implementation worktree and
branch are gone. Before-merge failure retains both for correction. If the
command reports `cleanup_failed`, inspect its merge commit and remaining
resource; do not merge the candidate again.

Pass the verified merge commit to Stage 4. No execution state, claim, lease,
proposal receipt, or closure checkpoint is carried forward. Pass ordinary Git
facts, verification/review evidence, exact closure-document paths and flow mode
in the visible Stage-4 handoff. When Stage 3 is attached to a discussion topic,
also pass the exact project/tree/topic identity, actor binding, completed
phase-3 result id and the ledger/topic revisions returned by the required final
`read-topic`; standalone Stage 3 passes `none` for that entire group. Local
completion grants no remote-write authority.
