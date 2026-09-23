# Requirement preparation and freezing

Use `scripts/requirement_prepare.py` with one bounded JSON object. It returns the
same success/error envelope as [entry preparation](entry-preparation.md).
Every request requires protocol=`requirement-freeze-v2`, operation and entry (the
complete entry request). Reuse the original pin. The adapter refreshes local/host
facts and reuses the existing publication lock for standalone mutations.

## Ownership and confirmation

The Agent writes the semantic content and obtains existing confirmations. The
script cannot certify that prose is complete or that a user agreed. Authorization
references must come from the controller's existing confirmed checkpoint; a string
is not a new permission mechanism. Stage 1 retains its completion/commit confirmation;
explicit standalone Stage 2 retains its existing bounded snapshot preparation
authorization. No additional stage, task or remote authority is granted.

Resolve one path using the repository convention. With no convention use
`docs/problem-framing/<date>-<target>.md` for Stage 1 or
`docs/requirements/<date>-<target>.md` for standalone Stage 2. Date and target are
the confirmed workflow naming inputs, not a search for an arbitrary existing file.
The adapter validates and records that exact path and never chooses another on retry.
All path components must be nonsymlinks; files must be regular Markdown documents.

## Standalone document sequence

1. Call prepare with purpose=write, path, version, authorization and content.
   This is read-only. Save its complete returned intent in the existing checkpoint
   and verify that persistence before mutation.
2. Call write with intent=<saved intent>. The script creates the document without
   overwrite, or replaces only its verified previous bytes, and returns a document
   receipt. Save the whole receipt; never manually reconstruct its digest.
3. For an update, prepare another write on the same path with previous=<document
   receipt>. Changed content requires a newer version. A same-intent retry uses
   reconcile rather than preparing a new document.
4. After semantic readiness and applicable confirmation, call prepare with
   purpose=freeze, the same path/version, authorization and previous=<latest
   document receipt>. Optional owned_paths is a sorted unique list of at most 32
   exact stage-owned Markdown paths including the requirement. Default is just
   the requirement. The confirmation must authorize every supplied path.
5. Save the returned freeze intent, then call freeze with that intent. The script
   stages exact literal paths, commits only those paths and reads the Git objects
   back. It preserves unrelated staged/unstaged/untracked files. A partial staged
   version of an owned file is a conflict, not permission to overwrite it.

For a document already owned by an existing conversation checkpoint, previous may
instead be its trusted projection with exactly path, sha256, version, write_owner,
repository and receipt. Entry.source.path must match; write_owner must be the
current authenticated task and repository the verified checkout. These fields
come from the original controller checkpoint, not from untrusted document content.
This consumes existing ownership, not a task-transfer or old-run migration API.
Carrier transfer/binding remains with the existing B-side protocols.

Example request shapes (replace the named values with complete JSON objects):

```text
{protocol:"requirement-freeze-v2",operation:"prepare",entry:ENTRY,
 purpose:"write",path:"docs/requirements/target.md",version:1,
 authorization:"confirmed decision reference",content:"confirmed requirements"}
{protocol:"requirement-freeze-v2",operation:"write",entry:ENTRY,intent:INTENT}
{protocol:"requirement-freeze-v2",operation:"prepare",entry:ENTRY,
 purpose:"freeze",path:"docs/requirements/target.md",version:1,
 authorization:"existing commit authorization",previous:DOCUMENT_RECEIPT}
{protocol:"requirement-freeze-v2",operation:"freeze",entry:ENTRY,intent:FREEZE_INTENT}
```

## Failure and retry

Retain the original intent and all successful writes. Reconcile accepts that same
intent. A matching write is reused; a conflicting write stops. A matching freeze
commit is reused; an unperformed freeze returns prepared. Changed HEAD, ownership,
document bytes or unrelated workspace evidence stops without reset/stash/cleanup.
write, freeze and reconcile require any current explicit source.path to match
the saved intent's path before file/index/commit mutation. An initial entry may
omit source.path; specifying that same document later remains valid. Omitting a
path never selects a different document: the retained intent remains authoritative.
The script does not repeat semantic questioning or move Stage 2 back to Stage 1.
If a matching commit exists off HEAD, reconciliation reports commit_detached and
preserves both objects and workspace. The controller must restore the original
lineage; the adapter never resets refs or creates a replacement commit. Multiple
matching commits are ambiguous and stop.

