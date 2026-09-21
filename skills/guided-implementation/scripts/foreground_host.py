"""One foreground app-server connection; no business or native-role authority."""
from __future__ import annotations

from collections import deque
from contextlib import nullcontext
import copy
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
import uuid

PROTOCOL = 'foreground-host-v1'
MAX_MESSAGE = 16 * 1024 * 1024


class HostError(RuntimeError):
    pass


class HostRpcError(HostError):
    """An explicit response error, distinct from an unknown transport outcome."""


class HostPrelaunchError(HostError):
    """CLI capability check failed before any app-server process was launched."""


def _rpc_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate RPC field')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('non-JSON constant: ' + value)


def validate_configuration(value, controller, repository, worktree, git_common_dir):
    if not isinstance(value, dict) or set(value) != {'transport', 'cli_version', 'source', 'thread', 'effective'}:
        raise HostError('host_configuration_required: freeze the effective Controller configuration')
    source = value['source']
    if (value['transport'] != 'app-server-stdio' or not isinstance(value['cli_version'], str) or
            not value['cli_version'] or not isinstance(source, dict) or
            source.get('kind') != 'controller-current-config' or source.get('controller_ref') != controller or
            not isinstance(source.get('receipt'), str) or not source['receipt']):
        raise HostError('host_configuration_required: current Controller provenance is required')
    params, expected = value['thread'], value['effective']
    allowed = {'approvalPolicy', 'approvalsReviewer', 'sandbox', 'permissions', 'config', 'runtimeWorkspaceRoots', 'modelProvider'}
    if (not isinstance(params, dict) or set(params) - allowed or
            not {'approvalPolicy', 'config', 'runtimeWorkspaceRoots'} <= set(params) or
            ('sandbox' in params) == ('permissions' in params) or not isinstance(params['config'], dict)):
        raise HostError('unsupported_host_configuration: retain exactly one permission mechanism')
    if 'permissions' in params:
        raise HostError('unsupported_host_configuration: this CLI readback exposes named-profile identity, not its complete rules; retain the Controller selection and stop before business work')
    roots = params['runtimeWorkspaceRoots']
    if (not isinstance(roots, list) or not all(isinstance(p, str) and Path(p).is_absolute() for p in roots) or
            not {repository, worktree, git_common_dir} <= set(roots)):
        raise HostError('host_configuration_required: confirmed project, worktree and Git roots required')
    if (not isinstance(expected, dict) or not {'approvalPolicy', 'sandbox', 'runtimeWorkspaceRoots'} <= set(expected) or
            set(expected) - {'approvalPolicy', 'approvalsReviewer', 'sandbox', 'runtimeWorkspaceRoots', 'activePermissionProfile', 'modelProvider'} or
            expected['approvalPolicy'] != params['approvalPolicy'] or expected['runtimeWorkspaceRoots'] != roots):
        raise HostError('host_configuration_required: complete effective permissions readback required')
    return copy.deepcopy(value)


