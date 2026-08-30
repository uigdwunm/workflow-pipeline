# Thread Settings Protocol

Read this reference immediately before a workflow must inherit or verify one
Codex task's model and reasoning effort. The canonical implementation is:

```text
<guided-implementation-skill-root>/scripts/thread_settings.py
```

Require `protocol-version` to return exactly `thread-settings-v2`. A missing
script or different version is `workflow_runtime_version_mismatch`: stop and
ask the user to install the same workflow-pipeline version for all five Skills.
Never fall back to defaults, task titles, summaries, ordering, or an unresolved
“inherit current settings” value.

## Resolve a bound task

Use an exact authenticated or tool-returned task ID when the caller already has
one:

```text
python3 <guided-implementation-skill-root>/scripts/thread_settings.py resolve \
  --thread-id <exact-thread-id>
```

Use the current-task Adapter only for the task executing the command:

```text
python3 <guided-implementation-skill-root>/scripts/thread_settings.py resolve \
  --current
```

Run the command unchanged. `--current` reads the existing `CODEX_THREAD_ID`,
requires canonical task identity, requires `CODEX_SESSION_ID` to match when it
is present, and then binds the rollout through `session_meta.id`. Never set or
override either environment variable for the command. On any missing,
conflicting, ambiguous, or unsupported runtime fact, stop at the caller's
stable recovery point.

A successful resolution returns only `protocol`, `source`, `thread_id`,
`model`, `reasoning_effort`, and `turn_id`. The implementation safely opens one
canonical rollout below the configured Codex sessions root and never returns
message content or another rollout field. The caller separately requires the
resolved model/effort pair to be advertised by the exact `create_thread` or
`spawn_agent` capability it will use.

## Revalidate before an authorized launch

When a user confirmation binds inherited settings, resolve before displaying
the block and verify immediately before the authorized launch:

```text
python3 <guided-implementation-skill-root>/scripts/thread_settings.py verify \
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
