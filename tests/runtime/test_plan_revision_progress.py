"""Controller plan revision at the durable C and real Git boundary."""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_scope_progress as fixture
import workflow_progress as progress
import test_layered_review as layered_fixture
from unittest.mock import patch


class T04Flow(layered_fixture.LayeredReviewTests):
    def finish_design(self, mode):
        if getattr(self, 'revision_shared_graph', False):
            larger = getattr(self, 'revision_capacity_diagnostic', False)
            self.planning_documents['docs/spec.md'] = (
                'approved design\n## Implementation Decisions\n### D1 — Plan\n'
                'Declared shared and prerequisite relationships across Tickets.\n')
            for ticket in ('A', 'B'):
                path = '.scratch/layered/issues/' + ticket + '.md'
                body = self.planning_documents[path]
                match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
                plan = json.loads(match.group(1))
                if ticket == 'A':
                    plan['behavior_slices'][0]['shared_state'] = ([] if getattr(self, 'revision_reference_fault', False) else ['b1.py'])
                    if not larger:
                        plan['blocked_by'] = ['B']
                        plan['steps'][1]['depends_on'] = ['B1']
                        body = body.replace('**Blocked by:** None', '**Blocked by:** B')
                        match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
                else:
                    if larger:
                        plan['steps'][1]['depends_on'] = ['B1']
                    else:
                        plan['behavior_slices'][1]['shared_state'] = ['.scratch/layered/issues/A.md']
                self.planning_documents[path] = body[:match.start(1)] + json.dumps(plan) + body[match.end(1):]
            for ticket in (('C', 'D', 'E') if larger else ('C',)):
                path = ticket.lower() + '1.py'
                self.implementation_paths.append(path)
                plan = {'schema_version': 2, 'ticket_id': ticket, 'plan_version': 1,
                    'spec_ref': {'path': 'docs/spec.md', 'section': 'Implementation Decisions'},
                    'contract_refs': [ticket + ':What to build', ticket + ':AC-1', 'PRD:D1:Plan'],
                    'blocked_by': ['C'] if larger and ticket == 'D' else [], 'artifact_steps': [],
                    'verifications': [{'id': 'V-' + ticket + '1', 'behavior': ticket + ':AC-1',
                        'seam': 'original workflow_progress.handle',
                        'commands': ['python3 scripts/run_tests.py --test-file tests/runtime/test_layered_review.py']}],
                    'steps': [{'id': ticket + '1', 'target': {'path': path, 'symbol': 'run', 'operation': 'add'},
                        'capability': ticket + ':AC-1', 'approach': ['implement run'],
                        'inputs': ['frozen requirement'], 'outputs': ['observed result'], 'failures': ['invalid input fails'],
                        'depends_on': ['C1'] if larger and ticket == 'D' else [],
                        'verification_refs': ['V-' + ticket + '1'], 'read_only': []}],
                    'behavior_slices': [{'id': ticket + '1', 'step_ids': [ticket + '1'], 'ac_refs': [ticket + ':AC-1'],
                        'verification_refs': ['V-' + ticket + '1'], 'read_only': [],
                        'shared_state': ['.scratch/layered/issues/A.md'] if larger and ticket == 'C' else []}]}
                self.planning_documents['.scratch/layered/issues/' + ticket + '.md'] = (
                    'Ticket ID: ' + ticket + '\nPlan Version: 1\n**What to build:** approved shared behavior\n'
                    '**Blocked by:** ' + ('C' if larger and ticket == 'D' else 'None') +
                    '\nAC-1: observed result\n```execution-plan-v2\n' + json.dumps(plan) + '\n```\n')
            self.input['scope']['owned_paths'] = sorted(self.planning_documents)
            self.input['authorization']['scope_digest'] = progress.entry.digest(self.input['scope'])
        return super().finish_design(mode)

    def propose_design_scope(self, candidate, section='approved design'):
        saved = self.state()
        basis = {'commit': candidate, 'path': 'docs/spec.md', 'section': section}
        manifest = [{'path': path, 'owner': 'implementation', 'kind': 'source',
                     'operation': 'add', 'basis': basis} for path in self.implementation_paths]
        manifest += [{'path': path, 'owner': 'closure', 'kind': 'document',
                      'operation': 'add', 'basis': basis} for path in self.planning_documents]
        proposal = {'proposal_id': 'proposal-1', 'expected_revision': 0,
            'candidate_commit': candidate, 'manifest': sorted(manifest, key=lambda item: item['path']),
            'implementation_required': True, 'requirement_identity': saved['handoff']['requirement_identity'],
            'binding': saved['handoff']['binding'], 'previous_scope_revision': 0}
        proposed = self.invoke('scope-propose', proposal)
        self.assertEqual(proposed['next_action']['operation'], 'scope-proposed', proposed)
        return proposed['next_action']['proposal']['digest']


