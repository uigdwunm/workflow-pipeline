"""Foreground lifecycle through the public runner and real C/B/Git fixtures."""
import io
import json
import os
import subprocess
import sys
import threading
import time
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_workflow_progress as scenario
import test_workflow as cli

runner = scenario.runner
progress = scenario.progress


def publish_host_fixture(path, value):
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def host_calls(path):
    if not path.exists():
        return []
    # The external process can still be appending its final JSONL record.
    return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith('\n')]

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

CHAIN_HOST = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
log = Path(os.environ['HOST_LOG'])
def note(value):
    with log.open('a') as stream: stream.write(json.dumps(value) + '\n')
def emit(value): print(json.dumps(value),flush=True)
if '--version' in sys.argv: print('codex-cli fixture'); sys.exit()
note({'event':'spawn','pid':os.getpid()})
threads = 0
turns = 0
for line in sys.stdin:
    request = json.loads(line); method = request['method']; params = request.get('params',{})
    note({'event':'request','method':method,'params':params})
    if method == 'initialized': continue
    if method == 'initialize': response = {'userAgent':'fixture'}
    elif method == 'thread/start':
        threads += 1
        response = dict(thread={'id':'carrier-'+str(threads)},model=params['model'],cwd=params['cwd'],
            reasoningEffort=params['config']['model_reasoning_effort'],approvalPolicy=params['approvalPolicy'],
            sandbox={'type':'readOnly'},runtimeWorkspaceRoots=params['runtimeWorkspaceRoots'])
    elif method == 'turn/start':
        turns += 1
        payload = json.loads(params['input'][0]['text'].rsplit('\n',1)[1]); stage = payload['stage']
        emit({'id':request['id'],'result':{'turn':{'id':str(turns),'status':'inProgress'}}})
        path = Path(str(log)+'.turn-'+str(turns))
        deadline = time.monotonic()+45
        while not path.exists():
            if time.monotonic()>deadline: raise RuntimeError('fixture carrier deadline')
            time.sleep(.01)
        result = json.loads(path.read_text())
        emit({'method':'item/completed','params':{'threadId':params['threadId'],'turnId':str(turns),
            'item':{'type':'agentMessage','id':stage+'-message','phase':'final_answer','text':json.dumps(result)}}})
        emit({'method':'turn/completed','params':{'threadId':params['threadId'],'turn':{'id':str(turns),'status':'completed'}}})
        continue
    elif method == 'thread/list':
        snapshot = json.loads(Path(str(log)+'.snapshot-'+params['ancestorThreadId']).read_text())
        response = next(page['response'] for page in snapshot['pages'] if page['request']['params']['archived'] == params['archived'])
    else: raise RuntimeError(method)
    emit({'id':request['id'],'result':response})
