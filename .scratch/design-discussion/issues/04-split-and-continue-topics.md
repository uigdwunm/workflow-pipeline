# Split and continue discussion topics

Status: `ready-for-agent`

## What to build

Allow a mature topic to create a recursively nested child discussion or transfer an unavailable topic to a continuation conversation, while preserving exact identity, source evidence and a single active binding.

## Acceptance criteria

- [ ] Child creation records a stable handoff intent and child topic before calling the Codex task-creation surface, then binds only a verified real conversation reference.
- [ ] Handoff payloads use the approved identity envelope, work snapshot and authoritative references with field allowlists, digests and size limits.
- [ ] A created child or continuation performs identity and source verification in its first turn and waits until a later turn before substantive discussion.
- [ ] Setup-pending or outcome-unknown creation cannot be blindly retried; explicit failure, reconciliation, cancellation, late arrival and user-authorized forced retry preserve unique attempt history.
- [ ] Each topic has one active conversation binding; continuation atomically supersedes the old binding while retaining the same topic identity.
- [ ] Child results can be absorbed within their scope; cross-parent or sibling effects become explicit impacts rather than silent edits.
- [ ] Concurrency and fault tests prove that duplicate tasks cannot obtain two active bindings or reuse revoked attempts.

Blocked by: 03 — Publish verifiable discussion checkpoints.

## Comments
