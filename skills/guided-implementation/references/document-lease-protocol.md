# Document Lease Protocol

Use this protocol for every repository documentation write performed by stages
1 through 4 and for stage 3 Git operations that require a stable document
working tree. The document lease is cooperative and repository-scoped. It
serializes documentation activity without granting Git, code, publication, or
remote-write authority.

## Contents

1. Lease state and commands
2. Acquire with bounded waiting
3. Renew, verify, and release
4. Separate document and checkout authority
5. Preserve implementation-only commits
6. Handle shared documents and failures

## Lease state and commands

Use only:

```text
python3 <guided-implementation-skill-root>/scripts/supervision_protocol.py
```

The tool owns these files inside the ordinary checkout's real `.git`
directory:

```text
cc-switch-document-lease.json
cc-switch-document-lease.guard
```

Never create, edit, replace, chmod, truncate, or delete either file manually.
The state file remains after release, retains a monotonically increasing
`version`, and sets `holder` to `null`. Each acquisition uses one unpredictable
`lease_id`. Acquisition, renewal, and release are CAS transitions protected by
the stable guard file.

Use these commands:

```text
inspect-document-lease --repository <absolute repository>
acquire-document-lease --input <absolute ordinary JSON>
verify-document-lease --file <absolute lease file> --id <lease id> --version <version>
renew-document-lease --file <absolute lease file> --id <lease id> --version <version> --ttl-seconds <seconds>
release-document-lease --file <absolute lease file> --id <lease id> --version <version>
```

The acquisition input must contain exactly:

```json
{
  "owner_host_id": "<host id>",
  "owner_task_id": "<task id>",
  "purpose": "document-write | git-stability-barrier",
  "repository": "<absolute repository>",
  "stage": "problem-framing | solution-design | guided-implementation | change-closure",
  "ttl_seconds": 300
}
```

Write the ordinary input outside every repository and fixed runtime root.
Delete only that ordinary input after the command returns a verified result.

## Acquire with bounded waiting

Use the command defaults. It attempts acquisition immediately. When another
unexpired holder owns the lease, it waits 10 seconds, rereads actual state, and
tries the CAS again. It performs at most 10 wait-and-retry cycles: 11 total
attempts and approximately 100 seconds of waiting.

Every retry must use the current version, holder, and expiry returned by the
tool. A holder may renew while another task waits. Never infer availability
from an earlier expiry or replace an unexpired holder.

When an attempt observes an expired holder, the tool may atomically replace it
and reports `replaced_expired_lease_id`. The former holder cannot renew,
release, or continue writing after losing the lease.

Proceed only when the result contains `acquired: true`, `state: held`, the
current `lease_id`, `version`, path, owner, purpose, and expiry. If the result
contains `state: timeout`, stop the dependent operation and notify the user:

```text
文档锁等待：超时
目标文档区：<absolute repository documentation area>
锁文件：<absolute lease path>
当前版本：<version>
当前持有者：task=<task id>; host=<host id>; stage=<stage>; purpose=<purpose>
当前租约：<lease id>
预计释放时间：<expires_at_epoch>
尝试次数：11
累计等待：约 100 秒
当前阶段：<1拷问 | 2方案 | 3实现 | 4归档>
下一步：停止当前依赖操作；锁释放后可重试
```

Do not convert timeout into workspace cleanup, repository-lease takeover, or a
different document target.

## Renew, verify, and release

Verify the exact lease immediately before the first write and after every
renewal. For a write that could outlive the expiry, renew before the remaining
time becomes insufficient. Renewal increments `version`; use the returned
version for every later verify, renew, or release.

Release immediately after the bounded document edit or Git stability barrier.
Never hold the lease while waiting for user input, subagent review, an external
tool, repository-lease availability, or unrelated work. Release requires the
exact current `lease_id` and `version`, increments the version, clears the
holder, and preserves the state file.

A failed verify, renewal, or release means the caller no longer has write
authority. Stop without another document write. A stale holder must never clear
or overwrite a newer holder.

## Separate document and checkout authority

The repository lease mode `exclusive-checkout-v2` exclusively owns Git state,
implementation paths, branch switching, staging, commits, merge, stash, reset,
clean, and branch removal in the ordinary checkout. It does not itself grant or
deny a documentation write.

The document lease exclusively owns repository documentation writes. It does
not authorize staging, committing, branch changes, code writes, remote
publication, or cleanup.

Apply these stage rules:

- Stage 1 and stage 2 may write exact documentation paths while another task
  holds the repository lease. They must not stage or commit until the
  repository lease is available and their native publication gate permits it.
- Stage 3 may acquire `purpose: document-write`, create or update exact
  documentation paths, record their before/after hashes, and leave them
  unstaged for stage 4. It never stages or commits documentation.
- Stage 3 must acquire `purpose: git-stability-barrier` around candidate commits,
  branch switching, final merge preparation, merge commits, and final Git-state
  verification. It performs no document write under a barrier lease.
- Stage 4 acquires the repository lease first and the document lease second,
  updates only closure-owned documentation, commits exact document pathspecs,
  releases the document lease, and then continues cleanup. Stages 1 and 2 must
  never hold the document lease while waiting for a repository lease, so this
  order cannot form a lock cycle.

## Preserve implementation-only commits

Classify repository paths by effect and project convention. Implementation
artifacts include source, tests, migrations, schemas, fixtures, build and
runtime configuration, and dependency lockfiles. Documentation includes
README files, `docs/**`, `CONTEXT.md`, ADRs, Specs, Tickets, requirement drafts,
completion records, and project-specific equivalents.

Stage 3 must use exact implementation pathspecs. It must never use an unbounded
`git add .` or `git add -A`. Immediately before every implementation or merge
commit, inspect the NUL-delimited staged path list and require zero
documentation path. Also require the implementation commit range to contain no
documentation path.

Unstaged documentation changes do not make stage 3 dirty. Stage 3 status is
acceptable only when:

- the index contains only the exact current implementation or merge paths;
- implementation paths contain no unintended uncommitted change;
- every remaining working-tree entry is a recognized documentation path or a
  verified managed Skill link; and
- branch switching or merge will not overwrite a documentation path.

If Git reports that an unstaged document would be overwritten, or the
implementation branch changes a documentation path, stop. Never stash, clean,
reset, discard, or silently include the document.

For each stage-3 document write, record:

```text
文档路径：<path>
写入前 SHA-256：<sha256 | none>
写入后 SHA-256：<sha256>
文档锁 ID：<lease id>
文档锁版本：<version used for the write>
修改目的：<reason>
提交状态：未暂存；等待4归档
```

Carry the exact list and final hashes into the candidate report, merge result,
and stage-3 success footer.

## Handle shared documents and failures

The lease prevents simultaneous writes; it does not assign parts of one file
to different requirements. Before staging a shared document such as
`CONTEXT.md`, a README, ADR index, or common documentation index, require its
entire unstaged diff to belong to the current stage. If ownership cannot be
proven, do not stage it. Record the intended change in a requirement-specific
document or stop with one exact reconciliation question.

Stage 4 must stage only exact closure-owned paths. It must not stage a document
merely because it is under a documentation directory. Unrelated pending
documents remain unstaged for their owning stages.

Lease timeout, ownership ambiguity, expired authority, a document path in an
implementation commit, or a branch operation that would overwrite documents is
a protocol blocker. Preserve code commits, document bytes, lease facts, and the
current checkpoint; never repair the condition with broad staging or cleanup.
