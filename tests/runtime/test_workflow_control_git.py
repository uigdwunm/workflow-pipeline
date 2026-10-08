"""Control decisions consume actual Git evidence through the production adapter."""
import hashlib
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/shared/scripts"))
import test_workflow_control
import execution_plan
import workflow_control as control
import workflow_control_git as control_git
import supervision_protocol as supervision

CLI = Path(__file__).resolve().parents[2] / "src/shared/scripts/workflow_control_git.py"

class WorkflowGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repository'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        (self.repo / 'docs').mkdir()
        (self.repo / 'docs/draft.md').write_text('initial requirement\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        self.base = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], text=True, capture_output=True, check=True).stdout.strip()

    def call(self, action, evidence, context):
        if getattr(self, "coverage_flow", None) is not None:
            return self.public_coverage_call(action, evidence, context)
        evidence = dict(evidence)
        if action in {'assign', 'execution-result', 'accept-execution'} and context is not None:
            executions = (context.get('handoff_progress') or {}).get('executions', [])
            identity = 'task_id' if action == 'assign' else 'agent_ref'
            execution = next((item for item in executions
                if item.get(identity) == evidence.get(identity)), None)
            if execution is not None and 'plan_refs' in execution:
                if action == 'assign':
                    evidence['write_release'] = control.digest(['fixture:release', execution['allocation_digest']])
                else:
                    evidence.update(plan_refs=execution['plan_refs'],
                        plan_authority=execution['plan_authority'],
                        write_release=execution['write_release'])
        return self.git_call(action, evidence, context)

    def git_call(self, action, evidence, context):
        result = subprocess.run([sys.executable, str(CLI)], text=True, capture_output=True,
            input=json.dumps({'repository': str(self.repo), 'baseline': self.base,
                'request': {'schema_version': context['schema_version'], 'action': action, 'actor_ref': context['controller_ref'], 'context': context, 'evidence': evidence}}))
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def start_public_coverage_flow(self, paths=('a.py', 'b.py'), *, accepted_read_only=False):
        """Real A/B/C/Git producer for cases that consume original review coverage."""
        import test_workflow_progress as progress_fixture
        flow = progress_fixture.ProgressTests(methodName='runTest')
        preflight = progress_fixture.transfer.entry.skill_preflight
        real_verify_identity = preflight.verify_identity
        package_root = Path(__file__).resolve().parents[2] / 'skills'
        names = ('design-discussion', 'problem-framing', 'solution-design',
                 'guided-implementation', 'change-closure')
        identities = {name: preflight.package_identity(str(package_root / name / 'SKILL.md'), name)
                      for name in names}
        base_setup = progress_fixture.transfer.test_entry_prepare.EntrySupport.setUp
        def canonical_setup(case):
            base_setup(case)
            case.package.clear()
            case.package.update(identities['problem-framing'])
            preflight.preflight.return_value = {'packages': identities, 'external': {}}
            registry = {'source': 'host-current-skills', 'entries': [
                {'name': name, 'entry': identities[name]['entry'],
                 'source': 'fixture-host', 'enabled': True} for name in names]}
            case.request = case.with_registration({**case.request, 'registry': registry},
                project=case.root, receipt='host:canonical-fixture')
            identity_check = patch.object(preflight, 'verify_identity', side_effect=real_verify_identity)
            identity_check.start()
            case.addCleanup(identity_check.stop)
        with patch.object(progress_fixture.transfer.test_entry_prepare.EntrySupport,
                          'setUp', canonical_setup):
            flow.setUp()
        self.addCleanup(flow.doCleanups)
        self.repo = flow.flow
        (self.repo / 'docs/draft.md').write_text('initial requirement\n')
        flow.flow_git('add', 'docs/draft.md')
        flow.flow_git('commit', '-qm', 'protected caller document')
        self.base = flow.flow_git('rev-parse', 'HEAD')
        flow.git('merge', '--ff-only', self.base)
        request = flow.input_for(3)
        scope = request['scope']
        scope.update(owned_paths=list(paths), implementation_paths=list(paths), closure_paths=['README.md'])
        scope['protected_paths'] = sorted(set(scope['protected_paths'] + ['docs/draft.md']))
        original = scope['downstream']['manifest']
        implementation = next(item for item in original if item['owner'] == 'implementation')
        closure = next(item for item in original if item['owner'] == 'closure')
        scope['downstream']['manifest'] = sorted([
            *[{**copy.deepcopy(implementation), 'path': path} for path in paths],
            {**copy.deepcopy(closure), 'path': 'README.md'}], key=lambda item: item['path'])
        context = flow.context_for(3)
        plan = self.plan_evidence(context, list(paths), accepted_read_only=accepted_read_only)
        request['semantic']['inline_plan'] = copy.deepcopy(
            plan['execution_authority']['execution_plan']['catalog']['tickets'][0]['plan'])
        request['authorization']['scope_digest'] = control.digest(scope)
        changed = control_git.changed_paths(self.repo, flow.git('rev-parse', 'main'))
        baseline = {'source_commit': flow.frozen['commit'], 'requirement_sha256': flow.frozen['sha256'],
            'binding_base': flow.binding['base_commit'], 'baseline': self.base,
            'current': flow.flow_git('rev-parse', 'HEAD'), 'target': flow.git('rev-parse', 'main'),
            'owned': scope['implementation_paths'], 'protected': scope['protected_paths'],
            'changed_paths': sorted(changed)}
        self.assertEqual(changed, [], baseline)
        request.pop('inline_authority', None)
        flow.context = context
        flow.begin('continuous', request)
        flow.invoke('observe', flow.observation(ref='native:dispatcher'))
        self.coverage_flow = flow
        self.public_tasks, self.public_agents = {}, {}
        return flow.state()['control']['context']

    def public_coverage_call(self, action, evidence, context):
        """Retain original test actions while C owns every allocation and proof."""
        import test_workflow_progress as progress_fixture
        flow = self.coverage_flow
        original_settings = flow.settings['thread_id']
        original_host = copy.deepcopy(flow.request['host'])
        if action in {'assign', 'execution-result', 'accept-execution'}:
            flow.settings['thread_id'] = 'dispatcher-runtime'
            flow.request['host'].update(thread_id='dispatcher-runtime', role='implementation-dispatcher',
                source_ref=flow.state()['handoff']['controller_ref'],
                actor_ref=flow.state()['control']['context']['carrier']['ref'])
        try:
            if action == 'plan-execution':
                import test_layered_review as layered_fixture
                owner = progress_fixture.progress.Progress(flow.checkpoint,
                    progress_fixture.progress.read_active_owner(flow.checkpoint))
                due = owner.ticket_checks_due()
                if due:
                    checkpoint = owner.state['control']['context']['handoff_progress']
                    expected = {}
                    for accepted in control.current_accepted_results(checkpoint):
                        expected.update(execution_plan.accepted_owned_fingerprints(accepted))
                    changed = control_git.changed_paths(self.repo, self.git('rev-parse', 'HEAD'))
                    self.assertLessEqual(set(changed), set(expected), 'only original accepted bytes precede Ticket checks')
                    for path in changed:
                        self.assertEqual(control_git.file_fingerprint(self.repo, path), expected[path])
                    if changed:
                        self.git('add', '--', *changed)
                        self.git('commit', '-qm', 'accepted source for required Ticket checks')
                    for ticket in due:
                        checked = layered_fixture.LayeredReviewTests.early_ticket_check(
                            flow, self.git('rev-parse', 'HEAD'), ticket)
                        self.assertTrue((checked.get('next_action') or {}).get('result', {}).get('ok'), checked)
                flow.settings['thread_id'] = 'dispatcher-runtime'
                flow.request['host'].update(thread_id='dispatcher-runtime', role='implementation-dispatcher',
                    source_ref=flow.state()['handoff']['controller_ref'],
                    actor_ref=flow.state()['control']['context']['carrier']['ref'])
                task = evidence['task_id']
                previous_settings = flow.settings['thread_id']
                previous_host = copy.deepcopy(flow.request['host'])
                try:
                    request = (flow.inherited_execution_input() if flow.state().get('final_integration_recovery')
                               else flow.execution_input())
                    dispatcher = flow.state()['control']['context']['carrier']['ref']
                    flow.request['host']['actor_ref'] = dispatcher
                    request['entry']['host']['actor_ref'] = dispatcher
                    request['expected_entry'] = progress_fixture.transfer.entry.resolve(request['entry'])
                    request['scope']['owned_paths'] = list(evidence['paths'])
                    request['scope']['protected_paths'] = sorted(set(
                        request['scope']['protected_paths'] + evidence.get('read_only', [])))
                    request['authorization']['scope_digest'] = control.digest(request['scope'])
                    request['semantic']['plan_refs'] = copy.deepcopy(evidence['plan_refs'])
                    for field in ('adopt_paths', 'adoption_snapshot_digest'):
                        if field in evidence:
                            request['semantic'][field] = copy.deepcopy(evidence[field])
                    view = flow.invoke('allocation', {'allocation_id': task,
                        'operation': 'prepare', 'handoff': request})
                finally:
                    flow.settings['thread_id'] = previous_settings
                    flow.request['host'] = previous_host
                self.public_tasks[task] = task
            elif action == 'assign':
                task = self.public_tasks[evidence['task_id']]
                slot = flow.allocation_slot(task)
                descriptor = progress_fixture.transfer.dispatch.allocation_descriptor(slot['record'])
                if evidence['allocation_digest'] != descriptor['digest']:
                    return {'ok': False, 'error': 'allocation digest differs from the original C input'}
                view = flow.invoke('allocation', {'allocation_id': task, 'operation': 'bind',
                    'receipt': flow.receipt(slot['record'], ref=evidence['agent_ref'])})
                self.public_agents[evidence['agent_ref']] = task
            elif action in {'execution-result', 'accept-execution'}:
                task = self.public_agents[evidence['agent_ref']]
                slot = flow.allocation_slot(task)
                if action == 'execution-result':
                    original_input = progress_fixture.transfer.dispatch.dispatch_input(slot['record'])
                    payload = {key: copy.deepcopy(evidence[key]) for key in
                        ('changed_paths', 'file_hashes', 'tests', 'adopted_paths') if key in evidence}
                    payload.update(write_release=progress_fixture.transfer.dispatch.execution_release_token(slot['record']),
                        plan_refs=original_input['semantic']['plan_refs'],
                        plan_authority=flow.state()['control']['context']['handoff_progress']['plan_authority'])
                    view = flow.invoke('allocation', {'allocation_id': task, 'operation': 'receive',
                        'receipt': flow.receipt(slot['record'], 'stopped', 'result', ref=evidence['agent_ref']),
                        'result': {'delivery_id': task + '-result', 'status': 'completed', 'payload': payload}})
                else:
                    view = flow.invoke('allocation', {'allocation_id': task, 'operation': 'accept',
                        'decision': {'reference': 'dispatcher:accepted:' + task}})
            elif action == 'candidate-ready':
                if not hasattr(self, 'original_ticket_coverage'):
                    flow.complete_fixture_local_reviews(evidence['commit'], '-git-candidate')
                    owner = progress_fixture.progress.Progress(flow.checkpoint,
                        progress_fixture.progress.read_active_owner(flow.checkpoint))
                    self.original_ticket_coverage = owner.build_review_coverage(evidence['commit'], delivery=False)
                selected = copy.deepcopy(evidence)
                dispatcher = flow.state()['control']['context']['carrier']['ref']
                selected['dispatcher_ref'] = dispatcher
                selected['source']['raw']['dispatcher_ref'] = dispatcher
                selected['ticket_coverage'] = copy.deepcopy(self.original_ticket_coverage)
                return self.git_call(action, selected, flow.state()['control']['context'])
            else:
                view = flow.invoke('control', {'action': action, 'evidence': evidence,
                    'receipt': {'controller_ref': flow.state()['control']['context']['controller_ref'],
                        'reference': evidence.get('reference', 'controller:' + action)}})
        except (progress_fixture.transfer.entry.PreparationError, control.ControlError) as error:
            return {'ok': False, 'error': str(error)}
        finally:
            flow.settings['thread_id'] = original_settings
            flow.request['host'] = original_host
        outcome = (view.get('next_action') or {}).get('result') or {}
        result = {**outcome, 'ok': view['status'] != 'blocked' and outcome.get('ok', True),
            'context': flow.state()['control']['context']}
        if action == 'plan-execution' and result['ok']:
            slot = flow.allocation_slot(evidence['task_id'])
            result['allocation_digest'] = progress_fixture.transfer.dispatch.allocation_descriptor(slot['record'])['digest']
        if view.get('error'):
            result['error'] = view['error']
        return result

    def public_start_allocations(self):
        context = self.start_public_coverage_flow()
        planned = self.call('plan-execution', self.execution_envelope(context, 'a', ['a.py']), context)
        self.assertTrue(planned['ok'], planned)
        bound = self.call('assign', {'task_id': 'a', 'agent_ref': 'a',
            'allocation_digest': planned['allocation_digest']}, planned['context'])
        self.assertTrue(bound['ok'], bound)
        return bound['context']

    def plan_evidence(self, context, paths, *, accepted_read_only=False, declared_dependency=True):
        scope = {'baseline': self.base, 'owned_paths': paths,
                 'implementation_paths': paths, 'closure_paths': [],
                 'protected_paths': ['docs/draft.md'],
                 'downstream': {'state': 'sealed', 'revision': 1,
                     'implementation_required': True, 'proposal_ref': 'fixture:scope',
                     'seal_ref': 'fixture:seal', 'manifest': [
                         {'path': path, 'owner': 'implementation', 'kind': 'source',
                          'operation': 'modify' if (self.repo / path).exists() else 'add',
                          'basis': {'commit': self.base, 'path': 'docs/draft.md',
                                    'section': 'initial requirement'}} for path in paths]}}
        plan = {'schema_version': 2, 'ticket_id': 'INLINE-GIT', 'plan_version': 1,
                'spec_ref': {'path': 'inline', 'section': 'brief'},
                'contract_refs': ['standalone:objective'], 'blocked_by': [],
                'verifications': [{'id': 'V-cli', 'behavior': 'CLI behavior',
                                   'seam': 'real control and Git adapter', 'commands': ['CLI']}],
                'steps': [
                    {'id': 'step-' + str(index),
                     'target': {'path': path, 'symbol': 'run_' + str(index),
                                'operation': item['operation']},
                     'capability': 'implement ' + path, 'approach': ['implement the planned function'],
                     'inputs': ['frozen requirement'], 'outputs': ['observable result'],
                     'failures': ['invalid input is reported'], 'depends_on': [],
                     'verification_refs': ['V-cli'], 'read_only': []}
                    for index, (path, item) in enumerate(zip(paths, scope['downstream']['manifest']))],
                'artifact_steps': []}
        if accepted_read_only:
            if declared_dependency:
                plan['steps'][1]['depends_on'] = [plan['steps'][0]['id']]
            plan['steps'][1]['read_only'] = [paths[0]]
        plan['behavior_slices'] = [
            {'id': step['id'], 'step_ids': [step['id']],
             'ac_refs': ['standalone:objective'], 'verification_refs': step['verification_refs'],
             'read_only': step['read_only'], 'shared_state': []} for step in plan['steps']]
        semantic = {'objective': 'implement the frozen requirement', 'testing_basis': 'CLI',
                    'completion_criteria': ['CLI'], 'constraints': ['stay in scope']}
        authority = execution_plan.inline_authority(plan, scope, semantic,
            context['requirement_identity'], repository=str(self.repo), input_digest='9' * 64)
        catalog = authority['execution_plan']
        return {'execution_authority': authority,
                'plan_authority': {'revision': authority['revision'],
                    'authority_digest': control.digest(authority),
                    'plan_digest': catalog['plan_digest'],
                    'contract_digest': catalog['contract_digest']}}

    def execution_envelope(self, context, task_id, paths, read_only=(), **extra):
        progress = context['handoff_progress']
        catalog = progress['execution_authority']['execution_plan']['catalog']
        ticket = catalog['tickets'][0]
        steps = {step['id']: step for step in ticket['plan']['steps']}
        refs = [execution_plan.step_ref(ticket, steps[step_id])
                for step_id in catalog['ordered_step_ids']
                if steps[step_id]['target']['path'] in paths]
        envelope = {'task_id': task_id, 'paths': list(paths), 'read_only': list(read_only),
                    'behavior': extra.pop('behavior', task_id),
                    'tests': list(dict.fromkeys([*extra.pop('tests', []), 'CLI'])),
                    'git_operations': [],
                    'configuration': test_workflow_control.WorkflowControlTests().configuration('execution-agent'),
                    'plan_refs': refs, 'plan_authority': progress['plan_authority'],
                    'group_digest': control.digest([progress['plan_authority'], refs])}
        return {**envelope, **extra}

    def test_binding_verifier_returns_facts_from_the_package_cli(self):
        binding = supervision.start_worktree({'repository': str(self.repo),
            'worktree': str(self.root / 'flow'), 'branch': 'codex/verifier',
            'target_branch': self.git('branch', '--show-current')})['binding']
        report = control_git.verify_binding(Path(binding['worktree']), binding)
        self.assertEqual(report, {'binding': binding, 'ok': True, 'verified': True,
                                  'current_commit': self.base})

    def test_binding_verifier_rejects_malformed_or_mismatched_cli_facts(self):
        binding = {'repository': str(self.repo)}
        valid = {'binding': binding, 'ok': True, 'verified': True, 'current_commit': self.base}
        outputs = ['not json', '{"ok":true,"ok":false}',
                   json.dumps({**valid, 'binding': {'repository': str(self.root)}}),
                   json.dumps({**valid, 'verified': 1}),
                   json.dumps({**valid, 'current_commit': 'HEAD'}),
                   ' ' * (control_git.MAX_BYTES + 1)]
        for output in outputs:
            with self.subTest(output=output[:80]), patch.object(control_git.subprocess, 'run',
                    return_value=subprocess.CompletedProcess([], 0, output, '')):
                with self.assertRaises(control.ControlError):
                    control_git.verify_binding(self.repo, binding)

    def test_scope_correction_requires_clean_unchanged_planning_baseline(self):
        original = control_git.scope_revision_snapshot(
            self.repo, self.base, ['docs/draft.md'], ['later.py'])
        self.assertIsNone(original['files']['later.py'])
        (self.repo / 'later.py').write_text('unapproved before correction\n')
        with self.assertRaisesRegex(control.ControlError, 'clean planning baseline'):
            control_git.scope_revision_snapshot(
                self.repo, self.base, ['docs/draft.md'], ['later.py'])
        self.git('add', 'later.py')
        self.git('commit', '-qm', 'implementation already started')
        with self.assertRaisesRegex(control.ControlError, 'clean planning baseline'):
            control_git.scope_revision_snapshot(
                self.repo, self.base, ['docs/draft.md'], ['later.py'])

    def test_plan_revision_verifier_preserves_staged_implementation_partition(self):
        (self.repo / 'docs/plan.md').write_text('private plan v1\n')
        self.git('add', 'docs/plan.md')
        self.git('commit', '-qm', 'sealed planning source')
        sealed = self.git('rev-parse', 'HEAD')
        authority = {'candidate_commit': sealed, 'scope': {
            'implementation_paths': ['impl.py'], 'closure_paths': ['docs/plan.md']}}
        (self.repo / 'impl.py').write_text('staged implementation\n')
        self.git('add', 'impl.py')
        stopped = control_git.plan_revision_snapshot(self.repo, authority,
            ['docs/draft.md', 'docs/plan.md'], ['docs/plan.md'])
        (self.repo / 'docs/plan.md').write_text('private plan v2\n')
        self.git('add', 'docs/plan.md')
        self.git('commit', '--only', '-qm', 'private plan revision', '--', 'docs/plan.md')
        candidate = self.git('rev-parse', 'HEAD')
        facts = control_git.verify_plan_revision(self.repo, stopped, candidate)
        self.assertEqual(facts['changed_paths'], ['docs/plan.md'])
        self.assertEqual(facts['parent'], sealed)
        (self.repo / 'impl.py').write_text('changed during revision\n')
        self.git('add', 'impl.py')
        with self.assertRaisesRegex(control.ControlError, 'index stages or flags changed'):
            control_git.verify_plan_revision(self.repo, stopped, candidate)

    def test_t04_plan_revision_commit_keeps_sole_parent_and_protected_partition(self):
        (self.repo / 'docs/plan.md').write_text('private plan v1\n')
        self.git('add', 'docs/plan.md')
        self.git('commit', '-qm', 'sealed planning source')
        sealed = self.git('rev-parse', 'HEAD')
        authority = {'candidate_commit': sealed, 'scope': {
            'implementation_paths': ['impl.py'], 'closure_paths': ['docs/plan.md']}}
        stopped = control_git.plan_revision_snapshot(self.repo, authority,
            ['docs/draft.md', 'docs/plan.md'], ['docs/plan.md'])
        (self.repo / 'docs/plan.md').write_text('private plan v2\n')
        self.git('add', 'docs/plan.md')
        self.git('commit', '--only', '-qm', 'private revision', '--', 'docs/plan.md')
        candidate = self.git('rev-parse', 'HEAD')
        original = (self.repo / 'docs/draft.md').read_bytes()
        (self.repo / 'docs/draft.md').write_text('unapproved protected requirement\n')
        with self.assertRaisesRegex(control.ControlError, 'protected bytes changed'):
            control_git.verify_plan_revision(self.repo, stopped, candidate)
        (self.repo / 'docs/draft.md').write_bytes(original)
        self.assertEqual(control_git.verify_plan_revision(self.repo, stopped, candidate)['parent'], sealed)
        self.git('commit', '--allow-empty', '-qm', 'second commit is outside the release')
        with self.assertRaisesRegex(control.ControlError, 'only parent'):
            control_git.verify_plan_revision(self.repo, stopped, self.git('rev-parse', 'HEAD'))

    def test_changed_requirement_is_received_and_archived_after_takeover(self):
        helper = test_workflow_control.WorkflowControlTests()
        ctx = helper.bound()
        original = hashlib.sha256((self.repo / 'docs/draft.md').read_bytes()).hexdigest()
        ctx['requirement_identity']['sha256'] = original
        (self.repo / 'docs/draft.md').write_text('accepted completed requirement\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'completed requirement')
        commit = self.git('rev-parse', 'HEAD').strip()
        completed_hash = hashlib.sha256((self.repo / 'docs/draft.md').read_bytes()).hexdigest()
        delivery = {'delivery_id': 'delivery', 'source_ref': 'old', 'attempt': ctx['carrier']['attempt'],
                    'requirement_identity': {'path': 'docs/draft.md', 'version': 2, 'sha256': completed_hash},
                    'commit': commit, 'verified_commit_hash': completed_hash}
        received = self.call('receive', delivery, ctx)
        self.assertTrue(received['ok'], received)
        accepted = self.call('accept', {'delivery_digest': received['delivery_digest']}, received['context'])
        ready = self.call('successor-ready', {'ref': 'native-successor', 'stage': 2, 'role': 'solution-designer',
            'input_digest': received['delivery_digest'], 'binding_verified': True, 'activated': False,
            'confirmed': True, 'archive_ref': 'old', 'takeover_proof': test_workflow_control.takeover_proof('old', ctx['carrier']['attempt'])}, accepted['context'])
        archived = self.call('archive', {'handoff_id': ready['handoff_id']}, ready['context'])
        self.assertEqual(archived['effects'][0]['ref'], 'old')
        delivery['commit'] = self.base
        self.assertFalse(self.call('receive', delivery, ctx)['ok'])

    def test_execution_reads_real_diff_and_rejects_executor_git_commit(self):
        helper = test_workflow_control.WorkflowControlTests()
        ctx = helper.context(); ctx['stage'] = 3
        branch = self.git('branch', '--show-current').strip()
        started_flow = subprocess.run([sys.executable, str(CLI.with_name('supervision_protocol.py')), 'start-worktree'],
            input=json.dumps({'repository': str(self.repo), 'worktree': str(self.root / 'flow'), 'branch': 'codex/flow', 'target_branch': branch}),
            text=True, capture_output=True, check=True)
        binding = json.loads(started_flow.stdout)['binding']
        self.repo = self.root / 'flow'
        plan = self.plan_evidence(ctx, ['src/a.py'])
        started = self.call('start-dispatch', {'binding': binding, 'binding_verified': True,
            'allowed_paths': ['src/a.py'], 'protected_paths': ['docs/draft.md'], 'authority_digest': 'c' * 64,
            'testing_basis': 'CLI', 'validation_plan': __import__('test_workflow_control').plan_for(binding['worktree']),
            'configuration': helper.configuration('implementation-dispatcher'), **plan}, ctx)
        self.assertTrue(started['ok'], started)
        bound = self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']}, started['context'])
        envelope = self.execution_envelope(bound['context'], 'a', ['src/a.py'], ['docs/draft.md'])
        planned = self.call('plan-execution', envelope, bound['context'])
        assigned = self.call('assign', {'task_id': 'a', 'agent_ref': 'executor',
            'allocation_digest': planned['allocation_digest']}, planned['context'])
        self.assertTrue(assigned['ok'], assigned)
        # Only the external native adapter is substituted. Control allocation,
        # Git snapshots and result intake remain real subprocess boundaries.
        adapter = subprocess.run([sys.executable, '-c',
            'import json, pathlib, sys; request=json.load(sys.stdin); '
            'path=pathlib.Path(request["cwd"])/request["effect"]["envelope"]["paths"][0]; '
            'path.parent.mkdir(); path.write_text("print(1)\\n"); '
            'print(json.dumps({"ref": request["ref"], "trace": [request["effect"]], "stopped": True}))'],
            input=json.dumps({'cwd': str(self.repo), 'ref': 'executor', 'effect': planned['effects'][0]}), text=True, capture_output=True, check=True)
        receipt = json.loads(adapter.stdout)
        self.assertEqual(receipt['trace'][0]['role'], 'execution-agent')
        self.assertEqual(receipt['trace'][0]['envelope']['configuration']['model'], 'supported')
        result = {'agent_ref': receipt['ref'], 'stopped': receipt['stopped'], 'changed_paths': ['src/a.py'],
                  'file_hashes': {'src/a.py': hashlib.sha256((self.repo/'src/a.py').read_bytes()).hexdigest()},
                  'tests': ['CLI passed'], 'git_unchanged': True}
        received = self.call('execution-result', result, assigned['context'])
        self.assertTrue(received['ok'], received)
        execution = received['context']['handoff_progress']['executions'][0]
        self.assertEqual(list(execution['file_hashes']), ['src/a.py'])
        self.git('add', '.'); self.git('commit', '-qm', 'unauthorized executor commit')
        self.assertFalse(self.call('execution-result', result, assigned['context'])['ok'])

    def start_dispatcher(self, *, accepted_read_only=False, declared_dependency=True):
        helper = test_workflow_control.WorkflowControlTests()
        target = self.git('branch', '--show-current')
        receipt = subprocess.run([sys.executable, str(CLI.with_name('supervision_protocol.py')), 'start-worktree'],
            input=json.dumps({'repository': str(self.repo), 'worktree': str(self.root / 'flow'), 'branch': 'codex/flow', 'target_branch': target}),
            text=True, capture_output=True, check=True)
        binding = json.loads(receipt.stdout)['binding']; self.repo = self.root / 'flow'
        context = helper.context(); context['stage'] = 3
        plan = self.plan_evidence(context, ['a.py', 'b.py'], accepted_read_only=accepted_read_only,
                                  declared_dependency=declared_dependency)
        started = self.call('start-dispatch', {'binding': binding, 'binding_verified': True,
            'allowed_paths': ['a.py', 'b.py'], 'protected_paths': ['docs/draft.md'], 'authority_digest': 'c' * 64,
            'testing_basis': 'CLI', 'validation_plan': __import__('test_workflow_control').plan_for(binding['worktree']),
            'configuration': helper.configuration('implementation-dispatcher'), **plan}, context)
        self.assertTrue(started['ok'], started)
        return self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']}, started['context'])['context']

    def start_allocations(self, peers=False):
        helper = test_workflow_control.WorkflowControlTests()
        context = self.start_dispatcher()
        for name in ('a', 'b') if peers else ('a',):
            planned = self.call('plan-execution', self.execution_envelope(context, name, [name+'.py']), context)
            context = self.call('assign', {'task_id': name, 'agent_ref': name, 'allocation_digest': planned['allocation_digest']}, planned['context'])['context']
        return context

    def accept_second_plan_step(self, context):
        planned = self.call('plan-execution', self.execution_envelope(context, 'b', ['b.py']), context)
        self.assertTrue(planned['ok'], planned)
        assigned = self.call('assign', {'task_id': 'b', 'agent_ref': 'b',
            'allocation_digest': planned['allocation_digest']}, planned['context'])
        self.assertTrue(assigned['ok'], assigned)
        (self.repo / 'b.py').write_text('verified second step\n')
        received = self.call('execution-result', self.result_evidence('b', ['b.py']), assigned['context'])
        self.assertTrue(received['ok'], received)
        accepted = self.call('accept-execution', {'agent_ref': 'b', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        return accepted['context']

    def test_allocation_rejects_implementation_written_before_first_executor(self):
        context = self.start_dispatcher()
        (self.repo/'a.py').write_text('dispatcher wrote first\n')
        helper = test_workflow_control.WorkflowControlTests()
        envelope = self.execution_envelope(context, 'first', ['a.py'],
            behavior='first production slice', tests=['red and green'])
        self.assertFalse(self.call('plan-execution', envelope, context)['ok'])
        (self.repo/'a.py').unlink()
        self.assertTrue(self.call('plan-execution', envelope, context)['ok'])

    def test_binding_rejects_changes_after_allocation_preparation(self):
        context = self.start_dispatcher()
        helper = test_workflow_control.WorkflowControlTests()
        planned = self.call('plan-execution', self.execution_envelope(context, 'first', ['a.py'],
            behavior='real caller slice', tests=['focused']), context)
        self.assertTrue(planned['ok'], planned)
        binding = {'task_id': 'first', 'agent_ref': 'executor',
                   'allocation_digest': planned['allocation_digest']}
        (self.repo/'a.py').write_text('dispatcher wrote before handoff\n')
        self.assertFalse(self.call('assign', binding, planned['context'])['ok'])
        (self.repo/'a.py').unlink()
        self.assertTrue(self.call('assign', binding, planned['context'])['ok'])

    def test_git_enrichment_rejects_a_checkpoint_over_capacity_before_release(self):
        context = self.start_dispatcher()
        envelope = self.execution_envelope(context, 'capacity', ['a.py'])
        request = {'schema_version': context['schema_version'], 'action': 'plan-execution',
                   'actor_ref': 'controller', 'context': context, 'evidence': envelope}
        payload = {'repository': str(self.repo), 'baseline': self.base, 'request': request}
        pure = control.transition(request)
        enriched = control_git.verified_transition(payload)
        pure_size = len(json.dumps(pure['context']).encode())
        enriched_size = len(json.dumps(enriched['context']).encode())
        self.assertGreater(enriched_size, pure_size)
        capacity = pure_size + (enriched_size - pure_size) // 2
        original = copy.deepcopy(context)

        with patch.object(control, 'MAX_BYTES', capacity), patch.object(control_git, 'MAX_BYTES', capacity):
            self.assertTrue(control.transition(request)['ok'])
            with self.assertRaisesRegex(control.ControlError, 'control history exceeds byte limit'):
                control_git.verified_transition(payload)

        self.assertEqual(context, original)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertNotIn('write_release', context['handoff_progress']['executions'][-1]
                         if context['handoff_progress']['executions'] else {})

    def test_dispatch_baseline_enrichment_obeys_checkpoint_capacity(self):
        helper = test_workflow_control.WorkflowControlTests()
        target = self.git('branch', '--show-current')
        binding = supervision.start_worktree({'repository': str(self.repo),
            'worktree': str(self.root / 'flow'), 'branch': 'codex/flow',
            'target_branch': target})['binding']
        self.repo = self.root / 'flow'
        context = helper.context()
        context['stage'] = 3
        evidence = {'binding': binding, 'binding_verified': True,
            'allowed_paths': ['a.py', 'b.py'], 'protected_paths': ['docs/draft.md'],
            'authority_digest': 'c' * 64, 'testing_basis': 'CLI',
            'validation_plan': test_workflow_control.plan_for(binding['worktree']),
            'configuration': helper.configuration('implementation-dispatcher'),
            **self.plan_evidence(context, ['a.py', 'b.py'])}
        request = {'schema_version': context['schema_version'], 'action': 'start-dispatch',
                   'actor_ref': 'controller', 'context': context, 'evidence': evidence}
        payload = {'repository': str(self.repo), 'baseline': self.base, 'request': request}
        pure = control.transition(request)
        enriched = control_git.verified_transition(payload)
        self.assertEqual(enriched['context']['handoff_progress']['git_baseline_commit'], self.base)
        capacity = (len(json.dumps(pure['context']).encode()) +
                    len(json.dumps(enriched['context']).encode())) // 2
        with patch.object(control, 'MAX_BYTES', capacity), patch.object(control_git, 'MAX_BYTES', capacity):
            self.assertTrue(control.transition(request)['ok'])
            with self.assertRaisesRegex(control.ControlError, 'control history exceeds byte limit'):
                control_git.verified_transition(payload)

    def test_execution_result_snapshot_enrichment_obeys_checkpoint_capacity(self):
        context = self.start_allocations()
        (self.repo / 'a.py').write_text('accepted behavior\n')
        execution = context['handoff_progress']['executions'][0]
        evidence = {**self.result_evidence('a', ['a.py']),
                    'plan_refs': execution['plan_refs'],
                    'plan_authority': execution['plan_authority'],
                    'write_release': execution['write_release']}
        request = {'schema_version': context['schema_version'], 'action': 'execution-result',
                   'actor_ref': 'controller', 'context': context, 'evidence': evidence}
        payload = {'repository': str(self.repo), 'baseline': self.base, 'request': request}
        pure = control.transition(request)
        enriched = control_git.verified_transition(payload)
        self.assertIn('git_result_snapshot', enriched['context']['handoff_progress']['executions'][0])
        capacity = (len(json.dumps(pure['context']).encode()) +
                    len(json.dumps(enriched['context']).encode())) // 2
        with patch.object(control, 'MAX_BYTES', capacity), patch.object(control_git, 'MAX_BYTES', capacity):
            self.assertTrue(control.transition(request)['ok'])
            with self.assertRaisesRegex(control.ControlError, 'control history exceeds byte limit'):
                control_git.verified_transition(payload)

    def test_later_allocation_rejects_unaccepted_dispatcher_changes(self):
        context = self.start_allocations()
        (self.repo/'a.py').write_text('executor change\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        (self.repo/'b.py').write_text('dispatcher repair\n')
        helper = test_workflow_control.WorkflowControlTests()
        envelope = self.execution_envelope(accepted['context'], 'next', ['b.py'],
            behavior='later slice', tests=['focused'])
        self.assertFalse(self.call('plan-execution', envelope, accepted['context'])['ok'])

    def test_later_group_reads_exact_accepted_implementation(self):
        context = self.start_dispatcher(accepted_read_only=True)
        first = self.call('plan-execution', self.execution_envelope(context, 'a', ['a.py']), context)
        self.assertTrue(first['ok'], first)
        assigned = self.call('assign', {'task_id': 'a', 'agent_ref': 'a',
            'allocation_digest': first['allocation_digest']}, first['context'])
        self.assertTrue(assigned['ok'], assigned)
        (self.repo / 'a.py').write_text('accepted first group\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), assigned['context'])
        self.assertTrue(received['ok'], received)
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        context = accepted['context']
        second = self.execution_envelope(context, 'b', ['b.py'], ['a.py'])
        planned = self.call('plan-execution', second, context)
        self.assertTrue(planned['ok'], planned)
        self.assertFalse(self.call('plan-execution', self.execution_envelope(context, 'both',
            ['a.py', 'b.py'], ['a.py']), context)['ok'])
        reading = self.call('assign', {'task_id': 'b', 'agent_ref': 'b',
            'allocation_digest': planned['allocation_digest']}, planned['context'])
        self.assertTrue(reading['ok'], reading)
        self.assertFalse(self.call('plan-execution', self.execution_envelope(reading['context'],
            'rewrite-a', ['a.py']), reading['context'])['ok'])
        rewriting = self.call('plan-execution', self.execution_envelope(context,
            'rewrite-a', ['a.py']), context)
        self.assertTrue(rewriting['ok'], rewriting)
        active_writer = self.call('assign', {'task_id': 'rewrite-a', 'agent_ref': 'rewrite-a',
            'allocation_digest': rewriting['allocation_digest']}, rewriting['context'])
        self.assertTrue(active_writer['ok'], active_writer)
        self.assertFalse(self.call('plan-execution', second, active_writer['context'])['ok'])
        (self.repo / 'a.py').write_text('changed after acceptance\n')
        self.assertFalse(self.call('plan-execution', second, context)['ok'])

    def test_implementation_read_requires_acceptance_even_without_explicit_step_dependency(self):
        context = self.start_dispatcher(accepted_read_only=True, declared_dependency=False)
        envelope = self.execution_envelope(context, 'b', ['b.py'], ['a.py'])
        self.assertFalse(self.call('plan-execution', envelope, context)['ok'])

    def test_implementation_read_rechecks_bytes_before_native_binding(self):
        context = self.start_dispatcher(accepted_read_only=True)
        first = self.call('plan-execution', self.execution_envelope(context, 'a', ['a.py']), context)
        assigned = self.call('assign', {'task_id': 'a', 'agent_ref': 'a',
            'allocation_digest': first['allocation_digest']}, first['context'])
        (self.repo / 'a.py').write_text('accepted input\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), assigned['context'])
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        context = accepted['context']
        planned = self.call('plan-execution', self.execution_envelope(context, 'b', ['b.py'], ['a.py']), context)
        self.assertTrue(planned['ok'], planned)
        (self.repo / 'a.py').write_text('changed before bind\n')
        self.assertFalse(self.call('assign', {'task_id': 'b', 'agent_ref': 'b',
            'allocation_digest': planned['allocation_digest']}, planned['context'])['ok'])

    def test_recovery_snapshot_outweighs_historical_acceptance_until_replacement_accepts(self):
        progress = {'git_baseline_commit': 'a' * 40, 'allowed_paths': ['impl.py'],
            'executions': [
                {'state': 'accepted', 'agent_ref': 'old-accepted',
                 'git_result_snapshot': {'impl.py': 'first'}},
                {'state': 'cancelled', 'stopped': True, 'agent_ref': 'old-released',
                 'paths': ['impl.py']}],
            'recovery': {'state': 'activated',
                         'host_evidence': {'stopped_refs': ['dispatcher', 'old-accepted', 'old-released']},
                         'decision': {'assume_paths': []},
                         'releases': [{'agent_ref': 'old-released'}],
                         'snapshot': {'files': {'impl.py': 'assumed'}}}}
        with patch.object(control_git, 'committed_fingerprint', return_value=None), \
                patch.object(control_git, 'file_fingerprint', return_value='assumed'):
            control_git.verify_execution_start(Path('/tmp'), progress)
        progress['executions'].append({'state': 'accepted', 'agent_ref': 'new-accepted',
                                       'git_result_snapshot': {'impl.py': 'replacement'}})
        with patch.object(control_git, 'committed_fingerprint', return_value=None), \
                patch.object(control_git, 'file_fingerprint', return_value='replacement'):
            control_git.verify_execution_start(Path('/tmp'), progress)

    def result_evidence(self, name, paths):
        return {'agent_ref': name, 'stopped': True, 'changed_paths': paths,
            'file_hashes': {p: hashlib.sha256((self.repo/p).read_bytes()).hexdigest() for p in paths},
            'tests': ['CLI passed'], 'git_unchanged': True}

    def test_full_allocation_delta_rejects_unassigned_changes_at_receive_and_accept(self):
        context = self.start_allocations()
        (self.repo/'a.py').write_text('a'); (self.repo/'b.py').write_text('unassigned')
        for reported in (['a.py', 'b.py'], ['a.py']):
            with self.subTest(reported=reported):
                rejected = self.call('execution-result', self.result_evidence('a', reported), context)
                self.assertFalse(rejected['ok'], rejected)
        (self.repo/'b.py').unlink()
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        self.assertTrue(received['ok'], received)
        (self.repo/'b.py').write_text('changed after receipt')
        rejected = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertFalse(rejected['ok'], rejected)

    def test_target_advance_does_not_block_execution_intake_or_next_allocation(self):
        context = self.start_allocations()
        primary = self.root / 'repository'
        (primary / 'other-task.md').write_text('another task planning\n')
        subprocess.run(['git', '-C', str(primary), 'add', 'other-task.md'], check=True)
        subprocess.run(['git', '-C', str(primary), 'commit', '-qm', 'parallel task'], check=True)
        (self.repo / 'a.py').write_text('executor change\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        self.assertTrue(received['ok'], received)
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        helper = test_workflow_control.WorkflowControlTests()
        envelope = self.execution_envelope(accepted['context'], 'next', ['b.py'],
            behavior='next slice', tests=['focused'])
        self.assertTrue(self.call('plan-execution', envelope, accepted['context'])['ok'])
        self.assertFalse((self.repo / 'other-task.md').exists())
        (self.repo / 'outside.txt').write_text('actual unassigned work\n')
        self.assertFalse(self.call('plan-execution', envelope, accepted['context'])['ok'])

    def test_final_integration_conflict_is_revalidated_in_original_scope(self):
        context = self.public_start_allocations()
        (self.repo / 'a.py').write_text('task version\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        context = self.call('accept-execution', {'agent_ref':'a', 'file_hashes':{}}, received['context'])['context']
        context = self.accept_second_plan_step(context)
        self.git('add', 'a.py', 'b.py'); self.git('commit', '-qm', 'accepted implementation')
        accepted = self.git('rev-parse', 'HEAD')
        flow = self.coverage_flow
        delivered = flow.accept_current({'artifacts': ['a.py', 'b.py'], 'checks': ['CLI passed'],
            'candidate_commit': accepted, 'review': {axis: {'reviewer_ref': axis}
                for axis in ('standards', 'spec')}}, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(delivered['status'], 'accepted', delivered)
        original = copy.deepcopy(flow.state())
        primary = Path(flow.binding['repository'])
        (primary / 'a.py').write_text('parallel version\n')
        subprocess.run(['git', '-C', str(primary), 'add', 'a.py'], check=True)
        subprocess.run(['git', '-C', str(primary), 'commit', '-qm', 'parallel task'], check=True)
        target = self.git('rev-parse', context['handoff_progress']['binding']['target_branch'])
        flow.context = flow.context_for(4)
        closure_input = flow.input_for(4, original['accepted'])
        closure_input['scope']['protected_paths'] = sorted(set(
            closure_input['scope']['protected_paths'] + original['handoff']['scope']['protected_paths']))
        closure_input['authorization']['scope_digest'] = control.digest(closure_input['scope'])
        flow.begin('continuous', closure_input)
        flow.invoke('observe', flow.observation(ref='native:closure'))
        stopped = flow.observation('stopped', 'result', ref='native:closure')
        stopped['closure'] = {'ref': 'native:closure', 'candidate': accepted, 'merge': None,
            'ancestor_verified': False, 'changed_paths': [], 'binding': flow.binding, 'checks': [],
            'worktree_removed': False, 'branch_removed': False, 'disposition': 'deleted',
            'disposition_verified': False, 'implementation_problem': 'target advanced at final publication'}
        rejected = flow.invoke('observe', stopped)
        self.assertEqual(rejected['next_action']['operation'], 'implementation-recovery', rejected)
        recovery = {'reference': 'controller:original-integration', 'closure_digest': flow.state()['handoff']['digest']}
        flow.recover_final_integration(original, recovery)
        continued = flow.invoke('observe', flow.observation('idle', 'result', ref='native:dispatcher'))
        self.assertEqual(continued['next_action']['operation'], 'continue-host', continued)
        context = flow.state()['control']['context']
        merged = subprocess.run(['git', '-C', str(self.repo), 'merge', '--no-edit', target], capture_output=True)
        self.assertNotEqual(merged.returncode, 0)
        flow.invoke('observe', flow.observation('stopped', 'result', ref='native:dispatcher'))
        captured = self.call('capture-integration', {'reference':'controller:conflict'}, context)
        self.assertTrue(captured['ok'], captured)
        source = captured['context']['handoff_progress']['integration_source']
        helper = test_workflow_control.WorkflowControlTests()
        planned = self.call('plan-execution', self.execution_envelope(captured['context'],
            'resolve', ['a.py'], ['docs/draft.md'], behavior='resolve final conflict',
            tests=['resolved behavior'], adopt_paths=['a.py'],
            adoption_snapshot_digest=source['snapshot_digest']), captured['context'])
        self.assertTrue(planned['ok'], planned)
        assigned = self.call('assign', {'task_id':'resolve', 'agent_ref':'resolver',
            'allocation_digest':planned['allocation_digest']}, planned['context'])
        (self.repo / 'a.py').write_text('verified combined version\n')
        evidence = {**self.result_evidence('resolver', ['a.py']), 'adopted_paths':['a.py']}
        received = self.call('execution-result', evidence, assigned['context'])
        self.assertTrue(received['ok'], received)
        accepted_result = self.call('accept-execution', {'agent_ref':'resolver', 'file_hashes':{}}, received['context'])
        self.assertTrue(accepted_result['ok'], accepted_result)
        planned_b = self.call('plan-execution', self.execution_envelope(accepted_result['context'],
            'revalidate-b', ['b.py'], ['docs/draft.md'], behavior='revalidate retained b behavior',
            tests=['retained behavior'], adopt_paths=['b.py'],
            adoption_snapshot_digest=source['snapshot_digest']), accepted_result['context'])
        self.assertTrue(planned_b['ok'], planned_b)
        assigned_b = self.call('assign', {'task_id': 'revalidate-b', 'agent_ref': 'b-resolver',
            'allocation_digest': planned_b['allocation_digest']}, planned_b['context'])
        self.assertTrue(assigned_b['ok'], assigned_b)
        adopted_b = {**self.result_evidence('b-resolver', []),
            'adopted_paths': ['b.py'],
            'file_hashes': {'b.py': hashlib.sha256((self.repo / 'b.py').read_bytes()).hexdigest()}}
        received_b = self.call('execution-result', adopted_b, assigned_b['context'])
        self.assertTrue(received_b['ok'], received_b)
        accepted_b = self.call('accept-execution', {'agent_ref': 'b-resolver', 'file_hashes': {}},
                               received_b['context'])
        self.assertTrue(accepted_b['ok'], accepted_b)
        self.git('add', 'a.py'); self.git('commit', '-qm', 'verified final integration')
        candidate = self.git('rev-parse', 'HEAD')
        checkpoint = accepted_b['context']['handoff_progress']
        checks = test_workflow_control.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        ready = self.call('candidate-ready', {'dispatcher_ref':'dispatcher',
            'attempt':accepted_b['context']['carrier']['attempt'], 'commit':candidate,
            'expected_target_head':target, 'binding':checkpoint['binding'], 'plan_digest':checkpoint['plan_digest'],
            'plan_authority': checkpoint['plan_authority'],
            'checks':checks, 'source':{'adapter':'fixture', 'call_ref':'focused', 'response_ref':'focused-result',
                'raw':{'checks':checks, 'source_unchanged':True, 'stopped':True, 'dispatcher_ref':'dispatcher'}}}, accepted_b['context'])
        self.assertTrue(ready['ok'], ready)

    def test_parallel_results_wait_for_peer_evidence_before_acceptance(self):
        context = self.start_allocations(peers=True)
        (self.repo/'a.py').write_text('a'); (self.repo/'b.py').write_text('b')
        first = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['pending_peer_refs'], ['b'])
        unclaimed = self.call('execution-result', self.result_evidence('b', []), first['context'])
        self.assertFalse(unclaimed['ok'], unclaimed)
        early = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, first['context'])
        self.assertFalse(early['ok'], early)
        second = self.call('execution-result', self.result_evidence('b', ['b.py']), first['context'])
        self.assertTrue(second['ok'], second)
        accepted_b = self.call('accept-execution', {'agent_ref': 'b', 'file_hashes': {}}, second['context'])
        self.assertTrue(accepted_b['ok'], accepted_b)
        (self.repo/'b.py').write_text('changed after peer receipt')
        stale_peer = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, accepted_b['context'])
        self.assertFalse(stale_peer['ok'], stale_peer)
        (self.repo/'b.py').write_text('b')
        accepted_a = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, accepted_b['context'])
        self.assertTrue(accepted_a['ok'], accepted_a)
        helper = test_workflow_control.WorkflowControlTests()
        planned = self.call('plan-execution', self.execution_envelope(accepted_a['context'],
            'a2', ['a.py'], behavior='serial follow-up'), accepted_a['context'])
        assigned = self.call('assign', {'task_id': 'a2', 'agent_ref': 'a2', 'allocation_digest': planned['allocation_digest']}, planned['context'])
        (self.repo/'a.py').write_text('serial improvement')
        received = self.call('execution-result', self.result_evidence('a2', ['a.py']), assigned['context'])
        self.assertTrue(received['ok'], received)
        final = self.call('accept-execution', {'agent_ref': 'a2', 'file_hashes': {}}, received['context'])
        self.assertTrue(final['ok'], final)
        _, ownership, revalidate = control_git.recovery_snapshot(self.repo, final['context']['handoff_progress'])
        self.assertEqual(ownership['accepted'], ['a.py', 'b.py'])
        self.assertEqual(revalidate, [])

    def test_candidate_rejects_mode_changed_after_execution_acceptance(self):
        context = self.public_start_allocations()
        (self.repo/'a.py').write_text('agent bytes\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        self.assertTrue(received['ok'], received)
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        current = self.accept_second_plan_step(accepted['context'])
        self.git('add', 'a.py', 'b.py'); self.git('commit', '-qm', 'accepted implementation')
        progress = current['handoff_progress']
        plan = progress['validation_plan']
        target = self.git('rev-parse', progress['binding']['target_branch'])
        def evidence(candidate):
            checks = test_workflow_control.checks_for(plan['review_required'], candidate)
            return {'dispatcher_ref': 'dispatcher', 'attempt': accepted['context']['carrier']['attempt'],
                    'commit': candidate, 'expected_target_head': target, 'binding': progress['binding'],
                    'plan_digest': control.digest(plan), 'plan_authority': progress['plan_authority'],
                    'checks': checks,
                    'source': {'adapter': 'fixture', 'call_ref': 'focused', 'response_ref': 'focused-result',
                               'raw': {'checks': checks, 'source_unchanged': True,
                                       'stopped': True, 'dispatcher_ref': 'dispatcher'}}}
        ready = self.call('candidate-ready', evidence(self.git('rev-parse', 'HEAD')), current)
        self.assertTrue(ready['ok'], ready)
        target_file = self.repo/'a.py'
        target_file.chmod((target_file.stat().st_mode & 0o777) | 0o111)
        self.git('add', 'a.py'); self.git('commit', '-qm', 'dispatcher mode change')
        self.assertFalse(self.call('candidate-ready', evidence(self.git('rev-parse', 'HEAD')),
                                   current)['ok'])

    def candidate_reader_after_input_change(self, input_change):
        context = self.start_public_coverage_flow(accepted_read_only=True)
        def accept(current, task, relative, body, read_only=(), executable=False):
            planned = self.call('plan-execution', self.execution_envelope(
                current, task, [relative], read_only), current)
            self.assertTrue(planned['ok'], planned)
            assigned = self.call('assign', {'task_id': task, 'agent_ref': task,
                'allocation_digest': planned['allocation_digest']}, planned['context'])
            self.assertTrue(assigned['ok'], assigned)
            target = self.repo / relative
            if body is None:
                target.unlink()
            else:
                target.write_text(body)
                if executable:
                    target.chmod(0o755)
            result = {'agent_ref': task, 'stopped': True, 'changed_paths': [relative],
                'file_hashes': {relative: None if body is None else
                    hashlib.sha256(target.read_bytes()).hexdigest()},
                'tests': ['CLI passed'], 'git_unchanged': True}
            received = self.call('execution-result', result,
                                 assigned['context'])
            self.assertTrue(received['ok'], received)
            accepted = self.call('accept-execution', {'agent_ref': task, 'file_hashes': {}},
                                 received['context'])
            self.assertTrue(accepted['ok'], accepted)
            return accepted['context']

        context = accept(context, 'first-b', 'a.py', 'first accepted input\n')
        context = accept(context, 'old-a', 'b.py', 'reader using first input\n', ['a.py'])
        replacement = {'content': 'second accepted input\n',
                       'mode': 'first accepted input\n', 'absence': None}[input_change]
        context = accept(context, 'new-b', 'a.py', replacement,
                         executable=input_change == 'mode')
        self.git('add', '-A')
        self.git('commit', '-qm', 'accepted implementation')
        progress = context['handoff_progress']
        target = self.git('rev-parse', progress['binding']['target_branch'])
        def candidate_evidence():
            candidate = self.git('rev-parse', 'HEAD')
            checks = test_workflow_control.checks_for(
                progress['validation_plan']['review_required'], candidate)
            return {'dispatcher_ref': 'dispatcher', 'attempt': context['carrier']['attempt'],
                'commit': candidate, 'expected_target_head': target, 'binding': progress['binding'],
                'plan_digest': control.digest(progress['validation_plan']),
                'plan_authority': progress['plan_authority'], 'checks': checks,
                'source': {'adapter': 'fixture', 'call_ref': 'focused',
                    'response_ref': 'focused-result', 'raw': {'checks': checks,
                        'source_unchanged': True, 'stopped': True, 'dispatcher_ref': 'dispatcher'}}}
        self.assertFalse(self.call('candidate-ready', candidate_evidence(), context)['ok'])
        context = accept(context, 'new-a', 'b.py', 'reader revalidated with second input\n', ['a.py'])
        self.git('add', 'b.py')
        self.git('commit', '-qm', 'fresh accepted reader')
        progress = context['handoff_progress']
        self.assertTrue(self.call('candidate-ready', candidate_evidence(), context)['ok'])

    def test_candidate_requires_fresh_reader_after_accepted_input_changes(self):
        self.candidate_reader_after_input_change('content')

    def test_candidate_requires_fresh_reader_after_accepted_input_mode_changes(self):
        self.candidate_reader_after_input_change('mode')

    def test_candidate_requires_fresh_reader_after_accepted_input_disappears(self):
        self.candidate_reader_after_input_change('absence')

    def test_candidate_rejects_reverted_out_of_scope_history(self):
        context = self.public_start_allocations()
        (self.repo/'a.py').write_text('accepted bytes\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        accepted['context'] = self.accept_second_plan_step(accepted['context'])
        self.git('add', 'a.py', 'b.py'); self.git('commit', '-qm', 'accepted implementation')
        self.coverage_flow.mark_reviewable(self.git('rev-parse', 'HEAD'), fixture_execution=False)
        (self.repo/'outside.py').write_text('private bytes\n')
        self.git('add', 'outside.py'); self.git('commit', '-qm', 'outside scope')
        self.git('rm', 'outside.py'); self.git('commit', '-qm', 'hide outside change')
        progress = accepted['context']['handoff_progress']
        candidate = self.git('rev-parse', 'HEAD')
        target = self.git('rev-parse', progress['binding']['target_branch'])
        checks = test_workflow_control.checks_for(progress['validation_plan']['review_required'], candidate)
        evidence = {'dispatcher_ref': 'dispatcher', 'attempt': accepted['context']['carrier']['attempt'],
                    'commit': candidate, 'expected_target_head': target, 'binding': progress['binding'],
                    'plan_digest': control.digest(progress['validation_plan']),
                    'plan_authority': progress['plan_authority'], 'checks': checks,
                    'source': {'adapter': 'fixture', 'call_ref': 'focused', 'response_ref': 'focused-result',
                               'raw': {'checks': checks, 'source_unchanged': True,
                                       'stopped': True, 'dispatcher_ref': 'dispatcher'}}}
        self.assertFalse(self.call('candidate-ready', evidence, accepted['context'])['ok'])

    def test_full_delta_includes_deletion_and_mode_changes(self):
        existing = 'def run_1():\n    return "existing"\n'
        (self.repo/'b.py').write_text(existing)
        self.git('add', '.'); self.git('commit', '-qm', 'existing allowed file')
        self.base = self.git('rev-parse', 'HEAD')
        context = self.start_allocations()
        (self.repo/'a.py').write_text('a')
        original_mode = (self.repo/'b.py').stat().st_mode & 0o777
        (self.repo/'b.py').chmod(original_mode ^ 0o100)
        self.assertFalse(self.call('execution-result', self.result_evidence('a', ['a.py']), context)['ok'])
        (self.repo/'b.py').chmod(original_mode)
        (self.repo/'b.py').unlink()
        self.assertFalse(self.call('execution-result', self.result_evidence('a', ['a.py']), context)['ok'])
        (self.repo/'b.py').write_text(existing); (self.repo/'b.py').chmod(original_mode)
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        self.assertTrue(received['ok'], received)
        (self.repo/'a.py').chmod((self.repo/'a.py').stat().st_mode ^ 0o100)
        self.assertFalse(self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])['ok'])

    def test_closure_verifies_supervision_publication_and_actual_cleanup(self, cleanup_failure=False):
        import test_workflow_progress as progress_fixture
        helper = test_workflow_control.WorkflowControlTests()
        context = self.start_public_coverage_flow(('code.py',))
        flow = self.coverage_flow
        primary = Path(flow.binding['repository'])
        planned = self.call('plan-execution', self.execution_envelope(context,
            'closure-code', ['code.py']), context)
        self.assertTrue(planned['ok'], planned)
        bound_code = self.call('assign', {'task_id': 'closure-code', 'agent_ref': 'executor',
            'allocation_digest': planned['allocation_digest']}, planned['context'])
        self.assertTrue(bound_code['ok'], bound_code)
        (self.repo / 'code.py').write_text('print(1)\n')
        received_code = self.call('execution-result', self.result_evidence('executor', ['code.py']),
            bound_code['context'])
        self.assertTrue(received_code['ok'], received_code)
        accepted_code = self.call('accept-execution', {'agent_ref': 'executor', 'file_hashes': {}},
            received_code['context'])
        self.assertTrue(accepted_code['ok'], accepted_code)
        self.git('add', 'code.py'); self.git('commit', '-qm', 'implementation')
        candidate = self.git('rev-parse', 'HEAD')
        acceptance = flow.accept_current({'artifacts': ['code.py'], 'checks': ['CLI passed'],
            'candidate_commit': candidate, 'review': {axis: {'reviewer_ref': axis}
                for axis in ('standards', 'spec')}}, 'native:dispatcher', fixture_execution=False)
        self.assertEqual(acceptance['status'], 'accepted', acceptance)
        accepted = flow.state()['accepted']
        stage4_input = flow.input_for(4, accepted)
        stage4_input['predecessor'] = progress_fixture.progress.implementation_predecessor(flow.checkpoint)
        inherited = flow.state()['handoff']['scope']
        stage4_input['scope']['protected_paths'] = sorted(set(
            stage4_input['scope']['protected_paths'] + inherited['protected_paths']))
        stage4_input['authorization']['scope_digest'] = control.digest(stage4_input['scope'])
        stage4 = progress_fixture.transfer.handoff.handle(stage4_input)
        reference = progress_fixture.transfer.handoff.unseal(stage4)['predecessor']
        verification = progress_fixture.progress.closure_delivery_view(reference)
        binding = flow.binding
        ctx = flow.context_for(4)
        evidence = {'candidate': candidate, 'dispatcher_ref': accepted['role_ref'],
            'review': copy.deepcopy(accepted['payload']['review']), 'verification': verification,
            'binding': binding, 'implementation_paths': ['code.py'], 'closure_paths': ['README.md'],
            'protected_paths': verification['view']['protected_paths'],
            'configuration': helper.configuration('closure-agent')}
        # C's accepted implementation stays immutable. Stage-4 CLI validates
        # its real predecessor/source refs; no CLI result is copied back into C.
        started = self.git_call('start-closure', evidence, ctx)
        self.assertTrue(started['ok'], started)
        bound = self.git_call('closure-bound', {'ref': 'closure', 'attempt': started['attempt']}, started['context'])
        result = {'ref': 'closure', 'candidate': candidate, 'merge': candidate, 'ancestor_verified': True,
            'changed_paths': ['code.py'], 'binding': binding, 'checks': ['full suite'],
            'worktree_removed': True, 'branch_removed': True, 'disposition': 'deleted', 'disposition_verified': True, 'implementation_problem': None}
        premature = self.git_call('closure-result', result, bound['context'])
        self.assertFalse(premature['ok'], premature)
        (self.repo / 'README.md').write_text('Document the behavior\n')
        self.git('add', '.'); self.git('commit', '-qm', 'closure documentation')
        import os
        import shutil
        environment = dict(os.environ)
        if cleanup_failure:
            binary_dir = self.root / 'bin'; binary_dir.mkdir()
            real_git = shutil.which('git')
            wrapper = binary_dir / 'git'
            wrapper.write_text('#!/usr/bin/env python3\nimport os, sys\n' +
                'if "worktree" in sys.argv and "remove" in sys.argv: sys.exit(1)\n' +
                'os.execv(' + repr(real_git) + ', [' + repr(real_git) + '] + sys.argv[1:])\n')
            wrapper.chmod(0o755)
            environment['PATH'] = str(binary_dir) + os.pathsep + environment['PATH']
        complete = subprocess.run([sys.executable, str(CLI.with_name('supervision_protocol.py')), 'complete-worktree'],
            input=json.dumps({'binding': binding, 'candidate_commit': self.git('rev-parse', 'HEAD'),
                'expected_target_head': self.base, 'scope_base_commit': self.base,
                'allowed_paths': ['README.md', 'code.py'], 'protected_paths': ['docs/draft.md']}),
            text=True, capture_output=True, env=environment)
        published = json.loads(complete.stdout)
        self.assertEqual(complete.returncode, 2 if cleanup_failure else 0, published)
        self.repo = primary
        result['merge'] = published['error']['context']['merge_commit'] if cleanup_failure else published['merge_commit']
        verified = self.git_call('closure-result', result, bound['context'])
        self.assertTrue(verified['ok'], verified)
        if cleanup_failure:
            self.assertEqual(verified['context']['handoff_progress']['state'], 'cleanup-pending')
            self.assertEqual(verified['effects'][0]['operation'], 'cleanup-only')
            self.git('worktree', 'remove', binding['worktree'])
            self.git('branch', '-d', binding['branch'])
            verified = self.git_call('closure-result', result, verified['context'])
        self.assertEqual(verified['context']['handoff_progress']['state'], 'completed')

    def test_cleanup_failure_reconciles_git_without_republishing(self):
        self.test_closure_verifies_supervision_publication_and_actual_cleanup(cleanup_failure=True)
