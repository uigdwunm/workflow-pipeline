# Persistent discussion identity across Flow execution

Status: ready-for-human
Lifecycle: completed

The user approved implementation in this task after reviewing the proposed plan.
Baseline: 7d7f4e6c22470c56003691441c43354527ae8461.
Development checkout: the existing 67e2 worktree, branch codex/discussion-project-identity.

## Contract

Separate actual execution cwd/Git facts from the durable discussion owner. Locate
its existing ledger using the original attachment and execution coordination store;
use ledger project_manifest_path as a locator, then retain the original manifest,
absolute topic-document and actor checks. Persist the verified identity through
A requirement evidence, B handoff, C lifecycle and D post-cleanup acceptance.

The discussion owner must survive, including when it is a linked checkout. It may
not be inside the disposable Flow. Missing/relocated owners fail closed. No ledger
copy, document migration, actor expansion, old-envelope rewrite or old-pin migration.

Preparation v2, stage-transfer v2, workflow-progress v5 isolate the new evidence
contract. Runner outer v3, discussion schemas, control and supervision v2 remain.

## Acceptance

- Preserve the baseline red Flow-entry and post-removal readback assertions.
- Real isolated Git/ledger attached Stage2→3→4 through legal source/carrier Phase
  operations, B receive/accept, publication and Flow removal.
- Replay exact lifecycle envelopes; interrupt final B intake after removal and
  recover without publishing or creating a Flow again.
- Reject identity/envelope drift; support a linked discussion owner and retain
  standalone/non-Git behavior through the existing suite.
- Build generated packages from src; run scripts/validate.sh and diff checks.
- Commit candidate and return it to the coordinating task for independent review.

No push, merge, deployment, installation, other-task cleanup or Stage1→2 producer
implementation is authorized by this plan. Host receipts in integration tests are
fixtures; they do not prove real native/cross-host/Matt/remote field acceptance.
