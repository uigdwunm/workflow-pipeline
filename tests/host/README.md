# Foreground native lifecycle acceptance

Run these checks explicitly in an isolated temporary repository. They are not
part of default unit-test discovery and do not install or deploy packages.
Use the built candidate's real `foreground_host.py`; preserve its module hash,
package digest, CLI version, Controller configuration and complete raw events.

```sh
python3 scripts/build_skills.py
python3 tests/host/verify_native_lifecycle.py --input /absolute/controller-evidence.json
python3 tests/host/verify_native_lifecycle.py --case stop --input /absolute/another-output-evidence.json
```

The input contains `workspace`, a fresh `output` directory, `controller_ref`,
`settings: {model, reasoning_effort}` and the version-5 confirmed `host` object
documented in the shared workflow-progression reference. Create an isolated Git
repository plus its `flow` directory first. Freeze actual current permissions,
approval policy, workspace roots and their Controller source; there is no safe
default to guess. The adapter compares the actual thread readback before its
first business turn. Each run requires a different output directory.

The lifecycle case uses one native child, ends the first parent turn while that
child waits, then waits for completion and sends one same-identity followup in
the original host. It checks raw creation/completion/interaction events, exact
thread/turn IDs, the current descendant lookup and local process cleanup.

The stop case interrupts the original child, explicitly resumes that same
target with a bounded waiting task, then cancels it. Each parent turn uses the
same product connection. Current descendant states and raw interrupted and
interacted events establish the observations separately from model footers.
Neither case creates nested agents or silently replaces an unavailable target.

`events.jsonl`, `stderr.log` and `report.json` remain under the supplied isolated
output directory. A failed run reports failure and local resource cleanup;
process exit never becomes native stop proof. Do not copy private raw logs into
the repository. Do not resume any existing production runner or reuse its pins.

The deterministic suites separately drive public runner start/resume/control
and real C/B/Git with only external transport/storage faults substituted. They
cover queued answers, stop ordering, both fixed review slots, unbound execution
allocations, unknown descendants, raw receipt recovery and unique publication.
Those tests certify product decisions, not real-host capabilities. The explicit
host reports certify the listed real product-adapter/native behaviors, not a
complete model-driven Stage 2→3→4 business flow.

Real nested integration remains **pending external dependency**. Its external
governance prerequisite is owned elsewhere. Do not investigate, modify, bypass
or rerun that nested integration here. Keep its runtime gates closed when actual
identity, stop or unresolved-call evidence is missing; never count a transport
double or the single-layer checks as a nested-host pass. Cross-host resurrection
is also unproven: the product retains evidence and awaits original-host recovery.

For review-first validation, run both real cases only after the exact candidate
has converged Standards and Spec review. Bind their raw reports to that candidate
and its frozen final validation attempt. A prior package report does not validate
the new candidate. Keep the existing isolated test skip reason explicit.
