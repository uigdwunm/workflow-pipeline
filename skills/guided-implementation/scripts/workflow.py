#!/usr/bin/env python3
"""Foreground, checkpointed execution of the approved Stage 2 -> 3 -> 4 flow.

The runner deliberately has no scheduler or background recovery.  A run record is
the only durable state and it must be stored outside the Flow Worktree, because
Stage 4 is allowed to remove that worktree. One foreground app-server retains
carrier turns under the Controller's frozen effective permissions.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import time
import uuid
from contextlib import contextmanager
from typing import Any

from workflow_control import ControlError, validate_review, select_configuration, paths
from skill_preflight import PreflightError, package_identity, preflight, verify_identity
import workflow_progress as progression
import stage_handoff
import entry_prepare
import requirement_prepare
import foreground_host


STAGES = ("stage2", "stage3", "stage4")
SCHEMA_PATH = Path(__file__).with_name("workflow_stage_result.schema.json").resolve()
_MISSING = object()
_STOP_STATUS_RANK = {'active': 0, 'needs_input': 0, 'pausing': 1, 'paused': 2,
                     'cancelling': 3, 'cancelled': 4}


class WorkflowError(RuntimeError):
    """A user-correctable input or runtime problem."""


class RunBusy(WorkflowError):
    """The foreground owner still owns this record's process lock."""


class CheckpointPendingError(WorkflowError):
    """A durable carrier receipt has not reached the main checkpoint."""


class LaunchDeferred(WorkflowError):
    """The saved checkpoint needs intake before a normal carrier turn."""


