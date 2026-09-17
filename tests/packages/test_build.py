"""Release seam: build, isolate, and execute the installed JSON CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]


class PackageBuildTests(unittest.TestCase):
    def test_deterministic_release_rejects_drift_and_undeclared_import(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            fixture = root / 'source'
            for directory in ('src', 'build', 'scripts'):
                shutil.copytree(ROOT / directory, fixture / directory)
            command = [sys.executable, str(fixture / 'scripts/build_skills.py')]
            one, two = root / 'one', root / 'two'
            for output in (one, two):
                result = subprocess.run(command + ['--output', str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            def snapshot(directory):
                return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mode & 0o777)
                    for p in directory.rglob('*') if p.is_file()}
            self.assertEqual(snapshot(one), snapshot(two))
            target = one / 'design-discussion/scripts/discussion_protocol.py'
            target.write_text(target.read_text() + '\n# manual drift\n')
            check = subprocess.run(command + ['--output',str(one),'--check'],capture_output=True,text=True)
            self.assertNotEqual(check.returncode, 0)
            source = fixture / 'src/shared/scripts/discussion_protocol.py'
            source.write_text(source.read_text() + '\nimport undeclared_runtime_dependency\n')
            broken = subprocess.run(command + ['--output',str(two)],capture_output=True,text=True)
            self.assertNotEqual(broken.returncode, 0)
            self.assertIn('undeclared_runtime_dependency', broken.stderr)

    def test_isolated_discussion_bootstrap_and_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / 'installed'
            built = subprocess.run([sys.executable, str(ROOT / 'scripts/build_skills.py'),
                '--output', str(output), '--package', 'design-discussion'], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            project = root / '项目 with spaces'
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            environment = dict(os.environ)
            environment.pop('PYTHONPATH', None)
            def call(request):
                result = subprocess.run([sys.executable, '-I', str(output / 'design-discussion/scripts/discussion_protocol.py')],
                    input=json.dumps(request), capture_output=True, text=True, cwd=project, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return json.loads(result.stdout)
            topic = call({'protocol_version': 1, 'operation': 'bootstrap', 'project_path': str(project),
                'entry_mode': 'explicit-skill', 'conversation_ref': 'isolated-discussion',
                'idempotency_key': '291db9a6-9442-4c10-a254-96c3a5bac5f9', 'root_slug': 'isolated'})
            self.assertTrue(topic['topic_id'])
            read = call({'protocol_version': 1, 'operation': 'read-topic', 'project_path': str(project),
                'project_id': topic['project_id'], 'tree_id': topic['tree_id'], 'actor_topic_id': topic['topic_id'],
                'actor_conversation_ref': 'isolated-discussion'})
            self.assertTrue(read['ok'])
            envelope = {'protocol_version': 1, 'project_path': str(project),
                'project_id': topic['project_id'], 'tree_id': topic['tree_id'], 'actor_topic_id': topic['topic_id'],
                'actor_conversation_ref': 'isolated-discussion'}
            prepared = call(dict(envelope, operation='prepare-topic-update',
                expected_ledger_revision=1, expected_topic_revision=1, idempotency_key=str(uuid.uuid4()),
                mutation={'type': 'confirm-decision', 'summary': 'Only this package is installed.',
                    'rationale': 'The release contains its runtime closure.'}))
            applied = call(dict(envelope, operation='apply-document-write',
                expected_ledger_revision=2, expected_topic_revision=2, idempotency_key=str(uuid.uuid4()),
                document_write_id=prepared['document_write_id']))
            self.assertTrue(applied['document_verified'])
            self.assertIn('Only this package is installed.', Path(topic['topic_document_path']).read_text())
            subprocess.run(['git', '-C', str(project), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(project), '-c', 'user.name=Test', '-c',
                'user.email=test@example.com', 'commit', '-qm', 'Initial'], check=True)
            branch = subprocess.check_output(['git','-C',str(project),'branch','--show-current'], text=True).strip()
            def supervise(operation, request):
                result = subprocess.run([sys.executable, '-I', str(output / 'design-discussion/scripts/supervision_protocol.py'), operation],
                    input=json.dumps(request), capture_output=True, text=True, cwd=project, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return json.loads(result.stdout)
            started = supervise('start-worktree', {'repository':str(project), 'target_branch':branch,
                'branch':'codex/isolated', 'worktree':str(root / 'flow')})
            verified = supervise('verify-worktree', {'binding':started['binding'], 'platform_cwd':str(root / 'flow')})
            self.assertTrue(verified['verified'])


if __name__ == '__main__':
    unittest.main()
