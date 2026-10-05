"""Bounded progression through the real Controller, dispatch and Git boundary."""
import hashlib
import copy
import json
import os
import io
from contextlib import redirect_stdout
import unittest
from pathlib import Path
from unittest.mock import patch

from test_workflow_progress import ProgressTests, progress, transfer, runner


class BoundedProgressTests(ProgressTests):
    def test_stage2_scope_missing_corrupt_or_mismatched_objects_fail_closed(self):
        accepted = self.finish_design('stepwise')
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        envelope = json.loads(self.checkpoint.read_bytes())
        def object_path(reference):
            digest = reference['sha256']
            return directory / 'objects' / digest[:2] / digest[2:4] / digest
        root_document = json.loads(object_path(envelope['current']).read_bytes())
        scope_reference = root_document['value']['scope']
        scope_path = object_path(scope_reference)
        original_scope = scope_path.read_bytes()
        for corruption in ('missing', 'bytes', 'run', 'subject', 'schema'):
            with self.subTest(corruption=corruption):
                root = copy.deepcopy(root_document)
                pointer = copy.deepcopy(envelope)
                def write_document(document):
                    raw = progress.state_store.encode(document)
                    reference = {'run_id': document['run_id'], 'kind': document['kind'],
                                 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
                    destination = object_path(reference)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(raw)
                    return reference
                if corruption == 'missing':
                    scope_path.unlink()
                elif corruption == 'bytes':
                    scope_path.write_bytes(original_scope + b' ')
                elif corruption == 'run':
                    root['value']['scope']['run_id'] = 'another-run'
                else:
                    scope = json.loads(original_scope)
                    if corruption == 'subject':
                        scope['value']['subject_ref']['sha256'] = '0' * 64
                    else:
                        scope['value']['value'].pop('stopped')
                    root['value']['scope'] = write_document(scope)
                pointer['current'] = write_document(root)
                self.checkpoint.write_bytes(progress.state_store.encode(pointer))
                with self.assertRaises(transfer.entry.PreparationError):
                    progress.read_scope_readiness(accepted['readiness'])
                scope_path.write_bytes(original_scope)
                self.checkpoint.write_bytes(progress.state_store.encode(envelope))

    def test_stage2_scope_rejects_changed_readiness_identity(self):
        accepted = self.finish_design('stepwise')
        for field, value in (('scope_seal', 'wrong-seal'), ('checkpoint_revision', 0),
                             ('accepted_digest', 'wrong-acceptance')):
            with self.subTest(field=field):
                with self.assertRaises(transfer.entry.PreparationError) as rejected:
                    progress.read_scope_readiness({**accepted['readiness'], field: value})
                self.assertEqual(rejected.exception.code, 'handoff_not_ready')
        changed = copy.deepcopy(accepted['readiness'])
        changed['scope_authority']['candidate_commit'] = self.frozen['commit']
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            progress.read_scope_readiness(changed)
        self.assertEqual(rejected.exception.code, 'handoff_not_ready')

    def test_stage2_scope_receiving_wrong_candidate_is_blocked(self):
        payload = self.prepare_design_result('stepwise')
        payload['planning_commit'] = self.frozen['commit']
        rejected = self.invoke('observe', self.observation('stopped', 'result', {
            'delivery_id': 'wrong-design', 'status': 'completed', 'payload': payload}))
        self.assertEqual(rejected['error']['code'], 'scope_changed')
        self.assertFalse(rejected['handoff_ready'])

    def test_stage2_scope_pause_before_acceptance_replaces_stop_facts(self):
        self.prepare_design_result('stepwise')
        before = progress.state_store.StateStore(self.checkpoint).open_current()
        stopped = self.invoke('pause')
        self.assertFalse(stopped['handoff_ready'])
        after = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertNotEqual(after.root['scope'], before.root['scope'])
        scoped = progress.read_projection(self.checkpoint, projection='scope', stage=2).record[progress.KEY]
        self.assertEqual(scoped['stop_requested'], 'pausing')
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            progress.read_scope_readiness({'checkpoint_path': str(self.checkpoint)})
        self.assertEqual(rejected.exception.code, 'handoff_not_ready')

    def test_stage2_scope_reads_no_stage_subject_or_unrelated_evidence(self):
        accepted = self.finish_design('stepwise')
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        before = progress.read_projection(self.checkpoint, projection='scope', stage=2)
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        unrelated = directory / 'objects' / 'unused'
        unrelated.mkdir()
        for number in range(32):
            (unrelated / str(number)).write_bytes(b'x' * 65536)
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            after = progress.read_projection(self.checkpoint, projection='scope', stage=2)
            scope_reads = [path for path in opened if path == self.checkpoint or path.is_relative_to(directory)]
            progress.read_scope_readiness(accepted['readiness'])
        self.assertEqual((before.files_read, before.bytes_read), (after.files_read, after.bytes_read))
        self.assertEqual(after.files_read, 3)
        self.assertEqual(len(scope_reads), after.files_read)
        self.assertEqual(sum(path.stat().st_size for path in scope_reads), after.bytes_read)
        self.assertLessEqual(after.bytes_read, 3 * 256 * 1024)
        self.assertFalse({ref['sha256'] for ref in current.root['subjects'].values()} &
                         {path.name for path in opened})
        self.assertFalse(any(path.is_relative_to(unrelated) for path in opened))
        view = after.record[progress.KEY]
        self.assertFalse({'allocations', 'host', 'control', 'transaction', 'lifecycle_snapshot'} & view.keys())
        self.assertNotIn('write_release', json.dumps(view))
        if os.environ.get('T01_SCOPE_METRICS_PATH'):
            Path(os.environ['T01_SCOPE_METRICS_PATH']).write_text(json.dumps({
                'scope_files_read': after.files_read, 'scope_bytes_read': after.bytes_read,
                'scope_object_bytes': current.root['scope']['size'], 'stage_subjects_read': 0,
                'unrelated_evidence_bytes': 32 * 65536}, indent=2) + '\n')

    def test_stage2_scope_outer_stop_or_unknown_blocks_saved_readiness(self):
        accepted = self.finish_design('stepwise')
        readiness = accepted['readiness']
        successor = self.input_for(3, self.state()['accepted'])
        scope = readiness['scope_authority']['scope']
        for key in ('implementation_paths', 'closure_paths', 'downstream'):
            successor['scope'][key] = copy.deepcopy(scope[key])
        successor['scope']['owned_paths'] = copy.deepcopy(scope['implementation_paths'])
        successor['authorization']['scope_digest'] = transfer.entry.digest(successor['scope'])
        successor['readiness'] = readiness
        transfer.handoff.prepare(successor)
        original = progress.read_projection(self.checkpoint, projection='identity').record
        original.pop(progress.KEY)
        before = progress.state_store.StateStore(self.checkpoint).open_current()
        for change in ({'runner_request': {'operation': 'pause', 'request_id': 'pause-before-handoff'}},
                       {'runner_request': {'operation': 'cancel', 'request_id': 'cancel-before-handoff'}},
                       {'transport': {'lookup_blocked': 'lookup outcome unknown'}}):
            with self.subTest(change=change):
                current = progress.state_store.StateStore(self.checkpoint).open_current()
                with progress.record_lock(self.checkpoint):
                    progress.save_outer_record(self.checkpoint, {**original, **change}, expected_revision=current.revision)
                after = progress.state_store.StateStore(self.checkpoint).open_current()
                self.assertEqual(after.root['scope'], before.root['scope'])
                with self.assertRaises(transfer.entry.PreparationError) as rejected:
                    progress.read_scope_readiness(readiness)
                self.assertEqual(rejected.exception.code, 'handoff_not_ready')
                with self.assertRaises(transfer.entry.PreparationError) as rejected:
                    transfer.handoff.prepare(successor)
                self.assertEqual(rejected.exception.code, 'handoff_not_ready')

    def test_stage2_scope_published_result_reaches_current_readiness(self):
        self.prepare_design_result('stepwise')
        received = self.invoke('receive-publication')
        self.assertEqual(received['status'], 'needs_input', received)
        self.assertEqual(received['pending']['kind'], 'acceptance')
        pending = received['pending']
        accepted = self.invoke('decide', {'decision_id': pending['decision_id'],
            'subject': pending['subject'], 'answer': 'accept', 'reference': 'controller:readiness'})
        self.assertTrue(accepted['handoff_ready'], accepted)
        current = progress.read_scope_readiness(accepted['readiness'])
        self.assertEqual(current['scope_authority']['candidate_commit'],
                         accepted['readiness']['publication']['planning_commit'])

    def test_running_polls_after_outer_cas_keep_controller_revision_and_ack(self):
        self.begin_dispatcher()
        poll = self.observation('running', 'result', ref='native:dispatcher')
        initial = self.invoke('observe', poll)
        fresh = copy.deepcopy(poll)
        fresh['event_id'] = 'fresh-after-outer'
        fresh['receipt']['receipt_ref'] = 'fresh-after-outer-receipt'
        fresh['provenance'].update(call_ref='fresh-after-outer-call', response_ref='fresh-after-outer-response')
        duplicate_response = {**copy.deepcopy(fresh), 'event_id': 'duplicate-response-after-outer'}
        for number, observation in enumerate((fresh, duplicate_response, fresh), 1):
            before = progress.read_projection(self.checkpoint, projection='identity')
            outer = {key: value for key, value in before.record.items() if key != progress.KEY}
            outer['transport'] = {'instance': 'fixture-host', 'native_event_sequence': 0,
                                  'request_state': 'completed', 'last_poll': number}
            with progress.record_lock(self.checkpoint):
                progress.save_outer_record(self.checkpoint, outer, expected_revision=before.authority_revision)
            committed = progress.state_store.StateStore(self.checkpoint).open_current()
            self.assertGreater(committed.revision, before.authority_revision)
            observed = self.invoke('observe', observation, revision=initial['revision'])
            self.assertEqual(observed['status'], 'active', observed.get('error'))
            self.assertTrue(observed['acknowledged'])
            self.assertEqual(observed['next_action']['operation'], 'wait-host')
            self.assertEqual(observed['revision'], initial['revision'])
            after = progress.state_store.StateStore(self.checkpoint).open_current()
            self.assertEqual(after.revision, committed.revision)
            self.assertEqual(after.root['subjects'], committed.root['subjects'])
        self.assertEqual(self.invoke('inspect')['status'], 'active')

    def test_runner_outer_cas_preserves_controller_receipt_and_stale_owner_is_rejected(self):
        self.prepare_execution()
        slot = self.state()['allocations']['slice-one']
        request = {'allocation_id': 'slice-one', 'operation': 'bind',
                   'receipt': self.receipt(slot['record'], ref='native:executor')}
        bound = self.invoke('allocation', request)
        self.assertEqual(bound['next_action']['result']['status'], 'bound')
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        markers = {path.name: path.read_bytes() for path in (directory / 'commits').iterdir()}
        before = progress.state_store.StateStore(self.checkpoint).open_current()
        outer = progress.read_projection(self.checkpoint, projection='identity').record
        outer.pop(progress.KEY)
        stop = {'operation': 'pause', 'request_id': 'original-runner-stop'}
        outer['runner_request'] = stop
        # The same production CAS adapter used by the public runner. This adds
        # an outer stop without inventing a runner or an accepted-stage history.
        with progress.record_lock(self.checkpoint):
            progress.save_outer_record(self.checkpoint, outer, expected_revision=before.revision)
        after = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertGreater(after.revision, before.revision)
        self.assertEqual(after.root['subjects'], before.root['subjects'])
        self.assertEqual(self.invoke('inspect')['revision'], bound['revision'])
        self.assertEqual({name: (directory / 'commits' / name).read_bytes() for name in markers}, markers)
        with progress.record_lock(self.checkpoint):
            with self.assertRaises(transfer.entry.PreparationError) as stale:
                progress.save_outer_record(self.checkpoint, {**outer, 'runner_request': None},
                                           expected_revision=before.revision)
        self.assertEqual(stale.exception.code, 'stale_checkpoint')
        replayed = self.invoke('allocation', request, revision=bound['revision'])
        self.assertTrue(replayed['acknowledged'])
        self.assertEqual(replayed['next_action']['result']['status'], 'bound')
        self.assertEqual(replayed['revision'], bound['revision'])
        self.assertFalse(replayed['next_action'].get('write_authority', False))
        retained = progress.read_projection(self.checkpoint, projection='identity').record
        self.assertEqual(retained['runner_request'], stop)
        self.assertEqual(progress.state_store.StateStore(self.checkpoint).open_current().revision, after.revision)

    def test_prepared_requirement_survives_first_progress_initialization(self):
        request = {'protocol': transfer.requirement.PROTOCOL, 'operation': 'verify',
                   'entry': self.request, 'evidence': self.frozen}
        prepared = self.invoke('prepare-requirement', {'request': request})
        self.assertEqual(prepared['status'], 'requirement-ready')
        original = progress.read_record(self.checkpoint)['workflow_requirements']
        self.begin()
        self.assertEqual(progress.read_projection(self.checkpoint, projection='identity').record[
            'workflow_requirements'], original)
        self.assertEqual(self.invoke('inspect')['status'], 'active')

    def test_runner_public_start_pause_resume_preserves_original_carrier(self):
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
        fixture = self
        calls = []
        class Host:
            instance = 'fixture-host'
            process = None
            active = None
            server_requests = {}
            threads = {'stage2': 'carrier'}
            def __init__(self, configuration, cwd, journal):
                self.journal = journal
            def start(self, stderr):
                self.process = object()
                self.journal({'kind': 'host-started', 'instance': self.instance, 'pid': os.getpid()})
            def start_carrier(self, stage, settings):
                calls.append(('carrier', 'carrier'))
                return 'carrier'
            def run_turn(self, session, prompt, settings, schema, on_progress):
                calls.append(('turn', session))
                if len([call for call in calls if call[0] == 'turn']) == 1:
                    fixture.begin()
                    fixture.invoke('observe', fixture.observation())
                    before = progress.state_store.StateStore(fixture.checkpoint).open_current()
                    revision = fixture.invoke('inspect')['revision']
                    opened = []
                    original_open = Path.open
                    def tracked_open(path, *args, **kwargs):
                        opened.append(path)
                        return original_open(path, *args, **kwargs)
                    with patch.object(Path, 'open', tracked_open):
                        self.journal({'kind': 'turn-bound', 'instance': self.instance,
                                      'thread': session, 'turn': 'original-turn'})
                    after = progress.state_store.StateStore(fixture.checkpoint).open_current()
                    fixture.assertEqual(before.root['subjects'], after.root['subjects'])
                    fixture.assertGreater(after.revision, before.revision)
                    fixture.assertEqual(fixture.invoke('inspect')['revision'], revision)
                    subject_hashes = {ref['sha256'] for ref in before.root['subjects'].values()}
                    fixture.assertFalse(subject_hashes & {path.name for path in opened},
                        'runner journal must not read any Controller/Ticket subject')
                    fixture.assertEqual(runner.request_control(fixture.checkpoint, 'pause'), 0)
                    fixture.invoke('pause')
                    stopped = fixture.observation('stopped', 'result')
                    stopped['receipt']['raw']['resumable'] = True
                    fixture.assertEqual(fixture.invoke('observe', stopped)['status'], 'paused')
                    self.snapshot = stopped['lifecycle']
                    fixture.assertEqual(runner.resume(fixture.checkpoint), 0)
                else:
                    resumed = fixture.invoke('resume')
                    fixture.assertEqual(resumed['next_action']['operation'], 'continue-host')
                    fixture.assertEqual(fixture.state()['control']['context']['carrier']['ref'], 'native:designer')
                    asked = fixture.invoke('observe', fixture.observation('idle', 'result', {
                        'delivery_id': 'original-question', 'status': 'needs_input',
                        'payload': {'question': 'Continue the original work?'}}))
                    pending = asked['pending']
                    fixture.assertEqual(runner.resume(fixture.checkpoint, 'yes',
                        decision_id=pending['decision_id']), 0)
                    queued = progress.read_projection(fixture.checkpoint, projection='identity').record[
                        'controller_decision']
                    fixture.invoke('decide', queued)
                    self.process = None
                return json.dumps({'result': 'needs_input', 'needs_input_kind': 'user_decision',
                    'question': 'Retained carrier is waiting', 'message': '', 'artifacts': [],
                    'evidence': [], 'handoff_json': '{}'}), 'turn-' + str(len(calls))
            def snapshot_lifecycle(self, stage):
                return self.snapshot
            def poll(self, timeout):
                raise AssertionError('the queued resume must be consumed without another host call')
            def close(self):
                self.process = None
        # Execute the source runner against the installed-layout package identity;
        # only the external host transport and inherited host settings are doubles.
        with patch.object(runner, '__file__', str(package_root / 'guided-implementation/scripts/workflow.py')), \
             patch.object(runner.foreground_host, 'ForegroundHost', Host), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.start(confirmed_path), 0)
            self.assertEqual(self.state()['status'], 'active')
            self.assertNotIn('controller_decision', progress.read_projection(
                self.checkpoint, projection='identity').record)
            with self.assertRaisesRegex(runner.WorkflowError, 'await-host-recovery'):
                runner.resume(self.checkpoint)
        self.assertEqual([call for call in calls if call[0] == 'turn'], [('turn', 'carrier'), ('turn', 'carrier')])
        self.assertEqual(progress.read_projection(self.checkpoint, projection='identity').record['sessions'],
                         {'stage2': 'carrier'})
        self.assertLessEqual(self.checkpoint.stat().st_size, 256 * 1024)

    def test_fresh_poll_provenance_is_bounded_and_old_conflicts_remain_detectable(self):
        self.begin_dispatcher()
        poll = self.observation('running', 'result', ref='native:dispatcher')
        initial = self.invoke('observe', poll)
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        objects = {path.relative_to(directory) for path in (directory / 'objects').rglob('*') if path.is_file()}
        charged_before = json.loads((directory / 'usage.json').read_bytes())['allocated']
        first_state = self.state()
        middle = None
        for number in range(1000):
            fresh = copy.deepcopy(poll)
            fresh['event_id'] = 'fresh-poll-' + str(number)
            fresh['receipt']['receipt_ref'] = 'fresh-receipt-' + str(number)
            fresh['provenance'].update(call_ref='fresh-call-' + str(number), response_ref='fresh-response-' + str(number))
            observed = self.invoke('observe', fresh)
            self.assertEqual(observed['revision'], initial['revision'],
                'a fresh legal response to the same logical poll must not create another authority revision')
            if number == 500:
                middle = copy.deepcopy(fresh)
        self.assertEqual({path.relative_to(directory) for path in (directory / 'objects').rglob('*') if path.is_file()}, objects)
        self.assertEqual(self.state()['events'], first_state['events'])
        self.assertEqual(self.state()['observations'], first_state['observations'])
        self.assertEqual(self.state()['host']['seen'], first_state['host']['seen'])
        self.assertEqual(self.state()['host']['calls'], first_state['host']['calls'])
        telemetry = progress.read_record(self.checkpoint)['poll']
        self.assertEqual(telemetry['count'], 1000)
        self.assertEqual(telemetry['first']['provenance'], poll['provenance'])
        self.assertEqual(telemetry['latest']['provenance'], fresh['provenance'])
        self.assertLessEqual(self.checkpoint.stat().st_size, 256 * 1024)
        index_root = directory / 'poll-fingerprints'
        index_entries = [index_root, *index_root.rglob('*')]
        index_files = [path for path in index_entries if path.is_file()]
        metrics = {'fresh_polls': 1000, 'root_bytes': self.checkpoint.stat().st_size,
            'new_evidence_objects': 0, 'new_current_events': 0,
            'fingerprint_files': len(index_files),
            'fingerprint_payload_bytes': sum(path.stat().st_size for path in index_files),
            'fingerprint_file_allocated_bytes': sum(path.stat().st_blocks * 512 for path in index_files),
            'fingerprint_tree_allocated_bytes': sum(path.stat().st_blocks * 512 for path in index_entries),
            'fingerprint_budget_charge': json.loads((directory / 'usage.json').read_bytes())['allocated'] - charged_before}
        self.assertGreaterEqual(metrics['fingerprint_budget_charge'], metrics['fingerprint_file_allocated_bytes'])
        if os.environ.get('T01_POLL_METRICS_PATH'):
            Path(os.environ['T01_POLL_METRICS_PATH']).write_text(json.dumps(metrics, indent=2) + '\n')
        conflicting = copy.deepcopy(middle)
        conflicting['receipt']['receipt_ref'] = 'changed-receipt'
        event_conflict = self.invoke('observe', conflicting)
        self.assertEqual(event_conflict['error']['code'], 'event_conflict')
        conflicting = copy.deepcopy(middle)
        conflicting['event_id'] = 'reused-middle-response'
        conflicting['provenance']['action_id'] = 'another-original-call'
        source_conflict = self.invoke('observe', conflicting)
        self.assertEqual(source_conflict['error']['code'], 'host_provenance_conflict')
        self.assertEqual(self.state()['host']['status'], 'unknown')

    def test_review_poll_cannot_hide_event_id_conflict(self):
        self.begin_dispatcher()
        poll = self.observation('running', 'result', ref='native:dispatcher')
        self.invoke('observe', poll)
        conflicting = copy.deepcopy(poll)
        conflicting['receipt']['receipt_ref'] = 'changed-original-receipt'
        result = self.invoke('observe', conflicting)
        self.assertEqual((result.get('error') or {}).get('code'), 'event_conflict')

    def test_review_poll_cannot_hide_provenance_conflict(self):
        self.begin_dispatcher()
        poll = self.observation('running', 'result', ref='native:dispatcher')
        self.invoke('observe', poll)
        conflicting = copy.deepcopy(poll)
        conflicting['event_id'] = 'conflicting-poll'
        conflicting['provenance']['action_id'] = 'different-original-action'
        result = self.invoke('observe', conflicting)
        self.assertEqual((result.get('error') or {}).get('code'), 'host_provenance_conflict')
        self.assertEqual(self.state()['host']['status'], 'unknown')
        self.assertIsNone(self.state()['host']['proof'])

    def test_review_wrong_identity_cannot_consume_original_reserve(self):
        self.begin_dispatcher()
        stopped = self.invoke('pause')
        action_id = stopped['next_action']['action_id']
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        reserve = directory / 'reserves' / 'recovery' / hashlib.sha256(action_id.encode()).hexdigest()
        before = reserve.stat().st_size
        wrong = self.observation('stopped', 'result', ref='native:intruder')
        result = self.invoke('observe', wrong)
        self.assertEqual(result['error']['code'], 'identity_mismatch')
        self.assertEqual(self.state()['observations'][-1], wrong, 'rejected raw evidence remains auditable')
        self.assertEqual(reserve.stat().st_size, before)
        (directory / 'usage.json').write_text(json.dumps({'allocated': 2 * 1024**3 - 128 * 1024**2}))
        base = self.observation('stopped', 'result', ref='native:dispatcher')
        for invalid in ('identity', 'status', 'cause'):
            with self.subTest(invalid=invalid):
                wrong = copy.deepcopy(base)
                wrong['event_id'] = 'invalid-' + invalid
                if invalid == 'identity':
                    wrong['receipt']['ref'] = 'native:intruder'
                elif invalid == 'status':
                    wrong['receipt']['status'] = 'invalid-state'
                else:
                    wrong['provenance']['action_id'] = 'different-original-action'
                try:
                    result = self.invoke('observe', wrong)
                except transfer.entry.PreparationError:
                    pass  # Audit admission may fail when ordinary capacity is full.
                else:
                    self.assertFalse(result.get('acknowledged'))
                self.assertEqual(reserve.stat().st_size, before,
                    'invalid evidence must not charge the legitimate stop receipt reserve')
        self.assertNotEqual(self.state()['host']['status'], 'stopped')

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
