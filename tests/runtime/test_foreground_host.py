"""Foreground lifecycle through the public runner and real C/B/Git fixtures."""
import io
import json
import os
import subprocess
import threading
import time
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_workflow_progress as scenario
import test_workflow as cli

runner = scenario.runner
progress = scenario.progress

# This external process speaks both the old CLI transport (for the regression)
# and app-server stdio. It never substitutes runner or C decisions.
HOST = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
log = Path(os.environ['HOST_LOG'])
def note(value):
    with log.open('a') as stream: stream.write(json.dumps(value) + '\n')
def emit(value): print(json.dumps(value), flush=True)
def result(turn):
    return dict(result='continue' if turn == 1 else 'needs_input', artifacts=[], evidence=[], handoff_json='{}', question='' if turn == 1 else 'bounded fixture end', message='native child running' if turn == 1 else '', needs_input_kind='none' if turn == 1 else 'technical_error')
if '--version' in sys.argv:
    print('codex-cli fixture'); sys.exit()
note({'event':'spawn', 'pid':os.getpid()})
if sys.argv[1] == 'exec':
    turn = sum(json.loads(line)['event'] == 'spawn' for line in log.read_text().splitlines())
    emit({'type':'thread.started','thread_id':'carrier'})
    
    import time
    while not Path(str(log)+'.ready').exists(): time.sleep(0.01)
    emit({'type':'turn.completed'})
    Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(result(turn)))
    sys.exit()
turn = 0
for line in sys.stdin:
    request = json.loads(line); method = request['method']; p = request.get('params', {})
    note({'event':'request','method':method,'params':p})
    if method == 'initialized': continue
    if method == 'initialize': response = {'userAgent':'fixture'}
    elif method == 'thread/start':
        response = dict(thread={'id':'carrier'}, model=p['model'], reasoningEffort=p['config']['model_reasoning_effort'], cwd=p['cwd'], approvalPolicy=p['approvalPolicy'], sandbox={'type':'readOnly'}, runtimeWorkspaceRoots=p['runtimeWorkspaceRoots'])
    elif method == 'turn/start':
        turn += 1
        import time
        while not Path(str(log)+'.ready').exists(): time.sleep(0.01)
        response = {'turn':{'id':str(turn),'status':'inProgress'}}
    else: raise RuntimeError(method)
    emit({'id':request['id'],'result':response})
    if method == 'turn/start':
        emit({'method':'item/completed','params':{'threadId':'carrier','turnId':str(turn),'item':{'type':'agentMessage','id':'message-'+str(turn),'phase':'final_answer','text':json.dumps(result(turn))}}})
        emit({'method':'turn/completed','params':{'threadId':'carrier','turn':{'id':str(turn),'status':'completed'}}})
