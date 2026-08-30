# Dependency contract

The optional discussion stage and the four established stages invoke other Skills by their registered names. Installation location is intentionally not fixed; the active Codex Skill registry is authoritative at runtime.

## Internal dependencies

| Stage | Internal Skills |
| --- | --- |
| `design-discussion` | `guided-implementation` |
| `problem-framing` | `design-discussion`, `solution-design`, `guided-implementation` |
| `solution-design` | `design-discussion`, `problem-framing`, `guided-implementation` |
| `guided-implementation` | `design-discussion`, `problem-framing`, `solution-design`, `change-closure` |
| `change-closure` | `design-discussion`, `guided-implementation` |

Install all five Skills together so optional discovery and cross-stage transitions remain available.

`guided-implementation` also owns the shared `thread-settings-v2` runtime and
protocol used by `design-discussion`, `problem-framing`, `solution-design` and
`guided-implementation`. Install or update all five Skills from the same
workflow-pipeline version. A missing resolver or a protocol version other than
`thread-settings-v2` is a fail-closed `workflow_runtime_version_mismatch`; a
caller must stop at its stable recovery point instead of guessing settings or
using a different installed copy.

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

These dependencies are currently a documented runtime contract rather than vendored or version-resolved packages. The repository cannot mechanically prove the installed version; the commit and release above are a manually reviewed compatibility baseline. When the upstream behavior changes:

1. update the compatibility baseline in this file and the root README;
2. run the validation suite;
3. forward-test each stage transition against realistic repositories;
4. document any required minimum upstream version in the next release.

Do not install duplicate copies of the same dependency under different Skill providers. A unique registered name is required for deterministic routing.

## Validation contract

`./scripts/validate.sh` discovers every `skills/**/scripts/test_*.py` test and mechanically verifies registered internal/external Skill names, reachable progressive references and the absence of macOS, Linux and Windows user-specific absolute paths under `skills/`. Optional 0讨论 routes and recovery behavior are exercised without granting any new external-write authority.

`./scripts/check-dependencies.sh` is a bounded filesystem approximation, not a registry query. It treats this checkout's five internal Skill directories as source under validation, then searches `CODEX_HOME/skills` when configured plus the conventional `~/.codex/skills`, `~/.agents/skills` and `~/.cc-switch/skills` roots. Canonically identical roots are deduplicated. More than one external file for the same required registered name is an error; one installed copy of an internal Skill may coexist with this source checkout. The script cannot see plugin/provider registrations that are not represented in those roots and cannot verify version metadata. Inspect the active Codex registry and compare the documented compatibility baseline when installation identity or version matters.

The thread-settings protocol performs its own exact runtime-version check at
the point of use. The filesystem approximation does not replace that check and
does not authorize mixing internal Skills from different releases.
