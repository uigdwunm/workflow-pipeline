"""C orchestration through real A, B, Git and ledger; only the host is a double."""
import copy
import json
import tempfile
import sys
import unittest
import io
import subprocess
import hashlib
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_transfer as transfer
import workflow_progress as progress
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/stages/guided-implementation/scripts"))
import workflow as runner
from test_discussion_protocol import DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport


class ProgressTests(transfer.StageTransferTests):
    # Reuse fixture helpers, not the parent's test inventory.
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkpoint = Path(temporary.name) / "checkpoint.json"

    def state(self):
        return progress.read_record(self.checkpoint)[progress.KEY]

    def invoke(self, operation, data=None, revision=None):
        current = progress.read_record(self.checkpoint).get(progress.KEY, {}).get("revision", 0)
        return progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL, "operation": operation,
            "expected_revision": current if revision is None else revision, "data": data or {}})

    def begin(self, mode="stepwise", input_data=None):
        value = copy.deepcopy(input_data or self.input)
        value["authorization"]["flow_mode"] = mode
        result = self.invoke("start", {"handoff": value, "control": {"context": self.context, "discussion": None}})
        self.assertEqual(result["next_action"]["operation"], "invoke-host")
        return result

    def observation(self, status="ready", event="create", result=None, ref="native:designer"):
        saved = self.state()
        value = {"event_id": "event-" + str(len(saved["events"])),
                 "receipt": self.receipt(saved["dispatch"], status, event, ref=ref)}
        if result is not None:
            value["result"] = result
        return value

    def test_launch_is_saved_before_host_and_never_reissued(self):
        self.begin()
        saved = self.state()
        self.assertEqual(saved["step"], "host-response")
        again = self.invoke("advance")
        self.assertEqual(again["next_action"]["operation"], "lookup-exact-action")
        self.assertEqual(self.state()["dispatch"], saved["dispatch"])
        observed = self.observation()
        bound = self.invoke("observe", observed)
        self.assertEqual(bound["next_action"]["operation"], "wait-host")
        self.assertTrue(self.invoke("observe", observed, revision=0)["acknowledged"])

    def test_unknown_preserves_attempt_and_wrong_receipt(self):
        self.begin()
        unknown = self.observation("unknown", ref=None)
        result = self.invoke("observe", unknown)
        self.assertEqual(result["next_action"]["operation"], "lookup-exact-action")
        wrong = self.observation("ready", "lookup")
        wrong["receipt"]["attempt"] = "wrong"
        self.assertEqual(self.invoke("observe", wrong)["status"], "blocked")
        self.assertEqual(len(self.state()["observations"]), 2)
        ready = self.observation("ready", "lookup")
        self.invoke("observe", ready)
        self.assertEqual(self.state()["dispatch"]["status"], "bound")

    def test_proven_noncreation_can_retry_only_with_new_controller_request(self):
        self.begin("continuous")
        old_request = self.state()["dispatch"]["request"]
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        retry = copy.deepcopy(self.input)
        retry["authorization"]["flow_mode"] = "continuous"
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("start", {"handoff": retry})
        retry["authorization"]["reference"] = "controller:retry-proven-not-created"
        started = self.invoke("start", {"handoff": retry})
        self.assertEqual(started["next_action"]["operation"], "invoke-host")
        self.assertNotEqual(self.state()["dispatch"]["request"]["digest"], old_request["digest"])
        late = self.observation()
        late["receipt"]["request_digest"] = old_request["digest"]
        self.assertEqual(self.invoke("observe", late)["status"], "blocked")

    def test_continue_uses_same_identity_and_no_duplicate_send(self):
        self.begin()
        self.invoke("observe", self.observation())
        value = self.observation("idle", "result", {"delivery_id": "turn-1", "status": "continue", "payload": {"progress": "slice-1"}})
        result = self.invoke("observe", value)
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertEqual(result["next_action"]["payload"]["ref"], "native:designer")
        self.assertEqual(self.invoke("advance")["next_action"]["operation"], "lookup-exact-action")
        action = self.state()["action"]
        duplicate_poll = copy.deepcopy(value)
        duplicate_poll["event_id"] = "second-host-poll"
        duplicate_poll["receipt"]["receipt_ref"] = "host:new-poll-same-result"
        self.assertTrue(self.invoke("observe", duplicate_poll)["acknowledged"])
        self.assertEqual(self.state()["action"], action)
        (self.flow / self.requirement_path).write_text("unexpected source drift\n")
        next_turn = self.observation("idle", "result", {"delivery_id": "turn-2", "status": "continue", "payload": {"progress": "slice-2"}})
        self.assertEqual(self.invoke("observe", next_turn)["status"], "blocked")
        self.assertEqual(self.state()["action"], action)

    def test_exact_decision_and_duplicate_answer(self):
        self.begin()
        self.invoke("observe", self.observation())
        value = self.observation("idle", "result", {"delivery_id": "turn-1", "status": "needs_input", "payload": {"question": "choose behavior"}})
        pending = self.invoke("observe", value)["pending"]
        answer = {"decision_id": pending["decision_id"], "subject": pending["subject"], "answer": "choice A", "reference": "controller:answer"}
        result = self.invoke("decide", answer)
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertTrue(self.invoke("decide", answer, revision=0)["acknowledged"])

    def prepare_design_result(self, mode):
        self.begin(mode)
        self.invoke("observe", self.observation())
        (self.flow / "docs/spec.md").write_text("approved design\n")
        self.flow_git("add", "docs/spec.md")
        self.flow_git("commit", "-qm", "plan")
        planning = self.flow_git("rev-parse", "HEAD")
        published = self.invoke("publication", {"candidate_commit": planning, "reference": "controller:planning-readiness"})
        self.assertTrue(published["next_action"]["publication"]["result"]["ok"], published)
        payload = {"artifacts": ["docs/spec.md"], "checks": ["design readiness"], "planning_commit": planning,
                   "planning_merge_commit": self.flow_git("rev-parse", "HEAD"), "planning_paths": ["docs/spec.md"]}
        return payload

    def finish_design(self, mode):
        payload = self.prepare_design_result(mode)
        observed = self.observation("stopped", "result", {"delivery_id": "design", "status": "completed", "payload": payload})
        pending = self.invoke("observe", observed)["pending"]
        answer = {"decision_id": pending["decision_id"], "subject": pending["subject"], "answer": "accept", "reference": "controller:readiness"}
        return self.invoke("decide", answer)

    def test_real_acceptance_and_stepwise_boundary(self):
        result = self.finish_design("stepwise")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["pending"]["kind"], "stage-entry")
        self.assertIn("stage_result", result["completion"])
        self.assertEqual(self.invoke("advance")["status"], "accepted")

    def test_continuous_acceptance_has_no_human_stage_gate(self):
        result = self.finish_design("continuous")
        self.assertIsNone(result["pending"])
        predecessor = self.state()["accepted"]
        self.context = self.context_for(3)
        self.begin("continuous", self.input_for(3, predecessor))
        self.assertEqual(len(self.state()["history"]), 1)

    def test_successor_derives_a_evidence_predecessor_and_control_without_field_copying(self):
        self.finish_design("continuous")
        next_input = self.input_for(3, self.state()["accepted"])
        verified = self.invoke("prepare-requirement", {"request": {"protocol": transfer.requirement.PROTOCOL,
            "operation": "verify", "entry": next_input["entry"], "evidence": self.frozen}})
        self.assertEqual(verified["status"], "requirement-ready", verified)
        for key in ("expected_entry", "predecessor", "requirement"):
            next_input.pop(key)
        next_input["authorization"]["flow_mode"] = "continuous"
        result = self.invoke("start", {"handoff": next_input, "requirement_transaction": verified["transaction"]})
        self.assertEqual(result["next_action"]["operation"], "invoke-host", result)
        self.assertEqual(self.state()["stage"], 3)
        self.assertEqual(self.state()["control"]["context"]["carrier"]["kind"], "implementation-dispatcher")

    def test_compatible_registration_switch_keeps_original_executing_package(self):
        packages = {name: {"root": str(self.root / ("old-" + name)), "compatibility_key": {"preparation": "v1"}}
                    for name in ("solution-design", "guided-implementation", "change-closure")}
        transfer.entry.skill_preflight.preflight.return_value = {"packages": packages, "external": {}}
        self.request["target_stages"] = [2, 3, 4]
        self.input = self.input_for(2)
        self.finish_design("continuous")
        next_input = self.input_for(3, self.state()["accepted"])
        old_root = Path(packages["guided-implementation"]["root"])
        old_root.mkdir()
        (old_root / "package.json").write_text("{}")
        registered = copy.deepcopy(packages)
        registered["guided-implementation"]["root"] = str(self.root / "new-guided-registration")
        transfer.entry.skill_preflight.preflight.return_value = {"packages": registered, "external": {}}
        self.context = self.context_for(3)
        with patch.object(transfer.entry, "__file__", str(old_root / "scripts/entry_prepare.py")):
            self.begin("continuous", next_input)
        self.assertEqual(self.state()["packages"]["guided-implementation"], packages["guided-implementation"])

    def test_pause_and_cancel_require_stopped_host(self):
        self.begin()
        self.invoke("observe", self.observation())
        self.assertEqual(self.invoke("pause")["status"], "pausing")
        self.assertEqual(self.invoke("observe", self.observation("stopped", "result"))["status"], "paused")
        self.assertEqual(self.invoke("resume")["next_action"]["operation"], "wait-host")
        self.assertEqual(self.invoke("cancel")["status"], "cancelled")

    def pending_business_answer(self, stage=2):
        if stage == 3:
            self.begin_dispatcher()
        else:
            self.begin()
            self.invoke("observe", self.observation())
        ref = "native:dispatcher" if stage == 3 else "native:designer"
        result = self.invoke("observe", self.observation("idle", "result", {
            "delivery_id": "question", "status": "needs_input", "payload": {"question": "Continue?"}}, ref=ref))
        pending = result["pending"]
        return {"decision_id": pending["decision_id"], "subject": pending["subject"],
                "answer": "yes", "reference": "controller:answer"}

    def test_business_answer_does_not_revoke_pause(self):
        answer = self.pending_business_answer()
        self.invoke("pause")
        result = self.invoke("decide", answer)
        self.assertEqual(result["status"], "pausing")
        self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        self.assertEqual(self.state()["pending"]["decision_id"], answer["decision_id"])
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))

    def test_business_answer_does_not_revoke_cancellation(self):
        answer = self.pending_business_answer()
        self.invoke("cancel")
        result = self.invoke("decide", answer)
        self.assertEqual(result["status"], "cancelling")
        self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        self.assertEqual(self.state()["stop_requested"], "cancelling")
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))

    def test_paused_answer_consumes_only_after_explicit_resume_and_live_observation(self):
        answer = self.pending_business_answer()
        self.invoke("pause")
        before = self.state()["action"]
        first = self.invoke("decide", answer)
        self.assertTrue(first["decision_deferred"])
        self.assertEqual(self.state()["action"], before)
        self.assertTrue(self.invoke("decide", answer, revision=0)["acknowledged"])
        self.assertEqual(self.invoke("resume")["status"], "pausing")
        self.invoke("observe", self.observation("stopped", "result"))
        self.assertEqual(self.invoke("decide", answer)["status"], "paused")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["decision"], answer)
        resumed = self.invoke("resume")
        self.assertEqual(resumed["next_action"]["operation"], "wait-host")
        self.assertEqual(self.state()["decisions"][answer["decision_id"]], answer)
        continued = self.invoke("observe", self.observation("idle", "result"))
        self.assertEqual(continued["next_action"]["operation"], "continue-host")
        action = self.state()["action"]
        self.assertTrue(self.invoke("decide", answer, revision=0)["acknowledged"])
        self.assertEqual(self.state()["action"], action)

    def test_cancelled_answer_needs_controller_recovery_and_new_reference(self):
        answer = self.pending_business_answer(stage=3)
        self.invoke("pause")
        self.invoke("decide", answer)
        self.invoke("cancel")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["disposition"], "cancelled")
        self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(self.invoke("decide", answer)["status"], "cancelled")
        self.assertEqual(self.invoke("resume")["status"], "cancelled")
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))
        self.invoke("control", {"action": "recover-dispatch", "evidence": {
            "stopped_refs": ["native:dispatcher"], "file_hashes": {}, "replacement_ref": "native:replacement"},
            "receipt": {"host": "fixture", "stopped": "native:dispatcher", "ready": "native:replacement"}})
        replay = self.invoke("decide", answer)
        self.assertTrue(replay["acknowledged"])
        self.assertNotEqual((replay.get("next_action") or {}).get("operation"), "continue-host")
        renewed = {**answer, "reference": "controller:renew-after-recovery"}
        result = self.invoke("decide", renewed)
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertEqual(result["next_action"]["payload"]["ref"], "native:replacement")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["decision"], answer)

    def test_answer_after_stop_receipt_is_saved_without_consuming_pending(self):
        answer = self.pending_business_answer()
        self.invoke("pause")
        self.invoke("observe", self.observation("stopped", "result"))
        saved = self.invoke("decide", answer)
        self.assertEqual(saved["status"], "paused")
        wrong = {**answer, "decision_id": "old-matter"}
        with self.assertRaises(transfer.entry.PreparationError): self.invoke("decide", wrong)
        with self.assertRaises(transfer.entry.PreparationError): self.invoke("decide", {**answer, "answer": "changed"})
        self.assertEqual(self.state()["status"], "paused")
        self.assertEqual(self.state()["pending"]["decision_id"], answer["decision_id"])
        self.assertEqual(self.invoke("resume")["next_action"]["operation"], "wait-host")

    def test_stop_intent_guards_continue_and_effect_even_if_status_is_active(self):
        self.begin()
        self.invoke("observe", self.observation())
        outer = progress.read_record(self.checkpoint)
        state = outer[progress.KEY]
        state.update(status="active", step="continue", stop_requested="cancelling")
        progress.atomic_save(self.checkpoint, outer)
        result = self.invoke("advance")
        self.assertEqual(result["status"], "cancelling")
        self.assertEqual(result["next_action"]["operation"], "stop-host")
        owner = progress.Progress(self.checkpoint, progress.read_record(self.checkpoint))
        for operation in ("invoke-host", "continue-host", "source-lifecycle", "carrier-lifecycle"):
            with self.assertRaises(transfer.entry.PreparationError): owner.effect(operation, {})

    def test_runner_stop_request_defers_reply_before_shared_suspend(self):
        answer = self.pending_business_answer()
        outer = progress.read_record(self.checkpoint)
        outer["runner_request"] = {"operation": "pause", "request_id": "saved-request"}
        progress.atomic_save(self.checkpoint, outer)
        response = self.invoke("decide", answer)
        self.assertEqual(response["status"], "pausing")
        self.assertTrue(response["decision_deferred"])
        self.invoke("observe", self.observation("stopped", "result"))
        self.assertEqual(self.invoke("resume")["status"], "paused")
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))

    def test_finished_receipt_intake_precedes_recovery_flags(self):
        for reason in ("technical", "pause", "consumed-decision"):
            with self.subTest(reason=reason):
                shared = {}
                consumed = reason == "consumed-decision"
                if consumed:
                    self.checkpoint.unlink()
                    queued = self.pending_business_answer()
                    self.invoke("decide", queued)
                    shared = progress.read_record(self.checkpoint)
                else:
                    queued = {"decision_id": "unconsumed", "subject": {"stage": 2}, "answer": "yes", "reference": "controller:input"}
                state = runner._new_state({})
                state.update(resume_progression=True, answer_pending_delivery="previous answer",
                             progression_response={"saved": reason})
                state["launch"] = {"stage": "stage2", "state": "launched", "turn": 1,
                                   "request": ["fixture", reason], "invocation_id": reason}
                state["controller_decision"] = queued
                self.checkpoint = self.checkpoint.resolve()
                progress.atomic_save(self.checkpoint, {**shared, **state, "workflow_requirements": {"keep": "C-owned"}})
                result = {"result": "needs_input", "artifacts": [], "evidence": [], "handoff_json": "{}",
                          "handoff": {}, "question": "Next choice?", "message": "", "needs_input_kind": "user_decision"}
                receipt = {"run_record": str(self.checkpoint), "stage": "stage2", "turn": 1, "invocation_id": reason,
                    "request_digest": transfer.entry.digest(state["launch"]["request"]), "outcome": "completed_turn",
                    "events": [{"type": "thread.started", "thread_id": "carrier"}, {"type": "turn.completed"}], "result": result}
                progress.atomic_save(runner._carrier_receipt_path(self.checkpoint, "stage2", 1), receipt)
                runner._recover_carrier_receipt(state, self.checkpoint)
                with patch.object(runner, "_invoke", side_effect=AssertionError("duplicate-carrier-invoke")) as invoked, redirect_stdout(io.StringIO()):
                    self.assertEqual(runner._advance(state, self.checkpoint), 0)
                self.assertEqual(invoked.call_count, 0)
                self.assertEqual(state["status"], "needs_input")
                if consumed:
                    self.assertNotIn("controller_decision", state)
                    self.assertEqual(progress.read_record(self.checkpoint)[progress.KEY], shared[progress.KEY])
                else:
                    self.assertEqual(state["controller_decision"], queued)
                for key in ("resume_progression", "answer_pending_delivery", "progression_response"):
                    self.assertNotIn(key, state)
                self.assertEqual(progress.read_record(self.checkpoint)["workflow_requirements"], {"keep": "C-owned"})

    def test_cancellation_dominates_shared_pause_and_late_receipts(self):
        self.begin()
        self.invoke("observe", self.observation())
        cancelled = self.invoke("cancel")
        action = self.state()["action"]
        for operation in ("pause", "cancel", "pause"):
            result = self.invoke(operation)
            self.assertEqual(result["status"], "cancelling")
            self.assertEqual(self.state()["stop_requested"], "cancelling")
            self.assertEqual(self.state()["action"], action)
        stopped = self.invoke("observe", self.observation("stopped", "result"))
        self.assertEqual(stopped["status"], "cancelled")
        for operation in ("pause", "cancel", "resume"):
            result = self.invoke(operation)
            self.assertEqual(result["status"], "cancelled")
            self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("observe", self.observation("idle", "result"))
        self.assertEqual(self.state()["status"], "cancelled")

    def test_runner_pause_waits_for_native_stopped_proof(self):
        self.begin()
        self.invoke("observe", self.observation())
        state = runner._new_state({})
        state["launch"].update(state="completed_turn", turn=1)
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertFalse(self.state()["stopped"])
        self.assertEqual(self.state()["status"], "pausing")
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "pausing")
        self.invoke("observe", self.observation("stopped", "result"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "paused")

    def test_runner_pause_before_any_carrier_can_be_paused(self):
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "paused")

    def test_runner_pause_with_unknown_creation_stays_pausing(self):
        self.begin()
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
            runner._advance(state, self.checkpoint)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "pausing")
        self.assertFalse(self.state()["stopped"])
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        with redirect_stdout(io.StringIO()):
            runner._advance(state, self.checkpoint)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "paused")

    def test_runner_and_shared_cancel_pause_order_keeps_cancellation(self):
        self.begin()
        self.invoke("observe", self.observation())
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "cancel")
            self.invoke("pause")
            runner.request_control(self.checkpoint, "pause")
            runner._advance(state, self.checkpoint)
        self.assertEqual(self.state()["stop_requested"], "cancelling")
        self.assertEqual(progress.read_record(self.checkpoint)["runner_request"]["operation"], "cancel")
        self.invoke("observe", self.observation("stopped", "result"))
        with redirect_stdout(io.StringIO()):
            runner._advance(state, self.checkpoint)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "cancelled")

    def test_runner_pause_acknowledges_prior_shared_cancellation_without_write(self):
        self.begin()
        self.invoke("observe", self.observation())
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        self.invoke("cancel")
        before = self.checkpoint.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            runner.request_control(self.checkpoint, "pause")
        self.assertEqual(json.loads(output.getvalue())["status"], "cancelling")
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.invoke("observe", self.observation("stopped", "result"))
        output = io.StringIO()
        with redirect_stdout(output):
            runner.request_control(self.checkpoint, "pause")
        self.assertEqual(json.loads(output.getvalue())["status"], "cancelled")

    def test_completed_result_arriving_during_pause_is_consumed_on_resume(self):
        payload = self.prepare_design_result("stepwise")
        self.assertEqual(self.invoke("pause")["status"], "pausing")
        observed = self.observation("stopped", "result", {"delivery_id": "design", "status": "completed", "payload": payload})
        self.assertEqual(self.invoke("observe", observed)["status"], "paused")
        self.assertIsNone(self.state()["dispatch"]["delivery"])
        resumed = self.invoke("resume")
        self.assertEqual(resumed["status"], "needs_input", resumed)
        self.assertEqual(resumed["pending"]["kind"], "acceptance")
        self.assertEqual(self.state()["dispatch"]["status"], "received")
        pending = resumed["pending"]
        self.assertEqual(self.invoke("pause")["status"], "paused")
        self.assertEqual(self.invoke("resume")["pending"], pending)

    def accept_current(self, payload, role):
        message = {"delivery_id": "completed-" + str(self.state()["stage"]), "status": "completed", "payload": payload}
        result = self.invoke("observe", self.observation("stopped", "result", message, ref=role))
        self.assertEqual(result["status"], "needs_input", result)
        pending = result["pending"]
        return self.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                                     "answer": "accept", "reference": "controller:accept"})

    def finish_next(self, stage, mode):
        if stage == 2:
            return self.finish_design(mode)
        predecessor = self.state()["accepted"]
        value = self.input_for(stage, predecessor)
        self.context = self.context_for(stage)
        self.begin(mode, value)
        role = "native:dispatcher" if stage == 3 else "native:closure"
        self.invoke("observe", self.observation(ref=role))
        if stage == 3:
            (self.flow / "impl.py").write_text("print('ok')\n")
            self.flow_git("add", "impl.py")
            self.flow_git("commit", "-qm", "implementation")
            candidate = self.flow_git("rev-parse", "HEAD")
            payload = {"artifacts": ["impl.py"], "checks": ["behavior tested"], "candidate_commit": candidate,
                "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"} for axis in ("standards", "spec")},
                "verification": {"candidate": candidate, "checks": ["behavior tested"]}}
        else:
            candidate = predecessor["payload"]["candidate_commit"]
            published = self.invoke("publication", {"candidate_commit": candidate,
                "expected_target_head": self.git("rev-parse", "HEAD"), "reference": "controller:closure"})
            self.assertTrue(published["next_action"]["publication"]["result"]["ok"], published)
            payload = {"artifacts": ["impl.py"], "checks": ["publication verified"], "candidate_commit": candidate,
                "merge_commit": self.git("rev-parse", "HEAD"), "changed_paths": ["impl.py"],
                "cleanup": {"worktree_removed": True, "branch_removed": True}}
        return self.accept_current(payload, role)

    def run_real_chain(self, mode):
        confirmed = {**{k: self.binding[k] for k in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": mode, "frozen_requirement": {**self.frozen, "path": self.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        runner._atomic_save(self.checkpoint, state)
        calls = []

        def carrier(state, path, answer, continuing):
            stage = int(state["current_stage"][-1])
            calls.append(stage)
            response = self.finish_next(stage, mode)
            self.assertEqual(response["status"], "accepted", response)
            result = response["completion"]["stage_result"]
            return {**result, "handoff": json.loads(result["handoff_json"])}

        with patch.object(runner, "_invoke", side_effect=carrier), redirect_stdout(io.StringIO()):
            for stage in (2, 3, 4) if mode == "stepwise" else (4,):
                self.assertEqual(runner._advance(state, self.checkpoint), 0)
                if stage < 4:
                    self.assertEqual(state["status"], "needs_input")
                    pending = state["pending_input"]
                    self.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                                          "answer": "confirm", "reference": "user:stage-entry"})
                    state["status"] = "active"
                    state.pop("pending_input")
        self.assertEqual(calls, [2, 3, 4])
        self.assertEqual(state["status"], "completed")
        self.assertEqual(self.state()["history"][0]["publication"]["result"]["state"], "planning_published")
        self.assertEqual(set(state["stage_results"]), {"stage2", "stage3", "stage4"})
        self.assertFalse(self.flow.exists())

    def test_real_runner_continuous_three_stage_chain(self):
        self.run_real_chain("continuous")

    def test_real_runner_stepwise_same_three_stage_chain(self):
        self.run_real_chain("stepwise")

    def test_runner_delivers_controller_acceptance_back_to_original_carrier(self):
        self.settings["thread_id"] = "carrier"
        self.request["host"].update(thread_id="carrier", role="scripted-carrier", source_ref="task")
        self.input = self.input_for(2)
        payload = self.prepare_design_result("stepwise")
        response = self.invoke("observe", self.observation("stopped", "result",
            {"delivery_id": "design", "status": "completed", "payload": payload}))
        pending = response["pending"]
        confirmed = {**{key: self.binding[key] for key in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": "stepwise", "frozen_requirement": {**self.frozen, "path": self.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        state.update(status="needs_input", pending_input=pending, sessions={"stage2": "carrier"},
                     launch={"stage": "stage2", "state": "completed_turn", "turn": 1})
        runner._atomic_save(self.checkpoint, state)
        self.settings["thread_id"] = "task"

        def resume_carrier(state, record, answer, continuing):
            self.assertTrue(continuing)
            self.assertEqual(state["sessions"]["stage2"], "carrier")
            self.settings["thread_id"] = "carrier"
            try:
                accepted = self.invoke("decide", state["controller_decision"])
                self.assertEqual(accepted["status"], "accepted", accepted)
                result = accepted["completion"]["stage_result"]
                return {**result, "handoff": json.loads(result["handoff_json"])}
            finally:
                self.settings["thread_id"] = "task"

        with patch.object(runner, "validate_confirmed", side_effect=lambda value, **kwargs: value), \
             patch.object(runner, "_check_current_registry"), \
             patch.object(runner, "_invoke", side_effect=resume_carrier) as invoked, redirect_stdout(io.StringIO()):
            self.assertEqual(runner.resume(self.checkpoint, "accept", decision_id=pending["decision_id"]), 0)
        self.assertEqual(invoked.call_count, 1)
        outer = progress.read_record(self.checkpoint)
        self.assertEqual(outer["current_stage"], "stage3")
        self.assertEqual(outer["pending_input"]["kind"], "stage-entry")
        self.assertNotIn("controller_decision", outer)

    def test_raw_footer_without_saved_acceptance_cannot_advance(self):
        result = self.finish_design("continuous")["completion"]["stage_result"]
        value = {**result, "handoff": json.loads(result["handoff_json"])}
        outer = progress.read_record(self.checkpoint)
        outer[progress.KEY]["status"] = "active"
        progress.atomic_save(self.checkpoint, outer)
        with self.assertRaises(runner.WorkflowError):
            runner._accepted_stage(self.checkpoint, "stage2", value, {"controller_ref": "task", "frozen_requirement": self.frozen})

    def test_receive_accept_crash_replays_same_transaction(self):
        self.begin()
        observed = self.observation()
        with patch.object(progress.Progress, "apply", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke("observe", observed)
        self.assertIsNotNone(self.state()["transaction"])
        result = self.invoke("resume")
        self.assertEqual(result["next_action"]["operation"], "wait-host")
        self.assertEqual(self.state()["dispatch"]["status"], "bound")

    def test_resume_recovers_original_stopped_writer_control_transaction(self):
        self.finish_design("continuous")
        self.context = self.context_for(3)
        self.begin("continuous", self.input_for(3, self.state()["accepted"]))
        self.invoke("observe", self.observation(ref="native:dispatcher"))
        request = {"action": "recover-dispatch", "evidence": {"stopped_refs": ["native:dispatcher"],
            "file_hashes": {}, "replacement_ref": "native:replacement"},
            "receipt": {"host": "fixture", "stopped": "native:dispatcher", "ready": "native:replacement"}}
        with patch.object(progress.Progress, "next_envelope", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke("control", request)
        self.assertEqual(self.state()["control"]["context"]["carrier"]["ref"], "native:dispatcher")
        recovered = self.invoke("resume")
        self.assertEqual(recovered["next_action"]["operation"], "control-effects")
        self.assertEqual(self.state()["control"]["context"]["carrier"]["ref"], "native:replacement")
        self.assertTrue(self.invoke("control", request)["acknowledged"])

    def begin_dispatcher(self):
        self.context = self.context_for(3)
        self.begin("continuous", self.input_for(3))
        self.invoke("observe", self.observation(ref="native:dispatcher"))

    def execution_input(self):
        self.settings["thread_id"] = "dispatcher-runtime"
        self.request["host"].update(thread_id="dispatcher-runtime", role="implementation-dispatcher",
                                    source_ref="task", actor_ref="native:dispatcher")
        execution = self.input_for(3)
        execution.update(role="execution-agent", configuration=transfer.configuration("execution-agent"))
        execution["authorization"]["flow_mode"] = "continuous"
        return execution

    def prepare_execution(self):
        self.begin_dispatcher()
        execution = self.execution_input()
        prepared = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "prepare", "handoff": execution})
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
        return execution

    def test_suspend_blocks_new_allocations_in_all_stop_states(self):
        for operation, stopped in (("pause", False), ("pause", True), ("cancel", False), ("cancel", True)):
            with self.subTest(operation=operation, stopped=stopped):
                if self.checkpoint.exists(): self.checkpoint.unlink()
                self.settings["thread_id"] = "task"
                self.request["host"].update(thread_id="task", role="controller", source_ref=None)
                self.request["host"].pop("actor_ref", None)
                self.begin_dispatcher()
                self.invoke(operation)
                if stopped:
                    self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher"))
                before = self.state()
                execution = self.execution_input()
                for identity in ("first-new", "different-new"):
                    with self.assertRaises(transfer.entry.PreparationError) as error:
                        self.invoke("allocation", {"allocation_id": identity, "operation": "prepare", "handoff": execution})
                    self.assertEqual(error.exception.code, "progression_suspended")
                    self.assertEqual(self.state(), before)

    def test_suspend_blocks_prepared_but_unissued_allocation_and_allows_lookup(self):
        for operation in ("pause", "cancel"):
            with self.subTest(operation=operation):
                if self.checkpoint.exists(): self.checkpoint.unlink()
                self.settings["thread_id"] = "task"
                self.request["host"].update(thread_id="task", role="controller", source_ref=None)
                self.request["host"].pop("actor_ref", None)
                self.begin_dispatcher()
                execution = self.execution_input()
                request = {"allocation_id": "prepared", "operation": "prepare", "handoff": execution}
                with patch.object(progress.Progress, "issue_allocation", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt): self.invoke("allocation", request)
                self.invoke(operation)
                before = self.state()
                with self.assertRaises(transfer.entry.PreparationError): self.invoke("allocation", request)
                self.assertEqual(self.state(), before)
                owner = progress.Progress(self.checkpoint, progress.read_record(self.checkpoint))
                with self.assertRaises(transfer.entry.PreparationError):
                    owner.issue_allocation("prepared", owner.state["allocations"]["prepared"])
                self.assertEqual(self.state(), before)
                slot = self.state()["allocations"]["prepared"]
                result = self.invoke("allocation", {"allocation_id": "prepared", "operation": "reconcile",
                    "receipt": self.receipt(slot["record"], "not-created", "lookup", ref=None)})
                self.assertEqual(result["next_action"]["result"]["status"], "not-created")

    def test_saved_runner_stop_request_blocks_allocation_before_core_suspend(self):
        self.begin_dispatcher()
        execution = self.execution_input()
        for operation in ("pause", "cancel"):
            outer = progress.read_record(self.checkpoint)
            outer["runner_request"] = {"operation": operation, "request_id": operation}
            progress.atomic_save(self.checkpoint, outer)
            before = self.checkpoint.read_bytes()
            with self.assertRaises(transfer.entry.PreparationError) as error:
                self.invoke("allocation", {"allocation_id": operation, "operation": "prepare", "handoff": execution})
            self.assertEqual(error.exception.code, "progression_suspended")
            self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_pausing_still_receives_and_accepts_existing_allocation(self):
        self.prepare_execution()
        slot = self.state()["allocations"]["slice-one"]
        self.invoke("allocation", {"allocation_id": "slice-one", "operation": "bind",
            "receipt": self.receipt(slot["record"], ref="native:executor")})
        self.invoke("pause")
        data = b"print('finished before stopping')\n"
        (self.flow / "impl.py").write_bytes(data)
        slot = self.state()["allocations"]["slice-one"]
        received = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "receive",
            "receipt": self.receipt(slot["record"], "stopped", "result", ref="native:executor"),
            "result": {"delivery_id": "stopped-slice", "status": "completed", "payload": {"changed_paths": ["impl.py"],
                "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["fixture boundary passed"]}}})
        self.assertEqual(received["next_action"]["result"]["status"], "received")
        accepted = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "accept", "decision": {"reference": "dispatcher:accept-stopped"}})
        self.assertEqual(accepted["status"], "pausing")
        self.assertEqual(accepted["next_action"]["result"]["status"], "accepted")
        self.assertIsNone(self.state()["accepted"])

    def test_native_allocations_share_parent_authority_and_accept_real_delta(self):
        execution = self.prepare_execution()
        slot = self.state()["allocations"]["slice-one"]
        repeated = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "prepare", "handoff": execution})
        self.assertEqual(repeated["next_action"]["operation"], "lookup-exact-allocation")
        self.assertEqual(len(self.state()["control"]["context"]["handoff_progress"]["executions"]), 1)
        self.invoke("allocation", {"allocation_id": "slice-one", "operation": "bind",
            "receipt": self.receipt(slot["record"], ref="native:executor")})
        data = b"print('slice')\n"
        (self.flow / "impl.py").write_bytes(data)
        slot = self.state()["allocations"]["slice-one"]
        received = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "receive",
            "receipt": self.receipt(slot["record"], "stopped", "result", ref="native:executor"),
            "result": {"delivery_id": "slice", "status": "completed", "payload": {"changed_paths": ["impl.py"],
                "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["fixture boundary passed"]}}})
        self.assertEqual(received["next_action"]["result"]["status"], "received", received)
        accepted = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "accept", "decision": {"reference": "dispatcher:accept"}})
        self.assertFalse(accepted["downstream_ready"])
        self.assertIsNone(self.state()["accepted"])
        self.assertEqual(self.state()["dispatch"]["request"]["role"], "implementation-dispatcher")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["executions"][0]["state"], "accepted")

    def test_cancelled_parent_reconciles_exact_unbound_allocation(self):
        self.prepare_execution()
        self.invoke("cancel")
        self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher"))
        slot = self.state()["allocations"]["slice-one"]
        data = {"allocation_id": "slice-one", "operation": "reconcile",
                "receipt": self.receipt(slot["record"], "not-created", "lookup", ref=None)}
        released = self.invoke("allocation", data)
        self.assertEqual(released["status"], "cancelled", released)
        self.assertTrue(self.invoke("allocation", data)["acknowledged"])
        late = {"allocation_id": "slice-one", "operation": "reconcile",
                "receipt": self.receipt(slot["record"], "ready", "lookup", ref="late:executor")}
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("allocation", late)
        self.assertEqual(self.state()["status"], "cancelled")

    def test_stale_revision_and_answer_do_not_launch(self):
        self.begin()
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("advance", revision=0)
        calls = self.state()["action"]
        result = self.invoke("decide", {"decision_id": "old", "subject": {}, "answer": "accept", "reference": "old"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.state()["action"], calls)

    def test_requirement_write_freeze_partial_commit_and_exact_recovery(self):
        request = copy.deepcopy(self.request)
        request.update(stage=1, source={"kind": "stage1", "path": "docs/requirements/c.md"},
                       target={"kind": "planning", "repository": str(self.flow), "branch": self.binding["branch"]})
        write = {"protocol": transfer.requirement.PROTOCOL, "operation": "prepare", "entry": request,
            "purpose": "write", "path": "docs/requirements/c.md", "version": 1, "authorization": "user:write", "content": "new requirement\n"}
        response = self.invoke("prepare-requirement", {"request": write})
        self.assertEqual(response["status"], "requirement-ready", response)
        modified = (self.flow / write["path"]).stat().st_mtime_ns
        repeated = self.invoke("prepare-requirement", {"request": write})["result"]
        self.assertEqual({k: repeated[k] for k in ("path", "sha256", "version")},
                         {k: response["result"][k] for k in ("path", "sha256", "version")})
        self.assertEqual((self.flow / write["path"]).stat().st_mtime_ns, modified)
        freeze = {k: v for k, v in write.items() if k != "content"}
        freeze.update(purpose="freeze", authorization="user:freeze", previous=response["result"])
        hook = self.root / ".git/hooks/post-commit"
        hook.write_text("#!/bin/sh\nprintf drift > docs/requirements/c.md\n")
        hook.chmod(0o755)
        before = int(self.flow_git("rev-list", "--count", "HEAD"))
        response = self.invoke("prepare-requirement", {"request": freeze})
        self.assertEqual(response["status"], "blocked", response)
        self.assertFalse(response["downstream_ready"])
        self.assertEqual(len(response["error"]["completed_evidence"]), 1)
        committed = self.flow_git("rev-parse", "HEAD")
        self.assertEqual(self.invoke("prepare-requirement", {"request": freeze})["status"], "blocked")
        (self.flow / "docs/requirements/c.md").write_text("new requirement\n")
        recovered = self.invoke("prepare-requirement", {"request": freeze})
        self.assertEqual(recovered["result"]["commit"], committed)
        self.assertEqual(int(self.flow_git("rev-list", "--count", "HEAD")), before + 1)

    def test_published_merge_cleanup_failure_never_republishes(self):
        self.finish_design("continuous")
        self.finish_next(3, "continuous")
        predecessor = self.state()["accepted"]
        self.context = self.context_for(4)
        self.begin("continuous", self.input_for(4, predecessor))
        self.invoke("observe", self.observation(ref="native:closure"))
        candidate = predecessor["payload"]["candidate_commit"]
        request = {"candidate_commit": candidate, "expected_target_head": self.git("rev-parse", "HEAD"), "reference": "controller:close"}
        original = transfer.supervision._run_git

        def fail_cleanup(repository, arguments, **kwargs):
            if arguments[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(arguments, 1, "", "fixture cleanup failure")
            return original(repository, arguments, **kwargs)

        with patch.object(transfer.supervision, "_run_git", side_effect=fail_cleanup):
            response = self.invoke("publication", request)
        self.assertEqual(response["status"], "blocked")
        merged = self.git("rev-parse", "HEAD")
        self.assertNotEqual(merged, request["expected_target_head"])
        with patch.object(transfer.supervision, "complete_worktree", side_effect=AssertionError("must not republish")):
            self.assertEqual(self.invoke("publication", request)["next_action"]["operation"], "reconcile-publication")
        receipt = self.observation("stopped", "result", ref="native:closure")
        receipt["closure"] = {"ref": "native:closure", "candidate": candidate, "merge": merged, "ancestor_verified": True,
            "changed_paths": ["impl.py"], "binding": self.binding, "checks": ["published"],
            "worktree_removed": False, "branch_removed": False, "implementation_problem": None}
        self.invoke("observe", receipt)
        self.assertEqual(self.state()["closure_effects"][0]["operation"], "cleanup-only")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["merge"], merged)
        self.assertEqual(self.git("rev-parse", "HEAD"), merged)
        late_problem = self.observation("stopped", "result", ref="native:closure")
        late_problem["closure"] = {**receipt["closure"], "implementation_problem": "needs implementation changes"}
        self.assertEqual(self.invoke("observe", late_problem)["error"]["code"], "already_published")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["merge"], merged)

    def test_prepublication_implementation_problem_returns_exact_original_role(self):
        self.finish_design("continuous")
        self.finish_next(3, "continuous")
        predecessor = self.state()["accepted"]
        self.context = self.context_for(4)
        self.begin("continuous", self.input_for(4, predecessor))
        self.invoke("observe", self.observation(ref="native:closure"))
        observed = self.observation("stopped", "result", ref="native:closure")
        observed["closure"] = {"ref": "native:closure", "candidate": predecessor["payload"]["candidate_commit"],
            "merge": None, "ancestor_verified": False, "changed_paths": [], "binding": self.binding,
            "checks": [], "worktree_removed": False, "branch_removed": False,
            "implementation_problem": "accepted behavior needs controller recovery"}
        result = self.invoke("observe", observed)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["next_action"]["operation"], "implementation-recovery")
        self.assertEqual(result["next_action"]["ref"], "native:dispatcher")
        self.assertEqual(self.invoke("resume")["next_action"], result["next_action"])

    def test_cancel_unknown_then_not_created_cannot_accept_late_ready(self):
        self.begin()
        self.assertEqual(self.invoke("cancel")["status"], "cancelling")
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        self.assertEqual(self.state()["status"], "cancelled")
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("observe", self.observation("ready", "lookup"))
        self.assertEqual(self.state()["status"], "cancelled")

    def test_accepted_source_drift_blocks_successor_without_repeating_acceptance(self):
        self.finish_design("continuous")
        saved = self.state()["accepted"]
        (self.flow / "docs/spec.md").write_text("external drift\n")
        self.context = self.context_for(3)
        value = self.input_for(3, saved)
        value["authorization"]["flow_mode"] = "continuous"
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("start", {"handoff": value, "control": {"context": self.context, "discussion": None}})
        self.assertEqual(self.state()["accepted"], saved)

    def test_completed_turn_intake_after_crash_does_not_reinvoke_carrier(self):
        response = self.finish_design("stepwise")
        value = response["completion"]["stage_result"]
        value = {**value, "handoff": json.loads(value["handoff_json"])}
        confirmed = {**{k: self.binding[k] for k in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": "stepwise", "frozen_requirement": {**self.frozen, "path": self.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        state["turn_result"] = value
        with patch.object(runner, "_invoke", side_effect=AssertionError("completed turn must not run again")), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertEqual(state["current_stage"], "stage3")
        self.assertEqual(state["status"], "needs_input")

    def test_legacy_checkpoint_is_never_mutated(self):
        progress.atomic_save(self.checkpoint, {"version": 2, "sentinel": "old runtime"})
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("inspect")
        self.assertEqual(self.checkpoint.read_bytes(), before)


class AttachedProgressTests(transfer.AttachedTransferTests):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkpoint = Path(temporary.name) / "checkpoint.json"

    def invoke_progress(self, operation, data=None):
        current = progress.read_record(self.checkpoint).get(progress.KEY, {})
        return progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL, "operation": operation,
            "expected_revision": current.get("revision", 0), "data": data or {}})

    def test_real_ledger_prepare_bind_new_envelopes_and_lost_bind(self):
        started = self.invoke_progress("start", {"handoff": self.input, "control": self.port()})
        self.assertEqual(started["next_action"]["operation"], "invoke-host")
        saved = progress.read_record(self.checkpoint)[progress.KEY]
        record = saved["dispatch"]
        receipt = {"adapter": "fixture-task-host", "receipt_ref": "tool:create", "request_digest": record["request"]["digest"],
            "attempt": record["request"]["attempt"], "role": "dedicated-discussion", "event": "create", "status": "ready",
            "ref": "task:dedicated", "pending_id": None, "configuration": {"model": "fixture-model", "effort": "high"},
            "raw": {"threadId": "task:dedicated"}}
        observation = {"event_id": "created", "receipt": receipt}
        with patch.object(progress.Progress, "apply", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke_progress("observe", observation)
        self.assertEqual(self.read()["workflow_control"]["carrier"]["ref"], "task:dedicated")
        before = self.ledger.read_bytes()
        resumed = self.invoke_progress("resume")
        self.assertEqual(resumed["next_action"]["operation"], "wait-host", resumed)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertTrue(self.invoke_progress("observe", observation)["acknowledged"])
        if self.NON_GIT:
            self.assertIsNone(saved["handoff"]["source_commit"])


class NonGitProgressTests(AttachedProgressTests):
    NON_GIT = True


class PhaseProgressTests(DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def test_source_activation_uses_exact_real_phase_and_idempotent_envelope(self):
        project = self.make_project("source-activation", git=False)
        topic = self.bootstrap_topic(project)
        prepared = self.prepare_phase_run(topic, to_phase=2, carrier_kind="current-topic")
        for revision, operation, fields in (
            (2, "authorize-phase-carrier", {"carrier_ref": "discussion-task"}),
            (3, "phase-ready", {"carrier_ref": "discussion-task", "evidence": prepared["evidence"]}),
        ):
            code, result, stderr = self.run_cli(self.phase_request(topic, operation, revision,
                phase_run_id=prepared["phase_run_id"], attempt_id=prepared["attempt_id"], **fields))
            self.assertEqual(code, 0, (result, stderr))
        attachment = {"project_id": topic["project_id"], "tree_id": topic["tree_id"],
                      "actor_topic_id": topic["topic_id"], "actor_conversation_ref": "discussion-task"}
        # A port-level test of C's source adapter. Phase and ledger are real;
        # full stage acceptance is exercised separately through A/B/Git above.
        state = {"protocol": progress.PROTOCOL, "revision": 0, "mode": "stepwise", "stage": 2,
            "status": "active", "step": "bound", "packages": {}, "accepted": None, "phase_complete": False,
            "action": None, "handoff": {"stage": 2, "binding": None,
                "authorization": {"phase": {"run_id": prepared["phase_run_id"], "attempt_id": prepared["attempt_id"]}},
                "entry": {"source": {"kind": "discussion", "attachment": attachment}},
                "expected_entry": {"repository": {"root": str(project)}}}}
        checkpoint = self.root / "source-record.json"
        owner = progress.Progress(checkpoint, {progress.KEY: state})
        emitted = owner.phase_action()
        self.assertEqual(emitted["next_action"]["operation"], "source-lifecycle")
        request = emitted["next_action"]["payload"]["request"]
        self.assertEqual(request["operation"], "phase-activate")
        self.assertEqual(request["evidence"], prepared["evidence"])
        outer = progress.read_record(checkpoint)
        observed = progress.lifecycle(checkpoint, outer, {"request": request})
        self.assertEqual(observed["result"]["result"]["state"], "active")
        ledger = Path(topic["ledger_path"])
        before = ledger.read_bytes()
        progress.lifecycle(checkpoint, progress.read_record(checkpoint), {"request": request})
        self.assertEqual(ledger.read_bytes(), before)
        self.assertIsNone(progress.Progress(checkpoint, progress.read_record(checkpoint)).phase_action())


def load_tests(loader, tests, pattern):
    # B's tests run in their own module; inherit only its real fixture helpers.
    suite = unittest.TestSuite(ProgressTests(name) for name in loader.getTestCaseNames(ProgressTests) if name in ProgressTests.__dict__)
    for cls in (AttachedProgressTests, NonGitProgressTests):
        suite.addTests(cls(name) for name in loader.getTestCaseNames(cls) if name in AttachedProgressTests.__dict__)
    suite.addTests(loader.loadTestsFromTestCase(PhaseProgressTests))
    return suite