class ForegroundHost:
    """Byte transport seam. Every request is durable before its first write.

    Native notifications retain their original attribution, including old parent
    turns. Only the exact current carrier thread and turn can complete run_turn.
    """
    def __init__(self, configuration, cwd, journal, *, executable=None):
        self.configuration = configuration
        self.cwd = cwd
        self.journal = journal
        self.executable = executable or os.environ.get('CODEX_BIN', 'codex')
        self.instance = str(uuid.uuid4())
        self.process = None
        self.selector = selectors.DefaultSelector()
        self.buffer = b''
        self.messages = deque()
        self.notifications = deque()
        self.request_number = 0
        self.responses = set()
        self.threads = {}
        self.active = None
        self.turns = set()
        self.stderr = None
        self.server_requests = {}
        self.lifecycle_events = []

    def start(self, diagnostics):
        try:
            actual = subprocess.run([self.executable, '--version'], text=True, capture_output=True, timeout=10, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise HostPrelaunchError('host_cli_unavailable: version check failed before host launch') from error
        if actual != self.configuration['cli_version']:
            raise HostPrelaunchError('host_version_changed: revalidate the confirmed CLI schema/configuration')
        self.journal({'kind':'host-intent', 'instance':self.instance, 'protocol':PROTOCOL, 'cli_version':actual})
        self.stderr = open(diagnostics, 'ab')
        self.process = subprocess.Popen([self.executable, 'app-server', '--listen', 'stdio://'], cwd=self.cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, start_new_session=True)
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.journal({'kind':'host-started', 'instance':self.instance, 'pid':self.process.pid})
        self._request('initialize', {'clientInfo':{'name':'workflow_pipeline','version':'1'},
                                    'capabilities':{'experimentalApi':True}})
        self._write({'method':'initialized'})

    def _write(self, message):
        try:
            self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode())
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise HostError('transport_uncertain: request must not be replayed') from error

    def _read(self, timeout=1):
        if not 0 <= timeout <= 60:
            raise HostError('invalid_wait: foreground reads must be bounded to 60 seconds')
        if self.messages:
            return self.messages.popleft()
        if not self.selector.select(timeout):
            return None
        chunk = os.read(self.process.stdout.fileno(), 65536)
        if not chunk:
            raise HostError('transport_lost: app-server EOF; preserve original identities and calls')
        self.buffer += chunk
        if len(self.buffer) > MAX_MESSAGE:
            raise HostError('host_protocol_error: oversized message')
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                message = json.loads(line, object_pairs_hook=_rpc_object, parse_constant=_invalid_constant)
            except (ValueError, UnicodeError) as error:
                raise HostError('host_protocol_error: invalid JSON') from error
            if not isinstance(message, dict):
                raise HostError('host_protocol_error: expected RPC object')
            if 'id' in message and (type(message['id']) not in {str,int}):
                raise HostError('host_protocol_error: invalid RPC identity')
            if 'method' in message:
                if (not isinstance(message['method'],str) or not message['method'] or
                        not isinstance(message.get('params',{}),dict) or 'result' in message or 'error' in message):
                    raise HostError('host_protocol_error: invalid notification or server request')
                params = message.get('params',{})
                if 'item' in params and not isinstance(params['item'],dict):
                    raise HostError('host_protocol_error: invalid item payload')
                if 'turn' in params and not isinstance(params['turn'],dict):
                    raise HostError('host_protocol_error: invalid turn payload')
            elif 'id' not in message:
                raise HostError('host_protocol_error: response identity missing')
            self.journal({'kind':'message', 'instance':self.instance, 'message':message})
            if message.get('params', {}).get('item', {}).get('type') == 'subAgentActivity':
                self.lifecycle_events.append(copy.deepcopy(message))
            self.messages.append(message)
        return self.messages.popleft() if self.messages else None

    def _request(self, method, params):
        self.request_number += 1
        request = {'id':self.request_number,'method':method,'params':params}
        self.journal({'kind':'request-intent','instance':self.instance,'request':request})
        self._write(request)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            message = self._read(min(1, max(0, deadline-time.monotonic())))
            if message is None:
                continue
            if 'method' in message:
                self._notification(message)
                continue
            if message.get('id') != request['id'] or message['id'] in self.responses:
                raise HostError('host_protocol_error: unexpected or duplicate RPC response')
            self.responses.add(message['id'])
            if ('error' in message) == ('result' in message):
                raise HostError('host_protocol_error: result/error must be exclusive')
            if 'error' in message:
                self.journal({'kind':'request-result','instance':self.instance,'request_id':request['id'],'outcome':'error'})
                raise HostRpcError('host_rpc_error: ' + method + ': ' + json.dumps(message['error']))
            if not isinstance(message['result'],dict):
                raise HostError('host_protocol_error: method result must be an object')
            self.journal({'kind':'request-result','instance':self.instance,'request_id':request['id'],'outcome':'success'})
            return message['result']
        raise HostError('transport_uncertain: RPC response deadline expired; do not replay')

    def _notification(self, message):
        if 'id' in message:
            previous = self.server_requests.get(message['id'])
            if previous is not None and previous != message:
                raise HostError('host_protocol_error: conflicting server request ID')
            self.server_requests[message['id']] = message
            self.journal({'kind':'server-request','instance':self.instance,'request':message})
        else:
            self.notifications.append(message)

    def poll(self, timeout=1):
        """Read bounded foreground progress without creating a thread or turn."""
        if self.notifications:
            return self.notifications.popleft()
        message = self._read(timeout)
        if message is not None:
            if 'method' not in message:
                raise HostError('host_protocol_error: unsolicited RPC response')
            if 'id' in message:
                self._notification(message)
        return message

    def steer_control(self, checkpoint):
        if self.active is None:
            return False
        thread, turn = self.active
        try:
            result = self._request('turn/steer', {'threadId':thread, 'expectedTurnId':turn,
                'input':[{'type':'text','text':f'Controller control input is saved at {checkpoint}. Read it now and apply C stop/decision priority in this original carrier. Preserve original refs and unresolved calls; do not dispatch replacement work.'}]})
        except HostRpcError:
            if any(message.get('method') == 'turn/completed' and message.get('params',{}).get('threadId') == thread and
                   message['params'].get('turn',{}).get('id') == turn for message in [*self.notifications,*self.messages]):
                return False  # The queued command remains for the next carrier boundary.
            raise
        if result.get('turnId') != turn:
            raise HostError('host_protocol_error: steering receipt changed the active turn')
        return True

    def answer_request(self, request, result, *, send_guard=None):
        if self.server_requests.get(request.get('id')) != request:
            raise HostError('stale_decision: original server request is not pending in this host')
        self.journal({'kind':'server-response-intent','instance':self.instance,'request':request,'result':result})
        with send_guard() if send_guard is not None else nullcontext(True) as allowed:
            if not allowed:
                return False
            self._write({'id':request['id'],'result':result})
        del self.server_requests[request['id']]
        self.journal({'kind':'server-response-sent','instance':self.instance,'request_id':request['id']})
        return True

    def snapshot_lifecycle(self, stage):
        """Current descendants of this run's original carrier, never discovery."""
        if stage not in self.threads:
            raise HostError('host_identity_missing: no original carrier in this connection')
        pages = []
        for archived in (False, True):
            cursor, cursors = None, set()
            for _ in range(100):
                params = {'ancestorThreadId':self.threads[stage], 'limit':100, 'archived':archived, 'modelProviders':[],
                          'sourceKinds':['cli','vscode','exec','appServer','subAgent','subAgentReview',
                                         'subAgentCompact','subAgentThreadSpawn','subAgentOther','unknown']}
                if cursor is not None:
                    params['cursor'] = cursor
                result = self._request('thread/list', params)
                if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                    raise HostError('host_protocol_error: malformed descendant lookup')
                pages.append({'request':{'id':self.request_number,'method':'thread/list','params':params}, 'response':result})
                cursor = result.get('nextCursor')
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise HostError('host_protocol_error: invalid descendant cursor')
                cursors.add(cursor)
            else:
                raise HostError('host_lookup_incomplete: descendant enumeration exceeded its bound')
        return {'instance':self.instance, 'carrier_thread':self.threads[stage],
                'sequence':len(self.lifecycle_events), 'pages':pages, 'events':self.scoped_events(stage)}

    def scoped_events(self, stage, extra=None):
        events = [*self.lifecycle_events, *([extra] if extra is not None else [])]
        parents = {self.threads.get(stage)}
        while True:
            expanded = parents | {event['params']['item'].get('agentThreadId') for event in events
                if event.get('params',{}).get('threadId') in parents}
            expanded.discard(None)
            if expanded == parents:
                break
            parents = expanded
        return [copy.deepcopy(event) for event in events if event.get('params',{}).get('threadId') in parents]

    def start_carrier(self, stage, settings):
        if stage in self.threads:
            return self.threads[stage]
        params = copy.deepcopy(self.configuration['thread'])
        params.update(cwd=self.cwd, model=settings['model'], allowProviderModelFallback=False)
        params['config']['model_reasoning_effort'] = settings['reasoning_effort']
        result = self._request('thread/start', params)
        expected = {**self.configuration['effective'], 'cwd':self.cwd, 'model':settings['model'],
                    'reasoningEffort':settings['reasoning_effort']}
        if not isinstance(result, dict) or any(result.get(k) != v for k,v in expected.items()):
            raise HostError('host_configuration_changed: actual thread differs before first business turn')
        if not isinstance(result.get('thread'),dict):
            raise HostError('host_protocol_error: missing carrier thread object')
        thread = result['thread'].get('id')
        if not isinstance(thread, str) or not thread:
            raise HostError('host_protocol_error: missing carrier thread identity')
        self.threads[stage] = thread
        return thread

    def run_turn(self, thread, prompt, settings, schema, on_progress=None):
        if self.active is not None:
            raise HostError('host_protocol_error: carrier turn already active')
        result = self._request('turn/start', {'threadId':thread, 'input':[{'type':'text','text':prompt}],
            'effort':settings['reasoning_effort'], 'outputSchema':schema})
        if not isinstance(result.get('turn'),dict):
            raise HostError('host_protocol_error: missing carrier turn object')
        turn = result['turn'].get('id')
        if not isinstance(turn, str) or not turn:
            raise HostError('host_protocol_error: missing turn identity')
        if (thread,turn) in self.turns:
            raise HostError('host_protocol_error: duplicate carrier turn identity')
        self.turns.add((thread,turn))
        self.active = (thread, turn)
        self.journal({'kind':'turn-bound','instance':self.instance,'thread':thread,'turn':turn})
        final = None
        while True:
            if self.notifications:
                message = self.notifications.popleft()
            else:
                message = self._read(1)
                if message is None:
                    if on_progress:
                        on_progress(None)
                    continue
                if 'method' not in message:
                    raise HostError('host_protocol_error: unsolicited response outside RPC')
                if 'id' in message:
                    self._notification(message)
            if on_progress:
                on_progress(message)
            params = message.get('params', {})
            if params.get('threadId') != thread:
                continue
            if message['method'] == 'item/completed' and params.get('turnId') == turn:
                item = params.get('item', {})
                if item.get('type') == 'agentMessage' and item.get('phase') in {None, 'final_answer'}:
                    if final is not None and final != item.get('text'):
                        raise HostError('host_protocol_error: conflicting final carrier messages')
                    final = item.get('text')
            if message['method'] == 'turn/completed' and params.get('turn', {}).get('id') == turn:
                self.active = None
                if params['turn'].get('status') != 'completed' or not isinstance(final, str):
                    raise HostError('carrier_turn_failed: matching final message and successful turn required')
                return final, turn

    def close(self):
        """Bounded local resource cleanup, explicitly not native stopped proof."""
        if self.process is None:
            self.selector.close()
            if self.stderr is not None:
                self.stderr.close()
            return
        try:
            try:
                self.process.stdin.close()
            except OSError:
                pass  # Broken pipe is already a lost transport, not stop proof.
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            # The leader may exit before a local descendant. Clean this exact
            # private process group even when the leader has already exited.
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=3)
        finally:
            self.selector.close()
            self.process.stdout.close()
            self.stderr.close()
        self.journal({'kind':'local-process-closed','instance':self.instance,'returncode':self.process.returncode,
                      'native_stopped':False})
