# Issue tracker: Local Markdown

Issues and planning artifacts for this repository live as Markdown files under `.scratch/`.

## Conventions

- Keep one feature per directory: `.scratch/<feature-slug>/`.
- Store the feature PRD or Spec at `.scratch/<feature-slug>/PRD.md`.
- Store implementation issues at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`.
- Record triage state as a `Status:` line near the top of each issue file. Use the role strings in `triage-labels.md`.
- Record work closure independently as `Lifecycle: completed`; omit it or use another lifecycle value while work remains open.
- Append comments and conversation history under a `## Comments` heading at the bottom of the relevant file.

For the design-discussion work, use `.scratch/design-discussion/`.

## Publishing and reading

When a Skill says to publish to the issue tracker, create the appropriate file under `.scratch/<feature-slug>/`, creating the directory when needed.

When a Skill says to fetch a Spec or Ticket, read the referenced Markdown file. Callers should pass its path or issue number explicitly.

Before automatically claiming a `ready-for-agent` issue, run
`python3 scripts/validate_repository.py --repository . --list-frontier` and
select only a returned path. The listing mechanically excludes every issue
whose lifecycle is `completed`; status and lifecycle are orthogonal.

## Wayfinding operations

For `$wayfinder`, use one map and one child file per ticket:

- Map: `.scratch/<effort>/map.md`.
- Child ticket: `.scratch/<effort>/issues/<NN>-<slug>.md`, numbered from `01`.
- Record the ticket type in a `Type:` line and its state in a `Status:` line.
- Record dependencies as `Blocked by: NN, NN`.
- Treat an open, unblocked, unclaimed ticket whose lifecycle is not `completed` as frontier work; choose the lowest number first.
- Claim by setting `Status: claimed` before starting work.
- Resolve by appending the answer under `## Answer`, setting `Status: resolved`, and adding a concise context pointer to the map's decisions-so-far section.
