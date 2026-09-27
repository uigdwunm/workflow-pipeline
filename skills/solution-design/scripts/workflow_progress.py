#!/usr/bin/env python3
"""Durable foreground progression. Host calls remain authenticated adapter work.

The outer checkpoint belongs to the existing conversation/runner. Only the
workflow_progress member is owned here; discussion authority stays in its ledger.
No host tools, daemon, Git publisher or governance-private records live here.
"""
from __future__ import annotations

import argparse
import base64
import copy
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager, nullcontext

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_prepare as entry
import requirement_prepare as requirement
import requirement_delivery
import stage_handoff as handoff
import stage_dispatch as dispatch
import stage_scope
import workflow_control as control
import workflow_control_git as control_git
import skill_preflight
import supervision_protocol as supervision

PROTOCOL = "workflow-progress-v14"
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


def collaboration_inventory_pending(inventory, expected):
    agents = collaboration_inventory(inventory)
    actual = set(agents) - {inventory['controller_ref']}
    pending = []
    if actual != expected:
        pending.append({'kind': 'unreconciled-descendants', 'expected': sorted(expected), 'actual': sorted(actual)})
    for ref in sorted(actual):
        status = agents[ref]
        if not (isinstance(status, dict) and set(status) == {'completed'} and isinstance(status['completed'], str)):
            pending.append({'kind': 'native-turn-not-stopped', 'ref': ref, 'status': status})
    return pending


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


@contextmanager
def legacy_runner_lock(path):
    """Take the original runner lock before C's record lock during takeover."""
    path = Path(path).with_name(Path(path).name + ".runner.lock")
    parent = path.parent
    current = os.stat(parent, follow_symlinks=False)
    require(stat.S_ISDIR(current.st_mode) and current.st_uid == os.geteuid() and
            stat.S_IMODE(current.st_mode) & 0o077 == 0,
            "invalid_checkpoint", "legacy runner record directory must be owner-only")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        identity = os.fstat(descriptor)
        require(stat.S_ISREG(identity.st_mode) and identity.st_uid == os.geteuid() and
                identity.st_nlink == 1, "invalid_checkpoint", "legacy runner lock is not owner-owned")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise entry.PreparationError("run_busy", "original runner still owns this checkpoint") from error
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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


LEGACY_COMPATIBILITY = {"handoff": "workflow-controller-v1",
    "confirmation": "goal-scoped-confirmation-v3", "discussion_request": 1,
    "ledger_read": [1, 2, 3], "ledger_write": 3,
    "topic_gate": "phase-0-1-discussion-v2", "control": 6, "entry": "workflow-entry-v3", "runner": 6,
    "supervision": "flow-worktree-v2", "preparation": "workflow-preparation-v4",
    "stage_transfer": "workflow-stage-transfer-v7", "workflow_progress": "workflow-progress-v12",
    "requirement_delivery": "requirement-delivery-v1", "thread_settings": "thread-settings-v5"}
LEGACY_COMPATIBILITY_PRE_CONFIRMATION = {key: value for key, value in LEGACY_COMPATIBILITY.items()
                                         if key != "confirmation"}
LEGACY_COMPATIBILITY_V3 = copy.deepcopy(LEGACY_COMPATIBILITY_PRE_CONFIRMATION)
LEGACY_COMPATIBILITY_V3["preparation"] = "workflow-preparation-v3"
LEGACY_COMPATIBILITIES = (LEGACY_COMPATIBILITY, LEGACY_COMPATIBILITY_PRE_CONFIRMATION,
                          LEGACY_COMPATIBILITY_V3)


def _legacy_attached_control_raw(state):
    discussion = state["control"].get("discussion")
    attached_source = state.get("handoff", {}).get("entry", {}).get("source", {}).get("kind") == "discussion"
    require(not attached_source or discussion is not None, "authority_missing",
            "attached legacy flow lacks its original Topic workflow-control envelope")
    require(discussion is None or attached_source, "authority_missing",
            "legacy control envelope cannot attach an unrelated standalone source")
    if discussion is None:
        return None
    from discussion_core import state as ledger_state
    envelope = {key: value for key, value in discussion.items()
                if key not in {"expected_ledger_revision", "expected_topic_revision", "idempotency_key"}}
    envelope["operation"] = "read-topic"
    _, ledger_path, _, _, _ = ledger_state._evolution_paths(envelope, query=True, allow_tree_topic=True)
    _, records = ledger_state._load_records(ledger_path)
    matches = [record for record in records["Phase Results"]
               if record.get("result_kind") == "workflow-control" and
                  record.get("topic_id") == envelope["actor_topic_id"]]
    require(len(matches) == 1 and isinstance(matches[0].get("data_json"), str),
            "legacy_run_requires_original_runtime", "original attached workflow control is unavailable")
    return matches[0].get("legacy_control_json", matches[0]["data_json"])


def _verify_legacy_host(state, supplied):
    native = state.get("native_host") or {}
    old_stop = (state.get("host") or {}).get("last_stop") or {}
    require(state.get("stopped") is True and state.get("host", {}).get("status") == "stopped" and
            old_stop.get("provenance") and old_stop.get("receipt", {}).get("status") == "stopped" and
            old_stop["receipt"].get("ref") == state["accepted"].get("role_ref"),
            "host_evidence_missing", "old accepted designer lacks a causal complete stop")
    require(isinstance(supplied, dict) and supplied.get("adapter") == native.get("adapter"),
            "host_evidence_missing", "current native evidence must use the original host adapter")
    if native.get("adapter") == "app-server":
        entry.fields(supplied, {"adapter", "snapshot"})
        snapshot = supplied["snapshot"]
        expected_carrier = state["handoff"]["expected_entry"]["actor"]["thread_id"]
        validate_rpc_lifecycle(snapshot, expected_carrier)
        require(snapshot["instance"] == native.get("instance") and
                snapshot["sequence"] >= (state.get("lifecycle_snapshot") or {}).get("sequence", 0),
                "host_recovery_required", "current host differs from old pinned connection")
        descendants = [child for page in snapshot["pages"] for child in page["response"]["data"]]
        require(descendants and all(child.get("status") == {"type": "idle"} for child in descendants),
                "host_evidence_missing", "all old native descendants must currently be idle")
        pending = set()
        for event in snapshot["events"]:
            item = event["params"]["item"]
            key = (event["params"]["threadId"], item["id"])
            if event["method"] == "item/started":
                pending.add(key)
            else:
                pending.discard(key)
        require(not pending, "host_evidence_missing", "old native calls remain open")
    elif native.get("adapter") == "collaboration":
        require(not collaboration_inventory_pending(supplied,
                set(native.get("baseline_refs", [])) | {state["accepted"]["role_ref"]}),
                "host_evidence_missing", "old collaboration descendants are not all stopped")
    else:
        raise entry.PreparationError("host_capability_missing", "unsupported old native host adapter")
    require(not any(not action.get("resolved") and not action.get("creation_resolved") for action in
                [*state.get("retained_host_actions", []), state.get("host_action") or {}] if action),
            "host_action_pending", "reconcile all original host calls before takeover")


def inspect_legacy_checkpoint(path, data, *, raw=None, old_runtime=True):
    """Qualify only the v12/v7 accepted Stage-2 combination, without writes."""
    entry.fields(data, {"action", "recovery_id", "original_checkpoint_sha256",
                        "original_accepted_digest", "controller_ref", "host_evidence"},
                 {"manifest", "implementation_required", "decision", "new_packages",
                  "new_confirmed", "workspace_evidence", "generated_outputs"})
    require(data["action"] in {"inspect", "prepare-takeover"}, "invalid_operation",
            "legacy qualification requires inspect or prepare-takeover")
    path = Path(path)
    require(path.is_absolute() and str(path.resolve()) == str(path), "invalid_checkpoint",
            "canonical original checkpoint path required")
    entry.nonempty(data["recovery_id"])
    raw = path.read_bytes() if raw is None else raw
    require(hashlib.sha256(raw).hexdigest() == data["original_checkpoint_sha256"],
            "legacy_checkpoint_changed", "original checkpoint bytes differ from requested digest")
    try:
        outer = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise entry.PreparationError("invalid_checkpoint", "old checkpoint must be UTF-8 JSON") from error
    require(isinstance(outer, dict) and outer.get("version") in (None, 6),
            "legacy_run_requires_original_runtime", "only original outer runner 6 or standalone checkpoint is recoverable")
    state = outer.get(KEY)
    require(isinstance(state, dict) and state.get("protocol") == "workflow-progress-v12" and
            state.get("stage") == 2 and state.get("status") == "accepted" and
            state.get("phase_complete") is True and state.get("stopped") is True and
            state.get("accepted", {}).get("protocol") == "workflow-stage-transfer-v7" and
            state["accepted"].get("downstream_ready") is True and
            state["accepted"].get("digest") == data["original_accepted_digest"] and
            state.get("control", {}).get("context", {}).get("schema_version") == 4 and
            state["control"]["context"].get("controller_ref") == data["controller_ref"] ==
                state.get("handoff", {}).get("controller_ref"),
            "legacy_run_requires_original_runtime", "only exact accepted v12/v7 Stage-2 authority may be recovered")
    require(not any(item.get("stage", 0) >= 3 for item in state.get("history", [])) and
            not outer.get("stage_results", {}).get("stage3") and
            not outer.get("sessions", {}).get("stage3") and
            outer.get("current_stage", "stage2") == "stage2" and
            not outer.get("stage3_prepared") and
            not (outer.get("prepared_inputs") or {}).get("stage3") and
            not outer.get("runner_request") and not outer.get("pending_input") and
            not outer.get("turn_result") and
            (outer.get("launch") or {}).get("stage", "stage2") == "stage2" and
            not any(isinstance(item, dict) and (
                item.get("stage") in (3, "stage3") or item.get("current_stage") == "stage3" or
                any(isinstance(item.get(key), dict) and item[key].get("stage") == 3
                    for key in ("handoff", "request", "input")))
                for item in outer.get("history", [])) and
            not state.get("allocations") and not state.get("review_activity") and
            state.get("transaction") is None and not state.get("transport_loss") and
            not state.get("business_block") and
            all(value.get("result") is not None or value.get("rejection") is not None
                for value in state.get("control_transactions", {}).values()),
            "legacy_recovery_ineligible", "old record has Stage-3 intent or unresolved business activity")
    recorded_paths = [action["checkpoint"] for action in
        [state.get("action") or {}, state.get("host_action") or {}, *state.get("retained_host_actions", [])]
        if isinstance(action, dict) and "checkpoint" in action]
    require(recorded_paths and all(recorded == str(path) for recorded in recorded_paths),
            "legacy_checkpoint_changed", "copied old checkpoint has no matching original host-call identity")
    accepted = state["accepted"]
    require(state.get("dispatch", {}).get("acceptance") == accepted and
            accepted.get("handoff") == state["handoff"] and
            accepted.get("requirement_identity") == state["handoff"]["requirement_identity"],
            "legacy_recovery_ineligible", "old acceptance differs from its original dispatch and source")
    pins = state.get("packages") or {}
    require(isinstance(pins, dict) and pins, "legacy_run_requires_original_runtime", "old package pins are missing")
    old_key = next(iter(pins.values())).get("compatibility_key")
    require(old_key in LEGACY_COMPATIBILITIES,
            "legacy_run_requires_original_runtime", "old package protocol combination is unsupported")
    for pin in pins.values():
        try:
            skill_preflight.verify_identity(pin)
        except skill_preflight.PreflightError as error:
            raise entry.PreparationError(error.code, "original pinned package is unavailable or changed") from error
        require(pin["compatibility_key"] == old_key,
                "legacy_run_requires_original_runtime", "old package compatibility combination differs")
    if outer.get("version") == 6:
        confirmed = outer.get("confirmed")
        role_pins = (confirmed or {}).get("packages")
        expected_roles = {"runner": "guided-implementation", "stage2": "solution-design",
                          "stage3": "guided-implementation", "stage4": "change-closure"}
        require(isinstance(role_pins, dict) and set(role_pins) == set(expected_roles) and
                confirmed.get("controller_ref") == data["controller_ref"] and
                confirmed.get("flow_mode") == state["mode"] and
                (confirmed.get("requirement") is None or
                 confirmed["requirement"] == state["handoff"]["requirement"]) and
                all(confirmed.get(key) == state["handoff"]["binding"][key] for key in
                    ("repository", "worktree", "git_common_dir", "target_branch")) and
                confirmed.get("frozen_requirement", {}).get("path") ==
                    state["handoff"]["requirement"]["absolute_path"] and
                confirmed["frozen_requirement"].get("commit") == state["handoff"]["source_commit"] and
                confirmed["frozen_requirement"].get("sha256") ==
                    state["handoff"]["requirement_identity"]["sha256"],
                "legacy_run_requires_original_runtime", "old runner confirmation differs from accepted Stage 2")
        for role, pin in role_pins.items():
            require(isinstance(pin, dict) and pin.get("name") == expected_roles[role],
                    "legacy_run_requires_original_runtime", "old runner role pin is missing or mismatched")
            try:
                skill_preflight.verify_identity(pin)
            except skill_preflight.PreflightError as error:
                raise entry.PreparationError(error.code, "original runner package is unavailable or changed") from error
            require(pin["compatibility_key"] == old_key,
                    "legacy_run_requires_original_runtime", "old runner pin differs")
    _verify_legacy_host(state, data["host_evidence"])
    if old_runtime:
        package = next((pin for pin in pins.values()
            if (Path(pin["root"]) / "scripts/workflow_progress.py").is_file()), None)
        require(package is not None, "package_unavailable", "original progression package is required")
        script = Path(package["root"]) / "scripts/workflow_progress.py"
        require(script.is_file(), "package_unavailable", "original read-only verifier is unavailable")
        code = ("import json,sys;sys.path.insert(0,sys.argv[1]);import workflow_progress as p;"
                "s=json.load(sys.stdin);p.validate_state(s);p.verify_phase_completed(s);"
                "p.handoff.verify_result(2,s['accepted']['payload'],s['handoff']['binding'],"
                "s['handoff']['scope'],s['handoff']['binding']['worktree'],s['accepted']['role_ref']);"
                "v=p._view(s);assert v['downstream_ready'] is True;print('verified')")
        completed = subprocess.run([sys.executable, "-B", "-c", code, str(script.parent)],
            input=json.dumps(state, ensure_ascii=False), text=True, capture_output=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)
        require(completed.returncode == 0 and completed.stdout.strip() == "verified",
                "legacy_recovery_ineligible", "original pinned runtime could not verify its accepted Stage-2 record")
    saved = state["handoff"]
    binding = saved["binding"]
    root, worktree = binding["repository"], binding["worktree"]
    payload = accepted["payload"]
    for key in ("planning_commit", "planning_merge_commit", "planning_paths"):
        require(key in payload, "legacy_recovery_ineligible", "old planning publication is incomplete")
    require(entry.git(root, "merge-base", "--is-ancestor", payload["planning_commit"],
                      payload["planning_merge_commit"], check=False).returncode == 0 and
            entry.git(root, "merge-base", "--is-ancestor", payload["planning_merge_commit"],
                      entry.git(root, "rev-parse", binding["target_branch"]).stdout.decode().strip(),
                      check=False).returncode == 0 and
            entry.git(worktree, "rev-parse", "HEAD").stdout.decode().strip() == payload["planning_merge_commit"] and
            not entry.git(worktree, "status", "--porcelain", "-z").stdout and
            state.get("publication", {}).get("result", {}).get("merge_commit") == payload["planning_merge_commit"],
            "legacy_recovery_ineligible", "published planning Git facts or clean worktree changed")
    source = saved["requirement_identity"]
    require(hashlib.sha256(handoff.blob(root, saved["source_commit"], source["path"])).hexdigest() ==
            source["sha256"] and
            handoff.blob(root, saved["scope"]["baseline"], source["path"]) ==
            handoff.blob(root, payload["planning_merge_commit"], source["path"]) and
            handoff.mode(root, saved["source_commit"], source["path"]) == b"100644" and
            handoff.mode(root, saved["scope"]["baseline"], source["path"]) == b"100644" and
            handoff.mode(root, payload["planning_merge_commit"], source["path"]) == b"100644",
            "source_changed", "frozen requirement bytes changed in published planning")
    old_control_raw = _legacy_attached_control_raw(state)
    if old_control_raw is not None:
        require(json.loads(old_control_raw) == state["control"]["context"],
                "control_context_changed", "attached control differs from old checkpoint")
    return {"outer": outer, "state": state, "raw": raw, "old_control_raw": old_control_raw,
            "binding": binding, "accepted": accepted,
            "original_checkpoint_sha256": data["original_checkpoint_sha256"],
            "original_accepted_digest": data["original_accepted_digest"],
            "recovery_id": data["recovery_id"]}


