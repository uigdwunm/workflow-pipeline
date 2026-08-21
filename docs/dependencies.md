# Dependency contract

The optional discussion stage and the four established stages invoke other Skills by their registered names. Installation location is intentionally not fixed; the active Codex Skill registry is authoritative.

## Internal dependencies

| Stage | Internal Skills |
| --- | --- |
| `design-discussion` | `guided-implementation` |
| `problem-framing` | `design-discussion`, `solution-design`, `guided-implementation` |
| `solution-design` | `design-discussion`, `problem-framing`, `guided-implementation` |
| `guided-implementation` | `design-discussion`, `problem-framing`, `solution-design`, `change-closure` |
| `change-closure` | `design-discussion`, `guided-implementation` |

Install all five Skills together so optional discovery, cross-stage transitions and shared lease verification remain available.

## Matt Pocock runtime dependencies

| Skill | Used by | Purpose |
| --- | --- | --- |
| `setup-matt-pocock-skills` | repository setup | Configure tracker, labels and domain docs before first use |
| `ask-matt` | stages 1, 2 and 4 | Route work to an appropriate engineering workflow |
| `grill-with-docs` | stage 1 | Question requirements while maintaining domain documents |
| `grilling` | through `grill-with-docs` | One-question-at-a-time interview primitive |
| `domain-modeling` | through `grill-with-docs` | Maintain `CONTEXT.md` and ADRs |
| `to-spec` | stage 2 | Produce the implementation Spec |
| `to-tickets` | stage 2 | Produce dependency-aware Tickets when useful |
| `implement` | stage 3 | Execute the accepted implementation work |
| `tdd` | stage 3 | Apply a red-green-refactor implementation loop |
| `code-review` | stages 3 and 4 | Independently review the implementation |

Install them from https://github.com/mattpocock/skills. The baseline used for this release is commit `84fdeffd12f2ee307994d1eb6feb48173b6e0502` (`1.2.3`, MIT).

## Compatibility policy

These dependencies are currently a documented runtime contract rather than vendored or version-resolved packages. When the upstream behavior changes:

1. update the compatibility baseline in this file and the root README;
2. run the validation suite;
3. forward-test each stage transition against realistic repositories;
4. document any required minimum upstream version in the next release.

Do not install duplicate copies of the same dependency under different Skill providers. A unique registered name is required for deterministic routing.

## Validation contract

`./scripts/validate.sh` discovers every `skills/**/scripts/test_*.py` test and mechanically verifies registered internal/external Skill names, reachable progressive references and the absence of user-specific absolute paths under `skills/`. Optional 0讨论 routes and recovery behavior are exercised without granting any new external-write authority.
