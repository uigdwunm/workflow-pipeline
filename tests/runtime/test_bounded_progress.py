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
    def recover_implementation_with_closure(self, *, standalone=False, rejected=None):
        implementation = self.accepted_implementation_for_closure(standalone=standalone)
        self.old_implementation_footer = json.loads(implementation['completion']['stage_result']['handoff_json'])['accepted_transfer']
        original = self.state()
        self.original_stop = copy.deepcopy(original['host']['last_stop'])
        original_subjects = {ref['sha256'] for ref in progress.state_store.StateStore(self.checkpoint).open_current().root['subjects'].values()}
        accepted = original['accepted']
        closure = self.input_for(4, accepted)
        closure['predecessor'] = json.loads(implementation['completion']['stage_result']['handoff_json'])['accepted_transfer']
        self.context = self.context_for(4)
        self.begin('continuous', closure)
        self.invoke('observe', self.observation(ref='native:closure'))
        (self.flow / 'CHANGELOG.md').write_text('retained closure documentation\n')
        self.flow_git('add', 'CHANGELOG.md')
        self.flow_git('commit', '-qm', 'retained closure documentation')
        (self.root / 'other-task.md').write_text('parallel delivery\n')
        self.git('add', 'other-task.md')
        self.git('commit', '-qm', 'parallel delivery')
        target = self.git('rev-parse', 'HEAD')
        observed = self.observation('stopped', 'result', ref='native:closure')
        observed['closure'] = {'ref': 'native:closure', 'candidate': accepted['payload']['candidate_commit'],
            'merge': None, 'ancestor_verified': False, 'changed_paths': [], 'binding': self.binding,
            'checks': [], 'worktree_removed': False, 'branch_removed': False,
            'disposition': 'deleted', 'disposition_verified': False,
            'implementation_problem': 'target advanced before publication'}
        if rejected == 'unknown':
            observed['receipt']['status'] = 'unknown'
            observed.pop('lifecycle')
        problem = self.invoke('observe', observed)
        self.assertEqual((problem.get('next_action') or {}).get('operation'), 'implementation-recovery', problem)
        if rejected == 'dirty':
            (self.flow / 'CHANGELOG.md').write_text('uncommitted closure mutation\n')
        if rejected == 'protected':
            (self.root / self.requirement_path).write_text('changed protected source\n')
            self.git('add', self.requirement_path)
            self.git('commit', '-qm', 'changed protected source')
        opened = []
        original_open = Path.open
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        def tracked_open(path, *args, **kwargs):
            if path == self.checkpoint or path.is_relative_to(directory):
                opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            recovered = self.invoke('implementation-recovery', {'reference': 'controller:recovery',
                'closure_digest': self.state()['handoff']['digest']})
        if rejected is not None:
            self.assertEqual(self.state()['stage'], 4, recovered)
            return recovered
        self.assertFalse(original_subjects & {path.name for path in opened})
        self.assertLessEqual(sum(path.stat().st_size for path in opened), progress.state_store.READ_LIMIT)
        self.assertLessEqual(self.checkpoint.stat().st_size, progress.state_store.ROOT_LIMIT)
        self.assertEqual(self.state()['stage'], 3, recovered)
        self.assertEqual((recovered.get('next_action') or {}).get('operation'), 'inspect-host-state', recovered)
        self.assertEqual(self.state()['control']['context']['carrier'], original['control']['context']['carrier'])
        self.assertEqual(self.state()['scope_authority'], original['scope_authority'])
        self.assertEqual(self.state()['history'], [])
        self.assertEqual((self.flow / 'CHANGELOG.md').read_text(), 'retained closure documentation\n')
        self.assertEqual(self.git('rev-parse', 'HEAD'), target)
        continued = self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        self.assertEqual(continued['next_action']['operation'], 'continue-host', continued)
        self.assertEqual(continued['next_action']['payload']['ref'], 'native:dispatcher')
        return accepted

    def test_inline_recovery_rejects_dirty_closure_documents(self):
        result = self.recover_implementation_with_closure(rejected='dirty')
        self.assertEqual(result['error']['code'], 'candidate_changed', result)

    def test_inline_recovery_rejects_changed_protected_target(self):
        result = self.recover_implementation_with_closure(rejected='protected')
        self.assertEqual(result['error']['code'], 'source_changed', result)

    def test_inline_recovery_rejects_unknown_closure_writer(self):
        result = self.recover_implementation_with_closure(rejected='unknown')
        self.assertEqual(result['error']['code'], 'host_action_pending', result)

    def test_inline_recovery_rejects_old_response_and_corrupted_stop_proof(self):
        self.recover_implementation_with_closure()
        old = self.observation('idle', 'result', ref='native:dispatcher')
        old['provenance']['response_ref'] = self.original_stop['provenance']['response_ref']
        result = self.invoke('observe', old)
        self.assertEqual(result['error']['code'], 'host_provenance_conflict', result)
        reference = self.state()['final_integration_recovery']['closure_handoff']['stop_ref']
        digest = reference['sha256']
        path = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'objects' / digest[:2] / digest[2:4] / digest
        path.write_bytes(b'corrupted closure stop proof')
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('inspect')

    def test_inline_recovery_keeps_dispatcher_and_committed_closure(self):
        self.recover_implementation_with_closure()

    def test_inline_recovery_revalidates_real_allocation_and_replaces_delivery(self):
        self.repair_recovered_implementation(self.recover_implementation_with_closure())

    def test_inline_recovery_without_readiness_revalidates_original_authority(self):
        self.repair_recovered_implementation(self.recover_implementation_with_closure(standalone=True))

    def test_inline_recovery_second_round_retains_previously_accepted_closure_paths(self):
        self.repair_recovered_implementation(self.recover_implementation_with_closure())
        prior = transfer.handoff.predecessor_facts(self.state()['handoff']['predecessor'])
        self.invoke('observe', self.observation(ref='native:closure'))
        observed = self.observation('stopped', 'result', ref='native:closure')
        observed['closure'] = {'ref': 'native:closure', 'candidate': prior['payload']['candidate_commit'],
            'merge': None, 'ancestor_verified': False, 'changed_paths': [], 'binding': self.binding,
            'checks': [], 'worktree_removed': False, 'branch_removed': False,
            'disposition': 'deleted', 'disposition_verified': False,
            'implementation_problem': 'second prepublication repair'}
        problem = self.invoke('observe', observed)
        self.assertEqual(problem['next_action']['operation'], 'implementation-recovery', problem)
        recovered = self.invoke('implementation-recovery', {'reference': 'controller:second-recovery',
            'closure_digest': self.state()['handoff']['digest']})
        self.assertEqual(self.state()['stage'], 3, recovered)
        self.assertEqual(self.state()['history'], [])
        self.assertEqual(len(progress.state_store.StateStore(self.checkpoint).open_current().root['subjects']), 1)
        self.assertEqual(self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
            ['next_action']['operation'], 'continue-host')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        captured = self.invoke('control', {'action': 'capture-integration',
            'evidence': {'reference': 'controller:second-integration'},
            'receipt': {'controller_ref': 'task', 'reference': 'controller:second-integration'}})
        self.assertNotEqual(captured['status'], 'blocked', captured)
        self.old_implementation_footer = self.latest_implementation_footer
        self.repair_recovered_implementation(prior, suffix='-second', captured_already=True)
        self.assertEqual(self.state()['history'], [])
        self.assertEqual(len(progress.state_store.StateStore(self.checkpoint).open_current().root['subjects']), 1)

    def repair_recovered_implementation(self, original, suffix='-repair', *, captured_already=False):
        self.flow_git('merge', '--no-edit', 'main')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        if not captured_already:
            captured = self.invoke('control', {'action': 'capture-integration',
                'evidence': {'reference': 'controller:merged' + suffix},
                'receipt': {'controller_ref': 'task', 'reference': 'controller:merged' + suffix}})
            self.assertNotEqual(captured['status'], 'blocked', captured)
        with patch.dict(self.settings, thread_id='dispatcher-runtime'):
            execution = self.inherited_execution_input()
            prepared = self.invoke('allocation', {'allocation_id': 'repair',
                'operation': 'prepare', 'handoff': execution})
            self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)
            slot = self.allocation_slot('repair')
            bound = self.invoke('allocation', {'allocation_id': 'repair', 'operation': 'bind',
                'receipt': {**self.receipt(slot['record'], ref='native:repair'), 'receipt_ref': 'host:repair-bind' + suffix}})
            self.assertIsNotNone(bound['next_action'], bound)
            content = ("print('repaired accepted implementation" + suffix + "')\n").encode()
            (self.flow / 'impl.py').write_bytes(content)
            slot = self.allocation_slot('repair')
            received = self.invoke('allocation', {'allocation_id': 'repair', 'operation': 'receive',
                'receipt': {**self.receipt(slot['record'], 'stopped', 'result', ref='native:repair'),
                    'receipt_ref': 'host:repair-result' + suffix},
                'result': {'delivery_id': 'repair-result', 'status': 'completed', 'payload': {
                    'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                    'tests': ['repaired behavior passed'], 'write_release': bound['next_action']['result']['write_release'],
                    'plan_refs': execution['semantic']['plan_refs'],
                    'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
            self.assertEqual(received['next_action']['result']['status'], 'received', received)
            self.assertEqual(self.invoke('allocation', {'allocation_id': 'repair', 'operation': 'accept',
                'decision': {'reference': 'dispatcher:repair' + suffix}})['next_action']['result']['status'], 'accepted')
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'repair accepted implementation')
        candidate = self.flow_git('rev-parse', 'HEAD')
        accepted = self.accept_current({'artifacts': ['impl.py'], 'checks': ['repaired behavior passed'],
            'candidate_commit': candidate, 'review': {axis: {'reviewer_ref': axis}
                for axis in ('standards', 'spec')}}, 'native:dispatcher', fixture_execution=False, evidence_suffix=suffix)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        self.assertNotEqual(self.state()['accepted']['digest'], original['digest'])
        self.latest_implementation_footer = json.loads(accepted['completion']['stage_result']['handoff_json'])['accepted_transfer']
        self.assertEqual(progress.read_projection(self.checkpoint, projection='delivery').record[progress.KEY]
            ['accepted']['payload']['candidate_commit'], candidate)
        with self.assertRaises(transfer.entry.PreparationError):
            progress.implementation_acceptance(self.old_implementation_footer)
        self.context = self.context_for(4)
        closure = self.input_for(4, self.state()['accepted'])
        started = self.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host', started)
        self.assertEqual((self.flow / 'CHANGELOG.md').read_text(), 'retained closure documentation\n')

    def prepare_design_result(self, mode):
        if not getattr(self, 'two_tickets', False):
            return super().prepare_design_result(mode)
        planning_paths = ['docs/peer.md', 'docs/spec.md']
        self.input['scope']['owned_paths'] = planning_paths
        self.input['authorization']['scope_digest'] = transfer.entry.digest(self.input['scope'])
        self.begin(mode)
        self.invoke('observe', self.observation())
        (self.flow / 'docs/spec.md').write_text(self.planning_spec())
        peer_spec = self.planning_spec().replace('FEP-01', 'FEP-02').replace('FP1-01', 'FP2-01').replace('impl.py', 'peer.py')
        if getattr(self, 'dependent_ticket', False):
            peer_spec = peer_spec.replace('**Blocked by:** None', '**Blocked by:** FEP-01').replace(
                '"blocked_by": []', '"blocked_by": ["FEP-01"]')
        (self.flow / 'docs/peer.md').write_text(peer_spec)
        self.flow_git('add', *planning_paths)
        self.flow_git('commit', '-qm', 'two approved Tickets')
        candidate = self.flow_git('rev-parse', 'HEAD')
        basis = {'commit': candidate, 'path': 'docs/spec.md', 'section': 'approved design'}
        state = self.state()
        proposed = self.invoke('scope-propose', {'proposal_id': 'proposal-1', 'expected_revision': 0,
            'candidate_commit': candidate, 'manifest': [
                {'path': path, 'owner': owner, 'kind': kind, 'operation': 'add', 'basis': basis}
                for path, owner, kind in [('CHANGELOG.md', 'closure', 'document'),
                    ('impl.py', 'implementation', 'source'), ('peer.py', 'implementation', 'source')]],
            'implementation_required': True, 'requirement_identity': state['handoff']['requirement_identity'],
            'binding': state['handoff']['binding'], 'previous_scope_revision': 0})
        self.assertEqual(proposed['next_action']['operation'], 'scope-proposed', proposed)
        observed = self.observation('stopped', 'result')
        observed['publication_candidate'] = {'candidate_commit': candidate, 'artifacts': planning_paths,
                                            'checks': ['two Ticket design readiness']}
        pending = self.invoke('observe', observed)['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:planning-readiness'})
        self.seal_design_scope(proposed['next_action']['proposal']['digest'])
        published = self.invoke('publication', {'candidate_commit': candidate,
                                               'reference': 'controller:planning-readiness'})
        self.assertTrue(published['next_action']['publication']['result']['ok'], published)
        return {'artifacts': planning_paths, 'checks': ['two Ticket design readiness'],
            'planning_commit': candidate, 'planning_merge_commit': self.flow_git('rev-parse', 'HEAD'),
            'planning_paths': planning_paths}

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

    def complete_closure_publication(self):
        implementation = self.state()['handoff']['predecessor']['accepted_digest']
        accepted = transfer.handoff.predecessor_facts(self.state()['handoff']['predecessor'])
        candidate = accepted['payload']['candidate_commit']
        (self.flow / 'CHANGELOG.md').write_text('documented accepted implementation\n')
        self.flow_git('add', 'CHANGELOG.md')
        self.flow_git('commit', '-qm', 'closure documentation')
        tip = self.flow_git('rev-parse', 'HEAD')
        self.assertNotEqual(tip, candidate)
        self.ready_publication(tip, 'controller:closure', 'native:closure')
        published = self.invoke('publication', {'candidate_commit': tip,
            'expected_target_head': self.git('rev-parse', 'HEAD'), 'reference': 'controller:closure'})
        self.assertTrue(published['next_action']['publication']['result']['ok'], published)
        payload = copy.deepcopy(self.state()['publication']['completion_payload'])
        self.assertEqual(payload['candidate_commit'], candidate)
        self.assertEqual(self.git('rev-parse', payload['merge_commit'] + '^2'), tip)
        received = self.invoke('receive-publication')
        self.assertEqual(received['pending']['kind'], 'acceptance', received)
        pending = received['pending']
        result = self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:final-acceptance'})
        self.assertEqual(result['status'], 'accepted', result)
        self.assertTrue(result['workflow_completion']['completed'], result)
        self.assertEqual(self.state()['accepted']['payload'], payload)
        self.assertEqual(self.state()['handoff']['predecessor']['accepted_digest'], implementation)
        self.assertFalse(self.flow.exists())
        for operation in ('inspect', 'resume'):
            self.assertEqual(self.invoke(operation)['workflow_completion'], result['workflow_completion'])
        self.assertEqual(json.loads(result['completion']['stage_result']['handoff_json'])['merge_commit'],
                         payload['merge_commit'])
        return result

    def completed_closure(self):
        implementation = self.accepted_implementation_for_closure()
        prior = progress.state_store.StateStore(self.checkpoint).open_current()
        self.completion_prior_subjects = {ref['sha256'] for ref in prior.root['subjects'].values()}
        closure = self.input_for(4, self.state()['accepted'])
        closure['predecessor'] = json.loads(implementation['completion']['stage_result']['handoff_json'])['accepted_transfer']
        self.context = self.context_for(4)
        self.begin('continuous', closure)
        self.invoke('observe', self.observation(ref='native:closure'))
        return self.complete_closure_publication()

    def test_completion_publishes_real_closure_and_consumes_exact_proofs(self):
        result = self.completed_closure()
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            if path == self.checkpoint or path.is_relative_to(directory):
                opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            self.assertEqual(self.invoke('inspect')['workflow_completion'], result['workflow_completion'])
        self.assertFalse(self.completion_prior_subjects & {path.name for path in opened})
        read_bytes = sum(path.stat().st_size for path in opened)
        self.assertLessEqual(read_bytes, progress.state_store.READ_LIMIT)
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertEqual(self.state()['history'], [])
        self.assertLessEqual(self.checkpoint.stat().st_size, progress.state_store.ROOT_LIMIT)
        if os.environ.get('T01_COMPLETION_METRICS_PATH'):
            Path(os.environ['T01_COMPLETION_METRICS_PATH']).write_text(json.dumps({
                'root_bytes': self.checkpoint.stat().st_size,
                'stage_subject_bytes': current.root['subjects'][current.root['metadata']['stage_subject']]['size'],
                'inspect_read_bytes': read_bytes, 'inspect_read_files': len(opened),
                'footer_bytes': len(result['completion']['stage_result']['handoff_json'].encode()),
                'merge_commit': result['workflow_completion']['merge_commit']}, indent=2) + '\n')
        predecessor = current.root['metadata']['implementation_predecessor']
        facts, _, _ = progress.read_implementation_delivery(self.checkpoint, predecessor)
        for reference in (predecessor['reference'], facts['design_predecessor']['reference']):
            digest = reference['sha256']
            proof_path = directory / 'objects' / digest[:2] / digest[2:4] / digest
            original = proof_path.read_bytes()
            proof_path.write_bytes(b'corrupt accepted predecessor')
            try:
                with self.assertRaises(transfer.entry.PreparationError):
                    self.invoke('inspect')
            finally:
                proof_path.write_bytes(original)
        self.assertTrue(self.invoke('inspect')['workflow_completion']['completed'])
        self.flow.mkdir()
        (self.flow / 'personal.txt').write_text('unrelated replacement')
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('inspect')
        self.assertEqual((self.flow / 'personal.txt').read_text(), 'unrelated replacement')

    def test_completion_rejects_current_outer_stop_revocation(self):
        self.completed_closure()
        root = progress.state_store.StateStore(self.checkpoint).open_current()
        for gate in ('pause', 'cancel', 'lost', 'lookup_blocked', 'sequence', 'instance'):
            with self.subTest(gate=gate):
                self.delivery_outer_gate(gate, root.root['outer'])
                with self.assertRaises(transfer.entry.PreparationError):
                    self.invoke('inspect')

    def test_closure_entry_same_controller_keeps_exact_delivery(self):
        completed = self.accepted_implementation_for_closure()
        accepted = self.state()['accepted']
        original = progress.state_store.StateStore(self.checkpoint).open_current()
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            delivery = progress.read_projection(self.checkpoint, projection='delivery')
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        actual_reads = [path for path in opened if path == self.checkpoint or path.is_relative_to(directory)]
        actual_bytes = sum(path.stat().st_size for path in actual_reads)
        self.assertLessEqual(actual_bytes, progress.state_store.READ_LIMIT)
        original_subjects = {reference['sha256'] for reference in original.root['subjects'].values()}
        self.assertFalse(original_subjects & {path.name for path in actual_reads})
        self.assertNotIn('control', delivery.record[progress.KEY])
        self.assertNotIn('allocations', delivery.record[progress.KEY])
        self.assertLessEqual(delivery.bytes_read, progress.state_store.READ_LIMIT)
        footer = json.loads(completed['completion']['stage_result']['handoff_json'])['accepted_transfer']
        self.assertEqual(footer['accepted_digest'], accepted['digest'])
        self.assertNotIn('handoff', footer)
        closure = self.input_for(4, accepted)
        closure['predecessor'] = footer
        for field, replacement in [('run_id', 'another-run'), ('accepted_digest', '0' * 64),
                                    ('checkpoint_path', str(self.checkpoint.with_name('missing.json')))]:
            invalid = copy.deepcopy(closure)
            body = transfer.handoff.unseal(footer)
            body[field] = replacement
            invalid['predecessor'] = transfer.handoff.seal(body)
            before = self.checkpoint.read_bytes()
            with self.assertRaises(transfer.entry.PreparationError):
                self.invoke('start', {'handoff': invalid})
            self.assertEqual(self.checkpoint.read_bytes(), before)
        self.context = self.context_for(4)
        started = self.begin('continuous', closure)
        self.assertEqual(started['next_action']['operation'], 'invoke-host', started)
        before = self.checkpoint.read_bytes()
        forged_control = copy.deepcopy(self.state()['control'])
        forged_control['context']['handoff_progress']['verification']['view']['review_sources']['spec']['evidence_digest'] = '0' * 64
        with self.assertRaisesRegex((transfer.entry.PreparationError, transfer.control.ControlError), 'checkpoint pin'):
            transfer.dispatch.handle({'protocol': transfer.handoff.PROTOCOL, 'operation': 'bind',
                'record': self.state()['dispatch'], 'control': forged_control,
                'receipt': self.receipt(self.state()['dispatch'], ref='native:closure')})
        with self.assertRaisesRegex((transfer.entry.PreparationError, transfer.control.ControlError), 'exact committed'):
            progress.control_git.verified_transition({'repository': str(self.flow),
                'baseline': self.state()['handoff']['scope']['baseline'],
                'request': {'schema_version': forged_control['context']['schema_version'],
                    'action': 'closure-bound', 'actor_ref': 'task', 'context': forged_control['context'],
                    'evidence': {'ref': 'native:closure',
                        'attempt': self.state()['control']['context']['carrier']['attempt']}}})
        request = {'schema_version': forged_control['context']['schema_version'],
            'action': 'closure-bound', 'actor_ref': 'task', 'context': self.state()['control']['context'],
            'evidence': {'ref': 'native:closure',
                'attempt': self.state()['control']['context']['carrier']['attempt']}}
        verified = progress.control_git.verified_transition({'repository': str(self.flow),
            'baseline': self.state()['handoff']['scope']['baseline'], 'request': request})
        self.assertEqual(verified['context']['carrier']['ref'], 'native:closure')
        self.assertEqual(verified['context']['handoff_progress']['verification']['kind'],
                         'implementation-verification-ref')
        wrong_reference = copy.deepcopy(request)
        wrong_reference['context']['handoff_progress']['verification']['verification_ref']['sha256'] = '0' * 64
        with self.assertRaisesRegex((transfer.entry.PreparationError, transfer.control.ControlError), 'exact committed'):
            progress.control_git.verified_transition({'repository': str(self.flow),
                'baseline': self.state()['handoff']['scope']['baseline'], 'request': wrong_reference})
        self.assertEqual(self.checkpoint.read_bytes(), before)
        bound = self.invoke('observe', self.observation(ref='native:closure'))
        self.assertEqual(bound['status'], 'active', bound)
        self.assertEqual(self.invoke('inspect')['status'], 'active')
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertEqual(current.run_id, original.run_id)
        self.assertEqual(self.state()['stage'], 4)
        self.assertEqual(self.state()['history'], [])
        self.assertLessEqual(self.checkpoint.stat().st_size, progress.state_store.ROOT_LIMIT)
        reference = self.state()['handoff']['predecessor']
        self.assertEqual(reference, current.root['metadata']['implementation_predecessor'])
        self.assertNotIn('handoff', reference)
        facts, _, store = progress.read_implementation_delivery(self.checkpoint, reference)
        self.assertEqual(facts['source_revision'], original.root['metadata']['revision'])
        self.assertEqual(facts['payload']['candidate_commit'], accepted['payload']['candidate_commit'])
        self.assertNotIn('verification', facts['payload'])
        proof = store.read_evidence(facts['verification_ref'], run_id=current.run_id)['value']
        self.assertEqual(proof['attempt_count'], 1)
        self.assertEqual(len(proof['attempt_ids_digest']), 64)
        self.assertNotIn('prior_attempt_ids', proof)
        self.assertNotIn('attempts', proof)
        self.assertIn('final_attempt_ref', proof)
        self.assertNotIn('history', facts)
        self.assertEqual(facts['design_predecessor'], original.root['metadata']['design_predecessor'])
        observed = transfer.handoff.accepted_stage3_progress(reference, str(self.checkpoint))
        self.assertEqual(observed['candidate'], accepted['payload']['candidate_commit'])
        self.assertEqual(observed['attempts'], accepted['payload']['verification']['attempts'])
        self.assertIn('stage_input', self.state()['dispatch'])
        self.assertNotIn('payload', self.state()['dispatch']['request'])
        closure_verification = self.state()['control']['context']['handoff_progress']['verification']
        self.assertEqual(closure_verification.get('kind'), 'implementation-verification-ref')
        self.assertNotIn('raw', json.dumps(closure_verification))
        self.assertNotIn('attempts', closure_verification)
        proof_path = directory / 'objects' / facts['verification_ref']['sha256'][:2] / facts['verification_ref']['sha256'][2:4] / facts['verification_ref']['sha256']
        proof_bytes = proof_path.read_bytes()
        proof_path.write_bytes(b'corrupt verification')
        try:
            with self.assertRaises(transfer.entry.PreparationError):
                transfer.handoff.verify(self.state()['handoff'], checkpoint_path=str(self.checkpoint))
        finally:
            proof_path.write_bytes(proof_bytes)
        if os.environ.get('T01_CLOSURE_ENTRY_METRICS_PATH'):
            Path(os.environ['T01_CLOSURE_ENTRY_METRICS_PATH']).write_text(json.dumps({
                'root_bytes': self.checkpoint.stat().st_size, 'delivery_bytes': reference['reference']['size'],
                'stage3_delivery_read_bytes': delivery.bytes_read, 'stage3_delivery_read_files': delivery.files_read,
                'actual_delivery_read_bytes': actual_bytes, 'actual_delivery_read_files': len(actual_reads),
                'stage4_predecessor_read_bytes': store.bytes_read, 'stage4_predecessor_read_files': store.files_read,
                'stage4_subject_bytes': current.root['subjects'][current.root['metadata']['stage_subject']]['size'],
                'accepted_candidate': facts['payload']['candidate_commit'],
                'source_revision': facts['source_revision']}, indent=2) + '\n')

    def test_closure_entry_rejects_unknown_dispatcher_stop(self):
        self.accepted_implementation_for_closure()
        closure = self.input_for(4, self.state()['accepted'])
        observed = self.invoke('observe', self.observation('running', 'result', ref='native:dispatcher'))
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('start', {'handoff': closure})
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertFalse(self.state()['stopped'])

    def delivery_outer_gate(self, gate, baseline):
        outer = copy.deepcopy(baseline)
        if gate in {'pause', 'cancel'}:
            outer['runner_request'] = {'operation': gate, 'request_id': 'outer-' + gate, 'stage': 'stage' + str(self.state()['stage'])}
        elif gate != 'restore':
            snapshot = self.state()['lifecycle_snapshot']
            transport = outer.setdefault('transport', {'instance': snapshot['instance'],
                'native_event_sequence': snapshot['sequence'], 'state': 'live'})
            if gate == 'lost':
                transport['state'] = 'lost'
            elif gate == 'lookup_blocked':
                transport['lookup_blocked'] = 'original lookup failed'
            elif gate == 'sequence':
                transport['native_event_sequence'] = snapshot['sequence'] + 1
            elif gate == 'instance':
                transport['instance'] = 'another-native-host'
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        with progress.record_lock(self.checkpoint):
            progress.save_outer_record(self.checkpoint, outer, expected_revision=current.revision)

    def test_closure_entry_review_outer_cas_revokes_current_delivery(self):
        completed = self.accepted_implementation_for_closure()
        original = self.state()
        root = progress.state_store.StateStore(self.checkpoint).open_current()
        footer = json.loads(completed['completion']['stage_result']['handoff_json'])['accepted_transfer']
        predecessor = progress.implementation_predecessor(self.checkpoint)
        closure = self.input_for(4, original['accepted'])
        for gate in ('pause', 'cancel', 'lost', 'lookup_blocked', 'sequence', 'instance'):
            with self.subTest(gate=gate):
                self.delivery_outer_gate(gate, root.root['outer'])
                changed = progress.state_store.StateStore(self.checkpoint).open_current()
                self.assertEqual(changed.root['metadata'], root.root['metadata'])
                self.assertEqual(changed.root['delivery'], root.root['delivery'])
                self.assertEqual(changed.root['subjects'], root.root['subjects'])
                self.assertGreater(changed.revision, root.revision)
                readers = (
                    lambda: progress.read_projection(self.checkpoint, projection='delivery'),
                    lambda: transfer.handoff.predecessor_facts(footer),
                    lambda: transfer.handoff.render(original['accepted'], checkpoint_path=str(self.checkpoint)),
                    lambda: transfer.handoff.prepare(closure, checkpoint_path=str(self.checkpoint)),
                    lambda: progress.closure_delivery_view(predecessor))
                for read in readers:
                    with self.assertRaises(transfer.entry.PreparationError):
                        read()
                inspected = self.invoke('inspect')
                self.assertFalse(inspected['downstream_ready'])
                self.assertNotIn('completion', inspected)
        self.delivery_outer_gate('restore', root.root['outer'])
        self.assertEqual(transfer.handoff.predecessor_facts(footer)['digest'], original['accepted']['digest'])
        self.context = self.context_for(4)
        self.begin('continuous', closure)
        self.invoke('observe', self.observation(ref='native:closure'))
        stage4 = progress.state_store.StateStore(self.checkpoint).open_current()
        self.delivery_outer_gate('pause', stage4.root['outer'])
        # Consumed Stage-3 facts remain readable, but grant no current Stage-4 operation.
        prior, _, _ = progress.read_implementation_delivery(self.checkpoint, self.state()['implementation_predecessor'])
        self.assertEqual(prior['accepted']['digest'], original['accepted']['digest'])
        with self.assertRaisesRegex(transfer.entry.PreparationError, 'resume|stop|pause|suspend'):
            self.invoke('publication', {'candidate_commit': prior['payload']['candidate_commit'],
                'expected_target_head': self.git('rev-parse', 'HEAD'), 'reference': 'blocked:closure'})
        self.assertNotIn('publication', self.state())

    def test_closure_entry_review_public_runner_rejects_outer_lookup_failure(self):
        self.runner_transition('continuous', closure_entry=True, outer_gate='lookup_blocked')

    def test_transition_two_tickets_keep_peer_in_stop_barrier(self):
        self.two_tickets = True
        accepted = self.finish_design('continuous')
        old = progress.state_store.StateStore(self.checkpoint).open_current()
        successor = self.staged_successor(accepted)
        self.context = self.context_for(3)
        self.begin('continuous', successor)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        tickets = self.state()['scope_authority']['execution_plan']['catalog']['tickets']
        self.assertEqual({item['ticket_id'] for item in tickets}, {'FEP-01', 'FEP-02'})
        first = self.execution_input()
        prepared = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'prepare', 'handoff': first})
        self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)
        one = self.allocation_slot('slice-one', 'FEP-01')
        bound = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(one['record'], ref='native:executor-one')})
        self.assertEqual(bound['next_action']['result']['status'], 'bound')
        peer = self.inherited_execution_input()
        peer['scope']['owned_paths'] = ['peer.py']
        peer['semantic']['plan_refs'] = [ref for ref in peer['semantic']['plan_refs'] if ref['ticket_id'] == 'FEP-02']
        peer['authorization']['scope_digest'] = transfer.entry.digest(peer['scope'])
        prepared = self.invoke('allocation', {'allocation_id': 'slice-two', 'operation': 'prepare', 'handoff': peer})
        self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)
        two = self.allocation_slot('slice-two', 'FEP-02')
        receipt = self.receipt(two['record'], ref='native:executor-two')
        receipt['raw']['output'] = 'peer raw evidence ' * 1000
        bound_peer = self.invoke('allocation', {'allocation_id': 'slice-two', 'operation': 'bind', 'receipt': receipt})
        self.assertEqual(bound_peer['next_action']['result']['status'], 'bound')
        two = self.allocation_slot('slice-two', 'FEP-02')
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            scope = progress.read_projection(self.checkpoint, projection='scope')
            target = progress.read_projection(self.checkpoint, projection='transaction',
                subject_id='ticket:FEP-01:0', allocation_id='slice-one', intent=one['action']['action_id'])
            progress.read_scope_readiness(accepted['readiness'])
        self.assertNotIn(old.root['subjects'][old.root['metadata']['stage_subject']]['sha256'], {p.name for p in opened})
        self.assertNotIn(two['record']['receipts'][0]['raw_ref']['sha256'], {p.name for p in opened})
        self.assertFalse(any('archives' in p.parts for p in opened))
        self.assertFalse({'control', 'host', 'allocations'} & scope.record[progress.KEY].keys())
        self.assertNotIn('write_release', json.dumps(scope.record))
        opened.clear()
        with patch.object(Path, 'open', tracked_open):
            self.invoke('inspect')
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        recovery_reads = [p for p in opened if p == self.checkpoint or p.is_relative_to(directory)]
        recovery_bytes = sum(p.stat().st_size for p in recovery_reads)
        self.assertLessEqual(recovery_bytes, progress.state_store.READ_LIMIT)
        self.assertNotIn(two['record']['receipts'][0]['raw_ref']['sha256'], {p.name for p in recovery_reads})
        current_state = self.state()
        self.assertEqual(current_state['handoff']['predecessor']['kind'], 'design-predecessor-ref')
        self.assertNotIn('handoff', current_state['handoff']['predecessor'])
        self.assertIn('stage_input', current_state['dispatch'])
        self.assertNotIn('handoff', current_state['dispatch'])
        self.assertNotIn('payload', current_state['dispatch']['request'])
        self.assertLessEqual(scope.bytes_read + target.bytes_read, progress.state_store.READ_LIMIT)
        content = b"print('first Ticket finished')\n"
        (self.flow / 'impl.py').write_bytes(content)
        one = self.allocation_slot('slice-one', 'FEP-01')
        received = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'receive',
            'receipt': self.receipt(one['record'], 'stopped', 'result', ref='native:executor-one'),
            'result': {'delivery_id': 'first-finished', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                'tests': ['fixture boundary passed'], 'write_release': bound['next_action']['result']['write_release'],
                'plan_refs': first['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.assertEqual(received['next_action']['result']['status'], 'received')
        self.assertEqual(self.invoke('pause')['status'], 'pausing')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(self.invoke('inspect')['status'], 'pausing')
        self.assertFalse(self.state()['stopped'])
        self.assertEqual(self.allocation_slot('slice-two', 'FEP-02')['record']['request']['attempt'], two['record']['request']['attempt'])
        with self.assertRaises(transfer.entry.PreparationError):
            self.invoke('allocation', {'allocation_id': 'new-peer', 'operation': 'prepare', 'handoff': peer})
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertEqual(current.run_id, old.run_id)
        self.assertLessEqual(self.checkpoint.stat().st_size, progress.state_store.ROOT_LIMIT)
        if os.environ.get('T01_TRANSITION_METRICS_PATH'):
            Path(os.environ['T01_TRANSITION_METRICS_PATH']).write_text(json.dumps({
                'tickets': 2, 'scope_bytes_read': scope.bytes_read, 'scope_files_read': scope.files_read,
                'target_bytes_read': target.bytes_read, 'target_files_read': target.files_read,
                'root_bytes': self.checkpoint.stat().st_size, 'inspect_read_bytes': recovery_bytes,
                'inspect_files_read': len(recovery_reads),
                'predecessor_bytes': current.root['metadata']['design_predecessor']['reference']['size'],
                'current_subject_bytes': {key: ref['size'] for key, ref in current.root['subjects'].items()},
                'peer_original_receipt_bytes': two['record']['receipts'][0]['raw_ref']['size']}, indent=2) + '\n')

    def test_transition_dependency_requires_stopped_accepted_result(self):
        self.two_tickets = self.dependent_ticket = True
        accepted = self.finish_design('continuous')
        successor = self.staged_successor(accepted)
        self.context = self.context_for(3)
        self.begin('continuous', successor)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        first = self.execution_input()
        self.invoke('allocation', {'allocation_id': 'first', 'operation': 'prepare', 'handoff': first})
        slot = self.allocation_slot('first', 'FEP-01')
        bound = self.invoke('allocation', {'allocation_id': 'first', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:first')})
        peer = self.inherited_execution_input()
        peer['scope']['owned_paths'] = ['peer.py']
        peer['semantic']['plan_refs'] = [ref for ref in peer['semantic']['plan_refs'] if ref['ticket_id'] == 'FEP-02']
        peer['authorization']['scope_digest'] = transfer.entry.digest(peer['scope'])
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            transfer.handoff.prepare(peer)
        self.assertEqual(rejected.exception.code, 'execution_plan_stale')
        slot = self.allocation_slot('first', 'FEP-01')
        content = b"print('accepted dependency')\n"
        (self.flow / 'impl.py').write_bytes(content)
        result = self.invoke('allocation', {'allocation_id': 'first', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:first'),
            'result': {'delivery_id': 'first-result', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                'tests': ['dependency behavior passed'], 'write_release': bound['next_action']['result']['write_release'],
                'plan_refs': first['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.assertEqual(result['next_action']['result']['status'], 'received')
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            transfer.handoff.prepare(peer)
        self.assertEqual(rejected.exception.code, 'execution_plan_stale')
        slot = self.allocation_slot('first', 'FEP-01')
        accepted_result = self.invoke('allocation', {'allocation_id': 'first', 'operation': 'accept',
            'decision': {'delivery_digest': slot['record']['delivery']['digest'], 'reference': 'dispatcher:first-accepted'}})
        self.assertEqual(accepted_result['next_action']['result']['status'], 'accepted')
        prepared = self.invoke('allocation', {'allocation_id': 'dependent', 'operation': 'prepare', 'handoff': peer})
        self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)

    def transition_checkpoint_crash(self, when):
        accepted = self.finish_design('continuous')
        original = progress.state_store.StateStore(self.checkpoint).open_current()
        successor = self.staged_successor(accepted)
        self.context = self.context_for(3)
        original_replace = os.replace
        hit = []
        def interrupted_replace(source, target):
            if Path(target) == self.checkpoint and not hit:
                candidate = json.loads(Path(source).read_bytes())
                if candidate['workflow_progress']['stage'] == 3:
                    hit.append(candidate['revision'])
                    if when == 'after':
                        original_replace(source, target)
                    raise KeyboardInterrupt('transition root ' + when)
            return original_replace(source, target)
        with patch.object(os, 'replace', interrupted_replace):
            with self.assertRaises(KeyboardInterrupt):
                self.begin('continuous', successor)
        self.assertEqual(len(hit), 1)
        current = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertEqual(current.run_id, original.run_id)
        self.assertEqual(self.state()['stage'], 2 if when == 'before' else 3)
        if when == 'before':
            issued = self.begin('continuous', successor)
        else:
            issued = self.invoke('resume')
        self.assertEqual(issued['next_action']['operation'], 'invoke-host', issued)
        issued_action = copy.deepcopy(self.state()['host_action'])
        recovered = self.invoke('resume')
        self.assertEqual(recovered['next_action']['operation'], 'lookup-exact-action', recovered)
        self.assertEqual(self.state()['host_action'], issued_action)
        self.assertEqual(issued_action['payload']['digest'], issued['next_action']['payload']['digest'])
        self.assertNotIn('payload', issued_action['payload'])
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        self.assertEqual(self.invoke('inspect')['status'], 'active')

    def test_transition_cas_before_retains_design(self):
        self.transition_checkpoint_crash('before')

    def test_transition_cas_after_resumes_exact_dispatcher(self):
        self.transition_checkpoint_crash('after')

    def test_transition_wrong_predecessor_and_readiness_fail_closed(self):
        accepted = self.finish_design('continuous')
        successor = self.staged_successor(accepted)
        self.context = self.context_for(3)
        self.begin('continuous', successor)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        saved = self.state()['handoff']
        original = self.checkpoint.read_bytes()
        for kind in ('run', 'path', 'digest', 'object', 'revision', 'seal', 'requirement', 'worktree'):
            with self.subTest(kind=kind):
                request = {key: copy.deepcopy(saved[key]) for key in transfer.handoff.FIELDS | transfer.handoff.OPTIONAL_FIELDS if key in saved}
                request['operation'] = 'prepare'
                ref = transfer.handoff.unseal(request['predecessor'])
                if kind == 'run':
                    ref['reference']['run_id'] = 'different-run'
                elif kind == 'path':
                    ref['checkpoint_path'] = str(self.checkpoint.with_name('other.json'))
                elif kind == 'digest':
                    ref['accepted_digest'] = '0' * 64
                elif kind == 'object':
                    ref['reference']['sha256'] = '0' * 64
                elif kind == 'revision':
                    request['readiness']['checkpoint_revision'] += 1
                elif kind == 'seal':
                    request['readiness']['scope_seal'] = 'changed-seal'
                elif kind == 'requirement':
                    request['requirement']['sha256'] = '0' * 64
                else:
                    request['binding']['worktree'] = str(self.flow.parent / 'other-flow')
                request['predecessor'] = transfer.handoff.seal(ref)
                with self.assertRaises(transfer.entry.ERROR_TYPES):
                    transfer.handoff.prepare(request)
                self.assertEqual(self.checkpoint.read_bytes(), original)
        readiness = accepted['readiness']
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        ref = current.root['metadata']['design_predecessor']['reference']
        obj = store.directory / 'objects' / ref['sha256'][:2] / ref['sha256'][2:4] / ref['sha256']
        raw = obj.read_bytes()
        obj.write_bytes(raw.replace(b'accepted-design-v1', b'accepted-design-v0'))
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            progress.read_scope_readiness(readiness)
        self.assertEqual(rejected.exception.code, 'state_object_corrupt')
        self.assertEqual(self.checkpoint.read_bytes(), original)

    def test_transition_prior_baseline_receipt_cannot_be_relabelled(self):
        from test_collaboration_lifecycle import CollaborationLifecycleTests
        helper = CollaborationLifecycleTests(methodName='runTest')
        helper.f = self
        self.input['authorization']['flow_mode'] = 'continuous'
        helper.test_missing_baseline_exact_current_read_allows_publication_without_archiving()
        old_query = copy.deepcopy(self.state()['baseline_thread_queries'][0])
        pending = self.invoke('receive-publication')['pending']
        accepted = self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:accept'})
        successor = self.staged_successor(accepted)
        successor['authorization']['flow_mode'] = 'continuous'
        self.invoke('start', {'handoff': successor, 'native_host': helper.inventory()})
        self.invoke('observe', self.observation(ref='/root/dispatcher'))
        observed = self.observation('stopped', 'result', ref='/root/dispatcher')
        observed.pop('lifecycle')
        self.invoke('observe', observed)
        agents = helper.inventory()['response']['agents'] + [
            {'agent_name': '/root/dispatcher', 'agent_status': {'completed': 'dispatcher stopped'}}]
        self.invoke('lifecycle-state', helper.snapshot(helper.inventory(agents)))
        query = self.invoke('baseline-thread-query', old_query['input'])['next_action']
        replay = {**old_query['result'], 'query_id': query['query_id']}
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        predecessor = progress.read_design_predecessor(self.state()['design_predecessor'], self.checkpoint)
        index_ref = predecessor['native_source_index_ref']
        index = store.read_evidence(index_ref, run_id=current.run_id, kind='native-source-index')['value']
        raw_hashes = {ref['sha256'] for ref in index.values()}
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            rejected = self.invoke('baseline-thread-state', replay)
        reads = [path for path in opened if path == self.checkpoint or path.is_relative_to(store.directory)]
        read_bytes = sum(path.stat().st_size for path in reads)
        self.assertLessEqual(read_bytes, progress.state_store.READ_LIMIT)
        self.assertLessEqual(index_ref['size'], progress.state_store.SUBJECT_LIMIT)
        self.assertIn(index_ref['sha256'], {path.name for path in reads})
        self.assertFalse(raw_hashes & {path.name for path in reads})
        if os.environ.get('T01_TRANSITION_NATIVE_METRICS_PATH'):
            Path(os.environ['T01_TRANSITION_NATIVE_METRICS_PATH']).write_text(json.dumps({
                'read_bytes': read_bytes, 'files_read': len(reads), 'source_index_bytes': index_ref['size'],
                'index_reads': sum(path.name == index_ref['sha256'] for path in reads),
                'raw_source_objects_read': 0, 'predecessor_bytes': current.root['metadata']['design_predecessor']['reference']['size']}, indent=2) + '\n')
        self.assertEqual((rejected.get('error') or {}).get('code'), 'host_provenance_conflict', rejected)
        self.assertFalse(self.state()['stopped'])
        self.invoke('baseline-thread-state', helper.baseline_readback(query))
        self.assertTrue(self.state()['stopped'])

    def test_transition_paused_dispatcher_resumes_same_scope(self):
        accepted = self.finish_design('continuous')
        successor = self.staged_successor(accepted)
        self.context = self.context_for(3)
        self.begin('continuous', successor)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        attempt = self.state()['dispatch']['request']['attempt']
        self.invoke('pause')
        stopped = self.observation('stopped', 'result', ref='native:dispatcher')
        stopped['receipt']['raw']['resumable'] = True
        self.assertEqual(self.invoke('observe', stopped)['status'], 'paused')
        resumed = self.invoke('resume')
        if (resumed.get('next_action') or {}).get('operation') == 'inspect-host-state':
            observed = self.observation('stopped', 'result', ref='native:dispatcher')
            observed['receipt']['raw']['resumable'] = True
            resumed = self.invoke('observe', observed)
        self.assertEqual((resumed.get('next_action') or {}).get('operation'), 'continue-host', resumed)
        self.assertEqual(resumed['next_action']['payload']['ref'], 'native:dispatcher')
        self.assertEqual(self.state()['dispatch']['request']['attempt'], attempt)
        action = copy.deepcopy(self.state()['host_action'])
        again = self.invoke('resume')
        self.assertEqual(again['next_action']['operation'], 'lookup-exact-action')
        self.assertEqual(self.state()['host_action'], action)

    def test_transition_same_controller_design_to_dispatcher(self):
        accepted = self.finish_design('continuous')
        store = progress.state_store.StateStore(self.checkpoint)
        original = store.open_current()
        predecessor = self.state()['accepted']
        self.context = self.context_for(3)
        successor = self.input_for(3, predecessor)
        scope = accepted['readiness']['scope_authority']['scope']
        for key in ('downstream', 'implementation_paths', 'closure_paths'):
            successor['scope'][key] = copy.deepcopy(scope[key])
        successor['scope']['owned_paths'] = copy.deepcopy(scope['implementation_paths'])
        successor['authorization']['scope_digest'] = transfer.entry.digest(successor['scope'])
        self.begin('continuous', successor)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        inspected = self.invoke('inspect')
        self.assertEqual(self.state()['stage'], 3)
        self.assertEqual(inspected['status'], 'active', inspected)
        self.assertEqual(store.open_current().run_id, original.run_id)
        self.assertEqual(self.state()['checkpoint_path'], str(self.checkpoint))
        self.assertFalse(self.state().get('history'))
        self.assertEqual(progress.read_scope_readiness(accepted['readiness'])['stage'], 3)

    def test_allocation_review_bound_identity_rejection_keeps_original_audit(self):
        self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:original')})
        slot = self.allocation_slot('slice-one')
        bad = {'allocation_id': 'slice-one', 'operation': 'reconcile',
            'receipt': self.receipt(slot['record'], 'ready', 'lookup', ref='native:other')}
        rejected = self.invoke('allocation', bad)
        self.assertEqual(rejected['error']['code'], 'identity_mismatch')
        after = self.allocation_slot('slice-one')
        self.assertEqual(after['record'], slot['record'])
        self.assertIsNone(after['transaction'])
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        audit = store.read_evidence(after['observations'][-1], run_id=current.run_id)['value']
        self.assertEqual(audit, {'allocation_observation': bad})
        known = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'reconcile',
            'receipt': self.receipt(slot['record'], 'ready', 'lookup', ref='native:original')})
        self.assertEqual(known['next_action']['result']['status'], 'bound')

    def test_allocation_review_large_receipt_recovers_from_original_observation_ref(self):
        self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        receipt = self.receipt(slot['record'], ref='native:executor')
        receipt['raw']['output'] = 'x' * (300 * 1024)
        data = {'allocation_id': 'slice-one', 'operation': 'bind', 'receipt': receipt}
        original_replace = os.replace
        def crash_after_intent(source, target):
            if Path(target).parent.name == 'commits':
                current = self.allocation_slot('slice-one')
                if (current.get('transaction') or {}).get('operation') == 'bind':
                    raise KeyboardInterrupt('large receipt intent is durable before B dispatch')
            return original_replace(source, target)
        with patch.object(os, 'replace', crash_after_intent):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('allocation', data)
        pending = self.allocation_slot('slice-one')['transaction']
        self.assertNotIn('receipt', pending)
        self.assertNotIn('result', pending)
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        original = store.read_evidence(pending['observation_ref'], run_id=current.run_id)['value']
        self.assertEqual(original, {'allocation_observation': data})
        self.assertLess(current.root['subjects']['ticket:INLINE-TRANSFER:0']['size'], 256 * 1024)
        recovered = self.invoke('resume')['next_action']
        self.assertEqual(recovered['operation'], 'allocation-recovery')
        self.assertEqual(recovered['data'], data)
        bound = self.invoke('allocation', recovered['data'])
        self.assertEqual((bound.get('next_action') or {}).get('result', {}).get('status'), 'bound', bound)
        replay = self.invoke('allocation', data)
        self.assertTrue(replay['acknowledged'])
        self.assertEqual(self.allocation_slot('slice-one')['action']['action_id'], slot['action']['action_id'])

    def test_allocation_review_invalid_receipts_cannot_spend_original_reserve(self):
        for invalid in ('event', 'status', 'ref', 'source'):
            with self.subTest(invalid=invalid):
                self.checkpoint = self.checkpoint.with_name('reserve-' + invalid + '.json')
                self.settings['thread_id'] = 'task'
                self.request['host'].update(thread_id='task', role='controller', source_ref=None)
                self.request['host'].pop('actor_ref', None)
                self.prepare_execution()
                slot = self.allocation_slot('slice-one')
                self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
                    'receipt': self.receipt(slot['record'], 'unknown', 'create', ref=None)})
                slot = self.allocation_slot('slice-one')
                directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
                reserve = directory / 'reserves' / 'business' / hashlib.sha256(slot['action']['action_id'].encode()).hexdigest()
                available = reserve.stat().st_size
                (directory / 'usage.json').write_text(json.dumps({'allocated': 2 * 1024**3 - 128 * 1024**2}))
                bad = self.receipt(slot['record'], 'ready', 'lookup', ref='native:executor')
                operation = 'reconcile'
                if invalid == 'event':
                    bad['event'] = 'result'
                elif invalid == 'status':
                    bad['status'] = 'stopped'
                elif invalid == 'ref':
                    bad['status'] = 'unknown'
                else:
                    operation, bad['event'] = 'bind', 'create'
                try:
                    rejected = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': operation, 'receipt': bad})
                    self.assertEqual(rejected['status'], 'blocked')
                except transfer.entry.PreparationError:
                    pass
                self.assertEqual(reserve.stat().st_size, available, invalid)
                self.assertIsNone(self.allocation_slot('slice-one')['transaction'])
                ready = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'reconcile',
                    'receipt': self.receipt(slot['record'], 'ready', 'lookup', ref='native:executor')})
                self.assertEqual((ready.get('next_action') or {}).get('result', {}).get('status'), 'bound', ready)

    def test_allocation_review_unknown_lookup_binds_the_original_call(self):
        self.prepare_execution()
        original = self.allocation_slot('slice-one')
        unknown = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(original['record'], 'unknown', 'create', ref=None)})
        self.assertEqual((unknown.get('next_action') or {}).get('result', {}).get('status'), 'unknown', unknown)
        slot = self.allocation_slot('slice-one')
        ready = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'reconcile',
            'receipt': self.receipt(slot['record'], 'ready', 'lookup', ref='native:executor')})
        self.assertEqual((ready.get('next_action') or {}).get('result', {}).get('status'), 'bound', ready)
        recovered = self.allocation_slot('slice-one')
        self.assertEqual(recovered['record']['request'], original['record']['request'])
        self.assertEqual(recovered['action']['action_id'], original['action']['action_id'])
        self.assertEqual(len(recovered['record']['receipts']), 2)

    def test_allocation_contract_two_active_slices_keep_peer_in_stop_barrier(self):
        self.context = self.context_for(3)
        initial = self.input_for(3)
        scope = initial['scope']
        scope['implementation_paths'].append('peer.py')
        scope['owned_paths'].append('peer.py')
        scope['downstream']['manifest'].append({**copy.deepcopy(scope['downstream']['manifest'][-1]), 'path': 'peer.py'})
        initial['authorization']['scope_digest'] = transfer.entry.digest(scope)
        plan = initial['semantic']['inline_plan']
        plan['steps'].append({**copy.deepcopy(plan['steps'][0]), 'id': 'inline-peer',
                              'target': {'path': 'peer.py', 'symbol': 'run', 'operation': 'add'}})
        self.begin('continuous', initial)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        objects = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'objects'
        def object_bytes():
            return sum(path.stat().st_size for path in objects.rglob('*') if path.is_file())
        initial_bytes = object_bytes()
        first = self.execution_input()
        prepared = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'prepare', 'handoff': first})
        self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)
        one = self.allocation_slot('slice-one')
        first_bytes = object_bytes()
        bound = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(one['record'], ref='native:executor-one')})
        self.assertEqual(bound['next_action']['result']['status'], 'bound')
        one = self.allocation_slot('slice-one')
        peer = self.inherited_execution_input()
        peer['scope']['owned_paths'] = ['peer.py']
        peer['semantic']['plan_refs'] = [ref for ref in peer['semantic']['plan_refs'] if ref['step_id'] == 'inline-peer']
        peer['authorization']['scope_digest'] = transfer.entry.digest(peer['scope'])
        second = self.invoke('allocation', {'allocation_id': 'slice-two', 'operation': 'prepare', 'handoff': peer})
        self.assertEqual((second.get('next_action') or {}).get('operation'), 'invoke-host', {key: second.get(key) for key in ('status', 'error')})
        two = self.allocation_slot('slice-two')
        second_bytes = object_bytes()
        self.assertEqual(one['handoff']['refs'], two['handoff']['refs'])
        peer_receipt = self.receipt(two['record'], ref='native:executor-two')
        peer_receipt['raw']['output'] = 'peer native output ' * 3000
        peer_bound = self.invoke('allocation', {'allocation_id': 'slice-two', 'operation': 'bind',
            'receipt': peer_receipt})
        self.assertEqual(peer_bound['next_action']['result']['status'], 'bound')
        two = self.allocation_slot('slice-two')
        self.assertIn('raw_ref', two['record']['receipts'][0])
        peer_delta = two['record']['receipts'][0]['raw_ref']['sha256']
        opened = []
        original_open = Path.open
        def tracked_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            target = progress.read_projection(self.checkpoint, projection='transaction',
                subject_id='ticket:INLINE-TRANSFER:0', allocation_id='slice-one', intent=one['action']['action_id'])
        self.assertNotIn(peer_delta, {path.name for path in opened})
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        reads = [path for path in opened if path == self.checkpoint or path.is_relative_to(directory)]
        self.assertEqual(target.files_read, len(reads))
        self.assertEqual(target.bytes_read, sum(path.stat().st_size for path in reads))
        self.assertEqual(target.record[progress.KEY]['status'], 'bound')
        self.assertNotIn('slice-two', json.dumps(target.record))
        content = b"print('first finished')\n"
        (self.flow / 'impl.py').write_bytes(content)
        received = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'receive',
            'receipt': self.receipt(one['record'], 'stopped', 'result', ref='native:executor-one'),
            'result': {'delivery_id': 'first-finished', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                'tests': ['fixture boundary passed'], 'write_release': bound['next_action']['result']['write_release'],
                'plan_refs': first['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}})
        self.assertEqual(received['next_action']['result']['status'], 'received')
        stopped = self.invoke('pause')
        self.assertEqual(stopped['status'], 'pausing')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        self.assertEqual(self.invoke('inspect')['status'], 'pausing')
        self.assertFalse(self.state()['stopped'])
        self.assertEqual(self.allocation_slot('slice-two')['record']['request']['attempt'],
                         two['record']['request']['attempt'])
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            self.invoke('allocation', {'allocation_id': 'new-peer', 'operation': 'prepare', 'handoff': peer})
        self.assertEqual(rejected.exception.code, 'progression_suspended')
        if os.environ.get('T01_ALLOCATION_METRICS_PATH'):
            Path(os.environ['T01_ALLOCATION_METRICS_PATH']).write_text(json.dumps({
                'subjects': 1, 'allocations': 2, 'target_files_read': target.files_read,
                'target_bytes_read': target.bytes_read, 'peer_raw_bytes': len(peer_receipt['raw']['output']),
                'peer_raw_read': False, 'initial_object_bytes': initial_bytes,
                'first_prepare_added_bytes': first_bytes - initial_bytes,
                'first_bind_and_second_prepare_added_bytes': second_bytes - first_bytes,
                'final_object_bytes': object_bytes(), 'root_bytes': self.checkpoint.stat().st_size,
                'scope': 'two allocations in one Ticket; not a multi-Ticket or history pressure measurement'
            }, indent=2) + '\n')

    def test_allocation_contract_receives_accepts_and_replays_original_receipt(self):
        execution = self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        bind = {'allocation_id': 'slice-one', 'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref='native:executor')}
        bound = self.invoke('allocation', bind)
        self.assertEqual(bound['next_action']['result']['status'], 'bound')
        release = bound['next_action']['result']['write_release']
        data = b"print('shared allocation')\n"
        (self.flow / 'impl.py').write_bytes(data)
        slot = self.allocation_slot('slice-one')
        receive = {'allocation_id': 'slice-one', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
            'result': {'delivery_id': 'slice-result', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(data).hexdigest()},
                'tests': ['fixture boundary passed'], 'write_release': release,
                'plan_refs': execution['semantic']['plan_refs'],
                'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}}}
        received = self.invoke('allocation', receive)
        self.assertEqual(received['next_action']['result']['status'], 'received')
        accepted = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'accept',
            'decision': {'reference': 'dispatcher:shared-accept'}})
        self.assertEqual(accepted['next_action']['result']['status'], 'accepted')
        self.assertNotIn('handoff', self.allocation_slot('slice-one')['record']['acceptance'])
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            transfer.handoff.render(accepted['next_action']['result']['accepted'])
        self.assertEqual(rejected.exception.code, 'result_incomplete')
        self.assertEqual(self.invoke('inspect')['status'], 'active')
        before = self.checkpoint.read_bytes()
        replay = self.invoke('allocation', bind)
        self.assertTrue(replay['acknowledged'])
        self.assertNotIn('write_release', replay['next_action']['result'])
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_allocation_contract_transaction_rejects_wrong_target_without_writes(self):
        self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        args = {'subject_id': 'ticket:INLINE-TRANSFER:0', 'allocation_id': 'slice-one',
                'intent': slot['action']['action_id']}
        before = self.checkpoint.read_bytes()
        for replacement in ({'subject_id': 'all'}, {'subject_id': 'ticket:missing:0'},
                            {'allocation_id': 'other'}, {'intent': 'other'}, {'intent': None}):
            with self.subTest(replacement=replacement):
                with self.assertRaises(transfer.entry.PreparationError):
                    progress.read_projection(self.checkpoint, projection='transaction', **{**args, **replacement})
                self.assertEqual(self.checkpoint.read_bytes(), before)
        with self.assertRaises(transfer.entry.PreparationError):
            progress.read_projection(self.checkpoint, projection='transaction')

    def test_allocation_contract_dispatch_requires_original_controller_intent(self):
        self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            transfer.dispatch.handle({'protocol': transfer.handoff.PROTOCOL, 'operation': 'bind',
                'record': slot['record'], 'control': self.state()['control'],
                'receipt': self.receipt(slot['record'], ref='native:executor')})
        self.assertEqual(rejected.exception.code, 'authority_missing')
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_allocation_contract_recovers_the_persisted_transaction_intent(self):
        self.begin_dispatcher()
        execution = self.execution_input()
        original_replace = os.replace
        def crash_after_intent(source, target):
            if Path(target).parent.name == 'commits':
                store = progress.state_store.StateStore(self.checkpoint)
                current = store.open_current()
                subject_id = 'ticket:INLINE-TRANSFER:0'
                if subject_id in current.root['subjects']:
                    slot = store.read_subject(current, subject_id).value['allocations']['slice-one']
                    if slot.get('transaction') is not None:
                        raise KeyboardInterrupt('persisted C allocation intent before dispatch')
            return original_replace(source, target)
        with patch.object(os, 'replace', crash_after_intent):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'prepare', 'handoff': execution})
        slot = self.allocation_slot('slice-one')
        intent = copy.deepcopy(slot['transaction'])
        before = self.checkpoint.read_bytes()
        with self.assertRaises(transfer.entry.PreparationError) as rejected:
            transfer.dispatch.handle({'protocol': transfer.handoff.PROTOCOL, 'operation': 'prepare',
                'handoff': slot['handoff'], 'control': {**self.state()['control'], 'discussion': {'forged': True}},
                'allocation_intent': intent['intent_id']})
        self.assertEqual(rejected.exception.code, 'authority_changed')
        self.assertEqual(self.checkpoint.read_bytes(), before)
        recovery = self.invoke('resume')['next_action']
        self.assertEqual(recovery['operation'], 'allocation-recovery')
        saved_intents = []
        def observe_intents(source, target):
            result = original_replace(source, target)
            if Path(target) == self.checkpoint:
                transaction = self.allocation_slot('slice-one')['transaction']
                if transaction is not None:
                    saved_intents.append(transaction)
            return result
        with patch.object(os, 'replace', observe_intents):
            launched = self.invoke('allocation', recovery['data'])
        self.assertTrue(all(saved == intent for saved in saved_intents), saved_intents)
        self.assertEqual(launched['next_action']['operation'], 'invoke-host')

    def test_allocation_contract_missing_corrupt_and_wrong_parts_fail_closed(self):
        self.prepare_execution()
        slot = self.allocation_slot('slice-one')
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        reference = slot['handoff']['refs']['contract']
        path = directory / 'objects' / reference['sha256'][:2] / reference['sha256'][2:4] / reference['sha256']
        original = path.read_bytes()
        for corruption in ('missing', 'bytes'):
            with self.subTest(corruption=corruption):
                if corruption == 'missing':
                    path.unlink()
                else:
                    path.write_bytes(original + b' ')
                with self.assertRaises(transfer.entry.PreparationError):
                    progress.read_projection(self.checkpoint, projection='transaction',
                        subject_id='ticket:INLINE-TRANSFER:0', allocation_id='slice-one',
                        intent=slot['action']['action_id'])
                path.write_bytes(original)
        # Reference integrity is also enforced when the enclosing descriptor has
        # a valid seal: a digest alone cannot grant cross-run/type authority.
        for field, value in (('run_id', 'other-run'), ('kind', 'subject'), ('sha256', '0' * 64)):
            with self.subTest(field=field):
                altered = copy.deepcopy(transfer.handoff.unseal(slot['handoff']))
                altered['refs']['contract'][field] = value
                with self.assertRaises(transfer.entry.PreparationError):
                    transfer.handoff.resolve_allocation(transfer.handoff.seal(altered))


    def test_allocation_contract_is_shared_before_dispatch_and_inspect(self):
        execution = self.prepare_execution()
        self.assertEqual(self.invoke('inspect')['status'], 'active')
        store = progress.state_store.StateStore(self.checkpoint)
        current = store.open_current()
        ticket_id = execution['semantic']['plan_refs'][0]['ticket_id']
        subject_id = 'ticket:' + ticket_id + ':0'
        slot = store.read_subject(current, subject_id).value['allocations']['slice-one']
        self.assertNotIn('input', slot)
        self.assertEqual(slot['handoff']['kind'], 'allocation-input')
        self.assertEqual(set(slot['handoff']['refs']), {'contract', 'package', 'baseline'})
        self.assertNotIn('payload', slot['record']['request'])
        self.assertNotIn('payload', slot['action'])
        self.assertNotIn('handoff', slot['record'])
        self.assertEqual(set(slot['record']['allocation']), {'checkpoint', 'run_id', 'reference'})
        observed = progress.read_projection(self.checkpoint, projection='transaction',
            subject_id=subject_id, allocation_id='slice-one', intent=slot['action']['action_id'])
        self.assertEqual(observed.record[progress.KEY]['allocation_id'], 'slice-one')
        self.assertNotIn('write_release', json.dumps(observed.record))
        replay = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'prepare', 'handoff': execution})
        self.assertEqual(replay['next_action']['operation'], 'lookup-exact-allocation')

    def test_stage2_scope_review_acceptance_stays_ready_for_existing_readers(self):
        accepted = self.finish_design('stepwise')
        with self.subTest(reader='handle-inspect'):
            inspected = self.invoke('inspect')
            self.assertTrue(inspected['handoff_ready'], {key: inspected.get(key) for key in
                ('status', 'revision', 'route', 'handoff_ready', 'error')})
            self.assertEqual(inspected['route'], 'stage-3')
            self.assertEqual(inspected['readiness'], accepted['readiness'])
        with self.subTest(reader='stage2-delivery'):
            delivery = progress.read_projection(self.checkpoint, projection='delivery').record[progress.KEY]
            self.assertEqual(progress.scope_readiness(delivery, self.checkpoint), accepted['readiness'])
        with self.subTest(reader='stale-stage2-delivery'):
            stale = copy.deepcopy(delivery)
            stale['revision'] -= 1
            self.assertIsNone(progress.scope_readiness(stale, self.checkpoint))

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
        slot = self.allocation_slot('slice-one')
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

    def runner_transition(self, mode, *, closure_entry=False, closure_completion=False, outer_gate=None, recovery_entry=False):
        package_root, confirmed_path = self.runner_stage2_fixture()
        configuration = json.loads(confirmed_path.read_text())
        configuration['flow_mode'] = mode
        configuration['authority_scope']['allowed_paths'] = ['CHANGELOG.md', 'docs', 'impl.py']
        confirmed_path.write_text(json.dumps(configuration))
        fixture = self
        calls, queued, turns = [], [], []
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
                if stage in self.threads:
                    return self.threads[stage]
                calls.append(stage)
                self.threads[stage] = {'stage2': 'carrier', 'stage3': 'carrier-three', 'stage4': 'carrier-four'}[stage]
                return self.threads[stage]
            def poll(self, timeout):
                if recovery_entry and fixture.state()['stage'] == 4:
                    prior = transfer.handoff.predecessor_facts(fixture.state()['handoff']['predecessor'])
                    observed = fixture.observation('stopped', 'result', ref='native:closure')
                    observed['closure'] = {'ref': 'native:closure', 'candidate': prior['payload']['candidate_commit'],
                        'merge': None, 'ancestor_verified': False, 'changed_paths': [], 'binding': fixture.binding,
                        'checks': [], 'worktree_removed': False, 'branch_removed': False,
                        'disposition': 'deleted', 'disposition_verified': False,
                        'implementation_problem': 'resume original foreground implementation carrier'}
                    fixture.assertEqual(fixture.invoke('observe', observed)['next_action']['operation'], 'implementation-recovery')
                    recovered = fixture.invoke('implementation-recovery', {'reference': 'controller:runner-recovery',
                        'closure_digest': fixture.state()['handoff']['digest']})
                    fixture.assertEqual((recovered.get('next_action') or {}).get('operation'), 'inspect-host-state', recovered)
                    fixture.assertNotIn('native:closure', fixture.state()['recovered_native_refs'])
                    fixture.assertEqual(runner.resume(fixture.checkpoint), 0)
                    fixture.assertEqual(runner.resume(fixture.checkpoint), 0)
                    return
                outer = progress.read_projection(fixture.checkpoint, projection='identity').record
                pending = outer.get('pending_input')
                fixture.assertEqual(pending['kind'], 'stage-entry')
                fixture.assertFalse(queued)
                queued.append(pending['decision_id'])
                fixture.assertEqual(runner.resume(fixture.checkpoint, answer='confirm',
                                                  decision_id=pending['decision_id']), 0)
            def run_turn(self, session, prompt, settings, schema, on_progress):
                turns.append((session, progress.read_projection(fixture.checkpoint, projection='identity').record['launch']['turn']))
                if session == 'carrier':
                    fixture.accepted_stage2 = fixture.finish_design(mode)
                    return json.dumps(fixture.accepted_stage2['completion']['stage_result']), 'stage2-turn'
                if session == 'carrier-four':
                    fixture.settings['thread_id'] = session
                    fixture.request['host'].update(thread_id=session, role='scripted-carrier', source_ref='task')
                    value = fixture.input_for(4, fixture.state()['accepted'])
                    value['predecessor'] = json.loads(fixture.accepted_stage3['completion']['stage_result']['handoff_json'])['accepted_transfer']
                    fixture.context = fixture.context_for(4)
                    fixture.begin(mode, value)
                    fixture.invoke('observe', fixture.observation(ref='native:closure'))
                    if closure_completion:
                        result = fixture.complete_closure_publication()
                        return json.dumps(result['completion']['stage_result']), 'stage4-turn'
                    terminal = fixture.observation('stopped' if recovery_entry else 'idle', 'result', {
                        'delivery_id': 'closure-entry-pending', 'status': 'needs_input',
                        'payload': {'question': 'bounded transition reached Stage 4'}}, ref='native:closure')
                    self.snapshot = terminal.get('lifecycle')
                    pending = fixture.invoke('observe', terminal)['pending']
                    if not recovery_entry:
                        self.process = None
                    return json.dumps({'result': 'needs_input', 'needs_input_kind': 'user_decision',
                        'question': pending['question'], 'artifacts': [], 'evidence': [],
                        'handoff_json': '{}', 'message': ''}), 'stage4-turn'
                fixture.assertEqual(session, 'carrier-three')
                outer = progress.read_projection(fixture.checkpoint, projection='identity').record
                if outer.get('controller_decision'):
                    fixture.invoke('decide', outer['controller_decision'])
                fixture.settings['thread_id'] = session
                fixture.request['host'].update(thread_id=session, role='scripted-carrier', source_ref='task')
                if recovery_entry and fixture.state().get('final_integration_recovery'):
                    continued = fixture.invoke('observe', fixture.observation('idle', 'result', ref='native:dispatcher'))
                    fixture.assertEqual(continued['next_action']['operation'], 'continue-host', continued)
                    pending = fixture.invoke('observe', fixture.observation('idle', 'result', {
                        'delivery_id': 'recovered-original-dispatcher', 'status': 'needs_input',
                        'payload': {'question': 'original Dispatcher recovery resumed'}}, ref='native:dispatcher'))['pending']
                    self.process = None
                    return json.dumps({'result': 'needs_input', 'needs_input_kind': 'user_decision',
                        'question': pending['question'], 'artifacts': [], 'evidence': [],
                        'handoff_json': '{}', 'message': ''}), 'recovery-turn'
                if closure_entry:
                    fixture.accepted_stage3 = fixture.accepted_implementation_for_closure(fixture.accepted_stage2)
                    if outer_gate is not None:
                        current = progress.state_store.StateStore(fixture.checkpoint).open_current()
                        fixture.delivery_outer_gate(outer_gate, current.root['outer'])
                    return json.dumps(fixture.accepted_stage3['completion']['stage_result']), 'stage3-turn'
                successor = fixture.staged_successor(fixture.accepted_stage2)
                successor['predecessor'] = json.loads(fixture.accepted_stage2['completion']['stage_result']['handoff_json'])['accepted_transfer']
                fixture.context = fixture.context_for(3)
                fixture.begin(mode, successor)
                fixture.invoke('observe', fixture.observation(ref='native:dispatcher'))
                pending = fixture.invoke('observe', fixture.observation('idle', 'result', {
                    'delivery_id': 'stage3-pending', 'status': 'needs_input',
                    'payload': {'question': 'bounded transition reached Stage 3'}}, ref='native:dispatcher'))['pending']
                self.process = None
                return json.dumps({'result': 'needs_input', 'needs_input_kind': 'user_decision',
                    'question': pending['question'], 'artifacts': [], 'evidence': [], 'handoff_json': '{}',
                    'message': ''}), 'stage3-turn'
            def snapshot_lifecycle(self, stage):
                return copy.deepcopy(self.snapshot)
            def close(self):
                self.process = None
        with patch.object(runner, '__file__', str(package_root / 'guided-implementation/scripts/workflow.py')), \
             patch.object(runner.foreground_host, 'ForegroundHost', Host), redirect_stdout(io.StringIO()):
            if outer_gate is not None:
                with self.assertRaises((runner.WorkflowError, transfer.entry.PreparationError)):
                    runner.start(confirmed_path)
                self.assertEqual(calls, ['stage2', 'stage3'])
                return
            self.assertEqual(runner.start(confirmed_path), 0)
        outer = progress.read_projection(self.checkpoint, projection='identity').record
        if recovery_entry:
            self.assertEqual(calls, ['stage2', 'stage3', 'stage4'])
            self.assertEqual([turn for session, turn in turns if session == 'carrier-three'], [1, 2])
            self.assertEqual(outer['sessions']['stage3'], 'carrier-three')
            self.assertEqual(outer['current_stage'], 'stage3')
            self.assertEqual(outer['status'], 'needs_input')
            self.assertEqual(self.state()['control']['context']['carrier']['ref'], 'native:dispatcher')
            return
        if closure_entry:
            self.assertEqual(calls, ['stage2', 'stage3', 'stage4'])
            self.assertEqual(outer['current_stage'], 'stage4')
            self.assertEqual(outer[progress.KEY]['stage'], 4)
            self.assertEqual(outer['status'], 'completed' if closure_completion else 'needs_input')
            if closure_completion:
                self.assertEqual(set(outer['stage_results']), set(runner.STAGES))
                self.assertFalse(self.flow.exists())
                with patch.object(runner, '__file__', str(package_root / 'guided-implementation/scripts/workflow.py')), \
                     redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.resume(self.checkpoint), 0)
                self.assertEqual(calls, ['stage2', 'stage3', 'stage4'])
            footer = json.loads(outer['stage_results']['stage3']['handoff_json'])['accepted_transfer']
            self.assertEqual(footer['kind'], 'implementation-acceptance-ref')
            self.assertNotIn('handoff', footer)
            persisted = json.loads(outer['stage_results']['stage3']['handoff_json'])
            self.assertNotIn('verification', persisted)
            self.assertIn('verification_ref', persisted)
            self.assertNotIn('attempts', persisted)
            self.assertNotIn('raw', json.dumps(persisted))
            return
        self.assertEqual(calls, ['stage2', 'stage3'])
        self.assertEqual(len(queued), int(mode == 'stepwise'))
        self.assertEqual(outer['current_stage'], 'stage3')
        self.assertEqual(outer[progress.KEY]['stage'], 3)
        self.assertEqual(outer['status'], 'needs_input')
        previous = json.loads(outer['stage_results']['stage2']['handoff_json'])['accepted_transfer']
        self.assertNotIn('handoff', previous)
        self.assertEqual(previous['run_id'], outer[progress.KEY]['design_predecessor']['reference']['run_id'])
        self.assertEqual(previous['stage'], 2)
        self.assertEqual(transfer.handoff.predecessor_digest(previous), self.accepted_stage2['readiness']['accepted_digest'])

    def test_inline_recovery_public_runner_resumes_original_turn_and_session(self):
        self.runner_transition('continuous', closure_entry=True, recovery_entry=True)

    def test_completion_runner_public_continuous(self):
        self.runner_transition('continuous', closure_entry=True, closure_completion=True)

    def test_closure_entry_runner_public_continuous(self):
        self.runner_transition('continuous', closure_entry=True)

    def test_transition_runner_public_continuous_stops_in_stage3(self):
        self.runner_transition('continuous')

    def test_transition_runner_public_stepwise_resume_stops_in_stage3(self):
        self.runner_transition('stepwise')

    def test_runner_public_stage2_acceptance_stops_at_stepwise_boundary(self):
        package_root, confirmed_path = self.runner_stage2_fixture()
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
                calls.append(stage)
                fixture.assertEqual(stage, 'stage2')
                return 'carrier'
            def run_turn(self, session, prompt, settings, schema, on_progress):
                fixture.accepted_stage2 = fixture.finish_design('stepwise')
                self.process = None
                return json.dumps(fixture.accepted_stage2['completion']['stage_result']), 'stage2-turn'
            def close(self):
                self.process = None
        with patch.object(runner, '__file__', str(package_root / 'guided-implementation/scripts/workflow.py')), \
             patch.object(runner.foreground_host, 'ForegroundHost', Host), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.start(confirmed_path), 0)
        saved = progress.read_projection(self.checkpoint, projection='identity').record
        self.assertEqual(calls, ['stage2'])
        self.assertEqual(saved['status'], 'needs_input')
        self.assertEqual(saved['pending_input']['kind'], 'stage-entry')
        self.assertEqual(saved[progress.KEY]['stage'], 2)
        self.assertEqual(set(saved['stage_results']), {'stage2'})
        result = saved['stage_results']['stage2']
        payload = json.loads(result['handoff_json'])
        self.assertTrue(payload['handoff_ready'])
        self.assertEqual(payload['scope_revision'], self.accepted_stage2['readiness']['scope_revision'])
        self.assertEqual(payload['planning_commit'], self.accepted_stage2['readiness']['publication']['planning_commit'])
        self.assertEqual(result['handoff_json'], self.accepted_stage2['completion']['stage_result']['handoff_json'])

    def test_runner_public_start_pause_resume_preserves_original_carrier(self):
        package_root, confirmed_path = self.runner_stage2_fixture()
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
        slot = self.allocation_slot('slice-one')
        bound = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'bind',
            'receipt': self.receipt(slot['record'], ref='native:executor')})
        self.assertEqual(bound['next_action']['result']['status'], 'bound')
        content = b"print('receipt')\n"
        (self.flow / 'impl.py').write_bytes(content)
        slot = self.allocation_slot('slice-one')
        received = self.invoke('allocation', {'allocation_id': 'slice-one', 'operation': 'receive',
            'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:executor'),
            'result': {'delivery_id': 'receipt-slice', 'status': 'completed', 'payload': {
                'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                'tests': ['fixture boundary passed'], 'write_release': transfer.dispatch.execution_release_token(slot['record']),
                'plan_refs': slot['handoff']['semantic']['plan_refs'],
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
        slot = self.allocation_slot('slice-one')
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
        self.assertEqual(self.allocation_slot('slice-one')['record']['status'], 'bound')

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
        slot = self.allocation_slot('slice-one')
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
        self.assertEqual(self.allocation_slot('slice-one')['record']['status'], 'bound')

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
            self.assertEqual((prepared.get('next_action') or {}).get('operation'), 'invoke-host', prepared)
            reserve = self.checkpoint.with_name(self.checkpoint.name + '.state') / 'reserves' / 'business' / hashlib.sha256(
                prepared['next_action']['action_id'].encode()).hexdigest()
            self.assertTrue(reserve.exists(), 'host invocation requires a durable result-space reservation')
            self.assertEqual(reserve.stat().st_size, 16 * 1024 * 1024)
            replay = self.invoke('allocation', {'allocation_id': identity,
                'operation': 'prepare', 'handoff': execution})
            self.assertEqual(replay['next_action']['operation'], 'lookup-exact-allocation')
            slot = self.allocation_slot(identity)
            self.invoke('allocation', {'allocation_id': identity, 'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref=ref)})
            content = ('print(' + str(number) + ')\n').encode()
            (self.flow / 'impl.py').write_bytes(content)
            slot = self.allocation_slot(identity)
            received = self.invoke('allocation', {'allocation_id': identity, 'operation': 'receive',
                'receipt': self.receipt(slot['record'], 'stopped', 'result', ref=ref),
                'result': {'delivery_id': identity, 'status': 'completed', 'payload': {
                    'changed_paths': ['impl.py'], 'file_hashes': {'impl.py': hashlib.sha256(content).hexdigest()},
                    'tests': ['fixture boundary passed'],
                    'write_release': transfer.dispatch.execution_release_token(slot['record']),
                    'plan_refs': slot['handoff']['semantic']['plan_refs'],
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
