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

If a missing or conflicting user choice would materially change the requirement
contract, ask only that question here, then incorporate the answer. Reuse
settled answers. Record engineering questions for the designer to resolve in
the Spec under the frozen behavior and constraints.
A missing draft alone is not a reason to ask questions or enter stages 0/1.
If context is unavailable, request the missing facts rather than inventing them.
Ordinary implementation design choices belong in the Spec, not this snapshot.
Freeze the goal and constraints here while leaving downstream file paths
unresolved. The Stage-2 designer derives the exact implementation and closure
manifest from the completed Spec/Tickets; the snapshot's silence about a file
does not mean an empty downstream authorization.

## Save and freeze

Execute the steps below through the
[requirement adapter](../shared/requirement-preparation.md):
prepare/write the snapshot, save its receipt in the existing checkpoint, then
prepare/freeze its exact bytes. Use reconcile with the original intent after
uncertain outcomes. Do not manually assemble commit/hash evidence.

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
3. Record the actual preparation checkout separately from the planning target.
   A suitable existing independent workspace may freeze this snapshot there; the
   target-checkout route remains valid. Commit only this snapshot after checking
   its baseline and index; preserve unrelated staged and unstaged changes. Verify
   the commit's changed paths contain only the snapshot.
4. Retain the adapter's committed source identity and require working bytes to
   match. Reuse verified existing frozen evidence. If the source is outside target
   history, use the existing [bounded target delivery](../shared/requirement-delivery.md)
   for this Stage-2 conversation snapshot under the original Controller authority.
   Keep source and delivery commits distinct; deliver only the authorized document,
   never unrelated source history. Stage-1 and discussion source ownership is unchanged.
5. Verify the same source in the chosen Flow Worktree and target proof before
   finalizing its planning baseline. If controlled source and target delivery
   commits forked, integrate the target only with writers stopped and a clean
   workspace, verifying the delta stays within authorized content. Real content
   conflicts use the existing decision path. Bind `base_commit` to the verified
   target delivery HEAD, not the source's integration HEAD. Binding `allowed_paths`
   must include the frozen requirement path: its incoming source commit remains in
   history even when source and target final bytes are identical. After binding, the
   frozen requirement belongs in `protected_paths`, not designer planning
   `allowed_paths`; its preparation commit is not new planning output.

A failed write, commit or verification uses the existing pre-launch anomaly
with the exact failed operation and recovery point. Preserve successful writes;
reconcile uncertain outcomes and reuse a verified source on retry. Do not
send the user to Stage 1 merely because this mechanical preparation failed.
Once frozen, launch under the explicit stage instruction without another approval.
Use stepwise mode unless the user requested continuous execution; preserve that
authorization through stages 3/4. Additional remote actions require their own authority.

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
