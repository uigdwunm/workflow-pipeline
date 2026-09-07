# Dedicated Worktree Execution Protocol

The dedicated task accepts the verified Flow Worktree binding inherited from
Stage 2 or created by a qualified standalone Stage-3 entry. It must run
`verify-worktree` against its actual working directory before editing.

Before editing, run the executable `thread-settings-v5` verification:

```text
python3 <guided-implementation-skill-root>/scripts/thread_settings.py verify \
  --current \
  --model gpt-5.6-terra \
  --reasoning-effort high
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
workflow for the accepted work, including `$tdd`, typechecking, focused tests,
and the final full suite. Keep planning sources read-only when present.
Implementation commits contain only code, tests and required implementation
artifacts; report documentation paths and intended updates to the originating
task for Stage 4 instead of editing or committing them here.

Before ending any execution turn, report one result block:

```text
结果类型：<candidate | checkpoint | blocked>
当前 HEAD：<commit>
已完成：<completed scope>
剩余：<remaining scope | none>
验证：<focused and full checks | checks run for this checkpoint>
阻塞：<specific decision gap | none>
```

Use `candidate` only after all confirmed work is complete and the clean exact
HEAD has focused and full verification. Use `checkpoint` for clean committed
in-scope progress when planned work remains; the Originating Task will continue
the same task from that HEAD. Use `blocked` only for a concrete condition that
prevents the next edit and identify the decision or evidence required to clear
it. A large remaining scope or the absence of a final candidate is unfinished
work, not a blocker.

Before reporting a `candidate`:

1. require a clean worktree;
2. run focused and full checks;
3. report the exact HEAD and changed paths;
4. disclose remaining risks; and
5. retain the verified worktree for remediation.

The Dedicated Implementation Task must not dispatch Standards or Spec review
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
repair in scope and rerun affected and full checks in this same worktree.
If diagnosis exposes an insufficient scope or plan, stop for the existing
implementation-authority/anomaly decision; ordinary defects continue here.
The dedicated task reports an unexpected material implementation-authority gap
to the originating task before the affected change and never asks the user directly.
It performs no push, pull request, deployment, release, tracker write or other
remote mutation without separate explicit authority.

If the originating task reports a newer unrelated target HEAD, merge that
target in this worktree, resolve conflicts here, rerun affected checks and
report the replacement candidate. The authoritative role contract determines
the Originating Task's review action. A changed planning source, material
semantic conflict, or standalone-brief conflict stops for user direction.

The dedicated task never updates the target branch and never removes the Flow
Worktree or branch. It returns the clean accepted candidate in that retained
worktree for Stage 4.
