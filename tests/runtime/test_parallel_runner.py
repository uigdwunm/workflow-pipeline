"""Exercise the test runner through disposable test suites and real processes."""
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


RUNNER = Path(__file__).resolve().parents[2] / "scripts/run_tests.py"
sys.path.insert(0, str(RUNNER.parent))
import run_tests as runner


class ParallelRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tests = self.root / "tests"
        self.tests.mkdir()

    def source(self, name, body):
        (self.tests / f"test_{name}.py").write_text(textwrap.dedent(body), encoding="utf-8")

    def command(self, jobs):
        command = [sys.executable, "-B", str(RUNNER), "--repository", str(self.root)]
        return command if jobs is None else command + ["--jobs", str(jobs)]

    def run_suite(self, jobs=2):
        return subprocess.run(self.command(jobs), capture_output=True, text=True, timeout=30)

    def test_files_really_overlap_in_isolated_processes(self):
        for name, peer in (("a", "b"), ("b", "a")):
            self.source(name, f'''
                import os, time, unittest
                from pathlib import Path
                class Case(unittest.TestCase):
                    def test_overlap(self):
                        os.environ['RUNNER_ISOLATION'] = '{name}'
                        Path('{name}.ready').write_text(str(os.getpid()))
                        deadline = time.monotonic() + 10
                        while not Path('{peer}.ready').exists() and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertTrue(Path('{peer}.ready').exists())
                        self.assertNotEqual(Path('{peer}.ready').read_text(), str(os.getpid()))
                        self.assertEqual(os.environ['RUNNER_ISOLATION'], '{name}')
            ''')
        result = self.run_suite()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 2 tests across 2 files", result.stdout)

    def test_one_job_is_serial_and_respects_load_tests(self):
        self.source("a", '''
            import unittest
            from pathlib import Path
            class Case(unittest.TestCase):
                def test_first(self): Path('first.done').write_text('done')
        ''')
        self.source("b", '''
            import unittest
            from pathlib import Path
            class Case(unittest.TestCase):
                def test_second(self): self.assertTrue(Path('first.done').exists())
                def test_excluded(self): self.fail('load_tests must exclude me')
            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([Case('test_second')])
        ''')
        result = self.run_suite(1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 2 tests across 2 files", result.stdout)

    def test_failures_crashes_and_skips_are_not_hidden(self):
        self.source("failure", '''
            import unittest
            class Case(unittest.TestCase):
                def test_fail(self): self.fail('visible assertion')
                @unittest.skip('fixture skip')
                def test_skip(self): pass
                def test_error(self): raise RuntimeError('visible error')
        ''')
        self.source("crash", "import os\nos._exit(7)\n")
        self.source("import_error", "raise RuntimeError('visible import error')\n")
        self.source("success", '''
            import unittest
            class Case(unittest.TestCase):
                def test_ok(self): pass
        ''')
        result = self.run_suite()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for marker in ("Ran 5 tests across 4 files", "failures=1", "errors=2", "skipped=1",
                       "visible assertion", "visible error", "visible import error", "exit 7",
                       "PASS tests/test_success.py", "Failed files:"):
            self.assertIn(marker, result.stdout)

    def test_unexpected_success_fails_validation(self):
        self.source("unexpected", '''
            import unittest
            class Case(unittest.TestCase):
                @unittest.expectedFailure
                def test_unexpected(self): pass
        ''')
        result = self.run_suite()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unexpected_successes=1", result.stdout)

    def test_empty_suite_and_invalid_concurrency_fail(self):
        result = self.run_suite()
        self.assertEqual(result.returncode, 1)
        self.assertIn("No test files found", result.stderr)
        for jobs in (0, -1, "invalid"):
            with self.subTest(jobs=jobs):
                self.assertEqual(self.run_suite(jobs).returncode, 2)

    def test_default_limits_active_workers_to_two(self):
        for name in ('one', 'two', 'three'):
            self.source(name, '''
                import fcntl, json, time, unittest
                from pathlib import Path
                def change(delta):
                    with open('count.lock', 'a+') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        path = Path('counts.json')
                        counts = json.loads(path.read_text()) if path.exists() else {'active':0,'peak':0}
                        counts['active'] += delta
                        counts['peak'] = max(counts['peak'], counts['active'])
                        path.write_text(json.dumps(counts))
                class Case(unittest.TestCase):
                    def test_bounded(self):
                        change(1)
                        try: time.sleep(0.1)
                        finally: change(-1)
            ''')
        result = self.run_suite(None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        counts = json.loads((self.root/'counts.json').read_text())
        self.assertEqual(counts['active'], 0)
        self.assertLessEqual(counts['peak'], 2)
        self.assertIn(f'with {min(2, os.cpu_count() or 1)} worker(s)', result.stdout)

    @unittest.skipUnless(hasattr(os, 'getpriority') and hasattr(os, 'nice'), 'POSIX priorities required')
    def test_worker_and_subprocess_have_lower_priority_without_compounding(self):
        inherited = os.getpriority(os.PRIO_PROCESS, 0)
        self.source('priority', '''
            import json, os, subprocess, sys, unittest
            from pathlib import Path
            class Case(unittest.TestCase):
                def test_priority(self):
                    child = subprocess.check_output([sys.executable, '-c',
                        'import os; print(os.getpriority(os.PRIO_PROCESS, 0))'], text=True)
                    Path('priorities.json').write_text(json.dumps([os.getpriority(os.PRIO_PROCESS, 0), int(child)]))
        ''')
        for nice in (0, 10):
            result = subprocess.run(self.command(1) + ['--nice', str(nice)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected = max(inherited, nice) if nice else inherited
            self.assertEqual(json.loads((self.root/'priorities.json').read_text()), [expected, expected])

    def test_history_prioritizes_long_tests_but_serial_order_is_stable(self):
        for name in ('a_short', 'z_long', 'repository_validation'):
            self.source(name, '# fixture\n')
        files = list(self.tests.glob('test_*.py'))
        history = {'tests/test_a_short.py': 1.0, 'tests/test_z_long.py': 100.0}
        self.assertEqual([p.name for p in runner.ordered_tests(files, self.root, 2, history)],
                         ['test_repository_validation.py', 'test_z_long.py', 'test_a_short.py'])
        self.assertEqual([p.name for p in runner.ordered_tests(files, self.root, 1, history)],
                         ['test_repository_validation.py', 'test_a_short.py', 'test_z_long.py'])

    def test_timings_are_updated_without_hiding_failures(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        history = self.root/'.git/workflow-test-timings.json'
        history.write_text(json.dumps({'schema': 1, 'seconds': {'tests/test_failure.py': 9.0}}))
        self.source('success', 'import unittest\nclass Case(unittest.TestCase):\n    def test_ok(self): pass\n')
        self.source('failure', 'import unittest\nclass Case(unittest.TestCase):\n    def test_fail(self): self.fail("still fails")\n')
        result = self.run_suite()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        seconds = json.loads(history.read_text())['seconds']
        self.assertGreater(seconds['tests/test_success.py'], 0)
        self.assertEqual(seconds['tests/test_failure.py'], 9.0)
        self.assertIn('still fails', result.stdout)

    def test_invalid_history_is_only_a_scheduling_hint(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root/'.git/workflow-test-timings.json').write_text('{broken')
        self.source('only', 'import unittest\nclass Case(unittest.TestCase):\n    def test_ok(self): pass\n')
        result = self.run_suite()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ignoring unreadable test timing history', result.stderr)
        self.assertIn('Ran 1 tests across 1 files', result.stdout)

    def test_nested_non_git_fixture_does_not_use_parent_history(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.assertIsNone(runner.timing_file(self.tests))

    def test_invalid_priority_is_rejected(self):
        for nice in ('-1', '20', 'invalid'):
            result = subprocess.run(self.command(1) + ['--nice', nice], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2)

    def test_interrupt_stops_worker_and_its_subprocess(self):
        child = self.root / "child.py"
        child.write_text(textwrap.dedent('''
            import signal, sys, time
            from pathlib import Path
            def stop(signum, frame):
                Path('child.stopped').touch()
                sys.exit(0)
            signal.signal(signal.SIGTERM, stop)
            Path('child.ready').touch()
            time.sleep(60)
        '''))
        self.source("waiting", '''
            import signal, subprocess, sys, time, unittest
            from pathlib import Path
            class Case(unittest.TestCase):
                def test_wait(self):
                    def stop(signum, frame):
                        Path('worker.stopped').touch()
                        sys.exit(0)
                    signal.signal(signal.SIGTERM, stop)
                    child = subprocess.Popen([sys.executable, 'child.py'])
                    try: child.wait()
                    finally: child.wait(timeout=5)
        ''')
        process = subprocess.Popen(self.command(2), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while not (self.root / "child.ready").exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root / "child.ready").exists())
            process.send_signal(signal.SIGINT)
            output, error = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 130, output + error)
            self.assertTrue((self.root / "worker.stopped").exists())
            self.assertTrue((self.root / "child.stopped").exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
