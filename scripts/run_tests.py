#!/usr/bin/env python3
"""Run each source test module in its own process, with bounded concurrency."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from validate_repository import discover_tests


ROOT = Path(__file__).resolve().parents[1]
EARLY = ("test_repository_validation.py", "test_solution_design_contract.py")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("jobs must be a positive integer")
    return number


def worker(test_file: Path, report: Path) -> int:
    sys.path.insert(0, str(test_file.parent))
    suite = unittest.defaultTestLoader.discover(str(test_file.parent), pattern=test_file.name)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    report.write_text(json.dumps({
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "successful": result.wasSuccessful(),
    }), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def stop_workers(active: dict) -> None:
    # Include Git/host subprocesses when stopping each isolated process group.
    for process in active:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 3
    while any(process.poll() is None for process in active) and time.monotonic() < deadline:
        time.sleep(0.05)
    for process, (_, log, _, _) in active.items():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        log.close()


def run(repository: Path, jobs: int) -> int:
    files = discover_tests(repository)
    if not files:
        print(f"No test files found under {repository / 'tests'}", file=sys.stderr)
        return 1
    files.sort(key=lambda path: (path.name not in EARLY, str(path)))
    pending = deque(enumerate(files))
    active = {}
    totals = dict.fromkeys(("tests", "failures", "errors", "skipped",
                           "expected_failures", "unexpected_successes"), 0)
    failed = []
    started = time.monotonic()
    print(f"Running {len(files)} test files with {jobs} worker(s)", flush=True)
    with tempfile.TemporaryDirectory(prefix="workflow-tests-") as directory:
        try:
            while pending or active:
                while pending and len(active) < jobs:
                    index, path = pending.popleft()
                    report = Path(directory) / f"{index}.json"
                    log = (Path(directory) / f"{index}.log").open("w+", encoding="utf-8")
                    try:
                        process = subprocess.Popen(
                            [sys.executable, "-B", str(Path(__file__).resolve()),
                             "--worker", str(path), "--report", str(report)],
                            cwd=repository, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                    except BaseException:
                        log.close()
                        raise
                    active[process] = (path, log, report, time.monotonic())
                for process, (path, log, report, began) in list(active.items()):
                    if process.poll() is None:
                        continue
                    successful = False
                    detail = "worker did not produce a valid report"
                    try:
                        result = json.loads(report.read_text(encoding="utf-8"))
                        if (not isinstance(result, dict)
                                or not all(type(result.get(key)) is int and result[key] >= 0 for key in totals)
                                or type(result.get("successful")) is not bool):
                            raise ValueError("invalid worker report")
                        for key in totals:
                            totals[key] += result[key]
                        successful = process.returncode == 0 and result["successful"]
                        detail = f"{result['tests']} tests, {result['skipped']} skipped"
                    except (OSError, ValueError):
                        pass
                    label = path.relative_to(repository)
                    print(f"{'PASS' if successful else 'FAIL'} {label}: {detail} "
                          f"({time.monotonic() - began:.1f}s, exit {process.returncode})", flush=True)
                    if not successful:
                        failed.append(str(label))
                        log.seek(0)
                        print(log.read(), end="", flush=True)
                    log.close()
                    del active[process]
                if active:
                    time.sleep(0.05)
        finally:
            stop_workers(active)
    print(f"Ran {totals['tests']} tests across {len(files)} files in {time.monotonic() - started:.1f}s; "
          + ", ".join(f"{key}={value}" for key, value in totals.items() if key != "tests"), flush=True)
    if failed:
        print("Failed files: " + ", ".join(failed), flush=True)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=positive_int, default=min(4, os.cpu_count() or 1),
                        help="maximum concurrent test processes (default: up to 4; use 1 for serial)")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.worker:
        if arguments.report is None:
            parser.error("--worker requires --report")
        return worker(arguments.worker, arguments.report)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(arguments.repository.resolve(), arguments.jobs)
    except KeyboardInterrupt:
        print("Test run interrupted; workers stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
