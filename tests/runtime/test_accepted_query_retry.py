"""Explicit query retry preserves accepted business and unsettled host identity."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures
progress = fixtures.progress


class AcceptedQueryRetryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProgressTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def accept(self, pending):
        decision = {"decision_id": pending["decision_id"], "subject": pending["subject"],
                    "answer": "accept", "reference": "controller:accepted-query-retry"}
        return decision, self.f.invoke("decide", decision)

    def stage2(self, mode="continuous"):
        f = self.f
        f.prepare_design_result(mode)
        pending = f.invoke("receive-publication")["pending"]
        f.invoke("observe", f.observation("running", "result"))
        first = f.invoke("pause")["next_action"]
        self.assertEqual(first["operation"], "stop-host")
        self.assertEqual(f.invoke("observe", f.observation("unknown", "result"))["next_action"]["operation"], "stop-host")
        self.assertEqual(f.invoke("observe", f.observation("stopped", "result"))["status"], "paused")
        decision, result = self.accept(pending)
        self.assertTrue(result["decision_deferred"])
        self.assertEqual(f.invoke("resume")["status"], "accepted")
        return "native:designer", decision

    def stage3(self):
        f = self.f
        f.finish_design("continuous")
        predecessor = f.state()["accepted"]
        f.context = f.context_for(3)
        f.begin("continuous", f.input_for(3, predecessor))
        f.invoke("observe", f.observation(ref="native:dispatcher"))
        (f.flow / "impl.py").write_text("print('ok')\n")
        f.flow_git("add", "impl.py")
        f.flow_git("commit", "-qm", "implementation")
        candidate = f.flow_git("rev-parse", "HEAD")
        payload = {"artifacts": ["impl.py"], "checks": ["behavior tested"], "candidate_commit": candidate,
            "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"} for axis in ("standards", "spec")},
            "verification": {"candidate": candidate, "checks": ["behavior tested"]}}
        self.assertEqual(f.invoke("pause")["next_action"]["operation"], "stop-host")
        observed = f.observation("stopped", "result", {
            "delivery_id": "paused-completed", "status": "completed", "payload": payload}, ref="native:dispatcher")
        self.assertEqual(f.invoke("observe", observed)["status"], "paused")
        pending = f.invoke("resume")["pending"]
        decision, result = self.accept(pending)
        self.assertEqual(result["status"], "accepted")
        return "native:dispatcher", decision

    def retry_after_negative(self, stage, status, mode="continuous"):
        role, decision = self.stage2(mode) if stage == 2 else self.stage3()
        f = self.f
        accepted = copy.deepcopy(f.state()["accepted"])
        original_dispatch = copy.deepcopy(f.state()["dispatch"])
        pending_boundary = copy.deepcopy(f.state()["pending"])
        query = f.invoke("resume")["next_action"]
        original = copy.deepcopy(query["payload"]["unresolved_action"])
        failed = f.observation(status, "result", ref=role)
        failed.pop("action_resolution")
        f.invoke("observe", failed)
        ended = copy.deepcopy(f.state()["host"]["query"])
        self.assertTrue(ended["resolved"])
        with patch.object(progress.dispatch, "handle", side_effect=AssertionError("B result replayed")):
            retried = f.invoke("resume")["next_action"]
            self.assertEqual(retried["operation"], "inspect-host-state")
            self.assertNotEqual(retried["action_id"], query["action_id"])
            self.assertEqual(retried["payload"]["unresolved_action"], original)
            self.assertEqual(f.state()["host"]["query_history"][-1], ended)
            self.assertEqual(f.state()["accepted"], accepted)
            self.assertEqual(f.state()["dispatch"], original_dispatch)
            response = f.observation("idle", "result", ref=role)
            self.assertEqual(f.invoke("observe", response)["status"], "accepted")
            self.assertTrue(f.invoke("observe", response)["acknowledged"])
            self.assertTrue(f.invoke("decide", decision)["acknowledged"])
            before = f.checkpoint.read_bytes()
            self.assertTrue(f.invoke("resume")["acknowledged"])
            self.assertEqual(f.checkpoint.read_bytes(), before)
            self.assertEqual(f.state()["pending"], pending_boundary)
        self.assertEqual(progress.Progress(f.checkpoint, progress.read_record(f.checkpoint)).unresolved_host_actions(), [])
        if pending_boundary is not None:
            f.invoke("decide", {"decision_id": pending_boundary["decision_id"], "subject": pending_boundary["subject"],
                "answer": "confirm", "reference": "controller:next-stage"})
        f.context = f.context_for(stage + 1)
        self.assertEqual(f.begin(mode, f.input_for(stage + 1, accepted))["next_action"]["operation"], "invoke-host")

    def test_stage2_unknown_query_can_retry(self): self.retry_after_negative(2, "unknown")
    def test_stage3_missing_resolution_can_retry(self): self.retry_after_negative(3, "idle")

    def test_stage2_stepwise_keeps_stage_boundary(self): self.retry_after_negative(2, "unknown", "stepwise")

    def test_repeated_negative_queries_and_stale_query_cannot_release_action(self):
        role, decision = self.stage2()
        f = self.f
        accepted = copy.deepcopy(f.state()["accepted"])
        query = f.invoke("resume")["next_action"]
        original = copy.deepcopy(query["payload"]["unresolved_action"])
        ids = [query["action_id"]]
        negatives = []
        with patch.object(progress.dispatch, "handle", side_effect=AssertionError("reaccepted business")):
            for status, resolution in (("unknown", None), ("idle", {"action_id": original["action_id"], "outcome": "unknown"}),
                                       ("idle", {"action_id": "wrong-action", "outcome": "completed"})):
                data = f.observation(status, "result", ref=role)
                if resolution is None:
                    data.pop("action_resolution")
                else:
                    data["action_resolution"] = resolution
                negatives.append(copy.deepcopy(data))
                f.invoke("observe", data)
                ended = copy.deepcopy(f.state()["host"]["query"])
                before = f.checkpoint.read_bytes()
                f.invoke("advance")
                self.assertEqual(f.checkpoint.read_bytes(), before)
                self.assertEqual(f.state()["host"]["query"], ended)
                query = f.invoke("resume")["next_action"]
                self.assertEqual(query["operation"], "inspect-host-state")
                self.assertNotIn(query["action_id"], ids)
                ids.append(query["action_id"])
                self.assertEqual(query["payload"]["unresolved_action"], original)
                self.assertEqual(f.invoke("resume")["next_action"], query)
                self.assertEqual(f.state()["accepted"], accepted)
            self.assertEqual([item["action_id"] for item in f.state()["host"]["query_history"][-3:]], ids[:-1])
            late = copy.deepcopy(negatives[0])
            late["event_id"] += ":late-terminal"
            late["receipt"]["receipt_ref"] += ":late-terminal"
            late["receipt"]["status"] = "idle"
            late["receipt"]["raw"]["status"] = "idle"
            late["provenance"]["response_ref"] += ":late-terminal"
            late["action_resolution"] = {"action_id": original["action_id"], "outcome": "completed"}
            f.invoke("observe", late)
            self.assertEqual(f.state()["host"]["query"], query)
            self.assertEqual(progress.Progress(f.checkpoint, progress.read_record(f.checkpoint)).unresolved_host_actions(), [original])
            self.assertIsNone(f.state()["host"]["proof"])
            self.assertEqual(f.invoke("observe", f.observation("idle", "result", ref=role))["status"], "accepted")
            self.assertTrue(f.invoke("decide", decision)["acknowledged"])

    def retry_save_window(self, after):
        role, _ = self.stage2()
        f = self.f
        first = f.invoke("resume")["next_action"]
        negative = f.observation("unknown", "result", ref=role)
        negative.pop("action_resolution")
        f.invoke("observe", negative)
        ended = copy.deepcopy(f.state()["host"]["query"])
        accepted = copy.deepcopy(f.state()["accepted"])
        save = progress.Progress.save
        def crash(owner):
            if owner.state["host"]["query"] is None and owner.state["host"].get("query_history"):
                if after:
                    save(owner)
                raise KeyboardInterrupt()
            save(owner)
        with patch.object(progress.Progress, "save", new=crash):
            with self.assertRaises(KeyboardInterrupt):
                f.invoke("resume")
        self.assertEqual(f.state()["host"]["query"], None if after else ended)
        new = f.invoke("resume")["next_action"]
        self.assertEqual(new["operation"], "inspect-host-state")
        self.assertNotEqual(new["action_id"], first["action_id"])
        self.assertEqual(new["payload"]["unresolved_action"], first["payload"]["unresolved_action"])
        self.assertEqual(f.state()["host"]["query_history"], [ended])
        self.assertEqual(f.state()["accepted"], accepted)

    def test_retry_before_archive_save_keeps_old_query(self): self.retry_save_window(False)
    def test_retry_after_archive_save_keeps_original_action(self): self.retry_save_window(True)

    def runner_stop(self, operation):
        self.stage2()
        f = self.f
        f.invoke("resume")
        negative = f.observation("unknown", "result")
        negative.pop("action_resolution")
        f.invoke("observe", negative)
        ended = copy.deepcopy(f.state()["host"]["query"])
        fixtures.runner._atomic_save(f.checkpoint, fixtures.runner._new_state({}))
        fixtures.runner.request_control(f.checkpoint, operation)
        for resume in ("resume", "advance", "resume"):
            result = f.invoke(resume)
            self.assertNotEqual((result.get("next_action") or {}).get("operation"), "inspect-host-state")
            self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
            self.assertEqual(f.state()["host"]["query"], ended)
            self.assertFalse(f.state()["host"].get("query_history"))

    def test_runner_pause_does_not_retry_accepted_query(self): self.runner_stop("pause")
    def test_runner_cancel_does_not_retry_accepted_query(self): self.runner_stop("cancel")
