"""Real A/Git lifecycle; only settings, registry and host receipts are doubles."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import uuid
import hashlib
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_entry_prepare
import entry_prepare as entry
import requirement_prepare as requirement
import stage_handoff as handoff
import stage_dispatch as dispatch
import supervision_protocol as supervision
import workflow_control as control
import discussion_protocol


def configuration(role):
    return {"role": role, "required_capability": None,
            "supported": [{"model": "fixture-model", "effort": "high", "capability": None,
                           "cost": None, "permission": "same", "visible_identity": "same"}],
            "user": None, "frozen": None, "previous": None, "receipt": "host:configuration",
            "can_override": False, "inherited": {"model": "fixture-model", "effort": "high"}, "upgrade_attempted": False}


class StageTransferTests(test_entry_prepare.EntrySupport):
    def setUp(self):
        super().setUp()
        self.requirement_path = "docs/requirements/a.md"
        write = requirement.handle({"protocol": requirement.PROTOCOL, "operation": "prepare", "entry": self.request,
                    "purpose": "write", "path": self.requirement_path, "version": 1, "authorization": "confirmed:write", "content": "confirmed requirement\n"})
        document = requirement.handle({"protocol": requirement.PROTOCOL, "operation": "write", "entry": self.request, "intent": write})
        freeze = requirement.handle({"protocol": requirement.PROTOCOL, "operation": "prepare", "entry": self.request,
                    "purpose": "freeze", "path": self.requirement_path, "version": 1, "authorization": "confirmed:freeze", "previous": document})
        self.frozen = copy.deepcopy(requirement.handle({"protocol": requirement.PROTOCOL, "operation": "freeze", "entry": self.request, "intent": freeze}))
        self.flow = self.root / "flow"
        # Worktrees cannot be nested here: keep the flow outside the primary root
        # so its directory does not appear as unrelated untracked source state.
        self.flow = self.root.parent / (self.root.name + "-flow")
        self.binding = supervision.start_worktree({"repository": str(self.root), "worktree": str(self.flow),
                                  "branch": "codex/test-flow", "target_branch": "main"})["binding"]
        self.addCleanup(lambda: subprocess.run(["git", "-C", str(self.root), "worktree", "remove", "--force", str(self.flow)], capture_output=True))
        entry.os.getcwd.return_value = str(self.flow)
        self.request.update(stage=2, source={"kind": "frozen", "path": self.requirement_path}, target={"kind": "flow", "binding": self.binding})
        self.request["host"]["project_path"] = str(self.flow)
        self.input = self.input_for(2)
        self.context = self.context_for(2)

    def flow_git(self, *args):
        return subprocess.run(["git", "-C", str(self.flow), *args], capture_output=True, check=True).stdout.decode().strip()

    def input_for(self, stage, predecessor=None):
        self.request["stage"] = stage
        role = handoff.ROLES[stage]
        owned = ["docs/spec.md"] if stage == 2 else ["impl.py"] if stage == 3 else ["CHANGELOG.md"]
        scope = {"baseline": self.flow_git("rev-parse", "HEAD"), "owned_paths": owned,
                 "protected_paths": [self.requirement_path], "implementation_paths": ["impl.py"], "closure_paths": ["CHANGELOG.md"]}
        if stage >= 3 and (self.flow / "docs/spec.md").exists():
            scope["protected_paths"] = sorted([self.requirement_path, "docs/spec.md"])
        if stage == 4 and predecessor is not None:
            scope["baseline"] = predecessor["handoff"]["scope"]["baseline"]
        return {"protocol": handoff.PROTOCOL, "operation": "prepare", "entry": copy.deepcopy(self.request),
                "expected_entry": entry.resolve(self.request), "stage": stage, "role": role, "requirement": self.frozen,
                "predecessor": predecessor, "target": {"repository": str(self.root), "branch": "main"}, "delivery": None,
                "binding": self.binding, "scope": scope,
                "authorization": {"reference": "checkpoint:confirmed", "flow_mode": "stepwise", "scope_digest": entry.digest(scope)},
                "configuration": configuration(role), "semantic": {"objective": "implement the approved behavior", "testing_basis": "real CLI",
                    "completion_criteria": ["verified outputs"], "constraints": ["no remote mutations"]}}

    def context_for(self, stage):
        return {"schema_version": 1, "controller_ref": "task", "topic_ref": None, "stage": stage,
                "carrier": None, "preference": {"topic_current": False, "stage_current": False}, "flow_authority": None,
                "requirement_identity": self.frozen["requirement_identity"], "handoff_progress": None}

    def launch(self, input_data=None):
        saved = handoff.handle(input_data or self.input)
        result = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved,
                                  "control": {"context": self.context, "discussion": None}})
        self.context = result["checkpoint"]["context"]
        return result

    def receipt(self, record, status="ready", event="create", ref="native:designer", pending=None):
        return {"adapter": "test-boundary", "receipt_ref": "host:" + event + ":" + status,
                "request_digest": record["request"]["digest"], "attempt": record["request"]["attempt"],
                "role": record["request"]["role"], "event": event, "status": status, "ref": ref, "pending_id": pending,
                "configuration": {"model": "fixture-model", "effort": "high"} if ref is not None else None,
                "raw": {"test_boundary": True, "status": status, "id": ref, "client_id": pending}}

    def call(self, operation, record, **values):
        result = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": operation, "record": record,
                                  "control": {"context": self.context, "discussion": None}, **values})
        if "checkpoint" in result:
            self.context = result["checkpoint"]["context"]
        return result

    def complete_design(self):
        started = self.launch()
        bound = self.call("bind", started["record"], receipt=self.receipt(started["record"]))
        (self.flow / "docs/spec.md").write_text("approved plan\n")
        self.flow_git("add", "docs/spec.md"); self.flow_git("commit", "-qm", "planning")
        planning = self.flow_git("rev-parse", "HEAD")
        supervision.publish_planning({"binding": self.binding, "planning_commit": planning,
                                      "allowed_paths": ["docs/spec.md"], "protected_paths": [self.requirement_path]})
        payload = {"artifacts": ["docs/spec.md"], "checks": ["design review"], "planning_commit": planning,
                   "planning_merge_commit": self.flow_git("rev-parse", "HEAD"), "planning_paths": ["docs/spec.md"]}
        message = {"delivery_id": "design-1", "status": "completed", "payload": payload}
        received = self.call("receive", bound["record"], receipt=self.receipt(bound["record"], "stopped", "result"), result=message)
        return self.call("accept", received["record"], decision={"reference": "review:accepted", "delivery_digest": received["record"]["delivery"]["digest"]})

    def test_real_a_freeze_and_flow_transfer_preserve_user_changes(self):
        (self.root / "existing.txt").write_text("user staged\n")
        self.git("add", "existing.txt")
        (self.root / "existing.txt").write_text("user unstaged\n")
        before = self.git("ls-files", "--stage"), (self.root / "existing.txt").read_bytes()
        saved = handoff.handle(self.input)
        self.assertEqual(saved["source_commit"], self.frozen["commit"])
        self.assertEqual(handoff.verify(saved), saved)
        self.assertEqual(before, (self.git("ls-files", "--stage"), (self.root / "existing.txt").read_bytes()))
        self.assertIn('"input_digest"', handoff.render(saved)["text"])

    def test_source_drift_partial_evidence_and_protected_scope_block(self):
        partial = copy.deepcopy(self.input)
        partial["requirement"] = {"kind": "verified-requirement-commit", "downstream_ready": False}
        self.assert_code("invalid_source", lambda: handoff.handle(partial))
        invalid = copy.deepcopy(self.input)
        invalid["scope"]["owned_paths"] = [self.requirement_path]
        self.assert_code("invalid_scope", lambda: handoff.handle(invalid))
        saved = handoff.handle(self.input)
        (self.flow / self.requirement_path).write_text("drift\n")
        self.assert_code("entry_changed", lambda: handoff.verify(saved))

    def test_unknown_pending_and_late_receipts_never_relaunch(self):
        launched = self.launch()
        record = launched["record"]
        pending = self.call("bind", record, receipt=self.receipt(record, "pending", ref=None, pending="client-1"))
        self.assertIsNone(self.context["carrier"]["ref"])
        self.assertFalse(pending["downstream_ready"])
        self.assert_code("outcome_unknown", lambda: self.call("bind", pending["record"], receipt=self.receipt(record)))
        ready = self.call("reconcile", pending["record"], receipt=self.receipt(record, event="lookup"))
        self.assertEqual(ready["status"], "bound")
        wrong = self.receipt(record, event="lookup", ref="foreign")
        self.assert_code("identity_mismatch", lambda: self.call("reconcile", ready["record"], receipt=wrong))

    def test_explicit_not_created_releases_only_original_attempt(self):
        launched = self.launch()
        failed = self.call("bind", launched["record"], receipt=self.receipt(launched["record"], "not-created", ref=None))
        self.assertEqual(self.context["handoff_progress"]["state"], "cancelled")
        self.assert_code("attempt_mismatch", lambda: self.call("reconcile", failed["record"], receipt=self.receipt(failed["record"], event="lookup")))

    def test_wrong_attempt_and_client_id_cannot_bind(self):
        launched = self.launch()
        receipt = self.receipt(launched["record"])
        receipt["attempt"] = "wrong"
        self.assert_code("identity_mismatch", lambda: self.call("bind", launched["record"], receipt=receipt))
        receipt = self.receipt(launched["record"])
        receipt["configuration"]["model"] = "unexpected"
        self.assert_code("configuration_changed", lambda: self.call("bind", launched["record"], receipt=receipt))
        receipt = self.receipt(launched["record"], pending="client-id")
        self.assert_code("identity_mismatch", lambda: self.call("bind", launched["record"], receipt=receipt))

    def test_idle_and_turn_complete_are_not_business_completion(self):
        launched = self.launch()
        bound = self.call("bind", launched["record"], receipt=self.receipt(launched["record"]))
        for status in ("idle", "turn-completed"):
            self.assert_code("host_evidence_missing", lambda: self.call("receive", bound["record"],
                receipt=self.receipt(bound["record"], status, "result"), result={"delivery_id": "d", "status": "completed", "payload": {}}))
        continued = self.call("receive", bound["record"], receipt=self.receipt(bound["record"], "turn-completed", "result"),
                              result={"delivery_id": "d", "status": "continue", "payload": {"message": "remaining work"}})
        self.assertFalse(continued["downstream_ready"])

    def test_target_mode_change_and_non_document_design_scope_are_rejected(self):
        wrong = copy.deepcopy(self.input)
        wrong["scope"]["owned_paths"] = ["code.py"]
        self.assert_code("invalid_scope", lambda: handoff.handle(wrong))
        self.git("update-index", "--chmod=+x", self.requirement_path)
        self.git("commit", "-qm", "mode drift on target")
        self.assert_code("delivery_pending", lambda: handoff.handle(self.input))

    def test_real_design_publication_receive_accept_and_duplicate(self):
        accepted = self.complete_design()
        self.assertTrue(accepted["downstream_ready"])
        rendered = handoff.render(accepted["accepted"])
        self.assertEqual(json.loads(rendered["stage_result"]["handoff_json"])["role_ref"], "native:designer")
        self.assertEqual(rendered["payload"]["allowed_paths"], ["impl.py"])
        record = accepted["record"]
        ack = self.call("receive", record, receipt=self.receipt(record, "stopped", "result"), result=record["delivery"]["message"])
        self.assertTrue(ack["acknowledged"])
        message = copy.deepcopy(record["delivery"]["message"])
        message["payload"]["checks"] = ["different"]
        self.assert_code("delivery_conflict", lambda: self.call("receive", record, receipt=self.receipt(record, "stopped", "result"), result=message))
        self.assertTrue(self.call("accept", record, decision=accepted["accepted"]["decision"])["acknowledged"])
        self.assert_code("delivery_conflict", lambda: self.call("accept", record, decision={**accepted["accepted"]["decision"], "reference": "other"}))

    def test_accepted_design_consumed_by_stage3_and_actual_candidate_review(self):
        accepted = self.complete_design()["accepted"]
        next_input = self.input_for(3, accepted)
        self.context = self.context_for(3)
        launched = self.launch(next_input)
        bound = self.call("bind", launched["record"], receipt=self.receipt(launched["record"], ref="native:dispatcher"))
        (self.flow / "impl.py").write_text("print('ok')\n")
        self.flow_git("add", "impl.py"); self.flow_git("commit", "-qm", "implement")
        candidate = self.flow_git("rev-parse", "HEAD")
        payload = {"artifacts": ["impl.py"], "checks": ["CLI passed"], "candidate_commit": candidate,
                   "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"} for axis in ("standards", "spec")},
                   "verification": {"candidate": candidate, "checks": ["CLI passed"]}}
        received = self.call("receive", bound["record"], receipt=self.receipt(bound["record"], "stopped", "result", "native:dispatcher"),
                             result={"delivery_id": "candidate", "status": "completed", "payload": payload})
        done = self.call("accept", received["record"], decision={"reference": "accepted:candidate", "delivery_digest": received["record"]["delivery"]["digest"]})
        self.assertEqual(done["accepted"]["payload"]["candidate_commit"], candidate)
        closure = self.input_for(4, done["accepted"])
        self.context = self.context_for(4)
        launched = self.launch(closure)
        self.assertEqual(launched["host_call"]["role"], "closure-agent")
        bound = self.call("bind", launched["record"], receipt=self.receipt(launched["record"], ref="native:closure"))
        supervision.complete_worktree({"binding": self.binding, "candidate_commit": candidate,
                    "expected_target_head": self.git("rev-parse", "HEAD"), "scope_base_commit": closure["scope"]["baseline"],
                    "allowed_paths": ["impl.py"], "protected_paths": closure["scope"]["protected_paths"]})
        payload = {"artifacts": ["impl.py"], "checks": ["publication verified"], "candidate_commit": candidate,
                   "merge_commit": self.git("rev-parse", "HEAD"), "changed_paths": ["impl.py"],
                   "cleanup": {"worktree_removed": True, "branch_removed": True}}
        received = self.call("receive", bound["record"], receipt=self.receipt(bound["record"], "stopped", "result", "native:closure"),
                             result={"delivery_id": "closure", "status": "completed", "payload": payload})
        accepted = self.call("accept", received["record"], decision={"reference": "closed", "delivery_digest": received["record"]["delivery"]["digest"]})
        self.assertTrue(accepted["downstream_ready"])

    def test_cli_rejects_duplicate_keys_and_hides_host_errors(self):
        for raw in (b'{"protocol":1,"protocol":2}', b'[]'):
            output = io.StringIO()
            with patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw))), redirect_stdout(output):
                self.assertEqual(handoff.cli(handoff.handle), 1)
            self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_distinct_source_and_bounded_delivery_commits_use_real_a(self):
        source_entry = copy.deepcopy(self.request)
        source_entry.update(stage=1, source={"kind": "stage1", "path": "docs/requirements/b.md"},
                            target={"kind": "planning", "repository": str(self.flow), "branch": self.binding["branch"]})
        def req(operation, **kwargs):
            return copy.deepcopy(requirement.handle({"protocol": requirement.PROTOCOL, "operation": operation, "entry": source_entry, **kwargs}))
        intent = req("prepare", purpose="write", path="docs/requirements/b.md", version=1, authorization="write:b", content="new confirmed source\n")
        document = req("write", intent=intent)
        frozen = req("freeze", intent=req("prepare", purpose="freeze", path=document["path"], version=1, authorization="freeze:b", previous=document))
        self.frozen = frozen
        self.requirement_path = document["path"]
        self.request["source"]["path"] = document["path"]
        candidate = self.input_for(2)
        with self.assertRaises(entry.PreparationError):
            handoff.handle(candidate)
        (self.root / document["path"]).write_text("new confirmed source\n")
        self.git("add", document["path"]); self.git("commit", "-qm", "bounded delivery")
        delivered = self.git("rev-parse", "HEAD")
        self.flow_git("merge", "--no-edit", "main")
        candidate = self.input_for(2)
        candidate["delivery"] = {"commit": delivered, "paths": [document["path"]], "receipt": "delivery-owner:result"}
        saved = handoff.handle(candidate)
        self.assertNotEqual(saved["source_commit"], saved["delivery_facts"]["delivery_commit"])
        self.assertEqual(handoff.verify(saved), saved)

    def test_result_drift_between_receive_and_accept_blocks(self):
        completed = self.complete_design()
        # Use the original received record, preserving its exact source rather
        # than inventing a new delivery. A new acceptance must recheck Git.
        record = handoff.unseal(completed["record"])
        record.update(status="received", acceptance=None)
        (self.flow / "docs/spec.md").write_text("unreviewed change\n")
        self.assert_code("dirty_worktree", lambda: self.call("accept", handoff.seal(record), decision=completed["accepted"]["decision"]))

    def test_dedicated_reservation_replay_has_no_host_call(self):
        entry.os.getcwd.return_value = str(self.root)
        self.request.update(stage=1, source={"kind": "stage1", "path": self.requirement_path},
                            target={"kind": "planning", "repository": str(self.root), "branch": "main"})
        self.request["host"]["project_path"] = str(self.root)
        candidate = copy.deepcopy(self.input)
        candidate.update(stage=1, role="dedicated-problem-framing", entry=copy.deepcopy(self.request), expected_entry=entry.resolve(self.request),
                         binding=None, configuration=configuration("dedicated-problem-framing"))
        candidate["scope"]["owned_paths"] = [self.requirement_path]
        candidate["scope"]["protected_paths"] = []
        candidate["authorization"]["scope_digest"] = entry.digest(candidate["scope"])
        ctx = self.context_for(1)
        def transition(action, evidence):
            nonlocal ctx
            result = control.transition({"schema_version": 1, "actor_ref": "task", "context": ctx, "action": action, "evidence": evidence})
            ctx = result["context"]
            return result
        prepared = transition("prepare", {"target": "local", "project": "project", "title": "Discuss", "missing_context": [],
                     "configuration": candidate["configuration"], "next_step": "stage2", "archive_ref": None, "gate_open": True})
        transition("decide", {"plan_id": prepared["plan"]["plan_id"], "intent": "confirm"})
        self.context = ctx
        saved = handoff.handle(candidate)
        first = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved, "control": {"context": ctx, "discussion": None}})
        second = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved,
                                  "control": {"context": first["checkpoint"]["context"], "discussion": None}})
        self.assertNotIn("host_call", second)
        self.assertEqual(second["status"], "unknown")

    def test_execution_allocation_uses_actual_git_delta_and_is_not_stage_completion(self):
        candidate = self.input_for(3)
        self.context = self.context_for(3)
        launch = self.launch(candidate)
        self.call("bind", launch["record"], receipt=self.receipt(launch["record"], ref="native:dispatcher"))
        execution = self.input_for(3)
        execution.update(role="execution-agent", configuration=configuration("execution-agent"))
        launched = self.launch(execution)
        bound = self.call("bind", launched["record"], receipt=self.receipt(launched["record"], ref="native:executor"))
        data = b"print('slice')\n"
        (self.flow / "impl.py").write_bytes(data)
        message = {"delivery_id": "slice", "status": "completed", "payload": {"changed_paths": ["impl.py"],
                    "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["production CLI passed"]}}
        received = self.call("receive", bound["record"], receipt=self.receipt(bound["record"], "stopped", "result", "native:executor"), result=message)
        accepted = self.call("accept", received["record"], decision={"reference": "slice:accepted", "delivery_digest": received["record"]["delivery"]["digest"]})
        self.assertFalse(accepted["downstream_ready"])
        self.assertEqual(self.context["handoff_progress"]["executions"][0]["state"], "accepted")


class AttachedTransferTests(test_entry_prepare.EntrySupport):
    NON_GIT = False

    def setUp(self):
        super().setUp()
        if self.NON_GIT:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name).resolve()
            entry.os.getcwd.return_value = str(self.root)
            self.request["host"]["project_path"] = str(self.root)
            self.request.pop("target")
        initial = discussion_protocol.handle({"protocol_version": 1, "operation": "bootstrap", "project_path": str(self.root),
                    "entry_mode": "explicit-skill", "conversation_ref": "task", "idempotency_key": str(uuid.uuid4()), "root_slug": "topic"})
        self.attachment = {"project_id": initial["project_id"], "tree_id": initial["tree_id"],
                           "actor_topic_id": initial["topic_id"], "actor_conversation_ref": "task"}
        self.request.update(stage=0, source={"kind": "discussion", "attachment": self.attachment})
        self.base = {"protocol_version": 1, "project_path": str(self.root), **self.attachment}
        self.ledger = Path(initial["ledger_path"])
        self.original = self.mutate("prepare-handoff", handoff_kind="dedicated-stage", target_slug="topic",
                                     scope=["requirements"], work_snapshot={"goal": "Discuss"}, authoritative_references=[], stage=0)
        prepared = self.mutate("workflow-control", action="prepare", evidence={"target": "local", "project": initial["project_id"],
                      "title": "Discuss", "missing_context": [], "configuration": configuration("dedicated-discussion"),
                      "next_step": "stage1", "archive_ref": None, "gate_open": True})
        self.mutate("workflow-control", action="decide", evidence={"plan_id": prepared["control"]["plan"]["plan_id"], "intent": "confirm"})
        path = Path(initial["topic_document_path"]).relative_to(self.root).as_posix()
        scope = {"baseline": None if self.NON_GIT else self.git("rev-parse", "HEAD"), "owned_paths": [path], "protected_paths": [],
                 "implementation_paths": [], "closure_paths": []}
        self.input = {"protocol": handoff.PROTOCOL, "operation": "prepare", "entry": copy.deepcopy(self.request),
                "expected_entry": entry.resolve(self.request), "stage": 0, "role": "dedicated-discussion", "requirement": None,
                "predecessor": None, "target": {"repository": str(self.root), "branch": "main"}, "delivery": None,
                "binding": None, "scope": scope, "authorization": {"reference": "confirmed:dedicated", "flow_mode": "stepwise", "scope_digest": entry.digest(scope)},
                "configuration": configuration("dedicated-discussion"), "semantic": {"objective": "Discuss", "testing_basis": "requirements review",
                    "completion_criteria": ["confirmed document"], "constraints": ["no implementation"]}}

    def read(self):
        return discussion_protocol.handle({**self.base, "operation": "read-topic"})

    def envelope(self):
        topic = self.read()
        return {**self.base, "expected_ledger_revision": topic["ledger_revision"], "expected_topic_revision": topic["record_revision"], "idempotency_key": str(uuid.uuid4())}

    def mutate(self, operation, **values):
        return discussion_protocol.handle({**self.envelope(), "operation": operation, **values})

    def port(self):
        return {"context": self.read()["workflow_control"], "discussion": self.envelope()}

    def test_real_ledger_binds_exact_carrier_and_recovers_lost_response(self):
        saved = handoff.handle(self.input)
        launched = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved, "control": self.port()})
        record = launched["record"]
        receipt = {"adapter": "fixture-task-host", "receipt_ref": "tool:create", "request_digest": record["request"]["digest"],
                   "attempt": record["request"]["attempt"], "role": "dedicated-discussion", "event": "create", "status": "ready",
                   "ref": "task:dedicated", "pending_id": None, "configuration": {"model": "fixture-model", "effort": "high"}, "raw": {"threadId": "task:dedicated"}}
        stale_port = self.port()
        bound = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "bind", "record": record, "control": stale_port, "receipt": receipt})
        self.assertEqual(bound["status"], "bound")
        self.assertEqual(self.read()["workflow_control"]["carrier"]["ref"], "task:dedicated")
        before = self.ledger.read_bytes()
        recovered = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "reconcile", "record": record, "control": stale_port,
                                     "receipt": {**receipt, "event": "lookup", "receipt_ref": "tool:lookup"}})
        self.assertTrue(recovered["acknowledged"])
        self.assertEqual(before, self.ledger.read_bytes())

        self.assertEqual(self.read()["current_phase"], 0)

    def test_cancelled_ledger_attempt_cannot_bind_using_stale_context(self):
        saved = handoff.handle(self.input)
        launched = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved, "control": self.port()})
        record = launched["record"]
        stale = self.port()
        self.mutate("workflow-control", action="cancel", evidence={})
        receipt = {"adapter": "fixture-task-host", "receipt_ref": "late", "request_digest": record["request"]["digest"],
                   "attempt": record["request"]["attempt"], "role": "dedicated-discussion", "event": "lookup", "status": "ready",
                   "ref": "late", "pending_id": None, "configuration": {"model": "fixture-model", "effort": "high"}, "raw": {"threadId": "late"}}
        before = self.ledger.read_bytes()
        self.assert_code("attempt_mismatch", lambda: dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "reconcile",
                         "record": record, "control": stale, "receipt": receipt}))
        self.assertEqual(before, self.ledger.read_bytes())


class NonGitAttachedTransferTests(AttachedTransferTests):
    NON_GIT = True

    def test_non_git_snapshot_can_complete_stage0_without_git_claims(self):
        intent = requirement.handle({"protocol": requirement.PROTOCOL, "operation": "prepare", "entry": self.request,
                     "purpose": "freeze", "authorization": "confirmed:snapshot", "base_ref": "HEAD"})
        frozen = requirement.handle({"protocol": requirement.PROTOCOL, "operation": "freeze", "entry": self.request, "intent": intent})
        self.input.update(requirement=frozen, expected_entry=entry.resolve(self.request))
        saved = handoff.handle(self.input)
        launched = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved, "control": self.port()})
        record = launched["record"]
        receipt = {"adapter": "fixture", "receipt_ref": "created", "request_digest": record["request"]["digest"],
                   "attempt": record["request"]["attempt"], "role": "dedicated-discussion", "event": "create", "status": "ready",
                   "ref": "task:dedicated", "pending_id": None, "configuration": {"model": "fixture-model", "effort": "high"}, "raw": {"threadId": "task:dedicated"}}
        bound = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "bind", "record": record, "control": self.port(), "receipt": receipt})
        discussion_protocol.handle({**self.envelope(), "actor_conversation_ref": "task:dedicated", "operation": "accept-handoff",
                    "handoff_id": self.original["handoff_id"], "attempt_id": self.original["attempt_id"],
                    "payload_sha256": self.original["payload_sha256"], "source_reference_sha256": self.original["authoritative_references_sha256"], "turn_number": 1})
        received = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "receive", "record": bound["record"], "control": self.port(),
                    "receipt": {**receipt, "receipt_ref": "stopped", "event": "result", "status": "stopped"},
                    "result": {"delivery_id": "snapshot", "status": "completed", "payload": {"artifacts": ["topic"], "checks": ["requirements confirmed"],
                                "requirement": frozen, "delivery": None}}})
        accepted = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "accept", "record": received["record"], "control": self.port(),
                       "decision": {"reference": "accepted:snapshot", "delivery_digest": received["record"]["delivery"]["digest"]}})
        self.assertTrue(accepted["downstream_ready"])
        self.assertIsNone(accepted["accepted"]["payload"]["requirement"]["commit"])
        self.assertEqual(self.read()["current_phase"], 0)
