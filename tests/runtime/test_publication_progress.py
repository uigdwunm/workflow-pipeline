"""D's C/B seam using real temporary Git and exact fixture host receipts."""
import copy
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures
progress = fixtures.progress
supervision = fixtures.transfer.supervision


class PublicationProgressTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.ProgressTests(methodName="runTest")
        self.addCleanup(self.flow.doCleanups)
        self.flow.setUp()

    def closure(self):
        f = self.flow
        f.finish_design("continuous")
        f.finish_next(3, "continuous")
        predecessor = f.state()["accepted"]
        f.context = f.context_for(4)
        f.begin("continuous", f.input_for(4, predecessor))
        f.invoke("observe", f.observation(ref="native:closure"))
        return predecessor["payload"]["candidate_commit"]

    def accept_publication(self):
        f = self.flow
        received = f.invoke("receive-publication")
        self.assertEqual(received["pending"]["kind"], "acceptance")
        again = f.invoke("receive-publication")
        self.assertEqual(again["pending"], received["pending"])
        pending = received["pending"]
        return f.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                                  "answer": "accept", "reference": "controller:final-acceptance"})

    def test_closure_tip_and_accepted_implementation_keep_distinct_sources(self):
        f = self.flow
        accepted = self.closure()
        (f.flow / "CHANGELOG.md").write_text("documented implementation\n")
        f.flow_git("add", "CHANGELOG.md")
        f.flow_git("commit", "-qm", "closure documentation")
        tip = f.flow_git("rev-parse", "HEAD")
        f.ready_publication(tip, "controller:closure", "native:closure")
        native = copy.deepcopy(f.state()["publication_candidate"])
        f.invoke("publication", {"candidate_commit": tip, "reference": "controller:closure",
                                 "expected_target_head": f.git("rev-parse", "HEAD")})
        original_observations = copy.deepcopy(f.state()["observations"])
        result = self.accept_publication()
        self.assertTrue(result["workflow_completion"]["completed"])
        state = f.state()
        self.assertEqual(state["accepted"]["payload"]["candidate_commit"], accepted)
        self.assertEqual(state["publication"]["result"]["candidate_commit"], tip)
        self.assertEqual(state["publication"]["intake"]["native_candidate"], native)
        self.assertEqual(state["observations"], original_observations)
        self.assertEqual(state["publication"]["intake"]["kind"], "stage-owner-publication")

    def test_cleanup_resume_never_publishes_again(self):
        f = self.flow
        candidate = self.closure()
        f.ready_publication(candidate, "controller:closure", "native:closure")
        original = supervision._run_git
        def fail_cleanup(repository, arguments, **kwargs):
            if arguments[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(arguments, 1, "", "injected cleanup failure")
            return original(repository, arguments, **kwargs)
        with patch.object(supervision, "_run_git", side_effect=fail_cleanup):
            result = f.invoke("publication", {"candidate_commit": candidate, "reference": "controller:closure",
                                              "expected_target_head": f.git("rev-parse", "HEAD")})
        self.assertEqual(result["status"], "blocked")
        merged = f.git("rev-parse", "HEAD")
        with patch.object(supervision, "complete_worktree", side_effect=AssertionError("republished")):
            result = f.invoke("resume")
        self.assertEqual(result["next_action"]["publication"]["result"]["merge_commit"], merged)
        self.assertFalse(f.flow.exists())
        self.assertTrue(self.accept_publication()["workflow_completion"]["completed"])

    def test_lost_intake_response_replays_original_b_transaction(self):
        f = self.flow
        f.prepare_design_result("continuous")
        with patch.object(progress.Progress, "apply", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("receive-publication")
        transaction = copy.deepcopy(f.state()["transaction"])
        native = copy.deepcopy(f.state()["publication_candidate"])
        with patch.object(supervision, "publish_planning", side_effect=AssertionError("republished")):
            received = f.invoke("resume")
        self.assertEqual(received["pending"]["kind"], "acceptance")
        self.assertIsNone(f.state()["transaction"])
        self.assertEqual(f.state()["dispatch"]["delivery"]["message"], transaction["result"])
        self.assertEqual(f.state()["publication"]["intake"]["native_candidate"], native)

    def test_unknown_published_outcome_cannot_return_to_implementation(self):
        f = self.flow
        candidate = self.closure()
        f.ready_publication(candidate, "controller:closure", "native:closure")
        original = supervision._merge_candidate_into_target
        def crash(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(supervision, "_merge_candidate_into_target", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("publication", {"candidate_commit": candidate, "reference": "controller:closure",
                                         "expected_target_head": f.git("rev-parse", "HEAD")})
        observed = f.observation("stopped", "result", ref="native:closure")
        observed["closure"] = {"ref": "native:closure", "candidate": candidate, "merge": None,
                               "ancestor_verified": False, "changed_paths": [], "binding": f.binding,
                               "checks": [], "worktree_removed": False, "branch_removed": False,
                               "implementation_problem": "late report"}
        result = f.invoke("observe", observed)
        self.assertEqual(result["error"]["code"], "already_published")
        self.assertEqual(f.state()["control"]["context"]["handoff_progress"]["state"], "closing")

    def test_new_control_cannot_write_to_a_v1_progression(self):
        f = self.flow
        f.begin("continuous")
        outer = progress.read_record(f.checkpoint)
        outer.update(version=3, status="active")
        outer[progress.KEY]["protocol"] = "workflow-progress-v1"
        progress.atomic_save(f.checkpoint, outer)
        before = f.checkpoint.read_bytes()
        for operation in ("pause", "cancel"):
            with self.assertRaises(fixtures.runner.WorkflowError):
                fixtures.runner.request_control(f.checkpoint, operation)
            self.assertEqual(f.checkpoint.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
