"""Real attached A/B/C/Phase/Git closure; only native host receipts are fixtures."""
import copy
import json
import re
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

import test_workflow_progress as fixtures
import test_entry_prepare

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
                   '### D1 — Plan\n```execution-plan-v1\n' +
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
            self.invoke('lifecycle', {'request': request})
            self.assertEqual(self.ledger.read_bytes(), before)
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

    def test_attached_stage3_direct_policy_reaches_review_without_execution(self):
        from test_review_first_validation import ReviewFirstTests
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
        value['semantic']['implementation_policy'] = ReviewFirstTests.direct_policy(self, value)
        self.context = self.context_for(3)
        self.begin('stepwise', value)
        self.invoke('observe', self.observation(ref='native:dispatcher'))
        (self.flow / 'impl.py').write_text("print('attached')\n")
        self.flow_git('add', 'impl.py'); self.flow_git('commit', '-qm', 'direct attached output')
        self.invoke('observe', self.observation('stopped', 'result', ref='native:dispatcher'))
        result = self.invoke('control', ReviewFirstTests.direct_candidate_request(self))
        checkpoint = self.state()['control']['context']['handoff_progress']
        self.assertEqual(checkpoint['state'], 'reviewable', result)
        self.assertEqual(checkpoint['executions'], [])
        self.assertEqual(self.state()['handoff']['authorization']['phase'], value['authorization']['phase'])
        self.assertIsNone(self.state()['dispatch']['delivery'])

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
        # This fixture's older Stage-2 helper ran with a local control port.
        # Seed its already-bound Stage-3 context as the original ledger record,
        # then exercise revision only through the real attached C/B port.
        outer = progress.read_record(self.checkpoint)
        current_context = copy.deepcopy(outer[progress.KEY]['control']['context'])
        frontmatter, records = protocol._load_records(self.ledger)
        records['Phase Results'].append({'result_id': 'WC-' + self.attachment['actor_topic_id'],
            'result_kind': 'workflow-control', 'topic_id': self.attachment['actor_topic_id'],
            'record_revision': 0, 'state': 'active',
            'data_json': protocol._canonical_json(current_context)})
        self.ledger.write_bytes(protocol._render_records_ledger(frontmatter, records))
        topic = entry.topic_read(str(self.root), self.attachment)
        outer[progress.KEY]['control']['discussion'] = {**self.base,
            'expected_ledger_revision': topic['ledger_revision'],
            'expected_topic_revision': topic['record_revision'],
            'idempotency_key': str(uuid.uuid4())}
        progress.atomic_save(self.checkpoint, outer)
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
        match = re.search(r'```execution-plan-v1\n(.*?)\n```', content, re.DOTALL)
        updated = copy.deepcopy(plan)
        updated['plan_version'] = 2
        updated['steps'][0]['approach'].append('move private helper')
        spec.write_text((content[:match.start(1)] + json.dumps(updated) + content[match.end(1):])
                        .replace('Plan Version: 1', 'Plan Version: 2'))
        self.flow_git('add', 'docs/spec.md')
        self.flow_git('commit', '--only', '-qm', 'attached private revision', '--', 'docs/spec.md')
        result = self.invoke('commit-plan-revision', {'revision_id': report['revision_id'],
            'controller_ref': 'task', 'write_release': stopped['next_action']['write_release'],
            'planning_commit': self.flow_git('rev-parse', 'HEAD')})
        self.assertEqual(result['next_action']['operation'], 'plan-revision-activated')
        self.assertEqual(self.ledger, old_ledger)
        current = protocol.handle({**self.base, 'operation': 'read-topic'})['workflow_control']
        self.assertEqual(current['controller_ref'], 'task')
        self.assertEqual(current['topic_ref'], self.attachment['actor_topic_id'])
        self.assertEqual(len(current['handoff_progress']['plan_revisions']), 1)

    def test_attached_flow_publication_cleanup_acceptance_and_phase_replay(self):
        self.attached_publication_chain()

    def test_attached_final_recovery_keeps_completed_phase_and_active_closure(self):
        self.attached_publication_chain(recover=True)

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
            original_stage3 = copy.deepcopy(self.state()['history'][-1])
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
            accepted = self.accept_current(payload,'native:dispatcher')
            self.assertEqual(accepted['status'],'accepted',accepted.get('error'))
            self.assertEqual(self.phases[3], completed_phase)
            self.assertIn(original_stage3, self.state()['history'])
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
