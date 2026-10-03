"""Controller plan revision at the durable C and real Git boundary."""
import copy
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_scope_progress as fixture
import workflow_progress as progress
from unittest.mock import patch


class PlanRevisionProgressTests(unittest.TestCase):
    def new_active_flow(self, *, generated=False, read_dependency=False, grouped_reads=False,
                        artifact_mode=None, retained_note=False):
        case = fixture.ScopeProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        original_manifest = case.manifest

        def planning_manifest(candidate, *, implementation=True):
            manifest = original_manifest(candidate, implementation=implementation)
            if artifact_mode == "only":
                manifest = [item for item in manifest if item["path"] != "impl.py"]
            if artifact_mode is not None:
                manifest.append({"path": "prompt.md", "owner": "implementation",
                    "kind": "source", "operation": "add",
                    "basis": {"commit": candidate, "path": "docs/spec.md",
                              "section": "approved design"}})
            manifest.append({"path": "docs/spec.md", "owner": "closure", "kind": "document",
                "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md",
                    "section": "approved design"}})
            if generated:
                manifest.append({"path": "generated/impl_copy.py", "owner": "implementation",
                    "kind": "generated", "operation": "add",
                    "basis": {"commit": candidate, "path": "docs/spec.md",
                              "section": "approved design"}, "source_paths": ["impl.py"]})
            if read_dependency:
                for relative in ("later.py", "independent.py", "downstream.py", "reader.py"):
                    manifest.append({"path": relative, "owner": "implementation", "kind": "source",
                        "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md",
                                                       "section": "approved design"}})
            return sorted(manifest, key=lambda item: item["path"])

        case.manifest = planning_manifest
        if retained_note:
            original_candidate = case.planning_candidate
            def planning_with_retained_note(*args, **kwargs):
                original_candidate(*args, **kwargs)
                source = case.flow.flow / "docs/spec.md"
                source.write_text(source.read_text() + "### N4 — Private inventory\nInitial note.\n")
                case.flow.flow_git("add", "docs/spec.md")
                case.flow.flow_git("commit", "-qm", "private inventory note")
                return case.flow.flow_git("rev-parse", "HEAD")
            case.planning_candidate = planning_with_retained_note
        if artifact_mode is not None:
            original_candidate = case.planning_candidate
            def planning_with_artifact(*args, **kwargs):
                original_candidate(*args, **kwargs)
                path = case.flow.flow / "docs/spec.md"
                text = path.read_text()
                match = re.search(r"```execution-plan-v1\n(.*?)\n```", text, re.DOTALL)
                plan = json.loads(match.group(1))
                if artifact_mode == "only":
                    plan["steps"] = []
                plan["artifact_steps"] = [{"id": "ART-1", "path": "prompt.md",
                    "operation": "add", "capability": "provide the approved prompt artifact",
                    "approach": "write the approved prompt", "verification_refs": ["V1"]}]
                updated = text[:match.start(1)] + json.dumps(plan) + text[match.end(1):]
                if artifact_mode == "only":
                    updated = updated.replace("Implementation: impl.py", "Implementation: prompt.md")
                path.write_text(updated)
                case.flow.flow_git("add", "docs/spec.md")
                case.flow.flow_git("commit", "-qm", "plan artifact step")
                return case.flow.flow_git("rev-parse", "HEAD")
            case.planning_candidate = planning_with_artifact
        if read_dependency:
            original_candidate = case.planning_candidate
            def planning_with_read_dependency(*args, **kwargs):
                original_candidate(*args, **kwargs)
                path = case.flow.flow / "docs/spec.md"
                text = path.read_text()
                match = re.search(r"```execution-plan-v1\n(.*?)\n```", text, re.DOTALL)
                plan = json.loads(match.group(1))
                plan["steps"][1]["target"] = {"path": "later.py", "symbol": "later", "operation": "add"}
                plan["steps"][1]["read_only"] = ["impl.py"]
                plan["steps"][1]["depends_on"] = []
                for identity in ("V2", "V3"):
                    verification = copy.deepcopy(plan["verifications"][0])
                    verification["id"] = identity
                    verification["behavior"] = "independent reader behavior" if identity == "V2" else "unrelated behavior"
                    plan["verifications"].append(verification)
                plan["steps"][1]["verification_refs"] = ["V2"]
                for identity, relative, dependencies, reads in (
                    ("FP1-03", "independent.py", [], []),
                    ("FP1-04", "downstream.py", ["FP1-02"], []),
                    ("FP1-05", "reader.py", [], ["later.py"])):
                    step = copy.deepcopy(plan["steps"][1])
                    step.update(id=identity, target={"path": relative,
                        "symbol": relative.removesuffix(".py"), "operation": "add"},
                        depends_on=dependencies, read_only=reads,
                        verification_refs=["V3"] if identity == "FP1-03" else ["V2"])
                    plan["steps"].append(step)
                if grouped_reads:
                    plan["steps"][1]["read_only"] = []
                    plan["steps"][2]["read_only"] = ["impl.py"]
                    plan["steps"][3]["depends_on"] = ["FP1-02", "FP1-03"]
                path.write_text(text[:match.start(1)] + json.dumps(plan) + text[match.end(1):])
                case.flow.flow_git("add", "docs/spec.md")
                case.flow.flow_git("commit", "-qm", "planned accepted implementation read")
                return case.flow.flow_git("rev-parse", "HEAD")
            case.planning_candidate = planning_with_read_dependency
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

    def test_prepare_plan_revision_rejects_non_string_report_ids_before_saving(self):
        flow = self.new_active_flow()
        before = flow.checkpoint.read_bytes()
        head = flow.flow_git("rev-parse", "HEAD")
        for field, value in (("ticket_ids", {"id": "FEP-01"}),
                             ("step_ids", ["FP1-01"]),
                             ("dependency_impact", {"id": "FP1-01"}),
                             ("verification_impact", ["V1"])):
            with self.subTest(field=field):
                report = self.report(flow)
                report["report"][field] = [value]
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke("prepare-plan-revision", report)
                self.assertEqual(error.exception.code, "invalid_request")
                self.assertIn(f"report.{field}[0]", str(error.exception))
                self.assertEqual(flow.checkpoint.read_bytes(), before)
                self.assertEqual(flow.flow_git("rev-parse", "HEAD"), head)

    def test_prepare_plan_revision_rejects_non_string_carry_and_planning_paths(self):
        for field in ("carry_forward", "planning_paths"):
            with self.subTest(field=field):
                flow = self.new_active_flow()
                before = flow.checkpoint.read_bytes()
                head = flow.flow_git("rev-parse", "HEAD")
                report = self.report(flow)
                if field == "carry_forward":
                    report["report"][field] = [["FP1-01"]]
                else:
                    report[field] = [["docs/spec.md"]]
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke("prepare-plan-revision", report)
                self.assertEqual(error.exception.code, "invalid_request")
                self.assertIn(f"{field}[0]", str(error.exception))
                self.assertEqual(flow.checkpoint.read_bytes(), before)
                self.assertEqual(flow.flow_git("rev-parse", "HEAD"), head)

    def test_prepare_plan_revision_rejects_duplicate_or_unknown_carry_steps(self):
        flow = self.new_active_flow()
        before = flow.checkpoint.read_bytes()
        for carry, code in ((["FP1-01", "FP1-01"], "invalid_request"),
                            (["missing-step"], "invalid_plan_revision")):
            with self.subTest(carry=carry):
                report = self.report(flow)
                report["report"]["carry_forward"] = carry
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke("prepare-plan-revision", report)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(flow.checkpoint.read_bytes(), before)

    def accept_planned_step(self, flow, ticket, identity, relative, content, allocation_id):
        execution = flow.execution_input()
        execution["scope"]["owned_paths"] = [relative]
        step = next(item for item in ticket["plan"]["steps"] if item["id"] == identity)
        execution["scope"]["protected_paths"] += step["read_only"]
        execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
            execution["scope"])
        execution["semantic"]["plan_refs"] = [progress.execution_plan.step_ref(ticket, step)]
        prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "prepare", "handoff": execution})
        self.assertIsNotNone(prepared["next_action"], prepared)
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
        slot = flow.state()["allocations"][allocation_id]
        bound = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
        release = bound["next_action"]["result"]["write_release"]
        (flow.flow / relative).write_bytes(content)
        result = {"changed_paths": [relative],
            "file_hashes": {relative: hashlib.sha256(content).hexdigest()},
            "tests": ["behavior passed"], "write_release": release,
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
            "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
            "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                       "payload": result}})
        delivered = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
        self.assertEqual(delivered["next_action"]["result"]["status"], "accepted")

    def accept_planned_group(self, flow, ticket, identities, contents, allocation_id,
                             read_only=(), owned_paths=None):
        execution = flow.execution_input()
        execution["scope"]["owned_paths"] = sorted(contents if owned_paths is None else owned_paths)
        execution["scope"]["protected_paths"] += list(read_only)
        execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
            execution["scope"])
        execution["semantic"]["plan_refs"] = [progress.execution_plan.step_ref(ticket, step)
            for step in ticket["plan"]["steps"] if step["id"] in identities]
        prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "prepare", "handoff": execution})
        self.assertIsNotNone(prepared["next_action"], prepared)
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
        slot = flow.state()["allocations"][allocation_id]
        bound = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
        release = bound["next_action"]["result"]["write_release"]
        for relative, content in contents.items():
            (flow.flow / relative).write_bytes(content)
        result = {"changed_paths": sorted(contents),
            "file_hashes": {relative: hashlib.sha256(content).hexdigest()
                            for relative, content in contents.items()},
            "tests": ["group behavior passed"], "write_release": release,
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
            "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
            "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                       "payload": result}})
        delivered = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
        self.assertEqual(delivered["next_action"]["result"]["status"], "accepted")

    def carried_mixed_group(self, replacement_contents=None):
        flow = self.new_active_flow(read_dependency=True, grouped_reads=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "input-owner")
        self.accept_planned_group(flow, ticket, {"FP1-02", "FP1-03"},
            {"later.py": b"def later():\n    return 1\n",
             "independent.py": b"def independent():\n    return 1\n"},
            "mixed-group", read_only=["impl.py"])
        if replacement_contents is not None:
            self.accept_planned_group(flow, ticket, {"FP1-02", "FP1-03"},
                replacement_contents, "replacement-group", read_only=["impl.py"],
                owned_paths=["independent.py", "later.py"])

        report = self.report(flow)
        report["report"].update(step_ids=["FP1-05"], dependency_impact=["FP1-05"],
            verification_impact=["V2"], carry_forward=["FP1-01", "FP1-02", "FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation(
            "stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][4]["approach"].append("revise unrelated reader")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise unrelated reader", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        retention = flow.state()["control"]["context"]["handoff_progress"]
        carried_group = next(item for item in retention["carried_results"]
                             if item["paths"] == ["independent.py", "later.py"])
        self.assertEqual(set(carried_group["read_only_fingerprints"]), {"impl.py"})
        current_ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        return flow, current_ticket, controller_request, controller_settings

    def test_zero_delta_group_carries_all_owned_fingerprints_and_reader_dependency(self):
        flow, ticket, _, _ = self.carried_mixed_group(replacement_contents={})
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        carried = next(item for item in checkpoint["carried_results"]
                       if item["paths"] == ["independent.py", "later.py"])
        self.assertEqual(carried["git_result_snapshot"], {})
        self.assertEqual(set(carried["owned_final_fingerprints"]), set(carried["paths"]))
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "downstream-after-zero-delta")
        context = flow.state()["control"]["context"]
        original = next(item for item in context["handoff_progress"]["plan_revision_sources"][0]["executions"]
                        if item["agent_ref"] == "replacement-group")
        self.assertEqual(original["git_result_snapshot"], {})
        for corruption in ("original-start", "original-delta", "source-digest",
                           "start-projection", "final-projection"):
            with self.subTest(corruption=corruption):
                tampered = copy.deepcopy(context)
                state = tampered["handoff_progress"]
                archived = next(item for item in state["plan_revision_sources"][0]["executions"]
                                if item["agent_ref"] == "replacement-group")
                carried = next(item for item in state["carried_results"]
                               if item["agent_ref"] == "replacement-group")
                if corruption == "original-start":
                    archived["git_snapshot"]["files"]["independent.py"] = "0" * 64
                elif corruption == "original-delta":
                    archived["git_result_snapshot"]["independent.py"] = "0" * 64
                elif corruption == "source-digest":
                    carried["source_result_digest"] = "0" * 64
                elif corruption == "start-projection":
                    carried["source_owned_start_fingerprints"]["independent.py"] = "0" * 64
                else:
                    carried["owned_final_fingerprints"]["independent.py"] = "0" * 64
                if corruption not in {"original-start", "original-delta"}:
                    revision = state["plan_revisions"][-1]
                    revision["evidence"]["retention"]["carried_results"] = copy.deepcopy(
                        state["carried_results"])
                    revision["revision_ref"] = progress.control.digest({
                        "controller_ref": revision["controller_ref"],
                        "evidence": revision["evidence"]})
                with self.assertRaisesRegex(progress.control.ControlError,
                                            "original accepted|owned fingerprints"):
                    progress.control.validate_context(tampered)

        relative = flow.flow / "independent.py"
        original_bytes = relative.read_bytes()
        original_mode = relative.stat().st_mode & 0o7777
        for mutation in ("content", "mode", "presence"):
            with self.subTest(mutation=mutation):
                if mutation == "content":
                    relative.write_bytes(original_bytes + b"# changed\n")
                elif mutation == "mode":
                    relative.chmod(0o755)
                else:
                    relative.unlink()
                with self.assertRaisesRegex(progress.control.ControlError,
                                            "accepted content, existence or mode changed"):
                    progress.control_git.recovery_snapshot(
                        flow.flow, context["handoff_progress"])
                if mutation == "presence":
                    relative.write_bytes(original_bytes)
                else:
                    relative.write_bytes(original_bytes)
                relative.chmod(original_mode)

    def test_partial_delta_group_can_enter_candidate_with_unchanged_owned_path(self):
        flow, ticket, _, _ = self.carried_mixed_group(
            replacement_contents={"later.py": b"def later():\n    return 2\n"})
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        carried = next(item for item in checkpoint["carried_results"]
                       if item["paths"] == ["independent.py", "later.py"])
        self.assertEqual(set(carried["git_result_snapshot"]), {"later.py"})
        self.assertEqual(set(carried["owned_final_fingerprints"]), set(carried["paths"]))
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "candidate-downstream")
        self.accept_planned_step(flow, ticket, "FP1-05", "reader.py",
                                 b"def reader():\n    return 1\n", "candidate-reader")
        flow.flow_git("add", "impl.py", "later.py", "independent.py", "downstream.py", "reader.py")
        flow.flow_git("commit", "-qm", "accepted partial delta implementation")
        candidate = flow.flow_git("rev-parse", "HEAD")
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        checks = fixture.fixture.transfer.checks_for(
            checkpoint["validation_plan"]["review_required"], candidate)
        evidence = {
            "dispatcher_ref": "native:dispatcher",
            "attempt": flow.state()["control"]["context"]["carrier"]["attempt"],
            "commit": candidate, "expected_target_head": flow.git("rev-parse", "main"),
            "binding": flow.binding, "plan_digest": checkpoint["plan_digest"],
            "plan_authority": checkpoint["plan_authority"], "checks": checks,
            "source": {"adapter": "fixture", "call_ref": "focused-partial-group",
                "response_ref": "focused-partial-group-result", "raw": {"checks": checks,
                    "source_unchanged": True, "stopped": True,
                    "dispatcher_ref": "native:dispatcher"}}}
        ready = progress.control_git.verified_transition({"repository": str(flow.flow),
            "baseline": checkpoint["git_baseline_commit"], "request": {
                "schema_version": 6, "action": "candidate-ready", "actor_ref": "task",
                "context": flow.state()["control"]["context"], "evidence": evidence}})
        self.assertEqual(ready["context"]["handoff_progress"]["state"], "reviewable")

    def test_zero_delta_owned_evidence_survives_two_activated_revisions(self):
        flow, _, _, _ = self.carried_mixed_group(replacement_contents={})
        first = flow.state()["control"]["context"]["handoff_progress"]
        original = next(item for item in first["carried_results"]
                        if item["agent_ref"] == "replacement-group")
        report = self.report(flow)
        report["revision_id"] = "zero-delta-second"
        report["classification"]["reference"] = "controller:zero-delta-second"
        report["report"].update(step_ids=["FP1-05"], dependency_impact=["FP1-05"],
            verification_impact=["V2"], carry_forward=["FP1-01", "FP1-02", "FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        stopped = flow.observation("stopped", "result", ref="native:dispatcher")
        stopped["lifecycle"]["pages"][0]["response"]["data"].extend(
            {"id": ref, "status": {"type": "idle"}}
            for ref in ("input-owner", "mixed-group", "replacement-group"))
        prepared = flow.invoke("observe", stopped)
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 3
        plan["steps"][4]["approach"].append("revise unrelated reader again")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 2", "Plan Version: 3"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise unrelated reader again", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated")
        second = flow.state()["control"]["context"]["handoff_progress"]
        carried = next(item for item in second["carried_results"]
                       if item["agent_ref"] == "replacement-group")
        for field in ("git_result_snapshot", "source_owned_start_fingerprints",
                      "owned_final_fingerprints", "source_result_digest", "source_revision"):
            self.assertEqual(carried[field], original[field], field)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "after-two-zero-delta-revisions")

    def test_carried_group_with_mixed_reads_satisfies_later_dependencies(self):
        flow, current_ticket, _, _ = self.carried_mixed_group()
        self.accept_planned_step(flow, current_ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "downstream-after-carry")

        self.accept_planned_step(flow, current_ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 2\n", "changed-input-owner")
        retention = flow.state()["control"]["context"]["handoff_progress"]
        completed = progress.execution_plan.current_accepted_steps(
            flow.state()["scope_authority"]["execution_plan"]["catalog"],
            retention["carried_results"] + retention["executions"],
            flow.state()["scope_authority"])
        self.assertTrue({"FP1-02", "FP1-03"}.isdisjoint(completed))
        self.accept_planned_group(flow, current_ticket, {"FP1-02", "FP1-03"},
            {"later.py": b"def later():\n    return 2\n",
             "independent.py": b"def independent():\n    return 2\n"},
            "fresh-mixed-group", read_only=["impl.py"])
        self.accept_planned_step(flow, current_ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 2\n", "downstream-after-revalidation")

    def test_carried_mixed_read_group_can_enter_candidate(self):
        flow, ticket, controller_request, controller_settings = self.carried_mixed_group()
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "candidate-downstream")
        self.accept_planned_step(flow, ticket, "FP1-05", "reader.py",
                                 b"def reader():\n    return 1\n", "candidate-reader")
        flow.flow_git("add", "impl.py", "later.py", "independent.py", "downstream.py", "reader.py")
        flow.flow_git("commit", "-qm", "accepted mixed read implementation")
        candidate = flow.flow_git("rev-parse", "HEAD")
        flow.request = controller_request
        flow.settings = controller_settings
        flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        checks = fixture.fixture.transfer.checks_for(
            checkpoint["validation_plan"]["review_required"], candidate)
        ready = flow.invoke("control", {"action": "candidate-ready", "evidence": {
            "dispatcher_ref": "native:dispatcher",
            "attempt": flow.state()["control"]["context"]["carrier"]["attempt"],
            "commit": candidate, "expected_target_head": flow.git("rev-parse", "main"),
            "binding": flow.binding, "plan_digest": checkpoint["plan_digest"],
            "plan_authority": checkpoint["plan_authority"], "checks": checks},
            "receipt": {"adapter": "fixture", "call_ref": "focused-mixed-group",
                "response_ref": "focused-mixed-group-result", "raw": {"checks": checks,
                    "source_unchanged": True, "stopped": True,
                    "dispatcher_ref": "native:dispatcher"}}})
        self.assertEqual(flow.state()["control"]["context"]["handoff_progress"]["state"],
                         "reviewable", ready)

    def test_revision_revalidates_reader_after_accepted_input_changes(self):
        flow = self.new_active_flow(read_dependency=True)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py", b"def impl():\n    return 1\n", "first-b")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py", b"def later():\n    return 1\n", "old-a")
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py", b"def impl():\n    return 2\n", "new-b")
        report = self.report(flow)
        report["report"].update(step_ids=["FP1-03"], dependency_impact=["FP1-03"],
            verification_impact=["V3"], carry_forward=["FP1-01", "FP1-02"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][2]["approach"].append("revise unrelated private implementation")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise independent step", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        retention = flow.state()["control"]["context"]["handoff_progress"]
        self.assertEqual({item["paths"][0] for item in retention["carried_results"]}, {"impl.py"})
        self.assertIn("later.py", retention["revalidation_source"]["pending_paths"])

    def test_consecutive_revisions_keep_the_original_reader_witness(self):
        flow = self.new_active_flow(read_dependency=True)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py", b"def impl():\n    return 1\n", "first-b")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py", b"def later():\n    return 1\n", "old-a")

        def revise_independent(number):
            report = self.report(flow)
            report["revision_id"] = f"independent-{number}"
            report["classification"]["reference"] = f"controller:independent-{number}"
            report["report"].update(step_ids=["FP1-03"], dependency_impact=["FP1-03"],
                verification_impact=["V3"], carry_forward=["FP1-01", "FP1-02"])
            flow.invoke("prepare-plan-revision", report)
            stopped = flow.observation("stopped", "result", ref="native:dispatcher")
            if number > 1:
                stopped["lifecycle"]["pages"][0]["response"]["data"].extend(
                    {"id": ref, "status": {"type": "idle"}} for ref in ("first-b", "old-a"))
            prepared = flow.invoke("observe", stopped)
            self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared", prepared)
            source = flow.flow / "docs/spec.md"
            body = source.read_text()
            match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
            plan = json.loads(match.group(1))
            plan["plan_version"] = number + 1
            plan["steps"][2]["approach"].append(f"independent revision {number}")
            source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                              .replace(f"Plan Version: {number}", f"Plan Version: {number + 1}"))
            flow.flow_git("add", "docs/spec.md")
            flow.flow_git("commit", "--only", "-qm", f"independent revision {number}",
                          "--", "docs/spec.md")
            activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
            self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
            return flow.state()["control"]["context"]["handoff_progress"]

        first = revise_independent(1)
        carried_reader = next(item for item in first["carried_results"] if item["paths"] == ["later.py"])
        original_input = carried_reader["read_only_fingerprints"]["impl.py"]
        tampered = copy.deepcopy(flow.state()["control"]["context"])
        original_reader = next(item for item in
            tampered["handoff_progress"]["plan_revision_sources"][0]["executions"]
            if item["agent_ref"] == "old-a")
        original_reader["git_snapshot"]["files"].pop("impl.py")
        self.assertIsNone(progress.execution_plan.accepted_read_fingerprints(
            original_reader, {"impl.py"}))
        with self.assertRaisesRegex(progress.control.ControlError, "original accepted"):
            progress.control.validate_context(tampered)
        self.accept_planned_step(flow, flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0],
            "FP1-01", "impl.py", b"def impl():\n    return 2\n", "new-b")
        second = revise_independent(2)
        self.assertEqual({item["paths"][0] for item in second["carried_results"]}, {"impl.py"})
        self.assertIn("later.py", second["revalidation_source"]["pending_paths"])
        self.assertNotEqual(original_input, second["revalidation_source"]["snapshot"]["files"]["impl.py"])

    def test_revision_revalidates_independent_readers_and_carries_unrelated_result(self):
        flow = self.new_active_flow(read_dependency=True)
        original = flow.state()["scope_authority"]
        ticket = original["execution_plan"]["catalog"]["tickets"][0]
        accepted = []
        for identity, relative in (("FP1-01", "impl.py"), ("FP1-02", "later.py"),
                                   ("FP1-03", "independent.py"),
                                   ("FP1-04", "downstream.py"),
                                   ("FP1-05", "reader.py")):
            execution = flow.execution_input()
            execution["scope"]["owned_paths"] = [relative]
            step = next(item for item in ticket["plan"]["steps"] if item["id"] == identity)
            execution["scope"]["protected_paths"] += step["read_only"]
            execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
                execution["scope"])
            execution["semantic"]["plan_refs"] = [progress.execution_plan.step_ref(ticket, step)]
            allocation_id = "accepted-" + identity
            prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
                "operation": "prepare", "handoff": execution})
            self.assertIsNotNone(prepared["next_action"], prepared)
            self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
            slot = flow.state()["allocations"][allocation_id]
            bound = flow.invoke("allocation", {"allocation_id": allocation_id,
                "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
            release = bound["next_action"]["result"]["write_release"]
            content = ("def " + relative.removesuffix(".py") + "():\n    return 1\n").encode()
            (flow.flow / relative).write_bytes(content)
            result = {"changed_paths": [relative],
                "file_hashes": {relative: hashlib.sha256(content).hexdigest()},
                "tests": ["behavior passed"], "write_release": release,
                "plan_refs": execution["semantic"]["plan_refs"],
                "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
            flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
                "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
                "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                           "payload": result}})
            delivered = flow.invoke("allocation", {"allocation_id": allocation_id,
                "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
            self.assertEqual(delivered["next_action"]["result"]["status"], "accepted")
            accepted.append(allocation_id)
        report = self.report(flow)
        report["report"].update(step_ids=["FP1-01"], dependency_impact=["FP1-01", "FP1-02",
            "FP1-04", "FP1-05"], verification_impact=["V1", "V2"],
            carry_forward=["FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("retain the public behavior with revised internals")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise accepted writer", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        retention = flow.state()["control"]["context"]["handoff_progress"]
        self.assertEqual({item["paths"][0] for item in retention["carried_results"]},
                         {"independent.py"})
        self.assertEqual(set(retention["revalidation_source"]["pending_paths"]),
                         {"impl.py", "later.py", "downstream.py", "reader.py"})

    def integration_after_plan_revision(self, *, retained_note):
        flow = self.new_active_flow(retained_note=retained_note)
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("revise private helper sequence")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "Controller plan revision", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated")
        execution = flow.execution_input()
        allocated = flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "prepare", "handoff": execution})
        self.assertEqual(allocated["next_action"]["operation"], "invoke-host", allocated)
        slot = flow.state()["allocations"]["integrated-implementation"]
        bound = flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "bind", "receipt": flow.receipt(slot["record"], ref="native:integrated")})
        release = bound["next_action"]["result"]["write_release"]
        implementation = b"def prepare():\n    return 'current'\ndef execute():\n    return prepare()\n"
        (flow.flow / "impl.py").write_bytes(implementation)
        result = {"changed_paths": ["impl.py"],
            "file_hashes": {"impl.py": hashlib.sha256(implementation).hexdigest()},
            "tests": ["current behavior passed"], "write_release": release,
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "receive", "receipt": flow.receipt(slot["record"], "stopped", "result",
            ref="native:integrated"), "result": {"delivery_id": "integrated-result",
            "status": "completed", "payload": result}})
        accepted = flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "accept", "decision": {"reference": "dispatcher:accepted"}})
        self.assertEqual(accepted["next_action"]["result"]["status"], "accepted")
        flow.flow_git("add", "impl.py")
        flow.flow_git("commit", "-qm", "accepted current implementation")
        accepted_commit = flow.flow_git("rev-parse", "HEAD")
        if retained_note:
            source.write_text(source.read_text().replace("Initial note.", "Closure note."))
            flow.flow_git("add", "docs/spec.md")
            flow.flow_git("commit", "-qm", "closure private inventory note")
        retained_commit = flow.flow_git("rev-parse", "HEAD")
        retained_paths = ["docs/spec.md"] if retained_note else []
        (flow.root / "parallel.md").write_text("parallel target work\n")
        flow.git("add", "parallel.md")
        flow.git("commit", "-qm", "parallel target work")
        target = flow.git("rev-parse", "main")
        flow.flow_git("merge", "--no-edit", target)
        context = copy.deepcopy(flow.state()["control"]["context"])
        checkpoint = context["handoff_progress"]
        checkpoint.update(integration_candidate=retained_commit, integration_target_head=target,
            integration_retained={"commit": retained_commit, "paths": retained_paths},
            validation_history=[{"candidate": accepted_commit}])
        self.assertEqual(checkpoint["plan_authority"]["revision"], 2)
        self.assertEqual(checkpoint["git_baseline_commit"],
                         flow.state()["handoff"]["scope"]["baseline"])

        def control(action, evidence, current=context):
            command = Path(progress.control_git.__file__)
            response = subprocess.run([sys.executable, str(command)], input=json.dumps({
                "repository": str(flow.flow), "baseline": checkpoint["git_baseline_commit"],
                "request": {"schema_version": current["schema_version"], "action": action,
                    "actor_ref": current["controller_ref"], "context": current,
                    "evidence": evidence}}), capture_output=True, text=True)
            self.assertTrue(response.stdout, response.stderr)
            return json.loads(response.stdout)

        captured = control("capture-integration", {"reference": "controller:target-race"})
        self.assertTrue(captured["ok"], captured)
        integration = captured["context"]["handoff_progress"]["integration_source"]
        self.assertEqual(integration["pending_paths"], ["impl.py"])
        current = captured["context"]
        current_progress = current["handoff_progress"]
        group = progress.execution_plan.validate_plan_refs(
            progress.execution_plan.current_catalog(current_progress["execution_authority"]),
            execution["semantic"]["plan_refs"], ["impl.py"],
            current_progress["protected_paths"], [], current_progress["execution_authority"])
        evidence = {"task_id": "adopt-current", "paths": ["impl.py"],
            "read_only": current["handoff_progress"]["protected_paths"],
            "behavior": "revalidate integrated implementation", "tests": group["verification_commands"],
            "git_operations": [], "configuration": execution["configuration"],
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": current["handoff_progress"]["plan_authority"],
            "group_digest": group["group_digest"],
            "adopt_paths": ["impl.py"],
            "adoption_snapshot_digest": integration["snapshot_digest"]}
        planned = control("plan-execution", evidence, current)
        self.assertTrue(planned["ok"], planned)

        source.write_text(source.read_text() + "external planning byte\n")
        self.assertFalse(control("capture-integration", {"reference": "controller:target-race"})["ok"])
        source.write_text(source.read_text().replace("external planning byte\n", ""))
        source.chmod(0o755)
        self.assertFalse(control("capture-integration", {"reference": "controller:target-race"})["ok"])
        source.chmod(0o644)
        source.write_text(source.read_text() + "index-only change\n")
        flow.flow_git("add", "docs/spec.md")
        source.write_text(source.read_text().replace("index-only change\n", ""))
        self.assertFalse(control("capture-integration", {"reference": "controller:target-race"})["ok"])

    def test_capture_integration_excludes_verified_controller_planning_delta(self):
        self.integration_after_plan_revision(retained_note=False)

    def test_capture_integration_accepts_current_planning_and_retained_note_only(self):
        self.integration_after_plan_revision(retained_note=True)

    def test_artifact_only_and_mixed_reports_revise_at_controller_git_boundary(self):
        for mode in ("only", "mixed"):
            with self.subTest(mode=mode):
                flow = self.new_active_flow(artifact_mode=mode)
                report = self.report(flow)
                current_ids = ["ART-1"] if mode == "only" else ["FP1-01", "ART-1"]
                report["report"]["step_ids"] = current_ids
                report["report"]["dependency_impact"] = current_ids
                first = flow.invoke("prepare-plan-revision", report)
                self.assertEqual(first["next_action"]["operation"], "stop-host", first)
                prepared = flow.invoke("observe", flow.observation(
                    "stopped", "result", ref="native:dispatcher"))
                self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
                spec = flow.flow / "docs/spec.md"
                body = spec.read_text()
                match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
                plan = json.loads(match.group(1))
                plan["plan_version"] = 2
                plan["artifact_steps"][0]["approach"] = "write the revised private prompt"
                spec.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                                .replace("Plan Version: 1", "Plan Version: 2"))
                flow.flow_git("add", "docs/spec.md")
                flow.flow_git("commit", "--only", "-qm", "revise artifact plan", "--", "docs/spec.md")
                outcome = flow.invoke("commit-plan-revision", {
                    "revision_id": report["revision_id"], "controller_ref": "task",
                    "write_release": prepared["next_action"]["write_release"],
                    "planning_commit": flow.flow_git("rev-parse", "HEAD")})
                self.assertEqual(outcome["next_action"]["operation"], "plan-revision-activated")
                self.assertEqual(flow.state()["scope_authority"]["revision"], 2)

    def test_retired_artifact_id_cannot_be_reused_after_intermediate_revision(self):
        flow = self.new_active_flow(artifact_mode="mixed")
        for number in (1, 2, 3):
            report = self.report(flow)
            report["revision_id"] = f"retired-artifact-{number}"
            report["classification"]["reference"] = f"controller:retired-artifact-{number}"
            report["report"]["step_ids"] = ["ART-1"]
            report["report"]["dependency_impact"] = ["ART-1"]
            prepared = flow.invoke("prepare-plan-revision", report)
            if prepared["next_action"]["operation"] == "stop-host":
                prepared = flow.invoke("observe", flow.observation(
                    "stopped", "result", ref="native:dispatcher"))
            self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
            spec = flow.flow / "docs/spec.md"
            body = spec.read_text()
            match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
            plan = json.loads(match.group(1))
            plan["plan_version"] += 1
            if number in (1, 3):
                artifact = copy.deepcopy(plan["artifact_steps"][0])
                artifact["id"] = "ART-retired"
                plan["artifact_steps"].append(artifact)
            else:
                plan["artifact_steps"] = [item for item in plan["artifact_steps"]
                                          if item["id"] != "ART-retired"]
            spec.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace(f"Plan Version: {number}", f"Plan Version: {number + 1}"))
            flow.flow_git("add", "docs/spec.md")
            flow.flow_git("commit", "--only", "-qm", f"retire artifact revision {number}",
                          "--", "docs/spec.md")
            before = flow.state()["scope_authority"]["revision"]
            request = {"revision_id": report["revision_id"], "controller_ref": "task",
                       "write_release": prepared["next_action"]["write_release"],
                       "planning_commit": flow.flow_git("rev-parse", "HEAD")}
            if number < 3:
                outcome = flow.invoke("commit-plan-revision", request)
                self.assertEqual(outcome["next_action"]["operation"], "plan-revision-activated")
                self.assertEqual(flow.state()["scope_authority"]["revision"], before + 1)
            else:
                with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
                    flow.invoke("commit-plan-revision", request)
                self.assertEqual(error.exception.code, "execution_plan_incomplete")
                self.assertEqual(flow.state()["scope_authority"]["revision"], before)

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

    def test_only_planning_commit_activates_one_current_plan_and_preserves_unrelated_index(self):
        flow = self.new_active_flow()
        (flow.flow / "unrelated.txt").write_text("existing staged note\n")
        flow.flow_git("add", "unrelated.txt")
        before_index = progress.control_git.plan_revision_snapshot(flow.flow,
            flow.state()["scope_authority"], flow.state()["handoff"]["scope"]["protected_paths"],
            ["docs/spec.md"])["index_entries"]["unrelated.txt"]
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
            ["docs/spec.md"])["index_entries"]["unrelated.txt"], before_index)
        for operation, data in (("prepare-plan-revision", report),
                                ("commit-plan-revision", {"revision_id": report["revision_id"],
                                    "controller_ref": "task", "write_release": release,
                                    "planning_commit": candidate}),
                                ("reconcile-plan-revision", {"revision_id": report["revision_id"]})):
            replay = flow.invoke(operation, data, revision=0)
            self.assertTrue(replay["acknowledged"], replay)
            self.assertNotIn("write_release", replay["next_action"])
            self.assertEqual(flow.flow_git("rev-parse", "HEAD"), candidate)

    def test_retired_function_id_cannot_be_reused_after_intermediate_revision(self):
        flow = self.new_active_flow()
        for number in (1, 2, 3):
            report = self.report(flow)
            report['revision_id'] = f'retired-id-{number}'
            report['classification']['reference'] = f'controller:retired-id-{number}'
            prepared = flow.invoke('prepare-plan-revision', report)
            if prepared['next_action']['operation'] == 'stop-host':
                prepared = flow.invoke('observe', flow.observation('stopped', 'result', ref='native:dispatcher'))
            self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
            spec = flow.flow / 'docs/spec.md'
            body = spec.read_text()
            match = re.search(r'```execution-plan-v1\n(.*?)\n```', body, re.DOTALL)
            plan = json.loads(match.group(1))
            plan['plan_version'] += 1
            if number in (1, 3):
                helper = copy.deepcopy(plan['steps'][0])
                helper['id'] = 'FP1-retired-helper'
                helper['target']['symbol'] = 'retired_helper'
                helper['depends_on'] = ['FP1-01']
                plan['steps'].append(helper)
            else:
                plan['steps'] = [step for step in plan['steps'] if step['id'] != 'FP1-retired-helper']
            spec.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace(f'Plan Version: {number}', f'Plan Version: {number + 1}'))
            flow.flow_git('add', 'docs/spec.md')
            flow.flow_git('commit', '--only', '-qm', f'retire function revision {number}', '--', 'docs/spec.md')
            before = flow.state()['scope_authority']['revision']
            commit_request = {'revision_id': report['revision_id'],
                'controller_ref': 'task', 'write_release': prepared['next_action']['write_release'],
                'planning_commit': flow.flow_git('rev-parse', 'HEAD')}
            if number < 3:
                outcome = flow.invoke('commit-plan-revision', commit_request)
                self.assertEqual(outcome['next_action']['operation'], 'plan-revision-activated', outcome)
                self.assertEqual(flow.state()['scope_authority']['revision'], before + 1)
            else:
                with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
                    flow.invoke('commit-plan-revision', commit_request)
                self.assertEqual(error.exception.code, 'execution_plan_incomplete')
                self.assertEqual(flow.state()['scope_authority']['revision'], before)

    def test_inline_revision_b_delivery_reads_current_controller_plan_and_n5(self):
        flow = fixture.fixture.ProgressTests(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.begin_dispatcher()
        authority = flow.state()['scope_authority']
        original_b0 = copy.deepcopy(fixture.fixture.transfer.handoff.unseal(flow.state()['handoff']))
        original_b0.pop('inline_authority')
        original_b0['semantic'].pop('execution_plan')
        self.assertEqual(authority['execution_plan']['sources'][0]['input_digest'],
                         fixture.fixture.transfer.entry.digest(original_b0))
        plan = copy.deepcopy(authority['execution_plan']['catalog']['tickets'][0]['plan'])
        plan['plan_version'] += 1
        plan['steps'][0]['approach'].append('Use the revised private helper sequence')
        helper_step = copy.deepcopy(plan['steps'][0])
        helper_step['id'] = 'inline-private-helper'
        helper_step['target'] = {'path': 'helper.py', 'symbol': 'helper', 'operation': 'add'}
        helper_step['depends_on'] = [plan['steps'][0]['id']]
        plan['steps'].append(helper_step)
        old_manifest = authority['scope']['downstream']['manifest']
        helper_manifest = copy.deepcopy(next(item for item in old_manifest if item['path'] == 'impl.py'))
        helper_manifest['path'] = 'helper.py'
        revised_manifest = sorted([*old_manifest, helper_manifest], key=lambda item: item['path'])
        validation = copy.deepcopy(flow.state()['control']['context']['handoff_progress']['validation_plan'])
        validation['review_required'][0]['command'] += ' revised'
        report = {'revision_id': 'inline-private-1',
            'expected_authority_revision': authority['revision'],
            'expected_authority_digest': fixture.fixture.transfer.entry.digest(authority),
            'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:inline-private'},
            'report': {'ticket_ids': [plan['ticket_id']], 'step_ids': [plan['steps'][0]['id']],
                'reason': 'Reorder a private helper', 'production_facts': 'Same output and caller',
                'contract_before': authority['execution_plan']['contract_digest'],
                'contract_after': authority['execution_plan']['contract_digest'],
                'dependency_impact': [plan['steps'][0]['id']],
                'verification_impact': [plan['verifications'][0]['id']],
                'carry_forward': [], 'manifest_diff': progress.stage_scope.manifest_diff(
                    old_manifest, revised_manifest)}, 'planning_paths': [],
            'manifest': revised_manifest, 'inline_plan': plan, 'validation_plan': validation}
        flow.invoke('prepare-plan-revision', report)
        prepared = flow.invoke('observe', flow.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        activated = flow.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
            'controller_ref': 'task', 'write_release': prepared['next_action']['write_release'],
            'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated', activated)
        revised = flow.state()['scope_authority']['execution_plan']
        self.assertEqual(revised['original_scope_revision'], authority['execution_plan']['original_scope_revision'])
        self.assertIsNone(revised['plan_commit'])
        self.assertEqual(revised['sources'][0]['input_digest'],
                         authority['execution_plan']['sources'][0]['input_digest'])
        port = {'record': flow.state()['dispatch'], 'control': copy.deepcopy(flow.state()['control'])}
        progress.dispatch.record_for(port)
        self.assertEqual(port['_current_stage3_progress']['plan_authority']['revision'], 2)
        self.assertEqual(port['_current_stage3_progress']['validation_plan'], validation)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        allocation_input = flow.execution_input()
        allocation_input['scope']['owned_paths'] = ['helper.py', 'impl.py']
        allocation_input['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            allocation_input['scope'])
        ticket = flow.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        allocation_input['semantic']['plan_refs'].append(progress.execution_plan.step_ref(ticket, helper_step))
        prepared_allocation = flow.invoke('allocation', {'allocation_id': 'inline-current', 'operation': 'prepare',
            'handoff': allocation_input})
        self.assertIsNotNone(prepared_allocation['next_action'], prepared_allocation)
        self.assertEqual(prepared_allocation['next_action']['operation'], 'invoke-host', prepared_allocation)
        slot = flow.state()['allocations']['inline-current']
        payload = slot['record']['request']['payload']
        self.assertEqual(payload['execution_plan']['sources'][0]['input_digest'],
                         authority['execution_plan']['sources'][0]['input_digest'])
        self.assertNotEqual(payload['input_digest'], payload['execution_plan']['sources'][0]['input_digest'])
        bound = flow.invoke('allocation', {'allocation_id': 'inline-current', 'operation': 'bind',
            'receipt': flow.receipt(slot['record'], ref='native:inline-agent')})
        release = bound['next_action']['result']['write_release']
        implementation = b"print('revised inline behavior')\n"
        helper_bytes = b"def helper():\n    return 'revised inline behavior'\n"
        (flow.flow / 'impl.py').write_bytes(implementation)
        (flow.flow / 'helper.py').write_bytes(helper_bytes)
        slot = flow.state()['allocations']['inline-current']
        result_payload = {'changed_paths': ['helper.py', 'impl.py'],
            'file_hashes': {'impl.py': hashlib.sha256(implementation).hexdigest(),
                            'helper.py': hashlib.sha256(helper_bytes).hexdigest()},
            'tests': ['revised behavior passed'], 'write_release': release,
            'plan_refs': allocation_input['semantic']['plan_refs'],
            'plan_authority': flow.state()['control']['context']['handoff_progress']['plan_authority']}
        flow.invoke('allocation', {'allocation_id': 'inline-current', 'operation': 'receive',
            'receipt': flow.receipt(slot['record'], 'stopped', 'result', ref='native:inline-agent'),
            'result': {'delivery_id': 'inline-current-result', 'status': 'completed',
                       'payload': result_payload}})
        accepted_result = flow.invoke('allocation', {'allocation_id': 'inline-current',
            'operation': 'accept', 'decision': {'reference': 'dispatcher:inline-current'}})
        self.assertEqual(accepted_result['next_action']['result']['status'], 'accepted')
        flow.request = controller_request
        flow.settings = controller_settings
        flow.flow_git('add', 'impl.py', 'helper.py')
        flow.flow_git('commit', '-qm', 'revised inline implementation')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.invoke('observe', flow.observation('idle', 'result', ref='native:dispatcher'))
        checkpoint = flow.state()['control']['context']['handoff_progress']
        checks = fixture.fixture.transfer.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        ready = flow.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': flow.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
            'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks},
            'receipt': {'adapter': 'fixture', 'call_ref': 'focused-inline',
                'response_ref': 'focused-inline-result', 'raw': {'checks': checks,
                    'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(flow.state()['control']['context']['handoff_progress']['state'], 'reviewable', ready)
        flow.mark_reviewable = lambda _candidate, fixture_execution=False: {
            'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks}
        payload = {'artifacts': ['impl.py', 'helper.py'], 'checks': ['revised behavior passed'],
            'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': checks}}
        accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        self.assertEqual(flow.state()['accepted']['payload']['verification']['validation_plan'], validation)

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
        self.assertIn("index stages or flags changed", str(error.exception))
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
