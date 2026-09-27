"""Historical takeover fences tested with separate OS processes and real locks."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
import entry_prepare as entry
import workflow_progress as progress
import test_stage_scope_legacy as legacy


PRE_CONFIRMATION_V4 = "ec7e30e0f189cf00824aebcefa824faa4670dedb"
ROOT = legacy.ROOT

RUN_LOCK_HOLDER = r'''
import fcntl, os, sys
path = sys.argv[1]
descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(descriptor, fcntl.LOCK_EX)
print('ready', flush=True)
sys.stdin.readline()
fcntl.flock(descriptor, fcntl.LOCK_UN)
os.close(descriptor)
'''

LATE_OLD_WRITER = r'''
import fcntl, json, os, sys, time
from pathlib import Path
checkpoint = Path(sys.argv[1])
descriptor = os.open(str(checkpoint) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(descriptor, fcntl.LOCK_EX)
print('ready', flush=True)
time.sleep(0.4)
outer = json.loads(checkpoint.read_bytes())
outer['late_old_write'] = {'runtime': 'workflow-progress-v12'}
temporary = checkpoint.with_name(checkpoint.name + '.late-writer')
temporary.write_text(json.dumps(outer, sort_keys=True))
os.replace(temporary, checkpoint)
fcntl.flock(descriptor, fcntl.LOCK_UN)
os.close(descriptor)
'''

RACING_TAKEOVER = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import workflow_progress as progress
checkpoint, request, barrier = map(Path, sys.argv[2:])
print('ready', flush=True)
deadline = time.monotonic() + 10
while not barrier.exists():
    if time.monotonic() > deadline:
        raise RuntimeError('takeover barrier did not open')
    time.sleep(0.01)
try:
    result = progress.handle(checkpoint, json.loads(request.read_text()))
    print(json.dumps({'status': result['state'], 'acknowledged': result.get('acknowledged', False)}), flush=True)
except Exception as error:
    print(json.dumps({'error': getattr(error, 'code', type(error).__name__)}), flush=True)
'''

OLD_RUNTIME_AFTER_TAKEOVER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import workflow_progress as old
try:
    old.handle(Path(sys.argv[2]), {'protocol': old.PROTOCOL, 'operation': 'pause',
                                  'expected_revision': 0, 'data': {}})
    print(json.dumps({'status': 'unexpected-write'}))
except Exception as error:
    print(json.dumps({'error': getattr(error, 'code', type(error).__name__)}))
'''


class HistoricalTakeoverLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        available = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e",
            PRE_CONFIRMATION_V4 + "^{commit}"], capture_output=True)
        if available.returncode:
            raise unittest.SkipTest("historical prep-v4 runtime is absent from this checkout")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.scratch = Path(cls.temporary.name).resolve()
        cls.metadata = legacy.historical_fixture(PRE_CONFIRMATION_V4, cls.scratch)
        cls.checkpoint = Path(cls.metadata["checkpoint"])
        cls.old_bytes = cls.checkpoint.read_bytes()

    def setUp(self):
        self.checkpoint.write_bytes(self.old_bytes)
        self.scenario = legacy.LegacyScopeTests(methodName="runTest")
        self.scenario.scratch = self.scratch
        self.scenario.metadata = self.metadata
        self.scenario.checkpoint = self.checkpoint
        self.scenario.original_bytes = self.old_bytes
        self.scenario.original = json.loads(self.old_bytes)

    def _takeover(self):
        return self.scenario.takeover_inputs()

    def _ready(self, process):
        self.assertTrue(select.select([process.stdout], [], [], 5)[0], "lock process never became ready")
        self.assertEqual(process.stdout.readline().strip(), "ready")

    def test_pre_confirmation_v4_is_read_only_inspectable(self):
        self.assertEqual(self.metadata["old_pin"]["compatibility_key"]["preparation"],
                         "workflow-preparation-v4")
        self.assertNotIn("confirmation", self.metadata["old_pin"]["compatibility_key"])
        inspected = self.scenario.recover(self.scenario.recovery_input())
        self.assertEqual(inspected["state"], "inspected")
        self.assertEqual(self.checkpoint.read_bytes(), self.old_bytes)

    def test_other_process_run_lock_blocks_takeover_then_exact_retry_succeeds(self):
        inputs = self._takeover()
        lock_path = self.checkpoint.with_name(self.checkpoint.name + ".runner.lock")
        holder = subprocess.Popen([sys.executable, "-c", RUN_LOCK_HOLDER, str(lock_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self._ready(holder)
            with self.assertRaises(entry.PreparationError) as raised:
                self.scenario.recover(inputs)
            self.assertEqual(raised.exception.code, "run_busy")
            self.assertEqual(self.checkpoint.read_bytes(), self.old_bytes)
        finally:
            holder.stdin.write("\n")
            holder.stdin.flush()
            _, stderr = holder.communicate(timeout=5)
            self.assertEqual(holder.returncode, 0, stderr)
        prepared = self.scenario.recover(inputs)
        self.assertEqual(prepared["state"], "takeover-pending")
        current = json.loads(self.checkpoint.read_bytes())
        self.assertEqual(len(current[progress.KEY]["scope_recoveries"]), 1)
        self.assertEqual(base64.b64decode(current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]
                                           ["original_bytes_base64"]), self.old_bytes)

    def test_late_old_writer_under_record_lock_invalidates_exact_digest(self):
        inputs = self._takeover()
        writer = subprocess.Popen([sys.executable, "-c", LATE_OLD_WRITER, str(self.checkpoint)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self._ready(writer)
            with self.assertRaises(entry.PreparationError) as raised:
                self.scenario.recover(inputs)
            self.assertEqual(raised.exception.code, "legacy_checkpoint_changed")
        finally:
            _, stderr = writer.communicate(timeout=5)
            self.assertEqual(writer.returncode, 0, stderr)
        written = self.checkpoint.read_bytes()
        self.assertNotEqual(written, self.old_bytes)
        self.assertEqual(json.loads(written)["late_old_write"], {"runtime": "workflow-progress-v12"})
        self.assertEqual(json.loads(written)[progress.KEY]["protocol"], "workflow-progress-v12")
        self.assertNotIn("scope_recoveries", json.loads(written)[progress.KEY])

    def test_two_processes_cannot_create_two_current_takeovers(self):
        inputs = self._takeover()
        request = self.scratch / "racing-takeover.json"
        request.write_text(json.dumps({"protocol": progress.PROTOCOL,
            "operation": "scope-recover-legacy", "expected_revision": 0, "data": inputs}))
        barrier = self.scratch / "racing-takeover.go"
        if barrier.exists():
            barrier.unlink()
        workers = [subprocess.Popen([sys.executable, "-c", RACING_TAKEOVER,
            str(ROOT / "src/shared/scripts"), str(self.checkpoint), str(request), str(barrier)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        try:
            for worker in workers:
                self._ready(worker)
            barrier.write_text("go")
            results = []
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=15)
                self.assertEqual(worker.returncode, 0, stderr)
                results.append(json.loads(stdout.strip().splitlines()[-1]))
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill()
                    worker.communicate(timeout=5)
        self.assertTrue(any(item.get("status") == "takeover-pending" for item in results), results)
        self.assertTrue(all(item.get("status") == "takeover-pending" or item.get("error") == "run_busy"
                            for item in results), results)
        current = json.loads(self.checkpoint.read_bytes())
        self.assertEqual(current["version"], 8)
        self.assertEqual(len(current[progress.KEY]["scope_recoveries"]), 1)
        self.assertEqual(base64.b64decode(current[progress.KEY]["scope_recoveries"]["legacy-scope-1"]
                                           ["original_bytes_base64"]), self.old_bytes)

    def test_original_runtime_process_cannot_late_write_after_takeover(self):
        self.assertEqual(self.scenario.recover(self._takeover())["state"], "takeover-pending")
        before = self.checkpoint.read_bytes()
        old_scripts = Path(self.metadata["old_pin"]["root"]) / "scripts"
        late = subprocess.run([sys.executable, "-B", "-c", OLD_RUNTIME_AFTER_TAKEOVER,
            str(old_scripts), str(self.checkpoint)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(late.stdout)["error"], "legacy_run_requires_original_runtime")
        self.assertEqual(self.checkpoint.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
