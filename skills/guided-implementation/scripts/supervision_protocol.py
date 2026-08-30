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

sys.path.insert(0, str(Path(__file__).parent))
from supervision_core import ArgumentSpec, CommandRegistry, CommandSpec, LeaseHolderPolicy


_CC_SWITCH_HOME = Path(
    os.environ.get("CC_SWITCH_HOME", Path.home() / ".cc-switch")
)
_CC_SWITCH_RUNTIME = Path(
    os.environ.get("CC_SWITCH_RUNTIME_ROOT", _CC_SWITCH_HOME / "runtime")
)
RUNTIME_ROOT = _CC_SWITCH_RUNTIME / "guided-implementation-handoffs"
CLOSURE_ROOT = _CC_SWITCH_RUNTIME / "change-closure-checkpoints"
IMPLEMENTATION_OUTCOME_ROOT = _CC_SWITCH_RUNTIME / "implementation-outcomes"
DOCUMENT_CONVERGENCE_ROOT = _CC_SWITCH_RUNTIME / "document-convergences"
WORKTREE_CLOSURE_ROOT = _CC_SWITCH_RUNTIME / "worktree-closures"
HANDOFF_FILENAME = "handoff.json"
HANDOFF_VERSION = 5
STALE_REPOSITORY_LEASE_FILENAME = "cc-switch-guided-implementation-lease.json"
DOCUMENT_LEASE_FILENAME = "cc-switch-document-lease.json"
DOCUMENT_LEASE_GUARD_FILENAME = "cc-switch-document-lease.guard"
DOCUMENT_LEASE_VERSION = 2
DOCUMENT_LEASE_SCHEMA = 2
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
IMPLEMENTATION_SOURCE_PROTECTION_FILENAME = (
    "cc-switch-implementation-source-protection.json"
)
IMPLEMENTATION_SOURCE_PROTECTION_GUARD_FILENAME = (
    "cc-switch-implementation-source-protection.guard"
)
IMPLEMENTATION_SOURCE_PROTECTION_VERSION = 1
IMPLEMENTATION_SOURCE_PROTECTION_SCHEMA = 1
MAX_IMPLEMENTATION_SOURCE_PROTECTION_BYTES = 262_144
REPOSITORY_COORDINATION_LEASE_FILENAME = "cc-switch-repository-coordination-lease.json"
REPOSITORY_COORDINATION_LEASE_GUARD_FILENAME = "cc-switch-repository-coordination-lease.guard"
REPOSITORY_COORDINATION_LEASE_VERSION = 1
REPOSITORY_COORDINATION_LEASE_SCHEMA = 1
REPOSITORY_COORDINATION_LEASE_PURPOSES = {
    "checkpoint-publish",
}
REPOSITORY_COORDINATION_LEASE_STAGES = {
    "design-discussion",
    "guided-implementation",
    "change-closure",
}
MAX_REPOSITORY_COORDINATION_LEASE_TTL_SECONDS = 900
MAX_REPOSITORY_COORDINATION_LEASE_BYTES = 16_384
WORKTREE_EXECUTION_CLAIM_DIRECTORY = "cc-switch-worktree-execution-claims"
WORKTREE_EXECUTION_CLAIM_GUARD_FILENAME = "cc-switch-worktree-execution-claims.guard"
WORKTREE_EXECUTION_CLAIM_VERSION = 1
WORKTREE_EXECUTION_CLAIM_SCHEMA = 1
MAX_WORKTREE_EXECUTION_CLAIM_BYTES = 65_536
TARGET_PUBLICATION_DIRECTORY = "cc-switch-target-publications"
TARGET_PUBLICATION_GUARD_FILENAME = "cc-switch-target-publications.guard"
TARGET_PUBLICATION_VERSION = 1
TARGET_PUBLICATION_SCHEMA = 1
MAX_TARGET_PUBLICATION_BYTES = 131_072
WORKTREE_STATE_RECEIPT_DIRECTORY = "cc-switch-worktree-state-receipts"
WORKTREE_STATE_RECEIPT_VERSION = 1
SUPERVISION_VERSION = 1
MANIFEST_VERSION = 1
CONTROL_VERSION = 1
CLEANUP_VERSION = 1
MAX_HANDOFF_BYTES = 524_288
MAX_SUPERVISION_FILE_BYTES = 524_288
MAX_CONTROL_BYTES = 8_192
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
MESSAGE_PROTOCOL = "supervision-duplex-observe-v1"
DELIVERY_POLICY = MESSAGE_PROTOCOL
MESSAGE_TYPE = "SUPERVISION_MESSAGE"
DELIVERY_CHECK_DELAY_SECONDS = 5
DELIVERY_RESEND_LIMIT = 1
DELIVERY_INSTRUCTION = (
    "DELIVERY CHECK REQUIRED: after sending, wait 5 seconds and inspect the "
    "authenticated target task once for the exact canonical message and "
    "message_id. Resend the byte-identical message once only when exact absence "
    "is proven, then wait 5 seconds and inspect once more. Do not send an ACK. "
    "Apply each verified message_id at most once."
)
MESSAGE_CARD_FORMAT = "supervision-chinese-card-v1"
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
WORKTREE_CLOSURE_PHASES = (
    "documents-committed",
    "worktree-removed",
    "branch-removed",
    "execution-claim-released",
    "archived",
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
        "discussion_receipt_invalid": "讨论协议未能验证当前来源、依赖或活动运行凭证。",
        "integration_state_stale": "集成前的权威来源、依赖、活动运行或 Git 状态已变化。",
        "outcome_unknown": "操作结果未知，必须先检查权威状态再恢复。",
        "worktree_branch_mismatch": "精确 worktree 的分支或基础提交与冻结绑定不一致。",
        "worktree_exact_missing": "冻结绑定中的精确 worktree 不存在。",
        "document_lease_invalid": "文档提案没有精确、有效的 4归档文档租约。",
        "document_proposal_authority_invalid": "文档提案不属于冻结的 isolated worktree、handoff 或目标集合。",
        "document_proposal_conflict": "基础 checkout 中的文档已偏离提案基线，必须人工收敛。",
        "implementation_source_protected": "目标文档是活动实现的冻结来源，当前操作不得修改。",
        "implementation_source_drift": "活动实现的冻结来源已被外部修改。",
        "implementation_source_cas_mismatch": "实现来源保护记录的 CAS 已变化。",
        "worktree_claim_conflict": "worktree 执行所有权已被其他不可变绑定占用。",
        "worktree_claim_cas_mismatch": "worktree claim 的 ID、版本或字节 CAS 已变化。",
        "worktree_provisioning_ambiguous": "worktree、分支引用或文件系统存在无法自动认领的部分状态。",
        "worktree_platform_mismatch": "平台工作目录、Git common directory 或冻结 worktree 绑定不一致。",
        "candidate_review_invalid": "候选提交、测试证据或审查结论没有形成精确且可验收的绑定。",
        "candidate_acceptance_stale": "候选提交或审查记录在验收后已变化。",
        "publication_checkout_invalid": "主 checkout 不在干净、正确分支且无 Git 中间操作的发布状态。",
        "publication_head_mismatch": "目标分支 HEAD 已偏离发布 CAS 的预期值。",
        "publication_conflict": "候选与当前目标分支发生冲突，主 checkout 已回滚，必须在原 worktree 修复。",
        "publication_restore_failed": "合并失败后无法证明主 checkout 已恢复到发布前状态。",
        "publication_outcome_ambiguous": "发布结果不是无效果或唯一可认领的合并提交。",
        "unsupported_stale_execution_state": "检测到切换前的活动执行状态；当前协议不会迁移、释放或完成该状态。",
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
    if "proposal_path" in source:
        raise ProtocolError(
            "document_proposal_authority_invalid",
            "proposal_path is derived from the frozen closure worktree and cannot be caller supplied",
        )
    _expect_keys(
        source,
        {
            "base_sha256",
            "closure_checkpoint",
            "document_lease",
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
    if target_text not in holder.get("paths", []):
        raise ProtocolError(
            "document_lease_invalid",
            "document lease does not authorize the exact convergence target",
            context={"repository": str(repository)},
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

    authority = _load_document_proposal_authority(
        source["closure_checkpoint"],
        repository=repository,
        target_path=target_text,
    )
    proposal_path = Path(authority["proposal_path"])
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
        "proposal_authority": authority["proposal_authority"],
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


def _validate_handoff_envelope(value: Any) -> dict[str, Any]:
    envelope = _expect_object(value, "handoff envelope")
    _expect_keys(
        envelope,
        {"implementation_source_checkpoint", "repository", "worktree"},
        "handoff envelope",
    )
    repository = _expect_absolute_path(envelope["repository"], "handoff envelope.repository")
    _validate_implementation_source_checkpoint(envelope["implementation_source_checkpoint"], "handoff envelope.implementation_source_checkpoint")
    worktree = _expect_object(envelope["worktree"], "handoff envelope.worktree")
    _expect_keys(worktree, {"base_commit", "branch", "execution_claim", "scope_sha256", "worktree_path"}, "handoff envelope.worktree")
    _expect_git_oid(worktree["base_commit"], "handoff worktree base_commit")
    _expect_nonempty_string(worktree["branch"], "handoff worktree branch", max_bytes=1024)
    worktree_path = _expect_absolute_path(worktree["worktree_path"], "handoff worktree path")
    _expect_sha256(worktree["scope_sha256"], "handoff worktree scope_sha256")
    claim = _expect_object(worktree["execution_claim"], "handoff worktree execution_claim")
    _expect_keys(claim, {"claim_id", "file_bytes", "file_sha256", "path", "version"}, "handoff worktree execution_claim")
    _expect_handoff_id(claim["claim_id"], "handoff claim ID")
    _expect_int(claim["file_bytes"], "handoff claim bytes", 1, MAX_WORKTREE_EXECUTION_CLAIM_BYTES)
    _expect_sha256(claim["file_sha256"], "handoff claim SHA-256")
    _expect_int(claim["version"], "handoff claim version", 1, 2**63 - 2)
    claim_path = _expect_absolute_path(claim["path"], "handoff claim path")
    if claim_path.parent != repository / ".git" / WORKTREE_EXECUTION_CLAIM_DIRECTORY or claim_path.suffix != ".json" or worktree_path == repository:
        raise ProtocolError("handoff worktree claim or path is not repository-scoped")
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
    _validate_handoff_envelope(envelope)
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
    if outer.get("handoff_version") != HANDOFF_VERSION:
        raise ProtocolError(
            "unsupported_stale_execution_state",
            "only the current version-5 worktree handoff is supported",
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
        outer["handoff_version"], "handoff.handoff_version", HANDOFF_VERSION, HANDOFF_VERSION
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
    _validate_handoff_envelope(envelope)
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
            path = Path(value).resolve(strict=False)
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
    claims = []
    claim_directory = _worktree_execution_claim_directory(repository)
    if claim_directory.exists():
        for path in sorted(claim_directory.glob("*.json")):
            document, data = _read_worktree_execution_claim(path)
            report = _worktree_claim_report(path, document, data)
            claims.append(
                {
                    "binding": report["binding"],
                    "claim_id": report["claim_id"],
                    "file_sha256": report["file_sha256"],
                    "state": report["state"],
                    "version": report["version"],
                }
            )
    return {
        "execution_claims": claims,
        "git_worktrees": worktrees,
    }


def _worktree_state_receipt_path(repository: Path, topic_id: str) -> Path:
    name = hashlib.sha256(topic_id.encode("utf-8")).hexdigest() + ".json"
    return repository / ".git" / WORKTREE_STATE_RECEIPT_DIRECTORY / name


def create_worktree_state_receipt(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(input_path, max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES, label="worktree state receipt input"),
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
            old = _expect_object(_load_json_bytes(_read_regular_file(path, max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES, label="worktree state receipt"), "worktree state receipt", require_canonical=True), "worktree state receipt")
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
            _replace_private_file(directory_fd, name=path.name, data=data, max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES, label="worktree state receipt")
        else:
            _publish_group(directory_fd, [(path.name, data)])
        os.fsync(directory_fd)
        return {**document, "file_bytes": len(data), "file_sha256": _sha256(data), "path": str(path), "state": "issued"}
    finally:
        os.close(directory_fd)
        os.close(git_fd)


def verify_worktree_state_receipt(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES, label="worktree state verification input"), "worktree state verification input"), "worktree state verification input")
    _expect_keys(source, {"file_bytes", "file_sha256", "path", "project_id", "repository", "topic_id", "tree_id", "version"}, "worktree state verification input")
    repository = _expect_absolute_path(source["repository"], "repository")
    topic_id = _expect_nonempty_string(source["topic_id"], "topic_id", max_bytes=128)
    path = _expect_absolute_path(source["path"], "receipt path")
    expected_path = _worktree_state_receipt_path(repository, topic_id)
    if path != expected_path:
        raise ProtocolError("receipt_identity_mismatch", f"worktree receipt path mismatch; expected={expected_path}; observed={path}", context={"repository": str(repository), "topic_id": topic_id})
    data = _read_regular_file(path, max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES, label="worktree state receipt")
    if len(data) != _expect_int(source["file_bytes"], "file_bytes", 1, MAX_WORKTREE_EXECUTION_CLAIM_BYTES) or _sha256(data) != _expect_sha256(source["file_sha256"], "file_sha256"):
        raise ProtocolError("receipt_cas_mismatch", "worktree receipt byte identity changed", context={"repository": str(repository), "topic_id": topic_id})
    receipt = _expect_object(_load_json_bytes(data, "worktree state receipt", require_canonical=True), "worktree state receipt")
    for key in ("project_id", "repository", "topic_id", "tree_id", "version"):
        if receipt.get(key) != source[key]:
            raise ProtocolError("receipt_identity_mismatch", f"worktree receipt {key} mismatch", context={"repository": str(repository), "topic_id": topic_id, "receipt_id": receipt.get("receipt_id")})
    current = _worktree_state_snapshot(repository)
    if receipt.get("snapshot") != current:
        raise ProtocolError("receipt_stale", "Git worktree or execution claim state changed after receipt issuance", retryable=True, context={"repository": str(repository), "topic_id": topic_id, "receipt_id": receipt.get("receipt_id"), "current": current})
    return {**receipt, "file_bytes": len(data), "file_sha256": _sha256(data), "path": str(path), "state": "valid", "verified": True}


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


def _worktree_execution_claim_directory(repository: Path) -> Path:
    return repository / ".git" / WORKTREE_EXECUTION_CLAIM_DIRECTORY


def _worktree_execution_claim_path(repository: Path, worktree_path: Path) -> Path:
    name = hashlib.sha256(str(worktree_path).encode("utf-8")).hexdigest() + ".json"
    return _worktree_execution_claim_directory(repository) / name


def _open_worktree_execution_claim_directory(repository: Path) -> tuple[Path, int, int]:
    return _open_private_git_subdirectory(repository, WORKTREE_EXECUTION_CLAIM_DIRECTORY)


def _open_worktree_execution_claim_guard(git_fd: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            WORKTREE_EXECUTION_CLAIM_GUARD_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=git_fd,
        )
    except FileExistsError:
        descriptor = os.open(
            WORKTREE_EXECUTION_CLAIM_GUARD_FILENAME,
            os.O_RDWR | nofollow,
            dir_fd=git_fd,
        )
    status = os.fstat(descriptor)
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.getuid()
        or _mode_bits(status) != 0o600
    ):
        os.close(descriptor)
        raise ProtocolError("worktree execution claim guard is unsafe")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _normalize_worktree_claim_binding(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(
        source,
        {
            "base_commit",
            "implementation_branch",
            "implementation_id",
            "phase_run_id",
            "repository",
            "scope_sha256",
            "source_checkpoint",
            "topic_id",
            "worktree_path",
        },
        label,
    )
    repository = _expect_absolute_path(source["repository"], f"{label}.repository")
    repository = repository.resolve(strict=True)
    if not (repository / ".git").is_dir():
        raise ProtocolError(
            "worktree_claim_conflict",
            "execution claims must be reserved from the ordinary Git checkout",
            context={"repository": str(repository)},
        )
    worktree_path = _expect_absolute_path(
        source["worktree_path"], f"{label}.worktree_path"
    )
    canonical_worktree_path = worktree_path.resolve(strict=False)
    if worktree_path != canonical_worktree_path or worktree_path == repository:
        raise ProtocolError(
            "worktree_claim_conflict",
            "worktree_path must be canonical and differ from the ordinary checkout",
            context={"repository": str(repository)},
        )
    branch = _expect_nonempty_string(
        source["implementation_branch"],
        f"{label}.implementation_branch",
        max_bytes=1024,
    )
    checked = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if checked.returncode != 0:
        raise ProtocolError(
            "worktree_claim_conflict",
            "implementation branch is not a valid local branch name",
            cause=checked.stderr.strip() or None,
            context={"repository": str(repository)},
        )
    base_commit = _expect_git_oid(source["base_commit"], f"{label}.base_commit")
    base_check = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "-e", f"{base_commit}^{{commit}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if base_check.returncode != 0:
        raise ProtocolError(
            "worktree_claim_conflict",
            "base_commit does not identify a local commit",
            cause=base_check.stderr.strip() or None,
            context={"repository": str(repository)},
        )
    return {
        "base_commit": base_commit,
        "implementation_branch": branch,
        "implementation_id": _expect_nonempty_string(
            source["implementation_id"],
            f"{label}.implementation_id",
            max_bytes=256,
        ),
        "phase_run_id": _expect_nonempty_string(
            source["phase_run_id"], f"{label}.phase_run_id", max_bytes=128
        ),
        "repository": str(repository),
        "scope_sha256": _expect_sha256(
            source["scope_sha256"], f"{label}.scope_sha256"
        ),
        "source_checkpoint": _validate_implementation_source_checkpoint(
            source["source_checkpoint"], f"{label}.source_checkpoint"
        ),
        "topic_id": _expect_nonempty_string(
            source["topic_id"], f"{label}.topic_id", max_bytes=128
        ),
        "worktree_path": str(worktree_path),
    }


def _validate_worktree_execution_claim(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(
        source,
        {
            "binding",
            "claim_id",
            "provisioning",
            "repository",
            "schema",
            "state",
            "version",
            "worktree_execution_claim_version",
        },
        label,
    )
    if (
        source["schema"] != WORKTREE_EXECUTION_CLAIM_SCHEMA
        or source["worktree_execution_claim_version"]
        != WORKTREE_EXECUTION_CLAIM_VERSION
    ):
        raise ProtocolError("worktree execution claim version is unsupported")
    binding = _normalize_worktree_claim_binding(source["binding"], f"{label}.binding")
    repository = str(_expect_absolute_path(source["repository"], f"{label}.repository"))
    if repository != binding["repository"]:
        raise ProtocolError("worktree execution claim repository does not match binding")
    state = _expect_nonempty_string(source["state"], f"{label}.state", max_bytes=32)
    if state not in {"reserved", "active", "released"}:
        raise ProtocolError("worktree execution claim state is unsupported")
    provisioning = source["provisioning"]
    if provisioning is not None:
        provisioning = _expect_object(provisioning, f"{label}.provisioning")
    return {
        "binding": binding,
        "claim_id": _expect_handoff_id(source["claim_id"], f"{label}.claim_id"),
        "provisioning": provisioning,
        "repository": repository,
        "schema": WORKTREE_EXECUTION_CLAIM_SCHEMA,
        "state": state,
        "version": _expect_int(source["version"], f"{label}.version", 1, 2**63 - 2),
        "worktree_execution_claim_version": WORKTREE_EXECUTION_CLAIM_VERSION,
    }


def _read_worktree_execution_claim(path: Path) -> tuple[dict[str, Any], bytes]:
    data = _read_regular_file(
        path,
        max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
        label="worktree execution claim",
    )
    document = _validate_worktree_execution_claim(
        _load_json_bytes(data, "worktree execution claim", require_canonical=True),
        "worktree execution claim",
    )
    return document, data


def _worktree_claim_report(
    path: Path, document: dict[str, Any], data: bytes
) -> dict[str, Any]:
    return {
        **document,
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "path": str(path),
    }


def _git_branch_oid(repository: Path, branch: str) -> str | None:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode == 1:
        return None
    if completed.returncode != 0:
        raise ProtocolError(
            "worktree_provisioning_ambiguous",
            "cannot inspect the implementation branch ref",
            cause=completed.stderr.strip() or None,
            context={"repository": str(repository)},
        )
    return _expect_git_oid(completed.stdout.strip(), "implementation branch ref")


def _worktree_provisioning_observation(binding: dict[str, Any]) -> dict[str, Any]:
    repository = Path(binding["repository"])
    path = Path(binding["worktree_path"])
    worktrees = _active_git_worktrees(repository)
    path_matches = [item for item in worktrees if item["path"] == str(path)]
    branch_matches = [
        item
        for item in worktrees
        if item.get("branch") == binding["implementation_branch"]
    ]
    branch_oid = _git_branch_oid(repository, binding["implementation_branch"])
    path_exists = os.path.lexists(path)
    observation = {
        "branch_oid": branch_oid,
        "branch_worktrees": branch_matches,
        "path_exists": path_exists,
        "path_worktrees": path_matches,
    }
    if len(path_matches) == 1:
        observed = path_matches[0]
        if (
            path.is_dir()
            and not observed.get("detached")
            and observed.get("branch") == binding["implementation_branch"]
            and observed.get("head") == binding["base_commit"]
            and branch_oid == binding["base_commit"]
            and branch_matches == path_matches
        ):
            return {**observation, "classification": "exact-adoption"}
        return {**observation, "classification": "ambiguous"}
    if path_matches or branch_matches or path_exists:
        return {**observation, "classification": "ambiguous"}
    if branch_oid is None:
        return {
            **observation,
            "classification": "safe-retry",
            "retry_action": "create-branch",
        }
    if branch_oid == binding["base_commit"]:
        return {
            **observation,
            "classification": "safe-retry",
            "retry_action": "reuse-exact-branch",
        }
    return {**observation, "classification": "ambiguous"}


def inspect_worktree_execution_claims(repository: Path) -> dict[str, Any]:
    repository = _expect_absolute_path(repository, "repository").resolve(strict=True)
    directory = _worktree_execution_claim_directory(repository)
    claims = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            document, data = _read_worktree_execution_claim(path)
            claims.append(_worktree_claim_report(path, document, data))
    return {"claims": claims, "repository": str(repository), "state": "inspected"}


def cutover_preflight(repository: Path) -> dict[str, Any]:
    repository = _expect_absolute_path(repository, "repository").resolve(strict=True)
    if not (repository / ".git").is_dir():
        raise ProtocolError("unsupported_stale_execution_state", "cutover requires the ordinary Git checkout", context={"repository": str(repository)})
    stale_targets = [
        repository / ".git" / STALE_REPOSITORY_LEASE_FILENAME,
        repository / ".git" / "cc-switch-worktree-execution-leases",
        repository / ".git" / "cc-switch-isolated-worktree-confirmations",
    ]
    observed = [str(path) for path in stale_targets if os.path.lexists(path)]
    if observed:
        raise ProtocolError(
            "unsupported_stale_execution_state",
            "pre-cutover execution artifacts are unsupported and were left byte-for-byte unchanged",
            context={"current": {"artifacts": observed}, "repository": str(repository)},
        )
    return {"repository": str(repository), "state": "ready", "stale_artifacts": []}


def reserve_worktree_execution_claim(input_path: Path) -> dict[str, Any]:
    binding = _normalize_worktree_claim_binding(
        _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
                label="worktree execution claim input",
            ),
            "worktree execution claim input",
        ),
        "worktree execution claim input",
    )
    repository = Path(binding["repository"])
    _verify_active_source_protection_binding(
        repository,
        binding["implementation_id"],
        expected_checkpoint=binding["source_checkpoint"],
    )
    path = _worktree_execution_claim_path(
        repository, Path(binding["worktree_path"])
    )
    directory, directory_fd, git_fd = _open_worktree_execution_claim_directory(
        repository
    )
    guard_fd = _open_worktree_execution_claim_guard(git_fd)
    try:
        if path.exists():
            current, current_data = _read_worktree_execution_claim(path)
            report = _worktree_claim_report(path, current, current_data)
            if current["binding"] == binding and current["state"] in {
                "reserved",
                "active",
            }:
                return {**report, "created": False}
            raise ProtocolError(
                "worktree_claim_conflict",
                "the exact worktree path already has a different durable claim",
                context={
                    "current": report,
                    "repository": str(repository),
                    "topic_id": binding["topic_id"],
                },
            )
        for other_path in sorted(directory.glob("*.json")):
            other, other_data = _read_worktree_execution_claim(other_path)
            if other["state"] == "released":
                continue
            other_binding = other["binding"]
            if (
                other_binding["implementation_id"] == binding["implementation_id"]
                or other_binding["implementation_branch"]
                == binding["implementation_branch"]
                or other_binding["worktree_path"] == binding["worktree_path"]
            ):
                raise ProtocolError(
                    "worktree_claim_conflict",
                    "implementation identity, branch or worktree path is already claimed",
                    context={
                        "current": _worktree_claim_report(
                            other_path, other, other_data
                        ),
                        "repository": str(repository),
                        "topic_id": binding["topic_id"],
                    },
                )
        observation = _worktree_provisioning_observation(binding)
        if observation["classification"] != "safe-retry" or observation.get(
            "retry_action"
        ) != "create-branch":
            raise ProtocolError(
                "worktree_claim_conflict",
                "claim reservation requires an unused branch and worktree path",
                context={
                    "current": observation,
                    "repository": str(repository),
                    "topic_id": binding["topic_id"],
                },
            )
        document = {
            "binding": binding,
            "claim_id": secrets.token_hex(16),
            "provisioning": None,
            "repository": str(repository),
            "schema": WORKTREE_EXECUTION_CLAIM_SCHEMA,
            "state": "reserved",
            "version": 1,
            "worktree_execution_claim_version": WORKTREE_EXECUTION_CLAIM_VERSION,
        }
        data = _canonical_json_bytes(document)
        _publish_group(directory_fd, [(path.name, data)])
        os.fsync(directory_fd)
        report = _worktree_claim_report(path, document, data)
        if os.environ.get("CODEX_SUPERVISION_TEST_FAILPOINT") == "claim-after-publish":
            raise ProtocolError(
                "outcome_unknown",
                "worktree claim was published before the caller observed completion",
                retryable=True,
                context={
                    "current": report,
                    "repository": str(repository),
                    "topic_id": binding["topic_id"],
                },
            )
        return {**report, "created": True}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def _verify_worktree_claim_receipt(
    value: Any,
    repository: Path,
    *,
    allowed_states: set[str],
) -> tuple[Path, dict[str, Any], bytes]:
    receipt = _expect_object(value, "claim")
    _expect_keys(
        receipt,
        {"claim_id", "file_bytes", "file_sha256", "path", "version"},
        "claim",
    )
    path = _expect_absolute_path(receipt["path"], "claim.path")
    document, data = _read_worktree_execution_claim(path)
    expected_path = _worktree_execution_claim_path(
        repository, Path(document["binding"]["worktree_path"])
    )
    if (
        path != expected_path
        or document["repository"] != str(repository)
        or document["claim_id"]
        != _expect_handoff_id(receipt["claim_id"], "claim.claim_id")
        or document["version"]
        != _expect_int(receipt["version"], "claim.version", 1, 2**63 - 2)
        or len(data)
        != _expect_int(
            receipt["file_bytes"],
            "claim.file_bytes",
            1,
            MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
        )
        or _sha256(data) != _expect_sha256(receipt["file_sha256"], "claim.file_sha256")
        or document["state"] not in allowed_states
    ):
        raise ProtocolError(
            "worktree_claim_cas_mismatch",
            "worktree execution claim CAS verification failed",
            context={
                "repository": str(repository),
                "topic_id": document["binding"]["topic_id"],
            },
        )
    return path, document, data


def _activate_worktree_execution_claim(
    path: Path,
    claim: dict[str, Any],
    claim_data: bytes,
    observation: dict[str, Any],
) -> dict[str, Any]:
    repository = Path(claim["repository"])
    _, directory_fd, git_fd = _open_worktree_execution_claim_directory(repository)
    guard_fd = _open_worktree_execution_claim_guard(git_fd)
    try:
        current, current_data = _read_worktree_execution_claim(path)
        if current_data != claim_data or current != claim or current["state"] != "reserved":
            raise ProtocolError(
                "worktree_claim_cas_mismatch",
                "worktree execution claim changed before activation",
                context={
                    "repository": str(repository),
                    "topic_id": claim["binding"]["topic_id"],
                },
            )
        updated = {
            **current,
            "provisioning": observation,
            "state": "active",
            "version": current["version"] + 1,
        }
        updated_data = _canonical_json_bytes(updated)
        _replace_private_file(
            directory_fd,
            name=path.name,
            data=updated_data,
            max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
            label="worktree execution claim",
        )
        os.fsync(directory_fd)
        return _worktree_claim_report(path, updated, updated_data)
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def reconcile_worktree_provisioning(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
                label="worktree provisioning reconciliation input",
            ),
            "worktree provisioning reconciliation input",
        ),
        "worktree provisioning reconciliation input",
    )
    _expect_keys(source, {"claim", "repository"}, "worktree provisioning reconciliation input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(
        strict=True
    )
    path, claim, claim_data = _verify_worktree_claim_receipt(
        source["claim"], repository, allowed_states={"reserved", "active"}
    )
    observation = _worktree_provisioning_observation(claim["binding"])
    if observation["classification"] == "ambiguous":
        raise ProtocolError(
            "worktree_provisioning_ambiguous",
            "partial worktree state does not exactly match the durable claim",
            retryable=False,
            context={
                "current": observation,
                "repository": str(repository),
                "topic_id": claim["binding"]["topic_id"],
            },
        )
    if observation["classification"] == "exact-adoption":
        if claim["state"] == "active":
            return {
                **_worktree_claim_report(path, claim, claim_data),
                "classification": "exact-adoption",
                "reconciled": True,
            }
        activated = _activate_worktree_execution_claim(
            path, claim, claim_data, observation
        )
        return {**activated, "classification": "exact-adoption", "reconciled": True}
    if claim["state"] != "reserved":
        raise ProtocolError(
            "worktree_provisioning_ambiguous",
            "active claim no longer has its exact worktree",
            context={
                "current": observation,
                "repository": str(repository),
                "topic_id": claim["binding"]["topic_id"],
            },
        )
    return {
        **_worktree_claim_report(path, claim, claim_data),
        "classification": "safe-retry",
        "observation": observation,
        "reconciled": True,
    }


def provision_claimed_worktree(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
                label="claimed worktree provisioning input",
            ),
            "claimed worktree provisioning input",
        ),
        "claimed worktree provisioning input",
    )
    _expect_keys(source, {"claim", "repository"}, "claimed worktree provisioning input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(
        strict=True
    )
    path, claim, claim_data = _verify_worktree_claim_receipt(
        source["claim"], repository, allowed_states={"reserved"}
    )
    binding = claim["binding"]
    observation = _worktree_provisioning_observation(binding)
    if observation["classification"] == "ambiguous":
        raise ProtocolError(
            "worktree_provisioning_ambiguous",
            "pre-existing Git or filesystem state does not match the durable claim",
            context={
                "current": observation,
                "repository": str(repository),
                "topic_id": binding["topic_id"],
            },
        )
    if observation["classification"] == "exact-adoption":
        activated = _activate_worktree_execution_claim(
            path, claim, claim_data, observation
        )
        return {**activated, "classification": "exact-adoption", "created": False}
    try:
        if observation["retry_action"] == "create-branch":
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "branch",
                    "--",
                    binding["implementation_branch"],
                    binding["base_commit"],
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        if os.environ.get("CODEX_SUPERVISION_TEST_FAILPOINT") == "claim-after-branch":
            raise ProtocolError(
                "outcome_unknown",
                "branch creation completed before worktree registration",
                retryable=True,
                context={
                    "current": _worktree_provisioning_observation(binding),
                    "repository": str(repository),
                    "topic_id": binding["topic_id"],
                },
            )
        Path(binding["worktree_path"]).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "worktree",
                "add",
                binding["worktree_path"],
                binding["implementation_branch"],
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except ProtocolError:
        raise
    except (OSError, subprocess.CalledProcessError) as error:
        current = _worktree_provisioning_observation(binding)
        if current["classification"] == "ambiguous":
            raise ProtocolError(
                "worktree_provisioning_ambiguous",
                "worktree provisioning failed with non-adoptable partial state",
                cause=str(error),
                context={
                    "current": current,
                    "repository": str(repository),
                    "topic_id": binding["topic_id"],
                },
            ) from error
        raise ProtocolError(
            "outcome_unknown",
            "worktree provisioning did not complete; exact reconciliation is required",
            retryable=True,
            cause=str(error),
            context={
                "current": current,
                "repository": str(repository),
                "topic_id": binding["topic_id"],
            },
        ) from error
    observation = _worktree_provisioning_observation(binding)
    if observation["classification"] != "exact-adoption":
        raise ProtocolError(
            "worktree_provisioning_ambiguous",
            "created worktree failed exact postcondition verification",
            context={
                "current": observation,
                "repository": str(repository),
                "topic_id": binding["topic_id"],
            },
        )
    if os.environ.get("CODEX_SUPERVISION_TEST_FAILPOINT") == "claim-after-worktree":
        raise ProtocolError(
            "outcome_unknown",
            "worktree creation completed before claim activation",
            retryable=True,
            context={
                "current": observation,
                "repository": str(repository),
                "topic_id": binding["topic_id"],
            },
        )
    activated = _activate_worktree_execution_claim(path, claim, claim_data, observation)
    return {**activated, "classification": "exact-adoption", "created": True}


