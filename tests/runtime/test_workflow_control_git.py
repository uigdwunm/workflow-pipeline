"""Control decisions consume actual Git evidence through the production adapter."""
import hashlib
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
        result = subprocess.run([sys.executable, str(CLI)], text=True, capture_output=True,
            input=json.dumps({'repository': str(self.repo), 'baseline': self.base,
                'request': {'schema_version': 5, 'action': action, 'actor_ref': 'controller', 'context': context, 'evidence': evidence}}))
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def plan_evidence(self, context, paths):
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
        plan = {'schema_version': 1, 'ticket_id': 'INLINE-GIT', 'plan_version': 1,
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
        semantic = {'objective': 'implement the frozen requirement', 'testing_basis': 'CLI',
                    'completion_criteria': ['CLI'], 'constraints': ['stay in scope']}
        authority = execution_plan.inline_authority(plan, scope, semantic,
            context['requirement_identity'], repository=str(self.repo))
        catalog = authority['execution_plan']
        return {'execution_authority': authority,
                'plan_authority': {'revision': authority['revision'],
                    'authority_digest': control.digest(authority),
                    'plan_digest': catalog['plan_digest'],
                    'contract_digest': catalog['contract_digest']}}

    def execution_envelope(self, context, task_id, paths, read_only=(), **extra):
        progress = context['handoff_progress']
        catalog = progress['execution_authority']['execution_plan']
        ticket = catalog['tickets'][0]
        steps = {step['id']: step for step in ticket['plan']['steps']}
        refs = [{'ticket_id': ticket['ticket_id'], 'step_id': step_id,
                 'plan_version': ticket['plan_version'], 'source': ticket['source'],
                 'step_digest': control.digest(steps[step_id])}
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
        with self.assertRaisesRegex(control.ControlError, 'index entries changed'):
            control_git.verify_plan_revision(self.repo, stopped, candidate)

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

    def start_dispatcher(self):
        helper = test_workflow_control.WorkflowControlTests()
        target = self.git('branch', '--show-current')
        receipt = subprocess.run([sys.executable, str(CLI.with_name('supervision_protocol.py')), 'start-worktree'],
            input=json.dumps({'repository': str(self.repo), 'worktree': str(self.root / 'flow'), 'branch': 'codex/flow', 'target_branch': target}),
            text=True, capture_output=True, check=True)
        binding = json.loads(receipt.stdout)['binding']; self.repo = self.root / 'flow'
        context = helper.context(); context['stage'] = 3
        plan = self.plan_evidence(context, ['a.py', 'b.py'])
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
        context = self.start_allocations()
        (self.repo / 'a.py').write_text('task version\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        context = self.call('accept-execution', {'agent_ref':'a', 'file_hashes':{}}, received['context'])['context']
        self.git('add', 'a.py'); self.git('commit', '-qm', 'accepted implementation')
        accepted = self.git('rev-parse', 'HEAD')
        primary = self.root / 'repository'
        (primary / 'a.py').write_text('parallel version\n')
        subprocess.run(['git', '-C', str(primary), 'add', 'a.py'], check=True)
        subprocess.run(['git', '-C', str(primary), 'commit', '-qm', 'parallel task'], check=True)
        target = self.git('rev-parse', context['handoff_progress']['binding']['target_branch'])
        context['handoff_progress'].update(integration_candidate=accepted, integration_target_head=target)
        merged = subprocess.run(['git', '-C', str(self.repo), 'merge', '--no-edit', target], capture_output=True)
        self.assertNotEqual(merged.returncode, 0)
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
        self.git('add', 'a.py'); self.git('commit', '-qm', 'verified final integration')
        candidate = self.git('rev-parse', 'HEAD')
        checkpoint = accepted_result['context']['handoff_progress']
        checks = test_workflow_control.checks_for(checkpoint['validation_plan']['review_required'], candidate)
        ready = self.call('candidate-ready', {'dispatcher_ref':'dispatcher',
            'attempt':accepted_result['context']['carrier']['attempt'], 'commit':candidate,
            'expected_target_head':target, 'binding':checkpoint['binding'], 'plan_digest':checkpoint['plan_digest'],
            'checks':checks, 'source':{'adapter':'fixture', 'call_ref':'focused', 'response_ref':'focused-result',
                'raw':{'checks':checks, 'source_unchanged':True, 'stopped':True, 'dispatcher_ref':'dispatcher'}}}, accepted_result['context'])
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
        context = self.start_allocations()
        (self.repo/'a.py').write_text('agent bytes\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        self.git('add', 'a.py'); self.git('commit', '-qm', 'accepted implementation')
        progress = accepted['context']['handoff_progress']
        plan = progress['validation_plan']
        target = self.git('rev-parse', progress['binding']['target_branch'])
        def evidence(candidate):
            checks = test_workflow_control.checks_for(plan['review_required'], candidate)
            return {'dispatcher_ref': 'dispatcher', 'attempt': accepted['context']['carrier']['attempt'],
                    'commit': candidate, 'expected_target_head': target, 'binding': progress['binding'],
                    'plan_digest': control.digest(plan), 'checks': checks,
                    'source': {'adapter': 'fixture', 'call_ref': 'focused', 'response_ref': 'focused-result',
                               'raw': {'checks': checks, 'source_unchanged': True,
                                       'stopped': True, 'dispatcher_ref': 'dispatcher'}}}
        self.assertTrue(self.call('candidate-ready', evidence(self.git('rev-parse', 'HEAD')),
                                  accepted['context'])['ok'])
        target_file = self.repo/'a.py'
        target_file.chmod((target_file.stat().st_mode & 0o777) | 0o111)
        self.git('add', 'a.py'); self.git('commit', '-qm', 'dispatcher mode change')
        self.assertFalse(self.call('candidate-ready', evidence(self.git('rev-parse', 'HEAD')),
                                   accepted['context'])['ok'])

    def test_candidate_rejects_reverted_out_of_scope_history(self):
        context = self.start_allocations()
        (self.repo/'a.py').write_text('accepted bytes\n')
        received = self.call('execution-result', self.result_evidence('a', ['a.py']), context)
        accepted = self.call('accept-execution', {'agent_ref': 'a', 'file_hashes': {}}, received['context'])
        self.assertTrue(accepted['ok'], accepted)
        self.git('add', 'a.py'); self.git('commit', '-qm', 'accepted implementation')
        (self.repo/'outside.py').write_text('private bytes\n')
        self.git('add', 'outside.py'); self.git('commit', '-qm', 'outside scope')
        self.git('rm', 'outside.py'); self.git('commit', '-qm', 'hide outside change')
        progress = accepted['context']['handoff_progress']
        candidate = self.git('rev-parse', 'HEAD')
        target = self.git('rev-parse', progress['binding']['target_branch'])
        checks = test_workflow_control.checks_for(progress['validation_plan']['review_required'], candidate)
        evidence = {'dispatcher_ref': 'dispatcher', 'attempt': accepted['context']['carrier']['attempt'],
                    'commit': candidate, 'expected_target_head': target, 'binding': progress['binding'],
                    'plan_digest': control.digest(progress['validation_plan']), 'checks': checks,
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
        helper = test_workflow_control.WorkflowControlTests()
        primary = self.repo
        target = self.git('branch', '--show-current')
        start = subprocess.run([sys.executable, str(CLI.with_name('supervision_protocol.py')), 'start-worktree'],
            input=json.dumps({'repository': str(primary), 'worktree': str(self.root / 'flow'), 'branch': 'codex/flow', 'target_branch': target}),
            text=True, capture_output=True, check=True)
        binding = json.loads(start.stdout)['binding']
        self.repo = self.root / 'flow'
        (self.repo / 'code.py').write_text('print(1)\n')
        self.git('add', '.'); self.git('commit', '-qm', 'implementation')
        candidate = self.git('rev-parse', 'HEAD')
        ctx = helper.context(); ctx['stage'] = 4
        evidence = {'candidate': candidate, 'dispatcher_ref': 'dispatcher',
            'review': {axis: {'candidate': candidate, 'reviewer_ref': axis, 'status': 'accepted'} for axis in ('standards', 'spec')},
            'verification': {'candidate': candidate, 'checks': ['full suite']}, 'binding': binding,
            'implementation_paths': ['code.py'], 'closure_paths': ['README.md'], 'protected_paths': ['docs/draft.md'],
            'configuration': helper.configuration('closure-agent')}
        evidence['review'], evidence['verification'] = __import__('test_workflow_control').verification_fixture(candidate, 'dispatcher', binding['worktree'], self.git('rev-parse', 'main'))
        started = self.call('start-closure', evidence, ctx)
        self.assertTrue(started['ok'], started)
        bound = self.call('closure-bound', {'ref': 'closure', 'attempt': started['attempt']}, started['context'])
        result = {'ref': 'closure', 'candidate': candidate, 'merge': candidate, 'ancestor_verified': True,
            'changed_paths': ['code.py'], 'binding': binding, 'checks': ['full suite'],
            'worktree_removed': True, 'branch_removed': True, 'disposition': 'deleted', 'disposition_verified': True, 'implementation_problem': None}
        premature = self.call('closure-result', result, bound['context'])
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
        verified = self.call('closure-result', result, bound['context'])
        self.assertTrue(verified['ok'], verified)
        if cleanup_failure:
            self.assertEqual(verified['context']['handoff_progress']['state'], 'cleanup-pending')
            self.assertEqual(verified['effects'][0]['operation'], 'cleanup-only')
            self.git('worktree', 'remove', binding['worktree'])
            self.git('branch', '-d', binding['branch'])
            verified = self.call('closure-result', result, verified['context'])
        self.assertEqual(verified['context']['handoff_progress']['state'], 'completed')

    def test_cleanup_failure_reconciles_git_without_republishing(self):
        self.test_closure_verifies_supervision_publication_and_actual_cleanup(cleanup_failure=True)
