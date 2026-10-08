"""C orchestration through real A, B, Git and ledger; only the host is a double."""
import copy
import json
import tempfile
import sys
import unittest
import io
import os
import subprocess
import hashlib
import errno
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_stage_transfer as transfer
import workflow_progress as progress
import execution_plan
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/stages/guided-implementation/scripts"))
import workflow as runner
from test_discussion_protocol import DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport


class ProgressTests(transfer.StageTransferTests):
    # Reuse fixture helpers, not the parent's test inventory.
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkpoint = (Path(temporary.name) / "checkpoint.json").resolve()
        self.host_response_sequence = 0

    def state(self):
        return progress.read_active_owner(self.checkpoint)[progress.KEY]

    def save_transport(self, **changes):
        identity = progress.read_projection(self.checkpoint, projection='identity')
        store = progress.state_store.StateStore(self.checkpoint)
        original = store.open_current()
        outer = {key: copy.deepcopy(value) for key, value in identity.record.items() if key != progress.KEY}
        outer.setdefault('transport', {}).update(changes)
        progress.save_outer_record(self.checkpoint, outer, expected_revision=identity.authority_revision)
        current = store.open_current()
        # Runner transport updates retain the exact Controller facts and proof refs.
        for key in ('metadata', 'subjects', 'scope', 'delivery'):
            self.assertEqual(current.root.get(key), original.root.get(key))
        self.assertEqual(progress.read_record(self.checkpoint)['previous'], original.reference)
        retained = progress.state_store.StateStore(self.checkpoint).read_evidence(
            original.reference, run_id=original.run_id, kind='root')['value']
        self.assertEqual(retained, original.root)

    def allocation_slot(self, allocation_id, ticket_id=None):
        if ticket_id is None:
            tickets = self.state()['scope_authority']['execution_plan']['catalog']['tickets']
            self.assertEqual(len(tickets), 1, 'multi-Ticket fixtures must select the exact subject')
            ticket_id = tickets[0]['ticket_id']
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        subject_id = 'ticket:' + ticket_id + ':0'
        if subject_id in current.root['subjects']:
            slots = store.read_subject(current, subject_id).value['allocations']
            if allocation_id in slots:
                return slots[allocation_id]
        return store.read_archive(current, 'allocation:' + allocation_id).value['slot']

    def retired_fixture(self, carrier_ref='old-task'):
        import test_workflow_control
        helper = test_workflow_control.WorkflowControlTests()
        context = helper.context()
        context['controller_ref'] = 'task'
        def apply(action, evidence):
            nonlocal context
            result = progress.control.transition({'schema_version': 7, 'actor_ref': 'task',
                'context': context, 'action': action, 'evidence': evidence})
            context = result['context']
            return result
        planned = apply('prepare', {'target': 'local', 'project': 'project', 'title': 'Old requirement',
            'missing_context': [], 'configuration': helper.configuration(), 'next_step': 'stage2',
            'archive_ref': None, 'gate_open': True})
        attempt = planned['plan']['plan_id']
        apply('decide', {'plan_id': attempt, 'intent': 'confirm'})
        apply('creation-result', {'status': 'ready', 'ref': carrier_ref, 'attempt': attempt})
        received = apply('receive', {'delivery_id': 'old-delivery', 'source_ref': carrier_ref, 'attempt': attempt,
            'requirement_identity': context['requirement_identity'], 'commit': 'b' * 40,
            'verified_commit_hash': context['requirement_identity']['sha256']})
        apply('accept', {'delivery_digest': received['delivery_digest']})
        ready = apply('successor-ready', {'ref': 'native:designer', 'stage': 2, 'role': 'solution-designer',
            'input_digest': received['delivery_digest'], 'binding_verified': True, 'activated': False,
            'confirmed': True, 'archive_ref': carrier_ref, 'takeover_proof': test_workflow_control.takeover_proof(carrier_ref, attempt)})
        self.context['retired_handoffs'] = context['retired_handoffs']
        evidence = context['retired_handoffs'][ready['handoff_id']]['successor']
        request = {'action': 'successor-ready', 'evidence': evidence,
                   'receipt': {'controller_ref': 'task', 'reference': 'old-takeover'}}
        self.takeover_input = copy.deepcopy(request)
        self.takeover_input['evidence'].pop('takeover_proof')
        self.takeover_transaction = {'request': request, 'takeover_input': self.takeover_input,
            'result': ready, 'port': {'context': context, 'discussion': None}}
        return ready['handoff_id']

    def archive_receipt(self, effect, status):
        receipt = {'adapter': 'fixture', 'invocation_id': 'call:' + effect['operation_id'],
            'response_id': status + ':' + effect['operation_id'], 'ref': effect['ref'], 'no_write': status == 'issued',
            'raw': {key: effect[key] for key in ('operation_id', 'operation', 'ref')} if status == 'issued' else {'status': status}}
        return {'action': 'archive-result', 'evidence': {'handoff_id': effect['handoff_id'],
            'operation_id': effect['operation_id'], 'ref': effect['ref'], 'status': status, 'receipt': receipt}, 'receipt': receipt}

    def test_retired_archive_after_start_preserves_current_native_context(self):
        handoff_id = self.retired_fixture()
        self.finish_design('continuous')
        outer = progress.read_active_owner(self.checkpoint)
        owner = progress.Progress(self.checkpoint, outer)
        # Completed production control calls commit the original unaugmented
        # input/result receipt and remove their active transaction in one CAS.
        subject = owner.control_receipt_subject(self.takeover_input)
        self.assertEqual(subject, self.takeover_input['evidence']['input_digest'])
        store, current, key = owner.receipt_store('control:successor-ready', subject,
            self.takeover_input['receipt']['reference'])
        result = {key: value for key, value in self.takeover_transaction['result'].items() if key != 'context'}
        owner._commit_receipts.append(store.prepare_receipt(current, key, self.takeover_input, result))
        owner.save()
        committed = store.open_current()
        self.assertEqual(store.read_receipt(committed, key, self.takeover_input), result)
        self.assertFalse(owner.state.get('control_transactions'))
        intent = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'automatic'}})
        effect = intent['next_action']['result']['effects'][0]
        pins = self.state()['control_transactions']
        original_pin = next(pin for pin in pins.values() if pin.get('reserve_owner'))
        reserve = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'reserves' / 'business' / hashlib.sha256(original_pin['reserve_action'].encode()).hexdigest()
        self.assertTrue(reserve.exists())
        self.invoke('control', self.archive_receipt(effect, 'issued'))
        with patch.object(progress.dispatch, 'checkpoint', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('control', self.archive_receipt(effect, 'unknown'))
        self.assertFalse(progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint)).unresolved_control_transactions())
        predecessor = self.state()['accepted']
        self.context = self.context_for(3)
        self.begin('continuous', self.input_for(3, predecessor))
        self.assertTrue(reserve.exists(), 'successor start retains the exact original archive capacity')
        self.assertTrue(any(pin.get('reserve_action') == original_pin['reserve_action']
            for pin in self.state()['control_transactions'].values()))
        before = copy.deepcopy(self.state()['control']['context'])
        history = copy.deepcopy(self.state()['history'])
        late = self.invoke('control', self.archive_receipt(effect, 'unknown'))
        after = self.state()['control']['context']
        self.assertEqual({k:v for k,v in before.items() if k != 'retired_handoffs'},
                         {k:v for k,v in after.items() if k != 'retired_handoffs'})
        self.assertEqual(self.state()['history'], history)
        query = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'user-recovery'}})['next_action']['result']['effects'][0]
        self.assertEqual((query['operation'], query['ref']), ('read-archive-state', 'old-task'))
        issued_query = self.invoke('control', self.archive_receipt(query, 'issued'))
        self.assertTrue(issued_query['next_action']['result']['ok'], issued_query)
        reads = []
        original_open = Path.open
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        def opened(path, mode='r', *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if 'r' in mode and (path == self.checkpoint or path.is_relative_to(directory) or
                                path.is_relative_to(self.root)):
                original_read = stream.read
                def read(*args, **kwargs):
                    value = original_read(*args, **kwargs)
                    reads.append((str(path), len(value.encode('utf-8') if isinstance(value, str) else value)))
                    return value
                stream.read = read
            return stream
        with patch.object(Path, 'open', opened):
            archived_query = self.invoke('control', self.archive_receipt(query, 'archived'))
        self.assertTrue(archived_query['next_action']['result']['ok'], archived_query)
        self.assertFalse(reserve.exists(), 'exact committed archived readback settles the inherited original slot')
        self.assertLessEqual(sum(size for _, size in reads), progress.state_store.READ_LIMIT)
        self.assertFalse(any('/archives/' in path for path, _ in reads), reads)
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        self.assertLessEqual(len(progress.state_store.encode({'format': progress.state_store.FORMAT,
            'run_id': current.run_id, 'kind': 'root', 'value': current.root})), progress.state_store.ROOT_LIMIT)
        subject = store.read_subject(current, current.root['metadata']['stage_subject'])
        self.assertLessEqual(subject.reference['size'], progress.state_store.SUBJECT_LIMIT)
        self.assertFalse(any(item.get('scope') == 'archive'
            for item in subject.value.get('control_transactions', {}).values()))
        changed = self.archive_receipt(query, 'archived')
        changed['evidence']['status'] = 'unknown'
        bytes_before = self.checkpoint.read_bytes()
        conflicting_key = self.invoke('control', changed)
        self.assertEqual(conflicting_key['next_action']['result']['error']['code'], 'receipt_conflict')
        self.assertEqual(self.checkpoint.read_bytes(), bytes_before)
        self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['archive_status'], 'archived')
        replay = self.invoke('control', self.archive_receipt(effect, 'unknown'))
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['archive_status'], 'archived')
        conflict = self.invoke('control', self.archive_receipt(effect, 'archived'))
        self.assertFalse(conflict['next_action']['result']['ok'])
        saved = self.state()
        self.assertEqual(saved['control']['context']['retired_handoffs'][handoff_id]['archive_status'], 'archived')
        self.assertEqual(len(saved['control']['context']['retired_handoffs'][handoff_id]['conflicts']), 1)
        self.assertEqual(saved['history'], history)
        replayed_takeover = self.invoke('control', self.takeover_input)
        self.assertTrue(replayed_takeover['acknowledged'])
        self.assertEqual(replayed_takeover['next_action']['result']['effects'], [])
        self.assertEqual(self.state()['control']['context'], saved['control']['context'])
        # The original receipt cannot authorize changed bytes under the same
        # frozen delivery, or a different delivery that has no saved receipt.
        changed_takeover = copy.deepcopy(self.takeover_input)
        changed_takeover['evidence']['ref'] = 'native:other'
        conflicting = self.invoke('control', changed_takeover)
        self.assertEqual(conflicting['error']['code'], 'receipt_conflict')
        self.assertEqual(self.state()['control']['context'], saved['control']['context'])
        fresh_takeover = copy.deepcopy(self.takeover_input)
        fresh_takeover['evidence']['input_digest'] = 'f' * 64
        fresh = self.invoke('control', fresh_takeover)
        self.assertFalse(fresh['acknowledged'])
        self.assertEqual(fresh['error']['code'], 'host_evidence_missing')
        self.assertEqual(self.state()['control']['context'], saved['control']['context'])

    def test_archive_reservation_exists_before_dispatch_and_survives_pending_effect(self):
        handoff_id = self.retired_fixture()
        self.begin()
        store = progress.state_store.StateStore(self.checkpoint)
        business = store.directory / 'reserves' / 'business'
        before = {path.name for path in business.iterdir()}
        snapshots = []
        original = progress.dispatch.checkpoint
        def observed(*args, **kwargs):
            snapshots.append({path.name: path.stat().st_size for path in business.iterdir()})
            return original(*args, **kwargs)
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'reserved-archive'}}
        with patch.object(progress.dispatch, 'checkpoint', side_effect=observed):
            result = self.invoke('control', request)
        self.assertEqual(len(result['next_action']['result']['effects']), 1, result)
        self.assertEqual(len(snapshots), 1)
        reserved = set(snapshots[0]) - before
        self.assertEqual(len(reserved), 1, 'archive dispatch needs its own durable receipt capacity')
        self.assertEqual(snapshots[0][next(iter(reserved))], 16 * 1024 * 1024)
        archive_reserve = business / next(iter(reserved))
        if hasattr(archive_reserve.stat(), 'st_blocks'):
            self.assertGreaterEqual(archive_reserve.stat().st_blocks * 512, 16 * 1024 * 1024)
        self.assertTrue(reserved <= {path.name for path in business.iterdir()},
            'returning an external intent cannot settle its original reservation')
        effect = result['next_action']['result']['effects'][0]
        self.invoke('control', self.archive_receipt(effect, 'issued'))
        self.invoke('control', self.archive_receipt(effect, 'unknown'))
        self.assertTrue(reserved <= {path.name for path in business.iterdir()})
        if os.environ.get('T01_ARCHIVE_RESERVATION_EVIDENCE_PATH'):
            current = progress.state_store.StateStore(self.checkpoint).open_current()
            Path(os.environ['T01_ARCHIVE_RESERVATION_EVIDENCE_PATH']).write_text(json.dumps({
                'before_dispatch_reserves': snapshots[0],
                'original_archive_reserve': {'path_digest': archive_reserve.name,
                    'usable_bytes': archive_reserve.stat().st_size,
                    'allocated_bytes': archive_reserve.stat().st_blocks * 512 if hasattr(archive_reserve.stat(), 'st_blocks') else None},
                'operation': effect, 'pending_transactions': self.state()['control_transactions'],
                'unknown_root_reference': current.reference}, indent=2) + '\n')
        replay = self.invoke('control', request)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(replay['next_action']['result']['effects'], [])
        self.assertTrue(reserved <= {path.name for path in business.iterdir()})

    def test_archive_reservation_rejects_full_business_window_before_dispatch(self):
        handoff_id = self.retired_fixture()
        self.begin()
        store = progress.state_store.StateStore(self.checkpoint)
        business = store.directory / 'reserves' / 'business'
        self.assertEqual(len(list(business.iterdir())), 1)
        for number in range(3):
            store.reserve('other-original-business-' + str(number))
        before = self.checkpoint.read_bytes()
        original = progress.dispatch.checkpoint
        calls = []
        def observed(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)
        with patch.object(progress.dispatch, 'checkpoint', side_effect=observed):
            result = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                'receipt': {'controller_ref': 'task', 'reference': 'full-window'}})
        self.assertEqual(calls, [], 'a fifth business intent must never reach archive dispatch')
        self.assertEqual(result['next_action']['result']['error']['code'], 'state_capacity', result)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['operations'], [])
        self.assertEqual(len(list(business.iterdir())), 4)
        if all(hasattr(path.stat(), 'st_blocks') for path in business.iterdir()):
            self.assertGreaterEqual(sum(path.stat().st_blocks * 512 for path in business.iterdir()), 64 * 1024 * 1024)

    def test_archive_reservation_query_and_stop_use_recovery_until_exact_completion(self):
        handoff_id = self.retired_fixture()
        self.begin()
        self.invoke('observe', self.observation())
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'original-business-archive'}}
        effect = self.invoke('control', request)['next_action']['result']['effects'][0]
        self.invoke('control', self.archive_receipt(effect, 'issued'))
        self.invoke('control', self.archive_receipt(effect, 'unknown'))
        store = progress.state_store.StateStore(self.checkpoint)
        business = store.directory / 'reserves' / 'business'
        recovery = store.directory / 'reserves' / 'recovery'
        business_before = {path.name: path.stat().st_size for path in business.iterdir()}
        self.assertEqual(len(business_before), 1, 'unknown archive retains its business slot')
        ordinary_limit = progress.state_store.RUN_LIMIT - progress.state_store.BUSINESS_RESERVE - progress.state_store.RECOVERY_RESERVE
        progress.state_store.atomic_write(store.directory / 'usage.json', progress.state_store.encode({'allocated': ordinary_limit}))
        query = self.invoke('control', {**request, 'receipt': {
            'controller_ref': 'task', 'reference': 'exact-original-query'}})['next_action']['result']['effects'][0]
        self.assertEqual(query['operation'], 'read-archive-state')
        self.assertEqual({path.name: path.stat().st_size for path in business.iterdir()}, business_before)
        self.assertEqual(len(list(recovery.iterdir())), 1)
        query_reserve = next(recovery.iterdir())
        self.assertGreater(query_reserve.stat().st_size, 0)
        stopped = self.invoke('pause')
        self.assertEqual(stopped['next_action']['operation'], 'stop-host', stopped)
        self.assertEqual({path.name: path.stat().st_size for path in business.iterdir()}, business_before)
        self.assertEqual(len(list(recovery.iterdir())), 2)
        self.invoke('control', self.archive_receipt(query, 'issued'))
        self.assertTrue(query_reserve.exists())
        completed = self.archive_receipt(query, 'archived')
        result = self.invoke('control', completed)
        self.assertTrue(result['next_action']['result']['ok'], result)
        self.assertFalse(query_reserve.exists())
        self.assertEqual(len(list(business.iterdir())), 0, 'exact archive readback releases the original archive slot')
        before = self.checkpoint.read_bytes()
        usage = (store.directory / 'usage.json').read_bytes()
        replay = self.invoke('control', completed)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(replay['next_action']['result']['effects'], [])
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual((store.directory / 'usage.json').read_bytes(), usage)
        changed = copy.deepcopy(completed)
        changed['evidence']['status'] = 'unknown'
        conflict = self.invoke('control', changed)
        self.assertEqual(conflict['next_action']['result']['error']['code'], 'receipt_conflict', conflict)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual((store.directory / 'usage.json').read_bytes(), usage)

    def test_archive_reservation_capacity_failure_cannot_dispatch_new_business(self):
        handoff_id = self.retired_fixture()
        self.begin()
        store = progress.state_store.StateStore(self.checkpoint)
        ordinary_limit = progress.state_store.RUN_LIMIT - progress.state_store.BUSINESS_RESERVE - progress.state_store.RECOVERY_RESERVE
        progress.state_store.atomic_write(store.directory / 'usage.json', progress.state_store.encode({'allocated': ordinary_limit}))
        before = self.checkpoint.read_bytes()
        usage_before = (store.directory / 'usage.json').read_bytes()
        reserves_before = {str(path.relative_to(store.directory)): path.stat().st_size
            for path in (store.directory / 'reserves').rglob('*') if path.is_file()}
        original = progress.dispatch.checkpoint
        calls = []
        def observed(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)
        with patch.object(progress.dispatch, 'checkpoint', side_effect=observed):
            result = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                'receipt': {'controller_ref': 'task', 'reference': 'ordinary-capacity-full'}})
        self.assertEqual(calls, [])
        self.assertEqual(result['next_action']['result']['error']['code'], 'state_capacity', result)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['operations'], [])
        self.assertEqual((store.directory / 'usage.json').read_bytes(), usage_before)
        self.assertEqual({str(path.relative_to(store.directory)): path.stat().st_size
            for path in (store.directory / 'reserves').rglob('*') if path.is_file()}, reserves_before)

    def test_archive_reservation_terminal_receipt_faults_release_only_after_commit_proof(self):
        checkpoint = self.checkpoint
        for after_root in (False, True):
            with self.subTest(after_root=after_root):
                self.checkpoint = checkpoint.with_name('reserved-terminal-' + str(after_root) + '.json')
                handoff_id = self.retired_fixture()
                self.begin()
                request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                    'receipt': {'controller_ref': 'task', 'reference': 'terminal-archive'}}
                effect = self.invoke('control', request)['next_action']['result']['effects'][0]
                self.invoke('control', self.archive_receipt(effect, 'issued'))
                store = progress.state_store.StateStore(self.checkpoint)
                before_reserves = {path.name for path in (store.directory / 'reserves' / 'business').iterdir()}
                self.assertEqual(len(before_reserves), 2)
                completed = self.archive_receipt(effect, 'archived')
                replace = os.replace
                interrupted_once = False
                def interrupted(source, target):
                    nonlocal interrupted_once
                    target = Path(target)
                    if interrupted_once:
                        return replace(source, target)
                    if target == self.checkpoint and not after_root:
                        envelope = json.loads(Path(source).read_bytes())
                        digest = envelope['current']['sha256']
                        root = json.loads((store.directory / 'objects' / digest[:2] / digest[2:4] / digest).read_bytes())['value']
                        if root.get('receipts'):
                            interrupted_once = True
                            raise KeyboardInterrupt('terminal receipt before archive root')
                    elif target.parent.name == 'commits' and after_root:
                        if json.loads(Path(source).read_bytes()).get('receipts'):
                            interrupted_once = True
                            raise KeyboardInterrupt('terminal root before archive proof')
                    return replace(source, target)
                with patch.object(os, 'replace', interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        self.invoke('control', completed)
                self.assertEqual({path.name for path in (store.directory / 'reserves' / 'business').iterdir()}, before_reserves,
                    'a fault must never release before the committed receipt membership is proven')
                before = self.checkpoint.read_bytes()
                recovered = self.invoke('control', completed)
                self.assertTrue(recovered['next_action']['result']['ok'], recovered)
                self.assertEqual(recovered['acknowledged'], after_root)
                self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['archive_status'], 'archived')
                self.assertEqual(len(list((store.directory / 'reserves' / 'business').iterdir())), 1)
                if after_root:
                    self.assertEqual(self.checkpoint.read_bytes(), before)
                saved = self.checkpoint.read_bytes()
                replay = self.invoke('control', completed)
                self.assertTrue(replay['acknowledged'], replay)
                self.assertEqual(replay['next_action']['result']['effects'], [])
                self.assertEqual(self.checkpoint.read_bytes(), saved)
                self.assertEqual(len(self.state()['control']['context']['retired_handoffs'][handoff_id]['operations']), 1)

    def test_archive_reservation_inline_recovery_keeps_original_unknown_operation(self):
        handoff_id = self.retired_fixture()
        self.accepted_implementation_for_closure()
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'archive-before-inline-recovery'}}
        effect = self.invoke('control', request)['next_action']['result']['effects'][0]
        self.invoke('control', self.archive_receipt(effect, 'issued'))
        self.invoke('control', self.archive_receipt(effect, 'unknown'))
        original_pin = next(pin for pin in self.state()['control_transactions'].values() if pin.get('reserve_owner'))
        store = progress.state_store.StateStore(self.checkpoint)
        reserve = store.directory / 'reserves' / 'business' / hashlib.sha256(original_pin['reserve_action'].encode()).hexdigest()
        original = copy.deepcopy(self.state())
        recovery_input, failed_publication = self.prepare_final_recovery(original)
        self.assertTrue(reserve.exists())
        self.recover_final_integration(original, recovery_input, failed_publication)
        self.assertTrue(reserve.exists(), 'inline recovery retains the unknown original external operation')
        retained = next(pin for pin in self.state()['control_transactions'].values() if pin.get('reserve_owner'))
        self.assertEqual(retained, original_pin)
        self.assertEqual(retained['operation_id'], effect['operation_id'])
        dispatcher = copy.deepcopy(self.state()['dispatch'])
        replay = self.invoke('control', request)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(replay['next_action']['result']['effects'], [])
        query = self.invoke('control', {**request, 'receipt': {
            'controller_ref': 'task', 'reference': 'inline-recovery-exact-query'}})['next_action']['result']['effects'][0]
        self.assertEqual(query['operation'], 'read-archive-state')
        self.assertTrue(reserve.exists())
        self.invoke('control', self.archive_receipt(query, 'issued'))
        self.invoke('control', self.archive_receipt(query, 'archived'))
        self.assertFalse(reserve.exists())
        self.assertEqual(self.state()['dispatch'], dispatcher)
        self.assertEqual([operation['operation_id'] for operation in self.state()['control']['context']['retired_handoffs'][handoff_id]['operations']],
            [effect['operation_id'], query['operation_id']])

    def test_archive_reservation_enospc_and_caught_exception_preserve_original_identity(self):
        handoff_id = self.retired_fixture()
        self.begin()
        store = progress.state_store.StateStore(self.checkpoint)
        business = store.directory / 'reserves' / 'business'
        before = self.checkpoint.read_bytes()
        reserves_before = {path.name for path in business.iterdir()}
        original_dispatch = progress.dispatch.checkpoint
        calls, effects = [], []
        def observed(*args, **kwargs):
            calls.append(args)
            result = original_dispatch(*args, **kwargs)
            effects.extend(result[0]['effects'])
            return result
        replace = os.replace
        def no_space(source, target):
            if Path(target).parent == business:
                raise OSError(errno.ENOSPC, 'controlled archive reservation allocation failure')
            return replace(source, target)
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'original-error-archive'}}
        with patch.object(os, 'replace', no_space), patch.object(progress.dispatch, 'checkpoint', side_effect=observed):
            rejected = self.invoke('control', request)
        self.assertFalse(rejected['next_action']['result']['ok'], rejected)
        self.assertEqual(calls, [])
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual({path.name for path in business.iterdir()}, reserves_before)
        def lost_response(*args, **kwargs):
            observed(*args, **kwargs)
            raise transfer.entry.PreparationError('archive_response_lost', 'controlled original response loss')
        with patch.object(progress.dispatch, 'checkpoint', side_effect=lost_response):
            failed = self.invoke('control', request)
        self.assertEqual(failed['next_action']['result']['error']['code'], 'archive_response_lost', failed)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(effects), 1)
        pending = self.state()['control_transactions'][transfer.entry.digest(request)]
        self.assertEqual(pending['request'], request)
        reserve = business / hashlib.sha256(pending['reserve_action'].encode()).hexdigest()
        self.assertEqual(reserve.stat().st_size, 16 * 1024 * 1024,
            'normal exception exit cannot collect an unknown original invocation reserve')
        recovered = self.invoke('control', request)
        self.assertEqual(recovered['next_action']['result']['effects'], effects)
        self.assertTrue(reserve.exists())
        self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['operations'][0]['operation_id'], effects[0]['operation_id'])
        replay = self.invoke('control', request)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(replay['next_action']['result']['effects'], [])
        self.assertEqual(len(self.state()['control']['context']['retired_handoffs'][handoff_id]['operations']), 1)

    def test_archive_reservation_does_not_block_unrelated_native_finalization(self):
        from test_collaboration_lifecycle import CollaborationLifecycleTests
        handoff_id = self.retired_fixture()
        helper = CollaborationLifecycleTests(methodName='runTest')
        helper.f = self
        self.input['authorization']['flow_mode'] = 'continuous'
        helper.test_real_tool_shape_allows_planning_publication_without_rpc_pages()
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'unrelated-old-task-archive'}}
        effect = self.invoke('control', request)['next_action']['result']['effects'][0]
        self.invoke('control', self.archive_receipt(effect, 'issued'))
        self.invoke('control', self.archive_receipt(effect, 'unknown'))
        original = next(pin for pin in self.state()['control_transactions'].values() if pin.get('reserve_owner'))
        store = progress.state_store.StateStore(self.checkpoint)
        reserve = store.directory / 'reserves' / 'business' / hashlib.sha256(original['reserve_action'].encode()).hexdigest()
        self.invoke('lifecycle-state', helper.snapshot())
        prepared = self.invoke('native-finalize', helper.finalization())
        self.assertEqual(prepared['next_action']['operation'], 'finish-native', prepared)
        self.assertTrue(reserve.exists(), 'unrelated native closure cannot settle the original archive')
        self.assertEqual(next(pin for pin in self.state()['control_transactions'].values()
            if pin.get('reserve_owner')), original)
        self.invoke('native-finalize', helper.finalize_observation(prepared['next_action']))
        self.assertTrue(reserve.exists())
        self.assertEqual(self.state()['control']['context']['retired_handoffs'][handoff_id]['archive_status'], 'unknown')

    def test_archive_reservation_blocks_finalization_of_its_original_target(self):
        from test_collaboration_lifecycle import CollaborationLifecycleTests
        handoff_id = self.retired_fixture('/root/designer')
        helper = CollaborationLifecycleTests(methodName='runTest')
        helper.f = self
        self.input['authorization']['flow_mode'] = 'continuous'
        helper.test_real_tool_shape_allows_planning_publication_without_rpc_pages()
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'original-native-target-archive'}}
        effect = self.invoke('control', request)['next_action']['result']['effects'][0]
        self.assertEqual(effect['ref'], '/root/designer')
        self.invoke('control', self.archive_receipt(effect, 'issued'))
        self.invoke('control', self.archive_receipt(effect, 'unknown'))
        original = next(pin for pin in self.state()['control_transactions'].values() if pin.get('reserve_owner'))
        self.assertEqual(original['archive_ref'], '/root/designer')
        store = progress.state_store.StateStore(self.checkpoint)
        reserve = store.directory / 'reserves' / 'business' / hashlib.sha256(original['reserve_action'].encode()).hexdigest()
        self.invoke('lifecycle-state', helper.snapshot())
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            self.invoke('native-finalize', helper.finalization())
        self.assertEqual(rejected.exception.code, 'host_action_pending')
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(self.state().get('native_finalizations', []), [])
        self.assertEqual(reserve.stat().st_size, 16 * 1024 * 1024)
        self.assertEqual(next(pin for pin in self.state()['control_transactions'].values()
            if pin.get('reserve_owner')), original)

    def test_archive_intent_crash_replays_without_duplicate_operation(self):
        handoff_id = self.retired_fixture()
        self.begin()
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                   'receipt': {'controller_ref': 'task', 'reference': 'automatic'}}
        with patch.object(progress.dispatch, 'checkpoint', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('control', request)
        pending = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint)).unresolved_control_transactions()
        self.assertTrue(pending)
        transaction = pending[transfer.entry.digest(request)]
        self.assertEqual(transaction['request'], request)
        self.assertEqual(transaction['port'], {'discussion': None})
        self.assertEqual(transaction['handoff'], {'controller_ref': 'task'})
        self.assertEqual(transaction['base_control_digest'], transfer.entry.digest(self.state()['control']))
        self.assertLess(transaction['base_revision'], self.state()['revision'])
        reserve = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'reserves' / 'business' / hashlib.sha256(transaction['reserve_action'].encode()).hexdigest()
        self.assertEqual(reserve.stat().st_size, 16 * 1024 * 1024)
        first = self.invoke('control', request)['next_action']['result']['effects'][0]
        self.assertTrue(reserve.exists())
        self.assertEqual(next(pin for pin in self.state()['control_transactions'].values()
            if pin.get('reserve_owner'))['reserve_action'], transaction['reserve_action'])
        replay = self.invoke('control', request)
        self.assertEqual(replay['next_action']['result']['effects'], [])
        self.assertEqual(len(self.state()['control']['context']['retired_handoffs'][handoff_id]['operations']), 1)
        self.assertEqual(first['ref'], 'old-task')
        lookup = {'operation_id': first['operation_id'], 'ref': 'old-task', 'adapter': 'fixture',
            'invocation_id': 'lookup-call', 'response_id': 'lookup-response', 'status': 'not-issued',
            'raw': {'calls': []}}
        recovered = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id,
            'call_lookup': lookup}, 'receipt': lookup})
        self.assertEqual(recovered['next_action']['result']['effects'], [first])
        self.invoke('control', self.archive_receipt(first, 'issued'))
        query = self.invoke('control', {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'lost-response'}})
        self.assertEqual(query['next_action']['result']['effects'][0]['operation'], 'read-archive-state')

    def test_archive_receipt_commit_crashes_preserve_exact_replay(self):
        for after_root in (False, True):
            with self.subTest(after_root=after_root):
                self.checkpoint = self.checkpoint.with_name('archive-' + str(after_root) + '.json')
                handoff_id = self.retired_fixture()
                self.begin()
                request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                    'receipt': {'controller_ref': 'task', 'reference': 'original-archive'}}
                directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
                original_replace = os.replace
                def interrupted(source, target):
                    target = Path(target)
                    if target == self.checkpoint and not after_root:
                        envelope = json.loads(Path(source).read_bytes())
                        digest = envelope['current']['sha256']
                        root = json.loads((directory / 'objects' / digest[:2] / digest[2:4] / digest).read_bytes())['value']
                        if root.get('receipts'):
                            raise KeyboardInterrupt('archive receipt before root commit')
                    elif target.parent.name == 'commits' and after_root:
                        if json.loads(Path(source).read_bytes()).get('receipts'):
                            raise KeyboardInterrupt('archive root before commit marker')
                    return original_replace(source, target)
                with patch.object(os, 'replace', interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        self.invoke('control', request)
                before = self.invoke('inspect')['revision']
                recovered = self.invoke('control', request)
                self.assertTrue(recovered['next_action']['result']['ok'], recovered)
                self.assertEqual(recovered['acknowledged'], after_root)
                self.assertEqual(len(recovered['next_action']['result']['effects']), 0 if after_root else 1)
                if after_root:
                    self.assertEqual(recovered['revision'], before)
                operations = self.state()['control']['context']['retired_handoffs'][handoff_id]['operations']
                self.assertEqual(len(operations), 1)
                saved_bytes = self.checkpoint.read_bytes()
                replay = self.invoke('control', request)
                self.assertTrue(replay['acknowledged'])
                self.assertEqual(replay['next_action']['result']['effects'], [])
                self.assertEqual(self.checkpoint.read_bytes(), saved_bytes)
                changed = copy.deepcopy(request)
                changed['evidence']['call_lookup'] = {}
                conflict = self.invoke('control', changed)
                self.assertEqual(conflict['next_action']['result']['error']['code'], 'receipt_conflict')
                self.assertEqual(self.checkpoint.read_bytes(), saved_bytes)

    def test_new_runtime_rejects_old_control_and_c_without_writes(self):
        self.begin()
        original_path = self.checkpoint
        original_bytes = original_path.read_bytes()
        saved = progress.read_active_owner(original_path)
        for version in ('control', 'workflow-progress-v9', 'workflow-progress-v11',
                        'workflow-progress-v17', 'runner-9'):
            with self.subTest(version=version):
                old = copy.deepcopy(dict(saved))
                if version == 'control':
                    old[progress.KEY]['control']['context']['schema_version'] = 5
                elif version == 'runner-9':
                    old['version'] = 9
                else:
                    old[progress.KEY]['protocol'] = version
                self.checkpoint = original_path.with_name('legacy-' + version + '.json')
                progress.atomic_save(self.checkpoint, old)
                before = self.checkpoint.read_bytes()
                with patch.object(progress.dispatch, 'checkpoint', side_effect=AssertionError('legacy-control-effect')) as dispatched:
                    with self.assertRaises(transfer.entry.PreparationError) as error:
                        self.invoke('inspect')
                self.assertEqual(error.exception.code, 'legacy_run_requires_original_runtime')
                self.assertEqual(dispatched.call_count, 0)
                self.assertEqual(self.checkpoint.read_bytes(), before)
                self.assertEqual(original_path.read_bytes(), original_bytes)
        self.checkpoint = original_path

    def test_scope_readiness_requires_original_runtime_for_previous_progress(self):
        self.begin()
        old = progress.read_projection(self.checkpoint, projection='scope').record
        old[progress.KEY]['protocol'] = 'workflow-progress-v17'
        self.checkpoint = self.checkpoint.with_name('legacy.json')
        progress.atomic_save(self.checkpoint, old)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError) as error:
            progress.read_scope_readiness({'checkpoint_path': str(self.checkpoint)})
        self.assertEqual(error.exception.code, 'legacy_run_requires_original_runtime')
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_unknown_business_control_blocks_start_and_supplied_stop_is_rejected(self):
        self.begin()
        self.invoke('observe', self.observation())
        self.invoke('pause')
        self.invoke('observe', self.observation('stopped', 'result'))
        rejected = self.invoke('control', {'action': 'successor-ready', 'evidence': {'takeover_proof': {'stopped': True}},
            'receipt': {'controller_ref': 'task'}})
        self.assertEqual(rejected['error']['code'], 'invalid_evidence')
        original_owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        self.assertTrue(original_owner.host_stopped())
        self.assertFalse(original_owner.unresolved_host_actions())
        self.assertFalse(original_owner.unresolved_control_transactions())
        self.checkpoint = self.checkpoint.with_name('unknown-business.json')
        self.begin()
        self.invoke('observe', self.observation())
        paused = self.invoke('pause')
        stop_action = copy.deepcopy(self.state()['host_action'])
        self.assertEqual(paused['next_action']['action_id'], stop_action['action_id'])
        self.assertEqual(stop_action['operation'], 'stop-host')
        stopped = self.observation('stopped', 'result')
        self.assertEqual(self.invoke('observe', stopped)['status'], 'paused')
        original = copy.deepcopy(self.state())
        self.assertEqual(original['host']['last_stop'], original['host']['observation'])
        self.assertEqual(original['host']['last_stop']['generation'], original['host']['generation'])
        self.assertEqual(original['host']['last_stop']['action_id'], stop_action['action_id'])
        self.assertEqual(original['host']['last_stop']['provenance'], stopped['provenance'])
        self.assertEqual(original['lifecycle_snapshot']['instance'], stopped['lifecycle']['instance'])
        self.assertEqual(original['lifecycle_snapshot']['sequence'], stopped['lifecycle']['sequence'])
        # An exact response replay ACKs without replacing current stop authority.
        replay = self.invoke('observe', stopped)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(self.state()['host'], original['host'])
        request = {'action': 'successor-ready', 'evidence': {},
                   'receipt': {'controller_ref': 'task'}}
        with patch.object(progress.dispatch, 'checkpoint', side_effect=KeyboardInterrupt) as dispatched:
            try:
                response = self.invoke('control', request)
            except KeyboardInterrupt:
                pass
            else:
                self.fail('control intent did not reach its authority: ' + str(response))
        self.assertEqual(dispatched.call_count, 1)
        port, handoff, action, evidence = dispatched.call_args.args
        self.assertEqual(port['context']['controller_ref'], 'task')
        self.assertEqual(handoff['digest'], original['handoff']['digest'])
        self.assertEqual(action, 'successor-ready')
        proof = evidence['takeover_proof']
        self.assertEqual(proof['stop_receipt'], {key: original['host']['last_stop'][key] for key in
            ('event_id', 'receipt', 'action_id', 'provenance', 'action_resolution')})
        self.assertEqual(proof['ref'], stopped['receipt']['ref'])
        self.assertEqual(proof['attempt'], original['control']['context']['carrier']['attempt'])
        self.assertEqual(proof['host_ref'], original['handoff']['expected_entry']['actor']['thread_id'])
        self.assertEqual(proof['invocation_id'], stopped['provenance']['call_ref'])
        self.assertEqual(proof['response_id'], stopped['provenance']['response_ref'])
        self.assertEqual(proof['business_calls']['unresolved'], [])
        pending = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint)).unresolved_control_transactions()
        self.assertEqual(len(pending), 1)
        transaction = next(iter(pending.values()))
        self.assertEqual(transaction['takeover_input'], request)
        self.assertIsNone(transaction['result'])
        with patch.object(progress.dispatch, 'checkpoint', side_effect=AssertionError('new control call')) as dispatched:
            with self.assertRaises(transfer.entry.PreparationError) as error:
                self.invoke('start', {'handoff': self.input})
        self.assertEqual(error.exception.code, 'control_outcome_unknown')
        self.assertEqual(dispatched.call_count, 0)
        self.assertEqual(self.state()['host'], original['host'])
        self.assertEqual(self.state()['lifecycle_snapshot'], original['lifecycle_snapshot'])
        self.assertEqual(progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint)).unresolved_control_transactions(), pending)

    def test_dedicated_successor_ready_consumes_causal_stop_through_real_control(self):
        self.context = self.context_for(0)
        for action, evidence in (
            ('prepare', {'target': 'local', 'project': 'project', 'title': 'Discuss',
                'missing_context': [], 'configuration': transfer.configuration('dedicated-discussion'),
                'next_step': 'stage1', 'archive_ref': None, 'gate_open': True}),
            ('decide', None),
        ):
            if evidence is None:
                evidence = {'plan_id': self.context['handoff_progress']['plan']['plan_id'], 'intent': 'confirm'}
            self.context = progress.control.transition({'schema_version': 7, 'actor_ref': 'task',
                'context': self.context, 'action': action, 'evidence': evidence})['context']
        value = self.input_for(0)
        value['binding'] = None
        value['scope'].pop('downstream')
        value['scope'].update(owned_paths=[self.requirement_path], protected_paths=[],
            implementation_paths=[], closure_paths=[])
        value['authorization']['scope_digest'] = transfer.entry.digest(value['scope'])
        self.begin(input_data=value)
        self.invoke('observe', self.observation(ref='task:dedicated'))
        self.assertEqual(self.invoke('pause')['next_action']['operation'], 'stop-host')
        stopped = self.observation('stopped', 'result', {
            'delivery_id': 'dedicated-result', 'status': 'completed',
            'payload': {'artifacts': [self.requirement_path], 'checks': ['confirmed requirement'],
                'requirement': self.frozen, 'delivery': None}}, ref='task:dedicated')
        self.assertEqual(self.invoke('observe', stopped)['status'], 'paused')
        resumed = self.invoke('resume')
        self.assertEqual(resumed['status'], 'needs_input', resumed)
        pending = resumed['pending']
        accepted = self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:dedicated-accept'})
        self.assertEqual(accepted['status'], 'accepted', accepted)
        original = copy.deepcopy(self.state())
        self.assertEqual(original['control']['context']['handoff_progress']['state'], 'result-accepted')
        self.assertEqual(original['host']['last_stop'], original['host']['observation'])
        request = {'action': 'successor-ready', 'evidence': {
            'ref': 'task:successor', 'stage': 1, 'role': 'dedicated-problem-framing',
            'input_digest': original['control']['context']['handoff_progress']['delivery_digest'],
            'binding_verified': True, 'activated': False, 'confirmed': True, 'archive_ref': 'task:dedicated'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:dedicated-takeover'}}
        result = self.invoke('control', request)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['next_action']['result']['ok'], result)
        saved = self.state()
        retired = next(iter(saved['control']['context']['retired_handoffs'].values()))
        self.assertTrue(retired['handoff_completed'])
        self.assertEqual(retired['carrier']['ref'], 'task:dedicated')
        self.assertEqual(retired['successor']['ref'], 'task:successor')
        self.assertEqual(retired['successor']['takeover_proof']['stop_receipt'], {
            key: original['host']['last_stop'][key] for key in
            ('event_id', 'receipt', 'action_id', 'provenance', 'action_resolution')})
        self.assertEqual(saved['host'], original['host'])
        self.assertFalse(progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint)).unresolved_control_transactions())
        if os.environ.get('T01_CAUSAL_STOP_P2_EVIDENCE_PATH'):
            store = progress.state_store.StateStore(self.checkpoint)
            current = store.open_current()
            subject = store.read_subject(current, current.root['metadata']['stage_subject'])
            Path(os.environ['T01_CAUSAL_STOP_P2_EVIDENCE_PATH']).write_text(json.dumps({
                'actual_result': result, 'committed_root': current.root, 'root_ref': current.reference,
                'stage_subject_ref': subject.reference, 'retired_handoff': retired,
                'saved_stop': saved['host']['last_stop'], 'current_observation': saved['host']['observation'],
                'current_generation': saved['host']['generation']}, indent=2) + '\n')
        before = self.checkpoint.read_bytes()
        replay = self.invoke('control', request)
        self.assertTrue(replay['acknowledged'], replay)
        self.assertEqual(replay['next_action']['result']['effects'], [])
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_successor_ready_rejects_noncausal_and_revoked_stop_observations(self):
        checkpoint = self.checkpoint
        for later in ('noncausal-stopped', 'running', 'unknown', 'old-stopped'):
            with self.subTest(later=later):
                self.checkpoint = checkpoint.with_name(later + '.json')
                self.begin()
                self.invoke('observe', self.observation())
                self.invoke('pause')
                stopped = self.observation('stopped', 'result')
                self.assertEqual(self.invoke('observe', stopped)['status'], 'paused')
                original = copy.deepcopy(self.state())
                if later == 'old-stopped':
                    resumed = self.invoke('resume')
                    self.assertEqual(resumed['next_action']['operation'], 'inspect-host-state')
                changed = self.observation('stopped' if later.endswith('stopped') else later, 'result')
                if later.endswith('stopped'):
                    # A fresh response attributed to an ended/old action is
                    # valid host evidence, but cannot become current causal proof.
                    changed['action_id'] = stopped['action_id']
                    changed['provenance']['action_id'] = stopped['action_id']
                self.invoke('observe', changed)
                after = copy.deepcopy(self.state())
                self.assertGreater(after['host']['generation'], original['host']['generation'])
                self.assertIsNone(after['host']['proof'])
                if later.endswith('stopped'):
                    self.assertEqual(after['host']['status'], 'stopped')
                    self.assertEqual(after['host']['observation'], original['host']['observation'])
                    self.assertNotEqual(after['host']['last_stop'], after['host']['observation'])
                else:
                    self.assertNotEqual(after['host']['status'], 'stopped')
                # Neither replay shape can refresh the old causal observation.
                self.assertTrue(self.invoke('observe', stopped)['acknowledged'])
                duplicate = copy.deepcopy(stopped)
                duplicate['event_id'] = 'old-stop-new-event'
                replayed = self.invoke('observe', duplicate)
                for field in ('status', 'generation', 'proof', 'observation', 'last_stop', 'calls', 'seen'):
                    self.assertEqual(self.state()['host'][field], after['host'][field], field)
                self.assertNotEqual((replayed.get('next_action') or {}).get('operation'), 'continue-host', replayed)
                request = {'action': 'successor-ready', 'evidence': {},
                           'receipt': {'controller_ref': 'task'}}
                with patch.object(progress.dispatch, 'checkpoint', side_effect=AssertionError('stale stop dispatched')) as dispatched:
                    rejected = self.invoke('control', request)
                self.assertEqual(rejected['error']['code'], 'host_evidence_missing', rejected)
                self.assertEqual(dispatched.call_count, 0)
                recovered = self.invoke('resume')
                self.assertNotEqual((recovered.get('next_action') or {}).get('operation'), 'continue-host', recovered)

    def invoke(self, operation, data=None, revision=None):
        if operation == 'start' and data['handoff'].get('stage', 0) >= 2 and 'native_host' not in data:
            carrier = data['handoff']['entry']['host']['thread_id']
            data = {**data, 'native_host': {'adapter': 'app-server', 'snapshot': {
                'instance': (progress.read_record(self.checkpoint).get('transport') or {}).get('instance', 'fixture-host'),
                'carrier_thread': carrier, 'sequence': 0, 'events': [],
                'pages': [{'request': {'method': 'thread/list', 'params': {
                    'ancestorThreadId': carrier, 'archived': archived,
                    'sourceKinds': list(progress.NATIVE_SOURCE_KINDS), 'modelProviders': []}},
                    'response': {'data': [], 'nextCursor': None}} for archived in (False, True)]}}}
        current = progress.read_record(self.checkpoint).get(progress.KEY, {}).get("revision", 0)
        return progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL, "operation": operation,
            "expected_revision": current if revision is None else revision, "data": data or {}})

    def begin(self, mode="stepwise", input_data=None):
        value = copy.deepcopy(input_data or self.input)
        if value["authorization"]["flow_mode"] != mode:
            value.pop("inline_authority", None)
        value["authorization"]["flow_mode"] = mode
        result = self.invoke("start", {"handoff": value, "control": {"context": self.context, "discussion": None}})
        self.assertEqual(result["next_action"]["operation"], "invoke-host")
        return result

    def observation(self, status="ready", event="create", result=None, ref="native:designer"):
        saved = self.state()
        value = {"event_id": "event-" + str(len(saved["events"])),
                 "receipt": self.receipt(saved["dispatch"], status, event, ref=ref)}
        # Fixture adapter authenticates each real tool response and its cause.
        action = saved.get("host", {}).get("query") or saved.get("host_action") or saved.get("action")
        if action is not None:
            value["action_id"] = action["action_id"]
        value["receipt"]["receipt_ref"] += ":" + value["event_id"]
        self.host_response_sequence += 1
        if action is not None:
            value["provenance"] = {"call_ref": "fixture-call:" + str(self.host_response_sequence),
                "response_ref": "fixture-response:" + str(self.host_response_sequence), "action_id": action["action_id"]}
            if action["operation"] == "inspect-host-state" and action["payload"].get("unresolved_action"):
                # Fixture host resolves the named invocation in its call history;
                # tests of unavailable/uncertain lookup remove this evidence.
                value["action_resolution"] = {"action_id": action["payload"]["unresolved_action"]["action_id"],
                                              "outcome": "completed"}
        if result is not None:
            value["result"] = result
        if status == 'stopped':
            context = saved['control']['context']
            threads = [{'id':ref, 'status':{'type':'idle'}}]
            for execution in (context.get('handoff_progress') or {}).get('executions', []):
                if execution.get('agent_ref'):
                    threads.append({'id':execution['agent_ref'], 'status':{'type':'idle' if execution['stopped'] else 'active'}})
            for slot in saved.get('review_activity', {}).values():
                if slot and slot.get('ref'):
                    threads.append({'id':slot['ref'], 'status':{'type':'idle' if slot['stopped'] else 'active'}})
            # A Ticket reviewer may legitimately appear in several stopped slice slots.
            unique_threads = {}
            for thread in threads:
                previous = unique_threads.get(thread['id'])
                if previous is None or thread['status']['type'] == 'active':
                    unique_threads[thread['id']] = thread
            threads = list(unique_threads.values())
            # The external host still lists stopped writers from the previous delivery.
            listed = {thread['id'] for thread in threads}
            threads.extend({'id': prior, 'status': {'type': 'idle'}}
                           for prior in saved.get('recovered_native_refs', []) if prior not in listed)
            # Closed roles remain in the real host census after their current
            # slots leave the window. Only verified original stop facts may
            # supply the fixture's idle rows; unknown roles must stay missing.
            owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
            checkpoint = owner.state['control']['context'].get('handoff_progress') or {}
            executions = progress.control.all_execution_results(checkpoint) + checkpoint.get('carried_results', [])
            for source in checkpoint.get('plan_revision_sources', []):
                executions.extend(source['executions'] + source['carried_results'])
            stopped = {item['agent_ref'] for item in executions if item.get('stopped') is True}
            if checkpoint.get('review_claims'):
                stopped.update(claim['reviewer_ref'] for claim in owner.read_review_claims().values())
            missing = owner.native_refs() - {thread['id'] for thread in threads}
            self.assertLessEqual(missing, stopped, 'fixture requires original stop proof for archived roles')
            threads.extend({'id': identity, 'status': {'type': 'idle'}} for identity in sorted(missing))
            carrier = progress.read_projection(self.checkpoint, projection='identity').record.get('sessions', {}).get('stage' + str(saved['stage'])) or saved['handoff']['expected_entry']['actor']['thread_id']
            transport = progress.read_projection(self.checkpoint, projection='identity').record.get('transport') or {}
            value['lifecycle'] = {'instance':transport.get('instance','fixture-host'),'carrier_thread':carrier,
                'sequence':transport.get('native_event_sequence',0),'events':[],
                'pages':[{'request':{'method':'thread/list','params':{'ancestorThreadId':carrier,'archived':archived,
                          'sourceKinds':list(progress.NATIVE_SOURCE_KINDS),'modelProviders':[]}},
                          'response':{'data':[] if archived else threads,'nextCursor':None}} for archived in (False,True)]}
        return value

    def test_launch_is_saved_before_host_and_never_reissued(self):
        self.begin()
        saved = self.state()
        self.assertEqual(saved["step"], "host-response")
        again = self.invoke("advance")
        self.assertEqual(again["next_action"]["operation"], "lookup-exact-action")
        self.assertEqual(self.state()["dispatch"], saved["dispatch"])
        observed = self.observation()
        bound = self.invoke("observe", observed)
        self.assertEqual(bound["next_action"]["operation"], "wait-host")
        self.assertTrue(self.invoke("observe", observed, revision=0)["acknowledged"])

    def test_unknown_preserves_attempt_and_wrong_receipt(self):
        self.begin()
        unknown = self.observation("unknown", ref=None)
        result = self.invoke("observe", unknown)
        self.assertEqual(result["next_action"]["operation"], "lookup-exact-action")
        wrong = self.observation("ready", "lookup")
        wrong["receipt"]["attempt"] = "wrong"
        self.assertEqual(self.invoke("observe", wrong)["status"], "blocked")
        self.assertEqual(len(self.state()["observations"]), 2)
        ready = self.observation("ready", "lookup")
        self.invoke("observe", ready)
        self.assertEqual(self.state()["dispatch"]["status"], "bound")

    def test_proven_noncreation_can_retry_only_with_new_controller_request(self):
        self.begin("continuous")
        original_binding = copy.deepcopy(self.state()["handoff"]["binding"])
        original_controller = self.state()['control']['context']['controller_ref']
        original_packages = copy.deepcopy(self.state()['packages'])
        original_requirement = (self.flow / self.requirement_path).read_bytes()
        old_request = self.state()["dispatch"]["request"]
        old_action = copy.deepcopy(self.state()['host_action'])
        noncreation = self.observation("not-created", "lookup", ref=None)
        self.invoke("observe", noncreation)
        closed = self.state()
        old_result = copy.deepcopy(closed['last_result'])
        old_subject, old_revision = closed['stage_subject'], closed['revision']
        before_store = progress.state_store.StateStore(self.checkpoint)
        before_current = before_store.open_current()
        before_store.read_subject(before_current, old_subject)
        before_metrics = {'root_bytes': before_current.reference['size'],
            'stage_bytes': before_current.root['subjects'][old_subject]['size'],
            'files_read': before_store.files_read, 'bytes_read': before_store.bytes_read}
        retry = copy.deepcopy(self.input)
        retry["authorization"]["flow_mode"] = "continuous"
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("start", {"handoff": retry})
        retry["authorization"]["reference"] = "controller:retry-proven-not-created"
        with patch.object(transfer.supervision, "start_worktree", wraps=transfer.supervision.start_worktree) as create:
            started = self.invoke("start", {"handoff": retry})
            create.assert_not_called()
        self.assertEqual(started["next_action"]["operation"], "invoke-host")
        self.assertEqual(self.state()["handoff"]["binding"], original_binding)
        self.assertEqual(self.state()['control']['context']['controller_ref'], original_controller)
        self.assertEqual(self.state()['packages'], original_packages)
        self.assertEqual(self.state()['history'], [])
        self.assertEqual((self.flow / self.requirement_path).read_bytes(), original_requirement)
        self.assertNotEqual(self.state()["dispatch"]["request"]["digest"], old_request["digest"])
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        reference = self.state()['noncreation_retry']
        self.assertEqual(reference, current.root['metadata']['noncreation_retry'])
        proof = progress.read_noncreation_retry(self.state(), store=store, current=current)
        self.assertEqual((proof['stage_subject'], proof['source_revision']), (old_subject, old_revision))
        self.assertEqual(store.read_evidence(proof['result_ref'], run_id=current.run_id)['value'], old_result)
        retained_action = store.read_evidence(proof['action_ref'], run_id=current.run_id)['value']
        self.assertEqual(retained_action['action_id'], old_action['action_id'])
        self.assertEqual(retained_action['payload'], old_request)
        self.assertEqual(store.read_evidence(proof['observation_ref'], run_id=current.run_id)['value'], noncreation)
        self.assertNotIn('history', proof)
        self.assertNotIn('root', proof)
        self.assertLessEqual(reference['size'], progress.state_store.ROOT_LIMIT)
        self.assertLessEqual(current.reference['size'], progress.state_store.ROOT_LIMIT)
        self.assertLessEqual(current.root['subjects'][self.state()['stage_subject']]['size'], progress.state_store.SUBJECT_LIMIT)
        measured_store = progress.state_store.StateStore(self.checkpoint)
        measured_current = measured_store.open_current()
        measured_state = measured_store.read_subject(measured_current, self.state()['stage_subject']).value
        progress.read_noncreation_retry(measured_state, store=measured_store, current=measured_current)
        self.assertLessEqual(measured_store.bytes_read, progress.state_store.READ_LIMIT)
        if os.environ.get('T01_NONCREATION_RETRY_METRICS_PATH'):
            Path(os.environ['T01_NONCREATION_RETRY_METRICS_PATH']).write_text(json.dumps({
                'before': before_metrics, 'after': {'root_bytes': measured_current.reference['size'],
                    'stage_bytes': measured_current.root['subjects'][measured_state['stage_subject']]['size'],
                    'proof_bytes': reference['size'], 'files_read': measured_store.files_read,
                    'bytes_read': measured_store.bytes_read},
                'proof_refs': {key: proof[key]['size'] for key in ('result_ref', 'action_ref', 'observation_ref')},
                'history_entries': len(measured_state['history'])}, indent=2) + '\n')
        new_action = copy.deepcopy(self.state()['host_action'])
        before = self.checkpoint.read_bytes()
        with self.assertRaisesRegex(transfer.entry.PreparationError, 'finish the current stage'):
            self.invoke('start', {'handoff': retry})
        changed = copy.deepcopy(retry)
        changed['semantic']['objective'] = 'different work with the same retry decision'
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('start', {'handoff': changed})
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(self.invoke('advance')['next_action']['operation'], 'lookup-exact-action')
        self.assertEqual(self.state()['host_action'], new_action)
        self.assertEqual(self.invoke('inspect')['status'], 'active')
        late = self.observation()
        late["receipt"]["request_digest"] = old_request["digest"]
        self.assertEqual(self.invoke("observe", late)["status"], "blocked")

    def test_noncreation_retry_proof_references_fail_closed(self):
        self.begin('continuous')
        self.invoke('observe', self.observation('not-created', 'lookup', ref=None))
        retry = copy.deepcopy(self.input)
        retry['authorization'].update(flow_mode='continuous', reference='controller:retry-proof')
        self.invoke('start', {'handoff': retry})
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        reference = self.state()['noncreation_retry']
        proof = progress.read_noncreation_retry(self.state())
        before = self.checkpoint.read_bytes()
        for key in ('noncreation_retry', 'result_ref', 'action_ref', 'observation_ref'):
            with self.subTest(reference=key):
                selected = reference if key == 'noncreation_retry' else proof[key]
                path = store._path(selected['sha256'])
                original = path.read_bytes()
                for content in (None, b'corrupt retry proof'):
                    with self.subTest(content=content):
                        if content is None:
                            path.unlink()
                        else:
                            path.write_bytes(content)
                        try:
                            with self.assertRaises(transfer.entry.PreparationError):
                                self.invoke('inspect')
                            self.assertEqual(self.checkpoint.read_bytes(), before)
                        finally:
                            path.write_bytes(original)
        for changes in ({'run_id': 'different-run'}, {'size': progress.state_store.ROOT_LIMIT + 1},
                        {'kind': 'subject'}, {'sha256': 'invalid-digest'}):
            with self.subTest(changes=changes):
                owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
                owner.state['noncreation_retry'] = {**reference, **changes}
                with self.assertRaises(transfer.entry.PreparationError):
                    owner.save()
                self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertEqual(self.invoke('advance')['next_action']['operation'], 'lookup-exact-action')
        self.invoke('observe', self.observation('not-created', 'lookup', ref=None))
        retry['authorization']['reference'] = 'controller:retry-proof-again'
        self.assertEqual(self.invoke('start', {'handoff': retry})['next_action']['operation'], 'invoke-host')
        latest = self.state()
        self.assertEqual(latest['history'], [])
        latest_proof = progress.read_noncreation_retry(latest)
        self.assertNotEqual(latest['noncreation_retry'], reference)
        self.assertNotIn(reference['sha256'], json.dumps(latest_proof))
        self.assertEqual(store.read_evidence(reference, run_id=current.run_id)['value'], proof)
        self.assertLessEqual(latest['noncreation_retry']['size'], progress.state_store.ROOT_LIMIT)

    def test_untrusted_noncreation_cannot_enable_retry(self):
        self.begin('continuous')
        observation = self.observation('not-created', 'lookup', ref=None)
        observation.pop('provenance')
        self.invoke('observe', observation)
        self.assertNotEqual(self.state()['status'], 'cancelled')
        retry = copy.deepcopy(self.input)
        retry['authorization'].update(flow_mode='continuous', reference='controller:retry-missing-proof')
        before = self.checkpoint.read_bytes()
        with self.assertRaisesRegex(transfer.entry.PreparationError, 'reconcile non-creation'):
            self.invoke('start', {'handoff': retry})
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_noncreation_retry_valid_object_chain_rejects_false_proof(self):
        self.begin('continuous')
        self.invoke('observe', self.observation('not-created', 'lookup', ref=None))
        retry = copy.deepcopy(self.input)
        retry['authorization'].update(flow_mode='continuous', reference='controller:retry-semantic-proof')
        self.invoke('start', {'handoff': retry})
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        saved = self.state()
        proof = progress.read_noncreation_retry(saved)
        action = store.read_evidence(proof['action_ref'], run_id=current.run_id)['value']
        observation = store.read_evidence(proof['observation_ref'], run_id=current.run_id)['value']
        result = store.read_evidence(proof['result_ref'], run_id=current.run_id)['value']

        def publish_corrupt_fixture(changed):
            # Simulate storage corruption with valid hashes and run references.
            # The public handle must reject the proof's business facts too.
            active = store.open_current()
            candidate = copy.deepcopy(saved)
            candidate['noncreation_retry'] = store.write_object(active.run_id, 'evidence', changed)
            metadata = {**active.root['metadata'], 'noncreation_retry': candidate['noncreation_retry']}
            store.commit_current(expected_revision=active.revision, revision=active.revision + 1,
                subjects={candidate['stage_subject']: progress.stage_subject_value(candidate)},
                metadata=metadata, outer=active.root['outer'],
                scope=progress.stage2_scope(candidate, self.checkpoint,
                                           {**active.root['outer'], progress.KEY: candidate}))

        for corruption in ('schema', 'action', 'receipt', 'result'):
            with self.subTest(corruption=corruption):
                changed = copy.deepcopy(proof)
                if corruption == 'schema':
                    changed['unexpected'] = 'a valid hash cannot widen the proof schema'
                elif corruption == 'action':
                    changed_action = {**action, 'action_id': 'another-native-invocation'}
                    changed['action_ref'] = store.write_object(current.run_id, 'evidence', changed_action)
                elif corruption == 'receipt':
                    changed_observation = copy.deepcopy(observation)
                    changed_observation['receipt']['request_digest'] = 'f' * 64
                    changed_result = copy.deepcopy(result)
                    record = progress.handoff.unseal(changed_result['record'])
                    record['receipts'] = [changed_observation['receipt']]
                    changed_result['record'] = progress.handoff.seal(record)
                    changed['observation_ref'] = store.write_object(current.run_id, 'evidence', changed_observation)
                    changed['result_ref'] = store.write_object(current.run_id, 'evidence', changed_result)
                else:
                    changed['result_ref'] = store.write_object(current.run_id, 'evidence', {**result, 'status': 'unknown'})
                publish_corrupt_fixture(changed)
                before = self.checkpoint.read_bytes()
                with self.assertRaises(transfer.entry.PreparationError) as caught:
                    self.invoke('inspect')
                self.assertEqual(caught.exception.code, {'schema': 'invalid_request',
                    'action': 'host_evidence_missing', 'receipt': 'identity_mismatch',
                    'result': 'invalid_checkpoint'}[corruption])
                self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_noncreation_retry_cannot_change_work_or_target(self):
        self.begin("continuous")
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        before = self.checkpoint.read_bytes()
        changes = (("scope", "owned_paths", ["docs/other.md"]),
                   ("target", "branch", "other-target"),
                   ("binding", "worktree", str(self.flow) + "-replacement"),
                   ("semantic", "objective", "different work"))
        for field, key, value in changes:
            with self.subTest(field=field):
                retry = copy.deepcopy(self.input)
                retry["authorization"].update(flow_mode="continuous", reference="controller:retry")
                retry[field][key] = value
                with self.assertRaisesRegex(transfer.entry.PreparationError, "original work"):
                    self.invoke("start", {"handoff": retry})
                self.assertEqual(self.checkpoint.read_bytes(), before)

    def assert_noncreation_retry_keeps_stage_predecessor(self, request, role_ref):
        original = self.state()
        original_root = progress.state_store.StateStore(self.checkpoint).open_current()
        old_request = copy.deepcopy(original['dispatch']['request'])
        self.assertEqual(original['control']['context']['controller_ref'], 'task')
        self.invoke('observe', self.observation('not-created', 'lookup', ref=None))
        self.assertEqual(self.state()['status'], 'cancelled')
        retry = copy.deepcopy(request)
        for key in ('role', 'scope', 'target', 'binding', 'semantic'):
            retry[key] = copy.deepcopy(original['handoff'][key])
        retry['predecessor'] = copy.deepcopy(original['handoff']['predecessor'])
        retry['authorization']['reference'] = 'controller:retry-stage-' + str(original['stage'])
        # start durably saves the retry, then advance prepares its B dispatch.
        started = self.invoke('start', {'handoff': retry})
        self.assertEqual(started['next_action']['operation'], 'invoke-host', started)
        retried = self.state()
        for key in ('design_predecessor', 'consumed_readiness', 'implementation_predecessor',
                    'packages', 'native_host', 'workspace_evidence'):
            self.assertEqual(retried[key], original[key], key)
        self.assertEqual(retried['handoff']['binding'], original['handoff']['binding'])
        self.assertEqual(retried['control']['context']['controller_ref'], 'task')
        self.assertEqual(retried['history'], [])
        self.assertNotEqual(retried['dispatch']['request']['digest'], old_request['digest'])
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertEqual(current.run_id, original_root.run_id)
        self.assertLessEqual(current.reference['size'], progress.state_store.ROOT_LIMIT)
        self.assertEqual(self.invoke('advance')['next_action']['operation'], 'lookup-exact-action')
        self.assertEqual(self.invoke('observe', self.observation(ref=role_ref))['next_action']['operation'], 'wait-host')
        self.assertEqual(self.invoke('inspect')['status'], 'active')
        if original['stage'] == 3:
            self.assertEqual(progress.consumed_scope_readiness(self.state(), self.checkpoint),
                             original['consumed_readiness'])
        else:
            prior, _, _ = progress.read_implementation_delivery(self.checkpoint,
                                                                original['implementation_predecessor'])
            self.assertEqual(prior['accepted']['digest'], original['handoff']['predecessor']['accepted_digest'])
        late = self.observation()
        late['receipt']['request_digest'] = old_request['digest']
        self.assertEqual(self.invoke('observe', late)['status'], 'blocked')

    def test_noncreation_retry_stage3_retains_consumed_design_predecessor(self):
        design = self.finish_design('continuous')
        request = self.staged_successor(design)
        request['authorization']['flow_mode'] = 'continuous'
        self.assertEqual(self.invoke('start', {'handoff': request})['next_action']['operation'], 'invoke-host')
        self.assertIsNotNone(self.state()['design_predecessor'])
        self.assertIsNotNone(self.state()['consumed_readiness'])
        self.assert_noncreation_retry_keeps_stage_predecessor(request, 'native:dispatcher-retry')

    def test_noncreation_retry_stage4_retains_implementation_predecessor(self):
        self.accepted_implementation_for_closure()
        request = self.input_for(4, self.state()['accepted'])
        request['authorization']['flow_mode'] = 'continuous'
        self.assertEqual(self.invoke('start', {'handoff': request})['next_action']['operation'], 'invoke-host')
        self.assertIsNotNone(self.state()['implementation_predecessor'])
        self.assert_noncreation_retry_keeps_stage_predecessor(request, 'native:closure-retry')

    def test_unknown_creation_cannot_retry_with_fresh_controller_authorization(self):
        self.begin("continuous")
        self.invoke("observe", self.observation("unknown", ref=None))
        before = self.checkpoint.read_bytes()
        retry = copy.deepcopy(self.input)
        retry["authorization"].update(flow_mode="continuous", reference="controller:retry")
        with self.assertRaisesRegex(transfer.entry.PreparationError, "reconcile non-creation"):
            self.invoke("start", {"handoff": retry})
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_continue_uses_same_identity_and_no_duplicate_send(self):
        self.begin()
        self.invoke("observe", self.observation())
        self.settings.update(model='new-controller-model', reasoning_effort='low', turn_id='turn-2')
        value = self.observation("idle", "result", {"delivery_id": "turn-1", "status": "continue", "payload": {"progress": "slice-1"}})
        result = self.invoke("observe", value)
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertEqual(result["next_action"]["payload"]["ref"], "native:designer")
        self.assertEqual(self.invoke("advance")["next_action"]["operation"], "lookup-exact-action")
        action = self.state()["action"]
        duplicate_poll = copy.deepcopy(value)
        duplicate_poll["event_id"] = "second-host-poll"
        duplicate_poll["receipt"]["receipt_ref"] = "host:new-poll-same-result"
        self.assertTrue(self.invoke("observe", duplicate_poll)["acknowledged"])
        self.assertEqual(self.state()["action"], action)
        (self.flow / self.requirement_path).write_text("unexpected source drift\n")
        next_turn = self.observation("idle", "result", {"delivery_id": "turn-2", "status": "continue", "payload": {"progress": "slice-2"}})
        self.assertEqual(self.invoke("observe", next_turn)["status"], "blocked")
        self.assertEqual(self.state()["action"], action)

    def test_exact_decision_and_duplicate_answer(self):
        self.begin()
        self.invoke("observe", self.observation())
        value = self.observation("idle", "result", {"delivery_id": "turn-1", "status": "needs_input", "payload": {"question": "choose behavior"}})
        pending = self.invoke("observe", value)["pending"]
        answer = {"decision_id": pending["decision_id"], "subject": pending["subject"], "answer": "choice A", "reference": "controller:answer"}
        result = self.invoke("decide", answer)
        self.assertEqual(result["next_action"]["operation"], "continue-host")
        self.assertTrue(self.invoke("decide", answer, revision=0)["acknowledged"])

    def ready_publication(self, candidate, reference, role="native:designer"):
        observed = self.observation("stopped", "result", ref=role)
        observed["publication_candidate"] = {"candidate_commit": candidate, "artifacts": sorted(getattr(self, "planning_documents", {"docs/spec.md": None})) if role == "native:designer" else ["impl.py"], "checks": ["design readiness" if role == "native:designer" else "publication verified"]}
        response = self.invoke("observe", observed)
        self.assertEqual(response["status"], "needs_input", response)
        pending = response["pending"]
        self.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                               "answer": "accept", "reference": reference})

    def propose_design_scope(self, candidate, section="approved design"):
        saved = self.state()
        basis = {"commit": candidate, "path": "docs/spec.md", "section": section}
        proposal = {"proposal_id": "proposal-1", "expected_revision": 0,
            "candidate_commit": candidate, "manifest": [
                {"path": "CHANGELOG.md", "owner": "closure", "kind": "document",
                 "operation": "add", "basis": basis},
                *[{"path": path, "owner": "implementation", "kind": "source",
                   "operation": "add", "basis": basis}
                  for path in getattr(self, "implementation_paths", ["impl.py"])]],
            "implementation_required": True,
            "requirement_identity": copy.deepcopy(saved["handoff"]["requirement_identity"]),
            "binding": copy.deepcopy(saved["handoff"]["binding"]), "previous_scope_revision": 0}
        proposed = self.invoke("scope-propose", proposal)
        self.assertEqual(proposed["next_action"]["operation"], "scope-proposed", proposed)
        return proposed["next_action"]["proposal"]["digest"]

    def planning_spec(self):
        plan = {"schema_version": 2, "ticket_id": "FEP-01", "plan_version": 1,
            "spec_ref": {"path": "docs/spec.md", "section": "Implementation Decisions"},
            "contract_refs": ["FEP-01:What to build", "FEP-01:AC-1", "PRD:D1:Plan"],
            "blocked_by": [], "verifications": [{"id": "V1", "behavior": "approved behavior",
                "seam": "C/B/Git fixture", "commands":
                ["python3 scripts/run_tests.py --test-file tests/runtime/test_workflow_progress.py"]}],
            "steps": [{"id": "FP1-01", "target": {"path": "impl.py", "symbol": "run", "operation": "add"},
                "capability": "implement approved behavior", "approach": ["implement and connect run"],
                "inputs": ["approved design"], "outputs": ["observable result"],
                "failures": ["invalid input is reported"], "depends_on": [],
                "verification_refs": ["V1"], "read_only": []}], "artifact_steps": [], "behavior_slices": [{"id": "SL1",
                "step_ids": ["FP1-01"], "ac_refs": ["FEP-01:AC-1"], "verification_refs": ["V1"],
                "read_only": [], "shared_state": []}]}
        return ("approved design\nTicket ID: FEP-01\nPlan Version: 1\n"
                "**What to build:** approved behavior\n**Blocked by:** None\n"
                "AC-1: approved behavior\n## Implementation Decisions\n### D1 — Plan\n"
                "```execution-plan-v2\n" + json.dumps(plan) + "\n```\n")

    def seal_design_scope(self, proposal_digest):
        sealed = self.invoke("scope-seal", {"proposal_id": "proposal-1",
            "proposal_digest": proposal_digest, "controller_ref": "task",
            "decision": {"kind": "in-requirement-refinement", "reference": "controller:scope-review"}})
        self.assertEqual(sealed["next_action"]["operation"], "scope-sealed", sealed)

    def prepare_design_result(self, mode):
        self.begin(mode)
        self.invoke("observe", self.observation())
        documents = getattr(self, "planning_documents", None) or {"docs/spec.md": self.planning_spec()}
        for path, body in documents.items():
            target = self.flow / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        self.flow_git("add", *sorted(documents))
        self.flow_git("commit", "-qm", "plan")
        planning = self.flow_git("rev-parse", "HEAD")
        proposal_digest = self.propose_design_scope(planning)
        self.ready_publication(planning, "controller:planning-readiness")
        self.seal_design_scope(proposal_digest)
        published = self.invoke("publication", {"candidate_commit": planning, "reference": "controller:planning-readiness"})
        self.assertTrue(published["next_action"]["publication"]["result"]["ok"], published)
        payload = {"artifacts": ["docs/spec.md"], "checks": ["design readiness"], "planning_commit": planning,
                   "planning_merge_commit": self.flow_git("rev-parse", "HEAD"), "planning_paths": sorted(documents)}
        return payload

    def test_publication_requires_exact_readiness_and_stopped_writer(self):
        self.begin("continuous")
        self.invoke("observe", self.observation())
        (self.flow / "docs/spec.md").write_text(self.planning_spec())
        self.flow_git("add", "docs/spec.md")
        self.flow_git("commit", "-qm", "plan")
        candidate = self.flow_git("rev-parse", "HEAD")
        request = {"candidate_commit": candidate, "reference": "original:review"}
        result = self.invoke("publication", request)
        self.assertEqual(result["error"]["code"], "candidate_not_accepted")
        self.assertNotIn("publication", self.state())
        observed = self.observation("idle", "result")
        observed["publication_candidate"] = {"candidate_commit": candidate, "artifacts": ["docs/spec.md"], "checks": ["readiness"]}
        result = self.invoke("observe", observed)
        self.assertEqual(result["error"]["code"], "host_evidence_missing")
        # The idle response ended that turn. Obtain a causally new stop receipt,
        # then a current query; merely relabelling the old action is not proof.
        self.invoke("pause")
        self.invoke("observe", self.observation("stopped", "result"))
        self.invoke("resume")
        proposal_digest = self.propose_design_scope(candidate)
        self.ready_publication(candidate, "original:review")
        self.seal_design_scope(proposal_digest)
        result = self.invoke("publication", {**request, "reference": "different:review"})
        self.assertEqual(result["error"]["code"], "candidate_changed")
        self.assertNotIn("publication", self.state())
        self.assertEqual(self.invoke("publication", request)["next_action"]["operation"], "publication-receipt")
        received = self.invoke("receive-publication")
        self.assertEqual(received["pending"]["kind"], "acceptance")
        self.assertEqual(self.state()["dispatch"]["delivery"]["message"]["payload"], self.state()["publication"]["completion_payload"])

    def test_resume_reconciles_missing_publication_receipt_and_stop_blocks_writes(self):
        self.begin("continuous")
        self.invoke("observe", self.observation())
        (self.flow / "docs/spec.md").write_text(self.planning_spec())
        self.flow_git("add", "docs/spec.md")
        self.flow_git("commit", "-qm", "plan")
        candidate = self.flow_git("rev-parse", "HEAD")
        proposal_digest = self.propose_design_scope(candidate)
        self.ready_publication(candidate, "original:review")
        self.seal_design_scope(proposal_digest)
        original = transfer.supervision._merge_candidate_into_target
        def crash(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(transfer.supervision, "_merge_candidate_into_target", side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke("publication", {"candidate_commit": candidate, "reference": "original:review"})
        merged = self.git("rev-parse", "HEAD")
        self.assertEqual(self.invoke("pause")["status"], "paused")
        observation = self.invoke("reconcile-publication")
        self.assertEqual(observation["next_action"]["result"]["state"], "flow-advance-pending")
        self.assertEqual(self.flow_git("rev-parse", "HEAD"), candidate)
        with patch.object(transfer.supervision, "publish_planning", side_effect=AssertionError("republished")):
            recovered = self.invoke("resume")
        self.assertEqual(recovered["next_action"]["operation"], "publication-receipt")
        self.assertEqual(self.flow_git("rev-parse", "HEAD"), merged)
        self.assertEqual(self.invoke("resume")["pending"]["kind"], "acceptance")

    def test_overall_completion_requires_original_publication_and_cleanup(self):
        self.run_real_chain("continuous")
        evidence = progress.verify_completion(self.state())
        self.assertTrue(evidence["completed"])
        broken = copy.deepcopy(self.state())
        broken["publication"]["result"] = None
        with self.assertRaises(transfer.entry.PreparationError):
            progress.verify_completion(broken)
        broken = copy.deepcopy(self.state())
        broken["control"]["context"]["handoff_progress"]["state"] = "cleanup-pending"
        with self.assertRaises(transfer.entry.PreparationError):
            progress.verify_completion(broken)
        self.assertFalse(self.flow.exists())
        self.flow.mkdir()
        (self.flow / "personal.txt").write_text("unrelated replacement")
        with self.assertRaises(transfer.entry.PreparationError):
            progress.verify_completion(self.state())
        self.assertEqual((self.flow / "personal.txt").read_text(), "unrelated replacement")

    def finish_design(self, mode):
        payload = self.prepare_design_result(mode)
        observed = self.observation("stopped", "result", {"delivery_id": "design", "status": "completed", "payload": payload})
        pending = self.invoke("observe", observed)["pending"]
        answer = {"decision_id": pending["decision_id"], "subject": pending["subject"], "answer": "accept", "reference": "controller:readiness"}
        return self.invoke("decide", answer)

    def test_real_acceptance_and_stepwise_boundary(self):
        result = self.finish_design("stepwise")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["pending"]["kind"], "stage-entry")
        self.assertIn("stage_result", result["completion"])
        self.assertEqual(self.invoke("advance")["status"], "accepted")

    def test_continuous_acceptance_has_no_human_stage_gate(self):
        result = self.finish_design("continuous")
        self.assertIsNone(result["pending"])
        predecessor = self.state()["accepted"]
        self.context = self.context_for(3)
        self.begin("continuous", self.input_for(3, predecessor))
        self.assertFalse(self.state()["history"])
        self.assertEqual(self.state()["design_predecessor"]["accepted_digest"], predecessor["digest"])

    def test_successor_derives_a_evidence_predecessor_and_control_without_field_copying(self):
        self.finish_design("continuous")
        next_input = self.input_for(3, self.state()["accepted"])
        verified = self.invoke("prepare-requirement", {"request": {"protocol": transfer.requirement.PROTOCOL,
            "operation": "verify", "entry": next_input["entry"], "evidence": self.frozen}})
        self.assertEqual(verified["status"], "requirement-ready", verified)
        for key in ("expected_entry", "predecessor", "requirement"):
            next_input.pop(key)
        next_input["authorization"]["flow_mode"] = "continuous"
        result = self.invoke("start", {"handoff": next_input, "requirement_transaction": verified["transaction"]})
        self.assertEqual(result["next_action"]["operation"], "invoke-host", result)
        self.assertEqual(self.state()["stage"], 3)
        self.assertEqual(self.state()["control"]["context"]["carrier"]["kind"], "implementation-dispatcher")

    def test_compatible_registration_switch_keeps_original_executing_package(self):
        packages = {name: {"root": str(self.root / ("old-" + name)), "compatibility_key": {"preparation": "v1"}}
                    for name in ("solution-design", "guided-implementation", "change-closure")}
        transfer.entry.skill_preflight.preflight.return_value = {"packages": packages, "external": {}}
        self.request["target_stages"] = [2, 3, 4]
        self.input = self.input_for(2)
        self.finish_design("continuous")
        next_input = self.input_for(3, self.state()["accepted"])
        old_root = Path(packages["guided-implementation"]["root"])
        old_root.mkdir()
        (old_root / "package.json").write_text("{}")
        registered = copy.deepcopy(packages)
        registered["guided-implementation"]["root"] = str(self.root / "new-guided-registration")
        transfer.entry.skill_preflight.preflight.return_value = {"packages": registered, "external": {}}
        self.context = self.context_for(3)
        with patch.object(transfer.entry, "__file__", str(old_root / "scripts/entry_prepare.py")):
            self.begin("continuous", next_input)
        self.assertEqual(self.state()["packages"]["guided-implementation"], packages["guided-implementation"])

    def test_pause_and_cancel_require_stopped_host(self):
        self.begin()
        self.invoke("observe", self.observation())
        self.assertEqual(self.invoke("pause")["status"], "pausing")
        self.assertEqual(self.invoke("observe", self.observation("stopped", "result"))["status"], "paused")
        self.assertEqual(self.invoke("resume")["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(self.invoke("cancel")["status"], "cancelled")

    def pending_business_answer(self, stage=2):
        if stage == 3:
            self.begin_dispatcher()
        else:
            self.begin()
            self.invoke("observe", self.observation())
        ref = "native:dispatcher" if stage == 3 else "native:designer"
        result = self.invoke("observe", self.observation("idle", "result", {
            "delivery_id": "question", "status": "needs_input", "payload": {"question": "Continue?"}}, ref=ref))
        pending = result["pending"]
        return {"decision_id": pending["decision_id"], "subject": pending["subject"],
                "answer": "yes", "reference": "controller:answer"}

    def test_business_answer_does_not_revoke_pause(self):
        answer = self.pending_business_answer()
        self.invoke("pause")
        result = self.invoke("decide", answer)
        self.assertEqual(result["status"], "pausing")
        self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        self.assertEqual(self.state()["pending"]["decision_id"], answer["decision_id"])
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))

    def test_business_answer_does_not_revoke_cancellation(self):
        answer = self.pending_business_answer()
        self.invoke("cancel")
        result = self.invoke("decide", answer)
        self.assertEqual(result["status"], "cancelling")
        self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        self.assertEqual(self.state()["stop_requested"], "cancelling")
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))

    def test_paused_answer_consumes_only_after_explicit_resume_and_live_observation(self):
        answer = self.pending_business_answer()
        self.invoke("pause")
        before = self.state()["action"]
        first = self.invoke("decide", answer)
        self.assertTrue(first["decision_deferred"])
        self.assertEqual(self.state()["action"], before)
        self.assertTrue(self.invoke("decide", answer, revision=0)["acknowledged"])
        self.assertEqual(self.invoke("resume")["status"], "pausing")
        self.invoke("observe", self.observation("stopped", "result"))
        self.assertEqual(self.invoke("decide", answer)["status"], "paused")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["decision"], answer)
        resumed = self.invoke("resume")
        self.assertEqual(resumed["next_action"]["operation"], "inspect-host-state")
        self.assertEqual(self.state()["decisions"][answer["decision_id"]], answer)
        continued = self.invoke("observe", self.observation("idle", "result"))
        self.assertEqual(continued["next_action"]["operation"], "continue-host")
        action = self.state()["action"]
        self.assertTrue(self.invoke("decide", answer, revision=0)["acknowledged"])
        self.assertEqual(self.state()["action"], action)

    def test_cancelled_answer_cannot_replace_dispatcher_with_legacy_claims(self):
        answer = self.pending_business_answer(stage=3)
        self.invoke("pause")
        self.invoke("decide", answer)
        self.invoke("cancel")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["disposition"], "cancelled")
        self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher"))
        self.assertEqual(self.invoke("decide", answer)["status"], "cancelled")
        self.assertEqual(self.invoke("resume")["status"], "cancelled")
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("control", {"action": "recover-dispatch", "evidence": {
                "stopped_refs": ["native:dispatcher"], "file_hashes": {}, "replacement_ref": "native:replacement"},
                "receipt": {"host": "fixture", "stopped": "native:dispatcher", "ready": "native:replacement"}})
        self.assertEqual(self.state()["control"]["context"]["carrier"]["ref"], "native:dispatcher")
        self.assertEqual(self.invoke("resume")["status"], "cancelled")
        self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]]["disposition"], "cancelled")

    def test_answer_after_stop_receipt_is_saved_without_consuming_pending(self):
        answer = self.pending_business_answer()
        self.invoke("pause")
        self.invoke("observe", self.observation("stopped", "result"))
        saved = self.invoke("decide", answer)
        self.assertEqual(saved["status"], "paused")
        wrong = {**answer, "decision_id": "old-matter"}
        with self.assertRaises(transfer.entry.PreparationError): self.invoke("decide", wrong)
        with self.assertRaises(transfer.entry.PreparationError): self.invoke("decide", {**answer, "answer": "changed"})
        self.assertEqual(self.state()["status"], "paused")
        self.assertEqual(self.state()["pending"]["decision_id"], answer["decision_id"])
        self.assertEqual(self.invoke("resume")["next_action"]["operation"], "inspect-host-state")

    def test_stop_intent_guards_continue_and_effect_even_if_status_is_active(self):
        self.begin()
        self.invoke("observe", self.observation())
        outer = progress.read_projection(self.checkpoint, projection='scope').record
        state = outer[progress.KEY]
        state.update(status="active", step="continue", stop_requested="cancelling")
        progress.Progress(self.checkpoint, outer).save()
        result = self.invoke("advance")
        self.assertEqual(result["status"], "cancelling")
        self.assertEqual(result["next_action"]["operation"], "stop-host")
        owner = progress.Progress(self.checkpoint,
            progress.read_projection(self.checkpoint, projection='scope').record)
        for operation in ("invoke-host", "continue-host", "source-lifecycle", "carrier-lifecycle"):
            with self.assertRaises(transfer.entry.PreparationError): owner.effect(operation, {})

    def test_runner_stop_request_defers_reply_before_shared_suspend(self):
        answer = self.pending_business_answer()
        outer = progress.read_projection(self.checkpoint, projection='scope').record
        outer["runner_request"] = {"operation": "pause", "request_id": "saved-request"}
        progress.Progress(self.checkpoint, outer).save()
        response = self.invoke("decide", answer)
        self.assertEqual(response["status"], "pausing")
        self.assertTrue(response["decision_deferred"])
        self.invoke("observe", self.observation("stopped", "result"))
        self.assertEqual(self.invoke("resume")["status"], "paused")
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))

    def test_finished_receipt_intake_precedes_recovery_flags(self):
        for reason in ("technical", "pause", "consumed-decision"):
            with self.subTest(reason=reason):
                shared = {}
                consumed = reason == "consumed-decision"
                if consumed:
                    self.checkpoint.unlink()
                    queued = self.pending_business_answer()
                    self.invoke("decide", queued)
                    shared = copy.deepcopy(self.state())
                else:
                    queued = {"decision_id": "unconsumed", "subject": {"stage": 2}, "answer": "yes", "reference": "controller:input"}
                state = runner._new_state({})
                state.update(resume_progression=True, answer_pending_delivery="previous answer",
                             progression_response={"saved": reason})
                state["launch"] = {"stage": "stage2", "state": "launched", "turn": 1,
                                   "request": ["fixture", reason], "invocation_id": reason}
                state["controller_decision"] = queued
                self.checkpoint = self.checkpoint.resolve()
                if consumed:
                    identity = progress.read_projection(self.checkpoint, projection='identity')
                    outer = {key: copy.deepcopy(value) for key, value in identity.record.items() if key != progress.KEY}
                    outer.update(state)
                    outer['workflow_requirements'] = {"keep": "C-owned"}
                    progress.save_outer_record(self.checkpoint, outer, expected_revision=identity.authority_revision)
                else:
                    progress.atomic_save(self.checkpoint, {**state, "workflow_requirements": {"keep": "C-owned"}})
                state.refresh(runner._read_checkpoint(self.checkpoint))
                result = {"result": "needs_input", "artifacts": [], "evidence": [], "handoff_json": "{}",
                          "handoff": {}, "question": "Next choice?", "message": "", "needs_input_kind": "user_decision"}
                receipt = {"run_record": str(self.checkpoint), "stage": "stage2", "turn": 1, "invocation_id": reason,
                    "request_digest": transfer.entry.digest(state["launch"]["request"]), "outcome": "completed_turn",
                    "events": [{"type": "thread.started", "thread_id": "carrier"}, {"type": "turn.completed"}], "result": result}
                progress.atomic_save(runner._carrier_receipt_path(self.checkpoint, "stage2", 1), receipt)
                runner._recover_carrier_receipt(state, self.checkpoint)
                with patch.object(runner, "_invoke", side_effect=AssertionError("duplicate-carrier-invoke")) as invoked, redirect_stdout(io.StringIO()):
                    self.assertEqual(runner._advance(state, self.checkpoint), 0)
                self.assertEqual(invoked.call_count, 0)
                self.assertEqual(state["status"], "needs_input")
                if consumed:
                    self.assertNotIn("controller_decision", state)
                    self.assertEqual(self.state(), shared)
                else:
                    self.assertEqual(state["controller_decision"], queued)
                for key in ("resume_progression", "answer_pending_delivery", "progression_response"):
                    self.assertNotIn(key, state)
                self.assertEqual(progress.read_projection(self.checkpoint, projection="identity").record["workflow_requirements"], {"keep": "C-owned"})

    def test_cancellation_dominates_shared_pause_and_late_receipts(self):
        self.begin()
        self.invoke("observe", self.observation())
        cancelled = self.invoke("cancel")
        action = self.state()["action"]
        for operation in ("pause", "cancel", "pause"):
            result = self.invoke(operation)
            self.assertEqual(result["status"], "cancelling")
            self.assertEqual(self.state()["stop_requested"], "cancelling")
            self.assertEqual(self.state()["action"], action)
        stopped = self.invoke("observe", self.observation("stopped", "result"))
        self.assertEqual(stopped["status"], "cancelled")
        for operation in ("pause", "cancel", "resume"):
            result = self.invoke(operation)
            self.assertEqual(result["status"], "cancelled")
            self.assertNotEqual((result.get("next_action") or {}).get("operation"), "continue-host")
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("observe", self.observation("idle", "result"))
        self.assertEqual(self.state()["status"], "cancelled")

    def test_root_stop_does_not_close_unsettled_execution_allocation(self):
        self.prepare_execution()
        self.invoke('pause')
        self.invoke('observe', self.observation('stopped','result',ref='native:dispatcher'))
        self.assertEqual(self.state()['status'], 'pausing')
        self.assertFalse(self.state()['stopped'])

    def test_malformed_host_event_is_controlled_and_does_not_mutate_checkpoint(self):
        self.begin()
        self.save_transport(instance='fixture-host', state='live')
        before = self.checkpoint.read_bytes()
        for params in ([], None, {'item':[]}, {'item':None},
                       {'threadId':'carrier','item':{'type':'subAgentActivity','id':'call','kind':'started','agentPath':['bad']}}):
            with self.subTest(params=params):
                with self.assertRaises(transfer.entry.PreparationError) as error:
                    self.invoke('host-event',{'instance':'fixture-host','event':{'method':'item/started','params':params}})
                self.assertEqual(error.exception.code,'invalid_result')
                self.assertEqual(self.checkpoint.read_bytes(),before)

    def test_snapshot_rejects_malformed_native_alias_without_saving_it(self):
        self.begin()
        self.invoke('observe',self.observation())
        self.invoke('pause')
        observation = self.observation('stopped','result')
        snapshot = observation['lifecycle']
        snapshot['events'] = [{'method':'item/started','params':{'threadId':snapshot['carrier_thread'],
            'item':{'type':'subAgentActivity','id':'x','kind':'started','agentPath':['bad'],'agentThreadId':'child'}}}]
        before = self.checkpoint.read_bytes()
        for operation, data in (('observe',observation),('lifecycle-state',snapshot)):
            with self.subTest(operation=operation):
                with self.assertRaises(transfer.entry.PreparationError) as error:
                    self.invoke(operation,data)
                self.assertEqual(error.exception.code,'invalid_result')
                self.assertEqual(self.checkpoint.read_bytes(),before)

    def test_previous_candidate_reviewers_require_current_stop_evidence(self):
        self.begin_dispatcher()
        rounds = []
        for number in (1,2):
            (self.flow/'impl.py').write_text('implemented = '+str(number)+'\n')
            self.flow_git('add','impl.py'); self.flow_git('commit','-qm','candidate '+str(number))
            candidate = self.flow_git('rev-parse','HEAD')
            verification = self.mark_reviewable(candidate, evidence_suffix="-candidate-" + str(number))
            slots = {}
            for axis in ('standards','spec'):
                prepared = self.invoke('review-activity',{'operation':'prepare','axis':axis,'candidate':candidate,
                    'actor_ref':'task','verification':verification})
                identity = prepared['next_action']['action_id']
                ref = 'native:'+axis+'-'+str(number)
                request = {'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task','action_id':identity,
                    'receipt':{'adapter':'fixture','call_ref':'wait-'+ref,'response_ref':'stopped-'+ref,
                    'ref':ref,'status':'stopped','raw':{'task_name':ref,'status':'completed'}}}
                self.invoke('review-activity',request)
                slots[axis] = request
            rounds.append(slots)
        self.invoke('pause')
        observation = self.observation('stopped','result',ref='native:dispatcher')
        snapshot = observation['lifecycle']
        for request in rounds[0].values():
            ref = request['receipt']['ref']
            snapshot['pages'][0]['response']['data'].append({'id':ref,'status':{'type':'idle'}})
            snapshot['events'].append({'method':'item/completed','params':{'threadId':snapshot['carrier_thread'],
                'item':{'type':'subAgentActivity','id':'create-'+ref,'kind':'started','agentPath':ref,'agentThreadId':ref}}})
        self.assertEqual(self.invoke('observe',observation)['status'],'paused')
        active = copy.deepcopy(snapshot)
        active['pages'][0]['response']['data'][-1]['status'] = {'type':'active'}
        self.assertEqual(self.invoke('lifecycle-state',active)['status'],'pausing')
        omitted = copy.deepcopy(snapshot); omitted['pages'][0]['response']['data'].pop()
        self.assertEqual(self.invoke('lifecycle-state',omitted)['status'],'pausing')
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'paused')
        old = rounds[0]['standards']
        self.save_transport(instance=snapshot['instance'], state='live', native_event_sequence=1)
        event = {'method':'item/started','params':{'threadId':snapshot['carrier_thread'],'item':{
            'id':'late-old-review','type':'subAgentActivity','kind':'interacted','agentPath':old['receipt']['ref'],
            'agentThreadId':old['receipt']['ref']}}}
        self.assertEqual(self.invoke('host-event',{'instance':snapshot['instance'],'event':event})['status'],'pausing')
        fresh = copy.deepcopy(snapshot); fresh['sequence'] = 1
        fresh['events'].append({**event,'method':'item/completed'})
        self.assertEqual(self.invoke('lifecycle-state',fresh)['status'],'pausing')
        duplicate = self.invoke('review-activity',old)
        self.assertEqual(duplicate['status'],'pausing')
        self.assertTrue(duplicate['acknowledged'])
        stopped = copy.deepcopy(old)
        stopped['receipt'].update(call_ref='fresh-old-stop',response_ref='fresh-old-stopped')
        self.assertEqual(self.invoke('review-activity',stopped)['status'],'paused')

    def test_descendant_lookup_rejects_incomplete_archive_or_source_coverage(self):
        self.begin()
        self.invoke('observe',self.observation())
        snapshot = self.observation('stopped','result')['lifecycle']
        for mutation in ('missing-archive','filtered-source','filtered-provider','filtered-cwd','moved-between-partitions','missing-page'):
            incomplete = copy.deepcopy(snapshot)
            if mutation == 'missing-archive':
                incomplete['pages'] = incomplete['pages'][:1]
            elif mutation == 'filtered-source':
                incomplete['pages'][0]['request']['params']['sourceKinds'] = ['appServer']
            elif mutation == 'filtered-provider':
                incomplete['pages'][0]['request']['params']['modelProviders'] = ['one-provider']
            elif mutation == 'filtered-cwd':
                incomplete['pages'][0]['request']['params']['cwd'] = str(self.root)
            elif mutation == 'moved-between-partitions':
                incomplete['pages'][1]['response']['data'] = copy.deepcopy(incomplete['pages'][0]['response']['data'])
            else:
                incomplete['pages'][0]['response']['nextCursor'] = 'missing-page'
            result = self.invoke('lifecycle-state',incomplete)
            self.assertEqual(result['status'],'blocked',mutation)
            self.assertEqual(result['error']['code'],'host_evidence_missing',mutation)
            self.assertIsNone(self.state().get('lifecycle_snapshot'))

    def test_stop_barrier_includes_both_review_axes_and_unknown_descendants(self):
        self.begin_dispatcher()
        (self.flow / 'impl.py').write_text('implemented = True\n')
        self.flow_git('add','impl.py'); self.flow_git('commit','-qm','candidate')
        candidate = self.flow_git('rev-parse','HEAD')
        verification = self.mark_reviewable(candidate)
        slots = {}
        for axis in ('standards','spec'):
            prepare = {'operation':'prepare','axis':axis,'candidate':candidate,'actor_ref':'task',
                       'verification':verification}
            first = self.invoke('review-activity',prepare)
            slots[axis] = first['next_action']['action_id']
            duplicate = self.invoke('review-activity',prepare)
            self.assertEqual(duplicate['next_action']['operation'],'lookup-exact-review')
            self.assertEqual(duplicate['next_action']['action_id'],slots[axis])
            self.invoke('review-activity',{'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task',
                'action_id':slots[axis], 'receipt':{'adapter':'fixture','call_ref':'spawn-'+axis,'response_ref':'created-'+axis,
                'ref':'native:'+axis,'status':'running','raw':{'task_name':'native:'+axis,'status':'running'}}})
        self.invoke('pause')
        self.invoke('observe',self.observation('stopped','result',ref='native:dispatcher'))
        self.assertEqual(self.state()['status'],'pausing')
        for axis in ('standards','spec'):
            self.invoke('review-activity',{'operation':'observe','axis':axis,'candidate':candidate,'actor_ref':'task',
                'action_id':slots[axis], 'receipt':{'adapter':'fixture','call_ref':'wait-'+axis,'response_ref':'stopped-'+axis,
                'ref':'native:'+axis,'status':'stopped','raw':{'task_name':'native:'+axis,'status':'completed'}}})
        snapshot = self.observation('stopped','result',ref='native:dispatcher')['lifecycle']
        snapshot['pages'][0]['response']['data'].append({'id':'unreconciled-grandchild','status':{'type':'idle'}})
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'pausing')
        snapshot['pages'][0]['response']['data'].pop()
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'paused')
        self.assertTrue(self.state()['stopped'])

    def test_late_spawn_revokes_complete_partition_snapshot(self):
        self.begin()
        self.invoke('observe',self.observation())
        self.invoke('pause')
        self.save_transport(instance='fixture-host', state='live', native_event_sequence=0)
        self.assertEqual(self.invoke('observe',self.observation('stopped','result'))['status'],'paused')
        original = copy.deepcopy(self.state()['lifecycle_snapshot'])
        omitted = copy.deepcopy(original)
        omitted['pages'][0]['response']['data'] = []
        self.assertEqual(self.invoke('lifecycle-state',omitted)['status'],'pausing')
        self.assertEqual(self.invoke('lifecycle-state',original)['status'],'paused')
        self.save_transport(native_event_sequence=1)
        event = {'method':'item/started','params':{'threadId':self.state()['lifecycle_snapshot']['carrier_thread'],
            'turnId':'original-turn','item':{'type':'subAgentActivity','id':'late-call','kind':'started',
            'agentPath':'native:late-child','agentThreadId':'late-child-thread'}}}
        self.assertEqual(self.invoke('host-event',{'instance':'fixture-host','event':event})['status'],'pausing')
        snapshot = copy.deepcopy(self.state()['lifecycle_snapshot'])
        snapshot.update(sequence=1,events=[event])
        self.assertEqual(self.invoke('lifecycle-state',snapshot)['status'],'pausing')
        self.assertFalse(self.state()['stopped'])

    def test_failed_lookup_cannot_reuse_prior_complete_snapshot(self):
        self.begin()
        self.invoke('observe',self.observation())
        self.invoke('pause')
        self.save_transport(instance='fixture-host', state='live', native_event_sequence=0)
        self.assertEqual(self.invoke('observe',self.observation('stopped','result'))['status'],'paused')
        self.save_transport(lookup_blocked='original host lookup unavailable')
        self.assertEqual(self.invoke('advance')['status'],'pausing')
        self.assertFalse(self.state()['stopped'])

    def paused_runner_cancel(self, saved_answer, recovery=None, stage=2):
        answer = self.pending_business_answer(stage)
        state = runner._new_state({})
        state["current_stage"] = "stage" + str(stage)
        state["launch"].update(state="completed_turn", turn=1)
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
        if saved_answer:
            self.invoke("decide", answer)
        self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher" if stage == 3 else "native:designer"))
        with redirect_stdout(io.StringIO()):
            runner._advance(state, self.checkpoint)
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["status"], "paused")
        stopped = copy.deepcopy(self.state()["observations"])
        action = copy.deepcopy(self.state()["action"])
        with patch.object(runner, "_invoke", side_effect=AssertionError("carrier reissued")), redirect_stdout(io.StringIO()):
            if recovery:
                # A crash after the runner stop request is durable, before C intake.
                outer = runner._read_json(self.checkpoint, "run record")
                outer["runner_request"] = {"operation": "cancel", "request_id": "durable-cancel"}
                store = progress.state_store.StateStore(self.checkpoint)
                original = store.open_current()
                progress.save_outer_record(self.checkpoint,
                    {key: value for key, value in outer.items() if key != progress.KEY},
                    expected_revision=outer.authority_revision)
                current = store.open_current()
                self.assertEqual(current.root["subjects"], original.root["subjects"])
                self.assertEqual(current.root["metadata"], original.root["metadata"])
                self.invoke(recovery)
            else:
                runner.request_control(self.checkpoint, "cancel")
            self.assertEqual(self.state()["status"], "cancelled")
            for operation in ("cancel", "cancel", "pause"):
                runner.request_control(self.checkpoint, operation)
                for shared_operation in ("advance", "resume", "pause"):
                    result = self.invoke(shared_operation)
                    self.assertEqual(result["status"], "cancelled")
                    self.assertIsNone(result["next_action"])
                runner._advance(state, self.checkpoint)
            # A fully stopped cancellation is a read-only acknowledgement,
            # never permission to resume the cancelled carrier.
            before_ack = self.checkpoint.read_bytes()
            with patch.object(runner, "validate_confirmed", return_value={}):
                self.assertEqual(runner.resume(self.checkpoint), 0)
            self.assertEqual(self.checkpoint.read_bytes(), before_ack)
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["status"], "cancelled")
        self.assertEqual(self.state()["stop_requested"], "cancelling")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["state"], "cancelled")
        self.assertTrue(self.state()["stopped"])
        self.assertEqual(self.state()["observations"], stopped)
        self.assertEqual(self.state()["action"], action)
        if saved_answer:
            self.assertEqual(self.state()["deferred_decisions"][answer["decision_id"]],
                             {"decision": answer, "disposition": "cancelled"})
            self.assertTrue(self.invoke("decide", answer)["acknowledged"])
        else:
            self.assertTrue(self.invoke("decide", answer)["decision_deferred"])
        self.assertNotIn(answer["decision_id"], self.state().get("decisions", {}))
        self.assertEqual(self.state()["status"], "cancelled")

    def test_runner_cancel_upgrades_stopped_pause_without_answer(self):
        self.paused_runner_cancel(False)

    def test_runner_cancel_upgrades_stopped_pause_with_answer(self):
        self.paused_runner_cancel(True)

    def test_advance_recovers_saved_cancel_over_stopped_pause(self):
        self.paused_runner_cancel(True, "advance")

    def test_resume_recovers_saved_cancel_over_stopped_pause(self):
        self.paused_runner_cancel(False, "resume")

    def test_dispatcher_runner_cancel_upgrades_stopped_pause(self):
        self.paused_runner_cancel(True, stage=3)

    def test_dispatcher_resume_recovers_saved_cancel_over_stopped_pause(self):
        self.paused_runner_cancel(False, "resume", stage=3)

    def test_runner_pause_waits_for_native_stopped_proof(self):
        self.begin()
        self.invoke("observe", self.observation())
        state = runner._new_state({})
        state["launch"].update(state="completed_turn", turn=1)
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertFalse(self.state()["stopped"])
        self.assertEqual(self.state()["status"], "pausing")
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["status"], "pausing")
        self.invoke("observe", self.observation("stopped", "result"))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["status"], "paused")

    def test_runner_pause_before_any_carrier_can_be_paused(self):
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertEqual(progress.read_record(self.checkpoint)["status"], "paused")

    def test_runner_pause_with_unknown_creation_stays_pausing(self):
        self.begin()
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "pause")
            runner._advance(state, self.checkpoint)
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["status"], "pausing")
        self.assertFalse(self.state()["stopped"])
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        with redirect_stdout(io.StringIO()):
            runner._advance(state, self.checkpoint)
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["status"], "paused")

    def test_runner_and_shared_cancel_pause_order_keeps_cancellation(self):
        self.begin()
        self.invoke("observe", self.observation())
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        with redirect_stdout(io.StringIO()):
            runner.request_control(self.checkpoint, "cancel")
            self.invoke("pause")
            runner.request_control(self.checkpoint, "pause")
            runner._advance(state, self.checkpoint)
        self.assertEqual(self.state()["stop_requested"], "cancelling")
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["runner_request"]["operation"], "cancel")
        self.invoke("observe", self.observation("stopped", "result"))
        with redirect_stdout(io.StringIO()):
            runner._advance(state, self.checkpoint)
        self.assertEqual(runner._read_json(self.checkpoint, "run record")["status"], "cancelled")

    def test_runner_pause_acknowledges_prior_shared_cancellation_without_write(self):
        self.begin()
        self.invoke("observe", self.observation())
        state = runner._new_state({})
        runner._atomic_save(self.checkpoint, state)
        self.invoke("cancel")
        before = self.checkpoint.read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            runner.request_control(self.checkpoint, "pause")
        self.assertEqual(json.loads(output.getvalue())["status"], "cancelling")
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.invoke("observe", self.observation("stopped", "result"))
        output = io.StringIO()
        with redirect_stdout(output):
            runner.request_control(self.checkpoint, "pause")
        self.assertEqual(json.loads(output.getvalue())["status"], "cancelled")

    def test_completed_result_arriving_during_pause_is_consumed_on_resume(self):
        payload = self.prepare_design_result("stepwise")
        self.assertEqual(self.invoke("pause")["status"], "paused")
        observed = self.observation("stopped", "result", {"delivery_id": "design", "status": "completed", "payload": payload})
        self.assertEqual(self.invoke("observe", observed)["status"], "paused")
        self.assertIsNone(self.state()["dispatch"]["delivery"])
        resumed = self.invoke("resume")
        self.assertEqual(resumed["status"], "needs_input", resumed)
        self.assertEqual(resumed["pending"]["kind"], "acceptance")
        self.assertEqual(self.state()["dispatch"]["status"], "received")
        pending = resumed["pending"]
        self.assertEqual(self.invoke("pause")["status"], "paused")
        self.assertEqual(self.invoke("resume")["pending"], pending)

    def accept_fixture_candidate(self, candidate):
        """Recreate temporary candidate bytes only through the real writer grant."""
        owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        checkpoint = owner.state['control']['context']['handoff_progress']
        self.assertEqual(self.flow_git('rev-parse', 'HEAD'), candidate)
        self.assertFalse(self.flow_git('status', '--porcelain'))
        executions = progress.control.all_execution_results(checkpoint)
        self.assertTrue(all(item.get('stopped') is True and item['state'] == 'accepted'
            for item in executions if item.get('agent_ref')), 'fixture cannot replace unknown or unsettled owners')
        allowed = checkpoint['allowed_paths']
        expected = {name: progress.control_git.committed_fingerprint(self.flow,
            checkpoint['git_baseline_commit'], name) for name in allowed}
        if checkpoint.get('integration_source') is not None:
            expected = copy.deepcopy(checkpoint['integration_source']['snapshot']['files'])
        else:
            revision = checkpoint.get('revalidation_source')
            if revision:
                expected.update({name: revision['snapshot']['files'][name] for name in revision['pending_paths']})
            for result in checkpoint.get('carried_results', []) + executions:
                if result.get('state') == 'accepted':
                    expected.update(execution_plan.accepted_owned_fingerprints(result))
        changed = [name for name in allowed if progress.control_git.file_fingerprint(self.flow, name) != expected[name]]
        if not changed:
            return
        original = progress.control_git.snapshot(self.flow)
        protected = {name: progress.control_git.file_fingerprint(self.flow, name) for name in checkpoint['protected_paths']}
        catalog = execution_plan.current_catalog(owner.state['scope_authority'])
        self.assertEqual(len(catalog['tickets']), 1, 'multi-Ticket candidates use their original execution fixtures')
        ticket = catalog['tickets'][0]
        steps = ticket['plan']['steps'] + ticket['plan']['artifact_steps']
        owned = sorted({step.get('target', {}).get('path', step.get('path')) for step in steps})
        self.assertLessEqual(set(changed), set(owned))
        contents = {}
        for name in owned:
            path = self.flow / name
            self.assertFalse(path.is_symlink())
            self.assertTrue(path.resolve().is_relative_to(self.flow.resolve()))
            contents[name] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
        commits = [checkpoint['git_baseline_commit']] + self.flow_git('rev-list',
            checkpoint['git_baseline_commit'] + '..' + candidate).splitlines()
        for name in changed:
            path = self.flow / name
            if expected[name] is None:
                path.unlink()
                continue
            source = next((commit for commit in commits
                if progress.control_git.committed_fingerprint(self.flow, commit, name) == expected[name]), None)
            self.assertIsNotNone(source, 'fixture must retain exact original accepted bytes and mode: ' + name)
            body = subprocess.run(['git', '-C', str(self.flow), 'show', source + ':' + name],
                capture_output=True, check=True).stdout
            tree = self.flow_git('ls-tree', source, '--', name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.chmod(0o755 if tree.startswith('100755 ') else 0o644)
            self.assertEqual(progress.control_git.file_fingerprint(self.flow, name), expected[name])
        previous_thread = self.settings['thread_id']
        previous_host = copy.deepcopy(self.request['host'])
        try:
            execution = ProgressTests.execution_input(self)
            execution['scope']['owned_paths'] = owned
            execution['semantic']['plan_refs'] = [execution_plan.step_ref(ticket, step) for step in steps]
            execution['authorization']['scope_digest'] = transfer.entry.digest(execution['scope'])
            identity = 'fixture-candidate-' + candidate + '-' + str(owner.state['scope_authority']['revision'])
            prepared = self.invoke('allocation', {'allocation_id': identity, 'operation': 'prepare', 'handoff': execution})
            self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)
            slot = ProgressTests.allocation_slot(self, identity, ticket['ticket_id'])
            ref = 'native:' + identity
            bound = self.invoke('allocation', {'allocation_id': identity, 'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref=ref)})
            self.assertEqual(bound['next_action']['result']['status'], 'bound', bound)
            for name, content in contents.items():
                path = self.flow / name
                if content is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content[0])
                    path.chmod(content[1])
            actual = progress.control_git.snapshot(self.flow)
            self.assertEqual({key: actual[key] for key in ('head', 'branch', 'index_hash')},
                             {key: original[key] for key in ('head', 'branch', 'index_hash')})
            self.assertEqual({name: progress.control_git.file_fingerprint(self.flow, name) for name in protected}, protected)
            payload = {'changed_paths': sorted(changed),
                'file_hashes': {name: progress.control_git.implementation_hash(self.flow, name) for name in changed},
                'tests': ['deterministic public execution fixture'], 'write_release': bound['next_action']['result']['write_release'],
                'plan_refs': execution['semantic']['plan_refs'], 'plan_authority': checkpoint['plan_authority']}
            received = self.invoke('allocation', {'allocation_id': identity, 'operation': 'receive',
                'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=ref),
                'result': {'delivery_id': identity, 'status': 'completed', 'payload': payload}})
            self.assertEqual(received['next_action']['result']['status'], 'received', received)
            accepted = self.invoke('allocation', {'allocation_id': identity, 'operation': 'accept',
                'decision': {'reference': 'dispatcher:' + identity}})
            self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
        finally:
            self.settings['thread_id'] = previous_thread
            self.request['host'] = previous_host

    def complete_fixture_local_reviews(self, candidate, evidence_suffix):
        """Deterministic host/command responses consumed by original C review calls."""
        import test_layered_review
        owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
        for ticket in owner.ticket_checks_due():
            checked = test_layered_review.LayeredReviewTests.early_ticket_check(self, candidate, ticket)
            self.assertTrue(checked['next_action']['result']['ok'], checked)
        catalog = execution_plan.current_catalog(self.state()['scope_authority'])
        for ticket in catalog['tickets']:
            axes = [('local:' + ticket['ticket_id'] + ':' + group['id'], 'local-review')
                for group in ticket['plan']['behavior_slices']] + [('ticket:' + ticket['ticket_id'], 'ticket-accept')]
            for axis, action in axes:
                owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
                _, applications = owner.review_applications(candidate)
                if applications.get(axis, {}).get('applicable'):
                    continue
                actor_ref = owner.state['handoff']['expected_entry']['actor']['thread_id']
                controller_ref = owner.state['control']['context']['controller_ref']
                checkpoint = owner.state['control']['context']['handoff_progress']
                checks = transfer.checks_for(progress.review_evidence.local_required_checks(catalog, axis, str(self.flow)), candidate)
                identity = candidate + ':' + axis + evidence_suffix
                source = {'adapter': 'fixture-command', 'call_ref': 'check:' + identity,
                    'response_ref': 'check-stopped:' + identity, 'raw': {'checks': copy.deepcopy(checks),
                    'source_unchanged': True, 'stopped': True, 'dispatcher_ref': owner.state['control']['context']['carrier']['ref'],
                    'candidate': candidate, 'run_id': progress.read_record(self.checkpoint)['current']['run_id'],
                    'package_digest': transfer.entry.digest(self.state()['packages']), 'plan_authority': checkpoint['plan_authority'],
                    'environment': {'adapter': 'fixture-command', 'cwd': str(self.flow)},
                    'fixture': {'axis': axis, 'candidate': candidate}}}
                prepared = self.invoke('review-activity', {'operation': 'prepare', 'axis': axis, 'candidate': candidate,
                    'actor_ref': actor_ref, 'verification': {'plan_authority': checkpoint['plan_authority'], 'checks': checks, 'source': source}})
                self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-review', prepared)
                scope = prepared['next_action']['review_scope']
                response = 'review-stopped:' + identity
                observed = self.invoke('review-activity', {'operation': 'observe', 'axis': axis, 'candidate': candidate,
                    'actor_ref': actor_ref, 'action_id': prepared['next_action']['action_id'], 'receipt': {'adapter': 'fixture-native',
                    'call_ref': 'review:' + identity, 'response_ref': response, 'status': 'stopped', 'ref': 'native:reviewer:' + axis,
                    'raw': {'review': {'status': 'accepted', 'findings': [], 'checks': checks,
                        **{key: scope[key] for key in ('read_paths', 'ac_refs', 'dependency_claim_ids', 'requirement_refs')},
                        'review_scope': scope}}}})
                self.assertNotEqual(observed['status'], 'blocked', observed)
                accepted = self.invoke('control', {'action': action, 'evidence': {'axis': axis, 'candidate': candidate,
                    'result_ref': response}, 'receipt': {'controller_ref': controller_ref, 'reference': 'controller:' + identity}})
                self.assertNotEqual(accepted['status'], 'blocked', accepted)

    def mark_reviewable(self, candidate, *, fixture_execution=True, evidence_suffix=""):
        current = self.state()['control']['context']
        dispatcher = current['carrier']['ref']
        if current['handoff_progress'].get('candidate') is not None:
            self.invoke('control', {'action':'invalidate-candidate',
                'evidence':{'candidate':current['handoff_progress']['candidate'],'reference':'fixture:remediation','reason':'replacement'},
                'receipt':{'adapter':'fixture','call_ref':'remediate','response_ref':'remediation','raw':{'decision':'fix'}}})
        if fixture_execution:
            ProgressTests.accept_fixture_candidate(self, candidate)
        ProgressTests.complete_fixture_local_reviews(self, candidate, evidence_suffix)
        self.invoke('observe', self.observation('idle','result',ref=dispatcher))
        current = self.state()['control']['context']
        plan = current['handoff_progress']['validation_plan']
        checks = transfer.checks_for(plan['review_required'],candidate)
        result = self.invoke('control', {'action':'candidate-ready', 'evidence':{
            'dispatcher_ref':dispatcher,'attempt':current['carrier']['attempt'],'commit':candidate,
            'expected_target_head':self.git('rev-parse','main'),'binding':self.binding,
            'plan_digest':transfer.control.digest(plan),'checks':checks},
            'receipt':{'adapter':'fixture','call_ref':('focused' + evidence_suffix),'response_ref':('focused-result' + evidence_suffix),'raw':{'checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':dispatcher}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'],'reviewable',result)
        return {'candidate':candidate,'plan_digest':transfer.control.digest(plan),
                'checks':checks,'plan_authority':copy.deepcopy(
                    self.state()['control']['context']['handoff_progress']['plan_authority'])}

    def accept_current(self, payload, role, *, fixture_execution=True, evidence_suffix=""):
        if self.state()['stage'] == 3:
            verification = self.mark_reviewable(payload['candidate_commit'], fixture_execution=fixture_execution, evidence_suffix=evidence_suffix)
            actor = self.state()['handoff']['expected_entry']['actor']['thread_id']
            for axis in ('standards','spec'):
                prepared = self.invoke('review-activity', {'operation':'prepare','axis':axis,'candidate':payload['candidate_commit'],
                    'actor_ref':actor,'verification':verification})
                self.assertEqual(prepared['next_action']['operation'],'invoke-review',prepared)
                ref = payload['review'][axis]['reviewer_ref']
                self.invoke('review-activity',{'operation':'observe','axis':axis,'candidate':payload['candidate_commit'],
                    'actor_ref':actor,'action_id':prepared['next_action']['action_id'],
                    'receipt':{'adapter':'fixture','call_ref':('review-' + evidence_suffix)+axis,'response_ref':('terminal-' + evidence_suffix)+axis,
                               'ref':ref,'status':'stopped','raw':{'task_name':ref,'status':'completed',
                                   'review': {'status': 'accepted', 'findings': [],
                                       'checks': verification['checks'], **{
                                           key: prepared['next_action']['review_scope'][key] for key in
                                           ('read_paths', 'ac_refs', 'requirement_refs', 'dependency_claim_ids')},
                                       'review_scope': prepared['next_action']['review_scope']}}}})
            candidate = payload['candidate_commit']
            checkpoint = self.state()['control']['context']['handoff_progress']
            review = {axis:{'candidate':candidate,'expected_target_head':checkpoint['expected_target_head'],
                      'plan_digest':checkpoint['plan_digest'],
                      'plan_authority':checkpoint['plan_authority'],
                      'reviewer_ref':payload['review'][axis]['reviewer_ref'],
                      'status':'accepted','result_ref':('terminal-' + evidence_suffix)+axis} for axis in ('standards','spec')}
            response = self.invoke('control', {'action':'review-converged',
                'evidence':{'candidate':candidate,'review':review,'reference':'fixture:converged'},
                'receipt':{'adapter':'fixture','call_ref':('review' + evidence_suffix),'response_ref':('review-converged' + evidence_suffix),'raw':{'review':review}}})
            checkpoint = self.state()['control']['context']['handoff_progress']
            self.assertEqual(checkpoint['state'],'final-validation-pending',response)
            context = self.state()['control']['context']
            start = {'attempt_id':('fixture-final' + evidence_suffix),'dispatcher_ref':role,'carrier_attempt':context['carrier']['attempt'],
                     **{key:checkpoint[key] for key in ('candidate','expected_target_head',
                         'plan_digest','plan_authority','review_digest')}}
            response = self.invoke('control', {'action':'validation-start','evidence':start,
                'receipt':{'adapter':'fixture','call_ref':('final' + evidence_suffix),'response_ref':('final-start' + evidence_suffix),'raw':{'decision':'run'}}})
            self.assertIsNotNone(response['next_action'], response)
            if response['next_action']['operation'] == 'inspect-host-state':
                response = self.invoke('observe', self.observation('idle', 'result', ref=role))
            self.assertEqual(response['next_action']['operation'],'continue-host', response.get('error'))
            checks = transfer.checks_for(checkpoint['validation_plan']['final_required'], candidate)
            response = self.invoke('control', {'action':'validation-result','evidence':{'attempt_id':('fixture-final' + evidence_suffix),'checks':checks},
                'receipt':{'adapter':'fixture','call_ref':('command' + evidence_suffix),'response_ref':('command-result' + evidence_suffix),
                           'raw':{'attempt_id':('fixture-final' + evidence_suffix),'checks':checks,'source_unchanged':True,'stopped':True,'dispatcher_ref':role}}})
            checkpoint = self.state()['control']['context']['handoff_progress']
            self.assertEqual(checkpoint['state'],'deliverable',response)
            payload.update(review=review, verification=transfer.control.delivery_verification(checkpoint,role))
        message = {"delivery_id": "completed-" + str(self.state()["stage"]), "status": "completed", "payload": payload}
        result = self.invoke("observe", self.observation("stopped", "result", message, ref=role))
        self.assertEqual(result["status"], "needs_input", result)
        pending = result["pending"]
        return self.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                                     "answer": "accept", "reference": "controller:accept"})

    def finish_next(self, stage, mode):
        if stage == 2:
            return self.finish_design(mode)
        predecessor = self.state()["accepted"]
        value = self.input_for(stage, predecessor)
        self.context = self.context_for(stage)
        self.begin(mode, value)
        role = "native:dispatcher" if stage == 3 else "native:closure"
        self.invoke("observe", self.observation(ref=role))
        if stage == 3:
            (self.flow / "impl.py").write_text("print('ok')\n")
            self.flow_git("add", "impl.py")
            self.flow_git("commit", "-qm", "implementation")
            candidate = self.flow_git("rev-parse", "HEAD")
            payload = {"artifacts": ["impl.py"], "checks": ["behavior tested"], "candidate_commit": candidate,
                "review": {axis: {"candidate": candidate, "reviewer_ref": axis, "status": "accepted"} for axis in ("standards", "spec")},
                "verification": {"candidate": candidate, "checks": ["behavior tested"]}}
        else:
            candidate = predecessor["payload"]["candidate_commit"]
            self.ready_publication(candidate, "controller:closure", role)
            published = self.invoke("publication", {"candidate_commit": candidate,
                "expected_target_head": self.git("rev-parse", "HEAD"), "reference": "controller:closure"})
            self.assertTrue(published["next_action"]["publication"]["result"]["ok"], published)
            payload = {"artifacts": ["impl.py"], "checks": ["publication verified"], "candidate_commit": candidate,
                "merge_commit": self.git("rev-parse", "HEAD"), "changed_paths": self.state()["publication"]["result"]["changed_paths"],
                "cleanup": {"worktree_removed": True, "branch_removed": True, "disposition": "deleted", "disposition_verified": True}}
        return self.accept_current(payload, role)

    def run_real_chain(self, mode):
        confirmed = {**{k: self.binding[k] for k in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": mode, "frozen_requirement": {**self.frozen, "path": self.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        runner._atomic_save(self.checkpoint, state)
        calls = []
        original_publication = None

        def carrier(state, path, answer, continuing, host=None):
            nonlocal original_publication
            stage = int(state["current_stage"][-1])
            calls.append(stage)
            response = self.finish_next(stage, mode)
            self.assertEqual(response["status"], "accepted", response)
            if stage == 2:
                original_publication = copy.deepcopy(self.state()["publication"])
                self.assertEqual(original_publication["result"]["state"], "planning_published")
            result = runner._stage_projection(path, "stage" + str(stage), self.state())
            return {**result, "handoff": json.loads(result["handoff_json"])}

        with patch.object(runner, "_invoke", side_effect=carrier), redirect_stdout(io.StringIO()):
            for stage in (2, 3, 4) if mode == "stepwise" else (4,):
                self.assertEqual(runner._advance(state, self.checkpoint), 0)
                if stage < 4:
                    self.assertEqual(state["status"], "needs_input")
                    pending = state["pending_input"]
                    self.invoke("decide", {"decision_id": pending["decision_id"], "subject": pending["subject"],
                                          "answer": "confirm", "reference": "user:stage-entry"})
                    state["status"] = "active"
                    state.pop("pending_input")
        self.assertEqual(calls, [2, 3, 4])
        self.assertEqual(state["status"], "completed")
        implementation, _, _ = progress.read_implementation_delivery(
            self.checkpoint, self.state()["implementation_predecessor"])
        planning = progress.read_design_predecessor(implementation["design_predecessor"], self.checkpoint)
        self.assertEqual(planning["readiness"]["publication"], {
            "planning_commit": original_publication["request"]["planning_commit"],
            "planning_merge_commit": original_publication["result"]["merge_commit"],
            "publication_request_digest": transfer.entry.digest(original_publication["request"])})
        self.assertEqual(set(state["stage_results"]), {"stage2", "stage3", "stage4"})
        self.assertFalse(self.flow.exists())

    def test_real_runner_continuous_three_stage_chain(self):
        self.run_real_chain("continuous")

    def test_real_runner_stepwise_same_three_stage_chain(self):
        self.run_real_chain("stepwise")

    def test_runner_delivers_controller_acceptance_back_to_original_carrier(self):
        self.settings["thread_id"] = "carrier"
        self.request["host"].update(thread_id="carrier", role="scripted-carrier", source_ref="task")
        self.input = self.input_for(2)
        payload = self.prepare_design_result("stepwise")
        response = self.invoke("observe", self.observation("stopped", "result",
            {"delivery_id": "design", "status": "completed", "payload": payload}))
        pending = response["pending"]
        confirmed = {**{key: self.binding[key] for key in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": "stepwise", "registry_input": str(self.checkpoint),
            "frozen_requirement": {**self.frozen, "path": self.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        state.update(status="needs_input", pending_input=pending, sessions={"stage2": "carrier"},
                     launch={"stage": "stage2", "state": "completed_turn", "turn": 1})
        runner._atomic_save(self.checkpoint, state)
        self.settings["thread_id"] = "task"

        def resume_carrier(state, record, answer, continuing, host=None):
            self.assertTrue(continuing)
            self.assertEqual(state["sessions"]["stage2"], "carrier")
            self.settings["thread_id"] = "carrier"
            try:
                accepted = self.invoke("decide", state["controller_decision"])
                self.assertEqual(accepted["status"], "accepted", accepted)
                result = accepted["completion"]["stage_result"]
                return {**result, "handoff": json.loads(result["handoff_json"])}
            finally:
                self.settings["thread_id"] = "task"

        with patch.object(runner, "validate_confirmed", side_effect=lambda value, **kwargs: value), \
             patch.object(runner, "_check_current_registry"), \
             patch.object(runner, "_invoke", side_effect=resume_carrier) as invoked, redirect_stdout(io.StringIO()):
            # A retained carrier belongs to the live owner. Concurrent resume
            # queues the exact decision; only that owner delivers it to C.
            with runner.RunLock(self.checkpoint):
                self.assertEqual(runner.resume(self.checkpoint, "accept", decision_id=pending["decision_id"]), 0)
                self.assertEqual(invoked.call_count, 0)
                self.assertTrue(runner._live_commands(state, self.checkpoint))
                self.assertEqual(runner._advance_in_host(state, self.checkpoint, "accept", object()), 0)
        self.assertEqual(invoked.call_count, 1)
        outer = runner._read_json(self.checkpoint, "run record")
        self.assertEqual(outer["current_stage"], "stage3")
        self.assertEqual(outer["pending_input"]["kind"], "stage-entry")
        self.assertNotIn("controller_decision", outer)

    def test_raw_footer_without_saved_acceptance_cannot_advance(self):
        result = self.finish_design("continuous")["completion"]["stage_result"]
        value = {**result, "handoff": json.loads(result["handoff_json"])}
        outer = progress.read_active_owner(self.checkpoint)
        outer[progress.KEY]["status"] = "active"
        progress.Progress(self.checkpoint, outer).save()
        with self.assertRaises(runner.WorkflowError):
            runner._accepted_stage(self.checkpoint, "stage2", value, {"controller_ref": "task", "frozen_requirement": self.frozen})

    def test_receive_accept_crash_replays_same_transaction(self):
        self.begin()
        observed = self.observation()
        with patch.object(progress.Progress, "apply", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke("observe", observed)
        self.assertIsNotNone(self.state()["transaction"])
        result = self.invoke("resume")
        self.assertEqual(result["next_action"]["operation"], "wait-host")
        self.assertEqual(self.state()["dispatch"]["status"], "bound")

    def test_legacy_replacement_cannot_start_a_control_transaction(self):
        self.finish_design("continuous")
        self.context = self.context_for(3)
        self.begin("continuous", self.input_for(3, self.state()["accepted"]))
        self.invoke("observe", self.observation(ref="native:dispatcher"))
        request = {"action": "recover-dispatch", "evidence": {"stopped_refs": ["native:dispatcher"],
            "file_hashes": {}, "replacement_ref": "native:replacement"},
            "receipt": {"host": "fixture", "stopped": "native:dispatcher", "ready": "native:replacement"}}
        result = self.invoke("control", request)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.state()["control"]["context"]["carrier"]["ref"], "native:dispatcher")
        self.assertFalse(progress.Progress(self.checkpoint, progress.read_record(self.checkpoint)).unresolved_control_transactions())

    def begin_dispatcher(self):
        self.context = self.context_for(3)
        self.begin("continuous", self.input_for(3))
        self.invoke("observe", self.observation(ref="native:dispatcher"))

    def execution_input(self):
        self.settings["thread_id"] = "dispatcher-runtime"
        self.request["host"].update(thread_id="dispatcher-runtime", role="implementation-dispatcher",
                                    source_ref="task", actor_ref="native:dispatcher")
        execution = self.input_for(3)
        execution.update(role="execution-agent", configuration=transfer.configuration("execution-agent"))
        execution["authorization"]["flow_mode"] = "continuous"
        state = self.state()
        parent = state["handoff"]
        authority = state["scope_authority"]
        execution["scope"] = copy.deepcopy(parent["scope"])
        execution["scope"]["baseline"] = self.flow_git("rev-parse", "HEAD")
        execution["scope"]["downstream"] = copy.deepcopy(authority["scope"]["downstream"])
        execution["scope"]["implementation_paths"] = copy.deepcopy(authority["scope"]["implementation_paths"])
        execution["scope"]["closure_paths"] = copy.deepcopy(authority["scope"]["closure_paths"])
        execution["scope"]["owned_paths"] = ["impl.py"]
        if parent.get("readiness") is not None:
            execution.pop("inline_authority", None)
            execution["semantic"].pop("inline_plan", None)
            execution["readiness"] = progress.consumed_scope_readiness(state, self.checkpoint)
            execution["manifest_baseline"] = authority["scope"]["baseline"]
        else:
            execution["inline_authority"] = copy.deepcopy(authority)
            execution["semantic"]["inline_plan"] = copy.deepcopy(
                authority["execution_plan"]["catalog"]["tickets"][0]["plan"])
        refs = []
        for ticket in authority["execution_plan"]["catalog"]["tickets"]:
            for step in ticket["plan"]["steps"]:
                if step["target"]["path"] in execution["scope"]["owned_paths"]:
                    refs.append(execution_plan.step_ref(ticket, step))
        execution["semantic"].update(plan_refs=refs, authority_revision=authority["revision"],
            authority_digest=transfer.entry.digest(authority),
            plan_digest=authority["execution_plan"]["plan_digest"])
        execution["authorization"]["scope_digest"] = transfer.entry.digest(execution["scope"])
        return execution

    def prepare_execution(self):
        self.begin_dispatcher()
        execution = self.execution_input()
        prepared = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "prepare", "handoff": execution})
        self.assertEqual(prepared["next_action"]["operation"], "invoke-host", prepared)
        return execution

    def test_suspend_blocks_new_allocations_in_all_stop_states(self):
        for operation, stopped in (("pause", False), ("pause", True), ("cancel", False), ("cancel", True)):
            with self.subTest(operation=operation, stopped=stopped):
                self.checkpoint = self.checkpoint.with_name('checkpoint-' + operation + '-' + str(stopped) + '.json')
                self.settings["thread_id"] = "task"
                self.request["host"].update(thread_id="task", role="controller", source_ref=None)
                self.request["host"].pop("actor_ref", None)
                self.begin_dispatcher()
                self.invoke(operation)
                if stopped:
                    self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher"))
                before = self.state()
                execution = self.execution_input()
                for identity in ("first-new", "different-new"):
                    with self.assertRaises(transfer.entry.PreparationError) as error:
                        self.invoke("allocation", {"allocation_id": identity, "operation": "prepare", "handoff": execution})
                    self.assertEqual(error.exception.code, "progression_suspended")
                    self.assertEqual(self.state(), before)

    def test_suspend_blocks_prepared_but_unissued_allocation_and_allows_lookup(self):
        for operation in ("pause", "cancel"):
            with self.subTest(operation=operation):
                self.checkpoint = self.checkpoint.with_name('checkpoint-' + operation + '.json')
                self.settings["thread_id"] = "task"
                self.request["host"].update(thread_id="task", role="controller", source_ref=None)
                self.request["host"].pop("actor_ref", None)
                self.begin_dispatcher()
                execution = self.execution_input()
                request = {"allocation_id": "prepared", "operation": "prepare", "handoff": execution}
                with patch.object(progress.Progress, "issue_allocation", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt): self.invoke("allocation", request)
                self.invoke(operation)
                before = self.state()
                with self.assertRaises(transfer.entry.PreparationError) as error:
                    self.invoke("allocation", request)
                self.assertEqual(error.exception.code, "progression_suspended")
                self.assertEqual(self.state(), before)
                owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
                with self.assertRaises(transfer.entry.PreparationError) as error:
                    owner.issue_allocation("prepared", self.allocation_slot("prepared"))
                self.assertEqual(error.exception.code, "progression_suspended")
                self.assertEqual(self.state(), before)
                slot = self.allocation_slot('prepared')
                result = self.invoke("allocation", {"allocation_id": "prepared", "operation": "reconcile",
                    "receipt": self.receipt(slot["record"], "not-created", "lookup", ref=None)})
                self.assertEqual(result["next_action"]["result"]["status"], "not-created")

    def test_saved_runner_stop_request_blocks_allocation_before_core_suspend(self):
        self.begin_dispatcher()
        execution = self.execution_input()
        for operation in ("pause", "cancel"):
            outer = runner._read_json(self.checkpoint, "run record")
            outer["runner_request"] = {"operation": operation, "request_id": operation}
            store = progress.state_store.StateStore(self.checkpoint)
            original = store.open_current()
            progress.save_outer_record(self.checkpoint,
                {key: value for key, value in outer.items() if key != progress.KEY},
                expected_revision=outer.authority_revision)
            current = store.open_current()
            self.assertEqual(current.root["subjects"], original.root["subjects"])
            self.assertEqual(current.root["metadata"], original.root["metadata"])
            before = self.checkpoint.read_bytes()
            with self.assertRaises(transfer.entry.PreparationError) as error:
                self.invoke("allocation", {"allocation_id": operation, "operation": "prepare", "handoff": execution})
            self.assertEqual(error.exception.code, "progression_suspended")
            self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_pausing_still_receives_and_accepts_existing_allocation(self):
        self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        self.invoke("allocation", {"allocation_id": "slice-one", "operation": "bind",
            "receipt": self.receipt(slot["record"], ref="native:executor")})
        self.invoke("pause")
        data = b"print('finished before stopping')\n"
        (self.flow / "impl.py").write_bytes(data)
        slot = self.allocation_slot('slice-one')
        received = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "receive",
            "receipt": self.receipt(slot["record"], "stopped", "result", ref="native:executor"),
            "result": {"delivery_id": "stopped-slice", "status": "completed", "payload": {"changed_paths": ["impl.py"],
                "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["fixture boundary passed"],
                "write_release": transfer.dispatch.execution_release_token(slot["record"]),
                "plan_refs": slot["handoff"]["semantic"]["plan_refs"],
                "plan_authority": self.state()["control"]["context"]["handoff_progress"]["plan_authority"]}}})
        self.assertEqual(received["next_action"]["result"]["status"], "received")
        accepted = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "accept", "decision": {"reference": "dispatcher:accept-stopped"}})
        self.assertEqual(accepted["status"], "pausing")
        self.assertEqual(accepted["next_action"]["result"]["status"], "accepted")
        self.assertIsNone(self.state()["accepted"])

    def test_native_allocations_share_parent_authority_and_accept_real_delta(self):
        execution = self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        repeated = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "prepare", "handoff": execution})
        self.assertEqual(repeated["next_action"]["operation"], "lookup-exact-allocation")
        self.assertEqual(len(self.state()["control"]["context"]["handoff_progress"]["executions"]), 1)
        self.invoke("allocation", {"allocation_id": "slice-one", "operation": "bind",
            "receipt": self.receipt(slot["record"], ref="native:executor")})
        data = b"print('slice')\n"
        (self.flow / "impl.py").write_bytes(data)
        slot = self.allocation_slot('slice-one')
        received = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "receive",
            "receipt": self.receipt(slot["record"], "stopped", "result", ref="native:executor"),
            "result": {"delivery_id": "slice", "status": "completed", "payload": {"changed_paths": ["impl.py"],
                "file_hashes": {"impl.py": hashlib.sha256(data).hexdigest()}, "tests": ["fixture boundary passed"],
                "write_release": transfer.dispatch.execution_release_token(slot["record"]),
                "plan_refs": slot["handoff"]["semantic"]["plan_refs"],
                "plan_authority": self.state()["control"]["context"]["handoff_progress"]["plan_authority"]}}})
        self.assertEqual(received["next_action"]["result"]["status"], "received", received)
        accepted = self.invoke("allocation", {"allocation_id": "slice-one", "operation": "accept", "decision": {"reference": "dispatcher:accept"}})
        self.assertFalse(accepted["downstream_ready"])
        self.assertIsNone(self.state()["accepted"])
        self.assertEqual(self.state()["dispatch"]["request"]["role"], "implementation-dispatcher")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["executions"][0]["state"], "accepted")

    def test_public_control_cannot_bypass_execution_receipts(self):
        self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        self.invoke("allocation", {"allocation_id": "slice-one", "operation": "bind",
            "receipt": self.receipt(slot["record"], ref="native:executor")})
        before = self.state()["control"]["context"]
        for action in ("execution-dispatch-result", "execution-result", "accept-execution"):
            with self.subTest(action=action):
                blocked = self.invoke("control", {"action": action, "evidence": {},
                    "receipt": {"host": "fixture"}})
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(self.state()["error"]["code"], "invalid_operation")
                self.assertEqual(self.state()["control"]["context"], before)

    def test_cancelled_parent_reconciles_exact_unbound_allocation(self):
        self.prepare_execution()
        self.invoke("cancel")
        self.invoke("observe", self.observation("stopped", "result", ref="native:dispatcher"))
        slot = self.allocation_slot('slice-one')
        data = {"allocation_id": "slice-one", "operation": "reconcile",
                "receipt": self.receipt(slot["record"], "not-created", "lookup", ref=None)}
        released = self.invoke("allocation", data)
        self.assertEqual(released["status"], "cancelled", released)
        self.assertTrue(self.invoke("allocation", data)["acknowledged"])
        late = {"allocation_id": "slice-one", "operation": "reconcile",
                "receipt": self.receipt(slot["record"], "ready", "lookup", ref="late:executor")}
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("allocation", late)
        self.assertEqual(self.state()["status"], "cancelled")

    def test_stale_revision_and_answer_do_not_launch(self):
        self.begin()
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("advance", revision=0)
        calls = self.state()["action"]
        result = self.invoke("decide", {"decision_id": "old", "subject": {}, "answer": "accept", "reference": "old"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.state()["action"], calls)

    def test_requirement_write_freeze_partial_commit_and_exact_recovery(self):
        request = copy.deepcopy(self.request)
        request.update(stage=1, source={"kind": "stage1", "path": "docs/requirements/c.md"},
                       target={"kind": "planning", "repository": str(self.flow), "branch": self.binding["branch"]})
        request = self.with_registration(request, project=self.flow, receipt='host:requirement-source-registry')
        write = {"protocol": transfer.requirement.PROTOCOL, "operation": "prepare", "entry": request,
            "purpose": "write", "path": "docs/requirements/c.md", "version": 1, "authorization": "user:write", "content": "new requirement\n"}
        response = self.invoke("prepare-requirement", {"request": write})
        self.assertEqual(response["status"], "requirement-ready", response)
        modified = (self.flow / write["path"]).stat().st_mtime_ns
        repeated = self.invoke("prepare-requirement", {"request": write})["result"]
        self.assertEqual({k: repeated[k] for k in ("path", "sha256", "version")},
                         {k: response["result"][k] for k in ("path", "sha256", "version")})
        self.assertEqual((self.flow / write["path"]).stat().st_mtime_ns, modified)
        freeze = {k: v for k, v in write.items() if k != "content"}
        freeze.update(purpose="freeze", authorization="user:freeze", previous=response["result"])
        hook = self.root / ".git/hooks/post-commit"
        hook.write_text("#!/bin/sh\nprintf drift > docs/requirements/c.md\n")
        hook.chmod(0o755)
        before = int(self.flow_git("rev-list", "--count", "HEAD"))
        response = self.invoke("prepare-requirement", {"request": freeze})
        self.assertEqual(response["status"], "blocked", response)
        self.assertFalse(response["downstream_ready"])
        self.assertEqual(len(response["error"]["completed_evidence"]), 1)
        committed = self.flow_git("rev-parse", "HEAD")
        self.assertEqual(self.invoke("prepare-requirement", {"request": freeze})["status"], "blocked")
        (self.flow / "docs/requirements/c.md").write_text("new requirement\n")
        recovered = self.invoke("prepare-requirement", {"request": freeze})
        self.assertEqual(recovered["result"]["commit"], committed)
        self.assertEqual(int(self.flow_git("rev-list", "--count", "HEAD")), before + 1)

    def test_published_merge_cleanup_failure_never_republishes(self):
        self.finish_design("continuous")
        self.finish_next(3, "continuous")
        predecessor = self.state()["accepted"]
        self.context = self.context_for(4)
        self.begin("continuous", self.input_for(4, predecessor))
        self.invoke("observe", self.observation(ref="native:closure"))
        candidate = predecessor["payload"]["candidate_commit"]
        request = {"candidate_commit": candidate, "expected_target_head": self.git("rev-parse", "HEAD"), "reference": "controller:close"}
        self.ready_publication(candidate, "controller:close", "native:closure")
        original = transfer.supervision._run_git

        def fail_cleanup(repository, arguments, **kwargs):
            if arguments[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(arguments, 1, "", "fixture cleanup failure")
            return original(repository, arguments, **kwargs)

        with patch.object(transfer.supervision, "_run_git", side_effect=fail_cleanup):
            response = self.invoke("publication", request)
        self.assertEqual(response["status"], "blocked")
        merged = self.git("rev-parse", "HEAD")
        self.assertNotEqual(merged, request["expected_target_head"])
        with patch.object(transfer.supervision, "complete_worktree", side_effect=AssertionError("must not republish")):
            self.assertEqual(self.invoke("publication", request)["next_action"]["operation"], "reconcile-publication")
        receipt = self.observation("stopped", "result", ref="native:closure")
        receipt["closure"] = {"ref": "native:closure", "candidate": candidate, "merge": merged, "ancestor_verified": True,
            "changed_paths": ["impl.py"], "binding": self.binding, "checks": ["published"],
            "worktree_removed": False, "branch_removed": False, "disposition": "deleted", "disposition_verified": False, "implementation_problem": None}
        self.invoke("observe", receipt)
        self.assertEqual(self.state()["closure_effects"][0]["operation"], "cleanup-only")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["merge"], merged)
        self.assertEqual(self.git("rev-parse", "HEAD"), merged)
        late_problem = self.observation("stopped", "result", ref="native:closure")
        late_problem["closure"] = {**receipt["closure"], "implementation_problem": "needs implementation changes"}
        self.assertEqual(self.invoke("observe", late_problem)["error"]["code"], "already_published")
        self.assertEqual(self.state()["control"]["context"]["handoff_progress"]["merge"], merged)

    def test_prepublication_implementation_problem_returns_exact_original_role(self):
        self.finish_design("continuous")
        self.finish_next(3, "continuous")
        predecessor = self.state()["accepted"]
        self.context = self.context_for(4)
        self.begin("continuous", self.input_for(4, predecessor))
        self.invoke("observe", self.observation(ref="native:closure"))
        observed = self.observation("stopped", "result", ref="native:closure")
        observed["closure"] = {"ref": "native:closure", "candidate": predecessor["payload"]["candidate_commit"],
            "merge": None, "ancestor_verified": False, "changed_paths": [], "binding": self.binding,
            "checks": [], "worktree_removed": False, "branch_removed": False, "disposition": "deleted", "disposition_verified": False,
            "implementation_problem": "accepted behavior needs controller recovery"}
        result = self.invoke("observe", observed)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["next_action"]["operation"], "implementation-recovery")
        self.assertEqual(result["next_action"]["ref"], "native:dispatcher")
        self.assertEqual(self.invoke("resume")["next_action"], result["next_action"])

    def test_final_recovery_preserves_acceptance_and_revalidates_replacement(self):
        original_invoke = self.invoke
        checked = [False]
        def reject_unowned_review(operation, data=None, revision=None):
            if (not checked[0] and operation == 'review-activity' and data.get('operation') == 'prepare' and
                    self.state().get('final_integration_recovery') is not None):
                checked[0] = True
                candidate, before = data['candidate'], self.checkpoint.read_bytes()
                for relative in ('unowned-review.py', 'docs/requirements/a.md'):
                    try:
                        (self.flow / relative).write_bytes(b'outside original review authority\n')
                        self.flow_git('add', relative)
                        self.flow_git('commit', '-qm', 'unauthorized review input fixture')
                        bad = copy.deepcopy(data)
                        bad['candidate'] = self.flow_git('rev-parse', 'HEAD')
                        for check in bad['verification']['checks']:
                            check.update(start_commit=bad['candidate'], end_commit=bad['candidate'])
                        raw = bad['verification']['source']['raw']
                        raw.update(candidate=bad['candidate'], checks=copy.deepcopy(bad['verification']['checks']))
                        raw['fixture']['candidate'] = bad['candidate']
                        with self.assertRaises(progress.entry.PreparationError) as denied:
                            original_invoke(operation, bad, revision)
                        if relative == 'unowned-review.py':
                            self.assertEqual(denied.exception.code, 'scope_changed')
                        else:
                            self.assertIn(denied.exception.code, {'scope_changed', 'source_changed'})
                        self.assertEqual(self.checkpoint.read_bytes(), before)
                    finally:
                        self.flow_git('reset', '--hard', candidate)
            return original_invoke(operation, data, revision)
        original_freeze = progress.freeze_implementation_native_owners
        inspected = [False]
        def verify_closure_subset(state, store, current, prior):
            if state.get('final_integration_recovery') is not None and not inspected[0]:
                inspected[0] = True
                closure = state['final_integration_recovery']['closure_handoff']
                for label in ('foreign-run', 'wrong-ref', 'revoked-stop'):
                    rejected = copy.deepcopy(state)
                    changed = copy.deepcopy(closure)
                    if label == 'foreign-run':
                        changed['stop_ref']['run_id'] = 'different-run'
                    elif label == 'wrong-ref':
                        changed['role_ref'] = 'unregistered-closure'
                    else:
                        rejected.setdefault('native_adverse', {})[changed['role_ref']] = {'status': 'running'}
                    if label != 'revoked-stop':
                        changed = progress.handoff.seal({key: value for key, value in changed.items() if key != 'digest'})
                        rejected['final_integration_recovery']['closure_handoff'] = changed
                        rejected['handoff']['authorization']['integration_recovery']['closure'] = copy.deepcopy(changed)
                    with self.subTest(label=label), store.measure_writes(sequence=True):
                        with self.assertRaises((progress.entry.PreparationError, progress.control.ControlError)):
                            original_freeze(rejected, store, current, prior)
                owner = progress.Progress(self.checkpoint, {**current.root['outer'], progress.KEY: state})
                with self.assertRaises(progress.entry.PreparationError) as no_grant:
                    owner.finalization_owner(closure['role_ref'])
                self.assertEqual(no_grant.exception.code, 'result_incomplete')
            result = original_freeze(state, store, current, prior)
            if state.get('final_integration_recovery') is not None:
                self.assertNotIn(state['final_integration_recovery']['closure_handoff']['role_ref'], result)
            return result
        with patch.object(self, 'invoke', reject_unowned_review), \
                patch.object(progress, 'freeze_implementation_native_owners', verify_closure_subset):
            self.final_recovery_chain()
        self.assertTrue(checked[0])

    def test_final_recovery_preserves_committed_closure_documents(self):
        original_invoke = self.invoke
        checked = [False]
        def verify_original_actor(operation, data=None, revision=None):
            if (not checked[0] and operation == 'review-activity' and data.get('operation') == 'prepare' and
                    data['actor_ref'] != self.state()['control']['context']['controller_ref']):
                checked[0] = True
                before = self.checkpoint.read_bytes()
                for actor in (self.state()['control']['context']['controller_ref'], 'forged-originating-task'):
                    with self.assertRaises(progress.entry.PreparationError) as denied:
                        original_invoke(operation, {**data, 'actor_ref': actor}, revision)
                    self.assertEqual(denied.exception.code, 'identity_mismatch')
                    self.assertEqual(self.checkpoint.read_bytes(), before)
                prepared = original_invoke(operation, data, revision)
                replay = original_invoke(operation, data)
                self.assertTrue(replay['acknowledged'])
                self.assertEqual(replay['next_action']['operation'], 'lookup-exact-review')
                self.assertEqual(replay['next_action']['action_id'], prepared['next_action']['action_id'])
                return prepared
            return original_invoke(operation, data, revision)
        with patch.object(self, 'invoke', verify_original_actor):
            self.final_recovery_chain(retain_docs=True)
        self.assertTrue(checked[0])

    def test_final_recovery_rejects_reused_historical_host_response(self):
        self.final_recovery_chain(replay_host=True)

    def test_final_recovery_after_target_advances_during_publication(self):
        original_invoke = self.invoke
        checked = [False]
        def verify_original_recovery_source(operation, data=None, revision=None):
            if operation == 'implementation-recovery' and data.get('reference') == 'controller:final-recovery' and not checked[0]:
                checked[0] = True
                facts, current, store = progress.read_implementation_delivery(
                    self.checkpoint, self.state()['handoff']['predecessor'])
                reference = facts['recovery_ref']
                recovered = store.read_evidence(reference, run_id=current.run_id)['value']
                self.assertEqual(recovered['plan_revisions'], [])
                path = store._path(reference['sha256'])
                original, before = path.read_bytes(), self.checkpoint.read_bytes()
                for absent in (False, True):
                    try:
                        if absent:
                            path.unlink()
                        else:
                            path.write_bytes(b'corrupt original recovery record')
                        with progress.state_store.operation_store(self.checkpoint) as measured:
                            owner = progress.Progress(self.checkpoint, progress.read_active_owner(self.checkpoint))
                            with measured.measure_writes(sequence=True):
                                with self.assertRaises(progress.entry.PreparationError) as rejected:
                                    owner.implementation_recovery(data)
                        self.assertEqual(rejected.exception.code,
                                         'state_object_missing' if absent else 'state_object_corrupt')
                        self.assertEqual(self.checkpoint.read_bytes(), before)
                    finally:
                        path.write_bytes(original)
            return original_invoke(operation, data, revision)
        with patch.object(self, 'invoke', verify_original_recovery_source):
            self.final_recovery_chain(publication_race=True)
        self.assertTrue(checked[0])

    def execution_transfer_request(self):
        parent = self.state()['handoff']
        authority = self.state()['scope_authority']
        request = {key: copy.deepcopy(value) for key, value in parent.items()
                   if key in transfer.handoff.FIELDS | transfer.handoff.OPTIONAL_FIELDS}
        request.update(operation='prepare', role='execution-agent', predecessor=None,
                       configuration=transfer.configuration('execution-agent'))
        request['scope']['baseline'] = self.flow_git('rev-parse', 'HEAD')
        request['scope']['downstream'] = copy.deepcopy(authority['scope']['downstream'])
        request['scope']['implementation_paths'] = copy.deepcopy(authority['scope']['implementation_paths'])
        request['scope']['closure_paths'] = copy.deepcopy(authority['scope']['closure_paths'])
        request['scope']['owned_paths'] = copy.deepcopy(authority['scope']['implementation_paths'])
        if parent.get('readiness') is not None:
            request['readiness'] = progress.consumed_scope_readiness(self.state(), self.checkpoint)
            request['manifest_baseline'] = authority['scope']['baseline']
        else:
            request['inline_authority'] = copy.deepcopy(authority)
            request['semantic']['inline_plan'] = copy.deepcopy(authority['execution_plan']['catalog']['tickets'][0]['plan'])
        refs = []
        for ticket in authority['execution_plan']['catalog']['tickets']:
            for step in ticket['plan']['steps']:
                if step['target']['path'] in request['scope']['owned_paths']:
                    refs.append(execution_plan.step_ref(ticket, step))
        request['semantic'].update(plan_refs=refs, authority_revision=authority['revision'],
            authority_digest=transfer.entry.digest(authority),
            plan_digest=authority['execution_plan']['plan_digest'])
        request['authorization']['scope_digest'] = transfer.entry.digest(request['scope'])
        request['semantic'].pop('validation_plan', None)
        request['semantic'].pop('implementation_policy', None)
        return request

    def inherited_execution_input(self):
        parent = self.state()['handoff']
        request = self.execution_transfer_request()
        request['entry']['host'].update(thread_id=self.settings['thread_id'], role='implementation-dispatcher',
            source_ref=parent['controller_ref'], actor_ref=self.state()['control']['context']['carrier']['ref'])
        request['expected_entry'] = transfer.entry.resolve(request['entry'])
        return request

    def revalidate_integrated_execution(self):
        parent = self.state()['handoff']
        source = self.state()['control']['context']['handoff_progress']['integration_source']
        dispatcher = self.state()['control']['context']['carrier']['ref']
        executor = dispatcher + '/integrated_executor' if dispatcher.startswith('/root/') else 'native:integrated-executor'
        before = progress.control_git.snapshot(self.flow)
        with patch.dict(self.settings, thread_id='dispatcher-runtime'):
            request = self.inherited_execution_input()
            request['scope']['owned_paths'] = copy.deepcopy(source['pending_paths'])
            request['authorization']['scope_digest'] = transfer.entry.digest(request['scope'])
            request['semantic'].update(adopt_paths=copy.deepcopy(source['pending_paths']),
                                       adoption_snapshot_digest=source['snapshot_digest'])
            checkpoint = self.checkpoint.read_bytes()
            for field in ('controller_ref', 'requirement_identity'):
                with self.subTest(caller_field=field), self.assertRaises(transfer.entry.PreparationError) as error:
                    transfer.handoff.prepare({**request, field: parent[field]})
                self.assertEqual(error.exception.code, 'invalid_request')
            internal = {**request, 'controller_ref': parent['controller_ref'],
                        'requirement_identity': parent['requirement_identity']}
            for field, value in (('controller_ref', 'other-controller'),
                                 ('requirement_identity', {**parent['requirement_identity'], 'sha256': '0' * 64}),
                                 ('binding', {**parent['binding'], 'branch': 'codex/other-flow'}),
                                 ('scope', {**request['scope'], 'implementation_paths': ['other.py']})):
                with self.subTest(authority_field=field), self.assertRaises(transfer.entry.PreparationError) as error:
                    transfer.handoff.phase_evidence({**internal, field: value})
                self.assertEqual(error.exception.code, 'authority_changed')
            self.assertEqual(self.checkpoint.read_bytes(), checkpoint)
            prepared = self.invoke('allocation', {'allocation_id': 'integrated-execution',
                'operation': 'prepare', 'handoff': request})
            self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)
            slot = self.allocation_slot('integrated-execution')
            self.assertEqual(slot['handoff']['controller_ref'], parent['controller_ref'])
            self.assertEqual(slot['handoff']['requirement_identity'], parent['requirement_identity'])
            bound = self.invoke('allocation', {'allocation_id': 'integrated-execution', 'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref=executor)})
            self.assertIsNotNone(bound['next_action'], bound)
            self.assertEqual(bound['next_action']['result']['status'], 'bound', bound)
            slot = self.allocation_slot('integrated-execution')
            self.invoke('allocation', {'allocation_id': 'integrated-execution', 'operation': 'receive',
                'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=executor),
                'result': {'delivery_id': 'integrated-execution', 'status': 'completed', 'payload': {
                    'changed_paths': [], 'adopted_paths': source['pending_paths'],
                    'file_hashes': {path: hashlib.sha256((self.flow / path).read_bytes()).hexdigest()
                                   for path in source['pending_paths']},
                    'tests': ['unchanged integrated implementation bytes verified'],
                    'write_release': transfer.dispatch.execution_release_token(slot['record']),
                    'plan_refs': transfer.dispatch.dispatch_input(slot['record'])['semantic']['plan_refs'],
                    'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
            accepted = self.invoke('allocation', {'allocation_id': 'integrated-execution', 'operation': 'accept',
                'decision': {'reference': 'dispatcher:accept-integrated-execution'}})
            self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
            if parent.get('readiness') is None:
                current = self.state()
                completed = current['allocations']['integrated-execution']
                self.assertIsNone(completed['transaction'])
                self.assertEqual(transfer.dispatch.record_for({
                    'record': completed['record'], 'control': current['control']}),
                    transfer.handoff.unseal(completed['record']))
                ready = next(receipt for receipt in completed['record']['receipts']
                             if receipt['status'] == 'ready')
                with self.assertRaises(transfer.entry.PreparationError) as error:
                    transfer.dispatch.handle({'protocol': transfer.handoff.PROTOCOL,
                        'operation': 'bind', 'record': completed['record'],
                        'control': current['control'], 'receipt': ready,
                        'scope_checkpoint_path': str(self.checkpoint.resolve())})
                self.assertEqual(error.exception.code, 'authority_missing')
        self.assertEqual(progress.control_git.snapshot(self.flow), before)

    def staged_successor(self, accepted):
        successor = self.input_for(3, self.state()['accepted'])
        scope = accepted['readiness']['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            successor['scope'][key] = copy.deepcopy(scope[key])
        successor['scope']['owned_paths'] = copy.deepcopy(scope['implementation_paths'])
        successor['scope']['protected_paths'] = sorted(set(scope['protected_paths'] +
            self.state()['accepted']['payload']['planning_paths']))
        successor['authorization']['scope_digest'] = transfer.entry.digest(successor['scope'])
        return successor


    def accepted_implementation_for_closure(self, design=None, *, standalone=False):
        design = None if standalone else design or self.finish_design('continuous')
        self.context = self.context_for(3)
        self.begin('continuous', self.input_for(3) if standalone else self.staged_successor(design))
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        actor_settings = self.settings['thread_id']
        actor_host = copy.deepcopy(self.request['host'])
        execution = self.execution_input()
        prepared = self.invoke('allocation', {'allocation_id': 'closure-slice',
            'operation': 'prepare', 'handoff': execution})
        self.assertEqual(prepared['next_action']['operation'], 'invoke-host', prepared)
        slot = self.allocation_slot('closure-slice')
        receipt = self.receipt(slot['record'], ref='native:executor')
        receipt['raw']['output'] = 'original allocation report ' * 4000
        bound = self.invoke('allocation', {'allocation_id': 'closure-slice', 'operation': 'bind',
            'receipt': receipt})
        content = b"print('accepted implementation')\n"
        (self.flow / 'impl.py').write_bytes(content)
        slot = self.allocation_slot('closure-slice')
        received = self.invoke('allocation', {'allocation_id': 'closure-slice', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
            'result': {'delivery_id': 'closure-slice-result', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                'tests': ['implementation behavior passed'],
                'write_release': bound['next_action']['result']['write_release'],
                'plan_refs': execution['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.assertEqual(received['next_action']['result']['status'], 'received', received)
        accepted = self.invoke('allocation', {'allocation_id': 'closure-slice', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:closure-slice'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted', accepted)
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'accepted implementation')
        candidate = self.flow_git('rev-parse', 'HEAD')
        self.settings['thread_id'] = actor_settings
        self.request['host'] = actor_host
        result = self.accept_current({'artifacts': ['impl.py'], 'checks': ['implementation behavior passed'],
            'candidate_commit': candidate, 'review': {axis: {'reviewer_ref': axis}
                for axis in ('standards', 'spec')}}, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(result['status'], 'accepted', result)
        return result


    def runner_stage2_fixture(self):
        package_root = Path(__file__).resolve().parents[2] / 'skills'
        registry = {'source': 'host-current-skills', 'entries': [
            {'name': name, 'entry': str(package_root / name / 'SKILL.md'),
             'source': 'fixture-host', 'enabled': True}
            for name in ('solution-design', 'guided-implementation', 'change-closure')]}
        roots = [self.binding[key] for key in ('repository', 'worktree', 'git_common_dir')]
        permissions = {'approvalPolicy': 'never', 'sandbox': 'danger-full-access',
                       'runtimeWorkspaceRoots': roots}
        confirmed = {**{key: self.binding[key] for key in ('repository', 'worktree', 'git_common_dir', 'target_branch')},
            'controller_ref': 'task', 'flow_mode': 'stepwise', 'run_record': str(self.checkpoint),
            'frozen_requirement': {key: self.frozen[key] for key in ('commit', 'sha256')},
            'requirement': self.frozen, 'authority_scope': {'allowed_paths': ['docs']},
            'registry': registry, 'registry_context': {'project_path': str(self.root),
                'project_id': 'project', 'controller_ref': 'task', 'receipt': 'host:registry',
                'registry_digest': transfer.entry.digest(registry)},
            'stages': {stage: {'model': 'fixture-model', 'reasoning_effort': 'high',
                'selection_input': transfer.configuration('scripted-carrier')} for stage in runner.STAGES},
            'host': {'transport': 'app-server-stdio', 'cli_version': 'fixture',
                'source': {'kind': 'controller-current-config', 'controller_ref': 'task', 'receipt': 'host:config'},
                'thread': {**permissions, 'config': {}}, 'effective': permissions}}
        confirmed['frozen_requirement']['path'] = self.frozen['absolute_path']
        confirmed_path = self.checkpoint.with_name('confirmed.json')
        confirmed_path.write_text(json.dumps(confirmed))
        self.settings['thread_id'] = 'carrier'
        self.request['host'].update(thread_id='carrier', role='scripted-carrier', source_ref='task')
        self.input = self.input_for(2)
        return package_root, confirmed_path


    def final_recovery_chain(self, retain_docs=False, replay_host=False, publication_race=False):
        if retain_docs:
            return self.final_recovery_runner()
        self.accepted_implementation_for_closure()
        original = copy.deepcopy(self.state())
        recovery_input, failed_publication = self.prepare_final_recovery(original, publication_race=publication_race)
        self.recover_final_integration(original, recovery_input, failed_publication, replay_host=replay_host)
        if replay_host:
            return
        self.revalidate_final_recovery(original)
        self.assert_final_recovery_completed(self.finish_next(4, 'continuous'))

    def prepare_final_recovery(self, original, *, retain_docs=False, publication_race=False):
        predecessor = original['accepted']
        failed_publication = None
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('implementation-recovery', {'reference':'unauthorized', 'closure_digest':'a'*64})
        self.assertEqual(self.checkpoint.read_bytes(), before)
        def advance_target():
            (self.root / 'other-task.md').write_text('parallel planning\n')
            self.git('add', 'other-task.md'); self.git('commit', '-qm', 'parallel task')

        if not publication_race:
            advance_target()
        self.context = self.context_for(4)
        self.begin('continuous', self.input_for(4, predecessor))
        self.invoke('observe', self.observation(ref='native:closure'))
        if retain_docs:
            (self.flow / 'CHANGELOG.md').write_text('closure notes\n')
            self.flow_git('add', 'CHANGELOG.md'); self.flow_git('commit', '-qm', 'closure documentation')
        if publication_race:
            candidate = self.flow_git('rev-parse', 'HEAD')
            request = {'candidate_commit':candidate, 'expected_target_head':self.git('rev-parse', 'HEAD'),
                       'reference':'controller:close'}
            self.ready_publication(candidate, request['reference'], 'native:closure')
            publisher = transfer.supervision.complete_worktree
            def race_before_publish(*args, **kwargs):
                advance_target()
                return publisher(*args, **kwargs)
            with patch.object(transfer.supervision, 'complete_worktree', side_effect=race_before_publish):
                failed = self.invoke('publication', request)
            self.assertEqual(failed['error']['code'], 'target_changed', failed)
            failed_publication = copy.deepcopy(self.state()['publication'])
            self.assertEqual([fact['event'] for fact in failed_publication['facts']], ['prepared'])
            self.assertIsNone(failed_publication['result'])
        observed = self.observation('stopped', 'result', ref='native:closure')
        observed['closure'] = {'ref':'native:closure', 'candidate':predecessor['payload']['candidate_commit'],
            'merge':None, 'ancestor_verified':False, 'changed_paths':[], 'binding':self.binding,
            'checks':[], 'worktree_removed':False, 'branch_removed':False, 'disposition':'deleted',
            'disposition_verified':False, 'implementation_problem':'target advanced at final publication'}
        self.assertEqual(self.invoke('observe', observed)['next_action']['operation'], 'implementation-recovery')
        recovery_input = {'reference':'controller:final-recovery', 'closure_digest':self.state()['handoff']['digest']}
        return recovery_input, failed_publication

    def recover_final_integration(self, original, recovery_input, failed_publication=None, *, replay_host=False):
        predecessor = original['accepted']
        recovered = self.invoke('implementation-recovery', recovery_input)
        self.assertEqual(self.state()['stage'], 3, recovered)
        self.assertIsNone(self.state()['accepted'])
        retained, current, store = progress.read_implementation_delivery(
            self.checkpoint, self.state()['recovery_predecessor'])
        self.assertEqual(retained['accepted']['digest'], predecessor['digest'])
        self.assertEqual(retained['payload']['candidate_commit'], predecessor['payload']['candidate_commit'])
        self.assertEqual(retained['dispatcher'], {key: original['control']['context']['carrier'][key]
            for key in ('ref', 'attempt')})
        self.assertEqual(retained['execution_authority'], original['scope_authority'])
        self.assertEqual(self.state()['history'], [])
        if failed_publication is not None:
            closure = self.state()['final_integration_recovery']['closure_handoff']
            self.assertIn('publication_ref', closure)
            audit = progress.read_recovery_publication(self.checkpoint, closure, include_outcome=True)
            self.assertEqual(audit['transaction'], {key: failed_publication[key]
                for key in ('operation', 'request', 'facts')})
            self.assertEqual(audit['result'], failed_publication['result'])
            self.assertEqual(audit['error'], failed_publication['error'])
            self.assertEqual(audit['observation']['state'], 'not-published')
            self.assertNotIn('publication', self.state())
            before = self.checkpoint.read_bytes()
            observation = self.invoke('reconcile-publication')
            self.assertEqual(observation['next_action']['result']['state'], 'not-published')
            self.assertEqual(self.checkpoint.read_bytes(), before)
            with self.assertRaises(transfer.supervision.ProtocolError):
                transfer.supervision.reconcile_publication(audit['transaction'], resume=True)
            self.assertEqual(self.checkpoint.read_bytes(), before)
            proof = store.read_evidence(closure['publication_ref'], run_id=current.run_id)['value']
            error_ref = proof['error_ref']
            directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
            def object_path(reference):
                digest = reference['sha256']
                return directory / 'objects' / digest[:2] / digest[2:4] / digest
            excluded = {object_path(closure['publication_ref']), object_path(proof['transaction_ref']),
                        object_path(error_ref)}
            opened = []
            original_open = Path.open
            def tracked_open(path, *args, **kwargs):
                opened.append(path)
                return original_open(path, *args, **kwargs)
            with patch.object(Path, 'open', tracked_open):
                self.assertEqual(self.invoke('inspect')['status'], 'active')
            self.assertFalse(excluded & set(opened))
            raw_error = object_path(error_ref).read_bytes()
            try:
                object_path(error_ref).write_bytes(b'corrupted original publication error')
                # Ordinary recovery and transaction inspection do not hydrate an old error.
                self.assertEqual(self.invoke('inspect')['status'], 'active')
                self.assertEqual(self.invoke('reconcile-publication')['next_action']['result']['state'], 'not-published')
                with self.assertRaises(transfer.entry.PreparationError) as corrupted:
                    progress.read_recovery_publication(self.checkpoint, closure, include_outcome=True)
                self.assertEqual(corrupted.exception.code, 'state_object_corrupt')
            finally:
                object_path(error_ref).write_bytes(raw_error)
        replay = self.invoke('implementation-recovery', recovery_input)
        self.assertTrue(replay['acknowledged'], replay)
        if replay_host:
            forged = self.observation('idle', 'result', ref='native:dispatcher')
            forged['provenance']['response_ref'] = original['host']['last_stop']['provenance']['response_ref']
            rejected = self.invoke('observe', forged)
            self.assertEqual(rejected['error']['code'], 'host_provenance_conflict')
            self.assertIsNone(self.state()['accepted'])
            return

    def revalidate_final_recovery(self, original):
        predecessor = original['accepted']
        continuation = self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        self.assertEqual(continuation['next_action']['operation'], 'continue-host', continuation)
        self.flow_git('merge', '--no-edit', 'main')
        replacement = self.flow_git('rev-parse', 'HEAD')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        captured = self.invoke('control', {'action':'capture-integration', 'evidence':{'reference':'controller:merged'},
            'receipt':{'controller_ref':'task', 'reference':'controller:merged'}})
        self.assertNotEqual(captured['status'], 'blocked', captured)
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'implementing', captured)
        self.revalidate_integrated_execution()
        payload = {'artifacts':['impl.py'], 'checks':['verified'], 'candidate_commit':replacement,
            'review':{axis:{'candidate':replacement, 'reviewer_ref':axis, 'status':'accepted'} for axis in ('standards','spec')},
            'verification':{'candidate':replacement}}
        accepted = self.accept_current(payload, 'native:dispatcher', fixture_execution=False, evidence_suffix='-replacement')
        self.assertEqual(accepted['status'], 'accepted', accepted)
        self.assertNotEqual(self.state()['accepted']['digest'], predecessor['digest'])
        return accepted

    def assert_final_recovery_completed(self, completed, *, retain_docs=False):
        self.assertEqual(completed['status'], 'accepted', completed.get('error'))
        self.assertEqual((self.root / 'other-task.md').read_text(), 'parallel planning\n')
        self.assertTrue((self.root / 'impl.py').exists())
        if retain_docs:
            self.assertEqual((self.root / 'CHANGELOG.md').read_text(), 'closure notes\n')

    def final_recovery_runner(self):
        package_root, confirmed_path = self.runner_stage2_fixture()
        configuration = json.loads(confirmed_path.read_text())
        configuration['flow_mode'] = 'continuous'
        configuration['authority_scope']['allowed_paths'] = ['CHANGELOG.md', 'docs', 'impl.py']
        confirmed_path.write_text(json.dumps(configuration))
        fixture = self
        calls, turns = [], []
        class Host:
            instance = 'fixture-host'
            process = None
            active = None
            server_requests = {}
            threads = {}
            def __init__(self, configuration, cwd, journal):
                self.journal = journal
            def start(self, stderr):
                self.process = object()
                self.journal({'kind': 'host-started', 'instance': self.instance, 'pid': os.getpid()})
            def start_carrier(self, stage, settings):
                if stage not in self.threads:
                    calls.append(stage)
                    self.threads[stage] = {'stage2': 'carrier', 'stage3': 'carrier-three',
                                           'stage4': 'carrier-four'}[stage]
                return self.threads[stage]
            def run_turn(self, session, prompt, settings, schema, on_progress):
                outer = progress.read_projection(fixture.checkpoint, projection='identity').record
                turns.append((session, outer['launch']['turn']))
                fixture.settings['thread_id'] = session
                fixture.request['host'].update(thread_id=session, role='scripted-carrier', source_ref='task')
                if session == 'carrier':
                    self.design = fixture.finish_design('continuous')
                    return json.dumps(self.design['completion']['stage_result']), 'design-turn'
                if session == 'carrier-three':
                    if not fixture.state().get('final_integration_recovery'):
                        result = fixture.accepted_implementation_for_closure(self.design)
                        self.original = copy.deepcopy(fixture.state())
                        self.original_footer = json.loads(result['completion']['stage_result']['handoff_json'])['accepted_transfer']
                        self.original_turn = outer['launch']['turn']
                    else:
                        fixture.assertEqual(outer['sessions']['stage3'], 'carrier-three')
                        fixture.assertEqual(outer['launch']['turn'], self.original_turn + 1)
                        result = fixture.revalidate_final_recovery(self.original)
                    return json.dumps(result['completion']['stage_result']), 'implementation-turn-' + str(outer['launch']['turn'])
                fixture.assertEqual(session, 'carrier-four')
                if not hasattr(self, 'recovery_input'):
                    self.recovery_input, _ = fixture.prepare_final_recovery(self.original, retain_docs=True)
                    self.snapshot = copy.deepcopy(fixture.state()['lifecycle_snapshot'])
                    denied = fixture.invoke('implementation-recovery', self.recovery_input)
                    fixture.assertEqual(denied['error']['code'], 'carrier_active')
                    fixture.assertEqual(fixture.state()['stage'], 4)
                    return json.dumps({'result': 'needs_input', 'needs_input_kind': 'user_decision',
                        'question': 'Final integration requires original implementation recovery',
                        'artifacts': [], 'evidence': [], 'handoff_json': '{}', 'message': ''}), 'closure-recovery-turn'
                result = fixture.finish_next(4, 'continuous')
                fixture.assert_final_recovery_completed(result, retain_docs=True)
                return json.dumps(result['completion']['stage_result']), 'closure-completed-turn'
            def poll(self, timeout):
                fixture.recover_final_integration(self.original, self.recovery_input)
                outer = progress.read_projection(fixture.checkpoint, projection='identity').record
                fixture.assertEqual(outer['current_stage'], 'stage3')
                fixture.assertEqual(outer['launch']['turn'], self.original_turn)
                fixture.assertEqual(outer['sessions']['stage3'], 'carrier-three')
                fixture.assertNotIn('stage3', outer['stage_results'])
                # Recovery retains the exact acceptance as evidence, not as current deliverability.
                accepted, _ = progress.implementation_acceptance(fixture.state()['recovery_predecessor'])
                fixture.assertEqual(accepted['digest'], self.original_footer['accepted_digest'])
                fixture.assertEqual(accepted['digest'], self.original['accepted']['digest'])
                fixture.assertEqual(runner.resume(fixture.checkpoint), 0)
            def snapshot_lifecycle(self, stage):
                return copy.deepcopy(self.snapshot)
            def close(self):
                self.process = None
        with patch.object(runner, '__file__', str(package_root / 'guided-implementation/scripts/workflow.py')), \
             patch.object(runner.foreground_host, 'ForegroundHost', Host), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.start(confirmed_path), 0)
        outer = progress.read_projection(self.checkpoint, projection='identity').record
        self.assertEqual(calls, ['stage2', 'stage3', 'stage4'])
        self.assertEqual([turn for session, turn in turns if session == 'carrier-three'], [1, 2])
        self.assertEqual(outer['sessions']['stage3'], 'carrier-three')
        self.assertEqual(outer['status'], 'completed')

    def test_cancel_unknown_then_not_created_cannot_accept_late_ready(self):
        self.begin()
        self.assertEqual(self.invoke("cancel")["status"], "cancelling")
        self.invoke("observe", self.observation("not-created", "lookup", ref=None))
        self.assertEqual(self.state()["status"], "cancelled")
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("observe", self.observation("ready", "lookup"))
        self.assertEqual(self.state()["status"], "cancelled")

    def test_accepted_source_drift_blocks_successor_without_repeating_acceptance(self):
        self.finish_design("continuous")
        saved = self.state()["accepted"]
        (self.flow / "docs/spec.md").write_text("external drift\n")
        self.context = self.context_for(3)
        value = self.input_for(3, saved)
        value["authorization"]["flow_mode"] = "continuous"
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("start", {"handoff": value, "control": {"context": self.context, "discussion": None}})
        self.assertEqual(self.state()["accepted"], saved)

    def test_completed_turn_intake_after_crash_does_not_reinvoke_carrier(self):
        response = self.finish_design("stepwise")
        value = response["completion"]["stage_result"]
        value = {**value, "handoff": json.loads(value["handoff_json"])}
        confirmed = {**{k: self.binding[k] for k in ("repository", "worktree", "git_common_dir", "target_branch")},
            "controller_ref": "task", "flow_mode": "stepwise", "frozen_requirement": {**self.frozen, "path": self.frozen["absolute_path"]}}
        state = runner._new_state(confirmed)
        state["turn_result"] = value
        with patch.object(runner, "_invoke", side_effect=AssertionError("completed turn must not run again")), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(state, self.checkpoint), 0)
        self.assertEqual(state["current_stage"], "stage3")
        self.assertEqual(state["status"], "needs_input")

    def test_legacy_checkpoint_is_never_mutated(self):
        progress.atomic_save(self.checkpoint, {"version": 2, "sentinel": "old runtime"})
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke("inspect")
        self.assertEqual(self.checkpoint.read_bytes(), before)


class AttachedProgressTests(transfer.AttachedTransferTests):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkpoint = Path(temporary.name) / "checkpoint.json"

    def invoke_progress(self, operation, data=None):
        current = progress.read_record(self.checkpoint).get(progress.KEY, {})
        return progress.handle(self.checkpoint, {"protocol": progress.PROTOCOL, "operation": operation,
            "expected_revision": current.get("revision", 0), "data": data or {}})

    def test_ledger_pointer_recovers_original_bind_after_runner_replace_failure(self):
        started = self.invoke_progress('start', {'handoff': self.input, 'control': self.port()})
        self.assertEqual(started['next_action']['operation'], 'invoke-host')
        public = self.read()
        self.assertIn('workflow_control_ref', public)
        state = progress.read_active_owner(self.checkpoint)[progress.KEY]
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        persisted = store.read_subject(current, current.root['metadata']['stage_subject']).value
        self.assertNotIn('context', persisted['control'])
        self.assertEqual(persisted['control']['store_ref'], public['workflow_control_ref'])
        record = state['dispatch']
        receipt = {'adapter': 'fixture-task-host', 'receipt_ref': 'tool:create',
            'request_digest': record['request']['digest'], 'attempt': record['request']['attempt'],
            'role': 'dedicated-discussion', 'event': 'create', 'status': 'ready',
            'ref': 'task:dedicated', 'pending_id': None,
            'configuration': {'model': 'fixture-model', 'effort': 'high'},
            'raw': {'threadId': 'task:dedicated'}}
        original_replace = os.replace
        interrupted = []
        def replace(source, target):
            if Path(target) == self.checkpoint and self.read()['workflow_control']['carrier']['ref'] == 'task:dedicated':
                interrupted.append(str(target))
                raise KeyboardInterrupt('ledger committed before runner mirror replace')
            return original_replace(source, target)
        with patch.object(os, 'replace', replace):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke_progress('observe', {'event_id': 'created', 'receipt': receipt})
        self.assertEqual(interrupted, [str(self.checkpoint)])
        committed = self.read()
        self.assertEqual(committed['workflow_control']['carrier']['ref'], 'task:dedicated')
        before = self.ledger.read_bytes()
        reads = []
        original_open = Path.open
        directories = [self.checkpoint.with_name(self.checkpoint.name + '.state'),
                       self.ledger.with_name(self.ledger.name + '.state')]
        def opened(path, mode='r', *args, **kwargs):
            if 'r' in mode and (path in {self.checkpoint, self.ledger} or
                    any(path.is_relative_to(directory) for directory in directories)):
                reads.append(path.stat().st_size)
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', opened):
            resumed = self.invoke_progress('resume')
        self.assertEqual(resumed['next_action']['operation'], 'wait-host', resumed)
        self.assertLessEqual(sum(reads), progress.state_store.READ_LIMIT)
        self.assertLessEqual(self.checkpoint.stat().st_size, progress.state_store.ROOT_LIMIT)
        self.assertLessEqual(committed['workflow_control_ref']['reference']['size'], progress.state_store.SUBJECT_LIMIT)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(self.read()['workflow_control_ref'], committed['workflow_control_ref'])
        self.assertTrue(self.invoke_progress('observe', {'event_id': 'created', 'receipt': receipt})['acknowledged'])
        if self.NON_GIT:
            self.assertIsNone(state['handoff']['source_commit'])

    def test_retired_archive_uses_exact_ledger_handoff_without_replacing_current_context(self):
        import test_workflow_control
        started = self.invoke_progress('start', {'handoff': self.input, 'control': self.port()})
        self.assertEqual(started['next_action']['operation'], 'invoke-host')
        state = progress.read_active_owner(self.checkpoint)[progress.KEY]
        record = state['dispatch']
        created = {'adapter': 'fixture-task-host', 'receipt_ref': 'created',
            'request_digest': record['request']['digest'], 'attempt': record['request']['attempt'],
            'role': 'dedicated-discussion', 'event': 'create', 'status': 'ready',
            'ref': 'task:dedicated', 'pending_id': None,
            'configuration': {'model': 'fixture-model', 'effort': 'high'}, 'raw': {'threadId': 'task:dedicated'}}
        self.invoke_progress('observe', {'event_id': 'created', 'receipt': created})
        carrier = lambda operation, **values: transfer.discussion_protocol.handle({
            **self.envelope(), 'actor_conversation_ref': 'task:dedicated', 'operation': operation, **values})
        carrier('accept-handoff', handoff_id=self.original['handoff_id'], attempt_id=self.original['attempt_id'],
            payload_sha256=self.original['payload_sha256'],
            source_reference_sha256=self.original['authoritative_references_sha256'], turn_number=1)
        path = self.input['scope']['owned_paths'][0]
        if not self.NON_GIT:
            self.git('add', '-f', path)
            self.git('commit', '-qm', 'accepted dedicated topic')
        old = self.read()['workflow_control']
        received = self.mutate('workflow-control', action='receive', evidence={
            'delivery_id': 'dedicated-result', 'source_ref': 'task:dedicated',
            'attempt': old['carrier']['attempt'],
            'commit': None if self.NON_GIT else self.git('rev-parse', 'HEAD'),
            'requirement_identity': old['requirement_identity'],
            'verified_commit_hash': old['requirement_identity']['sha256']})['control']
        self.mutate('workflow-control', action='accept', evidence={'delivery_digest': received['delivery_digest']})
        checkpoint = carrier('prepare-checkpoint', purpose='stage-entry', base_ref='HEAD')
        published = carrier('publish-non-git-checkpoint' if self.NON_GIT else 'publish-git-checkpoint',
            checkpoint_id=checkpoint['checkpoint_id'],
            expected_checkpoint_revision=checkpoint['checkpoint_record_revision'])
        phase = self.mutate('prepare-wrapper-phase-run', from_phase=0, to_phase=1, route='0->1',
            carrier_kind='current-problem-framing', source_checkpoint_id=checkpoint['checkpoint_id'],
            flow_mode='stepwise', flow_mode_source='explicit-stage-confirmation', scope=['requirements'])
        identity = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        self.mutate('authorize-phase-carrier', **identity, carrier_ref='task')
        self.mutate('claim-phase-carrier', **identity, carrier_ref='task',
            source_checkpoint_id=checkpoint['checkpoint_id'],
            source_checkpoint_identity=published['snapshot_digest' if self.NON_GIT else 'commit_id'])
        self.mutate('phase-ready', **identity, carrier_ref='task', evidence=phase['evidence'])
        self.mutate('phase-activate', **identity, evidence=phase['evidence'])
        retired = self.mutate('workflow-control', action='successor-ready', evidence={
            'ref': 'task', 'stage': 1, 'input_digest': received['delivery_digest'],
            'role': 'current-problem-framing', 'binding_verified': True, 'activated': True,
            'confirmed': True, 'archive_ref': 'task:dedicated',
            'takeover_proof': test_workflow_control.takeover_proof('task:dedicated',
                old['carrier']['attempt'], model='fixture-model')})['control']
        handoff_id = retired['handoff_id']
        before = copy.deepcopy(progress.read_active_owner(self.checkpoint)[progress.KEY]['control'])
        request = {'action': 'archive', 'evidence': {'handoff_id': handoff_id},
            'receipt': {'controller_ref': 'task', 'reference': 'original-ledger-archive'}}
        original_checkpoint = progress.dispatch.checkpoint
        def lost_response(*args, **kwargs):
            original_checkpoint(*args, **kwargs)
            raise KeyboardInterrupt('ledger archive committed before Controller receipt')
        with patch.object(progress.dispatch, 'checkpoint', side_effect=lost_response):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke_progress('control', request)
        pending = progress.read_active_owner(self.checkpoint)[progress.KEY]['control_transactions'][transfer.entry.digest(request)]
        original_pointer = pending['port']['store_ref']
        original_reserve = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'reserves' / 'business' / hashlib.sha256(pending['reserve_action'].encode()).hexdigest()
        self.assertEqual(original_reserve.stat().st_size, 16 * 1024 * 1024)
        self.assertEqual(pending['request'], request)
        self.assertEqual(set(pending['port']), {'discussion', 'store_ref'})
        self.assertEqual(set(pending['handoff']), {'controller_ref', 'expected_entry'})
        self.assertEqual(set(pending['handoff']['expected_entry']), {'discussion_project', 'requirement', 'repository'})
        self.assertNotEqual(original_pointer, self.read()['workflow_control_ref'])
        ledger_bytes = self.ledger.read_bytes()
        reads = []
        original_open = Path.open
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        def opened(path, mode='r', *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if 'r' in mode and (path == self.checkpoint or path.is_relative_to(directory) or
                                path.is_relative_to(self.root)):
                original_read = stream.read
                def read(*args, **kwargs):
                    value = original_read(*args, **kwargs)
                    reads.append((str(path), len(value.encode('utf-8') if isinstance(value, str) else value)))
                    return value
                stream.read = read
            return stream
        with patch.object(Path, 'open', opened):
            intent = self.invoke_progress('control', request)
        self.assertTrue(intent['next_action']['result']['ok'], intent)
        self.assertLessEqual(sum(size for _, size in reads), progress.state_store.READ_LIMIT)
        self.assertFalse(any('/archives/' in path for path, _ in reads), reads)
        self.assertEqual(self.ledger.read_bytes(), ledger_bytes)
        self.assertTrue(original_reserve.exists(), 'exact ledger replay retains the original business reserve')
        restored = progress.read_active_owner(self.checkpoint)[progress.KEY]['control_transactions']
        self.assertEqual(restored[transfer.entry.digest(request)]['reserve_action'], pending['reserve_action'])
        effect = intent['next_action']['result']['effects'][0]
        self.assertEqual((effect['operation'], effect['ref']), ('archive', 'task:dedicated'))
        for status in ('issued', 'unknown'):
            self.assertTrue(self.invoke_progress('control', ProgressTests.archive_receipt(self, effect, status))['next_action']['result']['ok'])
        query = self.invoke_progress('control', {**request, 'receipt': {
            'controller_ref': 'task', 'reference': 'ledger-readback'}})['next_action']['result']['effects'][0]
        self.assertEqual(query['operation'], 'read-archive-state')
        for status in ('issued', 'archived'):
            result = self.invoke_progress('control', ProgressTests.archive_receipt(self, query, status))
            self.assertTrue(result['next_action']['result']['ok'], result)
        public = self.read()
        selected, _ = progress.state_store.StateStore(self.ledger).read_ledger_control(
            public['workflow_control_ref'], selector={'handoff_id': handoff_id})
        self.assertEqual(selected['retired_handoffs'][handoff_id]['archive_status'], 'archived')
        self.assertFalse(original_reserve.exists())
        self.assertEqual(progress.read_active_owner(self.checkpoint)[progress.KEY]['control'], before)
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        subject = store.read_subject(current, current.root['metadata']['stage_subject'])
        self.assertNotIn('context', subject.value['control'])
        self.assertFalse(subject.value.get('control_transactions'))
        self.assertLessEqual(current.reference['size'], progress.state_store.ROOT_LIMIT)
        self.assertLessEqual(subject.reference['size'], progress.state_store.SUBJECT_LIMIT)
        ledger_bytes = self.ledger.read_bytes()
        replay = self.invoke_progress('control', ProgressTests.archive_receipt(self, effect, 'unknown'))
        self.assertTrue(replay['acknowledged'])
        self.assertEqual(replay['next_action']['result']['effects'], [])
        self.assertEqual(self.ledger.read_bytes(), ledger_bytes)
        self.assertEqual(progress.read_active_owner(self.checkpoint)[progress.KEY]['control'], before)

    def test_uncommitted_ledger_objects_do_not_acknowledge_a_control_call(self):
        self.invoke_progress('start', {'handoff': self.input, 'control': self.port()})
        request = {**self.envelope(), 'operation': 'workflow-control', 'action': 'cancel', 'evidence': {}}
        before = self.read()
        original_replace = os.replace
        def replace(source, target):
            if Path(target) == self.ledger:
                raise KeyboardInterrupt('before ledger authority replace')
            return original_replace(source, target)
        with patch.object(os, 'replace', replace):
            with self.assertRaises(KeyboardInterrupt):
                transfer.discussion_protocol.handle(request)
        self.assertEqual(self.read()['workflow_control_ref'], before['workflow_control_ref'])
        result = transfer.discussion_protocol.handle(request)
        self.assertFalse(result.get('idempotent_replay', False))
        self.assertEqual(result['ledger_revision'], before['ledger_revision'] + 1)

    def test_ledger_commit_without_receipt_certificate_recovers_exact_projection(self):
        self.invoke_progress('start', {'handoff': self.input, 'control': self.port()})
        request = {**self.envelope(), 'operation': 'workflow-control', 'action': 'cancel', 'evidence': {}}
        previous = self.read()['ledger_revision']
        original_replace = os.replace
        def replace(source, target):
            result = original_replace(source, target)
            if Path(target) == self.ledger:
                raise KeyboardInterrupt('after ledger commit before receipt certificate')
            return result
        with patch.object(os, 'replace', replace):
            with self.assertRaises(KeyboardInterrupt):
                transfer.discussion_protocol.handle(request)
        committed = self.read()
        self.assertEqual(committed['ledger_revision'], previous + 1)
        before = self.ledger.read_bytes()
        replay = transfer.discussion_protocol.handle(request)
        self.assertTrue(replay['idempotent_replay'])
        self.assertEqual(replay['control']['store_ref'], committed['workflow_control_ref'])
        self.assertEqual(replay['control']['context'], committed['workflow_control'])
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_original_ledger_receipt_survives_recent_event_eviction(self):
        self.invoke_progress('start', {'handoff': self.input, 'control': self.port()})
        request = {**self.envelope(), 'operation': 'workflow-control', 'action': 'cancel', 'evidence': {}}
        original = transfer.discussion_protocol.handle(request)
        for _ in range(201):
            self.mutate('workflow-control', action='choose-dedicated', evidence={'intent': 'explicit-dedicated'})
        before = self.ledger.read_bytes()
        replay = transfer.discussion_protocol.handle(request)
        self.assertTrue(replay.pop('idempotent_replay'))
        self.assertEqual(replay, original)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertNotEqual(self.read()['workflow_control_ref'], replay['control']['store_ref'])
        with self.assertRaises(transfer.discussion_protocol.ProtocolError) as conflict:
            transfer.discussion_protocol.handle({**request, 'evidence': {'unexpected': True}})
        self.assertEqual(conflict.exception.code, 'idempotency_conflict')
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_stale_or_modified_runner_control_cannot_overwrite_ledger(self):
        self.invoke_progress('start', {'handoff': self.input, 'control': self.port()})
        state = progress.read_active_owner(self.checkpoint)[progress.KEY]
        port = copy.deepcopy(state['control'])
        self.mutate('workflow-control', action='cancel', evidence={})
        before = self.ledger.read_bytes()
        port['discussion'] = self.envelope()
        with self.assertRaises(transfer.discussion_protocol.ProtocolError) as raised:
            transfer.dispatch.checkpoint(port, state['handoff'], 'cancel', {})
        self.assertEqual(raised.exception.code, 'control_revision_conflict')
        self.assertEqual(self.ledger.read_bytes(), before)
        outer = progress.read_active_owner(self.checkpoint)
        outer[progress.KEY]['control']['context']['carrier']['ref'] = 'forged-runner'
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            progress.Progress(self.checkpoint, outer).save()
        self.assertEqual(rejected.exception.code, 'control_context_changed')
        self.assertEqual(self.ledger.read_bytes(), before)

class NonGitProgressTests(AttachedProgressTests):
    NON_GIT = True


class PhaseProgressTests(DiscussionProtocolScenarioFixture, DiscussionProtocolTestSupport):
    def test_ledger_phase_completion_requires_exact_accepted_projection(self):
        project = self.make_project('ledger-phase-acceptance', git=True)
        topic = transfer.discussion_protocol.handle(self.request(project, conversation_ref='discussion-task'))
        path = Path(topic['topic_document_path']).relative_to(project).as_posix()
        subprocess.run(['git', '-C', str(project), 'add', '-f', path], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(project), 'commit', '-qm', 'frozen topic'], check=True, capture_output=True)
        commit = subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD'], text=True).strip()
        base = {'protocol_version': 1, 'project_path': str(project), 'project_id': topic['project_id'],
            'tree_id': topic['tree_id'], 'actor_topic_id': topic['topic_id'], 'actor_conversation_ref': 'discussion-task'}
        def mutate(operation, *, actor='discussion-task', **values):
            current = transfer.discussion_protocol.handle({**base, 'operation': 'read-topic'})
            return transfer.discussion_protocol.handle({**base, 'operation': operation,
                'actor_conversation_ref': actor, 'expected_ledger_revision': current['ledger_revision'],
                'expected_topic_revision': current['record_revision'], 'idempotency_key': str(progress.uuid.uuid4()), **values})
        checkpoint = mutate('prepare-checkpoint', purpose='stage-entry', base_ref='HEAD')
        published = mutate('publish-git-checkpoint', checkpoint_id=checkpoint['checkpoint_id'],
                           expected_checkpoint_revision=checkpoint['checkpoint_record_revision'])
        phase = mutate('prepare-wrapper-phase-run', from_phase=0, to_phase=1, route='0->1',
            carrier_kind='dedicated-grilling', source_checkpoint_id=checkpoint['checkpoint_id'],
            flow_mode='stepwise', flow_mode_source='explicit-stage-confirmation', scope=['requirements'])
        planned = mutate('workflow-control', action='prepare', evidence={'target': 'local', 'project': topic['project_id'],
            'title': 'Frame', 'missing_context': [], 'configuration': transfer.configuration('dedicated-problem-framing'),
            'next_step': 'stage2', 'archive_ref': None, 'gate_open': True})['control']
        plan = planned['plan']['plan_id']
        mutate('workflow-control', action='decide', evidence={'plan_id': plan, 'intent': 'confirm'})
        binding = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        mutate('authorize-phase-carrier', **binding, carrier_ref='task:framing')
        mutate('workflow-control', action='creation-result', evidence={'status': 'ready', 'ref': 'task:framing', 'attempt': plan})
        mutate('claim-phase-carrier', actor='task:framing', **binding, carrier_ref='task:framing',
            source_checkpoint_id=checkpoint['checkpoint_id'], source_checkpoint_identity=published['commit_id'])
        mutate('phase-ready', actor='task:framing', **binding, carrier_ref='task:framing', evidence=phase['evidence'])
        mutate('phase-activate', **binding, evidence=phase['evidence'])
        mutate('claim-phase-completion', actor='task:framing', **binding, carrier_ref='task:framing', evidence=phase['evidence'])
        received = mutate('workflow-control', action='receive', evidence={'delivery_id': 'framing-output',
            'source_ref': 'task:framing', 'attempt': plan, 'commit': commit,
            'requirement_identity': planned['context']['requirement_identity'],
            'verified_commit_hash': planned['context']['requirement_identity']['sha256']})['control']
        with self.assertRaises(transfer.discussion_protocol.ProtocolError) as rejected:
            mutate('complete-phase-run', **binding, evidence=phase['evidence'])
        self.assertEqual(rejected.exception.code, 'phase_completion_not_claimed')
        mutate('workflow-control', action='accept', evidence={'delivery_digest': received['delivery_digest']})
        self.assertEqual(mutate('complete-phase-run', **binding, evidence=phase['evidence'])['state'], 'completion-pending')
        self.assertEqual(mutate('finalize-phase-run', **binding, evidence=phase['evidence'])['state'], 'completed')

    def test_source_activation_uses_exact_real_phase_and_idempotent_envelope(self):
        # Reuse the supported caller-controlled Stage-2+ attached initializer.
        # Its topic_ref is real; discussion=None keeps the Controller owner here.
        from test_attached_flow_progress import AttachedFlowTests
        fixture = AttachedFlowTests('test_lifecycle_new_outer_activates_but_inline_controller_is_rejected')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.begin('stepwise')
        fixture.invoke('observe', fixture.observation())
        prepared = fixture.phases[2]
        identity = {'phase_run_id': prepared['phase_run_id'], 'attempt_id': prepared['attempt_id']}
        checkpoint = fixture.frozen['checkpoint']
        fixture.mutate('claim-phase-carrier', **identity, actor_conversation_ref='native:designer',
            carrier_ref='native:designer', source_checkpoint_id=checkpoint['checkpoint_id'],
            source_checkpoint_identity=checkpoint['published_identity'])
        fixture.mutate('phase-ready', **identity, actor_conversation_ref='native:designer',
            carrier_ref='native:designer', evidence=prepared['evidence'])
        outer = progress.read_active_owner(fixture.checkpoint)
        self.assertIsNotNone(outer[progress.KEY]['control']['context']['topic_ref'])
        self.assertIsNone(outer[progress.KEY]['control']['discussion'])
        owner = progress.Progress(fixture.checkpoint, outer)
        emitted = owner.phase_action()
        self.assertEqual(emitted['next_action']['operation'], 'source-lifecycle')
        request = emitted['next_action']['payload']['request']
        self.assertEqual(request['operation'], 'phase-activate')
        self.assertEqual(request['phase_run_id'], prepared['phase_run_id'])
        self.assertEqual(request['attempt_id'], prepared['attempt_id'])
        self.assertEqual(request['evidence'], prepared['evidence'])
        observed = progress.lifecycle(fixture.checkpoint, progress.read_active_owner(fixture.checkpoint),
                                      {'request': request})
        self.assertEqual(observed['result']['result']['state'], 'active')
        before = fixture.ledger.read_bytes()
        progress.lifecycle(fixture.checkpoint, progress.read_active_owner(fixture.checkpoint), {'request': request})
        self.assertEqual(fixture.ledger.read_bytes(), before)
        self.assertIsNone(progress.Progress(fixture.checkpoint,
            progress.read_active_owner(fixture.checkpoint)).phase_action())


class RunnerCheckpointMutationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkpoint = Path(temporary.name) / 'run.json'
        self.state = runner._new_state({})
        runner._atomic_save(self.checkpoint, self.state)

    def save_routing_subject(self, routing):
        """Simulated Controller routing facts, not a real stage initializer.

        The runner reads this finite current identity/delivery projection through
        the production store and CAS. No legacy inline state or write lease is
        exposed; execution authority and scope readiness are not manufactured.
        """
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current() if progress.read_record(self.checkpoint).get('store_format') == progress.state_store.FORMAT else None
        outer = (current.root['outer'] if current else progress.read_record(self.checkpoint))
        stage_id = 'stage:runner-routing'
        metadata = {'protocol': progress.PROTOCOL, 'revision': (current.root['metadata']['revision'] + 1 if current else 1),
                    'stage_subject': stage_id, **copy.deepcopy(routing)}
        store.commit_current(expected_revision=current.revision if current else 0,
            revision=current.revision + 1 if current else 1, subjects={stage_id: metadata},
            metadata=metadata, outer=outer, initial_outer=None if current else outer)

    def test_prior_transfer_runtime_cannot_mutate_checkpoint(self):
        records = []
        for protocol, schema in [('workflow-progress-v9', 3), ('workflow-progress-v10', 4)]:
            old_pin = {'compatibility_key': {'workflow_progress': protocol,
                                            'stage_transfer': 'workflow-stage-transfer-v6'}}
            records.extend([runner._new_state({'packages': {'runner': old_pin}}),
                            {progress.KEY: {'protocol': protocol,
                                            'control': {'context': {'schema_version': schema}}}}])
        for record in records:
            for operation in ('pause', 'cancel', 'resume', 'prepare-requirement',
                              'deliver-requirement', 'lifecycle'):
                with self.subTest(entry='progress', operation=operation, pinned='confirmed' in record):
                    progress.atomic_save(self.checkpoint, record)
                    before = self.checkpoint.read_bytes()
                    with self.assertRaisesRegex(progress.entry.PreparationError, 'original.*runtime'):
                        progress.handle(self.checkpoint, {'protocol': progress.PROTOCOL,
                            'operation': operation, 'expected_revision': 0, 'data': {}})
                    self.assertEqual(self.checkpoint.read_bytes(), before)
        for record in records[::2]:
            for live in (False, True):
                for operation in ('pause', 'cancel'):
                    with self.subTest(entry='runner', operation=operation, live=live):
                        progress.atomic_save(self.checkpoint, record)
                        before = self.checkpoint.read_bytes()
                        with self.assertRaisesRegex(runner.WorkflowError, 'legacy_run_requires_original_runtime'):
                            runner._request_control(self.checkpoint, operation, live=live)
                        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_runner_patch_preserves_newer_checkpoint_fields(self):
        newer = progress.read_record(self.checkpoint)
        newer['future_c_field'] = {'evidence': 'retained'}
        newer['transport'] = {'instance': 'original', 'state': 'live'}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'paused'
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['future_c_field'], {'evidence': 'retained'})
        self.assertEqual(saved['transport'], newer['transport'])
        self.assertEqual(self.state, saved)

    def test_runner_patch_combines_independent_nested_updates(self):
        self.state['confirmed'] = {'flow_mode': 'stepwise', 'registry_input': 'first'}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['confirmed']['registry_input'] = 'second'
        progress.atomic_save(self.checkpoint, newer)
        self.state['confirmed']['flow_mode'] = 'continuous'
        runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint)['confirmed'],
                         {'flow_mode': 'continuous', 'registry_input': 'second'})

    def test_runner_patch_rejects_unowned_inline_decision_proof(self):
        decision = {'decision_id': 'choice', 'answer': 'yes'}
        newer = progress.read_record(self.checkpoint)
        newer['controller_decision'] = decision
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'paused'
        runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint)['controller_decision'], decision)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'decisions': {'choice': decision}, 'history': []}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'active'
        before = self.checkpoint.read_bytes()
        with self.assertRaisesRegex(progress.entry.PreparationError, 'outer initialization requires a new runner'):
            runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(self.checkpoint.read_bytes(), before)
        # Real Controller consumption is covered through public start/resume in
        # test_bounded_progress; an inline fixture cannot assert that authority.

    def test_runner_patch_rejects_conflicting_status_change(self):
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'needs_input'
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'paused'
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_conflict: status'):
            runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint), newer)

    def test_original_host_request_keeps_pending_input_priority(self):
        host_pending = {'kind': 'host-request', 'decision_id': 'host'}
        newer = progress.read_record(self.checkpoint)
        newer['transport'] = {'server_requests': {'host': {'id': 'host'}}}
        newer['pending_input'] = host_pending
        progress.atomic_save(self.checkpoint, newer)
        self.state['pending_input'] = {'kind': 'user-decision', 'decision_id': 'business'}
        runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint)['pending_input'], host_pending)
        self.assertEqual(self.state['pending_input'], host_pending)

    def test_cancel_dominates_new_host_request_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'needs_input'
        newer['pending_input'] = {'kind': 'host-request', 'decision_id': 'host'}
        newer['transport'] = {'server_requests': {'host': {'id': 'host'}}}
        newer['runner_request'] = {'operation': 'cancel', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, newer)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(self.state, self.checkpoint), 0)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'cancelling')
        self.assertEqual(saved['pending_input'], newer['pending_input'])
        self.assertEqual(saved['runner_request'], newer['runner_request'])

    def test_pause_waits_despite_new_host_request_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'needs_input'
        newer['pending_input'] = {'kind': 'host-request', 'decision_id': 'host'}
        newer['transport'] = {'server_requests': {'host': {'id': 'host'}}}
        newer['runner_request'] = {'operation': 'pause', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, newer)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance(self.state, self.checkpoint), 0)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'pausing')
        self.assertEqual(saved['pending_input'], newer['pending_input'])
        self.assertEqual(saved['runner_request'], newer['runner_request'])

    def test_late_cancel_supersedes_newer_paused_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'paused'
        newer['runner_request'] = {'operation': 'cancel', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'cancelling'
        runner._atomic_save(self.checkpoint, self.state)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'cancelling')
        self.assertEqual(saved['runner_request'], newer['runner_request'])

    def test_stale_pause_cannot_downgrade_cancelling_status(self):
        self.state['launch']['state'] = 'launched'
        self.state['sessions']['stage2'] = 'carrier'
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer['status'] = 'cancelling'
        newer['runner_request'] = {'operation': 'pause', 'request_id': 'stale-pause'}
        progress.atomic_save(self.checkpoint, newer)
        self.state['status'] = 'pausing'
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_conflict: status'):
            runner._atomic_save(self.checkpoint, self.state)
        self.assertEqual(progress.read_record(self.checkpoint), newer)

    def test_runner_patch_does_not_recreate_a_missing_checkpoint(self):
        self.checkpoint.unlink()
        self.state['status'] = 'paused'
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_missing'):
            runner._atomic_save(self.checkpoint, self.state)
        with self.assertRaisesRegex(runner.WorkflowError, 'checkpoint_missing'):
            self.state.refresh(progress.read_record(self.checkpoint))
        self.assertFalse(self.checkpoint.exists())

    def test_launch_receipt_survives_checkpoint_write_failure(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)

        class Host:
            instance = 'original-host'
            process = None

            def start(self, diagnostics):
                self.process = object()

            def start_carrier(self, stage, settings):
                return 'original-carrier'

            def run_turn(self, *args):
                raise AssertionError('the uncertain launch must not issue a turn')

        original_save = runner._atomic_save

        def fail_launched_save(path, state, **options):
            if state['launch']['state'] == 'launched':
                raise OSError('checkpoint write failed')
            return original_save(path, state, **options)

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_atomic_save', side_effect=fail_launched_save):
            with self.assertRaisesRegex(runner.CheckpointPendingError, 'reconcile the original launch'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())
        saved = progress.read_record(self.checkpoint)
        receipt_path = runner._carrier_receipt_path(self.checkpoint, 'stage2', 1)
        receipt = progress.read_record(receipt_path)
        self.assertEqual(saved['launch']['state'], 'spawning')
        self.assertEqual(receipt['events'], [{'type': 'thread.started', 'thread_id': 'original-carrier'}])
        recovered = runner.CheckpointState(saved)
        with self.assertRaisesRegex(runner.WorkflowError, 'carrier outcome unresolved'):
            runner._recover_carrier_receipt(recovered, self.checkpoint)
        self.assertEqual(progress.read_record(self.checkpoint)['sessions']['stage2'], 'original-carrier')

    def test_queued_stop_before_launch_intent_prevents_new_carrier(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        queued = progress.read_record(self.checkpoint)
        queued['runner_request'] = {'operation': 'cancel', 'request_id': 'queued-before-launch'}
        queued['status'] = 'cancelled'
        progress.atomic_save(self.checkpoint, queued)

        class Host:
            instance = 'original-host'
            process = None
            started = False

            def start(self, diagnostics):
                self.started = True

            def start_carrier(self, stage, settings):
                raise AssertionError('a queued stop must prevent carrier creation')

        host = Host()
        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, host)
        self.assertFalse(host.started)
        self.assertEqual(progress.read_record(self.checkpoint), queued)

    def test_accepted_stage_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'accepted', 'phase_complete': True}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('accepted stage must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_pending_decision_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'needs_input', 'pending': {'decision_id': 'choice'}}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('pending decision must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_business_block_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'active',
                               'business_block': {'id': 'original-block'}}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('business block must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_blocked_progress_before_launch_intent_prevents_extra_turn(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        newer = progress.read_record(self.checkpoint)
        newer[progress.KEY] = {'stage': 2, 'status': 'blocked'}
        progress.atomic_save(self.checkpoint, newer)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('blocked progress must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(runner.WorkflowError, 'launch_deferred'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_consumed_old_decision_cannot_answer_new_pending_input(self):
        old_decision = {'decision_id': 'old', 'subject': {'stage': 2}, 'answer': 'first',
                        'reference': 'controller-answer:old'}
        pending = {'kind': 'user-decision', 'decision_id': 'new', 'subject': {'stage': 2},
                   'question': 'Choose again'}
        self.state['controller_decision'] = old_decision
        runner._atomic_save(self.checkpoint, self.state)
        self.save_routing_subject({'stage': 2, 'status': 'needs_input', 'pending': pending,
                               'decisions': {'old': old_decision}, 'history': []})
        with patch.object(runner, '_invoke', side_effect=AssertionError('new question needs an answer')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, object()), 0)
        latest = progress.read_projection(self.checkpoint, projection='identity').record
        self.assertEqual(latest['pending_input'], pending)
        self.assertNotIn('controller_decision', latest)

    def test_control_turn_can_use_existing_carrier_after_stop_is_queued(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        runner._atomic_save(self.checkpoint, self.state)
        queued = progress.read_record(self.checkpoint)
        queued['runner_request'] = {'operation': 'pause', 'request_id': 'stop'}
        progress.atomic_save(self.checkpoint, queued)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                return 'original-carrier'

            def run_turn(self, *args):
                raise AssertionError('control turn reached the original carrier')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='stop prompt'):
            with self.assertRaisesRegex(AssertionError, 'control turn reached the original carrier'):
                runner._invoke(self.state, self.checkpoint, None, True, Host(), control_turn=True)

    def test_normal_turn_rechecks_stop_queued_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)

        class Host:
            instance = 'original-host'
            process = None
            started = False

            def start(self, diagnostics):
                self.started = True

            def start_carrier(self, stage, settings):
                raise AssertionError('a queued stop must prevent carrier creation')

        host = Host()
        original_invoke = runner._invoke

        def queue_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                queued = progress.read_record(self.checkpoint)
                queued['runner_request'] = {'operation': 'cancel', 'request_id': 'queued-after-read'}
                queued['status'] = 'cancelled'
                progress.atomic_save(self.checkpoint, queued)
            return original_invoke(*args, **kwargs)

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=queue_before_issue), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, host), 0)
        self.assertFalse(host.started)
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'], 'cancelled')
        self.assertEqual(saved['runner_request']['request_id'], 'queued-after-read')

    def test_normal_turn_rechecks_decision_queued_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        pending = {'kind': 'user-decision', 'decision_id': 'choice', 'subject': {'stage': 2},
                   'question': 'Choose next step'}
        original_invoke = runner._invoke

        def queue_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                self.save_routing_subject({'stage': 2, 'status': 'needs_input', 'pending': pending})
            return original_invoke(*args, **kwargs)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('pending decision must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=queue_before_issue), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, Host()), 0)
        self.assertEqual(progress.read_projection(self.checkpoint, projection='identity').record['pending_input'], pending)

    def test_normal_turn_rechecks_acceptance_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        original_invoke = runner._invoke

        def accept_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                self.save_routing_subject({'stage': 2, 'status': 'accepted', 'phase_complete': True,
                                       'accepted': {'digest': 'accepted'}})
            return original_invoke(*args, **kwargs)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('accepted stage must not issue another turn')

        # This routing seam has no design scope; simulate its explicit not-ready
        # response while retaining the runner's acceptance recheck and rejection.
        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=accept_before_issue), \
                patch.object(progress, 'scope_readiness', return_value=None):
            with self.assertRaisesRegex(runner.WorkflowError, 'scope_incomplete'):
                runner._advance_in_host(self.state, self.checkpoint, None, Host())

    def test_normal_turn_rechecks_business_block_after_progress_read(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        original_invoke = runner._invoke

        def block_before_issue(*args, **kwargs):
            with progress.record_lock(self.checkpoint):
                self.save_routing_subject({'stage': 2, 'status': 'active',
                                       'business_block': {'id': 'original-block'}})
            return original_invoke(*args, **kwargs)

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('business block must not issue another turn')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'), \
                patch.object(runner, '_invoke', side_effect=block_before_issue), redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, Host()), 1)
        latest = progress.read_projection(self.checkpoint, projection='identity').record
        self.assertEqual(latest['status'], 'failed')
        self.assertEqual(latest['error']['business_block_id'], 'original-block')

    def test_prior_stage_stop_does_not_block_next_stage_launch(self):
        self.state['current_stage'] = 'stage3'
        self.state['launch'] = {'stage': 'stage3', 'state': 'prelaunch', 'turn': 0}
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage3': {}},
                                   'stages': {'stage3': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        self.save_routing_subject({'stage': 2, 'status': 'accepted', 'stop_requested': 'pausing'})

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('next-stage launch was reached')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(AssertionError, 'next-stage launch was reached'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_noncreation_outcome_is_not_a_user_stop_request(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        runner._atomic_save(self.checkpoint, self.state)
        self.save_routing_subject({'stage': 2, 'status': 'cancelled', 'stopped': True})

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('noncreation path reached the carrier')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='prompt'):
            with self.assertRaisesRegex(AssertionError, 'noncreation path reached the carrier'):
                runner._invoke(self.state, self.checkpoint, None, False, Host())

    def test_saved_resume_can_reenter_paused_original_carrier(self):
        self.state['confirmed'] = {'packages': {'runner': {}, 'stage2': {}},
                                   'stages': {'stage2': {}}}
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        self.state['resume_progression'] = True
        runner._atomic_save(self.checkpoint, self.state)
        self.save_routing_subject({'stage': 2, 'status': 'paused', 'stop_requested': 'pausing'})

        class Host:
            instance = 'original-host'
            process = object()

            def start_carrier(self, stage, settings):
                raise AssertionError('resume reached the original carrier')

        with patch.object(runner, '_check_current_registry'), patch.object(runner, 'verify_identity'), \
                patch.object(runner, '_stage_prompt', return_value='resume prompt'):
            with self.assertRaisesRegex(AssertionError, 'resume reached the original carrier'):
                runner._invoke(self.state, self.checkpoint, None, True, Host())

    def test_new_pause_after_saved_resume_prevents_normal_turn(self):
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        self.state['resume_progression'] = True
        runner._atomic_save(self.checkpoint, self.state)
        self.save_routing_subject({'stage': 2, 'status': 'pausing', 'stop_requested': 'pausing'})
        with patch.object(runner, '_invoke', side_effect=AssertionError('normal turn must not start')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, object()), 0)
        self.assertEqual(progress.read_projection(self.checkpoint, projection='identity').record['status'], 'pausing')

    def test_cancellation_upgrades_saved_pause_resume(self):
        self.state['sessions']['stage2'] = 'original-carrier'
        self.state['launch'] = {'stage': 'stage2', 'state': 'completed_turn', 'turn': 1}
        self.state['resume_progression'] = True
        runner._atomic_save(self.checkpoint, self.state)
        self.save_routing_subject({'stage': 2, 'status': 'paused', 'stop_requested': 'cancelling'})
        with patch.object(runner, '_invoke', side_effect=AssertionError('normal turn must not start')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(runner._advance_in_host(self.state, self.checkpoint, None, object()), 0)
        self.assertEqual(progress.read_projection(self.checkpoint, projection='identity').record['status'], 'cancelling')

    def test_unhandled_launch_deferral_does_not_spin(self):
        blocked = runner.LaunchDeferred('launch_deferred')
        with patch.object(runner, '_invoke', side_effect=[blocked, blocked,
                                                          AssertionError('unexpected third retry')]) as invoked:
            with self.assertRaisesRegex(runner.WorkflowError, 'did not reach a ready branch'):
                runner._advance_in_host(self.state, self.checkpoint, None, object())
        self.assertEqual(invoked.call_count, 2)


def load_tests(loader, tests, pattern):
    # B's tests run in their own module; inherit only its real fixture helpers.
    suite = unittest.TestSuite(ProgressTests(name) for name in loader.getTestCaseNames(ProgressTests) if name in ProgressTests.__dict__)
    for cls in (AttachedProgressTests, NonGitProgressTests):
        suite.addTests(cls(name) for name in loader.getTestCaseNames(cls) if name in AttachedProgressTests.__dict__)
    suite.addTests(loader.loadTestsFromTestCase(PhaseProgressTests))
    suite.addTests(loader.loadTestsFromTestCase(RunnerCheckpointMutationTests))
    return suite
