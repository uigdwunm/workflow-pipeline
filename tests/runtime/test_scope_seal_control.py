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
        changed_plan = copy.deepcopy(evidence)
        changed_plan['proposal_digest'] = '0' * 64
        self.assertFalse(self.fixture.call('scope-seal', changed_plan, sealed['context'])['ok'])
        revision = {
            'revision_id': 'revision-2', 'expected_scope_revision': 1,
            'previous_scope_digest': '1' * 64, 'new_scope_digest': '2' * 64,
            'scope_diff_digest': '3' * 64, 'snapshot_digest': '4' * 64,
            'requirement_identity': context['requirement_identity'], 'binding': binding,
            'planning_commit': 'c' * 40, 'planning_merge_commit': 'd' * 40,
            'candidate_commit': 'c' * 40,
            'decision': {'kind': 'in-requirement-refinement', 'reference': 'controller:revision'},
            'stop_evidence_ref': None}
        sealed['context']['handoff_progress']['state'] = 'design-accepted'
        amended = self.fixture.call('scope-revise', revision, sealed['context'])
        self.assertTrue(amended['ok'], amended)
        self.assertEqual(amended['scope_revision'],
                         amended['context']['handoff_progress']['scope_revisions'][0])
        self.assertTrue(self.fixture.call('scope-revise', revision, amended['context'])['acknowledged'])
        changed_revision = copy.deepcopy(revision)
        changed_revision['new_scope_digest'] = '5' * 64
        self.assertFalse(self.fixture.call('scope-revise', changed_revision, amended['context'])['ok'])


if __name__ == "__main__":
    unittest.main()
