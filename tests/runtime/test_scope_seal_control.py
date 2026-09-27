"""The Controller binds the reviewed scope proposal before planning publication."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_control as fixtures


class ScopeSealControlTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkflowControlTests(methodName="runTest")

    def test_design_scope_seal_is_controller_bound_and_immutable(self):
        context = self.fixture.context()
        context['stage'] = 2
        binding = self.fixture.binding()
        started = self.fixture.call('start-design', {
            'design_input': 'reviewed-design', 'binding': binding,
            'allowed_paths': ['docs/spec.md'],
            'protected_paths': [context['requirement_identity']['path']],
            'configuration': self.fixture.configuration('solution-designer')}, context)
        self.assertTrue(started['ok'], started)
        context = started['context']
        bound = self.fixture.call('designer-bound', {'ref': 'native:designer',
                                             'attempt': context['carrier']['attempt']}, context)
        self.assertTrue(bound['ok'], bound)
        context = bound['context']
        evidence = {
            'proposal_id': 'proposal-1', 'proposal_digest': 'b' * 64,
            'expected_scope_revision': 0, 'candidate_commit': 'c' * 40,
            'manifest_digest': 'd' * 64,
            'requirement_identity': context['requirement_identity'],
            'binding': binding, 'prior_authorization_ref': 'user:design',
            'decision': {'kind': 'in-requirement-refinement', 'reference': 'controller:review'},
            'target_head': 'e' * 40, 'basis_blobs': {'docs/spec.md': 'f' * 64}}
        sealed = self.fixture.call('scope-seal', evidence, context)
        self.assertTrue(sealed['ok'], sealed)
        self.assertEqual(sealed['scope_seal'], sealed['context']['handoff_progress']['scope_seal'])
        replay = self.fixture.call('scope-seal', evidence, sealed['context'])
        self.assertTrue(replay['acknowledged'], replay)
        changed = copy.deepcopy(evidence)
        changed['manifest_digest'] = '0' * 64
        self.assertFalse(self.fixture.call('scope-seal', changed, sealed['context'])['ok'])
        revision = {
            'revision_id': 'revision-2', 'expected_scope_revision': 1,
            'previous_scope_digest': '1' * 64, 'new_scope_digest': '2' * 64,
            'scope_diff_digest': '3' * 64, 'snapshot_digest': '4' * 64,
            'requirement_identity': context['requirement_identity'], 'binding': binding,
            'planning_commit': 'c' * 40, 'planning_merge_commit': 'd' * 40,
            'candidate_commit': 'c' * 40,
            'decision': {'kind': 'in-requirement-refinement', 'reference': 'controller:revision'},
            'stop_evidence_ref': None}
        amended = self.fixture.call('scope-revise', revision, sealed['context'])
        self.assertTrue(amended['ok'], amended)
        self.assertEqual(amended['scope_revision'],
                         amended['context']['handoff_progress']['scope_revisions'][0])
        self.assertTrue(self.fixture.call('scope-revise', revision, amended['context'])['acknowledged'])
        changed_revision = copy.deepcopy(revision)
        changed_revision['new_scope_digest'] = '5' * 64
        self.assertFalse(self.fixture.call('scope-revise', changed_revision, amended['context'])['ok'])

    def test_revision_rebinds_same_dispatcher_without_spawning(self):
        context = self.fixture.context()
        context['stage'] = 3
        binding = self.fixture.binding()
        evidence = {'binding': binding, 'binding_verified': True,
                    'allowed_paths': ['old.py'], 'protected_paths': ['docs/draft.md'],
                    'authority_digest': '1' * 64, 'testing_basis': 'focused test',
                    'validation_plan': fixtures.plan_for(Path('/tmp/flow')),
                    'configuration': self.fixture.configuration('implementation-dispatcher')}
        started = self.fixture.call('start-dispatch', evidence, context)
        self.assertTrue(started['ok'], started)
        bound = self.fixture.call('dispatcher-bound', {'ref': 'native:original',
            'attempt': started['attempt']}, started['context'])
        self.assertTrue(bound['ok'], bound)
        revision = {'revision_id': 'rev-2', 'expected_scope_revision': 1,
                    'previous_scope_digest': '1' * 64, 'new_scope_digest': '2' * 64,
                    'scope_diff_digest': '3' * 64, 'snapshot_digest': '4' * 64,
                    'requirement_identity': context['requirement_identity'], 'binding': binding,
                    'planning_commit': 'a' * 40, 'planning_merge_commit': 'b' * 40,
                    'candidate_commit': 'a' * 40,
                    'decision': {'kind': 'in-requirement-refinement', 'reference': 'controller:revision'},
                    'stop_evidence_ref': '5' * 64}
        amended = self.fixture.call('scope-revise', revision, bound['context'])
        self.assertTrue(amended['ok'], amended)
        resume = {**evidence, 'allowed_paths': ['new.py', 'old.py'],
                  'authority_digest': '2' * 64,
                  'revision_ref': amended['scope_revision']['revision_ref'],
                  'revision_scope_digest': '2' * 64,
                  'old_ref': 'native:original', 'old_attempt': started['attempt'],
                  'stop_evidence_ref': '5' * 64}
        resumed = self.fixture.call('start-dispatch-revision', resume, amended['context'])
        self.assertTrue(resumed['ok'], resumed)
        self.assertEqual(resumed['context']['carrier']['ref'], 'native:original')
        self.assertNotEqual(resumed['attempt'], started['attempt'])
        self.assertEqual(resumed['effects'][0]['operation'], 'continue_native')
        self.assertEqual(resumed['context']['handoff_progress']['allowed_paths'], ['new.py', 'old.py'])


if __name__ == '__main__':
    unittest.main()
