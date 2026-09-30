# Reduce stage-start Git reads

Status: ready-for-agent

Spec: ../PRD.md

Owner authorized formal implementation, necessary validation, independent review and local merge to main. Work is based on `e5699cf4a312fcfa576b29d3311dac81c22c9f1b`.

## Acceptance

- One measured start uses at most 107 Git calls, including package CLI children.
- Public and restored calls keep complete fresh verification and original attempts.
- Published package isolation, wire fields and compatibility keys remain intact.
- Focused checks, complete validation and independent Standards/Spec review pass.

## Comments

- Initial formal sample: total 241 calls (setup 129, start 102, later requests 9, cleanup 1), with all four binding CLI subprocesses retained. Repeated sampling and complete validation remain in progress.
- Focused checks passed: 13 identity/location, 19 startup/recovery/ledger, 6 committed-document/object-view, 2 binding CLI response checks.
