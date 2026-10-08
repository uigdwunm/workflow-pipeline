"""Runner boundary checks for the current sealed Stage-2 scope."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "src/stages/guided-implementation/scripts/workflow.py"
sys.path.insert(0, str(SCRIPT.parents[3] / "shared/scripts"))
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("stage_scope_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)


class StageScopeRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.record = Path(temporary.name) / "run.json"
        self.accepted = {"digest": "accepted-planning"}
        self.current = {
            "stage": 2, "status": "accepted", "phase_complete": True,
            "accepted": self.accepted,
            "scope_authority": {"scope": {"downstream": {
                "state": "sealed", "implementation_required": False,
            }}},
        }

    def test_runner_ten_rejects_original_runner_nine_record(self):
        for version in (9, 10):
            with self.subTest(previous_runner=version):
                with self.assertRaisesRegex(runner.WorkflowError, "legacy_run_requires_original_runtime"):
                    runner._validate_record({"version": version, "confirmed": {"packages": {}}})
                self.record.write_text(json.dumps({"version": version}))
                with self.assertRaisesRegex(runner.WorkflowError, "legacy_run_requires_original_runtime"):
                    runner._request_control(self.record, "pause", live=False)
        self.assertEqual(runner._new_state({})["version"], 11)

    def test_runner_rejects_removed_recovery_source(self):
        with self.assertRaisesRegex(runner.WorkflowError, "unsupported run record recovery_source"):
            runner._validate_record({"version": 11, "recovery_source": {}})

    def test_stage_two_projection_uses_current_readiness(self):
        readiness = {"accepted_digest": "accepted-planning", "scope_revision": 2}
        exact = {"result": "completed", "handoff_json": "{}"}
        with patch.object(runner.progression, "scope_readiness", return_value=readiness), \
             patch.object(runner.stage_handoff, "render", return_value={"stage_result": exact}) as render:
            self.assertEqual(runner._stage_projection(self.record, "stage2", self.current), exact)
        render.assert_called_once_with(self.accepted, readiness=readiness, checkpoint_path=None)

    def test_unready_required_implementation_does_not_project_handoff(self):
        self.current["scope_authority"]["scope"]["downstream"]["implementation_required"] = True
        with patch.object(runner.progression, "scope_readiness", return_value=None), \
             patch.object(runner.stage_handoff, "render") as render:
            with self.assertRaisesRegex(runner.WorkflowError, "scope_incomplete"):
                runner._stage_projection(self.record, "stage2", self.current)
        render.assert_not_called()

    def test_planning_only_projection_waits_for_scope_transaction(self):
        self.current["transaction"] = {"operation": "scope-revise"}
        with patch.object(runner.progression, "scope_readiness", return_value=None), \
             patch.object(runner.stage_handoff, "render") as render:
            with self.assertRaisesRegex(runner.WorkflowError, "scope_incomplete"):
                runner._stage_projection(self.record, "stage2", self.current)
        render.assert_not_called()

    def test_planning_only_ends_without_stage_three_launch(self):
        exact = {"result": "completed", "artifacts": ["Spec.md"], "evidence": ["accepted"],
                 "handoff_json": "{}", "question": "", "message": "", "needs_input_kind": "none"}
        state = runner.CheckpointState({
            "current_stage": "stage2", "confirmed": {"flow_mode": "continuous"},
            "stage_results": {}, "launch": {"state": "completed_turn"},
            "status": "active", "history": [],
        })
        saved = {runner.progression.KEY: self.current}
        with patch.object(runner.progression, "read_record", return_value=saved), \
             patch.object(runner.progression, "scope_readiness", return_value=None), \
             patch.object(runner, "_stage_projection", return_value=exact), \
             patch.object(runner, "_accepted_stage", return_value=self.accepted), \
             patch.object(runner, "_validate_handoff_continuity"), \
             patch.object(runner, "_atomic_save"), \
             patch.object(runner, "_invoke") as invoke:
            self.assertEqual(runner._advance_in_host(state, self.record, None, None), 0)
        self.assertEqual(state["status"], "planning_only")
        self.assertEqual(state["current_stage"], "stage2")
        self.assertEqual(state["stage_results"]["stage2"]["result"], "completed")
        invoke.assert_not_called()

    def test_stage_three_prompt_rejects_stale_stage_two_authority(self):
        confirmed = {"run_record": str(self.record)}
        state = {"confirmed": confirmed, "stage_results": {}}
        with patch.object(runner.progression, "read_record", return_value={runner.progression.KEY: self.current}), \
             patch.object(runner.progression, "scope_readiness", return_value=None):
            with self.assertRaisesRegex(runner.WorkflowError, "scope_incomplete"):
                runner._stage_prompt(state, "stage3", None, False)

    def test_stage_three_prompt_uses_current_authority_not_confirmed_ceiling(self):
        authority = {"state": "sealed", "revision": 2, "scope": {"implementation_paths": ["src/feature.py"]}}
        self.current["scope_authority"] = authority
        readiness = {"scope_revision": 2, "scope_authority": authority}
        confirmed = {
            "run_record": str(self.record), "controller_ref": "controller",
            "frozen_requirement": {"path": "requirements/a.md"}, "requirement": {},
            "repository": "/repo", "worktree": "/flow", "git_common_dir": "/repo/.git",
            "target_branch": "main", "authority_scope": {"allowed_paths": None},
            "stages": {"stage3": {"model": "fixture"}}, "flow_mode": "continuous",
            "packages": {stage: {"entry": "/fixture/" + stage} for stage in runner.STAGES},
            "registry_input": "/registry.json", "registration_context": {},
        }
        with patch.object(runner.progression, "read_record", return_value={runner.progression.KEY: self.current}), \
             patch.object(runner.progression, "scope_readiness", return_value=readiness):
            prompt = runner._stage_prompt({"confirmed": confirmed, "stage_results": {}}, "stage3", None, False)
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        self.assertIsNone(payload["authority_scope"])
        self.assertEqual(payload["current_scope_authority"], authority)
        self.assertEqual(payload["stage3_readiness"], readiness)

    def test_stage_three_continuation_requires_consumed_readiness(self):
        current = {"stage": 3, "scope_authority": {"state": "sealed"}}
        state = {"confirmed": {"run_record": str(self.record)}, "stage_results": {}}
        with patch.object(runner.progression, "read_record", return_value={runner.progression.KEY: current}), \
             patch.object(runner.progression, "consumed_scope_readiness", return_value=None):
            with self.assertRaisesRegex(runner.WorkflowError, "consumed readiness"):
                runner._stage_prompt(state, "stage3", None, True)

if __name__ == "__main__":
    unittest.main()