def _new_recovery_packages(data, original, checkpoint_path):
    pins = data["new_packages"]
    require(isinstance(pins, dict) and pins, "invalid_request", "current package identities are required")
    for pin in pins.values():
        try:
            skill_preflight.verify_identity(pin)
        except skill_preflight.PreflightError as error:
            raise entry.PreparationError(error.code, "new package identity is unavailable or changed") from error
        require(pin["compatibility_key"].get("workflow_progress") == PROTOCOL and
                pin["compatibility_key"].get("stage_transfer") == handoff.PROTOCOL and
                pin["compatibility_key"].get("control") == 8,
                "incompatible_package", "supplemental authorization requires the current package protocol")
    if original.get("version") is None:
        require(data.get("new_confirmed") is None, "invalid_request",
                "standalone takeover has no runner confirmation")
        return pins, None
    confirmed = data.get("new_confirmed")
    roles = {"runner": pins.get("guided-implementation"), "stage2": pins.get("solution-design"),
             "stage3": pins.get("guided-implementation"), "stage4": pins.get("change-closure")}
    require(isinstance(confirmed, dict) and confirmed.get("packages") == roles and
            confirmed.get("run_record") == str(checkpoint_path),
            "invalid_request", "current runner confirmation must pin this exact checkpoint and packages")
    runner = roles["runner"]
    require(isinstance(runner, dict) and runner.get("name") == "guided-implementation",
            "incompatible_package", "current guided-implementation runner pin required")
    script = Path(runner["root"]) / "scripts/workflow.py"
    require(script.is_file(), "package_unavailable", "current runner validator is unavailable")
    code = ("import json,sys;sys.path.insert(0,sys.argv[1]);import workflow as w;"
            "v=w.validate_confirmed(json.load(sys.stdin),restoring=True,legacy_recovery=True);"
            "print(json.dumps(v,ensure_ascii=False,sort_keys=True))")
    completed = subprocess.run([sys.executable, "-B", "-c", code, str(script.parent)],
        input=json.dumps(confirmed, ensure_ascii=False), text=True, capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=60)
    require(completed.returncode == 0, "new_runtime_unavailable",
            "current runner could not validate the supplemental confirmation")
    try:
        normalized = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise entry.PreparationError("new_runtime_unavailable", "current runner returned no confirmation") from error
    return pins, normalized


def _legacy_bridge(record):
    body = {"protocol": handoff.PROTOCOL, "kind": "legacy-supplemental-acceptance",
        "recovery_id": record["recovery_id"],
        "original_snapshot_sha256": record["original_checkpoint_sha256"],
        "original_accepted_digest": record["original_accepted_digest"],
        "legacy_accepted": copy.deepcopy(record["original_accepted"]),
        "controller_ref": record["controller_ref"],
        "requirement_identity": copy.deepcopy(record["requirement_identity"]),
        "binding": copy.deepcopy(record["binding"]),
        "scope_authority": copy.deepcopy(record["supplemental_authority"]),
        "payload": copy.deepcopy(record["original_accepted"]["payload"]),
        "planning_complete": True, "downstream_ready": False}
    return {**body, "digest": entry.digest(body)}


def _prepare_legacy_record(path, data, inspected, *, previous_record=None):
    entry.fields(data, {"action", "recovery_id", "original_checkpoint_sha256",
                        "original_accepted_digest", "controller_ref", "host_evidence",
                        "manifest", "implementation_required", "decision", "new_packages",
                        "workspace_evidence"}, {"new_confirmed", "generated_outputs"})
    original, old, accepted = inspected["outer"], inspected["state"], inspected["accepted"]
    pins, confirmed = _new_recovery_packages(data, original, path)
    requirement_identity = old["handoff"]["requirement_identity"]
    require(confirmed is None or
            (confirmed["controller_ref"] == data["controller_ref"] and
             confirmed["frozen_requirement"]["sha256"] == requirement_identity["sha256"] and
             confirmed["frozen_requirement"]["commit"] == old["handoff"]["source_commit"] and
             confirmed["requirement"]["requirement_identity"] == requirement_identity and
             confirmed["repository"] == inspected["binding"]["repository"] and
             confirmed["worktree"] == inspected["binding"]["worktree"] and
             confirmed["git_common_dir"] == inspected["binding"]["git_common_dir"] and
             confirmed["target_branch"] == inspected["binding"]["target_branch"] and
             confirmed["flow_mode"] == old["mode"]),
            "source_changed", "current runner confirmation differs from old requirement, worktree or Controller")
    workspace = data["workspace_evidence"]
    require(isinstance(workspace, dict) and workspace.get("controller_ref") == data["controller_ref"] and
            isinstance(workspace.get("reference"), str) and bool(workspace["reference"]),
            "host_evidence_missing", "Controller workspace evidence required for retained binding")
    lifecycle = {"origin": "reused", "manager": "git", "disposition": "retain",
                 "evidence_ref": workspace["reference"], "creation_receipt": None}
    old_binding = inspected["binding"]
    generated_outputs = generated_outputs_for(data)
    projected = stage_scope.validate_manifest(data["manifest"], old_binding["repository"],
        accepted["payload"]["planning_commit"], baseline=old["handoff"]["scope"]["baseline"],
        protected_paths=old["handoff"]["scope"]["protected_paths"],
        planning_paths=old["handoff"]["scope"]["owned_paths"],
        requirement_path=requirement_identity["path"], generated_outputs=generated_outputs)
    require(type(data["implementation_required"]) is bool and
            data["implementation_required"] == bool(projected["implementation_paths"]),
            "scope_incomplete", "supplemental manifest contradicts implementation decision")
    bound = supervision.bind_worktree({"repository": old_binding["repository"],
        "worktree": old_binding["worktree"], "branch": old_binding["branch"],
        "target_branch": old_binding["target_branch"], "base_commit": old_binding["base_commit"],
        "allowed_paths": sorted(set(old["handoff"]["scope"]["owned_paths"] +
                                    projected["implementation_paths"] + projected["closure_paths"])),
        "lifecycle": lifecycle})["binding"]
    require(all(bound[key] == old_binding[key] for key in
                ("repository", "worktree", "git_common_dir", "branch", "target_branch", "base_commit")),
            "binding_changed", "supplemental binding changed the original worktree")
    if previous_record is not None:
        require(bound == previous_record["binding"] and
                workspace["reference"] == previous_record["workspace_evidence"]["reference"],
                "binding_changed", "supersede must retain the exact active worktree ownership evidence")
    workspace_evidence(bound, data["controller_ref"], workspace)
    scope = {key: copy.deepcopy(old["handoff"]["scope"][key]) for key in
             ("baseline", "owned_paths", "protected_paths")}
    proposal = {"recovery_id": data["recovery_id"], "manifest": copy.deepcopy(data["manifest"]),
                "implementation_required": data["implementation_required"],
                "generated_outputs": generated_outputs,
                "original_checkpoint_sha256": data["original_checkpoint_sha256"]}
    proposal_ref = entry.digest(proposal)
    candidate = accepted["payload"]["planning_commit"]
    basis_paths = {requirement_identity["path"]} | {item["basis"]["path"] for item in data["manifest"]}
    basis_blobs = {relative: hashlib.sha256(handoff.blob(bound["repository"], candidate, relative)).hexdigest()
                   for relative in sorted(basis_paths)}
    seal_evidence = {"proposal_id": "legacy:" + data["recovery_id"],
        "proposal_digest": proposal_ref, "expected_scope_revision": 0,
        "candidate_commit": candidate, "manifest_digest": entry.digest(data["manifest"]),
        "requirement_identity": copy.deepcopy(requirement_identity), "binding": copy.deepcopy(bound),
        "prior_authorization_ref": old["handoff"]["authorization"]["reference"],
        "decision": copy.deepcopy(data["decision"]),
        "target_head": supervision._branch_oid(Path(bound["repository"]), bound["target_branch"]),
        "basis_blobs": basis_blobs}
    change_evidence = approved_change_evidence(old, data["decision"])
    new_control = copy.deepcopy(old["control"]["context"])
    new_control["schema_version"] = 5
    new_control["handoff_progress"]["binding"] = copy.deepcopy(bound)
    sealed = control.transition({"schema_version": 5, "actor_ref": data["controller_ref"],
        "context": new_control, "action": "scope-seal", "evidence": seal_evidence})
    new_control = sealed["context"]
    seal = sealed["scope_seal"]
    scope.update(projected)
    scope["downstream"] = {"state": "sealed", "revision": 1,
        "implementation_required": data["implementation_required"],
        "manifest": copy.deepcopy(data["manifest"]), "proposal_ref": proposal_ref,
        "seal_ref": seal["seal_ref"]}
    stage_scope.validate_scope(scope, bound["repository"], stage=2,
        requirement_path=requirement_identity["path"],
        planning_paths=accepted["payload"]["planning_paths"], candidate_commit=candidate,
        generated_outputs=generated_outputs)
    authority = {"state": "sealed", "revision": 1, "scope": scope,
        "proposal": proposal, "proposal_digest": proposal_ref,
        "seal": seal, "seal_ref": seal["seal_ref"], "candidate_commit": candidate,
        "approved_changes": [change_evidence] if change_evidence is not None else []}
    ledger = None
    old_control_raw = inspected["old_control_raw"]
    takeover_transaction_id = entry.digest({"recovery_id": data["recovery_id"],
        "original_checkpoint_sha256": data["original_checkpoint_sha256"],
        "original_accepted_digest": data["original_accepted_digest"],
        **({"supersedes": previous_record["supplemental_authority"]["seal_ref"],
            "proposal_ref": proposal_ref} if previous_record is not None else {})})
    phase_finalization_ref = None
    phase = old["handoff"]["authorization"].get("phase")
    if phase is not None:
        source = old["handoff"]["entry"]["source"]
        require(source["kind"] == "discussion", "phase_authorization_required",
                "old Phase Run must belong to its attached discussion")
        phase_result = entry.discussion_protocol.handle({"protocol_version": 1,
            "operation": "read-phase-run",
            "project_path": entry.discussion_root(old["handoff"]["expected_entry"]),
            **source["attachment"], "phase_run_id": phase["run_id"]})["phase_run"]
        require(phase_result.get("state") == "completed" and any(
            attempt.get("attempt_id") == phase["attempt_id"] and attempt.get("state") == "completed"
            for attempt in phase_result.get("attempts", [])),
            "phase_authorization_required", "original Stage-2 Phase Run is not finalized")
        phase_finalization_ref = entry.digest(phase_result)
    if old_control_raw is not None:
        metadata = {"state": "pending", "recovery_id": data["recovery_id"],
            "checkpoint_path": str(path),
            "original_checkpoint_sha256": data["original_checkpoint_sha256"],
            "original_accepted_digest": data["original_accepted_digest"],
            "old_control_sha256": hashlib.sha256(old_control_raw.encode("utf-8")).hexdigest(),
            "takeover_transaction_id": takeover_transaction_id}
        new_control["legacy_takeover"] = metadata
        control.validate_context(new_control)
        discussion = old["control"]["discussion"]
        base = {key: copy.deepcopy(value) for key, value in discussion.items()
                if key not in {"expected_ledger_revision", "expected_topic_revision", "idempotency_key"}}
        topic = entry.discussion_protocol.handle({**base, "operation": "read-topic"})
        expected_control = (old["control"]["context"] if previous_record is None else
            previous_record["ledger"]["active_receipt"]["control"]["context"])
        require(topic["workflow_control"] == expected_control,
                "control_context_changed", "attached ledger changed before takeover or supersede")
        evidence = {**{key: metadata[key] for key in metadata if key != "state"},
                    "controller_ref": data["controller_ref"],
                    "new_control_context": copy.deepcopy(new_control)}
        if previous_record is not None:
            evidence.update(previous_takeover_transaction_id=
                previous_record["ledger"]["active_request"]["evidence"]["takeover_transaction_id"],
                previous_scope_seal_ref=previous_record["supplemental_authority"]["seal_ref"])
        ledger = {"pending_request": {**base, "operation": "workflow-control",
            "action": "scope-recover-legacy-supersede" if previous_record is not None else "scope-recover-legacy",
            "evidence": evidence,
            "expected_ledger_revision": topic["ledger_revision"],
            "expected_topic_revision": topic["record_revision"],
            "idempotency_key": str(uuid.uuid4())},
            "pending_receipt": None, "active_request": None, "active_receipt": None}
    record = {"recovery_id": data["recovery_id"], "state": "takeover-pending",
        "original_checkpoint_path": str(path),
        "takeover_transaction_id": takeover_transaction_id,
        "original_checkpoint_sha256": data["original_checkpoint_sha256"],
        "original_accepted_digest": data["original_accepted_digest"],
        "original_bytes_base64": base64.b64encode(inspected["raw"]).decode("ascii"),
        "original_pins": copy.deepcopy(old["packages"]),
        "original_accepted": copy.deepcopy(accepted),
        "controller_ref": data["controller_ref"],
        "requirement_identity": copy.deepcopy(requirement_identity),
        "binding": copy.deepcopy(bound), "supplemental_authority": authority,
        "new_pins": copy.deepcopy(pins), "decision": copy.deepcopy(data["decision"]),
        "host_evidence": copy.deepcopy(data["host_evidence"]),
        "workspace_evidence": copy.deepcopy(workspace),
        "publication_request_digest": entry.digest(old["publication"]["request"]),
        "old_stop_ref": entry.digest(old["host"]["last_stop"]),
        "phase_finalization_ref": phase_finalization_ref,
        "takeover_receipts": {"local": {"original_checkpoint_sha256": data["original_checkpoint_sha256"]},
                              "ledger_pending": None, "ledger_active": None},
        "ledger": ledger,
        "input": copy.deepcopy(data)}
    if previous_record is not None:
        prior = {key: copy.deepcopy(value) for key, value in previous_record.items()
                 if key not in {"original_bytes_base64", "superseded"}}
        record["superseded"] = [*copy.deepcopy(previous_record.get("superseded", [])), prior]
    record["bridge"] = _legacy_bridge(record)
    packages = {pin["name"]: copy.deepcopy(pin) for pin in pins.values()}
    native_host = native_host_preflight(data["host_evidence"])
    if native_host["adapter"] == "collaboration":
        native_host.update(instance=old["handoff"]["expected_entry"]["actor"]["thread_id"],
                           baseline_refs=copy.deepcopy(old["native_host"].get("baseline_refs", [])))
    current = {"protocol": PROTOCOL, "recovery_mode": "legacy-takeover",
        "revision": 0, "mode": old["mode"], "stage": 2, "status": "accepted", "step": "accepted",
        "next_stage": 3, "phase_complete": True, "stopped": True,
        "packages": packages, "handoff": copy.deepcopy(old["handoff"]),
        "control": {"context": new_control, "discussion": copy.deepcopy(old["control"]["discussion"])},
        "dispatch": None, "accepted": copy.deepcopy(record["bridge"]), "pending": None,
        "scope_authority": authority, "scope_recoveries": {data["recovery_id"]: record},
        "checkpoint_path": str(path), "native_host": native_host,
        "host": copy.deepcopy(old["host"]), "workspace_evidence": copy.deepcopy(workspace),
        "history": [], "transaction": None, "transaction_source": None,
        "transaction_result": None, "retained_host_actions": []}
    if original.get("version") == 6:
        recovered = {"version": 8, "confirmed": confirmed, "status": "recovery_pending",
            "current_stage": "stage3", "sessions": {}, "stage_results": {},
            "launch": {"stage": "stage3", "state": "prelaunch", "turn": 0},
            "history": [{"event": "scope-recovery-takeover", "recovery_id": data["recovery_id"]}],
            "recovery_source": {"recovery_id": data["recovery_id"],
                "original_checkpoint_sha256": data["original_checkpoint_sha256"]}, KEY: current}
    else:
        recovered = {"recovery_source": {"recovery_id": data["recovery_id"],
            "original_checkpoint_sha256": data["original_checkpoint_sha256"]}, KEY: current}
    return recovered


