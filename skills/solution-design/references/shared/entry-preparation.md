# Entry preparation

Use `scripts/entry_prepare.py` from this stage's pinned package. Send one bounded
JSON object on stdin; success is `{ok:true,result:<complete evidence>}`. Failures
have `ok:false,error:{code,message,operation,completed_evidence,recovery}` and exit 1. A digest detects
changed data; it is not authentication or permission.
completed_evidence is an array of verified partial operations, empty when no such
evidence is available. It never substitutes for the successful result. Dependency
and OS exception text is not returned verbatim; malformed operations are reported
as null rather than echoing arbitrary request data.

## Host boundary

The adapter obtains cwd from the executing process, repository/branch/HEAD/common
directory from Git, current task/settings through thread-settings-v5, and complete
current registration through the existing forced-refresh host adapter. It verifies
Flow Worktree bindings through the existing supervision implementation.

The controller supplies project ID, authenticated controller/carrier/source refs,
role and tool receipt reference from the actual host. These cannot be proved by
local Git, arbitrary JSON, task names, prompt Skill lists or document frontmatter.
The host must authenticate them before invoking the adapter. A reference labelled
`receipt` is attribution, not a cryptographic signature. Missing authenticated
host evidence blocks the relevant action; never fill it from a guess.

The controller still interprets user intent, chooses stage/action, resolves
semantic conflicts, follows the existing configuration-selection contract and
obtains its required decisions. This adapter returns the actual current model and
effort, not an invented default. Optional supported configurations must come from
the exact target tool; its absence does not establish launch compatibility.
The actual launch adapter must verify its selected configuration before launch.

## Request

```json
{
  "protocol": "workflow-entry-v1",
  "operation": "resolve",
  "stage": 1,
  "action": "entry",
  "host": {
    "project_path": "<absolute actual task cwd>",
    "project_id": "<tool-returned project ID>",
    "thread_id": "<authenticated current task ID>",
    "controller_ref": "<authenticated controller ID>",
    "role": "controller",
    "source_ref": null,
    "receipt": "<host evidence reference>"
  },
  "source": {"kind": "stage1"},
  "target": {
    "kind": "planning",
    "repository": "<absolute planning checkout>",
    "branch": "<verified planning target branch>"
  }
}
```

The exact required fields are protocol, operation, stage, action, host and source.
Optional fields are target, registry, target_stages, required_skills,
pinned_packages and expected. Unknown fields and duplicate JSON keys fail.
`registry` is the existing trusted complete host/controller snapshot alternative;
otherwise the adapter queries the actual cwd. It never falls back to disk discovery.
Action names and target_stages retain their existing preflight semantics.

Host fields in the example are required; source_ref may be null only for a
controller. Optional actor_ref is the host-authenticated alias for this exact
runtime task (for example a native carrier's tool ref); it is never inferred from
task recency. Without it, only the current thread ID or its codex-thread: form is
accepted as a discussion actor. Optional host.supported_configurations is a nonempty array of exact
`{model,reasoning_effort}` pairs. Roles are controller; dedicated-discussion at 0;
dedicated-problem-framing at 1; solution-designer or scripted-carrier at 2; implementation-dispatcher,
execution-agent or scripted-carrier at 3; closure-agent or scripted-carrier at 4.
The Stage-2 foreground CLI carrier uses scripted-carrier, not controller or the
native solution-designer identity. A scripted carrier must name its authenticated
launch source and external controller; neither may identify the current carrier.
It retains the runner's authority and does not gain controller permissions.

Source kinds:

- `none`: no requirement selected; not a complete downstream launch input.
- `stage1`: mutable standalone Stage-1 draft, optionally with exact relative path.
- `conversation`: standalone Stage-2 snapshot, optionally with exact relative path.
- `frozen`: existing immutable source, with exact relative path for verification.
- `discussion`: exactly kind and attachment. Attachment contains project_id,
  tree_id, actor_topic_id and actor_conversation_ref. The existing read-topic
  protocol verifies ownership and returns current revisions, pending writes,
  checkpoints and authority candidates. This is not phase activation permission.

Target may be omitted for read-only/non-Git discussion entry. A Flow Worktree uses
`{kind:"flow",binding:<existing exact binding>}`. Do not create another worktree
to satisfy entry preparation. Standalone document mutations require the explicit
planning target and execution at its checkout root.

## Result and freshness

The complete result contains protocol, repository, actor, entry, target, packages,
external, configuration, requirement and evidence_digest. Save it unchanged in
the existing conversation checkpoint or runner record. Do not splice extra fields
into the strict Workflow Control context.

Use operation=verify and expected=<saved result> immediately before dependent
work. It compares repository, actor, stage/action, target and source facts;
settings may advance turn_id without changing model/effort. Missing registration,
changed source, identity, settings or incompatible packages stops the action.
It verifies retained package bytes and keeps the original roots even if current
registration selects another compatible version. `pinned_packages` supplies that
same original map when preparing a new action in an existing run; never use it to
invent or change a pin. Installed scripts must execute from the stage's pinned root.

Requirement writes intentionally change source bytes/HEAD. Their adapter rechecks
the immutable owner/package/configuration facts and uses its own exact prepared
baseline to reconcile those changes. Do not repeatedly resolve fresh evidence to
bypass a failed confirmation or source check.

## Downstream boundary

This result supplies facts to B; it does not create, bind, dispatch or accept tasks.
C persists it in the existing runner and invokes verify/reconcile on resume; A does
not advance the route. D consumes protected source identity and Git binding; A does
not publish planning, merge or clean up. Old pinned runs retain their original
runtime and records. No migration, installation or project-scope change is implied.
