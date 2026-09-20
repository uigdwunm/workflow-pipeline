# Stop must retain unsettled predecessor actions

Base: `d7d01b75688d6ad501cb547b8c8334fb8011193b`.
Branch: `codex/publication-recovery-unified-fix`.
The coordinator relayed explicit user authorization to fix the independently
reproduced P1 on this existing branch/worktree.

## Root cause and repair

`host_action` represented both the current call and the sole unsettled invocation.
Issuing stop replaced an unresolved continue/invoke. A stopped receipt settled
only the new stop, but the old invocation was no longer available to query, so a
subsequent idle query incorrectly permitted continuation without its outcome.

In the same atomic checkpoint save as each new invoke/continue/stop issuance, C
now copies any unresolved previous action into ordered `retained_host_actions`.
The current stop can execute promptly; its response never settles another call.
Original IDs, payloads and generations remain until their exact terminal outcome
is authenticated by the existing current-query contract.

Queries select the oldest unresolved predecessor and then the current action,
using unchanged singular `payload.unresolved_action` / `action_resolution`.
One response settles one action. More pending actions produce another read-only
query; unknown/wrong/nonterminal evidence waits. The continuation gate checks the
entire set before current proof can issue one new continuation. Stop/cancel cannot
erase that set. New publication/intake and next stage entry also require it to be
settled; querying an accepted stage retains its business completion. B's proven
non-creation retry remains unchanged.

Resolved predecessor entries remain as audit evidence. This is bookkeeping in
the existing checkpoint, not another store, lock or scheduler. It fulfills the
existing v4 requirement without adding host permissions or changing A/B/control/
supervision. The protocol key stays v4; generated bundle identities change. Old
pinned runs are not migrated; missing identities in already-overwritten older
records are not invented.

## Evidence

`STOP-PENDING-RED.txt` runs the original counterexample against workflow_progress
loaded from immutable commit d7d01b7 using `git show`, with real isolated A/B/Git
fixtures. The assertion fails because the old code emits `continue-host` after
stop followed by idle without original-action resolution. No raw nonce or
synthetic stop-cancels-all assumption is introduced.

`tests/runtime/test_pending_host_actions.py` covers:

- Original continue stays pending after pause and a new stop's stopped response.
- Exact completed/not-issued/cancelled outcomes plus current host evidence permit
  one continuation; repeats ACK or return the saved action/query.
- Wrong/old IDs, the stop's ID, unknown/running outcomes and late query causes
  cannot settle the original; attaching its resolution to a stop response fails.
- Multiple unresolved stops are reconciled individually in retained order.
- Before/after stop issuance and resolution saves preserve identity and prevent
  duplicate continuation.
- Repeated pause, pause while querying and cancel priority preserve older
  responsibilities; cancelled state cannot resume.
- Both bound invoke and uncertain creation reconciled to the actual original
  identity remain accountable when a later stop replaces them.

Initial focused matrix: 12 passed (`STOP-PENDING-GREEN.txt`). The final matrix has
15 tests; its combined run with the original unified/business recovery suites
passed all 62 tests in 229.530 seconds (`STOP-PENDING-RECOVERY.txt`).

The first full run completed 608 tests with one failure: the new pending-action
check preceded the existing candidate-readiness check, changing the expected
`candidate_not_accepted` error. The original assertion was retained. Moving the
new check after existing readiness validation restored it without weakening the
action barrier. `STOP-PENDING-VALIDATION-FAILED.txt` preserves that full run;
`STOP-PENDING-PUBLICATION-REGRESSION.txt` and
`STOP-PENDING-PUBLICATION-GREEN.txt` preserve the targeted red/green checks.

Final `./scripts/validate.sh` exited 0: 608 tests in 1361.626 seconds,
607 passed and 1 existing inapplicable skip. Build consistency and all five Skill
validators passed; repository validation reports `issues=[]`, 76 references and
28 test modules. Evidence: `STOP-PENDING-VALIDATION.txt`. Final diff check passed.

## Limits and handoff

Native invocation provenance and terminal outcomes remain explicit host fixtures;
real A/B, Git and local checkpoint/ledger mechanisms are exercised. No real native
host, remote, Matt or deployment field acceptance is claimed. The original three
v4 fixes, raw-content distinction, business recovery, candidate separation and
publication/deletion protections remain in the complete suite.

No new task/worktree, merge, push, deployment or installation is performed.
The exact resulting commit returns to the coordinator for independent review;
merge still requires separate user authorization.
