# Dedicated Worktree Execution Protocol

The dedicated task accepts only a verified version-5 handoff and active claim
matching its platform working directory.

Before substantive work, run the shared thread-settings v2 self-check with
`verify --current --model gpt-5.6-terra --reasoning-effort high`. A mismatch
stops at the stable recovery point.

Verify handoff bytes, source checkpoint, claim CAS, cwd, Git common directory,
branch and base. Stop on mismatch and never use another checkout.

Read Tickets fully, topologically sort `Blocked by`, and preserve document order
among ready items. Work only inside the claim. Implementation commits contain
only declared paths; source documents stay unchanged. Outcome notes use
`create-implementation-outcome-proposal` outside the worktree.

Report exact candidate, changed paths, source, claim, focused and full checks,
standards review, requirements review, findings and risks. After repairable
conflict, integrate the supplied latest target, repair locally, rerun every
affected check and review, and publish a new candidate. Material semantic
conflict stops for a user decision.

After publication, keep branch, worktree and claim for Stage 4. The dedicated
task cannot release the claim directly.
