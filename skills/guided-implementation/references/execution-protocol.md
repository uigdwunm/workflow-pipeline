# Dedicated Worktree Execution Protocol

The dedicated task accepts only the binding returned by `start-worktree` and
must run `verify-worktree` against its actual working directory before editing.

Before editing, run the executable `thread-settings-v3` verification:

```text
python3 <guided-implementation-skill-root>/scripts/thread_settings.py verify \
  --current \
  --model gpt-5.6-terra \
  --reasoning-effort high
```

Require exit `0` and `status: match`. Then read every planning source and Ticket
completely. Topologically sort Ticket `Blocked by` edges and preserve source
document order among simultaneously ready Tickets. A missing dependency or
cycle stops for correction.

Treat those accepted planning sources and the launch prompt's exact authority
boundaries as the complete scope. Build a robust solution inside that scope;
the goal is not merely the smallest diff. Resolve ordinary technical details
from the sources, project instructions, and established repository conventions
when doing so does not change confirmed behavior or scope.

Do not add unplanned features, business rules, configuration surfaces,
user-visible behavior, or side effects. Do not remove, replace, or change
existing behavior unless the accepted plan makes that effect explicit. Leave
optional adjacent improvements untouched and report them as out-of-scope
observations rather than implementing them or requesting a decision.

If an unexpected fact makes a material expansion or unconfirmed behavior
change necessary, stop before the affected edit and report the exact planning
gap to the originating task. Resume only after it supplies an explicit
user-authorized resolution. A material question in stage 3 is evidence of an
incomplete plan, not permission to guess.

Work only in the verified worktree and invoke the complete native `$implement`
workflow for the accepted work, including `$tdd`, typechecking, focused tests,
the final full suite and `$code-review`. Keep planning sources read-only.
Implementation commits contain only code, tests and required implementation
artifacts; report documentation paths and intended updates to the originating
task for Stage 4 instead of editing or committing them here.

Before reporting a candidate:

1. require a clean worktree;
2. run focused and full checks;
3. report the exact HEAD and changed paths;
4. complete Standards and Spec review; and
5. disclose findings and remaining risks.

Return review or test remediation to the same implementation task and worktree.
The dedicated task reports an unexpected material planning gap to the
originating task before the affected change and never asks the user directly.
It performs no push, pull request, deployment, release, tracker write or other
remote mutation without separate explicit authority.

If the originating task reports a newer unrelated target HEAD, merge that
target in this worktree, resolve conflicts here, rerun affected checks and
review, and report the new HEAD. A changed planning source, material semantic
conflict, or changed requirement stops for user direction.

The dedicated task never updates the target branch and never removes the
worktree or branch.
