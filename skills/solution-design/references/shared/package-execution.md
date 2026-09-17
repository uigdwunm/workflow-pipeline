# Package execution and action preflight

Before this stage's first side effect, resolve this entry from the current
host's effective Skills list. Invoke this package's
`<skill-root>/scripts/skill_preflight.py` with one JSON object on stdin:

```json
{"stage":0,"action":"entry","registry":{"source":"host-current-skills","entries":[{"name":"design-discussion","entry":"<absolute registered SKILL.md>","source":"<host registration source>"}]}}
```

Replace stage/name/path with this stage's actual registration. Include the exact
current effective entries needed by the action, including external Matt Skills.
A controller may pass its authenticated current snapshot using
`controller-current-skills`; user text asserting registration is not evidence.
Every native child checks its own current Skills list before its action.
Filesystem `diagnose` output is never proof that an Agent can invoke a Skill.

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
2. The runner pins the confirmed-input file as `registry_input`, rereads its current
`registry` before every executor launch, and rechecks all remaining targets on
resume. The trusted host/controller must refresh that evidence when registrations
change; executor filesystem discovery is not registration evidence. To supply a
new controller evidence file, use `resume <record> <answer> --registry-input <file>`;
the file must contain the current `registry` object. This changes evidence only:
execution retains the original package realpaths and identities. Missing,
ambiguous or incompatible current targets block without launching an executor.
Resume retains the record lock, sessions and completed results. Version 1 records
are read-only: `legacy_run_requires_original_runtime` means use the retained original
runner and installation tree; never guess identities or migrate/restart the run.
