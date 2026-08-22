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
without increasing revision or event count. Reusing a key with different
parameters is a conflict. A project that already has a different root must not
be guessed or replaced.

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
