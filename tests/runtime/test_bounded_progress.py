"""Bounded progression through the real Controller, dispatch and Git boundary."""
import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workflow_progress import ProgressTests, progress, transfer


class BoundedProgressTests(ProgressTests):
    def test_store_boundary_missing_pointer_never_replaces_prior_authority(self):
        self.begin_dispatcher()
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        markers = {path.name: path.read_bytes() for path in (directory / 'commits').iterdir()}
        self.checkpoint.unlink()  # Explicit filesystem fault: original pointer lost.
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            self.begin_dispatcher()
        self.assertFalse(self.checkpoint.exists(), 'do not create a replacement authority over a retained run')
        self.assertEqual(rejected.exception.code, 'state_authority_missing')
        self.assertEqual({path.name: path.read_bytes() for path in (directory / 'commits').iterdir()}, markers)

    def test_store_boundary_rejects_legacy_shadow_writes(self):
        self.begin_dispatcher()
        before = self.checkpoint.read_bytes()
        outer = progress.read_record(self.checkpoint)
        outer['runner_request'] = {'operation': 'pause', 'request_id': 'legacy-writer'}
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            progress.atomic_save(self.checkpoint, outer)
        self.assertEqual(rejected.exception.code, 'state_owner_required')
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_corrupt_missing_and_cross_run_objects_fail_closed(self):
        self.begin_dispatcher()
        checkpoint_bytes = self.checkpoint.read_bytes()
        envelope = json.loads(checkpoint_bytes)
        digest = envelope['current']['sha256']
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        object_path = directory / 'objects' / digest[:2] / digest[2:4] / digest
        original = object_path.read_bytes()
        object_path.write_bytes(original + b' ')
        with self.assertRaises(transfer.entry.PreparationError) as corrupt:
            self.invoke('inspect')
        self.assertEqual(corrupt.exception.code, 'state_object_corrupt')
        self.assertEqual(self.checkpoint.read_bytes(), checkpoint_bytes)
        object_path.unlink()
        with self.assertRaises(transfer.entry.PreparationError) as missing:
            self.invoke('inspect')
        self.assertEqual(missing.exception.code, 'state_object_missing')
        self.assertEqual(self.checkpoint.read_bytes(), checkpoint_bytes)
        object_path.write_bytes(original)
        envelope['current']['run_id'] = 'a-different-run'
        self.checkpoint.write_text(json.dumps(envelope))
        crossed = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError) as cross_run:
            self.invoke('inspect')
        self.assertEqual(cross_run.exception.code, 'invalid_state_store')
        self.assertEqual(self.checkpoint.read_bytes(), crossed)

    def test_completed_control_keeps_only_exact_receipt(self):
        self.prepare_execution()
        slot = self.state()['allocations']['slice-one']
        bound = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:executor')})
        self.assertEqual(bound['next_action']['result']['status'], 'bound')
        content = b"print('receipt')\n"
        (self.flow / 'impl.py').write_bytes(content)
        slot = self.state()['allocations']['slice-one']
        received = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
            'result': {'delivery_id': 'receipt-slice', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                'tests': ['fixture boundary passed'], 'write_release': transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.assertEqual(received['next_action']['result']['status'], 'received')
        accepted = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:accept'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted')
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'fixture candidate')
        candidate = self.flow_git('rev-parse', 'HEAD')
        self.settings['thread_id'] = self.state()['handoff']['expected_entry']['actor']['thread_id']
        self.mark_reviewable(candidate, fixture_execution=False)
        self.assertEqual(self.state().get('control_transactions', {}), {},
            'completed control handshakes must leave the active transaction window')

    def receipt_commit_crash(self, after_root):
        self.prepare_execution()
        slot = self.state()['allocations']['slice-one']
        request = {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:executor')}
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        replace = os.replace
        def interrupted(source, target):
            target = Path(target)
            if target == self.checkpoint and not after_root:
                envelope = json.loads(Path(source).read_bytes())
                digest = envelope['current']['sha256']
                root = json.loads((directory / 'objects' / digest[:2] / digest[2:4] / digest).read_bytes())['value']
                if root.get('receipts'):
                    raise KeyboardInterrupt('prepared receipt before authority commit')
            elif target.parent.name == 'commits' and after_root:
                if json.loads(Path(source).read_bytes()).get('receipts'):
                    raise KeyboardInterrupt('authority committed before marker/return')
            return replace(source, target)
        with patch.object(os, 'replace', interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('allocation', request)
        revision = self.invoke('inspect')['revision']
        recovered = self.invoke('allocation', request)
        self.assertEqual(recovered['next_action']['result']['status'], 'bound')
        self.assertEqual(recovered['acknowledged'], after_root)
        if after_root:
            self.assertEqual(recovered['revision'], revision)
        repeated = self.invoke('allocation', request)
        self.assertTrue(repeated['acknowledged'])
        self.assertEqual(repeated['revision'], recovered['revision'])
        changed = {**request, 'receipt': {**request['receipt'], 'raw': {'different': True}}}
        conflict = self.invoke('allocation', changed)
        self.assertEqual(conflict['error']['code'], 'receipt_conflict')
        self.assertEqual(self.state()['allocations']['slice-one']['record']['status'], 'bound')

    def test_prepared_receipt_is_not_a_committed_ack(self):
        self.receipt_commit_crash(False)

    def test_committed_receipt_repairs_marker_without_reapplying(self):
        self.receipt_commit_crash(True)

    def test_root_commit_faults_preserve_authority_and_repair_proof(self):
        self.begin_dispatcher()
        previous = self.checkpoint.read_bytes()
        replace = os.replace
        def before_root(source, target):
            if Path(target) == self.checkpoint:
                raise KeyboardInterrupt('before original owner commit')
            return replace(source, target)
        with patch.object(os, 'replace', before_root):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('pause')
        self.assertEqual(self.checkpoint.read_bytes(), previous)
        self.assertEqual(self.invoke('inspect')['status'], 'active')
        def after_root(source, target):
            if Path(target).parent.name == 'commits':
                raise KeyboardInterrupt('after original owner commit, before proof marker')
            return replace(source, target)
        with patch.object(os, 'replace', after_root):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('pause')
        revision = self.invoke('inspect')['revision']
        marker = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'commits' / str(revision)
        self.assertFalse(marker.exists())
        recovered = self.invoke('advance')
        self.assertEqual((recovered.get('next_action') or {}).get('operation'), 'stop-host')
        self.assertTrue(marker.exists(), 'current authority must repair its proof before the next mutation')
        repeated = self.invoke('advance')
        self.assertEqual((repeated.get('next_action') or {}).get('operation'), 'lookup-exact-action')
        self.assertEqual(repeated['next_action']['action']['action_id'],
                         recovered['next_action']['action_id'])

    def test_budget_full_keeps_original_call_receivable(self):
        execution = self.prepare_execution()
        slot = self.state()['allocations']['slice-one']
        store = self.checkpoint.with_name(self.checkpoint.name + '.state')
        # A persisted capacity account is a filesystem boundary, representing
        # previously charged retained evidence without allocating 2 GiB in CI.
        (store / 'usage.json').write_text(json.dumps({'allocated': 2 * 1024**3 - 128 * 1024**2}))
        result = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:executor')})
        self.assertEqual((result.get('next_action') or {}).get('result', {}).get('status'), 'bound',
            {key: result.get(key) for key in ('status', 'error')})
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('allocation', {'allocation_id': 'new-slice', 'operation': 'prepare', 'handoff': execution})
        self.assertEqual(self.state()['allocations']['slice-one']['record']['status'], 'bound')

    def test_two_slices_poll_and_resume_keep_current_state_bounded(self):
        self.context = self.context_for(3)
        self.begin('continuous', self.input_for(3))
        bound = self.invoke('observe', self.observation(ref='native:dispatcher'))
        self.assertNotEqual(bound['status'], 'blocked', bound.get('error'))
        self.assertEqual(self.state()['control']['context']['carrier']['ref'], 'native:dispatcher')
        original_dispatch = self.state()['dispatch']['request']['digest']
        for number in (1, 2):
            identity = 'slice-' + str(number)
            ref = 'native:executor-' + str(number)
            execution = self.execution_input()
            prepared = self.invoke('allocation', {'allocation_id': identity,
                'operation': 'prepare', 'handoff': execution})
            self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
            reserve = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'reserves' / 'business' / hashlib.sha256(
                prepared['next_action']['action_id'].encode()).hexdigest()
            self.assertTrue(reserve.exists(), 'host invocation requires a durable result-space reservation')
            self.assertEqual(reserve.stat().st_size, 16 * 1024 * 1024)
            replay = self.invoke('allocation', {'allocation_id': identity,
                'operation': 'prepare', 'handoff': execution})
            self.assertEqual(replay['next_action']['operation'], 'lookup-exact-allocation')
            slot = self.state()['allocations'][identity]
            self.invoke('allocation', {'allocation_id': identity, 'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref=ref)})
            content = ('print(' + str(number) + ')\n').encode()
            (self.flow / 'impl.py').write_bytes(content)
            slot = self.state()['allocations'][identity]
            received = self.invoke('allocation', {'allocation_id': identity, 'operation': 'receive',
                'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=ref),
                'result': {'delivery_id': identity, 'status': 'completed', 'payload': {
                    'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                    'tests': ['fixture boundary passed'],
                    'write_release': transfer.dispatch.execution_release_token(slot['record']),
                    'plan_refs': slot['record']['handoff']['semantic']['plan_refs'],
                    'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
            self.assertEqual(received['next_action']['result']['status'], 'received', received)
            accepted = self.invoke('allocation', {'allocation_id': identity,
                'operation': 'accept', 'decision': {'reference': 'dispatcher:accept:' + identity}})
            self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
            committed_revision = accepted['revision']
            replayed = self.invoke('allocation', {'allocation_id': identity,
                'operation': 'accept', 'decision': {'reference': 'dispatcher:accept:' + identity}})
            self.assertTrue(replayed['acknowledged'])
            self.assertEqual(replayed['revision'], committed_revision,
                'an exact committed receipt replay must not append another authority revision')
        poll = self.observation('running', 'result', ref='native:dispatcher')
        self.invoke('observe', poll)
        before = self.checkpoint.stat().st_size
        objects = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'objects'
        before_objects = {path.relative_to(objects) for path in objects.rglob('*') if path.is_file()}
        before_events = len(self.state()['events'])
        for number in range(1000):
            observed = {**poll, 'event_id': 'same-status-poll-' + str(number)}
            result = self.invoke('observe', observed)
            self.assertEqual(result['next_action']['operation'], 'wait-host', result)
            self.assertLessEqual(self.checkpoint.stat().st_size, 256 * 1024,
                'current root exceeds the fixed budget during repeated native polling')
        self.assertEqual({path.relative_to(objects) for path in objects.rglob('*') if path.is_file()}, before_objects)
        self.assertEqual(len(self.state()['events']), before_events)
        self.settings['thread_id'] = self.state()['handoff']['expected_entry']['actor']['thread_id']
        self.invoke('pause')
        stopped = self.observation('stopped', 'result', ref='native:dispatcher')
        stopped['receipt']['raw']['resumable'] = True
        paused = self.invoke('observe', stopped)
        self.assertEqual(paused['status'], 'paused', paused)
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            resumed = self.invoke('resume')
        self.assertEqual((resumed.get('next_action') or {}).get('operation'), 'continue-host',
            {key: resumed.get(key) for key in ('status', 'step', 'error', 'pending')})
        self.assertEqual(self.state()['dispatch']['request']['digest'], original_dispatch)
        self.assertFalse(any('archive' in path.parts for path in opened), opened)
        current_reads = [path for path in opened if path.is_relative_to(self.checkpoint.parent) and path.is_file()]
        current_bytes = sum(path.stat().st_size for path in current_reads)
        self.assertLessEqual(current_bytes, 9 * 256 * 1024)
        metrics = {'root_bytes': self.checkpoint.stat().st_size,
            'resume_files': len(current_reads), 'resume_bytes': current_bytes,
            'poll_new_objects': 0, 'poll_new_events': 0}
        print('bounded-current metrics:', metrics)
        if os.environ.get('T01_METRICS_PATH'):
            Path(os.environ['T01_METRICS_PATH']).write_text(json.dumps(metrics, indent=2) + '\n')
        self.assertLessEqual(self.checkpoint.stat().st_size, 256 * 1024)
        self.assertLessEqual(self.checkpoint.stat().st_size - before, 16 * 1024)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(BoundedProgressTests(name)
        for name in loader.getTestCaseNames(BoundedProgressTests)
        if name in BoundedProgressTests.__dict__)
