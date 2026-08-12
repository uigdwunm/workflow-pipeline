#!/usr/bin/env python3
"""Deterministic bootstrap protocol for persistent design discussions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
from typing import Any


PROTOCOL_VERSION = 1
EXPLICIT_ENTRY_MODES = {
    "explicit-skill",
    "explicit-interface-name",
    "explicit-sustained-design",
}
STATELESS_ENTRY_MODE = "ordinary-consultation"
ROOT_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
IDENTITY_RE = re.compile(r"^(project|tree|topic)-[0-9a-f]{32}$")
MAX_REQUEST_BYTES = 64 * 1024


class ProtocolError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        cause: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.cause = cause
        self.context = context or {}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _response_error(error: ProtocolError) -> dict[str, Any]:
    chinese_messages = {
        "discussion_already_initialized": "项目已绑定其他持久化根话题，不能猜测或替换。",
        "git_identity_invalid": "Git 协调目录身份无效。",
        "idempotency_conflict": "同一幂等键已用于不同的初始化参数。",
        "initialization_conflict": "初始化目标已存在，已停止且未覆盖原内容。",
        "initialization_failed": "持久化讨论初始化失败，已停止并回滚本次新增状态。",
        "initialization_verification_failed": "初始化后的权威状态回读验证失败。",
        "invalid_entry_mode": "当前入口不是明确的持久化 0讨论 触发。",
        "invalid_json": "标准输入必须只包含一个有效 JSON 值。",
        "invalid_project_path": "项目路径必须是已存在且规范化的绝对目录。",
        "invalid_request": "请求不符合 bootstrap 类型化接口。",
        "invalid_root_slug": "根话题 slug 格式无效。",
        "invalid_storage_path": "持久化路径包含不安全或无效的组件。",
        "state_corrupt": "持久化讨论权威状态损坏或不完整。",
        "unsupported_operation": "当前工单只支持 bootstrap 操作。",
        "unsupported_protocol_version": "协议版本不受支持。",
    }
    detail: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
        "message_zh": chinese_messages.get(error.code, "讨论协议操作失败。"),
        "retryable": error.retryable,
        "cause": error.cause,
    }
    return {
        "ok": False,
        "state": error.context.get("state", "stopped"),
        "ledger_revision": error.context.get("ledger_revision"),
        "record_revision": error.context.get("record_revision"),
        "project_id": error.context.get("project_id"),
        "tree_id": error.context.get("tree_id"),
        "topic_id": error.context.get("topic_id"),
        "error": detail,
    }


def _expect_keys(source: dict[str, Any], expected: set[str], label: str) -> None:
    observed = set(source)
    if observed != expected:
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        raise ProtocolError(
            "invalid_request",
            f"{label} fields do not match the bootstrap schema; "
            f"missing={missing!r}; unexpected={unexpected!r}",
        )


def _expect_string(value: Any, label: str, *, max_bytes: int = 512) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ProtocolError("invalid_request", f"{label} must be a non-empty single line")
    if len(value.encode("utf-8")) > max_bytes:
        raise ProtocolError("invalid_request", f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _validate_project_path(value: Any) -> Path:
    raw = _expect_string(value, "project_path", max_bytes=4096)
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ProtocolError("invalid_project_path", "project_path must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ProtocolError(
            "invalid_project_path",
            "project_path must resolve to an existing directory",
            cause=str(error),
        ) from error
    if not resolved.is_dir():
        raise ProtocolError("invalid_project_path", "project_path must resolve to a directory")
    if candidate != resolved:
        raise ProtocolError(
            "invalid_project_path",
            "project_path must be canonical and contain no symlink or relative components",
        )
    return resolved


def _validate_uuid4(value: Any, label: str) -> str:
    text = _expect_string(value, label, max_bytes=64)
    try:
        parsed = uuid.UUID(text)
    except ValueError as error:
        raise ProtocolError("invalid_request", f"{label} must be a UUIDv4") from error
    if parsed.version != 4 or str(parsed) != text:
        raise ProtocolError("invalid_request", f"{label} must be a canonical UUIDv4")
    return text


def _new_identity(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex}"


def _git_common_dir(project: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        return None
    path = Path(completed.stdout.strip())
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ProtocolError(
            "git_identity_invalid",
            "Git reported a common directory that cannot be resolved",
            cause=str(error),
        ) from error
    if not resolved.is_dir():
        raise ProtocolError("git_identity_invalid", "Git common directory is not a directory")
    return resolved


def _coordination_root(project: Path) -> tuple[Path, str]:
    common_dir = _git_common_dir(project)
    if common_dir is not None:
        return common_dir / "cc-switch" / "design-discussion" / "v1", "git"
    return project / ".codex" / "design-discussion" / "v1", "non-git"


def _project_lock_name(project: Path) -> str:
    return hashlib.sha256(str(project).encode("utf-8")).hexdigest() + ".lock"


def _yaml_scalar(value: str | int | bool | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _yaml_record_block(records: list[dict[str, Any]]) -> str:
    lines = ["```yaml", "records:"]
    if not records:
        lines.append("  []")
    for record in records:
        first = True
        for key, value in record.items():
            prefix = "  - " if first else "    "
            lines.append(f"{prefix}{key}: {_yaml_scalar(value)}")
            first = False
    lines.append("```")
    return "\n".join(lines)


def _render_project_manifest(
    *, project_id: str, tree_id: str, topic_id: str, root_slug: str
) -> bytes:
    return (
        "---\n"
        "schema_version: 1\n"
        f"project_id: {project_id}\n"
        f"tree_id: {tree_id}\n"
        f"topic_id: {topic_id}\n"
        f"root_slug: {root_slug}\n"
        "---\n"
        "# Codex Design Discussion Project\n"
    ).encode("utf-8")


def _render_topic_document(
    *, project_id: str, tree_id: str, topic_id: str, root_slug: str
) -> bytes:
    return (
        "---\n"
        "schema_version: 1\n"
        f"project_id: {project_id}\n"
        f"tree_id: {tree_id}\n"
        f"topic_id: {topic_id}\n"
        "parent_topic_id: null\n"
        "topic_revision: 1\n"
        "---\n"
        f"# {root_slug}\n\n"
        "## Confirmed Decisions\n\n- None.\n\n"
        "## Candidate Solution\n\n- None.\n\n"
        "## Tentative Assumptions\n\n- None.\n\n"
        "## Facts\n\n- Persistent discussion workspace initialized.\n\n"
        "## Pending Questions\n\n- The first substantive question is asked after bootstrap verification.\n\n"
        "## Decision Evolution\n\n- None.\n"
    ).encode("utf-8")


LEDGER_SECTION_NAMES = (
    "Current Topics",
    "Pending Items",
    "Phase Results",
    "Checkpoints",
    "Phase Runs",
    "Pending Document Writes",
    "Impacts",
    "Relations and Coverage",
    "Dependencies and Active Implementations",
    "Conversation Bindings",
    "Recent Events",
)


def _render_ledger(
    *,
    project_id: str,
    tree_id: str,
    topic_id: str,
    root_slug: str,
    conversation_ref: str,
    idempotency_key: str,
    request_fingerprint: str,
    project_manifest_path: Path,
    topic_document_path: Path,
) -> bytes:
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in LEDGER_SECTION_NAMES}
    records["Current Topics"] = [
        {
            "topic_id": topic_id,
            "record_revision": 1,
            "root_slug": root_slug,
            "current_phase": 0,
            "phase_state": "active",
            "review_state": "unreviewed",
            "topic_state": "open",
            "topic_document_path": str(topic_document_path),
        }
    ]
    records["Conversation Bindings"] = [
        {
            "topic_id": topic_id,
            "conversation_ref": conversation_ref,
            "binding_state": "active",
            "record_revision": 1,
        }
    ]
    records["Recent Events"] = [
        {
            "event_id": "event-00000001",
            "event_type": "root-topic-bootstrapped",
            "ledger_revision": 1,
            "topic_id": topic_id,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
        }
    ]
    body_lines = ["# Design Discussion Ledger", ""]
    for name in LEDGER_SECTION_NAMES:
        body_lines.extend([f"## {name}", "", _yaml_record_block(records[name]), ""])
    body = "\n".join(body_lines)
    frontmatter_without_digest = (
        "schema_version: 1\n"
        f"project_id: {project_id}\n"
        f"tree_id: {tree_id}\n"
        "ledger_revision: 1\n"
        "event_count: 1\n"
        f"project_manifest_path: {json.dumps(str(project_manifest_path))}\n"
    )
    digest = hashlib.sha256((frontmatter_without_digest + body).encode("utf-8")).hexdigest()
    return (
        "---\n"
        + frontmatter_without_digest
        + f"content_digest: {digest}\n"
        + "---\n"
        + body
    ).encode("utf-8")


def _parse_frontmatter(data: bytes, label: str) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("state_corrupt", f"{label} is not UTF-8", cause=str(error)) from error
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ProtocolError("state_corrupt", f"{label} has invalid frontmatter")
    frontmatter = text[4:].split("\n---\n", 1)[0]
    result: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ": " not in line:
            raise ProtocolError("state_corrupt", f"{label} frontmatter is malformed")
        key, value = line.split(": ", 1)
        if not key or key in result:
            raise ProtocolError("state_corrupt", f"{label} frontmatter keys are invalid")
        result[key] = value.strip('"')
    return result


def _require_regular_nosymlink(path: Path, label: str) -> bytes:
    cursor = path
    while cursor != cursor.parent:
        try:
            status = os.lstat(cursor)
        except OSError as error:
            raise ProtocolError(
                "state_corrupt", f"cannot inspect {label}: {cursor}", cause=str(error)
            ) from error
        if stat.S_ISLNK(status.st_mode):
            raise ProtocolError("state_corrupt", f"{label} contains a symbolic-link component")
        cursor = cursor.parent
    try:
        status = os.lstat(path)
    except OSError as error:
        raise ProtocolError("state_corrupt", f"cannot inspect {label}", cause=str(error)) from error
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ProtocolError("state_corrupt", f"{label} must be a single-link regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ProtocolError("state_corrupt", f"cannot read {label}", cause=str(error)) from error


def _verify_ledger_digest(data: bytes) -> tuple[dict[str, str], str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("state_corrupt", "ledger is not UTF-8", cause=str(error)) from error
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ProtocolError("state_corrupt", "ledger has invalid frontmatter")
    frontmatter_text, body = text[4:].split("\n---\n", 1)
    frontmatter = _parse_frontmatter(data, "ledger")
    digest = frontmatter.get("content_digest")
    if digest is None or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ProtocolError("state_corrupt", "ledger content_digest is invalid")
    lines_without_digest = [
        line for line in frontmatter_text.splitlines() if not line.startswith("content_digest: ")
    ]
    expected = hashlib.sha256(
        (("\n".join(lines_without_digest) + "\n") + body).encode("utf-8")
    ).hexdigest()
    if digest != expected:
        raise ProtocolError("state_corrupt", "ledger content_digest does not match its bytes")
    return frontmatter, text


def _ledger_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for index, name in enumerate(LEDGER_SECTION_NAMES):
        marker = f"## {name}\n\n"
        if text.count(marker) != 1:
            raise ProtocolError("state_corrupt", f"ledger section {name!r} is missing or duplicated")
        start = text.index(marker) + len(marker)
        if index + 1 < len(LEDGER_SECTION_NAMES):
            next_marker = f"\n## {LEDGER_SECTION_NAMES[index + 1]}\n\n"
            try:
                end = text.index(next_marker, start)
            except ValueError as error:
                raise ProtocolError(
                    "state_corrupt", f"ledger section order is invalid after {name!r}"
                ) from error
        else:
            end = len(text)
        section = text[start:end]
        if not section.startswith("```yaml\nrecords:") or not section.rstrip().endswith("```"):
            raise ProtocolError("state_corrupt", f"ledger section {name!r} has invalid record framing")
        sections[name] = section
    return sections


def _mkdirs(path: Path, created_directories: list[Path]) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    try:
        cursor_stat = os.lstat(cursor)
    except OSError as error:
        raise ProtocolError(
            "invalid_storage_path",
            f"cannot inspect required storage path component: {cursor}",
            cause=str(error),
        ) from error
    if not stat.S_ISDIR(cursor_stat.st_mode):
        raise ProtocolError(
            "invalid_storage_path",
            f"required directory parent is not a directory: {cursor}",
        )
    existing = cursor
    while existing != existing.parent:
        existing_stat = os.lstat(existing)
        if stat.S_ISLNK(existing_stat.st_mode):
            raise ProtocolError(
                "invalid_storage_path",
                f"storage path contains a symbolic-link component: {existing}",
            )
        existing = existing.parent
    for directory in reversed(missing):
        directory.mkdir()
        created_directories.append(directory)


def _write_new_file(path: Path, data: bytes, created_files: list[Path]) -> None:
    if path.exists():
        raise ProtocolError("initialization_conflict", f"refusing to replace existing path: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise ProtocolError(
                "initialization_conflict", f"refusing to replace existing path: {path}"
            ) from error
        created_files.append(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _rollback(created_files: list[Path], created_directories: list[Path]) -> None:
    for path in reversed(created_files):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    for path in reversed(created_directories):
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _request_fingerprint(
    *, project: Path, root_slug: str, conversation_ref: str, entry_mode: str
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "conversation_ref": conversation_ref,
                "entry_mode": entry_mode,
                "project_path": str(project),
                "root_slug": root_slug,
            }
        ).encode("utf-8")
    ).hexdigest()


def _existing_response(
    *,
    project: Path,
    coordination_root: Path,
    storage_mode: str,
    conversation_ref: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    manifest_path = project / "docs" / "discussions" / ".codex-project.md"
    if not manifest_path.exists():
        return None
    manifest_data = _require_regular_nosymlink(manifest_path, "project identity manifest")
    manifest = _parse_frontmatter(manifest_data, "project identity manifest")
    required = {"project_id", "tree_id", "topic_id", "root_slug"}
    if not required.issubset(manifest):
        raise ProtocolError("state_corrupt", "project identity manifest is incomplete")
    for field in ("project_id", "tree_id", "topic_id"):
        if not IDENTITY_RE.fullmatch(manifest[field]):
            raise ProtocolError("state_corrupt", f"project identity manifest has invalid {field}")
    root_slug = manifest["root_slug"]
    if not ROOT_SLUG_RE.fullmatch(root_slug):
        raise ProtocolError("state_corrupt", "project identity manifest has invalid root_slug")
    ledger_path = (
        coordination_root
        / "projects"
        / manifest["project_id"]
        / "trees"
        / manifest["tree_id"]
        / "ledger.md"
    )
    topic_path = project / "docs" / "discussions" / root_slug / "topic.md"
    context = {
        "state": "stopped",
        "ledger_revision": 1,
        "record_revision": 1,
        "project_id": manifest["project_id"],
        "tree_id": manifest["tree_id"],
        "topic_id": manifest["topic_id"],
    }
    try:
        ledger_data = _require_regular_nosymlink(ledger_path, "ledger")
        topic_data = _require_regular_nosymlink(topic_path, "topic document")
        ledger_frontmatter, ledger_text = _verify_ledger_digest(ledger_data)
        topic_frontmatter = _parse_frontmatter(topic_data, "topic document")
        expected_ledger_frontmatter = {
            "schema_version": "1",
            "project_id": manifest["project_id"],
            "tree_id": manifest["tree_id"],
            "ledger_revision": "1",
            "event_count": "1",
            "project_manifest_path": str(manifest_path),
        }
        for field, expected_value in expected_ledger_frontmatter.items():
            if ledger_frontmatter.get(field) != expected_value:
                raise ProtocolError("state_corrupt", f"ledger has invalid {field}")
        expected_topic_frontmatter = {
            "schema_version": "1",
            "project_id": manifest["project_id"],
            "tree_id": manifest["tree_id"],
            "topic_id": manifest["topic_id"],
            "parent_topic_id": "null",
            "topic_revision": "1",
        }
        for field, expected_value in expected_topic_frontmatter.items():
            if topic_frontmatter.get(field) != expected_value:
                raise ProtocolError("state_corrupt", f"topic document has invalid {field}")
        sections = _ledger_sections(ledger_text)
        current_topic_records = (
            f'topic_id: "{manifest["topic_id"]}"',
            "current_phase: 0",
            'phase_state: "active"',
            'topic_state: "open"',
            f'topic_document_path: "{topic_path}"',
        )
        binding_records = (
            f'topic_id: "{manifest["topic_id"]}"',
            f'conversation_ref: "{conversation_ref}"',
            'binding_state: "active"',
        )
        event_records = (
            f'topic_id: "{manifest["topic_id"]}"',
            'event_type: "root-topic-bootstrapped"',
            f'idempotency_key: "{idempotency_key}"',
            f'request_fingerprint: "{request_fingerprint}"',
        )
        if any(sections["Current Topics"].count(record) != 1 for record in current_topic_records):
            raise ProtocolError("state_corrupt", "root topic record is invalid")
        if any(
            sections["Conversation Bindings"].count(record) != 1
            for record in binding_records
        ):
            raise ProtocolError("state_corrupt", "active conversation binding is invalid")
        if any(sections["Recent Events"].count(record) != 1 for record in event_records):
            raise ProtocolError("state_corrupt", "root topic, binding or event record is invalid")
    except ProtocolError as error:
        error.context = {**context, **error.context}
        raise
    if f'idempotency_key: "{idempotency_key}"' not in ledger_text:
        raise ProtocolError(
            "discussion_already_initialized",
            "the project already has a different persistent discussion root",
            context=context,
        )
    if f'request_fingerprint: "{request_fingerprint}"' not in ledger_text:
        raise ProtocolError(
            "idempotency_conflict",
            "idempotency key was already used with different bootstrap parameters",
            context=context,
        )
    for identity in required - {"root_slug"}:
        value = manifest[identity]
        if value.encode("utf-8") not in ledger_data or value.encode("utf-8") not in topic_data:
            raise ProtocolError(
                "state_corrupt", "bootstrap identity reread verification failed", context=context
            )
    return {
        "ok": True,
        "state": "started",
        "created": False,
        "idempotent_replay": True,
        "reread_verified": True,
        "storage_mode": storage_mode,
        "project_id": manifest["project_id"],
        "tree_id": manifest["tree_id"],
        "topic_id": manifest["topic_id"],
        "root_slug": manifest["root_slug"],
        "ledger_revision": 1,
        "topic_revision": 1,
        "event_count": 1,
        "project_manifest_path": str(manifest_path),
        "topic_document_path": str(topic_path),
        "ledger_path": str(ledger_path),
    }


def _bootstrap(request: dict[str, Any]) -> dict[str, Any]:
    entry_mode = request.get("entry_mode")
    if entry_mode == STATELESS_ENTRY_MODE:
        _expect_keys(
            request,
            {"protocol_version", "operation", "project_path", "entry_mode"},
            "ordinary-consultation request",
        )
        _validate_project_path(request["project_path"])
        return {"ok": True, "state": "stateless", "created": False}
    if entry_mode not in EXPLICIT_ENTRY_MODES:
        raise ProtocolError("invalid_entry_mode", "entry_mode is not an explicit 0讨论 trigger")
    _expect_keys(
        request,
        {
            "protocol_version",
            "operation",
            "project_path",
            "entry_mode",
            "conversation_ref",
            "idempotency_key",
            "root_slug",
        },
        "bootstrap request",
    )
    project = _validate_project_path(request["project_path"])
    conversation_ref = _expect_string(request["conversation_ref"], "conversation_ref")
    idempotency_key = _validate_uuid4(request["idempotency_key"], "idempotency_key")
    root_slug = _expect_string(request["root_slug"], "root_slug", max_bytes=96)
    if not ROOT_SLUG_RE.fullmatch(root_slug):
        raise ProtocolError(
            "invalid_root_slug",
            "root_slug must use lowercase ASCII words separated by single hyphens",
        )
    coordination_root, storage_mode = _coordination_root(project)
    request_fingerprint = _request_fingerprint(
        project=project,
        root_slug=root_slug,
        conversation_ref=conversation_ref,
        entry_mode=entry_mode,
    )
    created_files: list[Path] = []
    created_directories: list[Path] = []
    lock_root = coordination_root / "locks"
    try:
        _mkdirs(lock_root, created_directories)
        lock_path = lock_root / _project_lock_name(project)
        lock_existed = lock_path.exists()
        lock_stream = lock_path.open("a+b")
        if not lock_existed:
            created_files.append(lock_path)
        try:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            existing = _existing_response(
                project=project,
                coordination_root=coordination_root,
                storage_mode=storage_mode,
                conversation_ref=conversation_ref,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if existing is not None:
                created_files.clear()
                created_directories.clear()
                return existing

            project_id = _new_identity("project")
            tree_id = _new_identity("tree")
            topic_id = _new_identity("topic")
            manifest_path = project / "docs" / "discussions" / ".codex-project.md"
            topic_path = project / "docs" / "discussions" / root_slug / "topic.md"
            ledger_path = (
                coordination_root
                / "projects"
                / project_id
                / "trees"
                / tree_id
                / "ledger.md"
            )
            for parent in (manifest_path.parent, topic_path.parent, ledger_path.parent):
                _mkdirs(parent, created_directories)
            manifest_data = _render_project_manifest(
                project_id=project_id,
                tree_id=tree_id,
                topic_id=topic_id,
                root_slug=root_slug,
            )
            topic_data = _render_topic_document(
                project_id=project_id,
                tree_id=tree_id,
                topic_id=topic_id,
                root_slug=root_slug,
            )
            ledger_data = _render_ledger(
                project_id=project_id,
                tree_id=tree_id,
                topic_id=topic_id,
                root_slug=root_slug,
                conversation_ref=conversation_ref,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                project_manifest_path=manifest_path,
                topic_document_path=topic_path,
            )
            _write_new_file(ledger_path, ledger_data, created_files)
            _write_new_file(topic_path, topic_data, created_files)
            _write_new_file(manifest_path, manifest_data, created_files)
            reread = (
                ledger_path.read_bytes(),
                topic_path.read_bytes(),
                manifest_path.read_bytes(),
            )
            if reread != (ledger_data, topic_data, manifest_data):
                raise ProtocolError(
                    "initialization_verification_failed",
                    "bootstrap artifacts did not reread with their committed bytes",
                )
            for identity in (project_id, tree_id, topic_id):
                encoded = identity.encode("utf-8")
                if any(encoded not in data for data in reread):
                    raise ProtocolError(
                        "initialization_verification_failed",
                        "bootstrap identities did not reread across every authoritative artifact",
                    )
            return {
                "ok": True,
                "state": "started",
                "created": True,
                "idempotent_replay": False,
                "reread_verified": True,
                "storage_mode": storage_mode,
                "project_id": project_id,
                "tree_id": tree_id,
                "topic_id": topic_id,
                "root_slug": root_slug,
                "ledger_revision": 1,
                "topic_revision": 1,
                "event_count": 1,
                "project_manifest_path": str(manifest_path),
                "topic_document_path": str(topic_path),
                "ledger_path": str(ledger_path),
            }
        finally:
            try:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
            finally:
                lock_stream.close()
    except ProtocolError:
        _rollback(created_files, created_directories)
        raise
    except OSError as error:
        _rollback(created_files, created_directories)
        raise ProtocolError(
            "initialization_failed",
            "persistent discussion initialization stopped without publishing partial state",
            cause=str(error),
        ) from error


def handle(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise ProtocolError("invalid_request", "request must be a JSON object")
    if request.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(
            "unsupported_protocol_version",
            f"protocol_version must be {PROTOCOL_VERSION}",
        )
    if request.get("operation") != "bootstrap":
        raise ProtocolError("unsupported_operation", "Ticket 01 supports only bootstrap")
    return _bootstrap(request)


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        response = _response_error(
            ProtocolError("invalid_request", f"request exceeds {MAX_REQUEST_BYTES} bytes")
        )
        sys.stdout.write(_canonical_json(response) + "\n")
        return 1
    try:
        request = json.loads(raw)
        response = handle(request)
        returncode = 0
    except json.JSONDecodeError as error:
        protocol_error = ProtocolError("invalid_json", "stdin must contain one JSON value", cause=str(error))
        response = _response_error(protocol_error)
        print(protocol_error.message, file=sys.stderr)
        returncode = 1
    except ProtocolError as error:
        response = _response_error(error)
        print(error.message, file=sys.stderr)
        returncode = 1
    sys.stdout.write(_canonical_json(response) + "\n")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
