"""Accepted history survives explicit current-identity query retries in v6.

Every mutation is settled before acceptance. A later adverse host fact revokes
current release proof, without rerunning B or changing its historical result.
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixtures
progress = fixtures.progress


class AcceptedQueryRetryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProgressTests(methodName='runTest')
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def stage2(self, mode='continuous'):
        self.f.finish_design(mode)
        decision = copy.deepcopy(self.f.state()['last_decision'])
        self.assertTrue(self.f.state()['stopped'])
        self.f.invoke('observe',self.f.observation('running','result'))
        self.assertFalse(self.f.state()['stopped'])
        return 'native:designer',decision

    def stage3(self):
        self.f.finish_design('continuous')
        self.f.finish_next(3,'continuous')
        decision = copy.deepcopy(self.f.state()['last_decision'])
        self.f.invoke('observe',self.f.observation('running','result',ref='native:dispatcher'))
        self.assertFalse(self.f.state()['stopped'])
        return 'native:dispatcher',decision

    def retry_after_negative(self, stage, status, mode='continuous'):
        role,decision = self.stage2(mode) if stage == 2 else self.stage3()
        f = self.f
        accepted = copy.deepcopy(f.state()['accepted'])
        original_dispatch = copy.deepcopy(f.state()['dispatch'])
        boundary = copy.deepcopy(f.state()['pending'])
        query = f.invoke('resume')['next_action']
        self.assertEqual(query['operation'],'inspect-host-state')
        self.assertEqual(query['payload']['ref'],role)
        self.assertNotIn('unresolved_action',query['payload'])
        failed = f.observation(status,'result',ref=role)
        f.invoke('observe',failed)
        ended = copy.deepcopy(f.state()['host']['query'])
        self.assertTrue(ended['resolved'])
        self.assertFalse(f.state()['stopped'])
        with patch.object(progress.dispatch,'handle',side_effect=AssertionError('B result replayed')):
            retried = f.invoke('resume')['next_action']
            self.assertEqual(retried['operation'],'inspect-host-state')
            self.assertNotEqual(retried['action_id'],query['action_id'])
            self.assertEqual(retried['payload']['subject'],query['payload']['subject'])
            self.assertEqual(f.state()['host']['query_history'][-1],ended)
            self.assertEqual(f.state()['accepted'],accepted)
            self.assertEqual(f.state()['dispatch'],original_dispatch)
            response = f.observation('stopped','result',ref=role)
            result = f.invoke('observe',response)
            self.assertEqual(result['status'],'accepted')
            self.assertTrue(f.state()['stopped'])
            self.assertTrue(f.invoke('observe',response)['acknowledged'])
            self.assertTrue(f.invoke('decide',decision)['acknowledged'])
            before = f.checkpoint.read_bytes()
            self.assertTrue(f.invoke('resume')['acknowledged'])
            self.assertEqual(f.checkpoint.read_bytes(),before)
            self.assertEqual(f.state()['pending'],boundary)
        if boundary is not None:
            f.invoke('decide',{'decision_id':boundary['decision_id'],'subject':boundary['subject'],
                              'answer':'confirm','reference':'controller:next-stage'})
        f.context = f.context_for(stage+1)
        self.assertEqual(f.begin(mode,f.input_for(stage+1,accepted))['next_action']['operation'],'invoke-host')

    def test_stage2_unknown_query_can_retry(self): self.retry_after_negative(2,'unknown')
    def test_stage3_missing_resolution_can_retry(self): self.retry_after_negative(3,'idle')
    def test_stage2_stepwise_keeps_stage_boundary(self): self.retry_after_negative(2,'unknown','stepwise')

    def test_repeated_negative_queries_and_stale_query_cannot_release_action(self):
        role,decision = self.stage2()
        f = self.f
        accepted = copy.deepcopy(f.state()['accepted'])
        query = f.invoke('resume')['next_action']
        ids,negatives = [query['action_id']],[]
        with patch.object(progress.dispatch,'handle',side_effect=AssertionError('reaccepted business')):
            for _ in range(3):
                data = f.observation('unknown','result',ref=role)
                negatives.append(copy.deepcopy(data))
                f.invoke('observe',data)
                ended = copy.deepcopy(f.state()['host']['query'])
                before = f.checkpoint.read_bytes()
                f.invoke('advance')
                self.assertEqual(f.checkpoint.read_bytes(),before)
                self.assertEqual(f.state()['host']['query'],ended)
                query = f.invoke('resume')['next_action']
                self.assertEqual(query['operation'],'inspect-host-state')
                self.assertNotIn(query['action_id'],ids)
                ids.append(query['action_id'])
                self.assertEqual(f.invoke('resume')['next_action'],query)
                self.assertEqual(f.state()['accepted'],accepted)
            late = copy.deepcopy(negatives[0])
            late['event_id'] += ':late'
            late['receipt']['receipt_ref'] += ':late'
            late['receipt']['status'] = 'idle'
            late['receipt']['raw']['status'] = 'idle'
            late['provenance']['response_ref'] += ':late'
            f.invoke('observe',late)
            self.assertEqual(f.state()['host']['query'],query)
            self.assertIsNone(f.state()['host']['proof'])
            self.assertFalse(f.state()['stopped'])
            self.assertEqual(f.invoke('observe',f.observation('stopped','result',ref=role))['status'],'accepted')
            self.assertTrue(f.invoke('decide',decision)['acknowledged'])

    def retry_save_window(self,after):
        role,_ = self.stage2()
        f = self.f
        first = f.invoke('resume')['next_action']
        f.invoke('observe',f.observation('unknown','result',ref=role))
        ended = copy.deepcopy(f.state()['host']['query'])
        accepted = copy.deepcopy(f.state()['accepted'])
        save = progress.Progress.save
        def crash(owner):
            if owner.state['host']['query'] is None and owner.state['host'].get('query_history'):
                if after: save(owner)
                raise KeyboardInterrupt()
            save(owner)
        with patch.object(progress.Progress,'save',new=crash):
            with self.assertRaises(KeyboardInterrupt): f.invoke('resume')
        self.assertEqual(f.state()['host']['query'],None if after else ended)
        new = f.invoke('resume')['next_action']
        self.assertEqual(new['operation'],'inspect-host-state')
        self.assertNotEqual(new['action_id'],first['action_id'])
        self.assertEqual(new['payload'],first['payload'])
        self.assertEqual(f.state()['host']['query_history'],[ended])
        self.assertEqual(f.state()['accepted'],accepted)

    def test_retry_before_archive_save_keeps_old_query(self): self.retry_save_window(False)
    def test_retry_after_archive_save_keeps_original_action(self): self.retry_save_window(True)

    def runner_stop(self,operation):
        self.stage2()
        f = self.f
        f.invoke('resume')
        f.invoke('observe',f.observation('unknown','result'))
        ended = copy.deepcopy(f.state()['host']['query'])
        fixtures.runner._atomic_save(f.checkpoint,fixtures.runner._new_state({}))
        fixtures.runner.request_control(f.checkpoint,operation)
        for action in ('resume','advance','resume'):
            result = f.invoke(action)
            self.assertNotEqual((result.get('next_action') or {}).get('operation'),'inspect-host-state')
            self.assertNotEqual((result.get('next_action') or {}).get('operation'),'continue-host')
            self.assertEqual(f.state()['host']['query'],ended)
            self.assertFalse(f.state()['host'].get('query_history'))

    def test_runner_pause_does_not_retry_accepted_query(self): self.runner_stop('pause')
    def test_runner_cancel_does_not_retry_accepted_query(self): self.runner_stop('cancel')
