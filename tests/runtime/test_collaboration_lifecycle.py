"""Interactive tool receipts through C/B and real temporary Git publication."""
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
        (f.flow / 'docs/spec.md').write_text('approved design\n')
        f.flow_git('add', 'docs/spec.md')
        f.flow_git('commit', '-qm', 'plan')
        candidate = f.flow_git('rev-parse', 'HEAD')
        observed = f.observation('stopped', 'result', ref='/root/designer')
        observed.pop('lifecycle')
        observed['publication_candidate'] = {'candidate_commit': candidate,
            'artifacts': ['docs/spec.md'], 'checks': ['design readiness']}
        pending = f.invoke('observe', observed)['pending']
        f.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
                           'answer': 'accept', 'reference': 'controller:ready'})
        return candidate

    def snapshot(self, inventory=None):
        f = self.f
        action = f.invoke('lifecycle-query')['next_action']
        return {'query_id': action['query_id'], 'call_ref': 'call:' + action['query_id'],
                'response_ref': 'response:' + action['query_id'], 'inventory': inventory or self.inventory()}

    def test_real_tool_shape_allows_planning_publication_without_rpc_pages(self):
        self.start()
        candidate = self.candidate()
        self.f.invoke('lifecycle-state', self.snapshot())
        result = self.f.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:ready'})
        self.assertTrue(result['next_action']['publication']['result']['ok'], result)
        self.assertEqual((self.f.root / 'docs/spec.md').read_text(), 'approved design\n')
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
