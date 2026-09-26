"""Exercise the test runner through disposable test suites and real processes."""
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


RUNNER = Path(__file__).resolve().parents[2] / "scripts/run_tests.py"


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
        return [sys.executable, "-B", str(RUNNER), "--repository", str(self.root), "--jobs", str(jobs)]

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
