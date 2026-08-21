# Split and continue discussion topics

Status: `ready-for-agent`

## What to build

Allow a mature topic to create a recursively nested child discussion or transfer an unavailable topic to a continuation conversation, while preserving exact identity, source evidence and a single active binding.

## Acceptance criteria

- [x] Child creation records a stable handoff intent and child topic before calling the Codex task-creation surface, then binds only a verified real conversation reference.
- [x] Handoff payloads use the approved identity envelope, work snapshot and authoritative references with field allowlists, digests and size limits.
- [x] A created child or continuation performs identity and source verification in its first turn and waits until a later turn before substantive discussion.
- [x] Setup-pending or outcome-unknown creation cannot be blindly retried; explicit failure, reconciliation, cancellation, late arrival and user-authorized forced retry preserve unique attempt history.
- [x] Each topic has one active conversation binding; continuation atomically supersedes the old binding while retaining the same topic identity.
- [x] Child results can be absorbed within their scope; cross-parent or sibling effects become explicit impacts rather than silent edits.
- [x] Concurrency and fault tests prove that duplicate tasks cannot obtain two active bindings or reuse revoked attempts.

Blocked by: 03 — Publish verifiable discussion checkpoints.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
- 2026-08-21 后续加固把原先停在“later protocol”占位的用户流程接到真实 Codex task seam：父题确认与最新 CP、prepare、独立创建确认、`create_thread`、真实 `threadId` bind、首轮 accept、后续 authorize、结果吸收/impact、continuation 原子 supersede 及完整创建恢复均已有 action authority 和机械验收。
