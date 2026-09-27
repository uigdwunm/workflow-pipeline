"""Retained workspaces complete the real C/B/Git lifecycle, including runner intake."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import test_publication_progress as fixtures
import workflow


class RetainedWorkspaceHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationProgressTests(methodName="runTest")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.f = self.fixture.flow
        f = self.f
        f.binding = fixtures.supervision.bind_worktree({
            **{key: f.binding[key] for key in
               ("repository", "target_branch", "worktree", "branch", "base_commit")},
            "allowed_paths": [],
            "lifecycle": {"origin": "reused", "manager": "host", "disposition": "retain",
                          "evidence_ref": "fixture:original-host-workspace", "creation_receipt": None},
        })["binding"]
        f.request["target"]["binding"] = f.binding
        f.input = f.input_for(2)
        self.selection = {"controller_ref": "task", "reference": f.binding["lifecycle"]["evidence_ref"],
                          "worktree": str(f.flow), "receipts": [{"tool": "fixture-host.workspace-inventory",
                          "request": {}, "response": {"worktree": str(f.flow), "tasks": ["task"]}}]}
        invoke = f.invoke
        def with_workspace_evidence(operation, data=None, revision=None):
            if operation == "start" and not f.checkpoint.exists():
                data = {**data, "workspace_evidence": self.selection}
            return invoke(operation, data, revision)
        f.invoke = with_workspace_evidence

    def test_retained_workspace_completes_with_closure_documents_and_runner_projection(self):
        f = self.f
        accepted = self.fixture.closure()
        (f.flow / "CHANGELOG.md").write_text("closure documentation\n")
        f.flow_git("add", "CHANGELOG.md")
        f.flow_git("commit", "-qm", "closure")
        tip = f.flow_git("rev-parse", "HEAD")
        f.ready_publication(tip, "controller:closure", "native:closure")
        f.invoke("publication", {"candidate_commit": tip, "reference": "controller:closure",
                                 "expected_target_head": f.git("rev-parse", "HEAD")})
        result = self.fixture.accept_publication()
        self.assertTrue(result["workflow_completion"]["completed"])
        self.assertTrue(f.flow.is_dir())
        self.assertEqual(f.flow_git("rev-parse", "HEAD"), tip)
        state = f.state()
        self.assertEqual(state["workspace_evidence"], self.selection)
        payload = state["accepted"]["payload"]
        self.assertEqual(payload["candidate_commit"], accepted)
        self.assertEqual(payload["cleanup"], {"disposition": "retained", "disposition_verified": True,
                                             "worktree_removed": False, "branch_removed": False})
        projection = fixtures.progress.handoff.render(state["accepted"])["stage_result"]
        # The same persisted completion must be valid for the scripted carrier.
        parsed = {**projection, "handoff": fixtures.progress.handoff.render(state["accepted"])["payload"]}
        workflow._require_handoff("stage4", parsed)
        self.assertEqual(parsed["result"], "completed")
        merged = f.git("rev-parse", "HEAD")
        replay = f.invoke("resume")
        self.assertTrue(replay["workflow_completion"]["completed"])
        self.assertEqual(f.git("rev-parse", "HEAD"), merged)

    def test_lifecycle_cannot_change_at_next_stage(self):
        f = self.f
        f.finish_design("continuous")
        request = f.input_for(3, f.state()["accepted"])
        request = copy.deepcopy(request)
        request["binding"]["lifecycle"]["manager"] = "git"
        request["entry"]["target"]["binding"] = request["binding"]
        request["expected_entry"] = fixtures.progress.entry.resolve(request["entry"])
        with self.assertRaises(fixtures.progress.entry.PreparationError):
            fixtures.progress.handoff.handle(request)

    def test_missing_or_mismatched_original_workspace_observations_block_start(self):
        f = self.f
        for selection in (None, {**self.selection, "receipts": []},
                          {**self.selection, "worktree": str(f.flow) + "-other"}):
            self.selection = selection
            with self.assertRaises(fixtures.progress.entry.PreparationError):
                f.begin("continuous")
            self.assertFalse(f.checkpoint.exists())


if __name__ == "__main__":
    unittest.main()
