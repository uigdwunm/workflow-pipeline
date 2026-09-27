#!/usr/bin/env python3
"""Run each source test module in its own process, with bounded concurrency."""

from __future__ import annotations

import argparse
from collections import deque
import json
import math
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
DEFAULT_JOBS = min(2, os.cpu_count() or 1)
DEFAULT_NICE = 10


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("jobs must be a positive integer")
    return number


def worker_niceness(value: str) -> int:
    number = int(value)
    if not 0 <= number <= 19:
        raise argparse.ArgumentTypeError("worker niceness must be between 0 and 19")
    return number


def timing_file(repository: Path) -> Path | None:
    """Scheduling hints live outside tested files, including across worktrees."""
    try:
        environment = {key: value for key, value in os.environ.items()
                       if key not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR'}}
        result = subprocess.run(['git', 'rev-parse', '--show-toplevel', '--git-common-dir'], cwd=repository,
                                env=environment, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    locations = result.stdout.splitlines()
    if len(locations) != 2 or Path(locations[0]).resolve() != repository.resolve():
        return None
    return (repository / locations[1]).resolve() / 'workflow-test-timings.json'


def load_timings(path: Path | None) -> dict[str, float]:
    if path is None or not path.exists():
        return {}
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('timing history is too large')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('schema') != 1 or not isinstance(value.get('seconds'), dict):
            raise ValueError('invalid timing history')
        return {name: float(seconds) for name, seconds in value['seconds'].items()
                if isinstance(name, str) and type(seconds) in (int, float)
                and math.isfinite(seconds) and seconds > 0}
    except (OSError, ValueError, OverflowError):
        print('Ignoring unreadable test timing history; using fallback order.', file=sys.stderr)
        return {}


def save_timings(path: Path | None, seconds: dict[str, float]) -> None:
    if path is None:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.test-timings-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'schema': 1, 'seconds': seconds}, stream, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, path)
    except OSError as error:
        print(f'Could not save test timing hints: {error}', file=sys.stderr)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as error:
                print(f'Could not remove temporary timing hints: {error}', file=sys.stderr)


def ordered_tests(files: list[Path], repository: Path, jobs: int, seconds: dict[str, float]) -> list[Path]:
    if jobs == 1:
        return sorted(files, key=lambda path: (path.name not in EARLY, str(path)))
    return sorted(files, key=lambda path: (
        path.name not in EARLY, -seconds.get(path.relative_to(repository).as_posix(), 0.0),
        -path.stat().st_size, str(path)))


def worker(test_file: Path, report: Path, niceness: int = DEFAULT_NICE) -> int:
    if niceness:
        # Never raise inherited priority, or compound niceness in nested runners.
        current = os.getpriority(os.PRIO_PROCESS, 0)
        if current < niceness:
            os.nice(niceness - current)
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


def run(repository: Path, jobs: int, niceness: int = DEFAULT_NICE) -> int:
    files = discover_tests(repository)
    if not files:
        print(f"No test files found under {repository / 'tests'}", file=sys.stderr)
        return 1
    history_path = timing_file(repository)
    history = load_timings(history_path)
    files = ordered_tests(files, repository, jobs, history)
    pending = deque(enumerate(files))
    measured = {}
    if niceness and not all(hasattr(os, attr) for attr in ('nice', 'getpriority', 'PRIO_PROCESS')):
        print('Worker priority adjustment is unavailable; keeping inherited priority.', file=sys.stderr)
        niceness = 0
    active = {}
    totals = dict.fromkeys(("tests", "failures", "errors", "skipped",
                           "expected_failures", "unexpected_successes"), 0)
    failed = []
    started = time.monotonic()
    print(f"Running {len(files)} test files with {jobs} worker(s), worker nice={niceness} "
          f"({'stable serial order' if jobs == 1 else 'longest recorded tests first'})", flush=True)
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
                             "--worker", str(path), "--report", str(report), "--nice", str(niceness)],
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
                    elapsed = time.monotonic() - began
                    if successful:
                        measured[label.as_posix()] = elapsed
                    print(f"{'PASS' if successful else 'FAIL'} {label}: {detail} "
                          f"({elapsed:.1f}s, exit {process.returncode})", flush=True)
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
    if measured:
        existing = {path.relative_to(repository).as_posix() for path in files}
        save_timings(history_path, {**{key: value for key, value in history.items() if key in existing}, **measured})
    print(f"Ran {totals['tests']} tests across {len(files)} files in {time.monotonic() - started:.1f}s; "
          + ", ".join(f"{key}={value}" for key, value in totals.items() if key != "tests"), flush=True)
    if failed:
        print("Failed files: " + ", ".join(failed), flush=True)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=positive_int, default=DEFAULT_JOBS,
                        help="maximum concurrent test processes (default: up to 2; use 1 for serial)")
    parser.add_argument("--nice", type=worker_niceness, default=DEFAULT_NICE,
                        help="worker CPU niceness 0-19 (default: 10; 0 keeps inherited priority)")
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.worker:
        if arguments.report is None:
            parser.error("--worker requires --report")
        return worker(arguments.worker, arguments.report, arguments.nice)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(arguments.repository.resolve(), arguments.jobs, arguments.nice)
    except KeyboardInterrupt:
        print("Test run interrupted; workers stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
