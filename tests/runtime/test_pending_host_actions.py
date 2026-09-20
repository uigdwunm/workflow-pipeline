"""Stop a writer promptly without discarding earlier unsettled invocations."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures
progress = fixtures.progress


class PendingHostActionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProgressTests(methodName="runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.begin("continuous")

    def bind(self):
        if self.f.state()["dispatch"]["status"] != "bound":
            self.f.invoke("observe", self.f.observation())

    def continue_action(self):
        self.bind()
        result = self.f.invoke("observe", self.f.observation("idle", "result", {
            "delivery_id": "first-continue", "status": "continue", "payload": {"slice": 1}}))
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        return copy.deepcopy(result["next_action"])

    def pause_and_stop(self):
        self.bind()
        stop = self.f.invoke("pause")["next_action"]
        self.assertEqual(stop["operation"], "stop-host")
        self.assertEqual(self.f.invoke("observe", self.f.observation("stopped", "result"))["status"], "paused")
        return stop

    def test_stop_does_not_settle_preceding_continue(self):
        original = self.continue_action()
        self.pause_and_stop()
        query = self.f.invoke("resume")["next_action"]
        idle = self.f.observation("idle", "result")
        idle.pop("action_resolution", None)
        result = self.f.invoke("observe", idle)
        self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        self.assertEqual(query["payload"]["unresolved_action"], original)

    def unresolved(self):
        return progress.Progress(self.f.checkpoint, progress.read_record(self.f.checkpoint)).unresolved_host_actions()

    def test_exact_settlement_allows_one_continuation(self):
        original = self.continue_action()
        stop = self.pause_and_stop()
        self.assertNotEqual(stop["action_id"], original["action_id"])
        query = self.f.invoke("resume")["next_action"]
        self.assertEqual(query["payload"]["unresolved_action"], original)
        self.assertEqual(self.f.invoke("advance")["next_action"], query)
        response = self.f.observation("idle", "result")
        self.assertEqual(response["action_resolution"]["action_id"], original["action_id"])
        continued = self.f.invoke("observe", response)["next_action"]
        self.assertEqual(continued["operation"], "continue-host")
        self.assertNotEqual(continued["action_id"], original["action_id"])
        self.assertEqual(self.f.state()["retained_host_actions"][0]["resolution"], response["action_resolution"])
        repeated = self.f.invoke("observe", response, revision=0)
        self.assertTrue(repeated["acknowledged"])
        self.assertEqual(repeated["next_action"]["action"], continued)
        self.assertEqual(self.f.invoke("advance")["next_action"]["action"], continued)

    def test_wrong_nonterminal_and_stop_identity_do_not_settle_continue(self):
        original = self.continue_action()
        stop = self.pause_and_stop()
        for action_id, outcome in ((original["action_id"], "unknown"), (original["action_id"], "running"),
                                   ("old-unrelated-action", "completed"), (stop["action_id"], "completed")):
            with self.subTest(action_id=action_id, outcome=outcome):
                self.f.invoke("resume")
                response = self.f.observation("idle", "result")
                response["action_resolution"] = {"action_id": action_id, "outcome": outcome}
                result = self.f.invoke("observe", response)
                self.assertEqual(result["next_action"]["operation"], "await-host-recovery")
                self.assertEqual(self.unresolved()[0], original)
                self.assertIsNone(self.f.state()["host"]["proof"])

    def test_stop_receipt_cannot_settle_the_earlier_action(self):
        original = self.continue_action()
        self.f.invoke("pause")
        response = self.f.observation("stopped", "result")
        response["action_resolution"] = {"action_id": original["action_id"], "outcome": "cancelled"}
        self.assertEqual(self.f.invoke("observe", response)["status"], "paused")
        self.assertEqual(self.unresolved(), [original])
        self.assertEqual(self.f.invoke("resume")["next_action"]["payload"]["unresolved_action"], original)

    def test_multiple_unresolved_stops_are_settled_individually(self):
        original = self.continue_action()
        first_stop = self.f.invoke("pause")["next_action"]
        second_stop = self.f.invoke("observe", self.f.observation("unknown", "result"))["next_action"]
        self.assertEqual(second_stop["operation"], "stop-host")
        self.assertNotEqual(first_stop["action_id"], second_stop["action_id"])
        self.f.invoke("pause")
        self.assertEqual([item["action_id"] for item in self.unresolved()],
                         [original["action_id"], first_stop["action_id"], second_stop["action_id"]])
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        first_query = self.f.invoke("resume")["next_action"]
        self.assertEqual(first_query["payload"]["unresolved_action"], original)
        first_response = self.f.observation("idle", "result")
        second_query = self.f.invoke("observe", first_response)["next_action"]
        self.assertEqual(second_query["operation"], "inspect-host-state")
        self.assertEqual(second_query["payload"]["unresolved_action"], first_stop)
        self.assertEqual(self.f.invoke("observe", first_response)["next_action"], second_query)
        self.assertEqual(self.f.invoke("observe", self.f.observation("idle", "result"))["next_action"]["operation"], "continue-host")
        self.assertTrue(all(item["resolved"] for item in self.f.state()["retained_host_actions"]))

    def stop_save_window(self, after):
        original = self.continue_action()
        save = progress.Progress.save
        def crash(owner):
            if (owner.state.get("action") or {}).get("operation") == "stop-host":
                if after:
                    save(owner)
                raise KeyboardInterrupt()
            save(owner)
        with patch.object(progress.Progress, "save", new=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.f.invoke("pause")
        self.assertEqual(self.unresolved()[0], original)
        action = self.f.invoke("advance")["next_action"]
        if after:
            self.assertEqual(action["operation"], "lookup-exact-action")
            action = action["action"]
        self.assertEqual(action["operation"], "stop-host")
        self.assertEqual(self.f.state()["retained_host_actions"], [original])
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        self.assertEqual(self.f.invoke("resume")["next_action"]["payload"]["unresolved_action"], original)

    def test_stop_before_save_retains_original(self): self.stop_save_window(False)
    def test_stop_after_save_retains_original(self): self.stop_save_window(True)

    def resolution_save_window(self, after):
        original = self.continue_action()
        self.pause_and_stop()
        self.f.invoke("resume")
        response = self.f.observation("idle", "result")
        save = progress.Progress.save
        def crash(owner):
            if (owner.state["host"].get("query") or {}).get("settled_action_id") == original["action_id"]:
                if after:
                    save(owner)
                raise KeyboardInterrupt()
            save(owner)
        with patch.object(progress.Progress, "save", new=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.f.invoke("observe", response)
        retained = self.f.state()["retained_host_actions"][0]
        self.assertEqual(retained.get("resolved", False), after)
        continued = self.f.invoke("observe", response)["next_action"]
        self.assertEqual(continued["operation"], "continue-host")
        self.assertTrue(self.f.invoke("observe", response)["acknowledged"])
        self.assertEqual(self.f.state()["action"], continued)

    def test_resolution_before_save_keeps_original(self): self.resolution_save_window(False)
    def test_resolution_after_save_is_not_reissued(self): self.resolution_save_window(True)

    def test_cancel_preserves_responsibility_and_cannot_resume(self):
        original = self.continue_action()
        first_stop = self.f.invoke("pause")["next_action"]
        cancel_stop = self.f.invoke("cancel")["next_action"]
        self.assertEqual(cancel_stop["operation"], "stop-host")
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        self.assertEqual(self.f.invoke("resume")["status"], "cancelled")
        self.assertEqual([item["action_id"] for item in self.unresolved()], [original["action_id"], first_stop["action_id"]])
        self.assertEqual(self.f.invoke("pause")["status"], "cancelled")
        self.assertEqual(self.f.state()["action"]["action_id"], cancel_stop["action_id"])

    def test_bound_invoke_is_retained_when_stop_replaces_it(self):
        original = copy.deepcopy(self.f.state()["host_action"])
        self.pause_and_stop()
        self.assertEqual(original["operation"], "invoke-host")
        query = self.f.invoke("resume")["next_action"]
        self.assertEqual(query["payload"]["unresolved_action"], original)
        response = self.f.observation("idle", "result")
        response.pop("action_resolution")
        self.assertEqual(self.f.invoke("observe", response)["next_action"]["operation"], "await-host-recovery")

    def test_uncertain_creation_is_looked_up_before_stop_and_retained(self):
        original = copy.deepcopy(self.f.state()["host_action"])
        self.f.invoke("observe", self.f.observation("unknown", "lookup", ref=None))
        self.assertEqual(self.f.invoke("pause")["next_action"]["operation"], "lookup-exact-action")
        stop = self.f.invoke("observe", self.f.observation("ready", "lookup"))["next_action"]
        self.assertEqual(stop["operation"], "stop-host")
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        self.assertEqual(self.f.invoke("resume")["next_action"]["payload"]["unresolved_action"], original)

    def test_pause_during_query_preserves_old_action_and_rejects_late_query(self):
        original = self.continue_action()
        self.pause_and_stop()
        first_query = self.f.invoke("resume")["next_action"]
        late = self.f.observation("idle", "result")
        self.assertEqual(self.f.invoke("pause")["status"], "paused")
        second_query = self.f.invoke("resume")["next_action"]
        self.assertNotEqual(first_query["action_id"], second_query["action_id"])
        self.assertEqual(self.f.invoke("observe", late)["next_action"], second_query)
        self.assertEqual(self.unresolved()[0], original)
        self.assertEqual(self.f.invoke("observe", self.f.observation("idle", "result"))["next_action"]["operation"], "continue-host")

    def terminal_outcome(self, outcome):
        original = self.continue_action()
        self.pause_and_stop()
        self.f.invoke("resume")
        response = self.f.observation("idle", "result")
        response["action_resolution"] = {"action_id": original["action_id"], "outcome": outcome}
        self.assertEqual(self.f.invoke("observe", response)["next_action"]["operation"], "continue-host")
        self.assertEqual(self.f.state()["retained_host_actions"][0]["resolution"]["outcome"], outcome)

    def test_original_action_proven_not_issued_can_resume(self): self.terminal_outcome("not-issued")
    def test_original_action_proven_cancelled_can_resume(self): self.terminal_outcome("cancelled")
