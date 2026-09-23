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
import json
import os
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
import workflow_control as control
import skill_preflight
import supervision_protocol as supervision

PROTOCOL = "workflow-progress-v8"
KEY = "workflow_progress"
CHECKPOINT_LOCK_TIMEOUT = 5.0
NATIVE_SOURCE_KINDS = ('cli', 'vscode', 'exec', 'appServer', 'subAgent', 'subAgentReview',
                       'subAgentCompact', 'subAgentThreadSpawn', 'subAgentOther', 'unknown')
require = entry.require


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
    # Checkpoints contain accumulated raw evidence, unlike bounded single inputs.
    with Path(path).open(encoding="utf-8") as stream:
        value = json.load(stream)
    require(isinstance(value, dict), "invalid_checkpoint", "checkpoint must be an object")
    return value


def atomic_save(path, value):
    path = Path(path)
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
    entry.fields(decision, {"decision_id", "subject", "answer", "reference"})
    require(pending is not None and decision["decision_id"] == pending["decision_id"] and
            decision["subject"] == pending["subject"], "stale_decision", "answer does not name the current pending matter")
    entry.nonempty(decision["reference"]); entry.nonempty(decision["answer"])


def stage_boundary(mode, stage, accepted, next_stage=None):
    require(mode in {"continuous", "stepwise"}, "invalid_mode", "explicit flow mode required")
    return None if mode == "continuous" or stage == 4 else pending_decision(
        "stage-entry", {"stage": next_stage if next_stage is not None else stage + 1, "predecessor": accepted["digest"]},
        "Confirm entry to Stage " + str(next_stage if next_stage is not None else stage + 1))


def validate_state(state):
    require(isinstance(state, dict) and state.get("protocol") == PROTOCOL,
            "legacy_run_requires_original_runtime", "use original " + str(state.get("protocol") if isinstance(state,dict) else None) + "; package digests " + str({k:v.get("bundle_digest") for k,v in (state.get("packages",{}) if isinstance(state,dict) else {}).items()}))
    require(type(state.get("revision")) is int and state["revision"] >= 0,
            "invalid_checkpoint", "checkpoint revision is invalid")
    require(state.get("mode") in {"stepwise", "continuous"}, "invalid_checkpoint", "invalid mode")
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
    require(isinstance(state.get("retained_host_actions", []), list),
            "invalid_checkpoint", "retained host actions must be an ordered list")
    control.validate_context(state["control"]["context"])
    for pin in state["packages"].values():
        skill_preflight.verify_identity(pin)
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


def _view(state, next_action=None, acknowledged=False):
    if state.get("business_block") and state["status"] == "blocked" and (next_action or {}).get('operation') not in {
            'invoke-recovery-host', 'lookup-recovery-host', 'activate-dispatch-recovery', 'prepare-dispatch-recovery', 'control-effects', 'await-host-recovery'}:
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
    result = {"protocol": PROTOCOL, "revision": state["revision"], "status": state["status"],
              "step": state["step"], "pending": state.get("pending"), "next_action": next_action,
              "downstream_ready": state["status"] == "accepted" and state.get('stopped', False) and state.get("phase_complete", True) and
                  bool((state.get("accepted") or {}).get("downstream_ready")), "acknowledged": acknowledged}
    if state["status"] == "accepted" and state.get('stopped', False) and state.get("accepted") is not None and state["accepted"]["downstream_ready"] and state.get("phase_complete", True):
        if state["stage"] == 4:
            result["workflow_completion"] = verify_completion(state)
        result["completion"] = handoff.render(state["accepted"])
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
        selected = control.selected_control(state["control"]["context"],
            {"attempt": state["dispatch"]["request"]["attempt"]})
        authority = (selected.get("handoff_progress") or {}).get("plan", {}).get("entry_authority", {})
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
    # Historical worktrees are gone. Verify immutable predecessor links and their
    # saved acceptances/phases, never demand the old worktree HEAD again.
    previous = saved.get("predecessor")
    while previous is not None:
        handoff.unseal(previous)
        prior = previous["handoff"]
        matches = [item for item in state.get("history", []) if (item.get("accepted") or {}).get("digest") == previous["digest"]]
        if matches:
            require(len(matches) == 1 and matches[0]["status"] == "accepted", "result_changed", "ambiguous prior acceptance")
            verify_phase_completed(matches[0])
        elif prior["authorization"].get("phase") is not None:
            verify_phase_completed({"handoff": prior})
        if prior["stage"] == 2:
            root = saved["binding"]["repository"]
            handoff.ancestor(root, previous["payload"]["planning_commit"], previous["payload"]["planning_merge_commit"])
            handoff.ancestor(root, previous["payload"]["planning_merge_commit"], payload["merge_commit"])
        previous = prior.get("predecessor")
    return {"completed": True, "merge_commit": payload["merge_commit"], "cleanup": actual["cleanup"],
            "acceptance": accepted["digest"], "phase_complete": True}