class PlanRevisionProgressTests(unittest.TestCase):
    @staticmethod
    def refresh_fixture_slices(plan):
        # Preserve the original fixture's complete allocation groups while
        # updating their declared reads/verifications after a valid revision.
        steps = {step['id']: step for step in plan['steps'] + plan['artifact_steps']}
        groups = []
        covered = set()
        for group in plan['behavior_slices']:
            members = [identity for identity in group['step_ids'] if identity in steps]
            if not members:
                continue
            groups.append({**group, 'step_ids': members})
            covered.update(members)
        ac_refs = sorted({ref for group in plan['behavior_slices'] for ref in group['ac_refs']})
        groups.extend({'id': identity, 'step_ids': [identity], 'ac_refs': ac_refs,
            'shared_state': []} for identity in steps if identity not in covered)
        for group in groups:
            selected = [steps[identity] for identity in group['step_ids']]
            group['verification_refs'] = sorted({value for step in selected for value in step['verification_refs']})
            group['read_only'] = sorted({value for step in selected for value in step.get('read_only', [])})
        plan['behavior_slices'] = groups

    def t04_inline_authority(self, *, shared=None, same_file=False, read=False,
                             reverse_shared=False, planning_shared=False):
        count = 4 if reverse_shared or planning_shared else 2
        plan = {'schema_version': 2, 'ticket_id': 'PRIVATE', 'plan_version': 1,
            'spec_ref': {'path': 'inline', 'section': 'brief'},
            'contract_refs': ['PRIVATE:AC-' + str(number) for number in range(1, count + 1)],
            'blocked_by': [], 'artifact_steps': [], 'steps': [], 'verifications': [], 'behavior_slices': []}
        for number in range(1, count + 1):
            identity, path, verify = 'S' + str(number), 'private' + str(number) + '.py', 'V' + str(number)
            plan['steps'].append({'id': identity, 'target': {'path': path, 'symbol': 'run', 'operation': 'add'},
                'capability': 'approved behavior', 'approach': ['implement private helper'],
                'inputs': ['input'], 'outputs': ['result'], 'failures': ['invalid input'],
                'depends_on': [], 'read_only': [], 'verification_refs': [verify]})
            plan['verifications'].append({'id': verify, 'behavior': 'approved behavior',
                'seam': 'public module', 'commands': ['focused check']})
            plan['behavior_slices'].append({'id': identity, 'step_ids': [identity],
                'ac_refs': ['PRIVATE:AC-' + str(number)], 'verification_refs': [verify],
                'read_only': [], 'shared_state': [shared] if number == 2 and shared else []})
        if same_file:
            plan['steps'][1]['target'].update(path='private1.py', symbol='neighbor')
        if read:
            plan['steps'][1]['read_only'] = ['private1.py']
            plan['behavior_slices'][1]['read_only'] = ['private1.py']
        if reverse_shared:
            plan['behavior_slices'][0]['shared_state'] = ['private2.py']
        if planning_shared:
            plan['behavior_slices'][1]['shared_state'] = ['docs/private-plan.md']
        if reverse_shared or planning_shared:
            plan['steps'][2]['depends_on'] = ['S2']
        paths = ['private1.py'] if same_file else ['private' + str(number) + '.py' for number in range(1, count + 1)]
        scope = {'baseline': 'a' * 40, 'implementation_paths': paths,
            'protected_paths': ['docs/private-plan.md'] if planning_shared else [],
            'downstream': {'revision': 1, 'manifest': [
                {'path': path, 'owner': 'implementation', 'kind': 'source', 'operation': 'add'}
                for path in paths]}}
        semantic = {'objective': 'approved behavior', 'testing_basis': 'public module',
            'completion_criteria': ['results match'], 'constraints': ['private implementation']}
        return progress.execution_plan.inline_authority(plan, scope, semantic,
            {'sha256': 'b' * 64}, input_digest='c' * 64)

    def test_t04_shared_closure_waits_for_paths_and_protected_planning_consumers(self):
        for variant, changes in (({'reverse_shared': True}, []),
                                 ({'planning_shared': True}, ['docs/private-plan.md'])):
            with self.subTest(variant=variant):
                old = self.t04_inline_authority(**variant)
                plan = copy.deepcopy(progress.execution_plan.current_catalog(old)['tickets'][0]['plan'])
                plan['plan_version'] = 2
                plan['steps'][0]['approach'].append('change the private helper')
                scope = copy.deepcopy(old['scope'])
                scope['downstream']['revision'] = 2
                new = progress.execution_plan.inline_authority(plan, scope,
                    {'objective': 'approved behavior', 'testing_basis': 'public module',
                     'completion_criteria': ['results match'], 'constraints': ['private implementation']},
                    {'sha256': 'b' * 64}, input_digest='c' * 64)
                impact = progress.execution_plan.affected_plan_closure(old, new, changes)
                self.assertEqual(impact['impacted_step_ids'], ['S1', 'S2', 'S3'])
                self.assertEqual(impact['verification_ids'], ['V1', 'V2', 'V3'])
                self.assertNotIn('private4.py', impact['paths'])

    def test_t04_private_revision_closes_over_slice_shared_state_and_unknown(self):
        for variant, expected in (({}, ['S1']), ({'shared': 'private1.py'}, ['S1', 'S2']),
                ({'shared': 'unmapped-registry'}, ['S1', 'S2']), ({'same_file': True}, ['S1', 'S2']),
                ({'read': True}, ['S1', 'S2']), ({'verification': True}, ['S1', 'S2'])):
            with self.subTest(variant=variant):
                old = self.t04_inline_authority(**{key: value for key, value in variant.items() if key != 'verification'})
                plan = copy.deepcopy(progress.execution_plan.current_catalog(old)['tickets'][0]['plan'])
                plan['plan_version'] = 2
                plan['steps'][0]['approach'].append('split the private helper')
                if variant.get('verification'):
                    plan['verifications'][1]['commands'] = ['changed focused check']
                new_scope = copy.deepcopy(old['scope'])
                new_scope['downstream']['revision'] = 2
                new = progress.execution_plan.inline_authority(plan, new_scope,
                    {'objective': 'approved behavior', 'testing_basis': 'public module',
                     'completion_criteria': ['results match'], 'constraints': ['private implementation']},
                    {'sha256': 'b' * 64}, input_digest='c' * 64)
                impact = progress.execution_plan.affected_plan_closure(old, new, [])
                self.assertEqual(impact['impacted_step_ids'], expected)

    def test_t04_shared_revision_retires_results_and_claims_through_both_graphs(self):
        self.t04_shared_revision()

    def test_t04_shared_revision_keeps_five_tickets_and_seven_allocations_within_capacity(self):
        self.t04_shared_revision(larger=True)

    def test_t04_resume_reference_failure_keeps_original_slots(self):
        self.t04_shared_revision(reference_fault=True)

    def test_t04_resume_reference_failure_preserves_seven_original_slots(self):
        self.t04_shared_revision(larger=True, reference_fault=True)

    def t04_shared_revision(self, *, larger=False, reference_fault=False):
        flow = T04Flow(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.revision_shared_graph = True
        flow.revision_capacity_diagnostic = larger
        flow.revision_reference_fault = reference_fault
        candidate = flow.layered_flow()
        if larger:
            for ticket, slice_id in (('A', 'A1'), ('B', 'B1'), ('B', 'B2'), ('C', 'C1')):
                flow.local_review(candidate, ticket, slice_id)
            verification = flow.local_verification(candidate, 'C', 'C1')
            verification['source'].update(call_ref='ticket-C-checks', response_ref='ticket-C-checks-stopped')
            prepared = flow.invoke('review-activity', {'operation': 'prepare', 'axis': 'ticket:C',
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
            self.assertEqual(prepared['next_action']['operation'], 'invoke-review', prepared)
            scope = prepared['next_action']['review_scope']
            receipt = {'adapter': 'fixture-native', 'call_ref': 'ticket-C-review',
                'response_ref': 'ticket-C-review-stopped', 'status': 'stopped', 'ref': 'native:reviewer-C',
                'raw': {'review': {'status': 'accepted', 'findings': [], 'read_paths': scope['read_paths'],
                    'ac_refs': scope['ac_refs'], 'checks': verification['checks'],
                    'dependency_claim_ids': scope['dependency_claim_ids'],
                    'requirement_refs': scope['requirement_refs'], 'review_scope': scope}}}
            flow.invoke('review-activity', {'operation': 'observe', 'axis': 'ticket:C', 'candidate': candidate,
                'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'], 'receipt': receipt})
            accepted = flow.invoke('control', {'action': 'ticket-accept', 'evidence': {
                'axis': 'ticket:C', 'candidate': candidate, 'result_ref': receipt['response_ref']},
                'receipt': {'controller_ref': 'task', 'reference': 'controller:ticket-C'}})
            self.assertTrue(accepted['next_action']['result']['ok'], accepted)
            for ticket in ('D', 'E'):
                flow.local_review(candidate, ticket, ticket + '1')
        else:
            for number in (1, 2):
                flow.local_review(candidate, 'B', 'B' + str(number))
            flow.ticket_review(candidate, 'B')
            for ticket, slice_id in (('A', 'A1'), ('A', 'A2'), ('C', 'C1')):
                flow.local_review(candidate, ticket, slice_id)
        before = flow.state()
        checkpoint = before['control']['context']['handoff_progress']
        views = copy.deepcopy(checkpoint['review_claims'])
        store = progress.state_store.StateStore(flow.checkpoint)
        current = store.open_current()
        sharing_axis = 'local:C:C1' if larger else 'local:B:B2'
        shared_claim = store.read_evidence(views[sharing_axis]['claim_ref'], run_id=current.run_id)['value']['review_claim']
        self.assertIn('.scratch/layered/issues/A.md', shared_claim['read_fingerprints'])
        unaffected = 'E' if larger else 'C'
        axis = 'local:' + unaffected + ':' + unaffected + '1'
        original = store.read_evidence(views[axis]['claim_ref'], run_id=current.run_id)['value']
        original_result = next(item for item in checkpoint['executions'] if item['agent_ref'] == 'native:executor-' + unaffected + '1')
        authority = before['scope_authority']
        path = '.scratch/layered/issues/A.md'
        report = {'revision_id': 't04-shared', 'expected_authority_revision': authority['revision'],
            'expected_authority_digest': progress.entry.digest(authority), 'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:t04-shared'}, 'planning_paths': [path],
            'report': {'ticket_ids': ['A'], 'step_ids': ['A1'], 'reason': 'private helper adjustment',
                'production_facts': 'shared implementation and planning consumers stay in the approved scope',
                'contract_before': authority['execution_plan']['contract_digest'],
                'contract_after': authority['execution_plan']['contract_digest'], 'dependency_impact': ['A1'],
                'verification_impact': ['V-A1'], 'carry_forward': ['A2', 'E1'] if larger else ['C1']}}
        flow.invoke('prepare-plan-revision', report)
        prepared = flow.invoke('observe', flow.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        body = (flow.flow / path).read_text()
        match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan['plan_version'] = 2
        plan['steps'][0]['approach'].append('adjust the private helper')
        (flow.flow / path).write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                                    .replace('Plan Version: 1', 'Plan Version: 2'))
        flow.flow_git('add', path)
        flow.flow_git('commit', '--only', '-qm', 'private shared plan revision', '--', path)
        commit = {'revision_id': report['revision_id'], 'controller_ref': 'task',
            'write_release': prepared['next_action']['write_release'], 'planning_commit': flow.flow_git('rev-parse', 'HEAD')}
        original_commit = progress.state_store.StateStore.commit_current
        measured = []
        def observe_capacity(owner, **kwargs):
            current = owner.open_current()
            run_id = current.run_id
            for name, value in kwargs['subjects'].items():
                size = len(progress.state_store.encode({'format': progress.state_store.FORMAT,
                    'run_id': run_id, 'kind': 'subject', 'subject_id': name, 'value': value}))
                if name == kwargs['metadata']['stage_subject'] and not measured and (
                        size > progress.state_store.SUBJECT_LIMIT or
                        (value.get('plan_revision_transaction') or {}).get('state') == 'activated'):
                    previous = owner.read_subject(current, name).value
                    fields = lambda item: {key: len(progress.state_store.encode(body)) for key, body in item.items()}
                    row = {'subject': name, 'limit': progress.state_store.SUBJECT_LIMIT,
                        'before_bytes': current.root['subjects'][name]['size'], 'candidate_bytes': size,
                        'before_fields': fields(previous), 'candidate_fields': fields(value),
                        'before_progress_fields': fields(previous['control']['context']['handoff_progress']),
                        'candidate_progress_fields': fields(value['control']['context']['handoff_progress'])}
                    measured.append(row)
                    print('T04_CAPACITY_ACTUAL ' + json.dumps(row, sort_keys=True))
            return original_commit(owner, **kwargs)
        with patch.object(progress.state_store.StateStore, 'commit_current', observe_capacity):
            if reference_fault:
                original_atomic = progress.state_store.atomic_write
                def fail_reference(number):
                    seen = []
                    def write(path, data):
                        body = json.loads(data)
                        if isinstance(body.get('value'), dict) and 'plan_revision_allocation' in body['value']:
                            seen.append(path)
                            if len(seen) == number:
                                raise OSError('reference write interrupted at ' + str(number))
                        return original_atomic(path, data)
                    return seen, write
                seen, write = fail_reference(1)
                with patch.object(progress.state_store, 'atomic_write', write):
                    with self.assertRaisesRegex(OSError, 'reference write interrupted'):
                        flow.invoke('commit-plan-revision', commit)
                self.assertEqual(len(seen), 1)
                pending = flow.state()
                self.assertEqual(pending['scope_authority'], before['scope_authority'])
                self.assertEqual(pending['allocations'], before['allocations'])
                self.assertEqual(pending['plan_revision_transaction']['state'], 'planning-committed')
                intent = next(value['request'] for value in pending['control_transactions'].values()
                              if value['request']['action'] == 'plan-revision-activate')
                seen, write = fail_reference(3)
                with patch.object(progress.state_store, 'atomic_write', write):
                    resumed = flow.invoke('resume')
                self.assertEqual(len(seen), 3)
                self.assertEqual(resumed['status'], 'blocked', resumed)
                stopped = flow.state()
                self.assertEqual(stopped['scope_authority'], before['scope_authority'])
                self.assertEqual(stopped['allocations'], before['allocations'])
                self.assertEqual(stopped['plan_revision_transaction']['state'], 'planning-committed')
                self.assertEqual(stopped.get('plan_revision_allocation_history', []), [])
                self.assertTrue(any(value['request'] == intent and value['result'] is None
                                    for value in stopped['control_transactions'].values()))
                original_write = progress.state_store.StateStore.write_object
                original_charge = progress.state_store.StateStore._charge
                active, charged = [0], []
                def mark_reference(owner, run_id, kind, value, **kwargs):
                    previous = active[0]
                    if kind == 'evidence' and isinstance(value, dict) and 'plan_revision_allocation' in value:
                        active[0] = len(charged) + 1
                        charged.append(value)
                    try:
                        return original_write(owner, run_id, kind, value, **kwargs)
                    finally:
                        active[0] = previous
                failed_charge = []
                def fail_charge(owner, size, **kwargs):
                    if active[0] == 4 and not failed_charge:
                        failed_charge.append(True)
                        raise progress.entry.PreparationError('state_capacity', 'reference charge interrupted')
                    return original_charge(owner, size, **kwargs)
                with patch.object(progress.state_store.StateStore, 'write_object', mark_reference), \
                     patch.object(progress.state_store.StateStore, '_charge', fail_charge):
                    charged_result = flow.invoke('resume')
                self.assertEqual(failed_charge, [True])
                self.assertEqual(charged_result['status'], 'blocked', charged_result)
                stopped = flow.state()
                self.assertEqual(stopped['scope_authority'], before['scope_authority'])
                self.assertEqual(stopped['allocations'], before['allocations'])
                self.assertEqual(stopped['plan_revision_transaction']['state'], 'planning-committed')
                self.assertEqual(stopped.get('plan_revision_allocation_history', []), [])
                self.assertTrue(any(value['request'] == intent and value['result'] is None
                                    for value in stopped['control_transactions'].values()))
                result = flow.invoke('resume')
            else:
                result = flow.invoke('commit-plan-revision', commit)
        self.assertIn(result['next_action']['operation'], {'plan-revision-activated', 'control-effects'}, result)
        self.assertEqual(len(measured), 1)
        self.assertLessEqual(measured[0]['candidate_bytes'], progress.state_store.SUBJECT_LIMIT)
        destination = os.environ.get('T04_REVISION_CAPACITY_METRICS_PATH')
        if destination and larger:
            with Path(destination).open('x') as output:
                json.dump(measured[0], output, sort_keys=True)
                output.write('\n')
        after = flow.state()['control']['context']['handoff_progress']
        impact = after['plan_revisions'][-1]['evidence']['retention']['impact']
        expected_steps = (['A1', 'C1', 'D1'] if larger else ['B2', 'A1', 'A2']) if reference_fault else (
            ['A1', 'B1', 'B2', 'C1', 'D1'] if larger else ['B1', 'B2', 'A1', 'A2'])
        self.assertEqual(impact['impacted_step_ids'], expected_steps)
        self.assertEqual(impact['verification_ids'], sorted('V-' + item for item in expected_steps))
        carried_steps = {'A2', 'B1', 'B2', 'E1'} if larger else {'B1', 'C1'}
        self.assertEqual({ref['step_id'] for item in after['carried_results'] for ref in item['plan_refs']},
                         carried_steps if reference_fault else {'A2', 'E1'} if larger else {'C1'})
        retained_axes = {axis, 'local:B:B1'} | ({'local:B:B2'} if larger else set()) if reference_fault else {axis}
        self.assertEqual(set(after['review_claims']), retained_axes)
        self.assertEqual(after['review_applicability']['affected'], sorted(view['claim_id']
            for name, view in views.items() if name not in retained_axes))
        application = store.read_evidence(after['review_applicability']['reference'], run_id=current.run_id)['value']['applications'][sharing_axis]
        self.assertNotEqual(application['compared']['read_fingerprints']['.scratch/layered/issues/A.md'],
                            shared_claim['read_fingerprints']['.scratch/layered/issues/A.md'])
        self.assertEqual(after['review_claims'][axis], views[axis])
        self.assertEqual(store.read_evidence(views[axis]['claim_ref'], run_id=current.run_id)['value'], original)
        carried = next(item for item in after['carried_results'] if item['agent_ref'] == 'native:executor-' + unaffected + '1')
        self.assertEqual(carried['source_result_digest'], progress.entry.digest(original_result))
        self.assertEqual(carried['owned_final_fingerprints'], progress.execution_plan.accepted_owned_fingerprints(original_result))
        self.assertEqual(after['plan_revision_sources'][-1]['executions'], checkpoint['executions'])
        history = flow.state()['plan_revision_allocation_history'][-1]['allocations']
        self.assertEqual(len(history), 7 if larger else 5)
        for identity, reference in history.items():
            slot = store.read_evidence(reference['reference'], run_id=current.run_id)['value']['plan_revision_allocation']
            self.assertEqual(slot, before['allocations'][identity])
        if reference_fault:
            replay = flow.invoke('commit-plan-revision', commit, revision=0)
            self.assertTrue(replay['acknowledged'], replay)
            self.assertEqual(len(flow.state()['control']['context']['handoff_progress']['plan_revisions']), 1)
            candidate = flow.flow_git('rev-parse', 'HEAD')
            flow.early_ticket_check(candidate, 'B')
            request = flow.execution_input()
            ticket = next(item for item in flow.state()['scope_authority']['execution_plan']['catalog']['tickets']
                          if item['ticket_id'] == 'B')
            request['scope']['owned_paths'] = ['b1.py']
            request['authorization']['scope_digest'] = progress.entry.digest(request['scope'])
            request['semantic']['plan_refs'] = [progress.execution_plan.step_ref(ticket, ticket['plan']['steps'][0])]
            continued = flow.invoke('allocation', {'allocation_id': 'B1', 'operation': 'repair',
                'invocation_id': 'B1-after-reference-failure', 'handoff': request})
            self.assertIsNotNone(continued['next_action'], continued)
            self.assertEqual(continued['next_action']['operation'], 'invoke-host', continued)
            self.assertEqual(continued['next_action']['payload']['payload']['execution_invocation']['ref'], 'native:executor-B1')
            old_slot = before['allocations']['B1']
            slot = flow.allocation_slot('B1', 'B')
            with self.assertRaises(progress.entry.PreparationError):
                progress.dispatch.validate_allocation_receipt(slot['record'], 'bind',
                    flow.receipt(old_slot['record'], ref='native:executor-B1'), flow.state()['control']['context'])
            receipt = flow.receipt(slot['record'], ref='native:executor-B1')
            receipt['receipt_ref'] = 'B1-after-reference-failure:ready'
            bound = flow.invoke('allocation', {'allocation_id': 'B1', 'operation': 'bind', 'receipt': receipt})
            self.assertNotEqual(bound['next_action']['result']['write_release'],
                progress.dispatch.execution_release_token(progress.handoff.unseal(old_slot['record'])))
            self.assertEqual(flow.allocation_slot('B1', 'B')['metrics']['first_submission'], old_slot['metrics']['first_submission'])

    def test_t04_revision_preserves_original_unaffected_claims_and_one_cas(self):
        flow = T04Flow(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        candidate = flow.layered_flow()
        flow.local_review(candidate)
        for number in (1, 2):
            flow.local_review(candidate, 'B', 'B' + str(number))
        flow.ticket_review(candidate, 'B')
        before = flow.state()
        checkpoint = before['control']['context']['handoff_progress']
        views = copy.deepcopy(checkpoint['review_claims'])
        store = progress.state_store.StateStore(flow.checkpoint)
        originals = {axis: store.read_evidence(view['claim_ref'], run_id=store.open_current().run_id)['value']
                     for axis, view in views.items()}
        old_slot = flow.allocation_slot('B1', 'B')
        (flow.flow / 'unrelated.txt').write_text('existing staged note\n')
        flow.flow_git('add', 'unrelated.txt')
        index = flow.flow_git('ls-files', '--stage', 'unrelated.txt')
        authority = before['scope_authority']
        path = '.scratch/layered/issues/A.md'
        report = {'revision_id': 't04-private', 'expected_authority_revision': authority['revision'],
            'expected_authority_digest': progress.entry.digest(authority), 'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:t04-private'},
            'planning_paths': [path], 'report': {'ticket_ids': ['A'], 'step_ids': ['A1'],
                'reason': 'split a private helper', 'production_facts': 'same approved module output',
                'contract_before': authority['execution_plan']['contract_digest'],
                'contract_after': authority['execution_plan']['contract_digest'],
                'dependency_impact': ['A1'], 'verification_impact': ['V-A1'], 'carry_forward': ['B1', 'B2']}}
        public = copy.deepcopy(report)
        public['classification']['kind'] = 'public'
        pointer = flow.checkpoint.read_bytes()
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke('prepare-plan-revision', public)
        self.assertEqual(error.exception.code, 'user_decision_required')
        self.assertEqual(flow.checkpoint.read_bytes(), pointer)
        stopping = flow.invoke('prepare-plan-revision', report)
        self.assertEqual(stopping['next_action']['operation'], 'stop-host', stopping)
        unknown = flow.invoke('observe', flow.observation('unknown', 'result', ref='native:dispatcher'))
        self.assertNotIn('write_release', unknown.get('next_action') or {})
        self.assertEqual(flow.state()['plan_revision_transaction']['state'], 'stopping')
        lookup = flow.invoke('advance')
        self.assertIn(lookup['next_action']['operation'], {'inspect-host-state', 'await-host-recovery', 'lookup-exact-action'})
        prepared = flow.invoke('observe', flow.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        body = (flow.flow / path).read_text()
        match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan['plan_version'] = 2
        plan['steps'][0]['approach'].append('split the private helper')
        (flow.flow / path).write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                                    .replace('Plan Version: 1', 'Plan Version: 2'))
        flow.flow_git('add', path)
        flow.flow_git('commit', '--only', '-qm', 't04 private plan revision', '--', path)
        revised = flow.flow_git('rev-parse', 'HEAD')
        request = {'revision_id': report['revision_id'], 'controller_ref': 'task',
            'write_release': prepared['next_action']['write_release'], 'planning_commit': revised}
        original_bytes = (flow.flow / 'a1.py').read_bytes()
        (flow.flow / 'a1.py').write_bytes(b'unapproved implementation drift\n')
        pointer = flow.checkpoint.read_bytes()
        with self.assertRaisesRegex(progress.control.ControlError, 'implementation or protected bytes changed'):
            flow.invoke('commit-plan-revision', request)
        self.assertEqual(flow.checkpoint.read_bytes(), pointer)
        (flow.flow / 'a1.py').write_bytes(original_bytes)
        original_replace = progress.state_store.os.replace
        lost = []
        def lost_after_activation(source, target, *args, **kwargs):
            losing = False
            if Path(target) == flow.checkpoint and not lost:
                envelope = json.loads(Path(source).read_bytes())
                root = store.read_evidence(envelope['current'], run_id=envelope['run_id'], kind='root')['value']
                subject = store.read_evidence(root['subjects'][root['metadata']['stage_subject']],
                    run_id=envelope['run_id'], kind='subject')['value']
                losing = (subject.get('plan_revision_transaction') or {}).get('state') == 'activated'
            result = original_replace(source, target, *args, **kwargs)
            if losing:
                lost.append(True)
                raise OSError('lost return after activation CAS')
            return result
        with patch.object(progress.state_store.os, 'replace', lost_after_activation):
            with self.assertRaisesRegex(OSError, 'lost return after activation CAS'):
                flow.invoke('commit-plan-revision', request)
        self.assertEqual(lost, [True])
        after = flow.state()['control']['context']['handoff_progress']
        self.assertNotIn('local:A:A1', after['review_claims'])
        for axis in ('local:B:B1', 'local:B:B2', 'ticket:B'):
            self.assertEqual(after['review_claims'][axis], views[axis])
            self.assertEqual(store.read_evidence(views[axis]['claim_ref'], run_id=store.open_current().run_id)['value'], originals[axis])
        self.assertEqual(after['plan_revision_sources'][-1]['review_claims'], views)
        self.assertEqual(flow.state().get('rework_receipts'), before.get('rework_receipts'))
        self.assertEqual(flow.flow_git('ls-files', '--stage', 'unrelated.txt'), index)
        carried = next(item for item in after['carried_results'] if item['agent_ref'] == 'native:executor-B1')
        self.assertEqual(carried['agent_ref'], 'native:executor-B1')
        self.assertEqual(carried['plan_authority'], after['plan_authority'])
        self.assertNotEqual(carried['plan_authority'], checkpoint['plan_authority'])
        proof = after['review_applicability']
        self.assertEqual(proof['candidate'], revised)
        self.assertEqual(proof['affected'], [views['local:A:A1']['claim_id']])
        self.assertEqual(proof['retained'], sorted(views[axis]['claim_id'] for axis in ('local:B:B1', 'local:B:B2', 'ticket:B')))
        replay = flow.invoke('commit-plan-revision', request, revision=0)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertTrue(flow.invoke('reconcile-plan-revision', {'revision_id': report['revision_id']}, revision=0)['acknowledged'])
        self.assertEqual(len(flow.state()['control']['context']['handoff_progress']['plan_revisions']), 1)
        with self.assertRaises(progress.entry.PreparationError):
            progress.dispatch.matching(progress.handoff.unseal(old_slot['record']), flow.state()['control']['context'])
        # The original stopped executor continues with current authority. An
        # inherited accepted result is evidence, not its old writer lease.
        flow.flow_git('reset', '--', 'unrelated.txt')
        (flow.flow / 'unrelated.txt').unlink()
        flow.early_ticket_check(revised, 'B')
        current = flow.execution_input()
        ticket = next(item for item in flow.state()['scope_authority']['execution_plan']['catalog']['tickets']
                      if item['ticket_id'] == 'B')
        current['scope']['owned_paths'] = ['b1.py']
        current['authorization']['scope_digest'] = progress.entry.digest(current['scope'])
        current['semantic']['plan_refs'] = [progress.execution_plan.step_ref(ticket, ticket['plan']['steps'][0])]
        continued = flow.invoke('allocation', {'allocation_id': 'B1', 'operation': 'repair',
            'invocation_id': 't04-current-B1', 'handoff': current})
        self.assertIsNotNone(continued['next_action'], continued)
        self.assertEqual(continued['next_action']['operation'], 'invoke-host', continued)
        self.assertEqual(continued['next_action']['payload']['payload']['execution_invocation']['ref'], 'native:executor-B1')
        new_slot = flow.allocation_slot('B1', 'B')
        old_receipt = flow.receipt(old_slot['record'], ref='native:executor-B1')
        with self.assertRaises(progress.entry.PreparationError):
            progress.dispatch.validate_allocation_receipt(new_slot['record'], 'bind', old_receipt,
                flow.state()['control']['context'])
        receipt = flow.receipt(new_slot['record'], ref='native:executor-B1')
        receipt['receipt_ref'] = 't04-current-B1:ready'
        bound = flow.invoke('allocation', {'allocation_id': 'B1', 'operation': 'bind', 'receipt': receipt})
        self.assertNotEqual(bound['next_action']['result']['write_release'],
            progress.dispatch.execution_release_token(progress.handoff.unseal(old_slot['record'])))
        self.assertEqual(new_slot['metrics']['first_submission'], old_slot['metrics']['first_submission'])

    def new_active_flow(self, *, generated=False, read_dependency=False, grouped_reads=False,
                        artifact_mode=None, retained_note=False, ticket=False):
        case = fixture.ScopeProgressTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        original_manifest = case.manifest

        def planning_manifest(candidate, *, implementation=True):
            manifest = original_manifest(candidate, implementation=implementation)
            if artifact_mode == "only":
                manifest = [item for item in manifest if item["path"] != "impl.py"]
            if artifact_mode is not None:
                manifest.append({"path": "prompt.md", "owner": "implementation",
                    "kind": "source", "operation": "add",
                    "basis": {"commit": candidate, "path": "docs/spec.md",
                              "section": "approved design"}})
            manifest.append({"path": "docs/spec.md", "owner": "closure", "kind": "document",
                "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md",
                    "section": "approved design"}})
            if ticket:
                manifest.append({"path": "docs/issues/FEP-01.md", "owner": "closure",
                    "kind": "document", "operation": "add", "basis": {"commit": candidate,
                        "path": "docs/issues/FEP-01.md", "section": "approved design"}})
            if generated:
                manifest.append({"path": "generated/impl_copy.py", "owner": "implementation",
                    "kind": "generated", "operation": "add",
                    "basis": {"commit": candidate, "path": "docs/spec.md",
                              "section": "approved design"}, "source_paths": ["impl.py"]})
            if read_dependency:
                for relative in ("later.py", "independent.py", "downstream.py", "reader.py"):
                    manifest.append({"path": relative, "owner": "implementation", "kind": "source",
                        "operation": "add", "basis": {"commit": candidate, "path": "docs/spec.md",
                                                       "section": "approved design"}})
            return sorted(manifest, key=lambda item: item["path"])

        case.manifest = planning_manifest
        if retained_note:
            original_candidate = case.planning_candidate
            def planning_with_retained_note(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
                source = case.flow.flow / "docs/spec.md"
                source.write_text(source.read_text() + "### N4 — Private inventory\nInitial note.\n")
                case.flow.flow_git("add", "docs/spec.md")
                case.flow.flow_git("commit", "-qm", "private inventory note")
                return case.flow.flow_git("rev-parse", "HEAD")
            case.planning_candidate = planning_with_retained_note
        if artifact_mode is not None:
            original_candidate = case.planning_candidate
            def planning_with_artifact(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
                path = case.flow.flow / "docs/spec.md"
                text = path.read_text()
                match = re.search(r"```execution-plan-v2\n(.*?)\n```", text, re.DOTALL)
                plan = json.loads(match.group(1))
                if artifact_mode == "only":
                    plan["steps"] = []
                plan["artifact_steps"] = [{"id": "ART-1", "path": "prompt.md",
                    "operation": "add", "capability": "provide the approved prompt artifact",
                    "approach": "write the approved prompt", "verification_refs": ["V1"]}]
                PlanRevisionProgressTests.refresh_fixture_slices(plan)
                updated = text[:match.start(1)] + json.dumps(plan) + text[match.end(1):]
                if artifact_mode == "only":
                    updated = updated.replace("Implementation: impl.py", "Implementation: prompt.md")
                path.write_text(updated)
                case.flow.flow_git("add", "docs/spec.md")
                case.flow.flow_git("commit", "-qm", "plan artifact step")
                return case.flow.flow_git("rev-parse", "HEAD")
            case.planning_candidate = planning_with_artifact
        if read_dependency:
            original_candidate = case.planning_candidate
            def planning_with_read_dependency(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
                path = case.flow.flow / "docs/spec.md"
                text = path.read_text()
                match = re.search(r"```execution-plan-v2\n(.*?)\n```", text, re.DOTALL)
                plan = json.loads(match.group(1))
                plan["steps"][1]["target"] = {"path": "later.py", "symbol": "later", "operation": "add"}
                plan["steps"][1]["read_only"] = ["impl.py"]
                plan["steps"][1]["depends_on"] = []
                for identity in ("V2", "V3"):
                    verification = copy.deepcopy(plan["verifications"][0])
                    verification["id"] = identity
                    verification["behavior"] = "independent reader behavior" if identity == "V2" else "unrelated behavior"
                    plan["verifications"].append(verification)
                plan["steps"][1]["verification_refs"] = ["V2"]
                for identity, relative, dependencies, reads in (
                    ("FP1-03", "independent.py", [], []),
                    ("FP1-04", "downstream.py", ["FP1-02"], []),
                    ("FP1-05", "reader.py", [], ["later.py"])):
                    step = copy.deepcopy(plan["steps"][1])
                    step.update(id=identity, target={"path": relative,
                        "symbol": relative.removesuffix(".py"), "operation": "add"},
                        depends_on=dependencies, read_only=reads,
                        verification_refs=["V3"] if identity == "FP1-03" else ["V2"])
                    plan["steps"].append(step)
                if grouped_reads:
                    plan["steps"][1]["read_only"] = []
                    plan["steps"][2]["read_only"] = ["impl.py"]
                    plan["steps"][3]["depends_on"] = ["FP1-02", "FP1-03"]
                PlanRevisionProgressTests.refresh_fixture_slices(plan)
                if grouped_reads:
                    plan['behavior_slices'] = [group for group in plan['behavior_slices']
                        if group['id'] not in {'FP1-02', 'FP1-03'}] + [{
                        'id': 'mixed-group', 'step_ids': ['FP1-02', 'FP1-03'],
                        'ac_refs': ['FEP-01:AC-1'], 'shared_state': []}]
                    PlanRevisionProgressTests.refresh_fixture_slices(plan)
                path.write_text(text[:match.start(1)] + json.dumps(plan) + text[match.end(1):])
                case.flow.flow_git("add", "docs/spec.md")
                case.flow.flow_git("commit", "-qm", "planned accepted implementation read")
                return case.flow.flow_git("rev-parse", "HEAD")
            case.planning_candidate = planning_with_read_dependency
        if generated:
            def propose_with_generated(candidate, *, implementation=True):
                state = case.flow.state()
                data = {"proposal_id": "proposal-1", "expected_revision": 0,
                    "candidate_commit": candidate, "manifest": case.manifest(candidate),
                    "implementation_required": True, "generated_outputs":
                        {"impl.py": ["generated/impl_copy.py"]},
                    "requirement_identity": copy.deepcopy(state["handoff"]["requirement_identity"]),
                    "binding": copy.deepcopy(state["handoff"]["binding"]),
                    "previous_scope_revision": 0}
                response = case.flow.invoke("scope-propose", data)
                case.assertEqual(response["next_action"]["operation"], "scope-proposed")
                return data, response["next_action"]["proposal"]["digest"]
            case.propose = propose_with_generated
        if ticket:
            case.flow.input['scope']['owned_paths'] = ['docs/issues/FEP-01.md', 'docs/spec.md']
            case.flow.input['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
                case.flow.input['scope'])
            original_candidate = case.planning_candidate
            def planning_with_ticket(*args, _original=original_candidate, **kwargs):
                _original(*args, **kwargs)
                issue = case.flow.flow / 'docs/issues/FEP-01.md'
                issue.parent.mkdir(parents=True, exist_ok=True)
                issue.write_text((case.flow.flow / 'docs/spec.md').read_text() +
                                 '\n## Comments\nInitial implementation note.\n')
                case.flow.flow_git('add', 'docs/issues/FEP-01.md')
                case.flow.flow_git('commit', '-qm', 'publish Ticket plan')
                return case.flow.flow_git('rev-parse', 'HEAD')
            case.planning_candidate = planning_with_ticket
        case.finish_planned_design()
        predecessor = case.flow.state()["accepted"]
        authority = case.flow.state()["scope_authority"]
        request = case.flow.input_for(3, predecessor)
        request["scope"]["downstream"] = copy.deepcopy(authority["scope"]["downstream"])
        request["scope"]["implementation_paths"] = copy.deepcopy(authority["scope"]["implementation_paths"])
        request["scope"]["closure_paths"] = copy.deepcopy(authority["scope"]["closure_paths"])
        if ticket:
            request["scope"]["protected_paths"] = sorted(set(request["scope"]["protected_paths"] +
                                                              ["docs/issues/FEP-01.md"]))
        request["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(request["scope"])
        case.flow.context = case.flow.context_for(3)
        case.flow.begin("continuous", request)
        case.flow.invoke("observe", case.flow.observation(ref="native:dispatcher"))
        return case.flow

    def report(self, flow):
        authority = flow.state()["scope_authority"]
        return {"revision_id": "internal-1", "expected_authority_revision": authority["revision"],
            "expected_authority_digest": fixture.fixture.transfer.entry.digest(authority),
            "controller_ref": "task", "classification": {"kind": "internal",
                "reference": "controller:private-function-move"},
            "report": {"ticket_ids": ["FEP-01"], "step_ids": ["FP1-01"],
                "reason": "Move a private helper within the approved behavior",
                "production_facts": "The current caller and output stay within the same module",
                "contract_before": authority["execution_plan"]["contract_digest"],
                "contract_after": authority["execution_plan"]["contract_digest"],
                "dependency_impact": ["FP1-01", "FP1-02"],
                "verification_impact": ["V1"], "carry_forward": []},
            "planning_paths": [authority['execution_plan']['catalog']['tickets'][0]['source']['path']]}

    def test_revision_entries_reject_non_string_ids_without_changing_state(self):
        flow = self.new_active_flow()
        before = flow.checkpoint.read_bytes()
        head = flow.flow_git("rev-parse", "HEAD")
        for operation in ("prepare-plan-revision", "commit-plan-revision",
                          "reconcile-plan-revision"):
            for invalid in ([], {}, "", "  "):
                with self.subTest(operation=operation, invalid=invalid):
                    if operation == "prepare-plan-revision":
                        request = self.report(flow)
                    elif operation == "commit-plan-revision":
                        request = {"revision_id": "internal-1", "controller_ref": "task",
                                   "write_release": "unused", "planning_commit": head}
                    else:
                        request = {"revision_id": "internal-1"}
                    request["revision_id"] = invalid
                    with self.assertRaises(progress.entry.PreparationError) as error:
                        flow.invoke(operation, request)
                    self.assertEqual(error.exception.code, "invalid_request")
                    self.assertIn("revision_id", str(error.exception))
                    self.assertEqual(flow.checkpoint.read_bytes(), before)
                    self.assertEqual(flow.flow_git("rev-parse", "HEAD"), head)

    def test_prepare_plan_revision_rejects_non_string_report_ids_before_saving(self):
        flow = self.new_active_flow()
        before = flow.checkpoint.read_bytes()
        head = flow.flow_git("rev-parse", "HEAD")
        for field, value in (("ticket_ids", {"id": "FEP-01"}),
                             ("step_ids", ["FP1-01"]),
                             ("dependency_impact", {"id": "FP1-01"}),
                             ("verification_impact", ["V1"])):
            with self.subTest(field=field):
                report = self.report(flow)
                report["report"][field] = [value]
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke("prepare-plan-revision", report)
                self.assertEqual(error.exception.code, "invalid_request")
                self.assertIn(f"report.{field}[0]", str(error.exception))
                self.assertEqual(flow.checkpoint.read_bytes(), before)
                self.assertEqual(flow.flow_git("rev-parse", "HEAD"), head)

    def test_prepare_plan_revision_rejects_non_string_carry_and_planning_paths(self):
        for field in ("carry_forward", "planning_paths"):
            with self.subTest(field=field):
                flow = self.new_active_flow()
                before = flow.checkpoint.read_bytes()
                head = flow.flow_git("rev-parse", "HEAD")
                report = self.report(flow)
                if field == "carry_forward":
                    report["report"][field] = [["FP1-01"]]
                else:
                    report[field] = [["docs/spec.md"]]
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke("prepare-plan-revision", report)
                self.assertEqual(error.exception.code, "invalid_request")
                self.assertIn(f"{field}[0]", str(error.exception))
                self.assertEqual(flow.checkpoint.read_bytes(), before)
                self.assertEqual(flow.flow_git("rev-parse", "HEAD"), head)

    def test_prepare_plan_revision_rejects_duplicate_or_unknown_carry_steps(self):
        flow = self.new_active_flow()
        before = flow.checkpoint.read_bytes()
        for carry, code in ((["FP1-01", "FP1-01"], "invalid_request"),
                            (["missing-step"], "invalid_plan_revision")):
            with self.subTest(carry=carry):
                report = self.report(flow)
                report["report"]["carry_forward"] = carry
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke("prepare-plan-revision", report)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(flow.checkpoint.read_bytes(), before)

    def accept_planned_step(self, flow, ticket, identity, relative, content, allocation_id,
                            *, accept=True):
        self.check_due_fixture_tickets(flow)
        execution = flow.execution_input()
        execution["scope"]["owned_paths"] = [relative]
        step = next(item for item in ticket["plan"]["steps"] if item["id"] == identity)
        execution["scope"]["protected_paths"] += step["read_only"]
        execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
            execution["scope"])
        execution["semantic"]["plan_refs"] = [progress.execution_plan.step_ref(ticket, step)]
        prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "prepare", "handoff": execution})
        self.assertIsNotNone(prepared["next_action"], prepared)
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
        slot = flow.state()["allocations"][allocation_id]
        bound = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
        release = bound["next_action"]["result"]["write_release"]
        if content is None:
            (flow.flow / relative).unlink()
        else:
            (flow.flow / relative).write_bytes(content)
        result = {"changed_paths": [relative],
            "file_hashes": {relative: None if content is None else hashlib.sha256(content).hexdigest()},
            "tests": ["behavior passed"], "write_release": release,
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
            "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
            "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                       "payload": result}})
        if not accept:
            return
        delivered = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
        self.assertEqual(delivered["next_action"]["result"]["status"], "accepted")

    def accept_planned_group(self, flow, ticket, identities, contents, allocation_id,
                             read_only=(), owned_paths=None):
        self.check_due_fixture_tickets(flow)
        execution = flow.execution_input()
        execution["scope"]["owned_paths"] = sorted(contents if owned_paths is None else owned_paths)
        execution["scope"]["protected_paths"] += list(read_only)
        execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
            execution["scope"])
        execution["semantic"]["plan_refs"] = [progress.execution_plan.step_ref(ticket, step)
            for step in ticket["plan"]["steps"] if step["id"] in identities]
        prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "prepare", "handoff": execution})
        self.assertIsNotNone(prepared["next_action"], prepared)
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
        slot = flow.state()["allocations"][allocation_id]
        bound = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
        release = bound["next_action"]["result"]["write_release"]
        for relative, content in contents.items():
            (flow.flow / relative).write_bytes(content)
        result = {"changed_paths": sorted(contents),
            "file_hashes": {relative: hashlib.sha256(content).hexdigest()
                            for relative, content in contents.items()},
            "tests": ["group behavior passed"], "write_release": release,
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
            "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
            "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                       "payload": result}})
        delivered = flow.invoke("allocation", {"allocation_id": allocation_id,
            "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
        self.assertEqual(delivered["next_action"]["result"]["status"], "accepted")

    def check_due_fixture_tickets(self, flow):
        owner = progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint))
        due = owner.ticket_checks_due()
        if not due:
            return
        checkpoint = owner.state['control']['context']['handoff_progress']
        self.assertTrue(all(item.get('stopped') is True
            for item in progress.control.all_execution_results(checkpoint) if item.get('agent_ref')),
            'fixture integration requires original execution stop facts')
        allowed = set(flow.state()['scope_authority']['scope']['implementation_paths'])
        changed = set(filter(None, flow.flow_git('diff', '--name-only', 'HEAD').splitlines()))
        changed.update(filter(None, flow.flow_git('ls-files', '--others', '--exclude-standard').splitlines()))
        self.assertLessEqual(changed, allowed, 'fixture integration must preserve planning and unrelated bytes')
        previous_thread = flow.settings['thread_id']
        previous_host = copy.deepcopy(flow.request['host'])
        flow.settings['thread_id'] = 'task'
        flow.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        try:
            if changed:
                flow.flow_git('add', '-A', '--', *sorted(changed))
                flow.flow_git('commit', '--only', '-qm', 'integrated accepted fixture slices', '--', *sorted(changed))
            candidate = flow.flow_git('rev-parse', 'HEAD')
            for ticket in due:
                checked = layered_fixture.LayeredReviewTests.early_ticket_check(flow, candidate, ticket)
                self.assertTrue(checked['next_action']['result']['ok'], checked)
        finally:
            flow.settings['thread_id'] = previous_thread
            flow.request['host'] = previous_host

    def carried_mixed_group(self, replacement_contents=None):
        flow = self.new_active_flow(read_dependency=True, grouped_reads=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "input-owner")
        self.accept_planned_group(flow, ticket, {"FP1-02", "FP1-03"},
            {"later.py": b"def later():\n    return 1\n",
             "independent.py": b"def independent():\n    return 1\n"},
            "mixed-group", read_only=["impl.py"])
        if replacement_contents is not None:
            self.accept_planned_group(flow, ticket, {"FP1-02", "FP1-03"},
                replacement_contents, "replacement-group", read_only=["impl.py"],
                owned_paths=["independent.py", "later.py"])

        report = self.report(flow)
        report["report"].update(step_ids=["FP1-05"], dependency_impact=["FP1-05"],
            verification_impact=["V2"], carry_forward=["FP1-01", "FP1-02", "FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation(
            "stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][4]["approach"].append("revise unrelated reader")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise unrelated reader", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        retention = flow.state()["control"]["context"]["handoff_progress"]
        carried_group = next(item for item in retention["carried_results"]
                             if item["paths"] == ["independent.py", "later.py"])
        self.assertEqual(set(carried_group["read_only_fingerprints"]), {"impl.py"})
        current_ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        return flow, current_ticket, controller_request, controller_settings

    def test_zero_delta_group_carries_all_owned_fingerprints_and_reader_dependency(self):
        flow, ticket, _, _ = self.carried_mixed_group(replacement_contents={})
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        carried = next(item for item in checkpoint["carried_results"]
                       if item["paths"] == ["independent.py", "later.py"])
        self.assertEqual(carried["git_result_snapshot"], {})
        self.assertEqual(set(carried["owned_final_fingerprints"]), set(carried["paths"]))
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "downstream-after-zero-delta")
        context = flow.state()["control"]["context"]
        original = next(item for item in context["handoff_progress"]["plan_revision_sources"][0]["executions"]
                        if item["agent_ref"] == "replacement-group")
        self.assertEqual(original["git_result_snapshot"], {})
        for corruption in ("original-start", "original-delta", "source-digest",
                           "start-projection", "final-projection"):
            with self.subTest(corruption=corruption):
                tampered = copy.deepcopy(context)
                state = tampered["handoff_progress"]
                archived = next(item for item in state["plan_revision_sources"][0]["executions"]
                                if item["agent_ref"] == "replacement-group")
                carried = next(item for item in state["carried_results"]
                               if item["agent_ref"] == "replacement-group")
                if corruption == "original-start":
                    archived["git_snapshot"]["files"]["independent.py"] = "0" * 64
                elif corruption == "original-delta":
                    archived["git_result_snapshot"]["independent.py"] = "0" * 64
                elif corruption == "source-digest":
                    carried["source_result_digest"] = "0" * 64
                elif corruption == "start-projection":
                    carried["source_owned_start_fingerprints"]["independent.py"] = "0" * 64
                else:
                    carried["owned_final_fingerprints"]["independent.py"] = "0" * 64
                if corruption not in {"original-start", "original-delta"}:
                    revision = state["plan_revisions"][-1]
                    revision["evidence"]["retention"]["carried_results"] = copy.deepcopy(
                        state["carried_results"])
                    revision["revision_ref"] = progress.control.digest({
                        "controller_ref": revision["controller_ref"],
                        "evidence": revision["evidence"]})
                with self.assertRaisesRegex(progress.control.ControlError,
                                            "original accepted|owned fingerprints"):
                    progress.control.validate_context(tampered)

        relative = flow.flow / "independent.py"
        original_bytes = relative.read_bytes()
        original_mode = relative.stat().st_mode & 0o7777
        for mutation in ("content", "mode", "presence"):
            with self.subTest(mutation=mutation):
                if mutation == "content":
                    relative.write_bytes(original_bytes + b"# changed\n")
                elif mutation == "mode":
                    relative.chmod(0o755)
                else:
                    relative.unlink()
                with self.assertRaisesRegex(progress.control.ControlError,
                                            "accepted content, existence or mode changed"):
                    progress.control_git.recovery_snapshot(
                        flow.flow, context["handoff_progress"])
                if mutation == "presence":
                    relative.write_bytes(original_bytes)
                else:
                    relative.write_bytes(original_bytes)
                relative.chmod(original_mode)

    def test_partial_delta_group_can_enter_candidate_with_unchanged_owned_path(self):
        flow, ticket, _, _ = self.carried_mixed_group(
            replacement_contents={"later.py": b"def later():\n    return 2\n"})
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        carried = next(item for item in checkpoint["carried_results"]
                       if item["paths"] == ["independent.py", "later.py"])
        self.assertEqual(set(carried["git_result_snapshot"]), {"later.py"})
        self.assertEqual(set(carried["owned_final_fingerprints"]), set(carried["paths"]))
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "candidate-downstream")
        self.accept_planned_step(flow, ticket, "FP1-05", "reader.py",
                                 b"def reader():\n    return 1\n", "candidate-reader")
        flow.flow_git("add", "impl.py", "later.py", "independent.py", "downstream.py", "reader.py")
        flow.flow_git("commit", "-qm", "accepted partial delta implementation")
        candidate = flow.flow_git("rev-parse", "HEAD")
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        checks = fixture.fixture.transfer.checks_for(
            checkpoint["validation_plan"]["review_required"], candidate)
        evidence = {
            "dispatcher_ref": "native:dispatcher",
            "attempt": flow.state()["control"]["context"]["carrier"]["attempt"],
            "commit": candidate, "expected_target_head": flow.git("rev-parse", "main"),
            "binding": flow.binding, "plan_digest": checkpoint["plan_digest"],
            "plan_authority": checkpoint["plan_authority"], "checks": checks,
            "source": {"adapter": "fixture", "call_ref": "focused-partial-group",
                "response_ref": "focused-partial-group-result", "raw": {"checks": checks,
                    "source_unchanged": True, "stopped": True,
                    "dispatcher_ref": "native:dispatcher"}}}
        ready = progress.control_git.verified_transition({"repository": str(flow.flow),
            "baseline": checkpoint["git_baseline_commit"], "request": {
                "schema_version": 6, "action": "candidate-ready", "actor_ref": "task",
                "context": flow.state()["control"]["context"], "evidence": evidence}})
        self.assertEqual(ready["context"]["handoff_progress"]["state"], "reviewable")

    def test_zero_delta_owned_evidence_survives_two_activated_revisions(self):
        flow, _, _, _ = self.carried_mixed_group(replacement_contents={})
        first = flow.state()["control"]["context"]["handoff_progress"]
        original = next(item for item in first["carried_results"]
                        if item["agent_ref"] == "replacement-group")
        report = self.report(flow)
        report["revision_id"] = "zero-delta-second"
        report["classification"]["reference"] = "controller:zero-delta-second"
        report["report"].update(step_ids=["FP1-05"], dependency_impact=["FP1-05"],
            verification_impact=["V2"], carry_forward=["FP1-01", "FP1-02", "FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        stopped = flow.observation("stopped", "result", ref="native:dispatcher")
        projection = progress.read_projection(flow.checkpoint, projection='scope')
        scoped = projection.record[progress.KEY]
        pending_transaction = flow.state()['plan_revision_transaction']
        self.assertEqual(scoped['plan_revision_transaction'], {key: pending_transaction.get(key)
            for key in ('state', 'proposed_authority')})
        self.assertIn(scoped['plan_revision_transaction']['state'], ('stopping', 'prepared'))
        self.assertEqual(scoped['execution_authority'], flow.state()['scope_authority'])
        self.assertLessEqual(projection.bytes_read, progress.state_store.READ_LIMIT)
        self.assertLessEqual(len(progress.state_store.encode(scoped)), 256 * 1024)
        pending_input = flow.execution_input()
        before_pending = flow.checkpoint.read_bytes()
        with self.assertRaises(progress.entry.PreparationError) as pending_error:
            progress.handoff.handle(pending_input)
        self.assertEqual(pending_error.exception.code, 'handoff_not_ready')
        self.assertNotIn('pending-must-not-dispatch', flow.state().get('allocations', {}))
        self.assertEqual(flow.checkpoint.read_bytes(), before_pending)
        original_history = copy.deepcopy(progress.read_active_owner(flow.checkpoint)[progress.KEY]['plan_revision_history'])
        for corruption in ('missing', 'receipt', 'foreign', 'unactivated', 'commit-input'):
            with self.subTest(activated_proof=corruption):
                changed = progress.read_active_owner(flow.checkpoint)
                history = changed[progress.KEY]['plan_revision_history']
                transaction = next(iter(history.values()))
                if corruption == 'missing':
                    history.clear()
                elif corruption == 'receipt':
                    transaction['control_result']['revision_ref'] = '0' * 64
                elif corruption == 'foreign':
                    transaction['proposed_authority']['revision'] += 1
                elif corruption == 'unactivated':
                    transaction['state'] = 'prepared'
                else:
                    transaction['commit_input'] = []
                progress.Progress(flow.checkpoint, changed).save()
                before = flow.checkpoint.read_bytes()
                invalid_projection = progress.read_projection(flow.checkpoint, projection='scope').record[progress.KEY]
                self.assertIsNone(invalid_projection['execution_authority'])
                self.assertIsNone(progress.consumed_scope_readiness(invalid_projection, flow.checkpoint))
                forged_projection = copy.deepcopy(invalid_projection)
                forged_projection['execution_authority'] = copy.deepcopy(forged_projection['scope_authority'])
                self.assertIsNone(progress.consumed_scope_readiness(forged_projection, flow.checkpoint))
                forged_projection['scope_authority'] = copy.deepcopy(forged_projection['consumed_readiness']['scope_authority'])
                forged_projection['execution_authority'] = copy.deepcopy(forged_projection['scope_authority'])
                self.assertIsNone(progress.consumed_scope_readiness(forged_projection, flow.checkpoint))
                with self.assertRaises(progress.entry.PreparationError):
                    flow.invoke('inspect')
                self.assertEqual(flow.checkpoint.read_bytes(), before)
                restored = progress.read_active_owner(flow.checkpoint)
                restored[progress.KEY]['plan_revision_history'] = copy.deepcopy(original_history)
                progress.Progress(flow.checkpoint, restored).save()
        listed = {row['id'] for row in stopped['lifecycle']['pages'][0]['response']['data']}
        self.assertLessEqual({'input-owner', 'mixed-group', 'replacement-group'}, listed)
        prepared = flow.invoke("observe", stopped)
        self.assertIsNotNone(prepared['next_action'], prepared)
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 3
        plan["steps"][4]["approach"].append("revise unrelated reader again")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 2", "Plan Version: 3"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise unrelated reader again", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated")
        second = flow.state()["control"]["context"]["handoff_progress"]
        carried = next(item for item in second["carried_results"]
                       if item["agent_ref"] == "replacement-group")
        for field in ("git_result_snapshot", "source_owned_start_fingerprints",
                      "owned_final_fingerprints", "source_result_digest", "source_revision"):
            self.assertEqual(carried[field], original[field], field)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "after-two-zero-delta-revisions")

    def test_carried_group_with_mixed_reads_satisfies_later_dependencies(self):
        flow, current_ticket, _, _ = self.carried_mixed_group()
        self.accept_planned_step(flow, current_ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "downstream-after-carry")

        self.accept_planned_step(flow, current_ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 2\n", "changed-input-owner")
        retention = flow.state()["control"]["context"]["handoff_progress"]
        completed = progress.execution_plan.current_accepted_steps(
            flow.state()["scope_authority"]["execution_plan"]["catalog"],
            retention["carried_results"] + retention["executions"],
            flow.state()["scope_authority"])
        self.assertTrue({"FP1-02", "FP1-03"}.isdisjoint(completed))
        self.accept_planned_group(flow, current_ticket, {"FP1-02", "FP1-03"},
            {"later.py": b"def later():\n    return 2\n",
             "independent.py": b"def independent():\n    return 2\n"},
            "fresh-mixed-group", read_only=["impl.py"])
        self.accept_planned_step(flow, current_ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 2\n", "downstream-after-revalidation")

    def test_carried_mixed_read_group_can_enter_candidate(self):
        flow, ticket, controller_request, controller_settings = self.carried_mixed_group()
        self.accept_planned_step(flow, ticket, "FP1-04", "downstream.py",
                                 b"def downstream():\n    return 1\n", "candidate-downstream")
        self.accept_planned_step(flow, ticket, "FP1-05", "reader.py",
                                 b"def reader():\n    return 1\n", "candidate-reader")
        flow.flow_git("add", "impl.py", "later.py", "independent.py", "downstream.py", "reader.py")
        flow.flow_git("commit", "-qm", "accepted mixed read implementation")
        candidate = flow.flow_git("rev-parse", "HEAD")
        flow.request = controller_request
        flow.settings = controller_settings
        flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        checkpoint = flow.state()["control"]["context"]["handoff_progress"]
        checks = fixture.fixture.transfer.checks_for(
            checkpoint["validation_plan"]["review_required"], candidate)
        ready = flow.invoke("control", {"action": "candidate-ready", "evidence": {
            "dispatcher_ref": "native:dispatcher",
            "attempt": flow.state()["control"]["context"]["carrier"]["attempt"],
            "commit": candidate, "expected_target_head": flow.git("rev-parse", "main"),
            "binding": flow.binding, "plan_digest": checkpoint["plan_digest"],
            "plan_authority": checkpoint["plan_authority"], "checks": checks},
            "receipt": {"adapter": "fixture", "call_ref": "focused-mixed-group",
                "response_ref": "focused-mixed-group-result", "raw": {"checks": checks,
                    "source_unchanged": True, "stopped": True,
                    "dispatcher_ref": "native:dispatcher"}}})
        self.assertEqual(flow.state()["control"]["context"]["handoff_progress"]["state"],
                         "reviewable", ready)

    def test_revision_revalidates_reader_after_accepted_input_changes(self):
        flow = self.new_active_flow(read_dependency=True)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py", b"def impl():\n    return 1\n", "first-b")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py", b"def later():\n    return 1\n", "old-a")
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py", b"def impl():\n    return 2\n", "new-b")
        report = self.report(flow)
        report["report"].update(step_ids=["FP1-03"], dependency_impact=["FP1-03"],
            verification_impact=["V3"], carry_forward=["FP1-01", "FP1-02"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][2]["approach"].append("revise unrelated private implementation")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise independent step", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        retention = flow.state()["control"]["context"]["handoff_progress"]
        self.assertEqual({item["paths"][0] for item in retention["carried_results"]}, {"impl.py"})
        self.assertIn("later.py", retention["revalidation_source"]["pending_paths"])

    def test_consecutive_revisions_keep_the_original_reader_witness(self):
        flow = self.new_active_flow(read_dependency=True)
        ticket = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py", b"def impl():\n    return 1\n", "first-b")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py", b"def later():\n    return 1\n", "old-a")

        def revise_independent(number):
            report = self.report(flow)
            report["revision_id"] = f"independent-{number}"
            report["classification"]["reference"] = f"controller:independent-{number}"
            report["report"].update(step_ids=["FP1-03"], dependency_impact=["FP1-03"],
                verification_impact=["V3"], carry_forward=["FP1-01", "FP1-02"])
            flow.invoke("prepare-plan-revision", report)
            stopped = flow.observation("stopped", "result", ref="native:dispatcher")
            if number > 1:
                stopped["lifecycle"]["pages"][0]["response"]["data"].extend(
                    {"id": ref, "status": {"type": "idle"}} for ref in ("first-b", "old-a"))
            prepared = flow.invoke("observe", stopped)
            self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared", prepared)
            source = flow.flow / "docs/spec.md"
            body = source.read_text()
            match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
            plan = json.loads(match.group(1))
            plan["plan_version"] = number + 1
            plan["steps"][2]["approach"].append(f"independent revision {number}")
            source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                              .replace(f"Plan Version: {number}", f"Plan Version: {number + 1}"))
            flow.flow_git("add", "docs/spec.md")
            flow.flow_git("commit", "--only", "-qm", f"independent revision {number}",
                          "--", "docs/spec.md")
            activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
            self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
            return flow.state()["control"]["context"]["handoff_progress"]

        first = revise_independent(1)
        carried_reader = next(item for item in first["carried_results"] if item["paths"] == ["later.py"])
        original_input = carried_reader["read_only_fingerprints"]["impl.py"]
        tampered = copy.deepcopy(flow.state()["control"]["context"])
        original_reader = next(item for item in
            tampered["handoff_progress"]["plan_revision_sources"][0]["executions"]
            if item["agent_ref"] == "old-a")
        original_reader["git_snapshot"]["files"].pop("impl.py")
        self.assertIsNone(progress.execution_plan.accepted_read_fingerprints(
            original_reader, {"impl.py"}))
        with self.assertRaisesRegex(progress.control.ControlError, "original accepted"):
            progress.control.validate_context(tampered)
        self.accept_planned_step(flow, flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0],
            "FP1-01", "impl.py", b"def impl():\n    return 2\n", "new-b")
        second = revise_independent(2)
        self.assertEqual({item["paths"][0] for item in second["carried_results"]}, {"impl.py"})
        self.assertIn("later.py", second["revalidation_source"]["pending_paths"])
        self.assertNotEqual(original_input, second["revalidation_source"]["snapshot"]["files"]["impl.py"])

    def test_revision_revalidates_independent_readers_and_carries_unrelated_result(self):
        flow = self.new_active_flow(read_dependency=True)
        original = flow.state()["scope_authority"]
        ticket = original["execution_plan"]["catalog"]["tickets"][0]
        accepted = []
        for identity, relative in (("FP1-01", "impl.py"), ("FP1-02", "later.py"),
                                   ("FP1-03", "independent.py"),
                                   ("FP1-04", "downstream.py"),
                                   ("FP1-05", "reader.py")):
            execution = flow.execution_input()
            execution["scope"]["owned_paths"] = [relative]
            step = next(item for item in ticket["plan"]["steps"] if item["id"] == identity)
            execution["scope"]["protected_paths"] += step["read_only"]
            execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
                execution["scope"])
            execution["semantic"]["plan_refs"] = [progress.execution_plan.step_ref(ticket, step)]
            allocation_id = "accepted-" + identity
            prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
                "operation": "prepare", "handoff": execution})
            self.assertIsNotNone(prepared["next_action"], prepared)
            self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
            slot = flow.state()["allocations"][allocation_id]
            bound = flow.invoke("allocation", {"allocation_id": allocation_id,
                "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
            release = bound["next_action"]["result"]["write_release"]
            content = ("def " + relative.removesuffix(".py") + "():\n    return 1\n").encode()
            (flow.flow / relative).write_bytes(content)
            result = {"changed_paths": [relative],
                "file_hashes": {relative: hashlib.sha256(content).hexdigest()},
                "tests": ["behavior passed"], "write_release": release,
                "plan_refs": execution["semantic"]["plan_refs"],
                "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
            flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
                "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
                "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                           "payload": result}})
            delivered = flow.invoke("allocation", {"allocation_id": allocation_id,
                "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
            self.assertEqual(delivered["next_action"]["result"]["status"], "accepted")
            accepted.append(allocation_id)
        report = self.report(flow)
        report["report"].update(step_ids=["FP1-01"], dependency_impact=["FP1-01", "FP1-02",
            "FP1-04", "FP1-05"], verification_impact=["V1", "V2"],
            carry_forward=["FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("retain the public behavior with revised internals")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise accepted writer", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        retention = flow.state()["control"]["context"]["handoff_progress"]
        self.assertEqual({item["paths"][0] for item in retention["carried_results"]},
                         {"independent.py"})
        self.assertEqual(set(retention["revalidation_source"]["pending_paths"]),
                         {"impl.py", "later.py", "downstream.py", "reader.py"})

    def integration_after_plan_revision(self, *, retained_note):
        flow = self.new_active_flow(retained_note=retained_note)
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("revise private helper sequence")
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "Controller plan revision", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated")
        execution = flow.execution_input()
        allocated = flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "prepare", "handoff": execution})
        self.assertEqual(allocated["next_action"]["operation"], "invoke-host", allocated)
        slot = flow.state()["allocations"]["integrated-implementation"]
        bound = flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "bind", "receipt": flow.receipt(slot["record"], ref="native:integrated")})
        release = bound["next_action"]["result"]["write_release"]
        implementation = b"def prepare():\n    return 'current'\ndef execute():\n    return prepare()\n"
        (flow.flow / "impl.py").write_bytes(implementation)
        result = {"changed_paths": ["impl.py"],
            "file_hashes": {"impl.py": hashlib.sha256(implementation).hexdigest()},
            "tests": ["current behavior passed"], "write_release": release,
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
        flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "receive", "receipt": flow.receipt(slot["record"], "stopped", "result",
            ref="native:integrated"), "result": {"delivery_id": "integrated-result",
            "status": "completed", "payload": result}})
        accepted = flow.invoke("allocation", {"allocation_id": "integrated-implementation",
            "operation": "accept", "decision": {"reference": "dispatcher:accepted"}})
        self.assertEqual(accepted["next_action"]["result"]["status"], "accepted")
        flow.flow_git("add", "impl.py")
        flow.flow_git("commit", "-qm", "accepted current implementation")
        accepted_commit = flow.flow_git("rev-parse", "HEAD")
        if retained_note:
            source.write_text(source.read_text().replace("Initial note.", "Closure note."))
            flow.flow_git("add", "docs/spec.md")
            flow.flow_git("commit", "-qm", "closure private inventory note")
        retained_commit = flow.flow_git("rev-parse", "HEAD")
        retained_paths = ["docs/spec.md"] if retained_note else []
        (flow.root / "parallel.md").write_text("parallel target work\n")
        flow.git("add", "parallel.md")
        flow.git("commit", "-qm", "parallel target work")
        target = flow.git("rev-parse", "main")
        flow.flow_git("merge", "--no-edit", target)
        context = copy.deepcopy(flow.state()["control"]["context"])
        checkpoint = context["handoff_progress"]
        checkpoint.update(integration_candidate=retained_commit, integration_target_head=target,
            integration_retained={"commit": retained_commit, "paths": retained_paths},
            validation_history=[{"candidate": accepted_commit}])
        self.assertEqual(checkpoint["plan_authority"]["revision"], 2)
        self.assertEqual(checkpoint["git_baseline_commit"],
                         flow.state()["handoff"]["scope"]["baseline"])

        def control(action, evidence, current=context):
            command = Path(progress.control_git.__file__)
            response = subprocess.run([sys.executable, str(command)], input=json.dumps({
                "repository": str(flow.flow), "baseline": checkpoint["git_baseline_commit"],
                "request": {"schema_version": current["schema_version"], "action": action,
                    "actor_ref": current["controller_ref"], "context": current,
                    "evidence": evidence}}), capture_output=True, text=True)
            self.assertTrue(response.stdout, response.stderr)
            return json.loads(response.stdout)

        if retained_note:
            malformed = copy.deepcopy(context)
            malformed_catalog = malformed["handoff_progress"]["execution_authority"][
                "execution_plan"]["catalog"]
            self.assertIsNotNone(malformed_catalog["commit"])
            del malformed_catalog["sources"]
            self.assertFalse(control("capture-integration", {"reference": "controller:target-race"},
                                     malformed)["ok"])
        captured = control("capture-integration", {"reference": "controller:target-race"})
        self.assertTrue(captured["ok"], captured)
        integration = captured["context"]["handoff_progress"]["integration_source"]
        self.assertEqual(integration["pending_paths"], ["impl.py"])
        current = captured["context"]
        current_progress = current["handoff_progress"]
        group = progress.execution_plan.validate_plan_refs(
            progress.execution_plan.current_catalog(current_progress["execution_authority"]),
            execution["semantic"]["plan_refs"], ["impl.py"],
            current_progress["protected_paths"], [], current_progress["execution_authority"])
        evidence = {"task_id": "adopt-current", "paths": ["impl.py"],
            "read_only": current["handoff_progress"]["protected_paths"],
            "behavior": "revalidate integrated implementation", "tests": group["verification_commands"],
            "git_operations": [], "configuration": execution["configuration"],
            "plan_refs": execution["semantic"]["plan_refs"],
            "plan_authority": current["handoff_progress"]["plan_authority"],
            "group_digest": group["group_digest"],
            "adopt_paths": ["impl.py"],
            "adoption_snapshot_digest": integration["snapshot_digest"]}
        planned = control("plan-execution", evidence, current)
        self.assertTrue(planned["ok"], planned)

        source.write_text(source.read_text() + "external planning byte\n")
        self.assertFalse(control("capture-integration", {"reference": "controller:target-race"})["ok"])
        source.write_text(source.read_text().replace("external planning byte\n", ""))
        source.chmod(0o755)
        self.assertFalse(control("capture-integration", {"reference": "controller:target-race"})["ok"])
        source.chmod(0o644)
        source.write_text(source.read_text() + "index-only change\n")
        flow.flow_git("add", "docs/spec.md")
        source.write_text(source.read_text().replace("index-only change\n", ""))
        self.assertFalse(control("capture-integration", {"reference": "controller:target-race"})["ok"])

    def test_capture_integration_excludes_verified_controller_planning_delta(self):
        self.integration_after_plan_revision(retained_note=False)

    def test_capture_integration_accepts_current_planning_and_retained_note_only(self):
        self.integration_after_plan_revision(retained_note=True)

    def test_final_integration_snapshot_supersedes_carried_and_revision_bytes(self):
        self.final_integration_snapshot(ticket=False)

    def test_ticket_comments_and_spec_archive_survive_full_final_integration_recovery(self):
        self.final_integration_snapshot(ticket=True)

    def final_integration_snapshot(self, *, ticket):
        flow = self.new_active_flow(read_dependency=True, ticket=ticket)
        controller_thread = flow.state()["handoff"]["expected_entry"]["actor"]["thread_id"]
        ticket_record = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        for identity, relative in (("FP1-01", "impl.py"), ("FP1-02", "later.py"),
                                   ("FP1-03", "independent.py"),
                                   ("FP1-04", "downstream.py"), ("FP1-05", "reader.py")):
            self.accept_planned_step(flow, ticket_record, identity, relative,
                ("def " + relative.removesuffix(".py") + "():\n    return 1\n").encode(),
                "accepted-" + identity)
        report = self.report(flow)
        report["report"].update(step_ids=["FP1-02"],
            dependency_impact=["FP1-02", "FP1-04", "FP1-05"],
            verification_impact=["V2"], carry_forward=["FP1-01", "FP1-03"])
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        planning_path = "docs/issues/FEP-01.md" if ticket else "docs/spec.md"
        planning = flow.flow / planning_path
        body = planning.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][1]["approach"].append("revise private implementation")
        planning.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", planning_path)
        flow.flow_git("commit", "--only", "-qm", "revise plan before final integration",
                      "--", planning_path)
        flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        ticket_record = flow.state()["scope_authority"]["execution_plan"]["catalog"]["tickets"][0]
        progress_before = flow.state()["control"]["context"]["handoff_progress"]
        self.assertTrue(progress_before["carried_results"])
        self.assertTrue(progress_before["revalidation_source"]["pending_paths"])
        original_carry = copy.deepcopy(progress_before["carried_results"])
        original_revision = copy.deepcopy(progress_before["plan_revisions"])
        original_source = copy.deepcopy(progress_before["revalidation_source"])
        original_receipts = copy.deepcopy(progress_before["plan_revision_sources"])
        original_observation = flow.observation

        def with_historical_agents(*args, **kwargs):
            evidence = original_observation(*args, **kwargs)
            if "lifecycle" in evidence:
                known = {item["id"] for item in evidence["lifecycle"]["pages"][0]["response"]["data"]}
                evidence["lifecycle"]["pages"][0]["response"]["data"].extend(
                    {"id": ref, "status": {"type": "idle"}}
                    for ref in {item["agent_ref"] for source in original_receipts
                                for item in source["executions"] if item["agent_ref"]} - known)
            return evidence

        for identity, relative in (("FP1-02", "later.py"),
                                   ("FP1-04", "downstream.py"), ("FP1-05", "reader.py")):
            self.accept_planned_step(flow, ticket_record, identity, relative,
                ("def " + relative.removesuffix(".py") + "():\n    return 2\n").encode(),
                "revalidated-" + identity)

        flow.flow_git("add", "impl.py", "later.py", "independent.py", "downstream.py", "reader.py")
        flow.flow_git("commit", "-qm", "accepted plan revision implementation")
        accepted = flow.flow_git("rev-parse", "HEAD")
        flow.settings["thread_id"] = controller_thread
        flow.request["host"].update(thread_id=controller_thread, role="controller", source_ref=None)
        flow.request["host"].pop("actor_ref", None)
        payload = {"artifacts": ["impl.py", "later.py", "independent.py", "downstream.py", "reader.py"],
            "checks": ["current plan verified"], "candidate_commit": accepted,
            "review": {axis: {"candidate": accepted, "reviewer_ref": axis, "status": "accepted"}
                       for axis in ("standards", "spec")},
            "verification": {"candidate": accepted}}
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            stage3 = flow.accept_current(payload, "native:dispatcher", fixture_execution=False)
        self.assertEqual(stage3["status"], "accepted", stage3)
        predecessor = flow.state()["accepted"]
        stage4_input = flow.input_for(4, predecessor)
        current_scope = flow.state()['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            stage4_input['scope'][key] = copy.deepcopy(current_scope[key])
        stage4_input['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            stage4_input['scope'])
        self.assertEqual(progress.handoff.prepare(stage4_input)["stage"], 4)
        original_checkpoint = flow.checkpoint.read_bytes()
        altered = progress.read_record(flow.checkpoint)
        altered[progress.KEY]["control"]["context"]["handoff_progress"]["plan_revisions"][-1]["evidence"]["planning_commit"] = accepted
        try:
            progress.atomic_save(flow.checkpoint, altered)
            with self.assertRaises((progress.entry.PreparationError,
                                    progress.control_git.ControlError)):
                progress.handoff.prepare(stage4_input)
        finally:
            flow.checkpoint.write_bytes(original_checkpoint)
        self.assertEqual(progress.handoff.prepare(stage4_input)["stage"], 4)
        current_progress = flow.state()["control"]["context"]["handoff_progress"]
        requirement = flow.flow / flow.state()["handoff"]["requirement_identity"]["path"]
        original_requirement = requirement.read_bytes()
        try:
            requirement.write_bytes(original_requirement + b"unauthorized requirement change\n")
            with self.assertRaises((progress.entry.PreparationError,
                                    progress.control_git.ControlError)):
                progress.handoff.prepare(stage4_input)
        finally:
            requirement.write_bytes(original_requirement)
        original_planning = planning.read_bytes()
        try:
            planning.write_bytes(original_planning + b"unapproved planning change\n")
            with self.assertRaises(progress.control_git.ControlError):
                progress.control_git.verified_planning_paths(flow.flow, current_progress)
        finally:
            planning.write_bytes(original_planning)
        try:
            planning.chmod(0o755)
            with self.assertRaises(progress.control_git.ControlError):
                progress.control_git.verified_planning_paths(flow.flow, current_progress)
        finally:
            planning.chmod(0o644)
        try:
            planning.write_bytes(original_planning + b"index-only planning change\n")
            flow.flow_git("add", planning_path)
            planning.write_bytes(original_planning)
            with self.assertRaises(progress.control_git.ControlError):
                progress.control_git.verified_planning_paths(flow.flow, current_progress)
        finally:
            flow.flow_git("reset", "-q", "--", planning_path)
            planning.write_bytes(original_planning)
        (flow.root / "impl.py").write_text("def impl():\n    return 99\n")
        (flow.root / "later.py").write_text("def later():\n    return 99\n")
        flow.git("add", "impl.py", "later.py")
        flow.git("commit", "-qm", "parallel target changes implementation")
        target = flow.git("rev-parse", "main")
        flow.context = flow.context_for(4)
        flow.begin("continuous", stage4_input)
        flow.invoke("observe", flow.observation(ref="native:closure"))
        if ticket:
            issue = flow.flow / planning_path
            issue.write_text(issue.read_text() + 'Verification: Stage 4 accepted.\n')
            spec = flow.flow / 'docs/spec.md'
            spec.write_text(spec.read_text() + '\n## Archive Notes\nStage 4 review accepted.\n')
            flow.flow_git('add', planning_path, 'docs/spec.md')
            flow.flow_git('commit', '-qm', 'retain Ticket and Spec closure notes')
        observed = flow.observation("stopped", "result", ref="native:closure")
        observed["closure"] = {"ref": "native:closure", "candidate": accepted,
            "merge": None, "ancestor_verified": False, "changed_paths": [],
            "binding": flow.binding, "checks": [], "worktree_removed": False,
            "branch_removed": False, "disposition": "deleted", "disposition_verified": False,
            "implementation_problem": "target changed implementation bytes"}
        blocked = flow.invoke("observe", observed)
        self.assertEqual(blocked["next_action"]["operation"], "implementation-recovery")
        recovery = flow.invoke("implementation-recovery", {"reference": "controller:target-race",
            "closure_digest": flow.state()["handoff"]["digest"]})
        self.assertEqual(flow.state()["stage"], 3, recovery)
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        merge = subprocess.run(["git", "-C", str(flow.flow), "merge", "--no-edit", target],
                               capture_output=True, text=True)
        self.assertNotEqual(merge.returncode, 0, merge.stdout)
        (flow.flow / "impl.py").write_bytes(b"def impl():\n    return 3\n")
        (flow.flow / "later.py").write_bytes(b"def later():\n    return 4\n")
        flow.flow_git("add", "impl.py", "later.py")
        flow.flow_git("commit", "-qm", "resolve final integration")
        integrated_candidate = flow.flow_git("rev-parse", "HEAD")
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        if ticket:
            progress.control_git.verified_planning_paths(
                flow.flow, flow.state()['control']['context']['handoff_progress'])
        captured = flow.invoke("control", {"action": "capture-integration",
            "evidence": {"reference": "controller:target-race"},
            "receipt": {"controller_ref": "task", "reference": "controller:target-race"}})
        self.assertNotEqual(captured["status"], "blocked", captured)
        current_progress = flow.state()["control"]["context"]["handoff_progress"]
        source = current_progress["integration_source"]
        self.assertEqual(current_progress["carried_results"], original_carry)
        self.assertEqual(current_progress["plan_revisions"], original_revision)
        self.assertEqual(current_progress["revalidation_source"], original_source)
        self.assertEqual(current_progress["plan_revision_sources"], original_receipts)
        self.assertTrue({"impl.py", "later.py"} <= set(source["pending_paths"]))
        carried_impl = next(result for result in original_carry if "impl.py" in result["paths"])
        self.assertNotEqual(source["snapshot"]["files"]["impl.py"],
                            carried_impl["owned_final_fingerprints"]["impl.py"])
        self.assertNotEqual(source["snapshot"]["files"]["later.py"],
                            original_source["snapshot"]["files"]["later.py"])
        self.assertEqual(source["snapshot"]["files"]["impl.py"],
                         progress.control_git.file_fingerprint(flow.flow, "impl.py"))

        if ticket:
            with patch.dict(flow.settings), patch.dict(flow.request["host"]):
                protected_input = flow.execution_input()
                protected_input["semantic"].update(
                    adopt_paths=["impl.py"], adoption_snapshot_digest=source["snapshot_digest"])
                protected_handoff = progress.handoff.handle(protected_input)
            current_control = flow.state()["control"]["context"]

            def reject_protected(control_context=current_control, handoff=protected_handoff):
                with self.assertRaises((progress.entry.PreparationError,
                                        progress.control_git.ControlError)):
                    progress.dispatch.handle({"protocol": progress.handoff.PROTOCOL,
                        "operation": "prepare", "handoff": handoff,
                        "control": {"context": control_context, "discussion": None}})

            forged = copy.deepcopy(current_control)
            forged["handoff_progress"]["integration_retained"]["commit"] = integrated_candidate
            forged["handoff_progress"]["integration_candidate"] = integrated_candidate
            reject_protected(forged)
            original_ticket = planning.read_bytes()
            planning.write_bytes(original_ticket + b"unactivated planning commit\n")
            flow.flow_git("add", planning_path)
            flow.flow_git("commit", "-qm", "unactivated planning change")
            try:
                reject_protected()
            finally:
                flow.flow_git("reset", "--hard", integrated_candidate)
            planning.write_bytes(original_ticket + b"unapproved public or plan bytes\n")
            try:
                reject_protected()
            finally:
                planning.write_bytes(original_ticket)
            planning.chmod(0o755)
            try:
                reject_protected()
            finally:
                planning.chmod(0o644)
            planning.write_bytes(original_ticket + b"index-only planning bytes\n")
            flow.flow_git("add", planning_path)
            planning.write_bytes(original_ticket)
            try:
                reject_protected()
            finally:
                flow.flow_git("reset", "-q", "--", planning_path)
            requirement.write_bytes(original_requirement + b"unapproved requirement bytes\n")
            try:
                reject_protected()
            finally:
                requirement.write_bytes(original_requirement)

        def reaccept(identity, relative):
            step = next(item for item in ticket_record["plan"]["steps"] if item["id"] == identity)
            with patch.dict(flow.settings), patch.dict(flow.request["host"]):
                execution = flow.execution_input()
                execution["scope"]["owned_paths"] = [relative]
                execution["scope"]["protected_paths"] += step["read_only"]
                execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(
                    execution["scope"])
                execution["semantic"].update(
                    plan_refs=[progress.execution_plan.step_ref(ticket_record, step)],
                    adopt_paths=[relative], adoption_snapshot_digest=source["snapshot_digest"])
                allocation_id = "integrated-" + identity
                prepared = flow.invoke("allocation", {"allocation_id": allocation_id,
                    "operation": "prepare", "handoff": execution})
                self.assertIsNotNone(prepared["next_action"], prepared)
                self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
                slot = flow.state()["allocations"][allocation_id]
                bound = flow.invoke("allocation", {"allocation_id": allocation_id,
                    "operation": "bind", "receipt": flow.receipt(slot["record"], ref=allocation_id)})
                release = bound["next_action"]["result"]["write_release"]
                content = (flow.flow / relative).read_bytes()
                result = {"changed_paths": [], "adopted_paths": [relative],
                    "file_hashes": {relative: hashlib.sha256(content).hexdigest()},
                    "tests": ["integrated behavior passed"], "write_release": release,
                    "plan_refs": execution["semantic"]["plan_refs"],
                    "plan_authority": flow.state()["control"]["context"]["handoff_progress"]["plan_authority"]}
                flow.invoke("allocation", {"allocation_id": allocation_id, "operation": "receive",
                    "receipt": flow.receipt(slot["record"], "stopped", "result", ref=allocation_id),
                    "result": {"delivery_id": allocation_id + "-result", "status": "completed",
                               "payload": result}})
                accepted_result = flow.invoke("allocation", {"allocation_id": allocation_id,
                    "operation": "accept", "decision": {"reference": "dispatcher:" + allocation_id}})
                self.assertEqual(accepted_result["next_action"]["result"]["status"], "accepted")

        for identity, relative in (("FP1-01", "impl.py"), ("FP1-02", "later.py"),
                                   ("FP1-03", "independent.py"),
                                   ("FP1-04", "downstream.py"), ("FP1-05", "reader.py")):
            reaccept(identity, relative)
        final_progress = flow.state()["control"]["context"]["handoff_progress"]
        self.assertEqual(final_progress["carried_results"], original_carry)
        self.assertEqual(final_progress["revalidation_source"], original_source)
        self.assertEqual(final_progress["plan_revision_sources"], original_receipts)
        payload["candidate_commit"] = integrated_candidate
        payload["review"] = {axis: {"candidate": integrated_candidate,
            "reviewer_ref": "integrated-" + axis, "status": "accepted"}
            for axis in ("standards", "spec")}
        with patch.object(flow, "observation", side_effect=with_historical_agents):
            replaced = flow.accept_current(payload, "native:dispatcher", fixture_execution=False)
        self.assertEqual(replaced["status"], "accepted", replaced)
        self.assertNotEqual(flow.state()["accepted"]["digest"], predecessor["digest"])
        after_integration = self.report(flow)
        after_integration["revision_id"] = "after-final-integration"
        after_integration["classification"]["reference"] = "controller:after-final-integration"
        saved_checkpoint = flow.checkpoint.read_bytes()
        saved_head = flow.flow_git("rev-parse", "HEAD")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("prepare-plan-revision", after_integration)
        self.assertEqual(error.exception.code, "plan_revision_unavailable")
        self.assertEqual(flow.checkpoint.read_bytes(), saved_checkpoint)
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), saved_head)

    def test_artifact_only_and_mixed_reports_revise_at_controller_git_boundary(self):
        for mode in ("only", "mixed"):
            with self.subTest(mode=mode):
                flow = self.new_active_flow(artifact_mode=mode)
                report = self.report(flow)
                current_ids = ["ART-1"] if mode == "only" else ["FP1-01", "ART-1"]
                report["report"]["step_ids"] = current_ids
                report["report"]["dependency_impact"] = current_ids
                first = flow.invoke("prepare-plan-revision", report)
                self.assertEqual(first["next_action"]["operation"], "stop-host", first)
                prepared = flow.invoke("observe", flow.observation(
                    "stopped", "result", ref="native:dispatcher"))
                self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
                spec = flow.flow / "docs/spec.md"
                body = spec.read_text()
                match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
                plan = json.loads(match.group(1))
                plan["plan_version"] = 2
                plan["artifact_steps"][0]["approach"] = "write the revised private prompt"
                spec.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                                .replace("Plan Version: 1", "Plan Version: 2"))
                flow.flow_git("add", "docs/spec.md")
                flow.flow_git("commit", "--only", "-qm", "revise artifact plan", "--", "docs/spec.md")
                outcome = flow.invoke("commit-plan-revision", {
                    "revision_id": report["revision_id"], "controller_ref": "task",
                    "write_release": prepared["next_action"]["write_release"],
                    "planning_commit": flow.flow_git("rev-parse", "HEAD")})
                self.assertEqual(outcome["next_action"]["operation"], "plan-revision-activated")
                self.assertEqual(flow.state()["scope_authority"]["revision"], 2)

    def test_retired_artifact_id_cannot_be_reused_after_intermediate_revision(self):
        flow = self.new_active_flow(artifact_mode="mixed")
        for number in (1, 2, 3):
            report = self.report(flow)
            report["revision_id"] = f"retired-artifact-{number}"
            report["classification"]["reference"] = f"controller:retired-artifact-{number}"
            report["report"]["step_ids"] = ["ART-1"]
            report["report"]["dependency_impact"] = ["ART-1"]
            prepared = flow.invoke("prepare-plan-revision", report)
            if prepared["next_action"]["operation"] == "stop-host":
                prepared = flow.invoke("observe", flow.observation(
                    "stopped", "result", ref="native:dispatcher"))
            self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
            spec = flow.flow / "docs/spec.md"
            body = spec.read_text()
            match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
            plan = json.loads(match.group(1))
            plan["plan_version"] += 1
            if number in (1, 3):
                artifact = copy.deepcopy(plan["artifact_steps"][0])
                artifact["id"] = "ART-retired"
                plan["artifact_steps"].append(artifact)
            else:
                plan["artifact_steps"] = [item for item in plan["artifact_steps"]
                                          if item["id"] != "ART-retired"]
            self.refresh_fixture_slices(plan)
            spec.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace(f"Plan Version: {number}", f"Plan Version: {number + 1}"))
            flow.flow_git("add", "docs/spec.md")
            flow.flow_git("commit", "--only", "-qm", f"retire artifact revision {number}",
                          "--", "docs/spec.md")
            before = flow.state()["scope_authority"]["revision"]
            request = {"revision_id": report["revision_id"], "controller_ref": "task",
                       "write_release": prepared["next_action"]["write_release"],
                       "planning_commit": flow.flow_git("rev-parse", "HEAD")}
            if number < 3:
                outcome = flow.invoke("commit-plan-revision", request)
                self.assertEqual(outcome["next_action"]["operation"], "plan-revision-activated")
                self.assertEqual(flow.state()["scope_authority"]["revision"], before + 1)
            else:
                with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
                    flow.invoke("commit-plan-revision", request)
                self.assertEqual(error.exception.code, "execution_plan_incomplete")
                self.assertEqual(flow.state()["scope_authority"]["revision"], before)

    def test_active_writer_blocks_planning_release_before_any_document_edit(self):
        flow = self.new_active_flow()
        before = (flow.flow / "docs/spec.md").read_bytes()
        response = flow.invoke("prepare-plan-revision", self.report(flow))
        self.assertEqual(response["next_action"]["operation"], "stop-host")
        self.assertNotIn("write_release", response["next_action"])
        late = flow.observation("running", "result", ref="native:dispatcher",
            result={"delivery_id": "late-result", "status": "completed", "payload": {"candidate": "late"}})
        flow.invoke("observe", late)
        self.assertEqual(flow.state()["plan_revision_late_results"][0]["event_id"], late["event_id"])
        self.assertIsNone(flow.state()["dispatch"]["delivery"])
        self.assertEqual((flow.flow / "docs/spec.md").read_bytes(), before)
        self.assertEqual(flow.state()["scope_authority"]["revision"], 1)

    def test_stopped_original_host_receives_one_exact_planning_release(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        stopped = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(stopped["next_action"]["operation"], "plan-revision-prepared")
        release = stopped["next_action"]["write_release"]
        self.assertEqual(release, flow.state()["plan_revision_transaction"]["write_release"])
        self.assertEqual(flow.state()["plan_revision_transaction"]["snapshot"]["planning_paths"],
                         ["docs/spec.md"])
        replay = flow.invoke("prepare-plan-revision", report, revision=0)
        self.assertEqual(replay["next_action"]["write_release"], release)
        self.assertEqual(flow.state()["scope_authority"]["revision"], 1)

    def test_unknown_stop_call_requires_original_lookup_before_release(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        first = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(first["next_action"]["operation"], "stop-host")
        unknown = flow.invoke("observe", flow.observation("unknown", "result", ref="native:dispatcher"))
        self.assertNotIn("write_release", unknown.get("next_action") or {})
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "stopping")
        lookup = flow.invoke("advance")
        self.assertIn(lookup["next_action"]["operation"],
                      {"inspect-host-state", "await-host-recovery", "lookup-exact-action"})
        settled = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(settled["next_action"]["operation"], "plan-revision-prepared")

    def test_late_native_bind_settles_without_releasing_old_executor(self):
        flow = self.new_active_flow()
        execution = flow.execution_input()
        parent = flow.state()["handoff"]
        execution["scope"] = copy.deepcopy(parent["scope"])
        execution["scope"]["owned_paths"] = ["impl.py"]
        execution["authorization"]["scope_digest"] = fixture.fixture.transfer.entry.digest(execution["scope"])
        prepared = flow.invoke("allocation", {"allocation_id": "late-native", "operation": "prepare",
            "handoff": execution})
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host")
        flow.invoke("prepare-plan-revision", self.report(flow))
        slot = flow.state()["allocations"]["late-native"]
        late = flow.invoke("allocation", {"allocation_id": "late-native", "operation": "bind",
            "receipt": flow.receipt(slot["record"], ref="native:late-executor")})
        self.assertEqual(late["next_action"]["operation"], "allocation-result")
        self.assertNotIn("write_release", late["next_action"]["result"])
        self.assertNotIn("write_release", flow.state()["allocations"]["late-native"]["result"])
        replay = flow.invoke("allocation", {"allocation_id": "late-native", "operation": "reconcile",
            "receipt": flow.receipt(flow.state()["allocations"]["late-native"]["record"],
                                    "ready", "lookup", ref="native:late-executor")})
        self.assertNotIn("write_release", replay["next_action"]["result"])
        stopped = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertNotIn("write_release", stopped.get("next_action") or {})
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "stopping")

    def test_only_planning_commit_activates_one_current_plan_and_preserves_unrelated_index(self):
        flow = self.new_active_flow()
        (flow.flow / "unrelated.txt").write_text("existing staged note\n")
        flow.flow_git("add", "unrelated.txt")
        before_index = progress.control_git.plan_revision_snapshot(flow.flow,
            flow.state()["scope_authority"], flow.state()["handoff"]["scope"]["protected_paths"],
            ["docs/spec.md"])["index_entries"]["unrelated.txt"]
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        release = prepared["next_action"]["write_release"]
        spec = flow.flow / "docs/spec.md"
        old = spec.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", old, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("move the private helper without changing output")
        spec.write_text(old[:match.start(1)] + json.dumps(plan) + old[match.end(1):])
        spec.write_text(spec.read_text().replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise private plan", "--", "docs/spec.md")
        candidate = flow.flow_git("rev-parse", "HEAD")
        result = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": release, "planning_commit": candidate})
        self.assertEqual(result["next_action"]["operation"], "plan-revision-activated")
        self.assertEqual(flow.state()["scope_authority"]["revision"], 2)
        self.assertEqual(progress.control_git.plan_revision_snapshot(flow.flow,
            flow.state()["scope_authority"], flow.state()["handoff"]["scope"]["protected_paths"],
            ["docs/spec.md"])["index_entries"]["unrelated.txt"], before_index)
        for operation, data in (("prepare-plan-revision", report),
                                ("commit-plan-revision", {"revision_id": report["revision_id"],
                                    "controller_ref": "task", "write_release": release,
                                    "planning_commit": candidate}),
                                ("reconcile-plan-revision", {"revision_id": report["revision_id"]})):
            replay = flow.invoke(operation, data, revision=0)
            self.assertTrue(replay["acknowledged"], replay)
            self.assertNotIn("write_release", replay["next_action"])
            self.assertEqual(flow.flow_git("rev-parse", "HEAD"), candidate)

    def test_retired_function_id_cannot_be_reused_after_intermediate_revision(self):
        flow = self.new_active_flow()
        for number in (1, 2, 3):
            report = self.report(flow)
            report['revision_id'] = f'retired-id-{number}'
            report['classification']['reference'] = f'controller:retired-id-{number}'
            prepared = flow.invoke('prepare-plan-revision', report)
            if prepared['next_action']['operation'] == 'stop-host':
                prepared = flow.invoke('observe', flow.observation('stopped', 'result', ref='native:dispatcher'))
            self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
            spec = flow.flow / 'docs/spec.md'
            body = spec.read_text()
            match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
            plan = json.loads(match.group(1))
            plan['plan_version'] += 1
            if number in (1, 3):
                helper = copy.deepcopy(plan['steps'][0])
                helper['id'] = 'FP1-retired-helper'
                helper['target']['symbol'] = 'retired_helper'
                helper['depends_on'] = ['FP1-01']
                plan['steps'].append(helper)
            else:
                plan['steps'] = [step for step in plan['steps'] if step['id'] != 'FP1-retired-helper']
            self.refresh_fixture_slices(plan)
            spec.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace(f'Plan Version: {number}', f'Plan Version: {number + 1}'))
            flow.flow_git('add', 'docs/spec.md')
            flow.flow_git('commit', '--only', '-qm', f'retire function revision {number}', '--', 'docs/spec.md')
            before = flow.state()['scope_authority']['revision']
            commit_request = {'revision_id': report['revision_id'],
                'controller_ref': 'task', 'write_release': prepared['next_action']['write_release'],
                'planning_commit': flow.flow_git('rev-parse', 'HEAD')}
            if number < 3:
                outcome = flow.invoke('commit-plan-revision', commit_request)
                self.assertEqual(outcome['next_action']['operation'], 'plan-revision-activated', outcome)
                self.assertEqual(flow.state()['scope_authority']['revision'], before + 1)
            else:
                with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
                    flow.invoke('commit-plan-revision', commit_request)
                self.assertEqual(error.exception.code, 'execution_plan_incomplete')
                self.assertEqual(flow.state()['scope_authority']['revision'], before)

    def test_inline_revision_b_delivery_reads_current_controller_plan_and_n5(self):
        flow = fixture.fixture.ProgressTests(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.begin_dispatcher()
        authority = flow.state()['scope_authority']
        original_b0 = copy.deepcopy(fixture.fixture.transfer.handoff.unseal(flow.state()['handoff']))
        original_b0.pop('inline_authority')
        original_b0['semantic'].pop('execution_plan')
        self.assertEqual(authority['execution_plan']['sources'][0]['input_digest'],
                         fixture.fixture.transfer.entry.digest(original_b0))
        plan = copy.deepcopy(authority['execution_plan']['catalog']['tickets'][0]['plan'])
        plan['plan_version'] += 1
        plan['steps'][0]['approach'].append('Use the revised private helper sequence')
        helper_step = copy.deepcopy(plan['steps'][0])
        helper_step['id'] = 'inline-private-helper'
        helper_step['target'] = {'path': 'helper.py', 'symbol': 'helper', 'operation': 'add'}
        helper_step['depends_on'] = [plan['steps'][0]['id']]
        plan['steps'].append(helper_step)
        self.refresh_fixture_slices(plan)
        old_manifest = authority['scope']['downstream']['manifest']
        helper_manifest = copy.deepcopy(next(item for item in old_manifest if item['path'] == 'impl.py'))
        helper_manifest['path'] = 'helper.py'
        generated_manifest = {'path': 'generated/helper_copy.py', 'owner': 'implementation',
            'kind': 'generated', 'operation': 'add', 'basis': copy.deepcopy(helper_manifest['basis']),
            'source_paths': ['helper.py']}
        revised_manifest = sorted([*old_manifest, helper_manifest, generated_manifest],
                                  key=lambda item: item['path'])
        validation = copy.deepcopy(flow.state()['control']['context']['handoff_progress']['validation_plan'])
        validation['review_required'][0]['command'] += ' revised'
        report = {'revision_id': 'inline-private-1',
            'expected_authority_revision': authority['revision'],
            'expected_authority_digest': fixture.fixture.transfer.entry.digest(authority),
            'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:inline-private'},
            'report': {'ticket_ids': [plan['ticket_id']], 'step_ids': [plan['steps'][0]['id']],
                'reason': 'Reorder a private helper', 'production_facts': 'Same output and caller',
                'contract_before': authority['execution_plan']['contract_digest'],
                'contract_after': authority['execution_plan']['contract_digest'],
                'dependency_impact': [plan['steps'][0]['id']],
                'verification_impact': [plan['verifications'][0]['id']],
                'carry_forward': [], 'manifest_diff': progress.stage_scope.manifest_diff(
                    old_manifest, revised_manifest)}, 'planning_paths': [],
            'manifest': revised_manifest, 'inline_plan': plan, 'validation_plan': validation}
        report['generated_outputs'] = {'helper.py': ['generated/helper_copy.py']}
        flow.invoke('prepare-plan-revision', report)
        prepared = flow.invoke('observe', flow.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        activated = flow.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
            'controller_ref': 'task', 'write_release': prepared['next_action']['write_release'],
            'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated', activated)
        revised = flow.state()['scope_authority']['execution_plan']
        self.assertEqual(revised['original_scope_revision'], authority['execution_plan']['original_scope_revision'])
        self.assertIsNone(revised['plan_commit'])
        self.assertEqual(revised['sources'][0]['input_digest'],
                         authority['execution_plan']['sources'][0]['input_digest'])
        port = {'record': flow.state()['dispatch'], 'control': copy.deepcopy(flow.state()['control'])}
        progress.dispatch.record_for(port)
        self.assertEqual(port['_current_stage3_progress']['plan_authority']['revision'], 2)
        self.assertEqual(port['_current_stage3_progress']['validation_plan'], validation)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        allocation_input = flow.execution_input()
        allocation_input['scope']['owned_paths'] = ['generated/helper_copy.py', 'helper.py', 'impl.py']
        allocation_input['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            allocation_input['scope'])
        ticket = flow.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        allocation_input['semantic']['plan_refs'].append(progress.execution_plan.step_ref(ticket, helper_step))
        prepared_allocation = flow.invoke('allocation', {'allocation_id': 'inline-current', 'operation': 'prepare',
            'handoff': allocation_input})
        self.assertIsNotNone(prepared_allocation['next_action'], prepared_allocation)
        self.assertEqual(prepared_allocation['next_action']['operation'], 'invoke-host', prepared_allocation)
        slot = flow.state()['allocations']['inline-current']
        record = progress.dispatch.record_for({'record': slot['record'],
            'control': copy.deepcopy(flow.state()['control'])})
        original_input = progress.dispatch.dispatch_input(record)
        payload = progress.handoff.unseal(progress.dispatch.allocation_launch(record))['payload']
        source = original_input['inline_authority']['execution_plan']['sources'][0]
        self.assertEqual(source['input_digest'], authority['execution_plan']['sources'][0]['input_digest'])
        self.assertEqual(payload['execution_plan']['identity']['plan_digest'],
                         original_input['inline_authority']['execution_plan']['plan_digest'])
        self.assertNotEqual(payload['input_digest'], source['input_digest'])
        bound = flow.invoke('allocation', {'allocation_id': 'inline-current', 'operation': 'bind',
            'receipt': flow.receipt(slot['record'], ref='native:inline-agent')})
        release = bound['next_action']['result']['write_release']
        implementation = b"print('revised inline behavior')\n"
        helper_bytes = b"def helper():\n    return 'revised inline behavior'\n"
        (flow.flow / 'generated').mkdir()
        (flow.flow / 'impl.py').write_bytes(implementation)
        (flow.flow / 'helper.py').write_bytes(helper_bytes)
        (flow.flow / 'generated/helper_copy.py').write_bytes(helper_bytes)
        slot = flow.state()['allocations']['inline-current']
        result_payload = {'changed_paths': ['generated/helper_copy.py', 'helper.py', 'impl.py'],
            'file_hashes': {'impl.py': hashlib.sha256(implementation).hexdigest(),
                            'helper.py': hashlib.sha256(helper_bytes).hexdigest(),
                            'generated/helper_copy.py': hashlib.sha256(helper_bytes).hexdigest()},
            'tests': ['revised behavior passed'], 'write_release': release,
            'plan_refs': allocation_input['semantic']['plan_refs'],
            'plan_authority': flow.state()['control']['context']['handoff_progress']['plan_authority']}
        flow.invoke('allocation', {'allocation_id': 'inline-current', 'operation': 'receive',
            'receipt': flow.receipt(slot['record'], 'stopped', 'result', ref='native:inline-agent'),
            'result': {'delivery_id': 'inline-current-result', 'status': 'completed',
                       'payload': result_payload}})
        accepted_result = flow.invoke('allocation', {'allocation_id': 'inline-current',
            'operation': 'accept', 'decision': {'reference': 'dispatcher:inline-current'}})
        self.assertEqual(accepted_result['next_action']['result']['status'], 'accepted')
        flow.request = controller_request
        flow.settings = controller_settings
        flow.flow_git('add', 'impl.py', 'helper.py', 'generated/helper_copy.py')
        flow.flow_git('commit', '-qm', 'revised inline implementation')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.invoke('observe', flow.observation('idle', 'result', ref='native:dispatcher'))
        checkpoint = flow.state()['control']['context']['handoff_progress']
        checks = fixture.fixture.transfer.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        ready = flow.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': flow.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
            'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks},
            'receipt': {'adapter': 'fixture', 'call_ref': 'focused-inline',
                'response_ref': 'focused-inline-result', 'raw': {'checks': checks,
                    'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(flow.state()['control']['context']['handoff_progress']['state'], 'reviewable', ready)
        flow.mark_reviewable = lambda _candidate, fixture_execution=False: {
            'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks}
        payload = {'artifacts': ['impl.py', 'helper.py', 'generated/helper_copy.py'], 'checks': ['revised behavior passed'],
            'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': checks}}
        accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        self.assertEqual(flow.state()['accepted']['payload']['verification']['validation_plan'], validation)
        predecessor = flow.state()['accepted']
        completion = progress.handoff.render(predecessor,
            checkpoint_path=str(flow.checkpoint))['payload']
        self.assertIn('helper.py', completion['implementation_paths'])
        self.assertEqual(completion['closure_paths'],
                         flow.state()['scope_authority']['scope']['closure_paths'])
        closure = flow.input_for(4, predecessor)
        current_scope = flow.state()['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            closure['scope'][key] = copy.deepcopy(current_scope[key])
        closure['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(closure['scope'])
        with self.assertRaises(progress.entry.PreparationError) as missing_pin:
            progress.handoff.prepare(closure)
        self.assertEqual(missing_pin.exception.code, 'handoff_not_ready')
        stale = copy.deepcopy(closure)
        stale['scope']['implementation_paths'] = predecessor['handoff']['scope']['implementation_paths']
        stale['scope']['downstream'] = predecessor['handoff']['scope']['downstream']
        stale['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(stale['scope'])
        before = flow.checkpoint.read_bytes()
        flow.context = flow.context_for(4)
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': stale, 'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        widened = copy.deepcopy(closure)
        widened['scope']['owned_paths'] = ['helper.py']
        widened['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(widened['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': widened, 'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        started = flow.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host')
        stage4 = flow.state()
        self.assertEqual(stage4['handoff']['scope']['implementation_paths'],
                         current_scope['implementation_paths'])
        self.assertEqual(stage4['handoff']['scope']['baseline'],
                         predecessor['handoff']['scope']['baseline'])
        self.assertEqual(stage4['dispatch']['scope_checkpoint_path'], str(flow.checkpoint))
        self.assertEqual(progress.handoff.verify(stage4['handoff'],
                         checkpoint_path=str(flow.checkpoint)), stage4['handoff'])
        forged = progress.handoff.unseal(stage4['dispatch'])
        forged['scope_checkpoint_path'] = str(flow.checkpoint.with_name('forged-progress.json'))
        forged = progress.handoff.seal(forged)
        with self.assertRaises(progress.entry.PreparationError):
            progress.dispatch.record_for({'record': forged,
                'control': copy.deepcopy(stage4['control'])})
        original_checkpoint = flow.checkpoint.read_bytes()
        changed = progress.read_record(flow.checkpoint)
        changed[progress.KEY]['history'][-1]['plan_revision_transaction']['state'] = 'prepared'
        try:
            progress.atomic_save(flow.checkpoint, changed)
            with self.assertRaises(progress.entry.PreparationError):
                progress.handoff.verify(stage4['handoff'], checkpoint_path=str(flow.checkpoint))
        finally:
            flow.checkpoint.write_bytes(original_checkpoint)

    def test_staged_private_helper_revision_reaches_current_stage4_handoff(self):
        flow = self.new_active_flow()
        authority = flow.state()['scope_authority']
        report = self.report(flow)
        manifest = copy.deepcopy(authority['scope']['downstream']['manifest'])
        helper = copy.deepcopy(next(item for item in manifest if item['path'] == 'impl.py'))
        helper['path'] = 'helper.py'
        helper['basis']['section'] = 'D1'
        generated = {'path': 'generated/helper_copy.py', 'owner': 'implementation',
            'kind': 'generated', 'operation': 'add', 'basis': copy.deepcopy(helper['basis']),
            'source_paths': ['helper.py']}
        manifest = sorted([*manifest, helper, generated], key=lambda item: item['path'])
        report['manifest'] = manifest
        report['generated_outputs'] = {'helper.py': ['generated/helper_copy.py']}
        report['report']['manifest_diff'] = progress.stage_scope.manifest_diff(
            authority['scope']['downstream']['manifest'], manifest)
        flow.invoke('prepare-plan-revision', report)
        prepared = flow.invoke('observe', flow.observation('stopped', 'result',
            ref='native:dispatcher'))
        planning = flow.flow / 'docs/spec.md'
        body = planning.read_text()
        match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan['plan_version'] += 1
        plan['steps'][0]['approach'].append('Use the new private helper')
        extra = copy.deepcopy(plan['steps'][0])
        extra['id'] = 'staged-private-helper'
        extra['target'] = {'path': 'helper.py', 'symbol': 'helper', 'operation': 'add'}
        extra['depends_on'] = [plan['steps'][0]['id']]
        plan['steps'].append(extra)
        self.refresh_fixture_slices(plan)
        planning.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                            .replace('Plan Version: 1', 'Plan Version: 2'))
        flow.flow_git('add', 'docs/spec.md')
        flow.flow_git('commit', '--only', '-qm', 'revise staged private helper',
                      '--', 'docs/spec.md')
        activated = flow.invoke('commit-plan-revision', {
            'revision_id': report['revision_id'], 'controller_ref': 'task',
            'write_release': prepared['next_action']['write_release'],
            'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated',
                         activated)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        ticket = flow.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        (flow.flow / 'generated').mkdir()
        self.accept_planned_group(flow, ticket, ['FP1-01', 'FP1-02', 'staged-private-helper'], {
            'impl.py': b"print('staged behavior')\n",
            'helper.py': b"def helper():\n    return 'staged behavior'\n",
            'generated/helper_copy.py': b"def helper():\n    return 'staged behavior'\n",
        }, 'staged-helper')
        flow.request = controller_request
        flow.settings = controller_settings
        flow.flow_git('add', 'impl.py', 'helper.py', 'generated/helper_copy.py')
        flow.flow_git('commit', '-qm', 'implement staged private helper')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.invoke('observe', flow.observation('idle', 'result', ref='native:dispatcher'))
        checkpoint = flow.state()['control']['context']['handoff_progress']
        checks = fixture.fixture.transfer.checks_for(
            checkpoint['validation_plan']['review_required'], candidate)
        ready = flow.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': flow.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
            'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks},
            'receipt': {'adapter': 'fixture', 'call_ref': 'focused-staged',
                'response_ref': 'focused-staged-result', 'raw': {'checks': checks,
                    'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(flow.state()['control']['context']['handoff_progress']['state'],
                         'reviewable', ready.get('error'))
        flow.mark_reviewable = lambda _candidate, fixture_execution=False: {
            'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks}
        payload = {'artifacts': ['impl.py', 'helper.py', 'generated/helper_copy.py'], 'checks': ['staged behavior passed'],
            'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate}}
        accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        predecessor = flow.state()['accepted']
        current_scope = flow.state()['scope_authority']['scope']
        closure = flow.input_for(4, predecessor)
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            closure['scope'][key] = copy.deepcopy(current_scope[key])
        closure['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            closure['scope'])
        flow.context = flow.context_for(4)
        before = flow.checkpoint.read_bytes()
        stale_map = copy.deepcopy(closure)
        generated_item = next(item for item in stale_map['scope']['downstream']['manifest']
                              if item['path'] == 'generated/helper_copy.py')
        generated_item['source_paths'] = ['impl.py']
        stale_map['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            stale_map['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': stale_map,
                'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        stale_basis = copy.deepcopy(closure)
        helper_item = next(item for item in stale_basis['scope']['downstream']['manifest']
                           if item['path'] == 'helper.py')
        helper_item['basis']['section'] = 'approved design'
        stale_basis['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            stale_basis['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            flow.invoke('start', {'handoff': stale_basis,
                'control': {'context': flow.context, 'discussion': None}})
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        started = flow.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host')
        stage4 = flow.state()
        self.assertEqual(stage4['dispatch']['scope_checkpoint_path'], str(flow.checkpoint))
        self.assertEqual(progress.handoff.verify(stage4['handoff'],
            checkpoint_path=str(flow.checkpoint)), stage4['handoff'])

    def test_public_contract_change_keeps_prepared_barrier_without_activation(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        spec.write_text(original.replace("AC-1: approved behavior", "AC-1: changed behavior"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "change public behavior", "--", "docs/spec.md")
        candidate = flow.flow_git("rev-parse", "HEAD")
        before = flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": candidate})
        self.assertEqual(error.exception.code, "contract_changed")
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "prepared")

    def test_plan_revision_cannot_change_ticket_comments_or_spec_archive(self):
        for ticket in (False, True):
            with self.subTest(ticket=ticket):
                flow = self.new_active_flow(ticket=ticket)
                report = self.report(flow)
                flow.invoke('prepare-plan-revision', report)
                prepared = flow.invoke('observe', flow.observation(
                    'stopped', 'result', ref='native:dispatcher'))
                relative = 'docs/issues/FEP-01.md' if ticket else 'docs/spec.md'
                source = flow.flow / relative
                body = source.read_text()
                match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
                plan = json.loads(match.group(1))
                plan['plan_version'] += 1
                plan['steps'][0]['approach'].append('revise private helper')
                body = (body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                body = body.replace('Plan Version: 1', 'Plan Version: 2')
                if ticket:
                    body = body.replace('Initial implementation note.',
                        'Changed existing implementation note.')
                else:
                    body += '\n## Archive Notes\nUnreviewed historical claim.\n'
                source.write_text(body)
                flow.flow_git('add', relative)
                flow.flow_git('commit', '--only', '-qm', 'private plan and record rewrite',
                              '--', relative)
                before = flow.checkpoint.read_bytes()
                with self.assertRaises(progress.entry.PreparationError) as error:
                    flow.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
                        'controller_ref': 'task',
                        'write_release': prepared['next_action']['write_release'],
                        'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
                self.assertEqual(error.exception.code, 'contract_changed')
                self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_formal_revision_allows_private_n4_inventory_with_plan_change(self):
        flow = self.new_active_flow(retained_note=True)
        report = self.report(flow)
        flow.invoke('prepare-plan-revision', report)
        prepared = flow.invoke('observe', flow.observation(
            'stopped', 'result', ref='native:dispatcher'))
        source = flow.flow / 'docs/spec.md'
        body = source.read_text()
        match = re.search(r'```execution-plan-v2\n(.*?)\n```', body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan['plan_version'] += 1
        plan['steps'][0]['approach'].append('revise private helper')
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace('Plan Version: 1', 'Plan Version: 2')
                          .replace('Initial note.', 'Updated private inventory.'))
        flow.flow_git('add', 'docs/spec.md')
        flow.flow_git('commit', '--only', '-qm', 'revise plan and N4 inventory',
                      '--', 'docs/spec.md')
        activated = flow.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
            'controller_ref': 'task',
            'write_release': prepared['next_action']['write_release'],
            'planning_commit': flow.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated',
                         activated)

    def test_implementation_index_drift_rejects_planning_commit(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("move private helper")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "private plan", "--", "docs/spec.md")
        (flow.flow / "impl.py").write_text("unapproved implementation during planning\n")
        flow.flow_git("add", "impl.py")
        before = flow.checkpoint.read_bytes()
        with self.assertRaises(progress.control.ControlError) as error:
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertIn("index stages or flags changed", str(error.exception))
        self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_manifest_refinement_and_new_path_activate_together(self):
        flow = self.new_active_flow()
        authority = flow.state()["scope_authority"]
        manifest = copy.deepcopy(authority["scope"]["downstream"]["manifest"])
        manifest.append({"path": "helper.py", "owner": "implementation", "kind": "source",
            "operation": "add", "basis": {"commit": authority["candidate_commit"],
                "path": "docs/spec.md", "section": "approved design"}})
        manifest.sort(key=lambda item: item["path"])
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"]["manifest_diff"] = progress.stage_scope.manifest_diff(
            authority["scope"]["downstream"]["manifest"], manifest)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared")
        self.assertIsNone(flow.state()["plan_revision_transaction"]["snapshot"]["files"]["helper.py"])
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"].append({**copy.deepcopy(plan["steps"][0]), "id": "FP1-03",
            "target": {"path": "helper.py", "symbol": "helper", "operation": "add"},
            "depends_on": ["FP1-01"]})
        self.refresh_fixture_slices(plan)
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "add approved private helper plan", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated")
        self.assertEqual(flow.state()["scope_authority"]["scope"]["implementation_paths"],
                         ["helper.py", "impl.py"])
        self.assertEqual(flow.state()["control"]["context"]["handoff_progress"]["allowed_paths"],
                         ["helper.py", "impl.py"])

    def retired_helper_revision_scenario(self, *, use_new_id=False):
        flow = self.new_active_flow(read_dependency=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        authority = flow.state()["scope_authority"]
        ticket = authority["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "retired-input")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py",
                                 b"def later():\n    return 1\n", "retired-helper")
        flow.flow_git('add', 'impl.py', 'later.py')
        flow.flow_git('commit', '-qm', 'commit accepted helper implementation')
        implemented_helper = flow.flow_git('rev-parse', 'HEAD')
        flow.request = controller_request
        flow.settings = controller_settings
        old_manifest = authority["scope"]["downstream"]["manifest"]
        manifest = [item for item in old_manifest if item["path"] != "later.py"]
        report = self.report(flow)
        if use_new_id:
            report['revision_id'] = 'internal-9'
        rejected_id = report['revision_id']
        report["manifest"] = manifest
        report["report"].update(step_ids=["FP1-02"],
                                dependency_impact=["FP1-04", "FP1-05"],
                                verification_impact=["V2"],
                                manifest_diff=progress.stage_scope.manifest_diff(old_manifest, manifest))
        requested = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(requested["next_action"]["operation"], "stop-host", requested)
        before = flow.flow_git("rev-parse", "HEAD")
        stopped = flow.observation("stopped", "result", ref="native:dispatcher")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("observe", stopped)
        self.assertEqual(error.exception.code, "invalid_plan_revision")
        self.assertIn("later.py", str(error.exception))
        self.assertEqual(flow.state()["scope_authority"], authority)
        self.assertIsNone(flow.state().get("plan_revision_transaction"))
        changed_report = copy.deepcopy(report)
        changed_report["report"]["reason"] = "A different explanation"
        with self.assertRaises(progress.entry.PreparationError) as identity_conflict:
            flow.invoke("prepare-plan-revision", changed_report)
        self.assertEqual(identity_conflict.exception.code, "plan_revision_conflict")
        rejected = flow.state()["plan_revision_history"][report["revision_id"]]
        self.assertEqual(rejected["input"], report)
        self.assertEqual(rejected["input_digest"], progress.entry.digest(report))
        self.assertEqual(rejected["old_authority"], authority)
        self.assertEqual(rejected["state"], "rejected")
        saved_checkpoint = progress.read_record(flow.checkpoint)
        changed_checkpoint = copy.deepcopy(saved_checkpoint)
        changed_checkpoint[progress.KEY]["plan_revision_history"][report["revision_id"]][
            "rejection"]["result"]["message"] = "forged rejection result"
        progress.atomic_save(flow.checkpoint, changed_checkpoint)
        with self.assertRaises(progress.entry.PreparationError) as forged_rejection:
            flow.invoke("inspect")
        self.assertEqual(forged_rejection.exception.code, "invalid_checkpoint")
        progress.atomic_save(flow.checkpoint, saved_checkpoint)
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), before)
        self.assertIn("later.py", flow.state()["control"]["context"]["handoff_progress"]["allowed_paths"])
        reconciled = flow.invoke("reconcile-plan-revision", {"revision_id": report["revision_id"]})
        self.assertEqual(reconciled["next_action"]["operation"], "plan-revision-rejected")
        self.assertEqual(reconciled["next_action"]["result"]["code"], "invalid_plan_revision")
        self.assertEqual(reconciled["next_action"]["result"]["retired_paths"], ["later.py"])
        before_replay = flow.checkpoint.read_bytes()
        replayed = flow.invoke("prepare-plan-revision", report, revision=0)
        self.assertEqual(replayed["next_action"], reconciled["next_action"])
        self.assertEqual(flow.checkpoint.read_bytes(), before_replay)
        with self.assertRaises(progress.entry.PreparationError) as no_release:
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": "forged",
                "planning_commit": before})
        self.assertEqual(no_release.exception.code, "plan_revision_unavailable")
        self.assertEqual(flow.checkpoint.read_bytes(), before_replay)
        for field in ("report", "classification", "manifest"):
            altered = copy.deepcopy(report)
            if field == "report":
                altered["report"]["reason"] = "A different explanation"
            elif field == "classification":
                altered["classification"]["reference"] = "controller:different-evidence"
            else:
                altered["manifest"] = old_manifest
                altered["report"].pop("manifest_diff")
            before_conflict = flow.checkpoint.read_bytes()
            with self.subTest(field=field), self.assertRaises(progress.entry.PreparationError) as conflict:
                flow.invoke("prepare-plan-revision", altered)
            self.assertEqual(conflict.exception.code, "plan_revision_conflict")
            self.assertEqual(flow.checkpoint.read_bytes(), before_conflict)
            self.assertEqual(flow.flow_git("rev-parse", "HEAD"), before)
        if use_new_id:
            another_report = copy.deepcopy(report)
            another_report['revision_id'] = 'internal-8'
            with self.assertRaises(progress.entry.PreparationError) as another_rejection:
                flow.invoke('prepare-plan-revision', another_report)
            self.assertEqual(another_rejection.exception.code, 'invalid_plan_revision')
            another = flow.state()['plan_revision_history']['internal-8']
            self.assertEqual(another['state'], 'rejected')
            self.assertEqual(another['old_authority'], authority)
            self.assertEqual(another['rejection']['stop'], rejected['rejection']['stop'])
        resumed = flow.invoke("advance")
        self.assertEqual(resumed["next_action"]["operation"], "inspect-host-state", resumed)
        repeat = flow.invoke("advance")
        self.assertEqual(repeat["next_action"]["action_id"], resumed["next_action"]["action_id"])
        resumed = flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        self.assertEqual(resumed["next_action"]["operation"], "continue-host", resumed)
        repeat = flow.invoke("advance")
        self.assertEqual(repeat["next_action"]["operation"], "lookup-exact-action", repeat)
        self.assertEqual(repeat["next_action"]["action"]["action_id"],
                         resumed["next_action"]["action_id"])
        flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        (flow.flow / "later.py").unlink()
        flow.flow_git("add", "-u", "later.py")
        flow.flow_git("commit", "-qm", "unallocated helper cleanup")
        unallocated_head = flow.flow_git("rev-parse", "HEAD")
        before_unallocated_retry = flow.checkpoint.read_bytes()
        unallocated_retry = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(unallocated_retry["next_action"]["operation"], "plan-revision-rejected")
        self.assertEqual(flow.checkpoint.read_bytes(), before_unallocated_retry)
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), unallocated_head)
        flow.flow_git("reset", "--hard", implemented_helper)
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py", None,
                                 "retired-helper-cleanup", accept=False)
        flow.flow_git("add", "-u", "later.py")
        flow.flow_git("commit", "-qm", "received but unaccepted helper cleanup")
        before_unaccepted_retry = flow.checkpoint.read_bytes()
        unaccepted_retry = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(unaccepted_retry["next_action"]["operation"], "plan-revision-rejected")
        self.assertEqual(flow.checkpoint.read_bytes(), before_unaccepted_retry)
        flow.flow_git("reset", "--mixed", implemented_helper)
        accepted_cleanup = flow.invoke("allocation", {"allocation_id": "retired-helper-cleanup",
            "operation": "accept", "decision": {"reference": "dispatcher:retired-helper-cleanup"}})
        self.assertEqual(accepted_cleanup["next_action"]["result"]["status"], "accepted")
        self.assertFalse((flow.flow / "later.py").exists())
        flow.flow_git('add', '-u', 'later.py')
        flow.flow_git('commit', '-qm', 'commit accepted helper cleanup')
        cleaned_helper = flow.flow_git('rev-parse', 'HEAD')
        self.assertNotEqual(implemented_helper, cleaned_helper)
        (flow.flow / "later.py").write_text("unallocated later rewrite\n")
        flow.flow_git("add", "later.py")
        flow.flow_git("commit", "-qm", "unallocated rewrite after cleanup")
        (flow.flow / "later.py").unlink()
        flow.flow_git("add", "-u", "later.py")
        flow.flow_git("commit", "-qm", "restore helper after unallocated rewrite")
        before_later_retry = flow.checkpoint.read_bytes()
        later_retry = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(later_retry["next_action"]["operation"], "plan-revision-rejected")
        self.assertEqual(flow.checkpoint.read_bytes(), before_later_retry)
        flow.flow_git("reset", "--hard", cleaned_helper)
        (flow.flow / "later.py").write_text("staged but absent\n")
        flow.flow_git("add", "later.py")
        (flow.flow / "later.py").unlink()
        before_index_retry = flow.checkpoint.read_bytes()
        index_retry = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(index_retry["next_action"]["operation"], "plan-revision-rejected")
        self.assertEqual(flow.checkpoint.read_bytes(), before_index_retry)
        flow.flow_git("reset", "--mixed", cleaned_helper)
        flow.request = controller_request
        flow.settings = controller_settings
        if use_new_id:
            report = copy.deepcopy(report)
            report["revision_id"] = "internal-2"
        retried = flow.invoke("prepare-plan-revision", report)
        self.assertEqual(retried["next_action"]["operation"], "stop-host", retried)
        self.assertNotEqual(retried["next_action"]["action_id"], requested["next_action"]["action_id"])
        retry_transaction = flow.state()["plan_revision_transaction"]
        self.assertEqual(retry_transaction["state"], "stopping")
        if use_new_id:
            self.assertEqual(flow.state()["plan_revision_history"][rejected_id], rejected)
            self.assertNotIn("prior_rejections", retry_transaction)
        else:
            self.assertEqual(retry_transaction["prior_rejections"], [rejected["rejection"]])
        self.assertNotIn("snapshot", retry_transaction)
        self.assertNotIn("write_release", retry_transaction)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(prepared["next_action"]["operation"], "plan-revision-prepared", prepared)
        self.assertEqual(flow.state()["plan_revision_transaction"]["snapshot"]["head"], cleaned_helper)
        source = flow.flow / "docs/spec.md"
        body = source.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", body, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"] = [step for step in plan["steps"] if step["id"] != "FP1-02"]
        next(step for step in plan["steps"] if step["id"] == "FP1-04")["depends_on"] = []
        next(step for step in plan["steps"] if step["id"] == "FP1-05")["read_only"] = []
        self.refresh_fixture_slices(plan)
        source.write_text((body[:match.start(1)] + json.dumps(plan) + body[match.end(1):])
                          .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "retire baseline helper", "--", "docs/spec.md")
        activated = flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
            "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
            "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(activated["next_action"]["operation"], "plan-revision-activated", activated)
        if use_new_id:
            self.assertEqual(flow.state()["plan_revision_history"][rejected_id], rejected)
            self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "activated")
            old_reconcile = flow.invoke("reconcile-plan-revision", {"revision_id": rejected_id})
            self.assertEqual(old_reconcile["next_action"]["operation"], "plan-revision-rejected")
        self.assertNotIn("later.py", flow.state()["scope_authority"]["scope"]["implementation_paths"])
        self.assertNotIn("later.py", flow.state()["control"]["context"]["handoff_progress"]["allowed_paths"])
        flow.invoke('advance')
        flow.invoke('observe', flow.observation('running', 'result', ref='native:dispatcher'))
        current_ticket = flow.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        for identity, relative in (('FP1-03', 'independent.py'),
                                   ('FP1-04', 'downstream.py'), ('FP1-05', 'reader.py')):
            self.accept_planned_step(flow, current_ticket, identity, relative,
                (f'def {relative.removesuffix(".py")}():\n    return 2\n').encode(),
                'current-' + identity)
        flow.flow_git('add', 'independent.py', 'downstream.py', 'reader.py')
        flow.flow_git('commit', '-qm', 'commit current plan implementation')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.request = controller_request
        flow.settings = controller_settings
        flow.settings['thread_id'] = 'task'
        flow.request['host'].update(thread_id='task', role='controller', source_ref=None)
        flow.request['host'].pop('actor_ref', None)
        checkpoint = flow.state()['control']['context']['handoff_progress']
        checks = fixture.fixture.transfer.checks_for(
            checkpoint['validation_plan']['review_required'], candidate)
        evidence = {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': flow.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': flow.git('rev-parse', 'main'),
            'binding': flow.binding, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks,
            'source': {'adapter': 'fixture', 'call_ref': 'retired-helper-ready',
                'response_ref': 'retired-helper-ready-result',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': 'native:dispatcher'}}}
        ready = progress.control_git.verified_transition({'repository': str(flow.flow),
            'baseline': checkpoint['git_baseline_commit'], 'request': {
                'schema_version': 6, 'action': 'candidate-ready', 'actor_ref': 'task',
                'context': copy.deepcopy(flow.state()['control']['context']),
                'evidence': evidence}})
        self.assertTrue(ready['ok'], ready)
        self.assertEqual(ready['context']['handoff_progress']['state'], 'reviewable')
        (flow.flow / 'later.py').write_text('unauthorized retired helper\n')
        flow.flow_git('add', 'later.py')
        flow.flow_git('commit', '-qm', 'unaccepted retired helper rewrite')
        (flow.flow / 'later.py').unlink()
        flow.flow_git('add', '-u', 'later.py')
        flow.flow_git('commit', '-qm', 'revert unaccepted retired helper rewrite')
        forged_candidate = flow.flow_git('rev-parse', 'HEAD')
        forged_evidence = copy.deepcopy(evidence)
        forged_evidence['commit'] = forged_candidate
        forged_evidence['checks'] = fixture.fixture.transfer.checks_for(
            checkpoint['validation_plan']['review_required'], forged_candidate)
        forged_evidence['source']['raw']['checks'] = forged_evidence['checks']
        with self.assertRaisesRegex(progress.control.ControlError,
                                    'candidate history escapes implementation scope'):
            progress.control_git.verified_transition({'repository': str(flow.flow),
                'baseline': checkpoint['git_baseline_commit'], 'request': {
                    'schema_version': 6, 'action': 'candidate-ready', 'actor_ref': 'task',
                    'context': copy.deepcopy(flow.state()['control']['context']),
                    'evidence': forged_evidence}})

        flow.flow_git('reset', '--hard', candidate)
        ready = flow.invoke('control', {'action': 'candidate-ready', 'evidence': {
            key: copy.deepcopy(value) for key, value in evidence.items() if key != 'source'},
            'receipt': evidence['source']})
        self.assertEqual(flow.state()['control']['context']['handoff_progress']['state'],
                         'reviewable', ready)
        flow.mark_reviewable = lambda _candidate, fixture_execution=False: {
            'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks}
        payload = {'artifacts': ['impl.py', 'independent.py', 'downstream.py', 'reader.py'],
            'checks': ['retired helper behavior passed'], 'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate}}
        original_observation = flow.observation
        historical_refs = {item['agent_ref'] for source in checkpoint['plan_revision_sources']
                           for item in source['executions'] if item['agent_ref']}
        def with_historical_agents(*args, **kwargs):
            observation = original_observation(*args, **kwargs)
            if 'lifecycle' in observation:
                members = observation['lifecycle']['pages'][0]['response']['data']
                known = {item['id'] for item in members}
                members.extend({'id': ref, 'status': {'type': 'idle'}}
                               for ref in historical_refs - known)
            return observation
        with patch.object(flow, 'observation', side_effect=with_historical_agents):
            if use_new_id:
                with patch.object(progress.handoff, 'render', side_effect=RuntimeError('lost accepted response')):
                    with self.assertRaisesRegex(RuntimeError, 'lost accepted response'):
                        flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
                self.assertEqual(flow.state()['status'], 'accepted')
            else:
                accepted = flow.accept_current(payload, 'native:dispatcher', fixture_execution=False)
                self.assertEqual(accepted['status'], 'accepted', accepted)
        if use_new_id:
            replayed = flow.invoke('inspect')
            self.assertEqual(replayed['status'], 'accepted')
            self.assertEqual(replayed['completion'], flow.invoke('inspect')['completion'])
            self.assertEqual(replayed['completion']['payload']['candidate_commit'], candidate)
        predecessor = flow.state()['accepted']
        current_scope = flow.state()['scope_authority']['scope']
        closure = flow.input_for(4, predecessor)
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            closure['scope'][key] = copy.deepcopy(current_scope[key])
        closure['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(
            closure['scope'])
        with self.assertRaises(progress.entry.PreparationError) as wrong_pin:
            progress.handoff.prepare(closure, checkpoint_path=str(flow.checkpoint.with_name('other.json')))
        self.assertEqual(wrong_pin.exception.code, 'handoff_not_ready')
        stale = copy.deepcopy(closure)
        stale['scope']['implementation_paths'] = predecessor['handoff']['scope']['implementation_paths']
        stale['scope']['downstream'] = predecessor['handoff']['scope']['downstream']
        stale['authorization']['scope_digest'] = fixture.fixture.transfer.entry.digest(stale['scope'])
        with self.assertRaises(progress.entry.PreparationError):
            progress.handoff.prepare(stale, checkpoint_path=str(flow.checkpoint))
        if use_new_id:
            original_checkpoint = progress.read_record(flow.checkpoint)
            for mutation in ('unknown-state', 'wrong-old-authority', 'reordered-history',
                             'wrong-rejection-evidence'):
                changed = copy.deepcopy(original_checkpoint)
                rejected_entry = changed[progress.KEY]['plan_revision_history'][rejected_id]
                if mutation == 'unknown-state':
                    rejected_entry['state'] = 'unknown'
                elif mutation == 'wrong-old-authority':
                    rejected_entry['old_authority']['scope']['implementation_paths'] = ['forged.py']
                elif mutation == 'reordered-history':
                    rejected_entry['old_authority'] = copy.deepcopy(
                        changed[progress.KEY]['plan_revision_transaction']['proposed_authority'])
                    rejected_entry['rejection']['result']['retired_paths'] = ['impl.py']
                    rejected_entry['rejection']['result']['message'] = (
                        'retired implementation paths still differ from baseline: impl.py')
                if mutation in ('wrong-old-authority', 'reordered-history'):
                    rejected_entry['rejection']['result']['evidence_digest'] = progress.entry.digest({
                        'input_digest': rejected_entry['input_digest'],
                        'old_authority_digest': progress.entry.digest(rejected_entry['old_authority']),
                        'snapshot': rejected_entry['rejection']['snapshot'],
                        'stop': rejected_entry['rejection']['stop'],
                        'accepted_executions': rejected_entry['rejection']['accepted_executions'],
                        'retired_paths': rejected_entry['rejection']['result']['retired_paths']})
                elif mutation == 'wrong-rejection-evidence':
                    rejected_entry['rejection']['result']['message'] = 'forged rejection'
                progress.atomic_save(flow.checkpoint, changed)
                with self.subTest(mutation=mutation), self.assertRaises(progress.entry.PreparationError):
                    flow.invoke('inspect')
                progress.atomic_save(flow.checkpoint, original_checkpoint)
        prepared_closure = progress.handoff.prepare(closure, checkpoint_path=str(flow.checkpoint))
        self.assertEqual(progress.handoff.verify(prepared_closure,
            checkpoint_path=str(flow.checkpoint)), prepared_closure)
        flow.context = flow.context_for(4)
        started = flow.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host', started)
        stage4 = flow.state()
        self.assertEqual(progress.handoff.verify(stage4['handoff'],
            checkpoint_path=str(flow.checkpoint)), stage4['handoff'])

    def test_retired_helper_with_accepted_bytes_cannot_receive_planning_release(self):
        self.retired_helper_revision_scenario()

    def test_rejected_revision_id_remains_audit_after_different_id_activates(self):
        self.retired_helper_revision_scenario(use_new_id=True)

    def test_retired_helper_with_unaccepted_bytes_cannot_receive_planning_release(self):
        flow = self.new_active_flow(read_dependency=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        authority = flow.state()["scope_authority"]
        ticket = authority["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "pending-input")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py",
                                 b"def later():\n    return 1\n", "pending-helper", accept=False)
        executions = flow.state()["control"]["context"]["handoff_progress"]["executions"]
        self.assertEqual(executions[-1]["state"], "received")
        self.assertTrue(executions[-1]["stopped"])
        flow.request = controller_request
        flow.settings = controller_settings
        old_manifest = authority["scope"]["downstream"]["manifest"]
        manifest = [item for item in old_manifest if item["path"] != "later.py"]
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"].update(step_ids=["FP1-02"],
                                dependency_impact=["FP1-04", "FP1-05"],
                                verification_impact=["V2"],
                                manifest_diff=progress.stage_scope.manifest_diff(old_manifest, manifest))
        flow.invoke("prepare-plan-revision", report)
        before = flow.flow_git("rev-parse", "HEAD")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(error.exception.code, "invalid_plan_revision")
        self.assertIn("later.py", str(error.exception))
        self.assertEqual(flow.state()["scope_authority"], authority)
        self.assertIsNone(flow.state().get("plan_revision_transaction"))
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), before)
        inspected = flow.invoke("advance")
        self.assertEqual(inspected["next_action"]["operation"], "inspect-host-state")
        uncertain = flow.observation("unknown", "result", ref="native:dispatcher")
        unresolved = flow.invoke("observe", uncertain)
        self.assertNotEqual((unresolved.get("next_action") or {}).get("operation"), "continue-host")
        replay = flow.invoke("observe", uncertain, revision=0)
        self.assertTrue(replay["acknowledged"])
        self.assertEqual(flow.state()["scope_authority"], authority)

    def test_paused_retired_helper_rejection_keeps_original_recovery(self):
        flow = self.new_active_flow(read_dependency=True)
        controller_request = copy.deepcopy(flow.request)
        controller_settings = copy.deepcopy(flow.settings)
        authority = flow.state()["scope_authority"]
        ticket = authority["execution_plan"]["catalog"]["tickets"][0]
        self.accept_planned_step(flow, ticket, "FP1-01", "impl.py",
                                 b"def impl():\n    return 1\n", "paused-input")
        self.accept_planned_step(flow, ticket, "FP1-02", "later.py",
                                 b"def later():\n    return 1\n", "paused-helper")
        flow.request = controller_request
        flow.settings = controller_settings
        old_manifest = authority["scope"]["downstream"]["manifest"]
        manifest = [item for item in old_manifest if item["path"] != "later.py"]
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"].update(step_ids=["FP1-02"],
                                dependency_impact=["FP1-04", "FP1-05"],
                                verification_impact=["V2"],
                                manifest_diff=progress.stage_scope.manifest_diff(old_manifest, manifest))
        flow.invoke("prepare-plan-revision", report)
        flow.invoke("pause")
        flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(flow.state()["status"], "paused")
        with self.assertRaises(progress.entry.PreparationError) as error:
            flow.invoke("resume")
        self.assertEqual(error.exception.code, "invalid_plan_revision")
        self.assertIn("later.py", str(error.exception))
        self.assertEqual(flow.state()["status"], "active")
        self.assertEqual(flow.state()["scope_authority"], authority)
        self.assertIsNone(flow.state().get("plan_revision_transaction"))
        inspected = flow.invoke("advance")
        self.assertEqual(inspected["next_action"]["operation"], "inspect-host-state", inspected)
        resumed = flow.invoke("observe", flow.observation("idle", "result", ref="native:dispatcher"))
        self.assertEqual(resumed["next_action"]["operation"], "continue-host", resumed)

    def test_generated_output_cannot_disappear_without_a_new_full_inventory(self):
        flow = self.new_active_flow(generated=True)
        authority = flow.state()["scope_authority"]
        self.assertEqual(authority["proposal"]["generated_outputs"],
                         {"impl.py": ["generated/impl_copy.py"]})
        manifest = [item for item in authority["scope"]["downstream"]["manifest"]
                    if item["path"] != "generated/impl_copy.py"]
        report = self.report(flow)
        report["manifest"] = manifest
        report["report"]["manifest_diff"] = progress.stage_scope.manifest_diff(
            authority["scope"]["downstream"]["manifest"], manifest)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("private revision with incomplete output map")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "plan missing generated inventory", "--", "docs/spec.md")
        before = flow.checkpoint.read_bytes()
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": prepared["next_action"]["write_release"],
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        self.assertEqual(error.exception.code, "scope_incomplete")
        self.assertEqual(flow.checkpoint.read_bytes(), before)

    def test_pause_and_cancel_never_activate_prepared_release(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        release = prepared["next_action"]["write_release"]
        paused = flow.invoke("pause")
        self.assertIn(paused["status"], {"paused", "pausing"})
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError):
            flow.invoke("commit-plan-revision", {"revision_id": report["revision_id"],
                "controller_ref": "task", "write_release": release,
                "planning_commit": flow.flow_git("rev-parse", "HEAD")})
        cancelled = flow.invoke("cancel")
        self.assertIn(cancelled["status"], {"cancelled", "cancelling"})
        self.assertNotIn("write_release", flow.invoke("reconcile-plan-revision",
            {"revision_id": report["revision_id"]})["next_action"])

    def test_lost_activation_response_reconciles_same_commit_and_authority(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("use the same approved public behavior")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "revise plan once", "--", "docs/spec.md")
        candidate = flow.flow_git("rev-parse", "HEAD")
        request = {"revision_id": report["revision_id"], "controller_ref": "task",
                   "write_release": prepared["next_action"]["write_release"],
                   "planning_commit": candidate}
        original_control = progress.Progress.control_action

        def lost_after_control(owner, input_data):
            original_control(owner, input_data)
            raise RuntimeError("lost response after control commit")

        with patch.object(progress.Progress, "control_action", lost_after_control):
            with self.assertRaisesRegex(RuntimeError, "lost response"):
                flow.invoke("commit-plan-revision", request)
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "activated")
        self.assertTrue(flow.invoke("commit-plan-revision", request, revision=0)["acknowledged"])
        self.assertTrue(flow.invoke("reconcile-plan-revision",
            {"revision_id": report["revision_id"]}, revision=0)["acknowledged"])
        self.assertEqual(flow.flow_git("rev-parse", "HEAD"), candidate)
        self.assertEqual(len(flow.state()["control"]["context"]["handoff_progress"]["plan_revisions"]), 1)

    def test_planning_committed_reconcile_respects_pause_and_new_host_activity(self):
        flow = self.new_active_flow()
        report = self.report(flow)
        flow.invoke("prepare-plan-revision", report)
        prepared = flow.invoke("observe", flow.observation("stopped", "result", ref="native:dispatcher"))
        spec = flow.flow / "docs/spec.md"
        original = spec.read_text()
        match = re.search(r"```execution-plan-v2\n(.*?)\n```", original, re.DOTALL)
        plan = json.loads(match.group(1))
        plan["plan_version"] = 2
        plan["steps"][0]["approach"].append("keep the same output after moving a helper")
        spec.write_text((original[:match.start(1)] + json.dumps(plan) + original[match.end(1):])
                        .replace("Plan Version: 1", "Plan Version: 2"))
        flow.flow_git("add", "docs/spec.md")
        flow.flow_git("commit", "--only", "-qm", "save planning before activation", "--", "docs/spec.md")
        request = {"revision_id": report["revision_id"], "controller_ref": "task",
                   "write_release": prepared["next_action"]["write_release"],
                   "planning_commit": flow.flow_git("rev-parse", "HEAD")}
        with patch.object(progress.Progress, "control_action", side_effect=RuntimeError("lost before control")):
            with self.assertRaisesRegex(RuntimeError, "lost before control"):
                flow.invoke("commit-plan-revision", request)
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "planning-committed")
        flow.invoke("observe", flow.observation("running", "result", ref="native:dispatcher"))
        with self.assertRaises(fixture.fixture.transfer.entry.PreparationError) as error:
            flow.invoke("reconcile-plan-revision", {"revision_id": report["revision_id"]})
        self.assertEqual(error.exception.code, "host_evidence_missing")
        flow.invoke("pause")
        paused = flow.invoke("reconcile-plan-revision", {"revision_id": report["revision_id"]})
        self.assertNotEqual(paused["next_action"]["operation"], "plan-revision-activated")
        self.assertEqual(flow.state()["plan_revision_transaction"]["state"], "planning-committed")
        self.assertEqual(flow.state()["scope_authority"]["revision"], 1)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(PlanRevisionProgressTests)
