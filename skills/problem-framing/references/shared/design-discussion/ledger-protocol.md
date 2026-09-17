# Ledger and Bootstrap Protocol

Read this reference when initializing persistent mode, validating reread
evidence, or handling protocol errors.

## Storage

Each discussion tree has one authoritative `ledger.md`:

- Git projects store it below the repository's Git common directory at
  `cc-switch/design-discussion/v1/`.
- Non-Git projects store it below the project at
  `.codex/design-discussion/v1/`.

The protocol asks Git whether the project is a worktree before choosing a
root. Only Git's explicit “not a repository” result permits non-Git storage.
Missing or incompatible Git, damaged metadata, permission or ownership
failures, and an invalid common directory fail closed; they must never create
a second project-local coordination authority.

Project identity is located by `docs/discussions/.codex-project.md`. The root
topic document remains in `docs/discussions/<root-slug>/topic.md` for both
storage modes.

## Bootstrap contract

Bootstrap creates one project identity, root tree, root topic, active
conversation binding, ledger event and minimum topic document as a single
all-or-stop operation. The caller may say that 0讨论 started only after the CLI
rereads every artifact, verifies identical committed bytes, and verifies the
same project, tree and topic identities across all three authorities.

An exact retry with the same UUIDv4 and parameters returns the existing state
at its current ledger and topic revisions without increasing revision or event
count. It does not restore the original binding or lifecycle state. Reusing a
key with different parameters is a conflict. A project that already has a
different root must not be guessed or replaced.

Ledger schema v2 stores the permanent creation receipt in frontmatter as
`creation_idempotency_key` and `creation_fingerprint`. The receipt belongs to
the ledger itself: it is not topic state, and it remains authoritative after
the bounded `Recent Events` window drops the original
`root-topic-bootstrapped` event. `bootstrap` and
`initialize-document-context` use this same receipt rule.

For an existing schema-v1 ledger, the protocol may derive the receipt in memory
only from exactly one valid `root-topic-bootstrapped` event that identifies a
current topic. An exact creation replay remains read-only. The next ordinary
ledger mutation writes schema v2 as part of that mutation, without adding a
separate revision or event. A v1 ledger whose creation event is no longer
present remains readable and mutable as v1, but it cannot prove a creation
replay.

Bootstrap serializes on one stable per-project lock file. That coordination
file may remain after a failed bootstrap and is not business state. It is never
unlinked during rollback because existing waiters may already hold its inode.
Lock acquisition uses the same bounded wait as ledger mutations and reports
the retryable `coordination_busy` code on timeout. Rollback still removes every
new ledger, manifest, topic document and other partial business artifact.

## Errors

Use the structured error code and `retryable` field, never parse the Chinese or
English message. Invalid paths, identities, schema, idempotency and existing
state require the caller to stop. An initialization failure rolls back only
files and directories created by that invocation and preserves pre-existing
paths.

Do not edit `ledger.md` directly. Later topic, question, result, relation,
impact, checkpoint, document-write and phase-run changes require their own
typed protocol operations.
