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
                        artifact_mode=None, retained_note=False, ticket=False):
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
            if ticket:
                manifest.append({"path": "docs/issues/FEP-01.md", "owner": "closure",
                    "kind": "document", "operation": "add", "basis": {"commit": candidate,
                        "path": "docs/issues/FEP-01.md", "section": "approved design"}})
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
            def planning_with_retained_note(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
                source = case.flow.flow / "docs/spec.md"
                source.write_text(source.read_text() + "### N4 — Private inventory\nInitial note.\n")
                case.flow.flow_git("add", "docs/spec.md")
                case.flow.flow_git("commit", "-qm", "private inventory note")
                return case.flow.flow_git("rev-parse", "HEAD")
            case.planning_candidate = planning_with_retained_note
        if artifact_mode is not None:
            original_candidate = case.planning_candidate
            def planning_with_artifact(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
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
            def planning_with_read_dependency(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
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
        if ticket:
            case.flow.input['scope']['owned_paths'] = ['docs/issues/FEP-01.md', 'docs/spec.md']
            case.flow.input['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
                case.flow.input['scope'])
            original_candidate = case.planning_candidate
            def planning_with_ticket(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
                issue = case.flow.flow / 'docs/issues/FEP-01.md'
                issue.parent.mkdir(parents=True, exist_ok=True)
                issue.write_text((case.flow.flow / 'docs/spec.md').read_text() +
                                 '\n## Comments\nInitial implementation note.\n')
                case.flow.flow_git('add', 'docs/issues/FEP-01.md')
                case.flow.flow_git('commit', '-qm', 'publish Ticket plan')
                return case.flow.flow_git('rev-parse', 'HEAD')
            case.planning_candidate = planning_with_ticket
        case.finish_planned_design()
        predecessor = case.flow.state()["accepted"]
        authority = case.flow.state()["scope_authority"]
        request = case.flow.input_for(3, predecessor)
        request["scope"]["downstream"] = copy.deepcopy(authority["scope"]["downstream"])
        request["scope"]["implementation_paths"] = copy.deepcopy(authority["scope"]["implementation_paths"])
        request["scope"]["closure_paths"] = copy.deepcopy(authority["scope"]["closure_paths"])
        if ticket:
            request["scope"]["protected_paths"] = sorted(set(request["scope"]["protected_paths"] +
                                                              ["docs/issues/FEP-01.md"]))
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
            "planning_paths": [authority['execution_plan']['catalog']['tickets'][0]['source']['path']]}

    def test_revision_entries_reject_non_string_ids_without_changing_state(self):
        flow = self.new_active_flow()
        before = flow.checkpoint.read_bytes()
        head = flow.flow_git("rev-parse", "HEAD")
        for operation in ("prepare-plan-revision", "commit-plan-revision",
                          "reconcile-plan-revision"):
            for invalid in ([], {}, "", "  "):
                with self.subTest(operation=operation, invalid=invalid):
                    if operation == "prepare-plan-revision":
                        request = self.report(flow)
                    elif operation == "commit-plan-revision":
                        request = {"revision_id": "internal-1", "controller_ref": "task",
                                   "write_release": "unused", "planning_commit": head}
                    else:
                        request = {"revision_id": "internal-1"}
                    request["revision_id"] = invalid
                    with self.assertRaises(progress.entry.PreparationError) as error:
                        flow.invoke(operation, request)
                    self.assertEqual(error.exception.code, "invalid_request")
                    self.assertIn("revision_id", str(error.exception))
                    self.assertEqual(flow.checkpoint.read_bytes(), before)
                    self.assertEqual(flow.flow_git("rev-parse", "HEAD"), head)

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

    def accept_planned_step(self, flow, ticket, identity, relative, content, allocation_id,
                            *, accept=True):
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
        if content is None:
            (flow.flow / relative).unlink()
        else:
            (flow.flow / relative).write_bytes(content)
        result = {"changed_paths": [relative],
            "file_hashes": {relative: None if content is None else hashlib.sha256(content).hexdigest()},
            "tests": ["behavior passed"], "write_release": release,
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
            "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
            "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                       "payload": result}})
        if not accept:
            return
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

        if retained_note:
            malformed = copy.deepcopy(context)
            malformed_catalog = malformed["handoff_progress"]["execution_authority"][
                "execution_plan"]["catalog"]
            self.assertIsNotNone(malformed_catalog["commit"])
            del malformed_catalog["sources"]
            self.assertFalse(control("capture-integration", {"reference": "controller:target-race"},
                                     malformed)["ok"])
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

    def test_final_integration_snapshot_supersedes_carried_and_revision_bytes(self):
        self.final_integration_snapshot(ticket=False)

    def test_ticket_comments_and_spec_archive_survive_full_final_integration_recovery(self):
        self.final_integration_snapshot(ticket=True)

    def final_integration_snapshot(self, *, ticket):
        flow = self.new_active_flow(read_dependency=True, ticket=ticket)
        controller_thread = flow.state()["handoff"]["expected_entry"]["actor"]["thread_id"]
        ticket_record = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        for identity, relative in (("FP1-01", "impl.py"), ("FP1-02", "later.py"),
                                   ("FP1-03", "independent.py"),
                                   ("FP1-04", "downstream.py"), ("FP1-05", "reader.py")):
            self.accept_planned_step(flow, ticket_record, identity, relative,
                ("def " + relative.removesuffix(".py") + "():\n    return 1\n").encode(),
                "accepted-" + identity)
        report = self.report(flow)
        report["report"].update(step_ids=["FP1-02"],
            dependency_impact=["FP1-02", "FP1-04", "FP1-05"],
            verification_impact=["V2"], carry_forward=["FP1-01", "FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        planning_path = "docs/issues/FEP-01.md" if ticket else "docs/spec.md"
        planning = flow.flow / planning_path
        body = planning.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][1]["approach"].append("revise private implementation")
        planning.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", planning_path)
        flow.flow_git("commit", "--only", "-qm", "revise plan before final integration",
                      "--", planning_path)
        flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        ticket_record = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        progress_before = flow.state()["control"]["context"]["handoff_progress"]
        self.assertTrue(progress_before["carried_results"])
        self.assertTrue(progress_before["revalidation_source"]["pending_paths"])
        original_carry = copy.deepcopy(progress_before["carried_results"])
        original_revision = copy.deepcopy(progress_before["plan_revisions"])
        original_source = copy.deepcopy(progress_before["revalidation_source"])
        original_receipts = copy.deepcopy(progress_before["plan_revision_sources"])
        original_observation = flow.observation

        def with_historical_agents(*args, **kwargs):
            evidence = original_observation(*args, **kwargs)
            if "lifecycle" in evidence:
                known = {item["id"] for item in evidence["lifecycle"]["pages"][0]["response"]["data"]}
                evidence["lifecycle"]["pages"][0]["response"]["data"].extend(
                    {"id": ref, "status": {"type": "idle"}}
                    for ref in {item["agent_ref"] for source in original_receipts
                                for item in source["executions"] if item["agent_ref"]} - known)
            return evidence

        for identity, relative in (("FP1-02", "later.py"),
                                   ("FP1-04", "downstream.py"), ("FP1-05", "reader.py")):
            self.accept_planned_step(flow, ticket_record, identity, relative,
                ("def " + relative.removesuffix(".py") + "():\n    return 2\n").encode(),
                "revalidated-" + identity)

        flow.flow_git("add", "impl.py", "later.py", "independent.py", "downstream.py", "reader.py")
        flow.flow_git("commit", "-qm", "accepted plan revision implementation")
        accepted = flow.flow_git("rev-parse", "HEAD")
        flow.settings["thread_id"] = controller_thread
        flow.request["host"].update(thread_id=controller_thread, role="controller", source_ref=None)
        flow.request["host"].pop("actor_ref", None)
        payload = {"artifacts": ["impl.py", "later.py", "independent.py", "downstream.py", "reader.py"],
            "checks": ["current plan verified"], "candidate_commit": accepted,
            "review": {axis: {"candidate": accepted, "reviewer_ref": axis, "status": "accepted"}
                       for axis in ("standards", "spec")},
            "verification": {"candidate": accepted}}
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            stage3 = flow.accept_current(payload, "native:dispatcher", fixture_execution=False)
        self.assertEqual(stage3["status"], "accepted", stage3)
        predecessor = flow.state()["accepted"]
        stage4_input = flow.input_for(4, predecessor)
        current_scope = flow.state()['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            stage4_input['scope'][key] = copy.deepcopy(current_scope[key])
        stage4_input['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            stage4_input['scope'])
        self.assertEqual(progress.handoff.prepare(stage4_input)["stage"], 4)
        original_checkpoint = flow.checkpoint.read_bytes()
        altered = progress.read_record(flow.checkpoint)
        altered[progress.KEY]["control"]["context"]["handoff_progress"]["plan_revisions"][-1]["evidence"]["planning_commit"] = accepted
        try:
            progress.atomic_save(flow.checkpoint, altered)
            with self.assertRaises((progress.entry.PreparationError,
                                    progress.control_git.ControlError)):
                progress.handoff.prepare(stage4_input)
        finally:
            flow.checkpoint.write_bytes(original_checkpoint)
        self.assertEqual(progress.handoff.prepare(stage4_input)["stage"], 4)
        current_progress = flow.state()["control"]["context"]["handoff_progress"]
        requirement = flow.flow / flow.state()["handoff"]["requirement_identity"]["path"]
        original_requirement = requirement.read_bytes()
        try:
            requirement.write_bytes(original_requirement + b"unauthorized requirement change\n")
            with self.assertRaises((progress.entry.PreparationError,
                                    progress.control_git.ControlError)):
                progress.handoff.prepare(stage4_input)
        finally:
            requirement.write_bytes(original_requirement)
        original_planning = planning.read_bytes()
        try:
            planning.write_bytes(original_planning + b"unapproved planning change\n")
            with self.assertRaises(progress.control_git.ControlError):
                progress.control_git.verified_planning_paths(flow.flow, current_progress)
        finally:
            planning.write_bytes(original_planning)
        try:
            planning.chmod(0o755)
            with self.assertRaises(progress.control_git.ControlError):
                progress.control_git.verified_planning_paths(flow.flow, current_progress)
        finally:
            planning.chmod(0o644)
        try:
            planning.write_bytes(original_planning + b"index-only planning change\n")
            flow.flow_git("add", planning_path)
            planning.write_bytes(original_planning)
            with self.assertRaises(progress.control_git.ControlError):
                progress.control_git.verified_planning_paths(flow.flow, current_progress)
        finally:
            flow.flow_git("reset", "-q", "--", planning_path)
            planning.write_bytes(original_planning)
        (flow.root / "impl.py").write_text("def impl():\n    return 99\n")
        (flow.root / "later.py").write_text("def later():\n    return 99\n")
        flow.git("add", "impl.py", "later.py")
        flow.git("commit", "-qm", "parallel target changes implementation")
        target = flow.git("rev-parse", "main")
        flow.context = flow.context_for(4)
        flow.begin("continuous", stage4_input)
        flow.invoke("observe", flow.observation(ref="native:closure"))
        if ticket:
            issue = flow.flow / planning_path
            issue.write_text(issue.read_text() + 'Verification: Stage 4 accepted.\n')
            spec = flow.flow / 'docs/spec.md'
            spec.write_text(spec.read_text() + '\n## Archive Notes\nStage 4 review accepted.\n')
            flow.flow_git('add', planning_path, 'docs/spec.md')
            flow.flow_git('commit', '-qm', 'retain Ticket and Spec closure notes')
        observed = flow.observation("stopped", "result", ref="native:closure")
        observed["closure"] = {"ref": "native:closure", "candidate": accepted,
            "merge": None, "ancestor_verified": False, "changed_paths": [],
            "binding": flow.binding, "checks": [], "worktree_removed": False,
            "branch_removed": False, "disposition": "deleted", "disposition_verified": False,
            "implementation_problem": "target changed implementation bytes"}
        blocked = flow.invoke("observe", observed)
        self.assertEqual(blocked["next_action"]["operation"], "implementation-recovery")
        recovery = flow.invoke("implementation-recovery", {"reference": "controller:target-race",
            "closure_digest": flow.state()["handoff"]["digest"]})
        self.assertEqual(flow.state()["stage"], 3, recovery)
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        merge = subprocess.run(["git", "-C", str(flow.flow), "merge", "--no-edit", target],
                               capture_output=True, text=True)
        self.assertNotEqual(merge.returncode, 0, merge.stdout)
        (flow.flow / "impl.py").write_bytes(b"def impl():\n    return 3\n")
        (flow.flow / "later.py").write_bytes(b"def later():\n    return 4\n")
        flow.flow_git("add", "impl.py", "later.py")
        flow.flow_git("commit", "-qm", "resolve final integration")
        integrated_candidate = flow.flow_git("rev-parse", "HEAD")
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        if ticket:
            progress.control_git.verified_planning_paths(
                flow.flow, flow.state()['control']['context']['handoff_progress'])
        captured = flow.invoke("control", {"action": "capture-integration",
            "evidence": {"reference": "controller:target-race"},
            "receipt": {"controller_ref": "task", "reference": "controller:target-race"}})
        self.assertNotEqual(captured["status"], "blocked", captured)
        current_progress = flow.state()["control"]["context"]["handoff_progress"]
        source = current_progress["integration_source"]
        self.assertEqual(current_progress["carried_results"], original_carry)
        self.assertEqual(current_progress["plan_revisions"], original_revision)
        self.assertEqual(current_progress["revalidation_source"], original_source)
        self.assertEqual(current_progress["plan_revision_sources"], original_receipts)
        self.assertTrue({"impl.py", "later.py"} <= set(source["pending_paths"]))
        carried_impl = next(result for result in original_carry if "impl.py" in result["paths"])
        self.assertNotEqual(source["snapshot"]["files"]["impl.py"],
                            carried_impl["owned_final_fingerprints"]["impl.py"])
        self.assertNotEqual(source["snapshot"]["files"]["later.py"],
                            original_source["snapshot"]["files"]["later.py"])
        self.assertEqual(source["snapshot"]["files"]["impl.py"],
                         progress.control_git.file_fingerprint(flow.flow, "impl.py"))

        if ticket:
            with patch.dict(flow.settings), patch.dict(flow.request["host"]):
                protected_input = flow.execution_input()
                protected_input["semantic"].update(
                    adopt_paths=["impl.py"], adoption_snapshot_digest=source["snapshot_digest"])
                protected_handoff = progress.handoff.handle(protected_input)
            current_control = flow.state()["control"]["context"]

            def reject_protected(control_context=current_control, handoff=protected_handoff):
                with self.assertRaises((progress.entry.PreparationError,
                                        progress.control_git.ControlError)):
                    progress.dispatch.handle({"protocol": progress.handoff.PROTOCOL,
                        "operation": "prepare", "handoff": handoff,
                        "control": {"context": control_context, "discussion": None}})

            forged = copy.deepcopy(current_control)
            forged["handoff_progress"]["integration_retained"]["commit"] = integrated_candidate
            forged["handoff_progress"]["integration_candidate"] = integrated_candidate
            reject_protected(forged)
            original_ticket = planning.read_bytes()
            planning.write_bytes(original_ticket + b"unactivated planning commit\n")
            flow.flow_git("add", planning_path)
            flow.flow_git("commit", "-qm", "unactivated planning change")
            try:
                reject_protected()
            finally:
                flow.flow_git("reset", "--hard", integrated_candidate)
            planning.write_bytes(original_ticket + b"unapproved public or plan bytes\n")
            try:
                reject_protected()
            finally:
                planning.write_bytes(original_ticket)
            planning.chmod(0o755)
            try:
                reject_protected()
            finally:
                planning.chmod(0o644)
            planning.write_bytes(original_ticket + b"index-only planning bytes\n")
            flow.flow_git("add", planning_path)
            planning.write_bytes(original_ticket)
            try:
                reject_protected()
            finally:
                flow.flow_git("reset", "-q", "--", planning_path)
            requirement.write_bytes(original_requirement + b"unapproved requirement bytes\n")
            try:
                reject_protected()
            finally:
                requirement.write_bytes(original_requirement)

        def reaccept(identity, relative):
            step = next(item for item in ticket_record["plan"]["steps"] if item["id"] == identity)
            with patch.dict(flow.settings), patch.dict(flow.request["host"]):
                execution = flow.execution_input()
                execution["scope"]["owned_paths"] = [relative]
                execution["scope"]["protected_paths"] += step["read_only"]
                execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
                    execution["scope"])
                execution["semantic"].update(
                    plan_refs=[progress.execution_plan.step_ref(ticket_record, step)],
                    adopt_paths=[relative], adoption_snapshot_digest=source["snapshot_digest"])
                allocation_id = "integrated-" + identity
                prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
                    "operation": "prepare", "handoff": execution})
                self.assertIsNotNone(prepared["next_action"], prepared)
                self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
                slot = flow.state()["allocations"][allocation_id]
                bound = flow.invoke("allocation", {"allocation_id": allocation_id,
                    "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
                release = bound["next_action"]["result"]["write_release"]
                content = (flow.flow / relative).read_bytes()
                result = {"changed_paths": [], "adopted_paths": [relative],
                    "file_hashes": {relative: hashlib.sha256(content).hexdigest()},
                    "tests": ["integrated behavior passed"], "write_release": release,
                    "plan_refs": execution["semantic"]["plan_refs"],
                    "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
                flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
                    "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
                    "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                               "payload": result}})
                accepted_result = flow.invoke("allocation", {"allocation_id": allocation_id,
                    "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
                self.assertEqual(accepted_result["next_action"]["result"]["status"], "accepted")

        for identity, relative in (("FP1-01", "impl.py"), ("FP1-02", "later.py"),
                                   ("FP1-03", "independent.py"),
                                   ("FP1-04", "downstream.py"), ("FP1-05", "reader.py")):
            reaccept(identity, relative)
        final_progress = flow.state()["control"]["context"]["handoff_progress"]
        self.assertEqual(final_progress["carried_results"], original_carry)
        self.assertEqual(final_progress["revalidation_source"], original_source)
        self.assertEqual(final_progress["plan_revision_sources"], original_receipts)
        payload["candidate_commit"] = integrated_candidate
        payload["review"] = {axis: {"candidate": integrated_candidate,
            "reviewer_ref": "integrated-" + axis, "status": "accepted"}
            for axis in ("standards", "spec")}
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            replaced = flow.accept_current(payload, "native:dispatcher", fixture_execution=False)
        self.assertEqual(replaced["status"], "accepted", replaced)
        self.assertNotEqual(flow.state()["accepted"]["digest"], predecessor["digest"])
        after_integration = self.report(flow)
        after_integration["revision_id"] = "after-final-integration"
        after_integration["classification"]["reference"] = "controller:after-final-integration"
        saved_checkpoint = flow.checkpoint.read_bytes()
        saved_head = flow.flow_git("rev-parse", "HEAD")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("prepare-plan-revision", after_integration)
        self.assertEqual(error.exception.code, "plan_revision_unavailable")
        self.assertEqual(flow.checkpoint.read_bytes(), saved_checkpoint)
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), saved_head)

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
        generated_manifest = {'path': 'generated/helper_copy.py', 'owner': 'implementation',
            'kind': 'generated', 'operation': 'add', 'basis': copy.deepcopy(helper_manifest['basis']),
            'source_paths': ['helper.py']}
        revised_manifest = sorted([*old_manifest, helper_manifest, generated_manifest],
                                  key=lambda item: item['path'])
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
        report['generated_outputs'] = {'helper.py': ['generated/helper_copy.py']}
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
        allocation_input['scope']['owned_paths'] = ['generated/helper_copy.py', 'helper.py', 'impl.py']
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
        (flow.flow / 'generated').mkdir()
        (flow.flow / 'impl.py').write_bytes(implementation)
        (flow.flow / 'helper.py').write_bytes(helper_bytes)
        (flow.flow / 'generated/helper_copy.py').write_bytes(helper_bytes)
        slot = flow.state()['allocations']['inline-current']
        result_payload = {'changed_paths': ['generated/helper_copy.py', 'helper.py', 'impl.py'],
            'file_hashes': {'impl.py': hashlib.sha256(implementation).hexdigest(),
                            'helper.py': hashlib.sha256(helper_bytes).hexdigest(),
                            'generated/helper_copy.py': hashlib.sha256(helper_bytes).hexdigest()},
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
        flow.flow_git('add', 'impl.py', 'helper.py', 'generated/helper_copy.py')
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
        payload = {'artifacts': ['impl.py', 'helper.py', 'generated/helper_copy.py'], 'checks': ['revised behavior passed'],
            'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': checks}}
        accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        self.assertEqual(flow.state()['accepted']['payload']['verification']['validation_plan'], validation)
        predecessor = flow.state()['accepted']
        completion = progress.handoff.render(predecessor,
            checkpoint_path=str(flow.checkpoint))['payload']
        self.assertIn('helper.py', completion['implementation_paths'])
        self.assertEqual(completion['closure_paths'],
                         flow.state()['scope_authority']['scope']['closure_paths'])
        closure = flow.input_for(4, predecessor)
        current_scope = flow.state()['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            closure['scope'][key] = copy.deepcopy(current_scope[key])
        closure['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(closure['scope'])
        with self.assertRaises(progress.entry.PreparationError) as missing_pin:
            progress.handoff.prepare(closure)
        self.assertEqual(missing_pin.exception.code, 'handoff_not_ready')
        stale = copy.deepcopy(closure)
        stale['scope']['implementation_paths'] = predecessor['handoff']['scope']['implementation_paths']
        stale['scope']['downstream'] = predecessor['handoff']['scope']['downstream']
        stale['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(stale['scope'])
        before = flow.checkpoint.read_bytes()
        flow.context = flow.context_for(4)
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': stale, 'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        widened = copy.deepcopy(closure)
        widened['scope']['owned_paths'] = ['helper.py']
        widened['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(widened['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': widened, 'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        started = flow.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host')
        stage4 = flow.state()
        self.assertEqual(stage4['handoff']['scope']['implementation_paths'],
                         current_scope['implementation_paths'])
        self.assertEqual(stage4['handoff']['scope']['baseline'],
                         predecessor['handoff']['scope']['baseline'])
        self.assertEqual(stage4['dispatch']['scope_checkpoint_path'], str(flow.checkpoint))
        self.assertEqual(progress.handoff.verify(stage4['handoff'],
                         checkpoint_path=str(flow.checkpoint)), stage4['handoff'])
        forged = progress.handoff.unseal(stage4['dispatch'])
        forged['scope_checkpoint_path'] = str(flow.checkpoint.with_name('forged-progress.json'))
        forged = progress.handoff.seal(forged)
        with self.assertRaises(progress.entry.PreparationError):
            progress.dispatch.record_for({'record': forged,
                'control': copy.deepcopy(stage4['control'])})
        original_checkpoint = flow.checkpoint.read_bytes()
        changed = progress.read_record(flow.checkpoint)
        changed[progress.KEY]['history'][-1]['plan_revision_transaction']['state'] = 'prepared'
        try:
            progress.atomic_save(flow.checkpoint, changed)
            with self.assertRaises(progress.entry.PreparationError):
                progress.handoff.verify(stage4['handoff'], checkpoint_path=str(flow.checkpoint))
        finally:
            flow.checkpoint.write_bytes(original_checkpoint)

    def test_staged_private_helper_revision_reaches_current_stage4_handoff(self):
        flow = self.new_active_flow()
        authority = flow.state()['scope_authority']
        report = self.report(flow)
        manifest = copy.deepcopy(authority['scope']['downstream']['manifest'])
        helper = copy.deepcopy(next(item for item in manifest if item['path'] == 'impl.py'))
        helper['path'] = 'helper.py'
        helper['basis']['section'] = 'D1'
        generated = {'path': 'generated/helper_copy.py', 'owner': 'implementation',
            'kind': 'generated', 'operation': 'add', 'basis': copy.deepcopy(helper['basis']),
            'source_paths': ['helper.py']}
        manifest = sorted([*manifest, helper, generated], key=lambda item: item['path'])
        report['manifest'] = manifest
        report['generated_outputs'] = {'helper.py': ['generated/helper_copy.py']}
        report['report']['manifest_diff'] = progress.stage_scope.manifest_diff(
            authority['scope']['downstream']['manifest'], manifest)
        flow.invoke('prepare-plan-revision', report)
        prepared = flow.invoke('observe', flow.observation('stopped', 'result',
            ref='native:dispatcher'))
        planning = flow.flow / 'docs/spec.md'
        body = planning.read_text()
        match = re.search(r'```execution-plan-v1\n(.*?)\n```', body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan['plan_version'] += 1
        plan['steps'][0]['approach'].append('Use the new private helper')
        extra = copy.deepcopy(plan['steps'][0])
        extra['id'] = 'staged-private-helper'
        extra['target'] = {'path': 'helper.py', 'symbol': 'helper', 'operation': 'add'}
        extra['depends_on'] = [plan['steps'][0]['id']]
        plan['steps'].append(extra)
        planning.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace('Plan Version: 1', 'Plan Version: 2'))
        flow.flow_git('add', 'docs/spec.md')
        flow.flow_git('commit', '--only', '-qm', 'revise staged private helper',
                      '--', 'docs/spec.md')
        activated = flow.invoke('commit-plan-revision', {
            'revision_id': report['revision_id'], 'controller_ref': 'task',
            'write_release': prepared['next_action']['write_release'],
            'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated',
                         activated)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        ticket = flow.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        (flow.flow / 'generated').mkdir()
        self.accept_planned_group(flow, ticket, ['FP1-01', 'FP1-02', 'staged-private-helper'], {
            'impl.py': b"print('staged behavior')\n",
            'helper.py': b"def helper():\n    return 'staged behavior'\n",
            'generated/helper_copy.py': b"def helper():\n    return 'staged behavior'\n",
        }, 'staged-helper')
        flow.request = controller_request
        flow.settings = controller_settings
        flow.flow_git('add', 'impl.py', 'helper.py', 'generated/helper_copy.py')
        flow.flow_git('commit', '-qm', 'implement staged private helper')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.invoke('observe', flow.observation('idle', 'result', ref='native:dispatcher'))
        checkpoint = flow.state()['control']['context']['handoff_progress']
        checks = fixture.fixture.transfer.checks_for(
            checkpoint['validation_plan']['review_required'], candidate)
        ready = flow.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': flow.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
            'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks},
            'receipt': {'adapter': 'fixture', 'call_ref': 'focused-staged',
                'response_ref': 'focused-staged-result', 'raw': {'checks': checks,
                    'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(flow.state()['control']['context']['handoff_progress']['state'],
                         'reviewable', ready.get('error'))
        flow.mark_reviewable = lambda _candidate, fixture_execution=False: {
            'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks}
        payload = {'artifacts': ['impl.py', 'helper.py', 'generated/helper_copy.py'], 'checks': ['staged behavior passed'],
            'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate}}
        accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        predecessor = flow.state()['accepted']
        current_scope = flow.state()['scope_authority']['scope']
        closure = flow.input_for(4, predecessor)
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            closure['scope'][key] = copy.deepcopy(current_scope[key])
        closure['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            closure['scope'])
        flow.context = flow.context_for(4)
        before = flow.checkpoint.read_bytes()
        stale_map = copy.deepcopy(closure)
        generated_item = next(item for item in stale_map['scope']['downstream']['manifest']
                              if item['path'] == 'generated/helper_copy.py')
        generated_item['source_paths'] = ['impl.py']
        stale_map['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            stale_map['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': stale_map,
                'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        stale_basis = copy.deepcopy(closure)
        helper_item = next(item for item in stale_basis['scope']['downstream']['manifest']
                           if item['path'] == 'helper.py')
        helper_item['basis']['section'] = 'approved design'
        stale_basis['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            stale_basis['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': stale_basis,
                'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        started = flow.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host')
        stage4 = flow.state()
        self.assertEqual(stage4['dispatch']['scope_checkpoint_path'], str(flow.checkpoint))
        self.assertEqual(progress.handoff.verify(stage4['handoff'],
            checkpoint_path=str(flow.checkpoint)), stage4['handoff'])

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

    def test_plan_revision_cannot_change_ticket_comments_or_spec_archive(self):
        for ticket in (False, True):
            with self.subTest(ticket=ticket):
                flow = self.new_active_flow(ticket=ticket)
                report = self.report(flow)
                flow.invoke('prepare-plan-revision', report)
                prepared = flow.invoke('observe', flow.observation(
                    'stopped', 'result', ref='native:dispatcher'))
                relative = 'docs/issues/FEP-01.md' if ticket else 'docs/spec.md'
                source = flow.flow / relative
                body = source.read_text()
                match = re.search(r'```execution-plan-v1\n(.*?)\n```', body, re.DOTALL)
                plan = json.loads(match.group(1))
                plan['plan_version'] += 1
                plan['steps'][0]['approach'].append('revise private helper')
                body = (body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                body = body.replace('Plan Version: 1', 'Plan Version: 2')
                if ticket:
                    body = body.replace('Initial implementation note.',
                        'Changed existing implementation note.')
                else:
                    body += '\n## Archive Notes\nUnreviewed historical claim.\n'
                source.write_text(body)
                flow.flow_git('add', relative)
                flow.flow_git('commit', '--only', '-qm', 'private plan and record rewrite',
                              '--', relative)
                before = flow.checkpoint.read_bytes()
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
                        'controller_ref': 'task',
                        'write_release': prepared['next_action']['write_release'],
                        'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
                self.assertEqual(error.exception.code, 'contract_changed')
                self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_formal_revision_allows_private_n4_inventory_with_plan_change(self):
        flow = self.new_active_flow(retained_note=True)
        report = self.report(flow)
        flow.invoke('prepare-plan-revision', report)
        prepared = flow.invoke('observe', flow.observation(
            'stopped', 'result', ref='native:dispatcher'))
        source = flow.flow / 'docs/spec.md'
        body = source.read_text()
        match = re.search(r'```execution-plan-v1\n(.*?)\n```', body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan['plan_version'] += 1
        plan['steps'][0]['approach'].append('revise private helper')
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace('Plan Version: 1', 'Plan Version: 2')
                          .replace('Initial note.', 'Updated private inventory.'))
        flow.flow_git('add', 'docs/spec.md')
        flow.flow_git('commit', '--only', '-qm', 'revise plan and N4 inventory',
                      '--', 'docs/spec.md')
        activated = flow.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
            'controller_ref': 'task',
            'write_release': prepared['next_action']['write_release'],
            'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated',
                         activated)

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

    def test_retired_helper_with_accepted_bytes_cannot_receive_planning_release(self):
        flow = self.new_active_flow(read_dependency=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        authority = flow.state()["scope_authority"]
        ticket = authority["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "retired-input")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py",
                                 b"def later():\n    return 1\n", "retired-helper")
        flow.flow_git('add', 'impl.py', 'later.py')
        flow.flow_git('commit', '-qm', 'commit accepted helper implementation')
        implemented_helper = flow.flow_git('rev-parse', 'HEAD')
        flow.request = controller_request
        flow.settings = controller_settings
        old_manifest = authority["scope"]["downstream"]["manifest"]
        manifest = [item for item in old_manifest if item["path"] != "later.py"]
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"].update(step_ids=["FP1-02"],
                                dependency_impact=["FP1-04", "FP1-05"],
                                verification_impact=["V2"],
                                manifest_diff=progress.stage_scope.manifest_diff(old_manifest, manifest))
        requested = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(requested["next_action"]["operation"], "stop-host", requested)
        before = flow.flow_git("rev-parse", "HEAD")
        stopped = flow.observation("stopped", "result", ref="native:dispatcher")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("observe", stopped)
        self.assertEqual(error.exception.code, "invalid_plan_revision")
        self.assertIn("later.py", str(error.exception))
        self.assertEqual(flow.state()["scope_authority"], authority)
        self.assertIsNone(flow.state().get("plan_revision_transaction"))
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), before)
        self.assertIn("later.py", flow.state()["control"]["context"]["handoff_progress"]["allowed_paths"])
        with self.assertRaises(progress.entry.PreparationError) as missing:
            flow.invoke("reconcile-plan-revision", {"revision_id": report["revision_id"]})
        self.assertEqual(missing.exception.code, "plan_revision_missing")
        resumed = flow.invoke("advance")
        self.assertEqual(resumed["next_action"]["operation"], "inspect-host-state", resumed)
        repeat = flow.invoke("advance")
        self.assertEqual(repeat["next_action"]["action_id"], resumed["next_action"]["action_id"])
        resumed = flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        self.assertEqual(resumed["next_action"]["operation"], "continue-host", resumed)
        repeat = flow.invoke("advance")
        self.assertEqual(repeat["next_action"]["operation"], "lookup-exact-action", repeat)
        self.assertEqual(repeat["next_action"]["action"]["action_id"],
                         resumed["next_action"]["action_id"])
        flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py", None,
                                 "retired-helper-cleanup")
        self.assertFalse((flow.flow / "later.py").exists())
        flow.flow_git('add', '-u', 'later.py')
        flow.flow_git('commit', '-qm', 'commit accepted helper cleanup')
        cleaned_helper = flow.flow_git('rev-parse', 'HEAD')
        self.assertNotEqual(implemented_helper, cleaned_helper)
        flow.request = controller_request
        flow.settings = controller_settings
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared", prepared)
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"] = [step for step in plan["steps"] if step["id"] != "FP1-02"]
        next(step for step in plan["steps"] if step["id"] == "FP1-04")["depends_on"] = []
        next(step for step in plan["steps"] if step["id"] == "FP1-05")["read_only"] = []
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "retire baseline helper", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        self.assertNotIn("later.py", flow.state()["scope_authority"]["scope"]["implementation_paths"])
        self.assertNotIn("later.py", flow.state()["control"]["context"]["handoff_progress"]["allowed_paths"])
        flow.invoke('advance')
        flow.invoke('observe', flow.observation('running', 'result', ref='native:dispatcher'))
        current_ticket = flow.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        for identity, relative in (('FP1-03', 'independent.py'),
                                   ('FP1-04', 'downstream.py'), ('FP1-05', 'reader.py')):
            self.accept_planned_step(flow, current_ticket, identity, relative,
                (f'def {relative.removesuffix(".py")}():\n    return 2\n').encode(),
                'current-' + identity)
        flow.flow_git('add', 'independent.py', 'downstream.py', 'reader.py')
        flow.flow_git('commit', '-qm', 'commit current plan implementation')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.request = controller_request
        flow.settings = controller_settings
        flow.settings['thread_id'] = 'task'
        flow.request['host'].update(thread_id='task', role='controller', source_ref=None)
        flow.request['host'].pop('actor_ref', None)
        checkpoint = flow.state()['control']['context']['handoff_progress']
        checks = fixture.fixture.transfer.checks_for(
            checkpoint['validation_plan']['review_required'], candidate)
        evidence = {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': flow.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
            'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks,
            'source': {'adapter': 'fixture', 'call_ref': 'retired-helper-ready',
                'response_ref': 'retired-helper-ready-result',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': 'native:dispatcher'}}}
        ready = progress.control_git.verified_transition({'repository': str(flow.flow),
            'baseline': checkpoint['git_baseline_commit'], 'request': {
                'schema_version': 6, 'action': 'candidate-ready', 'actor_ref': 'task',
                'context': copy.deepcopy(flow.state()['control']['context']),
                'evidence': evidence}})
        self.assertTrue(ready['ok'], ready)
        self.assertEqual(ready['context']['handoff_progress']['state'], 'reviewable')
        (flow.flow / 'later.py').write_text('unauthorized retired helper\n')
        flow.flow_git('add', 'later.py')
        flow.flow_git('commit', '-qm', 'unaccepted retired helper rewrite')
        (flow.flow / 'later.py').unlink()
        flow.flow_git('add', '-u', 'later.py')
        flow.flow_git('commit', '-qm', 'revert unaccepted retired helper rewrite')
        forged_candidate = flow.flow_git('rev-parse', 'HEAD')
        forged_evidence = copy.deepcopy(evidence)
        forged_evidence['commit'] = forged_candidate
        forged_evidence['checks'] = fixture.fixture.transfer.checks_for(
            checkpoint['validation_plan']['review_required'], forged_candidate)
        forged_evidence['source']['raw']['checks'] = forged_evidence['checks']
        with self.assertRaisesRegex(progress.control.ControlError,
                                    'candidate history escapes implementation scope'):
            progress.control_git.verified_transition({'repository': str(flow.flow),
                'baseline': checkpoint['git_baseline_commit'], 'request': {
                    'schema_version': 6, 'action': 'candidate-ready', 'actor_ref': 'task',
                    'context': copy.deepcopy(flow.state()['control']['context']),
                    'evidence': forged_evidence}})

        flow.flow_git('reset', '--hard', candidate)
        ready = flow.invoke('control', {'action': 'candidate-ready', 'evidence': {
            key: copy.deepcopy(value) for key, value in evidence.items() if key != 'source'},
            'receipt': evidence['source']})
        self.assertEqual(flow.state()['control']['context']['handoff_progress']['state'],
                         'reviewable', ready)
        flow.mark_reviewable = lambda _candidate, fixture_execution=False: {
            'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks}
        payload = {'artifacts': ['impl.py', 'independent.py', 'downstream.py', 'reader.py'],
            'checks': ['retired helper behavior passed'], 'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate}}
        original_observation = flow.observation
        historical_refs = {item['agent_ref'] for source in checkpoint['plan_revision_sources']
                           for item in source['executions'] if item['agent_ref']}
        def with_historical_agents(*args, **kwargs):
            observation = original_observation(*args, **kwargs)
            if 'lifecycle' in observation:
                members = observation['lifecycle']['pages'][0]['response']['data']
                known = {item['id'] for item in members}
                members.extend({'id': ref, 'status': {'type': 'idle'}}
                               for ref in historical_refs - known)
            return observation
        with patch.object(flow, 'observation', side_effect=with_historical_agents):
            accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        predecessor = flow.state()['accepted']
        current_scope = flow.state()['scope_authority']['scope']
        closure = flow.input_for(4, predecessor)
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            closure['scope'][key] = copy.deepcopy(current_scope[key])
        closure['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            closure['scope'])
        with self.assertRaises(progress.entry.PreparationError) as wrong_pin:
            progress.handoff.prepare(closure, checkpoint_path=str(flow.checkpoint.with_name('other.json')))
        self.assertEqual(wrong_pin.exception.code, 'handoff_not_ready')
        stale = copy.deepcopy(closure)
        stale['scope']['implementation_paths'] = predecessor['handoff']['scope']['implementation_paths']
        stale['scope']['downstream'] = predecessor['handoff']['scope']['downstream']
        stale['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(stale['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            progress.handoff.prepare(stale, checkpoint_path=str(flow.checkpoint))
        prepared_closure = progress.handoff.prepare(closure, checkpoint_path=str(flow.checkpoint))
        self.assertEqual(progress.handoff.verify(prepared_closure,
            checkpoint_path=str(flow.checkpoint)), prepared_closure)
        flow.context = flow.context_for(4)
        started = flow.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host', started)
        stage4 = flow.state()
        self.assertEqual(progress.handoff.verify(stage4['handoff'],
            checkpoint_path=str(flow.checkpoint)), stage4['handoff'])

    def test_retired_helper_with_unaccepted_bytes_cannot_receive_planning_release(self):
        flow = self.new_active_flow(read_dependency=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        authority = flow.state()["scope_authority"]
        ticket = authority["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "pending-input")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py",
                                 b"def later():\n    return 1\n", "pending-helper", accept=False)
        executions = flow.state()["control"]["context"]["handoff_progress"]["executions"]
        self.assertEqual(executions[-1]["state"], "received")
        self.assertTrue(executions[-1]["stopped"])
        flow.request = controller_request
        flow.settings = controller_settings
        old_manifest = authority["scope"]["downstream"]["manifest"]
        manifest = [item for item in old_manifest if item["path"] != "later.py"]
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"].update(step_ids=["FP1-02"],
                                dependency_impact=["FP1-04", "FP1-05"],
                                verification_impact=["V2"],
                                manifest_diff=progress.stage_scope.manifest_diff(old_manifest, manifest))
        flow.invoke("prepare-plan-revision", report)
        before = flow.flow_git("rev-parse", "HEAD")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(error.exception.code, "invalid_plan_revision")
        self.assertIn("later.py", str(error.exception))
        self.assertEqual(flow.state()["scope_authority"], authority)
        self.assertIsNone(flow.state().get("plan_revision_transaction"))
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), before)
        inspected = flow.invoke("advance")
        self.assertEqual(inspected["next_action"]["operation"], "inspect-host-state")
        uncertain = flow.observation("unknown", "result", ref="native:dispatcher")
        unresolved = flow.invoke("observe", uncertain)
        self.assertNotEqual((unresolved.get("next_action") or {}).get("operation"), "continue-host")
        replay = flow.invoke("observe", uncertain, revision=0)
        self.assertTrue(replay["acknowledged"])
        self.assertEqual(flow.state()["scope_authority"], authority)

    def test_paused_retired_helper_rejection_keeps_original_recovery(self):
        flow = self.new_active_flow(read_dependency=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        authority = flow.state()["scope_authority"]
        ticket = authority["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "paused-input")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py",
                                 b"def later():\n    return 1\n", "paused-helper")
        flow.request = controller_request
        flow.settings = controller_settings
        old_manifest = authority["scope"]["downstream"]["manifest"]
        manifest = [item for item in old_manifest if item["path"] != "later.py"]
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"].update(step_ids=["FP1-02"],
                                dependency_impact=["FP1-04", "FP1-05"],
                                verification_impact=["V2"],
                                manifest_diff=progress.stage_scope.manifest_diff(old_manifest, manifest))
        flow.invoke("prepare-plan-revision", report)
        flow.invoke("pause")
        flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(flow.state()["status"], "paused")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("resume")
        self.assertEqual(error.exception.code, "invalid_plan_revision")
        self.assertIn("later.py", str(error.exception))
        self.assertEqual(flow.state()["status"], "active")
        self.assertEqual(flow.state()["scope_authority"], authority)
        self.assertIsNone(flow.state().get("plan_revision_transaction"))
        inspected = flow.invoke("advance")
        self.assertEqual(inspected["next_action"]["operation"], "inspect-host-state", inspected)
        resumed = flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        self.assertEqual(resumed["next_action"]["operation"], "continue-host", resumed)

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