class Progress:
    def __init__(self, path, outer):
        self.path, self.outer = Path(path), outer
        self.state = outer.get(KEY)

    def save(self):
        # Compatibility projection, never a business-replay liveness input.
        # Before any issue, or after exact non-creation, there is no writer.
        root_stopped = (self.state["host"]["status"] == "stopped" or
                                 self.state.get("host_action") is None or
                                 (self.state.get("dispatch") or {}).get("status") == "not-created")
        self.state['stopped'] = root_stopped and not self.stop_barrier_pending()
        if not self.state['stopped'] and self.state['status'] in {'paused','cancelled'}:
            self.state['status'] = 'cancelling' if self.stop_intent() == 'cancelling' else 'pausing'
        if self.state.get("business_block") and self.state["status"] not in {"pausing", "paused", "cancelling", "cancelled"}:
            self.state["status"] = "blocked"
            self.state["error"] = copy.deepcopy(self.state["business_block"]["error"])
        self.state["revision"] += 1
        self.outer[KEY] = self.state
        atomic_save(self.path, self.outer)

    def stop_barrier_pending(self):
        """Derive writer closure from the existing roles and invocation journal."""
        s = self.state
        pending = []
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
        if s.get('stage', 0) >= 2 and s.get('dispatch') and self.bound_ref() is not None:
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
                refs = {self.bound_ref()}
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

    def lifecycle_state(self, snapshot, *, advance=True):
        entry.fields(snapshot, {'instance','carrier_thread','sequence','pages','events'})
        entry.nonempty(snapshot['instance'])
        expected = self.outer.get('sessions', {}).get('stage' + str(self.state['stage'])) or self.state['handoff']['expected_entry']['actor']['thread_id']
        require(snapshot['carrier_thread'] == expected, 'identity_mismatch', 'lookup must retain the original carrier ancestor')
        previous = self.state.get('lifecycle_snapshot')
        require(previous is None or previous['instance'] == snapshot['instance'], 'host_recovery_required', 'a new host cannot replace current native evidence')
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
        self.state['lifecycle_snapshot'] = copy.deepcopy(snapshot)
        self.save()
        if (self.state['status'] == 'blocked' and self.state.get('error', {}).get('code') == 'host_evidence_missing' and
                not self.state.get('business_block') and not self.stop_barrier_pending()):
            self.state['status'] = self.state.get('blocked_from', 'active')
            self.state.pop('error', None)
            self.save()
        return self.advance_stop(self.stop_intent()) if advance and self.stop_intent() else _view(self.state)

    def host_event(self, data):
        entry.fields(data, {'instance','event'}, {'carrier_thread'})
        transport = self.outer.get('transport')
        require(transport is not None and data['instance'] == transport.get('instance'),
                'identity_mismatch', 'native event belongs to the current foreground connection')
        event = data['event']
        validate_native_event(event)
        item = event['params']['item']
        carrier = data.get('carrier_thread')
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
        require(self.stop_intent() is None, "progression_suspended",
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
        require(self.outer.get('transport', {}).get('state') != 'lost', 'host_recovery_required', 'lost foreground transport cannot create a native role')
        entry.fields(data, {"handoff"}, {"control", "next_stage", "requirement_transaction"})
        request = copy.deepcopy(data["handoff"])
        require(request.get("operation") == "prepare", "invalid_request", "handoff prepare input required")
        old = self.state
        retry = old is not None and old["status"] == "cancelled" and (old.get("dispatch") or {}).get("status") == "not-created" and not old.get("stop_requested")
        if old is not None:
            validate_state(old)
            require(old.get('transport_loss') is None, 'host_recovery_required', 'retain the original foreground host recovery point')
            require(old["status"] == "accepted" or retry, "run_active", "finish the current stage or reconcile non-creation before a new attempt")
            if not retry:
                require(not self.unresolved_host_actions(), "host_action_pending",
                        "settle original host actions before starting another stage")
                require(old.get('stopped') and not self.stop_barrier_pending(), 'host_evidence_missing', 'recheck the complete original native barrier before the successor')
                verify_phase_completed(old)
            predecessor = old["handoff"]["predecessor"] if retry else old["accepted"]
            request.setdefault("predecessor", predecessor)
            request.setdefault("requirement", old["handoff"]["requirement"] if retry or old["stage"] >= 2 else old["accepted"]["payload"]["requirement"])
            require(request["predecessor"] == predecessor, "predecessor_changed", "retain the original predecessor")
            require(request["stage"] == (old["stage"] if retry else old["next_stage"]), "illegal_route", "use the authorized stage")
            require(old.get("pending") is None, "decision_required", "confirm the saved stage boundary first")
            if retry:
                require(request["authorization"]["reference"] != old["handoff"]["authorization"]["reference"],
                        "decision_required", "a new controller retry decision must distinguish the failed request")
                require(all(request[key] == old["handoff"][key] for key in ("role", "scope", "target", "binding", "semantic")),
                        "scope_changed", "non-creation retry cannot widen or replace the original work")
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
        for identity in self.outer.get("confirmed", {}).get("packages", {}).values():
            if "name" in identity:
                retained[identity["name"]] = identity
        retain_entry_pins(request, retained)
        # A resolves/rechecks actual cwd and complete host registration before B.
        resolved = entry.resolve({**request["entry"], "operation": "verify", "expected": request["expected_entry"]})
        mode = request["authorization"]["flow_mode"]
        require(old is None or mode == old["mode"], "authorization_changed", "mode cannot change without its exact decision")
        for root in (resolved["repository"]["root"], (request.get("binding") or {}).get("worktree")):
            require(root is None or not self.path.resolve().is_relative_to(Path(root).resolve()),
                    "unsafe_checkpoint", "checkpoint must live outside repository and disposable worktree")
        saved = handoff.handle(request)
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
                            saved["scope"]["implementation_paths"] + saved["scope"]["closure_paths"]),
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
        control.validate_context(port["context"])
        next_stage = data.get("next_stage", old["next_stage"] if retry else 2 if request["stage"] == 0 and mode == "continuous" else request["stage"] + 1)
        require(request["stage"] == 4 or (request["stage"], next_stage) in {(0, 1), (0, 2), (1, 2), (2, 3), (3, 4)},
                "illegal_route", "select a legal next stage")
        phase_required = "phase" in request["authorization"]
        if request["stage"] < 2:
            selector = request["authorization"].get("control_plan_id")
            selected = control.selected_control(port["context"], {"control_plan_id": selector} if selector else {})
            phase_required = (selected.get("handoff_progress") or {}).get("plan", {}).get("entry_authority", {}).get("kind") == "wrapper-phase-run"
        self.state = {"protocol": PROTOCOL, "revision": old["revision"] if old else 0,
            "mode": mode, "stage": request["stage"], "status": "active", "step": "prepare-dispatch",
            "next_stage": next_stage, "phase_complete": not phase_required,
            "packages": {**retained, **resolved["packages"]}, "handoff": saved, "control": copy.deepcopy(port),
            "dispatch": None, "accepted": None, "action": None, "pending": None,
            "events": {}, "observations": [], "transaction": None, "transaction_source": None,
            "transaction_result": None, "stopped": False,
            "retained_host_actions": [],
            "host": {"generation": 0, "status": "unknown", "proof": None, "query": None,
                     "seen": {}, "calls": {}, "last_stop": None},
            "history": (old["history"] + [{k: copy.deepcopy(value) for k, value in old.items() if k != "history"}]) if old else []}
        self.save()
        return self.advance()

    def call(self, operation, *, _source=None, **extra):
        s = self.state
        self.require_controls_reconciled()
        require(s["transaction"] is None, "transaction_pending", "consume the original B transaction before new business input")
        if operation in {"receive", "accept"}:
            self.require_business_ready()
        if operation == 'accept':
            require(s['stopped'] and not self.stop_barrier_pending(), 'host_evidence_missing', 'acceptance requires all original native calls and writers to be closed')
            if s['stage'] == 3:
                payload = s['dispatch']['delivery']['message']['payload']
                slots = s.get('review_activity', {})
                require(set(slots) == {'standards','spec'} and all(
                    slots[axis]['candidate'] == payload['candidate_commit'] and slots[axis]['stopped'] and
                    slots[axis]['ref'] == payload['review'][axis]['reviewer_ref'] for axis in slots),
                    'review_incomplete', 'both stopped native reviewers must match the exact candidate')
        request = {"protocol": handoff.PROTOCOL, "operation": operation, "control": copy.deepcopy(s["control"]), **extra}
        request["handoff" if operation == "prepare" else "record"] = copy.deepcopy(s["handoff"] if operation == "prepare" else s["dispatch"])
        s["transaction"], s["transaction_source"], s["transaction_result"] = request, copy.deepcopy(_source), None
        self.save()
        return self.replay_transaction()

    def replay_transaction(self):
        s = self.state
        require(s["transaction"] is not None, "transaction_missing", "no original B transaction")
        require(s["control"]["context"] == s["transaction"]["control"]["context"],
                "authority_changed", "original B replay cannot overwrite subsequent controller recovery")
        if s["transaction_result"] is None:
            self.apply(dispatch.handle(copy.deepcopy(s["transaction"])))
        result = copy.deepcopy(s["transaction_result"])
        self.consume_transaction()
        return result

    def apply(self, result):
        # Save the result independently. Recovery can consume it without calling
        # B again; the original request/envelope remains until atomic consumption.
        self.state["transaction_result"] = copy.deepcopy(result)
        self.save()

    def consume_transaction(self):
        s = self.state
        request, result, source = s["transaction"], s["transaction_result"], s["transaction_source"]
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
            s["pending"] = stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"]) if s["accepted"]["downstream_ready"] and s["phase_complete"] else None
            s.setdefault("decisions", {})[source["decision_id"]] = copy.deepcopy(source)
            s["last_decision"] = copy.deepcopy(source)
        if source is not None and "event_id" in source:
            s["events"][source["event_id"]]["applied"] = True
            if "result" in source:
                s.setdefault("observed_messages", {})[self.message_key(source)] = source["event_id"]
        s["transaction"], s["transaction_source"], s["transaction_result"] = None, None, None
        s["last_result"] = result
        self.save()

    def message_key(self, data):
        return entry.digest({"request": self.state["dispatch"]["request"]["digest"],
                             "ref": data["receipt"]["ref"], "result": data["result"]})

    def revoke_host(self):
        host = self.state["host"]
        host["generation"] += 1
        host["proof"], host["query"] = None, None

    def record_host(self, data):
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
        provenance = data.get("provenance")
        # Unavailable/malformed causal records cannot hide adverse host facts.
        valid = (isinstance(provenance, dict) and set(provenance) == {"call_ref", "response_ref", "action_id"}
                 and all(isinstance(value, str) and value.strip() for value in provenance.values()))
        conflict = False
        duplicate = False
        if valid:
            response_key = entry.digest([receipt["adapter"], provenance["response_ref"]])
            call_key = entry.digest([receipt["adapter"], provenance["call_ref"]])
            identity = {"provenance": provenance,
                        "receipt": {key: value for key, value in receipt.items() if key != "receipt_ref"},
                        "action_resolution": data.get("action_resolution")}
            fingerprint = entry.digest(identity)
            previous = host["seen"].get(response_key)
            conflict = ((previous is not None and previous != fingerprint) or
                        (call_key in host["calls"] and host["calls"][call_key] != provenance["action_id"]))
            duplicate = previous == fingerprint and not conflict
            if not conflict and not duplicate:
                host["seen"][response_key] = fingerprint
                host["calls"][call_key] = provenance["action_id"]
        if duplicate:
            event["host_applied"] = True
            self.save()
            return
        action = host.get("query") or s.get("host_action")
        causal = (valid and not conflict and action is not None and
                  data.get("action_id") == provenance["action_id"] == action["action_id"] and
                  action["generation"] == host["generation"] and not action.get("resolved"))
        if causal and action["operation"] == "inspect-host-state" and action["payload"].get("unresolved_action"):
            outstanding = action["payload"]["unresolved_action"]
            retained = next((item for item in self.unresolved_host_actions()
                             if item["action_id"] == outstanding["action_id"]), None)
            resolution = data.get("action_resolution")
            settled = (isinstance(resolution, dict) and set(resolution) == {"action_id", "outcome"} and
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
        if receipt["status"] == "stopped":
            host["last_stop"] = copy.deepcopy(observation)
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
        # Unordered stops retain an existing barrier, never replace causal proof.
        if causal or status != "stopped":
            host["observation"] = observation
        resumable = status == 'stopped' and receipt['raw'].get('resumable') is True
        host["proof"] = copy.deepcopy(observation) if causal and (status in {"idle", "turn-completed"} or resumable) else None
        if action is not None and action["operation"] == "inspect-host-state" and (causal or status == "unknown"):
            # Retain a negative query result so advance waits rather than loops.
            action["resolved"] = True
            host["query"] = action
        event["host_applied"] = True
        event["host_conflict"] = conflict
        self.save()
        require(not conflict, "host_provenance_conflict", "tool provenance was reused with different evidence or cause")

    def unresolved_control_transactions(self):
        return {identity:value for identity,value in self.state.get('control_transactions',{}).items()
                if value.get('result') is None and value.get('rejection') is None}

    def require_controls_reconciled(self, replay_identity=None):
        require(not (set(self.unresolved_control_transactions()) - {replay_identity}),
                'control_outcome_unknown', 'reconcile the exact original control transaction before another state change')

    def require_business_ready(self, *, control_replay=False):
        if not control_replay:
            self.require_controls_reconciled()
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

    def continuation_gate(self):
        self.require_business_ready()
        s, host = self.state, self.state["host"]
        self.require_not_stopping()
        require(s["step"] == "continue" and s["status"] == "active" and s["transaction"] is None,
                "cannot_continue", "continuation requires consumed business intent")
        require(s.get("continuation_intent") is not None or s.get("resume_intent") is not None,
                "cannot_continue", "a host receipt alone cannot authorize business continuation")
        require(self.bound_ref() is not None, "identity_mismatch", "retain the original bound identity")
        authority = {"record": s["dispatch"], "control": copy.deepcopy(s["control"])}
        dispatch.record_for(authority)
        require(authority["control"]["context"] == s["control"]["context"] and
                s["handoff"] == s["dispatch"]["handoff"],
                "authority_changed", "continuation requires the original current control and scope")
        ref, state, _ = dispatch.matching(s["dispatch"], authority["control"]["context"])
        require(ref == self.bound_ref() and state not in {"cancelled", "creation-failed", "completed"},
                "authority_changed", "original control must still authorize this identity")
        current = handoff.refresh(s["handoff"]["entry"], s["handoff"]["expected_entry"], after_work=True)
        if s["stage"] >= 2:
            handoff.source(current, s["handoff"]["requirement"], s["stage"])
        handoff.phase_evidence(s["handoff"], role_ref=self.bound_ref())
        outstanding = s.get("host_action")
        if (outstanding is not None and outstanding["operation"] == "continue-host" and
                not outstanding.get("resolved")):
            if outstanding["generation"] == host["generation"]:
                return _view(s, {"operation": "lookup-exact-action", "action": outstanding})
        if self.unresolved_host_actions():
            return self.query_host()
        proof = host["proof"]
        if proof is not None and proof["generation"] == host["generation"] and proof["receipt"]["ref"] == self.bound_ref():
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

    def query_host(self):
        s, host = self.state, self.state["host"]
        outstanding = self.unresolved_host_actions()
        if (host.get("query") or {}).get("settled_action_id") and outstanding:
            # One authenticated response settles one action, not the entire
            # chain. Continue read-only reconciliation of the next original.
            host["query"] = None
        if host["query"] is None:
            host["query"] = {"action_id": str(uuid.uuid4()), "operation": "inspect-host-state",
                "generation": host["generation"], "payload": {"ref": self.bound_ref(), "subject": self.subject(),
                    "request": copy.deepcopy(s["dispatch"]["request"])}, "checkpoint": str(self.path)}
            if outstanding:
                host["query"]["payload"]["unresolved_action"] = copy.deepcopy(outstanding[0])
            self.save()
        query = host["query"]
        return _view(s, {"operation": "await-host-recovery", **self.recovery_requirements(), "query": query}
                     if query.get("resolved") else query)

    def next_envelope(self, receipt):
        if receipt is not None and self.state["control"]["discussion"] is not None:
            # New logical mutations get a new key; a saved transaction keeps its
            # original envelope until its idempotent result has been recovered.
            self.state["control"]["discussion"] = {**self.state["control"]["discussion"],
                "expected_ledger_revision": receipt["ledger_revision"],
                "expected_topic_revision": receipt["record_revision"], "idempotency_key": str(uuid.uuid4())}

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
            return _view(s)
        if s["step"] == "prepare-dispatch":
            if s["dispatch"] is None:
                result = self.call("prepare")
                if "record" not in result:
                    return self.block(entry.PreparationError("outcome_unknown", "recover the original dispatch request from its checkpoint owner"))
            s["step"] = "launch"
            self.save()
        if s["step"] == "launch":
            self.verify_launch()
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
            handoff.verify(saved)
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

    def observe(self, data):
        entry.fields(data, {"event_id", "receipt"}, {"result", "action_id", "provenance", "action_resolution", "closure", "publication_candidate", "lifecycle"})
        s, receipt = self.state, data["receipt"]
        entry.nonempty(data["event_id"])
        digest = entry.digest(data)
        previous = s["events"].get(data["event_id"])
        if previous is not None:
            require(previous["digest"] == digest, "event_conflict", "event ID reused with different content")
            if previous["applied"]:
                return _view(s, acknowledged=True)
        # Keep even invalid/late raw receipts for exact recovery, not as authority.
        if previous is None:
            s["events"][data["event_id"]] = {"digest": digest, "applied": False}
            s["observations"].append(copy.deepcopy(data))
            self.save()
        dispatch.receipt_for(s["dispatch"], receipt, {"create", "lookup", "result"})
        if receipt["event"] == "result":
            require(receipt["ref"] == self.bound_ref(), "identity_mismatch", "observation must name the bound role")
            require(receipt["status"] in {"running", "idle", "turn-completed", "stopped", "unknown"},
                    "invalid_result", "invalid host execution state")
            self.record_host(data)
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
                    observed_publication = supervision.reconcile_publication(self.publication_transaction())
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
                        "ref": s["handoff"]["predecessor"]["role_ref"], "binding": s["handoff"]["binding"],
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
        s = self.state
        prior = s.get("decisions", {}).get(data.get("decision_id"))
        if prior is not None:
            require(prior == data, "decision_conflict", "decision cannot be replaced")
            return _view(s, acknowledged=True)
        self.require_business_ready()
        decision_matches(s.get("pending"), data)
        pending = s["pending"]
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
            s["pending"] = None
        else:
            s["pending"], s["status"], s["step"] = None, "active", "continue"
            s["continuation_intent"] = {"kind": "decision", "decision": copy.deepcopy(data)}
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
            predecessor = handoff.unseal(saved["predecessor"])
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
            payload.update(candidate_commit=s["handoff"]["predecessor"]["payload"]["candidate_commit"],
                           merge_commit=result["merge_commit"], cleanup=supervision._cleanup_facts(binding),
                           changed_paths=supervision._changed_paths(Path(binding["repository"]), scope["baseline"], result["merge_commit"]))
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

    def control_action(self, data):
        entry.fields(data, {"action", "evidence", "receipt"})
        entry.nonempty(data["action"])
        require(data["action"] in {"successor-ready", "archive", "archive-result",
                    "prepare-dispatch-recovery", "dispatch-recovery-intent", "dispatch-recovery-result", "recover-dispatch", "release-recovery-allocation", "candidate-ready", "review-converged", "validation-start", "validation-result", "validation-retry", "invalidate-candidate"},
                "invalid_operation", "use the original bounded control recovery/closure operation")
        require(isinstance(data["receipt"], dict) and data["receipt"], "host_evidence_missing", "original authenticated host/controller evidence required")
        s = self.state
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
            require(s['stage'] == 3 and not s.get('publication') and not s.get('transport_loss'),
                    'host_recovery_required', 'retain original host and stage; publication must reconcile first')
            handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
            require(not self.unresolved_host_actions() and not self.stop_barrier_pending(),
                    'host_evidence_missing', 'reconcile all original calls and prove every old writer stopped')
            stop = s['host'].get('last_stop')
            require(s['host']['status'] == 'stopped' and stop is not None and stop.get('provenance') is not None,
                    'host_evidence_missing', 'current causal stop observation required')
            raw = stop['receipt']['raw']
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
        if data["action"] in {"candidate-ready", "review-converged", "validation-start", "validation-result", "validation-retry", "invalidate-candidate"}:
            self.require_business_ready(control_replay=True)
            self.require_not_stopping()
            current = handoff.refresh(s['handoff']['entry'], s['handoff']['expected_entry'], after_work=True)
            require(current['actor']['thread_id'] == s['handoff']['expected_entry']['actor']['thread_id'], 'identity_mismatch', 'original controller required')
            if data['action'] == 'validation-start' and self.unresolved_host_actions():
                return self.query_host()
            data = copy.deepcopy(data)
            if data['action'] == 'review-converged':
                slots = s.get('review_activity', {})
                require(set(slots) == {'standards','spec'}, 'review_incomplete', 'both native review axes required')
                native = {}
                for axis, slot in slots.items():
                    review = data['evidence']['review'][axis]
                    require(slot['stopped'] and slot['ref'] == review['reviewer_ref'] and slot['candidate'] == data['evidence']['candidate'], 'review_incomplete', 'native reviews must stop at exact candidate')
                    receipts = [r for r in slot['receipts'] if r['response_ref'] == review['result_ref'] and r['status'] == 'stopped']
                    require(len(receipts) == 1, 'review_incomplete', 'semantic result must name original stopped response')
                    native[axis] = receipts[0]
                data['evidence']['native_evidence'] = native
            if data['action'] in {'candidate-ready', 'validation-result'}:
                data['evidence']['source'] = copy.deepcopy(data['receipt'])
            if data['action'] == 'invalidate-candidate' and (s['control']['context'].get('handoff_progress') or {}).get('state') == 'validating':
                require(data['receipt'].get('raw',{}).get('stopped') is True, 'host_evidence_missing', 'stop and reconcile the validation command before invalidation')
                data['evidence']['stopped'] = True
            if data['action'] in {'validation-retry', 'invalidate-candidate'}:
                require(not self.unresolved_host_actions(), 'host_action_pending', 'reconcile original calls before validation recovery')
                require(all(v.get('stopped') for v in s.get('review_activity', {}).values()), 'review_active', 'stop old native reviewers before recovery')
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
            return _view(s, {"operation": "control-effects", "result": previous["result"]}, acknowledged=True)
        self.require_controls_reconciled(identity)
        require(s['transaction'] is None and not any(slot.get('transaction') is not None for slot in s.get('allocations',{}).values()),
                'transaction_pending', 'reconcile original B/allocation transactions before control mutation')
        transactions.setdefault(identity, {"request": copy.deepcopy(data), "port": copy.deepcopy(s["control"]),
                                            "status": s["status"], "result": None,
                                            "business_block_id": (s.get("business_block") or {}).get("id")})
        require(transactions[identity]['port']['context'] == s['control']['context'],
                'control_context_changed', 'retain the original result without overwriting newer control authority')
        self.save()
        # Retain exact ledger envelope on a lost response; never promote slots by hand.
        try:
            result, applied = dispatch.checkpoint(transactions[identity]["port"], s["handoff"], data["action"], data["evidence"])
        except control.ControlError as error:
            # This pure/Git read-only boundary definitively rejected before any
            # durable control mutation. OS/transport/ledger failures stay unknown.
            if transactions[identity]['port']['discussion'] is None:
                transactions[identity]['rejection'] = str(error)
                self.save()
            raise
        s["control"]["context"], s["control_receipt"] = result["context"], applied
        self.next_envelope(applied)
        transactions[identity]["result"] = result
        if data["action"] == "validation-result":
            s.pop("deferred_validation", None)
        if s["status"] == "blocked":
            s["status"] = transactions[identity]["status"] if transactions[identity]["status"] != "blocked" else s.get("blocked_from","active")
            s.pop("error", None)
        if data["action"] == "recover-dispatch":
            recovery = result['recovery']
            old_record = copy.deepcopy(s['dispatch'])
            record_body = handoff.unseal(old_record)
            launch = copy.deepcopy(handoff.unseal(old_record['request']))
            s.setdefault('recovery_dispatch_history', []).append(old_record)
            launch['attempt'] = result['context']['carrier']['attempt']
            launch['payload']['dispatch_recovery'] = {k:copy.deepcopy(recovery[k]) for k in
                ('recovery_id','snapshot_digest','remaining_paths','revalidate','ownership')}
            s['dispatch'] = handoff.seal({**record_body, 'request':handoff.seal(launch),
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
        if data['action'] == 'validation-start':
            s.update(step='continue', status='active', continuation_intent={'kind':'final-validation', 'attempt_id':data['evidence']['attempt_id']})
        if data['action'] == 'dispatch-recovery-result' and s.get('transport_loss'):
            s['status'] = self.stop_intent() or 'blocked'
            s['recovery_action'] = {'operation':'await-host-recovery', **self.recovery_requirements(),
                                    'reason':s['transport_loss']['reason']}
        self.save()
        if data['action'] == 'dispatch-recovery-result' and s.get('transport_loss'):
            return _view(s, s['recovery_action'])
        if data['action'] == 'dispatch-recovery-intent':
            return self.recovery_view(issued=bool(result['effects']))
        if data['action'] == 'validation-start':
            return self.advance()
        return _view(s, {"operation": "control-effects", "result": result})

    def recovery_view(self, *, issued=False, acknowledged=False):
        recovery = self.state['control']['context']['handoff_progress']['recovery']
        if recovery['state'] == 'activated':
            return _view(self.state, {'operation':'wait-host', 'ref':self.bound_ref()}, acknowledged=acknowledged)
        operation = ('invoke-recovery-host' if issued else 'lookup-recovery-host') if recovery['state'] in {
            'dispatch-pending', 'dispatch-unknown'} else 'activate-dispatch-recovery' if recovery['state'] == 'ready' else 'prepare-dispatch-recovery'
        request = None if recovery['intent'] is None else {**recovery['intent'],
            'checkpoint':str(self.path), 'handoff_field':KEY + '.handoff', 'control_field':KEY + '.control',
            'registration_input':copy.deepcopy(self.state['dispatch']['request']['payload']['registration_input'])}
        return _view(self.state, {'operation': operation, 'recovery_id': recovery['recovery_id'],
            'request': request, 'receipts': recovery['receipts'], 'write_authority': False}, acknowledged=acknowledged)

    def allocation(self, data):
        """B transport records share the dispatcher's existing control roster."""
        entry.fields(data, {"allocation_id", "operation"}, {"handoff", "receipt", "result", "decision"})
        identity = entry.nonempty(data["allocation_id"])
        operation = data["operation"]
        require(operation in {"prepare", "bind", "reconcile", "receive", "accept"}, "invalid_operation", "unknown allocation operation")
        s = self.state
        require(s["stage"] == 3 and s["handoff"]["role"] == "implementation-dispatcher", "role_mismatch", "allocation belongs to the Stage-3 dispatcher")
        self.require_controls_reconciled()
        slots = s.setdefault("allocations", {})
        slot = slots.get(identity)
        if operation == "prepare":
            entry.fields(data, {"allocation_id", "operation", "handoff"})
            if slot is not None:
                require(slot["input"] == data["handoff"], "allocation_conflict", "retain the original allocation intent")
                if slot["record"] is not None:
                    if slot.get("action") is None:
                        self.require_allocation_active()
                        handoff.verify(slot["handoff"])
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
                require(request["binding"] == s["handoff"]["binding"] and request["target"] == s["handoff"]["target"],
                        "binding_changed", "allocation must retain its dispatcher's flow")
                retain_entry_pins(request, s["packages"])
                actor = request["expected_entry"]["actor"]
                require(actor["role"] == "implementation-dispatcher" and self.bound_ref() in
                        {actor["thread_id"], "codex-thread:" + actor["thread_id"], actor.get("actor_ref")},
                        "identity_mismatch", "only the bound native dispatcher allocates execution agents")
                saved = handoff.handle(request)
                parent_scope, scope = s["handoff"]["scope"], saved["scope"]
                require(scope["implementation_paths"] == parent_scope["implementation_paths"] and
                        scope["closure_paths"] == parent_scope["closure_paths"] and
                        set(scope["owned_paths"]) <= set(parent_scope["implementation_paths"]) and
                        set(parent_scope["protected_paths"]) <= set(scope["protected_paths"]),
                        "scope_changed", "allocation must retain parent scope and protections")
                handoff.ancestor(saved['expected_entry']['repository']['root'], parent_scope['baseline'], scope['baseline'])
                slot = {"input": copy.deepcopy(data["handoff"]), "handoff": saved, "record": None, "owner_ref": self.bound_ref(),
                        "transaction": None, "observations": [], "result": None}
                slots[identity] = slot
                self.save()
        else:
            require(slot is not None and slot["record"] is not None, "allocation_missing", "exact saved allocation required")
            require(not (slot['record']['status'] == 'not-created' and (data.get('receipt') or {}).get('ref') is not None),
                    'attempt_ended', 'a late ref cannot bind a proven non-created allocation')
            required = {"allocation_id", "operation", "decision"} if operation == "accept" else {"allocation_id", "operation", "receipt"}
            if operation == "receive": required.add("result")
            entry.fields(data, required)
            slot["observations"].append(copy.deepcopy(data))
            self.save()
        if operation == "prepare":
            self.require_allocation_active()
        request = {"protocol": handoff.PROTOCOL, "operation": operation, "control": copy.deepcopy(s["control"])}
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
        slot["transaction"] = copy.deepcopy(request)
        self.save()
        result = dispatch.handle(request)
        if "checkpoint" in result:
            s["control"]["context"] = result["checkpoint"]["context"]
            s["control_receipt"] = result["checkpoint"].get("discussion_receipt")
            self.next_envelope(s["control_receipt"])
        slot["record"] = result.get("record", slot["record"])
        slot["transaction"], slot["result"] = None, result
        if (data.get('receipt') or {}).get('status') == 'stopped':
            s.get('native_adverse', {}).pop(data['receipt']['ref'], None)
        if s.get("stop_requested") == "cancelling" and s["stopped"]:
            if all(item["stopped"] for item in s["control"]["context"]["handoff_progress"]["executions"]):
                s["status"], s["stop_effects"] = "cancelled", []
        self.save()
        if self.stop_intent() is not None and s['stopped']:
            self.advance_stop(self.stop_intent())
        if operation == "prepare":
            require(slot["record"] is not None, "outcome_unknown", "recover the original allocation reservation")
            return self.issue_allocation(identity, slot)
        else:
            action = {"operation": "allocation-result", "allocation_id": identity, "result": result,
                      "checkpoint": str(self.path)}
        return _view(s, action, acknowledged=result.get("acknowledged", False))

    def issue_allocation(self, identity, slot):
        self.require_allocation_active()
        action = {"action_id": str(uuid.uuid4()), "operation": "invoke-host", "allocation_id": identity,
                  "payload": slot["record"]["request"], "checkpoint": str(self.path)}
        slot["action"] = action
        self.save()
        return _view(self.state, action)

    def require_allocation_active(self):
        self.require_not_stopping()

    def review_activity(self, data):
        """Two fixed review slots; allocation and candidate acceptance stay in B."""
        entry.fields(data, {'operation','axis','candidate','actor_ref'}, {'verification','action_id','receipt'})
        s = self.state
        require(s['stage'] == 3 and data['axis'] in {'standards','spec'}, 'role_mismatch', 'only the Stage-3 review axes are supported')
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
            self.require_not_stopping()
            self.require_business_ready()
            control.validate_reviewable(s['control']['context']['handoff_progress'], data['candidate'], data['verification'])
            binding, scope = s['handoff']['binding'], s['handoff']['scope']
            observed = supervision.verify_worktree({'binding':binding,'platform_cwd':binding['worktree']})
            require(observed['current_commit'] == data['candidate'] and not supervision._full_status(Path(binding['worktree'])),
                    'candidate_changed', 'review requires the clean exact candidate')
            require(supervision._branch_oid(Path(binding['repository']), binding['target_branch']) == s['control']['context']['handoff_progress']['expected_target_head'], 'target_changed', 'review target changed')
            changed = supervision._changed_paths(Path(binding['repository']), s['control']['context']['handoff_progress']['expected_target_head'], data['candidate'])
            require(set(changed) <= set(scope['implementation_paths']) and not set(changed) & set(scope['protected_paths']),
                    'scope_changed', 'review candidate escaped implementation scope')
            if any(value['candidate'] != data['candidate'] for value in slots.values()):
                require(all(value.get('stopped') for value in slots.values()), 'review_active', 'stop and reconcile both old review axes first')
                s.setdefault('review_history', []).append(copy.deepcopy(slots))
                s['review_activity'] = slots = {}
                slot = None
            if slot is not None:
                require(slot['prepare'] == data, 'review_conflict', 'retain the original review intent')
                return _view(s, {'operation':'lookup-exact-review','axis':data['axis'], 'action_id':slot['action_id'], 'ref':slot.get('ref')}, acknowledged=True)
            result, applied = dispatch.checkpoint(s['control'], s['handoff'], 'review-start', {'candidate':data['candidate']})
            s['control']['context'] = result['context']
            self.next_envelope(applied)
            slot = {'candidate':data['candidate'], 'originating_ref':data['actor_ref'], 'prepare':copy.deepcopy(data),
                    'action_id':str(uuid.uuid4()), 'ref':None, 'stopped':False, 'receipts':[]}
            slots[data['axis']] = slot
            self.save()
            return _view(s, {'operation':'invoke-review','axis':data['axis'], 'candidate':data['candidate'], 'action_id':slot['action_id']})
        entry.fields(data, {'operation','axis','candidate','actor_ref','action_id','receipt'})
        require(data['operation'] == 'observe' and slot is not None and slot['candidate'] == data['candidate'] and
                slot['action_id'] == data['action_id'], 'review_missing', 'observe the original exact review call')
        receipt = data['receipt']
        entry.fields(receipt, {'adapter','call_ref','response_ref','status','ref','raw'})
        for key in ('adapter','call_ref','response_ref'):
            entry.nonempty(receipt[key])
        require(isinstance(receipt['raw'], dict) and receipt['raw'], 'host_evidence_missing', 'raw authenticated native receipt required')
        require(receipt['status'] in {'running','stopped','unknown','not-created'}, 'invalid_result', 'invalid native review state')
        previous = next((value for value in slot['receipts'] if value['response_ref'] == receipt['response_ref']), None)
        require(previous is None or previous == receipt, 'review_conflict', 'response identity cannot change')
        if previous is not None:
            return _view(s, acknowledged=True)
        if receipt['status'] == 'not-created':
            require(slot['ref'] is None and receipt['ref'] is None, 'identity_mismatch', 'bound reviewer cannot become not-created')
        elif receipt['ref'] is not None:
            entry.nonempty(receipt['ref'])
            require(slot['ref'] in {None,receipt['ref']} and receipt['ref'] not in {self.bound_ref(),data['actor_ref']},
                    'identity_mismatch', 'reviewer must retain an independent native identity')
            require(all(axis == data['axis'] or value.get('ref') != receipt['ref'] for axis,value in slots.items()),
                    'identity_mismatch', 'review axes require separate identities')
            slot['ref'] = receipt['ref']
        require(receipt['status'] != 'stopped' or slot['ref'] is not None, 'host_evidence_missing', 'stopped review needs an actual ref')
        slot['receipts'].append(copy.deepcopy(receipt))
        slot['stopped'] = receipt['status'] in {'stopped','not-created'}
        if slot['stopped']:
            s.get('native_adverse', {}).pop(slot['ref'], None)
        self.save()
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

    def resume(self):
        s = self.state
        if self.outer.get("runner_request", {}).get("operation") in {"pause", "cancel"}:
            return self.advance_stop(self.stop_intent())
        if s.get("recovery_action"):
            return _view(s)
        for identity, slot in s.get("allocations", {}).items():
            if slot.get("transaction") is not None:
                request = slot["transaction"]
                operation = request["operation"]
                data = {"allocation_id": identity, "operation": operation}
                data.update({"handoff": slot["input"]} if operation == "prepare" else
                            {key: request[key] for key in ("receipt", "result", "decision") if key in request})
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
                s['host'].setdefault('query_history', []).append(copy.deepcopy(query))
                s['host']['query'] = None
                self.save()
            return self.advance_stop("pausing")
        if s["status"] == "paused":
            require(s["stopped"] is True, "host_evidence_missing", "completed pause requires original stopped-writer evidence")
        # Explicit resume may retry an ended query only after stop admission.
        # Do this before any business replay/accepted/publication early return.
        # Advance and duplicate receipts never refresh a negative query.
        query = s["host"].get("query")
        if ((query or {}).get("resolved") and not s.get("business_block") and
                (s["status"] != "accepted" or self.unresolved_host_actions() or not s['stopped'])):
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
        atomic_save(path, outer)
    try:
        if original["operation"] == "verify":
            saved["result"] = requirement.handle(original)
        else:
            if saved["intent"] is None:
                saved["intent"] = requirement.handle(original)
                atomic_save(path, outer)
            operation = "reconcile" if saved.get("issued") else original["purpose"]
            saved["issued"] = True
            atomic_save(path, outer)
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
        atomic_save(path, outer)
        return {"protocol": PROTOCOL, "status": "blocked", "transaction": identity, "error": saved["error"], "downstream_ready": False}
    atomic_save(path, outer)
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
    atomic_save(path, outer)
    # The ledger authenticates actor, binding, revisions, gates and exact replay.
    # Replaying the same envelope reads its original idempotent receipt.
    saved["result"] = entry.discussion_protocol.handle(copy.deepcopy(saved["request"]))
    atomic_save(path, outer)
    if request["operation"] == "complete-phase-run":
        if saved["finalize"] is None:
            result = saved["result"]
            saved["finalize"] = {**request, "operation": "finalize-phase-run",
                "expected_ledger_revision": result["ledger_revision"], "expected_topic_revision": result["record_revision"],
                "idempotency_key": str(uuid.uuid4())}
            atomic_save(path, outer)
        saved["finalized"] = entry.discussion_protocol.handle(copy.deepcopy(saved["finalize"]))
        atomic_save(path, outer)
    if request["operation"] in {"complete-phase-run", "finalize-phase-run"}:
        verify_phase_completed(outer[KEY])
        if not outer[KEY]["phase_complete"]:
            state = outer[KEY]
            state["pending"] = stage_boundary(state["mode"], state["stage"], state["accepted"], state["next_stage"])
        outer[KEY]["phase_complete"] = True
        outer[KEY]["revision"] += 1
        atomic_save(path, outer)
    state = outer.get(KEY)
    action = (state or {}).get("action")
    if action and action["operation"] in {"source-lifecycle", "carrier-lifecycle"} and action["payload"]["request"] == request:
        action["receipt"] = copy.deepcopy(saved)
        state["step"] = "accepted" if state["status"] == "accepted" else action["payload"].get("return_step", "bound")
        state["revision"] += 1
        atomic_save(path, outer)
    return {"protocol": PROTOCOL, "status": "lifecycle-observed", "transaction": identity, "result": saved}


def handle(path, request):
    entry.fields(request, {"protocol", "operation", "expected_revision"}, {"data"})
    require(request["protocol"] == PROTOCOL, "legacy_run_requires_original_runtime", "retain original progression runtime " + str(request.get("protocol")))
    require(type(request["expected_revision"]) is int, "invalid_request", "exact revision required")
    path = Path(path)
    require(path.is_absolute(), "invalid_checkpoint", "absolute checkpoint path required")
    with record_lock(path):
        outer = read_record(path)
        require(outer.get("version") not in {1, 2, 3, 4}, "legacy_run_requires_original_runtime", "retain the original runner and record")
        member = outer.get(KEY)
        pinned_protocol = outer.get("confirmed", {}).get("packages", {}).get("runner", {}).get("compatibility_key", {}).get("workflow_progress")
        require((member is None or isinstance(member, dict) and member.get("protocol") == PROTOCOL) and
                (pinned_protocol is None or pinned_protocol == PROTOCOL),
                "legacy_run_requires_original_runtime", "all operations require the original pinned progression runtime")
        if member is not None:
            require(member.get("control",{}).get("context",{}).get("schema_version") == 3,
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
        duplicate = owner.state is not None and ((operation == "observe" and
            owner.state["events"].get(data.get("event_id"), {}).get("applied")) or
            (operation == "recover-business" and data.get("decision_id") in owner.state.get("recovery_decisions", {})) or
            (operation == "decide" and (data.get("decision_id") in owner.state.get("decisions", {}) or
             owner.state.get("deferred_decisions", {}).get(data.get("decision_id"), {}).get("decision") == data)))
        require(actual == request["expected_revision"] or duplicate, "stale_checkpoint", "read the current checkpoint before changing it")
        if operation == "start":
            return owner.start(data)
        require(owner.state is not None, "missing_checkpoint", "start a progression checkpoint first")
        # Reject malformed adapter events before even the raw observation
        # journal changes. All three ingress paths share the same shape rules.
        if operation == 'host-event':
            require(isinstance(data,dict), 'invalid_result', 'native event data must be an object')
            validate_native_event(data.get('event'))
        elif operation == 'lifecycle-state':
            validate_snapshot_events(data)
        elif operation == 'observe' and isinstance(data,dict) and 'lifecycle' in data:
            validate_snapshot_events(data['lifecycle'])
        try:
            if operation == "advance": return owner.advance()
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
            if operation == 'host-event': return owner.host_event(data)
            if operation == 'transport-lost': return owner.transport_lost(data)
            if operation == 'carrier-failure': return owner.carrier_failure(data)
            if operation == "pause": return owner.suspend()
            if operation == "cancel": return owner.suspend(cancel=True)
            if operation == "resume": return owner.resume()
            raise entry.PreparationError("invalid_operation", "unknown progression operation")
        except entry.ERROR_TYPES + (control.ControlError,) as error:
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
