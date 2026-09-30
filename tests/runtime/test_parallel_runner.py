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
        for marker in ("Ran 4 tests across 4 files", "failures=1", "errors=2", "skipped=1",
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
        for jobs in (0, -1, 3, 4, "invalid"):
            with self.subTest(jobs=jobs):
                self.assertEqual(self.run_suite(jobs).returncode, 2)

    def test_excess_concurrency_is_rejected_before_tests_start(self):
        self.source('unexpected', '''
            import unittest
            from pathlib import Path
            class Case(unittest.TestCase):
                def test_must_not_start(self): Path('started').touch()
        ''')
        result = self.run_suite(4)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('maximum: 2 workers', result.stderr)
        self.assertFalse((self.root / 'started').exists())

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

    def test_selected_module_and_case_respect_load_tests(self):
        self.source('selected', '''
            import unittest
            class Case(unittest.TestCase):
                def test_keep(self): pass
                def test_other(self): self.fail('unselected case ran')
                def test_excluded(self): self.fail('load_tests was bypassed')
            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([Case('test_keep'), Case('test_other')])
        ''')
        self.source('unselected', "raise RuntimeError('unselected module loaded')\n")
        command = self.command(2) + ['--test-file', 'tests/test_selected.py', '--match', 'test_keep']
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ran 1 tests across 1 files', result.stdout)
        for match in ('test_excluded', 'missing'):
            result = subprocess.run(command[:-1] + [match], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('No tests match', result.stdout)

    def test_filtered_load_error_is_not_hidden(self):
        self.source('broken', "raise RuntimeError('filtered import error')\n")
        result = subprocess.run(self.command(1) + ['--test-file', 'tests/test_broken.py', '--match', 'absent'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('filtered import error', result.stdout)
        self.assertIn('errors=1', result.stdout)

    def test_load_tests_cannot_discard_loading_errors_or_run_unselected_cases(self):
        for empty in (False, True):
            self.source('hook_error', f'''
                import unittest
                from pathlib import Path
                class Case(unittest.TestCase):
                    def test_must_not_start(self): Path('started').touch()
                def load_tests(loader, tests, pattern):
                    loader.loadTestsFromName('workflow_runner_missing_test_module')
                    return unittest.TestSuite({'[]' if empty else "[Case('test_must_not_start')]"})
            ''')
            for match in (None, 'test_must_not_start', 'absent'):
                with self.subTest(empty=empty, match=match):
                    command = self.command(1) + ['--test-file', 'tests/test_hook_error.py']
                    if match is not None:
                        command += ['--match', match]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn('workflow_runner_missing_test_module', result.stdout)
                    self.assertIn('errors=1', result.stdout)
                    self.assertIn('Ran 0 tests across 1 files', result.stdout)
                    self.assertFalse((self.root / 'started').exists())

    def test_invalid_filter_and_outside_file_are_rejected(self):
        self.source('only', '# empty fixture\n')
        for arguments in (['--match', 'anything'], ['--test-file', 'tests/test_only.py', '--match', ' '],
                          ['--test-file', '../outside.py'], ['--test-file', 'tests/missing.py'],
                          ['--test-f', 'tests/test_only.py']):
            with self.subTest(arguments=arguments):
                result = subprocess.run(self.command(1) + arguments, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('Running ', result.stdout)
        outside = self.root / 'outside.py'
        outside.write_text("raise RuntimeError('outside module loaded')\n")
        (self.tests / 'test_link.py').symlink_to(outside)
        result = subprocess.run(self.command(1) + ['--test-file', 'tests/test_link.py'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn('inside tests/', result.stderr)
        self.assertNotIn('outside module loaded', result.stdout)

    def test_module_selection_preserves_other_timings_and_case_selection_does_not_update_them(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        history = self.root / '.git/workflow-test-timings.json'
        original = {'schema': 1, 'seconds': {'tests/test_a.py': 50.0, 'tests/test_b.py': 60.0}}
        history.write_text(json.dumps(original))
        for name in ('a', 'b'):
            self.source(name, 'import unittest\nclass Case(unittest.TestCase):\n    def test_ok(self): pass\n')
        command = self.command(1) + ['--test-file', 'tests/test_a.py']
        result = subprocess.run(command + ['--match', 'test_ok'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(history.read_text()), original)
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        updated = json.loads(history.read_text())['seconds']
        self.assertEqual(updated['tests/test_b.py'], 60.0)
        self.assertGreater(updated['tests/test_a.py'], 0)
        self.assertNotEqual(updated['tests/test_a.py'], 50.0)

    def test_full_validation_rejects_filters_before_checks(self):
        validate = RUNNER.with_name('validate.sh')
        for arguments in (['--test-file', 'tests/test_selected.py'], ['--test-file=tests/test_selected.py'],
                          ['--match', 'anything'], ['--match=anything']):
            result = subprocess.run(['bash', str(validate), *arguments], capture_output=True, text=True,
                                    env={**os.environ, 'CODEX_SKILL_VALIDATOR': '/missing/validator'}, timeout=30)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('full test suite', result.stderr)
            self.assertNotIn('Skill validator not found', result.stderr)

    def test_full_validation_rejects_repository_and_worker_overrides_before_checks(self):
        validate = RUNNER.with_name('validate.sh')
        for arguments in (['--repository', str(self.root)], ['--repository=' + str(self.root)],
                          ['--worker', 'tests/test_only.py', '--report', 'report.json'],
                          ['--worker=tests/test_only.py'], ['--report=report.json'], ['--help']):
            with self.subTest(arguments=arguments):
                result = subprocess.run(['bash', str(validate), *arguments], capture_output=True, text=True,
                                        env={**os.environ, 'CODEX_SKILL_VALIDATOR': '/missing/validator'}, timeout=30)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('only accepts --jobs and --nice', result.stderr)
                self.assertNotIn('Skill validator not found', result.stderr)
                self.assertNotIn('Validation complete', result.stdout)
        for option in ('--jobs', '--nice'):
            result = subprocess.run(['bash', str(validate), option], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('requires a value', result.stderr)

    def test_full_validation_forwards_public_options_and_preserves_check_order(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        validate = scripts / 'validate.sh'
        validate.write_text(RUNNER.with_name('validate.sh').read_text())
        (self.root / 'skills/one').mkdir(parents=True)
        validator = scripts / 'quick_validate.py'
        validator.write_text("print('skill')\n")
        (scripts / 'build_skills.py').write_text("print('build')\n")
        (scripts / 'validate_repository.py').write_text("print('repository')\n")
        (scripts / 'run_tests.py').write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n')
        for arguments in ([], ['--jobs', '2', '--nice', '10'], ['--jobs=1', '--nice=0']):
            with self.subTest(arguments=arguments):
                result = subprocess.run(['bash', str(validate), *arguments], capture_output=True, text=True,
                                        env={**os.environ, 'CODEX_SKILL_VALIDATOR': str(validator)}, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                lines = result.stdout.splitlines()
                self.assertEqual(lines[:2], ['build', 'skill'])
                forwarded = json.loads(lines[2])
                self.assertEqual(forwarded[0], '--repository')
                self.assertEqual(Path(forwarded[1]).resolve(), self.root.resolve())
                self.assertEqual(forwarded[2:], arguments)
                self.assertEqual(lines[3:], ['repository', 'Validation complete.'])

    def holding_run(self):
        self.source('holding', '''
            import os, time, unittest
            from pathlib import Path
            class Case(unittest.TestCase):
                def test_hold(self):
                    Path('worker.ready').write_text(str(os.getpid()))
                    deadline = time.monotonic() + 20
                    while not Path('release').exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
        ''')
        process = subprocess.Popen(self.command(1), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            ready = self.root / 'worker.ready'
            if ready.exists():
                try:
                    os.killpg(int(ready.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 10
        while not (self.root / 'worker.ready').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.root / 'worker.ready').exists())
        return process

    def test_same_repository_is_busy_then_releases_after_completion(self):
        process = self.holding_run()
        result = self.run_suite(1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Tests already running', result.stderr)
        self.assertIn(str(process.pid), result.stderr)
        self.assertIn(str(self.root.resolve()), result.stderr)
        self.assertNotIn('Running ', result.stdout)
        (self.root / 'release').touch()
        output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, output + error)
        result = self.run_suite(1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failure_releases_lock_for_the_next_run(self):
        self.source('failure', 'import unittest\nclass Case(unittest.TestCase):\n    def test_fail(self): self.fail("fixture")\n')
        self.assertEqual(self.run_suite(1).returncode, 1)
        self.source('failure', 'import unittest\nclass Case(unittest.TestCase):\n    def test_ok(self): pass\n')
        result = self.run_suite(1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_terminate_stops_worker_before_releasing_lock(self):
        process = self.holding_run()
        process.terminate()
        output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130, output + error)
        with runner.test_lock(self.root.resolve()):
            pass

    def test_worktrees_share_lock_but_another_repository_is_independent(self):
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.root)], check=True)
        self.source('only', 'import unittest\nclass Case(unittest.TestCase):\n    def test_ok(self): pass\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'tests'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
        peer_parent = tempfile.TemporaryDirectory()
        self.addCleanup(peer_parent.cleanup)
        peer = Path(peer_parent.name) / 'peer'
        subprocess.run(['git', '-C', str(self.root), 'worktree', 'add', '-qb', 'peer', str(peer)], check=True)
        process = self.holding_run()
        result = subprocess.run([sys.executable, '-B', str(RUNNER), '--repository', str(peer)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Tests already running', result.stderr)
        with tempfile.TemporaryDirectory() as separate:
            repository = Path(separate)
            subprocess.run(['git', 'init', '-q', str(repository)], check=True)
            (repository / 'tests').mkdir()
            (repository / 'tests/test_only.py').write_text('# empty independent suite\n')
            result = subprocess.run([sys.executable, '-B', str(RUNNER), '--repository', separate],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        process.terminate()
        process.communicate(timeout=10)

    def test_killed_coordinator_does_not_release_live_worker_lock(self):
        process = self.holding_run()
        process.kill()
        process.communicate(timeout=10)
        result = self.run_suite(1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Tests already running', result.stderr)
        worker_pid = int((self.root / 'worker.ready').read_text())
        (self.root / 'release').touch()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with runner.test_lock(self.root.resolve()):
                    break
            except runner.RunnerError:
                time.sleep(0.02)
        else:
            self.fail('Lock remained busy after the orphan worker completed: ' + str(worker_pid))
        result = self.run_suite(1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_broken_git_identity_fails_without_starting_workers(self):
        self.source('only', '# empty fixture\n')
        (self.root / '.git').write_text('gitdir: /missing/workflow-runner-git-dir\n')
        result = self.run_suite(1)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Cannot resolve', result.stderr)
        self.assertNotIn('Running ', result.stdout)

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
            with runner.test_lock(self.root.resolve()):
                pass
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
