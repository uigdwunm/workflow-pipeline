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
import importlib.util
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "skills/guided-implementation/scripts/workflow.py"
class WorkflowCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / 'repository'
        self.repository.mkdir()
        self.worktree = self.root / 'flow'
        self.record = self.root / 'run.json'
        self.confirmed = self.root / 'confirmed.json'
        sys.path.insert(0, str(SCRIPT.parent))
        spec = importlib.util.spec_from_file_location('runner_input_validation', SCRIPT)
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)

    def reject_before_launch(self, value, error):
        self.confirmed.write_text(json.dumps(value))
        with self.assertRaisesRegex(self.runner.WorkflowError, error):
            self.runner.start(self.confirmed)
        self.assertFalse(self.record.exists())

    def test_complete_a_requirement_is_required(self):
        value = self.confirmed_input()
        value.pop('requirement')
        self.reject_before_launch(value, 'missing')

    def test_unconfirmed_model_is_rejected(self):
        value = self.confirmed_input()
        value['stages']['stage2']['model'] = 'unconfirmed'
        self.reject_before_launch(value, 'configuration')

    def test_host_configuration_cannot_be_inferred(self):
        self.reject_before_launch(self.confirmed_input(), 'host_configuration_required')

    def test_profile_name_alone_cannot_prove_complete_permissions(self):
        value = self.confirmed_input()
        roots = [str(self.repository),str(self.worktree),str(self.repository / '.git')]
        value['host'] = {'transport':'app-server-stdio','cli_version':'codex-cli fixture',
            'source':{'kind':'controller-current-config','controller_ref':'controller','receipt':'fixture:profile'},
            'thread':{'approvalPolicy':'never','permissions':'custom','config':{},'runtimeWorkspaceRoots':roots},
            'effective':{'approvalPolicy':'never','sandbox':{'type':'workspaceWrite'},'runtimeWorkspaceRoots':roots,
                         'activePermissionProfile':{'id':'custom'}}}
        self.reject_before_launch(value,'unsupported_host_configuration')

    def test_record_cannot_live_in_disposable_worktree(self):
        value = self.confirmed_input()
        value['run_record'] = str(self.worktree / 'record.json')
        self.reject_before_launch(value, 'outside')

    def test_requirement_identity_cannot_drift(self):
        value = self.confirmed_input()
        value['frozen_requirement']['sha256'] = 'c' * 64
        self.reject_before_launch(value, 'requirement evidence')

    def test_missing_registered_stage_never_launches(self):
        value = self.confirmed_input()
        value['registry']['entries'] = value['registry']['entries'][:-1]
        self.reject_before_launch(value, 'skill_not_active')

    def confirmed_input(self) -> dict[str, object]:
        # Transport-only A boundary double. Successful business chains use real
        # A freezing and Git through test_workflow_progress, not this fixture.
        def seal(value, field="digest"):
            return {**value, field: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()}
        original_entry = seal({"protocol": "workflow-entry-v2", "repository": {"root": str(self.repository)},
            "actor": {"controller_ref": "controller"}, "entry": {"stage": 1, "action": "entry"},
            "target": {"repository": str(self.repository), "branch": "main"}, "packages": {}, "external": {},
            "configuration": {}, "requirement": {"kind": "stage1"}}, "evidence_digest")
        source = {"protocol": "requirement-freeze-v2", "kind": "frozen", "source_kind": "stage1", "commit": "a" * 40,
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


class BusinessRecoveryTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPT.parent))
        source = SCRIPT.parents[3] / "src/stages/guided-implementation/scripts/workflow.py"
        spec = importlib.util.spec_from_file_location("foreground_recovery_transport", source)
        cls.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.runner)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.record = Path(self.temp.name) / "run.json"
        self.decision_path = Path(self.temp.name) / "decision.json"
        self.decision = {"decision_id": "repair-1", "subject": {"block_id": "block-1"},
                         "reference": "controller-review", "diagnosis": "fixed input", "instruction": "continue",
                         "expected_progress": "produce candidate"}
        self.decision_path.write_text(json.dumps(self.decision))
        self.state = {"version": 4, "status": "failed", "current_stage": "stage3", "sessions": {"stage3": "original-cli"},
                      "stage_results": {}, "confirmed": {}, "launch": {"state": "completed_turn"},
                      self.runner.progression.KEY: {"revision": 7, "stage": 3, "status": "blocked", "business_block": {"id": "block-1"}}}
        self.record.write_text(json.dumps(self.state))

    def test_recovery_requires_persisted_exact_consumption_and_never_launches(self):
        def consume(path, request):
            self.assertEqual(request, {"protocol": self.runner.progression.PROTOCOL, "operation": "recover-business",
                                      "expected_revision": 7, "data": self.decision})
            saved = json.loads(path.read_text())
            saved[self.runner.progression.KEY].update(status="active", business_block=None,
                                                      recovery_decisions={"repair-1": self.decision})
            path.write_text(json.dumps(saved))
            return {"status": "active"}
        with patch.object(self.runner, "_validate_record", side_effect=lambda x: x), \
             patch.object(self.runner.progression, "handle", side_effect=consume), \
             patch.object(self.runner, "_invoke") as invoke:
            self.assertEqual(self.runner.recover_business(self.record, self.decision_path), 0)
        saved = json.loads(self.record.read_text())
        self.assertTrue(saved["resume_progression"])
        self.assertEqual(saved["sessions"], {"stage3": "original-cli"})
        invoke.assert_not_called()

    def test_success_response_alone_cannot_authorize_carrier_recovery(self):
        before = self.record.read_bytes()
        with patch.object(self.runner, "_validate_record", side_effect=lambda x: x), \
             patch.object(self.runner.progression, "handle", return_value={"status": "active"}):
            self.runner.recover_business(self.record, self.decision_path)
        self.assertEqual(self.record.read_bytes(), before)

    def test_consumed_replay_cannot_restart_completed_or_uncertain_carrier(self):
        for status, launch in (("completed", "completed_turn"), ("failed", "uncertain")):
            with self.subTest(status=status, launch=launch):
                self.state.update(status=status, launch={"state": launch})
                self.state[self.runner.progression.KEY].update(status="active", business_block=None,
                                                              recovery_decisions={"repair-1": self.decision})
                self.record.write_text(json.dumps(self.state))
                before = self.record.read_bytes()
                with patch.object(self.runner, "_validate_record", side_effect=lambda x: x), \
                     patch.object(self.runner.progression, "handle", return_value={"status": "active", "acknowledged": True}):
                    self.runner.recover_business(self.record, self.decision_path)
                self.assertEqual(self.record.read_bytes(), before)

    def test_paused_recovery_decision_does_not_unpause(self):
        self.state["status"] = "paused"
        self.state[self.runner.progression.KEY]["status"] = "paused"
        self.record.write_text(json.dumps(self.state))
        before = self.record.read_bytes()
        with patch.object(self.runner, "_validate_record", side_effect=lambda x: x), \
             patch.object(self.runner.progression, "handle", return_value={"status": "paused"}), \
             patch.object(self.runner, "_invoke") as invoke:
            self.runner.recover_business(self.record, self.decision_path)
        self.assertEqual(self.record.read_bytes(), before)
        invoke.assert_not_called()

    def test_ordinary_resume_keeps_business_block_and_pause(self):
        self.state["status"] = "paused"
        self.state["runner_request"] = {"operation": "pause", "request_id": "pause-1"}
        self.record.write_text(json.dumps(self.state))
        before = self.record.read_bytes()
        with patch.object(self.runner, "_validate_record", side_effect=lambda x: x), \
             patch.object(self.runner, "_recover_carrier_receipt"), \
             patch.object(self.runner, "_invoke") as invoke:
            with self.assertRaisesRegex(self.runner.WorkflowError,'await-host-recovery'):
                self.runner.resume(self.record)
        self.assertEqual(self.record.read_bytes(), before)
        invoke.assert_not_called()

    def test_authorized_business_recovery_cannot_recreate_absent_owner(self):
        self.state.update(status="active", resume_progression=True)
        self.state[self.runner.progression.KEY].update(status="active", business_block=None, stage=3)
        self.record.write_text(json.dumps(self.state))
        with patch.object(self.runner, "_validate_record", side_effect=lambda x: x), \
             patch.object(self.runner, "_recover_carrier_receipt"), \
             patch.object(self.runner, "_check_current_registry"), \
             patch.object(self.runner, "_advance", return_value=0) as advance:
            with self.assertRaisesRegex(self.runner.WorkflowError,'await-host-recovery'):
                self.runner.resume(self.record)
        advance.assert_not_called()

    def test_explicit_resume_retains_deferred_decision_after_owner_loss(self):
        self.state["status"] = "paused"
        self.state[self.runner.progression.KEY].update(status="paused", deferred_business_recovery=self.decision)
        self.record.write_text(json.dumps(self.state))
        before = self.record.read_bytes()
        with patch.object(self.runner, "_validate_record", side_effect=lambda x: x), \
             patch.object(self.runner, "_recover_carrier_receipt"), \
             patch.object(self.runner, "_check_current_registry"), \
             patch.object(self.runner, "_advance", return_value=0) as advance:
            with self.assertRaisesRegex(self.runner.WorkflowError,'await-host-recovery'):
                self.runner.resume(self.record)
        advance.assert_not_called()
        self.assertEqual(self.record.read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
