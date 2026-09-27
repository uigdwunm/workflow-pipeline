"""Negative scope-revision edges through real C, B and temporary Git state."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_scope_progress as scope_fixture
import test_workflow_progress as fixture
import test_review_first_validation as review_fixture
import stage_scope


class ScopeRevisionEdgeTests(unittest.TestCase):
    def setUp(self):
        self.scenario = scope_fixture.ScopeProgressTests(methodName="runTest")
        self.scenario.setUp()
        self.addCleanup(self.scenario.doCleanups)
        self.flow = self.scenario.flow

    def active_stage_three(self, *, direct=False):
        candidate = self.scenario.planning_candidate()
        _, digest = self.scenario.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.scenario.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "controller:accept-plan"})
        design = self.flow.state()
        authority = design["scope_authority"]
        request = self.flow.input_for(3, design["accepted"])
        for key in ("downstream", "implementation_paths", "closure_paths"):
            request["scope"][key] = copy.deepcopy(authority["scope"][key])
        request["authorization"]["scope_digest"] = fixture.transfer.entry.digest(request["scope"])
        if direct:
            request["semantic"]["validation_plan"] = review_fixture.ReviewFirstTests.plan(self.flow)
            self.flow.plan = lambda: review_fixture.ReviewFirstTests.plan(self.flow)
            request["semantic"]["implementation_policy"] = review_fixture.ReviewFirstTests.direct_policy(
                self.flow, request)
        self.flow.context = self.flow.context_for(3)
        self.flow.begin("continuous", request)
        self.flow.invoke("observe", self.flow.observation(ref="native:dispatcher"))
        return candidate, design, request

    def revision(self, candidate, design, *, manifest=None, revision_id="edge-revision"):
        authority = design["scope_authority"]
        old_manifest = authority["scope"]["downstream"]["manifest"]
        if manifest is None:
            manifest = copy.deepcopy(old_manifest)
            manifest.append({"path": "tests/test_impl.py", "owner": "implementation",
                "kind": "test", "operation": "add",
                "basis": {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}})
        return {"revision_id": revision_id, "expected_scope_revision": 1,
            "previous_scope_digest": fixture.transfer.entry.digest(authority["scope"]),
            "requirement_identity": copy.deepcopy(design["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(design["handoff"]["binding"]),
            "planning_commit": candidate,
            "planning_merge_commit": design["accepted"]["payload"]["planning_merge_commit"],
            "candidate_commit": candidate, "old_manifest": copy.deepcopy(old_manifest),
            "manifest": manifest,
            "implementation_required": any(item["owner"] == "implementation" for item in manifest),
            "diff": stage_scope.manifest_diff(old_manifest, manifest),
            "reason": "Correct the approved implementation paths before work resumes.",
            "controller_ref": "task", "decision": {"kind": "in-requirement-refinement",
                "reference": "controller:revision-edge"}}

    def stop_dispatcher(self):
        stopped = self.flow.observation("stopped", "result", ref="native:dispatcher")
        stopped["receipt"]["raw"]["resumable"] = True
        return self.flow.invoke("observe", stopped)

    def test_unsettled_execution_allocation_prevents_revision_authorization(self):
        candidate, design, _ = self.active_stage_three()
        execution = self.flow.execution_input()
        execution["scope"] = copy.deepcopy(self.flow.state()["handoff"]["scope"])
        execution["authorization"]["scope_digest"] = fixture.transfer.entry.digest(execution["scope"])
        execution["binding"] = copy.deepcopy(self.flow.state()["handoff"]["binding"])
        execution["target"] = copy.deepcopy(self.flow.state()["handoff"]["target"])
        prepared = self.flow.invoke("allocation", {"allocation_id": "slice-one",
            "operation": "prepare", "handoff": execution})
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
        revision = self.revision(candidate, design)
        self.flow.invoke("scope-revise", revision)
        result = self.stop_dispatcher()
        current = self.flow.state()
        self.assertEqual(current["scope_revision_transaction"]["state"], "stopping")
        self.assertEqual(current["scope_authority"]["revision"], 1)
        self.assertNotIn("scope_revisions", current["control"]["context"]["handoff_progress"])
        self.assertNotEqual(result["next_action"]["operation"], "stage3-revision-attempt")

    def test_touched_implementation_path_cannot_be_removed_after_stop(self):
        candidate, design, _ = self.active_stage_three()
        (self.flow.flow / "impl.py").write_text("def approved_behavior():\n    return True\n")
        self.flow.flow_git("add", "impl.py")
        self.flow.flow_git("commit", "-qm", "implementation before scope correction")
        old_manifest = design["scope_authority"]["scope"]["downstream"]["manifest"]
        retained = [copy.deepcopy(item) for item in old_manifest if item["path"] != "impl.py"]
        revision = self.revision(candidate, design, manifest=retained)
        self.flow.invoke("scope-revise", revision)
        stopped = self.stop_dispatcher()
        self.assertEqual(stopped["error"]["code"], "scope_changed", stopped)
        current = self.flow.state()
        self.assertEqual(current["scope_authority"]["revision"], 1)
        self.assertEqual(current["scope_revision_transaction"]["state"], "stopping")
        self.assertEqual((self.flow.flow / "impl.py").read_text(),
                         "def approved_behavior():\n    return True\n")

    def test_touched_implementation_path_cannot_transfer_to_closure(self):
        candidate, design, _ = self.active_stage_three()
        (self.flow.flow / "impl.py").write_text("def approved_behavior():\n    return True\n")
        self.flow.flow_git("add", "impl.py")
        self.flow.flow_git("commit", "-qm", "implementation before ownership transfer")
        manifest = copy.deepcopy(design["scope_authority"]["scope"]["downstream"]["manifest"])
        next(item for item in manifest if item["path"] == "impl.py")["owner"] = "closure"
        revision = self.revision(candidate, design, manifest=manifest,
                                 revision_id="transfer-in-flight-path")
        started = self.flow.invoke("scope-revise", revision)
        self.assertEqual(started["next_action"]["operation"], "stop-host", started)
        response = self.stop_dispatcher()
        self.assertEqual(response["error"]["code"], "scope_changed", response)
        current = self.flow.state()
        self.assertEqual(current["scope_revision_transaction"]["state"], "stopping")
        self.assertEqual(current["scope_authority"]["scope"]["implementation_paths"], ["impl.py"])

    def test_running_reviewer_remains_a_writer_after_dispatcher_stops(self):
        candidate, design, _ = self.active_stage_three()
        (self.flow.flow / "impl.py").write_text("def approved_behavior():\n    return True\n")
        self.flow.flow_git("add", "impl.py")
        self.flow.flow_git("commit", "-qm", "candidate under review")
        implementation = self.flow.flow_git("rev-parse", "HEAD")
        verification = self.flow.mark_reviewable(implementation)
        actor = self.flow.state()["handoff"]["expected_entry"]["actor"]["thread_id"]
        prepared = self.flow.invoke("review-activity", {"operation": "prepare", "axis": "standards",
            "candidate": implementation, "actor_ref": actor, "verification": verification})
        action_id = prepared["next_action"]["action_id"]
        self.flow.invoke("review-activity", {"operation": "observe", "axis": "standards",
            "candidate": implementation, "actor_ref": actor, "action_id": action_id,
            "receipt": {"adapter": "fixture", "call_ref": "review-call",
                "response_ref": "review-running", "ref": "native:reviewer",
                "status": "running", "raw": {"task_name": "native:reviewer", "status": "running"}}})
        revision = self.revision(candidate, design)
        self.flow.invoke("scope-revise", revision)
        response = self.stop_dispatcher()
        self.assertEqual(self.flow.state()["scope_revision_transaction"]["state"], "stopping")
        self.assertEqual(self.flow.state()["scope_authority"]["revision"], 1)
        self.assertEqual(response["next_action"]["operation"], "reconcile-stopped-writers", response)
        self.assertTrue(any(item["kind"] == "reviewer" for item in response["next_action"]["pending"]))

    def test_started_stage_four_cannot_revise_stage_three_scope_in_place(self):
        candidate, design, _ = self.active_stage_three()
        (self.flow.flow / "impl.py").write_text("def approved_behavior():\n    return True\n")
        self.flow.flow_git("add", "impl.py")
        self.flow.flow_git("commit", "-qm", "accepted implementation")
        implementation = self.flow.flow_git("rev-parse", "HEAD")
        payload = {"artifacts": ["impl.py"], "checks": ["behavior tested"],
            "candidate_commit": implementation,
            "review": {axis: {"candidate": implementation, "reviewer_ref": axis,
                             "status": "accepted"} for axis in ("standards", "spec")},
            "verification": {"candidate": implementation, "checks": ["behavior tested"]}}
        accepted = self.flow.accept_current(payload, "native:dispatcher")
        self.assertEqual(accepted["status"], "accepted", accepted)
        predecessor = self.flow.state()["accepted"]
        self.flow.context = self.flow.context_for(4)
        self.flow.begin("continuous", self.flow.input_for(4, predecessor))
        self.assertEqual(self.flow.state()["stage"], 4)
        before = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("scope-revise", self.revision(candidate, design))
        self.assertEqual(error.exception.code, "scope_revision_unavailable")
        self.assertEqual(self.flow.checkpoint.read_bytes(), before)

    def test_old_direct_policy_cannot_authorize_revised_dispatcher(self):
        candidate, design, original = self.active_stage_three(direct=True)
        self.flow.invoke("scope-revise", self.revision(candidate, design))
        authorized = self.stop_dispatcher()
        self.assertEqual(authorized["next_action"]["operation"], "stage3-revision-attempt", authorized)
        current = self.flow.state()
        readiness = fixture.progress.revision_scope_readiness(current, self.flow.checkpoint)
        revised_scope = copy.deepcopy(current["handoff"]["scope"])
        revised_scope["baseline"] = current["scope_revision_transaction"]["snapshot"]["head"]
        for key in ("downstream", "implementation_paths", "closure_paths"):
            revised_scope[key] = copy.deepcopy(readiness["scope_authority"]["scope"][key])
        revised_scope["owned_paths"] = copy.deepcopy(revised_scope["implementation_paths"])
        request = copy.deepcopy(original)
        request["scope"] = revised_scope
        request["authorization"].update(reference="controller:revision-edge",
            flow_mode="continuous", scope_digest=fixture.transfer.entry.digest(revised_scope))
        request["readiness"] = readiness
        before = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("activate-scope-revision", {"revision_id": "edge-revision", "handoff": request})
        self.assertEqual(error.exception.code, "policy_reevaluation_required")
        self.assertEqual(self.flow.checkpoint.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
