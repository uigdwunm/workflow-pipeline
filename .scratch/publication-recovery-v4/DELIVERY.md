# D publication/recovery v4 delivery

Base: `508afc302e6338f62eced44ebe3c905bd9cffa56`.
Branch: `codex/publication-recovery-unified-fix`.
Implementation was authorized after read-only intake and explicit confirmation
of the complete unified plan. This is an unmerged candidate for coordinator review.

## Result and boundaries

The repair separates actual invocation identity from raw response content and
makes technical/no-progress business blocks durable. The new bounded Controller
recovery records diagnosis, instruction and expected progress against the exact
original block; it neither changes native identity nor supplies runtime proof.
Existing B dispatcher replacement remains a distinct explicitly verified recovery.
No ordinary resume, host observation, binding or publication bookkeeping clears a
business block. Host facts and B transaction replay remain separate.

`workflow-progress-v4` is a compatibility break. New operations also reject old
members through the prepare-requirement/lifecycle routes, before writes. Five
packages are rebuilt from source. Runner outer version 3 and supervision
`flow-worktree-v2` remain unchanged; old pinned records/packages are not migrated.
A/B, workflow_control and supervision scripts are unchanged. No main merge, push,
installation, deployment, original worktree edit or external plugin edit occurred.

## Source changes

- `src/shared/scripts/workflow_progress.py`: invocation/response provenance,
  sticky provenance-conflict rejection, current-state/action reconciliation,
  independent last-stop record; exact durable business blocks and recovery;
  existing dispatcher recovery bound to its original block; publication intake
  quiescence checks; compatibility guard before ancillary operations.
- `src/stages/guided-implementation/scripts/workflow.py`: explicit
  recover-business transport and persisted-decision readback, original CLI
  session continuation, business-block guard and host adapter prompt.
- `workflow_stage_result.schema.json`, shared workflow-progression and
  worktree-execution contracts, and `build/skill-packages.json`: v4 contract/key.
- Runtime tests: no unique raw nonce in host fixture; three baseline failures;
  recovery/authorship/interruption/late-result cases; Stage4 and published intake;
  runner transport; no changes to publisher deletion-protection assertions.
- `skills/*`: generated package bodies and manifests only, produced by build.

## Authority and transitions

| Input | Allowed update | Forbidden implication |
| --- | --- | --- |
| New authenticated invocation response | Current host projection and proof | Raw equality is not replay identity |
| Same response replay | ACK; conflict remains rejected on retry | Cannot renew consumed/revoked proof |
| Adverse original-ref fact | Revoke proof; retain last stop before business validation | Unordered stop is not new quiescence proof |
| Current query with unresolved action | Settle exact terminal action outcome plus current state | Idle alone cannot settle a possibly pending mutation |
| B receive/accept replay | Original business/control/pending/dedup consumption | No current-host projection changes |
| technical_error / no_progress | Durable exact business block and trigger | No automatic clearing by resume or later business result |
| recover-business | Exact reviewed decision, history, one recovery intent, old proof invalidation | No new identity, scope, Phase or acceptance authority |
| Existing recover-dispatch | Clear only the block bound before verified B recovery | Old control replay cannot clear a new failure |
| Pause/cancel | Stop/reconcile; defer authorized recovery during pause | Deferred recovery never clears pause/cancel by itself |
| Published result | Exact Git reconciliation and original intake/acceptance | No republish or native implementation restart |

Call/response/action references are supplied by the trusted adapter from real tool
provenance, outside unchanged raw. C verifies consistency, not authenticity of
arbitrary JSON. `action_resolution` is required when a query must settle an old
unresolved action. Missing lookup support or terminal evidence stays unknown.
Neither this field nor a hash manufactures original host authority.

## Evidence

`RED.txt` records three independently failing baseline tests before production
changes: repeated raw stop emitted continuation; Stage2 technical error lacked a
recovery action; plain resume cleared no-progress. Those assertions remain.

Targeted checks before the final full run:

- `UNIFIED.txt`: 27 tests passed, including the three original counterexamples.
- `BUSINESS.txt`: initial 17 recovery tests passed; subsequently expanded cases
  are included in the final full run.
- `PROGRESS.txt`: 57 existing progress tests passed.
- `PUBLICATION.txt`: 22 existing publication-progress tests passed.
- `PUBLICATION-BUSINESS.txt`: Stage4 exact identity/candidate recovery and already
  published intake-only recovery passed (2 tests).
- `DISPATCH-RECOVERY.txt`: existing dispatcher replacement clears only its exact
  block, and replay cannot clear a later block (1 test).
- `PUBLICATION-QUIESCENCE.txt`: fresh stopped query required after a new running
  fact before publication intake, with no republish/continue (1 test).
- `RUNNER.txt`: full runner suite passed (34 tests, including 7 new transport tests).
- `RECOVERY-ALL.txt`: combined unified/business recovery passed (46 tests at that
  point); later unknown-action and ancillary compatibility checks passed separately.
- `ACTION-RECOVERY.txt`, `UNKNOWN-ACTION.txt`: unknown/unrelated action outcomes
  must be settled by the current exact query before continuation.
- `LEGACY.txt`: old v3 member rejected without writes for resume, requirement,
  lifecycle and recovery entrypoints.
- `PROVENANCE-CONFLICT.txt`: conflicting provenance stays rejected on exact retry.

Two preliminary full runs were deliberately interrupted while completing the
unresolved-action and sticky-conflict checks. Their logs are retained as
`VALIDATION-INTERRUPTED*.txt`; they are not success evidence.

Final full validation: `./scripts/validate.sh` exited 0. `VALIDATION.txt` records
593 tests in 1324.842 seconds: 592 passed and 1 existing inapplicable skip
(`wrapper transfer uses a Git stage-entry checkpoint`). Build consistency, all
five Skill validators and repository validation passed; repository `issues=[]`,
76 references, 5 Skills and 27 test modules. Final `git diff --check` passed.
The final full run includes every source/test change described above.

## Verification limits

A/B, Git, checkpoint and ledger operations use isolated real fixtures. Native
host provenance, actual Controller decisions and invocation settlement use an
explicit host boundary fixture. This is not field acceptance of a real native
host, cross-host execution, external Matt calls, remote publication or local
installation. Standalone, continuous and stepwise mechanical recovery rules are
shared; actual Stage2 continuous/stepwise and Stage4 continuation/publication are
covered. No synthetic Phase or independent Stage1-to-2 producer was added.

The coordinator must review this exact candidate independently. Passing tests
here is not approval to merge or proof of real-host end-to-end execution.
