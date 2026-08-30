# Worktree Closure Checkpoint Protocol

The checkpoint is private immutable-CAS state under the fixed worktree-closure
runtime root. It has one protocol and one phase sequence:

```text
documents-committed -> worktree-removed -> branch-removed
-> execution-claim-released -> archived
```

Each side-effecting advance first persists `pending_phase`. If the process
stops after the effect and before phase persistence, the next invocation may
adopt only the uniquely proven expected absence or released claim. Absence
before intent, a changed path/ref/commit/claim, dirty worktree, unproven branch
ancestry or any ambiguous observation stops without force.

The final resource phase first verifies that no other implementation protects
the source, releases this implementation's source protection, and then releases
the exact claim by ID/version/bytes/digest. A retry may adopt exactly the next
released claim revision with terminal state `archived`.

An archived checkpoint is idempotent. Old checkpoint shapes are unsupported
and are never migrated or normalized.
