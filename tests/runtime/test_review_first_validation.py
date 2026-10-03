"""Review-first behavior through C, real B/control and temporary Git."""
import copy
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixture
import workflow_control as control
import workflow_control_git as control_git
import execution_plan
import test_stage_transfer as transfer


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

    def _revision_carry_and_adopt(self, storage, *, advance_target=False):
        self.context = self.context_for(3)
        value = self.input_for(3)
        scope = value['scope']
        scope['owned_paths'] = scope['implementation_paths'] = ['impl.py', 'other.py']
        scope['downstream']['manifest'].append({
            'path': 'other.py', 'owner': 'implementation', 'kind': 'source',
            'operation': 'add', 'basis': scope['downstream']['manifest'][1]['basis']})
        plan = value['semantic']['inline_plan']
        plan['verifications'].append({'id': 'V-other', 'behavior': 'other behavior',
            'seam': 'real C/B/Git fixture', 'commands': [
                'python3 scripts/run_tests.py --test-file tests/runtime/test_review_first_validation.py']})
        other = copy.deepcopy(plan['steps'][0])
        other.update(id='inline-other', target={'path': 'other.py', 'symbol': 'run', 'operation': 'add'},
                     verification_refs=['V-other'])
        plan['steps'].append(other)
        value['authorization']['scope_digest'] = transfer.entry.digest(scope)
        self.begin('continuous', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))

        execution = self.execution_input()
        prepared = self.invoke('allocation', {'allocation_id': 'unchanged', 'operation': 'prepare',
                                               'handoff': execution})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = self.state()['allocations']['unchanged']
        self.invoke('allocation', {'allocation_id': 'unchanged', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:executor')})
        data = b"print('accepted')\n"
        (self.flow / 'impl.py').write_bytes(data)
        slot = self.state()['allocations']['unchanged']
        self.invoke('allocation', {'allocation_id': 'unchanged', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
            'result': {'delivery_id': 'unchanged-result', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                'tests': ['real allocation fixture passed'],
                'write_release': transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.invoke('allocation', {'allocation_id': 'unchanged', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accepted'}})
        second = self.execution_input()
        second['scope']['owned_paths'] = ['other.py']
        second['semantic']['plan_refs'] = [ref for ref in [
            execution_plan.step_ref(ticket, step)
            for ticket in self.state()['scope_authority']['execution_plan']['catalog']['tickets']
            for step in ticket['plan']['steps']] if ref['step_id'] == 'inline-other']
        second['authorization']['scope_digest'] = transfer.entry.digest(second['scope'])
        prepared = self.invoke('allocation', {'allocation_id': 'affected-old', 'operation': 'prepare',
                                               'handoff': second})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = self.state()['allocations']['affected-old']
        self.invoke('allocation', {'allocation_id': 'affected-old', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:other-executor')})
        other_data = b"print('needs revalidation')\n"
        (self.flow / 'other.py').write_bytes(other_data)
        slot = self.state()['allocations']['affected-old']
        self.invoke('allocation', {'allocation_id': 'affected-old', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:other-executor'),
            'result': {'delivery_id': 'affected-old-result', 'status': 'completed', 'payload': {
                'changed_paths': ['other.py'],
                'file_hashes': {'other.py': hashlib.sha256(other_data).hexdigest()},
                'tests': ['real allocation fixture passed'],
                'write_release': transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.invoke('allocation', {'allocation_id': 'affected-old', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:other-accepted'}})
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))

        old = self.state()['scope_authority']
        revised = copy.deepcopy(plan)
        revised['plan_version'] = 2
        revised['steps'][1]['approach'] = ['changed private implementation for other.py']
        report = {'ticket_ids': ['INLINE-TRANSFER'], 'step_ids': ['inline-other'],
            'reason': 'Revise the other private function', 'production_facts': 'Other function needs a new approach',
            'contract_before': old['execution_plan']['contract_digest'],
            'contract_after': old['execution_plan']['contract_digest'],
            'dependency_impact': ['inline-other'], 'verification_impact': ['V-other'],
            'carry_forward': ['inline-implementation']}
        request = {'revision_id': 'other-private-2', 'expected_authority_revision': old['revision'],
            'expected_authority_digest': transfer.entry.digest(old), 'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:other-private-2'},
            'report': report, 'planning_paths': [], 'inline_plan': revised}
        prepared = self.invoke('prepare-plan-revision', request)
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        released = prepared['next_action']['write_release']
        activated = self.invoke('commit-plan-revision', {'revision_id': 'other-private-2',
            'controller_ref': 'task', 'write_release': released,
            'planning_commit': self.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated', activated)
        current = self.state()['control']['context']['handoff_progress']
        self.assertEqual(current['plan_authority']['revision'], old['revision'] + 1)
        self.assertEqual([item['agent_ref'] for item in current['carried_results']], ['native:executor'])
        self.assertEqual(current['revalidation_source']['pending_paths'], ['other.py'])
        forged = copy.deepcopy(self.state()['control']['context'])
        forged['handoff_progress']['carried_results'][0]['file_hashes']['impl.py'] = '0' * 64
        with self.assertRaises(control.ControlError):
            control.validate_context(forged)
        owner = fixture.progress.Progress(self.checkpoint, fixture.progress.read_record(self.checkpoint))
        self.assertTrue({'native:executor', 'native:other-executor'} <= owner.native_refs())
        self.assertEqual(owner.finalization_owner('native:executor')[0], 'native:dispatcher')
        active = self.observation('stopped', 'result', ref='native:dispatcher')['lifecycle']
        active['pages'][0]['response']['data'].extend([
            {'id': 'native:executor', 'status': {'type': 'idle'}},
            {'id': 'native:other-executor', 'status': {'type': 'active'}}])
        self.invoke('lifecycle-state', active)
        pending = fixture.progress.Progress(self.checkpoint,
            fixture.progress.read_record(self.checkpoint)).stop_barrier_pending()
        self.assertTrue(any(item['kind'] == 'native-turn-not-stopped' and
                            item['thread'] == 'native:other-executor' for item in pending), pending)
        stopped = self.observation('stopped', 'result', ref='native:dispatcher')['lifecycle']
        stopped['pages'][0]['response']['data'].extend([
            {'id': 'native:executor', 'status': {'type': 'idle'}},
            {'id': 'native:other-executor', 'status': {'type': 'idle'}}])
        self.invoke('lifecycle-state', stopped)
        pending = fixture.progress.Progress(self.checkpoint,
            fixture.progress.read_record(self.checkpoint)).stop_barrier_pending()
        self.assertFalse(any(item['kind'] in {'unreconciled-descendants',
            'native-turn-not-stopped'} for item in pending), pending)
        unknown = copy.deepcopy(stopped)
        unknown['pages'][0]['response']['data'].append(
            {'id': 'native:unbound', 'status': {'type': 'idle'}})
        self.invoke('lifecycle-state', unknown)
        pending = fixture.progress.Progress(self.checkpoint,
            fixture.progress.read_record(self.checkpoint)).stop_barrier_pending()
        self.assertTrue(any(item['kind'] == 'unreconciled-descendants' for item in pending), pending)
        self.invoke('lifecycle-state', stopped)
        if storage != 'dirty':
            self.flow_git('add', 'impl.py', 'other.py')
        if storage == 'committed':
            self.flow_git('commit', '-qm', 'candidate bytes from original agents')
        target = self.git('rev-parse', 'main')
        def controller_host():
            self.settings['thread_id'] = 'task'
            self.request['host'].update(thread_id='task', role='controller', source_ref=None)
            self.request['host'].pop('actor_ref', None)
        controller_host()
        self.invoke('advance')
        self.invoke('observe', self.observation('running', 'result', ref='native:dispatcher'))
        def candidate_request(call_ref):
            candidate = self.flow_git('rev-parse', 'HEAD')
            checks = fixture.transfer.checks_for(self.plan()['review_required'], candidate)
            evidence = {'dispatcher_ref': 'native:dispatcher',
                'attempt': self.state()['control']['context']['carrier']['attempt'],
                'commit': candidate, 'expected_target_head': target, 'binding': self.binding,
                'plan_digest': control.digest(self.plan()), 'checks': checks}
            return {'action': 'candidate-ready', 'evidence': copy.deepcopy(evidence),
                'receipt': {'adapter': 'fixture-controller', 'call_ref': call_ref,
                    'response_ref': call_ref + ':result', 'raw': {'checks': checks,
                        'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': 'native:dispatcher'}}}
        if storage == 'committed':
            progress = self.state()['control']['context']['handoff_progress']
            catalog = execution_plan.current_catalog(progress['execution_authority'])
            accepted = progress['carried_results'] + [execution for execution in progress['executions']
                if execution['state'] == 'accepted' and execution['agent_ref'] is not None]
            fingerprints = {relative: control_git.file_fingerprint(self.flow, relative)
                            for relative in progress['allowed_paths']}
            self.assertEqual(execution_plan.current_accepted_steps(
                catalog, accepted, progress['execution_authority'], fingerprints),
                {'inline-implementation'})
            with self.assertRaises(control.ControlError) as stale_result:
                control_git.verified_transition({'repository': str(self.flow),
                    'baseline': current['git_baseline_commit'],
                    'request': {'schema_version': 6, 'action': 'candidate-ready',
                        'actor_ref': 'task', 'context': copy.deepcopy(self.state()['control']['context']),
                        'evidence': candidate_request('before-fresh-adoption')['evidence']}})
            self.assertIn('current function step: inline-other', str(stale_result.exception))
        if advance_target:
            (self.root / 'impl.py').write_text("print('upstream sibling')\n")
            self.git('add', 'impl.py')
            self.git('commit', '-qm', 'advance other authorized path on target')
        adoption = self.execution_input()
        adoption['scope']['owned_paths'] = ['other.py']
        authority = self.state()['scope_authority']
        ticket = authority['execution_plan']['catalog']['tickets'][0]
        step = next(item for item in ticket['plan']['steps'] if item['id'] == 'inline-other')
        adoption['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, step)]
        adoption['semantic']['adopt_paths'] = ['other.py']
        adoption['semantic']['adoption_snapshot_digest'] = current['revalidation_source']['snapshot_digest']
        adoption['authorization']['scope_digest'] = transfer.entry.digest(adoption['scope'])
        stale_adoption = copy.deepcopy(adoption)
        stale_adoption['semantic']['adoption_snapshot_digest'] = '0' * 64
        with self.assertRaises((control.ControlError, transfer.entry.PreparationError)):
            transfer.dispatch.handle({'protocol': transfer.handoff.PROTOCOL,
                'operation': 'prepare', 'handoff': transfer.handoff.handle(stale_adoption),
                'control': copy.deepcopy(self.state()['control'])})
        prepared = self.invoke('allocation', {'allocation_id': 'affected-new', 'operation': 'prepare',
                                               'handoff': adoption})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = self.state()['allocations']['affected-new']
        self.invoke('allocation', {'allocation_id': 'affected-new', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:fresh-executor')})
        slot = self.state()['allocations']['affected-new']
        self.invoke('allocation', {'allocation_id': 'affected-new', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:fresh-executor'),
            'result': {'delivery_id': 'affected-revalidated', 'status': 'completed', 'payload': {
                'changed_paths': [], 'adopted_paths': ['other.py'],
                'file_hashes': {'other.py': hashlib.sha256(other_data).hexdigest()},
                'tests': ['fresh behavioral revalidation passed'],
                'write_release': transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.invoke('allocation', {'allocation_id': 'affected-new', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:fresh-adoption-accepted'}})
        if storage != 'committed':
            self.flow_git('add', 'impl.py', 'other.py')
            self.flow_git('commit', '-qm', 'candidate bytes after fresh adoption')
        controller_host()
        candidate_request_data = candidate_request('after-fresh-adoption')
        candidate = candidate_request_data['evidence']['commit']
        checks = candidate_request_data['evidence']['checks']
        evidence = candidate_request_data['evidence']
        result = self.invoke('control', candidate_request_data)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],
                         'reviewable', result)
        current_ref = self.state()['control']['context']['handoff_progress']['plan_authority']
        verification = {'candidate': candidate, 'plan_digest': evidence['plan_digest'],
                        'checks': checks, 'plan_authority': current_ref}
        review = self.invoke('review-activity', {'operation': 'prepare', 'axis': 'standards',
            'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(review['next_action']['operation'], 'invoke-review', review)
        stale = copy.deepcopy(verification)
        stale['plan_authority'] = {**current_ref, 'revision': old['revision']}
        refused = self.invoke('review-activity', {'operation': 'prepare', 'axis': 'spec',
            'candidate': candidate, 'actor_ref': 'task', 'verification': stale})
        self.assertEqual(refused['status'], 'blocked', refused)

    def test_c_revision_carries_unchanged_accepted_step_through_current_authority(self):
        self._revision_carry_and_adopt('committed')

    def test_c_revision_adopts_dirty_affected_bytes(self):
        self._revision_carry_and_adopt('dirty')

    def test_c_revision_adopts_staged_affected_bytes(self):
        self._revision_carry_and_adopt('staged')

    def test_full_adoption_survives_target_advance_on_other_authorized_path(self):
        self._revision_carry_and_adopt('committed', advance_target=True)

    def _reject_net_zero_foreign_history(self, merged):
        self.begin_dispatcher()
        (self.flow / 'impl.py').write_text("print('accepted')\n")
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'accepted implementation')
        accepted = self.flow_git('rev-parse', 'HEAD')
        outer = fixture.progress.read_record(self.checkpoint)
        self.fixture_accepted_execution(outer[fixture.progress.KEY]['control']['context'], accepted)
        fixture.progress.atomic_save(self.checkpoint, outer)
        if merged:
            self.flow_git('checkout', '-qb', 'foreign-side')
        (self.flow / 'foreign.py').write_text('outside assigned scope\n')
        self.flow_git('add', 'foreign.py')
        self.flow_git('commit', '-qm', 'foreign addition')
        (self.flow / 'foreign.py').unlink()
        self.flow_git('add', '-u')
        self.flow_git('commit', '-qm', 'foreign deletion')
        if merged:
            self.flow_git('checkout', '-q', self.binding['branch'])
            self.flow_git('merge', '--no-ff', '-qm', 'merge foreign side', 'foreign-side')
        candidate = self.flow_git('rev-parse', 'HEAD')
        self.assertNotIn('foreign.py', self.flow_git('diff', '--name-only',
            self.git('rev-parse', 'main'), candidate).splitlines())
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = fixture.transfer.checks_for(self.plan()['review_required'], candidate)
        evidence = {'dispatcher_ref': 'native:dispatcher',
            'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': self.git('rev-parse', 'main'),
            'binding': self.binding, 'plan_digest': control.digest(self.plan()),
            'checks': checks}
        with self.assertRaises(control.ControlError) as rejected:
            control_git.verified_transition({'repository': str(self.flow),
                'baseline': checkpoint['git_baseline_commit'],
                'request': {'schema_version': 6, 'action': 'candidate-ready',
                    'actor_ref': 'task', 'context': self.state()['control']['context'],
                    'evidence': evidence}})
        self.assertIn('history escapes implementation scope', str(rejected.exception))

    def test_full_candidate_rejects_foreign_add_then_delete_history(self):
        self._reject_net_zero_foreign_history(False)

    def test_full_candidate_rejects_reverted_foreign_side_branch(self):
        self._reject_net_zero_foreign_history(True)

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
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],
                         'reviewable', result)
        return candidate, evidence

    def test_new_dispatch_rejects_direct_policy_before_native_creation(self):
        self.context = self.context_for(3)
        value = self.input_for(3)
        value['semantic']['implementation_policy'] = {'mode': 'direct'}
        with self.assertRaises(transfer.entry.PreparationError):
            self.begin('continuous', value)
        self.assertFalse(self.checkpoint.exists())

    def test_plan_revision_retires_passed_review_and_final_at_same_commit(self):
        candidate = self.complete_final()
        prior = copy.deepcopy(self.state()['control']['context']['handoff_progress'])
        original = control.delivery_verification(prior, 'native:dispatcher')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        old = self.state()['scope_authority']
        revised = copy.deepcopy(old['execution_plan']['catalog']['tickets'][0]['plan'])
        revised['plan_version'] += 1
        revised['steps'][0]['approach'] = ['same contract with a revised private implementation']
        contract_digest = old['execution_plan']['contract_digest']
        request = {'revision_id': 'after-final-1',
            'expected_authority_revision': old['revision'],
            'expected_authority_digest': transfer.entry.digest(old),
            'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:after-final-1'},
            'report': {'ticket_ids': ['INLINE-TRANSFER'],
                'step_ids': ['inline-implementation'], 'reason': 'Change a private function plan',
                'production_facts': 'Same behavior requires a new internal approach',
                'contract_before': contract_digest, 'contract_after': contract_digest,
                'dependency_impact': ['inline-implementation'],
                'verification_impact': ['V-inline'], 'carry_forward': []},
            'planning_paths': [], 'inline_plan': revised}
        prepared = self.invoke('prepare-plan-revision', request)
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        activated = self.invoke('commit-plan-revision', {'revision_id': 'after-final-1',
            'controller_ref': 'task', 'write_release': prepared['next_action']['write_release'],
            'planning_commit': candidate})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated', activated)
        current = self.state()['control']['context']['handoff_progress']
        self.assertEqual(current['state'], 'implementing')
        self.assertIsNone(current['candidate'])
        source = current['plan_revision_sources'][-1]
        self.assertEqual(source['candidate'], candidate)
        self.assertEqual(source['review_digest'], prior['review_digest'])
        self.assertEqual(source['attempts'][-1]['state'], 'passed')
        with self.assertRaises(control.ControlError):
            control.validate_deliverable(current, candidate, prior['review_decision']['review'],
                                         original, 'native:dispatcher')

    def test_new_accepted_writer_overrides_carried_bytes_at_candidate(self):
        self.context = self.context_for(3)
        value = self.input_for(3)
        scope = value['scope']
        scope['owned_paths'] = scope['implementation_paths'] = ['impl.py', 'other.py']
        scope['downstream']['manifest'].append({
            'path': 'other.py', 'owner': 'implementation', 'kind': 'source',
            'operation': 'add', 'basis': scope['downstream']['manifest'][1]['basis']})
        plan = value['semantic']['inline_plan']
        other = copy.deepcopy(plan['steps'][0])
        other.update(id='inline-other', target={'path': 'other.py', 'symbol': 'run', 'operation': 'add'})
        plan['steps'].append(other)
        value['authorization']['scope_digest'] = transfer.entry.digest(scope)
        self.begin('continuous', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))

        def accept(allocation_id, agent_ref, data):
            execution = self.execution_input()
            prepared = self.invoke('allocation', {'allocation_id': allocation_id,
                'operation': 'prepare', 'handoff': execution})
            self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
            slot = self.state()['allocations'][allocation_id]
            self.invoke('allocation', {'allocation_id': allocation_id, 'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref=agent_ref)})
            (self.flow / 'impl.py').write_bytes(data)
            slot = self.state()['allocations'][allocation_id]
            self.invoke('allocation', {'allocation_id': allocation_id, 'operation': 'receive',
                'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=agent_ref),
                'result': {'delivery_id': allocation_id + '-result', 'status': 'completed', 'payload': {
                    'changed_paths': ['impl.py'],
                    'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                    'tests': ['real behavior check passed'],
                    'write_release': transfer.dispatch.execution_release_token(slot['record']),
                    'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                    'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
            self.invoke('allocation', {'allocation_id': allocation_id, 'operation': 'accept',
                'decision': {'reference': 'dispatcher:' + allocation_id}})

        accept('old-writer', 'native:old-writer', b"print('old')\n")
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        old = self.state()['scope_authority']
        revised = copy.deepcopy(plan)
        revised['plan_version'] += 1
        revised['steps'][1]['approach'] = ['revise the other private function']
        contract_digest = old['execution_plan']['contract_digest']
        prepared = self.invoke('prepare-plan-revision', {
            'revision_id': 'other-only-2', 'expected_authority_revision': old['revision'],
            'expected_authority_digest': transfer.entry.digest(old), 'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:other-only-2'},
            'report': {'ticket_ids': ['INLINE-TRANSFER'], 'step_ids': ['inline-other'],
                'reason': 'Revise the other function', 'production_facts': 'Private approach changed',
                'contract_before': contract_digest, 'contract_after': contract_digest,
                'dependency_impact': ['inline-other'], 'verification_impact': ['V-inline'],
                'carry_forward': ['inline-implementation']},
            'planning_paths': [], 'inline_plan': revised})
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        self.invoke('commit-plan-revision', {'revision_id': 'other-only-2',
            'controller_ref': 'task', 'write_release': prepared['next_action']['write_release'],
            'planning_commit': self.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['carried_results'][0]['agent_ref'],
                         'native:old-writer')
        self.invoke('advance')
        self.invoke('observe', self.observation('running', 'result', ref='native:dispatcher'))
        replacement = b"print('new')\n"
        accept('new-writer', 'native:new-writer', replacement)
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'new accepted writer replaces carried bytes')
        candidate = self.flow_git('rev-parse', 'HEAD')
        progress = self.state()['control']['context']['handoff_progress']
        catalog = execution_plan.current_catalog(progress['execution_authority'])
        accepted = progress['carried_results'] + [execution for execution in progress['executions']
            if execution['state'] == 'accepted' and execution['agent_ref'] is not None]
        fingerprints = {relative: control_git.file_fingerprint(self.flow, relative)
                        for relative in progress['allowed_paths']}
        self.assertEqual(execution_plan.current_accepted_steps(
            catalog, accepted, progress['execution_authority'], fingerprints),
            {'inline-implementation'})
        checks = fixture.transfer.checks_for(self.plan()['review_required'], candidate)
        incomplete = {'dispatcher_ref': 'native:dispatcher',
            'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': self.git('rev-parse', 'main'),
            'binding': self.binding, 'plan_digest': control.digest(self.plan()),
            'checks': checks, 'source': {'adapter': 'fixture-controller',
                'call_ref': 'partial-checks', 'response_ref': 'partial-checks-result',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': 'native:dispatcher'}}}
        with self.assertRaises(control.ControlError) as missing_step:
            control_git.verified_transition({'repository': str(self.flow),
                'baseline': self.state()['control']['context']['handoff_progress']['git_baseline_commit'],
                'request': {'schema_version': 6, 'action': 'candidate-ready', 'actor_ref': 'task',
                    'context': copy.deepcopy(self.state()['control']['context']),
                    'evidence': incomplete}})
        self.assertIn('current function step: inline-other', str(missing_step.exception))
        other_execution = self.execution_input()
        other_execution['scope']['owned_paths'] = ['other.py']
        ticket = self.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        other_step = next(item for item in ticket['plan']['steps'] if item['id'] == 'inline-other')
        other_execution['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, other_step)]
        other_execution['authorization']['scope_digest'] = transfer.entry.digest(other_execution['scope'])
        prepared = self.invoke('allocation', {'allocation_id': 'other-current',
            'operation': 'prepare', 'handoff': other_execution})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = self.state()['allocations']['other-current']
        self.invoke('allocation', {'allocation_id': 'other-current', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:other-current')})
        other_bytes = b"print('other current')\n"
        (self.flow / 'other.py').write_bytes(other_bytes)
        slot = self.state()['allocations']['other-current']
        self.invoke('allocation', {'allocation_id': 'other-current', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:other-current'),
            'result': {'delivery_id': 'other-current-result', 'status': 'completed', 'payload': {
                'changed_paths': ['other.py'],
                'file_hashes': {'other.py': hashlib.sha256(other_bytes).hexdigest()},
                'tests': ['other behavior verified'],
                'write_release': transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.invoke('allocation', {'allocation_id': 'other-current', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:other-current'}})
        self.flow_git('add', 'other.py')
        self.flow_git('commit', '-qm', 'complete other current function step')
        candidate = self.flow_git('rev-parse', 'HEAD')
        self.settings['thread_id'] = 'task'
        self.request['host'].update(thread_id='task', role='controller', source_ref=None)
        self.request['host'].pop('actor_ref', None)
        checks = fixture.transfer.checks_for(self.plan()['review_required'], candidate)
        result = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher',
            'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': self.git('rev-parse', 'main'),
            'binding': self.binding, 'plan_digest': control.digest(self.plan()), 'checks': checks},
            'receipt': {'adapter': 'fixture-controller', 'call_ref': 'new-writer-checks',
                'response_ref': 'new-writer-checks-result', 'raw': {'checks': checks,
                    'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}})
        rejections = [item['rejection'] for item in self.state()['control_transactions'].values()
                      if item.get('rejection')]
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],
                         'reviewable', (result, rejections))
        current_results = self.state()['control']['context']['handoff_progress']['executions']
        self.assertEqual(next(item['file_hashes'] for item in current_results
                              if item['agent_ref'] == 'native:new-writer'),
                         {'impl.py': hashlib.sha256(replacement).hexdigest()})

    def test_pause_and_cancel_keep_original_plan_revision_barrier_closed(self):
        self.begin_dispatcher()
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        old = self.state()['scope_authority']
        revised = copy.deepcopy(old['execution_plan']['catalog']['tickets'][0]['plan'])
        revised['plan_version'] += 1
        revised['steps'][0]['approach'] = ['revised private function approach']
        contract_digest = old['execution_plan']['contract_digest']
        prepared = self.invoke('prepare-plan-revision', {
            'revision_id': 'paused-private-2', 'expected_authority_revision': old['revision'],
            'expected_authority_digest': transfer.entry.digest(old), 'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:paused-private-2'},
            'report': {'ticket_ids': ['INLINE-TRANSFER'], 'step_ids': ['inline-implementation'],
                'reason': 'Revise a private function', 'production_facts': 'Implementation detail changed',
                'contract_before': contract_digest, 'contract_after': contract_digest,
                'dependency_impact': ['inline-implementation'],
                'verification_impact': ['V-inline'], 'carry_forward': []},
            'planning_paths': [], 'inline_plan': revised})
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        release = prepared['next_action']['write_release']
        paused = self.invoke('pause')
        self.assertEqual(paused['status'], 'paused', paused)
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('commit-plan-revision', {'revision_id': 'paused-private-2',
                'controller_ref': 'task', 'write_release': release,
                'planning_commit': self.flow_git('rev-parse', 'HEAD')})
        cancelled = self.invoke('cancel')
        self.assertEqual(cancelled['status'], 'cancelled', cancelled)
        reconciled = self.invoke('reconcile-plan-revision', {'revision_id': 'paused-private-2'})
        self.assertEqual(reconciled['next_action']['operation'], 'plan-revision-suspended', reconciled)
        self.assertEqual(self.state()['scope_authority'], old)

    def test_internal_revision_updates_only_affected_review_checks(self):
        self.begin_dispatcher()
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        old = self.state()['scope_authority']
        original_validation = copy.deepcopy(self.state()['control']['context']['handoff_progress']['validation_plan'])
        revised = copy.deepcopy(old['execution_plan']['catalog']['tickets'][0]['plan'])
        revised['plan_version'] += 1
        revised['steps'][0]['approach'] = ['revised private function test seam']
        validation = copy.deepcopy(original_validation)
        validation['review_required'][1]['command'] = 'python3 -m unittest affected_revision'
        contract_digest = old['execution_plan']['contract_digest']
        request = {'revision_id': 'review-check-2',
            'expected_authority_revision': old['revision'],
            'expected_authority_digest': transfer.entry.digest(old),
            'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:review-check-2'},
            'report': {'ticket_ids': ['INLINE-TRANSFER'],
                'step_ids': ['inline-implementation'],
                'reason': 'Revise the focused test command for the private function',
                'production_facts': 'The affected test file moved to the current seam',
                'contract_before': contract_digest, 'contract_after': contract_digest,
                'dependency_impact': ['inline-implementation'],
                'verification_impact': ['V-inline'], 'carry_forward': []},
            'planning_paths': [], 'inline_plan': revised,
            'validation_plan': validation}
        weakened = copy.deepcopy(request)
        weakened['validation_plan']['final_required'][0]['command'] = 'true'
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('prepare-plan-revision', weakened)
        wrong_runner = copy.deepcopy(request)
        wrong_runner['validation_plan']['review_required'][1]['command'] = (
            'python3 scripts/run_tests.py --test-file tests/runtime/test_review_first_validation.py')
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('prepare-plan-revision', wrong_runner)
        weakened_skip = copy.deepcopy(request)
        weakened_skip['validation_plan']['review_required'][1]['allowed_skips'] = ['any failure']
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('prepare-plan-revision', weakened_skip)
        prepared = self.invoke('prepare-plan-revision', request)
        self.assertEqual(prepared['next_action']['operation'], 'plan-revision-prepared', prepared)
        activated = self.invoke('commit-plan-revision', {'revision_id': 'review-check-2',
            'controller_ref': 'task', 'write_release': prepared['next_action']['write_release'],
            'planning_commit': self.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(activated['next_action']['operation'], 'plan-revision-activated', activated)
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['validation_plan'], validation)
        self.assertEqual(checkpoint['plan_digest'], control.digest(validation))
        self.assertEqual(checkpoint['plan_revision_sources'][-1]['validation_plan'], original_validation)

    def test_focused_candidate_can_review_but_cannot_complete(self):
        candidate, evidence = self.ready_candidate()
        current_plan = self.state()['control']['context']['handoff_progress']['plan_authority']
        for axis in ('standards','spec'):
            response = self.invoke('review-activity', {'operation':'prepare','axis':axis,'candidate':candidate,
                'actor_ref':'task','verification':{'candidate':candidate,'plan_digest':evidence['plan_digest'],
                                                   'checks':evidence['checks'],'plan_authority':current_plan}})
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
        current_plan = self.state()['control']['context']['handoff_progress']['plan_authority']
        for axis in ('standards', 'spec'):
            prepared = self.invoke('review-activity', {'operation':'prepare','axis':axis,'candidate':candidate,
                'actor_ref':'task','verification':{'candidate':candidate,'plan_digest':evidence['plan_digest'],
                                                   'checks':evidence['checks'],'plan_authority':current_plan}})
            receipt = {'adapter':'fixture-native','call_ref':'call:'+axis,'response_ref':'response:'+axis,
                       'status':'stopped','ref':'native:'+axis,'raw':{'result':'accepted'}}
            self.invoke('review-activity', {'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task',
                        'action_id':prepared['next_action']['action_id'],'receipt':receipt})
            reviews[axis] = {'candidate':candidate,'expected_target_head':evidence['expected_target_head'],
                'plan_digest':evidence['plan_digest'],'plan_authority':current_plan,
                'reviewer_ref':'native:'+axis,'status':'accepted',
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
                 'plan_digest':evidence['plan_digest'],'plan_authority':checkpoint['plan_authority'],
                 'review_digest':checkpoint['review_digest']}
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
        start = {k:failed[k] for k in ('dispatcher_ref','carrier_attempt','candidate',
                                      'expected_target_head','plan_digest','plan_authority','review_digest')}
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

    def test_target_advance_allows_closure_to_handle_final_integration(self):
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
        result = adapter.verified_transition({'repository':str(self.flow),'baseline':self.state()['handoff']['scope']['baseline'],'request':request})
        self.assertEqual(result['context']['handoff_progress']['state'], 'closure-pending')

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
            lambda v:v['plan_authority'].update(revision=v['plan_authority']['revision'] - 1),
            lambda v:v['attempts'][-1]['plan_authority'].update(
                revision=v['attempts'][-1]['plan_authority']['revision'] - 1),
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
        stale_review = copy.deepcopy(review)
        stale_review['spec']['plan_authority']['revision'] -= 1
        with self.assertRaises(control.ControlError):
            control.validate_review(checkpoint['candidate'], stale_review, original,
                                    'native:dispatcher')

    def test_target_advance_does_not_invalidate_review_or_final_validation(self):
        candidate, evidence = self.ready_candidate()
        (self.root / 'unrelated.txt').write_text('new target\n')
        self.git('add', 'unrelated.txt'); self.git('commit', '-qm', 'advance target')
        self.converge(candidate, evidence)
        checkpoint = self.state()['control']['context']['handoff_progress']
        review_digest = checkpoint['review_digest']
        start = {'attempt_id':'after-target-advance', 'dispatcher_ref':'native:dispatcher',
            'carrier_attempt':self.state()['control']['context']['carrier']['attempt'],
            **{k:checkpoint[k] for k in ('candidate','expected_target_head','plan_digest',
                                         'plan_authority','review_digest')}}
        result = self.invoke('control', {'action':'validation-start', 'evidence':start,
            'receipt':{'adapter':'fixture-controller','call_ref':'final','response_ref':'after-target-final',
                       'raw':{'decision':'validate own candidate'}}})
        self.assertEqual(result['next_action']['operation'], 'continue-host', result)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['review_digest'], review_digest)
        self.assertFalse((self.flow / 'unrelated.txt').exists())

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
            **{k:checkpoint[k] for k in ('candidate','expected_target_head','plan_digest',
                                         'plan_authority','review_digest')}},
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
            'verification':{'candidate':candidate,'plan_digest':progress['plan_digest'],
                            'checks':progress['tests'],'plan_authority':progress['plan_authority']}}),
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
                 **{key:context['handoff_progress'][key] for key in ('candidate','expected_target_head',
                      'plan_digest','plan_authority','review_digest')}}
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
