"""Real attached A/B/C/Phase/Git closure; only native host receipts are fixtures."""
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

import test_workflow_progress as fixtures
import test_entry_prepare
import discussion_core.workflow_control as ledger_control

entry = fixtures.transfer.entry
requirement = fixtures.transfer.requirement
protocol = fixtures.transfer.discussion_protocol
progress = fixtures.progress
supervision = fixtures.transfer.supervision


class AttachedFlowTests(fixtures.ProgressTests):
    def setUp(self):
        test_entry_prepare.EntrySupport.setUp(self)
        self.request['host']['supported_configurations'] = [
            {'model': 'fixture-model', 'reasoning_effort': 'high'}]
        catalog_patch = patch.object(fixtures.transfer.model_inventory, 'available_pairs',
                                     side_effect=AssertionError('normal progression must not query the account catalog'))
        catalog_patch.start()
        self.addCleanup(catalog_patch.stop)
        topic = protocol.handle({'protocol_version': 1, 'operation': 'bootstrap', 'project_path': str(self.root),
            'entry_mode': 'explicit-skill', 'conversation_ref': 'task', 'idempotency_key': str(uuid.uuid4()), 'root_slug': 'attached'})
        self.attachment = {'project_id': topic['project_id'], 'tree_id': topic['tree_id'],
            'actor_topic_id': topic['topic_id'], 'actor_conversation_ref': 'task'}
        self.base = {'protocol_version': 1, 'project_path': str(self.root), **self.attachment}
        self.ledger = Path(topic['ledger_path'])
        self.requirement_path = Path(topic['topic_document_path']).relative_to(self.root).as_posix()
        self.git('add', 'docs/discussions')
        self.git('commit', '-qm', 'confirmed discussion')
        self.request.update(stage=0, source={'kind': 'discussion', 'attachment': self.attachment})
        intent = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'prepare', 'entry': self.request,
            'purpose': 'freeze', 'authorization': 'source:freeze', 'base_ref': 'HEAD'})
        self.frozen = requirement.handle({'protocol': requirement.PROTOCOL, 'operation': 'freeze', 'entry': self.request, 'intent': intent})
        self.git('merge', '--ff-only', self.frozen['commit'])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.flow = Path(temporary.name).resolve() / 'flow'
        self.checkpoint = Path(temporary.name).resolve() / 'progress.json'
        self.binding = supervision.start_worktree({'repository': str(self.root), 'worktree': str(self.flow),
            'branch': 'codex/attached-flow', 'target_branch': 'main'})['binding']
        entry.os.getcwd.return_value = str(self.flow)
        self.request['host']['project_path'] = str(self.flow)
        self.request['target'] = {'kind': 'flow', 'binding': self.binding}
        self.host_response_sequence = 0
        self.phases = {}
        self.input = self.input_for(2)
        self.context = self.context_for(2)

    def write_planning_spec(self):
        from test_stage_scope_progress import ScopeProgressTests
        plan = ScopeProgressTests.inline_plan(self)
        content = ('approved design\nTicket ID: FEP-01\nPlan Version: 1\n'
                   '**What to build:** approved behavior\n**Blocked by:** None\n'
                   'AC-1: approved behavior\n## Implementation Decisions\n'
                   '### D1 — Plan\n```execution-plan-v2\n' +
                   json.dumps(plan, ensure_ascii=False) + '\n```\n')
        (self.flow / 'docs/spec.md').write_text(content)
        return content, plan

    def mutate(self, operation, **values):
        topic = entry.topic_read(str(self.root), self.attachment)
        return protocol.handle({**self.base, 'operation': operation,
            'expected_ledger_revision': topic['ledger_revision'], 'expected_topic_revision': topic['record_revision'],
            'idempotency_key': str(uuid.uuid4()), **values})

    def input_for(self, stage, predecessor=None):
        if stage == 2:
            phase = self.mutate('prepare-wrapper-phase-run', from_phase=0, to_phase=2, route='0->2',
                carrier_kind='solution-designer', source_checkpoint_id=self.frozen['checkpoint']['checkpoint_id'],
                flow_mode='stepwise', flow_mode_source='explicit-stage-confirmation', scope=['repository'])
        else:
            phase = self.mutate('prepare-phase-run', from_phase=stage-1, to_phase=stage,
                route=f'{stage-1}->{stage}', carrier_kind='current-topic')
            ref = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
            self.mutate('authorize-phase-carrier', **ref, carrier_ref='task')
            self.mutate('phase-ready', **ref, carrier_ref='task', evidence=phase['evidence'])
            self.mutate('phase-activate', **ref, evidence=phase['evidence'])
        self.phases[stage] = phase
        value = super().input_for(stage, predecessor)
        value['authorization']['phase'] = {'run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        return value

    def context_for(self, stage):
        value = super().context_for(stage)
        value['topic_ref'] = self.attachment['actor_topic_id']
        return value

    def activate_designer(self):
        phase = self.phases[2]
        ref = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        cp = self.frozen['checkpoint']
        self.mutate('claim-phase-carrier', **ref, actor_conversation_ref='native:designer', carrier_ref='native:designer',
            source_checkpoint_id=cp['checkpoint_id'], source_checkpoint_identity=cp['published_identity'])
        self.mutate('phase-ready', **ref, actor_conversation_ref='native:designer', carrier_ref='native:designer', evidence=phase['evidence'])
        action = self.invoke('advance')['next_action']
        self.assertEqual(action['operation'], 'source-lifecycle')
        self.invoke('lifecycle', {'request': action['payload']['request']})
        self.invoke('advance')

    def complete_phase(self):
        for _ in range(5):
            result = self.invoke('advance')
            if self.state()['phase_complete']:
                return result
            action = result['next_action']
            if action['operation'] == 'reconcile-source-lifecycle':
                action = action['action']
            self.assertIn(action['operation'], ('carrier-lifecycle', 'source-lifecycle'), result)
            request = copy.deepcopy(action['payload']['request'])
            self.assertEqual(request['project_path'], str(self.root))
            self.invoke('lifecycle', {'request': request})
            before = self.ledger.read_bytes()
            current = progress.state_store.StateStore(self.checkpoint).open_current()
            self.invoke('lifecycle', {'request': request})
            self.assertEqual(self.ledger.read_bytes(), before)
            self.assertEqual(progress.state_store.StateStore(self.checkpoint).open_current().reference, current.reference)
        self.fail('phase did not complete')

    def runner_input(self):
        package_root = Path(__file__).resolve().parents[2] / 'skills'
        roots = [self.binding[key] for key in ('repository','worktree','git_common_dir')]
        registry = {'source': 'host-current-skills', 'entries': [{'name': name,
            'entry': str(package_root / name / 'SKILL.md'), 'source': 'test-host', 'enabled': True}
            for name in ('solution-design', 'guided-implementation', 'change-closure')]}
        registry_context = {'project_path':str(self.root), 'project_id':self.request['host']['project_id'],
            'controller_ref':'task', 'receipt':'fixture:attached-host-registry', 'registry_digest':entry.digest(registry)}
        registry_input = self.checkpoint.parent / 'registry.json'
        registry_input.write_text(json.dumps({'registry':registry, 'registry_context':registry_context}))
        return {'controller_ref': 'task', 'requirement': copy.deepcopy(self.frozen),
            'host':{'transport':'app-server-stdio','cli_version':'codex-cli fixture',
                'source':{'kind':'controller-current-config','controller_ref':'task','receipt':'fixture:effective-config'},
                'thread':{'approvalPolicy':'never','sandbox':'read-only','config':{},'runtimeWorkspaceRoots':roots},
                'effective':{'approvalPolicy':'never','sandbox':{'type':'readOnly'},'runtimeWorkspaceRoots':roots}},
            'frozen_requirement': {'path': self.frozen['absolute_path'], 'commit': self.frozen['commit'],
                                   'sha256': self.frozen['requirement_identity']['sha256']},
            **{key: self.binding[key] for key in ('repository', 'worktree', 'git_common_dir', 'target_branch')},
            'run_record': str(self.checkpoint), 'registry_input': str(registry_input),
            'authority_scope': {'allowed_paths': ['impl.py', 'CHANGELOG.md']}, 'flow_mode': 'stepwise',
            'stages': {stage: {'model': 'fixture-model', 'reasoning_effort': 'high',
                'selection_input': fixtures.transfer.configuration('scripted-carrier')} for stage in fixtures.runner.STAGES},
            'registry': registry, 'registry_context': registry_context}

    def test_lifecycle_new_outer_activates_but_inline_controller_is_rejected(self):
        phase = self.phases[2]
        ref = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        self.mutate('authorize-phase-carrier', **ref, carrier_ref='native:designer')
        checkpoint = self.frozen['checkpoint']
        self.mutate('claim-phase-carrier', **ref, actor_conversation_ref='native:designer',
            carrier_ref='native:designer', source_checkpoint_id=checkpoint['checkpoint_id'],
            source_checkpoint_identity=checkpoint['published_identity'])
        self.mutate('phase-ready', **ref, actor_conversation_ref='native:designer',
            carrier_ref='native:designer', evidence=phase['evidence'])
        topic = entry.topic_read(str(self.root), self.attachment)
        request = {**self.base, 'operation': 'phase-activate', **ref, 'evidence': phase['evidence'],
            'expected_ledger_revision': topic['ledger_revision'], 'expected_topic_revision': topic['record_revision'],
            'idempotency_key': str(uuid.uuid4())}
        command = {'protocol': progress.PROTOCOL, 'operation': 'lifecycle', 'expected_revision': 0,
                   'data': {'request': request}}
        query = {'protocol': progress.PROTOCOL, 'operation': 'lifecycle', 'expected_revision': 0,
                 'data': {'request': {**self.base, 'operation': 'read-phase-run',
                                      'phase_run_id': phase['phase_run_id']}}}
        self.assertEqual(progress.handle(self.checkpoint, query)['result']['result']['phase_run']['state'], 'ready')
        result = progress.handle(self.checkpoint, command)
        self.assertEqual(result['result']['result']['state'], 'active')
        self.assertEqual(progress.handle(self.checkpoint, query)['result']['result']['phase_run']['state'], 'active')
        self.assertNotIn(progress.KEY, progress.read_record(self.checkpoint))
        invalid = self.checkpoint.with_name('inline-controller.json')
        invalid.write_text(json.dumps({progress.KEY: {'protocol': progress.PROTOCOL,
            'control': {'context': {'schema_version': 6}}}}))
        before, ledger = invalid.read_bytes(), self.ledger.read_bytes()
        with self.assertRaises(entry.PreparationError) as caught:
            progress.handle(invalid, command)
        self.assertEqual(caught.exception.code, 'legacy_run_requires_original_runtime')
        self.assertEqual(invalid.read_bytes(), before)
        self.assertEqual(self.ledger.read_bytes(), ledger)

    def lifecycle_activation_replace_failure(self, *, after_ledger):
        self.begin('stepwise')
        self.invoke('observe', self.observation())
        phase = self.phases[2]
        ref = {'phase_run_id': phase['phase_run_id'], 'attempt_id': phase['attempt_id']}
        checkpoint = self.frozen['checkpoint']
        self.mutate('claim-phase-carrier', **ref, actor_conversation_ref='native:designer',
            carrier_ref='native:designer', source_checkpoint_id=checkpoint['checkpoint_id'],
            source_checkpoint_identity=checkpoint['published_identity'])
        self.mutate('phase-ready', **ref, actor_conversation_ref='native:designer',
            carrier_ref='native:designer', evidence=phase['evidence'])
        request = self.invoke('advance')['next_action']['payload']['request']
        before = progress.state_store.StateStore(self.checkpoint).open_current()
        ledger_before = self.ledger.read_bytes()
        ledger_committed = []
        original_replace = os.replace
        def interrupt_replace(source, target):
            if Path(target) == self.checkpoint and (not after_ledger or ledger_committed):
                raise KeyboardInterrupt('checkpoint replace interrupted at lifecycle boundary')
            result = original_replace(source, target)
            if Path(target) == self.ledger:
                ledger_committed.append(True)
            return result
        with patch.object(os, 'replace', interrupt_replace):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('lifecycle', {'request': request})
        crashed = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertEqual(crashed.root['subjects'], before.root['subjects'])
        self.assertEqual(crashed.root['scope'], before.root['scope'])
        self.assertEqual(crashed.root['metadata']['revision'], before.root['metadata']['revision'])
        self.assertEqual(bool(ledger_committed), after_ledger)
        if after_ledger:
            self.assertGreater(crashed.revision, before.revision)
            journal = crashed.root['outer']['workflow_lifecycle'][entry.digest(request)]
            self.assertEqual(journal, {'request': request, 'result': None, 'finalize': None})
            self.assertNotEqual(self.ledger.read_bytes(), ledger_before)
        else:
            self.assertEqual(crashed.revision, before.revision)
            self.assertEqual(self.ledger.read_bytes(), ledger_before)
        committed_ledger = self.ledger.read_bytes()
        reads = []
        original_open = Path.open
        state_directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        def tracked_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get('mode', 'r')
            if 'r' in mode and (path in {self.checkpoint, self.ledger} or path.is_relative_to(state_directory)):
                reads.append((str(path), path.stat().st_size))
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', tracked_open):
            recovered = self.invoke('lifecycle', {'request': request})
        after = progress.state_store.StateStore(self.checkpoint).open_current()
        self.assertGreater(after.revision, crashed.revision)
        self.assertEqual(after.root['metadata']['revision'], before.root['metadata']['revision'] + 1)
        self.assertEqual(self.state()['step'], 'bound')
        self.assertEqual(self.state()['control']['context']['carrier']['ref'], 'native:designer')
        self.assertIsNone(self.state()['control']['discussion'])
        if after_ledger:
            self.assertEqual(self.ledger.read_bytes(), committed_ledger)
        self.assertEqual(recovered['result']['request'], request)
        self.assertEqual(recovered['result']['result']['state'], 'active')
        ledger_bytes = self.ledger.read_bytes()
        replay = self.invoke('lifecycle', {'request': request})
        self.assertEqual(replay, recovered)
        self.assertEqual(self.ledger.read_bytes(), ledger_bytes)
        self.assertEqual(progress.state_store.StateStore(self.checkpoint).open_current().reference, after.reference)
        self.assertLessEqual(self.checkpoint.stat().st_size, progress.state_store.ROOT_LIMIT)
        self.assertLessEqual(after.reference['size'], progress.state_store.ROOT_LIMIT)
        self.assertTrue(all(ref['size'] <= progress.state_store.SUBJECT_LIMIT for ref in after.root['subjects'].values()))
        self.assertLessEqual(sum(size for _, size in reads), progress.state_store.READ_LIMIT)
        metrics = {'after_ledger': after_ledger,
            'root_bytes': after.reference['size'], 'checkpoint_bytes': self.checkpoint.stat().st_size,
            'subject_bytes': max(ref['size'] for ref in after.root['subjects'].values()),
            'files_read': len(reads), 'bytes_read': sum(size for _, size in reads)}
        if os.environ.get('T01_LIFECYCLE_METRICS_PATH'):
            with Path(os.environ['T01_LIFECYCLE_METRICS_PATH']).open('a') as stream:
                stream.write(json.dumps(metrics) + '\n')

    def test_lifecycle_activation_recovers_before_checkpoint_replace(self):
        self.lifecycle_activation_replace_failure(after_ledger=False)

    def test_lifecycle_activation_recovers_after_ledger_commit(self):
        self.lifecycle_activation_replace_failure(after_ledger=True)

    def test_runner_accepts_real_a_receipt_on_start_and_restore_and_rejects_drift(self):
        runner = fixtures.runner
        packages = Path(__file__).resolve().parents[2] / 'skills'
        identity = runner.package_identity(str(packages / 'guided-implementation/SKILL.md'), 'guided-implementation')
        # Source-module runner stands in for its generated entry location only.
        # A, ledger, Git, strict receipt and restored-record validation are real.
        with patch.object(runner, 'package_identity', return_value=identity):
            confirmed = runner.validate_confirmed(self.runner_input())
            state = runner._new_state(confirmed)
            self.assertEqual(runner._validate_record(copy.deepcopy(state))['confirmed']['requirement'], self.frozen)
            # Normalize once before checking ownership and persist those same paths.
            variant = self.runner_input()
            for key in ('repository', 'worktree', 'git_common_dir'):
                path = Path(variant[key])
                variant[key] = str(path / '..' / path.name)
            normalized = runner.validate_confirmed(variant)
            for key in ('repository', 'worktree', 'git_common_dir'):
                self.assertEqual(normalized[key], confirmed[key])
            self.assertEqual(runner._validate_record(runner._new_state(normalized))['confirmed'], confirmed)
            for forbidden in (str(self.root), str(self.root / '..' / self.root.name), str(self.root / 'docs' / '..')):
                with self.subTest(worktree=forbidden):
                    raw = self.runner_input()
                    raw['worktree'] = forbidden
                    with self.assertRaises(runner.WorkflowError):
                        runner.validate_confirmed(raw)
                    changed = copy.deepcopy(state)
                    changed['confirmed']['worktree'] = forbidden
                    with self.assertRaises(runner.WorkflowError):
                        runner._validate_record(changed)
            self.git('worktree', 'remove', str(self.flow))
            self.assertEqual(runner._validate_record(copy.deepcopy(state))['confirmed']['requirement'], self.frozen)
            self.assertFalse(self.flow.exists())
            variants = []
            missing = copy.deepcopy(self.frozen)
            missing.pop('discussion_project')
            variants.append(missing)
            old = copy.deepcopy(self.frozen)
            old['protocol'] = 'requirement-freeze-v1'
            variants.append(old)
            for field, value in (('root', str(self.flow)), ('actor_topic_id', 'topic-' + 'a' * 32),
                                 ('git_common_dir', str(self.root / 'other-store'))):
                changed = copy.deepcopy(self.frozen)
                changed['discussion_project'][field] = value
                variants.append(changed)
            for source in variants:
                with self.subTest(source=source['protocol'], identity=source.get('discussion_project')):
                    raw = self.runner_input()
                    raw['requirement'] = source
                    with self.assertRaises(runner.WorkflowError):
                        runner.validate_confirmed(raw)
                    changed = copy.deepcopy(state)
                    changed['confirmed']['requirement'] = source
                    with self.assertRaises(runner.WorkflowError):
                        runner._validate_record(changed)

    def test_lifecycle_delayed_readiness_issues_one_boundary_until_confirmed(self):
        self.begin('stepwise')
        self.invoke('observe', self.observation())
        self.activate_designer()
        self.write_planning_spec()
        self.flow_git('add', 'docs/spec.md')
        self.flow_git('commit', '-qm', 'planning for delayed boundary')
        candidate = self.flow_git('rev-parse', 'HEAD')
        proposal_digest = self.propose_design_scope(candidate)
        self.ready_publication(candidate, 'controller:planning')
        self.seal_design_scope(proposal_digest)
        self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:planning'})
        pending = self.invoke('receive-publication')['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:accept-plan'})
        for _ in range(3):
            action = self.invoke('advance')['next_action']
            if action['operation'] == 'reconcile-source-lifecycle':
                action = action['action']
            request = action['payload']['request']
            if request['operation'] == 'complete-phase-run':
                break
            self.invoke('lifecycle', {'request': request})
        else:
            self.fail('the original accepted phase did not reach completion')

        def set_runner_gate(blocked):
            with progress.record_lock(self.checkpoint):
                outer = progress.read_projection(self.checkpoint, projection='identity').record
                authority_revision = outer.authority_revision
                values = {key: value for key, value in outer.items() if key != progress.KEY}
                if blocked:
                    values['runner_request'] = {'operation': 'pause', 'request_id': 'temporary-source-pause'}
                else:
                    values.pop('runner_request')
                progress.save_outer_record(self.checkpoint, values, expected_revision=authority_revision)

        set_runner_gate(True)
        original = self.invoke('lifecycle', {'request': request})
        self.assertTrue(self.state()['phase_complete'])
        self.assertIsNone(self.state()['pending'])
        self.assertIsNone(progress.scope_readiness(self.state(), self.checkpoint))
        gated = self.checkpoint.read_bytes()
        self.assertEqual(self.invoke('lifecycle', {'request': request}), original)
        self.assertEqual(self.checkpoint.read_bytes(), gated)
        ledger = self.ledger.read_bytes()
        set_runner_gate(False)
        self.assertIsNotNone(progress.scope_readiness(self.state(), self.checkpoint))
        self.assertEqual(self.invoke('lifecycle', {'request': request}), original)
        pending = self.state()['pending']
        self.assertIsNotNone(pending, 'released readiness must still ask for the first Stage-3 confirmation')
        self.assertEqual(pending['kind'], 'stage-entry')
        self.assertEqual(pending['subject'], {'stage': 3, 'predecessor': self.state()['accepted']['digest']})
        pending_root = self.checkpoint.read_bytes()
        self.assertEqual(self.invoke('lifecycle', {'request': request}), original)
        self.assertEqual(self.state()['pending'], pending)
        self.assertEqual(self.checkpoint.read_bytes(), pending_root)
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:delayed-confirmation'})
        confirmed_root = self.checkpoint.read_bytes()
        self.assertEqual(self.invoke('lifecycle', {'request': request}), original)
        self.assertIsNone(self.state()['pending'])
        self.assertEqual(self.checkpoint.read_bytes(), confirmed_root)
        self.assertEqual(self.ledger.read_bytes(), ledger)

    def test_attached_stage3_rejects_direct_before_native_dispatch(self):
        self.begin('stepwise')
        self.invoke('observe', self.observation())
        self.activate_designer()
        self.write_planning_spec()
        self.flow_git('add', 'docs/spec.md'); self.flow_git('commit', '-qm', 'planning')
        candidate = self.flow_git('rev-parse', 'HEAD')
        proposal_digest = self.propose_design_scope(candidate)
        self.ready_publication(candidate, 'controller:planning')
        self.seal_design_scope(proposal_digest)
        self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:planning'})
        pending = self.invoke('receive-publication')['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:accept-plan'})
        self.complete_phase()
        pending = self.state()['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:enter-stage3'})
        value = self.input_for(3, self.state()['accepted'])
        self.plan = lambda: value['semantic']['validation_plan']
        value['semantic']['implementation_policy'] = {'mode': 'direct'}
        self.context = self.context_for(3)
        before = self.checkpoint.read_bytes()
        with self.assertRaises(entry.PreparationError) as error:
            self.invoke('start', {'handoff': value,
                'control': {'context': self.context, 'discussion': None}})
        self.assertEqual(error.exception.code, 'invalid_policy')
        self.assertEqual(self.checkpoint.read_bytes(), before)
        self.assertFalse((self.flow / 'impl.py').exists())
        self.assertNotEqual(self.state()['dispatch']['request']['role'], 'implementation-dispatcher')

    def test_attached_plan_revision_uses_original_ledger_record(self):
        self.begin('stepwise')
        self.invoke('observe', self.observation())
        self.activate_designer()
        content, plan = self.write_planning_spec()
        self.flow_git('add', 'docs/spec.md')
        self.flow_git('commit', '-qm', 'attached plan')
        candidate = self.flow_git('rev-parse', 'HEAD')
        basis = {'commit': candidate, 'path': 'docs/spec.md', 'section': 'approved design'}
        manifest = [{'path': path, 'owner': owner, 'kind': kind, 'operation': 'add', 'basis': basis}
                    for path, owner, kind in [('CHANGELOG.md', 'closure', 'document'),
                                              ('docs/spec.md', 'closure', 'document'),
                                              ('impl.py', 'implementation', 'source')]]
        state = self.state()
        proposal = {'proposal_id': 'proposal-1', 'expected_revision': 0,
            'candidate_commit': candidate, 'manifest': manifest, 'implementation_required': True,
            'requirement_identity': copy.deepcopy(state['handoff']['requirement_identity']),
            'binding': copy.deepcopy(state['handoff']['binding']), 'previous_scope_revision': 0}
        proposed = self.invoke('scope-propose', proposal)
        self.ready_publication(candidate, 'controller:planning')
        self.seal_design_scope(proposed['next_action']['proposal']['digest'])
        self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:planning'})
        pending = self.invoke('receive-publication')['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:accept-plan'})
        self.complete_phase()
        pending = self.state()['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:enter-stage3'})
        value = self.input_for(3, self.state()['accepted'])
        self.context = self.context_for(3)
        self.begin('stepwise', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        outer = progress.read_active_owner(self.checkpoint)
        current_context = copy.deepcopy(outer[progress.KEY]['control']['context'])
        # Simulated existing caller-owned Stage-3 ledger authority. This fixture
        # commits a valid pointer under the original ledger lock/CAS; it does
        # not exercise or enable deferred ledger Stage-2 initialization.
        seed_request = {**self.base, 'operation': 'workflow-control',
            'action': 'fixture-original-control', 'evidence': {},
            'expected_ledger_revision': entry.topic_read(str(self.root), self.attachment)['ledger_revision'],
            'expected_topic_revision': entry.topic_read(str(self.root), self.attachment)['record_revision'],
            'idempotency_key': str(uuid.uuid4())}
        ledger_path, lock_path, _, owner_ref = ledger_control._handoff_mutation_context(
            seed_request, {'action', 'evidence'})
        with lock_path.open('a+b') as stream:
            ledger_control._flock_with_timeout(stream)
            frontmatter, records = protocol._load_records(ledger_path)
            topic_record = ledger_control._record_by_id(records['Current Topics'], 'topic_id',
                self.attachment['actor_topic_id'], 'topic_id')
            revision, _ = ledger_control._validate_revisions(seed_request, frontmatter, topic_record)
            self.assertEqual(current_context['controller_ref'], owner_ref)
            self.assertFalse(any(record.get('result_kind') == 'workflow-control'
                and record.get('topic_id') == self.attachment['actor_topic_id']
                for record in records['Phase Results']))
            pointer = progress.state_store.StateStore(ledger_path).prepare_ledger_control(
                current_context, topic_id=self.attachment['actor_topic_id'],
                ledger_revision=revision + 1, control_revision=1, previous=None, receipt=None)
            records['Phase Results'].append({'result_id': 'WC-' + self.attachment['actor_topic_id'],
                'result_kind': 'workflow-control', 'topic_id': self.attachment['actor_topic_id'],
                'record_revision': 1, 'state': 'active', 'data_json': protocol._canonical_json(pointer)})
            ledger_control._write_ledger_transaction(ledger_path, frontmatter, records, seed_request,
                ledger_revision=revision + 1, event_type='fixture-original-control', result={'store_ref': pointer})
        topic = entry.topic_read(str(self.root), self.attachment)
        stored_context, _ = progress.state_store.StateStore(ledger_path).read_ledger_control(pointer)
        self.assertFalse(current_context.get('retired_handoffs'))
        self.assertEqual(stored_context, {key: value for key, value in current_context.items()
                                          if key != 'retired_handoffs'})
        outer[progress.KEY]['control'].update(context=stored_context, store_ref=pointer, discussion={**self.base,
            'expected_ledger_revision': topic['ledger_revision'],
            'expected_topic_revision': topic['record_revision'],
            'idempotency_key': str(uuid.uuid4())})
        progress.Progress(self.checkpoint, outer).save()
        authority = self.state()['scope_authority']
        old_ledger = self.ledger
        report = {'revision_id': 'attached-private-1',
            'expected_authority_revision': authority['revision'],
            'expected_authority_digest': entry.digest(authority), 'controller_ref': 'task',
            'classification': {'kind': 'internal', 'reference': 'controller:attached-private'},
            'report': {'ticket_ids': ['FEP-01'], 'step_ids': ['FP1-01'],
                'reason': 'Move a private function', 'production_facts': 'Same output and caller',
                'contract_before': authority['execution_plan']['contract_digest'],
                'contract_after': authority['execution_plan']['contract_digest'],
                'dependency_impact': ['FP1-01', 'FP1-02'], 'verification_impact': ['V1'],
                'carry_forward': []}, 'planning_paths': ['docs/spec.md']}
        self.invoke('prepare-plan-revision', report)
        stopped = self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        spec = self.flow / 'docs/spec.md'
        match = re.search(r'```execution-plan-v2\n(.*?)\n```', content, re.DOTALL)
        updated = copy.deepcopy(plan)
        updated['plan_version'] = 2
        updated['steps'][0]['approach'].append('move private helper')
        spec.write_text((content[:match.start(1)] + json.dumps(updated) + content[match.end(1):])
                        .replace('Plan Version: 1', 'Plan Version: 2'))
        self.flow_git('add', 'docs/spec.md')
        self.flow_git('commit', '--only', '-qm', 'attached private revision', '--', 'docs/spec.md')
        with patch.object(ledger_control._git, 'verified_transition',
                          wraps=ledger_control._git.verified_transition) as verified, \
                patch.object(protocol, 'handle', wraps=protocol.handle) as ledger_calls:
            result = self.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
                'controller_ref': 'task', 'write_release': stopped['next_action']['write_release'],
                'planning_commit': self.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(verified.call_count, 1)
        self.assertEqual(verified.call_args.args[0]['repository'], str(self.flow))
        self.assertEqual(result['next_action']['operation'], 'plan-revision-activated')
        self.assertEqual(self.ledger, old_ledger)
        current = protocol.handle({**self.base, 'operation': 'read-topic'})['workflow_control']
        self.assertEqual(current['controller_ref'], 'task')
        self.assertEqual(current['topic_ref'], self.attachment['actor_topic_id'])
        self.assertEqual(len(current['handoff_progress']['plan_revisions']), 1)
        # Completed control intents leave the active roster. Replay the exact
        # original public RPC, including its pointer, CAS and idempotency key.
        original_calls = [call.args[0] for call in ledger_calls.call_args_list
            if call.args[0].get('operation') == 'workflow-control'
            and call.args[0].get('action') == 'plan-revision-activate']
        self.assertEqual(len(original_calls), 1)
        original_ledger_request = copy.deepcopy(original_calls[0])
        self.assertFalse(any(value['request']['action'] == 'plan-revision-activate'
            for value in self.state()['control_transactions'].values()))
        before_replay = self.ledger.read_bytes()
        repeated = protocol.handle(original_ledger_request)
        self.assertEqual(repeated['control']['context'], current)
        self.assertEqual(self.ledger.read_bytes(), before_replay)
        from test_plan_driven_dispatch import PlanDrivenDispatchTests
        controller_request, controller_settings = copy.deepcopy(self.request), copy.deepcopy(self.settings)
        self.request['source'] = {'kind': 'frozen', 'path': self.requirement_path}
        with patch.object(self, 'input_for', side_effect=lambda stage, predecessor=None:
                          super(AttachedFlowTests, self).input_for(stage, predecessor)):
            allocation_input = PlanDrivenDispatchTests.execution_request(self, self)
        bad_checkpoint = copy.deepcopy(allocation_input)
        bad_checkpoint['requirement']['checkpoint']['checkpoint_id'] = 'other-checkpoint'
        with self.assertRaises(entry.PreparationError):
            fixtures.transfer.handoff.handle(bad_checkpoint)
        bad_child_path = copy.deepcopy(allocation_input)
        bad_child_path['entry']['source']['path'] = 'docs/other-requirement.md'
        with self.assertRaises(entry.PreparationError):
            fixtures.transfer.handoff.handle(bad_child_path)
        bad_child_hash = copy.deepcopy(allocation_input)
        bad_child_hash['expected_entry']['requirement']['sha256'] = '0' * 64
        with self.assertRaises(entry.PreparationError):
            fixtures.transfer.handoff.handle(bad_child_hash)
        fixtures.transfer.handoff.handle(allocation_input)
        with patch.object(ledger_control._git, 'verified_transition',
                          wraps=ledger_control._git.verified_transition) as verified:
            allocation = self.invoke('allocation', {'allocation_id': 'attached-current-group',
                'operation': 'prepare', 'handoff': allocation_input})
        self.assertIsNotNone(allocation['next_action'], allocation)
        self.assertEqual(allocation['next_action']['operation'], 'invoke-host', allocation)
        self.assertEqual(verified.call_count, 1)
        self.assertEqual(verified.call_args.args[0]['request']['action'], 'plan-execution')
        slot = self.state()['allocations']['attached-current-group']
        with patch.object(ledger_control._git, 'verified_transition',
                          wraps=ledger_control._git.verified_transition) as verified:
            bound = self.invoke('allocation', {'allocation_id': 'attached-current-group',
                'operation': 'bind',
                'receipt': self.receipt(slot['record'], ref='native:attached-agent')})
        self.assertEqual(verified.call_args.args[0]['request']['action'], 'assign')
        release = bound['next_action']['result']['write_release']
        implementation = b"print('attached revised behavior')\n"
        (self.flow / 'impl.py').write_bytes(implementation)
        slot = self.state()['allocations']['attached-current-group']
        result_payload = {'changed_paths': ['impl.py'],
            'file_hashes': {'impl.py': hashlib.sha256(implementation).hexdigest()},
            'tests': ['attached behavior passed'], 'write_release': release,
            'plan_refs': allocation_input['semantic']['plan_refs'],
            'plan_authority': self.state()['control']['context']['handoff_progress']['plan_authority']}
        with patch.object(ledger_control._git, 'verified_transition',
                          wraps=ledger_control._git.verified_transition) as verified:
            received = self.invoke('allocation', {'allocation_id': 'attached-current-group',
                'operation': 'receive',
                'receipt': self.receipt(slot['record'], 'stopped', 'result', ref='native:attached-agent'),
                'result': {'delivery_id': 'attached-current-result', 'status': 'completed',
                           'payload': result_payload}})
        self.assertEqual(received['next_action']['result']['status'], 'received', received)
        self.assertEqual(verified.call_args.args[0]['request']['action'], 'execution-result')
        with patch.object(ledger_control._git, 'verified_transition',
                          wraps=ledger_control._git.verified_transition) as verified:
            accepted_group = self.invoke('allocation', {'allocation_id': 'attached-current-group',
                'operation': 'accept', 'decision': {'reference': 'dispatcher:attached-current'}})
        self.assertEqual(accepted_group['next_action']['result']['status'], 'accepted', accepted_group)
        self.assertEqual(verified.call_args.args[0]['request']['action'], 'accept-execution')
        self.request, self.settings = controller_request, controller_settings
        self.flow_git('add', 'impl.py')
        self.flow_git('commit', '-qm', 'attached revised implementation')
        candidate = self.flow_git('rev-parse', 'HEAD')
        self.invoke('observe', self.observation('idle', 'result', ref='native:dispatcher'))
        checkpoint = self.state()['control']['context']['handoff_progress']
        checks = fixtures.transfer.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        with patch.object(ledger_control._git, 'verified_transition',
                          wraps=ledger_control._git.verified_transition) as verified:
            ready = self.invoke('control', {'action': 'candidate-ready', 'evidence': {
                'dispatcher_ref': 'native:dispatcher',
                'attempt': self.state()['control']['context']['carrier']['attempt'],
                'commit': candidate, 'expected_target_head': self.git('rev-parse', 'main'),
                'binding': self.binding, 'plan_digest': checkpoint['plan_digest'],
                'plan_authority': checkpoint['plan_authority'], 'checks': checks},
                'receipt': {'adapter': 'fixture', 'call_ref': 'attached-focused',
                    'response_ref': 'attached-focused-result', 'raw': {'checks': checks,
                        'source_unchanged': True, 'stopped': True,
                        'dispatcher_ref': 'native:dispatcher'}}})
        self.assertEqual(self.state()['control']['context']['handoff_progress']['state'], 'reviewable', ready)
        self.assertEqual(verified.call_args.args[0]['request']['action'], 'candidate-ready')
        ledger_before = self.ledger.read_bytes()
        (self.flow / 'impl.py').write_text("print('unaccepted review drift')\n")
        review_envelope = self.state()['control']['discussion']
        with self.assertRaisesRegex(Exception, 'validation candidate changed or dirty'):
            protocol.handle({**review_envelope, 'operation': 'workflow-control',
                'action': 'review-start', 'evidence': {'candidate': candidate}})
        self.assertEqual(self.ledger.read_bytes(), ledger_before)
        (self.flow / 'impl.py').write_bytes(implementation)
        self.mark_reviewable = lambda _candidate, fixture_execution=False, evidence_suffix='': {
            'candidate': candidate, 'plan_digest': checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'], 'checks': checks}
        payload = {'artifacts': ['impl.py'], 'checks': ['attached behavior passed'],
            'candidate_commit': candidate,
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis,
                              'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': checks}}
        with patch.object(ledger_control._git, 'verified_transition',
                          wraps=ledger_control._git.verified_transition) as verified:
            accepted = self.accept_current(payload, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(accepted['status'], 'accepted', accepted)
        actions = [call.args[0]['request']['action'] for call in verified.call_args_list]
        for action in ('review-start', 'review-converged', 'validation-start',
                       'validation-result', 'delivery-ready', 'accept-delivery'):
            self.assertIn(action, actions)

    def original_ledger_start_fixture(self):
        self.begin('stepwise')
        self.invoke('observe', self.observation())
        self.activate_designer()
        self.write_planning_spec()
        self.flow_git('add', 'docs/spec.md')
        self.flow_git('commit', '-qm', 'planning')
        candidate = self.flow_git('rev-parse', 'HEAD')
        proposal_digest = self.propose_design_scope(candidate)
        self.ready_publication(candidate, 'controller:planning')
        self.seal_design_scope(proposal_digest)
        self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:planning'})
        pending = self.invoke('receive-publication')['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'accept', 'reference': 'controller:accept-plan'})
        self.complete_phase()
        pending = self.state()['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:enter-stage3'})
        self.context = self.context_for(3)
        current_context = copy.deepcopy(self.context)
        # Simulated existing caller-owned Stage-3 ledger authority. This fixture
        # commits a valid pointer under the original ledger lock/CAS; it does
        # not exercise or enable deferred ledger Stage-2 initialization.
        seed_request = {**self.base, 'operation': 'workflow-control',
            'action': 'fixture-original-control', 'evidence': {},
            'expected_ledger_revision': entry.topic_read(str(self.root), self.attachment)['ledger_revision'],
            'expected_topic_revision': entry.topic_read(str(self.root), self.attachment)['record_revision'],
            'idempotency_key': str(uuid.uuid4())}
        ledger_path, lock_path, _, owner_ref = ledger_control._handoff_mutation_context(
            seed_request, {'action', 'evidence'})
        with lock_path.open('a+b') as stream:
            ledger_control._flock_with_timeout(stream)
            frontmatter, records = protocol._load_records(ledger_path)
            topic_record = ledger_control._record_by_id(records['Current Topics'], 'topic_id',
                self.attachment['actor_topic_id'], 'topic_id')
            revision, _ = ledger_control._validate_revisions(seed_request, frontmatter, topic_record)
            self.assertEqual(current_context['controller_ref'], owner_ref)
            self.assertFalse(any(record.get('result_kind') == 'workflow-control'
                and record.get('topic_id') == self.attachment['actor_topic_id']
                for record in records['Phase Results']))
            pointer = progress.state_store.StateStore(ledger_path).prepare_ledger_control(
                current_context, topic_id=self.attachment['actor_topic_id'],
                ledger_revision=revision + 1, control_revision=1, previous=None, receipt=None)
            records['Phase Results'].append({'result_id': 'WC-' + self.attachment['actor_topic_id'],
                'result_kind': 'workflow-control', 'topic_id': self.attachment['actor_topic_id'],
                'record_revision': 1, 'state': 'active', 'data_json': protocol._canonical_json(pointer)})
            ledger_control._write_ledger_transaction(ledger_path, frontmatter, records, seed_request,
                ledger_revision=revision + 1, event_type='fixture-original-control', result={'store_ref': pointer})
        stored_context, _ = progress.state_store.StateStore(ledger_path).read_ledger_control(pointer)
        self.assertFalse(current_context.get('retired_handoffs'))
        self.assertEqual(stored_context, {key: value for key, value in current_context.items()
                                          if key != 'retired_handoffs'})
        self.context = stored_context
        value = self.input_for(3, self.state()['accepted'])
        topic = entry.topic_read(str(self.root), self.attachment)
        envelope = {**self.base, 'expected_ledger_revision': topic['ledger_revision'],
            'expected_topic_revision': topic['record_revision'], 'idempotency_key': str(uuid.uuid4())}
        return value, envelope, pointer

    def test_attached_start_rejects_head_ahead_of_frozen_b_baseline(self):
        value, envelope, pointer = self.original_ledger_start_fixture()
        original = protocol.handle
        raced = []
        def after_b_preparation(request):
            if request.get('operation') == 'workflow-control' and request.get('action') == 'start-dispatch':
                raced.append(True)
                (self.flow / 'impl.py').write_text("print('unaccepted preexisting bytes')\n")
                self.flow_git('add', 'impl.py')
                self.flow_git('commit', '-qm', 'head advanced after B preparation')
            return original(request)
        ledger_before_race = self.ledger.read_bytes()
        with patch.object(protocol, 'handle', side_effect=after_b_preparation), \
                patch.object(ledger_control, '_start_git_authority',
                             wraps=ledger_control._start_git_authority) as checked:
            with self.assertRaises(Exception) as raised:
                self.invoke('start', {'handoff': value,
                    'control': {'context': self.context, 'discussion': envelope, 'store_ref': pointer}})
        self.assertEqual(getattr(raised.exception, 'code', None), 'source_changed')
        self.assertEqual(self.ledger.read_bytes(), ledger_before_race)
        self.assertEqual(raced, [True], str(raised.exception))
        self.assertEqual(checked.call_count, 1)
        latest = protocol.handle({**self.base, 'operation': 'read-topic'})['workflow_control']
        self.assertIsNone(latest['handoff_progress'])

    def test_attached_start_prepares_original_ledger_b_and_preserves_launch_boundary(self):
        value, envelope, pointer = self.original_ledger_start_fixture()
        ledger_before = self.ledger.read_bytes()
        original = ledger_control._start_git_authority
        checked = []
        def verify_original(request, context, evidence, action):
            before = self.ledger.read_bytes()
            reads = []
            original_read = progress.state_store.StateStore._read
            def tracked_read(store, path, limit):
                result = original_read(store, path, limit)
                reads.append((str(path), len(result)))
                return result
            with patch.object(progress.state_store.StateStore, '_read', tracked_read):
                identity = progress.read_projection(self.checkpoint, projection='identity')
                stage_id = identity.record[progress.KEY]['stage_subject']
                target = progress.read_projection(self.checkpoint, projection='transaction',
                                                 subject_id=stage_id, intent='prepare')
            current = target.record[progress.KEY]
            self.assertIsInstance(target, progress.state_store.TransactionProjection)
            self.assertEqual(current['handoff'], current['transaction']['handoff'])
            self.assertEqual(current['control_ref'], pointer)
            self.assertEqual(current['control_digest'], entry.digest(self.context))
            self.assertEqual(current['transaction']['operation'], 'prepare')
            self.assertEqual(current['transaction']['scope_checkpoint_path'], str(self.checkpoint))
            self.assertEqual(current['transaction']['base_control_digest'], current['control_port_digest'])
            self.assertNotIn('control', current)
            self.assertNotIn('allocations', current)
            self.assertNotIn('history', current)
            self.assertLessEqual(target.bytes_read + identity.bytes_read, progress.state_store.READ_LIMIT)
            self.assertLessEqual(sum(size for _, size in reads), progress.state_store.READ_LIMIT)
            for changed_context, changed_evidence, changed_action in (
                    ({**context, 'controller_ref': 'another-controller'}, evidence, action),
                    (context, {**evidence, 'binding': {**evidence['binding'], 'branch': 'other'}}, action),
                    (context, evidence, 'start-design')):
                with self.assertRaises(ledger_control.ProtocolError) as rejected:
                    original(request, changed_context, changed_evidence, changed_action)
                self.assertEqual(rejected.exception.code, 'invalid_request')
                self.assertEqual(self.ledger.read_bytes(), before)
            with self.assertRaises(ledger_control.ProtocolError) as rejected:
                original({**request, 'expected_control_ref': {**pointer, 'control_revision': 0}},
                         context, evidence, action)
            self.assertEqual(rejected.exception.code, 'invalid_request')
            self.assertEqual(self.ledger.read_bytes(), before)
            for args in ({'subject_id': 'stage:missing', 'intent': 'prepare'},
                         {'subject_id': stage_id, 'intent': 'accept'},
                         {'subject_id': 'all', 'intent': 'prepare'},
                         {'subject_id': stage_id, 'intent': 'prepare', 'allocation_id': 'another'}):
                with self.assertRaises(entry.PreparationError):
                    progress.read_projection(self.checkpoint, projection='transaction', **args)
                self.assertEqual(self.ledger.read_bytes(), before)
            result = original(request, context, evidence, action)
            self.assertEqual(result, (str(self.flow), current['handoff']['scope']['baseline']))
            self.assertEqual(self.ledger.read_bytes(), before)
            checked.append(True)
            if os.environ.get('T01_LEDGER_PREPARE_METRICS_PATH'):
                Path(os.environ['T01_LEDGER_PREPARE_METRICS_PATH']).write_text(json.dumps({
                    'identity_bytes': identity.bytes_read, 'transaction_bytes': target.bytes_read,
                    'actual_files_read': len(reads), 'actual_bytes_read': sum(size for _, size in reads),
                    'read_paths': [path for path, _ in reads],
                    'returned_fields': sorted(current), 'baseline': result[1]}, indent=2) + '\n')
            return result
        with patch.object(ledger_control, '_start_git_authority', side_effect=verify_original):
            # This existing-authority fixture supports B preparation. Stage-3
            # ledger-backed launch retains its original strict entry boundary;
            # preparation changes the live topic control view, not source bytes.
            with self.assertRaises(entry.PreparationError) as rejected:
                self.invoke('start', {'handoff': value,
                    'control': {'context': self.context, 'discussion': envelope, 'store_ref': pointer}})
        self.assertEqual(checked, [True])
        self.assertEqual(rejected.exception.code, 'entry_changed')
        self.assertEqual(str(rejected.exception), 'entry evidence changed: requirement')
        latest = protocol.handle({**self.base, 'operation': 'read-topic'})
        self.assertEqual(latest['workflow_control']['controller_ref'], self.context['controller_ref'])
        self.assertEqual(latest['workflow_control']['handoff_progress']['state'], 'dispatcher-pending')
        self.assertIsNone(latest['workflow_control']['carrier']['ref'])
        self.assertEqual(latest['workflow_control']['handoff_progress']['binding'], value['binding'])
        self.assertEqual(latest['workflow_control']['handoff_progress']['allowed_paths'],
                         value['scope']['implementation_paths'])
        self.assertEqual(latest['ledger_revision'], envelope['expected_ledger_revision'] + 1)
        self.assertEqual(latest['workflow_control_ref']['control_revision'], pointer['control_revision'] + 1)
        self.assertNotEqual(self.ledger.read_bytes(), ledger_before)
        self.assertNotEqual(latest['workflow_control_ref'], pointer)
        current = self.state()
        self.assertEqual(current['control']['store_ref'], latest['workflow_control_ref'])
        self.assertEqual(current['dispatch']['status'], 'prepared')
        self.assertEqual(current['step'], 'launch')
        self.assertIsNone(current['action'])
        self.assertIsNone(current['transaction'])

    def test_attached_flow_publication_cleanup_acceptance_and_phase_replay(self):
        self.attached_publication_chain()

    def test_attached_final_recovery_keeps_completed_phase_and_active_closure(self):
        metrics = []
        replace = os.replace
        directory = self.checkpoint.with_name(self.checkpoint.name + '.state')
        def tracked_commit(source, target):
            if Path(target) == self.checkpoint:
                envelope = json.loads(Path(source).read_bytes())
                if envelope.get('store_format') == progress.state_store.FORMAT:
                    digest = envelope['current']['sha256']
                    root = json.loads((directory / 'objects' / digest[:2] / digest[2:4] / digest).read_bytes())['value']
                    subject_ref = root['subjects'][root['metadata']['stage_subject']]
                    digest = subject_ref['sha256']
                    state = json.loads((directory / 'objects' / digest[:2] / digest[2:4] / digest).read_bytes())['value']
                    result = state.get('transaction_result')
                    if state['stage'] == 3 and (state.get('transaction') or {}).get('operation') == 'accept' and result:
                        self.assertEqual(result['kind'], 'stage-transaction-result-ref-v1')
                        self.assertEqual(result['request_digest'], entry.digest(state['transaction']))
                        self.assertLessEqual(len(progress.state_store.encode(result)), 1024)
                        self.assertLessEqual(subject_ref['size'], progress.state_store.SUBJECT_LIMIT)
                        store = progress.state_store.open_store(self.checkpoint)
                        self.assertLessEqual(store.bytes_read, progress.state_store.READ_LIMIT)
                        metrics.append({'root_bytes': len(Path(source).read_bytes()),
                            'subject_bytes': subject_ref['size'], 'result_ref_bytes': len(progress.state_store.encode(result)),
                            'result_proof_bytes': result['reference']['size'],
                            'store_read_files': store.files_read, 'store_read_bytes': store.bytes_read})
            return replace(source, target)
        with patch.object(os, 'replace', tracked_commit):
            self.attached_publication_chain(recover=True)
        self.assertEqual(len(metrics), 2, 'both original and recovered acceptance save one exact B proof')
        print('recovery-result-capacity metrics:', json.dumps(metrics, sort_keys=True))
        if os.environ.get('T01_RECOVERY_RESULT_METRICS_PATH'):
            Path(os.environ['T01_RECOVERY_RESULT_METRICS_PATH']).write_text(json.dumps(metrics, indent=2) + '\n')

    def attached_publication_chain(self, recover=False):
        self.begin('stepwise')
        self.invoke('observe', self.observation())
        self.activate_designer()
        self.write_planning_spec()
        self.flow_git('add', 'docs/spec.md')
        self.flow_git('commit', '-qm', 'planning')
        candidate = self.flow_git('rev-parse', 'HEAD')
        proposal_digest = self.propose_design_scope(candidate)
        self.ready_publication(candidate, 'controller:planning')
        self.seal_design_scope(proposal_digest)
        result = self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:planning'})
        self.assertEqual(result['next_action']['operation'], 'publication-receipt', result)
        pending = self.invoke('receive-publication')['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'], 'answer': 'accept', 'reference': 'controller:accept-plan'})
        self.complete_phase()
        pending = self.state()['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:enter-stage3'})
        self.finish_next(3, 'stepwise')
        checkpoint = self.state()['control']['context']['handoff_progress']
        accepted_execution = checkpoint['executions'][-1]
        self.assertEqual(accepted_execution['git_snapshot']['head'], checkpoint['git_baseline_commit'])
        self.assertEqual(set(accepted_execution['git_snapshot']['files']), set(checkpoint['allowed_paths']))
        self.assertIsNone(accepted_execution['git_snapshot']['files']['impl.py'])
        self.assertEqual(accepted_execution['git_result_snapshot']['impl.py'],
                         progress.control_git.file_fingerprint(self.flow, 'impl.py'))
        self.complete_phase()
        pending = self.state()['pending']
        self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
            'answer': 'confirm', 'reference': 'controller:enter-stage4'})
        predecessor = self.state()['accepted']
        value = self.input_for(4, predecessor)
        self.context = self.context_for(4)
        self.begin('stepwise', value)
        self.invoke('observe', self.observation(ref='native:closure'))
        if recover:
            original_stage3 = copy.deepcopy(self.state()['implementation_predecessor'])
            original_delivery, _, _ = progress.read_implementation_delivery(self.checkpoint, original_stage3)
            original_design = copy.deepcopy(original_delivery['design_predecessor'])
            completed_phase = copy.deepcopy(self.phases[3])
            (self.root / 'other-task.md').write_text('parallel update\n')
            self.git('add', 'other-task.md'); self.git('commit', '-qm', 'parallel task')
            observed = self.observation('stopped', 'result', ref='native:closure')
            observed['closure'] = {'ref':'native:closure', 'candidate':predecessor['payload']['candidate_commit'],
                'merge':None, 'ancestor_verified':False, 'changed_paths':[], 'binding':self.binding,
                'checks':[], 'worktree_removed':False, 'branch_removed':False, 'disposition':'deleted',
                'disposition_verified':False, 'implementation_problem':'target advanced at final publication'}
            self.invoke('observe', observed)
            restored = self.invoke('implementation-recovery', {'reference':'controller:final', 'closure_digest':self.state()['handoff']['digest']})
            self.assertEqual(self.state()['stage'], 3, restored.get('error'))
            continued = self.invoke('observe', self.observation('idle','result',ref='native:dispatcher'))
            self.assertEqual(continued['next_action']['operation'], 'continue-host', continued.get('error'))
            self.flow_git('merge','--no-edit','main')
            candidate = self.flow_git('rev-parse','HEAD')
            self.invoke('observe', self.observation('stopped','result',ref='native:dispatcher'))
            self.invoke('control', {'action':'capture-integration', 'evidence':{'reference':'controller:merged'},
                'receipt':{'controller_ref':'task','reference':'controller:merged'}})
            # The topic carrier can prepare the transfer under its own identity.
            # A separate Dispatcher cannot inherit that conversation binding.
            parent = self.state()['handoff']
            request = self.execution_transfer_request()
            source = self.state()['control']['context']['handoff_progress']['integration_source']
            request['scope']['owned_paths'] = copy.deepcopy(source['pending_paths'])
            request['authorization']['scope_digest'] = entry.digest(request['scope'])
            request['semantic'].update(adopt_paths=copy.deepcopy(source['pending_paths']),
                                       adoption_snapshot_digest=source['snapshot_digest'])
            request['expected_entry'] = entry.resolve(request['entry'])
            ledger = self.ledger.read_bytes()
            checkpoint = self.checkpoint.read_bytes()
            snapshot = progress.control_git.snapshot(self.flow)
            prepared = fixtures.transfer.handoff.prepare(request)
            self.assertEqual(prepared['controller_ref'], parent['controller_ref'])
            self.assertEqual(prepared['requirement_identity'], parent['requirement_identity'])
            self.assertEqual(prepared['expected_entry']['actor']['role'], 'controller')
            self.assertEqual(prepared['expected_entry']['actor']['thread_id'], 'task')
            with patch.dict(self.settings, thread_id='dispatcher-runtime'):
                with self.assertRaises(entry.PreparationError) as error:
                    self.inherited_execution_input()
                self.assertEqual(error.exception.code, 'identity_mismatch')
            self.assertEqual(self.ledger.read_bytes(), ledger)
            self.assertEqual(self.checkpoint.read_bytes(), checkpoint)
            self.assertEqual(progress.control_git.snapshot(self.flow), snapshot)
            payload = {'candidate_commit':candidate, 'artifacts':['impl.py'], 'checks':['verified'],
                'review':{axis:{'candidate':candidate,'reviewer_ref':axis,'status':'accepted'} for axis in ('standards','spec')},
                'verification':{'candidate':candidate}}
            accepted = self.accept_current(payload, 'native:dispatcher',
                evidence_suffix='-attached-final-recovery')
            self.assertEqual(accepted['status'],'accepted',accepted.get('error'))
            self.assertEqual(self.phases[3], completed_phase)
            self.assertEqual(self.state()['recovery_predecessor'], original_stage3)
            self.assertEqual(self.state()['design_predecessor'], original_design)
            retained, _, _ = progress.read_implementation_delivery(self.checkpoint, original_stage3)
            self.assertEqual(retained, original_delivery)
            pending = self.state()['pending']
            self.invoke('decide', {'decision_id':pending['decision_id'], 'subject':pending['subject'],
                'answer':'confirm','reference':'controller:return-stage4'})
            predecessor = self.state()['accepted']
            next_input = fixtures.ProgressTests.input_for(self,4,predecessor)
            next_input['authorization']['phase'] = copy.deepcopy(value['authorization']['phase'])
            self.context = self.context_for(4)
            self.begin('stepwise',next_input)
            self.invoke('observe',self.observation(ref='native:closure'))
        candidate = predecessor['payload']['candidate_commit']
        self.ready_publication(candidate, 'controller:closure', 'native:closure')
        result = self.invoke('publication', {'candidate_commit': candidate, 'reference': 'controller:closure',
            'expected_target_head': self.git('rev-parse', 'HEAD')})
        self.assertFalse(self.flow.exists(), result)
        # Crash after B receives the result but before C consumes its saved transaction.
        with patch.object(progress.Progress, 'apply', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke('receive-publication')
        with patch.object(supervision, 'complete_worktree', side_effect=AssertionError('republished')):
            pending = self.invoke('resume')['pending']
            self.invoke('decide', {'decision_id': pending['decision_id'], 'subject': pending['subject'],
                'answer': 'accept', 'reference': 'controller:final-accept'})
            result = self.complete_phase()
            self.assertTrue(result['workflow_completion']['completed'])
            self.assertTrue(self.invoke('inspect')['workflow_completion']['completed'])
        self.assertEqual(entry.topic_read(str(self.root), self.attachment)['current_phase'], 4)
        self.assertFalse(self.flow.exists())


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(AttachedFlowTests(name) for name in loader.getTestCaseNames(AttachedFlowTests)
        if name in AttachedFlowTests.__dict__)
