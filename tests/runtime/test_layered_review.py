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
        response_ref = 'local-stopped:' + slice_id + ':' + candidate
        observed = self.invoke('review-activity', {'operation': 'observe', 'axis': axis,
            'candidate': candidate, 'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'],
            'receipt': {'adapter': 'fixture-native', 'call_ref': 'local-call:' + slice_id + ':' + candidate,
                'response_ref': response_ref, 'status': 'stopped', 'ref': reviewer or 'native:reviewer-' + ticket,
                'raw': {'review': semantic}}})
        self.assertNotEqual(observed['status'], 'blocked', observed)
        result = self.invoke('control', {'action': 'local-review', 'evidence': {
            'axis': axis, 'candidate': candidate, 'result_ref': response_ref},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:local-' + slice_id + ':' + candidate}})
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

    def ticket_review(self, candidate, ticket):
        first = self.local_verification(candidate, ticket, ticket + '1')
        second = self.local_verification(candidate, ticket, ticket + '2')
        first['checks'].extend(second['checks'])
        first['source']['raw']['checks'] = copy.deepcopy(first['checks'])
        first['source']['call_ref'] = 'ticket-check-call:' + ticket + ':' + candidate
        first['source']['response_ref'] = 'ticket-check-stop:' + ticket + ':' + candidate
        axis = 'ticket:' + ticket
        prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis,
            'candidate': candidate, 'actor_ref': 'task', 'verification': first})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-review', prepared)
        scope = prepared['next_action']['review_scope']
        receipt = {'adapter': 'fixture-native', 'call_ref': 'ticket-review-call:' + ticket + ':' + candidate,
            'response_ref': 'ticket-review-stop:' + ticket + ':' + candidate, 'status': 'stopped',
            'ref': 'native:reviewer-' + ticket,
            'raw': {'review': {'status': 'accepted', 'findings': [], 'read_paths': scope['read_paths'],
                'ac_refs': [ticket + ':AC-1', ticket + ':AC-2'], 'checks': first['checks'],
                'dependency_claim_ids': scope['dependency_claim_ids'],
                'requirement_refs': scope['requirement_refs'], 'review_scope': scope}}}
        self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
            'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'], 'receipt': receipt})
        accepted = self.invoke('control', {'action': 'ticket-accept', 'evidence': {
            'axis': axis, 'candidate': candidate, 'result_ref': receipt['response_ref']},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:ticket-' + ticket + ':' + candidate}})
        self.assertTrue(accepted['next_action']['result']['ok'], accepted)
        return accepted['next_action']['result']['claim']

    def test_ticket_acceptance_rejects_missing_ac_self_author_and_unknown_stop(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
        for ticket, native in (('A', 'native:reviewer-A'), ('B', 'native:executor-B1')):
            verification = self.local_verification(candidate, ticket, ticket + '1')
            verification['checks'].extend(self.local_verification(candidate, ticket, ticket + '2')['checks'])
            verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
            axis = 'ticket:' + ticket
            prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis,
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
            scope = prepared['next_action']['review_scope']
            result_ref = 'negative-ticket-stop:' + ticket
            report = {'status': 'accepted', 'findings': [], 'read_paths': scope['read_paths'],
                'ac_refs': [ticket + ':AC-1'] if ticket == 'A' else scope['ac_refs'],
                'checks': verification['checks'], 'dependency_claim_ids': scope['dependency_claim_ids'],
                'requirement_refs': scope['requirement_refs'], 'review_scope': scope}
            receipt = {'adapter': 'fixture-native', 'call_ref': 'negative-ticket-call:' + ticket,
                'response_ref': result_ref, 'status': 'stopped', 'ref': native, 'raw': {'review': report}}
            observed = self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'], 'receipt': receipt})
            rejected = self.invoke('control', {'action': 'ticket-accept', 'evidence': {
                'axis': axis, 'candidate': candidate, 'result_ref': receipt['response_ref']},
                'receipt': {'controller_ref': 'task', 'reference': 'negative-ticket-settled:' + ticket}})
            self.assertEqual(rejected['status'], 'blocked', rejected)
            self.assertEqual(rejected['error']['code'], 'review_incomplete' if ticket == 'A' else 'identity_mismatch')
            print(json.dumps({'native_ticket_negative': ticket, 'error': rejected['error'],
                'ticket_granted': axis in self.state()['control']['context']['handoff_progress'].get('review_claims', {})}, sort_keys=True))
            self.assertNotIn(axis, self.state()['control']['context']['handoff_progress'].get('review_claims', {}))

    def test_ticket_and_delivery_certificate_preserve_unchanged_ticket_claims(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
        accepted_a = self.ticket_review(candidate, 'A')
        accepted_b = self.ticket_review(candidate, 'B')
        self.assertEqual(accepted_a['layer'], 'ticket')
        self.assertEqual(accepted_b['layer'], 'ticket')
        review = self.delivery_reviews(candidate)
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertTrue(checkpoint['coverage']['delivery'])
        retained_b = copy.deepcopy(checkpoint['review_claims']['ticket:B'])
        old_b_claim = progress.state_store.StateStore(self.checkpoint).read_evidence(retained_b['claim_ref'],
            run_id=retained_b['claim_ref']['run_id'])['value']['review_claim']
        retired = self.invoke('control', {'action': 'invalidate-candidate', 'evidence': {
            'candidate': candidate, 'reason': 'legal local A repair', 'reference': 'controller:repair-A'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:repair-A'}})
        self.assertTrue(retired['next_action']['result']['ok'], retired)
        self.repair_a()
        target = self.flow_git('rev-parse', 'HEAD')
        applied = self.invoke('control', {'action': 'review-applicability', 'evidence': {
            'candidate': target, 'reference': 'controller:target-applicability'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:target-applicability'}})
        proof = applied['next_action']['result']['applicability']
        self.assertIn(retained_b['claim_id'], proof['retained'])
        self.assertIn(accepted_a['claim_id'], proof['affected'])
        self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims']['ticket:B'], retained_b)
        current_b_claim = progress.state_store.StateStore(self.checkpoint).read_evidence(retained_b['claim_ref'],
            run_id=retained_b['claim_ref']['run_id'])['value']['review_claim']
        self.assertEqual(current_b_claim, old_b_claim)
        self.assertEqual(current_b_claim['native_stop_ref'], old_b_claim['native_stop_ref'])
        self.local_review(target, 'A', 'A1')
        self.ticket_review(target, 'A')
        reviews = self.delivery_reviews(target)
        self.inherit_empty_candidate(target, reviews)

    def candidate_ready(self, candidate):
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = fixture.transfer.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        ready = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher', 'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': checkpoint['git_baseline_commit'], 'binding': self.binding,
            'plan_digest': checkpoint['plan_digest'], 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'early:' + candidate, 'response_ref': 'early-stop:' + candidate,
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}})
        self.assertTrue(ready['next_action']['result']['ok'], ready)
        return checks

    def delivery_reviews(self, candidate):
        checks = self.candidate_ready(candidate)
        checkpoint = self.state()['control']['context']['handoff_progress']
        reviews = {}
        for axis in ('standards', 'spec'):
            prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'verification': {'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
                    'plan_authority': checkpoint['plan_authority'], 'checks': checks}})
            scope = prepared['next_action']['review_scope']
            self.assertEqual(scope['mode'], 'cross-ticket-and-coverage')
            self.assertEqual(scope['integration_delta_paths'], [])
            response = axis + ':stopped:' + candidate
            self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'],
                'receipt': {'adapter': 'fixture-native', 'call_ref': axis + ':call:' + candidate,
                    'response_ref': response, 'status': 'stopped', 'ref': 'native:delivery-' + axis,
                    'raw': {'review': {'status': 'accepted', 'findings': [], 'ac_refs': scope['ac_refs'],
                        'requirement_refs': scope['requirement_refs'], 'dependency_claim_ids': scope['dependency_claim_ids'],
                        'review_scope': scope}}}})
            reviews[axis] = {'candidate': candidate, 'expected_target_head': checkpoint['expected_target_head'],
                'plan_digest': checkpoint['plan_digest'], 'plan_authority': checkpoint['plan_authority'],
                'reviewer_ref': 'native:delivery-' + axis, 'status': 'accepted', 'result_ref': response}
        converged = self.invoke('control', {'action': 'review-converged', 'evidence': {
            'candidate': candidate, 'review': reviews, 'reference': 'controller:delivery:' + candidate},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:delivery:' + candidate}})
        self.assertTrue(converged['next_action']['result']['ok'], converged)
        return reviews

    def inherit_empty_candidate(self, candidate, reviews):
        before_views = copy.deepcopy(self.state()['control']['context']['handoff_progress']['review_claims'])
        self.invoke('control', {'action': 'invalidate-candidate', 'evidence': {'candidate': candidate,
            'reason': 'empty commit metadata', 'reference': 'controller:empty'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:empty'}})
        self.flow_git('commit', '--allow-empty', '-qm', 'empty candidate metadata')
        target = self.flow_git('rev-parse', 'HEAD')
        self.candidate_ready(target)
        converged = self.invoke('control', {'action': 'review-converged', 'evidence': {
            'candidate': target, 'review': reviews, 'reference': 'controller:inherited-delivery'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:inherited-delivery'}})
        self.assertTrue(converged['next_action']['result']['ok'], converged)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims'], before_views)
        self.assertTrue(all(report['candidate'] == candidate for report in reviews.values()))
        self.finish_certificate_delivery(target, reviews)

    def repair_a(self):
        ticket = self.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        step = ticket['plan']['steps'][0]
        request = self.execution_input()
        request['scope']['owned_paths'] = ['a1.py']
        request['authorization']['scope_digest'] = fixture.transfer.entry.digest(request['scope'])
        request['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, step)]
        self.invoke('allocation', {'allocation_id': 'A1-repair', 'operation': 'prepare', 'handoff': request})
        slot = self.allocation_slot('A1-repair', 'A')
        bound = self.invoke('allocation', {'allocation_id': 'A1-repair', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:executor-A1-repair')})
        release = bound['next_action']['result']['write_release']
        body = b'def run():\n    return 2\n'
        (self.flow / 'a1.py').write_bytes(body)
        slot = self.allocation_slot('A1-repair', 'A')
        self.invoke('allocation', {'allocation_id': 'A1-repair', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor-A1-repair'),
            'result': {'delivery_id': 'A1-repair-result', 'status': 'completed', 'payload': {
                'changed_paths': ['a1.py'], 'file_hashes': {'a1.py': hashlib.sha256(body).hexdigest()},
                'tests': ['repair behavior passed'], 'write_release': release,
                'plan_refs': request['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.invoke('allocation', {'allocation_id': 'A1-repair', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accept-A1-repair'}})
        self.settings['thread_id'] = 'task'
        self.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        self.flow_git('add', 'a1.py')
        self.flow_git('commit', '-qm', 'legal A1 repair')

    def finish_certificate_delivery(self, candidate, reviews):
        checkpoint = self.state()['control']['context']['handoff_progress']
        start = {'attempt_id': 'second-final', 'dispatcher_ref': 'native:dispatcher',
            'carrier_attempt': self.state()['control']['context']['carrier']['attempt'],
            **{key: checkpoint[key] for key in ('candidate', 'expected_target_head', 'plan_digest', 'plan_authority', 'review_digest')}}
        self.invoke('control', {'action': 'validation-start', 'evidence': start,
            'receipt': {'controller_ref': 'task', 'reference': 'controller:final-start'}})
        checks = fixture.transfer.checks_for(checkpoint['validation_plan']['final_required'], candidate)
        complete = self.invoke('control', {'action': 'validation-result', 'evidence': {'attempt_id': 'second-final', 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'final-call', 'response_ref': 'final-stop',
                'raw': {'attempt_id': 'second-final', 'checks': checks, 'source_unchanged': True,
                    'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'deliverable', complete)
        verification = progress.control.delivery_verification(self.state()['control']['context']['handoff_progress'], 'native:dispatcher')
        payload = {'candidate_commit': candidate, 'artifacts': self.implementation_paths, 'checks': ['complete candidate'],
            'review': reviews, 'verification': verification}
        observed = self.invoke('observe', self.observation('stopped', 'result', {
            'delivery_id': 'second-delivery', 'status': 'completed', 'payload': payload}, ref='native:dispatcher'))
        self.assertEqual(observed['status'], 'needs_input', observed)
        pending = observed['pending']
        accepted = self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:second-accepted'})
        self.assertEqual(accepted['status'], 'accepted', accepted)
        predecessor = progress.implementation_predecessor(self.checkpoint)
        stage3 = fixture.transfer.handoff.accepted_stage3_progress(predecessor, str(self.checkpoint))
        self.assertEqual(stage3['coverage']['candidate'], candidate)
        frozen, checkpoint = progress.implementation_acceptance(predecessor)
        self.assertEqual(checkpoint['coverage'], verification['coverage'])
        before = progress.state_store.StateStore(self.checkpoint).directory
        objects = sorted(str(path) for path in (before / 'objects').rglob('*') if path.is_file())
        for _ in range(3):
            self.invoke('inspect')
        self.assertEqual(sorted(str(path) for path in (before / 'objects').rglob('*') if path.is_file()), objects)
        bad = copy.deepcopy(verification['coverage'])
        bad['certificate_digest'] = '0' * 64
        with self.assertRaises(progress.control.ControlError):
            progress.control_git.verify_stored_review_coverage(bad, checkpoint, self.state()['packages'],
                progress.read_record(self.checkpoint)['current']['run_id'])
        self.context = self.context_for(4)
        self.request['host'].pop('actor_ref', None)
        successor = self.input_for(4, self.state()['accepted'])
        successor['scope']['protected_paths'] = sorted(set(frozen['handoff']['scope']['protected_paths']) -
            set(successor['scope']['closure_paths']))
        successor['authorization']['scope_digest'] = fixture.transfer.entry.digest(successor['scope'])
        self.begin('continuous', successor)
        after_transfer = progress.closure_delivery_view(predecessor)
        self.assertEqual(after_transfer['view']['coverage']['candidate'], candidate)


for _name in dir(fixture.ProgressTests):
    if _name.startswith('test_') and _name not in LayeredReviewTests.__dict__:
        setattr(LayeredReviewTests, _name, None)