note({'event':'eof'})
'''


class ForegroundLifecycleTests(scenario.ProgressTests):
    def test_continue_running_keeps_one_host_and_original_native_ref(self):
        host = self.checkpoint.parent / 'host'
        host.write_text(HOST)
        host.chmod(0o755)
        log = self.checkpoint.parent / 'host.jsonl'
        # Reuse the current registry/configuration evidence builder, replacing
        # its transport-only requirement with the real A frozen receipt.
        helper = cli.WorkflowCliTests()
        helper.repository, helper.worktree, helper.record = self.root, self.flow, self.checkpoint
        raw = helper.confirmed_input()
        raw.update(requirement=self.frozen, controller_ref='task',
                   frozen_requirement={'path':self.frozen['absolute_path'], 'commit':self.frozen['commit'], 'sha256':self.frozen['sha256']})
        raw['authority_scope']['allowed_paths'] = ['impl.py', 'CHANGELOG.md']
        for settings in raw['stages'].values():
            settings['model'] = 'fixture-model'
            settings['selection_input']['supported'][0]['model'] = 'fixture-model'
            settings['selection_input']['user']['model'] = 'fixture-model'
        raw['host'] = {'transport':'app-server-stdio','cli_version':'codex-cli fixture',
            'source':{'kind':'controller-current-config','controller_ref':'task','receipt':'fixture:effective-config'},
            'thread':{'approvalPolicy':'never','sandbox':'read-only','config':{},
                      'runtimeWorkspaceRoots':[str(self.root),str(self.flow),self.binding['git_common_dir']]},
            'effective':{'approvalPolicy':'never','sandbox':{'type':'readOnly'},
                         'runtimeWorkspaceRoots':[str(self.root),str(self.flow),self.binding['git_common_dir']]}}
        confirmed = self.checkpoint.parent / 'confirmed.json'
        confirmed.write_text(json.dumps(raw))
        original = subprocess.Popen
        created = []
        errors = []
        def bind_native():
            try:
                deadline = time.monotonic() + 10
                while progress.read_record(self.checkpoint).get('sessions', {}).get('stage2') != 'carrier':
                    if time.monotonic() > deadline: raise RuntimeError('carrier did not bind')
                    time.sleep(0.01)
                self.settings['thread_id'] = 'carrier'
                self.input['entry']['host'].update(thread_id='carrier', role='scripted-carrier', source_ref='runner')
                self.input['expected_entry'] = scenario.transfer.entry.resolve(self.input['entry'])
                self.begin('continuous')
                self.invoke('observe', self.observation())
                self.invoke('observe', self.observation('running','result'))
            except BaseException as error:
                errors.append(error)
            finally:
                Path(str(log)+'.ready').touch()
        worker = threading.Thread(target=bind_native)
        def spawn(command, *args, **kwargs):
            if command[0] == str(host) and '--version' not in command:
                created.append(command)
                if len(created) == 1:
                    worker.start()
            return original(command, *args, **kwargs)
        with patch.object(runner, '__file__', str(cli.SCRIPT)), patch.dict(os.environ, {'CODEX_BIN':str(host),'HOST_LOG':str(log)}), patch.object(subprocess,'Popen',side_effect=spawn), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(runner.WorkflowError, 'bounded fixture end'):
                runner.start(confirmed)
        worker.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(created), 1, 'continue while C reports running must retain its foreground host')
        self.assertEqual(self.state()['control']['context']['carrier']['ref'], 'native:designer')
        self.assertEqual(self.state()['host']['status'], 'running')
        before = self.checkpoint.read_bytes()
        with patch.object(runner, '__file__', str(cli.SCRIPT)), self.assertRaisesRegex(runner.WorkflowError, 'await-host-recovery'):
            runner.resume(self.checkpoint)
        self.assertEqual(self.checkpoint.read_bytes(), before)

    def test_legacy_runner_is_read_only(self):
        for version in (1, 2, 3):
            self.checkpoint.write_text(json.dumps({'version':version}))
            before = self.checkpoint.read_bytes()
            with self.assertRaisesRegex(runner.WorkflowError, 'legacy_run_requires_original_runtime'):
                runner.resume(self.checkpoint)
            self.assertEqual(self.checkpoint.read_bytes(), before)


class HostProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executable = self.root / 'host'
        self.executable.write_text(HOST)
        self.executable.chmod(0o755)
        self.log = self.root / 'log'
        Path(str(self.log) + '.ready').touch()
        environment = patch.dict(os.environ, {'HOST_LOG':str(self.log)})
        environment.start()
        self.addCleanup(environment.stop)
        self.settings = {'model':'fixture-model','reasoning_effort':'high'}
        self.configuration = {'cli_version':'codex-cli fixture',
            'thread':{'approvalPolicy':'never','sandbox':'read-only','config':{},'runtimeWorkspaceRoots':[str(self.root)]},
            'effective':{'approvalPolicy':'never','sandbox':{'type':'readOnly'},'runtimeWorkspaceRoots':[str(self.root)]}}
        self.events = []
        self.host = runner.foreground_host.ForegroundHost(self.configuration, str(self.root), self.events.append,
                                                         executable=str(self.executable))
        self.addCleanup(self.host.close)

    def test_cross_turn_native_events_are_retained_but_do_not_complete_carrier(self):
        changed = HOST.replace("if method == 'turn/start':\n        emit", """if method == 'turn/start':
        emit({'method':'turn/completed','params':{'threadId':'other','turn':{'id':str(turn),'status':'completed'}}})
        emit({'method':'turn/completed','params':{'threadId':'carrier','turn':{'id':'older','status':'completed'}}})
        emit({'method':'item/completed','params':{'threadId':'carrier','turnId':'older','item':{'type':'subAgentActivity','kind':'completed','agentThreadId':'original-child'}}})
        emit({'method':'item/agentMessage/delta','params':{'threadId':'carrier','turnId':str(turn),'delta':'not a result'}})
        emit""")
        self.executable.write_text(changed)
        self.host.start(self.root / 'stderr')
        thread = self.host.start_carrier('stage2', self.settings)
        first, first_turn = self.host.run_turn(thread, 'first', self.settings, {})
        second, second_turn = self.host.run_turn(thread, 'second', self.settings, {})
        self.assertEqual((json.loads(first)['result'], json.loads(second)['result']), ('continue','needs_input'))
        self.assertNotEqual(first_turn, second_turn)
        observed = [e['message'] for e in self.events if e['kind'] == 'message']
        self.assertEqual(sum(e.get('params', {}).get('item', {}).get('agentThreadId') == 'original-child' for e in observed), 2)
        requests = [e['request'] for e in self.events if e['kind'] == 'request-intent']
        self.assertEqual([r['method'] for r in requests], ['initialize','thread/start','turn/start','turn/start'])
        self.assertFalse(requests[1]['params']['allowProviderModelFallback'])

    def test_configuration_drift_stops_before_first_business_turn(self):
        self.executable.write_text(HOST.replace("model=p['model']", "model='unexpected-model'"))
        self.host.start(self.root / 'stderr')
        with self.assertRaisesRegex(runner.foreground_host.HostError, 'host_configuration_changed'):
            self.host.start_carrier('stage2', self.settings)
        self.assertNotIn('turn/start', [e['request']['method'] for e in self.events if e['kind'] == 'request-intent'])

    def test_rpc_error_is_not_a_notification_or_success(self):
        self.executable.write_text(HOST.replace("emit({'id':request['id'],'result':response})", "emit({'id':request['id'],'error':{'code':-32602,'message':'unsupported'}})"))
        with self.assertRaisesRegex(runner.foreground_host.HostError, 'host_rpc_error'):
            self.host.start(self.root / 'stderr')
        self.assertEqual([e['request']['method'] for e in self.events if e['kind'] == 'request-intent'], ['initialize'])

    def test_duplicate_rpc_response_is_rejected(self):
        self.executable.write_text(HOST.replace("emit({'id':request['id'],'result':response})", "emit({'id':request['id'],'result':response}); emit({'id':request['id'],'result':response})"))
        self.host.start(self.root / 'stderr')
        with self.assertRaisesRegex(runner.foreground_host.HostError, 'unexpected or duplicate RPC response'):
            self.host.start_carrier('stage2', self.settings)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(ForegroundLifecycleTests(name) for name in loader.getTestCaseNames(ForegroundLifecycleTests) if name in ForegroundLifecycleTests.__dict__)
    suite.addTests(loader.loadTestsFromTestCase(HostProtocolTests))
    return suite