def prepare_legacy_takeover(path, data):
    """Atomically replace the sole current old checkpoint, retaining raw bytes."""
    path = Path(path)
    require(path.is_absolute() and str(path.resolve()) == str(path),
            "invalid_checkpoint", "canonical original checkpoint path required")
    preliminary = read_record(path)
    runner = preliminary.get("version") in {6, 8}
    with (legacy_runner_lock(path) if runner else nullcontext()):
        with record_lock(path):
            current = read_record(path)
            if current.get("version") == 8 or current.get(KEY, {}).get("recovery_mode") == "legacy-takeover":
                proof = verify_legacy_recovery(current, path)
                record = current[KEY]["scope_recoveries"][proof["recovery_id"]]
                require(record["input"] == data, "scope_conflict", "replay must retain exact original takeover input")
                return {"protocol": PROTOCOL, "status": record["state"], "state": record["state"],
                    "recovery_id": proof["recovery_id"], "bridge": copy.deepcopy(record["bridge"]),
                    "acknowledged": True, "downstream_ready": False}
            raw = path.read_bytes()
            inspected = inspect_legacy_checkpoint(path, data, raw=raw)
            require((inspected["outer"].get("version") == 6) == runner,
                    "legacy_checkpoint_changed", "runner ownership changed before takeover lock")
            recovered = _prepare_legacy_record(path, data, inspected)
            atomic_save(path, recovered)
            record = recovered[KEY]["scope_recoveries"][data["recovery_id"]]
            return {"protocol": PROTOCOL, "status": "takeover-pending", "state": "takeover-pending",
                    "recovery_id": data["recovery_id"], "bridge": copy.deepcopy(record["bridge"]),
                    "downstream_ready": False}


def supersede_legacy_takeover(path, data):
    """Replace only an unconsumed supplement, retaining its entire prior audit."""
    entry.fields(data, {"action", "recovery_id", "original_checkpoint_sha256",
                        "original_accepted_digest", "controller_ref", "host_evidence",
                        "manifest", "implementation_required", "decision", "new_packages",
                        "workspace_evidence", "previous_takeover_transaction_id",
                        "previous_scope_seal_ref"}, {"new_confirmed", "generated_outputs"})
    require(data["action"] == "supersede", "invalid_operation", "explicit supersede action required")
    path = Path(path)
    require(path.is_absolute() and str(path.resolve()) == str(path),
            "invalid_checkpoint", "canonical recovered checkpoint required")
    preliminary = read_record(path)
    with (legacy_runner_lock(path) if preliminary.get("version") == 8 else nullcontext()):
        with record_lock(path):
            outer = read_record(path)
            proof = verify_legacy_recovery(outer, path)
            require(proof["recovery_id"] == data["recovery_id"] and
                    proof["original_checkpoint_sha256"] == data["original_checkpoint_sha256"],
                    "scope_conflict", "supersede must retain the original checkpoint identity")
            state = outer[KEY]
            current = state["scope_recoveries"][proof["recovery_id"]]
            if current.get("input") == data:
                return {"protocol": PROTOCOL, "status": current["state"], "state": current["state"],
                        "recovery_id": data["recovery_id"], "bridge": copy.deepcopy(current["bridge"]),
                        "acknowledged": True, "downstream_ready": False}
            require(state.get("recovery_mode") == "legacy-takeover" and
                    current["state"] in {"takeover-pending", "authorized"} and
                    state.get("accepted") == current["bridge"] and
                    (outer.get("version") != 8 or
                     (outer.get("current_stage") == "stage3" and not outer.get("sessions", {}).get("stage3") and
                      (outer.get("launch") or {}).get("state") == "prelaunch")) and
                    not (current.get("ledger") or {}).get("reservation_request") and
                    not (current.get("ledger") or {}).get("reservation_receipt"),
                    "legacy_recovery_consumed", "supersede cannot replace consumed or reserved Stage-3 authority")
            require(data["previous_scope_seal_ref"] == current["supplemental_authority"]["seal_ref"] and
                    data["previous_takeover_transaction_id"] == current["takeover_transaction_id"] and
                    data["controller_ref"] == current["controller_ref"] and
                    data["original_accepted_digest"] == current["original_accepted_digest"],
                    "scope_conflict", "supersede must name the exact current supplemental decision")
            if current.get("ledger") is not None:
                require(current["state"] == "authorized" and
                        current["ledger"].get("active_receipt") is not None,
                        "control_outcome_unknown", "settle original attached takeover before superseding")
            normalized = {key: copy.deepcopy(value) for key, value in data.items()
                          if key not in {"previous_takeover_transaction_id", "previous_scope_seal_ref"}}
            normalized["action"] = "prepare-takeover"
            raw = base64.b64decode(current["original_bytes_base64"], validate=True)
            inspected = inspect_legacy_checkpoint(path, normalized, raw=raw)
            recovered = _prepare_legacy_record(path, normalized, inspected, previous_record=current)
            replacement = recovered[KEY]["scope_recoveries"][data["recovery_id"]]
            require(replacement["supplemental_authority"]["seal_ref"] !=
                    current["supplemental_authority"]["seal_ref"],
                    "scope_conflict", "supersede needs a distinct supplemental decision")
            replacement["input"] = copy.deepcopy(data)
            atomic_save(path, recovered)
            return {"protocol": PROTOCOL, "status": "takeover-pending", "state": "takeover-pending",
                    "recovery_id": data["recovery_id"], "bridge": copy.deepcopy(replacement["bridge"]),
                    "downstream_ready": False}


def activate_legacy_takeover(path, data):
    """Reconcile the original attached transaction, then open current Stage 3."""
    entry.fields(data, {"action", "recovery_id", "original_checkpoint_sha256",
                        "controller_ref", "host_evidence"})
    require(data["action"] == "activate", "invalid_operation", "legacy activation action required")
    path = Path(path)
    require(path.is_absolute() and str(path.resolve()) == str(path),
            "invalid_checkpoint", "canonical recovery checkpoint path required")
    preliminary = read_record(path)
    with (legacy_runner_lock(path) if preliminary.get("version") == 8 else nullcontext()):
        with record_lock(path):
            outer = read_record(path)
            proof = verify_legacy_recovery(outer, path)
            require(proof["recovery_id"] == data["recovery_id"] and
                    proof["original_checkpoint_sha256"] == data["original_checkpoint_sha256"],
                    "scope_conflict", "activation names another original recovery")
            state = outer[KEY]
            record = state["scope_recoveries"][proof["recovery_id"]]
            require(data["controller_ref"] == record["controller_ref"],
                    "identity_mismatch", "original Controller must activate recovery")
            if record["state"] in {"authorized", "consumed"}:
                return {"protocol": PROTOCOL, "status": record["state"], "state": record["state"],
                    "recovery_id": data["recovery_id"], "acknowledged": True,
                    "readiness": legacy_scope_readiness(state, path) if record["state"] == "authorized" else None}
            previous = record.get("activation_input")
            require(previous is None or previous == data, "scope_conflict", "resume exact activation evidence")
            raw = base64.b64decode(record["original_bytes_base64"], validate=True)
            old = json.loads(raw.decode("utf-8"))[KEY]
            _verify_legacy_host(old, data["host_evidence"])
            require(data["host_evidence"] == record["host_evidence"] or
                    data["host_evidence"].get("snapshot", {}).get("sequence", -1) >=
                        record["host_evidence"].get("snapshot", {}).get("sequence", 0),
                    "host_evidence_missing", "activation host observation is older than inspection")
            bridge = record["bridge"]
            binding = bridge["binding"]
            payload = bridge["payload"]
            require(entry.git(binding["worktree"], "rev-parse", "HEAD").stdout.decode().strip() ==
                    payload["planning_merge_commit"] and
                    not entry.git(binding["worktree"], "status", "--porcelain", "-z").stdout and
                    entry.git(binding["repository"], "merge-base", "--is-ancestor",
                        payload["planning_merge_commit"],
                        entry.git(binding["repository"], "rev-parse", binding["target_branch"]).stdout.decode().strip(),
                        check=False).returncode == 0,
                    "legacy_recovery_changed", "old published planning Git facts changed before activation")
            stage_scope.validate_scope(record["supplemental_authority"]["scope"], binding["repository"],
                stage=2, requirement_path=record["requirement_identity"]["path"],
                planning_paths=payload["planning_paths"], candidate_commit=payload["planning_commit"],
                generated_outputs=generated_outputs_for(record["supplemental_authority"]["proposal"]))
            for pin in [*record["original_pins"].values(), *record["new_pins"].values()]:
                skill_preflight.verify_identity(pin)
            if previous is None:
                record["activation_input"] = copy.deepcopy(data)
                state["revision"] += 1
                atomic_save(path, outer)
            ledger = record.get("ledger")
            if ledger is not None:
                if ledger["pending_receipt"] is None:
                    ledger["pending_receipt"] = entry.discussion_protocol.handle(
                        copy.deepcopy(ledger["pending_request"]))
                    record["takeover_receipts"]["ledger_pending"] = copy.deepcopy(ledger["pending_receipt"])
                    state["revision"] += 1
                    atomic_save(path, outer)
                require(ledger["pending_receipt"].get("state") in {"takeover-pending", "takeover-active"},
                        "control_context_changed", "attached takeover did not persist pending control")
                if ledger["active_request"] is None:
                    pending = ledger["pending_request"]
                    active = copy.deepcopy(pending)
                    active["action"] = "scope-recover-legacy"
                    active["evidence"].pop("previous_takeover_transaction_id", None)
                    active["evidence"].pop("previous_scope_seal_ref", None)
                    active["evidence"]["new_control_context"]["legacy_takeover"]["state"] = "active"
                    active["expected_ledger_revision"] = ledger["pending_receipt"]["ledger_revision"]
                    active["expected_topic_revision"] = ledger["pending_receipt"]["record_revision"]
                    active["idempotency_key"] = str(uuid.uuid4())
                    ledger["active_request"] = active
                    state["revision"] += 1
                    atomic_save(path, outer)
                if ledger["active_receipt"] is None:
                    ledger["active_receipt"] = entry.discussion_protocol.handle(
                        copy.deepcopy(ledger["active_request"]))
                    record["takeover_receipts"]["ledger_active"] = copy.deepcopy(ledger["active_receipt"])
                    state["revision"] += 1
                    atomic_save(path, outer)
                require(ledger["active_receipt"].get("state") == "takeover-active" and
                        ledger["active_receipt"]["control"]["context"] ==
                            ledger["active_request"]["evidence"]["new_control_context"],
                        "control_context_changed", "attached takeover active control differs")
                state["control"]["context"] = copy.deepcopy(ledger["active_receipt"]["control"]["context"])
                discussion = state["control"]["discussion"]
                discussion.update(expected_ledger_revision=ledger["active_receipt"]["ledger_revision"],
                    expected_topic_revision=ledger["active_receipt"]["record_revision"],
                    idempotency_key=str(uuid.uuid4()))
            record["state"] = "authorized"
            if outer.get("version") == 8:
                outer["status"] = "active"
            state["revision"] += 1
            atomic_save(path, outer)
            return {"protocol": PROTOCOL, "status": "authorized", "state": "authorized",
                    "recovery_id": data["recovery_id"],
                    "readiness": legacy_scope_readiness(state, path),
                    "downstream_ready": legacy_scope_readiness(state, path) is not None}


