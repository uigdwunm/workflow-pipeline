# Repository Coordination and Recovery

Read this reference only before repository inspection, a documentation write or
commit, a workspace decision, or retrying a completed stage-1 checkpoint.

## Baseline and leases

Resolve the repository root read-only and inspect the repository lease before
mutable checkout state. If another task holds it, use its verified base branch
and HEAD as the pinned snapshot; read code only from committed objects. Do not
inspect or mutate its implementation paths.

When available, record attached branch, HEAD, staged diff and
`git status --porcelain=v1 -z --untracked-files=all`. Exclude only verified
managed Skill symlinks resolved through the active registry. The index and
implementation paths must be clean; every other path must be exact recognized
documentation or user work.

Before each documentation write, read the shared document-lease protocol and
acquire `stage: problem-framing`, `purpose: document-write`. Verify immediately
before the bounded edit, renew if necessary and release before waiting. A held
repository lease does not block lease-owned documentation writes, but it does
block staging, commits and workspace actions.

## Stage-owned commit

Freeze accepted draft bytes, then recheck the repository lease. When available,
revalidate descendant HEAD and documentation-aware status, stage only exact
stage-owned documentation paths, verify the staged diff and commit concisely.
Never include a path present in the recorded baseline, another task's document,
implementation output or unrelated staged work. Do not create an empty commit.

If the repository lease remains held, leave the completed draft unstaged and
use the stable pending-planning-commit footer from `templates.md`. On retry,
inspect actual bytes, both leases, branch, HEAD and native document blobs; do
not repeat questioning or an uncertain write.

## Workspace and recovery

Unknown user-owned work uses the stable workspace-decision block from
`templates.md`. Generic approval permits inspection and a proposal only.
Before acting, disclose exact directory, commands, flags, pathspecs, affected
paths, commit message and untracked-file treatment. Destructive or
unrecoverable actions require separate exact current-turn authorization.

Retry only one mechanically recoverable document-lease, planning-commit,
delivery, intake, archive or transition action after the understanding is
complete. Inspect actual state first, preserve uncertain effects, and resume
from the last verified checkpoint.
