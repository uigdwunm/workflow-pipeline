# Conversation requirement source

Read for explicit standalone Stage-2 entry from the current conversation or
correction of its snapshot. The primary owns this preparation; the isolated
solution-designer receives only the resulting immutable document.

## Establish scope

Use the user's current request and confirmed decisions in the available
conversation to extract the goal, included scope, non-goals, constraints and
observable acceptance conditions. Read relevant repository context to resolve
technical facts. Distinguish user decisions from repository facts and open
choices; assistant suggestions, silence and tentative assumptions are not
confirmed requirements.

If a missing or conflicting decision would materially change the solution,
ask only that question here, then incorporate the answer. Reuse settled answers.
A missing draft alone is not a reason to ask questions or enter stages 0/1.
If context is unavailable, request the missing facts rather than inventing them.
Ordinary implementation design choices belong in the Spec, not this snapshot.

## Save and freeze

An explicit request to enter Stage 2 authorizes this bounded local preparation:

1. Resolve the target repository and planning target. Use the repository's
   requirement convention, or `docs/requirements/<yyyy-mm-dd>-<short-target>.md`.
   Create one regular in-repository file, with no symlink path components and
   no overwrite of an existing file. A retry reuses its recorded path.
2. Write a concise current-state snapshot of the confirmed requirements and
   relevant facts, identifying the source as the current conversation. It is
   a requirement document, not a transcript or proposed solution. Disclose its
   path and scope summary while continuing; saving settled decisions needs no
   additional confirmation.
3. Before `start-worktree`, commit only this snapshot on the verified planning
   target checkout. Check the exact path's baseline and index state, preserve
   all unrelated staged and unstaged changes, and verify the commit's changed
   paths contain only the snapshot. Never use a blanket add or commit.
4. Read the committed bytes, compute SHA-256, and record absolute path, commit
   and hash. Require the working document to match. The source commit must be
   an ancestor of the target used by `start-worktree`, so its bytes are present
   in the one Flow Worktree. Include the snapshot in `protected_paths`, not the
   child's planning `allowed_paths`.

A failed write, commit or verification uses the existing pre-launch anomaly
with the exact failed operation and recovery point. Preserve successful writes;
reconcile uncertain outcomes and reuse a verified source on retry. Do not
send the user to Stage 1 merely because this mechanical preparation failed.
Once frozen, continue the existing launch and review flow. This entry defaults
to stepwise mode and does not authorize stages 3/4 or additional remote actions.

## Correct the source

Before child launch, a material user correction updates the same snapshot,
commits only that file and replaces its recorded identity before proceeding.
After launch, the child reports the existing anomaly and stops affected work;
the primary resolves the missing decision with the user. Invalidate affected
reviews and planning evidence before refreezing the same source path. Reconcile
the retained Flow Worktree and protected source identity before resuming the
same child. Published planning requires the existing plan-change recovery;
never silently reuse it with a new source. A source from a 0/1 handoff stays
owned by that source stage and cannot be rewritten through this entry.
