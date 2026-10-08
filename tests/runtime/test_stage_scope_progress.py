"""Stage-2 scope finalization through the durable C/B/controller boundary."""
import copy
import json
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

    def inline_plan(self):
        def step(id, symbol, depends_on):
            return {"id": id, "target": {"path": "impl.py", "symbol": symbol, "operation": "add"},
                "capability": "provide the approved behavior", "approach": ["implement and connect the function"],
                "inputs": ["approved input"], "outputs": ["approved result"],
                "failures": ["invalid input is rejected"], "depends_on": depends_on,
                "verification_refs": ["V1"], "read_only": []}
        return {"schema_version": 2, "ticket_id": "FEP-01", "plan_version": 1,
            "spec_ref": {"path": "docs/spec.md", "section": "Implementation Decisions"},
            "contract_refs": ["FEP-01:What to build", "FEP-01:AC-1", "PRD:D1:Plan"],
            "blocked_by": [], "verifications": [{"id": "V1", "behavior": "approved behavior",
                "seam": "C scope-propose and Git fixture", "commands":
                ["python3 scripts/run_tests.py --test-file tests/runtime/test_stage_scope_progress.py"]}],
            "steps": [step("FP1-01", "prepare", []), step("FP1-02", "execute", ["FP1-01"])],
            "artifact_steps": [],
            "behavior_slices": [{"id": identity, "step_ids": [identity],
                "ac_refs": ["FEP-01:AC-1"], "verification_refs": ["V1"],
                "read_only": [], "shared_state": []} for identity in ("FP1-01", "FP1-02")]}

    def planning_candidate(self, mode="continuous", *, with_plan=True):
        self.flow.begin(mode)
        self.flow.invoke("observe", self.flow.observation())
        content = "approved design\nImplementation: impl.py\nClosure: CHANGELOG.md\n"
        if with_plan:
            content += ("Ticket ID: FEP-01\nPlan Version: 1\n**What to build:** approved behavior\n"
                        "**Blocked by:** None\nAC-1: approved behavior\n## Implementation Decisions\n"
                        "### D1 — Plan\n```execution-plan-v2\n" +
                        json.dumps(self.inline_plan(), ensure_ascii=False) + "\n```\n")
        (self.flow.flow / "docs/spec.md").write_text(content)
        self.flow.flow_git("add", "docs/spec.md")
        self.flow.flow_git("commit", "-qm", "plan")
        return self.flow.flow_git("rev-parse", "HEAD")

    def manifest(self, candidate, *, implementation=True):
        if not implementation:
            return []
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

    def finish_planned_design(self):
        candidate = self.planning_candidate()
        _, digest = self.propose(candidate)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate,
            "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        self.flow.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "controller:accept-plan"})

    def test_missing_function_plan_rejects_scope_proposal_without_checkpoint_change(self):
        candidate = self.planning_candidate(with_plan=False)
        state = self.flow.state()
        proposal = {"proposal_id": "missing-plan", "expected_revision": 0,
            "candidate_commit": candidate, "manifest": self.manifest(candidate),
            "implementation_required": True,
            "requirement_identity": copy.deepcopy(state["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(state["handoff"]["binding"]),
            "previous_scope_revision": 0}
        before = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("scope-propose", proposal)
        self.assertEqual(error.exception.code, "execution_plan_incomplete")
        self.assertEqual(self.flow.checkpoint.read_bytes(), before)

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
        before = self.flow.checkpoint.read_bytes()
        outer = progress.read_active_owner(self.flow.checkpoint)
        state = outer[progress.KEY]
        authority = state["scope_authority"]
        authority["scope"]["downstream"]["manifest"][-1]["path"] = "other.py"
        authority["scope"]["implementation_paths"] = ["other.py"]
        authority["proposal"]["manifest"][-1]["path"] = "other.py"
        authority["proposal_digest"] = fixture.transfer.entry.digest(authority["proposal"])
        authority["scope"]["downstream"]["proposal_ref"] = authority["proposal_digest"]
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            progress.Progress(self.flow.checkpoint, outer).save()
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
        catalog = accepted["readiness"]["execution_plan"]["catalog"]
        self.assertEqual(catalog["ordered_step_ids"], ["FP1-01", "FP1-02"])
        self.assertEqual([step["verification_refs"] for step in catalog["tickets"][0]["plan"]["steps"]],
                         [["V1"], ["V1"]])
        self.assertEqual(catalog["plan_digest"], self.flow.state()["scope_authority"]["execution_plan"]["plan_digest"])

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
        # Advance the real current revision through its owner CAS; retain the
        # original accepted readiness token to exercise stale-token rejection.
        owner = progress.Progress(self.flow.checkpoint, progress.read_active_owner(self.flow.checkpoint))
        owner.save()
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

    def test_invalid_document_scope_proposals_leave_checkpoint_and_target_unchanged(self):
        candidate = self.planning_candidate()
        state = self.flow.state()
        checkpoint = self.flow.checkpoint.read_bytes()
        target = self.flow.git("rev-parse", "HEAD")
        for owner, code in (("implementation", "invalid_scope"), ("closure", "scope_incomplete")):
            with self.subTest(owner=owner):
                document = {**self.manifest(candidate)[0], "owner": owner}
                data = {"proposal_id": "invalid-document-" + owner, "expected_revision": 0,
                    "candidate_commit": candidate, "manifest": [document],
                    "implementation_required": owner == "implementation",
                    "requirement_identity": state["handoff"]["requirement_identity"],
                    "binding": state["handoff"]["binding"], "previous_scope_revision": 0}
                with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
                    self.flow.invoke("scope-propose", data)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(self.flow.checkpoint.read_bytes(), checkpoint)
                self.assertEqual(self.flow.git("rev-parse", "HEAD"), target)

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

    def test_prelaunch_revision_adds_closure_document_without_rewriting_acceptance(self):
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
        manifest.append({"path": "docs/extra.md", "owner": "closure", "kind": "document",
            "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}})
        manifest.sort(key=lambda item: item["path"])
        data = {"revision_id": "revision-1", "expected_scope_revision": 1,
            "previous_scope_digest": fixture.transfer.entry.digest(authority["scope"]),
            "requirement_identity": copy.deepcopy(before["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(before["handoff"]["binding"]),
            "planning_commit": candidate,
            "planning_merge_commit": before["accepted"]["payload"]["planning_merge_commit"],
            "candidate_commit": candidate, "old_manifest": copy.deepcopy(authority["scope"]["downstream"]["manifest"]),
            "manifest": manifest, "implementation_required": True,
            "diff": stage_scope.manifest_diff(authority["scope"]["downstream"]["manifest"], manifest),
            "reason": "The closure needs its approved documentation file.",
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
                         ["impl.py"])
        self.assertTrue(self.flow.invoke("scope-revise", data, revision=0)["acknowledged"])
        second = copy.deepcopy(data)
        current = after["scope_authority"]
        second.update(revision_id="revision-2", expected_scope_revision=2,
                      previous_scope_digest=fixture.transfer.entry.digest(current["scope"]),
                      old_manifest=copy.deepcopy(current["scope"]["downstream"]["manifest"]))
        second["manifest"].append({**copy.deepcopy(second["manifest"][1]), "path": "docs/other.md"})
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
        self.assertIn("docs/other.md", started["handoff"]["scope"]["closure_paths"])
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

    def test_raw_control_cannot_replace_a_prepared_correction_receipt(self):
        self.finish_planned_design()
        before = self.flow.state()
        authority = before["scope_authority"]
        old_manifest = authority["scope"]["downstream"]["manifest"]
        manifest = sorted([*copy.deepcopy(old_manifest),
            {**copy.deepcopy(old_manifest[0]), "path": "docs/extra.md"}],
            key=lambda item: item["path"])
        data = {"revision_id": "guarded-correction", "expected_scope_revision": 1,
            "previous_scope_digest": fixture.transfer.entry.digest(authority["scope"]),
            "requirement_identity": before["handoff"]["requirement_identity"],
            "binding": before["handoff"]["binding"],
            "planning_commit": authority["candidate_commit"],
            "planning_merge_commit": before["accepted"]["payload"]["planning_merge_commit"],
            "candidate_commit": authority["candidate_commit"], "old_manifest": old_manifest,
            "manifest": manifest, "implementation_required": True,
            "diff": stage_scope.manifest_diff(old_manifest, manifest), "reason": "Include closure document",
            "controller_ref": "task", "decision": {"kind": "in-requirement-refinement",
                "reference": "controller:include-test"}}
        calls = []
        def interrupted(owner, request):
            calls.append(copy.deepcopy(request))
            raise RuntimeError("before Controller receipt")
        with patch.object(progress.Progress, "control_action", interrupted):
            with self.assertRaisesRegex(RuntimeError, "before Controller receipt"):
                self.flow.invoke("scope-revise", data)
        checkpoint = self.flow.checkpoint.read_bytes()
        forged = calls[0]
        forged["evidence"]["new_scope_digest"] = "0" * 64
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            self.flow.invoke("control", forged)
        self.assertEqual(error.exception.code, "invalid_operation")
        self.assertEqual(self.flow.checkpoint.read_bytes(), checkpoint)
        resumed = self.flow.invoke("scope-revise", data)
        self.assertTrue(resumed["handoff_ready"], resumed)
        self.assertEqual(self.flow.state()["accepted"], before["accepted"])

    def test_raw_scope_control_without_transaction_is_rejected_without_writes(self):
        self.flow.begin("continuous")
        checkpoint = self.flow.checkpoint.read_bytes()
        for action in ("scope-seal", "scope-revise"):
            with self.subTest(action=action):
                with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
                    self.flow.invoke("control", {"action": action, "evidence": {},
                        "receipt": {"controller_ref": "task", "reference": "raw-control"}})
                self.assertEqual(error.exception.code, "invalid_operation")
                self.assertEqual(self.flow.checkpoint.read_bytes(), checkpoint)

    def test_progression_rejects_old_runner_envelope_without_writes(self):
        self.finish_planned_design()
        original = self.flow.checkpoint.read_bytes()
        readiness = progress.scope_readiness(self.flow.state(), self.flow.checkpoint)
        for version in (7, 8):
            with self.subTest(version=version):
                legacy = self.flow.checkpoint.with_name('legacy-' + str(version) + '.json')
                progress.atomic_save(legacy, {'version': version, progress.KEY: {'protocol': progress.PROTOCOL}})
                checkpoint = legacy.read_bytes()
                with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
                    progress.handle(legacy, {'protocol': progress.PROTOCOL, 'operation': 'inspect',
                        'expected_revision': 0, 'data': {}})
                self.assertEqual(error.exception.code, "legacy_run_requires_original_runtime")
                self.assertEqual(legacy.read_bytes(), checkpoint)

                with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
                    fixture.transfer.handoff.current_readiness({**readiness, 'checkpoint_path': str(legacy.resolve())})
                self.assertEqual(error.exception.code, "legacy_run_requires_original_runtime")
                self.assertEqual(legacy.read_bytes(), checkpoint)
                self.assertEqual(self.flow.checkpoint.read_bytes(), original)

    def test_correction_updates_stepwise_boundary_without_reasking_confirmed_entry(self):
        candidate = self.planning_candidate("stepwise")
        _, digest = self.propose(candidate, implementation=False)
        self.flow.ready_publication(candidate, "controller:planning-readiness")
        self.seal(digest)
        self.flow.invoke("publication", {"candidate_commit": candidate, "reference": "controller:planning-readiness"})
        pending = self.flow.invoke("receive-publication")["pending"]
        self.flow.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
            "answer": "accept", "reference": "controller:accept-plan"})
        self.assertIsNone(self.flow.state()["pending"])

        def correct(implementation, *, extra_closure=False, closure_only=False):
            state = self.flow.state()
            authority = state["scope_authority"]
            manifest = self.manifest(candidate, implementation=implementation)
            if closure_only:
                manifest = self.manifest(candidate)[:1]
            if extra_closure:
                manifest.append({**copy.deepcopy(manifest[0]), "path": "docs/extra.md"})
                manifest.sort(key=lambda item: item["path"])
            previous = authority["scope"]["downstream"]["manifest"]
            return self.flow.invoke("scope-revise", {
                "revision_id": "revision-" + str(authority["revision"]),
                "expected_scope_revision": authority["revision"],
                "previous_scope_digest": fixture.transfer.entry.digest(authority["scope"]),
                "requirement_identity": state["handoff"]["requirement_identity"], "binding": state["handoff"]["binding"],
                "planning_commit": candidate, "planning_merge_commit": state["accepted"]["payload"]["planning_merge_commit"],
                "candidate_commit": candidate, "old_manifest": previous, "manifest": manifest,
                "implementation_required": implementation, "diff": stage_scope.manifest_diff(previous, manifest),
                "reason": "Correct planning scope", "controller_ref": "task",
                "decision": {"kind": "in-requirement-refinement", "reference": "controller:correct-scope"}})

        ready = correct(True)
        self.assertIsNotNone(ready["pending"])
        self.assertEqual(ready["pending"]["kind"], "stage-entry")
        still_pending = correct(True, extra_closure=True)
        self.assertEqual(still_pending["pending"], ready["pending"])
        checkpoint = self.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            correct(False, closure_only=True)
        self.assertEqual(error.exception.code, "scope_incomplete")
        self.assertEqual(self.flow.checkpoint.read_bytes(), checkpoint)
        planning_only = correct(False)
        self.assertEqual(planning_only["route"], "planning-only")
        self.assertIsNone(planning_only["pending"])
        ready = correct(True)
        pending = ready["pending"]
        self.flow.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
            "answer": "confirm", "reference": "user:enter-implementation"})
        refined = correct(True, extra_closure=True)
        self.assertTrue(refined["handoff_ready"])
        self.assertIsNone(refined["pending"])


if __name__ == "__main__":
    unittest.main()
