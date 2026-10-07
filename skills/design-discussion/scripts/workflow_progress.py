#!/usr/bin/env python3
"""Durable foreground progression. Host calls remain authenticated adapter work.

The outer checkpoint belongs to the existing conversation/runner. Only the
workflow_progress member is owned here; discussion authority stays in its ledger.
No host tools, daemon, Git publisher or governance-private records live here.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import requirement_prepare as requirement
import requirement_delivery
import stage_handoff as handoff
import stage_dispatch as dispatch
import stage_scope
import execution_plan
import review_evidence
import workflow_control as control
import workflow_control_git as control_git
import skill_preflight
import supervision_protocol as supervision
import workflow_state_store as state_store

PROTOCOL = "workflow-progress-v20"
KEY = "workflow_progress"
CHECKPOINT_LOCK_TIMEOUT = 5.0
REVIEW_INLINE_RAW_LIMIT = 4 * 1024
NATIVE_SOURCE_KINDS = ('cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
                       'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown')
require = entry.require


class _RejectedPlanRevision(entry.PreparationError):
    """A stopped revision was safely rejected before its planning write release."""


def validate_native_event(event):
    require(isinstance(event, dict) and isinstance(event.get('method'), str) and event['method'] in {'item/started','item/completed'} and
            isinstance(event.get('params'), dict) and isinstance(event['params'].get('item'), dict) and
            event['params']['item'].get('type') == 'subAgentActivity',
            'invalid_result', 'raw native lifecycle notification required')
    item = event['params']['item']
    require(all(isinstance(value,str) and value for value in (event['params'].get('threadId'),item.get('id'),item.get('kind'))) and
            all(item.get(key) is None or isinstance(item[key],str) and item[key] for key in ('agentPath','agentThreadId')),
            'invalid_result', 'native lifecycle identities must be strings')


def validate_snapshot_events(snapshot):
    require(isinstance(snapshot,dict) and isinstance(snapshot.get('events'),list),
            'invalid_result', 'raw lifecycle events must be a list')
    for event in snapshot['events']:
        validate_native_event(event)


def validate_collaboration_event(event):
    require(isinstance(event, dict) and event.get('type') == 'subAgentActivity' and
            all(isinstance(event.get(key), str) and event[key]
                for key in ('id', 'kind', 'agentPath', 'agentThreadId')) and
            re.fullmatch(r'/root(?:/[a-z0-9_]+)*', event['agentPath']),
            'invalid_result', 'raw collaboration activity requires its native item and exact identities')


def native_raw_object(raw):
    require(isinstance(raw, dict) and raw and raw.get('isError') is not True,
            'host_evidence_missing', 'retain the authenticated successful raw host response')
    if 'content' in raw:
        require(isinstance(raw['content'], list) and all(isinstance(item, dict) for item in raw['content']),
                'host_evidence_missing', 'raw host content must be a list of content objects')
        texts = [item.get('text') for item in raw['content'] if item.get('type') == 'text']
        require(len(texts) == 1 and isinstance(texts[0], str), 'host_evidence_missing', 'one raw host JSON response required')
        try:
            raw = json.loads(texts[0])
        except (ValueError, TypeError) as error:
            raise entry.PreparationError('host_evidence_missing', 'host response is not JSON evidence') from error
    require(isinstance(raw, dict), 'host_evidence_missing', 'raw host object required')
    return raw


def validate_native_close(close, ref, thread):
    entry.fields(close, {'adapter', 'request'})
    request = close['request']
    require(isinstance(request, dict), 'host_capability_missing', 'discover an actual exact-target close capability')
    if close['adapter'] == 'codex-desktop':
        entry.fields(request, {'tool', 'arguments'})
        arguments = request['arguments']
        require(request['tool'] == 'mcp__codex_app__set_thread_archived' and isinstance(arguments, dict) and
                set(arguments) == {'threadId', 'hostId', 'archived'} and arguments['threadId'] == thread and
                isinstance(arguments['hostId'], str) and arguments['hostId'] and arguments['archived'] is True,
                'identity_mismatch', 'archive the mechanically bound exact desktop thread on its original host')
    else:
        raise entry.PreparationError('host_capability_missing', 'no supported native close capability is available')


def native_receipt_matches(saved, incoming):
    raw = saved.get('receipt', {}).get('raw', {})
    if raw.get('format') != 'native-raw-reference-v1':
        return saved == incoming
    receipt = incoming.get('receipt', {})
    size, digest = state_store.raw_measure(receipt.get('raw'))
    return ({key: value for key, value in saved.items() if key != 'receipt'} ==
            {key: value for key, value in incoming.items() if key != 'receipt'} and
            {key: (raw.get('reported_status', value) if key == 'status' else value)
             for key, value in saved['receipt'].items() if key != 'raw'} ==
            {key: value for key, value in receipt.items() if key != 'raw'} and
            raw['size'] == size and raw['sha256'] == digest)


def host_raw_facts(receipt):
    raw = receipt.get('raw', {})
    return raw.get('facts', {}) if raw.get('format') == 'host-raw-reference-v1' else raw


def host_raw_binding(data, facts, digest):
    return {'kind': 'host-result', 'event_id': data['event_id'], 'event_digest': digest,
            'receipt': {key: value for key, value in data['receipt'].items() if key != 'raw'},
            'action_id': data.get('action_id'), 'provenance': data.get('provenance'),
            'action_resolution': data.get('action_resolution'), 'facts': facts}


def validate_saved_host_raw(state):
    # These are the live permission-bearing copies, not a scan of raw history.
    for observation in (state['host'].get(key) for key in ('observation', 'last_stop', 'proof')):
        if not observation:
            continue
        receipt = observation['receipt']
        raw = receipt.get('raw', {})
        if raw.get('format') != 'host-raw-reference-v1':
            continue
        store = state_store.open_store(state['checkpoint_path'])
        current = store.open_current()
        manifest = store.read_raw_manifest(raw.get('evidence_ref'), run_id=current.run_id)
        binding = manifest.get('native_receipt', {})
        require(isinstance(binding, dict) and isinstance(binding.get('facts'), dict) and
                set(binding['facts']) <= {'resumable', 'dispatch_available'} and
                all(type(value) is bool for value in binding['facts'].values()) and
                isinstance(raw.get('event_digest'), str) and re.fullmatch(r'[0-9a-f]{64}', raw['event_digest']),
                'invalid_checkpoint', 'host raw manifest binding is invalid')
        original = {**observation, 'receipt': {**receipt, 'status': raw.get('reported_status')}}
        complete = manifest['complete'] and manifest['size'] <= state_store.EVIDENCE_LIMIT
        require(raw.get('sha256') == manifest['sha256'] and raw.get('size') == manifest['size'] and
                raw.get('truncated') is (not complete) and
                raw.get('facts') == (binding.get('facts') if complete else {}) and
                binding == host_raw_binding(original, binding.get('facts'), raw.get('event_digest')) and
                receipt['status'] == (raw['reported_status'] if complete else 'unknown'),
                'invalid_checkpoint', 'host raw facts differ from the original receipt manifest')
        require(store.bytes_read <= state_store.READ_LIMIT, 'state_capacity', 'host manifests exceed the current read budget')


def native_result_facts(record, action, *, query=False):
    facts = {'thread_id': record['thread_id'], 'operation': action['operation']}
    if action['operation'] == 'finish-native' or query:
        facts.update(status='notLoaded' if query else 'idle', finish_action_id=record['actions'][0]['action_id'])
    return facts


def native_receipt_binding(receipt, record, action, facts):
    return {'action_id': action['action_id'], 'ref': record['ref'], 'facts': facts,
            **{key: receipt[key] for key in ('adapter', 'call_ref', 'response_ref')},
            'reported_status': receipt['status'], 'query': receipt.get('query', False),
            'query_for': receipt.get('query_for')}


def validate_saved_native_result(receipt, record, action, status=None, *, checkpoint):
    raw = receipt['raw']
    if raw.get('format') == 'native-raw-reference-v1':
        expected = native_result_facts(record, action, query=receipt.get('query', False))
        require(raw.get('facts') == expected and raw.get('truncated') is False and
                raw.get('reported_status') == 'completed' and raw.get('reason') is None and
                type(raw.get('size')) is int and 0 < raw['size'] <= state_store.EVIDENCE_LIMIT,
                'invalid_checkpoint', 'native result projection changed')
        store = state_store.open_store(checkpoint)
        current = store.open_current()
        require(current is not None, 'invalid_checkpoint', 'native result lost its original owner')
        manifest = store.read_raw_manifest(raw.get('evidence_ref'), run_id=current.run_id)
        require(manifest['complete'] and manifest['size'] == raw['size'] and manifest['sha256'] == raw.get('sha256') and
                manifest.get('native_receipt') == native_receipt_binding(receipt, record, action, expected),
                'invalid_checkpoint', 'native result manifest differs from its original validated receipt')
        require(store.bytes_read <= state_store.READ_LIMIT, 'state_capacity', 'native manifests exceed the current read budget')
    elif status is not None:
        validate_native_readback(native_raw_object(raw), record, status)
    else:
        validate_native_close_result(action['payload'], native_raw_object(raw))


def validate_native_close_result(payload, raw):
    if payload['adapter'] == 'codex-desktop':
        require(raw.get('threadId') == payload['thread_id'] and raw.get('archived') is True,
                'host_evidence_missing', 'retain the exact-thread successful archive response')


def validate_native_readback(raw, record, status):
    turns = raw.get('turns')
    require(isinstance(raw.get('thread'), dict) and isinstance(turns, list) and turns and
            isinstance(turns[0], dict) and isinstance(turns[0].get('items'), list) and
            all(isinstance(item, dict) for item in turns[0]['items']),
            'host_evidence_missing', 'current thread, completed turn and final item objects are required')
    require(raw['thread'].get('id') == record['thread_id'] and
            raw['thread'].get('hostId') == record['prepare']['close']['request']['arguments']['hostId'] and
            raw['thread'].get('status') == {'type': status} and turns[0].get('status') == 'completed' and
            any(item.get('type') == 'agentMessage' and item.get('phase') == 'final_answer' and
                isinstance(item.get('text'), str) and
                'NATIVE_FINISHED action_id=' + record['actions'][0]['action_id'] in item['text'] and
                all(marker in item['text'] for marker in ('mailbox=consumed', 'business_result=unchanged', 'commands=stopped'))
                for item in turns[0]['items']),
            'host_evidence_missing', 'current exact-thread readback must retain the correlated original finish turn and state')


def collaboration_inventory(value):
    """Validate the native tool's unmodified whole-tree response, not RPC pages."""
    entry.fields(value, {'adapter', 'controller_ref', 'request', 'response'})
    require(value['adapter'] == 'collaboration' and
            value['request'] == {'tool': 'collaboration.list_agents', 'arguments': {}},
            'host_capability_missing', 'use the unfiltered collaboration.list_agents tool')
    require(isinstance(value['controller_ref'], str) and
            re.fullmatch(r'/root(?:/[a-z0-9_]+)*', value['controller_ref']),
            'identity_mismatch', 'retain the runtime controller agent path')
    response = value['response']
    require(isinstance(response, dict) and set(response) == {'agents'} and isinstance(response['agents'], list),
            'host_capability_missing', 'complete raw collaboration agent inventory required')
    agents = {}
    for agent in response['agents']:
        require(isinstance(agent, dict) and set(agent) == {'agent_name', 'agent_status'} and
                isinstance(agent['agent_name'], str) and re.fullmatch(r'/root(?:/[a-z0-9_]+)*', agent['agent_name']) and
                agent['agent_name'] not in agents and isinstance(agent['agent_status'], (str, dict)),
                'invalid_result', 'native inventory requires unique canonical agent paths and raw statuses')
        agents[agent['agent_name']] = agent['agent_status']
    require(value['controller_ref'] in agents, 'identity_mismatch', 'inventory must include the current controller')
    return agents


def collaboration_inventory_pending(inventory, expected, closed=(), stopped_missing=()):
    agents = collaboration_inventory(inventory)
    actual = set(agents) - {inventory['controller_ref']}
    pending = []
    remaining = expected - set(closed)
    if actual - remaining or remaining - actual - set(stopped_missing):
        pending.append({'kind': 'unreconciled-descendants', 'expected': sorted(remaining), 'actual': sorted(actual)})
    for ref in sorted(actual):
        status = agents[ref]
        if not (isinstance(status, dict) and set(status) == {'completed'} and isinstance(status['completed'], str)):
            pending.append({'kind': 'native-turn-not-stopped', 'ref': ref, 'status': status})
    return pending


def baseline_thread_identity(state, data):
    """Bind an admitted direct baseline child without giving it a workflow role."""
    entry.fields(data, {'ref', 'binding'})
    host = state.get('native_host')
    require(isinstance(host, dict) and host.get('adapter') == 'collaboration',
            'host_capability_missing', 'baseline lookup requires the original admitted collaboration host')
    require(data['ref'] in host['baseline_refs'] and
            data['ref'].rsplit('/', 1)[0] == host['controller_ref'] and
            not any(ref.startswith(data['ref'] + '/') for ref in host['baseline_refs']),
            'identity_mismatch', 'exact-thread baseline lookup requires an admitted direct leaf identity')
    binding = data['binding']
    entry.fields(binding, {'call_ref', 'response_ref', 'raw'})
    entry.nonempty(binding['call_ref']); entry.nonempty(binding['response_ref'])
    raw = native_raw_object(binding['raw'])
    require(isinstance(raw.get('thread'), dict) and raw['thread'].get('id') == host['instance'] and
            isinstance(raw['thread'].get('hostId'), str) and raw['thread']['hostId'] and
            isinstance(raw.get('turns'), list) and all(isinstance(turn, dict) and
                isinstance(turn.get('items'), list) and all(isinstance(item, dict) for item in turn['items'])
                for turn in raw['turns']),
            'identity_mismatch', 'retain the original Controller thread readback binding the baseline identity')
    events = [item for turn in raw['turns'] for item in turn['items']
              if item.get('type') == 'subAgentActivity' and item.get('agentPath') == data['ref']]
    require(all(isinstance(item.get('agentThreadId'), str) and item['agentThreadId'] for item in events),
            'identity_mismatch', 'baseline native event thread identities must be nonempty strings')
    identities = {item['agentThreadId'] for item in events}
    require(len(identities) == 1 and all(isinstance(identity, str) and identity for identity in identities),
            'identity_mismatch', 'the parent must bind one exact native thread to the missing baseline ref')
    return next(iter(identities)), raw['thread']['hostId']


def validate_baseline_thread_read(query, data):
    entry.fields(data, {'query_id', 'call_ref', 'response_ref', 'raw'})
    for key in ('query_id', 'call_ref', 'response_ref'): entry.nonempty(data[key])
    require(data['query_id'] == query['query_id'], 'host_provenance_conflict', 'retain the issued baseline lookup')
    raw = native_raw_object(data['raw'])
    turns, thread = raw.get('turns'), raw.get('thread')
    require(isinstance(thread, dict) and thread.get('id') == query['arguments']['threadId'] and
            thread.get('hostId') == query['arguments']['hostId'] and
            thread.get('status') in ({'type': 'idle'}, {'type': 'notLoaded'}) and
            isinstance(raw.get('page'), dict) and raw['page'].get('order') == 'newest_first' and
            isinstance(turns, list) and len(turns) == 1 and isinstance(turns[0], dict) and
            isinstance(turns[0].get('id'), str) and turns[0]['id'] and turns[0].get('status') == 'completed' and
            isinstance(turns[0].get('items'), list) and all(isinstance(item, dict) for item in turns[0]['items']) and
            any(item.get('type') == 'agentMessage' and item.get('phase') == 'final_answer' and
                isinstance(item.get('text'), str) and item['text'].strip() for item in turns[0]['items']) and
            all(item.get('status') == 'completed' for item in turns[0]['items']
                if item.get('type') in {'commandExecution', 'collabAgentToolCall'}),
            'host_evidence_missing', 'current exact-thread readback must show its latest completed final turn and inactive host')


def native_host_preflight(value):
    require(isinstance(value, dict), 'host_capability_missing',
            'check native lifecycle support before worktree creation or dispatch; supply native_host evidence')
    if value.get('adapter') == 'collaboration':
        agents = collaboration_inventory(value)
        require(not collaboration_inventory_pending(value, set(agents) - {value['controller_ref']}),
                'host_evidence_missing', 'finish existing native agents before admitting this workflow')
        return {'adapter': 'collaboration', 'controller_ref': value['controller_ref']}
    require(value.get('adapter') == 'app-server', 'host_capability_missing', 'unsupported native lifecycle adapter')
    entry.fields(value, {'adapter', 'snapshot'})
    snapshot = value['snapshot']
    require(isinstance(snapshot, dict), 'host_capability_missing', 'raw app-server lifecycle lookup required')
    validate_rpc_lifecycle(snapshot, snapshot.get('carrier_thread'))
    return {'adapter': 'app-server', 'instance': snapshot['instance']}


def validate_rpc_lifecycle(snapshot, expected):
    entry.fields(snapshot, {'instance','carrier_thread','sequence','pages','events'})
    entry.nonempty(snapshot['instance'])
    entry.nonempty(expected)
    require(snapshot['carrier_thread'] == expected, 'identity_mismatch', 'lookup must retain the original carrier ancestor')
    require(type(snapshot['sequence']) is int and snapshot['sequence'] >= 0 and isinstance(snapshot['events'], list) and
            isinstance(snapshot['pages'], list) and snapshot['pages'], 'invalid_result', 'complete raw lifecycle lookup required')
    validate_snapshot_events(snapshot)
    cursor, seen, cursors = None, set(), set()
    archived, complete = False, False
    for page in snapshot['pages']:
        entry.fields(page, {'request','response'})
        request, response = page['request'], page['response']
        require(isinstance(request,dict) and isinstance(request.get('params'),dict) and isinstance(response,dict),
                'invalid_result', 'lookup request and response must be objects')
        params = request['params']
        kinds = params.get('sourceKinds')
        require(not complete and request.get('method') == 'thread/list' and params.get('ancestorThreadId') == expected and
                set(params) <= {'ancestorThreadId','archived','sourceKinds','modelProviders','limit','cursor'} and
                params.get('modelProviders') == [] and
                params.get('archived') is archived and isinstance(kinds,list) and len(kinds) == len(NATIVE_SOURCE_KINDS) and
                all(isinstance(kind,str) for kind in kinds) and set(kinds) == set(NATIVE_SOURCE_KINDS) and
                params.get('cursor') == cursor and isinstance(response.get('data'), list),
                'host_evidence_missing', 'retain every exact descendant lookup page')
        for thread in response['data']:
            require(isinstance(thread, dict) and isinstance(thread.get('id'), str) and thread['id'] not in seen,
                    'host_evidence_missing', 'descendant identities must be unique')
            seen.add(thread['id'])
        cursor = response.get('nextCursor')
        if cursor is None:
            if archived:
                complete = True
            archived, cursors = True, set()
        else:
            require(isinstance(cursor,str) and cursor and cursor not in cursors,
                    'host_evidence_missing', 'pagination must be contiguous without repeated cursors')
            cursors.add(cursor)
    require(complete and cursor is None, 'host_evidence_missing', 'both archived and non-archived descendant lookups must be complete')


@contextmanager
def record_lock(path, *, timeout=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+") as stream:
        deadline = time.monotonic() + (CHECKPOINT_LOCK_TIMEOUT if timeout is None else timeout)
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise entry.PreparationError("checkpoint_busy", "checkpoint lock wait timed out; retain the current operation") from error
                time.sleep(min(0.05, remaining))
        yield


def read_record(path):
    if not Path(path).exists():
        return {}
    with Path(path).open('rb') as stream:
        data = stream.read(state_store.ROOT_LIMIT + 1)
    require(len(data) <= state_store.ROOT_LIMIT, 'legacy_run_requires_original_runtime',
            'oversized legacy checkpoint requires its originally pinned runtime')
    value = json.loads(data)
    require(isinstance(value, dict), "invalid_checkpoint", "checkpoint must be an object")
    return value


def read_projection(path, *, projection, stage=None, subject_id=None, allocation_id=None, intent=None):
    """Explicit current-operation projection; never loads archived subjects."""
    require(projection in {'identity', 'scope', 'transaction', 'delivery'},
            'invalid_projection', 'choose identity, scope, transaction or delivery')
    if stage is not None:
        require(projection == 'scope' and stage == 2, 'invalid_projection', 'select the current Stage-2 scope')
    if projection == 'transaction':
        require(isinstance(subject_id, str) and (
                subject_id.startswith('stage:') and allocation_id is None and intent == 'prepare' or
                subject_id.startswith('ticket:') and isinstance(allocation_id, str) and allocation_id and
                isinstance(intent, str) and intent),
                'invalid_projection', 'transaction requires an exact stage prepare or allocation intent')
    else:
        require(subject_id is None and allocation_id is None and intent is None,
                'invalid_projection', 'allocation selectors belong to transaction reads')
    envelope = read_record(path) if stage is None and projection != 'transaction' else None
    classes = {'identity': state_store.IdentityProjection, 'scope': state_store.ScopeProjection,
               'transaction': state_store.TransactionProjection, 'delivery': state_store.DeliveryProjection}
    if envelope is not None and envelope.get('store_format') != state_store.FORMAT:
        return classes[projection](envelope, int(Path(path).exists()),
                                   Path(path).stat().st_size if Path(path).exists() else 0)
    store = state_store.open_store(path)
    current = store.open_current()
    require(current is not None, 'missing_checkpoint', 'current scope checkpoint is unavailable')
    if projection == 'delivery' and current.root['metadata']['stage'] == 3 and current.root.get('delivery') is not None:
        value, _, store = read_implementation_delivery(path, store=store, current=current)
        reference = handoff.seal({'kind': 'implementation-predecessor-ref', 'protocol': handoff.PROTOCOL,
            'checkpoint_path': str(Path(path).resolve()), 'reference': current.root['delivery'],
            'accepted_digest': value['accepted']['digest']})
        accepted, checkpoint = implementation_acceptance(reference, store=store)
        state = {**current.root['metadata'], 'delivery_kind': value['kind'], 'accepted': accepted,
                 'delivery_progress': checkpoint}
        return classes[projection](state_store.ProjectedRecord({**current.root['outer'], KEY: state}, current.revision),
                                   store.files_read, store.bytes_read, current.revision)
    if projection == 'identity':
        return classes[projection](state_store.ProjectedRecord({**current.root['outer'], KEY: current.root['metadata']}, current.revision),
                                   store.files_read, store.bytes_read, current.revision)
    subjects = current.root['subjects']
    stage_id = current.root['metadata']['stage_subject']
    require(stage_id in subjects and stage_id.startswith('stage:'),
            'invalid_checkpoint', 'current stage subject is missing')
    if projection == 'transaction' and subject_id.startswith('stage:'):
        require(subject_id == stage_id, 'intent_mismatch', 'prepare belongs to the current stage subject')
        state = store.read_subject(current, subject_id).value
        restore_ledger_mirror(state)
        saved = state.get('handoff')
        require(isinstance(saved, dict) and state.get('stage_subject') == subject_id and
                state.get('revision') == current.root['metadata']['revision'] and
                state.get('stage') == current.root['metadata']['stage'] and
                state.get('checkpoint_path') == str(Path(path).resolve()) and
                saved.get('digest') == current.root['metadata']['handoff']['digest'] and
                saved.get('controller_ref') == current.root['metadata']['controller_ref'] and
                saved.get('binding') == current.root['metadata']['binding'],
                'invalid_checkpoint', 'prepare identity differs from its current root')
        handoff.unseal(saved)
        for pin in current.root['metadata']['packages'].values():
            skill_preflight.verify_identity(pin)
        pending = state.get('transaction')
        require(isinstance(pending, dict) and pending.get('operation') == intent,
                'intent_mismatch', 'current stage has no original prepare intent')
        port = state['control']
        value = {key: state[key] for key in ('checkpoint_path', 'stage', 'stage_subject', 'revision', 'handoff')}
        value.update(control_digest=entry.digest(port['context']), control_port_digest=entry.digest(port),
                     control_ref=port.get('store_ref'), transaction=copy.deepcopy(pending))
        return state_store.TransactionProjection({KEY: value}, store.files_read, store.bytes_read, current.revision)
    if stage is not None:
        require(projection == 'scope' and stage == 2 and current.root['metadata']['stage'] == stage,
                'invalid_projection', 'select the current Stage-2 scope')
        state = store.read_scope(current, stage_id)
        validate_stage2_scope(state)
        for pin in current.root['metadata']['packages'].values():
            skill_preflight.verify_identity(pin)
        require(state['stage_subject'] == stage_id and state['revision'] == current.root['metadata']['revision'] and
                state['handoff']['digest'] == current.root['metadata']['handoff']['digest'] and
                state['handoff']['controller_ref'] == current.root['metadata']['controller_ref'] and
                state['handoff']['binding'] == current.root['metadata']['binding'] and
                state['checkpoint_path'] == str(Path(path).resolve()),
                'invalid_checkpoint', 'scope identity differs from its current root')
        state['run_id'] = current.run_id
        state['current_transport'] = (state['transport'] == scope_transport(current.root['outer']) and
                                      not current.root['outer'].get('runner_request'))
        return state_store.ScopeProjection({KEY: state}, store.files_read, store.bytes_read, current.revision)
    if projection == 'scope' and current.root['metadata']['stage'] == 3:
        state = store.read_scope(current, stage_id)
        validate_stage3_scope(state)
        for pin in current.root['metadata']['packages'].values():
            skill_preflight.verify_identity(pin)
        require(state['handoff']['controller_ref'] == current.root['metadata']['controller_ref'] and
                state['handoff']['binding'] == current.root['metadata']['binding'] and
                state['design_predecessor'] == current.root['metadata'].get('design_predecessor') and
                state['stage_subject'] == stage_id and
                state['revision'] == current.root['metadata']['revision'] and
                state['handoff']['digest'] == current.root['metadata']['handoff']['digest'] and
                state['checkpoint_path'] == str(Path(path).resolve()),
                'invalid_checkpoint', 'scope identity differs from current root')
        return classes[projection]({KEY: state}, store.files_read, store.bytes_read, current.revision)
    if projection == 'transaction':
        require(isinstance(subject_id, str) and subject_id.startswith('ticket:') and
                isinstance(allocation_id, str) and allocation_id and isinstance(intent, str) and intent,
                'invalid_projection', 'transaction requires exact subject, allocation and intent')
        subject = store.read_subject(current, subject_id).value
        require(subject_id == 'ticket:' + subject.get('ticket_id', '') + ':0' and subject.get('batch') == 0,
                'invalid_checkpoint', 'Ticket subject identity changed')
        slot = subject.get('allocations', {}).get(allocation_id)
        require(isinstance(slot, dict), 'allocation_missing', 'allocation is not in the specified subject')
        action = slot.get('action') or {}
        transaction = slot.get('transaction') or {}
        require(intent in {action.get('action_id'), transaction.get('intent_id')},
                'intent_mismatch', 'transaction does not name the original allocation intent')
        if transaction.get('operation') == 'prepare' and slot.get('repair_proposal') is not None:
            slot = slot['repair_proposal']['slot']
        saved = slot['handoff']
        require(saved.get('allocation_id') == allocation_id and saved.get('subject_id') == subject_id,
                'identity_mismatch', 'allocation subject identity changed')
        handoff.resolve_allocation(saved, store=store, current=current)
        record = slot.get('record') or {}
        state = {'allocation_id': allocation_id, 'subject_id': subject_id, 'intent': intent,
            'handoff_digest': saved['digest'], 'refs': copy.deepcopy(saved['refs']),
            'status': record.get('status'), 'owner_ref': slot['owner_ref'],
            'base_revision': transaction.get('base_revision'),
            'base_control_digest': transaction.get('base_control_digest'),
            'request_digest': transaction.get('request_digest'),
            'operation': transaction.get('operation'),
            'request': copy.deepcopy(record.get('request')),
            'receipt_refs': [item['receipt_ref'] for item in record.get('receipts', [])],
            'delivery_digest': (record.get('delivery') or {}).get('digest'),
            'acceptance_digest': (record.get('acceptance') or {}).get('digest')}
        return classes[projection]({KEY: state}, store.files_read, store.bytes_read, current.revision)
    state = store.read_subject(current, stage_id).value
    restore_ledger_mirror(state)
    return classes[projection](state_store.ProjectedRecord({**current.root['outer'], KEY: state}, current.revision), store.files_read, store.bytes_read, current.revision)


def read_active_owner(path):
    """Controller-only bounded roster needed for mutation, stop barriers and CAS.

    This is not a read-only projection or an archive reader. Allocation contracts
    remain references; only the current stage and at most 32 live slots are read.
    """
    envelope = read_record(path)
    if envelope.get('store_format') != state_store.FORMAT:
        return envelope
    store = state_store.open_store(path)
    current = store.open_current()
    state = store.read_subject(current, current.root['metadata']['stage_subject']).value
    outer = state_store.ProjectedRecord({**current.root['outer'], KEY: state}, current.revision)
    accepted = state.get('accepted')
    if isinstance(accepted, dict) and accepted.get('kind') == 'current-implementation-acceptance-body-ref':
        state['accepted'] = store.read_evidence(accepted['reference'], run_id=current.run_id)['value']['accepted']
        handoff.unseal(state['accepted'])
    allocations = {}
    for subject_id in current.root['subjects']:
        if subject_id == current.root['metadata']['stage_subject']:
            continue
        require(subject_id.startswith('ticket:') or subject_id == 'review:delivery:0', 'invalid_checkpoint', 'unknown active subject')
        subject = store.read_subject(current, subject_id).value
        require(subject.get('batch') == 0 and isinstance(subject.get('allocations'), dict) and
                not set(allocations) & set(subject['allocations']),
                'invalid_checkpoint', 'invalid current Ticket batch')
        allocations.update(subject['allocations'])
        reviews = subject.get('review_activity', {})
        require(isinstance(reviews, dict) and not set(reviews) & set(state.get('review_activity', {})),
                'invalid_checkpoint', 'local review subject identity is duplicated')
        state.setdefault('review_activity', {}).update(reviews)
        state.setdefault('review_history', []).extend(subject.get('review_history', []))
        require(len(allocations) <= 32, 'state_capacity', 'current allocation window is full')
    outer[KEY]['allocations'] = allocations
    restore_ledger_mirror(outer[KEY])
    return outer


def scope_transport(outer):
    transport = outer.get('transport') or {}
    return {key: transport.get(key) for key in ('instance', 'native_event_sequence', 'state', 'lookup_blocked')}


def stage_subject_value(state):
    """Canonical persisted stage fields; allocations belong to Ticket subjects."""
    value = {key: value for key, value in state.items() if key != 'allocations'}
    value['review_history'] = []
    value['review_activity'] = {}
    if state.get('stage') == 3 and state.get('accepted') is not None:
        store = state_store.open_store(state['checkpoint_path'])
        current = store.open_current()
        value['accepted'] = {'kind': 'current-implementation-acceptance-body-ref',
            'reference': store.write_object(current.run_id, 'evidence', {'accepted': state['accepted']})}
    port = state.get('control') or {}
    if port.get('discussion') is not None:
        reference = port.get('store_ref')
        require(reference is not None, 'authority_missing', 'attached control requires its original ledger pointer')
        context, _ = state_store.open_store(reference['ledger_path']).read_ledger_control(reference)
        require(context == port['context'], 'control_context_changed', 'runner cannot publish a modified ledger projection')
        value['control'] = {key: item for key, item in port.items() if key != 'context'}
    return value


def restore_ledger_mirror(state):
    port = state.get('control') or {}
    if port.get('discussion') is None:
        return
    reference = port.get('store_ref')
    require(reference is not None, 'authority_missing', 'attached mirror lost its ledger reference')
    context, _ = state_store.StateStore(reference['ledger_path']).read_ledger_control(reference)
    require(reference['topic_id'] == port['discussion']['actor_topic_id'] and
            reference['controller_ref'] == port['discussion']['actor_conversation_ref'],
            'identity_mismatch', 'mirror references a different ledger owner')
    require('context' not in port or port['context'] == context,
            'control_context_changed', 'runner control mirror differs from its ledger projection')
    port['context'] = context


def read_archive_context(port, handoff_id):
    """Resolve one retired handoff at the original ledger generation."""
    pointer = port.get('store_ref')
    envelope = port.get('discussion')
    require(isinstance(pointer, dict) and isinstance(envelope, dict) and
            pointer.get('topic_id') == envelope.get('actor_topic_id') and
            pointer.get('controller_ref') == envelope.get('actor_conversation_ref'),
            'identity_mismatch', 'archive ledger pointer differs from its original owner')
    context, _ = state_store.StateStore(pointer['ledger_path']).read_ledger_control(
        pointer, selector={'handoff_id': handoff_id})
    control.validate_current_context(context)
    require(handoff_id in context.get('retired_handoffs', {}),
            'identity_mismatch', 'exact completed ledger handoff required')
    return context


def stage2_scope(state, path, outer):
    """Derive a read-only contract from the Controller's current validated facts."""
    owner = Progress(path, outer)
    context = state['control']['context']
    current = context.get('handoff_progress') or {}
    publication = state.get('publication')
    projected = {key: copy.deepcopy(state.get(key)) for key in (
        'protocol', 'revision', 'stage', 'stage_subject', 'checkpoint_path', 'status',
        'stopped', 'phase_complete', 'stop_requested', 'handoff', 'scope_authority', 'accepted')}
    projected.update(scope_kind='stage2-scope-v1', state_digest=entry.digest(stage_subject_value(state)),
        transport=scope_transport(outer), control_revision=context.get('revision'),
        scope_seal=copy.deepcopy(current.get('scope_seal')),
        scope_revisions=copy.deepcopy(current.get('scope_revisions', [])),
        decision_provenance=change_decision_journal(state),
        pending_kind=(state.get('pending') or {}).get('kind'),
        transaction_pending=state.get('transaction') is not None,
        scope_revision_pending=state.get('scope_revision_transaction') is not None,
        transport_loss=bool(state.get('transport_loss')), business_block=bool(state.get('business_block')),
        stop_barrier_pending=bool(owner.stop_barrier_pending()),
        control_pending=bool(owner.unresolved_control_transactions()),
        host_pending=any(not action.get('resolved') and not action.get('creation_resolved') for action in
            [*state.get('retained_host_actions', []), state.get('host_action') or {}] if action),
        host_status=state['host']['status'], stop_evidence_ref=entry.digest(state['host']['last_stop']),
        publication=({key: copy.deepcopy(publication.get(key)) for key in ('operation', 'request', 'facts', 'result')}
                     if publication else None))
    validate_stage2_scope(projected)
    return projected


def stage3_scope(state):
    """Read authority and identity facts without native payloads or writer leases."""
    context = state['control']['context']
    current = context.get('handoff_progress') or {}
    return {**{key: copy.deepcopy(state.get(key)) for key in (
        'protocol', 'revision', 'stage', 'stage_subject', 'checkpoint_path', 'handoff',
        'scope_authority', 'consumed_readiness', 'design_predecessor', 'recovery_predecessor', 'decision_provenance')},
        'scope_kind': 'stage3-scope-v1', 'control_digest': entry.digest(context),
        'control_revision': context.get('revision'),
        'execution_authority': copy.deepcopy(current.get('execution_authority')),
        'integration_recovery': ({'decision': copy.deepcopy(state['final_integration_recovery']),
            'predecessor': copy.deepcopy(state['recovery_predecessor']),
            'carrier': copy.deepcopy(context['carrier']),
            'dispatch_attempt': state['dispatch']['request']['attempt'],
            'retained': copy.deepcopy(current.get('integration_retained')),
            'source_digest': (current.get('integration_source') or {}).get('snapshot_digest')}
            if state.get('final_integration_recovery') else None),
        'plan_results': [{key: copy.deepcopy(value) for key, value in item.items() if key in {
            'state', 'stopped', 'plan_authority', 'paths', 'plan_refs', 'git_result_snapshot',
            'source_result_digest', 'source_owned_start_fingerprints', 'owned_final_fingerprints',
            'read_only_fingerprints'}} | ({'git_snapshot': {'files': copy.deepcopy(item['git_snapshot']['files'])}}
                if isinstance(item.get('git_snapshot'), dict) and 'files' in item['git_snapshot'] else {})
            for item in current.get('carried_results', []) + current.get('executions', [])
            if item.get('state') == 'accepted'],
        'plan_revision_transaction': ({key: copy.deepcopy(state['plan_revision_transaction'].get(key))
            for key in ('state', 'proposed_authority')} if state.get('plan_revision_transaction') else None)}


def validate_stage3_scope(state):
    fields = {'protocol', 'revision', 'stage', 'stage_subject', 'checkpoint_path', 'handoff',
        'scope_authority', 'consumed_readiness', 'design_predecessor', 'recovery_predecessor', 'decision_provenance',
        'scope_kind', 'control_digest', 'control_revision', 'execution_authority', 'integration_recovery', 'plan_results', 'plan_revision_transaction'}
    require(isinstance(state, dict) and set(state) == fields and state['stage'] == 3 and
            state['protocol'] == PROTOCOL and state['scope_kind'] == 'stage3-scope-v1' and
            type(state['revision']) is int and (state['control_revision'] is None or type(state['control_revision']) is int) and
            isinstance(state['control_digest'], str) and re.fullmatch('[0-9a-f]{64}', state['control_digest']),
            'invalid_checkpoint', 'invalid Stage-3 scope projection')
    handoff.unseal(state['handoff'])
    require(state['handoff']['stage'] == 3 and state['handoff']['role'] == 'implementation-dispatcher',
            'invalid_checkpoint', 'scope does not belong to the Dispatcher')


def implementation_outer_ready(outer, stop_transport):
    """Current runner/transport facts may revoke a C-owned stopped delivery."""
    if (outer.get('runner_request') or {}).get('operation') in {'pause', 'cancel'}:
        return False
    transport = outer.get('transport') or {}
    if transport.get('lookup_blocked') or transport.get('state') == 'lost':
        return False
    if stop_transport['transport_present'] and not transport:
        return False
    if stop_transport['adapter'] == 'collaboration':
        return not transport
    return not transport or (transport.get('instance') == stop_transport['instance'] and
        transport.get('native_event_sequence', 0) == stop_transport['native_event_sequence'])


def freeze_native_source_index(state, store, current, inherited=()):
    """Freeze source identities, not applied-operation authority or old stage state."""
    index = {}
    for reference in inherited:
        prior = store.read_evidence(reference, run_id=current.run_id, kind='native-source-index')['value']
        require(isinstance(prior, dict), 'state_object_corrupt', 'invalid native source index')
        for key, value in prior.items():
            index.setdefault(key, copy.deepcopy(value))

    def source(value, identities=None):
        identities = value if identities is None else identities
        keys = [key for key in ('query_id', 'call_ref', 'response_ref')
                if isinstance(identities.get(key), str)]
        if not keys:
            return
        reference = store.write_object(current.run_id, 'evidence', {'native_source': value})
        for key in keys:
            index.setdefault(entry.digest([key, identities[key]]), reference)

    for value in state.get('collaboration_lookups', {}).values():
        source(value)
    for query in state.get('baseline_thread_queries', []):
        source({key: value for key, value in query.items() if key not in {'input', 'result'}})
        source(query['input']['binding'])
        if query.get('result') is not None:
            source(query['result'])
    for observation in state.get('observations', []):
        provenance = observation.get('provenance')
        if isinstance(provenance, dict):
            source(observation, provenance)
    # Coalesced polls already retain their exact run/kind/key conflict fingerprints.
    # This index covers the original observations from which seen/calls were derived.
    return store.write_object(current.run_id, 'native-source-index', index)


def plan_revision_allocation_slot(state, value, *, store=None, current=None):
    """Read one original invocation without re-embedding its history in C."""
    require(isinstance(value, dict), 'invalid_checkpoint', 'original revision allocation reference required')
    if value.get('kind') != 'plan-revision-allocation-ref':
        return value
    entry.fields(value, {'kind', 'allocation_digest', 'reference'})
    store = store or state_store.open_store(state['checkpoint_path'])
    current = current or store.open_current()
    body = store.read_evidence(value['reference'], run_id=current.run_id,
        maximum=state_store.SUBJECT_LIMIT)['value']
    entry.fields(body, {'plan_revision_allocation'})
    slot = body['plan_revision_allocation']
    require(isinstance(slot, dict) and (slot.get('record') or {}).get('request', {}).get('attempt') ==
            value['allocation_digest'], 'invalid_checkpoint', 'original revision allocation identity changed')
    return slot


def freeze_implementation_native_owners(state, store, current, prior):
    """Keep accepted owner facts after allocations leave the active read set."""
    references = copy.deepcopy(prior['native_owner_refs']) if prior else {}

    def accepted_owner(ref, owner, thread, digest, stopped):
        require(stopped is True, 'handoff_not_ready', 'native owner requires original stopped business work')
        references[ref] = store.write_object(current.run_id, 'evidence', {
            'kind': 'accepted-native-owner-v1', 'ref': ref, 'owner_ref': owner,
            'parent_thread': thread, 'delivery_digest': digest, 'stopped': True,
            'status': 'accepted'})

    accepted_owner(state['accepted']['role_ref'], state['handoff']['controller_ref'],
        state['handoff']['expected_entry']['actor']['thread_id'], state['accepted']['digest'],
        state['host']['status'] == 'stopped')
    slots = [plan_revision_allocation_slot(state, slot, store=store, current=current)
             for history in reversed(state.get('plan_revision_allocation_history', []))
             for slot in history['allocations'].values()]
    slots.extend(state.get('allocations', {}).values())
    for slot in slots:
        record = handoff.unseal(slot['record']) if slot.get('record') else {}
        accepted = record.get('acceptance')
        if accepted and record['status'] == 'accepted':
            require(accepted['status'] == 'accepted' and record['receipts'] and
                    record['receipts'][-1]['status'] == 'stopped' and
                    record['receipts'][-1]['ref'] == accepted['role_ref'],
                    'handoff_not_ready', 'allocation owner lost original acceptance or stop proof')
            accepted_owner(accepted['role_ref'], slot['owner_ref'],
                handoff.resolve_allocation(slot['handoff'])['expected_entry']['actor']['thread_id'],
                accepted['digest'], True)
    checkpoint = state['control']['context']['handoff_progress']
    for group in [*reversed(state.get('review_history', [])), state.get('review_activity', {})]:
        for slot in group.values():
            if slot and slot.get('ref') and slot.get('stopped') and checkpoint.get('state') in {
                    'final-validation-pending', 'validating', 'final-validation-failed', 'deliverable', 'completed'}:
                accepted_owner(slot['ref'], slot['originating_ref'], slot['originating_ref'],
                    entry.digest(slot['receipts']), True)
    return references


def implementation_delivery(state, path, store, current):
    """Freeze only accepted implementation facts; original reports remain exact refs."""
    saved, accepted = state['handoff'], state['accepted']
    owner = Progress(path, {**current.root['outer'], KEY: state})
    require(state['stage'] == 3 and state['status'] == 'accepted' and state['stopped'] and
            not owner.stop_barrier_pending() and not owner.unresolved_host_actions() and
            state['phase_complete'] and state['dispatch']['acceptance'] == accepted,
            'handoff_not_ready', 'implementation delivery requires original acceptance and complete stop proof')
    verify_phase_completed(state)
    checkpoint = state['control']['context']['handoff_progress']
    initial = (saved.get('readiness') or {}).get('scope_authority') or saved.get('inline_authority')
    recovery_prior = (read_implementation_delivery(path, state['recovery_predecessor'])[0]
                      if state.get('recovery_predecessor') else None)
    lineage = recovery_prior['execution_authority'] if recovery_prior else initial
    revisions = list(state.get('plan_revision_history', {}).values())
    transaction = state.get('plan_revision_transaction')
    if transaction is not None:
        require(all(item['old_authority']['revision'] <= transaction['old_authority']['revision']
                    for item in revisions), 'handoff_not_ready', 'revision history follows current transaction')
        revisions.append(transaction)
    chain = copy.deepcopy(recovery_prior['authority_lineage']) if recovery_prior else []
    for item in sorted(revisions, key=lambda item: (item['old_authority']['revision'], item['state'] == 'activated')):
        require(item['old_authority'] == lineage and item['state'] in {'activated', 'rejected'},
                'handoff_not_ready', 'implementation lost its original authority lineage')
        before = entry.digest(lineage)
        if item['state'] == 'activated':
            lineage = item['proposed_authority']
        chain.append({'state': item['state'], 'old_authority_digest': before,
                      'authority_digest': entry.digest(lineage)})
    require(isinstance(initial, dict) and lineage == state['scope_authority'] == checkpoint['execution_authority'] and
            lineage['execution_plan']['contract_digest'] == initial['execution_plan']['contract_digest'] and
            lineage['scope']['protected_paths'] == initial['scope']['protected_paths'] and
            checkpoint['git_baseline_commit'] == saved['scope']['baseline'] and
            (transaction or {}).get('state') in (None, 'activated') and
            (saved.get('readiness') is None or consumed_scope_readiness(state, path) is not None),
            'handoff_not_ready', 'implementation delivery lacks current activated authority')
    control.validate_deliverable(checkpoint, accepted['payload']['candidate_commit'],
        accepted['payload']['review'], accepted['payload']['verification'], accepted['role_ref'])
    def evidence(value):
        return store.write_object(current.run_id, 'evidence', value)
    verification = accepted['payload']['verification']
    decision = verification['review_decision']
    native_refs = {axis: evidence(source) for axis, source in decision['native_evidence'].items()}
    latest = verification['attempts'][-1]
    final = {key: copy.deepcopy(item) for key, item in latest.items() if key not in {'source', 'result'}}
    final['source_ref'] = evidence(latest['source'])
    verification_facts = {key: copy.deepcopy(item) for key, item in verification.items()
                          if key not in {'review_decision', 'attempts'}}
    verification_facts.update(kind='implementation-verification-v1',
        review_decision={key: copy.deepcopy(item) for key, item in decision.items() if key != 'native_evidence'},
        review_sources=native_refs, final_attempt_ref=evidence(final),
        attempt_count=len(verification['attempts']),
        attempt_ids_digest=entry.digest([item['attempt_id'] for item in verification['attempts']]))
    inherited_sources = [recovery_prior['native_source_index_ref']] if recovery_prior else []
    if state.get('final_integration_recovery') is not None:
        inherited_sources.append(state['final_integration_recovery']['closure_handoff']['native_source_index_ref'])
    source_index_ref = freeze_native_source_index(state, store, current, inherited_sources)
    git_proof = {'candidate_evidence': {'plan_authority': copy.deepcopy(checkpoint['candidate_evidence']['plan_authority'])},
        'plan_revisions': [], 'plan_revision_sources': []}
    for record in checkpoint.get('plan_revisions', []):
        facts = record['evidence']
        git_proof['plan_revisions'].append({'controller_ref': record['controller_ref'], 'evidence': {
            key: copy.deepcopy(facts[key]) for key in
            ('revision_id', 'planning_commit', 'planning_paths', 'git_facts', 'snapshot', 'new_scope')}})
    for source in checkpoint.get('plan_revision_sources', []):
        git_proof['plan_revision_sources'].append({'revision_id': source['revision_id'], 'executions': [
            {key: copy.deepcopy(item[key]) for key in ('state', 'stopped', 'paths', 'git_result_snapshot') if key in item} |
            ({'git_snapshot': {key: copy.deepcopy(item['git_snapshot'][key]) for key in ('head', 'files')}}
             if 'git_snapshot' in item else {}) for item in source['executions']]})
    # The Git reader needs only the final retained-candidate boundary, never its old reports.
    if checkpoint.get('integration_retained') is not None:
        retained_paths = copy.deepcopy(checkpoint['integration_retained']['paths'])
        # This accepted commit is the new finite proof boundary; no ancestor delivery chain.
        git_proof.update(integration_retained={'commit': accepted['payload']['candidate_commit'],
            'paths': retained_paths}, integration_candidate=accepted['payload']['candidate_commit'],
            validation_history=[{'candidate': accepted['payload']['candidate_commit'],
                                 'accepted_paths': retained_paths}])
    original = {key: copy.deepcopy(saved[key]) for key in ('protocol', 'stage', 'role', 'controller_ref',
        'requirement_identity', 'requirement', 'binding', 'scope', 'authorization', 'source_commit', 'digest')}
    original.update(planning_paths=handoff.inherited_planning_paths(saved.get('predecessor')),
        manifest_baseline=handoff.inherited_manifest_baseline(saved),
        kind='implementation-launch-facts', expected_entry={
        'discussion_project': saved['expected_entry']['discussion_project']})
    payload = {key: copy.deepcopy(accepted['payload'][key]) for key in ('artifacts', 'checks', 'candidate_commit')}
    value = {'kind': 'accepted-implementation-v1', 'checkpoint_path': str(Path(path).resolve()),
        'stage': 3, 'stage_subject': state['stage_subject'], 'source_revision': state['revision'],
        'controller_ref': saved['controller_ref'], 'packages': copy.deepcopy(state['packages']),
        'handoff': original, 'accepted': {key: copy.deepcopy(accepted[key]) for key in
            ('protocol', 'status', 'downstream_ready', 'role_ref', 'requirement_identity', 'digest')},
        'payload': payload, 'review_ref': evidence(accepted['payload']['review']),
        'verification_ref': evidence(verification_facts),
        'execution_authority': copy.deepcopy(lineage), 'initial_authority_digest': entry.digest(initial),
        'authority_lineage': chain, 'dispatcher': {key: state['control']['context']['carrier'][key]
            for key in ('ref', 'attempt')},
        'dispatch_attempt': state['dispatch']['request']['attempt'],
        'control_revision': state['control']['context'].get('revision'),
        'design_predecessor': copy.deepcopy(state.get('design_predecessor')),
        'phase_ref': evidence({'handoff': {key: copy.deepcopy(saved[key]) for key in
            ('stage', 'entry', 'expected_entry', 'authorization', 'binding', 'requirement_identity', 'controller_ref')}}),
        'stop_ref': evidence({'host': state['host']['last_stop'], 'lifecycle': state.get('lifecycle_snapshot')}),
        'stop_transport': {'adapter': state['native_host']['adapter'],
            'transport_present': bool(current.root['outer'].get('transport')),
            'instance': state['lifecycle_snapshot']['instance'],
            'native_event_sequence': state['lifecycle_snapshot'].get('sequence')},
        'native_refs': sorted(owner.native_refs()),
        'native_owner_refs': freeze_implementation_native_owners(state, store, current, recovery_prior),
        'native_source_index_ref': source_index_ref,
        'git_evidence_ref': evidence(git_proof),
        # Recovery consumes contracts and scalar authority facts, never an old stage/control.
        'recovery_ref': evidence({'launch': {key: copy.deepcopy(item) for key, item in saved.items()
            if key != 'digest'}, 'control_identity': {key: copy.deepcopy(state['control']['context'][key])
            for key in control.CONTEXT_FIELDS - {'stage', 'carrier', 'handoff_progress'}},
            'dispatch_configuration': copy.deepcopy(checkpoint['configuration']),
            'authority_digest': checkpoint['authority_digest'],
            'implementation_policy': copy.deepcopy(checkpoint.get('implementation_policy', {'mode': 'full'})),
            'runner': ({'session': current.root['outer']['sessions']['stage3'],
                        'turn': current.root['outer']['launch']['turn']}
                       if current.root['outer'].get('confirmed', {}).get('stages') else None)}),
        'stopped': True, 'phase_complete': True}
    return value


def validate_recovery_publication_ref(reference, run_id):
    """Check the finite audit pointer without opening its historical evidence."""
    require(isinstance(reference, dict) and set(reference) == {'run_id', 'kind', 'sha256', 'size'} and
            reference['run_id'] == run_id and reference['kind'] == 'evidence' and
            isinstance(reference['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', reference['sha256']) and
            type(reference['size']) is int and 0 < reference['size'] <= state_store.EVIDENCE_LIMIT,
            'state_object_corrupt', 'invalid recovery publication reference')


def freeze_recovery_publication(state, accepted, observation, store, current):
    publication = state.get('publication')
    if publication is None:
        return None
    # Keep the exact original transaction and outcomes separately. The current
    # closure receives only a reference, never a second publication authority.
    transaction = {key: publication[key] for key in ('operation', 'request', 'facts')}
    proof = {'kind': 'recovery-publication-v1', 'closure_digest': state['handoff']['digest'],
        'accepted_candidate': accepted, 'binding_digest': entry.digest(state['handoff']['binding']),
        'request_digest': entry.digest(publication['request']),
        'candidate': publication['request']['candidate_commit'],
        'transaction_ref': store.write_object(current.run_id, 'evidence', transaction),
        'observation': {'ok': observation['ok'], 'state': observation['state']}}
    for key in ('result', 'error'):
        original = publication.get(key)
        proof[key + '_ref'] = (store.write_object(current.run_id, 'evidence', original)
                              if original is not None else None)
    return store.write_object(current.run_id, 'evidence', proof)


def read_recovery_publication(path, closure, *, include_outcome=False):
    """Resolve one pinned historical transaction for audit, without write authority."""
    store = state_store.open_store(path)
    current = store.open_current()
    require(current is not None and current.root['metadata']['stage'] == 3,
            'publication_missing', 'historical publication requires the current recovery')
    scope = store.read_scope(current, current.root['metadata']['stage_subject'])
    recovery = scope.get('integration_recovery') or {}
    require(recovery.get('decision', {}).get('closure_handoff') == closure,
            'publication_missing', 'publication does not belong to the pinned recovery')
    handoff.unseal(closure)
    require(closure.get('publication_ref') is not None, 'publication_missing',
            'no original publication preceded this recovery')
    validate_recovery_publication_ref(closure['publication_ref'], current.run_id)
    proof = store.read_evidence(closure['publication_ref'], run_id=current.run_id)['value']
    entry.fields(proof, {'kind', 'closure_digest', 'accepted_candidate', 'binding_digest',
        'request_digest', 'candidate', 'transaction_ref', 'result_ref', 'error_ref', 'observation'})
    require(proof['kind'] == 'recovery-publication-v1' and
            proof['closure_digest'] == closure['source_digest'] and
            proof['accepted_candidate'] == closure['candidate'] and
            proof['binding_digest'] == entry.digest(closure['binding']) and
            proof['observation'] in ({'ok': True, 'state': 'not-published'}, {'ok': True, 'state': 'prepared'}),
            'state_object_corrupt', 'recovery publication binding changed')
    validate_recovery_publication_ref(proof['transaction_ref'], current.run_id)
    transaction = store.read_evidence(proof['transaction_ref'], run_id=current.run_id)['value']
    entry.fields(transaction, {'operation', 'request', 'facts'})
    require(transaction['operation'] == 'complete-worktree' and
            isinstance(transaction['request'], dict) and isinstance(transaction['facts'], list) and
            transaction['request'].get('binding') == closure['binding'] and
            transaction['request'].get('candidate_commit') == proof['candidate'] and
            entry.digest(transaction['request']) == proof['request_digest'],
            'state_object_corrupt', 'original publication request changed')
    result = {'transaction': transaction, 'observation': proof['observation']}
    for key in ('result', 'error'):
        reference = proof[key + '_ref']
        if reference is not None:
            validate_recovery_publication_ref(reference, current.run_id)
        if include_outcome:
            result[key] = (store.read_evidence(reference, run_id=current.run_id)['value']
                           if reference is not None else None)
    return result


def read_implementation_delivery(path, reference=None, *, store=None, current=None):
    """Read the one current or pinned accepted delivery, without a writer lease."""
    store = store or state_store.open_store(path)
    current = current or store.open_current()
    require(current is not None, 'handoff_not_ready', 'original implementation checkpoint is unavailable')
    require(str(store.checkpoint.resolve()) == str(Path(path).resolve()),
            'handoff_not_ready', 'delivery store belongs to another checkpoint')
    metadata = current.root['metadata']
    if reference is None:
        require(metadata['stage'] == 3, 'handoff_not_ready', 'current implementation delivery required')
        raw_ref = current.root.get('delivery')
    else:
        body = handoff.unseal(reference)
        entry.fields(body, {'kind', 'protocol', 'checkpoint_path', 'reference', 'accepted_digest'})
        require(body['kind'] == 'implementation-predecessor-ref' and body['protocol'] == handoff.PROTOCOL and
                body['checkpoint_path'] == str(Path(path).resolve()),
                'handoff_not_ready', 'implementation predecessor checkpoint changed')
        raw_ref = body['reference']
        require((metadata['stage'] == 3 and raw_ref == current.root.get('delivery')) or
                (metadata['stage'] == 4 and metadata.get('implementation_predecessor') == reference) or
                (metadata['stage'] == 3 and metadata.get('recovery_predecessor') == reference),
                'handoff_not_ready', 'implementation predecessor is not pinned by this Controller')
    require(raw_ref is not None, 'handoff_not_ready', 'accepted implementation delivery is unavailable')
    value = store.read_predecessor(current, raw_ref)
    entry.fields(value, {'kind', 'checkpoint_path', 'stage', 'stage_subject', 'source_revision',
        'controller_ref', 'packages', 'handoff', 'accepted', 'payload', 'review_ref', 'verification_ref',
        'execution_authority', 'initial_authority_digest', 'authority_lineage', 'dispatcher',
        'dispatch_attempt', 'control_revision', 'design_predecessor', 'phase_ref', 'stop_ref',
        'git_evidence_ref', 'recovery_ref', 'stopped', 'phase_complete', 'native_refs', 'native_owner_refs', 'native_source_index_ref', 'stop_transport'})
    require(type(value['source_revision']) is int and value['source_revision'] >= 0 and
            isinstance(value['authority_lineage'], list) and len(value['authority_lineage']) <= 33,
            'handoff_not_ready', 'implementation delivery schema changed')
    require(value['kind'] == 'accepted-implementation-v1' and value['stage'] == 3 and
            value['controller_ref'] == metadata['controller_ref'] and
            value['handoff']['binding'] == metadata['binding'] and value['stopped'] and value['phase_complete'] and
            all(metadata['packages'].get(name) == pin for name, pin in value['packages'].items()) and
            (reference is None or value['accepted']['digest'] == reference['accepted_digest']),
            'handoff_not_ready', 'implementation delivery identity changed')
    stop_transport = value['stop_transport']
    entry.fields(stop_transport, {'adapter', 'transport_present', 'instance', 'native_event_sequence'})
    require(stop_transport['adapter'] in {'app-server', 'collaboration'} and
            type(stop_transport['transport_present']) is bool and isinstance(stop_transport['instance'], str) and
            (stop_transport['adapter'] == 'collaboration' or
             type(stop_transport['native_event_sequence']) is int and stop_transport['native_event_sequence'] >= 0),
            'handoff_not_ready', 'invalid delivery stop transport fingerprint')
    if metadata['stage'] == 3 and raw_ref == current.root.get('delivery'):
        require(implementation_outer_ready(current.root['outer'], stop_transport),
                'handoff_not_ready', 'current runner or transport revoked implementation delivery readiness')
        require(metadata['status'] == 'accepted' and metadata['stopped'] and metadata['phase_complete'] and
                not metadata.get('stop_requested') and not metadata.get('business_block') and
                value['source_revision'] == metadata['revision'] and
                value['stage_subject'] == metadata['stage_subject'] and
                value['handoff']['digest'] == metadata['handoff']['digest'],
                'handoff_not_ready', 'delivery differs from current Controller revision')
    if value['handoff']['authorization'].get('phase') is not None:
        phase = store.read_evidence(value['phase_ref'], run_id=current.run_id)['value']
        require(all(phase.get('handoff', {}).get(key) == value['handoff'][key] for key in
                    ('authorization', 'binding', 'requirement_identity', 'controller_ref')),
                'identity_mismatch', 'implementation phase proof belongs to another acceptance')
        verify_phase_completed(phase)
    for pin in value['packages'].values():
        skill_preflight.verify_identity(pin)
    require(value['handoff']['controller_ref'] == value['controller_ref'] and
            value['accepted']['requirement_identity'] == value['handoff']['requirement_identity'],
            'handoff_not_ready', 'implementation source identity changed')
    require(value['dispatcher']['ref'] == value['accepted']['role_ref'] and
            value['dispatcher']['attempt'] == value['dispatch_attempt'],
            'handoff_not_ready', 'original Dispatcher identity changed')
    require(isinstance(value['native_owner_refs'], dict) and
            value['native_owner_refs'].keys() <= set(value['native_refs']),
            'handoff_not_ready', 'native owner facts differ from original registered identities')
    for owner_ref in value['native_owner_refs'].values():
        validate_recovery_publication_ref(owner_ref, current.run_id)
    lineage = value['initial_authority_digest']
    for item in value['authority_lineage']:
        require(item['old_authority_digest'] == lineage and item['state'] in {'activated', 'rejected'} and
                (item['state'] != 'rejected' or item['authority_digest'] == lineage),
                'handoff_not_ready', 'implementation authority lineage changed')
        lineage = item['authority_digest']
    require(lineage == entry.digest(value['execution_authority']),
            'handoff_not_ready', 'current implementation authority differs from lineage')
    return value, current, store


def implementation_predecessor(path):
    value, current, _ = read_implementation_delivery(path)
    return handoff.seal({'kind': 'implementation-predecessor-ref', 'protocol': handoff.PROTOCOL,
        'checkpoint_path': str(Path(path).resolve()), 'reference': current.root['delivery'],
        'accepted_digest': value['accepted']['digest']})


def implementation_acceptance(reference, path=None, *, store=None):
    """Resolve an immutable footer or predecessor to the exact finite delivery evidence."""
    body = handoff.unseal(reference)
    if body.get('kind') == 'implementation-acceptance-ref':
        entry.fields(body, {'kind', 'protocol', 'stage', 'run_id', 'checkpoint_path', 'accepted_digest'})
        require(body['stage'] == 3 and body['protocol'] == handoff.PROTOCOL and
                str(Path(body['checkpoint_path']).resolve()) == body['checkpoint_path'] and
                (path is None or str(path) == body['checkpoint_path']),
                'handoff_not_ready', 'original implementation acceptance checkpoint required')
        path = body['checkpoint_path']
        store = store or state_store.open_store(path)
        current = store.open_current()
        require(current is not None and current.run_id == body['run_id'],
                'handoff_not_ready', 'acceptance belongs to another run')
        pinned = current.root['metadata'].get('implementation_predecessor')
        value, current, store = read_implementation_delivery(path, pinned, store=store, current=current)
    else:
        path = path or body['checkpoint_path']
        value, current, store = read_implementation_delivery(path, reference, store=store)
    require(value['accepted']['digest'] == body['accepted_digest'],
            'handoff_not_ready', 'footer differs from original implementation acceptance')
    review = store.read_evidence(value['review_ref'], run_id=current.run_id)['value']
    proof = store.read_evidence(value['verification_ref'], run_id=current.run_id)['value']
    entry.fields(proof, (control.DELIVERY_VERIFICATION_FIELDS - {'review_decision', 'attempts'}) |
        {'kind', 'review_decision', 'review_sources', 'final_attempt_ref', 'attempt_count', 'attempt_ids_digest'})
    require(proof['kind'] == 'implementation-verification-v1' and type(proof['attempt_count']) is int and
            proof['attempt_count'] > 0 and isinstance(proof['attempt_ids_digest'], str) and
            re.fullmatch('[0-9a-f]{64}', proof['attempt_ids_digest']) and
            set(proof['review_sources']) == {'standards', 'spec'},
            'handoff_not_ready', 'implementation verification proof changed')
    final = store.read_evidence(proof['final_attempt_ref'], run_id=current.run_id)['value']
    source = store.read_evidence(final['source_ref'], run_id=current.run_id)['value']
    latest = {key: copy.deepcopy(item) for key, item in final.items() if key != 'source_ref'}
    latest['source'] = source
    latest['result'] = {key: copy.deepcopy(latest[key]) for key in ('attempt_id', 'checks', 'source', 'source_snapshot')}
    verification = {key: copy.deepcopy(proof[key]) for key in control.DELIVERY_VERIFICATION_FIELDS
                    if key not in {'review_decision', 'attempts'}}
    verification['review_decision'] = {**proof['review_decision'], 'native_evidence': {
        axis: store.read_evidence(ref, run_id=current.run_id)['value'] for axis, ref in proof['review_sources'].items()}}
    verification['attempts'] = [latest]
    git_facts = store.read_evidence(value['git_evidence_ref'], run_id=current.run_id)['value']
    if git_facts.get('integration_retained') is not None:
        pinned_ref = handoff.seal({'kind': 'implementation-predecessor-ref', 'protocol': handoff.PROTOCOL,
            'checkpoint_path': str(Path(path).resolve()),
            'reference': (current.root['delivery'] if current.root['metadata']['stage'] == 3 and
                          value['accepted']['digest'] == (body.get('accepted_digest')) and
                          current.root['delivery'] is not None and reference.get('kind') == 'implementation-acceptance-ref'
                          else (pinned['reference'] if reference.get('kind') == 'implementation-acceptance-ref'
                                else reference['reference'])),
            'accepted_digest': value['accepted']['digest']})
        git_facts['validation_history'][-1]['proof_ref'] = pinned_ref
    checkpoint = {**verification, **git_facts, 'state': 'deliverable',
        'binding': value['handoff']['binding'], 'git_baseline_commit': value['handoff']['scope']['baseline'],
        'execution_authority': value['execution_authority'],
        'allowed_paths': value['execution_authority']['scope']['implementation_paths'],
        'protected_paths': value['handoff']['scope']['protected_paths']}
    accepted = {**value['accepted'], 'handoff': value['handoff'],
                'payload': {**value['payload'], 'review': review, 'verification': verification}}
    control.validate_deliverable(checkpoint, value['payload']['candidate_commit'], review, verification,
                                 accepted['role_ref'])
    control_git.verify_stored_review_coverage(verification['coverage'], checkpoint, value['packages'], current.run_id)
    require(store.bytes_read <= state_store.READ_LIMIT, 'state_capacity', 'implementation delivery exceeds read budget')
    return accepted, checkpoint


def closure_delivery_view(reference):
    """Adapter-only resolution of one committed delivery into raw-free control facts."""
    accepted, checkpoint = implementation_acceptance(reference)
    facts, _, _ = read_implementation_delivery(reference['checkpoint_path'], reference)
    verification = accepted['payload']['verification']
    latest = verification['attempts'][-1]
    source = latest['source']
    final = {key: copy.deepcopy(value) for key, value in latest.items()
             if key not in {'source', 'source_snapshot', 'result'}}
    final['source_snapshot_digest'] = entry.digest(latest['source_snapshot'])
    final['source'] = {key: source[key] for key in ('adapter', 'call_ref', 'response_ref')}
    final['source'].update(evidence_digest=entry.digest(source), checks_digest=entry.digest(source['raw']['checks']),
        source_unchanged=source['raw']['source_unchanged'], stopped=source['raw']['stopped'],
        dispatcher_ref=source['raw']['dispatcher_ref'], attempt_id=source['raw']['attempt_id'])
    scope = checkpoint['execution_authority']['scope']
    view = {'accepted_digest': accepted['digest'], 'candidate': verification['candidate'],
        'dispatcher_ref': accepted['role_ref'], 'dispatch_attempt': facts['dispatch_attempt'],
        'binding': facts['handoff']['binding'], 'controller_ref': facts['controller_ref'],
        'requirement_identity': accepted['requirement_identity'],
        'implementation_paths': scope['implementation_paths'], 'closure_paths': scope['closure_paths'],
        'protected_paths': sorted(set(facts['handoff']['scope']['protected_paths']) - set(scope['closure_paths'])),
        'validation_plan': verification['validation_plan'], 'plan_digest': verification['plan_digest'],
        'review_digest': verification['review_digest'], 'coverage': verification['coverage'], 'review_sources': {
            axis: {key: item[key] for key in ('adapter', 'call_ref', 'response_ref', 'status', 'ref')} |
                  {'evidence_digest': entry.digest(item)}
            for axis, item in verification['review_decision']['native_evidence'].items()},
        'final_attempt': final}
    return {'kind': 'implementation-verification-ref', 'predecessor': copy.deepcopy(reference),
        'review_ref': facts['review_ref'], 'verification_ref': facts['verification_ref'],
        'expected_target_head': verification['expected_target_head'], 'plan_authority': verification['plan_authority'],
        'controller_ref': facts['controller_ref'], 'requirement_identity': accepted['requirement_identity'], 'view': view}


def design_predecessor(state, path, readiness):
    """Freeze accepted facts after the original Controller has checked every gate."""
    store = state_store.open_store(path)
    current = store.open_current()
    scoped = store.read_scope(current, state['stage_subject'])
    validate_stage2_scope(scoped)
    require(readiness['checkpoint_revision'] == state['revision'] and
            current.root['metadata']['revision'] == state['revision'] and
            scoped['state_digest'] == entry.digest(stage_subject_value(state)),
            'stale_checkpoint', 'design predecessor must be the current accepted revision')
    saved = state['handoff']
    # These are already verified facts, not a resealed callable B input.
    original = {key: copy.deepcopy(saved[key]) for key in (
        'digest', 'protocol', 'kind', 'stage', 'role', 'controller_ref', 'requirement_identity', 'requirement',
        'binding', 'scope', 'authorization')}
    original['kind'] = 'design-launch-facts'
    original['expected_entry'] = {'discussion_project': saved['expected_entry']['discussion_project'],
                                  'repository': {'root': saved['binding']['repository']},
                                  'actor': {'thread_id': saved['expected_entry']['actor']['thread_id']}}
    accepted = {key: copy.deepcopy(value) for key, value in state['accepted'].items()
                if key not in {'handoff', 'record'}}
    accepted['handoff'] = original
    value = {'kind': 'accepted-design-v1', 'checkpoint_path': str(Path(path).resolve()),
        'stage_subject': state['stage_subject'], 'source_revision': state['revision'],
        'controller_ref': saved['controller_ref'], 'packages': copy.deepcopy(state['packages']),
        'handoff': original, 'accepted': accepted, 'readiness': copy.deepcopy(readiness),
        'scope_authority': copy.deepcopy(state['scope_authority']),
        'scope_seal': copy.deepcopy(scoped['scope_seal']),
        'scope_revisions': copy.deepcopy(scoped['scope_revisions']),
        'decision_provenance': copy.deepcopy(scoped['decision_provenance']),
        'status': state['status'], 'stopped': state['stopped'], 'phase_complete': state['phase_complete'],
        'stop_evidence_ref': scoped['stop_evidence_ref']}
    # Preserve only this design stage's native source identities and original
    # evidence refs. Later queries compare identifiers without reading raw tools.
    sources = list(state.get('collaboration_lookups', {}).values())
    for query in state.get('baseline_thread_queries', []):
        sources.append(query['input']['binding'])
        if query.get('result') is not None:
            sources.append(query['result'])
    source_index = {}
    for source in {entry.digest(source): source for source in sources}.values():
        source_ref = store.write_object(current.run_id, 'evidence', {'native_source': source})
        for key in ('query_id', 'call_ref', 'response_ref'):
            if key in source:
                source_index[entry.digest([key, source[key]])] = source_ref
    value['native_source_index_ref'] = store.write_object(current.run_id, 'native-source-index', source_index)
    value['stop_source_ref'] = store.write_object(current.run_id, 'evidence', {'native_stop': state['host']['last_stop']})
    if saved['authorization'].get('phase') is not None:
        verify_phase_completed(state)
        value['phase_ref'] = store.write_object(current.run_id, 'evidence', {'handoff': {
            'authorization': copy.deepcopy(saved['authorization']),
            'entry': {'source': copy.deepcopy(saved['entry']['source'])},
            'expected_entry': {key: copy.deepcopy(saved['expected_entry'][key]) for key in
                ('discussion_project', 'requirement', 'repository')}}})
    reference = store.write_predecessor(current, value)
    return handoff.seal({'kind': 'design-predecessor-ref', 'protocol': handoff.PROTOCOL,
        'checkpoint_path': str(Path(path).resolve()), 'reference': reference,
        'accepted_digest': state['accepted']['digest']})


def read_design_acceptance(reference):
    """Resolve a footer's exact acceptance through its original current authority."""
    body = handoff.unseal(reference)
    entry.fields(body, {'kind', 'protocol', 'stage', 'run_id', 'checkpoint_path', 'accepted_digest'})
    require(body['kind'] == 'design-acceptance-ref' and body['protocol'] == handoff.PROTOCOL and body['stage'] == 2 and
            isinstance(body['checkpoint_path'], str) and
            str(Path(body['checkpoint_path']).resolve()) == body['checkpoint_path'],
            'handoff_not_ready', 'canonical original acceptance checkpoint required')
    path = Path(body['checkpoint_path'])
    envelope = read_record(path)
    require(envelope.get('run_id') == body['run_id'], 'handoff_not_ready', 'acceptance belongs to another run')
    stage = envelope.get(KEY, {}).get('stage')
    require(stage in (2, 3), 'handoff_not_ready', 'original design acceptance is unavailable')
    if stage == 2:
        state = read_projection(path, projection='scope', stage=2).record[KEY]
        accepted = state['accepted']
    else:
        reference = envelope[KEY].get('design_predecessor')
        require(reference is not None, 'handoff_not_ready', 'current Stage 3 has no accepted design')
        state = read_design_predecessor(reference, path)
        accepted = state['accepted']
    require(isinstance(accepted, dict) and accepted['digest'] == body['accepted_digest'],
            'handoff_not_ready', 'footer differs from the original design acceptance')
    return copy.deepcopy(accepted)


def read_design_predecessor(reference, path):
    body = handoff.unseal(reference)
    entry.fields(body, {'kind', 'protocol', 'checkpoint_path', 'reference', 'accepted_digest'})
    require(body['kind'] == 'design-predecessor-ref' and body['protocol'] == handoff.PROTOCOL and
            body['checkpoint_path'] == str(Path(path).resolve()),
            'handoff_not_ready', 'predecessor must retain its original checkpoint')
    store = state_store.open_store(path)
    current = store.open_current()
    require(current is not None, 'handoff_not_ready', 'original predecessor checkpoint is unavailable')
    value = store.read_predecessor(current, body['reference'])
    metadata = current.root['metadata']
    require(value.get('controller_ref') == metadata['controller_ref'] and
            value.get('handoff', {}).get('binding') == metadata['binding'] and
            all(metadata['packages'].get(name) == pin for name, pin in value.get('packages', {}).items()),
            'handoff_not_ready', 'predecessor identity differs from current Controller')
    if metadata['stage'] == 2:
        scoped = store.read_scope(current, metadata['stage_subject'])
        require(value.get('source_revision') == metadata['revision'] and
                value.get('stage_subject') == metadata['stage_subject'] and
                value.get('scope_authority') == scoped['scope_authority'] and
                value.get('scope_seal') == scoped['scope_seal'] and
                value.get('scope_revisions') == scoped['scope_revisions'] and
                value.get('decision_provenance') == scoped['decision_provenance'] and
                all(value['accepted'].get(key) == item for key, item in scoped['accepted'].items()
                    if key != 'handoff') and
                all(value['handoff'].get(key) == item for key, item in scoped['handoff'].items()
                    if key in value['handoff'] and key not in {'kind', 'expected_entry'}),
                'handoff_not_ready', 'predecessor was not accepted by this Controller revision')
    else:
        pinned = metadata.get('design_predecessor') if metadata['stage'] == 3 else None
        if metadata['stage'] == 4 and metadata.get('implementation_predecessor') is not None:
            delivery, _, _ = read_implementation_delivery(path, metadata['implementation_predecessor'],
                                                         store=store, current=current)
            pinned = delivery['design_predecessor']
        require(metadata['stage'] in (3, 4) and pinned == reference,
                'handoff_not_ready', 'predecessor is not pinned by the current Controller')
    require(value['accepted']['digest'] == body['accepted_digest'] == value['readiness']['accepted_digest'] and
            value['readiness']['checkpoint_revision'] == value['source_revision'] and
            value['readiness']['checkpoint_path'] == body['checkpoint_path'] and
            value['status'] == 'accepted' and value['stopped'] and value['phase_complete'] and
            value['handoff'].get('kind') == 'design-launch-facts' and
            value['handoff'] == value['accepted']['handoff'] and
            value['controller_ref'] == value['handoff']['controller_ref'] and
            value['scope_authority'] == value['readiness']['scope_authority'] and
            value['stop_evidence_ref'] == value['readiness']['stop_evidence_ref'],
            'handoff_not_ready', 'accepted design predecessor facts changed')
    if value['handoff']['authorization'].get('phase') is not None:
        require(value.get('phase_ref') is not None, 'phase_pending', 'original planning phase proof is unavailable')
        phase = store.read_evidence(value['phase_ref'], run_id=current.run_id)['value']
        require(phase.get('handoff', {}).get('authorization') == value['handoff']['authorization'],
                'identity_mismatch', 'planning phase proof belongs to another authorization')
        verify_phase_completed(phase)
    verify_scope_authority(value['scope_authority'], initial_seal=value['scope_seal'],
        revision_records=value['scope_revisions'], requirement_identity=value['handoff']['requirement_identity'],
        binding=value['handoff']['binding'], decision_records=value['decision_provenance'])
    for pin in value['packages'].values():
        skill_preflight.verify_identity(pin)
    return value


def read_noncreation_retry(state, *, store=None, current=None):
    """Resolve one ended invocation, without its old stage or ancestors."""
    store = store or state_store.open_store(state['checkpoint_path'])
    current = current or store.open_current()
    reference = state['noncreation_retry']
    proof = store.read_evidence(reference, run_id=current.run_id,
                                maximum=state_store.ROOT_LIMIT)['value']
    entry.fields(proof, {'kind', 'checkpoint_path', 'stage', 'stage_subject', 'source_revision',
        'controller_ref', 'binding', 'requirement_identity', 'packages', 'native_host',
        'workspace_evidence', 'result_ref', 'action_ref', 'observation_ref', 'retry_handoff_digest'})
    require(proof['kind'] == 'noncreation-retry-v1' and
            proof['checkpoint_path'] == state['checkpoint_path'] == str(store.checkpoint.resolve()) and
            proof['stage'] == state['stage'] and type(proof['source_revision']) is int and
            0 <= proof['source_revision'] < state['revision'] and
            isinstance(proof['stage_subject'], str) and proof['stage_subject'].startswith('stage:') and
            proof['controller_ref'] == state['handoff']['controller_ref'] and
            proof['binding'] == state['handoff']['binding'] and
            proof['requirement_identity'] == state['handoff']['requirement_identity'] and
            proof['packages'] == state['packages'] and proof['native_host'] == state['native_host'] and
            proof['workspace_evidence'] == state['workspace_evidence'] and
            proof['retry_handoff_digest'] == state['handoff']['digest'],
            'invalid_checkpoint', 'non-creation retry identity changed')
    metadata = current.root['metadata']
    require((metadata['stage_subject'] == state['stage_subject'] and
             metadata.get('noncreation_retry') == reference) or
            (metadata['stage_subject'] == proof['stage_subject'] and
             metadata['revision'] == proof['source_revision']),
            'invalid_checkpoint', 'retry proof is not pinned by the original Controller')
    result, action, observation = [store.read_evidence(proof[key], run_id=current.run_id,
        maximum=state_store.ROOT_LIMIT)['value'] for key in ('result_ref', 'action_ref', 'observation_ref')]
    require(isinstance(result, dict) and result.get('status') == 'not-created' and
            isinstance(result.get('record'), dict) and isinstance(action, dict) and
            isinstance(observation, dict), 'invalid_checkpoint', 'original non-creation proof is incomplete')
    record = handoff.unseal(result['record'])
    entry.fields(record, {'protocol', 'kind', 'request', 'receipts', 'delivery', 'acceptance', 'status'},
                 {'handoff', 'stage_input', 'scope_checkpoint_path'})
    entry.fields(observation, {'event_id', 'receipt', 'action_id', 'provenance'},
                 {'result', 'action_resolution', 'closure', 'publication_candidate', 'lifecycle'})
    entry.nonempty(observation['event_id'])
    require(record['protocol'] == handoff.PROTOCOL and record['kind'] == 'dispatch' and
            sum(key in record for key in ('handoff', 'stage_input')) == 1 and
            record.get('status') == 'not-created' and record.get('delivery') is None and
            record.get('acceptance') is None and isinstance(record.get('request'), dict) and
            isinstance(record.get('receipts'), list),
            'invalid_checkpoint', 'retry requires an ended unbound dispatch')
    if 'stage_input' in record:
        pin = record['stage_input']
        entry.fields(pin, {'checkpoint', 'run_id', 'reference'})
        require(pin['checkpoint'] == state['checkpoint_path'] and pin['run_id'] == current.run_id,
                'identity_mismatch', 'retry input belongs to another checkpoint')
        value = store.read_evidence(pin['reference'], run_id=current.run_id,
                                    maximum=state_store.ROOT_LIMIT)['value']
        entry.fields(value, {'stage_input'})
        original = value['stage_input']
    else:
        original = record.get('handoff')
    handoff.unseal(original)
    handoff.require_current_protocol(original)
    launch = record['request']
    require(all(key in original for key in ('stage', 'controller_ref', 'role', 'selection', 'scope',
            'target', 'binding', 'semantic', 'requirement_identity', 'predecessor', 'authorization')) and
            isinstance(original['authorization'], dict) and
            all(key in original['authorization'] for key in ('reference', 'flow_mode')) and
            all(key in launch for key in ('input_digest', 'controller_ref', 'role', 'configuration', 'attempt', 'digest')) and
            isinstance(observation.get('receipt'), dict),
            'invalid_checkpoint', 'original retry invocation schema changed')
    require(launch['input_digest'] == original['digest'] and
            launch['controller_ref'] == original['controller_ref'] == proof['controller_ref'] and
            launch['role'] == original['role'] and launch['configuration'] == original['selection'] and
            action.get('operation') == 'invoke-host' and action.get('checkpoint') == state['checkpoint_path'] and
            action.get('payload') == launch and action.get('creation_resolved') is True and
            action.get('creation_receipt') == {'event_id': observation.get('event_id'), 'outcome': 'not-created'} and
            all(original[key] == state['handoff'][key] for key in
                ('stage', 'role', 'scope', 'target', 'binding', 'semantic', 'requirement_identity', 'predecessor')) and
            original['authorization']['reference'] != state['handoff']['authorization']['reference'] and
            original['authorization']['flow_mode'] == state['mode'],
            'invalid_checkpoint', 'retry differs from the original ended invocation')
    receipt = observation['receipt']
    dispatch.receipt_for({'request': launch}, receipt, {'lookup'})
    provenance = observation.get('provenance')
    require(receipt['status'] == 'not-created' and receipt['ref'] is None and
            receipt in record['receipts'] and isinstance(provenance, dict) and
            set(provenance) == {'call_ref', 'response_ref', 'action_id'} and
            all(isinstance(value, str) and value.strip() for value in provenance.values()) and
            observation.get('action_id') == provenance['action_id'] == action.get('action_id'),
            'host_evidence_missing', 'retry requires the exact authenticated non-creation lookup')
    raw = receipt['raw']
    if raw.get('format') == 'host-raw-reference-v1':
        manifest = store.read_raw_manifest(raw['evidence_ref'], run_id=current.run_id)
        binding = manifest.get('native_receipt', {})
        require(manifest['complete'] and raw.get('truncated') is False and
                raw.get('sha256') == manifest['sha256'] and raw.get('size') == manifest['size'] and
                binding == host_raw_binding(observation, binding.get('facts'), raw.get('event_digest')),
                'host_evidence_missing', 'original non-creation raw proof is incomplete')
    require(store.bytes_read <= state_store.READ_LIMIT, 'state_capacity', 'retry proof exceeds read budget')
    return proof


def noncreation_retry(state, path, saved):
    store = state_store.open_store(path)
    current = store.open_current()
    require(current.root['metadata']['stage_subject'] == state['stage_subject'] and
            current.root['metadata']['revision'] == state['revision'],
            'stale_checkpoint', 'retry must close the current failed request')
    record = state['dispatch']
    known, status, _ = dispatch.matching(record, state['control']['context'])
    action = state.get('host_action') or {}
    event_id = (action.get('creation_receipt') or {}).get('event_id')
    observation = next((item for item in state['observations'] if item['event_id'] == event_id), None)
    event = state['events'].get(event_id, {})
    result = state.get('last_result')
    provenance = (observation or {}).get('provenance') or {}
    require(known is None and status == 'cancelled' and state['stopped'] and
            not state.get('stop_requested') and observation is not None and event.get('applied') and
            not event.get('host_conflict') and action.get('creation_resolved') is True and
            state['host']['calls'].get(entry.digest([observation['receipt']['adapter'],
                provenance.get('call_ref')])) == provenance.get('action_id') == action.get('action_id') and
            isinstance(result, dict) and result.get('status') == 'not-created' and result.get('record') == record,
            'host_evidence_missing', 'retain the original applied non-creation lookup and result')
    values = {'result_ref': result, 'action_ref': action, 'observation_ref': observation}
    # No evidence object may hide a whole old current state behind a large limit.
    require(all(len(state_store.encode({'format': state_store.FORMAT, 'run_id': current.run_id,
            'kind': 'evidence', 'value': value})) <= state_store.ROOT_LIMIT for value in values.values()),
            'state_capacity', 'non-creation proof exceeds its fixed budget')
    proof = {key: copy.deepcopy(state[key]) for key in ('stage', 'stage_subject', 'checkpoint_path',
        'packages', 'native_host', 'workspace_evidence')}
    proof.update(kind='noncreation-retry-v1', source_revision=state['revision'],
        controller_ref=state['handoff']['controller_ref'], binding=state['handoff']['binding'],
        requirement_identity=state['handoff']['requirement_identity'], retry_handoff_digest=saved['digest'])
    proof.update({key: store.write_object(current.run_id, 'evidence', value) for key, value in values.items()})
    require(len(state_store.encode({'format': state_store.FORMAT, 'run_id': current.run_id,
            'kind': 'evidence', 'value': proof})) <= state_store.ROOT_LIMIT,
            'state_capacity', 'retry predecessor exceeds its fixed budget')
    return store.write_object(current.run_id, 'evidence', proof)


def validate_stage2_scope(state):
    fields = {'protocol', 'revision', 'stage', 'stage_subject', 'checkpoint_path', 'status', 'stopped',
        'phase_complete', 'stop_requested', 'handoff', 'scope_authority', 'accepted', 'scope_kind', 'state_digest',
        'transport', 'control_revision', 'scope_seal', 'scope_revisions', 'decision_provenance', 'pending_kind',
        'transaction_pending', 'scope_revision_pending', 'transport_loss', 'business_block', 'stop_barrier_pending',
        'control_pending', 'host_pending', 'host_status', 'stop_evidence_ref', 'publication'}
    require(isinstance(state, dict) and set(state) == fields and state['scope_kind'] == 'stage2-scope-v1' and
            state['stage'] == 2 and state['protocol'] == PROTOCOL and type(state['revision']) is int and
            state['revision'] >= 0 and isinstance(state['stage_subject'], str) and
            isinstance(state['checkpoint_path'], str) and isinstance(state['transport'], dict) and
            all(isinstance(state[key], str) and re.fullmatch(r'[0-9a-f]{64}', state[key])
                for key in ('state_digest', 'stop_evidence_ref')) and
            isinstance(state['scope_revisions'], list) and isinstance(state['decision_provenance'], dict) and
            all(type(state[key]) is bool for key in ('stopped', 'phase_complete', 'transaction_pending',
                'scope_revision_pending', 'transport_loss', 'business_block', 'stop_barrier_pending',
                'control_pending', 'host_pending')),
            'invalid_checkpoint', 'invalid Stage-2 scope projection')
    saved = state['handoff']
    handoff.unseal(saved)
    handoff.require_current_protocol(saved)
    require(saved.get('stage') == 2, 'invalid_checkpoint', 'scope handoff is not Stage 2')
    stage_scope.validate_scope(saved['scope'], saved['binding']['repository'], stage=2,
        requirement_path=saved['requirement_identity']['path'])
    authority = state['scope_authority']
    if authority is not None:
        require(state['scope_seal'] is not None, 'scope_changed', 'scope needs its Controller seal')
        verify_scope_authority(authority, initial_seal=state['scope_seal'], revision_records=state['scope_revisions'],
            requirement_identity=saved['requirement_identity'], binding=saved['binding'],
            decision_records=state['decision_provenance'])
        stage_scope.validate_scope(authority['scope'], saved['binding']['repository'], stage=2,
            requirement_path=saved['requirement_identity']['path'], candidate_commit=authority['candidate_commit'],
            generated_outputs=generated_outputs_for(authority['proposal']))
    if state['accepted'] is not None:
        handoff.unseal(state['accepted'])
        require(state['accepted'].get('handoff') == saved and isinstance(state['accepted'].get('payload'), dict),
                'invalid_checkpoint', 'scope acceptance changed handoff')


def save_current_record(path, outer, expected_revision, *, expected_authority_revision, receipt_action=None, receipts=()):
    state = outer[KEY]
    require(not state.get('history'), 'state_capacity',
            'stage history must be archived before opening another current stage')
    require(len(state.get('allocations', {})) <= 32 and
            len(state.get('control_transactions', {})) <= 64,
            'state_capacity', 'current allocation or pending receipt window is full')
    stage_id = state['stage_subject']
    subjects = {stage_id: stage_subject_value(state)}
    for identity, slot in state.get('allocations', {}).items():
        refs = slot['handoff']['semantic']['plan_refs']
        tickets = {ref['ticket_id'] for ref in refs}
        require(len(tickets) == 1, 'invalid_allocation', 'one active batch belongs to exactly one Ticket')
        ticket = next(iter(tickets))
        subject_id = 'ticket:' + ticket + ':0'
        subject = subjects.setdefault(subject_id, {'ticket_id': ticket, 'batch': 0, 'allocations': {}})
        subject['allocations'][identity] = slot
    def review_subject(axis, slot):
        if axis in {'standards', 'spec'}:
            return subjects.setdefault('review:delivery:0', {'batch': 0, 'allocations': {}})
        ticket = slot['subject']['ticket_id']
        return subjects.setdefault('ticket:' + ticket + ':0', {'ticket_id': ticket, 'batch': 0, 'allocations': {}})
    for axis, slot in state.get('review_activity', {}).items():
        review_subject(axis, slot).setdefault('review_activity', {})[axis] = slot
    for group in state.get('review_history', []):
        for axis, slot in group.items():
            review_subject(axis, slot).setdefault('review_history', []).append({axis: slot})
    metadata = {key: state[key] for key in ('protocol', 'revision', 'stage', 'status')}
    metadata['stage_subject'] = stage_id
    if state.get('design_predecessor') is not None:
        metadata['design_predecessor'] = copy.deepcopy(state['design_predecessor'])
    for key in ('implementation_predecessor', 'recovery_predecessor', 'noncreation_retry'):
        if state.get(key) is not None:
            metadata[key] = copy.deepcopy(state[key])
    metadata.update(controller_ref=state['control']['context']['controller_ref'],
        checkpoint_path=state['checkpoint_path'], packages=state['packages'],
        binding=state['handoff'].get('binding'))
    # Runner routing facts carry no execution leases or native raw evidence.
    for key in ('mode', 'pending', 'stop_requested', 'stopped', 'phase_complete',
                'business_block', 'deferred_business_recovery', 'continuation_intent',
                'recovery_decisions', 'recovered_request', 'recovery_action', 'error', 'decisions'):
        if key in state:
            metadata[key] = copy.deepcopy(state[key])
    metadata['host'] = {key: state.get('host', {}).get(key) for key in ('status', 'generation')}
    if state.get('host', {}).get('query') is not None:
        metadata['host']['query'] = {'resolved': state['host']['query'].get('resolved', False)}
    if state.get('cancellation') is not None:
        metadata['cancellation'] = {'result': None if state['cancellation'].get('result') is None else True}
    metadata['handoff'] = {'digest': state['handoff']['digest']}
    metadata['dispatch'] = {'request': {key: (state.get('dispatch') or {}).get('request', {}).get(key)
                                      for key in ('attempt', 'digest')}}
    store = state_store.open_store(path, receipt_action=receipt_action)
    envelope = read_record(path)
    current = store.open_current() if envelope.get('store_format') == state_store.FORMAT else None
    require((current.root['metadata']['revision'] if current else 0) == expected_revision,
            'stale_checkpoint', 'Controller revision changed')
    require((current.revision if current else 0) == expected_authority_revision,
            'stale_checkpoint', 'checkpoint authority changed; reload before saving')
    initial = envelope if envelope else None
    if current is None:
        require(KEY not in envelope and envelope.get('version') in (None, 11),
                'legacy_run_requires_original_runtime', 'retain original checkpoint runtime')
    else:
        initial = None
    if state.get('noncreation_retry') is not None:
        read_noncreation_retry(state, store=store, current=current)
    return store.commit_current(expected_revision=current.revision if current else 0,
        revision=current.revision + 1 if current else 1, subjects=subjects, metadata=metadata,
        outer={key: value for key, value in outer.items() if key != KEY}, receipts=receipts,
        initial_outer=initial, scope=(stage2_scope(state, path, outer) if state['stage'] == 2 else
               stage3_scope(state) if state['stage'] == 3 else None),
        delivery=(implementation_delivery(state, path, store, current) if state['stage'] == 3 and
                  state['status'] == 'accepted' and state['stopped'] and state['phase_complete'] else None))


def save_outer_record(path, outer, *, expected_revision):
    """CAS the runner envelope without deriving or replacing Controller subjects."""
    require(KEY not in outer and 'store_format' not in outer, 'state_owner_required',
            'outer owner cannot write Controller state')
    envelope = read_record(path)
    if envelope.get('store_format') == state_store.FORMAT:
        return state_store.open_store(path).commit_outer(expected_revision=expected_revision, outer=outer)
    require(expected_revision == 0 and KEY not in envelope and outer.get('version') == 11,
            'legacy_run_requires_original_runtime', 'outer initialization requires a new runner')
    atomic_save(path, outer)


def atomic_save(path, value):
    path = Path(path)
    require(not (isinstance(value, dict) and value.get('store_format') == state_store.FORMAT) and
            (not path.exists() or read_record(path).get('store_format') != state_store.FORMAT),
            'state_owner_required',
            'bounded checkpoint writes require the original owner CAS; legacy runner/header writes are unsupported')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="." + path.name, delete=False) as stream:
            temporary = stream.name
            stream.write((json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def pending_decision(kind, subject, question):
    entry.nonempty(kind); entry.nonempty(question)
    return {"decision_id": str(uuid.uuid4()), "kind": kind, "subject": copy.deepcopy(subject), "question": question}


def decision_matches(pending, decision):
    entry.fields(decision, {"decision_id", "subject", "answer", "reference"}, {"flow_intent"})
    require(pending is not None and decision["decision_id"] == pending["decision_id"] and
            decision["subject"] == pending["subject"], "stale_decision", "answer does not name the current pending matter")
    entry.nonempty(decision["reference"]); entry.nonempty(decision["answer"])


def change_decision_journal(state):
    records = {}
    if state.get('design_predecessor') is not None:
        records.update(read_design_predecessor(state['design_predecessor'], state['checkpoint_path'])['decision_provenance'])
    for saved in [*state.get("history", []), state]:
        for identity, value in saved.get("decision_provenance", {}).items():
            require(identity not in records or records[identity] == value,
                    "scope_conflict", "original user-decision journal changed")
            records[identity] = value
    return records


def approved_change_evidence(state, decision):
    if decision.get("kind") == "in-requirement-refinement":
        return None
    require(decision.get("kind") == "reference-to-approved-change" and
            isinstance(decision.get("approved_change_ref"), str),
            "approved_change_missing", "explicit approved change reference required")
    controller = state["control"]["context"]["controller_ref"]
    requirement_identity = state["handoff"]["requirement_identity"]
    ancestry = {item.get("handoff", {}).get("digest") for item in [*state.get("history", []), state]}
    matches = [record for record in change_decision_journal(state).values()
        if record.get("pending_kind") == "user-decision" and
           record.get("controller_ref") == controller and
           record.get("requirement_identity") == requirement_identity and
           record.get("handoff_digest") in ancestry and
           record.get("decision", {}).get("answer") == "approve-change" and
           record["decision"].get("reference") == decision["approved_change_ref"] and
           record["decision"].get("subject", {}).get("handoff") == record["handoff_digest"]]
    require(len(matches) == 1, "approved_change_missing",
            "approved change must name one original user-decision in this Controller's source history")
    record = copy.deepcopy(matches[0])
    return {"record": record, "digest": entry.digest(record)}


def generated_outputs_for(data):
    mapping = data.get("generated_outputs", {})
    require(isinstance(mapping, dict), "invalid_scope", "generated output inventory must be a map")
    require(isinstance(data.get("manifest"), list) and all(isinstance(item, dict) for item in data["manifest"]),
            "invalid_scope", "complete manifest array required")
    if any(item.get("kind") == "generated" for item in data["manifest"]):
        require("generated_outputs" in data and bool(mapping), "scope_incomplete",
                "generated files require a declared source-to-output inventory")
    return copy.deepcopy(mapping)


def stage_boundary(mode, stage, accepted, next_stage=None):
    require(mode in {"continuous", "stepwise"}, "invalid_mode", "explicit flow mode required")
    return None if mode == "continuous" or stage == 4 else pending_decision(
        "stage-entry", {"stage": next_stage if next_stage is not None else stage + 1, "predecessor": accepted["digest"]},
        "Confirm entry to Stage " + str(next_stage if next_stage is not None else stage + 1))


def workspace_evidence(binding, controller_ref, supplied):
    """Retain original host observations in C; raw tools are not an authority store."""
    lifecycle = binding['lifecycle']
    if lifecycle['disposition'] == 'delete':
        expected = {'controller_ref': controller_ref, 'reference': lifecycle['evidence_ref'],
                    'worktree': binding['worktree'], 'creation_receipt': lifecycle['creation_receipt']}
        require(supplied is None or supplied == expected, 'workspace_evidence_changed', 'retain the script creation receipt')
        return copy.deepcopy(expected)
    entry.fields(supplied, {'controller_ref', 'reference', 'worktree', 'receipts'})
    require(supplied['controller_ref'] == controller_ref and supplied['reference'] == lifecycle['evidence_ref']
            and supplied['worktree'] == binding['worktree'], 'workspace_evidence_changed',
            'workspace observation must name the original Controller, reference and exact path')
    receipts = supplied['receipts']
    require(isinstance(receipts, list) and 0 < len(receipts) <= 16, 'host_evidence_missing',
            'retain actual workspace selection and occupancy observations')
    for receipt in receipts:
        entry.fields(receipt, {'tool', 'request', 'response'})
        entry.nonempty(receipt['tool'])
        require(isinstance(receipt['request'], dict) and
                isinstance(receipt['response'], (dict, list, str)) and bool(receipt['response']),
                'host_evidence_missing', 'complete workspace tool request and raw response required')
    return copy.deepcopy(supplied)


def read_stage_transaction_result(state):
    """Read only the immutable proof named by the original pending B request."""
    saved = state['transaction_result']
    require(saved is not None and saved['request_digest'] == entry.digest(state['transaction']),
            'transaction_pending', 'retain the result bound to the original B request')
    store = state_store.open_store(state['checkpoint_path'])
    current = store.open_current()
    proof = store.read_evidence(saved['reference'], run_id=current.run_id,
                               maximum=state_store.SUBJECT_LIMIT)['value']
    require(isinstance(proof, dict) and set(proof) == {'request_digest', 'result'} and
            proof['request_digest'] == saved['request_digest'] and
            isinstance(proof['result'], dict) and
            entry.digest(proof['result']) == saved['result_digest'] and
            proof['result'].get('status') == saved['status'],
            'state_object_corrupt', 'original B result proof changed')
    require(store.bytes_read <= state_store.READ_LIMIT,
            'state_capacity', 'original B result exceeds the operation read budget')
    return proof['result']


def validate_saved_reviews(state):
    store = state_store.open_store(state['checkpoint_path'])
    current = store.open_current()
    identities = set()
    for group in [state.get('review_activity', {}), *state.get('review_history', [])]:
        require(isinstance(group, dict) and len(group) <= 32 and all(isinstance(key, str) and
                (key in {'standards', 'spec'} or key.startswith(('local:', 'ticket:'))) for key in group),
                'invalid_checkpoint', 'retain the two original review axes')
        for axis, slot in group.items():
            require(isinstance(slot, dict) and isinstance(slot.get('action_id'), str) and slot['action_id'] and
                    slot['action_id'] not in identities and isinstance(slot.get('prepare'), dict) and
                    slot['prepare'].get('axis') == axis and slot['prepare'].get('candidate') == slot.get('candidate') and
                    slot['prepare'].get('actor_ref') == slot.get('originating_ref') and
                    type(slot.get('stopped')) is bool and isinstance(slot.get('receipts'), list),
                    'invalid_checkpoint', 'review must retain its original request and native identity')
            identities.add(slot['action_id'])
            if 'cost' in slot:
                cost = slot['cost']
                entry.fields(cost, {'classification', 'source_ref', 'waiting_seconds'})
                require(isinstance(cost['classification'], str) and cost['classification'] in {'accepted', 'review-defect', 'unknown'} and
                        cost['waiting_seconds'] == 'unknown' and any(
                            cost['source_ref'] == entry.digest({key: receipt[key] for key in ('adapter', 'call_ref', 'response_ref')})
                            for receipt in slot['receipts'] if receipt['status'] in {'stopped', 'not-created'}),
                        'invalid_checkpoint', 'cost hint must retain its original stopped receipt identity')
            start = slot.get('start')
            require(isinstance(start, dict) and type(start.get('applied')) is bool and
                    isinstance(start.get('control_digest'), str) and re.fullmatch(r'[0-9a-f]{64}', start['control_digest']),
                    'invalid_checkpoint', 'review must retain its original C intent')
            if start['applied']:
                require(isinstance(start.get('result_digest'), str) and
                        re.fullmatch(r'[0-9a-f]{64}', start['result_digest']) and
                        'result_ref' in start and (start['result_ref'] is None or isinstance(start['result_ref'], dict)),
                        'invalid_checkpoint', 'applied review must retain its original C result proof')
            query = slot.get('query')
            if query is not None:
                require(isinstance(query, dict) and isinstance(query.get('action_id'), str) and query['action_id'] and
                        query['action_id'] != slot['action_id'] and query.get('original_action_id') == slot['action_id'] and
                        type(query.get('resolved')) is bool and
                        (query.get('call_ref') is None or isinstance(query['call_ref'], str) and query['call_ref']),
                        'invalid_checkpoint', 'review lookup must retain its original call')
            for receipt in slot['receipts']:
                require(isinstance(receipt, dict) and set(receipt) == {
                        'adapter', 'call_ref', 'response_ref', 'status', 'ref', 'raw'} and
                        receipt['status'] in {'running', 'stopped', 'unknown', 'not-created'} and
                        all(isinstance(receipt[key], str) and receipt[key] for key in ('adapter', 'call_ref', 'response_ref')) and
                        isinstance(receipt['raw'], dict) and receipt['raw'],
                        'invalid_checkpoint', 'review receipt fields changed')
                raw = receipt['raw']
                if raw.get('format') == 'review-raw-reference-v1':
                    manifest = store.read_raw_manifest(raw['evidence_ref'], run_id=current.run_id)
                    binding = {key: receipt[key] for key in ('adapter', 'call_ref', 'response_ref', 'ref')}
                    binding.update(status=raw['reported_status'], action_id=slot['action_id'],
                        candidate=slot['candidate'], originating_ref=slot['originating_ref'])
                    truncated = manifest['size'] > state_store.EVIDENCE_LIMIT or not manifest['complete']
                    require(manifest['native_receipt'] == binding and raw['size'] == manifest['size'] and
                            raw['sha256'] == manifest['sha256'] and raw['truncated'] is truncated and
                            receipt['status'] == ('unknown' if truncated else raw['reported_status']),
                            'state_object_corrupt', 'review raw proof changed its original binding or outcome')
    require(store.bytes_read <= state_store.READ_LIMIT, 'state_capacity', 'review proof exceeds current read budget')


def validate_state(state):
    require(isinstance(state, dict) and state.get("protocol") == PROTOCOL,
            "legacy_run_requires_original_runtime", "use original " + str(state.get("protocol") if isinstance(state,dict) else None) + "; package digests " + str({k:v.get("bundle_digest") for k,v in (state.get("packages",{}) if isinstance(state,dict) else {}).items()}))
    require(type(state.get("revision")) is int and state["revision"] >= 0,
            "invalid_checkpoint", "checkpoint revision is invalid")
    require(state.get("mode") in {"stepwise", "continuous"}, "invalid_checkpoint", "invalid mode")
    if state.get('noncreation_retry') is not None:
        read_noncreation_retry(state)
    native_host = state.get('native_host')
    if state.get('stage', 0) >= 2:
        require('workspace_evidence' in state, 'host_evidence_missing', 'original workspace evidence is missing')
        workspace_evidence(state['handoff']['binding'], state['handoff']['controller_ref'], state['workspace_evidence'])
        require(isinstance(native_host, dict) and native_host.get('adapter') in {'collaboration', 'app-server'} and
                isinstance(native_host.get('instance'), str) and bool(native_host['instance']),
                'invalid_checkpoint', 'the original admitted native host is required')
        if native_host['adapter'] == 'collaboration':
            require(set(native_host) == {'adapter', 'instance', 'controller_ref', 'baseline_refs'} and
                    isinstance(native_host['controller_ref'], str) and
                    re.fullmatch(r'/root(?:/[a-z0-9_]+)*', native_host['controller_ref']) and
                    isinstance(native_host['baseline_refs'], list) and
                    all(isinstance(ref, str) and ref != native_host['controller_ref'] and
                        re.fullmatch(r'/root(?:/[a-z0-9_]+)*', ref) for ref in native_host['baseline_refs']) and
                    native_host['baseline_refs'] == sorted(set(native_host['baseline_refs'])),
                    'invalid_checkpoint', 'retain the original collaboration controller identity')
        else:
            require(set(native_host) == {'adapter', 'instance'}, 'invalid_checkpoint', 'retain the app-server connection identity')
    host = state.get("host")
    require(isinstance(host, dict) and type(host.get("generation")) is int and host["generation"] >= 0 and
            host.get("status") in {"unknown", "running", "idle", "turn-completed", "stopped"} and
            isinstance(host.get("seen"), dict) and isinstance(host.get("calls"), dict) and
            isinstance(host.get("query_history", []), list) and
            "last_stop" in host and "proof" in host and "query" in host,
            "invalid_checkpoint", "v4 current host projection is required")
    require(all(key in state for key in ("transaction", "transaction_source", "transaction_result")) and
            (state["transaction"] is not None or state["transaction_source"] is None and state["transaction_result"] is None),
            "invalid_checkpoint", "v4 transaction journal is inconsistent")
    result = state['transaction_result']
    if result is not None:
        require(isinstance(result, dict) and set(result) == {
                'kind', 'request_digest', 'result_digest', 'reference', 'status'} and
                result['kind'] == 'stage-transaction-result-ref-v1' and
                result['request_digest'] == entry.digest(state['transaction']) and
                isinstance(result['result_digest'], str) and re.fullmatch(r'[0-9a-f]{64}', result['result_digest']) and
                isinstance(result['status'], str) and isinstance(result['reference'], dict) and
                set(result['reference']) == {'run_id', 'kind', 'sha256', 'size'} and
                result['reference']['kind'] == 'evidence' and
                isinstance(result['reference']['run_id'], str) and
                isinstance(result['reference']['sha256'], str) and
                re.fullmatch(r'[0-9a-f]{64}', result['reference']['sha256']) and
                type(result['reference']['size']) is int and
                0 < result['reference']['size'] <= state_store.SUBJECT_LIMIT,
                'invalid_checkpoint', 'original B result reference changed')
        # Validate the one named pending proof before public mutation/error
        # handling. Corrupt evidence must not generate a replacement Root.
        read_stage_transaction_result(state)
    require(isinstance(state.get("retained_host_actions", []), list),
            "invalid_checkpoint", "retained host actions must be an ordered list")
    records = state.get('native_finalizations', [])
    validate_saved_host_raw(state)
    validate_saved_reviews(state)
    require(isinstance(records, list), 'invalid_checkpoint', 'native lifecycle receipts must be an ordered list')
    refs, actions = set(), set()
    for record in records:
        require(isinstance(record, dict) and isinstance(record.get('ref'), str) and record['ref'] not in refs and
                isinstance(record.get('thread_id'), str) and record['thread_id'] and isinstance(record.get('actions'), list) and
                isinstance(record.get('delivery_digest'), str) and re.fullmatch(r'[0-9a-f]{64}', record['delivery_digest']) and
                all(isinstance(action, dict) for action in record['actions']) and
                [action.get('operation') for action in record['actions']] in (['finish-native'], ['finish-native', 'close-native']) and
                isinstance(record.get('receipts'), list) and isinstance(record.get('prepare'), dict) and
                record['prepare'].get('ref') == record['ref'] and isinstance(record['prepare'].get('close'), dict),
                'invalid_checkpoint', 'retain one finish and at most one close action for the exact native identity')
        refs.add(record['ref'])
        validate_native_close(record['prepare']['close'], record['ref'], record['thread_id'])
        if 'revoked' in record:
            revoked = record['revoked']
            require(isinstance(revoked, dict) and set(revoked) == {'checkpoint_revision', 'evidence'} and
                    type(revoked['checkpoint_revision']) is int and 0 <= revoked['checkpoint_revision'] <= state['revision'] and
                    isinstance(revoked['evidence'], dict) and revoked['evidence'],
                    'invalid_checkpoint', 'native revocation must retain its observation revision and raw evidence')
        for action in record['actions']:
            require(isinstance(action.get('action_id'), str) and action['action_id'] not in actions and
                    isinstance(action.get('payload'), dict) and action['payload'].get('ref') == record['ref'] and
                    action['payload'].get('thread_id') == record['thread_id'],
                    'invalid_checkpoint', 'native action identity changed')
            if action['operation'] == 'close-native':
                require(action['payload'] == {'ref': record['ref'], 'thread_id': record['thread_id'],
                        **record['prepare']['close']}, 'invalid_checkpoint', 'native close request changed')
            actions.add(action['action_id'])
        queries = record.get('queries', [])
        require(isinstance(queries, list) and sum(not query.get('resolved') for query in queries
                if isinstance(query, dict)) <= 1, 'invalid_checkpoint', 'retain one pending native readback')
        close = record['actions'][-1]
        for query in queries:
            require(isinstance(query, dict) and isinstance(query.get('action_id'), str) and
                    query['action_id'] not in actions and query.get('close_action_id') == close['action_id'] and
                    close['operation'] == 'close-native' and close.get('resolution') == 'completed' and
                    isinstance(query.get('query_for'), str) and re.fullmatch(r'[0-9a-f]{64}', query['query_for']) and
                    query.get('request') == {'tool': 'mcp__codex_app__read_thread', 'arguments': {
                        'threadId': record['thread_id'],
                        'hostId': record['prepare']['close']['request']['arguments']['hostId'],
                        'turnLimit': 1, 'includeOutputs': False}} and
                    ('call_ref' not in query or isinstance(query['call_ref'], str) and query['call_ref']) and
                    (not query.get('resolved') or query.get('resolution') in {'completed', 'failed'}),
                    'invalid_checkpoint', 'native query must retain its original close, target and revocation guard')
            actions.add(query['action_id'])
        record_actions = {action['action_id'] for action in record['actions']}
        for data in record['receipts']:
            require(isinstance(data, dict) and isinstance(data.get('action_id'), str) and
                    data['action_id'] in record_actions and data.get('ref') == record['ref'] and
                    isinstance(data.get('receipt'), dict), 'invalid_checkpoint', 'native receipt attribution is invalid')
            receipt = data['receipt']
            require(receipt.get('ref') == record['ref'] and receipt.get('status') in ('completed', 'unknown', 'failed') and
                    all(isinstance(receipt.get(key), str) and receipt[key] for key in ('adapter', 'call_ref', 'response_ref')) and
                    isinstance(receipt.get('raw'), dict) and receipt['raw'] and
                    ('query' not in receipt or receipt['query'] is True) and
                    ('query_for' not in receipt or receipt.get('query') is True),
                    'invalid_checkpoint', 'native receipt fields are invalid')
            action = next(action for action in record['actions'] if action['action_id'] == data['action_id'])
            if receipt.get('query'):
                require(isinstance(receipt.get('query_for'), str) and re.fullmatch(r'[0-9a-f]{64}', receipt['query_for']),
                        'invalid_checkpoint', 'readback must retain its original revocation guard')
                require(action['operation'] == 'close-native' and action.get('resolved') and
                        action.get('resolution') == 'completed', 'invalid_checkpoint', 'readback cannot replace close completion')
                require(len([query for query in queries if query['close_action_id'] == data['action_id'] and
                             query['query_for'] == receipt['query_for'] and
                             query.get('call_ref') == receipt['call_ref']]) == 1,
                        'invalid_checkpoint', 'readback receipt must name its persisted query invocation')
                if receipt['status'] == 'completed':
                    validate_saved_native_result(receipt, record, action, 'notLoaded', checkpoint=state['checkpoint_path'])
            elif action['operation'] == 'close-native' and receipt['status'] == 'completed':
                validate_saved_native_result(receipt, record, action, checkpoint=state['checkpoint_path'])
            elif receipt['status'] == 'completed' and receipt['raw'].get('format') == 'native-raw-reference-v1':
                validate_saved_native_result(receipt, record, action, 'idle', checkpoint=state['checkpoint_path'])
        close = record['actions'][-1]
        if close['operation'] == 'close-native' and close.get('resolution') == 'completed':
            completed = [data['receipt'] for data in record['receipts'] if data['action_id'] == close['action_id'] and
                         data['receipt']['status'] == 'completed' and not data['receipt'].get('query') and
                         data['receipt']['ref'] == record['ref']]
            require(close['operation'] == 'close-native' and close.get('resolved') and close.get('resolution') == 'completed' and
                    len(completed) == 1, 'invalid_checkpoint', 'verified absence requires the original successful close receipt')
            validate_saved_native_result(completed[0], record, close, checkpoint=state['checkpoint_path'])
        if record.get('verified'):
            require(isinstance(record['verified'], dict) and isinstance(record['verified'].get('inventory'), dict) and
                    close['operation'] == 'close-native' and close.get('resolved') and close.get('resolution') == 'completed',
                    'invalid_checkpoint', 'native closure inventory is invalid')
            inventory = collaboration_inventory(record['verified']['inventory'])
            require(not any(ref == record['ref'] or ref.startswith(record['ref'] + '/') for ref in inventory),
                    'invalid_checkpoint', 'verified native closure must retain the exact inactive subtree inventory')
    baseline_queries = state.get('baseline_thread_queries', [])
    require(isinstance(baseline_queries, list), 'invalid_checkpoint', 'baseline lookups must be an ordered journal')
    query_ids, call_refs, response_refs = set(), set(), set()
    for query in baseline_queries:
        require(isinstance(query, dict) and set(query) == {'operation', 'query_id', 'tool', 'arguments',
                'ref', 'input', 'guard', 'inventory_query_id', 'result'} and
                query['operation'] == 'invoke-baseline-thread-query' and query['tool'] == 'mcp__codex_app__read_thread' and
                isinstance(query['query_id'], str) and query['query_id'] and query['query_id'] not in query_ids and
                isinstance(query['guard'], str) and re.fullmatch(r'[0-9a-f]{64}', query['guard']) and
                isinstance(query['inventory_query_id'], str) and query['inventory_query_id'],
                'invalid_checkpoint', 'retain the original baseline query, inventory and liveness guard')
        thread, host_id = baseline_thread_identity(state, query['input'])
        require(query['ref'] == query['input']['ref'] and
                query['arguments'] == {'threadId': thread, 'hostId': host_id, 'turnLimit': 1},
                'invalid_checkpoint', 'baseline lookup must retain its parent-bound exact native thread')
        query_ids.add(query['query_id'])
        if query['result'] is not None:
            validate_baseline_thread_read(query, query['result'])
            require(query['result']['call_ref'] not in call_refs and query['result']['response_ref'] not in response_refs,
                    'invalid_checkpoint', 'a baseline response cannot belong to multiple queries')
            call_refs.add(query['result']['call_ref']); response_refs.add(query['result']['response_ref'])
    control.validate_current_context(state["control"]["context"])
    indexes = state.get('rework_receipts', {})
    require(isinstance(indexes, dict) and len(indexes) <= 64, 'invalid_checkpoint', 'bounded diagnosis receipt window required')
    for saved in indexes.values():
        entry.fields(saved, {'base_revision', 'receipt'})
        require(type(saved['base_revision']) is int and 0 <= saved['base_revision'] < state['revision'],
                'invalid_checkpoint', 'diagnosis receipt must retain original owner revision')
        entry.fields(saved['receipt'], {'key', 'request_digest', 'request', 'result'})
    for identity, transaction in state.get('control_transactions', {}).items():
        require(type(transaction.get('base_revision')) is int and 0 <= transaction['base_revision'] < state['revision'],
                'invalid_checkpoint', 'control transaction must retain its original revision')
        require(entry.digest(transaction['request']) == identity, 'invalid_checkpoint', 'control transaction identity changed')
        if transaction.get('scope') == 'archive':
            port = transaction.get('port')
            require(isinstance(port, dict) and
                    set(port) == ({'discussion'} if port.get('discussion') is None else {'discussion', 'store_ref'}) and
                    transaction.get('result') is None and
                    type(transaction.get('base_revision')) is int and
                    0 <= transaction['base_revision'] < state['revision'] and
                    isinstance(transaction.get('base_control_digest'), str) and
                    re.fullmatch(r'[0-9a-f]{64}', transaction['base_control_digest']) and
                    transaction.get('handoff', {}).get('controller_ref') == state['control']['context']['controller_ref'],
                    'invalid_checkpoint', 'archive intent lost its finite original authority binding')
            retired = state['control']['context'].get('retired_handoffs', {}).get(transaction.get('handoff_id'))
            if retired is None and transaction.get('port', {}).get('discussion') is not None:
                retired = read_archive_context(transaction['port'], transaction.get('handoff_id'))['retired_handoffs'][transaction['handoff_id']]
            require(retired is not None and retired['handoff_completed'] and
                    transaction.get('handoff_completed') is True and
                    transaction['request']['action'] in {'archive', 'archive-result'} and
                    transaction['request']['evidence']['handoff_id'] == transaction['handoff_id'] and
                    transaction.get('archive_ref') == retired['carrier']['ref'],
                    'invalid_checkpoint', 'archive transaction lacks committed handoff authority')
            require(transaction.get('intent_persisted') is not True or transaction.get('result') is not None or
                    any(op['operation_id'] == transaction.get('operation_id') for op in retired['operations']),
                    'invalid_checkpoint', 'archive intent has no durable result')
            reserve_kind = transaction.get('reserve_kind')
            require(reserve_kind in {'archive:archive', 'archive:read-archive-state',
                                    'control:archive', 'control:archive-result'} and
                    type(transaction.get('reserve_owner')) is bool and
                    transaction.get('reserve_recovery') is (reserve_kind != 'archive:archive') and
                    (not transaction['reserve_owner'] or reserve_kind.startswith('archive:')),
                    'invalid_checkpoint', 'archive reservation lost its original capacity class')
            invocation = (transaction.get('operation_id') if reserve_kind.startswith('archive:') else
                transaction['request']['receipt'].get('reference') or transaction['request']['receipt'].get('call_ref') or
                transaction['request']['receipt'].get('operation_id') or entry.digest(transaction['request']['receipt']))
            store = state_store.open_store(state['checkpoint_path'])
            expected = store.receipt_key(store.open_current(), actor=state['control']['context']['controller_ref'],
                kind=reserve_kind, subject=transaction['handoff_id'], invocation=invocation)
            require(transaction.get('reserve_action') == expected,
                    'invalid_checkpoint', 'archive reservation changed its original run or operation identity')
    for pin in state["packages"].values():
        skill_preflight.verify_identity(pin)
    if state["stage"] == 2:
        stage_scope.validate_scope(state["handoff"]["scope"], state["handoff"]["binding"]["repository"],
            stage=2, requirement_path=state["handoff"]["requirement_identity"]["path"])
        authority = state.get("scope_authority")
        if authority is not None:
            require(authority.get("state") == "sealed" and authority.get("revision") ==
                    authority.get("scope", {}).get("downstream", {}).get("revision") and
                    authority.get("seal_ref") == authority.get("scope", {}).get("downstream", {}).get("seal_ref") and
                    authority.get("proposal_digest") == entry.digest(authority.get("proposal")) and
                    authority.get("seal") == (state["control"]["context"].get("handoff_progress") or {}).get("scope_seal"),
                    "invalid_checkpoint", "scope authority differs from original Controller seal")
            stage_scope.validate_scope(authority["scope"], state["handoff"]["binding"]["repository"], stage=2,
                requirement_path=state["handoff"]["requirement_identity"]["path"],
                candidate_commit=authority["candidate_commit"],
                generated_outputs=generated_outputs_for(authority["proposal"]))
            current_progress = state["control"]["context"].get("handoff_progress") or {}
            verify_scope_authority(authority, initial_seal=current_progress.get("scope_seal"),
                revision_records=current_progress.get("scope_revisions", []),
                requirement_identity=state["handoff"]["requirement_identity"],
                binding=state["handoff"]["binding"],
                decision_records=change_decision_journal(state))
    if state.get('final_integration_recovery') is not None:
        closure = state['final_integration_recovery']['closure_handoff']
        handoff.unseal(closure)
        store = state_store.open_store(state['checkpoint_path'])
        current = store.open_current()
        if closure.get('publication_ref') is not None:
            validate_recovery_publication_ref(closure['publication_ref'], current.run_id)
        index = store.read_evidence(closure['native_source_index_ref'], run_id=current.run_id,
                                    kind='native-source-index')['value']
        require(isinstance(index, dict), 'state_object_corrupt', 'invalid closure native source index')
        proof = store.read_evidence(closure['stop_ref'], run_id=current.run_id)['value']
        require(proof['host']['receipt']['status'] == 'stopped' and
                proof['host']['receipt']['ref'] == closure['role_ref'],
                'host_evidence_missing', 'retain the original stopped closure writer proof')
        if state['native_host']['adapter'] == 'app-server':
            validate_rpc_lifecycle(proof['lifecycle'], closure['expected_entry']['actor']['thread_id'])
    if state.get('implementation_predecessor') is not None:
        require(state['stage'] == 4 and state['handoff']['predecessor'] == state['implementation_predecessor'],
                'handoff_not_ready', 'closure lost its exact accepted implementation')
        prior, _, _ = read_implementation_delivery(state['checkpoint_path'], state['implementation_predecessor'])
        require(state['scope_authority'] == prior['execution_authority'],
                'handoff_not_ready', 'closure changed accepted implementation authority')
        control_git.verify_closure_delivery(state['control']['context'].get('handoff_progress'))
    if state.get('design_predecessor') is not None:
        require(consumed_scope_readiness(state, state['checkpoint_path']) is not None,
                'handoff_not_ready', 'current Stage 3 lost its accepted design predecessor')
    if state["stage"] == 3 and state.get("scope_authority") is not None:
        authority = state["scope_authority"]
        catalog = execution_plan.current_catalog(authority)
        require(isinstance(catalog, dict) and
                authority.get("revision") == authority.get("scope", {}).get("downstream", {}).get("revision"),
                "scope_changed", "current Stage-3 plan authority differs from its sealed catalog")
        plan_authority = (state["control"]["context"].get("handoff_progress") or {}).get("plan_authority")
        if plan_authority is not None:
            require(plan_authority == {"revision": authority["revision"],
                    "authority_digest": entry.digest(authority),
                    "plan_digest": catalog["plan_digest"],
                    "contract_digest": catalog["contract_digest"]},
                    "scope_changed", "control and current Scope Authority disagree")
            require((state["control"]["context"].get("handoff_progress") or {}).get(
                "execution_authority") == authority,
                "scope_changed", "control catalog differs from current Scope Authority")
        transactions = [*state.get("plan_revision_history", {}).values()]
        if state.get("plan_revision_transaction") is not None:
            transactions.append(state["plan_revision_transaction"])
        require(isinstance(state.get("plan_revision_history", {}), dict) and
                len(state.get("plan_revision_history", {})) <= 32,
                "invalid_checkpoint", "plan revision history is not bounded")
        seen = set()
        for transaction in transactions:
            require(isinstance(transaction, dict) and transaction.get("state") in
                    {"stopping", "prepared", "planning-committed", "activated", "rejected"} and
                    isinstance(transaction.get("input"), dict) and
                    transaction.get("input_digest") == entry.digest(transaction["input"]) and
                    isinstance(transaction.get("old_authority"), dict),
                    "invalid_checkpoint", "plan revision intent changed")
            revision_id = transaction["input"].get("revision_id")
            require(isinstance(revision_id, str) and revision_id and revision_id not in seen,
                    "invalid_checkpoint", "duplicate plan revision identity")
            seen.add(revision_id)
            require(isinstance(transaction.get("prior_rejections", []), list),
                    "invalid_checkpoint", "plan revision rejection history changed")
            rejections = [*transaction.get("prior_rejections", [])]
            if transaction["state"] == "rejected":
                rejections.append(transaction.get("rejection"))
                require("snapshot" not in transaction and "write_release" not in transaction and
                        "commit_input" not in transaction, "invalid_checkpoint",
                        "rejected revision cannot hold a planning release")
            for rejection in rejections:
                require(isinstance(rejection, dict) and isinstance(rejection.get("snapshot"), dict) and
                        isinstance(rejection.get("stop"), dict) and
                        isinstance(rejection.get("accepted_executions"), list) and
                        all(isinstance(identity, str) for identity in rejection["accepted_executions"]) and
                        rejection["accepted_executions"] == sorted(set(rejection["accepted_executions"])) and
                        isinstance(rejection["stop"].get("receipt"), dict) and
                        rejection["stop"].get("receipt", {}).get("status") == "stopped" and
                        isinstance(rejection.get("result"), dict) and
                        set(rejection["result"]) == {"code", "message", "retired_paths", "evidence_digest"} and
                        rejection["result"].get("code") == "invalid_plan_revision" and
                        isinstance(rejection["result"].get("retired_paths"), list) and
                        bool(rejection["result"]["retired_paths"]) and
                        all(isinstance(relative, str) for relative in rejection["result"]["retired_paths"]) and
                        rejection["result"]["retired_paths"] == sorted(set(rejection["result"]["retired_paths"])) and
                        rejection["result"]["message"] ==
                        "retired implementation paths still differ from baseline: " +
                        ", ".join(rejection["result"]["retired_paths"]) and
                        set(rejection["result"]["retired_paths"]) <=
                        set(transaction["old_authority"]["scope"]["implementation_paths"]) and
                        rejection["result"].get("evidence_digest") == entry.digest({
                            "input_digest": transaction["input_digest"],
                            "old_authority_digest": entry.digest(transaction["old_authority"]),
                            "snapshot": rejection["snapshot"], "stop": rejection["stop"],
                            "accepted_executions": rejection["accepted_executions"],
                            "retired_paths": rejection["result"]["retired_paths"]}),
                        "invalid_checkpoint", "plan revision rejection evidence changed")
            if transaction["state"] not in {"stopping", "rejected"}:
                require(isinstance(transaction.get("snapshot"), dict) and
                        isinstance(transaction["input"].get("controller_ref"), str) and
                        isinstance(transaction["input"].get("planning_paths"), list),
                        "invalid_checkpoint", "prepared planning snapshot is incomplete")
                expected_release = entry.digest({"revision_id": revision_id,
                    "controller_ref": transaction["input"]["controller_ref"],
                    "planning_paths": transaction["input"]["planning_paths"],
                    "snapshot": transaction["snapshot"],
                    "input_digest": transaction["input_digest"]})
                require(transaction.get("write_release") == expected_release,
                        "invalid_checkpoint", "plan revision release changed")
            if transaction["state"] in {"planning-committed", "activated"}:
                require(isinstance(transaction.get("commit_input"), dict) and
                        isinstance(transaction.get("git_facts"), dict) and
                        isinstance(transaction.get("proposed_authority"), dict) and
                        transaction["commit_input"].get("revision_id") == revision_id and
                        transaction.get("git_facts", {}).get("commit") ==
                        transaction["commit_input"].get("planning_commit") and
                        transaction.get("proposed_authority", {}).get("revision") ==
                        transaction["old_authority"]["revision"] + 1,
                        "invalid_checkpoint", "planning commit journal changed")
            if transaction["state"] == "activated":
                require(transaction.get("control_result", {}).get("evidence", {}).get("revision_id") == revision_id,
                        "invalid_checkpoint", "activated control receipt changed")
        require(all(item.get("state") in {"activated", "rejected"} and key == item["input"]["revision_id"]
                    for key, item in state.get("plan_revision_history", {}).items()),
                "invalid_checkpoint", "historical revision identity changed")
    return state


def retain_entry_pins(request, retained):
    entry_request = request["entry"]
    if retained:
        registry_request = {"stage": entry_request["stage"], "action": entry_request["action"],
            **{key: entry_request[key] for key in ("target_stages", "required_skills") if key in entry_request}}
        registry_request.update(entry.registration_inputs(entry_request)[0])
        registered = skill_preflight.preflight(registry_request)
        current_pins = {name: retained.get(name, identity) for name, identity in registered["packages"].items()}
        entry_request["pinned_packages"] = current_pins
        if "expected_entry" in request:
            require(request["expected_entry"]["packages"] == current_pins, "package_changed", "entry must retain originally pinned package roots")
    if "expected_entry" not in request:
        request["expected_entry"] = entry.resolve(entry_request)


def require_scope_correction_available(outer):
    """Any saved implementation intent permanently closes planning correction."""
    state = outer.get(KEY) or {}
    def implementation_record(value):
        return isinstance(value, dict) and value.get("stage") in (3, 4, "stage3", "stage4")
    records = [state, *state.get("history", [])]
    require(state.get("stage") == 2 and not any(
            implementation_record(record) or implementation_record(record.get("handoff")) or
            implementation_record((record.get("dispatch") or {}).get("handoff")) or
            implementation_record((record.get("transaction") or {}).get("handoff"))
            for record in records) and
            not any(record.get("stage") in ("stage3", "stage4") or
                    record.get("next_stage") in ("stage3", "stage4")
                    for record in outer.get("history", [])) and
            outer.get("current_stage") not in ("stage3", "stage4") and
            not any(key in outer.get("sessions", {}) for key in ("stage3", "stage4")) and
            not implementation_record(outer.get("launch")) and
            not implementation_record(outer.get("launch_intent")) and
            "stage3_prepared" not in outer and
            not any(key in outer.get("prepared_inputs", {}) for key in ("stage3", "stage4")) and
            not any(key in outer.get("stage_results", {}) for key in ("stage3", "stage4")),
            "scope_revision_unavailable", "scope correction closes at the first saved Stage-3 input or launch intent")


def read_scope_readiness(readiness):
    """Read and verify C-owned readiness without exposing selection policy to B."""
    require(isinstance(readiness, dict) and isinstance(readiness.get("checkpoint_path"), str),
            "handoff_not_ready", "current Controller readiness is required")
    path = Path(readiness["checkpoint_path"])
    require(path.is_absolute() and str(path.resolve()) == str(path),
            "invalid_checkpoint", "canonical absolute progression checkpoint required")
    outer = read_record(path)
    if outer.get('store_format') == state_store.FORMAT and outer.get(KEY, {}).get('stage') == 2:
        state = read_projection(path, projection='scope', stage=2).record[KEY]
        actual = scope_readiness(state, path)
        require(actual is not None and actual == readiness, 'handoff_not_ready', 'Controller readiness is stale or changed')
        return state
    if outer.get('store_format') == state_store.FORMAT and outer.get(KEY, {}).get('stage') == 3:
        state = read_projection(path, projection='scope').record[KEY]
        actual = consumed_scope_readiness(state, path)
        require(actual is not None and actual == readiness, 'handoff_not_ready', 'Controller readiness is stale or changed')
        return state
    require(outer.get("version") in (None, 11), "legacy_run_requires_original_runtime",
            "scope readiness requires a standalone record or runner version 11")
    state = outer.get(KEY)
    require(isinstance(state, dict), "handoff_not_ready",
            "current progression checkpoint is unavailable")
    require(state.get("protocol") == PROTOCOL, "legacy_run_requires_original_runtime",
            "retain original progression runtime " + str(state.get("protocol")))
    validate_state(state)
    require(state.get("checkpoint_path") == str(path), "handoff_not_ready",
            "readiness must name the original current checkpoint")
    actual = (scope_readiness(state, path, persisted=outer) if state.get("stage") == 2 else
              consumed_scope_readiness(state, path))
    require(actual is not None and actual == readiness, "handoff_not_ready", "Controller readiness is stale or changed")
    return state


def scope_readiness(state, checkpoint_path, *, persisted=None):
    """Project Stage-3 authority from the current accepted Stage-2 facts.

    This is also used by B's read-only checkpoint verification. The immutable B
    acceptance records the planning result; it cannot itself grant a later lease.
    """
    if state.get('stage') != 2 or state.get('status') != 'accepted' or checkpoint_path is None:
        return None
    path = Path(checkpoint_path).resolve()
    current = read_projection(path, projection='scope', stage=2).record[KEY]
    if state.get('scope_kind') == 'stage2-scope-v1':
        if state != current:
            return None
    elif current['state_digest'] != entry.digest(stage_subject_value(state)):
        return None
    state = current
    if (not state['current_transport'] or state.get("status") != "accepted" or
            state.get("accepted") is None or not state.get("stopped") or
            not state.get("phase_complete") or state.get("stop_requested") or
            state.get("transport_loss") or state.get("business_block")):
        return None
    if state['scope_revision_pending']:
        return None
    authority = state.get("scope_authority")
    publication = state.get("publication")
    if (not isinstance(authority, dict) or authority.get("state") != "sealed" or
            not isinstance(publication, dict) or publication.get("result") is None or
            state['transaction_pending'] or state['pending_kind'] not in (None, 'stage-entry')):
        return None
    scope = authority.get("scope")
    if not isinstance(scope, dict) or scope.get("downstream", {}).get("state") != "sealed":
        return None
    if not scope["downstream"]["implementation_required"] or not scope["implementation_paths"]:
        return None
    verify_scope_authority(authority, initial_seal=state['scope_seal'],
        revision_records=state['scope_revisions'],
        requirement_identity=state["handoff"]["requirement_identity"],
        binding=state["handoff"]["binding"],
        decision_records=change_decision_journal(state))
    accepted = state["accepted"]
    payload = accepted.get("payload", {})
    if (accepted.get("handoff") != state["handoff"] or
            payload.get("planning_commit") != authority.get("candidate_commit") or
            payload.get("planning_merge_commit") != publication["result"].get("merge_commit") or
            state['host_status'] != 'stopped' or state['host_pending']):
        return None
    if state['stop_barrier_pending'] or state['control_pending']:
        return None
    stage_scope.validate_scope(scope, state["handoff"]["binding"]["repository"], stage=3,
        requirement_path=state["handoff"]["requirement_identity"]["path"],
        planning_paths=payload["planning_paths"],
        candidate_commit=authority["candidate_commit"],
        generated_outputs=generated_outputs_for(authority["proposal"]))
    actual = supervision.reconcile_publication({key: publication[key] for key in ("operation", "request", "facts")})
    if actual.get("state") != "planning_published" or actual.get("merge_commit") != payload["planning_merge_commit"]:
        return None
    phase = state["handoff"]["authorization"].get("phase")
    phase_finalization_ref = None
    if phase is not None:
        saved = state["handoff"]
        require(saved['entry']['source']['kind'] == 'discussion', 'authority_missing',
                'phase requires its original discussion attachment')
        result = entry.discussion_protocol.handle({"protocol_version": 1, "operation": "read-phase-run",
            "project_path": entry.discussion_root(saved["expected_entry"]),
            **saved["entry"]["source"]["attachment"], "phase_run_id": phase["run_id"]})
        run = result['phase_run']
        require(run['state'] == 'completed' and any(a['attempt_id'] == phase['attempt_id'] and
                a['state'] == 'completed' for a in run['attempts']),
                'phase_pending', 'complete and finalize the original phase before advancing')
        phase_finalization_ref = entry.digest(result["phase_run"])
    return {"run_id": state["run_id"], "accepted_digest": accepted["digest"], "scope_revision": authority["revision"],
            "scope_seal": authority["seal_ref"], "scope_authority": copy.deepcopy(authority),
            "execution_plan": copy.deepcopy(authority.get("execution_plan")),
            "publication": {"planning_commit": payload["planning_commit"],
                            "planning_merge_commit": payload["planning_merge_commit"],
                            "publication_request_digest": entry.digest(publication["request"])},
            "stop_evidence_ref": state['stop_evidence_ref'],
            "phase_finalization_ref": phase_finalization_ref,
            "controller_ref": state["handoff"]["controller_ref"],
            "control_revision": state['control_revision'],
            "checkpoint_path": str(path), "checkpoint_revision": state["revision"]}


def consumed_scope_readiness(state, checkpoint_path):
    """Verify the one Stage-2 readiness atomically consumed by current Stage 3."""
    if state.get("stage") != 3 or not isinstance(state.get("consumed_readiness"), dict):
        return None
    readiness = state["consumed_readiness"]
    path = Path(checkpoint_path).resolve()
    if state.get('design_predecessor') is not None:
        old = read_design_predecessor(state['design_predecessor'], path)
        saved = state['handoff']
        if (state['checkpoint_path'] != str(path) or readiness != old['readiness'] or
                saved.get('readiness') != readiness or saved.get('predecessor') != state['design_predecessor'] or
                saved['binding'] != old['handoff']['binding'] or
                saved['controller_ref'] != old['controller_ref'] or
                saved['requirement_identity'] != old['handoff']['requirement_identity'] or
                any(saved['scope'][key] != old['scope_authority']['scope'][key]
                    for key in ('downstream', 'implementation_paths', 'closure_paths'))):
            return None
        current = state.get('scope_authority')
        if current == old['scope_authority']:
            return copy.deepcopy(readiness)
        if state.get('recovery_predecessor') is not None:
            accepted, _, _ = read_implementation_delivery(path, state['recovery_predecessor'])
            if current == accepted['execution_authority'] and accepted['design_predecessor'] == state['design_predecessor']:
                return {**copy.deepcopy(readiness), 'scope_revision': current['revision'],
                    'scope_seal': current['seal_ref'], 'scope_authority': copy.deepcopy(current),
                    'execution_plan': copy.deepcopy(current['execution_plan'])}
        transaction = state.get('plan_revision_transaction') or {}
        if (transaction.get('state') != 'activated' or transaction.get('proposed_authority') != current or
                current.get('execution_plan', {}).get('contract_digest') != readiness['execution_plan']['contract_digest'] or
                current.get('scope', {}).get('baseline') != old['scope_authority']['scope']['baseline']):
            return None
        return {**copy.deepcopy(readiness), 'scope_revision': current['revision'],
                'scope_seal': current['seal_ref'], 'scope_authority': copy.deepcopy(current),
                'execution_plan': copy.deepcopy(current['execution_plan'])}

    if (state.get("checkpoint_path") != str(path) or readiness.get("checkpoint_path") != str(path) or
            state.get("handoff", {}).get("readiness") != readiness or
            not state.get("history")):
        return None
    old = state["history"][-1]
    if state.get('final_integration_recovery') is not None:
        matches = [item for item in state['history'] if item.get('stage') == 2 and
                   item.get('revision') == readiness.get('checkpoint_revision') and
                   item.get('accepted', {}).get('digest') == readiness.get('accepted_digest')]
        if not matches or any(item != matches[0] for item in matches):
            return None
        old = matches[0]
    if (old.get("stage") != 2 or old.get("revision") != readiness.get("checkpoint_revision") or
            old.get("status") != "accepted" or not old.get("stopped") or not old.get("phase_complete") or
            old.get("accepted", {}).get("digest") != readiness.get("accepted_digest") or
            old.get("scope_authority") != readiness.get("scope_authority") or
            state["handoff"].get("predecessor") != old.get("accepted") or
            state["handoff"].get("binding") != old.get("handoff", {}).get("binding") or
            state["handoff"].get("scope", {}).get("downstream") != old["scope_authority"]["scope"]["downstream"] or
            state["handoff"]["scope"].get("implementation_paths") != old["scope_authority"]["scope"]["implementation_paths"] or
            state["handoff"]["scope"].get("closure_paths") != old["scope_authority"]["scope"]["closure_paths"]):
        return None
    publication = old.get("publication") or {}
    payload = old["accepted"].get("payload", {})
    if (publication.get("result", {}).get("merge_commit") != payload.get("planning_merge_commit") or
            readiness.get("publication") != {"planning_commit": payload.get("planning_commit"),
                "planning_merge_commit": payload.get("planning_merge_commit"),
                "publication_request_digest": entry.digest(publication.get("request"))} or
            readiness.get("stop_evidence_ref") != entry.digest(old.get("host", {}).get("last_stop"))):
        return None
    original_progress = old["control"]["context"].get("handoff_progress") or {}
    verify_scope_authority(readiness["scope_authority"],
        initial_seal=original_progress.get("scope_seal"),
        revision_records=original_progress.get("scope_revisions", []),
        requirement_identity=old["handoff"]["requirement_identity"],
        binding=old["handoff"]["binding"],
        decision_records=change_decision_journal(state))
    current = state.get("scope_authority")
    if current == readiness["scope_authority"]:
        return copy.deepcopy(readiness)
    transaction = state.get("plan_revision_transaction") or {}
    if (transaction.get("state") != "activated" or
            transaction.get("proposed_authority") != current or
            current.get("execution_plan", {}).get("contract_digest") !=
            readiness["execution_plan"]["contract_digest"] or
            current.get("scope", {}).get("baseline") !=
            readiness["scope_authority"]["scope"]["baseline"]):
        return None
    updated = copy.deepcopy(readiness)
    updated.update(scope_revision=current["revision"], scope_seal=current["seal_ref"],
                   scope_authority=copy.deepcopy(current),
                   execution_plan=copy.deepcopy(current["execution_plan"]))
    return updated


def corrected_scope_authority(transaction, receipt):
    scope = copy.deepcopy(transaction["proposed_scope"])
    scope["downstream"].update(state="sealed", seal_ref=receipt["revision_ref"])
    old = transaction["old_authority"]
    approved = transaction.get("approved_change_evidence")
    return {**copy.deepcopy(old), "state": "sealed",
        "revision": scope["downstream"]["revision"], "scope": scope,
        "proposal": copy.deepcopy(transaction["envelope"]),
        "proposal_digest": entry.digest(transaction["envelope"]),
        "execution_plan": (execution_plan.authority_envelope(transaction["execution_plan"],
            (old["execution_plan"]["original_scope_revision"] if old.get("execution_plan")
             else old["revision"]))
            if transaction["execution_plan"] is not None else None),
        "generated_outputs": copy.deepcopy(transaction["envelope"].get(
            "generated_outputs", handoff.authority_generated_outputs(old))),
        "seal_ref": receipt["revision_ref"],
        "revisions": [*old.get("revisions", []), copy.deepcopy(receipt)],
        "approved_changes": [*old.get("approved_changes", []), *([approved] if approved else [])]}


def verify_scope_authority(authority, *, initial_seal=None, revision_records=None,
                           requirement_identity=None, binding=None, decision_records=None):
    """Bind the current manifest to durable Controller receipts, not its own fields."""
    require(isinstance(authority, dict) and authority.get("state") == "sealed" and
            type(authority.get("revision")) is int and authority["revision"] >= 1 and
            isinstance(authority.get("scope"), dict) and
            authority.get("proposal_digest") == entry.digest(authority.get("proposal")),
            "scope_changed", "scope proposal identity changed")
    scope = authority["scope"]
    downstream = scope["downstream"]
    require(downstream["state"] == "sealed" and downstream["revision"] == authority["revision"] and
            downstream["proposal_ref"] == authority["proposal_digest"] and
            downstream["seal_ref"] == authority["seal_ref"],
            "scope_changed", "current downstream scope differs from its authority references")
    if downstream["implementation_required"]:
        catalog = execution_plan.current_catalog(authority)
        require(isinstance(catalog, dict) and
                authority["proposal"].get("execution_plan") ==
                {"plan_digest": catalog["plan_digest"], "contract_digest": catalog.get("contract_digest")},
                "scope_changed", "sealed function plan differs from proposal identity")
    seal = authority.get("seal")
    require(isinstance(seal, dict) and seal.get("seal_ref") ==
            control.digest({key: value for key, value in seal.items() if key != "seal_ref"}) and
            (initial_seal is None or seal == initial_seal),
            "scope_changed", "original Controller seal differs from current control history")
    evidence = seal["evidence"]
    require(evidence["candidate_commit"] == authority["candidate_commit"] and
            (requirement_identity is None or evidence["requirement_identity"] == requirement_identity) and
            (binding is None or evidence["binding"] == binding),
            "scope_changed", "sealed candidate, requirement or worktree changed")
    approved = authority.get("approved_changes", [])
    require(isinstance(approved, list) and all(isinstance(item, dict) and
            item.get("digest") == entry.digest(item.get("record")) for item in approved),
            "scope_changed", "approved change evidence changed")
    def verify_approved(decision):
        if decision.get("kind") != "reference-to-approved-change":
            return
        matches = [item["record"] for item in approved
            if item["record"].get("decision", {}).get("reference") == decision.get("approved_change_ref") and
               item["record"].get("decision", {}).get("answer") == "approve-change" and
               item["record"].get("pending_kind") == "user-decision" and
               item["record"].get("controller_ref") == seal["controller_ref"] and
               item["record"].get("requirement_identity") == evidence["requirement_identity"]]
        require(len(matches) == 1 and (decision_records is None or matches[0] in decision_records.values()),
                "approved_change_missing", "approved change no longer matches original user-decision journal")
    verify_approved(evidence["decision"])
    if authority["revision"] == 1:
        require(authority["seal_ref"] == seal["seal_ref"] and
                evidence["proposal_digest"] == authority["proposal_digest"] and
                evidence["manifest_digest"] == entry.digest(downstream["manifest"]) and
                (authority["proposal"].get("candidate_commit", authority["candidate_commit"]) ==
                 authority["candidate_commit"]),
                "scope_changed", "manifest differs from original Controller seal")
    else:
        revisions = authority.get("revisions")
        require(isinstance(revisions, list) and len(revisions) == authority["revision"] - 1 and
                isinstance(revisions[-1], dict) and
                revisions[-1].get("revision_ref") == control.digest({key: value for key, value in
                    revisions[-1].items() if key != "revision_ref"}) and
                authority["seal_ref"] == revisions[-1]["revision_ref"] and
                (revision_records is None or revisions == revision_records),
                "scope_changed", "latest Controller revision receipt changed")
        latest = revisions[-1]["evidence"]
        verify_approved(latest["decision"])
        proposed = copy.deepcopy(scope)
        proposed["downstream"].update(state="proposed", seal_ref=None)
        require(latest["expected_scope_revision"] + 1 == authority["revision"] and
                latest["new_scope_digest"] == entry.digest(proposed) and
                latest["scope_diff_digest"] == entry.digest(authority["proposal"]["diff"]) and
                latest["candidate_commit"] == authority["candidate_commit"] and
                latest["requirement_identity"] == evidence["requirement_identity"] and
                latest["binding"] == evidence["binding"] and
                authority["proposal"]["candidate_commit"] == authority["candidate_commit"],
                "scope_changed", "current manifest differs from authorized revision snapshot")
    return authority


def native_finalization_lookup(record):
    action = record['actions'][-1]
    if action.get('resolved') and action.get('resolution') == 'failed':
        failure = next((data['receipt'] for data in reversed(record['receipts'])
                        if data['action_id'] == action['action_id'] and data['receipt']['status'] == 'failed' and
                        not data['receipt'].get('query')), None)
        require(failure is not None, 'invalid_checkpoint', 'failed native action requires its original failed receipt')
        return {'operation': 'await-host-recovery', 'ref': record['ref'], 'action': copy.deepcopy(action),
                'receipt': copy.deepcopy(failure),
                'reason': 'The original ' + action['operation'] + ' failed; its business owner must handle host recovery.'}
    result = {'operation': 'lookup-exact-native-action', 'action': copy.deepcopy(action)}
    if record.get('revoked') and action['operation'] == 'close-native' and action.get('resolution') == 'completed':
        query = next((item for item in record.get('queries', []) if not item.get('resolved')), None)
        if query is None:
            result['requires_query_prepare'] = True
        else:
            state_store.open_store(action['checkpoint']).require_reserve(query['action_id'], recovery=True)
            result.update(query=True, query_for=query['query_for'], query_action_id=query['action_id'],
                          **copy.deepcopy(query['request']))
            if query.get('call_ref'):
                result['query_call_ref'] = query['call_ref']
    return result


def native_finalization_action(state):
    record = next((item for item in state.get('native_finalizations', []) if not item.get('verified') or item.get('revoked')), None)
    if record is None:
        return None
    action = record['actions'][-1]
    if (not action.get('resolved') or action.get('resolution') != 'completed' or record.get('revoked') or
            state.get('stop_requested') or state['status'] in {'pausing', 'paused', 'cancelling', 'cancelled'}):
        return native_finalization_lookup(record)
    return {'operation': 'lifecycle-query' if state.get('lifecycle_snapshot') is None else 'reconcile-stop-barrier',
            'ref': record['ref'], 'action_id': action['action_id'], 'prepare': copy.deepcopy(record['prepare'])}


def _view(state, next_action=None, acknowledged=False, *, verified_readiness=None):
    if next_action is None and state.get('business_block') and state['status'] == 'blocked':
        next_action = native_finalization_action(state)
    if state.get("business_block") and state["status"] == "blocked" and (next_action or {}).get('operation') not in {
            'invoke-recovery-host', 'lookup-recovery-host', 'activate-dispatch-recovery', 'prepare-dispatch-recovery',
            'control-effects', 'await-host-recovery', 'lookup-exact-native-action', 'lifecycle-query',
            'invoke-lifecycle-query', 'reconcile-stop-barrier'}:
        next_action = {"operation": "await-business-recovery", "subject": state["business_block"]["subject"]}
    if next_action is None and state.get("recovery_action"):
        next_action = state["recovery_action"]
    if next_action is None and state["step"] == "host-response" and state.get("action"):
        next_action = {"operation": "lookup-exact-action", "action": state["action"]}
    if next_action is None and state["step"] == "continue" and state.get("host", {}).get("query"):
        query = state["host"]["query"]
        next_action = ({"operation": "await-host-recovery", "query": query, "ref": query["payload"]["ref"]}
                       if query.get("resolved") else query)
    if next_action is None and state["step"] == "publication-ready":
        ready = state["publication_candidate"]
        data = {"candidate_commit": ready["payload"]["candidate_commit"], "reference": ready["decision"]["reference"]}
        if state["stage"] == 4:
            data["expected_target_head"] = ready["target_head"]
        next_action = {"operation": "publication", "data": data}
    if next_action is None and state["step"] == "publication-complete":
        next_action = {"operation": "receive-publication", "data": {}}
    if next_action is None:
        next_action = native_finalization_action(state)
    readiness = (verified_readiness if verified_readiness is not None else
        scope_readiness(state, state.get("checkpoint_path"))
        if state.get("stage") == 2 and state.get("status") == "accepted" else None)
    planning_complete = state.get("stage") == 2 and state.get("status") == "accepted" and state.get("accepted") is not None
    revision_stable = (state.get("scope_revision_transaction") or {}).get("state") in (None,)
    downstream_ready = (readiness is not None if state.get("stage") == 2 else
        revision_stable and state["status"] == "accepted" and state.get('stopped', False) and state.get("phase_complete", True) and
        bool((state.get("accepted") or {}).get("downstream_ready")))
    if state['stage'] == 3 and downstream_ready:
        store = state_store.open_store(state['checkpoint_path'])
        current = store.open_current()
        reference = current.root.get('delivery') if current is not None else None
        delivery = store.read_predecessor(current, reference) if reference is not None else None
        downstream_ready = (delivery is not None and
            implementation_outer_ready(current.root['outer'], delivery['stop_transport']))
    result = {"protocol": PROTOCOL, "revision": state["revision"], "status": state["status"],
              "step": state["step"], "pending": state.get("pending"), "next_action": next_action,
              "planning_complete": planning_complete, "handoff_ready": readiness is not None,
              "downstream_ready": downstream_ready, "acknowledged": acknowledged}
    if planning_complete:
        stable_scope = (state.get("scope_revision_transaction") or {}).get("state") in (None,)
        result["route"] = "stage-3" if readiness is not None else "planning-only" if stable_scope and (state.get("scope_authority") or {}).get("scope", {}).get("downstream", {}).get("implementation_required") is False else "pending"
        if readiness is not None:
            result["readiness"] = readiness
    if state["status"] == "accepted" and state.get('stopped', False) and state.get("accepted") is not None and (state["stage"] == 2 or downstream_ready) and state.get("phase_complete", True):
        if state["stage"] == 4:
            result["workflow_completion"] = verify_completion(state)
        result["completion"] = (handoff._render_verified(state["accepted"], readiness)
            if state["stage"] == 2 else handoff.render(state["accepted"],
                checkpoint_path=state["checkpoint_path"] if state["stage"] == 3 else None))
    elif state["status"] == "accepted" and not state.get("phase_complete", True) and next_action is None:
        result["next_action"] = {"operation": "complete-original-phase", "phase": state["handoff"]["authorization"].get("phase")}
    if state.get("business_block"):
        result["business_block"] = copy.deepcopy(state["business_block"])
    if state.get("error"):
        result["error"] = state["error"]
    return result


def verify_phase_completed(state):
    saved = state["handoff"]
    phase = saved["authorization"].get("phase")
    if phase is None:
        # 0/1 wrapper identity lives in the original selected control plan.
        context = state['control']['context']
        attempt = state['dispatch']['request']['attempt']
        plans = [(slot.get('handoff_progress') or {}).get('plan', {})
                 for slot in (context, context.get('successor_control', {}))]
        plans.extend(record['plan'] for record in context.get('retired_handoffs', {}).values())
        matches = [plan['entry_authority'] for plan in plans if plan.get('plan_id') == attempt]
        require(state['stage'] >= 2 or matches, 'identity_mismatch', 'original dedicated completion authority unavailable')
        require(not matches or all(item == matches[0] for item in matches), 'identity_mismatch', 'conflicting original completion authority')
        authority = matches[0] if matches else {}
        if authority.get("kind") != "wrapper-phase-run":
            return
        phase = {"run_id": authority["run_id"], "attempt_id": authority["attempt_id"]}
    source = saved["entry"]["source"]
    require(source["kind"] == "discussion", "authority_missing", "phase requires its original discussion attachment")
    root = entry.discussion_root(saved["expected_entry"])
    result = entry.discussion_protocol.handle({"protocol_version": 1, "operation": "read-phase-run", "project_path": root,
        **source["attachment"], "phase_run_id": phase["run_id"]})
    run = result["phase_run"]
    require(run["state"] == "completed" and any(a["attempt_id"] == phase["attempt_id"] and a["state"] == "completed" for a in run["attempts"]),
            "phase_pending", "complete and finalize the original phase before advancing")


def verify_completion(state):
    """Re-read final Git, B/control acceptance and the original phase authority."""
    validate_state(state)
    require(state["stage"] == 4 and state["status"] == "accepted", "result_incomplete", "final B acceptance is required")
    require(state.get('stopped') is True, 'host_evidence_missing', 'final release requires the complete native stop barrier')
    outer = read_projection(state['checkpoint_path'], projection='identity').record
    current = outer[KEY]
    require(current['stage_subject'] == state['stage_subject'] and current['revision'] == state['revision'] and
            current['handoff']['digest'] == state['handoff']['digest'],
            'stale_checkpoint', 'completion must retain the current closure authority')
    owner = Progress(state['checkpoint_path'], {**outer, KEY: state})
    require(owner.stop_intent() is None and not owner.stop_barrier_pending(),
            'host_evidence_missing', 'current runner or native state revoked closure completion')
    accepted = state["accepted"]
    handoff.unseal(accepted)
    require(state["dispatch"]["acceptance"] == accepted, "result_changed", "final acceptance differs from the original dispatch")
    saved, payload = accepted["handoff"], accepted["payload"]
    verify_phase_completed(state)
    handoff.verify_result(4, payload, saved["binding"], saved["scope"], saved["binding"]["repository"], accepted["role_ref"])
    owner = state["control"]["context"]["handoff_progress"]
    require(owner["state"] == "completed" and owner["merge"] == payload["merge_commit"],
            "control_pending", "original closure control has not completed")
    publication = state.get("publication")
    require(publication is not None and publication.get("result") is not None, "publication_pending", "original publication is not reconciled")
    actual = supervision.reconcile_publication({key: publication[key] for key in ("operation", "request", "facts")})
    require(actual["state"] == "completed" and actual["merge_commit"] == payload["merge_commit"] and
            publication["completion_payload"] == payload, "publication_pending", "publication or cleanup differs from the accepted delivery")
    # Only the exact accepted implementation and its planning predecessor are
    # needed after cleanup. Their old worktree and full Controller state are not.
    reference = state.get('implementation_predecessor')
    require(reference is not None and reference == saved.get('predecessor'),
            'handoff_not_ready', 'completion requires its exact accepted implementation')
    implementation, _, _ = read_implementation_delivery(state['checkpoint_path'], reference)
    require(implementation['payload']['candidate_commit'] == payload['candidate_commit'],
            'source_changed', 'completion differs from accepted implementation')
    planning = implementation['design_predecessor']
    if planning is not None:
        previous = read_design_predecessor(planning, state['checkpoint_path'])
        proof = previous['accepted']['payload']
        root = saved['binding']['repository']
        handoff.ancestor(root, proof['planning_commit'], proof['planning_merge_commit'])
        handoff.ancestor(root, proof['planning_merge_commit'], payload['merge_commit'])
    return {"completed": True, "merge_commit": payload["merge_commit"], "cleanup": actual["cleanup"],
            "acceptance": accepted["digest"], "phase_complete": True}


class Progress:
    def __init__(self, path, outer):
        self.path, self.outer = Path(path), outer
        self.state = outer.get(KEY)
        self._verified_readiness = None
        self._receipt_action = None
        self._commit_receipts = []
        self._authority_revision = getattr(outer, 'authority_revision', 0)
        self._save_error = None

    def host_stopped(self):
        # Compatibility projection, never a business-replay liveness input.
        # Before any issue, or after exact non-creation, there is no writer.
        root_stopped = (self.state["host"]["status"] == "stopped" or
                                 self.state.get("host_action") is None or
                                 (self.state.get("dispatch") or {}).get("status") == "not-created")
        return root_stopped and not self.stop_barrier_pending()

    def save(self):
        self._verified_readiness = None
        self.state['stopped'] = self.host_stopped()
        if not self.state['stopped'] and self.state['status'] in {'paused','cancelled'}:
            self.state['status'] = 'cancelling' if self.stop_intent() == 'cancelling' else 'pausing'
        if self.state.get("business_block") and self.state["status"] not in {"pausing", "paused", "cancelling", "cancelled"}:
            self.state["status"] = "blocked"
            self.state["error"] = copy.deepcopy(self.state["business_block"]["error"])
        previous_revision = self.state["revision"]
        self.state["revision"] += 1
        self.outer[KEY] = self.state
        try:
            committed = save_current_record(self.path, self.outer, previous_revision,
                expected_authority_revision=self._authority_revision, receipt_action=self._receipt_action,
                receipts=self._commit_receipts)
            self._authority_revision = committed.revision
            self._commit_receipts = []
            self.release_settled_reserves()
        except entry.ERROR_TYPES + (control.ControlError,) as error:
            # A failed return does not tell us whether the original Root CAS
            # applied. Reconcile only its validated pointer, then exit this
            # operation with the primary error. An error-block save could turn
            # uncommitted receipt membership into a false business ACK.
            self._save_error = error
            try:
                current = state_store.open_store(self.path).open_current()
                self._authority_revision = current.revision if current else 0
                self.state['revision'] = current.root['metadata']['revision'] if current else previous_revision
                if current and all(receipt in current.root.get('receipts', [])
                                   for receipt in self._commit_receipts):
                    self._commit_receipts = []
            except entry.ERROR_TYPES + (control.ControlError,) as reconciliation_error:
                raise error from reconciliation_error
            raise

    def release_settled_reserves(self):
        pending = {action['action_id'] for action in self.unresolved_host_actions()}
        pending.update(slot['action']['action_id'] for slot in self.state.get('allocations', {}).values()
            if slot.get('action') and (slot.get('record') or {}).get('status') in {'prepared', 'unknown'})
        pending.update(action['action_id'] for record in self.state.get('native_finalizations', [])
            for action in record['actions'] if not action.get('resolved'))
        pending.update(query['action_id'] for record in self.state.get('native_finalizations', [])
            for query in record.get('queries', []) if not query.get('resolved'))
        pending.update(event['receipt_action'] for event in self.state.get('events', {}).values()
            if not event.get('applied') and event.get('receipt_action'))
        query = self.state['host'].get('query')
        if query and not query.get('resolved'):
            pending.add(query['action_id'])
        for group in [self.state.get('review_activity', {}), *self.state.get('review_history', [])]:
            for slot in group.values():
                if not slot.get('business_settled'):
                    pending.add(slot['action_id'])
                if slot.get('raw_ingress'):
                    pending.add(slot['raw_ingress']['receipt_action'])
                if slot.get('query') and not slot['query'].get('resolved'):
                    pending.add(slot['query']['action_id'])
        pending.update(transaction['reserve_action'] for transaction in self.state.get('control_transactions', {}).values()
            if transaction.get('reserve_action') and transaction.get('result') is None)
        state_store.open_store(self.path).release_settled_reserves(pending)

    def current_verified_readiness(self):
        saved = self._verified_readiness
        return copy.deepcopy(saved[1]) if saved is not None and saved[0] == self.state["revision"] else None

    def receipt_store(self, kind, subject, invocation):
        store = state_store.open_store(self.path, receipt_action=self._receipt_action)
        current = store.open_current()
        key = store.receipt_key(current, actor=self.state['control']['context']['controller_ref'],
                                kind=kind, subject=subject, invocation=invocation)
        return store, current, key

    def stop_barrier_pending(self):
        """Derive writer closure from the existing roles and invocation journal."""
        s = self.state
        pending = []
        pending.extend({'kind': 'host-raw-ingress', 'event_id': event_id,
                        'action_id': event['raw_ingress']['action_id']}
                       for event_id, event in s.get('events', {}).items()
                       if not event.get('applied') and (event.get('raw_ingress') or {}).get('admitted') is True)
        for record in s.get('native_finalizations', []):
            for action in record['actions']:
                if not action.get('resolved'):
                    pending.append({'kind': 'native-finalization-action', 'ref': record['ref'], 'action_id': action['action_id']})
            for query in record.get('queries', []):
                if not query.get('resolved'):
                    pending.append({'kind': 'native-finalization-query', 'ref': record['ref'], 'action_id': query['action_id']})
        recovery = (s.get('control', {}).get('context', {}).get('handoff_progress') or {}).get('recovery')
        if recovery and recovery['state'] in {'dispatch-pending', 'dispatch-unknown'}:
            pending.append({'kind':'replacement-call', 'intent_id':recovery['intent']['intent_id']})
        pending.extend({'kind':'prior-carrier-activity','carrier_thread':thread} for thread in s.get('prior_carrier_activity', {}))
        pending.extend({'kind':'native-stop-proof-revoked','ref':ref} for ref in s.get('native_adverse', {}))
        for action in self.unresolved_host_actions():
            pending.append({'kind':'host-action', 'action_id':action['action_id']})
        selected = control.selected_control(s['control']['context'],
            {'attempt':s['dispatch']['request']['attempt']}) if s.get('dispatch') else s.get('control', {}).get('context', {})
        for execution in (selected.get('handoff_progress') or {}).get('executions', []):
            if not execution.get('stopped'):
                pending.append({'kind':'execution', 'task_id':execution['task_id'], 'ref':execution.get('agent_ref')})
        for identity, slot in s.get('allocations', {}).items():
            if slot.get('transaction') is not None or slot.get('record') is None:
                pending.append({'kind':'allocation', 'allocation_id':identity})
        review_groups = [s.get('review_activity', {}), *s.get('review_history', [])]
        for group in review_groups:
            for axis, slot in group.items():
                if slot is not None and not slot.get('stopped'):
                    pending.append({'kind':'reviewer', 'axis':axis, 'ref':slot.get('ref'), 'action_id':slot['action_id']})
                if slot is not None and slot.get('query') and not slot['query'].get('resolved'):
                    pending.append({'kind': 'review-lookup', 'axis': axis, 'ref': slot.get('ref'),
                                    'action_id': slot['query']['action_id'], 'original_action_id': slot['action_id']})
        if s.get('stage', 0) >= 2 and s.get('dispatch') and self.bound_ref() is not None:
            if s.get('native_host', {}).get('adapter') == 'collaboration':
                return pending + self.collaboration_pending()
            snapshot = s.get('lifecycle_snapshot')
            transport = self.outer.get('transport')
            if transport and transport.get('lookup_blocked'):
                pending.append({'kind':'current-descendants-lookup-failed'})
            if snapshot is None:
                pending.append({'kind':'current-descendants-missing'})
            elif transport and (snapshot['instance'] != transport.get('instance') or
                                snapshot['sequence'] != transport.get('native_event_sequence', 0) or transport.get('state') == 'lost'):
                pending.append({'kind':'current-descendants-stale'})
            else:
                refs = {self.bound_ref()} | self.native_refs()
                refs.update(s.get('recovered_native_refs', []))
                for transaction in s.get('control_transactions', {}).values():
                    if transaction.get('result') is not None and transaction['request']['action'] == 'recover-dispatch':
                        refs.update(transaction['result']['recovery']['host_evidence']['stopped_refs'])
                recovery = (selected.get('handoff_progress') or {}).get('recovery')
                if recovery and recovery['state'] == 'ready':
                    refs.add(recovery['receipts'][-1]['ref'])
                refs.update(e['agent_ref'] for e in (selected.get('handoff_progress') or {}).get('executions', []) if e.get('agent_ref'))
                refs.update(slot['ref'] for group in review_groups for slot in group.values() if slot and slot.get('ref'))
                aliases, calls = {}, set()
                for event in snapshot['events']:
                    params = event.get('params', {})
                    item = params.get('item', {})
                    if item.get('type') != 'subAgentActivity':
                        continue
                    if item.get('agentPath') and item.get('agentThreadId'):
                        if item['agentPath'] not in refs:
                            pending.append({'kind':'unbound-native-descendant','ref':item['agentPath']})
                        if aliases.get(item['agentPath'], item['agentThreadId']) != item['agentThreadId']:
                            pending.append({'kind':'native-identity-conflict','ref':item['agentPath']})
                        aliases[item['agentPath']] = item['agentThreadId']
                    if event.get('method') == 'item/started':
                        calls.add((params.get('threadId'), item.get('id')))
                    if event.get('method') == 'item/completed':
                        calls.discard((params.get('threadId'), item.get('id')))
                expected = {aliases.get(ref, ref.removeprefix('codex-thread:')) for ref in refs}
                actual = {}
                for page in snapshot['pages']:
                    for thread in page['response']['data']:
                        actual[thread['id']] = thread.get('status')
                if set(actual) != expected:
                    pending.append({'kind':'unreconciled-descendants', 'expected':sorted(expected), 'actual':sorted(actual)})
                for ref, status in actual.items():
                    if status != {'type':'idle'}:
                        pending.append({'kind':'native-turn-not-stopped', 'thread':ref, 'status':status})
                if calls:
                    pending.append({'kind':'native-calls-pending', 'calls':sorted(calls)})
        return pending

    def native_refs(self, state=None):
        """Registered writers, including replaced roles and historical reviewers."""
        s = self.state if state is None else state
        refs = set()
        recovery_prior = s.get('implementation_predecessor') or s.get('recovery_predecessor')
        if recovery_prior is not None and s.get('native_host', {}).get('adapter') == 'collaboration':
            prior, _, _ = read_implementation_delivery(self.path, recovery_prior)
            refs.update(prior['native_refs'])
        if s.get('design_predecessor') is not None and s.get('native_host', {}).get('adapter') == 'collaboration':
            prior = read_design_predecessor(s['design_predecessor'], self.path)
            refs.add(prior['accepted']['role_ref'])
        refs.update(s.get('recovered_native_refs', []))
        if s.get('stage', 0) < 2:
            return refs
        if s.get('dispatch') and s['dispatch'].get('request'):
            selected = control.selected_control(s['control']['context'], {'attempt': s['dispatch']['request']['attempt']})
            carrier = selected.get('carrier')
            if s['handoff']['role'] == 'execution-agent':
                ref, _, _ = dispatch.matching(s['dispatch'], s['control']['context'])
                if ref:
                    refs.add(ref)
            elif carrier and carrier.get('ref'):
                refs.add(carrier['ref'])
        else:
            selected = s.get('control', {}).get('context', {})
        progress = selected.get('handoff_progress') or {}
        refs.update(item['agent_ref'] for item in progress.get('executions', []) if item.get('agent_ref'))
        refs.update(item['agent_ref'] for item in progress.get('carried_results', [])
                    if item.get('agent_ref'))
        for source in progress.get('plan_revision_sources', []):
            for item in source['executions'] + source['carried_results']:
                if item.get('agent_ref'):
                    refs.add(item['agent_ref'])
        recovery = progress.get('recovery')
        if recovery and recovery['state'] == 'ready':
            refs.add(recovery['receipts'][-1]['ref'])
        for transaction in s.get('control_transactions', {}).values():
            if transaction.get('result') is not None and transaction['request']['action'] == 'recover-dispatch':
                refs.update(transaction['result']['recovery']['host_evidence']['stopped_refs'])
        for group in [s.get('review_activity', {}), *s.get('review_history', [])]:
            refs.update(slot['ref'] for slot in group.values() if slot and slot.get('ref'))
        return refs

    def collaboration_guard(self):
        # Business readiness/acceptance saves do not change liveness. New host
        # calls, allocations, reviews and adverse observations do.
        s = self.state
        selected = control.selected_control(s['control']['context'], {'attempt': s['dispatch']['request']['attempt']})
        return entry.digest({key: s.get(key) for key in (
            'host', 'host_action', 'retained_host_actions', 'allocations', 'review_activity',
            'review_history', 'native_adverse', 'prior_carrier_activity')} | {
                'refs': sorted(self.native_refs()),
                'executions': (selected.get('handoff_progress') or {}).get('executions', []),
                'native_finalizations': [{key: record.get(key) for key in ('ref', 'thread_id', 'actions', 'revoked')}
                                         for record in s.get('native_finalizations', [])]})

    def reject_design_native_replay(self, data):
        references = []
        design = self.state.get('design_predecessor')
        recovery_prior = self.state.get('implementation_predecessor') or self.state.get('recovery_predecessor')
        if recovery_prior is not None:
            prior, _, _ = read_implementation_delivery(self.path, recovery_prior)
            references.append(prior['native_source_index_ref'])
            design = prior['design_predecessor']
        closure = (self.state.get('final_integration_recovery') or {}).get('closure_handoff')
        if closure is not None:
            references.append(closure['native_source_index_ref'])
        if design is not None:
            references.append(read_design_predecessor(design, self.path)['native_source_index_ref'])
        store = state_store.open_store(self.path)
        current = store.open_current()
        for reference in references:
            index = store.read_evidence(reference, run_id=current.run_id, kind='native-source-index')['value']
            require(isinstance(index, dict), 'state_object_corrupt', 'invalid predecessor native source index')
            require(store.bytes_read <= state_store.READ_LIMIT, 'state_capacity', 'native lookup exceeds read budget')
            require(all(entry.digest([key, data[key]]) not in index
                        for key in ('query_id', 'call_ref', 'response_ref') if key in data),
                    'host_provenance_conflict', 'original native receipt cannot be rebound to current work')

    def collaboration_pending(self):
        s = self.state
        snapshot = s.get('lifecycle_snapshot')
        if snapshot is None:
            return [{'kind': 'current-descendants-missing'}]
        if snapshot.get('guard') != self.collaboration_guard():
            return [{'kind': 'current-descendants-stale'}]
        expected = set(s['native_host']['baseline_refs']) | self.native_refs()
        expected.update(ref for old in s.get('history', []) for ref in self.native_refs(old))
        return collaboration_inventory_pending(snapshot['inventory'], expected, self.closed_native_refs(),
                                               self.baseline_stopped_refs())

    def baseline_stopped_refs(self):
        s = self.state
        snapshot = s.get('lifecycle_snapshot')
        if snapshot is None or snapshot.get('guard') != self.collaboration_guard():
            return set()
        return {query['ref'] for query in s.get('baseline_thread_queries', [])
            if query.get('result') is not None and query['inventory_query_id'] == snapshot['query_id'] and
            query['guard'] == snapshot['guard']}

    def baseline_thread_query(self, data):
        s = self.state
        thread, host = baseline_thread_identity(s, data)
        require(data['ref'] not in self.native_refs() and
                not any(data['ref'] in self.native_refs(old) for old in s.get('history', [])),
                'identity_mismatch', 'workflow roles must retain their original observation and finalization contracts')
        agents = self.finalization_snapshot()
        require(data['ref'] not in agents and not any(ref.startswith(data['ref'] + '/') for ref in agents),
                'host_evidence_missing', 'baseline lookup supplements only a missing admitted leaf in the current inventory')
        snapshot = s['lifecycle_snapshot']
        for query in s.get('baseline_thread_queries', []):
            if query['input'] == data and query['inventory_query_id'] == snapshot['query_id'] and query.get('result') is None:
                return _view(s, {key: copy.deepcopy(query[key]) for key in ('operation', 'query_id', 'tool', 'arguments')},
                             acknowledged=True)
        query = {'operation': 'invoke-baseline-thread-query', 'query_id': str(uuid.uuid4()),
            'tool': 'mcp__codex_app__read_thread', 'arguments': {'threadId': thread, 'hostId': host, 'turnLimit': 1},
            'ref': data['ref'], 'input': copy.deepcopy(data), 'guard': snapshot['guard'],
            'inventory_query_id': snapshot['query_id'], 'result': None}
        s.setdefault('baseline_thread_queries', []).append(query)
        self.save()
        return _view(s, {key: copy.deepcopy(query[key]) for key in ('operation', 'query_id', 'tool', 'arguments')})

    def baseline_thread_state(self, data):
        entry.fields(data, {'query_id', 'call_ref', 'response_ref', 'raw'})
        for key in ('query_id', 'call_ref', 'response_ref'): entry.nonempty(data[key])
        s = self.state
        query = next((query for query in s.get('baseline_thread_queries', []) if query['query_id'] == data['query_id']), None)
        require(query is not None, 'host_provenance_conflict', 'baseline readback requires its original issued query')
        snapshot = s.get('lifecycle_snapshot')
        require(snapshot is not None and snapshot['guard'] == self.collaboration_guard() == query['guard'] and
                snapshot['query_id'] == query['inventory_query_id'],
                'host_evidence_missing', 'a new inventory or native activity requires a new exact-thread baseline lookup')
        self.reject_design_native_replay(data)
        for state in [s, *s.get('history', [])]:
            sources = list(state.get('collaboration_lookups', {}).values())
            sources.extend(item['input']['binding'] for item in state.get('baseline_thread_queries', []))
            require(all(source['call_ref'] != data['call_ref'] and source['response_ref'] != data['response_ref']
                        for source in sources), 'host_provenance_conflict',
                    'inventory and parent-binding responses cannot be relabelled as a current child-thread read')
        if query['result'] is not None:
            require(query['result'] == data, 'host_provenance_conflict', 'baseline response cannot be changed or rebound')
            return _view(s, acknowledged=True)
        for state in [s, *s.get('history', [])]:
            for previous in state.get('baseline_thread_queries', []):
                result = previous.get('result')
                require(result is None or result['call_ref'] != data['call_ref'] and result['response_ref'] != data['response_ref'],
                        'host_provenance_conflict', 'cached baseline evidence cannot satisfy a new inventory or query')
        validate_baseline_thread_read(query, data)
        query['result'] = copy.deepcopy(data)
        self.save()
        return self.finish_lifecycle(True)

    def closed_native_refs(self):
        return {record['ref'] for record in self.state.get('native_finalizations', [])
                if record.get('verified') and not record.get('revoked')}

    def require_finalizations_released(self):
        require(all(record.get('verified') and not record.get('revoked') for record in self.state.get('native_finalizations', [])),
                'host_action_pending', 'finish the existing native finalization before creating another native role')

    def finalization_owner(self, ref):
        """Acceptance stays in B/control; lifecycle evidence grants no role."""
        require(ref in self.native_refs() or any(ref in self.native_refs(old) for old in self.state.get('history', [])),
                'identity_mismatch', 'finalize only a registered workflow native identity')
        for saved in [self.state, *reversed(self.state.get('history', []))]:
            accepted = saved.get('accepted')
            if accepted and accepted['role_ref'] == ref and saved.get('host', {}).get('status') == 'stopped':
                return saved['handoff']['controller_ref'], accepted['digest'], saved['handoff']['expected_entry']['actor']['thread_id']
            if saved is self.state and saved.get('dispatch') and saved['dispatch'].get('request'):
                selected = control.selected_control(saved['control']['context'],
                    {'attempt': saved['dispatch']['request']['attempt']})
                carrier = selected.get('carrier')
                current_ref = (dispatch.matching(saved['dispatch'], saved['control']['context'])[0]
                    if saved['handoff']['role'] == 'execution-agent' else (carrier or {}).get('ref'))
                require(ref != current_ref, 'result_incomplete',
                        'current native business work must be stopped and accepted before finalization')
            slots = list(saved.get('allocations', {}).values())
            active_count = len(slots)
            slots.extend(plan_revision_allocation_slot(saved, slot)
                         for history in saved.get('plan_revision_allocation_history', [])
                         for slot in history['allocations'].values())
            for index, slot in enumerate(slots):
                record = handoff.unseal(slot['record']) if slot.get('record') else {}
                accepted = record.get('acceptance')
                if accepted and accepted['role_ref'] == ref and record['status'] == 'accepted':
                    return slot['owner_ref'], accepted['digest'], handoff.resolve_allocation(slot['handoff'])['expected_entry']['actor']['thread_id']
                require(not (saved is self.state and index < active_count and
                            any(receipt.get('ref') == ref for receipt in record.get('receipts', []))),
                        'result_incomplete', 'current allocation must be accepted before native finalization')
            checkpoint = saved.get('control', {}).get('context', {}).get('handoff_progress') or {}
            for index, group in enumerate([saved.get('review_activity', {}), *saved.get('review_history', [])]):
                for slot in group.values():
                    if slot and slot.get('ref') == ref and slot.get('stopped') and checkpoint.get('state') in {
                            'final-validation-pending', 'validating', 'final-validation-failed', 'deliverable', 'completed'}:
                        return slot['originating_ref'], entry.digest(slot['receipts']), slot['originating_ref']
                    require(not (saved is self.state and index == 0 and slot and slot.get('ref') == ref),
                            'result_incomplete', 'current review must stop and converge before native finalization')
        if self.state.get('design_predecessor') is not None:
            prior = read_design_predecessor(self.state['design_predecessor'], self.path)
            if prior['accepted']['role_ref'] == ref:
                return prior['controller_ref'], prior['accepted']['digest'], prior['handoff']['expected_entry']['actor']['thread_id']
        predecessor = self.state.get('implementation_predecessor') or self.state.get('recovery_predecessor')
        if predecessor is not None:
            prior, current, store = read_implementation_delivery(self.path, predecessor)
            reference = prior['native_owner_refs'].get(ref)
            if reference is not None:
                facts = store.read_evidence(reference, run_id=current.run_id, kind='evidence')['value']
                entry.fields(facts, {'kind', 'ref', 'owner_ref', 'parent_thread', 'delivery_digest', 'stopped', 'status'})
                require(facts['kind'] == 'accepted-native-owner-v1' and facts['ref'] == ref and
                        facts['status'] == 'accepted' and facts['stopped'] is True and
                        isinstance(facts['delivery_digest'], str) and re.fullmatch('[0-9a-f]{64}', facts['delivery_digest']),
                        'result_incomplete', 'original native business acceptance or stop proof changed')
                entry.nonempty(facts['owner_ref']); entry.nonempty(facts['parent_thread'])
                return facts['owner_ref'], facts['delivery_digest'], facts['parent_thread']
        raise entry.PreparationError('result_incomplete', 'native finalization requires authoritative stopped and accepted business work')

    def finalization_snapshot(self):
        snapshot = self.state.get('lifecycle_snapshot')
        require(snapshot is not None, 'host_evidence_missing', 'obtain a fresh whole-tree lookup before native finalization')
        require(snapshot.get('guard') == self.collaboration_guard(), 'host_evidence_missing', 'native inventory is stale')
        return collaboration_inventory(snapshot['inventory'])

    def finalization_stopped(self, record):
        agents = self.finalization_snapshot()
        refs = self.native_refs() | set().union(*(self.native_refs(old) for old in self.state.get('history', [])))
        require(set(agents) - {self.state['native_host']['controller_ref']} <= refs | set(self.state['native_host']['baseline_refs']),
                'host_evidence_missing', 'unknown native identities must be reconciled before finalization')
        targets = {ref for ref in refs if ref == record['ref'] or ref.startswith(record['ref'] + '/')}
        descendants = {ref for ref in agents if ref == record['ref'] or ref.startswith(record['ref'] + '/')}
        require(descendants == targets - self.closed_native_refs() and record['ref'] in descendants and
                all(isinstance(agents[ref], dict) and set(agents[ref]) == {'completed'} and
                    isinstance(agents[ref]['completed'], str) for ref in descendants),
                'host_evidence_missing', 'target and every registered descendant must have stopped; unknown descendants block closure')
        host_calls = [action for action in self.unresolved_host_actions()
                      if action['payload'].get('ref', self.bound_ref()) in targets]
        transactions = [transaction for transaction in self.state.get('control_transactions', {}).values()
                        if transaction.get('result') is None and
                           (transaction.get('archive_ref') in targets if transaction.get('scope') == 'archive' else
                            self.bound_ref() in targets or
                            any(value in targets for key, value in transaction['request'].get('evidence', {}).items()
                                if key in {'ref', 'agent_ref', 'dispatcher_ref'} and isinstance(value, str)))]
        allocations = [slot for slot in self.state.get('allocations', {}).values() if slot.get('transaction') and
                       (slot['owner_ref'] in targets or any(receipt.get('ref') in targets
                        for receipt in (slot.get('record') or {}).get('receipts', [])))]
        require(not host_calls and not transactions and not allocations,
                'host_action_pending', 'reconcile the target and descendant original calls before native finalization')

    def verify_native_finalizations(self, agents, evidence):
        for record in self.state.get('native_finalizations', []):
            close = next((action for action in record['actions'] if action['operation'] == 'close-native'), None)
            if close is None or not close.get('resolved') or close.get('resolution') != 'completed':
                continue
            descendants = {ref for ref in agents if ref.startswith(record['ref'] + '/')}
            inactive = record['ref'] not in agents and not descendants
            if inactive and not record.get('revoked'):
                record['verified'] = copy.deepcopy(evidence)

    def prepare_native_query(self, record):
        close = record['actions'][-1]
        if not (record.get('revoked') and close['operation'] == 'close-native' and
                close.get('resolved') and close.get('resolution') == 'completed'):
            return
        pending = next((query for query in record.get('queries', []) if not query.get('resolved')), None)
        store = state_store.open_store(self.path)
        if pending is not None:
            store.require_reserve(pending['action_id'], recovery=True)
            return
        query = {'action_id': str(uuid.uuid4()), 'close_action_id': close['action_id'],
                 'query_for': entry.digest(record['revoked']), 'request': {
                     'tool': 'mcp__codex_app__read_thread', 'arguments': {
                         'threadId': record['thread_id'],
                         'hostId': record['prepare']['close']['request']['arguments']['hostId'],
                         'turnLimit': 1, 'includeOutputs': False}}}
        # No executable request enters the journal until its own reserve exists.
        store.reserve(query['action_id'], recovery=True)
        record.setdefault('queries', []).append(query)
        self._receipt_action = query['action_id']
        self.save()

    def native_receipt_projection(self, data, record, action, reference, manifest, measurement):
        size, digest = measurement
        truncated = size > state_store.EVIDENCE_LIMIT or not manifest['complete']
        facts = {}
        if not truncated and data['receipt']['status'] == 'completed':
            facts = native_result_facts(record, action, query=data['receipt'].get('query', False))
        return {**data, 'receipt': {**data['receipt'],
            'status': 'unknown' if truncated else data['receipt']['status'], 'raw': {
                'format': 'native-raw-reference-v1', 'evidence_ref': reference,
                'sha256': digest, 'size': size, 'facts': facts, 'truncated': truncated,
                'reported_status': data['receipt']['status'],
                'reason': ('atomic_limit_exceeded' if size > state_store.EVIDENCE_LIMIT else
                           manifest['reason'])}}}

    def native_receipt_commit_bytes(self, data, record, action, store, current, measurement, binding):
        """Measure finite success/unknown/interrupted candidates, never the external raw body."""
        size, digest = measurement
        chunks = [{'run_id': current.run_id, 'kind': 'raw-chunk', 'sha256': '0' * 64,
                   'size': min(1024 * 1024, size - offset)}
                  for offset in range(0, size, 1024 * 1024)]
        measured = []
        for reason in (None, 'state_capacity', 'interrupted'):
            manifest = {'format': 'raw-evidence-v1', 'complete': reason is None, 'size': size,
                        'retained_size': size, 'sha256': digest, 'chunks': chunks, 'reason': reason, 'native_receipt': binding}
            with store.measure_writes() as writes:
                reference = store.write_object(current.run_id, 'evidence', manifest)
                outer = copy.deepcopy(self.outer)
                candidate = Progress(self.path, outer)
                candidate._authority_revision = self._authority_revision
                candidate._receipt_action = self._receipt_action
                saved = next(item for item in candidate.state['native_finalizations'] if item['ref'] == record['ref'])
                target = next(item for item in saved['actions'] if item['action_id'] == action['action_id'])
                receipt = self.native_receipt_projection(data, saved, target, reference, manifest, measurement)
                saved['receipts'].append(receipt)
                if reason == 'interrupted':
                    saved['raw_ingress'].update(evidence_ref=reference, reason=reason)
                else:
                    saved.pop('raw_ingress', None)
                if receipt['receipt']['status'] != 'unknown':
                    if data['receipt'].get('query'):
                        query = next(item for item in saved['queries'] if item['action_id'] == self._receipt_action)
                        query.update(resolved=True, resolution=receipt['receipt']['status'])
                        if receipt['receipt']['status'] == 'completed':
                            saved.pop('verified', None)
                            saved.pop('revoked', None)
                    else:
                        target.update(resolved=True, resolution=receipt['receipt']['status'])
                candidate.state['lifecycle_snapshot'] = None
                candidate.state.pop('lifecycle_query', None)
                candidate.save()
                measured.append(sum(writes.values()))
        return max(measured)

    def retain_native_receipt(self, data, record, action):
        raw = data['receipt']['raw']
        size, digest = state_store.raw_measure(raw)
        if size <= 64 * 1024:
            record.pop('raw_ingress', None)
            return copy.deepcopy(data)
        # Pin the authenticated source before any large evidence write. A crash
        # retains the original call even when no complete manifest was committed.
        source = {'action_id': data['action_id'], 'call_ref': data['receipt']['call_ref'],
                  'response_ref': data['receipt']['response_ref'], 'size': size, 'sha256': digest}
        record['raw_ingress'] = source
        if not data['receipt'].get('query'):
            action['call_ref'] = data['receipt']['call_ref']
        self.save()
        store = state_store.open_store(self.path, receipt_action=self._receipt_action)
        current = store.open_current()
        measurement = (size, digest)
        facts = (native_result_facts(record, action, query=data['receipt'].get('query', False))
                 if size <= state_store.EVIDENCE_LIMIT and data['receipt']['status'] == 'completed' else {})
        binding = native_receipt_binding(data['receipt'], record, action, facts)
        commit_bytes = (self.native_receipt_commit_bytes(data, record, action, store, current, measurement, binding)
                        if size <= state_store.EVIDENCE_LIMIT else 0)
        try:
            reference, manifest = store.write_raw_evidence(current.run_id, raw, measurement=measurement,
                                                         commit_bytes=commit_bytes, native_receipt=binding)
        except state_store.RawEvidenceInterrupted as error:
            source['evidence_ref'] = error.reference
            source['reason'] = 'interrupted'
            manifest = store.read_evidence(error.reference, run_id=current.run_id)['value']
            record['receipts'].append(self.native_receipt_projection(
                data, record, action, error.reference, manifest, measurement))
            self.state['lifecycle_snapshot'] = None
            self.state.pop('lifecycle_query', None)
            self.save()
            raise
        record.pop('raw_ingress', None)
        return self.native_receipt_projection(data, record, action, reference, manifest, measurement)

    def native_finalize(self, data):
        entry.fields(data, {'operation', 'ref'}, {'actor_ref', 'reference', 'reuse', 'binding', 'close', 'action_id', 'receipt'})
        entry.nonempty(data['ref'])
        s = self.state
        require(s.get('native_host', {}).get('adapter') == 'collaboration', 'host_capability_missing',
                'native finalization currently requires the verified collaboration and desktop host capabilities')
        records = s.setdefault('native_finalizations', [])
        record = next((item for item in records if item['ref'] == data['ref']), None)
        if data['operation'] == 'prepare':
            entry.fields(data, {'operation', 'ref', 'actor_ref', 'reference', 'reuse', 'binding', 'close'})
            self.require_not_stopping()
            new_record = record is None
            owner, delivery, parent_thread = self.finalization_owner(data['ref'])
            require(data['actor_ref'] == owner and data['reuse'] is False, 'authority_missing',
                    'the original business owner must retire this identity from all future reuse')
            entry.nonempty(data['reference'])
            binding = data['binding']
            entry.fields(binding, {'call_ref', 'response_ref', 'raw'})
            entry.nonempty(binding['call_ref']); entry.nonempty(binding['response_ref'])
            raw = native_raw_object(binding['raw'])
            require(isinstance(raw.get('thread'), dict) and raw['thread'].get('id') == parent_thread,
                    'identity_mismatch', 'native binding must come from the frozen business owner thread')
            if record is not None:
                require(record['prepare'] == data and record['delivery_digest'] == delivery,
                        'finalization_conflict', 'retain the original identity, business acceptance and no-reuse decision')
                if record.get('verified') and not record.get('revoked'):
                    return _view(s, acknowledged=True)
                action = record['actions'][-1]
                if not action.get('resolved') or action.get('resolution') != 'completed' or record.get('revoked'):
                    self.prepare_native_query(record)
                    return _view(s, native_finalization_lookup(record), acknowledged=True)
                if action['operation'] == 'close-native':
                    return _view(s, {'operation': 'reconcile-stop-barrier', 'ref': record['ref']}, acknowledged=True)
                self.require_business_ready()
                self.finalization_stopped(record)
                require(not any(ref.startswith(record['ref'] + '/') for ref in
                            (self.native_refs() | set().union(*(self.native_refs(old) for old in s.get('history', [])))) - self.closed_native_refs()),
                        'host_evidence_missing', 'finalize and verify registered descendants before closing their parent')
                action = {'action_id': str(uuid.uuid4()), 'operation': 'close-native', 'payload': {
                    'ref': record['ref'], 'thread_id': record['thread_id'], **copy.deepcopy(data['close'])}, 'checkpoint': str(self.path)}
            else:
                self.require_business_ready()
                require(isinstance(raw.get('thread'), dict) and isinstance(raw.get('turns'), list) and
                        all(isinstance(turn, dict) and isinstance(turn.get('items'), list) and
                            all(isinstance(item, dict) for item in turn['items']) for turn in raw['turns']),
                        'host_evidence_missing', 'retain the parent readback thread, turns and native item objects')
                entry.nonempty(raw['thread'].get('id')); entry.nonempty(raw['thread'].get('hostId'))
                require(all(isinstance(item.get('agentThreadId'), str) and item['agentThreadId'] for turn in raw['turns']
                        for item in turn['items'] if item.get('type') == 'subAgentActivity' and item.get('agentPath') == data['ref']),
                        'identity_mismatch', 'native event thread identities must be nonempty strings')
                identities = {item['agentThreadId'] for turn in raw.get('turns', []) for item in turn.get('items', [])
                              if item.get('type') == 'subAgentActivity' and item.get('agentPath') == data['ref'] and item.get('agentThreadId')}
                require(len(identities) == 1 and all(isinstance(identity, str) and identity for identity in identities),
                        'identity_mismatch', 'retain the actual parent native event binding the canonical ref to its exact thread ID')
                thread = next(iter(identities))
                validate_native_close(data['close'], data['ref'], thread)
                require(data['close']['request']['arguments']['hostId'] == raw['thread']['hostId'],
                        'identity_mismatch', 'close must retain the actual parent native host')
                record = {'ref': data['ref'], 'thread_id': thread, 'prepare': copy.deepcopy(data),
                          'delivery_digest': delivery, 'actions': [], 'receipts': []}
                self.finalization_stopped(record)
                identity = str(uuid.uuid4())
                action = {'action_id': identity, 'operation': 'finish-native', 'payload': {
                    'ref': record['ref'], 'thread_id': thread, 'read_only': True,
                    'message': 'Read-only finalization. Consume queued mailbox messages, preserve the accepted business delivery and all commits/tests. '
                               'Stop commands and descendants. Return one terminal final answer: NATIVE_FINISHED action_id=' + identity +
                               ' mailbox=consumed business_result=unchanged commands=stopped. Do not send an acknowledgement back or resume business work.'},
                          'checkpoint': str(self.path)}
            state_store.open_store(self.path).reserve(action['action_id'], recovery=True)
            if new_record:
                records.append(record)
            record['actions'].append(action)
            s['lifecycle_snapshot'] = None
            s.pop('lifecycle_query', None)
            self._receipt_action = action['action_id']
            self.save()
            return _view(s, action)
        require(record is not None, 'identity_mismatch', 'look up only the exact saved native finalization')
        if data['operation'] == 'lookup':
            entry.fields(data, {'operation', 'ref'})
            self.prepare_native_query(record)
            return _view(s, native_finalization_lookup(record))
        entry.fields(data, {'operation', 'ref', 'action_id', 'receipt'})
        require(data['operation'] == 'observe', 'invalid_operation', 'unknown native finalization operation')
        action = next((item for item in record['actions'] if item['action_id'] == data['action_id']), None)
        require(action is not None, 'identity_mismatch', 'receipt must name the original exact native action')
        receipt = data['receipt']
        entry.fields(receipt, {'adapter', 'call_ref', 'response_ref', 'status', 'ref', 'raw'}, {'query', 'query_for'})
        require('query' not in receipt or receipt['query'] is True, 'invalid_result', 'readback query marker must be true')
        require('query_for' not in receipt or receipt.get('query') is True,
                'invalid_result', 'revocation guard belongs only to a readback query')
        for key in ('adapter', 'call_ref', 'response_ref'): entry.nonempty(receipt[key])
        require(isinstance(receipt['raw'], dict) and receipt['raw'] and
                receipt['raw'].get('format') != 'native-raw-reference-v1', 'host_evidence_missing', 'retain the raw authenticated native response')
        require(receipt['ref'] == record['ref'], 'identity_mismatch', 'retain the exact native target')
        require(isinstance(receipt['status'], str) and receipt['status'] in {'completed', 'unknown', 'failed'},
                'invalid_result', 'native outcome status must be completed, unknown or failed')
        require(receipt['adapter'] == record['prepare']['close']['adapter'],
                'identity_mismatch', 'finish and close evidence must retain the original desktop adapter')
        raw_size, _ = state_store.raw_measure(receipt['raw'])
        for previous_record in records:
            for previous in previous_record['receipts']:
                if previous['receipt']['call_ref'] == receipt['call_ref']:
                    require(previous['action_id'] == data['action_id'] and previous['ref'] == data['ref'],
                            'host_provenance_conflict', 'native invocation cannot be rebound to another action')
                if previous['receipt']['response_ref'] == receipt['response_ref']:
                    require(native_receipt_matches(previous, data), 'host_provenance_conflict', 'native response cannot be rebound or changed')
                    if receipt.get('query'):
                        # A committed receipt may have lost its return before the
                        # operation released its reserve. ACK performs no new call.
                        self.release_settled_reserves()
                    return _view(s, acknowledged=True)
        if receipt.get('query'):
            require(isinstance(receipt.get('query_for'), str) and re.fullmatch(r'[0-9a-f]{64}', receipt['query_for']),
                    'host_evidence_missing', 'readback requires the guard issued with its original lookup')
            require(record.get('revoked') and action['operation'] == 'close-native' and action.get('resolved') and
                    action.get('resolution') == 'completed', 'finalization_conflict', 'readback reconciles only a revoked successful close')
            query = next((item for item in record.get('queries', []) if not item.get('resolved')), None)
            require(query is not None and query['close_action_id'] == data['action_id'] and
                    query['query_for'] == receipt['query_for'],
                    'host_provenance_conflict', 'readback must bind the original persisted query and revocation guard')
            require(query.get('call_ref', receipt['call_ref']) == receipt['call_ref'],
                    'host_provenance_conflict', 'an unknown readback retains its original invocation')
            if 'call_ref' not in query:
                require(all(previous['receipt']['call_ref'] != receipt['call_ref'] for item in records for previous in item['receipts']),
                        'host_provenance_conflict', 'readback requires a new authenticated read_thread invocation')
            state_store.open_store(self.path).require_reserve(query['action_id'], recovery=True)
            if receipt['status'] == 'completed' and raw_size <= state_store.EVIDENCE_LIMIT:
                validate_native_readback(native_raw_object(receipt['raw']), record, 'notLoaded')
            query['call_ref'] = receipt['call_ref']
            self._receipt_action = query['action_id']
            retained = self.retain_native_receipt(data, record, action)
            receipt = retained['receipt']
            if receipt['status'] == 'completed' and receipt['query_for'] == entry.digest(record['revoked']):
                record.pop('verified', None)
                record.pop('revoked', None)
            if receipt['status'] != 'unknown':
                query.update(resolved=True, resolution=receipt['status'])
            record['receipts'].append(retained)
            s['lifecycle_snapshot'] = None
            s.pop('lifecycle_query', None)
            self.save()
            return _view(s, native_finalization_lookup(record) if receipt['status'] == 'unknown' else
                         {'operation': 'reconcile-stop-barrier', 'ref': record['ref']})
        require(not action.get('resolved'), 'finalization_conflict', 'a resolved native action cannot acquire another outcome')
        require(action.get('call_ref', receipt['call_ref']) == receipt['call_ref'],
                'host_provenance_conflict', 'an incomplete native result retains its original invocation')
        if receipt['status'] == 'completed' and raw_size <= state_store.EVIDENCE_LIMIT:
            raw = native_raw_object(receipt['raw'])
            if action['operation'] == 'finish-native':
                validate_native_readback(raw, record, 'idle')
            else:
                require(receipt['adapter'] == action['payload']['adapter'], 'identity_mismatch', 'retain the original close adapter')
                validate_native_close_result(action['payload'], raw)
        self._receipt_action = action['action_id']
        retained = self.retain_native_receipt(data, record, action)
        receipt = retained['receipt']
        if receipt['status'] in {'completed', 'failed'}:
            action['resolved'], action['resolution'] = True, receipt['status']
        record['receipts'].append(retained)
        s['lifecycle_snapshot'] = None
        s.pop('lifecycle_query', None)
        self.save()
        return _view(s, native_finalization_lookup(record) if receipt['status'] in {'failed', 'unknown'} else
                     {'operation': 'reconcile-stop-barrier', 'ref': record['ref']})

    def lifecycle_query(self, data):
        entry.fields(data, set())
        require(self.state.get('native_host', {}).get('adapter') == 'collaboration' and
                self.state.get('dispatch') and self.native_refs(),
                'host_capability_missing', 'interactive inventory query requires the original bound collaboration host')
        query = {'query_id': str(uuid.uuid4()), 'guard': self.collaboration_guard()}
        self.state['lifecycle_query'] = query
        # A failed or interrupted lookup cannot reuse the preceding snapshot.
        self.state['lifecycle_snapshot'] = None
        self.save()
        return _view(self.state, {'operation': 'invoke-lifecycle-query', 'query_id': query['query_id'],
                                 'tool': 'collaboration.list_agents', 'arguments': {}})

    def collaboration_lifecycle(self, data, *, advance):
        s = self.state
        previous = s.get('lifecycle_snapshot')
        if (previous is not None and previous.get('guard') == self.collaboration_guard() and
                data == {key: previous[key] for key in ('query_id', 'call_ref', 'response_ref', 'inventory')}):
            result = self.finish_lifecycle(advance)
            result['acknowledged'] = True
            return result
        # Even a late or malformed lookup cannot hide newly observed activity.
        # Consume its query as well: replaying an older completed response after
        # a rejection must never restore the old proof.
        query = s.pop('lifecycle_query', None)
        s['lifecycle_snapshot'] = None
        self.save()
        entry.fields(data, {'query_id', 'call_ref', 'response_ref', 'inventory'})
        for key in ('query_id', 'call_ref', 'response_ref'):
            entry.nonempty(data[key])
        agents = collaboration_inventory(data['inventory'])
        incoming_guard = self.collaboration_guard()
        for record in s.get('native_finalizations', []):
            subtree = {ref for ref in agents if ref == record['ref'] or ref.startswith(record['ref'] + '/')}
            active = any(not (isinstance(agents[ref], dict) and set(agents[ref]) == {'completed'} and
                             isinstance(agents[ref]['completed'], str)) for ref in subtree)
            readback_completed = any(observed['receipt'].get('query') and observed['receipt']['status'] == 'completed'
                                     for observed in record['receipts'])
            if any(action['operation'] == 'close-native' for action in record['actions']) and (
                    active or subtree and (record.get('verified') or readback_completed)):
                record['revoked'] = {'checkpoint_revision': s['revision'], 'evidence': copy.deepcopy(data)}
        require(data['inventory']['controller_ref'] == s['native_host']['controller_ref'],
                'identity_mismatch', 'retain the original controller agent path')
        # Trusted ingress authenticates these actual tool identities. Never
        # relabel cached raw bytes as a new call to refresh a stale barrier.
        # Keep conflicts as observations too, without overwriting the original
        # query's receipt or allowing rejected provenance to be recycled.
        s.setdefault('collaboration_lookups', {})[entry.digest(data)] = copy.deepcopy(data)
        self.save()
        self.reject_design_native_replay(data)
        for state in [s, *s.get('history', [])]:
            for previous in state.get('collaboration_lookups', {}).values():
                if (previous['query_id'] == data['query_id'] or previous['call_ref'] == data['call_ref'] or
                        previous['response_ref'] == data['response_ref']):
                    require(previous == data, 'host_provenance_conflict', 'inventory response cannot be rebound to a new query')
        require(query is not None and query['query_id'] == data['query_id'] and
                query['guard'] == incoming_guard,
                'host_evidence_missing', 'obtain a new inventory after native activity or changed writer bindings')
        s['lifecycle_snapshot'] = {**copy.deepcopy(data), 'guard': self.collaboration_guard(),
                                   'instance': s['native_host']['instance']}
        self.verify_native_finalizations(collaboration_inventory(data['inventory']), data)
        self.save()
        return self.finish_lifecycle(advance)

    def lifecycle_state(self, snapshot, *, advance=True):
        if self.state.get('native_host', {}).get('adapter') == 'collaboration':
            return self.collaboration_lifecycle(snapshot, advance=advance)
        expected = self.outer.get('sessions', {}).get('stage' + str(self.state['stage'])) or self.state['handoff']['expected_entry']['actor']['thread_id']
        validate_rpc_lifecycle(snapshot, expected)
        require(not self.state.get('native_host') or snapshot['instance'] == self.state['native_host']['instance'],
                'host_recovery_required', 'lifecycle evidence must retain the admitted app-server connection')
        previous = self.state.get('lifecycle_snapshot')
        require(previous is None or previous['instance'] == snapshot['instance'], 'host_recovery_required', 'a new host cannot replace current native evidence')
        self.state['lifecycle_snapshot'] = copy.deepcopy(snapshot)
        self.save()
        return self.finish_lifecycle(advance)

    def finish_lifecycle(self, advance):
        if (self.state['status'] == 'blocked' and self.state.get('error', {}).get('code') == 'host_evidence_missing' and
                not self.state.get('business_block') and not self.stop_barrier_pending()):
            self.state['status'] = self.state.get('blocked_from', 'active')
            self.state.pop('error', None)
            self.save()
        return self.advance_stop(self.stop_intent()) if advance and self.stop_intent() else _view(self.state)

    def host_event(self, data):
        entry.fields(data, {'instance','event'}, {'carrier_thread'})
        if self.state.get('native_host', {}).get('adapter') == 'collaboration':
            require(data['instance'] == self.state['native_host']['instance'] and 'carrier_thread' not in data,
                    'identity_mismatch', 'activity must retain the original collaboration host')
            item = data['event']
            validate_collaboration_event(item)
            self.state['observations'].append({'native_event': copy.deepcopy(data)})
            if item['kind'] in {'started', 'interacted'}:
                self.state['lifecycle_snapshot'] = None
                self.state.pop('lifecycle_query', None)
                for record in self.state.get('native_finalizations', []):
                    if (item['agentPath'] == record['ref'] or item['agentPath'].startswith(record['ref'] + '/') or
                            item['agentThreadId'] == record['thread_id']) and any(
                            action['operation'] == 'close-native' for action in record['actions']):
                        record['revoked'] = {'checkpoint_revision': self.state['revision'], 'evidence': copy.deepcopy(data)}
            self.save()
            return _view(self.state)
        transport = self.outer.get('transport')
        require(transport is not None and data['instance'] == transport.get('instance'),
                'identity_mismatch', 'native event belongs to the current foreground connection')
        event = data['event']
        validate_native_event(event)
        item = event['params']['item']
        carrier = data.get('carrier_thread')
        if event['method'] == 'item/started' and item.get('kind') in {'started', 'interacted'}:
            for record in self.state.get('native_finalizations', []):
                if (item.get('agentPath') == record['ref'] or item.get('agentThreadId') == record['thread_id'] or
                        (item.get('agentPath') or '').startswith(record['ref'] + '/')):
                    record['revoked'] = {'checkpoint_revision': self.state['revision'], 'evidence': copy.deepcopy(data)}
        if carrier is not None and carrier != self.outer.get('sessions', {}).get('stage' + str(self.state['stage'])):
            require(carrier in self.outer.get('sessions', {}).values(), 'identity_mismatch', 'event must retain a known original carrier')
            self.state['observations'].append({'native_event':copy.deepcopy(data)})
            if event['method'] == 'item/started' and item.get('kind') in {'started','interacted'}:
                self.state.setdefault('prior_carrier_activity', {})[carrier] = copy.deepcopy(data)
                self.state['recovery_action'] = {'operation':'await-host-recovery','carrier_thread':carrier,
                    'reason':'reconcile new activity through its original prior-stage parent'}
            self.save()
            return _view(self.state)
        self.state['observations'].append({'native_event':copy.deepcopy(data)})
        if event['method'] == 'item/started' and item.get('kind') in {'started','interacted'} and item.get('agentPath'):
            self.state.setdefault('native_adverse', {})[item['agentPath']] = copy.deepcopy(data)
            for slot in (slot for group in [self.state.get('review_activity', {}), *self.state.get('review_history', [])]
                         for slot in group.values()):
                if slot and slot.get('ref') == item['agentPath']:
                    slot['stopped'] = False
        if (self.state.get('dispatch') and item.get('agentPath') == self.bound_ref() and
                event['method'] == 'item/started' and item.get('kind') in {'started','interacted'}):
            if not self.unresolved_host_actions():
                self.revoke_host()
            self.state['host']['status'] = 'running'
        self.save()
        return _view(self.state)

    def transport_lost(self, data):
        entry.fields(data, {'instance','reason'})
        entry.nonempty(data['reason'])
        transport = self.outer.get('transport')
        require(transport is not None and data['instance'] == transport.get('instance'),
                'identity_mismatch', 'loss must name the original foreground host')
        if self.state.get('transport_loss') == data:
            return _view(self.state, self.state.get('recovery_action'), acknowledged=True)
        self.state['transport_loss'] = copy.deepcopy(data)
        self.revoke_host()
        self.state['host']['status'] = 'unknown'
        self.state['recovery_action'] = {'operation':'await-host-recovery', **self.recovery_requirements(), 'reason':data['reason']}
        if self.state['status'] != 'accepted':
            self.state['status'] = self.stop_intent() or 'blocked'
        self.save()
        return _view(self.state, self.state['recovery_action'])

    def recovery_requirements(self):
        s = self.state
        context = s.get('control', {}).get('context', {})
        progress = context.get('handoff_progress') or {}
        refs = {context.get('carrier', {}).get('ref')} if context.get('carrier') else set()
        refs.update(e.get('agent_ref') for e in progress.get('executions', []))
        recovery = progress.get('recovery')
        if recovery:
            refs.update(receipt['ref'] for receipt in recovery['receipts'] if receipt['status'] == 'ready')
        for group in [s.get('review_activity', {}), *s.get('review_history', [])]:
            refs.update(slot.get('ref') for slot in group.values() if slot)
        return {'host_instance': (self.outer.get('transport') or {}).get('instance'),
            'carrier_thread': self.outer.get('sessions', {}).get('stage' + str(s['stage'])) or s['handoff']['expected_entry']['actor']['thread_id'],
            'ref': self.bound_ref() if s.get('dispatch') else None, 'native_refs': sorted(ref for ref in refs if ref),
            'unresolved_actions': [action['action_id'] for action in self.unresolved_host_actions()],
            'missing': ['current-original-host-identity', 'same-identity-resumability-or-complete-stop-proof',
                        'reconciled-original-invocations'], 'pending': self.stop_barrier_pending()}

    def carrier_failure(self, data):
        entry.fields(data, {'instance','invocation_id','code','evidence'})
        require(data['instance'] == self.outer.get('transport', {}).get('instance') and
                data['invocation_id'] == self.outer.get('launch', {}).get('invocation_id') and
                data['code'] in {'technical_error','no_progress'}, 'identity_mismatch', 'failure must belong to the current carrier invocation')
        if self.state.get('business_block') is None:
            self.state['error'] = {'code':data['code'],'message':'Original carrier reported a business failure', 'downstream_ready':False}
            self.state['blocked_from'] = self.state['status']
            self.state['status'] = 'blocked'
            self.set_business_block(data['code'], data['evidence'])
            self.save()
        return _view(self.state)

    def stop_intent(self):
        s = self.state
        operation = self.outer.get("runner_request", {}).get("operation")
        if operation == "cancel" or s.get("stop_requested") == "cancelling" or s["status"] in {"cancelling", "cancelled"}:
            return "cancelling"
        if operation == "pause" or s.get("stop_requested") == "pausing" or s["status"] in {"pausing", "paused"}:
            return "pausing"
        return None

    def require_not_stopping(self):
        revision = self.state.get("scope_revision_transaction") or {}
        require(self.stop_intent() is None and revision.get("state") in (None,), "progression_suspended",
                "stop intent permits reconciliation and stopping, not new work or continuation")

    def advance_stop(self, intent):
        s = self.state
        if s["status"] in {"cancelled", "accepted"}:
            return _view(s, {"operation": "await-controller-recovery"} if s["status"] == "accepted" else None)
        if s.get("stop_requested") != intent:
            return self.suspend(cancel=intent == "cancelling")
        if s["status"] == "paused":
            return _view(s)
        if s["status"] != intent:
            s["status"] = intent
            self.save()
        cancellation = s.get('cancellation')
        if cancellation is not None and cancellation.get('result') is None:
            return _view(s, {'operation':'await-controller-recovery','cancellation':cancellation})
        if intent == "pausing" and s["stopped"]:
            s["status"], s["step"] = "paused", "bound"
            self.save()
            return _view(s)
        if intent == 'cancelling' and s['stopped']:
            self.cancel_control()
            self.save()
            return _view(s)
        if s["step"] == "host-response":
            return _view(s, {"operation": "lookup-exact-action", "action": s["action"],
                             "request": s["dispatch"]["request"], "receipts": s["observations"]})
        if intent == "cancelling" and s.get("stop_effects"):
            return _view(s, {"operation": "reconcile-stopped-writers", "effects": s["stop_effects"]})
        if ((s['host'].get('query') is not None and (self.unresolved_host_actions() or s['host']['status'] == 'unknown')) or
                (s['host']['status'] == 'unknown' and s['host'].get('last_stop') is not None)):
            return self.query_host()
        if s['host']['status'] == 'stopped' and self.unresolved_host_actions():
            return self.query_host()
        if s['host']['status'] == 'stopped' and not s['stopped']:
            return _view(s, {'operation':'reconcile-stop-barrier', 'pending':self.stop_barrier_pending(),
                             'ref':self.bound_ref()})
        if any(action['operation'] == 'stop-host' for action in self.unresolved_host_actions()):
            return self.query_host()
        if s["dispatch"] and not s["stopped"] and self.bound_ref() is not None:
            return self.effect("stop-host", {"subject": self.subject(), "ref": self.bound_ref()})
        return _view(s)

    def effect(self, operation, payload):
        # An issued mutation is never reissued by advance. Exact lookup is safe.
        if operation in {"invoke-host", "continue-host", "source-lifecycle", "carrier-lifecycle"}:
            self.require_not_stopping()
        if operation == "continue-host":
            require(not any(record['ref'] == self.bound_ref() for record in self.state.get('native_finalizations', [])),
                    'identity_retired', 'the original owner retired this native identity from future reuse')
            blocked = self.continuation_gate()
            if blocked is not None:
                return blocked
            require(payload.get("ref") == self.bound_ref() and payload.get("subject") == self.subject(),
                    "identity_mismatch", "continuation must retain the original scope and identity")
        if operation == "continue-host" and self.state.get("closure_effects"):
            payload = {**payload, "remaining_actions": self.state["closure_effects"],
                       "publication_receipt": self.state["closure_receipt"]}
        action = {"action_id": str(uuid.uuid4()), "operation": operation, "payload": copy.deepcopy(payload),
                  "checkpoint": str(self.path)}
        store = state_store.open_store(self.path)
        store.reserve(action['action_id'], recovery=operation == 'stop-host')
        if operation in {"invoke-host", "continue-host", "stop-host"}:
            previous = self.state.get("host_action")
            if previous is not None and not previous.get("resolved"):
                # A stop must be issued promptly, but cannot erase an earlier
                # invocation which might still execute after this writer stops.
                self.state.setdefault("retained_host_actions", []).append(copy.deepcopy(previous))
            self.revoke_host()
            action["generation"] = self.state["host"]["generation"]
            self.state["host_action"] = copy.deepcopy(action)
        if operation == "continue-host":
            self.state.pop("continuation_intent", None)
            self.state.pop("resume_intent", None)
        self.state["action"] = action
        self.state["step"] = "host-response"
        if operation == 'stop-host':
            self._receipt_action = action['action_id']
        else:
            # A just-completed query funds only its original receipt, never
            # the new business invocation that the observation might enable.
            self._receipt_action = None
            store.receipt_action = None
        self.save()
        return _view(self.state, action)

    def subject(self):
        record = self.state.get("dispatch")
        return {"stage": self.state["stage"], "attempt": record["request"]["attempt"] if record else None,
                "handoff": self.state.get("handoff", {}).get("digest")}

    def block(self, error):
        if self.state["status"] != "blocked":
            self.state["blocked_from"] = self.state["status"]
        self.state["status"] = "blocked"
        self.state["error"] = {"code": getattr(error, "code", "progress_failed"),
            "message": entry.error_message(error), "completed_evidence": getattr(error, "completed_evidence", []),
            "downstream_ready": False, "recovery": "resume the saved operation; do not recreate or republish"}
        if self.state.get("business_block") is not None:
            self.state["error"] = copy.deepcopy(self.state["business_block"]["error"])
        self.save()
        return _view(self.state)

    def start(self, data):
        require(not (self.state or {}).get("scope_revision_transaction"),
                "scope_revision_pending", "finish the saved planning correction before starting another stage")
        require(self.outer.get('transport', {}).get('state') != 'lost', 'host_recovery_required', 'lost foreground transport cannot create a native role')
        entry.fields(data, {"handoff"}, {"control", "next_stage", "requirement_transaction", "native_host", "workspace_evidence"})
        request = copy.deepcopy(data["handoff"])
        require(request.get("operation") == "prepare", "invalid_request", "handoff prepare input required")
        old = self.state
        predecessor_ref = None
        implementation_ref = None
        consumed_readiness = None
        retry = old is not None and old["status"] == "cancelled" and (old.get("dispatch") or {}).get("status") == "not-created" and not old.get("stop_requested")
        if old is not None:
            validate_state(old)
            self.require_controls_reconciled()
            self.require_finalizations_released()
            require(old.get('transport_loss') is None, 'host_recovery_required', 'retain the original foreground host recovery point')
            require(old["status"] == "accepted" or retry, "run_active", "finish the current stage or reconcile non-creation before a new attempt")
            if not retry:
                require(not self.unresolved_host_actions(), "host_action_pending",
                        "settle original host actions before starting another stage")
                require(old.get('stopped') and not self.stop_barrier_pending(), 'host_evidence_missing', 'recheck the complete original native barrier before the successor')
                verify_phase_completed(old)
            if not retry and old["stage"] == 2 and request["stage"] == 3:
                readiness = scope_readiness(old, self.path)
                require(readiness is not None, "scope_incomplete",
                        "Stage 3 requires the current sealed scope and completed planning handoff; use scope-propose or scope-revise")
                effective_scope = old["scope_authority"]["scope"]
                if "scope" not in request:
                    request["scope"] = {**copy.deepcopy(effective_scope),
                        "baseline": old["accepted"]["payload"]["planning_merge_commit"],
                        "owned_paths": copy.deepcopy(effective_scope["implementation_paths"]),
                        "protected_paths": sorted(set(effective_scope["protected_paths"] +
                            old["accepted"]["payload"]["planning_paths"]))}
                require(all(request["scope"][key] == effective_scope[key] for key in
                            ("downstream", "implementation_paths", "closure_paths")) and
                        request["scope"]["baseline"] == old["accepted"]["payload"]["planning_merge_commit"] and
                        set(effective_scope["protected_paths"] + old["accepted"]["payload"]["planning_paths"]) <=
                        set(request["scope"]["protected_paths"]),
                        "scope_changed", "Stage-3 input differs from the sealed scope ceiling or published baseline")
                require("readiness" not in request or request["readiness"] == readiness,
                        "scope_changed", "Stage-3 input differs from current readiness")
                request["readiness"] = readiness
                consumed_readiness = copy.deepcopy(readiness)
            predecessor = old["handoff"]["predecessor"] if retry else old["accepted"]
            request.setdefault("predecessor", predecessor)
            request.setdefault("requirement", old["handoff"]["requirement"] if retry or old["stage"] >= 2 else old["accepted"]["payload"]["requirement"])
            require(request['predecessor'] == predecessor or
                    old['stage'] == 3 and request['stage'] == 4 and isinstance(request['predecessor'], dict) and
                    request['predecessor'].get('kind') == 'implementation-acceptance-ref' and
                    implementation_acceptance(request['predecessor'])[0]['digest'] == predecessor['digest'] or
                    old['stage'] == 2 and request['stage'] == 3 and isinstance(request['predecessor'], dict) and
                    request['predecessor'].get('kind') == 'design-acceptance-ref' and
                    read_design_acceptance(request['predecessor']) == predecessor,
                    'predecessor_changed', 'retain the original predecessor')
            require(request["stage"] == (old["stage"] if retry else old["next_stage"]), "illegal_route", "use the authorized stage")
            require(old.get("pending") is None, "decision_required", "confirm the saved stage boundary first")
            if retry:
                require(request["authorization"]["reference"] != old["handoff"]["authorization"]["reference"],
                        "decision_required", "a new controller retry decision must distinguish the failed request")
                require(all(request[key] == old["handoff"][key] for key in ("role", "scope", "target", "binding", "semantic")),
                        "scope_changed", "non-creation retry cannot widen or replace the original work")
                predecessor_ref = copy.deepcopy(old.get('design_predecessor'))
                implementation_ref = copy.deepcopy(old.get('implementation_predecessor'))
                consumed_readiness = copy.deepcopy(old.get('consumed_readiness'))
        else:
            request.setdefault("predecessor", None)
            if self.outer.get("confirmed", {}).get("requirement") is not None:
                request.setdefault("requirement", self.outer["confirmed"]["requirement"])
        if "requirement_transaction" in data:
            transaction = self.outer.get("workflow_requirements", {}).get(data["requirement_transaction"])
            require(transaction is not None and transaction["result"] is not None and transaction["error"] is None,
                    "requirement_incomplete", "exact successful requirement transaction required")
            require("requirement" not in data["handoff"] or data["handoff"]["requirement"] == transaction["result"],
                    "source_changed", "requirement differs from saved A transaction")
            request["requirement"] = transaction["result"]
        retained = copy.deepcopy(old["packages"] if old else {})
        native_host = {}
        if request.get('stage', 0) >= 2:
            transport = self.outer.get('transport') or {}
            if 'native_host' in data:
                native_host = native_host_preflight(data['native_host'])
                if native_host['adapter'] == 'app-server':
                    require(data['native_host']['snapshot']['carrier_thread'] == request['entry']['host']['thread_id'],
                            'identity_mismatch', 'preflight must inspect the original carrier')
                    require(not transport or native_host['instance'] == transport.get('instance'),
                            'identity_mismatch', 'preflight must retain the actual foreground connection')
                else:
                    require(not transport, 'identity_mismatch', 'a foreground connection cannot switch to collaboration evidence')
                    native_host['instance'] = request['entry']['host']['thread_id']
                    agents = collaboration_inventory(data['native_host'])
                    expected = self.native_refs(old) if old else set()
                    historical = set().union(*(self.native_refs(item) for item in (old or {}).get('history', [])))
                    actual = set(agents) - {native_host['controller_ref']}
                    native_host['baseline_refs'] = (old['native_host']['baseline_refs'] if old and
                        old.get('native_host', {}).get('adapter') == 'collaboration' else sorted(actual))
                    supplement = (self.baseline_stopped_refs() if old and
                        (old.get('lifecycle_snapshot') or {}).get('inventory') == data['native_host'] else set())
                    require(not collaboration_inventory_pending(data['native_host'],
                            expected | historical | set(native_host['baseline_refs']),
                            self.closed_native_refs() if old else (), supplement),
                            'host_evidence_missing', 'reconcile existing native agents before starting this stage')
            elif transport.get('state') == 'live' and transport.get('instance'):
                native_host = {'adapter': 'app-server', 'instance': transport['instance']}
            else:
                native_host_preflight(None)
            if old and old.get('stage', 0) >= 2 and old.get('native_host'):
                require(native_host == old['native_host'], 'host_recovery_required', 'retain the original native host across stages')
        for identity in self.outer.get("confirmed", {}).get("packages", {}).values():
            if "name" in identity:
                retained[identity["name"]] = identity
        retain_entry_pins(request, retained)
        if old and old['stage'] == 2 and request['stage'] == 3 and not retry:
            predecessor_ref = design_predecessor(old, self.path, consumed_readiness)
            request['predecessor'] = predecessor_ref
        if old and old['stage'] == 3 and request['stage'] == 4 and not retry:
            implementation_ref = implementation_predecessor(self.path)
            request['predecessor'] = implementation_ref
        saved, resolved = handoff._prepare(request,
            checkpoint_path=str(self.path.resolve()) if old and old['stage'] == 3 and request['stage'] == 4 else None)
        mode = request["authorization"]["flow_mode"]
        require(old is None or mode == old["mode"], "authorization_changed", "mode cannot change without its exact decision")
        for root in (resolved["repository"]["root"], (request.get("binding") or {}).get("worktree")):
            require(root is None or not self.path.resolve().is_relative_to(Path(root).resolve()),
                    "unsafe_checkpoint", "checkpoint must live outside repository and disposable worktree")
        selection = None
        if request['stage'] >= 2:
            previous_selection = old.get('workspace_evidence') if old and old['stage'] >= 2 else None
            supplied_selection = data.get('workspace_evidence', previous_selection or
                                           self.outer.get('confirmed', {}).get('workspace_evidence'))
            selection = workspace_evidence(saved['binding'], saved['controller_ref'], supplied_selection)
            require(previous_selection is None or selection == previous_selection,
                    'workspace_evidence_changed', 'retain the original workspace selection across stages and retries')
        confirmed = self.outer.get("confirmed")
        if confirmed is not None:
            require(self.outer.get("current_stage") == "stage" + str(saved["stage"]),
                    "route_owner_mismatch", "only the runner advances the foreground stage")
            if "stages" in confirmed:
                stage_name = self.outer["current_stage"]
                actor, configuration = saved["expected_entry"]["actor"], saved["expected_entry"]["configuration"]
                require(actor["role"] == "scripted-carrier" and self.outer["sessions"].get(stage_name) == actor["thread_id"],
                        "carrier_unbound", "the runner must bind this exact CLI carrier before native dispatch")
                require(all(configuration[key] == confirmed["stages"][stage_name][key] for key in ("model", "reasoning_effort")),
                        "configuration_changed", "carrier differs from its confirmed runner configuration")
            require(saved["controller_ref"] == confirmed["controller_ref"] and mode == confirmed["flow_mode"],
                    "authorization_changed", "carrier must retain runner controller and flow mode")
            frozen = confirmed["frozen_requirement"]
            require(saved["source_commit"] == frozen["commit"] and saved["requirement_identity"]["sha256"] == frozen["sha256"] and
                    saved["requirement"]["absolute_path"] == frozen["path"], "source_changed", "carrier must consume the runner's exact frozen source")
            require(all(saved["binding"][key] == confirmed[key] for key in ("repository", "worktree", "git_common_dir", "target_branch")),
                    "binding_changed", "carrier worktree differs from the runner's authorized target")
            if "authority_scope" in confirmed:
                allowed = confirmed["authority_scope"]["allowed_paths"]
                require(all(supervision._path_matches(path, allowed) for path in
                            (saved["scope"]["implementation_paths"] or []) + (saved["scope"]["closure_paths"] or [])),
                        "scope_changed", "carrier scope exceeds the runner's authority")
        port = data.get("control")
        if port is None:
            require(old is not None and old["stage"] >= 2 and request["stage"] >= 2 and
                    old["control"]["context"].get("successor_control") is None,
                    "authority_missing", "initial/dedicated entry requires its original control port")
            context = copy.deepcopy(old["control"]["context"])
            context.update(stage=request["stage"], carrier=None, handoff_progress=None,
                           requirement_identity=old["handoff"]["requirement_identity"] if retry else old["accepted"]["requirement_identity"])
            port = {"context": context, "discussion": None}
        if port.get('discussion') is not None:
            query = {key: value for key, value in port['discussion'].items()
                     if key not in {'expected_ledger_revision', 'expected_topic_revision', 'idempotency_key'}}
            authoritative = dispatch.discussion_protocol.handle({**query, 'operation': 'read-topic'})
            require(port['context'] == authoritative['workflow_control'] and
                    port.get('store_ref', authoritative['workflow_control_ref']) == authoritative['workflow_control_ref'],
                    'control_context_changed', 'new attached entry requires the current ledger projection')
            port = {**port, 'store_ref': authoritative['workflow_control_ref']}
        if old is not None:
            previous_history = old['control']['context'].get('retired_handoffs', {})
            if old['control']['discussion'] is not None:
                query = {key: value for key, value in old['control']['discussion'].items()
                         if key not in {'expected_ledger_revision', 'expected_topic_revision', 'idempotency_key'}}
                authoritative = dispatch.discussion_protocol.handle({**query, 'operation': 'read-topic'})['workflow_control']
                require(authoritative is not None, 'authority_missing', 'original attached control is unavailable')
                control.validate_context(authoritative)
                latest_history = authoritative.get('retired_handoffs', {})
                require(all(key in latest_history and all(latest_history[key][field] == value[field]
                        for field in ('carrier', 'plan', 'delivery', 'successor')) for key, value in previous_history.items()),
                        'control_context_changed', 'original authority lost retired handoff evidence')
                previous_history = latest_history
            supplied_history = port['context'].get('retired_handoffs', {})
            require(all(key not in supplied_history or supplied_history[key] == value for key, value in previous_history.items()),
                    'control_context_changed', 'retired handoff projection conflicts with original authority')
            port = copy.deepcopy(port)
            if previous_history or supplied_history:
                port['context']['retired_handoffs'] = {**previous_history, **supplied_history}
        control.validate_context(port["context"])
        next_stage = data.get("next_stage", old["next_stage"] if retry else 2 if request["stage"] == 0 and mode == "continuous" else request["stage"] + 1)
        require(request["stage"] == 4 or (request["stage"], next_stage) in {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)},
                "illegal_route", "select a legal next stage")
        phase_required = "phase" in request["authorization"]
        if request["stage"] < 2:
            selector = request["authorization"].get("control_plan_id")
            selected = control.selected_control(port["context"], {"control_plan_id": selector} if selector else {})
            phase_required = (selected.get("handoff_progress") or {}).get("plan", {}).get("entry_authority", {}).get("kind") == "wrapper-phase-run"
        retry_ref = noncreation_retry(old, self.path, saved) if retry else None
        self.state = {"protocol": PROTOCOL, "revision": old["revision"] if old else 0,
            "stage_subject": 'stage:' + str(request['stage']) + ':' + str(uuid.uuid4()),
            "mode": mode, "stage": request["stage"], "status": "active", "step": "prepare-dispatch",
            "next_stage": next_stage, "phase_complete": not phase_required,
            "packages": {**retained, **resolved["packages"]}, "handoff": saved, "control": copy.deepcopy(port),
            "checkpoint_path": str(self.path.resolve()),
            "scope_authority": copy.deepcopy(old.get("scope_authority") if old and request["stage"] >= 3 else None),
            "consumed_readiness": consumed_readiness,
            "design_predecessor": predecessor_ref,
            "implementation_predecessor": implementation_ref,
            "noncreation_retry": retry_ref,
            "scope_proposals": {}, "scope_seals": {},
            "dispatch": None, "accepted": None, "action": None, "pending": None,
            "events": {}, "observations": [], "transaction": None, "transaction_source": None,
            "transaction_result": None, "stopped": False,
            "retained_host_actions": [],
            "control_transactions": {identity: copy.deepcopy(transaction)
                for identity, transaction in (old or {}).get('control_transactions', {}).items()
                if transaction.get('scope') == 'archive' and transaction.get('intent_persisted') is True
                and transaction.get('reserve_action') and transaction.get('operation_id')},
            "native_finalizations": copy.deepcopy(old.get('native_finalizations', []) if old else []),
            "native_host": native_host,
            "workspace_evidence": selection,
            "host_preflight": copy.deepcopy(data.get('native_host')),
            "host": {"generation": 0, "status": "unknown", "proof": None, "query": None,
                     "seen": {}, "calls": {}, "last_stop": None},
            "history": (old["history"] + [{k: copy.deepcopy(value) for k, value in old.items() if k != "history"}]) if old and predecessor_ref is None and implementation_ref is None and retry_ref is None else []}
        self.save()
        return self.advance()

    def call(self, operation, *, _source=None, **extra):
        s = self.state
        if operation in {"prepare", "receive", "accept"}:
            self.require_not_stopping()
        self.require_controls_reconciled()
        require(s["transaction"] is None, "transaction_pending", "consume the original B transaction before new business input")
        if operation in {"receive", "accept"}:
            self.require_business_ready()
        if operation == 'accept':
            require(s['stopped'] and not self.stop_barrier_pending(), 'host_evidence_missing', 'acceptance requires all original native calls and writers to be closed')
            if s['stage'] == 3:
                payload = s['dispatch']['delivery']['message']['payload']
                claims, applications = self.review_applications(payload['candidate_commit'])
                require(all(axis in claims and applications[axis]['applicable'] and
                    claims[axis]['reviewer_ref'] == payload['review'][axis]['reviewer_ref'] and
                    claims[axis]['source_candidate'] == payload['review'][axis]['candidate']
                    for axis in ('standards', 'spec')), 'review_incomplete',
                    'acceptance requires original native delivery claims applicable to this exact candidate')
        if (s["stage"] == 2 and (operation == "accept" or
                operation == "receive" and extra.get("result", {}).get("status") == "completed")):
            authority = s.get("scope_authority")
            require(authority is not None and authority.get("state") == "sealed", "scope_incomplete",
                    "seal the complete planning scope before accepting its publication")
            extra.update(scope_authority=copy.deepcopy(authority), scope_checkpoint_path=str(self.path.resolve()))
        if s['stage'] >= 2 and operation == 'prepare':
            extra['scope_checkpoint_path'] = str(self.path.resolve())
        request = {"protocol": handoff.PROTOCOL, "operation": operation,
            "base_control_digest": entry.digest(s['control']), "base_revision": s['revision'], **extra}
        request["handoff" if operation == "prepare" else "record"] = copy.deepcopy(s["handoff"] if operation == "prepare" else s["dispatch"])
        s["transaction"], s["transaction_source"], s["transaction_result"] = request, copy.deepcopy(_source), None
        self.save()
        return self.replay_transaction()

    def replay_transaction(self):
        s = self.state
        require(s["transaction"] is not None, "transaction_missing", "no original B transaction")
        if s["transaction_result"] is None:
            require(entry.digest(s['control']) == s['transaction']['base_control_digest'],
                    'authority_changed', 'original B replay cannot overwrite subsequent controller recovery')
            request = {key: value for key, value in s['transaction'].items()
                       if key not in {'base_control_digest', 'base_revision'}}
            self.apply(dispatch.handle({**request, 'control': copy.deepcopy(s['control'])}))
        result = read_stage_transaction_result(s)
        self.consume_transaction(result)
        return result

    def apply(self, result):
        # Save the result independently. Recovery can consume it without calling
        # B again; the original request/envelope remains until atomic consumption.
        value = {key: value for key, value in result.items() if key != 'checkpoint'}
        request_digest = entry.digest(self.state['transaction'])
        store = state_store.open_store(self.path, receipt_action=self._receipt_action)
        current = store.open_current()
        proof = {'request_digest': request_digest, 'result': value}
        require(len(state_store.encode({'format': state_store.FORMAT, 'run_id': current.run_id,
                'kind': 'evidence', 'value': proof})) <= state_store.SUBJECT_LIMIT,
                'state_capacity', 'original B result exceeds its fixed proof budget')
        self.state['transaction_result'] = {'kind': 'stage-transaction-result-ref-v1',
            'request_digest': request_digest, 'result_digest': entry.digest(value),
            'reference': store.write_object(current.run_id, 'evidence', proof), 'status': value['status']}
        if 'checkpoint' in result:
            self.state['control']['context'] = result['checkpoint']['context']
            self.state['control_receipt'] = result['checkpoint'].get('discussion_receipt')
            self.next_envelope(self.state['control_receipt'])
        self.save()

    def consume_transaction(self, result):
        s = self.state
        request, source = s["transaction"], s["transaction_source"]
        require(request is not None and result is not None, "transaction_pending", "original B result required")
        if request["operation"] == "prepare" and "record" not in result:
            self.block(entry.PreparationError("outcome_unknown", "recover the original reserved launch from its checkpoint owner"))
            return
        if s["status"] == "blocked" and s.get("error", {}).get("code") not in {"technical_error", "no_progress"}:
            s["status"] = "active"
            s.pop("error", None)
        if "checkpoint" in result:
            s["control"]["context"] = result["checkpoint"]["context"]
            s["control_receipt"] = result["checkpoint"].get("discussion_receipt")
            self.next_envelope(s["control_receipt"])
        if "record" in result:
            s["dispatch"] = result["record"]
        if "accepted" in result:
            s["accepted"] = result["accepted"]
        operation = request["operation"]
        if operation == "prepare":
            s["step"] = "launch"
            if (s["stage"] == 3 and s.get("scope_authority") is None and
                    s["handoff"].get("inline_authority") is not None):
                s["scope_authority"] = copy.deepcopy(s["handoff"]["inline_authority"])
            if s["stage"] == 3 and s.get("scope_authority") is not None:
                authority = s["scope_authority"]
                s["control"]["context"]["handoff_progress"]["plan_authority"] = {
                    "revision": authority["revision"], "authority_digest": entry.digest(authority),
                    "plan_digest": authority["execution_plan"]["plan_digest"],
                    "contract_digest": authority["execution_plan"]["contract_digest"]}
        elif operation in {"bind", "reconcile"}:
            s["step"] = "bound" if result["status"] == "bound" else "host-response"
            if result["status"] == "not-created":
                s["status"], s["step"] = "cancelled", "not-created"
        elif operation == "receive":
            status = result["status"]
            s.pop("continuation_intent", None)
            s["step"] = {"received": "received", "continue": "continue", "needs_input": "decision",
                         "technical_error": "technical-error", "accepted": "accepted"}[status]
            if source is not None:
                s["last_observation"] = copy.deepcopy(source)
            if status == "received":
                subject = {**self.subject(), "delivery_digest": s["dispatch"]["delivery"]["digest"]}
                s["pending"] = pending_decision("acceptance", subject, "Controller acceptance of the verified candidate")
                s["status"] = "needs_input"
            elif status == "needs_input":
                s["pending"] = pending_decision("user-decision", self.subject(), request["result"]["payload"].get("question"))
                s["status"] = "needs_input"
            elif status == "technical_error":
                s["status"] = "blocked"
                s["error"] = {"code": "technical_error", "message": request["result"]["payload"], "downstream_ready": False}
                self.set_business_block("technical_error", request["result"])
            elif status == "continue":
                s["continuation_intent"] = {"kind": "business", "result": copy.deepcopy(request["result"])}
                fingerprint = entry.digest(request["result"]["payload"])
                repeats = s.get("continue_repeats", 0) + 1 if fingerprint == s.get("continue_fingerprint") else 0
                s["continue_fingerprint"], s["continue_repeats"] = fingerprint, repeats
                if repeats >= 2:
                    s["status"] = "blocked"
                    s["error"] = {"code": "no_progress", "message": "repeated continuation has no new progress evidence", "downstream_ready": False}
                    self.set_business_block("no_progress", {"result": request["result"], "fingerprint": fingerprint, "repeats": repeats})
        elif operation == "accept":
            require(source is not None, "decision_required", "retain original controller decision")
            s["status"], s["step"] = "accepted", "accepted"
            s["pending"] = (stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"])
                            if s["stage"] != 2 and s["accepted"]["downstream_ready"] and s["phase_complete"] else None)
            s.setdefault("decisions", {})[source["decision_id"]] = copy.deepcopy(source)
            s["last_decision"] = copy.deepcopy(source)
        if source is not None and "event_id" in source:
            s["events"][source["event_id"]]["applied"] = True
            if "result" in source:
                s.setdefault("observed_messages", {})[self.message_key(source)] = source["event_id"]
        s["transaction"], s["transaction_source"], s["transaction_result"] = None, None, None
        s["last_result"] = ({"status": result["status"], "record_digest": result["record"]["digest"],
            "accepted_digest": result["accepted"]["digest"]} if operation == "accept" and s["stage"] == 3 else result)
        self.save()
        if operation == "accept" and s["stage"] == 2 and s["phase_complete"]:
            readiness = scope_readiness(s, self.path)
            if readiness is not None:
                pending = stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"])
                if pending is not None:
                    s["pending"] = pending
                    self.save()
                    readiness = {**readiness, "checkpoint_revision": s["revision"]}
                self._verified_readiness = (s["revision"], copy.deepcopy(readiness))

    def scope_propose(self, data):
        """Record the designer's exact candidate and manifest without granting writes."""
        entry.fields(data, {"proposal_id", "expected_revision", "candidate_commit", "manifest",
                            "implementation_required", "requirement_identity", "binding", "previous_scope_revision"},
                     {"generated_outputs"})
        entry.nonempty(data["proposal_id"])
        s = self.state
        existing = s.setdefault("scope_proposals", {}).get(data["proposal_id"])
        if existing is not None:
            require(existing["input"] == data, "scope_conflict", "proposal ID was reused with different content")
            return _view(s, {"operation": "scope-proposed", "proposal": existing}, acknowledged=True)
        self.require_business_ready()
        self.require_not_stopping()
        require(s["stage"] == 2 and s["dispatch"] is not None and s["dispatch"]["status"] == "bound" and
                not s.get("publication") and s.get("accepted") is None, "scope_changed",
                "scope proposal belongs to the active unpublished design")
        require(type(data["expected_revision"]) is int and data["expected_revision"] == 0 and
                data["previous_scope_revision"] == 0 and s.get("scope_authority") is None,
                "stale_scope_revision", "initial scope proposal must name unresolved revision zero")
        saved = s["handoff"]
        require(data["requirement_identity"] == saved["requirement_identity"] and
                data["binding"] == saved["binding"], "source_changed",
                "proposal must retain the frozen requirement and worktree binding")
        original_scope = saved["scope"]
        require(original_scope["downstream"]["state"] == "unresolved", "scope_changed",
                "only an unresolved Stage-2 scope can receive an initial proposal")
        candidate = handoff.commit(data["candidate_commit"])
        self.verify_publication_authority(candidate, before=True)
        generated_outputs = generated_outputs_for(data)
        projection = stage_scope.validate_manifest(data["manifest"], saved["binding"]["repository"], candidate,
            baseline=original_scope["baseline"], protected_paths=original_scope["protected_paths"],
            planning_paths=original_scope["owned_paths"], requirement_path=saved["requirement_identity"]["path"],
            generated_outputs=generated_outputs)
        require(type(data["implementation_required"]) is bool and
                data["implementation_required"] == bool(projection["implementation_paths"]),
                "scope_incomplete", "implementation-required decision contradicts the complete manifest")
        catalog = (execution_plan.collect_execution_plan(saved["binding"]["repository"], candidate,
                    original_scope["owned_paths"],
                    {"baseline": original_scope["baseline"], "downstream": {"manifest": data["manifest"]}},
                    saved["requirement_identity"])
                   if data["implementation_required"] else None)
        envelope = copy.deepcopy(data)
        if catalog is not None:
            envelope["execution_plan"] = {"plan_digest": catalog["plan_digest"],
                                          "contract_digest": catalog["contract_digest"]}
        proposal_digest = entry.digest(envelope)
        scope = copy.deepcopy(original_scope)
        scope.update(projection)
        scope["downstream"] = {"state": "proposed", "revision": 0,
            "implementation_required": data["implementation_required"],
            "manifest": copy.deepcopy(data["manifest"]), "proposal_ref": proposal_digest,
            "seal_ref": None}
        stage_scope.validate_scope(scope, saved["binding"]["repository"], stage=2,
            requirement_path=saved["requirement_identity"]["path"], candidate_commit=candidate,
            generated_outputs=generated_outputs)
        record = {"input": copy.deepcopy(data), "envelope": envelope,
                  "execution_plan": catalog, "digest": proposal_digest, "scope": scope,
                  "target_head": supervision._branch_oid(Path(saved["binding"]["repository"]), saved["binding"]["target_branch"])}
        s["scope_proposals"][data["proposal_id"]] = record
        self.save()
        return _view(s, {"operation": "scope-proposed", "proposal": record})

    def scope_seal(self, data):
        """Bind a saved proposal to the original Controller's durable decision."""
        entry.fields(data, {"proposal_id", "proposal_digest", "controller_ref", "decision"})
        s = self.state
        existing = s.setdefault("scope_seals", {}).get(data["proposal_id"])
        if existing is not None:
            require(existing["input"] == data, "scope_conflict", "seal ID was reused with different content")
            return _view(s, {"operation": "scope-sealed", "authority": s["scope_authority"]}, acknowledged=True)
        self.require_business_ready()
        self.require_not_stopping()
        require(s["stage"] == 2 and not s.get("publication") and s.get("accepted") is None and
                s.get("scope_authority") is None, "scope_changed", "initial seal belongs to unpublished design")
        proposal = s["scope_proposals"].get(data["proposal_id"])
        require(proposal is not None and proposal["digest"] == data["proposal_digest"],
                "scope_conflict", "seal must name the exact saved proposal")
        saved = s["handoff"]
        require(data["controller_ref"] == saved["controller_ref"] == s["control"]["context"]["controller_ref"],
                "identity_mismatch", "only the original Controller can seal the proposal")
        candidate = proposal["input"]["candidate_commit"]
        ready = s.get("publication_candidate")
        require(ready is not None and ready["payload"]["candidate_commit"] == candidate,
                "candidate_changed", "the stopped planning candidate must match the proposal")
        self.verify_publication_authority(candidate, before=True)
        require(supervision._branch_oid(Path(saved["binding"]["repository"]), saved["binding"]["target_branch"]) ==
                proposal["target_head"], "target_changed", "target moved before the scope seal")
        basis_paths = {saved["requirement_identity"]["path"]} | {item["basis"]["path"] for item in proposal["input"]["manifest"]}
        basis_blobs = {path: hashlib.sha256(handoff.blob(saved["binding"]["repository"], candidate, path)).hexdigest()
                       for path in sorted(basis_paths)}
        evidence = {"proposal_id": data["proposal_id"], "proposal_digest": proposal["digest"],
            "expected_scope_revision": 0, "candidate_commit": candidate,
            "manifest_digest": entry.digest(proposal["input"]["manifest"]),
            "requirement_identity": copy.deepcopy(saved["requirement_identity"]),
            "binding": copy.deepcopy(saved["binding"]),
            "prior_authorization_ref": saved["authorization"]["reference"],
            "decision": copy.deepcopy(data["decision"]), "target_head": proposal["target_head"],
            "basis_blobs": basis_blobs}
        change_evidence = approved_change_evidence(s, data["decision"])
        result = self.control_action({"action": "scope-seal", "evidence": evidence,
            "receipt": {"controller_ref": data["controller_ref"], "reference": data["decision"].get("reference")}})
        seal = s["control"]["context"]["handoff_progress"]["scope_seal"]
        scope = copy.deepcopy(proposal["scope"])
        scope["downstream"].update(state="sealed", revision=1, seal_ref=seal["seal_ref"])
        stage_scope.validate_scope(scope, saved["binding"]["repository"], stage=2,
            requirement_path=saved["requirement_identity"]["path"], candidate_commit=candidate,
            generated_outputs=generated_outputs_for(proposal["input"]))
        authority = {"state": "sealed", "revision": 1, "scope": scope,
                     "proposal": copy.deepcopy(proposal["envelope"]),
                     "execution_plan": (execution_plan.authority_envelope(proposal["execution_plan"], 1)
                                        if proposal["execution_plan"] is not None else None),
                     "proposal_digest": proposal["digest"],
                     "seal": copy.deepcopy(seal), "seal_ref": seal["seal_ref"], "candidate_commit": candidate,
                     "approved_changes": [change_evidence] if change_evidence is not None else []}
        s["scope_authority"] = authority
        s["scope_seals"][data["proposal_id"]] = {"input": copy.deepcopy(data), "authority_digest": entry.digest(authority)}
        self.save()
        return _view(s, {"operation": "scope-sealed", "authority": authority})

    def scope_revise(self, data):
        """Correct accepted planning scope before any saved implementation intent."""
        require_scope_correction_available(self.outer)
        entry.fields(data, {"revision_id", "expected_scope_revision", "previous_scope_digest",
                            "requirement_identity", "binding", "planning_commit", "planning_merge_commit",
                            "candidate_commit", "old_manifest", "manifest", "implementation_required",
                            "diff", "reason", "controller_ref", "decision"}, {"generated_outputs"})
        entry.nonempty(data["revision_id"]); entry.nonempty(data["reason"])
        s = self.state
        history = s.get("scope_revisions", {})
        prior = history.get(data["revision_id"])
        if prior is not None:
            require(prior["input"] == data, "scope_conflict", "revision ID was reused with different content")
            return _view(s, {"operation": "scope-revision", "revision": prior}, acknowledged=True)
        transaction = s.get("scope_revision_transaction")
        if transaction is not None:
            require(transaction["input"] == data, "scope_conflict", "finish the original scope correction first")
        else:
            self.require_business_ready()
            require(s["status"] == "accepted" and s.get("stopped") and s.get("phase_complete") and
                    s.get("host", {}).get("status") == "stopped" and not self.stop_barrier_pending() and
                    not self.unresolved_host_actions() and (s.get("scope_authority") or {}).get("state") == "sealed",
                    "scope_revision_unavailable", "correction requires the current stopped accepted design")
            require(s["transaction"] is None and not self.unresolved_control_transactions(),
                    "transaction_pending", "settle prior transactions")
            authority = s["scope_authority"]
            old_scope = authority["scope"]
            require(type(data["expected_scope_revision"]) is int and
                    data["expected_scope_revision"] == authority["revision"] and
                    data["previous_scope_digest"] == entry.digest(old_scope) and
                    data["old_manifest"] == old_scope["downstream"]["manifest"],
                    "stale_scope_revision", "revision must name the exact current manifest and digest")
            design = s
            require(design is not None and design.get("accepted") is not None,
                    "scope_revision_unavailable", "accepted planning source is unavailable")
            payload = design["accepted"]["payload"]
            design_source = design["handoff"]
            current_binding = design_source["binding"]
            require(data["requirement_identity"] == design_source["requirement_identity"] and
                    data["binding"] == design_source["binding"] == current_binding and
                    data["planning_commit"] == payload["planning_commit"] == authority["candidate_commit"] and
                    data["planning_merge_commit"] == payload["planning_merge_commit"] and
                    data["candidate_commit"] == authority["candidate_commit"] and
                    data["controller_ref"] == design_source["controller_ref"] == s["control"]["context"]["controller_ref"],
                    "source_changed", "revision must retain the published design, requirement and Controller")
            diff = stage_scope.manifest_diff(data["old_manifest"], data["manifest"])
            require(data["diff"] == diff and any(diff.values()), "scope_conflict",
                    "revision must declare the exact nonempty manifest difference")
            generated_outputs = generated_outputs_for(data)
            projection = stage_scope.validate_manifest(data["manifest"], data["binding"]["repository"],
                data["candidate_commit"], baseline=old_scope["baseline"],
                protected_paths=old_scope["protected_paths"], planning_paths=old_scope["owned_paths"],
                requirement_path=data["requirement_identity"]["path"],
                generated_outputs=generated_outputs)
            require(type(data["implementation_required"]) is bool and
                    data["implementation_required"] == bool(projection["implementation_paths"]),
                    "scope_incomplete", "revision implementation requirement disagrees with manifest")
            proposed_scope = copy.deepcopy(old_scope)
            proposed_scope.update(projection)
            catalog = (execution_plan.collect_execution_plan(data["binding"]["repository"],
                        data["candidate_commit"], old_scope["owned_paths"],
                        {"baseline": old_scope["baseline"], "downstream": {"manifest": data["manifest"]}},
                        data["requirement_identity"])
                       if data["implementation_required"] else None)
            envelope = copy.deepcopy(data)
            if catalog is not None:
                envelope["execution_plan"] = {"plan_digest": catalog["plan_digest"],
                                              "contract_digest": catalog["contract_digest"]}
            proposed_scope["downstream"] = {"state": "proposed", "revision": authority["revision"] + 1,
                "implementation_required": data["implementation_required"],
                "manifest": copy.deepcopy(data["manifest"]), "proposal_ref": entry.digest(envelope),
                "seal_ref": None}
            stage_scope.validate_scope(proposed_scope, data["binding"]["repository"], stage=2,
                requirement_path=data["requirement_identity"]["path"],
                planning_paths=payload["planning_paths"], candidate_commit=data["candidate_commit"],
                generated_outputs=generated_outputs)
            change_evidence = approved_change_evidence(s, data["decision"])
            protected = sorted(set(old_scope["protected_paths"]) | set(payload["planning_paths"]))
            proposed = sorted(set(proposed_scope["implementation_paths"] + proposed_scope["closure_paths"]) - set(protected))
            snapshot = control_git.scope_revision_snapshot(data["binding"]["worktree"],
                data["planning_merge_commit"], protected, proposed)
            transaction = {"input": copy.deepcopy(data), "envelope": envelope,
                "execution_plan": catalog, "state": "prepared",
                "old_authority": copy.deepcopy(authority), "proposed_scope": proposed_scope,
                "snapshot": snapshot, "control_result": None,
                "approved_change_evidence": change_evidence}
            s["scope_revision_transaction"] = transaction
            self.save()
        if transaction["state"] == "prepared":
            old_scope = transaction["old_authority"]["scope"]
            protected = sorted(set(old_scope["protected_paths"]) | set(s["accepted"]["payload"]["planning_paths"]))
            proposed = sorted(set(transaction["proposed_scope"]["implementation_paths"] +
                transaction["proposed_scope"]["closure_paths"]) - set(protected))
            actual = control_git.scope_revision_snapshot(data["binding"]["worktree"],
                data["planning_merge_commit"], protected, proposed)
            stage_scope.validate_snapshot(transaction["snapshot"], actual)
            evidence = {"revision_id": data["revision_id"], "expected_scope_revision": data["expected_scope_revision"],
                "previous_scope_digest": data["previous_scope_digest"],
                "new_scope_digest": entry.digest(transaction["proposed_scope"]),
                "scope_diff_digest": entry.digest(data["diff"]),
                "snapshot_digest": entry.digest(actual),
                "requirement_identity": copy.deepcopy(data["requirement_identity"]),
                "binding": copy.deepcopy(data["binding"]),
                "planning_commit": data["planning_commit"],
                "planning_merge_commit": data["planning_merge_commit"],
                "candidate_commit": data["candidate_commit"], "decision": copy.deepcopy(data["decision"]),
                "stop_evidence_ref": entry.digest(s["host"]["last_stop"]) if s["host"].get("last_stop") else None}
            self.control_action({"action": "scope-revise", "evidence": evidence,
                "receipt": {"controller_ref": data["controller_ref"],
                            "reference": data["decision"].get("reference")}})
            receipt = s["control"]["context"]["handoff_progress"]["scope_revisions"][-1]
            transaction.update(state="sealed", control_result=copy.deepcopy(receipt))
            self.save()
        s["scope_authority"] = corrected_scope_authority(transaction, transaction["control_result"])
        s.setdefault("scope_revisions", {})[data["revision_id"]] = copy.deepcopy(transaction)
        s.pop("scope_revision_transaction", None)
        pending = (stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"])
                   if s["scope_authority"]["scope"]["downstream"]["implementation_required"] else None)
        if pending is not None:
            previous = s.get("pending")
            if previous and previous["kind"] == "stage-entry" and previous["subject"] == pending["subject"]:
                pending = previous
            if any(decision.get("subject") == pending["subject"] and
                   decision.get("answer") in {"confirm", "continuous"}
                   for decision in s.get("decisions", {}).values()):
                pending = None
        s["pending"] = pending
        self.save()
        return _view(s, {"operation": "scope-revision-sealed", "authority": s["scope_authority"]})

    def rejected_plan_revision(self, transaction):
        return _view(self.state, {"operation": "plan-revision-rejected",
            "revision_id": transaction["input"]["revision_id"],
            "result": copy.deepcopy(transaction["rejection"]["result"])}, acknowledged=True)

    def plan_revision_path_changes(self, repository, start, end, relative):
        try:
            control_git.git(repository, "merge-base", "--is-ancestor", start, end)
            commits = control_git.git(repository, "rev-list", "--first-parent", "--reverse",
                                      start + ".." + end).decode().splitlines()
            previous_commit = start
            previous = control_git.committed_fingerprint(repository, start, relative)
            changes = []
            for commit in commits:
                parents = control_git.git(repository, "rev-list", "--parents", "-n", "1", commit).decode().split()
                if parents != [commit, previous_commit]:
                    return None
                current = control_git.committed_fingerprint(repository, commit, relative)
                if current != previous:
                    changes.append(current)
                previous_commit, previous = commit, current
            return changes if previous_commit == end else None
        except control.ControlError:
            return None

    def plan_revision_retry_ready(self, transaction):
        s = self.state
        host = s["host"]
        proof = host.get("proof") or {}
        if (s["status"] not in {"active", "blocked"} or
                (s["status"] == "blocked" and
                 (s.get("error") or {}).get("code") != "business_result_missing") or
                self.stop_intent() is not None or
                s.get("transport_loss") is not None or s.get("business_block") is not None or
                s["scope_authority"] != transaction["old_authority"] or
                host["status"] not in {"idle", "turn-completed"} or
                proof.get("generation") != host["generation"] or
                proof.get("receipt", {}).get("ref") != self.bound_ref() or
                proof.get("receipt", {}).get("status") not in {"idle", "turn-completed"} or
                proof.get("event_id") == transaction["rejection"]["stop"].get("event_id") or
                self.unresolved_host_actions() or
                self.unresolved_control_transactions() or s["transaction"] is not None):
            return False
        repository = Path(s["handoff"]["binding"]["worktree"])
        head = control_git.snapshot(repository)["head"]
        checkpoint = s["control"]["context"]["handoff_progress"]
        baseline = checkpoint["git_baseline_commit"]
        rejection = transaction["rejection"]
        rejected_head = rejection["snapshot"]["head"]
        previously_accepted = set(rejection["accepted_executions"])
        for relative in rejection["result"]["retired_paths"]:
            expected = control_git.committed_fingerprint(repository, baseline, relative)
            if (control_git.file_fingerprint(repository, relative) != expected or
                    control_git.committed_fingerprint(repository, head, relative) != expected or
                    control_git.git(repository, "status", "--porcelain", "-z", "--", relative)):
                return False
            cleaned = False
            for execution in checkpoint["executions"]:
                if (execution["state"] != "accepted" or
                        execution["allocation_digest"] in previously_accepted or
                        execution.get("plan_authority") != checkpoint["plan_authority"] or
                        relative not in execution["paths"] or
                        execution.get("git_snapshot", {}).get("files", {}).get(relative) !=
                        rejection["snapshot"]["files"][relative] or
                        relative not in execution.get("git_result_snapshot", {}) or
                        execution.get("git_result_snapshot", {}).get(relative) != expected):
                    continue
                allocation_head = execution["git_snapshot"]["head"]
                before = self.plan_revision_path_changes(repository, rejected_head,
                                                         allocation_head, relative)
                after = self.plan_revision_path_changes(repository, allocation_head, head, relative)
                committed_at_allocation = control_git.committed_fingerprint(
                    repository, allocation_head, relative)
                if (before == [] and after == ([] if committed_at_allocation == expected else [expected]) and
                        execution["git_snapshot"]["files"][relative] != expected):
                    cleaned = True
                    break
            if not cleaned:
                return False
        return True

    def prepare_plan_revision(self, data):
        """Persist a Controller intent before asking the original host to stop."""
        entry.fields(data, {"revision_id", "expected_authority_revision",
            "expected_authority_digest", "controller_ref", "classification", "report",
            "planning_paths"}, {"manifest", "generated_outputs", "inline_plan",
                                 "validation_plan"})
        s = self.state
        require(s["stage"] == 3 and s.get("scope_authority") is not None,
                "plan_revision_unavailable", "an active sealed Stage-3 authority is required")
        require(data["controller_ref"] == s["handoff"]["controller_ref"] ==
                s["control"]["context"]["controller_ref"],
                "identity_mismatch", "only the original Controller may revise the plan")
        entry.nonempty(data["revision_id"])
        prior = s.get("plan_revision_transaction")
        historical = s.get("plan_revision_history", {}).get(data["revision_id"])
        retry = None
        if historical is not None:
            require(historical["input"] == data, "plan_revision_conflict",
                    "historical revision ID was reused with different content")
            if historical["state"] == "activated":
                return _view(s, {"operation": "plan-revision-activated",
                                 "revision_id": data["revision_id"]}, acknowledged=True)
            if not self.plan_revision_retry_ready(historical):
                return self.rejected_plan_revision(historical)
            retry = historical
        if prior is not None and prior["input"]["revision_id"] == data["revision_id"]:
            require(prior["input"] == data, "plan_revision_conflict",
                    "revision ID was reused with different content")
            return self.reconcile_plan_revision({"revision_id": data["revision_id"]})
        require(prior is None or prior["state"] == "activated", "plan_revision_pending",
                "finish the existing plan revision before opening another")
        require(s["status"] not in {"accepted", "cancelled", "cancelling"} and
                (s.get("dispatch") or {}).get("status") == "bound",
                "plan_revision_unavailable", "revision requires the active original dispatcher")
        require((s["control"]["context"].get("handoff_progress") or {}).get("state") != "validating",
                "plan_revision_unavailable", "settle the original final validation attempt before revising the plan")
        entry.fields(data["classification"], {"kind", "reference"})
        require(data["classification"]["kind"] == "internal", "user_decision_required",
                "public or uncertain contract impact requires the original user decision")
        entry.nonempty(data["classification"]["reference"])
        entry.fields(data["report"], {"ticket_ids", "step_ids", "reason",
            "production_facts", "contract_before", "contract_after", "dependency_impact",
            "verification_impact", "carry_forward"}, {"manifest_diff"})
        for field in ("ticket_ids", "step_ids", "dependency_impact", "verification_impact",
                      "carry_forward"):
            items = data["report"][field]
            require(isinstance(items, list) and (field == "carry_forward" or bool(items)),
                    "invalid_request", "revision report requires exact unique " + field)
            for index, item in enumerate(items):
                require(isinstance(item, str) and execution_plan.IDENTIFIER.fullmatch(item) is not None,
                        "invalid_request", f"report.{field}[{index}] requires a valid ID")
            require(len(items) == len(set(items)), "invalid_request",
                    "revision report requires exact unique " + field)
        for field in ("reason", "production_facts", "contract_before", "contract_after"):
            entry.nonempty(data["report"][field])
        authority = s["scope_authority"]
        require(data["expected_authority_revision"] == authority["revision"] and
                data["expected_authority_digest"] == entry.digest(authority),
                "stale_plan_revision", "report must name the exact current authority")
        require(data["report"]["contract_before"] == data["report"]["contract_after"] ==
                authority["execution_plan"]["contract_digest"], "contract_changed",
                "internal revision cannot change the sealed functional contract")
        catalog = execution_plan.current_catalog(authority)
        if "validation_plan" in data:
            try:
                control.validate_revision_validation_plan(
                    s["control"]["context"]["handoff_progress"]["validation_plan"],
                    data["validation_plan"], s["handoff"]["binding"])
            except control.ControlError as error:
                raise entry.PreparationError("invalid_plan_revision", str(error)) from error
        ticket_ids = {ticket["ticket_id"] for ticket in catalog["tickets"]}
        step_ids = {step["id"] for ticket in catalog["tickets"]
                    for step in ticket["plan"]["steps"] + ticket["plan"]["artifact_steps"]}
        verification_ids = {item["id"] for ticket in catalog["tickets"]
                            for item in ticket["plan"]["verifications"]}
        require(set(data["report"]["ticket_ids"]) <= ticket_ids and
                set(data["report"]["step_ids"] + data["report"]["dependency_impact"]) <= step_ids and
                set(data["report"]["verification_impact"]) <= verification_ids and
                set(data["report"]["carry_forward"]) <= step_ids,
                "invalid_plan_revision", "report references unknown current Ticket, step or verification")
        planning = data["planning_paths"]
        require(isinstance(planning, list), "invalid_request", "exact sorted planning paths required")
        for index, relative in enumerate(planning):
            require(isinstance(relative, str) and bool(relative), "invalid_request",
                    f"planning_paths[{index}] requires a path")
        require(planning == sorted(set(planning)) and
                (planning or authority.get("kind") == "inline"),
                "invalid_request", "exact sorted planning paths required")
        closure = set(authority["scope"]["closure_paths"])
        require(set(planning) <= closure and set(planning) <= set(s["handoff"]["scope"]["protected_paths"]),
                "invalid_scope", "planning paths must be closure-owned protected planning files")
        old_manifest = authority["scope"]["downstream"]["manifest"]
        proposed_manifest = data.get("manifest", old_manifest)
        stage_scope.manifest_projection(proposed_manifest)
        if authority.get("kind") == "inline":
            require(planning == [] and isinstance(data.get("inline_plan"), dict),
                    "invalid_plan_revision", "inline revision needs exact Controller plan bytes and no planning file")
            proposed_scope = copy.deepcopy(authority["scope"])
            proposed_scope["downstream"]["manifest"] = copy.deepcopy(proposed_manifest)
            projected = stage_scope.manifest_projection(proposed_manifest)
            proposed_scope["implementation_paths"] = projected["implementation_paths"]
            proposed_scope["closure_paths"] = projected["closure_paths"]
            proposed_scope["owned_paths"] = copy.deepcopy(projected["implementation_paths"])
            new_catalog = execution_plan.collect_inline_plan(data["inline_plan"], proposed_scope,
                s["handoff"]["semantic"], s["handoff"]["requirement_identity"],
                repository=s["handoff"]["binding"]["worktree"])
            previous = catalog["tickets"][0]
            require(new_catalog["contract_digest"] == catalog["contract_digest"] and
                    new_catalog["tickets"][0]["ticket_id"] == previous["ticket_id"] and
                    new_catalog["tickets"][0]["plan_version"] == previous["plan_version"] + 1,
                    "contract_changed", "inline revision must preserve contract and advance its plan version")
        else:
            require("inline_plan" not in data, "invalid_plan_revision",
                    "committed planning revision cannot replace an inline plan")
        if "manifest_diff" in data["report"]:
            require(data["report"]["manifest_diff"] ==
                    stage_scope.manifest_diff(old_manifest, proposed_manifest),
                    "scope_changed", "reported manifest diff differs from exact proposal")
        else:
            require(proposed_manifest == old_manifest, "scope_changed",
                    "manifest change requires its exact reported diff")
        if "generated_outputs" in data:
            require(isinstance(data["generated_outputs"], dict), "invalid_request",
                    "generated output inventory must be an exact map")
        require(not s.get("scope_revision_transaction") and not s.get("final_integration_recovery") and
                not s.get("publication_transaction"), "plan_revision_unavailable",
                "another publication or revision recovery has priority")
        selected = {identity for ticket in catalog['tickets'] for group in ticket['plan']['behavior_slices']
            if set(group['step_ids']) & set(data['report']['step_ids'] + data['report']['dependency_impact'])
            for identity in group['step_ids']}
        preflight = execution_plan.dispatch_preflight(catalog, selected)
        if prior is not None:
            s.setdefault("plan_revision_history", {})[prior["input"]["revision_id"]] = copy.deepcopy(prior)
        if retry is not None:
            s["plan_revision_history"].pop(data["revision_id"])
        s["plan_revision_transaction"] = {"state": "stopping", "input": copy.deepcopy(data),
            "input_digest": entry.digest(data), "old_authority": copy.deepcopy(authority),
            "preflight": preflight,
            **({"prior_rejections": copy.deepcopy([*retry.get("prior_rejections", []),
                retry["rejection"]])} if retry is not None else {})}
        self.save()
        return self.advance_plan_revision()

    def advance_plan_revision(self):
        s = self.state
        transaction = s["plan_revision_transaction"]
        if s["status"] in {"paused", "cancelled", "pausing", "cancelling"}:
            return _view(s, {"operation": "plan-revision-suspended", "state": transaction["state"]})
        if transaction["state"] != "stopping":
            if transaction["state"] == "planning-committed":
                return _view(s, {"operation": "reconcile-plan-revision",
                    "revision_id": transaction["input"]["revision_id"]})
            if transaction["state"] == "prepared":
                pending = self.stop_barrier_pending()
                if s["host"]["status"] != "stopped" or pending:
                    return _view(s, {"operation": "reconcile-stop-barrier", "pending": pending})
                current = control_git.snapshot(s["handoff"]["binding"]["worktree"])
                if current["head"] != transaction["snapshot"]["head"]:
                    return _view(s, {"operation": "inspect-planning-commit",
                        "revision_id": transaction["input"]["revision_id"],
                        "expected_parent": transaction["snapshot"]["head"],
                        "current_head": current["head"]})
            return _view(s, {"operation": "plan-revision-" + transaction["state"],
                             "revision_id": transaction["input"]["revision_id"],
                             **({"write_release": transaction["write_release"]}
                                if transaction["state"] == "prepared" else {})})
        if s["host"]["status"] != "stopped":
            if self.unresolved_host_actions():
                return self.query_host()
            if any(action["operation"] == "stop-host" for action in
                   [s.get("host_action") or {}, *s.get("retained_host_actions", [])]):
                return self.query_host()
            return self.effect("stop-host", {"subject": self.subject(), "ref": self.bound_ref()})
        pending = self.stop_barrier_pending()
        if pending or self.unresolved_control_transactions() or s["transaction"] is not None:
            return _view(s, {"operation": "reconcile-stop-barrier", "pending": pending})
        proposed = transaction["input"].get("manifest", transaction["old_authority"]["scope"]["downstream"]["manifest"])
        proposed_paths = stage_scope.manifest_projection(proposed)["implementation_paths"]
        snapshot = control_git.plan_revision_snapshot(s["handoff"]["binding"]["worktree"],
            transaction["old_authority"], s["handoff"]["scope"]["protected_paths"],
            transaction["input"]["planning_paths"],
            additional_paths=sorted(set(proposed_paths) - set(transaction["old_authority"]["scope"]["implementation_paths"])))
        retired_paths = sorted(set(transaction["old_authority"]["scope"]["implementation_paths"]) -
                               set(proposed_paths))
        nonbaseline = [relative for relative in retired_paths
            if snapshot["files"][relative] != control_git.committed_fingerprint(
                s["handoff"]["binding"]["worktree"],
                s["control"]["context"]["handoff_progress"]["git_baseline_commit"], relative)]
        if nonbaseline:
            # No planning write was released: keep the old authority usable for
            # a real Execution Agent to restore these paths before retrying.
            message = "retired implementation paths still differ from baseline: " + ", ".join(nonbaseline)
            stop = copy.deepcopy(s["host"]["last_stop"])
            accepted_executions = sorted(execution["allocation_digest"] for execution in
                s["control"]["context"]["handoff_progress"]["executions"]
                if execution["state"] == "accepted")
            transaction["rejection"] = {"snapshot": snapshot, "stop": stop,
                "accepted_executions": accepted_executions,
                "result": {"code": "invalid_plan_revision", "message": message,
                    "retired_paths": nonbaseline,
                    "evidence_digest": entry.digest({"input_digest": transaction["input_digest"],
                        "old_authority_digest": entry.digest(transaction["old_authority"]),
                        "snapshot": snapshot, "stop": stop,
                        "accepted_executions": accepted_executions,
                        "retired_paths": nonbaseline})}}
            transaction["state"] = "rejected"
            s.setdefault("plan_revision_history", {})[transaction["input"]["revision_id"]] = copy.deepcopy(transaction)
            s.pop("plan_revision_transaction")
            s["status"] = "active"
            s["step"] = "continue"
            s.pop("error", None)
            s["continuation_intent"] = {"kind": "plan-revision-rejected",
                "revision_id": transaction["input"]["revision_id"],
                "retired_paths": nonbaseline}
            self.save()
            raise _RejectedPlanRevision("invalid_plan_revision", message)
        transaction["snapshot"] = snapshot
        transaction["state"] = "prepared"
        transaction["write_release"] = entry.digest({"revision_id": transaction["input"]["revision_id"],
            "controller_ref": transaction["input"]["controller_ref"],
            "planning_paths": transaction["input"]["planning_paths"],
            "snapshot": snapshot, "input_digest": transaction["input_digest"]})
        self.save()
        return self.advance_plan_revision()

    def reconcile_plan_revision(self, data):
        entry.fields(data, {"revision_id"})
        historical = self.state.get("plan_revision_history", {}).get(data["revision_id"])
        if historical is not None:
            if historical["state"] == "rejected":
                return self.rejected_plan_revision(historical)
            return _view(self.state, {"operation": "plan-revision-activated",
                                      "revision_id": data["revision_id"]}, acknowledged=True)
        transaction = self.state.get("plan_revision_transaction")
        require(transaction is not None and transaction["input"]["revision_id"] == data["revision_id"],
                "plan_revision_missing", "reconcile the exact saved revision")
        if transaction["state"] == "activated":
            return _view(self.state, {"operation": "plan-revision-activated",
                                      "revision_id": data["revision_id"]}, acknowledged=True)
        if transaction["state"] == "planning-committed":
            if self.state["status"] in {"paused", "pausing", "cancelled", "cancelling"}:
                return self.advance_plan_revision()
            return self.activate_plan_revision()
        return self.advance_plan_revision()

    def commit_plan_revision(self, data):
        entry.fields(data, {"revision_id", "controller_ref", "write_release", "planning_commit"})
        s = self.state
        transaction = s.get("plan_revision_transaction")
        historical = s.get("plan_revision_history", {}).get(data["revision_id"])
        if historical is not None:
            require(historical["state"] == "activated", "plan_revision_unavailable",
                    "rejected revision has no planning write release")
            require(data == historical["commit_input"], "plan_revision_conflict",
                    "historical planning commit input changed")
            return _view(s, {"operation": "plan-revision-activated",
                             "revision_id": data["revision_id"]}, acknowledged=True)
        require(transaction is not None and data["revision_id"] == transaction["input"]["revision_id"],
                "plan_revision_missing", "commit the exact prepared Controller revision")
        require(data["controller_ref"] == transaction["input"]["controller_ref"],
                "identity_mismatch", "only the original Controller may commit planning")
        if transaction["state"] == "activated":
            require(data == transaction["commit_input"], "plan_revision_conflict",
                    "activated revision input changed")
            return _view(s, {"operation": "plan-revision-activated",
                             "authority": s["scope_authority"]}, acknowledged=True)
        require(transaction["state"] in {"prepared", "planning-committed"} and
                data["write_release"] == transaction["write_release"],
                "plan_revision_unavailable", "the exact prepared write release is required")
        require(s["status"] not in {"paused", "pausing", "cancelled", "cancelling"},
                "plan_revision_suspended", "paused or cancelled revisions cannot activate")
        require(s["host"]["status"] == "stopped" and not self.stop_barrier_pending() and
                not self.unresolved_host_actions() and not self.unresolved_control_transactions() and
                s["transaction"] is None, "host_evidence_missing",
                "all original writers and calls must remain settled")
        if transaction["state"] == "planning-committed":
            require(data == transaction["commit_input"], "plan_revision_conflict",
                    "planning commit input changed")
            return self.activate_plan_revision()
        repository = s["handoff"]["binding"]["worktree"]
        old_authority = transaction["old_authority"]
        inline = old_authority.get("kind") == "inline"
        facts = (control_git.verify_inline_plan_revision if inline else
                 control_git.verify_plan_revision)(repository, transaction["snapshot"],
                                                   data["planning_commit"])
        scope = copy.deepcopy(old_authority["scope"])
        scope["downstream"]["manifest"] = copy.deepcopy(transaction["input"].get("manifest",
            old_authority["scope"]["downstream"]["manifest"]))
        projected = stage_scope.manifest_projection(scope["downstream"]["manifest"])
        scope["implementation_paths"] = projected["implementation_paths"]
        scope["closure_paths"] = projected["closure_paths"]
        scope["owned_paths"] = copy.deepcopy(scope["implementation_paths"])
        scope["downstream"]["implementation_required"] = bool(scope["implementation_paths"])
        scope["downstream"]["revision"] += 1
        sources = ([] if inline else sorted({item["path"] for item in
                   execution_plan.current_catalog(old_authority)["contract_projection"]["files"]}))
        require(set(transaction["input"]["planning_paths"]) <= set(sources),
                "invalid_scope", "new planning sources need an explicit current contract boundary")
        catalog = (execution_plan.collect_inline_plan(transaction["input"]["inline_plan"], scope,
                    s["handoff"]["semantic"], s["handoff"]["requirement_identity"],
                    repository=repository)
                   if inline else execution_plan.collect_execution_plan(repository, data["planning_commit"],
                    sources, scope, s["handoff"]["requirement_identity"]))
        require(catalog["contract_digest"] == old_authority["execution_plan"]["contract_digest"],
                "contract_changed", "planning commit changed the immutable functional contract")
        old_tickets = {item["ticket_id"]: item for item in execution_plan.current_catalog(old_authority)["tickets"]}
        new_tickets = {item["ticket_id"]: item for item in catalog["tickets"]}
        require(set(old_tickets) == set(new_tickets), "contract_changed",
                "internal revision cannot add or remove a behavior Ticket")
        historical_ids = {step['id'] for prior in s.get('plan_revision_history', {}).values()
                          if prior.get('state') == 'activated'
                          for authority in (prior['old_authority'], prior['proposed_authority'])
                          for ticket in execution_plan.current_catalog(authority)['tickets']
                          for step in ticket['plan']['steps'] + ticket['plan']['artifact_steps']}
        current_ids = {step['id'] for ticket in old_tickets.values()
                       for step in ticket['plan']['steps'] + ticket['plan']['artifact_steps']}
        new_ids = {step['id'] for ticket in new_tickets.values()
                   for step in ticket['plan']['steps'] + ticket['plan']['artifact_steps']}
        require(not (historical_ids - current_ids) & new_ids,
                'execution_plan_incomplete', 'retired step IDs cannot be reused')
        for ticket_id, prior in old_tickets.items():
            current = new_tickets[ticket_id]
            changed = prior["source"]["sha256"] != current["source"]["sha256"]
            require(current["plan_version"] == prior["plan_version"] + (1 if changed else 0),
                    "invalid_plan_revision", "Ticket plan version must advance exactly once: " + ticket_id)
        generated_outputs = transaction["input"].get("generated_outputs",
            old_authority.get("generated_outputs",
                old_authority.get("proposal", {}).get("generated_outputs", {})))
        stage_scope.validate_scope(scope, s["handoff"]["binding"]["repository"], stage=3,
            requirement_path=s["handoff"]["requirement_identity"]["path"],
            planning_paths=sources or None,
            candidate_commit=old_authority["candidate_commit"] if inline else data["planning_commit"],
            manifest_baseline=scope["baseline"],
            generated_outputs=generated_outputs)
        proposed = copy.deepcopy(old_authority)
        proposed.update(revision=old_authority["revision"] + 1,
                        candidate_commit=old_authority["candidate_commit"] if inline else data["planning_commit"],
                        execution_plan=execution_plan.authority_envelope(catalog,
                            old_authority["execution_plan"]["original_scope_revision"],
                            input_digest=(old_authority["execution_plan"]["sources"][0]["input_digest"]
                                          if inline else None)),
                        scope=scope, generated_outputs=copy.deepcopy(generated_outputs))
        if inline:
            require(proposed == execution_plan.inline_authority(transaction["input"]["inline_plan"],
                scope, s["handoff"]["semantic"], s["handoff"]["requirement_identity"],
                generated_outputs, repository=repository,
                original_scope_revision=old_authority["execution_plan"]["original_scope_revision"],
                input_digest=old_authority["execution_plan"]["sources"][0]["input_digest"]),
                "scope_changed", "inline authority projection changed")
        transaction.update(state="planning-committed", commit_input=copy.deepcopy(data),
                           git_facts=facts, proposed_authority=proposed)
        impact = execution_plan.affected_plan_closure(old_authority, proposed, facts['changed_paths'])
        selected = {identity for ticket in catalog['tickets'] for group in ticket['plan']['behavior_slices']
            if set(group['step_ids']) & set(impact['impacted_step_ids']) for identity in group['step_ids']}
        if selected:
            transaction['proposed_preflight'] = execution_plan.dispatch_preflight(catalog, selected)
        self.save()
        return self.activate_plan_revision()

    def activate_plan_revision(self):
        s = self.state
        transaction = s["plan_revision_transaction"]
        require(transaction["state"] == "planning-committed", "plan_revision_unavailable",
                "activation requires the verified planning commit")
        require(s["status"] not in {"paused", "pausing", "cancelled", "cancelling"},
                "plan_revision_suspended", "paused or cancelled planning commit cannot activate")
        require(s["host"]["status"] == "stopped" and not self.stop_barrier_pending() and
                not self.unresolved_host_actions() and s["transaction"] is None,
                "host_evidence_missing", "original writers and host calls must still be stopped")
        data = transaction["commit_input"]
        (control_git.verify_inline_plan_revision if transaction["old_authority"].get("kind") == "inline" else
         control_git.verify_plan_revision)(s["handoff"]["binding"]["worktree"],
                                          transaction["snapshot"], data["planning_commit"])
        old = transaction["old_authority"]
        new = transaction["proposed_authority"]
        checkpoint = s["control"]["context"]["handoff_progress"]
        repository = s["handoff"]["binding"]["worktree"]
        baseline_fingerprints = {relative: control_git.committed_fingerprint(
            repository, checkpoint["git_baseline_commit"], relative)
            for relative in new["scope"]["implementation_paths"]}
        retention = execution_plan.classify_retained_results(
            old, new, checkpoint["executions"], checkpoint.get("carried_results", []),
            checkpoint.get("revalidation_source"), transaction["snapshot"],
            baseline_fingerprints, transaction["git_facts"]["changed_paths"])
        evidence = {"revision_id": data["revision_id"],
            "old_authority": {"revision": old["revision"], "authority_digest": entry.digest(old),
                "plan_digest": old["execution_plan"]["plan_digest"],
                "contract_digest": old["execution_plan"]["contract_digest"]},
            "new_authority": {"revision": new["revision"], "authority_digest": entry.digest(new),
                "plan_digest": new["execution_plan"]["plan_digest"],
                "contract_digest": new["execution_plan"]["contract_digest"]},
            "new_execution_authority": copy.deepcopy(new),
            "planning_commit": data["planning_commit"],
            "planning_paths": transaction["input"]["planning_paths"],
            "new_scope": {"implementation_paths": new["scope"]["implementation_paths"],
                "protected_paths": s["handoff"]["scope"]["protected_paths"]},
            "snapshot": transaction["snapshot"],
            "baseline_fingerprints": baseline_fingerprints,
            "retention": retention,
            "classification": transaction["input"]["classification"],
            "git_facts": transaction["git_facts"]}
        if "validation_plan" in transaction["input"]:
            evidence["validation_plan"] = copy.deepcopy(transaction["input"]["validation_plan"])
        if checkpoint.get('review_claims'):
            evidence['review_retention'] = self.persist_plan_revision_reviews(transaction)
        result = self.control_action({"action": "plan-revision-activate", "evidence": evidence,
            "receipt": {"controller_ref": data["controller_ref"],
                        "reference": evidence["classification"]["reference"]}})
        if transaction["state"] == "planning-committed":
            transaction["state"] = "activated"
            s["scope_authority"] = copy.deepcopy(new)
            self.save()
        return _view(s, {"operation": "plan-revision-activated", "authority": s["scope_authority"],
                         "control": result["next_action"]["result"]})

    def message_key(self, data):
        return entry.digest({"request": self.state["dispatch"]["request"]["digest"],
                             "ref": data["receipt"]["ref"], "result": data["result"]})

    def revoke_host(self):
        host = self.state["host"]
        host["generation"] += 1
        host["proof"], host["query"] = None, None

    def host_observation_facts(self, data):
        """Read only the current response/call indexes before admitting a poll."""
        host, receipt = self.state['host'], data['receipt']
        provenance = data.get('provenance')
        valid = (isinstance(receipt, dict) and isinstance(provenance, dict) and
                 set(provenance) == {'call_ref', 'response_ref', 'action_id'} and
                 all(isinstance(value, str) and value.strip() for value in provenance.values()))
        facts = {'valid': valid, 'conflict': False, 'duplicate': False, 'attributed': False, 'causal': False}
        if not valid:
            return facts
        if self.state.get('recovery_predecessor') is not None:
            self.reject_design_native_replay(provenance)
        response_key = entry.digest([receipt['adapter'], provenance['response_ref']])
        call_key = entry.digest([receipt['adapter'], provenance['call_ref']])
        fingerprint = state_store.raw_measure({'provenance': provenance,
            'receipt': {key: value for key, value in receipt.items() if key != 'receipt_ref'},
            'action_resolution': data.get('action_resolution')})[1]
        store = state_store.open_store(self.path)
        previous = host['seen'].get(response_key)
        if previous is None:
            previous = store.poll_fingerprint('response', response_key)
        call_digest = (entry.digest(host['calls'][call_key]) if call_key in host['calls'] else
                       store.poll_fingerprint('call', call_key))
        conflict = ((previous is not None and previous != fingerprint) or
                    (call_digest is not None and call_digest != entry.digest(provenance['action_id'])))
        action = host.get('query') or self.state.get('host_action')
        attributed = (not conflict and action is not None and
            data.get('action_id') == provenance['action_id'] == action['action_id'] and
            action['generation'] == host['generation'])
        facts.update(response_key=response_key, call_key=call_key, fingerprint=fingerprint,
            conflict=conflict, duplicate=previous == fingerprint and not conflict,
            attributed=attributed, causal=attributed and not action.get('resolved'))
        return facts

    def record_host(self, data, *, facts=None):
        """Only trusted adapter ingress writes host facts, never B replay.

        Provenance names an actual invocation/response authenticated by the
        adapter, not a signature. Raw content is neither an identity nor a nonce.
        """
        s, receipt = self.state, data["receipt"]
        host = s["host"]
        event = s["events"][data["event_id"]]
        if event.get("host_applied"):
            require(not event.get("host_conflict"), "host_provenance_conflict",
                    "tool provenance was reused with different evidence or cause")
            return
        status = receipt["status"]
        truncated = (receipt['raw'].get('format') == 'host-raw-reference-v1' and
                     receipt['raw'].get('truncated') is True)
        provenance = data.get("provenance")
        # The fast poll path uses these same bounded attribution checks.
        facts = self.host_observation_facts(data) if facts is None else facts
        conflict, duplicate = facts['conflict'], facts['duplicate']
        if facts['valid'] and not conflict and not duplicate:
            host['seen'][facts['response_key']] = facts['fingerprint']
            host['calls'][facts['call_key']] = provenance['action_id']
        if duplicate:
            event["host_applied"] = True
            self.save()
            return
        action = host.get("query") or s.get("host_action")
        causal = facts['causal']
        if causal and action["operation"] == "inspect-host-state" and action["payload"].get("unresolved_action"):
            outstanding = action["payload"]["unresolved_action"]
            retained = next((item for item in self.unresolved_host_actions()
                             if item["action_id"] == outstanding["action_id"]), None)
            resolution = data.get("action_resolution")
            settled = (not truncated and isinstance(resolution, dict) and set(resolution) == {"action_id", "outcome"} and
                       resolution["action_id"] == outstanding["action_id"] and
                       isinstance(resolution["outcome"], str) and
                       resolution["outcome"] in {"completed", "not-issued", "cancelled"} and
                       retained == outstanding)
            if settled:
                retained["resolved"] = True
                retained["resolution"] = copy.deepcopy(resolution)
                action["settled_action_id"] = outstanding["action_id"]
            else:
                # An idle identity alone does not settle an uncertain mutation
                # which might still execute later. Retain it and wait.
                status = "unknown"
        observation = {"event_id": data["event_id"], "receipt": copy.deepcopy(receipt),
                       "action_id": data.get("action_id"), "provenance": copy.deepcopy(provenance),
                       "action_resolution": copy.deepcopy(data.get("action_resolution"))}
        if receipt["status"] == "stopped" and causal:
            checkpoint = s['control']['context'].get('handoff_progress') or {}
            if control.direct_implementation(checkpoint) or 'design_input' in checkpoint:
                try:
                    observation['git_snapshot'] = control_git.recovery_snapshot(Path(checkpoint['binding']['worktree']), checkpoint)[0]
                except control.ControlError as error:
                    observation['snapshot_error'] = str(error)
        if status in {"stopped", "unknown"} or conflict:
            was_stopped = host["status"] == "stopped"
            self.revoke_host()
            if conflict or (status == "stopped" and not causal and not was_stopped):
                status = "unknown"
        elif status == "running" and not causal:
            self.revoke_host()
        elif not causal:
            event["host_applied"] = True
            self.save()
            return
        if causal and status in {"idle", "turn-completed", "stopped"}:
            action["resolved"] = True
        host["status"] = status
        observation["generation"] = host["generation"]
        if receipt["status"] == "stopped":
            host["last_stop"] = copy.deepcopy(observation)
        # Unordered stops retain an existing barrier, never replace causal proof.
        if causal or status != "stopped":
            host["observation"] = observation
        resumable = status == 'stopped' and host_raw_facts(receipt).get('resumable') is True
        host["proof"] = copy.deepcopy(observation) if causal and (status in {"idle", "turn-completed"} or resumable) else None
        if action is not None and action["operation"] == "inspect-host-state" and (causal or status == "unknown"):
            # Retain a negative query result so advance waits rather than loops.
            if not truncated:
                action["resolved"] = True
            host["query"] = action
        event["host_applied"] = True
        event["host_conflict"] = conflict
        self.save()
        require(not conflict, "host_provenance_conflict", "tool provenance was reused with different evidence or cause")

    def unresolved_control_transactions(self):
        return {identity:value for identity,value in self.state.get('control_transactions',{}).items()
                if value.get('result') is None and value.get('rejection') is None and not
                (value.get('scope') == 'archive' and value.get('handoff_completed') is True and value.get('intent_persisted') is True)}

    def require_controls_reconciled(self, replay_identity=None):
        require(not (set(self.unresolved_control_transactions()) - {replay_identity}),
                'control_outcome_unknown', 'reconcile the exact original control transaction before another state change')

    def consumed_rework_sources(self):
        consumed = []
        decisions = self.state.get('recovery_decisions', {})
        for history in self.state.get('business_history', []):
            block, decision = history['block'], history['decision']
            if (block['code'] == 'rework_diagnosis_required' and
                    history['kind'] == 'recover-business' and decision['subject'] == block['subject'] and
                    decisions.get(decision['decision_id']) == decision):
                consumed.extend(block['evidence']['triggers'])
        return consumed

    def command_rework_event(self, request, revision):
        receipt = request.get('receipt', {})
        raw = receipt.get('raw', {})
        if not isinstance(raw, dict) or not raw.get('checks'):
            return None
        classification = raw.get('classification', 'unknown')
        if not isinstance(classification, str) or classification not in {
                'fixture-error', 'assertion-error', 'tdd-red', 'unknown', 'implementation-defect'}:
            classification = 'unknown'
        try:
            control.validate_result_source(receipt, raw['checks'], self.bound_ref(), require_stopped=False)
        except control.ControlError:
            classification = 'unknown'
        checks = raw['checks']
        if (raw.get('stopped') is not True or not isinstance(checks, list) or
                not all(isinstance(check, dict) for check in checks) or
                any(check.get('status') == 'unknown' for check in checks) or
                all(check.get('status') == 'passed' for check in checks)):
            classification = 'unknown'
        slice_id = raw.get('slice_id', request.get('evidence', {}).get('ticket_id', 'unknown'))
        if not isinstance(slice_id, str) or not slice_id or len(slice_id.encode()) > 4096:
            slice_id, classification = 'unknown', 'unknown'
        return {'kind': 'fixture-error' if classification == 'assertion-error' else classification,
            'slice_id': slice_id, 'boundary': 'checks', 'source_ref': entry.digest([self.state['stage_subject'], receipt.get('adapter'),
                receipt.get('call_ref'), raw.get('dispatcher_ref')]), 'revision': revision}

    def rework_receipt_records(self):
        store = state_store.open_store(self.path)
        current = store.open_current()
        records = {}
        for identity, saved in self.state.get('rework_receipts', {}).items():
            entry.fields(saved, {'base_revision', 'receipt'})
            membership = saved['receipt']
            request = store.read_evidence(membership['request'], run_id=current.run_id)['value']
            result = store.read_receipt(current, membership['key'], request)
            require(result is not None and membership['request_digest'] == state_store.raw_measure(request)[1] and
                    store.read_evidence(membership['result'], run_id=current.run_id)['value'] == result,
                    'invalid_checkpoint', 'diagnosis source requires original receipt commit proof')
            records[identity] = {'base_revision': saved['base_revision'], 'request': request, 'result': result}
        return records

    def remember_rework_receipt(self, transaction, membership):
        event = self.command_rework_event(transaction['request'], transaction['base_revision'])
        application = (transaction.get('result') or {}).get('applicability')
        if event is None and not application:
            return
        indexes = self.state.setdefault('rework_receipts', {})
        consumed = {source for trigger in self.consumed_rework_sources() for source in trigger['source_refs']}
        # Only diagnosed facts leave the active index; immutable original receipts remain.
        for identity, record in self.rework_receipt_records().items():
            original = self.command_rework_event(record['request'], record['base_revision'])
            proof = (record.get('result') or {}).get('applicability', {})
            sources = set(proof.get('affected', []))
            if sources:
                sources = {entry.digest([claim, proof['candidate']]) for claim in sources}
            if original and original['kind'] == 'fixture-error':
                sources.add(original['source_ref'])
            if sources and sources <= consumed:
                indexes.pop(identity)
        # The latest committed interval supersedes older intervals for this slice.
        # Admission has already refused every unconsumed pair before this result.
        identity = ('interval:' + entry.digest([event['slice_id'], event['boundary']])
            if event and event['kind'] != 'fixture-error' else membership['key'])
        if identity in indexes and indexes[identity]['base_revision'] > transaction['base_revision']:
            return
        require(identity in indexes or len(indexes) < 64, 'state_capacity',
                'diagnosis receipt window is full; retain original facts and resolve diagnosis before continuing')
        indexes[identity] = {'base_revision': transaction['base_revision'], 'receipt': membership}

    def rework_diagnostics(self):
        """Read original receipt proofs and order them by their owner revisions."""
        events = []
        retained = self.rework_receipt_records()
        records = [*retained.values(), *self.state.get('control_transactions', {}).values()]
        ordered = sorted(records, key=lambda value: value['base_revision'])
        commands = {}
        for transaction in ordered:
            event = self.command_rework_event(transaction['request'], transaction['base_revision'])
            if event is None:
                continue
            original = commands.get(event['source_ref'])
            if original is None:
                commands[event['source_ref']] = event
            elif original['kind'] == 'unknown' and event['kind'] != 'unknown':
                # A new observation may resolve this call; it cannot move its
                # original place in the command sequence or become a new call.
                event['revision'] = original['revision']
                commands[event['source_ref']] = event
        intervals = {}
        for identity, record in retained.items():
            if identity.startswith('interval:'):
                observed = self.command_rework_event(record['request'], record['base_revision'])
                event = commands[observed['source_ref']]
                if event['kind'] != 'fixture-error':
                    key = (event['slice_id'], event['boundary'])
                    intervals[key] = max(intervals.get(key, -1), event['revision'])
        events.extend(event for event in commands.values() if event['kind'] != 'fixture-error' or
            event['revision'] > intervals.get((event['slice_id'], event['boundary']), -1))
        for transaction in ordered:
            proof = (transaction.get('result') or {}).get('applicability', {})
            if proof.get('affected'):
                store = state_store.open_store(self.path)
                value = store.read_evidence(proof['reference'], run_id=store.open_current().run_id)['value']
                for axis, application in value['applications'].items():
                    if not application['applicable']:
                        events.append({'kind': 'accepted-slice-invalidated', 'slice_id': axis,
                            'boundary': axis, 'source_ref': entry.digest([application['claim_id'], proof['candidate']])})
        observed_axes = set()
        for group in [*self.state.get('review_history', []), self.state.get('review_activity', {})]:
            for axis, slot in group.items():
                report = next((receipt['raw'].get('review') for receipt in reversed(slot['receipts'])
                    if receipt['status'] == 'stopped' and receipt['raw'].get('review')), None)
                defect = (slot.get('cost', {}).get('classification') == 'review-defect' or
                    isinstance(report, dict) and isinstance(report.get('findings'), list) and bool(report['findings']))
                if slot['stopped'] and defect and axis in observed_axes:
                    events.append({'kind': 'review-repair-defect', 'slice_id': axis, 'boundary': axis,
                        'source_ref': slot['action_id']})
                observed_axes.add(axis)
        return execution_plan.rework_diagnosis(events, consumed=self.consumed_rework_sources())

    def require_rework_diagnosis(self):
        triggers = self.rework_diagnostics()
        if not triggers:
            return
        evidence = {'triggers': triggers}
        error = entry.PreparationError('rework_diagnosis_required',
            'original Controller must diagnose the confirmed rework sources before continuing')
        self.block(error)
        if self.state.get('business_block') is None:
            self.set_business_block(error.code, evidence)
            self.save()
        raise error

    def ticket_checks_due(self):
        checkpoint = self.state['control']['context'].get('handoff_progress') or {}
        if checkpoint.get('state') != 'implementing':
            return []
        catalog = execution_plan.current_catalog(self.state['scope_authority'])
        completed = execution_plan.current_accepted_steps(catalog, control.current_accepted_results(checkpoint),
            self.state['scope_authority'])
        due = []
        for ticket in catalog['tickets']:
            accepted = [group for group in ticket['plan']['behavior_slices'] if set(group['step_ids']) <= completed]
            if len(accepted) < 2 and not any(group['shared_state'] for group in accepted):
                continue
            checked = checkpoint.get('ticket_checks', {}).get(ticket['ticket_id'])
            selected = execution_plan.ticket_check_paths(catalog, ticket['ticket_id'],
                checkpoint['allowed_paths'], checkpoint['protected_paths'], self.state['scope_authority'])
            if not checked or checked['plan_authority'] != checkpoint['plan_authority'] or checked['source_snapshot'] != {
                    path: control_git.file_fingerprint(Path(checkpoint['binding']['worktree']), path) for path in selected}:
                due.append(ticket['ticket_id'])
        return due

    def require_business_ready(self, *, control_replay=False):
        self.require_rework_diagnosis()
        if not control_replay:
            self.require_controls_reconciled()
        require(not any(not slot['start']['applied'] and not slot.get('business_settled')
                        for slot in self.state.get('review_activity', {}).values()),
                'control_outcome_unknown', 'look up the original pending review start before new business work')
        require(not self.state.get('prior_carrier_activity'), 'host_recovery_required', 'reconcile activity through the original prior-stage carrier')
        require(self.state.get('transport_loss') is None, 'host_recovery_required', 'original host identity and calls require current recovery evidence')
        require(self.state.get("business_block") is None, "business_recovery_required",
                "original Controller must resolve the exact business block first")

    def set_business_block(self, code, evidence):
        s = self.state
        require(s.get("business_block") is None, "business_recovery_required", "retain the original business failure")
        identity = entry.digest({"subject": self.subject(), "code": code, "evidence": evidence})
        subject = {**self.subject(), "block_id": identity, "ref": self.bound_ref(),
                   "controller_ref": s["dispatch"]["request"]["controller_ref"]}
        s["business_block"] = {"id": identity, "code": code, "subject": subject,
                               "evidence": copy.deepcopy(evidence), "error": copy.deepcopy(s["error"])}
        s.pop("continuation_intent", None)
        s.pop("resume_intent", None)

    def recover_business(self, data):
        """Record the original Controller's decision; never invoke a host here."""
        entry.fields(data, {"decision_id", "subject", "reference", "diagnosis", "instruction", "expected_progress"})
        for key in ("decision_id", "reference", "diagnosis", "instruction", "expected_progress"):
            entry.nonempty(data[key])
        s = self.state
        previous = s.get("recovery_decisions", {}).get(data["decision_id"])
        if previous is not None:
            require(previous == data, "decision_conflict", "recovery decision cannot be replaced")
            return _view(s, acknowledged=True)
        block = s.get("business_block")
        require(block is not None and data["subject"] == block["subject"],
                "stale_decision", "recovery must name the exact active business block")
        require(s["transaction"] is None and
                not self.unresolved_control_transactions() and
                not any(item.get("transaction") is not None for item in s.get("allocations", {}).values()),
                "transaction_pending", "consume original transactions before a recovery decision")
        authority = {"record": s["dispatch"], "control": copy.deepcopy(s["control"])}
        dispatch.record_for(authority)
        require(authority["control"]["context"] == s["control"]["context"] and
                block["subject"]["controller_ref"] == s["control"]["context"]["controller_ref"],
                "authority_changed", "original Controller authority changed")
        ref, state, _ = dispatch.matching(s["dispatch"], authority["control"]["context"])
        require(ref == block["subject"]["ref"] == self.bound_ref() and
                {key: block["subject"][key] for key in self.subject()} == self.subject() and
                state not in {"cancelled", "creation-failed", "completed"},
                "authority_changed", "business recovery must retain the original active identity and scope")
        require(self.stop_intent() != "cancelling", "attempt_ended", "business recovery cannot undo cancellation")
        deferred = s.get("deferred_business_recovery")
        require(deferred is None or deferred == data, "decision_conflict", "retain the paused recovery decision")
        if self.stop_intent() == "pausing":
            s["deferred_business_recovery"] = copy.deepcopy(data)
            self.save()
            return _view(s, acknowledged=deferred == data)
        s.setdefault("recovery_decisions", {})[data["decision_id"]] = copy.deepcopy(data)
        self.clear_business_block(data, "recover-business")
        s.pop("deferred_business_recovery", None)
        self.revoke_host()
        # Once publication exists, only original finalization is permissible.
        if s.get("publication"):
            s["step"] = "publication-complete" if s["publication"].get("result") else "publication-ready"
        elif s.get("publication_candidate"):
            s["step"] = "publication-ready" if s["publication_candidate"].get("decision") else "bound"
        elif s.get("pending"):
            s["step"] = "received" if s["pending"]["kind"] == "acceptance" else "decision"
        else:
            s["step"] = "continue"
            s["continuation_intent"] = {"kind": "recovery", "decision": copy.deepcopy(data)}
        s["status"] = "needs_input" if s.get("pending") else "active"
        self.save()
        return _view(s)

    def clear_business_block(self, decision, kind):
        # Called only after the corresponding Controller recovery was verified.
        s = self.state
        s.setdefault("business_history", []).append({"block": copy.deepcopy(s["business_block"]),
            "decision": copy.deepcopy(decision), "kind": kind})
        s["business_block"] = None
        s.pop("error", None)
        s["continue_repeats"] = 0
        s.pop("continue_fingerprint", None)

    def implementation_recovery(self, data):
        """Reopen only the accepted implementation named by an unpublished closure."""
        entry.fields(data, {'reference', 'closure_digest'})
        entry.nonempty(data['reference'])
        s = self.state
        prior_recovery = s.get('final_integration_recovery')
        if s['stage'] == 3 and prior_recovery is not None:
            require(prior_recovery['reference'] == data['reference'] and prior_recovery['closure_handoff']['source_digest'] == data['closure_digest'], 'recovery_conflict', 'retain the original recovery decision')
            return _view(s, acknowledged=True)
        self.require_not_stopping()
        require(s['stage'] == 4 and s['handoff']['digest'] == data['closure_digest'] and (s.get('recovery_action') or {}).get('operation') == 'implementation-recovery' and
                s['control']['context']['handoff_progress']['state'] == 'implementation-required',
                'recovery_missing', 'only a final unpublished closure problem can reopen implementation')
        self.require_controls_reconciled()
        require(s['transaction'] is None and not self.unresolved_host_actions() and
                s.get('stopped') and not self.stop_barrier_pending(),
                'host_action_pending', 'stop and reconcile all closure writers before implementation recovery')
        if self.outer.get('confirmed', {}).get('stages'):
            require(self.outer.get('current_stage') == 'stage4' and
                    self.outer.get('launch', {}).get('state') == 'completed_turn',
                    'carrier_active', 'finish or reconcile the original closure carrier turn before rerouting the runner')
        handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
        handoff.phase_evidence(s['handoff'])
        for pin in s['packages'].values():
            skill_preflight.verify_identity(pin)
        publication = None
        if s.get('publication'):
            publication = supervision.reconcile_publication(self.publication_transaction(), for_implementation_recovery=True)
            require(publication['state'] in {'not-published', 'prepared'}, 'already_published',
                    'published or uncertain effects permit publication reconciliation only')
        predecessor = s['handoff']['predecessor']
        facts, current, store = read_implementation_delivery(self.path, predecessor)
        _, accepted_progress = implementation_acceptance(predecessor)
        recovery = store.read_evidence(facts['recovery_ref'], run_id=current.run_id)['value']
        entry.fields(recovery, {'launch', 'control_identity', 'dispatch_configuration',
                                'authority_digest', 'implementation_policy', 'runner'})
        require(s['control']['discussion'] is None, 'authority_missing',
                'ledger recovery requires its original authority adapter')
        require(not any(record['ref'] == facts['dispatcher']['ref'] for record in s.get('native_finalizations', [])),
                'identity_retired', 'implementation recovery requires the original reusable native identity')
        require(facts['handoff']['binding'] == s['handoff']['binding'] and facts['stopped'] and
                facts['dispatcher']['ref'] == s['recovery_action']['ref'] and
                facts['execution_authority'] == s['scope_authority'],
                'binding_changed', 'recovery must retain original stopped implementation and authority')
        worktree = Path(facts['handoff']['binding']['worktree'])
        control_git.verify_binding(worktree, facts['handoff']['binding'])
        accepted = facts['payload']['candidate_commit']
        retained_commit = control_git.snapshot(worktree)['head']
        handoff.ancestor(worktree, accepted, retained_commit)
        previous_paths = (accepted_progress.get('integration_retained') or {}).get('paths', [])
        retained_paths = sorted(set(previous_paths) |
            set(supervision._changed_paths(worktree, accepted, retained_commit)))
        require(not supervision._full_status(worktree) and
                (set(retained_paths) | control_git.incoming_changed_paths(worktree, accepted, retained_commit)) <= set(s['handoff']['scope']['closure_paths']),
                'candidate_changed', 'recovery requires the clean accepted candidate plus committed closure documents')
        target = supervision._branch_oid(Path(s['handoff']['binding']['repository']), s['handoff']['binding']['target_branch'])
        baseline = facts['handoff']['scope']['baseline']
        handoff.ancestor(worktree, baseline, target)
        for relative in facts['handoff']['scope']['protected_paths']:
            require(control_git.committed_fingerprint(worktree, target, relative) ==
                    control_git.committed_fingerprint(worktree, baseline, relative),
                    'source_changed', 'final target changed protected source: ' + relative)
        # Phase/closure authority is a bounded fact set, not a recursive Stage-4 input.
        closure = handoff.seal({key: copy.deepcopy(s['handoff'][key]) for key in
            ('stage', 'entry', 'expected_entry', 'controller_ref', 'binding', 'scope',
             'requirement_identity', 'authorization')} | {
                'kind': 'recovery-closure-facts', 'candidate': accepted,
                'source_digest': s['handoff']['digest'], 'role_ref': self.bound_ref(),
                'publication_ref': freeze_recovery_publication(s, accepted, publication, store, current),
                'native_source_index_ref': freeze_native_source_index(s, store, current),
                'stop_ref': store.write_object(current.run_id, 'evidence', {
                    'host': s['host']['last_stop'], 'lifecycle': s['lifecycle_snapshot']})})
        body = copy.deepcopy(recovery['launch'])
        body['authorization']['integration_recovery'] = {'closure': closure,
            'retained_commit': retained_commit, 'retained_paths': retained_paths}
        saved = handoff.seal(body)
        require(all(saved[key] == facts['handoff'][key] for key in
                    ('controller_ref', 'requirement_identity', 'binding', 'scope', 'source_commit')),
                'authority_changed', 'recovery launch differs from accepted implementation')
        checkpoint = {key: copy.deepcopy(accepted_progress[key]) for key in
            ('binding', 'git_baseline_commit', 'execution_authority', 'allowed_paths',
             'protected_paths', 'validation_plan', 'plan_digest', 'plan_authority')}
        checkpoint.update(state='implementing', candidate=None, tests=[], executions=[],
            configuration=recovery['dispatch_configuration'], authority_digest=recovery['authority_digest'],
            implementation_policy=recovery['implementation_policy'],
            integration_target_head=target, integration_candidate=retained_commit,
            integration_retained={'commit': retained_commit, 'paths': retained_paths},
            validation_history=[{'candidate': accepted, 'accepted_paths': previous_paths,
                                 'proof_ref': predecessor}],
            plan_revisions=accepted_progress.get('plan_revisions', []),
            plan_revision_sources=accepted_progress.get('plan_revision_sources', []))
        context = {**recovery['control_identity'], 'stage': 3,
            'carrier': {'kind': 'implementation-dispatcher', **facts['dispatcher']},
            'handoff_progress': checkpoint,
            'retired_handoffs': copy.deepcopy(s['control']['context'].get('retired_handoffs', {}))}
        launch = handoff.seal({'kind': 'native', 'role': saved['role'],
            'controller_ref': saved['controller_ref'], 'attempt': facts['dispatch_attempt'],
            'input_digest': saved['digest'], 'configuration': saved['selection']})
        record = handoff.seal({'protocol': handoff.PROTOCOL, 'kind': 'dispatch',
            'stage_input': {'checkpoint': str(self.path), 'run_id': current.run_id,
                'reference': store.write_object(current.run_id, 'evidence', {'stage_input': saved})},
            'scope_checkpoint_path': str(self.path), 'request': launch,
            'status': 'bound', 'delivery': None, 'acceptance': None, 'receipts': []})
        same_tree = (s['native_host']['adapter'] == 'collaboration' or
            saved['expected_entry']['actor']['thread_id'] == s['handoff']['expected_entry']['actor']['thread_id'])
        recovered_refs = sorted(set(facts['native_refs']) | (self.native_refs(s) if same_tree else set()))
        require(len(recovered_refs) <= 32, 'state_capacity',
                'recovery writer inventory exceeds its fixed active identity window')
        restored = {key: copy.deepcopy(s[key]) for key in
            ('protocol', 'revision', 'mode', 'packages', 'checkpoint_path', 'native_host',
             'workspace_evidence', 'native_finalizations')}
        restored.update(stage=3, stage_subject='stage:3:' + str(uuid.uuid4()), next_stage=4,
            handoff=saved, dispatch=record, control={'context': context, 'discussion': None},
            scope_authority=facts['execution_authority'], consumed_readiness=saved.get('readiness'),
            design_predecessor=facts['design_predecessor'], implementation_predecessor=None,
            recovery_predecessor=predecessor,
            final_integration_recovery={'reference': data['reference'], 'closure_handoff': closure},
            history=[], allocations={}, accepted=None, pending=None, status='active', step='continue',
            transaction=None, transaction_source=None, transaction_result=None, events={}, observations=[],
            control_transactions={identity: copy.deepcopy(transaction)
                for identity, transaction in s.get('control_transactions', {}).items()
                if transaction.get('scope') == 'archive' and transaction.get('intent_persisted') is True
                and transaction.get('reserve_action') and transaction.get('operation_id')},
            retained_host_actions=[], phase_complete=facts['phase_complete'],
            stopped=False, continuation_intent={'kind': 'final-integration', 'reference': data['reference']},
            review_activity={}, action=None, host_action=None,
            recovered_native_refs=recovered_refs,
            host={'generation': s['host']['generation'] + 1, 'status': 'unknown', 'proof': None,
                  'query': None, 'seen': {}, 'calls': {}, 'last_stop': None})
        self.state = restored
        if self.outer.get('confirmed', {}).get('stages'):
            runner_pin = recovery['runner']
            require(isinstance(runner_pin, dict) and
                    self.outer['sessions'].get('stage3') == runner_pin['session'],
                    'carrier_active', 'retain the original implementation carrier session')
            for key in ('stage3', 'stage4'):
                self.outer.get('stage_results', {}).pop(key, None)
            self.outer.update(current_stage='stage3', status='active', pending_input=None,
                launch={'stage': 'stage3', 'state': 'prelaunch', 'turn': runner_pin['turn']})
            self.outer.pop('turn_result', None)
        self.save()
        # The caller may still be the Stage-4 carrier. Only a read-only query is
        # issued here; continuation rechecks the original Stage-3 entry in its carrier.
        return self.query_host()

    def continuation_gate(self):
        self.require_business_ready()
        s, host = self.state, self.state["host"]
        self.require_not_stopping()
        require(s["step"] == "continue" and s["status"] == "active" and s["transaction"] is None,
                "cannot_continue", "continuation requires consumed business intent")
        require(s.get("continuation_intent") is not None or s.get("resume_intent") is not None,
                "cannot_continue", "a host receipt alone cannot authorize business continuation")
        if (s.get('resume_intent') or {}).get('operation') == 'resume-original-paused-scope':
            self.verify_direct_resume()
        require(self.bound_ref() is not None, "identity_mismatch", "retain the original bound identity")
        authority = {"record": s["dispatch"], "control": copy.deepcopy(s["control"])}
        dispatch.record_for(authority)
        require(authority["control"]["context"] == s["control"]["context"] and
                s["handoff"] == dispatch.dispatch_input(s["dispatch"]),
                "authority_changed", "continuation requires the original current control and scope")
        ref, state, _ = dispatch.matching(s["dispatch"], authority["control"]["context"])
        require(ref == self.bound_ref() and state not in {"cancelled", "creation-failed", "completed"},
                "authority_changed", "original control must still authorize this identity")
        current = handoff.refresh(s["handoff"]["entry"], s["handoff"]["expected_entry"], after_work=True)
        if s["stage"] >= 2:
            handoff.source(current, s["handoff"]["requirement"], s["stage"])
        handoff.phase_evidence(s["handoff"], role_ref=self.bound_ref(), control_context=s["control"]["context"])
        outstanding = s.get("host_action")
        if (outstanding is not None and outstanding["operation"] == "continue-host" and
                not outstanding.get("resolved")):
            if outstanding["generation"] == host["generation"]:
                return _view(s, {"operation": "lookup-exact-action", "action": outstanding})
        if self.unresolved_host_actions():
            return self.query_host()
        proof = host["proof"]
        if proof is not None and proof["generation"] == host["generation"] and proof["receipt"]["ref"] == self.bound_ref():
            if (s.get("continuation_intent") or {}).get("kind") == "scope-revision":
                require(entry.digest(host["last_stop"]) == s["consumed_readiness"]["stop_evidence_ref"] and
                        host_raw_facts(proof['receipt']).get("resumable") is True,
                        "host_recovery_required", "new revision requires the original resumable stop proof")
            else:
                dispatch.receipt_for(s["dispatch"], proof["receipt"], {"result"})
            return None
        return self.query_host()

    def unresolved_host_actions(self):
        # Older replaced invocations retain their identities until exact query
        # settlement. Return the actual journal objects for atomic resolution.
        actions = [item for item in self.state.get("retained_host_actions", []) if not item.get("resolved") and not item.get('creation_resolved')]
        current = self.state.get("host_action")
        if current is not None and not current.get("resolved") and not current.get('creation_resolved'):
            actions.append(current)
        return actions

    def query_host(self, *, retry=False):
        s, host = self.state, self.state["host"]
        outstanding = self.unresolved_host_actions()
        previous = host['query']
        # One authenticated response settles one action, not the entire chain.
        # Construct a replacement locally: failed admission must leave the
        # last durable query intact, never publish an unreserved invocation.
        if previous is None or retry or previous.get('settled_action_id') and outstanding:
            query = {"action_id": str(uuid.uuid4()), "operation": "inspect-host-state",
                "generation": host["generation"], "payload": {"ref": self.bound_ref(), "subject": self.subject(),
                    "request": copy.deepcopy(s["dispatch"]["request"])}, "checkpoint": str(self.path)}
            if outstanding:
                query['payload']['unresolved_action'] = copy.deepcopy(outstanding[0])
            state_store.open_store(self.path).reserve(query['action_id'], recovery=True)
            if retry and previous is not None:
                host.setdefault('query_history', []).append(copy.deepcopy(previous))
            host['query'] = query
            self._receipt_action = query['action_id']
            self.save()
        query = host["query"]
        if not query.get('resolved'):
            state_store.open_store(self.path).require_reserve(query['action_id'], recovery=True)
        return _view(s, {"operation": "await-host-recovery", **self.recovery_requirements(), "query": query}
                     if query.get("resolved") else query)

    def next_envelope(self, receipt):
        if receipt is not None and self.state["control"]["discussion"] is not None:
            # New logical mutations get a new key; a saved transaction keeps its
            # original envelope until its idempotent result has been recovered.
            self.state["control"]["discussion"] = {**self.state["control"]["discussion"],
                "expected_ledger_revision": receipt["ledger_revision"],
                "expected_topic_revision": receipt["record_revision"], "idempotency_key": str(uuid.uuid4())}
            reference = receipt.get('control', {}).get('store_ref')
            require(reference is not None, 'authority_missing', 'ledger result must name its original current projection')
            self.state['control']['store_ref'] = reference

    def advance(self):
        s = self.state
        recovery = (s.get('control', {}).get('context', {}).get('handoff_progress') or {}).get('recovery')
        if recovery and recovery['state'] != 'activated' and self.stop_intent() is None and not s.get('transport_loss') and not self.unresolved_control_transactions() and s['transaction'] is None:
            return self.recovery_view()
        if s.get('stopped') and self.stop_barrier_pending():
            self.save()
            return _view(s, {'operation':'await-host-recovery','pending':self.stop_barrier_pending()})
        if s.get('transport_loss') is not None:
            return _view(s, s['recovery_action'])
        intent = self.stop_intent()
        if intent is not None:
            return self.advance_stop(intent)
        if (s.get("plan_revision_transaction") or {}).get("state") not in (None, "activated"):
            return self.advance_plan_revision()
        if native_finalization_action(s) is not None:
            return _view(s, native_finalization_action(s))
        if (s.get("scope_revision_transaction") or {}).get("state") not in (None,):
            return self.scope_revise(s["scope_revision_transaction"]["input"])
        for identity, slot in s.get('allocations', {}).items():
            if slot.get('raw_ingress') and not slot['raw_ingress']['resolved'] and slot.get('transaction') is None:
                return self.allocation_raw_lookup(identity, slot)
        if s.get('deferred_validation') is not None:
            if self.unresolved_host_actions() or self.stop_barrier_pending():
                return self.query_host()
            return self.resume_control(s['deferred_validation'])
        self.require_controls_reconciled()
        if s["transaction"] is not None:
            self.replay_transaction()
        recovery = (s['control']['context'].get('handoff_progress') or {}).get('recovery')
        if recovery and recovery['state'] != 'activated':
            return self.recovery_view()
        if s.get("business_block"):
            return _view(s)
        if s["status"] == "accepted" and not s.get("phase_complete", True):
            return self.phase_action(completing=True)
        if s["status"] in {"blocked", "paused", "cancelled", "needs_input", "accepted"}:
            return _view(s, verified_readiness=self.current_verified_readiness())
        if s["step"] == "prepare-dispatch":
            if s["dispatch"] is None:
                result = self.call("prepare")
                if "record" not in result:
                    return self.block(entry.PreparationError("outcome_unknown", "recover the original dispatch request from its checkpoint owner"))
            s["step"] = "launch"
            self.save()
        if s["step"] == "launch":
            self.verify_launch()
            if 'stage_input' in s['dispatch']:
                launch = dispatch.stage_launch(s['dispatch'])
                body = handoff.unseal(s['dispatch'])
                body['request'] = {key: value for key, value in launch.items() if key != 'payload'}
                body['request']['payload_digest'] = entry.digest(launch['payload'])
                s['dispatch'] = handoff.seal(body)
                result = self.effect('invoke-host', body['request'])
                result['next_action']['payload'] = launch
                return result
            return self.effect("invoke-host", s["dispatch"]["request"])
        if s["step"] == "host-response":
            return _view(s, {"operation": "lookup-exact-action", "action": s["action"],
                             "request": s["dispatch"]["request"], "receipts": s["observations"]})
        if s["status"] == "cancelling" and s.get("stop_effects"):
            return _view(s, {"operation": "reconcile-stopped-writers", "effects": s["stop_effects"]})
        if s["step"] == "bound":
            if s.get("stop_requested") in {"pausing", "cancelling"}:
                return self.effect("stop-host", {"subject": self.subject(), "ref": self.bound_ref()})
            if s["handoff"]["authorization"].get("phase") and s["stage"] == 2:
                action = self.phase_action()
                if action is not None:
                    return action
            return _view(s, {"operation": "wait-host", "ref": self.bound_ref(), "request": s["dispatch"]["request"]})
        implementation = s['control']['context'].get('handoff_progress') or {}
        if s['stage'] == 3 and implementation.get('state') in {'reviewable','reviewing','final-validation-pending','final-validation-failed'}:
            return _view(s, {'operation':{'reviewable':'review-candidate','reviewing':'await-review-convergence',
                          'final-validation-pending':'await-validation-start','final-validation-failed':'await-validation-recovery'}[implementation['state']],
                          'candidate':implementation['candidate']})
        if s["step"] == "continue":
            if s["handoff"]["authorization"].get("phase") and s["stage"] == 2:
                action = self.phase_action()
                if action is not None:
                    return action
            return self.effect("continue-host", {"ref": self.bound_ref(), "subject": self.subject(),
                "result": s.get("last_observation"), "decision": s.get("last_decision"),
                "intent": s.get("resume_intent") or s.get("continuation_intent"),
                **({"request": copy.deepcopy(s["dispatch"]["request"])} if
                   (s.get("continuation_intent") or {}).get("kind") in {"scope-revision", "plan-revision", "final-integration"} else {}),
                **({"validation_attempt":s["control"]["context"]["handoff_progress"]["attempts"][-1]} if
                   (s["control"]["context"].get("handoff_progress") or {}).get("state") == "validating" else {})})
        if s["step"] == "received":
            subject = {**self.subject(), "delivery_digest": s["dispatch"]["delivery"]["digest"]}
            if s["pending"] is None:
                s["pending"] = pending_decision("acceptance", subject, "Controller acceptance of the verified candidate")
            else:
                require(s["pending"]["kind"] == "acceptance" and s["pending"]["subject"] == subject,
                        "decision_conflict", "retain the original pending acceptance")
            s["status"] = "needs_input"
            self.save()
        return _view(s)

    def phase_action(self, completing=False):
        s, saved = self.state, self.state["handoff"]
        phase = saved["authorization"].get("phase")
        if phase is None:
            selected = control.selected_control(s["control"]["context"], {"attempt": s["dispatch"]["request"]["attempt"]})
            authority = selected["handoff_progress"]["plan"]["entry_authority"]
            phase = {"run_id": authority["run_id"], "attempt_id": authority["attempt_id"]}
        root = entry.discussion_root(saved["expected_entry"])
        attachment = saved["entry"]["source"]["attachment"]
        base = {"protocol_version": 1, "project_path": root, **attachment}
        run = entry.discussion_protocol.handle({**base, "operation": "read-phase-run", "phase_run_id": phase["run_id"]})["phase_run"]
        attempts = [a for a in run["attempts"] if a["attempt_id"] == phase["attempt_id"]]
        require(len(attempts) == 1, "identity_mismatch", "original phase attempt is missing")
        attempt = attempts[0]
        require(attempt["state"] not in {"failed", "cancelled", "superseded", "outcome-unknown", "blocked"},
                "phase_requires_reconciliation", "original phase attempt is terminal or uncertain; do not wait or replace blindly")
        require(completing or attempt["state"] in {"setup-pending", "ready", "active"},
                "phase_requires_reconciliation", "completion-claimed phase cannot resume substantive work")
        if completing and run["state"] == "completed" and attempt["state"] == "completed":
            s["phase_complete"] = True
            s["pending"] = None
            self.save()
            if s["stage"] != 2 or scope_readiness(s, self.path):
                s["pending"] = stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"])
                self.save()
            return _view(s)
        operation = ({"active": "claim-phase-completion", "completion-claimed": "complete-phase-run", "completion-pending": "finalize-phase-run"}.get(attempt["state"])
                     if completing else "phase-activate" if attempt["state"] == "ready" else None)
        if operation is None:
            if not completing and attempt["state"] == "active":
                return None
            return _view(s, {"operation": "wait-phase-completion" if completing else "wait-phase-ready",
                             "phase": phase, "ref": attempt.get("carrier_ref"), "state": attempt["state"]})
        if (s.get("action") or {}).get("operation") in {"source-lifecycle", "carrier-lifecycle"} and not s["action"].get("receipt"):
            return _view(s, {"operation": "reconcile-source-lifecycle", "action": s["action"]})
        topic = entry.topic_read(root, attachment)
        request = {**base, "operation": operation, "expected_ledger_revision": topic["ledger_revision"],
                   "expected_topic_revision": topic["record_revision"], "idempotency_key": str(uuid.uuid4()),
                   "phase_run_id": phase["run_id"], "attempt_id": phase["attempt_id"],
                   "evidence": attempt.get("output_evidence", attempt.get("working_evidence", run["evidence"])) if completing else run["evidence"]}
        actor = attachment["actor_conversation_ref"]
        if operation == "claim-phase-completion":
            actor = attempt["carrier_ref"]
            request.update(actor_conversation_ref=actor, carrier_ref=actor)
        # Only the authenticated source executes this envelope. A CLI/native
        # carrier must return it to that source, never impersonate its identity.
        return self.effect("carrier-lifecycle" if operation == "claim-phase-completion" else "source-lifecycle", {"actor_ref": actor, "request": request,
                                                "return_step": s["step"]})

    def verify_launch(self):
        s, saved = self.state, self.state["handoff"]
        if saved["stage"] >= 2 or saved["entry"]["source"]["kind"] != "discussion":
            handoff.verify(saved, checkpoint_path=str(self.path.resolve()) if saved['stage'] == 4 else None)
            return
        # reserve-launch legitimately changed the original ledger control view.
        # Bind owner/settings/pins with B's post-mutation verifier, then require
        # unchanged source/Git facts and *exact* readback of our saved reservation.
        current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
        identity, revision = handoff.source(current, saved["requirement"], saved["stage"])
        require(identity == saved["requirement_identity"] and revision == saved["source_commit"],
                "source_changed", "source changed after the saved reservation")
        require(current["repository"] == saved["expected_entry"]["repository"], "entry_changed", "repository changed after reservation")
        handoff.phase_evidence(saved)
        topic = entry.topic_read(entry.discussion_root(current), saved["entry"]["source"]["attachment"])
        require(topic["workflow_control"] == s["control"]["context"], "authority_changed", "reservation is no longer current")

    def bound_ref(self):
        carrier = control.selected_control(self.state["control"]["context"],
                    {"attempt": self.state["dispatch"]["request"]["attempt"]})["carrier"]
        if self.state["handoff"]["role"] == "execution-agent":
            ref, _, _ = dispatch.matching(self.state["dispatch"], self.state["control"]["context"])
            return ref
        return carrier["ref"]

    def host_receipt_projection(self, data, reference, manifest, measurement, digest):
        size, raw_digest = measurement
        truncated = size > state_store.EVIDENCE_LIMIT or not manifest['complete']
        return {**data, 'receipt': {**data['receipt'],
            'status': 'unknown' if truncated else data['receipt']['status'], 'raw': {
                'format': 'host-raw-reference-v1', 'evidence_ref': reference,
                'sha256': raw_digest, 'size': size, 'event_digest': digest,
                'reported_status': data['receipt']['status'], 'truncated': truncated,
                'facts': {} if truncated else manifest['native_receipt']['facts']}}}

    def apply_host_receipt(self, data, facts, receipt_error=None, *, interrupted=False):
        """Persist only local host facts; no control/ledger transition or new call."""
        s, receipt = self.state, data['receipt']
        event = s['events'][data['event_id']]
        if interrupted:
            event['raw_ingress'].update(evidence_ref=receipt['raw']['evidence_ref'], reason='interrupted')
        prior = next((index for index, value in enumerate(s['observations'])
                      if value.get('event_id') == data['event_id']), None)
        if (prior is None or
                (s['observations'][prior].get('receipt', {}).get('raw', {}).get('truncated') is True and
                 s['observations'][prior]['receipt']['raw'].get('evidence_ref') != receipt['raw']['evidence_ref'])):
            s['observations'].append(data)
        else:
            s['observations'][prior] = data
        self.save()
        if interrupted:
            return
        if receipt_error is not None:
            raise receipt_error
        self.record_host(data, facts=facts)
        if s['host']['status'] == 'stopped':
            s.get('native_adverse', {}).pop(receipt['ref'], None)
        if 'lifecycle' in data:
            self.lifecycle_state(data['lifecycle'], advance=False)
        # The pending ingress remains a stop barrier through every preceding
        # durable save. Only this final local application commits its release.
        event.pop('raw_ingress', None)
        s['stopped'] = self.host_stopped()
        if not s.get('business_block'):
            require(s['status'] != 'cancelled', 'attempt_ended', 'cancelled attempt cannot accept late results')
            if self.stop_intent() is not None:
                if s['stopped'] and self.stop_intent() == 'pausing':
                    s['status'] = 'paused'
                s['step'] = 'bound'
            elif (receipt['status'] == 'stopped' and s.get('final_integration_recovery') is not None and
                  (s.get('host_action') or {}).get('operation') == 'continue-host' and
                  ((s.get('host_action') or {}).get('payload', {}).get('intent') or {}).get('kind') == 'final-integration'):
                s['step'] = 'bound'
                s.pop('continuation_intent', None)
            elif (s['step'] in {'technical-error', 'continue', 'accepted'} or s.get('pending') is not None or
                  s.get('publication') or s.get('publication_candidate')):
                pass
            elif receipt['status'] in {'idle', 'turn-completed', 'stopped', 'unknown'}:
                s['status'] = 'blocked'
                s['error'] = {'code': 'business_result_missing', 'downstream_ready': False,
                    'message': 'Host state alone does not establish business completion or permission to resume'}
            else:
                s['step'] = 'bound'
        event['applied'] = True
        if (receipt['status'] == 'running' and s['host']['status'] == 'running' and
                receipt['raw']['poll_identity'] is not None):
            s['current_poll'] = {'identity': data['receipt']['raw']['poll_identity'],
                                 'generation': s['host']['generation']}
        self.save()

    def host_receipt_commit_bytes(self, data, store, current, measurement, binding, digest, facts, receipt_error, poll_identity):
        """Measure the actual finite local save sequence, never execute downstream work."""
        size, raw_digest = measurement
        chunks = [{'run_id': current.run_id, 'kind': 'raw-chunk', 'sha256': '0' * 64,
                   'size': min(1024 * 1024, size - offset)}
                  for offset in range(0, size, 1024 * 1024)]
        measured = []
        for reason in (None, 'state_capacity', 'interrupted'):
            manifest = {'format': 'raw-evidence-v1', 'complete': reason is None, 'size': size,
                        'retained_size': size, 'sha256': raw_digest, 'chunks': chunks,
                        'reason': reason, 'native_receipt': binding}
            with store.measure_writes(sequence=True) as writes:
                reference = store.write_object(current.run_id, 'evidence', manifest)
                retained = self.host_receipt_projection(data, reference, manifest, measurement, digest)
                retained['receipt']['raw']['poll_identity'] = poll_identity
                candidate = Progress(self.path, copy.deepcopy(self.outer))
                candidate._authority_revision = self._authority_revision
                candidate._receipt_action = self._receipt_action
                try:
                    candidate.apply_host_receipt(retained, facts, receipt_error, interrupted=reason == 'interrupted')
                except entry.ERROR_TYPES + (control.ControlError,) as error:
                    candidate.block(error)
                measured.append(sum(writes.values()))
        return max(measured)

    def retain_host_receipt(self, data, digest, facts, receipt_error, poll_identity):
        raw = data['receipt']['raw']
        measurement = state_store.raw_measure(raw)
        if measurement[0] <= 64 * 1024:
            return data
        event = self.state['events'].setdefault(data['event_id'], {'digest': digest, 'applied': False})
        if self._receipt_action is not None:
            event['receipt_action'] = self._receipt_action
        admitted = (receipt_error is None and facts is not None and facts['causal'] or
                    (event.get('raw_ingress') or {}).get('admitted') is True)
        event['raw_ingress'] = {'action_id': data.get('action_id'), 'provenance': data.get('provenance'),
                                'sha256': measurement[1], 'size': measurement[0], 'admitted': admitted}
        self.save()
        store = state_store.open_store(self.path, receipt_action=self._receipt_action)
        current = store.open_current()
        raw_facts = {key: raw[key] for key in ('resumable', 'dispatch_available') if type(raw.get(key)) is bool}
        binding = host_raw_binding(data, raw_facts, digest)
        commit_bytes = self.host_receipt_commit_bytes(data, store, current, measurement, binding, digest, facts, receipt_error, poll_identity)
        try:
            reference, manifest = store.write_raw_evidence(current.run_id, raw, measurement=measurement,
                commit_bytes=commit_bytes, native_receipt=binding)
        except state_store.RawEvidenceInterrupted as error:
            reference = error.reference
            manifest = store.read_raw_manifest(reference, run_id=current.run_id)
            retained = self.host_receipt_projection(data, reference, manifest, measurement, digest)
            retained['receipt']['raw']['poll_identity'] = poll_identity
            self.apply_host_receipt(retained, facts, receipt_error, interrupted=True)
            raise
        retained = self.host_receipt_projection(data, reference, manifest, measurement, digest)
        retained['receipt']['raw']['poll_identity'] = poll_identity
        return retained

    def observe(self, data):
        entry.fields(data, {"event_id", "receipt"}, {"result", "action_id", "provenance", "action_resolution", "closure", "publication_candidate", "lifecycle"})
        s, receipt = self.state, data["receipt"]
        entry.nonempty(data["event_id"])
        require(not isinstance(receipt, dict) or not isinstance(receipt.get('raw'), dict) or
                receipt['raw'].get('format') not in {'host-raw-reference-v1', 'native-raw-reference-v1'},
                'host_evidence_missing', 'retain the external raw response, not internal raw facts or references')
        receipt_error = None
        try:
            dispatch.receipt_for(s['dispatch'], receipt, {'create', 'lookup', 'result'})
            known = self.bound_ref()
            if receipt['event'] == 'result':
                require(known is not None and receipt['ref'] == known, 'identity_mismatch',
                        'observation must name the bound role')
                require(receipt['status'] in {'running', 'idle', 'turn-completed', 'stopped', 'unknown'},
                        'invalid_result', 'invalid host execution state')
            else:
                require(receipt['status'] in {'ready', 'pending', 'unknown', 'not-created'},
                        'invalid_request', 'unknown launch outcome')
                require((receipt['status'] == 'ready') == (receipt['ref'] is not None) and
                        (receipt['status'] != 'ready' or receipt['pending_id'] is None) and
                        (known is None or receipt['ref'] == known),
                        'identity_mismatch', 'receipt cannot replace the original bound identity')
        except entry.PreparationError as error:
            # Reject authority, but retain the original raw observation below.
            # Invalid evidence never consumes a legitimate call's reservation.
            receipt_error = error
        digest = state_store.raw_measure(data)[1]
        event_index_key = entry.digest([s['stage_subject'], data['event_id']])
        previous = s['events'].get(data['event_id'])
        if previous is None:
            poll_event = state_store.open_store(self.path).poll_fingerprint('event', event_index_key)
            require(poll_event is None or poll_event == digest,
                    'event_conflict', 'event ID reused with different content')
        if previous is not None:
            require(previous['digest'] == digest, 'event_conflict', 'event ID reused with different content')
            if previous['applied']:
                return _view(s, acknowledged=True)
        facts = self.host_observation_facts(data) if receipt_error is None else None
        action = s['host'].get('query') or s.get('host_action')
        if (facts is not None and facts['causal'] and
                (receipt['event'] == 'result' or action['operation'] in {'invoke-host', 'inspect-host-state'})):
            self._receipt_action = action['action_id']
        elif receipt_error is None and previous is not None and previous.get('receipt_action'):
            # The exact event digest was checked above. A committed partial
            # receipt retains its original reservation across host generations.
            require(previous['receipt_action'] == data.get('action_id') ==
                    (data.get('provenance') or {}).get('action_id'),
                    'identity_mismatch', 'partial observation must retain its original call')
            self._receipt_action = previous['receipt_action']
        # Only a resolved, read-only running observation is coalescible. Stops,
        # unknown calls, results and identity changes still take the full path.
        poll_identity = None
        if (receipt_error is None and receipt.get('event') == 'result' and receipt.get('status') == 'running' and
                not any(key in data for key in ('result', 'closure', 'publication_candidate',
                                                'lifecycle', 'action_resolution')) and
                not self.unresolved_host_actions()):
            dispatch.receipt_for(s['dispatch'], receipt, {'result'})
            require(receipt['ref'] == self.bound_ref(), 'identity_mismatch',
                    'poll must retain the original native identity')
            poll_identity = state_store.raw_measure({'receipt': {key: value for key, value in receipt.items()
                if key != 'receipt_ref'}, 'action_id': data.get('action_id')})[1]
            previous_poll = s.get('current_poll')
            if (facts['valid'] and not facts['conflict'] and
                    (facts['attributed'] or facts['duplicate'] and
                     data.get('action_id') == data['provenance']['action_id']) and
                    previous_poll == {'identity': poll_identity, 'generation': s['host']['generation']} and
                    s['host']['status'] == 'running'):
                def attribution(observation):
                    return {'event_id': observation['event_id'],
                        'receipt_ref': observation['receipt']['receipt_ref'],
                        'provenance': observation['provenance']}
                # Poll telemetry CAS follows the original storage owner. Runner
                # outer commits advance this revision without changing C's revision.
                state_store.open_store(self.path).record_poll(revision=self._authority_revision, identity=poll_identity,
                    first=attribution(s['host']['observation']), latest=attribution(data),
                    fingerprints=[('event', event_index_key, digest),
                        ('response', facts['response_key'], facts['fingerprint']),
                        ('call', facts['call_key'], entry.digest(data['provenance']['action_id']))])
                return _view(s, {'operation': 'wait-host', 'ref': self.bound_ref(),
                    'request': s['dispatch']['request']}, acknowledged=True)
        # Only host result raw is admitted here; business reports and launch
        # receipts retain their separate existing contracts.
        if (receipt.get('event') == 'result' and isinstance(receipt.get('raw'), dict) and
                not any(key in data for key in ('result', 'closure', 'publication_candidate'))):
            data = self.retain_host_receipt(data, digest, facts, receipt_error, poll_identity)
            receipt = data['receipt']
            if receipt['raw'].get('format') == 'host-raw-reference-v1':
                self.apply_host_receipt(data, facts, receipt_error)
                # The raw receipt is durable. New control work and new queries
                # must not spend the settled original invocation's reservation.
                self._receipt_action = None
                state_store.open_store(self.path).receipt_action = None
                if s['transaction'] is not None:
                    intent = self.stop_intent()
                    return self.advance_stop(intent) if intent is not None else _view(s)
                return (_view(s) if s.get('business_block') and self.stop_intent() is None else self.advance())
        # Keep even invalid/late raw receipts for exact recovery, not as authority.
        if previous is None:
            s["events"].setdefault(data["event_id"], {"digest": digest, "applied": False})
            if self._receipt_action is not None:
                s['events'][data['event_id']]['receipt_action'] = self._receipt_action
            if not any(value.get('event_id') == data['event_id'] for value in s['observations']):
                s["observations"].append(copy.deepcopy(data))
            self.save()
        if receipt_error is not None:
            raise receipt_error
        if receipt["event"] == "result":
            require(receipt["ref"] == self.bound_ref(), "identity_mismatch", "observation must name the bound role")
            require(receipt["status"] in {"running", "idle", "turn-completed", "stopped", "unknown"},
                    "invalid_result", "invalid host execution state")
            self.record_host(data, facts=facts)
            if s['host']['status'] == 'stopped':
                s.get('native_adverse', {}).pop(receipt['ref'], None)
            if 'lifecycle' in data:
                self.lifecycle_state(data['lifecycle'], advance=False)
        if "result" in data:
            entry.fields(data["result"], {"delivery_id", "status", "payload"})
            if data["result"]["status"] == "needs_input":
                require(isinstance(data["result"]["payload"], dict), "invalid_result", "question payload must be an object")
                entry.nonempty(data["result"]["payload"].get("question"))
        message_key = self.message_key(data) if "result" in data else None
        duplicate_message = message_key is not None and message_key in s.get("observed_messages", {})
        if duplicate_message:
            s["events"][data["event_id"]]["applied"] = True
            self.save()
            if self.stop_intent() is not None and s["stopped"]:
                s["step"] = "bound"
                if self.stop_intent() == "pausing":
                    s["status"] = "paused"
                    self.save()
                else:
                    self.cancel_control()
                    self.save()
            if s["step"] == "continue" and self.stop_intent() is None and s["transaction"] is None:
                return self.advance()
            return _view(s, acknowledged=True)
        if (s.get("plan_revision_transaction") or {}).get("state") not in (None, "activated") and "result" in data:
            s.setdefault("plan_revision_late_results", []).append({"event_id": data["event_id"],
                "message_digest": message_key, "observation": copy.deepcopy(data)})
            s["events"][data["event_id"]]["applied"] = True
            self.save()
            return self.advance()
        # Host facts can be retained while a prior business transaction is unresolved.
        if s["transaction"] is not None and ("result" in data or receipt["event"] != "result"):
            intent = self.stop_intent()
            if intent is not None and s["transaction"]["operation"] not in {"bind", "reconcile"}:
                if s["stopped"]:
                    s["step"] = "bound"
                    if intent == "pausing":
                        s["status"] = "paused"
                    else:
                        self.cancel_control()
                    self.save()
                return self.advance_stop(intent)
            require(s["transaction_source"] == data, "transaction_pending", "recover original business input first")
            self.replay_transaction()
            return self.advance()
        if s.get("business_block"):
            s["events"][data["event_id"]]["applied"] = True
            if message_key is not None:
                s.setdefault("observed_messages", {})[message_key] = data["event_id"]
            self.save()
            intent = self.stop_intent()
            return self.advance_stop(intent) if intent else _view(s)
        if receipt["event"] in {"create", "lookup"}:
            result = self.call("bind" if receipt["event"] == "create" else "reconcile", _source=data, receipt=receipt)
            action, provenance = s.get('host_action'), data.get('provenance')
            if (result['status'] in {'bound','not-created'} and action is not None and
                    action['operation'] == 'invoke-host' and isinstance(provenance, dict) and
                    set(provenance) == {'call_ref','response_ref','action_id'} and
                    all(isinstance(value, str) and value for value in provenance.values()) and
                    data.get('action_id') == provenance['action_id'] == action['action_id']):
                call_key = entry.digest([receipt['adapter'], provenance['call_ref']])
                require(s['host']['calls'].get(call_key, action['action_id']) == action['action_id'],
                        'host_provenance_conflict', 'creation response belongs to another call')
                s['host']['calls'][call_key] = action['action_id']
                action['creation_resolved'] = True
                action['creation_receipt'] = {'event_id':data['event_id'], 'outcome':result['status']}
            if result["status"] == "bound":
                s["step"] = "bound"
            elif result["status"] == "not-created":
                s["status"], s["step"], s["stopped"] = "cancelled", "not-created", True
            else:
                s["step"] = "host-response"
        else:
            require(receipt["ref"] == self.bound_ref(), "identity_mismatch", "observation must name the bound role")
            require(receipt["status"] in {"running", "idle", "turn-completed", "stopped", "unknown"},
                    "invalid_result", "invalid host execution state")
            if s["status"] == "cancelled":
                raise entry.PreparationError("attempt_ended", "cancelled attempt cannot accept late results")
            if "publication_candidate" in data:
                require("result" not in data and "closure" not in data, "invalid_result", "candidate readiness is separate from completed delivery")
                self.publication_candidate(data["publication_candidate"], receipt)
                s["events"][data["event_id"]]["applied"] = True
                self.save()
                return _view(s)
            if "closure" in data:
                require(s["stage"] == 4, "invalid_result", "closure observation belongs to Stage 4")
                publication = s.get("publication") or {}
                if publication and data["closure"].get("implementation_problem") is not None:
                    observed_publication = supervision.reconcile_publication(self.publication_transaction(), for_implementation_recovery=True)
                    require(observed_publication["state"] in {"not-published", "prepared"},
                            "already_published", "published or uncertain actions cannot return to implementation")
                known_merge = (publication.get("result") or {}).get("merge_commit") or (
                    ((publication.get("error") or {}).get("error") or {}).get("context") or {}).get("merge_commit")
                require(not (known_merge and data["closure"].get("implementation_problem") is not None),
                        "already_published", "published merge permits cleanup-only, never implementation replay")
                # Existing Git adapter determines actual ancestry and cleanup.
                self.require_controls_reconciled()
                result, applied = dispatch.checkpoint(s["control"], s["handoff"], "closure-result", data["closure"])
                s["control"]["context"] = result["context"]
                s["control_receipt"] = applied
                self.next_envelope(applied)
                s["closure_effects"] = result["effects"]
                s["closure_receipt"] = copy.deepcopy(data)
                self.save()
                if result.get("return_stage") == 3:
                    s["recovery_action"] = {"operation": "implementation-recovery", "stage": 3,
                        "ref": handoff.predecessor_facts(s["handoff"]["predecessor"])["role_ref"], "binding": s["handoff"]["binding"],
                        "problem": data["closure"]["implementation_problem"]}
                    return self.block(entry.PreparationError("implementation_required", "return the retained flow to its original controller's implementation recovery; do not wait on closure"))
            if s.get("stop_requested") in {"pausing", "cancelling"}:
                if s["stop_requested"] == "pausing" and "result" in data:
                    previous = s.get("deferred_observation")
                    require(previous is None or self.message_key(previous) == message_key,
                            "business_pending", "retain the first paused business result")
                    if previous is None:
                        s["deferred_observation"] = copy.deepcopy(data)
                if s["stopped"]:
                    if s["stop_requested"] == "cancelling":
                        self.cancel_control()
                    else:
                        s["status"] = "paused"
                s["step"] = "bound"
            elif "result" in data:
                if s["step"] == "technical-error":
                    return _view(s)  # Preserve the original failure and recovery authority.
                require(s.get("pending") is None, "decision_pending", "retain the exact unresolved controller matter")
                self.call("receive", _source=data, receipt=receipt, result=data["result"])
            elif (receipt['status'] == 'stopped' and s.get('final_integration_recovery') is not None and
                  (s.get('host_action') or {}).get('operation') == 'continue-host' and
                  ((s.get('host_action') or {}).get('payload', {}).get('intent') or {}).get('kind') == 'final-integration'):
                s['step'] = 'bound'
                s.pop('continuation_intent', None)
            elif s["step"] == "technical-error" or s.get("pending") is not None:
                pass  # Liveness evidence cannot resolve a business failure.
            elif s["step"] == "continue":
                pass  # Current host facts may unblock the retained business intent.
            elif s.get("publication") or s.get("publication_candidate") or s["step"] == "accepted":
                pass  # Reconcile host actions without replacing saved completion.
            elif receipt["status"] in {"idle", "turn-completed", "stopped", "unknown"}:
                s["status"] = "blocked"
                s["error"] = {"code": "business_result_missing", "downstream_ready": False,
                              "message": "Host state alone does not establish business completion or permission to resume"}
            else:
                s["step"] = "bound"
        s["events"][data["event_id"]]["applied"] = True
        if poll_identity is not None and s['host']['status'] == 'running':
            s['current_poll'] = {'identity': poll_identity, 'generation': s['host']['generation']}
        if message_key is not None and s.get("deferred_observation") != data:
            s.setdefault("observed_messages", {})[message_key] = data["event_id"]
        if s["status"] == "blocked":
            # A newly validated result can resolve an observation/transport error.
            # Technical business outcomes remain blocked until exact recovery.
            if ("result" in data or receipt["event"] in {"create", "lookup"}) and data.get("result", {}).get("status") != "technical_error" and s["step"] != "technical-error" and s.get("error", {}).get("code") != "no_progress":
                s["status"] = s.get("stop_requested", "active")
                s.pop("error", None)
        self.save()
        if s["transaction"] is not None:
            intent = self.stop_intent()
            return self.advance_stop(intent) if intent is not None else _view(s)
        return self.advance()

    def decide(self, data):
        require((self.state.get("plan_revision_transaction") or {}).get("state") in (None, "activated"),
                "plan_revision_pending", "old decisions are closed during plan revision")
        require((self.state.get("scope_revision_transaction") or {}).get("state") in (None,),
                "scope_revision_pending", "old acceptance and stage-entry decisions are closed during revision")
        s = self.state
        prior = s.get("decisions", {}).get(data.get("decision_id"))
        if prior is not None:
            require(prior == data, "decision_conflict", "decision cannot be replaced")
            return _view(s, acknowledged=True)
        self.require_business_ready()
        decision_matches(s.get("pending"), data)
        pending = s["pending"]
        flow_intent = data.get("flow_intent")
        if "flow_intent" in data:
            require(isinstance(flow_intent, str) and flow_intent in {"stepwise", "continuous", "design-only"} and
                    pending["kind"] == "user-decision" and s["stage"] in {2, 3},
                    "invalid_request", "flow intent requires the current stage's user decision")
            require(flow_intent != "design-only" or s["stage"] == 2,
                    "invalid_request", "design-only scope is available before Stage 2 completion")
        deferred = s.get("deferred_decisions", {}).get(data["decision_id"])
        intent = self.stop_intent()
        if deferred is not None:
            if deferred["disposition"] == "cancelled" and intent is None:
                if deferred["decision"] == data:
                    return _view(s, acknowledged=True)
                require(data["reference"] != deferred["decision"]["reference"], "decision_conflict",
                        "cancelled input needs a fresh controller decision after authorized recovery")
            else:
                require(deferred["decision"] == data, "decision_conflict", "saved answer cannot be replaced")
        if intent is not None:
            disposition = "cancelled" if intent == "cancelling" else "paused"
            saved = {"decision": copy.deepcopy(data), "disposition": disposition}
            if deferred != saved:
                s.setdefault("deferred_decisions", {})[data["decision_id"]] = saved
                self.save()
            # Recording a valid reply is not acceptance, continuation or resume.
            result = self.advance_stop(intent)
            result.update(decision_deferred=True, acknowledged=deferred == saved)
            return result
        if s["transaction"] is not None:
            require(s["transaction"]["operation"] == "accept" and s["transaction_source"] == data,
                    "decision_conflict", "retain the decision bound to the original B transaction")
            self.replay_transaction()
            return self.advance()
        if flow_intent is not None:
            s["mode"] = "continuous" if flow_intent == "continuous" else "stepwise"
            s["flow_intent"] = copy.deepcopy(data)
        if pending["kind"] == "publication-readiness":
            require(data["answer"] == "accept", "decision_required", "original controller readiness acceptance required")
            s["publication_candidate"]["decision"] = copy.deepcopy(data)
            s["pending"], s["status"], s["step"] = None, "active", "publication-ready"
        elif pending["kind"] == "acceptance":
            require(data["answer"] == "accept", "decision_required", "acceptance requires the controller's accept decision")
            if (s.get("publication") or {}).get("intake") is not None:
                original = s["publication"].get("acceptance_decision")
                require(original is None or original == data, "decision_conflict", "retain the original publication acceptance decision")
                if original is None:
                    s["publication"]["acceptance_decision"] = copy.deepcopy(data)
                    self.save()
            self.call("accept", _source=data, decision={"reference": data["reference"], "delivery_digest": pending["subject"]["delivery_digest"]})
            return self.advance()
        elif pending["kind"] == "stage-entry":
            require(data["answer"] in {"confirm", "continuous"}, "decision_required", "confirm or continuous required")
            if data["answer"] == "continuous":
                s["mode"] = "continuous"
            s.pop("flow_intent", None)
            s["pending"] = None
        else:
            s["pending"], s["status"], s["step"] = None, "active", "continue"
            s["continuation_intent"] = {"kind": "decision", "decision": copy.deepcopy(data)}
        if pending["kind"] == "user-decision":
            s.setdefault("decision_provenance", {})[data["decision_id"]] = {
                "pending_kind": pending["kind"], "pending_subject": copy.deepcopy(pending["subject"]),
                "decision": copy.deepcopy(data),
                "controller_ref": s["control"]["context"]["controller_ref"],
                "requirement_identity": copy.deepcopy(s["handoff"]["requirement_identity"]),
                "handoff_digest": s["handoff"]["digest"]}
        s.setdefault("decisions", {})[data["decision_id"]] = copy.deepcopy(data)
        s["last_decision"] = copy.deepcopy(data)
        self.save()
        return self.advance()

    def cancel_control(self):
        s = self.state
        selector = s["handoff"]["authorization"].get("control_plan_id")
        evidence = {"control_plan_id": selector} if selector else {}
        cancellation = s.get('cancellation')
        if cancellation is None:
            cancellation = {'port':copy.deepcopy(s['control']), 'evidence':copy.deepcopy(evidence), 'result':None, 'applied':False}
            s['cancellation'] = cancellation
            self.save()
            result, applied = dispatch.checkpoint(cancellation['port'], s['handoff'], 'cancel', cancellation['evidence'])
            cancellation['result'] = {'result':copy.deepcopy(result),'receipt':copy.deepcopy(applied)}
            self.save()
        require(cancellation['result'] is not None, 'cancellation_outcome_unknown', 'reconcile the retained original cancellation envelope')
        result, applied = cancellation['result']['result'], cancellation['result']['receipt']
        if not cancellation['applied']:
            s["control"]["context"], s["control_receipt"] = result["context"], applied
            self.next_envelope(applied)
            cancellation['applied'] = True
        # Every old writer must still be reconciled by the original control recovery.
        s["stop_effects"] = [e for e in result["effects"] if not (s["stopped"] and e.get("ref") == self.bound_ref())]
        s["status"] = "cancelled" if not s["stop_effects"] else "cancelling"

    def publication_candidate(self, data, receipt):
        self.require_business_ready()
        s, saved = self.state, self.state["handoff"]
        self.require_not_stopping()
        require(s["stage"] in {2, 4} and not s.get("publication"), "already_published", "reconcile the original publication")
        entry.fields(data, {"candidate_commit", "artifacts", "checks"})
        handoff.commit(data["candidate_commit"])
        handoff.strings(data["artifacts"]); handoff.strings(data["checks"])
        require(receipt["status"] == "stopped", "host_evidence_missing", "publication requires the original writer's stopped receipt")
        require(s["host"]["status"] == "stopped", "host_evidence_missing", "publication readiness needs current causal stopped proof")
        self.verify_publication_authority(data["candidate_commit"], before=True)
        s["publication_candidate"] = {"payload": copy.deepcopy(data), "receipt": copy.deepcopy(receipt),
                                      "handoff_digest": saved["digest"], "decision": None,
                                      "target_head": supervision._branch_oid(Path(saved["binding"]["repository"]), saved["binding"]["target_branch"])}
        s["pending"] = pending_decision("publication-readiness", {**self.subject(), "candidate": entry.digest(data)},
                                        "Record the original controller's candidate readiness decision")
        s["status"], s["step"] = "needs_input", "publication-readiness"

    def verify_publication_authority(self, candidate, *, before):
        s, saved = self.state, self.state["handoff"]
        handoff.unseal(saved)
        control.validate_context(s["control"]["context"])
        ref, state, owner = dispatch.matching(s["dispatch"], s["control"]["context"])
        require(ref == self.bound_ref() and s["control"]["context"]["controller_ref"] == saved["controller_ref"] and
                owner["binding"] == saved["binding"] and owner["git_baseline_commit"] == saved["scope"]["baseline"] and
                s["control"]["context"]["requirement_identity"] == saved["requirement_identity"],
                "authority_changed", "publication owner, baseline or binding changed")
        require(state in ({"designing"} if s["stage"] == 2 else {"closing", "cleanup-pending", "completed"}),
                "authority_changed", "publication control is no longer active")
        for pin in s["packages"].values():
            skill_preflight.verify_identity(pin)
        # Never substitute another cwd for the pinned entry after removal.
        if Path(saved["binding"]["worktree"]).exists():
            current = handoff.refresh(saved["entry"], saved["expected_entry"], after_work=True)
            identity, revision = handoff.source(current, saved["requirement"], s["stage"])
            require(identity == saved["requirement_identity"] and revision == saved["source_commit"],
                    "source_changed", "publication source changed")
        handoff.phase_evidence(saved, receiving=True)
        scope, binding = saved["scope"], saved["binding"]
        root = binding["repository"]
        if s["stage"] == 4:
            predecessor = handoff.predecessor_facts(saved["predecessor"])
            accepted = predecessor["payload"]["candidate_commit"]
            require(owner["candidate"] == accepted and owner["closure_paths"] == scope["closure_paths"] and
                    owner["implementation_paths"] == scope["implementation_paths"] and
                    owner["protected_paths"] == scope["protected_paths"], "scope_changed", "closure authority changed")
            control.validate_review(accepted, predecessor["payload"]["review"], predecessor["payload"]["verification"], predecessor["role_ref"])
            if before:
                require(supervision._branch_oid(Path(root), binding['target_branch']) == predecessor['payload']['verification']['expected_target_head'],
                        'target_changed', 'implementation validation belongs to another target')
            handoff.ancestor(root, accepted, candidate)
            changed = supervision._changed_paths(Path(root), accepted, candidate)
            require(set(changed) <= set(scope["closure_paths"]), "scope_changed", "closure candidate changes implementation or unapproved documents")
        else:
            require(owner["allowed_paths"] == scope["owned_paths"] and owner["protected_paths"] == scope["protected_paths"],
                    "scope_changed", "planning authority changed")
        for relative in scope["protected_paths"]:
            require(handoff.blob(root, scope["baseline"], relative) == handoff.blob(root, candidate, relative) and
                    handoff.mode(root, scope["baseline"], relative) == handoff.mode(root, candidate, relative),
                    "source_changed", "publication changed protected source")
        if before:
            report = supervision.verify_worktree({"binding": binding, "platform_cwd": binding["worktree"]})
            require(report["current_commit"] == candidate and not supervision._full_status(Path(binding["worktree"])),
                    "candidate_changed", "publication requires the exact clean candidate")

    def publication_transaction(self):
        publication = self.state["publication"]
        return {key: copy.deepcopy(publication[key]) for key in ("operation", "request", "facts")}

    def record_publication_fact(self, fact):
        self.state["publication"]["facts"].append(copy.deepcopy(fact))
        self.save()

    def verify_publication_readiness(self):
        s = self.state
        ready = s.get("publication_candidate")
        require(ready is not None and ready["decision"] is not None and ready["handoff_digest"] == s["handoff"]["digest"],
                "candidate_not_accepted", "receive the stopped candidate and record its original readiness decision first")
        if s["stage"] == 2:
            authority = s.get("scope_authority")
            require(authority is not None and authority.get("state") == "sealed" and
                    authority["candidate_commit"] == ready["payload"]["candidate_commit"] and
                    authority["seal"] == (s["control"]["context"].get("handoff_progress") or {}).get("scope_seal"),
                    "scope_incomplete", "seal the exact stopped planning candidate before publication")
            stage_scope.validate_scope(authority["scope"], s["handoff"]["binding"]["repository"],
                stage=2, requirement_path=s["handoff"]["requirement_identity"]["path"],
                candidate_commit=ready["payload"]["candidate_commit"],
                generated_outputs=generated_outputs_for(authority["proposal"]))
        dispatch.receipt_for(s["dispatch"], ready["receipt"], {"result"})
        require(ready["receipt"]["status"] == "stopped" and ready["receipt"]["ref"] == self.bound_ref(),
                "host_evidence_missing", "exact original writer stop proof required")
        current = s["host"].get("observation", {}).get("receipt", {})
        require(current.get("ref") == self.bound_ref() and s["host"]["status"] == "stopped",
                "writer_active", "current authenticated writer state must be stopped")
        require(s['stopped'] and not self.stop_barrier_pending(), 'host_evidence_missing', 'publication requires the complete native stop barrier')
        self.verify_publication_authority(ready["payload"]["candidate_commit"], before=False)
        require(not self.unresolved_host_actions(), "host_action_pending",
                "settle every original host action before publication or new intake")
        return ready

    def publication_payload(self, result):
        s, ready = self.state, self.state["publication_candidate"]["payload"]
        payload = {key: copy.deepcopy(ready[key]) for key in ("artifacts", "checks")}
        if s["stage"] == 2:
            payload.update(planning_commit=ready["candidate_commit"], planning_merge_commit=result["merge_commit"],
                           planning_paths=s["publication"]["request"]["allowed_paths"])
        else:
            binding, scope = s["handoff"]["binding"], s["handoff"]["scope"]
            payload.update(candidate_commit=handoff.predecessor_facts(s["handoff"]["predecessor"])["payload"]["candidate_commit"],
                           merge_commit=result["merge_commit"],
                           cleanup=supervision.verify_disposition(binding, result["candidate_commit"], result["merge_commit"]),
                           changed_paths=copy.deepcopy(result['changed_paths']))
        return payload

    def finish_publication(self, result):
        s = self.state
        s["publication"]["result"], s["publication"]["error"] = copy.deepcopy(result), None
        s["publication"]["completion_payload"] = self.publication_payload(result)
        if s["status"] == "blocked":
            s["status"] = "active"
            s.pop("error", None)
        s["step"] = "publication-complete"
        self.save()
        return _view(s, {"operation": "publication-receipt", "publication": s["publication"]})

    def reconcile_publication(self, *, resume=False):
        s = self.state
        if not resume and s.get('publication') is None and s.get('final_integration_recovery'):
            closure = s['final_integration_recovery']['closure_handoff']
            audit = read_recovery_publication(self.path, closure)
            try:
                result = supervision.reconcile_publication(audit['transaction'], for_implementation_recovery=True)
            except supervision.ProtocolError as error:
                result = supervision._error_response(error, 'reconcile-publication')
            return _view(s, {'operation': 'publication-observation', 'result': result,
                             'publication_ref': closure['publication_ref']})
        require(s.get("publication") is not None, "publication_missing", "no original publication to reconcile")
        if resume:
            self.require_business_ready()
            self.require_not_stopping()
            self.verify_publication_readiness()
        try:
            result = supervision.reconcile_publication(self.publication_transaction(), resume=resume,
                                                       record=self.record_publication_fact if resume else None)
        except supervision.ProtocolError as error:
            if not resume:
                return _view(s, {"operation": "publication-observation", "result": supervision._error_response(error, "reconcile-publication")})
            s["publication"]["error"] = supervision._error_response(error, "reconcile-publication")
            self.save()
            return self.block(entry.PreparationError(error.code, error.message))
        if resume:
            return self.finish_publication(result)
        return _view(s, {"operation": "publication-observation", "result": result})

    def receive_publication(self):
        self.require_business_ready()
        s = self.state
        self.require_not_stopping()
        require(s.get("publication") is not None and s["publication"].get("result") is not None,
                "publication_pending", "reconcile publication before receiving it")
        publication = s["publication"]
        actual = supervision.reconcile_publication(self.publication_transaction())
        require(actual["state"] in {"planning_published", "completed"} and
                actual["merge_commit"] == publication["result"]["merge_commit"],
                "publication_pending", "reverify the original publication before B intake")
        message = {"delivery_id": "publication:" + entry.digest(publication["request"]),
                   "status": "completed", "payload": publication["completion_payload"]}
        if s["transaction"] is not None:
            require(s["transaction"]["operation"] in {"receive", "accept"}, "transaction_pending", "recover original B transaction")
            if s["transaction"]["operation"] == "receive":
                require(s["transaction"]["result"] == message and
                        s["transaction"]["receipt"] == s["publication_candidate"]["receipt"],
                        "transaction_pending", "retain the original publication intake")
            self.replay_transaction()
        if s["dispatch"].get("delivery") is not None:
            require(s["dispatch"]["delivery"]["message"] == message, "delivery_conflict", "retain the original B delivery")
            if s["status"] == "accepted":
                return _view(s, acknowledged=True)
            result = self.advance()
            result["acknowledged"] = True
            return result
        # This is stage-owner composition, not a new host observation or a claim
        # that the stopped child subsequently ran Git. Preserve both sources.
        self.verify_publication_readiness()
        publication["intake"] = {"kind": "stage-owner-publication", "owner": s["handoff"]["expected_entry"]["actor"],
                                 "native_candidate": copy.deepcopy(s["publication_candidate"]),
                                 "git_facts": copy.deepcopy(actual), "result": copy.deepcopy(message)}
        self.save()
        self.call("receive", receipt=s["publication_candidate"]["receipt"], result=message)
        s["step"] = "accepted" if s["dispatch"]["status"] == "accepted" else "received"
        self.save()
        return self.advance()

    def publication(self, data):
        self.require_business_ready()
        entry.fields(data, {"candidate_commit", "reference"}, {"expected_target_head", "planning_paths"})
        self.require_not_stopping()
        entry.nonempty(data["reference"])
        s = self.state
        require(s["stage"] in {2, 4} and s["dispatch"] is not None and s["dispatch"]["status"] == "bound" and
                s["status"] not in {"paused", "pausing", "cancelling", "cancelled"}, "authority_missing", "publication requires its active bound stage")
        ready = self.verify_publication_readiness()
        require(data["candidate_commit"] == ready["payload"]["candidate_commit"] and data["reference"] == ready["decision"]["reference"],
                "candidate_changed", "publication must consume the original readiness decision")
        scope, binding = s["handoff"]["scope"], s["handoff"]["binding"]
        if s["stage"] == 2:
            paths = data.get("planning_paths", scope["owned_paths"])
            control.paths(paths)
            require(set(paths) <= set(scope["owned_paths"]), "scope_changed", "planning publication exceeds approved paths")
            request = {"binding": binding, "planning_commit": data["candidate_commit"], "allowed_paths": paths,
                       "protected_paths": scope["protected_paths"]}
            operation = "publish-planning"
        else:
            require(data.get("expected_target_head") == ready["target_head"], "target_changed", "retain the target bound to readiness")
            require(not (s["control"]["context"].get("handoff_progress") or {}).get("merge"),
                    "already_published", "published merge permits cleanup-only")
            request = {"binding": binding, "candidate_commit": data["candidate_commit"],
                       "expected_target_head": data.get("expected_target_head"), "scope_base_commit": scope["baseline"],
                       "allowed_paths": sorted(set(scope["implementation_paths"] + scope["closure_paths"])),
                       "protected_paths": scope["protected_paths"]}
            operation = "complete-worktree"
        existing = s.get("publication")
        if existing:
            require(existing["request"] == request, "publication_conflict", "reconcile the original publication before changing its candidate")
            return _view(s, {"operation": "publication-receipt" if existing.get("result") else "reconcile-publication",
                             "publication": existing}, acknowledged=True)
        s["publication"] = {"operation": operation, "request": request, "reference": data["reference"],
                            "result": None, "error": None, "issued": True, "facts": []}
        self.save()
        try:
            # Existing primitives own Git behavior, locks, merge and cleanup.
            result = (supervision.publish_planning if s["stage"] == 2 else supervision.complete_worktree)(request, record=self.record_publication_fact)
        except supervision.ProtocolError as error:
            s["publication"]["error"] = supervision._error_response(error, operation)
            self.save()
            return self.block(entry.PreparationError(error.code, "publication requires original-protocol reconciliation; do not republish"))
        return self.finish_publication(result)

    def control_receipt_subject(self, data):
        # A completed takeover belongs to its frozen delivery, so an exact
        # replay keeps its receipt identity when the current stage changes.
        if data['action'] == 'successor-ready':
            return data['evidence'].get('input_digest') or self.state['stage_subject']
        return (data['evidence'].get('handoff_id') or data['evidence'].get('attempt') or
                data['evidence'].get('revision_id') or self.state['stage_subject'])

    def control_action(self, data):
        entry.fields(data, {'action', 'evidence', 'receipt'})
        original_input = copy.deepcopy(data)
        require(isinstance(data['receipt'], dict) and data['receipt'],
                'host_evidence_missing', 'original authenticated host/controller evidence required')
        invocation = ((data['receipt'].get('response_ref') if data['action'] == 'validation-result' else None) or
                      data['receipt'].get('reference') or data['receipt'].get('call_ref') or
                      data['receipt'].get('operation_id') or entry.digest(data['receipt']))
        control_subject = self.control_receipt_subject(data)
        receipt_store, receipt_current, receipt_key = self.receipt_store(
            'control:' + data['action'], control_subject, invocation)
        cached_receipt = receipt_store.read_receipt(receipt_current, receipt_key, original_input)
        if cached_receipt is not None:
            if data['action'] in {'archive', 'archive-result'}:
                # read_receipt has verified/repaired the committed membership
                # proof; a crash after root CAS must not strand a settled slot.
                self.release_settled_reserves()
            return _view(self.state, {'operation': 'control-effects',
                'result': {**cached_receipt, 'effects': []}, 'write_authority': False}, acknowledged=True)
        require((self.state.get("plan_revision_transaction") or {}).get("state") in (None, "activated") or
                data.get("action") == "plan-revision-activate",
                "plan_revision_pending", "old control lease is closed during plan revision")
        entry.fields(data, {"action", "evidence", "receipt"})
        entry.nonempty(data["action"])
        require(data["action"] in {"successor-ready", "archive", "archive-result",
                    "scope-seal", "scope-revise", "plan-revision-activate", "escalate-implementation", "prepare-dispatch-recovery", "dispatch-recovery-intent", "dispatch-recovery-result", "recover-dispatch", "release-recovery-allocation", "capture-integration", "local-review", "ticket-accept", "ticket-check", "review-applicability", "candidate-ready", "review-converged", "validation-start", "validation-result", "validation-retry", "invalidate-candidate"},
                "invalid_operation", "use the original bounded control recovery/closure operation")
        require(isinstance(data["receipt"], dict) and data["receipt"], "host_evidence_missing", "original authenticated host/controller evidence required")
        s = self.state
        if data['action'] == 'capture-integration':
            self.require_not_stopping()
            self.require_business_ready()
            require(s['stage'] == 3 and s.get('final_integration_recovery') is not None and
                    s['host']['status'] == 'stopped' and s.get('stopped') and not self.stop_barrier_pending() and
                    not self.unresolved_host_actions(), 'host_evidence_missing', 'capture needs the original final recovery writer to stop')
            require(data['receipt'].get('controller_ref') == s['handoff']['controller_ref'] and
                    data['receipt'].get('reference') == data['evidence'].get('reference'),
                    'identity_mismatch', 'original Controller integration receipt required')
            require(set(data['evidence']) == {'reference'}, 'invalid_evidence', 'Git derives final integration bytes and paths')
        if data["action"] == "scope-revise":
            require_scope_correction_available(self.outer)
        if (s.get("scope_revision_transaction") or {}).get("state") not in (None,):
            require(data["action"] == "scope-revise", "scope_revision_pending",
                    "scope revision has closed the old control lease")
        if data["action"] == "plan-revision-activate":
            transaction = s.get("plan_revision_transaction")
            require(transaction is not None and transaction["state"] == "planning-committed" and
                    data["evidence"]["revision_id"] == transaction["input"]["revision_id"] and
                    data["receipt"] == {"controller_ref": transaction["input"]["controller_ref"],
                        "reference": transaction["input"]["classification"]["reference"]},
                    "plan_revision_conflict", "activation must retain the saved original Controller decision")
        if data['action'] in {'archive', 'archive-result'}:
            return self.archive_action(data)
        if data['action'] == 'successor-ready':
            original = copy.deepcopy(data)
            retained = [value for state in [s, *s.get('history', [])]
                        for value in state.get('control_transactions', {}).values() if value.get('takeover_input') == original]
            if retained:
                require(all(value['request'] == retained[0]['request'] for value in retained),
                        'identity_mismatch', 'conflicting original takeover request')
                completed = [value['result'] for value in retained if value.get('result') is not None]
                if completed:
                    require(all(value == completed[0] for value in completed), 'identity_mismatch', 'conflicting original takeover result')
                    handoff_id = completed[0]['handoff_id']
                    retired = s['control']['context'].get('retired_handoffs', {}).get(handoff_id)
                    frozen_evidence = retained[0]['request']['evidence']
                    require(retired is not None and retired['successor'] == {key:value for key,value in frozen_evidence.items() if key != 'control_plan_id'} and
                            frozen_evidence.get('control_plan_id', retired['plan']['plan_id']) == retired['plan']['plan_id'],
                            'identity_mismatch', 'completed takeover no longer matches its retired evidence')
                    if retired['archive_status'] == 'not-requested':
                        return self.archive_action({'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                            'receipt': {'controller_ref': s['control']['context']['controller_ref'],
                                'handoff_transaction': entry.digest(retained[0]['request'])}})
                    return _view(s, {'operation': 'control-effects', 'result': {**completed[0],
                        'context': s['control']['context'], 'effects': []}}, acknowledged=True)
                data = copy.deepcopy(retained[0]['request'])
            else:
                require('takeover_proof' not in data['evidence'], 'invalid_evidence', 'takeover proof is derived from saved host evidence')
                self.require_business_ready()
                require(s.get('stopped') and not self.stop_barrier_pending(), 'host_evidence_missing', 'settle original writer and calls before takeover')
                stop = s['host'].get('last_stop')
                require(s['host']['status'] == 'stopped' and stop and stop.get('provenance') and
                        stop == s['host'].get('observation'), 'host_evidence_missing', 'fresh causal original host stop required')
                selected = control.selected_control(s['control']['context'], data['evidence'])
                carrier = selected['carrier']
                require(stop['receipt'].get('ref') == carrier['ref'], 'identity_mismatch', 'stop belongs to another carrier')
                calls = {'invocations': copy.deepcopy(s['host']['calls']), 'responses': copy.deepcopy(s['host']['seen']),
                         'unresolved': self.unresolved_host_actions()}
                data = copy.deepcopy(data)
                data['evidence']['takeover_proof'] = {'ref': carrier['ref'], 'attempt': carrier['attempt'],
                    'adapter': stop['receipt']['adapter'], 'host_ref': s['handoff']['expected_entry']['actor']['thread_id'],
                    'invocation_id': stop['provenance']['call_ref'], 'response_id': stop['provenance']['response_ref'],
                    'stop_receipt': {key: copy.deepcopy(stop[key]) for key in
                        ('event_id', 'receipt', 'action_id', 'provenance', 'action_resolution')},
                    'business_calls': calls, 'business_calls_digest': entry.digest(calls)}
            takeover_input = original
        if data['action'] == 'escalate-implementation':
            escalation_paused = (s['status'] == 'paused' or s['status'] == 'blocked' and s.get('blocked_from') == 'paused') and s.get('stopped') is True and self.stop_intent() == 'pausing'
            if not escalation_paused:
                self.require_not_stopping()
            handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
            require(data['receipt'].get('controller_ref') == s['control']['context']['controller_ref'] and
                    data['receipt'].get('reference') == data['evidence']['reference'],
                    'identity_mismatch', 'original Controller escalation receipt required')
            original = copy.deepcopy(data)
            for field in ('stop', 'snapshot'):
                original['evidence'].pop(field, None)
            retained = [v for v in s.get('control_transactions', {}).values()
                        if v.get('escalation_input') == original]
            if retained:
                require(all(field not in data['evidence'] or data['evidence'][field] == retained[0]['request']['evidence'][field]
                            for field in ('stop', 'snapshot')), 'snapshot_changed', 'caller changed derived escalation evidence')
                data = copy.deepcopy(retained[0]['request'])
                if retained[0].get('result') is not None:
                    current = s['control']['context']['handoff_progress']
                    worktree = Path(current['binding']['worktree'])
                    control_git.verify_stopped_snapshot(worktree, data['evidence']['snapshot'],
                                                        control_git.recovery_snapshot(worktree, current)[0])
                    if escalation_paused:
                        return _view(s, acknowledged=True)
                    return _view(s, {'operation': 'control-effects', 'result': retained[0]['result']}, acknowledged=True)
            else:
                require(not ({'stop', 'snapshot'} & set(data['evidence'])), 'invalid_evidence', 'escalation snapshot is derived from saved native evidence')
                stop = s['host'].get('last_stop')
                require(s['host']['status'] == 'stopped' and stop and stop.get('provenance') and stop.get('git_snapshot'),
                        'host_evidence_missing', 'escalation requires a saved causal stop and byte snapshot')
                data = copy.deepcopy(data)
                data['evidence'].update(stop=copy.deepcopy(stop), snapshot=copy.deepcopy(stop['git_snapshot']))
            require(not s.get('publication') and not s.get('transport_loss') and not self.unresolved_host_actions() and
                    not self.stop_barrier_pending() and all(v.get('stopped') for v in s.get('review_activity', {}).values()),
                    'host_action_pending', 'settle all native, review and publication activity before escalation')
            escalation_input = original
        if data['action'] == 'release-recovery-allocation':
            self.require_not_stopping()
            handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
            require(data['receipt'].get('controller_ref') == s['control']['context']['controller_ref'] and
                    data['receipt'].get('reference') == data['evidence']['reference'], 'identity_mismatch', 'Controller ownership assumption required')
        recovery_action = data['action'] in {'dispatch-recovery-intent', 'dispatch-recovery-result', 'recover-dispatch'}
        if recovery_action:
            recovery = (s['control']['context'].get('handoff_progress') or {}).get('recovery')
            if data['action'] == 'dispatch-recovery-intent' and recovery and recovery['receipts'] and recovery['receipts'][-1]['status'] == 'not-created':
                data = copy.deepcopy(data)
                data['receipt']['derived_not_created'] = recovery['receipts'][-1]['response_ref']
            cached = s.get('control_transactions', {}).get(entry.digest(data))
            if cached and cached.get('result') is not None:
                if data['action'] == 'dispatch-recovery-intent':
                    return self.recovery_view(acknowledged=True)
                if data['action'] == 'dispatch-recovery-result' and s.get('transport_loss'):
                    return _view(s, s['recovery_action'], acknowledged=True)
                return _view(s, {'operation':'control-effects', 'result':cached['result']}, acknowledged=True)
            if data['action'] == 'dispatch-recovery-intent' and cached is None:
                self.require_finalizations_released()
            if data['action'] != 'dispatch-recovery-result':
                self.require_not_stopping()
            require(not s.get('publication') and (data['action'] == 'dispatch-recovery-result' or not s.get('transport_loss')),
                    'host_recovery_required', 'recover only in the original retained host')
            handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
            require(not self.unresolved_host_actions(), 'host_action_pending', 'reconcile original calls before replacement')
            recovery = (s['control']['context'].get('handoff_progress') or {}).get('recovery')
            require(recovery is not None, 'recovery_missing', 'prepare the original dispatcher recovery first')
            if data['action'] == 'dispatch-recovery-result':
                require(data['receipt'] == data['evidence']['receipt'], 'host_evidence_missing', 'retain exact external replacement response')
            else:
                require(data['receipt'].get('controller_ref') == s['control']['context']['controller_ref'] and
                        data['receipt'].get('reference') == data['evidence']['decision']['reference'],
                        'identity_mismatch', 'original Controller snapshot decision required')
                require(s['host']['status'] == 'stopped' and not self.stop_barrier_pending(),
                        'host_evidence_missing', 'current complete old-writer stop barrier required')
        if data['action'] == 'prepare-dispatch-recovery':
            self.require_not_stopping()
            require(s['stage'] in {2, 3} and not s.get('publication') and not s.get('transport_loss'),
                    'host_recovery_required', 'retain original host and stage; publication must reconcile first')
            handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
            require(not self.unresolved_host_actions() and not self.stop_barrier_pending(),
                    'host_evidence_missing', 'reconcile all original calls and prove every old writer stopped')
            stop = s['host'].get('last_stop')
            require(s['host']['status'] == 'stopped' and stop is not None and stop.get('provenance') is not None,
                    'host_evidence_missing', 'current causal stop observation required')
            raw = host_raw_facts(stop['receipt'])
            require(raw.get('resumable') is False and raw.get('dispatch_available') is True,
                    'host_recovery_required', 'original host must prove nonresumability and current dispatch capability')
            stop_refs = [stop['receipt']['receipt_ref']]
            refs = [self.bound_ref()]
            for slot in s.get('allocations', {}).values():
                record = slot.get('record') or {}
                stopped = [r for r in record.get('receipts', []) if r.get('status') == 'stopped']
                if stopped:
                    stop_refs.append(stopped[-1]['receipt_ref']); refs.append(stopped[-1]['ref'])
            evidence = data['evidence']
            require(data['receipt'].get('controller_ref') == s['control']['context']['controller_ref'] and
                    data['receipt'].get('reference') == evidence['reference'], 'identity_mismatch', 'original Controller recovery reference required')
            require(set(evidence['stop_receipts']) == set(stop_refs) and
                    evidence['call_receipts'] == [stop['provenance']['response_ref']],
                    'host_evidence_missing', 'references must name actual saved stop and invocation receipts')
            data = copy.deepcopy(data)
            checkpoint = s['control']['context']['handoff_progress']
            if control.direct_implementation(checkpoint) or 'design_input' in checkpoint:
                worktree = Path(checkpoint['binding']['worktree'])
                control_git.verify_stopped_snapshot(worktree, stop.get('git_snapshot'),
                                                    control_git.recovery_snapshot(worktree, checkpoint)[0])
            data['evidence']['host_evidence'] = {'stopped_refs': sorted(set(refs)),
                'stop_receipts': evidence['stop_receipts'], 'call_receipts': evidence['call_receipts'],
                'lifecycle_digest': entry.digest(s['lifecycle_snapshot']),
                'original_host': s['lifecycle_snapshot']['instance']}
        if data['action'] == 'validation-result' and self.stop_intent() is not None:
            data = copy.deepcopy(data)
            # Replayed transactions already contain the adapter's derived source;
            # retain and compare the original Controller input on the stop path.
            data['evidence'].pop('source', None)
            previous = s.get('deferred_validation')
            require(previous is None or previous == data, 'validation_conflict', 'retain exact late validation result')
            s['deferred_validation'] = copy.deepcopy(data)
            self.save()
            return _view(s, {'operation':'reconcile-validation-result','disposition':'stopped','attempt_id':data['evidence']['attempt_id']})
        if data["action"] in {"candidate-ready", "ticket-check", "review-converged", "validation-start", "validation-result", "validation-retry", "invalidate-candidate"}:
            self.require_business_ready(control_replay=True)
            self.require_not_stopping()
            current = handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
            require(current['actor']['thread_id'] == s['handoff']['expected_entry']['actor']['thread_id'], 'identity_mismatch', 'original controller required')
            if data['action'] == 'candidate-ready':
                checkpoint = s['control']['context']['handoff_progress']
                require(all('ticket:' + ticket['ticket_id'] in checkpoint.get('review_claims', {})
                            for ticket in execution_plan.current_catalog(s['scope_authority'])['tickets']),
                        'review_incomplete', 'Ticket acceptance is required before candidate-ready')
                data = copy.deepcopy(data)
                data['evidence']['ticket_coverage'] = self.build_review_coverage(data['evidence']['commit'], delivery=False)
            if data['action'] == 'validation-start' and self.unresolved_host_actions():
                return self.query_host()
            data = copy.deepcopy(data)
            if data['action'] == 'review-converged':
                checkpoint = s['control']['context']['handoff_progress']
                existing, applications = self.review_applications(data['evidence']['candidate'])
                slots = s.get('review_activity', {})
                native, claims = {}, {}
                for axis in ('standards', 'spec'):
                    review = data['evidence']['review'][axis]
                    retained = existing.get(axis)
                    if retained is not None and applications[axis]['applicable'] and                            retained['source_candidate'] == review['candidate'] and retained['native_stop']['response_ref'] == review['result_ref'] and                            retained['reviewer_ref'] == review['reviewer_ref']:
                        store = state_store.open_store(self.path)
                        current = store.open_current()
                        native[axis] = store.read_evidence(retained['native_stop_ref'], run_id=current.run_id)['value']['native_review_stop']
                        claims[axis] = checkpoint['review_claims'][axis]
                    else:
                        slot = slots.get(axis)
                        require(slot and slot['stopped'] and (not slot.get('query') or slot['query']['resolved']) and
                                slot['ref'] == review['reviewer_ref'] and slot['candidate'] == data['evidence']['candidate'],
                                'review_incomplete', 'both original delivery reviewers or valid inherited claims are required')
                        receipts = [r for r in slot['receipts'] if r['response_ref'] == review['result_ref'] and r['status'] == 'stopped']
                        require(len(receipts) == 1, 'review_incomplete', 'semantic result must name original stopped response')
                        native[axis] = receipts[0]
                        claims[axis] = self.accept_delivery_review(axis, review['result_ref'])
                data['evidence']['native_evidence'] = native
                original = checkpoint.get('review_claims', {})
                checkpoint['review_claims'] = {**original, **claims}
                try:
                    data['evidence']['coverage'] = self.build_review_coverage(data['evidence']['candidate'], delivery=True)
                finally:
                    checkpoint['review_claims'] = original
                data['evidence']['claims'] = claims
            if data['action'] in {'candidate-ready', 'validation-result', 'ticket-check'}:
                data['evidence']['source'] = copy.deepcopy(data['receipt'])
                if data['action'] == 'ticket-check':
                    data['evidence']['source_snapshot'] = {}
                if data['action'] == 'candidate-ready' and control.direct_implementation(s['control']['context']['handoff_progress']):
                    stop = s['host'].get('last_stop')
                    require(s['host']['status'] == 'stopped' and stop is not None and stop.get('provenance') and
                            not self.unresolved_host_actions() and not self.stop_barrier_pending(),
                            'host_evidence_missing', 'direct candidate requires saved causal native stop and complete barrier')
                    require(stop['receipt']['attempt'] == s['dispatch']['request']['attempt'] and
                            stop['receipt']['request_digest'] == s['dispatch']['request']['digest'],
                            'identity_mismatch', 'direct stop belongs to another dispatch attempt')
                    checkpoint = s['control']['context']['handoff_progress']
                    proof = {'policy_digest': control.digest(checkpoint['implementation_policy']),
                        'dispatcher_ref': self.bound_ref(), 'attempt': s['control']['context']['carrier']['attempt'],
                        'baseline': checkpoint['git_baseline_commit'], 'candidate': data['evidence']['commit'],
                        'stop': copy.deepcopy(stop), 'request_digest': s['dispatch']['request']['digest']}
                    require('direct_provenance' not in data['evidence'] or data['evidence']['direct_provenance'] == proof,
                            'identity_mismatch', 'direct provenance is derived from the original host')
                    data['evidence']['direct_provenance'] = proof
            if data['action'] == 'invalidate-candidate' and (s['control']['context'].get('handoff_progress') or {}).get('state') == 'validating':
                require(data['receipt'].get('raw',{}).get('stopped') is True, 'host_evidence_missing', 'stop and reconcile the validation command before invalidation')
                data['evidence']['stopped'] = True
            if data['action'] in {'validation-retry', 'invalidate-candidate'}:
                require(not self.unresolved_host_actions(), 'host_action_pending', 'reconcile original calls before validation recovery')
                require(all(v.get('stopped') for v in s.get('review_activity', {}).values()), 'review_active', 'stop old native reviewers before recovery')
        if data['action'] in {'local-review', 'ticket-accept'}:
            data = copy.deepcopy(data)
            require(set(data['evidence']) == {'axis', 'candidate', 'result_ref'},
                    'invalid_evidence', 'local claim is derived from its original native result')
            data['evidence']['claim'] = self.accept_local_review(data['evidence'])
            data['evidence']['claim_checkpoint'] = str(self.path)
        if data['action'] == 'review-applicability':
            data = copy.deepcopy(data)
            require(set(data['evidence']) == {'candidate', 'reference'}, 'invalid_evidence', 'derive target applicability from original claims and Git')
            data['evidence']['applicability'] = self.persist_review_applicability(data['evidence']['candidate'])
        identity = entry.digest(data)
        transactions = s.setdefault("control_transactions", {})
        previous = transactions.get(identity)
        if previous and previous.get("rejection") is not None:
            if data['action'] in {'prepare-dispatch-recovery', 'recover-dispatch'}:
                previous.setdefault('rejected_attempts', []).append(previous.pop('rejection'))
            else:
                raise control.ControlError(previous["rejection"])
        if previous and previous.get("result") is not None:
            if data['action'] == 'dispatch-recovery-intent':
                return self.recovery_view(acknowledged=True)
            if data['action'] == 'successor-ready':
                handoff_id = previous['result']['handoff_id']
                if s['control']['context']['retired_handoffs'][handoff_id]['archive_status'] == 'not-requested':
                    return self.archive_action({'action': 'archive', 'evidence': {'handoff_id': handoff_id},
                        'receipt': {'controller_ref': s['control']['context']['controller_ref'], 'handoff_transaction': identity}})
            return _view(s, {"operation": "control-effects", "result": previous["result"]}, acknowledged=True)
        self.require_controls_reconciled(identity)
        require(s['transaction'] is None and not any(slot.get('transaction') is not None for slot in s.get('allocations',{}).values()),
                'transaction_pending', 'reconcile original B/allocation transactions before control mutation')
        transactions.setdefault(identity, {"request": copy.deepcopy(data),
                                            'base_control_digest': entry.digest(s['control']),
                                            'base_revision': s['revision'],
                                            "status": s["status"], "result": None,
                                            "business_block_id": (s.get("business_block") or {}).get("id")})
        require(transactions[identity]['base_control_digest'] == entry.digest(s['control']),
                'control_context_changed', 'retain the original result without overwriting newer control authority')
        if data['action'] == 'successor-ready':
            transactions[identity]['takeover_input'] = takeover_input
        if data['action'] == 'escalate-implementation':
            transactions[identity]['escalation_input'] = escalation_input
        self.save()
        # Retain exact ledger envelope on a lost response; never promote slots by hand.
        try:
            if data["action"] == "plan-revision-activate" and s["control"]["discussion"] is None:
                result = control_git.verified_transition({"repository": s["handoff"]["binding"]["worktree"],
                    "baseline": s["control"]["context"]["handoff_progress"]["git_baseline_commit"],
                    "request": {"schema_version": 7, "action": data["action"],
                        "actor_ref": s["control"]["context"]["controller_ref"],
                        "context": s['control']['context'],
                        "evidence": data["evidence"]}})
                applied = None
            else:
                result, applied = dispatch.checkpoint(s['control'], s["handoff"], data["action"], data["evidence"])
        except control.ControlError as error:
            # This pure/Git read-only boundary definitively rejected before any
            # durable control mutation. OS/transport/ledger failures stay unknown.
            if s['control']['discussion'] is None:
                transactions[identity]['rejection'] = str(error)
                self.save()
            raise
        s["control"]["context"], s["control_receipt"] = result["context"], applied
        self.next_envelope(applied)
        transactions[identity]["result"] = {key: value for key, value in result.items() if key != 'context'}
        if data["action"] == "scope-revise" and s.get("scope_revision_transaction"):
            correction = s["scope_revision_transaction"]
            receipt = result["scope_revision"]
            correction.update(state="sealed", control_result=copy.deepcopy(receipt))
            s["scope_authority"] = corrected_scope_authority(correction, receipt)
        if data["action"] == "plan-revision-activate":
            transaction = s["plan_revision_transaction"]
            transaction["control_result"] = copy.deepcopy(result["plan_revision"])
            s["scope_authority"] = copy.deepcopy(transaction["proposed_authority"])
            transaction["state"] = "activated"
            store = state_store.open_store(self.path)
            current = store.open_current()
            s.setdefault("plan_revision_allocation_history", []).append({
                "revision_id": transaction["input"]["revision_id"],
                "allocations": {identity: {'kind': 'plan-revision-allocation-ref',
                    'allocation_digest': slot['record']['request']['attempt'],
                    'reference': store.write_object(current.run_id, 'evidence', {'plan_revision_allocation': slot})}
                    for identity, slot in s.pop('allocations', {}).items()}})
            if s.get("review_activity"):
                s.setdefault("review_history", []).append(copy.deepcopy(s.pop("review_activity")))
            s["status"] = "active"
            s["step"] = "continue"
            s["continuation_intent"] = {"kind": "plan-revision",
                "revision_id": transaction["input"]["revision_id"]}
        if data["action"] == "validation-result":
            s.pop("deferred_validation", None)
        if s["status"] == "blocked":
            s["status"] = transactions[identity]["status"] if transactions[identity]["status"] != "blocked" else s.get("blocked_from","active")
            s.pop("error", None)
        if data["action"] == "recover-dispatch":
            recovery = result['recovery']
            s['recovered_native_refs'] = sorted(set(s.get('recovered_native_refs', [])) |
                                               set(recovery['host_evidence']['stopped_refs']))
            require(len(s['recovered_native_refs']) <= 32, 'state_capacity',
                    'retained native recovery window is full')
            old_record = copy.deepcopy(s['dispatch'])
            record_body = handoff.unseal(old_record)
            launch = handoff.unseal(dispatch.stage_launch(old_record) if 'stage_input' in old_record
                                   else old_record['request'])
            s.setdefault('recovery_dispatch_history', []).append(old_record)
            launch['attempt'] = result['context']['carrier']['attempt']
            launch['payload']['dispatch_recovery'] = {k:copy.deepcopy(recovery[k]) for k in
                ('recovery_id','snapshot_digest','remaining_paths','revalidate','ownership')}
            sealed_launch = handoff.seal(launch)
            if 'stage_input' in old_record:
                store = state_store.open_store(self.path)
                current = store.open_current()
                record_body['dispatch_recovery_ref'] = store.write_object(current.run_id, 'evidence',
                    {'dispatcher_recovery': launch['payload']['dispatch_recovery']})
                sealed_launch = {key: value for key, value in sealed_launch.items() if key != 'payload'}
                sealed_launch['payload_digest'] = entry.digest(launch['payload'])
            s['dispatch'] = handoff.seal({**record_body, 'request': sealed_launch,
                'receipts':[], 'delivery':None, 'acceptance':None, 'status':'bound'})
            for ref in recovery['host_evidence']['stopped_refs']:
                s.get('native_adverse', {}).pop(ref, None)
            if s.get("business_block") and s["business_block"]["id"] == transactions[identity]["business_block_id"]:
                self.clear_business_block(data, "recover-dispatch")
                s.pop("deferred_business_recovery", None)
            s.pop("stop_requested", None)
            s.pop("error", None)
            self.revoke_host()
            s["host"]["status"] = "unknown"
            s.update(status="active", step="bound", stopped=False)
            s["recovered_request"] = self.outer.get("runner_request")
        elif s.get("stop_requested") == "cancelling" and s["stopped"]:
            remaining = [e for e in (s["control"]["context"].get("handoff_progress") or {}).get("executions", []) if not e["stopped"]]
            if not remaining:
                s["status"], s["stop_effects"] = "cancelled", []
        if data['action'] == 'escalate-implementation':
            s['continuation_intent'] = {'kind':'implementation-escalation',
                'snapshot_digest':result['context']['handoff_progress']['implementation_escalation']['snapshot_digest']}
            if escalation_paused:
                s['suspended_step'] = 'continue'
            else:
                s.update(step='continue', status='active')
        if data['action'] == 'validation-start':
            s.update(step='continue', status='active', continuation_intent={'kind':'final-validation', 'attempt_id':data['evidence']['attempt_id']})
        if data['action'] == 'dispatch-recovery-result' and s.get('transport_loss'):
            s['status'] = self.stop_intent() or 'blocked'
            s['recovery_action'] = {'operation':'await-host-recovery', **self.recovery_requirements(),
                                    'reason':s['transport_loss']['reason']}
        receipt_store, receipt_current, receipt_key = self.receipt_store(
            'control:' + data['action'], control_subject, invocation)
        membership = receipt_store.prepare_receipt(receipt_current, receipt_key,
            original_input, {key: value for key, value in result.items() if key != 'context'})
        self._commit_receipts.append(membership)
        self.remember_rework_receipt(transactions[identity], membership)
        del transactions[identity]
        self.save()
        if data['action'] == 'dispatch-recovery-result' and s.get('transport_loss'):
            return _view(s, s['recovery_action'])
        if data['action'] == 'dispatch-recovery-intent':
            return self.recovery_view(issued=bool(result['effects']))
        if data['action'] == 'escalate-implementation' and escalation_paused:
            return _view(s)
        if data['action'] in {'validation-start', 'escalate-implementation'}:
            return self.advance()
        if data['action'] == 'successor-ready' and not result.get('acknowledged'):
            return self.archive_action({'action': 'archive', 'evidence': {'handoff_id': result['handoff_id']},
                'receipt': {'controller_ref': s['control']['context']['controller_ref'], 'handoff_transaction': identity}})
        return _view(s, {"operation": "control-effects", "result": result})

    def archive_action(self, data):
        """Use immutable audit routes, but mutate only the latest original authority."""
        s = self.state
        handoff_id = data['evidence'].get('handoff_id')
        current = s['control']['context']
        identity = entry.digest(data)
        self.require_controls_reconciled(identity)
        transactions = s.setdefault('control_transactions', {})
        retired = current.get('retired_handoffs', {}).get(handoff_id)
        archive_route = None
        if retired is None and s['control']['discussion'] is not None:
            pending = transactions.get(identity)
            port = copy.deepcopy(pending['port'] if pending else s['control'])
            if pending is None:
                query = {key: value for key, value in port['discussion'].items()
                         if key not in {'expected_ledger_revision', 'expected_topic_revision', 'idempotency_key'}}
                latest = dispatch.discussion_protocol.handle({**query, 'operation': 'read-topic'})
                port['store_ref'] = latest['workflow_control_ref']
                port['discussion'].update(expected_ledger_revision=latest['ledger_revision'],
                    expected_topic_revision=latest['record_revision'], idempotency_key=str(uuid.uuid4()))
            port['context'] = read_archive_context(port, handoff_id)
            retired = port['context']['retired_handoffs'][handoff_id]
            archive_route = (port, s['handoff'])
        require(retired is not None and retired.get('handoff_completed'), 'identity_mismatch', 'exact completed handoff required')
        original = None
        for state in [s, *s.get('history', [])]:
            for transaction_id, transaction in state.get('control_transactions', {}).items():
                if transaction_id == identity:
                    require(original is None or all(original.get(key) == transaction.get(key) for key in
                            ('request', 'port', 'handoff', 'handoff_id', 'operation_id')),
                            'identity_mismatch', 'conflicting historical archive request')
                    if original is not None and original.get('result') is not None and transaction.get('result') is not None:
                        require(original['result'] == transaction['result'], 'identity_mismatch', 'conflicting historical archive result')
                    if original is None or original.get('result') is None:
                        original = transaction
        if original is not None and original.get('result') is not None:
            return _view(s, {'operation': 'control-effects', 'result': {**original['result'], 'context': current, 'effects': []}}, acknowledged=True)
        if identity not in transactions:
            if original is not None:
                transactions[identity] = copy.deepcopy(original)
                transactions[identity]['original_transaction'] = identity
            else:
                routes = [archive_route] if archive_route is not None else []
                for state in [s, *s.get('history', [])]:
                    port = state['control']
                    if port['discussion'] is not None and port['context']['controller_ref'] == retired['controller_ref'] and port['context']['topic_ref'] == retired['topic_ref']:
                        routes.append((port, state['handoff']))
                if retired['topic_ref'] is not None:
                    require(routes, 'authority_missing', 'original attached archive authority unavailable')
                    port, saved = copy.deepcopy(routes[0])
                    route_keys = ('project_path', 'project_id', 'tree_id', 'actor_topic_id', 'actor_conversation_ref')
                    require(all(all(other['discussion'][key] == port['discussion'][key] for key in route_keys) for other, _ in routes), 'identity_mismatch', 'conflicting original archive authority')
                    query = {key: value for key, value in port['discussion'].items() if key in {*route_keys, 'protocol_version'}}
                    if archive_route is None:
                        latest = dispatch.discussion_protocol.handle({**query, 'operation': 'read-topic'})
                        port['store_ref'] = latest['workflow_control_ref']
                        port['discussion'].update(expected_ledger_revision=latest['ledger_revision'],
                            expected_topic_revision=latest['record_revision'], idempotency_key=str(uuid.uuid4()))
                        port['context'] = read_archive_context(port, handoff_id)
                else:
                    port, saved = copy.deepcopy(s['control']), copy.deepcopy(s['handoff'])
                authoritative = port['context'].get('retired_handoffs', {}).get(handoff_id)
                require(authoritative is not None and all(authoritative[key] == retired[key] for key in ('controller_ref', 'topic_ref', 'carrier', 'plan', 'delivery', 'delivery_digest', 'successor')), 'identity_mismatch', 'original frozen handoff changed')
                # Archive has no Git execution lease. Retain only its original
                # authentication route and exact ledger CAS pointer, never a
                # second current context or a complete stage input.
                archive_port = {'discussion': copy.deepcopy(port['discussion'])}
                archive_handoff = {'controller_ref': saved['controller_ref']}
                if port['discussion'] is not None:
                    archive_port['store_ref'] = copy.deepcopy(port['store_ref'])
                    source_entry = handoff.attached_source_entry(saved) or saved['expected_entry']
                    archive_handoff['expected_entry'] = {
                        'discussion_project': copy.deepcopy(source_entry['discussion_project']),
                        'requirement': {'attachment': copy.deepcopy(source_entry['requirement']['attachment'])},
                        'repository': {key: copy.deepcopy(value) for key, value in source_entry['repository'].items()
                                       if key in {'kind', 'root', 'git_common_dir'}}}
                transactions[identity] = {'request': copy.deepcopy(data), 'port': archive_port,
                    'handoff': archive_handoff, 'base_revision': s['revision'],
                    'base_control_digest': entry.digest(s['control']),
                    'scope': 'archive', 'handoff_id': handoff_id, 'archive_ref': retired['carrier']['ref'],
                    'operation_id': data['evidence'].get('operation_id'),
                    'handoff_completed': True, 'intent_persisted': data['action'] == 'archive-result' and
                        any(op['operation_id'] == data['evidence'].get('operation_id') for op in authoritative['operations']), 'result': None}
        transaction = transactions[identity]
        if data['action'] == 'archive-result':
            require(data['receipt'] == data['evidence'].get('receipt'), 'host_evidence_missing', 'retain exact authenticated archive receipt')
        elif 'call_lookup' in data['evidence']:
            require(data['receipt'] == data['evidence']['call_lookup'], 'host_evidence_missing', 'retain authenticated original invocation lookup')
        execution_port = copy.deepcopy(transaction['port'])
        if execution_port['discussion'] is None:
            execution_port['context'] = copy.deepcopy(current)
        else:
            execution_port['context'] = read_archive_context(execution_port, handoff_id)
        # Plan through the same pure archive transition before any ledger write
        # or executable effect. The original operation ID is independent of C's
        # receipt key and remains fixed across lost responses and exact lookup.
        preview_context = copy.deepcopy(execution_port['context'])
        preview = control.archive_transition(preview_context, data['action'], data['evidence'])
        preview_retired = preview_context['retired_handoffs'][handoff_id]
        effects = preview['effects']
        operation_id = (effects[0]['operation_id'] if effects else
                        data['evidence'].get('operation_id'))
        operation = next((item for item in preview_retired['operations']
                          if item['operation_id'] == operation_id), None)
        store = state_store.open_store(self.path)
        reserve_owner = bool(effects)
        if operation is not None:
            reserve_action = store.receipt_key(store.open_current(), actor=current['controller_ref'],
                kind='archive:' + operation['kind'], subject=handoff_id, invocation=operation_id)
            recovery = operation['kind'] == 'read-archive-state'
            original_operation = next((item for item in execution_port['context']['retired_handoffs'][handoff_id]['operations']
                                       if item['operation_id'] == operation_id), None)
            reserve_owner = reserve_owner and (original_operation is None or transaction.get('reserve_owner') is True)
            if original_operation is None:
                store.reserve(reserve_action, recovery=recovery)
            elif (original_operation['status'] in {'intent', 'issued', 'unknown'} and
                  execution_port['context']['retired_handoffs'][handoff_id]['archive_status'] not in {'archived', 'not-archived'}):
                store.require_reserve(reserve_action, recovery=recovery)
            else:
                operation = None  # Retaining a late conflict is recovery only.
        if operation is None:
            invocation = (data['receipt'].get('reference') or data['receipt'].get('call_ref') or
                          data['receipt'].get('operation_id') or entry.digest(data['receipt']))
            reserve_action = store.receipt_key(store.open_current(), actor=current['controller_ref'],
                kind='control:' + data['action'], subject=handoff_id, invocation=invocation)
            recovery, reserve_owner = True, False
            store.reserve(reserve_action, recovery=True)
        if transaction.get('reserve_action') is not None:
            require(transaction['reserve_action'] == reserve_action and
                    transaction['reserve_recovery'] == recovery,
                    'identity_mismatch', 'archive replay changed its original reservation')
        else:
            transaction.update(reserve_action=reserve_action, reserve_recovery=recovery,
                               reserve_owner=reserve_owner, operation_id=operation_id,
                               reserve_kind='archive:' + operation['kind'] if operation is not None else 'control:' + data['action'])
            # Measure the bounded completion through the production serializer.
            # Keeping every pending pin is conservative relative to settlement;
            # two root budgets also cover the finite ledger pointer/proof delta.
            with store.measure_writes(sequence=True) as writes:
                candidate = Progress(self.path, copy.deepcopy(self.outer))
                candidate._authority_revision = self._authority_revision
                if execution_port['discussion'] is None:
                    candidate.state['control']['context']['retired_handoffs'][handoff_id] = preview_retired
                candidate.state['control_transactions'][identity]['intent_persisted'] = bool(operation_id)
                invocation = (data['receipt'].get('reference') or data['receipt'].get('call_ref') or
                              data['receipt'].get('operation_id') or entry.digest(data['receipt']))
                receipt_current = store.open_current()
                receipt_key = store.receipt_key(receipt_current, actor=current['controller_ref'],
                    kind='control:' + data['action'], subject=handoff_id, invocation=invocation)
                prepared = store.prepare_receipt(receipt_current, receipt_key, data,
                    {key: value for key, value in preview.items() if key != 'context'})
                candidate.save()
                measured_current = store.open_current()
                store.write_object(measured_current.run_id, 'root',
                    {**measured_current.root, 'receipts': [prepared]})
                require(sum(writes.values()) + 2 * state_store.ROOT_LIMIT <= state_store.EVIDENCE_LIMIT,
                    'state_capacity', 'archive completion exceeds its original receipt reserve')
            # New business intent must fit ordinary capacity. Receipt, stop and
            # exact query saves may use their own independent reserved space.
            if recovery or data['action'] == 'archive-result':
                self._receipt_action = reserve_action
            self.save()
        self._receipt_action = reserve_action
        store.require_reserve(reserve_action, recovery=recovery)
        result, applied = dispatch.checkpoint(execution_port, transaction['handoff'], data['action'], data['evidence'])
        if transaction['port']['discussion'] is None:
            updated = result['context']['retired_handoffs'][handoff_id]
        else:
            updated = read_archive_context({**transaction['port'], 'store_ref': result['store_ref']},
                handoff_id)['retired_handoffs'][handoff_id]
            if s['control'].get('store_ref') == transaction['port']['store_ref']:
                require(result['context'] == current, 'control_context_changed',
                        'archive cannot replace current ledger execution facts')
                self.next_envelope(applied)
        # A retired business port can never replace current execution authority.
        # Ledger mirrors retain only their exact authoritative current pointer.
        if s['control']['discussion'] is None:
            current.setdefault('retired_handoffs', {})[handoff_id] = copy.deepcopy(updated)
        frozen_result = {key: value for key, value in result.items() if key != 'context'}
        invocation = (data['receipt'].get('reference') or data['receipt'].get('call_ref') or
                      data['receipt'].get('operation_id') or entry.digest(data['receipt']))
        store, receipt_current, key = self.receipt_store('control:' + data['action'], handoff_id, invocation)
        self._commit_receipts.append(store.prepare_receipt(receipt_current, key, data, frozen_result))
        # The exact request/result and receipt membership are committed with
        # this current projection. Completed routes do not occupy active memory.
        settled = updated['archive_status'] in {'archived', 'not-archived'}
        for pending_id, pending in list(transactions.items()):
            if pending.get('scope') != 'archive' or pending.get('handoff_id') != handoff_id:
                continue
            original_operation = next((item for item in updated['operations']
                if item['operation_id'] == pending.get('operation_id')), None)
            known_no_write = (original_operation is not None and original_operation['status'] == 'failed' and
                original_operation['kind'] == 'archive' and
                original_operation['receipt']['receipt']['no_write'])
            if settled or known_no_write or not pending.get('reserve_owner'):
                del transactions[pending_id]
            else:
                pending['intent_persisted'] = True
                if pending['port']['discussion'] is not None:
                    pending['port']['store_ref'] = copy.deepcopy(result['store_ref'])
        self.save()
        return _view(s, {'operation': 'control-effects', 'result': {**frozen_result, 'context': current}})

    def recovery_view(self, *, issued=False, acknowledged=False):
        recovery = self.state['control']['context']['handoff_progress']['recovery']
        if recovery['state'] == 'activated':
            return _view(self.state, {'operation':'wait-host', 'ref':self.bound_ref()}, acknowledged=acknowledged)
        operation = ('invoke-recovery-host' if issued else 'lookup-recovery-host') if recovery['state'] in {
            'dispatch-pending', 'dispatch-unknown'} else 'activate-dispatch-recovery' if recovery['state'] == 'ready' else 'prepare-dispatch-recovery'
        launch = (dispatch.stage_launch(self.state['dispatch']) if 'stage_input' in self.state['dispatch']
                  else self.state['dispatch']['request']) if recovery['intent'] is not None else None
        request = None if recovery['intent'] is None else {**recovery['intent'],
            'checkpoint':str(self.path), 'handoff_field':KEY + '.handoff', 'control_field':KEY + '.control',
            'registration_input':copy.deepcopy(launch['payload']['registration_input'])}
        return _view(self.state, {'operation': operation, 'recovery_id': recovery['recovery_id'],
            'request': request, 'receipts': recovery['receipts'], 'write_authority': False}, acknowledged=acknowledged)

    def allocation_raw_lookup(self, identity, slot):
        source = slot['raw_ingress']
        require(source['binding']['action_id'] == slot['action']['action_id'] and
                source['binding']['allocation_id'] == identity and
                source['binding']['receipt']['request_digest'] == slot['record']['request']['digest'],
                'identity_mismatch', 'large allocation receipt changed its original call')
        if source.get('evidence_ref') is not None:
            store = state_store.open_store(self.path)
            manifest = store.read_raw_manifest(source['evidence_ref'], run_id=store.open_current().run_id)
            require(manifest['native_receipt'] == source['binding'] and
                    (manifest['size'], manifest['sha256']) == (source['size'], source['sha256']),
                    'invalid_checkpoint', 'allocation raw manifest differs from its original receipt')
        if source.get('result_ref') is not None:
            self.allocation_raw_result(source)
        return _view(self.state, {'operation': 'lookup-exact-allocation', 'allocation_id': identity,
            'request': slot['record']['request'], 'checkpoint': str(self.path)})

    def allocation_raw_result(self, source):
        store = state_store.open_store(self.path)
        current = store.open_current()
        value = store.read_evidence(source['result_ref'], run_id=current.run_id)['value']
        expected = {'status': 'unknown', 'action_id': source['binding']['action_id'],
            'observation_digest': source['binding']['observation_digest'], 'evidence_ref': source['evidence_ref'],
            'authority_revision': source['authority_revision']}
        require(type(source['authority_revision']) is int and 0 < source['authority_revision'] <= current.revision and
                value == {'allocation_result': expected}, 'invalid_checkpoint',
                'original allocation unknown result changed its committed evidence')
        return value['allocation_result']

    def retain_oversized_allocation(self, identity, slot, data, measurement):
        # The original invocation remains unbound even if every raw chunk fits.
        # Persist its finite identity before capture; never put the external body
        # through ordinary evidence encoding or a control transaction.
        binding = {'allocation_id': identity, 'action_id': slot['action']['action_id'],
            'operation': data['operation'], 'receipt': {key: value for key, value in data['receipt'].items() if key != 'raw'},
            'observation_digest': state_store.raw_measure(data)[1]}
        source = {'binding': binding, 'size': measurement[0], 'sha256': measurement[1],
                  'status': 'unknown', 'reason': 'atomic_limit_exceeded', 'resolved': False}
        slot['raw_ingress'] = source
        self.save()
        store = state_store.open_store(self.path, receipt_action=self._receipt_action)
        current = store.open_current()
        interruption = None
        try:
            reference, manifest = store.write_raw_evidence(current.run_id, data['receipt']['raw'],
                measurement=measurement, commit_bytes=0, native_receipt=binding)
        except state_store.RawEvidenceInterrupted as error:
            reference = error.reference
            manifest = store.read_raw_manifest(reference, run_id=current.run_id)
            interruption = error
        source['evidence_ref'] = reference
        if not manifest['complete']:
            source['reason'] = manifest['reason']
        if reference not in slot['observations']:
            slot['observations'].append(reference)
        # This finite result becomes authoritative only with the same root CAS
        # that pins it. A later lookup must not rewrite its original outcome.
        source['authority_revision'] = self._authority_revision + 1
        source['result_ref'] = store.write_object(current.run_id, 'evidence', {'allocation_result': {
            'status': 'unknown', 'action_id': binding['action_id'],
            'observation_digest': binding['observation_digest'], 'evidence_ref': reference,
            'authority_revision': source['authority_revision']}})
        self.save()
        if interruption is not None:
            raise interruption
        return self.allocation_raw_lookup(identity, slot)

    def allocation(self, data):
        """B transport records share the dispatcher's existing control roster."""
        entry.fields(data, {"allocation_id", "operation"}, {"handoff", "receipt", "result", "decision", "invocation_id"})
        identity = entry.nonempty(data["allocation_id"])
        operation = data["operation"]
        require(operation in {"prepare", "repair", "bind", "reconcile", "receive", "accept"}, "invalid_operation", "unknown allocation operation")
        s = self.state
        prior_slot = s.get('allocations', {}).get(identity)
        inherited_slot = False
        if operation == 'repair' and prior_slot is None:
            checkpoint = s['control']['context']['handoff_progress']
            carried = {item['allocation_digest']: item for item in checkpoint.get('carried_results', [])}
            candidates = [group['allocations'][identity] for group in s.get('plan_revision_allocation_history', [])
                if identity in group['allocations'] and
                (group['allocations'][identity].get('allocation_digest')
                 if group['allocations'][identity].get('kind') == 'plan-revision-allocation-ref' else
                 (group['allocations'][identity].get('record') or {}).get('request', {}).get('attempt')) in carried]
            require(len(candidates) == 1, 'allocation_missing', 'repair requires one original carried allocation')
            prior_slot = copy.deepcopy(plan_revision_allocation_slot(s, candidates[0]))
            inherited_slot = True
        proposal = prior_slot.get('repair_proposal') if prior_slot else None
        require(proposal is None or operation in {'repair', 'prepare'}, 'outcome_unknown',
                'reconcile the original repair preparation before another allocation operation')
        if operation == 'prepare' and prior_slot is None:
            require(not self.ticket_checks_due(), 'ticket_check_required',
                'run current affected/Ticket checks before another execution dispatch')
        if operation in {'bind', 'reconcile', 'receive'} and prior_slot and prior_slot.get('record'):
            launch = prior_slot['record']['request']
            require(data['receipt'].get('attempt') == launch['attempt'] and
                    data['receipt'].get('request_digest') == launch['digest'],
                    'attempt_mismatch', 'receipt belongs to a previous execution invocation')
        if operation in {'prepare', 'repair'}:
            self.require_business_ready()
        if operation == 'repair':
            entry.fields(data, {'allocation_id', 'operation', 'handoff', 'invocation_id'})
            self.require_allocation_active()
            invocation_id = entry.nonempty(data['invocation_id'])
            require(len(invocation_id.encode()) <= 4096 and prior_slot is not None,
                    'allocation_missing', 'repair requires a bounded invocation and original allocation')
            previous_repair = prior_slot['handoff']['semantic'].get('repair')
            request = copy.deepcopy(data['handoff'])
            require('repair' not in request['semantic'], 'invalid_request', 'Controller derives repair provenance')
            if proposal is not None:
                proposed = proposal['slot']
                require(proposed['handoff']['semantic']['repair']['invocation_id'] == invocation_id,
                        'outcome_unknown', 'retain the original proposed repair invocation')
                request['semantic']['repair'] = copy.deepcopy(proposed['handoff']['semantic']['repair'])
                require(entry.digest(request) == proposed['input_digest'], 'allocation_conflict',
                        'reconcile the exact proposed repair before changing its input')
            elif previous_repair is not None and previous_repair['invocation_id'] == invocation_id:
                request['semantic']['repair'] = copy.deepcopy(previous_repair)
            else:
                require(prior_slot.get('transaction') is None and prior_slot.get('record') is not None and
                        prior_slot['record']['status'] in {'received', 'accepted'},
                        'outcome_unknown', 'settle the original execution before repair')
                if inherited_slot:
                    original_record = handoff.unseal(prior_slot['record'])
                    dispatch.verified_launch(original_record)
                    dispatch.verify_allocation_receipts(original_record)
                    carry = carried[original_record['request']['attempt']]
                    original = control.repair_execution_record(checkpoint, carry['agent_ref'])
                    require(original['allocation_digest'] == original_record['request']['attempt'] and
                            original['task_id'] == carry['task_id'],
                            'identity_mismatch', 'carried allocation differs from its original native request')
                    ref, state = original['agent_ref'], original['state']
                else:
                    original_record = dispatch.record_for({'record': prior_slot['record'], 'control': copy.deepcopy(s['control'])})
                    ref, state, original = dispatch.matching(original_record, s['control']['context'])
                stops = [receipt for receipt in original_record['receipts']
                         if receipt['status'] == 'stopped' and receipt['ref'] == ref]
                require(ref is not None and original['stopped'] is True and state in {'received', 'accepted'} and stops,
                        'host_evidence_missing', 'repair requires the original authenticated stopped result')
                repair = {'agent_ref': ref, 'allocation_digest': original['allocation_digest'],
                    'stop_ref': stops[-1]['receipt_ref'], 'invocation_id': invocation_id}
                request['semantic']['repair'] = repair
                require(request['scope']['owned_paths'] == original['paths'] and
                        request['scope']['protected_paths'] == original['read_only'] and
                        request['semantic']['plan_refs'] == original['plan_refs'],
                        'scope_changed', 'repair keeps the original slice, reads and plan refs')
                try:
                    selection = control.select_configuration(request['configuration'])
                except control.ControlError as error:
                    raise entry.PreparationError('configuration_changed',
                        'repair configuration selection is invalid; retain the original invocation') from error
                require(selection == original['configuration'], 'configuration_changed',
                        'repair must retain the original normalized configuration')
                store = state_store.open_store(self.path)
                current = store.open_current()
                history = list(prior_slot.get('invocation_history', []))
                require(len(history) < 32, 'state_capacity', 'execution invocation history window is full')
                for reference in history:
                    historical = store.read_evidence(reference, run_id=current.run_id,
                        maximum=state_store.SUBJECT_LIMIT)['value']['execution_invocation']
                    require(historical['handoff']['semantic'].get('repair', {}).get('invocation_id') != invocation_id,
                            'allocation_conflict', 'repair invocation identity was already used')
                history.append(store.write_object(current.run_id, 'evidence', {'execution_invocation': prior_slot}))
                # Keep the accepted current slot until full B/Git admission and
                # authority intake complete. A proposed input carries no release.
                saved = handoff.prepare(request, allocation_store=(store, current, identity))
                proposed = {'input_digest': entry.digest(request), 'handoff': saved,
                    'record': None, 'owner_ref': self.bound_ref(), 'transaction': None,
                    'observations': [], 'result': None, 'invocation_history': history,
                    'metrics': copy.deepcopy(prior_slot.get('metrics', {'slice_id': identity, 'submissions': 0}))}
                prior_slot['repair_proposal'] = {'phase': 'validating', 'slot': proposed}
                if inherited_slot:
                    s.setdefault('allocations', {})[identity] = prior_slot
            data = {'allocation_id': identity, 'operation': 'prepare', 'handoff': request}
            operation = 'prepare'
        invocation = ((data.get('receipt') or {}).get('receipt_ref') or
                      (data.get('decision') or {}).get('reference'))
        if invocation is not None:
            store, current, receipt_key = self.receipt_store('allocation:' + operation, identity, invocation)
            committed = store.read_receipt(current, receipt_key, data)
            if committed is not None:
                return _view(s, {'operation': 'allocation-result', 'allocation_id': identity,
                    'result': committed, 'write_authority': False, 'checkpoint': str(self.path)}, acknowledged=True)
        plan_state = (s.get("plan_revision_transaction") or {}).get("state")
        if plan_state in {"prepared", "planning-committed"} and operation in {"bind", "reconcile", "receive", "accept"}:
            s.setdefault("plan_revision_late_allocations", []).append(copy.deepcopy(data))
            if isinstance(data.get("receipt"), dict) and data["receipt"].get("ref"):
                s.setdefault("native_adverse", {})[data["receipt"]["ref"]] = copy.deepcopy(data)
            s["lifecycle_snapshot"] = None
            self.save()
            return _view(s, {"operation": "audit-late-allocation", "allocation_id": identity,
                             "revision_id": s["plan_revision_transaction"]["input"]["revision_id"]})
        require(plan_state in (None, "activated") or
                plan_state == "stopping" and operation in {"bind", "reconcile", "receive"},
                "plan_revision_pending", "new allocation or acceptance is closed during plan revision")
        require(s["stage"] == 3 and s["handoff"]["role"] == "implementation-dispatcher", "role_mismatch", "allocation belongs to the Stage-3 dispatcher")
        self.require_controls_reconciled()
        slots = s.setdefault("allocations", {})
        slot = slots.get(identity)
        intent_slot = slot
        proposal = slot.get('repair_proposal') if slot else None
        if proposal is not None:
            slot = proposal['slot']
        observation_ref = None
        if operation == "prepare":
            entry.fields(data, {"allocation_id", "operation", "handoff"})
            if slot is not None:
                require(slot["input_digest"] == entry.digest(data["handoff"]) or
                        intent_slot.get('transaction') is not None and data['handoff'] == slot['handoff'],
                        "allocation_conflict", "retain the original allocation intent")
                if slot["record"] is not None:
                    if slot.get("action") is None:
                        self.require_allocation_active()
                        checkpoint_path = (str(self.path.resolve())
                            if 'integration_recovery' in slot['handoff']['authorization'] and
                               handoff.resolve_allocation(slot['handoff']).get('readiness') is None else None)
                        handoff.verify(slot["handoff"], checkpoint_path=checkpoint_path)
                        return self.issue_allocation(identity, slot)
                    if slot["record"]["status"] in {"received", "accepted", "not-created"}:
                        return _view(s, {"operation": "allocation-result", "allocation_id": identity, "result": slot["result"]}, acknowledged=True)
                    return _view(s, {"operation": "lookup-exact-allocation", "allocation_id": identity,
                                     "request": slot["record"]["request"], "checkpoint": str(self.path)}, acknowledged=True)
            else:
                self.require_allocation_active()
                request = copy.deepcopy(data["handoff"])
                require(request["role"] == "execution-agent" and request["stage"] == 3, "role_mismatch", "execution-agent input required")
                request.setdefault("requirement", s["handoff"]["requirement"])
                request.setdefault("predecessor", None)
                request.setdefault("binding", s["handoff"]["binding"])
                request.setdefault("target", s["handoff"]["target"])
                request.setdefault("delivery", s["handoff"]["delivery"])
                parent_scope = s["handoff"]["scope"]
                current_readiness = (consumed_scope_readiness(s, self.path)
                                     if "readiness" in s["handoff"] else None)
                if "readiness" in s["handoff"]:
                    require(current_readiness is not None, "scope_changed",
                            "current Stage-3 plan authority is unavailable")
                current_scope = (current_readiness["scope_authority"]["scope"]
                                 if current_readiness is not None else
                                 s["scope_authority"]["scope"] if (s.get("scope_authority") or {}).get("kind") == "inline"
                                 else parent_scope)
                manifest_base = current_scope.get("baseline", s["handoff"].get(
                    "manifest_baseline", parent_scope["baseline"]))
                require("manifest_baseline" not in request or
                        request["manifest_baseline"] == manifest_base,
                        "scope_changed", "allocation must retain the sealed manifest baseline")
                request["manifest_baseline"] = manifest_base
                if 'integration_recovery' in s['handoff']['authorization']:
                    request['authorization']['integration_recovery'] = copy.deepcopy(s['handoff']['authorization']['integration_recovery'])
                if "readiness" in s["handoff"]:
                    require("readiness" not in request or
                            request["readiness"] == current_readiness,
                            "scope_changed", "allocation readiness differs from its Dispatcher")
                    request["readiness"] = copy.deepcopy(current_readiness)
                    require("generated_outputs" not in request, "scope_changed",
                            "inherited generated inventory belongs to the sealed authority")
                else:
                    generated = (s["scope_authority"].get("generated_outputs", {})
                                 if (s.get("scope_authority") or {}).get("kind") == "inline"
                                 else s["handoff"].get("generated_outputs", {}))
                    require("generated_outputs" not in request or request["generated_outputs"] == generated,
                            "scope_changed", "allocation generated inventory differs from its Dispatcher")
                    if generated:
                        request["generated_outputs"] = copy.deepcopy(generated)
                    if "inline_authority" in s["handoff"]:
                        inline = s["scope_authority"]
                        require("inline_authority" not in request or
                                request["inline_authority"] == inline,
                                "execution_plan_stale", "standalone allocation changed its inline authority")
                        request["inline_authority"] = copy.deepcopy(inline)
                        request["semantic"].setdefault("inline_plan", copy.deepcopy(
                            execution_plan.current_catalog(inline)["tickets"][0]["plan"]))
                require(request["binding"] == s["handoff"]["binding"] and request["target"] == s["handoff"]["target"],
                        "binding_changed", "allocation must retain its dispatcher's flow")
                retain_entry_pins(request, s["packages"])
                actor = request["expected_entry"]["actor"]
                require(actor["role"] == "implementation-dispatcher" and self.bound_ref() in
                        {actor["thread_id"], "codex-thread:" + actor["thread_id"], actor.get("actor_ref")},
                        "identity_mismatch", "only the bound native dispatcher allocates execution agents")
                inline_recovery = ('integration_recovery' in request['authorization'] and
                                   request.get('readiness') is None)
                saved = handoff.prepare(request,
                    checkpoint_path=str(self.path.resolve()) if inline_recovery else None,
                    allocation_store=(state_store.open_store(self.path),
                        state_store.open_store(self.path).open_current(), identity))
                resolved = handoff.resolve_allocation(saved)
                parent_scope, scope = s["handoff"]["scope"], resolved["scope"]
                require(scope["implementation_paths"] == current_scope["implementation_paths"] and
                        scope["closure_paths"] == current_scope["closure_paths"] and
                        scope["downstream"] == current_scope["downstream"] and
                        set(scope["owned_paths"]) <= set(current_scope["implementation_paths"]) and
                        set(parent_scope["protected_paths"]) <= set(scope["protected_paths"]),
                        "scope_changed", "allocation must retain parent scope and protections")
                handoff.ancestor(resolved['expected_entry']['repository']['root'], parent_scope['baseline'], scope['baseline'])
                slot = {"input_digest": entry.digest(data["handoff"]), "handoff": saved, "record": None, "owner_ref": self.bound_ref(),
                        "transaction": None, "observations": [], "result": None,
                        'metrics': {'slice_id': identity, 'submissions': 0}}
                slots[identity] = slot
                self.save()
        else:
            require(slot is not None and slot["record"] is not None, "allocation_missing", "exact saved allocation required")
            required = {"allocation_id", "operation", "decision"} if operation == "accept" else {"allocation_id", "operation", "receipt"}
            if operation == "receive": required.add("result")
            entry.fields(data, required)
            if operation != 'accept':
                source = slot.get('raw_ingress')
                if source is not None and source['binding']['receipt']['receipt_ref'] == data['receipt']['receipt_ref']:
                    require(source['binding']['observation_digest'] == state_store.raw_measure(data)[1],
                            'allocation_conflict', 'original large receipt cannot name another response')
                    if source.get('result_ref') is not None:
                        lookup = self.allocation_raw_lookup(identity, slot)
                        if source['resolved']:
                            return _view(s, {'operation': 'allocation-result', 'allocation_id': identity,
                                'result': self.allocation_raw_result(source),
                                'result_ref': source['result_ref']}, acknowledged=True)
                        lookup['acknowledged'] = True
                        return lookup
                elif source is not None and not source['resolved']:
                    require(operation == 'reconcile', 'outcome_unknown', 'look up the original allocation call')
                try:
                    require(not (slot['record']['status'] == 'not-created' and data['receipt'].get('ref') is not None),
                            'attempt_ended', 'a late ref cannot bind a proven non-created allocation')
                    dispatch.validate_allocation_receipt(slot['record'], operation, data['receipt'],
                        s['control']['context'], data.get('result'))
                    if slot['transaction'] is not None:
                        require(slot['transaction']['intent_id'] == entry.digest([identity, operation, data]),
                                'allocation_conflict', 'receipt must retain the original pending allocation request')
                    action = slot.get('action')
                    if action is not None:
                        require(action['operation'] == 'invoke-host' and action['allocation_id'] == identity and
                                action['request_digest'] == data['receipt']['request_digest'],
                                'identity_mismatch', 'receipt must name the original persisted host action')
                except entry.ERROR_TYPES:
                    # Invalid evidence may use ordinary audit space only. Never
                    # let its claimed attempt borrow a legitimate call reserve.
                    self._receipt_action = None
                    audit_store = state_store.open_store(self.path)
                    audit_store.receipt_action = None
                    audit_root = audit_store.open_current()
                    slot['observations'].append(audit_store.write_object(audit_root.run_id,
                        'evidence', {'allocation_observation': data}))
                    self.save()
                    raise
                if action is not None:
                    self._receipt_action = action['action_id']
                if operation == 'bind' and action is not None:
                    measurement = state_store.raw_measure(data['receipt']['raw'])
                    if measurement[0] > state_store.EVIDENCE_LIMIT:
                        return self.retain_oversized_allocation(identity, slot, data, measurement)
            observation_store = state_store.open_store(self.path, receipt_action=self._receipt_action)
            observation_root = observation_store.open_current()
            observation_ref = observation_store.write_object(
                observation_root.run_id, 'evidence', {'allocation_observation': data})
            if observation_ref not in slot['observations']:
                slot["observations"].append(observation_ref)
                self.save()
        if operation == "prepare":
            self.require_allocation_active()
        request = {"protocol": handoff.PROTOCOL, "operation": operation}
        if 'integration_recovery' in slot['handoff']['authorization'] and handoff.resolve_allocation(slot['handoff']).get('readiness') is None:
            request['scope_checkpoint_path'] = str(self.path.resolve())
        if operation == "prepare":
            request["handoff"] = slot["handoff"]
        else:
            request["record"] = slot["record"]
            request.update({key: data[key] for key in ("receipt", "result", "decision") if key in data})
            if operation == "accept":
                entry.fields(data["decision"], {"reference"}, {"delivery_digest"})
                require(slot["record"]["delivery"] is not None, "result_incomplete", "receive this allocation's result first")
                request["decision"] = {"delivery_digest": slot["record"]["delivery"]["digest"], **data["decision"]}
        # These are the same B operations/authority, not an alternate allocator.
        if intent_slot is None:
            intent_slot = slot
        if intent_slot['transaction'] is None:
            intent_slot["transaction"] = {"operation": operation, "intent_id": entry.digest([identity, operation, data]),
                'request_digest': entry.digest(request),
                'observation_ref': observation_ref,
                'base_control_digest': entry.digest(s['control']), 'base_revision': s['revision']}
            self.save()
        else:
            require(intent_slot['transaction']['request_digest'] == entry.digest(request) and
                    intent_slot['transaction']['base_control_digest'] == entry.digest(s['control']),
                    'allocation_conflict', 'reconcile the original allocation intent before another request')
        transaction = intent_slot['transaction']
        prepared_request = {**request, 'control': copy.deepcopy(s['control']),
            **({'allocation_intent': transaction['intent_id']}
               if slot['handoff'].get('kind') == 'allocation-input' else {})}
        if proposal is not None and proposal['phase'] == 'validating':
            try:
                preview = dispatch.handle(prepared_request, validate_only=True)
                if 'allocation' in preview['record']:
                    dispatch.allocation_launch(preview['record'])
            except entry.ERROR_TYPES + (control.ControlError,):
                # This phase performs no B/ledger authority mutation. A known
                # validation failure can discard only its proposed C input.
                intent_slot.pop('repair_proposal')
                intent_slot['transaction'] = None
                self.save()
                raise
            proposal['phase'] = 'applying'
            self.save()
        previous_status = slot['record']['status'] if slot.get('record') is not None else None
        # After this call starts, an exception may hide an applied ledger result.
        # Retain the original transaction/proposal and reconcile it exactly.
        result = dispatch.handle(prepared_request)
        if plan_state == "stopping":
            # B still authenticates the outstanding original native bind/result,
            # but its ordinary ready receipt cannot release an old executor
            # after the Controller has closed allocation authority.
            result = copy.deepcopy(result)
            result.pop("write_release", None)
        if "checkpoint" in result:
            s["control"]["context"] = result["checkpoint"]["context"]
            s["control_receipt"] = result["checkpoint"].get("discussion_receipt")
            self.next_envelope(s["control_receipt"])
        slot["record"] = result.get("record", slot["record"])
        if operation == 'receive' and result.get('status') == 'received' and previous_status not in {'received', 'accepted'}:
            metrics = slot.setdefault('metrics', {'slice_id': identity, 'submissions': 0})
            metrics['submissions'] += 1
            execution = control.execution_record(s['control']['context']['handoff_progress'], data['receipt']['ref'])
            metrics.setdefault('first_submission', {'sequence': 1, 'attempt': slot['record']['request']['attempt'],
                'source_commit': execution['git_snapshot']['head'],
                'receipt_ref': data['receipt']['receipt_ref'], 'duration_seconds': 'unknown'})
        if operation == 'reconcile' and slot.get('raw_ingress') and result['status'] in {'bound', 'not-created'}:
            slot['raw_ingress']['resolved'] = True
        # The slot owns its input once; the record pins the immutable digest/refs.
        # The compact allocation descriptor is small and contains no expanded contract.
        # Context is a single current authority update, never an audit copy in
        # each allocation result. The sealed record already has its own slot.
        slot["transaction"], slot["result"] = None, {
            key: value for key, value in result.items() if key not in {'checkpoint', 'record', 'observation', 'result'}}
        if 'observation' in result or 'result' in result:
            slot['result']['observation_ref'] = observation_ref
        if proposal is not None:
            slots[identity] = slot
        if (data.get('receipt') or {}).get('status') == 'stopped':
            s.get('native_adverse', {}).pop(data['receipt']['ref'], None)
        if s.get("stop_requested") == "cancelling" and s["stopped"]:
            if all(item["stopped"] for item in s["control"]["context"]["handoff_progress"]["executions"]):
                s["status"], s["stop_effects"] = "cancelled", []
        if invocation is not None:
            store, current, receipt_key = self.receipt_store('allocation:' + operation, identity, invocation)
            readonly_result = {key: value for key, value in slot['result'].items() if key != 'write_release'}
            self._commit_receipts.append(store.prepare_receipt(current, receipt_key, data, readonly_result))
        self.save()
        if self.stop_intent() is not None and s['stopped']:
            self.advance_stop(self.stop_intent())
        if operation == "prepare":
            require(slot["record"] is not None, "outcome_unknown", "recover the original allocation reservation")
            return self.issue_allocation(identity, slot)
        else:
            action = {"operation": "allocation-result", "allocation_id": identity, "result": result,
                      "checkpoint": str(self.path)}
            if operation == "accept":
                action["required_ticket_checks"] = self.ticket_checks_due()
        return _view(s, action, acknowledged=result.get("acknowledged", False))

    def issue_allocation(self, identity, slot):
        self.require_allocation_active()
        launch = dispatch.allocation_launch(slot['record']) if 'allocation' in slot['record'] else slot['record']['request']
        if 'allocation' in slot['record']:
            body = handoff.unseal(slot['record'])
            body['request'] = {key: value for key, value in launch.items() if key != 'payload'}
            body['request']['payload_digest'] = entry.digest(launch['payload'])
            slot['record'] = handoff.seal(body)
        action = {"action_id": str(uuid.uuid4()), "operation": "invoke-host", "allocation_id": identity,
                  "request_digest": launch['digest'], "checkpoint": str(self.path)}
        state_store.open_store(self.path).reserve(action['action_id'])
        slot["action"] = action
        self.save()
        reading = handoff.model_input(launch['payload'],
            read_totals=state_store.open_store(self.path).read_totals)['reading']
        return _view(self.state, {**action, 'payload': launch, 'reading': reading})

    def require_allocation_active(self):
        self.require_not_stopping()
        self.require_finalizations_released()

    def review_dependency_axes(self, axis):
        catalog = execution_plan.current_catalog(self.state['scope_authority'])
        if axis in {'standards', 'spec'}:
            return ['ticket:' + ticket['ticket_id'] for ticket in catalog['tickets']]
        ticket, group, steps = review_evidence.subject(catalog, axis)
        if axis.startswith('ticket:'):
            dependencies = ['local:' + ticket['ticket_id'] + ':' + item['id'] for item in ticket['plan']['behavior_slices']]
        else:
            deps = {step_id for step in steps for step_id in step.get('depends_on', [])} - set(group['step_ids'])
            dependencies = ['local:' + other['ticket_id'] + ':' + item['id'] for other in catalog['tickets']
                for item in other['plan']['behavior_slices'] if set(item['step_ids']) & deps]
        dependencies += ['ticket:' + identity for identity in ticket['plan']['blocked_by']]
        return sorted(set(dependencies))

    def review_dependencies(self, axis):
        views = self.state['control']['context']['handoff_progress'].get('review_claims', {})
        axes = self.review_dependency_axes(axis)
        return sorted(views[key]['claim_id'] for key in axes if key in views), any(key not in views for key in axes)

    def review_scope(self, axis, projection=None):
        catalog = execution_plan.current_catalog(self.state['scope_authority'])
        dependencies, missing = self.review_dependencies(axis)
        if axis in {'standards', 'spec'}:
            return {'layer': 'delivery', 'mode': 'cross-ticket-and-coverage', 'axis': axis,
                'ticket_ids': [ticket['ticket_id'] for ticket in catalog['tickets']],
                'requirement_refs': catalog['requirement_refs'],
                'ac_refs': sorted({ac for ticket in catalog['tickets'] for group in ticket['plan']['behavior_slices'] for ac in group['ac_refs']}),
                'dependency_claim_ids': dependencies, 'read_paths': sorted({p for ticket in catalog['tickets'] for group in ticket['plan']['behavior_slices'] for p in group['read_only']}),
                'shared_state': sorted({p for ticket in catalog['tickets'] for group in ticket['plan']['behavior_slices'] for p in group['shared_state']}),
                'integration_delta_paths': [], 'missing_dependencies': missing}
        projection = projection or self.local_review_subject({'axis': axis, 'candidate': control_git.snapshot(Path(self.state['handoff']['binding']['worktree']))['head']})
        return {'layer': 'ticket' if axis.startswith('ticket:') else 'local', 'axis': axis,
            'mode': 'complete-ticket-AC' if axis.startswith('ticket:') else 'local-standards-and-contract',
            'ac_refs': projection['ac_refs'], 'requirement_refs': projection['requirement_refs'],
            'dependency_claim_ids': dependencies, 'read_paths': sorted(projection['read_fingerprints']),
            'shared_state': projection['shared_state'], 'missing_dependencies': missing}

    def read_review_claims(self):
        store = state_store.open_store(self.path)
        current = store.open_current()
        views = self.state['control']['context']['handoff_progress'].get('review_claims', {})
        claims = {}
        for axis, view in views.items():
            claim = store.read_evidence(view['claim_ref'], run_id=current.run_id, maximum=state_store.SUBJECT_LIMIT)['value']['review_claim']
            review_evidence.validate_claim(claim)
            require(review_evidence.claim_view(claim, view['claim_ref']) == view and
                    claim['run_id'] == current.run_id and claim['package_digest'] == entry.digest(self.state['packages']),
                    'review_incomplete', 'review claim belongs to another run or package')
            stop = store.read_evidence(claim['native_stop_ref'], run_id=current.run_id)['value']['native_review_stop']
            require(all(stop[key] == claim['native_stop'][key] for key in ('adapter', 'call_ref', 'response_ref', 'status', 'ref')),
                    'review_incomplete', 'original native stopped claim source changed')
            source = store.read_evidence(claim['verification']['source_ref'], run_id=current.run_id)['value']['local_check_source']
            require(entry.digest(source) == claim['verification']['source_digest'] and
                    source['raw'].get('checks') == claim['verification']['checks'],
                    'review_incomplete', 'original stopped regression source changed')
            authority = store.read_evidence(claim['verification']['source_authority_ref'],
                run_id=current.run_id)['value']['review_source_authority']
            issuer = store.read_evidence(authority['issuer_ref'], run_id=current.run_id)['value']['review_issuer']
            identity = review_evidence.validate_original_source(claim, source, authority, issuer)
            slots = [group[axis] for group in [self.state.get('review_activity', {}),
                *self.state.get('review_history', [])] if axis in group and
                group[axis]['action_id'] == claim['native_stop']['action_id']]
            require(len(slots) == 1, 'review_incomplete', 'claim has no exact original prepared native review action')
            control_git.verify_review_native_anchor(store, current, claim, authority, stop, slot=slots[0])
            context = self.state['control']['context']
            require(identity in review_evidence.source_dispatchers(context['carrier'], context['handoff_progress']),
                    'review_incomplete', 'original command source has no authenticated Dispatcher lineage')
            claims[axis] = claim
        return claims

    def review_applications(self, candidate, *, claims=None, changed_paths=(), planning_paths=(), invalid_ids=()):
        claims = self.read_review_claims() if claims is None else claims
        catalog = execution_plan.current_catalog(self.state['scope_authority'])
        checkpoint = self.state['control']['context']['handoff_progress']
        targets, changed, invalid = {}, set(changed_paths), set(invalid_ids)
        axes = {'ticket:' + ticket['ticket_id'] for ticket in catalog['tickets']} | {
            'local:' + ticket['ticket_id'] + ':' + group['id'] for ticket in catalog['tickets']
            for group in ticket['plan']['behavior_slices']} | {'standards', 'spec'}
        repository = Path(self.state['handoff']['binding']['worktree'])
        for axis, claim in claims.items():
            if axis not in axes:
                # A retired slice still has an immutable source. Its target is
                # explicitly missing, never reinterpreted as a replacement slice.
                targets[axis] = {key: copy.deepcopy(claim[key]) for key in (
                    'contract_digest', 'plan_refs', 'owned_fingerprints', 'read_fingerprints',
                    'shared_state', 'dependency_claim_ids', 'ac_refs', 'requirement_refs')}
                targets[axis].update(required=claim['verification']['required'],
                    known_paths=checkpoint['allowed_paths'] + checkpoint['protected_paths'], missing_dependencies=True)
                invalid.add(claim['claim_id'])
                continue
            if axis in {'standards', 'spec'}:
                target = self.delivery_review_subject(candidate, axis)
                target['required'] = checkpoint['validation_plan']['review_required']
            else:
                target = self.local_review_subject({'axis': axis, 'candidate': candidate})
                target['required'] = review_evidence.local_required_checks(catalog, axis, checkpoint['binding']['worktree'])
            target.pop('verification_refs', None)
            target['known_paths'] = checkpoint['allowed_paths'] + checkpoint['protected_paths']
            _, target['missing_dependencies'] = self.review_dependencies(axis)
            targets[axis] = target
            changed.update(set(control_git.git(repository, 'diff', '--name-only', claim['source_candidate'], candidate).decode().splitlines()) -
                           (set(control_git.verified_planning_paths(repository, checkpoint)) | set(planning_paths)))
            if claim['reviewer_ref'] in self.state.get('native_adverse', {}):
                invalid.add(claim['claim_id'])
            for group in [self.state.get('review_activity', {}), *self.state.get('review_history', [])]:
                slot = group.get(axis)
                if slot and slot['action_id'] == claim['native_stop']['action_id'] and (
                        not slot['stopped'] or slot.get('query') and not slot['query']['resolved']):
                    invalid.add(claim['claim_id'])
                if slot and slot['action_id'] == claim['native_stop']['action_id'] and slot['receipts']:
                    latest = slot['receipts'][-1]['raw']
                    if latest.get('format') == 'review-raw-reference-v1' and not latest.get('truncated'):
                        store = state_store.open_store(self.path)
                        latest = {'review': self.read_original_review_semantic(store, store.open_current(), slot, slot['receipts'][-1])}
                    report = latest.get('review')
                    if isinstance(report, dict) and (report.get('findings') or report.get('status') != 'accepted'):
                        invalid.add(claim['claim_id'])
        return claims, review_evidence.applicable_claims(claims, targets, candidate, checkpoint['plan_authority'], changed, invalid_ids=invalid)

    def plan_revision_reviews(self, transaction):
        """Compare authenticated original claims to the verified proposed plan."""
        claims = self.read_review_claims()
        target = copy.copy(self)
        target.state = copy.deepcopy(self.state)
        authority = transaction['proposed_authority']
        target.state['scope_authority'] = copy.deepcopy(authority)
        checkpoint = target.state['control']['context']['handoff_progress']
        checkpoint.update(execution_authority=copy.deepcopy(authority),
            plan_authority={'revision': authority['revision'], 'authority_digest': entry.digest(authority),
                'plan_digest': authority['execution_plan']['plan_digest'],
                'contract_digest': authority['execution_plan']['contract_digest']},
            allowed_paths=authority['scope']['implementation_paths'])
        checkpoint['validation_plan'] = copy.deepcopy(transaction['input'].get('validation_plan', checkpoint['validation_plan']))
        impact = execution_plan.affected_plan_closure(transaction['old_authority'], authority,
            transaction['git_facts']['changed_paths'])
        invalid = [claim['claim_id'] for claim in claims.values()
            if any(ref['step_id'] in impact['impacted_step_ids'] for ref in claim['plan_refs'])]
        implementation_paths = set(transaction['old_authority']['scope']['implementation_paths'] +
                                   authority['scope']['implementation_paths'])
        # Verified planning/shared paths seed the step closure and exact read
        # comparisons. They are not unknown implementation changes that retire
        # every otherwise independent claim.
        return target.review_applications(transaction['commit_input']['planning_commit'], claims=claims,
            changed_paths=implementation_paths.intersection(impact['paths']),
            planning_paths=transaction['git_facts']['changed_paths'], invalid_ids=invalid)

    def persist_plan_revision_reviews(self, transaction):
        _, applications = self.plan_revision_reviews(transaction)
        store = state_store.open_store(self.path)
        current = store.open_current()
        value = {'kind': 'review-applicability-set-v1',
            'candidate': transaction['commit_input']['planning_commit'],
            'plan_authority': next(iter(applications.values()))['target_plan_authority'],
            'applications': applications}
        reference = store.write_object(current.run_id, 'evidence', value)
        return {'candidate': value['candidate'], 'reference': reference, 'digest': entry.digest(value),
            'retained': sorted(app['claim_id'] for app in applications.values() if app['applicable']),
            'affected': sorted(app['claim_id'] for app in applications.values() if not app['applicable']),
            'checkpoint_path': str(self.path)}

    def persist_review_applicability(self, candidate):
        repository = Path(self.state['handoff']['binding']['worktree'])
        require(control_git.snapshot(repository)['head'] == candidate and not control_git.git(repository, 'status', '--porcelain'),
                'candidate_changed', 'applicability requires one exact clean target candidate')
        claims, applications = self.review_applications(candidate)
        store = state_store.open_store(self.path)
        current = store.open_current()
        views = self.state['control']['context']['handoff_progress'].get('review_claims', {})
        for axis, application in applications.items():
            reference = store.write_object(current.run_id, 'evidence', {'review_applicability': application})
            application.update(applicability_ref=reference, claim_ref=views[axis]['claim_ref'])
        value = {'kind': 'review-applicability-set-v1', 'candidate': candidate,
            'plan_authority': self.state['control']['context']['handoff_progress']['plan_authority'],
            'applications': applications}
        reference = store.write_object(current.run_id, 'evidence', value)
        return {'candidate': candidate, 'reference': reference, 'digest': entry.digest(value),
            'retained': sorted(app['claim_id'] for app in applications.values() if app['applicable']),
            'affected': sorted(app['claim_id'] for app in applications.values() if not app['applicable']),
            'checkpoint_path': str(self.path)}

    def build_review_coverage(self, candidate, *, delivery):
        claims, applications = self.review_applications(candidate)
        proof = self.persist_review_applicability(candidate)
        store = state_store.open_store(self.path)
        current = store.open_current()
        applications = store.read_evidence(proof['reference'], run_id=current.run_id)['value']['applications']
        checkpoint = self.state['control']['context']['handoff_progress']
        certificate = review_evidence.coverage_certificate(execution_plan.current_catalog(self.state['scope_authority']),
            claims, applications, candidate, checkpoint['plan_authority'], checkpoint['allowed_paths'], delivery=delivery)
        reference = store.write_object(current.run_id, 'evidence', {'review_coverage': certificate})
        return review_evidence.certificate_view(certificate, reference, str(self.path))

    def delivery_review_subject(self, candidate, axis):
        scope = self.review_scope(axis)
        checkpoint = self.state['control']['context']['handoff_progress']
        read = set(scope['read_paths']) | (set(scope['shared_state']) & set(checkpoint['allowed_paths'] + checkpoint['protected_paths']))
        return {'ticket_id': 'delivery', 'slice_id': axis, 'plan_refs': [],
            'ac_refs': scope['ac_refs'], 'requirement_refs': scope['requirement_refs'],
            'dependency_claim_ids': scope['dependency_claim_ids'], 'shared_state': scope['shared_state'],
            'owned_fingerprints': {}, 'read_fingerprints': control_git.review_fingerprints(Path(checkpoint['binding']['worktree']), candidate, read),
            'contract_digest': entry.digest({'requirements': scope['requirement_refs'], 'acs': scope['ac_refs'],
                'shared_state': scope['shared_state'], 'plan_contract': checkpoint['plan_authority']['contract_digest']})}

    def accept_delivery_review(self, axis, result_ref):
        slot = self.state.get('review_activity', {}).get(axis)
        checkpoint = self.state['control']['context']['handoff_progress']
        require(slot and slot['stopped'] and slot['candidate'] == checkpoint['candidate'] and
                (not slot.get('query') or slot['query']['resolved']), 'review_incomplete', 'original delivery reviewer must stop')
        receipts = [receipt for receipt in slot['receipts'] if receipt['status'] == 'stopped' and receipt['response_ref'] == result_ref]
        require(len(receipts) == 1, 'review_incomplete', 'delivery report needs its original stopped response')
        receipt = receipts[0]
        scope = self.review_scope(axis)
        store = state_store.open_store(self.path)
        current = store.open_current()
        report = self.read_original_review_semantic(store, current, slot, receipt)
        require(isinstance(report, dict) and report.get('status') == 'accepted' and report.get('findings') == [] and
                report.get('review_scope') == scope and report.get('ac_refs') == scope['ac_refs'] and
                report.get('requirement_refs') == scope['requirement_refs'] and
                report.get('dependency_claim_ids') == scope['dependency_claim_ids'],
                'review_incomplete', 'delivery review must accept cross-Ticket/shared-state and complete coverage')
        writers = checkpoint['executions'] + checkpoint.get('carried_results', [])
        for source in checkpoint.get('plan_revision_sources', []):
            writers += source['executions'] + source['carried_results']
        require(receipt['ref'] not in {item['agent_ref'] for item in writers} | {self.bound_ref(), self.state['handoff']['controller_ref']},
                'identity_mismatch', 'delivery reviewer cannot be any implementation author')
        source = checkpoint['candidate_evidence']['source']
        required = checkpoint['validation_plan']['review_required']
        control.validate_checks(required, checkpoint['tests'], checkpoint['candidate'])
        control.validate_result_source(source, checkpoint['tests'], self.bound_ref())
        projection = self.delivery_review_subject(checkpoint['candidate'], axis)
        claim = {'schema_version': 1, 'run_id': current.run_id, 'package_digest': entry.digest(self.state['packages']),
            'layer': 'delivery', **projection, 'plan_authority': checkpoint['plan_authority'],
            'source_candidate': checkpoint['candidate'], 'verification': {'ids': [item['id'] for item in required],
                'required': required, 'checks': checkpoint['tests'], 'source_results': [],
                'source_ref': store.write_object(current.run_id, 'evidence', {'local_check_source': source}),
                'source_digest': entry.digest(source), 'environment_digest': entry.digest(source['raw'].get('environment')),
                'fixture_digest': entry.digest(source['raw'].get('fixture'))},
            'reviewer_ref': receipt['ref'], 'native_stop': {**{key: receipt[key] for key in ('adapter', 'call_ref', 'response_ref', 'status', 'ref')},
                'action_id': slot['action_id']}, 'native_stop_ref': store.write_object(current.run_id, 'evidence', {'native_review_stop': receipt}),
            'findings': [], 'status': 'accepted'}
        self.freeze_review_source_authority(claim, slot, store, current)
        claim['claim_id'] = entry.digest(claim)
        reference = store.write_object(current.run_id, 'evidence', {'review_claim': claim})
        return review_evidence.claim_view(claim, reference)

    def local_review_subject(self, data):
        checkpoint = self.state['control']['context']['handoff_progress']
        ticket, group, steps = review_evidence.subject(execution_plan.current_catalog(self.state['scope_authority']), data['axis'])
        owned = {step['target']['path'] if 'target' in step else step['path'] for step in steps}
        for item in self.state['scope_authority']['scope']['downstream']['manifest']:
            if item['owner'] == 'implementation' and item['kind'] == 'generated' and owned & set(item['source_paths']):
                owned.add(item['path'])
        read = set(group['read_only']) | (set(group['shared_state']) &
            set(checkpoint['allowed_paths'] + checkpoint['protected_paths']))
        read -= owned
        fingerprints = control_git.review_fingerprints(Path(self.state['handoff']['binding']['worktree']),
            data['candidate'], owned | read)
        dependencies, missing = self.review_dependencies(data['axis'])
        return {'ticket_id': ticket['ticket_id'], 'slice_id': group['id'],
            'plan_refs': [execution_plan.step_ref(ticket, step) for step in steps],
            'ac_refs': group['ac_refs'], 'shared_state': group['shared_state'],
            'owned_fingerprints': {p: fingerprints[p] for p in sorted(owned)},
            'read_fingerprints': {p: fingerprints[p] for p in sorted(read)},
            'contract_digest': entry.digest({'contract_refs': ticket['plan']['contract_refs'],
                                            'slice': group}),
            'verification_refs': group['verification_refs'], 'dependency_claim_ids': dependencies,
            'requirement_refs': review_evidence.ticket_requirement_refs(execution_plan.current_catalog(self.state['scope_authority']), ticket)
                if data['axis'].startswith('ticket:') else []}

    def validate_local_review(self, data):
        checkpoint = self.state['control']['context']['handoff_progress']
        require(checkpoint['state'] == 'implementing', 'review_incomplete', 'local review precedes Ticket acceptance')
        catalog = execution_plan.current_catalog(self.state['scope_authority'])
        current = state_store.open_store(self.path).open_current()
        required = review_evidence.validate_local_verification(catalog, data['axis'], data['verification'],
            data['candidate'], checkpoint['plan_authority'], self.state['handoff']['binding']['worktree'],
            current.run_id, entry.digest(self.state['packages']), self.bound_ref())
        ticket, group, steps = review_evidence.subject(catalog, data['axis'])
        groups = ticket['plan']['behavior_slices']
        if ticket['ticket_id'] in self.ticket_checks_due():
            checked = checkpoint.get('ticket_checks', {}).get(ticket['ticket_id'])
            selected = execution_plan.ticket_check_paths(catalog, ticket['ticket_id'],
                checkpoint['allowed_paths'], checkpoint['protected_paths'], self.state['scope_authority'])
            require(checked is not None and checked['plan_authority'] == checkpoint['plan_authority'] and
                    checked['source_snapshot'] == {relative: control_git.file_fingerprint(
                        Path(checkpoint['binding']['worktree']), relative) for relative in selected},
                    'ticket_check_required', 'run the current affected/Ticket checks before local or Ticket review')
        results = control.current_accepted_results(checkpoint)
        completed = execution_plan.current_accepted_steps(execution_plan.current_catalog(self.state['scope_authority']),
            results, self.state['scope_authority'])
        require(set(group['step_ids']) <= completed, 'review_incomplete', 'accept and stop every local Execution result first')
        selected_paths = set(self.local_review_subject(data)['owned_fingerprints'])
        final_owned = {}
        for result in results:
            owned = execution_plan.accepted_owned_fingerprints(result)
            require(owned is not None and result['stopped'] and result['state'] == 'accepted' and
                    result.get('plan_authority') == checkpoint['plan_authority'],
                    'review_incomplete', 'current stopped Execution fingerprints are required')
            final_owned.update(owned)
        repository = Path(self.state['handoff']['binding']['worktree'])
        require(selected_paths <= set(final_owned) and all(control_git.file_fingerprint(repository, relative) == final_owned[relative]
                    for relative in selected_paths), 'review_incomplete', 'local source differs from its latest accepted Execution owner')
        if data['axis'].startswith('ticket:'):
            claims, applications = self.review_applications(data['candidate'])
            required_axes = self.review_dependency_axes(data['axis'])
            require(all(axis in claims and applications[axis]['applicable'] for axis in required_axes),
                    'review_incomplete', 'Ticket acceptance requires every applicable local slice and prerequisite Ticket')
        return required

    def accept_local_review(self, evidence):
        self.require_not_stopping()
        self.require_business_ready()
        require(not self.unresolved_host_actions(), 'outcome_unknown', 'resolve original calls before accepting local review')
        slot = self.state.get('review_activity', {}).get(evidence['axis'])
        require(slot is not None and slot['candidate'] == evidence['candidate'] and slot['start']['applied'] and
                slot['stopped'] and (not slot.get('query') or slot['query']['resolved']),
                'review_incomplete', 'original local native review must stop')
        required = self.validate_local_review(slot['prepare'])
        require(slot['subject'] == self.local_review_subject(slot['prepare']),
                'review_incomplete', 'local review source or dependency snapshot changed')
        repository = Path(self.state['handoff']['binding']['worktree'])
        require(control_git.snapshot(repository)['head'] == evidence['candidate'] and
                not control_git.git(repository, 'status', '--porcelain'),
                'candidate_changed', 'local review consumes its exact clean source snapshot')
        receipts = [r for r in slot['receipts'] if r['response_ref'] == evidence['result_ref'] and r['status'] == 'stopped']
        require(len(receipts) == 1 and receipts[0]['ref'] == slot['ref'],
                'review_incomplete', 'semantic claim must name its original native stopped response')
        receipt, projection = receipts[0], slot['subject']
        checkpoint = self.state['control']['context']['handoff_progress']
        executions = list(checkpoint['executions']) + list(checkpoint.get('carried_results', []))
        for previous in checkpoint.get('plan_revision_sources', []):
            executions.extend(previous['executions'] + previous['carried_results'])
        require(slot['ref'] not in {result['agent_ref'] for result in executions
                    if set(result['paths']) & set(projection['owned_fingerprints'])} and
                slot['ref'] not in {self.bound_ref(), self.state['handoff']['controller_ref']},
                'identity_mismatch', 'a reviewer cannot review any version of its implementation')
        store = state_store.open_store(self.path)
        current = store.open_current()
        semantic = self.read_original_review_semantic(store, current, slot, receipt)
        require(isinstance(semantic, dict) and semantic.get('status') == 'accepted' and
                semantic.get('findings') == [] and semantic.get('checks') == slot['prepare']['verification']['checks'] and
                set(semantic.get('read_paths', [])) == set(projection['read_fingerprints']) and
                set(semantic.get('ac_refs', [])) == set(projection['ac_refs']) and
                semantic.get('dependency_claim_ids', []) == projection['dependency_claim_ids'] and
                semantic.get('requirement_refs', []) == projection['requirement_refs'] and
                (not evidence['axis'].startswith('ticket:') or semantic.get('review_scope') == self.review_scope(evidence['axis'], projection)),
                'review_incomplete', 'original reviewer must accept exact ACs, reads and local regressions without findings')
        stop_ref = store.write_object(current.run_id, 'evidence', {'native_review_stop': receipt})
        source = slot['prepare']['verification']['source']
        source_ref = store.write_object(current.run_id, 'evidence', {'local_check_source': source})
        claim = {'schema_version': 1, 'run_id': current.run_id, 'package_digest': entry.digest(self.state['packages']),
            'layer': 'ticket' if evidence['axis'].startswith('ticket:') else 'local', **{k: copy.deepcopy(v) for k, v in projection.items() if k != 'verification_refs'},
            'plan_authority': checkpoint['plan_authority'], 'source_candidate': evidence['candidate'],
            'verification': {'ids': projection['verification_refs'],
                'required': required, 'checks': semantic['checks'], 'source_ref': source_ref,
                'source_digest': entry.digest(source),
                'environment_digest': entry.digest(source['raw']['environment']),
                'fixture_digest': entry.digest(source['raw']['fixture']),
                'source_results': [entry.digest(result) for result in executions
                    if set(result['paths']) & set(projection['owned_fingerprints'])]},
            'reviewer_ref': slot['ref'], 'native_stop': {**{k: receipt[k] for k in
                ('adapter', 'call_ref', 'response_ref', 'status', 'ref')}, 'action_id': slot['action_id']},
            'native_stop_ref': stop_ref, 'findings': [], 'status': 'accepted'}
        self.freeze_review_source_authority(claim, slot, store, current)
        claim['claim_id'] = entry.digest(claim)
        review_evidence.validate_claim(claim)
        reference = store.write_object(current.run_id, 'evidence', {'review_claim': claim})
        return review_evidence.claim_view(claim, reference)

    def freeze_review_source_authority(self, claim, slot, store, current):
        carrier = self.state['control']['context']['carrier']
        verification = claim['verification']
        issuer = store.read_evidence(slot['issuer_ref'], run_id=current.run_id)['value']['review_issuer']
        require((issuer['dispatcher_ref'], issuer['carrier_attempt']) == (carrier['ref'], carrier['attempt']),
                'review_incomplete', 'only the original current Dispatcher can mint its review claim')
        authority = {'dispatcher_ref': carrier['ref'], 'carrier_attempt': carrier['attempt'],
            'request_digest': entry.digest(slot['prepare']), 'source_ref': verification['source_ref'],
            'source_digest': verification['source_digest'], 'native_action_id': slot['action_id'],
            'native_stop_ref': claim['native_stop_ref'], 'candidate': claim['source_candidate'],
            'plan_authority': claim['plan_authority'], 'run_id': current.run_id,
            'package_digest': claim['package_digest'], 'issuer_ref': slot['issuer_ref'], 'issuer_digest': entry.digest(issuer),
            'originating_ref': slot['originating_ref'], 'stage_subject': self.state['stage_subject'],
            'prepare_request_ref': store.write_object(current.run_id, 'evidence', slot['prepare'])}
        stop = store.read_evidence(claim['native_stop_ref'], run_id=current.run_id)['value']['native_review_stop']
        observation = {'operation': 'observe', 'axis': slot['prepare']['axis'], 'candidate': slot['candidate'],
            'actor_ref': slot['originating_ref'], 'action_id': slot['action_id'], 'receipt': copy.deepcopy(stop)}
        if stop['raw'].get('format') == 'review-raw-reference-v1':
            observation['receipt']['raw'] = {key: stop['raw'][key] for key in ('size', 'sha256')}
        else:
            observation = self.review_receipt_request(observation)
        authority['stop_request_ref'] = store.write_object(current.run_id, 'evidence', observation)
        verification['source_authority_ref'] = store.write_object(current.run_id, 'evidence',
            {'review_source_authority': authority})
        verification['source_authority_digest'] = entry.digest(authority)

    def read_original_review_semantic(self, store, current, slot, receipt):
        observation = {'operation': 'observe', 'axis': slot['prepare']['axis'], 'candidate': slot['candidate'],
            'actor_ref': slot['originating_ref'], 'action_id': slot['action_id'], 'receipt': copy.deepcopy(receipt)}
        if receipt['raw'].get('format') == 'review-raw-reference-v1':
            observation['receipt']['raw'] = {key: receipt['raw'][key] for key in ('size', 'sha256')}
        else:
            observation = self.review_receipt_request(observation)
        return control_git.read_review_semantic(store, current, receipt, observation, self.state['stage_subject'])

    def commit_review_start(self, slot, result, applied):
        self.state['control']['context'] = result['context']
        self.next_envelope(applied)
        slot['start'].update(applied=True, result_digest=entry.digest(result['context']),
                             result_ref=copy.deepcopy(result.get('store_ref')))
        store, current, key = self.receipt_store('review:prepare', self.state['stage_subject'], slot['action_id'])
        self._commit_receipts.append(store.prepare_receipt(current, key, slot['prepare'], {
            'action_id': slot['action_id'], 'control_digest': slot['start']['result_digest'],
            'control_ref': slot['start']['result_ref'], 'issuer_ref': slot['issuer_ref']}))
        self.save()

    def read_review_start_result(self, slot):
        """Read the original ledger transaction proof, never invoke review-start again."""
        start = slot['start']
        if start['applied']:
            return
        if slot['prepare']['axis'].startswith(('local:', 'ticket:')):
            require(entry.digest(self.state['control']) == start['control_digest'],
                    'control_context_changed', 'pending local review cannot replace a newer authority')
            return {'context': copy.deepcopy(self.state['control']['context'])}, None
        if start['discussion'] is None:
            return
        require(entry.digest(self.state['control']) == start['control_digest'],
                'control_context_changed', 'pending review start cannot replace a newer authority')
        envelope = start['discussion']
        query = {key: value for key, value in envelope.items()
                 if key not in {'expected_ledger_revision', 'expected_topic_revision', 'idempotency_key'}}
        latest = dispatch.discussion_protocol.handle({**query, 'operation': 'read-topic'})
        pointer = latest['workflow_control_ref']
        require(pointer is not None, 'authority_missing', 'original review ledger is unavailable')
        ledger = state_store.StateStore(pointer['ledger_path'])
        request = {**envelope, 'operation': 'workflow-control', 'action': 'review-start',
                   'evidence': {'candidate': slot['candidate']}}
        if start['store_ref'] is not None:
            request['expected_control_ref'] = start['store_ref']
        fingerprint = hashlib.sha256(dispatch.discussion_protocol._canonical_json(
            {key: value for key, value in request.items() if key != 'idempotency_key'}).encode('utf-8')).hexdigest()
        recovered = ledger.read_ledger_receipt(envelope['actor_topic_id'], envelope['idempotency_key'], fingerprint)
        if recovered is None:
            # The actual read-topic pointer proves this generation committed,
            # including a crash between ledger CAS and receipt certification.
            context, body = ledger.read_ledger_control(pointer)
            proof = body['receipt']
            if proof is None or proof['key'] != envelope['idempotency_key']:
                return
            require(proof['fingerprint'] == fingerprint,
                    'review_conflict', 'original ledger review intent changed')
            recovered = {**proof['result'], 'control': {**proof['result']['control'],
                                                        'context': context, 'store_ref': pointer}}
        else:
            require(recovered['control']['store_ref'] == pointer,
                    'control_context_changed', 'later ledger authority cannot be replaced by an old review proof')
            context, _ = ledger.read_ledger_control(pointer)
            recovered['control']['context'] = context
        result = recovered['control']
        require(result['context']['controller_ref'] == self.state['control']['context']['controller_ref'] and
                result['context']['handoff_progress']['candidate'] == slot['candidate'] and
                result['context']['handoff_progress']['state'] == 'reviewing',
                'review_conflict', 'original ledger proof does not apply this candidate review')
        return result, recovered

    def new_review_query(self, slot):
        return {'action_id': str(uuid.uuid4()), 'resolved': False,
                'original_action_id': slot['action_id'], 'call_ref': None}

    def bind_review_receipt(self, axis, slot, receipt, actor_ref):
        if receipt['status'] == 'not-created':
            require(slot['ref'] is None and receipt['ref'] is None,
                    'identity_mismatch', 'bound reviewer cannot become not-created')
        elif receipt['ref'] is not None:
            entry.nonempty(receipt['ref'])
            require(slot['ref'] in {None, receipt['ref']} and receipt['ref'] not in {self.bound_ref(), actor_ref},
                    'identity_mismatch', 'reviewer must retain an independent native identity')
            require(all(other_axis == axis or value.get('ref') != receipt['ref']
                        for other_axis, value in self.state.get('review_activity', {}).items()
                        if axis in {'standards', 'spec'} and other_axis in {'standards', 'spec'}),
                    'identity_mismatch', 'review axes require separate identities')
            slot['ref'] = receipt['ref']
        require(receipt['status'] != 'stopped' or slot['ref'] is not None,
                'host_evidence_missing', 'stopped review needs an actual ref')

    def review_lookup(self, axis, slot):
        """Protect the exact original call lookup independently of business space."""
        store = state_store.open_store(self.path)
        query = slot.get('query')
        if query is None or query.get('resolved'):
            query = self.new_review_query(slot)
            store.reserve(query['action_id'], recovery=True)
            slot['query'] = query
            self._receipt_action = query['action_id']
            self.save()
        else:
            store.require_reserve(query['action_id'], recovery=True)
        return {'operation': 'lookup-exact-review', 'axis': axis,
                'action_id': slot['action_id'], 'ref': slot.get('ref'),
                'call_ref': slot.get('call_ref') or (slot.get('raw_ingress') or {}).get('call_ref'),
                'query_action_id': query['action_id'],
                'query_call_ref': query.get('call_ref')}

    def review_receipt_request(self, data):
        size, digest = state_store.raw_measure(data['receipt']['raw'])
        return {**data, 'receipt': {**data['receipt'], 'raw': {'size': size, 'sha256': digest}}}

    def review_semantic_fact(self, receipt, original, raw):
        projection = receipt['raw']
        complete = projection.get('format') != 'review-raw-reference-v1' or not projection['truncated']
        report = raw.get('review') if isinstance(raw, dict) else None
        report = ({key: copy.deepcopy(report[key]) for key in ('status', 'findings', 'read_paths', 'ac_refs',
            'checks', 'dependency_claim_ids', 'requirement_refs', 'review_scope') if key in report}
            if isinstance(report, dict) else None)
        fact = {'kind': 'native-stop-semantic-v1', 'complete': complete, 'report': report if complete else None,
            'binding': {key: copy.deepcopy(original[key]) for key in ('axis', 'candidate', 'actor_ref', 'action_id')},
            'receipt': {key: receipt[key] for key in ('adapter', 'call_ref', 'response_ref', 'status', 'ref')},
            'raw_proof': copy.deepcopy(original['receipt']['raw']),
            'raw_manifest_ref': projection.get('evidence_ref') if projection.get('format') == 'review-raw-reference-v1' else None}
        if len(state_store.encode(fact)) > state_store.SUBJECT_LIMIT:
            fact.update(complete=False, report=None)
        return fact

    def prepare_review_observation_proof(self, store, current, key, slot, receipt, original, raw):
        return store.prepare_receipt(current, key, original, {
            'action_id': slot['action_id'], 'response_ref': receipt['response_ref'],
            'native_stop_ref': store.write_object(current.run_id, 'evidence', {'native_review_stop': receipt}),
            'native_stop_semantic_ref': store.write_object(current.run_id, 'evidence', {
                'native_stop_semantic': self.review_semantic_fact(receipt, original, raw)})})

    def apply_review_receipt(self, slot, receipt, original, key, current, *, proof=True, semantic_raw=None):
        query = slot.get('query')
        if 'call_ref' not in slot:
            slot['call_ref'] = receipt['call_ref']
        if query and not query.get('resolved'):
            query['call_ref'] = receipt['call_ref']
            query['resolved'] = receipt['status'] != 'unknown'
        slot['receipts'].append(copy.deepcopy(receipt))
        slot['stopped'] = receipt['status'] in {'stopped', 'not-created'}
        if slot['stopped']:
            report = (receipt['raw'] if semantic_raw is None else semantic_raw).get('review')
            classification = 'unknown'
            if isinstance(report, dict) and isinstance(report.get('findings'), list):
                classification = 'review-defect' if report['findings'] else 'accepted' if report.get('status') == 'accepted' else 'unknown'
            slot['cost'] = {'classification': classification,
                'source_ref': entry.digest({key: receipt[key] for key in ('adapter', 'call_ref', 'response_ref')}),
                'waiting_seconds': 'unknown'}
            slot['business_settled'] = True
            self.state.get('native_adverse', {}).pop(slot['ref'], None)
        if proof:
            store = state_store.open_store(self.path, receipt_action=self._receipt_action)
            self._commit_receipts.append(self.prepare_review_observation_proof(store, current, key, slot, receipt,
                original, receipt['raw'] if semantic_raw is None else semantic_raw))
        self.save()

    def review_raw_projection(self, receipt, reference, manifest, measurement):
        size, digest = measurement
        truncated = size > state_store.EVIDENCE_LIMIT or not manifest['complete']
        return {**receipt, 'status': 'unknown' if truncated else receipt['status'], 'raw': {
            'format': 'review-raw-reference-v1', 'evidence_ref': reference, 'size': size,
            'sha256': digest, 'truncated': truncated, 'reported_status': receipt['status'],
            'reason': 'atomic_limit_exceeded' if size > state_store.EVIDENCE_LIMIT else manifest['reason']}}

    def retain_review_receipt(self, slot, data, original, key, current):
        receipt = data['receipt']
        measurement = (original['receipt']['raw']['size'], original['receipt']['raw']['sha256'])
        if measurement[0] <= REVIEW_INLINE_RAW_LIMIT:
            slot.pop('raw_ingress', None)
            return copy.deepcopy(receipt)
        binding = {key: receipt[key] for key in ('adapter', 'call_ref', 'response_ref', 'ref', 'status')}
        binding.update(action_id=slot['action_id'], candidate=slot['candidate'],
                       originating_ref=slot['originating_ref'])
        slot['raw_ingress'] = {**binding, 'size': measurement[0], 'sha256': measurement[1],
                               'receipt_action': self._receipt_action}
        # A crash before/during spooling retains the authenticated original
        # response identity. It cannot be mistaken for a completed stop.
        slot['stopped'] = False
        self.save()
        store = state_store.open_store(self.path, receipt_action=self._receipt_action)
        current = store.open_current()
        size, digest = measurement
        chunks = [{'run_id': current.run_id, 'kind': 'raw-chunk', 'sha256': '0' * 64,
                   'size': min(1024 * 1024, size - offset)}
                  for offset in range(0, min(size, state_store.EVIDENCE_LIMIT), 1024 * 1024)]
        measured = []
        for reason in (None, 'state_capacity', 'interrupted'):
            manifest = {'format': 'raw-evidence-v1', 'complete': reason is None, 'size': size,
                'retained_size': min(size, state_store.EVIDENCE_LIMIT), 'sha256': digest,
                'chunks': chunks, 'reason': reason, 'native_receipt': binding}
            with store.measure_writes(sequence=True) as writes:
                reference = store.write_object(current.run_id, 'evidence', manifest)
                candidate = Progress(self.path, copy.deepcopy(self.outer))
                candidate._authority_revision = self._authority_revision
                candidate._receipt_action = self._receipt_action
                saved = next(value for group in [candidate.state.get('review_activity', {}),
                    *candidate.state.get('review_history', [])] for value in group.values()
                    if value['action_id'] == slot['action_id'])
                projected = self.review_raw_projection(receipt, reference, manifest, measurement)
                if reason != 'interrupted':
                    saved.pop('raw_ingress', None)
                candidate.apply_review_receipt(saved, projected, original, key, current, proof=False)
                candidate.prepare_review_observation_proof(store, current, key, saved, projected, original, receipt['raw'])
                measured.append(sum(writes.values()) + 2 * state_store.ROOT_LIMIT)
        commit_bytes = max(measured)
        require(commit_bytes <= state_store.EVIDENCE_LIMIT,
                'state_capacity', 'review receipt exceeds its bounded commit reserve')
        try:
            reference, manifest = store.write_raw_evidence(current.run_id, receipt['raw'],
                measurement=measurement, commit_bytes=commit_bytes, native_receipt=binding)
        except state_store.RawEvidenceInterrupted as error:
            slot['raw_ingress']['evidence_ref'] = error.reference
            manifest = store.read_raw_manifest(error.reference, run_id=current.run_id)
            self.apply_review_receipt(slot, self.review_raw_projection(receipt, error.reference, manifest, measurement),
                                      original, key, current, semantic_raw=receipt['raw'])
            raise
        slot.pop('raw_ingress', None)
        return self.review_raw_projection(receipt, reference, manifest, measurement)

    def review_activity(self, data):
        """Two fixed review slots; allocation and candidate acceptance stay in B."""
        entry.fields(data, {'operation','axis','candidate','actor_ref'}, {'verification','action_id','receipt'})
        s = self.state
        require(s['stage'] == 3 and isinstance(data['axis'], str) and (data['axis'] in {'standards','spec'} or data['axis'].startswith(('local:', 'ticket:'))), 'role_mismatch', 'only the Stage-3 review axes are supported')
        current = handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
        actor = s['handoff']['expected_entry']['actor']
        require(data['actor_ref'] == actor['thread_id'] == current['actor']['thread_id'] and
                actor['role'] in {'controller','scripted-carrier'}, 'identity_mismatch', 'only the original Originating Task records review activity')
        handoff.commit(data['candidate'])
        slots = s.setdefault('review_activity', {})
        slot = slots.get(data['axis'])
        if data['operation'] == 'observe' and (slot is None or slot['candidate'] != data['candidate'] or
                                               slot['action_id'] != data.get('action_id')):
            matches = [group[data['axis']] for group in s.get('review_history', []) if data['axis'] in group and
                       group[data['axis']]['candidate'] == data['candidate'] and group[data['axis']]['action_id'] == data.get('action_id')]
            require(len(matches) == 1, 'review_missing', 'observe one exact retained historical review call')
            slot = matches[0]
        if data['operation'] == 'prepare':
            entry.fields(data, {'operation','axis','candidate','actor_ref','verification'})
            if slot is not None and slot['candidate'] == data['candidate']:
                require(slot['prepare'] == data, 'review_conflict', 'retain the original review intent')
                if slot['start']['applied']:
                    store, receipt_current, key = self.receipt_store('review:prepare', s['stage_subject'], slot['action_id'])
                    proof = store.read_receipt(receipt_current, key, data)
                    require(proof == {'action_id': slot['action_id'],
                            'control_digest': slot['start']['result_digest'], 'control_ref': slot['start']['result_ref'],
                            'issuer_ref': slot['issuer_ref']},
                            'review_conflict', 'review start lacks its committed original receipt')
                recovered = self.read_review_start_result(slot)
                lookup = self.review_lookup(data['axis'], slot)
                if recovered is not None:
                    self._receipt_action = slot['query']['action_id']
                    self.commit_review_start(slot, *recovered)
                return _view(s, lookup, acknowledged=True)
            self.require_not_stopping()
            self.require_business_ready()
            if slot is None or slot['candidate'] != data['candidate']:
                self.require_finalizations_released()
            local = data['axis'].startswith(('local:', 'ticket:'))
            if local:
                self.validate_local_review(data)
            else:
                control.validate_reviewable(s['control']['context']['handoff_progress'], data['candidate'], data['verification'])
            binding = s['handoff']['binding']
            scope = (s.get('scope_authority') or {}).get('scope', s['handoff']['scope'])
            observed = supervision.verify_worktree({'binding':binding,'platform_cwd':binding['worktree']})
            require(observed['current_commit'] == data['candidate'] and not supervision._full_status(Path(binding['worktree'])),
                    'candidate_changed', 'review requires the clean exact candidate')
            checkpoint = s['control']['context']['handoff_progress']
            changed = (set(supervision._changed_paths(Path(binding['repository']),
                checkpoint.get('expected_target_head', checkpoint['git_baseline_commit']), data['candidate'])) -
                set(checkpoint.get('integration_retained', {}).get('paths', [])) -
                control_git.verified_planning_paths(Path(binding['worktree']), checkpoint))
            require(set(changed) <= set(scope['implementation_paths']) and
                    not set(changed) & set(checkpoint['protected_paths']),
                    'scope_changed', 'review candidate escaped implementation scope')
            if local and slot is not None:
                require(slot.get('stopped'), 'review_active', 'stop the original local review before replacing it')
                s.setdefault('review_history', []).append({data['axis']: copy.deepcopy(slot)})
                slots.pop(data['axis'])
                slot = None
            if not local and any(value['candidate'] != data['candidate'] for key, value in slots.items()
                                 if key in {'standards', 'spec'}):
                require(all(value.get('stopped') for value in slots.values()), 'review_active', 'stop and reconcile both old review axes first')
                old_delivery = {key: slots.pop(key) for key in ('standards', 'spec') if key in slots}
                s.setdefault('review_history', []).append(old_delivery)
                slot = None
            slot = {'candidate':data['candidate'], 'originating_ref':data['actor_ref'], 'prepare':copy.deepcopy(data),
                    'action_id':str(uuid.uuid4()), 'ref':None, 'stopped':False, 'receipts':[],
                    'start': {'control_digest': entry.digest(s['control']),
                              'discussion': copy.deepcopy(s['control']['discussion']),
                              'store_ref': copy.deepcopy(s['control'].get('store_ref')), 'applied': False}}
            if local:
                slot['subject'] = self.local_review_subject(data)
            slots[data['axis']] = slot
            store = state_store.open_store(self.path)
            issuer_current = store.open_current()
            carrier = s['control']['context']['carrier']
            command_source = (data['verification']['source'] if local else checkpoint['candidate_evidence']['source'])
            slot['issuer_ref'] = store.write_object(issuer_current.run_id, 'evidence', {'review_issuer': {
                'dispatcher_ref': carrier['ref'], 'carrier_attempt': carrier['attempt'],
                'request_digest': entry.digest(data), 'native_action_id': slot['action_id'],
                'run_id': issuer_current.run_id, 'package_digest': entry.digest(s['packages']),
                'plan_authority': checkpoint['plan_authority'], 'source_digest': entry.digest(command_source),
                'source_ref': store.write_object(issuer_current.run_id, 'evidence', {'local_check_source': command_source})}})
            # Reject every bounded candidate before reserve allocation or C's
            # review-start. No external call can escape this durable intent.
            ledger_review = None
            if not local and s['control']['discussion'] is not None:
                ledger_review = {**s['control']['discussion'], 'operation': 'workflow-control',
                    'action': 'review-start', 'evidence': {'candidate': data['candidate']},
                    'expected_control_ref': s['control']['store_ref']}
            intent_bytes = 0
            for status in ('unknown', 'running', 'stopped', 'not-created'):
                with store.measure_writes(sequence=True, ledger_review=ledger_review) as writes:
                    candidate = Progress(self.path, copy.deepcopy(self.outer))
                    candidate._authority_revision = self._authority_revision
                    candidate.save()
                    intent_bytes = max(intent_bytes, sum(writes.values()))
                    target = candidate.state['review_activity'][data['axis']]
                    if local:
                        preview = {'context': copy.deepcopy(candidate.state['control']['context'])}
                        applied = None
                    elif ledger_review is None:
                        context = candidate.state['control']['context']
                        preview = control.transition({'schema_version': context['schema_version'],
                            'actor_ref': context['controller_ref'], 'context': context,
                            'action': 'review-start', 'evidence': {'candidate': data['candidate']}})
                        applied = None
                    else:
                        # The existing ledger adapter runs its original guards,
                        # pure/Git transition and pointer/receipt serializer; its
                        # exact named overlay prevents only physical publication.
                        preview, applied = dispatch.checkpoint(candidate.state['control'], candidate.state['handoff'],
                            'review-start', {'candidate': data['candidate']})
                    candidate.commit_review_start(target, preview, applied)
                    target['query'] = candidate.new_review_query(target)
                    candidate.save()
                    # Every permitted opaque UTF-8 byte may encode as six JSON
                    # bytes. Raw's limit already measures canonical JSON bytes.
                    opaque = '\x01' * 4096
                    ref = next(chr(code) * 4096 for code in (2, 3, 4)
                        if chr(code) * 4096 not in {candidate.bound_ref(), data['actor_ref']} and
                        all(axis == data['axis'] or value.get('ref') != chr(code) * 4096
                            for axis, value in candidate.state['review_activity'].items()))
                    receipt = {'adapter': opaque, 'call_ref': opaque, 'response_ref': opaque,
                        'ref': None if status == 'not-created' else ref, 'status': status,
                        'raw': {'output': 'x' * (REVIEW_INLINE_RAW_LIMIT - 13)}}
                    observation = {key: data[key] for key in ('axis', 'candidate', 'actor_ref')}
                    observation.update(operation='observe', action_id=target['action_id'], receipt=receipt)
                    candidate.bind_review_receipt(data['axis'], target, receipt, data['actor_ref'])
                    original = candidate.review_receipt_request(observation)
                    current = store.open_current()
                    key = store.receipt_key(current, actor=candidate.state['control']['context']['controller_ref'],
                        kind='review:observe', subject=s['stage_subject'], invocation=receipt['response_ref'])
                    candidate.apply_review_receipt(target, receipt, original, key, current)
                    require(sum(writes.values()) + 2 * state_store.ROOT_LIMIT <= state_store.EVIDENCE_LIMIT,
                            'state_capacity', 'review completion exceeds its original receipt reserve')
            store.require_ordinary_capacity(intent_bytes)
            store.reserve(slot['action_id'])
            self.save()
            if local:
                result, applied = {'context': copy.deepcopy(s['control']['context'])}, None
            else:
                result, applied = dispatch.checkpoint(s['control'], s['handoff'], 'review-start', {'candidate':data['candidate']})
            self._receipt_action = slot['action_id']
            self.commit_review_start(slot, result, applied)
            return _view(s, {'operation':'invoke-review','axis':data['axis'],
                'candidate':data['candidate'], 'verification':copy.deepcopy(data['verification']),
                'action_id':slot['action_id'], 'review_scope': self.review_scope(data['axis'], slot.get('subject'))})
        entry.fields(data, {'operation','axis','candidate','actor_ref','action_id','receipt'})
        require(data['operation'] == 'observe' and slot is not None and slot['candidate'] == data['candidate'] and
                slot['action_id'] == data['action_id'], 'review_missing', 'observe the original exact review call')
        receipt = data['receipt']
        entry.fields(receipt, {'adapter','call_ref','response_ref','status','ref','raw'})
        for key in ('adapter','call_ref','response_ref'):
            entry.nonempty(receipt[key])
            require(len(receipt[key].encode('utf-8')) <= 4096,
                    'state_capacity', 'review native identity exceeds its fixed budget')
        require(receipt['ref'] is None or isinstance(receipt['ref'], str) and
                0 < len(receipt['ref'].encode('utf-8')) <= 4096,
                'identity_mismatch', 'reviewer reference must be a bounded native identity')
        require(isinstance(receipt['raw'], dict) and receipt['raw'] and
                receipt['raw'].get('format') != 'review-raw-reference-v1',
                'host_evidence_missing', 'raw authenticated native receipt required')
        require(receipt['status'] in {'running','stopped','unknown','not-created'}, 'invalid_result', 'invalid native review state')
        original = self.review_receipt_request(data)
        store, receipt_current, key = self.receipt_store('review:observe', s['stage_subject'], receipt['response_ref'])
        if store.read_receipt(receipt_current, key, original) is not None:
            self.release_settled_reserves()
            return _view(s, acknowledged=True)
        for group in [s.get('review_activity', {}), *s.get('review_history', [])]:
            for other in group.values():
                for previous in other['receipts']:
                    if previous['call_ref'] == receipt['call_ref'] or previous['response_ref'] == receipt['response_ref']:
                        require(other['action_id'] == slot['action_id'] and previous['adapter'] == receipt['adapter'],
                                'review_conflict', 'native review invocation cannot be rebound')
                    require(previous['response_ref'] != receipt['response_ref'],
                            'review_conflict', 'review response lacks its committed receipt proof')
        query = slot.get('query')
        original_call = slot.get('call_ref') or (slot.get('raw_ingress') or {}).get('call_ref')
        require(slot['ref'] is not None or original_call is None or receipt['call_ref'] == original_call or
                query is not None and not query.get('resolved'),
                'review_conflict', 'lost creation permits only the original invocation or its protected exact lookup')
        if query and not query.get('resolved'):
            store.require_reserve(query['action_id'], recovery=True)
            require(query['call_ref'] in {None, receipt['call_ref']},
                    'review_conflict', 'unknown lookup retains its exact invocation')
            self._receipt_action = query['action_id']
        elif receipt['status'] in {'stopped', 'not-created'} or slot.get('business_settled'):
            recovery = store.receipt_key(receipt_current, actor=data['actor_ref'], kind='review:reconcile',
                subject=slot['action_id'], invocation=receipt['call_ref'])
            store.reserve(recovery, recovery=True)
            self._receipt_action = recovery
        else:
            store.require_reserve(slot['action_id'])
            self._receipt_action = slot['action_id']
        store = state_store.open_store(self.path, receipt_action=self._receipt_action)
        self.bind_review_receipt(data['axis'], slot, receipt, data['actor_ref'])
        retained = self.retain_review_receipt(slot, data, original, key, receipt_current)
        self.apply_review_receipt(slot, retained, original, key, receipt_current, semantic_raw=receipt['raw'])
        return self.advance_stop(self.stop_intent()) if self.stop_intent() else _view(s)

    def suspend(self, cancel=False):
        s = self.state
        if cancel:
            for saved in s.get("deferred_decisions", {}).values():
                saved["disposition"] = "cancelled"
        if s["status"] == "cancelled":
            if cancel and s.get("stop_requested") != "cancelling":
                s["stop_requested"] = "cancelling"
                self.save()
            return _view(s, acknowledged=True)
        if s.get("stop_requested") == "cancelling" or s["status"] == "cancelling":
            if s["status"] != "cancelling":
                s["status"] = "cancelling"
                self.save()
            return _view(s, acknowledged=True)
        if not cancel and s["status"] in {"pausing", "paused"}:
            return _view(s, acknowledged=True)
        require(s["status"] not in {"accepted", "cancelled"}, "attempt_ended", "accepted results cannot be cancelled or paused")
        self.revoke_host()
        s["suspended_step"] = s["step"]
        s["status"] = "cancelling" if cancel else "pausing"
        s["stop_requested"] = s["status"]
        if s["dispatch"] is None or s["step"] == "launch":
            if cancel and s["dispatch"] is not None:
                self.cancel_control()
            else:
                s["status"] = "cancelled" if cancel else "paused"
            s["stopped"] = True
        elif s["stopped"]:
            if cancel:
                self.cancel_control()
            else:
                s["status"] = "paused"
        elif self.bound_ref() is not None:
            s["step"] = "bound"
            if not s.get('transport_loss') and not (cancel and s.get('stop_effects')):
                # The stop/query action persists this intent with its own
                # recovery reservation, even when ordinary capacity is full.
                return self.advance_stop(s['status'])
        self.save()
        return self.advance()

    def restore_paused_step(self):
        s = self.state
        s['step'] = s.get('suspended_step', 'bound')
        if s['transaction'] is None and s['dispatch'] and s['step'] not in {
                'launch', 'prepare-dispatch', 'received', 'publication-ready', 'publication-complete', 'technical-error'} and not s.get('publication'):
            s['step'] = 'continue'
            s['resume_intent'] = {'operation':'resume-original-paused-scope', 'subject':self.subject()}

    def resume_control(self, request):
        result = self.control_action(request)
        if ((result.get('next_action') or {}).get('operation') == 'control-effects' and
                request == self.state.get('deferred_validation')):
            # An identical already-consumed result ACKs before control_action's
            # normal consumption path; retire only this deferred work pointer.
            self.state.pop('deferred_validation')
            self.save()
        # Reconciliation is not the stopped dispatcher's business continuation.
        # A control action that already issued its own followup keeps that action.
        if (self.state.get('resume_intent') is not None and self.state['step'] == 'continue' and
                (result.get('next_action') or {}).get('operation') == 'control-effects'):
            return self.resume()
        return result

    def verify_direct_resume(self):
        s = self.state
        checkpoint = s['control']['context'].get('handoff_progress') or {}
        if not control.direct_implementation(checkpoint):
            return
        stop = s['host'].get('last_stop') or {}
        require(checkpoint['implementation_policy'] == s['handoff']['semantic'].get('implementation_policy') and
                stop.get('receipt', {}).get('ref') == self.bound_ref() and
                stop['receipt'].get('attempt') == s['control']['context']['carrier']['attempt'] and stop.get('provenance'),
                'implementation_escalation_required', 'direct resume requires unchanged original policy and stopped identity')
        control_git.verify_stopped_snapshot(Path(checkpoint['binding']['worktree']), stop.get('git_snapshot'),
            control_git.recovery_snapshot(Path(checkpoint['binding']['worktree']), checkpoint)[0])

    def resume(self):
        s = self.state
        if self.outer.get("runner_request", {}).get("operation") in {"pause", "cancel"}:
            return self.advance_stop(self.stop_intent())
        if s.get("recovery_action"):
            return _view(s)
        for identity, slot in s.get("allocations", {}).items():
            if slot.get('raw_ingress') and not slot['raw_ingress']['resolved'] and slot.get('transaction') is None:
                return self.allocation_raw_lookup(identity, slot)
            if slot.get("transaction") is not None:
                request = slot["transaction"]
                operation = request["operation"]
                data = {"allocation_id": identity, "operation": operation}
                if operation == 'prepare':
                    data['handoff'] = (slot.get('repair_proposal') or {}).get('slot', slot)['handoff']
                else:
                    store = state_store.open_store(self.path)
                    current = store.open_current()
                    reference = request.get('observation_ref')
                    require(reference in slot['observations'], 'invalid_checkpoint',
                            'allocation intent must pin its committed observation')
                    value = store.read_evidence(reference, run_id=current.run_id)['value']
                    require(isinstance(value, dict) and set(value) == {'allocation_observation'},
                            'invalid_evidence', 'original allocation observation required')
                    observed = value['allocation_observation']
                    require(isinstance(observed, dict) and observed.get('allocation_id') == identity and
                            observed.get('operation') == operation and
                            request['intent_id'] == entry.digest([identity, operation, observed]),
                            'identity_mismatch', 'allocation observation differs from its original intent')
                    data = observed
                return _view(s, {"operation": "allocation-recovery", "ref": slot["owner_ref"], "data": data,
                                 "checkpoint": str(self.path)})
        pending_control = list(self.unresolved_control_transactions().values())
        require(len(pending_control) <= 1, "control_outcome_unknown", "reconcile each exact outstanding control operation before advancing")
        if pending_control and self.stop_intent() is None:
            return self.resume_control(pending_control[0]["request"])
        recovery = (s['control']['context'].get('handoff_progress') or {}).get('recovery')
        if recovery and recovery['state'] != 'activated' and self.stop_intent() is None:
            return self.recovery_view()
        if s.get("stop_requested") == "cancelling":
            if s["status"] == "cancelled":
                return _view(s, acknowledged=True)
            s["status"] = "cancelling"
            self.save()
            return self.advance()
        require(s["status"] not in {"cancelled", "cancelling"}, "attempt_ended", "cancelled writers require the existing controller recovery protocol")
        if self.stop_intent() == "pausing" and s["status"] != "paused":
            query = s['host'].get('query')
            if query and query.get('resolved'):
                if self.unresolved_host_actions() or s['host']['status'] == 'unknown':
                    # Refreshing an ended lookup is recovery, not business
                    # admission. Its own reserve also persists the retry intent.
                    return self.query_host(retry=True)
                s['host'].setdefault('query_history', []).append(copy.deepcopy(query))
                s['host']['query'] = None
                self.save()
            return self.advance_stop("pausing")
        if s["status"] == "paused":
            require(s["stopped"] is True, "host_evidence_missing", "completed pause requires original stopped-writer evidence")
            self.verify_direct_resume()
        # Explicit resume may retry an ended query only after stop admission.
        # Do this before any business replay/accepted/publication early return.
        # Advance and duplicate receipts never refresh a negative query.
        query = s["host"].get("query")
        if ((query or {}).get("resolved") and not s.get("business_block") and
                (s["status"] != "accepted" or self.unresolved_host_actions() or not s['stopped'])):
            if self.unresolved_host_actions() or s['host']['status'] == 'unknown':
                return self.query_host(retry=True)
            s["host"].setdefault("query_history", []).append(copy.deepcopy(query))
            s["host"]["query"] = None
            self.save()
        if (s["status"] == "paused" or s["status"] == "active" and
                (s.get('resume_intent') or {}).get('operation') == 'resume-original-paused-scope'):
            s.pop("stop_requested", None)
            s["status"] = "active"
            if pending_control:
                self.restore_paused_step()
            self.save()  # Explicit unpause is durable before deferred recovery.
            if pending_control:
                return self.resume_control(pending_control[0]['request'])
            if s.get("deferred_business_recovery") is not None:
                self.recover_business(s["deferred_business_recovery"])
                return self.advance()
            if s.get("business_block") and s["transaction"] is None:
                return _view(s)
            if s["transaction"] is None and s.get("pending") is not None:
                s["status"] = "needs_input"
                deferred = s.get("deferred_decisions", {}).get(s["pending"]["decision_id"])
                if deferred is not None and deferred["disposition"] == "paused":
                    return self.decide(deferred["decision"])
                self.save()
                return _view(s)
            if s["transaction"] is None and s.get("deferred_observation") is not None:
                observed = s.pop("deferred_observation")
                s["status"] = "active"
                self.call("receive", _source=observed, receipt=observed["receipt"], result=observed["result"])
                return self.advance()
            # Resuming host execution still needs proof. Replaying an already
            # issued B transaction does not resume or recreate its carrier.
            self.restore_paused_step()
            # Persist explicit unpause before any original B transaction can
            # replay/consume and return early. The exact envelope stays unchanged.
            self.save()
        if self.unresolved_host_actions() and (s["status"] == "accepted" or s.get("blocked_from") == "accepted"):
            return self.query_host()
        if s["stage"] == 4 and s["status"] == "blocked" and s.get("blocked_from") == "accepted":
            verify_completion({**s, "status": "accepted"})
            s["status"] = "accepted"
            s.pop("error", None)
            self.save()
            return _view(s, acknowledged=True)
        if s['status'] == 'accepted' and not s['stopped']:
            return self.query_host()
        if s["status"] == "accepted":
            return _view(s, acknowledged=True)
        if s["status"] == "needs_input" and s["transaction"] is None:
            return _view(s, acknowledged=True)
        require(s["status"] != "needs_input" or s["transaction"] is not None, "decision_required", "answer the exact pending matter")
        if s["transaction"] is not None:
            self.replay_transaction()
            return self.advance()
        if s.get("deferred_business_recovery") is not None:
            self.recover_business(s["deferred_business_recovery"])
        if s.get("business_block"):
            return _view(s)
        if (s.get("publication") or s.get("publication_candidate")) and (s["host"]["status"] != "stopped" or self.unresolved_host_actions()):
            return self.query_host()
        if s.get("publication") is not None and s["publication"].get("result") is None:
            return self.reconcile_publication(resume=True)
        if s["step"] == "publication-complete":
            return self.receive_publication()
        if s["step"] == "publication-ready":
            return self.publication(_view(s)["next_action"]["data"])
        if s["step"] == "technical-error":
            s["status"] = "blocked"
            self.save()
            return _view(s)
        if s["status"] != "accepted":
            s["status"] = "active"
        s.pop("error", None)
        self.save()
        return self.advance()


def prepare_requirement(path, outer, request):
    """Persist A's exact intent before writes, and reconcile that same intent."""
    def save():
        if isinstance(outer, state_store.ProjectedRecord):
            committed = save_outer_record(path, {key: value for key, value in outer.items() if key != KEY},
                                          expected_revision=outer.authority_revision)
            outer.authority_revision = committed.revision
        else:
            atomic_save(path, outer)

    entry.fields(request, {"request"})
    original = copy.deepcopy(request["request"])
    require(original.get("protocol") == requirement.PROTOCOL and original.get("operation") in {"prepare", "verify"},
            "invalid_request", "supply A's complete prepare or verify request")
    root = original["entry"]["host"]["project_path"]
    require(not Path(path).resolve().is_relative_to(Path(root).resolve()), "unsafe_checkpoint", "checkpoint must be outside the project")
    identity = entry.digest(original)
    records = outer.setdefault("workflow_requirements", {})
    saved = records.get(identity)
    if saved is None:
        saved = {"request": original, "intent": None, "result": None, "error": None}
        records[identity] = saved
        save()
    try:
        if original["operation"] == "verify":
            saved["result"] = requirement.handle(original)
        else:
            if saved["intent"] is None:
                saved["intent"] = requirement.handle(original)
                save()
            operation = "reconcile" if saved.get("issued") else original["purpose"]
            saved["issued"] = True
            save()
            outcome = requirement.handle({"protocol": requirement.PROTOCOL, "operation": operation,
                            "entry": original["entry"], "intent": saved["intent"]})
            if operation == "reconcile" and outcome.get("state") == "prepared":
                outcome = requirement.handle({"protocol": requirement.PROTOCOL, "operation": original["purpose"],
                                "entry": original["entry"], "intent": saved["intent"]})
            saved["result"] = outcome
        saved["error"] = None
    except entry.ERROR_TYPES as error:
        saved["error"] = {"code": getattr(error, "code", "requirement_failed"), "message": entry.error_message(error),
                          "completed_evidence": getattr(error, "completed_evidence", []), "downstream_ready": False}
        save()
        return {"protocol": PROTOCOL, "status": "blocked", "transaction": identity, "error": saved["error"], "downstream_ready": False}
    save()
    return {"protocol": PROTOCOL, "status": "requirement-ready", "transaction": identity, "result": saved["result"]}


def deliver_requirement(path, outer, data):
    entry.fields(data, {"request"})
    original = copy.deepcopy(data["request"])
    require(isinstance(original, dict), "invalid_request", "delivery request must be an object")
    require(original.get("protocol") == requirement_delivery.PROTOCOL and original.get("operation") == "prepare",
            "invalid_request", "complete delivery prepare request required")
    for root in (original["entry"]["host"]["project_path"], original["target"]["repository"]):
        require(not Path(path).resolve().is_relative_to(Path(root).resolve()), "unsafe_checkpoint", "checkpoint must be outside source and target")
    identity = entry.digest(original)
    saved = outer.setdefault("workflow_requirements", {}).setdefault(identity,
        {"kind": "delivery", "request": original, "intent": None, "result": None, "state": "prepared"})
    atomic_save(path, outer)
    try:
        for other_id, other in outer["workflow_requirements"].items():
            if (other_id != identity and other.get("kind") == "delivery" and other.get("intent") is not None
                    and other["request"]["target"] == original["target"]
                    and (other.get("result") is None or other.get("error") is not None)):
                raise entry.PreparationError("delivery_in_progress", "reconcile the original target delivery transaction before preparing another")
        def admit_write():
            state = outer.get(KEY)
            require(outer.get("runner_request", {}).get("operation") not in {"pause", "cancel"},
                    "progression_suspended", "resume original owner before delivery writes")
            if state is not None:
                owner = Progress(path, outer)
                owner.require_not_stopping()
                require(not owner.unresolved_host_actions() and state.get("transaction") is None,
                        "host_action_pending", "reconcile pending side effects before delivery")
                actor = state["handoff"]["expected_entry"]["actor"]
                require(original["entry"]["host"]["thread_id"] == actor["thread_id"], "identity_mismatch", "delivery must retain original attempt actor")
            require(saved.get("result") is None and not (saved.get("error") or {}).get("completed_evidence"),
                    "delivery_outcome_unknown", "saved delivery results or observed objects require successful read-only reconciliation before any new write")
        if saved["intent"] is None:
            admit_write()
            saved["intent"] = requirement_delivery.handle(original)
            atomic_save(path, outer)
        def invoke(operation):
            return requirement_delivery.handle({"protocol": requirement_delivery.PROTOCOL,
                "operation": operation, "entry": original["entry"], "intent": saved["intent"]})
        outcome = invoke("reconcile")
        for _ in range(2):
            if outcome.get("state") == "verified":
                break
            admit_write()
            if outcome.get("state") == "refresh-required":
                previous = saved["intent"]
                refreshed = requirement_delivery.handle(original)
                saved.setdefault("prior_intents", []).append(previous)
                saved["intent"] = refreshed
                saved["issued"] = False
                saved["state"] = "prepared"
                atomic_save(path, outer)
            saved.update(issued=True, state="issued")
            atomic_save(path, outer)
            outcome = invoke("deliver")
        require(outcome.get("state") == "verified", "target_changed", "target repeatedly advanced; retain original transaction")
        saved["result"] = outcome
        saved["state"] = "delivered"
        atomic_save(path, outer)
        saved["result"] = requirement_delivery.handle({"protocol": requirement_delivery.PROTOCOL, "operation": "verify",
            "entry": original["entry"], "result": outcome})
        saved.update(state="verified", error=None)
    except entry.ERROR_TYPES as error:
        saved["state"] = "issued" if saved.get("issued") else "prepared"
        evidence = list(getattr(error, "completed_evidence", []))
        identities = {(item.get("operation_id"), item.get("intent_digest"), item.get("commit")) for item in evidence}
        for item in (saved.get("error") or {}).get("completed_evidence", []):
            identity_key = (item.get("operation_id"), item.get("intent_digest"), item.get("commit"))
            if identity_key not in identities:
                evidence.append(item)
                identities.add(identity_key)
        saved["error"] = {"code": getattr(error, "code", "delivery_failed"), "message": entry.error_message(error),
            "completed_evidence": evidence, "downstream_ready": False}
        atomic_save(path, outer)
        return {"protocol": PROTOCOL, "status": "blocked", "transaction": identity, "error": saved["error"], "downstream_ready": False}
    atomic_save(path, outer)
    return {"protocol": PROTOCOL, "status": "delivery-ready", "transaction": identity, "result": saved["result"]}


def lifecycle(path, outer, data):
    """Retain original actor/envelope; only source completion chains finalize."""
    bounded = isinstance(outer, state_store.ProjectedRecord)
    require(bounded or KEY not in outer, 'legacy_run_requires_original_runtime',
            'lifecycle requires the original bounded Controller or a new outer record')
    owner = Progress(path, outer) if bounded else None

    def save(*, progress_changed=False):
        if not bounded:
            atomic_save(path, outer)
        elif progress_changed:
            owner.save()
            outer.authority_revision = owner._authority_revision
        else:
            committed = save_outer_record(path, {key: value for key, value in outer.items() if key != KEY},
                                          expected_revision=outer.authority_revision)
            outer.authority_revision = committed.revision
            owner._authority_revision = committed.revision

    entry.fields(data, {"request"})
    request = copy.deepcopy(data["request"])
    allowed = {"claim-phase-carrier", "phase-ready", "phase-activate", "claim-phase-completion",
               "complete-phase-run", "finalize-phase-run", "reconcile-phase-run", "read-phase-run"}
    require(request.get("operation") in allowed, "invalid_operation", "use an existing phase carrier/source operation")
    root = request["project_path"]
    state = outer.get(KEY)
    if state is not None:
        current = state["handoff"]["expected_entry"]
        require(root == entry.discussion_root(current) and
                all(request.get(key) == current["requirement"]["attachment"][key]
                    for key in ("project_id", "tree_id", "actor_topic_id")),
                "discussion_identity_conflict", "lifecycle envelope differs from pinned discussion project")
    require(not Path(path).resolve().is_relative_to(Path(root).resolve()), "unsafe_checkpoint", "checkpoint must be outside the project")
    if request["operation"] in {"complete-phase-run", "finalize-phase-run"}:
        state = outer.get(KEY)
        require(state is not None and state["status"] == "accepted", "result_incomplete", "accept B's result before source phase completion")
        phase = state["handoff"]["authorization"].get("phase")
        if phase is not None:
            require(request["phase_run_id"] == phase["run_id"] and request["attempt_id"] == phase["attempt_id"],
                    "identity_mismatch", "phase completion must name the accepted stage attempt")
    records = outer.setdefault("workflow_lifecycle", {})
    identity = entry.digest(request)
    saved = records.setdefault(identity, {"request": request, "result": None, "finalize": None})
    save()
    # The ledger authenticates actor, binding, revisions, gates and exact replay.
    # Replaying the same envelope reads its original idempotent receipt.
    if saved["result"] is None or request["operation"] == "read-phase-run":
        saved["result"] = entry.discussion_protocol.handle(copy.deepcopy(saved["request"]))
        save()
    if request["operation"] == "complete-phase-run":
        if saved["finalize"] is None:
            result = saved["result"]
            saved["finalize"] = {**request, "operation": "finalize-phase-run",
                "expected_ledger_revision": result["ledger_revision"], "expected_topic_revision": result["record_revision"],
                "idempotency_key": str(uuid.uuid4())}
            save()
        if saved.get("finalized") is None:
            saved["finalized"] = entry.discussion_protocol.handle(copy.deepcopy(saved["finalize"]))
            save()
    state = outer.get(KEY)
    action = (state or {}).get("action")
    action_pending = (action and action["operation"] in {"source-lifecycle", "carrier-lifecycle"} and
                      action["payload"]["request"] == request and action.get("receipt") != saved)
    if request["operation"] in {"complete-phase-run", "finalize-phase-run"}:
        verify_phase_completed(state)
        if not state["phase_complete"]:
            state["pending"] = None
            state["phase_complete"] = True
            save(progress_changed=True)
        # Phase completion can precede readiness. Its receipt does not prove
        # that this acceptance's stepwise boundary was issued or consumed.
        boundary_subject = {"stage": state["next_stage"], "predecessor": state["accepted"]["digest"]}
        confirmed = any(decision.get("subject") == boundary_subject and
                        decision.get("answer") in {"confirm", "continuous"}
                        for decision in state.get("decisions", {}).values())
        if not confirmed and state["pending"] is None and (state["stage"] != 2 or scope_readiness(state, path)):
            pending = stage_boundary(state["mode"], state["stage"], state["accepted"], state["next_stage"])
            if pending is not None:
                state["pending"] = pending
                save(progress_changed=True)
    if action_pending:
        action["receipt"] = copy.deepcopy(saved)
        state["step"] = "accepted" if state["status"] == "accepted" else action["payload"].get("return_step", "bound")
        save(progress_changed=True)
    return {"protocol": PROTOCOL, "status": "lifecycle-observed", "transaction": identity, "result": saved}


def handle(path, request):
    entry.fields(request, {"protocol", "operation", "expected_revision"}, {"data"})
    if request.get('operation') == 'host-preflight':
        return _handle(path, request)
    # Keep counting through owner cleanup. Returning from inside the inner
    # lock must not omit a dependency/ledger or reserve-release read.
    with state_store.measure_reads() as totals:
        result = _handle(path, request)
    reading = {'structured_bytes': totals['bytes'], 'structured_files': totals['files'],
        'coverage': totals['coverage'], 'external_read_bytes': 'unknown', 'external_read_files': 'unknown'}
    result['reading'] = reading
    action = result.get('next_action') or {}
    if 'reading' in action:
        action['reading'].update(reading)
    return result


def _handle(path, request):
    entry.fields(request, {"protocol", "operation", "expected_revision"}, {"data"})
    require(request["protocol"] == PROTOCOL, "legacy_run_requires_original_runtime", "retain original progression runtime " + str(request.get("protocol")))
    require(type(request["expected_revision"]) is int, "invalid_request", "exact revision required")
    if request['operation'] == 'host-preflight':
        # Read-only capability check, usable before creating a flow or checkpoint.
        return {'protocol': PROTOCOL, 'status': 'host-ready',
                'native_host': native_host_preflight(request.get('data'))}
    path = Path(path)
    require(path.is_absolute(), "invalid_checkpoint", "absolute checkpoint path required")
    data = request.get("data")
    if request["operation"] == "control" and isinstance(data, dict):
        require(data.get("action") not in ("scope-seal", "scope-revise"),
                "invalid_operation", "use the dedicated scope-seal or scope-revise operation")
    if request["operation"] == "scope-revise":
        require_scope_correction_available(read_record(path))
    with record_lock(path), state_store.operation_store(path):
        outer = read_active_owner(path)
        if request["operation"] == "scope-revise":
            require_scope_correction_available(outer)
        require(outer.get("version") in (None, 11), "legacy_run_requires_original_runtime", "progression requires a standalone record or runner version 11")
        member = outer.get(KEY)
        pinned_protocol = outer.get("confirmed", {}).get("packages", {}).get("runner", {}).get("compatibility_key", {}).get("workflow_progress")
        require((member is None or isinstance(member, dict) and member.get("protocol") == PROTOCOL) and
                (pinned_protocol is None or pinned_protocol == PROTOCOL),
                "legacy_run_requires_original_runtime", "all operations require the original pinned progression runtime")
        if member is not None:
            require(member.get("control",{}).get("context",{}).get("schema_version") == 7,
                    "legacy_run_requires_original_runtime", "retain the original embedded control runtime")
        if request["operation"] == "deliver-requirement":
            return deliver_requirement(path, outer, request.get("data", {}))
        if request["operation"] == "prepare-requirement":
            return prepare_requirement(path, outer, request.get("data", {}))
        if request["operation"] == "lifecycle":
            return lifecycle(path, outer, request.get("data", {}))
        owner = Progress(path, outer)
        if owner.state is not None:
            validate_state(owner.state)
        actual = owner.state["revision"] if owner.state else 0
        operation = request["operation"]
        if operation == "inspect":
            require(owner.state is not None, "missing_checkpoint", "no progression checkpoint")
            return _view(owner.state)
        # Exact duplicate observations/decisions may ACK even with an old revision.
        data = request.get("data", {})
        if operation in {"prepare-plan-revision", "commit-plan-revision", "reconcile-plan-revision"}:
            require(isinstance(data, dict) and isinstance(data.get("revision_id"), str) and
                    bool(data["revision_id"].strip()), "invalid_request",
                    "revision_id requires a nonempty string")
        review_duplicate = owner.state is not None and operation == 'review-activity' and isinstance(data, dict) and any(
            slot.get('prepare') == data or any(receipt['response_ref'] == (data.get('receipt') if isinstance(data.get('receipt'), dict) else {}).get('response_ref')
                                              for receipt in slot['receipts'])
            for group in [owner.state.get('review_activity', {}), *owner.state.get('review_history', [])]
            for slot in group.values())
        duplicate = review_duplicate or owner.state is not None and ((operation == 'native-finalize' and any(
            record.get('prepare') == data or any(native_receipt_matches(saved, data) for saved in record['receipts']) for record in owner.state.get('native_finalizations', []))) or (operation == "observe" and
            owner.state["events"].get(data.get("event_id"), {}).get("applied")) or
            (operation == "scope-propose" and data.get("proposal_id") in owner.state.get("scope_proposals", {})) or
            (operation == "scope-seal" and data.get("proposal_id") in owner.state.get("scope_seals", {})) or
            (operation == "scope-revise" and (data.get("revision_id") in owner.state.get("scope_revisions", {}) or
             data.get("revision_id") == (owner.state.get("scope_revision_transaction") or {}).get("input", {}).get("revision_id"))) or
            (operation in {"prepare-plan-revision", "commit-plan-revision", "reconcile-plan-revision"} and
             (data.get("revision_id") == (owner.state.get("plan_revision_transaction") or {}).get("input", {}).get("revision_id") or
              data.get("revision_id") in owner.state.get("plan_revision_history", {}))) or
            (operation == "recover-business" and data.get("decision_id") in owner.state.get("recovery_decisions", {})) or
            (operation == "decide" and (data.get("decision_id") in owner.state.get("decisions", {}) or
             owner.state.get("deferred_decisions", {}).get(data.get("decision_id"), {}).get("decision") == data)))
        require(actual == request["expected_revision"] or duplicate, "stale_checkpoint", "read the current checkpoint before changing it")
        if actual != request['expected_revision'] and operation == 'native-finalize' and data.get('operation') == 'prepare':
            record = next(item for item in owner.state['native_finalizations'] if item['prepare'] == data)
            return _view(owner.state, native_finalization_lookup(record), acknowledged=True)
        if operation == "start":
            return owner.start(data)
        require(owner.state is not None, "missing_checkpoint", "start a progression checkpoint first")
        # Reject malformed adapter events before even the raw observation
        # journal changes. All three ingress paths share the same shape rules.
        if operation == 'host-event':
            require(isinstance(data,dict), 'invalid_result', 'native event data must be an object')
            if owner.state.get('native_host', {}).get('adapter') == 'collaboration':
                validate_collaboration_event(data.get('event'))
            else:
                validate_native_event(data.get('event'))
        elif operation == 'lifecycle-state' and owner.state.get('native_host', {}).get('adapter') != 'collaboration':
            validate_snapshot_events(data)
        elif operation == 'observe' and isinstance(data,dict) and 'lifecycle' in data:
            require(owner.state.get('native_host', {}).get('adapter') != 'collaboration',
                    'invalid_result', 'record collaboration inventory through lifecycle-query and lifecycle-state')
            validate_snapshot_events(data['lifecycle'])
        try:
            if operation == "advance": return owner.advance()
            if operation == 'implementation-recovery': return owner.implementation_recovery(data)
            if operation == "scope-propose": return owner.scope_propose(data)
            if operation == "scope-seal": return owner.scope_seal(data)
            if operation == "scope-revise": return owner.scope_revise(data)
            if operation == "prepare-plan-revision": return owner.prepare_plan_revision(data)
            if operation == "commit-plan-revision": return owner.commit_plan_revision(data)
            if operation == "reconcile-plan-revision": return owner.reconcile_plan_revision(data)
            if operation == "observe": return owner.observe(data)
            if operation == "decide": return owner.decide(data)
            if operation == "publication": return owner.publication(data)
            if operation == "reconcile-publication":
                entry.fields(data, set())
                return owner.reconcile_publication()
            if operation == "receive-publication":
                entry.fields(data, set())
                return owner.receive_publication()
            if operation == "recover-business": return owner.recover_business(data)
            if operation == "control": return owner.control_action(data)
            if operation == "allocation": return owner.allocation(data)
            if operation == 'review-activity': return owner.review_activity(data)
            if operation == 'lifecycle-state': return owner.lifecycle_state(data)
            if operation == 'lifecycle-query': return owner.lifecycle_query(data)
            if operation == 'baseline-thread-query': return owner.baseline_thread_query(data)
            if operation == 'baseline-thread-state': return owner.baseline_thread_state(data)
            if operation == 'native-finalize': return owner.native_finalize(data)
            if operation == 'host-event': return owner.host_event(data)
            if operation == 'transport-lost': return owner.transport_lost(data)
            if operation == 'carrier-failure': return owner.carrier_failure(data)
            if operation == "pause": return owner.suspend()
            if operation == "cancel": return owner.suspend(cancel=True)
            if operation == "resume": return owner.resume()
            raise entry.PreparationError("invalid_operation", "unknown progression operation")
        except entry.ERROR_TYPES + (control.ControlError,) as error:
            if error is owner._save_error:
                raise
            if operation in {'native-finalize', 'review-activity'}:
                # Admission/conflicting evidence cannot publish an unreserved
                # review intent through the generic error-block save.
                raise
            if (isinstance(error, _RejectedPlanRevision) or
                    operation in {"scope-propose", "scope-seal", "scope-revise",
                              "prepare-plan-revision", "commit-plan-revision",
                              "reconcile-plan-revision"} or
                    getattr(error, "code", None) in {"scope_revision_pending", "scope_conflict", "stale_scope_revision"}):
                raise
            if operation == 'control' and data.get('action') in {'archive', 'archive-result'}:
                return _view(owner.state, {'operation': 'control-effects', 'result': {'ok': False,
                    'handoff_id': data['evidence'].get('handoff_id'),
                    'error': {'code': getattr(error, 'code', 'archive_recovery_required'), 'message': entry.error_message(error)}}})
            if operation == "decide" and owner.stop_intent() is not None and getattr(error, "code", None) in {"stale_decision", "decision_conflict", "invalid_request"}:
                raise
            if owner.state["status"] in {"accepted", "cancelled"} or getattr(error, "code", None) == "progression_suspended":
                raise
            return owner.block(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args()
    try:
        request = entry.decode(sys.stdin.buffer.read(entry.MAX_BYTES + 1))
        result = handle(args.checkpoint, request)
        print(json.dumps({"ok": result["status"] != "blocked", "result": result}, ensure_ascii=False))
        return 1 if result["status"] == "blocked" else 0
    except entry.ERROR_TYPES + (control.ControlError, RecursionError) as error:
        print(json.dumps({"ok": False, "error": {"code": getattr(error, "code", "progress_failed"),
            "message": entry.error_message(error), "downstream_ready": False}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
