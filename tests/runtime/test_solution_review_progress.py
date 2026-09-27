"""Combined design review uses live C decisions, then consumes entry authority."""
import copy
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures


class CombinedSolutionReviewTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.ProgressTests(methodName="runTest")
        self.addCleanup(self.flow.doCleanups)
        self.flow.setUp()

    def test_revision_then_combined_acceptance_enters_implementation_once(self):
        f = self.flow
        f.begin("stepwise")
        f.invoke("observe", f.observation())
        identity = copy.deepcopy(f.state()["dispatch"]["request"])
        reference = "user:accept-complete-solution-and-enter-stage3"
        for number, answer in enumerate(("split the second Ticket", "accept and enter Stage 3")):
            (f.flow / "docs/spec.md").write_text(f"complete design with Tickets revision {number}\n")
            review = f.invoke("observe", f.observation("idle", "result", {
                "delivery_id": f"combined-review-{number}", "status": "needs_input",
                "payload": {"question": "Accept complete Spec and Tickets, publish and enter Stage 3?"}}))["pending"]
            self.assertNotIn("publication", f.state())
            self.assertIsNone(f.state()["accepted"])
            result = f.invoke("decide", {"decision_id": review["decision_id"], "subject": review["subject"],
                "answer": answer, "reference": reference if number else "user:revise-ticket"})
            self.assertEqual(result["next_action"]["operation"], "continue-host")
            self.assertEqual(f.state()["dispatch"]["request"], identity)

        f.flow_git("add", "docs/spec.md")
        f.flow_git("commit", "-qm", "reviewed complete planning")
        candidate = f.flow_git("rev-parse", "HEAD")
        f.ready_publication(candidate, reference)
        f.invoke("publication", {"candidate_commit": candidate, "reference": reference})
        pending = f.invoke("receive-publication")["pending"]
        result = f.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
            "answer": "accept", "reference": "controller:verified-publication"})
        boundary = result["pending"]
        accepted = copy.deepcopy(f.state()["accepted"])
        self.assertEqual(fixtures.runner._pending_stage_entry("stepwise", 2, accepted, f.state()), boundary)
        # This is bookkeeping of the original combined answer, not a second user reply.
        decision = {"decision_id": boundary["decision_id"], "subject": boundary["subject"],
                    "answer": "confirm", "reference": reference}
        f.invoke("decide", decision)
        self.assertTrue(f.invoke("decide", decision, revision=0)["acknowledged"])
        self.assertIsNone(fixtures.runner._pending_stage_entry("stepwise", 2, accepted, f.state()))
        self.assertEqual(f.state()["mode"], "stepwise")
        for changed in ("subject", "reference", "answer"):
            invalid = copy.deepcopy(f.state())
            record = invalid["decisions"][boundary["decision_id"]]
            record[changed] = {} if changed == "subject" else "" if changed == "reference" else "revise"
            self.assertEqual(fixtures.runner._pending_stage_entry("stepwise", 2, accepted, invalid)["subject"], boundary["subject"])
        # A later boundary retains its own decision; Stage-3 authorization is not Stage-4 authorization.
        later = {**accepted, "digest": "different-stage-result"}
        self.assertIsNotNone(fixtures.runner._pending_stage_entry("stepwise", 3, later, f.state()))
        f.context = f.context_for(3)
        entered = f.begin("stepwise", f.input_for(3, accepted))
        self.assertEqual(entered["next_action"]["operation"], "invoke-host")
        self.assertEqual(f.state()["stage"], 3)

    def complete_with_intent(self, initial_mode, intent):
        f = self.flow
        f.begin(initial_mode)
        f.invoke("observe", f.observation())
        pending = f.invoke("observe", f.observation("idle", "result", {
            "delivery_id": "scope-choice", "status": "needs_input", "payload": {"question": "Current requested scope"}}))["pending"]
        decision = {"decision_id": pending["decision_id"], "subject": pending["subject"],
                    "answer": "apply requested scope", "reference": "user:scope", "flow_intent": intent}
        f.invoke("decide", decision)
        self.assertEqual(f.state()["mode"], "continuous" if intent == "continuous" else "stepwise")
        self.assertTrue(f.invoke("decide", decision, revision=0)["acknowledged"])
        (f.flow / "docs/spec.md").write_text("complete reviewed design\n")
        f.flow_git("add", "docs/spec.md")
        f.flow_git("commit", "-qm", "complete design")
        candidate = f.flow_git("rev-parse", "HEAD")
        f.ready_publication(candidate, "controller:readiness")
        f.invoke("publication", {"candidate_commit": candidate, "reference": "controller:readiness"})
        pending = f.invoke("receive-publication")["pending"]
        return f.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
            "answer": "accept", "reference": "controller:acceptance"})

    def run_foreground_scope(self, mode, intent):
        f = self.flow
        runner = fixtures.runner
        confirmed = {**{k: f.binding[k] for k in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": mode,
            "frozen_requirement": {**f.frozen, "path": f.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        runner._atomic_save(f.checkpoint, state)
        calls = []
        def carrier(state, path, answer, continuing, host=None):
            calls.append(state["current_stage"])
            self.assertEqual(state["current_stage"], "stage2", "unauthorized implementation started")
            response = self.complete_with_intent(mode, intent)
            result = response["completion"]["stage_result"]
            return {**result, "handoff": json.loads(result["handoff_json"])}
        output = io.StringIO()
        with patch.object(runner, "_invoke", side_effect=carrier), redirect_stdout(output):
            self.assertEqual(runner._advance_in_host(state, f.checkpoint, None, None), 0)
        self.assertEqual(calls, ["stage2"])
        self.assertEqual(state["confirmed"]["flow_mode"], "stepwise")
        self.assertTrue(f.flow.exists())
        return state, output.getvalue()

    def test_continuous_design_only_pauses_without_asking_or_starting_implementation(self):
        state, output = self.run_foreground_scope("continuous", "design-only")
        self.assertEqual(state["status"], "paused")
        self.assertIn('"stage_result": "design-completed"', output)
        self.assertNotIn('"status": "needs_input"', output)
        self.assertEqual(state["pending_input"]["subject"]["stage"], 3)
        f = self.flow
        pending = state["pending_input"]
        f.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                           "answer": "confirm", "reference": "user:later-explicit-implementation"})
        self.assertNotIn("flow_intent", f.state())

    def test_stepwise_design_only_pauses_without_asking_or_starting_implementation(self):
        state, output = self.run_foreground_scope("stepwise", "design-only")
        self.assertEqual(state["status"], "paused")
        self.assertNotIn('"status": "needs_input"', output)

    def test_switch_from_continuous_to_stepwise_retains_unapproved_stage_boundary(self):
        state, _ = self.run_foreground_scope("continuous", "stepwise")
        self.assertEqual(state["status"], "needs_input")
        self.assertEqual(state["pending_input"]["subject"]["stage"], 3)

    def test_switch_to_continuous_has_no_stage_boundary(self):
        result = self.complete_with_intent("stepwise", "continuous")
        self.assertIsNone(result["pending"])
        self.assertEqual(self.flow.state()["mode"], "continuous")

    def test_invalid_or_stale_flow_intent_cannot_change_mode(self):
        f = self.flow
        f.begin("continuous")
        f.invoke("observe", f.observation())
        pending = f.invoke("observe", f.observation("idle", "result", {
            "delivery_id": "scope-choice", "status": "needs_input", "payload": {"question": "Current scope"}}))["pending"]
        for intent, identity in (([], pending["decision_id"]), (None, pending["decision_id"]), ("stepwise", "stale")):
            f.invoke("decide", {"decision_id": identity, "subject": pending["subject"],
                     "answer": "change mode", "reference": "user:scope", "flow_intent": intent})
            self.assertEqual(f.state()["mode"], "continuous")
            self.assertNotIn("flow_intent", f.state())

    def test_queued_scope_intent_is_consumed_exactly_and_conflicting_retry_rejected(self):
        f = self.flow
        runner = fixtures.runner
        confirmed = {**{k: f.binding[k] for k in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": "continuous", "registry_input": str(f.checkpoint),
            "frozen_requirement": {**f.frozen, "path": f.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        runner._atomic_save(f.checkpoint, state)
        f.begin("continuous")
        f.invoke("observe", f.observation())
        pending = f.invoke("observe", f.observation("idle", "result", {
            "delivery_id": "scope-choice", "status": "needs_input", "payload": {"question": "Review scope"}}))["pending"]
        state.update(status="needs_input", pending_input=pending)
        runner._atomic_save(f.checkpoint, state)
        with patch.object(runner, "validate_confirmed", side_effect=lambda value, **kwargs: value), \
             patch.object(runner, "_check_current_registry"), redirect_stdout(io.StringIO()):
            with runner.RunLock(f.checkpoint):
                self.assertEqual(runner.resume(f.checkpoint, "finish design only", decision_id=pending["decision_id"],
                                              flow_intent="design-only"), 0)
                queued = runner.CheckpointState(fixtures.progress.read_record(f.checkpoint))
                decision = queued["controller_decision"]
                self.assertEqual(decision["flow_intent"], "design-only")
                self.assertEqual(f.state()["mode"], "continuous", "queueing cannot consume the decision")
                with self.assertRaisesRegex(runner.WorkflowError, "decision_conflict"):
                    runner.resume(f.checkpoint, "finish design only", decision_id=pending["decision_id"],
                                  flow_intent="continuous")
            f.invoke("decide", decision)
            runner._atomic_save(f.checkpoint, queued)
            consumed = fixtures.progress.read_record(f.checkpoint)
            self.assertNotIn("controller_decision", consumed)
            self.assertEqual(f.state()["flow_intent"], decision)
            with runner.RunLock(f.checkpoint):
                self.assertEqual(runner.resume(f.checkpoint, "finish design only", decision_id=pending["decision_id"],
                                              flow_intent="design-only"), 0)
                with self.assertRaisesRegex(runner.WorkflowError, "decision_conflict"):
                    runner.resume(f.checkpoint, "finish design only", decision_id=pending["decision_id"])

    def test_invalid_stage_scope_is_rejected_before_queue_write(self):
        f = self.flow
        runner = fixtures.runner
        for stage, intent in ((3, "design-only"), (4, "stepwise")):
            pending = {"decision_id": "wrong-scope", "kind": "user-decision", "subject": {"stage": stage}}
            state = {"status": "needs_input", "current_stage": f"stage{stage}",
                     "confirmed": {"registry_input": str(f.checkpoint)}, "pending_input": pending}
            fixtures.progress.atomic_save(f.checkpoint, state)
            before = f.checkpoint.read_bytes()
            with patch.object(runner, "_validate_record", side_effect=lambda value: value), \
                 patch.object(runner, "_check_current_registry"), redirect_stdout(io.StringIO()):
                with runner.RunLock(f.checkpoint):
                    with self.assertRaisesRegex(runner.WorkflowError, "invalid_flow_intent"):
                        runner.resume(f.checkpoint, "scope", decision_id="wrong-scope", flow_intent=intent)
            self.assertEqual(f.checkpoint.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
