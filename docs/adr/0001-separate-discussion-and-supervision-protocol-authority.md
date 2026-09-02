# ADR-0001: Separate discussion state from worktree execution

Status: Accepted

## Context

`design-discussion` maintains durable topics, decisions, relations, checkpoints,
handoffs, Phase Runs, and lifecycle state. The delivery stages only need to
create, verify, publish, and finally remove one isolated Flow Worktree.

Combining these concerns would couple frequent discussion-ledger changes to Git
worktree operations and would create workflow state that duplicates facts Git
already owns.

## Decision

- `discussion_protocol.py` exclusively owns the discussion ledger, topic and
  conversation identity, pending items, relations, impacts, discussion
  checkpoints, Phase Runs, and lifecycle transitions.
- Topic document updates and discussion checkpoint refs are serialized by the
  discussion protocol's own short lock.
- `supervision_protocol.py` exposes only the Git worktree operations accepted by
  the current worktree-lifecycle ADR. It derives authority from the repository,
  branch, worktree, commits, and caller-supplied committed paths.
- Supervision persists no execution lifecycle, claim, lease, queue, handoff, or
  closure record. Its only shared coordination is a short file lock held while
  publishing to the target branch.
- The protocols do not exchange authorization receipts or copy one another's
  state.

## Consequences

- Discussion recovery remains independent of implementation execution.
- Git is the single authority for active flow branches and worktrees.
- Cross-domain lease and state-version compatibility code is unnecessary.
- Existing stages 1–4 can run without a discussion context when discovery does
  not identify one exact topic.
