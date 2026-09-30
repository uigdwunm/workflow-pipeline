# Reduce stage-start Git reads

Status: ready-for-agent
Lifecycle: completed

Spec: ../PRD.md

Owner authorized formal implementation, necessary validation, independent review and local merge to main. Work is based on `e5699cf4a312fcfa576b29d3311dac81c22c9f1b`.

## Acceptance

- One measured start uses at most 107 Git calls, including package CLI children.
- Public and restored calls keep complete fresh verification and original attempts.
- Published package isolation, wire fields and compatibility keys remain intact.
- Focused checks, complete validation and independent Standards/Spec review pass.

## Comments

- Five interleaved formal samples each used 241 calls (setup 129, start 102, later requests 9, cleanup 1), with all four binding CLI subprocesses retained. Baseline used 382 total and 215 start calls. CPU and elapsed medians decreased approximately 31% in this one case.
- Focused checks passed: 13 identity/location, 19 startup/recovery/ledger, 6 committed-document/object-view, 2 binding CLI response checks.
- Complete validation: 1024 tests across 54 files in 1951.9 seconds; zero failures/errors, one existing skip. All five package validators, generation check and repository validation passed.
- Independent Standards and Spec reviews of candidate `26e736021813b9e0cee55803a49a95a64d0e5db0` against `e5699cf` reported zero findings and no overdesign. The candidate was fast-forward merged into local main. No push or installation was performed.
