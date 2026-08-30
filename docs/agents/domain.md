# Domain Docs

This repository uses a single-context domain-document layout.

## Read before exploring

- Read `CONTEXT.md` at the repository root when it exists.
- Read relevant architectural decisions under `docs/adr/` when they exist.

If either location does not exist, proceed without flagging its absence. Create or update domain documentation lazily through the appropriate domain-modeling workflow when terminology or architectural decisions are actually resolved.

## Expected layout

```text
/
├── CONTEXT.md
├── docs/adr/
└── ...
```

## Vocabulary

Use terms as defined in `CONTEXT.md` in Specs, Tickets, implementation plans, tests, and review findings. Avoid synonyms that the glossary explicitly rejects.

If a required concept is missing, reconsider whether the project already uses another term. When the omission represents a real domain-language gap, route it to `$domain-modeling`.

## Architectural decisions

Surface any conflict with an existing ADR explicitly. Do not silently override an accepted architectural decision.
