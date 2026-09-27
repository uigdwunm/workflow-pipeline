"""Stage-2 scope finalization through the durable C/B/controller boundary."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixture
import workflow_progress as progress
import stage_scope


class ScopeProgressTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixture.ProgressTests(methodName="runTest")
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        scope = self.flow.input["scope"]
        scope["implementation_paths"] = None
        scope["closure_paths"] = None
        scope["downstream"] = {"state": "unresolved", "revision": 0,
            "implementation_required": None, "manifest": None,
            "proposal_ref": None, "seal_ref": None}
        self.flow.input["authorization"]["scope_digest"] = fixture.transfer.entry.digest(scope)

    def planning_candidate(self):
        self.flow.begin("continuous")
        self.flow.invoke("observe", self.flow.observation())
        (self.flow.flow / "docs/spec.md").write_text("approved design\nImplementation: impl.py\nClosure: CHANGELOG.md\n")
        self.flow.flow_git("add", "docs/spec.md")
        self.flow.flow_git("commit", "-qm", "plan")
        return self.flow.flow_git("rev-parse", "HEAD")

    def manifest(self, candidate, *, implementation=True):
        basis = {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}
        items = [{"path": "CHANGELOG.md", "owner": "closure", "kind": "document",
                  "operation": "add", "basis": basis}]
        if implementation:
            items.append({"path": "impl.py", "owner": "implementation", "kind": "source",
                          "operation": "add", "basis": basis})
        return items

    def propose(self, candidate, *, implementation=True):
        state = self.flow.state()
        data = {"proposal_id": "proposal-1", "expected_revision": 0,
                "candidate_commit": candidate, "manifest": self.manifest(candidate, implementation=implementation),
                "implementation_required": implementation,
                "requirement_identity": copy.deepcopy(state["handoff"]["requirement_identity"]),
                "binding": copy.deepcopy(state["handoff"]["binding"]),
                "previous_scope_revision": 0}
        response = self.flow.invoke("scope-propose", data)
        self.assertEqual(response["next_action"]["operation"], "scope-proposed", response)
        return data, response["next_action"]["proposal"]["digest"]

    def seal(self, digest):
        data = {"proposal_id": "proposal-1", "proposal_digest": digest,
                "controller_ref": "task", "decision": {"kind": "in-requirement-refinement",
                "reference": "controller:scope-review"}}
        response = self.flow.invoke("scope-seal", data)
        self.assertEqual(response["next_action"]["operation"], "scope-sealed", response)
        return data

    def test_proposal_and_seal_are_durable_and_idempotent(self):
        candidate = self.planning_candidate()
        proposal, digest = self.propose(candidate)
        self.assertIsNone(self.flow.state()["scope_authority"])
        replay = self.flow.invoke("scope-propose", proposal, revision=0)
        self.assertTrue(replay["acknowledged"])
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        seal = self.seal(digest)
        authority = self.flow.state()["scope_authority"]
        self.assertEqual(authority["scope"]["implementation_paths"], ["impl.py"])
        self.assertEqual(authority["scope"]["closure_paths"], ["CHANGELOG.md"])
        self.assertEqual(authority["revision"], 1)
        self.assertEqual(self.flow.state()["control"]["context"]["handoff_progress"]["scope_seal"], authority["seal"])
        self.assertTrue(self.flow.invoke("scope-seal", seal, revision=0)["acknowledged"])
        conflict = copy.deepcopy(proposal)
        conflict["implementation_required"] = False
        before = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("scope-propose", conflict, revision=0)
        self.assertEqual(error.exception.code, "scope_conflict")
        self.assertEqual(self.flow.checkpoint.read_bytes(), before)

    def test_generated_inventory_rejects_omitted_declared_output(self):
        candidate = self.planning_candidate()
        state = self.flow.state()
        proposal = {"proposal_id": "generated-proposal", "expected_revision": 0,
            "candidate_commit": candidate, "implementation_required": True,
            "requirement_identity": copy.deepcopy(state["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(state["handoff"]["binding"]),
            "previous_scope_revision": 0,
            "generated_outputs": {"impl.py": ["generated/a.py", "generated/b.py"]}}
        basis = {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}
        generated = {"path": "generated/a.py", "owner": "implementation", "kind": "generated",
            "operation": "add", "basis": basis, "source_paths": ["impl.py"]}
        proposal["manifest"] = sorted([*self.manifest(candidate), generated], key=lambda item: item["path"])
        before = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("scope-propose", proposal)
        self.assertEqual(error.exception.code, "scope_incomplete")
        self.assertEqual(self.flow.checkpoint.read_bytes(), before)
        proposal["manifest"].append({**generated, "path": "generated/b.py"})
        proposal["manifest"] = sorted(proposal["manifest"], key=lambda item: item["path"])
        proposed = self.flow.invoke("scope-propose", proposal)
        self.assertEqual(proposed["next_action"]["operation"], "scope-proposed", proposed)

    def test_unicode_controller_seal_survives_current_readback(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        response = self.flow.invoke("scope-seal", {"proposal_id": "proposal-1",
            "proposal_digest": digest, "controller_ref": "task",
            "decision": {"kind": "in-requirement-refinement", "reference": "控制者：范围确认"}})
        self.assertEqual(response["next_action"]["operation"], "scope-sealed", response)
        self.assertEqual(self.flow.invoke("inspect")["status"], "active")

    def test_current_manifest_cannot_drift_from_controller_seal(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        outer = progress.read_record(self.flow.checkpoint)
        state = outer[progress.KEY]
        authority = state["scope_authority"]
        authority["scope"]["downstream"]["manifest"][-1]["path"] = "other.py"
        authority["scope"]["implementation_paths"] = ["other.py"]
        authority["proposal"]["manifest"][-1]["path"] = "other.py"
        authority["proposal_digest"] = fixture.transfer.entry.digest(authority["proposal"])
        authority["scope"]["downstream"]["proposal_ref"] = authority["proposal_digest"]
        progress.atomic_save(self.flow.checkpoint, outer)
        before = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("inspect")
        self.assertEqual(error.exception.code, "scope_changed")
        self.assertEqual(self.flow.checkpoint.read_bytes(), before)

    def test_expanded_scope_requires_prior_saved_user_change_decision(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        question = self.flow.observation("idle", "result", {"delivery_id": "change-question",
            "status": "needs_input", "payload": {"question": "Approve the changed behavior?"}})
        pending = self.flow.invoke("observe", question)["pending"]
        self.assertEqual(pending["kind"], "user-decision")
        self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "approve-change",
            "reference": "user:approved-change-1"})
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        data = {"proposal_id": "proposal-1", "proposal_digest": digest,
            "controller_ref": "task", "decision": {"kind": "reference-to-approved-change",
                "reference": "controller:expanded-scope", "approved_change_ref": "forged"}}
        before = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("scope-seal", data)
        self.assertEqual(error.exception.code, "approved_change_missing")
        self.assertEqual(self.flow.checkpoint.read_bytes(), before)
        data["decision"]["approved_change_ref"] = "user:approved-change-1"
        sealed = self.flow.invoke("scope-seal", data)
        self.assertEqual(sealed["next_action"]["operation"], "scope-sealed", sealed)
        self.assertEqual(self.flow.state()["scope_authority"]["approved_changes"][0]["record"]["decision"]["answer"],
                         "approve-change")
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        acceptance = self.flow.invoke("receive-publication")["pending"]
        accepted = self.flow.invoke("decide", {"decision_id": acceptance["decision_id"],
            "subject": acceptance["subject"], "answer": "accept", "reference": "controller:accepted"})
        self.assertTrue(accepted["handoff_ready"], accepted)

    def test_publication_requires_seal_and_accepted_plan_becomes_ready(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        blocked = self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        self.assertEqual(blocked["error"]["code"], "scope_incomplete")
        self.seal(digest)
        published = self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        self.assertEqual(published["next_action"]["operation"], "publication-receipt", published)
        pending = self.flow.invoke("receive-publication")["pending"]
        accepted = self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "controller:accept-plan"})
        self.assertTrue(accepted["planning_complete"], accepted)
        self.assertTrue(accepted["handoff_ready"], accepted)
        self.assertEqual(accepted["readiness"]["scope_revision"], 1)

    def test_accept_reuses_verified_readiness_then_public_render_rechecks(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        decision = {"decision_id": pending["decision_id"], "subject": pending["subject"],
                    "answer": "accept", "reference": "controller:accept-plan"}
        with patch.object(progress, "scope_readiness", wraps=progress.scope_readiness) as readiness_calls, \
                patch.object(fixture.transfer.handoff, "current_readiness",
                             side_effect=AssertionError("C view repeated public B readback")):
            accepted = self.flow.invoke("decide", decision)
        self.assertTrue(accepted["handoff_ready"], accepted)
        self.assertEqual(readiness_calls.call_count, 1)
        current = self.flow.state()
        fixture.transfer.handoff.render(current["accepted"], readiness=accepted["readiness"])
        outer = progress.read_record(self.flow.checkpoint)
        outer[progress.KEY]["revision"] += 1
        progress.atomic_save(self.flow.checkpoint, outer)
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            fixture.transfer.handoff.render(current["accepted"], readiness=accepted["readiness"])
        self.assertEqual(error.exception.code, "handoff_not_ready")

    def test_explicit_empty_implementation_is_planning_only(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate, implementation=False)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        accepted = self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "controller:accept-plan"})
        self.assertTrue(accepted["planning_complete"], accepted)
        self.assertFalse(accepted["handoff_ready"], accepted)
        self.assertEqual(accepted["route"], "planning-only")

    def test_stage_three_consumes_current_seal_and_retains_it_after_start(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        accepted = self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "controller:accept-plan"})
        self.assertTrue(accepted["handoff_ready"], accepted)
        predecessor = self.flow.state()["accepted"]
        authority = self.flow.state()["scope_authority"]
        request = self.flow.input_for(3, predecessor)
        request["scope"]["downstream"] = copy.deepcopy(authority["scope"]["downstream"])
        request["scope"]["implementation_paths"] = copy.deepcopy(authority["scope"]["implementation_paths"])
        request["scope"]["closure_paths"] = copy.deepcopy(authority["scope"]["closure_paths"])
        request["authorization"]["scope_digest"] = fixture.transfer.entry.digest(request["scope"])
        self.flow.context = self.flow.context_for(3)
        started = self.flow.begin("continuous", request)
        self.assertEqual(started["next_action"]["operation"], "invoke-host", started)
        state = self.flow.state()
        self.assertEqual(state["consumed_readiness"], accepted["readiness"])
        self.assertEqual(progress.consumed_scope_readiness(state, self.flow.checkpoint), accepted["readiness"])
        fixture.transfer.handoff.verify(state["handoff"])

    def test_prelaunch_revision_adds_missing_test_without_rewriting_acceptance(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "controller:accept-plan"})
        before = self.flow.state()
        authority = before["scope_authority"]
        manifest = copy.deepcopy(authority["scope"]["downstream"]["manifest"])
        manifest.append({"path": "tests/test_impl.py", "owner": "implementation", "kind": "test",
            "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}})
        data = {"revision_id": "revision-1", "expected_scope_revision": 1,
            "previous_scope_digest": fixture.transfer.entry.digest(authority["scope"]),
            "requirement_identity": copy.deepcopy(before["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(before["handoff"]["binding"]),
            "planning_commit": candidate,
            "planning_merge_commit": before["accepted"]["payload"]["planning_merge_commit"],
            "candidate_commit": candidate, "old_manifest": copy.deepcopy(authority["scope"]["downstream"]["manifest"]),
            "manifest": manifest, "implementation_required": True,
            "diff": stage_scope.manifest_diff(authority["scope"]["downstream"]["manifest"], manifest),
            "reason": "The implementation needs its approved regression test file.",
            "controller_ref": "task", "decision": {"kind": "in-requirement-refinement",
                "reference": "controller:add-test"}}
        original_control = progress.Progress.control_action
        def interrupted(owner, request):
            original_control(owner, request)
            raise RuntimeError("interrupted after Controller receipt")
        with patch.object(progress.Progress, "control_action", interrupted):
            with self.assertRaisesRegex(RuntimeError, "Controller receipt"):
                self.flow.invoke("scope-revise", data)
        revised = self.flow.invoke("scope-revise", data)
        self.assertEqual(revised["next_action"]["operation"], "scope-revision-sealed", revised)
        after = self.flow.state()
        self.assertEqual(after["accepted"], before["accepted"])
        self.assertEqual(after["scope_authority"]["revision"], 2)
        self.assertEqual(after["scope_authority"]["scope"]["implementation_paths"],
                         ["impl.py", "tests/test_impl.py"])
        self.assertTrue(self.flow.invoke("scope-revise", data, revision=0)["acknowledged"])
        second = copy.deepcopy(data)
        current = after["scope_authority"]
        second.update(revision_id="revision-2", expected_scope_revision=2,
                      previous_scope_digest=fixture.transfer.entry.digest(current["scope"]),
                      old_manifest=copy.deepcopy(current["scope"]["downstream"]["manifest"]))
        second["manifest"].append({**copy.deepcopy(second["manifest"][-1]), "path": "tests/test_extra.py"})
        second["manifest"].sort(key=lambda item: item["path"])
        second["diff"] = stage_scope.manifest_diff(second["old_manifest"], second["manifest"])
        with patch.object(progress.Progress, "control_action", interrupted):
            with self.assertRaisesRegex(RuntimeError, "Controller receipt"):
                self.flow.invoke("scope-revise", second)
        self.flow.invoke("scope-revise", second)
        self.assertEqual(self.flow.state()["scope_authority"]["revision"], 3)
        self.assertEqual(self.flow.state()["accepted"], before["accepted"])
        corrected = self.flow.state()
        request = self.flow.input_for(3, corrected["accepted"])
        for key in ("downstream", "implementation_paths", "closure_paths"):
            request["scope"][key] = copy.deepcopy(corrected["scope_authority"]["scope"][key])
        request["authorization"]["scope_digest"] = fixture.transfer.entry.digest(request["scope"])
        self.flow.context = self.flow.context_for(3)
        self.flow.begin("continuous", request)
        started = self.flow.state()
        self.assertEqual(started["stage"], 3)
        self.assertEqual(started["consumed_readiness"]["scope_revision"], 3)
        self.assertIn("tests/test_extra.py", started["handoff"]["scope"]["implementation_paths"])
        fixture.transfer.handoff.verify(started["handoff"])

    def test_active_stage_three_revision_rejected_before_checkpoint_write(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "controller:accept-plan"})
        design = self.flow.state()
        authority = design["scope_authority"]
        request = self.flow.input_for(3, design["accepted"])
        request["scope"]["downstream"] = copy.deepcopy(authority["scope"]["downstream"])
        request["scope"]["implementation_paths"] = copy.deepcopy(authority["scope"]["implementation_paths"])
        request["scope"]["closure_paths"] = copy.deepcopy(authority["scope"]["closure_paths"])
        request["authorization"]["scope_digest"] = fixture.transfer.entry.digest(request["scope"])
        self.flow.context = self.flow.context_for(3)
        self.flow.begin("continuous", request)
        self.flow.invoke("observe", self.flow.observation(ref="native:dispatcher"))
        (self.flow.flow / "impl.py").write_text("def approved_behavior():\n    return True\n")
        self.flow.flow_git("add", "impl.py")
        self.flow.flow_git("commit", "-qm", "existing authorized implementation")
        before = self.flow.state()
        manifest = copy.deepcopy(authority["scope"]["downstream"]["manifest"])
        manifest.append({"path": "tests/test_impl.py", "owner": "implementation", "kind": "test",
            "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}})
        data = {"revision_id": "revision-active", "expected_scope_revision": 1,
            "previous_scope_digest": fixture.transfer.entry.digest(authority["scope"]),
            "requirement_identity": copy.deepcopy(design["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(design["handoff"]["binding"]),
            "planning_commit": candidate,
            "planning_merge_commit": design["accepted"]["payload"]["planning_merge_commit"],
            "candidate_commit": candidate, "old_manifest": copy.deepcopy(authority["scope"]["downstream"]["manifest"]),
            "manifest": manifest, "implementation_required": True,
            "diff": stage_scope.manifest_diff(authority["scope"]["downstream"]["manifest"], manifest),
            "reason": "Add the regression test file before implementation resumes.",
            "controller_ref": "task", "decision": {"kind": "in-requirement-refinement",
                "reference": "controller:add-test"}}
        checkpoint = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("scope-revise", data)
        self.assertEqual(error.exception.code, "scope_revision_unavailable")
        self.assertEqual(self.flow.checkpoint.read_bytes(), checkpoint)

    def test_saved_launch_intent_closes_correction_even_when_unknown_or_cancelled(self):
        for launch_state in ("unknown", "cancelled", "prelaunch"):
            outer = {progress.KEY: {"stage": 2},
                     "launch": {"stage": "stage3", "state": launch_state}}
            with self.subTest(launch_state=launch_state), self.assertRaises(fixture.transfer.entry.PreparationError):
                progress.require_scope_correction_available(outer)

    def test_saved_stage_three_preparation_closes_correction(self):
        for prepared in ({"stage3_prepared": {}}, {"prepared_inputs": {"stage3": {}}}):
            with self.subTest(prepared=prepared), self.assertRaises(fixture.transfer.entry.PreparationError):
                progress.require_scope_correction_available({progress.KEY: {"stage": 2}, **prepared})

    def test_saved_runner_history_does_not_reopen_correction(self):
        for record in ({"event": "failure", "stage": "stage3"},
                       {"event": "checkpoint", "next_stage": "stage3"}):
            with self.subTest(record=record), self.assertRaises(fixture.transfer.entry.PreparationError):
                progress.require_scope_correction_available({progress.KEY: {"stage": 2},
                    "current_stage": "stage2", "history": [record]})


if __name__ == "__main__":
    unittest.main()
