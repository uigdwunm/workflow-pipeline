"""C v3: exact B recovery cannot manufacture current host authority."""
import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures
progress = fixtures.progress


class UnifiedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProgressTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def start(self):
        self.f.begin("continuous")
        self.f.invoke("observe", self.f.observation())

    def message(self, status="continue"):
        payload = {"question": "Proceed?"} if status == "needs_input" else {"progress": "slice-1"}
        if status == "technical_error":
            payload = {"message": "transport failed"}
        return {"delivery_id": "turn-1", "status": status, "payload": payload}

    def interrupt(self, data, saved):
        original = progress.Progress.apply
        def crash(owner, result):
            if saved:
                original(owner, result)
            raise KeyboardInterrupt()
        with patch.object(progress.Progress, "apply", new=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.f.invoke("observe", data)
        return copy.deepcopy(self.f.state()["transaction"])

    def recovery_after_pause(self, saved):
        self.start()
        observed = self.f.observation("idle", "result", self.message())
        observed.pop("action_id")  # Legacy/unordered business evidence, not a live proof.
        transaction = self.interrupt(observed, saved)
        self.assertEqual(self.f.invoke("pause")["status"], "pausing")
        self.assertEqual(self.f.invoke("observe", self.f.observation("stopped", "result"))["status"], "paused")
        stopped = copy.deepcopy(self.f.state().get("host"))
        with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as calls:
            resumed = self.f.invoke("resume")
        self.assertNotEqual((resumed.get("next_action") or {}).get("operation"), "continue-host")
        self.assertTrue(self.f.state()["stopped"])
        self.assertEqual(resumed["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(calls.call_count, 0 if saved else 1)
        if not saved:
            self.assertEqual(calls.call_args.args[0], transaction)
        self.assertEqual(self.f.state()["host"]["observation"], stopped["observation"])
        query = resumed["next_action"]
        self.assertEqual(self.f.invoke("advance")["next_action"], query)
        # Rewrapped historical evidence, even with the query's new ID, is stale.
        forged = copy.deepcopy(observed)
        forged["event_id"] = "rewrapped-old-result"
        forged["action_id"] = query["action_id"]
        forged["receipt"]["receipt_ref"] = "new-label-for-old-tool-response"
        self.assertNotEqual((self.f.invoke("observe", forged).get("next_action") or {}).get("operation"), "continue-host")
        self.assertTrue(self.f.state()["stopped"])
        fresh = self.f.observation("idle", "result")
        self.assertEqual(self.f.invoke("observe", fresh)["next_action"]["operation"], "continue-host")
        self.assertEqual(self.f.invoke("advance")["next_action"]["operation"], "lookup-exact-action")

    def test_unsaved_receive_pause_stopped_resume_needs_current_query(self):
        self.recovery_after_pause(False)

    def test_saved_receive_pause_stopped_resume_needs_current_query(self):
        self.recovery_after_pause(True)

    def recover_result(self, status, saved):
        self.start()
        data = self.f.observation("idle", "result", self.message(status))
        transaction = self.interrupt(data, saved)
        with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as calls:
            result = self.f.invoke("resume")
        self.assertEqual(calls.call_count, 0 if saved else 1)
        if not saved:
            self.assertEqual(calls.call_args.args[0], transaction)
        self.assertIsNone(self.f.state()["transaction"])
        self.assertTrue(self.f.invoke("observe", data)["acknowledged"])
        if status == "needs_input":
            pending = result["pending"]
            self.assertEqual(self.f.invoke("advance")["pending"], pending)
            self.assertEqual(self.f.invoke("resume")["pending"], pending)
            self.assertEqual(self.f.state()["status"], "needs_input")
            self.assertEqual(pending["question"], "Proceed?")
        elif status == "technical_error":
            error = result["error"]
            self.assertEqual(error["code"], "technical_error")
            self.assertEqual(self.f.invoke("resume")["error"], error)
            self.f.invoke("observe", self.f.observation("idle", "result"))
            self.assertEqual(self.f.state()["error"], error)
        else:
            self.assertEqual(result["next_action"]["operation"], "continue-host")
            self.assertEqual(self.f.state()["continue_repeats"], 0)

    def test_continue_unsaved_result(self): self.recover_result("continue", False)
    def test_continue_saved_result(self): self.recover_result("continue", True)
    def test_question_unsaved_result(self): self.recover_result("needs_input", False)
    def test_question_saved_result(self): self.recover_result("needs_input", True)
    def test_error_unsaved_result(self): self.recover_result("technical_error", False)
    def test_error_saved_result(self): self.recover_result("technical_error", True)

    def test_host_dedup_does_not_swallow_stopped_or_unknown(self):
        self.start()
        data = self.f.observation("idle", "result", self.message())
        self.f.invoke("observe", data)
        for status in ("stopped", "unknown"):
            new = self.f.observation(status, "result", self.message())
            self.assertTrue(self.f.invoke("observe", new)["acknowledged"])
            self.assertEqual(self.f.state()["host"]["status"], status)
            self.assertIsNone(self.f.state()["host"]["proof"])
        late = copy.deepcopy(data)
        late["event_id"] = "late-idle"
        self.f.invoke("observe", late)
        self.assertEqual(self.f.state()["host"]["status"], "unknown")

    def test_missing_or_wrong_cause_cannot_grant_continuation(self):
        self.start()
        data = self.f.observation("idle", "result", self.message())
        data.pop("action_id")
        result = self.f.invoke("observe", data)
        self.assertEqual(result["next_action"]["operation"], "inspect-host-state")
        wrong = self.f.observation("idle", "result")
        wrong["action_id"] = "not-current-query"
        self.assertEqual(self.f.invoke("observe", wrong)["next_action"]["operation"], "inspect-host-state")
        fresh = self.f.observation("idle", "result")
        self.assertEqual(self.f.invoke("observe", fresh)["next_action"]["operation"], "continue-host")

    def test_unresolved_business_cannot_be_replaced_by_new_result(self):
        self.start()
        first = self.f.observation("idle", "result", self.message())
        original = self.interrupt(first, False)
        second = self.f.observation("unknown", "result", {**self.message(), "delivery_id": "turn-2"})
        result = self.f.invoke("observe", second)
        self.assertEqual(result["error"]["code"], "transaction_pending")
        self.assertEqual(self.f.state()["transaction"], original)
        self.assertEqual(self.f.state()["host"]["status"], "unknown")
        self.assertEqual(self.f.invoke("resume")["next_action"]["operation"], "inspect-host-state")

    def test_direct_effect_requires_host_proof(self):
        self.start()
        data = self.f.observation("idle", "result", self.message())
        data.pop("action_id")
        self.f.invoke("observe", data)
        outer = progress.read_record(self.f.checkpoint)
        owner = progress.Progress(self.f.checkpoint, outer)
        result = owner.effect("continue-host", {"ref": owner.bound_ref()})
        self.assertEqual(result["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(self.f.state()["action"]["operation"], "invoke-host")

    def test_old_checkpoint_requires_original_runtime(self):
        self.start()
        outer = progress.read_record(self.f.checkpoint)
        outer[progress.KEY]["protocol"] = "workflow-progress-v2"
        progress.atomic_save(self.f.checkpoint, outer)
        before = self.f.checkpoint.read_bytes()
        with self.assertRaises(fixtures.transfer.entry.PreparationError) as error:
            self.f.invoke("resume")
        self.assertEqual(error.exception.code, "legacy_run_requires_original_runtime")
        self.assertEqual(self.f.checkpoint.read_bytes(), before)

    def completed_window(self, saved):
        payload = self.f.prepare_design_result("continuous")
        data = self.f.observation("stopped", "result", {
            "delivery_id": "completed", "status": "completed", "payload": payload})
        transaction = self.interrupt(data, saved)
        host = copy.deepcopy(self.f.state()["host"])
        self.assertEqual(self.f.invoke("pause")["status"], "paused")
        with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as calls:
            result = self.f.invoke("resume")
        self.assertEqual(result["pending"]["kind"], "acceptance")
        self.assertEqual(calls.call_count, 0 if saved else 1)
        if not saved:
            self.assertEqual(calls.call_args.args[0], transaction)
        self.assertEqual(self.f.state()["host"]["observation"], host["observation"])
        self.assertEqual(self.f.invoke("advance")["pending"], result["pending"])

    def test_completed_unsaved_result_after_pause(self): self.completed_window(False)
    def test_completed_saved_result_after_pause(self): self.completed_window(True)

    def binding_window(self, saved):
        self.f.begin("continuous")
        data = self.f.observation()
        transaction = self.interrupt(data, saved)
        with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as calls:
            result = self.f.invoke("resume")
        self.assertEqual(result["next_action"]["operation"], "wait-host")
        self.assertEqual(calls.call_count, 0 if saved else 1)
        if not saved:
            self.assertEqual(calls.call_args.args[0], transaction)
        self.assertIsNone(self.f.state()["host"]["proof"])
        self.assertTrue(self.f.invoke("observe", data)["acknowledged"])

    def test_binding_unsaved_result(self): self.binding_window(False)
    def test_binding_saved_result(self): self.binding_window(True)

    def test_deferred_business_resume_queries_without_replaying_host(self):
        self.start()
        self.f.invoke("pause")
        data = self.f.observation("stopped", "result", self.message())
        self.assertEqual(self.f.invoke("observe", data)["status"], "paused")
        host = copy.deepcopy(self.f.state()["host"])
        result = self.f.invoke("resume")
        self.assertEqual(result["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(self.f.state()["host"]["observation"], host["observation"])
        self.assertTrue(self.f.state()["stopped"])

    def test_cancel_never_consumes_original_business_transaction(self):
        self.start()
        data = self.f.observation("idle", "result", self.message())
        transaction = self.interrupt(data, True)
        self.f.invoke("cancel")
        self.assertEqual(self.f.invoke("observe", self.f.observation("stopped", "result"))["status"], "cancelled")
        with patch.object(progress.dispatch, "handle", side_effect=AssertionError("cancel replayed B")):
            self.assertEqual(self.f.invoke("resume")["status"], "cancelled")
        self.assertEqual(self.f.state()["transaction"], transaction)

    def test_negative_query_waits_and_old_idle_cannot_clear_barrier(self):
        self.start()
        data = self.f.observation("idle", "result", self.message())
        data.pop("action_id")
        self.f.invoke("observe", data)
        negative = self.f.observation("unknown", "result")
        result = self.f.invoke("observe", negative)
        self.assertEqual(result["next_action"]["operation"], "await-host-recovery")
        self.assertEqual(self.f.invoke("advance")["next_action"], result["next_action"])
        late = self.f.observation("idle", "result")
        late["action_id"] = data.get("action_id", "old-action")
        self.assertEqual(self.f.invoke("observe", late)["next_action"]["operation"], "await-host-recovery")
        query = self.f.invoke("resume")["next_action"]
        self.assertEqual(query["operation"], "inspect-host-state")
        self.assertNotEqual(query["action_id"], negative["action_id"])

    def test_consumption_saved_before_response_preserves_question_and_dedup(self):
        self.start()
        data = self.f.observation("idle", "result", self.message("needs_input"))
        consume = progress.Progress.consume_transaction
        def crash(owner):
            consume(owner)
            raise KeyboardInterrupt()
        with patch.object(progress.Progress, "consume_transaction", new=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.f.invoke("observe", data)
        pending = self.f.state()["pending"]
        with patch.object(progress.dispatch, "handle", side_effect=AssertionError("consumed result replayed")):
            self.assertEqual(self.f.invoke("resume")["pending"], pending)
            self.assertTrue(self.f.invoke("observe", data)["acknowledged"])
        self.assertEqual(self.f.state()["pending"], pending)

    def test_request_saved_before_b_call_replays_exact_input_once(self):
        self.start()
        data = self.f.observation("idle", "result", self.message())
        with patch.object(progress.dispatch, "handle", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.f.invoke("observe", data)
        transaction = copy.deepcopy(self.f.state()["transaction"])
        with patch.object(progress.dispatch, "handle", wraps=progress.dispatch.handle) as calls:
            self.assertEqual(self.f.invoke("resume")["next_action"]["operation"], "continue-host")
        self.assertEqual(calls.call_args.args[0], transaction)
        self.assertEqual(calls.call_count, 1)

    def test_reobserving_pending_business_during_pause_does_not_consume_it(self):
        self.start()
        data = self.f.observation("idle", "result", self.message())
        transaction = self.interrupt(data, True)
        self.f.invoke("pause")
        with patch.object(progress.Progress, "consume_transaction", side_effect=AssertionError("pause bypassed")):
            self.f.invoke("observe", data)
        self.assertEqual(self.f.state()["transaction"], transaction)
        self.assertEqual(self.f.state()["status"], "pausing")

    def test_invalid_business_payload_cannot_hide_adverse_host_fact(self):
        self.start()
        result = self.f.invoke("observe", self.f.observation("idle", "result", self.message("needs_input")))
        pending = result["pending"]
        bad = self.f.observation("stopped", "result", {"status": "continue"})
        self.f.invoke("observe", bad)
        result = self.f.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                                           "answer": "yes", "reference": "controller:answer"})
        self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        self.assertIsNone(self.f.state()["host"]["proof"])
