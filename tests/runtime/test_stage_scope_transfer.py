"""Stage-transfer checks for the sealed scope boundary, using real Git blobs."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import stage_handoff as handoff
import test_stage_transfer as transfer_fixture
import test_stage_scope_progress as progress_fixture


class StageScopeTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.root / "docs").mkdir()
        (self.root / "docs/requirement.md").write_text("# Frozen requirement\n", encoding="utf-8")
        self.git("add", "docs/requirement.md")
        self.git("commit", "-qm", "requirement")
        self.baseline = self.git("rev-parse", "HEAD")
        self.unresolved = {"baseline": self.baseline, "owned_paths": ["docs/spec.md"],
            "protected_paths": ["docs/requirement.md"], "implementation_paths": None,
            "closure_paths": None, "downstream": {"state": "unresolved", "revision": 0,
                "implementation_required": None, "manifest": None, "proposal_ref": None, "seal_ref": None}}
        self.original = handoff.seal({"protocol": handoff.PROTOCOL, "kind": "stage-input", "stage": 2,
            "expected_entry": {"repository": {"root": str(self.root)}},
            "requirement_identity": {"path": "docs/requirement.md"}, "scope": self.unresolved,
            "controller_ref": "controller:test", "binding": {"worktree": str(self.root)},
            "source_commit": self.baseline, "role": "solution-designer"})
        (self.root / "docs/spec.md").write_text("# Approved file decision\n", encoding="utf-8")
        self.git("add", "docs/spec.md")
        self.git("commit", "-qm", "planning")
        self.candidate = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def authority(self, implementation=True):
        basis = {"commit": self.candidate, "path": "docs/spec.md", "section": "Approved file decision"}
        manifest = [{"path": "CHANGELOG.md", "owner": "closure", "kind": "document",
                     "operation": "add", "basis": basis}]
        if implementation:
            manifest.extend([
                {"path": "impl.py", "owner": "implementation", "kind": "source",
                 "operation": "add", "basis": basis},
                {"path": "tests/test_impl.py", "owner": "implementation", "kind": "test",
                 "operation": "add", "basis": basis}])
        else:
            manifest = []
        scope = copy.deepcopy(self.unresolved)
        scope["implementation_paths"] = [item["path"] for item in manifest if item["owner"] == "implementation"]
        scope["closure_paths"] = [item["path"] for item in manifest if item["owner"] == "closure"]
        scope["downstream"] = {"state": "sealed", "revision": 1,
            "implementation_required": implementation, "manifest": manifest,
            "proposal_ref": "proposal-digest", "seal_ref": "seal-reference"}
        return {"state": "sealed", "revision": 1, "scope": scope,
                "candidate_commit": self.candidate, "proposal_digest": "proposal-digest",
                "seal_ref": "seal-reference", "proposal": {"generated_outputs": {}}}

    def test_removed_bridge_is_not_an_accepted_stage_result(self):
        bridge = handoff.seal({"protocol": handoff.PROTOCOL,
                              "kind": "legacy-supplemental-acceptance"})
        with self.assertRaises(entry.PreparationError):
            handoff.render(bridge)

    def test_control_rejects_removed_takeover_and_stage_three_revision(self):
        context = {"schema_version": 5, "controller_ref": "controller:test", "topic_ref": None,
                   "stage": 3, "carrier": None, "preference": {"topic_current": False,
                   "stage_current": False}, "flow_authority": None, "handoff_progress": None,
                   "requirement_identity": {"path": "docs/requirement.md", "sha256": "a" * 64,
                                            "version": 1}}
        for action in ("scope-revise", "start-dispatch-revision", "scope-recover-legacy",
                       "scope-recover-legacy-reserve", "scope-recover-legacy-supersede"):
            with self.subTest(action=action), self.assertRaises(handoff.control.ControlError):
                handoff.control.transition({"schema_version": 5, "actor_ref": "controller:test",
                                            "context": context, "action": action, "evidence": {}})
        with self.assertRaises(handoff.control.ControlError):
            handoff.control.validate_context({**context, "legacy_takeover": {}})

    def test_sealed_manifest_creates_exact_stage_three_ceiling(self):
        sealed = handoff.effective_scope(self.original, self.authority())
        self.assertEqual(sealed["implementation_paths"], ["impl.py", "tests/test_impl.py"])
        self.assertEqual(sealed["closure_paths"], ["CHANGELOG.md"])
        self.assertIsNone(self.original["scope"]["implementation_paths"])

    def test_seal_cannot_rewrite_planning_launch_or_project_missing_test(self):
        authority = self.authority()
        authority["scope"]["owned_paths"] = ["docs/other.md"]
        with self.assertRaises(entry.PreparationError):
            handoff.effective_scope(self.original, authority)
        authority = self.authority()
        authority["scope"]["implementation_paths"] = ["impl.py"]
        with self.assertRaises(entry.PreparationError):
            handoff.effective_scope(self.original, authority)

    def test_planning_only_is_explicit_and_has_no_implementation_paths(self):
        sealed = handoff.effective_scope(self.original, self.authority(implementation=False))
        self.assertIs(sealed["downstream"]["implementation_required"], False)
        self.assertEqual(sealed["implementation_paths"], [])
        self.assertEqual(sealed["closure_paths"], [])

    def test_empty_closure_manifest_does_not_unfreeze_requirement(self):
        authority = self.authority()
        authority["scope"]["downstream"]["manifest"] = [item for item in
            authority["scope"]["downstream"]["manifest"] if item["owner"] == "implementation"]
        authority["scope"]["closure_paths"] = []
        sealed = handoff.effective_scope(self.original, authority)
        self.assertEqual(sealed["closure_paths"], [])
        self.assertIn("docs/requirement.md", sealed["protected_paths"])

    def test_generated_output_needs_the_exact_sealed_source_map(self):
        authority = self.authority()
        authority["scope"]["downstream"]["manifest"].insert(1, {
            "path": "generated/impl.out", "owner": "implementation", "kind": "generated",
            "operation": "add", "basis": {"commit": self.candidate, "path": "docs/spec.md",
                "section": "Approved file decision"}, "source_paths": ["impl.py"]})
        authority["scope"]["implementation_paths"].insert(0, "generated/impl.out")
        with self.assertRaises(entry.PreparationError):
            handoff.effective_scope(self.original, authority)
        authority["proposal"]["generated_outputs"] = {"impl.py": [
            "generated/extra.out", "generated/impl.out"]}
        with self.assertRaises(entry.PreparationError):
            handoff.effective_scope(self.original, authority)
        authority["proposal"]["generated_outputs"] = {"impl.py": ["generated/impl.out"]}
        self.assertEqual(handoff.effective_scope(self.original, authority)["implementation_paths"],
                         ["generated/impl.out", "impl.py", "tests/test_impl.py"])

    def test_real_stage_two_preparation_has_no_downstream_write_lease(self):
        fixture = transfer_fixture.StageTransferTests(
            "test_real_a_freeze_and_flow_transfer_preserve_user_changes")
        fixture.setUp()
        try:
            request = copy.deepcopy(fixture.input)
            request["scope"]["implementation_paths"] = None
            request["scope"]["closure_paths"] = None
            request["scope"]["downstream"] = copy.deepcopy(self.unresolved["downstream"])
            request["authorization"]["scope_digest"] = entry.digest(request["scope"])
            prepared = handoff.handle(request)
            self.assertEqual(prepared["scope"]["owned_paths"], ["docs/spec.md"])
            self.assertIsNone(prepared["scope"]["implementation_paths"])
            self.assertIsNone(prepared["scope"]["closure_paths"])
        finally:
            fixture.doCleanups()

    def test_accepted_planning_does_not_claim_stage_three_readiness(self):
        accepted = handoff.seal({"protocol": handoff.PROTOCOL, "status": "accepted",
            "handoff": self.original, "planning_complete": True, "downstream_ready": False,
            "role_ref": "native:designer", "requirement_identity": {"path": "docs/requirement.md"},
            "payload": {"artifacts": ["docs/spec.md"], "checks": ["reviewed"],
                        "planning_commit": self.candidate, "planning_merge_commit": self.candidate,
                        "planning_paths": ["docs/spec.md"]}})
        rendered = handoff.render(accepted)
        self.assertTrue(rendered["payload"]["planning_complete"])
        self.assertFalse(rendered["downstream_ready"])
        self.assertIsNone(rendered["payload"]["allowed_paths"])
        self.assertEqual(rendered["payload"]["protected_paths"],
                         ["docs/requirement.md", "docs/spec.md"])

    def test_standalone_stage_three_requires_a_sealed_manifest(self):
        fixture = transfer_fixture.StageTransferTests(
            "test_real_a_freeze_and_flow_transfer_preserve_user_changes")
        fixture.setUp()
        try:
            request = fixture.input_for(3)
            self.assertEqual(handoff.handle(request)["scope"]["implementation_paths"], ["impl.py"])
            unresolved = copy.deepcopy(request)
            unresolved["scope"]["implementation_paths"] = None
            unresolved["scope"]["closure_paths"] = None
            unresolved["scope"]["downstream"] = copy.deepcopy(self.unresolved["downstream"])
            unresolved["authorization"]["scope_digest"] = entry.digest(unresolved["scope"])
            with self.assertRaises(entry.PreparationError) as error:
                handoff.handle(unresolved)
            self.assertEqual(error.exception.code, "scope_incomplete")
        finally:
            fixture.doCleanups()

    def test_standalone_generated_output_map_is_part_of_the_input(self):
        fixture = transfer_fixture.StageTransferTests(
            "test_real_a_freeze_and_flow_transfer_preserve_user_changes")
        fixture.setUp()
        try:
            request = fixture.input_for(3)
            basis = copy.deepcopy(request["scope"]["downstream"]["manifest"][0]["basis"])
            request["scope"]["downstream"]["manifest"].insert(1, {
                "path": "generated/impl.out", "owner": "implementation", "kind": "generated",
                "operation": "add", "basis": basis, "source_paths": ["impl.py"]})
            request["scope"]["implementation_paths"].insert(0, "generated/impl.out")
            request["scope"]["owned_paths"].insert(0, "generated/impl.out")
            request["authorization"]["scope_digest"] = entry.digest(request["scope"])
            with self.assertRaises(entry.PreparationError):
                handoff.handle(request)
            request["generated_outputs"] = {"impl.py": ["generated/impl.out"]}
            prepared = handoff.handle(request)
            self.assertEqual(prepared["generated_outputs"], request["generated_outputs"])
            self.assertEqual(handoff.verify(prepared), prepared)
        finally:
            fixture.doCleanups()

    def test_allocation_keeps_manifest_origin_after_implementation_commit(self):
        fixture = transfer_fixture.StageTransferTests(
            "test_real_a_freeze_and_flow_transfer_preserve_user_changes")
        fixture.setUp()
        try:
            original = fixture.input_for(3)["scope"]["baseline"]
            (fixture.flow / "impl.py").write_text("print('existing work')\n", encoding="utf-8")
            fixture.flow_git("add", "impl.py")
            fixture.flow_git("commit", "-qm", "existing implementation")
            allocation = fixture.input_for(3)
            allocation.update(role="execution-agent",
                              configuration=transfer_fixture.configuration("execution-agent"))
            with self.assertRaises(entry.PreparationError):
                handoff.handle(allocation)
            allocation["manifest_baseline"] = original
            saved = handoff.handle(allocation)
            self.assertEqual(saved["scope"]["baseline"], fixture.flow_git("rev-parse", "HEAD"))
            self.assertEqual(saved["manifest_baseline"], original)
        finally:
            fixture.doCleanups()

    def test_inherited_allocation_uses_original_planning_manifest(self):
        scenario = progress_fixture.ScopeProgressTests(
            "test_stage_three_consumes_current_seal_and_retains_it_after_start")
        scenario.setUp()
        try:
            scenario.test_stage_three_consumes_current_seal_and_retains_it_after_start()
            flow = scenario.flow
            flow.invoke("observe", flow.observation(ref="native:dispatcher"))
            allocation = flow.execution_input()
            parent = flow.state()["handoff"]["scope"]
            for field in ("downstream", "implementation_paths", "closure_paths", "protected_paths"):
                allocation["scope"][field] = copy.deepcopy(parent[field])
            allocation["scope"]["owned_paths"] = ["impl.py"]
            allocation["authorization"]["scope_digest"] = entry.digest(allocation["scope"])
            prepared = flow.invoke("allocation", {"allocation_id": "inherited-slice",
                                                  "operation": "prepare", "handoff": allocation})
            self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
            saved = flow.state()["allocations"]["inherited-slice"]["handoff"]
            self.assertEqual(saved["manifest_baseline"],
                             flow.state()["scope_authority"]["scope"]["baseline"])
        finally:
            scenario.doCleanups()

    def test_internal_projection_matches_public_render_but_public_rejects_stale_readiness(self):
        scenario = progress_fixture.ScopeProgressTests(
            "test_publication_requires_seal_and_accepted_plan_becomes_ready")
        scenario.setUp()
        try:
            scenario.test_publication_requires_seal_and_accepted_plan_becomes_ready()
            flow = scenario.flow
            state = flow.state()
            readiness = progress_fixture.progress.scope_readiness(state, flow.checkpoint)
            accepted = state["accepted"]
            self.assertEqual(handoff._render_verified(accepted, readiness),
                             handoff.render(accepted, readiness=readiness))
            stale = {**readiness, "checkpoint_revision": readiness["checkpoint_revision"] - 1}
            with self.assertRaises(entry.PreparationError):
                handoff.render(accepted, readiness=stale)
        finally:
            scenario.doCleanups()



if __name__ == "__main__":
    unittest.main()
