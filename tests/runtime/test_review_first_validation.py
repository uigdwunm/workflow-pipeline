"""Review-first behavior through C, real B/control and temporary Git."""
import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixture
import workflow_control as control


class ReviewFirstTests(fixture.ProgressTests):
    # Only reuse public fixture operations, not the inherited test inventory.
    def input_for(self, stage, predecessor=None):
        value = super().input_for(stage, predecessor)
        if stage == 3:
            value['semantic']['validation_plan'] = self.plan()
        return value

    def plan(self):
        def check(identity, category, command):
            return {'id': identity, 'category': category, 'command': command,
                    'cwd': str(self.flow), 'pass_condition': 'exit 0',
                    'allowed_skips': [], 'environment': None}
        return {'review_required': [check('focused', 'focused', 'python3 -m unittest focused'),
                                    check('affected', 'affected', 'python3 -m unittest affected')],
                'final_required': [check('full', 'full', './scripts/validate.sh')],
                'environment_not_applicable': 'fixture has no external environment'}

    def ready_candidate(self):
        self.begin_dispatcher()
        (self.flow / 'impl.py').write_text("print('ok')\n")
        self.flow_git('add', 'impl.py'); self.flow_git('commit', '-qm', 'implementation')
        candidate = self.flow_git('rev-parse', 'HEAD')
        outer = fixture.progress.read_record(self.checkpoint)
        self.fixture_accepted_execution(outer[fixture.progress.KEY]['control']['context'], candidate)
        fixture.progress.atomic_save(self.checkpoint, outer)
        target = self.git('rev-parse', 'main')
        self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        checks = [dict(item, status='passed', exit_code=0, start_commit=candidate, end_commit=candidate,
                       environment_fingerprint=None, output_ref='fixture:output:'+item['id'], output_digest='a'*64)
                  for item in self.plan()['review_required']]
        evidence = {'dispatcher_ref':'native:dispatcher','attempt':self.state()['control']['context']['carrier']['attempt'],
                    'commit':candidate, 'expected_target_head':target, 'binding':self.binding,
                    'plan_digest':control.digest(self.plan()), 'checks':checks}
        result = self.invoke('control', {'action':'candidate-ready','evidence':evidence,
            'receipt':{'adapter':'fixture-controller','call_ref':'focused-call','response_ref':'focused-response','raw':{'attempt_id':'final-1','checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'reviewable', result)
        return candidate, evidence

    def direct_policy(self, value):
        return {'mode': 'direct', 'assessment': {
            'reference': 'controller:local-output', 'controller_ref': 'task',
            'requirement_identity': self.frozen['requirement_identity'],
            'scope_digest': value['authorization']['scope_digest'],
            'baseline': value['scope']['baseline'],
            'responsibility': 'Render the approved local output',
            'implementation_paths': ['impl.py'], 'test_paths': ['impl.py'],
            'behavior_ref': 'requirement:output', 'acceptance_ref': 'requirement:verified-output',
            'checks': [v['command'] for v in self.plan()['review_required']], 'testing_seam': 'CLI output',
            'conditions': {name: {'satisfied': True, 'evidence': reason} for name, reason in {
                'behavior_fixed': 'Output and acceptance are frozen',
                'single_responsibility': 'Only output rendering changes',
                'focused_verification': 'CLI check covers this behavior independently',
                'locations_known': 'The output lives in impl.py'}.items()},
            'exclusions': {name: {'present': False, 'evidence': 'Output-only change has no ' + name}
                for name in ('state_machine', 'concurrency', 'recovery', 'migration', 'public_interface',
                             'cross_module_interface', 'data_format', 'permissions', 'workflow_state')}}}

    def test_controller_direct_candidate_uses_real_dispatcher_without_execution(self):
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        self.begin('continuous', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        (self.flow / 'impl.py').write_text("print('ok')\n")
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'direct implementation')
        candidate = self.flow_git('rev-parse', 'HEAD')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        checks = fixture.transfer.checks_for(self.plan()['review_required'], candidate)
        evidence = {'dispatcher_ref': 'native:dispatcher',
            'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': self.git('rev-parse', 'main'),
            'binding': self.binding, 'plan_digest': control.digest(self.plan()), 'checks': checks}
        response = self.invoke('control', {'action': 'candidate-ready', 'evidence': evidence,
            'receipt': {'adapter': 'fixture-controller', 'call_ref': 'focused', 'response_ref': 'focused-result',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': 'native:dispatcher'}}})
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['state'], 'reviewable', response)
        self.assertEqual(checkpoint['executions'], [])
        self.assertEqual(checkpoint['candidate_evidence']['direct_provenance']['dispatcher_ref'], 'native:dispatcher')
        self.converge(candidate, evidence)
        checkpoint = self.state()['control']['context']['handoff_progress']
        started = self.invoke('control', {'action': 'validation-start', 'evidence': {
            'attempt_id': 'final-1', 'dispatcher_ref': 'native:dispatcher',
            'carrier_attempt': self.state()['control']['context']['carrier']['attempt'],
            **{key: checkpoint[key] for key in ('candidate', 'expected_target_head', 'plan_digest', 'review_digest')}},
            'receipt': {'adapter': 'fixture', 'call_ref': 'final', 'response_ref': 'final-started', 'raw': {'decision': 'run'}}})
        if started['next_action']['operation'] == 'inspect-host-state':
            started = self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        self.assertEqual(started['next_action']['operation'], 'continue-host', started)
        checks = fixture.transfer.checks_for(self.plan()['final_required'], candidate)
        self.invoke('control', self.final_result_request(checks))
        self.finish_resumed_validation_delivery(started, candidate)
        delivered = self.state()['accepted']
        self.assertEqual(delivered['payload']['candidate_commit'], candidate)
        closed = self.finish_next(4, 'continuous')
        self.assertEqual(closed['status'], 'accepted', closed)
        self.assertFalse(self.flow.exists())

    def escalate_and_adopt(self, storage, advance_target=False, drift_mode=False,
                           advance_target_after_stop=False):
        import hashlib
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        self.begin('continuous', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        data = b"print('inherited')\n"
        (self.flow / 'impl.py').write_bytes(data)
        if storage != 'dirty':
            self.flow_git('add', 'impl.py')
        if storage == 'committed':
            self.flow_git('commit', '-qm', 'retained direct commit')
        if advance_target and not advance_target_after_stop:
            (self.root / 'unrelated.txt').write_text('upstream\n')
            self.git('add', 'unrelated.txt')
            self.git('commit', '-qm', 'unrelated upstream change')
        before = self.flow_git('rev-parse', 'HEAD'), self.flow_git('ls-files', '--stage')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        if advance_target and advance_target_after_stop:
            (self.root / 'unrelated.txt').write_text('upstream\n')
            self.git('add', 'unrelated.txt')
            self.git('commit', '-qm', 'unrelated upstream change')
        context = self.state()['control']['context']
        request = {'action': 'escalate-implementation', 'evidence': {
            'dispatcher_ref': 'native:dispatcher', 'attempt': context['carrier']['attempt'],
            'reference': 'controller:escalate', 'reason': 'Behavior needs a wider independent verification',
            'assessment_reference': 'controller:local-output'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:escalate'}}
        result = self.invoke('control', request)
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['implementation_policy']['mode'], 'full', result)
        self.assertEqual(before, (self.flow_git('rev-parse', 'HEAD'), self.flow_git('ls-files', '--stage')))
        self.assertEqual(self.state()['control']['context']['carrier'], context['carrier'])
        escalation = checkpoint['implementation_escalation']
        self.assertEqual(escalation['pending_paths'], ['impl.py'])
        self.assertTrue(self.invoke('control', request)['acknowledged'])
        self.assertEqual(result['next_action']['operation'], 'inspect-host-state', result)
        continued = self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        self.assertEqual(continued['next_action']['operation'], 'continue-host', continued)
        self.assertEqual(continued['next_action']['payload']['ref'], 'native:dispatcher')
        self.invoke('observe', self.observation('running', 'result', ref='native:dispatcher'))
        if advance_target:
            if storage != 'committed':
                self.flow_git('add', 'impl.py')
                self.flow_git('commit', '-qm', 'preserve unaccepted direct bytes before integration')
            self.flow_git('merge', '--no-edit', 'main')
            self.assertEqual((self.flow / 'impl.py').read_bytes(), data)
        execution = self.execution_input()
        execution['semantic']['adopt_paths'] = ['impl.py']
        execution['semantic']['adoption_snapshot_digest'] = escalation['snapshot_digest']
        if drift_mode:
            (self.flow / 'impl.py').chmod(0o755)
        prepared = self.invoke('allocation', {'allocation_id': 'adopt', 'operation': 'prepare', 'handoff': execution})
        if drift_mode:
            self.assertEqual(prepared['status'], 'blocked', prepared)
            self.assertEqual(self.state()['control']['context']['handoff_progress']['executions'], [])
            return
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['implementation_escalation'], escalation)
        slot = self.state()['allocations']['adopt']
        self.invoke('allocation', {'allocation_id': 'adopt', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:executor')})
        slot = self.state()['allocations']['adopt']
        if storage == 'staged':
            valid_payload = {'changed_paths': [], 'adopted_paths': ['impl.py'],
                'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()}, 'tests': ['Inherited CLI verified'],
                'write_release': fixture.transfer.dispatch.execution_release_token(slot['record'])}
            for mutation in (lambda p: p.pop('adopted_paths'), lambda p: p.update(tests=[]),
                             lambda p: p.update(changed_paths=['impl.py'])):
                invalid = copy.deepcopy(valid_payload); mutation(invalid)
                with self.subTest(invalid=invalid), self.assertRaises((control.ControlError, fixture.transfer.entry.PreparationError)):
                    fixture.transfer.dispatch.handle({'protocol': fixture.transfer.handoff.PROTOCOL, 'operation': 'receive',
                        'record': slot['record'], 'control': self.state()['control'],
                        'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
                        'result': {'delivery_id': 'invalid-adoption', 'status': 'completed', 'payload': invalid}})
        received = self.invoke('allocation', {'allocation_id': 'adopt', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
            'result': {'delivery_id': 'adopted', 'status': 'completed', 'payload': {
                'changed_paths': [], 'adopted_paths': ['impl.py'],
                'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                'tests': ['Inherited CLI output verified'],
                'write_release': fixture.transfer.dispatch.execution_release_token(slot['record'])}}})
        self.assertNotEqual(received['status'], 'blocked', received)
        if storage == 'staged':
            slot = self.state()['allocations']['adopt']
            (self.flow / 'impl.py').chmod(0o755)
            with self.assertRaises((control.ControlError, fixture.transfer.entry.PreparationError)):
                fixture.transfer.dispatch.handle({'protocol': fixture.transfer.handoff.PROTOCOL, 'operation': 'accept',
                    'record': slot['record'], 'control': self.state()['control'],
                    'decision': {'reference': 'dispatcher:drift', 'delivery_digest': slot['record']['delivery']['digest']}})
            (self.flow / 'impl.py').chmod(0o644)
        accepted = self.invoke('allocation', {'allocation_id': 'adopt', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:adopt-accepted'}})
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['executions'][0]['state'], 'accepted', accepted)
        self.assertEqual(checkpoint['git_baseline_commit'], value['scope']['baseline'])
        if storage != 'committed' and not advance_target:
            self.flow_git('add', 'impl.py'); self.flow_git('commit', '-qm', 'accepted inherited bytes')
        self.settings['thread_id'] = 'task'
        self.request['host'].update(thread_id='task', role='controller', source_ref=None)
        self.request['host'].pop('actor_ref', None)
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        result = self.invoke('control', self.direct_candidate_request())
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['state'], 'reviewable', result)
        self.assertEqual(checkpoint['candidate_evidence']['changed_paths'], ['impl.py'])
        self.assertEqual(checkpoint['git_baseline_commit'], value['scope']['baseline'])

    def test_direct_escalates_before_integrating_unrelated_target_then_adopts(self):
        self.escalate_and_adopt('committed', advance_target=True)

    def test_direct_escalates_when_target_advances_after_dispatcher_stop(self):
        self.escalate_and_adopt('committed', advance_target=True,
                                advance_target_after_stop=True)

    def test_dirty_direct_escalates_before_integrating_unrelated_target_then_adopts(self):
        self.escalate_and_adopt('dirty', advance_target=True)

    def test_direct_integration_does_not_authorize_inherited_mode_drift(self):
        self.escalate_and_adopt('committed', advance_target=True, drift_mode=True)

    def test_direct_escalation_retains_staged_bytes_and_accepts_explicit_adoption(self):
        self.escalate_and_adopt('staged')

    def test_direct_escalation_retains_dirty_bytes_and_accepts_explicit_adoption(self):
        self.escalate_and_adopt('dirty')

    def test_direct_escalation_retains_committed_bytes_and_original_cumulative_baseline(self):
        self.escalate_and_adopt('committed')

    def test_direct_replacement_defaults_full_and_requires_real_adoption(self):
        self.direct_replacement_and_adoption()

    def test_direct_replacement_after_target_advances_during_stop(self):
        self.direct_replacement_and_adoption(advance_target_after_stop=True)

    def direct_replacement_and_adoption(self, advance_target_after_stop=False):
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        self.begin('continuous', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        (self.flow / 'impl.py').write_text("print('retained')\n")
        self.flow_git('add', 'impl.py'); self.flow_git('commit', '-qm', 'direct retained')
        stopped = self.observation('stopped', 'result', ref='native:dispatcher')
        stopped['receipt']['raw'].update(resumable=False, dispatch_available=True)
        self.invoke('observe', stopped)
        if advance_target_after_stop:
            (self.root / 'unrelated.txt').write_text('upstream after stop\n')
            self.git('add', 'unrelated.txt'); self.git('commit', '-qm', 'advance target after stop')
        context = self.state()['control']['context']
        result = self.invoke('control', {'action': 'prepare-dispatch-recovery', 'evidence': {
            'dispatcher_ref': 'native:dispatcher', 'attempt': context['carrier']['attempt'],
            'reference': 'controller:replace', 'reason': 'Original host cannot resume',
            'stop_receipts': [stopped['receipt']['receipt_ref']],
            'call_receipts': [stopped['provenance']['response_ref']]},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:replace'}})
        recovery = result['next_action']['result']['recovery']
        decision = {'reference': 'controller:assume', 'authority_digest': recovery['authority_digest'],
            'attempt': recovery['attempt'], 'remaining_paths': recovery['remaining_paths'],
            'assume_paths': recovery['ownership']['dispatcher']}
        identity = {'recovery_id': recovery['recovery_id'], 'snapshot_digest': recovery['snapshot_digest'], 'decision': decision}
        receipt = {'controller_ref': 'task', 'reference': decision['reference']}
        intent = self.invoke('control', {'action': 'dispatch-recovery-intent', 'evidence': identity,
            'receipt': receipt})['next_action']['request']
        native = {'adapter': 'fixture-native', 'call_ref': 'replace', 'response_ref': 'replacement',
            'intent_id': intent['intent_id'], 'status': 'ready', 'ref': 'native:replacement',
            'raw': {'status': 'ready', 'write_authority': False}}
        self.invoke('control', {'action': 'dispatch-recovery-result',
            'evidence': {'recovery_id': recovery['recovery_id'], 'receipt': native}, 'receipt': native})
        lifecycle = self.observation('stopped', 'result', ref='native:dispatcher')['lifecycle']
        lifecycle['pages'][0]['response']['data'].append({'id': 'native:replacement', 'status': {'type': 'idle'}})
        self.invoke('lifecycle-state', lifecycle)
        result = self.invoke('control', {'action': 'recover-dispatch',
            'evidence': {**identity, 'replacement_ref': 'native:replacement'}, 'receipt': receipt})
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['implementation_policy'], {'mode': 'full'}, result)
        self.assertEqual(checkpoint['recovery']['direct_policy'], value['semantic']['implementation_policy'])
        self.assertEqual(checkpoint['git_baseline_commit'], value['scope']['baseline'])
        if advance_target_after_stop:
            self.flow_git('merge', '--no-edit', 'main')
        execution = self.execution_input()
        execution['entry']['host']['actor_ref'] = 'native:replacement'
        execution['expected_entry'] = fixture.transfer.entry.resolve(execution['entry'])
        execution['semantic'].update(adopt_paths=['impl.py'], adoption_snapshot_digest=recovery['snapshot_digest'])
        prepared = self.invoke('allocation', {'allocation_id': 'recovered-adopt', 'operation': 'prepare', 'handoff': execution})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = self.state()['allocations']['recovered-adopt']
        self.invoke('allocation', {'allocation_id': 'recovered-adopt', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:recovered-executor')})
        slot = self.state()['allocations']['recovered-adopt']
        data = (self.flow / 'impl.py').read_bytes()
        import hashlib
        response = self.invoke('allocation', {'allocation_id': 'recovered-adopt', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:recovered-executor'),
            'result': {'delivery_id': 'recovered-validated', 'status': 'completed', 'payload': {
                'changed_paths': [], 'adopted_paths': ['impl.py'], 'tests': ['retained CLI verified'],
                'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                'write_release': fixture.transfer.dispatch.execution_release_token(slot['record'])}}})
        self.assertNotEqual(response['status'], 'blocked', response)
        accepted = self.invoke('allocation', {'allocation_id': 'recovered-adopt', 'operation': 'accept',
            'decision': {'reference': 'replacement:accepted'}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['executions'][0]['state'], 'accepted', accepted)

    def begin_direct(self):
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        self.begin('continuous', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        return value

    def direct_candidate_request(self):
        candidate = self.flow_git('rev-parse', 'HEAD')
        checks = fixture.transfer.checks_for(self.plan()['review_required'], candidate)
        return {'action': 'candidate-ready', 'evidence': {'dispatcher_ref': 'native:dispatcher',
            'attempt': self.state()['control']['context']['carrier']['attempt'], 'commit': candidate,
            'expected_target_head': self.git('rev-parse', 'main'), 'binding': self.binding,
            'plan_digest': control.digest(self.plan()), 'checks': checks},
            'receipt': {'adapter': 'fixture', 'call_ref': 'checks-' + candidate, 'response_ref': 'checked-' + candidate,
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}}

    def test_direct_requires_saved_stop_and_rejects_post_stop_git_changes(self):
        self.begin_direct()
        (self.flow / 'impl.py').write_text("print('original')\n")
        self.flow_git('add', 'impl.py'); self.flow_git('commit', '-qm', 'direct')
        request = self.direct_candidate_request()
        result = self.invoke('control', request)
        self.assertEqual(result['error']['code'], 'host_evidence_missing')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        (self.flow / 'impl.py').write_text("print('foreign')\n")
        self.flow_git('add', 'impl.py'); self.flow_git('commit', '-qm', 'post stop foreign')
        result = self.invoke('control', self.direct_candidate_request())
        self.assertEqual(result['status'], 'blocked', result)
        self.assertIsNone(self.state()['control']['context']['handoff_progress']['candidate'])

    def rejected_net_zero_foreign_write(self, committed):
        self.begin_direct()
        (self.root / 'unrelated.txt').write_text('upstream\n')
        self.git('add', 'unrelated.txt'); self.git('commit', '-qm', 'upstream')
        foreign = self.flow / 'foreign.py'
        foreign.write_text('outside approved scope\n')
        self.flow_git('add', 'foreign.py')
        if committed:
            self.flow_git('commit', '-qm', 'unauthorized foreign write')
        foreign.unlink()
        before = self.flow_git('rev-parse', 'HEAD'), self.flow_git('ls-files', '--stage')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        result = self.invoke('control', self.direct_escalation_request())
        self.assertEqual(result['status'], 'blocked', result)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['implementation_policy']['mode'], 'direct')
        self.assertEqual(before, (self.flow_git('rev-parse', 'HEAD'), self.flow_git('ls-files', '--stage')))

    def test_direct_target_advance_does_not_hide_committed_foreign_addition_then_deletion(self):
        self.rejected_net_zero_foreign_write(committed=True)

    def test_direct_target_advance_does_not_hide_staged_foreign_addition_then_deletion(self):
        self.rejected_net_zero_foreign_write(committed=False)

    def test_direct_target_advance_requires_full_even_after_clean_merge(self):
        self.begin_direct()
        (self.flow / 'impl.py').write_text("print('ok')\n")
        self.flow_git('add', 'impl.py'); self.flow_git('commit', '-qm', 'direct')
        (self.root / 'unrelated.txt').write_text('new target\n')
        self.git('add', 'unrelated.txt'); self.git('commit', '-qm', 'advance target')
        self.flow_git('merge', '--no-edit', 'main')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        result = self.invoke('control', self.direct_candidate_request())
        self.assertEqual(result['status'], 'blocked', result)
        self.assertIsNone(self.state()['control']['context']['handoff_progress']['candidate'])

    def test_direct_policy_rejects_unknown_conditions_exclusions_and_mismatched_bindings(self):
        value = self.input_for(3)
        original = self.direct_policy(value)
        changes = [lambda p: p['assessment'].update(controller_ref='native:dispatcher'),
                   lambda p: p['assessment'].update(baseline='f' * 40),
                   lambda p: p['assessment'].update(scope_digest='f' * 64),
                   lambda p: p['assessment'].update(requirement_identity={}),
                   lambda p: p['assessment'].pop('testing_seam'),
                   lambda p: p['assessment'].update(test_paths=['foreign.py'])]
        for name in original['assessment']['conditions']:
            changes.append(lambda p, name=name: p['assessment']['conditions'][name].update(satisfied=None))
        for name in original['assessment']['exclusions']:
            changes.append(lambda p, name=name: p['assessment']['exclusions'][name].update(present=True))
        for change in changes:
            request = copy.deepcopy(value)
            request['semantic']['implementation_policy'] = copy.deepcopy(original)
            change(request['semantic']['implementation_policy'])
            with self.subTest(change=change), self.assertRaises((control.ControlError, fixture.transfer.entry.PreparationError)):
                fixture.transfer.handoff.handle(request)

    def test_direct_resume_rejects_drift_before_any_business_followup(self):
        self.begin_direct()
        (self.flow / 'impl.py').write_text('retained direct bytes\n')
        self.invoke('pause')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(self.state()['status'], 'paused')
        (self.flow / 'impl.py').write_text('unexplained bytes while stopped\n')
        response = self.invoke('resume')
        self.assertEqual(response['status'], 'blocked', response)
        self.assertNotEqual((response.get('next_action') or {}).get('operation'), 'continue-host')
        (self.flow / 'impl.py').write_text('retained direct bytes\n')
        escalated = self.invoke('control', self.direct_escalation_request())
        self.assertEqual(self.state()['status'], 'paused', escalated)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['implementation_policy'], {'mode': 'full'})

    def direct_escalation_request(self):
        return {'action': 'escalate-implementation', 'evidence': {
            'dispatcher_ref': 'native:dispatcher', 'attempt': self.state()['control']['context']['carrier']['attempt'],
            'reference': 'controller:escalate', 'reason': 'Observed recovery impact',
            'assessment_reference': 'controller:local-output'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:escalate'}}

    def test_escalation_response_loss_reuses_snapshot_and_rejects_forged_replay(self):
        from unittest.mock import patch
        self.begin_direct()
        (self.flow / 'impl.py').write_text('inherited\n')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        request = self.direct_escalation_request()
        actual = fixture.progress.dispatch.checkpoint
        def lost(*args, **kwargs):
            actual(*args, **kwargs)
            raise OSError('lost read-only control response')
        with patch.object(fixture.progress.dispatch, 'checkpoint', side_effect=lost):
            self.assertEqual(self.invoke('control', request)['status'], 'blocked')
        transaction = next(iter(self.state()['control_transactions'].values()))
        snapshot = copy.deepcopy(transaction['request']['evidence']['snapshot'])
        self.invoke('control', request)
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['implementation_policy'], {'mode': 'full'})
        self.assertEqual(checkpoint['implementation_escalation']['snapshot'], snapshot)
        self.assertTrue(self.invoke('control', request)['acknowledged'])
        forged = copy.deepcopy(request)
        forged['evidence']['snapshot'] = {'head': 'f' * 40}
        self.assertEqual(self.invoke('control', forged)['status'], 'blocked')
        (self.flow / 'impl.py').write_text('drift\n')
        self.assertEqual(self.invoke('control', request)['status'], 'blocked')

    def test_unknown_escalation_does_not_refreeze_drifted_bytes(self):
        from unittest.mock import patch
        self.begin_direct()
        (self.flow / 'impl.py').write_text('original\n')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        request = self.direct_escalation_request()
        actual = fixture.progress.dispatch.checkpoint
        def lost(*args, **kwargs):
            actual(*args, **kwargs)
            raise OSError('lost response')
        with patch.object(fixture.progress.dispatch, 'checkpoint', side_effect=lost):
            self.invoke('control', request)
        before = next(iter(self.state()['control_transactions'].values()))['request']['evidence']['snapshot']
        (self.flow / 'impl.py').write_text('foreign after intent\n')
        self.assertEqual(self.invoke('control', request)['status'], 'blocked')
        self.assertEqual(next(iter(self.state()['control_transactions'].values()))['request']['evidence']['snapshot'], before)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['implementation_policy']['mode'], 'direct')

    def test_cancelled_direct_cannot_escalate_or_allocate(self):
        self.begin_direct()
        self.invoke('cancel')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        with self.assertRaises(fixture.transfer.entry.PreparationError):
            self.invoke('control', self.direct_escalation_request())
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'cancelled')

    def test_scripted_carrier_cannot_self_authorize_direct_implementation(self):
        self.settings['thread_id'] = 'scripted-runtime'
        self.request['host'].update(thread_id='scripted-runtime', role='scripted-carrier', source_ref='task')
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        with self.assertRaises(fixture.transfer.entry.PreparationError):
            fixture.transfer.handoff.handle(value)
        decision = {'controller_ref': 'task', 'reference': 'controller:local-output',
                    'policy_digest': control.digest(value['semantic']['implementation_policy']), 'receipt': 'host:controller-decision'}
        value['entry']['host']['implementation_decision'] = decision
        with self.assertRaises(fixture.transfer.entry.PreparationError):
            fixture.transfer.handoff.handle(value)
        value['entry']['host'].pop('implementation_decision')
        value['semantic'].pop('implementation_policy')
        value['expected_entry'] = fixture.transfer.entry.resolve(value['entry'])
        saved = fixture.transfer.handoff.handle(value)
        self.assertNotIn('implementation_policy', saved['semantic'])
        value['semantic']['implementation_policy'] = {'mode': 'full'}
        saved = fixture.transfer.handoff.handle(value)
        self.assertEqual(saved['semantic']['implementation_policy'], {'mode': 'full'})

    def test_direct_candidate_fingerprints_add_delete_and_executable_mode(self):
        for name in ('remove.py', 'executable.py'):
            (self.root / name).write_text('baseline\n')
        self.git('add', 'remove.py', 'executable.py'); self.git('commit', '-qm', 'baseline files')
        self.flow_git('merge', '--ff-only', 'main')
        self.context = self.context_for(3)
        value = self.input_for(3)
        locations = ['executable.py', 'impl.py', 'remove.py']
        value['scope']['implementation_paths'] = locations
        value['scope']['owned_paths'] = locations
        value['authorization']['scope_digest'] = control.digest(value['scope'])
        policy = self.direct_policy(value)
        policy['assessment']['implementation_paths'] = locations
        value['semantic']['implementation_policy'] = policy
        self.begin('continuous', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        (self.flow / 'remove.py').unlink()
        (self.flow / 'executable.py').chmod(0o755)
        (self.flow / 'impl.py').write_text('new output\n')
        self.flow_git('add', *locations); self.flow_git('commit', '-qm', 'direct changes')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        result = self.invoke('control', self.direct_candidate_request())
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['state'], 'reviewable', result)
        proof = checkpoint['candidate_evidence']['direct_provenance']
        self.assertEqual(proof['paths'], locations)
        self.assertIsNone(proof['fingerprints']['remove.py'])
        self.assertEqual(set(proof['fingerprints']), set(locations))

    def test_direct_start_rejects_existing_unaccepted_bytes(self):
        (self.flow / 'impl.py').write_text('pre-existing unowned bytes\n')
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        with self.assertRaises(control.ControlError):
            self.invoke('start', {'handoff': value, 'control': {'context': self.context, 'discussion': None}})
        self.assertIsNone(self.state()['control']['context']['carrier'])

    def test_direct_start_rejects_target_already_ahead_of_original_baseline(self):
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        (self.root / 'unrelated.txt').write_text('upstream before direct launch\n')
        self.git('add', 'unrelated.txt')
        self.git('commit', '-qm', 'advance target before dispatcher launch')
        with self.assertRaises((control.ControlError, fixture.transfer.entry.PreparationError)):
            self.begin('continuous', value)

    def test_direct_start_keeps_original_target_if_it_advances_during_admission(self):
        from unittest.mock import patch
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = self.direct_policy(value)
        original_target = self.git('rev-parse', 'main')
        original_transition = fixture.progress.control_git.transition
        advanced = []
        observed = []

        def advance_after_admission(request):
            if request['action'] == 'start-dispatch' and not advanced:
                (self.root / 'unrelated.txt').write_text('upstream during admission\n')
                self.git('add', 'unrelated.txt')
                self.git('commit', '-qm', 'advance target during admission')
                advanced.append(True)
            result = original_transition(request)
            if request['action'] == 'start-dispatch':
                observed.append(result)
            return result

        with patch.object(fixture.progress.control_git, 'transition', side_effect=advance_after_admission):
            with self.assertRaises(fixture.transfer.entry.PreparationError):
                self.begin('continuous', value)
        self.assertEqual(advanced, [True])
        self.assertEqual(len(observed), 1)
        self.assertNotEqual(self.git('rev-parse', 'main'), original_target)
        checkpoint = observed[0]['context']['handoff_progress']
        self.assertEqual(checkpoint['implementation_target_head'], original_target)

    def test_ordinary_full_allocation_cannot_adopt_without_direct_snapshot(self):
        self.begin_dispatcher()
        execution = self.execution_input()
        execution['semantic'].update(adopt_paths=['impl.py'], adoption_snapshot_digest='a' * 64)
        response = self.invoke('allocation', {'allocation_id': 'invalid-adopt', 'operation': 'prepare', 'handoff': execution})
        self.assertEqual(response['status'], 'blocked', response)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['executions'], [])

    def test_direct_cannot_allocate_an_executor_before_escalation(self):
        self.begin_direct()
        execution = self.execution_input()
        response = self.invoke('allocation', {'allocation_id': 'invalid-direct', 'operation': 'prepare', 'handoff': execution})
        self.assertEqual(response['status'], 'blocked', response)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['executions'], [])

    def test_paused_direct_escalates_without_resuming_until_explicit_resume(self):
        self.begin_direct()
        (self.flow / 'impl.py').write_text('retained\n')
        self.invoke('pause')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        result = self.invoke('control', self.direct_escalation_request())
        self.assertEqual(self.state()['status'], 'paused', result)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['implementation_policy'], {'mode': 'full'})
        self.assertNotEqual((result.get('next_action') or {}).get('operation'), 'continue-host')
        resumed = self.invoke('resume')
        self.assertEqual(resumed['next_action']['operation'], 'inspect-host-state', resumed)
        continued = self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        self.assertEqual(continued['next_action']['operation'], 'continue-host', continued)
        self.assertEqual(continued['next_action']['payload']['ref'], 'native:dispatcher')

    def test_direct_target_advance_blocks_business_followup_before_edit(self):
        self.begin_direct()
        (self.root / 'unrelated.txt').write_text('target advanced\n')
        self.git('add', 'unrelated.txt'); self.git('commit', '-qm', 'advance target')
        result = self.invoke('observe', self.observation('idle', 'result',
            {'delivery_id': 'continue-local', 'status': 'continue', 'payload': {'progress': 'next local step'}},
            ref='native:dispatcher'))
        self.assertEqual(result['status'], 'blocked', result)
        self.assertNotEqual((result.get('next_action') or {}).get('operation'), 'continue-host')

    def test_focused_candidate_can_review_but_cannot_complete(self):
        candidate, evidence = self.ready_candidate()
        for axis in ('standards','spec'):
            response = self.invoke('review-activity', {'operation':'prepare','axis':axis,'candidate':candidate,
                'actor_ref':'task','verification':{'candidate':candidate,'plan_digest':evidence['plan_digest'],'checks':evidence['checks']}})
            self.assertEqual(response['next_action']['operation'], 'invoke-review', response)
        self.assertIsNone(self.state()['dispatch']['delivery'])
        review = {axis:{'candidate':candidate,'reviewer_ref':'native:'+axis,'status':'accepted'} for axis in ('standards','spec')}
        payload = {'candidate_commit':candidate,'artifacts':['impl.py'],'checks':['focused'],
                   'review':review,'verification':{'candidate':candidate,'checks':['focused']}}
        result = self.invoke('observe', self.observation('stopped','result',
            {'delivery_id':'premature','status':'completed','payload':payload}, ref='native:dispatcher'))
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(self.state()['dispatch']['delivery'])

    def converge(self, candidate, evidence):
        reviews = {}
        for axis in ('standards', 'spec'):
            prepared = self.invoke('review-activity', {'operation':'prepare','axis':axis,'candidate':candidate,
                'actor_ref':'task','verification':{'candidate':candidate,'plan_digest':evidence['plan_digest'],'checks':evidence['checks']}})
            receipt = {'adapter':'fixture-native','call_ref':'call:'+axis,'response_ref':'response:'+axis,
                       'status':'stopped','ref':'native:'+axis,'raw':{'result':'accepted'}}
            self.invoke('review-activity', {'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task',
                        'action_id':prepared['next_action']['action_id'],'receipt':receipt})
            reviews[axis] = {'candidate':candidate,'expected_target_head':evidence['expected_target_head'],
                'plan_digest':evidence['plan_digest'],'reviewer_ref':'native:'+axis,'status':'accepted',
                'result_ref':'response:'+axis}
        result = self.invoke('control', {'action':'review-converged',
            'evidence':{'candidate':candidate,'review':reviews,'reference':'controller:converged'},
            'receipt':{'adapter':'fixture-controller','call_ref':'review','response_ref':'review-accepted','raw':{'review':reviews}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'final-validation-pending', result)
        return reviews

    def start_final(self):
        candidate, evidence = self.ready_candidate()
        reviews = self.converge(candidate, evidence)
        checkpoint = self.state()['control']['context']['handoff_progress']
        start = {'attempt_id':'final-1','dispatcher_ref':'native:dispatcher',
                 'carrier_attempt':self.state()['control']['context']['carrier']['attempt'],
                 'candidate':candidate,'expected_target_head':evidence['expected_target_head'],
                 'plan_digest':evidence['plan_digest'],'review_digest':checkpoint['review_digest']}
        response = self.invoke('control', {'action':'validation-start','evidence':start,
            'receipt':{'adapter':'fixture-controller','call_ref':'start','response_ref':'start-approved','raw':{'decision':'run final'}}})
        self.assertIsNotNone(response['next_action'], response)
        self.assertEqual(response['next_action']['operation'], 'continue-host', response)
        self.assertEqual(response['next_action']['payload']['ref'], 'native:dispatcher')
        self.assertEqual(self.state()['control']['context']['handoff_progress']['attempts'][-1]['attempt_id'], 'final-1')
        return candidate

    def complete_final(self, status='passed'):
        candidate = self.start_final()
        checks = [dict(item, status=status, exit_code=0 if status == 'passed' else 1, start_commit=candidate, end_commit=candidate,
                       environment_fingerprint=None, output_ref='fixture:full', output_digest='b'*64)
                  for item in self.plan()['final_required']]
        result = self.invoke('control', {'action':'validation-result','evidence':{'attempt_id':'final-1','checks':checks},
            'receipt':{'adapter':'fixture-controller','call_ref':'full-command','response_ref':'full-observed',
                       'raw':{'attempt_id':'final-1','checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'deliverable' if status == 'passed' else 'final-validation-failed', result)
        self.assertIsNone(self.state()['dispatch']['delivery'])
        return candidate

    def test_final_validation_requires_convergence_and_exact_complete_results(self):
        self.complete_final()

    def test_failed_attempt_retry_retains_original_review_and_failure(self):
        candidate = self.complete_final('failed')
        self.invoke('observe', self.observation('idle','result',ref='native:dispatcher'))
        checkpoint = self.state()['control']['context']['handoff_progress']
        review_digest = checkpoint['review_digest']
        failed = copy.deepcopy(checkpoint['attempts'][0])
        request = {'action':'validation-retry','evidence':{'attempt_id':'final-1','reference':'controller:retry'},
                   'receipt':{'adapter':'fixture-controller','call_ref':'retry','response_ref':'retry-approved','raw':{'reason':'retry'}}}
        result = self.invoke('control', request)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'final-validation-pending',result)
        start = {k:failed[k] for k in ('dispatcher_ref','carrier_attempt','candidate','expected_target_head','plan_digest','review_digest')}
        start['attempt_id'] = 'final-2'
        result = self.invoke('control', {'action':'validation-start','evidence':start,
            'receipt':{'adapter':'fixture-controller','call_ref':'retry-start','response_ref':'retry-start-approved','raw':{'decision':'run'}}})
        self.assertEqual(result['next_action']['operation'],'continue-host',result)
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        result_request = {'action':'validation-result','evidence':{'attempt_id':'final-2','checks':checks},
            'receipt':{'adapter':'fixture-controller','call_ref':'retry-command','response_ref':'retry-result',
                'raw':{'attempt_id':'final-2','checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}}
        self.invoke('control',result_request)
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['review_digest'],review_digest)
        self.assertEqual([v['state'] for v in checkpoint['attempts']],['failed','passed'])
        self.assertEqual(checkpoint['attempts'][0]['checks'],failed['checks'])
        self.assertTrue(self.invoke('control',result_request)['acknowledged'])
        changed = copy.deepcopy(result_request)
        changed['evidence']['checks'][0]['output_digest'] = 'c'*64
        changed['receipt']['raw']['checks'] = changed['evidence']['checks']
        self.assertEqual(self.invoke('control',changed)['status'],'blocked')

    def test_review_remediation_invalidates_current_delivery_and_keeps_history(self):
        candidate, evidence = self.ready_candidate()
        self.converge(candidate, evidence)
        result = self.invoke('control', {'action':'invalidate-candidate',
            'evidence':{'candidate':candidate,'reference':'controller:remediate','reason':'review follow-up'},
            'receipt':{'adapter':'fixture-controller','call_ref':'remediate','response_ref':'remediate-approved','raw':{'decision':'fix'}}})
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['state'], 'implementing', result)
        self.assertIsNone(checkpoint['candidate'])
        self.assertEqual(checkpoint['validation_history'][0]['candidate'], candidate)

    def test_target_advance_rejects_closure_with_stale_validation(self):
        self.complete_final()
        checkpoint = self.state()['control']['context']['handoff_progress']
        verification = control.delivery_verification(checkpoint, 'native:dispatcher')
        (self.root / 'unrelated.txt').write_text('new target\n')
        self.git('add', 'unrelated.txt'); self.git('commit', '-qm', 'advance target')
        import workflow_control_git as adapter
        context = self.context_for(4)
        request = {'schema_version':context['schema_version'],'action':'start-closure','actor_ref':'task','context':context,
            'evidence':{'candidate':checkpoint['candidate'],'dispatcher_ref':'native:dispatcher',
                'review':checkpoint['review_decision']['review'],'verification':verification,'binding':self.binding,
                'implementation_paths':['impl.py'],'closure_paths':['CHANGELOG.md'],
                'protected_paths':[self.requirement_path],'configuration':fixture.transfer.configuration('closure-agent')}}
        with self.assertRaisesRegex(control.ControlError, 'target_changed'):
            adapter.verified_transition({'repository':str(self.flow),'baseline':self.state()['handoff']['scope']['baseline'],'request':request})

    def test_legacy_control_is_rejected_without_mutating_input(self):
        old = self.context_for(3)
        old['schema_version'] = 1
        before = copy.deepcopy(old)
        with self.assertRaisesRegex(control.ControlError, 'legacy_run_requires_original_runtime'):
            control.transition({'schema_version':1,'actor_ref':'task','action':'cancel','context':old,'evidence':{}})
        self.assertEqual(old, before)

    def test_strict_delivery_rejects_each_corrupted_result(self):
        self.complete_final()
        checkpoint = self.state()['control']['context']['handoff_progress']
        original = control.delivery_verification(checkpoint,'native:dispatcher')
        review = checkpoint['review_decision']['review']
        mutations = [lambda v:v.update(plan_digest='f'*64), lambda v:v.update(candidate='f'*40),
            lambda v:v['attempts'][-1].update(checks=[]),
            lambda v:v['attempts'][-1]['checks'].append(copy.deepcopy(v['attempts'][-1]['checks'][0])),
            lambda v:v['attempts'][-1]['checks'][0].update(command='true'),
            lambda v:v['attempts'][-1]['checks'][0].update(category='focused'),
            lambda v:v['attempts'][-1]['checks'][0].update(status='skipped'),
            lambda v:v['attempts'][-1]['checks'][0].update(status='unknown'),
            lambda v:v['attempts'][-1]['checks'][0].update(end_commit='f'*40),
            lambda v:v['attempts'][-1]['source']['raw'].update(source_unchanged=False),
            lambda v:v['attempts'][-1]['source']['raw'].update(dispatcher_ref='other'),
            lambda v:v['attempts'].append(dict(v['attempts'][-1],attempt_id='newer',state='failed'))]
        for mutate in mutations:
            value = copy.deepcopy(original); mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(control.ControlError):
                control.validate_review(checkpoint['candidate'],review,value,'native:dispatcher')

    def test_replacement_after_merging_target_requires_fresh_reviews(self):
        candidate, evidence = self.ready_candidate()
        self.converge(candidate,evidence)
        self.invoke('control', {'action':'invalidate-candidate',
            'evidence':{'candidate':candidate,'reference':'controller:new-target','reason':'target advanced'},
            'receipt':{'adapter':'fixture','call_ref':'invalidate','response_ref':'invalidated','raw':{'decision':'merge target'}}})
        (self.root/'unrelated.txt').write_text('new target\n')
        self.git('add','unrelated.txt'); self.git('commit','-qm','advance target')
        self.flow_git('merge','--no-edit','main')
        replacement = self.flow_git('rev-parse','HEAD')
        current = self.state()['control']['context']
        ready = dict(evidence, commit=replacement, expected_target_head=self.git('rev-parse','main'),
                     checks=fixture.transfer.checks_for(self.plan()['review_required'],replacement))
        result = self.invoke('control',{'action':'candidate-ready','evidence':ready,
            'receipt':{'adapter':'fixture','call_ref':'focused-again','response_ref':'focused-again','raw':{'checks':ready['checks'],'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}})
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['state'],'reviewable',result)
        self.assertNotIn('review_digest',checkpoint)
        self.assertEqual(checkpoint['candidate'],replacement)

    def test_unknown_validation_followup_is_not_reissued(self):
        self.start_final()
        original = copy.deepcopy(self.state()['action'])
        response = self.invoke('advance')
        self.assertEqual(response['next_action']['operation'],'lookup-exact-action')
        self.assertEqual(self.state()['action'],original)
        self.assertEqual(len(self.state()['control']['context']['handoff_progress']['attempts']),1)

    def test_late_validation_result_during_pause_is_retained_without_delivery(self):
        candidate = self.start_final()
        self.invoke('pause')
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        request = {'action':'validation-result','evidence':{'attempt_id':'final-1','checks':checks},
            'receipt':{'adapter':'fixture','call_ref':'command','response_ref':'late-result',
                       'raw':{'attempt_id':'final-1','checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':'native:dispatcher'}}}
        response = self.invoke('control',request)
        self.assertEqual(response['next_action']['operation'],'reconcile-validation-result')
        self.assertEqual(self.state()['deferred_validation'],request)
        replayed = copy.deepcopy(request)
        replayed['evidence']['source'] = copy.deepcopy(request['receipt'])
        self.invoke('control',replayed)
        self.assertEqual(self.state()['deferred_validation'],request)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'validating')
        self.assertIsNone(self.state()['dispatch']['delivery'])
        self.invoke('cancel')
        self.invoke('control',request)
        self.assertNotEqual(self.state()['control']['context']['handoff_progress']['state'],'deliverable')

    def test_validation_attempt_survives_crash_before_followup_without_duplicate(self):
        from unittest.mock import patch
        candidate, evidence = self.ready_candidate()
        self.converge(candidate,evidence)
        checkpoint = self.state()['control']['context']['handoff_progress']
        request = {'action':'validation-start','evidence':{'attempt_id':'crash-final','dispatcher_ref':'native:dispatcher',
            'carrier_attempt':self.state()['control']['context']['carrier']['attempt'],
            **{k:checkpoint[k] for k in ('candidate','expected_target_head','plan_digest','review_digest')}},
            'receipt':{'adapter':'fixture','call_ref':'start','response_ref':'start-approved','raw':{'decision':'run'}}}
        original = fixture.progress.atomic_save
        def interrupted(path, value):
            original(path,value)
            state = value.get(fixture.progress.KEY,{})
            if state.get('step') == 'continue' and (state.get('continuation_intent') or {}).get('kind') == 'final-validation':
                raise KeyboardInterrupt()
        with patch.object(fixture.progress,'atomic_save',side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('control',request)
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(len(checkpoint['attempts']),1)
        self.assertEqual(checkpoint['attempts'][0]['state'],'running')
        resumed = self.invoke('advance')
        self.assertEqual(resumed['next_action']['operation'],'continue-host',resumed)
        action = self.state()['action']
        self.assertEqual(self.invoke('advance')['next_action']['operation'],'lookup-exact-action')
        self.assertEqual(self.state()['action'],action)

    def test_old_checks_cannot_bypass_direct_b_or_runner(self):
        candidate, evidence = self.ready_candidate()
        state = self.state()
        review = {axis:{'candidate':candidate,'reviewer_ref':axis,'status':'accepted'} for axis in ('standards','spec')}
        verification = {'candidate':candidate,'checks':['full passed']}
        payload = {'artifacts':['impl.py'],'checks':['full passed'],'candidate_commit':candidate,'review':review,'verification':verification}
        with self.assertRaises(control.ControlError):
            fixture.transfer.dispatch.handle({'protocol':fixture.transfer.handoff.PROTOCOL,'operation':'receive',
                'record':state['dispatch'],'control':state['control'],
                'receipt':self.receipt(state['dispatch'],'stopped','result','native:dispatcher'),
                'result':{'delivery_id':'premature-direct','status':'completed','payload':payload}})
        handoff = dict(payload,binding=self.binding,implementation_paths=['impl.py'],closure_paths=['CHANGELOG.md'],
                       protected_paths=[self.requirement_path],controller_ref='task',role_ref='native:dispatcher',
                       control_checkpoint={'controller_ref':'task','stage':3,'role_ref':'native:dispatcher','state':'completed','role_kind':'implementation-dispatcher'})
        with self.assertRaises(fixture.runner.WorkflowError):
            fixture.runner._require_handoff('stage3',{'handoff':handoff})
        self.assertIsNone(self.state()['dispatch']['delivery'])

    def test_old_runner_progress_and_transfer_records_are_not_migrated(self):
        self.begin_dispatcher()
        state = fixture.progress.read_record(self.checkpoint)
        state[fixture.progress.KEY]['protocol'] = 'workflow-progress-v6'
        fixture.progress.atomic_save(self.checkpoint,state)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            fixture.progress.handle(self.checkpoint,{'protocol':fixture.progress.PROTOCOL,'operation':'inspect','expected_revision':state[fixture.progress.KEY]['revision']})
        self.assertEqual(error.exception.code,'legacy_run_requires_original_runtime')
        self.assertEqual(self.checkpoint.read_bytes(),before)
        for version in (1,2,3,4):
            original = {'version':version,'confirmed':{'packages':{}},'sentinel':'unchanged'}
            with self.assertRaisesRegex(fixture.runner.WorkflowError,'legacy_run_requires_original_runtime'):
                fixture.runner._validate_record(original)
            self.assertEqual(original,{'version':version,'confirmed':{'packages':{}},'sentinel':'unchanged'})
        record = fixture.transfer.handoff.unseal(state[fixture.progress.KEY]['dispatch'])
        record['protocol'] = 'workflow-stage-transfer-v2'
        sealed = fixture.transfer.handoff.seal(record)
        with self.assertRaises(fixture.transfer.entry.PreparationError) as error:
            fixture.transfer.dispatch.record_for({'record':sealed,'control':state[fixture.progress.KEY]['control']})
        self.assertEqual(error.exception.code,'legacy_run_requires_original_runtime')

    def final_result_request(self, checks, response='final-observation'):
        return {'action':'validation-result','evidence':{'attempt_id':'final-1','checks':checks},
            'receipt':{'adapter':'fixture-controller','call_ref':'final-command','response_ref':response,
                       'raw':{'attempt_id':'final-1','checks':checks,'source_unchanged':True,'stopped':True,
                              'dispatcher_ref':'native:dispatcher'}}}

    def test_malformed_check_id_is_rejected_without_poisoning_recovery(self):
        candidate = self.start_final()
        self.invoke('observe',self.observation('idle','result',ref='native:dispatcher'))
        for invalid in ([], {}, None, '', 3):
            with self.subTest(invalid=invalid):
                checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
                checks[0]['id'] = invalid
                response = self.invoke('control',self.final_result_request(checks,str(invalid)))
                self.assertEqual(response['status'],'blocked')
                unresolved = [v for v in self.state()['control_transactions'].values()
                              if v['result'] is None and v.get('rejection') is None]
                self.assertEqual(unresolved,[])
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        self.invoke('control',self.final_result_request(checks))
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'deliverable')

    def test_unknown_control_result_blocks_new_decisions_until_exact_reconciliation(self):
        from unittest.mock import patch
        candidate = self.start_final()
        self.invoke('observe',self.observation('idle','result',ref='native:dispatcher'))
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        original = fixture.progress.dispatch.checkpoint
        def lose_response(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('injected response loss after control result')
        with patch.object(fixture.progress.dispatch,'checkpoint',side_effect=lose_response):
            self.assertEqual(self.invoke('control',self.final_result_request(checks))['status'],'blocked')
        context = copy.deepcopy(self.state()['control']['context'])
        progress = context['handoff_progress']
        sibling_requests = [('review-activity',{'operation':'prepare','axis':'standards','candidate':candidate,'actor_ref':'task',
            'verification':{'candidate':candidate,'plan_digest':progress['plan_digest'],'checks':progress['tests']}}),
            ('allocation',{'allocation_id':'blocked','operation':'prepare','handoff':{}}),
            ('publication',{'candidate_commit':candidate,'reference':'blocked'})]
        for operation,data in sibling_requests:
            with self.subTest(operation=operation):
                response = self.invoke(operation,data)
                self.assertEqual(response['error']['code'],'control_outcome_unknown')
                self.assertEqual(self.state()['control']['context'],context)
        invalidate = {'action':'invalidate-candidate',
            'evidence':{'candidate':candidate,'reference':'controller:invalidate','reason':'replacement'},
            'receipt':{'adapter':'fixture','call_ref':'invalidate','response_ref':'invalidated','raw':{'stopped':True}}}
        rejected = self.invoke('control',invalidate)
        self.assertEqual(rejected['error']['code'],'control_outcome_unknown')
        self.assertEqual(self.state()['control']['context'],context)
        unresolved = [v for v in self.state()['control_transactions'].values()
                      if v['result'] is None and v.get('rejection') is None]
        self.assertEqual(len(unresolved),1)
        self.invoke('resume')
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'deliverable')
        self.invoke('control',invalidate)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'implementing')
        self.invoke('resume')
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'implementing')

    def configure_environment_plan(self, condition='accepted'):
        original_plan = self.plan
        def plan():
            value = original_plan()
            value['final_required'].append({'id':'lifecycle','category':'environment','command':'Verify native lifecycle and stopped identities',
                'cwd':str(self.flow),'pass_condition':condition,'allowed_skips':[],'environment':'fixture-host-identity'})
            value['environment_not_applicable'] = None
            return value
        self.plan = plan

    def test_environment_program_acceptance_does_not_fabricate_exit_code(self):
        self.configure_environment_plan()
        candidate = self.start_final()
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        checks[-1].update(exit_code=None,environment_fingerprint='fixture-host-identity')
        response = self.invoke('control',self.final_result_request(checks))
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['state'],'deliverable',response)
        control.validate_review(candidate,checkpoint['review_decision']['review'],
            control.delivery_verification(checkpoint,'native:dispatcher'),'native:dispatcher')

    def test_malformed_adjacent_validation_collections_raise_control_errors(self):
        self.complete_final()
        checkpoint = self.state()['control']['context']['handoff_progress']
        verification = control.delivery_verification(checkpoint,'native:dispatcher')
        for invalid in ([], {}, None, '', 3):
            malformed = copy.deepcopy(verification)
            malformed['attempts'].insert(0,{'attempt_id':invalid})
            with self.subTest(field='attempt_id', invalid=invalid), self.assertRaises(control.ControlError):
                control.validate_review(checkpoint['candidate'],checkpoint['review_decision']['review'],malformed,'native:dispatcher')
            plan = self.plan()
            plan['final_required'][0]['category'] = invalid
            with self.subTest(field='category', invalid=invalid), self.assertRaises(control.ControlError):
                control.validate_validation_plan(plan,self.binding)
            checks = fixture.transfer.checks_for(self.plan()['final_required'],checkpoint['candidate'])
            checks[0]['status'] = invalid
            with self.subTest(field='status', invalid=invalid), self.assertRaises(control.ControlError):
                control.validate_checks(self.plan()['final_required'],checks,checkpoint['candidate'])

    def test_environment_command_still_requires_its_frozen_exit_condition(self):
        self.configure_environment_plan('exit 0')
        candidate = self.start_final()
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        checks[-1].update(exit_code=None,environment_fingerprint='fixture-host-identity')
        self.invoke('control',self.final_result_request(checks))
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'final-validation-failed')
        self.assertIsNone(self.state()['dispatch']['delivery'])

    def test_environment_acceptance_rejects_wrong_fingerprint_and_unknown_verdict(self):
        self.configure_environment_plan()
        candidate = self.start_final()
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        checks[-1].update(exit_code=None,environment_fingerprint='wrong-host')
        self.assertEqual(self.invoke('control',self.final_result_request(checks,'wrong-fingerprint'))['status'],'blocked')
        checks[-1].update(status='unknown',environment_fingerprint='fixture-host-identity')
        self.invoke('control',self.final_result_request(checks,'unknown-verdict'))
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'final-validation-failed')
        self.assertIsNone(self.state()['dispatch']['delivery'])

    def test_definitive_sibling_control_rejection_does_not_become_unknown(self):
        candidate = self.start_final()
        self.invoke('observe',self.observation('idle','result',ref='native:dispatcher'))
        rejected = self.invoke('control', {'action':'recover-dispatch','evidence':{
            'stopped_refs':[],'file_hashes':{},'replacement_ref':'native:dispatcher'},
            'receipt':{'adapter':'fixture','raw':{'stopped':False}}})
        self.assertEqual(rejected['status'],'blocked')
        pending = [v for v in self.state()['control_transactions'].values()
                   if v['result'] is None and v.get('rejection') is None]
        self.assertEqual(pending,[])
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        self.invoke('control',self.final_result_request(checks))
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'deliverable')

    def pause_after_lost_validation_result(self):
        from unittest.mock import patch
        candidate = self.start_final()
        self.invoke('observe',self.observation('idle','result',ref='native:dispatcher'))
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        original = fixture.progress.dispatch.checkpoint
        def lose_response(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('injected response loss')
        with patch.object(fixture.progress.dispatch,'checkpoint',side_effect=lose_response):
            self.invoke('control',self.final_result_request(checks))
        self.invoke('pause')
        self.invoke('observe',self.observation('stopped','result',ref='native:dispatcher'))
        self.assertEqual(self.state()['status'],'paused')
        return candidate

    def test_paused_unknown_control_result_reconciles_after_explicit_resume(self):
        candidate = self.pause_after_lost_validation_result()
        resumed = self.invoke('resume')
        self.assertNotEqual(resumed['status'],'paused')
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'deliverable')
        self.assertIsNone(self.state()['dispatch']['delivery'])
        self.finish_resumed_validation_delivery(resumed, candidate)

    def finish_resumed_validation_delivery(self, response, candidate):
        if response['next_action']['operation'] == 'control-effects':
            response = self.invoke('advance')
        if response['next_action']['operation'] == 'inspect-host-state':
            # Stop proof is insufficient to resume; the original host must now
            # authenticate this same identity as idle/resumable.
            response = self.invoke('observe',self.observation('idle','result',ref='native:dispatcher'))
        self.assertEqual(response['next_action']['operation'],'continue-host',response)
        self.assertEqual(response['next_action']['payload']['ref'],'native:dispatcher')
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(len(checkpoint['attempts']),1)
        payload = {'candidate_commit':candidate,'artifacts':['impl.py'],'checks':['final validation passed'],
            'review':checkpoint['review_decision']['review'],
            'verification':control.delivery_verification(checkpoint,'native:dispatcher')}
        response = self.invoke('observe',self.observation('stopped','result',
            {'delivery_id':'resumed-final-delivery','status':'completed','payload':payload},ref='native:dispatcher'))
        self.assertEqual(response['status'],'needs_input',response)
        pending = response['pending']
        accepted = self.invoke('decide',{'decision_id':pending['decision_id'],'subject':pending['subject'],
            'answer':'accept','reference':'controller:accept-resumed-delivery'})
        self.assertEqual(accepted['status'],'accepted',accepted)
        self.assertTrue(accepted['downstream_ready'])
        self.assertEqual(self.state()['accepted']['role_ref'],'native:dispatcher')
        self.assertEqual(self.state()['accepted']['payload']['candidate_commit'],candidate)
        self.assertEqual(self.flow_git('rev-parse','HEAD'),candidate)

    def test_resume_delivery_intent_survives_unpause_write_interruption(self):
        from unittest.mock import patch
        candidate = self.pause_after_lost_validation_result()
        original = fixture.progress.atomic_save
        def crash_after_unpause(path, value):
            original(path,value)
            state = value.get(fixture.progress.KEY,{})
            if state.get('status') == 'active' and state.get('resume_intent'):
                raise KeyboardInterrupt()
        with patch.object(fixture.progress,'atomic_save',side_effect=crash_after_unpause):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('resume')
        self.assertEqual(self.state()['step'],'continue')
        self.assertEqual(self.state()['resume_intent']['operation'],'resume-original-paused-scope')
        self.finish_resumed_validation_delivery(self.invoke('resume'),candidate)

    def test_duplicate_deferred_validation_result_resumes_to_actual_delivery(self):
        candidate = self.complete_final()
        self.invoke('observe',self.observation('idle','result',ref='native:dispatcher'))
        request = next(value['request'] for value in self.state()['control_transactions'].values()
                       if value['request']['action'] == 'validation-result')
        self.invoke('pause')
        self.invoke('observe',self.observation('stopped','result',ref='native:dispatcher'))
        self.invoke('control',request)
        self.assertIn('deferred_validation',self.state())
        response = self.invoke('resume')
        self.assertNotIn('deferred_validation',self.state())
        self.finish_resumed_validation_delivery(response,candidate)

    def test_paused_validation_start_reconciliation_issues_only_original_followup(self):
        from unittest.mock import patch
        candidate,evidence = self.ready_candidate()
        self.converge(candidate,evidence)
        context = self.state()['control']['context']
        start = {'attempt_id':'final-1','dispatcher_ref':'native:dispatcher','carrier_attempt':context['carrier']['attempt'],
                 **{key:context['handoff_progress'][key] for key in ('candidate','expected_target_head','plan_digest','review_digest')}}
        original = fixture.progress.dispatch.checkpoint
        def lose_response(*args, **kwargs):
            original(*args,**kwargs)
            raise OSError('injected validation-start response loss')
        with patch.object(fixture.progress.dispatch,'checkpoint',side_effect=lose_response):
            self.invoke('control',{'action':'validation-start','evidence':start,
                'receipt':{'adapter':'fixture','call_ref':'start','response_ref':'start-approved','raw':{'decision':'run'}}})
        self.invoke('pause')
        self.invoke('observe',self.observation('stopped','result',ref='native:dispatcher'))
        response = self.invoke('resume')
        if response['next_action']['operation'] == 'inspect-host-state':
            response = self.invoke('observe',self.observation('idle','result',ref='native:dispatcher'))
        self.assertEqual(response['next_action']['operation'],'continue-host',response)
        action = copy.deepcopy(self.state()['action'])
        self.assertEqual(self.invoke('advance')['next_action']['operation'],'lookup-exact-action')
        self.assertEqual(self.state()['action'],action)
        checks = fixture.transfer.checks_for(self.plan()['final_required'],candidate)
        self.invoke('control',self.final_result_request(checks))
        self.finish_resumed_validation_delivery(response,candidate)

    def test_resumed_validation_waits_for_actual_same_host_resumability(self):
        self.pause_after_lost_validation_result()
        resumed = self.invoke('resume')
        self.assertEqual(resumed['next_action']['operation'],'inspect-host-state',resumed)
        negative = self.invoke('observe',self.observation('stopped','result',ref='native:dispatcher'))
        self.assertEqual(negative['next_action']['operation'],'await-host-recovery',negative)
        query = copy.deepcopy(self.state()['host']['query'])
        self.assertEqual(self.invoke('advance')['next_action']['operation'],'await-host-recovery')
        self.assertEqual(self.state()['host']['query'],query)
        self.assertIsNone(self.state()['dispatch']['delivery'])
        self.assertEqual(self.state()['control']['context']['carrier']['ref'],'native:dispatcher')


# unittest otherwise inherits the unrelated several-hundred-check fixture inventory.
for _name in dir(fixture.ProgressTests):
    if _name.startswith('test_') and _name not in ReviewFirstTests.__dict__:
        setattr(ReviewFirstTests, _name, None)
