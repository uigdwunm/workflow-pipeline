# ADR-0003: Carry one Flow Worktree through delivery

Status: Superseded by ADR-0004
Supersedes: ADR-0002

Stage 2 currently writes in the primary checkout, while Stages 3 and 4 create
and remove separate worktrees. This lets unrelated primary-checkout changes
interrupt the flow and divides one delivery across multiple Git lifecycles.

Create one Flow Worktree at Stage 2, or at Stage 3 when Stage 3 is entered
standalone, and retain it through Stage 4. Stage 2 automatically publishes its
accepted planning commit to the latest target branch, asks the user only for a
real merge conflict, then advances the retained Flow Worktree to that planning
merge. An inherited Stage 3 reuses the binding, a standalone Stage 3 creates
one, and retries never create a replacement. Stage 4 owns the final
implementation-and-closure publication and non-force cleanup; standalone Stage
4 keeps its existing behavior.

Git commits, refs, registered worktrees, and one short publication lock remain
authoritative. The supervision protocol adds planning publication to its Git
interface but gains no lease, queue, scheduler, durable execution lifecycle, or
remote authority. The result isolates planning and implementation from
unrelated checkout work while preserving explicit user control over real
conflicts.
