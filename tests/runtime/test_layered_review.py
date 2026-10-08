"""Layered review through the original C/B/Git entry; host responses are doubles."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as fixture
import execution_plan
import workflow_progress as progress


class DispatchInputTests(unittest.TestCase):
    def test_measure_tool_reads_original_command_log_without_inventing_wall_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'actual-command.log'
            with output.open('xb') as stream:
                result = subprocess.run(['/usr/bin/time', '-p', sys.executable, '-c',
                    'print(sum(range(100000)))'], stdout=stream, stderr=subprocess.STDOUT)
            self.assertEqual(result.returncode, 0)
            before = output.read_bytes()
            script = Path(__file__).resolve().parents[2] / 'scripts/measure_review_state.py'
            measured = subprocess.run([sys.executable, str(script), '--command-log', str(output)],
                capture_output=True, text=True)
            self.assertEqual(measured.returncode, 0, measured.stderr)
            report = json.loads(measured.stdout)
            command = report['command_logs'][0]
            self.assertGreaterEqual(command['wall_seconds'], 0)
            self.assertGreaterEqual(command['cpu_user_seconds'], 0)
            self.assertEqual(command['started_at'], 'unknown')
            self.assertEqual(command['finished_at'], 'unknown')
            self.assertEqual(report['end_to_end_wall_seconds'], 'unknown')
            self.assertEqual(report['efficiency'], 'evidence-insufficient')
            self.assertEqual(command['sha256'], hashlib.sha256(before).hexdigest())
            self.assertEqual(output.read_bytes(), before)

    def test_rework_original_receipts_require_controller_diagnosis_and_measure_readonly(self):
        flow, request, original, context, _, _ = self.accepted_repair_flow()
        flow.flow_git('add', 'impl.py')
        flow.flow_git('commit', '-qm', 'one accepted slice')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.settings['thread_id'] = 'task'
        flow.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        checkpoint = flow.state()['control']['context']['handoff_progress']
        ticket = checkpoint['execution_authority']['execution_plan']['catalog']['tickets'][0]['ticket_id']
        required = [item for item in checkpoint['validation_plan']['review_required'] if item['category'] == 'affected']
        checks = fixture.transfer.checks_for(required, candidate)
        checks[0].update(status='failed', exit_code=1)
        for number in (1, 2):
            result = flow.invoke('control', {'action': 'ticket-check', 'evidence': {'ticket_id': ticket,
                'candidate': candidate, 'plan_authority': checkpoint['plan_authority'], 'checks': checks},
                'receipt': {'adapter': 'fixture-command', 'call_ref': 'fixture-failure:' + str(number),
                    'response_ref': 'fixture-stop:' + str(number), 'raw': {'checks': checks, 'source_unchanged': True,
                    'stopped': True, 'dispatcher_ref': 'native:dispatcher', 'classification': 'fixture-error',
                    'slice_id': 'original'}}})
            self.assertEqual(result['status'], 'blocked', result)
        denied = flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'repair',
            'handoff': request, 'invocation_id': 'diagnosed-repair'})
        self.assertEqual(denied['error']['code'], 'rework_diagnosis_required', denied)
        current = flow.state()
        self.assertEqual(current['allocations']['original']['metrics'], original['metrics'])
        block = current['business_block']
        recovered = flow.invoke('recover-business', {'decision_id': 'original-short-diagnosis',
            'subject': block['subject'], 'reference': 'controller:fixture-channel',
            'diagnosis': 'both original stopped commands report test preparation failure',
            'instruction': 'use the original legal checks and preserve all required checks',
            'expected_progress': 'same slice and original invocation can continue'})
        self.assertNotEqual(recovered['status'], 'blocked', recovered)
        before = flow.checkpoint.read_bytes()
        script = Path(__file__).resolve().parents[2] / 'scripts/measure_review_state.py'
        result = subprocess.run([sys.executable, str(script), '--checkpoint', str(flow.checkpoint)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        measured = json.loads(result.stdout)['checkpoint']
        self.assertEqual(measured['slices'][0]['slice_id'], 'original')
        self.assertEqual(measured['slices'][0]['submissions'], 1)
        self.assertEqual(len([source for source in measured['command_sources']
            if source['classification'] == 'fixture-error']), 2)
        self.assertEqual(flow.checkpoint.read_bytes(), before)
        for number in (3, 4):
            next_failure = flow.invoke('control', {'action': 'ticket-check', 'evidence': {'ticket_id': ticket,
                'candidate': candidate, 'plan_authority': checkpoint['plan_authority'], 'checks': checks},
                'receipt': {'adapter': 'fixture-command', 'call_ref': 'fixture-failure:' + str(number),
                    'response_ref': 'fixture-stop:' + str(number), 'raw': {'checks': checks, 'source_unchanged': True,
                    'stopped': True, 'dispatcher_ref': 'native:dispatcher', 'classification': 'fixture-error',
                    'slice_id': 'original'}}})
            self.assertEqual(next_failure['status'], 'blocked', next_failure)
        again = flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'repair',
            'handoff': request, 'invocation_id': 'diagnosed-repair'})
        self.assertEqual(again['error']['code'], 'rework_diagnosis_required', again)
        next_block = flow.state()['business_block']
        self.assertNotEqual(next_block['id'], block['id'])
        self.assertTrue(set(next_block['evidence']['triggers'][0]['source_refs']).isdisjoint(
            block['evidence']['triggers'][0]['source_refs']))
        self.assertEqual(flow.allocation_slot('original')['metrics'], original['metrics'])
        self.assertEqual(flow.allocation_slot('original')['record'], original['record'])
        flow.invoke('recover-business', {'decision_id': 'second-short-diagnosis', 'subject': next_block['subject'],
            'reference': 'controller:second-fixture-channel', 'diagnosis': 'new original receipts require a new diagnosis',
            'instruction': 'retain same slice, original ref and all checks', 'expected_progress': 'original repair can proceed'})
        self.assertIn('original-short-diagnosis', flow.state()['recovery_decisions'])
        self.assertEqual(len(flow.state()['business_history']), 2)
        request = flow.execution_input()
        request['manifest_baseline'] = context['handoff_progress']['git_baseline_commit']
        request['semantic'].pop('validation_plan', None)
        prepared = flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'repair',
            'handoff': request, 'invocation_id': 'diagnosed-repair'})
        self.assertIsNotNone(prepared['next_action'], prepared)
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        self.assertIn('ticket', prepared['next_action']['payload']['payload']['validation_responsibilities'])

    def test_rework_saved_transactions_use_original_revision_order(self):
        flow, _, original, _, _, _ = self.accepted_repair_flow()
        flow.flow_git('add', 'impl.py')
        flow.flow_git('commit', '-qm', 'original accepted slice')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.settings['thread_id'] = 'task'
        flow.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        checkpoint = flow.state()['control']['context']['handoff_progress']
        ticket = checkpoint['execution_authority']['execution_plan']['catalog']['tickets'][0]['ticket_id']
        required = [item for item in checkpoint['validation_plan']['review_required'] if item['category'] == 'affected']
        def command(identity, kind, status):
            checks = fixture.transfer.checks_for(required, candidate)
            for check in checks:
                check.update(status=status, exit_code=0 if status == 'passed' else None if status == 'unknown' else 1)
            receipt = {'adapter': 'fixture-command', 'call_ref': identity, 'response_ref': identity + ':response',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': status != 'unknown',
                    'dispatcher_ref': 'native:dispatcher', 'classification': kind, 'slice_id': 'original'}}
            return {'action': 'ticket-check', 'evidence': {'ticket_id': ticket, 'candidate': candidate,
                'plan_authority': checkpoint['plan_authority'], 'checks': checks}, 'receipt': receipt}
        def canonical_key(data):
            derived = copy.deepcopy(data)
            derived['evidence'].update(source=derived['receipt'], source_snapshot={})
            return progress.entry.digest(derived)
        def observed():
            return progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint)).rework_diagnostics()
        for interval_kind, interval_status in [('unknown', 'passed'), ('unknown', 'unknown'), ('tdd-red', 'failed')]:
            with self.subTest(interval=interval_status + interval_kind):
                prefix = interval_status + ':' + interval_kind
                failures = sorted([command(prefix + ':failure:' + str(number), 'fixture-error', 'failed')
                    for number in range(20)], key=canonical_key)
                intervals = sorted([command(prefix + ':interval:' + str(number), interval_kind, interval_status)
                    for number in range(20)], key=canonical_key)
                first, last, interval = failures[0], failures[1], intervals[-1]
                self.assertLess(canonical_key(last), canonical_key(interval))
                for data in (first, interval, last):
                    result = flow.invoke('control', data)
                    requests = [transaction['request'] for transaction in flow.state().get('control_transactions', {}).values()]
                    if data is not interval or interval_status != 'passed':
                        self.assertTrue(any(saved['receipt'].get('call_ref') == data['receipt']['call_ref'] for saved in requests), result)
                    else:
                        self.assertTrue(result['next_action']['result']['ok'], result)
                chronology = [(value['base_revision'], value['request']['receipt'].get('call_ref'),
                    value['request']['receipt'].get('raw', {}).get('classification'),
                    value['request']['receipt'].get('raw', {}).get('slice_id'))
                    for value in sorted(flow.state()['control_transactions'].values(), key=lambda value: value['base_revision'])]
                self.assertEqual(observed(), [], chronology)
                flow.invoke('control', command(prefix + ':reset', 'unknown', 'passed'))
        first = command('consecutive-final-1', 'fixture-error', 'failed')
        flow.invoke('control', first)
        self.assertEqual(observed(), [])
        flow.invoke('control', first)
        self.assertEqual(observed(), [])
        flow.invoke('control', command('consecutive-final-2', 'fixture-error', 'failed'))
        triggers = observed()
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]['slice_id'], 'original')
        self.assertEqual(flow.allocation_slot('original')['metrics'], original['metrics'])

    def test_rework_command_observations_keep_original_call_identity_and_order(self):
        flow, _, original, _, _, _ = self.accepted_repair_flow()
        flow.flow_git('add', 'impl.py')
        flow.flow_git('commit', '-qm', 'one original accepted slice')
        candidate = flow.flow_git('rev-parse', 'HEAD')
        flow.settings['thread_id'] = 'task'
        flow.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        checkpoint = flow.state()['control']['context']['handoff_progress']
        ticket = checkpoint['execution_authority']['execution_plan']['catalog']['tickets'][0]['ticket_id']
        required = [item for item in checkpoint['validation_plan']['review_required'] if item['category'] == 'affected']
        def observe(call, response, status='failed'):
            checks = fixture.transfer.checks_for(required, candidate)
            for check in checks:
                check.update(status=status, exit_code=0 if status == 'passed' else None if status == 'unknown' else 1)
            return flow.invoke('control', {'action': 'ticket-check', 'evidence': {'ticket_id': ticket,
                'candidate': candidate, 'plan_authority': checkpoint['plan_authority'], 'checks': checks},
                'receipt': {'adapter': 'fixture-command', 'call_ref': call, 'response_ref': response,
                    'raw': {'checks': checks, 'source_unchanged': True, 'stopped': status != 'unknown',
                        'dispatcher_ref': 'native:dispatcher', 'classification': 'fixture-error', 'slice_id': 'original'}}})
        def diagnostics():
            return progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint)).rework_diagnostics()
        observe('before-success', 'first-stop')
        passed = observe('success-interval', 'success-stop', 'passed')
        self.assertTrue(passed['next_action']['result']['ok'], passed)
        observe('before-success', 'second-stop')
        observe('after-success', 'independent-stop')
        self.assertEqual(diagnostics(), [])
        observe('next-interval', 'next-success', 'passed')
        observe('same-call', 'unknown-observation', 'unknown')
        self.assertEqual(diagnostics(), [])
        observe('same-call', 'first-definitive-stop')
        self.assertEqual(diagnostics(), [])
        observe('same-call', 'second-definitive-stop')
        self.assertEqual(diagnostics(), [])
        receipts = [transaction['request']['receipt'] for transaction in flow.state()['control_transactions'].values()]
        self.assertEqual({receipt['response_ref'] for receipt in receipts if receipt['call_ref'] == 'same-call'},
            {'unknown-observation', 'first-definitive-stop', 'second-definitive-stop'})
        observe('another-call', 'another-definitive-stop')
        self.assertEqual(len(diagnostics()), 1)
        self.assertEqual(flow.allocation_slot('original')['metrics'], original['metrics'])
        self.assertEqual(flow.allocation_slot('original')['record'], original['record'])

    def accepted_repair_flow(self):
        flow = fixture.ProgressTests(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        value = flow.input_for(3)
        plan = value['semantic']['inline_plan']
        plan['schema_version'] = 2
        step = plan['steps'][0]
        plan['behavior_slices'] = [{'id': 'whole', 'step_ids': [step['id']],
            'ac_refs': plan['contract_refs'][:], 'verification_refs': step['verification_refs'][:],
            'read_only': step['read_only'][:], 'shared_state': []}]
        flow.context = flow.context_for(3)
        flow.begin('continuous', value)
        flow.invoke('observe', flow.observation(ref='native:dispatcher'))
        request = flow.execution_input()
        prepared = flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'prepare', 'handoff': request})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = flow.allocation_slot('original')
        bound = flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'bind',
            'receipt': flow.receipt(slot['record'], ref='native:executor')})
        original_release = bound['next_action']['result']['write_release']
        body = b'def run():\n    return True\n'
        (flow.flow / 'impl.py').write_bytes(body)
        slot = flow.allocation_slot('original')
        flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'receive',
            'receipt': flow.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
            'result': {'delivery_id': 'original-result', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(body).hexdigest()},
                'tests': ['focused passed'], 'write_release': original_release,
                'plan_refs': request['semantic']['plan_refs'],
                'plan_authority': flow.state()['control']['context']['handoff_progress']['plan_authority']}}})
        accepted = flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'accept',
            'decision': {'reference': 'accept-original'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
        original = flow.allocation_slot('original')
        context = copy.deepcopy(flow.state()['control']['context'])
        request = flow.execution_input()
        return flow, request, original, context, original_release, body

    def test_t03_repair_prepare_return_loss_retains_original_intent_and_reuses_result(self):
        flow, request, original, context, _, _ = self.accepted_repair_flow()
        repair = {'allocation_id': 'original', 'operation': 'repair',
            'invocation_id': 'return-lost', 'handoff': request}
        store = progress.state_store.StateStore(flow.checkpoint)
        original_replace = progress.state_store.os.replace
        lost = []

        def replace(source, target, *args, **kwargs):
            if Path(target) == flow.checkpoint and not lost:
                envelope = json.loads(Path(source).read_bytes())
                root = store.read_evidence(envelope['current'], run_id=envelope['run_id'], kind='root')['value']
                for reference in root['subjects'].values():
                    subject = store.read_evidence(reference, run_id=envelope['run_id'], kind='subject')['value']
                    slot = subject.get('allocations', {}).get('original')
                    if slot and (slot.get('record') or {}).get('status') == 'prepared' and not slot.get('repair_proposal'):
                        lost.append(True)
                        raise OSError('prepared result lost before original C pointer installation')
            return original_replace(source, target, *args, **kwargs)

        with patch.object(progress.state_store.os, 'replace', replace):
            with self.assertRaises(OSError):
                flow.invoke('allocation', repair)
        self.assertEqual(lost, [True])
        pending = flow.allocation_slot('original')
        self.assertEqual(pending['record'], original['record'])
        self.assertEqual(flow.state()['control']['context'], context)
        self.assertEqual(pending['repair_proposal']['phase'], 'applying')
        intent = pending['transaction']['intent_id']
        changed = copy.deepcopy(repair)
        changed['handoff']['configuration']['receipt'] = 'changed-while-unknown'
        rejected = flow.invoke('allocation', changed)
        self.assertEqual(rejected['status'], 'blocked', rejected)
        self.assertEqual(flow.allocation_slot('original')['transaction']['intent_id'], intent)
        self.assertEqual(flow.allocation_slot('original')['record'], original['record'])
        recovery = flow.invoke('resume')
        self.assertEqual(recovery['next_action']['operation'], 'allocation-recovery', recovery)
        resumed = flow.invoke('allocation', recovery['next_action']['data'])
        self.assertIsNotNone(resumed['next_action'], resumed)
        self.assertEqual(resumed['next_action']['operation'], 'invoke-host', resumed)
        self.assertEqual(resumed['next_action']['payload']['payload']['execution_invocation']['ref'], 'native:executor')
        action_id = resumed['next_action']['action_id']
        repeated = flow.invoke('allocation', repair)
        self.assertEqual(repeated['next_action']['operation'], 'lookup-exact-allocation', repeated)
        self.assertEqual(flow.allocation_slot('original')['action']['action_id'], action_id)
        self.assertNotIn('repair_proposal', flow.allocation_slot('original'))
        self.assertEqual(flow.allocation_slot('original')['metrics']['first_submission'], original['metrics']['first_submission'])

    def test_t03_repair_admission_preserves_original_for_config_adoption_and_git_rejection(self):
        flow, request, original, context, original_release, body = self.accepted_repair_flow()
        rejected = copy.deepcopy(request)
        rejected['configuration']['receipt'] = 'other-valid-receipt'
        repair = {'allocation_id': 'original', 'operation': 'repair', 'invocation_id': 'retry-after-rejection'}
        execution = context['handoff_progress']['executions'][0]
        evidence = {key: copy.deepcopy(execution[key]) for key in ('task_id', 'paths', 'read_only', 'behavior',
            'tests', 'git_operations', 'plan_refs', 'plan_authority', 'group_digest')}
        authority = context['handoff_progress']['execution_authority']
        evidence['tests'] = execution_plan.validate_plan_refs(execution_plan.current_catalog(authority),
            execution['plan_refs'], execution['paths'], execution['read_only'],
            context['handoff_progress']['executions'], authority)['verification_commands']
        evidence.update(configuration=rejected['configuration'], repair={'agent_ref': 'native:executor',
            'allocation_digest': execution['allocation_digest'], 'stop_ref': 'host:result:stopped',
            'invocation_id': repair['invocation_id']})
        with self.assertRaisesRegex(progress.control.ControlError, 'original configuration'):
            progress.control.transition({'schema_version': 7, 'actor_ref': 'task', 'context': context,
                'action': 'repair-execution', 'evidence': evidence})
        with self.assertRaisesRegex(ValueError, 'configuration'):
            with progress.record_lock(flow.checkpoint), progress.state_store.operation_store(flow.checkpoint):
                progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint)).allocation(
                    {**repair, 'handoff': rejected})
        self.assertEqual(flow.allocation_slot('original')['record'], original['record'])
        self.assertEqual(flow.allocation_slot('original'), original)
        self.assertEqual(flow.state()['control']['context'], context)
        rejected_adoption = copy.deepcopy(request)
        rejected_adoption['semantic'].update(adopt_paths=['impl.py'], adoption_snapshot_digest='a' * 64)
        with self.assertRaises(ValueError):
            with progress.record_lock(flow.checkpoint), progress.state_store.operation_store(flow.checkpoint):
                progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint)).allocation(
                    {**repair, 'handoff': rejected_adoption})
        self.assertEqual(flow.allocation_slot('original'), original)
        self.assertEqual(flow.state()['control']['context'], context)
        (flow.flow / 'impl.py').write_bytes(b'def run():\n    return "unaccepted"\n')
        try:
            with self.assertRaises(ValueError):
                with progress.record_lock(flow.checkpoint), progress.state_store.operation_store(flow.checkpoint):
                    progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint)).allocation(
                        {**repair, 'handoff': request})
            self.assertEqual(flow.allocation_slot('original'), original)
            self.assertEqual(flow.state()['control']['context'], context)
        finally:
            (flow.flow / 'impl.py').write_bytes(body)
        resumed = flow.invoke('allocation', {**repair, 'handoff': request})
        self.assertEqual(resumed['next_action']['operation'], 'invoke-host', resumed)
        slot = flow.allocation_slot('original')
        self.assertEqual(resumed['next_action']['payload']['payload']['execution_invocation']['ref'], 'native:executor')
        with self.assertRaises(progress.entry.PreparationError):
            progress.dispatch.validate_allocation_receipt(slot['record'], 'bind',
                flow.receipt(original['record'], ref='native:executor'), flow.state()['control']['context'])
        receipt = flow.receipt(slot['record'], ref='native:executor')
        receipt['receipt_ref'] = 'repair-ready-after-rejection'
        rebound = flow.invoke('allocation', {'allocation_id': 'original', 'operation': 'bind', 'receipt': receipt})
        new_release = rebound['next_action']['result']['write_release']
        self.assertNotEqual(new_release, original_release)
        with self.assertRaisesRegex(progress.control.ControlError, 'result authority or release changed'):
            progress.control.transition({'schema_version': 7, 'actor_ref': 'task',
                'context': flow.state()['control']['context'], 'action': 'execution-result', 'evidence': {
                    'agent_ref': 'native:executor', 'stopped': True, 'git_unchanged': True,
                    'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(body).hexdigest()},
                    'tests': ['focused passed'], 'plan_refs': request['semantic']['plan_refs'],
                    'plan_authority': execution['plan_authority'], 'write_release': original_release}})

    def test_t03_handle_cleanup_excludes_competing_owner_and_preserves_unknown_reserve(self):
        flow = fixture.ProgressTests(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        launched = flow.begin('continuous')
        original_action = launched['next_action']['action_id']
        store = progress.state_store.StateStore(flow.checkpoint)
        settled = store.reserve('settled-earlier-invocation')
        settled_path = store.directory / 'reserves' / 'business' / settled
        contender = 'next-owner-invocation'
        observations = []
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == settled_path:
                try:
                    with progress.record_lock(flow.checkpoint, timeout=0):
                        progress.state_store.StateStore(flow.checkpoint).reserve(contender, recovery=True)
                    observations.append('acquired')
                except progress.entry.PreparationError as error:
                    observations.append(error.code)
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', unlink):
            paused = flow.invoke('pause')
        store.require_reserve(original_action)
        survived = None
        if observations == ['acquired']:
            try:
                store.require_reserve(contender, recovery=True)
                survived = True
            except progress.entry.PreparationError:
                survived = False
        self.assertEqual((observations, survived), (['checkpoint_busy'], None),
                         'cleanup must exclude a real competing owner; stale cleanup can remove its recovery reserve')
        self.assertGreater(paused['reading']['structured_bytes'], 0)
        with progress.record_lock(flow.checkpoint, timeout=0):
            store.reserve(contender, recovery=True)
        store.require_reserve(contender, recovery=True)

    def test_t03_model_input_counts_utf8_and_limits_total_expansion(self):
        normal = progress.handoff.model_input({'contract': 'é' * 100})
        self.assertEqual(normal['reading']['model_bytes'], len(normal['text'].encode('utf-8')))
        with self.assertRaises(progress.entry.PreparationError):
            progress.handoff.model_input({'contract': 'é' * 32768})
        expanded = progress.handoff.model_input({'segments': ['x' * 60000] * 4}, expanded=True)
        self.assertGreater(expanded['reading']['model_bytes'], 65536)
        with self.assertRaises(progress.entry.PreparationError):
            progress.handoff.model_input({'segments': ['x' * 60000] * 5}, expanded=True)
        with self.assertRaises(progress.entry.PreparationError):
            progress.handoff.model_input({'segments': ['x' * 70000]}, expanded=True)

    def test_t03_reads_count_actual_cross_checkpoint_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'first.json'
            second = Path(directory) / 'second.json'
            run_id = str(uuid.uuid4())
            refs = [progress.state_store.StateStore(path).write_object(run_id, 'evidence', {'proof': text})
                    for path, text in ((first, 'source'), (second, 'dependency'))]
            with progress.state_store.operation_store(first):
                owner = progress.state_store.open_store(first)
                owner.read_evidence(refs[0], run_id=run_id)
                progress.state_store.open_store(second).read_evidence(refs[1], run_id=run_id)
                self.assertEqual(owner.read_totals['files'], 2)
                self.assertEqual(owner.read_totals['bytes'], sum(ref['size'] for ref in refs))
                self.assertEqual(owner.read_totals['coverage'], 'state-store-only')


class LayeredReviewTests(fixture.ProgressTests):
    def test_final_unknown_observation_keeps_original_attempt_and_blocks_candidate_swap(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
            self.ticket_review(candidate, ticket)
        reviews = self.delivery_reviews(candidate)
        self.finish_certificate_delivery(candidate, reviews, unknown_first=True)

    def test_repair_continues_stopped_executor_with_fresh_release_and_snapshot(self):
        candidate = self.layered_flow()
        original = self.allocation_slot('A1', 'A')
        original_context = copy.deepcopy(self.state()['control']['context'])
        old_record = original['record']
        old_launch = progress.dispatch.allocation_launch(old_record)
        old_receipt = self.receipt(old_record, ref='native:executor-A1')
        request = self.execution_input()
        ticket = self.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        request['scope']['owned_paths'] = ['a1.py']
        request['authorization']['scope_digest'] = fixture.transfer.entry.digest(request['scope'])
        request['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, ticket['plan']['steps'][0])]
        prepared = self.invoke('allocation', {'allocation_id': 'A1', 'operation': 'repair',
            'invocation_id': 'repair-A1-first', 'handoff': request})
        self.assertIsNotNone(prepared['next_action'], prepared)
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        launch = prepared['next_action']['payload']
        self.assertEqual(launch['payload']['execution_invocation']['ref'], 'native:executor-A1')
        self.assertEqual(launch['payload']['execution_invocation']['operation'], 'continue')
        self.assertNotEqual(launch['attempt'], old_launch['attempt'])
        self.assertLessEqual(prepared['next_action']['reading']['model_bytes'], 65536)
        self.assertGreater(prepared['next_action']['reading']['structured_bytes'], 0)
        record = self.allocation_slot('A1', 'A')['record']
        pending_context = copy.deepcopy(self.state()['control']['context'])
        pending = pending_context['handoff_progress']['executions'][0]
        with self.assertRaises(progress.control.ControlError):
            progress.control.transition({'schema_version': 7, 'actor_ref': 'task', 'context': pending_context,
                'action': 'assign', 'evidence': {'agent_ref': 'native:replacement', 'task_id': pending['task_id'],
                    'allocation_digest': pending['allocation_digest'], 'write_release': 'new-release'}})
        with self.assertRaises(progress.entry.PreparationError):
            progress.dispatch.validate_allocation_receipt(record, 'bind', old_receipt, pending_context)
        original_execution = original_context['handoff_progress']['executions'][0]
        repair_evidence = {key: original_execution[key] for key in ('task_id', 'paths', 'read_only', 'behavior',
            'tests', 'git_operations', 'plan_refs', 'plan_authority', 'group_digest')}
        repair_evidence.update(configuration=request['configuration'], repair={
            'agent_ref': 'native:executor-A1', 'allocation_digest': original_execution['allocation_digest'],
            'stop_ref': 'host:result:stopped', 'invocation_id': 'control-negative'})
        original_execution.update(state='assigned', stopped=False)
        with self.assertRaises(progress.control.ControlError):
            progress.control.transition({'schema_version': 7, 'actor_ref': 'task', 'context': original_context,
                'action': 'repair-execution', 'evidence': repair_evidence})
        rejected = self.invoke('allocation', {'allocation_id': 'A1', 'operation': 'bind', 'receipt': old_receipt})
        self.assertEqual(rejected['status'], 'blocked', rejected)
        slot = self.allocation_slot('A1', 'A')
        continuation = self.receipt(slot['record'], ref='native:executor-A1')
        continuation['receipt_ref'] = 'host:create:repair-A1-first'
        bound = self.invoke('allocation', {'allocation_id': 'A1', 'operation': 'bind',
            'receipt': continuation})
        self.assertIsNotNone(bound['next_action'], bound)
        release = bound['next_action']['result']['write_release']
        self.assertNotEqual(release, progress.dispatch.execution_release_token(progress.handoff.unseal(old_record)))
        execution = self.state()['control']['context']['handoff_progress']['executions'][0]
        with self.assertRaisesRegex(progress.control.ControlError, 'result authority or release changed'):
            progress.control.transition({'schema_version': 7, 'actor_ref': 'task',
                'context': self.state()['control']['context'], 'action': 'execution-result', 'evidence': {
                    'agent_ref': 'native:executor-A1', 'stopped': True, 'git_unchanged': True,
                    'changed_paths': ['a1.py'], 'file_hashes': {'a1.py': 'a' * 64}, 'tests': ['repair check'],
                    'plan_refs': execution['plan_refs'], 'plan_authority': execution['plan_authority'],
                    'write_release': progress.dispatch.execution_release_token(progress.handoff.unseal(old_record))}})
        self.assertEqual(execution['git_snapshot']['head'], candidate)
        body = b'def run():\n    return 2\n'
        (self.flow / 'a1.py').write_bytes(body)
        slot = self.allocation_slot('A1', 'A')
        stopped = self.receipt(slot['record'], 'stopped', 'result', ref='native:executor-A1')
        stopped['receipt_ref'] = 'host:result:repair-A1-first'
        received = self.invoke('allocation', {'allocation_id': 'A1', 'operation': 'receive',
            'receipt': stopped,
            'result': {'delivery_id': 'A1-repair-result', 'status': 'completed', 'payload': {
                'changed_paths': ['a1.py'], 'file_hashes': {'a1.py': hashlib.sha256(body).hexdigest()},
                'tests': ['repair behavior passed'], 'write_release': release,
                'plan_refs': request['semantic']['plan_refs'], 'plan_authority': execution['plan_authority']}}})
        self.assertEqual(received['next_action']['result']['status'], 'received', received)
        accepted = self.invoke('allocation', {'allocation_id': 'A1', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accept-A1-repair'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['executions'][0]['task_id'],
                         execution['task_id'])
        metrics = self.allocation_slot('A1', 'A')['metrics']
        self.assertEqual(metrics['slice_id'], 'A1')
        self.assertEqual(metrics['submissions'], 2)
        self.assertEqual(metrics['first_submission']['sequence'], 1)
        self.assertEqual(metrics['first_submission']['receipt_ref'], 'host:result:stopped')

    def layered_flow(self, *, shared_verification=False, same_file=False, shared_read=False, unknown_shared=False, early_checks=True):
        self.shared_verification = shared_verification
        self.same_file = same_file
        self.shared_read = shared_read
        self.implementation_paths = ['a1.py', *([] if same_file else ['a2.py']), 'b1.py', 'b2.py']
        self.planning_documents = {'docs/spec.md':
            'approved design\n## Implementation Decisions\n### D1 — Plan\nTwo independent Tickets.\n'}
        for ticket in ('A', 'B'):
            plan = {'schema_version': 2, 'ticket_id': ticket, 'plan_version': 1,
                'spec_ref': {'path': 'docs/spec.md', 'section': 'Implementation Decisions'},
                'contract_refs': [ticket + ':What to build', ticket + ':AC-1', ticket + ':AC-2', 'PRD:D1:Plan'],
                'blocked_by': [], 'verifications': [], 'steps': [], 'artifact_steps': [],
                'behavior_slices': []}
            for number in (1, 2):
                step, verify, ac = ticket + str(number), 'V-' + ticket + str(number), ticket + ':AC-' + str(number)
                plan['verifications'].append({'id': verify, 'behavior': ac,
                    'seam': 'original workflow_progress.handle',
                    'commands': ['python3 scripts/run_tests.py --test-file tests/runtime/test_layered_review.py']})
                plan['steps'].append({'id': step, 'target': {'path': ticket.lower() + str(number) + '.py',
                    'symbol': 'run', 'operation': 'add'}, 'capability': ac, 'approach': ['implement run'],
                    'inputs': ['frozen requirement'], 'outputs': ['observed result'], 'failures': ['invalid input fails'],
                    'depends_on': [], 'verification_refs': [verify], 'read_only': []})
                plan['behavior_slices'].append({'id': step, 'step_ids': [step], 'ac_refs': [ac],
                    'verification_refs': [verify], 'read_only': [], 'shared_state': []})
                if same_file and ticket == 'A' and number == 2:
                    plan['steps'][-1]['target'].update(path='a1.py', symbol='neighbor')
                    plan['steps'][-1]['depends_on'] = ['A1']
                if shared_read and ticket == 'B':
                    plan['steps'][-1]['read_only'] = ['a1.py']
                    plan['behavior_slices'][-1].update(read_only=['a1.py'], shared_state=['a1.py'])
                if unknown_shared and ticket == 'A' and number == 1:
                    plan['behavior_slices'][-1]['shared_state'] = ['unmapped-shared-registry']
                if shared_verification and ticket == 'A' and number == 1:
                    shared = copy.deepcopy(plan['verifications'][-1])
                    shared['id'] = verify + '-shared'
                    plan['verifications'].append(shared)
                    plan['steps'][-1]['verification_refs'].append(shared['id'])
                    plan['behavior_slices'][-1]['verification_refs'].append(shared['id'])
            self.planning_documents['.scratch/layered/issues/' + ticket + '.md'] = (
                'Ticket ID: ' + ticket + '\nPlan Version: 1\n**What to build:** independent behavior\n'
                '**Blocked by:** None\nAC-1: first result\nAC-2: second result\n'
                '```execution-plan-v2\n' + json.dumps(plan) + '\n```\n')
        self.input['scope']['owned_paths'] = sorted(self.planning_documents)
        self.input['authorization']['scope_digest'] = fixture.transfer.entry.digest(self.input['scope'])
        design = self.finish_design('continuous')
        self.context = self.context_for(3)
        self.begin('continuous', self.staged_successor(design))
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        catalog = self.state()['scope_authority']['execution_plan']['catalog']
        for ticket in catalog['tickets']:
            for step in ticket['plan']['steps']:
                identity, path = step['id'], step['target']['path']
                request = self.execution_input()
                request['scope']['owned_paths'] = [path]
                request['scope']['protected_paths'] = sorted(set(request['scope']['protected_paths']) | set(step['read_only']))
                request['authorization']['scope_digest'] = fixture.transfer.entry.digest(request['scope'])
                selected = ([ticket['plan']['steps'][0], step] if same_file and identity == 'A2' else [step])
                request['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, item) for item in selected]
                self.invoke('allocation', {'allocation_id': identity, 'operation': 'prepare', 'handoff': request})
                slot = self.allocation_slot(identity, ticket['ticket_id'])
                native = 'native:executor-' + identity
                bound = self.invoke('allocation', {'allocation_id': identity, 'operation': 'bind',
                    'receipt': self.receipt(slot['record'], ref=native)})
                release = bound['next_action']['result']['write_release']
                body = b'def run():\n    return True\n'
                if same_file and identity == 'A2':
                    body = (self.flow / path).read_bytes() + b'\ndef neighbor():\n    return 1\n'
                if shared_read and ticket['ticket_id'] == 'B':
                    body = b'import a1\n\ndef run():\n    return a1.run()\n'
                (self.flow / path).write_bytes(body)
                slot = self.allocation_slot(identity, ticket['ticket_id'])
                received = self.invoke('allocation', {'allocation_id': identity, 'operation': 'receive',
                    'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=native),
                    'result': {'delivery_id': identity + '-result', 'status': 'completed', 'payload': {
                        'changed_paths': [path], 'file_hashes': {path: hashlib.sha256(body).hexdigest()},
                        'tests': ['focused behavior passed'], 'write_release': release,
                        'plan_refs': request['semantic']['plan_refs'],
                        'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
                self.assertEqual(received['next_action']['result']['status'], 'received', received)
                accepted = self.invoke('allocation', {'allocation_id': identity, 'operation': 'accept',
                    'decision': {'reference': 'dispatcher:accept-' + identity}})
                self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
                due = accepted['next_action'].get('required_ticket_checks', [])
                if due:
                    self.settings['thread_id'] = 'task'
                    self.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
                    self.flow_git('add', *[path for path in self.implementation_paths if (self.flow / path).is_file()])
                    self.flow_git('commit', '-qm', 'integrated Ticket slices')
                    combined = self.flow_git('rev-parse', 'HEAD')
                    if not early_checks:
                        return combined
                    for due_ticket in due:
                        checked = self.early_ticket_check(combined, due_ticket)
                        self.assertTrue(checked['next_action']['result']['ok'], checked)
                    self.settings['thread_id'] = 'native:dispatcher'
                    self.request['host'].update(thread_id='native:dispatcher', role='implementation-dispatcher',
                        source_ref='task', actor_ref='native:dispatcher')
        self.settings['thread_id'] = 'task'
        self.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        self.flow_git('add', *[path for path in self.implementation_paths if (self.flow / path).is_file()])
        if self.flow_git('status', '--porcelain'):
            self.flow_git('commit', '-qm', 'four real execution deliveries')
        candidate = self.flow_git('rev-parse', 'HEAD')
        return candidate

    def early_ticket_check(self, candidate, ticket):
        checkpoint = self.state()['control']['context']['handoff_progress']
        required = [check for check in checkpoint['validation_plan']['review_required'] if check['category'] == 'affected']
        checks = fixture.transfer.checks_for(required, candidate)
        return self.invoke('control', {'action': 'ticket-check', 'evidence': {'ticket_id': ticket,
            'candidate': candidate, 'plan_authority': checkpoint['plan_authority'], 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'early:' + ticket + ':' + candidate,
                'response_ref': 'early-stopped:' + ticket + ':' + candidate,
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': 'native:dispatcher'}}})

    def test_early_ticket_checks_reject_fixture_mismatch_before_any_local_review(self):
        candidate = self.layered_flow(early_checks=False)
        request = {'operation': 'prepare', 'axis': 'local:A:A1', 'candidate': candidate,
            'actor_ref': 'task', 'verification': self.local_verification(candidate)}
        with self.assertRaises(progress.entry.PreparationError) as blocked:
            self.invoke('review-activity', request)
        self.assertEqual(blocked.exception.code, 'ticket_check_required')
        self.assertNotIn('local:A:A1', self.state().get('review_activity', {}))
        fresh = self.execution_input()
        fresh['scope']['owned_paths'] = ['b1.py']
        fresh['authorization']['scope_digest'] = progress.entry.digest(fresh['scope'])
        catalog = self.state()['scope_authority']['execution_plan']['catalog']
        ticket = next(item for item in catalog['tickets'] if item['ticket_id'] == 'B')
        fresh['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, ticket['plan']['steps'][0])]
        denied = self.invoke('allocation', {'allocation_id': 'B1', 'operation': 'prepare', 'handoff': fresh})
        self.assertEqual(denied['error']['code'], 'ticket_check_required', denied)
        self.assertNotIn('B1', self.state()['allocations'])
        self.settings['thread_id'] = 'task'
        self.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        checkpoint = self.state()['control']['context']['handoff_progress']
        required = [check for check in checkpoint['validation_plan']['review_required'] if check['category'] == 'affected']
        checks = fixture.transfer.checks_for(required, candidate)
        checks[0].update(status='failed', exit_code=1)
        failed = self.invoke('control', {'action': 'ticket-check', 'evidence': {'ticket_id': 'A',
            'candidate': candidate, 'plan_authority': checkpoint['plan_authority'], 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'fixture-mismatch', 'response_ref': 'fixture-mismatch-stop',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(failed['status'], 'blocked', failed)
        self.assertNotIn('A', self.state()['control']['context']['handoff_progress'].get('ticket_checks', {}))
        passed = self.early_ticket_check(candidate, 'A')
        self.assertTrue(passed['next_action']['result']['ok'], passed)
        self.local_review(candidate)

    def local_verification(self, candidate, ticket='A', slice_id='A1'):
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = [{'id': 'V-' + slice_id + ':1', 'category': 'focused',
            'command': 'python3 scripts/run_tests.py --test-file tests/runtime/test_layered_review.py',
            'cwd': str(self.flow), 'pass_condition': 'exit 0', 'allowed_skips': [],
            'environment': None, 'status': 'passed', 'exit_code': 0,
            'start_commit': candidate, 'end_commit': candidate, 'environment_fingerprint': None,
            'output_ref': 'native-output:' + slice_id, 'output_digest': 'c' * 64}]
        if self.shared_verification and ticket == 'A' and slice_id == 'A1':
            shared = copy.deepcopy(checks[0])
            shared['id'] = 'V-A1-shared:1'
            checks.append(shared)
        source = {'adapter': 'fixture-command', 'call_ref': 'regression-call:' + slice_id,
            'response_ref': 'regression-stopped:' + slice_id,
            'raw': {'checks': copy.deepcopy(checks), 'source_unchanged': True,
                'stopped': True, 'dispatcher_ref': self.state()['control']['context']['carrier']['ref'], 'candidate': candidate,
                'run_id': progress.read_record(self.checkpoint)['current']['run_id'],
                'package_digest': fixture.transfer.entry.digest(self.state()['packages']),
                'plan_authority': copy.deepcopy(checkpoint['plan_authority']),
                'environment': {'adapter': 'fixture-command', 'cwd': str(self.flow)},
                'fixture': {'slice': slice_id, 'candidate': candidate}}}
        return {'plan_authority': checkpoint['plan_authority'], 'checks': checks, 'source': source}

    def local_review(self, candidate, ticket='A', slice_id='A1', reviewer=None, *, raw_padding=0, read_paths=None, expect_rejected=False):
        checkpoint = self.state()['control']['context']['handoff_progress']
        axis = 'local:' + ticket + ':' + slice_id
        verification = self.local_verification(candidate, ticket, slice_id)
        prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis,
            'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-review', prepared)
        semantic = {'status': 'accepted', 'findings': [], 'read_paths': prepared['next_action']['review_scope']['read_paths'] if read_paths is None else read_paths,
                    'ac_refs': [ticket + ':AC-' + slice_id[-1]], 'checks': verification['checks']}
        semantic.update(dependency_claim_ids=prepared['next_action']['review_scope']['dependency_claim_ids'])
        response_ref = 'local-stopped:' + slice_id + ':' + candidate
        observed = self.invoke('review-activity', {'operation': 'observe', 'axis': axis,
            'candidate': candidate, 'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'],
            'receipt': {'adapter': 'fixture-native', 'call_ref': 'local-call:' + slice_id + ':' + candidate,
                'response_ref': response_ref, 'status': 'stopped', 'ref': reviewer or 'native:reviewer-' + ticket,
                'raw': {'review': semantic, **({'padding': 'x' * raw_padding} if raw_padding else {})}}})
        if expect_rejected and observed['status'] == 'blocked':
            # advance may retry the preceding rejected local request. Always
            # consume this axis's own stopped response below as well.
            self.assertEqual(observed['error']['code'], 'review_incomplete', observed)
        else:
            self.assertNotEqual(observed['status'], 'blocked', observed)
        if raw_padding:
            slot = self.state()['review_activity'][axis]
            retained = slot['receipts'][-1]['raw']
            manifest = progress.state_store.StateStore(self.checkpoint).read_raw_manifest(
                retained['evidence_ref'], run_id=retained['evidence_ref']['run_id'])
            self.assertTrue(manifest['complete'])
            self.assertFalse(retained['truncated'])
            self.assertGreater(manifest['size'], raw_padding)
            self.assertLess(manifest['size'], progress.state_store.EVIDENCE_LIMIT)
        result = self.invoke('control', {'action': 'local-review', 'evidence': {
            'axis': axis, 'candidate': candidate, 'result_ref': response_ref},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:local-' + slice_id + ':' + candidate}})
        if expect_rejected:
            self.assertEqual(result['status'], 'blocked', result)
            self.assertEqual(result['error']['code'], 'review_incomplete', result)
            self.assertIn('reads', result['error']['message'])
            self.assertNotIn(axis, self.state()['control']['context']['handoff_progress'].get('review_claims', {}))
            self.assertNotIn('coverage', self.state()['control']['context']['handoff_progress'])
            return result
        self.assertIsNotNone(result['next_action'], result)
        self.assertTrue(result['next_action']['result']['ok'], result)
        return result['next_action']['result']['claim']

    def test_large_complete_local_report_consumes_committed_semantics_within_budget(self):
        candidate = self.layered_flow()
        original_read = progress.state_store.StateStore._read
        original_spool = progress.state_store.StateStore.write_raw_evidence
        accounts, spools = {}, []
        def measured_read(store, path, maximum):
            caller = sys._getframe(1).f_code.co_name
            data = original_read(store, path, maximum)
            account = accounts.setdefault(store, {'metadata': 0, 'raw_chunks': 0})
            category = 'raw_chunks' if maximum == 1024 * 1024 and caller in {'flush', 'read_raw_chunk', 'read_raw_evidence'} else 'metadata'
            account[category] += len(data)
            return data
        def measured_spool(store, run_id, value, **kwargs):
            result = original_spool(store, run_id, value, **kwargs)
            spools.append({'raw_bytes': result[1]['size'], 'retained_bytes': result[1]['retained_size'],
                'complete': result[1]['complete'], 'native_action_id': result[1]['native_receipt']['action_id']})
            return result
        with fixture.patch.object(progress.state_store.StateStore, '_read', measured_read), \
             fixture.patch.object(progress.state_store.StateStore, 'write_raw_evidence', measured_spool):
            for ticket in ('A', 'B'):
                for number in (1, 2):
                    claim = self.local_review(candidate, ticket, ticket + str(number), raw_padding=8 * 1024 * 1024)
                    self.assertEqual(claim['layer'], 'local')
                self.ticket_review(candidate, ticket, raw_padding=8 * 1024 * 1024)
            reviews = self.delivery_reviews(candidate, raw_padding=8 * 1024 * 1024)
            self.finish_certificate_delivery(candidate, reviews)
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        metrics = {'root_bytes': current.reference['size'],
            'subject_bytes': {key: value['size'] for key, value in current.root['subjects'].items()},
            'maximum_operation_read_bytes': max(sum(value.values()) for value in accounts.values()),
            'maximum_metadata_read_bytes': max(value['metadata'] for value in accounts.values()),
            'maximum_raw_read_bytes': max(value['raw_chunks'] for value in accounts.values()),
            'read_limit': progress.state_store.READ_LIMIT, 'raw_limit': progress.state_store.EVIDENCE_LIMIT,
            'combined_limit': progress.state_store.READ_LIMIT + progress.state_store.EVIDENCE_LIMIT,
            'accounts': list(accounts.values()), 'spools': spools,
            'reports': 8, 'raw_padding_bytes_each': 8 * 1024 * 1024}
        self.assertLessEqual(metrics['root_bytes'], progress.state_store.ROOT_LIMIT)
        self.assertTrue(all(size <= progress.state_store.SUBJECT_LIMIT for size in metrics['subject_bytes'].values()))
        self.assertLessEqual(metrics['maximum_metadata_read_bytes'], metrics['read_limit'])
        self.assertLessEqual(metrics['maximum_raw_read_bytes'], metrics['raw_limit'])
        self.assertLessEqual(metrics['maximum_operation_read_bytes'], metrics['combined_limit'])
        self.assertEqual(len(spools), 8)
        self.assertEqual(len({row['native_action_id'] for row in spools}), 8)
        self.assertTrue(all(row['complete'] and row['retained_bytes'] == row['raw_bytes'] for row in spools))
        destination = fixture.os.environ.get('T02_SEMANTIC_BUDGET_METRICS')
        if destination:
            with Path(destination).open('x') as output:
                json.dump(metrics, output, indent=2)
                output.write('\n')

    def test_local_claim_blocks_unaccepted_ticket(self):
        candidate = self.layered_flow()
        claim = self.local_review(candidate)
        self.assertEqual(claim['layer'], 'local')
        self.assertEqual(claim['ticket_id'], 'A')
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['review_claims']['local:A:A1']['claim_id'], claim['claim_id'])
        plan = checkpoint['validation_plan']
        checks = fixture.transfer.checks_for(plan['review_required'], candidate)
        result = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher', 'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': checkpoint['git_baseline_commit'], 'binding': self.binding,
            'plan_digest': checkpoint['plan_digest'], 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'focused', 'response_ref': 'focused-stopped',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(result['status'], 'blocked', result)
        self.assertIn('Ticket acceptance', str(result))
        self.assertIsNone(self.state()['dispatch']['delivery'])
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'implementing')

    def test_local_review_rejects_failed_check_observations(self):
        candidate = self.layered_flow()
        verification = self.local_verification(candidate)
        verification['checks'][0].update(status='failed', exit_code=1)
        verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
        with self.assertRaises(progress.entry.PreparationError) as error:
            self.invoke('review-activity', {'operation': 'prepare', 'axis': 'local:A:A1',
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(error.exception.code, 'review_incomplete')
        self.assertNotIn('local:A:A1', self.state().get('review_activity', {}))
        self.assertNotIn('local:A:A1', self.state()['control']['context']['handoff_progress'].get('review_claims', {}))

    def test_local_review_rejects_stale_candidate_wrong_ref_and_command_source(self):
        candidate = self.layered_flow()
        old = self.state()['control']['context']['handoff_progress']['git_baseline_commit']
        variants = (
            ('stale candidate', lambda item: item['checks'][0].update(start_commit=old, end_commit=old)),
            ('wrong verification ref', lambda item: item['checks'][0].update(id='V-B1:1')),
            ('wrong frozen command', lambda item: item['checks'][0].update(command='python3 scripts/run_tests.py --test-file tests/runtime/test_solution_review_progress.py')),
            ('duplicate ref', lambda item: item['checks'].append(copy.deepcopy(item['checks'][0]))),
            ('unknown check', lambda item: item['checks'][0].update(status='unknown', exit_code=None)),
            ('old package source', lambda item: item['source']['raw'].update(package_digest='0' * 64)),
            ('other run source', lambda item: item['source']['raw'].update(run_id='other-run')),
            ('unstopped command', lambda item: item['source']['raw'].update(stopped=False)),
            ('old authority source', lambda item: item['source']['raw']['plan_authority'].update(revision=0)),
            ('missing original source', lambda item: item.pop('source')),
        )
        for label, mutate in variants:
            with self.subTest(label=label):
                verification = self.local_verification(candidate)
                mutate(verification)
                self.assertEqual(verification['plan_authority'],
                    self.state()['control']['context']['handoff_progress']['plan_authority'])
                if 'source' in verification:
                    verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
                with self.assertRaises(progress.entry.PreparationError) as error:
                    self.invoke('review-activity', {'operation': 'prepare', 'axis': 'local:A:A1',
                        'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
                self.assertEqual(error.exception.code, 'review_incomplete')
                self.assertNotIn('local:A:A1', self.state().get('review_activity', {}))
                self.assertNotIn('local:A:A1', self.state()['control']['context']['handoff_progress'].get('review_claims', {}))

    def test_shared_command_requires_each_frozen_verification_ref(self):
        candidate = self.layered_flow(shared_verification=True)
        verification = self.local_verification(candidate)
        verification['checks'].pop()
        verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
        with self.assertRaises(progress.entry.PreparationError) as error:
            self.invoke('review-activity', {'operation': 'prepare', 'axis': 'local:A:A1',
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(error.exception.code, 'review_incomplete')
        claim = self.local_review(candidate)
        self.assertEqual(claim['layer'], 'local')
        self.assertEqual(claim['ticket_id'], 'A')

    def ticket_review(self, candidate, ticket, *, unknown_first=False, raw_padding=0):
        first = self.local_verification(candidate, ticket, ticket + '1')
        second = self.local_verification(candidate, ticket, ticket + '2')
        first['checks'].extend(second['checks'])
        first['source']['raw']['checks'] = copy.deepcopy(first['checks'])
        first['source']['call_ref'] = 'ticket-check-call:' + ticket + ':' + candidate
        first['source']['response_ref'] = 'ticket-check-stop:' + ticket + ':' + candidate
        axis = 'ticket:' + ticket
        prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis,
            'candidate': candidate, 'actor_ref': 'task', 'verification': first})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-review', prepared)
        scope = prepared['next_action']['review_scope']
        receipt = {'adapter': 'fixture-native', 'call_ref': 'ticket-review-call:' + ticket + ':' + candidate,
            'response_ref': 'ticket-review-stop:' + ticket + ':' + candidate, 'status': 'stopped',
            'ref': 'native:reviewer-' + ticket,
            'raw': {'review': {'status': 'accepted', 'findings': [], 'read_paths': scope['read_paths'],
                'ac_refs': [ticket + ':AC-1', ticket + ':AC-2'], 'checks': first['checks'],
                'dependency_claim_ids': scope['dependency_claim_ids'],
                'requirement_refs': scope['requirement_refs'], 'review_scope': scope}}}
        if raw_padding:
            receipt['raw']['padding'] = 'x' * raw_padding
        if unknown_first:
            previous = copy.deepcopy(self.state()['control']['context']['handoff_progress']['review_claims'][axis])
            unknown = {**receipt, 'response_ref': receipt['response_ref'] + ':unknown',
                'status': 'unknown', 'ref': None, 'raw': {'status': 'unknown'}}
            self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'], 'receipt': unknown})
            denied = self.invoke('control', {'action': 'ticket-accept', 'evidence': {
                'axis': axis, 'candidate': candidate, 'result_ref': unknown['response_ref']},
                'receipt': {'controller_ref': 'task', 'reference': 'controller:unknown-ticket:' + candidate}})
            self.assertEqual(denied['status'], 'blocked', denied)
            self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims'][axis], previous)
        self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
            'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'], 'receipt': receipt})
        accepted = self.invoke('control', {'action': 'ticket-accept', 'evidence': {
            'axis': axis, 'candidate': candidate, 'result_ref': receipt['response_ref']},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:ticket-' + ticket + ':' + candidate}})
        self.assertTrue(accepted['next_action']['result']['ok'], accepted)
        return accepted['next_action']['result']['claim']

    def test_ticket_acceptance_rejects_missing_ac_self_author_and_unknown_stop(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
        for ticket, native in (('A', 'native:reviewer-A'), ('B', 'native:executor-B1')):
            verification = self.local_verification(candidate, ticket, ticket + '1')
            verification['checks'].extend(self.local_verification(candidate, ticket, ticket + '2')['checks'])
            verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
            axis = 'ticket:' + ticket
            prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis,
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
            scope = prepared['next_action']['review_scope']
            result_ref = 'negative-ticket-stop:' + ticket
            report = {'status': 'accepted', 'findings': [], 'read_paths': scope['read_paths'],
                'ac_refs': [ticket + ':AC-1'] if ticket == 'A' else scope['ac_refs'],
                'checks': verification['checks'], 'dependency_claim_ids': scope['dependency_claim_ids'],
                'requirement_refs': scope['requirement_refs'], 'review_scope': scope}
            receipt = {'adapter': 'fixture-native', 'call_ref': 'negative-ticket-call:' + ticket,
                'response_ref': result_ref, 'status': 'stopped', 'ref': native, 'raw': {'review': report}}
            observed = self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'], 'receipt': receipt})
            rejected = self.invoke('control', {'action': 'ticket-accept', 'evidence': {
                'axis': axis, 'candidate': candidate, 'result_ref': receipt['response_ref']},
                'receipt': {'controller_ref': 'task', 'reference': 'negative-ticket-settled:' + ticket}})
            self.assertEqual(rejected['status'], 'blocked', rejected)
            self.assertEqual(rejected['error']['code'], 'review_incomplete' if ticket == 'A' else 'identity_mismatch')
            print(json.dumps({'native_ticket_negative': ticket, 'error': rejected['error'],
                'ticket_granted': axis in self.state()['control']['context']['handoff_progress'].get('review_claims', {})}, sort_keys=True))
            self.assertNotIn(axis, self.state()['control']['context']['handoff_progress'].get('review_claims', {}))

    def test_ticket_and_delivery_certificate_preserve_unchanged_ticket_claims(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
        accepted_a = self.ticket_review(candidate, 'A')
        accepted_b = self.ticket_review(candidate, 'B')
        self.assertEqual(accepted_a['layer'], 'ticket')
        self.assertEqual(accepted_b['layer'], 'ticket')
        review = self.delivery_reviews(candidate)
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertTrue(checkpoint['coverage']['delivery'])
        old_start = {'attempt_id': 'failed-original-final', 'dispatcher_ref': 'native:dispatcher',
            'carrier_attempt': self.state()['control']['context']['carrier']['attempt'],
            **{key: checkpoint[key] for key in ('candidate', 'expected_target_head', 'plan_digest', 'plan_authority', 'review_digest')}}
        self.invoke('control', {'action': 'validation-start', 'evidence': old_start,
            'receipt': {'controller_ref': 'task', 'reference': 'controller:failed-original-final'}})
        failed_checks = fixture.transfer.checks_for(checkpoint['validation_plan']['final_required'], candidate)
        failed_checks[0].update(status='failed', exit_code=1)
        failed = self.invoke('control', {'action': 'validation-result',
            'evidence': {'attempt_id': old_start['attempt_id'], 'checks': failed_checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'original-final-call', 'response_ref': 'original-final-stop',
                'raw': {'attempt_id': old_start['attempt_id'], 'checks': failed_checks, 'source_unchanged': True,
                    'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'final-validation-failed', failed)
        original_b = copy.deepcopy(self.allocation_slot('B1', 'B')['metrics'])
        retained_b = copy.deepcopy(checkpoint['review_claims']['ticket:B'])
        old_b_claim = progress.state_store.StateStore(self.checkpoint).read_evidence(retained_b['claim_ref'],
            run_id=retained_b['claim_ref']['run_id'])['value']['review_claim']
        retired = self.invoke('control', {'action': 'invalidate-candidate', 'evidence': {
            'candidate': candidate, 'reason': 'legal local A repair', 'reference': 'controller:repair-A'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:repair-A'}})
        self.assertTrue(retired['next_action']['result']['ok'], retired)
        self.repair_a(original=True)
        self.assertEqual(self.allocation_slot('B1', 'B')['metrics'], original_b)
        self.assertEqual(self.allocation_slot('A1', 'A')['metrics']['slice_id'], 'A1')
        target = self.flow_git('rev-parse', 'HEAD')
        applied = self.invoke('control', {'action': 'review-applicability', 'evidence': {
            'candidate': target, 'reference': 'controller:target-applicability'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:target-applicability'}})
        proof = applied['next_action']['result']['applicability']
        self.assertIn(retained_b['claim_id'], proof['retained'])
        self.assertIn(accepted_a['claim_id'], proof['affected'])
        self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims']['ticket:B'], retained_b)
        current_b_claim = progress.state_store.StateStore(self.checkpoint).read_evidence(retained_b['claim_ref'],
            run_id=retained_b['claim_ref']['run_id'])['value']['review_claim']
        self.assertEqual(current_b_claim, old_b_claim)
        self.assertEqual(current_b_claim['native_stop_ref'], old_b_claim['native_stop_ref'])
        self.local_review(target, 'A', 'A1')
        self.ticket_review(target, 'A')
        reviews = self.delivery_reviews(target)
        self.inherit_empty_candidate(target, reviews)

    def test_layered_coverage_git_cli_consumes_original_proofs(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
            self.ticket_review(candidate, ticket)
        original = progress.control_git.verified_transition
        envelopes = []
        executable = Path(progress.control_git.__file__)

        def actual_cli(envelope):
            if envelope['request']['action'] not in {'candidate-ready', 'review-converged'}:
                return original(envelope)
            envelopes.append(copy.deepcopy(envelope))
            result = subprocess.run([sys.executable, str(executable)], input=json.dumps(envelope),
                text=True, capture_output=True, cwd=self.flow)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

        with fixture.patch.object(progress.control_git, 'verified_transition', side_effect=actual_cli):
            self.delivery_reviews(candidate)
        self.assertEqual([item['request']['action'] for item in envelopes], ['candidate-ready', 'review-converged'])
        self.assertTrue(self.state()['control']['context']['handoff_progress']['coverage']['delivery'])
        before = self.checkpoint.read_bytes()
        stale = subprocess.run([sys.executable, str(executable)], input=json.dumps(envelopes[-1]),
            text=True, capture_output=True, cwd=self.flow)
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        self.assertIn('archived execution facts differ from the original Controller checkpoint',
                      json.loads(stale.stdout)['error'])
        self.assertNotIn('Traceback', stale.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        bad = copy.deepcopy(envelopes[-1])
        bad['request']['context'] = self.state()['control']['context']
        bad['request']['evidence']['coverage']['certificate_digest'] = '0' * 64
        before = self.checkpoint.read_bytes()
        rejected = subprocess.run([sys.executable, str(executable)], input=json.dumps(bad),
            text=True, capture_output=True, cwd=self.flow)
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
        self.assertFalse(json.loads(rejected.stdout)['ok'])
        self.assertNotIn('Traceback', rejected.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        store = progress.state_store.StateStore(self.checkpoint)
        view = envelopes[-1]['request']['evidence']['claims']['standards']
        claim = store.read_evidence(view['claim_ref'], run_id=view['claim_ref']['run_id'])['value']['review_claim']
        for forged_issuer, expected_error in ((False, 'original review issuer differs'),
                                            (True, 'original review issuer differs')):
            forged = copy.deepcopy(claim)
            source = store.read_evidence(claim['verification']['source_ref'],
                run_id=claim['run_id'])['value']['local_check_source']
            source['raw']['dispatcher_ref'] = 'native:forged-dispatcher'
            authority = store.read_evidence(claim['verification']['source_authority_ref'],
                run_id=claim['run_id'])['value']['review_source_authority']
            if forged_issuer:
                authority['dispatcher_ref'] = 'native:forged-dispatcher'
            forged['verification']['source_ref'] = store.write_object(claim['run_id'], 'evidence', {'local_check_source': source})
            forged['verification']['source_digest'] = fixture.transfer.entry.digest(source)
            authority.update(source_ref=forged['verification']['source_ref'],
                source_digest=forged['verification']['source_digest'])
            forged['verification']['source_authority_ref'] = store.write_object(claim['run_id'], 'evidence',
                {'review_source_authority': authority})
            forged['verification']['source_authority_digest'] = fixture.transfer.entry.digest(authority)
            forged['claim_id'] = fixture.transfer.entry.digest({key: value for key, value in forged.items() if key != 'claim_id'})
            claim_ref = store.write_object(claim['run_id'], 'evidence', {'review_claim': forged})
            hostile = copy.deepcopy(envelopes[-1])
            hostile['request']['context'] = self.state()['control']['context']
            hostile['request']['evidence']['claims']['standards'] = progress.review_evidence.claim_view(forged, claim_ref)
            blocked = subprocess.run([sys.executable, str(executable)], input=json.dumps(hostile),
                text=True, capture_output=True, cwd=self.flow)
            self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
            self.assertIn(expected_error, json.loads(blocked.stdout)['error'])
            self.assertNotIn('Traceback', blocked.stderr)
            self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_recovered_dispatcher_consumes_original_layered_claims(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
            self.ticket_review(candidate, ticket)
        self.delivery_reviews(candidate)
        before = copy.deepcopy(self.state()['control']['context']['handoff_progress']['review_claims'])
        self.recover_layered_dispatcher()
        self.assertEqual(self.state()['control']['context']['carrier']['ref'], 'native:dispatcher-D2')
        self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims'], before)
        applicable = self.invoke('control', {'action': 'review-applicability', 'evidence': {
            'candidate': candidate, 'reference': 'controller:recovered-applicability'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:recovered-applicability'}})
        self.assertNotEqual(applicable['status'], 'blocked', applicable)
        self.assertEqual(set(applicable['next_action']['result']['applicability']['retained']),
            {view['claim_id'] for view in before.values()})
        store = progress.state_store.StateStore(self.checkpoint)
        old_claim = store.read_evidence(before['ticket:B']['claim_ref'],
            run_id=before['ticket:B']['claim_ref']['run_id'])['value']['review_claim']
        authority = store.read_evidence(old_claim['verification']['source_authority_ref'],
            run_id=old_claim['run_id'])['value']['review_source_authority']
        self.assertEqual(authority['dispatcher_ref'], 'native:dispatcher')
        self.assertNotEqual(authority['carrier_attempt'], self.state()['control']['context']['carrier']['attempt'])
        digest = old_claim['verification']['source_ref']['sha256']
        original_source_file = store.directory / 'objects' / digest[:2] / digest[2:4] / digest
        original_source = original_source_file.read_bytes()
        try:
            original_source_file.write_bytes(b'corrupt original stopped command source')
            damaged = self.invoke('control', {'action': 'review-applicability', 'evidence': {
                'candidate': candidate, 'reference': 'controller:damaged-old-source'},
                'receipt': {'controller_ref': 'task', 'reference': 'controller:damaged-old-source'}})
            self.assertEqual(damaged['status'], 'blocked', damaged)
        finally:
            original_source_file.write_bytes(original_source)
        self.flow_git('commit', '--allow-empty', '-qm', 'recovered target candidate')
        target = self.flow_git('rev-parse', 'HEAD')
        verification = self.local_verification(target, 'A', 'A1')
        verification['checks'].extend(self.local_verification(target, 'A', 'A2')['checks'])
        verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
        old_actor = copy.deepcopy(verification)
        old_actor['source']['raw']['dispatcher_ref'] = 'native:dispatcher'
        with self.assertRaises(fixture.transfer.entry.PreparationError):
            self.invoke('review-activity', {'operation': 'prepare', 'axis': 'ticket:A',
                'candidate': target, 'actor_ref': 'task', 'verification': old_actor})
        accepted_a = self.ticket_review(target, 'A', unknown_first=True)
        self.assertEqual(accepted_a['source_candidate'], target)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims']['ticket:B'], before['ticket:B'])
        reviews = self.delivery_reviews(target)
        self.finish_certificate_delivery(target, reviews)

    def test_git_cli_rejects_whole_unprepared_native_claim_artifact_set(self):
        candidate = self.layered_flow()
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
            self.ticket_review(candidate, ticket)
        self.candidate_ready(candidate)
        for axis in ('standards', 'spec'):
            checkpoint = self.state()['control']['context']['handoff_progress']
            self.invoke('review-activity', {'operation': 'prepare', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'verification': {'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
                    'checks': checkpoint['tests'], 'plan_authority': checkpoint['plan_authority']}})
        owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        context = owner.state['control']['context']
        checkpoint, store = context['handoff_progress'], progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        source = copy.deepcopy(checkpoint['candidate_evidence']['source'])
        views, reports, stops = {}, {}, {}
        for axis in ('standards', 'spec'):
            slot = owner.state['review_activity'][axis]
            action_id = 'never-prepared-native-action:' + axis
            stop = {'adapter': 'fixture-native', 'call_ref': 'never-issued-call:' + axis,
                'response_ref': 'never-observed-stop:' + axis, 'status': 'stopped',
                'ref': 'native:never-issued-' + axis, 'raw': {'review': {
                    'status': 'accepted', 'findings': [], 'ac_refs': owner.review_scope(axis)['ac_refs'],
                    'dependency_claim_ids': owner.review_scope(axis)['dependency_claim_ids'],
                    'requirement_refs': owner.review_scope(axis)['requirement_refs'], 'review_scope': owner.review_scope(axis)}}}
            stop_ref = store.write_object(current.run_id, 'evidence', {'native_review_stop': stop})
            source_ref = store.write_object(current.run_id, 'evidence', {'local_check_source': source})
            issuer = {'dispatcher_ref': context['carrier']['ref'], 'carrier_attempt': context['carrier']['attempt'],
                'request_digest': fixture.transfer.entry.digest(slot['prepare']), 'native_action_id': action_id,
                'run_id': current.run_id, 'package_digest': fixture.transfer.entry.digest(owner.state['packages']),
                'plan_authority': checkpoint['plan_authority'], 'source_ref': source_ref,
                'source_digest': fixture.transfer.entry.digest(source)}
            issuer_ref = store.write_object(current.run_id, 'evidence', {'review_issuer': issuer})
            authority = {**issuer, 'native_stop_ref': stop_ref, 'candidate': candidate,
                'issuer_ref': issuer_ref, 'issuer_digest': fixture.transfer.entry.digest(issuer),
                'originating_ref': 'task', 'stage_subject': owner.state['stage_subject'],
                'prepare_request_ref': store.write_object(current.run_id, 'evidence', slot['prepare']),
                'stop_request_ref': store.write_object(current.run_id, 'evidence', owner.review_receipt_request({
                    'operation': 'observe', 'axis': axis, 'candidate': candidate, 'actor_ref': 'task',
                    'action_id': action_id, 'receipt': stop}))}
            authority_ref = store.write_object(current.run_id, 'evidence', {'review_source_authority': authority})
            required = checkpoint['validation_plan']['review_required']
            claim = {'schema_version': 1, 'run_id': current.run_id, 'package_digest': issuer['package_digest'],
                'layer': 'delivery', **owner.delivery_review_subject(candidate, axis),
                'plan_authority': checkpoint['plan_authority'], 'source_candidate': candidate,
                'verification': {'ids': [item['id'] for item in required], 'required': required,
                    'checks': checkpoint['tests'], 'source_results': [], 'source_ref': source_ref,
                    'source_digest': issuer['source_digest'], 'environment_digest': fixture.transfer.entry.digest(source['raw'].get('environment')),
                    'fixture_digest': fixture.transfer.entry.digest(source['raw'].get('fixture')),
                    'source_authority_ref': authority_ref, 'source_authority_digest': fixture.transfer.entry.digest(authority)},
                'reviewer_ref': stop['ref'], 'native_stop': {**{key: stop[key] for key in
                    ('adapter', 'call_ref', 'response_ref', 'status', 'ref')}, 'action_id': action_id},
                'native_stop_ref': stop_ref, 'findings': [], 'status': 'accepted'}
            claim['claim_id'] = fixture.transfer.entry.digest(claim)
            ref = store.write_object(current.run_id, 'evidence', {'review_claim': claim})
            views[axis] = progress.review_evidence.claim_view(claim, ref)
            reports[axis] = {'candidate': candidate, 'expected_target_head': checkpoint['expected_target_head'],
                'plan_digest': checkpoint['plan_digest'], 'plan_authority': checkpoint['plan_authority'],
                'reviewer_ref': stop['ref'], 'status': 'accepted', 'result_ref': stop['response_ref']}
            stops[axis] = stop
        checkpoint['review_claims'].update(views)
        # Adversarial input construction uses pure graph functions; permission
        # is exercised only by the actual CLI against the original C store.
        claims, targets = {}, {}
        for axis, view in checkpoint['review_claims'].items():
            claims[axis] = store.read_evidence(view['claim_ref'], run_id=current.run_id)['value']['review_claim']
            target = (owner.delivery_review_subject(candidate, axis) if axis in {'standards', 'spec'} else
                owner.local_review_subject({'axis': axis, 'candidate': candidate}))
            target.pop('verification_refs', None)
            target['required'] = (checkpoint['validation_plan']['review_required'] if axis in {'standards', 'spec'} else
                progress.review_evidence.local_required_checks(checkpoint['execution_authority']['execution_plan']['catalog'], axis, str(self.flow)))
            target['known_paths'] = checkpoint['allowed_paths'] + checkpoint['protected_paths']
            target['missing_dependencies'] = False
            targets[axis] = target
        applications = progress.review_evidence.applicable_claims(claims, targets, candidate, checkpoint['plan_authority'], [])
        for axis, application in applications.items():
            applications[axis] = {**application, 'claim_ref': checkpoint['review_claims'][axis]['claim_ref'],
                'applicability_ref': store.write_object(current.run_id, 'evidence', {'review_applicability': application})}
        certificate = progress.review_evidence.coverage_certificate(checkpoint['execution_authority']['execution_plan']['catalog'],
            claims, applications, candidate, checkpoint['plan_authority'], checkpoint['allowed_paths'], delivery=True)
        certificate_ref = store.write_object(current.run_id, 'evidence', {'review_coverage': certificate})
        coverage = progress.review_evidence.certificate_view(certificate, certificate_ref, str(owner.path))
        envelope = {'repository': str(self.flow), 'baseline': checkpoint['git_baseline_commit'], 'request': {
            'schema_version': 7, 'action': 'review-converged', 'actor_ref': 'task',
            'context': self.state()['control']['context'], 'evidence': {'candidate': candidate,
                'review': reports, 'native_evidence': stops, 'reference': 'controller:hostile-whole-artifacts',
                'coverage': coverage, 'claims': views}}}
        before = self.checkpoint.read_bytes()
        result = subprocess.run([sys.executable, str(Path(progress.control_git.__file__))],
            input=json.dumps(envelope), text=True, capture_output=True, cwd=self.flow)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)['ok'])
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def reviewed_ac4_candidate(self, **variant):
        candidate = self.layered_flow(**variant)
        for ticket in ('A', 'B'):
            for number in (1, 2):
                self.local_review(candidate, ticket, ticket + str(number))
            self.ticket_review(candidate, ticket)
        self.delivery_reviews(candidate)
        before = copy.deepcopy(self.state()['control']['context']['handoff_progress']['review_claims'])
        self.invoke('control', {'action': 'invalidate-candidate', 'evidence': {'candidate': candidate,
            'reason': 'AC4 in-scope countercase', 'reference': 'controller:AC4-change'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:AC4-change'}})
        return candidate, before

    def ac4_application_and_gate(self, candidate):
        applied = self.invoke('control', {'action': 'review-applicability', 'evidence': {
            'candidate': candidate, 'reference': 'controller:AC4-applicability'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:AC4-applicability'}})
        self.assertIsNotNone(applied['next_action'], applied)
        proof = applied['next_action']['result']['applicability']
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = fixture.transfer.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        denied = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': 'native:dispatcher', 'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': checkpoint['git_baseline_commit'], 'binding': self.binding,
            'plan_digest': checkpoint['plan_digest'], 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'AC4-current-command', 'response_ref': 'AC4-current-stop',
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(denied['status'], 'blocked', denied)
        self.assertEqual(denied['error']['code'], 'review_incomplete', denied)
        self.assertIn('no longer applicable', denied['error']['message'])
        self.assertNotEqual(checkpoint['state'], 'deliverable')
        return proof

    def test_ac4_influence_samefile_other_function_invalidates_both_slices(self):
        original, before = self.reviewed_ac4_candidate(same_file=True)
        self.repair_a(neighbor=True)
        target = self.flow_git('rev-parse', 'HEAD')
        self.assertNotEqual(original, target)
        self.assertTrue((self.flow / 'a1.py').read_text().startswith('def run():\n    return True'))
        proof = self.ac4_application_and_gate(target)
        for axis in ('local:A:A1', 'local:A:A2', 'ticket:A', 'standards', 'spec'):
            self.assertIn(before[axis]['claim_id'], proof['affected'])
        for axis in ('local:B:B1', 'local:B:B2', 'ticket:B'):
            self.assertIn(before[axis]['claim_id'], proof['retained'])
            self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims'][axis], before[axis])

    def test_ac4_influence_shared_fixture_reverse_closure_invalidates_consumers(self):
        original, before = self.reviewed_ac4_candidate(shared_read=True)
        b_bytes = {path: (self.flow / path).read_bytes() for path in ('b1.py', 'b2.py')}
        self.repair_a()
        target = self.flow_git('rev-parse', 'HEAD')
        proof = self.ac4_application_and_gate(target)
        self.assertEqual({path: (self.flow / path).read_bytes() for path in b_bytes}, b_bytes)
        for axis in ('local:A:A1', 'local:B:B1', 'local:B:B2', 'ticket:A', 'ticket:B', 'standards', 'spec'):
            self.assertIn(before[axis]['claim_id'], proof['affected'])
        self.assertIn(before['local:A:A2']['claim_id'], proof['retained'])

    def test_ac4_unknown_shared_change_conservatively_invalidates_all_proofs(self):
        original, before = self.reviewed_ac4_candidate(unknown_shared=True)
        self.repair_a()
        proof = self.ac4_application_and_gate(self.flow_git('rev-parse', 'HEAD'))
        self.assertEqual(set(proof['affected']), {view['claim_id'] for view in before.values()})
        self.assertEqual(proof['retained'], [])

    def test_ac4_report_reads_omitted_or_added_rejects_original_stopped_claim(self):
        candidate = self.layered_flow(shared_read=True)
        self.local_review(candidate, 'A', 'A1')
        self.local_review(candidate, 'A', 'A2')
        self.ticket_review(candidate, 'A')
        for slice_id, paths in (('B1', []), ('B2', ['a1.py', 'b1.py'])):
            rejected = self.local_review(candidate, 'B', slice_id, read_paths=paths, expect_rejected=True)
            self.assertEqual(rejected['error']['code'], 'review_incomplete')

    def test_native_report_boundary_oversize_retains_original_unknown_and_cannot_grant(self):
        candidate = self.layered_flow()
        axis = 'local:A:A1'
        verification = self.local_verification(candidate)
        prepare = {'operation': 'prepare', 'axis': axis, 'candidate': candidate,
            'actor_ref': 'task', 'verification': verification}
        action = self.invoke('review-activity', prepare)['next_action']
        semantic = {'status': 'accepted', 'findings': [], 'ac_refs': ['A:AC-1'],
            'checks': verification['checks'], 'read_paths': action['review_scope']['read_paths'],
            'dependency_claim_ids': action['review_scope']['dependency_claim_ids']}
        observation = {'operation': 'observe', 'axis': axis, 'candidate': candidate, 'actor_ref': 'task',
            'action_id': action['action_id'], 'receipt': {'adapter': 'fixture-native',
                'call_ref': 'oversize-original-call', 'response_ref': 'oversize-original-response',
                'status': 'stopped', 'ref': 'native:reviewer-A',
                'raw': {'review': semantic, 'padding': 'x' * (17 * 1024 * 1024)}}}
        self.invoke('review-activity', observation)
        slot = self.state()['review_activity'][axis]
        self.assertFalse(slot['stopped'])
        retained = slot['receipts'][-1]
        self.assertEqual(retained['status'], 'unknown')
        self.assertTrue(retained['raw']['truncated'])
        store = progress.state_store.StateStore(self.checkpoint)
        manifest = store.read_raw_manifest(retained['raw']['evidence_ref'], run_id=retained['raw']['evidence_ref']['run_id'])
        self.assertTrue(manifest['complete'])
        self.assertGreater(manifest['size'], progress.state_store.EVIDENCE_LIMIT)
        self.assertEqual(manifest['retained_size'], manifest['size'])
        self.assertEqual(retained['raw']['reason'], 'atomic_limit_exceeded')
        store.require_reserve(action['action_id'])
        before = copy.deepcopy(slot)
        lookup = self.invoke('review-activity', prepare)['next_action']
        self.assertEqual(lookup['operation'], 'lookup-exact-review')
        self.assertEqual(lookup['action_id'], action['action_id'])
        self.assertEqual(lookup['call_ref'], 'oversize-original-call')
        self.invoke('review-activity', observation)
        slot = self.state()['review_activity'][axis]
        self.assertEqual(slot['action_id'], before['action_id'])
        self.assertEqual(slot['receipts'], before['receipts'])
        denied = self.invoke('control', {'action': 'local-review', 'evidence': {
            'axis': axis, 'candidate': candidate, 'result_ref': 'oversize-original-response'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:oversize'}})
        self.assertEqual(denied['status'], 'blocked', denied)
        self.assertNotIn(axis, self.state()['control']['context']['handoff_progress'].get('review_claims', {}))
        self.assertNotIn('coverage', self.state()['control']['context']['handoff_progress'])
        self.assertEqual(len(slot['receipts']), 1)
        self.assertEqual(store.read_raw_manifest(retained['raw']['evidence_ref'], run_id=retained['raw']['evidence_ref']['run_id']), manifest)

    def test_native_report_boundary_partial_retains_unknown_without_semantic_grant(self):
        candidate = self.layered_flow()
        axis = 'local:A:A1'
        verification = self.local_verification(candidate)
        prepare = {'operation': 'prepare', 'axis': axis, 'candidate': candidate,
            'actor_ref': 'task', 'verification': verification}
        action = self.invoke('review-activity', prepare)['next_action']
        semantic = {'status': 'accepted', 'findings': [], 'ac_refs': ['A:AC-1'],
            'checks': verification['checks'], 'read_paths': action['review_scope']['read_paths'],
            'dependency_claim_ids': action['review_scope']['dependency_claim_ids']}
        observation = {'operation': 'observe', 'axis': axis, 'candidate': candidate, 'actor_ref': 'task',
            'action_id': action['action_id'], 'receipt': {'adapter': 'fixture-native',
                'call_ref': 'partial-original-call', 'response_ref': 'partial-original-response',
                'status': 'stopped', 'ref': 'native:reviewer-A',
                'raw': {'review': semantic, 'padding': 'x' * (2 * 1024 * 1024)}}}
        atomic_write = progress.state_store.atomic_write
        interrupted = []
        def stop_chunk(path, data, *args, **kwargs):
            if len(data) == 1024 * 1024 and not interrupted:
                interrupted.append(str(path))
                raise KeyboardInterrupt('actual original raw chunk write interrupted')
            return atomic_write(path, data, *args, **kwargs)
        with fixture.patch.object(progress.state_store, 'atomic_write', side_effect=stop_chunk):
            with self.assertRaises(progress.state_store.RawEvidenceInterrupted):
                self.invoke('review-activity', observation)
        self.assertEqual(len(interrupted), 1)
        slot = self.state()['review_activity'][axis]
        retained = slot['receipts'][-1]
        self.assertEqual(retained['status'], 'unknown')
        self.assertTrue(retained['raw']['truncated'])
        self.assertFalse(slot['stopped'])
        store = progress.state_store.StateStore(self.checkpoint)
        manifest = store.read_raw_manifest(retained['raw']['evidence_ref'], run_id=retained['raw']['evidence_ref']['run_id'])
        self.assertFalse(manifest['complete'])
        self.assertEqual(manifest['reason'], 'interrupted')
        self.assertLess(manifest['retained_size'], manifest['size'])
        store.require_reserve(action['action_id'])
        denied = self.invoke('control', {'action': 'local-review', 'evidence': {
            'axis': axis, 'candidate': candidate, 'result_ref': 'partial-original-response'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:partial'}})
        self.assertEqual(denied['status'], 'blocked', denied)
        self.assertNotIn(axis, self.state()['control']['context']['handoff_progress'].get('review_claims', {}))
        lookup = self.invoke('review-activity', prepare)['next_action']
        self.assertEqual(lookup['operation'], 'lookup-exact-review')
        self.assertEqual(lookup['action_id'], action['action_id'])
        self.assertEqual(lookup['call_ref'], 'partial-original-call')

    def test_ac4_missing_dependency_claim_invalidates_slice_and_blocks_ticket(self):
        candidate = self.layered_flow(same_file=True)
        neighbor = self.local_review(candidate, 'A', 'A2')
        self.assertEqual(neighbor['layer'], 'local')
        proof = self.invoke('control', {'action': 'review-applicability', 'evidence': {
            'candidate': candidate, 'reference': 'controller:missing-dependency'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:missing-dependency'}})
        self.assertIn(neighbor['claim_id'], proof['next_action']['result']['applicability']['affected'])
        self.local_review(candidate, 'A', 'A1')
        verification = self.local_verification(candidate, 'A', 'A1')
        verification['checks'].extend(self.local_verification(candidate, 'A', 'A2')['checks'])
        verification['source']['raw']['checks'] = copy.deepcopy(verification['checks'])
        with self.assertRaises(progress.entry.PreparationError) as denied:
            self.invoke('review-activity', {'operation': 'prepare', 'axis': 'ticket:A',
                'candidate': candidate, 'actor_ref': 'task', 'verification': verification})
        self.assertEqual(denied.exception.code, 'review_incomplete')
        self.assertIn('every applicable local slice', str(denied.exception))
        self.assertNotIn('ticket:A', self.state().get('review_activity', {}))
        self.assertNotIn('coverage', self.state()['control']['context']['handoff_progress'])

    def recover_layered_dispatcher(self):
        stopped = self.observation('stopped', 'result', ref='native:dispatcher')
        stopped['receipt']['raw'].update(resumable=False, dispatch_available=True)
        self.invoke('observe', stopped)
        context = self.state()['control']['context']
        owner = {'controller_ref': 'task', 'reference': 'controller:layered-recovery'}
        evidence = {'dispatcher_ref': 'native:dispatcher', 'attempt': context['carrier']['attempt'],
            'reference': owner['reference'], 'reason': 'original Dispatcher cannot resume',
            'stop_receipts': [stopped['receipt']['receipt_ref']] +
                [slot['record']['receipts'][-1]['receipt_ref'] for slot in self.state()['allocations'].values()],
            'call_receipts': [stopped['provenance']['response_ref']]}
        prepared = self.invoke('control', {'action': 'prepare-dispatch-recovery', 'evidence': evidence,
            'receipt': owner})['next_action']['result']['recovery']
        decision = {'reference': owner['reference'], 'authority_digest': prepared['authority_digest'],
            'attempt': prepared['attempt'], 'remaining_paths': prepared['remaining_paths'],
            'assume_paths': prepared['ownership']['dispatcher']}
        identity = {'recovery_id': prepared['recovery_id'], 'snapshot_digest': prepared['snapshot_digest'],
            'decision': decision}
        intent = self.invoke('control', {'action': 'dispatch-recovery-intent', 'evidence': identity,
            'receipt': owner})['next_action']['request']
        receipt = {'adapter': 'fixture-native', 'call_ref': 'layered-replacement-call',
            'response_ref': 'layered-replacement-response', 'intent_id': intent['intent_id'],
            'status': 'ready', 'ref': 'native:dispatcher-D2', 'raw': {'write_authority': False}}
        self.invoke('control', {'action': 'dispatch-recovery-result', 'evidence': {
            'recovery_id': prepared['recovery_id'], 'receipt': receipt}, 'receipt': receipt})
        inventory = self.observation('stopped', 'result', ref='native:dispatcher')['lifecycle']
        inventory['pages'][0]['response']['data'].append({'id': 'native:dispatcher-D2', 'status': {'type': 'idle'}})
        self.invoke('lifecycle-state', inventory)
        activated = self.invoke('control', {'action': 'recover-dispatch', 'evidence': {
            **identity, 'replacement_ref': 'native:dispatcher-D2'}, 'receipt': owner})
        self.assertTrue(activated['next_action']['result']['ok'], activated)

    def candidate_ready(self, candidate):
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = fixture.transfer.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        ready = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
            'dispatcher_ref': self.state()['control']['context']['carrier']['ref'], 'attempt': self.state()['control']['context']['carrier']['attempt'],
            'commit': candidate, 'expected_target_head': checkpoint['git_baseline_commit'], 'binding': self.binding,
            'plan_digest': checkpoint['plan_digest'], 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'early:' + candidate, 'response_ref': 'early-stop:' + candidate,
                'raw': {'checks': checks, 'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': self.state()['control']['context']['carrier']['ref']}}})
        self.assertIsNotNone(ready['next_action'], ready)
        self.assertTrue(ready['next_action']['result']['ok'], ready)
        return checks

    def delivery_reviews(self, candidate, *, raw_padding=0):
        checks = self.candidate_ready(candidate)
        checkpoint = self.state()['control']['context']['handoff_progress']
        reviews = {}
        for axis in ('standards', 'spec'):
            prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'verification': {'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
                    'plan_authority': checkpoint['plan_authority'], 'checks': checks}})
            scope = prepared['next_action']['review_scope']
            self.assertEqual(scope['mode'], 'cross-ticket-and-coverage')
            self.assertEqual(scope['integration_delta_paths'], [])
            response = axis + ':stopped:' + candidate
            self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
                'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'],
                'receipt': {'adapter': 'fixture-native', 'call_ref': axis + ':call:' + candidate,
                    'response_ref': response, 'status': 'stopped', 'ref': 'native:delivery-' + axis,
                    'raw': {'review': {'status': 'accepted', 'findings': [], 'ac_refs': scope['ac_refs'],
                        'requirement_refs': scope['requirement_refs'], 'dependency_claim_ids': scope['dependency_claim_ids'],
                        'review_scope': scope}, **({'padding': 'x' * raw_padding} if raw_padding else {})}}})
            reviews[axis] = {'candidate': candidate, 'expected_target_head': checkpoint['expected_target_head'],
                'plan_digest': checkpoint['plan_digest'], 'plan_authority': checkpoint['plan_authority'],
                'reviewer_ref': 'native:delivery-' + axis, 'status': 'accepted', 'result_ref': response}
        converged = self.invoke('control', {'action': 'review-converged', 'evidence': {
            'candidate': candidate, 'review': reviews, 'reference': 'controller:delivery:' + candidate},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:delivery:' + candidate}})
        self.assertTrue(converged['next_action']['result']['ok'], converged)
        return reviews

    def inherit_empty_candidate(self, candidate, reviews):
        before_views = copy.deepcopy(self.state()['control']['context']['handoff_progress']['review_claims'])
        self.invoke('control', {'action': 'invalidate-candidate', 'evidence': {'candidate': candidate,
            'reason': 'empty commit metadata', 'reference': 'controller:empty'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:empty'}})
        self.flow_git('commit', '--allow-empty', '-qm', 'empty candidate metadata')
        target = self.flow_git('rev-parse', 'HEAD')
        self.candidate_ready(target)
        converged = self.invoke('control', {'action': 'review-converged', 'evidence': {
            'candidate': target, 'review': reviews, 'reference': 'controller:inherited-delivery'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:inherited-delivery'}})
        self.assertTrue(converged['next_action']['result']['ok'], converged)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['review_claims'], before_views)
        self.assertTrue(all(report['candidate'] == candidate for report in reviews.values()))
        self.finish_certificate_delivery(target, reviews)

    def repair_a(self, *, neighbor=False, original=False):
        ticket = self.state()['scope_authority']['execution_plan']['catalog']['tickets'][0]
        step = ticket['plan']['steps'][0]
        request = self.execution_input()
        request['scope']['owned_paths'] = ['a1.py']
        request['authorization']['scope_digest'] = fixture.transfer.entry.digest(request['scope'])
        selected = ticket['plan']['steps'] if neighbor else [step]
        request['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, item) for item in selected]
        allocation_id = 'A1' if original else 'A1-repair'
        native_ref = 'native:executor-A1' if original else 'native:executor-A1-repair'
        self.invoke('allocation', {'allocation_id': allocation_id, 'operation': 'repair' if original else 'prepare',
            'handoff': request, **({'invocation_id': 'A1-final-repair'} if original else {})})
        slot = self.allocation_slot(allocation_id, 'A')
        bound_receipt = self.receipt(slot['record'], ref=native_ref)
        bound_receipt['receipt_ref'] += ':final-repair'
        bound = self.invoke('allocation', {'allocation_id': allocation_id, 'operation': 'bind',
            'receipt': bound_receipt})
        release = bound['next_action']['result']['write_release']
        body = b'def run():\n    return 2\n'
        if neighbor:
            body = b'def run():\n    return True\n\ndef neighbor():\n    return 2\n'
        (self.flow / 'a1.py').write_bytes(body)
        slot = self.allocation_slot(allocation_id, 'A')
        stopped_receipt = self.receipt(slot['record'], 'stopped', 'result', ref=native_ref)
        stopped_receipt['receipt_ref'] += ':final-repair'
        self.invoke('allocation', {'allocation_id': allocation_id, 'operation': 'receive',
            'receipt': stopped_receipt,
            'result': {'delivery_id': 'A1-repair-result', 'status': 'completed', 'payload': {
                'changed_paths': ['a1.py'], 'file_hashes': {'a1.py': hashlib.sha256(body).hexdigest()},
                'tests': ['repair behavior passed'], 'write_release': release,
                'plan_refs': request['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.invoke('allocation', {'allocation_id': allocation_id, 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accept-A1-repair'}})
        self.settings['thread_id'] = 'task'
        self.request['host'].update(thread_id='task', role='controller', source_ref=None, actor_ref=None)
        self.flow_git('add', 'a1.py')
        self.flow_git('commit', '-qm', 'legal A1 repair')
        self.early_ticket_check(self.flow_git('rev-parse', 'HEAD'), 'A')

    def finish_certificate_delivery(self, candidate, reviews, *, unknown_first=False):
        checkpoint = self.state()['control']['context']['handoff_progress']
        dispatcher = self.state()['control']['context']['carrier']['ref']
        start = {'attempt_id': 'second-final', 'dispatcher_ref': dispatcher,
            'carrier_attempt': self.state()['control']['context']['carrier']['attempt'],
            **{key: checkpoint[key] for key in ('candidate', 'expected_target_head', 'plan_digest', 'plan_authority', 'review_digest')}}
        self.invoke('control', {'action': 'validation-start', 'evidence': start,
            'receipt': {'controller_ref': 'task', 'reference': 'controller:final-start'}})
        checks = fixture.transfer.checks_for(checkpoint['validation_plan']['final_required'], candidate)
        if unknown_first:
            pending_checks = copy.deepcopy(checks)
            for check in pending_checks:
                check.update(status='unknown', exit_code=None)
            pending = self.invoke('control', {'action': 'validation-result',
                'evidence': {'attempt_id': 'second-final', 'checks': pending_checks},
                'receipt': {'adapter': 'fixture-command', 'call_ref': 'final-call', 'response_ref': 'final-pending',
                    'raw': {'attempt_id': 'second-final', 'checks': pending_checks, 'source_unchanged': True,
                        'stopped': False, 'dispatcher_ref': dispatcher}}})
            current = self.state()['control']['context']['handoff_progress']
            self.assertEqual(current['state'], 'validating', pending)
            self.assertEqual(current['attempts'][-1]['state'], 'unknown')
            self.assertEqual(current['attempts'][-1]['attempt_id'], 'second-final')
            self.assertEqual(len(current['attempts']), 1)
            original_attempt = copy.deepcopy(current['attempts'][-1])
            self.invoke('pause')
            self.assertEqual(self.state()['control']['context']['handoff_progress']['attempts'][-1], original_attempt)
            paused = self.invoke('observe', self.observation('stopped', 'result', ref=dispatcher))
            self.assertEqual(paused['status'], 'paused', paused)
            resumed = self.invoke('resume')
            self.assertEqual(self.state()['control']['context']['handoff_progress']['attempts'][-1], original_attempt)
            if resumed['next_action'] and resumed['next_action']['operation'] == 'invoke-host':
                self.invoke('observe', self.observation(ref=dispatcher))
            with self.assertRaisesRegex(progress.control.ControlError, 'original validation attempt'):
                progress.control.transition({'schema_version': 7, 'actor_ref': 'task',
                    'context': self.state()['control']['context'], 'action': 'invalidate-candidate',
                    'evidence': {'candidate': candidate, 'reference': 'replace-pending', 'reason': 'new bytes', 'stopped': True}})
        complete = self.invoke('control', {'action': 'validation-result', 'evidence': {'attempt_id': 'second-final', 'checks': checks},
            'receipt': {'adapter': 'fixture-command', 'call_ref': 'final-call', 'response_ref': 'final-stop',
                'raw': {'attempt_id': 'second-final', 'checks': checks, 'source_unchanged': True,
                    'stopped': True, 'dispatcher_ref': dispatcher}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'deliverable', complete)
        verification = progress.control.delivery_verification(self.state()['control']['context']['handoff_progress'], dispatcher)
        payload = {'candidate_commit': candidate, 'artifacts': self.implementation_paths, 'checks': ['complete candidate'],
            'review': reviews, 'verification': verification}
        observed = self.invoke('observe', self.observation('stopped', 'result', {
            'delivery_id': 'second-delivery', 'status': 'completed', 'payload': payload}, ref=dispatcher))
        self.assertEqual(observed['status'], 'needs_input', observed)
        pending = observed['pending']
        accepted = self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:second-accepted'})
        self.assertEqual(accepted['status'], 'accepted', accepted)
        predecessor = progress.implementation_predecessor(self.checkpoint)
        stage3 = fixture.transfer.handoff.accepted_stage3_progress(predecessor, str(self.checkpoint))
        self.assertEqual(stage3['coverage']['candidate'], candidate)
        frozen, checkpoint = progress.implementation_acceptance(predecessor)
        self.assertEqual(checkpoint['coverage'], verification['coverage'])
        before = progress.state_store.StateStore(self.checkpoint).directory
        objects = sorted(str(path) for path in (before / 'objects').rglob('*') if path.is_file())
        for _ in range(3):
            self.invoke('inspect')
        self.assertEqual(sorted(str(path) for path in (before / 'objects').rglob('*') if path.is_file()), objects)
        bad = copy.deepcopy(verification['coverage'])
        bad['certificate_digest'] = '0' * 64
        with self.assertRaises(progress.control.ControlError):
            progress.control_git.verify_stored_review_coverage(bad, checkpoint, self.state()['packages'],
                progress.read_record(self.checkpoint)['current']['run_id'])
        self.context = self.context_for(4)
        self.request['host'].pop('actor_ref', None)
        successor = self.input_for(4, self.state()['accepted'])
        successor['scope']['protected_paths'] = sorted(set(frozen['handoff']['scope']['protected_paths']) -
            set(successor['scope']['closure_paths']))
        successor['authorization']['scope_digest'] = fixture.transfer.entry.digest(successor['scope'])
        self.begin('continuous', successor)
        after_transfer = progress.closure_delivery_view(predecessor)
        self.assertEqual(after_transfer['view']['coverage']['candidate'], candidate)


for _name in dir(fixture.ProgressTests):
    if _name.startswith('test_') and _name not in LayeredReviewTests.__dict__:
        setattr(LayeredReviewTests, _name, None)
