"""Committed function-plan admission through the real Controller/Git seam."""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_scope_progress as fixture
import test_stage_scope_transfer as git_fixture
import execution_plan


class ExecutionPlanAdmissionTests(unittest.TestCase):
    def new_flow(self):
        case = fixture.ScopeProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def rejected_plan(self, change, *, change_document=None, add_unplanned_test=False):
        case = self.new_flow()
        candidate = case.planning_candidate()
        path = case.flow.flow / "docs/spec.md"
        text = path.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", text, re.DOTALL)
        plan = json.loads(match.group(1))
        replacement = change(plan)
        updated = text[:match.start(1)] + replacement + text[match.end(1):]
        if change_document is not None:
            updated = change_document(updated)
        if updated != text:
            path.write_text(updated)
            case.flow.flow_git("add", "docs/spec.md")
            case.flow.flow_git("commit", "-qm", "changed plan")
            candidate = case.flow.flow_git("rev-parse", "HEAD")
        state = case.flow.state()
        manifest = case.manifest(candidate)
        if add_unplanned_test:
            manifest.append({"path": "tests/test_impl.py", "owner": "implementation", "kind": "test",
                "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md",
                                            "section": "approved design"}})
        proposal = {"proposal_id": "invalid-plan", "expected_revision": 0,
            "candidate_commit": candidate, "manifest": manifest,
            "implementation_required": True,
            "requirement_identity": copy.deepcopy(state["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(state["handoff"]["binding"]),
            "previous_scope_revision": 0}
        before = case.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            case.flow.invoke("scope-propose", proposal)
        self.assertEqual(error.exception.code, "execution_plan_incomplete")
        self.assertEqual(case.flow.checkpoint.read_bytes(), before)

    def test_ticket_identity_requires_unique_exact_document_lines(self):
        def plan_ticket_id(plan):
            plan["ticket_id"] = "FEP-1"
            plan["contract_refs"] = [ref.replace("FEP-01:", "FEP-1:")
                                     for ref in plan["contract_refs"]]
            return json.dumps(plan)

        cases = (
            ("ticket prefix", plan_ticket_id,
             lambda text: text.replace("Ticket ID: FEP-01\n", "Ticket ID: FEP-10\n")),
            ("version prefix", json.dumps,
             lambda text: text.replace("Plan Version: 1\n", "Plan Version: 10\n")),
            ("missing ticket", json.dumps,
             lambda text: text.replace("Ticket ID: FEP-01\n", "")),
            ("duplicate ticket", json.dumps,
             lambda text: text.replace("Ticket ID: FEP-01\n", "Ticket ID: FEP-01\nTicket ID: FEP-01\n")),
            ("ambiguous ticket", json.dumps,
             lambda text: text.replace("Ticket ID: FEP-01\n", "Ticket ID: FEP-01 extra\n")),
            ("missing version", json.dumps,
             lambda text: text.replace("Plan Version: 1\n", "")),
            ("duplicate version", json.dumps,
             lambda text: text.replace("Plan Version: 1\n", "Plan Version: 1\nPlan Version: 1\n")),
            ("ambiguous version", json.dumps,
             lambda text: text.replace("Plan Version: 1\n", "Plan Version: 1 extra\n")),
        )
        for label, change_plan, change_document in cases:
            with self.subTest(label):
                self.rejected_plan(change_plan, change_document=change_document)

    def test_spec_reference_requires_one_real_section_at_controller_boundary(self):
        self.rejected_plan(lambda plan: json.dumps({**plan,
            "spec_ref": {"path": "docs/spec.md", "section": "Absent section"}}))
        case = self.new_flow()
        candidate = case.planning_candidate()
        path = case.flow.flow / "docs/spec.md"
        path.write_text(path.read_text().replace("### D1 — Plan\n", "### D1 — Plan\n### D1 — Duplicate\n"))
        case.flow.flow_git("add", "docs/spec.md")
        case.flow.flow_git("commit", "-qm", "duplicate contract heading")
        candidate = case.flow.flow_git("rev-parse", "HEAD")
        state = case.flow.state()
        proposal = {"proposal_id": "duplicate-heading", "expected_revision": 0,
            "candidate_commit": candidate, "manifest": case.manifest(candidate),
            "implementation_required": True,
            "requirement_identity": copy.deepcopy(state["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(state["handoff"]["binding"]),
            "previous_scope_revision": 0}
        before = case.flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            case.flow.invoke("scope-propose", proposal)
        self.assertEqual(error.exception.code, "execution_plan_incomplete")
        self.assertEqual(case.flow.checkpoint.read_bytes(), before)

    def test_ticket_projection_protects_bytes_after_plan_in_any_language(self):
        prefix = b"# Ticket\nTicket ID: TASK-B\nPlan Version: 1\n**Blocked by:** None\n"
        block = b"```execution-plan-v1\n{\"plan_version\":1}\n```\n"
        tail = "## 后续行为\n- [ ] AC-2: 用户可见结果\n".encode()
        source = {"path": "docs/issues/ticket.md", "mode": "100644", "body": prefix + block + tail,
                  "sha256": "committed-document-identity"}
        digest = lambda item: execution_plan.contract_projection("unused", "unused", [item])["digest"]
        initial = digest(source)
        self.assertEqual(initial, digest({**source, "body": prefix + block.replace(b":1", b":2") + tail}))
        self.assertEqual(initial, digest({**source, "body": prefix.replace(b"Version: 1", b"Version: 2") + block + tail}))
        self.assertNotEqual(initial, digest({**source, "body": prefix + block + tail.replace("用户可见".encode(), "不同".encode())}))

    def test_explicit_private_section_contract_ref_is_protected(self):
        original = b"## Further Notes\n### N2 - public promise\nStable API\n### N4 - inventory\nprivate table\n"
        edited_public = original.replace(b"Stable API", b"Changed API")
        edited_inventory = original.replace(b"private table", b"updated table")
        source = lambda body: {"path": "docs/spec.md", "mode": "100644", "body": body,
                               "sha256": "committed-document-identity"}
        project = lambda body: execution_plan.contract_projection("unused", "unused", [source(body)],
            spec_path="docs/spec.md", contract_refs=["PRD:N2:public promise"])["digest"]
        self.assertNotEqual(project(original), project(edited_public))
        self.assertEqual(project(original), project(edited_inventory))

    def test_existing_non_python_function_uses_committed_source_and_caller_locations(self):
        examples = (("impl.cc", "bool Widget::run() { return true; }\n"
                     "bool call() { return Widget::run(); }\n", "Widget::run"),
                    ("impl.js", "class Widget { run() { return true; } }\n"
                     "new Widget().run();\n", "Widget.run"))
        for target_path, code, symbol in examples:
            with self.subTest(target_path=target_path):
                case = git_fixture.StageScopeTransferTests(methodName="runTest")
                case.setUp()
                self.addCleanup(case.doCleanups)
                (case.root / target_path).write_text(code)
                case.git("add", target_path)
                case.git("commit", "-qm", "existing non-Python function and caller")
                baseline = case.git("rev-parse", "HEAD")
                path = case.root / "docs/spec.md"
                body = path.read_text()
                match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
                plan = json.loads(match.group(1))
                plan["steps"][0]["target"] = {"path": target_path, "symbol": symbol,
                                                 "operation": "modify"}
                plan["steps"][0]["approach"].extend([
                    f"source-location: {target_path}:1", f"production-caller: {target_path}:2"])
                path.write_text(body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                case.git("add", "docs/spec.md")
                case.git("commit", "-qm", "plan existing non-Python target")
                candidate = case.git("rev-parse", "HEAD")
                manifest = [{"path": target, "owner": "implementation", "kind": "source",
                             "operation": operation, "basis": {"commit": candidate,
                                 "path": "docs/spec.md", "section": "Approved file decision"}}
                            for target, operation in ((target_path, "modify"), ("impl.py", "add"),
                                                      ("tests/test_impl.py", "add"))]
                scope = {"baseline": baseline, "downstream": {"manifest": manifest}}
                catalog = execution_plan.collect_execution_plan(str(case.root), candidate,
                    ["docs/spec.md"], scope, {"path": "docs/requirement.md"})
                self.assertEqual(catalog["tickets"][0]["plan"]["steps"][0]["target"]["symbol"], symbol)
                if target_path == "impl.js":
                    plan["steps"][0]["approach"][-1] = "production-caller: impl.js:99"
                    committed = path.read_text()
                    match = re.search(r"```execution-plan-v1\n(.*?)\n```", committed, re.DOTALL)
                    path.write_text(committed[:match.start(1)] + json.dumps(plan) + committed[match.end(1):])
                    case.git("add", "docs/spec.md")
                    case.git("commit", "-qm", "invalid caller location")
                    invalid = case.git("rev-parse", "HEAD")
                    with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
                        execution_plan.collect_execution_plan(str(case.root), invalid,
                            ["docs/spec.md"], scope, {"path": "docs/requirement.md"})
                    self.assertEqual(error.exception.code, "execution_plan_incomplete")

    def test_blocker_uses_stable_ticket_id_instead_of_prefix_inference(self):
        case = git_fixture.StageScopeTransferTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        path = case.root / "docs/spec.md"
        body = path.read_text().replace("Ticket ID: FEP-01", "Ticket ID: TASK-B").replace(
            "**Blocked by:** None", "**Blocked by:** TASK-A")
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["ticket_id"] = "TASK-B"
        plan["blocked_by"] = ["TASK-A"]
        plan["contract_refs"] = [ref.replace("FEP-01:", "TASK-B:") for ref in plan["contract_refs"]]
        path.write_text(body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
        case.git("add", "docs/spec.md")
        case.git("commit", "-qm", "stable Ticket ID")
        candidate = case.git("rev-parse", "HEAD")
        source = execution_plan.read_ticket_plan(str(case.root), candidate, "docs/spec.md")
        spec_sections = {"docs/spec.md": execution_plan._sections(path.read_bytes())}
        record = execution_plan.validate_ticket_plan(source, str(case.root), candidate,
            {"impl.py", "tests/test_impl.py"}, baseline=case.baseline, spec_sections=spec_sections,
            ticket_ids=["TASK-A", "TASK-B"])
        self.assertEqual(record["plan"]["blocked_by"], ["TASK-A"])
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError):
            execution_plan.validate_ticket_plan(source, str(case.root), candidate,
                {"impl.py", "tests/test_impl.py"}, baseline=case.baseline,
                spec_sections=spec_sections, ticket_ids=["TASK-B"])

    def test_invalid_contract_references_io_cycles_and_coverage_are_not_sealed(self):
        def changed(mutator):
            def render(plan):
                mutator(plan)
                return json.dumps(plan)
            return render

        with self.subTest("missing output"):
            self.rejected_plan(changed(lambda plan: plan["steps"][0].update(outputs=[])))
        with self.subTest("unknown verification"):
            self.rejected_plan(changed(lambda plan: plan["steps"][0].update(verification_refs=["unknown"])))
        with self.subTest("unknown contract"):
            self.rejected_plan(changed(lambda plan: plan["contract_refs"].append("PRD:D9:Missing")))
        with self.subTest("dependency cycle"):
            self.rejected_plan(changed(lambda plan: plan["steps"][0].update(depends_on=["FP1-02"])))
        with self.subTest("unplanned manifest path"):
            self.rejected_plan(lambda plan: json.dumps(plan), add_unplanned_test=True)
        with self.subTest("duplicate JSON field"):
            self.rejected_plan(lambda plan: json.dumps(plan).replace('"plan_version": 1,',
                '"plan_version": 1, "plan_version": 2,'))
        with self.subTest("code capability cannot be an artifact-only plan"):
            def artifact_without_function(plan):
                plan['steps'] = []
                plan['artifact_steps'] = [{'id': 'ART-1', 'path': 'impl.py',
                    'operation': 'add', 'capability': 'implement code',
                    'approach': 'write code', 'verification_refs': ['V1']}]
                return json.dumps(plan)
            self.rejected_plan(artifact_without_function)

    def test_artifact_only_plan_seals_and_supplies_a_dispatchable_ref(self):
        case = self.new_flow()
        candidate = case.planning_candidate()
        path = case.flow.flow / "docs/spec.md"
        text = path.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", text, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["steps"] = []
        plan["artifact_steps"] = [{"id": "ART-1", "path": "prompt.md", "operation": "add",
            "capability": "provide the prompt artifact", "approach": "write the approved prompt",
            "verification_refs": ["V1"]}]
        path.write_text(text[:match.start(1)] + json.dumps(plan) + text[match.end(1):])
        case.flow.flow_git("add", "docs/spec.md")
        case.flow.flow_git("commit", "-qm", "artifact-only plan")
        candidate = case.flow.flow_git("rev-parse", "HEAD")
        scope = {"baseline": case.flow.flow_git("rev-parse", "HEAD~1"), "downstream": {"manifest": [
            {"path": "prompt.md", "owner": "implementation", "kind": "source", "operation": "add",
             "basis": {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}}]}}
        catalog = execution_plan.collect_execution_plan(str(case.flow.flow), candidate,
            ["docs/spec.md"], scope, {"path": "docs/requirement.md"})
        self.assertEqual(catalog["ordered_step_ids"], ["ART-1"])
        authority = {"state": "sealed", "revision": 1, "scope": scope, "execution_plan": catalog}
        ticket = catalog["tickets"][0]
        ref = {"ticket_id": ticket["ticket_id"], "step_id": "ART-1",
               "plan_version": ticket["plan_version"], "source": ticket["source"],
               "step_digest": fixture.fixture.transfer.entry.digest(plan["artifact_steps"][0])}
        group = execution_plan.validate_plan_refs(catalog, [ref], ["prompt.md"], [], [], authority)
        self.assertEqual(group["ordered_step_ids"], ["ART-1"])

    def test_ready_siblings_keep_source_order_when_first_depends_on_last(self):
        case = self.new_flow()
        candidate = case.planning_candidate()
        path = case.flow.flow / "docs/spec.md"
        text = path.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", text, re.DOTALL)
        plan = json.loads(match.group(1))
        first, second = plan["steps"]
        first["id"] = "A"
        first["depends_on"] = ["C"]
        second["id"] = "B"
        second["depends_on"] = []
        third = copy.deepcopy(second)
        third["id"] = "C"
        third["target"]["symbol"] = "third"
        plan["steps"].append(third)
        path.write_text(text[:match.start(1)] + json.dumps(plan) + text[match.end(1):])
        case.flow.flow_git("add", "docs/spec.md")
        case.flow.flow_git("commit", "-qm", "three sibling steps")
        candidate = case.flow.flow_git("rev-parse", "HEAD")
        scope = {"baseline": case.flow.flow_git("rev-parse", "HEAD~1"), "downstream": {"manifest": [
            {"path": "impl.py", "owner": "implementation", "kind": "source", "operation": "add",
             "basis": {"commit": candidate, "path": "docs/spec.md", "section": "approved design"}}]}}
        catalog = execution_plan.collect_execution_plan(str(case.flow.flow), candidate,
            ["docs/spec.md"], scope, {"path": "docs/requirement.md"})
        self.assertEqual(catalog["ordered_step_ids"], ["B", "C", "A"])


if __name__ == "__main__":
    unittest.main()
