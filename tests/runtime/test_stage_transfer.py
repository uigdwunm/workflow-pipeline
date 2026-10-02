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
import execution_plan
import model_inventory
import stage_dispatch as dispatch
import supervision_protocol as supervision
import workflow_control as control
import workflow_control_git as control_git
import workflow_progress as progress
import discussion_protocol
from test_workflow_control import plan_for, checks_for, verification_fixture


def configuration(role):
    return {"role": role, "required_capability": None,
            "supported": [{"model": "fixture-model", "effort": "high", "capability": None,
                           "cost": None, "permission": "same", "visible_identity": "same"}],
            "user": None, "frozen": None, "previous": None, "receipt": "host:configuration",
            "can_override": False, "inherited": {"model": "fixture-model", "effort": "high"}, "upgrade_attempted": False}


class StageTransferTests(test_entry_prepare.EntrySupport):
    def setUp(self):
        super().setUp()
        self.request['host']['supported_configurations'] = [
            {'model': 'fixture-model', 'reasoning_effort': 'high'}]
        catalog_patch = patch.object(model_inventory, 'available_pairs',
                                     side_effect=AssertionError('normal transfer must not query the account catalog'))
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
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
        self.addCleanup(lambda flow=self.flow: subprocess.run(["git", "-C", str(self.root), "worktree", "remove", "--force", str(flow)], capture_output=True))
        entry.os.getcwd.return_value = str(self.flow)
        self.request.update(stage=2, source={"kind": "frozen", "path": self.requirement_path}, target={"kind": "flow", "binding": self.binding})
        self.request["host"]["project_path"] = str(self.flow)
        self.input = self.input_for(2)
        self.context = self.context_for(2)
        progress_directory = tempfile.TemporaryDirectory()
        self.addCleanup(progress_directory.cleanup)
        self.checkpoint = (Path(progress_directory.name) / "checkpoint.json").resolve()
        self.host_response_sequence = 0

    def flow_git(self, *args):
        return subprocess.run(["git", "-C", str(self.flow), *args], capture_output=True, check=True).stdout.decode().strip()

    def fixture_accepted_execution(self, context, candidate):
        # Review and transfer fixtures start from committed code. Allocation
        # mechanics have their own tests; supply their accepted result here.
        progress = context['handoff_progress']
        changed = set(self.flow_git('diff', '--name-only', self.git('rev-parse', 'main'), candidate).splitlines())
        owned = sorted(changed & set(progress['allowed_paths']))
        self.assertTrue(owned)
        hashes = {name: control_git.implementation_hash(self.flow, name) for name in owned}
        fingerprints = {name: control_git.file_fingerprint(self.flow, name) for name in owned}
        index = len(progress['executions'])
        authority = progress['execution_authority']
        refs = [execution_plan.step_ref(ticket, step)
                for ticket in authority['execution_plan']['catalog']['tickets']
                for step in ticket['plan']['steps'] if step['target']['path'] in owned]
        self.assertTrue(refs)
        progress['executions'].append({'task_id': 'fixture-execution-' + str(index),
            'paths': owned, 'read_only': progress['protected_paths'],
            'behavior': 'fixture implementation', 'tests': ['focused fixture check'],
            'git_operations': [], 'configuration': progress['configuration'],
            'agent_ref': 'native:executor-' + str(index), 'state': 'accepted',
            'stopped': True, 'file_hashes': hashes,
            'git_result_snapshot': fingerprints,
            'plan_refs': refs, 'plan_authority': copy.deepcopy(progress['plan_authority']),
            'group_digest': entry.digest([progress['plan_authority'], refs]),
            'allocation_digest': 'fixture-allocation-' + str(index)})

    def input_for(self, stage, predecessor=None):
        self.request["stage"] = stage
        role = handoff.ROLES[stage]
        owned = ["docs/spec.md"] if stage == 2 else ["impl.py"] if stage == 3 else ["CHANGELOG.md"]
        baseline = self.flow_git("rev-parse", "HEAD")
        scope = {"baseline": baseline, "owned_paths": owned,
                 "protected_paths": [self.requirement_path], "implementation_paths": None,
                 "closure_paths": None, "downstream": {"state": "unresolved", "revision": 0,
                     "implementation_required": None, "manifest": None,
                     "proposal_ref": None, "seal_ref": None}}
        if stage == 3 and predecessor is None:
            basis = {"commit": self.frozen["commit"], "path": self.requirement_path,
                     "section": "confirmed requirement"}
            manifest = [{"path": "CHANGELOG.md", "owner": "closure", "kind": "document",
                         "operation": "add", "basis": basis},
                        {"path": "impl.py", "owner": "implementation", "kind": "source",
                         "operation": "add", "basis": basis}]
            scope.update(implementation_paths=["impl.py"], closure_paths=["CHANGELOG.md"],
                downstream={"state": "sealed", "revision": 1,
                    "implementation_required": True, "manifest": manifest,
                    "proposal_ref": "standalone:requirement", "seal_ref": "controller:standalone"})
        elif stage >= 3 and predecessor is not None:
            inherited = predecessor["handoff"]["scope"]
            if stage == 3 and predecessor["handoff"]["stage"] == 2:
                import workflow_progress as progress
                state = progress.read_record(self.checkpoint).get(progress.KEY, {})
                if state.get("stage") == 2 and state.get("accepted") == predecessor:
                    inherited = state["scope_authority"]["scope"]
            scope["downstream"] = copy.deepcopy(inherited["downstream"])
            scope["implementation_paths"] = copy.deepcopy(inherited["implementation_paths"])
            scope["closure_paths"] = copy.deepcopy(inherited["closure_paths"])
            if stage == 3:
                scope["owned_paths"] = copy.deepcopy(scope["implementation_paths"])
            else:
                scope["owned_paths"] = copy.deepcopy(scope["closure_paths"])
        if stage >= 3 and (self.flow / "docs/spec.md").exists():
            scope["protected_paths"] = sorted([self.requirement_path, "docs/spec.md"])
        if stage == 4 and predecessor is not None:
            scope["baseline"] = predecessor["handoff"]["scope"]["baseline"]
            scope["protected_paths"] = sorted(set(scope["protected_paths"]) - set(scope["closure_paths"]))
        request = {"protocol": handoff.PROTOCOL, "operation": "prepare", "entry": copy.deepcopy(self.request),
                "expected_entry": entry.resolve(self.request), "stage": stage, "role": role, "requirement": self.frozen,
                "predecessor": predecessor, "target": {"repository": str(self.root), "branch": "main"}, "delivery": None,
                "binding": self.binding, "scope": scope,
                "authorization": {"reference": "checkpoint:confirmed", "flow_mode": "stepwise", "scope_digest": entry.digest(scope)},
                "configuration": configuration(role), "semantic": {"objective": "implement the approved behavior", "testing_basis": "real CLI",
                    "completion_criteria": ["verified outputs"], "constraints": ["no remote mutations"],
                    **({"validation_plan":plan_for(self.flow)} if stage == 3 else {})}}
        if stage == 3 and predecessor is not None:
            import workflow_progress as progress
            state = progress.read_record(self.checkpoint).get(progress.KEY, {})
            if state.get("stage") == 2 and state.get("accepted") == predecessor:
                request["readiness"] = progress.scope_readiness(state, self.checkpoint)
        if stage == 3 and predecessor is None:
            request["semantic"]["inline_plan"] = {
                "schema_version": 1, "ticket_id": "INLINE-TRANSFER", "plan_version": 1,
                "spec_ref": {"path": "inline", "section": "brief"},
                "contract_refs": ["standalone:objective"], "blocked_by": [],
                "verifications": [{"id": "V-inline", "behavior": "approved implementation behavior",
                    "seam": "real B/C/Git fixture", "commands":
                    ["python3 scripts/run_tests.py --test-file tests/runtime/test_stage_transfer.py"]}],
                "steps": [{"id": "inline-implementation", "target": {"path": "impl.py", "symbol": "run", "operation": "add"},
                    "capability": "implement the approved behavior", "approach": ["implement the fixed brief"],
                    "inputs": ["frozen brief"], "outputs": ["observable result"],
                    "failures": ["invalid input is reported"], "depends_on": [],
                    "verification_refs": ["V-inline"], "read_only": [self.requirement_path]}],
                "artifact_steps": []}
        return request

    def context_for(self, stage):
        return {"schema_version": 5, "controller_ref": "task", "topic_ref": None, "stage": stage,
                "carrier": None, "preference": {"topic_current": False, "stage_current": False}, "flow_authority": None,
                "requirement_identity": self.frozen["requirement_identity"], "handoff_progress": None}

    def inline_execution_input(self):
        execution = self.input_for(3)
        execution["inline_authority"] = handoff.unseal(handoff.handle(execution))["inline_authority"]
        execution.update(role="execution-agent", configuration=configuration("execution-agent"))
        authority = execution["inline_authority"]
        ticket = authority["execution_plan"]["catalog"]["tickets"][0]
        step = ticket["plan"]["steps"][0]
        execution["semantic"].update(plan_refs=[execution_plan.step_ref(ticket, step)],
            authority_revision=authority["revision"], authority_digest=entry.digest(authority),
            plan_digest=authority["execution_plan"]["plan_digest"])
        return execution

    def test_inline_source_uses_complete_original_b_input_seal(self):
        saved = handoff.handle(self.input_for(3))
        body = handoff.unseal(saved)
        original = copy.deepcopy(body)
        original.pop("inline_authority")
        original["semantic"].pop("execution_plan")
        b0_digest = entry.digest(original)
        self.assertEqual(body["inline_authority"]["execution_plan"]["sources"][0]["input_digest"],
                         b0_digest)
        self.assertNotEqual(saved["digest"], b0_digest)
        self.assertEqual(handoff.verify(saved), saved)
        for field, update in (
            ("entry", lambda data: data["entry"]["host"].update(thread_id="forged-thread")),
            ("binding", lambda data: data["binding"].update(worktree="/tmp/forged-flow")),
            ("authorization", lambda data: data["authorization"].update(reference="forged-approval")),
            ("semantic", lambda data: data["semantic"].update(objective="forged objective")),
        ):
            with self.subTest(field=field):
                forged = copy.deepcopy(body)
                update(forged)
                with self.assertRaises((entry.PreparationError, control.ControlError)):
                    handoff.verify(handoff.seal(forged))

    def launch(self, input_data=None):
        value = input_data or self.input
        if value["stage"] == 3 and value.get("predecessor") is not None:
            import workflow_progress as progress
            state = progress.read_record(self.checkpoint).get(progress.KEY, {})
            if state.get("stage") == 2 and state.get("accepted") == value["predecessor"]:
                pending = state.get("pending")
                if pending is not None and pending["kind"] == "stage-entry":
                    self.invoke("decide", {"decision_id": pending["decision_id"],
                        "subject": pending["subject"], "answer": "confirm",
                        "reference": "user:stage-entry"})
                value = copy.deepcopy(value)
                value["readiness"] = progress.scope_readiness(self.state(), self.checkpoint)
                started = self.invoke("start", {"handoff": value,
                    "control": {"context": self.context, "discussion": None}})
                self.assertEqual(started["next_action"]["operation"], "invoke-host", started)
                state = self.state()
                self.context = state["control"]["context"]
                return {"record": state["dispatch"], "host_call": state["dispatch"]["request"],
                        "checkpoint": {"context": self.context}}
        saved = handoff.handle(value)
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
        # The direct B result is only planning_complete. A successor requires
        # C's sealed, published, stopped, and accepted current readiness.
        if not hasattr(self, "invoke"):
            from test_workflow_progress import ProgressTests
            for name in ("invoke", "state", "begin", "observation"):
                setattr(self, name, getattr(ProgressTests, name).__get__(self, type(self)))
        self.begin("stepwise")
        self.invoke("observe", self.observation())
        planning_paths = self.input["scope"]["owned_paths"]
        for path in planning_paths:
            plan = {"schema_version": 1, "ticket_id": "FEP-01", "plan_version": 1,
                "spec_ref": {"path": "docs/spec.md", "section": "Implementation Decisions"},
                "contract_refs": ["FEP-01:What to build", "FEP-01:AC-1", "PRD:D1:Plan"],
                "blocked_by": [], "verifications": [{"id": "V1", "behavior": "approved behavior",
                    "seam": "real C/B/Git fixture", "commands":
                    ["python3 scripts/run_tests.py --test-file tests/runtime/test_stage_transfer.py"]}],
                "steps": [{"id": "FP1-01", "target": {"path": "impl.py", "symbol": "run", "operation": "add"},
                    "capability": "implement approved behavior", "approach": ["write and connect run"],
                    "inputs": ["approved design"], "outputs": ["observable result"],
                    "failures": ["invalid input is reported"], "depends_on": [],
                    "verification_refs": ["V1"], "read_only": []}], "artifact_steps": []}
            content = "approved plan\nImplementation: impl.py\nClosure: CHANGELOG.md\n"
            if path == "docs/spec.md":
                content += ("Ticket ID: FEP-01\nPlan Version: 1\n**What to build:** approved behavior\n"
                    "**Blocked by:** None\nAC-1: approved behavior\n## Implementation Decisions\n"
                    "### D1 — Plan\n```execution-plan-v1\n" + json.dumps(plan) + "\n```\n")
            (self.flow / path).write_text(content)
        self.flow_git("add", *planning_paths)
        self.flow_git("commit", "-qm", "planning")
        planning = self.flow_git("rev-parse", "HEAD")
        basis = {"commit": planning, "path": "docs/spec.md", "section": "approved plan"}
        closure_paths = getattr(self, "planned_closure_paths", ["CHANGELOG.md"])
        manifest = [{"path": path, "owner": "closure", "kind": "document",
                     "operation": "add", "basis": basis} for path in closure_paths]
        manifest.append({"path": "impl.py", "owner": "implementation", "kind": "source",
                         "operation": "add", "basis": basis})
        manifest.sort(key=lambda item: item["path"])
        saved = self.state()
        proposal = {"proposal_id": "proposal-1", "expected_revision": 0,
            "candidate_commit": planning, "manifest": manifest, "implementation_required": True,
            "requirement_identity": copy.deepcopy(saved["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(saved["handoff"]["binding"]), "previous_scope_revision": 0}
        proposed = self.invoke("scope-propose", proposal)
        self.assertEqual(proposed["next_action"]["operation"], "scope-proposed", proposed)
        observed = self.observation("stopped", "result")
        observed["publication_candidate"] = {"candidate_commit": planning,
            "artifacts": planning_paths, "checks": ["design readiness"]}
        response = self.invoke("observe", observed)
        pending = response["pending"]
        self.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                              "answer": "accept", "reference": "controller:planning-readiness"})
        sealed = self.invoke("scope-seal", {"proposal_id": "proposal-1",
            "proposal_digest": proposed["next_action"]["proposal"]["digest"],
            "controller_ref": "task", "decision": {"kind": "in-requirement-refinement",
                "reference": "controller:scope-review"}})
        self.assertEqual(sealed["next_action"]["operation"], "scope-sealed", sealed)
        self.invoke("publication", {"candidate_commit": planning,
                    "reference": "controller:planning-readiness"})
        pending = self.invoke("receive-publication")["pending"]
        accepted = self.invoke("decide", {"decision_id": pending["decision_id"],
            "subject": pending["subject"], "answer": "accept", "reference": "review:accepted"})
        self.assertTrue(accepted["planning_complete"], accepted)
        self.context = self.state()["control"]["context"]
        return {**accepted, "accepted": self.state()["accepted"], "record": self.state()["dispatch"]}

    def finalize_candidate(self, record, candidate):
        import workflow_control_git
        saved = record['handoff']
        def apply(action, evidence):
            result = dispatch.checkpoint({'context':self.context,'discussion':None}, saved, action, evidence)[0]
            self.context = result['context']
            return result
        progress = self.context['handoff_progress']
        if progress.get('candidate') is not None:
            apply('invalidate-candidate', {'candidate':progress['candidate'],'reference':'fixture:invalidate','reason':'replacement'})
        self.fixture_accepted_execution(self.context, candidate)
        plan = progress['validation_plan']; target = self.git('rev-parse','main')
        apply('candidate-ready', {'dispatcher_ref':'native:dispatcher','attempt':self.context['carrier']['attempt'],
              'commit':candidate,'expected_target_head':target,'binding':self.binding,
              'plan_digest':control.digest(plan),'checks':checks_for(plan['review_required'],candidate),
              'source':{'adapter':'fixture','call_ref':'focused','response_ref':'focused-result','raw':{
              'checks':checks_for(plan['review_required'],candidate),'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}})
        apply('review-start', {'candidate':candidate})
        review, verification = verification_fixture(candidate,'native:dispatcher',self.flow,target)
        for axis in review:
            review[axis]['plan_authority'] = copy.deepcopy(progress['plan_authority'])
        apply('review-converged', {'candidate':candidate,'review':review,'reference':'fixture:convergence',
                                  'native_evidence':verification['review_decision']['native_evidence']})
        progress = self.context['handoff_progress']
        apply('validation-start', {'attempt_id':'final-'+candidate,'dispatcher_ref':'native:dispatcher',
              'carrier_attempt':self.context['carrier']['attempt'], 'candidate':candidate,'expected_target_head':target,
              'plan_digest':progress['plan_digest'],'plan_authority':progress['plan_authority'],
              'review_digest':progress['review_digest']})
        checks = checks_for(plan['final_required'],candidate)
        apply('validation-result', {'attempt_id':'final-'+candidate,'checks':checks,
              'source':{'adapter':'fixture','call_ref':'command','response_ref':'command-result',
                        'raw':{'attempt_id':'final-'+candidate,'checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}})
        return {'artifacts':['impl.py'],'checks':['CLI passed'],'candidate_commit':candidate,
                'review':review,'verification':control.delivery_verification(self.context['handoff_progress'],'native:dispatcher')}

    def mark_reviewable(self, candidate, *, fixture_execution=True):
        if fixture_execution:
            outer = progress.read_record(self.checkpoint)
            self.fixture_accepted_execution(outer[progress.KEY]['control']['context'], candidate)
            progress.atomic_save(self.checkpoint, outer)
        self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = checks_for(checkpoint['validation_plan']['review_required'], candidate)
        ready = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': self.git('rev-parse', 'main'),
            'binding': self.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks},
            'receipt': {'adapter': 'fixture', 'call_ref': 'focused-stage3',
                'response_ref': 'focused-stage3-result', 'raw': {'checks': checks,
                    'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'reviewable', ready)
        return {'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
                'plan_authority': checkpoint['plan_authority'], 'checks': checks}

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
        rendered = handoff.render(accepted["accepted"], readiness=accepted["readiness"])
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

    def test_stage3_selection_requires_complete_native_inventory(self):
        accepted = self.complete_design()
        self.request['host']['supported_configurations'] = [
            {'model': 'fixture-model', 'reasoning_effort': 'high'},
            {'model': 'gpt-6-sol', 'reasoning_effort': 'high'},
        ]
        implementation = self.input_for(3, accepted['accepted'])
        selected = configuration('implementation-dispatcher')
        selected.update(can_override=True, preferred={'model': 'gpt-6-sol', 'effort': 'high'},
                        preference_reason='use the available Stage-3 default for implementation')
        selected['supported'].append({**selected['supported'][0],
                                      'model': 'gpt-6-sol'})
        implementation['configuration'] = selected
        with patch.object(model_inventory, 'available_pairs', side_effect=model_inventory.ModelInventoryError('offline')) as inventory:
            self.assertTrue(handoff.handle(implementation))
            incomplete = copy.deepcopy(implementation)
            incomplete['configuration']['supported'] = selected['supported'][:1]
            incomplete['configuration']['preferred'] = {'model': 'fixture-model', 'effort': 'high'}
            self.assert_code('configuration_changed', lambda: handoff.handle(incomplete))
            filtered = copy.deepcopy(incomplete)
            filtered['entry']['host']['supported_configurations'] = [
                {'model': 'fixture-model', 'reasoning_effort': 'high'}]
            filtered['expected_entry'] = entry.resolve(filtered['entry'])
            self.assertEqual(handoff.handle(filtered)['selection']['model'], 'fixture-model')
            unsupported_frozen = copy.deepcopy(filtered)
            unsupported_frozen['configuration']['frozen'] = {'model': 'unavailable', 'effort': 'high'}
            self.assertEqual(handoff.handle(unsupported_frozen)['selection']['model'], 'fixture-model')
            inherited = copy.deepcopy(filtered)
            inherited['configuration'] = configuration('implementation-dispatcher')
            self.assertEqual(handoff.handle(inherited)['selection']['source'], 'inherited')
            inconsistent = copy.deepcopy(implementation)
            inconsistent['configuration']['can_override'] = False
            inconsistent['configuration']['inherited'] = {'model': 'fixture-model', 'effort': 'high'}
            self.assert_code('configuration_changed', lambda: handoff.handle(inconsistent))
            inventory.assert_not_called()

    def test_controller_model_can_differ_from_explicit_child_selection(self):
        self.settings.update(model='controller-only-model', reasoning_effort='low')
        request = copy.deepcopy(self.input)
        request['configuration'].update(can_override=True,
            user={'model': 'fixture-model', 'effort': 'high'})
        prepared = handoff.handle(request)
        self.assertEqual(prepared['selection']['model'], 'fixture-model')
        self.assertEqual(prepared['selection']['source'], 'user')
        self.assertEqual(handoff.verify(prepared), prepared)

    def test_stage3_selects_native_default_without_account_catalog(self):
        accepted = self.complete_design()
        self.request['host']['supported_configurations'] = [
            {'model': 'fixture-model', 'reasoning_effort': 'high'},
            {'model': 'gpt-6-sol', 'reasoning_effort': 'medium'},
        ]
        implementation = self.input_for(3, accepted['accepted'])
        selected = configuration('implementation-dispatcher')
        selected.update(can_override=True,
                        preferred={'model': 'gpt-6-sol', 'effort': 'medium'},
                        preference_reason='use the supported Stage-3 default')
        selected['supported'].append({**selected['supported'][0],
                                      'model': 'gpt-6-sol', 'effort': 'medium'})
        implementation['configuration'] = selected
        prepared = handoff.handle(implementation)
        self.assertEqual(prepared['selection']['model'], 'gpt-6-sol')
        self.assertEqual(prepared['selection']['effort'], 'medium')

    def test_stage3_explicit_supported_user_choice_overrides_default_pool(self):
        accepted = self.complete_design()
        self.request['host']['supported_configurations'].append(
            {'model': 'gpt-6-sol', 'reasoning_effort': 'high'})
        implementation = self.input_for(3, accepted['accepted'])
        selected = implementation['configuration']
        selected['can_override'] = True
        selected['supported'].append({**selected['supported'][0], 'model': 'gpt-6-sol'})
        for field, source in [('user', 'user'), ('frozen', 'confirmed')]:
            with self.subTest(field=field):
                selected['user'] = selected['frozen'] = None
                selected[field] = {'model': 'fixture-model', 'effort': 'high'}
                prepared = handoff.handle(implementation)
                self.assertEqual(prepared['selection']['model'], 'fixture-model')
                self.assertEqual(prepared['selection']['source'], source)
                self.assertEqual(handoff.verify(prepared), prepared)

    def test_frozen_stage3_handoff_ignores_account_catalog_changes(self):
        accepted = self.complete_design()
        self.request['host']['supported_configurations'] = [
            {'model': 'fixture-model', 'reasoning_effort': 'high'}]
        implementation = self.input_for(3, accepted['accepted'])
        selected = configuration('implementation-dispatcher')
        selected.update(can_override=True, preferred={'model': 'fixture-model', 'effort': 'high'},
                        preference_reason='no default pair is currently available')
        implementation['configuration'] = selected
        with patch.object(model_inventory, 'available_pairs',
                          side_effect=[{('fixture-model', 'high')},
                                       {('fixture-model', 'high'), ('gpt-6-sol', 'high')} ]) as inventory:
            frozen = handoff.handle(implementation)
            self.assertEqual(handoff.verify(frozen), frozen)
            self.assertNotIn('selection_catalog', frozen)
            inventory.assert_not_called()

    def test_legacy_catalog_handoff_requires_original_runtime(self):
        saved = handoff.unseal(handoff.handle(self.input))
        saved.update(protocol='workflow-stage-transfer-v6', selection_catalog=None)
        saved = handoff.seal(saved)
        for operation in (handoff.verify, handoff.render):
            self.assert_code('legacy_run_requires_original_runtime', lambda: operation(saved))

    def test_accepted_design_consumed_by_stage3_and_actual_candidate_review(self):
        accepted = self.complete_design()["accepted"]
        next_input = self.input_for(3, accepted)
        self.context = self.context_for(3)
        self.launch(next_input)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        (self.flow / "impl.py").write_text("print('ok')\n")
        self.flow_git("add", "impl.py"); self.flow_git("commit", "-qm", "implement")
        candidate = self.flow_git("rev-parse", "HEAD")
        payload = {"artifacts": ["impl.py"], "checks": ["CLI passed"], "candidate_commit": candidate,
                   "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"} for axis in ("standards", "spec")},
                   "verification": {"candidate": candidate, "checks": ["CLI passed"]}}
        from test_workflow_progress import ProgressTests
        self.accept_current = ProgressTests.accept_current.__get__(self, type(self))
        done = self.accept_current(payload, 'native:dispatcher')
        self.assertEqual(done['status'], 'accepted', done)
        self.assertEqual(self.state()['accepted']['payload']['candidate_commit'], candidate)
        closure = self.input_for(4, self.state()['accepted'])
        self.context = self.context_for(4)
        launched = self.launch(closure)
        self.assertEqual(launched["host_call"]["role"], "closure-agent")
        bound = self.call("bind", launched["record"], receipt=self.receipt(launched["record"], ref="native:closure"))
        supervision.complete_worktree({"binding": self.binding, "candidate_commit": candidate,
                    "expected_target_head": self.git("rev-parse", "HEAD"), "scope_base_commit": closure["scope"]["baseline"],
                    "allowed_paths": ["impl.py"], "protected_paths": closure["scope"]["protected_paths"]})
        payload = {"artifacts": ["impl.py"], "checks": ["publication verified"], "candidate_commit": candidate,
                   "merge_commit": self.git("rev-parse", "HEAD"), "changed_paths": ["impl.py"],
                   "cleanup": {"worktree_removed": True, "branch_removed": True, "disposition": "deleted", "disposition_verified": True}}
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
        # This independent source controller starts in the linked checkout;
        # its registry provenance names that original source project.
        source_entry = self.with_registration(source_entry, project=self.flow, receipt='host:source-project-registry')
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
        self.assert_code('source_history_conflict', lambda: handoff.handle(candidate))
        self.flow = self.root.parent / (self.root.name + '-delivered-flow')
        self.binding = supervision.start_worktree({"repository": str(self.root),
            "worktree": str(self.flow), "branch": "codex/delivered-flow",
            "target_branch": "main"})["binding"]
        self.addCleanup(lambda flow=self.flow: subprocess.run(["git", "-C", str(self.root),
            "worktree", "remove", "--force", str(flow)], capture_output=True))
        entry.os.getcwd.return_value = str(self.flow)
        self.request['host']['project_path'] = str(self.flow)
        self.request['target'] = {'kind': 'flow', 'binding': self.binding}
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
        self.assert_code("dirty_worktree", lambda: self.call("accept", handoff.seal(record),
            decision=completed["accepted"]["decision"], scope_checkpoint_path=str(self.checkpoint),
            scope_authority=self.state()["scope_authority"]))

    def test_dedicated_reservation_replay_has_no_host_call(self):
        entry.os.getcwd.return_value = str(self.root)
        self.request.update(stage=1, source={"kind": "stage1", "path": self.requirement_path},
                            target={"kind": "planning", "repository": str(self.root), "branch": "main"})
        self.request["host"]["project_path"] = str(self.root)
        candidate = copy.deepcopy(self.input)
        candidate.update(stage=1, role="dedicated-problem-framing", entry=copy.deepcopy(self.request), expected_entry=entry.resolve(self.request),
                         binding=None, configuration=configuration("dedicated-problem-framing"))
        candidate["scope"] = {"baseline": candidate["scope"]["baseline"],
                              "owned_paths": [self.requirement_path], "protected_paths": [],
                              "implementation_paths": [], "closure_paths": []}
        candidate["authorization"]["scope_digest"] = entry.digest(candidate["scope"])
        ctx = self.context_for(1)
        def transition(action, evidence):
            nonlocal ctx
            result = control.transition({"schema_version": 5, "actor_ref": "task", "context": ctx, "action": action, "evidence": evidence})
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
        execution = self.inline_execution_input()
        launched = self.launch(execution)
        self.assertEqual(launched["host_call"]["payload"]["write_authority"], 'await-bound-release')
        bound = self.call("bind", launched["record"], receipt=self.receipt(launched["record"], ref="native:executor"))
        data = b"print('slice')\n"
        (self.flow / "impl.py").write_bytes(data)
        message = {"delivery_id": "slice", "status": "completed", "payload": {"changed_paths": ["impl.py"],
                    "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["production CLI passed"],
                    "write_release": bound["write_release"],
                    "plan_refs": execution["semantic"]["plan_refs"],
                    "plan_authority": self.context["handoff_progress"]["plan_authority"]}}
        missing_release = copy.deepcopy(message)
        missing_release['payload'].pop('write_release')
        self.assert_code('invalid_request', lambda: self.call("receive", bound["record"],
            receipt=self.receipt(bound["record"], "stopped", "result", "native:executor"), result=missing_release))
        received = self.call("receive", bound["record"], receipt=self.receipt(bound["record"], "stopped", "result", "native:executor"), result=message)
        accepted = self.call("accept", received["record"], decision={"reference": "slice:accepted", "delivery_digest": received["record"]["delivery"]["digest"]})
        self.assertFalse(accepted["downstream_ready"])
        self.assertEqual(self.context["handoff_progress"]["executions"][0]["state"], "accepted")

    def test_cancelled_dispatcher_can_reconcile_unbound_allocation_before_recovery(self):
        self.context = self.context_for(3)
        parent = self.launch(self.input_for(3))
        self.call("bind", parent["record"], receipt=self.receipt(parent["record"], ref="native:dispatcher"))
        execution = self.inline_execution_input()
        pending = self.launch(execution)["record"]
        self.context = control.transition({"schema_version": 5, "actor_ref": "task", "context": self.context,
                                           "action": "cancel", "evidence": {}})["context"]
        unknown = self.call("reconcile", pending, receipt=self.receipt(pending, "unknown", "lookup", None))
        self.assertEqual(self.context["handoff_progress"]["executions"][0]["state"], "dispatch-pending")
        self.assert_code("attempt_mismatch", lambda: self.call("reconcile", unknown["record"], receipt=self.receipt(pending, "ready", "lookup", "late")))
        done = self.call("reconcile", unknown["record"], receipt=self.receipt(pending, "not-created", "lookup", None))
        self.assertEqual(self.context["handoff_progress"]["state"], "cancelled")
        self.assertTrue(self.context["handoff_progress"]["executions"][0]["stopped"])
        self.assertTrue(self.call("reconcile", pending, receipt=self.receipt(pending, "not-created", "lookup", None))["acknowledged"])
        self.assert_code("attempt_mismatch", lambda: self.call("reconcile", done["record"], receipt=self.receipt(pending, "ready", "lookup", "late")))
        import workflow_control_git
        def recover(stopped):
            return workflow_control_git.verified_transition({"repository": str(self.flow), "baseline": execution["scope"]["baseline"],
                "request": {"schema_version": 5, "actor_ref": "task", "context": self.context, "action": "recover-dispatch",
                            "evidence": {"stopped_refs": stopped, "file_hashes": {}, "replacement_ref": "native:replacement"}}})
        with self.assertRaises(control.ControlError):
            recover([])
        with self.assertRaises(control.ControlError):
            recover(["native:dispatcher"])
        self.assertEqual(self.context["handoff_progress"]["state"], "cancelled")

    def test_planning_read_only_in_implementation_but_authorized_for_closure(self):
        self.input["scope"]["owned_paths"] = ["docs/adr.md", "docs/spec.md"]
        self.planned_closure_paths = ["docs/spec.md"]
        self.input["authorization"]["scope_digest"] = entry.digest(self.input["scope"])
        design = self.complete_design()["accepted"]
        implementation = self.input_for(3, design)
        implementation["scope"]["closure_paths"] = ["docs/spec.md"]
        implementation["scope"]["protected_paths"] = sorted([self.requirement_path, "docs/adr.md", "docs/spec.md"])
        implementation["authorization"]["scope_digest"] = entry.digest(implementation["scope"])
        self.context = self.context_for(3)
        self.launch(implementation)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        (self.flow / "impl.py").write_text("print('done')\n")
        self.flow_git("add", "impl.py"); self.flow_git("commit", "-qm", "implementation")
        candidate = self.flow_git("rev-parse", "HEAD")
        payload = {"artifacts": ["impl.py"], "checks": ["CLI passed"], "candidate_commit": candidate,
                   "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"} for axis in ("standards", "spec")},
                   "verification": {"candidate": candidate, "checks": ["CLI passed"]}}
        # A planning write cannot be accepted as an implementation result.
        (self.flow / "docs/spec.md").write_text("unauthorized implementation edit\n")
        self.flow_git("add", "docs/spec.md"); self.flow_git("commit", "-qm", "invalid implementation planning edit")
        bad = copy.deepcopy(payload)
        bad["candidate_commit"] = self.flow_git("rev-parse", "HEAD")
        review_plan = self.state()['control']['context']['handoff_progress']['validation_plan']
        bad['verification'] = {'candidate': bad['candidate_commit'],
            'expected_target_head': self.git('rev-parse', 'main'),
            'validation_plan': review_plan, 'plan_digest': control.digest(review_plan),
            'review_digest': 'diagnostic', 'review_decision': {}, 'attempts': [],
            'dispatcher_ref': 'native:dispatcher',
            'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}
        for axis in bad["review"].values():
            axis["candidate"] = bad["candidate_commit"]
        self.assert_code('invalid_scope', lambda: handoff.verify_result(3, bad,
            self.binding, implementation['scope'], str(self.flow), 'native:dispatcher'))
        # Reverting in a new commit would retain the unauthorized planning edit
        # in candidate history. Discard that commit before resuming acceptance.
        self.flow_git("reset", "--hard", candidate)
        from test_workflow_progress import ProgressTests
        self.accept_current = ProgressTests.accept_current.__get__(self, type(self))
        done = self.accept_current(payload, 'native:dispatcher')
        self.assertEqual(done['status'], 'accepted', done)
        accepted = self.state()['accepted']
        self.assertIn("docs/spec.md", accepted["handoff"]["scope"]["protected_paths"])
        self.assertEqual(handoff.render(accepted)["payload"]["protected_paths"], sorted([self.requirement_path, "docs/adr.md"]))
        closure = self.input_for(4, accepted)
        closure["scope"].update(owned_paths=["docs/spec.md"], closure_paths=["docs/spec.md"], protected_paths=sorted([self.requirement_path, "docs/adr.md"]))
        closure["authorization"]["scope_digest"] = entry.digest(closure["scope"])
        for changed in ([self.requirement_path], ["other.md"]):
            bad = copy.deepcopy(closure)
            bad["scope"]["closure_paths"] = changed
            bad["authorization"]["scope_digest"] = entry.digest(bad["scope"])
            with self.assertRaises(entry.PreparationError):
                handoff.handle(bad)
        bad = copy.deepcopy(closure)
        bad["scope"]["protected_paths"] = [self.requirement_path]
        bad["authorization"]["scope_digest"] = entry.digest(bad["scope"])
        self.assert_code("scope_changed", lambda: handoff.handle(bad))
        self.context = self.context_for(4)
        launched = self.launch(closure)
        bound = self.call("bind", launched["record"], receipt=self.receipt(launched["record"], ref="native:closure"))
        target_before = self.git("rev-parse", "HEAD")
        for forbidden in (self.requirement_path, "impl.py", "docs/adr.md"):
            # Simulate a faulty publisher in this disposable Git fixture. B
            # must reject the real out-of-scope merge, even with a stopped ref.
            (self.flow / forbidden).write_text("out of scope\n")
            self.flow_git("add", forbidden); self.flow_git("commit", "-qm", "invalid closure")
            self.git("merge", "--ff-only", self.binding["branch"])
            wrong_result = {"artifacts": [forbidden], "checks": ["claimed"], "candidate_commit": candidate,
                            "merge_commit": self.git("rev-parse", "HEAD"), "changed_paths": [forbidden],
                            "cleanup": {"worktree_removed": True, "branch_removed": True, "disposition": "deleted", "disposition_verified": True}}
            self.assert_code("invalid_scope", lambda: self.call("receive", bound["record"],
                receipt=self.receipt(bound["record"], "stopped", "result", "native:closure"),
                result={"delivery_id": "bad-closure", "status": "completed", "payload": wrong_result}))
            self.git("reset", "--hard", target_before)
            self.flow_git("reset", "--hard", candidate)
        (self.flow / "docs/spec.md").write_text("approved plan\nImplementation completed.\n")
        self.flow_git("add", "docs/spec.md"); self.flow_git("commit", "-qm", "close authorized spec")
        supervision.complete_worktree({"binding": self.binding, "candidate_commit": self.flow_git("rev-parse", "HEAD"),
                    "expected_target_head": self.git("rev-parse", "HEAD"), "scope_base_commit": closure["scope"]["baseline"],
                    "allowed_paths": ["docs/spec.md", "impl.py"], "protected_paths": closure["scope"]["protected_paths"]})
        payload = {"artifacts": ["docs/spec.md"], "checks": ["publication verified"], "candidate_commit": candidate,
                   "merge_commit": self.git("rev-parse", "HEAD"), "changed_paths": ["docs/spec.md", "impl.py"],
                   "cleanup": {"worktree_removed": True, "branch_removed": True, "disposition": "deleted", "disposition_verified": True}}
        received = self.call("receive", bound["record"], receipt=self.receipt(bound["record"], "stopped", "result", "native:closure"),
                             result={"delivery_id": "closure", "status": "completed", "payload": payload})
        self.assertTrue(self.call("accept", received["record"], decision={"reference": "closed", "delivery_digest": received["record"]["delivery"]["digest"]})["downstream_ready"])


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
            self.request = self.with_registration(self.request, project=self.root, receipt='host:non-git-registry')
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

    def test_successor_dispatch_uses_real_ledger_slot_without_promoting_old_carrier(self):
        if self.NON_GIT:
            self.skipTest("wrapper transfer uses a Git stage-entry checkpoint")
        path = self.input["scope"]["owned_paths"][0]
        self.git("add", "-f", path); self.git("commit", "-qm", "stage zero input")
        old = self.read()["workflow_control"]
        old_plan = old["handoff_progress"]["plan"]["plan_id"]
        self.mutate("bind-handoff", handoff_id=self.original["handoff_id"], attempt_id=self.original["attempt_id"], conversation_ref="previous",
                    verified_identity={"project_id": self.attachment["project_id"], "tree_id": self.attachment["tree_id"],
                       "topic_id": self.attachment["actor_topic_id"], "handoff_id": self.original["handoff_id"],
                       "attempt_id": self.original["attempt_id"], "payload_sha256": self.original["payload_sha256"]})
        self.mutate("workflow-control", action="creation-result", evidence={"status": "ready", "ref": "previous", "attempt": old_plan})
        def carrier(operation, **values):
            return discussion_protocol.handle({**self.envelope(), "actor_conversation_ref": "previous", "operation": operation, **values})
        carrier("accept-handoff", handoff_id=self.original["handoff_id"], attempt_id=self.original["attempt_id"],
                payload_sha256=self.original["payload_sha256"], source_reference_sha256=self.original["authoritative_references_sha256"], turn_number=1)
        old_delivery = self.mutate("workflow-control", action="receive", evidence={"delivery_id": "previous-result", "source_ref": "previous",
            "attempt": old_plan, "commit": self.git("rev-parse", "HEAD"), "requirement_identity": old["requirement_identity"],
            "verified_commit_hash": old["requirement_identity"]["sha256"]})["control"]
        self.mutate("workflow-control", action="accept", evidence={"delivery_digest": old_delivery["delivery_digest"]})
        cp = carrier("prepare-checkpoint", purpose="stage-entry", base_ref="HEAD")
        carrier("publish-git-checkpoint", checkpoint_id=cp["checkpoint_id"], expected_checkpoint_revision=cp["checkpoint_record_revision"])
        self.mutate("prepare-wrapper-phase-run", from_phase=0, to_phase=1, route="0->1", carrier_kind="dedicated-grilling",
                    source_checkpoint_id=cp["checkpoint_id"], flow_mode="stepwise", flow_mode_source="explicit-stage-confirmation", scope=["requirements"])
        plan = self.mutate("workflow-control", action="prepare", evidence={"target": "local", "project": self.attachment["project_id"],
                "title": "Frame", "missing_context": [], "configuration": configuration("dedicated-problem-framing"),
                "next_step": "stage2", "archive_ref": None, "gate_open": True})["control"]["plan"]
        self.mutate("workflow-control", action="decide", evidence={"plan_id": plan["plan_id"], "intent": "confirm"})
        self.request["stage"] = 1
        self.input.update(stage=1, role="dedicated-problem-framing", entry=copy.deepcopy(self.request), expected_entry=entry.resolve(self.request),
                          configuration=configuration("dedicated-problem-framing"))
        ambiguous = handoff.handle(self.input)
        before = self.ledger.read_bytes()
        self.assert_code("plan_ambiguous", lambda: dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": ambiguous, "control": self.port()}))
        self.assertEqual(before, self.ledger.read_bytes())
        wrong = copy.deepcopy(self.input)
        wrong["authorization"]["control_plan_id"] = old_plan
        self.assert_code("source_changed", lambda: dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": handoff.handle(wrong), "control": self.port()}))
        self.assertEqual(before, self.ledger.read_bytes())
        wrong["authorization"]["control_plan_id"] = "unknown-plan"
        with self.assertRaises(control.ControlError):
            dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": handoff.handle(wrong), "control": self.port()})
        self.assertEqual(before, self.ledger.read_bytes())
        self.input["authorization"]["control_plan_id"] = plan["plan_id"]
        saved = handoff.handle(self.input)
        launched = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved, "control": self.port()})
        record = launched["record"]
        self.assertEqual(record["request"]["attempt"], plan["plan_id"])
        self.assertEqual(self.read()["workflow_control"]["carrier"]["ref"], "previous")
        receipt = {"adapter": "fixture", "receipt_ref": "pending", "request_digest": record["request"]["digest"],
                "attempt": plan["plan_id"], "role": "dedicated-problem-framing", "event": "create", "status": "pending",
                "ref": None, "pending_id": "client-1", "configuration": None, "raw": {"clientThreadId": "client-1"}}
        pending = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "bind", "record": record, "control": self.port(), "receipt": receipt})
        ready_receipt = {**receipt, "event": "lookup", "status": "ready", "ref": "successor", "pending_id": None,
                         "configuration": {"model": "fixture-model", "effort": "high"}, "receipt_ref": "ready", "raw": {"threadId": "successor"}}
        before = self.ledger.read_bytes()
        self.assert_code("identity_mismatch", lambda: dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "reconcile",
            "record": pending["record"], "control": self.port(), "receipt": {**ready_receipt, "attempt": old_plan}}))
        self.assertEqual(before, self.ledger.read_bytes())
        ready = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "reconcile", "record": pending["record"], "control": self.port(), "receipt": ready_receipt})
        actual = self.read()["workflow_control"]
        self.assertEqual(actual["stage"], 0)
        self.assertEqual(actual["carrier"]["ref"], "previous")
        self.assertEqual(actual["handoff_progress"]["state"], "result-accepted")
        self.assertEqual(actual["successor_control"]["carrier"]["ref"], "successor")
        self.assertFalse(ready["downstream_ready"])
        before = self.ledger.read_bytes()
        recovered = dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "reconcile", "record": pending["record"], "control": self.port(), "receipt": ready_receipt})
        self.assertTrue(recovered["acknowledged"])
        self.assertEqual(before, self.ledger.read_bytes())
        self.mutate("workflow-control", action="cancel", evidence={"control_plan_id": plan["plan_id"]})
        before = self.ledger.read_bytes()
        self.assert_code("attempt_mismatch", lambda: dispatch.handle({"protocol": handoff.PROTOCOL, "operation": "reconcile",
            "record": ready["record"], "control": self.port(), "receipt": {**ready_receipt, "receipt_ref": "late", "ref": "late-successor"}}))
        self.assertEqual(before, self.ledger.read_bytes())
        self.assertEqual(self.read()["workflow_control"]["handoff_progress"]["state"], "result-accepted")


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
