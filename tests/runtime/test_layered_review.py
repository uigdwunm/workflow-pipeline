"""Layered review through the original C/B/Git entry; host responses are doubles."""
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixture
import execution_plan
import workflow_progress as progress


class LayeredReviewTests(fixture.ProgressTests):
    def layered_flow(self, *, shared_verification=False):
        self.shared_verification = shared_verification
        self.implementation_paths = ['a1.py', 'a2.py', 'b1.py', 'b2.py']
        self.planning_documents = {'docs/spec.md':
            'approved design\n## Implementation Decisions\n### D1 — Plan\nTwo independent Tickets.\n'}
        for ticket in ('A', 'B'):
            plan = {'schema_version': 2, 'ticket_id': ticket, 'plan_version': 1,
                'spec_ref': {'path': 'docs/spec.md', 'section': 'Implementation Decisions'},
                'contract_refs': [ticket + ':What to build', ticket + ':AC-1', ticket + ':AC-2', 'PRD:D1:Plan'],
                'blocked_by': [], 'verifications': [], 'steps': [], 'artifact_steps': [],
                'behavior_slices': []}
            for number in (1, 2):
                step, verify, ac = ticket + str(number), 'V-' + ticket + str(number), ticket + ':AC-' + str(number)
                plan['verifications'].append({'id': verify, 'behavior': ac,
                    'seam': 'original workflow_progress.handle',
                    'commands': ['python3 scripts/run_tests.py --test-file tests/runtime/test_layered_review.py']})
                plan['steps'].append({'id': step, 'target': {'path': ticket.lower() + str(number) + '.py',
                    'symbol': 'run', 'operation': 'add'}, 'capability': ac, 'approach': ['implement run'],
                    'inputs': ['frozen requirement'], 'outputs': ['observed result'], 'failures': ['invalid input fails'],
                    'depends_on': [], 'verification_refs': [verify], 'read_only': []})
                plan['behavior_slices'].append({'id': step, 'step_ids': [step], 'ac_refs': [ac],
                    'verification_refs': [verify], 'read_only': [], 'shared_state': []})
                if shared_verification and ticket == 'A' and number == 1:
                    shared = copy.deepcopy(plan['verifications'][-1])
                    shared['id'] = verify + '-shared'
                    plan['verifications'].append(shared)
                    plan['steps'][-1]['verification_refs'].append(shared['id'])
                    plan['behavior_slices'][-1]['verification_refs'].append(shared['id'])
            self.planning_documents['.scratch/layered/issues/' + ticket + '.md'] = (
                'Ticket ID: ' + ticket + '\nPlan Version: 1\n**What to build:** independent behavior\n'
                '**Blocked by:** None\nAC-1: first result\nAC-2: second result\n'
                '```execution-plan-v2\n' + json.dumps(plan) + '\n```\n')
        self.input['scope']['owned_paths'] = sorted(self.planning_documents)
        self.input['authorization']['scope_digest'] = fixture.transfer.entry.digest(self.input['scope'])
        design = self.finish_design('continuous')
        self.context = self.context_for(3)
        self.begin('continuous', self.staged_successor(design))
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        catalog = self.state()['scope_authority']['execution_plan']['catalog']
        for ticket in catalog['tickets']:
            for step in ticket['plan']['steps']:
                identity, path = step['id'], step['target']['path']
                request = self.execution_input()
                request['scope']['owned_paths'] = [path]
                request['authorization']['scope_digest'] = fixture.transfer.entry.digest(request['scope'])
                request['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, step)]
                self.invoke('allocation', {'allocation_id': identity, 'operation': 'prepare', 'handoff': request})
                slot = self.allocation_slot(identity, ticket['ticket_id'])
                native = 'native:executor-' + identity
                bound = self.invoke('allocation', {'allocation_id': identity, 'operation': 'bind',
                    'receipt': self.receipt(slot['record'], ref=native)})
                release = bound['next_action']['result']['write_release']
                body = b'def run():\n    return True\n'
                (self.flow / path).write_bytes(body)
                slot = self.allocation_slot(identity, ticket['ticket_id'])
                received = self.invoke('allocation', {'allocation_id': identity, 'operation': 'receive',
                    'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=native),
                    'result': {'delivery_id': identity + '-result', 'status': 'completed', 'payload': {
                        'changed_paths': [path], 'file_hashes': {path: hashlib.sha256(body).hexdigest()},
                        'tests': ['focused behavior passed'], 'write_release': release,
                        'plan_refs': request['semantic']['plan_refs'],
                        'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
                self.assertEqual(received['next_action']['result']['status'], 'received', received)
                accepted = self.invoke('allocation', {'allocation_id': identity, 'operation': 'accept',
                    'decision': {'reference': 'dispatcher:accept-' + identity}})
                self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
        self.settings['thread_id'] = 'task'
        self.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        self.flow_git('add', *self.implementation_paths)
        self.flow_git('commit', '-qm', 'four real execution deliveries')
        candidate = self.flow_git('rev-parse', 'HEAD')
        return candidate

    def local_verification(self, candidate, ticket='A', slice_id='A1'):
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = [{'id': 'V-' + slice_id + ':1', 'category': 'focused',
            'command': 'python3 scripts/run_tests.py --test-file tests/runtime/test_layered_review.py',
            'cwd': str(self.flow), 'pass_condition': 'exit 0', 'allowed_skips': [],
            'environment': None, 'status': 'passed', 'exit_code': 0,
            'start_commit': candidate, 'end_commit': candidate, 'environment_fingerprint': None,
            'output_ref': 'native-output:' + slice_id, 'output_digest': 'c' * 64}]
        if self.shared_verification and ticket == 'A' and slice_id == 'A1':
            shared = copy.deepcopy(checks[0])
            shared['id'] = 'V-A1-shared:1'
            checks.append(shared)
        source = {'adapter': 'fixture-command', 'call_ref': 'regression-call:' + slice_id,
            'response_ref': 'regression-stopped:' + slice_id,
            'raw': {'checks': copy.deepcopy(checks), 'source_unchanged': True,
                'stopped': True, 'dispatcher_ref': 'native:dispatcher', 'candidate': candidate,
                'run_id': progress.read_record(self.checkpoint)['current']['run_id'],
                'package_digest': fixture.transfer.entry.digest(self.state()['packages']),
                'plan_authority': copy.deepcopy(checkpoint['plan_authority']),
                'environment': {'adapter': 'fixture-command', 'cwd': str(self.flow)},
                'fixture': {'slice': slice_id, 'candidate': candidate}}}
        return {'plan_authority': checkpoint['plan_authority'], 'checks': checks, 'source': source}

    def local_review(self, candidate, ticket='A', slice_id='A1', reviewer=None):
        checkpoint = self.state()['control']['context']['handoff_progress']
        axis = 'local:' + ticket + ':' + slice_id
        verification = self.local_verification(candidate, ticket, slice_id)
        prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis,
            'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-review', prepared)
        semantic = {'status': 'accepted', 'findings': [], 'read_paths': [],
                    'ac_refs': [ticket + ':AC-' + slice_id[-1]], 'checks': verification['checks']}
        response_ref = 'local-stopped:' + slice_id
        observed = self.invoke('review-activity', {'operation': 'observe', 'axis': axis,
            'candidate': candidate, 'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'],
            'receipt': {'adapter': 'fixture-native', 'call_ref': 'local-call:' + slice_id,
                'response_ref': response_ref, 'status': 'stopped', 'ref': reviewer or 'native:reviewer-' + ticket,
                'raw': {'review': semantic}}})
        self.assertNotEqual(observed['status'], 'blocked', observed)
        result = self.invoke('control', {'action': 'local-review', 'evidence': {
            'axis': axis, 'candidate': candidate, 'result_ref': response_ref},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:local-' + slice_id}})
        self.assertTrue(result['next_action']['result']['ok'], result)
        return result['next_action']['result']['claim']

    def test_local_claim_blocks_unaccepted_ticket(self):
        candidate = self.layered_flow()
        claim = self.local_review(candidate)
        self.assertEqual(claim['layer'], 'local')
        self.assertEqual(claim['ticket_id'], 'A')
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['review_claims']['local:A:A1']['claim_id'], claim['claim_id'])
        plan = checkpoint['validation_plan']
        checks = fixture.transfer.checks_for(plan['review_required'], candidate)
        result = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher', 'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': checkpoint['git_baseline_commit'], 'binding': self.binding,
            'plan_digest': checkpoint['plan_digest'], 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'focused', 'response_ref': 'focused-stopped',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(result['status'], 'blocked', result)
        self.assertIn('Ticket acceptance', str(result))
        self.assertIsNone(self.state()['dispatch']['delivery'])
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'implementing')

    def test_local_review_rejects_failed_check_observations(self):
        candidate = self.layered_flow()
        verification = self.local_verification(candidate)
        verification['checks'][0].update(status='failed', exit_code=1)
        verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
        with self.assertRaises(progress.entry.PreparationError) as error:
            self.invoke('review-activity', {'operation': 'prepare', 'axis': 'local:A:A1',
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(error.exception.code, 'review_incomplete')
        self.assertNotIn('local:A:A1', self.state().get('review_activity', {}))
        self.assertNotIn('local:A:A1', self.state()['control']['context']['handoff_progress'].get('review_claims', {}))

    def test_local_review_rejects_stale_candidate_wrong_ref_and_command_source(self):
        candidate = self.layered_flow()
        old = self.state()['control']['context']['handoff_progress']['git_baseline_commit']
        variants = (
            ('stale candidate', lambda item: item['checks'][0].update(start_commit=old, end_commit=old)),
            ('wrong verification ref', lambda item: item['checks'][0].update(id='V-B1:1')),
            ('wrong frozen command', lambda item: item['checks'][0].update(command='python3 scripts/run_tests.py --test-file tests/runtime/test_solution_review_progress.py')),
            ('duplicate ref', lambda item: item['checks'].append(copy.deepcopy(item['checks'][0]))),
            ('unknown check', lambda item: item['checks'][0].update(status='unknown', exit_code=None)),
            ('old package source', lambda item: item['source']['raw'].update(package_digest='0' * 64)),
            ('other run source', lambda item: item['source']['raw'].update(run_id='other-run')),
            ('unstopped command', lambda item: item['source']['raw'].update(stopped=False)),
            ('old authority source', lambda item: item['source']['raw']['plan_authority'].update(revision=0)),
            ('missing original source', lambda item: item.pop('source')),
        )
        for label, mutate in variants:
            with self.subTest(label=label):
                verification = self.local_verification(candidate)
                mutate(verification)
                self.assertEqual(verification['plan_authority'],
                    self.state()['control']['context']['handoff_progress']['plan_authority'])
                if 'source' in verification:
                    verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
                with self.assertRaises(progress.entry.PreparationError) as error:
                    self.invoke('review-activity', {'operation': 'prepare', 'axis': 'local:A:A1',
                        'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
                self.assertEqual(error.exception.code, 'review_incomplete')
                self.assertNotIn('local:A:A1', self.state().get('review_activity', {}))
                self.assertNotIn('local:A:A1', self.state()['control']['context']['handoff_progress'].get('review_claims', {}))

    def test_shared_command_requires_each_frozen_verification_ref(self):
        candidate = self.layered_flow(shared_verification=True)
        verification = self.local_verification(candidate)
        verification['checks'].pop()
        verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
        with self.assertRaises(progress.entry.PreparationError) as error:
            self.invoke('review-activity', {'operation': 'prepare', 'axis': 'local:A:A1',
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(error.exception.code, 'review_incomplete')
        claim = self.local_review(candidate)
        self.assertEqual(claim['layer'], 'local')
        self.assertEqual(claim['ticket_id'], 'A')


for _name in dir(fixture.ProgressTests):
    if _name.startswith('test_') and _name not in LayeredReviewTests.__dict__:
        setattr(LayeredReviewTests, _name, None)
