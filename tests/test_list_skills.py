# coding: utf-8
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = [REPOSITORY / 'skills' / name / 'scripts/list-skills.py'
           for name in ('design-discussion', 'solution-design', 'guided-implementation')]
FAKE_CODEX = '''# coding: utf-8
import json
import os
from pathlib import Path
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request.get('method') == 'initialize':
        result = {}
    elif request.get('method') == 'skills/list':
        Path(os.environ['WORKFLOW_REGISTRY_REQUEST']).write_text(
            json.dumps(request['params']), encoding='utf-8')
        result = json.loads(Path(os.environ['WORKFLOW_REGISTRY_FIXTURE']).read_text(encoding='utf-8'))
    else:
        continue
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
'''


class SkillRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.project = self.project.resolve()
        self.fixture = self.root / 'registry.json'
        self.request = self.root / 'request.json'
        self.binary = self.root / 'codex-fixture'
        self.binary.write_text('#!' + sys.executable + '\n' + FAKE_CODEX, encoding='utf-8')
        self.binary.chmod(0o700)
        self.skills = [
            {'name': 'to-spec', 'path': str(self.root / 'to-spec/SKILL.md'), 'enabled': True},
            {'name': 'tdd', 'path': str(self.root / 'tdd/SKILL.md'), 'enabled': False},
            {'name': 'to-spec', 'path': str(self.root / 'other-to-spec/SKILL.md'), 'enabled': True},
            {'name': 'unrelated', 'path': str(self.root / 'unrelated/SKILL.md'), 'enabled': True},
        ]
        self.registry = {'data': [{'cwd': str(self.project), 'skills': self.skills, 'errors': []}]}
        self.environment = {**os.environ, 'CODEX_BIN': str(self.binary),
                            'WORKFLOW_REGISTRY_FIXTURE': str(self.fixture),
                            'WORKFLOW_REGISTRY_REQUEST': str(self.request)}

    def query(self, *arguments, script=SCRIPTS[0]):
        self.fixture.write_text(json.dumps(self.registry), encoding='utf-8')
        return subprocess.run([sys.executable, '-B', str(script), '--cwd', str(self.project), *arguments],
                              env=self.environment, capture_output=True, text=True, encoding='utf-8')

    def assert_full_query(self):
        self.assertEqual(json.loads(self.request.read_text(encoding='utf-8')),
                         {'cwds': [str(self.project)], 'forceReload': True})

    def test_no_filter_preserves_full_output_in_all_three_copies(self):
        expected = [{'name': item['name'], 'entry': item['path'], 'enabled': item['enabled']}
                    for item in self.skills]
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = self.query(script=script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {'ok': True, 'cwd': str(self.project), 'skills': expected})
                self.assert_full_query()

    def test_filter_keeps_disabled_and_duplicate_entries_and_reports_missing_names(self):
        expected = [{'name': item['name'], 'entry': item['path'], 'enabled': item['enabled']}
                    for item in self.skills[:3]]
        for script in SCRIPTS:
            with self.subTest(script=script):
                result = self.query('--name', 'to-spec', '--name', 'tdd', '--name', 'missing', script=script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout),
                                 {'ok': True, 'cwd': str(self.project), 'skills': expected,
                                  'missing': ['missing']})
                self.assert_full_query()

    def test_matching_is_exact_and_missing_names_are_unique_in_request_order(self):
        result = self.query('--name', 'to', '--name', 'TO-SPEC', '--name', 'to')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['skills'], [])
        self.assertEqual(json.loads(result.stdout)['missing'], ['to', 'TO-SPEC'])
        self.assert_full_query()

    def test_filter_does_not_hide_invalid_unrequested_entries(self):
        self.skills[-1]['enabled'] = 'true'
        result = self.query('--name', 'to-spec')
        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['code'], 'invalid_registry')
        self.assertNotIn('skills', data)
        self.assert_full_query()

    def test_filter_does_not_hide_registry_load_errors(self):
        self.registry['data'][0]['errors'] = [{'path': str(self.root / 'unrelated/SKILL.md')}]
        result = self.query('--name', 'to-spec')
        self.assertEqual(result.returncode, 1, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data['ok'])
        self.assertEqual(data['error']['code'], 'registry_load_failed')
        self.assertNotIn('skills', data)
        self.assert_full_query()

    def test_invalid_names_are_rejected_before_starting_the_host(self):
        for name in ('', '   ', 'first\nsecond', 'first\tsecond', 'first\x85second',
                     'first\u2028second', 'first\u2029second'):
            with self.subTest(name=name):
                result = self.query('--name', name)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertFalse(self.request.exists())

    def test_three_copies_match_and_run_without_the_source_repository(self):
        self.assertEqual(SCRIPTS[0].read_bytes(), SCRIPTS[1].read_bytes())
        self.assertEqual(SCRIPTS[0].read_bytes(), SCRIPTS[2].read_bytes())
        installed = self.root / 'list-skills.py'
        shutil.copyfile(SCRIPTS[0], installed)
        result = self.query('--name', 'tdd', script=installed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['skills'],
                         [{'name': 'tdd', 'entry': self.skills[1]['path'], 'enabled': False}])
        self.assertEqual(json.loads(result.stdout)['missing'], [])


if __name__ == '__main__':
    unittest.main()
