# Integrate 1拷问 and 2方案 with discussion sources

Status: `ready-for-agent`

## What to build

Make `1拷问` and `2方案` consume a verified discussion source without duplicating the topic document, while preserving their existing behavior when no safe discussion context exists.

## Acceptance criteria

- [ ] `1拷问` updates the same 0/1 topic document in the current conversation or an approved dedicated grilling Phase Run and never creates a second problem-framing draft for verified discussion context.
- [ ] Dedicated grilling and solution-design carriers verify the frozen checkpoint, claim their attempt, report ready and wait for source-topic activation before substantive work.
- [ ] `1→3` is available only when scope, behavior, failures, acceptance conditions and test seam are complete; activation records a scoped 2-stage `not_applicable` result without fake planning artifacts.
- [ ] `0→2` and `1→2` freeze the latest effective 0/1 checkpoint; 2 owns only Spec, ADR, Tickets and its planning commit.
- [ ] Continuous mode starts only from a successful1 footer and does not propagate from0; existing stepwise confirmations and external authority boundaries remain intact.
- [ ] In `none` or `ambiguous` context, current `problem-framing` and `solution-design` entry, draft, publication and footer behavior remains unchanged.
- [ ] Integration tests cover current and dedicated1 carriers, solution designer ready/activate, pending impacts, source drift, direct-to3 and legacy/no-context flows.

Blocked by: 05 — Coordinate phase runs and lifecycle routing.

## Comments
