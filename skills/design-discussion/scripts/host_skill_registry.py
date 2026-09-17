"""Bounded, read-only Codex app-server Skills registry adapter (stdlib only)."""
from __future__ import annotations
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time

TIMEOUT_SECONDS = 15
MAX_OUTPUT_BYTES = 2 * 1024 * 1024


class RegistryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def query_registry(query):
    if (not isinstance(query, dict) or set(query) != {'cwd'}
        or not isinstance(query['cwd'], str) or not Path(query['cwd']).is_absolute()
        or not Path(query['cwd']).is_dir()):
        raise RegistryError('invalid_request', 'registry_query requires one absolute existing directory cwd')
    cwd = query['cwd']
    binary = os.environ.get('CODEX_BIN') or shutil.which('codex')
    if not binary:
        raise RegistryError('registry_unavailable', 'Codex executable unavailable; set CODEX_BIN or add codex to PATH')
    process = None
    try:
        process = subprocess.Popen([binary, 'app-server', '--stdio'], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        pending = bytearray()
        total = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)

            def send(message):
                process.stdin.write(json.dumps(message).encode() + b'\n')
                process.stdin.flush()

            def receive(request_id):
                nonlocal total
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RegistryError('registry_timeout', 'Codex Skills registry query timed out')
                    if b'\n' not in pending:
                        if not selector.select(remaining):
                            raise RegistryError('registry_timeout', 'Codex Skills registry query timed out')
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            raise RegistryError('registry_unavailable', 'Codex app-server closed before registry response')
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            raise RegistryError('registry_output_limit', 'Codex registry output exceeded limit')
                        pending.extend(chunk)
                        continue
                    line, _, rest = pending.partition(b'\n')
                    pending[:] = rest
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError) as exc:
                        raise RegistryError('invalid_registry', 'Codex app-server returned invalid JSON') from exc
                    if not isinstance(message, dict):
                        raise RegistryError('invalid_registry', 'Codex app-server response must be an object')
                    if 'id' not in message and isinstance(message.get('method'), str):
                        continue
                    if type(message.get('id')) is not int or message['id'] != request_id:
                        raise RegistryError('invalid_registry', 'Codex app-server returned an unexpected response id')
                    if 'error' in message:
                        raise RegistryError('registry_rpc_error', 'Codex app-server rejected the registry request')
                    if not isinstance(message.get('result'), dict):
                        raise RegistryError('invalid_registry', 'Codex app-server response has no result object')
                    return message['result']

            send({'id':1, 'method':'initialize', 'params':{'clientInfo':{
                'name':'workflow_skill_preflight', 'version':'1.0.0'}}})
            receive(1)
            send({'method':'initialized'})
            send({'id':2, 'method':'skills/list', 'params':{'cwds':[cwd], 'forceReload':True}})
            result = receive(2)
    except (OSError, ValueError) as exc:
        if isinstance(exc, RegistryError):
            raise
        raise RegistryError('registry_unavailable', 'Could not run Codex app-server for Skills registry lookup') from exc
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            process.stdin.close()
            process.stdout.close()
    data = result.get('data')
    if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
        or data[0].get('cwd') != cwd or not isinstance(data[0].get('skills'), list)
        or not isinstance(data[0].get('errors'), list)):
        raise RegistryError('invalid_registry', 'Skills registry must return exactly the requested cwd, skills and errors')
    if data[0]['errors']:
        raise RegistryError('registry_load_failed', 'Codex reported Skill loading errors for the requested cwd')
    entries = []
    for skill in data[0]['skills']:
        if (not isinstance(skill, dict) or not isinstance(skill.get('name'), str) or not skill['name']
            or not isinstance(skill.get('path'), str) or not Path(skill['path']).is_absolute()
            or type(skill.get('enabled')) is not bool):
            raise RegistryError('invalid_registry', 'Host Skill entry requires name, absolute path and boolean enabled')
        entries.append({'name':skill['name'], 'entry':skill['path'], 'enabled':skill['enabled'],
            'source':'codex-app-server'})
    return {'source':'host-current-skills', 'entries':entries}
