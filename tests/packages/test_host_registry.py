"""Host registry transport and explicit-only Skill regression coverage."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/shared/scripts'))
from skill_preflight import PreflightError, preflight


class HostRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.packages = cls.root / 'packages'
        subprocess.run([sys.executable, str(ROOT/'scripts/build_skills.py'), '--output', str(cls.packages)], check=True)
        cls.external = cls.root / 'to-spec' / 'SKILL.md'
        cls.external.parent.mkdir()
        cls.external.write_text('---\nname: to-spec\n---\n')
        agents = cls.external.parent / 'agents'
        agents.mkdir()
        (agents/'openai.yaml').write_text('policy:\n  allow_implicit_invocation: false\n')
        cls.binary = cls.root / 'codex'
        cls.binary.write_text('#!' + sys.executable + '\n' + '''import json, os, sys, time
assert sys.argv[1:] == ['app-server', '--stdio']
def read(): return json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value), flush=True)
init = read()
assert init['method'] == 'initialize' and init['params']['clientInfo']['name']
send({'id':init['id'], 'result':{}})
assert read()['method'] == 'initialized'
request = read()
assert request['method'] == 'skills/list'
assert request['params'] == {'cwds':[os.environ['TEST_CWD']], 'forceReload':True}
mode = os.environ.get('TEST_MODE')
if mode == 'timeout': time.sleep(30)
if mode == 'overflow': print('x' * (2 * 1024 * 1024 + 1), flush=True); time.sleep(30)
if mode == 'exit': sys.exit(1)
if mode == 'malformed': print('not json', flush=True); time.sleep(30)
send({'id':request['id'], **json.loads(os.environ['TEST_RESPONSE'])})
time.sleep(30)
''')
        cls.binary.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def data(self):
        return {'data':[{'cwd':str(self.root), 'errors':[], 'skills':[
            {'name':'solution-design','path':str(self.packages/'solution-design/SKILL.md'),'enabled':True},
            {'name':'to-spec','path':str(self.external),'enabled':True}]}]}

    def query(self, result=None, mode='', **extra):
        response = {'result': self.data()} if result is None else result
        with patch.dict(os.environ, {'CODEX_BIN':str(self.binary), 'TEST_CWD':str(self.root),
                                     'TEST_RESPONSE':json.dumps(response), 'TEST_MODE':mode}):
            return preflight({'stage':2,'action':'spec','registry_query':{'cwd':str(self.root)}, **extra})

    def test_enabled_explicit_only_skill_succeeds_without_prompt_catalog(self):
        self.assertTrue(self.query()['external']['to-spec']['callable'])

    def test_packaged_cli_uses_host_registry(self):
        with patch.dict(os.environ, {'CODEX_BIN':str(self.binary), 'TEST_CWD':str(self.root),
                                     'TEST_RESPONSE':json.dumps({'result':self.data()}), 'TEST_MODE':''}):
            result = subprocess.run([sys.executable, str(self.packages/'solution-design/scripts/skill_preflight.py')],
                input=json.dumps({'stage':2, 'action':'spec', 'registry_query':{'cwd':str(self.root)}}),
                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['external']['to-spec']['callable'])

    def test_conflicting_or_invalid_query_rejected(self):
        for extra in ({'registry':{}}, {'registry_query':{'cwd':'relative'}}, {'registry_query':{'cwd':str(self.root),'extra':1}}):
            with self.subTest(extra=extra), self.assertRaises(PreflightError) as error:
                self.query(**extra)
            self.assertEqual(error.exception.code, 'invalid_request')

    def test_missing_disabled_and_malformed_host_evidence_fail_closed(self):
        for kind, code in [('disabled','skill_disabled'),('missing','skill_not_registered'),('cwd','invalid_registry'),
                           ('enabled','invalid_registry'),('path','invalid_registry'),('errors','registry_load_failed')]:
            data=self.data(); row=data['data'][0]
            if kind=='disabled': row['skills'][1]['enabled']=False
            elif kind=='missing': row['skills'].pop()
            elif kind=='cwd': row['cwd']='/other'
            elif kind=='enabled': row['skills'][1].pop('enabled')
            elif kind=='path': row['skills'][1]['path']='relative'
            else: row['errors']=[{'message':'failed'}]
            with self.subTest(kind=kind), self.assertRaises(PreflightError) as error:
                self.query({'result':data})
            self.assertEqual(error.exception.code, code)

    def test_host_errors_preserve_only_bounded_structured_diagnostics(self):
        secret = 'PRIVATE_ERROR_PAYLOAD'
        data = self.data()
        paths = [str(self.root / ('broken-' + str(i)) / 'SKILL.md') for i in range(12)]
        data['data'][0]['errors'] = [
            {'path': 'relative/' + secret, 'message': secret},
            {'path': '/' + 'x' * 2048, 'message': secret},
            {'path': '/bad\npath', 'message': secret},
            {'message': secret},
        ] + [{'path': path, 'message': secret, 'data': {'token': secret}} for path in paths]
        with self.assertRaises(PreflightError) as error:
            self.query({'result': data})
        self.assertEqual(error.exception.code, 'registry_load_failed')
        self.assertEqual(error.exception.details, {'error_count': 16, 'error_paths': paths[:8]})
        self.assertNotIn(secret, str(error.exception) + json.dumps(error.exception.details))
        for code in (-32602, True, secret, 2 ** 80):
            with self.subTest(code=code), self.assertRaises(PreflightError) as error:
                self.query({'error': {'code': code, 'message': secret, 'data': {'token': secret}}})
            self.assertEqual(error.exception.code, 'registry_rpc_error')
            self.assertEqual(error.exception.details, {'rpc_code': -32602} if type(code) is int and code == -32602 else {})
            self.assertNotIn(secret, str(error.exception) + json.dumps(error.exception.details))

    def test_transport_failures_are_bounded_and_structured(self):
        import host_skill_registry
        for mode, code in [('timeout','registry_timeout'), ('overflow','registry_output_limit'),
                           ('exit','registry_unavailable'), ('malformed','invalid_registry')]:
            with self.subTest(mode=mode), patch.object(host_skill_registry, 'TIMEOUT_SECONDS', 0.3), self.assertRaises(PreflightError) as error:
                self.query(mode=mode)
            self.assertEqual(error.exception.code, code)
        with self.assertRaises(PreflightError) as error:
            self.query({'error':{'code':-1,'message':'unavailable'}})
        self.assertEqual(error.exception.code,'registry_rpc_error')
        with patch.dict(os.environ, {'CODEX_BIN':str(self.root/'missing-codex')}), self.assertRaises(PreflightError) as error:
            preflight({'stage':2,'action':'spec','registry_query':{'cwd':str(self.root)}})
        self.assertEqual(error.exception.code, 'registry_unavailable')


if __name__ == '__main__': unittest.main()
