"""Registration provenance crosses the generated entry CLI and real Flow Git."""
import copy
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import test_thread_settings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/shared/scripts'))
import entry_prepare as entry


class RegistrationContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.repo = self.root / 'repo'; self.repo.mkdir()
        self.git('init', '-q', '-b', 'main'); self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.repo / 'source').write_text('baseline'); self.git('add', '.'); self.git('commit', '-qm', 'baseline')
        self.flow = self.root / 'flow'
        package = ROOT / 'skills/guided-implementation'
        receipt = subprocess.run([sys.executable, str(package/'scripts/supervision_protocol.py'), 'start-worktree'],
            input=json.dumps({'repository': str(self.repo), 'worktree': str(self.flow), 'branch': 'codex/flow', 'target_branch': 'main'}),
            text=True, capture_output=True, check=True)
        self.binding = json.loads(receipt.stdout)['binding']
        sessions = self.root / 'sessions'
        fixture = test_thread_settings.ThreadSettingsTests(); fixture.make_rollout(sessions)
        self.env = {**os.environ, 'CODEX_THREAD_ID': fixture.thread_id, 'CODEX_SESSION_ID': fixture.thread_id,
                    'CODEX_SESSIONS_ROOT': str(sessions)}
        self.registry = {'source': 'controller-current-skills', 'entries': [
            {'name': 'guided-implementation', 'entry': str(package/'SKILL.md'), 'enabled': True, 'source': 'fixture-host'}]}
        self.context = {'project_path': str(self.repo), 'project_id': 'project', 'controller_ref': 'controller',
                        'receipt': 'host-query:1', 'registry_digest': entry.digest(self.registry)}
        self.input = self.root/'registry.json'; self.save()
        self.request = {'protocol': entry.PROTOCOL, 'operation': 'resolve', 'stage': 3, 'action': 'entry',
            'registry_input': str(self.input), 'host': {'project_path': str(self.flow), 'project_id': 'project',
            'thread_id': fixture.thread_id, 'controller_ref': 'controller', 'role': 'implementation-dispatcher',
            'source_ref': 'controller', 'receipt': 'native-spawn:1'}, 'source': {'kind': 'none'},
            'target': {'kind': 'flow', 'binding': self.binding}}
        self.cli = package/'scripts/entry_prepare.py'

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, check=True).stdout

    def save(self):
        self.input.write_text(json.dumps({'registry': self.registry, 'registry_context': self.context}))

    def call(self, request=None):
        result = subprocess.run([sys.executable, str(self.cli)], cwd=self.flow, env=self.env,
            input=json.dumps(request or self.request), text=True, capture_output=True)
        self.assertTrue(result.stdout, result.stderr)
        return json.loads(result.stdout)

    def test_flow_uses_original_registration_and_independent_binding(self):
        result = self.call()
        self.assertTrue(result['ok'], result)
        observed = result['result']
        self.assertEqual(observed['registration_context']['project_path'], str(self.repo))
        self.assertEqual(observed['repository']['cwd'], str(self.flow))
        self.assertEqual(observed['registration_context']['registry_digest'], entry.digest(self.registry))

    def test_missing_foreign_stale_and_mixed_evidence_fail_before_effects(self):
        before = self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD')
        missing = copy.deepcopy(self.request); missing.pop('registry_input')
        self.assertEqual(self.call(missing)['error']['code'], 'registry_context_required')
        mixed = {**self.request, 'registry': self.registry}
        self.assertEqual(self.call(mixed)['error']['code'], 'invalid_registry_context')
        for key, value in [('project_id', 'foreign'), ('controller_ref', 'other'), ('registry_digest', '0'*64),
                           ('project_path', str(self.flow))]:
            original = self.context[key]; self.context[key] = value; self.save()
            self.assertFalse(self.call()['ok'], key)
            self.context[key] = original
        self.save()
        wrong_cwd = copy.deepcopy(self.request); wrong_cwd['host']['project_path'] = str(self.repo)
        self.assertEqual(self.call(wrong_cwd)['error']['code'], 'project_mismatch')
        missing_binding = copy.deepcopy(self.request); missing_binding.pop('target')
        self.assertFalse(self.call(missing_binding)['ok'])
        self.assertEqual(before, (self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD')))

    def test_verify_rereads_file_allows_fresh_same_project_and_rejects_old_receipt(self):
        first = self.call()['result']
        verify = {**self.request, 'operation': 'verify', 'expected': first}
        self.registry['entries'].append({'name': 'unrelated', 'entry': '/unused/SKILL.md', 'enabled': False, 'source': 'fixture-host'})
        self.context['registry_digest'] = entry.digest(self.registry); self.save()
        self.assertEqual(self.call(verify)['error']['code'], 'registry_changed')
        self.context['receipt'] = 'host-query:2'; self.save()
        refreshed = self.call(verify)
        self.assertTrue(refreshed['ok'], refreshed)
        self.assertEqual(refreshed['result']['packages'], first['packages'])
        self.registry['entries'][0]['enabled'] = False
        self.context['registry_digest'] = entry.digest(self.registry); self.context['receipt'] = 'host-query:3'; self.save()
        self.assertEqual(self.call(verify)['error']['code'], 'skill_not_active')

    def test_pinned_package_drift_and_foreign_flow_binding_fail(self):
        package = self.root/'pinned-package'
        shutil.copytree(ROOT/'skills/guided-implementation',package)
        self.registry['entries'][0]['entry'] = str(package/'SKILL.md')
        self.context['registry_digest'] = entry.digest(self.registry); self.save()
        self.cli = package/'scripts/entry_prepare.py'
        first = self.call(); self.assertTrue(first['ok'],first)
        wrong = copy.deepcopy(self.request); wrong['target']['binding']['worktree'] = str(self.repo)
        self.assertFalse(self.call(wrong)['ok'])
        (package/'SKILL.md').write_text((package/'SKILL.md').read_text()+'\nchanged pinned bytes\n')
        self.assertEqual(self.call({**self.request,'operation':'verify','expected':first['result']})['error']['code'],'package_changed')

if __name__ == '__main__': unittest.main()
