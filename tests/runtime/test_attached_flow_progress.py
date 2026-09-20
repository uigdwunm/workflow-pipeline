"""Real attached A/B/C/Phase/Git closure; only native host receipts are fixtures."""
import copy
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

import test_workflow_progress as fixtures
import test_entry_prepare

entry = fixtures.transfer.entry
requirement = fixtures.transfer.requirement
protocol = fixtures.transfer.discussion_protocol
progress = fixtures.progress
supervision = fixtures.transfer.supervision


class AttachedFlowTests(fixtures.ProgressTests):
    def setUp(self):
        test_entry_prepare.EntrySupport.setUp(self)
        topic = protocol.handle({'protocol_version': 1, 'operation': 'bootstrap', 'project_path': str(self.root),
            'entry_mode': 'explicit-skill', 'conversation_ref': 'task', 'idempotency_key': str(uuid.uuid4()), 'root_slug': 'attached'})
        self.attachment = {'project_id': topic['project_id'], 'tree_id': topic['tree_id'],
            'actor_topic_id': topic['topic_id'], 'actor_conversation_ref': 'task'}
        self.base = {'protocol_version': 1, 'project_path': str(self.root), **self.attachment}
        self.ledger = Path(topic['ledger_path'])
        self.requirement_path = Path(topic['topic_document_path']).relative_to(self.root).as_posix()
        self.git('add', 'docs/discussions')
        self.git('commit', '-qm', 'confirmed discussion')
        self.request.update(stage=0, source={'kind': 'discussion', 'attachment': self.attachment})
        intent = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'purpose': 'freeze', 'authorization': 'source:freeze', 'base_ref': 'HEAD'})
        self.frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'freeze', 'entry': self.request, 'intent': intent})
        self.git('merge', '--ff-only', self.frozen['commit'])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.flow = Path(temporary.name).resolve() / 'flow'
        self.checkpoint = Path(temporary.name).resolve() / 'progress.json'
        self.binding = supervision.start_worktree({'repository': str(self.root), 'worktree': str(self.flow),
            'branch': 'codex/attached-flow', 'target_branch': 'main'})['binding']
        entry.os.getcwd.return_value = str(self.flow)
        self.request['host']['project_path'] = str(self.flow)
        self.request['target'] = {'kind': 'flow', 'binding': self.binding}
        self.host_response_sequence = 0
        self.phases = {}
        self.input = self.input_for(2)
        self.context = self.context_for(2)

    def mutate(self, operation, **values):
        topic = entry.topic_read(str(self.root), self.attachment)
        return protocol.handle({**self.base, 'operation': operation,
            'expected_ledger_revision': topic['ledger_revision'], 'expected_topic_revision': topic['record_revision'],
            'idempotency_key': str(uuid.uuid4()), **values})

    def input_for(self, stage, predecessor=None):
        if stage == 2:
            phase = self.mutate('prepare-wrapper-phase-run', from_phase=0, to_phase=2, route='0->2',
                carrier_kind='solution-designer', source_checkpoint_id=self.frozen['checkpoint']['checkpoint_id'],
                flow_mode='stepwise', flow_mode_source='explicit-stage-confirmation', scope=['repository'])
        else:
            phase = self.mutate('prepare-phase-run', from_phase=stage-1, to_phase=stage,
                route=f'{stage-1}->{stage}', carrier_kind='current-topic')
            ref = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
            self.mutate('authorize-phase-carrier', **ref, carrier_ref='task')
            self.mutate('phase-ready', **ref, carrier_ref='task', evidence=phase['evidence'])
            self.mutate('phase-activate', **ref, evidence=phase['evidence'])
        self.phases[stage] = phase
        value = super().input_for(stage, predecessor)
        value['authorization']['phase'] = {'run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        return value

    def context_for(self, stage):
        value = super().context_for(stage)
        value['topic_ref'] = self.attachment['actor_topic_id']
        return value

    def activate_designer(self):
        phase = self.phases[2]
        ref = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        cp = self.frozen['checkpoint']
        self.mutate('claim-phase-carrier', **ref, actor_conversation_ref='native:designer', carrier_ref='native:designer',
            source_checkpoint_id=cp['checkpoint_id'], source_checkpoint_identity=cp['published_identity'])
        self.mutate('phase-ready', **ref, actor_conversation_ref='native:designer', carrier_ref='native:designer', evidence=phase['evidence'])
        action = self.invoke('advance')['next_action']
        self.assertEqual(action['operation'], 'source-lifecycle')
        self.invoke('lifecycle', {'request': action['payload']['request']})
        self.invoke('advance')

    def complete_phase(self):
        for _ in range(5):
            result = self.invoke('advance')
            if self.state()['phase_complete']:
                return result
            action = result['next_action']
            if action['operation'] == 'reconcile-source-lifecycle':
                action = action['action']
            self.assertIn(action['operation'], ('carrier-lifecycle', 'source-lifecycle'), result)
            request = copy.deepcopy(action['payload']['request'])
            self.assertEqual(request['project_path'], str(self.root))
            self.invoke('lifecycle', {'request': request})
            before = self.ledger.read_bytes()
            self.invoke('lifecycle', {'request': request})
            self.assertEqual(self.ledger.read_bytes(), before)
        self.fail('phase did not complete')

    def test_attached_flow_publication_cleanup_acceptance_and_phase_replay(self):
        self.begin('stepwise')
        self.invoke('observe', self.observation())
        self.activate_designer()
        (self.flow / 'docs/spec.md').write_text('approved design\n')
        self.flow_git('add', 'docs/spec.md')
        self.flow_git('commit', '-qm', 'planning')
        candidate = self.flow_git('rev-parse', 'HEAD')
        self.ready_publication(candidate, 'controller:planning')
        result = self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:planning'})
        self.assertEqual(result['next_action']['operation'], 'publication-receipt', result)
        pending = self.invoke('receive-publication')['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'], 'answer': 'accept', 'reference': 'controller:accept-plan'})
        self.complete_phase()
        pending = self.state()['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:enter-stage3'})
        self.finish_next(3, 'stepwise')
        self.complete_phase()
        pending = self.state()['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:enter-stage4'})
        predecessor = self.state()['accepted']
        value = self.input_for(4, predecessor)
        self.context = self.context_for(4)
        self.begin('stepwise', value)
        self.invoke('observe', self.observation(ref='native:closure'))
        candidate = predecessor['payload']['candidate_commit']
        self.ready_publication(candidate, 'controller:closure', 'native:closure')
        result = self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:closure',
            'expected_target_head': self.git('rev-parse', 'HEAD')})
        self.assertFalse(self.flow.exists(), result)
        # Crash after B receives the result but before C consumes its saved transaction.
        with patch.object(progress.Progress, 'apply', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('receive-publication')
        with patch.object(supervision, 'complete_worktree', side_effect=AssertionError('republished')):
            pending = self.invoke('resume')['pending']
            self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
                'answer': 'accept', 'reference': 'controller:final-accept'})
            result = self.complete_phase()
            self.assertTrue(result['workflow_completion']['completed'])
            self.assertTrue(self.invoke('inspect')['workflow_completion']['completed'])
        self.assertEqual(entry.topic_read(str(self.root), self.attachment)['current_phase'], 4)
        self.assertFalse(self.flow.exists())


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(AttachedFlowTests(name) for name in loader.getTestCaseNames(AttachedFlowTests)
        if name in AttachedFlowTests.__dict__)
