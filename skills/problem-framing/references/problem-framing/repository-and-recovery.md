# Repository Writes and Recovery

Resolve the primary checkout before any documentation edit. Do not enter or
modify another task's worktree. A planning-source commit made after an
implementation starts is detected at integration and requires user direction.

Record the source checkout, planning target checkout/branch, exact owned paths
and delivery actor in the existing entry checkpoint. Prefer preparing ordinary
requirements directly in the target. For an authorized separate or detached
source, preserve its entry and follow
[target delivery](../shared/requirement-delivery.md) after freeze.
Successful freezing with incomplete delivery remains “需求已确认，交付待解决”.

Edit only stage-owned paths. Use the
[requirement adapter](../shared/requirement-preparation.md)
to compare the expected HEAD, commit exact stage-owned paths and verify committed
bytes. Preserve unrelated staged, unstaged and untracked work; its mere presence
does not block a documentation commit. A conflicting staged version of an owned
path does block it. Worktree implementation activity does not block planning
commits because each run uses its committed base. Other concurrent Git writers
must remain stopped during the exact planning commit.

Unknown user work uses the stable workspace-decision block. Never stash, clean,
reset or overwrite it. Recovery revalidates exact bytes, branch, and HEAD
without repeating completed questioning.
