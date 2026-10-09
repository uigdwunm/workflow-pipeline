"""Controlled replacement through C, production control, and real Git snapshots."""
import copy
import hashlib
import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_workflow_progress import ProgressTests
import stage_dispatch as dispatch
import execution_plan


class RecoveryFixture(ProgressTests):
    def input_for(self, stage, predecessor=None):
        value = super().input_for(stage, predecessor)
        if stage == 3:
            value['scope']['implementation_paths'] = ['extra.py', 'impl.py']
            manifest = value['scope']['downstream']['manifest']
            baseline_paths = self.flow_git('ls-tree', '-r', '--name-only', value['scope']['baseline']).splitlines()
            manifest.append({'path': 'extra.py', 'owner': 'implementation', 'kind': 'source',
                             'operation': 'modify' if 'extra.py' in baseline_paths else 'add',
                             'basis': copy.deepcopy(manifest[-1]['basis'])})
            manifest.sort(key=lambda item: item['path'])
            value['scope']['owned_paths'] = ['impl.py'] if self.request['host']['role'] == 'implementation-dispatcher' else ['extra.py', 'impl.py']
            import entry_prepare
            value['authorization']['scope_digest'] = entry_prepare.digest(value['scope'])
            if predecessor is None:
                plan = value['semantic']['inline_plan']
                extra = copy.deepcopy(plan['steps'][0])
                extra.update(id='inline-extra', target={'path': 'extra.py', 'symbol': 'run',
                                                        'operation': 'add'})
                plan['steps'].append(extra)
                group = copy.deepcopy(plan['behavior_slices'][0])
                group.update(id='inline-extra', step_ids=['inline-extra'],
                    verification_refs=extra['verification_refs'], read_only=extra['read_only'])
                plan['behavior_slices'].append(group)
        return value

    def execution_input(self):
        value = super().execution_input()
        value['scope']['downstream'] = copy.deepcopy(self.state()['handoff']['scope']['downstream'])
        import entry_prepare
        value['authorization']['scope_digest'] = entry_prepare.digest(value['scope'])
        return value


class DispatchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = RecoveryFixture(methodName='runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def assert_original_intent_ack(self, request):
        before = self.f.checkpoint.read_bytes()
        with patch.object(dispatch, 'checkpoint', side_effect=AssertionError('exact ACK redispatched control')):
            result = self.f.invoke('control', request)
        self.assertTrue(result['acknowledged'], result)
        self.assertEqual(result['next_action']['operation'], 'control-effects', result)
        self.assertEqual(result['next_action']['result']['effects'], [])
        self.assertIs(result['next_action']['write_authority'], False)
        self.assertEqual(self.f.checkpoint.read_bytes(), before)

    def fresh_replacement_census(self):
        import workflow_progress as progress
        f = self.f
        snapshot = f.observation('stopped', 'result', ref='native:dispatcher')['lifecycle']
        identities = [item['id'] for page in snapshot['pages'] for item in page['response']['data']]
        self.assertEqual(len(identities), len(set(identities)))
        owner = progress.Progress(f.checkpoint, progress.read_active_owner(f.checkpoint))
        self.assertEqual(set(identities), owner.native_refs() | {'native:replacement'})
        result = f.invoke('lifecycle-state', snapshot)
        self.assertNotEqual(result['status'], 'blocked', result)
        owner = progress.Progress(f.checkpoint, progress.read_active_owner(f.checkpoint))
        self.assertEqual(owner.stop_barrier_pending(), [])
        self.assertEqual(owner.state['host']['status'], 'stopped')

    def recovery_intent(self, *, accept=True, committed=False):
        request = self.prepare_mixed(accept=accept)
        if committed:
            self.f.flow_git('add', 'extra.py' if accept else 'impl.py')
            self.f.flow_git('commit', '-qm', 'retained dispatcher progress')
        prepared = self.f.invoke('control', request)['next_action']['result']['recovery']
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

    def ready_replacement(self, *, accept=True, committed=False):
        prepared, identity, request = self.recovery_intent(accept=accept, committed=committed)
        intent = self.f.invoke('control',request)['next_action']['request']
        self.replacement_response(prepared,intent,'ready','ready-response')
        self.fresh_replacement_census()
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
                'file_hashes':{'impl.py':hashlib.sha256(data).hexdigest()},'tests':['focused passed'],
                'write_release':dispatch.execution_release_token(slot['record']),
                'plan_refs':dispatch.dispatch_input(slot['record'])['semantic']['plan_refs'],
                'plan_authority':f.state()['control']['context']['handoff_progress']['plan_authority']}}})
        if accept:
            f.invoke('allocation', {'allocation_id':'slice-one','operation':'accept','decision':{'reference':'dispatcher:accepted'}})
            second = f.execution_input()
            second['scope']['owned_paths'] = ['extra.py']
            authority = f.state()['scope_authority']
            ticket = authority['execution_plan']['catalog']['tickets'][0]
            step = next(item for item in ticket['plan']['steps'] if item['id'] == 'inline-extra')
            second['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, step)]
            second['authorization']['scope_digest'] = dispatch.entry.digest(second['scope'])
            prepared = f.invoke('allocation', {'allocation_id':'extra-slice','operation':'prepare',
                                                 'handoff':second})
            self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
            slot = f.state()['allocations']['extra-slice']
            f.invoke('allocation', {'allocation_id':'extra-slice','operation':'bind',
                'receipt':f.receipt(slot['record'], ref='native:extra-executor')})
            extra = b'unaccepted agent bytes\n'
            (f.flow/'extra.py').write_bytes(extra)
            slot = f.state()['allocations']['extra-slice']
            received = f.invoke('allocation', {'allocation_id':'extra-slice','operation':'receive',
                'receipt':f.receipt(slot['record'],'stopped','result',ref='native:extra-executor'),
                'result':{'delivery_id':'extra-slice-result','status':'completed','payload':{
                    'changed_paths':['extra.py'], 'file_hashes':{'extra.py':hashlib.sha256(extra).hexdigest()},
                    'tests':['second agent stopped after focused check'],
                    'write_release':dispatch.execution_release_token(slot['record']),
                    'plan_refs':dispatch.dispatch_input(slot['record'])['semantic']['plan_refs'],
                    'plan_authority':f.state()['control']['context']['handoff_progress']['plan_authority']}}})
            self.assertEqual(received['next_action']['result']['status'], 'received', received)
        f.settings['thread_id'] = 'task'
        f.request['host'].update(thread_id='task',role='controller',source_ref=None); f.request['host'].pop('actor_ref',None)
        # The external host attests nonresumability; stopped alone does not.
        stopped = f.observation('stopped','result',ref='native:dispatcher')
        stopped['receipt']['raw'].update(resumable=False, dispatch_available=True)
        f.invoke('observe', stopped)
        progress = f.state()['control']['context']
        evidence = {'dispatcher_ref':'native:dispatcher','attempt':progress['carrier']['attempt'],
                    'reference':'controller:recover','reason':'original identity cannot resume',
            'stop_receipts':[stopped['receipt']['receipt_ref']] + [
                slot['record']['receipts'][-1]['receipt_ref']
                for slot in f.state()['allocations'].values()],
                    'call_receipts':[stopped['provenance']['response_ref']]}
        return {'action':'prepare-dispatch-recovery','evidence':evidence,
                'receipt':{'controller_ref':'task','reference':'controller:recover'}}

    def test_prepares_mixed_uncommitted_bytes_without_commit_or_cleaning(self):
        request = self.prepare_mixed(); f = self.f
        f.flow_git('add','extra.py'); f.flow_git('commit','-qm','dispatcher checkpoint')
        before = f.flow_git('rev-parse','HEAD'), f.flow_git('status','--porcelain')
        result = f.invoke('control', request)
        self.assertEqual(result['next_action']['operation'], 'control-effects', result)
        recovery = result['next_action']['result']['recovery']
        self.assertEqual(recovery['ownership']['accepted'], ['impl.py'])
        self.assertEqual(recovery['ownership']['unaccepted'], ['extra.py'])
        self.assertEqual(recovery['ownership']['dispatcher'], [])
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
        self.assert_original_intent_ack(request)
        self.assertEqual(f.invoke('advance')['next_action']['operation'], 'lookup-recovery-host')
        intent = issued['next_action']['request']
        ready = {'adapter':'fixture-native','call_ref':'replacement-call','response_ref':'replacement-response',
                 'intent_id':intent['intent_id'],'status':'ready','ref':'native:replacement',
                 'raw':{'write_authority':False,'ref':'native:replacement','status':'ready'}}
        observed = f.invoke('control', {'action':'dispatch-recovery-result','evidence':{'recovery_id':prepared['recovery_id'],'receipt':ready},
                                      'receipt':ready})
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:dispatcher')
        self.fresh_replacement_census()
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
        self.assert_original_intent_ack(request)
        self.assertEqual(f.invoke('advance')['next_action']['operation'], 'lookup-recovery-host')
        self.replacement_response(prepared,first['request'],'not-created','not-created-response')
        retried = f.invoke('control',request)['next_action']
        recovery = f.state()['control']['context']['handoff_progress']['recovery']
        self.assertEqual(retried['operation'], 'invoke-recovery-host', {
            'actual': retried, 'original_request': request,
            'saved_recovery': {key: recovery[key] for key in (
                'state', 'recovery_id', 'snapshot_digest', 'intent', 'decision', 'receipts')}})
        self.assertEqual(first['request'],retried['request'])
        self.assert_original_intent_ack(request)
        self.assertEqual(f.invoke('advance')['next_action']['operation'], 'lookup-recovery-host')

    def test_original_creation_receipt_is_retained_after_host_loss_without_activation(self):
        prepared, _, request = self.recovery_intent(); f = self.f
        intent = f.invoke('control',request)['next_action']['request']
        import workflow_progress as progress
        f.save_transport(instance=f.state()['native_host']['instance'], state='lost', native_event_sequence=0)
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
        commit = progress.state_store.StateStore.commit_current
        interrupted = []
        def interrupt(owner, **candidate):
            subject = candidate['subjects'][candidate['metadata']['stage_subject']]
            recovery = subject['control']['context']['handoff_progress'].get('recovery')
            if recovery and recovery['state'] == 'activated':
                self.assertEqual(subject['control']['context']['carrier']['ref'], 'native:replacement')
                interrupted.append(recovery['recovery_id'])
                raise KeyboardInterrupt()
            return commit(owner, **candidate)
        with patch.object(progress.state_store.StateStore, 'commit_current', interrupt):
            with self.assertRaises(KeyboardInterrupt): f.invoke('control',request)
        self.assertEqual(interrupted, [request['evidence']['recovery_id']])
        self.assertEqual(f.state()['control']['context']['carrier']['ref'],'native:dispatcher')
        f.invoke('resume')
        self.assertEqual(f.state()['control']['context']['carrier']['ref'],'native:replacement')
        self.assertEqual(len(f.state()['recovery_dispatch_history']),1)
        self.assertTrue(f.invoke('control',request)['acknowledged'])

    def test_unaccepted_allocation_keeps_ownership_until_exact_controller_release(self):
        request = self.ready_replacement(accept=False, committed=True); f = self.f
        original_baseline = f.state()['handoff']['scope']['baseline']
        retained_commit = f.flow_git('rev-parse', 'HEAD')
        self.assertNotEqual(retained_commit, original_baseline)
        activated = f.invoke('control', request)
        self.assertEqual(f.state()['control']['context']['carrier']['ref'], 'native:replacement', activated)
        progress = f.state()['control']['context']['handoff_progress']
        self.assertEqual(progress['recovery']['state'], 'activated', activated)
        self.assertEqual(progress['recovery']['revalidate'],['native:executor'])
        self.assertEqual(progress['executions'][0]['state'],'received')
        release = {'action':'release-recovery-allocation','evidence':{
            'recovery_id':progress['recovery']['recovery_id'],'snapshot_digest':progress['recovery']['snapshot_digest'],
            'agent_ref':'native:executor','reference':'controller:assume-unaccepted'},
            'receipt':{'controller_ref':'task','reference':'controller:assume-unaccepted'}}
        import workflow_progress as owner_progress
        original_block = owner_progress.Progress.block
        primary_errors = []
        def capture_error(owner, error):
            primary_errors.append({'type': type(error).__name__, 'code': getattr(error, 'code', None),
                                   'message': str(error)})
            return original_block(owner, error)
        with patch.object(owner_progress.Progress, 'block', capture_error):
            result = f.invoke('control', release)
        self.assertEqual(f.state()['control']['context']['handoff_progress']['executions'][0]['state'],
                         'cancelled', {'result': result, 'primary_errors': primary_errors})
        self.assertTrue(f.invoke('control',release)['acknowledged'])
        self.assertEqual((f.flow/'impl.py').read_text(),'accepted bytes\n')
        execution = f.execution_input()
        execution['entry']['host']['actor_ref'] = 'native:replacement'
        import entry_prepare
        execution['expected_entry'] = entry_prepare.resolve(execution['entry'])
        allocated = f.invoke('allocation', {'allocation_id':'replacement-slice','operation':'prepare','handoff':execution})
        self.assertEqual((allocated.get('next_action') or {}).get('operation'), 'invoke-host', allocated.get('error'))
        slot = f.state()['allocations']['replacement-slice']
        self.assertEqual(slot['handoff']['scope']['baseline'], retained_commit)
        self.assertEqual(f.state()['handoff']['scope']['baseline'], original_baseline)
        f.invoke('allocation', {'allocation_id':'replacement-slice','operation':'bind',
            'receipt':f.receipt(slot['record'], ref='native:new-executor')})
        data = b'revalidated successor implementation\n'; (f.flow/'impl.py').write_bytes(data)
        slot = f.state()['allocations']['replacement-slice']
        received = f.invoke('allocation', {'allocation_id':'replacement-slice','operation':'receive',
            'receipt':f.receipt(slot['record'],'stopped','result',ref='native:new-executor'),
            'result':{'delivery_id':'new-slice','status':'completed','payload':{'changed_paths':['impl.py'],
                'file_hashes':{'impl.py':hashlib.sha256(data).hexdigest()},'tests':['successor focused check passed'],
                'write_release':dispatch.execution_release_token(slot['record']),
                'plan_refs':dispatch.dispatch_input(slot['record'])['semantic']['plan_refs'],
                'plan_authority':f.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.assertEqual((received.get('next_action') or {}).get('result', {}).get('status'), 'received', received.get('error'))
        accepted = f.invoke('allocation', {'allocation_id':'replacement-slice','operation':'accept',
            'decision':{'reference':'replacement:accepted'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted')
        self.assertEqual(f.flow_git('rev-parse','HEAD'), retained_commit)
        self.assertEqual(f.state()['control']['context']['handoff_progress']['git_baseline_commit'], original_baseline)
        followup = f.execution_input()
        followup['entry']['host']['actor_ref'] = 'native:replacement'
        followup['expected_entry'] = entry_prepare.resolve(followup['entry'])
        next_slice = f.invoke('allocation', {'allocation_id':'post-recovery-slice',
            'operation':'prepare','handoff':followup})
        self.assertEqual((next_slice.get('next_action') or {}).get('operation'),
                         'invoke-host', next_slice.get('error'))

    def test_ordinary_dispatcher_cannot_allocate_after_unowned_partial_commit(self):
        f = self.f; f.begin_dispatcher()
        original = f.state()['control']['context']['handoff_progress']['git_baseline_commit']
        (f.flow/'extra.py').write_text('committed first slice\n')
        f.flow_git('add','extra.py'); f.flow_git('commit','-qm','first slice')
        current = f.flow_git('rev-parse','HEAD')
        result = f.invoke('allocation', {'allocation_id':'next-slice','operation':'prepare','handoff':f.execution_input()})
        self.assertNotEqual((result.get('next_action') or {}).get('operation'),'invoke-host')
        progress = f.state()['control']['context']['handoff_progress']
        self.assertEqual(progress['git_baseline_commit'],original)
        self.assertEqual(progress['executions'],[])
        self.assertEqual(f.flow_git('rev-parse','HEAD'),current)

    def test_new_allocation_cannot_rebase_away_an_original_protected_file(self):
        f = self.f
        f.context = f.context_for(3); parent = f.input_for(3)
        parent['scope']['protected_paths'].append('existing.txt')
        import entry_prepare
        parent['authorization']['scope_digest'] = entry_prepare.digest(parent['scope'])
        f.begin('continuous',parent); f.invoke('observe',f.observation(ref='native:dispatcher'))
        original = f.state()['control']['context']['handoff_progress']['git_baseline_commit']
        (f.flow/'existing.txt').write_text('changed original protection\n')
        f.flow_git('add','existing.txt'); f.flow_git('commit','-qm','invalid protected commit')
        execution = f.execution_input()
        if 'existing.txt' not in execution['scope']['protected_paths']:
            execution['scope']['protected_paths'].append('existing.txt')
        execution['authorization']['scope_digest'] = entry_prepare.digest(execution['scope'])
        result = f.invoke('allocation',{'allocation_id':'protected-drift','operation':'prepare','handoff':execution})
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['error']['code'],'source_changed')
        self.assertEqual(f.state()['control']['context']['handoff_progress']['git_baseline_commit'],original)
        self.assertEqual(f.state()['control']['context']['handoff_progress']['executions'],[])

    def test_new_allocation_cannot_change_committed_scope_or_ancestry(self):
        f = self.f; f.begin_dispatcher()
        (f.flow/'foreign.py').write_text('outside approved paths\n')
        f.flow_git('add','foreign.py'); f.flow_git('commit','-qm','out-of-scope commit')
        current = f.flow_git('rev-parse','HEAD')
        result = f.invoke('allocation',{'allocation_id':'foreign','operation':'prepare','handoff':f.execution_input()})
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(f.state()['control']['context']['handoff_progress']['executions'],[])
        self.assertEqual(f.flow_git('rev-parse','HEAD'),current)

    def test_new_allocation_head_must_descend_from_original_stage_baseline(self):
        f = self.f
        (f.flow/'extra.py').write_text('before dispatcher\n')
        f.flow_git('add','extra.py'); f.flow_git('commit','-qm','dispatch baseline')
        f.begin_dispatcher()
        original = f.state()['control']['context']['handoff_progress']['git_baseline_commit']
        f.flow_git('reset','--hard',f.binding['base_commit'])
        (f.flow/'extra.py').write_text('sibling commit\n')
        f.flow_git('add','extra.py'); f.flow_git('commit','-qm','different ancestry')
        current = f.flow_git('rev-parse','HEAD')
        result = f.invoke('allocation',{'allocation_id':'wrong-ancestry','operation':'prepare','handoff':f.execution_input()})
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(f.state()['control']['context']['handoff_progress']['executions'],[])
        self.assertEqual(f.state()['control']['context']['handoff_progress']['git_baseline_commit'],original)
        self.assertEqual(f.flow_git('rev-parse','HEAD'),current)


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(DispatchRecoveryTests)