class RunLock:
    def __init__(self, record_path: Path) -> None:
        self.path = record_path.with_name(record_path.name + ".runner.lock")
        self.stream: Any | None = None

    def __enter__(self) -> "RunLock":
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        current = os.stat(parent, follow_symlinks=False)
        if (not stat.S_ISDIR(current.st_mode) or current.st_uid != os.geteuid()
                or stat.S_IMODE(current.st_mode) & 0o077):
            raise WorkflowError("private run record directory required: use an owner-only directory")
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise WorkflowError("run lock cannot be opened safely") from error
        identity = os.fstat(descriptor)
        if not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.geteuid() or identity.st_nlink != 1:
            os.close(descriptor)
            raise WorkflowError("run lock must be an owner-owned regular file")
        self.stream = os.fdopen(descriptor, "r+", encoding="utf-8")
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.stream.close()
            raise RunBusy("run_busy: another start or resume owns this run") from exc
        return self

    def __exit__(self, *unused: object) -> None:
        assert self.stream is not None
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def _absolute_path(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"{name} must be a non-empty absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise WorkflowError(f"{name} must be an absolute path")
    return path.resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise WorkflowError(f"{name} must be a non-empty-string list")
    return list(value)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"invalid {label}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise WorkflowError(f"invalid {label}: expected one JSON object")
    return parsed


def validate_confirmed(raw: dict[str, Any], *, restoring: bool = False) -> dict[str, Any]:
    required = {
        "frozen_requirement", "repository", "worktree", "git_common_dir",
        "target_branch", "authority_scope", "run_record", "stages", "controller_ref", "requirement",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise WorkflowError("confirmed input missing: " + ", ".join(missing))
    if not isinstance(raw["controller_ref"], str) or not raw["controller_ref"].strip():
        raise WorkflowError("controller_ref must be authenticated and nonempty")
    requirement = raw["frozen_requirement"]
    if not isinstance(requirement, dict):
        raise WorkflowError("frozen_requirement must be an object")
    requirement_path = _absolute_path(requirement.get("path"), "frozen_requirement.path")
    commit = requirement.get("commit")
    digest = requirement.get("sha256")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise WorkflowError("frozen_requirement.commit must be a lowercase Git SHA")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise WorkflowError("frozen_requirement.sha256 must be a lowercase SHA-256")
    repository = _absolute_path(raw["repository"], "repository")
    worktree = _absolute_path(raw["worktree"], "worktree")
    git_common_dir = _absolute_path(raw["git_common_dir"], "git_common_dir")
    source = raw["requirement"]
    try:
        if isinstance(source, dict) and source.get("source_kind") == "discussion":
            entry_prepare.fields(source, {"protocol", "source_kind", "checkpoint", "attachment", "absolute_path",
                                          "requirement_identity", "commit", "blob", "discussion_project"})
            entry_prepare.require(isinstance(source["checkpoint"], dict) and source["checkpoint"].get("state") == "completed" and
                source["checkpoint"].get("storage_kind") == "git" and source["checkpoint"].get("published_identity") == commit,
                "requirement_incomplete", "completed original Git checkpoint required")
            # The execution checkout can already be removed when restoring.
            # Verify the original A receipt at its surviving discussion owner,
            # and independently bind that store to the publication repository.
            owner_entry = {"repository": entry_prepare.repository_facts(str(repository)),
                "discussion_project": source["discussion_project"],
                "requirement": {"kind": "discussion", "attachment": source["attachment"]}}
            stage_handoff.verify_discussion_binding(owner_entry, {
                "repository": str(repository), "git_common_dir": str(git_common_dir), "worktree": str(worktree)})
            actual = requirement_prepare.attached_result(owner_entry, source["checkpoint"]["checkpoint_id"])
            entry_prepare.require(actual == source, "requirement_incomplete", "original attached A receipt changed")
        else:
            requirement_prepare.validate_seal(source)
            entry_prepare.require({"entry", "path", "sha256", "version", "source_kind", "blob"} <= set(source),
                                  "requirement_incomplete", "complete A source fields required")
            original_entry = source["entry"]
            entry_prepare.require(isinstance(original_entry, dict) and original_entry.get("protocol") == entry_prepare.PROTOCOL and
                original_entry.get("evidence_digest") == entry_prepare.digest({k: v for k, v in original_entry.items() if k != "evidence_digest"}),
                "requirement_incomplete", "retain the original complete A entry evidence")
        identity = source.get("requirement_identity")
        entry_prepare.fields(identity, {"path", "sha256", "version"})
        paths([identity["path"]])
        if source.get("kind") == "frozen":
            entry_prepare.require(all(source[key] == identity[key] for key in ("path", "sha256", "version")),
                                  "requirement_incomplete", "frozen source fields must retain the original requirement identity")
        entry_prepare.require(source.get("protocol") == requirement_prepare.PROTOCOL and
            (source.get("kind") == "frozen" or source.get("source_kind") == "discussion") and
            source.get("commit") == commit and identity["sha256"] == digest and
            source.get("absolute_path") == str(requirement_path) and
            _is_git_sha(source.get("blob")) and
            type(identity["version"]) is int and identity["version"] > 0,
            "requirement_incomplete", "v3 requires the complete successful A frozen result matching the confirmed source")
    except (*entry_prepare.ERROR_TYPES, ControlError) as error:
        raise WorkflowError("invalid complete A requirement evidence") from error
    record_path = _absolute_path(raw["run_record"], "run_record")
    if _is_within(record_path, worktree) or _is_within(record_path, repository):
        raise WorkflowError("run_record must live outside worktree and repository")
    if not isinstance(raw["target_branch"], str) or not raw["target_branch"]:
        raise WorkflowError("target_branch must be a non-empty string")
    if raw.get("flow_mode", "continuous") not in {"continuous", "stepwise"}:
        raise WorkflowError("flow_mode must be continuous or stepwise")
    authority = raw["authority_scope"]
    if not isinstance(authority, dict):
        raise WorkflowError("authority_scope must be an object")
    _string_list(authority.get("allowed_paths"), "authority_scope.allowed_paths")
    stages = raw["stages"]
    if not isinstance(stages, dict) or set(stages) != set(STAGES):
        raise WorkflowError("stages must contain exactly stage2, stage3, and stage4")
    normalized_stages: dict[str, dict[str, str]] = {}
    for stage in STAGES:
        settings = stages[stage]
        if not isinstance(settings, dict):
            raise WorkflowError(f"stages.{stage} must be an object")
        model, effort = settings.get("model"), settings.get("reasoning_effort")
        if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
            raise WorkflowError(f"stages.{stage} requires model and reasoning_effort")
        try:
            selection = select_configuration(settings.get("selection_input"))
        except ControlError as error:
            raise WorkflowError(f"{stage} configuration: {error}") from error
        if selection["needs_decision"] or (selection["model"], selection["effort"]) != (model, effort):
            raise WorkflowError(f"{stage} configuration requires a new controller decision")
        normalized_stages[stage] = {"model": model, "reasoning_effort": effort,
                                    "selection_input": settings["selection_input"], "selection": selection}
    try:
        runner = package_identity(str(Path(__file__).resolve().parents[1] / 'SKILL.md'), 'guided-implementation')
        if restoring:
            packages = raw.get('packages')
            if not isinstance(packages, dict) or set(packages) != {'runner', *STAGES}:
                raise WorkflowError('invalid fixed package identities')
            for identity in packages.values():
                verify_identity(identity)
            for stage, name in zip(STAGES, ('solution-design', 'guided-implementation', 'change-closure')):
                if packages[stage]['name'] != name or packages[stage]['compatibility_key'] != runner['compatibility_key']:
                    raise WorkflowError('incompatible_package: fixed stage identity has wrong role or protocol key')
            if packages['runner'] != runner or packages['stage3'] != runner:
                raise WorkflowError('package_changed: resume with the original runner package')
        else:
            resolved = preflight({'stage':3, 'action':'entry', 'target_stages':[2,4], 'registry':raw.get('registry')})
            packages = {'runner': runner, **{stage:resolved['packages'][name] for stage,name in zip(STAGES,
                ('solution-design','guided-implementation','change-closure'))}}
            if packages['stage3'] != runner:
                raise WorkflowError('package_changed: invoke the registered Stage-3 runner')
    except PreflightError as exc:
        raise WorkflowError(f'{exc.code}: {exc}') from exc
    try:
        host = foreground_host.validate_configuration(raw.get("host"), raw["controller_ref"], str(repository), str(worktree), str(git_common_dir))
    except foreground_host.HostError as error:
        raise WorkflowError(str(error)) from error
    try:
        registration_request = {"registry_input": str(_absolute_path(raw.get('registry_input'), 'registry_input')),
            "host": {"project_id": raw['registration_context' if restoring else 'registry_context']['project_id'], "controller_ref": raw['controller_ref']},
            "target": {"binding": {"repository": str(repository), "git_common_dir": str(git_common_dir)}}}
        _, registration = entry_prepare.registration_inputs(registration_request, entry_prepare.repository_facts(str(repository)))
        if restoring:
            entry_prepare.verify_registration(registration, raw['registration_context'])
        else:
            entry_prepare.require(registration['project_path'] == str(repository), 'registration_identity_changed', 'original host project required')
    except entry_prepare.ERROR_TYPES as error:
        raise WorkflowError('invalid registration context: ' + str(error)) from error
    return {
        "registration_context": registration,
        "host": host,
        "controller_ref": raw["controller_ref"],
        "frozen_requirement": {"path": str(requirement_path), "commit": commit, "sha256": digest},
        "requirement": copy.deepcopy(source),
        "repository": str(repository), "worktree": str(worktree),
        "git_common_dir": str(git_common_dir), "target_branch": raw["target_branch"],
        "authority_scope": authority, "run_record": str(record_path), "stages": normalized_stages,
        "packages": packages,
        "flow_mode": raw.get("flow_mode", "continuous"),
        "registry_input": str(_absolute_path(raw.get('registry_input'), 'registry_input')),
    }


def _check_current_registry(confirmed: dict[str, Any], stages: list[int]) -> None:
    try:
        registration, current = entry_prepare.registration_inputs({
            "registry_input": confirmed['registry_input'], "host": confirmed['registration_context'],
            "target": {"binding": {"repository": confirmed['repository'], "git_common_dir": confirmed['git_common_dir']}}},
            entry_prepare.repository_facts(confirmed['repository']))
        entry_prepare.verify_registration(current, confirmed['registration_context'])
        resolved = preflight({'stage': 3, 'action': 'entry', 'target_stages': stages,
                              **registration})
        for identity in resolved['packages'].values():
            if identity['compatibility_key'] != confirmed['packages']['runner']['compatibility_key']:
                raise WorkflowError('incompatible_package: current registry protocols changed')
    except entry_prepare.ERROR_TYPES as exc:
        raise WorkflowError(f'{getattr(exc, "code", "registry_unavailable")}: {exc}; retained checkpoint, no executor launched') from exc


class CheckpointState(dict):
    """Runner's working view, with the last checkpoint it actually observed."""

    def __init__(self, value: dict[str, Any], *, original: dict[str, Any] | None = None) -> None:
        super().__init__(value)
        self.original = copy.deepcopy(value if original is None else original)

    def refresh(self, value: dict[str, Any]) -> None:
        if self.original and not value:
            raise WorkflowError('checkpoint_missing: retain the original run record')
        if self == value and self.original == value:
            return
        self.clear()
        self.update(value)
        self.original = copy.deepcopy(value)


def _apply_runner_change(latest: Any, before: Any, after: Any, path: tuple[str, ...]) -> Any:
    if isinstance(after, dict) and (isinstance(before, dict) or before is _MISSING):
        if latest is _MISSING:
            if before is not _MISSING:
                raise WorkflowError('checkpoint_conflict: ' + '.'.join(path) + ' changed since the runner observed it')
            latest = {}
        if not isinstance(latest, dict):
            raise WorkflowError('checkpoint_conflict: ' + '.'.join(path) + ' changed since the runner observed it')
        for key in sorted((before.keys() if isinstance(before, dict) else set()) | after.keys()):
            old_value = before.get(key, _MISSING) if isinstance(before, dict) else _MISSING
            new_value = after.get(key, _MISSING)
            if old_value == new_value:
                continue
            merged = _apply_runner_change(latest.get(key, _MISSING), old_value, new_value, (*path, key))
            if merged is _MISSING:
                latest.pop(key, None)
            else:
                latest[key] = merged
        return latest
    if latest != before and latest != after:
        raise WorkflowError('checkpoint_conflict: ' + '.'.join(path) + ' changed since the runner observed it')
    return _MISSING if after is _MISSING else copy.deepcopy(after)


def _decision_matches_pending(decision: Any, pending: Any) -> bool:
    return (isinstance(decision, dict) and isinstance(pending, dict) and
            isinstance(pending.get('decision_id'), str) and bool(pending['decision_id']) and
            decision.get('decision_id') == pending['decision_id'] and
            decision.get('subject') == pending.get('subject'))


def _runner_route(saved: dict[str, Any], state: dict[str, Any]) -> str:
    """Choose the saved work that must precede another ordinary carrier turn."""
    if saved.get('runner_request') is not None:
        return 'stop'
    current = saved.get(progression.KEY)
    if not current or current.get('stage') != int(state['current_stage'][-1]):
        return 'invoke'
    status = current.get('status')
    cancelling = current.get('stop_requested') == 'cancelling'
    resuming_pause = state.get('resume_progression') and status == 'paused' and not cancelling
    if not resuming_pause and (status in {'paused', 'pausing', 'cancelling'} or
                              status == 'cancelled' and cancelling):
        return 'stop'
    if current.get('business_block') and not (state.get('resume_progression') and
                                              current.get('deferred_business_recovery')):
        return 'business_block'
    if status == 'blocked' and not state.get('resume_progression'):
        return 'progression_blocked'
    if (status == 'needs_input' and state.get('turn_result') is None and
            not any(_decision_matches_pending(decision, current.get('pending'))
                    for decision in (state.get('controller_decision'), saved.get('controller_decision')))):
        return 'needs_input'
    if status == 'accepted' and current.get('phase_complete') is True:
        return 'result_ready'
    return 'invoke'


def _atomic_save(path: Path, state: dict[str, Any], *, require_launch_admission: bool = False) -> None:
    # The process-ownership lock stays held while the runner submits its changes.
    # Apply only differences from the runner's last observed checkpoint so new
    # fields written by C or the transport journal need no merge-list entry.
    if not isinstance(state, CheckpointState):
        raise WorkflowError('checkpoint_state_required: runner needs its observed checkpoint before saving')
    with progression.record_lock(path):
        current = progression.read_record(path)
        original = state.original
        if original and not current:
            raise WorkflowError('checkpoint_missing: retain the original run record')
        if require_launch_admission and _runner_route(current, state) != 'invoke':
            raise LaunchDeferred('launch_deferred: handle the saved checkpoint before another carrier turn')
        for key in sorted(original.keys() | state.keys()):
            before = original.get(key, _MISSING)
            after = state.get(key, _MISSING)
            if before == after:
                continue
            latest = current.get(key, _MISSING)
            if key == 'pending_input' and current.get('transport', {}).get('server_requests'):
                continue  # The original host request has priority until answered.
            if (key == 'pending_input' and before is not _MISSING and
                    isinstance(after, dict) and after.get('kind') == 'host-request' and latest is _MISSING):
                continue  # A stale host request cannot be restored after its reply.
            # A queued stop outranks a host prompt or an earlier pause. Recompute
            # its phase from the latest C/runner request before applying it.
            if (key == 'status' and latest in {'active', 'needs_input', 'pausing', 'paused', 'cancelling'} and
                    latest != before and latest != after and
                    after in {'pausing', 'paused', 'cancelling', 'cancelled'} and
                    _STOP_STATUS_RANK[after] >= _STOP_STATUS_RANK[latest]):
                progress_state = current.get(progression.KEY)
                request = current.get('runner_request')
                if ((request or {}).get('operation') in {'pause', 'cancel'} or
                        (progress_state or {}).get('stop_requested') in {'pausing', 'cancelling'}):
                    if _stop_status(state, progress_state, request) == after:
                        current['status'] = after
                        continue
            merged = _apply_runner_change(latest, before, after, (key,))
            if merged is _MISSING:
                current.pop(key, None)
            else:
                current[key] = merged
        decision = current.get("controller_decision")
        progress_state = current.get(progression.KEY)
        if decision and progress_state and any(item.get("decisions", {}).get(decision["decision_id"]) == decision
                                               for item in [progress_state, *progress_state["history"]]):
            current.pop("controller_decision")
        progression.atomic_save(path, current)
    state.refresh(current)


def _live_commands(state, record_path):
    """Owner-only delivery preparation; C consumes decisions in its carrier."""
    with progression.record_lock(record_path):
        saved = progression.read_record(record_path)
        current = saved.get(progression.KEY) or {}
        request = saved.get('runner_request')
        if (request or {}).get('operation') == 'cancel' or current.get('stop_requested') == 'cancelling':
            work = (request is not None and current.get('stop_requested') != 'cancelling' and
                    request['request_id'] not in saved.get('delivered_control_requests', []))
            if work:
                saved.setdefault('delivered_control_requests', []).append(request['request_id'])
                saved['stop_delivery_pending'] = True
                progression.atomic_save(record_path, saved)
        else:
            work = ((request or {}).get('operation') == 'pause' and current.get('stop_requested') != 'pausing' and
                    request['request_id'] not in saved.get('delivered_control_requests', []))
            if work:
                saved.setdefault('delivered_control_requests', []).append(request['request_id'])
                saved['stop_delivery_pending'] = True
            if (saved['status'] == 'failed' and current.get('status') == 'active' and
                    not current.get('business_block') and current.get('continuation_intent') and
                    current.get('recovery_decisions')):
                saved.update(status='active', resume_progression=True)
                work = True
            resume_intent = saved.get('queued_resume')
            if resume_intent is not None and current.get('status') == 'paused':
                subject = {'stage':current.get('stage'), 'attempt':(current.get('dispatch') or {}).get('request', {}).get('attempt'),
                           'handoff':(current.get('handoff') or {}).get('digest')}
                if resume_intent != {'stop_request':request, 'subject':subject}:
                    raise WorkflowError('stale_decision: queued resume no longer names the original pause')
                saved.pop('runner_request', None)
                saved.pop('queued_resume')
                saved.update(resume_progression=True, status='active')
                work = True
            decision = saved.get('controller_decision')
            if decision is not None and decision['subject'].get('host_instance') is None:
                identity = entry_prepare.digest(decision)
                if identity not in saved.get('delivered_commands', []):
                    saved.setdefault('delivered_commands', []).append(identity)
                    saved['answer_pending_delivery'] = decision['answer']
                    saved['control_delivery_pending'] = True
                    saved['status'] = 'active'
                    work = True
            if work:
                progression.atomic_save(record_path, saved)
    state.refresh(saved)
    return work


def _stop_command(saved):
    request = saved.get('runner_request')
    current = saved.get(progression.KEY) or {}
    intent = current.get('stop_requested')
    if intent in {'pausing','cancelling'}:
        return request or {'operation':intent,'stage':saved['current_stage']}
    return request


def _live_progress(state, record_path, host, steered):
    saved = progression.read_record(record_path)
    current = saved.get(progression.KEY) or {}
    transport = saved.get('transport') or {}
    snapshot_key = [saved.get('current_stage'), current.get('host', {}).get('generation'), transport.get('native_event_sequence', 0)]
    if (current.get('stage') == int(saved['current_stage'][-1]) and current.get('host', {}).get('status') == 'stopped' and
            transport.get('snapshot_key') != snapshot_key and not transport.get('lookup_blocked')):
        try:
            snapshot = host.snapshot_lifecycle(saved['current_stage'])
            current = progression.read_record(record_path)[progression.KEY]
            progression.handle(record_path, {'protocol':progression.PROTOCOL,'operation':'lifecycle-state',
                'expected_revision':current['revision'],'data':snapshot})
            with progression.record_lock(record_path):
                saved = progression.read_record(record_path)
                saved['transport']['snapshot_key'] = snapshot_key
                progression.atomic_save(record_path, saved)
        except foreground_host.HostError as error:
            with progression.record_lock(record_path):
                saved = progression.read_record(record_path)
                saved['transport']['lookup_blocked'] = str(error)
                progression.atomic_save(record_path, saved)
            progression.handle(record_path, {'protocol':progression.PROTOCOL,'operation':'advance',
                'expected_revision':saved[progression.KEY]['revision'],'data':{}})
    saved = progression.read_record(record_path)
    command = _stop_command(saved)
    decision = saved.get('transport', {}).get('server_decision') or saved.get('controller_decision')
    if command is None and decision is not None and decision['subject'].get('host_instance') is not None:
        subject = decision['subject']
        request = host.server_requests.get(subject.get('request_id'))
        if (subject.get('host_instance') != host.instance or request is None or
                subject.get('request_digest') != entry_prepare.digest(request)):
            raise WorkflowError('stale_decision: server request differs from the original host')
        try:
            response = json.loads(decision['answer'])
        except json.JSONDecodeError as error:
            raise WorkflowError('invalid_host_answer: provide the exact RPC result JSON') from error
        @contextmanager
        def send_guard():
            # Serialize the stop check with the first positive nonblocking
            # write only. Suffix writes, journaling and readiness waits are outside.
            with progression.record_lock(record_path):
                latest = progression.read_record(record_path)
                yield _stop_command(latest) is None

        if host.answer_request(request, response, send_guard=send_guard):
            with progression.record_lock(record_path):
                current = progression.read_record(record_path)
                if current.get('transport', {}).get('server_decision') == decision:
                    current['transport'].pop('server_decision')
                    progression.atomic_save(record_path, current)
            return
        saved = progression.read_record(record_path)
        command = _stop_command(saved)
    pending_decision = decision if decision is not None and entry_prepare.digest(decision) not in saved.get('delivered_commands', []) else None
    command = command or pending_decision
    if command is not None and host.active is not None:
        identity = entry_prepare.digest(command)
        if identity not in steered:
            steered.add(identity)
            host.steer_control(record_path)


def _host_pending(instance, request):
    return progression.pending_decision('host-request',
        {'host_instance':instance,'request_id':request['id'],'request_digest':entry_prepare.digest(request)},
        'Controller response required for ' + request['method'] + '; supply exact RPC result JSON')


def _new_state(confirmed: dict[str, Any]) -> dict[str, Any]:
    return CheckpointState({
        "version": 6,
        "confirmed": confirmed,
        "status": "active",
        "current_stage": "stage2",
        "sessions": {},
        "stage_results": {},
        "launch": {"stage": "stage2", "state": "prelaunch", "turn": 0},
        "history": [],
    }, original={})


def _validate_record(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("version") in {1, 2, 3, 4, 5}:
        raise WorkflowError('legacy_run_requires_original_runtime: record version ' + str(state.get('version')) + '; retain original package digests ' + str({k:v.get('bundle_digest') for k,v in state.get('confirmed',{}).get('packages',{}).items()}))
    if state.get("version") != 6:
        raise WorkflowError("unsupported run record version")
    confirmed = validate_confirmed(state.get("confirmed") if isinstance(state.get("confirmed"), dict) else {}, restoring=True)
    if state.get("status") not in {"active", "needs_input", "completed", "failed", "interrupted", "paused", "pausing", "cancelling", "cancelled"}:
        raise WorkflowError("invalid run record status")
    if state.get("current_stage") not in STAGES:
        raise WorkflowError("invalid run record current_stage")
    if not isinstance(state.get("sessions"), dict) or not isinstance(state.get("stage_results"), dict):
        raise WorkflowError("invalid run record state")
    for stage in STAGES:
        result = state["stage_results"].get(stage)
        if result is not None:
            if not isinstance(result, dict) or result.get("result") != "completed":
                raise WorkflowError(f"invalid {stage} result in run record")
            _require_handoff(stage, result)
            _validate_handoff_continuity(
                stage, result, confirmed, state["stage_results"].get("stage2")
            )
    if "stage4" in state["stage_results"]:
        _validate_closure_scope(state["stage_results"]["stage4"], state["stage_results"].get("stage3"))
    state["confirmed"] = confirmed
    return state


def _stage_prompt(state: dict[str, Any], stage: str, answer: str | None, continuing: bool) -> str:
    confirmed = state["confirmed"]
    prior = {
        name: state["stage_results"][name]
        for name in STAGES
        if name in state["stage_results"]
    }
    settings = confirmed["stages"][stage]
    role = {
        "stage2": (
            "You are the explicit foreground Stage-2 carrier, not a native child. Follow the exact "
            "solution-design Skill path below. Use the native collaboration child only where that Skill "
            "requires its solution_designer role; do not claim that the foreground carrier itself is that native role. "
            "The requested worktree is not yet a binding: create it once with the existing start-worktree "
            "protocol and return the actual binding in your completed handoff."
        ),
        "stage3": (
            "You are the Stage-3 Originating Task / stage carrier, not the implementation executor. Follow the "
            "exact guided-implementation Skill and dispatch exactly one native Implementation Dispatcher, with no visible implementation task, plus "
            "independent Standards and Spec review roles."
        ),
        "stage4": "You are the Stage-4 carrier delegated by the Workflow Controller. Dispatch exactly one native Closure Agent and verify its publication and cleanup. Return implementation defects to the controller for Stage 3.",
    }[stage]
    payload = {
        "stage": stage,
        "controller_ref": confirmed["controller_ref"],
        "control_checkpoint": prior.get("stage3", prior.get("stage2", {})).get("handoff", {}).get("control_checkpoint"),
        "frozen_requirement": confirmed["frozen_requirement"],
        "requirement": {"checkpoint": confirmed["run_record"], "field": "confirmed.requirement"},
        "repository": confirmed["repository"],
        "worktree": confirmed["worktree"],
        "git_common_dir": confirmed["git_common_dir"],
        "target_branch": confirmed["target_branch"],
        "authority_scope": confirmed["authority_scope"],
        "settings": settings,
        "flow_mode": confirmed["flow_mode"],
        "progression_checkpoint": confirmed["run_record"],
        "progression_protocol": progression.PROTOCOL,
        "progression_response": ({"checkpoint": confirmed["run_record"], "field": "progression_response"}
                                 if state.get("progression_response") is not None else None),
        "controller_decision": ({"checkpoint": confirmed["run_record"], "field": "controller_decision"}
                                if state.get("controller_decision") is not None else None),
        "resume_progression": state.get("resume_progression", False),
        "prior_stage_results": {name: {"artifacts": value["artifacts"], "checkpoint": confirmed["run_record"],
                                       "field": "stage_results." + name} for name, value in prior.items()},
        "user_answer": answer,
        "continuing_same_session": continuing,
        "skill_paths": {name: confirmed['packages'][name]['entry'] for name in STAGES},
        "registry_input": confirmed['registry_input'],
        "registration_context": confirmed['registration_context'],
        "package_identities": confirmed['packages'],
    }
    return (
        f"Stage {stage[-1]} foreground workflow execution. {role}\n"
        "The root conversation owns user decisions. Stay within the supplied frozen requirement and authority scope. "
        "Use the saved flow_mode and exact pending decisions; continuous mode skips only existing human stage gates. "
        "Do not start a daemon, scheduler, monitor, project, or discussion ledger. A carrier turn result never releases its foreground host.\n"
        "At the end, write exactly one JSON result matching the supplied output schema. Every schema field is required. "
        "Reviewable Stage-3 checkpoints return continue; completed requires converged review plus strict final verification and uses handoff_json for a JSON-encoded object (Stage 2 and 3 "
        "must carry their full handoff there); use empty question/message strings when inapplicable. needs_input uses "
        "empty artifacts/evidence, handoff_json '{}', and its exact question. needs_input_kind must be user_decision "
        "only for a genuine user decision; report technical failure with needs_input_kind technical_error and the exact "
        "issue, which stops the runner without asking for authorization. completed and continue use needs_input_kind none. "
        "continue returns to C next_action in this same host and thread: running permits wait only; current original-identity proof and reconciled calls are required for business followup.\n"
        "Stage 3 uses full Execution Agent implementation in this foreground carrier. The runner has no frozen "
        "Controller direct decision; follow the original full allocation and independent review contracts. "
        "Never change the original baseline or replay uncertain native calls.\n"
        "B owns the complete handoff projection, exact native identity, scope, candidate, reviews and Git evidence. "
        "Partial cleanup is not completed; consume the retained cleanup-only action.\n"
        "For publication, record the stopped native publication_candidate and original controller readiness through C; "
        "consume publication, reconcile-publication/resume, then receive-publication. Preserve native and Git evidence "
        "as separate sources; do not fabricate a new host completion. Final B acceptance and phase completion still apply.\n"
        "Pass registry_input unchanged to every carrier and native role entry. Scripts reread registry and registry_context; never copy entries or query the Flow cwd. Verify registration_context against the original host and actual execution cwd against the Flow binding independently. "
        "Use scripts/workflow_progress.py from the pinned stage package for A/B progression. "
        "Persist complete handoff, dispatch intent, raw host responses and controller acceptance in progression_checkpoint. "
        "The runner owns outer carrier fields: never overwrite the checkpoint yourself. Only C's adapter writes its member. "
        "Read prior results and any progression_response at their exact checkpoint fields; full evidence is retained there, "
        "not reconstructed from these compact prompt references. Consume the saved response's next_action before advancing again. "
        "If resume_progression is true, first reconcile C's saved operation with C resume in this carrier context. "
        "C v5 retains separate business recovery and current host evidence. Ordinary resume cannot clear a business block; "
        "only the original Controller's explicit recover-business decision authorizes recovery. "
        "For inspect-host-state, query only the exact "
        "original identity and return a fresh authenticated tool response with that query action_id. Never attach "
        "a new ID to cached evidence. The trusted host adapter supplies provenance={call_ref,response_ref,action_id} "
        "from the actual invocation alongside unchanged raw evidence. The outer action_id must match provenance.action_id "
        "and the outstanding action. C checks consistency but cannot authenticate arbitrary JSON. "
        "If the query includes unresolved_action, also reconcile that exact invocation and return action_resolution "
        "with its action_id and actual terminal outcome completed, not-issued or cancelled. Idle alone cannot settle it. "
        "CLI carrier identity is never native evidence. Observe invoke/continue/stop responses with their actual causal action_id. "
        "Unknown or unsupported lookup waits for original recovery; creation ready and user answers do not prove resumability. "
        "Then apply a saved controller_decision through C decide in this same carrier context; the external controller must not "
        "impersonate this carrier when A/B revalidates its entry. "
        "Return completed only using completion.stage_result returned by C; no hand-written handoff projection. "
        "For needs_input, retain C's exact pending decision in handoff_json as {pending: ...}; "
        "the root controller answers it. Inspect the checkpoint on every resumed turn; never reissue a host call.\n"
        + json.dumps(payload, sort_keys=True)
    )


def _read_stage_result(path: Path, stage: str) -> dict[str, Any]:
    result = _read_json(path, "stage result")
    required = {"result", "artifacts", "evidence", "handoff_json", "question", "message", "needs_input_kind"}
    if set(result) != required or result.get("result") not in {"completed", "continue", "needs_input"}:
        raise WorkflowError("invalid stage result shape")
    if not isinstance(result["handoff_json"], str):
        raise WorkflowError("stage result.handoff_json must be a JSON object string")
    try:
        handoff = json.loads(result["handoff_json"])
    except json.JSONDecodeError as exc:
        raise WorkflowError("stage result.handoff_json is malformed") from exc
    if not isinstance(handoff, dict):
        raise WorkflowError("stage result.handoff_json must encode an object")
    result["handoff"] = handoff
    status = result["result"]
    if result["needs_input_kind"] not in {"none", "user_decision", "technical_error"}:
        raise WorkflowError("invalid needs_input_kind")
    if status == "completed":
        if not _string_list(result.get("artifacts"), "stage result.artifacts") or not _string_list(result.get("evidence"), "stage result.evidence"):
            raise WorkflowError("completed stage result requires artifacts and evidence")
        if result["needs_input_kind"] != "none":
            raise WorkflowError("completed stage result must use needs_input_kind none")
    elif status == "needs_input":
        if not isinstance(result.get("question"), str) or not result["question"]:
            raise WorkflowError("needs_input stage result requires question")
        if result["needs_input_kind"] not in {"user_decision", "technical_error"}:
            raise WorkflowError("needs_input stage result must identify a user decision")
    else:
        if not isinstance(result.get("message"), str) or not result["message"]:
            raise WorkflowError("continue stage result requires message")
        if result["needs_input_kind"] != "none":
            raise WorkflowError("continue stage result must use needs_input_kind none")
    _require_handoff(stage, result) if status == "completed" else None
    return result


def _require_handoff(stage: str, result: dict[str, Any]) -> None:
    handoff = result.get("handoff")
    if not isinstance(handoff, dict):
        raise WorkflowError(f"{stage} completed result requires a structured handoff")
    if stage == "stage4":
        required = {"binding", "candidate_commit", "merge_commit", "cleanup", "ancestor_verified", "changed_paths"}
    elif stage == "stage2":
        required = {"binding", "planning_commit", "allowed_paths", "protected_paths"}
    else:
        required = {"binding", "candidate_commit", "review", "verification", "implementation_paths", "closure_paths", "protected_paths"}
    required |= {"controller_ref", "role_ref", "control_checkpoint"}
    if not required <= set(handoff):
        raise WorkflowError(f"{stage} handoff missing: " + ", ".join(sorted(required - set(handoff))))
    checkpoint = handoff["control_checkpoint"]
    if not isinstance(checkpoint, dict) or checkpoint != {"controller_ref": handoff["controller_ref"], "stage": int(stage[-1]), "role_ref": handoff["role_ref"], "state": "completed", "role_kind": {"stage2": "solution-designer", "stage3": "implementation-dispatcher", "stage4": "closure-agent"}[stage]}:
        raise WorkflowError("handoff control checkpoint does not bind controller and role")
    if not isinstance(handoff["role_ref"], str) or not handoff["role_ref"]:
        raise WorkflowError("handoff native role identity is missing")
    if not isinstance(handoff["binding"], dict) or not handoff["binding"]:
        raise WorkflowError(f"{stage} handoff.binding must be an object")
    try:
        for field in ("implementation_paths", "closure_paths", "protected_paths", "allowed_paths", "changed_paths"):
            if field in handoff:
                paths(handoff[field], empty=field in {"closure_paths", "protected_paths"})
    except ControlError as error:
        raise WorkflowError(str(error)) from error
    binding = handoff["binding"]
    binding_fields = {"base_commit", "branch", "git_common_dir", "repository", "target_branch", "worktree"}
    if not binding_fields <= set(binding) or not all(isinstance(binding[field], str) and binding[field] for field in binding_fields):
        raise WorkflowError(f"{stage} handoff.binding is incomplete")
    if stage == "stage2":
        if not _is_git_sha(handoff["planning_commit"]):
            raise WorkflowError("stage2 handoff.planning_commit must be a string")
        _string_list(handoff["allowed_paths"], "stage2 handoff.allowed_paths")
        if not isinstance(handoff["protected_paths"], list) or not all(isinstance(item, str) for item in handoff["protected_paths"]):
            raise WorkflowError("stage2 handoff.protected_paths must be a string list")
    elif stage == "stage4":
        if not _is_git_sha(handoff["candidate_commit"]) or not _is_git_sha(handoff["merge_commit"]) or handoff["ancestor_verified"] is not True or handoff["cleanup"] != {"worktree_removed": True, "branch_removed": True}:
            raise WorkflowError("stage4 publication or cleanup is incomplete")
    else:
        try:
            validate_review(handoff["candidate_commit"], handoff["review"], handoff["verification"], handoff["role_ref"])
        except ControlError as error:
            raise WorkflowError(str(error)) from error
        if not _is_git_sha(handoff["candidate_commit"]):
            raise WorkflowError("stage3 handoff.candidate_commit must be a string")
        if not isinstance(handoff["review"], dict) or not handoff["review"]:
            raise WorkflowError("stage3 handoff.review must be an object")


def _validate_handoff_continuity(
    stage: str,
    result: dict[str, Any],
    confirmed: dict[str, Any],
    stage2_result: dict[str, Any] | None,
) -> None:
    if result["handoff"]["controller_ref"] != confirmed["controller_ref"]:
        raise WorkflowError("handoff controller differs from confirmed controller")
    binding = result["handoff"]["binding"]
    immutable_fields = ("repository", "worktree", "git_common_dir", "target_branch")
    for field in immutable_fields:
        if binding[field] != confirmed[field]:
            raise WorkflowError(f"{stage} handoff.binding.{field} differs from confirmed input")
    if stage in {"stage3", "stage4"}:
        if not isinstance(stage2_result, dict):
            raise WorkflowError("stage3 handoff requires the prior stage2 handoff")
        prior_binding = stage2_result.get("handoff", {}).get("binding")
        if not isinstance(prior_binding, dict):
            raise WorkflowError("stage3 handoff requires a valid prior stage2 binding")
        binding_fields = ("base_commit", "branch", "git_common_dir", "repository", "target_branch", "worktree")
        if any(binding[field] != prior_binding.get(field) for field in binding_fields):
            raise WorkflowError("stage3 handoff.binding differs from the stage2 binding")


def _validate_closure_scope(result, stage3_result):
    if not isinstance(stage3_result, dict) or not isinstance(stage3_result.get("handoff"), dict):
        raise WorkflowError("closure requires accepted stage3 handoff")
    accepted = stage3_result["handoff"]
    actual = result["handoff"]
    if actual["candidate_commit"] != accepted["candidate_commit"]:
        raise WorkflowError("closure candidate differs from accepted stage3 candidate")
    if not set(actual["changed_paths"]) <= set(accepted["implementation_paths"] + accepted["closure_paths"]):
        raise WorkflowError("closure changed paths escape accepted scope")


def _is_git_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _artifact_path(record_path: Path, stage: str, turn: int) -> Path:
    return record_path.parent / f"{record_path.name}.{stage}.turn-{turn}.json"


def _write_stage_output(path: Path, content: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _carrier_receipt_path(record_path, stage, turn):
    return _artifact_path(record_path, stage, turn).with_suffix(".carrier.json")


def _finish_carrier_turn(state, result):
    """Identical local-turn intake for live completion and receipt recovery."""
    stage, turn = state["current_stage"], state["launch"]["turn"]
    state["launch"]["state"] = "completed_turn"
    state["status"] = "active"
    state["turn_result"] = result
    # These describe transport delivered to the finished invocation. A queued
    # controller_decision is different: _atomic_save removes it only with C's
    # exact consumption proof, otherwise the original user input remains saved.
    for key in ("answer_pending_delivery", "progression_response", "resume_progression", "control_delivery_pending", 'stop_delivery_pending'):
        state.pop(key, None)
    event = {"event": "turn.completed", "stage": stage, "turn": turn}
    if event not in state["history"]:
        state["history"].append(event)


def _recover_carrier_receipt(state, record_path):
    """Recover local transport facts, never native stopped/acceptance authority."""
    launch = state["launch"]
    if launch["state"] not in {"spawning", "launched"} or "request" not in launch:
        return
    path = _carrier_receipt_path(record_path, state["current_stage"], launch["turn"])
    if not path.exists():
        return
    receipt = _read_json(path, "carrier receipt")
    if receipt.get('host_instance') != state.get('transport', {}).get('instance') and state.get('transport') is not None:
        raise WorkflowError('carrier receipt belongs to another foreground host')
    if (receipt.get("run_record") != str(record_path) or receipt.get("stage") != state["current_stage"] or
            receipt.get("turn") != launch["turn"] or not launch.get("invocation_id") or
            receipt.get("invocation_id") != launch["invocation_id"] or receipt.get("request_digest") != entry_prepare.digest(launch["request"])):
        raise WorkflowError("carrier receipt differs from the exact saved launch")
    sessions = {event.get("thread_id") for event in receipt["events"] if event.get("type") == "thread.started"}
    if len(sessions) != 1 or not all(isinstance(value, str) and value for value in sessions):
        raise WorkflowError(f"carrier identity unresolved; reconcile the original launch using {path}")
    session = next(iter(sessions))
    previous = state["sessions"].get(state["current_stage"])
    if previous is not None and previous != session:
        raise WorkflowError("carrier receipt conflicts with the bound session")
    state["sessions"][state["current_stage"]] = session
    if receipt.get("outcome") == "failed":
        error = receipt["error"]
        _mark_failure(state, record_path, error["code"], error["detail"], error["uncertain"], error["recoverable"])
        return
    if receipt.get("outcome") != "completed_turn":
        _atomic_save(record_path, state)
        raise WorkflowError(f"carrier outcome unresolved; do not relaunch, reconcile {path}")
    if not any(event.get("type") == "turn.completed" for event in receipt["events"]) or any(event.get("type") == "turn.failed" for event in receipt["events"]):
        raise WorkflowError("carrier receipt lacks successful local turn completion")
    # _invoke validated this result after the real process returned successfully.
    # Native execution/acceptance still passes the ordinary C/B intake below.
    _finish_carrier_turn(state, receipt["result"])
    _atomic_save(record_path, state)


def _record_transport_loss(record_path, reason):
    with progression.record_lock(record_path):
        saved = progression.read_record(record_path)
        transport = saved.get('transport')
        if transport is None:
            return
        transport['state'] = 'lost'
        transport.setdefault('loss_reason', reason)
        progression.atomic_save(record_path, saved)
    current = saved.get(progression.KEY)
    if current is not None:
        progression.handle(record_path, {'protocol':progression.PROTOCOL,'operation':'transport-lost',
            'expected_revision':current['revision'], 'data':{'instance':transport['instance'],'reason':transport['loss_reason']}})


def _record_carrier_failure(state, record_path, code, evidence):
    saved = progression.read_record(record_path)
    current = saved.get(progression.KEY)
    if current is not None:
        progression.handle(record_path, {'protocol':progression.PROTOCOL,'operation':'carrier-failure',
            'expected_revision':current['revision'], 'data':{'instance':saved['transport']['instance'],
                'invocation_id':state['launch']['invocation_id'],'code':code,'evidence':evidence}})


def _recover_transport(state, record_path):
    """Read durable bytes from the old connection. Never open another host."""
    path = record_path.with_name(record_path.name + '.host.events.jsonl')
    if not path.exists():
        return
    transport = state.setdefault('transport', {})
    changed = False
    launch = state.get('launch', {})
    request, response, finals, terminals = None, None, {}, set()
    with path.open('rb') as stream:
        while True:
            line = stream.readline(foreground_host.MAX_MESSAGE + 1)
            if not line:
                break
            if len(line) > foreground_host.MAX_MESSAGE or not line.endswith(b'\n'):
                raise WorkflowError('transport_uncertain: incomplete original receipt; no request may be replayed')
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError) as error:
                raise WorkflowError('transport_uncertain: unreadable original receipt') from error
            if not isinstance(event, dict) or not isinstance(event.get('instance'), str):
                raise WorkflowError('transport_uncertain: invalid original receipt')
            if transport.get('instance') not in {None,event['instance']}:
                raise WorkflowError('transport_uncertain: conflicting host instances')
            if transport.get('instance') is None:
                transport.update(instance=event['instance'], protocol=foreground_host.PROTOCOL, receipt=str(path))
                changed = True
            if event.get('run_record') != str(record_path) or event.get('invocation_id') != launch.get('invocation_id'):
                continue
            if event.get('kind') == 'request-intent' and event['request'].get('method') == 'turn/start':
                if request is not None and request != event['request']:
                    raise WorkflowError('transport_uncertain: multiple turn requests for one original invocation')
                request = event['request']
            if event.get('kind') != 'message':
                continue
            message = event['message']
            if request is not None and message.get('id') == request['id'] and 'result' in message:
                response = message['result']
            params = message.get('params', {})
            if message.get('method') == 'item/completed':
                item = params.get('item', {})
                if item.get('type') == 'agentMessage' and item.get('phase') in {None,'final_answer'}:
                    key = (params.get('threadId'),params.get('turnId'))
                    if key in finals and finals[key] != item.get('text'):
                        raise WorkflowError('transport_uncertain: conflicting final messages')
                    finals[key] = item.get('text')
            if message.get('method') == 'turn/completed' and params.get('turn', {}).get('status') == 'completed':
                terminals.add((params.get('threadId'),params['turn'].get('id')))
    if changed:
        _atomic_save(record_path, state)
    if request is not None and response is not None and launch.get('state') in {'spawning','launched'}:
        thread, turn = request['params'].get('threadId'), response.get('turn', {}).get('id')
        final = finals.get((thread,turn))
        if (thread,turn) in terminals and isinstance(final,str):
            expected_input = [{'type':'text','text':launch.get('request', {}).get('prompt')}]
            if request['params'].get('input') != expected_input:
                raise WorkflowError('transport_uncertain: turn does not match the saved launch')
            output = _artifact_path(record_path,state['current_stage'],launch['turn'])
            _write_stage_output(output, final)
            result = _read_stage_result(output,state['current_stage'])
            recovered = {'run_record':str(record_path),'stage':state['current_stage'],'turn':launch['turn'],
                'invocation_id':launch['invocation_id'],'request_digest':entry_prepare.digest(launch['request']),
                'host_instance':transport['instance'],'events':[{'type':'thread.started','thread_id':thread},
                    {'type':'turn.completed','thread_id':thread,'turn_id':turn}],
                'outcome':'completed_turn','result':result}
            receipt_path = _carrier_receipt_path(record_path,state['current_stage'],launch['turn'])
            if receipt_path.exists():
                previous = _read_json(receipt_path,'carrier receipt')
                if any(previous.get(key) != recovered[key] for key in ('run_record','stage','turn','invocation_id','request_digest','host_instance')):
                    raise WorkflowError('carrier receipt differs from original transport identity')
                if previous.get('outcome') == 'completed_turn' and previous.get('result') != result:
                    raise WorkflowError('carrier receipt conflicts with original final message')
            progression.atomic_save(receipt_path,recovered)


def _mark_failure(
    state: dict[str, Any], record_path: Path, code: str, detail: str, uncertain: bool,
    recoverable: bool = False,
) -> None:
    state["status"] = "interrupted" if uncertain else "failed"
    state["launch"]["state"] = "uncertain" if uncertain else "failed"
    state["error"] = {"code": code, "detail": detail, "recoverable": recoverable and not uncertain}
    state["history"].append({"event": "failure", "stage": state["current_stage"], "code": code})
    _atomic_save(record_path, state)


def _invoke(state, record_path, answer, continuing, host, *, control_turn=False):
    stage, confirmed = state["current_stage"], state["confirmed"]
    _check_current_registry(confirmed, [int(stage[-1])])
    verify_identity(confirmed['packages']['runner'])
    verify_identity(confirmed['packages'][stage])
    turn = int(state["launch"].get("turn", 0)) + 1
    output = _artifact_path(record_path, stage, turn)
    prompt = _stage_prompt(state, stage, answer, continuing)
    request = {"stage":stage, "prompt":prompt, "settings":confirmed['stages'][stage]}
    state["launch"] = {"stage":stage,"state":"spawning","turn":turn,"output":str(output),
                       "invocation_id":str(uuid.uuid4()),"request":request}
    _atomic_save(record_path, state, require_launch_admission=not control_turn)
    receipt_path = _carrier_receipt_path(record_path, stage, turn)
    receipt = {"run_record":str(record_path), "stage":stage, "turn":turn,
               "invocation_id":state['launch']['invocation_id'], "request_digest":entry_prepare.digest(request),
               "host_instance":host.instance, "events":[], "outcome":"running"}
    progression.atomic_save(receipt_path, receipt)
    try:
        if host.process is None:
            host.start(record_path.with_name(record_path.name + '.host.stderr'))
        session = host.start_carrier(stage, confirmed['stages'][stage])
        prior = state['sessions'].get(stage)
        if prior is not None and prior != session:
            raise WorkflowError('await-host-recovery: original carrier identity cannot be replaced')
        state['sessions'][stage] = session
        state['launch']['state'] = 'launched'
        receipt['events'].append({'type':'thread.started','thread_id':session})
        progression.atomic_save(receipt_path, receipt)
        try:
            _atomic_save(record_path, state)
        except (OSError, entry_prepare.PreparationError, WorkflowError) as error:
            raise CheckpointPendingError(f'checkpoint_write_pending: reconcile the original launch from {receipt_path}; no executor retry') from error
        steered = {entry_prepare.digest(state['runner_request'])} if state.get('runner_request') else set()
        last_progress = [time.monotonic()]
        def on_progress(event):
            now = time.monotonic()
            if event is not None and event.get('method') in {'item/agentMessage/delta','item/started','item/completed'}:
                print(json.dumps({'event':'carrier.progress','stage':stage,'method':event['method'],
                    'delta':str(event.get('params', {}).get('delta',''))[:4096]}), flush=True)
                last_progress[0] = now
            elif now - last_progress[0] >= 30:
                print(json.dumps({'event':'carrier.waiting','stage':stage,'session':session}), flush=True)
                last_progress[0] = now
            _live_progress(state, record_path, host, steered)
        text, host_turn = host.run_turn(session, prompt, confirmed['stages'][stage],
            _read_json(SCHEMA_PATH, 'stage schema'), on_progress)
        receipt['events'].append({'type':'turn.completed','thread_id':session,'turn_id':host_turn})
        _write_stage_output(output, text)
        result = _read_stage_result(output, stage)
        receipt.update(outcome='completed_turn', result=result)
        try:
            progression.atomic_save(receipt_path, receipt)
        except OSError as error:
            raise CheckpointPendingError('checkpoint_write_pending: recover the exact finished turn from original host receipts; no executor retry') from error
        # The host may have answered RPC requests while this turn ran. Start
        # intake from that current checkpoint, then record the local result.
        state.refresh(progression.read_record(record_path))
        _finish_carrier_turn(state, result)
        try:
            _atomic_save(record_path, state)
        except (OSError, entry_prepare.PreparationError, WorkflowError) as error:
            raise CheckpointPendingError(f'checkpoint_write_pending: recover completed transport intake from {receipt_path}; no executor retry') from error
        return result
    except CheckpointPendingError:
        raise
    except foreground_host.HostPrelaunchError as error:
        state['status'] = 'failed'
        state['launch']['state'] = 'prelaunch'
        state['error'] = {'code':'host_prelaunch','detail':str(error),'recoverable':True}
        receipt.update(outcome='not-issued', error=str(error))
        progression.atomic_save(receipt_path,receipt)
        _atomic_save(record_path,state)
        raise WorkflowError(str(error)) from error
    except (foreground_host.HostError, WorkflowError) as error:
        state.refresh(progression.read_record(record_path))
        _mark_failure(state, record_path, 'transport_lost', str(error), True)
        raise WorkflowError(str(error)) from error


def _stop_status(state, current, request):
    """A request/finished CLI turn is not proof that native writers stopped."""
    cancelling = (request or {}).get("operation") == "cancel" or (current or {}).get("stop_requested") == "cancelling" or (current or {}).get("status") == "cancelling"
    if cancelling:
        unstarted = state['launch']['state'] == 'prelaunch' and state['current_stage'] not in state['sessions']
        if unstarted and (current is None or current['stage'] != int(state['current_stage'][-1]) and current['status'] == 'accepted' and current.get('stopped')):
            return 'cancelled'
        return "cancelled" if current and current["status"] == "cancelled" else "cancelling"
    if current and current["stage"] == int(state["current_stage"][-1]):
        return "paused" if current["status"] in {"paused", "accepted", "cancelled"} and current.get("stopped") else "pausing"
    no_carrier = state["launch"]["state"] == "prelaunch" and state["current_stage"] not in state["sessions"]
    previous_stopped = current is None or current["status"] in {"accepted", "cancelled"} and current.get("stopped")
    return "paused" if no_carrier and previous_stopped else "pausing"


def _advance(state, record_path, answer=None):
    # The run lock is held by the caller throughout this connection's lifetime.
    def journal(event):
        event = {**event, 'run_record':str(record_path), 'stage':state['current_stage'],
                 'invocation_id':state.get('launch', {}).get('invocation_id')}
        receipt = record_path.with_name(record_path.name + '.host.events.jsonl')
        try:
            descriptor = os.open(receipt, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as error:
            raise WorkflowError('host event log cannot be opened safely') from error
        identity = os.fstat(descriptor)
        if not stat.S_ISREG(identity.st_mode) or identity.st_uid != os.geteuid() or identity.st_nlink != 1:
            os.close(descriptor)
            raise WorkflowError('host event log must be an owner-owned regular file')
        with os.fdopen(descriptor, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        native_event = (event['kind'] == 'message' and
            event['message'].get('params', {}).get('item', {}).get('type') == 'subAgentActivity')
        if event['kind'] == 'message' and not native_event:
            return  # Raw progress/RPC bytes are durable without rewriting C's checkpoint.
        with progression.record_lock(record_path):
            saved = progression.read_record(record_path)
            transport = saved.setdefault('transport', {})
            transport.update(instance=event['instance'], protocol=foreground_host.PROTOCOL, receipt=str(receipt))
            if event['kind'] == 'request-intent':
                transport['request'] = event['request']
                transport['request_state'] = 'intent'
            if event['kind'] == 'request-result':
                transport['request_state'] = event['outcome']
            if event['kind'] == 'host-started':
                transport['pid'] = event['pid']
                transport['state'] = 'live'
            if native_event:
                transport['native_event_sequence'] = transport.get('native_event_sequence', 0) + 1
            if event['kind'] == 'turn-bound':
                transport.update(thread=event['thread'], turn=event['turn'])
            if event['kind'] == 'server-request':
                transport.setdefault('server_requests', {})[entry_prepare.digest(event['request']['id'])] = event['request']
                if saved.get('pending_input', {}).get('kind') != 'host-request':
                    transport['suspended_pending'] = saved.get('pending_input')
                    saved['pending_input'] = _host_pending(event['instance'],event['request'])
                saved['status'] = 'needs_input'
                displayed = transport['server_requests'][entry_prepare.digest(saved['pending_input']['subject']['request_id'])]
                print(json.dumps({'status':'needs_input','pending':saved['pending_input'],'request':displayed}), flush=True)
            if event['kind'] == 'server-response-sent':
                pending_requests = transport.get('server_requests', {})
                pending_requests.pop(entry_prepare.digest(event['request_id']),None)
                saved.pop('pending_input',None)
                if pending_requests:
                    next_request = next(iter(pending_requests.values()))
                    saved['pending_input'] = _host_pending(event['instance'], next_request)
                    print(json.dumps({'status':'needs_input','pending':saved['pending_input'],'request':next_request}), flush=True)
                else:
                    pending = (saved.get(progression.KEY) or {}).get('pending') or transport.pop('suspended_pending',None)
                    if pending is not None:
                        saved['pending_input'] = pending
            if event['kind'] == 'local-process-closed':
                transport.update(state='closed' if transport.get('state') == 'closing' else 'lost', cleanup=event)
            progression.atomic_save(record_path, saved)
        if native_event and saved.get(progression.KEY) is not None:
            owners = [stage for stage in host.threads if event['message'] in host.scoped_events(stage, event['message'])]
            if len(owners) != 1:
                return  # Retain unassigned raw evidence; snapshot closure must reconcile it.
            current = progression.read_record(record_path)[progression.KEY]
            progression.handle(record_path, {'protocol':progression.PROTOCOL, 'operation':'host-event',
                'expected_revision':current['revision'], 'data':{'instance':event['instance'],'event':event['message'],
                    'carrier_thread':host.threads[owners[0]]}})
    host = (foreground_host.ForegroundHost(state['confirmed']['host'], state['confirmed']['repository'], journal)
            if 'host' in state['confirmed'] else None)
    try:
        while True:
            result = _advance_in_host(state, record_path, answer, host)
            answer = None
            if host is None or host.process is None or state['status'] in {'completed','cancelled'}:
                if host is not None and host.process is not None:
                    with progression.record_lock(record_path):
                        closing = progression.read_record(record_path)
                        closing['transport']['state'] = 'closing'
                        progression.atomic_save(record_path, closing)
                return result
            print(json.dumps({'status':state['status'], 'run_record':str(record_path),
                              'host_retained':True}), flush=True)
            while True:
                _live_progress(state, record_path, host, set())
                if _live_commands(state, record_path):
                    break
                current = state.get(progression.KEY) or {}
                if state.get('runner_request') or current.get('stop_requested'):
                    status = _stop_status(state, current, state.get('runner_request'))
                    if status != state['status']:
                        state['status'] = status
                        _atomic_save(record_path, state)
                if state['status'] in {'pausing','cancelling'} and not current.get('recovery_action'):
                    if (state.get('transport', {}).get('lookup_blocked') or
                            (current.get('stop_requested') is None and state.get('runner_request', {}).get('request_id') in state.get('delivered_control_requests', [])) or
                            (current.get('cancellation') is not None and current['cancellation'].get('result') is None) or
                            (current.get('host', {}).get('status') == 'unknown' and (current.get('host', {}).get('query') or {}).get('resolved'))):
                        host.poll(1)
                        continue
                    # The original carrier runs C's stop/lookup action only.
                    # Its JSON footer cannot establish the stop barrier.
                    _invoke(state, record_path, None, True, host, control_turn=True)
                    break
                if state['status'] == 'cancelled':
                    with progression.record_lock(record_path):
                        closing = progression.read_record(record_path)
                        closing['transport']['state'] = 'closing'
                        progression.atomic_save(record_path, closing)
                    return result
                host.poll(1)
                _live_progress(state, record_path, host, set())
    except BaseException as error:
        if host is not None and host.process is not None:
            try:
                _record_transport_loss(record_path, str(error) or type(error).__name__)
            except entry_prepare.PreparationError as persistence_error:
                print(json.dumps({'status':'transport_uncertain','checkpoint':str(record_path),
                                  'error':str(persistence_error)}), file=sys.stderr)
        raise
    finally:
        if host is not None:
            host.close()


def _advance_in_host(state: dict[str, Any], record_path: Path, answer, host) -> int:
    resume_answer = answer
    deferred_launch_marker = None
    while True:
        stage = state["current_stage"]
        saved = progression.read_record(record_path)
        request = saved.get("runner_request")
        current_progress = saved.get(progression.KEY)
        route = _runner_route(saved, state)
        if route == 'stop':
            stop_delivery = state.pop('stop_delivery_pending', False)
            answer_delivery = state.pop('control_delivery_pending', False) and (request or {}).get('operation') != 'cancel'
            if stop_delivery or answer_delivery:
                _invoke(state, record_path, state.get('answer_pending_delivery'), True, host, control_turn=True)
                saved = progression.read_record(record_path)
                current_progress, request = saved.get(progression.KEY), saved.get('runner_request')
            state["status"] = _stop_status(state, current_progress, request)
            _atomic_save(record_path, state)
            print(json.dumps({"status": state["status"], "run_record": str(record_path)}))
            return 0
        if current_progress and current_progress["stage"] == int(stage[-1]):
            if route == 'business_block':
                state['status'] = 'failed'
                state['error'] = {'code':'business_block','business_block_id':current_progress['business_block']['id'],'recoverable':True}
                _atomic_save(record_path,state)
                print(json.dumps({"status": "blocked", "business_block": current_progress["business_block"],
                                  "next_action": {"operation": "await-business-recovery"}}))
                return 1
            if route == 'progression_blocked':
                state["status"] = "failed"
                state["error"] = {"code": "progression_blocked", "detail": "reconcile the retained C checkpoint", "recoverable": True}
                _atomic_save(record_path, state)
                print(json.dumps({"status": "blocked", "run_record": str(record_path), "error": current_progress.get("error")}))
                return 1
            if route == 'needs_input':
                state["status"], state["pending_input"] = "needs_input", current_progress["pending"]
                _atomic_save(record_path, state)
                print(json.dumps({"status": "needs_input", "pending": state["pending_input"]}))
                return 0
            if route == 'result_ready':
                projected = stage_handoff.render(current_progress["accepted"])["stage_result"]
                state["turn_result"] = {**projected, "handoff": json.loads(projected["handoff_json"])}
        # A recorded turn is received before any later transport instruction.
        # An unconsumed decision remains queued; it cannot make us run that turn again.
        result = state.get("turn_result")
        if result is None:
            try:
                result = _invoke(state, record_path, resume_answer, stage in state["sessions"], host)
            except LaunchDeferred:
                latest = progression.read_record(record_path)
                latest_progress = latest.get(progression.KEY) or {}
                latest_request = latest.get('runner_request') or {}
                marker = (latest.get('current_stage'), latest_request.get('request_id'),
                          latest_request.get('operation'), latest_progress.get('stage'),
                          latest_progress.get('stop_requested'), latest_progress.get('status'),
                          latest_progress.get('phase_complete'),
                          (latest_progress.get('pending') or {}).get('decision_id'),
                          (latest_progress.get('business_block') or {}).get('id'),
                          bool(latest_progress.get('deferred_business_recovery')),
                          latest.get('status'), bool(latest.get('resume_progression')))
                if marker == deferred_launch_marker:
                    raise WorkflowError('launch_deferred: checkpoint did not reach a ready branch')
                deferred_launch_marker = marker
                state.refresh(latest)
                continue
            deferred_launch_marker = None
        resume_answer = None
        if result["result"] == "continue":
            state.pop("turn_result", None)
            actual_progress = progression.read_record(record_path).get(progression.KEY, {})
            fingerprint = entry_prepare.digest({"message": result["message"], "artifacts": result["artifacts"],
                "evidence": result["evidence"], "progress_revision": actual_progress.get("revision")})
            waiting = actual_progress.get("host", {}).get("status") == "running"
            repeats = 0 if waiting else (state.get("continue_repeats", 0) + 1 if fingerprint == state.get("continue_fingerprint") else 0)
            state["continue_fingerprint"], state["continue_repeats"] = fingerprint, repeats
            if repeats >= 2:
                _record_carrier_failure(state, record_path, 'no_progress', result)
                _mark_failure(state, record_path, "carrier_no_progress", "carrier repeated unchanged progress; reconcile its retained next action", False, recoverable=True)
                return 1
            state["status"] = "active"
            state["history"].append({"event": "continue", "stage": stage})
            _atomic_save(record_path, state)
            continue
        if result["result"] == "needs_input":
            state.pop("turn_result", None)
            if result['needs_input_kind'] == 'technical_error':
                _record_carrier_failure(state, record_path, 'technical_error', result)
                state['status'] = 'failed'
                state['error'] = {'code':'stage_technical_error','detail':result['question'],'recoverable':True}
                _atomic_save(record_path,state)
                print(json.dumps({'status':'blocked','error':state['error'],'run_record':str(record_path)}),flush=True)
                return 1
            state["status"] = "needs_input"
            current = progression.read_record(record_path).get(progression.KEY)
            pending = current.get("pending") if current else None
            if pending is None:
                pending = progression.pending_decision("user-decision", {"stage": stage, "session": state["sessions"][stage],
                    "turn": state["launch"]["turn"]}, result["question"])
            state["pending_input"] = pending
            state["history"].append({"event": "needs_input", "stage": stage})
            _atomic_save(record_path, state)
            print(json.dumps({"status": "needs_input", "run_record": str(record_path), "pending": pending}))
            return 0
        if result["result"] == "completed":
            try:
                accepted = _accepted_stage(record_path, stage, result, state["confirmed"])
                _validate_handoff_continuity(
                    stage, result, state["confirmed"], state["stage_results"].get("stage2")
                )
            except WorkflowError as exc:
                _mark_failure(state, record_path, "handoff_validation_error", str(exc), False)
                raise
        if stage == "stage4":
            try:
                _validate_closure_scope(result, state["stage_results"].get("stage3"))
            except WorkflowError as error:
                _mark_failure(state, record_path, "handoff_validation_error", str(error), False)
                raise
        state["stage_results"][stage] = result
        state.pop("turn_result", None)
        print(json.dumps({"event": "stage.completed", "stage": stage}), flush=True)
        index = STAGES.index(stage)
        if index == len(STAGES) - 1:
            state["completion_evidence"] = _verify_run_completion(record_path, state)
            state["status"] = "completed"
            state["history"].append({"event": "completed", "stage": stage})
            _atomic_save(record_path, state)
            print(json.dumps({"status": "completed", "run_record": str(record_path),
                              "artifacts": {key: value["artifacts"] for key, value in state["stage_results"].items()}}))
            return 0
        state["current_stage"] = STAGES[index + 1]
        state["status"] = "active"
        state.pop("pending_input", None)
        state["launch"] = {"stage": state["current_stage"], "state": "prelaunch", "turn": 0}
        pending = progression.stage_boundary(state["confirmed"]["flow_mode"], int(stage[-1]), accepted)
        if pending is not None:
            # Use the C-owned boundary ID, never create a second confirmation.
            current = progression.read_record(record_path)[progression.KEY]
            state["pending_input"] = current.get("pending") or pending
            state["status"] = "needs_input"
        state["history"].append({"event": "checkpoint", "next_stage": state["current_stage"]})
        _atomic_save(record_path, state)
        print(json.dumps({"event": "checkpoint", "next_stage": state["current_stage"]}), flush=True)
        if state["status"] == "needs_input":
            print(json.dumps({"status": "needs_input", "pending": state["pending_input"]}))
            return 0


def _verify_run_completion(record_path, state):
    try:
        entry_prepare.require(all(stage in state["stage_results"] for stage in STAGES),
                              "result_incomplete", "every required stage must be accepted")
        outer = progression.read_record(record_path)
        current = outer.get(progression.KEY)
        progression.validate_state(current)
        entry_prepare.require(not progression.Progress(record_path,outer).stop_barrier_pending(),
                              'host_evidence_missing', 'final completion requires current complete native evidence')
        result = progression.verify_completion(current)
        expected = stage_handoff.render(current["accepted"])["stage_result"]
        entry_prepare.require(all(state["stage_results"]["stage4"][key] == value for key, value in expected.items()),
                              "result_changed", "final runner result differs from B acceptance")
        return result
    except entry_prepare.ERROR_TYPES as error:
        raise WorkflowError("workflow completion unverified: " + entry_prepare.error_message(error)) from error


def _accepted_stage(record_path, stage, result, confirmed):
    outer = progression.read_record(record_path)
    current = outer.get(progression.KEY)
    try:
        progression.validate_state(current)
        entry_prepare.require(current["status"] == "accepted" and current["stage"] == int(stage[-1]),
                              "result_incomplete", "stage requires its persisted B acceptance")
        entry_prepare.require(current.get('stopped') is True and not progression.Progress(record_path,outer).stop_barrier_pending(),
                              'host_evidence_missing', 'stage handoff requires the current complete native stop barrier')
        accepted = current["accepted"]
        progression.verify_phase_completed(current)
        projection = stage_handoff.render(accepted)["stage_result"]
        entry_prepare.require(all(result[k] == v for k, v in projection.items()), "result_changed", "return the exact B completion projection")
        saved = accepted["handoff"]
        entry_prepare.require(saved["controller_ref"] == confirmed["controller_ref"] and
            accepted["requirement_identity"]["sha256"] == confirmed["frozen_requirement"]["sha256"] and
            saved["source_commit"] == confirmed["frozen_requirement"]["commit"],
            "source_changed", "accepted stage differs from confirmed source/controller")
        stage_handoff.verify_result(int(stage[-1]), accepted["payload"], saved["binding"], saved["scope"],
                                   saved["expected_entry"]["repository"]["root"], accepted["role_ref"])
        return accepted
    except entry_prepare.ERROR_TYPES as error:
        raise WorkflowError("stage acceptance failed: " + entry_prepare.error_message(error)) from error


def start(confirmed_path: Path) -> int:
    raw = _read_json(confirmed_path, "confirmed input")
    raw['registry_input'] = str(confirmed_path.resolve())
    confirmed = validate_confirmed(raw)
    record_path = Path(confirmed["run_record"])
    if record_path.exists():
        raise WorkflowError("run record already exists; use resume")
    with RunLock(record_path):
        if record_path.exists():
            raise WorkflowError("run record already exists; use resume")
        state = _new_state(confirmed)
        _atomic_save(record_path, state)
        return _advance(state, record_path)


def _queue_resume(record_path, answer, registry_input, decision_id):
    observed = _validate_record(_read_json(record_path, 'run record'))
    if registry_input is not None:
        observed['confirmed']['registry_input'] = str(registry_input.resolve())
    _check_current_registry(observed['confirmed'], [int(s[-1]) for s in STAGES[STAGES.index(observed['current_stage']):]])
    with progression.record_lock(record_path):
        state = _validate_record(progression.read_record(record_path))
        if state['status'] == 'completed':
            _verify_run_completion(record_path, state)
            print(json.dumps({'status':'completed','acknowledged':True,'run_record':str(record_path)}))
            return 0
        if state.get('transport', {}).get('state') in {'closing', 'closed', 'lost'}:
            raise WorkflowError('await-host-recovery: owner is closing; request was not queued')
        current = state.get(progression.KEY) or {}
        request = state.get('runner_request')
        if (request or {}).get('operation') == 'cancel' or current.get('stop_requested') == 'cancelling':
            raise WorkflowError('cancellation_pending: an answer cannot release cancellation')
        server_pending = state.get('pending_input')
        pending = server_pending if (server_pending or {}).get('kind') == 'host-request' else current.get('pending') or server_pending
        previous = state.get('answers', {}).get(decision_id) if decision_id else None
        acknowledged = previous is not None
        if previous is not None:
            if previous != answer:
                raise WorkflowError('decision_conflict: retain the first exact answer')
        elif answer is not None:
            if not answer or pending is None or decision_id != pending['decision_id']:
                raise WorkflowError('stale_decision: answer the exact current pending matter')
            decision = {'decision_id':decision_id, 'subject':pending['subject'], 'answer':answer,
                        'reference':'controller-answer:' + decision_id}
            if pending['kind'] == 'host-request':
                try:
                    response = json.loads(answer)
                except json.JSONDecodeError as error:
                    raise WorkflowError('invalid_host_answer: supply exact RPC result JSON') from error
                if not isinstance(response,dict):
                    raise WorkflowError('invalid_host_answer: RPC result must be an object')
                if state['transport'].get('server_decision') not in (None,decision):
                    raise WorkflowError('decision_conflict: an original server decision is pending')
                state['transport']['server_decision'] = decision
            else:
                if state.get('controller_decision') not in (None, decision):
                    raise WorkflowError('decision_conflict: another original decision is awaiting consumption')
                state['controller_decision'] = decision
            state.setdefault('answers', {})[decision_id] = answer
        elif current.get('stop_requested') == 'pausing' or (request or {}).get('operation') == 'pause':
            subject = {'stage':current.get('stage'), 'attempt':(current.get('dispatch') or {}).get('request', {}).get('attempt'),
                       'handoff':(current.get('handoff') or {}).get('digest')}
            intent = {'stop_request':request, 'subject':subject}
            if state.get('queued_resume') is not None and state['queued_resume'] != intent:
                raise WorkflowError('stale_decision: pause identity changed')
            state['queued_resume'] = intent
        else:
            print(json.dumps({'status':state['status'], 'pending':pending, 'acknowledged':True}))
            return 0
        state['confirmed']['registry_input'] = observed['confirmed']['registry_input']
        progression.atomic_save(record_path, state)
    print(json.dumps({'status':'queued', 'run_record':str(record_path), 'acknowledged':acknowledged}))
    return 0


def resume(record_path, answer=None, registry_input=None, decision_id=None):
    record_path = _absolute_path(str(record_path), 'run_record')
    try:
        with RunLock(record_path):
            return _resume_owned(record_path, answer, registry_input, decision_id)
    except RunBusy:
        return _queue_resume(record_path, answer, registry_input, decision_id)


def _resume_owned(record_path: Path, answer: str | None = None, registry_input: Path | None = None, decision_id: str | None = None) -> int:
    record_path = _absolute_path(str(record_path), "run_record")
    state = CheckpointState(_validate_record(_read_json(record_path, "run record")))
    _recover_transport(state, record_path)
    _recover_carrier_receipt(state, record_path)
    status = state["status"]
    stage = state["current_stage"]
    if status == "completed":
        _verify_run_completion(record_path, state)
        print(json.dumps({"status": "completed", "run_record": str(record_path), "acknowledged": True}))
        return 0
    if status == 'cancelled' and state.get('transport', {}).get('state') in {None,'closed'}:
        current = state.get(progression.KEY)
        if current is not None:
            historical_boundary = (current['status'] == 'accepted' and state['launch']['state'] == 'prelaunch' and current['stage'] != int(state['current_stage'][-1]))
            entry_prepare.require((current['status'] == 'cancelled' or historical_boundary) and current['stopped'] and
                not progression.Progress(record_path,state).stop_barrier_pending(), 'host_evidence_missing', 'cancelled acknowledgement requires the retained complete stop barrier')
        print(json.dumps({'status':'cancelled','acknowledged':True,'run_record':str(record_path)}))
        return 0
    if state.get('transport') is not None or state['sessions']:
        # This process holds the run lock, hence it is not the old owner.
        # A durable carrier receipt permits intake, never a new host/ref.
        if state.get('transport') is not None and state['transport'].get('state') != 'lost':
            _record_transport_loss(record_path, 'foreground owner is absent')
        retained = progression.read_record(record_path)
        details = (retained.get(progression.KEY) or {}).get('recovery_action') or {
            'host_instance': (retained.get('transport') or {}).get('instance'), 'carrier_threads':retained.get('sessions', {}),
            'missing':['original-host-identity-and-native-stop-or-resume-evidence']}
        raise WorkflowError('await-host-recovery: original foreground owner is absent; ' + json.dumps(details, sort_keys=True))
    if state['launch']['state'] == 'prelaunch' and state.get('error', {}).get('code') == 'host_prelaunch':
        if registry_input is not None:
            state['confirmed']['registry_input'] = str(registry_input.resolve())
        _check_current_registry(state['confirmed'], [int(stage[-1])])
        state['status'] = 'active'
        state.pop('error',None)
        _atomic_save(record_path,state)
        return _advance(state,record_path)
    current = state.get(progression.KEY)
    if current and current.get("business_block") and not current.get("deferred_business_recovery"):
        print(json.dumps({"status": "blocked", "business_block": current["business_block"],
                          "next_action": {"operation": "await-business-recovery"}}))
        return 1
    request = state.get("runner_request")
    recovered_cancel = request and request["operation"] == "cancel" and state.get(progression.KEY, {}).get("recovered_request") == request
    if request and request["operation"] == "cancel" and not recovered_cancel:
        raise WorkflowError("cancelled carrier requires exact stopped-writer reconciliation through the original control adapter")
    if request and (request["operation"] == "pause" or recovered_cancel):
        state.pop("runner_request")
        with progression.record_lock(record_path):
            current_record = progression.read_record(record_path)
            if current_record.get("runner_request") != request:
                raise WorkflowError("control request changed; inspect current cancellation before resuming")
            current_record.pop("runner_request")
            progression.atomic_save(record_path, current_record)
        current = state.get(progression.KEY)
        if current and current["status"] == "paused":
            state["resume_progression"] = True
        state["status"] = status = "needs_input" if state.get("pending_input") else "active"
        _atomic_save(record_path, state)
    elif status == "paused" and state.get(progression.KEY):
        state["resume_progression"] = True
        state["status"] = status = "needs_input" if state.get("pending_input") else "active"
        _atomic_save(record_path, state)
    previous = state.get("answers", {}).get(decision_id) if decision_id else None
    if previous is not None:
        if previous != answer:
            raise WorkflowError("decision_conflict: answer cannot be replaced")
        print(json.dumps({"status": status, "acknowledged": True}))
        return 0
    if registry_input is not None:
        state['confirmed']['registry_input'] = str(registry_input.resolve())
    _check_current_registry(state['confirmed'], [int(value[-1]) for value in STAGES[STAGES.index(stage):]])
    current = state.get(progression.KEY)
    if current and status == "failed" and current["status"] == "blocked" and state.get("error", {}).get("recoverable"):
        state["resume_progression"] = True
        state["status"] = "active"
        _atomic_save(record_path, state)
        return _advance(state, record_path)
    if current and current["stage"] == int(stage[-1]) and status != "needs_input" and current["status"] == "accepted" and current["phase_complete"] and state.get("launch", {}).get("state") != "uncertain":
        return _advance(state, record_path)
    safe_between_stages = status == "active" and state.get("launch", {}).get("state") == "prelaunch" and stage not in state["sessions"]
    if status == "needs_input":
        pending = state["pending_input"]
        if answer is None and decision_id is None:
            print(json.dumps({"status": "needs_input", "pending": pending}))
            return 0
        if not answer or decision_id != pending["decision_id"]:
            raise WorkflowError("stale_decision: provide --decision-id for the current pending matter")
        decision = {"decision_id": decision_id, "subject": pending["subject"], "answer": answer,
                    "reference": "controller-answer:" + decision_id}
        current = progression.read_record(record_path).get(progression.KEY)
        if current and current.get("pending") == pending:
            if pending["kind"] == "stage-entry":
                # This only records the controller's route decision; no A/B
                # carrier entry is consumed in another runtime context.
                result = progression.handle(record_path, {"protocol": progression.PROTOCOL, "operation": "decide",
                    "expected_revision": current["revision"], "data": decision})
                if result["status"] == "blocked":
                    raise WorkflowError("stage decision blocked; inspect the retained checkpoint")
            else:
                state["controller_decision"] = decision
        state.setdefault("answers", {})[decision_id] = answer
        state["answer_pending_delivery"] = answer
        if pending["kind"] == "stage-entry" and answer == "continuous":
            state["confirmed"]["flow_mode"] = "continuous"
        state["status"] = "active"
        state.pop("pending_input", None)
        _atomic_save(record_path, state)
        return _advance(state, record_path, answer)
    if status == "active" and state.get("resume_progression") and state["launch"]["state"] in {"prelaunch", "completed_turn", "failed"}:
        return _advance(state, record_path)
    if safe_between_stages:
        return _advance(state, record_path, state.get("answer_pending_delivery"))
    if status in {"active", "failed"} and "turn_result" in state:
        return _advance(state, record_path)
    if status == "active" and state.get("controller_decision") and state["launch"]["state"] in {"prelaunch", "completed_turn"}:
        return _advance(state, record_path, state.get("answer_pending_delivery"))
    if status == "active" and state.get("answer_pending_delivery") and state["launch"]["state"] == "completed_turn":
        return _advance(state, record_path, state["answer_pending_delivery"])
    known_recovery = (
        status == "failed"
        and state.get("launch", {}).get("state") == "failed"
        and state.get("error", {}).get("recoverable") is True
        and isinstance(state["sessions"].get(stage), str)
    )
    if known_recovery:
        state["status"] = "active"
        state.pop("pending_input", None)
        _atomic_save(record_path, state)
        return _advance(state, record_path, answer)
    raise WorkflowError(f"run cannot resume from status {status}; it will not retry or duplicate an executor")


def recover_business(record_path, decision_path):
    record_path = _absolute_path(str(record_path), 'run_record')
    try:
        with RunLock(record_path):
            return _recover_business(record_path, decision_path)
    except RunBusy:
        return _recover_business(record_path, decision_path, live=True)


def _recover_business(record_path: Path, decision_path: Path, *, live=False) -> int:
    """Submit in the original Controller context; never launch a CLI carrier."""
    record_path = _absolute_path(str(record_path), "run_record")
    decision = _read_json(decision_path, "business recovery decision")
    state = _validate_record(_read_json(record_path, "run record"))
    current = state.get(progression.KEY)
    if current is None:
        raise WorkflowError("missing_checkpoint: business recovery requires the retained C checkpoint")
    result = progression.handle(record_path, {"protocol": progression.PROTOCOL,
        "operation": "recover-business", "expected_revision": current["revision"], "data": decision})
    state = CheckpointState(progression.read_record(record_path))
    current = state[progression.KEY]
    consumed = current.get("recovery_decisions", {}).get(decision.get("decision_id")) == decision
    if consumed and not live and not current.get("business_block"):
        stage = state["current_stage"]
        if (current["status"] == "active" and state["status"] in {"active", "failed"}
                and current["stage"] == int(stage[-1]) and isinstance(state["sessions"].get(stage), str)
                and state["launch"]["state"] in {"completed_turn", "failed"} and not state.get("runner_request")):
            state["progression_response"] = result
            state["resume_progression"] = True
            state["status"] = "active"
            _atomic_save(record_path, state)
    print(json.dumps(result))
    return 1 if result["status"] == "blocked" else 0


def request_control(record_path, operation):
    record_path = _absolute_path(str(record_path), 'run_record')
    try:
        with RunLock(record_path):
            return _request_control(record_path, operation, live=False)
    except RunBusy:
        return _request_control(record_path, operation, live=True)


def _request_control(record_path, operation, *, live):
    record_path = _absolute_path(str(record_path), "run_record")
    with progression.record_lock(record_path):
        state = progression.read_record(record_path)
        if state.get("version") != 6:
            raise WorkflowError("legacy_run_requires_original_runtime: control requires the original version-5 record")
        member = state.get(progression.KEY)
        pin = state.get("confirmed", {}).get("packages", {}).get("runner")
        if ((member is not None and member.get("protocol") != progression.PROTOCOL) or
                (pin is not None and pin.get("compatibility_key", {}).get("workflow_progress") != progression.PROTOCOL)):
            raise WorkflowError("legacy_run_requires_original_runtime: use the original pinned control runtime; record is unchanged")
        if state["status"] == "completed":
            _verify_run_completion(record_path, state)
            print(json.dumps({"status": "completed", "acknowledged": True}))
            return 0
        if state.get("runner_request", {}).get("operation") == "cancel":
            print(json.dumps({"status": "cancelling", "acknowledged": True}))
            return 0
        current = state.get(progression.KEY) or {}
        if operation == "pause" and (current.get("stop_requested") == "cancelling" or current.get("status") == "cancelling"):
            print(json.dumps({"status": "cancelled" if current["status"] == "cancelled" else "cancelling", "acknowledged": True}))
            return 0
        state["runner_request"] = {"operation": operation, "request_id": str(uuid.uuid4())}
        if (not live and not state.get('sessions') and not state.get('transport') and
                state.get('launch', {}).get('state') == 'prelaunch' and not current):
            state['status'] = 'paused' if operation == 'pause' else 'cancelled'
        progression.atomic_save(record_path, state)
    current = state.get(progression.KEY)
    if live:
        print(json.dumps({'status':'queued','operation':operation,'run_record':str(record_path)}))
        return 0
    if state['status'] in {'paused','cancelled'} and current is None:
        print(json.dumps({'status':state['status'],'run_record':str(record_path)}))
        return 0
    result = None
    if current and current["status"] not in {"accepted", "cancelled"} and (operation == "cancel" or current["status"] != "paused"):
        result = progression.handle(record_path, {"protocol": progression.PROTOCOL, "operation": operation,
                                    "expected_revision": current["revision"]})
    print(json.dumps({"status": "pausing" if operation == "pause" else "cancelling", "progression": result}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    start_parser = subcommands.add_parser("start", help="start one confirmed foreground run")
    start_parser.add_argument("confirmed_input", type=Path)
    resume_parser = subcommands.add_parser("resume", help="resume a saved needs_input checkpoint")
    resume_parser.add_argument("run_record", type=Path)
    resume_parser.add_argument("user_answer", nargs="?")
    resume_parser.add_argument("--decision-id")
    resume_parser.add_argument("--registry-input", type=Path, help="current controller input containing registry evidence")
    recovery_parser = subcommands.add_parser("recover-business", help="record an explicit original Controller recovery decision without launching a carrier")
    recovery_parser.add_argument("run_record", type=Path)
    recovery_parser.add_argument("decision_input", type=Path)
    for operation in ("pause", "cancel"):
        command = subcommands.add_parser(operation, help="request a foreground checkpoint pause or cancellation")
        command.add_argument("run_record", type=Path)
    args = parser.parse_args(argv)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def interrupt_on_sigterm(signum: int, frame: object) -> None:
        del signum, frame
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt_on_sigterm)
    try:
        if args.command == "recover-business":
            return recover_business(args.run_record, args.decision_input)
        if args.command in {"pause", "cancel"}:
            return request_control(args.run_record, args.command)
        return start(args.confirmed_input) if args.command == "start" else resume(args.run_record, args.user_answer, args.registry_input, args.decision_id)
    except (WorkflowError, entry_prepare.PreparationError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "error": "runner interrupted"}), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
