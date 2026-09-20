# Discussion project identity delivery

Status: ready-for-human
Lifecycle: completed


## Final accepted verification

Code candidate: 04015c26b71e5bcd4d5c967df4051f3f0300c7a4.
Independent coordinating Standards and Spec reviews closed the original discussion
path P1, runner receipt-consumer P1 and runner normalization P2, with no remaining
findings. The coordinator independently reran the real-A runner path regression.

Final scripts/validate.sh completed with exit 0: 625 tests in 1643.709s,
624 passed and 1 existing not-applicable skip. Build consistency and all five Skill
validators passed. Repository validation: valid, no issues, 76 references, 5 Skills,
31 test modules. Full output is retained in VALIDATION.log beside this document.
The cancelling/pausing/blocked JSON lines are expected fixture stdout; unittest
reported OK (skipped=1) and the script finished with Validation complete.

This final evidence update changes only .scratch delivery records and the log.
The src, tests, skills and build Git trees are checked against the exact code
candidate above. No push, merge, deployment or installation was performed.
Real native/cross-host/Matt/remote field acceptance remains unexecuted.

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

## Runner consumer correction

Independent review of 72d5c78 found the runner's strict attached receipt field
set still omitted discussion_project. Fixed validate_confirmed for both startup
and _validate_record restoring: retain the explicit field, verify its store against
the publication repository/common directory and disposable Flow boundary, and
compare the complete receipt to the original live A checkpoint result. Recovery
uses the surviving owner and does not require the removed Flow.

A new regression uses real A-produced evidence and generated package identities:
startup and restored records accept the valid receipt, including after removing
Flow; missing/changed identity and requirement-freeze-v1 fail on both entry paths.
The test passed in 12.335s. Only the source runner's package location is substituted
with the generated package identity; receipt/Git/ledger/record validation is real.

The initial full validation for 72d5c78 was explicitly interrupted (exit 130) to
repair this consumer. Its log remains at /tmp/workflow-discussion-identity-validation.log;
it is not passing evidence for either candidate. A complete new-tree run follows.
Coordination reported both independent review axes complete, with no other findings.

## Canonical runner input correction

Standards increment review found ownership was checked against raw worktree before
runner normalization. An owner/../owner spelling passed startup but normalized into
an invalid restoring record. Normalize repository/worktree/git_common_dir once
before attached validation, then reuse those exact values for checks and storage.
No envelope rewrite or string blacklist is involved.

The real-A runner regression now rejects canonical owner, owner/../owner and
owner/docs/.. as disposable Flow on both startup and restore. Valid noncanonical
repository/Flow/common-directory input normalizes to the same accepted restoring
record. All previous missing/drift/legacy/removed-Flow checks remain. The extended
test passed in 16.770s; generated packages and diff checks passed.

The 30899cf full run was explicitly interrupted (exit 130); its retained log is
/tmp/workflow-discussion-identity-validation-v2.log and does not establish a pass.
The corrected candidate requires a new complete validation run.
