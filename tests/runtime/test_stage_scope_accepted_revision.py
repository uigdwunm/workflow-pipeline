"""An accepted Stage-3 candidate is historical after a scope revision."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import test_stage_scope_progress as scope_fixture
import test_workflow_progress as flow_fixture
import stage_scope
import workflow_progress as progress


class AcceptedStageThreeRevisionTests(unittest.TestCase):
    def setUp(self):
        self.scenario = scope_fixture.ScopeProgressTests(methodName='runTest')
        self.scenario.setUp()
        self.addCleanup(self.scenario.doCleanups)
        self.flow = self.scenario.flow

    def accepted_stage_three(self):
        planning = self.scenario.planning_candidate()
        _, digest = self.scenario.propose(planning)
        self.flow.ready_publication(planning, 'controller:planning-readiness')
        self.scenario.seal(digest)
        self.flow.invoke('publication', {'candidate_commit': planning,
            'reference': 'controller:planning-readiness'})
        pending = self.flow.invoke('receive-publication')['pending']
        self.flow.invoke('decide', {'decision_id': pending['decision_id'],
            'subject': pending['subject'], 'answer': 'accept',
            'reference': 'controller:accept-plan'})
        design = copy.deepcopy(self.flow.state())
        authority = design['scope_authority']
        request = self.flow.input_for(3, design['accepted'])
        request['scope']['downstream'] = copy.deepcopy(authority['scope']['downstream'])
        request['scope']['implementation_paths'] = copy.deepcopy(authority['scope']['implementation_paths'])
        request['scope']['closure_paths'] = copy.deepcopy(authority['scope']['closure_paths'])
        request['authorization']['scope_digest'] = flow_fixture.transfer.entry.digest(request['scope'])
        self.flow.context = self.flow.context_for(3)
        self.flow.begin('continuous', request)
        self.flow.invoke('observe', self.flow.observation(ref='native:dispatcher'))
        (self.flow.flow / 'impl.py').write_text('def approved_behavior():\n    return True\n')
        self.flow.flow_git('add', 'impl.py')
        self.flow.flow_git('commit', '-qm', 'accepted implementation')
        implementation = self.flow.flow_git('rev-parse', 'HEAD')
        payload = {'artifacts': ['impl.py'], 'checks': ['behavior tested'],
            'candidate_commit': implementation,
            'review': {axis: {'candidate': implementation, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': implementation, 'checks': ['behavior tested']}}
        original_observation = self.flow.observation

        def resumable_observation(*args, **kwargs):
            value = original_observation(*args, **kwargs)
            if args and args[0] == 'stopped' and kwargs.get('ref') == 'native:dispatcher':
                value['receipt']['raw']['resumable'] = True
            return value

        with patch.object(self.flow, 'observation', side_effect=resumable_observation):
            accepted = self.flow.accept_current(payload, 'native:dispatcher')
        self.assertEqual(accepted['status'], 'accepted', accepted)
        self.assertEqual(self.flow.state()['stage'], 3)
        self.assertNotIn('stage4', progress.read_record(self.flow.checkpoint).get('stage_results', {}))
        return planning, design, request, copy.deepcopy(self.flow.state())

    def test_revision_keeps_accepted_candidate_and_review_only_in_history(self):
        planning, design, request, accepted = self.accepted_stage_three()
        old_acceptance = copy.deepcopy(accepted['accepted'])
        old_review = copy.deepcopy(accepted['control']['context']['handoff_progress'].get('review_decision'))
        old_dispatch = copy.deepcopy(accepted['dispatch'])
        self.assertIsNotNone(old_review)
        authority = design['scope_authority']
        manifest = copy.deepcopy(authority['scope']['downstream']['manifest'])
        manifest.append({'path': 'tests/test_impl.py', 'owner': 'implementation', 'kind': 'test',
            'operation': 'add', 'basis': {'commit': planning, 'path': 'docs/spec.md',
                                         'section': 'approved design'}})
        revision = {'revision_id': 'accepted-add-test', 'expected_scope_revision': 1,
            'previous_scope_digest': flow_fixture.transfer.entry.digest(authority['scope']),
            'requirement_identity': copy.deepcopy(design['handoff']['requirement_identity']),
            'binding': copy.deepcopy(design['handoff']['binding']),
            'planning_commit': planning,
            'planning_merge_commit': design['accepted']['payload']['planning_merge_commit'],
            'candidate_commit': planning,
            'old_manifest': copy.deepcopy(authority['scope']['downstream']['manifest']),
            'manifest': manifest, 'implementation_required': True,
            'diff': stage_scope.manifest_diff(authority['scope']['downstream']['manifest'], manifest),
            'reason': 'The accepted implementation needs a regression test file.',
            'controller_ref': 'task',
            'decision': {'kind': 'in-requirement-refinement', 'reference': 'controller:accepted-add-test'}}
        response = self.flow.invoke('scope-revise', revision)
        self.assertEqual(response['next_action']['operation'], 'stage3-revision-attempt', response)
        authorized = self.flow.state()
        self.assertEqual(authorized['scope_revision_transaction']['state'], 'authorized')
        self.assertEqual(authorized['accepted'], old_acceptance)
        self.assertEqual(authorized['dispatch'], old_dispatch)
        readiness = progress.revision_scope_readiness(authorized, self.flow.checkpoint)
        self.assertIsNotNone(readiness)

        revised_input = copy.deepcopy(request)
        revised_scope = copy.deepcopy(authorized['handoff']['scope'])
        revised_scope['baseline'] = authorized['scope_revision_transaction']['snapshot']['head']
        revised_scope['downstream'] = copy.deepcopy(readiness['scope_authority']['scope']['downstream'])
        revised_scope['implementation_paths'] = copy.deepcopy(readiness['scope_authority']['scope']['implementation_paths'])
        revised_scope['closure_paths'] = copy.deepcopy(readiness['scope_authority']['scope']['closure_paths'])
        revised_scope['owned_paths'] = copy.deepcopy(revised_scope['implementation_paths'])
        revised_input['scope'] = revised_scope
        revised_input['authorization'].update(reference='controller:accepted-add-test',
                                              flow_mode='continuous',
                                              scope_digest=flow_fixture.transfer.entry.digest(revised_scope))
        revised_input['readiness'] = readiness
        expected_scope = copy.deepcopy(authorized['handoff']['scope'])
        expected_scope.update(implementation_paths=copy.deepcopy(readiness['scope_authority']['scope']['implementation_paths']),
            closure_paths=copy.deepcopy(readiness['scope_authority']['scope']['closure_paths']),
            downstream=copy.deepcopy(readiness['scope_authority']['scope']['downstream']),
            baseline=authorized['scope_revision_transaction']['snapshot']['head'],
            owned_paths=copy.deepcopy(readiness['scope_authority']['scope']['implementation_paths']))
        self.assertEqual(revised_input['scope'], expected_scope)
        self.assertEqual(revised_input['predecessor'], design['accepted'])
        self.assertEqual(revised_input['binding'], authorized['handoff']['binding'])
        self.assertEqual(revised_input['requirement'], authorized['handoff']['requirement'])
        self.assertEqual(revised_input['configuration'], authorized['handoff']['configuration'])
        self.assertEqual(revised_input['target'], authorized['handoff']['target'])
        activated = self.flow.invoke('activate-scope-revision', {'revision_id': revision['revision_id'],
            'handoff': revised_input})
        self.assertEqual(activated['next_action']['operation'], 'continue-host', activated)
        current = self.flow.state()
        historical = current['history'][-1]
        self.assertEqual(historical['accepted'], old_acceptance)
        self.assertEqual(historical['dispatch'], old_dispatch)
        self.assertEqual(historical['control']['context']['handoff_progress']['review_decision'], old_review)
        self.assertIsNone(current['accepted'])
        self.assertIsNone(current['dispatch']['acceptance'])
        self.assertIsNone(current['control']['context']['handoff_progress']['candidate'])
        self.assertFalse(current.get('review_activity'))
        self.assertEqual(current['scope_authority']['revision'], 2)
        self.assertEqual(current['handoff']['predecessor'], design['accepted'])


if __name__ == '__main__':
    unittest.main()
