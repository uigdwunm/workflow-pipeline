"""Dedicated carriers preserve the topic's controller through the real CLI."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))

import uuid
from test_discussion_protocol import DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport

class EntrySupport:
    def mutate(self, topic, operation, owner='discussion-task', success=True, **parameters):
        code, current, err = self.run_cli(self.evolution_request(topic, operation='read-topic'))
        self.assertEqual(code, 0, (current, err))
        request = self.evolution_request(topic, operation=operation,
            expected_revision=current['ledger_revision'], expected_topic_revision=current['record_revision'],
            owner_ref=owner, **parameters)
        code, result, err = self.run_cli(request)
        self.assertEqual(code, 0 if success else 1, (result, err))
        return result

    def dedicated(self, topic, stage=0, ref='dedicated'):
        handoff = self.mutate(topic, 'prepare-handoff', handoff_kind='dedicated-stage',
            target_slug='checkout-redesign', scope=['requirements'], work_snapshot={'goal': 'Discuss'},
            authoritative_references=[], stage=stage)
        self.mutate(topic, 'bind-handoff', handoff_id=handoff['handoff_id'],
            attempt_id=handoff['attempt_id'], conversation_ref=ref, verified_identity={
                'project_id': topic['project_id'], 'tree_id': topic['tree_id'], 'topic_id': topic['topic_id'],
                'handoff_id': handoff['handoff_id'], 'attempt_id': handoff['attempt_id'],
                'payload_sha256': handoff['payload_sha256']})
        return handoff

    def accept_dedicated(self, topic, handoff, ref='dedicated', **kwargs):
        return self.mutate(topic, 'accept-handoff', owner=ref, handoff_id=handoff['handoff_id'],
            attempt_id=handoff['attempt_id'], payload_sha256=handoff['payload_sha256'],
            source_reference_sha256=handoff['authoritative_references_sha256'], turn_number=1, **kwargs)

    def write_requirement(self, topic, owner='dedicated'):
        write = self.mutate(topic, 'prepare-topic-update', owner=owner,
            mutation={'type': 'confirm-decision', 'summary': 'Accepted requirement', 'rationale': 'User confirmed'})
        return self.mutate(topic, 'apply-document-write', owner=owner, document_write_id=write['document_write_id'])



class DedicatedStageTests(EntrySupport, DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
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

        mutation = {'type': 'confirm-decision', 'summary': 'Shared goal', 'rationale': 'User confirmed'}
        request = self.evolution_request(topic, operation='prepare-topic-update', expected_revision=4,
            expected_topic_revision=1, mutation=mutation)
        code, rejected, _ = self.run_cli(request)
        self.assertEqual(code, 1)
        self.assertEqual(rejected['error']['code'], 'document_ownership_conflict')
        request['actor_conversation_ref'] = 'dedicated'
        code, written, err = self.run_cli(request)
        self.assertEqual(code, 0, (written, err))
        code, applied, err = self.run_cli(self.evolution_request(topic,
            operation='apply-document-write', expected_revision=5, expected_topic_revision=2,
            owner_ref='dedicated', document_write_id=written['document_write_id']))
        self.assertEqual(code, 0, (applied, err))
        for owner in ('dedicated', 'discussion-task'):
            code, read, err = self.run_cli(self.evolution_request(topic, operation='read-topic', owner_ref=owner))
            self.assertEqual(code, 0, (read, err))

        duplicate = self.handoff_request(topic, operation='prepare-handoff', ledger_revision=6, topic_revision=2,
            handoff_kind='dedicated-stage', target_slug='checkout-redesign', scope=['requirements'],
            work_snapshot={'goal': 'Discuss requirement'}, authoritative_references=[], stage=0)
        code, rejected, _ = self.run_cli(duplicate)
        self.assertEqual(code, 1)
        self.assertEqual(rejected['error']['code'], 'document_ownership_conflict')
        code, cancelled, err = self.run_cli(self.handoff_request(topic, operation='cancel-handoff-attempt',
            ledger_revision=6, topic_revision=2, handoff_id=prepared['handoff_id'], attempt_id=prepared['attempt_id'], reason='User stopped carrier'))
        self.assertEqual(code, 0, (cancelled, err))
        duplicate['expected_ledger_revision'] = 7
        code, replacement, err = self.run_cli(duplicate)
        self.assertEqual(code, 0, (replacement, err))
        code, retried, err = self.run_cli(self.handoff_request(topic, operation='retry-handoff',
            ledger_revision=8, topic_revision=2, handoff_id=prepared['handoff_id'], prior_attempt_id=prepared['attempt_id'],
            forced=True, user_authorization='Retry this dedicated carrier'))
        self.assertEqual(code, 1, (retried, err))
        request = self.evolution_request(topic, operation='prepare-topic-update', expected_revision=8,
            expected_topic_revision=2, mutation=mutation, owner_ref='dedicated')
        self.assertEqual(self.run_cli(request)[0], 1)

    def test_control_preparation_is_persisted_by_real_discussion_caller(self):
        topic = self.bootstrap_topic(self.make_project('control', git=False))
        handoff = self.mutate(topic, 'prepare-handoff', handoff_kind='dedicated-stage',
            target_slug='checkout-redesign', scope=['requirements'], work_snapshot={'goal': 'Discuss'},
            authoritative_references=[], stage=0)
        request = self.evolution_request(topic, operation='workflow-control', expected_revision=2,
            expected_topic_revision=1, action='prepare', evidence={
                'target': 'local', 'project': topic['project_id'], 'title': 'Discuss',
                'missing_context': [], 'configuration': {'role': 'dedicated-discussion', 'required_capability': 1, 'supported': [{'model': 'supported', 'effort': 'high', 'capability': 1, 'cost': None, 'permission': 'same', 'visible_identity': 'same'}], 'user': {'model': 'supported', 'effort': 'high'}, 'frozen': None, 'previous': None, 'receipt': 'adapter', 'can_override': True, 'inherited': None, 'upgrade_attempted': False},
                'next_step': 'discuss', 'archive_ref': None, 'gate_open': True})
        code, prepared, err = self.run_cli(request)
        self.assertEqual(code, 0, (prepared, err))
        self.assertEqual(prepared['control']['effects'], [])
        self.assertEqual(prepared['control']['context']['controller_ref'], 'discussion-task')

        code, update, err = self.run_cli(self.evolution_request(topic, operation='prepare-topic-update',
            expected_revision=3, expected_topic_revision=1,
            mutation={'type': 'confirm-decision', 'summary': 'New requirement', 'rationale': 'User correction'}))
        self.assertEqual(code, 0, (update, err))
        code, applied, err = self.run_cli(self.evolution_request(topic, operation='apply-document-write',
            expected_revision=4, expected_topic_revision=2, document_write_id=update['document_write_id']))
        self.assertEqual(code, 0, (applied, err))
        before = Path(topic['ledger_path']).read_bytes()
        code, stale, err = self.run_cli(self.evolution_request(topic, operation='workflow-control',
            expected_revision=5, expected_topic_revision=2, action='decide',
            evidence={'plan_id': prepared['control']['plan']['plan_id'], 'intent': 'confirm'}))
        self.assertEqual(code, 1, (stale, err))
        self.assertEqual(Path(topic['ledger_path']).read_bytes(), before)
        self.mutate(topic, 'workflow-control', action='prepare', evidence=request['evidence'], success=False)
        self.mutate(topic, 'workflow-control', action='cancel', evidence={})
        self.mutate(topic, 'prepare-handoff', handoff_kind='dedicated-stage',
            target_slug='checkout-redesign', scope=['requirements'], work_snapshot={'goal': 'Updated requirement'},
            authoritative_references=[], stage=0)
        refreshed = self.mutate(topic, 'workflow-control', action='prepare', evidence=request['evidence'])
        self.assertNotEqual(refreshed['control']['plan']['plan_id'], prepared['control']['plan']['plan_id'])
        decided = self.mutate(topic, 'workflow-control', action='decide',
            evidence={'plan_id': refreshed['control']['plan']['plan_id'], 'intent': 'confirm'})
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
        handoff = mutate('prepare-handoff', handoff_kind='dedicated-stage', target_slug='checkout-redesign',
            scope=['requirements'], work_snapshot={'goal': 'Discuss'}, authoritative_references=[], stage=0)
        prepared = mutate('workflow-control', action='prepare', evidence={'target': 'local', 'project': topic['project_id'],
            'title': 'Discuss', 'missing_context': [], 'configuration': config, 'next_step': 'stage2', 'archive_ref': None, 'gate_open': True})['control']
        confirmed = mutate('workflow-control', action='decide', evidence={'plan_id': prepared['plan']['plan_id'], 'intent': 'confirm'})['control']
        new_ref = 'task:' + str(uuid.uuid4())
        mutate('bind-handoff', handoff_id=handoff['handoff_id'], attempt_id=handoff['attempt_id'], conversation_ref=new_ref,
            verified_identity={'project_id': topic['project_id'], 'tree_id': topic['tree_id'], 'topic_id': topic['topic_id'],
                'handoff_id': handoff['handoff_id'], 'attempt_id': handoff['attempt_id'], 'payload_sha256': handoff['payload_sha256']})
        mutate('accept-handoff', owner=new_ref, handoff_id=handoff['handoff_id'], attempt_id=handoff['attempt_id'],
            payload_sha256=handoff['payload_sha256'], source_reference_sha256=handoff['authoritative_references_sha256'], turn_number=1)
        mutate('workflow-control', action='creation-result', evidence={'status': 'ready', 'ref': new_ref, 'attempt': confirmed['context']['carrier']['attempt']})
        update = mutate('prepare-topic-update', owner=new_ref,
            mutation={'type': 'confirm-decision', 'summary': 'Actual new requirement', 'rationale': 'User chose it'})
        pending_delivery = self.mutate(topic, 'workflow-control', action='receive', success=False, evidence={
            'delivery_id': 'premature', 'source_ref': new_ref, 'attempt': confirmed['context']['carrier']['attempt'],
            'commit': git('rev-parse', 'HEAD'), 'requirement_identity': {'path': document.relative_to(project).as_posix(),
                'version': update['record_revision'], 'sha256': hashlib.sha256(document.read_bytes()).hexdigest()},
            'verified_commit_hash': '0' * 64})
        self.assertEqual(pending_delivery['error']['code'], 'document_write_reconciliation_required')
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


class ProblemFramingEntryTests(EntrySupport, DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def test_same_stage_accept_grants_immediate_write_in_phases_zero_and_one(self):
        for stage in (0, 1):
            with self.subTest(stage=stage):
                topic = self.bootstrap_topic(self.make_project(f'immediate-{stage}', git=False))
                if stage == 1:
                    self.complete_current_topic_phase(topic, ledger_revision=1, topic_revision=1,
                                                      from_phase=0, to_phase=1)
                handoff = self.dedicated(topic, stage)
                accepted = self.accept_dedicated(topic, handoff)
                self.assertEqual(accepted['state'], 'active')
                self.assertTrue(accepted['substantive_discussion_allowed'])
                self.assertTrue(self.write_requirement(topic)['document_verified'])
                self.mutate(topic, 'prepare-topic-update', success=False,
                    mutation={'type': 'confirm-decision', 'summary': 'Competing writer', 'rationale': 'Invalid'})

    def wrapper(self, topic, carrier='dedicated-grilling'):
        cp = self.mutate(topic, 'prepare-checkpoint', purpose='stage-entry', base_ref='HEAD')
        published = self.mutate(topic, 'publish-non-git-checkpoint', checkpoint_id=cp['checkpoint_id'],
            expected_checkpoint_revision=cp['checkpoint_record_revision'])
        phase = self.mutate(topic, 'prepare-wrapper-phase-run', from_phase=0, to_phase=1,
            route='0->1', carrier_kind=carrier, source_checkpoint_id=cp['checkpoint_id'],
            flow_mode='stepwise', flow_mode_source='explicit-stage-confirmation', scope=['requirements'])
        fields = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        owner = 'dedicated' if carrier == 'dedicated-grilling' else 'discussion-task'
        self.mutate(topic, 'authorize-phase-carrier', **fields, carrier_ref=owner)
        self.mutate(topic, 'claim-phase-carrier', owner=owner, **fields, carrier_ref=owner,
            source_checkpoint_id=cp['checkpoint_id'], source_checkpoint_identity=published['snapshot_digest'])
        self.mutate(topic, 'phase-ready', owner=owner, **fields, carrier_ref=owner, evidence=phase['evidence'])
        self.mutate(topic, 'phase-activate', **fields, evidence=phase['evidence'])
        return phase, fields, owner

    def test_wrapper_legitimate_document_write_can_complete(self):
        for carrier in ('dedicated-grilling', 'current-problem-framing'):
            with self.subTest(carrier=carrier):
                topic = self.bootstrap_topic(self.make_project(carrier, git=False))
                phase, fields, owner = self.wrapper(topic, carrier)
                written = self.write_requirement(topic, owner)
                output = {**phase['evidence'], 'source': written['after_sha256']}
                self.mutate(topic, 'claim-phase-completion', owner=owner, **fields,
                    carrier_ref=owner, evidence=output)
                self.mutate(topic, 'prepare-topic-update', owner=owner, success=False,
                    mutation={'type': 'confirm-decision', 'summary': 'Too late', 'rationale': 'Frozen'})
                self.mutate(topic, 'complete-phase-run', **fields, evidence=output)
                result = self.mutate(topic, 'finalize-phase-run', **fields, evidence=output)
                self.assertEqual(result['current_phase'], 1)
                self.assertNotEqual(phase['evidence']['source'], written['after_sha256'])

    def control_plan(self, topic, role='dedicated-problem-framing'):
        return self.mutate(topic, 'workflow-control', action='prepare', evidence={
            'target': 'local', 'project': topic['project_id'], 'title': 'Frame requirement',
            'missing_context': [], 'configuration': {'role': role, 'required_capability': 1,
                'supported': [{'model': 'supported', 'effort': 'high', 'capability': 1,
                    'cost': None, 'permission': 'same', 'visible_identity': 'same'}],
                'user': {'model': 'supported', 'effort': 'high'}, 'frozen': None, 'previous': None,
                'receipt': 'adapter', 'can_override': True, 'inherited': None, 'upgrade_attempted': False},
            'next_step': 'stage2', 'archive_ref': None, 'gate_open': True})['control']

    def test_wrapper_control_delivery_before_finalize_and_replay_after(self):
        self.run_wrapper_control_delivery()

    def test_accepted_stage_zero_successor_keeps_both_slots_until_archive(self):
        self.run_wrapper_control_delivery(predecessor=True)

    def run_wrapper_control_delivery(self, predecessor=False):
        import hashlib
        import subprocess
        project = self.make_project('wrapper-control', git=True)
        def git(*args):
            return subprocess.run(['git', '-C', str(project), *args], check=True,
                text=True, capture_output=True).stdout.strip()
        git('config', 'user.name', 'Test'); git('config', 'user.email', 'test@example.invalid')
        topic = self.bootstrap_topic(project)
        document = Path(topic['topic_document_path'])
        git('add', '-f', str(document)); git('commit', '-qm', 'input')
        old_plan = old_delivery = None
        if predecessor:
            handoff = self.dedicated(topic, ref='previous')
            old_plan = self.control_plan(topic, role='dedicated-discussion')['plan']
            self.mutate(topic, 'workflow-control', action='decide', evidence={'plan_id': old_plan['plan_id'], 'intent': 'confirm'})
            self.mutate(topic, 'workflow-control', action='creation-result', evidence={
                'status': 'ready', 'ref': 'previous', 'attempt': old_plan['plan_id']})
            self.accept_dedicated(topic, handoff, ref='previous')
            old_delivery = self.mutate(topic, 'workflow-control', action='receive', evidence={
                'delivery_id': 'previous-result', 'source_ref': 'previous', 'attempt': old_plan['plan_id'],
                'commit': git('rev-parse', 'HEAD'), 'requirement_identity': old_plan['requirement_identity'],
                'verified_commit_hash': '0' * 64})['control']
            self.mutate(topic, 'workflow-control', action='accept', evidence={'delivery_digest': old_delivery['delivery_digest']})
        cp_owner = 'previous' if predecessor else 'discussion-task'
        cp = self.mutate(topic, 'prepare-checkpoint', owner=cp_owner, purpose='stage-entry', base_ref='HEAD')
        published = self.mutate(topic, 'publish-git-checkpoint', owner=cp_owner, checkpoint_id=cp['checkpoint_id'],
            expected_checkpoint_revision=cp['checkpoint_record_revision'])
        phase = self.mutate(topic, 'prepare-wrapper-phase-run', from_phase=0, to_phase=1,
            route='0->1', carrier_kind='dedicated-grilling', source_checkpoint_id=cp['checkpoint_id'],
            flow_mode='stepwise', flow_mode_source='explicit-stage-confirmation', scope=['requirements'])
        fields = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        plan = self.control_plan(topic)
        self.assertEqual(plan['context'].get('successor_control', plan['context'])['stage'], 1)
        self.assertEqual(plan['plan']['entry_authority']['kind'], 'wrapper-phase-run')
        self.mutate(topic, 'workflow-control', action='decide', evidence={'plan_id': plan['plan']['plan_id'], 'intent': 'confirm'})
        self.mutate(topic, 'authorize-phase-carrier', **fields, carrier_ref='dedicated')
        self.mutate(topic, 'workflow-control', action='creation-result', success=False, evidence={
            'status': 'ready', 'ref': 'impostor', 'attempt': plan['plan']['plan_id']})
        self.mutate(topic, 'workflow-control', action='creation-result', evidence={
            'status': 'ready', 'ref': 'dedicated', 'attempt': plan['plan']['plan_id']})
        self.mutate(topic, 'claim-phase-carrier', owner='dedicated', **fields, carrier_ref='dedicated',
            source_checkpoint_id=cp['checkpoint_id'], source_checkpoint_identity=published['commit_id'])
        self.mutate(topic, 'phase-ready', owner='dedicated', **fields, carrier_ref='dedicated', evidence=phase['evidence'])
        self.mutate(topic, 'phase-activate', **fields, evidence=phase['evidence'])
        if predecessor:
            self.mutate(topic, 'prepare-topic-update', owner='previous', success=False,
                mutation={'type': 'confirm-decision', 'summary': 'Old writer', 'rationale': 'Revoked'})
            self.mutate(topic, 'workflow-control', action='successor-ready', evidence={
                'ref': 'dedicated', 'stage': 1, 'role': 'dedicated-problem-framing',
                'input_digest': old_delivery['delivery_digest'], 'binding_verified': True,
                'activated': True, 'confirmed': True, 'archive_ref': 'previous'})
            archive_fields = {'control_plan_id': old_plan['plan_id']}
            self.mutate(topic, 'workflow-control', action='archive', evidence=archive_fields)
            unknown = self.mutate(topic, 'workflow-control', action='archive-result', evidence={
                **archive_fields, 'ref': 'previous', 'status': 'unknown'})['control']
            self.assertIn('successor_control', unknown['context'])
            retry = self.mutate(topic, 'workflow-control', action='archive', evidence=archive_fields)['control']
            self.assertEqual(retry['effects'], [{'operation': 'read-archive-state', 'ref': 'previous'}])
            promoted = self.mutate(topic, 'workflow-control', action='archive-result', evidence={
                **archive_fields, 'ref': 'previous', 'status': 'archived'})['control']
            self.assertNotIn('successor_control', promoted['context'])
            self.assertEqual(promoted['context']['carrier']['ref'], 'dedicated')
        written = self.write_requirement(topic)
        output = {**phase['evidence'], 'source': written['after_sha256']}
        git('add', '-f', str(document)); git('commit', '-qm', 'output')
        self.mutate(topic, 'claim-phase-completion', owner='dedicated', **fields, carrier_ref='dedicated', evidence=output)
        delivery = {'delivery_id': 'framed', 'source_ref': 'dedicated', 'attempt': plan['plan']['plan_id'],
            'commit': git('rev-parse', 'HEAD'), 'requirement_identity': {'path': document.relative_to(project).as_posix(),
                'version': written['record_revision'], 'sha256': hashlib.sha256(document.read_bytes()).hexdigest()},
            'verified_commit_hash': '0' * 64}
        self.mutate(topic, 'complete-phase-run', success=False, **fields, evidence=output)
        self.mutate(topic, 'workflow-control', action='receive', success=False, evidence={**delivery, 'source_ref': 'impostor'})
        received = self.mutate(topic, 'workflow-control', action='receive', evidence=delivery)['control']
        self.mutate(topic, 'workflow-control', action='accept', evidence={'delivery_digest': received['delivery_digest']})
        self.mutate(topic, 'complete-phase-run', **fields, evidence=output)
        result = self.mutate(topic, 'finalize-phase-run', **fields, evidence=output)
        self.assertEqual(result['current_phase'], 1)
        replay = self.mutate(topic, 'workflow-control', action='receive',
            evidence=received['context']['handoff_progress']['delivery'])['control']
        self.assertTrue(replay['acknowledged'])
        self.assertEqual(replay['effects'], [])
        cp = self.mutate(topic, 'prepare-checkpoint', purpose='stage-entry', base_ref='HEAD')
        self.mutate(topic, 'publish-git-checkpoint', checkpoint_id=cp['checkpoint_id'],
            expected_checkpoint_revision=cp['checkpoint_record_revision'])
        self.mutate(topic, 'prepare-wrapper-phase-run', from_phase=1, to_phase=2,
            route='1->2', carrier_kind='solution-designer', source_checkpoint_id=cp['checkpoint_id'],
            flow_mode='stepwise', flow_mode_source='explicit-stage-confirmation', scope=['requirements'])

    def test_dedicated_accept_rechecks_gate_and_baseline_without_accepting(self):
        for obstruction in ('gate', 'document'):
            with self.subTest(obstruction=obstruction):
                topic = self.bootstrap_topic(self.make_project('accept-' + obstruction, git=False))
                handoff = self.dedicated(topic)
                if obstruction == 'gate':
                    self.mutate(topic, 'prepare-handoff', handoff_kind='child', target_slug='blocked-child',
                        scope=['child'], work_snapshot={'goal': 'Need a child decision'}, authoritative_references=[],
                        initial_dependencies=[{'dependent_endpoint': 'source', 'prerequisite_topic_ref': 'target',
                            'requirement_kind': 'confirmed-decision', 'requirement_summary': 'Child authority required'}])
                else:
                    document = Path(topic['topic_document_path'])
                    document.write_bytes(document.read_bytes() + b'\nExternal edit\n')
                before = Path(topic['ledger_path']).read_bytes()
                rejected = self.accept_dedicated(topic, handoff, success=False)
                self.assertEqual(rejected['error']['code'], 'topic_gate_closed' if obstruction == 'gate' else 'phase_source_drift')
                self.assertEqual(Path(topic['ledger_path']).read_bytes(), before)

    def test_pending_document_write_blocks_transfer_and_completion(self):
        topic = self.bootstrap_topic(self.make_project('pending-transfer', git=False))
        write = self.mutate(topic, 'prepare-topic-update', mutation={
            'type': 'confirm-decision', 'summary': 'Pending', 'rationale': 'Not applied'})
        self.mutate(topic, 'prepare-handoff', success=False, handoff_kind='dedicated-stage',
            target_slug='checkout-redesign', scope=['requirements'], work_snapshot={'goal': 'Discuss'},
            authoritative_references=[], stage=0)
        self.mutate(topic, 'apply-document-write', document_write_id=write['document_write_id'])
        phase, fields, owner = self.wrapper(topic)
        write = self.mutate(topic, 'prepare-topic-update', owner=owner, mutation={
            'type': 'confirm-decision', 'summary': 'Another pending', 'rationale': 'Not applied'})
        rejected = self.mutate(topic, 'claim-phase-completion', success=False, owner=owner,
            **fields, carrier_ref=owner, evidence=phase['evidence'])
        self.assertEqual(rejected['error']['code'], 'document_write_reconciliation_required')
        self.mutate(topic, 'apply-document-write', owner=owner, document_write_id=write['document_write_id'])

    def test_wrapper_rejects_external_changes_and_competing_writers(self):
        topic = self.bootstrap_topic(self.make_project('exclusive', git=False))
        phase, fields, owner = self.wrapper(topic)
        for writer in ('discussion-task', 'unrelated-task'):
            self.mutate(topic, 'prepare-topic-update', owner=writer, success=False,
                mutation={'type': 'confirm-decision', 'summary': 'Competing', 'rationale': 'Not authorized'})
        self.mutate(topic, 'prepare-handoff', success=False, handoff_kind='dedicated-stage',
            target_slug='checkout-redesign', scope=['requirements'], work_snapshot={'goal': 'Competing'},
            authoritative_references=[], stage=0)
        document = Path(topic['topic_document_path'])
        original = document.read_bytes()
        document.write_bytes(original + b'\nHand edited\n')
        for operation in ('prepare-topic-update', 'claim-phase-completion'):
            args = {'mutation': {'type': 'confirm-decision', 'summary': 'Bad base', 'rationale': 'External'}} if operation == 'prepare-topic-update' else {**fields, 'carrier_ref': owner, 'evidence': phase['evidence']}
            rejected = self.mutate(topic, operation, owner=owner, success=False, **args)
            self.assertEqual(rejected['error']['code'], 'phase_source_drift')
        document.write_bytes(original)
        self.mutate(topic, 'revoke-phase-authorization', **fields, reason='User cancelled')
        self.mutate(topic, 'prepare-topic-update', owner=owner, success=False,
            mutation={'type': 'confirm-decision', 'summary': 'Late write', 'rationale': 'Revoked'})

    def test_document_apply_crash_replays_exact_phase_evidence_once(self):
        topic = self.bootstrap_topic(self.make_project('write-replay', git=False))
        phase, fields, owner = self.wrapper(topic)
        write = self.mutate(topic, 'prepare-topic-update', owner=owner, mutation={
            'type': 'confirm-decision', 'summary': 'Durable output', 'rationale': 'Confirmed'})
        request = self.evolution_request(topic, operation='apply-document-write', owner_ref=owner,
            expected_revision=write['ledger_revision'], expected_topic_revision=write['record_revision'],
            document_write_id=write['document_write_id'])
        before = Path(topic['ledger_path']).read_bytes()
        code, result, err = self.run_cli(request, failpoint='document-write-before-ledger-persist')
        self.assertEqual(code, 1, (result, err))
        self.assertEqual(Path(topic['ledger_path']).read_bytes(), before)
        self.assertEqual(__import__('hashlib').sha256(Path(topic['topic_document_path']).read_bytes()).hexdigest(), write['after_sha256'])
        code, result, err = self.run_cli(request)
        self.assertEqual(code, 0, (result, err))
        committed = Path(topic['ledger_path']).read_bytes()
        self.assertEqual(self.run_cli(request)[0], 0)
        self.assertEqual(Path(topic['ledger_path']).read_bytes(), committed)
        output = {**phase['evidence'], 'source': write['after_sha256']}
        self.mutate(topic, 'claim-phase-completion', owner=owner, **fields, carrier_ref=owner, evidence=output)
        self.mutate(topic, 'complete-phase-run', **fields, evidence=output)
        self.mutate(topic, 'finalize-phase-run', **fields, evidence=output)

    def test_control_cancel_revokes_the_frozen_wrapper_attempt(self):
        topic = self.bootstrap_topic(self.make_project('control-cancel', git=False))
        phase, fields, owner = self.wrapper(topic)
        plan = self.control_plan(topic)['plan']
        self.mutate(topic, 'workflow-control', action='decide', evidence={'plan_id': plan['plan_id'], 'intent': 'confirm'})
        pending = self.mutate(topic, 'workflow-control', action='creation-result', evidence={
            'status': 'unknown', 'ref': None, 'attempt': plan['plan_id']})['control']
        self.assertIsNone(pending['context']['carrier']['ref'])
        repeat = self.mutate(topic, 'workflow-control', action='decide', evidence={
            'plan_id': plan['plan_id'], 'intent': 'confirm'})['control']
        self.assertEqual(repeat['effects'], [])
        self.mutate(topic, 'workflow-control', action='cancel', evidence={})
        self.mutate(topic, 'workflow-control', action='creation-result', success=False, evidence={
            'status': 'ready', 'ref': owner, 'attempt': plan['plan_id']})
        self.mutate(topic, 'claim-phase-completion', owner=owner, success=False,
            **fields, carrier_ref=owner, evidence=phase['evidence'])
        self.mutate(topic, 'prepare-topic-update', owner=owner, success=False,
            mutation={'type': 'confirm-decision', 'summary': 'Cancelled', 'rationale': 'No authority'})

    def test_unknown_completion_reconciles_verified_output_without_rewriting_input(self):
        topic = self.bootstrap_topic(self.make_project('unknown-output', git=False))
        phase, fields, owner = self.wrapper(topic)
        written = self.write_requirement(topic, owner)
        output = {**phase['evidence'], 'source': written['after_sha256']}
        self.mutate(topic, 'phase-outcome-unknown', **fields, reason='Completion receipt was lost')
        self.mutate(topic, 'prepare-topic-update', success=False,
            mutation={'type': 'confirm-decision', 'summary': 'Competing recovery', 'rationale': 'Unknown owner'})
        self.mutate(topic, 'reconcile-phase-run', **fields, outcome='completed', reason='Read back actual completion', evidence=output)
        self.mutate(topic, 'complete-phase-run', **fields, evidence=output)
        self.mutate(topic, 'finalize-phase-run', **fields, evidence=output)

    def test_cancelling_old_plan_does_not_cancel_a_new_phase_attempt(self):
        topic = self.bootstrap_topic(self.make_project('exact-cancel', git=False))
        phase, fields, owner = self.wrapper(topic)
        plan = self.control_plan(topic)['plan']
        self.mutate(topic, 'workflow-control', action='decide', evidence={'plan_id': plan['plan_id'], 'intent': 'confirm'})
        self.mutate(topic, 'fail-phase-run', **fields, reason='Confirmed failure')
        retry = self.mutate(topic, 'retry-phase-run', phase_run_id=fields['phase_run_id'], prior_attempt_id=fields['attempt_id'])
        self.mutate(topic, 'workflow-control', action='cancel', evidence={})
        self.mutate(topic, 'authorize-phase-carrier', phase_run_id=fields['phase_run_id'],
            attempt_id=retry['attempt_id'], carrier_ref='replacement')
        self.mutate(topic, 'workflow-control', action='choose-dedicated', evidence={'intent': 'explicit-dedicated'})
        replacement = self.control_plan(topic)['plan']
        self.assertNotEqual(replacement['plan_id'], plan['plan_id'])
        self.assertEqual(replacement['entry_authority']['attempt_id'], retry['attempt_id'])
