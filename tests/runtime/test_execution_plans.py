"""Committed function-plan admission through the real Controller/Git seam."""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_scope_progress as fixture


class ExecutionPlanAdmissionTests(unittest.TestCase):
    def new_flow(self):
        case = fixture.ScopeProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def rejected_plan(self, change, *, add_unplanned_test=False):
        case = self.new_flow()
        candidate = case.planning_candidate()
        path = case.flow.flow / "docs/spec.md"
        text = path.read_text()
        match = re.search(r"```execution-plan-v1\n(.*?)\n```", text, re.DOTALL)
        plan = json.loads(match.group(1))
        replacement = change(plan)
        updated = text[:match.start(1)] + replacement + text[match.end(1):]
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


if __name__ == "__main__":
    unittest.main()