def verify_worktree_execution_claim(
    claim_path: Path,
    *,
    expected_id: str,
    expected_version: int,
    expected_bytes: int,
    expected_sha256: str,
    platform_cwd: Path,
) -> dict[str, Any]:
    claim_path = _expect_absolute_path(claim_path, "worktree execution claim path")
    claim, data = _read_worktree_execution_claim(claim_path)
    repository = Path(claim["repository"])
    receipt = {
        "claim_id": expected_id,
        "file_bytes": expected_bytes,
        "file_sha256": expected_sha256,
        "path": str(claim_path),
        "version": expected_version,
    }
    _, claim, data = _verify_worktree_claim_receipt(
        receipt, repository, allowed_states={"active"}
    )
    platform_cwd = _expect_absolute_path(platform_cwd, "platform working directory")
    if platform_cwd.resolve(strict=True) != Path(claim["binding"]["worktree_path"]):
        raise ProtocolError(
            "worktree_platform_mismatch",
            "platform working directory does not equal the claimed worktree",
            context={
                "repository": str(repository),
                "topic_id": claim["binding"]["topic_id"],
            },
        )
    common = subprocess.run(
        ["git", "-C", str(platform_cwd), "rev-parse", "--git-common-dir"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = platform_cwd / common_path
    if common_path.resolve(strict=True) != (repository / ".git").resolve(strict=True):
        raise ProtocolError(
            "worktree_platform_mismatch",
            "platform Git common directory does not equal the claimed repository",
            context={
                "repository": str(repository),
                "topic_id": claim["binding"]["topic_id"],
            },
        )
    _validate_worktree_binding_present(claim["binding"], allow_descendant=True)
    return {**_worktree_claim_report(claim_path, claim, data), "verified": True}


def verify_worktree_execution_claim_receipt(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
                label="worktree claim verification input",
            ),
            "worktree claim verification input",
        ),
        "worktree claim verification input",
    )
    _expect_keys(
        source,
        {"claim", "platform_cwd", "repository"},
        "worktree claim verification input",
    )
    repository = _expect_absolute_path(source["repository"], "repository").resolve(
        strict=True
    )
    receipt = _expect_object(source["claim"], "claim")
    _expect_keys(
        receipt,
        {"claim_id", "file_bytes", "file_sha256", "path", "version"},
        "claim",
    )
    verified = verify_worktree_execution_claim(
        _expect_absolute_path(receipt["path"], "claim.path"),
        expected_id=receipt["claim_id"],
        expected_version=receipt["version"],
        expected_bytes=receipt["file_bytes"],
        expected_sha256=receipt["file_sha256"],
        platform_cwd=_expect_absolute_path(source["platform_cwd"], "platform_cwd"),
    )
    if verified["repository"] != str(repository):
        raise ProtocolError(
            "worktree_platform_mismatch",
            "verified claim belongs to a different repository",
            context={"repository": str(repository)},
        )
    return verified


