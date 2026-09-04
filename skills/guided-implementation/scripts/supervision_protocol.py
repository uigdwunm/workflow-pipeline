#!/usr/bin/env python3

"""Create, verify, integrate, and clean one isolated Git worktree."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
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


def _binding(value: Any) -> dict[str, Any]:
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
    worktree = _canonical_absolute_path(value["worktree"], "worktree", must_exist=True)
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


def publish_planning(request: dict[str, Any]) -> dict[str, Any]:
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
                )
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
                if error.code == "target_changed" and attempt == 0:
                    continue
                raise
            break
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


def complete_worktree(request: dict[str, Any]) -> dict[str, Any]:
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
        merge_commit = _merge_candidate_into_target(
            binding, candidate, expected_target_head
        )

    removed = _run_git(
        repository, ["worktree", "remove", str(worktree)], check=False
    )
    if removed.returncode != 0:
        raise ProtocolError(
            "cleanup_failed",
            removed.stderr.strip() or "integrated worktree could not be removed",
            context={"merge_commit": merge_commit, "worktree": str(worktree)},
        )
    deleted = _run_git(repository, ["branch", "-d", binding["branch"]], check=False)
    if deleted.returncode != 0:
        raise ProtocolError(
            "cleanup_failed",
            deleted.stderr.strip() or "integrated branch could not be removed",
            context={"branch": binding["branch"], "merge_commit": merge_commit},
        )
    return {
        "candidate_commit": candidate,
        "changed_paths": changed,
        "merge_commit": merge_commit,
        "ok": True,
        "state": "completed",
        "target_branch": binding["target_branch"],
    }


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
