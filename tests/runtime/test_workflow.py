from __future__ import annotations

import json
import shutil
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import hashlib


SCRIPT = Path(__file__).resolve().parents[2] / "skills/guided-implementation/scripts/workflow.py"
FIXTURE = """#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
prompt = sys.argv[-1]
payload = json.loads(prompt.rsplit('\\n', 1)[1])
stage = payload['stage']
out = sys.argv[sys.argv.index('-o') + 1]
resume = sys.argv[2] == 'resume'
state_path = os.environ['FIXTURE_STATE']
try: state = json.load(open(state_path))
except FileNotFoundError: state = {}
state[stage] = state.get(stage, 0) + 1
state.setdefault('prompts', {})[stage] = prompt
state.setdefault('commands', {})[stage] = sys.argv
state.setdefault('pids', {})[stage] = os.getpid()
json.dump(state, open(state_path, 'w'))
mode = os.environ.get('FIXTURE_MODE', 'success')
if mode == 'checkpoint-lock':
    from pathlib import Path
    sys.path.insert(0, str(Path(payload['skill_paths']['stage2']).parent / 'scripts'))
    from workflow_progress import record_lock, read_record, atomic_save
    checkpoint = Path(payload['progression_checkpoint'])
    with record_lock(checkpoint):
        current = read_record(checkpoint)
        current['workflow_requirements'] = {'fixture': {'retained': True}}
        atomic_save(checkpoint, current)
        print(json.dumps({'type': 'thread.started', 'thread_id': stage + '-session'}), flush=True)
        time.sleep(1)
    mode = 'needs-input'
if mode == 'checkpoint-lock-finish':
    from pathlib import Path
    checkpoint = payload['progression_checkpoint']
    locker = subprocess.Popen([sys.executable, os.environ['FIXTURE_LOCK_HOLDER'], checkpoint],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    state['locker_pid'] = locker.pid
    json.dump(state, open(state_path, 'w'))
    deadline = time.monotonic() + 5
    while not Path(checkpoint + '.fixture-held').exists():
        if time.monotonic() > deadline: raise RuntimeError('fixture lock did not start')
        time.sleep(0.01)
    print(json.dumps({'type': 'thread.started', 'thread_id': stage + '-session'}), flush=True)
    mode = 'needs-input'
if mode == 'package-change' and stage == 'stage3':
    open(payload['skill_paths']['stage4'], 'a').write('changed by fault injection')
if mode == 'registry-remove' and stage == 'stage3':
    current = json.load(open(payload['registry_input']))
    current['registry']['entries'] = current['registry']['entries'][:-1]
    json.dump(current, open(payload['registry_input'], 'w'))
if mode == 'nonzero': sys.exit(9)
if mode == 'tail-error':
    print('x' * 70000)
    print('useful final failure detail')
    sys.exit(9)
if mode in ('interrupt', 'interrupt-child'):
    if mode == 'interrupt-child':
        child = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)'])
        state.setdefault('children', {})[stage] = child.pid
        json.dump(state, open(state_path, 'w'))
    print(json.dumps({'type': 'thread.started', 'thread_id': stage + '-session'}), flush=True)
    time.sleep(10)
if mode not in ('interrupt', 'interrupt-child', 'no-session-continue', 'no-session-needs-input'):
    print(json.dumps({'type': 'thread.started', 'thread_id': stage + '-session'}))
if mode == 'turn-failed':
    print(json.dumps({'type': 'turn.failed'})); sys.exit(0)
if mode != 'no-completion': print(json.dumps({'type': 'turn.completed'}))
if mode == 'malformed': open(out, 'w').write('{')
elif mode == 'no-result': sys.exit(0)
elif mode in ('needs-input', 'no-session-needs-input') and stage == 'stage2' and not resume:
    json.dump({'result': 'needs_input', 'artifacts': [], 'evidence': [], 'handoff_json': '{}', 'question': 'choose a value', 'message': '', 'needs_input_kind': 'user_decision'}, open(out, 'w'))
elif mode == 'technical-error':
    json.dump({'result': 'needs_input', 'artifacts': [], 'evidence': [], 'handoff_json': '{}', 'question': 'Git metadata write denied', 'message': '', 'needs_input_kind': 'technical_error'}, open(out, 'w'))
else:
    if mode == 'hold' and resume: time.sleep(1)
    if mode in ('continue', 'stale', 'no-session-continue') and stage == 'stage2' and state[stage] == 1:
        json.dump({'result': 'continue', 'artifacts': [], 'evidence': [], 'handoff_json': '{}', 'question': '', 'message': 'remaining work', 'needs_input_kind': 'none'}, open(out, 'w'))
        sys.exit(0)
    if mode == 'stale' and stage == 'stage2': sys.exit(0)
    binding = {'base_commit': 'a' * 40, 'branch': 'codex/flow', 'git_common_dir': payload['git_common_dir'], 'repository': payload['repository'], 'target_branch': payload['target_branch'], 'worktree': payload['worktree']}
    if mode == 'foreign-binding': binding['repository'] = '/foreign'
    if mode == 'mutated-stage3-binding' and stage == 'stage3': binding['branch'] = 'codex/other'
    handoff = ({'binding': binding, 'planning_commit': 'b' * 40, 'allowed_paths': ['a'], 'protected_paths': []} if stage == 'stage2' else ({'binding': binding, 'candidate_commit': 'c' * 40, 'review': {'standards': 'accepted', 'spec': 'accepted'}, 'verification': ['full']} if stage == 'stage3' else {}))
    handoff.update(controller_ref=payload.get('controller_ref', 'controller'), role_ref=stage + '-native')
    handoff['control_checkpoint'] = {'controller_ref': handoff['controller_ref'], 'stage': int(stage[-1]), 'role_ref': stage + '-native', 'state': 'completed', 'role_kind': {'stage2': 'solution-designer', 'stage3': 'implementation-dispatcher', 'stage4': 'closure-agent'}[stage]}
    if stage == 'stage3':
        handoff.update(implementation_paths=['a'], closure_paths=['README.md'], protected_paths=[])
        handoff['review'] = {axis: {'candidate': 'c' * 40, 'reviewer_ref': axis, 'status': 'accepted'} for axis in ('standards', 'spec')}
        handoff['verification'] = {'candidate': 'c' * 40, 'checks': ['full']}
    if stage == 'stage4':
        handoff.update(binding=binding, candidate_commit='c' * 40, merge_commit='d' * 40, cleanup={'worktree_removed': True, 'branch_removed': True}, ancestor_verified=True, changed_paths=['a', 'README.md'])
    if mode == 'wrong-role': handoff['control_checkpoint']['role_kind'] = 'execution-agent'
    if mode == 'wrong-review' and stage == 'stage3': handoff['review']['spec']['candidate'] = 'f' * 40
    if mode == 'partial-cleanup' and stage == 'stage4': handoff['cleanup']['worktree_removed'] = False
    if mode == 'changed-closure-code' and stage == 'stage4': handoff['changed_paths'] = ['unauthorized.py']
    if mode == 'wrong-controller': handoff['controller_ref'] = 'foreign'
    result = {'result': 'completed', 'artifacts': [stage + '-artifact'], 'evidence': [stage + '-evidence'], 'handoff_json': json.dumps(handoff if mode != 'missing-handoff' else {}), 'question': '', 'message': '', 'needs_input_kind': 'none'}
    json.dump(result, open(out, 'w'))
"""


class WorkflowCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.worktree = self.root / "flow-worktree"
        self.fixture = self.root / "fake-codex"
        self.fixture.write_text(FIXTURE, encoding="utf-8")
        self.fixture.chmod(0o755)
        self.fixture_state = self.root / "fixture-state.json"
        self.record = self.root / "records" / "run.json"
        self.confirmed = self.root / "confirmed.json"
        self.confirmed.write_text(json.dumps(self.confirmed_input()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_checkpoint_short_contention_does_not_kill_carrier(self):
        result = self.invoke('start', str(self.confirmed), mode='checkpoint-lock')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        self.assertEqual(state['status'], 'needs_input')
        self.assertEqual(state['sessions'], {'stage2': 'stage2-session'})
        self.assertTrue(state['workflow_requirements']['fixture']['retained'])
        self.assertEqual(json.loads(self.fixture_state.read_text())['stage2'], 1)

    def test_checkpoint_timeout_recovers_finished_turn_without_relaunch(self):
        shim = self.root / 'shim'
        shim.mkdir()
        (shim / 'sitecustomize.py').write_text(
            "import sys\n"
            "if sys.argv and sys.argv[0].endswith('workflow.py'):\n"
            f"    sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "    import workflow_progress\n"
            "    workflow_progress.CHECKPOINT_LOCK_TIMEOUT = 0.05\n")
        locker = self.root / 'lock_holder.py'
        locker.write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "from workflow_progress import record_lock, read_record, atomic_save\n"
            "path = Path(sys.argv[1])\n"
            "with record_lock(path):\n"
            "    value = read_record(path)\n"
            "    value['workflow_requirements'] = {'fixture': {'retained': True}}\n"
            "    atomic_save(path, value)\n"
            "    Path(str(path) + '.fixture-held').write_text('held')\n"
            "    time.sleep(1)\n"
            "Path(str(path) + '.fixture-released').write_text('released')\n")
        result = subprocess.run([sys.executable, str(SCRIPT), 'start', str(self.confirmed)],
            env={**os.environ, 'CODEX_BIN': str(self.fixture), 'FIXTURE_STATE': str(self.fixture_state),
                 'FIXTURE_MODE': 'checkpoint-lock-finish', 'FIXTURE_LOCK_HOLDER': str(locker), 'PYTHONPATH': str(shim)},
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('checkpoint_write_pending', result.stderr)
        self.assertNotEqual(self.state()['status'], 'interrupted')
        receipt = self.record.parent / 'run.json.stage2.turn-1.carrier.json'
        observed = json.loads(receipt.read_text())
        self.assertEqual(observed['outcome'], 'completed_turn')
        self.assertIn({'type': 'thread.started', 'thread_id': 'stage2-session'}, observed['events'])
        deadline = time.monotonic() + 5
        while not Path(str(self.record) + '.fixture-released').exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.02)
        receipt.write_text(json.dumps({**observed, 'invocation_id': 'stale-invocation'}))
        rejected = self.invoke('resume', str(self.record))
        self.assertEqual(rejected.returncode, 1)
        self.assertIn('receipt differs', rejected.stderr)
        self.assertEqual(json.loads(self.fixture_state.read_text())['stage2'], 1)
        receipt.write_text(json.dumps(observed))
        resumed = self.invoke('resume', str(self.record))
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.state()['status'], 'needs_input')
        self.assertEqual(self.state()['sessions'], {'stage2': 'stage2-session'})
        self.assertTrue(self.state()['workflow_requirements']['fixture']['retained'])
        self.assertEqual(json.loads(self.fixture_state.read_text())['stage2'], 1)

    def confirmed_input(self) -> dict[str, object]:
        # Transport-only A boundary double. Successful business chains use real
        # A freezing and Git through test_workflow_progress, not this fixture.
        def seal(value, field="digest"):
            return {**value, field: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()}
        original_entry = seal({"protocol": "workflow-entry-v1", "repository": {"root": str(self.repository)},
            "actor": {"controller_ref": "controller"}, "entry": {"stage": 1, "action": "entry"},
            "target": {"repository": str(self.repository), "branch": "main"}, "packages": {}, "external": {},
            "configuration": {}, "requirement": {"kind": "stage1"}}, "evidence_digest")
        source = {"protocol": "requirement-freeze-v1", "kind": "frozen", "source_kind": "stage1", "commit": "a" * 40,
                  "absolute_path": "/requirements/frozen.md",
                  "entry": original_entry, "path": "requirements/frozen.md", "sha256": "b" * 64, "version": 1, "blob": "c" * 40,
                  "requirement_identity": {"path": "requirements/frozen.md", "sha256": "b" * 64, "version": 1}}
        source = seal(source)
        return {
            "registry": {"source":"host-current-skills", "entries":[
                {"name":name,"entry":str(SCRIPT.resolve().parents[2] / name / "SKILL.md"),"source":"test-host"}
                for name in ("solution-design","guided-implementation","change-closure")]},
            "controller_ref": "controller",
            "frozen_requirement": {"path": "/requirements/frozen.md", "commit": "a" * 40, "sha256": "b" * 64},
            "requirement": source,
            "repository": str(self.repository), "worktree": str(self.worktree),
            "git_common_dir": str(self.repository / ".git"), "target_branch": "main",
            "authority_scope": {"allowed_paths": ["skills"]}, "run_record": str(self.record),
            "stages": {stage: {"model": "gpt-5.6-terra", "reasoning_effort": "high",
                "selection_input": {"role": "scripted-carrier", "required_capability": 2,
                    "supported": [{"model": "gpt-5.6-terra", "effort": "high", "capability": 3, "cost": None, "permission": "unchanged", "visible_identity": "unchanged"}],
                    "user": {"model": "gpt-5.6-terra", "effort": "high"}, "frozen": None, "previous": None,
                    "receipt": "current codex CLI adapter", "can_override": True, "inherited": None, "upgrade_attempted": False}}
                for stage in ("stage2", "stage3", "stage4")},
        }

    def invoke(self, *arguments: str, mode: str = "success") -> subprocess.CompletedProcess[str]:
        # Simulate the controller selecting its actual pending matter.
        if arguments[0] == "resume" and self.record.exists():
            pending = self.state().get("pending_input")
            if pending and "--decision-id" not in arguments:
                arguments = (*arguments, "--decision-id", pending["decision_id"])
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            env={**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": mode},
            text=True, capture_output=True,
        )

    def state(self) -> dict[str, object]:
        return json.loads(self.record.read_text(encoding="utf-8"))

    def wait_for_thread_started(self, runner: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = self.state()
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            if state.get("launch", {}).get("state") == "launched" and state.get("sessions", {}).get("stage2"):
                return
            if runner.poll() is not None:
                stderr = runner.stderr.read() if runner.stderr is not None else ""
                self.fail(f"runner exited before thread.started: {stderr}")
            time.sleep(0.05)
        self.fail("runner did not persist thread.started before signal")

    def test_resume_requires_current_registration(self):
        result = self.invoke('start', str(self.confirmed), mode='needs-input')
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = self.record.read_bytes()
        counts = self.fixture_state.read_bytes()
        current = self.confirmed_input()
        current['registry']['entries'] = current['registry']['entries'][:-1]
        self.confirmed.write_text(json.dumps(current))
        rejected = self.invoke('resume', str(self.record), 'continue')
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
        self.assertIn('skill_not_active', rejected.stderr)
        self.assertEqual(self.record.read_bytes(), saved)
        self.assertEqual(self.fixture_state.read_bytes(), counts)
        self.confirmed.write_text(json.dumps(self.confirmed_input()))
        resumed = self.invoke('resume', str(self.record), 'continue')
        self.assertEqual(resumed.returncode, 1, resumed.stderr)


    def test_refreshed_registration_keeps_original_execution_identity(self):
        result = self.invoke('start', str(self.confirmed), mode='needs-input')
        self.assertEqual(result.returncode, 0, result.stderr)
        pinned = self.state()['confirmed']['packages']['stage4']
        replacement = self.root / 'compatible-closure'
        shutil.copytree(Path(pinned['entry']).parent, replacement,
                        ignore=shutil.ignore_patterns('__pycache__'))
        current = self.confirmed_input()
        current['registry']['entries'][-1]['entry'] = str(replacement / 'SKILL.md')
        self.confirmed.write_text(json.dumps(current))
        result = self.invoke('resume', str(self.record), 'continue')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.state()['confirmed']['packages']['stage4'], pinned)
        counts = json.loads(self.fixture_state.read_text())
        payload = json.loads(counts['prompts']['stage2'].splitlines()[-1])
        self.assertEqual(payload['skill_paths']['stage4'], pinned['entry'])
        self.assertIn('stage acceptance failed', result.stderr)


    def test_v3_pins_packages_and_legacy_record_is_read_only(self):
        result = self.invoke("start", str(self.confirmed), mode="needs-input")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        self.assertEqual(state["version"], 3)
        self.assertEqual(set(state["confirmed"]["packages"]), {"runner","stage2","stage3","stage4"})
        state["version"] = 1
        self.record.write_text(json.dumps(state))
        before = self.record.read_bytes()
        resumed = self.invoke("resume", str(self.record), "Continue")
        self.assertNotEqual(resumed.returncode, 0)
        self.assertIn("legacy_run_requires_original_runtime", resumed.stderr)
        self.assertEqual(self.record.read_bytes(), before)

    def test_v3_requires_complete_a_evidence_before_launch(self):
        raw = self.confirmed_input()
        raw.pop("requirement")
        self.confirmed.write_text(json.dumps(raw))
        failed = self.invoke("start", str(self.confirmed))
        self.assertEqual(failed.returncode, 1)
        self.assertIn("requirement", failed.stderr)
        self.assertFalse(self.fixture_state.exists())
        self.assertFalse(self.record.exists())


    def test_unsupported_frozen_configuration_never_launches(self):
        confirmed = self.confirmed_input()
        confirmed['stages']['stage2']['selection_input']['supported'] = []
        self.confirmed.write_text(json.dumps(confirmed))
        completed = self.invoke('start', str(self.confirmed))
        self.assertEqual(completed.returncode, 1)
        self.assertFalse(self.fixture_state.exists())


    def test_foreign_controller_handoff_does_not_advance(self):
        completed = self.invoke("start", str(self.confirmed), mode="wrong-controller")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(self.fixture_state.read_text())["stage2"], 1)
        self.assertNotIn("stage3", json.loads(self.fixture_state.read_text()))

    def test_needs_input_resumes_the_same_stage_session(self) -> None:
        paused = self.invoke("start", str(self.confirmed), mode="needs-input")
        self.assertEqual(paused.returncode, 0, paused.stderr)
        self.assertEqual(self.state()["status"], "needs_input")
        self.assertEqual(self.state()["sessions"], {"stage2": "stage2-session"})
        completed = self.invoke("resume", str(self.record), "the chosen value", mode="needs-input")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(self.state()["status"], "failed")
        fixture = json.loads(self.fixture_state.read_text())
        self.assertEqual(fixture["stage2"], 2)
        self.assertEqual(fixture["commands"]["stage2"][1:3], ["exec", "resume"])
        self.assertIn("--model", fixture["commands"]["stage2"])
        self.assertIn("stage acceptance failed", self.state()["error"]["detail"])
        self.assertIn("model_reasoning_effort=high", fixture["commands"]["stage2"])

    def test_nonzero_before_session_is_uncertain_and_is_never_retried(self) -> None:
        failed = self.invoke("start", str(self.confirmed), mode="nonzero")
        self.assertEqual(failed.returncode, 1)
        state = self.state()
        self.assertEqual((state["status"], state["launch"]["state"], state["sessions"]), ("interrupted", "uncertain", {}))
        retry = self.invoke("resume", str(self.record), "try again")
        self.assertEqual(retry.returncode, 1)
        self.assertEqual(json.loads(self.fixture_state.read_text())["stage2"], 1)
        self.assertTrue(Path(self.state()["launch"]["diagnostic"]).is_file())

    def test_diagnostic_retains_the_bounded_tail_with_failure_context(self) -> None:
        failed = self.invoke("start", str(self.confirmed), mode="tail-error")
        self.assertEqual(failed.returncode, 1)
        diagnostic = Path(self.state()["launch"]["diagnostic"])
        self.assertLessEqual(diagnostic.stat().st_size, 65536)
        self.assertIn("useful final failure detail", diagnostic.read_text(encoding="utf-8"))

    def test_continue_reuses_exact_session_without_user_input(self) -> None:
        completed = self.invoke("start", str(self.confirmed), mode="continue")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        fixture = json.loads(self.fixture_state.read_text())
        self.assertEqual(fixture["stage2"], 2)
        self.assertEqual(fixture["commands"]["stage2"][1:3], ["exec", "resume"])
        self.assertEqual(self.state()["status"], "failed")

    def test_continue_or_needs_input_without_thread_identity_stops_once(self) -> None:
        for mode in ("no-session-continue", "no-session-needs-input"):
            with self.subTest(mode=mode):
                failed = self.invoke("start", str(self.confirmed), mode=mode)
                self.assertEqual(failed.returncode, 1)
                state = self.state()
                self.assertEqual((state["status"], state["sessions"]), ("interrupted", {}))
                self.assertNotIn("pending_input", state)
                fixture = json.loads(self.fixture_state.read_text())
                self.assertEqual(fixture["stage2"], 1)
                self.assertNotIn("stage3", fixture)
                self.record.unlink(); self.fixture_state.unlink()


    def test_stale_prior_turn_output_is_not_accepted_after_continue(self) -> None:
        failed = self.invoke("start", str(self.confirmed), mode="stale")
        self.assertEqual(failed.returncode, 1)
        state = self.state()
        self.assertEqual((state["status"], state["launch"]["turn"]), ("failed", 2))
        self.assertTrue((self.record.parent / "run.json.stage2.turn-1.json").is_file())
        self.assertFalse((self.record.parent / "run.json.stage2.turn-2.json").exists())

    def test_same_stem_different_record_cannot_read_another_runs_result(self) -> None:
        first = self.invoke("start", str(self.confirmed))
        self.assertEqual(first.returncode, 1, first.stderr)
        first_output = self.record.parent / "run.json.stage2.turn-1.json"
        first_bytes = first_output.read_bytes()
        second_record = self.record.parent / "run.state"
        second_confirmed = self.root / "confirmed-second.json"
        second = self.confirmed_input()
        second["run_record"] = str(second_record)
        second_confirmed.write_text(json.dumps(second), encoding="utf-8")

        failed = self.invoke("start", str(second_confirmed), mode="no-result")

        self.assertEqual(failed.returncode, 1)
        self.assertEqual(first_output.read_bytes(), first_bytes)
        self.assertEqual(self.state()["status"], "failed")
        second_state = json.loads(second_record.read_text(encoding="utf-8"))
        self.assertEqual(second_state["status"], "failed")

    def test_malformed_result_and_turn_failed_preserve_known_session(self) -> None:
        malformed = self.invoke("start", str(self.confirmed), mode="malformed")
        self.assertEqual(malformed.returncode, 1)
        self.assertEqual((self.state()["status"], self.state()["sessions"]), ("failed", {"stage2": "stage2-session"}))

    def test_completion_requires_turn_event_and_structured_handoff(self) -> None:
        no_completion = self.invoke("start", str(self.confirmed), mode="no-completion")
        self.assertEqual(no_completion.returncode, 1)
        self.assertIn("did not emit turn.completed", self.state()["error"]["detail"])
        self.record.unlink(); self.fixture_state.unlink()
        missing_handoff = self.invoke("start", str(self.confirmed), mode="missing-handoff")
        self.assertEqual(missing_handoff.returncode, 1)
        self.assertIn("handoff missing", self.state()["error"]["detail"])

    def test_technical_stage_error_stops_without_a_user_input_checkpoint(self) -> None:
        failed = self.invoke("start", str(self.confirmed), mode="technical-error")
        self.assertEqual(failed.returncode, 1)
        state = self.state()
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("pending_input", state)
        self.assertIn("Git metadata write denied", state["error"]["detail"])
        self.assertTrue(state["error"]["recoverable"])
        recovered = self.invoke("resume", str(self.record), "the Git write issue is fixed")
        self.assertEqual(recovered.returncode, 1, recovered.stderr)
        fixture = json.loads(self.fixture_state.read_text())
        self.assertEqual(fixture["stage2"], 2)
        self.assertEqual(fixture["commands"]["stage2"][1:3], ["exec", "resume"])
        self.assertEqual(self.state()["status"], "failed")
        self.record.unlink(); self.fixture_state.unlink()
        turn_failed = self.invoke("start", str(self.confirmed), mode="turn-failed")
        self.assertEqual(turn_failed.returncode, 1)
        self.assertEqual((self.state()["status"], self.state()["sessions"]), ("failed", {"stage2": "stage2-session"}))

    def test_concurrent_resume_is_rejected_without_another_executor(self) -> None:
        self.assertEqual(self.invoke("start", str(self.confirmed), mode="needs-input").returncode, 0)
        environment = {**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": "hold"}
        first = subprocess.Popen([sys.executable, str(SCRIPT), "resume", str(self.record), "answer", "--decision-id", self.state()["pending_input"]["decision_id"]], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.2)
        second = self.invoke("resume", str(self.record), "duplicate", mode="hold")
        _, first_stderr = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 1, first_stderr)
        self.assertEqual(second.returncode, 1)
        self.assertIn("run_busy", second.stderr)
        self.assertEqual(json.loads(self.fixture_state.read_text())["stage2"], 2)

    def test_cancel_command_stops_carrier_and_requires_writer_reconciliation(self):
        environment = {**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": "interrupt"}
        process = subprocess.Popen([sys.executable, str(SCRIPT), "start", str(self.confirmed)], env=environment,
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_for_thread_started(process)
            cancelled = self.invoke("cancel", str(self.record))
            self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
            process.communicate(timeout=10)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(self.state()["runner_request"]["operation"], "cancel")
            self.assertIn("stopped-writer reconciliation", self.invoke("resume", str(self.record)).stderr)
            self.assertEqual(json.loads(self.fixture_state.read_text())["stage2"], 1)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)

    def test_old_or_missing_decision_id_cannot_consume_current_question(self):
        self.assertEqual(self.invoke("start", str(self.confirmed), mode="needs-input").returncode, 0)
        before = self.record.read_bytes()
        failed = self.invoke("resume", str(self.record), "answer", "--decision-id", "old")
        self.assertEqual(failed.returncode, 1)
        self.assertIn("stale_decision", failed.stderr)
        self.assertEqual(self.record.read_bytes(), before)
        self.assertEqual(json.loads(self.fixture_state.read_text())["stage2"], 1)

    def test_interrupt_terminates_the_launched_process_group(self) -> None:
        environment = {**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": "interrupt"}
        runner = subprocess.Popen([sys.executable, str(SCRIPT), "start", str(self.confirmed)], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.wait_for_thread_started(runner)
        runner.send_signal(signal.SIGINT)
        _, stderr = runner.communicate(timeout=10)
        self.assertEqual(runner.returncode, 1, stderr)
        state = self.state()
        self.assertEqual(state["status"], "interrupted")
        pid = json.loads(self.fixture_state.read_text())["pids"]["stage2"]
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_interrupt_reaps_a_term_resistant_descendant(self) -> None:
        environment = {**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": "interrupt-child"}
        runner = subprocess.Popen([sys.executable, str(SCRIPT), "start", str(self.confirmed)], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.wait_for_thread_started(runner)
        deadline = time.monotonic() + 3
        fixture = {}
        while "children" not in fixture and time.monotonic() < deadline:
            try:
                fixture = json.loads(self.fixture_state.read_text())
            except json.JSONDecodeError:
                fixture = {}
            time.sleep(0.05)
        child = fixture["children"]["stage2"]
        runner.send_signal(signal.SIGINT)
        _, stderr = runner.communicate(timeout=10)
        self.assertEqual(runner.returncode, 1, stderr)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_sigterm_reaps_a_term_resistant_descendant_and_records_interruption(self) -> None:
        environment = {**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": "interrupt-child"}
        runner = subprocess.Popen([sys.executable, str(SCRIPT), "start", str(self.confirmed)], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.wait_for_thread_started(runner)
        deadline = time.monotonic() + 3
        fixture = {}
        while "children" not in fixture and time.monotonic() < deadline:
            try:
                fixture = json.loads(self.fixture_state.read_text())
            except json.JSONDecodeError:
                fixture = {}
            time.sleep(0.05)
        child = fixture["children"]["stage2"]
        runner.send_signal(signal.SIGTERM)
        _, stderr = runner.communicate(timeout=10)
        self.assertEqual(runner.returncode, 1, stderr)
        state = self.state()
        self.assertEqual((state["status"], state["launch"]["state"]), ("interrupted", "uncertain"))
        self.assertEqual(state["error"]["code"], "interrupted")
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_known_session_cleanup_uncertainty_is_persisted_as_interrupted(self) -> None:
        shim = self.root / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            "import os, signal, sys\n"
            "if sys.argv and sys.argv[0].endswith('workflow.py'):\n"
            "    original = os.killpg\n"
            "    def killpg(group, value):\n"
            "        if value == signal.SIGKILL:\n"
            "            return None\n"
            "        return original(group, value)\n"
            "    os.killpg = killpg\n",
            encoding="utf-8",
        )
        environment = {
            **os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state),
            "FIXTURE_MODE": "interrupt-child", "PYTHONPATH": str(shim),
        }
        runner = subprocess.Popen([sys.executable, str(SCRIPT), "start", str(self.confirmed)], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.wait_for_thread_started(runner)
        fixture = json.loads(self.fixture_state.read_text())
        child = fixture["children"]["stage2"]
        try:
            runner.send_signal(signal.SIGTERM)
            self.assertEqual(runner.wait(timeout=15), 1)
            state = self.state()
            self.assertEqual((state["status"], state["launch"]["state"]), ("interrupted", "uncertain"))
            self.assertEqual(state["error"]["code"], "interrupted")
            self.assertIn("diagnostics:", state["error"]["detail"])
        finally:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if runner.stdout is not None:
                runner.stdout.close()
            if runner.stderr is not None:
                runner.stderr.close()



if __name__ == "__main__":
    unittest.main()
