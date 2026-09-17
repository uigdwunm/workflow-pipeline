# Dependency contract

The optional discussion stage and the four established stages invoke other Skills by their registered names. Installation location is intentionally not fixed; the active Codex Skill registry is authoritative at runtime.

## Self-contained packages and current registration

Each of the five packages carries its own internal resource closure. No other workflow Skill is required to complete a single stage. A real cross-stage transition resolves the target from the host's current effective Skills registry, verifies its manifest and bundle digest, and requires an exactly equal compatibility key. Release versions need not match. Shared `thread-settings-v5`, control, discussion and worktree scripts are package-local; they retain the same canonical project/Git coordination state and locks.

Use the host's exact registered name, entry and source. A trusted controller may supply a current snapshot; user assertions and filesystem candidates are not registration evidence. Each native child checks its own registry. Missing, ambiguous, unreadable, inactive and incompatible targets stop the affected action with recovery information; there is no automatic installation or replacement implementation.

## Conditional Matt capabilities

| Stage / action | Required capability before the action |
| --- | --- |
| 0 `discuss` | none |
| 1 `route` | `ask-matt` |
| 1 `grill-with-docs` | `grill-with-docs`, `grilling`, `domain-modeling` |
| 2 `spec` | `to-spec` |
| 2 `decide-tickets` | `ask-matt` |
| 2 `tickets` | `to-tickets` |
| 2 `adr` | `domain-modeling` |
| 3 `dispatch` | `implement`, `tdd`, `code-review` |
| 3 `review` | `code-review` |
| 4 `route` (only when representation needs routing) | `ask-matt` |
| selected review/document/other legitimate route | exact capabilities named by that installed Skill |
| first-time setup, only when needed | `setup-matt-pocock-skills` |

Unused branches do not become entry gates. Stage 4 with clear existing document representation does not invoke `ask-matt`. Read existing project conventions before setup. Matt Skills remain external and are neither bundled nor automatically installed. The manually reviewed compatibility baseline is [mattpocock/skills](https://github.com/mattpocock/skills) commit `84fdeffd12f2ee307994d1eb6feb48173b6e0502` (`1.2.3`, MIT); package integrity checks do not prove an installed Matt version matches this baseline. Read the actual registered Skill and revalidate affected routes after upstream changes.

## Fixed identities and recovery

Pin real package roots, entry paths, digests and protocol keys in the existing checkpoint. Verify them before script launch, session recovery and handoff. Registry link changes affect new runs; an existing run retains its original root. Install upgrades into new immutable directories and keep old versions until active runs finish. Changed or missing resources stop at the existing recovery point.

Continuous mode checks the entire remaining route before new carriers/worktrees or activation, then rechecks at handoff. Missing targets never authorize a silent single-stage fallback or premature archive/Phase advance. A stage already authorized and running completes and retains its result. Stage 3 never publishes in place of missing Stage 4; merge-success recovery remains cleanup-only.

Runner v2 pins its own and Stage 2/3/4 identities. Its `registry_input` points to the confirmed JSON evidence file, reread before executor launches and on resume. The trusted host/controller must refresh its `registry` when registrations change. `resume <record> <answer> --registry-input <file>` selects a new current evidence file containing `registry`, without changing pinned identities. v1 records are read-only to the new runner and return `legacy_run_requires_original_runtime`; use the retained original runtime and installation tree, without migration or restart.

See the source [package execution contract](../src/shared/references/package-execution.md) for the preflight JSON interface.

## Optional subagent orchestration adapter

`solution-design` submits one semantic stage request to the current
native-subagent orchestration adapter. When runtime instructions require
Subagent Governance, that plugin is the adapter. The adapter owns native tool
arguments, exact-target binding, liveness, waiting, terminal normalization,
interruption and bookkeeping; `solution-design` retains the canonical stage
payload, permissions, review and anomaly states, and completion contract.

This is an environment compatibility path, not a hard installation dependency
for ordinary users. Never hardcode adapter installation or runtime state, private
schema, version, timeout or polling algorithm in this repository. If the
required adapter cannot establish one trusted child identity, dispatch stops at
a pre-launch anomaly rather than falling back silently.

This adapter seam does not own Workflow domain state. Discussion Topics, Topic
Dependencies and Gates, discussion handoffs, Phase Runs, Phase Carriers and Flow
Worktrees remain defined and enforced by this repository's protocols.

## Validation contract

`./scripts/validate.sh` checks source tests, deterministic package generation, five entry points, package-local references/imports, external names and portable paths. Tests exercise real JSON CLIs and Git/ledger boundaries; controlled registries and fake executors do not prove real host Agent behavior.

`./scripts/check-dependencies.sh --stage <name-or-number> --action <action> --project <project>` reports filesystem candidates only. Optional repeated `--root` bounds diagnostic roots and `--required-skill` names selected capabilities. Default roots include configured `CODEX_HOME/skills` and conventional user roots; `--project` adds project `.agents/skills` and `.codex/skills`. Legitimate links are resolved and identical real entries deduplicated. Multiple disk candidates do not override a unique current registry selection. Filesystem availability never means Agent activation or compatibility.

The acceptance record distinguishes 368 passing automated tests and temporary `skills@1.6.0` installation checks from unexecuted T13/T14 host flows. Generate their worksheet with `python3 scripts/field_acceptance.py`. Local installation synchronization is a separate authorized action, not part of validation or closure.
