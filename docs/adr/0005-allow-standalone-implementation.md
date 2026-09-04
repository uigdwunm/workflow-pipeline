# ADR-0005: Allow explicit standalone implementation

Status: Accepted
Supersedes: ADR-0004

Stage 2 remains the required source for Stage 3 in the normal staged and
discussion-attached flows. Its committed planning artifacts and retained Flow
Worktree are the implementation authority for those flows.

Stage 3 may also start from an explicit standalone invocation when the exact
request and repository evidence determine a self-contained implementation
brief: target, observable result, included and excluded scope, failure
semantics, acceptance checks, testing seam, and allowed implementation paths.
Ordinary technical choices may follow repository conventions; any unresolved
material product, scope, behavior, architecture, compatibility, data, or
testing decision blocks entry and routes the user to Stage 1 or 2.

The standalone brief preserves the exact user request and resolved facts in the
dedicated implementation task and both review axes. It is not a requirement,
Spec, ADR, or Ticket and creates no planning artifact. After the entry gate
passes, Stage 3 creates one Flow Worktree from the exact target `HEAD`, records
that commit as the standalone scope base, and retains the worktree for Stage 4.

Standalone Stage 3 is unattached to any Discussion Topic and does not restore a
`1→3` lifecycle transition. A missing or invalid claimed Stage-2 handoff remains
an anomaly and never silently falls back to standalone. This preserves the
verified staged flow while avoiding mandatory planning artifacts for already
self-contained implementation requests.
