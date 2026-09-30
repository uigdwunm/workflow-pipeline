# Reduce repeated Git reads during stage startup

The owner observed sustained fan load during repository tests. A single initial standalone Stage 2 case executed 382 Git subprocesses, including 215 in start. The earlier two-worker cap and shared test lock are complete; this change reduces repeated work inside the runtime.

Implementation baseline: `e5699cf4a312fcfa576b29d3311dac81c22c9f1b`. The preceding throwaway prototype is retained by tag `archive/git-query-prototype`, commit `3b2c072`; its interception mechanism is not production code.

## Requirements

- Aim for at most 107 Git calls in the measured Stage 2 start, with equivalent successful public JSON and unchanged pinned-package/CLI isolation.
- Keep current verification before the authority transition and before the host action. Keep cold verification on independent CLI calls, new requests, restored records and new actors.
- Reject changes to HEAD, branch/registration, source, symlink, protected live files/index, package identity and the original discussion reservation. Keep stop precedence and original transaction/attempt recovery.
- Reuse only internal facts with a concrete owner and lifetime. Public JSON must not accept an already-verified claim. Do not add persistent caches, queues, registries, generic command interception or new configuration.
- Preserve independent package construction, protocol compatibility keys and existing wire fields. Build source and generated copies together, review both correctness and repository standards, complete validation, then merge locally to main. Installation on the owner's Mac is outside this request.

## Implementation

C receives B's sealed preparation and the entry verified by that preparation, retaining checkpoint safety before C persistence. B privately normalizes its exact A binding and performs its complete Git verification at the latest binding check; differing A/B bindings still receive A's full independent check. Public A resolution remains full verification.

B reuses A's repository facts for the scope root's kind/identity checks, while live filesystem checks and B's later complete binding verification remain. The binding CLI still runs from the package's own directory; its original UTF-8 response is bounded, parsed and checked before using its repository facts. The latest worktree HEAD must match the entry observation.

Repeated committed document records/bytes are local to one delivery. Current paths, working bytes, mode and index are rechecked. Source reads remain cold. Git replacement refs and relevant configuration/environment are compared around delivery, including custom namespaces; a changed object view rejects the delivery. Stable replacement views retain their existing behavior. See [Git replacement documentation](https://git-scm.com/docs/git-replace) and [Git configuration](https://git-scm.com/docs/git-config/2.54.0).

Contiguous repository identity/location reads are batched. The non-Git probe remains separate; detected Git with invalid or unborn HEAD still fails. Whitespace/newline paths are preserved, with separate location reads when line framing is ambiguous.

## Validation and acceptance

The fixed performance case is `ProgressTests.test_launch_is_saved_before_host_and_never_reissued`. Count worker and Python-child Git calls together, excluding the runner's own two queries. Report setup/start/later-request/cleanup counts separately. Measure five interleaved baseline/candidate samples serially through the shared test runner; compare cumulative worker-plus-waited-child CPU and elapsed time without extrapolating to fan speed or the entire suite.

Regression coverage includes both gates, mutation midway through the final gate, restored B results without duplicate dispatch, actual ledger cancellation, public input spoofing, malformed binding CLI responses, Git object-view changes, current target bytes/index and unusual identity paths. Existing package and all-stage tests must continue to pass. Run `scripts/validate.sh --jobs 2` once the candidate and focused checks are ready, fix discovered failures, and report unexecuted checks honestly.