def pending_decision(kind, subject, question):
    entry.nonempty(kind); entry.nonempty(question)
    return {"decision_id": str(uuid.uuid4()), "kind": kind, "subject": copy.deepcopy(subject), "question": question}


def verify_legacy_recovery(outer, checkpoint_path=None):
    """Read the sole supplemental current without interpreting old v7 as v9."""
    require(isinstance(outer, dict) and outer.get("version") in (None, 8) and
            isinstance(outer.get(KEY), dict), "legacy_recovery_missing",
            "current recovered runner and progression are required")
    state = outer[KEY]
    path = Path(state.get("checkpoint_path", ""))
    if checkpoint_path is not None:
        require(str(Path(checkpoint_path).resolve()) == str(path),
                "legacy_recovery_changed", "copied recovery cannot own a second checkpoint path")
    require(path.is_absolute() and str(path.resolve()) == str(path) and path.is_file() and
            read_record(path) == outer,
            "legacy_recovery_changed", "recovery must be the unique current canonical checkpoint")
    require(state.get("protocol") == PROTOCOL and state.get("recovery_mode") == "legacy-takeover" or
            state.get("protocol") == PROTOCOL and state.get("stage") in {3, 4} and
            isinstance(state.get("scope_recoveries"), dict),
            "legacy_recovery_missing", "current v14 recovery projection is unavailable")
    source = outer.get("recovery_source")
    records = state.get("scope_recoveries")
    require(isinstance(source, dict) and set(source) == {"recovery_id", "original_checkpoint_sha256"} and
            isinstance(records, dict) and set(records) == {source["recovery_id"]},
            "legacy_recovery_changed", "exactly one current recovery provenance is required")
    record = records[source["recovery_id"]]
    require(record.get("recovery_id") == source["recovery_id"] and
            record.get("original_checkpoint_sha256") == source["original_checkpoint_sha256"] and
            record.get("state") in {"takeover-pending", "authorized", "consumed"},
            "legacy_recovery_changed", "current recovery state or source changed")
    try:
        raw = base64.b64decode(record["original_bytes_base64"], validate=True)
        original = json.loads(raw.decode("utf-8"))
    except (KeyError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise entry.PreparationError("legacy_recovery_changed", "original raw snapshot is unreadable") from error
    require(hashlib.sha256(raw).hexdigest() == record["original_checkpoint_sha256"] and
            original.get("version") in (None, 6) and
            original.get(KEY, {}).get("accepted") == record.get("original_accepted") and
            record["original_accepted"].get("digest") == record.get("original_accepted_digest") and
            record.get("bridge", {}).get("legacy_accepted") == record["original_accepted"] and
            record["bridge"].get("original_snapshot_sha256") == record["original_checkpoint_sha256"] and
            record["bridge"].get("digest") == entry.digest({key: value for key, value in record["bridge"].items()
                if key != "digest"}) and
            record.get("supplemental_authority") == record["bridge"].get("scope_authority"),
            "legacy_recovery_changed", "old bytes, bridge or supplemental authority changed")
    original_state = original[KEY]
    bound_paths = [action["checkpoint"] for action in
        [original_state.get("action") or {}, original_state.get("host_action") or {},
         *original_state.get("retained_host_actions", [])]
        if isinstance(action, dict) and "checkpoint" in action]
    require(record.get("original_checkpoint_path") == str(path) and
            bound_paths and all(saved_path == str(path) for saved_path in bound_paths),
            "legacy_recovery_changed", "original native calls bind another checkpoint path")
    if state.get("recovery_mode") == "legacy-takeover":
        require(state.get("stage") == 2 and state.get("accepted") == record["bridge"] and
                state.get("scope_authority") == record["supplemental_authority"] and
                record["state"] in {"takeover-pending", "authorized"},
                "legacy_recovery_changed", "current Stage-2 supplemental projection changed")
    else:
        require(record["state"] == "consumed" and
                any(item.get("recovery_mode") == "legacy-takeover" and
                    item.get("accepted") == record["bridge"] for item in state.get("history", [])),
                "legacy_recovery_changed", "consumed Stage-2 bridge is missing from current history")
    if record["state"] == "takeover-pending" and outer.get("version") == 8:
        require(outer.get("status") == "recovery_pending", "legacy_recovery_changed",
                "pending takeover cannot authorize the runner")
    if record["state"] == "authorized" and outer.get("version") == 8:
        require(outer.get("status") == "active", "legacy_recovery_changed",
                "only activated takeover can authorize Stage 3")
    return {"state": record["state"], "recovery_id": record["recovery_id"],
            "original_checkpoint_sha256": record["original_checkpoint_sha256"],
            "binding": copy.deepcopy(record["bridge"]["binding"]),
            "accepted_digest": record["bridge"]["digest"]}


def legacy_scope_readiness(state, checkpoint_path):
    path = Path(checkpoint_path).resolve()
    if state.get("checkpoint_path") != str(path):
        return None
    outer = read_record(path)
    proof = verify_legacy_recovery(outer, path)
    record = state["scope_recoveries"][proof["recovery_id"]]
    if record["state"] == "consumed" and state.get("stage") in {3, 4}:
        readiness = state.get("consumed_readiness")
        if (not isinstance(readiness, dict) or readiness.get("legacy_recovery_id") != record["recovery_id"] or
                readiness.get("accepted_digest") != record["bridge"]["digest"] or
                state.get("stage") == 3 and state.get("handoff", {}).get("readiness") != readiness):
            return None
        return copy.deepcopy(readiness)
    if (record["state"] != "authorized" or state.get("stage") != 2 or
            state.get("accepted") != record["bridge"] or not state.get("stopped") or
            not state.get("phase_complete") or state.get("status") != "accepted"):
        return None
    authority = record["supplemental_authority"]
    bridge = record["bridge"]
    payload = bridge["payload"]
    current_progress = state["control"]["context"].get("handoff_progress") or {}
    verify_scope_authority(authority, initial_seal=current_progress.get("scope_seal"),
        revision_records=current_progress.get("scope_revisions", []),
        requirement_identity=bridge["requirement_identity"], binding=bridge["binding"],
        decision_records=change_decision_journal(state))
    stage_scope.validate_scope(authority["scope"], bridge["binding"]["repository"], stage=3,
        requirement_path=bridge["requirement_identity"]["path"],
        planning_paths=payload["planning_paths"], candidate_commit=payload["planning_commit"],
        generated_outputs=generated_outputs_for(authority["proposal"]))
    if not authority["scope"]["downstream"]["implementation_required"]:
        return None
    if Path(bridge["binding"]["worktree"]).exists():
        require(entry.git(bridge["binding"]["worktree"], "rev-parse", "HEAD").stdout.decode().strip() ==
                payload["planning_merge_commit"], "legacy_recovery_changed",
                "recovered planning worktree changed before Stage 3")
    return {"accepted_digest": bridge["digest"], "scope_revision": authority["revision"],
            "scope_seal": authority["seal_ref"], "scope_authority": copy.deepcopy(authority),
            "publication": {"planning_commit": payload["planning_commit"],
                "planning_merge_commit": payload["planning_merge_commit"],
                "publication_request_digest": record["publication_request_digest"]},
            "stop_evidence_ref": record["old_stop_ref"],
            "phase_finalization_ref": record.get("phase_finalization_ref"),
            "controller_ref": bridge["controller_ref"],
            "control_revision": state["control"]["context"].get("revision"),
            "checkpoint_path": str(path), "checkpoint_revision": state["revision"],
            "legacy_recovery_id": record["recovery_id"],
            "original_snapshot_sha256": record["original_checkpoint_sha256"],
            "original_accepted_digest": record["original_accepted_digest"]}


def decision_matches(pending, decision):
    entry.fields(decision, {"decision_id", "subject", "answer", "reference"}, {"flow_intent"})
    require(pending is not None and decision["decision_id"] == pending["decision_id"] and
            decision["subject"] == pending["subject"], "stale_decision", "answer does not name the current pending matter")
    entry.nonempty(decision["reference"]); entry.nonempty(decision["answer"])


def change_decision_journal(state):
    records = {}
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


def validate_state(state):
    require(isinstance(state, dict) and state.get("protocol") == PROTOCOL,
            "legacy_run_requires_original_runtime", "use original " + str(state.get("protocol") if isinstance(state,dict) else None) + "; package digests " + str({k:v.get("bundle_digest") for k,v in (state.get("packages",{}) if isinstance(state,dict) else {}).items()}))
    if state.get("recovery_mode") == "legacy-takeover":
        require(state.get("stage") == 2 and state.get("status") == "accepted" and
                type(state.get("revision")) is int and state["revision"] >= 0 and
                isinstance(state.get("scope_recoveries"), dict) and len(state["scope_recoveries"]) == 1 and
                state.get("accepted") == next(iter(state["scope_recoveries"].values())).get("bridge") and
                state.get("scope_authority") == next(iter(state["scope_recoveries"].values())).get("supplemental_authority") and
                state.get("checkpoint_path") and
                state.get("control", {}).get("context", {}).get("schema_version") == 5,
                "invalid_checkpoint", "supplemental Stage-2 current projection is inconsistent")
        for pin in state.get("packages", {}).values():
            skill_preflight.verify_identity(pin)
        control.validate_context(state["control"]["context"])
        recovery = next(iter(state["scope_recoveries"].values()))
        verify_scope_authority(recovery["supplemental_authority"],
            initial_seal=(state["control"]["context"].get("handoff_progress") or {}).get("scope_seal"),
            revision_records=(state["control"]["context"].get("handoff_progress") or {}).get("scope_revisions", []),
            requirement_identity=recovery["bridge"]["requirement_identity"],
            binding=recovery["bridge"]["binding"],
            decision_records=change_decision_journal(state))
        return state
    require(type(state.get("revision")) is int and state["revision"] >= 0,
            "invalid_checkpoint", "checkpoint revision is invalid")
    require(state.get("mode") in {"stepwise", "continuous"}, "invalid_checkpoint", "invalid mode")
    native_host = state.get('native_host')
    if state.get('stage', 0) >= 2:
        require('workspace_evidence' in state, 'host_evidence_missing', 'original workspace evidence is missing')
        workspace_evidence(state['handoff']['binding'], state['handoff']['controller_ref'], state['workspace_evidence'])
        require(isinstance(native_host, dict) and native_host.get('adapter') in {'collaboration', 'app-server'} and
                isinstance(native_host.get('instance'), str) and bool(native_host['instance']),
                'invalid_checkpoint', 'v13 requires the originally admitted native host')
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
    require(isinstance(state.get("retained_host_actions", []), list),
            "invalid_checkpoint", "retained host actions must be an ordered list")
    control.validate_context(state["control"]["context"])
    for identity, transaction in state.get('control_transactions', {}).items():
        require(entry.digest(transaction['request']) == identity, 'invalid_checkpoint', 'control transaction identity changed')
        if transaction.get('scope') == 'archive':
            retired = state['control']['context'].get('retired_handoffs', {}).get(transaction.get('handoff_id'))
            require(retired is not None and retired['handoff_completed'] and
                    transaction.get('handoff_completed') is True and
                    transaction['request']['action'] in {'archive', 'archive-result'} and
                    transaction['request']['evidence']['handoff_id'] == transaction['handoff_id'],
                    'invalid_checkpoint', 'archive transaction lacks committed handoff authority')
            require(transaction.get('intent_persisted') is not True or transaction.get('result') is not None or
                    any(op['operation_id'] == transaction.get('operation_id') for op in retired['operations']),
                    'invalid_checkpoint', 'archive intent has no durable result')
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


def scope_readiness(state, checkpoint_path):
    """Project Stage-3 authority from the current accepted Stage-2 facts.

    This is also used by B's read-only checkpoint verification. The immutable B
    acceptance records the planning result; it cannot itself grant a later lease.
    """
    if (state.get("stage") != 2 or state.get("status") != "accepted" or
            state.get("accepted") is None or not state.get("stopped") or
            not state.get("phase_complete") or state.get("stop_requested") or
            state.get("transport_loss") or state.get("business_block")):
        return None
    if (state.get("scope_revision_transaction") or {}).get("state") not in (None, "active"):
        return None
    authority = state.get("scope_authority")
    publication = state.get("publication")
    if (not isinstance(authority, dict) or authority.get("state") != "sealed" or
            not isinstance(publication, dict) or publication.get("result") is None or
            state.get("transaction") is not None or state.get("pending") is not None and
            state["pending"].get("kind") != "stage-entry"):
        return None
    scope = authority.get("scope")
    if not isinstance(scope, dict) or scope.get("downstream", {}).get("state") != "sealed":
        return None
    if not scope["downstream"]["implementation_required"] or not scope["implementation_paths"]:
        return None
    current_progress = state["control"]["context"].get("handoff_progress") or {}
    verify_scope_authority(authority, initial_seal=current_progress.get("scope_seal"),
        revision_records=current_progress.get("scope_revisions", []),
        requirement_identity=state["handoff"]["requirement_identity"],
        binding=state["handoff"]["binding"],
        decision_records=change_decision_journal(state))
    accepted = state["accepted"]
    payload = accepted.get("payload", {})
    if (accepted.get("handoff") != state["handoff"] or
            payload.get("planning_commit") != authority.get("candidate_commit") or
            payload.get("planning_merge_commit") != publication["result"].get("merge_commit") or
            state.get("host", {}).get("status") != "stopped" or
            any(not action.get("resolved") and not action.get("creation_resolved") for action in
                [*state.get("retained_host_actions", []), state.get("host_action") or {}] if action)):
        return None
    if checkpoint_path is None:
        return None
    path = Path(checkpoint_path).resolve()
    persisted = read_record(path)
    if not path.is_absolute() or persisted.get(KEY) != state:
        return None
    owner = Progress(path, persisted)
    if owner.stop_barrier_pending() or owner.unresolved_control_transactions():
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
        verify_phase_completed(state)
        saved = state["handoff"]
        result = entry.discussion_protocol.handle({"protocol_version": 1, "operation": "read-phase-run",
            "project_path": entry.discussion_root(saved["expected_entry"]),
            **saved["entry"]["source"]["attachment"], "phase_run_id": phase["run_id"]})
        phase_finalization_ref = entry.digest(result["phase_run"])
    return {"accepted_digest": accepted["digest"], "scope_revision": authority["revision"],
            "scope_seal": authority["seal_ref"], "scope_authority": copy.deepcopy(authority),
            "publication": {"planning_commit": payload["planning_commit"],
                            "planning_merge_commit": payload["planning_merge_commit"],
                            "publication_request_digest": entry.digest(publication["request"])},
            "stop_evidence_ref": entry.digest(state["host"]["last_stop"]),
            "phase_finalization_ref": phase_finalization_ref,
            "controller_ref": state["handoff"]["controller_ref"],
            "control_revision": state["control"]["context"].get("revision"),
            "checkpoint_path": str(path), "checkpoint_revision": state["revision"]}


def consumed_scope_readiness(state, checkpoint_path):
    """Verify the one Stage-2 readiness atomically consumed by current Stage 3."""
    if (state.get("consumed_readiness") or {}).get("revision_transaction_ref"):
        return revision_scope_readiness(state, checkpoint_path)
    if (state.get("consumed_readiness") or {}).get("legacy_recovery_id"):
        return legacy_scope_readiness(state, checkpoint_path)
    if state.get("stage") != 3 or not isinstance(state.get("consumed_readiness"), dict):
        return None
    if (state.get("scope_revision_transaction") or {}).get("state") not in (None, "active"):
        return None
    readiness = state["consumed_readiness"]
    path = Path(checkpoint_path).resolve()
    if (state.get("checkpoint_path") != str(path) or readiness.get("checkpoint_path") != str(path) or
            state.get("handoff", {}).get("readiness") != readiness or
            state.get("scope_authority") != readiness.get("scope_authority") or
            not state.get("history")):
        return None
    old = state["history"][-1]
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
    return copy.deepcopy(readiness)


def revised_scope_authority(transaction):
    old = transaction["old_authority"]
    receipt = transaction["control_result"]
    scope = copy.deepcopy(transaction["proposed_scope"])
    scope["downstream"].update(state="sealed", seal_ref=receipt["revision_ref"])
    approved = transaction.get("approved_change_evidence")
    return {**copy.deepcopy(old), "state": "sealed", "revision": scope["downstream"]["revision"],
        "scope": scope, "proposal": copy.deepcopy(transaction["input"]),
        "proposal_digest": entry.digest(transaction["input"]),
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


def revision_scope_readiness(state, checkpoint_path):
    """Attest a stopped Stage-3 revision before and after its existing-ref bind."""
    if state.get("stage") == 4 and (state.get("consumed_readiness") or {}).get("revision_transaction_ref"):
        readiness = state["consumed_readiness"]
        prior = (state.get("history") or [])[-1:]
        if (not prior or prior[0].get("stage") != 3 or
                prior[0].get("consumed_readiness") != readiness or
                prior[0].get("scope_revision_transaction", {}).get("state") != "active" or
                prior[0].get("scope_authority") != readiness.get("scope_authority") or
                state.get("scope_authority") != readiness.get("scope_authority") or
                state.get("checkpoint_path") != str(Path(checkpoint_path).resolve())):
            return None
        return copy.deepcopy(readiness)
    if state.get("stage") != 3 or not isinstance(state.get("scope_revision_transaction"), dict):
        return None
    path = Path(checkpoint_path).resolve()
    if state.get("checkpoint_path") != str(path):
        return None
    transaction = state["scope_revision_transaction"]
    if transaction["state"] == "active":
        readiness = state.get("consumed_readiness")
        prior = (state.get("history") or [])[-1:]
        if (not isinstance(readiness, dict) or not readiness.get("revision_transaction_ref") or
                not prior or prior[0].get("scope_revision_transaction", {}).get("state") != "authorized" or
                state.get("handoff", {}).get("readiness") != readiness or
                state.get("scope_authority") != readiness.get("scope_authority") or
                state.get("handoff", {}).get("predecessor") != prior[0].get("handoff", {}).get("predecessor") or
                state.get("dispatch", {}).get("handoff") != state.get("handoff") or
                state.get("scope_revision_transaction", {}).get("input") !=
                    prior[0]["scope_revision_transaction"]["input"]):
            return None
        verify_scope_authority(readiness["scope_authority"],
            initial_seal=prior[0]["scope_revision_transaction"]["old_authority"]["seal"],
            revision_records=(state["control"]["context"].get("handoff_progress") or {}).get("scope_revisions", []),
            requirement_identity=state["handoff"]["requirement_identity"],
            binding=state["handoff"]["binding"],
            decision_records=change_decision_journal(state))
        return copy.deepcopy(readiness)
    if (transaction["state"] != "authorized" or not state.get("stopped") or
            state.get("host", {}).get("status") != "stopped"):
        return None
    outer = read_record(path)
    if outer.get(KEY, {}).get("revision") != state["revision"]:
        return None
    owner = Progress(path, outer)
    if owner.stop_barrier_pending() or owner.unresolved_host_actions() or owner.unresolved_control_transactions():
        return None
    stop = state["host"].get("last_stop")
    if (not isinstance(stop, dict) or not stop.get("provenance") or
            not isinstance(stop.get("receipt"), dict) or
            stop["receipt"].get("ref") != owner.bound_ref() or
            stop["receipt"].get("raw", {}).get("resumable") is not True):
        return None
    receipt = transaction.get("control_result")
    revisions = (state["control"]["context"].get("handoff_progress") or {}).get("scope_revisions", [])
    if not isinstance(receipt, dict) or not revisions or revisions[-1] != receipt:
        return None
    old_scope = transaction["old_authority"]["scope"]
    design = next((item for item in reversed(state.get("history", []))
        if item.get("stage") == 2 and item.get("status") == "accepted"), None)
    if design is None or design.get("accepted") is None:
        return None
    protected = set(old_scope["protected_paths"]) | set(design["accepted"]["payload"]["planning_paths"])
    proposed = set(transaction["proposed_scope"]["implementation_paths"] +
                   transaction["proposed_scope"]["closure_paths"]) - protected
    actual = control_git.scope_revision_snapshot(state["handoff"]["binding"]["worktree"],
        design["accepted"]["payload"]["planning_merge_commit"], old_scope["implementation_paths"],
        sorted(protected), sorted(proposed))
    stage_scope.validate_snapshot(transaction["snapshot"], actual)
    authority = revised_scope_authority(transaction)
    verify_scope_authority(authority, initial_seal=transaction["old_authority"]["seal"],
        revision_records=revisions,
        requirement_identity=receipt["evidence"]["requirement_identity"],
        binding=receipt["evidence"]["binding"],
        decision_records=change_decision_journal(state))
    design_source = (design["accepted"] if design["accepted"].get("kind") ==
                     "legacy-supplemental-acceptance" else design["handoff"])
    stage_scope.validate_scope(authority["scope"], state["handoff"]["binding"]["repository"],
        stage=2, requirement_path=design_source["requirement_identity"]["path"],
        planning_paths=design["accepted"]["payload"]["planning_paths"],
        candidate_commit=authority["candidate_commit"],
        generated_outputs=generated_outputs_for(authority["proposal"]))
    original = state.get("consumed_readiness") or {}
    readiness = {"accepted_digest": design["accepted"]["digest"], "scope_revision": authority["revision"],
            "scope_seal": authority["seal_ref"], "scope_authority": authority,
            "publication": copy.deepcopy(original.get("publication")),
            "stop_evidence_ref": entry.digest(stop),
            "phase_finalization_ref": original.get("phase_finalization_ref"),
            "controller_ref": design_source["controller_ref"],
            "control_revision": state["control"]["context"].get("revision"),
            "checkpoint_path": str(path), "checkpoint_revision": state["revision"],
            "revision_transaction_ref": entry.digest({"input": transaction["input"],
                "snapshot": transaction["snapshot"], "control_result": receipt}),
            "revision_snapshot_digest": entry.digest(transaction["snapshot"]),
            "prior_dispatch_digest": state["dispatch"]["digest"]}
    if original.get("legacy_recovery_id"):
        readiness.update({key: original[key] for key in
            ("legacy_recovery_id", "original_snapshot_sha256", "original_accepted_digest")})
    activation = transaction.get("activation")
    if activation is not None:
        frozen = activation["readiness"]
        require({key: value for key, value in readiness.items() if key != "checkpoint_revision"} ==
                {key: value for key, value in frozen.items() if key != "checkpoint_revision"},
                "scope_changed", "revision activation changed stopped authority")
        return copy.deepcopy(frozen)
    return readiness


def _view(state, next_action=None, acknowledged=False, *, verified_readiness=None):
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
    readiness = (verified_readiness if verified_readiness is not None else
        legacy_scope_readiness(state, state.get("checkpoint_path"))
        if state.get("recovery_mode") == "legacy-takeover" and state.get("status") == "accepted" else
        scope_readiness(state, state.get("checkpoint_path"))
        if state.get("stage") == 2 and state.get("status") == "accepted" else None)
    planning_complete = state.get("stage") == 2 and state.get("status") == "accepted" and state.get("accepted") is not None
    revision_stable = (state.get("scope_revision_transaction") or {}).get("state") in (None, "active")
    downstream_ready = (readiness is not None if state.get("stage") == 2 else
        revision_stable and state["status"] == "accepted" and state.get('stopped', False) and state.get("phase_complete", True) and
        bool((state.get("accepted") or {}).get("downstream_ready")))
    result = {"protocol": PROTOCOL, "revision": state["revision"], "status": state["status"],
              "step": state["step"], "pending": state.get("pending"), "next_action": next_action,
              "planning_complete": planning_complete, "handoff_ready": readiness is not None,
              "downstream_ready": downstream_ready, "acknowledged": acknowledged}
    if planning_complete:
        stable_scope = (state.get("scope_revision_transaction") or {}).get("state") in (None, "active")
        result["route"] = "stage-3" if readiness is not None else "planning-only" if stable_scope and (state.get("scope_authority") or {}).get("scope", {}).get("downstream", {}).get("implementation_required") is False else "pending"
        if readiness is not None:
            result["readiness"] = readiness
    if state["status"] == "accepted" and state.get('stopped', False) and state.get("accepted") is not None and (state["stage"] == 2 or downstream_ready) and state.get("phase_complete", True):
        if state["stage"] == 4:
            result["workflow_completion"] = verify_completion(state)
        result["completion"] = (handoff._render_verified(state["accepted"], readiness)
            if state["stage"] == 2 else handoff.render(state["accepted"]))
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
        if previous.get("kind") == "legacy-supplemental-acceptance":
            outer = read_record(state["checkpoint_path"])
            proof = verify_legacy_recovery(outer, state["checkpoint_path"])
            recovery = state["scope_recoveries"][proof["recovery_id"]]
            require(recovery["state"] == "consumed" and recovery["bridge"] == previous,
                    "result_changed", "final closure lost the exact supplemental Stage-2 bridge")
            original = json.loads(base64.b64decode(recovery["original_bytes_base64"], validate=True))
            verify_phase_completed(original[KEY])
            root = saved["binding"]["repository"]
            handoff.ancestor(root, previous["payload"]["planning_commit"],
                             previous["payload"]["planning_merge_commit"])
            handoff.ancestor(root, previous["payload"]["planning_merge_commit"], payload["merge_commit"])
            break
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
        self._verified_readiness = None

    def save(self):
        self._verified_readiness = None
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

    def current_verified_readiness(self):
        saved = self._verified_readiness
        return copy.deepcopy(saved[1]) if saved is not None and saved[0] == self.state["revision"] else None

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

    def native_refs(self, state=None):
        """Registered writers, including replaced roles and historical reviewers."""
        s = self.state if state is None else state
        refs = set()
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
                'executions': (selected.get('handoff_progress') or {}).get('executions', [])})

    def collaboration_pending(self):
        s = self.state
        snapshot = s.get('lifecycle_snapshot')
        if snapshot is None:
            return [{'kind': 'current-descendants-missing'}]
        if snapshot.get('guard') != self.collaboration_guard():
            return [{'kind': 'current-descendants-stale'}]
        expected = set(s['native_host']['baseline_refs']) | self.native_refs()
        expected.update(ref for old in s.get('history', []) for ref in self.native_refs(old))
        return collaboration_inventory_pending(snapshot['inventory'], expected)

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
        collaboration_inventory(data['inventory'])
        require(data['inventory']['controller_ref'] == s['native_host']['controller_ref'],
                'identity_mismatch', 'retain the original controller agent path')
        # Trusted ingress authenticates these actual tool identities. Never
        # relabel cached raw bytes as a new call to refresh a stale barrier.
        # Keep conflicts as observations too, without overwriting the original
        # query's receipt or allowing rejected provenance to be recycled.
        s.setdefault('collaboration_lookups', {})[entry.digest(data)] = copy.deepcopy(data)
        self.save()
        for state in [s, *s.get('history', [])]:
            for previous in state.get('collaboration_lookups', {}).values():
                if (previous['query_id'] == data['query_id'] or previous['call_ref'] == data['call_ref'] or
                        previous['response_ref'] == data['response_ref']):
                    require(previous == data, 'host_provenance_conflict', 'inventory response cannot be rebound to a new query')
        require(query is not None and query['query_id'] == data['query_id'] and
                query['guard'] == self.collaboration_guard(),
                'host_evidence_missing', 'obtain a new inventory after native activity or changed writer bindings')
        s['lifecycle_snapshot'] = {**copy.deepcopy(data), 'guard': query['guard'],
                                   'instance': s['native_host']['instance']}
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
        revision = self.state.get("scope_revision_transaction") or {}
        require(self.stop_intent() is None and revision.get("state") in (None, "active"), "progression_suspended",
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
        if (self.state or {}).get("recovery_mode") == "legacy-takeover":
            return self.start_legacy_stage3(data)
        require((self.state or {}).get("scope_revision_transaction", {}).get("state") in (None, "active"),
                "scope_revision_pending", "finish the original scope revision before starting another stage")
        require(self.outer.get('transport', {}).get('state') != 'lost', 'host_recovery_required', 'lost foreground transport cannot create a native role')
        entry.fields(data, {"handoff"}, {"control", "next_stage", "requirement_transaction", "native_host", "workspace_evidence"})
        request = copy.deepcopy(data["handoff"])
        require(request.get("operation") == "prepare", "invalid_request", "handoff prepare input required")
        old = self.state
        consumed_readiness = None
        retry = old is not None and old["status"] == "cancelled" and (old.get("dispatch") or {}).get("status") == "not-created" and not old.get("stop_requested")
        if old is not None:
            validate_state(old)
            self.require_controls_reconciled()
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
            elif not retry and old.get("scope_recoveries") and request["stage"] == 4:
                consumed_readiness = copy.deepcopy(old.get("consumed_readiness"))
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
                    require(not collaboration_inventory_pending(data['native_host'],
                            expected | historical | set(native_host['baseline_refs'])),
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
        # A resolves/rechecks actual cwd and complete host registration before B.
        resolved = entry.resolve({**request["entry"], "operation": "verify", "expected": request["expected_entry"]})
        mode = request["authorization"]["flow_mode"]
        require(old is None or mode == old["mode"], "authorization_changed", "mode cannot change without its exact decision")
        for root in (resolved["repository"]["root"], (request.get("binding") or {}).get("worktree")):
            require(root is None or not self.path.resolve().is_relative_to(Path(root).resolve()),
                    "unsafe_checkpoint", "checkpoint must live outside repository and disposable worktree")
        saved = handoff.handle(request)
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
        self.state = {"protocol": PROTOCOL, "revision": old["revision"] if old else 0,
            "mode": mode, "stage": request["stage"], "status": "active", "step": "prepare-dispatch",
            "next_stage": next_stage, "phase_complete": not phase_required,
            "packages": {**retained, **resolved["packages"]}, "handoff": saved, "control": copy.deepcopy(port),
            "checkpoint_path": str(self.path.resolve()),
            "scope_authority": copy.deepcopy(old.get("scope_authority") if old and request["stage"] >= 3 else None),
            "consumed_readiness": consumed_readiness,
            "scope_recoveries": copy.deepcopy(old.get("scope_recoveries", {}) if old else {}),
            "scope_proposals": {}, "scope_seals": {},
            "dispatch": None, "accepted": None, "action": None, "pending": None,
            "events": {}, "observations": [], "transaction": None, "transaction_source": None,
            "transaction_result": None, "stopped": False,
            "retained_host_actions": [],
            "native_host": native_host,
            "workspace_evidence": selection,
            "host_preflight": copy.deepcopy(data.get('native_host')),
            "host": {"generation": 0, "status": "unknown", "proof": None, "query": None,
                     "seen": {}, "calls": {}, "last_stop": None},
            "history": (old["history"] + [{k: copy.deepcopy(value) for k, value in old.items() if k != "history"}]) if old else []}
        self.save()
        return self.advance()

    def reserve_legacy_stage3(self, record, request):
        ledger = record.get("ledger")
        if ledger is None:
            return
        phase = (request.get("authorization") or {}).get("phase")
        require(isinstance(phase, dict) and set(phase) == {"run_id", "attempt_id"} and
                all(isinstance(value, str) and value for value in phase.values()),
                "phase_authorization_required", "attached recovery needs its actual Stage-3 Phase Run")
        original_input = record.get("reservation_input")
        require(original_input is None or original_input == request,
                "scope_conflict", "retain the exact original attached Stage-3 launch intent")
        if ledger.get("reservation_request") is None:
            current = self.state["control"]["discussion"]
            require(current is not None, "control_context_changed", "original Topic control envelope is missing")
            evidence = {"recovery_id": record["recovery_id"],
                "checkpoint_path": str(self.path.resolve()),
                "original_checkpoint_sha256": record["original_checkpoint_sha256"],
                "bridge_digest": record["bridge"]["digest"],
                "phase_run_id": phase["run_id"], "phase_attempt_id": phase["attempt_id"],
                "controller_ref": record["controller_ref"],
                "reservation_id": entry.digest({"recovery_id": record["recovery_id"],
                    "bridge_digest": record["bridge"]["digest"], "phase": phase})}
            ledger["reservation_request"] = {**copy.deepcopy(current),
                "operation": "workflow-control", "action": "scope-recover-legacy-reserve",
                "evidence": evidence}
            record["reservation_input"] = copy.deepcopy(request)
            self.save()
        if ledger.get("reservation_receipt") is None:
            ledger["reservation_receipt"] = entry.discussion_protocol.handle(
                copy.deepcopy(ledger["reservation_request"]))
            self.save()
        receipt = ledger["reservation_receipt"]
        require(receipt.get("state") == "launch-reserved" and
                receipt.get("reservation") == {key: value for key, value in
                    ledger["reservation_request"]["evidence"].items() if key != "controller_ref"} and
                receipt.get("control", {}).get("context", {}).get("legacy_takeover", {}).get("reservation") ==
                    receipt["reservation"],
                "phase_authorization_required", "Topic did not reserve this exact Stage-3 launch")
        if self.state["control"]["context"] != receipt["control"]["context"]:
            self.state["control"]["context"] = copy.deepcopy(receipt["control"]["context"])
            self.state["control"]["discussion"].update(
                expected_ledger_revision=receipt["ledger_revision"],
                expected_topic_revision=receipt["record_revision"],
                idempotency_key=str(uuid.uuid4()))
            self.save()
        query = {key: value for key, value in self.state["control"]["discussion"].items()
                 if key not in {"expected_ledger_revision", "expected_topic_revision", "idempotency_key"}}
        topic = entry.discussion_protocol.handle({**query, "operation": "read-topic"})
        require(topic["workflow_control"] == self.state["control"]["context"] and
                topic["workflow_control"]["legacy_takeover"]["reservation"] == receipt["reservation"],
                "control_context_changed", "Topic reservation changed before local Stage-3 consumption")

    def start_legacy_stage3(self, data):
        """Consume the supplemental authority with one new Stage-3 launch intent."""
        entry.fields(data, {"handoff", "native_host"}, {"workspace_evidence", "control", "next_stage"})
        old = self.state
        proof = verify_legacy_recovery(self.outer, self.path)
        require(proof["state"] == "authorized" and old["stage"] == 2 and
                old["recovery_mode"] == "legacy-takeover", "legacy_takeover_pending",
                "only authorized supplemental scope can start Stage 3")
        record = old["scope_recoveries"][proof["recovery_id"]]
        bridge = record["bridge"]
        request = copy.deepcopy(data["handoff"])
        require(request.get("operation") == "prepare" and request.get("stage") == 3 and
                request.get("role") == "implementation-dispatcher",
                "invalid_stage", "recovery can launch only the first implementation Dispatcher")
        readiness = legacy_scope_readiness(old, self.path)
        require(readiness is not None, "scope_incomplete",
                "legacy supplemental implementation scope is not ready")
        scope = copy.deepcopy(record["supplemental_authority"]["scope"])
        scope.update(baseline=bridge["payload"]["planning_merge_commit"],
            owned_paths=copy.deepcopy(scope["implementation_paths"]),
            protected_paths=sorted(set(scope["protected_paths"] + bridge["payload"]["planning_paths"])))
        require("scope" not in request or request["scope"] == scope,
                "scope_changed", "Stage-3 input must retain the supplemental manifest and planning protection")
        require("predecessor" not in request or request["predecessor"] == bridge,
                "predecessor_changed", "Stage-3 input must consume the current supplemental bridge")
        require("readiness" not in request or request["readiness"] == readiness,
                "scope_changed", "Stage-3 input must consume current legacy readiness")
        require("binding" not in request or request["binding"] == bridge["binding"],
                "binding_changed", "recovery must retain the exact Flow Worktree")
        requirement = ((self.outer.get("confirmed") or {}).get("requirement") or
                       old["handoff"]["requirement"])
        require("requirement" not in request or request["requirement"] == requirement,
                "source_changed", "recovery must retain the original frozen requirement")
        request.update(scope=scope, predecessor=bridge, readiness=readiness,
                       binding=copy.deepcopy(bridge["binding"]), requirement=copy.deepcopy(requirement))
        require(request["authorization"]["scope_digest"] == entry.digest(scope) and
                request["authorization"]["flow_mode"] == old["mode"] and
                request["target"] == {"repository": bridge["binding"]["repository"],
                                      "branch": bridge["binding"]["target_branch"]},
                "authorization_changed", "new Controller authorization must bind exact supplemental Stage-3 scope")
        retain_entry_pins(request, old["packages"])
        resolved = entry.resolve({**request["entry"], "operation": "verify",
                                  "expected": request["expected_entry"]})
        require(not self.path.resolve().is_relative_to(Path(resolved["repository"]["root"]).resolve()) and
                not self.path.resolve().is_relative_to(Path(bridge["binding"]["worktree"]).resolve()),
                "unsafe_checkpoint", "checkpoint must remain outside source and worktree")
        native = native_host_preflight(data["native_host"])
        if native["adapter"] == "app-server":
            require(data["native_host"]["snapshot"]["carrier_thread"] ==
                    request["entry"]["host"]["thread_id"],
                    "identity_mismatch", "new native host must inspect the new Stage-3 carrier")
        else:
            native["instance"] = request["entry"]["host"]["thread_id"]
            native["baseline_refs"] = sorted(set(collaboration_inventory(data["native_host"])) -
                                             {data["native_host"]["controller_ref"]})
        selected_workspace = workspace_evidence(bridge["binding"], bridge["controller_ref"],
            data.get("workspace_evidence", record["workspace_evidence"]))
        # Validate the complete local B/A/Git input before reserving the Topic.
        # Reservation is durable and must not be consumed by a malformed request.
        handoff.handle(request)
        self.reserve_legacy_stage3(record, data["handoff"])
        refreshed_readiness = legacy_scope_readiness(old, self.path)
        require(refreshed_readiness is not None, "scope_incomplete",
                "Topic reservation changed the supplemental handoff")
        request["readiness"] = refreshed_readiness
        saved = handoff.handle(request)
        context = copy.deepcopy(old["control"]["context"])
        context.update(stage=3, carrier=None, handoff_progress=None,
                       requirement_identity=copy.deepcopy(bridge["requirement_identity"]))
        control.validate_context(context)
        port = {"context": context, "discussion": None}
        if "control" in data:
            require(data["control"] == port, "control_context_changed",
                    "recovered Stage-3 control must derive from original Controller authority")
        history = [*old.get("history", []), {key: copy.deepcopy(value) for key, value in old.items()
                                              if key != "history"}]
        current = {"protocol": PROTOCOL, "revision": old["revision"], "mode": old["mode"],
            "stage": 3, "status": "active", "step": "prepare-dispatch", "next_stage": 4,
            "phase_complete": "phase" not in request["authorization"],
            "packages": copy.deepcopy(old["packages"]), "handoff": saved, "control": port,
            "checkpoint_path": str(self.path.resolve()),
            "scope_authority": copy.deepcopy(record["supplemental_authority"]),
            "scope_recoveries": copy.deepcopy(old["scope_recoveries"]),
            "consumed_readiness": copy.deepcopy(refreshed_readiness),
            "recovery_start_input": copy.deepcopy(data),
            "dispatch": None, "accepted": None, "action": None, "pending": None,
            "events": {}, "observations": [], "transaction": None,
            "transaction_source": None, "transaction_result": None,
            "stopped": False, "retained_host_actions": [],
            "native_host": native, "host_preflight": copy.deepcopy(data["native_host"]),
            "workspace_evidence": selected_workspace,
            "host": {"generation": 0, "status": "unknown", "proof": None, "query": None,
                     "seen": {}, "calls": {}, "last_stop": None},
            "history": history}
        current["scope_recoveries"][proof["recovery_id"]]["state"] = "consumed"
        self.state = current
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
                slots = s.get('review_activity', {})
                require(set(slots) == {'standards','spec'} and all(
                    slots[axis]['candidate'] == payload['candidate_commit'] and slots[axis]['stopped'] and
                    slots[axis]['ref'] == payload['review'][axis]['reviewer_ref'] for axis in slots),
                    'review_incomplete', 'both stopped native reviewers must match the exact candidate')
        if (s["stage"] == 2 and (operation == "accept" or
                operation == "receive" and extra.get("result", {}).get("status") == "completed")):
            authority = s.get("scope_authority")
            require(authority is not None and authority.get("state") == "sealed", "scope_incomplete",
                    "seal the complete planning scope before accepting its publication")
            extra.update(scope_authority=copy.deepcopy(authority), scope_checkpoint_path=str(self.path.resolve()))
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
            s["pending"] = (stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"])
                            if s["stage"] != 2 and s["accepted"]["downstream_ready"] and s["phase_complete"] else None)
            s.setdefault("decisions", {})[source["decision_id"]] = copy.deepcopy(source)
            s["last_decision"] = copy.deepcopy(source)
        if source is not None and "event_id" in source:
            s["events"][source["event_id"]]["applied"] = True
            if "result" in source:
                s.setdefault("observed_messages", {})[self.message_key(source)] = source["event_id"]
        s["transaction"], s["transaction_source"], s["transaction_result"] = None, None, None
        s["last_result"] = result
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
        proposal_digest = entry.digest(data)
        scope = copy.deepcopy(original_scope)
        scope.update(projection)
        scope["downstream"] = {"state": "proposed", "revision": 0,
            "implementation_required": data["implementation_required"],
            "manifest": copy.deepcopy(data["manifest"]), "proposal_ref": proposal_digest,
            "seal_ref": None}
        stage_scope.validate_scope(scope, saved["binding"]["repository"], stage=2,
            requirement_path=saved["requirement_identity"]["path"], candidate_commit=candidate,
            generated_outputs=generated_outputs)
        record = {"input": copy.deepcopy(data), "digest": proposal_digest, "scope": scope,
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
                     "proposal": copy.deepcopy(proposal["input"]), "proposal_digest": proposal["digest"],
                     "seal": copy.deepcopy(seal), "seal_ref": seal["seal_ref"], "candidate_commit": candidate,
                     "approved_changes": [change_evidence] if change_evidence is not None else []}
        s["scope_authority"] = authority
        s["scope_seals"][data["proposal_id"]] = {"input": copy.deepcopy(data), "authority_digest": entry.digest(authority)}
        self.save()
        return _view(s, {"operation": "scope-sealed", "authority": authority})

    def scope_revise(self, data):
        """Stop old leases, snapshot Git, and authorize one manifest revision.

        A Stage-3 writer keeps its old immutable B request while stopped. Its
        authorized revision waits for a new Stage-3 attempt; only a Stage-2
        accepted run with no implementation writer can activate in place.
        """
        entry.fields(data, {"revision_id", "expected_scope_revision", "previous_scope_digest",
                            "requirement_identity", "binding", "planning_commit", "planning_merge_commit",
                            "candidate_commit", "old_manifest", "manifest", "implementation_required",
                            "diff", "reason", "controller_ref", "decision"}, {"generated_outputs"})
        entry.nonempty(data["revision_id"]); entry.nonempty(data["reason"])
        s = self.state
        require(s.get("recovery_mode") != "legacy-takeover", "legacy_supersede_required",
                "unconsumed supplemental Stage-2 scope changes must use explicit scope-recover-legacy supersede")
        history = s.setdefault("scope_revisions", {})
        prior = history.get(data["revision_id"])
        if prior is not None:
            require(prior["input"] == data, "scope_conflict", "revision ID was reused with different content")
            return _view(s, {"operation": "scope-revision", "revision": prior}, acknowledged=True)
        transaction = s.get("scope_revision_transaction")
        if transaction is not None:
            require(transaction["input"] == data, "scope_conflict", "finish the original scope revision first")
        else:
            self.require_business_ready()
            require(s["stage"] in {2, 3} and (s.get("scope_authority") or {}).get("state") == "sealed" and
                    (s["stage"] != 2 or s["status"] == "accepted") and
                    (s["stage"] != 3 or not s.get("publication")),
                    "scope_revision_unavailable", "revision requires an accepted design and no later publication")
            require(s["stage"] != 3 or s["status"] != "cancelled",
                    "stage3_revision_attempt_required", "cancelled Stage-3 attempt cannot revise its scope")
            require(s["transaction"] is None and not self.unresolved_control_transactions() and
                    not any(slot.get("transaction") for slot in s.get("allocations", {}).values()),
                    "transaction_pending", "settle prior business and allocation transactions")
            authority = s["scope_authority"]
            old_scope = authority["scope"]
            require(type(data["expected_scope_revision"]) is int and
                    data["expected_scope_revision"] == authority["revision"] and
                    data["previous_scope_digest"] == entry.digest(old_scope) and
                    data["old_manifest"] == old_scope["downstream"]["manifest"],
                    "stale_scope_revision", "revision must name the exact current manifest and digest")
            design = s if s["stage"] == 2 else next((item for item in reversed(s.get("history", []))
                if item.get("stage") == 2 and item.get("status") == "accepted"), None)
            require(design is not None and design.get("accepted") is not None,
                    "scope_revision_unavailable", "accepted planning source is unavailable")
            payload = design["accepted"]["payload"]
            design_source = (design["accepted"] if design["accepted"].get("kind") ==
                             "legacy-supplemental-acceptance" else design["handoff"])
            current_binding = (s["handoff"]["binding"] if s["stage"] == 3 else
                               design_source["binding"])
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
            proposed_scope["downstream"] = {"state": "proposed", "revision": authority["revision"] + 1,
                "implementation_required": data["implementation_required"],
                "manifest": copy.deepcopy(data["manifest"]), "proposal_ref": entry.digest(data),
                "seal_ref": None}
            stage_scope.validate_scope(proposed_scope, data["binding"]["repository"], stage=2,
                requirement_path=data["requirement_identity"]["path"],
                planning_paths=payload["planning_paths"], candidate_commit=data["candidate_commit"],
                generated_outputs=generated_outputs)
            change_evidence = approved_change_evidence(s, data["decision"])
            transaction = {"input": copy.deepcopy(data), "state": "proposed",
                "old_authority": copy.deepcopy(authority), "proposed_scope": proposed_scope,
                "snapshot": None, "control_result": None,
                "approved_change_evidence": change_evidence,
                "old_pending": copy.deepcopy(s.get("pending"))}
            s["scope_revision_transaction"] = transaction
            self.save()
            transaction["state"] = "stopping"
            s["pending"] = None
            self.save()
        if transaction["state"] == "stopping":
            if self.stop_intent() is not None:
                return _view(s, {"operation": "await-original-stop-intent", "scope_revision": data["revision_id"]})
            if self.unresolved_host_actions():
                return self.query_host()
            if s["stage"] == 3 and s["host"]["status"] != "stopped" and self.bound_ref() is not None:
                return self.effect("stop-host", {"subject": self.subject(), "ref": self.bound_ref(),
                                                 "scope_revision": data["revision_id"]})
            pending = self.stop_barrier_pending()
            if not s.get("stopped") or pending:
                return _view(s, {"operation": "reconcile-stopped-writers", "pending": pending,
                                 "scope_revision": data["revision_id"]})
            design = s if s["stage"] == 2 else next(item for item in reversed(s["history"])
                if item.get("stage") == 2 and item.get("status") == "accepted")
            old_scope = transaction["old_authority"]["scope"]
            published_paths = design["accepted"]["payload"]["planning_paths"]
            protected = set(old_scope["protected_paths"]) | set(published_paths)
            proposed_paths = set(transaction["proposed_scope"]["implementation_paths"] +
                                 transaction["proposed_scope"]["closure_paths"]) - protected
            allowed = old_scope["implementation_paths"] if s["stage"] == 3 else []
            snapshot = control_git.scope_revision_snapshot(s["handoff"]["binding"]["worktree"],
                design["accepted"]["payload"]["planning_merge_commit"], allowed,
                sorted(protected), sorted(proposed_paths))
            changed = {item["path"] for item in data["diff"]["removed"]} | {
                item["before"]["path"] for item in data["diff"]["changed"]}
            require(not changed & set(snapshot["touched"]) and
                    not changed & {path for slot in s.get("allocations", {}).values()
                                   for path in (slot.get("handoff") or {}).get("scope", {}).get("implementation_paths", [])},
                    "scope_changed", "cannot remove or change a path with existing implementation work")
            transaction.update(state="quiescent", snapshot=snapshot)
            self.save()
        if transaction["state"] == "quiescent":
            old_scope = transaction["old_authority"]["scope"]
            design = s if s["stage"] == 2 else next(item for item in reversed(s["history"])
                if item.get("stage") == 2 and item.get("status") == "accepted")
            protected = set(old_scope["protected_paths"]) | set(design["accepted"]["payload"]["planning_paths"])
            proposed_paths = set(transaction["proposed_scope"]["implementation_paths"] +
                                 transaction["proposed_scope"]["closure_paths"]) - protected
            actual = control_git.scope_revision_snapshot(s["handoff"]["binding"]["worktree"],
                data["planning_merge_commit"], old_scope["implementation_paths"] if s["stage"] == 3 else [],
                sorted(protected), sorted(proposed_paths))
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
            result = self.control_action({"action": "scope-revise", "evidence": evidence,
                "receipt": {"controller_ref": data["controller_ref"],
                            "reference": data["decision"].get("reference")}})
            receipt = s["control"]["context"]["handoff_progress"]["scope_revisions"][-1]
            transaction.update(state="authorized", control_result=copy.deepcopy(receipt))
            self.save()
        if transaction["state"] == "authorized":
            if s["stage"] == 3:
                return _view(s, {"operation": "stage3-revision-attempt", "revision_id": data["revision_id"],
                                 "reason": "old Stage-3 dispatch remains sealed to the prior scope"})
            old_scope = transaction["old_authority"]["scope"]
            receipt = transaction["control_result"]
            scope = copy.deepcopy(transaction["proposed_scope"])
            scope["downstream"].update(state="sealed", seal_ref=receipt["revision_ref"])
            stage_scope.validate_scope(scope, data["binding"]["repository"], stage=2,
                requirement_path=data["requirement_identity"]["path"],
                planning_paths=s["accepted"]["payload"]["planning_paths"],
                candidate_commit=data["candidate_commit"],
                generated_outputs=generated_outputs_for(data))
            old = transaction["old_authority"]
            s["scope_authority"] = {**copy.deepcopy(old), "state": "sealed",
                "revision": scope["downstream"]["revision"], "scope": scope,
                "proposal": copy.deepcopy(data), "proposal_digest": entry.digest(data),
                "seal_ref": receipt["revision_ref"],
                "revisions": [*old.get("revisions", []), copy.deepcopy(receipt)]}
            transaction["state"] = "active"
            history[data["revision_id"]] = copy.deepcopy(transaction)
            s["pending"] = None
            self.save()
            if scope_readiness(s, self.path):
                s["pending"] = stage_boundary(s["mode"], s["stage"], s["accepted"], s["next_stage"])
                self.save()
            return _view(s, {"operation": "scope-revision-active", "authority": s["scope_authority"]})
        return _view(s, {"operation": "scope-revision", "revision": transaction})

    def activate_scope_revision(self, data):
        """Create a revised B attempt bound to the same stopped Dispatcher ref."""
        entry.fields(data, {"revision_id", "handoff"})
        s = self.state
        txn = s.get("scope_revision_transaction") or {}
        activation = txn.get("activation")
        if txn.get("state") == "active" and activation is not None:
            require(activation["input"] == data, "scope_conflict", "revision activation input changed")
            return _view(s, acknowledged=True)
        require(s["stage"] == 3 and txn.get("state") == "authorized" and
                txn["input"]["revision_id"] == data["revision_id"],
                "scope_revision_pending", "authorize the exact stopped Stage-3 revision first")
        readiness = revision_scope_readiness(s, self.path)
        require(readiness is not None, "host_recovery_required",
                "current stopped snapshot and resumable Dispatcher evidence required")
        original = s["handoff"]
        design = next(item for item in reversed(s["history"])
            if item.get("stage") == 2 and item.get("status") == "accepted")
        new_scope = copy.deepcopy(original["scope"])
        new_scope.update(implementation_paths=copy.deepcopy(readiness["scope_authority"]["scope"]["implementation_paths"]),
                         closure_paths=copy.deepcopy(readiness["scope_authority"]["scope"]["closure_paths"]),
                         downstream=copy.deepcopy(readiness["scope_authority"]["scope"]["downstream"]),
                         baseline=txn["snapshot"]["head"],
                         owned_paths=copy.deepcopy(readiness["scope_authority"]["scope"]["implementation_paths"]))
        request = copy.deepcopy(data["handoff"])
        require("generated_outputs" not in request or
                request["generated_outputs"] == generated_outputs_for(readiness["scope_authority"]["proposal"]),
                "scope_changed", "revision must use the generated inventory sealed by C")
        request.pop("generated_outputs", None)
        supplied_entry = request.get("entry")
        old_entry = original["entry"]
        require(isinstance(supplied_entry, dict) and
                {key: value for key, value in supplied_entry.items() if key != "pinned_packages"} ==
                {key: value for key, value in old_entry.items() if key != "pinned_packages"},
                "identity_mismatch", "revision must retain the original Dispatcher entry and host identity")
        current_entry = entry.resolve({**old_entry, "operation": "resolve"})
        old_expected = original["expected_entry"]
        require(current_entry["actor"] == old_expected["actor"] and
                current_entry["packages"] == old_expected["packages"] and
                current_entry["repository"]["head"] == txn["snapshot"]["head"] and
                current_entry["repository"]["root"] == old_expected["repository"]["root"] and
                (request.get("expected_entry") is None or
                 request["expected_entry"] == old_expected or request["expected_entry"] == current_entry),
                "entry_changed", "revision entry must retain pinned actor/packages at the stopped Git HEAD")
        request["entry"] = copy.deepcopy(old_entry)
        request["expected_entry"] = current_entry
        require(request.get("operation") == "prepare" and request.get("stage") == 3 and
                request.get("role") == "implementation-dispatcher" and
                request.get("predecessor") == design["accepted"] and
                request.get("binding") == original["binding"] and
                request.get("requirement") == original["requirement"] and
                request.get("scope") == new_scope and
                request.get("authorization", {}).get("reference") == txn["input"]["decision"]["reference"] and
                request["authorization"]["scope_digest"] == entry.digest(new_scope) and
                request["authorization"]["flow_mode"] == s["mode"] and
                request.get("readiness", readiness) == readiness and
                request.get("target") == original["target"] and
                request.get("configuration") == original["configuration"],
                "scope_changed", "revised attempt must retain original Dispatcher, source and exact new ceiling")
        old_semantic = original["semantic"]
        new_semantic = request.get("semantic") or {}
        stable_fields = {"objective", "testing_basis", "completion_criteria", "constraints", "validation_plan"}
        require(all(new_semantic.get(key) == old_semantic.get(key) for key in stable_fields),
                "scope_changed", "scope refinement cannot silently change implementation behavior")
        if (old_semantic.get("implementation_policy") or {}).get("mode") == "direct":
            require(new_semantic.get("implementation_policy") != old_semantic.get("implementation_policy") and
                    new_semantic.get("implementation_policy") is not None,
                    "policy_reevaluation_required", "direct policy must be freshly evaluated for the new scope digest")
        request["readiness"] = readiness
        if activation is None:
            saved = handoff.handle(request)
            b_request = {"protocol": handoff.PROTOCOL, "operation": "prepare", "handoff": saved,
                         "control": copy.deepcopy(s["control"])}
            activation = {"input": copy.deepcopy(data), "handoff": saved, "request": b_request,
                          "result": None, "readiness": readiness}
            txn["activation"] = activation
            self.save()
        else:
            require(activation["input"] == data and activation["readiness"] == readiness,
                    "scope_conflict", "resume the exact revision activation transaction")
        if activation["result"] is None:
            activation["result"] = dispatch.handle(copy.deepcopy(activation["request"]))
            self.save()
        result = activation["result"]
        require(result.get("status") == "bound" and result.get("record", {}).get("status") == "bound" and
                result["record"]["handoff"] == activation["handoff"] and
                result["checkpoint"]["context"]["carrier"]["ref"] == self.bound_ref(),
                "scope_revision_pending", "B must bind the revised attempt to the original stopped Dispatcher")
        previous = {key: copy.deepcopy(value) for key, value in s.items() if key != "history"}
        updated = copy.deepcopy(s)
        discussion = copy.deepcopy(s["control"]["discussion"])
        applied = result["checkpoint"].get("discussion_receipt")
        if discussion is not None and applied is not None:
            discussion.update(expected_ledger_revision=applied["ledger_revision"],
                expected_topic_revision=applied["record_revision"], idempotency_key=str(uuid.uuid4()))
        for field in ("allocations", "review_activity", "review_history", "publication", "publication_candidate",
                      "closure_effects", "closure_receipt", "last_observation", "last_decision", "decisions",
                      "business_block", "error", "recovery_action", "transport_loss", "deferred_validation",
                      "deferred_observation", "host_action", "action", "scope_revision_late_results"):
            updated.pop(field, None)
        updated.update(history=[*s["history"], previous], handoff=activation["handoff"],
            control={"context": copy.deepcopy(result["checkpoint"]["context"]), "discussion": discussion},
            dispatch=copy.deepcopy(result["record"]),
            scope_authority=copy.deepcopy(readiness["scope_authority"]),
            consumed_readiness=copy.deepcopy(readiness), accepted=None, pending=None,
            events={}, observations=[], transaction=None, transaction_source=None,
            transaction_result=None, status="active", step="continue",
            retained_host_actions=[],
            continuation_intent={"kind": "scope-revision", "revision_id": data["revision_id"],
                                 "revision_transaction_ref": readiness["revision_transaction_ref"]})
        updated["scope_revision_transaction"]["state"] = "active"
        updated["scope_revisions"][data["revision_id"]] = copy.deepcopy(updated["scope_revision_transaction"])
        self.state = updated
        self.save()
        return self.advance()

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
        if receipt["status"] == "stopped" and causal:
            checkpoint = s['control']['context'].get('handoff_progress') or {}
            if control.direct_implementation(checkpoint) or 'design_input' in checkpoint:
                try:
                    observation['git_snapshot'] = control_git.recovery_snapshot(Path(checkpoint['binding']['worktree']), checkpoint)[0]
                except control.ControlError as error:
                    observation['snapshot_error'] = str(error)
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
                if value.get('result') is None and value.get('rejection') is None and not
                (value.get('scope') == 'archive' and value.get('handoff_completed') is True and value.get('intent_persisted') is True)}

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
        implementation = s['control']['context'].get('handoff_progress') or {}
        if control.direct_implementation(implementation):
            target = control_git.git(Path(implementation['binding']['worktree']), 'rev-parse',
                                     implementation['binding']['target_branch']).decode().strip()
            require(target == implementation.get('implementation_target_head'), 'implementation_escalation_required',
                    'target advanced; escalate to full before continuing direct implementation')
        if (s.get('resume_intent') or {}).get('operation') == 'resume-original-paused-scope':
            self.verify_direct_resume()
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
                        proof["receipt"].get("raw", {}).get("resumable") is True,
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
        if (s.get("scope_revision_transaction") or {}).get("state") not in (None, "active"):
            return self.scope_revise(s["scope_revision_transaction"]["input"])
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
                   (s.get("continuation_intent") or {}).get("kind") == "scope-revision" else {}),
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
        revision = s.get("scope_revision_transaction") or {}
        if revision.get("state") not in (None, "active") and receipt["event"] == "result":
            # An old writer's late result is evidence for stopping, not a new
            # B delivery or candidate acceptance under the revoked scope.
            s.setdefault("scope_revision_late_results", []).append(copy.deepcopy(data))
            s["events"][data["event_id"]]["applied"] = True
            self.save()
            return self.scope_revise(revision["input"])
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
        require((self.state.get("scope_revision_transaction") or {}).get("state") in (None, "active"),
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
            payload.update(candidate_commit=s["handoff"]["predecessor"]["payload"]["candidate_commit"],
                           merge_commit=result["merge_commit"],
                           cleanup=supervision.verify_disposition(binding, result["candidate_commit"], result["merge_commit"]),
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
                    "scope-seal", "scope-revise", "escalate-implementation", "prepare-dispatch-recovery", "dispatch-recovery-intent", "dispatch-recovery-result", "recover-dispatch", "release-recovery-allocation", "candidate-ready", "review-converged", "validation-start", "validation-result", "validation-retry", "invalidate-candidate"},
                "invalid_operation", "use the original bounded control recovery/closure operation")
        require(isinstance(data["receipt"], dict) and data["receipt"], "host_evidence_missing", "original authenticated host/controller evidence required")
        s = self.state
        if (s.get("scope_revision_transaction") or {}).get("state") not in (None, "active"):
            require(data["action"] == "scope-revise", "scope_revision_pending",
                    "scope revision has closed the old control lease")
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
                    'stop_receipt': copy.deepcopy(stop), 'business_calls': calls, 'business_calls_digest': entry.digest(calls)}
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
        transactions.setdefault(identity, {"request": copy.deepcopy(data), "port": copy.deepcopy(s["control"]),
                                            "status": s["status"], "result": None,
                                            "business_block_id": (s.get("business_block") or {}).get("id")})
        require(transactions[identity]['port']['context'] == s['control']['context'],
                'control_context_changed', 'retain the original result without overwriting newer control authority')
        if data['action'] == 'successor-ready':
            transactions[identity]['takeover_input'] = takeover_input
        if data['action'] == 'escalate-implementation':
            transactions[identity]['escalation_input'] = escalation_input
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
        retired = current.get('retired_handoffs', {}).get(handoff_id)
        require(retired is not None and retired.get('handoff_completed'), 'identity_mismatch', 'exact completed handoff required')
        identity = entry.digest(data)
        self.require_controls_reconciled(identity)
        transactions = s.setdefault('control_transactions', {})
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
                routes = []
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
                    latest = dispatch.discussion_protocol.handle({**query, 'operation': 'read-topic'})
                    port['context'] = latest['workflow_control']
                    require(port['context'] is not None, 'authority_missing', 'original archive checkpoint unavailable')
                    port['discussion'].update(expected_ledger_revision=latest['ledger_revision'], expected_topic_revision=latest['record_revision'], idempotency_key=str(uuid.uuid4()))
                else:
                    port, saved = copy.deepcopy(s['control']), copy.deepcopy(s['handoff'])
                authoritative = port['context'].get('retired_handoffs', {}).get(handoff_id)
                require(authoritative is not None and all(authoritative[key] == retired[key] for key in ('controller_ref', 'topic_ref', 'carrier', 'plan', 'delivery', 'delivery_digest', 'successor')), 'identity_mismatch', 'original frozen handoff changed')
                transactions[identity] = {'request': copy.deepcopy(data), 'port': port, 'handoff': saved,
                    'scope': 'archive', 'handoff_id': handoff_id, 'operation_id': data['evidence'].get('operation_id'),
                    'handoff_completed': True, 'intent_persisted': data['action'] == 'archive-result' and
                        any(op['operation_id'] == data['evidence'].get('operation_id') for op in authoritative['operations']), 'result': None}
            self.save()
        transaction = transactions[identity]
        if data['action'] == 'archive-result':
            require(data['receipt'] == data['evidence'].get('receipt'), 'host_evidence_missing', 'retain exact authenticated archive receipt')
        elif 'call_lookup' in data['evidence']:
            require(data['receipt'] == data['evidence']['call_lookup'], 'host_evidence_missing', 'retain authenticated original invocation lookup')
        execution_port = copy.deepcopy(transaction['port'])
        if execution_port['discussion'] is None:
            execution_port['context'] = copy.deepcopy(current)
        result, applied = dispatch.checkpoint(execution_port, transaction['handoff'], data['action'], data['evidence'])
        updated = result['context']['retired_handoffs'][handoff_id]
        if transaction['port']['discussion'] is not None:
            query = {key: value for key, value in transaction['port']['discussion'].items()
                     if key not in {'expected_ledger_revision', 'expected_topic_revision', 'idempotency_key'}}
            latest = dispatch.discussion_protocol.handle({**query, 'operation': 'read-topic'})
            updated = latest['workflow_control']['retired_handoffs'][handoff_id]
        # A retired business port can never replace current execution authority.
        current.setdefault('retired_handoffs', {})[handoff_id] = copy.deepcopy(updated)
        transaction['result'] = {**result, 'context': copy.deepcopy(current)}
        transaction['applied'] = applied
        transaction['intent_persisted'] = True
        if result['effects']:
            transaction['operation_id'] = result['effects'][0]['operation_id']
        self.save()
        return _view(s, {'operation': 'control-effects', 'result': transaction['result']})

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
        if (s.get("scope_revision_transaction") or {}).get("state") not in (None, "active"):
            require(operation in {"bind", "reconcile"}, "scope_revision_pending",
                    "old allocation lease is closed during scope revision")
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
                parent_scope = s["handoff"]["scope"]
                manifest_base = ((s["handoff"].get("readiness") or {}).get("scope_authority") or {}).get("scope", {}).get(
                    "baseline", s["handoff"].get("manifest_baseline", parent_scope["baseline"]))
                require("manifest_baseline" not in request or
                        request["manifest_baseline"] == manifest_base,
                        "scope_changed", "allocation must retain the sealed manifest baseline")
                request["manifest_baseline"] = manifest_base
                if "readiness" in s["handoff"]:
                    require("readiness" not in request or
                            request["readiness"] == s["handoff"]["readiness"],
                            "scope_changed", "allocation readiness differs from its Dispatcher")
                    request["readiness"] = copy.deepcopy(s["handoff"]["readiness"])
                    require("generated_outputs" not in request, "scope_changed",
                            "inherited generated inventory belongs to the sealed authority")
                else:
                    generated = s["handoff"].get("generated_outputs", {})
                    require("generated_outputs" not in request or request["generated_outputs"] == generated,
                            "scope_changed", "allocation generated inventory differs from its Dispatcher")
                    if "generated_outputs" in s["handoff"]:
                        request["generated_outputs"] = copy.deepcopy(generated)
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
        require(stop.get('git_snapshot') == control_git.recovery_snapshot(Path(checkpoint['binding']['worktree']), checkpoint)[0],
                'implementation_escalation_required', 'direct resume snapshot changed; reconcile provenance and escalate before editing')

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
            self.verify_direct_resume()
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
            state["pending"] = None
        outer[KEY]["phase_complete"] = True
        outer[KEY]["revision"] += 1
        atomic_save(path, outer)
        state = outer[KEY]
        if state["stage"] != 2 or scope_readiness(state, path):
            state["pending"] = stage_boundary(state["mode"], state["stage"], state["accepted"], state["next_stage"])
            state["revision"] += 1
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
    if request['operation'] == 'host-preflight':
        # Read-only capability check, usable before creating a flow or checkpoint.
        return {'protocol': PROTOCOL, 'status': 'host-ready',
                'native_host': native_host_preflight(request.get('data'))}
    path = Path(path)
    require(path.is_absolute(), "invalid_checkpoint", "absolute checkpoint path required")
    if request["operation"] == "scope-recover-legacy" and (request.get("data") or {}).get("action") == "inspect":
        details = inspect_legacy_checkpoint(path, request["data"])
        return {"protocol": PROTOCOL, "status": "inspected", "state": "inspected",
                "recovery_id": details["recovery_id"],
                "original_checkpoint_sha256": details["original_checkpoint_sha256"],
                "original_accepted_digest": details["original_accepted_digest"],
                "binding": copy.deepcopy(details["binding"]), "downstream_ready": False}
    if request["operation"] == "scope-recover-legacy" and (request.get("data") or {}).get("action") == "prepare-takeover":
        return prepare_legacy_takeover(path, request["data"])
    if request["operation"] == "scope-recover-legacy" and (request.get("data") or {}).get("action") == "supersede":
        return supersede_legacy_takeover(path, request["data"])
    if request["operation"] == "scope-recover-legacy" and (request.get("data") or {}).get("action") == "activate":
        return activate_legacy_takeover(path, request["data"])
    with record_lock(path):
        outer = read_record(path)
        require(outer.get("version") not in {1, 2, 3, 4, 5, 6}, "legacy_run_requires_original_runtime", "retain the original runner and record")
        member = outer.get(KEY)
        pinned_protocol = outer.get("confirmed", {}).get("packages", {}).get("runner", {}).get("compatibility_key", {}).get("workflow_progress")
        require((member is None or isinstance(member, dict) and member.get("protocol") == PROTOCOL) and
                (pinned_protocol is None or pinned_protocol == PROTOCOL),
                "legacy_run_requires_original_runtime", "all operations require the original pinned progression runtime")
        if member is not None:
            require(member.get("control",{}).get("context",{}).get("schema_version") == 5,
                    "legacy_run_requires_original_runtime", "retain the original embedded control runtime")
            if member.get("recovery_mode") == "legacy-takeover" or outer.get("recovery_source") is not None:
                recovery = verify_legacy_recovery(outer, path)
                require(recovery["state"] != "takeover-pending" or request["operation"] == "inspect",
                        "legacy_takeover_pending", "activate or reconcile the original takeover before new work")
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
        if (operation == "start" and owner.state is not None and
                owner.state.get("recovery_start_input") is not None and
                owner.state["recovery_start_input"] == data and
                any(value.get("state") == "consumed" for value in owner.state.get("scope_recoveries", {}).values())):
            return _view(owner.state, acknowledged=True)
        duplicate = owner.state is not None and ((operation == "observe" and
            owner.state["events"].get(data.get("event_id"), {}).get("applied")) or
            (operation == "scope-propose" and data.get("proposal_id") in owner.state.get("scope_proposals", {})) or
            (operation == "scope-seal" and data.get("proposal_id") in owner.state.get("scope_seals", {})) or
            (operation == "scope-revise" and (data.get("revision_id") in owner.state.get("scope_revisions", {}) or
             data.get("revision_id") == (owner.state.get("scope_revision_transaction") or {}).get("input", {}).get("revision_id"))) or
            (operation == "activate-scope-revision" and data.get("revision_id") ==
             (owner.state.get("scope_revision_transaction") or {}).get("input", {}).get("revision_id") and
             (owner.state.get("scope_revision_transaction") or {}).get("activation", {}).get("input") == data) or
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
        elif operation == 'lifecycle-state' and owner.state.get('native_host', {}).get('adapter') != 'collaboration':
            validate_snapshot_events(data)
        elif operation == 'observe' and isinstance(data,dict) and 'lifecycle' in data:
            require(owner.state.get('native_host', {}).get('adapter') != 'collaboration',
                    'invalid_result', 'record collaboration inventory through lifecycle-query and lifecycle-state')
            validate_snapshot_events(data['lifecycle'])
        try:
            if operation == "advance": return owner.advance()
            if operation == "scope-propose": return owner.scope_propose(data)
            if operation == "scope-seal": return owner.scope_seal(data)
            if operation == "scope-revise": return owner.scope_revise(data)
            if operation == "activate-scope-revision": return owner.activate_scope_revision(data)
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
            if operation == 'host-event': return owner.host_event(data)
            if operation == 'transport-lost': return owner.transport_lost(data)
            if operation == 'carrier-failure': return owner.carrier_failure(data)
            if operation == "pause": return owner.suspend()
            if operation == "cancel": return owner.suspend(cancel=True)
            if operation == "resume": return owner.resume()
            raise entry.PreparationError("invalid_operation", "unknown progression operation")
        except entry.ERROR_TYPES + (control.ControlError,) as error:
            if (operation in {"scope-propose", "scope-seal", "scope-revise", "activate-scope-revision"} or
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
