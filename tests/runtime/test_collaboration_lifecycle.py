"""Interactive tool receipts through C/B and real temporary Git publication."""
import copy
import json
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures

progress = fixtures.progress


class CollaborationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProgressTests(methodName='runTest')
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def inventory(self, agents=None):
        return {'adapter': 'collaboration', 'controller_ref': '/root',
                'request': {'tool': 'collaboration.list_agents', 'arguments': {}},
                'response': {'agents': agents if agents is not None else [
                    {'agent_name': '/root', 'agent_status': 'running'},
                    {'agent_name': '/root/designer', 'agent_status': {'completed': 'plan ready'}}]}}

    def start(self):
        f = self.f
        f.invoke('start', {'handoff': f.input, 'control': {'context': f.context, 'discussion': None},
                          'native_host': self.inventory([{'agent_name': '/root', 'agent_status': 'running'}])})
        f.invoke('observe', f.observation(ref='/root/designer'))

    def candidate(self):
        f = self.f
        (f.flow / 'docs/spec.md').write_text(f.planning_spec())
        f.flow_git('add', 'docs/spec.md')
        f.flow_git('commit', '-qm', 'plan')
        candidate = f.flow_git('rev-parse', 'HEAD')
        proposal_digest = f.propose_design_scope(candidate)
        observed = f.observation('stopped', 'result', ref='/root/designer')
        observed.pop('lifecycle')
        observed['publication_candidate'] = {'candidate_commit': candidate,
            'artifacts': ['docs/spec.md'], 'checks': ['design readiness']}
        pending = f.invoke('observe', observed)['pending']
        f.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
                           'answer': 'accept', 'reference': 'controller:ready'})
        f.seal_design_scope(proposal_digest)
        return candidate

    def snapshot(self, inventory=None):
        f = self.f
        action = f.invoke('lifecycle-query')['next_action']
        return {'query_id': action['query_id'], 'call_ref': 'call:' + action['query_id'],
                'response_ref': 'response:' + action['query_id'], 'inventory': inventory or self.inventory()}

    def missing_baseline(self):
        f = self.f
        baseline = {'agent_name': '/root/old_review', 'agent_status': {'completed': 'old review completed'}}
        f.invoke('start', {'handoff': f.input, 'control': {'context': f.context, 'discussion': None},
            'native_host': self.inventory([{'agent_name': '/root', 'agent_status': 'running'}, baseline])})
        f.invoke('observe', f.observation(ref='/root/designer'))
        self.candidate()
        f.invoke('lifecycle-state', self.snapshot())
        self.assertFalse(f.state()['stopped'])
        return {'ref': '/root/old_review', 'binding': {
            'call_ref': 'actual-parent-read', 'response_ref': 'actual-parent-response',
            'raw': {'thread': {'id': 'task', 'hostId': 'local'}, 'turns': [{'items': [
                {'type': 'subAgentActivity', 'agentPath': '/root/old_review',
                 'agentThreadId': 'old-review-thread'}]}]}}}

    def baseline_readback(self, query):
        return {'query_id': query['query_id'], 'call_ref': 'read:' + query['query_id'],
            'response_ref': 'read-response:' + query['query_id'], 'raw': {
                'thread': {'id': 'old-review-thread', 'hostId': 'local', 'status': {'type': 'notLoaded'}},
                'page': {'order': 'newest_first'}, 'turns': [{'id': 'latest-old-review-turn',
                    'status': 'completed', 'items': [{'type': 'agentMessage', 'phase': 'final_answer',
                        'text': 'old review completed; commands=stopped'}]}]}}

    def test_missing_baseline_exact_current_read_allows_publication_without_archiving(self):
        f = self.f
        request = self.missing_baseline()
        before = copy.deepcopy({key: f.state()[key] for key in ('dispatch', 'control', 'native_host')})
        query = f.invoke('baseline-thread-query', request)['next_action']
        self.assertEqual(query['tool'], 'mcp__codex_app__read_thread')
        self.assertEqual(query['arguments'], {'threadId': 'old-review-thread', 'hostId': 'local', 'turnLimit': 1})
        f.invoke('baseline-thread-state', self.baseline_readback(query))
        result = f.invoke('publication', {'candidate_commit': f.flow_git('rev-parse', 'HEAD'),
            'reference': 'controller:ready'})
        self.assertTrue(result['next_action']['publication']['result']['ok'], result)
        self.assertEqual({key: f.state()[key] for key in before}, before)
        self.assertFalse(f.state()['native_finalizations'])

    def test_missing_baseline_inventory_or_parent_call_cannot_be_relabelled_as_thread_read(self):
        f = self.f
        request = self.missing_baseline()
        query = f.invoke('baseline-thread-query', request)['next_action']
        for source in (f.state()['lifecycle_snapshot'], request['binding']):
            data = self.baseline_readback(query)
            data['call_ref'], data['response_ref'] = source['call_ref'], source['response_ref']
            with self.subTest(source=source['call_ref']):
                result = f.invoke('baseline-thread-state', data)
                self.assertEqual(result['error']['code'], 'host_provenance_conflict', result)
                self.assertFalse(f.state()['stopped'])
        f.invoke('baseline-thread-state', self.baseline_readback(query))
        result = f.invoke('publication', {'candidate_commit': f.flow_git('rev-parse', 'HEAD'),
            'reference': 'controller:ready'})
        self.assertTrue(result['next_action']['publication']['result']['ok'], result)

    def test_missing_baseline_saved_readback_is_validated_before_use(self):
        f = self.f
        query = f.invoke('baseline-thread-query', self.missing_baseline())['next_action']
        f.invoke('baseline-thread-state', self.baseline_readback(query))
        envelope = json.loads(f.checkpoint.read_text())
        store = progress.state_store.StateStore(f.checkpoint)
        current = store.open_current()
        subject_id = current.root['metadata']['stage_subject']
        subject = store.read_subject(current, subject_id).value
        subject['baseline_thread_queries'][0]['result']['raw']['thread']['id'] = 'different-native-thread'
        subject_ref = store.write_object(current.run_id, 'subject', subject, subject_id=subject_id)
        scope = store.read_evidence(current.root['scope'], run_id=current.run_id, kind='scope')['value']
        scope['subject_ref'] = subject_ref
        scope['value']['state_digest'] = progress.entry.digest(subject)
        root = {**current.root, 'subjects': {**current.root['subjects'], subject_id: subject_ref},
                'scope': store.write_object(current.run_id, 'scope', scope)}
        root_ref = store.write_object(current.run_id, 'root', root)
        f.checkpoint.write_bytes(progress.state_store.encode({**envelope, 'current': root_ref}))
        with self.assertRaises(progress.entry.PreparationError) as rejected:
            f.invoke('inspect')
        self.assertEqual(rejected.exception.code, 'host_evidence_missing')

    def test_missing_baseline_wrong_or_active_readback_keeps_publication_blocked(self):
        f = self.f
        query = f.invoke('baseline-thread-query', self.missing_baseline())['next_action']
        head = f.git('rev-parse', 'HEAD')
        for mutation in ('wrong-thread', 'wrong-host', 'running', 'incomplete-turn', 'old-page',
                         'missing-final', 'running-command', 'error-response'):
            data = self.baseline_readback(query)
            raw = data['raw']
            if mutation == 'wrong-thread': raw['thread']['id'] = 'other-thread'
            elif mutation == 'wrong-host': raw['thread']['hostId'] = 'other-host'
            elif mutation == 'running': raw['thread']['status'] = {'type': 'active'}
            elif mutation == 'incomplete-turn': raw['turns'][0]['status'] = 'inProgress'
            elif mutation == 'old-page': raw['page']['order'] = 'oldest_first'
            elif mutation == 'missing-final': raw['turns'][0]['items'] = []
            elif mutation == 'running-command':
                raw['turns'][0]['items'].append({'type': 'commandExecution', 'status': 'inProgress'})
            else: raw['isError'] = True
            with self.subTest(mutation=mutation):
                result = f.invoke('baseline-thread-state', data)
                self.assertEqual(result['error']['code'], 'host_evidence_missing', result)
                self.assertFalse(f.state()['stopped'])
                self.assertEqual(f.git('rev-parse', 'HEAD'), head)
                self.assertIsNone(f.state()['baseline_thread_queries'][0]['result'])

    def test_missing_baseline_new_inventory_and_activity_require_new_thread_read(self):
        f = self.f
        request = self.missing_baseline()
        first = f.invoke('baseline-thread-query', request)['next_action']
        self.assertEqual(f.invoke('baseline-thread-query', request)['next_action'], first)
        first_read = self.baseline_readback(first)
        f.invoke('baseline-thread-state', first_read)
        f.invoke('lifecycle-state', self.snapshot())
        self.assertFalse(f.state()['stopped'])
        rejected = f.invoke('baseline-thread-state', first_read)
        self.assertEqual(rejected['error']['code'], 'host_evidence_missing')
        second = f.invoke('baseline-thread-query', request)['next_action']
        relabelled = {**first_read, 'query_id': second['query_id']}
        rejected = f.invoke('baseline-thread-state', relabelled)
        self.assertEqual(rejected['error']['code'], 'host_provenance_conflict')
        second_read = self.baseline_readback(second)
        f.invoke('baseline-thread-state', second_read)
        f.invoke('host-event', {'instance': f.state()['native_host']['instance'], 'event': {
            'type': 'subAgentActivity', 'id': 'new-baseline-turn', 'kind': 'interacted',
            'agentPath': request['ref'], 'agentThreadId': 'old-review-thread'}})
        self.assertFalse(f.state()['stopped'])
        rejected = f.invoke('baseline-thread-state', second_read)
        self.assertEqual(rejected['error']['code'], 'host_evidence_missing')

    def test_missing_baseline_does_not_hide_unknown_agents_or_missing_workflow_writer(self):
        f = self.f
        request = self.missing_baseline()
        agents = self.inventory()['response']['agents'] + [
            {'agent_name': '/root/unregistered', 'agent_status': {'completed': 'unknown completed'}}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        query = f.invoke('baseline-thread-query', request)['next_action']
        f.invoke('baseline-thread-state', self.baseline_readback(query))
        self.assertFalse(f.state()['stopped'])
        result = f.invoke('publication', {'candidate_commit': f.flow_git('rev-parse', 'HEAD'),
            'reference': 'controller:ready'})
        self.assertEqual(result['error']['code'], 'host_evidence_missing')
        wrong = copy.deepcopy(request)
        wrong['ref'] = '/root/designer'
        result = f.invoke('baseline-thread-query', wrong)
        self.assertEqual(result['error']['code'], 'identity_mismatch')

    def test_missing_baseline_binding_cannot_change_parent_or_native_identity(self):
        f = self.f
        request = self.missing_baseline()
        for mutation in ('different-parent', 'missing-event', 'ambiguous-identity', 'not-baseline'):
            wrong = copy.deepcopy(request)
            raw = wrong['binding']['raw']
            if mutation == 'different-parent': raw['thread']['id'] = 'other-controller'
            elif mutation == 'missing-event': raw['turns'][0]['items'] = []
            elif mutation == 'ambiguous-identity':
                raw['turns'][0]['items'].append({'type': 'subAgentActivity', 'agentPath': wrong['ref'],
                    'agentThreadId': 'other-child'})
            else: wrong['ref'] = '/root/unknown'
            with self.subTest(mutation=mutation):
                result = f.invoke('baseline-thread-query', wrong)
                self.assertEqual(result['error']['code'], 'identity_mismatch')
                self.assertFalse(f.state().get('baseline_thread_queries'))

    def test_missing_baseline_subtree_requires_the_original_whole_tree_barrier(self):
        f = self.f
        f.invoke('start', {'handoff': f.input, 'control': {'context': f.context, 'discussion': None},
            'native_host': self.inventory([{'agent_name': '/root', 'agent_status': 'running'},
                {'agent_name': '/root/old_review', 'agent_status': {'completed': 'stopped'}},
                {'agent_name': '/root/old_review/child', 'agent_status': {'completed': 'stopped'}}])})
        f.invoke('observe', f.observation(ref='/root/designer'))
        self.candidate()
        f.invoke('lifecycle-state', self.snapshot())
        request = {'ref': '/root/old_review',
            'binding': self.finalization('/root/old_review', 'old-review-thread')['binding']}
        result = f.invoke('baseline-thread-query', request)
        self.assertEqual(result['error']['code'], 'identity_mismatch')
        self.assertFalse(f.state()['stopped'])

    def test_missing_baseline_current_proof_allows_stage3_successor(self):
        f = self.f
        f.input['authorization']['flow_mode'] = 'continuous'
        self.test_missing_baseline_exact_current_read_allows_publication_without_archiving()
        pending = f.invoke('receive-publication')['pending']
        f.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:accept'})
        successor = f.input_for(3, f.state()['accepted'])
        successor['authorization']['flow_mode'] = 'continuous'
        result = f.invoke('start', {'handoff': successor, 'native_host': self.inventory()})
        self.assertEqual((result['next_action'] or {}).get('operation'), 'invoke-host', result)

    def test_missing_baseline_stage1_query_returns_structured_capability_error(self):
        f = self.f
        request = f.input_for(1)
        request['scope'].pop('downstream')
        request['scope'].update(implementation_paths=[], closure_paths=[])
        request['binding'] = None
        request['authorization']['scope_digest'] = progress.entry.digest(request['scope'])
        # Stage 1 saves a valid checkpoint before its dedicated launch plan;
        # native-only read operations must reject that pre-launch state cleanly.
        with self.assertRaises(progress.entry.PreparationError):
            f.invoke('start', {'handoff': request, 'control': {'context': f.context_for(1), 'discussion': None}})
        self.assertEqual(f.state()['stage'], 1)
        self.assertEqual(f.state()['native_host'], {})
        result = f.invoke('baseline-thread-query', {'ref': '/root/old_review', 'binding': {}})
        self.assertEqual(result['error']['code'], 'host_capability_missing')

    def test_missing_baseline_app_server_query_returns_structured_capability_error(self):
        f = self.f
        f.begin()
        result = f.invoke('baseline-thread-query', {'ref': '/root/old_review', 'binding': {}})
        self.assertEqual(result['error']['code'], 'host_capability_missing')

    def test_missing_baseline_historical_read_cannot_be_relabelled_after_stage_entry(self):
        f = self.f
        self.test_missing_baseline_current_proof_allows_stage3_successor()
        old_read = copy.deepcopy(f.state()['history'][-1]['baseline_thread_queries'][0]['result'])
        request = copy.deepcopy(f.state()['history'][-1]['baseline_thread_queries'][0]['input'])
        f.invoke('observe', f.observation(ref='/root/dispatcher'))
        stopped = f.observation('stopped', 'result', ref='/root/dispatcher')
        stopped.pop('lifecycle')
        f.invoke('observe', stopped)
        agents = self.inventory()['response']['agents'] + [
            {'agent_name': '/root/dispatcher', 'agent_status': {'completed': 'dispatcher stopped'}}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        query = f.invoke('baseline-thread-query', request)['next_action']
        relabelled = {**old_read, 'query_id': query['query_id']}
        result = f.invoke('baseline-thread-state', relabelled)
        self.assertEqual(result['error']['code'], 'host_provenance_conflict', result)
        self.assertFalse(f.state()['stopped'])
        f.invoke('baseline-thread-state', self.baseline_readback(query))
        self.assertTrue(f.state()['stopped'])

    def finalization(self, ref='/root/designer', thread='designer-thread'):
        parent_thread = 'dispatcher-runtime' if ref.startswith('/root/dispatcher/') else 'task'
        return {'operation': 'prepare', 'ref': ref, 'actor_ref': 'task',
            'reference': 'controller:no-further-reuse', 'reuse': False,
            'binding': {'call_ref': 'parent-read', 'response_ref': 'parent-response',
                'raw': {'thread': {'id': parent_thread, 'hostId': 'local'}, 'turns': [{'items': [
                    {'type': 'subAgentActivity', 'agentPath': ref, 'agentThreadId': thread}]}]}},
            'close': {'adapter': 'codex-desktop', 'request': {
                'tool': 'mcp__codex_app__set_thread_archived',
                'arguments': {'threadId': thread, 'hostId': 'local', 'archived': True}}}}

    def finalize_observation(self, action, status='completed'):
        ref, thread = action['payload']['ref'], action['payload']['thread_id']
        if action['operation'] == 'finish-native':
            raw = {'content': [{'type': 'text', 'text': json.dumps({
                'schemaVersion': 1, 'thread': {'id': thread, 'hostId': 'local', 'status': {'type': 'idle'}},
                'turns': [{'id': 'finish-turn', 'status': 'completed', 'items': [{
                    'type': 'agentMessage', 'phase': 'final_answer',
                    'text': 'NATIVE_FINISHED action_id=' + action['action_id'] + ' mailbox=consumed business_result=unchanged commands=stopped'}]}]})}], 'isError': False}
        else:
            raw = {'content': [{'type': 'text', 'text': json.dumps({'threadId': thread, 'archived': True})}], 'isError': False}
        return {'operation': 'observe', 'ref': ref, 'action_id': action['action_id'],
            'receipt': {'adapter': action['payload'].get('adapter', 'codex-desktop'),
                'call_ref': 'call:' + action['action_id'], 'response_ref': 'response:' + action['action_id'] + ':' + status,
                'status': status, 'ref': ref, 'raw': raw}}

    def unloaded_query(self, finish, close, reference='readback'):
        lookup = self.f.invoke('native-finalize', {'operation': 'lookup', 'ref': finish['payload']['ref']})['next_action']
        observed = self.finalize_observation(finish)
        observed['action_id'] = close['action_id']
        observed['receipt'].update(query=True, call_ref='call:' + reference, response_ref='response:' + reference)
        if 'query_for' in lookup:
            observed['receipt']['query_for'] = lookup['query_for']
        raw = json.loads(observed['receipt']['raw']['content'][0]['text'])
        raw['thread']['status'] = {'type': 'notLoaded'}
        observed['receipt']['raw']['content'][0]['text'] = json.dumps(raw)
        return observed

    def accepted_execution(self):
        f = self.f
        request = f.input_for(3)
        request['authorization']['flow_mode'] = 'continuous'
        f.context = f.context_for(3)
        f.invoke('start', {'handoff': request, 'control': {'context': f.context, 'discussion': None},
            'native_host': self.inventory([{'agent_name': '/root', 'agent_status': 'running'}])})
        f.invoke('observe', f.observation(ref='/root/dispatcher'))
        execution = self.execution_handoff()
        f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'prepare', 'handoff': execution})
        slot = f.state()['allocations']['first-slice']
        f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'bind',
            'receipt': f.receipt(slot['record'], ref='/root/dispatcher/executor')})
        data = b"def run():\n    return 'accepted first slice'\n"
        (f.flow / 'impl.py').write_bytes(data)
        slot = f.state()['allocations']['first-slice']
        f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'receive',
            'receipt': f.receipt(slot['record'], 'stopped', 'result', ref='/root/dispatcher/executor'),
            'result': {'delivery_id': 'first-slice', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                'tests': ['real first-slice bytes verified'],
                'write_release': fixtures.transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': progress.handoff.resolve_allocation(slot['handoff'])['semantic']['plan_refs'],
                'plan_authority': f.state()['control']['context']['handoff_progress']['plan_authority']}}})
        f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accept-first-slice'}})
        f.invoke('observe', f.observation('running', 'result', ref='/root/dispatcher'))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))

    def execution_handoff(self):
        f = self.f
        execution = f.execution_input()
        f.request['host']['actor_ref'] = '/root/dispatcher'
        execution['entry']['host']['actor_ref'] = '/root/dispatcher'
        execution['expected_entry'] = progress.entry.resolve(execution['entry'])
        return execution

    def execution_inventory(self, executor=True):
        agents = [{'agent_name': '/root', 'agent_status': 'running'},
                  {'agent_name': '/root/dispatcher', 'agent_status': 'running'}]
        if executor:
            agents.append({'agent_name': '/root/dispatcher/executor', 'agent_status': {'completed': 'accepted first slice'}})
        return self.inventory(agents)

    def test_running_dispatcher_releases_accepted_execution_before_next_slice(self):
        f = self.f
        self.accepted_execution()
        before = copy.deepcopy(f.state()['allocations']['first-slice']['record'])
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(finish['operation'], 'finish-native')
        f.invoke('native-finalize', self.finalize_observation(finish))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        close = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(close['operation'], 'close-native')
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertEqual(f.state()['host']['status'], 'running')
        self.assertEqual(f.state()['allocations']['first-slice']['record'], before)
        f.flow_git('add', 'impl.py')
        f.flow_git('commit', '-qm', 'integrate accepted first slice')
        result = f.invoke('allocation', {'allocation_id': 'next-slice', 'operation': 'prepare', 'handoff': self.execution_handoff()})
        self.assertEqual((result['next_action'] or {}).get('operation'), 'invoke-host', result)

    def final_recovery_with_native_retirement(self, *, pending_execution=False, retire_dispatcher=False):
        f = self.f
        agents = {}
        invoke = f.invoke

        def inventory():
            return self.inventory([{'agent_name': '/root', 'agent_status': 'running'}] + [
                {'agent_name': ref, 'agent_status': status} for ref, status in sorted(agents.items())])

        def host_invoke(operation, data=None, revision=None):
            data = copy.deepcopy(data or {})
            if operation == 'start':
                data['native_host'] = inventory()
            if operation == 'observe':
                data.pop('lifecycle', None)
            result = invoke(operation, data, revision)
            receipt = data.get('receipt') or {}
            if operation in {'observe', 'allocation', 'review-activity'} and receipt.get('ref'):
                status = 'running' if receipt.get('status') in {'ready', 'running'} else {'completed': 'host turn completed'}
                agents[receipt['ref']] = status
                invoke('lifecycle-state', self.snapshot(inventory()))
                if operation != 'allocation':
                    result = invoke('inspect')
            elif operation == 'native-finalize' and data.get('operation') == 'observe':
                record = next(record for record in f.state()['native_finalizations'] if record['ref'] == data['ref'])
                action = next(action for action in record['actions'] if action['action_id'] == data['action_id'])
                if action['operation'] == 'close-native' and receipt['status'] == 'completed':
                    agents.pop(data['ref'])
                invoke('lifecycle-state', self.snapshot(inventory()))
            return result

        def retire(request):
            f.invoke('lifecycle-state', self.snapshot(inventory()))
            finish = f.invoke('native-finalize', request)['next_action']
            self.assertEqual(finish['operation'], 'finish-native')
            f.invoke('native-finalize', self.finalize_observation(finish))
            close = f.invoke('native-finalize', request)['next_action']
            self.assertEqual(close['operation'], 'close-native')
            f.invoke('native-finalize', self.finalize_observation(close))

        with patch.object(f, 'invoke', side_effect=host_invoke):
            f.input['authorization']['flow_mode'] = 'continuous'
            self.test_real_tool_shape_allows_planning_publication_without_rpc_pages()
            request = f.input_for(3, f.state()['accepted'])
            request['authorization']['flow_mode'] = 'continuous'
            f.context = f.context_for(3)
            f.begin('continuous', request)
            f.invoke('observe', f.observation(ref='/root/dispatcher'))
            with patch.dict(f.settings, thread_id='dispatcher-runtime'):
                execution = f.inherited_execution_input()
                f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'prepare', 'handoff': execution})
                slot = f.state()['allocations']['first-slice']
                f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'bind',
                    'receipt': f.receipt(slot['record'], ref='/root/dispatcher/executor')})
                data = b"def run():\n    return 'accepted implementation'\n"
                (f.flow / 'impl.py').write_bytes(data)
                slot = f.state()['allocations']['first-slice']
                f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'receive',
                    'receipt': f.receipt(slot['record'], 'stopped', 'result', ref='/root/dispatcher/executor'),
                    'result': {'delivery_id': 'first-slice', 'status': 'completed', 'payload': {
                        'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                        'tests': ['implementation bytes verified'],
                        'write_release': fixtures.transfer.dispatch.execution_release_token(slot['record']),
                        'plan_refs': progress.handoff.resolve_allocation(slot['handoff'])['semantic']['plan_refs'],
                        'plan_authority': f.state()['control']['context']['handoff_progress']['plan_authority']}}})
                f.invoke('allocation', {'allocation_id': 'first-slice', 'operation': 'accept',
                    'decision': {'reference': 'dispatcher:accept-first-slice'}})
            execution_close = self.finalization('/root/dispatcher/executor', 'executor-thread')
            execution_close['actor_ref'] = '/root/dispatcher'
            if not pending_execution:
                retire(execution_close)
            f.flow_git('add', 'impl.py')
            f.flow_git('commit', '-qm', 'accepted implementation')
            candidate = f.flow_git('rev-parse', 'HEAD')
            accepted = f.accept_current({'candidate_commit': candidate, 'artifacts': ['impl.py'], 'checks': ['verified'],
                'review': {axis: {'candidate': candidate, 'reviewer_ref': '/root/' + axis, 'status': 'accepted'}
                           for axis in ('standards', 'spec')},
                'verification': {'candidate': candidate}}, '/root/dispatcher', fixture_execution=False)
            self.assertEqual(accepted['status'], 'accepted', accepted)
            if retire_dispatcher:
                retire(self.finalization('/root/dispatcher', 'dispatcher-thread'))
            predecessor = f.state()['accepted']
            store = progress.state_store.StateStore(f.checkpoint)
            accepted_current = store.open_current()
            slot = f.state()['allocations']['first-slice']
            ticket_id = progress.handoff.resolve_allocation(slot['handoff'])['semantic']['plan_refs'][0]['ticket_id']
            accepted_ticket_ref = copy.deepcopy(accepted_current.root['subjects']['ticket:' + ticket_id + ':0'])
            accepted_record = copy.deepcopy(store.read_evidence(accepted_ticket_ref,
                run_id=accepted_current.run_id, kind='subject')['value']['allocations']['first-slice']['record'])
            (f.root / 'other-task.md').write_text('parallel update\n')
            f.git('add', 'other-task.md')
            f.git('commit', '-qm', 'parallel target update')
            request = f.input_for(4, predecessor)
            f.context = f.context_for(4)
            f.begin('continuous', request)
            original_predecessor_ref = copy.deepcopy(f.state()['handoff']['predecessor'])
            original_delivery = progress.read_implementation_delivery(f.checkpoint, original_predecessor_ref)[0]
            self.assertEqual(original_predecessor_ref['accepted_digest'], predecessor['digest'])
            self.assertEqual(original_delivery['accepted']['digest'], predecessor['digest'])
            f.invoke('observe', f.observation(ref='/root/closure'))
            observation = f.observation('stopped', 'result', ref='/root/closure')
            observation['closure'] = {'ref': '/root/closure', 'candidate': candidate, 'merge': None,
                'ancestor_verified': False, 'changed_paths': [], 'binding': f.binding, 'checks': [],
                'worktree_removed': False, 'branch_removed': False, 'disposition': 'deleted',
                'disposition_verified': False, 'implementation_problem': 'target advanced at final publication'}
            f.invoke('observe', observation)
            recovery = {'reference': 'controller:final-recovery', 'closure_digest': f.state()['handoff']['digest']}
            result = f.invoke('implementation-recovery', recovery)
            if retire_dispatcher:
                self.assertEqual(result['error']['code'], 'identity_retired', result)
                self.assertEqual(f.state()['stage'], 4)
                self.assertEqual(f.state()['handoff']['predecessor'], original_predecessor_ref)
                self.assertEqual(progress.read_implementation_delivery(f.checkpoint,
                    original_predecessor_ref)[0], original_delivery)
                self.assertNotIn('/root/dispatcher', agents)
                return
            self.assertEqual(f.state()['stage'], 3, result)
            f.invoke('observe', f.observation('idle', 'result', ref='/root/dispatcher'))
            f.flow_git('merge', '--no-edit', 'main')
            f.invoke('observe', f.observation('stopped', 'result', ref='/root/dispatcher'))
            f.invoke('control', {'action': 'capture-integration', 'evidence': {'reference': 'controller:merged'},
                'receipt': {'controller_ref': 'task', 'reference': 'controller:merged'}})
            if pending_execution:
                f.invoke('lifecycle-state', self.snapshot(inventory()))
                before = f.checkpoint.read_bytes()
                for mutation, code in (('actor', 'authority_missing'), ('parent', 'identity_mismatch'),
                                       ('unregistered', 'identity_mismatch'), ('unaccepted', 'result_incomplete')):
                    wrong = copy.deepcopy(execution_close)
                    if mutation == 'actor':
                        wrong['actor_ref'] = 'task'
                    elif mutation == 'parent':
                        wrong['binding']['raw']['thread']['id'] = 'task'
                    else:
                        wrong['ref'] = '/root/never-accepted' if mutation == 'unregistered' else '/root/closure'
                    with self.subTest(mutation=mutation):
                        with self.assertRaises(progress.entry.PreparationError) as error:
                            f.invoke('native-finalize', wrong)
                        self.assertEqual(error.exception.code, code)
                        self.assertEqual(f.checkpoint.read_bytes(), before)
                wrong_prior = progress.handoff.unseal(original_predecessor_ref)
                wrong_prior['accepted_digest'] = '0' * 64
                with self.assertRaises(progress.entry.PreparationError):
                    progress.read_implementation_delivery(f.checkpoint, progress.handoff.seal(wrong_prior))
                owner_ref = original_delivery['native_owner_refs']['/root/dispatcher/executor']
                original_owner = store.read_evidence(owner_ref, run_id=accepted_current.run_id)['value']
                self.assertEqual((original_owner['owner_ref'], original_owner['parent_thread'],
                                  original_owner['delivery_digest']),
                                 ('/root/dispatcher', 'dispatcher-runtime', accepted_record['acceptance']['digest']))
                owner_object = store.directory / 'objects' / owner_ref['sha256'][:2] / owner_ref['sha256'][2:4] / owner_ref['sha256']
                owner_bytes = owner_object.read_bytes()
                try:
                    owner_object.write_bytes(b'corrupted original owner proof')
                    with self.assertRaises(progress.entry.PreparationError) as error:
                        f.invoke('native-finalize', execution_close)
                    self.assertEqual(error.exception.code, 'state_object_corrupt')
                    self.assertEqual(f.checkpoint.read_bytes(), before)
                finally:
                    owner_object.write_bytes(owner_bytes)
                finish = f.invoke('native-finalize', execution_close)['next_action']
                retirement = next(item for item in f.state()['native_finalizations'] if item['ref'] == execution_close['ref'])
                self.assertEqual(retirement['delivery_digest'], accepted_record['acceptance']['digest'])
                self.assertEqual(retirement['thread_id'], 'executor-thread')
                with patch.dict(f.settings, thread_id='dispatcher-runtime'):
                    request = f.inherited_execution_input()
                    control = copy.deepcopy(f.state()['control'])
                    blocked = f.invoke('allocation', {'allocation_id': 'waiting-integration',
                        'operation': 'prepare', 'handoff': request})
                    self.assertEqual(blocked['error']['code'], 'host_action_pending', blocked)
                    self.assertEqual(f.state()['control'], control)
                    self.assertNotIn('waiting-integration', f.state()['allocations'])
                f.invoke('native-finalize', self.finalize_observation(finish))
                close = f.invoke('native-finalize', execution_close)['next_action']
                f.invoke('native-finalize', self.finalize_observation(close))
                self.assertEqual(f.invoke('resume')['status'], 'active')
            f.revalidate_integrated_execution()
            # The retired allocation is immutable audit evidence, not a live
            # allocation or execution permission in the recovered Controller.
            retained_ticket = store.read_evidence(accepted_ticket_ref,
                run_id=accepted_current.run_id, kind='subject')['value']
            self.assertEqual(retained_ticket['allocations']['first-slice']['record'], accepted_record)
            self.assertNotIn('first-slice', f.state()['allocations'])
            self.assertIn('/root/dispatcher', agents)
            self.assertNotIn('/root/dispatcher/executor', agents)
            self.assertEqual(f.state()['control']['context']['carrier']['ref'], '/root/dispatcher')
            self.assertEqual(f.state()['allocations']['integrated-execution']['record']['status'], 'accepted')

    def test_final_recovery_allocates_after_verified_execution_retirement(self):
        self.final_recovery_with_native_retirement()

    def test_final_recovery_allocation_waits_for_pending_execution_retirement(self):
        self.final_recovery_with_native_retirement(pending_execution=True)

    def test_final_recovery_rejects_retired_original_dispatcher(self):
        self.final_recovery_with_native_retirement(retire_dispatcher=True)

    def finish_execution(self):
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = self.f.invoke('native-finalize', request)['next_action']
        self.f.invoke('native-finalize', self.finalize_observation(finish))
        self.f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        close = self.f.invoke('native-finalize', request)['next_action']
        return request, finish, close

    def test_unknown_finish_and_close_receipts_query_original_actions_and_ack_replays(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        unknown = self.finalize_observation(finish, 'unknown')
        revision = f.state()['revision']
        f.invoke('native-finalize', unknown)
        self.assertTrue(f.invoke('native-finalize', unknown, revision=revision)['acknowledged'])
        lookup = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(lookup['action']['action_id'], finish['action_id'])
        self.assertEqual(len(f.state()['native_finalizations'][0]['actions']), 1)
        f.invoke('native-finalize', self.finalize_observation(finish))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        close = f.invoke('native-finalize', request)['next_action']
        f.invoke('native-finalize', self.finalize_observation(close, 'unknown'))
        lookup = f.invoke('native-finalize', {'operation': 'lookup', 'ref': request['ref']})['next_action']
        self.assertEqual(lookup['action']['action_id'], close['action_id'])
        # Missing in an inventory cannot settle a lost close receipt.
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))
        f.invoke('native-finalize', self.finalize_observation(close))
        again = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(again['operation'], 'reconcile-stop-barrier')
        self.assertEqual(len(f.state()['native_finalizations'][0]['actions']), 2)
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertTrue(f.state()['native_finalizations'][0]['verified'])

    def assert_failed_native_action_waits_for_owner(self, operation):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        action = f.invoke('native-finalize', request)['next_action']
        if operation == 'close-native':
            f.invoke('native-finalize', self.finalize_observation(action))
            f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
            action = f.invoke('native-finalize', request)['next_action']
        failure = self.finalize_observation(action, 'failed')
        result = f.invoke('native-finalize', failure)
        actions = copy.deepcopy(f.state()['native_finalizations'][0]['actions'])
        before = f.checkpoint.read_bytes()
        requests = [('native-finalize', request), ('native-finalize', {'operation': 'lookup', 'ref': request['ref']}),
                    ('native-finalize', failure), ('inspect', {}), ('advance', {})]
        for observed in [result, *(f.invoke(name, data) for name, data in requests)]:
            waiting = observed['next_action']
            self.assertEqual(waiting['operation'], 'await-host-recovery', observed)
            self.assertEqual(waiting['ref'], request['ref'])
            self.assertEqual(waiting['action'], actions[-1])
            self.assertEqual(waiting['receipt'], failure['receipt'])
            self.assertIn('business owner', waiting['reason'])
        self.assertEqual(f.checkpoint.read_bytes(), before)
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', self.finalize_observation(action))
        self.assertEqual(error.exception.code, 'finalization_conflict')
        self.assertEqual(f.checkpoint.read_bytes(), before)
        blocked = f.invoke('allocation', {'allocation_id': 'after-terminal-failure', 'operation': 'prepare',
            'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')
        self.assertEqual(f.state()['native_finalizations'][0]['actions'], actions)

    def test_failed_native_finish_waits_for_owner_without_lookup_or_retry(self):
        self.assert_failed_native_action_waits_for_owner('finish-native')

    def test_failed_native_close_waits_for_owner_without_lookup_or_retry(self):
        self.assert_failed_native_action_waits_for_owner('close-native')

    def test_inspect_rejects_malformed_native_journal_without_rewriting_checkpoint(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        original = json.loads(f.checkpoint.read_text())
        changes = [
            lambda record, value=value: record.update(prepare=value)
            for value in (None, 'invalid', [])
        ]
        changes.append(lambda record: record['prepare'].pop('close'))
        changes.extend(lambda record, value=value: record['actions'][0].update(payload=value)
                       for value in (None, 'invalid', []))
        changes.extend((lambda record: record.update(receipts=[None]),
                        lambda record: record.update(receipts=[{}]),
                        lambda record: record['receipts'][0].update(receipt=None),
                        lambda record: record['actions'][-1]['payload'].pop('adapter'),
                        lambda record: record.update(verified=True),
                        lambda record: record['verified'].pop('inventory')))
        changes.extend(lambda record, value=value: record.update(revoked=value)
                       for value in (None, {}, {'checkpoint_revision': True, 'evidence': {'event': 'activity'}},
                                     {'checkpoint_revision': -1, 'evidence': {'event': 'activity'}},
                                     {'checkpoint_revision': original[progress.KEY]['revision'] + 1,
                                      'evidence': {'event': 'activity'}},
                                     {'checkpoint_revision': 0, 'evidence': []}))
        for index, change in enumerate(changes):
            damaged = copy.deepcopy(original)
            change(damaged[progress.KEY]['native_finalizations'][0])
            f.checkpoint.write_text(json.dumps(damaged))
            before = f.checkpoint.read_bytes()
            with self.subTest(case=index):
                with self.assertRaises(progress.entry.PreparationError) as error:
                    f.invoke('inspect')
                self.assertEqual(error.exception.code, 'invalid_checkpoint')
                self.assertEqual(f.checkpoint.read_bytes(), before)

    def test_stale_prepare_only_recovers_finish_and_current_revision_may_issue_close(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        original_revision = f.state()['revision']
        finish = f.invoke('native-finalize', request)['next_action']
        f.invoke('native-finalize', self.finalize_observation(finish))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        before = f.checkpoint.read_bytes()
        replay = f.invoke('native-finalize', request, revision=original_revision)
        self.assertTrue(replay['acknowledged'])
        self.assertEqual(replay['next_action']['operation'], 'lookup-exact-native-action')
        self.assertEqual(replay['next_action']['action']['action_id'], finish['action_id'])
        self.assertEqual(f.checkpoint.read_bytes(), before)
        close = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(close['operation'], 'close-native')
        self.assertEqual(len(f.state()['native_finalizations'][0]['actions']), 2)

    def test_missing_registered_descendant_blocks_finish_until_its_close_is_verified(self):
        f = self.f
        self.accepted_execution()
        f.flow_git('add', 'impl.py')
        f.flow_git('commit', '-qm', 'integrate first accepted slice')
        child_ref = '/root/dispatcher/executor/child'
        f.invoke('allocation', {'allocation_id': 'descendant-slice', 'operation': 'prepare', 'handoff': self.execution_handoff()})
        slot = f.state()['allocations']['descendant-slice']
        f.invoke('allocation', {'allocation_id': 'descendant-slice', 'operation': 'bind',
            'receipt': f.receipt(slot['record'], ref=child_ref)})
        data = b"def run():\n    return 'accepted descendant slice'\n"
        (f.flow / 'impl.py').write_bytes(data)
        slot = f.state()['allocations']['descendant-slice']
        f.invoke('allocation', {'allocation_id': 'descendant-slice', 'operation': 'receive',
            'receipt': f.receipt(slot['record'], 'stopped', 'result', ref=child_ref),
            'result': {'delivery_id': 'descendant-slice', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                'tests': ['real descendant bytes verified'],
                'write_release': fixtures.transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                'plan_authority': f.state()['control']['context']['handoff_progress']['plan_authority']}}})
        f.invoke('allocation', {'allocation_id': 'descendant-slice', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accept-descendant'}})
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        parent = self.finalization('/root/dispatcher/executor', 'executor-thread')
        parent['actor_ref'] = '/root/dispatcher'
        before = f.checkpoint.read_bytes()
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', parent)
        self.assertEqual(error.exception.code, 'host_evidence_missing')
        self.assertEqual(f.checkpoint.read_bytes(), before)
        self.assertFalse(f.state()['native_finalizations'])
        inventory = self.execution_inventory()['response']['agents'] + [
            {'agent_name': child_ref, 'agent_status': {'completed': 'accepted descendant slice'}}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(inventory)))
        child = self.finalization(child_ref, 'child-thread')
        child['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', child)['next_action']
        f.invoke('native-finalize', self.finalize_observation(finish))
        f.invoke('lifecycle-state', self.snapshot(self.inventory(inventory)))
        close = f.invoke('native-finalize', child)['next_action']
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        finish_parent = f.invoke('native-finalize', parent)['next_action']
        self.assertEqual(finish_parent['operation'], 'finish-native')
        self.assertEqual(finish_parent['payload']['ref'], parent['ref'])

    def test_finish_requires_real_binding_owner_capability_and_stopped_known_subtree(self):
        f = self.f
        self.accepted_execution()
        valid = self.finalization('/root/dispatcher/executor', 'executor-thread')
        valid['actor_ref'] = '/root/dispatcher'
        before = f.checkpoint.read_bytes()
        mutations = [lambda data: data.update(ref='/root/unknown'),
                     lambda data: data.update(actor_ref='other-parent'),
                     lambda data: data.update(reuse=True),
                     lambda data: data['close'].update(adapter='unavailable'),
                     lambda data: data['close']['request']['arguments'].update(threadId='wrong-thread'),
                     lambda data: data['close']['request']['arguments'].update(hostId='wrong-host'),
                     lambda data: data['binding'].update(raw={'business_payload': {'type': 'subAgentActivity',
                         'agentPath': valid['ref'], 'agentThreadId': 'executor-thread'}})]
        for mutate in mutations:
            data = copy.deepcopy(valid)
            mutate(data)
            with self.subTest(data=data), self.assertRaises(progress.entry.PreparationError):
                f.invoke('native-finalize', data)
            self.assertEqual(f.checkpoint.read_bytes(), before)
        for ref, status in ((valid['ref'] + '/late-child', 'running'), ('/root/unknown', {'completed': 'unknown'})):
            agents = self.execution_inventory()['response']['agents'] + [{'agent_name': ref, 'agent_status': status}]
            f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
            with self.subTest(ref=ref), self.assertRaises(progress.entry.PreparationError):
                f.invoke('native-finalize', valid)
        self.assertFalse(f.state().get('native_finalizations'))

    def test_wrong_or_stale_finish_receipt_and_cross_action_call_identity_reject(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        valid = self.finalize_observation(finish)
        for mutation in ('ref', 'action', 'thread', 'latest-turn', 'content-type', 'thread-type', 'turns-type', 'items-type', 'text-type'):
            data = copy.deepcopy(valid)
            if mutation == 'ref': data['receipt']['ref'] = '/root/wrong'
            elif mutation == 'action': data['action_id'] = 'other-action'
            elif mutation == 'content-type':
                data['receipt']['raw']['content'] = None
            else:
                raw = json.loads(data['receipt']['raw']['content'][0]['text'])
                if mutation == 'thread': raw['thread']['id'] = 'other-thread'
                elif mutation == 'latest-turn': raw['turns'].insert(0, {'status': 'completed', 'items': []})
                elif mutation == 'thread-type': raw['thread'] = None
                elif mutation == 'turns-type': raw['turns'] = 'invalid'
                elif mutation == 'items-type': raw['turns'][0]['items'] = None
                else: raw['turns'][0]['items'][0]['text'] = 42
                data['receipt']['raw']['content'][0]['text'] = json.dumps(raw)
            with self.subTest(mutation=mutation), self.assertRaises(progress.entry.PreparationError):
                f.invoke('native-finalize', data)
        f.invoke('native-finalize', valid)
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        close = f.invoke('native-finalize', request)['next_action']
        conflict = self.finalize_observation(close)
        conflict['receipt']['call_ref'] = valid['receipt']['call_ref']
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', conflict)
        self.assertEqual(error.exception.code, 'host_provenance_conflict')

    def test_native_receipt_reports_invalid_status_without_generic_type_error(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        before = f.checkpoint.read_bytes()
        for status in ({}, [], None, False, 42, 'unsupported'):
            observation = self.finalize_observation(finish)
            observation['receipt']['status'] = status
            with self.subTest(status=status):
                with self.assertRaises(progress.entry.PreparationError) as error:
                    f.invoke('native-finalize', observation)
                self.assertEqual(error.exception.code, 'invalid_result')
                self.assertEqual(f.checkpoint.read_bytes(), before)

    def test_binding_from_another_parent_thread_cannot_retire_registered_identity(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        request['binding']['raw']['thread']['id'] = 'unrelated-parent-thread'
        before = f.checkpoint.read_bytes()
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', request)
        self.assertEqual(error.exception.code, 'identity_mismatch')
        self.assertEqual(f.checkpoint.read_bytes(), before)

    def test_archive_success_old_query_and_resident_descendant_do_not_release_slot(self):
        f = self.f
        request, finish, close = self.finish_execution()
        before_close = self.snapshot(self.execution_inventory(False))
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', before_close)
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))
        active_child = self.execution_inventory(False)['response']['agents'] + [
            {'agent_name': request['ref'] + '/child', 'agent_status': 'running'}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(active_child)))
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))
        blocked = f.invoke('allocation', {'allocation_id': 'blocked-slice', 'operation': 'prepare', 'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')

    def test_reappearing_closed_identity_revokes_release_and_pause_cancel_precede_new_effects(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertEqual(f.invoke('native-finalize', request)['next_action']['operation'], 'lookup-exact-native-action')
        f.invoke('pause')
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', request)
        self.assertEqual(error.exception.code, 'progression_suspended')

    def test_pause_during_finish_reconciles_stopped_turn_without_issuing_close(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        f.invoke('pause')
        root_stop = f.observation('stopped', 'result', ref='/root/dispatcher')
        root_stop.pop('lifecycle')
        f.invoke('observe', root_stop)
        f.invoke('native-finalize', self.finalize_observation(finish))
        agents = self.execution_inventory()['response']['agents']
        agents[1]['agent_status'] = {'completed': 'dispatcher paused'}
        paused = f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        self.assertEqual(paused['status'], 'paused', paused)
        self.assertEqual(len(f.state()['native_finalizations'][0]['actions']), 1)
        self.assertEqual(paused['next_action']['operation'], 'lookup-exact-native-action')
        self.assertEqual(paused['next_action']['action']['action_id'], finish['action_id'])
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', request)
        self.assertEqual(error.exception.code, 'progression_suspended')
        f.invoke('cancel')
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', request)
        self.assertEqual(error.exception.code, 'progression_suspended')

    def test_lost_finish_prepare_response_is_visible_through_inspect_and_advance(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        before = f.checkpoint.read_bytes()
        for operation in ('inspect', 'advance'):
            observed = f.invoke(operation)['next_action']
            self.assertEqual(observed['operation'], 'lookup-exact-native-action')
            self.assertEqual(observed['action']['action_id'], finish['action_id'])
        self.assertEqual(f.checkpoint.read_bytes(), before)

    def test_business_failure_keeps_issued_native_finish_lookup_accessible(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        failed = f.observation('idle', 'result', {'delivery_id': 'dispatcher-error',
            'status': 'technical_error', 'payload': {'message': 'original dispatcher failed'}},
            ref='/root/dispatcher')
        failed.pop('lifecycle', None)
        f.invoke('observe', failed)
        block = copy.deepcopy(f.state()['business_block'])
        before = f.checkpoint.read_bytes()
        for operation, data in (('inspect', {}), ('advance', {}),
                                ('native-finalize', {'operation': 'lookup', 'ref': request['ref']})):
            action = f.invoke(operation, data)['next_action']
            self.assertEqual(action['operation'], 'lookup-exact-native-action')
            self.assertEqual(action['action']['action_id'], finish['action_id'])
        self.assertEqual(f.checkpoint.read_bytes(), before)
        f.invoke('native-finalize', self.finalize_observation(finish))
        action = f.invoke('lifecycle-query')['next_action']
        self.assertEqual(action['operation'], 'invoke-lifecycle-query')
        self.assertEqual(f.state()['business_block'], block)
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('native-finalize', request)
        self.assertEqual(error.exception.code, 'business_recovery_required')
        self.assertEqual(len(f.state()['native_finalizations'][0]['actions']), 1)

    def test_replacement_dispatch_waits_for_native_release_and_preserves_issued_lookup(self):
        f = self.f
        self.test_dispatch_recovery_retains_interactive_host_identity()
        request = self.finalization()
        finish = f.invoke('native-finalize', request)['next_action']
        f.invoke('native-finalize', self.finalize_observation(finish))
        agents = self.inventory()['response']['agents'] + [
            {'agent_name': '/root/dispatcher', 'agent_status': {'completed': 'original dispatcher stopped'}}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        recovery = f.state()['control']['context']['handoff_progress']['recovery']
        decision = {'reference': 'controller:assume-snapshot', 'authority_digest': recovery['authority_digest'],
            'attempt': recovery['attempt'], 'remaining_paths': recovery['remaining_paths'],
            'assume_paths': recovery['ownership']['dispatcher']}
        intent = {'action': 'dispatch-recovery-intent', 'evidence': {
            'recovery_id': recovery['recovery_id'], 'snapshot_digest': recovery['snapshot_digest'], 'decision': decision},
            'receipt': {'controller_ref': 'task', 'reference': decision['reference']}}
        before = copy.deepcopy(recovery)
        rejected = f.invoke('control', intent)
        self.assertEqual(rejected['status'], 'blocked', rejected)
        self.assertEqual(rejected['error']['code'], 'host_action_pending')
        self.assertEqual(f.state()['control']['context']['handoff_progress']['recovery'], before)
        close = f.invoke('native-finalize', request)['next_action']
        f.invoke('native-finalize', self.finalize_observation(close))
        released = self.inventory([agents[0], agents[-1]])
        f.invoke('lifecycle-state', self.snapshot(released))
        issued = f.invoke('control', intent)['next_action']
        self.assertEqual(issued['operation'], 'invoke-recovery-host')
        # Revoking release after creation cannot erase or reissue the saved call.
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        repeated = f.invoke('control', intent)['next_action']
        self.assertEqual(repeated['operation'], 'lookup-recovery-host')
        self.assertEqual(repeated['request'], issued['request'])
        receipt = {'adapter': 'fixture-native', 'call_ref': 'original-replacement-call',
            'response_ref': 'unknown-original-replacement', 'intent_id': issued['request']['intent_id'],
            'status': 'unknown', 'ref': None, 'raw': {'status': 'unknown', 'write_authority': False}}
        f.invoke('control', {'action': 'dispatch-recovery-result',
            'evidence': {'recovery_id': recovery['recovery_id'], 'receipt': receipt}, 'receipt': receipt})
        self.assertEqual(f.state()['control']['context']['handoff_progress']['recovery']['receipts'][-1], receipt)

    def test_late_running_inventory_revokes_closed_proof_before_stale_query_rejection(self):
        f = self.f
        request, finish, close = self.finish_execution()
        agents = self.execution_inventory()['response']['agents']
        agents[-1]['agent_status'] = 'running'
        late = self.snapshot(self.inventory(agents))
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertTrue(f.state()['native_finalizations'][0]['verified'])
        rejected = f.invoke('lifecycle-state', late)
        self.assertEqual(rejected['error']['code'], 'host_evidence_missing')
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        lookup = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(lookup['operation'], 'lookup-exact-native-action')
        self.assertEqual(lookup['action']['action_id'], close['action_id'])
        blocked = f.invoke('allocation', {'allocation_id': 'after-late-activity', 'operation': 'prepare', 'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')

    def test_desktop_activity_revokes_closed_proof_and_invalidates_inflight_inventory(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        stale = self.snapshot(self.execution_inventory(False))
        instance = f.state()['native_host']['instance']
        event = {'type': 'subAgentActivity', 'id': 'actual-native-interaction', 'kind': 'interacted',
            'agentPath': request['ref'], 'agentThreadId': 'executor-thread'}
        observed = f.invoke('host-event', {'instance': instance, 'event': event})
        self.assertNotEqual(observed.get('error', {}).get('code'), 'identity_mismatch', observed)
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        rejected = f.invoke('lifecycle-state', stale)
        self.assertEqual(rejected['error']['code'], 'host_evidence_missing')
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])

    def test_late_unknown_descendant_activity_blocks_new_allocation_before_inventory(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        f.invoke('host-event', {'instance': f.state()['native_host']['instance'], 'event': {
            'type': 'subAgentActivity', 'id': 'late-descendant-started', 'kind': 'started',
            'agentPath': request['ref'] + '/late_child', 'agentThreadId': 'new-descendant-thread'}})
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        blocked = f.invoke('allocation', {'allocation_id': 'after-late-descendant',
            'operation': 'prepare', 'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')

    def test_revoked_close_recovers_only_after_new_unloaded_readback_and_fresh_inventory(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        query = self.unloaded_query(finish, close)
        stale = self.snapshot(self.execution_inventory(False))
        original = copy.deepcopy(f.state()['native_finalizations'][0]['actions'])
        f.invoke('native-finalize', query)
        self.assertEqual(f.state()['native_finalizations'][0]['actions'], original)
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))
        self.assertFalse(f.state()['native_finalizations'][0].get('revoked'))
        f.invoke('lifecycle-state', stale)
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertTrue(f.state()['native_finalizations'][0]['verified'])
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        f.invoke('native-finalize', query)
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])

    def test_close_readback_failures_and_new_activity_preserve_original_close_and_revoke(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        original = copy.deepcopy(f.state()['native_finalizations'][0]['actions'])
        lookup = f.invoke('native-finalize', {'operation': 'lookup', 'ref': request['ref']})['next_action']
        self.assertEqual(lookup['tool'], 'mcp__codex_app__read_thread')
        self.assertEqual(lookup['arguments']['threadId'], 'executor-thread')
        for status in ('unknown', 'failed'):
            query = self.unloaded_query(finish, close, status)
            query['receipt']['status'] = status
            if status == 'failed':
                before = f.checkpoint.read_bytes()
                with self.assertRaises(progress.entry.PreparationError) as error:
                    f.invoke('native-finalize', query)
                self.assertEqual(error.exception.code, 'host_provenance_conflict')
                self.assertEqual(f.checkpoint.read_bytes(), before)
                query['receipt']['call_ref'] = 'call:unknown'
            f.invoke('native-finalize', query)
            self.assertEqual(f.state()['native_finalizations'][0]['actions'], original)
            self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        valid = self.unloaded_query(finish, close, 'complete')
        self.assertNotEqual(f.state()['native_finalizations'][0]['queries'][-1]['action_id'],
                            lookup['query_action_id'])
        for change in ('wrong-thread', 'old-turn', 'active', 'old-archive'):
            wrong = copy.deepcopy(valid)
            raw = json.loads(wrong['receipt']['raw']['content'][0]['text'])
            if change == 'wrong-thread': raw['thread']['id'] = 'other-thread'
            elif change == 'old-turn': raw['turns'].insert(0, {'status': 'completed', 'items': []})
            elif change == 'active': raw['thread']['status'] = {'type': 'active'}
            else: raw = {'threadId': 'executor-thread', 'archived': True}
            wrong['receipt']['raw']['content'][0]['text'] = json.dumps(raw)
            before = f.checkpoint.read_bytes()
            with self.subTest(change=change), self.assertRaises(progress.entry.PreparationError):
                f.invoke('native-finalize', wrong)
            self.assertEqual(f.checkpoint.read_bytes(), before)
        f.invoke('native-finalize', valid)
        stale = self.snapshot(self.execution_inventory(False))
        event = {'type': 'subAgentActivity', 'id': 'activity-during-restoration', 'kind': 'started',
            'agentPath': request['ref'], 'agentThreadId': 'executor-thread'}
        f.invoke('host-event', {'instance': f.state()['native_host']['instance'], 'event': event})
        f.invoke('lifecycle-state', stale)
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))

    def test_expected_finish_activity_only_invalidates_snapshot_not_retirement(self):
        f = self.f
        self.accepted_execution()
        request = self.finalization('/root/dispatcher/executor', 'executor-thread')
        request['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', request)['next_action']
        f.invoke('host-event', {'instance': f.state()['native_host']['instance'], 'event': {
            'type': 'subAgentActivity', 'id': 'expected-finish-turn', 'kind': 'interacted',
            'agentPath': request['ref'], 'agentThreadId': 'executor-thread'}})
        self.assertFalse(f.state()['native_finalizations'][0].get('revoked'))
        f.invoke('native-finalize', self.finalize_observation(finish))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        self.assertEqual(f.invoke('native-finalize', request)['next_action']['operation'], 'close-native')

    def test_readback_captured_before_new_activity_cannot_clear_later_revocation(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        readback = self.unloaded_query(finish, close, 'before-new-activity')
        f.invoke('host-event', {'instance': f.state()['native_host']['instance'], 'event': {
            'type': 'subAgentActivity', 'id': 'newer-than-readback', 'kind': 'interacted',
            'agentPath': request['ref'], 'agentThreadId': 'executor-thread'}})
        revoked = copy.deepcopy(f.state()['native_finalizations'][0]['revoked'])
        f.invoke('native-finalize', readback)
        self.assertEqual(f.state()['native_finalizations'][0].get('revoked'), revoked)
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertEqual(f.state()['native_finalizations'][0].get('revoked'), revoked)
        blocked = f.invoke('allocation', {'allocation_id': 'after-stale-readback', 'operation': 'prepare',
            'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')

    def test_late_activity_before_first_close_verification_still_requires_readback(self):
        f = self.f
        request, finish, close = self.finish_execution()
        agents = self.execution_inventory()['response']['agents']
        agents[-1]['agent_status'] = 'running'
        late = self.snapshot(self.inventory(agents))
        f.invoke('native-finalize', self.finalize_observation(close))
        rejected = f.invoke('lifecycle-state', late)
        self.assertEqual(rejected['error']['code'], 'host_evidence_missing')
        self.assertTrue(f.state()['native_finalizations'][0].get('revoked'))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))

    def test_inventory_replays_cannot_restore_an_old_readback_guard(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        old_inventory = self.snapshot(self.execution_inventory())
        f.invoke('lifecycle-state', old_inventory)
        readback = self.unloaded_query(finish, close, 'before-inventory-replay')
        f.invoke('host-event', {'instance': f.state()['native_host']['instance'], 'event': {
            'type': 'subAgentActivity', 'id': 'after-old-inventory', 'kind': 'interacted',
            'agentPath': request['ref'], 'agentThreadId': 'executor-thread'}})
        f.invoke('lifecycle-state', old_inventory)
        f.invoke('native-finalize', readback)
        self.assertTrue(f.state()['native_finalizations'][0].get('revoked'))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        blocked = f.invoke('allocation', {'allocation_id': 'after-inventory-replay', 'operation': 'prepare',
            'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')

    def test_event_replays_cannot_restore_an_old_readback_guard(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        old_event = {'instance': f.state()['native_host']['instance'], 'event': {
            'type': 'subAgentActivity', 'id': 'first-activity', 'kind': 'interacted',
            'agentPath': request['ref'], 'agentThreadId': 'executor-thread'}}
        f.invoke('host-event', old_event)
        readback = self.unloaded_query(finish, close, 'before-event-replay')
        newer_event = copy.deepcopy(old_event)
        newer_event['event']['id'] = 'newer-activity'
        f.invoke('host-event', newer_event)
        f.invoke('host-event', old_event)
        f.invoke('native-finalize', readback)
        self.assertTrue(f.state()['native_finalizations'][0].get('revoked'))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        blocked = f.invoke('allocation', {'allocation_id': 'after-event-replay', 'operation': 'prepare',
            'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')

    def assert_completed_reappearance_requires_readback(self, suffix):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory()))
        f.invoke('native-finalize', self.unloaded_query(finish, close, 'before-completed-reappearance'))
        self.assertFalse(f.state()['native_finalizations'][0].get('verified'))
        agents = self.execution_inventory(False)['response']['agents'] + [
            {'agent_name': request['ref'] + suffix, 'agent_status': {'completed': 'reappeared after readback'}}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        self.assertTrue(f.state()['native_finalizations'][0].get('revoked'))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        blocked = f.invoke('allocation', {'allocation_id': 'after-completed-reappearance', 'operation': 'prepare',
            'handoff': self.execution_handoff()})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')
        actions = copy.deepcopy(f.state()['native_finalizations'][0]['actions'])
        f.invoke('native-finalize', self.unloaded_query(finish, close, 'after-completed-reappearance'))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        self.assertTrue(f.state()['native_finalizations'][0].get('verified'))
        self.assertFalse(f.state()['native_finalizations'][0].get('revoked'))
        self.assertEqual(f.state()['native_finalizations'][0]['actions'], actions)

    def test_native_retirement_completed_target_reappearance_requires_readback(self):
        self.assert_completed_reappearance_requires_readback('')

    def test_native_retirement_completed_descendant_reappearance_requires_readback(self):
        self.assert_completed_reappearance_requires_readback('/new_descendant')

    def test_native_retirement_preserves_saved_review_query(self):
        f = self.f
        request, finish, close = self.finish_execution()
        f.invoke('native-finalize', self.finalize_observation(close))
        f.invoke('lifecycle-state', self.snapshot(self.execution_inventory(False)))
        f.flow_git('add', 'impl.py')
        f.flow_git('commit', '-qm', 'integrate accepted candidate for review')
        candidate = f.flow_git('rev-parse', 'HEAD')
        idle = f.observation('idle', 'result', ref='/root/dispatcher')
        idle.pop('lifecycle', None)
        f.invoke('observe', idle)
        f.settings['thread_id'] = 'task'
        f.request['host'].update(thread_id='task', role='controller', source_ref=None)
        f.request['host'].pop('actor_ref', None)
        current = f.state()['control']['context']
        plan = current['handoff_progress']['validation_plan']
        checks = fixtures.transfer.checks_for(plan['review_required'], candidate)
        result = f.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': '/root/dispatcher', 'attempt': current['carrier']['attempt'], 'commit': candidate,
            'expected_target_head': f.git('rev-parse', 'main'), 'binding': f.binding,
            'plan_digest': fixtures.transfer.control.digest(plan), 'checks': checks},
            'receipt': {'adapter': 'fixture', 'call_ref': 'focused', 'response_ref': 'focused-result',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': '/root/dispatcher'}}})
        self.assertEqual(f.state()['control']['context']['handoff_progress']['state'], 'reviewable', result)
        prepare = {'operation': 'prepare', 'axis': 'standards', 'candidate': candidate, 'actor_ref': 'task',
            'verification': {'candidate': candidate, 'plan_digest': fixtures.transfer.control.digest(plan),
                'checks': checks, 'plan_authority': f.state()['control']['context']['handoff_progress']['plan_authority']}}
        issued = f.invoke('review-activity', prepare)['next_action']
        saved = copy.deepcopy(f.state()['review_activity']['standards'])
        f.invoke('host-event', {'instance': f.state()['native_host']['instance'], 'event': {
            'type': 'subAgentActivity', 'id': 'reappeared-during-review', 'kind': 'interacted',
            'agentPath': request['ref'], 'agentThreadId': 'executor-thread'}})
        recovered = f.invoke('review-activity', prepare)
        self.assertEqual(recovered['next_action']['operation'], 'lookup-exact-review', recovered)
        self.assertEqual(recovered['next_action']['action_id'], issued['action_id'])
        self.assertEqual(f.state()['review_activity']['standards'], saved)
        blocked = f.invoke('review-activity', {**prepare, 'axis': 'spec'})
        self.assertEqual(blocked['error']['code'], 'host_action_pending')
        self.assertNotIn('spec', f.state()['review_activity'])

    def test_native_retirement_rejects_invalid_delivery_digest(self):
        f = self.f
        self.finish_execution()
        original = json.loads(f.checkpoint.read_text())
        store = progress.state_store.StateStore(f.checkpoint)
        current = store.open_current()
        subject_id = current.root['metadata']['stage_subject']
        subject = store.read_subject(current, subject_id).value
        scope = store.read_evidence(current.root['scope'], run_id=current.run_id, kind='scope')['value']
        for value in (None, '', [], {}, True, 'invalid', 'A' * 64):
            damaged = copy.deepcopy(subject)
            record = damaged['native_finalizations'][0]
            if value is None:
                record.pop('delivery_digest')
            else:
                record['delivery_digest'] = value
            subject_ref = store.write_object(current.run_id, 'subject', damaged, subject_id=subject_id)
            damaged_scope = copy.deepcopy(scope)
            damaged_scope['subject_ref'] = subject_ref
            damaged_scope['value']['state_digest'] = progress.entry.digest(damaged)
            root = {**current.root, 'subjects': {**current.root['subjects'], subject_id: subject_ref},
                    'scope': store.write_object(current.run_id, 'scope', damaged_scope)}
            root_ref = store.write_object(current.run_id, 'root', root)
            f.checkpoint.write_bytes(progress.state_store.encode({**original, 'current': root_ref}))
            before = f.checkpoint.read_bytes()
            with self.subTest(value=value):
                with self.assertRaises(progress.entry.PreparationError) as error:
                    f.invoke('inspect')
                self.assertEqual(error.exception.code, 'invalid_checkpoint')
                self.assertEqual(f.checkpoint.read_bytes(), before)

    def test_reappeared_target_does_not_prevent_stopped_sibling_finalization(self):
        f = self.f
        self.accepted_execution()
        f.flow_git('add', 'impl.py')
        f.flow_git('commit', '-qm', 'integrate accepted first slice')
        f.invoke('allocation', {'allocation_id': 'sibling', 'operation': 'prepare', 'handoff': self.execution_handoff()})
        sibling = '/root/dispatcher/sibling'
        slot = f.state()['allocations']['sibling']
        f.invoke('allocation', {'allocation_id': 'sibling', 'operation': 'bind',
            'receipt': f.receipt(slot['record'], ref=sibling)})
        data = b"def run():\n    return 'accepted sibling slice'\n"
        (f.flow / 'impl.py').write_bytes(data)
        slot = f.state()['allocations']['sibling']
        f.invoke('allocation', {'allocation_id': 'sibling', 'operation': 'receive',
            'receipt': f.receipt(slot['record'], 'stopped', 'result', ref=sibling),
            'result': {'delivery_id': 'sibling', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                'tests': ['real sibling bytes verified'],
                'write_release': fixtures.transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                'plan_authority': f.state()['control']['context']['handoff_progress']['plan_authority']}}})
        f.invoke('allocation', {'allocation_id': 'sibling', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accept-sibling'}})
        agents = self.execution_inventory()['response']['agents'] + [
            {'agent_name': sibling, 'agent_status': {'completed': 'accepted sibling'}}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        first = self.finalization('/root/dispatcher/executor', 'executor-thread')
        first['actor_ref'] = '/root/dispatcher'
        finish = f.invoke('native-finalize', first)['next_action']
        f.invoke('native-finalize', self.finalize_observation(finish))
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        close = f.invoke('native-finalize', first)['next_action']
        f.invoke('native-finalize', self.finalize_observation(close))
        absent = [agent for agent in agents if agent['agent_name'] != first['ref']]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(absent)))
        agents[2]['agent_status'] = 'running'
        for _ in range(2):
            f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])
        second = self.finalization(sibling, 'sibling-thread')
        second['actor_ref'] = '/root/dispatcher'
        result = f.invoke('native-finalize', second)
        self.assertEqual(result['next_action']['operation'], 'finish-native')
        self.assertEqual(result['next_action']['payload']['ref'], sibling)
        self.assertTrue(f.state()['native_finalizations'][0]['revoked'])

    def test_accepted_carrier_delivery_finishes_queued_ack_closes_and_allows_successor(self):
        f = self.f
        f.input['authorization']['flow_mode'] = 'continuous'
        self.test_real_tool_shape_allows_planning_publication_without_rpc_pages()
        before = copy.deepcopy({key: f.state()[key] for key in ('dispatch', 'accepted', 'control')})
        request = self.finalization()
        finish = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(finish['operation'], 'finish-native')
        self.assertIn('action_id=' + finish['action_id'], finish['payload']['message'])
        self.assertTrue(finish['payload']['read_only'])
        # QueueOnly confirmations remain mailbox input; the same identity gets
        # exactly one TriggerTurn action even when its business turn completed.
        again = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(again['operation'], 'lookup-exact-native-action')
        self.assertEqual(again['action']['action_id'], finish['action_id'])
        f.invoke('native-finalize', self.finalize_observation(finish))
        f.invoke('lifecycle-state', self.snapshot())
        close = f.invoke('native-finalize', request)['next_action']
        self.assertEqual(close['operation'], 'close-native')
        f.invoke('native-finalize', self.finalize_observation(close))
        self.assertFalse(f.state()['stopped'])
        missing = self.inventory([{'agent_name': '/root', 'agent_status': 'running'}])
        f.invoke('lifecycle-state', self.snapshot(missing))
        self.assertTrue(f.state()['stopped'])
        self.assertEqual({key: f.state()[key] for key in before}, before)
        successor = f.input_for(3, f.state()['accepted'])
        successor['authorization']['flow_mode'] = 'continuous'
        result = f.invoke('start', {'handoff': successor, 'native_host': missing})
        self.assertEqual((result['next_action'] or {}).get('operation'), 'invoke-host', result)

    def test_real_tool_shape_allows_planning_publication_without_rpc_pages(self):
        self.start()
        candidate = self.candidate()
        self.f.invoke('lifecycle-state', self.snapshot())
        result = self.f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertTrue(result['next_action']['publication']['result']['ok'], result)
        self.assertEqual((self.f.root / 'docs/spec.md').read_text(), self.f.planning_spec())
        pending = self.f.invoke('receive-publication')['pending']
        accepted = self.f.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
                                          'answer': 'accept', 'reference': 'controller:accept'})
        self.assertTrue(accepted['downstream_ready'])
        self.assertEqual(accepted['status'], 'accepted')

    def test_successor_keeps_host_and_accounts_for_prior_stage_agents(self):
        f = self.f
        f.input['authorization']['flow_mode'] = 'continuous'
        self.test_real_tool_shape_allows_planning_publication_without_rpc_pages()
        successor = f.input_for(3, f.state()['accepted'])
        successor['authorization']['flow_mode'] = 'continuous'
        result = f.invoke('start', {'handoff': successor, 'native_host': self.inventory()})
        self.assertEqual(result['next_action']['operation'], 'invoke-host')
        f.invoke('observe', f.observation(ref='/root/dispatcher'))
        f.invoke('pause')
        observation = f.observation('stopped', 'result', ref='/root/dispatcher')
        observation.pop('lifecycle')
        f.invoke('observe', observation)
        agents = self.inventory()['response']['agents'] + [
            {'agent_name': '/root/dispatcher', 'agent_status': {'completed': 'stopped'}}]
        result = f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        self.assertEqual(result['status'], 'paused', result)
        missing_prior = [agent for agent in agents if agent['agent_name'] != '/root/designer']
        result = f.invoke('lifecycle-state', self.snapshot(self.inventory(missing_prior)))
        self.assertEqual(result['status'], 'pausing', result)
        result = f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        self.assertEqual(result['status'], 'paused', result)
        agents[1]['agent_status'] = 'running'
        result = f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        self.assertEqual(result['status'], 'pausing', result)

    def test_dispatch_recovery_retains_interactive_host_identity(self):
        f = self.f
        f.input['authorization']['flow_mode'] = 'continuous'
        self.test_real_tool_shape_allows_planning_publication_without_rpc_pages()
        successor = f.input_for(3, f.state()['accepted'])
        successor['authorization']['flow_mode'] = 'continuous'
        f.invoke('start', {'handoff': successor, 'native_host': self.inventory()})
        f.invoke('observe', f.observation(ref='/root/dispatcher'))
        stopped = f.observation('stopped', 'result', ref='/root/dispatcher')
        stopped.pop('lifecycle')
        stopped['receipt']['raw'].update(resumable=False, dispatch_available=True)
        f.invoke('observe', stopped)
        agents = self.inventory()['response']['agents'] + [
            {'agent_name': '/root/dispatcher', 'agent_status': {'completed': 'stopped'}}]
        f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
        carrier = f.state()['control']['context']['carrier']
        result = f.invoke('control', {'action': 'prepare-dispatch-recovery', 'evidence': {
            'dispatcher_ref': carrier['ref'], 'attempt': carrier['attempt'],
            'reference': 'controller:recover', 'reason': 'original identity cannot resume',
            'stop_receipts': [stopped['receipt']['receipt_ref']],
            'call_receipts': [stopped['provenance']['response_ref']]},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:recover'}})
        self.assertEqual(result['next_action']['operation'], 'control-effects', result)
        self.assertEqual(result['next_action']['result']['recovery']['host_evidence']['original_host'], 'task')

    def test_missing_host_support_fails_before_dispatch(self):
        f = self.f
        with self.assertRaises(progress.entry.PreparationError) as error:
            progress.handle(f.checkpoint, {'protocol': progress.PROTOCOL, 'operation': 'start',
                'expected_revision': 0, 'data': {'handoff': f.input,
                'control': {'context': f.context, 'discussion': None}}})
        self.assertEqual(error.exception.code, 'host_capability_missing')
        self.assertNotIn(progress.KEY, progress.read_record(f.checkpoint))

    def test_inventory_query_before_native_binding_is_rejected(self):
        f = self.f
        f.invoke('start', {'handoff': f.input, 'control': {'context': f.context, 'discussion': None},
            'native_host': self.inventory([{'agent_name': '/root', 'agent_status': 'running'}])})
        result = f.invoke('lifecycle-query')
        self.assertEqual(result['error']['code'], 'host_capability_missing')
        self.assertIsNone(f.state().get('lifecycle_snapshot'))

    def test_preflight_uses_actual_inventory_and_creates_no_checkpoint(self):
        f = self.f
        result = f.invoke('host-preflight', self.inventory())
        self.assertEqual(result['status'], 'host-ready')
        self.assertFalse(f.checkpoint.exists())
        for mutation in ('filtered', 'missing-controller', 'unsupported'):
            value = self.inventory()
            if mutation == 'filtered':
                value['request']['arguments'] = {'path_prefix': '/root/designer'}
            elif mutation == 'missing-controller':
                value['response']['agents'].pop(0)
            else:
                value['adapter'] = 'unknown'
            with self.subTest(mutation=mutation), self.assertRaises(progress.entry.PreparationError):
                f.invoke('host-preflight', value)
            self.assertFalse(f.checkpoint.exists())

    def test_missing_running_unknown_and_duplicate_agents_do_not_publish(self):
        self.start()
        candidate = self.candidate()
        target = self.f.git('rev-parse', 'HEAD')
        base = self.inventory()['response']['agents']
        cases = [base[:1], [base[0], {**base[1], 'agent_status': 'running'}],
                 [*base, {'agent_name': '/root/designer/hidden', 'agent_status': {'completed': 'done'}}],
                 [base[0], {**base[1], 'agent_status': 'idle'}], [*base, base[1]]]
        for agents in cases:
            with self.subTest(agents=agents):
                self.f.invoke('lifecycle-state', self.snapshot(self.inventory(agents)))
                result = self.f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual(self.f.git('rev-parse', 'HEAD'), target)

    def test_failed_lookup_revokes_old_snapshot_and_fresh_lookup_recovers(self):
        self.start()
        candidate = self.candidate()
        old = self.snapshot()
        self.f.invoke('lifecycle-state', old)
        self.assertTrue(self.f.state()['stopped'])
        fresh = self.snapshot()
        self.assertFalse(self.f.state()['stopped'])
        self.f.invoke('lifecycle-state', old)
        result = self.f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertEqual(result['status'], 'blocked')
        self.f.invoke('lifecycle-state', fresh)
        self.assertFalse(self.f.state()['stopped'])
        self.f.invoke('lifecycle-state', self.snapshot())
        result = self.f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertTrue(result['next_action']['publication']['result']['ok'], result)

    def test_unchanged_current_inventory_replay_acknowledges_without_mutation(self):
        self.start()
        self.candidate()
        observed = self.snapshot()
        self.f.invoke('lifecycle-state', observed)
        before = self.f.checkpoint.read_bytes()
        result = self.f.invoke('lifecycle-state', observed)
        self.assertTrue(result['acknowledged'])
        self.assertEqual(self.f.checkpoint.read_bytes(), before)

    def test_inventory_replay_finishes_interrupted_host_recovery(self):
        self.start()
        candidate = self.candidate()
        blocked = self.f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertEqual(blocked['error']['code'], 'host_evidence_missing')
        observed = self.snapshot()
        with patch.object(progress.Progress, 'finish_lifecycle', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.f.invoke('lifecycle-state', observed)
        result = self.f.invoke('lifecycle-state', observed)
        self.assertEqual(result['status'], 'active', result)
        self.assertTrue(result['acknowledged'])

    def test_preflight_rejects_existing_active_helpers_before_creating_checkpoint(self):
        root = {'agent_name': '/root', 'agent_status': 'running'}
        helper = {'agent_name': '/root/research', 'agent_status': 'running'}
        with self.assertRaises(progress.entry.PreparationError) as error:
            self.f.invoke('host-preflight', self.inventory([root, helper]))
        self.assertEqual(error.exception.code, 'host_evidence_missing')
        self.assertFalse(self.f.checkpoint.exists())

    def test_old_response_cannot_be_relabelled_as_a_new_query(self):
        self.start()
        self.candidate()
        old = self.snapshot()
        self.f.invoke('lifecycle-state', old)
        fresh = self.snapshot()
        fresh.update(call_ref=old['call_ref'], response_ref=old['response_ref'])
        result = self.f.invoke('lifecycle-state', fresh)
        self.assertEqual(result['error']['code'], 'host_provenance_conflict')
        self.assertFalse(self.f.state()['stopped'])

    def test_rejected_response_cannot_be_relabelled_as_a_new_query(self):
        self.start()
        self.candidate()
        old = self.snapshot()
        self.snapshot()
        result = self.f.invoke('lifecycle-state', old)
        self.assertEqual(result['error']['code'], 'host_evidence_missing')
        fresh = self.snapshot()
        fresh.update(call_ref=old['call_ref'], response_ref=old['response_ref'])
        result = self.f.invoke('lifecycle-state', fresh)
        self.assertEqual(result['error']['code'], 'host_provenance_conflict')
        self.assertFalse(self.f.state()['stopped'])

    def test_new_activity_invalidates_snapshot_and_outstanding_query(self):
        self.start()
        self.candidate()
        old = self.snapshot()
        self.f.invoke('lifecycle-state', old)
        self.assertTrue(self.f.state()['stopped'])
        observation = self.f.observation('running', 'result', ref='/root/designer')
        self.f.invoke('observe', observation)
        self.assertFalse(self.f.state()['stopped'])
        result = self.f.invoke('lifecycle-state', old)
        self.assertEqual(result['error']['code'], 'host_evidence_missing')
        self.assertFalse(self.f.state()['stopped'])

    def test_rpc_evidence_cannot_replace_collaboration_evidence(self):
        self.start()
        snapshot = self.f.observation('stopped', 'result', ref='/root/designer')['lifecycle']
        result = self.f.invoke('lifecycle-state', snapshot)
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(self.f.state()['stopped'])

    def test_late_running_response_revokes_a_newer_completed_snapshot(self):
        self.start()
        candidate = self.candidate()
        old = self.snapshot()
        fresh = self.snapshot()
        self.f.invoke('lifecycle-state', fresh)
        self.assertTrue(self.f.state()['stopped'])
        old['inventory']['response']['agents'][1]['agent_status'] = 'running'
        self.f.invoke('lifecycle-state', old)
        self.assertFalse(self.f.state()['stopped'])
        self.f.invoke('lifecycle-state', fresh)
        self.assertFalse(self.f.state()['stopped'])
        result = self.f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertEqual(result['status'], 'blocked')

    def test_completed_existing_helpers_are_pinned_and_checked(self):
        f = self.f
        root = {'agent_name': '/root', 'agent_status': 'running'}
        helper = {'agent_name': '/root/research', 'agent_status': {'completed': 'read-only research'}}
        admitted = self.inventory([root, helper])
        self.assertEqual(f.invoke('host-preflight', admitted)['status'], 'host-ready')
        f.invoke('start', {'handoff': f.input, 'control': {'context': f.context, 'discussion': None},
                          'native_host': admitted})
        f.invoke('observe', f.observation(ref='/root/designer'))
        candidate = self.candidate()
        f.invoke('lifecycle-state', self.snapshot())
        result = f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertEqual(result['status'], 'blocked')
        full = self.inventory(self.inventory()['response']['agents'] + [helper])
        f.invoke('lifecycle-state', self.snapshot(full))
        result = f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertTrue(result['next_action']['publication']['result']['ok'], result)

    def test_query_provenance_cannot_be_overwritten(self):
        self.start()
        self.candidate()
        original = self.snapshot()
        self.f.invoke('lifecycle-state', original)
        changed = {**original, 'call_ref': 'replacement-call', 'response_ref': 'replacement-response'}
        result = self.f.invoke('lifecycle-state', changed)
        self.assertEqual(result['error']['code'], 'host_provenance_conflict')
        self.assertIn(original, self.f.state()['collaboration_lookups'].values())
        fresh = self.snapshot()
        fresh.update(call_ref=original['call_ref'], response_ref=original['response_ref'])
        result = self.f.invoke('lifecycle-state', fresh)
        self.assertEqual(result['error']['code'], 'host_provenance_conflict')
        self.assertFalse(self.f.state()['stopped'])

    def test_conflicting_response_cannot_be_relabelled_after_rejection(self):
        self.start()
        self.candidate()
        original = self.snapshot()
        self.f.invoke('lifecycle-state', original)
        conflict = {**original, 'call_ref': 'conflicting-call', 'response_ref': 'conflicting-response'}
        rejected = self.f.invoke('lifecycle-state', conflict)
        self.assertEqual(rejected['error']['code'], 'host_provenance_conflict')
        fresh = self.snapshot()
        fresh.update(call_ref=conflict['call_ref'], response_ref=conflict['response_ref'])
        result = self.f.invoke('lifecycle-state', fresh)
        self.assertEqual(result['error']['code'], 'host_provenance_conflict')
        self.assertFalse(self.f.state()['stopped'])

    def test_rpc_preflight_must_match_the_actual_transport(self):
        f = self.f
        progress.atomic_save(f.checkpoint, {'transport': {'instance': 'actual-host', 'state': 'live'}})
        snapshot = {'instance': 'other-host', 'carrier_thread': 'task', 'sequence': 0, 'events': [],
            'pages': [{'request': {'method': 'thread/list', 'params': {'ancestorThreadId': 'task',
                'archived': archived, 'sourceKinds': list(progress.NATIVE_SOURCE_KINDS), 'modelProviders': []}},
                'response': {'data': [], 'nextCursor': None}} for archived in (False, True)]}
        with self.assertRaises(progress.entry.PreparationError) as error:
            f.invoke('start', {'handoff': f.input, 'control': {'context': f.context, 'discussion': None},
                              'native_host': {'adapter': 'app-server', 'snapshot': snapshot}})
        self.assertEqual(error.exception.code, 'identity_mismatch')
        self.assertNotIn(progress.KEY, progress.read_record(f.checkpoint))


if __name__ == '__main__':
    unittest.main()
