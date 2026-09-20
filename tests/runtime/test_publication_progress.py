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

    def saved_intake_interruption(self, stage):
        f = self.flow
        if stage == 2:
            f.prepare_design_result("continuous")
        else:
            candidate = self.closure()
            f.ready_publication(candidate, "controller:closure", "native:closure")
            f.invoke("publication", {"candidate_commit": candidate, "reference": "controller:closure",
                                     "expected_target_head": f.git("rev-parse", "HEAD")})
        original = progress.Progress.apply
        def crash_after_save(owner, result):
            original(owner, result)
            raise KeyboardInterrupt()
        with patch.object(progress.Progress, "apply", new=crash_after_save):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("receive-publication")
        self.assertEqual(f.state()["transaction_result"]["record"]["status"], "received")
        self.assertEqual(f.state()["step"], "publication-complete")
        self.assertIsNotNone(f.state()["transaction"])
        merge = f.git("rev-parse", "HEAD")
        snapshot = copy.deepcopy(progress.read_record(f.checkpoint))
        for operation in ("resume", "receive-publication", "advance"):
            with self.subTest(stage=stage, operation=operation):
                progress.atomic_save(f.checkpoint, copy.deepcopy(snapshot))
                with patch.object(progress.dispatch, "handle", side_effect=AssertionError("B replayed")), \
                     patch.object(supervision, "_merge_candidate_into_target", side_effect=AssertionError("republished")):
                    result = f.invoke(operation)
                    self.assertEqual(result["status"], "needs_input")
                    self.assertEqual(result["pending"]["kind"], "acceptance")
                    pending = copy.deepcopy(result["pending"])
                    for repeat in ("resume", "receive-publication", "advance"):
                        self.assertEqual(f.invoke(repeat)["pending"], pending)
                self.assertEqual(f.git("rev-parse", "HEAD"), merge)

    def test_stage2_saved_intake_recovers_local_step(self):
        self.saved_intake_interruption(2)

    def test_stage4_saved_intake_recovers_local_step(self):
        self.saved_intake_interruption(4)

    def saved_acceptance_interruption(self, stage, *, after_save=True, mode="continuous"):
        f = self.flow
        if stage == 2:
            f.prepare_design_result(mode)
        else:
            candidate = self.closure()
            f.ready_publication(candidate, "controller:closure", "native:closure")
            f.invoke("publication", {"candidate_commit": candidate, "reference": "controller:closure",
                                     "expected_target_head": f.git("rev-parse", "HEAD")})
        pending = f.invoke("receive-publication")["pending"]
        decision = {"decision_id": pending["decision_id"], "subject": pending["subject"],
                    "answer": "accept", "reference": "controller:final"}
        original = progress.Progress.apply
        def crash_after_save(owner, result):
            if after_save:
                original(owner, result)
            raise KeyboardInterrupt()
        with patch.object(progress.Progress, "apply", new=crash_after_save):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("decide", decision)
        self.assertEqual(f.state()["dispatch"]["status"], "received")
        if after_save:
            self.assertEqual(f.state()["transaction_result"]["record"]["status"], "accepted")
        transaction = copy.deepcopy(f.state()["transaction"])
        options = {"side_effect": AssertionError("B acceptance replayed")} if after_save else {"wraps": progress.dispatch.handle}
        with patch.object(progress.dispatch, "handle", **options) as handler:
            rejected = f.invoke("decide", {**decision, "reference": "different:decision"})
            self.assertEqual(rejected["error"]["code"], "decision_conflict")
            self.assertEqual(f.state()["publication"]["acceptance_decision"], decision)
            result = f.invoke("resume")
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(f.state()["decisions"][decision["decision_id"]], decision)
            pending_after_acceptance = copy.deepcopy(result["pending"])
            for operation in ("receive-publication", "advance", "resume"):
                again = f.invoke(operation)
                self.assertEqual(again["status"], "accepted")
                self.assertEqual(again["pending"], pending_after_acceptance)
            self.assertTrue(f.invoke("decide", decision)["acknowledged"])
            if not after_save:
                self.assertEqual(handler.call_count, 1)
                self.assertEqual(handler.call_args.args[0], transaction)

    def test_stage2_saved_acceptance_recovers_original_decision(self):
        self.saved_acceptance_interruption(2)

    def test_stage4_saved_acceptance_recovers_original_decision(self):
        self.saved_acceptance_interruption(4)

    def test_stage2_unsaved_acceptance_replays_exact_transaction(self):
        self.saved_acceptance_interruption(2, after_save=False)

    def test_stage4_unsaved_acceptance_replays_exact_transaction(self):
        self.saved_acceptance_interruption(4, after_save=False)

    def test_stepwise_saved_acceptance_preserves_successor_decision(self):
        self.saved_acceptance_interruption(2, mode="stepwise")

    def test_stage4_unsaved_intake_replays_exact_transaction(self):
        f = self.flow
        candidate = self.closure()
        f.ready_publication(candidate, "controller:closure", "native:closure")
        f.invoke("publication", {"candidate_commit": candidate, "reference": "controller:closure",
                                 "expected_target_head": f.git("rev-parse", "HEAD")})
        with patch.object(progress.Progress, "apply", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("receive-publication")
        transaction = copy.deepcopy(f.state()["transaction"])
        with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as handler:
            result = f.invoke("resume")
        self.assertEqual(result["pending"]["kind"], "acceptance")
        self.assertEqual(handler.call_count, 1)
        self.assertEqual(handler.call_args.args[0], transaction)

    def test_saved_intake_does_not_override_pause_or_cancel(self):
        f = self.flow
        f.prepare_design_result("continuous")
        original = progress.Progress.apply
        def crash(owner, result):
            original(owner, result)
            raise KeyboardInterrupt()
        with patch.object(progress.Progress, "apply", new=crash):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("receive-publication")
        self.assertEqual(f.invoke("pause")["status"], "paused")
        self.assertEqual(f.invoke("advance")["status"], "paused")
        self.assertIsNone(f.state()["pending"])
        self.assertEqual(f.invoke("cancel")["status"], "cancelled")
        with patch.object(progress.Progress, "consume_transaction", side_effect=AssertionError("cancel was bypassed")):
            self.assertEqual(f.invoke("advance")["status"], "cancelled")
            self.assertEqual(f.invoke("resume")["status"], "cancelled")
            with self.assertRaises(fixtures.transfer.entry.PreparationError):
                f.invoke("receive-publication")
        self.assertIsNone(f.state()["pending"])

    def paused_transaction_window(self, stage, operation, saved):
        f = self.flow
        if stage == 2:
            f.prepare_design_result("continuous")
        else:
            candidate = self.closure()
            f.ready_publication(candidate, "controller:closure", "native:closure")
            f.invoke("publication", {"candidate_commit": candidate, "reference": "controller:closure",
                                     "expected_target_head": f.git("rev-parse", "HEAD")})
        decision = None
        if operation == "accept":
            pending = f.invoke("receive-publication")["pending"]
            decision = {"decision_id": pending["decision_id"], "subject": pending["subject"],
                        "answer": "accept", "reference": "controller:original-acceptance"}
        original_apply = progress.Progress.apply
        def interrupted(owner, result):
            if saved:
                original_apply(owner, result)
            raise KeyboardInterrupt()
        with patch.object(progress.Progress, "apply", new=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("decide", decision) if decision else f.invoke("receive-publication")
        transaction = copy.deepcopy(f.state()["transaction"])
        native = copy.deepcopy(f.state()["dispatch"]["request"])
        publication = copy.deepcopy(f.state()["publication"])
        merge = f.git("rev-parse", "HEAD")
        interrupted_state = copy.deepcopy(progress.read_record(f.checkpoint))
        if not saved:
            role = "native:designer" if stage == 2 else "native:closure"
            f.invoke("observe", f.observation("running", "result", ref=role))
            self.assertEqual(f.invoke("pause")["status"], "pausing")
            with patch.object(progress.dispatch, "handle", side_effect=AssertionError("unproved pause replayed B")):
                for action in ("resume", "advance", "resume"):
                    self.assertEqual(f.invoke(action)["status"], "pausing")
            self.assertEqual(f.state()["transaction"], transaction)
            self.assertFalse(f.state()["stopped"])
            self.assertEqual(f.invoke("observe", f.observation("stopped", "result", ref=role))["status"], "paused")
        # Counterfactual stop paths share immutable Git facts in this isolated
        # fixture; its control port has no external ledger or host mutations.
        for pause_first in (False, True):
            progress.atomic_save(f.checkpoint, copy.deepcopy(interrupted_state))
            if pause_first:
                self.assertEqual(f.invoke("pause")["status"], "paused")
            cancellation = f.invoke("cancel")
            # Stage 4 may need its original control recovery after Git removed
            # the worktree. That cannot revoke the durable cancellation intent.
            self.assertIn(cancellation["status"], {"cancelled", "cancelling", "blocked"})
            self.assertEqual(f.state()["stop_requested"], "cancelling")
            with patch.object(progress.dispatch, "handle", side_effect=AssertionError("cancelled transaction replayed")):
                self.assertIn(f.invoke("resume")["status"], {"cancelled", "cancelling"})
                self.assertIn(f.invoke("advance")["status"], {"cancelled", "cancelling"})
            self.assertEqual(f.state()["stop_requested"], "cancelling")
            self.assertEqual(f.state()["transaction"], transaction)
            if decision:
                f.invoke("decide", decision)
                self.assertNotIn(decision["decision_id"], f.state().get("decisions", {}))
        progress.atomic_save(f.checkpoint, copy.deepcopy(interrupted_state))
        self.assertEqual(f.invoke("pause")["status"], "paused")
        outer = progress.read_record(f.checkpoint)
        outer["runner_request"] = {"operation": "pause", "request_id": "original-runner-pause"}
        progress.atomic_save(f.checkpoint, outer)
        with patch.object(progress.dispatch, "handle", side_effect=AssertionError("runner request bypassed")):
            self.assertEqual(f.invoke("resume")["status"], "paused")
        outer = progress.read_record(f.checkpoint)
        self.assertEqual(outer.pop("runner_request"), {"operation": "pause", "request_id": "original-runner-pause"})
        progress.atomic_save(f.checkpoint, outer)
        self.assertEqual(f.invoke("advance")["status"], "paused")
        if decision:
            deferred = f.invoke("decide", decision)
            self.assertEqual(deferred["status"], "paused")
            self.assertTrue(deferred["decision_deferred"])
        self.assertEqual(f.state()["transaction"], transaction)
        with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as calls, \
             patch.object(progress.Progress, "effect", side_effect=AssertionError("carrier reissued")), \
             patch.object(supervision, "_merge_candidate_into_target", side_effect=AssertionError("republished")):
            if not saved:
                original_save = progress.Progress.save
                def crash_after_unpause(owner):
                    original_save(owner)
                    if owner.state["status"] == "active" and owner.state.get("stop_requested") is None:
                        raise KeyboardInterrupt()
                with patch.object(progress.Progress, "save", new=crash_after_unpause):
                    with self.assertRaises(KeyboardInterrupt):
                        f.invoke("resume")
                self.assertEqual(f.state()["transaction"], transaction)
                self.assertIsNone(f.state().get("stop_requested"))
                self.assertEqual(calls.call_count, 0)
            resumed = f.invoke("resume")
            expected = "accepted" if decision else "needs_input"
            self.assertEqual(resumed["status"], expected)
            pending = copy.deepcopy(resumed["pending"])
            for action in ("resume", "advance", "receive-publication"):
                repeated = f.invoke(action)
                self.assertEqual(repeated["status"], expected)
                self.assertEqual(repeated["pending"], pending)
            self.assertEqual(calls.call_count, 0 if saved else 1)
            if not saved:
                self.assertEqual(calls.call_args.args[0], transaction)
        self.assertIsNone(f.state()["transaction"])
        self.assertIsNone(f.state().get("stop_requested"))
        self.assertEqual(f.state()["dispatch"]["request"], native)
        self.assertEqual(f.state()["publication"]["request"], publication["request"])
        self.assertEqual(f.state()["publication"]["facts"], publication["facts"])
        self.assertEqual(f.git("rev-parse", "HEAD"), merge)
        if decision:
            self.assertEqual(f.state()["decisions"][decision["decision_id"]], decision)
            self.assertEqual(f.state()["publication"]["acceptance_decision"], decision)

    def test_paused_stage2_receive_before_save(self):
        self.paused_transaction_window(2, "receive", False)

    def test_paused_stage2_receive_after_save(self):
        self.paused_transaction_window(2, "receive", True)

    def test_paused_stage4_receive_before_save(self):
        self.paused_transaction_window(4, "receive", False)

    def test_paused_stage4_receive_after_save(self):
        self.paused_transaction_window(4, "receive", True)

    def test_paused_stage2_accept_before_save(self):
        self.paused_transaction_window(2, "accept", False)

    def test_paused_stage2_accept_after_save(self):
        self.paused_transaction_window(2, "accept", True)

    def test_paused_stage4_accept_before_save(self):
        self.paused_transaction_window(4, "accept", False)

    def test_paused_stage4_accept_after_save(self):
        self.paused_transaction_window(4, "accept", True)

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
