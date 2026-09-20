#!/usr/bin/env python3

"""Create, verify, integrate, and clean one isolated Git worktree."""

from __future__ import annotations

import argparse
import copy
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import time
from typing import Any

from supervision_core import CommandRegistry, CommandSpec


MAX_INPUT_BYTES = 1_048_576
OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
PUBLICATION_LOCK_FILENAME = "cc-switch-worktree-publication.lock"
PUBLICATION_LOCK_TIMEOUT_SECONDS = 5.0


class ProtocolError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context or {}


def _error_response(error: ProtocolError, command: str) -> dict[str, Any]:
    return {
        "command": command,
        "error": {
            "code": error.code,
            "context": error.context,
            "message": error.message,
        },
        "ok": False,
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("invalid_input", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_input(data: bytes) -> dict[str, Any]:
    if not data or len(data) > MAX_INPUT_BYTES:
        raise ProtocolError(
            "invalid_input",
            f"input must contain 1..{MAX_INPUT_BYTES} bytes",
        )
    try:
        value = json.loads(
            data,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=lambda _: (_ for _ in ()).throw(
                ProtocolError("invalid_input", "floating-point JSON values are unsupported")
            ),
            parse_constant=lambda _: (_ for _ in ()).throw(
                ProtocolError("invalid_input", "non-finite JSON values are unsupported")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid_input", "input must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ProtocolError("invalid_input", "input must be one JSON object")
    return value


def _expect_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    observed = set(value)
    if observed != keys:
        raise ProtocolError(
            "invalid_input",
            f"{label} fields mismatch; expected={sorted(keys)!r}; observed={sorted(observed)!r}",
        )


def _expect_string(value: Any, label: str, *, max_bytes: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > max_bytes:
        raise ProtocolError("invalid_input", f"{label} must be a non-empty bounded string")
    return value


def _expect_oid(value: Any, label: str) -> str:
    text = _expect_string(value, label, max_bytes=64)
    if OID_RE.fullmatch(text) is None:
        raise ProtocolError("invalid_input", f"{label} must be a full Git object ID")
    return text


def _canonical_absolute_path(
    value: Any,
    label: str,
    *,
    must_exist: bool,
) -> Path:
    raw = Path(_expect_string(value, label, max_bytes=8192))
    if not raw.is_absolute():
        raise ProtocolError("invalid_input", f"{label} must be absolute")
    try:
        resolved = raw.resolve(strict=must_exist)
    except OSError as error:
        raise ProtocolError("invalid_input", f"{label} cannot be resolved: {raw}") from error
    if raw != resolved:
        raise ProtocolError("invalid_input", f"{label} must be canonical: {raw}")
    return raw


def _normalize_relative_paths(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ProtocolError("invalid_input", f"{label} must be a list")
    normalized: list[str] = []
    for index, item in enumerate(value):
        text = _expect_string(item, f"{label}[{index}]", max_bytes=8192)
        path = Path(text)
        if (
            path.is_absolute()
            or path == Path(".")
            or ".." in path.parts
            or path.as_posix() != text
        ):
            raise ProtocolError(
                "invalid_input",
                f"{label}[{index}] must be a normalized repository-relative path",
            )
        normalized.append(text)
    if normalized != sorted(set(normalized)):
        raise ProtocolError("invalid_input", f"{label} must be sorted and unique")
    return normalized


def _run_git(
    repository: Path,
    arguments: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Git command failed"
        raise ProtocolError("git_failed", message)
    return completed


def _git_text(repository: Path, arguments: list[str]) -> str:
    return _run_git(repository, arguments).stdout.strip()


def _canonical_repository(value: Any) -> tuple[Path, Path]:
    repository = _canonical_absolute_path(value, "repository", must_exist=True)
    top = Path(
        _git_text(repository, ["rev-parse", "--path-format=absolute", "--show-toplevel"])
    )
    if top != repository or not (repository / ".git").is_dir():
        raise ProtocolError(
            "invalid_repository",
            "repository must be the canonical primary Git checkout",
        )
    common = Path(
        _git_text(repository, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    )
    if common != repository / ".git":
        raise ProtocolError("invalid_repository", "repository Git common directory is unexpected")
    return repository, common


def _current_branch(repository: Path) -> str:
    completed = _run_git(repository, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if completed.returncode != 0:
        raise ProtocolError("detached_checkout", "checkout must be on a named branch")
    return completed.stdout.strip()


def _branch_oid(repository: Path, branch: str) -> str | None:
    completed = _run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        check=False,
    )
    if completed.returncode == 1 and not completed.stdout.strip():
        return None
    if completed.returncode != 0:
        raise ProtocolError("git_failed", completed.stderr.strip() or "cannot inspect branch")
    return _expect_oid(completed.stdout.strip(), f"branch {branch}")


def _published_target_head(
    repository: Path,
    target_branch: str,
    planning_commit: str,
) -> str | None:
    target_head = _branch_oid(repository, target_branch)
    if target_head is not None and _is_ancestor(
        repository,
        planning_commit,
        target_head,
    ):
        return target_head
    return None


def _validate_branch(repository: Path, value: Any, label: str) -> str:
    branch = _expect_string(value, label, max_bytes=1024)
    if _run_git(repository, ["check-ref-format", "--branch", branch], check=False).returncode != 0:
        raise ProtocolError("invalid_input", f"{label} is not a valid Git branch")
    return branch


def _full_status(repository: Path) -> str:
    return _run_git(
        repository,
        ["status", "--porcelain=v1", "--untracked-files=all"],
    ).stdout


def _staged_paths(repository: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--"],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ProtocolError(
            "git_failed",
            completed.stderr.decode("utf-8", errors="replace").strip()
            or "cannot inspect staged paths",
        )
    try:
        return sorted(
            item.decode("utf-8") for item in completed.stdout.split(b"\0") if item
        )
    except UnicodeDecodeError as error:
        raise ProtocolError("git_failed", "staged path is not valid UTF-8") from error


def _ignored_paths(repository: Path, candidate_paths: list[str]) -> list[str]:
    pathspecs: set[str] = set()
    for path in candidate_paths:
        parts = path.split("/")
        pathspecs.update(
            "/".join(parts[:index]) for index in range(1, len(parts) + 1)
        )
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
            "--",
            *sorted(pathspecs),
        ],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ProtocolError(
            "git_failed",
            completed.stderr.decode("utf-8", errors="replace").strip()
            or "cannot inspect ignored paths",
        )
    try:
        return sorted(
            item.decode("utf-8") for item in completed.stdout.split(b"\0") if item
        )
    except UnicodeDecodeError as error:
        raise ProtocolError("git_failed", "ignored path is not valid UTF-8") from error


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    completed = _run_git(
        repository,
        ["merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise ProtocolError("git_failed", completed.stderr.strip() or "cannot compare commits")
    return completed.returncode == 0


def _flock_with_timeout(
    stream: Any,
    timeout_seconds: float = PUBLICATION_LOCK_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            if time.monotonic() >= deadline:
                raise ProtocolError(
                    "publication_busy",
                    "target publication lock did not become available within five seconds",
                ) from error
            time.sleep(0.05)


def _path_matches(path: str, scopes: list[str]) -> bool:
    return any(path == scope or path.startswith(scope + "/") for scope in scopes)


def _paths_overlap(first: str, second: str) -> bool:
    return (
        first == second
        or first.startswith(second + "/")
        or second.startswith(first + "/")
    )


def _ignored_collisions(repository: Path, changed_paths: list[str]) -> list[str]:
    return [
        ignored
        for ignored in _ignored_paths(repository, changed_paths)
        if any(_paths_overlap(ignored, changed) for changed in changed_paths)
    ]


def _changed_paths(repository: Path, older: str, newer: str) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", older, newer, "--"],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise ProtocolError(
            "git_failed",
            completed.stderr.decode("utf-8", errors="replace").strip()
            or "cannot inspect changed paths",
        )
    try:
        return sorted(
            item.decode("utf-8") for item in completed.stdout.split(b"\0") if item
        )
    except UnicodeDecodeError as error:
        raise ProtocolError("git_failed", "changed path is not valid UTF-8") from error


def _binding(value: Any, *, removed: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("invalid_input", "binding must be an object")
    _expect_keys(
        value,
        {
            "base_commit",
            "branch",
            "git_common_dir",
            "repository",
            "target_branch",
            "worktree",
        },
        "binding",
    )
    repository, common = _canonical_repository(value["repository"])
    supplied_common = _canonical_absolute_path(
        value["git_common_dir"], "git_common_dir", must_exist=True
    )
    if supplied_common != common:
        raise ProtocolError("worktree_mismatch", "binding Git common directory changed")
    worktree = _canonical_absolute_path(value["worktree"], "worktree", must_exist=not removed)
    branch = _validate_branch(repository, value["branch"], "branch")
    target_branch = _validate_branch(repository, value["target_branch"], "target_branch")
    if branch == target_branch:
        raise ProtocolError("invalid_input", "worktree and target branches must differ")
    return {
        "base_commit": _expect_oid(value["base_commit"], "base_commit"),
        "branch": branch,
        "git_common_dir": str(common),
        "repository": str(repository),
        "target_branch": target_branch,
        "worktree": str(worktree),
    }


def _verify_binding(binding: dict[str, Any], *, platform_cwd: Path | None = None) -> dict[str, Any]:
    repository = Path(binding["repository"])
    common = Path(binding["git_common_dir"])
    worktree = Path(binding["worktree"])
    if platform_cwd is not None and platform_cwd != worktree:
        raise ProtocolError("worktree_mismatch", "platform working directory does not match binding")
    try:
        observed_top = Path(
            _git_text(worktree, ["rev-parse", "--path-format=absolute", "--show-toplevel"])
        )
        observed_common = Path(
            _git_text(worktree, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
        )
    except ProtocolError as error:
        raise ProtocolError("worktree_mismatch", "bound worktree is not usable") from error
    if observed_top != worktree or observed_common != common:
        raise ProtocolError("worktree_mismatch", "worktree registration does not match binding")
    if _current_branch(worktree) != binding["branch"]:
        raise ProtocolError("worktree_mismatch", "worktree branch does not match binding")
    head = _expect_oid(_git_text(worktree, ["rev-parse", "HEAD"]), "worktree HEAD")
    if _branch_oid(repository, binding["branch"]) != head:
        raise ProtocolError("worktree_mismatch", "worktree branch ref does not match HEAD")
    if not _is_ancestor(repository, binding["base_commit"], head):
        raise ProtocolError("worktree_mismatch", "worktree no longer descends from its base")
    if _branch_oid(repository, binding["target_branch"]) is None:
        raise ProtocolError("target_changed", "target branch no longer exists")
    return {"current_commit": head, "verified": True}


def start_worktree(request: dict[str, Any]) -> dict[str, Any]:
    _expect_keys(
        request,
        {
            "branch",
            "repository",
            "target_branch",
            "worktree",
        },
        "start-worktree input",
    )
    repository, common = _canonical_repository(request["repository"])
    target_branch = _validate_branch(repository, request["target_branch"], "target_branch")
    if _current_branch(repository) != target_branch:
        raise ProtocolError("wrong_target_branch", "primary checkout is not on target_branch")
    base_commit = _branch_oid(repository, target_branch)
    if base_commit is None:
        raise ProtocolError("invalid_input", "target branch does not exist")
    if _expect_oid(_git_text(repository, ["rev-parse", "HEAD"]), "target HEAD") != base_commit:
        raise ProtocolError("target_changed", "primary checkout HEAD and target branch differ")
    branch = _validate_branch(repository, request["branch"], "branch")
    if branch == target_branch:
        raise ProtocolError("invalid_input", "worktree and target branches must differ")
    if _branch_oid(repository, branch) is not None:
        raise ProtocolError("branch_exists", f"branch already exists: {branch}")
    worktree = _canonical_absolute_path(request["worktree"], "worktree", must_exist=False)
    if worktree.exists():
        raise ProtocolError("worktree_exists", f"worktree path already exists: {worktree}")
    binding = {
        "base_commit": base_commit,
        "branch": branch,
        "git_common_dir": str(common),
        "repository": str(repository),
        "target_branch": target_branch,
        "worktree": str(worktree),
    }
    created = _run_git(
        repository,
        ["worktree", "add", "-b", branch, str(worktree), base_commit],
        check=False,
    )
    if created.returncode != 0:
        raise ProtocolError(
            "worktree_create_failed",
            created.stderr.strip() or created.stdout.strip() or "git worktree add failed",
        )
    try:
        _verify_binding(_binding(binding), platform_cwd=worktree)
    except ProtocolError:
        _run_git(repository, ["worktree", "remove", str(worktree)], check=False)
        _run_git(repository, ["branch", "-d", branch], check=False)
        raise
    return {"binding": binding, "ok": True, "state": "created"}


def verify_worktree(request: dict[str, Any]) -> dict[str, Any]:
    _expect_keys(request, {"binding", "platform_cwd"}, "verify-worktree input")
    binding = _binding(request["binding"])
    platform_cwd = _canonical_absolute_path(
        request["platform_cwd"], "platform_cwd", must_exist=True
    )
    report = _verify_binding(binding, platform_cwd=platform_cwd)
    return {"binding": binding, "ok": True, **report}


def _validate_candidate(
    binding: dict[str, Any],
    candidate: str,
    expected_target_head: str,
    scope_base_commit: str,
    allowed_paths: list[str],
    protected_paths: list[str],
) -> list[str]:
    repository = Path(binding["repository"])
    worktree = Path(binding["worktree"])
    report = _verify_binding(binding, platform_cwd=worktree)
    if report["current_commit"] != candidate:
        raise ProtocolError("candidate_changed", "candidate commit is not the worktree HEAD")
    if _full_status(worktree):
        raise ProtocolError(
            "worktree_not_clean",
            "commit every worktree change before integration",
        )
    if candidate == expected_target_head:
        raise ProtocolError("empty_candidate", "candidate contains no change")
    if not _is_ancestor(repository, scope_base_commit, expected_target_head):
        raise ProtocolError("target_changed", "target no longer descends from the scoped base")
    if not _is_ancestor(repository, scope_base_commit, candidate):
        raise ProtocolError("candidate_stale", "candidate no longer descends from the scoped base")
    if not _is_ancestor(repository, expected_target_head, candidate):
        raise ProtocolError(
            "candidate_stale",
            "candidate must integrate the expected target HEAD and be retested",
        )
    for path in protected_paths:
        if _run_git(
            repository,
            ["cat-file", "-e", f"{scope_base_commit}:{path}"],
            check=False,
        ).returncode != 0:
            raise ProtocolError(
                "source_missing",
                f"protected path is not committed at the scoped base: {path}",
            )
    source_changes = [
        path
        for path in _changed_paths(repository, scope_base_commit, expected_target_head)
        if _path_matches(path, protected_paths)
    ]
    if source_changes:
        raise ProtocolError(
            "source_changed",
            "committed implementation sources changed after worktree creation",
            context={"paths": source_changes},
        )
    changed = _changed_paths(repository, expected_target_head, candidate)
    if not changed:
        raise ProtocolError("empty_candidate", "candidate contains no scoped change")
    source_edits = [
        path for path in changed if _path_matches(path, protected_paths)
    ]
    if source_edits:
        raise ProtocolError(
            "source_changed",
            "candidate modifies committed implementation sources",
            context={"paths": source_edits},
        )
    outside = [
        path for path in changed if not _path_matches(path, allowed_paths)
    ]
    if outside:
        raise ProtocolError(
            "path_outside_scope",
            "candidate changes paths outside allowed_paths",
            context={"paths": outside},
        )
    return changed


def _merge_candidate_into_target(
    binding: dict[str, Any],
    candidate: str,
    expected_target_head: str,
) -> str:
    repository = Path(binding["repository"])
    common = Path(binding["git_common_dir"])
    if _current_branch(repository) != binding["target_branch"]:
        raise ProtocolError("wrong_target_branch", "primary checkout changed branches")
    current_target = _branch_oid(repository, binding["target_branch"])
    if current_target != expected_target_head:
        raise ProtocolError(
            "target_changed",
            "target branch changed before publication",
            context={"expected": expected_target_head, "observed": current_target},
        )
    if _expect_oid(_git_text(repository, ["rev-parse", "HEAD"]), "target HEAD") != expected_target_head:
        raise ProtocolError("target_changed", "primary checkout HEAD changed before publication")
    staged_paths = _staged_paths(repository)
    if staged_paths:
        raise ProtocolError(
            "checkout_not_clean",
            "primary checkout has staged changes that could enter the merge",
            context={"paths": staged_paths},
        )
    candidate_paths = _changed_paths(repository, expected_target_head, candidate)
    ignored_collisions = _ignored_collisions(repository, candidate_paths)
    if ignored_collisions:
        raise ProtocolError(
            "checkout_not_clean",
            "primary checkout has ignored files that the candidate could overwrite",
            context={"paths": ignored_collisions},
        )
    original_status = _full_status(repository)
    merged = _run_git(
        repository,
        ["merge", "--no-ff", "--no-edit", candidate],
        check=False,
    )
    if merged.returncode != 0:
        if (common / "MERGE_HEAD").exists():
            _run_git(repository, ["merge", "--abort"], check=False)
        restored = _git_text(repository, ["rev-parse", "HEAD"])
        restored_status = _full_status(repository)
        if (
            restored != expected_target_head
            or (common / "MERGE_HEAD").exists()
            or restored_status != original_status
        ):
            raise ProtocolError(
                "integration_restore_failed",
                "failed merge did not restore the primary checkout",
                context={"observed_target_head": restored},
            )
        raise ProtocolError(
            "integration_failed",
            merged.stderr.strip() or merged.stdout.strip() or "git merge failed",
            context={"restored_target_head": restored},
        )
    merge_commit = _expect_oid(
        _git_text(repository, ["rev-parse", "HEAD"]), "merge commit"
    )
    parents = _git_text(repository, ["show", "-s", "--format=%P", merge_commit]).split()
    if parents != [expected_target_head, candidate]:
        raise ProtocolError(
            "integration_unverified",
            "merge commit parents do not match the accepted target and candidate",
            context={"merge_commit": merge_commit, "parents": parents},
        )
    merge_tree = _expect_oid(
        _git_text(repository, ["rev-parse", f"{merge_commit}^{{tree}}"]),
        "merge tree",
    )
    candidate_tree = _expect_oid(
        _git_text(repository, ["rev-parse", f"{candidate}^{{tree}}"]),
        "candidate tree",
    )
    if merge_tree != candidate_tree or _full_status(repository) != original_status:
        raise ProtocolError(
            "integration_unverified",
            "merge did not preserve the accepted candidate and primary checkout state",
            context={"merge_commit": merge_commit},
        )
    return merge_commit


def _restore_planning_commit(
    worktree: Path,
    planning_commit: str,
    original_error: ProtocolError,
) -> None:
    restored = _run_git(
        worktree,
        ["reset", "--keep", planning_commit],
        check=False,
    )
    observed_head = _git_text(worktree, ["rev-parse", "HEAD"])
    if (
        restored.returncode != 0
        or observed_head != planning_commit
        or _full_status(worktree)
    ):
        raise ProtocolError(
            "integration_restore_failed",
            "failed publication did not restore the accepted planning commit",
            context={
                "observed_head": observed_head,
                "original_error": original_error.code,
            },
        ) from original_error


def _prepare_planning_candidate(
    binding: dict[str, Any],
    planning_commit: str,
    allowed_paths: list[str],
    protected_paths: list[str],
    record=None,
) -> tuple[str, str, list[str]]:
    repository = Path(binding["repository"])
    worktree = Path(binding["worktree"])
    target_head = _branch_oid(repository, binding["target_branch"])
    if target_head is None:
        raise ProtocolError("target_changed", "target branch no longer exists")
    if not _is_ancestor(repository, binding["base_commit"], target_head):
        raise ProtocolError(
            "target_changed",
            "target no longer descends from the flow base",
        )
    for path in protected_paths:
        if _run_git(
            repository,
            ["cat-file", "-e", f"{binding['base_commit']}:{path}"],
            check=False,
        ).returncode != 0:
            raise ProtocolError(
                "source_missing",
                f"protected path is not committed at the flow base: {path}",
            )
    target_changes = _changed_paths(
        repository,
        binding["base_commit"],
        target_head,
    )
    source_changes = [
        path
        for path in target_changes
        if _path_matches(path, protected_paths)
    ]
    if source_changes:
        raise ProtocolError(
            "source_changed",
            "committed planning sources changed after flow creation",
            context={"paths": source_changes},
        )
    if not _is_ancestor(repository, target_head, planning_commit):
        ignored_collisions = _ignored_collisions(worktree, target_changes)
        if ignored_collisions:
            raise ProtocolError(
                "worktree_not_clean",
                "Flow Worktree has ignored files that the target could overwrite",
                context={"paths": ignored_collisions},
            )
        if record is not None:
            record({"event": "refresh", "target": target_head, "candidate": planning_commit})
        refreshed = _run_git(
            worktree,
            ["merge", "--no-edit", target_head],
            check=False,
        )
        if refreshed.returncode != 0:
            conflicts = _changed_paths(repository, planning_commit, target_head)
            unresolved = _run_git(
                worktree,
                ["diff", "--name-only", "--diff-filter=U"],
                check=False,
            ).stdout.splitlines()
            _run_git(worktree, ["merge", "--abort"], check=False)
            restored = _git_text(worktree, ["rev-parse", "HEAD"])
            if restored != planning_commit or _full_status(worktree):
                raise ProtocolError(
                    "integration_restore_failed",
                    "failed planning refresh did not restore the Flow Worktree",
                )
            if unresolved:
                raise ProtocolError(
                    "planning_conflict",
                    "planning publication conflicts with the latest target",
                    context={"paths": sorted(unresolved)},
                )
            raise ProtocolError(
                "integration_failed",
                refreshed.stderr.strip()
                or refreshed.stdout.strip()
                or "cannot refresh planning from target",
                context={"paths": conflicts},
            )
    candidate = _expect_oid(_git_text(worktree, ["rev-parse", "HEAD"]), "candidate")
    changed = _validate_candidate(
        binding,
        candidate,
        target_head,
        binding["base_commit"],
        allowed_paths,
        protected_paths,
    )
    return target_head, candidate, changed


def publish_planning(request: dict[str, Any], *, record=None) -> dict[str, Any]:
    _expect_keys(
        request,
        {"allowed_paths", "binding", "planning_commit", "protected_paths"},
        "publish-planning input",
    )
    binding = _binding(request["binding"])
    planning_commit = _expect_oid(request["planning_commit"], "planning_commit")
    allowed_paths = _normalize_relative_paths(request["allowed_paths"], "allowed_paths")
    if not allowed_paths:
        raise ProtocolError("invalid_input", "allowed_paths must not be empty")
    protected_paths = _normalize_relative_paths(
        request["protected_paths"], "protected_paths"
    )
    repository = Path(binding["repository"])
    common = Path(binding["git_common_dir"])
    worktree = Path(binding["worktree"])
    lock_path = common / PUBLICATION_LOCK_FILENAME
    with os.fdopen(
        os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600),
        "r+b",
        closefd=True,
    ) as lock_stream:
        _flock_with_timeout(lock_stream)
        report = _verify_binding(binding, platform_cwd=worktree)
        published_target_head = _published_target_head(
            repository,
            binding["target_branch"],
            planning_commit,
        )
        if published_target_head is not None:
            raise ProtocolError(
                "integration_unverified",
                "planning is already published; publication state was retained",
                context={"published_target_head": published_target_head},
            )
        if report["current_commit"] != planning_commit:
            raise ProtocolError(
                "candidate_changed",
                "planning commit is not the worktree HEAD",
            )
        if _full_status(worktree):
            raise ProtocolError(
                "worktree_not_clean",
                "commit every planning change before publication",
            )
        for attempt in range(2):
            try:
                target_head, candidate, changed = _prepare_planning_candidate(
                    binding,
                    planning_commit,
                    allowed_paths,
                    protected_paths,
                    record=record,
                )
                if record is not None:
                    record({"event": "prepared", "target": target_head, "candidate": candidate,
                            "resources": _publication_resources(binding)})
                merge_commit = _merge_candidate_into_target(
                    binding,
                    candidate,
                    target_head,
                )
            except ProtocolError as error:
                published_target_head = _published_target_head(
                    repository,
                    binding["target_branch"],
                    planning_commit,
                )
                if published_target_head is not None:
                    raise ProtocolError(
                        "integration_unverified",
                        "planning is already published; publication state was retained",
                        context={
                            "original_error": error.code,
                            "published_target_head": published_target_head,
                        },
                    ) from error
                if error.code == "integration_unverified":
                    raise
                _restore_planning_commit(worktree, planning_commit, error)
                if record is not None:
                    record({"event": "restored", "candidate": planning_commit})
                if error.code == "target_changed" and attempt == 0:
                    continue
                raise
            break
        if record is not None:
            record({"event": "merged", "merge_commit": merge_commit})
        advanced = _run_git(
            worktree,
            ["merge", "--ff-only", merge_commit],
            check=False,
        )
        if advanced.returncode != 0:
            raise ProtocolError(
                "flow_advance_failed",
                advanced.stderr.strip() or "published Flow Worktree could not advance",
                context={"merge_commit": merge_commit, "worktree": str(worktree)},
            )
        current_commit = _expect_oid(
            _git_text(worktree, ["rev-parse", "HEAD"]), "Flow Worktree HEAD"
        )
        if current_commit != merge_commit or _full_status(worktree):
            raise ProtocolError(
                "flow_advance_failed",
                "published Flow Worktree did not reach the planning merge",
                context={"merge_commit": merge_commit, "worktree": str(worktree)},
            )
    return {
        "binding": binding,
        "candidate_commit": candidate,
        "changed_paths": changed,
        "current_commit": current_commit,
        "merge_commit": merge_commit,
        "ok": True,
        "planning_commit": planning_commit,
        "state": "planning_published",
        "target_branch": binding["target_branch"],
    }


def complete_worktree(request: dict[str, Any], *, record=None) -> dict[str, Any]:
    _expect_keys(
        request,
        {
            "allowed_paths",
            "binding",
            "candidate_commit",
            "expected_target_head",
            "protected_paths",
            "scope_base_commit",
        },
        "complete-worktree input",
    )
    binding = _binding(request["binding"])
    candidate = _expect_oid(request["candidate_commit"], "candidate_commit")
    expected_target_head = _expect_oid(
        request["expected_target_head"], "expected_target_head"
    )
    scope_base_commit = _expect_oid(request["scope_base_commit"], "scope_base_commit")
    allowed_paths = _normalize_relative_paths(request["allowed_paths"], "allowed_paths")
    if not allowed_paths:
        raise ProtocolError("invalid_input", "allowed_paths must not be empty")
    protected_paths = _normalize_relative_paths(
        request["protected_paths"], "protected_paths"
    )
    repository = Path(binding["repository"])
    common = Path(binding["git_common_dir"])
    worktree = Path(binding["worktree"])
    lock_path = common / PUBLICATION_LOCK_FILENAME
    with os.fdopen(
        os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600),
        "r+b",
        closefd=True,
    ) as lock_stream:
        _flock_with_timeout(lock_stream)
        changed = _validate_candidate(
            binding,
            candidate,
            expected_target_head,
            scope_base_commit,
            allowed_paths,
            protected_paths,
        )
        resources = _publication_resources(binding)
        if record is not None:
            record({"event": "prepared", "target": expected_target_head, "candidate": candidate, "resources": resources})
        merge_commit = _merge_candidate_into_target(
            binding, candidate, expected_target_head
        )
        if record is not None:
            record({"event": "merged", "merge_commit": merge_commit})
    _cleanup_published(binding, candidate, merge_commit, resources, record=record)

    return {
        "candidate_commit": candidate,
        "changed_paths": changed,
        "merge_commit": merge_commit,
        "ok": True,
        "state": "completed",
        "target_branch": binding["target_branch"],
    }


def _branch_identity(binding):
    common = Path(binding["git_common_dir"])
    ref = common / "refs/heads" / binding["branch"]
    log = common / "logs/refs/heads" / binding["branch"]
    source = ref if ref.exists() else common / "packed-refs"
    stat = source.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino,
            "ref": hashlib.sha256(source.read_bytes()).hexdigest(),
            "reflog": hashlib.sha256(log.read_bytes()).hexdigest() if log.exists() else None}


def _publication_resources(binding):
    worktree = Path(binding["worktree"])
    gitdir = Path(_git_text(worktree, ["rev-parse", "--absolute-git-dir"]))
    return {"worktree": [worktree.stat().st_dev, worktree.stat().st_ino],
            "gitdir": str(gitdir), "gitdir_identity": [gitdir.stat().st_dev, gitdir.stat().st_ino],
            "branch": _branch_identity(binding)}


def _verify_resource_identity(binding, resources):
    worktree = Path(binding["worktree"])
    if worktree.exists():
        current = _publication_resources(binding)
        if any(current[key] != resources[key] for key in ("worktree", "gitdir", "gitdir_identity")):
            raise ProtocolError("cleanup_pending", "published worktree identity was replaced; preserve it")
    if _branch_oid(Path(binding["repository"]), binding["branch"]) is not None and _branch_identity(binding) != resources["branch"]:
        raise ProtocolError("cleanup_pending", "published branch identity changed; preserve it")


def _branch_deletion_guards(binding, candidate, merge_commit):
    """Retain branch -d's merged/checked-out protections before an atomic delete."""
    repository, common = Path(binding["repository"]), Path(binding["git_common_dir"])
    ref = "refs/heads/" + binding["branch"]
    registered = _git_text(repository, ["worktree", "list", "--porcelain"]).splitlines()
    if "branch " + ref in registered:
        raise ProtocolError("cleanup_pending", "published branch is in use by a worktree")
    # Detached rebase/bisect/update-refs operations can still own a branch. Be
    # conservative about all active history operations, including stale metadata.
    admin = common / "worktrees"
    for gitdir in [common] + (list(admin.iterdir()) if admin.exists() else []):
        if not gitdir.is_dir():
            continue
        head = gitdir / "HEAD"
        if head.exists() and head.read_text().strip() == "ref: " + ref:
            raise ProtocolError("cleanup_pending", "published branch remains checked out")
        if any((gitdir / name).exists() for name in
               ("rebase-merge", "rebase-apply", "BISECT_START", "sequencer", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")):
            raise ProtocolError("cleanup_pending", "active worktree history operation prevents branch cleanup")
    symbolic = _run_git(repository, ["symbolic-ref", "--quiet", ref], check=False)
    if symbolic.returncode != 1:
        raise ProtocolError("cleanup_pending", "published branch is not an ordinary direct ref")
    upstream = _git_text(repository, ["for-each-ref", "--format=%(upstream)", ref])
    reference = upstream or "HEAD"
    resolved = _run_git(repository, ["rev-parse", "--verify", "--quiet", reference + "^{commit}"], check=False)
    if resolved.returncode != 0:
        reference = "HEAD"
        resolved = _run_git(repository, ["rev-parse", "--verify", "HEAD^{commit}"])
    oid = _expect_oid(resolved.stdout.strip(), "branch deletion reference")
    if not _is_ancestor(repository, candidate, oid):
        raise ProtocolError("cleanup_pending", "published branch is not merged into its upstream or HEAD")
    if reference == "HEAD":
        reference = _git_text(repository, ["rev-parse", "--symbolic-full-name", "HEAD"])
    if reference == ref:
        raise ProtocolError("cleanup_pending", "self-tracking branch cannot establish deletion safety")
    target_ref = "refs/heads/" + binding["target_branch"]
    target = _branch_oid(repository, binding["target_branch"])
    if target is None or not _is_ancestor(repository, merge_commit, target):
        raise ProtocolError("integration_unverified", "published merge is no longer on target")
    guards = {target_ref: target}
    if reference in guards and guards[reference] != oid:
        raise ProtocolError("cleanup_pending", "deletion reference changed during verification")
    guards[reference] = oid
    return guards


def _ref_transaction_command(process, command, acknowledgement):
    """One bounded command/ack; stdin EOF aborts any uncommitted transaction."""
    process.stdin.write(command.encode())
    process.stdin.flush()
    deadline = time.monotonic() + PUBLICATION_LOCK_TIMEOUT_SECONDS
    response = b""
    while not response.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([process.stdout], [], [], max(0, remaining))[0]:
            raise ProtocolError("cleanup_pending", "Git reference transaction acknowledgement timed out", context={"phase": acknowledgement})
        chunk = os.read(process.stdout.fileno(), 4096)
        if not chunk or len(response) + len(chunk) > 4096:
            raise ProtocolError("cleanup_pending", "Git reference transaction did not acknowledge the original action", context={"phase": acknowledgement})
        response += chunk
    if response != (acknowledgement + ": ok\n").encode():
        raise ProtocolError("cleanup_pending", "Git reference transaction returned an unexpected acknowledgement", context={"phase": acknowledgement})


def _delete_published_branch(binding, candidate, merge_commit, resources):
    repository = Path(binding["repository"])
    ref = "refs/heads/" + binding["branch"]
    guards = _branch_deletion_guards(binding, candidate, merge_commit)
    try:
        process = subprocess.Popen(["git", "-C", str(repository), "update-ref", "--stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    except OSError as error:
        raise ProtocolError("cleanup_pending", "cannot start Git reference transaction", context={"branch": binding["branch"]}) from error
    try:
        _ref_transaction_command(process, "start\n", "start")
        commands = "option no-deref\ndelete " + ref + " " + candidate + "\n"
        commands += "".join("verify " + name + " " + oid + "\n" for name, oid in sorted(guards.items()))
        _ref_transaction_command(process, commands + "prepare\n", "prepare")
        # prepare holds the branch and merge-reference locks until commit/abort.
        # A same-OID recreation before prepare must still match original resource
        # identity here. A later ordinary Git ref writer cannot pass the locks.
        _verify_resource_identity(binding, resources)
        if _branch_deletion_guards(binding, candidate, merge_commit) != guards:
            raise ProtocolError("cleanup_pending", "branch deletion conditions changed")
        _ref_transaction_command(process, "commit\n", "commit")
    except (BrokenPipeError, OSError) as error:
        raise ProtocolError("cleanup_pending", "Git reference deletion could not be confirmed; reconcile original publication") from error
    finally:
        # Closing stdin releases prepared locks through Git's own abort path.
        process.stdin.close()
        try:
            process.wait(timeout=PUBLICATION_LOCK_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=PUBLICATION_LOCK_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
    if process.returncode != 0:
        raise ProtocolError("cleanup_pending", "Git reference deletion did not finish successfully")


def _cleanup_published(binding, candidate, merge_commit, resources, *, record=None):
    """Remove only unchanged published resources; never force or prune."""
    repository, worktree = Path(binding["repository"]), Path(binding["worktree"])
    if not _is_ancestor(repository, merge_commit, binding["target_branch"]):
        raise ProtocolError("integration_unverified", "published merge is no longer on target")
    branch = _branch_oid(repository, binding["branch"])
    if branch is not None and branch != candidate:
        raise ProtocolError("cleanup_pending", "published branch changed; preserve it", context={"merge_commit": merge_commit})
    _verify_resource_identity(binding, resources)
    registered = _git_text(repository, ["worktree", "list", "--porcelain"]).splitlines()
    if worktree.exists():
        if "worktree " + str(worktree) not in registered:
            raise ProtocolError("cleanup_pending", "worktree path was replaced; preserve it")
        report = _verify_binding(binding, platform_cwd=worktree)
        if report["current_commit"] != candidate or _full_status(worktree):
            raise ProtocolError("cleanup_pending", "published worktree changed; preserve it")
        # `git worktree remove` may discard ignored content even without --force.
        # Preserve it explicitly; the owner must resolve it before cleanup.
        ignored = _run_git(worktree, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"]).stdout
        if ignored:
            raise ProtocolError("cleanup_pending", "worktree contains ignored user files; preserve them",
                                context={"merge_commit": merge_commit})
        removed = _run_git(repository, ["worktree", "remove", str(worktree)], check=False)
        if removed.returncode != 0:
            raise ProtocolError("cleanup_failed", removed.stderr.strip() or "worktree removal failed",
                                context={"merge_commit": merge_commit, "worktree": str(worktree)})
        if record is not None:
            record({"event": "worktree-removed"})
    elif "worktree " + str(worktree) in registered:
        raise ProtocolError("cleanup_pending", "missing worktree remains registered; do not prune other resources")
    if _branch_oid(repository, binding["branch"]) is not None:
        _delete_published_branch(binding, candidate, merge_commit, resources)
        if record is not None:
            record({"event": "branch-removed"})
    cleanup = _cleanup_facts(binding)
    if not all(cleanup.values()):
        raise ProtocolError("cleanup_pending", "published resources remain", context={"merge_commit": merge_commit})
    return cleanup


def _cleanup_facts(binding):
    repository = Path(binding["repository"])
    registered = _git_text(repository, ["worktree", "list", "--porcelain"]).splitlines()
    return {"worktree_removed": not Path(binding["worktree"]).exists() and
            "worktree " + binding["worktree"] not in registered,
            "branch_removed": _branch_oid(repository, binding["branch"]) is None and
            not os.path.lexists(Path(binding["git_common_dir"]) / "refs/heads" / binding["branch"]) and
            not os.path.lexists(Path(binding["git_common_dir"]) / "logs/refs/heads" / binding["branch"])}


def _publication_input(transaction):
    _expect_keys(transaction, {"operation", "request", "facts"}, "publication transaction")
    operation, request, facts = transaction["operation"], transaction["request"], transaction["facts"]
    if operation not in {"publish-planning", "complete-worktree"} or not isinstance(request, dict):
        raise ProtocolError("invalid_input", "unknown publication operation")
    fields = {"binding", "allowed_paths", "protected_paths"}
    fields |= {"planning_commit"} if operation == "publish-planning" else {"candidate_commit", "expected_target_head", "scope_base_commit"}
    _expect_keys(request, fields, "original publication request")
    binding = _binding(request["binding"], removed=True)
    if not isinstance(facts, list):
        raise ProtocolError("invalid_input", "ordered publication facts required")
    for fact in facts:
        if not isinstance(fact, dict):
            raise ProtocolError("invalid_input", "invalid publication fact")
        event = fact.get("event")
        extra = {"refresh": {"candidate", "target"}, "prepared": {"candidate", "target", "resources"},
                 "merged": {"merge_commit"}, "restored": {"candidate"},
                 "worktree-removed": set(), "branch-removed": set()}.get(event)
        if extra is None:
            raise ProtocolError("invalid_input", "unknown publication fact")
        _expect_keys(fact, {"event"} | extra, "publication fact")
        for key in extra - {"resources"}:
            _expect_oid(fact[key], key)
        if event == "prepared":
            resources = fact["resources"]
            if not isinstance(resources, dict):
                raise ProtocolError("invalid_input", "publication resource identities required")
            _expect_keys(resources, {"worktree", "gitdir", "gitdir_identity", "branch"}, "publication resources")
            for name in ("worktree", "gitdir_identity"):
                if not isinstance(resources[name], list) or len(resources[name]) != 2 or any(type(n) is not int or n < 0 for n in resources[name]):
                    raise ProtocolError("invalid_input", "invalid resource identity")
            _canonical_absolute_path(resources["gitdir"], "resource gitdir", must_exist=False)
            if not isinstance(resources["branch"], dict):
                raise ProtocolError("invalid_input", "invalid branch identity")
            _expect_keys(resources["branch"], {"device", "inode", "ref", "reflog"}, "branch identity")
    _normalize_relative_paths(request["allowed_paths"], "allowed_paths")
    _normalize_relative_paths(request["protected_paths"], "protected_paths")
    _expect_oid(request.get("planning_commit", request.get("candidate_commit")), "original candidate")
    if operation == "complete-worktree":
        _expect_oid(request["expected_target_head"], "expected_target_head")
        _expect_oid(request["scope_base_commit"], "scope_base_commit")
    return operation, request, facts, binding


def _verify_publication_scope(operation, request, binding, prepared):
    """Verify immutable objects even after the worktree has been removed."""
    repository = Path(binding["repository"])
    target, candidate = prepared["target"], prepared["candidate"]
    baseline = request.get("scope_base_commit", binding["base_commit"])
    original = request.get("planning_commit", request.get("candidate_commit"))
    if not all(_is_ancestor(repository, old, new) for old, new in
               ((baseline, target), (baseline, candidate), (target, candidate), (original, candidate))):
        raise ProtocolError("integration_unverified", "publication ancestry differs from saved scope")
    if operation == "complete-worktree" and (candidate != original or target != request["expected_target_head"]):
        raise ProtocolError("integration_unverified", "final publication changed original candidate or target")
    changed = _changed_paths(repository, target, candidate)
    if not changed or any(not _path_matches(p, request["allowed_paths"]) for p in changed):
        raise ProtocolError("path_outside_scope", "publication delta escapes original scope")
    if any(_path_matches(p, request["protected_paths"]) for p in _changed_paths(repository, baseline, candidate)):
        raise ProtocolError("source_changed", "publication changed protected sources")
    return changed


def _inspect_publication(transaction):
    operation, request, facts, binding = _publication_input(transaction)
    repository = Path(binding["repository"])
    head = _branch_oid(repository, binding["target_branch"])
    if head is None:
        raise ProtocolError("target_changed", "publication target is missing")
    preparations = [f for f in facts if f["event"] == "prepared"]
    matches = []
    # Search exact parent pairs, not merely candidate ancestry or current HEAD.
    for prepared in preparations:
        _verify_publication_scope(operation, request, binding, prepared)
        lines = _git_text(repository, ["rev-list", "--first-parent", "--parents", head,
                                      "^" + prepared["target"]]).splitlines()
        for line in lines:
            parts = line.split()
            if parts[1:] == [prepared["target"], prepared["candidate"]]:
                merge = parts[0]
                if _git_text(repository, ["rev-parse", merge + "^{tree}"]) != _git_text(repository, ["rev-parse", prepared["candidate"] + "^{tree}"]):
                    raise ProtocolError("integration_unverified", "published tree differs from saved candidate")
                if not any(item[1] == merge for item in matches):
                    matches.append((prepared, merge))
    if len(matches) > 1:
        raise ProtocolError("integration_unverified", "multiple publications match the original action")
    known = [f["merge_commit"] for f in facts if f["event"] == "merged"]
    if known and (not matches or any(m != matches[0][1] for m in known)):
        raise ProtocolError("integration_unverified", "saved publication is not proven on target")
    if matches:
        prepared, merge = matches[0]
        changed = _verify_publication_scope(operation, request, binding, prepared)
        result = {"ok": True, "state": "published", "candidate_commit": prepared["candidate"],
                  "merge_commit": merge, "changed_paths": changed, "target_branch": binding["target_branch"],
                  "resources": prepared["resources"]}
        if operation == "publish-planning":
            flow = _verify_binding(binding, platform_cwd=Path(binding["worktree"]))
            result.update(binding=binding, planning_commit=request["planning_commit"], current_commit=flow["current_commit"],
                          state="planning_published" if flow["current_commit"] == merge and not _full_status(Path(binding["worktree"])) else "flow-advance-pending")
        else:
            result["cleanup"] = _cleanup_facts(binding)
            result["state"] = "completed" if all(result["cleanup"].values()) else "cleanup-pending"
        return result
    # Absence of a matching merge alone does not authorize retry.
    original = request.get("planning_commit", request.get("candidate_commit"))
    if _is_ancestor(repository, original, head):
        raise ProtocolError("integration_unverified", "candidate is on target without exact publication proof")
    if (Path(binding["git_common_dir"]) / "MERGE_HEAD").exists():
        raise ProtocolError("integration_unverified", "target has an unfinished merge")
    flow = _verify_binding(binding, platform_cwd=Path(binding["worktree"]))
    if _full_status(Path(binding["worktree"])):
        raise ProtocolError("worktree_not_clean", "preserve changes during publication recovery")
    latest = facts[-1] if facts else None
    prepared = next((f for f in reversed(facts) if f["event"] == "prepared"), None)
    if prepared and latest["event"] not in {"restored", "refresh"}:
        if head != prepared["target"] or flow["current_commit"] != prepared["candidate"]:
            raise ProtocolError("integration_unverified", "target or prepared candidate changed after publication intent")
        return {"ok": True, "state": "prepared", "prepared": prepared}
    if latest and latest["event"] == "refresh" and flow["current_commit"] != original:
        parents = _git_text(repository, ["show", "-s", "--format=%P", flow["current_commit"]]).split()
        if head != latest["target"] or parents != [original, head]:
            raise ProtocolError("integration_unverified", "planning refresh cannot be attributed to original intent")
        prepared = {"event": "prepared", "target": head, "candidate": flow["current_commit"],
                    "resources": _publication_resources(binding)}
        _verify_publication_scope(operation, request, binding, prepared)
        return {"ok": True, "state": "prepared", "prepared": prepared}
    if flow["current_commit"] != original:
        raise ProtocolError("candidate_changed", "original candidate changed before publication")
    if operation == "complete-worktree" and head != request["expected_target_head"]:
        raise ProtocolError("target_changed", "original final target changed")
    return {"ok": True, "state": "not-published"}


def reconcile_publication(transaction, *, resume=False, cleanup_only=False, record=None):
    """Owner-saved facts are evidence, never authority. Caller verifies permission.

    Inspect is read-only. Resume persists each new intent through the existing
    checkpoint owner before writes; there is no second journal or run loop.
    """
    operation, request, _, binding = _publication_input(transaction)
    common, worktree = Path(binding["git_common_dir"]), Path(binding["worktree"])
    retry = False
    with os.fdopen(os.open(common / PUBLICATION_LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600), "r+b") as stream:
        _flock_with_timeout(stream)
        result = _inspect_publication(transaction)
        if not resume:
            return result
        if result["state"] in {"not-published", "prepared"}:
            if cleanup_only:
                raise ProtocolError("integration_unverified", "cleanup requires a proven publication")
            if record is None:
                raise ProtocolError("checkpoint_required", "publication recovery requires a durable owner callback")
            if result["state"] == "not-published":
                retry = True
            else:
                prepared = result["prepared"]
                _validate_candidate(binding, prepared["candidate"], prepared["target"],
                                    request.get("scope_base_commit", binding["base_commit"]),
                                    request["allowed_paths"], request["protected_paths"])
                record(copy.deepcopy(prepared))
                merge = _merge_candidate_into_target(binding, prepared["candidate"], prepared["target"])
                record({"event": "merged", "merge_commit": merge})
                # Include persisted facts supplied by the callback without relying
                # on caller object aliasing.
                updated = copy.deepcopy(transaction)
                updated["facts"].extend([prepared, {"event": "merged", "merge_commit": merge}])
                result = _inspect_publication(updated)
        if not retry and operation == "publish-planning" and result["state"] == "flow-advance-pending":
            if cleanup_only:
                raise ProtocolError("invalid_input", "planning has no cleanup operation")
            if _full_status(worktree) or result["current_commit"] != result["candidate_commit"]:
                raise ProtocolError("candidate_changed", "published Flow Worktree changed; preserve it")
            _verify_resource_identity(binding, result["resources"])
            advanced = _run_git(worktree, ["merge", "--ff-only", result["merge_commit"]], check=False)
            if advanced.returncode != 0:
                raise ProtocolError("flow_advance_failed", advanced.stderr.strip() or "Flow advance failed")
            if _git_text(worktree, ["rev-parse", "HEAD"]) != result["merge_commit"] or _full_status(worktree):
                raise ProtocolError("flow_advance_failed", "Flow advance could not be verified")
            result.update(state="planning_published", current_commit=result["merge_commit"])
        if not retry and operation == "publish-planning":
            return result
    if not retry:
        result["cleanup"] = _cleanup_published(binding, result["candidate_commit"], result["merge_commit"], result["resources"], record=record)
        result["state"] = "completed"
        return result
    # Initial publication rechecks under the same existing lock. The original
    # immutable request is retained; no changed candidate or target is substituted.
    publisher = publish_planning if operation == "publish-planning" else complete_worktree
    return publisher(request, record=record)


def cleanup_publication(transaction):
    return reconcile_publication(transaction, resume=True, cleanup_only=True)


def _emit_json(value: Any) -> None:
    data = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(data)


def _build_command_registry() -> CommandRegistry:
    return CommandRegistry(
        [
            CommandSpec("start-worktree", lambda a: start_worktree(a.request)),
            CommandSpec("verify-worktree", lambda a: verify_worktree(a.request)),
            CommandSpec("publish-planning", lambda a: publish_planning(a.request)),
            CommandSpec("complete-worktree", lambda a: complete_worktree(a.request)),
            CommandSpec("reconcile-publication", lambda a: reconcile_publication(a.request)),
            CommandSpec("cleanup-only", lambda a: cleanup_publication(a.request)),
        ]
    )


COMMAND_REGISTRY = _build_command_registry()


def _build_parser() -> argparse.ArgumentParser:
    return COMMAND_REGISTRY.build_parser(
        description="Run the minimal Flow Worktree delivery protocol."
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        arguments.request = _load_input(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
        _, result = COMMAND_REGISTRY.dispatch(arguments)
        _emit_json(result)
    except ProtocolError as error:
        _emit_json(_error_response(error, arguments.command))
        print(f"ERROR: {error.message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
