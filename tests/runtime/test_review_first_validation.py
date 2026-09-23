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
