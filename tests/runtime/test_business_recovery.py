"""V4 recovery with real A/B/Git; only invocation capture is a host fixture."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from test_unified_recovery import fixtures, progress


class BusinessRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProgressTests(methodName="runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def start(self, mode="continuous"):
        self.f.begin(mode)
        self.f.invoke("observe", self.f.observation())

    def business_fail(self, code="technical_error"):
        for index in range(3 if code == "no_progress" else 1):
            data = {"delivery_id": "failure-" + str(len(self.f.state()["events"])),
                    "status": "continue" if code == "no_progress" else code,
                    "payload": {"message": "same unresolved issue"}}
            result = self.f.invoke("observe", self.f.observation("idle", "result", data))
        self.assertEqual(result["business_block"]["code"], code)
        return copy.deepcopy(result["business_block"])

    def decision(self):
        return {"decision_id": "recovery:" + self.f.state()["business_block"]["id"],
                "subject": copy.deepcopy(self.f.state()["business_block"]["subject"]),
                "reference": "controller:review-1", "diagnosis": "dependency repaired and reviewed",
                "instruction": "retry the original bounded operation", "expected_progress": "new verified result"}

    def assert_no_continue(self, value):
        self.assertNotEqual((value.get("next_action") or {}).get("operation"), "continue-host")

    def recover(self, code, mode):
        self.start(mode)
        block = self.business_fail(code)
        identity = copy.deepcopy(self.f.state()["dispatch"]["request"])
        decision = self.decision()
        result = self.f.invoke("recover-business", decision)
        self.assertEqual(result["status"], "active")
        self.assert_no_continue(result)
        self.assertIsNone(self.f.state()["business_block"])
        self.assertIsNone(self.f.state()["host"]["proof"])
        self.assertEqual(self.f.state()["business_history"][0]["block"], block)
        query = self.f.invoke("resume")["next_action"]
        self.assertEqual(query["operation"], "inspect-host-state")
        result = self.f.invoke("observe", self.f.observation("idle", "result"))
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertEqual(self.f.state()["dispatch"]["request"], identity)
        self.assertTrue(self.f.invoke("recover-business", decision, revision=0)["acknowledged"])
        self.assertEqual(self.f.invoke("advance")["next_action"]["operation"], "lookup-exact-action")
        return decision

    def test_technical_recovery_continuous(self): self.recover("technical_error", "continuous")
    def test_technical_recovery_stepwise(self): self.recover("technical_error", "stepwise")
    def test_no_progress_recovery_continuous(self): self.recover("no_progress", "continuous")
    def test_no_progress_recovery_stepwise(self): self.recover("no_progress", "stepwise")

    def test_recovery_does_not_disable_later_no_progress_limit(self):
        old = self.recover("no_progress", "continuous")
        block = self.business_fail("no_progress")
        self.assertNotEqual(block["subject"], old["subject"])
        self.assertTrue(self.f.invoke("recover-business", old)["acknowledged"])
        self.assertEqual(self.f.state()["business_block"], block)
        self.assertEqual(self.f.invoke("resume")["status"], "blocked")

    def test_new_business_or_creation_lookup_cannot_clear_block(self):
        self.start()
        block = self.business_fail()
        for status in ("continue", "needs_input", "completed"):
            payload = {"question": "new question"} if status == "needs_input" else {"new": "payload"}
            result = self.f.invoke("observe", self.f.observation("idle", "result", {
                "delivery_id": status, "status": status, "payload": payload}))
            self.assertEqual(result["business_block"], block)
            self.assert_no_continue(result)
        self.f.invoke("observe", self.f.observation("ready", "lookup"))
        self.assertEqual(self.f.state()["business_block"], block)
        self.assertIsNone(self.f.state()["pending"])

    def test_wrong_subject_and_missing_diagnosis_cannot_recover(self):
        self.start()
        block = self.business_fail()
        for field in ("ref", "attempt", "controller_ref", "block_id", "handoff"):
            data = self.decision()
            data["subject"][field] = "wrong"
            self.assertEqual(self.f.invoke("recover-business", data)["status"], "blocked")
            self.assertEqual(self.f.state()["business_block"], block)
        data = self.decision()
        data["diagnosis"] = ""
        self.assertEqual(self.f.invoke("recover-business", data)["status"], "blocked")
        self.assertEqual(self.f.state()["business_block"], block)

    def test_pause_defers_recovery_until_explicit_resume(self):
        self.start()
        block = self.business_fail()
        decision = self.decision()
        self.assertEqual(self.f.invoke("pause")["status"], "pausing")
        self.f.invoke("recover-business", decision)
        self.assertEqual(self.f.state()["business_block"], block)
        self.assert_no_continue(self.f.invoke("resume"))
        self.assertEqual(self.f.state()["status"], "pausing")
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        self.assertEqual(self.f.state()["status"], "paused")
        self.assertEqual(self.f.invoke("advance")["status"], "paused")
        result = self.f.invoke("resume")
        self.assertEqual(result["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(self.f.state()["recovery_decisions"][decision["decision_id"]], decision)

    def test_cancel_does_not_apply_deferred_recovery(self):
        self.start()
        self.business_fail()
        decision = self.decision()
        self.f.invoke("pause")
        self.f.invoke("recover-business", decision)
        self.f.invoke("cancel")
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        self.assert_no_continue(self.f.invoke("resume"))
        self.assertIsNotNone(self.f.state()["business_block"])
        self.assertNotIn(decision["decision_id"], self.f.state().get("recovery_decisions", {}))

    def test_recovery_save_before_and_after_failure_is_atomic(self):
        self.start()
        block = self.business_fail()
        decision = self.decision()
        original = progress.atomic_save
        for after in (False, True):
            def crash(path, value):
                if after:
                    original(path, value)
                raise KeyboardInterrupt()
            with patch.object(progress, "atomic_save", new=crash):
                with self.assertRaises(KeyboardInterrupt):
                    self.f.invoke("recover-business", decision)
            if not after:
                self.assertEqual(self.f.state()["business_block"], block)
            else:
                self.assertIsNone(self.f.state()["business_block"])
                self.assertTrue(self.f.invoke("recover-business", decision)["acknowledged"])
        self.assertEqual(len(self.f.state()["business_history"]), 1)
        self.assertEqual(self.f.invoke("resume")["next_action"]["operation"], "inspect-host-state")

    def test_identical_raw_from_new_query_is_live_but_rewrapped_response_is_not(self):
        self.start()
        initial = self.f.observation("idle", "result", {"delivery_id": "first", "status": "continue", "payload": {"slice": 1}})
        self.f.invoke("observe", initial)
        self.f.invoke("pause")
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        self.assertEqual(self.f.state()['status'],'pausing')
        self.assertEqual(self.f.invoke('observe',self.f.observation('stopped','result'))['status'],'paused')
        query = self.f.invoke("resume")["next_action"]
        forged = copy.deepcopy(initial)
        forged["event_id"] = "old-response-new-label"
        forged["receipt"]["receipt_ref"] = "new-label"
        forged["action_id"] = query["action_id"]
        self.assert_no_continue(self.f.invoke("observe", forged))
        fresh = self.f.observation("idle", "result")
        fresh["receipt"]["raw"] = copy.deepcopy(initial["receipt"]["raw"])
        self.assertNotIn("tool_response_id", fresh["receipt"]["raw"])
        result = self.f.invoke("observe", fresh)
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertIsNotNone(self.f.state()["host"]["last_stop"])

    def test_missing_provenance_cannot_grant_proof_but_stop_revokes(self):
        self.start()
        data = self.f.observation("idle", "result", {"delivery_id": "first", "status": "continue", "payload": {"slice": 1}})
        data.pop("provenance")
        self.assertEqual(self.f.invoke("observe", data)["next_action"]["operation"], "inspect-host-state")
        self.f.invoke("observe", self.f.observation("idle", "result"))
        pending = self.f.invoke("observe", self.f.observation("idle", "result", {
            "delivery_id": "question", "status": "needs_input", "payload": {"question": "Continue?"}}))["pending"]
        stop = self.f.observation("stopped", "result")
        stop.pop("provenance")
        self.f.invoke("observe", stop)
        self.assertEqual(self.f.state()["host"]["status"], "unknown")
        self.assertIsNone(self.f.state()["host"]["proof"])
        self.assert_no_continue(self.f.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                                                        "answer": "yes", "reference": "controller:yes"}))

    def test_provenance_conflict_revokes_without_rebinding_old_response(self):
        self.start()
        first = self.f.observation("idle", "result", {"delivery_id": "first", "status": "continue", "payload": {"slice": 1}})
        self.f.invoke("observe", first)
        newer = self.f.observation("idle", "result")
        newer["provenance"]["response_ref"] = first["provenance"]["response_ref"]
        result = self.f.invoke("observe", newer)
        self.assertEqual(result["error"]["code"], "host_provenance_conflict")
        self.assertIsNone(self.f.state()["host"]["proof"])
        self.assertEqual(self.f.invoke("observe", newer)["error"]["code"], "host_provenance_conflict")

    def test_interrupted_explicit_unpause_consumes_same_deferred_recovery(self):
        self.start()
        self.business_fail()
        decision = self.decision()
        self.f.invoke("pause")
        self.f.invoke("recover-business", decision)
        self.f.invoke("observe", self.f.observation("stopped", "result"))
        with patch.object(progress.Progress, "recover_business", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.f.invoke("resume")
        self.assertNotIn("stop_requested", self.f.state())
        self.assertEqual(self.f.state()["deferred_business_recovery"], decision)
        result = self.f.invoke("resume")
        self.assertEqual(result["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(len(self.f.state()["business_history"]), 1)

    def test_unsupported_query_without_provenance_waits(self):
        self.start()
        message = self.f.observation("idle", "result", {"delivery_id": "first", "status": "continue", "payload": {"slice": 1}})
        message.pop("provenance")
        self.f.invoke("observe", message)
        negative = self.f.observation("unknown", "result")
        negative.pop("provenance")
        result = self.f.invoke("observe", negative)
        self.assertEqual(result["next_action"]["operation"], "await-host-recovery")
        self.assertEqual(self.f.invoke("advance")["next_action"], result["next_action"])

    def test_old_v3_member_is_rejected_without_writing(self):
        self.start()
        outer = progress.read_record(self.f.checkpoint)
        outer[progress.KEY]["protocol"] = "workflow-progress-v3"
        progress.atomic_save(self.f.checkpoint, outer)
        before = self.f.checkpoint.read_bytes()
        for operation in ("resume", "prepare-requirement", "lifecycle", "recover-business"):
            with self.subTest(operation=operation):
                with self.assertRaises(progress.entry.PreparationError) as error:
                    self.f.invoke(operation)
                self.assertEqual(error.exception.code, "legacy_run_requires_original_runtime")
                self.assertEqual(self.f.checkpoint.read_bytes(), before)

    def test_recovery_instruction_is_delivered_and_source_drift_still_blocks(self):
        self.start()
        self.business_fail()
        decision = self.decision()
        self.f.invoke("recover-business", decision)
        self.f.invoke("resume")
        (self.f.flow / "docs/requirements/a.md").write_text("changed frozen source\n")
        result = self.f.invoke("observe", self.f.observation("idle", "result"))
        self.assert_no_continue(result)
        self.assertEqual(result["status"], "blocked")

    def test_original_dispatcher_recovery_clears_only_its_bound_block(self):
        f = self.f
        f.finish_design("continuous")
        predecessor = f.state()["accepted"]
        f.context = f.context_for(3)
        f.begin("continuous", f.input_for(3, predecessor))
        f.invoke("observe", f.observation(ref="native:dispatcher"))
        failed = f.invoke("observe", f.observation("idle", "result", {
            "delivery_id": "dispatcher-error", "status": "technical_error", "payload": {"message": "worker failure"}}, ref="native:dispatcher"))
        block = copy.deepcopy(failed["business_block"])
        f.invoke('pause')
        stopped = f.observation('stopped','result',ref='native:dispatcher')
        stopped['receipt']['raw'].update(resumable=False, dispatch_available=True)
        f.invoke('observe',stopped); f.invoke('resume')
        old = f.state()['control']['context']['carrier']
        prepared = f.invoke('control',{'action':'prepare-dispatch-recovery','evidence':{
            'dispatcher_ref':old['ref'],'attempt':old['attempt'],'reference':'controller:prepare','reason':'cannot resume',
            'stop_receipts':[stopped['receipt']['receipt_ref']],'call_receipts':[stopped['provenance']['response_ref']]},
            'receipt':{'controller_ref':'task','reference':'controller:prepare'}})['next_action']['result']['recovery']
        decision = {'reference':'controller:assume','authority_digest':prepared['authority_digest'],'attempt':prepared['attempt'],
            'remaining_paths':prepared['remaining_paths'],'assume_paths':prepared['ownership']['dispatcher']}
        identity = {'recovery_id':prepared['recovery_id'],'snapshot_digest':prepared['snapshot_digest'],'decision':decision}
        owner = {'controller_ref':'task','reference':'controller:assume'}
        intent = f.invoke('control',{'action':'dispatch-recovery-intent','evidence':identity,'receipt':owner})['next_action']['request']
        receipt = {'adapter':'fixture-native','call_ref':'replacement-call','response_ref':'replacement-response',
            'intent_id':intent['intent_id'],'status':'ready','ref':'native:replacement','raw':{'write_authority':False}}
        f.invoke('control',{'action':'dispatch-recovery-result','evidence':{'recovery_id':prepared['recovery_id'],'receipt':receipt},'receipt':receipt})
        snapshot = f.observation('stopped','result',ref='native:dispatcher')['lifecycle']
        snapshot['pages'][0]['response']['data'].append({'id':'native:replacement','status':{'type':'idle'}})
        f.invoke('lifecycle-state',snapshot)
        recovery = {'action':'recover-dispatch','evidence':{**identity,'replacement_ref':'native:replacement'},'receipt':owner}
        f.invoke('control',recovery)
        self.assertIsNone(f.state()["business_block"])
        self.assertEqual(f.state()["business_history"][0]["block"], block)
        self.assertEqual(f.state()["business_history"][0]["kind"], "recover-dispatch")
        self.assertEqual(f.state()["control"]["context"]["carrier"]["ref"], "native:replacement")
        self.assertIsNone(f.state()["host"]["proof"])
        newer = f.invoke("observe", f.observation("idle", "result", {
            "delivery_id": "replacement-error", "status": "technical_error", "payload": {"message": "new failure"}}, ref="native:replacement"))
        self.assertTrue(f.invoke("control", recovery)["acknowledged"])
        self.assertEqual(f.state()["business_block"], newer["business_block"])

    def test_uncorrelated_error_recovery_settles_original_action_before_continue(self):
        self.start()
        f = self.f
        f.invoke("observe", f.observation("idle", "result", {
            "delivery_id": "continue-before-error", "status": "continue", "payload": {"slice": 1}}))
        outstanding = copy.deepcopy(f.state()["host_action"])
        failure = f.observation("idle", "result", {
            "delivery_id": "uncorrelated-error", "status": "technical_error", "payload": {"message": "transport lost"}})
        failure.pop("provenance")
        f.invoke("observe", failure)
        f.invoke("recover-business", self.decision())
        query = f.invoke("resume")["next_action"]
        self.assertEqual(query["operation"], "inspect-host-state")
        self.assertEqual(query["payload"]["unresolved_action"], outstanding)
        idle = f.observation("idle", "result")
        idle.pop("action_resolution")
        self.assertEqual(f.invoke("observe", idle)["next_action"]["operation"], "await-host-recovery")
        self.assertFalse(f.state()["host_action"].get("resolved", False))
        self.assertIsNone(f.state()["host"]["proof"])
        f.invoke("resume")
        wrong = f.observation("idle", "result")
        wrong["action_resolution"]["action_id"] = "another-action"
        self.assertEqual(f.invoke("observe", wrong)["next_action"]["operation"], "await-host-recovery")
        f.invoke("resume")
        settled = f.observation("idle", "result")
        self.assertEqual(settled["action_resolution"]["action_id"], outstanding["action_id"])
        result = f.invoke("observe", settled)
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertNotEqual(result["next_action"]["action_id"], outstanding["action_id"])
        self.assertEqual(f.invoke("advance")["next_action"]["operation"], "lookup-exact-action")

    def test_unknown_action_outcome_requires_exact_settlement(self):
        self.start()
        f = self.f
        self.assertIsNone(f.state()['host']['proof'])
        self.assertFalse(f.state()['stopped'])
        continued = f.invoke('observe',f.observation('idle','result',{
            'delivery_id':'initial-complete','status':'continue','payload':{'slice':0}}))
        self.assertEqual(continued['next_action']['operation'],'continue-host')
        original = copy.deepcopy(f.state()["host_action"])
        result = f.invoke("observe", f.observation("unknown", "result", {
            "delivery_id": "unknown-action", "status": "continue", "payload": {"slice": 1}}))
        query = result["next_action"]
        self.assertEqual(query["operation"], "inspect-host-state")
        self.assertEqual(query["payload"]["unresolved_action"]["action_id"], original["action_id"])
        self.assertFalse(f.state()["host_action"].get("resolved", False))
        fresh = f.observation("idle", "result")
        fresh["action_resolution"]["outcome"] = {"untrusted": "completed"}
        self.assertEqual(f.invoke("observe", fresh)["next_action"]["operation"], "await-host-recovery")
        self.assertIsNone(f.state()["host"]["proof"])
