"""Start gates recheck real A/B/Git facts before authority and host launch."""
import copy
import subprocess
import unittest
from unittest.mock import patch

import test_workflow_progress as existing

progress = existing.progress
transfer = existing.transfer
START_ERRORS = (transfer.entry.PreparationError, transfer.control.ControlError,
                transfer.supervision.ProtocolError)


class LaunchAssertions:
    def assert_no_host_launch(self, effects):
        self.assertNotIn('invoke-host', [call.args[1] for call in effects.call_args_list])
        saved = progress.read_record(self.checkpoint).get(progress.KEY, {})
        for key in ('action', 'host_action'):
            self.assertNotEqual((saved.get(key) or {}).get('operation'), 'invoke-host')
        self.assertFalse(saved.get('retained_host_actions'))


class StageStartVerificationTests(LaunchAssertions, existing.ProgressTests):
    def test_plan_group_cannot_allocate_over_unattributed_implementation_bytes(self):
        self.begin_dispatcher()
        request = self.execution_input()
        (self.flow / 'impl.py').write_text('unattributed bytes\n')
        result = self.invoke('allocation', {'allocation_id': 'dirty-plan-group',
            'operation': 'prepare', 'handoff': request})
        self.assertEqual(result['status'], 'blocked', result)
        self.assertIsNone(self.state()['allocations']['dirty-plan-group']['record'])
        self.assertEqual(self.state()['control']['context']['handoff_progress']['executions'], [])

    def external_git(self, *args, repository=None):
        # Another Git writer must bypass the worker's subprocess.run observer.
        process = subprocess.Popen(['git', '-C', str(repository or self.flow), *args],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, stderr = process.communicate()
        self.assertEqual(process.returncode, 0, stderr.decode())

    def mutate_start_fact(self, kind):
        source = self.flow / self.requirement_path
        if kind == 'source':
            source.write_text('changed between start gates\n')
        elif kind == 'head':
            self.external_git('commit', '--allow-empty', '-qm', 'Concurrent HEAD change')
        elif kind == 'primary_target_index':
            self.external_git('update-index', '--chmod=+x', self.requirement_path,
                              repository=self.root)
        elif kind == 'branch':
            self.external_git('switch', '-c', 'codex/concurrent-start-change')
        elif kind == 'symlink':
            elsewhere = self.root / 'source-copy.txt'
            elsewhere.write_bytes(source.read_bytes())
            source.unlink()
            source.symlink_to(elsewhere)
        elif kind == 'package':
            transfer.entry.skill_preflight.verify_identity.side_effect = transfer.entry.PreparationError(
                'package_changed', 'Pinned package changed between start gates')
        else:
            raise AssertionError(kind)

    def check_start_gate(self, boundary, kind):
        target, name = ((progress.dispatch, 'prepared') if boundary == 'before_authority'
                        else (progress.Progress, 'verify_launch'))
        original = getattr(target, name)
        entered = False

        def mutate_then_verify(*args, **kwargs):
            nonlocal entered
            self.assertFalse(entered, 'the rejected start must enter this gate only once')
            entered = True
            expected_calls = 0 if boundary == 'before_authority' else 1
            self.assertEqual(checkpoint.call_count, expected_calls)
            if boundary == 'before_action':
                self.assertEqual(checkpoint.call_args.args[2], 'start-design')
                saved = self.state()
                self.assertEqual(saved['control']['context']['carrier']['attempt'],
                                 saved['dispatch']['request']['attempt'])
                self.assertEqual(saved['control']['context']['handoff_progress']['state'],
                                 'designer-pending')
            self.mutate_start_fact(kind)
            return original(*args, **kwargs)

        with patch.object(target, name, mutate_then_verify), \
                patch.object(progress.dispatch, 'checkpoint', wraps=progress.dispatch.checkpoint) as checkpoint, \
                patch.object(progress.Progress, 'effect', autospec=True,
                             side_effect=progress.Progress.effect) as effects:
            with self.assertRaises(START_ERRORS):
                self.invoke('start', {'handoff': copy.deepcopy(self.input),
                    'control': {'context': self.context, 'discussion': None}})
            self.assertTrue(entered)
            self.assertEqual(checkpoint.call_count, 0 if boundary == 'before_authority' else 1)
            self.assert_no_host_launch(effects)
        if boundary == 'before_authority':
            saved = self.state()
            self.assertEqual(saved['control']['context'], self.context)
            self.assertIsNone(saved['dispatch'])
            self.assertIsNone(saved['transaction_result'])

    def test_final_gate_rechecks_branch_changed_after_source_verification(self):
        original_launch = progress.Progress.verify_launch
        original_source = transfer.handoff.source
        original_binding = transfer.handoff.control_git.verify_binding
        in_launch_gate = changed = binding_checked_after_change = False

        def launch(owner):
            nonlocal in_launch_gate
            self.assertEqual(checkpoint.call_count, 1)
            self.assertEqual(checkpoint.call_args.args[2], 'start-design')
            in_launch_gate = True
            try:
                return original_launch(owner)
            finally:
                in_launch_gate = False

        def source(*args, **kwargs):
            nonlocal changed
            result = original_source(*args, **kwargs)
            if in_launch_gate:
                self.assertFalse(changed)
                changed = True
                # A and the source already passed. B must still read the actual
                # binding through its complete CLI validator at this final gate.
                self.external_git('switch', '-c', 'codex/late-start-change')
            return result

        def binding(*args, **kwargs):
            nonlocal binding_checked_after_change
            if in_launch_gate and changed:
                binding_checked_after_change = True
            return original_binding(*args, **kwargs)

        with patch.object(progress.Progress, 'verify_launch', launch), \
                patch.object(transfer.handoff, 'source', source), \
                patch.object(transfer.handoff.control_git, 'verify_binding', binding), \
                patch.object(progress.dispatch, 'checkpoint', wraps=progress.dispatch.checkpoint) as checkpoint, \
                patch.object(progress.Progress, 'effect', autospec=True,
                             side_effect=progress.Progress.effect) as effects:
            with self.assertRaisesRegex(transfer.control.ControlError,
                                        'Flow Worktree binding verification failed'):
                self.invoke('start', {'handoff': self.input,
                    'control': {'context': self.context, 'discussion': None}})
            self.assertTrue(changed)
            self.assertTrue(binding_checked_after_change)
            self.assertEqual(checkpoint.call_count, 1)
            self.assert_no_host_launch(effects)

    def test_unsafe_checkpoint_fails_before_any_checkpoint_write(self):
        # C protects the actual execution root and disposable binding worktree.
        # Runner-owned record placement has its own confirmation validation.
        self.assertEqual(self.input['expected_entry']['repository']['root'], str(self.flow))
        self.checkpoint = self.flow / 'unsafe-checkpoint.json'
        with patch.object(progress, 'atomic_save', wraps=progress.atomic_save) as save, \
                patch.object(progress.dispatch, 'checkpoint', wraps=progress.dispatch.checkpoint) as checkpoint:
            with self.assertRaises(transfer.entry.PreparationError) as error:
                self.invoke('start', {'handoff': self.input,
                    'control': {'context': self.context, 'discussion': None}})
        self.assertEqual(error.exception.code, 'unsafe_checkpoint')
        save.assert_not_called()
        checkpoint.assert_not_called()
        self.assertFalse(self.checkpoint.exists())

    def test_public_request_cannot_claim_prior_verification(self):
        value = copy.deepcopy(self.input)
        value['already_verified'] = True
        with patch.object(progress.dispatch, 'checkpoint', wraps=progress.dispatch.checkpoint) as checkpoint, \
                patch.object(progress.Progress, 'effect', autospec=True,
                             side_effect=progress.Progress.effect) as effects:
            with self.assertRaises(transfer.entry.PreparationError) as error:
                self.invoke('start', {'handoff': value,
                    'control': {'context': self.context, 'discussion': None}})
            self.assertEqual(error.exception.code, 'invalid_request')
            checkpoint.assert_not_called()
            self.assert_no_host_launch(effects)
        self.assertFalse(self.checkpoint.exists())

    def test_entry_binding_differing_from_final_binding_is_verified(self):
        value = copy.deepcopy(self.input)
        forged = copy.deepcopy(value['entry']['target'])
        forged['binding']['branch'] = 'codex/forged-entry-binding'
        value['entry']['target'] = copy.deepcopy(forged)
        value['expected_entry']['target'] = copy.deepcopy(forged)
        expected = value['expected_entry']
        expected['evidence_digest'] = transfer.entry.digest({key: item for key, item in expected.items()
                                                            if key != 'evidence_digest'})
        self.assertNotEqual(value['entry']['target']['binding'], value['binding'])
        self.assertEqual(value['binding'], self.binding)
        with patch.object(progress.dispatch, 'checkpoint', wraps=progress.dispatch.checkpoint) as checkpoint, \
                patch.object(progress.Progress, 'effect', autospec=True,
                             side_effect=progress.Progress.effect) as effects:
            with self.assertRaises(START_ERRORS):
                self.invoke('start', {'handoff': value,
                    'control': {'context': self.context, 'discussion': None}})
            checkpoint.assert_not_called()
            self.assert_no_host_launch(effects)
        self.assertFalse(self.checkpoint.exists())

    def saved_dispatch_result(self):
        with patch.object(progress.Progress, 'consume_transaction', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('start', {'handoff': self.input,
                    'control': {'context': self.context, 'discussion': None}})
        saved = self.state()
        self.assertEqual(saved['transaction']['operation'], 'prepare')
        self.assertIsNotNone(saved['transaction_result'])
        self.assertIsNone(saved['dispatch'])
        self.assertIsNone(saved['action'])
        return copy.deepcopy(saved['transaction_result'])

    def test_saved_dispatch_result_resumes_original_attempt_without_recalling_b(self):
        original = self.saved_dispatch_result()
        with patch.object(progress.dispatch, 'handle', side_effect=AssertionError('must consume saved B result')) as dispatch:
            resumed = self.invoke('resume')
        dispatch.assert_not_called()
        self.assertEqual(resumed['next_action']['operation'], 'invoke-host')
        self.assertEqual(resumed['next_action']['payload'], original['record']['request'])
        saved = self.state()
        self.assertEqual(saved['dispatch'], original['record'])
        self.assertEqual(saved['last_result'], original)
        self.assertIsNone(saved['transaction'])
        self.assertIsNone(saved['transaction_result'])

    def test_saved_dispatch_result_rechecks_changed_binding_without_recalling_b(self):
        original = self.saved_dispatch_result()
        self.mutate_start_fact('branch')
        with patch.object(progress.dispatch, 'handle', side_effect=AssertionError('must consume saved B result')) as dispatch, \
                patch.object(progress.Progress, 'effect', autospec=True,
                             side_effect=progress.Progress.effect) as effects:
            resumed = self.invoke('resume')
            dispatch.assert_not_called()
            self.assertEqual(resumed['status'], 'blocked')
            self.assertFalse(resumed['downstream_ready'])
            self.assert_no_host_launch(effects)
        saved = self.state()
        self.assertEqual(saved['dispatch'], original['record'])
        self.assertEqual(saved['last_result'], original)
        self.assertEqual(saved['dispatch']['request']['attempt'], original['record']['request']['attempt'])
        self.assertIsNone(saved['transaction'])
        self.assertIsNone(saved['transaction_result'])


class AttachedStageStartVerificationTests(LaunchAssertions, existing.AttachedProgressTests):
    def test_real_ledger_cancellation_after_reservation_prevents_launch(self):
        original = progress.Progress.verify_launch
        cancelled = False
        reserved_attempt = None

        def cancel_then_verify(owner):
            nonlocal cancelled, reserved_attempt
            self.assertEqual(checkpoint.call_count, 1)
            self.assertEqual(checkpoint.call_args.args[2], 'reserve-launch')
            reserved = self.read()['workflow_control']
            saved = progress.read_record(self.checkpoint)[progress.KEY]
            self.assertEqual(reserved, saved['control']['context'])
            self.assertEqual(reserved['handoff_progress']['launch_input'], saved['handoff']['digest'])
            reserved_attempt = reserved['carrier']['attempt']
            self.assertEqual(reserved_attempt, saved['dispatch']['request']['attempt'])
            self.mutate('workflow-control', action='cancel', evidence={})
            cancelled = True
            return original(owner)

        with patch.object(progress.Progress, 'verify_launch', cancel_then_verify), \
                patch.object(progress.dispatch, 'checkpoint', wraps=progress.dispatch.checkpoint) as checkpoint, \
                patch.object(progress.Progress, 'effect', autospec=True,
                             side_effect=progress.Progress.effect) as effects:
            with self.assertRaises(transfer.entry.PreparationError) as error:
                self.invoke_progress('start', {'handoff': self.input, 'control': self.port()})
            self.assertEqual(error.exception.code, 'authority_changed')
            self.assertTrue(cancelled)
            self.assertEqual(checkpoint.call_count, 1)
            self.assert_no_host_launch(effects)
        actual = self.read()['workflow_control']
        self.assertEqual(actual['handoff_progress']['state'], 'cancelled')
        self.assertEqual(actual['carrier']['attempt'], reserved_attempt)


for boundary in ('before_authority', 'before_action'):
    for kind in ('source', 'head', 'primary_target_index', 'branch', 'symlink', 'package'):
        def check(self, boundary=boundary, kind=kind):
            self.check_start_gate(boundary, kind)
        setattr(StageStartVerificationTests, 'test_' + boundary + '_' + kind, check)


def load_tests(loader, tests, pattern):
    # Fixture inheritance supplies helpers; its test inventory runs separately.
    suite = unittest.TestSuite()
    for cls in (StageStartVerificationTests, AttachedStageStartVerificationTests):
        suite.addTests(cls(name) for name in sorted(vars(cls)) if name.startswith('test_'))
    return suite
