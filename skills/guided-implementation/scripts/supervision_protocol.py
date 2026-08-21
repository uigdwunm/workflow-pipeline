#!/usr/bin/env python3
"""Deterministic supervision transport and closure-checkpoint mechanics."""

from __future__ import annotations

import argparse
import base64
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from typing import Any


_CC_SWITCH_HOME = Path(
    os.environ.get("CC_SWITCH_HOME", Path.home() / ".cc-switch")
)
_CC_SWITCH_RUNTIME = Path(
    os.environ.get("CC_SWITCH_RUNTIME_ROOT", _CC_SWITCH_HOME / "runtime")
)
RUNTIME_ROOT = _CC_SWITCH_RUNTIME / "guided-implementation-handoffs"
CLOSURE_ROOT = _CC_SWITCH_RUNTIME / "change-closure-checkpoints"
HANDOFF_FILENAME = "handoff.json"
LEGACY_HANDOFF_VERSIONS = {1, 2, 3}
HANDOFF_VERSION = 4
LEASE_FILENAME = "cc-switch-guided-implementation-lease.json"
LEGACY_LEASE_VERSION = 1
LEGACY_LEASE_MODE = "exclusive-checkout-v1"
LEASE_VERSION = 2
LEASE_MODE = "exclusive-checkout-v2"
SUPPORTED_LEASE_IDENTITIES = {
    (LEGACY_LEASE_VERSION, LEGACY_LEASE_MODE),
    (LEASE_VERSION, LEASE_MODE),
}
MAX_LEASE_BYTES = 16_384
DOCUMENT_LEASE_FILENAME = "cc-switch-document-lease.json"
DOCUMENT_LEASE_GUARD_FILENAME = "cc-switch-document-lease.guard"
DOCUMENT_LEASE_VERSION = 1
DOCUMENT_LEASE_SCHEMA = 1
DOCUMENT_LEASE_PURPOSES = {"document-write", "git-stability-barrier"}
DOCUMENT_LEASE_STAGES = {
    "design-discussion",
    "problem-framing",
    "solution-design",
    "guided-implementation",
    "change-closure",
}
DOCUMENT_LEASE_WAIT_SECONDS = 10
DOCUMENT_LEASE_MAX_RETRIES = 10
MAX_DOCUMENT_LEASE_BYTES = 16_384
MAX_DOCUMENT_LEASE_TTL_SECONDS = 86_400
REPOSITORY_COORDINATION_LEASE_FILENAME = "cc-switch-repository-coordination-lease.json"
REPOSITORY_COORDINATION_LEASE_GUARD_FILENAME = "cc-switch-repository-coordination-lease.guard"
REPOSITORY_COORDINATION_LEASE_VERSION = 1
REPOSITORY_COORDINATION_LEASE_SCHEMA = 1
REPOSITORY_COORDINATION_LEASE_PURPOSES = {
    "checkpoint-publish",
    "serial-integration",
    "worktree-creation",
    "closure-critical-section",
}
REPOSITORY_COORDINATION_LEASE_STAGES = {
    "design-discussion",
    "guided-implementation",
    "change-closure",
}
MAX_REPOSITORY_COORDINATION_LEASE_TTL_SECONDS = 900
MAX_REPOSITORY_COORDINATION_LEASE_BYTES = 16_384
WORKTREE_EXECUTION_LEASE_DIRECTORY = "cc-switch-worktree-execution-leases"
WORKTREE_EXECUTION_LEASE_GUARD_FILENAME = "cc-switch-worktree-execution-leases.guard"
WORKTREE_EXECUTION_LEASE_VERSION = 1
WORKTREE_EXECUTION_LEASE_SCHEMA = 1
MAX_WORKTREE_EXECUTION_LEASE_BYTES = 32_768
MAX_WORKTREE_EXECUTION_LEASE_TTL_SECONDS = 604_800
WORKTREE_STATE_RECEIPT_DIRECTORY = "cc-switch-worktree-state-receipts"
WORKTREE_STATE_RECEIPT_VERSION = 1
ISOLATED_CONFIRMATION_DIRECTORY = "cc-switch-isolated-worktree-confirmations"
ISOLATED_CONFIRMATION_VERSION = 1
ISOLATED_USER_DECISION_SCHEMA = "isolated-worktree-user-decision-v1"
SUPERVISION_VERSION = 1
MANIFEST_VERSION = 1
LEGACY_CONTROL_VERSION = 1
EXPLICIT_ACK_CONTROL_VERSION = 2
STRUCTURED_ACK_CONTROL_VERSION = 3
DUPLEX_ACK_CONTROL_VERSION = 4
CONTROL_VERSION = 5
CARD_CONTROL_VERSION = 6
ACK_VERSION = 1
CLEANUP_VERSION = 1
LEGACY_CLOSURE_VERSION = 1
CLOSURE_VERSION = 2
ISOLATED_CLOSURE_VERSION = 3
MAX_HANDOFF_BYTES = 524_288
MAX_SUPERVISION_FILE_BYTES = 524_288
MAX_CONTROL_BYTES = 8_192
MAX_ACK_BYTES = 1_024
MAX_CLOSURE_CHECKPOINT_BYTES = 131_072
MAX_PAYLOAD_PART_BYTES = 380_000
MAX_PACKAGE_PARTS = 8
MAX_INPUT_BYTES = 4 * 1024 * 1024

HANDOFF_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
FOUR_DIGIT_RE = re.compile(r"^[0-9]{4}$")
KIND_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
SUPERVISION_NAME_RE = re.compile(
    r"^(parent-to-child|child-to-parent)-([0-9]{4})-"
    r"([0-9]{4})-of-([0-9]{4})\.json$"
)
DIRECTIONS = {"parent-to-child", "child-to-parent"}
CONTROL_STATUSES = {
    "PARENT_REVIEW_REQUIRED",
    "PARENT_DECISION_REQUIRED",
    "PARENT_BLOCKED",
    "PARENT_ACCEPTED_FOR_MERGE",
    "PARENT_REMEDIATION",
    "PARENT_DECISION",
    "PARENT_RETRY_MERGE",
    "MERGE_RESULT",
}
ACK_TYPE = "SUPERVISION_ACK"
LEGACY_ACK_POLICY = "supervision-ack-v1"
DUPLEX_ACK_POLICY = "supervision-duplex-ack-v1"
MESSAGE_PROTOCOL = "supervision-duplex-observe-v1"
DELIVERY_POLICY = MESSAGE_PROTOCOL
MESSAGE_TYPE = "SUPERVISION_MESSAGE"
ACK_FORMAT = "ACK <message_id>"
ACK_RESEND_LIMIT = 2
DELIVERY_CHECK_DELAY_SECONDS = 5
DELIVERY_RESEND_LIMIT = 1
LEGACY_ACK_INSTRUCTION = (
    "ACK REQUIRED: Authenticate and verify this control, then run create-ack "
    "and send its canonical SUPERVISION_ACK before applying the control."
)
ACK_INSTRUCTION = (
    "ACK REQUIRED BEFORE ACTION: send the exact ack_text to the authenticated "
    "sender first. ACK confirms delivery only, not task completion or result "
    "acceptance. After ACK, continue processing the payload. ACK messages are "
    "never ACKed."
)
DELIVERY_INSTRUCTION = (
    "DELIVERY CHECK REQUIRED: after sending, wait 5 seconds and inspect the "
    "authenticated target task once for the exact canonical message and "
    "message_id. Resend the byte-identical message once only when exact absence "
    "is proven, then wait 5 seconds and inspect once more. Do not send an ACK. "
    "Apply each verified message_id at most once."
)
EXPLICIT_ACK_PROTOCOL_MARKER = "`ack_required: true`"
ACK_POLICY_PROTOCOL_MARKER = "`ack_policy: supervision-ack-v1`"
DUPLEX_ACK_PROTOCOL_MARKER = "`ack_policy: supervision-duplex-ack-v1`"
DELIVERY_OBSERVE_PROTOCOL_MARKER = (
    "`delivery_policy: supervision-duplex-observe-v1`"
)
MESSAGE_CARD_FORMAT = "supervision-chinese-card-v1"
MESSAGE_CARD_PROTOCOL_MARKER = (
    "`message_format: supervision-chinese-card-v1`"
)
DIRECTION_LABELS = {
    "parent-to-child": "主任务 → 专用任务",
    "child-to-parent": "专用任务 → 主任务",
}
STATUS_LABELS = {
    "PARENT_REVIEW_REQUIRED": "请求主任务审查",
    "PARENT_DECISION_REQUIRED": "请求主任务决策",
    "PARENT_BLOCKED": "专用任务遇到阻塞",
    "MERGE_RESULT": "报告合并结果",
    "PARENT_REMEDIATION": "主任务要求修正",
    "PARENT_DECISION": "主任务下达决策",
    "PARENT_ACCEPTED_FOR_MERGE": "主任务接受并授权合并",
    "PARENT_RETRY_MERGE": "主任务要求重试合并",
}
MESSAGE_ROUTES = {
    ("child-to-parent", "candidate-report"): "PARENT_REVIEW_REQUIRED",
    ("child-to-parent", "decision-request"): "PARENT_DECISION_REQUIRED",
    ("child-to-parent", "blocker-report"): "PARENT_BLOCKED",
    ("child-to-parent", "merge-result"): "MERGE_RESULT",
    ("parent-to-child", "parent-remediation"): "PARENT_REMEDIATION",
    ("parent-to-child", "parent-decision"): "PARENT_DECISION",
    ("parent-to-child", "parent-merge-acceptance"): "PARENT_ACCEPTED_FOR_MERGE",
    ("parent-to-child", "parent-merge-retry"): "PARENT_RETRY_MERGE",
}
LEGACY_CLOSURE_PHASES = (
    "prepared",
    "documents-committed",
    "worktree-removed",
    "branch-removed",
    "remote-verified",
    "evidence-cleanup",
    "complete",
)
CLOSURE_PHASES = (
    "prepared",
    "documents-committed",
    "branch-removed",
    "lease-released",
    "remote-verified",
    "evidence-cleanup",
    "complete",
)
ISOLATED_CLOSURE_PHASES = (
    "prepared",
    "documents-committed",
    "worktree-removed",
    "branch-removed",
    "execution-lease-released",
    "remote-verified",
    "evidence-cleanup",
    "complete",
)


