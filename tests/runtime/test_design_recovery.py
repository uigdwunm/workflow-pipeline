"""Stage-2 replacement crosses C/B/control and real Git; only host is doubled."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as scenario


class DesignRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = scenario.ProgressTests(methodName='runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def prepared(self, *, resumable=False):
        f = self.f
        f.begin('continuous'); f.invoke('observe', f.observation())
        if hasattr(f, 'activate_designer'):
            f.activate_designer()
        checkpoint = f.state()['control']['context']['handoff_progress']
        self.path = checkpoint['allowed_paths'][0]
        (f.flow / self.path).parent.mkdir(parents=True, exist_ok=True)
        (f.flow / self.path).write_text('retained design draft\n')
        stopped = f.observation('stopped', 'result')
        stopped['receipt']['raw'].update(resumable=resumable, dispatch_available=True)
        f.invoke('observe', stopped)
        evidence = {'dispatcher_ref':'native:designer', 'attempt':f.state()['control']['context']['carrier']['attempt'],
            'reference':'controller:recover', 'reason':'original designer cannot resume',
            'stop_receipts':[stopped['receipt']['receipt_ref']], 'call_receipts':[stopped['provenance']['response_ref']]}
        request = {'action':'prepare-dispatch-recovery','evidence':evidence,
            'receipt':{'controller_ref':'task','reference':evidence['reference']}}
        return request

    def intent(self):
        f = self.f
        result = f.invoke('control', self.prepared())
        self.assertEqual(result['next_action']['operation'], 'control-effects', result)
        recovery = result['next_action']['result']['recovery']
        decision = {'reference':'controller:assume', 'authority_digest':recovery['authority_digest'],
            'attempt':recovery['attempt'], 'remaining_paths':recovery['remaining_paths'],
            'assume_paths':recovery['ownership']['designer']}
        request = {'action':'dispatch-recovery-intent', 'evidence':{'recovery_id':recovery['recovery_id'],
            'snapshot_digest':recovery['snapshot_digest'], 'decision':decision},
            'receipt':{'controller_ref':'task','reference':decision['reference']}}
        result = f.invoke('control', request)
        self.assertEqual(result['next_action']['operation'], 'invoke-recovery-host', result)
        return recovery, request, result['next_action']['request']

    def ready(self):
        recovery, request, intent = self.intent(); f = self.f
        receipt = {'adapter':'fixture-native','call_ref':'replacement-call','response_ref':'replacement-response',
            'intent_id':intent['intent_id'],'status':'ready','ref':'native:replacement',
            'raw':{'write_authority':False,'status':'ready'}}
        f.invoke('control', {'action':'dispatch-recovery-result',
            'evidence':{'recovery_id':recovery['recovery_id'],'receipt':receipt},'receipt':receipt})
        snapshot = f.observation('stopped','result')['lifecycle']
        rows = snapshot['pages'][0]['response']['data']
        self.assertEqual(sum(row['id'] == 'native:replacement' for row in rows), 1)
        self.assertEqual(next(row['status'] for row in rows if row['id'] == 'native:replacement'), {'type': 'idle'})
        f.invoke('lifecycle-state', snapshot)
        return {'action':'recover-dispatch', 'evidence':{**request['evidence'],'replacement_ref':'native:replacement'},
            'receipt':request['receipt']}

    def test_replacement_retains_design_scope_and_no_execution_records(self):
        recovery, request, intent = self.intent(); f = self.f
        self.assertEqual(intent['role'], 'solution-designer')
        self.assertFalse(intent['write_authority'])
        self.assertEqual(set(recovery['ownership']), {'designer'})
        self.assertIn('design_input', intent); self.assertNotIn('plan_digest', intent)
        replayed = f.invoke('control', request)
        self.assertTrue(replayed['acknowledged'], replayed)
        self.assertEqual(replayed['next_action']['operation'], 'control-effects', replayed)
        self.assertEqual(replayed['next_action']['result']['effects'], [])
        self.assertIs(replayed['next_action']['write_authority'], False)
        self.assertNotIn('executions', f.state()['control']['context']['handoff_progress'])

    def test_activation_keeps_bytes_and_rejects_late_old_results(self):
        request = self.ready(); f = self.f
        result = f.invoke('control', request)
        checkpoint = f.state()['control']['context']['handoff_progress']
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:replacement', result)
        self.assertEqual(checkpoint['state'], 'designing')
        self.assertNotIn('executions', checkpoint)
        self.assertEqual((f.flow/self.path).read_text(), 'retained design draft\n')
        self.assertTrue(f.invoke('control', request)['acknowledged'])
        observed = f.invoke('observe', f.observation('idle', 'result',
            {'delivery_id':'replacement-status','status':'technical_error','payload':{'message':'remaining design work'}},
            ref='native:replacement'))
        self.assertIn('business_block', f.state(), observed)
        import workflow_control as control
        with self.assertRaises(control.ControlError):
            control.transition({'schema_version':f.state()['control']['context']['schema_version'], 'actor_ref':'task',
                'context':f.state()['control']['context'], 'action':'design-result',
                'evidence':{'ref':'native:designer','result_digest':'late-result'}})

    def test_changed_stopped_snapshot_blocks_preparation(self):
        request = self.prepared(); f = self.f
        (f.flow/self.path).write_text('unattributed later bytes\n')
        result = f.invoke('control', request)
        self.assertEqual(result['status'], 'blocked', result)
        self.assertNotIn('recovery', f.state()['control']['context']['handoff_progress'])

    def test_changed_activation_snapshot_blocks_new_writer(self):
        request = self.ready(); f = self.f
        (f.flow/self.path).write_text('drift\n')
        result = f.invoke('control', request)
        self.assertEqual(result['status'], 'blocked', result)
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:designer')

    def test_publication_prevents_replacement(self):
        f = self.f
        f.prepare_design_result('continuous')
        before = f.flow_git('rev-parse', 'HEAD')
        publication = f.state()['publication']
        stop = f.state()['host']['last_stop']
        request = {'action':'prepare-dispatch-recovery', 'evidence':{
            'dispatcher_ref':'native:designer', 'attempt':f.state()['control']['context']['carrier']['attempt'],
            'reference':'controller:recover', 'reason':'original designer unavailable after publishing',
            'stop_receipts':[stop['receipt']['receipt_ref']], 'call_receipts':[stop['provenance']['response_ref']]},
            'receipt':{'controller_ref':'task','reference':'controller:recover'}}
        result = f.invoke('control', request)
        self.assertEqual(result['status'], 'blocked', result)
        self.assertNotIn('recovery', f.state()['control']['context']['handoff_progress'])
        self.assertEqual(f.state()['publication'], publication)
        self.assertEqual(f.flow_git('rev-parse', 'HEAD'), before)

    def test_unknown_replacement_is_looked_up_without_duplicate_dispatch(self):
        recovery, request, intent = self.intent(); f = self.f
        receipt = {'adapter':'fixture-native','call_ref':'replacement-call','response_ref':'unknown-response',
            'intent_id':intent['intent_id'],'status':'unknown','ref':None,'raw':{'status':'unknown'}}
        f.invoke('control', {'action':'dispatch-recovery-result',
            'evidence':{'recovery_id':recovery['recovery_id'],'receipt':receipt},'receipt':receipt})
        for operation in ('advance', 'resume'):
            result = f.invoke(operation)
            self.assertEqual(result['next_action']['operation'], 'lookup-recovery-host', result)
        result = f.invoke('control', request)
        self.assertTrue(result['acknowledged'], result)
        self.assertEqual(result['next_action']['operation'], 'control-effects', result)
        self.assertEqual(result['next_action']['result']['effects'], [])
        self.assertIs(result['next_action']['write_authority'], False)
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:designer')

    def test_pause_prevents_replacement(self):
        request = self.prepared(); f = self.f
        f.invoke('pause')
        import entry_prepare
        with self.assertRaises(entry_prepare.PreparationError) as error:
            f.invoke('control', request)
        self.assertEqual(error.exception.code, 'progression_suspended')
        self.assertNotIn('recovery', f.state()['control']['context']['handoff_progress'])

    def test_resumable_original_does_not_prepare_replacement(self):
        request = self.prepared(resumable=True); f = self.f
        original = f.state()['host']['last_stop']
        self.assertIs(scenario.progress.host_raw_facts(original['receipt'])['resumable'], True)
        result = f.invoke('control', request)
        self.assertEqual(result['status'], 'blocked', result)
        self.assertNotIn('recovery', f.state()['control']['context']['handoff_progress'])
        self.assertEqual(f.state()['host']['last_stop'], original)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(DesignRecoveryTests)