note({'event':'eof'})
'''


class ForegroundLifecycleTests(scenario.ProgressTests):
    def fixture(self):
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
        return host, log, confirmed

    def run_running_carrier(self, expected_error="invalid needs_input_kind", script=None):
        host, log, confirmed = self.fixture()
        host.write_text((script or HOST).replace("'technical_error'", "'invalid-kind'"))
        original = subprocess.Popen
        self.host_processes = []
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
            process = original(command, *args, **kwargs)
            if command[0] == str(host) and '--version' not in command:
                self.host_processes.append(process)
            return process
        with patch.object(runner, '__file__', str(cli.SCRIPT)), patch.dict(os.environ, {'CODEX_BIN':str(host),'HOST_LOG':str(log)}), patch.object(subprocess,'Popen',side_effect=spawn), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(runner.WorkflowError, expected_error):
                runner.start(confirmed)
        worker.join(10)
        self.assertEqual(errors, [])
        return created, log, confirmed

    def test_continue_running_keeps_one_host_and_original_native_ref(self):
        created, log, confirmed = self.run_running_carrier()
        self.assertEqual(len(created), 1, 'continue while C reports running must retain its foreground host')
        self.assertEqual(self.state()['control']['context']['carrier']['ref'], 'native:designer')
        self.assertEqual(self.state()['host']['status'], 'unknown')
        self.assertIsNone(self.state()['host']['proof'])
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

    def test_proven_prelaunch_failure_can_retry_without_replaying_a_host(self):
        executable, log, confirmed = self.fixture()
        executable.write_text(HOST.replace("print('codex-cli fixture')","print('codex-cli incompatible')"))
        with patch.object(runner,'__file__',str(cli.SCRIPT)), patch.dict(os.environ,{'CODEX_BIN':str(executable),'HOST_LOG':str(log)}), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(runner.WorkflowError,'host_version_changed'):
                runner.start(confirmed)
            self.assertFalse(log.exists())
            saved = progress.read_record(self.checkpoint)
            self.assertEqual(saved['launch']['state'],'prelaunch')
            self.assertEqual(saved['sessions'],{})
            self.assertNotIn('transport',saved)
            executable.write_text(HOST.replace("emit({'id':request['id'],'result':response})",
                "emit({'id':request['id'],'error':{'code':-32602,'message':'fixture stop before business'}})"))
            with self.assertRaisesRegex(runner.WorkflowError,'host_rpc_error'):
                runner.resume(self.checkpoint)
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(event['event']=='spawn' for event in events),1)
        self.assertFalse(any(event.get('method')=='turn/start' for event in events))

    def test_completed_transport_receipt_survives_main_checkpoint_failure(self):
        original_replace = os.replace
        failed = []
        def fail_checkpoint(source, target):
            if Path(target).resolve() == self.checkpoint.resolve() and not failed:
                value = json.loads(Path(source).read_text())
                if value.get('turn_result', {}).get('result') == 'continue':
                    failed.append(True)
                    raise OSError('injected checkpoint failure')
            return original_replace(source,target)
        with patch.object(os,'replace',side_effect=fail_checkpoint):
            created, log, _ = self.run_running_carrier('checkpoint_write_pending')
        self.assertEqual(len(created),1)
        receipt = runner._carrier_receipt_path(self.checkpoint,'stage2',1)
        self.assertEqual(json.loads(receipt.read_text())['outcome'],'completed_turn')
        before_log = log.read_bytes()
        before_head = self.flow_git('rev-parse','HEAD')
        with patch.object(runner,'__file__',str(cli.SCRIPT)), self.assertRaisesRegex(runner.WorkflowError,'await-host-recovery'):
            runner.resume(self.checkpoint)
        self.assertEqual(log.read_bytes(),before_log)
        self.assertEqual(self.flow_git('rev-parse','HEAD'),before_head)
        self.assertEqual(progress.read_record(self.checkpoint)['turn_result']['result'],'continue')

    def test_eof_after_request_write_never_replays_that_turn(self):
        script = HOST.replace("response = {'turn':", "sys.exit(7)\n        response = {'turn':")
        created, log, _ = self.run_running_carrier('transport_lost',script)
        self.assertEqual(len(created),1)
        before = log.read_bytes()
        with patch.object(runner,'__file__',str(cli.SCRIPT)), self.assertRaises(runner.WorkflowError):
            runner.resume(self.checkpoint)
        self.assertEqual(log.read_bytes(),before)
        self.assertIsNotNone(self.state()['transport_loss'])
        self.assertFalse(self.state()['stopped'])

    def test_raw_turn_recovers_when_carrier_receipt_save_fails(self):
        original_replace = os.replace
        failed = []
        def fail_receipt(source,target):
            if str(target).endswith('.carrier.json') and not failed:
                value = json.loads(Path(source).read_text())
                if value.get('outcome') == 'completed_turn':
                    failed.append(True)
                    raise OSError('injected receipt failure')
            return original_replace(source,target)
        with patch.object(os,'replace',side_effect=fail_receipt):
            created, log, _ = self.run_running_carrier('checkpoint_write_pending')
        self.assertEqual(len(created),1)
        before = log.read_bytes()
        with patch.object(runner,'__file__',str(cli.SCRIPT)), self.assertRaisesRegex(runner.WorkflowError,'await-host-recovery'):
            runner.resume(self.checkpoint)
        self.assertEqual(log.read_bytes(),before)
        self.assertEqual(progress.read_record(self.checkpoint)['turn_result']['result'],'continue')

    def test_live_controller_answers_two_exact_server_requests_once(self):
        script = HOST.replace("if method == 'turn/start':\n        emit", """if method == 'turn/start':
        if turn == 1:
            for identity in ('approval-1','approval-2'):
                emit({'id':identity,'method':'item/commandExecution/requestApproval','params':{'threadId':'carrier','turnId':str(turn),'command':[identity]}})
            for index in range(2): note({'event':'server-response','message':json.loads(sys.stdin.readline())})
        emit""")
        errors = []
        aborting = threading.Event()
        def controller():
            seen = set()
            try:
                deadline = time.monotonic()+20
                while len(seen) < 2 and not aborting.is_set():
                    pending = progress.read_record(self.checkpoint).get('pending_input') or {}
                    if pending.get('kind') == 'host-request' and pending['decision_id'] not in seen:
                        answer = json.dumps({'decision':'decline'})
                        runner.resume(self.checkpoint,answer,decision_id=pending['decision_id'])
                        runner.resume(self.checkpoint,answer,decision_id=pending['decision_id'])
                        seen.add(pending['decision_id'])
                    if time.monotonic()>deadline: raise RuntimeError('server decision deadline')
                    time.sleep(.02)
            except BaseException as error:
                errors.append(error)
                for process in getattr(self,'host_processes',[]):
                    if process.poll() is None: process.terminate()
        worker = threading.Thread(target=controller)
        worker.start()
        try:
            _,log,_ = self.run_running_carrier(script=script)
        finally:
            aborting.set(); worker.join(5)
        self.assertEqual(errors,[])
        replies = [json.loads(line)['message'] for line in log.read_text().splitlines() if json.loads(line)['event']=='server-response']
        self.assertEqual({reply['id'] for reply in replies},{'approval-1','approval-2'})
        self.assertEqual(len(replies),2)
        self.assertTrue(all(reply['result']=={'decision':'decline'} for reply in replies))

    def test_live_answer_is_queued_once_without_consuming_c_or_starting_host(self):
        _, _, confirmed = self.fixture()
        raw = json.loads(confirmed.read_text())
        raw['registry_input'] = str(confirmed)
        self.begin('continuous')
        self.invoke('observe', self.observation())
        pending = self.invoke('observe', self.observation('idle','result',
            {'delivery_id':'question','status':'needs_input','payload':{'question':'choose behavior'}}))['pending']
        before_c = self.state()
        with patch.object(runner, '__file__', str(cli.SCRIPT)):
            state = runner._new_state(runner.validate_confirmed(raw))
            state.update(status='needs_input', pending_input=pending, transport={'instance':'original'})
            runner._atomic_save(self.checkpoint, state)
            output = io.StringIO()
            with runner.RunLock(self.checkpoint), redirect_stdout(output):
                self.assertEqual(runner.resume(self.checkpoint, 'choice A', decision_id=pending['decision_id']), 0)
                self.assertEqual(runner.resume(self.checkpoint, 'choice A', decision_id=pending['decision_id']), 0)
                with self.assertRaisesRegex(runner.WorkflowError, 'decision_conflict'):
                    runner.resume(self.checkpoint, 'choice B', decision_id=pending['decision_id'])
                with self.assertRaisesRegex(runner.WorkflowError, 'stale_decision'):
                    runner.resume(self.checkpoint, 'choice A', decision_id='old')
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved[progress.KEY], before_c)
        self.assertEqual(saved['controller_decision']['subject'], pending['subject'])
        self.assertEqual(saved['controller_decision']['answer'], 'choice A')
        self.assertEqual(saved['sessions'], {})
        self.assertTrue(all(json.loads(line)['status'] == 'queued' for line in output.getvalue().splitlines()))

    def test_stop_queued_after_approval_preempts_actual_server_response(self):
        executable, log, confirmed = self.fixture()
        executable.write_text(HOST.replace("else: raise RuntimeError(method)",
            "elif method == 'turn/steer': response = {'turnId':p['expectedTurnId']}\n    else: raise RuntimeError(method)").replace(
            "request = json.loads(line); method = request['method']; p = request.get('params', {})",
            "request = json.loads(line)\n    if 'method' not in request:\n        note({'event':'server-response','message':request}); continue\n    method = request['method']; p = request.get('params', {})"))
        for operation, timing in ((operation,timing) for operation in ('pause','cancel') for timing in ('queued','before-write')):
            with self.subTest(operation=operation,timing=timing), patch.object(runner,'__file__',str(cli.SCRIPT)), \
                    patch.dict(os.environ,{'HOST_LOG':str(log)}), redirect_stdout(io.StringIO()):
                raw = json.loads(confirmed.read_text()); raw['registry_input'] = str(confirmed)
                state = runner._new_state(runner.validate_confirmed(raw))
                events = []
                def journal(event):
                    events.append(event)
                    if timing == 'before-write' and event['kind'] == 'server-response-intent':
                        runner.request_control(self.checkpoint,operation)
                host = runner.foreground_host.ForegroundHost(raw['host'],str(self.flow),journal,executable=str(executable))
                try:
                    host.start(self.checkpoint.parent/('approval-'+operation+'.stderr'))
                    thread = host.start_carrier('stage2',{'model':'fixture-model','reasoning_effort':'high'})
                    host.active = (thread,'1')
                    request = {'id':'approval','method':'item/commandExecution/requestApproval',
                               'params':{'threadId':thread,'turnId':'1','command':['must-not-run']}}
                    host.server_requests['approval'] = request
                    pending = runner._host_pending(host.instance,request)
                    state.update(status='needs_input',pending_input=pending,
                                 transport={'instance':host.instance,'state':'live'})
                    progress.atomic_save(self.checkpoint,state)
                    with runner.RunLock(self.checkpoint):
                        runner.resume(self.checkpoint,json.dumps({'decision':'accept'}),decision_id=pending['decision_id'])
                        if timing == 'queued':
                            runner.request_control(self.checkpoint,operation)
                        steered = set()
                        runner._live_progress(state,self.checkpoint,host,steered)
                        runner._live_progress(state,self.checkpoint,host,steered)
                    self.assertFalse(any(event['kind']=='server-response-sent' for event in events))
                    self.assertEqual(sum(event.get('request',{}).get('method')=='turn/steer' for event in events),1)
                    self.assertFalse(any(call['event']=='server-response' for call in host_calls(log)))
                    self.assertIn('server_decision',progress.read_record(self.checkpoint)['transport'])
                finally:
                    host.close()

    def test_live_owner_delivers_answer_in_original_carrier(self):
        executable, log, confirmed = self.fixture()
        script = HOST.replace("result='continue' if turn == 1 else 'needs_input'", "result='needs_input'")
        script = script.replace("question='' if turn == 1 else 'bounded fixture end'", "question='choose behavior' if turn == 1 else 'bounded fixture end'")
        script = script.replace("needs_input_kind='none' if turn == 1", "needs_input_kind='user_decision' if turn == 1")
        script = script.replace("response = {'turn':", "\n        if turn == 2:\n            while not Path(str(log)+'.answered').exists(): time.sleep(0.01)\n        response = {'turn':")
        executable.write_text(script.replace("'technical_error'", "'invalid-kind'"))
        errors, native_errors = [], []
        def native_adapter():
            try:
                deadline = time.monotonic() + 15
                while progress.read_record(self.checkpoint).get('sessions', {}).get('stage2') != 'carrier':
                    if time.monotonic() > deadline: raise RuntimeError('carrier did not bind')
                    time.sleep(0.01)
                self.settings['thread_id'] = 'carrier'
                self.input['entry']['host'].update(thread_id='carrier',role='scripted-carrier',source_ref='runner')
                self.input['expected_entry'] = scenario.transfer.entry.resolve(self.input['entry'])
                self.begin('continuous')
                self.invoke('observe', self.observation())
                self.invoke('observe', self.observation('idle','result',
                    {'delivery_id':'question','status':'needs_input','payload':{'question':'choose behavior'}}))
                Path(str(log)+'.ready').touch()
                while True:
                    turns = [call for call in host_calls(log) if call.get('method') == 'turn/start']
                    if len(turns) == 2: break
                    if time.monotonic() > deadline: raise RuntimeError('second carrier turn not delivered')
                    time.sleep(0.01)
                decision = progress.read_record(self.checkpoint)['controller_decision']
                self.invoke('decide', decision)
            except BaseException as error:
                native_errors.append(error)
            finally:
                Path(str(log)+'.ready').touch()
                Path(str(log)+'.answered').touch()
        def run_owner():
            try:
                runner.start(confirmed)
            except BaseException as error:
                errors.append(error)
        with patch.object(runner,'__file__',str(cli.SCRIPT)), patch.dict(os.environ,{'CODEX_BIN':str(executable),'HOST_LOG':str(log)}), redirect_stdout(io.StringIO()):
            native = threading.Thread(target=native_adapter)
            owner = threading.Thread(target=run_owner)
            native.start(); owner.start()
            deadline = time.monotonic() + 15
            while not progress.read_record(self.checkpoint).get('pending_input'):
                if not owner.is_alive(): break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            pending = progress.read_record(self.checkpoint)['pending_input']
            self.assertTrue(owner.is_alive())
            self.assertEqual(runner.resume(self.checkpoint,'choice A',decision_id=pending['decision_id']),0)
            owner.join(15); native.join(15)
        self.assertFalse(owner.is_alive())
        self.assertEqual(native_errors, [])
        self.assertEqual(len(errors),1)
        self.assertIn('invalid needs_input_kind',str(errors[0]))
        self.assertEqual(self.state()['decisions'][pending['decision_id']]['answer'],'choice A')
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(e['event'] == 'spawn' for e in events),1)
        self.assertEqual([e['params']['threadId'] for e in events if e.get('method') == 'turn/start'],['carrier','carrier'])

    def test_public_start_runs_real_c_b_git_chain_and_completed_resume_is_ack(self):
        executable, log, confirmed = self.fixture()
        executable.write_text(CHAIN_HOST)
        errors = []
        def native_adapter():
            try:
                for number in (2,3,4):
                    stage = 'stage'+str(number)
                    deadline = time.monotonic()+45
                    while True:
                        calls = host_calls(log)
                        turns = [call for call in calls if call.get('method') == 'turn/start' and
                                 json.loads(call['params']['input'][0]['text'].rsplit('\n',1)[1])['stage'] == stage]
                        if turns: break
                        if time.monotonic()>deadline: raise RuntimeError('original stage turn not received')
                        time.sleep(.01)
                    thread = turns[0]['params']['threadId']
                    self.settings['thread_id'] = thread
                    self.request['host'].update(thread_id=thread,role='scripted-carrier',source_ref='runner')
                    if number == 2:
                        self.input = self.input_for(2)
                    response = self.finish_next(number,'continuous')
                    self.assertEqual(response['status'],'accepted',response)
                    publish_host_fixture(Path(str(log)+'.snapshot-'+thread), self.state()['lifecycle_snapshot'])
                    publish_host_fixture(Path(str(log)+'.turn-'+str(number-1)), response['completion']['stage_result'])
            except BaseException as error:
                errors.append(error)
                for number in (2,3,4):
                    publish_host_fixture(Path(str(log)+'.turn-'+str(number-1)), {})
        worker = threading.Thread(target=native_adapter)
        with patch.object(runner,'__file__',str(cli.SCRIPT)), patch.dict(os.environ,{'CODEX_BIN':str(executable),'HOST_LOG':str(log)}), redirect_stdout(io.StringIO()):
            worker.start()
            try:
                self.assertEqual(runner.start(confirmed),0)
            except Exception as error:
                diagnostics = self.checkpoint.with_name(self.checkpoint.name + '.host.stderr')
                stderr = diagnostics.read_text() if diagnostics.exists() else 'host stderr unavailable'
                raise AssertionError(f'fixture adapter errors: {errors!r}; host stderr: {stderr}') from error
            finally:
                worker.join(50)
            before = log.read_bytes()
            self.assertEqual(runner.resume(self.checkpoint),0)
            self.assertEqual(log.read_bytes(),before)
        self.assertEqual(errors,[])
        saved = progress.read_record(self.checkpoint)
        self.assertEqual(saved['status'],'completed')
        self.assertEqual(set(saved['stage_results']),{'stage2','stage3','stage4'})
        self.assertFalse(self.flow.exists())
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(event['event'] == 'spawn' for event in events),1)
        self.assertEqual(sum(event.get('method') == 'turn/start' for event in events),3)
        self.assertEqual(len(self.git('log','--merges','--format=%H').splitlines()),2)

    def test_real_snapshot_to_c_cannot_omit_archived_active_descendant(self):
        executable, log, confirmed = self.fixture()
        executable.write_text(HOST.replace("else: raise RuntimeError(method)", """elif method == 'thread/list':
        response = {'data':([{'id':'archived-grandchild','status':{'type':'active'}}] if p.get('archived') else [{'id':'native:designer','status':{'type':'idle'}}]),'nextCursor':None}
    else: raise RuntimeError(method)"""))
        self.begin('continuous')
        self.invoke('observe',self.observation())
        self.invoke('pause')
        host = runner.foreground_host.ForegroundHost(json.loads(confirmed.read_text())['host'],str(self.flow),lambda event:None,
                                                     executable=str(executable))
        try:
            with patch.dict(os.environ,{'HOST_LOG':str(log)}):
                host.start(self.checkpoint.parent/'archive-host.stderr')
                thread = host.start_carrier('stage2',{'model':'fixture-model','reasoning_effort':'high'})
                saved = progress.read_record(self.checkpoint)
                saved['sessions'] = {'stage2':thread}
                saved['transport'] = {'instance':host.instance,'state':'live','native_event_sequence':0}
                progress.atomic_save(self.checkpoint,saved)
                observation = self.observation('stopped','result')
                observation['lifecycle'] = host.snapshot_lifecycle('stage2')
                result = self.invoke('observe',observation)
                self.assertEqual(result['status'],'pausing')
                self.assertFalse(self.state()['stopped'])
        finally:
            host.close()

    def test_live_pause_answer_resume_cancel_uses_original_owner_and_c_barrier(self):
        executable, log, confirmed = self.fixture()
        executable.write_text(CHAIN_HOST)
        errors, owner_results, continuations = [], [], []
        aborting = threading.Event()
        processes = []
        popen = subprocess.Popen
        def spawn(command,*args,**kwargs):
            process = popen(command,*args,**kwargs)
            if command[0] == str(executable) and '--version' not in command:
                processes.append(process)
            return process
        def native_adapter():
            try:
                for turn in range(1,6):
                    deadline = time.monotonic()+35
                    while True:
                        if aborting.is_set(): return
                        calls = host_calls(log)
                        turns = [call for call in calls if call.get('method') == 'turn/start']
                        if len(turns) >= turn: break
                        if time.monotonic()>deadline: raise RuntimeError('control turn missing')
                        time.sleep(.01)
                    thread = turns[turn-1]['params']['threadId']
                    if turn == 1:
                        self.settings['thread_id'] = thread
                        self.input['entry']['host'].update(thread_id=thread,role='scripted-carrier',source_ref='runner')
                        self.input['expected_entry'] = scenario.transfer.entry.resolve(self.input['entry'])
                        self.begin('continuous')
                        self.invoke('observe',self.observation())
                        self.invoke('observe',self.observation('idle','result',{'delivery_id':'choose','status':'needs_input',
                            'payload':{'question':'choose behavior'}}))
                    elif turn == 2:
                        self.invoke('pause')
                        self.assertEqual(self.invoke('observe',self.observation('stopped','result'))['status'],'paused')
                    elif turn == 3:
                        decision = progress.read_record(self.checkpoint)['controller_decision']
                        self.assertEqual(self.invoke('decide',decision)['status'],'paused')
                    elif turn == 4:
                        self.assertEqual(self.invoke('resume')['next_action']['operation'],'inspect-host-state')
                        action = self.invoke('observe',self.observation('idle','result'))['next_action']
                        self.assertEqual(action['operation'],'continue-host')
                        continuations.append(action)
                        self.invoke('observe',self.observation('running','result'))
                    else:
                        self.invoke('cancel')
                        self.invoke('observe',self.observation('stopped','result'))
                        self.assertEqual(self.invoke('observe',self.observation('stopped','result'))['status'],'cancelled')
                    snapshot = self.observation('stopped','result')['lifecycle']
                    publish_host_fixture(Path(str(log)+'.snapshot-'+thread), snapshot)
                    needs_input = turn in {1,4}
                    result = {'result':'needs_input' if needs_input else 'continue','artifacts':[],'evidence':[],
                        'handoff_json':'{}','question':('choose behavior' if turn == 1 else 'await cancel') if needs_input else '',
                        'message':'' if needs_input else 'control applied','needs_input_kind':'user_decision' if needs_input else 'none'}
                    publish_host_fixture(Path(str(log)+'.turn-'+str(turn)), result)
            except BaseException as error:
                errors.append(error)
                for turn in range(1,6): publish_host_fixture(Path(str(log)+'.turn-'+str(turn)), {})
        def owner():
            try: owner_results.append(runner.start(confirmed))
            except BaseException as error: errors.append(error)
        def wait_for(predicate):
            deadline = time.monotonic()+35
            while not predicate(progress.read_record(self.checkpoint)):
                if errors: raise AssertionError(str(errors))
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.02)
        with patch.object(runner,'__file__',str(cli.SCRIPT)), patch.dict(os.environ,{'CODEX_BIN':str(executable),'HOST_LOG':str(log)}), patch.object(subprocess,'Popen',side_effect=spawn), redirect_stdout(io.StringIO()):
            worker, owner_thread = threading.Thread(target=native_adapter), threading.Thread(target=owner)
            worker.start(); owner_thread.start()
            try:
                wait_for(lambda value:value.get('status') == 'needs_input')
                pending = progress.read_record(self.checkpoint)['pending_input']
                runner.request_control(self.checkpoint,'pause')
                wait_for(lambda value:value.get('status') == 'paused')
                self.assertTrue(owner_thread.is_alive())
                runner.resume(self.checkpoint,'choice A',decision_id=pending['decision_id'])
                wait_for(lambda value:pending['decision_id'] in value.get(progress.KEY,{}).get('deferred_decisions',{}))
                self.assertEqual(self.state()['status'],'paused')
                runner.resume(self.checkpoint)
                wait_for(lambda value:value.get('status') == 'needs_input' and value.get('pending_input',{}).get('question') == 'await cancel')
                runner.request_control(self.checkpoint,'cancel')
                owner_thread.join(35)
            finally:
                aborting.set()
                if owner_thread.is_alive():
                    for process in processes:
                        if process.poll() is None: process.terminate()
                worker.join(5)
                owner_thread.join(5)
        self.assertEqual(errors,[])
        self.assertEqual(owner_results,[0])
        self.assertEqual(progress.read_record(self.checkpoint)['status'],'cancelled')
        self.assertEqual([action['payload']['ref'] for action in continuations],['native:designer'])
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(event['event'] == 'spawn' for event in events),1)
        self.assertEqual(sum(event.get('method') == 'turn/start' for event in events),5)
        self.assertTrue(self.flow.exists())
        before = self.checkpoint.read_bytes(),log.read_bytes()
        with patch.object(runner,'__file__',str(cli.SCRIPT)), redirect_stdout(io.StringIO()):
            self.assertEqual(runner.resume(self.checkpoint),0)
        self.assertEqual((self.checkpoint.read_bytes(),log.read_bytes()),before)

    def test_real_lookup_failure_revokes_c_stop_before_runner_can_finish(self):
        executable, log, confirmed = self.fixture()
        executable.write_text(HOST.replace("else: raise RuntimeError(method)", """elif method == 'thread/list':
        if p.get('archived'):
            emit({'id':request['id'],'error':{'code':-32603,'message':'archive query failed'}})
            continue
        response = {'data':[{'id':'native:designer','status':{'type':'idle'}}],'nextCursor':None}
    else: raise RuntimeError(method)"""))
        self.begin('continuous')
        self.invoke('observe',self.observation())
        self.invoke('pause')
        host = runner.foreground_host.ForegroundHost(json.loads(confirmed.read_text())['host'],str(self.flow),lambda event:None,
                                                     executable=str(executable))
        try:
            with patch.dict(os.environ,{'HOST_LOG':str(log)}):
                host.start(self.checkpoint.parent/'failed-lookup.stderr')
                thread = host.start_carrier('stage2',{'model':'fixture-model','reasoning_effort':'high'})
                saved = progress.read_record(self.checkpoint)
                saved.update(current_stage='stage2',sessions={'stage2':thread},
                    transport={'instance':host.instance,'state':'live','native_event_sequence':0})
                progress.atomic_save(self.checkpoint,saved)
                self.assertEqual(self.invoke('observe',self.observation('stopped','result'))['status'],'paused')
                runner._live_progress(progress.read_record(self.checkpoint),self.checkpoint,host,set())
                self.assertEqual(self.state()['status'],'pausing')
                self.assertFalse(self.state()['stopped'])
                self.assertIn('archive query failed',progress.read_record(self.checkpoint)['transport']['lookup_blocked'])
        finally:
            host.close()


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

    def test_server_request_retains_exact_identity_and_requires_explicit_response(self):
        script = HOST.replace("if method == 'turn/start':\n        emit", """if method == 'turn/start':
        emit({'id':'approval-1','method':'item/commandExecution/requestApproval','params':{'threadId':'carrier','turnId':str(turn),'command':['do-not-execute']}})
        reply = json.loads(sys.stdin.readline())
        note({'event':'server-response','message':reply})
        if reply != {'id':'approval-1','result':{'decision':'decline'}}: raise RuntimeError('unapproved response')
        emit""")
        self.executable.write_text(script)
        self.host.start(self.root / 'stderr')
        thread = self.host.start_carrier('stage2',self.settings)
        decisions = []
        def controller(event):
            if self.host.server_requests:
                request = self.host.server_requests['approval-1']
                self.assertEqual(request['params']['command'],['do-not-execute'])
                with self.assertRaisesRegex(runner.foreground_host.HostError,'stale_decision'):
                    self.host.answer_request({**request,'id':'another-approval'},{'decision':'decline'})
                self.host.answer_request(request,{'decision':'decline'})
                decisions.append(request)
        self.host.run_turn(thread,'approval test',self.settings,{},controller)
        self.assertEqual(len(decisions),1)
        self.assertEqual(self.host.server_requests,{})
        responses = [json.loads(line) for line in self.log.read_text().splitlines() if json.loads(line)['event'] == 'server-response']
        self.assertEqual(responses,[{'event':'server-response','message':{'id':'approval-1','result':{'decision':'decline'}}}])

    def test_stage_snapshot_does_not_reassign_previous_carrier_children(self):
        script = HOST.replace("thread={'id':'carrier'}", "thread={'id':p['model']}")
        script = script.replace("'threadId':'carrier'", "'threadId':p.get('threadId','carrier')")
        script = script.replace("else: raise RuntimeError(method)", """elif method == 'thread/list':
        response = {'data':([] if p['archived'] else [{'id':'child-'+p['ancestorThreadId'],'status':{'type':'idle'}}]),'nextCursor':None}
    else: raise RuntimeError(method)""")
        script = script.replace("if method == 'turn/start':\n        emit", """if method == 'turn/start':
        item = {'type':'subAgentActivity','id':'create-'+str(turn),'kind':'started','agentThreadId':'child-'+p['threadId'],'agentPath':'/root/child-'+p['threadId']}
        emit({'method':'item/started','params':{'threadId':p['threadId'],'turnId':str(turn),'item':item}})
        emit({'method':'item/completed','params':{'threadId':p['threadId'],'turnId':str(turn),'item':item}})
        emit""")
        self.executable.write_text(script)
        self.host.start(self.root / 'stderr')
        for stage in ('stage2','stage3'):
            settings = {**self.settings,'model':stage}
            thread = self.host.start_carrier(stage,settings)
            self.host.run_turn(thread,stage,settings,{})
        snapshot = self.host.snapshot_lifecycle('stage3')
        self.assertEqual(snapshot['sequence'],4)
        self.assertEqual({event['params']['threadId'] for event in snapshot['events']},{'stage3'})
        self.assertEqual(snapshot['pages'][0]['request']['params']['ancestorThreadId'],'stage3')

    def test_snapshot_includes_archived_descendants(self):
        script = HOST.replace("else: raise RuntimeError(method)", """elif method == 'thread/list':
        response = {'data':([{'id':'archived-child','status':{'type':'active'}}] if p.get('archived') else []),'nextCursor':None}
    else: raise RuntimeError(method)""")
        self.executable.write_text(script)
        self.host.start(self.root / 'stderr')
        self.host.start_carrier('stage2',self.settings)
        snapshot = self.host.snapshot_lifecycle('stage2')
        self.assertEqual([page['request']['params'].get('archived') for page in snapshot['pages']],[False,True])
        self.assertEqual([thread['id'] for page in snapshot['pages'] for thread in page['response']['data']],['archived-child'])

    def test_each_archive_partition_requires_its_own_complete_pagination(self):
        script = HOST.replace("else: raise RuntimeError(method)", """elif method == 'thread/list':
        response = {'data':[],'nextCursor':None if p.get('cursor') else 'second'}
    else: raise RuntimeError(method)""")
        self.executable.write_text(script)
        self.host.start(self.root / 'stderr')
        self.host.start_carrier('stage2',self.settings)
        snapshot = self.host.snapshot_lifecycle('stage2')
        self.assertEqual([(page['request']['params']['archived'],page['request']['params'].get('cursor')) for page in snapshot['pages']],
                         [(False,None),(False,'second'),(True,None),(True,'second')])

    def test_archived_lookup_failure_cannot_return_partial_snapshot(self):
        script = HOST.replace("else: raise RuntimeError(method)", """elif method == 'thread/list':
        if p.get('archived'):
            emit({'id':request['id'],'error':{'code':-32603,'message':'lookup unavailable'}})
            continue
        response = {'data':[],'nextCursor':None}
    else: raise RuntimeError(method)""")
        self.executable.write_text(script)
        self.host.start(self.root / 'stderr')
        self.host.start_carrier('stage2',self.settings)
        with self.assertRaisesRegex(runner.foreground_host.HostRpcError,'lookup unavailable'):
            self.host.snapshot_lifecycle('stage2')


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(ForegroundLifecycleTests(name) for name in loader.getTestCaseNames(ForegroundLifecycleTests) if name in ForegroundLifecycleTests.__dict__)
    suite.addTests(loader.loadTestsFromTestCase(HostProtocolTests))
    return suite
