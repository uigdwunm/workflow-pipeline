# Accepted-stage negative query retry

Base: `83a13a5a782322278d5bca1612383363b14f360e`.
Branch: `codex/publication-recovery-unified-fix`.
The coordinator relayed user authorization for this bounded P2 follow-up.

## Cause and change

Accepted-stage resume returned query_host before the lower normal/publication
branches cleared an ended negative query. It repeatedly returned the same
await-host-recovery query, which could no longer grant current proof.

The refresh now happens once in resume after stop admission and before the
business replay, accepted-stage and publication early returns. It archives the
ended query in host.query_history and clears only the current query slot. New
query issuance still uses the same original unsettled action. Accepted B evidence,
Controller decisions and action identity remain unchanged. In-flight queries
retain their ID; terminal accepted stages with no unsettled action ACK without
new work. Ordinary advance, duplicate input and runner stop requests cannot use
the explicit-resume refresh path. Business blocks remain separately authoritative.

Protocol and public inputs remain v4. query_history is internal audit data, not
proof or a new state store. A/B/control/supervision/runner permissions and source
are unchanged. Five packages are generated from source; old pinned runs are not
migrated. No merge, push, deployment, installation or new worktree is involved.

## Evidence

ACCEPTED-QUERY-RED.txt records two failures on the exact base: Stage2 publication
acceptance with an older unresolved stop, and Stage3 deferred completed intake
with an unresolved invoke. Unknown/missing terminal evidence leaves an ended
query; explicit resume incorrectly returns await-host-recovery instead of a new
inspect-host-state action.

The unchanged two assertions passed after the repair (ACCEPTED-QUERY-GREEN.txt).
ACCEPTED-QUERY-MATRIX.txt records eight passing tests, including Stage2 stepwise
and Stage3 progression into the next stage; unknown/missing/wrong terminal data;
consecutive fresh query IDs for the same action; advance without polling; stale
query response rejection; B evidence and original decisions retained with no B
replay; successful input/acceptance ACKs; before/after query archive save faults;
and actual runner pause/cancel request admission.

Combined recovery validation and final full validation: pending.

## Limits

Tests use real isolated A/B/Git/checkpoint/ledger behavior and explicit native
host fixtures. This is not field acceptance of actual native/cross-host execution,
Matt, remote publication or deployment. The exact commit returns to the
coordinator for independent review; merge still requires separate authorization.
