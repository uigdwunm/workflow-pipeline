# Integrate 1拷问 and 2方案 with discussion sources

Status: `ready-for-agent`
Lifecycle: `completed`

## What to build

Make `1拷问` and `2方案` consume a verified discussion source without duplicating the topic document, while preserving their existing behavior when no safe discussion context exists.

## Acceptance criteria

- [x] `1拷问` updates the same 0/1 topic document in the current conversation or an approved dedicated grilling Phase Run and never creates a second problem-framing draft for verified discussion context.
- [x] Dedicated grilling and solution-design carriers verify the frozen checkpoint, claim their attempt, report ready and wait for source-topic activation before substantive work.
- [x] `1→3` is available only when scope, behavior, failures, acceptance conditions and test seam are complete; activation records a scoped 2-stage `not_applicable` result without fake planning artifacts.
- [x] `0→2` and `1→2` freeze the latest effective 0/1 checkpoint; 2 owns only Spec, ADR, Tickets and its planning commit.
- [x] Continuous mode starts only from a successful1 footer and does not propagate from0; existing stepwise confirmations and external authority boundaries remain intact.
- [x] In `none` or `ambiguous` context, current `problem-framing` and `solution-design` entry, draft, publication and footer behavior remains unchanged.
- [x] Integration tests cover current and dedicated1 carriers, solution designer ready/activate, pending impacts, source drift, direct-to3 and legacy/no-context flows.

Blocked by: 05 — Coordinate phase runs and lifecycle routing.

## Comments

- 已在候选 `07f80210be6dd2b7d3c7a35662ef5d5085851e81` 中验收，并由合并提交 `3624df56bca2d3884074c3a4e55506e4374aec9e` 集成；最终 145 项验证通过。
