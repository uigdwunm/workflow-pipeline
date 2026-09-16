"""Dedicated carriers preserve the topic's controller through the real CLI."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

import uuid
from test_discussion_protocol import DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport

class DedicatedStageTests(DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def test_dedicated_stage_keeps_controller_binding(self):
        topic = self.bootstrap_topic(self.make_project('dedicated', git=False))
        request = self.handoff_request(topic, operation='prepare-handoff', ledger_revision=1,
            handoff_kind='dedicated-stage', target_slug='checkout-redesign', scope=['requirements'],
            work_snapshot={'goal': 'Discuss requirement'}, authoritative_references=[], stage=0)
        code, prepared, err = self.run_cli(request)
        self.assertEqual(code, 0, (prepared, err))
        code, bound, err = self.run_cli(self.handoff_request(topic, operation='bind-handoff',
            ledger_revision=2, handoff_id=prepared['handoff_id'], attempt_id=prepared['attempt_id'],
            conversation_ref='dedicated', verified_identity={
                'project_id': topic['project_id'], 'tree_id': topic['tree_id'], 'topic_id': topic['topic_id'],
                'handoff_id': prepared['handoff_id'], 'attempt_id': prepared['attempt_id'],
                'payload_sha256': prepared['payload_sha256']}))
        self.assertEqual(code, 0, (bound, err))
        self.assertEqual(bound['active_conversation_ref'], 'discussion-task')
        code, accepted, err = self.run_cli(self.handoff_request(topic, operation='accept-handoff',
            ledger_revision=3, owner_ref='dedicated', handoff_id=prepared['handoff_id'],
            attempt_id=prepared['attempt_id'], payload_sha256=prepared['payload_sha256'],
            source_reference_sha256=prepared['authoritative_references_sha256'], turn_number=1))
        self.assertEqual(code, 0, (accepted, err))
        request['idempotency_key'] = str(uuid.uuid4())
        request['actor_conversation_ref'] = 'dedicated'
        request['expected_ledger_revision'] = 4
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected['error']['code'], 'document_ownership_conflict')

        code, active, err = self.run_cli(self.handoff_request(topic, operation='authorize-handoff-discussion',
            ledger_revision=4, owner_ref='dedicated', handoff_id=prepared['handoff_id'],
            attempt_id=prepared['attempt_id'], turn_number=2))
        self.assertEqual(code, 0, (active, err))
        mutation = {'type': 'confirm-decision', 'summary': 'Shared goal', 'rationale': 'User confirmed'}
        request = self.evolution_request(topic, operation='prepare-topic-update', expected_revision=5,
            expected_topic_revision=1, mutation=mutation)
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected['error']['code'], 'document_ownership_conflict')
        request['actor_conversation_ref'] = 'dedicated'
        code, written, err = self.run_cli(request)
        self.assertEqual(code, 0, (written, err))
        code, applied, err = self.run_cli(self.evolution_request(topic,
            operation='apply-document-write', expected_revision=6, expected_topic_revision=2,
            owner_ref='dedicated', document_write_id=written['document_write_id']))
        self.assertEqual(code, 0, (applied, err))
        for owner in ('dedicated', 'discussion-task'):
            code, read, err = self.run_cli(self.evolution_request(topic, operation='read-topic', owner_ref=owner))
            self.assertEqual(code, 0, (read, err))

        duplicate = self.handoff_request(topic, operation='prepare-handoff', ledger_revision=7, topic_revision=2,
            handoff_kind='dedicated-stage', target_slug='checkout-redesign', scope=['requirements'],
            work_snapshot={'goal': 'Discuss requirement'}, authoritative_references=[], stage=0)
        code, rejected, _ = self.run_cli(duplicate)
        self.assertEqual(code, 1)
        self.assertEqual(rejected['error']['code'], 'document_ownership_conflict')
        code, cancelled, err = self.run_cli(self.handoff_request(topic, operation='cancel-handoff-attempt',
            ledger_revision=7, topic_revision=2, handoff_id=prepared['handoff_id'], attempt_id=prepared['attempt_id'], reason='User stopped carrier'))
        self.assertEqual(code, 0, (cancelled, err))
        duplicate['expected_ledger_revision'] = 8
        code, replacement, err = self.run_cli(duplicate)
        self.assertEqual(code, 0, (replacement, err))
        code, retried, err = self.run_cli(self.handoff_request(topic, operation='retry-handoff',
            ledger_revision=9, topic_revision=2, handoff_id=prepared['handoff_id'], prior_attempt_id=prepared['attempt_id'],
            forced=True, user_authorization='Retry this dedicated carrier'))
        self.assertEqual(code, 1, (retried, err))
        request = self.evolution_request(topic, operation='prepare-topic-update', expected_revision=9,
            expected_topic_revision=2, mutation=mutation, owner_ref='dedicated')
        self.assertEqual(self.run_cli(request)[0], 1)

    def test_control_preparation_is_persisted_by_real_discussion_caller(self):
        topic = self.bootstrap_topic(self.make_project('control', git=False))
        request = self.evolution_request(topic, operation='workflow-control', expected_revision=1,
            expected_topic_revision=1, action='prepare', evidence={
                'target': 'local', 'project': topic['project_id'], 'title': 'Discuss',
                'missing_context': [], 'configuration': {'role': 'dedicated-discussion', 'required_capability': 1, 'supported': [{'model': 'supported', 'effort': 'high', 'capability': 1, 'cost': None, 'permission': 'same', 'visible_identity': 'same'}], 'user': {'model': 'supported', 'effort': 'high'}, 'frozen': None, 'previous': None, 'receipt': 'adapter', 'can_override': True, 'inherited': None, 'upgrade_attempted': False},
                'next_step': 'discuss', 'archive_ref': None, 'gate_open': True})
        code, prepared, err = self.run_cli(request)
        self.assertEqual(code, 0, (prepared, err))
        self.assertEqual(prepared['control']['effects'], [])
        self.assertEqual(prepared['control']['context']['controller_ref'], 'discussion-task')

        code, update, err = self.run_cli(self.evolution_request(topic, operation='prepare-topic-update',
            expected_revision=2, expected_topic_revision=1,
            mutation={'type': 'confirm-decision', 'summary': 'New requirement', 'rationale': 'User correction'}))
        self.assertEqual(code, 0, (update, err))
        code, applied, err = self.run_cli(self.evolution_request(topic, operation='apply-document-write',
            expected_revision=3, expected_topic_revision=2, document_write_id=update['document_write_id']))
        self.assertEqual(code, 0, (applied, err))
        before = Path(topic['ledger_path']).read_bytes()
        code, stale, err = self.run_cli(self.evolution_request(topic, operation='workflow-control',
            expected_revision=4, expected_topic_revision=2, action='decide',
            evidence={'plan_id': prepared['control']['plan']['plan_id'], 'intent': 'confirm'}))
        self.assertEqual(code, 1, (stale, err))
        self.assertEqual(Path(topic['ledger_path']).read_bytes(), before)
        code, refreshed, err = self.run_cli(self.evolution_request(topic, operation='workflow-control',
            expected_revision=4, expected_topic_revision=2, action='prepare', evidence=request['evidence']))
        self.assertEqual(code, 0, (refreshed, err))
        self.assertNotEqual(refreshed['control']['plan']['plan_id'], prepared['control']['plan']['plan_id'])
        code, decided, err = self.run_cli(self.evolution_request(topic, operation='workflow-control',
            expected_revision=5, expected_topic_revision=2, action='decide',
            evidence={'plan_id': refreshed['control']['plan']['plan_id'], 'intent': 'confirm'}))
        self.assertEqual(code, 0, (decided, err))
        self.assertEqual(decided['control']['effects'][0]['operation'], 'create_thread')

    def test_closed_gate_blocks_control_preparation_without_mutation(self):
        topic = self.bootstrap_topic(self.make_project('control-gate', git=False))
        self.prepare_child_handoff(topic, initial_dependencies=[{
            'dependent_endpoint': 'source', 'prerequisite_topic_ref': 'target',
            'requirement_kind': 'confirmed-decision', 'requirement_summary': 'Child authority required'}])
        ledger = Path(topic['ledger_path']); before = ledger.read_bytes()
        request = self.evolution_request(topic, operation='workflow-control', expected_revision=2,
            expected_topic_revision=1, action='prepare', evidence={})
        code, result, err = self.run_cli(request)
        self.assertEqual(code, 1, (result, err))
        self.assertEqual(result['error']['code'], 'topic_gate_closed')
        self.assertEqual(ledger.read_bytes(), before)

    def test_phase_zero_can_authorize_continuous_from_current_checkpoint(self):
        topic = self.bootstrap_topic(self.make_project('continuous-zero', git=False))
        prepared = self.prepare_checkpoint(topic, ledger_revision=1, purpose='stage-entry')
        code, published, err = self.run_cli(self.checkpoint_request(topic, operation='publish-non-git-checkpoint',
            ledger_revision=2, checkpoint_id=prepared['checkpoint_id'],
            expected_checkpoint_revision=prepared['checkpoint_record_revision']))
        self.assertEqual(code, 0, (published, err))
        code, authorized, err = self.run_cli(self.phase_request(topic, 'authorize-continuous-flow', 3,
            source_checkpoint_id=prepared['checkpoint_id'], source_checkpoint_identity=published['snapshot_digest'],
            phase_result_id=None, confirmation_intent='continuous', user_reply='Continue 2 through 4',
            source_phase=0, stages=[2, 3, 4], scope=['requirements']))
        self.assertEqual(code, 0, (authorized, err))
        self.assertIsNone(authorized['phase_result_id'])

    def test_explicit_standalone_from_discussion_does_not_mutate_ledger(self):
        topic = self.bootstrap_topic(self.make_project('standalone', git=False))
        before = Path(topic['ledger_path']).read_bytes()
        request = self.evolution_request(topic, operation='workflow-control', expected_revision=1,
            expected_topic_revision=1, action='standalone-entry', evidence={
                'goal': 'small fix', 'complexity': 'low', 'implementation_basis': 'exact brief',
                'allowed_paths': ['src/a.py'], 'failure_semantics': 'explicit failure',
                'acceptance': ['CLI behavior'], 'testing_seam': 'CLI', 'skip_stages_1_2': True,
                'explicit_standalone': True, 'claimed_attached': False, 'gate_open': True})
        code, result, err = self.run_cli(request)
        self.assertEqual(code, 0, (result, err))
        self.assertIsNone(result['control']['context']['topic_ref'])
        self.assertEqual(Path(topic['ledger_path']).read_bytes(), before)

    def test_real_modified_delivery_survives_phase_advance_before_archive(self):
        import hashlib
        import subprocess
        project = self.make_project('real-delivery', git=True)
        def git(*args):
            return subprocess.run(['git', '-C', str(project), *args], check=True, text=True, capture_output=True).stdout.strip()
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.invalid')
        topic = self.bootstrap_topic(project)
        document = Path(topic['topic_document_path'])
        git('add', '-f', str(document)); git('commit', '-qm', 'initial requirement')
        def mutate(operation, owner='discussion-task', **parameters):
            code, current, err = self.run_cli(self.evolution_request(topic, operation='read-topic'))
            self.assertEqual(code, 0, (current, err))
            request = self.evolution_request(topic, operation=operation,
                expected_revision=current['ledger_revision'], expected_topic_revision=current['record_revision'],
                owner_ref=owner, **parameters)
            code, result, err = self.run_cli(request)
            self.assertEqual(code, 0, (result, err))
            return result
        config = {'role': 'dedicated-discussion', 'required_capability': 1,
            'supported': [{'model': 'supported', 'effort': 'high', 'capability': 1, 'cost': None, 'permission': 'same', 'visible_identity': 'same'}],
            'user': {'model': 'supported', 'effort': 'high'}, 'frozen': None, 'previous': None,
            'receipt': 'adapter', 'can_override': True, 'inherited': None, 'upgrade_attempted': False}
        prepared = mutate('workflow-control', action='prepare', evidence={'target': 'local', 'project': topic['project_id'],
            'title': 'Discuss', 'missing_context': [], 'configuration': config, 'next_step': 'stage2', 'archive_ref': None, 'gate_open': True})['control']
        confirmed = mutate('workflow-control', action='decide', evidence={'plan_id': prepared['plan']['plan_id'], 'intent': 'confirm'})['control']
        handoff = mutate('prepare-handoff', handoff_kind='dedicated-stage', target_slug='checkout-redesign',
            scope=['requirements'], work_snapshot={'goal': 'Discuss'}, authoritative_references=[], stage=0)
        new_ref = 'task:' + str(uuid.uuid4())
        mutate('bind-handoff', handoff_id=handoff['handoff_id'], attempt_id=handoff['attempt_id'], conversation_ref=new_ref,
            verified_identity={'project_id': topic['project_id'], 'tree_id': topic['tree_id'], 'topic_id': topic['topic_id'],
                'handoff_id': handoff['handoff_id'], 'attempt_id': handoff['attempt_id'], 'payload_sha256': handoff['payload_sha256']})
        mutate('accept-handoff', owner=new_ref, handoff_id=handoff['handoff_id'], attempt_id=handoff['attempt_id'],
            payload_sha256=handoff['payload_sha256'], source_reference_sha256=handoff['authoritative_references_sha256'], turn_number=1)
        mutate('authorize-handoff-discussion', owner=new_ref, handoff_id=handoff['handoff_id'], attempt_id=handoff['attempt_id'], turn_number=2)
        mutate('workflow-control', action='creation-result', evidence={'status': 'ready', 'ref': new_ref, 'attempt': confirmed['context']['carrier']['attempt']})
        update = mutate('prepare-topic-update', owner=new_ref,
            mutation={'type': 'confirm-decision', 'summary': 'Actual new requirement', 'rationale': 'User chose it'})
        mutate('apply-document-write', owner=new_ref, document_write_id=update['document_write_id'])
        git('add', '-f', str(document)); git('commit', '-qm', 'completed requirement')
        completed_hash = hashlib.sha256(document.read_bytes()).hexdigest()
        received = mutate('workflow-control', action='receive', evidence={'delivery_id': 'completed', 'source_ref': new_ref,
            'attempt': confirmed['context']['carrier']['attempt'], 'commit': git('rev-parse', 'HEAD'),
            'requirement_identity': {'path': document.relative_to(project).as_posix(), 'version': 2, 'sha256': completed_hash},
            'verified_commit_hash': '0' * 64})['control']
        mutate('workflow-control', action='accept', evidence={'delivery_digest': received['delivery_digest']})
        cp = mutate('prepare-checkpoint', owner=new_ref, purpose='stage-entry', base_ref='HEAD')
        published = mutate('publish-git-checkpoint', owner=new_ref, checkpoint_id=cp['checkpoint_id'], expected_checkpoint_revision=cp['checkpoint_record_revision'])
        mutate('authorize-continuous-flow', source_checkpoint_id=cp['checkpoint_id'], source_checkpoint_identity=published['commit_id'],
            phase_result_id=None, confirmation_intent='continuous', user_reply='Continue 2 through 4', source_phase=0, stages=[2, 3, 4], scope=['requirements'])
        phase = mutate('prepare-wrapper-phase-run', from_phase=0, to_phase=2, route='0->2', carrier_kind='solution-designer',
            source_checkpoint_id=cp['checkpoint_id'], flow_mode='continuous', flow_mode_source='successful-stage-0-footer', scope=['requirements'])
        phase_fields = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        successor = 'native:stage2'
        mutate('authorize-phase-carrier', **phase_fields, carrier_ref=successor)
        mutate('claim-phase-carrier', owner=successor, **phase_fields, carrier_ref=successor,
            source_checkpoint_id=cp['checkpoint_id'], source_checkpoint_identity=published['commit_id'])
        mutate('phase-ready', owner=successor, **phase_fields, carrier_ref=successor, evidence=phase['evidence'])
        mutate('phase-activate', **phase_fields, evidence=phase['evidence'])
        mutate('claim-phase-completion', owner=successor, **phase_fields, carrier_ref=successor, evidence=phase['evidence'])
        mutate('complete-phase-run', **phase_fields, evidence=phase['evidence'])
        mutate('finalize-phase-run', **phase_fields, evidence=phase['evidence'])
        ready = mutate('workflow-control', action='successor-ready', evidence={'ref': successor, 'stage': 2, 'role': 'solution-designer',
            'input_digest': received['delivery_digest'], 'binding_verified': True, 'activated': True, 'confirmed': True, 'archive_ref': new_ref})['control']
        self.assertEqual(ready['context']['carrier']['ref'], new_ref)
        archived = mutate('workflow-control', action='archive', evidence={})['control']
        self.assertEqual(archived['effects'], [{'operation': 'archive', 'ref': new_ref}])

        duplicate = mutate('workflow-control', action='receive', evidence=received['context']['handoff_progress']['delivery'])['control']
        self.assertTrue(duplicate['acknowledged'])
        self.assertEqual(duplicate['effects'], [])
