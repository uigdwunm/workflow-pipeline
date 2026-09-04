#!/usr/bin/env python3
"""Resolve and verify one Codex task's latest model settings."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Mapping, NamedTuple


PROTOCOL_VERSION = "thread-settings-v4"
_CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
SESSIONS_ROOT = Path(
    os.environ.get("CODEX_SESSIONS_ROOT", _CODEX_HOME / "sessions")
)
THREAD_ID_PATTERN = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
THREAD_ID_RE = re.compile(rf"^{THREAD_ID_PATTERN}$")
YEAR_RE = re.compile(r"^[0-9]{4}$")
MONTH_RE = re.compile(r"^(?:0[1-9]|1[0-2])$")
DAY_RE = re.compile(r"^(?:0[1-9]|[12][0-9]|3[01])$")


class SettingsError(RuntimeError):
    pass


class RolloutFacts(NamedTuple):
    thread_id: str
    session_lineage_id: str | None
    source_kind: str | None
    parent_thread_id: str | None
    model: str
    reasoning_effort: str
    turn_id: str


class RolloutCandidate(NamedTuple):
    file_fd: int
    segment_id: str | None


class ParsedRollout(NamedTuple):
    lineage_state: tuple[str, str | None]
    source_state: tuple[str | None, str | None]
    history_mode: str | None
    history_base_id: str | None
    latest: dict[str, str] | None


def _open_bound_directory(parent_fd: int, name: str) -> int:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise SettingsError(f"unsafe directory component: {name}")
    fd = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    after = os.fstat(fd)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd)
        raise SettingsError(f"directory identity changed: {name}")
    return fd


def _open_root(root: Path) -> int:
    if not root.is_absolute():
        raise SettingsError("sessions root must be absolute")
    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            next_fd = _open_bound_directory(current_fd, part)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _matching_rollouts(thread_id: str, root: Path) -> list[RolloutCandidate]:
    filename_re = re.compile(
        rf"^rollout-[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T"
        rf"[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}-{re.escape(thread_id)}"
        rf"(?:_(?P<segment_id>{THREAD_ID_PATTERN}))?\.jsonl$"
    )
    root_fd = _open_root(root)
    matches: list[RolloutCandidate] = []
    try:
        for year in os.listdir(root_fd):
            if not YEAR_RE.fullmatch(year):
                continue
            year_fd = _open_bound_directory(root_fd, year)
            try:
                for month in os.listdir(year_fd):
                    if not MONTH_RE.fullmatch(month):
                        continue
                    month_fd = _open_bound_directory(year_fd, month)
                    try:
                        for day in os.listdir(month_fd):
                            if not DAY_RE.fullmatch(day):
                                continue
                            day_fd = _open_bound_directory(month_fd, day)
                            try:
                                for name in os.listdir(day_fd):
                                    filename_match = filename_re.fullmatch(name)
                                    if filename_match is None:
                                        continue
                                    before = os.stat(
                                        name, dir_fd=day_fd, follow_symlinks=False
                                    )
                                    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(
                                        before.st_mode
                                    ):
                                        raise SettingsError("rollout is not a regular file")
                                    file_fd = os.open(
                                        name,
                                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                        dir_fd=day_fd,
                                    )
                                    after = os.fstat(file_fd)
                                    if (before.st_dev, before.st_ino) != (
                                        after.st_dev,
                                        after.st_ino,
                                    ):
                                        os.close(file_fd)
                                        raise SettingsError("rollout identity changed")
                                    matches.append(
                                        RolloutCandidate(
                                            file_fd=file_fd,
                                            segment_id=filename_match.group(
                                                "segment_id"
                                            ),
                                        )
                                    )
                            finally:
                                os.close(day_fd)
                    finally:
                        os.close(month_fd)
            finally:
                os.close(year_fd)
    except Exception:
        for candidate in matches:
            os.close(candidate.file_fd)
        raise
    finally:
        os.close(root_fd)
    if not matches:
        raise SettingsError("no rollout matches the frozen thread id")
    return matches


def _runtime_thread_id(environ: Mapping[str, str]) -> str:
    thread_id = environ.get("CODEX_THREAD_ID")
    if not thread_id:
        raise SettingsError("current thread identity is unavailable")
    return thread_id


def _source_identity(source: object) -> tuple[str, str | None]:
    if isinstance(source, str) and source:
        return "root", None
    if not isinstance(source, dict):
        return "unsupported", None
    subagent = source.get("subagent")
    if subagent is None:
        return "unsupported", None
    if not isinstance(subagent, dict):
        return "invalid-subagent", None
    thread_spawn = subagent.get("thread_spawn")
    if not isinstance(thread_spawn, dict):
        return "invalid-subagent", None
    parent_thread_id = thread_spawn.get("parent_thread_id")
    if not isinstance(parent_thread_id, str):
        return "invalid-subagent", None
    return "subagent", parent_thread_id


def _read_rollout(
    candidate: RolloutCandidate,
    thread_id: str,
) -> ParsedRollout:
    session_ids: set[str] = set()
    lineage_states: set[tuple[str, str | None]] = set()
    source_states: set[tuple[str | None, str | None]] = set()
    history_mode_states: set[str | None] = set()
    history_base_states: set[tuple[str, str | None]] = set()
    latest: dict[str, str] | None = None
    with os.fdopen(os.dup(candidate.file_fd), "r", encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                if not line.endswith("\n"):
                    continue
                raise SettingsError("rollout contains malformed JSONL") from exc
            if not isinstance(record, dict):
                continue
            record_type = record.get("type")
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            if record_type == "session_meta":
                session_id = payload.get("id")
                if not isinstance(session_id, str):
                    raise SettingsError("rollout session_meta has invalid thread id")
                session_ids.add(session_id)
                if "session_id" not in payload:
                    lineage_states.add(("missing", None))
                else:
                    session_lineage_id = payload.get("session_id")
                    if not isinstance(session_lineage_id, str):
                        lineage_states.add(("invalid", None))
                    else:
                        lineage_states.add(("value", session_lineage_id))
                if "source" not in payload:
                    source_states.add((None, None))
                else:
                    source_states.add(_source_identity(payload.get("source")))
                history_mode = payload.get("history_mode")
                if history_mode not in (None, "paginated"):
                    raise SettingsError("rollout has invalid history metadata")
                history_mode_states.add(history_mode)
                if history_mode == "paginated":
                    history_base = payload.get("history_base")
                    if history_base is None:
                        history_base_states.add(("root", None))
                    elif isinstance(history_base, dict):
                        history_base_id = history_base.get("thread_id")
                        if not isinstance(
                            history_base_id, str
                        ) or not THREAD_ID_RE.fullmatch(history_base_id):
                            raise SettingsError(
                                "paginated rollout has invalid history metadata"
                            )
                        history_base_states.add(("value", history_base_id))
                    else:
                        raise SettingsError(
                            "paginated rollout has invalid history metadata"
                        )
                elif candidate.segment_id is not None:
                    raise SettingsError(
                        "paginated rollout has invalid history metadata"
                    )
            elif record_type == "turn_context":
                model = payload.get("model")
                effort = payload.get("effort")
                turn_id = payload.get("turn_id")
                if all(
                    isinstance(value, str) and value
                    for value in (model, effort, turn_id)
                ):
                    latest = {
                        "model": model,
                        "reasoning_effort": effort,
                        "turn_id": turn_id,
                    }
    if session_ids != {thread_id}:
        raise SettingsError("rollout session_meta does not match the frozen thread id")
    if len(lineage_states) != 1 or len(source_states) != 1:
        raise SettingsError("rollout session_meta identity fields conflict")
    if len(history_mode_states) != 1:
        raise SettingsError("rollout history metadata conflicts")
    history_mode = next(iter(history_mode_states))
    history_base_id: str | None = None
    if history_mode == "paginated":
        if len(history_base_states) != 1:
            raise SettingsError("paginated rollout history metadata conflicts")
        _, history_base_id = next(iter(history_base_states))
    return ParsedRollout(
        lineage_state=next(iter(lineage_states)),
        source_state=next(iter(source_states)),
        history_mode=history_mode,
        history_base_id=history_base_id,
        latest=latest,
    )


def _latest_paginated_rollout(
    segments: dict[str, ParsedRollout],
) -> ParsedRollout:
    referenced: set[str] = set()
    child_counts: dict[str, int] = {segment_id: 0 for segment_id in segments}
    for parsed in segments.values():
        parent_id = parsed.history_base_id
        if parent_id is None:
            continue
        if parent_id not in segments:
            raise SettingsError("paginated rollout history is incomplete")
        referenced.add(parent_id)
        child_counts[parent_id] += 1
    if any(count > 1 for count in child_counts.values()):
        raise SettingsError("paginated rollout history branches")
    roots = [
        segment_id
        for segment_id, parsed in segments.items()
        if parsed.history_base_id is None
    ]
    leaves = [segment_id for segment_id in segments if segment_id not in referenced]
    if len(roots) != 1 or len(leaves) != 1:
        raise SettingsError("paginated rollout history is ambiguous")

    visited: set[str] = set()
    current_id: str | None = leaves[0]
    latest: ParsedRollout | None = None
    while current_id is not None:
        if current_id in visited:
            raise SettingsError("paginated rollout history is ambiguous")
        visited.add(current_id)
        parsed = segments[current_id]
        if latest is None and parsed.latest is not None:
            latest = parsed
        current_id = parsed.history_base_id
    if visited != set(segments):
        raise SettingsError("paginated rollout history is ambiguous")
    if latest is None:
        raise SettingsError("rollout has no complete turn_context settings")
    return latest


def _read_rollout_facts(thread_id: str, root: Path) -> RolloutFacts:
    if not THREAD_ID_RE.fullmatch(thread_id):
        raise SettingsError("thread id is not a canonical lowercase UUID")
    candidates = _matching_rollouts(thread_id, root)
    canonical_candidates = [
        candidate for candidate in candidates if candidate.segment_id is None
    ]
    segment_candidates = [
        candidate for candidate in candidates if candidate.segment_id is not None
    ]
    if len(canonical_candidates) > 1:
        for candidate in candidates:
            os.close(candidate.file_fd)
        raise SettingsError("multiple rollouts match the frozen thread id")

    parsed_candidates: list[tuple[RolloutCandidate, ParsedRollout]] = []
    try:
        parsed_candidates = [
            (candidate, _read_rollout(candidate, thread_id))
            for candidate in candidates
        ]
    finally:
        for candidate in candidates:
            os.close(candidate.file_fd)

    lineage_states = {parsed.lineage_state for _, parsed in parsed_candidates}
    source_states = {parsed.source_state for _, parsed in parsed_candidates}
    if len(lineage_states) != 1 or len(source_states) != 1:
        raise SettingsError("rollout session_meta identity fields conflict")

    if segment_candidates:
        if len(canonical_candidates) != 1:
            raise SettingsError("paginated rollout canonical root is unavailable")
        segments: dict[str, ParsedRollout] = {}
        for candidate, parsed in parsed_candidates:
            if parsed.history_mode != "paginated":
                raise SettingsError(
                    "paginated rollout canonical root has invalid history metadata"
                )
            node_id = candidate.segment_id or thread_id
            if candidate.segment_id is None and parsed.history_base_id is not None:
                raise SettingsError(
                    "paginated rollout canonical root has invalid history metadata"
                )
            if node_id in segments:
                raise SettingsError("duplicate paginated rollout segment")
            segments[node_id] = parsed
        selected = _latest_paginated_rollout(segments)
        latest = selected.latest
    else:
        if len(canonical_candidates) != 1:
            raise SettingsError("no rollout matches the frozen thread id")
        latest = parsed_candidates[0][1].latest

    if latest is None:
        raise SettingsError("rollout has no complete turn_context settings")
    lineage_state, session_lineage_id = next(iter(lineage_states))
    if lineage_state == "missing":
        session_lineage_id = None
    source_kind, parent_thread_id = next(iter(source_states))
    return RolloutFacts(
        thread_id=thread_id,
        session_lineage_id=session_lineage_id,
        source_kind=source_kind,
        parent_thread_id=parent_thread_id,
        **latest,
    )


def _validate_current_identity(
    facts: RolloutFacts, environ: Mapping[str, str]
) -> None:
    session_lineage_id = facts.session_lineage_id
    if facts.source_kind == "root":
        if (
            session_lineage_id is None
            or not THREAD_ID_RE.fullmatch(session_lineage_id)
            or session_lineage_id != facts.thread_id
            or facts.parent_thread_id is not None
        ):
            raise SettingsError("invalid root task identity shape")
    elif facts.source_kind == "subagent":
        parent_thread_id = facts.parent_thread_id
        if (
            session_lineage_id is None
            or not THREAD_ID_RE.fullmatch(session_lineage_id)
            or parent_thread_id is None
            or not THREAD_ID_RE.fullmatch(parent_thread_id)
            or parent_thread_id == facts.thread_id
            or session_lineage_id == facts.thread_id
        ):
            raise SettingsError("invalid subagent task identity shape")
    elif facts.source_kind == "invalid-subagent":
        raise SettingsError("invalid subagent task identity shape")
    else:
        raise SettingsError("unsupported runtime task identity shape")
    runtime_session_id = environ.get("CODEX_SESSION_ID")
    if runtime_session_id is not None and runtime_session_id != session_lineage_id:
        raise SettingsError("runtime session lineage conflict")


def _public_receipt(facts: RolloutFacts) -> dict[str, str]:
    return {
        "protocol": PROTOCOL_VERSION,
        "source": "codex-rollout-latest-turn-context",
        "thread_id": facts.thread_id,
        "model": facts.model,
        "reasoning_effort": facts.reasoning_effort,
        "turn_id": facts.turn_id,
    }


def resolve_thread_settings(
    thread_id: str, root: Path = SESSIONS_ROOT
) -> dict[str, str]:
    return _public_receipt(_read_rollout_facts(thread_id, root))


def resolve_current_thread_settings(
    root: Path = SESSIONS_ROOT,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    runtime_environment = os.environ if environ is None else environ
    facts = _read_rollout_facts(_runtime_thread_id(runtime_environment), root)
    _validate_current_identity(facts, runtime_environment)
    return _public_receipt(facts)


def _verification_receipt(
    observed: dict[str, str],
    *,
    expected_model: str,
    expected_reasoning_effort: str,
) -> dict[str, object]:
    matches = (
        observed["model"] == expected_model
        and observed["reasoning_effort"] == expected_reasoning_effort
    )
    return {
        "protocol": PROTOCOL_VERSION,
        "operation": "verify",
        "status": "match" if matches else "changed",
        "thread_id": observed["thread_id"],
        "expected": {
            "model": expected_model,
            "reasoning_effort": expected_reasoning_effort,
        },
        "observed": observed,
    }


def verify_thread_settings(
    thread_id: str,
    *,
    expected_model: str,
    expected_reasoning_effort: str,
    root: Path = SESSIONS_ROOT,
) -> dict[str, object]:
    if not expected_model or not expected_reasoning_effort:
        raise SettingsError("expected model and reasoning effort must be non-empty")
    observed = resolve_thread_settings(thread_id, root)
    return _verification_receipt(
        observed,
        expected_model=expected_model,
        expected_reasoning_effort=expected_reasoning_effort,
    )


def verify_current_thread_settings(
    *,
    expected_model: str,
    expected_reasoning_effort: str,
    root: Path = SESSIONS_ROOT,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if not expected_model or not expected_reasoning_effort:
        raise SettingsError("expected model and reasoning effort must be non-empty")
    runtime_environment = os.environ if environ is None else environ
    observed = resolve_current_thread_settings(root, environ=runtime_environment)
    return _verification_receipt(
        observed,
        expected_model=expected_model,
        expected_reasoning_effort=expected_reasoning_effort,
    )


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--thread-id")
    identity.add_argument("--current", action="store_true")


def _resolve_from_args(args: argparse.Namespace) -> dict[str, str]:
    if args.current:
        return resolve_current_thread_settings()
    return resolve_thread_settings(args.thread_id)


def _verify_from_args(args: argparse.Namespace) -> dict[str, object]:
    if args.current:
        return verify_current_thread_settings(
            expected_model=args.model,
            expected_reasoning_effort=args.reasoning_effort,
        )
    return verify_thread_settings(
        args.thread_id,
        expected_model=args.model,
        expected_reasoning_effort=args.reasoning_effort,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve")
    _add_identity_arguments(resolve_parser)

    verify_parser = subparsers.add_parser("verify")
    _add_identity_arguments(verify_parser)
    verify_parser.add_argument("--model", required=True)
    verify_parser.add_argument("--reasoning-effort", required=True)

    subparsers.add_parser("protocol-version")
    args = parser.parse_args()

    if args.command == "protocol-version":
        print(PROTOCOL_VERSION)
        return 0
    try:
        result = (
            _resolve_from_args(args)
            if args.command == "resolve"
            else _verify_from_args(args)
        )
    except (OSError, UnicodeError, SettingsError) as exc:
        print(f"thread settings unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.command == "verify" and result["status"] == "changed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
