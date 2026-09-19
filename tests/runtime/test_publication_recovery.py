"""Interrupted real Git publication; only crash/transport points are doubles."""
import copy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_supervision_protocol as fixtures
protocol = fixtures.PROTOCOL


class SimulatedCrash(BaseException):
    pass


class PublicationRecoveryTests(unittest.TestCase):
    setUp = fixtures.WorktreeProtocolTests.setUp
    tearDown = fixtures.WorktreeProtocolTests.tearDown
    git = fixtures.WorktreeProtocolTests.git
    start = fixtures.WorktreeProtocolTests.start
    commit = fixtures.WorktreeProtocolTests.commit
    complete_input = fixtures.WorktreeProtocolTests.complete_input
    publish_input = fixtures.WorktreeProtocolTests.publish_input
    run_cli = fixtures.WorktreeProtocolTests.run_cli

    def transaction(self, planning=False):
        binding = self.start("recover")
        candidate = self.commit(Path(binding["worktree"]), "a.txt", "a1\n", "candidate")
        request = (self.publish_input("recover", binding, candidate, allowed_paths=["a.txt"]) if planning else
                   self.complete_input("recover", binding, candidate, binding["base_commit"], allowed_paths=["a.txt"]))
        return {"operation": "publish-planning" if planning else "complete-worktree", "request": request, "facts": []}

    def run_publication(self, transaction, crash=None):
        def record(fact):
            transaction["facts"].append(copy.deepcopy(fact))
            if fact["event"] == crash:
                raise SimulatedCrash()
        publisher = protocol.publish_planning if transaction["operation"] == "publish-planning" else protocol.complete_worktree
        return publisher(transaction["request"], record=record)

    def resume_publication(self, transaction):
        return protocol.reconcile_publication(transaction, resume=True,
            record=lambda fact: transaction["facts"].append(copy.deepcopy(fact)))

    def test_each_final_boundary_recovers_without_duplicate_merge(self):
        # A fresh repository for each crash, including branch deletion before receipt.
        for event in ("prepared", "merged", "worktree-removed", "branch-removed"):
            with self.subTest(event=event):
                transaction = self.transaction()
                with self.assertRaises(SimulatedCrash):
                    self.run_publication(transaction, event)
                before = self.git("rev-parse", "HEAD")
                result = self.resume_publication(transaction)
                self.assertEqual(result["state"], "completed")
                self.assertEqual(len(self.git("rev-list", "--merges", "HEAD").splitlines()), 1)
                if event != "prepared":
                    self.assertEqual(before, self.git("rev-parse", "HEAD"))
                self.assertEqual(self.resume_publication(transaction)["merge_commit"], result["merge_commit"])
                self.tearDown()
                self.setUp()

    def test_merge_succeeded_before_merged_receipt(self):
        transaction = self.transaction()
        original = protocol._merge_candidate_into_target
        def crash_after_merge(*args):
            original(*args)
            raise SimulatedCrash()
        with patch.object(protocol, "_merge_candidate_into_target", side_effect=crash_after_merge):
            with self.assertRaises(SimulatedCrash):
                self.run_publication(transaction)
        self.assertEqual([f["event"] for f in transaction["facts"]], ["prepared"])
        merged = self.git("rev-parse", "HEAD")
        # Target may advance after our merge; its HEAD is not our merge receipt.
        self.commit(self.repository, "b.txt", "later\n", "unrelated later change")
        with patch.object(protocol, "_merge_candidate_into_target", side_effect=AssertionError("republished")):
            result = self.resume_publication(transaction)
        self.assertEqual(result["merge_commit"], merged)
        self.assertEqual((self.repository / "b.txt").read_text(), "later\n")

    def test_planning_flow_advance_recovers_and_retains_worktree(self):
        transaction = self.transaction(planning=True)
        with self.assertRaises(SimulatedCrash):
            self.run_publication(transaction, "merged")
        merged = self.git("rev-parse", "HEAD")
        result = self.resume_publication(transaction)
        self.assertEqual(result["state"], "planning_published")
        flow = Path(transaction["request"]["binding"]["worktree"])
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=flow), merged)
        self.assertTrue(flow.exists())
        self.assertEqual(self.resume_publication(transaction)["merge_commit"], merged)

    def test_completed_refresh_without_prepared_receipt_recovers(self):
        transaction = self.transaction(planning=True)
        self.commit(self.repository, "b.txt", "target\n", "target advance")
        original = protocol._prepare_planning_candidate
        def crash_after_refresh(*args, **kwargs):
            original(*args, **kwargs)
            raise SimulatedCrash()
        with patch.object(protocol, "_prepare_planning_candidate", side_effect=crash_after_refresh):
            with self.assertRaises(SimulatedCrash):
                self.run_publication(transaction)
        self.assertEqual([f["event"] for f in transaction["facts"]], ["refresh"])
        self.assertEqual(self.resume_publication(transaction)["state"], "planning_published")

    def test_changed_target_before_merge_does_not_retry(self):
        transaction = self.transaction()
        with self.assertRaises(SimulatedCrash):
            self.run_publication(transaction, "prepared")
        self.commit(self.repository, "b.txt", "later\n", "target race")
        before = self.git("rev-parse", "HEAD")
        with self.assertRaises(protocol.ProtocolError):
            self.resume_publication(transaction)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertTrue(Path(transaction["request"]["binding"]["worktree"]).exists())

    def test_cleanup_preserves_new_user_work_and_reused_branch(self):
        transaction = self.transaction()
        with self.assertRaises(SimulatedCrash):
            self.run_publication(transaction, "merged")
        binding = transaction["request"]["binding"]
        flow = Path(binding["worktree"])
        (flow / "notes.txt").write_text("user work\n")
        with self.assertRaises(protocol.ProtocolError):
            self.resume_publication(transaction)
        self.assertEqual((flow / "notes.txt").read_text(), "user work\n")
        (flow / "notes.txt").unlink()
        self.git("worktree", "remove", str(flow))
        self.git("branch", "-d", binding["branch"])
        self.git("branch", binding["branch"], transaction["request"]["candidate_commit"])
        with self.assertRaises(protocol.ProtocolError) as caught:
            self.resume_publication(transaction)
        self.assertEqual(caught.exception.code, "cleanup_pending")
        self.assertEqual(self.git("rev-parse", binding["branch"]), transaction["request"]["candidate_commit"])

    def test_inspection_and_cleanup_cli_require_exact_saved_proof(self):
        import json
        transaction = self.transaction()
        with self.assertRaises(SimulatedCrash):
            self.run_publication(transaction, "merged")
        before = self.git("rev-parse", "HEAD")
        inspection = self.run_cli("reconcile-publication", json.dumps(transaction).encode())
        self.assertEqual(inspection.returncode, 0, inspection.stdout)
        self.assertEqual(json.loads(inspection.stdout)["state"], "cleanup-pending")
        self.assertTrue(Path(transaction["request"]["binding"]["worktree"]).exists())
        cleaned = self.run_cli("cleanup-only", json.dumps(transaction).encode())
        self.assertEqual(cleaned.returncode, 0, cleaned.stdout)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        transaction["facts"] = []
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.reconcile_publication(transaction)
        self.assertEqual(caught.exception.code, "integration_unverified")

    def test_cleanup_preserves_ignored_user_files(self):
        transaction = self.transaction()
        with self.assertRaises(SimulatedCrash):
            self.run_publication(transaction, "merged")
        flow = Path(transaction["request"]["binding"]["worktree"])
        (self.repository / ".git/info/exclude").write_text("personal.txt\n")
        (flow / "personal.txt").write_text("ignored user content")
        with self.assertRaises(protocol.ProtocolError):
            self.resume_publication(transaction)
        self.assertEqual((flow / "personal.txt").read_text(), "ignored user content")


if __name__ == "__main__":
    unittest.main()
