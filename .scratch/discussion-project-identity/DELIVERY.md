# Discussion project identity delivery

Status: ready-for-human
Lifecycle: verification-in-progress

## Candidate behavior

A retains actual execution cwd/Git evidence and separately resolves the discussion
owner from the exact existing ledger's project_manifest_path. Original read-topic
checks still authenticate the manifest/document/conversation relationship. B and C
consume the retained identity, including after D removes Flow. Control/lifecycle
envelopes cannot switch projects; pending envelopes and idempotency keys are kept.
No discussion_core, supervision cleanup algorithm, ledger schema or actor policy
was modified. Source ownership may be a surviving linked checkout.

## Evidence before code freeze

- Baseline 7d7f4e6: the two new Flow entry/readback tests failed with state_corrupt.
  A separate interface reproduction after temporary Flow removal failed with
  invalid_project_path; original topic revision remained 1 and ledger bytes matched.
- Final focused identity suite: 7 tests passed in 5.656s.
- Final attached Stage2→3→4 integration: 1 test passed in 97.261s. Real temporary Git,
  original ledger, A checkpoint, Source/Carrier authorization, B handoffs/control,
  C publication, Flow removal, interrupted B intake and exact lifecycle replay.
  Stepwise entry confirmations are supplied through C's ordinary decision API.
- Native host observations/settings/package registration are fixture boundaries;
  Phase/Git/A/B/C are production implementations, not mocked success responses.
- An isolated owner-directory relocation experiment failed with invalid_project
  while Flow survived; restoring the same owner restored readback.
- Build output regenerated; scripts/validate.sh passed build consistency and all
  five Skill validators and is still running. Full-suite completion is NOT claimed.

## Review boundary

Preparation v2, stage-transfer v2 and workflow-progress v5 reject mixed old/new
exchanges. Runner outer v3, discussion/control schemas and supervision v2 remain.
Old pinned runs use their original packages. The original owner must survive;
there is no fallback to Flow documents, owner migration or old-envelope rewriting.

No push, merge, install or deployment. No real native/cross-host/Matt/remote field
acceptance is claimed. Full validation result will be added in a separate evidence
commit; source, tests and generated package trees must remain identical to the
reviewed code candidate unless explicitly reported otherwise.
