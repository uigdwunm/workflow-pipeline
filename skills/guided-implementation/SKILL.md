---
name: guided-implementation
description: Use when the user explicitly invokes $guided-implementation (3实现), confirms entry from a valid upstream footer, resumes this Skill's exact worktree/claim/recovery footer, or the originating task receives a verified terminal control. Freeze committed implementation sources, protect their exact documents, provision one dedicated worktree through a durable CAS claim, independently review and accept one exact candidate, publish it through target-HEAD CAS, and route immutable outcome evidence to closure. Never implement in the originating task or use the primary checkout as implementation workspace.
---

# 3实现

Every implementation uses one dedicated Git worktree. There is no mode choice,
primary-checkout implementation path, expiring execution ownership, or mutation
of a running source. Read [references/worktree-execution.md](references/worktree-execution.md),
[references/originating-task-protocol.md](references/originating-task-protocol.md),
[references/execution-protocol.md](references/execution-protocol.md), and
[references/document-lease-protocol.md](references/document-lease-protocol.md)
before the first corresponding action.
Read [references/thread-settings-protocol.md](references/thread-settings-protocol.md)
immediately before resolving or verifying task model/reasoning settings
for a launch.

## Entry authority

- Accept only explicit invocation, an exact upstream stage confirmation, a
  verified continuous-flow footer, or recovery of one exact existing run.
- Resolve committed Spec, ADR and Ticket documents before provisioning. Create
  an `implementation-source` checkpoint containing every source path and blob.
- Mark each exact source document as protected while any dependent
  implementation is active. Other Skills must refuse to edit it.
- A new user requirement is either a successor document or a stop of every
  dependent implementation followed by a new checkpoint. It is never a pending
  edit to the active source.

## Lifecycle

Use the authoritative states:

```text
PREPARED -> QUEUED? -> PROVISIONING -> ACTIVE -> CANDIDATE -> ACCEPTED
         -> INTEGRATING -> INTEGRATED -> ARCHIVING -> ARCHIVED
```

`PAUSED` records a typed reason and the exact state to resume. Cancellation is
allowed only before integration and ends in `CANCELLED`. State and record
revision form one CAS; never infer or skip a transition.

## Provision and launch

1. Check path/module/interface/dependency overlap. A blocked serial run stays
   `QUEUED`; a safe run may enter `PROVISIONING`.
2. CAS-create a durable worktree claim before creating the branch or worktree.
   It binds repository, path, branch, base, implementation, Phase Run, scope
   digest and source checkpoint. It has no TTL or owner identity.
3. Provision through `provision-claimed-worktree`. Reconcile every uncertain
   result as exact adoption, safe retry, or typed ambiguity. Never fall back to
   another checkout.
4. Verify claim, platform working directory, Git common directory, branch and
   base before activation and substantive work.
5. Publish a version-5 immutable handoff containing the checkpoint and claim.
   Create one dedicated task and leave implementation work to it.

## Implement, review and accept

- Process Tickets in dependency order and document order among ready Tickets.
- Work only inside the claimed worktree. Routine edits, tests and commits use no
  repository-wide barrier.
- Keep source documents unchanged. Outcome notes are immutable external
  proposal evidence, not dirty worktree files.
- Create an implementation-only candidate, run focused and full checks, and
  complete standards and requirements review.
- `record-candidate-review` freezes candidate, source, claim, verification,
  review and paths. `accept-implementation-candidate` accepts exactly that
  record. Any candidate change invalidates both.

## Publish

Only the Stage-3 supervisor mutates the target branch. It acquires the short
publication slot, refuses a dirty or wrong checkout or Git operation in
progress, and compares `expected_target_head` immediately before merging. An
advanced target is allowed when that exact HEAD is supplied.

On conflict, abort and prove the primary checkout restored. Repair in the
original implementation worktree:

- textual/structural conflict: direct repair is allowed;
- local semantic adaptation: allowed only when scope, business behaviour and
  acceptance criteria remain unchanged;
- material semantic conflict, invalid assumptions or extensive redesign:
  enter `PAUSED` and ask the user.

Every repair integrates the latest target, reruns tests and review, creates a
new candidate, and obtains new acceptance.

## Route closure

After an exact `INTEGRATED` result, create immutable outcome proposals outside
the worktree. Stage 4 converges them under an exact-path document lease and
target-HEAD CAS, then performs checkpoint-owned cleanup:

```text
documents-committed -> worktree-removed -> branch-removed
-> execution-claim-released -> archived
```

The claim is the final resource released. Never force cleanup, accept
caller-authored cleanup success, or perform remote writes without separate
authority. Stable success footers report the claim and worktree, never a mode.

After verified integration, use this stable handoff footer:

```text
实现结果：成功
合并结果：成功
实现提交：<accepted candidate commit>
合并提交：<verified target merge commit>
执行工作树：<exact retained worktree path>
执行 claim：<claim ID/version/CAS receipt>
下一阶段：`$change-closure`（4归档）
```
