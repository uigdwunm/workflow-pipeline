"""Read the current Codex account model catalog without trusting a caller subset."""
from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time


class ModelInventoryError(ValueError):
    pass


def available_pairs(executable=None, timeout=10):
    command = [executable or os.environ.get('CODEX_BIN', 'codex'),
               'app-server', '--listen', 'stdio://']
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError as error:
        raise ModelInventoryError('Codex model catalog is unavailable') from error
    buffer = bytearray()
    deadline = time.monotonic() + timeout
    request_id = 0

    def request(method, params):
        nonlocal request_id
        request_id += 1
        process.stdin.write((json.dumps({'id': request_id, 'method': method,
                                         'params': params}) + '\n').encode())
        process.stdin.flush()
        while True:
            end = buffer.find(b'\n')
            if end >= 0:
                line = bytes(buffer[:end])
                del buffer[:end + 1]
                try:
                    response = json.loads(line)
                except (ValueError, UnicodeDecodeError) as error:
                    raise ModelInventoryError('Codex model catalog returned invalid JSON') from error
                if not isinstance(response, dict):
                    raise ModelInventoryError('Codex model catalog returned invalid data')
                if response.get('id') != request_id:
                    continue
                if 'error' in response or not isinstance(response.get('result'), dict):
                    raise ModelInventoryError('Codex model catalog request failed')
                return response['result']
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelInventoryError('Codex model catalog timed out')
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(remaining):
                    raise ModelInventoryError('Codex model catalog timed out')
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise ModelInventoryError('Codex model catalog closed before replying')
            buffer.extend(chunk)
            if len(buffer) > 16 * 1024 * 1024:
                raise ModelInventoryError('Codex model catalog response is too large')

    try:
        request('initialize', {'clientInfo': {'name': 'workflow_pipeline', 'version': '1'},
                               'capabilities': {'experimentalApi': True}})
        process.stdin.write(b'{"method":"initialized"}\n')
        process.stdin.flush()
        pairs, seen_models, seen_cursors = set(), set(), set()
        cursor = None
        for _ in range(32):
            params = {'includeHidden': False, 'limit': 100}
            if cursor is not None:
                params['cursor'] = cursor
            result = request('model/list', params)
            data = result.get('data')
            if not isinstance(data, list):
                raise ModelInventoryError('Codex model catalog has no model list')
            for model in data:
                if not isinstance(model, dict) or not isinstance(model.get('model'), str):
                    raise ModelInventoryError('Codex model catalog has an invalid model')
                name = model['model']
                if name in seen_models:
                    raise ModelInventoryError('Codex model catalog repeated a model')
                seen_models.add(name)
                hidden = model.get('hidden', False)
                if type(hidden) is not bool:
                    raise ModelInventoryError('Codex model catalog has invalid visibility')
                if hidden:
                    continue
                efforts = model.get('supportedReasoningEfforts')
                if not isinstance(efforts, list):
                    raise ModelInventoryError('Codex model catalog lacks reasoning efforts')
                for option in efforts:
                    effort = option.get('reasoningEffort') if isinstance(option, dict) else None
                    if not isinstance(effort, str) or not effort:
                        raise ModelInventoryError('Codex model catalog has an invalid reasoning effort')
                    pairs.add((name, effort))
            cursor = result.get('nextCursor')
            if cursor is None:
                if not pairs:
                    raise ModelInventoryError('Codex model catalog is empty')
                return frozenset(pairs)
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise ModelInventoryError('Codex model catalog pagination is invalid')
            seen_cursors.add(cursor)
        raise ModelInventoryError('Codex model catalog has too many pages')
    except (BrokenPipeError, OSError) as error:
        raise ModelInventoryError('Codex model catalog transport failed') from error
    finally:
        for stream in (process.stdin, process.stdout):
            try:
                stream.close()
            except OSError:
                pass
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
