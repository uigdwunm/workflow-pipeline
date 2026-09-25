# Package execution and action preflight

Registration provenance and controlled recovery require preparation=workflow-preparation-v3,
stage_transfer=workflow-stage-transfer-v7, workflow_progress=workflow-progress-v11,
and topic_gate=phase-0-1-discussion-v2. The topic gate key separates packages
that allow discussion behind a closed dependency from the older blanket guard.
Entry requests use workflow-entry-v3 and requirement requests use requirement-freeze-v2.
Old pinned runs keep their original packages and records; new packages reject
incompatible exchanges instead of filling missing identity fields or migrating runs.
Runner outer version 6 requires workflow-progress-v11, workflow-stage-transfer-v7
and control compatibility key 6 (JSON schema 4). Discussion request/ledger, requirement-freeze, thread-settings and
supervision flow-worktree-v2 retain their existing versions. Older records require
their original pinned runtime without mutation. Missing implementation_policy in
compatible new input means full; it never supplies direct provenance to an old
candidate. This change does not install packages or update active registrations.

Progression v11 combines Stage Transfer v7 with archive handoff decoupling, so old v9/v10 checkpoints and runner
pins are rejected before any operation, including pause/cancel and requirement
or lifecycle transactions. Runner outer version 6 and control schema 4 are unchanged from the archive handoff release.

Use the [entry adapter]({{resource:shared/references/entry-preparation.md}}) to
collect repository, task, source, settings and action dependencies before this
stage's first side effect. It invokes the preflight below rather than requiring
the Agent to assemble its results. Requirement preparation uses the
[requirement adapter]({{resource:shared/references/requirement-preparation.md}}).
The direct preflight interface below remains supported for existing callers.

Before this stage's first side effect, resolve the original host's complete current
Skill registry. Delegated and Flow entries use the inherited `registry_input` via
the entry adapter, or paired inline `registry` and `registry_context`. The file
contains those same two objects. Scripts reread it for each action; preserve its
reference through runner, carrier, transfer and native bootstrap. Verify the
original host project separately from the actual execution cwd and Flow binding.
Only an undelegated controller in its original checkout may query its actual cwd.
For that direct preflight route, invoke this package's
`<skill-root>/scripts/skill_preflight.py` with one JSON object on stdin:

```json
{"stage":0,"action":"entry","registry_query":{"cwd":"<absolute task project working directory>"}}
```

Replace stage and action with the current operation. The query uses the installed
Codex executable (`CODEX_BIN` when supplied, otherwise `codex` on PATH) and its
read-only `skills/list` interface with a forced refresh. Use the actual task's
project context. A child inherits the original host evidence instead of querying
its Flow directory. Missing provenance blocks without a directory fallback.

The prompt's Available skills list is not a complete registry. An enabled Skill
with `allow_implicit_invocation: false` can be absent from that list and still
be invoked explicitly by this workflow. Never construct registration evidence
from the prompt list or change that policy to make a dependency appear.

A trusted controller may instead supply a complete current host snapshot in
`registry`, with source `host-current-skills` or `controller-current-skills`
and exact name, entry, source and enabled fields. Do not combine `registry`
with `registry_query`. User assertions and filesystem `diagnose` output are not
registration evidence. A failed host query stops with its actual error; never
fall back to a partial prompt list or filesystem candidates.

Require `ok:true`. Save the returned own package identity in the existing
conversation checkpoint; use its canonical `root` as `<skill-root>`. Before each
script launch, restored session or target handoff, invoke that pinned root's
preflight with `{"operation":"verify","identity":<saved identity>}` and require
success. Load scripts and rules only from that fixed root. Switching the registry
symlink affects new runs; this run keeps its original real root. A changed digest,
missing resource or unreadable package stops at the first unfinished action.
Keep the existing session, attempt, accepted result and Flow Worktree. Restore the
original immutable package, or use the existing controller recovery procedure to
end the attempt and reconfirm; never silently upgrade an active run.

Before each Matt call, invoke preflight again with this stage and the action below.
Perform the action only after success. Read and follow the actual registered Matt
Skill; it remains external. A missing capability reports its exact name, purpose,
registration/candidates and recovery. Ask the user to install/enable it and refresh
the task, then retry the same action. Do not install it or implement a substitute.

| Stage | Action before side effect | Required external capability |
| --- | --- | --- |
| 0 | `discuss` | none |
| 1 | `route` | ask-matt |
| 1 | `grill-with-docs` | grill-with-docs, grilling, domain-modeling |
| 2 | `spec` | to-spec |
| 2 | `decide-tickets` | ask-matt |
| 2 | `tickets` | to-tickets |
| 2 | `adr` | domain-modeling |
| 3 | `dispatch` | implement, tdd, code-review |
| 3 | `review` | code-review |
| 4 | `route` | ask-matt |

For another selected legitimate Matt route or a dependency named by the installed
review/document Skill, use `action:"selected-capability"` and
`required_skills:[<exact selected names>]` before calling it. Preserve the stage's
scope: choosing a capability does not authorize another stage. Read the project's
existing tracker, triage and domain conventions first; only if first-time setup is
actually needed use `action:"setup"` to check setup-matt-pocock-skills.

For a single stage, omit `target_stages`. Complete its existing commits and
acceptance even when its successor is absent. Report the real completed stage,
real handoff and retained recovery evidence; name the unavailable successor and
its installation/activation requirement. Do not claim the entire flow completed.
Stage 3 retains its candidate; Stage 4 alone publishes and cleans it.

For continuous mode, freeze the remaining route and pass its exact stages as
`target_stages` before creating a carrier/worktree or activating this run. Resolve
all required targets from the current registry and require matching compatibility
keys. Missing targets block continuous entry, with no silent single-stage fallback.
If the user requests continuous mode after an authorized stage began, finish that
stage while recording the remaining route as blocked. Save all target identities
in the existing checkpoint. Before preparing or dispatching a successor, recheck
its current registration and verify the saved identity. A newly selected version
cannot replace that identity; a link switch may still point new tasks elsewhere
while this run uses its verified original root. Preserve completed results before
reporting a missing successor. Keep the old carrier until the original ready,
activation and archive protocol is satisfied; installation never supplies authority.

The foreground runner belongs only to Stage 3's package. Start requires a current
registry snapshot and freezes runner plus Stage 2/3/4 identities in record version
6. The runner pins the confirmed-input file as `registry_input`, rereads its current
`registry` and `registry_context` before every executor launch, and rechecks all remaining targets on
resume. The trusted host/controller must refresh that evidence when registrations
change; executor filesystem discovery is not registration evidence. To supply a
new controller evidence file, use `resume <record> <answer> --registry-input <file>`;
the file must contain current paired `registry` and `registry_context` objects. This changes evidence only:
execution retains the original package realpaths and identities. Missing,
ambiguous or incompatible current targets block without launching an executor.
Resume retains the record lock, sessions and completed results. Version 1–5 records
are read-only: `legacy_run_requires_original_runtime` means use the retained original
runner and installation tree; never guess identities or migrate/restart the run.
