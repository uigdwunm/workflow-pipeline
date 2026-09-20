"""Delivery through the real checkpoint, Git and receiving boundaries."""
import copy
import tempfile
from pathlib import Path
import subprocess
import unittest
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from test_entry_prepare import EntrySupport
import entry_prepare as entry
import requirement_prepare as requirement
import requirement_delivery as producer
import discussion_protocol
import uuid
from test_stage_transfer import configuration
import json
import io
from contextlib import redirect_stdout
import stage_dispatch as dispatch
import workflow_control as control
import workflow_progress as progress
import stage_handoff as handoff
import supervision_protocol as supervision


class DeliveryTests(EntrySupport):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.area = Path(temporary.name).resolve()
        self.source = self.area / 'source'
        self.git('worktree', 'add', '--detach', str(self.source), 'HEAD')
        self.addCleanup(lambda: subprocess.run(['git', '-C', str(self.root), 'worktree', 'remove', '--force', str(self.source)], capture_output=True))
        self.path = 'docs/requirements/a.md'
        entry.os.getcwd.return_value = str(self.source)
        self.request['host']['project_path'] = str(self.source)
        self.request['target'] = {'kind': 'planning', 'repository': str(self.source), 'branch': None}
        self.request['source']['path'] = self.path
        if self._testMethodName == 'test_dedicated_delivery_receive_accept_and_duplicate':
            self.request['host'].update(role='dedicated-problem-framing', controller_ref='controller', source_ref='host:created')
        prepared = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'purpose': 'write', 'path': self.path, 'version': 1, 'authorization': 'controller:write', 'content': 'confirmed requirement\n'})
        document = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'write', 'entry': self.request, 'intent': prepared})
        frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'purpose': 'freeze', 'path': self.path, 'version': 1, 'authorization': 'controller:freeze', 'previous': document})
        self.frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'freeze', 'entry': self.request, 'intent': frozen})
        self.delivery_request = {'protocol': 'requirement-delivery-v1', 'operation': 'prepare', 'entry': copy.deepcopy(self.request),
            'requirement': self.frozen, 'target': {'repository': str(self.root), 'branch': 'main'},
            'owned_paths': [self.path], 'authorization': 'controller:deliver'}
        self.checkpoint = self.area / 'checkpoint.json'

    def deliver(self):
        return progress.handle(self.checkpoint, {'protocol': progress.PROTOCOL, 'operation': 'deliver-requirement',
            'expected_revision': 0, 'data': {'request': self.delivery_request}})

    def test_detached_freeze_checkpoint_delivery_receiver_and_new_flow(self):
        request = {'protocol': progress.PROTOCOL, 'operation': 'deliver-requirement', 'expected_revision': 0,
                   'data': {'request': self.delivery_request}}
        output = io.StringIO()
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))
        with patch.object(sys, 'argv', [progress.__file__, str(self.checkpoint)]), patch.object(sys, 'stdin', stdin), redirect_stdout(output):
            code = progress.main()
        response = json.loads(output.getvalue())
        self.assertEqual(code, 0, response)
        result = response['result']
        self.assertEqual(result['status'], 'delivery-ready', result)
        outcome = result['result']
        received = handoff.delivery({'target': self.delivery_request['target'], 'delivery': outcome['delivery'],
            'requirement': self.frozen}, entry.resolve(self.request), self.frozen['requirement_identity'], self.frozen['commit'])
        self.assertEqual(received['source_commit'], self.frozen['commit'])
        self.assertNotEqual(received['source_commit'], received['delivery_commit'])
        self.assertNotEqual(subprocess.run(['git', '-C', str(self.root), 'merge-base', '--is-ancestor', self.frozen['commit'], 'HEAD']).returncode, 0)
        flow = self.area / 'flow'
        binding = supervision.start_worktree({'repository': str(self.root), 'worktree': str(flow),
            'branch': 'codex/next', 'target_branch': 'main'})['binding']
        self.addCleanup(lambda: subprocess.run(['git', '-C', str(self.root), 'worktree', 'remove', '--force', str(flow)], capture_output=True))
        self.assertEqual((flow / self.path).read_text(), 'confirmed requirement\n')
        self.assertEqual(handoff.delivery({'target': self.delivery_request['target'], 'delivery': outcome['delivery'],
            'requirement': self.frozen}, entry.resolve(self.request), self.frozen['requirement_identity'], self.frozen['commit']), received)
        self.assertEqual(binding['base_commit'], received['delivery_commit'])
        entry.os.getcwd.return_value = str(flow)
        downstream = copy.deepcopy(self.request)
        downstream.update(stage=2, source={'kind': 'frozen', 'path': self.path}, target={'kind': 'flow', 'binding': binding})
        downstream['host']['project_path'] = str(flow)
        scope = {'baseline': binding['base_commit'], 'owned_paths': ['docs/spec.md'], 'protected_paths': [self.path],
                 'implementation_paths': ['impl.py'], 'closure_paths': []}
        prepared = handoff.handle({'protocol': handoff.PROTOCOL, 'operation': 'prepare', 'entry': downstream,
            'expected_entry': entry.resolve(downstream), 'stage': 2, 'role': 'solution-designer', 'requirement': self.frozen,
            'predecessor': None, 'target': self.delivery_request['target'], 'delivery': outcome['delivery'], 'binding': binding,
            'scope': scope, 'authorization': {'reference': 'controller:next', 'flow_mode': 'continuous', 'scope_digest': entry.digest(scope)},
            'configuration': configuration('solution-designer'), 'semantic': {'objective': 'plan confirmed requirement',
            'testing_basis': 'real Git CLI', 'completion_criteria': ['plan'], 'constraints': ['owned paths']}})
        self.assertEqual(handoff.verify(prepared)['delivery_facts']['delivery_commit'], outcome['delivery_commit'])

    def test_unrelated_source_history_and_target_user_work_are_preserved(self):
        (self.source / 'unrelated.py').write_text('source only\n')
        subprocess.run(['git', '-C', str(self.source), 'add', 'unrelated.py'], check=True)
        subprocess.run(['git', '-C', str(self.source), 'commit', '-qm', 'unrelated source'], check=True)
        (self.root / 'existing.txt').write_text('staged\n')
        self.git('add', 'existing.txt')
        (self.root / 'existing.txt').write_text('unstaged\n')
        (self.root / 'untracked').write_text('user file\n')
        (self.root / 'user-link').symlink_to('existing.txt')
        before = requirement.unrelated(self.root, [self.path])
        result = self.deliver()
        self.assertEqual(result['status'], 'delivery-ready', result)
        self.assertEqual(result['result']['intent']['user_work']['user-link']['work'], {'link': 'existing.txt'})
        self.assertEqual(requirement.unrelated(self.root, [self.path]), before)
        self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'), self.path)
        self.assertFalse((self.root / 'unrelated.py').exists())

    def test_repeat_delivery_reuses_the_original_commit(self):
        first = self.deliver()
        self.assertEqual(first['status'], 'delivery-ready', first)
        head = self.git('rev-parse', 'HEAD')
        again = self.deliver()
        self.assertEqual(again['status'], 'delivery-ready', again)
        self.assertEqual(again['result']['delivery_commit'], head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_owned_untracked_document_is_never_adopted(self):
        destination = self.root / self.path
        destination.parent.mkdir(parents=True)
        destination.write_text('confirmed requirement\n')
        head = self.git('rev-parse', 'HEAD')
        result = self.deliver()
        self.assertEqual(result['error']['code'], 'delivery_conflict')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(destination.read_text(), 'confirmed requirement\n')

    def test_scope_and_actor_are_checked_before_target_write(self):
        for change in ('actor', 'paths', 'symlink', 'ignored'):
            with self.subTest(change=change):
                request = copy.deepcopy(self.delivery_request)
                if change == 'actor':
                    request['entry']['host']['thread_id'] = 'foreign'
                elif change == 'paths':
                    request['owned_paths'] = [self.path, 'other.md']
                elif change == 'symlink':
                    (self.root / 'docs').symlink_to(self.source / 'docs', target_is_directory=True)
                else:
                    (self.root / '.git/info/exclude').write_text('docs/\n')
                with self.assertRaises(entry.PreparationError):
                    producer.handle(request)
                if change == 'symlink':
                    (self.root / 'docs').unlink()
        self.assertFalse((self.root / self.path).exists())

    def test_lost_commit_response_is_reconciled_without_second_commit(self):
        real = entry.git
        def lost(root, *args, **kwargs):
            result = real(root, *args, **kwargs)
            if args[0] == 'commit' and Path(root) == self.root:
                raise OSError('simulated process loss')
            return result
        with patch.object(entry, 'git', side_effect=lost):
            failed = self.deliver()
        self.assertEqual(failed['status'], 'blocked')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(failed['error']['completed_evidence'][0]['commit'], head)
        self.assertFalse(failed['error']['completed_evidence'][0]['verified'])
        self.assertEqual(self.deliver()['result']['delivery_commit'], head)

        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_drift_after_prepare_is_not_overwritten(self):
        original = progress.atomic_save
        def drift(path, outer):
            original(path, outer)
            records = list(outer.get('workflow_requirements', {}).values())
            if records and records[0].get('issued'):
                destination = self.root / self.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text('new user work\n')
        with patch.object(progress, 'atomic_save', side_effect=drift):
            failed = self.deliver()
        self.assertEqual(failed['status'], 'blocked')
        self.assertEqual((self.root / self.path).read_text(), 'new user work\n')

    def test_hook_failure_keeps_intent_and_staging_for_retry(self):
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        before = self.git('rev-parse', 'HEAD')
        failed = self.deliver()
        self.assertEqual(failed['status'], 'blocked')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('diff', '--cached', '--name-only'), self.path)
        hook.unlink()
        success = self.deliver()
        self.assertEqual(success['status'], 'delivery-ready', success)
        self.assertEqual(self.git('rev-list', '--count', before + '..HEAD'), '1')

    def test_readonly_reconcile_does_not_finish_unissued_delivery(self):
        intent = producer.handle(self.delivery_request)
        observed = producer.handle({'protocol': producer.PROTOCOL, 'operation': 'reconcile', 'entry': self.request, 'intent': intent})
        self.assertEqual(observed['state'], 'prepared')
        self.assertFalse((self.root / self.path).exists())

    def test_malformed_intent_metadata_is_rejected_before_writes(self):
        original = producer.handle(self.delivery_request)
        baseline = self.git('rev-parse', 'HEAD')
        for field in ('operation_id', 'documents'):
            intent = copy.deepcopy(original)
            if field == 'operation_id':
                intent[field] = 123
            else:
                intent[field][self.path]['blob'] = self.git('rev-parse', 'HEAD:existing.txt')
            intent.pop('digest')
            intent = requirement.sealed(intent)
            with self.subTest(field=field), self.assertRaises(entry.ERROR_TYPES):
                producer.handle({'protocol': producer.PROTOCOL, 'operation': 'deliver', 'entry': self.request, 'intent': intent})
            self.assertEqual(self.git('rev-parse', 'HEAD'), baseline)
            self.assertFalse((self.root / self.path).exists())

    def test_unrelated_target_advance_refreshes_same_transaction(self):
        original = progress.atomic_save
        advanced = False
        def advance(path, outer):
            nonlocal advanced
            original(path, outer)
            records = list(outer.get('workflow_requirements', {}).values())
            if not advanced and records and records[0].get('issued'):
                advanced = True
                (self.root / 'other.txt').write_text('unrelated target advance\n')
                self.git('add', 'other.txt')
                self.git('commit', '-qm', 'unrelated advance')
        with patch.object(progress, 'atomic_save', side_effect=advance):
            result = self.deliver()
        self.assertEqual(result['status'], 'delivery-ready', result)
        records = progress.read_record(self.checkpoint)['workflow_requirements']
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[result['transaction']]['prior_intents']), 1)
        head = self.git('rev-parse', 'HEAD')
        (self.root / 'other.txt').write_text('later target advance\n')
        self.git('add', 'other.txt')
        self.git('commit', '-qm', 'later advance')
        self.assertEqual(self.deliver()['result']['delivery_commit'], head)

    def test_postcommit_drift_returns_object_evidence_and_can_recover(self):
        hook = self.root / '.git/hooks/post-commit'
        hook.write_text('#!/bin/sh\nprintf drift > existing.txt\n')
        hook.chmod(0o755)
        failed = self.deliver()
        self.assertEqual(failed['status'], 'blocked')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(failed['error']['completed_evidence'][0]['commit'], head)
        self.assertFalse(failed['error']['completed_evidence'][0]['downstream_ready'])
        hook.unlink()
        (self.root / 'existing.txt').write_text('baseline\n')
        self.assertEqual(self.deliver()['result']['delivery_commit'], head)


    def test_checkpoint_interruptions_resume_original_transaction(self):
        baseline = self.git('rev-parse', 'HEAD')
        for checkpoint in ('intent', 'issued', 'delivered'):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as area:
                self.checkpoint = Path(area).resolve() / 'checkpoint.json'
                original = progress.atomic_save
                stopped = False
                def interrupt(path, outer):
                    nonlocal stopped
                    original(path, outer)
                    records = list(outer.get('workflow_requirements', {}).values())
                    saved = records[0] if records else {}
                    reached = saved.get('intent') is not None if checkpoint == 'intent' else saved.get('state') == checkpoint
                    if not stopped and reached:
                        stopped = True
                        raise SystemExit('simulated process termination')
                # Each case restarts from its own durable checkpoint and intent.
                with patch.object(progress, 'atomic_save', side_effect=interrupt), self.assertRaises(SystemExit):
                    self.deliver()
                result = self.deliver()
                self.assertEqual(result['status'], 'delivery-ready', result)
                self.assertEqual(len(progress.read_record(self.checkpoint)['workflow_requirements']), 1)
                self.assertEqual(self.git('rev-list', '--count', baseline + '..HEAD'), '1')
                self.git('reset', '--hard', baseline)

    def test_multi_document_partial_write_and_partial_staging_resume(self):
        other = 'docs/requirements/b.md'
        (self.source / other).write_text('second owned document\n')
        prepared = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'purpose': 'freeze', 'path': self.path, 'version': 1, 'authorization': 'controller:freeze-both',
            'previous': self.frozen, 'owned_paths': [self.path, other]})
        self.frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'freeze', 'entry': self.request, 'intent': prepared})
        self.delivery_request.update(requirement=self.frozen, owned_paths=[self.path, other])
        real = producer.os.replace
        stopped = False
        def interrupt(source, destination):
            nonlocal stopped
            real(source, destination)
            if not stopped and Path(destination) == self.root / self.path:
                stopped = True
                raise SystemExit('partial write')
        with patch.object(producer.os, 'replace', side_effect=interrupt), self.assertRaises(SystemExit):
            self.deliver()
        self.assertTrue((self.root / self.path).exists())
        self.assertFalse((self.root / other).exists())
        self.git('add', self.path)
        result = self.deliver()
        self.assertEqual(result['status'], 'delivery-ready', result)
        self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines(), [self.path, other])
        (self.root / other).write_text('later protected change\n')
        self.git('add', other)
        self.git('commit', '-qm', 'change companion')
        self.assertEqual(self.deliver()['status'], 'blocked')

    def test_detached_delivery_object_is_not_recreated(self):
        baseline = self.git('rev-parse', 'HEAD')
        delivered = self.deliver()['result']['delivery_commit']
        self.git('reset', '--hard', baseline)
        failed = self.deliver()
        self.assertEqual(failed['error']['code'], 'delivery_detached')
        self.assertEqual(self.git('rev-parse', 'HEAD'), baseline)
        self.assertEqual(self.git('cat-file', '-t', delivered), 'commit')

    def test_identical_bytes_without_original_proof_do_not_create_empty_commit(self):
        first = self.deliver()
        head = self.git('rev-parse', 'HEAD')
        self.checkpoint = self.area / 'other-checkpoint.json'
        failed = self.deliver()
        self.assertEqual(failed['error']['code'], 'delivery_unverified', failed)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(first['result']['delivery_commit'], head)
        self.checkpoint = self.area / 'checkpoint.json'
        self.delivery_request['previous'] = first['result']
        reused = self.deliver()
        self.assertEqual(reused['status'], 'delivery-ready', reused)
        self.assertEqual(reused['result']['delivery_commit'], head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_pause_blocks_new_delivery_even_under_new_request(self):
        progress.atomic_save(self.checkpoint, {'runner_request': {'operation': 'pause'}})
        failed = self.deliver()
        self.assertEqual(failed['error']['code'], 'progression_suspended')
        self.delivery_request['authorization'] = 'another-string'
        self.assertEqual(self.deliver()['error']['code'], 'progression_suspended')
        self.assertFalse((self.root / self.path).exists())

    def test_source_already_on_target_uses_null_proof_without_commit(self):
        self.git('merge', '--ff-only', self.frozen['commit'])
        head = self.git('rev-parse', 'HEAD')
        first = self.deliver()
        self.assertEqual(first['status'], 'delivery-ready', first)
        self.assertIsNone(first['result']['delivery'])
        self.assertEqual(self.deliver()['result']['delivery_commit'], head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_dedicated_delivery_receive_accept_and_duplicate(self):
        outcome = self.deliver()['result']
        controller = copy.deepcopy(self.request)
        controller['host'].update(role='controller', controller_ref='task', source_ref=None)
        config = configuration('dedicated-problem-framing')
        ctx = {'schema_version': 1, 'controller_ref': 'task', 'topic_ref': None, 'stage': 1, 'carrier': None,
            'preference': {'topic_current': False, 'stage_current': False}, 'flow_authority': None,
            'requirement_identity': self.frozen['requirement_identity'], 'handoff_progress': None}
        planned = control.transition({'schema_version': 1, 'actor_ref': 'task', 'context': ctx, 'action': 'prepare',
            'evidence': {'target': 'local', 'project': 'project', 'title': 'Frame', 'missing_context': [], 'configuration': config,
                         'next_step': 'stage2', 'archive_ref': None, 'gate_open': True}})
        ctx = control.transition({'schema_version': 1, 'actor_ref': 'task', 'context': planned['context'], 'action': 'decide',
            'evidence': {'plan_id': planned['plan']['plan_id'], 'intent': 'confirm'}})['context']
        scope = {'baseline': self.frozen['commit'], 'owned_paths': [self.path], 'protected_paths': [],
                 'implementation_paths': [], 'closure_paths': []}
        saved = handoff.handle({'protocol': handoff.PROTOCOL, 'operation': 'prepare', 'entry': controller,
            'expected_entry': entry.resolve(controller), 'stage': 1, 'role': 'dedicated-problem-framing', 'requirement': self.frozen,
            'predecessor': None, 'target': self.delivery_request['target'], 'delivery': None, 'binding': None,
            'scope': scope, 'authorization': {'reference': 'controller:confirmed', 'flow_mode': 'stepwise', 'scope_digest': entry.digest(scope)},
            'configuration': config, 'semantic': {'objective': 'frame requirement', 'testing_basis': 'confirmed scope',
            'completion_criteria': ['frozen and delivered'], 'constraints': ['owned documentation']}})
        def call(operation, **values):
            nonlocal ctx
            result = dispatch.handle({'protocol': handoff.PROTOCOL, 'operation': operation,
                'control': {'context': ctx, 'discussion': None}, **values})
            if 'checkpoint' in result:
                ctx = result['checkpoint']['context']
            return result
        record = call('prepare', handoff=saved)['record']
        receipt = {'adapter': 'fixture-host', 'receipt_ref': 'host:create', 'request_digest': record['request']['digest'],
            'attempt': record['request']['attempt'], 'role': 'dedicated-problem-framing', 'event': 'create', 'status': 'ready',
            'ref': 'dedicated', 'pending_id': None, 'configuration': {'model': 'fixture-model', 'effort': 'high'}, 'raw': {'threadId': 'dedicated'}}
        record = call('bind', record=record, receipt=receipt)['record']
        message = {'delivery_id': 'source:' + self.frozen['commit'] + ':' + self.frozen['sha256'], 'status': 'completed',
                   'payload': {'artifacts': [self.path], 'checks': ['verified target delivery'], 'requirement': self.frozen,
                               'delivery': outcome['delivery']}}
        receipt.update(event='result', status='stopped', receipt_ref='host:result')
        def drift(kind):
            if kind == 'mode':
                (self.root / self.path).chmod(0o755)
            else:
                (self.root / self.path).write_text('different staged document\n')
                self.git('add', self.path)
                (self.root / self.path).write_text('confirmed requirement\n')
        def restore():
            (self.root / self.path).chmod(0o644)
            self.git('restore', '--staged', self.path)
        for kind in ('mode', 'index'):
            with self.subTest(boundary='receive', drift=kind):
                drift(kind)
                self.assert_code('delivery_pending', lambda: call('receive', record=record, receipt=receipt, result=message))
                restore()
        record = call('receive', record=record, receipt=receipt, result=message)['record']
        decision = {'reference': 'controller:accept', 'delivery_digest': record['delivery']['digest']}
        for kind in ('mode', 'index'):
            with self.subTest(boundary='accept', drift=kind):
                drift(kind)
                self.assert_code('delivery_pending', lambda: call('accept', record=record, decision=decision))
                restore()
        accepted = call('accept', record=record, decision=decision)
        self.assertTrue(accepted['downstream_ready'])
        self.assertEqual(accepted['accepted']['payload']['requirement']['commit'], self.frozen['commit'])
        self.assertTrue(call('receive', record=accepted['record'], receipt=receipt, result=message)['acknowledged'])
        self.assertTrue(call('accept', record=accepted['record'], decision=decision)['acknowledged'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), outcome['delivery_commit'])

    def test_git_filter_cannot_change_frozen_document(self):
        self.git('config', 'filter.delivery.clean', 'tr a-z A-Z')
        (self.root / '.gitattributes').write_text('*.md filter=delivery\n')
        head = self.git('rev-parse', 'HEAD')
        failed = self.deliver()
        self.assertEqual(failed['error']['code'], 'filtered_bytes_changed', failed)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual((self.root / self.path).read_text(), 'confirmed requirement\n')

    def test_hook_altered_commit_retains_unverified_object_evidence(self):
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nprintf "hook changed document\\n" > ' + self.path + '\ngit add -- ' + self.path + '\n')
        hook.chmod(0o755)
        before = self.git('rev-parse', 'HEAD')
        failed = self.deliver()
        head = self.git('rev-parse', 'HEAD')
        self.assertNotEqual(head, before)
        self.assertEqual(failed['status'], 'blocked')
        self.assertEqual(self.git('show', 'HEAD:' + self.path), 'hook changed document')
        evidence = failed['error']['completed_evidence'][0]
        self.assertEqual(evidence['kind'], 'observed-delivery-object')
        self.assertEqual(evidence['commit'], head)
        self.assertFalse(evidence['verified'])
        self.assertFalse(evidence['downstream_ready'])
        self.assertEqual(evidence['validation_error'], 'delivery_unverified')
        saved = progress.read_record(self.checkpoint)['workflow_requirements'][failed['transaction']]
        self.assertEqual(evidence['intent_digest'], saved['intent']['digest'])
        hook.unlink()
        retried = self.deliver()
        self.assertEqual(retried['error']['completed_evidence'][0]['commit'], head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_missing_trailer_retains_observed_object_on_reconcile(self):
        hook = self.root / '.git/hooks/commit-msg'
        hook.write_text('#!/bin/sh\nprintf "rewritten message\\n" > "$1"\n')
        hook.chmod(0o755)
        failed = self.deliver()
        self.assertEqual(failed['status'], 'blocked')
        head = self.git('rev-parse', 'HEAD')
        self.assertEqual(failed['error']['completed_evidence'][0]['commit'], head)
        hook.unlink()
        retried = self.deliver()
        self.assertEqual(retried['status'], 'blocked')
        self.assertEqual(retried['error']['completed_evidence'][0]['commit'], head)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_failed_commit_command_retains_observed_object_before_reuse(self):
        real = entry.git
        def failed_response(root, *args, **kwargs):
            result = real(root, *args, **kwargs)
            if args[0] == 'commit' and Path(root) == self.root:
                result.returncode = 1
            return result
        with patch.object(entry, 'git', side_effect=failed_response):
            failed = self.deliver()
        self.assertEqual(failed['error']['code'], 'delivery_outcome_unknown')
        head = self.git('rev-parse', 'HEAD')
        evidence = failed['error']['completed_evidence'][0]
        self.assertEqual(evidence['commit'], head)
        self.assertFalse(evidence['verified'])
        self.assertEqual(self.deliver()['result']['delivery_commit'], head)

    def test_ambiguous_matching_objects_block_without_ref_changes(self):
        first = self.deliver()
        head = self.git('rev-parse', 'HEAD')
        record = progress.read_record(self.checkpoint)['workflow_requirements'][first['transaction']]
        intent = record['intent']
        message = 'another candidate\n\nCodex-Requirement-Delivery: ' + intent['digest'] + '\n'
        other = subprocess.run(['git', '-C', str(self.root), 'commit-tree', head + '^{tree}', '-p', intent['target_head']],
            input=message.encode(), capture_output=True, check=True).stdout.decode().strip()
        self.assertNotEqual(other, head)
        self.assertEqual(self.deliver()['error']['code'], 'delivery_ambiguous')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_generated_cli_is_isolated_and_rejects_malformed_json(self):
        repository = Path(__file__).resolve().parents[2]
        for package in ('problem-framing', 'solution-design', 'guided-implementation', 'change-closure', 'design-discussion'):
            script = repository / 'skills' / package / 'scripts/requirement_delivery.py'
            for malformed in (b'{"protocol":1,"protocol":2}', b'[' * 1100 + b']' * 1100):
                result = subprocess.run([sys.executable, '-I', str(script)], input=malformed, capture_output=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                value = json.loads(result.stdout)
                self.assertFalse(value['ok'])
                self.assertNotIn('Traceback', result.stderr.decode())


class AttachedDeliveryTests(EntrySupport):
    def test_checkpoint_keeps_branch_then_delivery_publishes_only_topic(self):
        topic = discussion_protocol.handle({'protocol_version': 1, 'operation': 'bootstrap', 'project_path': str(self.root),
            'entry_mode': 'explicit-skill', 'conversation_ref': 'task', 'idempotency_key': str(uuid.uuid4()), 'root_slug': 'delivery'})
        attachment = {'project_id': topic['project_id'], 'tree_id': topic['tree_id'],
            'actor_topic_id': topic['topic_id'], 'actor_conversation_ref': 'task'}
        self.request['source'] = {'kind': 'discussion', 'attachment': attachment}
        path = Path(topic['topic_document_path']).relative_to(self.root).as_posix()
        head, index = self.git('rev-parse', 'HEAD'), self.git('ls-files', '--stage')
        intent = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'purpose': 'freeze', 'authorization': 'controller:freeze', 'base_ref': 'HEAD'})
        frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'freeze', 'entry': self.request, 'intent': intent})
        self.assertEqual((self.git('rev-parse', 'HEAD'), self.git('ls-files', '--stage')), (head, index))
        unauthorized = {'protocol': producer.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'requirement': frozen, 'target': {'repository': str(self.root), 'branch': 'main'}, 'owned_paths': [path],
            'authorization': 'controller:deliver'}
        self.assert_code('authority_missing', lambda: producer.handle(unauthorized))
        def mutate(operation, **values):
            current = entry.topic_read(str(self.root), attachment)
            return discussion_protocol.handle({'protocol_version': 1, 'project_path': str(self.root), **attachment,
                'operation': operation, 'expected_ledger_revision': current['ledger_revision'],
                'expected_topic_revision': current['record_revision'], 'idempotency_key': str(uuid.uuid4()), **values})
        phase = mutate('prepare-wrapper-phase-run', from_phase=0, to_phase=1, route='0->1', carrier_kind='current-problem-framing',
            source_checkpoint_id=frozen['checkpoint']['checkpoint_id'], flow_mode='stepwise',
            flow_mode_source='explicit-stage-confirmation', scope=['requirements'])
        fields = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        mutate('authorize-phase-carrier', **fields, carrier_ref='task')
        mutate('claim-phase-carrier', **fields, carrier_ref='task', source_checkpoint_id=frozen['checkpoint']['checkpoint_id'],
            source_checkpoint_identity=frozen['commit'])
        mutate('phase-ready', **fields, carrier_ref='task', evidence=phase['evidence'])
        mutate('phase-activate', **fields, evidence=phase['evidence'])
        mutate('claim-phase-completion', **fields, carrier_ref='task', evidence=phase['evidence'])
        ledger = Path(topic['ledger_path']).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            request = {'protocol': producer.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
                'requirement': frozen, 'target': {'repository': str(self.root), 'branch': 'main'}, 'owned_paths': [path],
                'authorization': 'controller:deliver'}
            delivered = progress.handle(Path(temporary).resolve() / 'state.json', {'protocol': progress.PROTOCOL,
                'operation': 'deliver-requirement', 'expected_revision': 0, 'data': {'request': request}})
        self.assertEqual(delivered['status'], 'delivery-ready', delivered)
        self.assertEqual(delivered['result']['source_commit'], frozen['commit'])
        self.assertEqual(Path(topic['ledger_path']).read_bytes(), ledger)
        self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'), path)
        self.assertEqual(requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'verify',
            'entry': self.request, 'checkpoint_id': frozen['checkpoint']['checkpoint_id']}), frozen)
        mutate('complete-phase-run', **fields, evidence=phase['evidence'])
        final = mutate('finalize-phase-run', **fields, evidence=phase['evidence'])
        self.assertEqual(final['current_phase'], 1)

    def test_non_git_checkpoint_never_claims_git_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            entry.os.getcwd.return_value = str(root)
            topic = discussion_protocol.handle({'protocol_version': 1, 'operation': 'bootstrap', 'project_path': str(root),
                'entry_mode': 'explicit-skill', 'conversation_ref': 'task', 'idempotency_key': str(uuid.uuid4()), 'root_slug': 'snapshot'})
            attachment = {'project_id': topic['project_id'], 'tree_id': topic['tree_id'],
                'actor_topic_id': topic['topic_id'], 'actor_conversation_ref': 'task'}
            self.request['host']['project_path'] = str(root)
            self.request['source'] = {'kind': 'discussion', 'attachment': attachment}
            self.request['target'] = None
            intent = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
                'purpose': 'freeze', 'authorization': 'controller:freeze', 'base_ref': 'HEAD'})
            frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'freeze', 'entry': self.request, 'intent': intent})
            self.assertIsNone(frozen['commit'])
            request = {'protocol': producer.PROTOCOL, 'operation': 'prepare', 'entry': self.request, 'requirement': frozen,
                'target': {'repository': str(self.root), 'branch': 'main'}, 'owned_paths': [frozen['requirement_identity']['path']],
                'authorization': 'controller:deliver'}
            self.assert_code('git_required', lambda: producer.handle(request))

    def test_pending_document_write_blocks_delivery_before_any_git_write(self):
        topic = discussion_protocol.handle({'protocol_version': 1, 'operation': 'bootstrap', 'project_path': str(self.root),
            'entry_mode': 'explicit-skill', 'conversation_ref': 'task', 'idempotency_key': str(uuid.uuid4()), 'root_slug': 'pending'})
        attachment = {'project_id': topic['project_id'], 'tree_id': topic['tree_id'],
            'actor_topic_id': topic['topic_id'], 'actor_conversation_ref': 'task'}
        self.request['source'] = {'kind': 'discussion', 'attachment': attachment}
        intent = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'purpose': 'freeze', 'authorization': 'controller:freeze', 'base_ref': 'HEAD'})
        frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'freeze', 'entry': self.request, 'intent': intent})
        current = entry.topic_read(str(self.root), attachment)
        discussion_protocol.handle({'protocol_version': 1, 'operation': 'prepare-topic-update', 'project_path': str(self.root),
            **attachment, 'expected_ledger_revision': current['ledger_revision'], 'expected_topic_revision': current['record_revision'],
            'idempotency_key': str(uuid.uuid4()), 'mutation': {'type': 'confirm-decision', 'summary': 'Pending decision', 'rationale': 'Confirmed'}})
        before = self.git('rev-parse', 'HEAD'), self.git('ls-files', '--stage')
        request = {'protocol': producer.PROTOCOL, 'operation': 'prepare', 'entry': self.request, 'requirement': frozen,
            'target': {'repository': str(self.root), 'branch': 'main'}, 'owned_paths': [frozen['requirement_identity']['path']],
            'authorization': 'controller:deliver'}
        self.assert_code('source_changed', lambda: producer.handle(request))
        self.assertEqual((self.git('rev-parse', 'HEAD'), self.git('ls-files', '--stage')), before)


if __name__ == '__main__':
    unittest.main()
