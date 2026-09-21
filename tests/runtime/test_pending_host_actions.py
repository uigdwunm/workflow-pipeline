"""Original native calls must settle before a v6 stop barrier can close."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures
progress = fixtures.progress


class PendingHostActionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProgressTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.begin('continuous')

    def bind(self):
        if self.f.state()['dispatch']['status'] != 'bound':
            self.f.invoke('observe', self.f.observation())

    def continue_action(self):
        self.bind()
        result = self.f.invoke('observe', self.f.observation('idle','result',
            {'delivery_id':'first-continue','status':'continue','payload':{'slice':1}}))
        self.assertEqual(result['next_action']['operation'],'continue-host')
        return copy.deepcopy(result['next_action'])

    def unresolved(self):
        return progress.Progress(self.f.checkpoint,progress.read_record(self.f.checkpoint)).unresolved_host_actions()

    def pause_and_stop(self):
        self.bind()
        stop = self.f.invoke('pause')['next_action']
        self.assertEqual(stop['operation'],'stop-host')
        result = self.f.invoke('observe',self.f.observation('stopped','result'))
        self.assertEqual(result['status'],'pausing' if self.unresolved() else 'paused')
        return stop

    def settle_then_resume(self, outcome='completed'):
        query = self.f.state()['host']['query']
        response = self.f.observation('stopped','result')
        response['action_resolution']['outcome'] = outcome
        settled = self.f.invoke('observe',response)
        self.assertEqual(settled['status'],'paused',settled)
        current = self.f.invoke('resume')['next_action']
        self.assertEqual(current['operation'],'inspect-host-state')
        self.assertNotEqual(current['action_id'],query['action_id'])
        continued = self.f.invoke('observe',self.f.observation('idle','result'))['next_action']
        self.assertEqual(continued['operation'],'continue-host')
        return response,continued

    def test_stop_does_not_settle_preceding_continue(self):
        original = self.continue_action()
        self.pause_and_stop()
        query = self.f.state()['host']['query']
        self.assertEqual(query['payload']['unresolved_action'],original)
        idle = self.f.observation('idle','result')
        idle.pop('action_resolution')
        result = self.f.invoke('observe',idle)
        self.assertEqual(result['status'],'pausing')
        self.assertEqual(result['next_action']['operation'],'await-host-recovery')
        self.assertEqual(self.unresolved(),[original])

    def test_exact_settlement_allows_one_continuation(self):
        original = self.continue_action()
        stop = self.pause_and_stop()
        self.assertNotEqual(stop['action_id'],original['action_id'])
        query = copy.deepcopy(self.f.state()['host']['query'])
        self.assertEqual(self.f.invoke('advance')['next_action'],query)
        response,continued = self.settle_then_resume()
        self.assertNotEqual(continued['action_id'],original['action_id'])
        self.assertTrue(self.f.invoke('observe',response,revision=0)['acknowledged'])
        self.assertEqual(self.f.state()['action'],continued)
        self.assertEqual(self.f.invoke('advance')['next_action']['action'],continued)

    def test_wrong_nonterminal_and_stop_identity_do_not_settle_continue(self):
        original = self.continue_action()
        stop = self.pause_and_stop()
        for action_id,outcome in ((original['action_id'],'unknown'),(original['action_id'],'running'),
                                  ('old-unrelated-action','completed'),(stop['action_id'],'completed')):
            with self.subTest(action_id=action_id,outcome=outcome):
                self.f.invoke('resume')  # Explicitly retry lookup, without releasing pause.
                response = self.f.observation('idle','result')
                response['action_resolution'] = {'action_id':action_id,'outcome':outcome}
                result = self.f.invoke('observe',response)
                self.assertEqual(result['status'],'pausing')
                self.assertEqual(result['next_action']['operation'],'await-host-recovery')
                self.assertEqual(self.unresolved(),[original])
                self.assertIsNone(self.f.state()['host']['proof'])

    def test_stop_receipt_cannot_settle_the_earlier_action(self):
        original = self.continue_action()
        self.f.invoke('pause')
        response = self.f.observation('stopped','result')
        response['action_resolution'] = {'action_id':original['action_id'],'outcome':'cancelled'}
        result = self.f.invoke('observe',response)
        self.assertEqual(result['status'],'pausing')
        self.assertEqual(self.unresolved(),[original])
        self.assertEqual(result['next_action']['payload']['unresolved_action'],original)

    def test_multiple_unresolved_stops_are_settled_individually(self):
        original = self.continue_action()
        stop = self.f.invoke('pause')['next_action']
        query = self.f.invoke('observe',self.f.observation('unknown','result'))['next_action']
        self.assertEqual(query['operation'],'inspect-host-state')
        self.assertEqual(self.f.state()['host_action']['action_id'],stop['action_id'])
        self.assertEqual([item['action_id'] for item in self.unresolved()],[original['action_id'],stop['action_id']])
        response = self.f.observation('stopped','result')
        second = self.f.invoke('observe',response)['next_action']
        self.assertEqual(second['operation'],'inspect-host-state')
        self.assertEqual(second['payload']['unresolved_action']['action_id'],stop['action_id'])
        self.f.invoke('observe',response)
        self.assertEqual(self.f.state()['host']['query'],second)
        self.assertEqual(self.f.invoke('observe',self.f.observation('stopped','result'))['status'],'paused')
        self.assertEqual(self.unresolved(),[])

    def stop_save_window(self,after):
        original = self.continue_action()
        save = progress.Progress.save
        def crash(owner):
            if (owner.state.get('action') or {}).get('operation') == 'stop-host':
                if after: save(owner)
                raise KeyboardInterrupt()
            save(owner)
        with patch.object(progress.Progress,'save',new=crash):
            with self.assertRaises(KeyboardInterrupt): self.f.invoke('pause')
        self.assertEqual(self.unresolved()[0],original)
        action = self.f.invoke('advance')['next_action']
        if after:
            self.assertEqual(action['operation'],'lookup-exact-action')
            action = action['action']
        self.assertEqual(action['operation'],'stop-host')
        self.f.invoke('observe',self.f.observation('stopped','result'))
        self.assertEqual(self.f.state()['status'],'pausing')
        self.assertEqual(self.f.state()['host']['query']['payload']['unresolved_action'],original)

    def test_stop_before_save_retains_original(self): self.stop_save_window(False)
    def test_stop_after_save_retains_original(self): self.stop_save_window(True)

    def resolution_save_window(self,after):
        original = self.continue_action()
        self.pause_and_stop()
        response = self.f.observation('stopped','result')
        save = progress.Progress.save
        def crash(owner):
            if (owner.state['host'].get('query') or {}).get('settled_action_id') == original['action_id']:
                if after: save(owner)
                raise KeyboardInterrupt()
            save(owner)
        with patch.object(progress.Progress,'save',new=crash):
            with self.assertRaises(KeyboardInterrupt): self.f.invoke('observe',response)
        retained = next(item for item in self.f.state()['retained_host_actions'] if item['action_id'] == original['action_id'])
        self.assertEqual(retained.get('resolved',False),after)
        self.assertEqual(self.f.invoke('observe',response)['status'],'paused')
        self.assertTrue(self.f.invoke('observe',response)['acknowledged'])
        self.assertEqual(self.unresolved(),[])
        self.f.invoke('resume')
        continued = self.f.invoke('observe',self.f.observation('idle','result'))['next_action']
        self.assertEqual(continued['operation'],'continue-host')

    def test_resolution_before_save_keeps_original(self): self.resolution_save_window(False)
    def test_resolution_after_save_is_not_reissued(self): self.resolution_save_window(True)

    def test_cancel_preserves_responsibility_and_cannot_resume(self):
        original = self.continue_action()
        stop = self.f.invoke('pause')['next_action']
        result = self.f.invoke('cancel')
        self.assertEqual(result['next_action']['operation'],'inspect-host-state')
        self.assertEqual(self.f.state()['host_action']['action_id'],stop['action_id'])
        self.f.invoke('observe',self.f.observation('stopped','result'))
        self.assertEqual(self.f.state()['status'],'cancelling')
        self.assertEqual(self.f.invoke('observe',self.f.observation('stopped','result'))['status'],'cancelled')
        self.assertEqual(self.unresolved(),[])
        self.assertEqual(self.f.invoke('resume')['status'],'cancelled')
        self.assertEqual(self.f.invoke('pause')['status'],'cancelled')
        self.assertNotEqual(self.f.state()['action']['action_id'],original['action_id'])

    def test_bound_invoke_is_retained_when_stop_replaces_it(self):
        original = copy.deepcopy(self.f.state()['host_action'])
        self.bind()
        self.assertFalse(self.f.state()['stopped'])
        self.assertTrue(self.f.state()['host_action']['creation_resolved'])
        self.pause_and_stop()
        retained = next(item for item in self.f.state()['retained_host_actions'] if item['action_id'] == original['action_id'])
        self.assertEqual(retained['payload'],original['payload'])
        query = self.f.invoke('resume')['next_action']
        self.assertNotIn('unresolved_action',query['payload'])
        self.assertEqual(query['payload']['ref'],'native:designer')

    def test_uncertain_creation_is_looked_up_before_stop_and_retained(self):
        original = copy.deepcopy(self.f.state()['host_action'])
        self.f.invoke('observe',self.f.observation('unknown','lookup',ref=None))
        self.assertEqual(self.f.invoke('pause')['next_action']['operation'],'lookup-exact-action')
        stop = self.f.invoke('observe',self.f.observation('ready','lookup'))['next_action']
        self.assertEqual(stop['operation'],'stop-host')
        self.assertEqual(self.f.invoke('observe',self.f.observation('stopped','result'))['status'],'paused')
        self.assertEqual(self.unresolved(),[])
        self.assertTrue(any(item['action_id'] == original['action_id'] for item in self.f.state()['retained_host_actions']))

    def test_pause_during_query_preserves_old_action_and_rejects_late_query(self):
        original = self.continue_action()
        self.pause_and_stop()
        first = self.f.state()['host']['query']
        late = self.f.observation('stopped','result')
        negative = self.f.observation('unknown','result')
        negative.pop('action_resolution')
        self.f.invoke('observe',negative)
        self.assertEqual(self.f.invoke('pause')['status'],'pausing')
        second = self.f.invoke('resume')['next_action']
        self.assertNotEqual(first['action_id'],second['action_id'])
        self.f.invoke('observe',late)
        self.assertEqual(self.f.state()['host']['query'],second)
        self.assertEqual(self.unresolved(),[original])
        self.assertEqual(self.f.invoke('observe',self.f.observation('stopped','result'))['status'],'paused')

    def terminal_outcome(self,outcome):
        original = self.continue_action()
        self.pause_and_stop()
        response,continued = self.settle_then_resume(outcome)
        self.assertEqual(response['action_resolution'],{'action_id':original['action_id'],'outcome':outcome})
        self.assertNotEqual(continued['action_id'],original['action_id'])

    def test_original_action_proven_not_issued_can_resume(self): self.terminal_outcome('not-issued')
    def test_original_action_proven_cancelled_can_resume(self): self.terminal_outcome('cancelled')