def _release_worktree_execution_claim(
    claim_path: Path,
    *,
    expected_id: str,
    expected_version: int,
    expected_bytes: int,
    expected_sha256: str,
    terminal_state: str,
) -> dict[str, Any]:
    if terminal_state not in {"archived", "cancelled"}:
        raise ProtocolError(
            "worktree_claim_conflict",
            "claim release requires an archived or cancelled terminal state",
        )
    claim_path = _expect_absolute_path(claim_path, "worktree execution claim path")
    claim, _ = _read_worktree_execution_claim(claim_path)
    repository = Path(claim["repository"])
    receipt = {
        "claim_id": expected_id,
        "file_bytes": expected_bytes,
        "file_sha256": expected_sha256,
        "path": str(claim_path),
        "version": expected_version,
    }
    _, claim, claim_data = _verify_worktree_claim_receipt(
        receipt, repository, allowed_states={"reserved", "active"}
    )
    _, directory_fd, git_fd = _open_worktree_execution_claim_directory(repository)
    guard_fd = _open_worktree_execution_claim_guard(git_fd)
    try:
        current, current_data = _read_worktree_execution_claim(claim_path)
        if current != claim or current_data != claim_data:
            raise ProtocolError(
                "worktree_claim_cas_mismatch",
                "worktree execution claim changed before release",
                context={
                    "repository": str(repository),
                    "topic_id": claim["binding"]["topic_id"],
                },
            )
        updated = {
            **current,
            "provisioning": {
                **(current.get("provisioning") or {}),
                "terminal_state": terminal_state,
            },
            "state": "released",
            "version": current["version"] + 1,
        }
        updated_data = _canonical_json_bytes(updated)
        _replace_private_file(
            directory_fd,
            name=claim_path.name,
            data=updated_data,
            max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
            label="worktree execution claim",
        )
        os.fsync(directory_fd)
        return {**_worktree_claim_report(claim_path, updated, updated_data), "released": True}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def administratively_release_worktree_execution_claim(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                input_path,
                max_bytes=MAX_WORKTREE_EXECUTION_CLAIM_BYTES,
                label="administrative worktree claim release input",
            ),
            "administrative worktree claim release input",
        ),
        "administrative worktree claim release input",
    )
    _expect_keys(
        source,
        {"action", "claim", "terminal_state"},
        "administrative worktree claim release input",
    )
    if source["action"] != "release-abandoned-claim":
        raise ProtocolError(
            "worktree_claim_conflict",
            "administrative claim release action is not explicit",
        )
    receipt = _expect_object(source["claim"], "claim")
    _expect_keys(
        receipt,
        {"claim_id", "file_bytes", "file_sha256", "path", "version"},
        "claim",
    )
    return _release_worktree_execution_claim(
        _expect_absolute_path(receipt["path"], "claim.path"),
        expected_id=receipt["claim_id"],
        expected_version=receipt["version"],
        expected_bytes=receipt["file_bytes"],
        expected_sha256=receipt["file_sha256"],
        terminal_state=_expect_nonempty_string(
            source["terminal_state"], "terminal_state", max_bytes=32
        ),
    )


def _target_publication_directory(repository: Path) -> Path:
    return repository / ".git" / TARGET_PUBLICATION_DIRECTORY


def _open_target_publication_directory(repository: Path) -> tuple[Path, int, int]:
    return _open_private_git_subdirectory(repository, TARGET_PUBLICATION_DIRECTORY)


def _open_target_publication_guard(git_fd: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            TARGET_PUBLICATION_GUARD_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=git_fd,
        )
    except FileExistsError:
        descriptor = os.open(
            TARGET_PUBLICATION_GUARD_FILENAME,
            os.O_RDWR | nofollow,
            dir_fd=git_fd,
        )
    status = os.fstat(descriptor)
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.getuid()
        or _mode_bits(status) != 0o600
    ):
        os.close(descriptor)
        raise ProtocolError("target publication guard is unsafe")
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def _normalize_implementation_paths(value: Any, label: str) -> list[str]:
    paths = _expect_string_list(value, label)
    normalized = [
        _normalize_protected_document_path(path, f"{label}[{index}]")
        for index, path in enumerate(paths)
    ]
    if not normalized or normalized != sorted(set(normalized)):
        raise ProtocolError(
            "candidate_review_invalid",
            f"{label} must be a non-empty sorted unique path list",
        )
    return normalized


def _path_is_in_implementation_scope(path: str, scopes: list[str]) -> bool:
    return any(path == scope or path.startswith(scope.rstrip("/") + "/") for scope in scopes)


