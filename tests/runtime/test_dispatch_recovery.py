"""Controlled replacement through C, production control, and real Git snapshots."""
import copy
import hashlib
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_workflow_progress import ProgressTests


class RecoveryFixture(ProgressTests):
    def input_for(self, stage, predecessor=None):
        value = super().input_for(stage, predecessor)
        if stage == 3:
            value['scope']['implementation_paths'] = ['extra.py', 'impl.py']
            value['scope']['owned_paths'] = ['impl.py'] if self.request['host']['role'] == 'implementation-dispatcher' else ['extra.py', 'impl.py']
            import entry_prepare
            value['authorization']['scope_digest'] = entry_prepare.digest(value['scope'])
        return value


class DispatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = RecoveryFixture(methodName='runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def recovery_intent(self, *, accept=True):
        prepared = self.f.invoke('control', self.prepare_mixed(accept=accept))['next_action']['result']['recovery']
        decision = {'reference':'controller:assume-snapshot','authority_digest':prepared['authority_digest'],
            'attempt':prepared['attempt'],'remaining_paths':prepared['remaining_paths'],'assume_paths':prepared['ownership']['dispatcher']}
        identity = {'recovery_id':prepared['recovery_id'],'snapshot_digest':prepared['snapshot_digest'],'decision':decision}
        request = {'action':'dispatch-recovery-intent','evidence':identity,
                   'receipt':{'controller_ref':'task','reference':decision['reference']}}
        return prepared, identity, request

    def replacement_response(self, prepared, intent, status, response):
        receipt = {'adapter':'fixture-native','call_ref':'call:'+response,'response_ref':response,
            'intent_id':intent['intent_id'],'status':status,'ref':'native:replacement' if status == 'ready' else None,
            'raw':{'status':status,'write_authority':False}}
        return self.f.invoke('control', {'action':'dispatch-recovery-result',
            'evidence':{'recovery_id':prepared['recovery_id'],'receipt':receipt},'receipt':receipt})

    def ready_replacement(self, *, accept=True):
        prepared, identity, request = self.recovery_intent(accept=accept)
        intent = self.f.invoke('control',request)['next_action']['request']
        self.replacement_response(prepared,intent,'ready','ready-response')
        snapshot = self.f.observation('stopped','result',ref='native:dispatcher')['lifecycle']
        snapshot['pages'][0]['response']['data'].append({'id':'native:replacement','status':{'type':'idle'}})
        self.f.invoke('lifecycle-state',snapshot)
        return {'action':'recover-dispatch','evidence':{**identity,'replacement_ref':'native:replacement'},
                'receipt':request['receipt']}

    def prepare_mixed(self, *, accept=True):
        f = self.f; f.prepare_execution()
        slot = f.state()['allocations']['slice-one']
        f.invoke('allocation', {'allocation_id':'slice-one','operation':'bind',
            'receipt':f.receipt(slot['record'], ref='native:executor')})
        data = b'accepted bytes\n'; (f.flow/'impl.py').write_bytes(data)
        slot = f.state()['allocations']['slice-one']
        f.invoke('allocation', {'allocation_id':'slice-one','operation':'receive',
            'receipt':f.receipt(slot['record'],'stopped','result',ref='native:executor'),
            'result':{'delivery_id':'slice','status':'completed','payload':{'changed_paths':['impl.py'],
                'file_hashes':{'impl.py':hashlib.sha256(data).hexdigest()},'tests':['focused passed']}}})
        if accept:
            f.invoke('allocation', {'allocation_id':'slice-one','operation':'accept','decision':{'reference':'dispatcher:accepted'}})
        (f.flow/'extra.py').write_text('dispatcher bytes\n')
        f.settings['thread_id'] = 'task'
        f.request['host'].update(thread_id='task',role='controller',source_ref=None); f.request['host'].pop('actor_ref',None)
        # The external host attests nonresumability; stopped alone does not.
        stopped = f.observation('stopped','result',ref='native:dispatcher')
        stopped['receipt']['raw'].update(resumable=False, dispatch_available=True)
        f.invoke('observe', stopped)
        progress = f.state()['control']['context']
        evidence = {'dispatcher_ref':'native:dispatcher','attempt':progress['carrier']['attempt'],
                    'reference':'controller:recover','reason':'original identity cannot resume',
                    'stop_receipts':[stopped['receipt']['receipt_ref'], f.state()['allocations']['slice-one']['record']['receipts'][-1]['receipt_ref']],
                    'call_receipts':[stopped['provenance']['response_ref']]}
        return {'action':'prepare-dispatch-recovery','evidence':evidence,
                'receipt':{'controller_ref':'task','reference':'controller:recover'}}

    def test_prepares_mixed_uncommitted_bytes_without_commit_or_cleaning(self):
        request = self.prepare_mixed(); f = self.f
        f.flow_git('add','extra.py'); f.flow_git('commit','-qm','dispatcher checkpoint')
        (f.flow/'extra.py').write_text('dispatcher bytes\ncontinued uncommitted\n')
        before = f.flow_git('rev-parse','HEAD'), f.flow_git('status','--porcelain')
        result = f.invoke('control', request)
        self.assertEqual(result['next_action']['operation'], 'control-effects', result)
        recovery = result['next_action']['result']['recovery']
        self.assertEqual(recovery['ownership']['accepted'], ['impl.py'])
        self.assertEqual(recovery['ownership']['dispatcher'], ['extra.py'])
        self.assertEqual(before, (f.flow_git('rev-parse','HEAD'), f.flow_git('status','--porcelain')))

    def test_unique_prepared_successor_activates_and_duplicate_does_not_clear_later_state(self):
        f = self.f
        prepared = f.invoke('control', self.prepare_mixed())['next_action']['result']['recovery']
        decision = {'reference':'controller:assume-snapshot','authority_digest':prepared['authority_digest'],
                    'attempt':prepared['attempt'],'remaining_paths':prepared['remaining_paths'],
                    'assume_paths':prepared['ownership']['dispatcher']}
        identity = {'recovery_id':prepared['recovery_id'],'snapshot_digest':prepared['snapshot_digest'],'decision':decision}
        request = {'action':'dispatch-recovery-intent','evidence':identity,
                   'receipt':{'controller_ref':'task','reference':decision['reference']}}
        issued = f.invoke('control', request)
        self.assertEqual(issued['next_action']['operation'], 'invoke-recovery-host', issued)
        self.assertEqual(f.invoke('control', request)['next_action']['operation'], 'lookup-recovery-host')
        intent = issued['next_action']['request']
        ready = {'adapter':'fixture-native','call_ref':'replacement-call','response_ref':'replacement-response',
                 'intent_id':intent['intent_id'],'status':'ready','ref':'native:replacement',
                 'raw':{'write_authority':False,'ref':'native:replacement','status':'ready'}}
        observed = f.invoke('control', {'action':'dispatch-recovery-result','evidence':{'recovery_id':prepared['recovery_id'],'receipt':ready},
                                      'receipt':ready})
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:dispatcher')
        snapshot = f.observation('stopped','result',ref='native:dispatcher')['lifecycle']
        snapshot['pages'][0]['response']['data'].append({'id':'native:replacement','status':{'type':'idle'}})
        f.invoke('lifecycle-state', snapshot)
        activated = f.invoke('control', {'action':'recover-dispatch','evidence':{**identity,'replacement_ref':'native:replacement'},
                                       'receipt':{'controller_ref':'task','reference':decision['reference']}})
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:replacement', activated)
        self.assertNotEqual(f.state()['control']['context']['carrier']['attempt'], prepared['attempt'])
        self.assertEqual((f.flow/'impl.py').read_text(), 'accepted bytes\n')
        self.assertIsNone(f.state()['control']['context']['handoff_progress']['candidate'])
        later = f.invoke('observe',f.observation('idle','result',{'delivery_id':'later-error','status':'technical_error',
            'payload':{'message':'new original-scope failure'}},ref='native:replacement'))
        self.assertIn('business_block', f.state(), later)
        block = copy.deepcopy(f.state()['business_block'])
        repeated = f.invoke('control', {'action':'recover-dispatch','evidence':{**identity,'replacement_ref':'native:replacement'},
                                       'receipt':{'controller_ref':'task','reference':decision['reference']}})
        self.assertTrue(repeated['acknowledged'])
        self.assertEqual(f.state()['business_block'],block)

    def test_mode_drift_and_out_of_scope_bytes_are_rejected_without_cleanup(self):
        request = self.prepare_mixed(); f = self.f
        (f.flow/'impl.py').chmod(0o755)
        before = f.flow_git('rev-parse','HEAD'), f.flow_git('status','--porcelain')
        rejected = f.invoke('control', request)
        self.assertEqual(rejected['status'], 'blocked', rejected)
        self.assertIsNone(f.state()['control']['context']['handoff_progress'].get('recovery'))
        self.assertEqual(before, (f.flow_git('rev-parse','HEAD'), f.flow_git('status','--porcelain')))
        (f.flow/'impl.py').chmod(0o644)
        (f.flow/'foreign.py').write_text('not owned')
        rejected = f.invoke('control', request)
        self.assertEqual(rejected['status'], 'blocked', rejected)
        self.assertTrue((f.flow/'foreign.py').exists())

    def test_stopped_without_nonresumability_and_unknown_calls_cannot_prepare(self):
        request = self.prepare_mixed(); f = self.f
        wrong = copy.deepcopy(request); wrong['evidence']['stop_receipts'] = ['child-self-report']
        rejected = f.invoke('control', wrong)
        self.assertEqual(rejected['status'], 'blocked', rejected)
        self.assertIsNone(f.state()['control']['context']['handoff_progress'].get('recovery'))

    def test_unknown_create_only_reconciles_and_not_created_retries_same_intent(self):
        prepared, _, request = self.recovery_intent(); f = self.f
        first = f.invoke('control',request)['next_action']
        self.assertEqual(first['operation'],'invoke-recovery-host')
        self.replacement_response(prepared,first['request'],'unknown','unknown-response')
        for operation in ('advance','resume'):
            self.assertEqual(f.invoke(operation)['next_action']['operation'],'lookup-recovery-host')
        self.assertEqual(f.invoke('control',request)['next_action']['operation'],'lookup-recovery-host')
        self.replacement_response(prepared,first['request'],'not-created','not-created-response')
        retried = f.invoke('control',request)['next_action']
        self.assertEqual(retried['operation'],'invoke-recovery-host',retried)
        self.assertEqual(first['request'],retried['request'])
        self.assertEqual(f.invoke('control',request)['next_action']['operation'],'lookup-recovery-host')

    def test_original_creation_receipt_is_retained_after_host_loss_without_activation(self):
        prepared, _, request = self.recovery_intent(); f = self.f
        intent = f.invoke('control',request)['next_action']['request']
        import workflow_progress as progress
        outer = progress.read_record(f.checkpoint)
        outer['transport'] = {'instance':'fixture-host','state':'lost','native_event_sequence':0}
        progress.atomic_save(f.checkpoint,outer)
        f.invoke('transport-lost',{'instance':'fixture-host','reason':'owner lost after native call'})
        result = self.replacement_response(prepared,intent,'ready','late-original-response')
        self.assertEqual(f.state()['control']['context']['handoff_progress']['recovery']['state'],'ready',result.get('error'))
        self.assertEqual(f.state()['control']['context']['carrier']['ref'],'native:dispatcher')
        self.assertEqual(result['next_action']['operation'],'await-host-recovery')
        self.assertIn('native:replacement',result['next_action']['native_refs'])

    def test_activation_rejects_index_content_and_deletion_drift_without_changes(self):
        request = self.ready_replacement(); f = self.f
        original = (f.flow/'extra.py').read_bytes()
        for mutation in ('index','content','delete'):
            if mutation == 'index': f.flow_git('add','extra.py')
            elif mutation == 'content': (f.flow/'extra.py').write_text('drift')
            else: (f.flow/'extra.py').unlink()
            before = f.flow_git('rev-parse','HEAD'), f.flow_git('status','--porcelain')
            rejected = f.invoke('control',request)
            self.assertEqual(rejected['status'],'blocked',rejected)
            self.assertEqual(f.state()['control']['context']['carrier']['ref'],'native:dispatcher')
            self.assertEqual(before,(f.flow_git('rev-parse','HEAD'),f.flow_git('status','--porcelain')))
            if mutation == 'index': f.flow_git('reset','--','extra.py')
            (f.flow/'extra.py').write_bytes(original)
        accepted = f.invoke('control',request)
        self.assertEqual(f.state()['control']['context']['carrier']['ref'],'native:replacement',accepted)
        self.assertTrue(f.invoke('control',request)['acknowledged'])

    def test_activation_save_interruption_replays_original_control_not_creation(self):
        request = self.ready_replacement(); f = self.f
        import workflow_progress as progress
        save = progress.atomic_save
        def interrupt(path, value):
            recovery = value[progress.KEY]['control']['context']['handoff_progress'].get('recovery')
            if recovery and recovery['state'] == 'activated':
                raise KeyboardInterrupt()
            return save(path,value)
        with patch.object(progress,'atomic_save',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): f.invoke('control',request)
        self.assertEqual(f.state()['control']['context']['carrier']['ref'],'native:dispatcher')
        f.invoke('resume')
        self.assertEqual(f.state()['control']['context']['carrier']['ref'],'native:replacement')
        self.assertEqual(len(f.state()['recovery_dispatch_history']),1)
        self.assertTrue(f.invoke('control',request)['acknowledged'])

    def test_unaccepted_allocation_keeps_ownership_until_exact_controller_release(self):
        request = self.ready_replacement(accept=False); f = self.f
        f.invoke('control',request)
        progress = f.state()['control']['context']['handoff_progress']
        self.assertEqual(progress['recovery']['revalidate'],['native:executor'])
        self.assertEqual(progress['executions'][0]['state'],'received')
        release = {'action':'release-recovery-allocation','evidence':{
            'recovery_id':progress['recovery']['recovery_id'],'snapshot_digest':progress['recovery']['snapshot_digest'],
            'agent_ref':'native:executor','reference':'controller:assume-unaccepted'},
            'receipt':{'controller_ref':'task','reference':'controller:assume-unaccepted'}}
        result = f.invoke('control',release)
        self.assertEqual(f.state()['control']['context']['handoff_progress']['executions'][0]['state'],'cancelled',result)
        self.assertTrue(f.invoke('control',release)['acknowledged'])
        self.assertEqual((f.flow/'impl.py').read_text(),'accepted bytes\n')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(DispatchRecoveryTests)
