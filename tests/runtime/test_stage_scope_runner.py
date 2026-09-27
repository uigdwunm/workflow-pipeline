"""Runner boundary checks for the current sealed Stage-2 scope."""

from __future__ import annotations

import importlib.util
import base64
import hashlib
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

    def test_runner_eight_rejects_original_runner_seven_record(self):
        with self.assertRaisesRegex(runner.WorkflowError, "legacy_run_requires_original_runtime"):
            runner._validate_record({"version": 7, "confirmed": {"packages": {}}})
        self.assertEqual(runner._new_state({})["version"], 8)
        self.record.write_text(json.dumps({"version": 7}))
        with self.assertRaisesRegex(runner.WorkflowError, "legacy_run_requires_original_runtime"):
            runner._request_control(self.record, "pause", live=False)

    def test_recovered_runner_does_not_read_old_host_receipts(self):
        old_log = self.record.with_name(self.record.name + ".host.events.jsonl")
        old_log.write_bytes(b"old outer-six receipt\n")
        state = runner.CheckpointState({"recovery_source": {"recovery_id": "legacy-1",
            "original_checkpoint_sha256": "a" * 64}, "launch": {"state": "prelaunch"}})
        new_log = runner._host_events_path(self.record, state)
        self.assertNotEqual(new_log, old_log)
        self.assertNotEqual(runner._host_stderr_path(self.record, state),
                            self.record.with_name(self.record.name + ".host.stderr"))
        self.assertFalse(new_log.exists())
        runner._recover_transport(state, self.record)
        self.assertNotIn("transport", state)

    def test_pending_legacy_takeover_has_no_executor_or_old_session(self):
        recovery_source = {"recovery_id": "legacy-1", "original_checkpoint_sha256": "a" * 64}
        outer = {"version": 8, "confirmed": {"run_record": str(self.record.resolve())},
                 "status": "recovery_pending", "current_stage": "stage3", "sessions": {},
                 "stage_results": {}, "launch": {"stage": "stage3", "state": "prelaunch", "turn": 0},
                 "recovery_source": recovery_source}
        proof = {**recovery_source, "state": "takeover-pending", "binding": {}}
        with patch.object(runner, "validate_confirmed", return_value=outer["confirmed"]), \
             patch.object(runner.progression, "verify_legacy_recovery", return_value=proof, create=True):
            self.assertEqual(runner._validate_record(outer)["status"], "recovery_pending")
            outer["sessions"] = {"stage2": "old-cli"}
            with self.assertRaisesRegex(runner.WorkflowError, "invalid recovered runner"):
                runner._validate_record(outer)

    def test_pending_recovery_uses_real_c_provenance_verifier(self):
        old_accepted = {"digest": "old-stage-two"}
        original = {"version": 6, runner.progression.KEY: {"accepted": old_accepted,
                    "action": {"checkpoint": str(self.record.resolve())}}}
        raw = json.dumps(original, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        authority = {"state": "sealed"}
        binding = {"repository": "/repo", "worktree": "/flow"}
        bridge = {"legacy_accepted": old_accepted, "original_snapshot_sha256": digest,
                  "scope_authority": authority, "binding": binding}
        bridge["digest"] = runner.entry_prepare.digest(bridge)
        source = {"recovery_id": "legacy-1", "original_checkpoint_sha256": digest}
        recovery = {**source, "state": "takeover-pending",
            "original_checkpoint_path": str(self.record.resolve()),
            "original_bytes_base64": base64.b64encode(raw).decode(),
            "original_accepted": old_accepted, "original_accepted_digest": old_accepted["digest"],
            "bridge": bridge, "supplemental_authority": authority}
        outer = {"version": 8, "status": "recovery_pending", "recovery_source": source,
                 runner.progression.KEY: {"protocol": runner.progression.PROTOCOL,
                     "checkpoint_path": str(self.record.resolve()),
                     "recovery_mode": "legacy-takeover", "stage": 2,
                     "accepted": bridge, "scope_authority": authority,
                     "scope_recoveries": {"legacy-1": recovery}}}
        self.record.write_text(json.dumps(outer))
        proof = runner._recovery_provenance(outer)
        self.assertEqual(proof["state"], "takeover-pending")
        self.assertEqual(proof["binding"], binding)
        self.assertEqual(proof["accepted_digest"], bridge["digest"])
        copied = self.record.with_name("copied-run.json")
        copied.write_text(json.dumps(outer))
        with self.assertRaisesRegex(runner.WorkflowError, "copied recovery"):
            runner._recovery_provenance(outer, copied)

    def test_pending_legacy_takeover_resume_never_starts_host(self):
        source = {"recovery_id": "legacy-1", "original_checkpoint_sha256": "a" * 64}
        outer = {"version": 8, "confirmed": {"run_record": str(self.record.resolve())},
                 "status": "recovery_pending", "current_stage": "stage3", "sessions": {},
                 "stage_results": {}, "launch": {"stage": "stage3", "state": "prelaunch", "turn": 0},
                 "recovery_source": source,
                 runner.progression.KEY: {"checkpoint_path": str(self.record.resolve())}}
        self.record.write_text(json.dumps(outer))
        with patch.object(runner, "_validate_record", side_effect=lambda value, **_: value), \
             patch.object(runner, "_recover_transport") as transport, \
             patch.object(runner, "_advance") as advance:
            self.assertEqual(runner._resume_owned(self.record), 0)
        transport.assert_not_called()
        advance.assert_not_called()

    def test_copied_recovery_checkpoint_cannot_resume(self):
        source = {"recovery_id": "legacy-1", "original_checkpoint_sha256": "a" * 64}
        original_path = self.record.resolve()
        copied = original_path.with_name("copied-run.json")
        outer = {"version": 8, "confirmed": {"run_record": str(original_path)},
                 "status": "recovery_pending", "current_stage": "stage3", "sessions": {},
                 "stage_results": {}, "launch": {"stage": "stage3", "state": "prelaunch", "turn": 0},
                 "recovery_source": source,
                 runner.progression.KEY: {"checkpoint_path": str(original_path)}}
        copied.write_text(json.dumps(outer))
        with patch.object(runner, "_validate_record", side_effect=lambda value, **_: value), \
             patch.object(runner, "_advance") as advance:
            with self.assertRaisesRegex(runner.WorkflowError, "checkpoint_identity_changed"):
                runner._resume_owned(copied)
        advance.assert_not_called()

    def test_recovered_stage_three_binding_uses_verified_bridge(self):
        binding = {"repository": "/repo", "worktree": "/flow", "git_common_dir": "/repo/.git",
                   "target_branch": "main"}
        result = {"handoff": {"controller_ref": "controller", "binding": binding}}
        confirmed = {**binding, "controller_ref": "controller"}
        runner._validate_handoff_continuity("stage3", result, confirmed, None,
            {"state": "consumed", "binding": binding})
        with self.assertRaisesRegex(runner.WorkflowError, "prior stage2"):
            runner._validate_handoff_continuity("stage3", result, confirmed, None, None)

    def test_recovered_stage_three_prompt_uses_supplemental_readiness(self):
        source = {"recovery_id": "legacy-1", "original_checkpoint_sha256": "a" * 64}
        current = {"stage": 2, "recovery_mode": "legacy-takeover"}
        confirmed = {
            "run_record": str(self.record), "controller_ref": "controller",
            "frozen_requirement": {}, "requirement": {}, "repository": "/repo",
            "worktree": "/flow", "git_common_dir": "/repo/.git",
            "target_branch": "main", "authority_scope": {"allowed_paths": None},
            "stages": {"stage3": {"model": "fixture"}}, "flow_mode": "continuous",
            "packages": {stage: {"entry": "/fixture/" + stage} for stage in runner.STAGES},
            "registry_input": "/registry.json", "registration_context": {},
        }
        readiness = {"legacy_recovery_id": "legacy-1", "scope_revision": 1}
        with patch.object(runner.progression, "read_record", return_value={runner.progression.KEY: current}), \
             patch.object(runner.progression, "legacy_scope_readiness", return_value=readiness, create=True) as legacy, \
             patch.object(runner.progression, "scope_readiness") as ordinary:
            prompt = runner._stage_prompt({"confirmed": confirmed, "stage_results": {},
                                           "recovery_source": source}, "stage3", None, False)
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        self.assertEqual(payload["stage3_readiness"], readiness)
        self.assertEqual(payload["legacy_recovery"], {"checkpoint": str(self.record), "field": "recovery_source"})
        legacy.assert_called_once()
        ordinary.assert_not_called()

    def test_final_completion_accepts_verified_recovery_without_old_stage_two_result(self):
        source = {"recovery_id": "legacy-1", "original_checkpoint_sha256": "a" * 64}
        exact = {"result": "completed", "handoff_json": "{}"}
        state = {"stage_results": {"stage3": {"result": "completed"}, "stage4": exact},
                 "recovery_source": source}
        outer = {runner.progression.KEY: {"accepted": {}}}
        with patch.object(runner.progression, "read_record", return_value=outer), \
             patch.object(runner, "_recovery_provenance", return_value={"state": "consumed"}), \
             patch.object(runner.progression, "validate_state"), \
             patch.object(runner.progression, "Progress") as progress, \
             patch.object(runner.progression, "verify_completion", return_value={"verified": True}), \
             patch.object(runner.stage_handoff, "render", return_value={"stage_result": exact}):
            progress.return_value.stop_barrier_pending.return_value = False
            self.assertEqual(runner._verify_run_completion(self.record, state), {"verified": True})

    def test_stage_two_projection_uses_current_readiness(self):
        readiness = {"accepted_digest": "accepted-planning", "scope_revision": 2}
        exact = {"result": "completed", "handoff_json": "{}"}
        with patch.object(runner.progression, "scope_readiness", return_value=readiness), \
             patch.object(runner.stage_handoff, "render", return_value={"stage_result": exact}) as render:
            self.assertEqual(runner._stage_projection(self.record, "stage2", self.current), exact)
        render.assert_called_once_with(self.accepted, readiness=readiness)

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

    def test_authorized_stage_three_revision_runs_control_only_activation(self):
        revision = {"state": "authorized", "input": {"revision_id": "rev-2"}}
        current = {"stage": 3, "status": "active", "scope_revision_transaction": revision}
        saved = {runner.progression.KEY: current}
        state = runner.CheckpointState({
            "current_stage": "stage3", "confirmed": {"flow_mode": "continuous"},
            "stage_results": {}, "sessions": {}, "launch": {"state": "completed_turn"},
            "status": "active", "history": [], "turn_result": {"result": "completed", "handoff_json": "{}"},
        })
        self.assertEqual(runner._runner_route(saved, state), "scope_revision_activation")
        with patch.object(runner.progression, "read_record", return_value=saved), \
             patch.object(runner, "_atomic_save"), \
             patch.object(runner, "_invoke", side_effect=RuntimeError("control turn issued")) as invoke:
            with self.assertRaisesRegex(RuntimeError, "control turn issued"):
                runner._advance_in_host(state, self.record, None, None)
        self.assertTrue(invoke.call_args.kwargs["control_turn"])
        self.assertEqual(state["current_stage"], "stage3")
        self.assertEqual(state["revision_displaced_results"][0]["result"], "completed")

    def test_stopping_revision_only_allows_control_turn(self):
        current = {"stage": 3, "status": "active", "scope_revision_transaction": {
            "state": "stopping", "input": {"revision_id": "rev-2"}}}
        self.assertEqual(runner._runner_route({runner.progression.KEY: current},
            {"current_stage": "stage3"}), "scope_revision_control")

    def test_user_pause_overrides_authorized_revision_activation(self):
        current = {"stage": 3, "status": "paused", "stop_requested": "pausing",
                   "scope_revision_transaction": {"state": "authorized",
                       "input": {"revision_id": "rev-2"}}}
        self.assertEqual(runner._runner_route({runner.progression.KEY: current},
            {"current_stage": "stage3"}), "stop")

    def test_authorized_revision_prompt_carries_new_readiness(self):
        authority = {"state": "sealed"}
        current = {"stage": 3, "scope_authority": authority,
                   "scope_revision_transaction": {"state": "authorized",
                       "input": {"revision_id": "rev-2"}}}
        confirmed = {
            "run_record": str(self.record), "controller_ref": "controller",
            "frozen_requirement": {}, "requirement": {}, "repository": "/repo",
            "worktree": "/flow", "git_common_dir": "/repo/.git",
            "target_branch": "main", "authority_scope": {"allowed_paths": None},
            "stages": {"stage3": {"model": "fixture"}}, "flow_mode": "continuous",
            "packages": {stage: {"entry": "/fixture/" + stage} for stage in runner.STAGES},
            "registry_input": "/registry.json", "registration_context": {},
        }
        readiness = {"scope_revision": 2, "revision_transaction_ref": "seal"}
        with patch.object(runner.progression, "read_record", return_value={runner.progression.KEY: current}), \
             patch.object(runner.progression, "revision_scope_readiness", return_value=readiness):
            prompt = runner._stage_prompt({"confirmed": confirmed, "stage_results": {}}, "stage3", None, True)
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        self.assertEqual(payload["stage3_readiness"], readiness)
        self.assertEqual(payload["scope_revision"], {"state": "authorized", "revision_id": "rev-2"})
        self.assertIn("activate-scope-revision", prompt)
        current["scope_revision_transaction"]["state"] = "active"
        current["consumed_readiness"] = readiness
        with patch.object(runner.progression, "read_record", return_value={runner.progression.KEY: current}), \
             patch.object(runner.progression, "revision_scope_readiness", return_value=readiness):
            revised_prompt = runner._stage_prompt({"confirmed": confirmed, "stage_results": {}}, "stage3", None, True)
        self.assertEqual(json.loads(revised_prompt.rsplit("\n", 1)[1])["stage3_readiness"], readiness)

if __name__ == "__main__":
    unittest.main()
