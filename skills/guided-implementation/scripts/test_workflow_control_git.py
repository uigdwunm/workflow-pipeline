"""Control decisions consume actual Git evidence through the production adapter."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_control

CLI = Path(__file__).with_name('workflow_control_git.py')

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
        result = subprocess.run([sys.executable, str(CLI)], text=True, capture_output=True,
            input=json.dumps({'repository': str(self.repo), 'baseline': self.base,
                'request': {'schema_version': 1, 'action': action, 'actor_ref': 'controller', 'context': context, 'evidence': evidence}}))
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

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
            'confirmed': True, 'archive_ref': 'old'}, accepted['context'])
        archived = self.call('archive', {}, ready['context'])
        self.assertEqual(archived['effects'], [{'operation': 'archive', 'ref': 'old'}])
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
        started = self.call('start-dispatch', {'binding': binding, 'binding_verified': True,
            'allowed_paths': ['src/a.py'], 'protected_paths': ['docs/draft.md'], 'authority_digest': 'c' * 64,
            'testing_basis': 'CLI', 'configuration': helper.configuration('implementation-dispatcher')}, ctx)
        self.assertTrue(started['ok'], started)
        bound = self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']}, started['context'])
        envelope = {'task_id': 'a', 'paths': ['src/a.py'], 'read_only': ['docs/draft.md'],
            'behavior': 'a', 'tests': ['CLI'], 'git_operations': [], 'configuration': helper.configuration('execution-agent')}
        planned = self.call('plan-execution', envelope, bound['context'])
        # Only the external native adapter is substituted. Control allocation,
        # Git snapshots and result intake remain real subprocess boundaries.
        adapter = subprocess.run([sys.executable, '-c',
            'import json, pathlib, sys, uuid; request=json.load(sys.stdin); '
            'path=pathlib.Path(request["cwd"])/request["effect"]["envelope"]["paths"][0]; '
            'path.parent.mkdir(); path.write_text("print(1)\\n"); '
            'print(json.dumps({"ref": str(uuid.uuid4()), "trace": [request["effect"]], "stopped": True}))'],
            input=json.dumps({'cwd': str(self.repo), 'effect': planned['effects'][0]}), text=True, capture_output=True, check=True)
        receipt = json.loads(adapter.stdout)
        self.assertEqual(receipt['trace'][0]['role'], 'execution-agent')
        self.assertEqual(receipt['trace'][0]['envelope']['configuration']['model'], 'supported')
        assigned = self.call('assign', {'task_id': 'a', 'agent_ref': receipt['ref'], 'allocation_digest': planned['allocation_digest']}, planned['context'])
        result = {'agent_ref': receipt['ref'], 'stopped': receipt['stopped'], 'changed_paths': ['src/a.py'],
                  'file_hashes': {'src/a.py': hashlib.sha256((self.repo/'src/a.py').read_bytes()).hexdigest()},
                  'tests': ['CLI passed'], 'git_unchanged': True}
        received = self.call('execution-result', result, assigned['context'])
        self.assertTrue(received['ok'], received)
        execution = received['context']['handoff_progress']['executions'][0]
        self.assertEqual(list(execution['file_hashes']), ['src/a.py'])
        self.git('add', '.'); self.git('commit', '-qm', 'unauthorized executor commit')
        self.assertFalse(self.call('execution-result', result, assigned['context'])['ok'])

    def start_allocations(self, peers=False):
        helper = test_workflow_control.WorkflowControlTests()
        target = self.git('branch', '--show-current')
        receipt = subprocess.run([sys.executable, str(CLI.with_name('supervision_protocol.py')), 'start-worktree'],
            input=json.dumps({'repository': str(self.repo), 'worktree': str(self.root / 'flow'), 'branch': 'codex/flow', 'target_branch': target}),
            text=True, capture_output=True, check=True)
        binding = json.loads(receipt.stdout)['binding']; self.repo = self.root / 'flow'
        context = helper.context(); context['stage'] = 3
        started = self.call('start-dispatch', {'binding': binding, 'binding_verified': True,
            'allowed_paths': ['a.py', 'b.py'], 'protected_paths': ['docs/draft.md'], 'authority_digest': 'c' * 64,
            'testing_basis': 'CLI', 'configuration': helper.configuration('implementation-dispatcher')}, context)
        context = self.call('dispatcher-bound', {'ref': 'dispatcher', 'attempt': started['attempt']}, started['context'])['context']
        for name in ('a', 'b') if peers else ('a',):
            planned = self.call('plan-execution', {'task_id': name, 'paths': [name+'.py'], 'read_only': [],
                'behavior': name, 'tests': ['CLI'], 'git_operations': [], 'configuration': helper.configuration('execution-agent')}, context)
            context = self.call('assign', {'task_id': name, 'agent_ref': name, 'allocation_digest': planned['allocation_digest']}, planned['context'])['context']
        return context

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
        planned = self.call('plan-execution', {'task_id': 'a2', 'paths': ['a.py'], 'read_only': [],
            'behavior': 'serial follow-up', 'tests': ['CLI'], 'git_operations': [],
            'configuration': helper.configuration('execution-agent')}, accepted_a['context'])
        assigned = self.call('assign', {'task_id': 'a2', 'agent_ref': 'a2', 'allocation_digest': planned['allocation_digest']}, planned['context'])
        (self.repo/'a.py').write_text('serial improvement')
        received = self.call('execution-result', self.result_evidence('a2', ['a.py']), assigned['context'])
        self.assertTrue(received['ok'], received)
        self.assertTrue(self.call('accept-execution', {'agent_ref': 'a2', 'file_hashes': {}}, received['context'])['ok'])

    def test_full_delta_includes_deletion_and_mode_changes(self):
        (self.repo/'b.py').write_text('existing')
        self.git('add', '.'); self.git('commit', '-qm', 'existing allowed file')
        context = self.start_allocations()
        (self.repo/'a.py').write_text('a')
        original_mode = (self.repo/'b.py').stat().st_mode & 0o777
        (self.repo/'b.py').chmod(original_mode ^ 0o100)
        self.assertFalse(self.call('execution-result', self.result_evidence('a', ['a.py']), context)['ok'])
        (self.repo/'b.py').chmod(original_mode)
        (self.repo/'b.py').unlink()
        self.assertFalse(self.call('execution-result', self.result_evidence('a', ['a.py']), context)['ok'])
        (self.repo/'b.py').write_text('existing'); (self.repo/'b.py').chmod(original_mode)
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
        started = self.call('start-closure', evidence, ctx)
        self.assertTrue(started['ok'], started)
        bound = self.call('closure-bound', {'ref': 'closure', 'attempt': started['attempt']}, started['context'])
        result = {'ref': 'closure', 'candidate': candidate, 'merge': candidate, 'ancestor_verified': True,
            'changed_paths': ['code.py'], 'binding': binding, 'checks': ['full suite'],
            'worktree_removed': True, 'branch_removed': True, 'implementation_problem': None}
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
