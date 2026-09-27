"""Attached legacy takeover uses the existing Topic workflow-control transaction."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/shared/scripts'))

import discussion_protocol as discussion
import workflow_control as control
import workflow_progress as progress
from discussion_core.state import _load_records, _render_records_ledger
from test_discussion_protocol import DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport
import test_workflow_control as control_fixtures
import test_stage_scope_legacy as historical


class AttachedLegacyScopeRecovery(DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def evolution_request(self, topic, **kwargs):
        kwargs.setdefault('owner_ref', self.owner)
        return super().evolution_request(topic, **kwargs)

    def setUp(self):
        super().setUp()
        self.project = self.make_project('legacy-topic', git=False)
        self.topic = self.bootstrap_topic(self.project, owner_ref='task')
        self.ledger = Path(self.topic['ledger_path'])
        self.owner = 'task'
        self.fixture = control_fixtures.WorkflowControlTests(methodName='runTest')
        topic_path = Path(self.topic['topic_document_path'])
        requirement = {'path': topic_path.relative_to(self.project).as_posix(),
                       'sha256': hashlib.sha256(topic_path.read_bytes()).hexdigest(), 'version': 1}
        binding = self.fixture.binding()
        context = self.fixture.context()
        context.update(controller_ref=self.owner, topic_ref=self.topic['topic_id'], stage=2,
                       requirement_identity=requirement)
        start = control.transition({'schema_version': 5, 'action': 'start-design',
            'actor_ref': self.owner, 'context': context,
            'evidence': {'design_input': 'accepted-design-input', 'binding': binding,
                'allowed_paths': ['docs/spec.md'], 'protected_paths': [requirement['path']],
                'configuration': self.fixture.configuration('solution-designer')}})
        context = start['context']
        for action, evidence in (
                ('designer-bound', {'ref': 'native:designer', 'attempt': context['carrier']['attempt']}),
                ('design-result', {'ref': 'native:designer', 'result_digest': 'a' * 64}),
                ('accept-design', {'ref': 'native:designer', 'result_digest': 'a' * 64})):
            context = control.transition({'schema_version': 5, 'action': action,
                'actor_ref': self.owner, 'context': context, 'evidence': evidence})['context']
        self.old = copy.deepcopy(context)
        self.old['schema_version'] = 4
        del self.old['handoff_progress']['binding']['lifecycle']
        self.raw_old = json.dumps(self.old, ensure_ascii=False, separators=(', ', ': '))
        binding = copy.deepcopy(binding)
        binding['lifecycle'] = {'origin': 'reused', 'manager': 'git',
                                'disposition': 'retain', 'evidence_ref': 'controller:verified-workspace',
                                'creation_receipt': None}
        context['handoff_progress']['binding'] = binding
        evidence = {'proposal_id': 'legacy-scope-proposal', 'proposal_digest': 'b' * 64,
            'expected_scope_revision': 0, 'candidate_commit': 'c' * 40,
            'manifest_digest': 'd' * 64, 'requirement_identity': requirement,
            'binding': binding, 'prior_authorization_ref': 'controller:old-scope',
            'decision': {'kind': 'in-requirement-refinement', 'reference': 'controller:supplement'},
            'target_head': 'e' * 40, 'basis_blobs': {'docs/spec.md': 'f' * 64}}
        self.new = control.transition({'schema_version': 5, 'action': 'scope-seal',
            'actor_ref': self.owner, 'context': context, 'evidence': evidence})['context']
        self.metadata = {'state': 'pending', 'recovery_id': 'recovery-1',
            'checkpoint_path': str(self.root / 'original-checkpoint.json'),
            'original_checkpoint_sha256': '1' * 64, 'original_accepted_digest': '2' * 64,
            'old_control_sha256': hashlib.sha256(self.raw_old.encode()).hexdigest(),
            'takeover_transaction_id': 'transaction-1'}
        self.new['legacy_takeover'] = copy.deepcopy(self.metadata)
        frontmatter, records = _load_records(self.ledger)
        topic_record = next(item for item in records['Current Topics'] if item['topic_id'] == self.topic['topic_id'])
        topic_record.update(current_phase=2, phase_state='active')
        records['Phase Results'].append({'result_id': 'WC-' + self.topic['topic_id'],
            'result_kind': 'workflow-control', 'topic_id': self.topic['topic_id'],
            'record_revision': 1, 'state': 'active', 'data_json': self.raw_old})
        self.ledger.write_bytes(_render_records_ledger(frontmatter, records))

    def request_recovery(self, new=None, *, expected=None, key=None):
        if expected is None:
            expected = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        metadata = self.metadata
        evidence = {field: metadata[field] for field in (
            'checkpoint_path', 'original_checkpoint_sha256', 'original_accepted_digest',
            'recovery_id', 'old_control_sha256', 'takeover_transaction_id')}
        evidence['controller_ref'] = self.owner
        evidence['new_control_context'] = copy.deepcopy(self.new if new is None else new)
        request = self.evolution_request(self.topic, operation='workflow-control',
            expected_revision=expected['ledger_revision'],
            expected_topic_revision=expected['record_revision'],
            action='scope-recover-legacy', evidence=evidence)
        if key is not None:
            request['idempotency_key'] = key
        return request

    def phase3_intent(self):
        run_id, attempt_id = 'PR-00000099', 'PA-00000099-1'
        data = {'run_id': run_id, 'run_kind': 'phase-run', 'record_revision': 1,
            'state': 'prepared', 'from_phase': 2, 'to_phase': 3, 'route': [2, 3],
            'carrier_kind': 'current-topic', 'source_topic_id': self.topic['topic_id'],
            'evidence': {field: 'a' * 64 for field in
                ('source', 'route', 'impact', 'coverage', 'dependency', 'coordination')},
            'attempts': [{'attempt_id': attempt_id, 'attempt_number': 1,
                'state': 'setup-pending', 'authorization': False, 'carrier_ref': None,
                'reason': None}], 'creation_idempotency_key': str(uuid.uuid4())}
        frontmatter, records = _load_records(self.ledger)
        records['Phase Runs'].append({'run_id': run_id, 'run_kind': 'phase-run',
            'state': 'prepared', 'record_revision': 1,
            'data_json': json.dumps(data, sort_keys=True, separators=(',', ':'))})
        self.ledger.write_bytes(_render_records_ledger(frontmatter, records))
        return run_id, attempt_id

    def reserve_request(self, run_id, attempt_id, *, expected=None, reservation_id='reservation-1'):
        if expected is None:
            expected = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        return self.evolution_request(self.topic, operation='workflow-control',
            expected_revision=expected['ledger_revision'],
            expected_topic_revision=expected['record_revision'],
            action='scope-recover-legacy-reserve', evidence={
                'recovery_id': self.metadata['recovery_id'],
                'checkpoint_path': self.metadata['checkpoint_path'],
                'original_checkpoint_sha256': self.metadata['original_checkpoint_sha256'],
                'bridge_digest': 'b' * 64,
                'phase_run_id': run_id, 'phase_attempt_id': attempt_id,
                'controller_ref': self.owner, 'reservation_id': reservation_id})

    def supersede_request(self, *, expected=None, transaction_id='transaction-2'):
        if expected is None:
            expected = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        prior = expected['workflow_control']
        new_context = copy.deepcopy(self.new)
        del new_context['legacy_takeover']
        del new_context['handoff_progress']['scope_seal']
        seal_evidence = copy.deepcopy(prior['handoff_progress']['scope_seal']['evidence'])
        seal_evidence.update(proposal_id='superseding-scope-' + transaction_id,
                             proposal_digest=control.digest(['proposal', transaction_id]),
                             manifest_digest=control.digest(['manifest', transaction_id]))
        new_context = control.transition({'schema_version': 5, 'action': 'scope-seal',
            'actor_ref': self.owner, 'context': new_context,
            'evidence': seal_evidence})['context']
        new_context['legacy_takeover'] = {**self.metadata,
                                           'takeover_transaction_id': transaction_id,
                                           'state': 'pending'}
        evidence = {field: self.metadata[field] for field in (
            'checkpoint_path', 'original_checkpoint_sha256', 'original_accepted_digest',
            'recovery_id', 'old_control_sha256')}
        evidence.update(controller_ref=self.owner, takeover_transaction_id=transaction_id,
                        new_control_context=new_context,
                        previous_takeover_transaction_id=prior['legacy_takeover']['takeover_transaction_id'],
                        previous_scope_seal_ref=prior['handoff_progress']['scope_seal']['seal_ref'])
        return self.evolution_request(self.topic, operation='workflow-control',
            expected_revision=expected['ledger_revision'],
            expected_topic_revision=expected['record_revision'],
            action='scope-recover-legacy-supersede', evidence=evidence)

    def attached_progress_fixture(self):
        """Join real old Git/C provenance to this disposable Topic transaction."""
        scratch = self.root / 'historical-fixture'
        scratch.mkdir()
        scenario = historical.LegacyScopeTests(methodName='runTest')
        scenario.scratch = scratch
        scenario.metadata = historical.historical_fixture(historical.OLD_REVISION, scratch)
        scenario.checkpoint = Path(scenario.metadata['checkpoint'])
        scenario.original_bytes = scenario.checkpoint.read_bytes()
        scenario.original = json.loads(scenario.original_bytes)
        inputs, _, original_record = scenario.authorized_standalone()
        self.metadata.update(recovery_id=original_record['recovery_id'],
            checkpoint_path=str(scenario.checkpoint),
            original_checkpoint_sha256=original_record['original_checkpoint_sha256'],
            original_accepted_digest=original_record['original_accepted_digest'])
        self.new['legacy_takeover'] = copy.deepcopy(self.metadata)
        discussion.handle(self.request_recovery())
        active = copy.deepcopy(self.new)
        active['legacy_takeover']['state'] = 'active'
        discussion.handle(self.request_recovery(active))
        run_id, attempt_id = self.phase3_intent()
        topic = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        outer = progress.read_record(scenario.checkpoint)
        state = outer[progress.KEY]
        state['control'] = {'context': topic['workflow_control'],
            'discussion': self.evolution_request(self.topic, operation='workflow-control',
                expected_revision=topic['ledger_revision'],
                expected_topic_revision=topic['record_revision'])}
        record = state['scope_recoveries'][original_record['recovery_id']]
        record['ledger'] = {'reservation_request': None, 'reservation_receipt': None}
        state['revision'] += 1
        progress.atomic_save(scenario.checkpoint, outer)
        start = scenario.recovered_stage_three_start(record, inputs)
        start['expected_revision'] = state['revision']
        start['data']['handoff']['authorization']['phase'] = {'run_id': run_id,
                                                               'attempt_id': attempt_id}
        return scenario, record, start

    def test_pending_then_active_preserves_exact_old_control_and_one_current(self):
        pending_request = self.request_recovery()
        pending = discussion.handle(pending_request)
        self.assertEqual(pending['state'], 'takeover-pending')
        self.assertEqual(discussion.handle(pending_request)['idempotent_replay'], True)
        frontmatter, records = _load_records(self.ledger)
        controls = [item for item in records['Phase Results'] if item.get('result_kind') == 'workflow-control']
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]['legacy_control_json'], self.raw_old)
        self.assertEqual(hashlib.sha256(controls[0]['legacy_control_json'].encode()).hexdigest(),
                         self.metadata['old_control_sha256'])
        read = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        self.assertEqual(read['workflow_control']['legacy_takeover']['state'], 'pending')
        active = copy.deepcopy(self.new)
        active['legacy_takeover']['state'] = 'active'
        activated = discussion.handle(self.request_recovery(active, expected=read))
        self.assertEqual(activated['state'], 'takeover-active')
        self.assertEqual(discussion.handle(self.request_recovery(active))['state'], 'takeover-active')
        self.assertEqual(discussion.handle(self.request_recovery(self.new))['state'], 'takeover-active')
        _, records = _load_records(self.ledger)
        self.assertEqual(records['Phase Results'][0]['legacy_control_json'], self.raw_old)
        self.assertEqual(len([r for r in records['Phase Results'] if r.get('result_kind') == 'workflow-control']), 1)

    def test_digest_identity_and_cas_fail_closed_without_mutation(self):
        before = self.ledger.read_bytes()
        wrong = self.request_recovery()
        wrong['evidence']['old_control_sha256'] = '0' * 64
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(wrong)
        altered = copy.deepcopy(self.new)
        altered['handoff_progress']['binding']['worktree'] = '/different-worktree'
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(self.request_recovery(altered))
        self.assertEqual(self.ledger.read_bytes(), before)
        stale = self.request_recovery()
        discussion.handle(self.request_recovery())
        after = self.ledger.read_bytes()
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(stale)
        self.assertEqual(self.ledger.read_bytes(), after)

    def test_any_stage3_phase_intent_blocks_takeover(self):
        frontmatter, records = _load_records(self.ledger)
        records['Phase Runs'].append({'run_id': 'old-phase-3-intent', 'run_kind': 'phase-run',
            'state': 'cancelled', 'record_revision': 1,
            'data_json': json.dumps({'source_topic_id': self.topic['topic_id'],
                'from_phase': 2, 'to_phase': 3, 'state': 'cancelled'})})
        self.ledger.write_bytes(_render_records_ledger(frontmatter, records))
        before = self.ledger.read_bytes()
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(self.request_recovery())
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_launch_reservation_is_visible_unique_and_idempotent(self):
        discussion.handle(self.request_recovery())
        active = copy.deepcopy(self.new)
        active['legacy_takeover']['state'] = 'active'
        discussion.handle(self.request_recovery(active))
        run_id, attempt_id = self.phase3_intent()
        request = self.reserve_request(run_id, attempt_id)
        saved = discussion.handle(request)
        self.assertEqual(saved['state'], 'launch-reserved')
        self.assertEqual(discussion.handle(request)['reservation'], saved['reservation'])
        read = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        self.assertEqual(read['workflow_control']['legacy_takeover']['reservation'], saved['reservation'])
        before = self.ledger.read_bytes()
        self.assertEqual(discussion.handle(self.reserve_request(run_id, attempt_id))['reservation'],
                         saved['reservation'])
        self.assertEqual(self.ledger.read_bytes(), before)
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(self.reserve_request(run_id, attempt_id,
                reservation_id='different-reservation'))
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_launch_reservation_requires_active_takeover_and_exact_phase(self):
        discussion.handle(self.request_recovery())
        run_id, attempt_id = 'PR-00000099', 'PA-00000099-1'
        before = self.ledger.read_bytes()
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(self.reserve_request(run_id, attempt_id))
        self.assertEqual(self.ledger.read_bytes(), before)
        active = copy.deepcopy(self.new)
        active['legacy_takeover']['state'] = 'active'
        discussion.handle(self.request_recovery(active))
        self.phase3_intent()
        wrong = self.reserve_request(run_id, 'PA-00000099-2')
        before = self.ledger.read_bytes()
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(wrong)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_supersede_replaces_unique_pending_control_and_keeps_prior_bytes(self):
        discussion.handle(self.request_recovery())
        _, records = _load_records(self.ledger)
        original_old = records['Phase Results'][0]['legacy_control_json']
        previous_current = records['Phase Results'][0]['data_json']
        request = self.supersede_request()
        result = discussion.handle(request)
        self.assertEqual(result['state'], 'takeover-pending')
        self.assertEqual(result['takeover_transaction_id'], 'transaction-2')
        self.assertEqual(result['previous_takeover_transaction_id'], 'transaction-1')
        _, records = _load_records(self.ledger)
        control_record = records['Phase Results'][0]
        self.assertEqual(control_record['legacy_control_json'], original_old)
        self.assertEqual(control_record['legacy_supersede_1_control_json'], previous_current)
        self.assertEqual(control_record['data_json'], json.dumps(result['control']['context'],
            sort_keys=True, separators=(',', ':'), ensure_ascii=False))
        self.assertEqual(len([r for r in records['Phase Results']
                              if r.get('result_kind') == 'workflow-control']), 1)
        before = self.ledger.read_bytes()
        self.assertEqual(discussion.handle(request)['idempotent_replay'], True)
        replay = copy.deepcopy(request)
        replay['idempotency_key'] = str(uuid.uuid4())
        latest = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        replay['expected_ledger_revision'] = latest['ledger_revision']
        replay['expected_topic_revision'] = latest['record_revision']
        self.assertTrue(discussion.handle(replay)['acknowledged'])
        self.assertEqual(self.ledger.read_bytes(), before)
        old_active = copy.deepcopy(self.new)
        old_active['legacy_takeover']['state'] = 'active'
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(self.request_recovery(old_active))
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_supersede_rejects_conflicting_prior_or_phase_three_intent(self):
        discussion.handle(self.request_recovery())
        wrong_prior = self.supersede_request()
        wrong_prior['evidence']['previous_takeover_transaction_id'] = 'another-transaction'
        before = self.ledger.read_bytes()
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(wrong_prior)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.phase3_intent()
        before = self.ledger.read_bytes()
        with self.assertRaises(discussion.ProtocolError):
            discussion.handle(self.supersede_request())
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_second_supersede_appends_history_without_reactivating_first(self):
        discussion.handle(self.request_recovery())
        first = discussion.handle(self.supersede_request())
        _, records = _load_records(self.ledger)
        first_history = records['Phase Results'][0]['legacy_supersede_1_control_json']
        first_meta = records['Phase Results'][0]['legacy_supersede_1_meta_json']
        second = discussion.handle(self.supersede_request(transaction_id='transaction-3'))
        self.assertEqual(second['state'], 'takeover-pending')
        _, records = _load_records(self.ledger)
        item = records['Phase Results'][0]
        self.assertEqual(item['legacy_supersede_1_control_json'], first_history)
        self.assertEqual(item['legacy_supersede_1_meta_json'], first_meta)
        self.assertEqual(json.loads(item['legacy_supersede_2_control_json']), first['control']['context'])
        self.assertEqual(json.loads(item['data_json'])['legacy_takeover']['takeover_transaction_id'],
                         'transaction-3')

    def test_c_reserves_topic_before_consuming_attached_stage_three(self):
        scenario, record, start = self.attached_progress_fixture()
        # This integration joins a historical standalone Git checkpoint to a
        # separate disposable Topic, so their earlier scope seals differ. The
        # reservation transaction below remains the real C→Topic path.
        scope_patch = patch.object(progress, 'verify_scope_authority', return_value=None)
        scope_patch.start()
        self.addCleanup(scope_patch.stop)
        original_ledger = self.ledger.read_bytes()
        original_local = scenario.checkpoint.read_bytes()
        cwd_patch, settings_patch = scenario.recovered_host_patches(record)
        invalid = copy.deepcopy(start)
        invalid['data']['handoff']['authorization']['scope_digest'] = '0' * 64
        with cwd_patch, settings_patch:
            with self.assertRaises(progress.entry.PreparationError):
                progress.handle(scenario.checkpoint, invalid)
        self.assertEqual(self.ledger.read_bytes(), original_ledger)
        self.assertEqual(scenario.checkpoint.read_bytes(), original_local)

        original_discussion_handle = discussion.handle

        def interrupt_reservation(request):
            if request.get('action') == 'scope-recover-legacy-reserve':
                raise KeyboardInterrupt('reservation RPC interrupted before Topic write')
            return original_discussion_handle(request)

        with cwd_patch, settings_patch, patch.object(progress.handoff, 'handle', return_value={'stage': 3}), \
                patch.object(progress.entry.discussion_protocol, 'handle', side_effect=interrupt_reservation):
            with self.assertRaises(KeyboardInterrupt):
                progress.handle(scenario.checkpoint, start)
        pending = progress.read_record(scenario.checkpoint)[progress.KEY]
        intent = pending['scope_recoveries'][record['recovery_id']]['ledger']['reservation_request']
        self.assertEqual(intent['action'], 'scope-recover-legacy-reserve')
        self.assertEqual(intent['evidence']['phase_run_id'], start['data']['handoff']['authorization']['phase']['run_id'])
        self.assertEqual(intent['evidence']['phase_attempt_id'], start['data']['handoff']['authorization']['phase']['attempt_id'])
        self.assertEqual(intent['evidence']['controller_ref'], self.owner)
        self.assertEqual(pending['stage'], 2)
        self.assertEqual(pending['scope_recoveries'][record['recovery_id']]['state'], 'authorized')
        self.assertEqual(self.ledger.read_bytes(), original_ledger)

        start['expected_revision'] = pending['revision']
        with cwd_patch, settings_patch, patch.object(progress.handoff, 'handle', return_value={'stage': 3}), \
                patch.object(progress.Progress, 'save', side_effect=KeyboardInterrupt('Topic wrote before C receipt save')):
            with self.assertRaises(KeyboardInterrupt):
                progress.handle(scenario.checkpoint, start)
        reserved_ledger = self.ledger.read_bytes()
        self.assertNotEqual(reserved_ledger, original_ledger)
        unresolved = progress.read_record(scenario.checkpoint)[progress.KEY]
        self.assertEqual(unresolved['stage'], 2)
        self.assertEqual(unresolved['scope_recoveries'][record['recovery_id']]['state'], 'authorized')
        self.assertIsNone(unresolved['scope_recoveries'][record['recovery_id']]['ledger']['reservation_receipt'])

        with cwd_patch, settings_patch, patch.object(progress.handoff, 'handle', return_value={'stage': 3}), \
                patch.object(progress.Progress, 'advance', return_value={'status': 'reserved'}):
            result = progress.handle(scenario.checkpoint, start)
        self.assertEqual(result['status'], 'reserved')
        self.assertEqual(self.ledger.read_bytes(), reserved_ledger)
        current = progress.read_record(scenario.checkpoint)[progress.KEY]
        receipt = current['scope_recoveries'][record['recovery_id']]['ledger']['reservation_receipt']
        self.assertEqual(receipt['reservation'], {key: value for key, value in intent['evidence'].items()
                                                  if key != 'controller_ref'})
        topic = discussion.handle(self.evolution_request(self.topic, operation='read-topic'))
        self.assertEqual(topic['workflow_control']['legacy_takeover']['reservation'], receipt['reservation'])
        self.assertEqual(current['scope_recoveries'][record['recovery_id']]['state'], 'consumed')
        self.assertEqual(current['stage'], 3)


if __name__ == '__main__':
    unittest.main()
