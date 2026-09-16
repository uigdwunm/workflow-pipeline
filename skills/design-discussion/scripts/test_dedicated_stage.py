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
