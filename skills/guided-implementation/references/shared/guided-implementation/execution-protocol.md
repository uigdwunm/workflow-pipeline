# Native Implementation Dispatcher Protocol

In a foreground run, retain the original native identity across carrier turns.
For a controlled recovery successor, first read the exact handoff/control fields
from the supplied checkpoint and run entry verification with registration_input.
Prepared write_authority:false permits inspection only. Begin writes only after
C's recover-dispatch receipt proves this exact real ref is the current dispatcher
under the new attempt and original authority/snapshot. Preserve accepted ownership;
revalidate historical allocations or obtain the Controller's exact release before
new allocation. Never promote inherited test strings to candidate verification.
Execution allocations remain in C/B and the control executions roster. On a
pause/cancel request, reconcile every issued allocation and stop its bound
Execution Agents before reporting root stop; an unknown or late creation receipt
keeps the barrier open. A terminal dispatcher turn is neither Stage-3 completion
nor proof that descendants stopped. Follow the shared progression next action.

Mechanical A/B execution uses [Workflow progression](../workflow-progression.md). Supply semantic decisions and authenticated raw host responses; the shared adapter retains intents, exact attempts, receipts and recovery steps. The role, review and authorization rules below remain binding.

Use its allocation operation for ordinary Execution Agents, with the original
checkpoint passed by the controller. Do not start a second workflow for each
child or copy the dispatcher's control into another authoritative record.
Allocation receipts and acceptance update the same control roster consumed by
the originating task's candidate intake. Existing native governance still owns
real launch, waits, stopped-writer proofs and interruption.

Before this role acts, execute [Package execution preflight](../package-execution.md) using the inherited registration_input fields from the verified handoff. Pass them unchanged to entry_prepare.py with this role’s actual host identity and cwd; keep registration_identity as the frozen source identity. File evidence is reread by the adapter, while inline registry/context remain paired. Missing evidence stops before work. Inherit and verify the controller’s fixed package identity and check each selected action before its side effects.

The dispatcher accepts the verified Flow Worktree binding inherited from
Stage 2 or created by a qualified standalone Stage-3 entry. It must run
`verify-worktree` against its actual working directory before editing.

Verify the selected configuration receipt against current adapter evidence.
When a current runtime identity is resolvable, run the executable `thread-settings-v5` verification:

```text
python3 <skill-root>/scripts/thread_settings.py verify \
  --current \
  --model <selected-model> \
  --reasoning-effort <selected-effort>
```

Require exit `0` and `status: match`. For an inherited flow, read every planning
source and Ticket completely, topologically sort Ticket `Blocked by` edges, and
preserve source document order among simultaneously ready Tickets. A missing
dependency or cycle stops for correction. For a standalone flow, read the exact
request and fixed implementation brief from the launch prompt and preserve them
unchanged.

Before implementing the remaining Tickets or sibling paths, start TDD with one
representative vertical slice. Read the Spec's owner, Interface, real
production call-chain, and Testing Decisions; choose an observable behavior
that crosses the changed internal boundary through the production caller
wiring; write the smallest failing test at that boundary; and make it pass with
the minimal in-scope implementation. Only after that slice passes may the task
expand the same pattern to equivalent in-scope paths.

The changed boundary and its production caller path may not be replaced by a
mock, stub, fake, or in-memory substitute. Doubles remain allowed for external
systems or dependencies downstream of the chosen boundary when the Spec's seam
requires variability. If the repository has no executable seam that exercises
the changed boundary, report an implementation-authority/testing-seam gap to
the originating task before coding; do not bypass the boundary to obtain a
green test.

Treat those accepted planning sources or the fixed standalone brief, together
with the launch prompt's exact authority boundaries, as the complete scope.
Build a robust solution inside that scope; the goal is not merely the smallest
diff. Resolve ordinary technical details
from the sources, project instructions, and established repository conventions
when doing so does not change confirmed behavior or scope.

Do not add unplanned features, business rules, configuration surfaces,
user-visible behavior, or side effects. Do not remove, replace, or change
existing behavior unless the accepted plan makes that effect explicit. Leave
optional adjacent improvements untouched and report them as out-of-scope
observations rather than implementing them or requesting a decision.

If an unexpected fact makes a material expansion or unconfirmed behavior
change necessary, stop before the affected edit and report the exact
implementation-authority gap to the originating task. Resume only after it
supplies an explicit user-authorized resolution. A material question in stage
3 is evidence of incomplete authority, not permission to guess.