class ProtocolError(ValueError):
    """Raised when protocol data or filesystem state is invalid."""

    def __init__(
        self,
        code_or_message: str,
        message: str | None = None,
        *,
        retryable: bool = False,
        cause: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.code = code_or_message if message is not None else "supervision_protocol_error"
        self.message = message if message is not None else code_or_message
        self.retryable = retryable
        self.cause = cause
        self.context = context or {}
        super().__init__(self.message)


def _error_response(error: ProtocolError, command: str) -> dict[str, Any]:
    chinese = {
        "receipt_identity_mismatch": "凭证不属于当前仓库、项目、话题或版本。",
        "receipt_cas_mismatch": "凭证字节、摘要或 CAS 版本已变化。",
        "receipt_stale": "权威状态已变化，必须重新生成凭证。",
        "isolated_confirmation_required": "缺少监督协议持久化的精确 isolated 创建确认。",
        "repository_coordination_required": "创建 isolated worktree 必须持有精确的仓库协调租约。",
        "worktree_creation_uncoordinated": "worktree 不是在已确认的创建临界区内生成。",
        "discussion_receipt_invalid": "讨论协议未能验证当前来源、依赖或活动运行凭证。",
        "integration_state_stale": "集成前的权威来源、依赖、活动运行或 Git 状态已变化。",
        "outcome_unknown": "操作结果未知，必须先检查权威状态再恢复。",
        "execution_availability_invalid": "执行可用性请求包含不支持的执行模式。",
        "integration_lease_invalid": "集成操作未持有精确的串行集成协调租约。",
        "worktree_branch_mismatch": "精确 worktree 的分支或基础提交与冻结绑定不一致。",
        "worktree_exact_missing": "冻结绑定中的精确 worktree 不存在。",
        "worktree_lease_release_cas_mismatch": "worktree execution lease 的释放 CAS 已变化。",
        "worktree_reconciliation_invalid_outcome": "worktree execution lease 对账结果不受支持。",
        "document_lease_invalid": "文档提案没有精确、有效的 4归档文档租约。",
        "document_proposal_conflict": "基础 checkout 中的文档已偏离提案基线，必须人工收敛。",
    }
    return {
        "ok": False,
        "state": error.context.get("state", "stopped"),
        "command": command,
        "repository": error.context.get("repository"),
        "topic_id": error.context.get("topic_id"),
        "receipt_id": error.context.get("receipt_id"),
        "current": error.context.get("current"),
        "error": {
            "code": error.code,
            "message": error.message,
            "message_zh": chinese.get(error.code, "监督协议操作失败。"),
            "retryable": error.retryable,
            "cause": error.cause,
        },
    }


def converge_document_proposal(input_path: Path) -> dict[str, Any]:
    """Converge one immutable proposal into the verified base checkout.

    The proposal is input-only: the only writable path is the normalized target
    below ``repository``.  Equality with the frozen base means apply, equality
    with the proposal means no-op, and every other state is an explicit
    three-way conflict.
    """
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                Path(input_path), max_bytes=MAX_INPUT_BYTES, label="document proposal input"
            ),
            "document proposal input",
        ),
        "document proposal input",
    )
    _expect_keys(
        source,
        {
            "base_sha256",
            "document_lease",
            "proposal_path",
            "proposal_sha256",
            "repository",
            "target_path",
        },
        "document proposal input",
    )
    repository = _expect_absolute_path(source["repository"], "repository")
    if not (repository / ".git").is_dir():
        raise ProtocolError(
            "document_lease_invalid",
            "document proposal convergence requires the ordinary Git checkout",
            context={"repository": str(repository)},
        )
    lease = _expect_object(source["document_lease"], "document_lease")
    _expect_keys(lease, {"lease_id", "path", "version"}, "document_lease")
    lease_path = _expect_absolute_path(lease["path"], "document_lease.path")
    if lease_path != _document_lease_path(repository):
        raise ProtocolError(
            "document_lease_invalid",
            "document lease does not belong to the base checkout",
            context={"repository": str(repository)},
        )
    verified = verify_document_lease(
        lease_path,
        expected_id=_expect_handoff_id(lease["lease_id"], "document_lease.lease_id"),
        expected_version=_expect_int(
            lease["version"], "document_lease.version", 1, 2**63 - 2
        ),
    )
    holder = verified.get("holder")
    if (
        verified.get("verified") is not True
        or not isinstance(holder, dict)
        or holder.get("stage") != "change-closure"
        or holder.get("purpose") != "document-write"
    ):
        raise ProtocolError(
            "document_lease_invalid",
            "document lease is not a live change-closure document-write lease",
            context={"repository": str(repository)},
        )

    target_text = _expect_nonempty_string(
        source["target_path"], "target_path", max_bytes=8_192
    )
    target_relative = Path(target_text)
    if target_relative.is_absolute() or target_relative == Path(".") or ".." in target_relative.parts:
        raise ProtocolError("document_lease_invalid", "target_path must be repository-relative")
    target = repository / target_relative
    resolved_parent = target.parent.resolve(strict=True)
    try:
        resolved_parent.relative_to(repository)
    except ValueError as error:
        raise ProtocolError("document_lease_invalid", "target_path escapes repository") from error
    if resolved_parent != target.parent:
        raise ProtocolError("document_lease_invalid", "target_path contains a symbolic-link parent")

    proposal_path = _expect_absolute_path(source["proposal_path"], "proposal_path")
    proposal = _read_regular_file(
        proposal_path, max_bytes=MAX_INPUT_BYTES, label="document proposal"
    )
    proposal_sha256 = _expect_sha256(source["proposal_sha256"], "proposal_sha256")
    if _sha256(proposal) != proposal_sha256:
        raise ProtocolError("receipt_cas_mismatch", "document proposal digest changed")
    base_sha256 = _expect_sha256(source["base_sha256"], "base_sha256")
    current = _read_regular_file(target, max_bytes=MAX_INPUT_BYTES, label="target document")
    current_sha256 = _sha256(current)
    if current_sha256 == proposal_sha256:
        outcome = "no-op"
    elif current_sha256 == base_sha256:
        target_stat = os.lstat(target)
        if not stat.S_ISREG(target_stat.st_mode) or target_stat.st_nlink != 1:
            raise ProtocolError("outcome_unknown", "target identity is unsafe for replacement")
        directory_fd = os.open(target.parent, os.O_RDONLY)
        temp_name = f".codex-archive-{secrets.token_hex(16)}.tmp"
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            temp_fd = os.open(temp_name, flags, _mode_bits(target_stat), dir_fd=directory_fd)
            try:
                _write_all(temp_fd, proposal)
                os.fsync(temp_fd)
            finally:
                os.close(temp_fd)
            os.replace(temp_name, target.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.close(directory_fd)
        if _sha256(_read_regular_file(target, max_bytes=MAX_INPUT_BYTES, label="target document")) != proposal_sha256:
            raise ProtocolError("outcome_unknown", "document proposal apply postcondition is unknown")
        outcome = "applied"
    else:
        raise ProtocolError(
            "document_proposal_conflict",
            "target differs from both proposal base and proposed bytes",
            context={
                "repository": str(repository),
                "current": {
                    "base_sha256": base_sha256,
                    "current_sha256": current_sha256,
                    "proposal_sha256": proposal_sha256,
                    "target_path": target_text,
                },
            },
        )
    return {
        "ok": True,
        "state": "converged",
        "outcome": outcome,
        "repository": str(repository),
        "target_path": target_text,
        "base_sha256": base_sha256,
        "proposal_sha256": proposal_sha256,
        "document_lease": {
            "lease_id": holder["lease_id"],
            "path": str(lease_path),
            "version": verified["version"],
        },
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_float(value: str) -> None:
    raise ProtocolError(f"JSON floating-point value is not allowed: {value}")


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"non-standard JSON constant is not allowed: {value}")


def _validate_json_tree(value: Any, label: str = "JSON value") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise ProtocolError(f"{label} integer is outside signed 64-bit range: {value}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_tree(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError(f"{label} contains a non-string key: {key!r}")
            _validate_json_tree(item, f"{label}.{key}")
        return
    raise ProtocolError(f"{label} contains unsupported type: {type(value).__name__}")


def _canonical_json_bytes(value: Any, *, trailing_newline: bool = True) -> bytes:
    _validate_json_tree(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded + (b"\n" if trailing_newline else b"")


def _load_json_bytes(
    data: bytes, label: str, *, require_canonical: bool = False
) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"{label} is not valid UTF-8: {error}") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ProtocolError:
        raise
    except json.JSONDecodeError as error:
        raise ProtocolError(f"{label} is not valid JSON: {error}") from error
    _validate_json_tree(value, label)
    if require_canonical and data != _canonical_json_bytes(value):
        raise ProtocolError(f"{label} is not canonical JSON with one trailing newline")
    return value


def _expect_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object; observed {type(value).__name__}")
    return value


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolError(f"{label} must be an array; observed {type(value).__name__}")
    return value


def _expect_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        unknown = sorted(observed - expected)
        raise ProtocolError(
            f"{label} keys mismatch; missing={missing!r}; unknown={unknown!r}"
        )


def _expect_string(value: Any, label: str, *, max_bytes: int | None = None) -> str:
    if not isinstance(value, str):
        raise ProtocolError(f"{label} must be a string; observed {type(value).__name__}")
    if max_bytes is not None and len(value.encode("utf-8")) > max_bytes:
        raise ProtocolError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _expect_nonempty_string(
    value: Any, label: str, *, max_bytes: int | None = None
) -> str:
    text = _expect_string(value, label, max_bytes=max_bytes)
    if not text:
        raise ProtocolError(f"{label} must not be empty")
    return text


def _expect_single_line(
    value: Any, label: str, *, max_bytes: int | None = None
) -> str:
    text = _expect_nonempty_string(value, label, max_bytes=max_bytes)
    if "\n" in text or "\r" in text:
        raise ProtocolError(f"{label} must be one line")
    return text


def _expect_chinese_line(
    value: Any, label: str, *, max_bytes: int | None = None
) -> str:
    text = _expect_single_line(value, label, max_bytes=max_bytes)
    if CJK_RE.search(text) is None:
        raise ProtocolError(f"{label} must contain Chinese text")
    return text


def _expect_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError(
            f"{label} must be an integer in [{minimum}, {maximum}]; observed {value!r}"
        )
    return value


def _expect_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ProtocolError(
            f"{label} must be a boolean; observed {type(value).__name__}"
        )
    return value


def _expect_string_list(
    value: Any,
    label: str,
    *,
    max_items: int = 100,
    max_item_bytes: int = 8_192,
) -> list[str]:
    items = _expect_list(value, label)
    if len(items) > max_items:
        raise ProtocolError(
            f"{label} exceeds {max_items} items; observed={len(items)}"
        )
    return [
        _expect_string(item, f"{label}[{index}]", max_bytes=max_item_bytes)
        for index, item in enumerate(items)
    ]


def _path_is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _expect_sha256(value: Any, label: str) -> str:
    text = _expect_string(value, label)
    if not SHA256_RE.fullmatch(text):
        raise ProtocolError(f"{label} must be 64 lowercase hexadecimal characters: {text!r}")
    return text


def _expect_git_oid(value: Any, label: str) -> str:
    text = _expect_string(value, label)
    if not GIT_OID_RE.fullmatch(text):
        raise ProtocolError(
            f"{label} must be a full 40- or 64-character lowercase Git object ID: {text!r}"
        )
    return text


def _expect_handoff_id(value: Any, label: str = "handoff_id") -> str:
    text = _expect_string(value, label)
    if not HANDOFF_ID_RE.fullmatch(text):
        raise ProtocolError(f"{label} must match ^[0-9a-f]{{32}}$: {text!r}")
    return text


def _expect_four_digits(value: Any, label: str, *, minimum: int = 1) -> str:
    text = _expect_string(value, label)
    if not FOUR_DIGIT_RE.fullmatch(text):
        raise ProtocolError(f"{label} must contain exactly four decimal digits: {text!r}")
    number = int(text)
    if number < minimum:
        raise ProtocolError(f"{label} must be at least {minimum:04d}: {text!r}")
    return text


def _mode_bits(file_stat: os.stat_result) -> int:
    return stat.S_IMODE(file_stat.st_mode)


def _read_regular_file(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    require_owner: bool = True,
    require_single_link: bool = True,
) -> bytes:
    if not path.is_absolute():
        raise ProtocolError(f"{label} path must be absolute: {path}")
    try:
        initial = os.lstat(path)
    except OSError as error:
        raise ProtocolError(f"cannot lstat {label} {path}: {error}") from error
    if not stat.S_ISREG(initial.st_mode):
        raise ProtocolError(f"{label} must be a regular non-symlink file: {path}")
    if require_owner and initial.st_uid != os.getuid():
        raise ProtocolError(
            f"{label} owner mismatch at {path}; expected uid={os.getuid()}; observed={initial.st_uid}"
        )
    if require_single_link and initial.st_nlink != 1:
        raise ProtocolError(
            f"{label} link count mismatch at {path}; expected=1; observed={initial.st_nlink}"
        )
    if not 0 <= initial.st_size <= max_bytes:
        raise ProtocolError(
            f"{label} size out of range at {path}; expected=0..{max_bytes}; observed={initial.st_size}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(path, flags)
    except OSError as error:
        raise ProtocolError(f"cannot open {label} without following links {path}: {error}") from error
    try:
        opened = os.fstat(file_fd)
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise ProtocolError(f"{label} changed between lstat and open: {path}")
        if opened.st_size != initial.st_size:
            raise ProtocolError(
                f"{label} size changed between lstat and open at {path}; initial={initial.st_size}; opened={opened.st_size}"
            )
        if not 0 <= opened.st_size <= max_bytes:
            raise ProtocolError(
                f"{label} opened size out of range at {path}; expected=0..{max_bytes}; observed={opened.st_size}"
            )
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(file_fd, min(remaining, 1024 * 1024))
            if not chunk:
                raise ProtocolError(f"unexpected EOF while reading {label}: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            raise ProtocolError(f"{label} grew while being read: {path}")
        final = os.fstat(file_fd)
        if (final.st_dev, final.st_ino, final.st_size) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ):
            raise ProtocolError(f"{label} changed while being read: {path}")
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def _validate_directory_stat(
    directory_stat: os.stat_result, path: Path, *, required_mode: int
) -> None:
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise ProtocolError(f"required directory is not a real directory: {path}")
    if directory_stat.st_uid != os.getuid():
        raise ProtocolError(
            f"directory owner mismatch at {path}; expected uid={os.getuid()}; observed={directory_stat.st_uid}"
        )
    if _mode_bits(directory_stat) != required_mode:
        raise ProtocolError(
            f"directory mode mismatch at {path}; expected={required_mode:04o}; observed={_mode_bits(directory_stat):04o}"
        )


def _open_directory(path: Path, *, required_mode: int = 0o700) -> int:
    if not path.is_absolute():
        raise ProtocolError(f"directory path must be absolute: {path}")
    if Path(os.path.realpath(path)) != path:
        raise ProtocolError(f"directory path contains a symbolic-link component: {path}")
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProtocolError(f"cannot lstat directory {path}: {error}") from error
    _validate_directory_stat(before, path, required_mode=required_mode)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(path, flags)
    except OSError as error:
        raise ProtocolError(f"cannot open directory without following links {path}: {error}") from error
    opened = os.fstat(directory_fd)
    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        os.close(directory_fd)
        raise ProtocolError(f"directory changed between lstat and open: {path}")
    _validate_directory_stat(opened, path, required_mode=required_mode)
    return directory_fd


def _ensure_runtime_root(runtime_root: Path) -> int:
    runtime_root = Path(runtime_root)
    if not runtime_root.is_absolute():
        raise ProtocolError(f"runtime root must be absolute: {runtime_root}")
    runtime_parent = runtime_root.parent
    try:
        os.lstat(runtime_parent)
    except FileNotFoundError:
        parent_container = runtime_parent.parent
        if Path(os.path.realpath(parent_container)) != parent_container:
            raise ProtocolError(
                f"runtime parent container contains a symbolic-link component: {parent_container}"
            )
        try:
            container_stat = os.lstat(parent_container)
        except OSError as error:
            raise ProtocolError(
                f"cannot inspect runtime parent container {parent_container}: {error}"
            ) from error
        if not stat.S_ISDIR(container_stat.st_mode):
            raise ProtocolError(
                f"runtime parent container is not a real directory: {parent_container}"
            )
        if container_stat.st_uid != os.getuid():
            raise ProtocolError(
                f"runtime parent container owner mismatch at {parent_container}; expected uid={os.getuid()}; observed={container_stat.st_uid}"
            )
        if _mode_bits(container_stat) & 0o022:
            raise ProtocolError(
                f"runtime parent container must not be group/world writable: {parent_container}"
            )
        try:
            os.mkdir(runtime_parent, 0o700)
        except OSError as error:
            raise ProtocolError(
                f"cannot create private runtime parent {runtime_parent}: {error}"
            ) from error
    except OSError as error:
        raise ProtocolError(f"cannot lstat runtime parent {runtime_parent}: {error}") from error
    parent_fd = _open_directory(runtime_parent)
    os.close(parent_fd)
    if Path(os.path.realpath(runtime_parent)) != runtime_parent:
        raise ProtocolError(
            f"runtime root parent contains a symbolic-link component: {runtime_parent}"
        )
    try:
        os.lstat(runtime_root)
    except FileNotFoundError:
        try:
            os.mkdir(runtime_root, 0o700)
        except OSError as error:
            raise ProtocolError(f"cannot create runtime root {runtime_root}: {error}") from error
    except OSError as error:
        raise ProtocolError(f"cannot lstat runtime root {runtime_root}: {error}") from error
    return _open_directory(runtime_root)


def _open_id_directory(runtime_root: Path, handoff_id: str) -> tuple[int, int]:
    _expect_handoff_id(handoff_id)
    root_fd = _open_directory(Path(runtime_root))
    id_path = Path(runtime_root) / handoff_id
    try:
        id_stat = os.stat(handoff_id, dir_fd=root_fd, follow_symlinks=False)
    except OSError as error:
        os.close(root_fd)
        raise ProtocolError(f"cannot inspect handoff directory {id_path}: {error}") from error
    try:
        _validate_directory_stat(id_stat, id_path, required_mode=0o700)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        id_fd = os.open(handoff_id, flags, dir_fd=root_fd)
        opened = os.fstat(id_fd)
        if (opened.st_dev, opened.st_ino) != (id_stat.st_dev, id_stat.st_ino):
            os.close(id_fd)
            raise ProtocolError(f"handoff directory changed while opening: {id_path}")
        _validate_directory_stat(opened, id_path, required_mode=0o700)
        return root_fd, id_fd
    except Exception:
        os.close(root_fd)
        raise


def _read_runtime_file(
    directory_fd: int,
    name: str,
    *,
    max_bytes: int,
    label: str,
    required_mode: int = 0o400,
) -> tuple[bytes, os.stat_result]:
    if not name or name in {".", ".."} or "/" in name:
        raise ProtocolError(f"{label} must use one basename: {name!r}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise ProtocolError(f"cannot open {label} {name!r}: {error}") from error
    try:
        file_stat = os.fstat(file_fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ProtocolError(f"{label} is not a regular file: {name!r}")
        if file_stat.st_uid != os.getuid():
            raise ProtocolError(
                f"{label} owner mismatch for {name!r}; expected uid={os.getuid()}; observed={file_stat.st_uid}"
            )
        if file_stat.st_nlink != 1:
            raise ProtocolError(
                f"{label} link count mismatch for {name!r}; expected=1; observed={file_stat.st_nlink}"
            )
        if _mode_bits(file_stat) != required_mode:
            raise ProtocolError(
                f"{label} mode mismatch for {name!r}; expected={required_mode:04o}; observed={_mode_bits(file_stat):04o}"
            )
        if not 1 <= file_stat.st_size <= max_bytes:
            raise ProtocolError(
                f"{label} size out of range for {name!r}; expected=1..{max_bytes}; observed={file_stat.st_size}"
            )
        chunks: list[bytes] = []
        remaining = file_stat.st_size
        while remaining:
            chunk = os.read(file_fd, min(remaining, 1024 * 1024))
            if not chunk:
                raise ProtocolError(f"unexpected EOF while reading {label} {name!r}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            raise ProtocolError(f"{label} grew while being read: {name!r}")
        final = os.fstat(file_fd)
        if (final.st_dev, final.st_ino, final.st_size) != (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_size,
        ):
            raise ProtocolError(f"{label} changed while being read: {name!r}")
        return b"".join(chunks), final
    finally:
        os.close(file_fd)


def _write_all(file_fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(file_fd, data[offset:])
        if written <= 0:
            raise ProtocolError("short write while staging immutable protocol file")
        offset += written


def _publish_group(directory_fd: int, files: list[tuple[str, bytes]]) -> None:
    if not files:
        raise ProtocolError("publish group must contain at least one file")
    final_names = [name for name, _ in files]
    if len(set(final_names)) != len(final_names):
        raise ProtocolError(f"publish group contains duplicate names: {final_names!r}")
    for name, data in files:
        if not name or name in {".", ".."} or "/" in name:
            raise ProtocolError(f"publish target must be one basename: {name!r}")
        if not data:
            raise ProtocolError(f"publish target cannot be empty: {name!r}")
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ProtocolError(f"cannot preflight publish target {name!r}: {error}") from error
        else:
            raise ProtocolError(f"publish target already exists: {name!r}")

    staged: list[tuple[str, str]] = []
    linked: list[str] = []
    try:
        for final_name, data in files:
            temp_name = f".tmp-{secrets.token_hex(16)}"
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
            )
            temp_fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
            try:
                _write_all(temp_fd, data)
                os.fsync(temp_fd)
                os.fchmod(temp_fd, 0o400)
                os.fsync(temp_fd)
                temp_stat = os.fstat(temp_fd)
                if not stat.S_ISREG(temp_stat.st_mode) or _mode_bits(temp_stat) != 0o400:
                    raise ProtocolError(f"staged file mode verification failed: {temp_name!r}")
                if temp_stat.st_uid != os.getuid() or temp_stat.st_nlink != 1:
                    raise ProtocolError(f"staged file identity verification failed: {temp_name!r}")
            finally:
                os.close(temp_fd)
            staged.append((temp_name, final_name))

        for temp_name, final_name in staged:
            os.link(
                temp_name,
                final_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            linked.append(final_name)

        for temp_name, _ in staged:
            os.unlink(temp_name, dir_fd=directory_fd)
        staged.clear()
        os.fsync(directory_fd)
    except Exception as error:
        for final_name in reversed(linked):
            try:
                os.unlink(final_name, dir_fd=directory_fd)
            except OSError:
                pass
        for temp_name, _ in staged:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except OSError:
                pass
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError(f"immutable no-replace publication failed: {error}") from error


def _replace_private_file(
    directory_fd: int,
    *,
    name: str,
    data: bytes,
    max_bytes: int,
    label: str,
) -> None:
    if not name or name in {".", ".."} or "/" in name:
        raise ProtocolError(f"{label} must use one basename: {name!r}")
    if not 1 <= len(data) <= max_bytes:
        raise ProtocolError(
            f"{label} replacement size out of range; expected=1..{max_bytes}; observed={len(data)}"
        )
    _, original = _read_runtime_file(
        directory_fd,
        name,
        max_bytes=max_bytes,
        label=label,
    )
    temp_name = f".tmp-{secrets.token_hex(16)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    temp_fd = -1
    try:
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        _write_all(temp_fd, data)
        os.fsync(temp_fd)
        os.fchmod(temp_fd, 0o400)
        os.fsync(temp_fd)
        staged = os.fstat(temp_fd)
        if not stat.S_ISREG(staged.st_mode) or _mode_bits(staged) != 0o400:
            raise ProtocolError(
                f"staged {label} mode verification failed: {temp_name!r}"
            )
        if staged.st_uid != os.getuid() or staged.st_nlink != 1:
            raise ProtocolError(
                f"staged {label} identity verification failed: {temp_name!r}"
            )
        os.close(temp_fd)
        temp_fd = -1
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            raise ProtocolError(f"{label} changed before atomic replacement: {name!r}")
        os.replace(
            temp_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temp_name = ""
        os.fsync(directory_fd)
    except Exception as error:
        if isinstance(error, ProtocolError):
            raise
        raise ProtocolError(f"atomic {label} replacement failed: {error}") from error
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _encode_json_record(value: Any) -> dict[str, Any]:
    payload = _canonical_json_bytes(value)
    return {
        "b64": base64.b64encode(payload).decode("ascii"),
        "bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _encode_text_record(payload: bytes) -> dict[str, Any]:
    return {
        "b64": base64.b64encode(payload).decode("ascii"),
        "bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _decode_record(record_value: Any, label: str) -> bytes:
    record = _expect_object(record_value, label)
    _expect_keys(record, {"b64", "bytes", "sha256"}, label)
    expected_bytes = _expect_int(record["bytes"], f"{label}.bytes", 1, MAX_INPUT_BYTES)
    expected_sha = _expect_sha256(record["sha256"], f"{label}.sha256")
    encoded = _expect_string(record["b64"], f"{label}.b64", max_bytes=MAX_INPUT_BYTES * 2)
    try:
        payload = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise ProtocolError(f"{label}.b64 is not strict base64: {error}") from error
    if base64.b64encode(payload).decode("ascii") != encoded:
        raise ProtocolError(f"{label}.b64 is not canonical base64")
    if len(payload) != expected_bytes:
        raise ProtocolError(
            f"{label} decoded byte count mismatch; expected={expected_bytes}; observed={len(payload)}"
        )
    observed_sha = _sha256(payload)
    if observed_sha != expected_sha:
        raise ProtocolError(
            f"{label} decoded SHA-256 mismatch; expected={expected_sha}; observed={observed_sha}"
        )
    return payload


def _handoff_metadata(path: Path, data: bytes, handoff_id: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "complete": f"HANDOFF_COMPLETE:{handoff_id}",
    }


def _validate_implementation_source_checkpoint(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(source, {"checkpoint_id", "commit_id", "committed", "read_only", "sha256"}, label)
    if not _expect_bool(source["committed"], f"{label}.committed") or not _expect_bool(source["read_only"], f"{label}.read_only"):
        raise ProtocolError(f"{label} must be committed and read-only")
    return {
        "checkpoint_id": _expect_nonempty_string(source["checkpoint_id"], f"{label}.checkpoint_id", max_bytes=256),
        "commit_id": _expect_git_oid(source["commit_id"], f"{label}.commit_id"),
        "committed": True,
        "read_only": True,
        "sha256": _expect_sha256(source["sha256"], f"{label}.sha256"),
    }


def _validate_v4_handoff_envelope(value: Any) -> dict[str, Any]:
    envelope = _expect_object(value, "handoff envelope")
    required = {"execution_mode", "implementation_source_checkpoint", "repository"}
    missing = required - set(envelope)
    if missing:
        raise ProtocolError(f"handoff envelope is missing v4 execution fields: {sorted(missing)!r}")
    mode = _expect_nonempty_string(envelope["execution_mode"], "handoff envelope.execution_mode", max_bytes=128)
    repository = _expect_absolute_path(envelope["repository"], "handoff envelope.repository")
    _validate_implementation_source_checkpoint(envelope["implementation_source_checkpoint"], "handoff envelope.implementation_source_checkpoint")
    if mode == LEASE_MODE:
        required_v2 = {"checkout_path", "repository_lease", "worktree_count"}
        missing_v2 = required_v2 - set(envelope)
        if missing_v2:
            raise ProtocolError(f"exclusive handoff is missing fields: {sorted(missing_v2)!r}")
        checkout_path = _expect_absolute_path(envelope["checkout_path"], "handoff envelope.checkout_path")
        if checkout_path != repository or envelope["worktree_count"] != 0:
            raise ProtocolError("exclusive handoff must bind the ordinary checkout and zero worktrees")
        lease = _expect_object(envelope["repository_lease"], "handoff envelope.repository_lease")
        required_lease = {"lease_id", "mode", "path"}
        if not required_lease.issubset(lease):
            raise ProtocolError("exclusive handoff repository lease identity is incomplete")
        if lease["mode"] != LEASE_MODE or _expect_handoff_id(lease["lease_id"], "repository lease ID") != lease["lease_id"]:
            raise ProtocolError("exclusive handoff repository lease identity is invalid")
        expected_path = repository / ".git" / LEASE_FILENAME
        if _expect_absolute_path(lease["path"], "repository lease path") != expected_path:
            raise ProtocolError("exclusive handoff repository lease path is not exact")
    elif mode == "isolated-worktree-v1":
        if "isolated_worktree" not in envelope:
            raise ProtocolError("isolated handoff is missing isolated_worktree")
        isolated = _expect_object(envelope["isolated_worktree"], "handoff envelope.isolated_worktree")
        _expect_keys(isolated, {"base_commit", "branch", "execution_lease", "parallelism_receipt", "scope_sha256", "sensitive_shared_surfaces", "worktree_path"}, "handoff envelope.isolated_worktree")
        _expect_git_oid(isolated["base_commit"], "isolated base_commit")
        _expect_nonempty_string(isolated["branch"], "isolated branch", max_bytes=1024)
        worktree_path = _expect_absolute_path(isolated["worktree_path"], "isolated worktree_path")
        _expect_sha256(isolated["parallelism_receipt"], "isolated parallelism_receipt")
        _expect_sha256(isolated["scope_sha256"], "isolated scope_sha256")
        if not _expect_string_list(isolated["sensitive_shared_surfaces"], "isolated sensitive_shared_surfaces"):
            raise ProtocolError("isolated sensitive shared surfaces must not be empty")
        lease = _expect_object(isolated["execution_lease"], "isolated execution_lease")
        _expect_keys(lease, {"lease_id", "path", "version"}, "isolated execution_lease")
        _expect_handoff_id(lease["lease_id"], "isolated execution lease ID")
        _expect_int(lease["version"], "isolated execution lease version", 1, 2**63 - 2)
        expected_directory = repository / ".git" / WORKTREE_EXECUTION_LEASE_DIRECTORY
        lease_path = _expect_absolute_path(lease["path"], "isolated execution lease path")
        if lease_path.parent != expected_directory or lease_path.suffix != ".json":
            raise ProtocolError("isolated execution lease path is not in the repository lease directory")
        if worktree_path == repository:
            raise ProtocolError("isolated worktree must differ from the ordinary checkout")
    else:
        raise ProtocolError(f"handoff execution mode is unsupported: {mode!r}")
    return envelope


def create_handoff(
    input_path: Path,
    protocol_path: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    input_data = _read_regular_file(
        Path(input_path), max_bytes=MAX_INPUT_BYTES, label="handoff input"
    )
    source = _expect_object(_load_json_bytes(input_data, "handoff input"), "handoff input")
    _expect_keys(source, {"envelope", "work_items", "artifacts"}, "handoff input")
    envelope = _expect_object(source["envelope"], "handoff input.envelope")
    _validate_v4_handoff_envelope(envelope)
    work_items = _expect_list(source["work_items"], "handoff input.work_items")
    artifacts = _expect_list(source["artifacts"], "handoff input.artifacts")
    if not work_items:
        raise ProtocolError("handoff input.work_items must contain at least one Work Item")
    for index, item in enumerate(work_items):
        _expect_object(item, f"handoff input.work_items[{index}]")
    for index, artifact in enumerate(artifacts):
        _expect_object(artifact, f"handoff input.artifacts[{index}]")

    protocol = _read_regular_file(
        Path(protocol_path), max_bytes=MAX_HANDOFF_BYTES, label="execution protocol"
    )
    try:
        protocol.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"execution protocol is not valid UTF-8: {error}") from error
    if not protocol.endswith(b"\n"):
        raise ProtocolError("execution protocol must end with one newline")

    handoff_id = secrets.token_hex(16)
    handoff = {
        "artifact_count": len(artifacts),
        "artifacts": [_encode_json_record(item) for item in artifacts],
        "complete": f"HANDOFF_COMPLETE:{handoff_id}",
        "envelope": _encode_json_record(envelope),
        "execution_protocol": _encode_text_record(protocol),
        "handoff_id": handoff_id,
        "handoff_version": HANDOFF_VERSION,
        "work_item_count": len(work_items),
        "work_items": [_encode_json_record(item) for item in work_items],
    }
    handoff_data = _canonical_json_bytes(handoff)
    if not 1 <= len(handoff_data) <= MAX_HANDOFF_BYTES:
        raise ProtocolError(
            f"canonical handoff size must be 1..{MAX_HANDOFF_BYTES}; observed={len(handoff_data)}"
        )

    runtime_root = Path(runtime_root)
    root_fd = _ensure_runtime_root(runtime_root)
    id_created = False
    try:
        try:
            os.mkdir(handoff_id, 0o700, dir_fd=root_fd)
            id_created = True
        except OSError as error:
            raise ProtocolError(
                f"cannot create unique handoff directory {runtime_root / handoff_id}: {error}"
            ) from error
        id_fd = os.open(
            handoff_id,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            _validate_directory_stat(
                os.fstat(id_fd), runtime_root / handoff_id, required_mode=0o700
            )
            _publish_group(id_fd, [(HANDOFF_FILENAME, handoff_data)])
        finally:
            os.close(id_fd)
    except Exception:
        if id_created:
            try:
                os.rmdir(handoff_id, dir_fd=root_fd)
                os.fsync(root_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(root_fd)

    handoff_path = runtime_root / handoff_id / HANDOFF_FILENAME
    verify_handoff(
        handoff_path,
        expected_id=handoff_id,
        expected_bytes=len(handoff_data),
        expected_sha256=_sha256(handoff_data),
        runtime_root=runtime_root,
    )
    return _handoff_metadata(handoff_path, handoff_data, handoff_id)


def verify_handoff(
    handoff_path: Path,
    *,
    expected_id: str | None = None,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    handoff_path = Path(handoff_path)
    runtime_root = Path(runtime_root)
    if not handoff_path.is_absolute():
        raise ProtocolError(f"handoff path must be absolute: {handoff_path}")
    handoff_id = _expect_handoff_id(handoff_path.parent.name, "handoff path ID")
    exact_path = runtime_root / handoff_id / HANDOFF_FILENAME
    if handoff_path != exact_path:
        raise ProtocolError(
            f"handoff path mismatch; expected={exact_path}; observed={handoff_path}"
        )
    if expected_id is not None and _expect_handoff_id(expected_id) != handoff_id:
        raise ProtocolError(
            f"bootstrap handoff ID mismatch; expected={expected_id}; path={handoff_id}"
        )
    root_fd, id_fd = _open_id_directory(runtime_root, handoff_id)
    try:
        data, _ = _read_runtime_file(
            id_fd,
            HANDOFF_FILENAME,
            max_bytes=MAX_HANDOFF_BYTES,
            label="handoff",
        )
    finally:
        os.close(id_fd)
        os.close(root_fd)
    if expected_bytes is not None and len(data) != expected_bytes:
        raise ProtocolError(
            f"handoff byte count mismatch; expected={expected_bytes}; observed={len(data)}"
        )
    observed_sha = _sha256(data)
    if expected_sha256 is not None:
        expected_sha256 = _expect_sha256(expected_sha256, "expected handoff SHA-256")
        if observed_sha != expected_sha256:
            raise ProtocolError(
                f"handoff SHA-256 mismatch; expected={expected_sha256}; observed={observed_sha}"
            )

    outer = _expect_object(
        _load_json_bytes(data, "handoff", require_canonical=True), "handoff"
    )
    _expect_keys(
        outer,
        {
            "artifact_count",
            "artifacts",
            "complete",
            "envelope",
            "execution_protocol",
            "handoff_id",
            "handoff_version",
            "work_item_count",
            "work_items",
        },
        "handoff",
    )
    observed_handoff_version = _expect_int(
        outer["handoff_version"], "handoff.handoff_version", 1, HANDOFF_VERSION
    )
    if observed_handoff_version not in LEGACY_HANDOFF_VERSIONS | {HANDOFF_VERSION}:
        raise ProtocolError(
            f"handoff_version is unsupported; observed={outer['handoff_version']!r}"
        )
    if outer["handoff_id"] != handoff_id:
        raise ProtocolError(
            f"handoff_id mismatch; expected={handoff_id}; observed={outer['handoff_id']!r}"
        )
    complete = f"HANDOFF_COMPLETE:{handoff_id}"
    if outer["complete"] != complete:
        raise ProtocolError(
            f"handoff completeness marker mismatch; expected={complete!r}; observed={outer['complete']!r}"
        )
    work_item_count = _expect_int(
        outer["work_item_count"], "handoff.work_item_count", 1, 100_000
    )
    artifact_count = _expect_int(
        outer["artifact_count"], "handoff.artifact_count", 0, 100_000
    )
    work_item_records = _expect_list(outer["work_items"], "handoff.work_items")
    artifact_records = _expect_list(outer["artifacts"], "handoff.artifacts")
    if len(work_item_records) != work_item_count:
        raise ProtocolError(
            f"Work Item count mismatch; declared={work_item_count}; observed={len(work_item_records)}"
        )
    if len(artifact_records) != artifact_count:
        raise ProtocolError(
            f"artifact count mismatch; declared={artifact_count}; observed={len(artifact_records)}"
        )

    envelope = _expect_object(
        _load_json_bytes(
            _decode_record(outer["envelope"], "handoff.envelope"),
            "decoded handoff.envelope",
            require_canonical=True,
        ),
        "decoded handoff.envelope",
    )
    if observed_handoff_version == HANDOFF_VERSION:
        _validate_v4_handoff_envelope(envelope)
    work_items: list[dict[str, Any]] = []
    for index, record in enumerate(work_item_records):
        decoded = _load_json_bytes(
            _decode_record(record, f"handoff.work_items[{index}]"),
            f"decoded handoff.work_items[{index}]",
            require_canonical=True,
        )
        work_items.append(_expect_object(decoded, f"decoded handoff.work_items[{index}]"))
    artifacts: list[dict[str, Any]] = []
    for index, record in enumerate(artifact_records):
        decoded = _load_json_bytes(
            _decode_record(record, f"handoff.artifacts[{index}]"),
            f"decoded handoff.artifacts[{index}]",
            require_canonical=True,
        )
        artifacts.append(_expect_object(decoded, f"decoded handoff.artifacts[{index}]"))
    protocol_bytes = _decode_record(
        outer["execution_protocol"], "handoff.execution_protocol"
    )
    try:
        protocol_text = protocol_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"decoded execution protocol is not valid UTF-8: {error}") from error
    if not protocol_bytes.endswith(b"\n"):
        raise ProtocolError("decoded execution protocol must end with one newline")

    return {
        "artifact_count": artifact_count,
        "artifacts": artifacts,
        "complete": complete,
        "envelope": envelope,
        "execution_protocol": protocol_text,
        "file_bytes": len(data),
        "file_sha256": observed_sha,
        "handoff_id": handoff_id,
        "handoff_version": observed_handoff_version,
        "path": str(handoff_path),
        "work_item_count": work_item_count,
        "work_items": work_items,
    }


def _open_repository_git_directory(repository: Path) -> tuple[Path, int]:
    repository = _expect_absolute_path(repository, "repository")
    if Path(os.path.realpath(repository)) != repository:
        raise ProtocolError(
            f"repository path contains a symbolic-link component: {repository}"
        )
    try:
        repository_stat = os.lstat(repository)
    except OSError as error:
        raise ProtocolError(f"cannot lstat repository {repository}: {error}") from error
    if not stat.S_ISDIR(repository_stat.st_mode):
        raise ProtocolError(f"repository must be a real directory: {repository}")
    if repository_stat.st_uid != os.getuid():
        raise ProtocolError(
            f"repository owner mismatch at {repository}; expected uid={os.getuid()}; "
            f"observed={repository_stat.st_uid}"
        )

    git_directory = repository / ".git"
    if Path(os.path.realpath(git_directory)) != git_directory:
        raise ProtocolError(
            "zero-worktree execution requires the project checkout's .git to be a "
            f"real directory, not a symlink or linked-worktree file: {git_directory}"
        )
    try:
        git_stat = os.lstat(git_directory)
    except OSError as error:
        raise ProtocolError(f"cannot lstat Git directory {git_directory}: {error}") from error
    if not stat.S_ISDIR(git_stat.st_mode):
        raise ProtocolError(
            "zero-worktree execution requires one ordinary checkout with a real .git "
            f"directory: {git_directory}"
        )
    if git_stat.st_uid != os.getuid():
        raise ProtocolError(
            f"Git directory owner mismatch at {git_directory}; expected uid={os.getuid()}; "
            f"observed={git_stat.st_uid}"
        )
    if _mode_bits(git_stat) & 0o022:
        raise ProtocolError(
            f"Git directory must not be group/world writable: {git_directory}"
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        git_fd = os.open(git_directory, flags)
    except OSError as error:
        raise ProtocolError(
            f"cannot open Git directory without following links {git_directory}: {error}"
        ) from error
    opened = os.fstat(git_fd)
    if (opened.st_dev, opened.st_ino) != (git_stat.st_dev, git_stat.st_ino):
        os.close(git_fd)
        raise ProtocolError(f"Git directory changed between lstat and open: {git_directory}")
    if not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.getuid():
        os.close(git_fd)
        raise ProtocolError(f"Git directory identity verification failed: {git_directory}")
    if _mode_bits(opened) & 0o022:
        os.close(git_fd)
        raise ProtocolError(
            f"Git directory became group/world writable: {git_directory}"
        )
    return git_directory, git_fd


def _lease_path(repository: Path) -> Path:
    return Path(repository) / ".git" / LEASE_FILENAME


def _validate_lease_document(value: Any, label: str) -> dict[str, Any]:
    lease = _expect_object(value, label)
    _expect_keys(
        lease,
        {
            "base_branch",
            "base_head",
            "complete",
            "lease_id",
            "lease_version",
            "mode",
            "owner_host_id",
            "owner_task_id",
            "repository",
        },
        label,
    )
    lease_version = _expect_int(
        lease["lease_version"], f"{label}.lease_version", 1, LEASE_VERSION
    )
    lease_id = _expect_handoff_id(lease["lease_id"], f"{label}.lease_id")
    complete = f"REPOSITORY_LEASE_COMPLETE:{lease_id}"
    if lease["complete"] != complete:
        raise ProtocolError(
            f"{label}.complete mismatch; expected={complete!r}; "
            f"observed={lease['complete']!r}"
        )
    mode = _expect_nonempty_string(lease["mode"], f"{label}.mode", max_bytes=128)
    if (lease_version, mode) not in SUPPORTED_LEASE_IDENTITIES:
        raise ProtocolError(
            f"{label} lease identity is unsupported; "
            f"observed_version={lease_version!r}; observed_mode={mode!r}"
        )
    return {
        "base_branch": _expect_nonempty_string(
            lease["base_branch"], f"{label}.base_branch", max_bytes=1_024
        ),
        "base_head": _expect_git_oid(lease["base_head"], f"{label}.base_head"),
        "complete": complete,
        "lease_id": lease_id,
        "lease_version": lease_version,
        "mode": mode,
        "owner_host_id": _expect_nonempty_string(
            lease["owner_host_id"], f"{label}.owner_host_id", max_bytes=256
        ),
        "owner_task_id": _expect_nonempty_string(
            lease["owner_task_id"], f"{label}.owner_task_id", max_bytes=256
        ),
        "repository": str(
            _expect_absolute_path(lease["repository"], f"{label}.repository")
        ),
    }


def inspect_repository_lease(repository: Path) -> dict[str, Any]:
    repository = _expect_absolute_path(repository, "repository")
    git_directory, git_fd = _open_repository_git_directory(repository)
    try:
        try:
            os.stat(LEASE_FILENAME, dir_fd=git_fd, follow_symlinks=False)
        except FileNotFoundError:
            return {
                "path": str(git_directory / LEASE_FILENAME),
                "repository": str(repository),
                "state": "available",
            }
        except OSError as error:
            raise ProtocolError(
                f"cannot inspect repository lease {git_directory / LEASE_FILENAME}: {error}"
            ) from error
        try:
            data, _ = _read_runtime_file(
                git_fd,
                LEASE_FILENAME,
                max_bytes=MAX_LEASE_BYTES,
                label="repository lease",
            )
        except ProtocolError:
            raise
    finally:
        os.close(git_fd)
    lease = _validate_lease_document(
        _load_json_bytes(data, "repository lease", require_canonical=True),
        "repository lease",
    )
    if lease["repository"] != str(repository):
        raise ProtocolError(
            f"repository lease target mismatch; expected={repository}; "
            f"observed={lease['repository']}"
        )
    return {
        **lease,
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "path": str(git_directory / LEASE_FILENAME),
        "state": "held",
    }


def acquire_repository_lease(input_path: Path) -> dict[str, Any]:
    data = _read_regular_file(
        Path(input_path), max_bytes=MAX_LEASE_BYTES, label="repository lease input"
    )
    source = _expect_object(
        _load_json_bytes(data, "repository lease input"), "repository lease input"
    )
    _expect_keys(
        source,
        {"base_branch", "base_head", "owner_host_id", "owner_task_id", "repository"},
        "repository lease input",
    )
    repository = _expect_absolute_path(
        source["repository"], "repository lease input.repository"
    )
    git_directory, git_fd = _open_repository_git_directory(repository)
    lease_id = secrets.token_hex(16)
    lease = {
        "base_branch": _expect_nonempty_string(
            source["base_branch"], "repository lease input.base_branch", max_bytes=1_024
        ),
        "base_head": _expect_git_oid(
            source["base_head"], "repository lease input.base_head"
        ),
        "complete": f"REPOSITORY_LEASE_COMPLETE:{lease_id}",
        "lease_id": lease_id,
        "lease_version": LEASE_VERSION,
        "mode": LEASE_MODE,
        "owner_host_id": _expect_nonempty_string(
            source["owner_host_id"],
            "repository lease input.owner_host_id",
            max_bytes=256,
        ),
        "owner_task_id": _expect_nonempty_string(
            source["owner_task_id"],
            "repository lease input.owner_task_id",
            max_bytes=256,
        ),
        "repository": str(repository),
    }
    lease_data = _canonical_json_bytes(lease)
    if len(lease_data) > MAX_LEASE_BYTES:
        os.close(git_fd)
        raise ProtocolError(
            f"repository lease exceeds {MAX_LEASE_BYTES} bytes; observed={len(lease_data)}"
        )
    try:
        _publish_group(git_fd, [(LEASE_FILENAME, lease_data)])
    finally:
        os.close(git_fd)
    report = inspect_repository_lease(repository)
    if report["lease_id"] != lease_id:
        raise ProtocolError(
            f"acquired repository lease identity mismatch; expected={lease_id}; "
            f"observed={report['lease_id']}"
        )
    return report


def verify_repository_lease(
    lease_path: Path,
    *,
    expected_id: str,
    expected_bytes: int,
    expected_sha256: str,
) -> dict[str, Any]:
    lease_path = Path(lease_path)
    if not lease_path.is_absolute() or lease_path.name != LEASE_FILENAME:
        raise ProtocolError(f"repository lease path is invalid: {lease_path}")
    if lease_path.parent.name != ".git":
        raise ProtocolError(
            f"repository lease must be directly inside an ordinary checkout .git directory: {lease_path}"
        )
    repository = lease_path.parent.parent
    report = inspect_repository_lease(repository)
    if report["state"] != "held":
        raise ProtocolError(f"repository lease is not held: {lease_path}")
    expected_id = _expect_handoff_id(expected_id, "expected lease ID")
    expected_sha256 = _expect_sha256(expected_sha256, "expected lease SHA-256")
    if report["path"] != str(lease_path):
        raise ProtocolError(
            f"repository lease path mismatch; expected={lease_path}; observed={report['path']}"
        )
    if report["lease_id"] != expected_id:
        raise ProtocolError(
            f"repository lease ID mismatch; expected={expected_id}; observed={report['lease_id']}"
        )
    if report["file_bytes"] != expected_bytes:
        raise ProtocolError(
            f"repository lease byte count mismatch; expected={expected_bytes}; "
            f"observed={report['file_bytes']}"
        )
    if report["file_sha256"] != expected_sha256:
        raise ProtocolError(
            f"repository lease SHA-256 mismatch; expected={expected_sha256}; "
            f"observed={report['file_sha256']}"
        )
    return report


def release_repository_lease(
    lease_path: Path,
    *,
    expected_id: str,
    expected_bytes: int,
    expected_sha256: str,
) -> dict[str, Any]:
    report = verify_repository_lease(
        lease_path,
        expected_id=expected_id,
        expected_bytes=expected_bytes,
        expected_sha256=expected_sha256,
    )
    repository = Path(report["repository"])
    git_directory, git_fd = _open_repository_git_directory(repository)
    try:
        _unlink_verified_file(
            git_fd,
            name=LEASE_FILENAME,
            expected_bytes=report["file_bytes"],
            expected_sha256=report["file_sha256"],
            max_bytes=MAX_LEASE_BYTES,
            label="repository lease",
        )
        os.fsync(git_fd)
    finally:
        os.close(git_fd)
    return {
        "lease_id": report["lease_id"],
        "path": str(git_directory / LEASE_FILENAME),
        "repository": str(repository),
        "state": "available",
        "verified_absent": True,
    }


def _now_epoch() -> int:
    return int(time.time())


def _git_text(repository: Path, arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise ProtocolError(
            f"Git command failed for {repository}: git {' '.join(arguments)}: {error.stderr.strip()}"
        ) from error
    return completed.stdout


def _active_git_worktrees(repository: Path) -> list[dict[str, Any]]:
    output = _git_text(repository, ["worktree", "list", "--porcelain"])
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in output.splitlines() + [""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            path = Path(value).resolve(strict=True)
            current["path"] = str(path)
        elif key == "HEAD":
            current["head"] = _expect_git_oid(value, "worktree HEAD")
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key in {"locked", "prunable"}:
            current[key] = value or True
    return sorted(records, key=lambda item: item["path"])


def _open_private_git_subdirectory(repository: Path, name: str) -> tuple[Path, int, int]:
    git_directory, git_fd = _open_repository_git_directory(repository)
    directory = git_directory / name
    try:
        os.mkdir(name, 0o700, dir_fd=git_fd)
        os.fsync(git_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(name, flags, dir_fd=git_fd)
    except OSError as error:
        os.close(git_fd)
        raise ProtocolError("supervision_protocol_error", f"cannot open private protocol directory {name}: {error}") from error
    status = os.fstat(directory_fd)
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.getuid() or _mode_bits(status) != 0o700:
        os.close(directory_fd)
        os.close(git_fd)
        raise ProtocolError("supervision_protocol_error", f"private protocol directory is unsafe: {directory}")
    return directory, directory_fd, git_fd


def _worktree_state_snapshot(repository: Path) -> dict[str, Any]:
    worktrees = _active_git_worktrees(repository)
    leases = []
    lease_directory = _worktree_execution_directory(repository)
    if lease_directory.exists():
        for path in sorted(lease_directory.glob("*.json")):
            document, data = _read_worktree_execution_lease(path)
            report = _worktree_lease_report(path, document, data)
            leases.append(
                {
                    "binding": report["binding"],
                    "file_sha256": report["file_sha256"],
                    "lease_id": report["holder"]["lease_id"] if report["holder"] else None,
                    "state": report["state"],
                    "version": report["version"],
                }
            )
    return {"execution_leases": leases, "git_worktrees": worktrees}


def _worktree_state_receipt_path(repository: Path, topic_id: str) -> Path:
    name = hashlib.sha256(topic_id.encode("utf-8")).hexdigest() + ".json"
    return repository / ".git" / WORKTREE_STATE_RECEIPT_DIRECTORY / name


def create_worktree_state_receipt(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(input_path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree state receipt input"),
            "worktree state receipt input",
        ),
        "worktree state receipt input",
    )
    _expect_keys(source, {"project_id", "repository", "topic_id", "tree_id"}, "worktree state receipt input")
    repository = _expect_absolute_path(source["repository"], "repository")
    identity = {
        "project_id": _expect_nonempty_string(source["project_id"], "project_id", max_bytes=128),
        "repository": str(repository),
        "topic_id": _expect_nonempty_string(source["topic_id"], "topic_id", max_bytes=128),
        "tree_id": _expect_nonempty_string(source["tree_id"], "tree_id", max_bytes=128),
    }
    path = _worktree_state_receipt_path(repository, identity["topic_id"])
    directory, directory_fd, git_fd = _open_private_git_subdirectory(repository, WORKTREE_STATE_RECEIPT_DIRECTORY)
    try:
        version = 1
        if path.exists():
            old = _expect_object(_load_json_bytes(_read_regular_file(path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree state receipt"), "worktree state receipt", require_canonical=True), "worktree state receipt")
            version = _expect_int(old.get("version"), "worktree state receipt.version", 1, 2**63 - 2) + 1
        snapshot = _worktree_state_snapshot(repository)
        document = {
            **identity,
            "receipt_id": _sha256(_canonical_json_bytes({"identity": identity, "snapshot": snapshot, "version": version})),
            "schema": WORKTREE_STATE_RECEIPT_VERSION,
            "snapshot": snapshot,
            "version": version,
        }
        data = _canonical_json_bytes(document)
        if path.exists():
            _replace_private_file(directory_fd, name=path.name, data=data, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree state receipt")
        else:
            _publish_group(directory_fd, [(path.name, data)])
        os.fsync(directory_fd)
        return {**document, "file_bytes": len(data), "file_sha256": _sha256(data), "path": str(path), "state": "issued"}
    finally:
        os.close(directory_fd)
        os.close(git_fd)


def verify_worktree_state_receipt(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree state verification input"), "worktree state verification input"), "worktree state verification input")
    _expect_keys(source, {"file_bytes", "file_sha256", "path", "project_id", "repository", "topic_id", "tree_id", "version"}, "worktree state verification input")
    repository = _expect_absolute_path(source["repository"], "repository")
    topic_id = _expect_nonempty_string(source["topic_id"], "topic_id", max_bytes=128)
    path = _expect_absolute_path(source["path"], "receipt path")
    expected_path = _worktree_state_receipt_path(repository, topic_id)
    if path != expected_path:
        raise ProtocolError("receipt_identity_mismatch", f"worktree receipt path mismatch; expected={expected_path}; observed={path}", context={"repository": str(repository), "topic_id": topic_id})
    data = _read_regular_file(path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree state receipt")
    if len(data) != _expect_int(source["file_bytes"], "file_bytes", 1, MAX_WORKTREE_EXECUTION_LEASE_BYTES) or _sha256(data) != _expect_sha256(source["file_sha256"], "file_sha256"):
        raise ProtocolError("receipt_cas_mismatch", "worktree receipt byte identity changed", context={"repository": str(repository), "topic_id": topic_id})
    receipt = _expect_object(_load_json_bytes(data, "worktree state receipt", require_canonical=True), "worktree state receipt")
    for key in ("project_id", "repository", "topic_id", "tree_id", "version"):
        if receipt.get(key) != source[key]:
            raise ProtocolError("receipt_identity_mismatch", f"worktree receipt {key} mismatch", context={"repository": str(repository), "topic_id": topic_id, "receipt_id": receipt.get("receipt_id")})
    current = _worktree_state_snapshot(repository)
    if receipt.get("snapshot") != current:
        raise ProtocolError("receipt_stale", "Git worktree or execution lease state changed after receipt issuance", retryable=True, context={"repository": str(repository), "topic_id": topic_id, "receipt_id": receipt.get("receipt_id"), "current": current})
    return {**receipt, "file_bytes": len(data), "file_sha256": _sha256(data), "path": str(path), "state": "valid", "verified": True}


def _verify_discussion_request(request: dict[str, Any]) -> dict[str, Any]:
    script = Path(__file__).resolve().parents[2] / "design-discussion" / "scripts" / "discussion_protocol.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            input=_canonical_json_bytes(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise ProtocolError(
            "discussion_receipt_invalid",
            "discussion verifier exceeded the bounded ten second deadline",
            retryable=True,
            cause=str(error),
        ) from error
    try:
        result = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ProtocolError("discussion_receipt_invalid", "discussion receipt verifier returned invalid JSON", cause=completed.stderr.decode("utf-8", "replace")[:1024]) from error
    if completed.returncode != 0 or not isinstance(result, dict) or result.get("ok") is not True:
        code = result.get("error", {}).get("code") if isinstance(result, dict) else None
        raise ProtocolError("discussion_receipt_invalid", f"discussion authority rejected receipt; code={code!r}", cause=completed.stderr.decode("utf-8", "replace")[:1024])
    return result


def _confirmation_path(repository: Path, implementation_id: str) -> Path:
    return repository / ".git" / ISOLATED_CONFIRMATION_DIRECTORY / (hashlib.sha256(implementation_id.encode("utf-8")).hexdigest() + ".json")


def _verify_creation_coordination(value: Any, repository: Path) -> dict[str, Any]:
    lease = _expect_object(value, "coordination_lease")
    _expect_keys(lease, {"lease_id", "path", "version"}, "coordination_lease")
    try:
        report = verify_repository_coordination_lease(
            _expect_absolute_path(lease["path"], "coordination_lease.path"),
            expected_id=lease["lease_id"],
            expected_version=lease["version"],
        )
    except ProtocolError as error:
        raise ProtocolError(
            "repository_coordination_required",
            "worktree creation coordination lease identity or CAS verification failed",
            cause=str(error),
            context={"repository": str(repository)},
        ) from error
    holder = report["holder"]
    if report["repository"] != str(repository) or holder is None or holder["stage"] != "guided-implementation" or holder["purpose"] != "worktree-creation":
        raise ProtocolError("repository_coordination_required", "exact guided-implementation worktree-creation coordination lease is required", context={"repository": str(repository)})
    return report


def _confirmation_report(path: Path, document: dict[str, Any], data: bytes) -> dict[str, Any]:
    return {**document, "file_bytes": len(data), "file_sha256": _sha256(data), "path": str(path)}


def _validate_isolated_user_decision(
    data: bytes,
    *,
    binding: dict[str, Any],
    source_task_id: str,
) -> dict[str, Any]:
    try:
        value = _expect_object(
            _load_json_bytes(data, "user decision", require_canonical=True),
            "user decision",
        )
    except ProtocolError as error:
        raise ProtocolError(
            "isolated_confirmation_required",
            "user decision must be the canonical typed durable payload",
            cause=str(error),
        ) from error
    try:
        _expect_keys(
            value,
            {
                "base_commit",
                "confirmed",
                "creation_action",
                "implementation_branch",
                "implementation_id",
                "phase_run_id",
                "repository",
                "schema",
                "scope_sha256",
                "sensitive_shared_surfaces",
                "source_task_id",
                "topic_id",
                "worktree_path",
            },
            "user decision",
        )
    except ProtocolError as error:
        raise ProtocolError(
            "isolated_confirmation_required",
            "user decision is missing required canonical identity fields",
            cause=str(error),
            context={"repository": binding["repository"], "topic_id": binding["topic_id"]},
        ) from error
    expected = {
        **binding,
        "confirmed": True,
        "creation_action": "create-isolated-worktree",
        "schema": ISOLATED_USER_DECISION_SCHEMA,
        "source_task_id": source_task_id,
    }
    if value != expected:
        raise ProtocolError(
            "isolated_confirmation_required",
            "user decision does not equal the exact worktree identity and creation action",
            context={"repository": binding["repository"], "topic_id": binding["topic_id"]},
        )
    return value


def record_isolated_confirmation(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="isolated confirmation input"), "isolated confirmation input"), "isolated confirmation input")
    _expect_keys(source, {"binding", "parallelism_validation", "source_task_id", "user_decision_file"}, "isolated confirmation input")
    binding = _normalize_worktree_binding(source["binding"], "binding")
    repository = Path(binding["repository"])
    if any(item["path"] == binding["worktree_path"] for item in _active_git_worktrees(repository)) or Path(binding["worktree_path"]).exists():
        raise ProtocolError("worktree_creation_uncoordinated", "isolated path must not exist before durable confirmation", context={"repository": str(repository), "topic_id": binding["topic_id"]})
    parallelism = _verify_discussion_request(_expect_object(source["parallelism_validation"], "parallelism_validation"))
    if parallelism.get("verdict") != "safe" or parallelism.get("implementation_id") != binding["implementation_id"] or parallelism.get("topic_id") != binding["topic_id"]:
        raise ProtocolError("isolated_confirmation_required", "confirmation requires the exact current safe parallelism receipt", context={"repository": str(repository), "topic_id": binding["topic_id"]})
    decision_path = _expect_absolute_path(source["user_decision_file"], "user_decision_file")
    decision_bytes = _read_regular_file(decision_path, max_bytes=16_384, label="user decision")
    decision = _validate_isolated_user_decision(
        decision_bytes,
        binding=binding,
        source_task_id=_expect_nonempty_string(
            source["source_task_id"], "source_task_id", max_bytes=256
        ),
    )
    path = _confirmation_path(repository, binding["implementation_id"])
    directory, directory_fd, git_fd = _open_private_git_subdirectory(repository, ISOLATED_CONFIRMATION_DIRECTORY)
    try:
        if path.exists():
            raise ProtocolError("isolated_confirmation_required", "an isolated confirmation already exists and must be reconciled")
        document = {
            "binding": binding,
            "parallelism": {"ledger_revision": parallelism["ledger_revision"], "receipt": parallelism["receipt"], "verdict": "safe"},
            "schema": ISOLATED_CONFIRMATION_VERSION,
            "state": "confirmed",
            "user_decision": {
                "bytes": len(decision_bytes),
                "payload": decision,
                "sha256": _sha256(decision_bytes),
                "utf8_b64": base64.b64encode(decision_bytes).decode("ascii"),
            },
            "version": 1,
        }
        data = _canonical_json_bytes(document)
        _publish_group(directory_fd, [(path.name, data)])
        os.fsync(directory_fd)
        return {**_confirmation_report(path, document, data), "ok": True}
    finally:
        os.close(directory_fd)
        os.close(git_fd)


def _verify_confirmation(
    value: Any,
    repository: Path,
    *,
    required_state: str | set[str],
) -> tuple[Path, dict[str, Any], bytes]:
    receipt = _expect_object(value, "confirmation")
    _expect_keys(receipt, {"file_bytes", "file_sha256", "path", "version"}, "confirmation")
    path = _expect_absolute_path(receipt["path"], "confirmation.path")
    data = _read_regular_file(path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="isolated confirmation")
    if len(data) != _expect_int(receipt["file_bytes"], "confirmation.file_bytes", 1, MAX_WORKTREE_EXECUTION_LEASE_BYTES) or _sha256(data) != _expect_sha256(receipt["file_sha256"], "confirmation.file_sha256"):
        raise ProtocolError("receipt_cas_mismatch", "isolated confirmation byte identity changed", context={"repository": str(repository)})
    document = _expect_object(_load_json_bytes(data, "isolated confirmation", require_canonical=True), "isolated confirmation")
    binding = _normalize_worktree_binding(document.get("binding"), "isolated confirmation.binding")
    if path != _confirmation_path(repository, binding["implementation_id"]) or binding["repository"] != str(repository) or document.get("schema") != ISOLATED_CONFIRMATION_VERSION or document.get("version") != receipt["version"]:
        raise ProtocolError("receipt_identity_mismatch", "isolated confirmation identity/version mismatch", context={"repository": str(repository), "topic_id": binding.get("topic_id")})
    allowed_states = {required_state} if isinstance(required_state, str) else required_state
    if document.get("state") not in allowed_states:
        raise ProtocolError("isolated_confirmation_required", f"isolated confirmation state must be one of {sorted(allowed_states)}; observed={document.get('state')!r}", context={"repository": str(repository), "topic_id": binding["topic_id"]})
    return path, document, data


def _replace_confirmation_document(
    repository: Path, path: Path, confirmation: dict[str, Any]
) -> dict[str, Any]:
    data = _canonical_json_bytes(confirmation)
    _, directory_fd, git_fd = _open_private_git_subdirectory(repository, ISOLATED_CONFIRMATION_DIRECTORY)
    try:
        _replace_private_file(directory_fd, name=path.name, data=data, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="isolated confirmation")
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
        os.close(git_fd)
    return _confirmation_report(path, confirmation, data)


def _confirmation_matches_coordination(
    confirmation: dict[str, Any], coordination: dict[str, Any]
) -> bool:
    holder = coordination.get("holder") or {}
    payload = (confirmation.get("user_decision") or {}).get("payload")
    if isinstance(payload, dict):
        return payload.get("source_task_id") == holder.get("owner_task_id")
    return confirmation.get("coordination_lease") == {
        "lease_id": holder.get("lease_id"),
        "path": coordination.get("path"),
        "version": coordination.get("version"),
    }


def create_isolated_worktree(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="isolated worktree creation input"), "isolated worktree creation input"), "isolated worktree creation input")
    _expect_keys(source, {"confirmation", "coordination_lease", "repository"}, "isolated worktree creation input")
    repository = _expect_absolute_path(source["repository"], "repository")
    coordination = _verify_creation_coordination(source["coordination_lease"], repository)
    path, confirmation, _ = _verify_confirmation(source["confirmation"], repository, required_state="confirmed")
    binding = confirmation["binding"]
    if not _confirmation_matches_coordination(confirmation, coordination):
        raise ProtocolError("repository_coordination_required", "confirmation source task and creation coordination lease differ", context={"repository": str(repository), "topic_id": binding["topic_id"]})
    try:
        subprocess.run(["git", "-C", str(repository), "worktree", "add", "-b", binding["implementation_branch"], binding["worktree_path"], binding["base_commit"]], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        uncertain = {
            **confirmation,
            "creation_observation": _active_git_worktrees(repository),
            "state": "outcome-unknown",
            "version": confirmation["version"] + 1,
        }
        current = _replace_confirmation_document(repository, path, uncertain)
        raise ProtocolError("outcome_unknown", "git worktree creation failed or its outcome is unknown", retryable=True, cause=error.stderr.decode("utf-8", "replace")[:1024], context={"repository": str(repository), "topic_id": binding["topic_id"], "current": current}) from error
    observed = _validate_worktree_binding_present(binding)
    if os.environ.get("CODEX_SUPERVISION_TEST_FAILPOINT") == "create-isolated-after-git":
        uncertain = {
            **confirmation,
            "creation_observation": observed,
            "state": "outcome-unknown",
            "version": confirmation["version"] + 1,
        }
        current = _replace_confirmation_document(repository, path, uncertain)
        raise ProtocolError(
            "outcome_unknown",
            "worktree exists but durable creation completion is outcome-unknown",
            retryable=True,
            context={"repository": str(repository), "topic_id": binding["topic_id"], "current": current},
        )
    updated = {**confirmation, "creation": observed, "state": "created", "version": confirmation["version"] + 1}
    return {**_replace_confirmation_document(repository, path, updated), "created": True, "ok": True}


def reconcile_isolated_worktree_creation(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="isolated worktree reconciliation input"), "isolated worktree reconciliation input"), "isolated worktree reconciliation input")
    _expect_keys(source, {"confirmation", "coordination_lease", "repository"}, "isolated worktree reconciliation input")
    repository = _expect_absolute_path(source["repository"], "repository")
    coordination = _verify_creation_coordination(source["coordination_lease"], repository)
    path, confirmation, _ = _verify_confirmation(source["confirmation"], repository, required_state={"confirmed", "outcome-unknown"})
    binding = confirmation["binding"]
    if not _confirmation_matches_coordination(confirmation, coordination):
        raise ProtocolError("repository_coordination_required", "confirmation source task and reconciliation coordination lease differ", context={"repository": str(repository), "topic_id": binding["topic_id"]})
    matches = [item for item in _active_git_worktrees(repository) if item["path"] == binding["worktree_path"]]
    if not matches and not Path(binding["worktree_path"]).exists():
        updated = {**confirmation, "creation_observation": [], "state": "confirmed", "version": confirmation["version"] + 1}
        return {**_replace_confirmation_document(repository, path, updated), "created": False, "ok": True, "reconciled": True}
    try:
        observed = _validate_worktree_binding_present(binding)
    except ProtocolError as error:
        raise ProtocolError("outcome_unknown", "worktree creation state exists but does not match the durable binding", retryable=True, cause=str(error), context={"repository": str(repository), "topic_id": binding["topic_id"], "current": _active_git_worktrees(repository)}) from error
    updated = {**confirmation, "creation": observed, "state": "created", "version": confirmation["version"] + 1}
    return {**_replace_confirmation_document(repository, path, updated), "created": True, "ok": True, "reconciled": True}


def verify_isolated_confirmation(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="isolated confirmation verification input"), "isolated confirmation verification input"), "isolated confirmation verification input")
    _expect_keys(source, {"binding", "confirmation", "parallelism_receipt", "repository"}, "isolated confirmation verification input")
    repository = _expect_absolute_path(source["repository"], "repository")
    _, confirmation, data = _verify_confirmation(source["confirmation"], repository, required_state="confirmed")
    binding = _normalize_worktree_binding(source["binding"], "binding")
    receipt = _expect_sha256(source["parallelism_receipt"], "parallelism_receipt")
    if confirmation["binding"] != binding or confirmation["parallelism"].get("receipt") != receipt or confirmation["parallelism"].get("verdict") != "safe":
        raise ProtocolError("isolated_confirmation_required", "durable confirmation does not bind the exact scope and safe receipt", context={"repository": str(repository), "topic_id": binding["topic_id"]})
    return {**confirmation, "file_sha256": _sha256(data), "state": "valid", "verified": True}


def _worktree_execution_directory(repository: Path) -> Path:
    return repository / ".git" / WORKTREE_EXECUTION_LEASE_DIRECTORY


def _worktree_execution_lease_path(repository: Path, worktree_path: Path) -> Path:
    name = hashlib.sha256(str(worktree_path).encode("utf-8")).hexdigest() + ".json"
    return _worktree_execution_directory(repository) / name


def _open_worktree_execution_directory(repository: Path) -> tuple[Path, int, int]:
    git_directory, git_fd = _open_repository_git_directory(repository)
    directory = git_directory / WORKTREE_EXECUTION_LEASE_DIRECTORY
    try:
        os.mkdir(WORKTREE_EXECUTION_LEASE_DIRECTORY, 0o700, dir_fd=git_fd)
        os.fsync(git_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(WORKTREE_EXECUTION_LEASE_DIRECTORY, flags, dir_fd=git_fd)
    except OSError as error:
        os.close(git_fd)
        raise ProtocolError(f"cannot open worktree execution lease directory: {error}") from error
    status = os.fstat(directory_fd)
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.getuid() or _mode_bits(status) != 0o700:
        os.close(directory_fd)
        os.close(git_fd)
        raise ProtocolError("worktree execution lease directory is unsafe")
    return directory, directory_fd, git_fd


def _open_worktree_execution_guard(git_fd: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            WORKTREE_EXECUTION_LEASE_GUARD_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=git_fd,
        )
    except FileExistsError:
        descriptor = os.open(
            WORKTREE_EXECUTION_LEASE_GUARD_FILENAME,
            os.O_RDWR | nofollow,
            dir_fd=git_fd,
        )
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode) or status.st_uid != os.getuid() or _mode_bits(status) != 0o600:
        os.close(descriptor)
        raise ProtocolError("worktree execution lease guard is unsafe")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _normalize_worktree_binding(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(
        source,
        {
            "base_commit", "implementation_branch", "implementation_id",
            "phase_run_id", "repository", "scope_sha256",
            "sensitive_shared_surfaces", "topic_id", "worktree_path",
        },
        label,
    )
    repository = _expect_absolute_path(source["repository"], f"{label}.repository")
    worktree_path = _expect_absolute_path(source["worktree_path"], f"{label}.worktree_path")
    surfaces = _expect_string_list(source["sensitive_shared_surfaces"], f"{label}.sensitive_shared_surfaces")
    if not surfaces or surfaces != sorted(set(surfaces)):
        raise ProtocolError(
            f"{label}.sensitive_shared_surfaces must be a non-empty sorted unique list"
        )
    return {
        "base_commit": _expect_git_oid(source["base_commit"], f"{label}.base_commit"),
        "implementation_branch": _expect_nonempty_string(source["implementation_branch"], f"{label}.implementation_branch", max_bytes=1024),
        "implementation_id": _expect_nonempty_string(source["implementation_id"], f"{label}.implementation_id", max_bytes=256),
        "phase_run_id": _expect_nonempty_string(source["phase_run_id"], f"{label}.phase_run_id", max_bytes=128),
        "repository": str(repository),
        "scope_sha256": _expect_sha256(source["scope_sha256"], f"{label}.scope_sha256"),
        "sensitive_shared_surfaces": surfaces,
        "topic_id": _expect_nonempty_string(source["topic_id"], f"{label}.topic_id", max_bytes=128),
        "worktree_path": str(worktree_path),
    }


def _validate_worktree_binding_present(
    binding: dict[str, Any], *, allow_descendant: bool = False
) -> dict[str, Any]:
    repository = Path(binding["repository"])
    matches = [item for item in _active_git_worktrees(repository) if item["path"] == binding["worktree_path"]]
    if len(matches) != 1:
        raise ProtocolError(
            "worktree_exact_missing",
            f"exact worktree binding is unavailable; expected={binding['worktree_path']}",
            context={"repository": binding["repository"], "topic_id": binding["topic_id"]},
        )
    observed = matches[0]
    head_matches = observed.get("head") == binding["base_commit"]
    if allow_descendant and not head_matches:
        try:
            subprocess.run(
                ["git", "-C", binding["worktree_path"], "merge-base", "--is-ancestor", binding["base_commit"], observed.get("head", "")],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            head_matches = True
        except subprocess.CalledProcessError:
            head_matches = False
    if observed.get("detached") or observed.get("branch") != binding["implementation_branch"] or not head_matches:
        raise ProtocolError(
            "worktree_branch_mismatch",
            "exact worktree binding mismatch; "
            f"expected_branch={binding['implementation_branch']!r}; observed_branch={observed.get('branch')!r}; "
            f"expected_base={binding['base_commit']}; observed_head={observed.get('head')}",
            context={"repository": binding["repository"], "topic_id": binding["topic_id"]},
        )
    return observed


def _validate_worktree_execution_document(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(source, {"binding", "holder", "repository", "schema", "version", "worktree_execution_lease_version"}, label)
    if source["schema"] != WORKTREE_EXECUTION_LEASE_SCHEMA or source["worktree_execution_lease_version"] != WORKTREE_EXECUTION_LEASE_VERSION:
        raise ProtocolError(f"{label} version is unsupported")
    binding = _normalize_worktree_binding(source["binding"], f"{label}.binding")
    repository = str(_expect_absolute_path(source["repository"], f"{label}.repository"))
    if repository != binding["repository"]:
        raise ProtocolError(f"{label}.repository does not match binding")
    holder = source["holder"]
    if holder is not None:
        holder = _expect_object(holder, f"{label}.holder")
        _expect_keys(holder, {"acquired_at_epoch", "expires_at_epoch", "lease_id", "owner_host_id", "owner_task_id"}, f"{label}.holder")
        holder = {
            "acquired_at_epoch": _expect_int(holder["acquired_at_epoch"], f"{label}.holder.acquired_at_epoch", 0, 2**63 - 1),
            "expires_at_epoch": _expect_int(holder["expires_at_epoch"], f"{label}.holder.expires_at_epoch", 1, 2**63 - 1),
            "lease_id": _expect_handoff_id(holder["lease_id"], f"{label}.holder.lease_id"),
            "owner_host_id": _expect_nonempty_string(holder["owner_host_id"], f"{label}.holder.owner_host_id", max_bytes=256),
            "owner_task_id": _expect_nonempty_string(holder["owner_task_id"], f"{label}.holder.owner_task_id", max_bytes=256),
        }
    return {"binding": binding, "holder": holder, "repository": repository, "schema": WORKTREE_EXECUTION_LEASE_SCHEMA, "version": _expect_int(source["version"], f"{label}.version", 1, 2**63 - 2), "worktree_execution_lease_version": WORKTREE_EXECUTION_LEASE_VERSION}


def _read_worktree_execution_lease(path: Path) -> tuple[dict[str, Any], bytes]:
    data = _read_regular_file(path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree execution lease")
    document = _validate_worktree_execution_document(_load_json_bytes(data, "worktree execution lease", require_canonical=True), "worktree execution lease")
    return document, data


def _worktree_lease_report(path: Path, document: dict[str, Any], data: bytes) -> dict[str, Any]:
    now = _now_epoch()
    holder = document["holder"]
    state = "available" if holder is None else ("expired" if holder["expires_at_epoch"] <= now else "held")
    return {**document, "file_bytes": len(data), "file_sha256": _sha256(data), "now_epoch": now, "path": str(path), "remaining_seconds": max(0, holder["expires_at_epoch"] - now) if holder else 0, "state": state}


def acquire_worktree_execution_lease(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree execution lease input"), "worktree execution lease input"), "worktree execution lease input")
    _expect_keys(source, {"base_commit", "confirmation", "coordination_lease", "implementation_branch", "implementation_id", "owner_host_id", "owner_task_id", "phase_run_id", "repository", "scope_sha256", "sensitive_shared_surfaces", "topic_id", "ttl_seconds", "worktree_path"}, "worktree execution lease input")
    binding = _normalize_worktree_binding({key: value for key, value in source.items() if key not in {"confirmation", "coordination_lease", "owner_host_id", "owner_task_id", "ttl_seconds"}}, "worktree execution lease input binding")
    _validate_worktree_binding_present(binding)
    ttl = _expect_int(source["ttl_seconds"], "worktree execution lease input.ttl_seconds", 1, MAX_WORKTREE_EXECUTION_LEASE_TTL_SECONDS)
    repository = Path(binding["repository"])
    coordination = _verify_creation_coordination(source["coordination_lease"], repository)
    _, confirmation, _ = _verify_confirmation(source["confirmation"], repository, required_state="created")
    if confirmation["binding"] != binding or not _confirmation_matches_coordination(confirmation, coordination):
        raise ProtocolError("worktree_creation_uncoordinated", "execution lease binding is not the exact coordinated creation", context={"repository": str(repository), "topic_id": binding["topic_id"]})
    path = _worktree_execution_lease_path(repository, Path(binding["worktree_path"]))
    directory, directory_fd, git_fd = _open_worktree_execution_directory(repository)
    guard_fd = _open_worktree_execution_guard(git_fd)
    try:
        if path.exists():
            current, current_data = _read_worktree_execution_lease(path)
            report = _worktree_lease_report(path, current, current_data)
            if report["state"] == "held":
                return {**report, "acquired": False}
            version = current["version"] + 1
        else:
            version = 1
        now = _now_epoch()
        document = {"binding": binding, "holder": {"acquired_at_epoch": now, "expires_at_epoch": now + ttl, "lease_id": secrets.token_hex(16), "owner_host_id": _expect_nonempty_string(source["owner_host_id"], "owner_host_id", max_bytes=256), "owner_task_id": _expect_nonempty_string(source["owner_task_id"], "owner_task_id", max_bytes=256)}, "repository": str(repository), "schema": WORKTREE_EXECUTION_LEASE_SCHEMA, "version": version, "worktree_execution_lease_version": WORKTREE_EXECUTION_LEASE_VERSION}
        data = _canonical_json_bytes(document)
        if path.exists():
            _replace_private_file(directory_fd, name=path.name, data=data, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree execution lease")
        else:
            _publish_group(directory_fd, [(path.name, data)])
        os.fsync(directory_fd)
        return {**_worktree_lease_report(path, document, data), "acquired": True}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def verify_worktree_execution_lease(lease_path: Path, *, expected_id: str, expected_version: int, platform_cwd: Path) -> dict[str, Any]:
    lease_path = _expect_absolute_path(lease_path, "worktree execution lease path")
    platform_cwd = _expect_absolute_path(platform_cwd, "platform working directory")
    document, data = _read_worktree_execution_lease(lease_path)
    report = _worktree_lease_report(lease_path, document, data)
    expected_path = _worktree_execution_lease_path(
        Path(report["repository"]), Path(report["binding"]["worktree_path"])
    )
    if lease_path != expected_path:
        raise ProtocolError(
            "receipt_identity_mismatch",
            f"worktree execution lease path mismatch; expected={expected_path}; observed={lease_path}",
            context={"repository": report["repository"], "topic_id": report["binding"]["topic_id"]},
        )
    holder = report["holder"]
    if report["state"] != "held" or holder is None or holder["lease_id"] != _expect_handoff_id(expected_id, "expected worktree lease ID") or report["version"] != _expect_int(expected_version, "expected worktree lease version", 1, 2**63 - 2):
        raise ProtocolError(
            "receipt_cas_mismatch",
            "worktree execution lease CAS verification failed",
            context={"repository": report["repository"], "topic_id": report["binding"]["topic_id"]},
        )
    if str(platform_cwd) != report["binding"]["worktree_path"]:
        raise ProtocolError(
            "receipt_identity_mismatch",
            f"platform working directory does not match exact worktree binding; expected={report['binding']['worktree_path']}; observed={platform_cwd}",
            context={"repository": report["repository"], "topic_id": report["binding"]["topic_id"]},
        )
    _validate_worktree_binding_present(report["binding"], allow_descendant=True)
    return {**report, "verified": True}


def release_worktree_execution_lease(lease_path: Path, *, expected_id: str, expected_version: int) -> dict[str, Any]:
    lease_path = _expect_absolute_path(lease_path, "worktree execution lease path")
    document, data = _read_worktree_execution_lease(lease_path)
    report = _worktree_lease_report(lease_path, document, data)
    expected_path = _worktree_execution_lease_path(
        Path(report["repository"]), Path(report["binding"]["worktree_path"])
    )
    if lease_path != expected_path:
        raise ProtocolError(
            "worktree_lease_release_cas_mismatch",
            f"worktree execution lease path mismatch; expected={expected_path}; observed={lease_path}",
            context={"repository": report["repository"], "topic_id": report["binding"]["topic_id"]},
        )
    holder = report["holder"]
    if report["state"] != "held" or holder is None or holder["lease_id"] != _expect_handoff_id(expected_id, "expected worktree lease ID") or report["version"] != expected_version:
        raise ProtocolError(
            "worktree_lease_release_cas_mismatch",
            "worktree execution lease CAS release failed",
            context={"repository": report["repository"], "topic_id": report["binding"]["topic_id"]},
        )
    repository = Path(report["repository"])
    _, directory_fd, git_fd = _open_worktree_execution_directory(repository)
    guard_fd = _open_worktree_execution_guard(git_fd)
    try:
        current, _ = _read_worktree_execution_lease(lease_path)
        if current["version"] != expected_version or current["holder"] is None or current["holder"]["lease_id"] != expected_id:
            raise ProtocolError(
                "worktree_lease_release_cas_mismatch",
                "worktree execution lease changed before release",
                context={"repository": report["repository"], "topic_id": report["binding"]["topic_id"]},
            )
        updated = {**current, "holder": None, "version": current["version"] + 1}
        updated_data = _canonical_json_bytes(updated)
        _replace_private_file(directory_fd, name=lease_path.name, data=updated_data, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="worktree execution lease")
        os.fsync(directory_fd)
        return {**_worktree_lease_report(lease_path, updated, updated_data), "released": True, "released_lease_id": expected_id}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def inspect_worktree_execution_leases(repository: Path) -> dict[str, Any]:
    repository = _expect_absolute_path(repository, "repository")
    directory = _worktree_execution_directory(repository)
    leases = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            document, data = _read_worktree_execution_lease(path)
            leases.append(_worktree_lease_report(path, document, data))
    return {"repository": str(repository), "leases": leases, "state": "inspected"}


def check_execution_availability(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_WORKTREE_EXECUTION_LEASE_BYTES, label="execution availability input"), "execution availability input"), "execution availability input")
    _expect_keys(source, {"execution_mode", "repository"}, "execution availability input")
    mode = _expect_nonempty_string(source["execution_mode"], "execution_mode", max_bytes=128)
    repository = _expect_absolute_path(source["repository"], "repository")
    if mode not in {LEASE_MODE, "isolated-worktree-v1"}:
        raise ProtocolError(
            "execution_availability_invalid",
            f"unsupported execution mode: {mode!r}",
            context={"repository": str(repository)},
        )
    leases = [item for item in inspect_worktree_execution_leases(repository)["leases"] if item["state"] == "held"]
    active_execution_leases = [
        {
            "implementation_id": item["binding"]["implementation_id"],
            "lease_id": item["holder"]["lease_id"],
            "path": item["binding"]["worktree_path"],
        }
        for item in leases
    ]
    blockers: list[dict[str, Any]] = []
    worktrees = _active_git_worktrees(repository)
    if mode == LEASE_MODE:
        blockers.extend(
            {
                "kind": "worktree-execution-lease",
                "lease_id": item["lease_id"],
                "path": item["path"],
            }
            for item in active_execution_leases
        )
        ordinary = str(repository)
        repository_lease = inspect_repository_lease(repository)
        if repository_lease["state"] == "held":
            blockers.append(
                {
                    "kind": "repository-lease",
                    "lease_id": repository_lease["lease_id"],
                    "path": repository_lease["path"],
                }
            )
        for worktree in worktrees:
            if worktree["path"] != ordinary and not any(item["path"] == worktree["path"] for item in blockers):
                blockers.append({"kind": "active-worktree", "lease_id": None, "path": worktree["path"]})
        coordination = inspect_repository_coordination_lease(repository)
        if coordination["state"] == "held":
            blockers.append({"kind": "repository-critical-section", "lease_id": coordination["holder"]["lease_id"], "path": coordination["path"]})
        document = inspect_document_lease(repository)
        if (
            document["state"] == "held"
            and document["holder"] is not None
            and (
                document["holder"]["purpose"] == "git-stability-barrier"
                or document["holder"]["stage"] == "change-closure"
            )
        ):
            blockers.append(
                {
                    "kind": "document-git-critical-section",
                    "lease_id": document["holder"]["lease_id"],
                    "path": document["path"],
                }
            )
    return {
        "execution_mode": mode,
        "repository": str(repository),
        "state": "queued" if blockers else "ready",
        "blockers": blockers,
        "active_execution_leases": active_execution_leases,
        "revalidate_required": bool(blockers),
    }


def reconcile_worktree_execution_lease(
    lease_path: Path,
    *,
    expected_id: str,
    expected_version: int,
    platform_cwd: Path,
    outcome: str,
) -> dict[str, Any]:
    if outcome == "active":
        return {
            **verify_worktree_execution_lease(
                lease_path,
                expected_id=expected_id,
                expected_version=expected_version,
                platform_cwd=platform_cwd,
            ),
            "reconciled": True,
        }
    if outcome == "released":
        return {
            **release_worktree_execution_lease(
                lease_path,
                expected_id=expected_id,
                expected_version=expected_version,
            ),
            "reconciled": True,
        }
    raise ProtocolError(
        "worktree_reconciliation_invalid_outcome",
        f"unsupported worktree execution reconciliation outcome: {outcome!r}",
    )


def revalidate_integration(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_REPOSITORY_COORDINATION_LEASE_BYTES,
                label="integration revalidation input",
            ),
            "integration revalidation input",
        ),
        "integration revalidation input",
    )
    if set(source) == {"coordination_lease", "current", "expected", "repository"}:
        raise ProtocolError(
            "discussion_receipt_invalid",
            "integration revalidation no longer accepts caller-supplied expected/current self-comparison; provide a current discussion validation request",
            context={"repository": source.get("repository")},
        )
    _expect_keys(
        source,
        {"coordination_lease", "discussion_validation", "repository"},
        "integration revalidation input",
    )
    repository = _expect_absolute_path(source["repository"], "repository")
    lease = _expect_object(source["coordination_lease"], "coordination_lease")
    _expect_keys(lease, {"lease_id", "path", "version"}, "coordination_lease")
    try:
        report = verify_repository_coordination_lease(
            _expect_absolute_path(lease["path"], "coordination_lease.path"),
            expected_id=lease["lease_id"],
            expected_version=lease["version"],
        )
    except ProtocolError as error:
        raise ProtocolError(
            "integration_lease_invalid",
            "integration coordination lease identity or CAS verification failed",
            cause=str(error),
            context={"repository": str(repository)},
        ) from error
    holder = report["holder"]
    if report["repository"] != str(repository) or holder is None or holder["stage"] != "guided-implementation" or holder["purpose"] != "serial-integration":
        raise ProtocolError(
            "integration_lease_invalid",
            "integration requires the exact guided-implementation serial-integration lease",
            context={"repository": str(repository)},
        )
    authority = _verify_discussion_request(_expect_object(source["discussion_validation"], "discussion_validation"))
    if authority.get("project_path") != str(repository):
        raise ProtocolError("receipt_identity_mismatch", "discussion receipt repository does not match integration repository", context={"repository": str(repository), "topic_id": authority.get("topic_id"), "receipt_id": authority.get("receipt")})
    dimensions: list[str] = []
    current_head = _expect_git_oid(_git_text(repository, ["rev-parse", "HEAD"]).strip(), "current repository HEAD")
    if current_head != authority["base_commit"]:
        dimensions.append("source")
    active_leases = {
        item["binding"]["implementation_id"]: item
        for item in inspect_worktree_execution_leases(repository)["leases"]
        if item["state"] == "held"
    }
    for implementation in authority["active_implementations"]:
        if implementation["execution_mode"] == "isolated-worktree-v1":
            lease_report = active_leases.get(implementation["implementation_id"])
            if lease_report is None or lease_report["binding"]["topic_id"] != authority["topic_id"] or lease_report["binding"]["worktree_path"] != implementation["worktree_path"]:
                dimensions.append("active-implementations")
                break
    if dimensions:
        state = "blocked"
    else:
        state = "ready"
    return {
        "ok": True,
        "repository": str(repository),
        "state": state,
        "stale_dimensions": dimensions,
        "coordination_lease": {
            "lease_id": holder["lease_id"],
            "path": report["path"],
            "version": report["version"],
        },
        "authority": {
            "ledger_revision": authority["ledger_revision"],
            "receipt": authority["receipt"],
            "base_commit": authority["base_commit"],
            "source_identity": authority["source_identity"],
        },
        "current": {"head": current_head, "held_execution_lease_ids": sorted(active_leases)},
    }


def _document_lease_path(repository: Path) -> Path:
    repository = Path(repository)
    coordination_directory = (
        repository / ".git" if (repository / ".git").is_dir() else repository / ".codex"
    )
    return coordination_directory / DOCUMENT_LEASE_FILENAME


def _document_lease_guard_path(repository: Path) -> Path:
    repository = Path(repository)
    coordination_directory = (
        repository / ".git" if (repository / ".git").is_dir() else repository / ".codex"
    )
    return coordination_directory / DOCUMENT_LEASE_GUARD_FILENAME


def _open_document_lease_directory(repository: Path) -> tuple[Path, int]:
    repository = _expect_absolute_path(repository, "repository")
    if (repository / ".git").is_dir():
        return _open_repository_git_directory(repository)
    if Path(os.path.realpath(repository)) != repository:
        raise ProtocolError(
            f"repository path contains a symbolic-link component: {repository}"
        )
    try:
        repository_stat = os.lstat(repository)
    except OSError as error:
        raise ProtocolError(f"cannot lstat repository {repository}: {error}") from error
    if not stat.S_ISDIR(repository_stat.st_mode) or repository_stat.st_uid != os.getuid():
        raise ProtocolError(f"non-Git project must be an owned real directory: {repository}")
    coordination_directory = repository / ".codex"
    try:
        coordination_directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    try:
        coordination_stat = os.lstat(coordination_directory)
    except OSError as error:
        raise ProtocolError(
            f"cannot lstat non-Git coordination directory {coordination_directory}: {error}"
        ) from error
    if (
        not stat.S_ISDIR(coordination_stat.st_mode)
        or coordination_stat.st_uid != os.getuid()
        or _mode_bits(coordination_stat) & 0o022
    ):
        raise ProtocolError(
            f"non-Git coordination directory is unsafe: {coordination_directory}"
        )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(coordination_directory, os.O_RDONLY | nofollow)
    except OSError as error:
        raise ProtocolError(
            f"cannot open non-Git coordination directory {coordination_directory}: {error}"
        ) from error
    return coordination_directory, descriptor


def _repository_from_document_lease_path(lease_path: Path) -> Path:
    if lease_path.parent.name not in {".git", ".codex"}:
        raise ProtocolError(
            "document lease must be directly inside a project .git or .codex directory: "
            f"{lease_path}"
        )
    repository = _expect_absolute_path(lease_path.parent.parent, "repository")
    if _document_lease_path(repository) != lease_path:
        raise ProtocolError(f"document lease path is invalid for repository: {lease_path}")
    return repository


def _empty_document_lease(repository: Path) -> dict[str, Any]:
    return {
        "document_lease_version": DOCUMENT_LEASE_VERSION,
        "holder": None,
        "repository": str(repository),
        "schema": DOCUMENT_LEASE_SCHEMA,
        "version": 0,
    }


def _validate_document_lease_holder(value: Any, label: str) -> dict[str, Any]:
    holder = _expect_object(value, label)
    _expect_keys(
        holder,
        {
            "acquired_at_epoch",
            "expires_at_epoch",
            "lease_id",
            "owner_host_id",
            "owner_task_id",
            "purpose",
            "stage",
        },
        label,
    )
    acquired_at_epoch = _expect_int(
        holder["acquired_at_epoch"], f"{label}.acquired_at_epoch", 0, 2**63 - 1
    )
    expires_at_epoch = _expect_int(
        holder["expires_at_epoch"], f"{label}.expires_at_epoch", 1, 2**63 - 1
    )
    if expires_at_epoch <= acquired_at_epoch:
        raise ProtocolError(
            f"{label}.expires_at_epoch must be later than acquired_at_epoch"
        )
    purpose = _expect_nonempty_string(
        holder["purpose"], f"{label}.purpose", max_bytes=128
    )
    if purpose not in DOCUMENT_LEASE_PURPOSES:
        raise ProtocolError(
            f"{label}.purpose is unsupported; expected one of "
            f"{sorted(DOCUMENT_LEASE_PURPOSES)!r}; observed={purpose!r}"
        )
    stage = _expect_nonempty_string(holder["stage"], f"{label}.stage", max_bytes=128)
    if stage not in DOCUMENT_LEASE_STAGES:
        raise ProtocolError(
            f"{label}.stage is unsupported; expected one of "
            f"{sorted(DOCUMENT_LEASE_STAGES)!r}; observed={stage!r}"
        )
    return {
        "acquired_at_epoch": acquired_at_epoch,
        "expires_at_epoch": expires_at_epoch,
        "lease_id": _expect_handoff_id(holder["lease_id"], f"{label}.lease_id"),
        "owner_host_id": _expect_nonempty_string(
            holder["owner_host_id"], f"{label}.owner_host_id", max_bytes=256
        ),
        "owner_task_id": _expect_nonempty_string(
            holder["owner_task_id"], f"{label}.owner_task_id", max_bytes=256
        ),
        "purpose": purpose,
        "stage": stage,
    }


def _validate_document_lease_document(
    value: Any, label: str, *, repository: Path
) -> dict[str, Any]:
    document = _expect_object(value, label)
    _expect_keys(
        document,
        {"document_lease_version", "holder", "repository", "schema", "version"},
        label,
    )
    if document["document_lease_version"] != DOCUMENT_LEASE_VERSION:
        raise ProtocolError(
            f"{label}.document_lease_version mismatch; "
            f"expected={DOCUMENT_LEASE_VERSION}; "
            f"observed={document['document_lease_version']!r}"
        )
    if document["schema"] != DOCUMENT_LEASE_SCHEMA:
        raise ProtocolError(
            f"{label}.schema mismatch; expected={DOCUMENT_LEASE_SCHEMA}; "
            f"observed={document['schema']!r}"
        )
    observed_repository = str(
        _expect_absolute_path(document["repository"], f"{label}.repository")
    )
    if observed_repository != str(repository):
        raise ProtocolError(
            f"{label}.repository mismatch; expected={repository}; "
            f"observed={observed_repository}"
        )
    holder_value = document["holder"]
    holder = (
        None
        if holder_value is None
        else _validate_document_lease_holder(holder_value, f"{label}.holder")
    )
    return {
        "document_lease_version": DOCUMENT_LEASE_VERSION,
        "holder": holder,
        "repository": observed_repository,
        "schema": DOCUMENT_LEASE_SCHEMA,
        "version": _expect_int(
            document["version"], f"{label}.version", 0, 2**63 - 2
        ),
    }


def _open_document_lease_guard(git_fd: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        guard_fd = os.open(
            DOCUMENT_LEASE_GUARD_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=git_fd,
        )
    except FileExistsError:
        try:
            guard_fd = os.open(
                DOCUMENT_LEASE_GUARD_FILENAME,
                os.O_RDWR | nofollow,
                dir_fd=git_fd,
            )
        except OSError as error:
            raise ProtocolError(
                f"cannot open existing document lease guard "
                f"{DOCUMENT_LEASE_GUARD_FILENAME!r}: {error}"
            ) from error
    except OSError as error:
        raise ProtocolError(
            f"cannot open document lease guard {DOCUMENT_LEASE_GUARD_FILENAME!r}: {error}"
        ) from error
    try:
        guard_stat = os.fstat(guard_fd)
        if not stat.S_ISREG(guard_stat.st_mode):
            raise ProtocolError("document lease guard is not a regular file")
        if guard_stat.st_uid != os.getuid() or guard_stat.st_nlink != 1:
            raise ProtocolError("document lease guard identity verification failed")
        if _mode_bits(guard_stat) != 0o600:
            raise ProtocolError(
                "document lease guard mode mismatch; "
                f"expected=0600; observed={_mode_bits(guard_stat):04o}"
            )
        fcntl.flock(guard_fd, fcntl.LOCK_EX)
        return guard_fd
    except Exception:
        os.close(guard_fd)
        raise


def _read_document_lease_locked(
    git_directory: Path, git_fd: int, repository: Path
) -> dict[str, Any]:
    try:
        os.stat(DOCUMENT_LEASE_FILENAME, dir_fd=git_fd, follow_symlinks=False)
    except FileNotFoundError:
        document = _empty_document_lease(repository)
        return {
            **document,
            "file_bytes": 0,
            "file_sha256": None,
            "path": str(git_directory / DOCUMENT_LEASE_FILENAME),
        }
    except OSError as error:
        raise ProtocolError(
            f"cannot inspect document lease {git_directory / DOCUMENT_LEASE_FILENAME}: {error}"
        ) from error
    data, _ = _read_runtime_file(
        git_fd,
        DOCUMENT_LEASE_FILENAME,
        max_bytes=MAX_DOCUMENT_LEASE_BYTES,
        label="document lease",
    )
    document = _validate_document_lease_document(
        _load_json_bytes(data, "document lease", require_canonical=True),
        "document lease",
        repository=repository,
    )
    return {
        **document,
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "path": str(git_directory / DOCUMENT_LEASE_FILENAME),
    }


def _write_document_lease_locked(
    git_fd: int,
    *,
    document: dict[str, Any],
    existed: bool,
) -> None:
    data = _canonical_json_bytes(document)
    if not 1 <= len(data) <= MAX_DOCUMENT_LEASE_BYTES:
        raise ProtocolError(
            f"document lease exceeds {MAX_DOCUMENT_LEASE_BYTES} bytes; observed={len(data)}"
        )
    if existed:
        _replace_private_file(
            git_fd,
            name=DOCUMENT_LEASE_FILENAME,
            data=data,
            max_bytes=MAX_DOCUMENT_LEASE_BYTES,
            label="document lease",
        )
    else:
        _publish_group(git_fd, [(DOCUMENT_LEASE_FILENAME, data)])
    os.fsync(git_fd)


def _document_lease_state(report: dict[str, Any], *, now_epoch: int) -> dict[str, Any]:
    holder = report["holder"]
    if holder is None:
        state = "available"
        remaining_seconds = 0
    elif holder["expires_at_epoch"] <= now_epoch:
        state = "expired"
        remaining_seconds = 0
    else:
        state = "held"
        remaining_seconds = holder["expires_at_epoch"] - now_epoch
    return {
        **report,
        "now_epoch": now_epoch,
        "remaining_seconds": remaining_seconds,
        "state": state,
    }


def inspect_document_lease(repository: Path) -> dict[str, Any]:
    repository = _expect_absolute_path(repository, "repository")
    git_directory, git_fd = _open_document_lease_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_document_lease_guard(git_fd)
        report = _read_document_lease_locked(git_directory, git_fd, repository)
        return _document_lease_state(report, now_epoch=_now_epoch())
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def _attempt_acquire_document_lease(
    repository: Path,
    *,
    lease_id: str,
    owner_task_id: str,
    owner_host_id: str,
    stage: str,
    purpose: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    git_directory, git_fd = _open_document_lease_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_document_lease_guard(git_fd)
        current = _read_document_lease_locked(git_directory, git_fd, repository)
        now_epoch = _now_epoch()
        state = _document_lease_state(current, now_epoch=now_epoch)
        if state["state"] == "held":
            return {**state, "acquired": False}
        replaced_expired_lease_id = (
            current["holder"]["lease_id"] if state["state"] == "expired" else None
        )
        version = current["version"] + 1
        document = {
            "document_lease_version": DOCUMENT_LEASE_VERSION,
            "holder": {
                "acquired_at_epoch": now_epoch,
                "expires_at_epoch": now_epoch + ttl_seconds,
                "lease_id": lease_id,
                "owner_host_id": owner_host_id,
                "owner_task_id": owner_task_id,
                "purpose": purpose,
                "stage": stage,
            },
            "repository": str(repository),
            "schema": DOCUMENT_LEASE_SCHEMA,
            "version": version,
        }
        _write_document_lease_locked(
            git_fd, document=document, existed=current["file_bytes"] > 0
        )
        written = _read_document_lease_locked(git_directory, git_fd, repository)
        return {
            **_document_lease_state(written, now_epoch=now_epoch),
            "acquired": True,
            "replaced_expired_lease_id": replaced_expired_lease_id,
        }
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def acquire_document_lease(
    input_path: Path,
    *,
    wait_seconds: int = DOCUMENT_LEASE_WAIT_SECONDS,
    max_retries: int = DOCUMENT_LEASE_MAX_RETRIES,
) -> dict[str, Any]:
    data = _read_regular_file(
        Path(input_path),
        max_bytes=MAX_DOCUMENT_LEASE_BYTES,
        label="document lease input",
    )
    source = _expect_object(
        _load_json_bytes(data, "document lease input"), "document lease input"
    )
    _expect_keys(
        source,
        {
            "owner_host_id",
            "owner_task_id",
            "purpose",
            "repository",
            "stage",
            "ttl_seconds",
        },
        "document lease input",
    )
    repository = _expect_absolute_path(
        source["repository"], "document lease input.repository"
    )
    owner_task_id = _expect_nonempty_string(
        source["owner_task_id"],
        "document lease input.owner_task_id",
        max_bytes=256,
    )
    owner_host_id = _expect_nonempty_string(
        source["owner_host_id"],
        "document lease input.owner_host_id",
        max_bytes=256,
    )
    stage = _expect_nonempty_string(
        source["stage"], "document lease input.stage", max_bytes=128
    )
    if stage not in DOCUMENT_LEASE_STAGES:
        raise ProtocolError(
            "document lease input.stage is unsupported; expected one of "
            f"{sorted(DOCUMENT_LEASE_STAGES)!r}; observed={stage!r}"
        )
    purpose = _expect_nonempty_string(
        source["purpose"], "document lease input.purpose", max_bytes=128
    )
    if purpose not in DOCUMENT_LEASE_PURPOSES:
        raise ProtocolError(
            "document lease input.purpose is unsupported; expected one of "
            f"{sorted(DOCUMENT_LEASE_PURPOSES)!r}; observed={purpose!r}"
        )
    ttl_seconds = _expect_int(
        source["ttl_seconds"],
        "document lease input.ttl_seconds",
        1,
        MAX_DOCUMENT_LEASE_TTL_SECONDS,
    )
    wait_seconds = _expect_int(wait_seconds, "wait_seconds", 0, 60)
    max_retries = _expect_int(
        max_retries, "max_retries", 0, DOCUMENT_LEASE_MAX_RETRIES
    )
    lease_id = secrets.token_hex(16)
    waited_seconds = 0
    last_report: dict[str, Any] | None = None
    for attempt in range(max_retries + 1):
        report = _attempt_acquire_document_lease(
            repository,
            lease_id=lease_id,
            owner_task_id=owner_task_id,
            owner_host_id=owner_host_id,
            stage=stage,
            purpose=purpose,
            ttl_seconds=ttl_seconds,
        )
        last_report = report
        if report["acquired"]:
            return {
                **report,
                "attempts": attempt + 1,
                "waited_seconds": waited_seconds,
            }
        if attempt == max_retries:
            break
        time.sleep(wait_seconds)
        waited_seconds += wait_seconds
    assert last_report is not None
    return {
        **last_report,
        "attempts": max_retries + 1,
        "state": "timeout",
        "waited_seconds": waited_seconds,
    }


def verify_document_lease(
    lease_path: Path, *, expected_id: str, expected_version: int
) -> dict[str, Any]:
    lease_path = Path(lease_path)
    if not lease_path.is_absolute() or lease_path.name != DOCUMENT_LEASE_FILENAME:
        raise ProtocolError(f"document lease path is invalid: {lease_path}")
    repository = _repository_from_document_lease_path(lease_path)
    report = inspect_document_lease(repository)
    holder = report["holder"]
    expected_id = _expect_handoff_id(expected_id, "expected document lease ID")
    expected_version = _expect_int(
        expected_version, "expected document lease version", 1, 2**63 - 2
    )
    if report["path"] != str(lease_path):
        raise ProtocolError(
            f"document lease path mismatch; expected={lease_path}; observed={report['path']}"
        )
    if report["state"] != "held" or holder is None:
        raise ProtocolError(
            f"document lease is not validly held; state={report['state']}"
        )
    if report["version"] != expected_version:
        raise ProtocolError(
            "document lease version mismatch; "
            f"expected={expected_version}; observed={report['version']}"
        )
    if holder["lease_id"] != expected_id:
        raise ProtocolError(
            "document lease ID mismatch; "
            f"expected={expected_id}; observed={holder['lease_id']}"
        )
    return {**report, "verified": True}


def renew_document_lease(
    lease_path: Path,
    *,
    expected_id: str,
    expected_version: int,
    ttl_seconds: int,
) -> dict[str, Any]:
    lease_path = Path(lease_path)
    if not lease_path.is_absolute() or lease_path.name != DOCUMENT_LEASE_FILENAME:
        raise ProtocolError(f"document lease path is invalid: {lease_path}")
    repository = _repository_from_document_lease_path(lease_path)
    expected_id = _expect_handoff_id(expected_id, "expected document lease ID")
    expected_version = _expect_int(
        expected_version, "expected document lease version", 1, 2**63 - 2
    )
    ttl_seconds = _expect_int(
        ttl_seconds, "ttl_seconds", 1, MAX_DOCUMENT_LEASE_TTL_SECONDS
    )
    git_directory, git_fd = _open_document_lease_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_document_lease_guard(git_fd)
        current = _read_document_lease_locked(git_directory, git_fd, repository)
        now_epoch = _now_epoch()
        state = _document_lease_state(current, now_epoch=now_epoch)
        holder = current["holder"]
        if state["state"] != "held" or holder is None:
            raise ProtocolError(
                f"cannot renew document lease in state {state['state']!r}"
            )
        if current["version"] != expected_version or holder["lease_id"] != expected_id:
            raise ProtocolError(
                "document lease CAS mismatch during renewal; "
                f"expected_version={expected_version}; observed_version={current['version']}; "
                f"expected_id={expected_id}; observed_id={holder['lease_id']}"
            )
        document = {
            "document_lease_version": DOCUMENT_LEASE_VERSION,
            "holder": {**holder, "expires_at_epoch": now_epoch + ttl_seconds},
            "repository": str(repository),
            "schema": DOCUMENT_LEASE_SCHEMA,
            "version": current["version"] + 1,
        }
        _write_document_lease_locked(git_fd, document=document, existed=True)
        written = _read_document_lease_locked(git_directory, git_fd, repository)
        return {
            **_document_lease_state(written, now_epoch=now_epoch),
            "previous_version": expected_version,
            "renewed": True,
        }
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def release_document_lease(
    lease_path: Path, *, expected_id: str, expected_version: int
) -> dict[str, Any]:
    lease_path = Path(lease_path)
    if not lease_path.is_absolute() or lease_path.name != DOCUMENT_LEASE_FILENAME:
        raise ProtocolError(f"document lease path is invalid: {lease_path}")
    repository = _repository_from_document_lease_path(lease_path)
    expected_id = _expect_handoff_id(expected_id, "expected document lease ID")
    expected_version = _expect_int(
        expected_version, "expected document lease version", 1, 2**63 - 2
    )
    git_directory, git_fd = _open_document_lease_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_document_lease_guard(git_fd)
        current = _read_document_lease_locked(git_directory, git_fd, repository)
        holder = current["holder"]
        observed_id = holder["lease_id"] if holder is not None else None
        if current["version"] != expected_version or observed_id != expected_id:
            raise ProtocolError(
                "document lease CAS mismatch during release; "
                f"expected_version={expected_version}; observed_version={current['version']}; "
                f"expected_id={expected_id}; observed_id={observed_id}"
            )
        document = {
            "document_lease_version": DOCUMENT_LEASE_VERSION,
            "holder": None,
            "repository": str(repository),
            "schema": DOCUMENT_LEASE_SCHEMA,
            "version": current["version"] + 1,
        }
        _write_document_lease_locked(git_fd, document=document, existed=True)
        written = _read_document_lease_locked(git_directory, git_fd, repository)
        return {
            **_document_lease_state(written, now_epoch=_now_epoch()),
            "previous_version": expected_version,
            "released": True,
            "released_lease_id": expected_id,
        }
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def _repository_coordination_lease_path(repository: Path) -> Path:
    repository = Path(repository)
    return repository / ".git" / REPOSITORY_COORDINATION_LEASE_FILENAME


def _repository_from_coordination_lease_path(lease_path: Path) -> Path:
    if lease_path.parent.name != ".git":
        raise ProtocolError(
            "repository coordination lease must be directly inside a repository .git directory"
        )
    repository = _expect_absolute_path(lease_path.parent.parent, "repository")
    if _repository_coordination_lease_path(repository) != lease_path:
        raise ProtocolError(
            f"repository coordination lease path is invalid: {lease_path}"
        )
    return repository


def _validate_repository_coordination_holder(
    value: Any, label: str
) -> dict[str, Any]:
    holder = _expect_object(value, label)
    _expect_keys(
        holder,
        {
            "acquired_at_epoch",
            "expires_at_epoch",
            "lease_id",
            "owner_host_id",
            "owner_task_id",
            "purpose",
            "stage",
        },
        label,
    )
    acquired_at_epoch = _expect_int(
        holder["acquired_at_epoch"], f"{label}.acquired_at_epoch", 0, 2**63 - 1
    )
    expires_at_epoch = _expect_int(
        holder["expires_at_epoch"], f"{label}.expires_at_epoch", 1, 2**63 - 1
    )
    if expires_at_epoch <= acquired_at_epoch:
        raise ProtocolError(
            f"{label}.expires_at_epoch must be later than acquired_at_epoch"
        )
    stage = _expect_nonempty_string(holder["stage"], f"{label}.stage", max_bytes=128)
    purpose = _expect_nonempty_string(
        holder["purpose"], f"{label}.purpose", max_bytes=128
    )
    if stage not in REPOSITORY_COORDINATION_LEASE_STAGES:
        raise ProtocolError(f"{label}.stage is unsupported: {stage!r}")
    if purpose not in REPOSITORY_COORDINATION_LEASE_PURPOSES:
        raise ProtocolError(f"{label}.purpose is unsupported: {purpose!r}")
    return {
        "acquired_at_epoch": acquired_at_epoch,
        "expires_at_epoch": expires_at_epoch,
        "lease_id": _expect_handoff_id(holder["lease_id"], f"{label}.lease_id"),
        "owner_host_id": _expect_nonempty_string(
            holder["owner_host_id"], f"{label}.owner_host_id", max_bytes=256
        ),
        "owner_task_id": _expect_nonempty_string(
            holder["owner_task_id"], f"{label}.owner_task_id", max_bytes=256
        ),
        "purpose": purpose,
        "stage": stage,
    }


def _empty_repository_coordination_lease(repository: Path) -> dict[str, Any]:
    return {
        "holder": None,
        "repository": str(repository),
        "repository_coordination_lease_version": REPOSITORY_COORDINATION_LEASE_VERSION,
        "schema": REPOSITORY_COORDINATION_LEASE_SCHEMA,
        "version": 0,
    }


def _validate_repository_coordination_document(
    value: Any, label: str, *, repository: Path
) -> dict[str, Any]:
    document = _expect_object(value, label)
    _expect_keys(
        document,
        {
            "holder",
            "repository",
            "repository_coordination_lease_version",
            "schema",
            "version",
        },
        label,
    )
    if document["repository_coordination_lease_version"] != REPOSITORY_COORDINATION_LEASE_VERSION:
        raise ProtocolError(f"{label}.repository_coordination_lease_version mismatch")
    if document["schema"] != REPOSITORY_COORDINATION_LEASE_SCHEMA:
        raise ProtocolError(f"{label}.schema mismatch")
    observed_repository = str(
        _expect_absolute_path(document["repository"], f"{label}.repository")
    )
    if observed_repository != str(repository):
        raise ProtocolError(f"{label}.repository mismatch")
    holder = (
        None
        if document["holder"] is None
        else _validate_repository_coordination_holder(
            document["holder"], f"{label}.holder"
        )
    )
    return {
        "holder": holder,
        "repository": observed_repository,
        "repository_coordination_lease_version": REPOSITORY_COORDINATION_LEASE_VERSION,
        "schema": REPOSITORY_COORDINATION_LEASE_SCHEMA,
        "version": _expect_int(document["version"], f"{label}.version", 0, 2**63 - 2),
    }


def _open_repository_coordination_guard(git_fd: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        guard_fd = os.open(
            REPOSITORY_COORDINATION_LEASE_GUARD_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=git_fd,
        )
    except FileExistsError:
        guard_fd = os.open(
            REPOSITORY_COORDINATION_LEASE_GUARD_FILENAME,
            os.O_RDWR | nofollow,
            dir_fd=git_fd,
        )
    try:
        guard_stat = os.fstat(guard_fd)
        if (
            not stat.S_ISREG(guard_stat.st_mode)
            or guard_stat.st_uid != os.getuid()
            or guard_stat.st_nlink != 1
            or _mode_bits(guard_stat) != 0o600
        ):
            raise ProtocolError("repository coordination lease guard is unsafe")
        fcntl.flock(guard_fd, fcntl.LOCK_EX)
        return guard_fd
    except Exception:
        os.close(guard_fd)
        raise


def _read_repository_coordination_locked(
    git_directory: Path, git_fd: int, repository: Path
) -> dict[str, Any]:
    try:
        os.stat(
            REPOSITORY_COORDINATION_LEASE_FILENAME,
            dir_fd=git_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        document = _empty_repository_coordination_lease(repository)
        return {
            **document,
            "file_bytes": 0,
            "file_sha256": None,
            "path": str(git_directory / REPOSITORY_COORDINATION_LEASE_FILENAME),
        }
    data, _ = _read_runtime_file(
        git_fd,
        REPOSITORY_COORDINATION_LEASE_FILENAME,
        max_bytes=MAX_REPOSITORY_COORDINATION_LEASE_BYTES,
        label="repository coordination lease",
    )
    document = _validate_repository_coordination_document(
        _load_json_bytes(
            data, "repository coordination lease", require_canonical=True
        ),
        "repository coordination lease",
        repository=repository,
    )
    return {
        **document,
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "path": str(git_directory / REPOSITORY_COORDINATION_LEASE_FILENAME),
    }


def _write_repository_coordination_locked(
    git_fd: int, *, document: dict[str, Any], existed: bool
) -> None:
    data = _canonical_json_bytes(document)
    if existed:
        _replace_private_file(
            git_fd,
            name=REPOSITORY_COORDINATION_LEASE_FILENAME,
            data=data,
            max_bytes=MAX_REPOSITORY_COORDINATION_LEASE_BYTES,
            label="repository coordination lease",
        )
    else:
        _publish_group(git_fd, [(REPOSITORY_COORDINATION_LEASE_FILENAME, data)])
    os.fsync(git_fd)


def _repository_coordination_state(
    report: dict[str, Any], *, now_epoch: int
) -> dict[str, Any]:
    holder = report["holder"]
    if holder is None:
        state = "available"
        remaining_seconds = 0
    elif holder["expires_at_epoch"] <= now_epoch:
        state = "expired"
        remaining_seconds = 0
    else:
        state = "held"
        remaining_seconds = holder["expires_at_epoch"] - now_epoch
    return {
        **report,
        "now_epoch": now_epoch,
        "remaining_seconds": remaining_seconds,
        "state": state,
    }


def inspect_repository_coordination_lease(repository: Path) -> dict[str, Any]:
    repository = _expect_absolute_path(repository, "repository")
    git_directory, git_fd = _open_repository_git_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_repository_coordination_guard(git_fd)
        report = _read_repository_coordination_locked(
            git_directory, git_fd, repository
        )
        return _repository_coordination_state(report, now_epoch=_now_epoch())
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def acquire_repository_coordination_lease(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_REPOSITORY_COORDINATION_LEASE_BYTES,
                label="repository coordination lease input",
            ),
            "repository coordination lease input",
        ),
        "repository coordination lease input",
    )
    _expect_keys(
        source,
        {
            "owner_host_id",
            "owner_task_id",
            "purpose",
            "repository",
            "stage",
            "ttl_seconds",
        },
        "repository coordination lease input",
    )
    repository = _expect_absolute_path(source["repository"], "repository")
    stage = _expect_nonempty_string(source["stage"], "stage", max_bytes=128)
    purpose = _expect_nonempty_string(source["purpose"], "purpose", max_bytes=128)
    if stage not in REPOSITORY_COORDINATION_LEASE_STAGES:
        raise ProtocolError(f"repository coordination stage is unsupported: {stage!r}")
    if purpose not in REPOSITORY_COORDINATION_LEASE_PURPOSES:
        raise ProtocolError(f"repository coordination purpose is unsupported: {purpose!r}")
    ttl_seconds = _expect_int(
        source["ttl_seconds"],
        "ttl_seconds",
        1,
        MAX_REPOSITORY_COORDINATION_LEASE_TTL_SECONDS,
    )
    git_directory, git_fd = _open_repository_git_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_repository_coordination_guard(git_fd)
        current = _read_repository_coordination_locked(
            git_directory, git_fd, repository
        )
        now_epoch = _now_epoch()
        state = _repository_coordination_state(current, now_epoch=now_epoch)
        if state["state"] == "held":
            return {**state, "acquired": False}
        lease_id = secrets.token_hex(16)
        document = {
            "holder": {
                "acquired_at_epoch": now_epoch,
                "expires_at_epoch": now_epoch + ttl_seconds,
                "lease_id": lease_id,
                "owner_host_id": _expect_nonempty_string(
                    source["owner_host_id"], "owner_host_id", max_bytes=256
                ),
                "owner_task_id": _expect_nonempty_string(
                    source["owner_task_id"], "owner_task_id", max_bytes=256
                ),
                "purpose": purpose,
                "stage": stage,
            },
            "repository": str(repository),
            "repository_coordination_lease_version": REPOSITORY_COORDINATION_LEASE_VERSION,
            "schema": REPOSITORY_COORDINATION_LEASE_SCHEMA,
            "version": current["version"] + 1,
        }
        _write_repository_coordination_locked(
            git_fd, document=document, existed=current["file_bytes"] > 0
        )
        written = _read_repository_coordination_locked(
            git_directory, git_fd, repository
        )
        return {
            **_repository_coordination_state(written, now_epoch=now_epoch),
            "acquired": True,
            "replaced_expired_lease_id": (
                current["holder"]["lease_id"] if state["state"] == "expired" else None
            ),
        }
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def verify_repository_coordination_lease(
    lease_path: Path, *, expected_id: str, expected_version: int
) -> dict[str, Any]:
    lease_path = Path(lease_path)
    if (
        not lease_path.is_absolute()
        or lease_path.name != REPOSITORY_COORDINATION_LEASE_FILENAME
    ):
        raise ProtocolError(f"repository coordination lease path is invalid: {lease_path}")
    repository = _repository_from_coordination_lease_path(lease_path)
    report = inspect_repository_coordination_lease(repository)
    holder = report["holder"]
    expected_id = _expect_handoff_id(expected_id, "expected repository coordination lease ID")
    expected_version = _expect_int(expected_version, "expected version", 1, 2**63 - 2)
    if (
        report["path"] != str(lease_path)
        or report["state"] != "held"
        or holder is None
        or report["version"] != expected_version
        or holder["lease_id"] != expected_id
    ):
        raise ProtocolError(
            "repository coordination lease verification failed; "
            f"expected_path={lease_path}; observed_path={report['path']}; "
            f"expected_state=held; observed_state={report['state']}; "
            f"expected_version={expected_version}; observed_version={report['version']}; "
            f"expected_id={expected_id}; observed_id={holder['lease_id'] if holder else None}"
        )
    return {**report, "verified": True}


def release_repository_coordination_lease(
    lease_path: Path, *, expected_id: str, expected_version: int
) -> dict[str, Any]:
    lease_path = Path(lease_path)
    repository = _repository_from_coordination_lease_path(lease_path)
    expected_id = _expect_handoff_id(expected_id, "expected repository coordination lease ID")
    expected_version = _expect_int(expected_version, "expected version", 1, 2**63 - 2)
    git_directory, git_fd = _open_repository_git_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_repository_coordination_guard(git_fd)
        current = _read_repository_coordination_locked(
            git_directory, git_fd, repository
        )
        holder = current["holder"]
        if (
            current["version"] != expected_version
            or holder is None
            or holder["lease_id"] != expected_id
        ):
            raise ProtocolError(
                "repository coordination lease CAS mismatch during release; "
                f"expected_version={expected_version}; observed_version={current['version']}; "
                f"expected_id={expected_id}; observed_id={holder['lease_id'] if holder else None}"
            )
        document = {
            "holder": None,
            "repository": str(repository),
            "repository_coordination_lease_version": REPOSITORY_COORDINATION_LEASE_VERSION,
            "schema": REPOSITORY_COORDINATION_LEASE_SCHEMA,
            "version": current["version"] + 1,
        }
        _write_repository_coordination_locked(
            git_fd, document=document, existed=True
        )
        written = _read_repository_coordination_locked(
            git_directory, git_fd, repository
        )
        return {
            **_repository_coordination_state(written, now_epoch=_now_epoch()),
            "released": True,
            "released_lease_id": expected_id,
            "previous_version": expected_version,
        }
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def _supervision_filename(
    direction: str, sequence: str, part_index: str, part_count: str
) -> str:
    return f"{direction}-{sequence}-{part_index}-of-{part_count}.json"


def _make_supervision_document(
    *,
    handoff_id: str,
    direction: str,
    sequence: str,
    kind: str,
    part_index: str,
    part_count: str,
    payload: bytes,
    package_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    complete = (
        f"SUPERVISION_COMPLETE:{handoff_id}:{direction}:{sequence}:"
        f"{part_index}:{part_count}"
    )
    document = {
        "complete": complete,
        "direction": direction,
        "handoff_id": handoff_id,
        "kind": kind,
        "package_sha256": package_sha256,
        "part_count": part_count,
        "part_index": part_index,
        "payload_b64": base64.b64encode(payload).decode("ascii"),
        "payload_bytes": len(payload),
        "payload_sha256": _sha256(payload),
        "sequence": sequence,
        "supervision_version": SUPERVISION_VERSION,
    }
    data = _canonical_json_bytes(document)
    if len(data) > MAX_SUPERVISION_FILE_BYTES:
        raise ProtocolError(
            f"supervision file exceeds {MAX_SUPERVISION_FILE_BYTES} bytes; observed={len(data)}"
        )
    return document, data


def _validate_supervision_document(
    data: bytes, path: Path
) -> tuple[dict[str, Any], bytes]:
    document = _expect_object(
        _load_json_bytes(data, f"supervision file {path}", require_canonical=True),
        f"supervision file {path}",
    )
    _expect_keys(
        document,
        {
            "complete",
            "direction",
            "handoff_id",
            "kind",
            "package_sha256",
            "part_count",
            "part_index",
            "payload_b64",
            "payload_bytes",
            "payload_sha256",
            "sequence",
            "supervision_version",
        },
        f"supervision file {path}",
    )
    if document["supervision_version"] != SUPERVISION_VERSION:
        raise ProtocolError(
            f"supervision_version mismatch at {path}; expected={SUPERVISION_VERSION}; observed={document['supervision_version']!r}"
        )
    handoff_id = _expect_handoff_id(document["handoff_id"], f"{path}.handoff_id")
    direction = _expect_string(document["direction"], f"{path}.direction")
    if direction not in DIRECTIONS:
        raise ProtocolError(f"invalid supervision direction at {path}: {direction!r}")
    sequence = _expect_four_digits(document["sequence"], f"{path}.sequence")
    kind = _expect_string(document["kind"], f"{path}.kind")
    if not KIND_RE.fullmatch(kind):
        raise ProtocolError(f"invalid supervision kind at {path}: {kind!r}")
    part_index = _expect_four_digits(document["part_index"], f"{path}.part_index")
    part_count = _expect_four_digits(document["part_count"], f"{path}.part_count")
    if int(part_index) > int(part_count) or int(part_count) > MAX_PACKAGE_PARTS:
        raise ProtocolError(
            f"invalid supervision part position at {path}: {part_index}/{part_count}"
        )
    payload_bytes = _expect_int(
        document["payload_bytes"], f"{path}.payload_bytes", 1, MAX_INPUT_BYTES
    )
    payload_sha = _expect_sha256(document["payload_sha256"], f"{path}.payload_sha256")
    _expect_sha256(document["package_sha256"], f"{path}.package_sha256")
    payload_b64 = _expect_string(
        document["payload_b64"], f"{path}.payload_b64", max_bytes=MAX_INPUT_BYTES * 2
    )
    try:
        payload = base64.b64decode(payload_b64.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise ProtocolError(f"invalid strict base64 payload at {path}: {error}") from error
    if base64.b64encode(payload).decode("ascii") != payload_b64:
        raise ProtocolError(f"non-canonical base64 payload at {path}")
    if len(payload) != payload_bytes:
        raise ProtocolError(
            f"payload byte count mismatch at {path}; expected={payload_bytes}; observed={len(payload)}"
        )
    if _sha256(payload) != payload_sha:
        raise ProtocolError(
            f"payload SHA-256 mismatch at {path}; expected={payload_sha}; observed={_sha256(payload)}"
        )
    complete = (
        f"SUPERVISION_COMPLETE:{handoff_id}:{direction}:{sequence}:"
        f"{part_index}:{part_count}"
    )
    if document["complete"] != complete:
        raise ProtocolError(
            f"supervision completeness marker mismatch at {path}; expected={complete!r}; observed={document['complete']!r}"
        )
    filename = _supervision_filename(direction, sequence, part_index, part_count)
    if path.name != filename:
        raise ProtocolError(
            f"supervision filename mismatch; expected={filename}; observed={path.name}"
        )
    return document, payload


def _supervision_entry(path: Path, document: dict[str, Any], data: bytes) -> dict[str, Any]:
    return {
        "complete": document["complete"],
        "direction": document["direction"],
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "kind": document["kind"],
        "part_count": document["part_count"],
        "part_index": document["part_index"],
        "path": str(path),
        "sequence": document["sequence"],
    }


def _validate_manifest_entry(
    value: Any, *, handoff_id: str, id_directory: Path, label: str
) -> dict[str, Any]:
    entry = _expect_object(value, label)
    _expect_keys(
        entry,
        {
            "complete",
            "direction",
            "file_bytes",
            "file_sha256",
            "kind",
            "part_count",
            "part_index",
            "path",
            "sequence",
        },
        label,
    )
    direction = _expect_string(entry["direction"], f"{label}.direction")
    if direction not in DIRECTIONS:
        raise ProtocolError(f"{label}.direction is invalid: {direction!r}")
    sequence = _expect_four_digits(entry["sequence"], f"{label}.sequence")
    kind = _expect_string(entry["kind"], f"{label}.kind")
    if not KIND_RE.fullmatch(kind):
        raise ProtocolError(f"{label}.kind is invalid: {kind!r}")
    part_index = _expect_four_digits(entry["part_index"], f"{label}.part_index")
    part_count = _expect_four_digits(entry["part_count"], f"{label}.part_count")
    if int(part_index) > int(part_count) or int(part_count) > MAX_PACKAGE_PARTS:
        raise ProtocolError(f"{label} has invalid part position {part_index}/{part_count}")
    _expect_int(entry["file_bytes"], f"{label}.file_bytes", 1, MAX_SUPERVISION_FILE_BYTES)
    _expect_sha256(entry["file_sha256"], f"{label}.file_sha256")
    path_text = _expect_string(entry["path"], f"{label}.path")
    entry_path = Path(path_text)
    expected_name = _supervision_filename(direction, sequence, part_index, part_count)
    expected_path = id_directory / expected_name
    if not entry_path.is_absolute() or entry_path != expected_path:
        raise ProtocolError(
            f"{label}.path mismatch; expected={expected_path}; observed={entry_path}"
        )
    complete = (
        f"SUPERVISION_COMPLETE:{handoff_id}:{direction}:{sequence}:"
        f"{part_index}:{part_count}"
    )
    if entry["complete"] != complete:
        raise ProtocolError(
            f"{label}.complete mismatch; expected={complete!r}; observed={entry['complete']!r}"
        )
    return entry


def _decode_manifest_payload(
    payload: bytes, *, handoff_id: str, id_directory: Path, label: str
) -> dict[str, Any]:
    manifest = _expect_object(
        _load_json_bytes(payload, label, require_canonical=True), label
    )
    _expect_keys(
        manifest,
        {"entries", "entry_count", "handoff_id", "manifest_version"},
        label,
    )
    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise ProtocolError(
            f"{label}.manifest_version mismatch; expected={MANIFEST_VERSION}; observed={manifest['manifest_version']!r}"
        )
    if manifest["handoff_id"] != handoff_id:
        raise ProtocolError(
            f"{label}.handoff_id mismatch; expected={handoff_id}; observed={manifest['handoff_id']!r}"
        )
    entries_value = _expect_list(manifest["entries"], f"{label}.entries")
    entry_count = _expect_int(
        manifest["entry_count"], f"{label}.entry_count", 1, 100_000
    )
    if len(entries_value) != entry_count:
        raise ProtocolError(
            f"{label}.entry_count mismatch; declared={entry_count}; observed={len(entries_value)}"
        )
    entries: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, value in enumerate(entries_value):
        entry = _validate_manifest_entry(
            value,
            handoff_id=handoff_id,
            id_directory=id_directory,
            label=f"{label}.entries[{index}]",
        )
        if entry["path"] in seen_paths:
            raise ProtocolError(f"{label} contains duplicate path: {entry['path']}")
        seen_paths.add(entry["path"])
        entries.append(entry)
    return {
        "entries": entries,
        "entry_count": entry_count,
        "handoff_id": handoff_id,
        "manifest_version": MANIFEST_VERSION,
    }


def _read_supervision_file(
    id_fd: int, id_directory: Path, name: str
) -> tuple[dict[str, Any], bytes, bytes, os.stat_result]:
    data, file_stat = _read_runtime_file(
        id_fd,
        name,
        max_bytes=MAX_SUPERVISION_FILE_BYTES,
        label="supervision file",
    )
    document, payload = _validate_supervision_document(data, id_directory / name)
    return document, payload, data, file_stat


def _require_entry_matches_file(
    entry: dict[str, Any], document: dict[str, Any], data: bytes, label: str
) -> None:
    observed = _supervision_entry(Path(entry["path"]), document, data)
    if observed != entry:
        raise ProtocolError(
            f"{label} metadata does not match immutable file; expected={entry!r}; observed={observed!r}"
        )


def _validate_package_groups(
    entries: list[dict[str, Any]],
    file_objects: dict[str, tuple[dict[str, Any], bytes]],
) -> None:
    previous_sequence: dict[str, int] = {direction: 0 for direction in DIRECTIONS}
    seen_groups: set[tuple[str, str]] = set()
    index = 0
    while index < len(entries):
        first = entries[index]
        group_key = (first["direction"], first["sequence"])
        if group_key in seen_groups:
            raise ProtocolError(f"supervision package is non-contiguous or reused: {group_key}")
        seen_groups.add(group_key)
        sequence_number = int(first["sequence"])
        if sequence_number <= previous_sequence[first["direction"]]:
            raise ProtocolError(
                f"supervision sequence is not strictly increasing for {first['direction']}: {first['sequence']}"
            )
        previous_sequence[first["direction"]] = sequence_number
        group: list[dict[str, Any]] = []
        while index < len(entries):
            candidate = entries[index]
            if (candidate["direction"], candidate["sequence"]) != group_key:
                break
            group.append(candidate)
            index += 1
        expected_count = int(first["part_count"])
        if len(group) != expected_count:
            raise ProtocolError(
                f"supervision package part count mismatch for {group_key}; expected={expected_count}; observed={len(group)}"
            )
        expected_indices = [f"{number:04d}" for number in range(1, expected_count + 1)]
        observed_indices = [entry["part_index"] for entry in group]
        if observed_indices != expected_indices:
            raise ProtocolError(
                f"supervision package parts are not contiguous for {group_key}; expected={expected_indices}; observed={observed_indices}"
            )
        kinds = {entry["kind"] for entry in group}
        counts = {entry["part_count"] for entry in group}
        if len(kinds) != 1 or counts != {first["part_count"]}:
            raise ProtocolError(f"supervision package metadata differs across parts: {group_key}")
        payloads: list[bytes] = []
        package_hashes: set[str] = set()
        for entry in group:
            document, payload = file_objects[entry["path"]]
            payloads.append(payload)
            package_hashes.add(document["package_sha256"])
        if len(package_hashes) != 1:
            raise ProtocolError(f"supervision package hashes differ across parts: {group_key}")
        expected_package_hash = next(iter(package_hashes))
        observed_package_hash = _sha256(b"".join(payloads))
        if observed_package_hash != expected_package_hash:
            raise ProtocolError(
                f"supervision package SHA-256 mismatch for {group_key}; expected={expected_package_hash}; observed={observed_package_hash}"
            )


def verify_supervision(
    handoff_path: Path,
    manifest_path: Path,
    *,
    expected_count: int,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    handoff = verify_handoff(handoff_path, runtime_root=runtime_root)
    handoff_id = handoff["handoff_id"]
    id_directory = Path(runtime_root) / handoff_id
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute() or manifest_path.parent != id_directory:
        raise ProtocolError(
            f"manifest path must be inside the exact handoff directory {id_directory}: {manifest_path}"
        )
    root_fd, id_fd = _open_id_directory(Path(runtime_root), handoff_id)
    try:
        manifest_document, manifest_payload, manifest_data, _ = _read_supervision_file(
            id_fd, id_directory, manifest_path.name
        )
        if manifest_document["kind"] != "manifest":
            raise ProtocolError(
                f"latest supervision file must have kind='manifest': {manifest_path}"
            )
        if manifest_document["part_index"] != "0001" or manifest_document["part_count"] != "0001":
            raise ProtocolError(f"latest manifest must be one single-part file: {manifest_path}")
        manifest = _decode_manifest_payload(
            manifest_payload,
            handoff_id=handoff_id,
            id_directory=id_directory,
            label=f"decoded manifest {manifest_path}",
        )
        entries = manifest["entries"]
        if expected_count != len(entries) + 1:
            raise ProtocolError(
                f"supervision file count mismatch; expected={expected_count}; manifest-derived={len(entries) + 1}"
            )

        expected_names = {HANDOFF_FILENAME, manifest_path.name}
        expected_names.update(Path(entry["path"]).name for entry in entries)
        observed_names = set(os.listdir(id_fd))
        if observed_names != expected_names:
            raise ProtocolError(
                f"handoff directory contents mismatch; missing={sorted(expected_names - observed_names)!r}; unexpected={sorted(observed_names - expected_names)!r}"
            )

        file_objects: dict[str, tuple[dict[str, Any], bytes]] = {}
        for index, entry in enumerate(entries):
            name = Path(entry["path"]).name
            document, payload, data, _ = _read_supervision_file(
                id_fd, id_directory, name
            )
            if document["handoff_id"] != handoff_id:
                raise ProtocolError(
                    f"supervision handoff ID mismatch at {entry['path']}; expected={handoff_id}; observed={document['handoff_id']}"
                )
            _require_entry_matches_file(entry, document, data, f"manifest entry {index}")
            file_objects[entry["path"]] = (document, payload)

        for index, entry in enumerate(entries):
            if entry["kind"] != "manifest":
                continue
            prior_document, prior_payload = file_objects[entry["path"]]
            if prior_document["part_index"] != "0001" or prior_document["part_count"] != "0001":
                raise ProtocolError(f"prior manifest is not single-part: {entry['path']}")
            prior = _decode_manifest_payload(
                prior_payload,
                handoff_id=handoff_id,
                id_directory=id_directory,
                label=f"decoded prior manifest {entry['path']}",
            )
            if prior["entries"] != entries[:index]:
                raise ProtocolError(
                    f"prior manifest does not equal the cumulative prefix at {entry['path']}"
                )

        current_entry = _supervision_entry(
            manifest_path, manifest_document, manifest_data
        )
        all_entries = entries + [current_entry]
        all_objects = dict(file_objects)
        all_objects[str(manifest_path)] = (manifest_document, manifest_payload)
        _validate_package_groups(all_entries, all_objects)
        return {
            "entries": entries,
            "handoff_id": handoff_id,
            "manifest_file": current_entry,
            "supervision_file_count": len(entries) + 1,
        }
    finally:
        os.close(id_fd)
        os.close(root_fd)


def publish_supervision(
    handoff_path: Path,
    *,
    direction: str,
    kind: str,
    payload_path: Path,
    previous_manifest_path: Path | None = None,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    if direction not in DIRECTIONS:
        raise ProtocolError(f"direction must be one of {sorted(DIRECTIONS)!r}: {direction!r}")
    if not KIND_RE.fullmatch(kind) or kind == "manifest":
        raise ProtocolError(f"payload kind must be a non-manifest kebab-case token: {kind!r}")
    handoff = verify_handoff(handoff_path, runtime_root=runtime_root)
    handoff_id = handoff["handoff_id"]
    id_directory = Path(runtime_root) / handoff_id
    previous_entries: list[dict[str, Any]] = []
    previous_manifest_entry: dict[str, Any] | None = None
    if previous_manifest_path is not None:
        previous_manifest_path = Path(previous_manifest_path)
        previous = verify_supervision(
            handoff_path,
            previous_manifest_path,
            expected_count=_manifest_count_from_file(
                previous_manifest_path, handoff_id=handoff_id, runtime_root=runtime_root
            ),
            runtime_root=runtime_root,
        )
        previous_entries = previous["entries"]
        previous_manifest_entry = previous["manifest_file"]

    payload = _read_regular_file(
        Path(payload_path),
        max_bytes=MAX_PAYLOAD_PART_BYTES * MAX_PACKAGE_PARTS,
        label="supervision payload",
    )
    if not payload:
        raise ProtocolError("supervision payload must not be empty")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"supervision payload must be valid UTF-8: {error}") from error

    existing_entries = list(previous_entries)
    if previous_manifest_entry is not None:
        existing_entries.append(previous_manifest_entry)
    max_sequence = max(
        (
            int(entry["sequence"])
            for entry in existing_entries
            if entry["direction"] == direction
        ),
        default=0,
    )
    if max_sequence > 9_997:
        raise ProtocolError(
            f"no two supervision sequence values remain for direction {direction}"
        )
    payload_sequence = f"{max_sequence + 1:04d}"
    manifest_sequence = f"{max_sequence + 2:04d}"
    parts = [
        payload[offset : offset + MAX_PAYLOAD_PART_BYTES]
        for offset in range(0, len(payload), MAX_PAYLOAD_PART_BYTES)
    ]
    if not 1 <= len(parts) <= MAX_PACKAGE_PARTS:
        raise ProtocolError(
            f"supervision package must contain 1..{MAX_PACKAGE_PARTS} parts; observed={len(parts)}"
        )
    part_count = f"{len(parts):04d}"
    package_sha = _sha256(payload)
    payload_files: list[tuple[str, bytes]] = []
    payload_entries: list[dict[str, Any]] = []
    for index, part in enumerate(parts, start=1):
        part_index = f"{index:04d}"
        document, data = _make_supervision_document(
            handoff_id=handoff_id,
            direction=direction,
            sequence=payload_sequence,
            kind=kind,
            part_index=part_index,
            part_count=part_count,
            payload=part,
            package_sha256=package_sha,
        )
        name = _supervision_filename(
            direction, payload_sequence, part_index, part_count
        )
        path = id_directory / name
        payload_files.append((name, data))
        payload_entries.append(_supervision_entry(path, document, data))

    manifest_entries = existing_entries + payload_entries
    manifest_payload = _canonical_json_bytes(
        {
            "entries": manifest_entries,
            "entry_count": len(manifest_entries),
            "handoff_id": handoff_id,
            "manifest_version": MANIFEST_VERSION,
        }
    )
    manifest_document, manifest_data = _make_supervision_document(
        handoff_id=handoff_id,
        direction=direction,
        sequence=manifest_sequence,
        kind="manifest",
        part_index="0001",
        part_count="0001",
        payload=manifest_payload,
        package_sha256=_sha256(manifest_payload),
    )
    manifest_name = _supervision_filename(
        direction, manifest_sequence, "0001", "0001"
    )
    manifest_path = id_directory / manifest_name

    root_fd, id_fd = _open_id_directory(Path(runtime_root), handoff_id)
    try:
        observed_names = set(os.listdir(id_fd))
        if previous_manifest_entry is None:
            expected_names = {HANDOFF_FILENAME}
        else:
            expected_names = {HANDOFF_FILENAME, Path(previous_manifest_entry["path"]).name}
            expected_names.update(Path(entry["path"]).name for entry in previous_entries)
        if observed_names != expected_names:
            raise ProtocolError(
                f"cannot publish over unexpected handoff directory state; missing={sorted(expected_names - observed_names)!r}; unexpected={sorted(observed_names - expected_names)!r}"
            )
        _publish_group(id_fd, payload_files + [(manifest_name, manifest_data)])
    finally:
        os.close(id_fd)
        os.close(root_fd)

    expected_count = len(manifest_entries) + 1
    verified = verify_supervision(
        handoff_path,
        manifest_path,
        expected_count=expected_count,
        runtime_root=runtime_root,
    )
    return {
        "handoff_id": handoff_id,
        "manifest_file": verified["manifest_file"],
        "payload_files": payload_entries,
        "supervision_file_count": expected_count,
    }


def _manifest_count_from_file(
    manifest_path: Path, *, handoff_id: str, runtime_root: Path
) -> int:
    id_directory = Path(runtime_root) / handoff_id
    if manifest_path.parent != id_directory:
        raise ProtocolError(
            f"previous manifest path must be inside {id_directory}: {manifest_path}"
        )
    root_fd, id_fd = _open_id_directory(Path(runtime_root), handoff_id)
    try:
        document, payload, _, _ = _read_supervision_file(
            id_fd, id_directory, manifest_path.name
        )
        if document["kind"] != "manifest":
            raise ProtocolError(f"previous manifest has non-manifest kind: {manifest_path}")
        manifest = _decode_manifest_payload(
            payload,
            handoff_id=handoff_id,
            id_directory=id_directory,
            label=f"decoded previous manifest {manifest_path}",
        )
        return len(manifest["entries"]) + 1
    finally:
        os.close(id_fd)
        os.close(root_fd)


def _validate_control_common(
    control: dict[str, Any],
    *,
    includes_version: bool,
    runtime_root: Path,
    create_version: int | None = None,
) -> dict[str, Any]:
    expected_keys = {
        "candidate_commit",
        "checkpoint",
        "handoff_id",
        "manifest_file",
        "merge_commit",
        "merge_result",
        "payload_files",
        "status",
        "supervision_file_count",
    }
    control_version = create_version or CONTROL_VERSION
    ack_instruction = ACK_INSTRUCTION
    ack_policy = DUPLEX_ACK_POLICY
    ack_resend_limit = ACK_RESEND_LIMIT
    ack_text: str | None = None
    message_id: str | None = None
    message_protocol = MESSAGE_PROTOCOL
    message_type = MESSAGE_TYPE
    delivery_instruction = DELIVERY_INSTRUCTION
    delivery_policy = DELIVERY_POLICY
    delivery_check_delay_seconds = DELIVERY_CHECK_DELAY_SECONDS
    delivery_resend_limit = DELIVERY_RESEND_LIMIT
    if includes_version:
        expected_keys.add("control_version")
        control_version = _expect_int(
            control.get("control_version"),
            "control envelope.control_version",
            LEGACY_CONTROL_VERSION,
            CONTROL_VERSION,
        )
        if control_version < DUPLEX_ACK_CONTROL_VERSION:
            ack_instruction = LEGACY_ACK_INSTRUCTION
            ack_policy = LEGACY_ACK_POLICY
        if control_version >= EXPLICIT_ACK_CONTROL_VERSION and control_version < CONTROL_VERSION:
            expected_keys.update({"ack_instruction", "ack_required"})
        if control_version >= STRUCTURED_ACK_CONTROL_VERSION and control_version < CONTROL_VERSION:
            expected_keys.add("ack_policy")
        if control_version == DUPLEX_ACK_CONTROL_VERSION:
            expected_keys.update(
                {
                    "ack_format",
                    "ack_resend_limit",
                    "ack_text",
                    "message_id",
                    "message_type",
                    "protocol",
                }
            )
        if control_version == CONTROL_VERSION:
            expected_keys.update(
                {
                    "delivery_check_delay_seconds",
                    "delivery_check_required",
                    "delivery_instruction",
                    "delivery_policy",
                    "message_id",
                    "message_type",
                    "protocol",
                    "delivery_resend_limit",
                }
            )
    _expect_keys(control, expected_keys, "control envelope")
    if includes_version and EXPLICIT_ACK_CONTROL_VERSION <= control_version < CONTROL_VERSION:
        if not _expect_bool(control["ack_required"], "control envelope.ack_required"):
            raise ProtocolError("control envelope.ack_required must be true")
        ack_instruction = _expect_nonempty_string(
            control["ack_instruction"],
            "control envelope.ack_instruction",
            max_bytes=256,
        )
        if (
            control_version == EXPLICIT_ACK_CONTROL_VERSION
            and ack_instruction != LEGACY_ACK_INSTRUCTION
        ):
            raise ProtocolError(
                "control envelope.ack_instruction does not match the v2 ACK requirement"
            )
        if control_version == STRUCTURED_ACK_CONTROL_VERSION:
            ack_policy = _expect_nonempty_string(
                control["ack_policy"],
                "control envelope.ack_policy",
                max_bytes=64,
            )
            if ack_policy != LEGACY_ACK_POLICY:
                raise ProtocolError(
                    "control envelope.ack_policy mismatch; "
                    f"expected={LEGACY_ACK_POLICY!r}; observed={ack_policy!r}"
                )
        if control_version == DUPLEX_ACK_CONTROL_VERSION:
            ack_policy = _expect_nonempty_string(
                control["ack_policy"],
                "control envelope.ack_policy",
                max_bytes=64,
            )
            if ack_policy != DUPLEX_ACK_POLICY:
                raise ProtocolError(
                    f"control envelope.ack_policy mismatch; expected={DUPLEX_ACK_POLICY!r}; observed={ack_policy!r}"
                )
            if ack_instruction != ACK_INSTRUCTION:
                raise ProtocolError(
                    "control envelope.ack_instruction does not match the duplex ACK requirement"
                )
            message_protocol = _expect_nonempty_string(
                control["protocol"], "control envelope.protocol", max_bytes=64
            )
            if message_protocol != DUPLEX_ACK_POLICY:
                raise ProtocolError(
                    "control envelope.protocol mismatch; "
                    f"expected={DUPLEX_ACK_POLICY!r}; observed={message_protocol!r}"
                )
            message_type = _expect_nonempty_string(
                control["message_type"], "control envelope.message_type", max_bytes=64
            )
            if message_type != MESSAGE_TYPE:
                raise ProtocolError(
                    "control envelope.message_type mismatch; "
                    f"expected={MESSAGE_TYPE!r}; observed={message_type!r}"
                )
            if control["ack_format"] != ACK_FORMAT:
                raise ProtocolError(
                    "control envelope.ack_format mismatch; "
                    f"expected={ACK_FORMAT!r}; observed={control['ack_format']!r}"
                )
            ack_resend_limit = _expect_int(
                control["ack_resend_limit"],
                "control envelope.ack_resend_limit",
                ACK_RESEND_LIMIT,
                ACK_RESEND_LIMIT,
            )
            message_id = _expect_sha256(
                control["message_id"], "control envelope.message_id"
            )
            ack_text = _expect_nonempty_string(
                control["ack_text"], "control envelope.ack_text", max_bytes=80
            )
    if includes_version and control_version == CONTROL_VERSION:
        if not _expect_bool(
            control["delivery_check_required"],
            "control envelope.delivery_check_required",
        ):
            raise ProtocolError("control envelope.delivery_check_required must be true")
        delivery_instruction = _expect_nonempty_string(
            control["delivery_instruction"],
            "control envelope.delivery_instruction",
            max_bytes=512,
        )
        if delivery_instruction != DELIVERY_INSTRUCTION:
            raise ProtocolError(
                "control envelope.delivery_instruction does not match the observe-delivery requirement"
            )
        delivery_policy = _expect_nonempty_string(
            control["delivery_policy"],
            "control envelope.delivery_policy",
            max_bytes=64,
        )
        if delivery_policy != DELIVERY_POLICY:
            raise ProtocolError(
                "control envelope.delivery_policy mismatch; "
                f"expected={DELIVERY_POLICY!r}; observed={delivery_policy!r}"
            )
        delivery_check_delay_seconds = _expect_int(
            control["delivery_check_delay_seconds"],
            "control envelope.delivery_check_delay_seconds",
            DELIVERY_CHECK_DELAY_SECONDS,
            DELIVERY_CHECK_DELAY_SECONDS,
        )
        delivery_resend_limit = _expect_int(
            control["delivery_resend_limit"],
            "control envelope.delivery_resend_limit",
            DELIVERY_RESEND_LIMIT,
            DELIVERY_RESEND_LIMIT,
        )
        message_protocol = _expect_nonempty_string(
            control["protocol"], "control envelope.protocol", max_bytes=64
        )
        if message_protocol != MESSAGE_PROTOCOL:
            raise ProtocolError(
                "control envelope.protocol mismatch; "
                f"expected={MESSAGE_PROTOCOL!r}; observed={message_protocol!r}"
            )
        message_type = _expect_nonempty_string(
            control["message_type"], "control envelope.message_type", max_bytes=64
        )
        if message_type != MESSAGE_TYPE:
            raise ProtocolError(
                "control envelope.message_type mismatch; "
                f"expected={MESSAGE_TYPE!r}; observed={message_type!r}"
            )
        message_id = _expect_sha256(control["message_id"], "control envelope.message_id")
    handoff_id = _expect_handoff_id(control["handoff_id"])
    status = _expect_string(control["status"], "control envelope.status")
    if status not in CONTROL_STATUSES:
        raise ProtocolError(f"unsupported control status: {status!r}")
    for field in ("candidate_commit", "merge_commit", "merge_result"):
        value = control[field]
        if value is not None:
            _expect_string(value, f"control envelope.{field}", max_bytes=256)
    checkpoint = _expect_object(control["checkpoint"], "control envelope.checkpoint")
    payload_values = _expect_list(control["payload_files"], "control envelope.payload_files")
    if not payload_values:
        raise ProtocolError("control envelope.payload_files must not be empty")
    manifest_value = _expect_object(
        control["manifest_file"], "control envelope.manifest_file"
    )
    id_directory = Path(runtime_root) / handoff_id
    manifest_entry = _validate_manifest_entry(
        manifest_value,
        handoff_id=handoff_id,
        id_directory=id_directory,
        label="control envelope.manifest_file",
    )
    if manifest_entry["kind"] != "manifest":
        raise ProtocolError("control envelope.manifest_file must have kind='manifest'")
    payload_entries: list[dict[str, Any]] = []
    for index, value in enumerate(payload_values):
        entry = _validate_manifest_entry(
            value,
            handoff_id=handoff_id,
            id_directory=id_directory,
            label=f"control envelope.payload_files[{index}]",
        )
        if entry["kind"] == "manifest":
            raise ProtocolError("control envelope.payload_files cannot include a manifest")
        payload_entries.append(entry)
    routes = {(entry["direction"], entry["kind"]) for entry in payload_entries}
    if len(routes) != 1:
        raise ProtocolError("control envelope.payload_files must form one message route")
    route = next(iter(routes))
    expected_status = MESSAGE_ROUTES.get(route)
    if expected_status is None:
        raise ProtocolError(
            "unsupported supervision message route; "
            f"direction={route[0]!r}; kind={route[1]!r}"
        )
    if status != expected_status:
        raise ProtocolError(
            "control status does not match supervision message route; "
            f"expected={expected_status!r}; observed={status!r}"
        )
    count = _expect_int(
        control["supervision_file_count"],
        "control envelope.supervision_file_count",
        len(payload_entries) + 1,
        100_000,
    )
    normalized = {
        "candidate_commit": control["candidate_commit"],
        "checkpoint": checkpoint,
        "control_version": control_version,
        "handoff_id": handoff_id,
        "manifest_file": manifest_entry,
        "merge_commit": control["merge_commit"],
        "merge_result": control["merge_result"],
        "payload_files": payload_entries,
        "status": status,
        "supervision_file_count": count,
    }
    if EXPLICIT_ACK_CONTROL_VERSION <= control_version < CONTROL_VERSION:
        normalized["ack_instruction"] = ack_instruction
        normalized["ack_required"] = True
    if control_version == STRUCTURED_ACK_CONTROL_VERSION:
        normalized["ack_policy"] = ack_policy
    if control_version == DUPLEX_ACK_CONTROL_VERSION:
        normalized.update(
            {
                "ack_format": ACK_FORMAT,
                "ack_policy": ack_policy,
                "ack_resend_limit": ack_resend_limit,
                "message_type": message_type,
                "protocol": DUPLEX_ACK_POLICY,
            }
        )
        expected_message_id = _sha256(_canonical_json_bytes(normalized))
        expected_ack_text = f"ACK {expected_message_id}"
        if includes_version:
            if message_id != expected_message_id:
                raise ProtocolError(
                    "control envelope.message_id does not match the canonical message core; "
                    f"expected={expected_message_id!r}; observed={message_id!r}"
                )
            if ack_text != expected_ack_text:
                raise ProtocolError(
                    "control envelope.ack_text mismatch; "
                    f"expected={expected_ack_text!r}; observed={ack_text!r}"
                )
        normalized["ack_text"] = expected_ack_text
        normalized["message_id"] = expected_message_id
    if control_version == CONTROL_VERSION:
        normalized.update(
            {
                "delivery_check_delay_seconds": delivery_check_delay_seconds,
                "delivery_check_required": True,
                "delivery_instruction": delivery_instruction,
                "delivery_policy": delivery_policy,
                "delivery_resend_limit": delivery_resend_limit,
                "message_type": message_type,
                "protocol": message_protocol,
            }
        )
        expected_message_id = _sha256(_canonical_json_bytes(normalized))
        if includes_version and message_id != expected_message_id:
            raise ProtocolError(
                "control envelope.message_id does not match the canonical message core; "
                f"expected={expected_message_id!r}; observed={message_id!r}"
            )
        normalized["message_id"] = expected_message_id
    return normalized


def _card_core(
    normalized: dict[str, Any],
    *,
    checkpoint: str,
    summary: str,
    in_reply_to: str | None,
) -> dict[str, Any]:
    return {
        "candidate_commit": normalized["candidate_commit"],
        "checkpoint": checkpoint,
        "control_version": CARD_CONTROL_VERSION,
        "delivery_check_delay_seconds": DELIVERY_CHECK_DELAY_SECONDS,
        "delivery_check_required": True,
        "delivery_policy": DELIVERY_POLICY,
        "delivery_resend_limit": DELIVERY_RESEND_LIMIT,
        "handoff_id": normalized["handoff_id"],
        "in_reply_to": in_reply_to,
        "manifest_file": normalized["manifest_file"],
        "merge_commit": normalized["merge_commit"],
        "merge_result": normalized["merge_result"],
        "message_format": MESSAGE_CARD_FORMAT,
        "message_type": MESSAGE_TYPE,
        "payload_files": normalized["payload_files"],
        "protocol": MESSAGE_PROTOCOL,
        "status": normalized["status"],
        "summary": summary,
        "supervision_file_count": normalized["supervision_file_count"],
    }


def _card_optional(value: str | None) -> str:
    return "无" if value is None else value


def _render_control_card(core: dict[str, Any], message_id: str) -> bytes:
    payload_entries = core["payload_files"]
    direction = payload_entries[0]["direction"]
    title = (
        f"【3实现消息｜{DIRECTION_LABELS[direction]}｜"
        f"{STATUS_LABELS[core['status']]}】"
    )
    lines = [
        title,
        "",
        f"消息编号：{message_id}",
        f"交接编号：{core['handoff_id']}",
        f"回复消息：{_card_optional(core['in_reply_to'])}",
        f"监督清单：{core['manifest_file']['path']}",
        f"监督文件数：{core['supervision_file_count']}",
        f"检查点：{core['checkpoint']}",
        f"候选提交：{_card_optional(core['candidate_commit'])}",
        f"合并提交：{_card_optional(core['merge_commit'])}",
        f"合并结果：{_card_optional(core['merge_result'])}",
        "",
        "摘要：",
        core["summary"],
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _create_control_card(
    source: dict[str, Any], *, runtime_root: Path
) -> bytes:
    expected_keys = {
        "candidate_commit",
        "checkpoint",
        "handoff_id",
        "in_reply_to",
        "manifest_file",
        "merge_commit",
        "merge_result",
        "payload_files",
        "status",
        "summary",
        "supervision_file_count",
    }
    _expect_keys(source, expected_keys, "Chinese message-card input")
    checkpoint = _expect_chinese_line(
        source["checkpoint"], "Chinese message-card input.checkpoint", max_bytes=512
    )
    summary = _expect_chinese_line(
        source["summary"], "Chinese message-card input.summary", max_bytes=1_024
    )
    in_reply_to = source["in_reply_to"]
    if in_reply_to is not None:
        in_reply_to = _expect_sha256(
            in_reply_to, "Chinese message-card input.in_reply_to"
        )
    legacy_source = dict(source)
    legacy_source.pop("in_reply_to")
    legacy_source.pop("summary")
    legacy_source["checkpoint"] = {"说明": checkpoint}
    normalized = _validate_control_common(
        legacy_source,
        includes_version=False,
        runtime_root=runtime_root,
        create_version=CONTROL_VERSION,
    )
    for field in ("candidate_commit", "merge_commit", "merge_result"):
        if normalized[field] == "无":
            raise ProtocolError(
                f"Chinese message-card input.{field} must use null instead of the reserved display value '无'"
            )
        if normalized[field] is not None:
            _expect_single_line(
                normalized[field],
                f"Chinese message-card input.{field}",
                max_bytes=256,
            )
    core = _card_core(
        normalized,
        checkpoint=checkpoint,
        summary=summary,
        in_reply_to=in_reply_to,
    )
    message_id = _sha256(_canonical_json_bytes(core))
    data = _render_control_card(core, message_id)
    if len(data) > MAX_CONTROL_BYTES:
        raise ProtocolError(
            f"Chinese message card exceeds {MAX_CONTROL_BYTES} UTF-8 bytes; observed={len(data)}"
        )
    return data


def _parse_card_line(line: str, prefix: str, label: str) -> str:
    if not line.startswith(prefix):
        raise ProtocolError(f"Chinese message card is missing canonical {label}")
    value = line[len(prefix) :]
    if not value:
        raise ProtocolError(f"Chinese message card {label} must not be empty")
    return value


def _latest_payload_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not entries or entries[-1]["kind"] == "manifest":
        raise ProtocolError("latest supervision manifest has no terminal payload package")
    last = entries[-1]
    route = (last["direction"], last["sequence"], last["kind"])
    start = len(entries) - 1
    while start > 0:
        previous = entries[start - 1]
        if (
            previous["direction"],
            previous["sequence"],
            previous["kind"],
        ) != route:
            break
        start -= 1
    return entries[start:]


def _verify_control_card(
    data: bytes,
    handoff_path: Path,
    *,
    runtime_root: Path,
) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"Chinese message card is not valid UTF-8: {error}") from error
    lines = text.splitlines()
    if len(lines) != 14 or lines[1] or lines[11] or lines[12] != "摘要：":
        raise ProtocolError("Chinese message card does not match the canonical layout")
    title = lines[0]
    message_id = _expect_sha256(
        _parse_card_line(lines[2], "消息编号：", "消息编号"),
        "Chinese message card.消息编号",
    )
    handoff_id = _expect_handoff_id(
        _parse_card_line(lines[3], "交接编号：", "交接编号")
    )
    reply_text = _parse_card_line(lines[4], "回复消息：", "回复消息")
    in_reply_to = None if reply_text == "无" else _expect_sha256(
        reply_text, "Chinese message card.回复消息"
    )
    manifest_path = Path(
        _parse_card_line(lines[5], "监督清单：", "监督清单")
    )
    count_text = _parse_card_line(lines[6], "监督文件数：", "监督文件数")
    if not count_text.isascii() or not count_text.isdigit():
        raise ProtocolError("Chinese message card.监督文件数 must be decimal digits")
    count = _expect_int(int(count_text), "Chinese message card.监督文件数", 2, 100_000)
    checkpoint = _expect_chinese_line(
        _parse_card_line(lines[7], "检查点：", "检查点"),
        "Chinese message card.检查点",
        max_bytes=512,
    )

    def parse_optional(line: str, prefix: str, label: str) -> str | None:
        value = _parse_card_line(line, prefix, label)
        return None if value == "无" else _expect_single_line(
            value, f"Chinese message card.{label}", max_bytes=256
        )

    candidate_commit = parse_optional(lines[8], "候选提交：", "候选提交")
    merge_commit = parse_optional(lines[9], "合并提交：", "合并提交")
    merge_result = parse_optional(lines[10], "合并结果：", "合并结果")
    summary = _expect_chinese_line(
        lines[13], "Chinese message card.摘要", max_bytes=1_024
    )

    handoff = verify_handoff(handoff_path, runtime_root=runtime_root)
    if MESSAGE_CARD_PROTOCOL_MARKER not in handoff["execution_protocol"]:
        raise ProtocolError(
            "Chinese message cards are not permitted for this already-published handoff"
        )
    if handoff_id != handoff["handoff_id"]:
        raise ProtocolError(
            f"message-card handoff ID mismatch; expected={handoff['handoff_id']}; observed={handoff_id}"
        )
    verified = verify_supervision(
        handoff_path,
        manifest_path,
        expected_count=count,
        runtime_root=runtime_root,
    )
    payload_entries = _latest_payload_entries(verified["entries"])
    source = {
        "candidate_commit": candidate_commit,
        "checkpoint": {"说明": checkpoint},
        "handoff_id": handoff_id,
        "manifest_file": verified["manifest_file"],
        "merge_commit": merge_commit,
        "merge_result": merge_result,
        "payload_files": payload_entries,
        "status": MESSAGE_ROUTES.get(
            (payload_entries[0]["direction"], payload_entries[0]["kind"])
        ),
        "supervision_file_count": count,
    }
    if source["status"] is None:
        raise ProtocolError("Chinese message card has an unsupported supervision route")
    normalized = _validate_control_common(
        source,
        includes_version=False,
        runtime_root=runtime_root,
        create_version=CONTROL_VERSION,
    )
    core = _card_core(
        normalized,
        checkpoint=checkpoint,
        summary=summary,
        in_reply_to=in_reply_to,
    )
    expected_message_id = _sha256(_canonical_json_bytes(core))
    if message_id != expected_message_id:
        raise ProtocolError(
            "Chinese message card message_id does not match its verified content"
        )
    expected_title = (
        f"【3实现消息｜{DIRECTION_LABELS[payload_entries[0]['direction']]}｜"
        f"{STATUS_LABELS[normalized['status']]}】"
    )
    if title != expected_title:
        raise ProtocolError(
            f"Chinese message card title mismatch; expected={expected_title!r}; observed={title!r}"
        )
    if data != _render_control_card(core, expected_message_id):
        raise ProtocolError("Chinese message card is not in canonical byte form")

    id_directory = Path(runtime_root) / handoff_id
    root_fd, id_fd = _open_id_directory(Path(runtime_root), handoff_id)
    try:
        payload_parts: list[bytes] = []
        package_hashes: set[str] = set()
        for entry in payload_entries:
            document, payload, file_data, _ = _read_supervision_file(
                id_fd, id_directory, Path(entry["path"]).name
            )
            _require_entry_matches_file(
                entry, document, file_data, "message-card payload entry"
            )
            payload_parts.append(payload)
            package_hashes.add(document["package_sha256"])
    finally:
        os.close(id_fd)
        os.close(root_fd)
    if len(package_hashes) != 1:
        raise ProtocolError("message-card payload package has inconsistent hashes")
    payload = b"".join(payload_parts)
    if _sha256(payload) != next(iter(package_hashes)):
        raise ProtocolError("message-card payload package SHA-256 mismatch")
    try:
        payload_text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(
            f"verified message-card payload is not valid UTF-8: {error}"
        ) from error
    return {
        "candidate_commit": candidate_commit,
        "checkpoint": checkpoint,
        "control_id": expected_message_id,
        "control_version": CARD_CONTROL_VERSION,
        "delivery_check_delay_seconds": DELIVERY_CHECK_DELAY_SECONDS,
        "delivery_check_required": True,
        "delivery_instruction": DELIVERY_INSTRUCTION,
        "delivery_policy": DELIVERY_POLICY,
        "delivery_resend_limit": DELIVERY_RESEND_LIMIT,
        "handoff_id": handoff_id,
        "in_reply_to": in_reply_to,
        "kind": payload_entries[0]["kind"],
        "merge_commit": merge_commit,
        "merge_result": merge_result,
        "message_format": MESSAGE_CARD_FORMAT,
        "message_id": expected_message_id,
        "message_type": MESSAGE_TYPE,
        "payload": payload_text,
        "protocol": MESSAGE_PROTOCOL,
        "status": normalized["status"],
        "summary": summary,
        "supervision_file_count": count,
    }


def create_control(
    input_path: Path, *, runtime_root: Path = RUNTIME_ROOT
) -> bytes:
    data = _read_regular_file(
        Path(input_path), max_bytes=MAX_CONTROL_BYTES, label="control input"
    )
    source = _expect_object(_load_json_bytes(data, "control input"), "control input")
    handoff_id = _expect_handoff_id(source.get("handoff_id"))
    handoff = verify_handoff(
        Path(runtime_root) / handoff_id / HANDOFF_FILENAME,
        runtime_root=runtime_root,
    )
    if MESSAGE_CARD_PROTOCOL_MARKER in handoff["execution_protocol"]:
        return _create_control_card(source, runtime_root=runtime_root)
    create_version = (
        CONTROL_VERSION
        if DELIVERY_OBSERVE_PROTOCOL_MARKER in handoff["execution_protocol"]
        else DUPLEX_ACK_CONTROL_VERSION
    )
    normalized = _validate_control_common(
        source,
        includes_version=False,
        runtime_root=runtime_root,
        create_version=create_version,
    )
    control_data = _canonical_json_bytes(normalized)
    if len(control_data) > MAX_CONTROL_BYTES:
        raise ProtocolError(
            f"control envelope exceeds {MAX_CONTROL_BYTES} UTF-8 bytes; observed={len(control_data)}"
        )
    return control_data


def verify_control(
    input_path: Path,
    handoff_path: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    data = _read_regular_file(
        Path(input_path), max_bytes=MAX_CONTROL_BYTES, label="control envelope"
    )
    if data.startswith("【3实现消息｜".encode("utf-8")):
        return _verify_control_card(
            data, Path(handoff_path), runtime_root=runtime_root
        )
    source = _expect_object(
        _load_json_bytes(data, "control envelope", require_canonical=True),
        "control envelope",
    )
    control = _validate_control_common(
        source, includes_version=True, runtime_root=runtime_root
    )
    handoff = verify_handoff(handoff_path, runtime_root=runtime_root)
    if MESSAGE_CARD_PROTOCOL_MARKER in handoff["execution_protocol"]:
        raise ProtocolError(
            "canonical JSON controls are not permitted for a Chinese message-card handoff"
        )
    if control["handoff_id"] != handoff["handoff_id"]:
        raise ProtocolError(
            f"control handoff ID mismatch; expected={handoff['handoff_id']}; observed={control['handoff_id']}"
        )
    if DELIVERY_OBSERVE_PROTOCOL_MARKER in handoff["execution_protocol"] and control[
        "control_version"
    ] != CONTROL_VERSION:
        raise ProtocolError(
            f"control_version {control['control_version']} is not permitted for an observe-delivery handoff"
        )
    if (
        control["control_version"] == LEGACY_CONTROL_VERSION
        and EXPLICIT_ACK_PROTOCOL_MARKER in handoff["execution_protocol"]
    ):
        raise ProtocolError(
            "legacy control_version 1 is not permitted for a handoff created "
            "under the explicit ACK control protocol"
        )
    if (
        control["control_version"] == EXPLICIT_ACK_CONTROL_VERSION
        and ACK_POLICY_PROTOCOL_MARKER in handoff["execution_protocol"]
    ):
        raise ProtocolError(
            "legacy control_version 2 is not permitted for a handoff created "
            "under the structured ACK policy protocol"
        )
    if (
        control["control_version"] < DUPLEX_ACK_CONTROL_VERSION
        and DUPLEX_ACK_PROTOCOL_MARKER in handoff["execution_protocol"]
    ):
        raise ProtocolError(
            f"legacy control_version {control['control_version']} is not permitted "
            "for a handoff created under the duplex ACK policy protocol"
        )
    manifest_path = Path(control["manifest_file"]["path"])
    verified = verify_supervision(
        handoff_path,
        manifest_path,
        expected_count=control["supervision_file_count"],
        runtime_root=runtime_root,
    )
    if verified["manifest_file"] != control["manifest_file"]:
        raise ProtocolError("control manifest metadata does not match the verified manifest")
    payload_entries = control["payload_files"]
    if verified["entries"][-len(payload_entries) :] != payload_entries:
        raise ProtocolError(
            "control payload metadata must equal the final payload package in the latest manifest"
        )
    direction = payload_entries[0]["direction"]
    sequence = payload_entries[0]["sequence"]
    kind = payload_entries[0]["kind"]
    expected_indices = [
        f"{index:04d}" for index in range(1, len(payload_entries) + 1)
    ]
    if any(
        entry["direction"] != direction
        or entry["sequence"] != sequence
        or entry["kind"] != kind
        for entry in payload_entries
    ):
        raise ProtocolError("control payload files do not form one package")
    if [entry["part_index"] for entry in payload_entries] != expected_indices:
        raise ProtocolError("control payload files are not in contiguous part order")
    id_directory = Path(runtime_root) / handoff["handoff_id"]
    root_fd, id_fd = _open_id_directory(Path(runtime_root), handoff["handoff_id"])
    try:
        payload_parts: list[bytes] = []
        package_hashes: set[str] = set()
        for entry in payload_entries:
            document, payload, file_data, _ = _read_supervision_file(
                id_fd, id_directory, Path(entry["path"]).name
            )
            _require_entry_matches_file(entry, document, file_data, "control payload entry")
            payload_parts.append(payload)
            package_hashes.add(document["package_sha256"])
    finally:
        os.close(id_fd)
        os.close(root_fd)
    if len(package_hashes) != 1:
        raise ProtocolError("control payload package contains inconsistent package hashes")
    payload = b"".join(payload_parts)
    if _sha256(payload) != next(iter(package_hashes)):
        raise ProtocolError("control payload package SHA-256 mismatch")
    try:
        payload_text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError(f"verified control payload is not valid UTF-8: {error}") from error
    control_identity = (
        control["message_id"]
        if control["control_version"] in (DUPLEX_ACK_CONTROL_VERSION, CONTROL_VERSION)
        else _sha256(data)
    )
    result = {
        "candidate_commit": control["candidate_commit"],
        "checkpoint": control["checkpoint"],
        "control_version": control["control_version"],
        "control_id": control_identity,
        "handoff_id": control["handoff_id"],
        "kind": kind,
        "merge_commit": control["merge_commit"],
        "merge_result": control["merge_result"],
        "payload": payload_text,
        "status": control["status"],
        "supervision_file_count": control["supervision_file_count"],
    }
    if control["control_version"] == DUPLEX_ACK_CONTROL_VERSION:
        result.update(
            {
                "ack_format": control["ack_format"],
                "ack_resend_limit": control["ack_resend_limit"],
                "ack_text": control["ack_text"],
                "message_id": control["message_id"],
                "message_type": control["message_type"],
                "protocol": control["protocol"],
            }
        )
    elif control["control_version"] == CONTROL_VERSION:
        result.update(
            {
                "delivery_check_delay_seconds": control["delivery_check_delay_seconds"],
                "delivery_check_required": control["delivery_check_required"],
                "delivery_instruction": control["delivery_instruction"],
                "delivery_policy": control["delivery_policy"],
                "delivery_resend_limit": control["delivery_resend_limit"],
                "message_id": control["message_id"],
                "message_type": control["message_type"],
                "protocol": control["protocol"],
            }
        )
    return result


def create_ack(
    control_path: Path,
    handoff_path: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> bytes:
    control = verify_control(
        Path(control_path), Path(handoff_path), runtime_root=runtime_root
    )
    if control["control_version"] in (CONTROL_VERSION, CARD_CONTROL_VERSION):
        raise ProtocolError("observe-delivery controls do not use ACK")
    if control["control_version"] == DUPLEX_ACK_CONTROL_VERSION:
        ack_data = control["ack_text"].encode("ascii")
        if len(ack_data) > MAX_ACK_BYTES:
            raise ProtocolError(
                f"ACK text exceeds {MAX_ACK_BYTES} UTF-8 bytes; observed={len(ack_data)}"
            )
        return ack_data
    ack = {
        "ack_version": ACK_VERSION,
        "control_id": control["control_id"],
        "handoff_id": control["handoff_id"],
        "status": control["status"],
        "type": ACK_TYPE,
    }
    ack_data = _canonical_json_bytes(ack)
    if len(ack_data) > MAX_ACK_BYTES:
        raise ProtocolError(
            f"ACK envelope exceeds {MAX_ACK_BYTES} UTF-8 bytes; observed={len(ack_data)}"
        )
    return ack_data


def verify_ack(
    input_path: Path,
    control_path: Path,
    handoff_path: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    control = verify_control(
        Path(control_path), Path(handoff_path), runtime_root=runtime_root
    )
    data = _read_regular_file(
        Path(input_path), max_bytes=MAX_ACK_BYTES, label="ACK envelope"
    )
    if control["control_version"] in (CONTROL_VERSION, CARD_CONTROL_VERSION):
        raise ProtocolError("observe-delivery controls do not use ACK")
    if control["control_version"] == DUPLEX_ACK_CONTROL_VERSION:
        expected_data = control["ack_text"].encode("ascii")
        if data != expected_data:
            raise ProtocolError(
                "ACK text does not match the exact verified message; "
                f"expected={control['ack_text']!r}; observed={data!r}"
            )
        return {
            "ack_text": control["ack_text"],
            "message_id": control["message_id"],
            "type": ACK_TYPE,
        }
    source = _expect_object(
        _load_json_bytes(data, "ACK envelope", require_canonical=True),
        "ACK envelope",
    )
    _expect_keys(
        source,
        {"ack_version", "control_id", "handoff_id", "status", "type"},
        "ACK envelope",
    )
    if source["ack_version"] != ACK_VERSION:
        raise ProtocolError(
            f"ack_version mismatch; expected={ACK_VERSION}; observed={source['ack_version']!r}"
        )
    if source["type"] != ACK_TYPE:
        raise ProtocolError(
            f"ACK type mismatch; expected={ACK_TYPE!r}; observed={source['type']!r}"
        )
    control_id = _expect_sha256(source["control_id"], "ACK envelope.control_id")
    handoff_id = _expect_handoff_id(source["handoff_id"])
    status = _expect_string(source["status"], "ACK envelope.status")
    if status not in CONTROL_STATUSES:
        raise ProtocolError(f"unsupported ACK control status: {status!r}")

    expected = {
        "ack_version": ACK_VERSION,
        "control_id": control["control_id"],
        "handoff_id": control["handoff_id"],
        "status": control["status"],
        "type": ACK_TYPE,
    }
    observed = {
        "ack_version": ACK_VERSION,
        "control_id": control_id,
        "handoff_id": handoff_id,
        "status": status,
        "type": ACK_TYPE,
    }
    if observed != expected:
        raise ProtocolError(
            f"ACK does not match the exact verified control; expected={expected!r}; observed={observed!r}"
        )
    return observed


def _validate_handoff_checkpoint(
    value: Any, *, handoff_id: str, runtime_root: Path
) -> dict[str, Any]:
    checkpoint = _expect_object(value, "cleanup checkpoint.handoff_file")
    _expect_keys(
        checkpoint,
        {"complete", "file_bytes", "file_sha256", "path"},
        "cleanup checkpoint.handoff_file",
    )
    expected_path = Path(runtime_root) / handoff_id / HANDOFF_FILENAME
    path = Path(_expect_string(checkpoint["path"], "cleanup checkpoint.handoff_file.path"))
    if not path.is_absolute() or path != expected_path:
        raise ProtocolError(
            f"cleanup handoff path mismatch; expected={expected_path}; observed={path}"
        )
    file_bytes = _expect_int(
        checkpoint["file_bytes"],
        "cleanup checkpoint.handoff_file.file_bytes",
        1,
        MAX_HANDOFF_BYTES,
    )
    file_sha = _expect_sha256(
        checkpoint["file_sha256"], "cleanup checkpoint.handoff_file.file_sha256"
    )
    complete = f"HANDOFF_COMPLETE:{handoff_id}"
    if checkpoint["complete"] != complete:
        raise ProtocolError(
            f"cleanup handoff completeness mismatch; expected={complete!r}; observed={checkpoint['complete']!r}"
        )
    return {
        "complete": complete,
        "file_bytes": file_bytes,
        "file_sha256": file_sha,
        "path": str(path),
    }


def _load_cleanup_checkpoint(
    checkpoint_path: Path, *, runtime_root: Path
) -> dict[str, Any]:
    data = _read_regular_file(
        Path(checkpoint_path), max_bytes=MAX_CONTROL_BYTES, label="cleanup checkpoint"
    )
    source = _expect_object(
        _load_json_bytes(data, "cleanup checkpoint"), "cleanup checkpoint"
    )
    _expect_keys(
        source,
        {
            "cleanup_version",
            "handoff_file",
            "handoff_id",
            "manifest_file",
            "supervision_file_count",
        },
        "cleanup checkpoint",
    )
    if source["cleanup_version"] != CLEANUP_VERSION:
        raise ProtocolError(
            f"cleanup_version mismatch; expected={CLEANUP_VERSION}; observed={source['cleanup_version']!r}"
        )
    handoff_id = _expect_handoff_id(source["handoff_id"])
    handoff_file = _validate_handoff_checkpoint(
        source["handoff_file"], handoff_id=handoff_id, runtime_root=runtime_root
    )
    id_directory = Path(runtime_root) / handoff_id
    manifest_file = _validate_manifest_entry(
        source["manifest_file"],
        handoff_id=handoff_id,
        id_directory=id_directory,
        label="cleanup checkpoint.manifest_file",
    )
    if manifest_file["kind"] != "manifest":
        raise ProtocolError("cleanup checkpoint.manifest_file must have kind='manifest'")
    count = _expect_int(
        source["supervision_file_count"],
        "cleanup checkpoint.supervision_file_count",
        1,
        100_000,
    )
    return {
        "cleanup_version": CLEANUP_VERSION,
        "handoff_file": handoff_file,
        "handoff_id": handoff_id,
        "manifest_file": manifest_file,
        "supervision_file_count": count,
    }


def _open_optional_id_directory(
    runtime_root: Path, handoff_id: str
) -> tuple[int | None, int | None]:
    try:
        os.lstat(runtime_root)
    except FileNotFoundError:
        return None, None
    except OSError as error:
        raise ProtocolError(f"cannot inspect runtime root {runtime_root}: {error}") from error
    root_fd = _open_directory(runtime_root)
    id_path = runtime_root / handoff_id
    try:
        id_stat = os.stat(handoff_id, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return root_fd, None
    except OSError as error:
        os.close(root_fd)
        raise ProtocolError(f"cannot inspect handoff directory {id_path}: {error}") from error
    try:
        _validate_directory_stat(id_stat, id_path, required_mode=0o700)
        id_fd = os.open(
            handoff_id,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        opened = os.fstat(id_fd)
        if (opened.st_dev, opened.st_ino) != (id_stat.st_dev, id_stat.st_ino):
            os.close(id_fd)
            raise ProtocolError(f"handoff directory changed while opening: {id_path}")
        _validate_directory_stat(opened, id_path, required_mode=0o700)
        return root_fd, id_fd
    except Exception:
        os.close(root_fd)
        raise


def _inspect_cleanup_internal(
    checkpoint_path: Path, *, runtime_root: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    checkpoint = _load_cleanup_checkpoint(checkpoint_path, runtime_root=runtime_root)
    handoff_id = checkpoint["handoff_id"]
    id_directory = Path(runtime_root) / handoff_id
    root_fd, id_fd = _open_optional_id_directory(Path(runtime_root), handoff_id)
    if root_fd is None:
        return ({"state": "complete", "removed_prefix_count": None, "entry_count": None}, checkpoint, [])
    if id_fd is None:
        os.close(root_fd)
        return ({"state": "complete", "removed_prefix_count": None, "entry_count": None}, checkpoint, [])
    try:
        names = set(os.listdir(id_fd))
        if not names:
            return (
                {"state": "handoff-cleared", "removed_prefix_count": None, "entry_count": None},
                checkpoint,
                [],
            )
        handoff_present = HANDOFF_FILENAME in names
        manifest_name = Path(checkpoint["manifest_file"]["path"]).name
        manifest_present = manifest_name in names
        if handoff_present:
            handoff_data, _ = _read_runtime_file(
                id_fd,
                HANDOFF_FILENAME,
                max_bytes=MAX_HANDOFF_BYTES,
                label="handoff",
            )
            expected_handoff = checkpoint["handoff_file"]
            if len(handoff_data) != expected_handoff["file_bytes"] or _sha256(handoff_data) != expected_handoff["file_sha256"]:
                raise ProtocolError("cleanup handoff file does not match the recorded checkpoint")
            verify_handoff(
                Path(expected_handoff["path"]),
                expected_id=handoff_id,
                expected_bytes=expected_handoff["file_bytes"],
                expected_sha256=expected_handoff["file_sha256"],
                runtime_root=runtime_root,
            )
        if not manifest_present:
            if names == {HANDOFF_FILENAME}:
                return (
                    {"state": "supervision-cleared", "removed_prefix_count": None, "entry_count": None},
                    checkpoint,
                    [],
                )
            raise ProtocolError(
                f"cleanup state is invalid without the latest manifest; entries={sorted(names)!r}"
            )
        if not handoff_present:
            raise ProtocolError("latest manifest remains but handoff.json is absent")

        manifest_document, manifest_payload, manifest_data, _ = _read_supervision_file(
            id_fd, id_directory, manifest_name
        )
        if manifest_document["kind"] != "manifest":
            raise ProtocolError("recorded cleanup manifest file has non-manifest kind")
        observed_manifest_entry = _supervision_entry(
            id_directory / manifest_name, manifest_document, manifest_data
        )
        if observed_manifest_entry != checkpoint["manifest_file"]:
            raise ProtocolError("cleanup manifest file does not match the recorded checkpoint")
        manifest = _decode_manifest_payload(
            manifest_payload,
            handoff_id=handoff_id,
            id_directory=id_directory,
            label=f"decoded cleanup manifest {id_directory / manifest_name}",
        )
        entries = manifest["entries"]
        if checkpoint["supervision_file_count"] != len(entries) + 1:
            raise ProtocolError(
                "cleanup supervision_file_count does not match the recorded manifest"
            )
        allowed_names = {HANDOFF_FILENAME, manifest_name}
        allowed_names.update(Path(entry["path"]).name for entry in entries)
        unexpected = names - allowed_names
        if unexpected:
            raise ProtocolError(
                f"cleanup directory contains unexpected entries: {sorted(unexpected)!r}"
            )
        removed_prefix = 0
        found_present = False
        present_objects: dict[str, tuple[dict[str, Any], bytes]] = {}
        for index, entry in enumerate(entries):
            name = Path(entry["path"]).name
            if name not in names:
                if found_present:
                    raise ProtocolError(
                        f"cleanup missing files are not one contiguous manifest prefix; gap at index={index} path={entry['path']}"
                    )
                removed_prefix += 1
                continue
            found_present = True
            document, payload, file_data, _ = _read_supervision_file(
                id_fd, id_directory, name
            )
            _require_entry_matches_file(
                entry, document, file_data, f"cleanup manifest entry {index}"
            )
            present_objects[entry["path"]] = (document, payload)
            if entry["kind"] == "manifest":
                prior = _decode_manifest_payload(
                    payload,
                    handoff_id=handoff_id,
                    id_directory=id_directory,
                    label=f"decoded remaining prior manifest {entry['path']}",
                )
                if prior["entries"] != entries[:index]:
                    raise ProtocolError(
                        f"remaining prior manifest does not equal cumulative prefix: {entry['path']}"
                    )
        if removed_prefix == 0:
            current_entry = checkpoint["manifest_file"]
            complete_objects = dict(present_objects)
            complete_objects[current_entry["path"]] = (
                manifest_document,
                manifest_payload,
            )
            _validate_package_groups(entries + [current_entry], complete_objects)
            state = "intact"
        else:
            state = f"partial-supervision:{removed_prefix}/{len(entries)}"
        return (
            {
                "state": state,
                "removed_prefix_count": removed_prefix,
                "entry_count": len(entries),
            },
            checkpoint,
            entries,
        )
    finally:
        os.close(id_fd)
        os.close(root_fd)


def inspect_cleanup(
    checkpoint_path: Path, *, runtime_root: Path = RUNTIME_ROOT
) -> dict[str, Any]:
    report, _, _ = _inspect_cleanup_internal(
        checkpoint_path, runtime_root=runtime_root
    )
    return report


def _unlink_verified_file(
    id_fd: int,
    *,
    name: str,
    expected_bytes: int,
    expected_sha256: str,
    max_bytes: int,
    label: str,
) -> None:
    data, opened = _read_runtime_file(
        id_fd, name, max_bytes=max_bytes, label=label
    )
    if len(data) != expected_bytes or _sha256(data) != expected_sha256:
        raise ProtocolError(f"{label} changed before unlink: {name!r}")
    try:
        current = os.stat(name, dir_fd=id_fd, follow_symlinks=False)
    except OSError as error:
        raise ProtocolError(f"cannot restat {label} before unlink {name!r}: {error}") from error
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise ProtocolError(f"{label} inode changed before unlink: {name!r}")
    try:
        os.unlink(name, dir_fd=id_fd)
        os.fsync(id_fd)
    except OSError as error:
        raise ProtocolError(f"cannot unlink exact {label} {name!r}: {error}") from error


def advance_cleanup(
    checkpoint_path: Path, *, runtime_root: Path = RUNTIME_ROOT
) -> dict[str, Any]:
    report, checkpoint, entries = _inspect_cleanup_internal(
        checkpoint_path, runtime_root=runtime_root
    )
    state = report["state"]
    if state == "complete":
        return report
    handoff_id = checkpoint["handoff_id"]
    root_fd, id_fd = _open_id_directory(Path(runtime_root), handoff_id)
    try:
        if state == "intact" or state.startswith("partial-supervision:"):
            removed = report["removed_prefix_count"]
            if removed < len(entries):
                entry = entries[removed]
                _unlink_verified_file(
                    id_fd,
                    name=Path(entry["path"]).name,
                    expected_bytes=entry["file_bytes"],
                    expected_sha256=entry["file_sha256"],
                    max_bytes=MAX_SUPERVISION_FILE_BYTES,
                    label="supervision file",
                )
            else:
                manifest = checkpoint["manifest_file"]
                _unlink_verified_file(
                    id_fd,
                    name=Path(manifest["path"]).name,
                    expected_bytes=manifest["file_bytes"],
                    expected_sha256=manifest["file_sha256"],
                    max_bytes=MAX_SUPERVISION_FILE_BYTES,
                    label="latest manifest",
                )
        elif state == "supervision-cleared":
            handoff = checkpoint["handoff_file"]
            _unlink_verified_file(
                id_fd,
                name=HANDOFF_FILENAME,
                expected_bytes=handoff["file_bytes"],
                expected_sha256=handoff["file_sha256"],
                max_bytes=MAX_HANDOFF_BYTES,
                label="handoff",
            )
        elif state == "handoff-cleared":
            if os.listdir(id_fd):
                raise ProtocolError("handoff-cleared directory is no longer empty")
            opened = os.fstat(id_fd)
            current = os.stat(handoff_id, dir_fd=root_fd, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ProtocolError("handoff directory inode changed before rmdir")
            os.close(id_fd)
            id_fd = -1
            try:
                os.rmdir(handoff_id, dir_fd=root_fd)
                os.fsync(root_fd)
            except OSError as error:
                raise ProtocolError(
                    f"cannot remove exact empty handoff directory {handoff_id}: {error}"
                ) from error
        else:
            raise ProtocolError(f"unsupported cleanup state: {state}")
    finally:
        if id_fd >= 0:
            os.close(id_fd)
        os.close(root_fd)
    return inspect_cleanup(checkpoint_path, runtime_root=runtime_root)


def _expect_absolute_path(value: Any, label: str) -> Path:
    if isinstance(value, os.PathLike):
        path = Path(value)
    else:
        path = Path(_expect_nonempty_string(value, label, max_bytes=8_192))
    if not path.is_absolute():
        raise ProtocolError(f"{label} must be absolute: {path}")
    return path


def _validate_managed_links(value: Any, label: str) -> list[dict[str, str]]:
    entries = _expect_list(value, label)
    if len(entries) > 100:
        raise ProtocolError(f"{label} exceeds 100 entries; observed={len(entries)}")
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(entries):
        entry_label = f"{label}[{index}]"
        entry = _expect_object(item, entry_label)
        _expect_keys(entry, {"path", "target"}, entry_label)
        path = _expect_nonempty_string(
            entry["path"], f"{entry_label}.path", max_bytes=8_192
        )
        target = _expect_absolute_path(entry["target"], f"{entry_label}.target")
        normalized.append({"path": path, "target": str(target)})
    return normalized


def _validate_lease_metadata(value: Any, label: str) -> dict[str, Any]:
    metadata = _expect_object(value, label)
    _expect_keys(
        metadata,
        {"complete", "file_bytes", "file_sha256", "lease_id", "path"},
        label,
    )
    lease_id = _expect_handoff_id(metadata["lease_id"], f"{label}.lease_id")
    complete = f"REPOSITORY_LEASE_COMPLETE:{lease_id}"
    if metadata["complete"] != complete:
        raise ProtocolError(
            f"{label}.complete mismatch; expected={complete!r}; "
            f"observed={metadata['complete']!r}"
        )
    return {
        "complete": complete,
        "file_bytes": _expect_int(
            metadata["file_bytes"], f"{label}.file_bytes", 1, MAX_LEASE_BYTES
        ),
        "file_sha256": _expect_sha256(
            metadata["file_sha256"], f"{label}.file_sha256"
        ),
        "lease_id": lease_id,
        "path": str(_expect_absolute_path(metadata["path"], f"{label}.path")),
    }


def _closure_phases(closure_version: int) -> tuple[str, ...]:
    if closure_version == LEGACY_CLOSURE_VERSION:
        return LEGACY_CLOSURE_PHASES
    if closure_version == CLOSURE_VERSION:
        return CLOSURE_PHASES
    if closure_version == ISOLATED_CLOSURE_VERSION:
        return ISOLATED_CLOSURE_PHASES
    raise ProtocolError(f"unsupported closure_version: {closure_version!r}")


def _validate_closure_facts(
    value: Any, label: str, *, closure_version: int
) -> dict[str, Any]:
    facts = _expect_object(value, label)
    if closure_version == LEGACY_CLOSURE_VERSION:
        _expect_keys(
            facts,
            {
                "base_branch",
                "implementation_branch",
                "implementation_commit",
                "managed_links",
                "merge_commit",
                "remote_actions",
                "repository",
                "source_host_id",
                "source_task_id",
                "spec_references",
                "ticket_references",
                "worktree_path",
            },
            label,
        )
    elif closure_version == CLOSURE_VERSION:
        _expect_keys(
            facts,
            {
                "base_branch",
                "checkout_path",
                "execution_mode",
                "implementation_branch",
                "implementation_commit",
                "managed_links",
                "merge_commit",
                "remote_actions",
                "repository",
                "repository_lease",
                "source_host_id",
                "source_task_id",
                "spec_references",
                "ticket_references",
            },
            label,
        )
    elif closure_version == ISOLATED_CLOSURE_VERSION:
        _expect_keys(
            facts,
            {
                "base_branch",
                "checkout_path",
                "documentation_proposals",
                "execution_lease",
                "execution_mode",
                "implementation_branch",
                "implementation_commit",
                "managed_links",
                "merge_commit",
                "remote_actions",
                "repository",
                "source_host_id",
                "source_task_id",
                "spec_references",
                "ticket_references",
                "worktree_path",
            },
            label,
        )
    else:
        raise ProtocolError(f"unsupported closure_version: {closure_version!r}")

    repository = str(
        _expect_absolute_path(facts["repository"], f"{label}.repository")
    )
    normalized = {
        "base_branch": _expect_nonempty_string(
            facts["base_branch"], f"{label}.base_branch", max_bytes=1_024
        ),
        "implementation_branch": _expect_nonempty_string(
            facts["implementation_branch"],
            f"{label}.implementation_branch",
            max_bytes=1_024,
        ),
        "implementation_commit": _expect_nonempty_string(
            facts["implementation_commit"],
            f"{label}.implementation_commit",
            max_bytes=128,
        ),
        "managed_links": _validate_managed_links(
            facts["managed_links"], f"{label}.managed_links"
        ),
        "merge_commit": _expect_nonempty_string(
            facts["merge_commit"], f"{label}.merge_commit", max_bytes=128
        ),
        "remote_actions": _expect_string_list(
            facts["remote_actions"], f"{label}.remote_actions"
        ),
        "repository": repository,
        "source_host_id": _expect_nonempty_string(
            facts["source_host_id"], f"{label}.source_host_id", max_bytes=256
        ),
        "source_task_id": _expect_nonempty_string(
            facts["source_task_id"], f"{label}.source_task_id", max_bytes=256
        ),
        "spec_references": _expect_string_list(
            facts["spec_references"], f"{label}.spec_references"
        ),
        "ticket_references": _expect_string_list(
            facts["ticket_references"], f"{label}.ticket_references"
        ),
    }
    if closure_version == LEGACY_CLOSURE_VERSION:
        normalized["worktree_path"] = str(
            _expect_absolute_path(facts["worktree_path"], f"{label}.worktree_path")
        )
        return normalized

    if closure_version == ISOLATED_CLOSURE_VERSION:
        if facts["execution_mode"] != "isolated-worktree-v1":
            raise ProtocolError(f"{label}.execution_mode must be 'isolated-worktree-v1'")
        checkout_path = str(
            _expect_absolute_path(facts["checkout_path"], f"{label}.checkout_path")
        )
        if checkout_path != repository:
            raise ProtocolError(f"{label}.checkout_path must equal repository")
        worktree_path = str(
            _expect_absolute_path(facts["worktree_path"], f"{label}.worktree_path")
        )
        if worktree_path == repository:
            raise ProtocolError(f"{label}.worktree_path must differ from repository")
        lease = _expect_object(facts["execution_lease"], f"{label}.execution_lease")
        _expect_keys(lease, {"lease_id", "path", "version"}, f"{label}.execution_lease")
        lease_path = str(
            _expect_absolute_path(lease["path"], f"{label}.execution_lease.path")
        )
        expected_lease_directory = Path(repository) / ".git" / WORKTREE_EXECUTION_LEASE_DIRECTORY
        if Path(lease_path).parent != expected_lease_directory:
            raise ProtocolError(
                f"{label}.execution_lease.path must be directly inside {expected_lease_directory}"
            )
        normalized.update(
            {
                "checkout_path": checkout_path,
                "documentation_proposals": _expect_string_list(
                    facts["documentation_proposals"], f"{label}.documentation_proposals"
                ),
                "execution_lease": {
                    "lease_id": _expect_handoff_id(
                        lease["lease_id"], f"{label}.execution_lease.lease_id"
                    ),
                    "path": lease_path,
                    "version": _expect_int(
                        lease["version"], f"{label}.execution_lease.version", 1, 2**63 - 2
                    ),
                },
                "execution_mode": "isolated-worktree-v1",
                "worktree_path": worktree_path,
            }
        )
        return normalized

    execution_mode = _expect_nonempty_string(
        facts["execution_mode"], f"{label}.execution_mode", max_bytes=128
    )
    if execution_mode not in {LEGACY_LEASE_MODE, LEASE_MODE}:
        raise ProtocolError(
            f"{label}.execution_mode mismatch; expected one of "
            f"{sorted({LEGACY_LEASE_MODE, LEASE_MODE})!r}; "
            f"observed={execution_mode!r}"
        )
    checkout_path = str(
        _expect_absolute_path(facts["checkout_path"], f"{label}.checkout_path")
    )
    if checkout_path != repository:
        raise ProtocolError(
            f"{label}.checkout_path must equal repository in zero-worktree mode; "
            f"repository={repository}; checkout_path={checkout_path}"
        )
    repository_lease = _validate_lease_metadata(
        facts["repository_lease"], f"{label}.repository_lease"
    )
    expected_lease_path = str(_lease_path(Path(repository)))
    if repository_lease["path"] != expected_lease_path:
        raise ProtocolError(
            f"{label}.repository_lease.path mismatch; expected={expected_lease_path}; "
            f"observed={repository_lease['path']}"
        )
    normalized.update(
        {
            "checkout_path": checkout_path,
            "execution_mode": execution_mode,
            "repository_lease": repository_lease,
        }
    )
    return normalized


def _closure_checkpoint_path(handoff_id: str, closure_root: Path) -> Path:
    return Path(closure_root) / f"{_expect_handoff_id(handoff_id)}.json"


def _validate_closure_receipt(
    value: Any, *, index: int, expected_phase: str
) -> dict[str, Any]:
    label = f"closure checkpoint.receipts[{index}]"
    receipt = _expect_object(value, label)
    _expect_keys(receipt, {"phase", "result"}, label)
    phase = _expect_string(receipt["phase"], f"{label}.phase")
    if phase != expected_phase:
        raise ProtocolError(
            f"{label}.phase mismatch; expected={expected_phase!r}; observed={phase!r}"
        )
    result = _expect_object(receipt["result"], f"{label}.result")
    return {"phase": phase, "result": result}


def _load_closure_checkpoint(
    checkpoint_path: Path, *, runtime_root: Path, closure_root: Path
) -> tuple[dict[str, Any], bytes]:
    checkpoint_path = Path(checkpoint_path)
    closure_root = Path(closure_root)
    if not checkpoint_path.is_absolute() or checkpoint_path.parent != closure_root:
        raise ProtocolError(
            f"closure checkpoint must be directly inside {closure_root}: {checkpoint_path}"
        )
    root_fd = _open_directory(closure_root)
    try:
        data, _ = _read_runtime_file(
            root_fd,
            checkpoint_path.name,
            max_bytes=MAX_CLOSURE_CHECKPOINT_BYTES,
            label="closure checkpoint",
        )
    finally:
        os.close(root_fd)
    source = _expect_object(
        _load_json_bytes(data, "closure checkpoint", require_canonical=True),
        "closure checkpoint",
    )
    _expect_keys(
        source,
        {
            "cleanup_checkpoint",
            "closure_version",
            "facts",
            "handoff_id",
            "phase",
            "receipts",
        },
        "closure checkpoint",
    )
    closure_version = _expect_int(
        source["closure_version"],
        "closure checkpoint.closure_version",
        LEGACY_CLOSURE_VERSION,
        ISOLATED_CLOSURE_VERSION,
    )
    phases = _closure_phases(closure_version)
    handoff_id = _expect_handoff_id(
        source["handoff_id"], "closure checkpoint.handoff_id"
    )
    expected_path = _closure_checkpoint_path(handoff_id, closure_root)
    if checkpoint_path != expected_path:
        raise ProtocolError(
            f"closure checkpoint path mismatch; expected={expected_path}; observed={checkpoint_path}"
        )
    cleanup_checkpoint = _expect_absolute_path(
        source["cleanup_checkpoint"], "closure checkpoint.cleanup_checkpoint"
    )
    facts = _validate_closure_facts(
        source["facts"],
        "closure checkpoint.facts",
        closure_version=closure_version,
    )
    repository = Path(facts["repository"])
    if _path_is_within(cleanup_checkpoint, repository):
        raise ProtocolError(
            f"cleanup checkpoint must be outside repository {repository}: {cleanup_checkpoint}"
        )
    if _path_is_within(cleanup_checkpoint, Path(runtime_root)):
        raise ProtocolError(
            f"cleanup checkpoint must be outside fixed runtime root {runtime_root}: {cleanup_checkpoint}"
        )
    phase = _expect_string(source["phase"], "closure checkpoint.phase")
    if phase not in phases:
        raise ProtocolError(f"unsupported closure checkpoint phase: {phase!r}")
    receipts_value = _expect_list(source["receipts"], "closure checkpoint.receipts")
    expected_count = phases.index(phase) + 1
    if len(receipts_value) != expected_count:
        raise ProtocolError(
            f"closure receipt count mismatch for phase {phase!r}; expected={expected_count}; observed={len(receipts_value)}"
        )
    receipts = [
        _validate_closure_receipt(
            item, index=index, expected_phase=phases[index]
        )
        for index, item in enumerate(receipts_value)
    ]
    initial_result = receipts[0]["result"]
    if initial_result != {"cleanup_state": "intact"}:
        raise ProtocolError(
            "prepared closure receipt must record cleanup_state='intact'"
        )
    for receipt in receipts[1:]:
        receipt_phase = receipt["phase"]
        receipt_result = receipt["result"]
        if receipt_phase in {
            "documents-committed",
            "worktree-removed",
            "branch-removed",
            "lease-released",
            "execution-lease-released",
            "remote-verified",
        }:
            normalized_result = _validate_closure_phase_result(
                receipt_phase,
                receipt_result,
                facts,
                closure_version=closure_version,
            )
        elif receipt_phase == "evidence-cleanup":
            normalized_result = {"cleanup_state": "intact"}
        elif receipt_phase == "complete":
            normalized_result = {"cleanup_state": "complete"}
        else:
            raise ProtocolError(
                f"unsupported closure receipt phase: {receipt_phase!r}"
            )
        if receipt_result != normalized_result:
            raise ProtocolError(
                f"closure receipt result is not normalized for phase {receipt_phase!r}"
            )
    return (
        {
            "cleanup_checkpoint": str(cleanup_checkpoint),
            "closure_version": closure_version,
            "facts": facts,
            "handoff_id": handoff_id,
            "phase": phase,
            "receipts": receipts,
        },
        data,
    )


def create_closure_checkpoint(
    input_path: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
    closure_root: Path = CLOSURE_ROOT,
) -> dict[str, Any]:
    data = _read_regular_file(
        Path(input_path),
        max_bytes=MAX_CLOSURE_CHECKPOINT_BYTES,
        label="closure checkpoint input",
    )
    source = _expect_object(
        _load_json_bytes(data, "closure checkpoint input"),
        "closure checkpoint input",
    )
    _expect_keys(source, {"cleanup_checkpoint", "facts"}, "closure checkpoint input")
    cleanup_checkpoint = _expect_absolute_path(
        source["cleanup_checkpoint"], "closure checkpoint input.cleanup_checkpoint"
    )
    raw_facts = _expect_object(source["facts"], "closure checkpoint input.facts")
    closure_version = (
        ISOLATED_CLOSURE_VERSION
        if raw_facts.get("execution_mode") == "isolated-worktree-v1"
        else CLOSURE_VERSION
    )
    facts = _validate_closure_facts(
        source["facts"],
        "closure checkpoint input.facts",
        closure_version=closure_version,
    )
    repository = Path(facts["repository"])
    if _path_is_within(cleanup_checkpoint, repository):
        raise ProtocolError(
            f"cleanup checkpoint must be outside repository {repository}: {cleanup_checkpoint}"
        )
    if _path_is_within(cleanup_checkpoint, Path(runtime_root)):
        raise ProtocolError(
            f"cleanup checkpoint must be outside fixed runtime root {runtime_root}: {cleanup_checkpoint}"
        )
    cleanup = _load_cleanup_checkpoint(
        cleanup_checkpoint, runtime_root=Path(runtime_root)
    )
    cleanup_report = inspect_cleanup(
        cleanup_checkpoint, runtime_root=Path(runtime_root)
    )
    if cleanup_report["state"] != "intact":
        raise ProtocolError(
            f"new closure checkpoint requires intact cleanup evidence; observed={cleanup_report['state']!r}"
        )
    handoff_id = cleanup["handoff_id"]
    document = {
        "cleanup_checkpoint": str(cleanup_checkpoint),
        "closure_version": closure_version,
        "facts": facts,
        "handoff_id": handoff_id,
        "phase": "prepared",
        "receipts": [
            {"phase": "prepared", "result": {"cleanup_state": "intact"}}
        ],
    }
    checkpoint_data = _canonical_json_bytes(document)
    if len(checkpoint_data) > MAX_CLOSURE_CHECKPOINT_BYTES:
        raise ProtocolError(
            f"closure checkpoint exceeds {MAX_CLOSURE_CHECKPOINT_BYTES} UTF-8 bytes; observed={len(checkpoint_data)}"
        )
    root_fd = _ensure_runtime_root(Path(closure_root))
    try:
        path = _closure_checkpoint_path(handoff_id, Path(closure_root))
        _publish_group(root_fd, [(path.name, checkpoint_data)])
    finally:
        os.close(root_fd)
    return inspect_closure_checkpoint(
        path, runtime_root=runtime_root, closure_root=closure_root
    )


def _validate_closure_phase_result(
    phase: str,
    value: Any,
    facts: dict[str, Any],
    *,
    closure_version: int,
) -> dict[str, Any]:
    label = f"closure phase result for {phase}"
    result = _expect_object(value, label)
    if phase == "documents-committed":
        uses_document_lease = (
            closure_version == ISOLATED_CLOSURE_VERSION
            or (
                closure_version == CLOSURE_VERSION
                and facts.get("execution_mode") == LEASE_MODE
            )
        )
        expected_keys = {"closure_commit", "documents_updated", "verification"}
        if uses_document_lease:
            expected_keys.update({"document_lease", "preserved_documents"})
        if closure_version == ISOLATED_CLOSURE_VERSION:
            expected_keys.add("proposal_outcomes")
        _expect_keys(
            result,
            expected_keys,
            label,
        )
        closure_commit = result["closure_commit"]
        if closure_commit is not None:
            closure_commit = _expect_nonempty_string(
                closure_commit, f"{label}.closure_commit", max_bytes=128
            )
        normalized = {
            "closure_commit": closure_commit,
            "documents_updated": _expect_string_list(
                result["documents_updated"], f"{label}.documents_updated"
            ),
            "verification": _expect_string_list(
                result["verification"], f"{label}.verification"
            ),
        }
        if uses_document_lease:
            document_lease = _expect_object(
                result["document_lease"], f"{label}.document_lease"
            )
            _expect_keys(
                document_lease,
                {"lease_id", "path", "state", "version"},
                f"{label}.document_lease",
            )
            if document_lease["state"] != "available":
                raise ProtocolError(
                    f"{label}.document_lease.state must be 'available'"
                )
            expected_document_path = str(
                _document_lease_path(Path(facts["repository"]))
            )
            document_path = str(
                _expect_absolute_path(
                    document_lease["path"], f"{label}.document_lease.path"
                )
            )
            if document_path != expected_document_path:
                raise ProtocolError(
                    f"{label}.document_lease.path mismatch; "
                    f"expected={expected_document_path}; observed={document_path}"
                )
            normalized.update(
                {
                    "document_lease": {
                        "lease_id": _expect_handoff_id(
                            document_lease["lease_id"],
                            f"{label}.document_lease.lease_id",
                        ),
                        "path": document_path,
                        "state": "available",
                        "version": _expect_int(
                            document_lease["version"],
                            f"{label}.document_lease.version",
                            1,
                            2**63 - 2,
                        ),
                    },
                    "preserved_documents": _expect_string_list(
                        result["preserved_documents"],
                        f"{label}.preserved_documents",
                    ),
                }
            )
        if closure_version == ISOLATED_CLOSURE_VERSION:
            outcomes = _expect_list(result["proposal_outcomes"], f"{label}.proposal_outcomes")
            normalized_outcomes = []
            for index, item in enumerate(outcomes):
                item_label = f"{label}.proposal_outcomes[{index}]"
                proposal = _expect_object(item, item_label)
                _expect_keys(
                    proposal,
                    {"base_sha256", "outcome", "path", "proposal_sha256"},
                    item_label,
                )
                outcome = _expect_nonempty_string(
                    proposal["outcome"], f"{item_label}.outcome", max_bytes=32
                )
                if outcome not in {"applied", "no-op"}:
                    raise ProtocolError(f"{item_label}.outcome is not converged")
                normalized_outcomes.append(
                    {
                        "base_sha256": _expect_sha256(
                            proposal["base_sha256"], f"{item_label}.base_sha256"
                        ),
                        "outcome": outcome,
                        "path": _expect_nonempty_string(
                            proposal["path"], f"{item_label}.path", max_bytes=8_192
                        ),
                        "proposal_sha256": _expect_sha256(
                            proposal["proposal_sha256"], f"{item_label}.proposal_sha256"
                        ),
                    }
                )
            if [item["path"] for item in normalized_outcomes] != facts["documentation_proposals"]:
                raise ProtocolError(
                    "proposal outcome paths must exactly match frozen documentation_proposals"
                )
            normalized["proposal_outcomes"] = normalized_outcomes
        return normalized
    if phase == "worktree-removed":
        if closure_version not in {LEGACY_CLOSURE_VERSION, ISOLATED_CLOSURE_VERSION}:
            raise ProtocolError("worktree-removed is not valid for this closure protocol")
        expected_keys = {"path", "verified_absent"}
        if closure_version == ISOLATED_CLOSURE_VERSION:
            expected_keys.add("observed_before")
        _expect_keys(result, expected_keys, label)
        path = _expect_absolute_path(result["path"], f"{label}.path")
        if str(path) != facts["worktree_path"]:
            raise ProtocolError(
                f"worktree receipt path mismatch; expected={facts['worktree_path']}; observed={path}"
            )
        if not _expect_bool(result["verified_absent"], f"{label}.verified_absent"):
            raise ProtocolError("worktree receipt requires verified_absent=true")
        normalized_worktree = {"path": str(path), "verified_absent": True}
        if closure_version == ISOLATED_CLOSURE_VERSION:
            observed_before = _expect_nonempty_string(
                result["observed_before"], f"{label}.observed_before", max_bytes=64
            )
            if observed_before != "present-clean":
                raise ProtocolError(
                    "isolated worktree removal requires observed_before='present-clean'; "
                    "missing or ambiguous worktrees must remain explicit"
                )
            normalized_worktree["observed_before"] = observed_before
        return normalized_worktree
    if phase == "branch-removed":
        expected_keys = {"name", "verified_absent"}
        if closure_version == ISOLATED_CLOSURE_VERSION:
            expected_keys.add("observed_before")
        _expect_keys(result, expected_keys, label)
        name = _expect_nonempty_string(
            result["name"], f"{label}.name", max_bytes=1_024
        )
        if name != facts["implementation_branch"]:
            raise ProtocolError(
                f"branch receipt name mismatch; expected={facts['implementation_branch']!r}; observed={name!r}"
            )
        if not _expect_bool(result["verified_absent"], f"{label}.verified_absent"):
            raise ProtocolError("branch receipt requires verified_absent=true")
        normalized_branch = {"name": name, "verified_absent": True}
        if closure_version == ISOLATED_CLOSURE_VERSION:
            observed_before = _expect_nonempty_string(
                result["observed_before"], f"{label}.observed_before", max_bytes=64
            )
            if observed_before != "present-merged":
                raise ProtocolError(
                    "isolated branch removal requires observed_before='present-merged'; "
                    "missing, unmerged or ambiguous branches must not be hidden"
                )
            normalized_branch["observed_before"] = observed_before
        return normalized_branch
    if phase == "lease-released":
        if closure_version != CLOSURE_VERSION:
            raise ProtocolError("lease-released is only valid for zero-worktree closure v2")
        _expect_keys(result, {"lease_id", "path", "verified_absent"}, label)
        lease = facts["repository_lease"]
        lease_id = _expect_handoff_id(result["lease_id"], f"{label}.lease_id")
        if lease_id != lease["lease_id"]:
            raise ProtocolError(
                f"lease receipt ID mismatch; expected={lease['lease_id']}; observed={lease_id}"
            )
        path = _expect_absolute_path(result["path"], f"{label}.path")
        if str(path) != lease["path"]:
            raise ProtocolError(
                f"lease receipt path mismatch; expected={lease['path']}; observed={path}"
            )
        if not _expect_bool(result["verified_absent"], f"{label}.verified_absent"):
            raise ProtocolError("lease release receipt requires verified_absent=true")
        return {
            "lease_id": lease_id,
            "path": str(path),
            "verified_absent": True,
        }
    if phase == "execution-lease-released":
        if closure_version != ISOLATED_CLOSURE_VERSION:
            raise ProtocolError(
                "execution-lease-released is only valid for isolated closure v3"
            )
        _expect_keys(result, {"lease_id", "path", "state", "version"}, label)
        lease = facts["execution_lease"]
        lease_id = _expect_handoff_id(result["lease_id"], f"{label}.lease_id")
        path = str(_expect_absolute_path(result["path"], f"{label}.path"))
        version = _expect_int(result["version"], f"{label}.version", 1, 2**63 - 2)
        if lease_id != lease["lease_id"] or path != lease["path"]:
            raise ProtocolError("execution lease release receipt identity mismatch")
        if result["state"] != "available" or version != lease["version"] + 1:
            raise ProtocolError(
                "execution lease release must be exactly verified available at the next CAS version"
            )
        return {
            "lease_id": lease_id,
            "path": path,
            "state": "available",
            "version": version,
        }
    if phase == "remote-verified":
        _expect_keys(result, {"actions", "results", "verified"}, label)
        actions = _expect_string_list(result["actions"], f"{label}.actions")
        if actions != facts["remote_actions"]:
            raise ProtocolError(
                "remote receipt actions must exactly match the prepared remote_actions"
            )
        if not _expect_bool(result["verified"], f"{label}.verified"):
            raise ProtocolError("remote receipt requires verified=true")
        results = _expect_string_list(result["results"], f"{label}.results")
        if closure_version == ISOLATED_CLOSURE_VERSION:
            expected_results = [f"{action}:verified" for action in actions]
            if results != expected_results:
                raise ProtocolError(
                    "isolated remote receipt must bind one verified result to each prepared action"
                )
        return {
            "actions": actions,
            "results": results,
            "verified": True,
        }
    raise ProtocolError(f"phase {phase!r} does not accept an external result file")


def inspect_closure_checkpoint(
    checkpoint_path: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
    closure_root: Path = CLOSURE_ROOT,
) -> dict[str, Any]:
    document, data = _load_closure_checkpoint(
        checkpoint_path,
        runtime_root=Path(runtime_root),
        closure_root=Path(closure_root),
    )
    phase = document["phase"]
    cleanup_state: str
    cleanup_path = Path(document["cleanup_checkpoint"])
    if phase == "complete":
        final_result = document["receipts"][-1]["result"]
        if final_result != {"cleanup_state": "complete"}:
            raise ProtocolError(
                "complete closure receipt must record cleanup_state='complete'"
            )
        cleanup_state = "complete"
    else:
        cleanup_report = inspect_cleanup(
            cleanup_path, runtime_root=Path(runtime_root)
        )
        cleanup_state = cleanup_report["state"]
        if phase != "evidence-cleanup" and cleanup_state != "intact":
            raise ProtocolError(
                f"closure phase {phase!r} requires intact evidence; observed cleanup state={cleanup_state!r}"
            )
    return {
        "cleanup_state": cleanup_state,
        "closure_version": document["closure_version"],
        "facts": document["facts"],
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "handoff_id": document["handoff_id"],
        "path": str(Path(checkpoint_path)),
        "phase": phase,
        "receipts": document["receipts"],
    }


def advance_closure_checkpoint(
    checkpoint_path: Path,
    *,
    phase: str,
    result_path: Path | None,
    runtime_root: Path = RUNTIME_ROOT,
    closure_root: Path = CLOSURE_ROOT,
) -> dict[str, Any]:
    document, _ = _load_closure_checkpoint(
        checkpoint_path,
        runtime_root=Path(runtime_root),
        closure_root=Path(closure_root),
    )
    current_phase = document["phase"]
    phases = _closure_phases(document["closure_version"])
    current_index = phases.index(current_phase)
    if current_index + 1 >= len(phases):
        raise ProtocolError("complete closure checkpoint cannot advance")
    expected_phase = phases[current_index + 1]
    if phase != expected_phase:
        raise ProtocolError(
            f"invalid closure phase transition; current={current_phase!r}; expected={expected_phase!r}; requested={phase!r}"
        )
    if phase in {"evidence-cleanup", "complete"}:
        if result_path is not None:
            raise ProtocolError(f"phase {phase!r} does not accept --result")
        cleanup_report = inspect_cleanup(
            Path(document["cleanup_checkpoint"]), runtime_root=Path(runtime_root)
        )
        required_state = "intact" if phase == "evidence-cleanup" else "complete"
        if cleanup_report["state"] != required_state:
            raise ProtocolError(
                f"phase {phase!r} requires cleanup state {required_state!r}; observed={cleanup_report['state']!r}"
            )
        result = {"cleanup_state": required_state}
    else:
        if result_path is None:
            raise ProtocolError(f"phase {phase!r} requires --result")
        result_data = _read_regular_file(
            Path(result_path),
            max_bytes=MAX_CLOSURE_CHECKPOINT_BYTES,
            label=f"closure phase result for {phase}",
        )
        result_source = _load_json_bytes(
            result_data, f"closure phase result for {phase}"
        )
        result = _validate_closure_phase_result(
            phase,
            result_source,
            document["facts"],
            closure_version=document["closure_version"],
        )
    updated = dict(document)
    updated["phase"] = phase
    updated["receipts"] = document["receipts"] + [
        {"phase": phase, "result": result}
    ]
    updated_data = _canonical_json_bytes(updated)
    if len(updated_data) > MAX_CLOSURE_CHECKPOINT_BYTES:
        raise ProtocolError(
            f"closure checkpoint exceeds {MAX_CLOSURE_CHECKPOINT_BYTES} UTF-8 bytes; observed={len(updated_data)}"
        )
    root_fd = _open_directory(Path(closure_root))
    try:
        _replace_private_file(
            root_fd,
            name=Path(checkpoint_path).name,
            data=updated_data,
            max_bytes=MAX_CLOSURE_CHECKPOINT_BYTES,
            label="closure checkpoint",
        )
    finally:
        os.close(root_fd)
    return inspect_closure_checkpoint(
        checkpoint_path, runtime_root=runtime_root, closure_root=closure_root
    )


def _bundled_protocol_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "execution-protocol.md"


def _emit_json(value: Any) -> None:
    sys.stdout.buffer.write(_canonical_json_bytes(value))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify deterministic implementation, ACK and closure protocol data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_lease_parser = subparsers.add_parser("inspect-repository-lease")
    inspect_lease_parser.add_argument("--repository", required=True, type=Path)

    acquire_lease_parser = subparsers.add_parser("acquire-repository-lease")
    acquire_lease_parser.add_argument("--input", required=True, type=Path)

    verify_lease_parser = subparsers.add_parser("verify-repository-lease")
    verify_lease_parser.add_argument("--file", required=True, type=Path)
    verify_lease_parser.add_argument("--id", required=True)
    verify_lease_parser.add_argument("--bytes", required=True, type=int)
    verify_lease_parser.add_argument("--sha256", required=True)

    release_lease_parser = subparsers.add_parser("release-repository-lease")
    release_lease_parser.add_argument("--file", required=True, type=Path)
    release_lease_parser.add_argument("--id", required=True)
    release_lease_parser.add_argument("--bytes", required=True, type=int)
    release_lease_parser.add_argument("--sha256", required=True)

    inspect_document_lease_parser = subparsers.add_parser("inspect-document-lease")
    inspect_document_lease_parser.add_argument("--repository", required=True, type=Path)

    acquire_document_lease_parser = subparsers.add_parser("acquire-document-lease")
    acquire_document_lease_parser.add_argument("--input", required=True, type=Path)
    acquire_document_lease_parser.add_argument(
        "--wait-seconds", type=int, default=DOCUMENT_LEASE_WAIT_SECONDS
    )
    acquire_document_lease_parser.add_argument(
        "--max-retries", type=int, default=DOCUMENT_LEASE_MAX_RETRIES
    )

    verify_document_lease_parser = subparsers.add_parser("verify-document-lease")
    verify_document_lease_parser.add_argument("--file", required=True, type=Path)
    verify_document_lease_parser.add_argument("--id", required=True)
    verify_document_lease_parser.add_argument("--version", required=True, type=int)

    renew_document_lease_parser = subparsers.add_parser("renew-document-lease")
    renew_document_lease_parser.add_argument("--file", required=True, type=Path)
    renew_document_lease_parser.add_argument("--id", required=True)
    renew_document_lease_parser.add_argument("--version", required=True, type=int)
    renew_document_lease_parser.add_argument(
        "--ttl-seconds", required=True, type=int
    )

    release_document_lease_parser = subparsers.add_parser("release-document-lease")
    release_document_lease_parser.add_argument("--file", required=True, type=Path)
    release_document_lease_parser.add_argument("--id", required=True)
    release_document_lease_parser.add_argument("--version", required=True, type=int)

    inspect_repository_coordination_parser = subparsers.add_parser(
        "inspect-repository-coordination-lease"
    )
    inspect_repository_coordination_parser.add_argument("--repository", required=True, type=Path)

    acquire_repository_coordination_parser = subparsers.add_parser(
        "acquire-repository-coordination-lease"
    )
    acquire_repository_coordination_parser.add_argument("--input", required=True, type=Path)

    verify_repository_coordination_parser = subparsers.add_parser(
        "verify-repository-coordination-lease"
    )
    verify_repository_coordination_parser.add_argument("--file", required=True, type=Path)
    verify_repository_coordination_parser.add_argument("--id", required=True)
    verify_repository_coordination_parser.add_argument("--version", required=True, type=int)

    release_repository_coordination_parser = subparsers.add_parser(
        "release-repository-coordination-lease"
    )
    release_repository_coordination_parser.add_argument("--file", required=True, type=Path)
    release_repository_coordination_parser.add_argument("--id", required=True)
    release_repository_coordination_parser.add_argument("--version", required=True, type=int)

    inspect_worktree_execution_parser = subparsers.add_parser(
        "inspect-worktree-execution-leases"
    )
    inspect_worktree_execution_parser.add_argument("--repository", required=True, type=Path)

    create_worktree_state_parser = subparsers.add_parser("create-worktree-state-receipt")
    create_worktree_state_parser.add_argument("--input", required=True, type=Path)

    verify_worktree_state_parser = subparsers.add_parser("verify-worktree-state-receipt")
    verify_worktree_state_parser.add_argument("--input", required=True, type=Path)

    record_isolated_confirmation_parser = subparsers.add_parser("record-isolated-worktree-confirmation")
    record_isolated_confirmation_parser.add_argument("--input", required=True, type=Path)

    verify_isolated_confirmation_parser = subparsers.add_parser("verify-isolated-worktree-confirmation")
    verify_isolated_confirmation_parser.add_argument("--input", required=True, type=Path)

    create_isolated_worktree_parser = subparsers.add_parser("create-isolated-worktree")
    create_isolated_worktree_parser.add_argument("--input", required=True, type=Path)

    reconcile_isolated_worktree_parser = subparsers.add_parser("reconcile-isolated-worktree-creation")
    reconcile_isolated_worktree_parser.add_argument("--input", required=True, type=Path)

    acquire_worktree_execution_parser = subparsers.add_parser(
        "acquire-worktree-execution-lease"
    )
    acquire_worktree_execution_parser.add_argument("--input", required=True, type=Path)

    verify_worktree_execution_parser = subparsers.add_parser(
        "verify-worktree-execution-lease"
    )
    verify_worktree_execution_parser.add_argument("--file", required=True, type=Path)
    verify_worktree_execution_parser.add_argument("--id", required=True)
    verify_worktree_execution_parser.add_argument("--version", required=True, type=int)
    verify_worktree_execution_parser.add_argument("--platform-cwd", required=True, type=Path)

    release_worktree_execution_parser = subparsers.add_parser(
        "release-worktree-execution-lease"
    )
    release_worktree_execution_parser.add_argument("--file", required=True, type=Path)
    release_worktree_execution_parser.add_argument("--id", required=True)
    release_worktree_execution_parser.add_argument("--version", required=True, type=int)

    reconcile_worktree_execution_parser = subparsers.add_parser(
        "reconcile-worktree-execution-lease"
    )
    reconcile_worktree_execution_parser.add_argument("--file", required=True, type=Path)
    reconcile_worktree_execution_parser.add_argument("--id", required=True)
    reconcile_worktree_execution_parser.add_argument("--version", required=True, type=int)
    reconcile_worktree_execution_parser.add_argument("--platform-cwd", required=True, type=Path)
    reconcile_worktree_execution_parser.add_argument(
        "--outcome", required=True
    )

    execution_availability_parser = subparsers.add_parser(
        "check-execution-availability"
    )
    execution_availability_parser.add_argument("--input", required=True, type=Path)

    integration_revalidation_parser = subparsers.add_parser(
        "revalidate-integration"
    )
    integration_revalidation_parser.add_argument("--input", required=True, type=Path)

    create_handoff_parser = subparsers.add_parser("create-handoff")
    create_handoff_parser.add_argument("--input", required=True, type=Path)

    verify_handoff_parser = subparsers.add_parser("verify-handoff")
    verify_handoff_parser.add_argument("--file", required=True, type=Path)
    verify_handoff_parser.add_argument("--id", required=True)
    verify_handoff_parser.add_argument("--bytes", required=True, type=int)
    verify_handoff_parser.add_argument("--sha256", required=True)

    publish_parser = subparsers.add_parser("publish-supervision")
    publish_parser.add_argument("--handoff-file", required=True, type=Path)
    publish_parser.add_argument("--direction", required=True, choices=sorted(DIRECTIONS))
    publish_parser.add_argument("--kind", required=True)
    publish_parser.add_argument("--payload-file", required=True, type=Path)
    publish_parser.add_argument("--previous-manifest", type=Path)

    verify_supervision_parser = subparsers.add_parser("verify-supervision")
    verify_supervision_parser.add_argument("--handoff-file", required=True, type=Path)
    verify_supervision_parser.add_argument("--manifest-file", required=True, type=Path)
    verify_supervision_parser.add_argument("--expected-count", required=True, type=int)

    create_control_parser = subparsers.add_parser("create-control")
    create_control_parser.add_argument("--input", required=True, type=Path)

    verify_control_parser = subparsers.add_parser("verify-control")
    verify_control_parser.add_argument("--input", required=True, type=Path)
    verify_control_parser.add_argument("--handoff-file", required=True, type=Path)

    create_ack_parser = subparsers.add_parser("create-ack")
    create_ack_parser.add_argument("--control", required=True, type=Path)
    create_ack_parser.add_argument("--handoff-file", required=True, type=Path)

    verify_ack_parser = subparsers.add_parser("verify-ack")
    verify_ack_parser.add_argument("--input", required=True, type=Path)
    verify_ack_parser.add_argument("--control", required=True, type=Path)
    verify_ack_parser.add_argument("--handoff-file", required=True, type=Path)

    inspect_parser = subparsers.add_parser("inspect-cleanup")
    inspect_parser.add_argument("--checkpoint", required=True, type=Path)

    advance_parser = subparsers.add_parser("advance-cleanup")
    advance_parser.add_argument("--checkpoint", required=True, type=Path)

    create_closure_parser = subparsers.add_parser("create-closure-checkpoint")
    create_closure_parser.add_argument("--input", required=True, type=Path)

    inspect_closure_parser = subparsers.add_parser("inspect-closure-checkpoint")
    inspect_closure_parser.add_argument("--checkpoint", required=True, type=Path)

    advance_closure_parser = subparsers.add_parser("advance-closure-checkpoint")
    advance_closure_parser.add_argument("--checkpoint", required=True, type=Path)
    advance_closure_parser.add_argument(
        "--phase",
        required=True,
        choices=sorted(
            set(
                CLOSURE_PHASES[1:]
                + LEGACY_CLOSURE_PHASES[1:]
                + ISOLATED_CLOSURE_PHASES[1:]
            )
        ),
    )
    advance_closure_parser.add_argument("--result", type=Path)

    converge_proposal_parser = subparsers.add_parser("converge-document-proposal")
    converge_proposal_parser.add_argument("--input", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "inspect-repository-lease":
            _emit_json(inspect_repository_lease(arguments.repository))
        elif arguments.command == "acquire-repository-lease":
            _emit_json(acquire_repository_lease(arguments.input))
        elif arguments.command == "verify-repository-lease":
            _emit_json(
                verify_repository_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_bytes=arguments.bytes,
                    expected_sha256=arguments.sha256,
                )
            )
        elif arguments.command == "release-repository-lease":
            _emit_json(
                release_repository_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_bytes=arguments.bytes,
                    expected_sha256=arguments.sha256,
                )
            )
        elif arguments.command == "inspect-document-lease":
            _emit_json(inspect_document_lease(arguments.repository))
        elif arguments.command == "acquire-document-lease":
            _emit_json(
                acquire_document_lease(
                    arguments.input,
                    wait_seconds=arguments.wait_seconds,
                    max_retries=arguments.max_retries,
                )
            )
        elif arguments.command == "verify-document-lease":
            _emit_json(
                verify_document_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                )
            )
        elif arguments.command == "renew-document-lease":
            _emit_json(
                renew_document_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                    ttl_seconds=arguments.ttl_seconds,
                )
            )
        elif arguments.command == "release-document-lease":
            _emit_json(
                release_document_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                )
            )
        elif arguments.command == "inspect-repository-coordination-lease":
            _emit_json(inspect_repository_coordination_lease(arguments.repository))
        elif arguments.command == "acquire-repository-coordination-lease":
            _emit_json(acquire_repository_coordination_lease(arguments.input))
        elif arguments.command == "verify-repository-coordination-lease":
            _emit_json(
                verify_repository_coordination_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                )
            )
        elif arguments.command == "release-repository-coordination-lease":
            _emit_json(
                release_repository_coordination_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                )
            )
        elif arguments.command == "inspect-worktree-execution-leases":
            _emit_json(inspect_worktree_execution_leases(arguments.repository))
        elif arguments.command == "create-worktree-state-receipt":
            _emit_json(create_worktree_state_receipt(arguments.input))
        elif arguments.command == "verify-worktree-state-receipt":
            _emit_json(verify_worktree_state_receipt(arguments.input))
        elif arguments.command == "record-isolated-worktree-confirmation":
            _emit_json(record_isolated_confirmation(arguments.input))
        elif arguments.command == "verify-isolated-worktree-confirmation":
            _emit_json(verify_isolated_confirmation(arguments.input))
        elif arguments.command == "create-isolated-worktree":
            _emit_json(create_isolated_worktree(arguments.input))
        elif arguments.command == "reconcile-isolated-worktree-creation":
            _emit_json(reconcile_isolated_worktree_creation(arguments.input))
        elif arguments.command == "acquire-worktree-execution-lease":
            _emit_json(acquire_worktree_execution_lease(arguments.input))
        elif arguments.command == "verify-worktree-execution-lease":
            _emit_json(
                verify_worktree_execution_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                    platform_cwd=arguments.platform_cwd,
                )
            )
        elif arguments.command == "release-worktree-execution-lease":
            _emit_json(
                release_worktree_execution_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                )
            )
        elif arguments.command == "reconcile-worktree-execution-lease":
            _emit_json(
                reconcile_worktree_execution_lease(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_version=arguments.version,
                    platform_cwd=arguments.platform_cwd,
                    outcome=arguments.outcome,
                )
            )
        elif arguments.command == "check-execution-availability":
            _emit_json(check_execution_availability(arguments.input))
        elif arguments.command == "revalidate-integration":
            _emit_json(revalidate_integration(arguments.input))
        elif arguments.command == "create-handoff":
            _emit_json(
                create_handoff(arguments.input, _bundled_protocol_path())
            )
        elif arguments.command == "verify-handoff":
            _emit_json(
                verify_handoff(
                    arguments.file,
                    expected_id=arguments.id,
                    expected_bytes=arguments.bytes,
                    expected_sha256=arguments.sha256,
                )
            )
        elif arguments.command == "publish-supervision":
            _emit_json(
                publish_supervision(
                    arguments.handoff_file,
                    direction=arguments.direction,
                    kind=arguments.kind,
                    payload_path=arguments.payload_file,
                    previous_manifest_path=arguments.previous_manifest,
                )
            )
        elif arguments.command == "verify-supervision":
            _emit_json(
                verify_supervision(
                    arguments.handoff_file,
                    arguments.manifest_file,
                    expected_count=arguments.expected_count,
                )
            )
        elif arguments.command == "create-control":
            sys.stdout.buffer.write(create_control(arguments.input))
        elif arguments.command == "verify-control":
            _emit_json(
                verify_control(arguments.input, arguments.handoff_file)
            )
        elif arguments.command == "create-ack":
            sys.stdout.buffer.write(
                create_ack(arguments.control, arguments.handoff_file)
            )
        elif arguments.command == "verify-ack":
            _emit_json(
                verify_ack(
                    arguments.input,
                    arguments.control,
                    arguments.handoff_file,
                )
            )
        elif arguments.command == "inspect-cleanup":
            _emit_json(inspect_cleanup(arguments.checkpoint))
        elif arguments.command == "advance-cleanup":
            _emit_json(advance_cleanup(arguments.checkpoint))
        elif arguments.command == "create-closure-checkpoint":
            _emit_json(create_closure_checkpoint(arguments.input))
        elif arguments.command == "inspect-closure-checkpoint":
            _emit_json(inspect_closure_checkpoint(arguments.checkpoint))
        elif arguments.command == "advance-closure-checkpoint":
            _emit_json(
                advance_closure_checkpoint(
                    arguments.checkpoint,
                    phase=arguments.phase,
                    result_path=arguments.result,
                )
            )
        elif arguments.command == "converge-document-proposal":
            _emit_json(converge_document_proposal(arguments.input))
        else:
            parser.error(f"unknown command: {arguments.command}")
    except ProtocolError as error:
        _emit_json(_error_response(error, arguments.command))
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
