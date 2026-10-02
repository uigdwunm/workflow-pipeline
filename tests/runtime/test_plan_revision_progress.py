"""Controller plan revision at the durable C and real Git boundary."""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_scope_progress as fixture
import workflow_progress as progress
from unittest.mock import patch


class PlanRevisionProgressTests(unittest.TestCase):
    def new_active_flow(self, *, generated=False):
        case = fixture.ScopeProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        original_manifest = case.manifest

        def planning_manifest(candidate, *, implementation=True):
            manifest = original_manifest(candidate, implementation=implementation)
            manifest.append({"path": "docs/spec.md", "owner": "closure", "kind": "document",
                "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md",
                    "section": "approved design"}})
            if generated:
                manifest.append({"path": "generated/impl_copy.py", "owner": "implementation",
                    "kind": "generated", "operation": "add",
                    "basis": {"commit": candidate, "path": "docs/spec.md",
                              "section": "approved design"}, "source_paths": ["impl.py"]})
            return sorted(manifest, key=lambda item: item["path"])

        case.manifest = planning_manifest
        if generated:
            def propose_with_generated(candidate, *, implementation=True):
                state = case.flow.state()
                data = {"proposal_id": "proposal-1", "expected_revision": 0,
                    "candidate_commit": candidate, "manifest": case.manifest(candidate),
                    "implementation_required": True, "generated_outputs":
                        {"impl.py": ["generated/impl_copy.py"]},
                    "requirement_identity": copy.deepcopy(state["handoff"]["requirement_identity"]),
                    "binding": copy.deepcopy(state["handoff"]["binding"]),
                    "previous_scope_revision": 0}
                response = case.flow.invoke("scope-propose", data)
                case.assertEqual(response["next_action"]["operation"], "scope-proposed")
                return data, response["next_action"]["proposal"]["digest"]
            case.propose = propose_with_generated
        case.finish_planned_design()
        predecessor = case.flow.state()["accepted"]
        authority = case.flow.state()["scope_authority"]
        request = case.flow.input_for(3, predecessor)
        request["scope"]["downstream"] = copy.deepcopy(authority["scope"]["downstream"])
        request["scope"]["implementation_paths"] = copy.deepcopy(authority["scope"]["implementation_paths"])
        request["scope"]["closure_paths"] = copy.deepcopy(authority["scope"]["closure_paths"])
        request["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(request["scope"])
        case.flow.context = case.flow.context_for(3)
        case.flow.begin("continuous", request)
        case.flow.invoke("observe", case.flow.observation(ref="native:dispatcher"))
        return case.flow

    def report(self, flow):
        authority = flow.state()["scope_authority"]
        return {"revision_id": "internal-1", "expected_authority_revision": authority["revision"],
            "expected_authority_digest": fixture.fixture.transfer.entry.digest(authority),
            "controller_ref": "task", "classification": {"kind": "internal",
                "reference": "controller:private-function-move"},
            "report": {"ticket_ids": ["FEP-01"], "step_ids": ["FP1-01"],
                "reason": "Move a private helper within the approved behavior",
                "production_facts": "The current caller and output stay within the same module",
                "contract_before": authority["execution_plan"]["contract_digest"],
                "contract_after": authority["execution_plan"]["contract_digest"],
                "dependency_impact": ["FP1-01", "FP1-02"],
                "verification_impact": ["V1"], "carry_forward": []},
            "planning_paths": ["docs/spec.md"]}

    def test_active_writer_blocks_planning_release_before_any_document_edit(self):
        flow = self.new_active_flow()
        before = (flow.flow / "docs/spec.md").read_bytes()
        response = flow.invoke("prepare-plan-revision", self.report(flow))
        self.assertEqual(response["next_action"]["operation"], "stop-host")
        self.assertNotIn("write_release", response["next_action"])
        late = flow.observation("running", "result", ref="native:dispatcher",
            result={"delivery_id": "late-result", "status": "completed", "payload": {"candidate": "late"}})
        flow.invoke("observe", late)
        self.assertEqual(flow.state()["plan_revision_late_results"][0]["event_id"], late["event_id"])
        self.assertIsNone(flow.state()["dispatch"]["delivery"])
        self.assertEqual((flow.flow / "docs/spec.md").read_bytes(), before)
        self.assertEqual(flow.state()["scope_authority"]["revision"], 1)

    def test_stopped_original_host_receives_one_exact_planning_release(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        stopped = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(stopped["next_action"]["operation"], "plan-revision-prepared")
        release = stopped["next_action"]["write_release"]
        self.assertEqual(release, flow.state()["plan_revision_transaction"]["write_release"])
        self.assertEqual(flow.state()["plan_revision_transaction"]["snapshot"]["planning_paths"],
                         ["docs/spec.md"])
        replay = flow.invoke("prepare-plan-revision", report, revision=0)
        self.assertEqual(replay["next_action"]["write_release"], release)
        self.assertEqual(flow.state()["scope_authority"]["revision"], 1)

    def test_unknown_stop_call_requires_original_lookup_before_release(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        first = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(first["next_action"]["operation"], "stop-host")
        unknown = flow.invoke("observe", flow.observation("unknown", "result", ref="native:dispatcher"))
        self.assertNotIn("write_release", unknown.get("next_action") or {})
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "stopping")
        lookup = flow.invoke("advance")
        self.assertIn(lookup["next_action"]["operation"],
                      {"inspect-host-state", "await-host-recovery", "lookup-exact-action"})
        settled = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(settled["next_action"]["operation"], "plan-revision-prepared")

    def test_late_native_bind_settles_without_releasing_old_executor(self):
        flow = self.new_active_flow()
        execution = flow.execution_input()
        parent = flow.state()["handoff"]
        execution["scope"] = copy.deepcopy(parent["scope"])
        execution["scope"]["owned_paths"] = ["impl.py"]
        execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(execution["scope"])
        prepared = flow.invoke("allocation", {"allocation_id": "late-native", "operation": "prepare",
            "handoff": execution})
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host")
        flow.invoke("prepare-plan-revision", self.report(flow))
        slot = flow.state()["allocations"]["late-native"]
        late = flow.invoke("allocation", {"allocation_id": "late-native", "operation": "bind",
            "receipt": flow.receipt(slot["record"], ref="native:late-executor")})
        self.assertEqual(late["next_action"]["operation"], "allocation-result")
        self.assertNotIn("write_release", late["next_action"]["result"])
        self.assertNotIn("write_release", flow.state()["allocations"]["late-native"]["result"])
        replay = flow.invoke("allocation", {"allocation_id": "late-native", "operation": "reconcile",
            "receipt": flow.receipt(flow.state()["allocations"]["late-native"]["record"],
                                    "ready", "lookup", ref="native:late-executor")})
        self.assertNotIn("write_release", replay["next_action"]["result"])
        stopped = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertNotIn("write_release", stopped.get("next_action") or {})
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "stopping")

    def test_only_planning_commit_activates_one_current_plan_and_preserves_index(self):
        flow = self.new_active_flow()
        (flow.flow / "impl.py").write_text("pending implementation\n")
        flow.flow_git("add", "impl.py")
        before_index = progress.control_git.plan_revision_snapshot(flow.flow,
            flow.state()["scope_authority"], flow.state()["handoff"]["scope"]["protected_paths"],
            ["docs/spec.md"])["index_entries"]["impl.py"]
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        release = prepared["next_action"]["write_release"]
        spec = flow.flow / "docs/spec.md"
        old = spec.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", old, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("move the private helper without changing output")
        spec.write_text(old[:match.start(1)] + json.dumps(plan) + old[match.end(1):])
        spec.write_text(spec.read_text().replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise private plan", "--", "docs/spec.md")
        candidate = flow.flow_git("rev-parse", "HEAD")
        result = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": release, "planning_commit": candidate})
        self.assertEqual(result["next_action"]["operation"], "plan-revision-activated")
        self.assertEqual(flow.state()["scope_authority"]["revision"], 2)
        self.assertEqual(progress.control_git.plan_revision_snapshot(flow.flow,
            flow.state()["scope_authority"], flow.state()["handoff"]["scope"]["protected_paths"],
            ["docs/spec.md"])["index_entries"]["impl.py"], before_index)
        for operation, data in (("prepare-plan-revision", report),
                                ("commit-plan-revision", {"revision_id": report["revision_id"],
                                    "controller_ref": "task", "write_release": release,
                                    "planning_commit": candidate}),
                                ("reconcile-plan-revision", {"revision_id": report["revision_id"]})):
            replay = flow.invoke(operation, data, revision=0)
            self.assertTrue(replay["acknowledged"], replay)
            self.assertNotIn("write_release", replay["next_action"])
            self.assertEqual(flow.flow_git("rev-parse", "HEAD"), candidate)

    def test_public_contract_change_keeps_prepared_barrier_without_activation(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        spec.write_text(original.replace("AC-1: approved behavior", "AC-1: changed behavior"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "change public behavior", "--", "docs/spec.md")
        candidate = flow.flow_git("rev-parse", "HEAD")
        before = flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": candidate})
        self.assertEqual(error.exception.code, "contract_changed")
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "prepared")

    def test_implementation_index_drift_rejects_planning_commit(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("move private helper")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "private plan", "--", "docs/spec.md")
        (flow.flow / "impl.py").write_text("unapproved implementation during planning\n")
        flow.flow_git("add", "impl.py")
        before = flow.checkpoint.read_bytes()
        with self.assertRaises(progress.control.ControlError) as error:
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertIn("index entries changed", str(error.exception))
        self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_manifest_refinement_and_new_path_activate_together(self):
        flow = self.new_active_flow()
        authority = flow.state()["scope_authority"]
        manifest = copy.deepcopy(authority["scope"]["downstream"]["manifest"])
        manifest.append({"path": "helper.py", "owner": "implementation", "kind": "source",
            "operation": "add", "basis": {"commit": authority["candidate_commit"],
                "path": "docs/spec.md", "section": "approved design"}})
        manifest.sort(key=lambda item: item["path"])
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"]["manifest_diff"] = progress.stage_scope.manifest_diff(
            authority["scope"]["downstream"]["manifest"], manifest)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        self.assertIsNone(flow.state()["plan_revision_transaction"]["snapshot"]["files"]["helper.py"])
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"].append({**copy.deepcopy(plan["steps"][0]), "id": "FP1-03",
            "target": {"path": "helper.py", "symbol": "helper", "operation": "add"},
            "depends_on": ["FP1-01"]})
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "add approved private helper plan", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated")
        self.assertEqual(flow.state()["scope_authority"]["scope"]["implementation_paths"],
                         ["helper.py", "impl.py"])
        self.assertEqual(flow.state()["control"]["context"]["handoff_progress"]["allowed_paths"],
                         ["helper.py", "impl.py"])

    def test_generated_output_cannot_disappear_without_a_new_full_inventory(self):
        flow = self.new_active_flow(generated=True)
        authority = flow.state()["scope_authority"]
        self.assertEqual(authority["proposal"]["generated_outputs"],
                         {"impl.py": ["generated/impl_copy.py"]})
        manifest = [item for item in authority["scope"]["downstream"]["manifest"]
                    if item["path"] != "generated/impl_copy.py"]
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"]["manifest_diff"] = progress.stage_scope.manifest_diff(
            authority["scope"]["downstream"]["manifest"], manifest)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("private revision with incomplete output map")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "plan missing generated inventory", "--", "docs/spec.md")
        before = flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(error.exception.code, "scope_incomplete")
        self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_pause_and_cancel_never_activate_prepared_release(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        release = prepared["next_action"]["write_release"]
        paused = flow.invoke("pause")
        self.assertIn(paused["status"], {"paused", "pausing"})
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError):
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": release,
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        cancelled = flow.invoke("cancel")
        self.assertIn(cancelled["status"], {"cancelled", "cancelling"})
        self.assertNotIn("write_release", flow.invoke("reconcile-plan-revision",
            {"revision_id": report["revision_id"]})["next_action"])

    def test_lost_activation_response_reconciles_same_commit_and_authority(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("use the same approved public behavior")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise plan once", "--", "docs/spec.md")
        candidate = flow.flow_git("rev-parse", "HEAD")
        request = {"revision_id": report["revision_id"], "controller_ref": "task",
                   "write_release": prepared["next_action"]["write_release"],
                   "planning_commit": candidate}
        original_control = progress.Progress.control_action

        def lost_after_control(owner, input_data):
            original_control(owner, input_data)
            raise RuntimeError("lost response after control commit")

        with patch.object(progress.Progress, "control_action", lost_after_control):
            with self.assertRaisesRegex(RuntimeError, "lost response"):
                flow.invoke("commit-plan-revision", request)
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "activated")
        self.assertTrue(flow.invoke("commit-plan-revision", request, revision=0)["acknowledged"])
        self.assertTrue(flow.invoke("reconcile-plan-revision",
            {"revision_id": report["revision_id"]}, revision=0)["acknowledged"])
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), candidate)
        self.assertEqual(len(flow.state()["control"]["context"]["handoff_progress"]["plan_revisions"]), 1)

    def test_planning_committed_reconcile_respects_pause_and_new_host_activity(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("keep the same output after moving a helper")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "save planning before activation", "--", "docs/spec.md")
        request = {"revision_id": report["revision_id"], "controller_ref": "task",
                   "write_release": prepared["next_action"]["write_release"],
                   "planning_commit": flow.flow_git("rev-parse", "HEAD")}
        with patch.object(progress.Progress, "control_action", side_effect=RuntimeError("lost before control")):
            with self.assertRaisesRegex(RuntimeError, "lost before control"):
                flow.invoke("commit-plan-revision", request)
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "planning-committed")
        flow.invoke("observe", flow.observation("running", "result", ref="native:dispatcher"))
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            flow.invoke("reconcile-plan-revision", {"revision_id": report["revision_id"]})
        self.assertEqual(error.exception.code, "host_evidence_missing")
        flow.invoke("pause")
        paused = flow.invoke("reconcile-plan-revision", {"revision_id": report["revision_id"]})
        self.assertNotEqual(paused["next_action"]["operation"], "plan-revision-activated")
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "planning-committed")
        self.assertEqual(flow.state()["scope_authority"]["revision"], 1)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(PlanRevisionProgressTests)
