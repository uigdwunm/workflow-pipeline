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
    def test_release_rejects_prior_transfer_and_progress(self):
        config = json.loads((ROOT / 'build/skill-packages.json').read_text())
        key = config['compatibility_key']
        self.assertEqual((key['control'], key['stage_transfer'], key['workflow_progress']),
                         (6, 'workflow-stage-transfer-v7', 'workflow-progress-v11'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'packages'
            subprocess.run([sys.executable, str(ROOT / 'scripts/build_skills.py'), '--output', str(output)],
                           capture_output=True, check=True)
            for name in ('solution-design', 'guided-implementation', 'change-closure'):
                package = json.loads((output / name / 'package.json').read_text())
                self.assertEqual(package['compatibility_key'], key)
            scripts = output / 'guided-implementation/scripts'
            requests = [('stage_handoff.py', [], {'protocol': 'workflow-stage-transfer-v5', 'operation': 'prepare'}),
                        ('stage_handoff.py', [], {'protocol': 'workflow-stage-transfer-v6', 'operation': 'prepare'}),
                        ('workflow_progress.py', [str(root / 'old-checkpoint.json')],
                         {'protocol': 'workflow-progress-v9', 'operation': 'inspect', 'expected_revision': 0, 'data': {}}),
                        ('workflow_progress.py', [str(root / 'old-checkpoint.json')],
                         {'protocol': 'workflow-progress-v10', 'operation': 'inspect', 'expected_revision': 0, 'data': {}}),
                        ('workflow_progress.py', [str(root / 'old-checkpoint.json')],
                         {'protocol': 'workflow-progress-v8', 'operation': 'inspect', 'expected_revision': 0, 'data': {}})]
            for script, args, request in requests:
                with self.subTest(script=script):
                    response = subprocess.run([sys.executable, str(scripts / script), *args],
                        input=json.dumps(request), text=True, capture_output=True)
                    self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
                    self.assertEqual(json.loads(response.stdout)['error']['code'], 'legacy_run_requires_original_runtime')
            self.assertFalse((root / 'old-checkpoint.json').exists())

    def test_delivery_capability_key_rejects_mixed_release(self):
        import shutil
        config = json.loads((ROOT / 'build/skill-packages.json').read_text())
        self.assertEqual(config['compatibility_key'].get('requirement_delivery'), 'requirement-delivery-v1')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / 'source'
            for directory in ('src', 'build', 'scripts'):
                shutil.copytree(ROOT / directory, fixture / directory)
            config['compatibility_key'].pop('requirement_delivery')
            (fixture / 'build/skill-packages.json').write_text(json.dumps(config))
            subprocess.run([sys.executable, str(fixture / 'scripts/build_skills.py'), '--output', str(root / 'legacy')], check=True)
            names = ('design-discussion', 'problem-framing', 'solution-design', 'guided-implementation', 'change-closure')
            current_keys = [json.loads((ROOT / 'skills' / name / 'package.json').read_text())['compatibility_key'] for name in names]
            self.assertTrue(all(key == current_keys[0] for key in current_keys))
            entries = [{'name': name, 'entry': str(ROOT / 'skills' / name / 'SKILL.md'), 'source': 'host'} for name in names]
            entries[1]['entry'] = str(root / 'legacy/problem-framing/SKILL.md')
            request = {'stage': 0, 'action': 'discuss', 'target_stages': [1],
                       'registry': {'source': 'host-current-skills', 'entries': entries}}
            result = subprocess.run([sys.executable, str(ROOT / 'skills/design-discussion/scripts/skill_preflight.py')],
                input=json.dumps(request), text=True, capture_output=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'incompatible_package')
            config['compatibility_key']['requirement_delivery'] = 'requirement-delivery-v1'
            config['compatibility_key']['control'] = 1
            config['compatibility_key']['stage_transfer'] = 'workflow-stage-transfer-v2'
            config['compatibility_key']['workflow_progress'] = 'workflow-progress-v6'
            (fixture / 'build/skill-packages.json').write_text(json.dumps(config))
            subprocess.run([sys.executable, str(fixture / 'scripts/build_skills.py'), '--output', str(root / 'legacy')], check=True)
            result = subprocess.run([sys.executable, str(ROOT / 'skills/design-discussion/scripts/skill_preflight.py')],
                input=json.dumps(request), text=True, capture_output=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'incompatible_package')

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

    def test_relative_from_import_requires_declared_module_or_export(self):
        import shutil
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();fixture=root/'source'
            for directory in ('src','build','scripts'):
                shutil.copytree(ROOT/directory,fixture/directory)
            source=fixture/'src/shared/scripts/discussion_core/__init__.py'
            source.write_text(source.read_text()+'\nfrom . import missing_runtime\n')
            result=subprocess.run([sys.executable,str(fixture/'scripts/build_skills.py'),'--output',str(root/'packages')],
                capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('missing_runtime',result.stderr)

    def test_committed_release_rebuilds_from_clean_archive(self):
        import io
        import tarfile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            archive=subprocess.check_output(['git','-C',str(ROOT),'-c','tar.umask=0022','archive','--format=tar','HEAD'])
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(root)
            result=subprocess.run([sys.executable,str(root/'scripts/build_skills.py'),'--check'],
                cwd=root,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(list(root.rglob('SKILL.md'))),5)

    def test_shared_changes_propagate_and_release_links_cannot_escape(self):
        import shutil
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();fixture=root/'source'
            for directory in ('src','build','scripts'):
                shutil.copytree(ROOT/directory,fixture/directory)
            command=[sys.executable,str(fixture/'scripts/build_skills.py')]
            output=root/'release'
            def build():return subprocess.run(command+['--output',str(output)],capture_output=True,text=True)
            def digests():return {p.parent.name:json.loads(p.read_text())['bundle_digest'] for p in output.glob('*/package.json')}
            self.assertEqual(build().returncode,0)
            original=digests()
            local=fixture/'src/stages/guided-implementation/scripts/workflow.py'
            local.write_text(local.read_text()+'\n# scoped release change\n')
            self.assertEqual(build().returncode,0)
            changed=digests()
            self.assertEqual({name for name in original if original[name]!=changed[name]},{'guided-implementation'})
            shared=fixture/'src/shared/references/package-execution.md'
            shared.write_text(shared.read_text()+'\nShared release clarification. [External documentation](https://example.com/skills/reference/).\n')
            self.assertEqual(build().returncode,0)
            self.assertTrue(all(digests()[name]!=changed[name] for name in changed))
            target=output/'design-discussion/SKILL.md';external=root/'external';external.write_bytes(target.read_bytes())
            target.unlink();target.symlink_to(external)
            result=subprocess.run(command+['--output',str(output),'--check'],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0,'symlink drift must fail even when bytes match')
            for invalid in ('[bad](missing.md)', '[bad](#unknown-anchor)', '`scripts/missing.py`', '{{resource:missing}}'):
                with self.subTest(invalid=invalid):
                    source=fixture/'src/stages/design-discussion/SKILL.md.in';before=source.read_text()
                    source.write_text(before+'\n'+invalid+'\n')
                    self.assertNotEqual(build().returncode,0)
                    source.write_text(before)
            code=fixture/'src/shared/scripts/discussion_protocol.py'
            code.write_text(code.read_text()+"\n__import__('unlisted_dynamic_module')\n")
            self.assertNotEqual(build().returncode,0)
            configuration=fixture/'build/skill-packages.json'
            data=json.loads(configuration.read_text())
            data['packages']['unexpected-sixth']=data['packages']['design-discussion']
            configuration.write_text(json.dumps(data))
            failure=build()
            self.assertNotEqual(failure.returncode,0)
            self.assertIn('exactly the five workflow packages',failure.stderr)

    def test_five_isolated_packages_execute_discussion_and_git_protocols(self):
        for package in ('design-discussion','problem-framing','solution-design','guided-implementation','change-closure'):
            with self.subTest(package=package):
                self.exercise_package(package)

    def exercise_package(self, package):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / 'installed'
            built = subprocess.run([sys.executable, str(ROOT / 'scripts/build_skills.py'),
                '--output', str(output), '--package', package], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            project = root / '项目 with spaces'
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            environment = dict(os.environ)
            environment.pop('PYTHONPATH', None)
            def call(request):
                result = subprocess.run([sys.executable, '-I', str(output / package / 'scripts/discussion_protocol.py')],
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
                result = subprocess.run([sys.executable, '-I', str(output / package / 'scripts/supervision_protocol.py'), operation],
                    input=json.dumps(request), capture_output=True, text=True, cwd=project, env=environment)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return json.loads(result.stdout)
            started = supervise('start-worktree', {'repository':str(project), 'target_branch':branch,
                'branch':'codex/isolated', 'worktree':str(root / 'flow')})
            verified = supervise('verify-worktree', {'binding':started['binding'], 'platform_cwd':str(root / 'flow')})
            self.assertTrue(verified['verified'])


if __name__ == '__main__':
    unittest.main()
