#!/usr/bin/env python3
"""Read one Codex thread's latest model settings without exposing messages."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path


_CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
SESSIONS_ROOT = Path(
    os.environ.get("CODEX_SESSIONS_ROOT", _CODEX_HOME / "sessions")
)
THREAD_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
YEAR_RE = re.compile(r"^[0-9]{4}$")
MONTH_RE = re.compile(r"^(?:0[1-9]|1[0-2])$")
DAY_RE = re.compile(r"^(?:0[1-9]|[12][0-9]|3[01])$")


class SettingsError(RuntimeError):
    pass


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


def _matching_rollout(thread_id: str, root: Path) -> tuple[int, str]:
    filename_re = re.compile(
        rf"^rollout-[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T"
        rf"[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}-{re.escape(thread_id)}\.jsonl$"
    )
    root_fd = _open_root(root)
    match: tuple[int, str] | None = None
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
                                    if not filename_re.fullmatch(name):
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
                                    if match is not None:
                                        os.close(file_fd)
                                        os.close(match[0])
                                        raise SettingsError(
                                            "multiple rollouts match the frozen thread id"
                                        )
                                    match = (
                                        file_fd,
                                        str(root / year / month / day / name),
                                    )
                            finally:
                                os.close(day_fd)
                    finally:
                        os.close(month_fd)
            finally:
                os.close(year_fd)
    finally:
        os.close(root_fd)
    if match is None:
        raise SettingsError("no rollout matches the frozen thread id")
    return match


def resolve_thread_settings(thread_id: str, root: Path = SESSIONS_ROOT) -> dict[str, str]:
    if not THREAD_ID_RE.fullmatch(thread_id):
        raise SettingsError("thread id is not a canonical lowercase UUID")
    file_fd, _ = _matching_rollout(thread_id, root)
    session_ids: set[str] = set()
    latest: dict[str, str] | None = None
    try:
        with os.fdopen(os.dup(file_fd), "r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    if not line.endswith("\n"):
                        continue
                    raise SettingsError("rollout contains malformed JSONL") from exc
                record_type = record.get("type")
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                if record_type == "session_meta":
                    session_id = payload.get("id")
                    if isinstance(session_id, str):
                        session_ids.add(session_id)
                elif record_type == "turn_context":
                    model = payload.get("model")
                    effort = payload.get("effort")
                    turn_id = payload.get("turn_id")
                    if all(isinstance(value, str) and value for value in (model, effort, turn_id)):
                        latest = {
                            "model": model,
                            "effort": effort,
                            "turn_id": turn_id,
                        }
    finally:
        os.close(file_fd)
    if session_ids != {thread_id}:
        raise SettingsError("rollout session_meta does not match the frozen thread id")
    if latest is None:
        raise SettingsError("rollout has no complete turn_context settings")
    return {
        "schema_version": "1",
        "source": "original-task-latest-turn-context",
        "thread_id": thread_id,
        **latest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread-id", required=True)
    args = parser.parse_args()
    try:
        result = resolve_thread_settings(args.thread_id)
    except (OSError, SettingsError) as exc:
        print(f"thread settings unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
