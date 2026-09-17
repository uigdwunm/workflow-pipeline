# Repository Writes and Recovery

Resolve the primary checkout before any documentation edit. Do not enter or
modify another task's worktree. A planning-source commit made after an
implementation starts is detected at integration and requires user direction.

Edit only stage-owned paths. Before a planning commit, require the primary
checkout and index to contain only those paths, compare the expected HEAD,
stage exact pathspecs, commit, and verify the commit. Worktree implementation
activity does not block planning commits because each run uses its committed
base.

Unknown user work uses the stable workspace-decision block. Never stash, clean,
reset or overwrite it. Recovery revalidates exact bytes, branch, and HEAD
without repeating completed questioning.