Hooks and filters remain enabled. A failed hook may leave the exact owned files
staged; retry reuses them. Filters changing the frozen bytes fail verification.
A successful commit with subsequent file drift is retained but not handed off.
When a commit is fully verified against the intent but subsequent checks fail,
error.completed_evidence contains one record with kind=verified-requirement-commit,
operation_id, intent_digest, commit, baseline, path, blob, sha256 and
downstream_ready=false. This bounded record proves the Git object, not a matching
workspace, final freeze success or launch permission. It has no requirement_identity
or successful receipt digest. The response remains ok=false with no result.
The same intent's reconcile returns this evidence again while drift persists;
after the source owner reconciles the workspace it can return the existing commit
as a successful frozen receipt without another commit. Before a verified commit
exists, completed_evidence is empty; that does not assert that no staging or other
partial effect occurred. Never infer success from an empty or partial error record.
Do not run concurrent external Git writers: the existing publication lock serializes
participating adapters, not arbitrary user commands or hooks. On unexpected changes
preserve the scene and use the controller's existing anomaly recovery.

Freeze verifies its exact parent, actual changed paths, all owned blobs, document
SHA-256 and current bytes. A requirement already matching the verified baseline is
reused without an empty commit. The frozen receipt contains protocol, kind=frozen,
source_kind, entry, path, absolute_path, version, sha256, commit, blob, baseline,
changed_paths, owned_paths, requirement_identity, operation_id and digest. requirement_identity
retains exactly `{path,sha256,version}`; commit remains separate for current consumers.

## Existing frozen input

Stage-1 freeze proves the source, not target delivery. Before a completed footer
or dedicated result, use [target delivery](requirement-delivery.md).
Its transaction retains this original receipt and the owned path scope.

Use source.kind=frozen and source.path, operation=verify and evidence=<original
frozen receipt>. A trusted existing handoff may instead supply exactly path,
commit, sha256, version, owner_ref and receipt. The adapter checks regular Git
blob mode, actual committed/worktree bytes and ancestry of the current target,
and returns a complete verified receipt without writing or committing. The host
authenticates the source owner; local digest checks alone do not authenticate it.

Stage 2 passes the source path as protected_paths, never child allowed_paths.
Do not supplement frozen bytes from chat. Corrections remain with the source owner
and invalidate downstream planning under the existing correction protocol.

## Attached discussion

DW/CP requests, document reads and absolute result paths use A's pinned
`discussion_project`, not the execution root. Attached receipts include the same
project identity. Reconciliation verifies that identity and retains the original
envelope and keys; it never normalizes an old request to a different project.

Use source.kind=discussion and the existing attachment. Do not supply a standalone
path, content, previous or owned_paths, and do not add a second requirement document.

- prepare with purpose=write, authorization and the existing semantic mutation
  produces a read-only intent. write executes `update-topic` with the original
  intent; reconcile retries that same request and pending DW.
- prepare with purpose=freeze, authorization and base_ref produces a read-only
  intent. freeze executes the existing stage-entry CP prepare, reconciliation and
  publication operations. Git bases are resolved to an exact commit when preparing.
- reconcile resumes the same intent through the original update/CP operations. Prepared
  CPs are reconciled before publication because an earlier process may have created
  an object without recording success. No second ledger or checkpoint authority exists.
- verify with checkpoint_id reads the exact completed stage-entry checkpoint and
  validates its current source. Stages 2–4 cannot mutate attached requirements.

The discussion result carries the original checkpoint, attachment, absolute_path,
requirement_identity, commit and blob. Non-Git discussion has commit/blob=null and
retains its original snapshot authority; it is not a Git planning input.
CP publication preserves ordinary HEAD/index/branch refs and uses only the existing
checkpoint ref. No planning publication is performed here. Phase activation,
pending impacts, gates and handoff readiness remain mandatory in their owning
protocol; a source receipt is not a completed phase transition.
If a dedicated carrier lacks the original protocol's outcome-unknown transition
permission, report the existing controller recovery anomaly. The controller
reconciles that exact CP through the original protocol, then the carrier retries
its retained intent and receives the completed result. Do not impersonate the
controller or widen the carrier operation allowlist.
