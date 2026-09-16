from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).with_name("workflow.py")
FIXTURE = """#!/usr/bin/env python3
import json, os, signal, subprocess, sys, time
prompt = sys.argv[-1]
stage = json.loads(prompt.rsplit('\\n', 1)[1])['stage']
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
    time.sleep(10)
print(json.dumps({'type': 'thread.started', 'thread_id': stage + '-session'}))
if mode == 'turn-failed':
    print(json.dumps({'type': 'turn.failed'})); sys.exit(0)
if mode != 'no-completion': print(json.dumps({'type': 'turn.completed'}))
if mode == 'malformed': open(out, 'w').write('{')
elif mode == 'needs-input' and stage == 'stage2' and not resume:
    json.dump({'result': 'needs_input', 'artifacts': [], 'evidence': [], 'handoff_json': '{}', 'question': 'choose a value', 'message': '', 'needs_input_kind': 'user_decision'}, open(out, 'w'))
elif mode == 'technical-error':
    json.dump({'result': 'needs_input', 'artifacts': [], 'evidence': [], 'handoff_json': '{}', 'question': 'Git metadata write denied', 'message': '', 'needs_input_kind': 'technical_error'}, open(out, 'w'))
else:
    if mode == 'hold' and resume: time.sleep(1)
    if mode in ('continue', 'stale') and stage == 'stage2' and state[stage] == 1:
        json.dump({'result': 'continue', 'artifacts': [], 'evidence': [], 'handoff_json': '{}', 'question': '', 'message': 'remaining work', 'needs_input_kind': 'none'}, open(out, 'w'))
        sys.exit(0)
    if mode == 'stale' and stage == 'stage2': sys.exit(0)
    binding = {'base_commit': 'a' * 40, 'branch': 'codex/flow', 'git_common_dir': '/repo/.git', 'repository': '/repo', 'target_branch': 'main', 'worktree': '/flow'}
    handoff = ({'binding': binding, 'planning_commit': 'b' * 40, 'allowed_paths': ['a'], 'protected_paths': []} if stage == 'stage2' else ({'binding': binding, 'candidate_commit': 'c' * 40, 'review': {'standards': 'accepted', 'spec': 'accepted'}, 'verification': ['full']} if stage == 'stage3' else {}))
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

    def confirmed_input(self) -> dict[str, object]:
        return {
            "frozen_requirement": {"path": "/requirements/frozen.md", "commit": "a" * 40, "sha256": "b" * 64},
            "repository": str(self.repository), "worktree": str(self.worktree),
            "git_common_dir": str(self.repository / ".git"), "target_branch": "main",
            "authority_scope": {"allowed_paths": ["skills"]}, "run_record": str(self.record),
            "stages": {stage: {"model": "gpt-5.6-terra", "reasoning_effort": "high"} for stage in ("stage2", "stage3", "stage4")},
        }

    def invoke(self, *arguments: str, mode: str = "success") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            env={**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": mode},
            text=True, capture_output=True,
        )

    def state(self) -> dict[str, object]:
        return json.loads(self.record.read_text(encoding="utf-8"))

    def test_start_runs_the_three_stages_and_forwards_artifacts(self) -> None:
        completed = self.invoke("start", str(self.confirmed))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        state = self.state()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["sessions"], {stage: f"{stage}-session" for stage in ("stage2", "stage3", "stage4")})
        self.assertEqual(state["stage_results"]["stage2"]["artifacts"], ["stage2-artifact"])
        self.assertTrue(all(self.record.parent.glob("run.stage*.turn-1.json")))
        fixture = json.loads(self.fixture_state.read_text())
        self.assertIn("explicit CLI Stage-2 carrier", fixture["prompts"]["stage2"])
        self.assertIn("independent Standards and Spec review roles", fixture["prompts"]["stage3"])
        self.assertIn("stage2-artifact", fixture["prompts"]["stage3"])
        self.assertIn('"flow_mode": "continuous_stage2_to_4"', fixture["prompts"]["stage2"])
        self.assertNotIn("--sandbox", fixture["commands"]["stage2"])

    def test_needs_input_resumes_the_same_stage_session(self) -> None:
        paused = self.invoke("start", str(self.confirmed), mode="needs-input")
        self.assertEqual(paused.returncode, 0, paused.stderr)
        self.assertEqual(self.state()["status"], "needs_input")
        self.assertEqual(self.state()["sessions"], {"stage2": "stage2-session"})
        completed = self.invoke("resume", str(self.record), "the chosen value", mode="needs-input")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.state()["status"], "completed")
        fixture = json.loads(self.fixture_state.read_text())
        self.assertEqual(fixture["stage2"], 2)
        self.assertEqual(fixture["commands"]["stage2"][1:3], ["exec", "resume"])
        self.assertIn("--model", fixture["commands"]["stage2"])
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
        self.assertEqual(completed.returncode, 0, completed.stderr)
        fixture = json.loads(self.fixture_state.read_text())
        self.assertEqual(fixture["stage2"], 2)
        self.assertEqual(fixture["commands"]["stage2"][1:3], ["exec", "resume"])
        self.assertEqual(self.state()["status"], "completed")

    def test_stale_prior_turn_output_is_not_accepted_after_continue(self) -> None:
        failed = self.invoke("start", str(self.confirmed), mode="stale")
        self.assertEqual(failed.returncode, 1)
        state = self.state()
        self.assertEqual((state["status"], state["launch"]["turn"]), ("failed", 2))
        self.assertTrue((self.record.parent / "run.stage2.turn-1.json").is_file())
        self.assertFalse((self.record.parent / "run.stage2.turn-2.json").exists())

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
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        fixture = json.loads(self.fixture_state.read_text())
        self.assertEqual(fixture["stage2"], 2)
        self.assertEqual(fixture["commands"]["stage2"][1:3], ["exec", "resume"])
        self.assertEqual(self.state()["status"], "completed")
        self.record.unlink(); self.fixture_state.unlink()
        turn_failed = self.invoke("start", str(self.confirmed), mode="turn-failed")
        self.assertEqual(turn_failed.returncode, 1)
        self.assertEqual((self.state()["status"], self.state()["sessions"]), ("failed", {"stage2": "stage2-session"}))

    def test_concurrent_resume_is_rejected_without_another_executor(self) -> None:
        self.assertEqual(self.invoke("start", str(self.confirmed), mode="needs-input").returncode, 0)
        environment = {**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": "hold"}
        first = subprocess.Popen([sys.executable, str(SCRIPT), "resume", str(self.record), "answer"], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.2)
        second = self.invoke("resume", str(self.record), "duplicate", mode="hold")
        _, first_stderr = first.communicate(timeout=5)
        self.assertEqual(first.returncode, 0, first_stderr)
        self.assertEqual(second.returncode, 1)
        self.assertIn("run_busy", second.stderr)
        self.assertEqual(json.loads(self.fixture_state.read_text())["stage2"], 2)

    def test_interrupt_terminates_the_launched_process_group(self) -> None:
        environment = {**os.environ, "CODEX_BIN": str(self.fixture), "FIXTURE_STATE": str(self.fixture_state), "FIXTURE_MODE": "interrupt"}
        runner = subprocess.Popen([sys.executable, str(SCRIPT), "start", str(self.confirmed)], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 3
        while not self.fixture_state.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.fixture_state.exists())
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
        deadline = time.monotonic() + 3
        while not self.fixture_state.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        deadline = time.monotonic() + 3
        while "children" not in json.loads(self.fixture_state.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        child = json.loads(self.fixture_state.read_text())["children"]["stage2"]
        runner.send_signal(signal.SIGINT)
        _, stderr = runner.communicate(timeout=10)
        self.assertEqual(runner.returncode, 1, stderr)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_bad_input_does_not_create_record_and_safe_checkpoint_restarts(self) -> None:
        bad = self.confirmed_input(); bad["run_record"] = str(self.repository / "run.json")
        self.confirmed.write_text(json.dumps(bad), encoding="utf-8")
        self.assertEqual(self.invoke("start", str(self.confirmed)).returncode, 1)
        self.assertFalse((self.repository / "run.json").exists())
        valid = self.confirmed_input(); self.record.parent.mkdir()
        self.record.write_text(json.dumps({
            "version": 1, "confirmed": valid, "status": "active", "current_stage": "stage3", "sessions": {"stage2": "stage2-session"},
            "stage_results": {"stage2": {"result": "completed", "artifacts": ["a"], "evidence": ["e"], "handoff": {"binding": {"base_commit": "a" * 40, "branch": "codex/flow", "git_common_dir": "/repo/.git", "repository": "/repo", "target_branch": "main", "worktree": "/flow"}, "planning_commit": "b" * 40, "allowed_paths": ["a"], "protected_paths": []}}},
            "launch": {"stage": "stage3", "state": "prelaunch", "turn": 0}, "history": [],
        }), encoding="utf-8")
        completed = self.invoke("resume", str(self.record), "continue from checkpoint")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.state()["status"], "completed")
        self.assertNotIn("stage2", json.loads(self.fixture_state.read_text()))


if __name__ == "__main__":
    unittest.main()
