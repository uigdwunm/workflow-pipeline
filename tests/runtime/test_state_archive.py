"""Archive/recovery through the original immutable store and owner interfaces."""
import copy
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/shared/scripts'))
import workflow_state_store as storage
import entry_prepare as entry
from test_workflow_progress import ProgressTests, progress, transfer, runner
from test_bounded_progress import LedgerReviewProgressTests
import hashlib
import os
import json
import subprocess


class StateArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'checkpoint.json'
        self.store = storage.StateStore(self.path)
        self.current = self.store.commit_current(expected_revision=0, revision=1,
            subjects={'stage:3:0': {'status': 'active'},
                      'ticket:T05:0': {'status': 'accepted', 'stopped': True,
                                       'pending': None, 'allocations': {}}},
            metadata={'stage_subject': 'stage:3:0', 'next_action': 'next-ticket'}, outer={})

    def test_archive_retains_exact_committed_receipt_and_current_route(self):
        request, result = {'operation': 'accept', 'invocation': 'original'}, {'accepted': 'original'}
        key = self.store.receipt_key(self.current, actor='owner', kind='accept',
                                     subject='ticket:T05:0', invocation='original')
        receipt = self.store.prepare_receipt(self.current, key, request, result)
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={name: self.store.read_subject(self.current, name).value
                      for name in self.current.root['subjects']},
            metadata=self.current.root['metadata'], outer={}, receipts=[receipt])
        original = self.store.read_subject(self.current, 'ticket:T05:0').value
        archived = self.store.compact(expected_revision=2, subject_ids=['ticket:T05:0'])
        reopened = storage.StateStore(self.path)
        current = reopened.open_current()
        self.assertEqual(current.root['metadata']['next_action'], 'next-ticket')
        self.assertNotIn('ticket:T05:0', current.root['subjects'])

        self.assertEqual(reopened.read_archive(current, 'ticket:T05:0').value, original)
        self.assertEqual(reopened.read_receipt(current, key, request), result)
        with self.assertRaises(entry.PreparationError) as conflict:
            reopened.read_receipt(current, key, {'operation': 'different'})
        self.assertEqual(conflict.exception.code, 'receipt_conflict')
        page = reopened.read_archive_page(current)
        self.assertEqual([item['subject_id'] for item in page['items']], ['ticket:T05:0'])
        self.assertIsNone(page['next_cursor'])
        daily = storage.StateStore(self.path)
        daily.open_current()
        self.assertEqual(daily.files_read, 2)
        self.assertEqual(archived.revision, 3)

    def test_archive_rejects_unknown_and_preserves_original_authority(self):
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active'}, 'ticket:T05:0': {
                'status': 'accepted', 'stopped': True, 'host': {'status': 'unknown'}}},
            metadata=self.current.root['metadata'], outer={})
        before = self.path.read_bytes()
        with self.assertRaises(entry.PreparationError) as error:
            self.store.compact(expected_revision=2, subject_ids=['ticket:T05:0'])
        self.assertEqual(error.exception.code, 'archive_pending')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn('ticket:T05:0', storage.StateStore(self.path).open_current().root['subjects'])

    def test_current_completed_slots_fit_32_entry_ticket_record(self):
        # Storage admission only: these data records grant no Controller lease.
        slots = {str(number): {'record': {'status': 'accepted'}, 'transaction': None,
            'original_result': 'x' * 11000} for number in range(32)}
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active'}, 'ticket:T05:0': {
                'batch': 0, 'allocations': slots}}, metadata=self.current.root['metadata'], outer={})
        reference = self.current.root['subjects']['ticket:T05:0']
        self.assertLessEqual(reference['size'], storage.SUBJECT_LIMIT)
        reopened = storage.StateStore(self.path)
        current = reopened.open_current()
        self.assertEqual(reopened.read_subject(current, 'ticket:T05:0').value['allocations'], slots)
        self.assertLessEqual(reopened.bytes_read, storage.READ_LIMIT)

    def test_final_archive_audit_verifies_original_reference_closure(self):
        original = self.store.write_object(self.current.run_id, 'evidence', {'original': 'native receipt'})
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active'}, 'ticket:T05:0': {
                'status': 'accepted', 'stopped': True, 'original_ref': original}},
            metadata=self.current.root['metadata'], outer={})
        current = self.store.compact(expected_revision=2, subject_ids=['ticket:T05:0'])
        reopened = storage.StateStore(self.path)
        audit = reopened.verify_archive_chain(reopened.open_current())
        self.assertEqual(audit['subjects'], 1)
        self.assertGreater(audit['structured_bytes'], original['size'])
        self.store._path(original['sha256']).write_bytes(b'corrupt original')
        reopened = storage.StateStore(self.path)
        with self.assertRaises(entry.PreparationError) as error:
            reopened.verify_archive_chain(reopened.open_current())
        self.assertEqual(error.exception.code, 'state_object_corrupt')

    def archive_ledger_boundary(self, *, certify=True, receipt=True, foreign=False):
        # Storage boundary only: publish the exact pointer before certifying its
        # commit receipt. This fixture grants no workflow Controller authority.
        ledger_path = self.path.with_name('ledger.json')
        ledger = storage.StateStore(ledger_path)
        context = {'controller_ref': 'owner', 'topic_ref': 'topic'}
        certificate = {'key': 'original', 'fingerprint': entry.digest(context),
                       'result': {'control': {}}} if receipt else None
        pointer = ledger.prepare_ledger_control(context, topic_id='topic', ledger_revision=1,
            control_revision=1, previous=None, receipt=certificate)
        storage.atomic_write(ledger_path, storage.encode({'workflow_control_ref': pointer}))
        if certify:
            ledger.certify_ledger_control(pointer)
        current_pointer = ledger.prepare_ledger_control(context, topic_id='topic', ledger_revision=2,
            control_revision=2, previous=pointer, receipt=None)
        storage.atomic_write(ledger_path, storage.encode({'workflow_control_ref': current_pointer}))
        archived_pointer = pointer
        if foreign:
            other = storage.StateStore(self.path.with_name('foreign-ledger.json'))
            archived_pointer = other.prepare_ledger_control(context, topic_id='topic', ledger_revision=1,
                control_revision=1, previous=None, receipt=certificate)
            storage.atomic_write(other.checkpoint, storage.encode({'workflow_control_ref': archived_pointer}))
            other.certify_ledger_control(archived_pointer)
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active', 'control': {'store_ref': current_pointer}},
                'ticket:T05:0': {'status': 'accepted', 'stopped': True,
                    'original': {'ledger_pointer': archived_pointer}}},
            metadata={**self.current.root['metadata'], 'controller_ref': 'owner'}, outer={})
        self.store.compact(expected_revision=2, subject_ids=['ticket:T05:0'])
        return ledger, pointer

    def test_final_ledger_pointer_body_identity_keeps_original_generation(self):
        ledger, pointer = self.archive_ledger_boundary()
        _, body = ledger.read_ledger_control(pointer)
        self.assertNotIn('reference', body['identity'])
        reopened = storage.StateStore(self.path)
        self.assertEqual(reopened.verify_archive_chain(reopened.open_current())['subjects'], 1)

    def test_final_ledger_pointer_rejects_foreign_original_identity(self):
        self.archive_ledger_boundary(foreign=True)
        reopened = storage.StateStore(self.path)
        with self.assertRaises(entry.PreparationError) as error:
            reopened.verify_archive_chain(reopened.open_current())
        self.assertEqual(error.exception.code, 'state_object_corrupt')

    def test_final_ledger_pointer_rejects_missing_commit_certificate(self):
        self.archive_ledger_boundary(certify=False)
        reopened = storage.StateStore(self.path)
        with self.assertRaises(entry.PreparationError) as error:
            reopened.verify_archive_chain(reopened.open_current())
        self.assertIn('not committed', str(error.exception))

    def test_final_ledger_pointer_rejects_generation_without_original_receipt(self):
        self.archive_ledger_boundary(receipt=False)
        reopened = storage.StateStore(self.path)
        with self.assertRaises(entry.PreparationError) as error:
            reopened.verify_archive_chain(reopened.open_current())
        self.assertIn('original ledger commit receipt', str(error.exception))

    def test_archive_two_generations_keep_original_native_and_exact_receipts(self):
        evidence = self.store.write_object(self.current.run_id, 'evidence', {'original': 'native stop'})
        request = {'original': 'accept'}
        key = self.store.receipt_key(self.current, actor='owner', kind='accept', subject='slice', invocation='first')
        receipt = self.store.prepare_receipt(self.current, key, request, {'accepted': 'first'})
        first = {'record': {'request': {'attempt': 'first'}, 'acceptance': {'role_ref': 'native:original'}},
                 'original_ref': evidence}
        original = {'status': 'accepted', 'stopped': True, 'allocation_id': 'slice', 'slot': first}
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active'}}, metadata=self.current.root['metadata'], outer={},
            archive_records={'allocation:slice': original}, receipts=[receipt])
        second = copy.deepcopy(first)
        second['record']['request']['attempt'] = 'second'
        second['invocation_history'] = [self.store.write_object(self.current.run_id, 'evidence', {'execution_invocation': first})]
        self.current = self.store.commit_current(expected_revision=2, revision=3,
            subjects={'stage:3:0': {'status': 'active'}}, metadata=self.current.root['metadata'], outer={},
            archive_records={'allocation:slice:second': {**original, 'slot': second}})
        reopened = storage.StateStore(self.path)
        current = reopened.open_current()
        self.assertEqual(reopened.read_archive(current, 'allocation:slice').value['slot'], second)
        self.assertEqual(reopened.read_native_archive(current, 'native:original').value['slot'], first)
        self.assertEqual(reopened.read_receipt(current, key, request), {'accepted': 'first'})
        self.assertEqual(reopened.verify_archive_chain(current)['subjects'], 2)
        self.store._path(evidence['sha256']).unlink()
        reopened = storage.StateStore(self.path)
        with self.assertRaises(entry.PreparationError):
            reopened.verify_archive_chain(reopened.open_current())

    def test_final_large_audit_does_not_consume_normal_receipt_read_budget(self):
        originals = [self.store.write_object(self.current.run_id, 'evidence', {'original': marker * (10 * 1024 * 1024)})
                     for marker in ('a', 'b')]
        request, result = {'original': 'request'}, {'original': 'result'}
        key = self.store.receipt_key(self.current, actor='owner', kind='accept', subject='ticket:T05:0', invocation='original')
        receipt = self.store.prepare_receipt(self.current, key, request, result)
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active'}, 'ticket:T05:0': {
                'status': 'accepted', 'stopped': True, 'originals': originals}},
            metadata=self.current.root['metadata'], outer={}, receipts=[receipt])
        self.store.compact(expected_revision=2, subject_ids=['ticket:T05:0'])
        reopened = storage.StateStore(self.path)
        current = reopened.open_current()
        reading = reopened.verify_archive_chain(current)
        self.assertGreater(reading['structured_bytes'], storage.READ_LIMIT + storage.EVIDENCE_LIMIT)
        before = reopened.bytes_read
        self.assertEqual(reopened.read_receipt(current, key, request), result)
        self.assertEqual(reopened.read_subject(current, 'stage:3:0').value, {'status': 'active'})
        self.assertGreater(reopened.bytes_read, before)
        self.assertLess(reopened.read_totals['bytes'], storage.READ_LIMIT)

    def test_current_256_observations_fit_and_retain_committed_nonce(self):
        observations = [{'event_id': 'event-' + str(number), 'original': 'x' * 2000} for number in range(256)]
        events = {observation['event_id']: {'digest': entry.digest(observation), 'applied': True}
                  for observation in observations}
        events['event-0']['fingerprints'] = {'source-call_ref': {
            'key': entry.digest(['call_ref', 'original-call']), 'digest': entry.digest('stage:3:0')}}
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active', 'events': events, 'observations': observations}},
            metadata=self.current.root['metadata'], outer={})
        self.assertLessEqual(self.current.root['subjects']['stage:3:0']['size'], storage.SUBJECT_LIMIT)
        reopened = storage.StateStore(self.path)
        current = reopened.open_current()
        self.assertEqual(reopened.read_subject(current, 'stage:3:0').value['observations'], observations)
        self.assertLessEqual(reopened.bytes_read, storage.READ_LIMIT)
        current = self.store.compact(expected_revision=2, subject_ids=[])
        reopened = storage.StateStore(self.path)
        self.assertEqual(reopened.read_source_stage('call_ref', 'original-call'), entry.digest('stage:3:0'))
        self.assertEqual(reopened.verify_archive_chain(current)['subjects'], 1)

    def test_runner_closed_history_exits_original_outer_cas(self):
        history = [{'event': 'turn.completed', 'turn': number} for number in range(32)]
        state = runner.CheckpointState(runner._read_checkpoint(self.path))
        state.update(history=history, status='active')
        runner._atomic_save(self.path, state)
        current = self.store.open_current()
        self.assertEqual(current.root['outer']['history'], [])
        archived = self.store.read_archive(current, 'runner-history:2').value
        self.assertEqual(archived['history'], history)
        self.assertEqual(current.root['subjects'], self.current.root['subjects'])

    def test_archive_prepared_locator_cannot_ack_before_root_commit(self):
        original_replace = storage.os.replace
        def fail_root(source, destination):
            if Path(destination) == self.path:
                raise OSError('root replacement interrupted')
            return original_replace(source, destination)
        with patch.object(storage.os, 'replace', side_effect=fail_root):
            with self.assertRaises(OSError):
                self.store.compact(expected_revision=1, subject_ids=['ticket:T05:0'])
        restored = storage.StateStore(self.path)
        current = restored.open_current()
        self.assertEqual(current.revision, 1)
        with self.assertRaises(entry.PreparationError) as error:
            restored.read_archive(current, 'ticket:T05:0')
        self.assertEqual(error.exception.code, 'subject_missing')
        committed = restored.compact(expected_revision=1, subject_ids=['ticket:T05:0'])
        self.assertEqual(restored.read_archive(committed, 'ticket:T05:0').value['status'], 'accepted')

    def test_archive_root_applied_before_return_repairs_original_proof(self):
        original_replace = storage.os.replace
        def lost_return(source, destination):
            result = original_replace(source, destination)
            if Path(destination) == self.path:
                raise KeyboardInterrupt('original root applied')
            return result
        with patch.object(storage.os, 'replace', side_effect=lost_return):
            with self.assertRaises(KeyboardInterrupt):
                self.store.compact(expected_revision=1, subject_ids=['ticket:T05:0'])
        reopened = storage.StateStore(self.path)
        current = reopened.open_current()
        authority = self.path.read_bytes()
        self.assertEqual(current.revision, 2)
        self.assertEqual(reopened.read_archive(current, 'ticket:T05:0').value['status'], 'accepted')
        self.assertEqual(self.path.read_bytes(), authority)
        self.assertNotIn('ticket:T05:0', current.root['subjects'])

    def test_archive_seals_256_events_and_replays_exact_observation(self):
        events = {'event:' + str(i): {'digest': entry.digest({'event': i}), 'applied': True}
                  for i in range(256)}
        self.current = self.store.commit_current(expected_revision=1, revision=2,
            subjects={'stage:3:0': {'status': 'active', 'events': events}},
            metadata=self.current.root['metadata'], outer={})
        sealed = self.store.compact(expected_revision=2, subject_ids=[])
        self.assertEqual(self.store.read_subject(sealed, 'stage:3:0').value['events'], {})
        self.assertTrue(self.store.read_event(sealed, 'event:7', {'event': 7})['applied'])
        with self.assertRaises(entry.PreparationError) as conflict:
            self.store.read_event(sealed, 'event:7', {'event': 8})
        self.assertEqual(conflict.exception.code, 'event_conflict')

    def test_archive_delivery_pages_do_not_expand_daily_recovery(self):
        metadata = copy.deepcopy(self.current.root['metadata'])
        self.current = self.store.compact(expected_revision=1, subject_ids=['ticket:T05:0'])
        for i in range(1, 40):
            owner = storage.StateStore(self.path)
            current = owner.open_current()
            identity = 'ticket:T05:' + str(i)
            current = owner.commit_current(expected_revision=current.revision, revision=current.revision + 1,
                subjects={'stage:3:0': {'status': 'active'}, identity: {
                    'status': 'accepted', 'stopped': True}}, metadata=metadata, outer={})
            owner.compact(expected_revision=current.revision, subject_ids=[identity])
        daily = storage.StateStore(self.path)
        current = daily.open_current()
        self.assertEqual(daily.files_read, 2)
        cursor, identities = None, []
        while True:
            page = storage.StateStore(self.path).read_archive_page(current, cursor)
            self.assertLessEqual(len(page['items']), 256)
            identities.extend(item['subject_id'] for item in page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertEqual(len(identities), 40)
        self.assertEqual(len(set(identities)), 40)
        self.assertNotIn('ticket:T05:0', current.root['subjects'])


class ArchiveOwnerTests(ProgressTests):
    def test_archive_production_256_events_preserves_original_replay(self):
        self.archive_original_owner_flow(reject_closed=False)
        first = None
        for number in range(256):
            observation = self.observation('running', 'result', ref='native:dispatcher')
            observation['event_id'] = 'transition-' + str(number)
            observation['receipt']['receipt_ref'] = 'host:transition-' + str(number)
            observation['receipt']['raw']['cursor'] = number
            if first is None:
                first = copy.deepcopy(observation)
            result = self.invoke('observe', observation)
            self.assertNotEqual(result['status'], 'blocked', (number, result))
        current = storage.StateStore(self.checkpoint).open_current()
        self.assertTrue(current.root.get('event_segments'))
        state = self.state()
        self.assertLess(len(state['events']), 256)
        self.assertLess(len(state['host']['seen']), 256)
        authority = self.checkpoint.read_bytes()
        replay = self.invoke('observe', first, revision=0)
        self.assertTrue(replay['acknowledged'])
        self.assertEqual(self.checkpoint.read_bytes(), authority)
        changed = copy.deepcopy(first)
        changed['receipt']['raw']['cursor'] = 'changed'
        with self.assertRaises(entry.PreparationError) as conflict:
            self.invoke('observe', changed, revision=0)
        self.assertEqual(conflict.exception.code, 'event_conflict')
        reading = progress.verify_archived_completion(state)
        self.assertGreaterEqual(reading['subjects'], 2)

    def test_archive_production_32_completed_slices_admits_33(self):
        planning = self.finish_design('continuous')
        self.context = self.context_for(3)
        successor = self.input_for(3, self.state()['accepted'])
        scope = planning['readiness']['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            successor['scope'][key] = copy.deepcopy(scope[key])
        successor['scope']['owned_paths'] = copy.deepcopy(scope['implementation_paths'])
        successor['authorization']['scope_digest'] = entry.digest(successor['scope'])
        self.begin('continuous', successor)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        first_accept = None
        for number in range(32):
            content = ("print('original path " + str(number) + "')\n").encode()
            identity, ref = 'slice-' + str(number), 'native:executor-' + str(number)
            execution = self.execution_input()
            result = self.invoke('allocation', {'allocation_id': identity, 'operation': 'prepare', 'handoff': execution})
            self.assertEqual(result['next_action']['operation'], 'invoke-host', result)
            slot = self.allocation_slot(identity)
            bound = self.invoke('allocation', {'allocation_id': identity, 'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref=ref)})
            (self.flow / 'impl.py').write_bytes(content)
            slot = self.allocation_slot(identity)
            received = self.invoke('allocation', {'allocation_id': identity, 'operation': 'receive',
                'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=ref),
                'result': {'delivery_id': 'result-' + str(number), 'status': 'completed', 'payload': {
                    'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                    'tests': ['archive fixture'], 'write_release': bound['next_action']['result']['write_release'],
                    'plan_refs': execution['semantic']['plan_refs'],
                    'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
            self.assertNotEqual(received['status'], 'blocked', (number, received))
            accept = {'allocation_id': identity, 'operation': 'accept',
                'decision': {'reference': 'dispatcher:accept-' + str(number)}}
            accepted = self.invoke('allocation', accept)
            self.assertNotEqual(accepted['status'], 'blocked', accepted)
            if number == 0:
                first_accept = accept
            if number == 30:
                self.assertEqual(len(self.state()['allocations']), 31)
        state = self.state()
        self.assertEqual(state['allocations'], {})
        store = storage.StateStore(self.checkpoint)
        current = store.open_current()
        page = store.read_archive_page(current)
        self.assertEqual(len(page['items']), 32)
        self.assertNotIn('ticket:FEP-01:0', current.root['subjects'])
        self.assertTrue(self.invoke('allocation', first_accept)['acknowledged'])
        result = self.invoke('allocation', {'allocation_id': 'slice-32', 'operation': 'prepare',
                                           'handoff': self.execution_input()})
        self.assertEqual(result['next_action']['operation'], 'invoke-host', result)

    def test_archive_original_owner_preserves_receipt_and_git_ownership(self):
        self.archive_original_owner_flow()

    def test_archive_repair_preserves_original_nonce_new_lease_and_two_generations(self):
        self.archive_original_owner_flow(reject_closed=False, prepare_successor=False)
        store = storage.StateStore(self.checkpoint)
        original = store.read_archive(store.open_current(), 'allocation:slice-one').value['slot']
        first = copy.deepcopy(original)
        old_receipt = self.receipt(first['record'], ref='native:archive-executor')
        old_release = progress.dispatch.execution_release_token(progress.handoff.unseal(first['record']))
        execution = self.execution_input()
        prepared = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'repair',
            'invocation_id': 'archive-repair-second', 'handoff': execution})
        self.assertNotEqual(prepared['status'], 'blocked', prepared)
        self.assertEqual(prepared['next_action']['payload']['payload']['execution_invocation']['operation'], 'continue')
        slot = self.allocation_slot('slice-one')
        self.assertNotEqual(slot['record']['request']['attempt'], first['record']['request']['attempt'])
        refused = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind', 'receipt': old_receipt})
        self.assertEqual(refused['error']['code'], 'attempt_mismatch', refused)
        fresh = self.receipt(slot['record'], ref='native:archive-executor')
        fresh['receipt_ref'] += ':second'
        bound = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind', 'receipt': fresh})
        self.assertEqual(bound['next_action']['result']['status'], 'bound', bound)
        release = bound['next_action']['result']['write_release']
        self.assertNotEqual(release, old_release)
        body = b"print('archive repaired')\n"
        (self.flow / 'impl.py').write_bytes(body)
        slot = self.allocation_slot('slice-one')
        stopped = self.receipt(slot['record'], 'stopped', 'result', ref='native:archive-executor')
        stopped['receipt_ref'] += ':second'
        received = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'receive', 'receipt': stopped,
            'result': {'delivery_id': 'archive-repaired', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(body).hexdigest()},
                'tests': ['archive repair original boundary'], 'write_release': release,
                'plan_refs': execution['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.assertEqual(received['next_action']['result']['status'], 'received', received)
        accepted = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:archive-repaired-accept'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
        second = copy.deepcopy(self.allocation_slot('slice-one'))
        self.assertEqual(second['metrics']['slice_id'], first['metrics']['slice_id'])
        self.assertEqual(second['metrics']['first_submission'], first['metrics']['first_submission'])
        owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        owner.compact_history()
        store = storage.StateStore(self.checkpoint)
        current = store.open_current()
        self.assertEqual(store.read_archive(current, 'allocation:slice-one').value['slot'], second)
        self.assertEqual(store.read_native_archive(current, 'native:archive-executor').value['slot'], first)
        self.assertEqual(progress.verify_archived_completion(self.state())['subjects'], 2)
        old_accept = {'allocation_id': 'slice-one', 'operation': 'accept',
                      'decision': {'reference': 'dispatcher:archive-accept'}}
        before = self.checkpoint.read_bytes()
        replay = self.invoke('allocation', old_accept)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertFalse(replay['next_action']['write_authority'])
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def archive_original_owner_flow(self, *, reject_closed=True, prepare_successor=True):
        planning = self.finish_design('continuous')
        self.context = self.context_for(3)
        successor = self.input_for(3, self.state()['accepted'])
        scope = planning['readiness']['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            successor['scope'][key] = copy.deepcopy(scope[key])
        successor['scope']['owned_paths'] = copy.deepcopy(scope['implementation_paths'])
        successor['authorization']['scope_digest'] = transfer.entry.digest(successor['scope'])
        self.begin('continuous', successor)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        controller_request, controller_settings = copy.deepcopy(self.request), copy.deepcopy(self.settings)
        execution = self.execution_input()
        self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'prepare', 'handoff': execution})
        slot = self.allocation_slot('slice-one')
        bound = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:archive-executor')})
        content = b"print('archive original')\n"
        (self.flow / 'impl.py').write_bytes(content)
        slot = self.allocation_slot('slice-one')
        receive = {'allocation_id': 'slice-one', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:archive-executor'),
            'result': {'delivery_id': 'archive-result', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                'tests': ['archive fixture boundary'], 'write_release': bound['next_action']['result']['write_release'],
                'plan_refs': execution['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}}
        self.invoke('allocation', receive)
        accept = {'allocation_id': 'slice-one', 'operation': 'accept',
                  'decision': {'reference': 'dispatcher:archive-accept'}}
        accepted = self.invoke('allocation', accept)
        original = copy.deepcopy(self.allocation_slot('slice-one'))
        owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        current_store = storage.StateStore(self.checkpoint)
        current_root = current_store.open_current()
        stage = progress.stage_subject_value(owner.state)
        if os.environ.get('T05_DIAGNOSTIC_PATH'):
            with open(os.environ['T05_DIAGNOSTIC_PATH'], 'x') as stream:
                storage.json.dump({'accepted_slot_bytes': len(storage.encode(original)),
                    'current_subject_sizes': {name: ref['size'] for name, ref in current_root.root['subjects'].items()},
                    'stage_field_bytes': {name: len(storage.encode(value)) for name, value in stage.items()},
                    'control_field_bytes': {name: len(storage.encode(value)) for name, value in stage['control']['context']['handoff_progress'].items()}}, stream)
        before = owner.state['control']['context']['handoff_progress']['executions'][0]
        owner.compact_history()
        restored = self.state()
        self.assertEqual(restored['allocations'], {})
        self.assertEqual(restored['control']['context']['handoff_progress']['executions'], [])
        self.assertEqual(progress.control.current_accepted_results(restored['control']['context']['handoff_progress']), [before])
        store = storage.StateStore(self.checkpoint)
        self.assertEqual(store.read_archive(store.open_current(), 'allocation:slice-one').value['slot'], original)
        self.assertEqual(store.read_native_archive(store.open_current(), 'native:archive-executor').value['slot'], original)
        owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        self.assertEqual(owner.finalization_owner('native:archive-executor')[1], original['record']['acceptance']['digest'])
        authority = self.checkpoint.read_bytes()
        replay = self.invoke('allocation', accept)
        self.assertTrue(replay['acknowledged'])
        self.assertFalse(replay['next_action']['write_authority'])
        self.assertEqual(replay['next_action']['result']['status'], accepted['next_action']['result']['status'])
        self.assertEqual(self.checkpoint.read_bytes(), authority)
        progress.control_git.verify_execution_start(self.flow, restored['control']['context']['handoff_progress'])
        plain = copy.deepcopy(restored['control']['context'])
        plain['handoff_progress'] = dict(plain['handoff_progress'])
        with self.assertRaises(progress.control.ControlError):
            progress.control.validate_current_context(plain)
        original_dispatch = progress.dispatch.handle
        def serialized_dispatch(request, **kwargs):
            # Model the existing JSON protocol transport, then run the actual B.
            return original_dispatch(storage.json.loads(storage.json.dumps(request)), **kwargs)
        if prepare_successor:
            with patch.object(progress.dispatch, 'handle', side_effect=serialized_dispatch):
                fresh = self.invoke('allocation', {'allocation_id': 'slice-two', 'operation': 'prepare',
                                                  'handoff': self.execution_input()})
            self.assertEqual((fresh.get('next_action') or {}).get('operation'), 'invoke-host', fresh)
        self.request, self.settings = controller_request, controller_settings
        if reject_closed:
            rejected = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'prepare', 'handoff': execution})
            self.assertEqual(rejected['error']['code'], 'attempt_ended')
        return planning


class ArchiveLedgerTests(LedgerReviewProgressTests):
    def invoke(self, operation, data=None, revision=None):
        result = super().invoke(operation, data, revision)
        if operation == 'allocation' and (data or {}).get('operation') == 'prepare':
            self.assertNotEqual(result['status'], 'blocked', storage.json.dumps(result))
        return result

    def write_planning_spec(self):
        from test_stage_scope_progress import ScopeProgressTests
        plan = ScopeProgressTests.inline_plan(self)
        plan.update(schema_version=2, spec_ref={'path': 'docs/spec.md', 'section': 'Implementation Decisions'},
                    contract_refs=['FEP-01:What to build', 'FEP-01:AC-1', 'PRD:D1:Plan'])
        plan['behavior_slices'] = [{'id': 'original',
            'step_ids': [step['id'] for step in plan['steps'] + plan['artifact_steps']],
            'ac_refs': ['FEP-01:AC-1'], 'verification_refs': [item['id'] for item in plan['verifications']],
            'read_only': [], 'shared_state': []}]
        content = ('approved design\nTicket ID: FEP-01\nPlan Version: 1\n'
                   '**What to build:** approved behavior\n**Blocked by:** None\nAC-1: approved behavior\n'
                   '## Implementation Decisions\n### D1 — Plan\n```execution-plan-v2\n' +
                   storage.json.dumps(plan) + '\n```\n')
        (self.flow / 'docs/spec.md').write_text(content)
        return content, plan

    def mark_reviewable(self, candidate, *, fixture_execution=True, evidence_suffix=''):
        """Deterministic native/command adapters; no native现场 assertion."""
        catalog = self.state()['scope_authority']['execution_plan']['catalog']
        for ticket in catalog['tickets']:
            axes = [('local:' + ticket['ticket_id'] + ':' + group['id'], 'local-review')
                    for group in ticket['plan']['behavior_slices']]
            axes.append(('ticket:' + ticket['ticket_id'], 'ticket-accept'))
            for axis, action in axes:
                checkpoint = self.state()['control']['context']['handoff_progress']
                required = progress.review_evidence.local_required_checks(catalog, axis, str(self.flow))
                checks = transfer.checks_for(required, candidate)
                source = {'adapter': 'fixture-command', 'call_ref': 'archive-check:' + axis,
                    'response_ref': 'archive-check-stop:' + axis, 'raw': {
                    'checks': copy.deepcopy(checks), 'source_unchanged': True, 'stopped': True,
                    'dispatcher_ref': self.state()['control']['context']['carrier']['ref'],
                    'candidate': candidate, 'run_id': progress.read_record(self.checkpoint)['current']['run_id'],
                    'package_digest': transfer.entry.digest(self.state()['packages']),
                    'plan_authority': checkpoint['plan_authority'],
                    'environment': {'adapter': 'fixture-command', 'cwd': str(self.flow)},
                    'fixture': {'axis': axis, 'candidate': candidate}}}
                prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis,
                    'candidate': candidate, 'actor_ref': 'task',
                    'verification': {'plan_authority': checkpoint['plan_authority'], 'checks': checks, 'source': source}})
                self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-review', prepared)
                scope = prepared['next_action']['review_scope']
                response = 'archive-review-stop:' + axis
                observed = self.invoke('review-activity', {'operation': 'observe', 'axis': axis,
                    'candidate': candidate, 'actor_ref': 'task', 'action_id': prepared['next_action']['action_id'],
                    'receipt': {'adapter': 'fixture-native', 'call_ref': 'archive-review-call:' + axis,
                        'response_ref': response, 'status': 'stopped', 'ref': 'native:archive-reviewer:' + axis,
                        'raw': {'review': {'status': 'accepted', 'findings': [], 'checks': checks,
                            'read_paths': scope['read_paths'], 'ac_refs': scope['ac_refs'],
                            'dependency_claim_ids': scope['dependency_claim_ids'],
                            'requirement_refs': scope['requirement_refs'], 'review_scope': scope}}}})
                self.assertNotEqual(observed['status'], 'blocked', observed)
                accepted = self.invoke('control', {'action': action, 'evidence': {
                    'axis': axis, 'candidate': candidate, 'result_ref': response},
                    'receipt': {'controller_ref': 'task', 'reference': 'controller:archive-review:' + axis}})
                self.assertNotEqual(accepted['status'], 'blocked', accepted)
        return ProgressTests.mark_reviewable(self, candidate, fixture_execution=False, evidence_suffix=evidence_suffix)

    def test_archive_ticket_accept_retains_exact_ledger_generation(self):
        original_protocol = transfer.discussion_protocol.handle
        def original_ledger(request):
            try:
                return original_protocol(request)
            except transfer.discussion_protocol.ProtocolError as error:
                if request.get('operation') == 'workflow-control' and request.get('action') == 'plan-execution':
                    raise AssertionError('original ledger preparation rejected: ' + str(error)) from error
                raise
        with patch.object(transfer.discussion_protocol, 'handle', side_effect=original_ledger):
            self.original_ledger_review_candidate()
        state = self.state()
        self.assertNotIn('ledger-review', state['allocations'])
        pointer = state['control']['store_ref']
        current = self.read_ledger()
        self.assertEqual(current['workflow_control_ref'], pointer)
        store = storage.StateStore(self.ledger)
        context, body = store.read_ledger_control(pointer)
        self.assertEqual(context, state['control']['context'])
        self.assertTrue(context['handoff_progress']['archived_executions'])
        self.assertEqual(state['review_activity'], {})
        self.assertEqual(state['review_history'], [])
        self.assertEqual(list(storage.StateStore(self.checkpoint).open_current().root['subjects']), [state['stage_subject']])
        self.assertGreaterEqual(progress.verify_archived_completion(state)['subjects'], 3)
        cstore = storage.StateStore(self.checkpoint)
        archived = cstore.read_archive(cstore.open_current(), 'allocation:ledger-review').value
        original_pointer = archived['ledger_ref']
        _, original_body = store.read_ledger_control(original_pointer)
        original = store.read_evidence(archived['execution_ref'], run_id=original_pointer['reference']['run_id'])['value']['execution']
        self.assertEqual(original['agent_ref'], 'native:ledger-review-agent')
        certificate = original_body['receipt']
        replay = store.read_ledger_receipt(pointer['topic_id'], certificate['key'], certificate['fingerprint'])
        self.assertEqual(replay['control']['store_ref'], original_pointer)
        self.assertTrue(replay['idempotent_replay'])


class ArchiveCompletionTests(ArchiveOwnerTests):
    def mark_reviewable(self, candidate, **kwargs):
        return ArchiveLedgerTests.mark_reviewable(self, candidate, **kwargs)

    def invoke(self, operation, data=None, revision=None):
        if operation == 'review-activity' and (data or {}).get('operation') == 'observe' and 'review' not in data['receipt']['raw']:
            data = copy.deepcopy(data)
            slot = self.state()['review_activity'][data['axis']]
            owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
            scope = owner.review_scope(data['axis'], slot.get('subject'))
            data['receipt']['raw']['review'] = {'status': 'accepted', 'findings': [],
                'checks': slot['prepare']['verification']['checks'], 'read_paths': scope['read_paths'],
                'ac_refs': scope['ac_refs'], 'dependency_claim_ids': scope['dependency_claim_ids'],
                'requirement_refs': scope['requirement_refs'], 'review_scope': scope}
        return super().invoke(operation, data, revision)

    def observation(self, status='ready', event='create', result=None, ref='native:designer'):
        observation = super().observation(status, event, result, ref)
        if status == 'stopped':
            owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
            # Authenticated fixture census, after original role stop receipts.
            observation['lifecycle']['pages'][0]['response']['data'] = [
                {'id': identity, 'status': {'type': 'idle'}} for identity in sorted(owner.native_refs())]
        return observation

    def test_archive_completion_runner_rejects_missing_original_proof(self):
        planning = self.archive_original_owner_flow(reject_closed=False, prepare_successor=False)
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'original archived implementation')
        candidate = self.flow_git('rev-parse', 'HEAD')
        payload = {'artifacts': ['impl.py'], 'checks': ['original archive fixture'], 'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': 'native:delivery-' + axis, 'status': 'accepted'}
                       for axis in ('standards', 'spec')}, 'verification': {}}
        accepted = self.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        implementation = progress.handoff.render_implementation_delivery(str(self.checkpoint))['stage_result']
        closure = self.finish_next(4, 'continuous')
        self.assertEqual(closure['status'], 'accepted', closure)
        state = {'stage_results': {'stage2': planning['completion']['stage_result'],
            'stage3': implementation, 'stage4': closure['completion']['stage_result']}}
        completion = runner._verify_run_completion(self.checkpoint, state)
        self.assertTrue(completion['completed'])
        self.assertGreaterEqual(completion['archive_reading']['subjects'], 5)
        store = storage.StateStore(self.checkpoint)
        original = store.read_archive(store.open_current(), 'allocation:slice-one').value
        receipt = original['slot']['record']['receipts'][0]['raw_ref']
        store._path(receipt['sha256']).unlink()
        with self.assertRaises(runner.WorkflowError):
            runner._verify_run_completion(self.checkpoint, state)

    def test_archive_pause_unknown_keeps_original_stop_and_exact_receipt(self):
        self.archive_original_owner_flow(reject_closed=False, prepare_successor=False)
        stop = self.invoke('pause')['next_action']
        unknown = self.invoke('observe', self.observation('unknown', 'result', ref='native:dispatcher'))
        self.assertEqual(unknown['next_action']['operation'], 'inspect-host-state', unknown)
        state = self.state()
        self.assertEqual(state['host']['status'], 'unknown')
        self.assertFalse(state['stopped'])
        self.assertEqual(state['host_action']['action_id'], stop['action_id'])
        owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        with self.assertRaises(entry.PreparationError) as pending:
            owner.compact_history()
        self.assertEqual(pending.exception.code, 'archive_pending')
        accept = {'allocation_id': 'slice-one', 'operation': 'accept',
                  'decision': {'reference': 'dispatcher:archive-accept'}}
        self.assertTrue(self.invoke('allocation', accept)['acknowledged'])
        resolved = self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(resolved['status'], 'paused', resolved)

    def test_archive_original_actor_cannot_bind_another_execution(self):
        self.archive_original_owner_flow(reject_closed=False)
        slot = self.allocation_slot('slice-two')
        result = self.invoke('allocation', {'allocation_id': 'slice-two', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:archive-executor')})
        self.assertEqual(result['error']['code'], 'identity_mismatch', result)
        self.assertFalse(self.state()['control']['context']['handoff_progress']['executions'][0]['stopped'])


class ArchiveAffectedTests(unittest.TestCase):
    @staticmethod
    def original_stopped_census(flow, observation):
        if observation['receipt']['status'] != 'stopped':
            return observation
        owner = progress.Progress(flow.checkpoint, progress.read_active_owner(flow.checkpoint))
        checkpoint = owner.state['control']['context']['handoff_progress']
        results = progress.control.all_execution_results(checkpoint) + checkpoint.get('carried_results', [])
        for source in checkpoint.get('plan_revision_sources', []):
            results.extend(source['executions'] + source['carried_results'])
        stopped = {item['agent_ref'] for item in results if item.get('stopped')}
        stopped.update(view['reviewer_ref'] for view in checkpoint.get('review_claims', {}).values())
        rows = observation['lifecycle']['pages'][0]['response']['data']
        existing = {row['id'] for row in rows}
        missing = owner.native_refs() - existing
        flow.assertLessEqual(missing, stopped, 'fixture may add only original stopped archived roles')
        rows.extend({'id': identity, 'status': {'type': 'idle'}} for identity in sorted(missing))
        return observation

    @staticmethod
    def original_slot(flow, allocation_id, ticket_id=None):
        if ticket_id is None:
            tickets = flow.state()['scope_authority']['execution_plan']['catalog']['tickets']
            flow.assertEqual(len(tickets), 1)
            ticket_id = tickets[0]['ticket_id']
        store = storage.StateStore(flow.checkpoint)
        current = store.open_current()
        subject_id = 'ticket:' + ticket_id + ':0'
        if subject_id in current.root['subjects']:
            slots = store.read_subject(current, subject_id).value['allocations']
            if allocation_id in slots:
                return slots[allocation_id]
        return store.read_archive(current, 'allocation:' + allocation_id).value['slot']

    def layered_fixture(self):
        from test_layered_review import LayeredReviewTests
        flow = LayeredReviewTests(methodName='runTest')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        flow.allocation_slot = lambda identity, ticket=None: self.original_slot(flow, identity, ticket)
        original_observation = flow.observation
        flow.observation = lambda *args, **kwargs: self.original_stopped_census(flow, original_observation(*args, **kwargs))
        return flow

    def test_archive_affected_ticket_repair_preserves_original_claims(self):
        from test_layered_review import LayeredReviewTests
        flow = self.layered_fixture()
        LayeredReviewTests.test_ticket_and_delivery_certificate_preserve_unchanged_ticket_claims(flow)

    def test_archive_affected_t04_original_claims_and_one_cas(self):
        from test_plan_revision_progress import PlanRevisionProgressTests, T04Flow
        fixture = PlanRevisionProgressTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original_observation = T04Flow.observation
        def archive_observation(flow, *args, **kwargs):
            return self.original_stopped_census(flow, original_observation(flow, *args, **kwargs))
        with patch.object(T04Flow, 'allocation_slot', self.original_slot), patch.object(T04Flow, 'observation', archive_observation):
            PlanRevisionProgressTests.test_t04_revision_preserves_original_unaffected_claims_and_one_cas(fixture)

    def test_archive_affected_cli_original_proof_rejections(self):
        from test_layered_review import LayeredReviewTests
        flow = self.layered_fixture()
        original_run = subprocess.run
        originals = []
        def original_cli(command, *args, **kwargs):
            if len(command) > 1 and command[1] == str(Path(progress.control_git.__file__)) and kwargs.get('input'):
                envelope = json.loads(kwargs['input'])
                if envelope['request']['action'] == 'review-converged':
                    originals.append(copy.deepcopy(envelope))
                    # The original adversarial cases must reach their original
                    # issuer/certificate checks using today's valid C context.
                    # Keep every forged proof and original rejection assertion.
                    envelope['request']['context'] = flow.state()['control']['context']
                    kwargs['input'] = json.dumps(envelope)
            return original_run(command, *args, **kwargs)
        with patch.object(subprocess, 'run', side_effect=original_cli):
            LayeredReviewTests.test_layered_coverage_git_cli_consumes_original_proofs(flow)
        before = flow.checkpoint.read_bytes()
        stale = original_run([sys.executable, str(Path(progress.control_git.__file__))],
            input=json.dumps(originals[0]), text=True, capture_output=True, cwd=flow.flow)
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        self.assertIn('archived execution facts differ from the original Controller checkpoint', json.loads(stale.stdout)['error'])
        self.assertNotIn('Traceback', stale.stderr)
        self.assertEqual(flow.checkpoint.read_bytes(), before)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(StateArchiveTests))
    suite.addTests(ArchiveOwnerTests(name) for name in ArchiveOwnerTests.__dict__ if name.startswith('test_'))
    suite.addTests(ArchiveLedgerTests(name) for name in ArchiveLedgerTests.__dict__ if name.startswith('test_'))
    suite.addTests(ArchiveCompletionTests(name) for name in ArchiveCompletionTests.__dict__ if name.startswith('test_'))
    suite.addTests(loader.loadTestsFromTestCase(ArchiveAffectedTests))
    return suite
