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
        self.host_response_sequence = 0

    def state(self):
        return progress.read_record(self.checkpoint)[progress.KEY]

    def retired_fixture(self):
        import test_workflow_control
        helper = test_workflow_control.WorkflowControlTests()
        context = helper.context()
        context['controller_ref'] = 'task'
        def apply(action, evidence):
            nonlocal context
            result = progress.control.transition({'schema_version': 4, 'actor_ref': 'task',
                'context': context, 'action': action, 'evidence': evidence})
            context = result['context']
            return result
        planned = apply('prepare', {'target': 'local', 'project': 'project', 'title': 'Old requirement',
            'missing_context': [], 'configuration': helper.configuration(), 'next_step': 'stage2',
            'archive_ref': None, 'gate_open': True})
        attempt = planned['plan']['plan_id']
        apply('decide', {'plan_id': attempt, 'intent': 'confirm'})
        apply('creation-result', {'status': 'ready', 'ref': 'old-task', 'attempt': attempt})
        received = apply('receive', {'delivery_id': 'old-delivery', 'source_ref': 'old-task', 'attempt': attempt,
            'requirement_identity': context['requirement_identity'], 'commit': 'b' * 40,
            'verified_commit_hash': context['requirement_identity']['sha256']})
        apply('accept', {'delivery_digest': received['delivery_digest']})
        ready = apply('successor-ready', {'ref': 'native:designer', 'stage': 2, 'role': 'solution-designer',
            'input_digest': received['delivery_digest'], 'binding_verified': True, 'activated': False,
            'confirmed': True, 'archive_ref': 'old-task', 'takeover_proof': test_workflow_control.takeover_proof('old-task', attempt)})
        self.context['retired_handoffs'] = context['retired_handoffs']
        evidence = context['retired_handoffs'][ready['handoff_id']]['successor']
        request = {'action': 'successor-ready', 'evidence': evidence,
                   'receipt': {'controller_ref': 'task', 'reference': 'old-takeover'}}
        self.takeover_input = copy.deepcopy(request)
        self.takeover_input['evidence'].pop('takeover_proof')
        self.takeover_transaction = {'request': request, 'takeover_input': self.takeover_input,
            'result': ready, 'port': {'context': context, 'discussion': None}}
        return ready['handoff_id']

    def archive_receipt(self, effect, status):
        receipt = {'adapter': 'fixture', 'invocation_id': 'call:' + effect['operation_id'],
            'response_id': status + ':' + effect['operation_id'], 'ref': effect['ref'], 'no_write': status == 'issued',
            'raw': {key: effect[key] for key in ('operation_id', 'operation', 'ref')} if status == 'issued' else {'status': status}}
        return {'action': 'archive-result', 'evidence': {'handoff_id': effect['handoff_id'],
            'operation_id': effect['operation_id'], 'ref': effect['ref'], 'status': status, 'receipt': receipt}, 'receipt': receipt}

    def test_retired_archive_after_start_preserves_current_native_context(self):
        handoff_id = self.retired_fixture()
        self.finish_design('continuous')
        outer = progress.read_record(self.checkpoint)
        outer[progress.KEY].setdefault('control_transactions', {})[transfer.entry.digest(self.takeover_transaction['request'])] = self.takeover_transaction
        progress.atomic_save(self.checkpoint, outer)
        intent = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'automatic'}})
        effect = intent['next_action']['result']['effects'][0]
        self.invoke('control', self.archive_receipt(effect, 'issued'))
        with patch.object(progress.dispatch, 'checkpoint', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('control', self.archive_receipt(effect, 'unknown'))
        self.assertFalse(progress.Progress(self.checkpoint, progress.read_record(self.checkpoint)).unresolved_control_transactions())
        predecessor = self.state()['accepted']
        self.context = self.context_for(3)
        self.begin('continuous', self.input_for(3, predecessor))
        before = copy.deepcopy(self.state()['control']['context'])
        history = copy.deepcopy(self.state()['history'])
        late = self.invoke('control', self.archive_receipt(effect, 'unknown'))
        after = self.state()['control']['context']
        self.assertEqual({k:v for k,v in before.items() if k != 'retired_handoffs'},
                         {k:v for k,v in after.items() if k != 'retired_handoffs'})
        self.assertEqual(self.state()['history'], history)
        query = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'user-recovery'}})['next_action']['result']['effects'][0]
        self.assertEqual((query['operation'], query['ref']), ('read-archive-state', 'old-task'))
        self.invoke('control', self.archive_receipt(query, 'issued'))
        self.invoke('control', self.archive_receipt(query, 'archived'))
        replay = self.invoke('control', self.archive_receipt(effect, 'unknown'))
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['archive_status'], 'archived')
        conflict = self.invoke('control', self.archive_receipt(effect, 'archived'))
        self.assertFalse(conflict['next_action']['result']['ok'])
        saved = self.state()
        self.assertEqual(saved['control']['context']['retired_handoffs'][handoff_id]['archive_status'], 'archived')
        self.assertEqual(len(saved['control']['context']['retired_handoffs'][handoff_id]['conflicts']), 1)
        self.assertEqual(saved['history'], history)
        replayed_takeover = self.invoke('control', self.takeover_input)
        self.assertTrue(replayed_takeover['acknowledged'])
        self.assertEqual(replayed_takeover['next_action']['result']['effects'], [])
        self.assertEqual(self.state()['control']['context'], saved['control']['context'])

    def test_archive_intent_crash_replays_without_duplicate_operation(self):
        handoff_id = self.retired_fixture()
        self.begin()
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                   'receipt': {'controller_ref': 'task', 'reference': 'automatic'}}
        with patch.object(progress.dispatch, 'checkpoint', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('control', request)
        self.assertTrue(progress.Progress(self.checkpoint, progress.read_record(self.checkpoint)).unresolved_control_transactions())
        first = self.invoke('control', request)['next_action']['result']['effects'][0]
        replay = self.invoke('control', request)
        self.assertEqual(replay['next_action']['result']['effects'], [])
        self.assertEqual(len(self.state()['control']['context']['retired_handoffs'][handoff_id]['operations']), 1)
        self.assertEqual(first['ref'], 'old-task')
        lookup = {'operation_id': first['operation_id'], 'ref': 'old-task', 'adapter': 'fixture',
            'invocation_id': 'lookup-call', 'response_id': 'lookup-response', 'status': 'not-issued',
            'raw': {'calls': []}}
        recovered = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id,
            'call_lookup': lookup}, 'receipt': lookup})
        self.assertEqual(recovered['next_action']['result']['effects'], [first])
        self.invoke('control', self.archive_receipt(first, 'issued'))
        query = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'lost-response'}})
        self.assertEqual(query['next_action']['result']['effects'][0]['operation'], 'read-archive-state')

    def test_new_runtime_rejects_old_control_and_c_without_writes(self):
        self.begin()
        saved = progress.read_record(self.checkpoint)
        for version in ('control', 'workflow-progress-v9', 'workflow-progress-v11'):
            old = copy.deepcopy(saved)
            if version == 'control':
                old[progress.KEY]['control']['context']['schema_version'] = 3
            else:
                old[progress.KEY]['protocol'] = version
            progress.atomic_save(self.checkpoint, old)
            before = self.checkpoint.read_bytes()
            with self.assertRaises(transfer.entry.PreparationError) as error:
                self.invoke('inspect')
            self.assertEqual(error.exception.code, 'legacy_run_requires_original_runtime')
            self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_unknown_business_control_blocks_start_and_supplied_stop_is_rejected(self):
        self.begin()
        rejected = self.invoke('control', {'action': 'successor-ready', 'evidence': {'takeover_proof': {'stopped': True}},
            'receipt': {'controller_ref': 'task'}})
        self.assertEqual(rejected['error']['code'], 'invalid_evidence')
        outer = progress.read_record(self.checkpoint)
        request = {'action': 'successor-ready', 'evidence': {}, 'receipt': {'controller_ref': 'task'}}
        outer[progress.KEY]['control_transactions'] = {transfer.entry.digest(request): {
            'request': request, 'port': outer[progress.KEY]['control'], 'result': None}}
        progress.atomic_save(self.checkpoint, outer)
        with self.assertRaises(transfer.entry.PreparationError) as error:
            self.invoke('start', {'handoff': self.input})
        self.assertEqual(error.exception.code, 'control_outcome_unknown')

    def invoke(self, operation, data=None, revision=None):
        if operation == 'start' and data['handoff'].get('stage', 0) >= 2 and 'native_host' not in data:
            carrier = data['handoff']['entry']['host']['thread_id']
            data = {**data, 'native_host': {'adapter': 'app-server', 'snapshot': {
                'instance': (progress.read_record(self.checkpoint).get('transport') or {}).get('instance', 'fixture-host'),
                'carrier_thread': carrier, 'sequence': 0, 'events': [],
                'pages': [{'request': {'method': 'thread/list', 'params': {
                    'ancestorThreadId': carrier, 'archived': archived,
                    'sourceKinds': list(progress.NATIVE_SOURCE_KINDS), 'modelProviders': []}},
                    'response': {'data': [], 'nextCursor': None}} for archived in (False, True)]}}}
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
        # Fixture adapter authenticates each real tool response and its cause.
        action = saved.get("host", {}).get("query") or saved.get("host_action") or saved.get("action")
        if action is not None:
            value["action_id"] = action["action_id"]
        value["receipt"]["receipt_ref"] += ":" + value["event_id"]
        self.host_response_sequence += 1
        if action is not None:
            value["provenance"] = {"call_ref": "fixture-call:" + str(self.host_response_sequence),
                "response_ref": "fixture-response:" + str(self.host_response_sequence), "action_id": action["action_id"]}
            if action["operation"] == "inspect-host-state" and action["payload"].get("unresolved_action"):
                # Fixture host resolves the named invocation in its call history;
                # tests of unavailable/uncertain lookup remove this evidence.
                value["action_resolution"] = {"action_id": action["payload"]["unresolved_action"]["action_id"],
                                              "outcome": "completed"}
        if result is not None:
            value["result"] = result
        if status == 'stopped':
            context = saved['control']['context']
            threads = [{'id':ref, 'status':{'type':'idle'}}]
            for execution in (context.get('handoff_progress') or {}).get('executions', []):
                if execution.get('agent_ref'):
                    threads.append({'id':execution['agent_ref'], 'status':{'type':'idle' if execution['stopped'] else 'active'}})
            for slot in saved.get('review_activity', {}).values():
                if slot and slot.get('ref'):
                    threads.append({'id':slot['ref'], 'status':{'type':'idle' if slot['stopped'] else 'active'}})
            carrier = progress.read_record(self.checkpoint).get('sessions', {}).get('stage' + str(saved['stage'])) or saved['handoff']['expected_entry']['actor']['thread_id']
            transport = progress.read_record(self.checkpoint).get('transport') or {}
            value['lifecycle'] = {'instance':transport.get('instance','fixture-host'),'carrier_thread':carrier,
                'sequence':transport.get('native_event_sequence',0),'events':[],
                'pages':[{'request':{'method':'thread/list','params':{'ancestorThreadId':carrier,'archived':archived,
                          'sourceKinds':list(progress.NATIVE_SOURCE_KINDS),'modelProviders':[]}},
                          'response':{'data':[] if archived else threads,'nextCursor':None}} for archived in (False,True)]}
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
        original_binding = copy.deepcopy(self.state()["handoff"]["binding"])
        original_requirement = (self.flow / self.requirement_path).read_bytes()
        old_request = self.state()["dispatch"]["request"]
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        retry = copy.deepcopy(self.input)
        retry["authorization"]["flow_mode"] = "continuous"
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("start", {"handoff": retry})
        retry["authorization"]["reference"] = "controller:retry-proven-not-created"
        with patch.object(transfer.supervision, "start_worktree", wraps=transfer.supervision.start_worktree) as create:
            started = self.invoke("start", {"handoff": retry})
            create.assert_not_called()
        self.assertEqual(started["next_action"]["operation"], "invoke-host")
        self.assertEqual(self.state()["handoff"]["binding"], original_binding)
        self.assertEqual((self.flow / self.requirement_path).read_bytes(), original_requirement)
        self.assertNotEqual(self.state()["dispatch"]["request"]["digest"], old_request["digest"])
        late = self.observation()
        late["receipt"]["request_digest"] = old_request["digest"]
        self.assertEqual(self.invoke("observe", late)["status"], "blocked")

    def test_noncreation_retry_cannot_change_work_or_target(self):
        self.begin("continuous")
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        before = self.checkpoint.read_bytes()
        changes = (("scope", "owned_paths", ["docs/other.md"]),
                   ("target", "branch", "other-target"),
                   ("binding", "worktree", str(self.flow) + "-replacement"),
                   ("semantic", "objective", "different work"))
        for field, key, value in changes:
            with self.subTest(field=field):
                retry = copy.deepcopy(self.input)
                retry["authorization"].update(flow_mode="continuous", reference="controller:retry")
                retry[field][key] = value
                with self.assertRaisesRegex(transfer.entry.PreparationError, "original work"):
                    self.invoke("start", {"handoff": retry})
                self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_unknown_creation_cannot_retry_with_fresh_controller_authorization(self):
        self.begin("continuous")
        self.invoke("observe", self.observation("unknown", ref=None))
        before = self.checkpoint.read_bytes()
        retry = copy.deepcopy(self.input)
        retry["authorization"].update(flow_mode="continuous", reference="controller:retry")
        with self.assertRaisesRegex(transfer.entry.PreparationError, "reconcile non-creation"):
            self.invoke("start", {"handoff": retry})
        self.assertEqual(self.checkpoint.read_bytes(), before)

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

    def ready_publication(self, candidate, reference, role="native:designer"):
        observed = self.observation("stopped", "result", ref=role)
        observed["publication_candidate"] = {"candidate_commit": candidate, "artifacts": ["docs/spec.md" if role == "native:designer" else "impl.py"], "checks": ["design readiness" if role == "native:designer" else "publication verified"]}
        response = self.invoke("observe", observed)
        self.assertEqual(response["status"], "needs_input", response)
        pending = response["pending"]
        self.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                               "answer": "accept", "reference": reference})

    def prepare_design_result(self, mode):
        self.begin(mode)
        self.invoke("observe", self.observation())
        (self.flow / "docs/spec.md").write_text("approved design\n")
        self.flow_git("add", "docs/spec.md")
        self.flow_git("commit", "-qm", "plan")
        planning = self.flow_git("rev-parse", "HEAD")
        self.ready_publication(planning, "controller:planning-readiness")
        published = self.invoke("publication", {"candidate_commit": planning, "reference": "controller:planning-readiness"})
        self.assertTrue(published["next_action"]["publication"]["result"]["ok"], published)
        payload = {"artifacts": ["docs/spec.md"], "checks": ["design readiness"], "planning_commit": planning,
                   "planning_merge_commit": self.flow_git("rev-parse", "HEAD"), "planning_paths": ["docs/spec.md"]}
        return payload

    def test_publication_requires_exact_readiness_and_stopped_writer(self):
        self.begin("continuous")
        self.invoke("observe", self.observation())
        (self.flow / "docs/spec.md").write_text("approved design\n")
        self.flow_git("add", "docs/spec.md")
        self.flow_git("commit", "-qm", "plan")
        candidate = self.flow_git("rev-parse", "HEAD")
        request = {"candidate_commit": candidate, "reference": "original:review"}
        result = self.invoke("publication", request)
        self.assertEqual(result["error"]["code"], "candidate_not_accepted")
        self.assertNotIn("publication", self.state())
        observed = self.observation("idle", "result")
        observed["publication_candidate"] = {"candidate_commit": candidate, "artifacts": ["docs/spec.md"], "checks": ["readiness"]}
        result = self.invoke("observe", observed)
        self.assertEqual(result["error"]["code"], "host_evidence_missing")
        # The idle response ended that turn. Obtain a causally new stop receipt,
        # then a current query; merely relabelling the old action is not proof.
        self.invoke("pause")
        self.invoke("observe", self.observation("stopped", "result"))
        self.invoke("resume")
        self.ready_publication(candidate, "original:review")
        result = self.invoke("publication", {**request, "reference": "different:review"})
        self.assertEqual(result["error"]["code"], "candidate_changed")
        self.assertNotIn("publication", self.state())
        self.assertEqual(self.invoke("publication", request)["next_action"]["operation"], "publication-receipt")
        received = self.invoke("receive-publication")
        self.assertEqual(received["pending"]["kind"], "acceptance")
        self.assertEqual(self.state()["dispatch"]["delivery"]["message"]["payload"], self.state()["publication"]["completion_payload"])

    def test_resume_reconciles_missing_publication_receipt_and_stop_blocks_writes(self):
        self.begin("continuous")
        self.invoke("observe", self.observation())
        (self.flow / "docs/spec.md").write_text("approved design\n")
        self.flow_git("add", "docs/spec.md")
        self.flow_git("commit", "-qm", "plan")
        candidate = self.flow_git("rev-parse", "HEAD")
        self.ready_publication(candidate, "original:review")
        original = transfer.supervision._merge_candidate_into_target
        def crash(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(transfer.supervision, "_merge_candidate_into_target", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke("publication", {"candidate_commit": candidate, "reference": "original:review"})
        merged = self.git("rev-parse", "HEAD")
        self.assertEqual(self.invoke("pause")["status"], "paused")
        observation = self.invoke("reconcile-publication")
        self.assertEqual(observation["next_action"]["result"]["state"], "flow-advance-pending")
        self.assertEqual(self.flow_git("rev-parse", "HEAD"), candidate)
        with patch.object(transfer.supervision, "publish_planning", side_effect=AssertionError("republished")):
            recovered = self.invoke("resume")
        self.assertEqual(recovered["next_action"]["operation"], "publication-receipt")
        self.assertEqual(self.flow_git("rev-parse", "HEAD"), merged)
        self.assertEqual(self.invoke("resume")["pending"]["kind"], "acceptance")

    def test_overall_completion_requires_original_publication_and_cleanup(self):
        self.run_real_chain("continuous")
        evidence = progress.verify_completion(self.state())
        self.assertTrue(evidence["completed"])
        broken = copy.deepcopy(self.state())
        broken["publication"]["result"] = None
        with self.assertRaises(transfer.entry.PreparationError):
            progress.verify_completion(broken)
        broken = copy.deepcopy(self.state())
        broken["control"]["context"]["handoff_progress"]["state"] = "cleanup-pending"
        with self.assertRaises(transfer.entry.PreparationError):
            progress.verify_completion(broken)
        self.assertFalse(self.flow.exists())
        self.flow.mkdir()
        (self.flow / "personal.txt").write_text("unrelated replacement")
        with self.assertRaises(transfer.entry.PreparationError):
            progress.verify_completion(self.state())
        self.assertEqual((self.flow / "personal.txt").read_text(), "unrelated replacement")

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
        self.assertEqual(self.invoke("resume")["next_action"]["operation"], "inspect-host-state")
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
        self.assertEqual(resumed["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(self.state()["decisions"][answer["decision_id"]], answer)
        continued = self.invoke("observe", self.observation("idle", "result"))
        self.assertEqual(continued["next_action"]["operation"], "continue-host")
        action = self.state()["action"]
        self.assertTrue(self.invoke("decide", answer, revision=0)["acknowledged"])
        self.assertEqual(self.state()["action"], action)

    def test_cancelled_answer_cannot_replace_dispatcher_with_legacy_claims(self):
        answer = self.pending_business_answer(stage=3)
        self.invoke("pause")
        self.invoke("decide", answer)
        self.invoke("cancel")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["disposition"], "cancelled")
        self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(self.invoke("decide", answer)["status"], "cancelled")
        self.assertEqual(self.invoke("resume")["status"], "cancelled")
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("control", {"action": "recover-dispatch", "evidence": {
                "stopped_refs": ["native:dispatcher"], "file_hashes": {}, "replacement_ref": "native:replacement"},
                "receipt": {"host": "fixture", "stopped": "native:dispatcher", "ready": "native:replacement"}})
        self.assertEqual(self.state()["control"]["context"]["carrier"]["ref"], "native:dispatcher")
        self.assertEqual(self.invoke("resume")["status"], "cancelled")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["disposition"], "cancelled")

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
        self.assertEqual(self.invoke("resume")["next_action"]["operation"], "inspect-host-state")

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
                state.refresh(progress.read_record(self.checkpoint))
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

    def test_root_stop_does_not_close_unsettled_execution_allocation(self):
        self.prepare_execution()
        self.invoke('pause')
        self.invoke('observe', self.observation('stopped','result',ref='native:dispatcher'))
        self.assertEqual(self.state()['status'], 'pausing')
        self.assertFalse(self.state()['stopped'])

    def test_malformed_host_event_is_controlled_and_does_not_mutate_checkpoint(self):
        self.begin()
        saved = progress.read_record(self.checkpoint)
        saved['transport'] = {'instance':'fixture-host','state':'live'}
        progress.atomic_save(self.checkpoint,saved)
        before = self.checkpoint.read_bytes()
        for params in ([], None, {'item':[]}, {'item':None},
                       {'threadId':'carrier','item':{'type':'subAgentActivity','id':'call','kind':'started','agentPath':['bad']}}):
            with self.subTest(params=params):
                with self.assertRaises(transfer.entry.PreparationError) as error:
                    self.invoke('host-event',{'instance':'fixture-host','event':{'method':'item/started','params':params}})
                self.assertEqual(error.exception.code,'invalid_result')
                self.assertEqual(self.checkpoint.read_bytes(),before)

    def test_snapshot_rejects_malformed_native_alias_without_saving_it(self):
        self.begin()
        self.invoke('observe',self.observation())
        self.invoke('pause')
        observation = self.observation('stopped','result')
        snapshot = observation['lifecycle']
        snapshot['events'] = [{'method':'item/started','params':{'threadId':snapshot['carrier_thread'],
            'item':{'type':'subAgentActivity','id':'x','kind':'started','agentPath':['bad'],'agentThreadId':'child'}}}]
        before = self.checkpoint.read_bytes()
        for operation, data in (('observe',observation),('lifecycle-state',snapshot)):
            with self.subTest(operation=operation):
                with self.assertRaises(transfer.entry.PreparationError) as error:
                    self.invoke(operation,data)
                self.assertEqual(error.exception.code,'invalid_result')
                self.assertEqual(self.checkpoint.read_bytes(),before)

    def test_previous_candidate_reviewers_require_current_stop_evidence(self):
        self.begin_dispatcher()
        rounds = []
        for number in (1,2):
            (self.flow/'impl.py').write_text('implemented = '+str(number)+'\n')
            self.flow_git('add','impl.py'); self.flow_git('commit','-qm','candidate '+str(number))
            candidate = self.flow_git('rev-parse','HEAD')
            verification = self.mark_reviewable(candidate)
            slots = {}
            for axis in ('standards','spec'):
                prepared = self.invoke('review-activity',{'operation':'prepare','axis':axis,'candidate':candidate,
                    'actor_ref':'task','verification':verification})
                identity = prepared['next_action']['action_id']
                ref = 'native:'+axis+'-'+str(number)
                request = {'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task','action_id':identity,
                    'receipt':{'adapter':'fixture','call_ref':'wait-'+ref,'response_ref':'stopped-'+ref,
                    'ref':ref,'status':'stopped','raw':{'task_name':ref,'status':'completed'}}}
                self.invoke('review-activity',request)
                slots[axis] = request
            rounds.append(slots)
        self.invoke('pause')
        observation = self.observation('stopped','result',ref='native:dispatcher')
        snapshot = observation['lifecycle']
        for request in rounds[0].values():
            ref = request['receipt']['ref']
            snapshot['pages'][0]['response']['data'].append({'id':ref,'status':{'type':'idle'}})
            snapshot['events'].append({'method':'item/completed','params':{'threadId':snapshot['carrier_thread'],
                'item':{'type':'subAgentActivity','id':'create-'+ref,'kind':'started','agentPath':ref,'agentThreadId':ref}}})
        self.assertEqual(self.invoke('observe',observation)['status'],'paused')
        active = copy.deepcopy(snapshot)
        active['pages'][0]['response']['data'][-1]['status'] = {'type':'active'}
        self.assertEqual(self.invoke('lifecycle-state',active)['status'],'pausing')
        omitted = copy.deepcopy(snapshot); omitted['pages'][0]['response']['data'].pop()
        self.assertEqual(self.invoke('lifecycle-state',omitted)['status'],'pausing')
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'paused')
        old = rounds[0]['standards']
        saved = progress.read_record(self.checkpoint)
        saved['transport'] = {'instance':snapshot['instance'],'state':'live','native_event_sequence':1}
        progress.atomic_save(self.checkpoint,saved)
        event = {'method':'item/started','params':{'threadId':snapshot['carrier_thread'],'item':{
            'id':'late-old-review','type':'subAgentActivity','kind':'interacted','agentPath':old['receipt']['ref'],
            'agentThreadId':old['receipt']['ref']}}}
        self.assertEqual(self.invoke('host-event',{'instance':snapshot['instance'],'event':event})['status'],'pausing')
        fresh = copy.deepcopy(snapshot); fresh['sequence'] = 1
        fresh['events'].append({**event,'method':'item/completed'})
        self.assertEqual(self.invoke('lifecycle-state',fresh)['status'],'pausing')
        duplicate = self.invoke('review-activity',old)
        self.assertEqual(duplicate['status'],'pausing')
        self.assertTrue(duplicate['acknowledged'])
        stopped = copy.deepcopy(old)
        stopped['receipt'].update(call_ref='fresh-old-stop',response_ref='fresh-old-stopped')
        self.assertEqual(self.invoke('review-activity',stopped)['status'],'paused')

    def test_descendant_lookup_rejects_incomplete_archive_or_source_coverage(self):
        self.begin()
        self.invoke('observe',self.observation())
        snapshot = self.observation('stopped','result')['lifecycle']
        for mutation in ('missing-archive','filtered-source','filtered-provider','filtered-cwd','moved-between-partitions','missing-page'):
            incomplete = copy.deepcopy(snapshot)
            if mutation == 'missing-archive':
                incomplete['pages'] = incomplete['pages'][:1]
            elif mutation == 'filtered-source':
                incomplete['pages'][0]['request']['params']['sourceKinds'] = ['appServer']
            elif mutation == 'filtered-provider':
                incomplete['pages'][0]['request']['params']['modelProviders'] = ['one-provider']
            elif mutation == 'filtered-cwd':
                incomplete['pages'][0]['request']['params']['cwd'] = str(self.root)
            elif mutation == 'moved-between-partitions':
                incomplete['pages'][1]['response']['data'] = copy.deepcopy(incomplete['pages'][0]['response']['data'])
            else:
                incomplete['pages'][0]['response']['nextCursor'] = 'missing-page'
            result = self.invoke('lifecycle-state',incomplete)
            self.assertEqual(result['status'],'blocked',mutation)
            self.assertEqual(result['error']['code'],'host_evidence_missing',mutation)
            self.assertIsNone(self.state().get('lifecycle_snapshot'))

    def test_stop_barrier_includes_both_review_axes_and_unknown_descendants(self):
        self.begin_dispatcher()
        (self.flow / 'impl.py').write_text('implemented = True\n')
        self.flow_git('add','impl.py'); self.flow_git('commit','-qm','candidate')
        candidate = self.flow_git('rev-parse','HEAD')
        verification = self.mark_reviewable(candidate)
        slots = {}
        for axis in ('standards','spec'):
            prepare = {'operation':'prepare','axis':axis,'candidate':candidate,'actor_ref':'task',
                       'verification':verification}
            first = self.invoke('review-activity',prepare)
            slots[axis] = first['next_action']['action_id']
            duplicate = self.invoke('review-activity',prepare)
            self.assertEqual(duplicate['next_action']['operation'],'lookup-exact-review')
            self.assertEqual(duplicate['next_action']['action_id'],slots[axis])
            self.invoke('review-activity',{'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task',
                'action_id':slots[axis], 'receipt':{'adapter':'fixture','call_ref':'spawn-'+axis,'response_ref':'created-'+axis,
                'ref':'native:'+axis,'status':'running','raw':{'task_name':'native:'+axis,'status':'running'}}})
        self.invoke('pause')
        self.invoke('observe',self.observation('stopped','result',ref='native:dispatcher'))
        self.assertEqual(self.state()['status'],'pausing')
        for axis in ('standards','spec'):
            self.invoke('review-activity',{'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task',
                'action_id':slots[axis], 'receipt':{'adapter':'fixture','call_ref':'wait-'+axis,'response_ref':'stopped-'+axis,
                'ref':'native:'+axis,'status':'stopped','raw':{'task_name':'native:'+axis,'status':'completed'}}})
        snapshot = self.observation('stopped','result',ref='native:dispatcher')['lifecycle']
        snapshot['pages'][0]['response']['data'].append({'id':'unreconciled-grandchild','status':{'type':'idle'}})
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'pausing')
        snapshot['pages'][0]['response']['data'].pop()
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'paused')
        self.assertTrue(self.state()['stopped'])

    def test_late_spawn_revokes_complete_partition_snapshot(self):
        self.begin()
        self.invoke('observe',self.observation())
        self.invoke('pause')
        saved = progress.read_record(self.checkpoint)
        saved['transport'] = {'instance':'fixture-host','state':'live','native_event_sequence':0}
        progress.atomic_save(self.checkpoint,saved)
        self.assertEqual(self.invoke('observe',self.observation('stopped','result'))['status'],'paused')
        original = copy.deepcopy(self.state()['lifecycle_snapshot'])
        omitted = copy.deepcopy(original)
        omitted['pages'][0]['response']['data'] = []
        self.assertEqual(self.invoke('lifecycle-state',omitted)['status'],'pausing')
        self.assertEqual(self.invoke('lifecycle-state',original)['status'],'paused')
        saved = progress.read_record(self.checkpoint)
        saved['transport']['native_event_sequence'] = 1
        progress.atomic_save(self.checkpoint,saved)
        event = {'method':'item/started','params':{'threadId':self.state()['lifecycle_snapshot']['carrier_thread'],
            'turnId':'original-turn','item':{'type':'subAgentActivity','id':'late-call','kind':'started',
            'agentPath':'native:late-child','agentThreadId':'late-child-thread'}}}
        self.assertEqual(self.invoke('host-event',{'instance':'fixture-host','event':event})['status'],'pausing')
        snapshot = copy.deepcopy(self.state()['lifecycle_snapshot'])
        snapshot.update(sequence=1,events=[event])
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'pausing')
        self.assertFalse(self.state()['stopped'])

    def test_failed_lookup_cannot_reuse_prior_complete_snapshot(self):
        self.begin()
        self.invoke('observe',self.observation())
        self.invoke('pause')
        saved = progress.read_record(self.checkpoint)
        saved['transport'] = {'instance':'fixture-host','state':'live','native_event_sequence':0}
        progress.atomic_save(self.checkpoint,saved)
        self.assertEqual(self.invoke('observe',self.observation('stopped','result'))['status'],'paused')
        saved = progress.read_record(self.checkpoint)
        saved['transport']['lookup_blocked'] = 'original host lookup unavailable'
        progress.atomic_save(self.checkpoint,saved)
        self.assertEqual(self.invoke('advance')['status'],'pausing')
        self.assertFalse(self.state()['stopped'])

    def paused_runner_cancel(self, saved_answer, recovery=None, stage=2):
        answer = self.pending_business_answer(stage)
        state = runner._new_state({})
        state["current_stage"] = "stage" + str(stage)
        state["launch"].update(state="completed_turn", turn=1)
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
        if saved_answer:
            self.invoke("decide", answer)
        self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher" if stage == 3 else "native:designer"))
        with redirect_stdout(io.StringIO()):
            runner._advance(state, self.checkpoint)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "paused")
        stopped = copy.deepcopy(self.state()["observations"])
        action = copy.deepcopy(self.state()["action"])
        with patch.object(runner, "_invoke", side_effect=AssertionError("carrier reissued")), redirect_stdout(io.StringIO()):
            if recovery:
                # A crash after the runner stop request is durable, before C intake.
                outer = progress.read_record(self.checkpoint)
                outer["runner_request"] = {"operation": "cancel", "request_id": "durable-cancel"}
                progress.atomic_save(self.checkpoint, outer)
                self.invoke(recovery)
            else:
                runner.request_control(self.checkpoint, "cancel")
            self.assertEqual(self.state()["status"], "cancelled")
            for operation in ("cancel", "cancel", "pause"):
                runner.request_control(self.checkpoint, operation)
                for shared_operation in ("advance", "resume", "pause"):
                    result = self.invoke(shared_operation)
                    self.assertEqual(result["status"], "cancelled")
                    self.assertIsNone(result["next_action"])
                runner._advance(state, self.checkpoint)
            # A fully stopped cancellation is a read-only acknowledgement,
            # never permission to resume the cancelled carrier.
            before_ack = self.checkpoint.read_bytes()
            with patch.object(runner, "validate_confirmed", return_value={}):
                self.assertEqual(runner.resume(self.checkpoint), 0)
            self.assertEqual(self.checkpoint.read_bytes(), before_ack)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "cancelled")
        self.assertEqual(self.state()["stop_requested"], "cancelling")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["state"], "cancelled")
        self.assertTrue(self.state()["stopped"])
        self.assertEqual(self.state()["observations"], stopped)
        self.assertEqual(self.state()["action"], action)
        if saved_answer:
            self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]],
                             {"decision": answer, "disposition": "cancelled"})
            self.assertTrue(self.invoke("decide", answer)["acknowledged"])
        else:
            self.assertTrue(self.invoke("decide", answer)["decision_deferred"])
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))
        self.assertEqual(self.state()["status"], "cancelled")

    def test_runner_cancel_upgrades_stopped_pause_without_answer(self):
        self.paused_runner_cancel(False)

    def test_runner_cancel_upgrades_stopped_pause_with_answer(self):
        self.paused_runner_cancel(True)

    def test_advance_recovers_saved_cancel_over_stopped_pause(self):
        self.paused_runner_cancel(True, "advance")

    def test_resume_recovers_saved_cancel_over_stopped_pause(self):
        self.paused_runner_cancel(False, "resume")

    def test_dispatcher_runner_cancel_upgrades_stopped_pause(self):
        self.paused_runner_cancel(True, stage=3)

    def test_dispatcher_resume_recovers_saved_cancel_over_stopped_pause(self):
        self.paused_runner_cancel(False, "resume", stage=3)

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
        self.assertEqual(self.invoke("pause")["status"], "paused")
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

    def mark_reviewable(self, candidate):
        current = self.state()['control']['context']
        if current['handoff_progress'].get('candidate') is not None:
            self.invoke('control', {'action':'invalidate-candidate',
                'evidence':{'candidate':current['handoff_progress']['candidate'],'reference':'fixture:remediation','reason':'replacement'},
                'receipt':{'adapter':'fixture','call_ref':'remediate','response_ref':'remediation','raw':{'decision':'fix'}}})
        outer = progress.read_record(self.checkpoint)
        self.fixture_accepted_execution(outer[progress.KEY]['control']['context'], candidate)
        progress.atomic_save(self.checkpoint, outer)
        self.invoke('observe', self.observation('idle','result',ref='native:dispatcher'))
        current = self.state()['control']['context']
        plan = current['handoff_progress']['validation_plan']
        checks = transfer.checks_for(plan['review_required'],candidate)
        result = self.invoke('control', {'action':'candidate-ready', 'evidence':{
            'dispatcher_ref':'native:dispatcher','attempt':current['carrier']['attempt'],'commit':candidate,
            'expected_target_head':self.git('rev-parse','main'),'binding':self.binding,
            'plan_digest':transfer.control.digest(plan),'checks':checks},
            'receipt':{'adapter':'fixture','call_ref':'focused','response_ref':'focused-result','raw':{'checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'reviewable',result)
        return {'candidate':candidate,'plan_digest':transfer.control.digest(plan),'checks':checks}

    def accept_current(self, payload, role):
        if self.state()['stage'] == 3:
            verification = self.mark_reviewable(payload['candidate_commit'])
            actor = self.state()['handoff']['expected_entry']['actor']['thread_id']
            for axis in ('standards','spec'):
                prepared = self.invoke('review-activity', {'operation':'prepare','axis':axis,'candidate':payload['candidate_commit'],
                    'actor_ref':actor,'verification':verification})
                self.assertEqual(prepared['next_action']['operation'],'invoke-review',prepared)
                ref = payload['review'][axis]['reviewer_ref']
                self.invoke('review-activity',{'operation':'observe','axis':axis,'candidate':payload['candidate_commit'],
                    'actor_ref':actor,'action_id':prepared['next_action']['action_id'],
                    'receipt':{'adapter':'fixture','call_ref':'review-'+axis,'response_ref':'terminal-'+axis,
                               'ref':ref,'status':'stopped','raw':{'task_name':ref,'status':'completed'}}})
            candidate = payload['candidate_commit']
            checkpoint = self.state()['control']['context']['handoff_progress']
            review = {axis:{'candidate':candidate,'expected_target_head':checkpoint['expected_target_head'],
                      'plan_digest':checkpoint['plan_digest'],'reviewer_ref':payload['review'][axis]['reviewer_ref'],
                      'status':'accepted','result_ref':'terminal-'+axis} for axis in ('standards','spec')}
            response = self.invoke('control', {'action':'review-converged',
                'evidence':{'candidate':candidate,'review':review,'reference':'fixture:converged'},
                'receipt':{'adapter':'fixture','call_ref':'review','response_ref':'review-converged','raw':{'review':review}}})
            checkpoint = self.state()['control']['context']['handoff_progress']
            self.assertEqual(checkpoint['state'],'final-validation-pending',response)
            context = self.state()['control']['context']
            start = {'attempt_id':'fixture-final','dispatcher_ref':role,'carrier_attempt':context['carrier']['attempt'],
                     **{key:checkpoint[key] for key in ('candidate','expected_target_head','plan_digest','review_digest')}}
            response = self.invoke('control', {'action':'validation-start','evidence':start,
                'receipt':{'adapter':'fixture','call_ref':'final','response_ref':'final-start','raw':{'decision':'run'}}})
            self.assertEqual(response['next_action']['operation'],'continue-host',response)
            checks = transfer.checks_for(checkpoint['validation_plan']['final_required'], candidate)
            response = self.invoke('control', {'action':'validation-result','evidence':{'attempt_id':'fixture-final','checks':checks},
                'receipt':{'adapter':'fixture','call_ref':'command','response_ref':'command-result',
                           'raw':{'attempt_id':'fixture-final','checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':role}}})
            checkpoint = self.state()['control']['context']['handoff_progress']
            self.assertEqual(checkpoint['state'],'deliverable',response)
            payload.update(review=review, verification=transfer.control.delivery_verification(checkpoint,role))
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
            self.ready_publication(candidate, "controller:closure", role)
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

        def carrier(state, path, answer, continuing, host=None):
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
            "controller_ref": "task", "flow_mode": "stepwise", "registry_input": str(self.checkpoint),
            "frozen_requirement": {**self.frozen, "path": self.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        state.update(status="needs_input", pending_input=pending, sessions={"stage2": "carrier"},
                     launch={"stage": "stage2", "state": "completed_turn", "turn": 1})
        runner._atomic_save(self.checkpoint, state)
        self.settings["thread_id"] = "task"

        def resume_carrier(state, record, answer, continuing, host=None):
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
            # A retained carrier belongs to the live owner. Concurrent resume
            # queues the exact decision; only that owner delivers it to C.
            with runner.RunLock(self.checkpoint):
                self.assertEqual(runner.resume(self.checkpoint, "accept", decision_id=pending["decision_id"]), 0)
                self.assertEqual(invoked.call_count, 0)
                self.assertTrue(runner._live_commands(state, self.checkpoint))
                self.assertEqual(runner._advance_in_host(state, self.checkpoint, "accept", object()), 0)
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

    def test_legacy_replacement_cannot_start_a_control_transaction(self):
        self.finish_design("continuous")
        self.context = self.context_for(3)
        self.begin("continuous", self.input_for(3, self.state()["accepted"]))
        self.invoke("observe", self.observation(ref="native:dispatcher"))
        request = {"action": "recover-dispatch", "evidence": {"stopped_refs": ["native:dispatcher"],
            "file_hashes": {}, "replacement_ref": "native:replacement"},
            "receipt": {"host": "fixture", "stopped": "native:dispatcher", "ready": "native:replacement"}}
        result = self.invoke("control", request)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.state()["control"]["context"]["carrier"]["ref"], "native:dispatcher")
        self.assertFalse(progress.Progress(self.checkpoint, progress.read_record(self.checkpoint)).unresolved_control_transactions())

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
                "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["fixture boundary passed"],
                "write_release": transfer.dispatch.execution_release_token(slot["record"])}}})
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
                "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["fixture boundary passed"],
                "write_release": transfer.dispatch.execution_release_token(slot["record"])}}})
        self.assertEqual(received["next_action"]["result"]["status"], "received", received)
        accepted = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "accept", "decision": {"reference": "dispatcher:accept"}})
        self.assertFalse(accepted["downstream_ready"])
        self.assertIsNone(self.state()["accepted"])
        self.assertEqual(self.state()["dispatch"]["request"]["role"], "implementation-dispatcher")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["executions"][0]["state"], "accepted")

    def test_public_control_cannot_bypass_execution_receipts(self):
        self.prepare_execution()
        slot = self.state()["allocations"]["slice-one"]
        self.invoke("allocation", {"allocation_id": "slice-one", "operation": "bind",
            "receipt": self.receipt(slot["record"], ref="native:executor")})
        before = self.state()["control"]["context"]
        for action in ("execution-dispatch-result", "execution-result", "accept-execution"):
            with self.subTest(action=action):
                blocked = self.invoke("control", {"action": action, "evidence": {},
                    "receipt": {"host": "fixture"}})
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(self.state()["error"]["code"], "invalid_operation")
                self.assertEqual(self.state()["control"]["context"], before)

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
        request = self.with_registration(request, project=self.flow, receipt='host:requirement-source-registry')
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
        self.ready_publication(candidate, "controller:close", "native:closure")
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
        facts = transfer.entry.repository_facts(str(project))
        discussion_project, _ = transfer.entry.resolve_discussion_project(facts, attachment)
        # A port-level test of C's source adapter. Phase and ledger are real;
        # full stage acceptance is exercised separately through A/B/Git above.
        state = {"protocol": progress.PROTOCOL, "revision": 0, "mode": "stepwise", "stage": 2,
            "status": "active", "step": "bound", "packages": {}, "accepted": None, "phase_complete": False,
            "host": {"generation": 0, "status": "unknown", "proof": None, "query": None, "seen": {}, "calls": {}, "last_stop": None},
            "transaction": None, "transaction_source": None, "transaction_result": None,
            "action": None, "handoff": {"stage": 2, "binding": None,
                "authorization": {"phase": {"run_id": prepared["phase_run_id"], "attempt_id": prepared["attempt_id"]}},
                "entry": {"source": {"kind": "discussion", "attachment": attachment}},
                "expected_entry": {"repository": facts, "discussion_project": discussion_project,
                                   "requirement": {"kind": "discussion", "attachment": attachment}}}}
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


class RunnerCheckpointMutationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkpoint = Path(temporary.name) / 'run.json'
        self.state = runner._new_state({})
        runner._atomic_save(self.checkpoint, self.state)

    def test_prior_transfer_runtime_cannot_mutate_checkpoint(self):
        records = []
        for protocol, schema in [('workflow-progress-v9', 3), ('workflow-progress-v10', 4)]:
            old_pin = {'compatibility_key': {'workflow_progress': protocol,
                                            'stage_transfer': 'workflow-stage-transfer-v6'}}
            records.extend([runner._new_state({'packages': {'runner': old_pin}}),
                            {progress.KEY: {'protocol': protocol,
                                            'control': {'context': {'schema_version': schema}}}}])
        for record in records:
            for operation in ('pause', 'cancel', 'resume', 'prepare-requirement',
                              'deliver-requirement', 'lifecycle'):
                with self.subTest(entry='progress', operation=operation, pinned='confirmed' in record):
                    progress.atomic_save(self.checkpoint, record)
                    before = self.checkpoint.read_bytes()
                    with self.assertRaisesRegex(progress.entry.PreparationError, 'original.*runtime'):
                        progress.handle(self.checkpoint, {'protocol': progress.PROTOCOL,
                            'operation': operation, 'expected_revision': 0, 'data': {}})
                    self.assertEqual(self.checkpoint.read_bytes(), before)
        for record in records[::2]:
            for live in (False, True):
                for operation in ('pause', 'cancel'):
                    with self.subTest(entry='runner', operation=operation, live=live):
                        progress.atomic_save(self.checkpoint, record)
                        before = self.checkpoint.read_bytes()
                        with self.assertRaisesRegex(runner.WorkflowError, 'legacy_run_requires_original_runtime'):
                            runner._request_control(self.checkpoint, operation, live=live)
                        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_runner_patch_preserves_newer_checkpoint_fields(self):
        newer = progress.read_record(self.checkpoint)
        newer['future_c_field'] = {'evidence': 'retained'}
        newer['transport'] = {'instance': 'original', 'state': 'live'}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'paused'
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['future_c_field'], {'evidence': 'retained'})
        self.assertEqual(saved['transport'], newer['transport'])
        self.assertEqual(self.state, saved)

    def test_runner_patch_combines_independent_nested_updates(self):
        self.state['confirmed'] = {'flow_mode': 'stepwise', 'registry_input': 'first'}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['confirmed']['registry_input'] = 'second'
        progress.atomic_save(self.checkpoint, newer)
        self.state['confirmed']['flow_mode'] = 'continuous'
        runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint)['confirmed'],
                         {'flow_mode': 'continuous', 'registry_input': 'second'})

    def test_runner_patch_retains_queued_decision_until_exact_consumption(self):
        decision = {'decision_id': 'choice', 'answer': 'yes'}
        newer = progress.read_record(self.checkpoint)
        newer['controller_decision'] = decision
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'paused'
        runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint)['controller_decision'], decision)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'decisions': {'choice': decision}, 'history': []}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'active'
        runner._atomic_save(self.checkpoint, self.state)
        self.assertNotIn('controller_decision', progress.read_record(self.checkpoint))

    def test_runner_patch_rejects_conflicting_status_change(self):
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'needs_input'
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'paused'
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_conflict: status'):
            runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint), newer)

    def test_original_host_request_keeps_pending_input_priority(self):
        host_pending = {'kind': 'host-request', 'decision_id': 'host'}
        newer = progress.read_record(self.checkpoint)
        newer['transport'] = {'server_requests': {'host': {'id': 'host'}}}
        newer['pending_input'] = host_pending
        progress.atomic_save(self.checkpoint, newer)
        self.state['pending_input'] = {'kind': 'user-decision', 'decision_id': 'business'}
        runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint)['pending_input'], host_pending)
        self.assertEqual(self.state['pending_input'], host_pending)

    def test_cancel_dominates_new_host_request_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'needs_input'
        newer['pending_input'] = {'kind': 'host-request', 'decision_id': 'host'}
        newer['transport'] = {'server_requests': {'host': {'id': 'host'}}}
        newer['runner_request'] = {'operation': 'cancel', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, newer)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(self.state, self.checkpoint), 0)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'cancelling')
        self.assertEqual(saved['pending_input'], newer['pending_input'])
        self.assertEqual(saved['runner_request'], newer['runner_request'])

    def test_pause_waits_despite_new_host_request_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'needs_input'
        newer['pending_input'] = {'kind': 'host-request', 'decision_id': 'host'}
        newer['transport'] = {'server_requests': {'host': {'id': 'host'}}}
        newer['runner_request'] = {'operation': 'pause', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, newer)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(self.state, self.checkpoint), 0)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'pausing')
        self.assertEqual(saved['pending_input'], newer['pending_input'])
        self.assertEqual(saved['runner_request'], newer['runner_request'])

    def test_late_cancel_supersedes_newer_paused_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'paused'
        newer['runner_request'] = {'operation': 'cancel', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'cancelling'
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'cancelling')
        self.assertEqual(saved['runner_request'], newer['runner_request'])

    def test_stale_pause_cannot_downgrade_cancelling_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'cancelling'
        newer['runner_request'] = {'operation': 'pause', 'request_id': 'stale-pause'}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'pausing'
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_conflict: status'):
            runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint), newer)

    def test_runner_patch_does_not_recreate_a_missing_checkpoint(self):
        self.checkpoint.unlink()
        self.state['status'] = 'paused'
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_missing'):
            runner._atomic_save(self.checkpoint, self.state)
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_missing'):
            self.state.refresh(progress.read_record(self.checkpoint))
        self.assertFalse(self.checkpoint.exists())

    def test_launch_receipt_survives_checkpoint_write_failure(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)

        class Host:
            instance = 'original-host'
            process = None

            def start(self, diagnostics):
                self.process = object()

            def start_carrier(self, stage, settings):
                return 'original-carrier'

            def run_turn(self, *args):
                raise AssertionError('the uncertain launch must not issue a turn')

        original_save = runner._atomic_save

        def fail_launched_save(path, state, **options):
            if state['launch']['state'] == 'launched':
                raise OSError('checkpoint write failed')
            return original_save(path, state, **options)

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_atomic_save', side_effect=fail_launched_save):
            with self.assertRaisesRegex(runner.CheckpointPendingError, 'reconcile the original launch'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())
        saved = progress.read_record(self.checkpoint)
        receipt_path = runner._carrier_receipt_path(self.checkpoint, 'stage2', 1)
        receipt = progress.read_record(receipt_path)
        self.assertEqual(saved['launch']['state'], 'spawning')
        self.assertEqual(receipt['events'], [{'type': 'thread.started', 'thread_id': 'original-carrier'}])
        recovered = runner.CheckpointState(saved)
        with self.assertRaisesRegex(runner.WorkflowError, 'carrier outcome unresolved'):
            runner._recover_carrier_receipt(recovered, self.checkpoint)
        self.assertEqual(progress.read_record(self.checkpoint)['sessions']['stage2'], 'original-carrier')

    def test_queued_stop_before_launch_intent_prevents_new_carrier(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        queued = progress.read_record(self.checkpoint)
        queued['runner_request'] = {'operation': 'cancel', 'request_id': 'queued-before-launch'}
        queued['status'] = 'cancelled'
        progress.atomic_save(self.checkpoint, queued)

        class Host:
            instance = 'original-host'
            process = None
            started = False

            def start(self, diagnostics):
                self.started = True

            def start_carrier(self, stage, settings):
                raise AssertionError('a queued stop must prevent carrier creation')

        host = Host()
        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, host)
        self.assertFalse(host.started)
        self.assertEqual(progress.read_record(self.checkpoint), queued)

    def test_accepted_stage_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'accepted', 'phase_complete': True}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('accepted stage must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_pending_decision_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'needs_input', 'pending': {'decision_id': 'choice'}}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('pending decision must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_business_block_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'active',
                               'business_block': {'id': 'original-block'}}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('business block must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_blocked_progress_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'blocked'}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('blocked progress must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_consumed_old_decision_cannot_answer_new_pending_input(self):
        old_decision = {'decision_id': 'old', 'subject': {'stage': 2}, 'answer': 'first',
                        'reference': 'controller-answer:old'}
        pending = {'kind': 'user-decision', 'decision_id': 'new', 'subject': {'stage': 2},
                   'question': 'Choose again'}
        self.state['controller_decision'] = old_decision
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        saved[progress.KEY] = {'stage': 2, 'status': 'needs_input', 'pending': pending,
                               'decisions': {'old': old_decision}, 'history': []}
        progress.atomic_save(self.checkpoint, saved)
        with patch.object(runner, '_invoke', side_effect=AssertionError('new question needs an answer')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, object()), 0)
        latest = progress.read_record(self.checkpoint)
        self.assertEqual(latest['pending_input'], pending)
        self.assertNotIn('controller_decision', latest)

    def test_control_turn_can_use_existing_carrier_after_stop_is_queued(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        runner._atomic_save(self.checkpoint, self.state)
        queued = progress.read_record(self.checkpoint)
        queued['runner_request'] = {'operation': 'pause', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, queued)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                return 'original-carrier'

            def run_turn(self, *args):
                raise AssertionError('control turn reached the original carrier')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='stop prompt'):
            with self.assertRaisesRegex(AssertionError, 'control turn reached the original carrier'):
                runner._invoke(self.state, self.checkpoint, None, True, Host(), control_turn=True)

    def test_normal_turn_rechecks_stop_queued_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)

        class Host:
            instance = 'original-host'
            process = None
            started = False

            def start(self, diagnostics):
                self.started = True

            def start_carrier(self, stage, settings):
                raise AssertionError('a queued stop must prevent carrier creation')

        host = Host()
        original_invoke = runner._invoke

        def queue_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                queued = progress.read_record(self.checkpoint)
                queued['runner_request'] = {'operation': 'cancel', 'request_id': 'queued-after-read'}
                queued['status'] = 'cancelled'
                progress.atomic_save(self.checkpoint, queued)
            return original_invoke(*args, **kwargs)

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=queue_before_issue), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, host), 0)
        self.assertFalse(host.started)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'cancelled')
        self.assertEqual(saved['runner_request']['request_id'], 'queued-after-read')

    def test_normal_turn_rechecks_decision_queued_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        pending = {'kind': 'user-decision', 'decision_id': 'choice', 'subject': {'stage': 2},
                   'question': 'Choose next step'}
        original_invoke = runner._invoke

        def queue_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                newer = progress.read_record(self.checkpoint)
                newer[progress.KEY] = {'stage': 2, 'status': 'needs_input', 'pending': pending}
                progress.atomic_save(self.checkpoint, newer)
            return original_invoke(*args, **kwargs)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('pending decision must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=queue_before_issue), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, Host()), 0)
        self.assertEqual(progress.read_record(self.checkpoint)['pending_input'], pending)

    def test_normal_turn_rechecks_acceptance_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        original_invoke = runner._invoke

        def accept_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                newer = progress.read_record(self.checkpoint)
                newer[progress.KEY] = {'stage': 2, 'status': 'accepted', 'phase_complete': True,
                                       'accepted': {'digest': 'accepted'}}
                progress.atomic_save(self.checkpoint, newer)
            return original_invoke(*args, **kwargs)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('accepted stage must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=accept_before_issue), \
                patch.object(runner.stage_handoff, 'render',
                             side_effect=AssertionError('accepted projection reached')):
            with self.assertRaisesRegex(AssertionError, 'accepted projection reached'):
                runner._advance_in_host(self.state, self.checkpoint, None, Host())

    def test_normal_turn_rechecks_business_block_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        original_invoke = runner._invoke

        def block_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                newer = progress.read_record(self.checkpoint)
                newer[progress.KEY] = {'stage': 2, 'status': 'active',
                                       'business_block': {'id': 'original-block'}}
                progress.atomic_save(self.checkpoint, newer)
            return original_invoke(*args, **kwargs)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('business block must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=block_before_issue), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, Host()), 1)
        latest = progress.read_record(self.checkpoint)
        self.assertEqual(latest['status'], 'failed')
        self.assertEqual(latest['error']['business_block_id'], 'original-block')

    def test_prior_stage_stop_does_not_block_next_stage_launch(self):
        self.state['current_stage'] = 'stage3'
        self.state['launch'] = {'stage': 'stage3', 'state': 'prelaunch', 'turn': 0}
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage3': {}},
                                   'stages': {'stage3': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        saved[progress.KEY] = {'stage': 2, 'status': 'accepted', 'stop_requested': 'pausing'}
        progress.atomic_save(self.checkpoint, saved)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('next-stage launch was reached')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(AssertionError, 'next-stage launch was reached'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_noncreation_outcome_is_not_a_user_stop_request(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        saved[progress.KEY] = {'stage': 2, 'status': 'cancelled', 'stopped': True}
        progress.atomic_save(self.checkpoint, saved)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('noncreation path reached the carrier')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(AssertionError, 'noncreation path reached the carrier'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_saved_resume_can_reenter_paused_original_carrier(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        self.state['resume_progression'] = True
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        saved[progress.KEY] = {'stage': 2, 'status': 'paused', 'stop_requested': 'pausing'}
        progress.atomic_save(self.checkpoint, saved)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('resume reached the original carrier')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='resume prompt'):
            with self.assertRaisesRegex(AssertionError, 'resume reached the original carrier'):
                runner._invoke(self.state, self.checkpoint, None, True, Host())

    def test_new_pause_after_saved_resume_prevents_normal_turn(self):
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        self.state['resume_progression'] = True
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        saved[progress.KEY] = {'stage': 2, 'status': 'pausing', 'stop_requested': 'pausing'}
        progress.atomic_save(self.checkpoint, saved)
        with patch.object(runner, '_invoke', side_effect=AssertionError('normal turn must not start')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, object()), 0)
        self.assertEqual(progress.read_record(self.checkpoint)['status'], 'pausing')

    def test_cancellation_upgrades_saved_pause_resume(self):
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        self.state['resume_progression'] = True
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        saved[progress.KEY] = {'stage': 2, 'status': 'paused', 'stop_requested': 'cancelling'}
        progress.atomic_save(self.checkpoint, saved)
        with patch.object(runner, '_invoke', side_effect=AssertionError('normal turn must not start')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, object()), 0)
        self.assertEqual(progress.read_record(self.checkpoint)['status'], 'cancelling')

    def test_unhandled_launch_deferral_does_not_spin(self):
        blocked = runner.LaunchDeferred('launch_deferred')
        with patch.object(runner, '_invoke', side_effect=[blocked, blocked,
                                                          AssertionError('unexpected third retry')]) as invoked:
            with self.assertRaisesRegex(runner.WorkflowError, 'did not reach a ready branch'):
                runner._advance_in_host(self.state, self.checkpoint, None, object())
        self.assertEqual(invoked.call_count, 2)


def load_tests(loader, tests, pattern):
    # B's tests run in their own module; inherit only its real fixture helpers.
    suite = unittest.TestSuite(ProgressTests(name) for name in loader.getTestCaseNames(ProgressTests) if name in ProgressTests.__dict__)
    for cls in (AttachedProgressTests, NonGitProgressTests):
        suite.addTests(cls(name) for name in loader.getTestCaseNames(cls) if name in AttachedProgressTests.__dict__)
    suite.addTests(loader.loadTestsFromTestCase(PhaseProgressTests))
    suite.addTests(loader.loadTestsFromTestCase(RunnerCheckpointMutationTests))
    return suite
