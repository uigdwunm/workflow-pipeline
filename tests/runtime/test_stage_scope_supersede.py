"""Unconsumed legacy scope supersession keeps one current authority."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_scope_legacy as legacy
import workflow_progress as progress
import entry_prepare as entry
import stage_scope
import test_stage_scope_attached_legacy as attached
import discussion_protocol as discussion
from discussion_core.state import _load_records


class SupersedeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        legacy.LegacyScopeTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        legacy.LegacyScopeTests.doClassCleanups()

    def setUp(self):
        self.fixture = legacy.LegacyScopeTests(methodName="test_fixture_is_exact_old_runtime_with_real_git_and_package")
        self.fixture.setUp()

    def supersede_data(self, prepared, record):
        data = copy.deepcopy(prepared)
        data["action"] = "supersede"
        data["previous_takeover_transaction_id"] = record["takeover_transaction_id"]
        data["previous_scope_seal_ref"] = record["supplemental_authority"]["seal_ref"]
        data["decision"]["reference"] = "controller:supersede-missing-test"
        basis = data["manifest"][-1]["basis"]
        data["manifest"].append({"path": "tests/test_impl.py", "owner": "implementation",
            "kind": "test", "operation": "add", "basis": copy.deepcopy(basis)})
        data["manifest"] = sorted(data["manifest"], key=lambda item: item["path"])
        return data

    def test_standalone_supersede_retains_original_and_prior_authority(self):
        _, prepared, prior = self.fixture.authorized_standalone()
        original = self.fixture.checkpoint.read_bytes()
        data = self.supersede_data(prepared, prior)
        result = self.fixture.recover(data)
        self.assertEqual(result["state"], "takeover-pending", result)
        current = progress.read_record(self.fixture.checkpoint)
        record = current[progress.KEY]["scope_recoveries"][data["recovery_id"]]
        self.assertEqual(record["original_bytes_base64"], prior["original_bytes_base64"])
        self.assertEqual(record["original_accepted"], prior["original_accepted"])
        self.assertEqual(record["superseded"][0]["bridge"], prior["bridge"])
        self.assertEqual(record["superseded"][0]["supplemental_authority"], prior["supplemental_authority"])
        self.assertEqual(record["supplemental_authority"]["scope"]["implementation_paths"],
                         ["impl.py", "tests/test_impl.py"])
        self.assertNotEqual(record["bridge"]["digest"], prior["bridge"]["digest"])
        proof = progress.verify_legacy_recovery(current, self.fixture.checkpoint)
        self.assertEqual(proof["state"], "takeover-pending")
        after = self.fixture.checkpoint.read_bytes()
        self.assertTrue(self.fixture.recover(data)["acknowledged"])
        self.assertEqual(self.fixture.checkpoint.read_bytes(), after)
        activation = {"action": "activate", "recovery_id": data["recovery_id"],
            "original_checkpoint_sha256": data["original_checkpoint_sha256"],
            "controller_ref": data["controller_ref"], "host_evidence": data["host_evidence"]}
        self.assertEqual(self.fixture.recover(activation)["state"], "authorized")
        self.assertTrue(progress.legacy_scope_readiness(progress.read_record(self.fixture.checkpoint)[progress.KEY],
                                                       self.fixture.checkpoint))
        self.assertNotEqual(self.fixture.checkpoint.read_bytes(), original)

    def test_consumed_and_reserved_authorities_reject_supersede(self):
        inputs, prepared, prior = self.fixture.authorized_standalone()
        data = self.supersede_data(prepared, prior)
        current = progress.read_record(self.fixture.checkpoint)
        current[progress.KEY]["scope_recoveries"][data["recovery_id"]]["ledger"] = {
            "reservation_request": {"operation": "scope-recover-legacy-reserve"}}
        progress.atomic_save(self.fixture.checkpoint, current)
        before = self.fixture.checkpoint.read_bytes()
        with self.assertRaises(entry.PreparationError) as error:
            self.fixture.recover(data)
        self.assertEqual(error.exception.code, "legacy_recovery_consumed")
        self.assertEqual(self.fixture.checkpoint.read_bytes(), before)
        current[progress.KEY]["scope_recoveries"][data["recovery_id"]]["ledger"] = None
        progress.atomic_save(self.fixture.checkpoint, current)
        start = self.fixture.recovered_stage_three_start(prior, inputs)
        cwd, settings = self.fixture.recovered_host_patches(prior)
        with cwd, settings:
            progress.handle(self.fixture.checkpoint, start)
        before = self.fixture.checkpoint.read_bytes()
        with self.assertRaises(entry.PreparationError) as error:
            self.fixture.recover(data)
        self.assertEqual(error.exception.code, "legacy_recovery_consumed")
        self.assertEqual(self.fixture.checkpoint.read_bytes(), before)

    def test_supersede_cannot_change_retained_worktree_evidence(self):
        _, prepared, prior = self.fixture.authorized_standalone()
        data = self.supersede_data(prepared, prior)
        data["workspace_evidence"]["reference"] = "controller:different-workspace"
        before = self.fixture.checkpoint.read_bytes()
        with self.assertRaises(entry.PreparationError) as error:
            self.fixture.recover(data)
        self.assertEqual(error.exception.code, "binding_changed")
        self.assertEqual(self.fixture.checkpoint.read_bytes(), before)

    def test_unconsumed_legacy_scope_rejects_ordinary_revision_without_write(self):
        _, _, record = self.fixture.authorized_standalone()
        authority = record["supplemental_authority"]
        manifest = copy.deepcopy(authority["scope"]["downstream"]["manifest"])
        manifest.append({"path": "tests/test_impl.py", "owner": "implementation", "kind": "test",
            "operation": "add", "basis": copy.deepcopy(manifest[-1]["basis"])})
        manifest.sort(key=lambda item: item["path"])
        payload = record["bridge"]["payload"]
        data = {"revision_id": "wrong-route", "expected_scope_revision": 1,
            "previous_scope_digest": entry.digest(authority["scope"]),
            "requirement_identity": record["bridge"]["requirement_identity"],
            "binding": record["bridge"]["binding"],
            "planning_commit": payload["planning_commit"],
            "planning_merge_commit": payload["planning_merge_commit"],
            "candidate_commit": payload["planning_commit"],
            "old_manifest": authority["scope"]["downstream"]["manifest"],
            "manifest": manifest, "implementation_required": True,
            "diff": stage_scope.manifest_diff(authority["scope"]["downstream"]["manifest"], manifest),
            "reason": "missing regression test", "controller_ref": record["controller_ref"],
            "decision": {"kind": "in-requirement-refinement", "reference": "controller:revise"}}
        before = self.fixture.checkpoint.read_bytes()
        with self.assertRaises(entry.PreparationError) as error:
            progress.handle(self.fixture.checkpoint, {"protocol": progress.PROTOCOL,
                "operation": "scope-revise", "expected_revision": progress.read_record(self.fixture.checkpoint)[progress.KEY]["revision"],
                "data": data})
        self.assertEqual(error.exception.code, "legacy_supersede_required")
        self.assertEqual(self.fixture.checkpoint.read_bytes(), before)

    def test_old_runner_missing_role_pin_blocks_inspect_without_write(self):
        altered = copy.deepcopy(self.fixture.original)
        del altered["confirmed"]["packages"]["runner"]
        progress.atomic_save(self.fixture.checkpoint, altered)
        before = self.fixture.checkpoint.read_bytes()
        request = self.fixture.recovery_input()
        request["original_checkpoint_sha256"] = hashlib.sha256(before).hexdigest()
        with self.assertRaises(entry.PreparationError) as error:
            self.fixture.recover(request)
        self.assertEqual(error.exception.code, "legacy_run_requires_original_runtime")
        self.assertEqual(self.fixture.checkpoint.read_bytes(), before)


class AttachedSupersedeSeamTests(unittest.TestCase):
    """Join C's saved supersede envelope to a real disposable Topic ledger."""

    @classmethod
    def setUpClass(cls):
        legacy.LegacyScopeTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        legacy.LegacyScopeTests.doClassCleanups()

    def test_saved_c_supersede_intent_replays_one_topic_transaction(self):
        scenario = legacy.LegacyScopeTests(methodName="test_fixture_is_exact_old_runtime_with_real_git_and_package")
        scenario.setUp()
        _, prepared, old_record = scenario.authorized_standalone()
        topic = attached.AttachedLegacyScopeRecovery(methodName="runTest")
        topic.setUp()
        self.addCleanup(topic.doCleanups)
        topic.metadata.update(recovery_id=old_record["recovery_id"],
            checkpoint_path=str(scenario.checkpoint),
            original_checkpoint_sha256=old_record["original_checkpoint_sha256"],
            original_accepted_digest=old_record["original_accepted_digest"])
        topic.new["legacy_takeover"] = copy.deepcopy(topic.metadata)
        discussion.handle(topic.request_recovery())
        active = copy.deepcopy(topic.new)
        active["legacy_takeover"]["state"] = "active"
        discussion.handle(topic.request_recovery(active))
        supersede_request = topic.supersede_request()
        outer = progress.read_record(scenario.checkpoint)
        state = outer[progress.KEY]
        record = state["scope_recoveries"][old_record["recovery_id"]]
        record["state"] = "takeover-pending"
        record["ledger"] = {"pending_request": supersede_request, "pending_receipt": None,
            "active_request": None, "active_receipt": None}
        state["control"] = {"context": copy.deepcopy(supersede_request["evidence"]["new_control_context"]),
            "discussion": {key: copy.deepcopy(value) for key, value in supersede_request.items()
                if key in {"protocol_version", "project_path", "project_id", "tree_id",
                           "actor_topic_id", "actor_conversation_ref", "expected_ledger_revision",
                           "expected_topic_revision", "idempotency_key"}}}
        progress.atomic_save(scenario.checkpoint, outer)
        activation = {"action": "activate", "recovery_id": record["recovery_id"],
            "original_checkpoint_sha256": record["original_checkpoint_sha256"],
            "controller_ref": record["controller_ref"], "host_evidence": record["host_evidence"]}
        with patch.object(progress, "legacy_scope_readiness", return_value=None):
            result = scenario.recover(activation)
        self.assertEqual(result["state"], "authorized", result)
        saved = progress.read_record(scenario.checkpoint)[progress.KEY]["scope_recoveries"][record["recovery_id"]]
        self.assertEqual(saved["takeover_receipts"]["ledger_pending"]["state"], "takeover-pending")
        self.assertEqual(saved["takeover_receipts"]["ledger_active"]["state"], "takeover-active")
        _, records = _load_records(topic.ledger)
        wc = next(item for item in records["Phase Results"] if item.get("result_kind") == "workflow-control")
        self.assertEqual(wc["legacy_control_json"], topic.raw_old)
        self.assertIn("legacy_supersede_1_control_json", wc)
        ledger_before = topic.ledger.read_bytes()
        with patch.object(progress, "legacy_scope_readiness", return_value=None):
            self.assertTrue(scenario.recover(activation)["acknowledged"])
        self.assertEqual(topic.ledger.read_bytes(), ledger_before)


if __name__ == "__main__":
    unittest.main()