def _normalize_verification_record(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(source, {"commands", "passed", "sha256"}, label)
    commands = _expect_string_list(source["commands"], f"{label}.commands")
    if not commands or commands != sorted(set(commands)) or source["passed"] is not True:
        raise ProtocolError(
            "candidate_review_invalid",
            "candidate verification must be a passing sorted unique command record",
        )
    return {
        "commands": commands,
        "passed": True,
        "sha256": _expect_sha256(source["sha256"], f"{label}.sha256"),
    }


def _normalize_candidate_review(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(source, {"decision", "sha256"}, label)
    if source["decision"] != "approved":
        raise ProtocolError("candidate_review_invalid", "candidate review is not approved")
    return {
        "decision": "approved",
        "sha256": _expect_sha256(source["sha256"], f"{label}.sha256"),
    }


def _target_publication_path(
    repository: Path, implementation_id: str, candidate_commit: str
) -> Path:
    identity = f"{implementation_id}\0{candidate_commit}"
    return _target_publication_directory(repository) / (
        hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".json"
    )


def _validate_target_publication(value: Any, label: str) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(
        source,
        {
            "acceptance",
            "binding",
            "integration",
            "repository",
            "review_id",
            "schema",
            "state",
            "version",
            "target_publication_version",
        },
        label,
    )
    if (
        source["schema"] != TARGET_PUBLICATION_SCHEMA
        or source["target_publication_version"] != TARGET_PUBLICATION_VERSION
    ):
        raise ProtocolError("target publication version is unsupported")
    binding = _expect_object(source["binding"], f"{label}.binding")
    _expect_keys(
        binding,
        {
            "candidate_commit",
            "execution_claim",
            "implementation_id",
            "implementation_paths",
            "review",
            "source_checkpoint",
            "target_branch",
            "verification",
            "worktree_path",
        },
        f"{label}.binding",
    )
    claim = _expect_object(binding["execution_claim"], f"{label}.binding.execution_claim")
    _expect_keys(
        claim,
        {"claim_id", "file_bytes", "file_sha256", "path", "version"},
        f"{label}.binding.execution_claim",
    )
    normalized_binding = {
        "candidate_commit": _expect_git_oid(
            binding["candidate_commit"], f"{label}.binding.candidate_commit"
        ),
        "execution_claim": {
            "claim_id": _expect_handoff_id(claim["claim_id"], "execution_claim.claim_id"),
            "file_bytes": _expect_int(
                claim["file_bytes"], "execution_claim.file_bytes", 1, MAX_WORKTREE_EXECUTION_CLAIM_BYTES
            ),
            "file_sha256": _expect_sha256(
                claim["file_sha256"], "execution_claim.file_sha256"
            ),
            "path": str(_expect_absolute_path(claim["path"], "execution_claim.path")),
            "version": _expect_int(
                claim["version"], "execution_claim.version", 1, 2**63 - 2
            ),
        },
        "implementation_id": _expect_nonempty_string(
            binding["implementation_id"], f"{label}.binding.implementation_id", max_bytes=256
        ),
        "implementation_paths": _normalize_implementation_paths(
            binding["implementation_paths"], f"{label}.binding.implementation_paths"
        ),
        "review": _normalize_candidate_review(binding["review"], f"{label}.binding.review"),
        "source_checkpoint": _validate_implementation_source_checkpoint(
            binding["source_checkpoint"], f"{label}.binding.source_checkpoint"
        ),
        "target_branch": _expect_nonempty_string(
            binding["target_branch"], f"{label}.binding.target_branch", max_bytes=1024
        ),
        "verification": _normalize_verification_record(
            binding["verification"], f"{label}.binding.verification"
        ),
        "worktree_path": str(
            _expect_absolute_path(binding["worktree_path"], f"{label}.binding.worktree_path")
        ),
    }
    repository = str(_expect_absolute_path(source["repository"], f"{label}.repository"))
    state = _expect_nonempty_string(source["state"], f"{label}.state", max_bytes=64)
    if state not in {"reviewed", "accepted", "conflict", "repair-required", "paused", "integrated"}:
        raise ProtocolError("target publication state is unsupported")
    acceptance = source["acceptance"]
    if acceptance is not None:
        acceptance = _expect_object(acceptance, f"{label}.acceptance")
        _expect_keys(acceptance, {"accepted", "sha256"}, f"{label}.acceptance")
        if acceptance["accepted"] is not True:
            raise ProtocolError("candidate acceptance is not affirmative")
        acceptance = {
            "accepted": True,
            "sha256": _expect_sha256(acceptance["sha256"], f"{label}.acceptance.sha256"),
        }
    integration = source["integration"]
    if integration is not None:
        integration = _expect_object(integration, f"{label}.integration")
    return {
        "acceptance": acceptance,
        "binding": normalized_binding,
        "integration": integration,
        "repository": repository,
        "review_id": _expect_handoff_id(source["review_id"], f"{label}.review_id"),
        "schema": TARGET_PUBLICATION_SCHEMA,
        "state": state,
        "version": _expect_int(source["version"], f"{label}.version", 1, 2**63 - 2),
        "target_publication_version": TARGET_PUBLICATION_VERSION,
    }


def _read_target_publication(path: Path) -> tuple[dict[str, Any], bytes]:
    data = _read_regular_file(
        path, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="target publication"
    )
    document = _validate_target_publication(
        _load_json_bytes(data, "target publication", require_canonical=True),
        "target publication",
    )
    return document, data


def _target_publication_report(
    path: Path, document: dict[str, Any], data: bytes
) -> dict[str, Any]:
    return {
        **document,
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "path": str(path),
    }


def _target_publication_receipt(value: Any, repository: Path) -> tuple[Path, dict[str, Any], bytes]:
    receipt = _expect_object(value, "publication")
    _expect_keys(
        receipt,
        {"file_bytes", "file_sha256", "path", "review_id", "version"},
        "publication",
    )
    path = _expect_absolute_path(receipt["path"], "publication.path")
    document, data = _read_target_publication(path)
    expected_path = _target_publication_path(
        repository,
        document["binding"]["implementation_id"],
        document["binding"]["candidate_commit"],
    )
    if (
        path != expected_path
        or document["repository"] != str(repository)
        or document["review_id"] != _expect_handoff_id(receipt["review_id"], "publication.review_id")
        or document["version"] != _expect_int(receipt["version"], "publication.version", 1, 2**63 - 2)
        or len(data) != _expect_int(receipt["file_bytes"], "publication.file_bytes", 1, MAX_TARGET_PUBLICATION_BYTES)
        or _sha256(data) != _expect_sha256(receipt["file_sha256"], "publication.file_sha256")
    ):
        raise ProtocolError(
            "candidate_acceptance_stale",
            "target publication receipt CAS no longer matches",
            context={"repository": str(repository)},
        )
    return path, document, data


def _active_source_for_candidate(
    repository: Path, implementation_id: str, checkpoint: dict[str, Any]
) -> dict[str, Any]:
    report = inspect_implementation_sources(repository)
    matches = [
        item
        for item in report["implementations"]
        if item["implementation_id"] == implementation_id
        and item["protection_state"] == "active"
    ]
    if len(matches) != 1 or matches[0]["source_checkpoint"] != checkpoint:
        raise ProtocolError(
            "implementation_source_cas_mismatch",
            "candidate does not match the active protected implementation source",
            context={"repository": str(repository)},
        )
    _verify_source_artifacts(repository, checkpoint, matches[0]["artifacts"])
    return matches[0]


def record_candidate_review(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(input_path, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="candidate review input"),
            "candidate review input",
        ),
        "candidate review input",
    )
    _expect_keys(
        source,
        {
            "candidate_commit",
            "execution_claim",
            "implementation_id",
            "implementation_paths",
            "repository",
            "review",
            "source_checkpoint",
            "target_branch",
            "verification",
            "worktree_path",
        },
        "candidate review input",
    )
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    worktree_path = _expect_absolute_path(source["worktree_path"], "worktree_path").resolve(strict=True)
    claim = verify_worktree_execution_claim_receipt_from_value(
        source["execution_claim"], repository, worktree_path
    )
    implementation_id = _expect_nonempty_string(source["implementation_id"], "implementation_id", max_bytes=256)
    checkpoint = _validate_implementation_source_checkpoint(source["source_checkpoint"], "source_checkpoint")
    if (
        claim["binding"]["implementation_id"] != implementation_id
        or claim["binding"]["source_checkpoint"] != checkpoint
        or claim["binding"]["worktree_path"] != str(worktree_path)
    ):
        raise ProtocolError("candidate_review_invalid", "candidate review does not equal the execution claim binding")
    candidate = _expect_git_oid(source["candidate_commit"], "candidate_commit")
    observed = _validate_worktree_binding_present(claim["binding"], allow_descendant=True)
    if observed.get("head") != candidate or _git_branch_oid(repository, claim["binding"]["implementation_branch"]) != candidate:
        raise ProtocolError("candidate_review_invalid", "candidate must equal the exact claimed worktree and branch HEAD")
    implementation_paths = _normalize_implementation_paths(source["implementation_paths"], "implementation_paths")
    changed = [
        line
        for line in _git_text(repository, ["diff", "--name-only", claim["binding"]["base_commit"], candidate]).splitlines()
        if line
    ]
    if any(not _path_is_in_implementation_scope(path, implementation_paths) for path in changed):
        raise ProtocolError("candidate_review_invalid", "candidate changes paths outside the declared implementation scope")
    protected = _active_source_for_candidate(repository, implementation_id, checkpoint)
    if set(changed) & {item["path"] for item in protected["artifacts"]}:
        raise ProtocolError("candidate_review_invalid", "candidate modifies its protected implementation source")
    target_branch = _expect_nonempty_string(source["target_branch"], "target_branch", max_bytes=1024)
    binding = {
        "candidate_commit": candidate,
        "execution_claim": {
            key: claim[key]
            for key in ("claim_id", "file_bytes", "file_sha256", "path", "version")
        },
        "implementation_id": implementation_id,
        "implementation_paths": implementation_paths,
        "review": _normalize_candidate_review(source["review"], "review"),
        "source_checkpoint": checkpoint,
        "target_branch": target_branch,
        "verification": _normalize_verification_record(source["verification"], "verification"),
        "worktree_path": str(worktree_path),
    }
    path = _target_publication_path(repository, implementation_id, candidate)
    directory, directory_fd, git_fd = _open_target_publication_directory(repository)
    guard_fd = _open_target_publication_guard(git_fd)
    try:
        if path.exists():
            current, current_data = _read_target_publication(path)
            if current["binding"] == binding:
                return {**_target_publication_report(path, current, current_data), "created": False}
            raise ProtocolError("candidate_review_invalid", "candidate identity was reviewed with different evidence")
        document = {
            "acceptance": None,
            "binding": binding,
            "integration": None,
            "repository": str(repository),
            "review_id": secrets.token_hex(16),
            "schema": TARGET_PUBLICATION_SCHEMA,
            "state": "reviewed",
            "version": 1,
            "target_publication_version": TARGET_PUBLICATION_VERSION,
        }
        data = _canonical_json_bytes(document)
        _publish_group(directory_fd, [(path.name, data)])
        os.fsync(directory_fd)
        return {**_target_publication_report(path, document, data), "created": True}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def verify_worktree_execution_claim_receipt_from_value(
    receipt_value: Any, repository: Path, platform_cwd: Path
) -> dict[str, Any]:
    receipt = _expect_object(receipt_value, "execution_claim")
    _expect_keys(
        receipt,
        {"claim_id", "file_bytes", "file_sha256", "path", "version"},
        "execution_claim",
    )
    return verify_worktree_execution_claim(
        _expect_absolute_path(receipt["path"], "execution_claim.path"),
        expected_id=receipt["claim_id"],
        expected_version=receipt["version"],
        expected_bytes=receipt["file_bytes"],
        expected_sha256=receipt["file_sha256"],
        platform_cwd=platform_cwd,
    )


def accept_implementation_candidate(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="candidate acceptance input"), "candidate acceptance input"),
        "candidate acceptance input",
    )
    _expect_keys(source, {"acceptance", "publication", "repository"}, "candidate acceptance input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    path, document, data = _target_publication_receipt(source["publication"], repository)
    acceptance = _expect_object(source["acceptance"], "acceptance")
    _expect_keys(acceptance, {"accepted", "sha256"}, "acceptance")
    normalized_acceptance = {
        "accepted": acceptance["accepted"] is True,
        "sha256": _expect_sha256(acceptance["sha256"], "acceptance.sha256"),
    }
    if not normalized_acceptance["accepted"] or document["state"] != "reviewed":
        raise ProtocolError("candidate_acceptance_stale", "only the exact reviewed candidate may be accepted")
    _, directory_fd, git_fd = _open_target_publication_directory(repository)
    guard_fd = _open_target_publication_guard(git_fd)
    try:
        current, current_data = _read_target_publication(path)
        if current != document or current_data != data:
            raise ProtocolError("candidate_acceptance_stale", "candidate review changed before acceptance")
        updated = {**current, "acceptance": normalized_acceptance, "state": "accepted", "version": current["version"] + 1}
        updated_data = _canonical_json_bytes(updated)
        _replace_private_file(directory_fd, name=path.name, data=updated_data, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="target publication")
        os.fsync(directory_fd)
        return {**_target_publication_report(path, updated, updated_data), "accepted": True}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def _git_operation_markers(repository: Path) -> list[str]:
    git_dir = repository / ".git"
    names = ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply", "sequencer"]
    return [name for name in names if os.path.lexists(git_dir / name)]


def _publication_checkout_snapshot(repository: Path, target_branch: str) -> dict[str, Any]:
    branch = _git_text(repository, ["symbolic-ref", "--quiet", "--short", "HEAD"]).strip()
    head = _expect_git_oid(_git_text(repository, ["rev-parse", "HEAD"]).strip(), "publication HEAD")
    status = _git_text(repository, ["status", "--porcelain=v1", "--untracked-files=all"])
    markers = _git_operation_markers(repository)
    return {"branch": branch, "head": head, "markers": markers, "status": status, "target_matches": branch == target_branch}


def _prove_publication_checkout(
    repository: Path, target_branch: str, expected_head: str
) -> dict[str, Any]:
    snapshot = _publication_checkout_snapshot(repository, target_branch)
    if not snapshot["target_matches"] or snapshot["status"] or snapshot["markers"]:
        raise ProtocolError(
            "publication_checkout_invalid",
            "primary checkout must be clean, on the target branch and outside every Git operation",
            context={"current": snapshot, "repository": str(repository)},
        )
    if snapshot["head"] != expected_head or _git_branch_oid(repository, target_branch) != expected_head:
        raise ProtocolError(
            "publication_head_mismatch",
            "target branch HEAD does not equal the expected publication CAS",
            context={"current": snapshot, "repository": str(repository)},
        )
    return snapshot


def _replace_target_publication(
    directory_fd: int, path: Path, document: dict[str, Any]
) -> dict[str, Any]:
    data = _canonical_json_bytes(document)
    _replace_private_file(directory_fd, name=path.name, data=data, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="target publication")
    os.fsync(directory_fd)
    return _target_publication_report(path, document, data)


def _restore_failed_publication(repository: Path, target_branch: str, expected_head: str) -> dict[str, Any]:
    if (repository / ".git" / "MERGE_HEAD").exists():
        subprocess.run(["git", "-C", str(repository), "merge", "--abort"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    current = _publication_checkout_snapshot(repository, target_branch)
    if current["head"] != expected_head and current["target_matches"]:
        subprocess.run(["git", "-C", str(repository), "reset", "--merge", expected_head], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return _prove_publication_checkout(repository, target_branch, expected_head)
    except ProtocolError as error:
        raise ProtocolError(
            "publication_restore_failed",
            "primary checkout could not be proven identical to the pre-publication state",
            cause=str(error),
            context={"current": _publication_checkout_snapshot(repository, target_branch), "repository": str(repository)},
        ) from error


def _validate_merge_commit(repository: Path, expected_head: str, candidate: str, merge_commit: str, paths: list[str]) -> dict[str, Any]:
    parents = _git_text(repository, ["rev-list", "--parents", "-n", "1", merge_commit]).strip().split()
    if parents != [merge_commit, expected_head, candidate]:
        raise ProtocolError("publication_outcome_ambiguous", "merge commit parents do not equal expected target and accepted candidate")
    if _git_branch_oid(repository, _git_text(repository, ["symbolic-ref", "--short", "HEAD"]).strip()) != merge_commit:
        raise ProtocolError("publication_outcome_ambiguous", "target ref does not point at the merge commit")
    subprocess.run(["git", "-C", str(repository), "merge-base", "--is-ancestor", candidate, merge_commit], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    changed = [line for line in _git_text(repository, ["diff", "--name-only", expected_head, merge_commit]).splitlines() if line]
    if any(not _path_is_in_implementation_scope(path, paths) for path in changed):
        raise ProtocolError("publication_outcome_ambiguous", "merge commit changes paths outside the accepted implementation scope")
    tree = _expect_git_oid(_git_text(repository, ["rev-parse", f"{merge_commit}^{{tree}}"]).strip(), "merge tree")
    return {"candidate_commit": candidate, "changed_paths": changed, "expected_target_head": expected_head, "merge_commit": merge_commit, "parents": parents[1:], "target_head_after": merge_commit, "tree": tree}


def publish_accepted_candidate(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="candidate publication input"), "candidate publication input"),
        "candidate publication input",
    )
    _expect_keys(source, {"expected_target_head", "publication", "repository"}, "candidate publication input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    expected_head = _expect_git_oid(source["expected_target_head"], "expected_target_head")
    path, document, data = _target_publication_receipt(source["publication"], repository)
    if document["state"] != "accepted" or document["acceptance"] is None:
        raise ProtocolError("candidate_acceptance_stale", "candidate is not in the exact accepted state")
    binding = document["binding"]
    _, directory_fd, git_fd = _open_target_publication_directory(repository)
    guard_fd = _open_target_publication_guard(git_fd)
    try:
        current, current_data = _read_target_publication(path)
        if current != document or current_data != data:
            raise ProtocolError("candidate_acceptance_stale", "candidate acceptance changed before publication")
        _active_source_for_candidate(repository, binding["implementation_id"], binding["source_checkpoint"])
        _prove_publication_checkout(repository, binding["target_branch"], expected_head)
        if _git_branch_oid(repository, binding["target_branch"]) != expected_head:
            raise ProtocolError("publication_head_mismatch", "target ref moved immediately before merge", context={"repository": str(repository)})
        merge = subprocess.run(
            ["git", "-C", str(repository), "-c", "user.name=Workflow Pipeline", "-c", "user.email=workflow-pipeline@example.invalid", "merge", "--no-ff", "--no-edit", binding["candidate_commit"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if merge.returncode != 0:
            restored = _restore_failed_publication(repository, binding["target_branch"], expected_head)
            updated = {**current, "integration": {"classification": "unclassified", "expected_target_head": expected_head, "stderr": merge.stderr[:2048], "restored": restored}, "state": "conflict", "version": current["version"] + 1}
            report = _replace_target_publication(directory_fd, path, updated)
            raise ProtocolError(
                "publication_conflict",
                "merge failed; primary checkout was restored and repair must happen in the original worktree",
                context={"current": report, "repository": str(repository)},
            )
        merge_commit = _expect_git_oid(_git_text(repository, ["rev-parse", "HEAD"]).strip(), "merge commit")
        try:
            if os.environ.get("CODEX_SUPERVISION_TEST_FAILPOINT") == "publication-after-merge":
                raise ProtocolError("outcome_unknown", "merge completed before durable publication recording", retryable=True)
            integration = _validate_merge_commit(repository, expected_head, binding["candidate_commit"], merge_commit, binding["implementation_paths"])
        except ProtocolError as error:
            if error.code == "outcome_unknown":
                raise
            _restore_failed_publication(repository, binding["target_branch"], expected_head)
            raise
        updated = {**current, "integration": integration, "state": "integrated", "version": current["version"] + 1}
        return {**_replace_target_publication(directory_fd, path, updated), "integrated": True}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def classify_publication_conflict(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="publication conflict classification input"), "publication conflict classification input"),
        "publication conflict classification input",
    )
    _expect_keys(source, {"classification", "preserves_acceptance", "preserves_business_behavior", "preserves_scope", "publication", "repository", "summary"}, "publication conflict classification input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    path, document, data = _target_publication_receipt(source["publication"], repository)
    if document["state"] != "conflict":
        raise ProtocolError("publication_conflict", "only a recorded merge conflict can be classified")
    classification = _expect_nonempty_string(source["classification"], "classification", max_bytes=64)
    if classification not in {"textual-structural", "local-semantic-adaptation", "material-semantic-conflict"}:
        raise ProtocolError("publication_conflict", "conflict classification is unsupported")
    preserves = all(source[key] is True for key in ("preserves_acceptance", "preserves_business_behavior", "preserves_scope"))
    state = "repair-required" if classification != "material-semantic-conflict" and preserves else "paused"
    integration = {**(document["integration"] or {}), "classification": classification, "preserves_acceptance": source["preserves_acceptance"] is True, "preserves_business_behavior": source["preserves_business_behavior"] is True, "preserves_scope": source["preserves_scope"] is True, "summary": _expect_nonempty_string(source["summary"], "summary", max_bytes=4096), "user_decision_required": state == "paused"}
    _, directory_fd, git_fd = _open_target_publication_directory(repository)
    guard_fd = _open_target_publication_guard(git_fd)
    try:
        current, current_data = _read_target_publication(path)
        if current != document or current_data != data:
            raise ProtocolError("candidate_acceptance_stale", "conflict record changed before classification")
        updated = {**current, "integration": integration, "state": state, "version": current["version"] + 1}
        return {**_replace_target_publication(directory_fd, path, updated), "user_decision_required": state == "paused"}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def reconcile_candidate_publication(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_TARGET_PUBLICATION_BYTES, label="candidate publication reconciliation input"), "candidate publication reconciliation input"),
        "candidate publication reconciliation input",
    )
    _expect_keys(source, {"expected_target_head", "publication", "repository"}, "candidate publication reconciliation input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    expected = _expect_git_oid(source["expected_target_head"], "expected_target_head")
    path, document, data = _target_publication_receipt(source["publication"], repository)
    if document["state"] == "integrated":
        return {**_target_publication_report(path, document, data), "classification": "exact-adoption", "reconciled": True}
    if document["state"] != "accepted":
        raise ProtocolError("publication_outcome_ambiguous", "publication record is not accepted or integrated")
    binding = document["binding"]
    current = _git_branch_oid(repository, binding["target_branch"])
    if current == expected:
        _prove_publication_checkout(repository, binding["target_branch"], expected)
        return {**_target_publication_report(path, document, data), "classification": "no-effect", "reconciled": True}
    candidates = []
    if current is not None:
        for commit in _git_text(repository, ["rev-list", "--first-parent", current, f"^{expected}"]).splitlines():
            parents = _git_text(repository, ["rev-list", "--parents", "-n", "1", commit]).strip().split()
            if parents == [commit, expected, binding["candidate_commit"]]:
                candidates.append(commit)
    if len(candidates) != 1:
        raise ProtocolError("publication_outcome_ambiguous", "publication outcome is neither no-effect nor one unique matching merge", context={"current": {"matching_merges": candidates, "target_head": current}, "repository": str(repository)})
    integration = _validate_merge_commit(repository, expected, binding["candidate_commit"], candidates[0], binding["implementation_paths"])
    integration["target_head_after"] = current
    _, directory_fd, git_fd = _open_target_publication_directory(repository)
    guard_fd = _open_target_publication_guard(git_fd)
    try:
        latest, latest_data = _read_target_publication(path)
        if latest != document or latest_data != data:
            raise ProtocolError("candidate_acceptance_stale", "publication record changed during reconciliation")
        updated = {**latest, "integration": integration, "state": "integrated", "version": latest["version"] + 1}
        return {**_replace_target_publication(directory_fd, path, updated), "classification": "exact-adoption", "reconciled": True}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def _implementation_source_protection_path(repository: Path) -> Path:
    return (
        Path(repository)
        / ".git"
        / IMPLEMENTATION_SOURCE_PROTECTION_FILENAME
    )


def _normalize_protected_document_path(value: Any, label: str) -> str:
    text = _expect_nonempty_string(value, label, max_bytes=8_192)
    path = Path(text)
    if (
        path.is_absolute()
        or path == Path(".")
        or ".." in path.parts
        or text != path.as_posix()
    ):
        raise ProtocolError(
            "implementation_source_protected",
            f"{label} must be one normalized repository-relative path",
        )
    return text


def _normalize_document_paths(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ProtocolError(
            "implementation_source_protected", f"{label} must be a list"
        )
    paths = [
        _normalize_protected_document_path(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    ]
    if paths != sorted(set(paths)):
        raise ProtocolError(
            "implementation_source_protected",
            f"{label} must contain sorted unique paths",
        )
    return paths


def _normalize_source_artifacts(value: Any, label: str) -> list[dict[str, str]]:
    artifacts = _expect_list(value, label)
    if not artifacts:
        raise ProtocolError(
            "implementation_source_protected",
            f"{label} must contain at least one authoritative artifact",
        )
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(artifacts):
        artifact_label = f"{label}[{index}]"
        source = _expect_object(item, artifact_label)
        _expect_keys(source, {"blob_id", "path", "sha256"}, artifact_label)
        normalized.append(
            {
                "blob_id": _expect_git_oid(
                    source["blob_id"], f"{artifact_label}.blob_id"
                ),
                "path": _normalize_protected_document_path(
                    source["path"], f"{artifact_label}.path"
                ),
                "sha256": _expect_sha256(
                    source["sha256"], f"{artifact_label}.sha256"
                ),
            }
        )
    paths = [item["path"] for item in normalized]
    if paths != sorted(set(paths)):
        raise ProtocolError(
            "implementation_source_protected",
            f"{label} must be sorted by unique path",
        )
    return normalized


def _validate_source_protection_document(
    value: Any, label: str, *, repository: Path
) -> dict[str, Any]:
    source = _expect_object(value, label)
    _expect_keys(
        source,
        {
            "implementation_source_protection_version",
            "implementations",
            "repository",
            "schema",
            "version",
        },
        label,
    )
    if (
        source["implementation_source_protection_version"]
        != IMPLEMENTATION_SOURCE_PROTECTION_VERSION
        or source["schema"] != IMPLEMENTATION_SOURCE_PROTECTION_SCHEMA
    ):
        raise ProtocolError(
            "implementation_source_protected",
            f"{label} version is unsupported",
        )
    observed_repository = str(
        _expect_absolute_path(source["repository"], f"{label}.repository")
    )
    if observed_repository != str(repository):
        raise ProtocolError(
            "implementation_source_protected",
            f"{label}.repository does not match {repository}",
        )
    implementations_value = _expect_list(
        source["implementations"], f"{label}.implementations"
    )
    implementations: list[dict[str, Any]] = []
    for index, item in enumerate(implementations_value):
        item_label = f"{label}.implementations[{index}]"
        implementation = _expect_object(item, item_label)
        _expect_keys(
            implementation,
            {
                "artifacts",
                "implementation_id",
                "protection_state",
                "source_checkpoint",
                "terminal_state",
            },
            item_label,
        )
        protection_state = _expect_nonempty_string(
            implementation["protection_state"],
            f"{item_label}.protection_state",
            max_bytes=64,
        )
        if protection_state not in {"active", "released"}:
            raise ProtocolError(
                "implementation_source_protected",
                f"{item_label}.protection_state is unsupported",
            )
        terminal_state = implementation["terminal_state"]
        if terminal_state is not None:
            terminal_state = _expect_nonempty_string(
                terminal_state, f"{item_label}.terminal_state", max_bytes=64
            )
        if (protection_state, terminal_state) not in {
            ("active", None),
            ("released", "archived"),
            ("released", "cancelled"),
        }:
            raise ProtocolError(
                "implementation_source_protected",
                f"{item_label} protection and terminal states conflict",
            )
        implementations.append(
            {
                "artifacts": _normalize_source_artifacts(
                    implementation["artifacts"], f"{item_label}.artifacts"
                ),
                "implementation_id": _expect_nonempty_string(
                    implementation["implementation_id"],
                    f"{item_label}.implementation_id",
                    max_bytes=256,
                ),
                "protection_state": protection_state,
                "source_checkpoint": _validate_implementation_source_checkpoint(
                    implementation["source_checkpoint"],
                    f"{item_label}.source_checkpoint",
                ),
                "terminal_state": terminal_state,
            }
        )
    identities = [item["implementation_id"] for item in implementations]
    if identities != sorted(set(identities)):
        raise ProtocolError(
            "implementation_source_protected",
            f"{label}.implementations must be sorted by unique implementation_id",
        )
    return {
        "implementation_source_protection_version": (
            IMPLEMENTATION_SOURCE_PROTECTION_VERSION
        ),
        "implementations": implementations,
        "repository": observed_repository,
        "schema": IMPLEMENTATION_SOURCE_PROTECTION_SCHEMA,
        "version": _expect_int(source["version"], f"{label}.version", 0, 2**63 - 2),
    }


def _empty_source_protection_document(repository: Path) -> dict[str, Any]:
    return {
        "implementation_source_protection_version": (
            IMPLEMENTATION_SOURCE_PROTECTION_VERSION
        ),
        "implementations": [],
        "repository": str(repository),
        "schema": IMPLEMENTATION_SOURCE_PROTECTION_SCHEMA,
        "version": 0,
    }


def _open_source_protection_guard(git_fd: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        guard_fd = os.open(
            IMPLEMENTATION_SOURCE_PROTECTION_GUARD_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow,
            0o600,
            dir_fd=git_fd,
        )
    except FileExistsError:
        guard_fd = os.open(
            IMPLEMENTATION_SOURCE_PROTECTION_GUARD_FILENAME,
            os.O_RDWR | nofollow,
            dir_fd=git_fd,
        )
    status = os.fstat(guard_fd)
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.getuid()
        or status.st_nlink != 1
        or _mode_bits(status) != 0o600
    ):
        os.close(guard_fd)
        raise ProtocolError(
            "implementation_source_protected",
            "implementation source protection guard is unsafe",
        )
    fcntl.flock(guard_fd, fcntl.LOCK_EX)
    return guard_fd


def _read_source_protection_locked(
    git_directory: Path, git_fd: int, repository: Path
) -> tuple[dict[str, Any], bytes | None]:
    try:
        os.stat(
            IMPLEMENTATION_SOURCE_PROTECTION_FILENAME,
            dir_fd=git_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return _empty_source_protection_document(repository), None
    data, _ = _read_runtime_file(
        git_fd,
        IMPLEMENTATION_SOURCE_PROTECTION_FILENAME,
        max_bytes=MAX_IMPLEMENTATION_SOURCE_PROTECTION_BYTES,
        label="implementation source protection",
    )
    document = _validate_source_protection_document(
        _load_json_bytes(
            data, "implementation source protection", require_canonical=True
        ),
        "implementation source protection",
        repository=repository,
    )
    return document, data


def _write_source_protection_locked(
    git_fd: int, *, document: dict[str, Any], existed: bool
) -> bytes:
    data = _canonical_json_bytes(document)
    if not 1 <= len(data) <= MAX_IMPLEMENTATION_SOURCE_PROTECTION_BYTES:
        raise ProtocolError(
            "implementation_source_protected",
            "implementation source protection record exceeds its size limit",
        )
    if existed:
        _replace_private_file(
            git_fd,
            name=IMPLEMENTATION_SOURCE_PROTECTION_FILENAME,
            data=data,
            max_bytes=MAX_IMPLEMENTATION_SOURCE_PROTECTION_BYTES,
            label="implementation source protection",
        )
    else:
        _publish_group(
            git_fd, [(IMPLEMENTATION_SOURCE_PROTECTION_FILENAME, data)]
        )
    os.fsync(git_fd)
    return data


def _source_protection_report(
    repository: Path, document: dict[str, Any], data: bytes | None
) -> dict[str, Any]:
    active = [
        item
        for item in document["implementations"]
        if item["protection_state"] == "active"
    ]
    protected_paths = sorted(
        {
            artifact["path"]
            for item in active
            for artifact in item["artifacts"]
        }
    )
    return {
        **document,
        "active_implementation_ids": [item["implementation_id"] for item in active],
        "file_bytes": 0 if data is None else len(data),
        "file_sha256": None if data is None else _sha256(data),
        "path": str(_implementation_source_protection_path(repository)),
        "protected_paths": protected_paths,
        "state": "protected" if active else "available",
    }


def inspect_implementation_sources(repository: Path) -> dict[str, Any]:
    repository = _expect_absolute_path(repository, "repository")
    git_directory, git_fd = _open_repository_git_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_source_protection_guard(git_fd)
        document, data = _read_source_protection_locked(
            git_directory, git_fd, repository
        )
        return _source_protection_report(repository, document, data)
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def _git_blob_at(repository: Path, commit_id: str, path: str) -> tuple[str, bytes]:
    listed = subprocess.run(
        ["git", "-C", str(repository), "ls-tree", "-z", "--full-tree", commit_id, "--", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if listed.returncode != 0:
        raise ProtocolError(
            "implementation_source_drift",
            f"cannot inspect source artifact {path!r} at {commit_id}",
            cause=listed.stderr.decode("utf-8", errors="replace").strip()[:1_024],
            context={"repository": str(repository)},
        )
    entries = [item for item in listed.stdout.split(b"\0") if item]
    if len(entries) != 1:
        raise ProtocolError(
            "implementation_source_drift",
            f"source artifact {path!r} is not one exact Git tree entry",
            context={"repository": str(repository)},
        )
    try:
        metadata, observed_path = entries[0].split(b"\t", 1)
        _, object_type, blob_bytes = metadata.split(b" ", 2)
        blob_id = blob_bytes.decode("ascii")
        decoded_path = observed_path.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ProtocolError(
            "implementation_source_drift",
            f"source artifact tree entry is malformed for {path!r}",
        ) from error
    if object_type != b"blob" or decoded_path != path:
        raise ProtocolError(
            "implementation_source_drift",
            f"source artifact {path!r} is not the expected blob",
            context={"repository": str(repository)},
        )
    loaded = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "blob", blob_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if loaded.returncode != 0:
        raise ProtocolError(
            "implementation_source_drift",
            f"cannot load source artifact blob for {path!r}",
            cause=loaded.stderr.decode("utf-8", errors="replace").strip()[:1_024],
            context={"repository": str(repository)},
        )
    return blob_id, loaded.stdout


def _verify_source_artifacts(
    repository: Path, checkpoint: dict[str, Any], artifacts: list[dict[str, str]]
) -> None:
    resolved = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{checkpoint['commit_id']}^{{commit}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if resolved.returncode != 0 or resolved.stdout.strip() != checkpoint["commit_id"]:
        raise ProtocolError(
            "implementation_source_drift",
            "implementation source checkpoint commit is unavailable or changed",
            cause=resolved.stderr.strip()[:1_024],
            context={"repository": str(repository)},
        )
    for artifact in artifacts:
        blob_id, data = _git_blob_at(
            repository, checkpoint["commit_id"], artifact["path"]
        )
        if blob_id != artifact["blob_id"] or _sha256(data) != artifact["sha256"]:
            raise ProtocolError(
                "implementation_source_drift",
                f"implementation source artifact identity mismatch: {artifact['path']}",
                context={"repository": str(repository)},
            )


def protect_implementation_source(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                Path(input_path),
                max_bytes=MAX_IMPLEMENTATION_SOURCE_PROTECTION_BYTES,
                label="implementation source protection input",
            ),
            "implementation source protection input",
        ),
        "implementation source protection input",
    )
    _expect_keys(
        source,
        {"artifacts", "implementation_id", "repository", "source_checkpoint"},
        "implementation source protection input",
    )
    repository = _expect_absolute_path(source["repository"], "repository")
    implementation_id = _expect_nonempty_string(
        source["implementation_id"], "implementation_id", max_bytes=256
    )
    checkpoint = _validate_implementation_source_checkpoint(
        source["source_checkpoint"], "source_checkpoint"
    )
    artifacts = _normalize_source_artifacts(source["artifacts"], "artifacts")
    _verify_source_artifacts(repository, checkpoint, artifacts)
    git_directory, git_fd = _open_repository_git_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_source_protection_guard(git_fd)
        current, current_data = _read_source_protection_locked(
            git_directory, git_fd, repository
        )
        expected_entry = {
            "artifacts": artifacts,
            "implementation_id": implementation_id,
            "protection_state": "active",
            "source_checkpoint": checkpoint,
            "terminal_state": None,
        }
        existing = next(
            (
                item
                for item in current["implementations"]
                if item["implementation_id"] == implementation_id
            ),
            None,
        )
        if existing is not None:
            if existing != expected_entry:
                raise ProtocolError(
                    "implementation_source_cas_mismatch",
                    "implementation source identity was already used for different authority",
                    context={"repository": str(repository)},
                )
            return {
                **_source_protection_report(repository, current, current_data),
                "idempotent_replay": True,
                "protected": True,
                "source_protection": existing,
                "verified": True,
            }
        updated = {
            **current,
            "implementations": sorted(
                current["implementations"] + [expected_entry],
                key=lambda item: item["implementation_id"],
            ),
            "version": current["version"] + 1,
        }
        updated_data = _write_source_protection_locked(
            git_fd, document=updated, existed=current_data is not None
        )
        return {
            **_source_protection_report(repository, updated, updated_data),
            "idempotent_replay": False,
            "protected": True,
            "source_protection": expected_entry,
            "verified": True,
        }
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def _verify_active_source_protection_binding(
    repository: Path,
    implementation_id: str,
    *,
    expected_checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = inspect_implementation_sources(repository)
    matches = [
        item
        for item in report["implementations"]
        if item["implementation_id"] == implementation_id
        and item["protection_state"] == "active"
    ]
    if len(matches) != 1:
        raise ProtocolError(
            "implementation_source_cas_mismatch",
            "implementation source protection is not exactly active",
            context={"repository": str(repository)},
        )
    if (
        expected_checkpoint is not None
        and matches[0]["source_checkpoint"] != expected_checkpoint
    ):
        raise ProtocolError(
            "implementation_source_cas_mismatch",
            "worktree claim source checkpoint does not match active protection",
            context={"repository": str(repository)},
        )
    head = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", "HEAD^{commit}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if head.returncode != 0:
        raise ProtocolError(
            "implementation_source_drift",
            "current checkout HEAD is unavailable for source verification",
            cause=head.stderr.strip()[:1_024],
            context={"repository": str(repository)},
        )
    drifted_paths: list[str] = []
    for artifact in matches[0]["artifacts"]:
        try:
            current_blob, committed_bytes = _git_blob_at(
                repository, head.stdout.strip(), artifact["path"]
            )
            working_bytes = _read_regular_file(
                repository / artifact["path"],
                max_bytes=MAX_INPUT_BYTES,
                label=f"active implementation source {artifact['path']}",
            )
        except ProtocolError:
            drifted_paths.append(artifact["path"])
            continue
        if (
            current_blob != artifact["blob_id"]
            or _sha256(committed_bytes) != artifact["sha256"]
            or _sha256(working_bytes) != artifact["sha256"]
        ):
            drifted_paths.append(artifact["path"])
    if drifted_paths:
        raise ProtocolError(
            "implementation_source_drift",
            "active implementation source differs from its frozen artifact set",
            context={
                "repository": str(repository),
                "current": {"drifted_paths": sorted(drifted_paths)},
            },
        )
    return {
        "implementation_id": implementation_id,
        "ok": True,
        "protected_paths": [
            artifact["path"] for artifact in matches[0]["artifacts"]
        ],
        "repository": str(repository),
        "source_checkpoint": matches[0]["source_checkpoint"],
        "state": "valid",
        "verified": True,
        "version": report["version"],
    }


def verify_implementation_source(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                Path(input_path),
                max_bytes=MAX_IMPLEMENTATION_SOURCE_PROTECTION_BYTES,
                label="implementation source verification input",
            ),
            "implementation source verification input",
        ),
        "implementation source verification input",
    )
    _expect_keys(
        source,
        {"implementation_id", "repository"},
        "implementation source verification input",
    )
    repository = _expect_absolute_path(source["repository"], "repository")
    implementation_id = _expect_nonempty_string(
        source["implementation_id"], "implementation_id", max_bytes=256
    )
    return _verify_active_source_protection_binding(
        repository, implementation_id
    )


def release_implementation_source(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(
            _read_regular_file(
                Path(input_path),
                max_bytes=MAX_IMPLEMENTATION_SOURCE_PROTECTION_BYTES,
                label="implementation source release input",
            ),
            "implementation source release input",
        ),
        "implementation source release input",
    )
    _expect_keys(
        source,
        {"expected_version", "implementation_id", "repository", "terminal_state"},
        "implementation source release input",
    )
    repository = _expect_absolute_path(source["repository"], "repository")
    expected_version = _expect_int(
        source["expected_version"], "expected_version", 1, 2**63 - 2
    )
    implementation_id = _expect_nonempty_string(
        source["implementation_id"], "implementation_id", max_bytes=256
    )
    terminal_state = _expect_nonempty_string(
        source["terminal_state"], "terminal_state", max_bytes=64
    )
    if terminal_state not in {"archived", "cancelled"}:
        raise ProtocolError(
            "implementation_source_cas_mismatch",
            "source protection release requires archived or cancelled terminal state",
        )
    git_directory, git_fd = _open_repository_git_directory(repository)
    guard_fd = -1
    try:
        guard_fd = _open_source_protection_guard(git_fd)
        current, current_data = _read_source_protection_locked(
            git_directory, git_fd, repository
        )
        if current["version"] != expected_version:
            raise ProtocolError(
                "implementation_source_cas_mismatch",
                "implementation source protection version changed before release",
                context={"repository": str(repository)},
            )
        matches = [
            item
            for item in current["implementations"]
            if item["implementation_id"] == implementation_id
        ]
        if len(matches) != 1 or matches[0]["protection_state"] != "active":
            raise ProtocolError(
                "implementation_source_cas_mismatch",
                "implementation source is not exactly active for release",
                context={"repository": str(repository)},
            )
        released_entry = {
            **matches[0],
            "protection_state": "released",
            "terminal_state": terminal_state,
        }
        updated = {
            **current,
            "implementations": [
                released_entry
                if item["implementation_id"] == implementation_id
                else item
                for item in current["implementations"]
            ],
            "version": current["version"] + 1,
        }
        updated_data = _write_source_protection_locked(
            git_fd, document=updated, existed=current_data is not None
        )
        return {
            **_source_protection_report(repository, updated, updated_data),
            "released": True,
            "released_implementation_id": implementation_id,
            "terminal_state": terminal_state,
        }
    finally:
        if guard_fd >= 0:
            os.close(guard_fd)
        os.close(git_fd)


def _document_source_protection_blockers(
    repository: Path, paths: list[str]
) -> dict[str, list[str]]:
    report = inspect_implementation_sources(repository)
    requested = set(paths)
    blockers: dict[str, list[str]] = {}
    for item in report["implementations"]:
        if item["protection_state"] != "active":
            continue
        overlap = sorted(
            requested & {artifact["path"] for artifact in item["artifacts"]}
        )
        for path in overlap:
            blockers.setdefault(path, []).append(item["implementation_id"])
    return {path: sorted(identities) for path, identities in sorted(blockers.items())}


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
            "implementation_id",
            "lease_id",
            "owner_host_id",
            "owner_task_id",
            "paths",
            "purpose",
            "stage",
        },
        label,
    )
    common = LeaseHolderPolicy(
        stages=DOCUMENT_LEASE_STAGES,
        purposes=DOCUMENT_LEASE_PURPOSES,
        protocol_error=ProtocolError,
        expect_object=_expect_object,
        expect_keys=_expect_keys,
        expect_int=_expect_int,
        expect_string=_expect_nonempty_string,
        expect_lease_id=_expect_handoff_id,
        verbose_vocab_errors=True,
        vocabulary_order=("purpose", "stage"),
    ).validate(
        {
            key: holder[key]
            for key in {
                "acquired_at_epoch",
                "expires_at_epoch",
                "lease_id",
                "owner_host_id",
                "owner_task_id",
                "purpose",
                "stage",
            }
        },
        label,
    )
    implementation_id = holder["implementation_id"]
    if implementation_id is not None:
        implementation_id = _expect_nonempty_string(
            implementation_id, f"{label}.implementation_id", max_bytes=256
        )
    return {
        **common,
        "implementation_id": implementation_id,
        "paths": _normalize_document_paths(holder["paths"], f"{label}.paths"),
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
    implementation_id: str | None,
    paths: list[str],
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
                "implementation_id": implementation_id,
                "lease_id": lease_id,
                "owner_host_id": owner_host_id,
                "owner_task_id": owner_task_id,
                "paths": paths,
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
            "implementation_id",
            "owner_host_id",
            "owner_task_id",
            "paths",
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
    implementation_id = source["implementation_id"]
    if implementation_id is not None:
        implementation_id = _expect_nonempty_string(
            implementation_id,
            "document lease input.implementation_id",
            max_bytes=256,
        )
    paths = _normalize_document_paths(
        source["paths"], "document lease input.paths"
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
    if purpose == "document-write" and not paths:
        raise ProtocolError(
            "implementation_source_protected",
            "document-write lease requires at least one exact target path",
            context={"repository": str(repository)},
        )
    if (repository / ".git").is_dir() and paths:
        blockers = _document_source_protection_blockers(repository, paths)
        if blockers:
            allowed_closure = (
                purpose == "document-write"
                and stage == "change-closure"
                and implementation_id is not None
                and all(
                    identities == [implementation_id]
                    for identities in blockers.values()
                )
            )
            if not allowed_closure:
                raise ProtocolError(
                    "implementation_source_protected",
                    "document lease targets an active implementation source",
                    context={
                        "repository": str(repository),
                        "current": {"blocked_paths": blockers},
                    },
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
            implementation_id=implementation_id,
            paths=paths,
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
    return LeaseHolderPolicy(
        stages=REPOSITORY_COORDINATION_LEASE_STAGES,
        purposes=REPOSITORY_COORDINATION_LEASE_PURPOSES,
        protocol_error=ProtocolError,
        expect_object=_expect_object,
        expect_keys=_expect_keys,
        expect_int=_expect_int,
        expect_string=_expect_nonempty_string,
        expect_lease_id=_expect_handoff_id,
    ).validate(value, label)


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
    control: dict[str, Any], *, runtime_root: Path
) -> dict[str, Any]:
    _expect_keys(
        control,
        {
            "candidate_commit",
            "checkpoint",
            "handoff_id",
            "manifest_file",
            "merge_commit",
            "merge_result",
            "payload_files",
            "status",
            "supervision_file_count",
        },
        "control input",
    )
    handoff_id = _expect_handoff_id(control["handoff_id"])
    status = _expect_string(control["status"], "control input.status")
    if status not in CONTROL_STATUSES:
        raise ProtocolError(f"unsupported control status: {status!r}")
    for field in ("candidate_commit", "merge_commit", "merge_result"):
        value = control[field]
        if value is not None:
            _expect_string(value, f"control input.{field}", max_bytes=256)
    checkpoint = _expect_object(control["checkpoint"], "control input.checkpoint")
    payload_values = _expect_list(control["payload_files"], "control input.payload_files")
    if not payload_values:
        raise ProtocolError("control input.payload_files must not be empty")
    manifest_value = _expect_object(
        control["manifest_file"], "control input.manifest_file"
    )
    id_directory = Path(runtime_root) / handoff_id
    manifest_entry = _validate_manifest_entry(
        manifest_value,
        handoff_id=handoff_id,
        id_directory=id_directory,
        label="control input.manifest_file",
    )
    if manifest_entry["kind"] != "manifest":
        raise ProtocolError("control input.manifest_file must have kind='manifest'")
    payload_entries: list[dict[str, Any]] = []
    for index, value in enumerate(payload_values):
        entry = _validate_manifest_entry(
            value,
            handoff_id=handoff_id,
            id_directory=id_directory,
            label=f"control input.payload_files[{index}]",
        )
        if entry["kind"] == "manifest":
            raise ProtocolError("control input.payload_files cannot include a manifest")
        payload_entries.append(entry)
    routes = {(entry["direction"], entry["kind"]) for entry in payload_entries}
    if len(routes) != 1:
        raise ProtocolError("control input.payload_files must form one message route")
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
        "control input.supervision_file_count",
        len(payload_entries) + 1,
        100_000,
    )
    return {
        "candidate_commit": control["candidate_commit"],
        "checkpoint": checkpoint,
        "handoff_id": handoff_id,
        "manifest_file": manifest_entry,
        "merge_commit": control["merge_commit"],
        "merge_result": control["merge_result"],
        "payload_files": payload_entries,
        "status": status,
        "supervision_file_count": count,
    }



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
        "control_version": CONTROL_VERSION,
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
    normalized_source = dict(source)
    normalized_source.pop("in_reply_to")
    normalized_source.pop("summary")
    normalized_source["checkpoint"] = {"说明": checkpoint}
    normalized = _validate_control_common(
        normalized_source,
        runtime_root=runtime_root,
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
        runtime_root=runtime_root,
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
        "control_version": CONTROL_VERSION,
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
    verify_handoff(
        Path(runtime_root) / handoff_id / HANDOFF_FILENAME,
        runtime_root=runtime_root,
    )
    return _create_control_card(source, runtime_root=runtime_root)


def verify_control(
    input_path: Path,
    handoff_path: Path,
    *,
    runtime_root: Path = RUNTIME_ROOT,
) -> dict[str, Any]:
    data = _read_regular_file(
        Path(input_path), max_bytes=MAX_CONTROL_BYTES, label="control message card"
    )
    if not data.startswith("【3实现消息｜".encode("utf-8")):
        raise ProtocolError(
            "unsupported_stale_execution_state",
            "only the current canonical Chinese message-card control protocol is supported",
        )
    return _verify_control_card(
        data, Path(handoff_path), runtime_root=runtime_root
    )



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


def _runtime_document_report(path: Path, document: dict[str, Any], data: bytes) -> dict[str, Any]:
    identity = document.get("proposal_id") or document.get("convergence_id") or document.get("closure_id")
    return {
        **document,
        "file_bytes": len(data),
        "file_sha256": _sha256(data),
        "id": identity,
        "path": str(path),
    }


def _publish_runtime_document(root: Path, name: str, document: dict[str, Any]) -> tuple[Path, bytes]:
    data = _canonical_json_bytes(document)
    if len(data) > MAX_INPUT_BYTES:
        raise ProtocolError("immutable runtime document exceeds the maximum size")
    root_fd = _ensure_runtime_root(root)
    try:
        path = root / name
        if path.exists():
            existing = _read_regular_file(path, max_bytes=MAX_INPUT_BYTES, label="immutable runtime document")
            if existing != data:
                raise ProtocolError("receipt_cas_mismatch", "immutable runtime document identity already exists with different bytes")
            return path, existing
        _publish_group(root_fd, [(name, data)])
        return path, data
    finally:
        os.close(root_fd)


def create_implementation_outcome_proposal(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="implementation outcome proposal input"), "implementation outcome proposal input"),
        "implementation outcome proposal input",
    )
    _expect_keys(source, {"after_utf8_b64", "change_kind", "implementation_id", "repository", "source_checkpoint", "target_path"}, "implementation outcome proposal input")
    if source["change_kind"] != "implementation-outcome":
        raise ProtocolError("document_proposal_authority_invalid", "new requirements or source changes are not implementation outcome proposals")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    implementation_id = _expect_nonempty_string(source["implementation_id"], "implementation_id", max_bytes=256)
    checkpoint = _validate_implementation_source_checkpoint(source["source_checkpoint"], "source_checkpoint")
    protected = _active_source_for_candidate(repository, implementation_id, checkpoint)
    target_path = _normalize_protected_document_path(source["target_path"], "target_path")
    artifacts = [item for item in protected["artifacts"] if item["path"] == target_path]
    if len(artifacts) != 1:
        raise ProtocolError("document_proposal_authority_invalid", "proposal target is not one frozen source artifact")
    encoded = _expect_nonempty_string(source["after_utf8_b64"], "after_utf8_b64", max_bytes=MAX_INPUT_BYTES * 2)
    try:
        after = base64.b64decode(encoded.encode("ascii"), validate=True)
        after.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, ValueError) as error:
        raise ProtocolError("document_proposal_authority_invalid", "proposal bytes must be canonical base64 UTF-8") from error
    if base64.b64encode(after).decode("ascii") != encoded or len(after) > MAX_INPUT_BYTES:
        raise ProtocolError("document_proposal_authority_invalid", "proposal bytes are non-canonical or too large")
    after_sha = _sha256(after)
    proposal_id = hashlib.sha256(f"{implementation_id}\0{target_path}\0{after_sha}".encode("utf-8")).hexdigest()[:32]
    document = {
        "after": {"bytes": len(after), "sha256": after_sha, "utf8_b64": encoded},
        "base": {"blob_id": artifacts[0]["blob_id"], "sha256": artifacts[0]["sha256"]},
        "change_kind": "implementation-outcome",
        "implementation_id": implementation_id,
        "proposal_id": proposal_id,
        "repository": str(repository),
        "schema": 1,
        "source_checkpoint": checkpoint,
        "target_path": target_path,
        "version": 1,
    }
    path, data = _publish_runtime_document(IMPLEMENTATION_OUTCOME_ROOT, proposal_id + ".json", document)
    return {**_runtime_document_report(path, document, data), "created": True}


def _load_outcome_proposal(path: Path) -> tuple[dict[str, Any], bytes]:
    path = _expect_absolute_path(path, "proposal path")
    if path.parent != IMPLEMENTATION_OUTCOME_ROOT or path.suffix != ".json":
        raise ProtocolError("document_proposal_authority_invalid", "proposal is outside the immutable outcome root")
    data = _read_regular_file(path, max_bytes=MAX_INPUT_BYTES, label="implementation outcome proposal")
    document = _expect_object(_load_json_bytes(data, "implementation outcome proposal", require_canonical=True), "implementation outcome proposal")
    _expect_keys(document, {"after", "base", "change_kind", "implementation_id", "proposal_id", "repository", "schema", "source_checkpoint", "target_path", "version"}, "implementation outcome proposal")
    if document["schema"] != 1 or document["version"] != 1 or document["change_kind"] != "implementation-outcome":
        raise ProtocolError("document_proposal_authority_invalid", "outcome proposal version or kind is invalid")
    after = _expect_object(document["after"], "proposal.after")
    _expect_keys(after, {"bytes", "sha256", "utf8_b64"}, "proposal.after")
    base = _expect_object(document["base"], "proposal.base")
    _expect_keys(base, {"blob_id", "sha256"}, "proposal.base")
    decoded = base64.b64decode(_expect_nonempty_string(after["utf8_b64"], "proposal.after.utf8_b64", max_bytes=MAX_INPUT_BYTES * 2).encode("ascii"), validate=True)
    if len(decoded) != _expect_int(after["bytes"], "proposal.after.bytes", 0, MAX_INPUT_BYTES) or _sha256(decoded) != _expect_sha256(after["sha256"], "proposal.after.sha256"):
        raise ProtocolError("document_proposal_authority_invalid", "outcome proposal bytes do not match their immutable identity")
    normalized = {
        **document,
        "after": {"bytes": len(decoded), "sha256": after["sha256"], "utf8_b64": after["utf8_b64"]},
        "base": {"blob_id": _expect_git_oid(base["blob_id"], "proposal.base.blob_id"), "sha256": _expect_sha256(base["sha256"], "proposal.base.sha256")},
        "implementation_id": _expect_nonempty_string(document["implementation_id"], "proposal.implementation_id", max_bytes=256),
        "proposal_id": _expect_handoff_id(document["proposal_id"], "proposal.proposal_id"),
        "repository": str(_expect_absolute_path(document["repository"], "proposal.repository")),
        "source_checkpoint": _validate_implementation_source_checkpoint(document["source_checkpoint"], "proposal.source_checkpoint"),
        "target_path": _normalize_protected_document_path(document["target_path"], "proposal.target_path"),
    }
    if path.name != normalized["proposal_id"] + ".json":
        raise ProtocolError("document_proposal_authority_invalid", "outcome proposal path does not match its identity")
    return normalized, data


def _replace_repository_file(path: Path, data: bytes) -> None:
    status = os.lstat(path)
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise ProtocolError("outcome_unknown", f"document target identity is unsafe: {path}")
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    temp_name = f".codex-convergence-{secrets.token_hex(16)}.tmp"
    try:
        descriptor = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), _mode_bits(status), dir_fd=directory_fd)
        try:
            _write_all(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temp_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def converge_implementation_outcomes(input_path: Path) -> dict[str, Any]:
    source = _expect_object(
        _load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="implementation outcome convergence input"), "implementation outcome convergence input"),
        "implementation outcome convergence input",
    )
    _expect_keys(source, {"document_lease", "paths", "repository"}, "implementation outcome convergence input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    paths = _normalize_document_paths(source["paths"], "paths")
    lease = _expect_object(source["document_lease"], "document_lease")
    _expect_keys(lease, {"lease_id", "path", "version"}, "document_lease")
    verified_lease = verify_document_lease(
        _expect_absolute_path(lease["path"], "document_lease.path"),
        expected_id=lease["lease_id"],
        expected_version=lease["version"],
    )
    holder = verified_lease.get("holder")
    if (
        verified_lease["repository"] != str(repository)
        or not isinstance(holder, dict)
        or holder.get("stage") != "change-closure"
        or holder.get("purpose") != "document-write"
        or holder.get("paths") != paths
        or holder.get("implementation_id") is None
    ):
        raise ProtocolError("document_lease_invalid", "convergence requires the final implementation's exact document lease")
    proposals: list[tuple[dict[str, Any], bytes, Path]] = []
    if IMPLEMENTATION_OUTCOME_ROOT.exists():
        for proposal_path in sorted(IMPLEMENTATION_OUTCOME_ROOT.glob("*.json")):
            proposal, proposal_data = _load_outcome_proposal(proposal_path)
            if proposal["repository"] == str(repository) and proposal["target_path"] in paths:
                proposals.append((proposal, proposal_data, proposal_path))
    if not proposals or set(paths) != {item[0]["target_path"] for item in proposals}:
        raise ProtocolError("document_proposal_authority_invalid", "every convergence path must have retained immutable outcome evidence")
    blockers = _document_source_protection_blockers(repository, paths)
    if any(identities != [holder["implementation_id"]] for identities in blockers.values()):
        raise ProtocolError("implementation_source_protected", "another dependent implementation still protects a convergence target", context={"current": blockers, "repository": str(repository)})
    staged: dict[str, bytes] = {}
    outcomes: list[dict[str, Any]] = []
    for path_text in paths:
        target = repository / path_text
        current = _read_regular_file(target, max_bytes=MAX_INPUT_BYTES, label="convergence target")
        value = current
        for proposal, _, proposal_path in sorted((item for item in proposals if item[0]["target_path"] == path_text), key=lambda item: (item[0]["implementation_id"], item[0]["proposal_id"])):
            after = base64.b64decode(proposal["after"]["utf8_b64"].encode("ascii"), validate=True)
            current_sha = _sha256(value)
            if current_sha == proposal["after"]["sha256"]:
                outcome = "no-op"
            elif current_sha == proposal["base"]["sha256"]:
                value = after
                outcome = "applied"
            else:
                raise ProtocolError("document_proposal_conflict", "retained outcome proposal conflicts with the exact current document", context={"current": {"current_sha256": current_sha, "path": path_text, "proposal_id": proposal["proposal_id"]}, "repository": str(repository)})
            outcomes.append({"implementation_id": proposal["implementation_id"], "outcome": outcome, "path": path_text, "proposal_id": proposal["proposal_id"], "proposal_sha256": _sha256(proposal_path.read_bytes())})
        staged[path_text] = value
    for path_text, value in staged.items():
        target = repository / path_text
        if _sha256(_read_regular_file(target, max_bytes=MAX_INPUT_BYTES, label="convergence target")) != _sha256(value):
            _replace_repository_file(target, value)
    final_documents = [{"path": path, "sha256": _sha256(value)} for path, value in sorted(staged.items())]
    convergence_id = hashlib.sha256(_canonical_json_bytes({"documents": final_documents, "outcomes": outcomes}, trailing_newline=False)).hexdigest()[:32]
    document = {
        "convergence_id": convergence_id,
        "document_lease": {"lease_id": holder["lease_id"], "path": verified_lease["path"], "version": verified_lease["version"]},
        "documents": final_documents,
        "documentation_commit": None,
        "expected_head": _expect_git_oid(_git_text(repository, ["rev-parse", "HEAD"]).strip(), "convergence HEAD"),
        "implementation_id": holder["implementation_id"],
        "outcomes": outcomes,
        "repository": str(repository),
        "schema": 1,
        "state": "converged",
        "version": 1,
    }
    path, data = _publish_runtime_document(DOCUMENT_CONVERGENCE_ROOT, convergence_id + ".json", document)
    return {**_runtime_document_report(path, document, data), "converged": True}


def _load_document_convergence_receipt(value: Any, repository: Path) -> tuple[Path, dict[str, Any], bytes]:
    receipt = _expect_object(value, "convergence")
    _expect_keys(receipt, {"file_bytes", "file_sha256", "id", "path", "version"}, "convergence")
    path = _expect_absolute_path(receipt["path"], "convergence.path")
    if path.parent != DOCUMENT_CONVERGENCE_ROOT:
        raise ProtocolError("document_proposal_authority_invalid", "convergence receipt is outside its immutable root")
    data = _read_regular_file(path, max_bytes=MAX_INPUT_BYTES, label="document convergence")
    document = _expect_object(_load_json_bytes(data, "document convergence", require_canonical=True), "document convergence")
    if (
        document.get("convergence_id") != receipt["id"]
        or document.get("repository") != str(repository)
        or document.get("version") != receipt["version"]
        or len(data) != receipt["file_bytes"]
        or _sha256(data) != receipt["file_sha256"]
    ):
        raise ProtocolError("receipt_cas_mismatch", "document convergence CAS changed")
    return path, document, data


def commit_converged_documents(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="documentation commit input"), "documentation commit input"), "documentation commit input")
    _expect_keys(source, {"convergence", "expected_target_head", "repository", "target_branch"}, "documentation commit input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    expected = _expect_git_oid(source["expected_target_head"], "expected_target_head")
    target_branch = _expect_nonempty_string(source["target_branch"], "target_branch", max_bytes=1024)
    path, document, data = _load_document_convergence_receipt(source["convergence"], repository)
    if document.get("state") != "converged" or document.get("expected_head") != expected:
        raise ProtocolError("publication_head_mismatch", "document convergence was not produced from the expected target HEAD")
    verified_lease = verify_document_lease(Path(document["document_lease"]["path"]), expected_id=document["document_lease"]["lease_id"], expected_version=document["document_lease"]["version"])
    paths = [item["path"] for item in document["documents"]]
    _, directory_fd, git_fd = _open_target_publication_directory(repository)
    guard_fd = _open_target_publication_guard(git_fd)
    try:
        snapshot = _publication_checkout_snapshot(repository, target_branch)
        if (
            not snapshot["target_matches"]
            or snapshot["head"] != expected
            or _git_branch_oid(repository, target_branch) != expected
            or snapshot["markers"]
        ):
            raise ProtocolError(
                "publication_checkout_invalid",
                "documentation publication requires the exact target branch, HEAD and no Git operation",
                context={"current": snapshot, "repository": str(repository)},
            )
        for item in document["documents"]:
            if _sha256(_read_regular_file(repository / item["path"], max_bytes=MAX_INPUT_BYTES, label="converged document")) != item["sha256"]:
                raise ProtocolError("document_proposal_conflict", "converged document bytes changed before commit")
        changed = set(_git_text(repository, ["diff", "--name-only"]).splitlines())
        untracked = set(_git_text(repository, ["ls-files", "--others", "--exclude-standard"]).splitlines())
        if not (changed | untracked).issubset(set(paths)):
            raise ProtocolError("publication_checkout_invalid", "checkout contains changes outside converged document paths")
        if not changed and not untracked:
            commit = expected
            outcome = "no-op"
        else:
            subprocess.run(["git", "-C", str(repository), "add", "--", *paths], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            completed = subprocess.run(["git", "-C", str(repository), "-c", "user.name=Workflow Pipeline", "-c", "user.email=workflow-pipeline@example.invalid", "commit", "-m", "docs: archive implementation outcome", "--", *paths], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if completed.returncode != 0:
                subprocess.run(["git", "-C", str(repository), "reset", "--", *paths], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                raise ProtocolError("outcome_unknown", "documentation commit failed; document changes remain unstaged for exact retry", retryable=True, cause=completed.stderr[:1024])
            commit = _expect_git_oid(_git_text(repository, ["rev-parse", "HEAD"]).strip(), "documentation commit")
            parents = _git_text(repository, ["rev-list", "--parents", "-n", "1", commit]).strip().split()
            if parents != [commit, expected]:
                raise ProtocolError("publication_outcome_ambiguous", "documentation commit parent does not equal expected target HEAD")
            outcome = "committed"
        updated = {**document, "documentation_commit": commit, "state": "committed", "version": document["version"] + 1}
        updated_data = _canonical_json_bytes(updated)
        root_fd = _open_directory(DOCUMENT_CONVERGENCE_ROOT)
        try:
            current = _read_regular_file(path, max_bytes=MAX_INPUT_BYTES, label="document convergence")
            if current != data:
                raise ProtocolError("receipt_cas_mismatch", "document convergence changed before commit recording")
            _replace_private_file(root_fd, name=path.name, data=updated_data, max_bytes=MAX_INPUT_BYTES, label="document convergence")
        finally:
            os.close(root_fd)
        return {**_runtime_document_report(path, updated, updated_data), "outcome": outcome, "verified_lease_version": verified_lease["version"]}
    finally:
        os.close(guard_fd)
        os.close(directory_fd)
        os.close(git_fd)


def _worktree_closure_path(closure_id: str) -> Path:
    return WORKTREE_CLOSURE_ROOT / (_expect_handoff_id(closure_id, "closure_id") + ".json")


def _load_worktree_closure(path: Path) -> tuple[dict[str, Any], bytes]:
    path = _expect_absolute_path(path, "worktree closure path")
    if path.parent != WORKTREE_CLOSURE_ROOT:
        raise ProtocolError("worktree closure is outside its fixed root")
    data = _read_regular_file(path, max_bytes=MAX_INPUT_BYTES, label="worktree closure")
    document = _expect_object(_load_json_bytes(data, "worktree closure", require_canonical=True), "worktree closure")
    if "closure_version" in document:
        raise ProtocolError(
            "unsupported_stale_execution_state",
            "pre-cutover closure checkpoints are unsupported and remain unchanged",
        )
    _expect_keys(document, {"closure_id", "facts", "pending_phase", "phase", "receipts", "schema", "version"}, "worktree closure")
    if document["schema"] != 1 or document["phase"] not in WORKTREE_CLOSURE_PHASES or path != _worktree_closure_path(document["closure_id"]):
        raise ProtocolError("worktree closure identity or phase is invalid")
    if document["pending_phase"] is not None and document["pending_phase"] not in WORKTREE_CLOSURE_PHASES:
        raise ProtocolError("worktree closure pending phase is invalid")
    return document, data


def create_worktree_closure_checkpoint(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="worktree closure input"), "worktree closure input"), "worktree closure input")
    _expect_keys(source, {"convergence", "execution_claim", "publication", "repository"}, "worktree closure input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    _, publication, _ = _target_publication_receipt(source["publication"], repository)
    if publication["state"] != "integrated":
        raise ProtocolError("archive_authority_invalid", "Stage 4 requires an exact INTEGRATED publication")
    _, convergence, _ = _load_document_convergence_receipt(source["convergence"], repository)
    if convergence.get("state") != "committed" or convergence.get("implementation_id") != publication["binding"]["implementation_id"]:
        raise ProtocolError("archive_authority_invalid", "documentation convergence is not committed for the integrated implementation")
    claim = verify_worktree_execution_claim_receipt_from_value(source["execution_claim"], repository, Path(publication["binding"]["worktree_path"]))
    if claim["binding"]["implementation_id"] != publication["binding"]["implementation_id"] or claim["binding"]["source_checkpoint"] != publication["binding"]["source_checkpoint"]:
        raise ProtocolError("archive_authority_invalid", "execution claim does not match integrated publication")
    target_head = _expect_git_oid(_git_text(repository, ["rev-parse", "HEAD"]).strip(), "closure target HEAD")
    if target_head != convergence["documentation_commit"]:
        raise ProtocolError("archive_authority_invalid", "documentation commit is not the current target HEAD")
    source_report = inspect_implementation_sources(repository)
    closure_id = secrets.token_hex(16)
    facts = {
        "candidate_commit": publication["binding"]["candidate_commit"],
        "documentation_commit": convergence["documentation_commit"],
        "execution_claim": {key: claim[key] for key in ("claim_id", "file_bytes", "file_sha256", "path", "version")},
        "implementation_branch": claim["binding"]["implementation_branch"],
        "implementation_id": claim["binding"]["implementation_id"],
        "merge_commit": publication["integration"]["merge_commit"],
        "repository": str(repository),
        "source_protection_version": source_report["version"],
        "target_branch": publication["binding"]["target_branch"],
        "worktree_path": claim["binding"]["worktree_path"],
    }
    document = {"closure_id": closure_id, "facts": facts, "pending_phase": None, "phase": "documents-committed", "receipts": [{"phase": "documents-committed", "result": {"documentation_commit": convergence["documentation_commit"]}}], "schema": 1, "version": 1}
    path, data = _publish_runtime_document(WORKTREE_CLOSURE_ROOT, closure_id + ".json", document)
    return _runtime_document_report(path, document, data)


def _persist_worktree_closure(path: Path, expected_data: bytes, document: dict[str, Any]) -> bytes:
    updated_data = _canonical_json_bytes(document)
    root_fd = _open_directory(WORKTREE_CLOSURE_ROOT)
    try:
        current = _read_regular_file(path, max_bytes=MAX_INPUT_BYTES, label="worktree closure")
        if current != expected_data:
            raise ProtocolError("receipt_cas_mismatch", "worktree closure changed during advancement")
        _replace_private_file(root_fd, name=path.name, data=updated_data, max_bytes=MAX_INPUT_BYTES, label="worktree closure")
    finally:
        os.close(root_fd)
    return updated_data


def _advance_worktree_closure_effect(document: dict[str, Any], phase: str, *, recovering: bool) -> dict[str, Any]:
    facts = document["facts"]
    repository = Path(facts["repository"])
    if phase == "worktree-removed":
        path = Path(facts["worktree_path"]).resolve(strict=False)
        matches = [item for item in _active_git_worktrees(repository) if Path(item["path"]) == path]
        if not matches:
            if recovering and not os.path.lexists(path):
                return {"adopted_absence": True, "path": str(path)}
            raise ProtocolError("closure_worktree_missing", "worktree absence has no persisted cleanup intent")
        observed = matches[0] if len(matches) == 1 else {}
        if observed.get("branch") != facts["implementation_branch"] or observed.get("head") != facts["candidate_commit"] or _git_text(path, ["status", "--porcelain=v1", "--untracked-files=all"]):
            raise ProtocolError("closure_worktree_identity_changed", "worktree is dirty or no longer equals the closure identity")
        completed = subprocess.run(["git", "-C", str(repository), "worktree", "remove", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if completed.returncode != 0:
            raise ProtocolError("closure_worktree_removal_refused", "non-force worktree removal failed", cause=completed.stderr[:1024])
        return {"adopted_absence": False, "path": str(path)}
    if phase == "branch-removed":
        branch = facts["implementation_branch"]
        branch_head = _git_branch_oid(repository, branch)
        if branch_head is None:
            if recovering:
                return {"adopted_absence": True, "name": branch}
            raise ProtocolError("closure_branch_missing", "branch absence has no persisted cleanup intent")
        if branch_head != facts["candidate_commit"]:
            raise ProtocolError("closure_branch_identity_changed", "implementation branch changed before cleanup")
        ancestry = subprocess.run(["git", "-C", str(repository), "merge-base", "--is-ancestor", branch_head, facts["documentation_commit"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if ancestry.returncode != 0:
            raise ProtocolError("closure_branch_deletion_refused", "implementation branch is not proven integrated")
        deleted = subprocess.run(["git", "-C", str(repository), "branch", "-d", branch], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if deleted.returncode != 0:
            raise ProtocolError("closure_branch_deletion_refused", "non-force branch deletion failed", cause=deleted.stderr[:1024])
        return {"adopted_absence": False, "name": branch}
    if phase == "execution-claim-released":
        receipt = facts["execution_claim"]
        claim_path = Path(receipt["path"])
        claim, claim_data = _read_worktree_execution_claim(claim_path)
        if claim["state"] == "released":
            terminal = (claim.get("provisioning") or {}).get("terminal_state")
            if recovering and claim["claim_id"] == receipt["claim_id"] and claim["version"] == receipt["version"] + 1 and terminal == "archived":
                return {"adopted_release": True, "claim_id": claim["claim_id"], "version": claim["version"]}
            raise ProtocolError("worktree_claim_cas_mismatch", "claim was released without the persisted closure intent")
        source_report = inspect_implementation_sources(repository)
        active = [item for item in source_report["implementations"] if item["implementation_id"] == facts["implementation_id"] and item["protection_state"] == "active"]
        if active:
            protected_paths = [item["path"] for item in active[0]["artifacts"]]
            blockers = _document_source_protection_blockers(repository, protected_paths)
            if any(identities != [facts["implementation_id"]] for identities in blockers.values()):
                raise ProtocolError("implementation_source_protected", "another implementation still depends on the source during closure")
            release_input = WORKTREE_CLOSURE_ROOT / f".{document['closure_id']}.source-release.json"
            release_data = _canonical_json_bytes({"expected_version": source_report["version"], "implementation_id": facts["implementation_id"], "repository": str(repository), "terminal_state": "archived"})
            descriptor = os.open(release_input, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                _write_all(descriptor, release_data)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                release_implementation_source(release_input)
            finally:
                release_input.unlink(missing_ok=True)
        released = _release_worktree_execution_claim(claim_path, expected_id=receipt["claim_id"], expected_version=receipt["version"], expected_bytes=receipt["file_bytes"], expected_sha256=receipt["file_sha256"], terminal_state="archived")
        return {"adopted_release": False, "claim_id": released["claim_id"], "version": released["version"]}
    if phase == "archived":
        return {"archived": True}
    raise ProtocolError("closure_side_effect_phase_invalid", f"unsupported worktree closure phase: {phase}")


def advance_worktree_closure_checkpoint(checkpoint_path: Path) -> dict[str, Any]:
    path = _expect_absolute_path(checkpoint_path, "worktree closure checkpoint")
    document, data = _load_worktree_closure(path)
    current_index = WORKTREE_CLOSURE_PHASES.index(document["phase"])
    if current_index + 1 >= len(WORKTREE_CLOSURE_PHASES):
        return {**_runtime_document_report(path, document, data), "idempotent": True}
    next_phase = WORKTREE_CLOSURE_PHASES[current_index + 1]
    recovering = document["pending_phase"] == next_phase
    if document["pending_phase"] not in {None, next_phase}:
        raise ProtocolError("closure_side_effect_phase_invalid", "closure has a different pending side effect")
    if not recovering:
        if next_phase == "worktree-removed":
            worktree_path = Path(document["facts"]["worktree_path"])
            if not any(Path(item["path"]) == worktree_path for item in _active_git_worktrees(Path(document["facts"]["repository"]))):
                raise ProtocolError("closure_worktree_missing", "worktree is absent before cleanup intent persistence")
        if next_phase == "branch-removed" and _git_branch_oid(Path(document["facts"]["repository"]), document["facts"]["implementation_branch"]) is None:
            raise ProtocolError("closure_branch_missing", "branch is absent before cleanup intent persistence")
        pending = {**document, "pending_phase": next_phase, "version": document["version"] + 1}
        data = _persist_worktree_closure(path, data, pending)
        document = pending
        if os.environ.get("CODEX_SUPERVISION_TEST_FAILPOINT") == f"closure-after-{next_phase}-intent":
            raise ProtocolError("outcome_unknown", "closure intent persisted before its side effect", retryable=True)
    result = _advance_worktree_closure_effect(document, next_phase, recovering=recovering)
    if os.environ.get("CODEX_SUPERVISION_TEST_FAILPOINT") == f"closure-after-{next_phase}-effect":
        raise ProtocolError("outcome_unknown", "closure side effect completed before checkpoint advancement", retryable=True)
    updated = {**document, "pending_phase": None, "phase": next_phase, "receipts": document["receipts"] + [{"phase": next_phase, "result": result}], "version": document["version"] + 1}
    updated_data = _persist_worktree_closure(path, data, updated)
    return _runtime_document_report(path, updated, updated_data)


def verify_worktree_closure_receipt(input_path: Path) -> dict[str, Any]:
    source = _expect_object(_load_json_bytes(_read_regular_file(input_path, max_bytes=MAX_INPUT_BYTES, label="worktree closure verification input"), "worktree closure verification input"), "worktree closure verification input")
    _expect_keys(source, {"closure", "implementation_id", "repository"}, "worktree closure verification input")
    repository = _expect_absolute_path(source["repository"], "repository").resolve(strict=True)
    implementation_id = _expect_nonempty_string(source["implementation_id"], "implementation_id", max_bytes=256)
    receipt = _expect_object(source["closure"], "closure")
    _expect_keys(receipt, {"file_bytes", "file_sha256", "id", "path", "version"}, "closure")
    path = _expect_absolute_path(receipt["path"], "closure.path")
    document, data = _load_worktree_closure(path)
    if (
        document["closure_id"] != receipt["id"]
        or document["version"] != receipt["version"]
        or len(data) != receipt["file_bytes"]
        or _sha256(data) != receipt["file_sha256"]
        or document["facts"].get("repository") != str(repository)
        or document["facts"].get("implementation_id") != implementation_id
        or document["phase"] != "archived"
        or document["pending_phase"] is not None
    ):
        raise ProtocolError("archive_checkpoint_invalid", "worktree closure receipt is not exact and archived", context={"repository": str(repository)})
    return {**_runtime_document_report(path, document, data), "verified": True}


def _bundled_protocol_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "execution-protocol.md"


def _emit_json(value: Any) -> None:
    sys.stdout.buffer.write(_canonical_json_bytes(value))


def _argument(*flags: str, **options: Any) -> ArgumentSpec:
    return ArgumentSpec(flags=flags, options=options)


def _build_command_registry() -> CommandRegistry:
    path_input = (_argument("--input", required=True, type=Path),)
    repository = (_argument("--repository", required=True, type=Path),)
    file_cas = (
        _argument("--file", required=True, type=Path),
        _argument("--id", required=True),
        _argument("--version", required=True, type=int),
    )
    immutable_file = (
        _argument("--file", required=True, type=Path),
        _argument("--id", required=True),
        _argument("--bytes", required=True, type=int),
        _argument("--sha256", required=True),
    )
    claim_cas = immutable_file + (
        _argument("--version", required=True, type=int),
    )
    return CommandRegistry(
        [
            CommandSpec("cutover-preflight", repository, lambda a: cutover_preflight(a.repository)),
            CommandSpec("inspect-implementation-sources", repository, lambda a: inspect_implementation_sources(a.repository)),
            CommandSpec("protect-implementation-source", path_input, lambda a: protect_implementation_source(a.input)),
            CommandSpec("verify-implementation-source", path_input, lambda a: verify_implementation_source(a.input)),
            CommandSpec("release-implementation-source", path_input, lambda a: release_implementation_source(a.input)),
            CommandSpec("inspect-document-lease", repository, lambda a: inspect_document_lease(a.repository)),
            CommandSpec("acquire-document-lease", path_input + (_argument("--wait-seconds", type=int, default=DOCUMENT_LEASE_WAIT_SECONDS), _argument("--max-retries", type=int, default=DOCUMENT_LEASE_MAX_RETRIES)), lambda a: acquire_document_lease(a.input, wait_seconds=a.wait_seconds, max_retries=a.max_retries)),
            CommandSpec("verify-document-lease", file_cas, lambda a: verify_document_lease(a.file, expected_id=a.id, expected_version=a.version)),
            CommandSpec("renew-document-lease", file_cas + (_argument("--ttl-seconds", required=True, type=int),), lambda a: renew_document_lease(a.file, expected_id=a.id, expected_version=a.version, ttl_seconds=a.ttl_seconds)),
            CommandSpec("release-document-lease", file_cas, lambda a: release_document_lease(a.file, expected_id=a.id, expected_version=a.version)),
            CommandSpec("inspect-repository-coordination-lease", repository, lambda a: inspect_repository_coordination_lease(a.repository)),
            CommandSpec("acquire-repository-coordination-lease", path_input, lambda a: acquire_repository_coordination_lease(a.input)),
            CommandSpec("verify-repository-coordination-lease", file_cas, lambda a: verify_repository_coordination_lease(a.file, expected_id=a.id, expected_version=a.version)),
            CommandSpec("release-repository-coordination-lease", file_cas, lambda a: release_repository_coordination_lease(a.file, expected_id=a.id, expected_version=a.version)),
            CommandSpec("create-worktree-state-receipt", path_input, lambda a: create_worktree_state_receipt(a.input)),
            CommandSpec("verify-worktree-state-receipt", path_input, lambda a: verify_worktree_state_receipt(a.input)),
            CommandSpec("inspect-worktree-execution-claims", repository, lambda a: inspect_worktree_execution_claims(a.repository)),
            CommandSpec("reserve-worktree-execution-claim", path_input, lambda a: reserve_worktree_execution_claim(a.input)),
            CommandSpec("provision-claimed-worktree", path_input, lambda a: provision_claimed_worktree(a.input)),
            CommandSpec("reconcile-worktree-provisioning", path_input, lambda a: reconcile_worktree_provisioning(a.input)),
            CommandSpec("verify-worktree-execution-claim", claim_cas + (_argument("--platform-cwd", required=True, type=Path),), lambda a: verify_worktree_execution_claim(a.file, expected_id=a.id, expected_version=a.version, expected_bytes=a.bytes, expected_sha256=a.sha256, platform_cwd=a.platform_cwd)),
            CommandSpec("verify-worktree-execution-claim-receipt", path_input, lambda a: verify_worktree_execution_claim_receipt(a.input)),
            CommandSpec("administratively-release-worktree-execution-claim", path_input, lambda a: administratively_release_worktree_execution_claim(a.input)),
            CommandSpec("record-candidate-review", path_input, lambda a: record_candidate_review(a.input)),
            CommandSpec("accept-implementation-candidate", path_input, lambda a: accept_implementation_candidate(a.input)),
            CommandSpec("publish-accepted-candidate", path_input, lambda a: publish_accepted_candidate(a.input)),
            CommandSpec("classify-publication-conflict", path_input, lambda a: classify_publication_conflict(a.input)),
            CommandSpec("reconcile-candidate-publication", path_input, lambda a: reconcile_candidate_publication(a.input)),
            CommandSpec("create-handoff", path_input, lambda a: create_handoff(a.input, _bundled_protocol_path())),
            CommandSpec("verify-handoff", immutable_file, lambda a: verify_handoff(a.file, expected_id=a.id, expected_bytes=a.bytes, expected_sha256=a.sha256)),
            CommandSpec("publish-supervision", (_argument("--handoff-file", required=True, type=Path), _argument("--direction", required=True, choices=sorted(DIRECTIONS)), _argument("--kind", required=True), _argument("--payload-file", required=True, type=Path), _argument("--previous-manifest", type=Path)), lambda a: publish_supervision(a.handoff_file, direction=a.direction, kind=a.kind, payload_path=a.payload_file, previous_manifest_path=a.previous_manifest)),
            CommandSpec("verify-supervision", (_argument("--handoff-file", required=True, type=Path), _argument("--manifest-file", required=True, type=Path), _argument("--expected-count", required=True, type=int)), lambda a: verify_supervision(a.handoff_file, a.manifest_file, expected_count=a.expected_count)),
            CommandSpec("create-control", path_input, lambda a: create_control(a.input), raw_output=True),
            CommandSpec("verify-control", path_input + (_argument("--handoff-file", required=True, type=Path),), lambda a: verify_control(a.input, a.handoff_file)),
            CommandSpec("inspect-cleanup", (_argument("--checkpoint", required=True, type=Path),), lambda a: inspect_cleanup(a.checkpoint)),
            CommandSpec("advance-cleanup", (_argument("--checkpoint", required=True, type=Path),), lambda a: advance_cleanup(a.checkpoint)),
            CommandSpec("create-implementation-outcome-proposal", path_input, lambda a: create_implementation_outcome_proposal(a.input)),
            CommandSpec("converge-implementation-outcomes", path_input, lambda a: converge_implementation_outcomes(a.input)),
            CommandSpec("commit-converged-documents", path_input, lambda a: commit_converged_documents(a.input)),
            CommandSpec("create-worktree-closure-checkpoint", path_input, lambda a: create_worktree_closure_checkpoint(a.input)),
            CommandSpec("advance-worktree-closure-checkpoint", (_argument("--checkpoint", required=True, type=Path),), lambda a: advance_worktree_closure_checkpoint(a.checkpoint)),
            CommandSpec("verify-worktree-closure-receipt", path_input, lambda a: verify_worktree_closure_receipt(a.input)),
        ]
    )


COMMAND_REGISTRY = _build_command_registry()


def _build_parser() -> argparse.ArgumentParser:
    return COMMAND_REGISTRY.build_parser(
        description="Create and verify deterministic implementation and closure protocol data."
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        raw_output, result = COMMAND_REGISTRY.dispatch(arguments)
        if raw_output:
            sys.stdout.buffer.write(result)
        else:
            _emit_json(result)
    except ProtocolError as error:
        _emit_json(_error_response(error, arguments.command))
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
