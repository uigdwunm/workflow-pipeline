"""Observable workflow controller plans through its JSON process boundary."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

CLI = Path(__file__).with_name('workflow_control.py')

class WorkflowControlTests(unittest.TestCase):
    def call(self, action, evidence=None, context=None):
        request = {'schema_version': 1, 'action': action,
                   'actor_ref': 'controller', 'context': context or self.context(),
                   'evidence': evidence or {}}
        result = subprocess.run([sys.executable, str(CLI)], input=json.dumps(request),
                                text=True, capture_output=True)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def configuration(self, role='dedicated-discussion'):
        return {'role': role, 'required_capability': 2,
                'supported': [{'model': 'supported', 'effort': 'high', 'capability': 3,
                               'cost': None, 'permission': 'same', 'visible_identity': 'same'}],
                'user': {'model': 'supported', 'effort': 'high'}, 'frozen': None,
                'previous': None, 'receipt': 'current tool', 'can_override': True,
                'inherited': None, 'upgrade_attempted': False}

    def context(self):
        return {'schema_version': 1, 'controller_ref': 'controller', 'topic_ref': None,
                'stage': 0, 'carrier': None,
                'preference': {'topic_current': False, 'stage_current': False},
                'flow_authority': None, 'requirement_identity': {'path': 'docs/draft.md',
                    'sha256': 'a' * 64, 'version': 1}, 'handoff_progress': None}

    def test_preparation_freezes_one_plan_without_creating_a_task(self):
        result = self.call('prepare', {'target': 'local', 'project': 'project',
            'title': 'Discuss', 'missing_context': [],
            'configuration': self.configuration(),
            'next_step': 'discuss', 'archive_ref': None, 'gate_open': True})
        self.assertTrue(result['ok'])
        self.assertEqual(result['plan']['task_count'], 1)
        self.assertEqual(result['plan']['configuration']['model'], 'supported')
        self.assertEqual(result['effects'], [])
        self.assertEqual(result['context']['handoff_progress']['state'], 'prepared')

    def test_confirm_is_once_and_cancelled_attempt_cannot_bind(self):
        evidence = {'target': 'local', 'project': 'project', 'title': 'Discuss',
            'missing_context': [], 'configuration': self.configuration(),
            'next_step': 'discuss', 'archive_ref': None, 'gate_open': True}
        prepared = self.call('prepare', evidence)
        ctx = prepared['context']
        confirmation = {'plan_id': prepared['plan']['plan_id'], 'intent': 'confirm'}
        created = self.call('decide', confirmation, ctx)
        self.assertTrue(created['ok'])
        self.assertEqual(created['effects'][0]['operation'], 'create_thread')
        replay = self.call('decide', confirmation, created['context'])
        self.assertEqual(replay['effects'], [])
        cancelled = self.call('cancel', {}, created['context'])
        late = self.call('creation-result', {'status': 'ready', 'ref': 'late',
            'attempt': created['context']['carrier']['attempt']}, cancelled['context'])
        self.assertFalse(late['ok'])

    def bound(self):
        prepared = self.call('prepare', {'target': 'local', 'project': 'project', 'title': 'Discuss',
            'missing_context': [], 'configuration': self.configuration(),
            'next_step': 'stage2', 'archive_ref': 'old', 'gate_open': True})
        confirmed = self.call('decide', {'plan_id': prepared['plan']['plan_id'], 'intent': 'confirm'}, prepared['context'])
        return self.call('creation-result', {'status': 'ready', 'ref': 'old',
            'attempt': confirmed['context']['carrier']['attempt']}, confirmed['context'])['context']

    def test_accept_then_successor_ready_then_archive_and_reconcile(self):
        ctx = self.bound()
        delivery = {'delivery_id': 'delivery', 'source_ref': 'old', 'attempt': ctx['carrier']['attempt'],
                    'requirement_identity': ctx['requirement_identity'], 'commit': 'b' * 40,
                    'verified_commit_hash': 'a' * 64}
        received = self.call('receive', delivery, ctx)
        self.assertTrue(received['ok'])
        self.assertFalse(self.call('archive', {}, received['context'])['ok'])
        accepted = self.call('accept', {'delivery_digest': received['delivery_digest']}, received['context'])
        self.assertTrue(accepted['ok'])
        replay = self.call('receive', delivery, accepted['context'])
        self.assertTrue(replay['acknowledged'])
        ready = self.call('successor-ready', {'ref': 'successor', 'stage': 2,
            'input_digest': received['delivery_digest'], 'role': 'solution-designer',
            'binding_verified': True, 'activated': False, 'confirmed': True}, accepted['context'])
        self.assertTrue(ready['ok'])
        archive = self.call('archive', {}, ready['context'])
        self.assertEqual(archive['effects'], [{'operation': 'archive', 'ref': 'old'}])
        unknown = self.call('archive-result', {'ref': 'old', 'status': 'unknown'}, archive['context'])
        self.assertEqual(self.call('archive', {}, unknown['context'])['effects'], [{'operation': 'read-archive-state', 'ref': 'old'}])
        done = self.call('archive-result', {'ref': 'old', 'status': 'archived'}, unknown['context'])
        self.assertEqual(done['context']['handoff_progress']['state'], 'archived')
        self.assertEqual(self.call('archive', {}, done['context'])['effects'], [])

    def test_single_dispatcher_exact_files_and_stopped_writer_recovery(self):
        ctx = self.context()
        ctx['stage'] = 3
        started = self.call('start-dispatch', {'binding': {'worktree': '/tmp/flow', 'branch': 'codex/flow'},
            'binding_verified': True, 'allowed_paths': ['src/a.py', 'src/b.py'], 'protected_paths': ['docs/draft.md'],
            'authority_digest': 'c' * 64, 'testing_basis': 'real CLI', 'configuration': self.configuration('implementation-dispatcher')}, ctx)
        self.assertTrue(started['ok'])
        self.assertFalse(self.call('start-dispatch', {}, started['context'])['ok'])
        bound = self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']}, started['context'])
        envelope = {'agent_ref': 'executor-a', 'task_id': 'a', 'paths': ['src/a.py'],
                    'read_only': ['src/b.py'], 'behavior': 'behavior a', 'tests': ['CLI test'], 'git_operations': []}
        assigned = self.call('assign', envelope, bound['context'])
        self.assertTrue(assigned['ok'])
        envelope['agent_ref'] = 'executor-b'
        envelope['task_id'] = 'b'
        self.assertFalse(self.call('assign', envelope, assigned['context'])['ok'])
        self.assertFalse(self.call('recover-dispatch', {'stopped_refs': ['dispatcher'], 'file_hashes': {},
            'replacement_ref': 'replacement'}, assigned['context'])['ok'])
        stopped = self.call('execution-result', {'agent_ref': 'executor-a', 'stopped': True,
            'changed_paths': ['src/a.py'], 'file_hashes': {'src/a.py': 'd' * 64}, 'tests': ['CLI passed'],
            'git_unchanged': True}, assigned['context'])
        self.assertTrue(stopped['ok'])
        accepted = self.call('accept-execution', {'agent_ref': 'executor-a', 'file_hashes': {'src/a.py': 'd' * 64}}, stopped['context'])
        recovered = self.call('recover-dispatch', {'stopped_refs': ['dispatcher', 'executor-a'],
            'file_hashes': {'src/a.py': 'd' * 64}, 'replacement_ref': 'replacement'}, accepted['context'])
        self.assertTrue(recovered['ok'])
        self.assertEqual(recovered['remaining_paths'], ['src/b.py'])
        self.assertEqual(recovered['revalidate'], [])

    def test_standalone_entry_requires_complete_explicit_unattached_brief(self):
        brief = {'goal': 'Small fix', 'complexity': 'low', 'implementation_basis': 'exact requirement',
                 'allowed_paths': ['src/a.py'], 'failure_semantics': 'fail explicitly',
                 'acceptance': ['known behavior'], 'testing_seam': 'CLI', 'skip_stages_1_2': True,
                 'explicit_standalone': True, 'claimed_attached': False, 'gate_open': True}
        ctx = self.context()
        ctx['topic_ref'] = 'topic'
        result = self.call('standalone-entry', brief, ctx)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['context']['topic_ref'])
        self.assertEqual(result['context']['controller_ref'], 'controller')
        self.assertEqual(result['effects'], [])
        brief['explicit_standalone'] = False
        self.assertFalse(self.call('standalone-entry', brief, ctx)['ok'])

    def test_closure_requires_reviewed_candidate_and_recovers_only_cleanup(self):
        ctx = self.context(); ctx['stage'] = 4
        candidate = 'b' * 40
        evidence = {'candidate': candidate, 'dispatcher_ref': 'dispatcher',
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis, 'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': ['full suite']},
            'binding': {'worktree': '/tmp/flow'}, 'implementation_paths': ['src/a.py'],
            'closure_paths': ['README.md'], 'protected_paths': ['docs/draft.md'], 'configuration': self.configuration('closure-agent')}
        started = self.call('start-closure', evidence, ctx)
        self.assertTrue(started['ok'])
        self.assertEqual(started['effects'][0]['role'], 'closure-agent')
        bound = self.call('closure-bound', {'ref': 'closure', 'attempt': started['attempt']}, started['context'])
        result = {'ref': 'closure', 'candidate': candidate, 'merge': 'c' * 40, 'ancestor_verified': True,
            'changed_paths': ['src/a.py', 'README.md'], 'binding': evidence['binding'],
            'checks': ['full suite'], 'worktree_removed': False, 'branch_removed': False,
            'implementation_problem': None}
        partial = self.call('closure-result', result, bound['context'])
        self.assertTrue(partial['ok'])
        self.assertEqual(partial['context']['handoff_progress']['state'], 'cleanup-pending')
        self.assertEqual(partial['effects'][0]['operation'], 'cleanup-only')
        result['worktree_removed'] = result['branch_removed'] = True
        complete = self.call('closure-result', result, partial['context'])
        self.assertEqual(complete['context']['handoff_progress']['state'], 'completed')

    def test_preference_rejects_creation_without_reprompt_and_closed_gate(self):
        ctx = self.bound()
        ctx['carrier'] = None
        ctx['handoff_progress'] = None
        ctx['preference']['topic_current'] = True
        evidence = {'target': 'local', 'project': 'project', 'title': 'Discuss',
            'missing_context': [], 'configuration': self.configuration(),
            'next_step': 'discuss', 'archive_ref': None, 'gate_open': True}
        result = self.call('prepare', evidence, ctx)
        self.assertTrue(result['ok'])
        self.assertIsNone(result['plan'])
        self.assertEqual(result['effects'], [])
        evidence['gate_open'] = False
        self.assertFalse(self.call('prepare', evidence, ctx)['ok'])

    def test_role_selection_preserves_confirmed_and_bounds_upgrade(self):
        cheap = {'model': 'small', 'effort': 'high', 'capability': 1, 'cost': 1, 'permission': 'same', 'visible_identity': 'same'}
        strong = {'model': 'large', 'effort': 'high', 'capability': 3, 'cost': None, 'permission': 'same', 'visible_identity': 'same'}
        evidence = {'role': 'execution-agent', 'required_capability': 1, 'supported': [cheap, strong],
            'user': None, 'frozen': {'model': 'small', 'effort': 'high'}, 'previous': cheap,
            'receipt': 'adapter-current', 'can_override': True, 'inherited': None, 'upgrade_attempted': False}
        selected = self.call('select-configuration', evidence)
        self.assertTrue(selected['ok'])
        self.assertEqual(selected['selection']['model'], 'small')
        self.assertFalse(selected['selection']['disclose'])
        evidence['previous'] = {**cheap, 'permission': 'old-permission'}
        self.assertTrue(self.call('select-configuration', evidence)['selection']['needs_decision'])
        evidence['previous'] = cheap
        evidence['supported'] = [strong]
        upgraded = self.call('select-configuration', evidence)
        self.assertTrue(upgraded['selection']['needs_decision'])
        evidence['upgrade_attempted'] = True
        self.assertFalse(self.call('select-configuration', evidence)['ok'])

if __name__ == '__main__':
    unittest.main()
