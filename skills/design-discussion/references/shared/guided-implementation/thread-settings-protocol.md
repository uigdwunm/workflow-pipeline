# Thread Settings Protocol

Read this reference immediately before a workflow must inherit or verify one
Codex task's model and reasoning effort. The canonical implementation is:

```text
<skill-root>/scripts/thread_settings.py
```

Require `protocol-version` to return exactly `thread-settings-v5`. A missing
script or different version is `workflow_runtime_version_mismatch`: stop and
ask the user to install the same workflow-pipeline version for all five Skills.
Never fall back to defaults, task titles, summaries, ordering, or an unresolved
“inherit current settings” value.

## Resolve a bound task

Use an exact authenticated or tool-returned task ID when the caller already has
one:

```text
python3 <skill-root>/scripts/thread_settings.py resolve \
  --thread-id <exact-thread-id>
```

Use the current-task Adapter only for the task executing the command:

```text
python3 <skill-root>/scripts/thread_settings.py resolve \
  --current
```

Run the command unchanged. `--current` reads the existing `CODEX_THREAD_ID`
as the current thread identity. The resolver first reads `threads.rollout_path`
for that exact ID from `state_5.sqlite` alongside the sessions root, using a
read-only SQLite connection. This index selects the active rollout, including a
suffixed paginated file with `history_base: null`; filename timestamps and other
pages do not select settings. Validate the indexed path beneath the sessions
root, the filename thread ID, the regular file and directory identities, and
`session_meta.id`. Read the latest complete `turn_context` in that file and
recheck the index binding after reading. An absent row, invalid database or
path, changed binding, or active page without complete settings fails closed;
never fall back to an older page after an index error.

Only when the state database is absent, use the existing filesystem path:
one canonical rollout, or one verified `history_base` chain rooted at that
canonical file. Multiple null roots without an index remain ambiguous.
`CODEX_SESSIONS_ROOT`, when configured, also determines the adjacent state
index location; never mix a custom sessions root with another home's index.

Separately treat `CODEX_SESSION_ID`, when present, as the session-lineage
identity and require it to match `session_meta.session_id`. A root task requires
its lineage ID to equal its current thread ID. A native subagent instead
requires a distinct lineage ID and a canonical, distinct
`source.subagent.thread_spawn.parent_thread_id`. Never set or override either
environment variable for the command. On missing, conflicting, ambiguous or
unsupported runtime facts, stop at the caller's stable recovery point.

A successful resolution returns only `protocol`, `source`, `thread_id`,
`model`, `reasoning_effort`, and `turn_id`. The implementation safely opens the
index-bound active rollout or verified filesystem rollout chain below the configured Codex sessions
root and never returns message content or another rollout field. The caller separately requires the
resolved model/effort pair to be advertised by the exact `create_thread` or
`spawn_agent` capability it will use.

## Revalidate before an authorized launch

When a user confirmation binds inherited settings, resolve before displaying
the block and verify immediately before the authorized launch:

```text
python3 <skill-root>/scripts/thread_settings.py verify \
  --current \
  --model <confirmed-model> \
  --reasoning-effort <confirmed-effort>
```

Use `--thread-id <exact-thread-id>` instead of `--current` when the confirmation
binds another frozen task. Exit `0` with `status: match` authorizes the exact
model/effort pair even when `turn_id` advanced. Exit `2` with `status: changed`
invalidates the block; use its current observed receipt to display a fresh
complete block. Exit `1` means settings are unavailable and routes to the
caller's stable anomaly or recovery footer. Never re-resolve after a changed
result merely to bypass confirmation.

Continuous mode resolves immediately before disclosure and performs the exact
launch in the same turn. A user-requested explicit override is recorded as
`user-requested-override`, must be supported by the target capability, and does
not claim current-task inheritance.

## Runtime evidence

The index lookup follows Codex's `find_rollout_path_by_id` in
[the state runtime](https://github.com/openai/codex/blob/main/codex-rs/state/src/runtime/threads.rs).
The state_5 layout and null-root active pages were checked against the local
runtime on 2026-09-06. This is a versioned local adapter, not a guarantee that
private storage schemas remain stable; an incompatible schema fails closed.

Use the shared select-configuration contract for dedicated 0/1, designer, dispatcher, execution and closure roles. This resolver remains read-only. Current supported tool evidence, explicit user/frozen choice and known cost determine the selection; unknown cost is never cheaper. No override means actual inheritance. Record ordinary execution settings without another prompt.