Work only in the verified worktree and invoke the complete native `$implement`
workflow for the accepted work, including `$tdd`, typechecking, focused and affected tests before review, then the frozen final suite only after
the original Controller confirms review convergence. Keep planning sources read-only when present.
Implementation commits contain only code, tests and required implementation
artifacts; report documentation paths and intended updates to the originating
task for Stage 4 instead of editing or committing them here.

Before ending any execution turn, report one result block:

```text
结果类型：<reviewable | candidate | checkpoint | blocked>
当前 HEAD：<commit>
已完成：<completed scope>
剩余：<remaining scope | none>
验证：<focused and full checks | checks run for this checkpoint>
阻塞：<specific decision gap | none>
```

Use `reviewable` after all implementation scope and review-required checks are
complete at a clean committed HEAD. Return this checkpoint for independent review;
final validation remains pending. Use `candidate` only after all confirmed work is complete and the clean exact
HEAD has focused and full verification. Use `checkpoint` for clean committed
in-scope progress when planned work remains; the Originating Task will continue
the same task from that HEAD. Use `blocked` only for a concrete condition that
prevents the next edit and identify the decision or evidence required to clear
it. A large remaining scope or the absence of a final candidate is unfinished
work, not a blocker.

Before reporting a `candidate`:

1. require a clean worktree;
2. retain focused checks and the same-commit final attempt authorized after review;
3. report the exact HEAD and changed paths;
4. disclose remaining risks; and
5. retain the verified worktree for remediation.

The Implementation Dispatcher must not dispatch Standards or Spec review
agents or receive the parent Session or CLI. It owns Ticket-ordered
implementation, TDD, focused/full validation, candidate commits, risk
reporting, and remediation only. The Originating Task owns `$code-review`,
and candidate acceptance; Stage 4 owns integration. The authoritative review contract is in
[originating-task-protocol.md](originating-task-protocol.md).

Return review or test remediation to the same implementation task and worktree.
When the Originating Task identifies the same failure mechanism after a fix, a
same-class regression in another affected path, or successive review/test
outcomes overturning the same implementation approach, the continuation is
diagnosis-first and carries the trigger plus the prior candidate/finding
identities. Before editing, provide execution evidence comparing the prior
failure and fix, explaining why the fix missed the mechanism, naming the
minimal effective validation at the affected real boundary, and listing the
inspected sibling paths with applicability. Run that focused validation, then
repair in scope and rerun affected checks in this same worktree.
If diagnosis exposes an insufficient scope or plan, stop for the existing
implementation-authority/anomaly decision; ordinary defects continue here.
The dispatcher reports an unexpected material implementation-authority gap
to the originating task before the affected change and never asks the user directly.
It performs no push, pull request, deployment, release, tracker write or other
remote mutation without separate explicit authority.

If the originating task reports a newer unrelated target HEAD, merge that
target in this worktree, resolve conflicts here, rerun affected checks and
report the replacement candidate. The authoritative role contract determines
the Originating Task's review action. A changed planning source, material
semantic conflict, or standalone-brief conflict stops for user direction.

The dispatcher never updates the target branch and never removes the Flow
Worktree or branch. It returns the clean accepted candidate in that retained
worktree for Stage 4.

Execution Agents use plan-execution before native spawn and assign after the actual
receipt as specified in [workflow-control-protocol.md](workflow-control-protocol.md).
They have no Git mutation authority; the dispatcher integrates verified bytes.

## Review-first final validation

Read the frozen `validation_plan` in the existing control checkpoint. Implement all
Tickets, run `review_required`, and commit a clean reviewable checkpoint. Report
`candidate-ready` through C control using the original carrier attempt, exact
commit, target, plan digest and structured results. This never completes B.
The Originating Task records two independent review responses and issues
`review-converged`. Only then consume C's `continue-host` validation attempt on
this same dispatcher identity. Run every `final_required` item from that attempt;
retain the original command outputs and observed source state for Controller
verification. Final full validation and required real environment acceptance are
mandatory before delivery, never prerequisites for initial or replacement review.

A failed attempt remains immutable. Retry the complete final list only after the
Controller names that failed attempt; reuse review only while commit, target,
plan, binding and source state remain unchanged. A code or target change returns
to implementation, focused/affected checks and both review axes before another
final attempt. Unknown command completion requires original-host reconciliation,
not replay. Pause/cancel intent precedes all new business actions.
