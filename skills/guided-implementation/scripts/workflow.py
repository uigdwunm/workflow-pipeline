#!/usr/bin/env python3
"""Foreground, checkpointed execution of the approved Stage 2 -> 3 -> 4 flow.

The runner deliberately has no scheduler or background recovery.  A run record is
the only durable state and it must be stored outside the Flow Worktree, because
Stage 4 is allowed to remove that worktree. Fresh ``codex exec`` calls retain the
user's configured approval and sandbox policy; the runner never overrides it.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any


STAGES = ("stage2", "stage3", "stage4")
SCHEMA_PATH = Path(__file__).with_name("workflow_stage_result.schema.json").resolve()


class WorkflowError(RuntimeError):
    """A user-correctable input or runtime problem."""


class UncertainExecutorError(WorkflowError):
    """The runner could not prove that a launched process stopped."""


class RecoverableStageError(WorkflowError):
    """A known session stopped at a stage-reported technical checkpoint."""


class RunLock:
    def __init__(self, record_path: Path) -> None:
        self.path = record_path.with_name(record_path.name + ".lock")
        self.stream: Any | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.stream.close()
            raise WorkflowError("run_busy: another start or resume owns this run") from exc
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


def validate_confirmed(raw: dict[str, Any]) -> dict[str, Any]:
    required = {
        "frozen_requirement", "repository", "worktree", "git_common_dir",
        "target_branch", "authority_scope", "run_record", "stages",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise WorkflowError("confirmed input missing: " + ", ".join(missing))
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
    record_path = _absolute_path(raw["run_record"], "run_record")
    if _is_within(record_path, worktree) or _is_within(record_path, repository):
        raise WorkflowError("run_record must live outside worktree and repository")
    if not isinstance(raw["target_branch"], str) or not raw["target_branch"]:
        raise WorkflowError("target_branch must be a non-empty string")
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
        normalized_stages[stage] = {"model": model, "reasoning_effort": effort}
    return {
        "frozen_requirement": {"path": str(requirement_path), "commit": commit, "sha256": digest},
        "repository": str(repository), "worktree": str(worktree),
        "git_common_dir": str(git_common_dir), "target_branch": raw["target_branch"],
        "authority_scope": authority, "run_record": str(record_path), "stages": normalized_stages,
    }


def _atomic_save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _new_state(confirmed: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "confirmed": confirmed,
        "status": "active",
        "current_stage": "stage2",
        "sessions": {},
        "stage_results": {},
        "launch": {"stage": "stage2", "state": "prelaunch", "turn": 0},
        "history": [],
    }


def _validate_record(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("version") != 1:
        raise WorkflowError("unsupported run record version")
    confirmed = validate_confirmed(state.get("confirmed") if isinstance(state.get("confirmed"), dict) else {})
    if state.get("status") not in {"active", "needs_input", "completed", "failed", "interrupted"}:
        raise WorkflowError("invalid run record status")
    if state.get("current_stage") not in STAGES:
        raise WorkflowError("invalid run record current_stage")
    if not isinstance(state.get("sessions"), dict) or not isinstance(state.get("stage_results"), dict):
        raise WorkflowError("invalid run record state")
    for stage in ("stage2", "stage3"):
        result = state["stage_results"].get(stage)
        if result is not None:
            if not isinstance(result, dict) or result.get("result") != "completed":
                raise WorkflowError(f"invalid {stage} result in run record")
            _require_handoff(stage, result)
            _validate_handoff_continuity(
                stage, result, confirmed, state["stage_results"].get("stage2")
            )
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
            "You are the explicit CLI Stage-2 carrier, not a native child. Follow the exact "
            "solution-design Skill path below. Use the native collaboration child only where that Skill "
            "requires its solution_designer role; do not claim that the CLI carrier itself is that native role. "
            "The requested worktree is not yet a binding: create it once with the existing start-worktree "
            "protocol and return the actual binding in your completed handoff."
        ),
        "stage3": (
            "You are the Stage-3 Originating Task / stage carrier, not the implementation executor. Follow the "
            "exact guided-implementation Skill and retain its internal dedicated implementation executor plus "
            "independent Standards and Spec review roles."
        ),
        "stage4": "You are the Stage-4 executor. Follow the exact change-closure Skill and its retained-worktree protocol.",
    }[stage]
    payload = {
        "stage": stage,
        "frozen_requirement": confirmed["frozen_requirement"],
        "repository": confirmed["repository"],
        "worktree": confirmed["worktree"],
        "git_common_dir": confirmed["git_common_dir"],
        "target_branch": confirmed["target_branch"],
        "authority_scope": confirmed["authority_scope"],
        "settings": settings,
        "flow_mode": "continuous_stage2_to_4",
        "prior_stage_results": prior,
        "user_answer": answer,
        "continuing_same_session": continuing,
        "skill_paths": {
            "stage2": str(Path(__file__).parents[2] / "solution-design" / "SKILL.md"),
            "stage3": str(Path(__file__).parents[1] / "SKILL.md"),
            "stage4": str(Path(__file__).parents[2] / "change-closure" / "SKILL.md"),
        },
    }
    return (
        f"Stage {stage[-1]} foreground workflow execution. {role}\n"
        "The root conversation owns user decisions. Stay within the supplied frozen requirement and authority scope. "
        "This payload is the already-authorized continuous Stage 2→3→4 flow; do not add a stage-entry confirmation. "
        "Do not start a daemon, scheduler, monitor, project, or discussion ledger. Treat a CLI exit as only a turn result.\n"
        "At the end, write exactly one JSON result matching the supplied output schema. Every schema field is required. "
        "completed requires useful artifacts/evidence and uses handoff_json for a JSON-encoded object (Stage 2 and 3 "
        "must carry their full handoff there); use empty question/message strings when inapplicable. needs_input uses "
        "empty artifacts/evidence, handoff_json '{}', and its exact question. needs_input_kind must be user_decision "
        "only for a genuine user decision; report technical failure with needs_input_kind technical_error and the exact "
        "issue, which stops the runner without asking for authorization. completed and continue use needs_input_kind none. "
        "continue means remaining work must continue in this same session.\n"
        + json.dumps(payload, sort_keys=True)
    )


def _event_session(line: str) -> str | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("type") != "thread.started":
        return None
    session_id = event.get("thread_id")
    return session_id if isinstance(session_id, str) and session_id else None


def _event_type(line: str) -> str | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    event_type = event.get("type") if isinstance(event, dict) else None
    return event_type if isinstance(event_type, str) else None


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    def alive() -> bool:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        return True

    def wait_for_group_exit(seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        return not alive()

    if not alive():
        process.wait()
        return
    cause: OSError | None = None
    try:
        os.killpg(process.pid, signal.SIGTERM)
        terminated = wait_for_group_exit(5)
    except OSError as exc:
        cause = exc
        terminated = False
    if not terminated:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            terminated = wait_for_group_exit(5)
        except OSError as cleanup_error:
            cause = cleanup_error
            terminated = False
    if not terminated:
        raise UncertainExecutorError(
            "executor cleanup could not prove process-group termination"
        ) from cause
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise UncertainExecutorError("executor leader could not be reaped") from exc


def _run_process(command: list[str], cwd: Path, on_line: Any, diagnostic_path: Path) -> None:
    try:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", start_new_session=True,
        )
    except OSError as exc:
        raise WorkflowError(f"executor could not start: {exc}") from exc
    assert process.stdout is not None
    tail = bytearray()
    try:
        for line in process.stdout:
            tail.extend(line.encode("utf-8", errors="replace"))
            if len(tail) > 65536:
                del tail[:-65536]
            on_line(line)
        returncode = process.wait()
        if returncode:
            raise WorkflowError(f"executor exited with status {returncode}; diagnostics: {diagnostic_path}")
    except BaseException:
        try:
            _terminate_process_group(process)
        except UncertainExecutorError:
            raise
        raise
    finally:
        process.stdout.close()
        diagnostic_path.write_bytes(tail)


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
        if result["needs_input_kind"] == "technical_error":
            raise RecoverableStageError("stage reported technical error: " + result["question"])
        if result["needs_input_kind"] != "user_decision":
            raise WorkflowError("needs_input stage result must identify a user decision")
    else:
        if not isinstance(result.get("message"), str) or not result["message"]:
            raise WorkflowError("continue stage result requires message")
        if result["needs_input_kind"] != "none":
            raise WorkflowError("continue stage result must use needs_input_kind none")
    _require_handoff(stage, result) if status == "completed" else None
    return result


def _require_handoff(stage: str, result: dict[str, Any]) -> None:
    if stage == "stage4":
        return
    handoff = result.get("handoff")
    if not isinstance(handoff, dict):
        raise WorkflowError(f"{stage} completed result requires a structured handoff")
    if stage == "stage2":
        required = {"binding", "planning_commit", "allowed_paths", "protected_paths"}
    else:
        required = {"binding", "candidate_commit", "review", "verification"}
    if not required <= set(handoff):
        raise WorkflowError(f"{stage} handoff missing: " + ", ".join(sorted(required - set(handoff))))
    if not isinstance(handoff["binding"], dict) or not handoff["binding"]:
        raise WorkflowError(f"{stage} handoff.binding must be an object")
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
    else:
        if not _is_git_sha(handoff["candidate_commit"]):
            raise WorkflowError("stage3 handoff.candidate_commit must be a string")
        if not isinstance(handoff["review"], dict) or not handoff["review"]:
            raise WorkflowError("stage3 handoff.review must be an object")
        if not isinstance(handoff["verification"], (dict, list)) or not handoff["verification"]:
            raise WorkflowError("stage3 handoff.verification must be useful evidence")


def _validate_handoff_continuity(
    stage: str,
    result: dict[str, Any],
    confirmed: dict[str, Any],
    stage2_result: dict[str, Any] | None,
) -> None:
    binding = result["handoff"]["binding"]
    immutable_fields = ("repository", "worktree", "git_common_dir", "target_branch")
    for field in immutable_fields:
        if binding[field] != confirmed[field]:
            raise WorkflowError(f"{stage} handoff.binding.{field} differs from confirmed input")
    if stage == "stage3":
        if not isinstance(stage2_result, dict):
            raise WorkflowError("stage3 handoff requires the prior stage2 handoff")
        prior_binding = stage2_result.get("handoff", {}).get("binding")
        if not isinstance(prior_binding, dict):
            raise WorkflowError("stage3 handoff requires a valid prior stage2 binding")
        binding_fields = ("base_commit", "branch", "git_common_dir", "repository", "target_branch", "worktree")
        if any(binding[field] != prior_binding.get(field) for field in binding_fields):
            raise WorkflowError("stage3 handoff.binding differs from the stage2 binding")


def _is_git_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _artifact_path(record_path: Path, stage: str, turn: int) -> Path:
    return record_path.parent / f"{record_path.name}.{stage}.turn-{turn}.json"


def _mark_failure(
    state: dict[str, Any], record_path: Path, code: str, detail: str, uncertain: bool,
    recoverable: bool = False,
) -> None:
    state["status"] = "interrupted" if uncertain else "failed"
    state["launch"]["state"] = "uncertain" if uncertain else "failed"
    state["error"] = {"code": code, "detail": detail, "recoverable": recoverable and not uncertain}
    state["history"].append({"event": "failure", "stage": state["current_stage"], "code": code})
    _atomic_save(record_path, state)


def _invoke(state: dict[str, Any], record_path: Path, answer: str | None, continuing: bool) -> dict[str, Any]:
    stage = state["current_stage"]
    confirmed = state["confirmed"]
    turn = int(state["launch"].get("turn", 0)) + 1
    output = _artifact_path(record_path, stage, turn)
    diagnostic = output.with_suffix(".events.jsonl")
    state["launch"] = {"stage": stage, "state": "spawning", "turn": turn, "output": str(output), "diagnostic": str(diagnostic)}
    _atomic_save(record_path, state)
    prompt = _stage_prompt(state, stage, answer, continuing)
    codex = os.environ.get("CODEX_BIN", "codex")
    session = state["sessions"].get(stage)
    if session:
        settings = confirmed["stages"][stage]
        command = [
            codex, "exec", "resume", "--json", "--output-schema", str(SCHEMA_PATH), "-o", str(output),
            "--model", settings["model"], "-c", f"model_reasoning_effort={settings['reasoning_effort']}", session, prompt,
        ]
    else:
        settings = confirmed["stages"][stage]
        command = [
            codex, "exec", "--json", "--output-schema", str(SCHEMA_PATH), "-o", str(output),
            "--cd", confirmed["repository"],
            "--add-dir", confirmed["worktree"], "--add-dir", confirmed["git_common_dir"],
            "--model", settings["model"], "-c", f"model_reasoning_effort={settings['reasoning_effort']}", prompt,
        ]
    turn_completed = False
    turn_failed = False

    def receive(line: str) -> None:
        nonlocal turn_completed, turn_failed
        started = _event_session(line)
        event_type = _event_type(line)
        turn_completed = turn_completed or event_type == "turn.completed"
        turn_failed = turn_failed or event_type == "turn.failed"
        if started:
            existing = state["sessions"].get(stage)
            if existing and existing != started:
                raise WorkflowError("session identity conflict")
            state["sessions"][stage] = started
            state["launch"]["state"] = "launched"
            state["history"].append({"event": "thread.started", "stage": stage, "session": started})
            _atomic_save(record_path, state)
            print(json.dumps({"event": "thread.started", "stage": stage, "session": started}), flush=True)
        if event_type == "turn.completed":
            print(json.dumps({"event": "turn.completed", "stage": stage}), flush=True)

    try:
        _run_process(command, Path(confirmed["repository"]), receive, diagnostic)
        if turn_failed:
            raise WorkflowError(f"executor emitted turn.failed; diagnostics: {diagnostic}")
        if not turn_completed:
            raise WorkflowError(f"executor did not emit turn.completed; diagnostics: {diagnostic}")
        if not isinstance(state["sessions"].get(stage), str):
            raise WorkflowError(f"executor did not report thread.started; diagnostics: {diagnostic}")
        result = _read_stage_result(output, stage)
    except KeyboardInterrupt:
        _mark_failure(state, record_path, "interrupted", "runner interrupted", stage not in state["sessions"])
        raise WorkflowError("runner interrupted")
    except WorkflowError as exc:
        uncertain = stage not in state["sessions"] or isinstance(exc, UncertainExecutorError)
        detail = str(exc)
        code = "executor_error"
        if isinstance(exc, UncertainExecutorError):
            code = "interrupted"
            detail = f"{detail}; diagnostics: {diagnostic}"
        _mark_failure(
            state, record_path, code, detail, uncertain,
            recoverable=isinstance(exc, RecoverableStageError),
        )
        raise
    state["launch"]["state"] = "completed_turn"
    state["history"].append({"event": "turn.completed", "stage": stage, "turn": turn})
    _atomic_save(record_path, state)
    return result


def _advance(state: dict[str, Any], record_path: Path, answer: str | None = None) -> int:
    resume_answer = answer
    while True:
        stage = state["current_stage"]
        result = _invoke(state, record_path, resume_answer, stage in state["sessions"])
        resume_answer = None
        if result["result"] == "continue":
            state["status"] = "active"
            state["history"].append({"event": "continue", "stage": stage})
            _atomic_save(record_path, state)
            continue
        if result["result"] == "needs_input":
            state["status"] = "needs_input"
            state["pending_input"] = {"stage": stage, "question": result["question"]}
            state["history"].append({"event": "needs_input", "stage": stage})
            _atomic_save(record_path, state)
            print(json.dumps({"status": "needs_input", "run_record": str(record_path), "question": result["question"]}))
            return 0
        if stage != "stage4":
            try:
                _validate_handoff_continuity(
                    stage, result, state["confirmed"], state["stage_results"].get("stage2")
                )
            except WorkflowError as exc:
                _mark_failure(state, record_path, "handoff_validation_error", str(exc), False)
                raise
        state["stage_results"][stage] = result
        print(json.dumps({"event": "stage.completed", "stage": stage}), flush=True)
        index = STAGES.index(stage)
        if index == len(STAGES) - 1:
            state["status"] = "completed"
            state["history"].append({"event": "completed", "stage": stage})
            _atomic_save(record_path, state)
            print(json.dumps({"status": "completed", "run_record": str(record_path), "artifacts": state["stage_results"]}))
            return 0
        state["current_stage"] = STAGES[index + 1]
        state["status"] = "active"
        state.pop("pending_input", None)
        state["launch"] = {"stage": state["current_stage"], "state": "prelaunch", "turn": 0}
        state["history"].append({"event": "checkpoint", "next_stage": state["current_stage"]})
        _atomic_save(record_path, state)
        print(json.dumps({"event": "checkpoint", "next_stage": state["current_stage"]}), flush=True)


def start(confirmed_path: Path) -> int:
    confirmed = validate_confirmed(_read_json(confirmed_path, "confirmed input"))
    record_path = Path(confirmed["run_record"])
    if record_path.exists():
        raise WorkflowError("run record already exists; use resume")
    with RunLock(record_path):
        if record_path.exists():
            raise WorkflowError("run record already exists; use resume")
        state = _new_state(confirmed)
        _atomic_save(record_path, state)
        return _advance(state, record_path)


def resume(record_path: Path, answer: str) -> int:
    if not answer.strip():
        raise WorkflowError("user_answer must not be empty")
    record_path = _absolute_path(str(record_path), "run_record")
    state = _validate_record(_read_json(record_path, "run record"))
    with RunLock(record_path):
        state = _validate_record(_read_json(record_path, "run record"))
        status = state["status"]
        stage = state["current_stage"]
        safe_between_stages = status == "active" and state.get("launch", {}).get("state") == "prelaunch" and stage not in state["sessions"]
        if status == "needs_input":
            state["status"] = "active"
            state.pop("pending_input", None)
            _atomic_save(record_path, state)
            return _advance(state, record_path, answer)
        if safe_between_stages:
            return _advance(state, record_path, answer)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    start_parser = subcommands.add_parser("start", help="start one confirmed foreground run")
    start_parser.add_argument("confirmed_input", type=Path)
    resume_parser = subcommands.add_parser("resume", help="resume a saved needs_input checkpoint")
    resume_parser.add_argument("run_record", type=Path)
    resume_parser.add_argument("user_answer")
    args = parser.parse_args(argv)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def interrupt_on_sigterm(signum: int, frame: object) -> None:
        del signum, frame
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt_on_sigterm)
    try:
        return start(args.confirmed_input) if args.command == "start" else resume(args.run_record, args.user_answer)
    except WorkflowError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "error": "runner interrupted"}), file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
