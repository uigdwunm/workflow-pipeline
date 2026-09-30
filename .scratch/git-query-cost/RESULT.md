# Stage startup Git-read optimization: completed

Implementation `26e736021813b9e0cee55803a49a95a64d0e5db0` was fast-forward merged into local main from baseline `e5699cf4a312fcfa576b29d3311dac81c22c9f1b`.

The runtime shares B's preparation with C, defers only the exact internal binding to B's final full verification, reuses specific repository facts and committed document reads, and batches contiguous identity reads. Independent public calls and restoration remain cold; the four original package-local verification subprocesses remain. There is no persistent or general command cache.

## Measurements

Five serial interleaved samples of `ProgressTests.test_launch_is_saved_before_host_and_never_reissued` were run with one worker. All samples had the same call counts; CPU and elapsed values below are medians for the complete case.

| Metric | Baseline | Implementation | Reduction |
| --- | ---: | ---: | ---: |
| start Git subprocesses | 215 | 102 | 52.6% |
| complete case Git subprocesses | 382 | 241 | 36.9% |
| cumulative worker + waited-child CPU | 3.527 seconds | 2.428 seconds | 31.2% |
| elapsed time including runner/instrumentation | 4.043 seconds | 2.791 seconds | 31.0% |

Implementation phases: setup 129, start 102, later requests 9, cleanup 1. Runner's own two queries are excluded. See [sample data](profile/measurements.json) and [measurement command](profile/measure_case.py). This is one case with instrumentation; it does not measure fan speed or prove the same percentage for the entire suite.

## Validation

`bash scripts/validate.sh --jobs 2` passed: 1024 tests in 54 modules, zero failures/errors, one existing skip, 1951.9 seconds. All five Skill validators, generation drift checking and final repository validation passed. The suite covers independent generated packages and cross-package execution, all existing stages/recovery paths, and 40 new checks for identity reads, both startup gates, saved transactions, actual reservation cancellation, malformed CLI responses, and committed/live document and object-view changes.

The source and all generated packages were committed together. Wire fields and protocol compatibility keys are unchanged. This task did not install the generated packages on the owner's Mac or push to a remote.

## Standards

Independent read-only review found no new documented-standard violations or blocking code smells, and no overdesign. Private facts/deferral and the delivery-owned document reuse serve the current startup path; the five package copies are generated outputs.

## Spec

Independent read-only review found no missing requirements, scope expansion or confirmed implementation errors. Authority and launch gates, public/recovery/new-actor verification, pinned package CLI isolation, original transactions, stop precedence and actual discussion reservation readback remain intact.

Both reviews were against the immutable implementation candidate `26e7360`; reviewers did not substitute static review for the separately completed full validation.
